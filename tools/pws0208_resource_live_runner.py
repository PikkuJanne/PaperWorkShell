"""Run and publish the fixed PWS-0208 constrained Windows evidence workload.

The child pytest process owns all document processing.  This parent samples the
complete child process tree, replaces only the two outer measurement fields in
the child's canonical report, validates the result, and publishes fixed local
artifacts without overwrite.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, NoReturn
from uuid import uuid4

from paperworkshell.domain.resource_policy import (
    CONSTRAINED_RESOURCE_POLICY_V1,
    RESOURCE_PROFILE_ENVIRONMENT_VARIABLE,
    ResourcePolicy,
)
from paperworkshell.storage._windows import (
    FileIdentity,
    WindowsHandle,
    open_handle,
    rename_open_file_no_replace_at,
    require_expected_handle,
    write_new_file,
)
from paperworkshell.storage.errors import WorkspaceError
from tools.m1_vertical_slice_benchmark import M1BenchmarkError
from tools.m1_vertical_slice_live_runner import _windows_process_tree_rss_sample
from tools.pws0208_resource_evidence import (
    decode_pws0208_resource_evidence,
    encode_pws0208_resource_evidence,
    validate_workload,
)

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
WORKLOAD_PATH: Final = PROJECT_ROOT / "benchmarks" / "pws0208-searchable-mixed-workload-v1.json"
LIVE_TEST_NODES: Final = (
    "tests/integration/test_pws0208_resource_workload_windows.py::"
    "test_production_adapter_reports_incremental_scheduler_peaks",
    "tests/integration/test_pws0208_resource_workload_windows.py::"
    "test_live_constrained_signed_source_resource_workflow",
)
LIVE_REPORT_FILENAME: Final = "PWS-0208-resource-evidence.json"
LIVE_DERIVATIVE_FILENAME: Final = "PWS-0208-searchable-unsigned-derivative.pdf"
CHILD_REPORT_FILENAME: Final = "child-resource-evidence.json"
CHILD_DERIVATIVE_FILENAME: Final = "child-searchable-derivative.pdf"
CHILD_ADAPTER_PROBE_FILENAME: Final = "child-adapter-probe.json"
CHILD_ARTIFACT_DIRECTORY_ENVIRONMENT: Final = "PWS0208_LIVE_ARTIFACT_DIRECTORY"
CHILD_REVISION_ENVIRONMENT: Final = "PWS0208_LIVE_REVISION"
LIVE_TIMEOUT_SECONDS: Final = 900.0
MAXIMUM_CHILD_REPORT_BYTES: Final = 16 * 1024
MAXIMUM_CHILD_DERIVATIVE_BYTES: Final = 128 * 1024 * 1024
_RUN_PREFIX: Final = "pws0208-live-"
_OUTPUT_STAGE_PREFIX: Final = ".pws0208-publish-"
_OUTPUT_STAGE_SUFFIX: Final = ".tmp"
_GIT_REVISION_LENGTH: Final = 40
_WINDOWS_ALREADY_EXISTS_ERRORS: Final = frozenset({80, 183})


@dataclass(slots=True)
class Pws0208LiveRunnerError(RuntimeError):
    """Stable tooling failure without document content or local path values."""

    code: str
    safe_message: str

    def __post_init__(self) -> None:
        self.args = (self.code, self.safe_message)

    def __str__(self) -> str:
        return f"{self.code}: {self.safe_message}"


@dataclass(frozen=True, slots=True)
class PublishedPws0208Artifacts:
    """The two fixed local outputs published by a successful run."""

    report: Path
    derivative: Path


def _fail(code: str, safe_message: str) -> NoReturn:
    # ``raise ... from None`` suppresses display of an active exception but
    # normally keeps that exception reachable through ``__context__``.  Live
    # runner failures cross a diagnostics boundary, so clear both links before
    # the stable failure leaves this helper.
    try:
        raise Pws0208LiveRunnerError(code, safe_message) from None
    except Pws0208LiveRunnerError as error:
        error.__cause__ = None
        error.__context__ = None
        error.__suppress_context__ = True
        raise


@dataclass(slots=True)
class _RetainedOutputDirectory:
    """One authenticated output namespace retained through publication."""

    path: Path
    handle: WindowsHandle
    identity: FileIdentity

    def require_current(self) -> None:
        """Prove that the retained handle still names the selected directory."""

        require_expected_handle(
            self.handle,
            self.path,
            directory=True,
            expected_identity=self.identity,
        )
        current = self.handle.refresh_info()
        if (
            current.identity != self.identity
            or not current.is_directory
            or current.is_reparse_point
        ):
            _fail("PWS0208_LIVE_OUTPUT_INVALID", "The output directory changed unexpectedly.")


@dataclass(frozen=True, slots=True)
class _PublishedOutput:
    """One exact published file retained until the bundle commits."""

    path: Path
    handle: WindowsHandle


def _child_command(basetemp: Path) -> tuple[str, ...]:
    """Return the fixed argument vector; source or destination inputs are impossible."""

    return (
        sys.executable,
        "-I",
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "--basetemp",
        str(basetemp),
        *LIVE_TEST_NODES,
    )


def _child_environment(artifact_directory: Path, revision: str) -> dict[str, str]:
    """Build a narrow environment while retaining admitted local engine locations."""

    retained = {
        "APPDATA",
        "COMSPEC",
        "LOCALAPPDATA",
        "NUMBER_OF_PROCESSORS",
        "PATH",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PWS_QPDF_EXECUTABLE",
        "PWS_TESSDATA_ROOT",
        "PWS_TESSERACT_EXECUTABLE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "WINDIR",
    }
    environment = {name: value for name, value in os.environ.items() if name in retained}
    environment.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            RESOURCE_PROFILE_ENVIRONMENT_VARIABLE: "constrained",
            CHILD_ARTIFACT_DIRECTORY_ENVIRONMENT: str(artifact_directory),
            CHILD_REVISION_ENVIRONMENT: revision,
        }
    )
    return environment


def _git_revision() -> str:
    """Return the exact clean SHA-1 revision without exposing status paths."""

    git = shutil.which("git")
    if git is None:
        _fail("PWS0208_LIVE_GIT_UNAVAILABLE", "Git is unavailable for evidence binding.")
    try:
        revision = (
            subprocess.run(
                (git, "rev-parse", "HEAD"),
                cwd=PROJECT_ROOT,
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
                timeout=30,
            )
            .stdout.decode("ascii")
            .strip()
        )
        status = subprocess.run(
            (git, "status", "--porcelain=v1", "--untracked-files=all"),
            cwd=PROJECT_ROOT,
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            timeout=30,
        ).stdout
    except OSError, UnicodeError, subprocess.SubprocessError:
        _fail("PWS0208_LIVE_GIT_FAILED", "Repository identity could not be verified.")
    if (
        len(revision) != _GIT_REVISION_LENGTH
        or any(character not in "0123456789abcdef" for character in revision)
        or status
    ):
        _fail(
            "PWS0208_LIVE_GIT_DIRTY",
            "The evidence workload requires one clean SHA-1 repository revision.",
        )
    return revision


def _is_reparse(path: Path) -> bool:
    try:
        return bool(int(getattr(path.lstat(), "st_file_attributes", 0)) & 0x0000_0400)
    except OSError:
        return True


def _read_bounded_regular_file(path: Path, *, maximum_bytes: int) -> bytes:
    """Read one non-link regular artifact with an exact pre/post size check."""

    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or _is_reparse(path)
            or not 1 <= before.st_size <= maximum_bytes
        ):
            raise OSError
        with path.open("rb") as stream:
            payload = stream.read(maximum_bytes + 1)
        after = path.lstat()
    except OSError:
        _fail("PWS0208_LIVE_CHILD_ARTIFACT_INVALID", "A child artifact is unavailable.")
    if (
        len(payload) != before.st_size
        or len(payload) > maximum_bytes
        or (before.st_dev, before.st_ino, before.st_size)
        != (after.st_dev, after.st_ino, after.st_size)
    ):
        _fail("PWS0208_LIVE_CHILD_ARTIFACT_INVALID", "A child artifact changed while read.")
    return payload


def finalize_child_report(
    child_payload: bytes,
    *,
    revision: str,
    peak_complete_process_tree_rss_bytes: int,
    complete_process_tree_rss_sample_count: int,
) -> bytes:
    """Replace only the placeholder outer-tree measurement and revalidate."""

    try:
        child = decode_pws0208_resource_evidence(child_payload)
    except ValueError:
        _fail("PWS0208_LIVE_CHILD_REPORT_INVALID", "The child evidence report is invalid.")
    if (
        child.revision != revision
        or child.peak_complete_process_tree_rss_bytes != 1
        or child.complete_process_tree_rss_sample_count != 1
        or child.overall_pass is not True
        or type(peak_complete_process_tree_rss_bytes) is not int
        or peak_complete_process_tree_rss_bytes < 1
        or type(complete_process_tree_rss_sample_count) is not int
        or complete_process_tree_rss_sample_count < 1
    ):
        _fail("PWS0208_LIVE_CHILD_REPORT_INVALID", "The child evidence binding is invalid.")
    if peak_complete_process_tree_rss_bytes > child.complete_process_tree_rss_limit_bytes:
        _fail(
            "PWS0208_LIVE_RESOURCE_BUDGET_EXCEEDED",
            "The complete process-tree RSS budget was exceeded.",
        )
    try:
        finalized = replace(
            child,
            peak_complete_process_tree_rss_bytes=peak_complete_process_tree_rss_bytes,
            complete_process_tree_rss_sample_count=(complete_process_tree_rss_sample_count),
        )
        payload = encode_pws0208_resource_evidence(finalized)
        if decode_pws0208_resource_evidence(payload) != finalized:
            raise ValueError
    except ValueError:
        _fail("PWS0208_LIVE_REPORT_INVALID", "The finalized evidence report is invalid.")
    return payload


def _execute_child(stage: Path, basetemp: Path, revision: str) -> tuple[bytes, bytes, int, int]:
    peak_rss = 0
    sample_count = 0
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            _child_command(basetemp),
            cwd=PROJECT_ROOT,
            env=_child_environment(stage, revision),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
        deadline = time.monotonic() + LIVE_TIMEOUT_SECONDS
        while process.poll() is None:
            sample = _windows_process_tree_rss_sample(process)
            if sample is not None:
                peak_rss = max(peak_rss, sample)
                sample_count += 1
            if time.monotonic() >= deadline:
                process.kill()
                process.wait(timeout=30)
                _fail("PWS0208_LIVE_CHILD_TIMEOUT", "The fixed evidence workload timed out.")
            time.sleep(CONSTRAINED_RESOURCE_POLICY_V1.resource_sampling_interval_ms / 1_000)
    except Pws0208LiveRunnerError:
        raise
    except M1BenchmarkError, OSError, subprocess.SubprocessError, RuntimeError:
        if process is not None and process.poll() is None:
            try:
                process.kill()
                process.wait(timeout=30)
            except OSError, subprocess.SubprocessError:
                pass
        _fail("PWS0208_LIVE_CHILD_FAILED", "The fixed evidence workload could not run safely.")
    if process.returncode != 0:
        _fail("PWS0208_LIVE_CHILD_FAILED", "The fixed evidence workload did not pass.")
    if peak_rss < 1 or sample_count < 1:
        _fail(
            "PWS0208_LIVE_RESOURCE_MEASUREMENT_FAILED",
            "Complete process-tree RSS could not be measured.",
        )
    report = _read_bounded_regular_file(
        stage / CHILD_REPORT_FILENAME,
        maximum_bytes=MAXIMUM_CHILD_REPORT_BYTES,
    )
    derivative = _read_bounded_regular_file(
        stage / CHILD_DERIVATIVE_FILENAME,
        maximum_bytes=MAXIMUM_CHILD_DERIVATIVE_BYTES,
    )
    return report, derivative, peak_rss, sample_count


def _close_output_handle_or_fail(handle: WindowsHandle | None) -> None:
    """Release one retained handle and make uncertain release authoritative."""

    if handle is None:
        return
    try:
        handle.close()
    except BaseException:
        _fail("PWS0208_LIVE_CLEANUP_FAILED", "An evidence authority could not be released.")


@contextmanager
def _retain_output_directory(path: Path) -> Iterator[_RetainedOutputDirectory]:
    """Retain one no-follow output-directory authority until publication settles."""

    retained: WindowsHandle | None = None
    try:
        if not isinstance(path, Path) or not path.is_absolute():
            _fail("PWS0208_LIVE_OUTPUT_INVALID", "The output directory is invalid.")
        retained = open_handle(path, traverse=True)
        require_expected_handle(retained, path, directory=True)
        opened = retained.info
        current = retained.refresh_info()
        if (
            current.identity != opened.identity
            or not current.is_directory
            or current.is_reparse_point
        ):
            _fail("PWS0208_LIVE_OUTPUT_INVALID", "The output directory is invalid.")
        authority = _RetainedOutputDirectory(path, retained, opened.identity)
    except Pws0208LiveRunnerError:
        _close_output_handle_or_fail(retained)
        raise
    except WorkspaceError, OSError:
        _close_output_handle_or_fail(retained)
        _fail("PWS0208_LIVE_OUTPUT_INVALID", "The output directory is unavailable.")
    except BaseException:
        _close_output_handle_or_fail(retained)
        raise
    try:
        yield authority
    finally:
        _close_output_handle_or_fail(authority.handle)


def _require_output_state(authority: _RetainedOutputDirectory) -> None:
    """Require both fixed leaves to be absent beneath one retained authority."""

    try:
        authority.require_current()
        for name in (LIVE_REPORT_FILENAME, LIVE_DERIVATIVE_FILENAME):
            try:
                (authority.path / name).lstat()
            except FileNotFoundError:
                continue
            except OSError:
                _fail("PWS0208_LIVE_OUTPUT_INVALID", "The output state is unavailable.")
            _fail("PWS0208_LIVE_OUTPUT_EXISTS", "Existing evidence outputs were preserved.")
        authority.require_current()
    except Pws0208LiveRunnerError:
        raise
    except WorkspaceError, OSError:
        _fail("PWS0208_LIVE_OUTPUT_INVALID", "The output state is unavailable.")


def _validate_published_output(
    authority: _RetainedOutputDirectory,
    published: _PublishedOutput,
    payload: bytes,
) -> None:
    """Reopen and byte-verify one output under the retained namespace."""

    reader: WindowsHandle | None = None
    try:
        authority.require_current()
        identity = published.handle.info.identity
        require_expected_handle(
            published.handle,
            published.path,
            directory=False,
            expected_identity=identity,
        )
        current = published.handle.refresh_info()
        if (
            current.identity != identity
            or current.is_directory
            or current.is_reparse_point
            or current.is_sparse
            or current.link_count != 1
            or current.logical_size != len(payload)
        ):
            _fail("PWS0208_LIVE_OUTPUT_FAILED", "An evidence output changed unexpectedly.")
        reader = open_handle(
            published.path,
            read=True,
            share_delete=True,
            share_write=True,
        )
        require_expected_handle(
            reader,
            published.path,
            directory=False,
            expected_identity=identity,
        )
        if reader.read_bytes(maximum_bytes=max(1, len(payload))) != payload:
            _fail("PWS0208_LIVE_OUTPUT_FAILED", "An evidence output failed reopen validation.")
        require_expected_handle(
            reader,
            published.path,
            directory=False,
            expected_identity=identity,
        )
        authority.require_current()
    finally:
        if reader is not None:
            reader.close()


def _discard_exact_output(
    authority: _RetainedOutputDirectory,
    published: _PublishedOutput,
) -> None:
    """Delete the exact opened output, never a possibly replaced pathname."""

    cleanup_failed = False
    try:
        published.handle.mark_for_deletion()
    except BaseException:
        cleanup_failed = True
    try:
        published.handle.close()
    except BaseException:
        cleanup_failed = True
    try:
        authority.require_current()
    except BaseException:
        cleanup_failed = True
    if cleanup_failed:
        _fail("PWS0208_LIVE_CLEANUP_FAILED", "A partial evidence output could not be removed.")


def _discard_all_exact_outputs(
    authority: _RetainedOutputDirectory,
    *published: _PublishedOutput,
) -> None:
    """Attempt every exact-handle rollback before surfacing uncertainty."""

    cleanup_failed = False
    for item in published:
        try:
            _discard_exact_output(authority, item)
        except BaseException:
            cleanup_failed = True
    if cleanup_failed:
        _fail("PWS0208_LIVE_CLEANUP_FAILED", "Partial evidence cleanup was uncertain.")


def _close_published_outputs(
    authority: _RetainedOutputDirectory,
    *published: _PublishedOutput,
) -> None:
    """Release committed output handles while preserving the published bundle."""

    cleanup_failed = False
    for item in published:
        try:
            item.handle.close()
        except BaseException:
            cleanup_failed = True
    try:
        authority.require_current()
    except BaseException:
        cleanup_failed = True
    if cleanup_failed:
        _fail("PWS0208_LIVE_CLEANUP_FAILED", "An evidence authority could not be released.")


def _publish_new_output(
    authority: _RetainedOutputDirectory,
    *,
    destination_name: str,
    payload: bytes,
) -> _PublishedOutput:
    """Stage, verify, and rename one exact file beneath a retained directory."""

    stage = authority.path / (f"{_OUTPUT_STAGE_PREFIX}{uuid4().hex}{_OUTPUT_STAGE_SUFFIX}")
    destination = authority.path / destination_name
    stage_handle: WindowsHandle | None = None
    published: _PublishedOutput | None = None
    try:
        authority.require_current()
        stage_handle = write_new_file(stage, payload)
        stage_identity = stage_handle.info.identity
        staged = _PublishedOutput(stage, stage_handle)
        _validate_published_output(authority, staged, payload)
        renamed, error_code = rename_open_file_no_replace_at(
            stage_handle,
            authority.handle,
            destination_name=destination_name,
        )
        if not renamed:
            code = (
                "PWS0208_LIVE_OUTPUT_EXISTS"
                if error_code in _WINDOWS_ALREADY_EXISTS_ERRORS
                else "PWS0208_LIVE_OUTPUT_FAILED"
            )
            _fail(code, "An evidence output could not be published without replacement.")
        published = _PublishedOutput(destination, stage_handle)
        current = stage_handle.refresh_info()
        if current.identity != stage_identity or current.link_count != 1:
            _fail("PWS0208_LIVE_OUTPUT_FAILED", "An evidence output changed during publication.")
        _validate_published_output(authority, published, payload)
        return published
    except Pws0208LiveRunnerError:
        if stage_handle is not None:
            _discard_exact_output(
                authority,
                published or _PublishedOutput(stage, stage_handle),
            )
        raise
    except WorkspaceError, OSError:
        if stage_handle is not None:
            _discard_exact_output(
                authority,
                published or _PublishedOutput(stage, stage_handle),
            )
        _fail("PWS0208_LIVE_OUTPUT_FAILED", "An evidence output could not be published.")
    except BaseException:
        if stage_handle is not None:
            _discard_exact_output(
                authority,
                published or _PublishedOutput(stage, stage_handle),
            )
        raise


def _publish_bundle_with_authority(
    authority: _RetainedOutputDirectory,
    *,
    report_payload: bytes,
    derivative_payload: bytes,
) -> PublishedPws0208Artifacts:
    """Publish one report/derivative bundle through an already-retained authority."""

    _require_output_state(authority)
    try:
        report = decode_pws0208_resource_evidence(report_payload)
    except ValueError:
        _fail("PWS0208_LIVE_REPORT_INVALID", "The finalized evidence report is invalid.")
    if (
        report.overall_pass is not True
        or len(derivative_payload) != report.output_bytes
        or hashlib.sha256(derivative_payload).hexdigest() != report.output_sha256
        or not derivative_payload.startswith(b"%PDF-")
        or b"%%EOF" not in derivative_payload[-1024:]
    ):
        _fail("PWS0208_LIVE_DERIVATIVE_INVALID", "The derivative evidence is invalid.")

    derivative = _publish_new_output(
        authority,
        destination_name=LIVE_DERIVATIVE_FILENAME,
        payload=derivative_payload,
    )
    try:
        report_output = _publish_new_output(
            authority,
            destination_name=LIVE_REPORT_FILENAME,
            payload=report_payload,
        )
    except BaseException:
        _discard_exact_output(authority, derivative)
        raise
    try:
        _validate_published_output(authority, derivative, derivative_payload)
        _validate_published_output(authority, report_output, report_payload)
    except BaseException:
        _discard_all_exact_outputs(authority, report_output, derivative)
        raise
    _close_published_outputs(authority, report_output, derivative)
    return PublishedPws0208Artifacts(report=report_output.path, derivative=derivative.path)


def publish_bundle(
    output_directory: Path,
    *,
    report_payload: bytes,
    derivative_payload: bytes,
) -> PublishedPws0208Artifacts:
    """Publish derivative then report, rolling back the derivative pre-commit."""

    with _retain_output_directory(output_directory) as authority:
        return _publish_bundle_with_authority(
            authority,
            report_payload=report_payload,
            derivative_payload=derivative_payload,
        )


def _prepare_directory(path: Path, *, code: str) -> Path:
    try:
        selected = path.resolve()
        selected.mkdir(parents=True, exist_ok=True)
    except OSError:
        _fail(code, "A required local evidence directory is unavailable.")
    if not selected.is_dir() or _is_reparse(selected):
        _fail(code, "A required local evidence directory is invalid.")
    return selected


def _remove_private_run(run: Path, work: Path) -> None:
    """Remove one private run root and fail closed when absence is uncertain."""

    try:
        resolved_run = run.resolve(strict=True)
        if resolved_run.parent != work or not resolved_run.name.startswith(_RUN_PREFIX):
            raise OSError
        shutil.rmtree(resolved_run)
        run.lstat()
    except FileNotFoundError:
        return
    except OSError:
        _fail("PWS0208_LIVE_CLEANUP_FAILED", "The private workload stage remains.")
    _fail("PWS0208_LIVE_CLEANUP_FAILED", "The private workload stage remains.")


def run_live_pws0208_resource_workload(
    *,
    work_directory: Path,
    output_directory: Path,
    resource_policy: ResourcePolicy = CONSTRAINED_RESOURCE_POLICY_V1,
) -> PublishedPws0208Artifacts:
    """Execute the exact workload and publish validated local evidence."""

    if os.name != "nt":
        _fail("PWS0208_LIVE_PLATFORM_UNSUPPORTED", "The PWS-0208 live workload requires Windows.")
    if resource_policy is not CONSTRAINED_RESOURCE_POLICY_V1:
        _fail(
            "PWS0208_LIVE_POLICY_INVALID",
            "The fixed workload requires constrained resource policy version 1.",
        )
    try:
        validate_workload(WORKLOAD_PATH)
    except OSError, ValueError:
        _fail("PWS0208_LIVE_WORKLOAD_INVALID", "The checked-in workload is invalid.")
    revision = _git_revision()
    work = _prepare_directory(work_directory, code="PWS0208_LIVE_WORK_INVALID")
    output = _prepare_directory(output_directory, code="PWS0208_LIVE_OUTPUT_INVALID")
    with _retain_output_directory(output) as authority:
        _require_output_state(authority)
        run: Path | None = None
        try:
            run = Path(tempfile.mkdtemp(prefix=_RUN_PREFIX, dir=work))
            stage = run / "artifacts"
            basetemp = run / "pytest"
            stage.mkdir()
        except OSError:
            if run is not None:
                _remove_private_run(run, work)
            _fail("PWS0208_LIVE_WORK_INVALID", "The private workload stage is unavailable.")
        try:
            child, derivative, peak_rss, sample_count = _execute_child(
                stage,
                basetemp,
                revision,
            )
        finally:
            _remove_private_run(run, work)
        if _git_revision() != revision:
            _fail("PWS0208_LIVE_GIT_CHANGED", "The repository changed during evidence collection.")
        finalized = finalize_child_report(
            child,
            revision=revision,
            peak_complete_process_tree_rss_bytes=peak_rss,
            complete_process_tree_rss_sample_count=sample_count,
        )
        return _publish_bundle_with_authority(
            authority,
            report_payload=finalized,
            derivative_payload=derivative,
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--work-directory",
        type=Path,
        default=PROJECT_ROOT / ".paperworkshell-local" / "pws0208-resource-work",
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=PROJECT_ROOT / "output" / "pdf",
    )
    arguments = parser.parse_args(argv)
    try:
        artifacts = run_live_pws0208_resource_workload(
            work_directory=arguments.work_directory,
            output_directory=arguments.output_directory,
        )
    except Pws0208LiveRunnerError as error:
        print(f"ERROR [{error.code}] {error.safe_message}", file=sys.stderr)
        return 1
    print("OK: PWS-0208 constrained signed-source resource evidence passed.")
    print(f"Derivative: {artifacts.derivative.name}")
    print(f"Report: {artifacts.report.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

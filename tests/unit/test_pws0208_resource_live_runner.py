"""Unit coverage for safe PWS-0208 live evidence orchestration."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import traceback
from dataclasses import fields
from pathlib import Path

import pytest

import tools.pws0208_resource_live_runner as runner
from paperworkshell.domain.resource_policy import RESOURCE_PROFILE_ENVIRONMENT_VARIABLE
from tools.pws0208_resource_evidence import (
    Pws0208ResourceEvidence,
    decode_pws0208_resource_evidence,
    encode_pws0208_resource_evidence,
)
from tools.pws0208_resource_live_runner import Pws0208LiveRunnerError

REVISION = "a" * 40
DERIVATIVE = b"%" + b"PDF-1.4\n1 0 obj<<>>endobj\n%%EOF\n"


def _evidence(**changes: object) -> Pws0208ResourceEvidence:
    values: dict[str, object] = {
        "revision": REVISION,
        "input_bytes": 100,
        "output_bytes": len(DERIVATIVE),
        "output_sha256": hashlib.sha256(DERIVATIVE).hexdigest(),
        "preparing_duration_ms": 1,
        "confirmed_success_duration_ms": 1,
        "cancellation_duration_ms": 1,
        "limit_failure_duration_ms": 1,
        "retry_duration_ms": 1,
        "total_duration_ms": 5,
        "peak_complete_process_tree_rss_bytes": 1,
        "complete_process_tree_rss_sample_count": 1,
        "peak_worker_process_commit_bytes": 10,
        "peak_worker_job_commit_bytes": 20,
        "sampled_worker_private_temp_peak_bytes": 1,
        "worker_private_temp_sample_count": 1,
        "worker_private_temp_measurement_complete": True,
        "peak_active_full_resolution_page_buffers": 1,
        "peak_active_page_results": 1,
        "peak_heavy_operation_concurrency": 1,
        "cancellation_acknowledgement_ms": 1,
        "limit_failure_code": "PDF_EXPORT_LIMIT_EXCEEDED",
        "limit_failure_operation_correlated": True,
        "worker_tree_terminated_after_cancel": True,
        "worker_tree_terminated_after_limit": True,
        "cancel_cleanup_complete": True,
        "limit_cleanup_complete": True,
        "retry_succeeded": True,
        "originals_unchanged": True,
        "manifest_expected_mutations_only": True,
        "manifest_unchanged_after_cancel_and_limit": True,
        "destination_absent_after_cancel": True,
        "destination_absent_after_limit": True,
        "overall_pass": True,
    }
    values.update(changes)
    return Pws0208ResourceEvidence(**values)  # type: ignore[arg-type]


def test_finalize_replaces_only_complete_tree_measurement() -> None:
    child = _evidence()
    payload = runner.finalize_child_report(
        encode_pws0208_resource_evidence(child),
        revision=REVISION,
        peak_complete_process_tree_rss_bytes=123_456,
        complete_process_tree_rss_sample_count=73,
    )

    finalized = decode_pws0208_resource_evidence(payload)
    assert finalized.peak_complete_process_tree_rss_bytes == 123_456
    assert finalized.complete_process_tree_rss_sample_count == 73
    excluded = {
        "peak_complete_process_tree_rss_bytes",
        "complete_process_tree_rss_sample_count",
    }
    assert {
        item.name: getattr(finalized, item.name)
        for item in fields(finalized)
        if item.name not in excluded
    } == {
        item.name: getattr(child, item.name) for item in fields(child) if item.name not in excluded
    }


@pytest.mark.parametrize(
    "payload",
    (
        b"{}\n",
        encode_pws0208_resource_evidence(_evidence()).rstrip(b"\n"),
    ),
)
def test_finalize_rejects_invalid_or_noncanonical_child_report(payload: bytes) -> None:
    with pytest.raises(Pws0208LiveRunnerError) as raised:
        runner.finalize_child_report(
            payload,
            revision=REVISION,
            peak_complete_process_tree_rss_bytes=2,
            complete_process_tree_rss_sample_count=2,
        )

    assert raised.value.code == "PWS0208_LIVE_CHILD_REPORT_INVALID"


def test_finalize_rejects_revision_or_placeholder_substitution() -> None:
    for child, revision in (
        (_evidence(), "b" * 40),
        (_evidence(peak_complete_process_tree_rss_bytes=2), REVISION),
        (_evidence(complete_process_tree_rss_sample_count=2), REVISION),
    ):
        with pytest.raises(Pws0208LiveRunnerError) as raised:
            runner.finalize_child_report(
                encode_pws0208_resource_evidence(child),
                revision=revision,
                peak_complete_process_tree_rss_bytes=3,
                complete_process_tree_rss_sample_count=3,
            )
        assert raised.value.code == "PWS0208_LIVE_CHILD_REPORT_INVALID"


def test_finalize_rejects_complete_tree_budget_overrun() -> None:
    child = _evidence()

    with pytest.raises(Pws0208LiveRunnerError) as raised:
        runner.finalize_child_report(
            encode_pws0208_resource_evidence(child),
            revision=REVISION,
            peak_complete_process_tree_rss_bytes=(child.complete_process_tree_rss_limit_bytes + 1),
            complete_process_tree_rss_sample_count=1,
        )

    assert raised.value.code == "PWS0208_LIVE_RESOURCE_BUDGET_EXCEEDED"


def test_child_command_is_fixed_and_contains_no_document_values(tmp_path: Path) -> None:
    command = runner._child_command(tmp_path / "pytest")
    rendered = " ".join(command)

    assert command[:4] == (sys.executable, "-I", "-m", "pytest")
    assert command[-2:] == runner.LIVE_TEST_NODES
    for prohibited in (
        "ocr-readable-mixed",
        "ocr-raster-text",
        "signed-marker",
        ".pdf",
        "PRIVATE-",
    ):
        assert prohibited not in rendered


def test_child_environment_is_narrow_and_does_not_forward_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PWS0208_PRIVATE_SECRET", "do-not-forward")
    monkeypatch.setenv("PWS_TESSERACT_EXECUTABLE", r"C:\tools\tesseract.exe")

    environment = runner._child_environment(tmp_path, REVISION)

    assert "PWS0208_PRIVATE_SECRET" not in environment
    assert environment["PWS_TESSERACT_EXECUTABLE"] == r"C:\tools\tesseract.exe"
    assert environment[RESOURCE_PROFILE_ENVIRONMENT_VARIABLE] == "constrained"
    assert environment[runner.CHILD_ARTIFACT_DIRECTORY_ENVIRONMENT] == str(tmp_path)
    assert environment[runner.CHILD_REVISION_ENVIRONMENT] == REVISION


def test_bounded_child_read_rejects_oversize_and_hardlink(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized"
    oversized.write_bytes(b"x" * 11)
    with pytest.raises(Pws0208LiveRunnerError) as raised:
        runner._read_bounded_regular_file(oversized, maximum_bytes=10)
    assert raised.value.code == "PWS0208_LIVE_CHILD_ARTIFACT_INVALID"

    source = tmp_path / "source"
    alias = tmp_path / "alias"
    source.write_bytes(b"safe")
    os.link(source, alias)
    with pytest.raises(Pws0208LiveRunnerError) as raised:
        runner._read_bounded_regular_file(source, maximum_bytes=10)
    assert raised.value.code == "PWS0208_LIVE_CHILD_ARTIFACT_INVALID"


def test_sanitized_failure_severs_raw_exception_chain(tmp_path: Path) -> None:
    private_canary = tmp_path / "PRIVATE-path-value-canary"

    with pytest.raises(Pws0208LiveRunnerError) as raised:
        runner._read_bounded_regular_file(private_canary, maximum_bytes=10)

    assert raised.value.code == "PWS0208_LIVE_CHILD_ARTIFACT_INVALID"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    rendered = "".join(traceback.format_exception(raised.value))
    assert str(private_canary) not in rendered
    assert private_canary.name not in rendered


def test_publish_bundle_writes_canonical_artifacts_without_overwrite(tmp_path: Path) -> None:
    report = encode_pws0208_resource_evidence(_evidence())

    published = runner.publish_bundle(
        tmp_path.resolve(),
        report_payload=report,
        derivative_payload=DERIVATIVE,
    )

    assert published.report.read_bytes() == report
    assert published.derivative.read_bytes() == DERIVATIVE
    with pytest.raises(Pws0208LiveRunnerError) as raised:
        runner.publish_bundle(
            tmp_path.resolve(),
            report_payload=report,
            derivative_payload=DERIVATIVE,
        )
    assert raised.value.code == "PWS0208_LIVE_OUTPUT_EXISTS"
    assert published.report.read_bytes() == report
    assert published.derivative.read_bytes() == DERIVATIVE


def test_publish_bundle_rolls_back_derivative_when_report_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = runner._publish_new_output

    def fail_report(
        authority: runner._RetainedOutputDirectory,
        *,
        destination_name: str,
        payload: bytes,
    ) -> runner._PublishedOutput:
        if destination_name == runner.LIVE_REPORT_FILENAME:
            raise Pws0208LiveRunnerError("TEST_REPORT_FAILURE", "report failed")
        return original(
            authority,
            destination_name=destination_name,
            payload=payload,
        )

    monkeypatch.setattr(runner, "_publish_new_output", fail_report)

    with pytest.raises(Pws0208LiveRunnerError) as raised:
        runner.publish_bundle(
            tmp_path.resolve(),
            report_payload=encode_pws0208_resource_evidence(_evidence()),
            derivative_payload=DERIVATIVE,
        )

    assert raised.value.code == "TEST_REPORT_FAILURE"
    assert not (tmp_path / runner.LIVE_DERIVATIVE_FILENAME).exists()
    assert not (tmp_path / runner.LIVE_REPORT_FILENAME).exists()


def test_publish_bundle_surfaces_uncertain_derivative_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_publish = runner._publish_new_output
    original_discard = runner._discard_exact_output

    def fail_report(
        authority: runner._RetainedOutputDirectory,
        *,
        destination_name: str,
        payload: bytes,
    ) -> runner._PublishedOutput:
        if destination_name == runner.LIVE_REPORT_FILENAME:
            raise Pws0208LiveRunnerError("TEST_REPORT_FAILURE", "report failed")
        return original_publish(
            authority,
            destination_name=destination_name,
            payload=payload,
        )

    def fail_derivative_cleanup(
        authority: runner._RetainedOutputDirectory,
        published: runner._PublishedOutput,
    ) -> None:
        if published.path.name == runner.LIVE_DERIVATIVE_FILENAME:
            published.handle.close()
            raise Pws0208LiveRunnerError(
                "PWS0208_LIVE_CLEANUP_FAILED",
                "A partial evidence output could not be removed.",
            )
        original_discard(authority, published)

    monkeypatch.setattr(runner, "_publish_new_output", fail_report)
    monkeypatch.setattr(runner, "_discard_exact_output", fail_derivative_cleanup)

    with pytest.raises(Pws0208LiveRunnerError) as raised:
        runner.publish_bundle(
            tmp_path.resolve(),
            report_payload=encode_pws0208_resource_evidence(_evidence()),
            derivative_payload=DERIVATIVE,
        )

    assert raised.value.code == "PWS0208_LIVE_CLEANUP_FAILED"
    assert (tmp_path / runner.LIVE_DERIVATIVE_FILENAME).read_bytes() == DERIVATIVE


@pytest.mark.skipif(os.name != "nt", reason="retained-directory authority requires Windows")
def test_publish_bundle_blocks_output_directory_replacement_between_leaves(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_publish = runner._publish_new_output
    moved = tmp_path.parent / f"{tmp_path.name}-moved"
    replacement_was_blocked = False

    def attempt_replacement(
        authority: runner._RetainedOutputDirectory,
        *,
        destination_name: str,
        payload: bytes,
    ) -> runner._PublishedOutput:
        nonlocal replacement_was_blocked
        if destination_name == runner.LIVE_REPORT_FILENAME:
            try:
                tmp_path.rename(moved)
            except OSError:
                replacement_was_blocked = True
        return original_publish(
            authority,
            destination_name=destination_name,
            payload=payload,
        )

    monkeypatch.setattr(runner, "_publish_new_output", attempt_replacement)

    published = runner.publish_bundle(
        tmp_path.resolve(),
        report_payload=encode_pws0208_resource_evidence(_evidence()),
        derivative_payload=DERIVATIVE,
    )

    assert replacement_was_blocked
    assert not moved.exists()
    assert published.report.is_file()
    assert published.derivative.is_file()


@pytest.mark.skipif(os.name != "nt", reason="handle-bound rollback requires Windows")
def test_rollback_deletes_exact_derivative_and_preserves_leaf_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_publish = runner._publish_new_output
    derivative = tmp_path / runner.LIVE_DERIVATIVE_FILENAME
    moved_original = tmp_path / "moved-original.pdf"
    replacement = b"replacement-must-survive"

    def replace_leaf_then_fail_report(
        authority: runner._RetainedOutputDirectory,
        *,
        destination_name: str,
        payload: bytes,
    ) -> runner._PublishedOutput:
        if destination_name == runner.LIVE_REPORT_FILENAME:
            derivative.rename(moved_original)
            derivative.write_bytes(replacement)
            raise Pws0208LiveRunnerError("TEST_REPORT_FAILURE", "report failed")
        return original_publish(
            authority,
            destination_name=destination_name,
            payload=payload,
        )

    monkeypatch.setattr(runner, "_publish_new_output", replace_leaf_then_fail_report)

    with pytest.raises(Pws0208LiveRunnerError) as raised:
        runner.publish_bundle(
            tmp_path.resolve(),
            report_payload=encode_pws0208_resource_evidence(_evidence()),
            derivative_payload=DERIVATIVE,
        )

    assert raised.value.code == "TEST_REPORT_FAILURE"
    assert derivative.read_bytes() == replacement
    assert not moved_original.exists()
    assert not (tmp_path / runner.LIVE_REPORT_FILENAME).exists()


def test_bundle_rollback_attempts_both_exact_outputs_after_first_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    discarded: list[str] = []
    report_validations = 0
    original_validate = runner._validate_published_output
    original_discard = runner._discard_exact_output

    def fail_final_validation(
        authority: runner._RetainedOutputDirectory,
        published: runner._PublishedOutput,
        payload: bytes,
    ) -> None:
        nonlocal report_validations
        if published.path.name == runner.LIVE_REPORT_FILENAME:
            report_validations += 1
            if report_validations == 2:
                raise OSError("private-report-validation-canary")
        original_validate(authority, published, payload)

    def fail_first_cleanup(
        authority: runner._RetainedOutputDirectory,
        published: runner._PublishedOutput,
    ) -> None:
        discarded.append(published.path.name)
        if published.path.name == runner.LIVE_REPORT_FILENAME:
            original_discard(authority, published)
            raise OSError("private-report-cleanup-canary")
        original_discard(authority, published)

    monkeypatch.setattr(runner, "_validate_published_output", fail_final_validation)
    monkeypatch.setattr(runner, "_discard_exact_output", fail_first_cleanup)

    with pytest.raises(Pws0208LiveRunnerError) as raised:
        runner.publish_bundle(
            tmp_path.resolve(),
            report_payload=encode_pws0208_resource_evidence(_evidence()),
            derivative_payload=DERIVATIVE,
        )

    assert raised.value.code == "PWS0208_LIVE_CLEANUP_FAILED"
    assert discarded == [runner.LIVE_REPORT_FILENAME, runner.LIVE_DERIVATIVE_FILENAME]
    assert not (tmp_path / runner.LIVE_REPORT_FILENAME).exists()
    assert not (tmp_path / runner.LIVE_DERIVATIVE_FILENAME).exists()


@pytest.mark.parametrize(
    "derivative",
    (
        b"not a pdf",
        b"%PDF-1.4\nmissing terminator",
        DERIVATIVE + b"extra",
    ),
)
def test_publish_bundle_rejects_invalid_derivative(tmp_path: Path, derivative: bytes) -> None:
    with pytest.raises(Pws0208LiveRunnerError) as raised:
        runner.publish_bundle(
            tmp_path.resolve(),
            report_payload=encode_pws0208_resource_evidence(_evidence()),
            derivative_payload=derivative,
        )

    assert raised.value.code == "PWS0208_LIVE_DERIVATIVE_INVALID"
    assert not tuple(tmp_path.iterdir())


def test_publish_bundle_rejects_same_size_derivative_with_wrong_digest(tmp_path: Path) -> None:
    changed = DERIVATIVE.replace(b"1 0 obj", b"2 0 obj")
    assert len(changed) == len(DERIVATIVE)

    with pytest.raises(Pws0208LiveRunnerError) as raised:
        runner.publish_bundle(
            tmp_path.resolve(),
            report_payload=encode_pws0208_resource_evidence(_evidence()),
            derivative_payload=changed,
        )

    assert raised.value.code == "PWS0208_LIVE_DERIVATIVE_INVALID"
    assert not tuple(tmp_path.iterdir())


def test_main_reports_only_fixed_artifact_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    private_component = "private-local-profile"
    published = runner.PublishedPws0208Artifacts(
        report=tmp_path / private_component / runner.LIVE_REPORT_FILENAME,
        derivative=tmp_path / private_component / runner.LIVE_DERIVATIVE_FILENAME,
    )
    monkeypatch.setattr(
        runner,
        "run_live_pws0208_resource_workload",
        lambda **_arguments: published,
    )

    assert runner.main([]) == 0

    output = capsys.readouterr().out
    assert runner.LIVE_REPORT_FILENAME in output
    assert runner.LIVE_DERIVATIVE_FILENAME in output
    assert private_component not in output
    assert str(tmp_path) not in output


@pytest.mark.skipif(os.name != "nt", reason="retained-directory authority requires Windows")
def test_live_run_retains_output_directory_across_child_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = (tmp_path / "work").resolve()
    output = (tmp_path / "output").resolve()
    moved = (tmp_path / "moved-output").resolve()
    replacement_was_blocked = False

    def execute_child(
        _stage: Path,
        _basetemp: Path,
        _revision: str,
    ) -> tuple[bytes, bytes, int, int]:
        nonlocal replacement_was_blocked
        try:
            output.rename(moved)
        except OSError:
            replacement_was_blocked = True
        return encode_pws0208_resource_evidence(_evidence()), DERIVATIVE, 2, 2

    monkeypatch.setattr(runner, "_git_revision", lambda: REVISION)
    monkeypatch.setattr(runner, "_execute_child", execute_child)

    published = runner.run_live_pws0208_resource_workload(
        work_directory=work,
        output_directory=output,
    )

    assert replacement_was_blocked
    assert not moved.exists()
    assert published.report.is_file()
    assert published.derivative.is_file()
    assert work.is_dir() and not tuple(work.iterdir())


def test_live_setup_failure_removes_created_private_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = (tmp_path / "work").resolve()
    output = (tmp_path / "output").resolve()
    original_mkdir = Path.mkdir

    def fail_artifact_directory(path: Path, *args: object, **kwargs: object) -> None:
        if path.name == "artifacts":
            raise OSError("private-value-canary")
        original_mkdir(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(runner, "_git_revision", lambda: REVISION)
    monkeypatch.setattr(Path, "mkdir", fail_artifact_directory)

    with pytest.raises(Pws0208LiveRunnerError) as raised:
        runner.run_live_pws0208_resource_workload(
            work_directory=work,
            output_directory=output,
        )

    assert raised.value.code == "PWS0208_LIVE_WORK_INVALID"
    assert work.is_dir() and not tuple(work.iterdir())
    assert output.is_dir() and not tuple(output.iterdir())


def test_live_setup_cleanup_uncertainty_is_authoritative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = (tmp_path / "work").resolve()
    output = (tmp_path / "output").resolve()
    original_mkdir = Path.mkdir

    def fail_artifact_directory(path: Path, *args: object, **kwargs: object) -> None:
        if path.name == "artifacts":
            raise OSError("private-value-canary")
        original_mkdir(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(runner, "_git_revision", lambda: REVISION)
    monkeypatch.setattr(Path, "mkdir", fail_artifact_directory)
    monkeypatch.setattr(
        shutil,
        "rmtree",
        lambda _path: (_ for _ in ()).throw(OSError("cleanup-canary")),
    )

    with pytest.raises(Pws0208LiveRunnerError) as raised:
        runner.run_live_pws0208_resource_workload(
            work_directory=work,
            output_directory=output,
        )

    assert raised.value.code == "PWS0208_LIVE_CLEANUP_FAILED"
    assert len(tuple(work.iterdir())) == 1

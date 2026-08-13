"""PWS-0105/PWS-0206 security tests for the fixed qpdf command boundary."""

from __future__ import annotations

import io
import os
import subprocess
import time
from pathlib import Path
from typing import cast

import pytest

from paperworkshell.domain.pdf_inspection import (
    PdfInspectionError,
    PdfInspectionErrorCode,
    PdfInspectionLimits,
    PdfStructuralState,
)
from paperworkshell.pdf.qpdf_checker import (
    DEFAULT_QPDF_EXECUTABLE,
    QPDF_VERSION,
    QpdfCommandChecker,
)

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0105 qpdf command evidence targets the supported Windows platform",
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PDF_FIXTURES = PROJECT_ROOT / "tests/fixtures/synthetic/v1/pdf"


def _limits(**overrides: int) -> PdfInspectionLimits:
    values = {
        "maximum_pages": 100,
        "maximum_objects": 10_000,
        "maximum_input_bytes": 10_000_000,
        "maximum_temporary_bytes": 10_000_000,
        "maximum_output_bytes": 4_096,
        "maximum_tool_output_bytes": 1_000_000,
    }
    values.update(overrides)
    return PdfInspectionLimits(**values)


class _CountingStream(io.BytesIO):
    def __init__(self, value: bytes) -> None:
        super().__init__(value)
        self.bytes_read = 0

    def read(self, size: int | None = -1, /) -> bytes:
        value = super().read(size)
        self.bytes_read += len(value)
        return value


class _FakeProcess:
    def __init__(
        self,
        return_code: int | None,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        terminate_exits: bool = True,
    ) -> None:
        self._return_code = return_code
        self.stdin = io.BytesIO()
        self.stdout = _CountingStream(stdout)
        self.stderr = _CountingStream(stderr)
        self.terminate_exits = terminate_exits
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self) -> int | None:
        return self._return_code

    def terminate(self) -> None:
        self.terminate_calls += 1
        if self.terminate_exits:
            self._return_code = -15

    def kill(self) -> None:
        self.kill_calls += 1
        self._return_code = -9

    def wait(self, timeout: float | None = None) -> int:
        if self._return_code is None:
            raise subprocess.TimeoutExpired(
                "fixed-qpdf",
                0.0 if timeout is None else timeout,
            )
        return self._return_code


def _fake_checker(tmp_path: Path) -> QpdfCommandChecker:
    executable = (tmp_path / "fixed-qpdf.exe").resolve()
    executable.write_bytes(b"synthetic executable marker")
    return QpdfCommandChecker(
        executable=executable,
        poll_seconds=0.001,
        termination_grace_seconds=0.01,
    )


def _required_qpdf_checker() -> QpdfCommandChecker:
    configured = os.environ.get("PWS_QPDF_EXECUTABLE")
    executable = DEFAULT_QPDF_EXECUTABLE if configured is None else Path(configured)
    assert executable.is_absolute(), "required qpdf path is not absolute"
    assert executable.is_file(), "required qpdf 12.3.2 is not installed"
    return QpdfCommandChecker(executable=executable)


def _install_fake_launch(
    monkeypatch: pytest.MonkeyPatch,
    process: _FakeProcess,
) -> tuple[dict[str, object], list[tuple[str, ...]]]:
    captured_keywords: dict[str, object] = {}
    captured_arguments: list[tuple[str, ...]] = []

    def fake_popen(arguments: tuple[str, ...], **keywords: object) -> _FakeProcess:
        captured_arguments.append(arguments)
        captured_keywords.update(keywords)
        return process

    monkeypatch.setattr(
        "paperworkshell.pdf.qpdf_checker.subprocess.Popen",
        fake_popen,
    )
    return captured_keywords, captured_arguments


def test_required_fixed_qpdf_checks_clean_and_malformed_fixtures() -> None:
    assert QPDF_VERSION == "12.3.2"
    assert DEFAULT_QPDF_EXECUTABLE == Path(r"C:\Program Files\qpdf 12.3.2\bin\qpdf.exe")
    checker = _required_qpdf_checker()

    clean = checker.check(
        (PDF_FIXTURES / "blank.pdf").resolve(),
        limits=_limits(),
        deadline=time.monotonic() + 30,
    )
    malformed = checker.check(
        (PDF_FIXTURES / "malformed-truncated.pdf").resolve(),
        limits=_limits(),
        deadline=time.monotonic() + 30,
    )

    assert clean is PdfStructuralState.CLEAN
    assert malformed is PdfStructuralState.FAILED


def test_metacharacter_filename_is_one_argv_value_and_cannot_inject(
    tmp_path: Path,
) -> None:
    source = (tmp_path / "source & echo %COMSPEC% (never-run).pdf").resolve()
    source.write_bytes((PDF_FIXTURES / "blank.pdf").read_bytes())
    sentinel = tmp_path / "qpdf-command-injection-sentinel.bin"

    result = _required_qpdf_checker().check(
        source,
        limits=_limits(),
        deadline=time.monotonic() + 30,
    )

    assert result is PdfStructuralState.CLEAN
    assert not sentinel.exists()


def test_launch_uses_fixed_argv_separator_no_shell_and_minimal_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checker = _fake_checker(tmp_path)
    source = (tmp_path / "input & metacharacters.pdf").resolve()
    process = _FakeProcess(0)
    captured_keywords, captured_arguments = _install_fake_launch(monkeypatch, process)
    monkeypatch.setenv("PATH", "PRIVATE-PATH-CANARY")
    monkeypatch.setenv("PATHEXT", "PRIVATE-PATHEXT-CANARY")
    monkeypatch.setenv("HTTP_PROXY", "PRIVATE-PROXY-CANARY")
    monkeypatch.setenv("COMSPEC", "PRIVATE-COMMAND-CANARY")

    result = checker.check(
        source,
        limits=_limits(),
        deadline=time.monotonic() + 30,
    )

    assert result is PdfStructuralState.CLEAN
    assert captured_arguments == [
        (
            os.fspath(checker.executable),
            "--check",
            "--suppress-recovery",
            "--no-warn",
            "--",
            os.fspath(source),
        )
    ]
    assert captured_keywords["shell"] is False
    assert captured_keywords["stdin"] is subprocess.PIPE
    assert process.stdin.closed
    assert captured_keywords["stdout"] is subprocess.PIPE
    assert captured_keywords["stderr"] is subprocess.PIPE
    assert captured_keywords["close_fds"] is True
    assert captured_keywords["cwd"] == checker.executable.parent
    environment = cast(dict[str, str], captured_keywords["env"])
    assert set(environment) <= {"SYSTEMROOT", "WINDIR", "LOCALAPPDATA", "TEMP", "TMP"}
    assert "PATH" not in environment
    assert "PATHEXT" not in environment
    assert "HTTP_PROXY" not in environment
    assert "COMSPEC" not in environment


def test_exact_version_probe_is_bounded_no_shell_and_uses_explicit_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checker = _fake_checker(tmp_path)
    process = _FakeProcess(
        0,
        stdout=(
            f"qpdf version {QPDF_VERSION}\r\n"
            "Run qpdf --copyright to see copyright and license information.\r\n"
        ).encode("ascii"),
    )
    captured_keywords, captured_arguments = _install_fake_launch(monkeypatch, process)

    checker.require_trusted_version(deadline=time.monotonic() + 30)

    assert captured_arguments == [(os.fspath(checker.executable), "--version")]
    assert captured_keywords["executable"] == os.fspath(checker.executable)
    assert captured_keywords["shell"] is False
    assert captured_keywords["stdin"] is subprocess.PIPE
    assert process.stdin.closed
    assert captured_keywords["stdout"] is subprocess.PIPE
    assert captured_keywords["stderr"] is subprocess.PIPE
    assert captured_keywords["close_fds"] is True
    assert captured_keywords["cwd"] == checker.executable.parent


def test_version_probe_rejects_mismatch_without_disclosing_raw_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_token = "PRIVATE-QPDF-VERSION:C:\\private\\applicant.pdf"
    checker = _fake_checker(tmp_path)
    _install_fake_launch(
        monkeypatch,
        _FakeProcess(
            0,
            stdout=f"qpdf version 99.0.0\n{private_token}\n".encode("ascii"),
        ),
    )

    with pytest.raises(PdfInspectionError) as raised:
        checker.require_trusted_version(deadline=time.monotonic() + 30)

    rendered = f"{raised.value!s} {raised.value!r} {caplog.text}"
    assert raised.value.code is PdfInspectionErrorCode.DEPENDENCY_UNAVAILABLE
    assert raised.value.context == "qpdf.version"
    assert private_token not in rendered
    assert "applicant.pdf" not in rendered


def test_version_probe_stops_combined_output_over_its_private_fixed_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checker = _fake_checker(tmp_path)
    process = _FakeProcess(
        None,
        stdout=b"A" * 8_192,
        stderr=b"B" * 8_192,
    )
    _install_fake_launch(monkeypatch, process)

    with pytest.raises(PdfInspectionError) as raised:
        checker.require_trusted_version(deadline=time.monotonic() + 30)

    assert raised.value.code is PdfInspectionErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "qpdf.tool_output"
    assert process.stdout.bytes_read > 0
    assert process.stderr.bytes_read > 0
    assert process.terminate_calls == 1
    assert process.poll() is not None


@pytest.mark.parametrize(
    ("return_code", "expected"),
    [
        (0, PdfStructuralState.CLEAN),
        (3, PdfStructuralState.WARNINGS),
        (2, PdfStructuralState.FAILED),
    ],
)
def test_only_documented_qpdf_return_codes_cross_the_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    return_code: int,
    expected: PdfStructuralState,
) -> None:
    checker = _fake_checker(tmp_path)
    _install_fake_launch(monkeypatch, _FakeProcess(return_code))

    result = checker.check(
        (tmp_path / "source.pdf").resolve(),
        limits=_limits(),
        deadline=time.monotonic() + 30,
    )

    assert result is expected


def test_concurrent_stdout_stderr_flood_is_discarded_then_stopped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checker = _fake_checker(tmp_path)
    process = _FakeProcess(
        None,
        stdout=b"A" * 8_192,
        stderr=b"B" * 8_192,
    )
    _install_fake_launch(monkeypatch, process)

    with pytest.raises(PdfInspectionError) as raised:
        checker.check(
            (tmp_path / "source.pdf").resolve(),
            limits=_limits(maximum_tool_output_bytes=128),
            deadline=time.monotonic() + 30,
        )

    assert raised.value.code is PdfInspectionErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "qpdf.tool_output"
    assert process.stdout.bytes_read > 0
    assert process.stderr.bytes_read > 0
    assert process.terminate_calls == 1
    assert process.poll() is not None


def test_timeout_escalates_from_terminate_to_kill_and_leaves_no_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checker = _fake_checker(tmp_path)
    process = _FakeProcess(None, terminate_exits=False)
    _install_fake_launch(monkeypatch, process)
    ticks = iter((0.0, 2.0))

    with pytest.raises(PdfInspectionError) as raised:
        checker.check(
            (tmp_path / "source.pdf").resolve(),
            limits=_limits(),
            deadline=1.0,
            monotonic=lambda: next(ticks),
        )

    assert raised.value.code is PdfInspectionErrorCode.TIMED_OUT
    assert process.terminate_calls == 1
    assert process.kill_calls == 1
    assert process.poll() is not None


def test_cancellation_after_launch_cleans_up_the_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checker = _fake_checker(tmp_path)
    process = _FakeProcess(None)
    _install_fake_launch(monkeypatch, process)
    checkpoints = 0

    def cancellation_probe() -> bool:
        nonlocal checkpoints
        checkpoints += 1
        return checkpoints >= 2

    with pytest.raises(PdfInspectionError) as raised:
        checker.check(
            (tmp_path / "source.pdf").resolve(),
            limits=_limits(),
            deadline=time.monotonic() + 30,
            cancellation_probe=cancellation_probe,
        )

    assert raised.value.code is PdfInspectionErrorCode.CANCELLED
    assert process.terminate_calls == 1
    assert process.poll() is not None


def test_raw_tool_output_and_unexpected_code_are_never_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_token = "PRIVATE-QPDF-OUTPUT:C:\\private\\applicant.pdf"
    checker = _fake_checker(tmp_path)
    process = _FakeProcess(
        17,
        stdout=private_token.encode("ascii"),
        stderr=private_token.encode("ascii"),
    )
    _install_fake_launch(monkeypatch, process)

    with pytest.raises(PdfInspectionError) as raised:
        checker.check(
            (tmp_path / "source.pdf").resolve(),
            limits=_limits(),
            deadline=time.monotonic() + 30,
        )

    rendered = f"{raised.value!s} {raised.value!r} {caplog.text}"
    assert raised.value.code is PdfInspectionErrorCode.DEPENDENCY_FAILED
    assert private_token not in rendered
    assert "applicant.pdf" not in rendered

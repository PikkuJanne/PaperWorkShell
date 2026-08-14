"""Focused tests for the fixed, bounded Tesseract subprocess boundary."""

from __future__ import annotations

import io
import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from paperworkshell.ocr.tesseract_runner import (
    TesseractCommandRunner,
    TesseractRunnerError,
    TesseractRunnerErrorCode,
    is_tesseract_completed_process,
)


@dataclass(slots=True)
class _Control:
    cancelled: bool = False
    expired: bool = False

    def cancellation_requested(self) -> bool:
        return self.cancelled

    def deadline_expired(self) -> bool:
        return self.expired


def test_completed_process_admission_stays_exact_inside_runner_authority() -> None:
    completed = subprocess.CompletedProcess(("approved",), 0, stdout=b"", stderr=None)

    assert is_tesseract_completed_process(completed)
    assert not is_tesseract_completed_process(object())

    class _DerivedCompletedProcess(subprocess.CompletedProcess[bytes]):
        pass

    assert not is_tesseract_completed_process(
        _DerivedCompletedProcess(("approved",), 0, stdout=b"", stderr=None)
    )


class _Process:
    def __init__(
        self,
        output: bytes,
        *,
        error_output: bytes = b"",
        running: bool = False,
    ) -> None:
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(output)
        self.stderr = io.BytesIO(error_output)
        self.return_code: int | None = None if running else 0
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.return_code

    def terminate(self) -> None:
        self.terminated = True
        self.return_code = 1

    def kill(self) -> None:
        self.killed = True
        self.return_code = 1

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if self.return_code is None:
            raise subprocess.TimeoutExpired(("approved",), 1)
        return self.return_code


class _StubbornProcess(_Process):
    """Ignore graceful termination so timeout handling must use the kill path."""

    def terminate(self) -> None:
        self.terminated = True


class _FailOnceCloseStream(io.BytesIO):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        if self.close_calls == 1:
            raise OSError("private transient close detail")
        super().close()


class _ControlledCloseFailureStream(io.BytesIO):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.allow_close = False

    def close(self) -> None:
        if not self.allow_close:
            raise OSError("private persistent close detail")
        super().close()


def _runner(tmp_path: Path, control: Any = None, **kwargs: Any) -> TesseractCommandRunner:
    executable = tmp_path / "bin" / "tesseract.exe"
    tessdata = tmp_path / "tessdata"
    private_temp = tmp_path / "private"
    executable.parent.mkdir()
    tessdata.mkdir()
    private_temp.mkdir()
    executable.write_bytes(b"synthetic executable")
    return TesseractCommandRunner(
        executable=executable,
        tessdata_directory=tessdata,
        temporary_directory=private_temp,
        control=control or _Control(),
        environment={"SYSTEMROOT": r"C:\Windows", "PATH": "ambient-canary"},
        **kwargs,
    )


def test_runner_replaces_only_argv_zero_and_uses_no_shell_or_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocation: dict[str, object] = {}

    def popen(arguments: tuple[str, ...], **kwargs: object) -> _Process:
        invocation["arguments"] = arguments
        invocation.update(kwargs)
        return _Process(b"tesseract 5.5.1\n")

    monkeypatch.setattr("paperworkshell.ocr.tesseract_runner.subprocess.Popen", popen)
    runner = _runner(tmp_path)

    completed = runner.run(
        ("tesseract", "--version", "literal;not-a-shell"),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )

    assert invocation["arguments"] == (
        os.fspath(runner.executable),
        "--version",
        "literal;not-a-shell",
    )
    assert invocation["shell"] is False
    assert invocation["cwd"] == runner.executable.parent
    environment = invocation["env"]
    assert isinstance(environment, dict)
    assert "PATH" not in environment
    assert environment["TESSDATA_PREFIX"] == os.fspath(runner.tessdata_directory)
    assert environment["OMP_THREAD_LIMIT"] == "1"
    assert completed.args == ("<approved-tesseract>",)


def test_popen_oserror_is_an_infrastructure_failure_after_resource_precheck(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary = "private ACL or quota detail"

    def fail_launch(*_args: object, **_kwargs: object) -> None:
        raise OSError(canary)

    monkeypatch.setattr(
        "paperworkshell.ocr.tesseract_runner.subprocess.Popen",
        fail_launch,
    )

    with pytest.raises(TesseractRunnerError) as rejected:
        _runner(tmp_path).run(("tesseract", "--version"))

    assert rejected.value.code is TesseractRunnerErrorCode.FAILED
    assert rejected.value.context == "tesseract.launch"
    assert canary not in repr(rejected.value)
    assert canary not in str(rejected.value)


def test_initial_clock_failure_terminates_child_closes_pipes_and_joins_drainers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _Process(b"", running=True)
    threads: list[threading.Thread] = []
    original_start = threading.Thread.start

    def record_start(thread: threading.Thread) -> None:
        threads.append(thread)
        original_start(thread)

    monkeypatch.setattr(threading.Thread, "start", record_start)
    monkeypatch.setattr(
        "paperworkshell.ocr.tesseract_runner.subprocess.Popen",
        lambda *_args, **_kwargs: process,
    )

    def fail_clock() -> float:
        raise RuntimeError("private clock detail")

    with pytest.raises(TesseractRunnerError) as rejected:
        _runner(tmp_path, monotonic=fail_clock).run(("tesseract", "--version"))

    assert rejected.value.code is TesseractRunnerErrorCode.FAILED
    assert rejected.value.context == "tesseract.clock"
    assert process.terminated
    assert process.stdin.closed and process.stdout.closed and process.stderr.closed
    assert len(threads) == 2
    assert all(not thread.is_alive() for thread in threads)
    assert "private" not in f"{rejected.value!r} {rejected.value}"


def test_second_drainer_start_failure_terminates_child_and_joins_first_drainer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _Process(b"", running=True)
    threads: list[threading.Thread] = []
    original_start = threading.Thread.start

    def fail_second_start(thread: threading.Thread) -> None:
        threads.append(thread)
        if len(threads) == 2:
            original_start(thread)
            raise RuntimeError("private thread-start detail")
        original_start(thread)

    monkeypatch.setattr(threading.Thread, "start", fail_second_start)
    monkeypatch.setattr(
        "paperworkshell.ocr.tesseract_runner.subprocess.Popen",
        lambda *_args, **_kwargs: process,
    )

    with pytest.raises(TesseractRunnerError) as rejected:
        _runner(tmp_path).run(("tesseract", "--version"))

    assert rejected.value.code is TesseractRunnerErrorCode.FAILED
    assert rejected.value.context == "tesseract.startup"
    assert process.terminated
    assert process.stdin.closed and process.stdout.closed and process.stderr.closed
    assert len(threads) == 2
    assert all(not thread.is_alive() for thread in threads)
    assert "private" not in f"{rejected.value!r} {rejected.value}"


def test_missing_pipe_and_stdin_close_failure_remain_inside_cleanup_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_pipe_root = tmp_path / "missing-pipe"
    stdin_failure_root = tmp_path / "stdin-failure"
    missing_pipe_root.mkdir()
    stdin_failure_root.mkdir()
    missing_pipe_process = _Process(b"", running=True)
    missing_pipe_process.stderr = None  # type: ignore[assignment]
    monkeypatch.setattr(
        "paperworkshell.ocr.tesseract_runner.subprocess.Popen",
        lambda *_args, **_kwargs: missing_pipe_process,
    )

    with pytest.raises(TesseractRunnerError) as missing_pipe:
        _runner(missing_pipe_root).run(("tesseract", "--version"))

    assert missing_pipe.value.code is TesseractRunnerErrorCode.FAILED
    assert missing_pipe_process.terminated
    assert missing_pipe_process.stdin.closed and missing_pipe_process.stdout.closed

    stdin_failure_process = _Process(b"", running=True)
    failing_stdin = _FailOnceCloseStream(b"")
    stdin_failure_process.stdin = failing_stdin
    monkeypatch.setattr(
        "paperworkshell.ocr.tesseract_runner.subprocess.Popen",
        lambda *_args, **_kwargs: stdin_failure_process,
    )

    with pytest.raises(TesseractRunnerError) as stdin_failure:
        _runner(stdin_failure_root).run(("tesseract", "--version"))

    assert stdin_failure.value.code is TesseractRunnerErrorCode.FAILED
    assert stdin_failure.value.context == "tesseract.stdin"
    assert stdin_failure_process.terminated
    assert failing_stdin.closed
    assert stdin_failure_process.stdout.closed and stdin_failure_process.stderr.closed


def test_stream_close_failure_has_sanitized_cleanup_failed_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _Process(b"tesseract 5.5.1\n")
    failing_error_stream = _ControlledCloseFailureStream(b"")
    process.stderr = failing_error_stream
    monkeypatch.setattr(
        "paperworkshell.ocr.tesseract_runner.subprocess.Popen",
        lambda *_args, **_kwargs: process,
    )

    try:
        with pytest.raises(TesseractRunnerError) as rejected:
            _runner(tmp_path).run(("tesseract", "--version"))

        assert rejected.value.code is TesseractRunnerErrorCode.CLEANUP_FAILED
        assert rejected.value.context == "tesseract.cleanup"
        assert process.stdin.closed and process.stdout.closed
        assert not failing_error_stream.closed
        assert "private" not in f"{rejected.value!r} {rejected.value}"
    finally:
        failing_error_stream.allow_close = True
        failing_error_stream.close()


def test_runner_bounds_output_without_retaining_it_in_the_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary = b"private-document-token"
    monkeypatch.setattr(
        "paperworkshell.ocr.tesseract_runner.subprocess.Popen",
        lambda *args, **kwargs: _Process(canary * 10),
    )
    runner = _runner(tmp_path, maximum_output_bytes=8)

    with pytest.raises(TesseractRunnerError) as raised:
        runner.run(("tesseract", "--version"))

    assert raised.value.code is TesseractRunnerErrorCode.OUTPUT_LIMIT
    assert canary.decode() not in repr(raised.value)
    assert canary.decode() not in str(raised.value)


def test_cancellation_terminates_and_drains_the_direct_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _Process(b"", running=True)

    class _CancellingControl:
        def __init__(self) -> None:
            self.checks = 0

        def cancellation_requested(self) -> bool:
            self.checks += 1
            return self.checks >= 2

        def deadline_expired(self) -> bool:
            return False

    control = _CancellingControl()
    monkeypatch.setattr(
        "paperworkshell.ocr.tesseract_runner.subprocess.Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr(
        "paperworkshell.ocr.tesseract_runner.time.sleep",
        lambda _seconds: None,
    )

    with pytest.raises(TesseractRunnerError) as raised:
        _runner(tmp_path, control).run(("tesseract", "--version"))

    assert raised.value.code is TesseractRunnerErrorCode.CANCELLED
    assert process.terminated


def test_local_timeout_terminates_then_kills_the_direct_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _StubbornProcess(b"", running=True)
    clock = iter((10.0, 11.0))
    monkeypatch.setattr(
        "paperworkshell.ocr.tesseract_runner.subprocess.Popen",
        lambda *args, **kwargs: process,
    )

    with pytest.raises(subprocess.TimeoutExpired) as raised:
        _runner(tmp_path, monotonic=lambda: next(clock)).run(
            ("tesseract", "--version"),
            timeout=1.0,
        )

    assert raised.value.cmd == ("<approved-tesseract>",)
    assert process.terminated
    assert process.killed
    assert process.poll() is not None


@pytest.mark.parametrize(
    "arguments",
    [
        ("ambient-tesseract", "--version"),
        ("tesseract", "bad\nargument"),
        ("tesseract", "bad\x00argument"),
    ],
)
def test_runner_rejects_untrusted_command_shapes(
    tmp_path: Path,
    arguments: tuple[str, ...],
) -> None:
    with pytest.raises(TesseractRunnerError) as raised:
        _runner(tmp_path).run(arguments)

    assert raised.value.code is TesseractRunnerErrorCode.CONFIGURATION


def test_runner_refuses_thread_limit_escalation(tmp_path: Path) -> None:
    with pytest.raises(TesseractRunnerError) as raised:
        _runner(tmp_path).run(
            ("tesseract", "--version"),
            env={"OMP_THREAD_LIMIT": "8"},
        )

    assert raised.value.code is TesseractRunnerErrorCode.CONFIGURATION


def test_recognition_output_is_counted_but_discarded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary = b"recognized-private-token"
    monkeypatch.setattr(
        "paperworkshell.ocr.tesseract_runner.subprocess.Popen",
        lambda *args, **kwargs: _Process(canary),
    )

    completed = _runner(tmp_path).run(
        ("tesseract", "page.png", "page", "hocr", "txt"),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )

    assert completed.stdout == b""
    assert canary not in repr(completed).encode()


def test_exact_quality_tsv_stdout_is_bounded_and_returned_only_to_worker_consumer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tsv = b"level\tpage_num\tconf\ttext\n"
    diagnostic = b"Estimating resolution as 144\n"
    monkeypatch.setattr(
        "paperworkshell.ocr.tesseract_runner.subprocess.Popen",
        lambda *args, **kwargs: _Process(tsv, error_output=diagnostic),
    )
    input_path = (tmp_path / "private" / "sample.ppm").resolve()

    completed = _runner(tmp_path, maximum_output_bytes=len(tsv) + len(diagnostic)).run(
        (
            "tesseract",
            input_path,
            "stdout",
            "-l",
            "eng",
            "--psm",
            "6",
            "tsv",
        ),
        check=False,
    )

    assert completed.stdout == tsv
    assert diagnostic not in completed.stdout
    assert diagnostic.decode("ascii").strip() not in repr(completed)


def test_quality_shaped_call_outside_private_temp_cannot_retain_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary = b"recognized-private-token"
    monkeypatch.setattr(
        "paperworkshell.ocr.tesseract_runner.subprocess.Popen",
        lambda *args, **kwargs: _Process(canary),
    )
    outside_input = (tmp_path / "outside" / "sample.ppm").resolve()

    completed = _runner(tmp_path).run(
        (
            "tesseract",
            outside_input,
            "stdout",
            "-l",
            "eng",
            "--psm",
            "6",
            "tsv",
        ),
        check=False,
    )

    assert completed.stdout == b""
    assert canary not in repr(completed).encode()


def test_quality_tsv_stdout_over_limit_terminates_without_error_leakage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary = b"recognized-private-token"
    process = _Process(canary * 100, running=True)
    monkeypatch.setattr(
        "paperworkshell.ocr.tesseract_runner.subprocess.Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr("paperworkshell.ocr.tesseract_runner.time.sleep", lambda _value: None)
    input_path = (tmp_path / "private" / "sample.ppm").resolve()

    with pytest.raises(TesseractRunnerError) as rejected:
        _runner(tmp_path, maximum_output_bytes=32).run(
            (
                "tesseract",
                input_path,
                "stdout",
                "-l",
                "eng",
                "--psm",
                "6",
                "tsv",
            ),
            check=False,
        )

    assert rejected.value.code is TesseractRunnerErrorCode.OUTPUT_LIMIT
    assert process.terminated
    assert canary.decode("ascii") not in repr(rejected.value)

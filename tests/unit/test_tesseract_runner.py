"""Focused tests for the fixed, bounded Tesseract subprocess boundary."""

from __future__ import annotations

import io
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from paperworkshell.ocr.tesseract_runner import (
    TesseractCommandRunner,
    TesseractRunnerError,
    TesseractRunnerErrorCode,
)


@dataclass(slots=True)
class _Control:
    cancelled: bool = False
    expired: bool = False

    def cancellation_requested(self) -> bool:
        return self.cancelled

    def deadline_expired(self) -> bool:
        return self.expired


class _Process:
    def __init__(self, output: bytes, *, running: bool = False) -> None:
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(output)
        self.stderr = None
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

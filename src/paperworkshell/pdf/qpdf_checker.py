"""Fixed-command qpdf structural checker with bounded output disposal."""

from __future__ import annotations

import math
import os
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final, cast

from paperworkshell.domain.pdf_inspection import (
    PdfInspectionError,
    PdfInspectionErrorCode,
    PdfInspectionLimits,
    PdfStructuralState,
    reject_pdf_inspection,
)

CancellationProbe = Callable[[], bool]
MonotonicClock = Callable[[], float]

QPDF_VERSION: Final = "12.3.2"
DEFAULT_QPDF_EXECUTABLE: Final = Path(r"C:\Program Files\qpdf 12.3.2\bin\qpdf.exe")
_OUTPUT_CHUNK_BYTES: Final = 65_536
_DEFAULT_POLL_SECONDS: Final = 0.01
_DEFAULT_TERMINATION_GRACE_SECONDS: Final = 1.0


def _never_cancel() -> bool:
    return False


@dataclass(frozen=True, slots=True)
class _CommandOperation:
    cancellation_probe: CancellationProbe
    deadline: float
    monotonic: MonotonicClock

    def checkpoint(self) -> None:
        try:
            cancelled = self.cancellation_probe()
        except Exception:
            reject_pdf_inspection(PdfInspectionErrorCode.INTERNAL_ERROR, "qpdf.cancel_probe")
        if type(cancelled) is not bool:
            reject_pdf_inspection(PdfInspectionErrorCode.INTERNAL_ERROR, "qpdf.cancel_probe")
        if cancelled:
            reject_pdf_inspection(PdfInspectionErrorCode.CANCELLED, "qpdf.operation")
        try:
            current = self.monotonic()
        except Exception:
            reject_pdf_inspection(PdfInspectionErrorCode.INTERNAL_ERROR, "qpdf.clock")
        if type(current) not in (int, float) or not math.isfinite(current):
            reject_pdf_inspection(PdfInspectionErrorCode.INTERNAL_ERROR, "qpdf.clock")
        if current >= self.deadline:
            reject_pdf_inspection(PdfInspectionErrorCode.TIMED_OUT, "qpdf.operation")


class _DiscardBudget:
    """Count two pipe readers atomically without retaining their bytes."""

    def __init__(self, maximum_bytes: int) -> None:
        self._maximum_bytes = maximum_bytes
        self._consumed_bytes = 0
        self._lock = threading.Lock()
        self.exceeded = threading.Event()
        self.failed = threading.Event()

    def consume(self, byte_count: int) -> None:
        with self._lock:
            self._consumed_bytes += byte_count
            if self._consumed_bytes > self._maximum_bytes:
                self.exceeded.set()


@dataclass(frozen=True, slots=True, repr=False)
class QpdfCommandChecker:
    """Invoke one trusted absolute qpdf executable without a shell."""

    executable: Path = DEFAULT_QPDF_EXECUTABLE
    poll_seconds: float = _DEFAULT_POLL_SECONDS
    termination_grace_seconds: float = _DEFAULT_TERMINATION_GRACE_SECONDS

    def __post_init__(self) -> None:
        if (
            not isinstance(self.executable, Path)
            or not self.executable.is_absolute()
            or type(self.poll_seconds) not in (int, float)
            or not math.isfinite(self.poll_seconds)
            or self.poll_seconds <= 0
            or type(self.termination_grace_seconds) not in (int, float)
            or not math.isfinite(self.termination_grace_seconds)
            or self.termination_grace_seconds <= 0
        ):
            reject_pdf_inspection(PdfInspectionErrorCode.MODEL_INVALID, "qpdf.configuration")

    def __repr__(self) -> str:
        return "QpdfCommandChecker(<approved fixed executable>)"

    def check(
        self,
        source_path: Path,
        *,
        limits: PdfInspectionLimits,
        deadline: float,
        cancellation_probe: CancellationProbe = _never_cancel,
        monotonic: MonotonicClock = time.monotonic,
    ) -> PdfStructuralState:
        """Map qpdf's documented check codes without retaining its raw output."""

        if type(limits) is not PdfInspectionLimits:
            reject_pdf_inspection(PdfInspectionErrorCode.MODEL_INVALID, "qpdf.limits")
        if not isinstance(source_path, Path) or not source_path.is_absolute():
            reject_pdf_inspection(PdfInspectionErrorCode.SOURCE_INVALID, "qpdf.source")
        if (
            type(deadline) not in (int, float)
            or not math.isfinite(deadline)
            or deadline <= 0
            or not callable(cancellation_probe)
            or not callable(monotonic)
        ):
            reject_pdf_inspection(PdfInspectionErrorCode.MODEL_INVALID, "qpdf.operation")
        if not self.executable.is_file():
            reject_pdf_inspection(
                PdfInspectionErrorCode.DEPENDENCY_UNAVAILABLE,
                "qpdf.executable",
            )

        operation = _CommandOperation(
            cancellation_probe=cancellation_probe,
            deadline=float(deadline),
            monotonic=monotonic,
        )
        operation.checkpoint()
        arguments = (
            os.fspath(self.executable),
            "--check",
            "--suppress-recovery",
            "--no-warn",
            "--",
            os.fspath(source_path),
        )
        creation_flags = 0
        if os.name == "nt":
            creation_flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP

        process: subprocess.Popen[bytes]
        try:
            process = subprocess.Popen(
                arguments,
                executable=os.fspath(self.executable),
                # AppContainer launches cannot rely on opening the NUL device.
                # A dedicated anonymous pipe is closed immediately instead.
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                cwd=self.executable.parent,
                env=_minimal_environment(os.environ),
                close_fds=True,
                creationflags=creation_flags,
                text=False,
            )
        except OSError:
            reject_pdf_inspection(
                PdfInspectionErrorCode.DEPENDENCY_UNAVAILABLE,
                "qpdf.launch",
            )
        except Exception:
            reject_pdf_inspection(PdfInspectionErrorCode.DEPENDENCY_FAILED, "qpdf.launch")

        stdin_pipe = process.stdin
        stdout_pipe = process.stdout
        stderr_pipe = process.stderr
        if stdin_pipe is None or stdout_pipe is None or stderr_pipe is None:
            _terminate_process_tree(process, float(self.termination_grace_seconds))
            reject_pdf_inspection(PdfInspectionErrorCode.DEPENDENCY_FAILED, "qpdf.pipes")
        try:
            stdin_pipe.close()
        except Exception:
            _terminate_process_tree(process, float(self.termination_grace_seconds))
            reject_pdf_inspection(PdfInspectionErrorCode.DEPENDENCY_FAILED, "qpdf.stdin")
        stdout = cast(BinaryIO, stdout_pipe)
        stderr = cast(BinaryIO, stderr_pipe)

        budget = _DiscardBudget(limits.maximum_tool_output_bytes)
        drainers = (
            _start_drainer(stdout, budget, "qpdf-stdout-discard"),
            _start_drainer(stderr, budget, "qpdf-stderr-discard"),
        )
        try:
            return_code = self._wait_for_result(
                process,
                operation=operation,
                budget=budget,
            )
        except PdfInspectionError:
            terminated = _terminate_process_tree(
                process,
                float(self.termination_grace_seconds),
            )
            drainers_finished = _finish_drainers(
                drainers,
                (stdout, stderr),
                float(self.termination_grace_seconds),
            )
            if not terminated or not drainers_finished:
                reject_pdf_inspection(PdfInspectionErrorCode.DEPENDENCY_FAILED, "qpdf.cleanup")
            raise
        except Exception:
            terminated = _terminate_process_tree(
                process,
                float(self.termination_grace_seconds),
            )
            drainers_finished = _finish_drainers(
                drainers,
                (stdout, stderr),
                float(self.termination_grace_seconds),
            )
            if not terminated or not drainers_finished:
                reject_pdf_inspection(PdfInspectionErrorCode.DEPENDENCY_FAILED, "qpdf.cleanup")
            reject_pdf_inspection(PdfInspectionErrorCode.DEPENDENCY_FAILED, "qpdf.wait")

        drainers_finished = _finish_drainers(
            drainers,
            (stdout, stderr),
            float(self.termination_grace_seconds),
        )
        if not drainers_finished or budget.failed.is_set():
            reject_pdf_inspection(PdfInspectionErrorCode.DEPENDENCY_FAILED, "qpdf.output")
        if budget.exceeded.is_set():
            reject_pdf_inspection(PdfInspectionErrorCode.LIMIT_EXCEEDED, "qpdf.tool_output")
        return _map_return_code(return_code)

    def _wait_for_result(
        self,
        process: subprocess.Popen[bytes],
        *,
        operation: _CommandOperation,
        budget: _DiscardBudget,
    ) -> int:
        while True:
            operation.checkpoint()
            if budget.exceeded.is_set():
                reject_pdf_inspection(PdfInspectionErrorCode.LIMIT_EXCEEDED, "qpdf.tool_output")
            if budget.failed.is_set():
                reject_pdf_inspection(PdfInspectionErrorCode.DEPENDENCY_FAILED, "qpdf.output")
            return_code = process.poll()
            if return_code is not None:
                return return_code
            time.sleep(float(self.poll_seconds))


def _start_drainer(
    stream: BinaryIO,
    budget: _DiscardBudget,
    name: str,
) -> threading.Thread:
    thread = threading.Thread(
        target=_drain_stream,
        args=(stream, budget),
        name=name,
        daemon=True,
    )
    thread.start()
    return thread


def _drain_stream(stream: BinaryIO, budget: _DiscardBudget) -> None:
    try:
        while True:
            block = stream.read(_OUTPUT_CHUNK_BYTES)
            if type(block) is not bytes:
                budget.failed.set()
                return
            if not block:
                return
            budget.consume(len(block))
            if budget.exceeded.is_set():
                return
    except Exception:
        budget.failed.set()


def _finish_drainers(
    drainers: tuple[threading.Thread, threading.Thread],
    streams: tuple[BinaryIO, BinaryIO],
    grace_seconds: float,
) -> bool:
    deadline = time.monotonic() + grace_seconds
    for thread in drainers:
        remaining = max(0.0, deadline - time.monotonic())
        thread.join(remaining)
    if any(thread.is_alive() for thread in drainers):
        for stream in streams:
            try:
                stream.close()
            except Exception:
                pass
        for thread in drainers:
            thread.join(0.05)
    else:
        for stream in streams:
            try:
                stream.close()
            except Exception:
                pass
    return not any(thread.is_alive() for thread in drainers)


def _terminate_process_tree(process: subprocess.Popen[bytes], grace_seconds: float) -> bool:
    """Terminate qpdf; the restricted worker Job Object contains any descendants."""

    try:
        if process.poll() is not None:
            return True
        process.terminate()
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=grace_seconds)
        return process.poll() is not None
    except Exception:
        try:
            process.kill()
            process.wait(timeout=grace_seconds)
        except Exception:
            return False
        return process.poll() is not None


def _minimal_environment(environment: Mapping[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in ("SYSTEMROOT", "WINDIR", "LOCALAPPDATA", "TEMP", "TMP"):
        value = environment.get(name)
        if type(value) is str and value:
            result[name] = value
    return result


def _map_return_code(return_code: int) -> PdfStructuralState:
    if return_code == 0:
        return PdfStructuralState.CLEAN
    if return_code == 3:
        return PdfStructuralState.WARNINGS
    if return_code == 2:
        return PdfStructuralState.FAILED
    reject_pdf_inspection(PdfInspectionErrorCode.DEPENDENCY_FAILED, "qpdf.return_code")

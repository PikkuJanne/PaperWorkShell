"""Authenticated, bounded Tesseract subprocess execution for the OCR worker."""

from __future__ import annotations

import math
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, BinaryIO, Final, Protocol, TypeGuard, cast

from packaging.version import InvalidVersion, Version

_APPROVED_COMMAND: Final = ("<approved-tesseract>",)
_OUTPUT_CHUNK_BYTES: Final = 65_536
_DEFAULT_MAXIMUM_OUTPUT_BYTES: Final = 1024 * 1024
_DEFAULT_POLL_SECONDS: Final = 0.01
_DEFAULT_TERMINATION_GRACE_SECONDS: Final = 1.0
_VERSION_PATTERN: Final = re.compile(rb"(?im)^tesseract\s+v?([^\s]+)")
_LANGUAGE_TOKEN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}\Z")
_QUALITY_LANGUAGE_ARGUMENT: Final = re.compile(
    r"[a-z][a-z0-9_]{1,15}(?:\+[a-z][a-z0-9_]{1,15}){0,15}\Z"
)


class TesseractRunnerErrorCode(StrEnum):
    """Closed, content-free failures exposed by the executable boundary."""

    CONFIGURATION = "configuration"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    OUTPUT_LIMIT = "output_limit"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    CLEANUP_FAILED = "cleanup_failed"


class TesseractRunnerError(RuntimeError):
    """A sanitized failure that never retains argv, paths, or tool output."""

    def __init__(self, code: TesseractRunnerErrorCode, context: str) -> None:
        self.code = code
        self.context = context
        super().__init__(code.value, context)

    def __repr__(self) -> str:
        return f"TesseractRunnerError(code={self.code.value!r}, context={self.context!r})"


def is_tesseract_completed_process(
    value: object,
) -> TypeGuard[subprocess.CompletedProcess[Any]]:
    """Keep exact subprocess-result admission inside the locked runner module."""

    return type(value) is subprocess.CompletedProcess


class TesseractOperationControl(Protocol):
    """Cancellation/deadline seam supplied by the authenticated worker request."""

    def cancellation_requested(self) -> bool: ...

    def deadline_expired(self) -> bool: ...


@dataclass(slots=True)
class _BoundedOutputBudget:
    maximum_bytes: int
    consumed_bytes: int = 0
    exceeded: threading.Event = field(default_factory=threading.Event, repr=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def accept(self, byte_count: int) -> int:
        with self.lock:
            remaining = max(0, self.maximum_bytes - self.consumed_bytes)
            accepted = min(remaining, byte_count)
            self.consumed_bytes += byte_count
            if accepted != byte_count:
                self.exceeded.set()
            return accepted


@dataclass(slots=True)
class _BoundedCapture:
    budget: _BoundedOutputBudget
    retain_output: bool
    payload: bytearray = field(default_factory=bytearray, repr=False)
    failed: threading.Event = field(default_factory=threading.Event, repr=False)

    def drain(self, stream: BinaryIO) -> None:
        try:
            while True:
                block = stream.read(_OUTPUT_CHUNK_BYTES)
                if type(block) is not bytes:
                    self.failed.set()
                    return
                if not block:
                    return
                accepted = self.budget.accept(len(block))
                if self.retain_output and accepted > 0:
                    self.payload.extend(block[:accepted])
                if accepted != len(block):
                    return
        except Exception:
            self.failed.set()


@dataclass(frozen=True, slots=True, repr=False)
class TesseractCommandRunner:
    """Invoke one configured Tesseract executable without ambient discovery."""

    executable: Path
    tessdata_directory: Path
    temporary_directory: Path
    control: TesseractOperationControl = field(repr=False)
    maximum_output_bytes: int = _DEFAULT_MAXIMUM_OUTPUT_BYTES
    poll_seconds: float = _DEFAULT_POLL_SECONDS
    termination_grace_seconds: float = _DEFAULT_TERMINATION_GRACE_SECONDS
    environment: Mapping[str, str] = field(default_factory=lambda: os.environ, repr=False)
    monotonic: Callable[[], float] = field(default=time.monotonic, repr=False)

    def __post_init__(self) -> None:
        if (
            any(
                not isinstance(path, Path) or not path.is_absolute()
                for path in (
                    self.executable,
                    self.tessdata_directory,
                    self.temporary_directory,
                )
            )
            or not callable(getattr(self.control, "cancellation_requested", None))
            or not callable(getattr(self.control, "deadline_expired", None))
            or type(self.maximum_output_bytes) is not int
            or not 1 <= self.maximum_output_bytes <= (1 << 31)
            or not _positive_finite(self.poll_seconds)
            or not _positive_finite(self.termination_grace_seconds)
            or not callable(self.monotonic)
        ):
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.CONFIGURATION,
                "tesseract.configuration",
            )

    def __repr__(self) -> str:
        return "TesseractCommandRunner(<approved executable and resources>)"

    def version(self) -> str:
        """Probe the configured executable through the same bounded runner."""

        completed = self.run(("tesseract", "--version"), check=True)
        output = _require_bytes(completed.stdout, "tesseract.version_output")
        match = _VERSION_PATTERN.search(output)
        if match is None:
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.FAILED,
                "tesseract.version",
            )
        try:
            version = Version(match.group(1).decode("ascii"))
        except InvalidVersion, UnicodeDecodeError:
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.FAILED,
                "tesseract.version",
            ) from None
        return str(version)

    def languages(self) -> frozenset[str]:
        """Return only validated language identifiers, never raw tool output."""

        completed = self.run(("tesseract", "--list-langs"), check=True)
        output = _require_bytes(completed.stdout, "tesseract.languages_output")
        try:
            lines = output.decode("utf-8", errors="strict").splitlines()
        except UnicodeDecodeError:
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.FAILED,
                "tesseract.languages",
            ) from None
        result = frozenset(
            line.strip()
            for line in lines[1:]
            if _LANGUAGE_TOKEN.fullmatch(line.strip()) is not None
        )
        if not result:
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.FAILED,
                "tesseract.languages",
            )
        return result

    def run(
        self,
        args: Sequence[str | os.PathLike[str]],
        *,
        env: Mapping[str, str] | None = None,
        logs_errors_to_stdout: bool = False,
        check: bool = False,
        timeout: float | None = None,
        text: bool = False,
        encoding: str | None = None,
        errors: str | None = None,
        stdout: Any = None,
        stderr: Any = None,
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[Any]:
        """OCRmyPDF-compatible ``run`` hook with fixed process semantics.

        OCRmyPDF controls the non-shell argument array. PaperWorkShell replaces
        only argv[0], ignores ambient search paths, separately drains stdout and
        stderr under one shared bound, and deliberately does not expose the real
        argv on returned exceptions.
        """

        del logs_errors_to_stdout
        self._validate_compatibility_options(
            env=env,
            stdout=stdout,
            stderr=stderr,
            encoding=encoding,
            errors=errors,
            kwargs=kwargs,
        )
        arguments = self._validated_arguments(args)
        local_timeout = _optional_timeout(timeout)
        self._checkpoint()
        if not self.executable.is_file() or not self.tessdata_directory.is_dir():
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.UNAVAILABLE,
                "tesseract.resources",
            )
        creation_flags = 0
        if os.name == "nt":
            creation_flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            process = subprocess.Popen(
                arguments,
                executable=os.fspath(self.executable),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                cwd=self.executable.parent,
                env=self._minimal_environment(env),
                close_fds=True,
                creationflags=creation_flags,
                text=False,
            )
        except OSError:
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.FAILED,
                "tesseract.launch",
            ) from None
        except Exception:
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.FAILED,
                "tesseract.launch",
            ) from None

        streams: list[BinaryIO] = []
        started_drainers: list[tuple[threading.Thread, BinaryIO]] = []
        try:
            stdin_pipe = cast(BinaryIO | None, process.stdin)
            output_pipe = cast(BinaryIO | None, process.stdout)
            error_pipe = cast(BinaryIO | None, process.stderr)
            streams.extend(
                stream for stream in (stdin_pipe, output_pipe, error_pipe) if stream is not None
            )
            if stdin_pipe is None or output_pipe is None or error_pipe is None:
                raise TesseractRunnerError(
                    TesseractRunnerErrorCode.FAILED,
                    "tesseract.pipes",
                )
            try:
                stdin_pipe.close()
            except Exception:
                raise TesseractRunnerError(
                    TesseractRunnerErrorCode.FAILED,
                    "tesseract.stdin",
                ) from None

            budget = _BoundedOutputBudget(self.maximum_output_bytes)
            capture = _BoundedCapture(
                budget,
                retain_output=_should_retain_bounded_output(
                    arguments,
                    self.temporary_directory,
                ),
            )
            error_capture = _BoundedCapture(budget, retain_output=False)
            drainer = threading.Thread(
                target=capture.drain,
                args=(output_pipe,),
                name="pws-tesseract-output",
                daemon=True,
            )
            error_drainer = threading.Thread(
                target=error_capture.drain,
                args=(error_pipe,),
                name="pws-tesseract-error-output",
                daemon=True,
            )
            _start_tracked_drainer(drainer, output_pipe, started_drainers)
            _start_tracked_drainer(error_drainer, error_pipe, started_drainers)
            started = self._read_clock()
            return_code = self._wait(
                process,
                (capture, error_capture),
                budget,
                started,
                local_timeout,
            )
        except BaseException as error:
            terminated = _terminate_process(
                process,
                float(self.termination_grace_seconds),
            )
            drainers_finished = _finish_drainers(
                tuple(started_drainers),
                tuple(streams),
                float(self.termination_grace_seconds),
            )
            if not terminated or not drainers_finished:
                raise TesseractRunnerError(
                    TesseractRunnerErrorCode.CLEANUP_FAILED,
                    "tesseract.cleanup",
                ) from None
            if isinstance(error, TesseractRunnerError | subprocess.TimeoutExpired):
                raise error from None
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.FAILED,
                "tesseract.startup",
            ) from None
        if not _finish_drainers(
            tuple(started_drainers),
            tuple(streams),
            float(self.termination_grace_seconds),
        ):
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.CLEANUP_FAILED,
                "tesseract.cleanup",
            )
        if capture.failed.is_set() or error_capture.failed.is_set():
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.FAILED,
                "tesseract.output",
            )
        if budget.exceeded.is_set():
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.OUTPUT_LIMIT,
                "tesseract.output_limit",
            )
        raw_output = bytes(capture.payload)
        completed_output: bytes | str
        if text:
            try:
                completed_output = raw_output.decode(encoding or "utf-8", errors or "strict")
            except UnicodeDecodeError:
                raise TesseractRunnerError(
                    TesseractRunnerErrorCode.FAILED,
                    "tesseract.output_encoding",
                ) from None
        else:
            completed_output = raw_output
        if check and return_code != 0:
            raise subprocess.CalledProcessError(
                return_code,
                _APPROVED_COMMAND,
                output=completed_output,
                stderr=None,
            )
        return subprocess.CompletedProcess(
            _APPROVED_COMMAND,
            return_code,
            stdout=completed_output,
            stderr=None,
        )

    def _validate_compatibility_options(
        self,
        *,
        env: Mapping[str, str] | None,
        stdout: Any,
        stderr: Any,
        encoding: str | None,
        errors: str | None,
        kwargs: Mapping[str, Any],
    ) -> None:
        if (
            kwargs
            or stdout not in (None, subprocess.PIPE)
            or stderr
            not in (
                None,
                subprocess.PIPE,
                subprocess.STDOUT,
            )
        ):
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.CONFIGURATION,
                "tesseract.run_options",
            )
        if encoding is not None and type(encoding) is not str:
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.CONFIGURATION,
                "tesseract.encoding",
            )
        if errors is not None and type(errors) is not str:
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.CONFIGURATION,
                "tesseract.encoding",
            )
        if env is not None:
            omp = env.get("OMP_THREAD_LIMIT")
            if omp is not None and omp != "1":
                raise TesseractRunnerError(
                    TesseractRunnerErrorCode.CONFIGURATION,
                    "tesseract.thread_limit",
                )

    def _validated_arguments(
        self,
        args: Sequence[str | os.PathLike[str]],
    ) -> tuple[str, ...]:
        if isinstance(args, str | bytes) or not isinstance(args, Sequence) or not args:
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.CONFIGURATION,
                "tesseract.arguments",
            )
        values: list[str] = []
        for argument in args:
            try:
                value = os.fspath(argument)
            except TypeError:
                raise TesseractRunnerError(
                    TesseractRunnerErrorCode.CONFIGURATION,
                    "tesseract.arguments",
                ) from None
            if (
                type(value) is not str
                or not value
                or "\x00" in value
                or "\r" in value
                or "\n" in value
            ):
                raise TesseractRunnerError(
                    TesseractRunnerErrorCode.CONFIGURATION,
                    "tesseract.arguments",
                )
            values.append(value)
        first = values[0]
        if first != "tesseract" and not _same_path(first, self.executable):
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.CONFIGURATION,
                "tesseract.executable_argument",
            )
        values[0] = os.fspath(self.executable)
        return tuple(values)

    def _minimal_environment(self, supplied: Mapping[str, str] | None) -> dict[str, str]:
        result: dict[str, str] = {}
        for name in ("SYSTEMROOT", "WINDIR"):
            value = self.environment.get(name)
            if type(value) is str and value:
                result[name] = value
        result.update(
            {
                "TEMP": os.fspath(self.temporary_directory),
                "TMP": os.fspath(self.temporary_directory),
                "TESSDATA_PREFIX": os.fspath(self.tessdata_directory),
                "OMP_THREAD_LIMIT": "1",
            }
        )
        if supplied is not None and supplied.get("OMP_THREAD_LIMIT") not in (None, "1"):
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.CONFIGURATION,
                "tesseract.thread_limit",
            )
        return result

    def _wait(
        self,
        process: subprocess.Popen[bytes],
        captures: tuple[_BoundedCapture, ...],
        budget: _BoundedOutputBudget,
        started: float,
        local_timeout: float | None,
    ) -> int:
        while True:
            self._checkpoint()
            if any(capture.failed.is_set() for capture in captures):
                raise TesseractRunnerError(
                    TesseractRunnerErrorCode.FAILED,
                    "tesseract.output",
                )
            if budget.exceeded.is_set():
                raise TesseractRunnerError(
                    TesseractRunnerErrorCode.OUTPUT_LIMIT,
                    "tesseract.output_limit",
                )
            if local_timeout is not None and self._read_clock() - started >= local_timeout:
                raise subprocess.TimeoutExpired(_APPROVED_COMMAND, local_timeout)
            return_code = process.poll()
            if return_code is not None:
                if type(return_code) is not int:
                    raise TesseractRunnerError(
                        TesseractRunnerErrorCode.FAILED,
                        "tesseract.return_code",
                    )
                return return_code
            time.sleep(float(self.poll_seconds))

    def _checkpoint(self) -> None:
        try:
            cancelled = self.control.cancellation_requested()
            expired = self.control.deadline_expired()
        except Exception:
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.FAILED,
                "tesseract.control",
            ) from None
        if type(cancelled) is not bool or type(expired) is not bool:
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.FAILED,
                "tesseract.control",
            )
        if cancelled:
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.CANCELLED,
                "tesseract.operation",
            )
        if expired:
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.TIMED_OUT,
                "tesseract.operation",
            )

    def _read_clock(self) -> float:
        try:
            value = self.monotonic()
        except Exception:
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.FAILED,
                "tesseract.clock",
            ) from None
        if type(value) not in (int, float) or not math.isfinite(value):
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.FAILED,
                "tesseract.clock",
            )
        return float(value)

    def _require_terminated(self, process: subprocess.Popen[bytes]) -> None:
        if not _terminate_process(process, float(self.termination_grace_seconds)):
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.CLEANUP_FAILED,
                "tesseract.termination",
            )


def _terminate_process(process: subprocess.Popen[bytes], grace_seconds: float) -> bool:
    """Stop the direct child; the worker Job Object owns the complete descendant tree."""

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


def _finish_drainers(
    drainers: tuple[tuple[threading.Thread, BinaryIO], ...],
    streams: tuple[BinaryIO, ...],
    grace_seconds: float,
) -> bool:
    cleanup_confirmed = True
    try:
        deadline = time.monotonic() + grace_seconds
    except Exception:
        deadline = 0.0
        cleanup_confirmed = False
    for drainer, _stream in drainers:
        try:
            drainer.join(max(0.0, deadline - time.monotonic()))
        except Exception:
            cleanup_confirmed = False
    for stream in streams:
        try:
            stream.close()
        except Exception:
            cleanup_confirmed = False
    for drainer, _stream in drainers:
        try:
            if drainer.is_alive():
                drainer.join(0.05)
            if drainer.is_alive():
                cleanup_confirmed = False
        except Exception:
            cleanup_confirmed = False
    return cleanup_confirmed


def _start_tracked_drainer(
    drainer: threading.Thread,
    stream: BinaryIO,
    started_drainers: list[tuple[threading.Thread, BinaryIO]],
) -> None:
    try:
        drainer.start()
    except BaseException:
        # A signal or injected startup fault can arrive after the native
        # thread exists but before Thread.start() returns. Retain that owner so
        # the post-Popen cleanup guard still joins it.
        if drainer.ident is not None:
            started_drainers.append((drainer, stream))
        raise
    started_drainers.append((drainer, stream))


def _positive_finite(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    numeric = cast(float, value)
    return math.isfinite(numeric) and numeric > 0


def _optional_timeout(value: object) -> float | None:
    if value is None:
        return None
    if not _positive_finite(value):
        raise TesseractRunnerError(
            TesseractRunnerErrorCode.CONFIGURATION,
            "tesseract.timeout",
        )
    return float(cast(float, value))


def _same_path(value: str, expected: Path) -> bool:
    try:
        supplied = Path(value)
        return supplied.is_absolute() and os.path.normcase(os.fspath(supplied)) == os.path.normcase(
            os.fspath(expected)
        )
    except OSError, ValueError:
        return False


def _should_retain_bounded_output(
    arguments: tuple[str, ...],
    temporary_directory: Path,
) -> bool:
    if len(arguments) == 2 and arguments[1] in {"--version", "--list-langs"}:
        return True
    # The page-quality worker is the sole non-probe consumer. Tesseract writes
    # its TSV to the bounded stdout pipe while separately bounded stderr is
    # discarded without contaminating the strict TSV parser.
    return (
        len(arguments) == 9
        and arguments[2] == "stdout"
        and arguments[3] == "-l"
        and _QUALITY_LANGUAGE_ARGUMENT.fullmatch(arguments[4]) is not None
        and arguments[5:] == ("--psm", "6", "-c", "tessedit_create_tsv=1")
        and _is_direct_private_ppm(arguments[1], temporary_directory)
    )


def _is_direct_private_ppm(value: str, temporary_directory: Path) -> bool:
    try:
        candidate = Path(value)
        return (
            candidate.is_absolute()
            and candidate.suffix.casefold() == ".ppm"
            and _same_path(os.fspath(candidate.parent), temporary_directory)
        )
    except OSError, ValueError:
        return False


def _require_bytes(value: object, context: str) -> bytes:
    if type(value) is not bytes:
        raise TesseractRunnerError(TesseractRunnerErrorCode.FAILED, context)
    return value

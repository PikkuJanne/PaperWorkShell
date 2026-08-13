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
from typing import Any, BinaryIO, Final, Protocol, cast

from packaging.version import InvalidVersion, Version

_APPROVED_COMMAND: Final = ("<approved-tesseract>",)
_OUTPUT_CHUNK_BYTES: Final = 65_536
_DEFAULT_MAXIMUM_OUTPUT_BYTES: Final = 1024 * 1024
_DEFAULT_POLL_SECONDS: Final = 0.01
_DEFAULT_TERMINATION_GRACE_SECONDS: Final = 1.0
_VERSION_PATTERN: Final = re.compile(rb"(?im)^tesseract\s+v?([^\s]+)")
_LANGUAGE_TOKEN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}\Z")


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


class TesseractOperationControl(Protocol):
    """Cancellation/deadline seam supplied by the authenticated worker request."""

    def cancellation_requested(self) -> bool: ...

    def deadline_expired(self) -> bool: ...


@dataclass(slots=True)
class _BoundedCapture:
    maximum_bytes: int
    retain_output: bool
    payload: bytearray = field(default_factory=bytearray, repr=False)
    consumed_bytes: int = 0
    exceeded: threading.Event = field(default_factory=threading.Event, repr=False)
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
                remaining = self.maximum_bytes - self.consumed_bytes
                if remaining < len(block):
                    if self.retain_output and remaining > 0:
                        self.payload.extend(block[:remaining])
                    self.consumed_bytes += len(block)
                    self.exceeded.set()
                    return
                self.consumed_bytes += len(block)
                if self.retain_output:
                    self.payload.extend(block)
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
        only argv[0], ignores ambient search paths, merges and bounds tool output,
        and deliberately does not expose the real argv on returned exceptions.
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
                stderr=subprocess.STDOUT,
                shell=False,
                cwd=self.executable.parent,
                env=self._minimal_environment(env),
                close_fds=True,
                creationflags=creation_flags,
                text=False,
            )
        except OSError:
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.UNAVAILABLE,
                "tesseract.launch",
            ) from None
        except Exception:
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.FAILED,
                "tesseract.launch",
            ) from None

        stdin_pipe = process.stdin
        output_pipe = process.stdout
        if stdin_pipe is None or output_pipe is None:
            self._require_terminated(process)
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.FAILED,
                "tesseract.pipes",
            )
        try:
            stdin_pipe.close()
        except Exception:
            self._require_terminated(process)
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.FAILED,
                "tesseract.stdin",
            ) from None

        capture = _BoundedCapture(
            self.maximum_output_bytes,
            retain_output=_is_capability_probe(arguments),
        )
        output = cast(BinaryIO, output_pipe)
        drainer = threading.Thread(
            target=capture.drain,
            args=(output,),
            name="pws-tesseract-output",
            daemon=True,
        )
        drainer.start()
        started = self._read_clock()
        try:
            return_code = self._wait(process, capture, started, local_timeout)
        except BaseException:
            self._require_terminated(process)
            if not _finish_drainer(
                drainer,
                output,
                float(self.termination_grace_seconds),
            ):
                raise TesseractRunnerError(
                    TesseractRunnerErrorCode.CLEANUP_FAILED,
                    "tesseract.cleanup",
                ) from None
            raise
        if not _finish_drainer(drainer, output, float(self.termination_grace_seconds)):
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.CLEANUP_FAILED,
                "tesseract.cleanup",
            )
        if capture.failed.is_set():
            raise TesseractRunnerError(
                TesseractRunnerErrorCode.FAILED,
                "tesseract.output",
            )
        if capture.exceeded.is_set():
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
        capture: _BoundedCapture,
        started: float,
        local_timeout: float | None,
    ) -> int:
        while True:
            self._checkpoint()
            if capture.failed.is_set():
                raise TesseractRunnerError(
                    TesseractRunnerErrorCode.FAILED,
                    "tesseract.output",
                )
            if capture.exceeded.is_set():
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


def _finish_drainer(
    drainer: threading.Thread,
    stream: BinaryIO,
    grace_seconds: float,
) -> bool:
    drainer.join(grace_seconds)
    try:
        stream.close()
    except Exception:
        pass
    if drainer.is_alive():
        drainer.join(0.05)
    return not drainer.is_alive()


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


def _is_capability_probe(arguments: tuple[str, ...]) -> bool:
    return len(arguments) == 2 and arguments[1] in {"--version", "--list-langs"}


def _require_bytes(value: object, context: str) -> bytes:
    if type(value) is not bytes:
        raise TesseractRunnerError(TesseractRunnerErrorCode.FAILED, context)
    return value

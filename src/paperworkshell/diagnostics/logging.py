"""Bounded structured logging that accepts no free-form messages or exceptions."""

from __future__ import annotations

import io
from collections import deque
from threading import RLock
from typing import BinaryIO, Final

from paperworkshell.diagnostics.codec import encode_diagnostic_record
from paperworkshell.diagnostics.errors import (
    DiagnosticErrorCode,
    DiagnosticErrorContext,
    reject_diagnostic,
)
from paperworkshell.diagnostics.models import DiagnosticRecord, copy_diagnostic_record

DEFAULT_MAXIMUM_DIAGNOSTIC_RECORDS: Final = 256
MAXIMUM_DIAGNOSTIC_RECORDS: Final = 4_096


class SanitizedDiagnosticLogger:
    """Keep a bounded safe record history and optionally mirror canonical JSON lines.

    The optional sink must already be open and authenticated by the composition root.
    This boundary never accepts a filesystem path, a message, logging extras, or an
    exception object, and it never owns or closes the sink.
    """

    __slots__ = (
        "_dropped_records",
        "_encoded_lines",
        "_flush_on_emit",
        "_lock",
        "_maximum_records",
        "_records",
        "_sink",
        "_sink_failures",
    )

    def __init__(
        self,
        *,
        maximum_records: int = DEFAULT_MAXIMUM_DIAGNOSTIC_RECORDS,
        sink: BinaryIO | None = None,
        flush_on_emit: bool = True,
    ) -> None:
        if (
            type(maximum_records) is not int
            or not 1 <= maximum_records <= MAXIMUM_DIAGNOSTIC_RECORDS
            or type(flush_on_emit) is not bool
        ):
            reject_diagnostic(
                DiagnosticErrorCode.CONFIGURATION_INVALID,
                DiagnosticErrorContext.CONFIGURATION,
            )
        if sink is not None and not isinstance(sink, (io.BufferedIOBase, io.RawIOBase)):
            reject_diagnostic(
                DiagnosticErrorCode.CONFIGURATION_INVALID,
                DiagnosticErrorContext.CONFIGURATION,
            )
        self._maximum_records = maximum_records
        self._sink = sink
        self._flush_on_emit = flush_on_emit
        self._records: deque[DiagnosticRecord] = deque(maxlen=maximum_records)
        self._encoded_lines: deque[bytes] = deque(maxlen=maximum_records)
        self._dropped_records = 0
        self._sink_failures = 0
        self._lock = RLock()

    @property
    def maximum_records(self) -> int:
        """Return the fixed in-memory record capacity."""

        return self._maximum_records

    @property
    def dropped_records(self) -> int:
        """Return the numeric count of records displaced from the bounded history."""

        with self._lock:
            return self._dropped_records

    @property
    def sink_failures(self) -> int:
        """Return the numeric count of failed mirror attempts."""

        with self._lock:
            return self._sink_failures

    def emit(self, value: DiagnosticRecord) -> None:
        """Record one validated event and mirror only its canonical safe encoding."""

        record = copy_diagnostic_record(value)
        line = encode_diagnostic_record(record) + b"\n"
        sink_failed = False
        with self._lock:
            if len(self._records) == self._maximum_records:
                self._dropped_records += 1
            self._records.append(record)
            self._encoded_lines.append(line)
            if self._sink is not None:
                try:
                    written = self._sink.write(line)
                    if type(written) is not int or written != len(line):
                        sink_failed = True
                    elif self._flush_on_emit:
                        self._sink.flush()
                except Exception:
                    sink_failed = True
                if sink_failed:
                    self._sink_failures += 1
        if sink_failed:
            reject_diagnostic(
                DiagnosticErrorCode.SINK_FAILED,
                DiagnosticErrorContext.SINK,
            )

    def snapshot_records(self) -> tuple[DiagnosticRecord, ...]:
        """Return independent validated copies of the current bounded history."""

        with self._lock:
            return tuple(copy_diagnostic_record(record) for record in self._records)

    def snapshot_lines(self) -> tuple[bytes, ...]:
        """Return immutable copies of the current canonical JSON lines."""

        with self._lock:
            return tuple(bytes(line) for line in self._encoded_lines)

    def __repr__(self) -> str:
        with self._lock:
            record_count = len(self._records)
            dropped_records = self._dropped_records
            sink_failures = self._sink_failures
        return (
            "SanitizedDiagnosticLogger("
            f"record_count={record_count}, dropped_records={dropped_records}, "
            f"sink_failures={sink_failures})"
        )

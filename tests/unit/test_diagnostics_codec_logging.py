"""Unit tests for canonical encoding and the bounded diagnostic logger."""

from __future__ import annotations

import json
from collections.abc import Buffer
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from io import BytesIO
from typing import BinaryIO, Never, cast
from uuid import UUID

import pytest

from paperworkshell.diagnostics.codec import encode_diagnostic_record
from paperworkshell.diagnostics.errors import DiagnosticError, DiagnosticErrorCode
from paperworkshell.diagnostics.logging import SanitizedDiagnosticLogger
from paperworkshell.diagnostics.models import (
    DiagnosticCount,
    DiagnosticCountKind,
    DiagnosticEventCode,
    DiagnosticOperationKind,
    DiagnosticOutcome,
    DiagnosticRecord,
    DiagnosticResource,
    DiagnosticResourceKind,
    DiagnosticVersion,
    DiagnosticVersionKind,
)

_NOW = datetime(2026, 8, 9, 12, 34, 56, 789, tzinfo=UTC)
_CASE_ID = UUID("00000000-0000-4000-8000-000000000001")
_OPERATION_ID = UUID("00000000-0000-4000-8000-000000000002")


def _completed_record(*, reverse_entries: bool = False) -> DiagnosticRecord:
    versions: tuple[DiagnosticVersion, ...] = (
        DiagnosticVersion(DiagnosticVersionKind.APPLICATION, (0, 0, 0)),
        DiagnosticVersion(DiagnosticVersionKind.QPDF, (12, 2, 0)),
    )
    counts: tuple[DiagnosticCount, ...] = (
        DiagnosticCount(DiagnosticCountKind.PAGES_PROCESSED, 3),
        DiagnosticCount(DiagnosticCountKind.OPERATIONS_COMPLETED, 1),
    )
    resources: tuple[DiagnosticResource, ...] = (
        DiagnosticResource(DiagnosticResourceKind.PEAK_MEMORY_BYTES, 4_096),
        DiagnosticResource(DiagnosticResourceKind.DURATION_MILLISECONDS, 25),
    )
    if reverse_entries:
        versions = tuple(reversed(versions))
        counts = tuple(reversed(counts))
        resources = tuple(reversed(resources))
    return DiagnosticRecord(
        occurred_at=_NOW,
        event=DiagnosticEventCode.OPERATION_COMPLETED,
        case_id=_CASE_ID,
        operation_id=_OPERATION_ID,
        operation_kind=DiagnosticOperationKind.PDF_EXPORT,
        outcome=DiagnosticOutcome.SUCCESS,
        versions=versions,
        counts=counts,
        resources=resources,
    )


def test_codec_emits_exact_canonical_json_with_only_fixed_keys() -> None:
    encoded = encode_diagnostic_record(_completed_record())
    assert encoded == encode_diagnostic_record(_completed_record(reverse_entries=True))
    assert not encoded.endswith(b"\n")
    document = json.loads(encoded)
    assert set(document) == {
        "case_id",
        "counts",
        "event",
        "failure_code",
        "occurred_at",
        "operation_id",
        "operation_kind",
        "outcome",
        "record_version",
        "resources",
        "versions",
    }
    assert document["occurred_at"] == "2026-08-09T12:34:56.000789Z"
    assert document["operation_kind"] == "pdf.export"
    assert document["versions"] == [
        {"components": [0, 0, 0], "kind": "application"},
        {"components": [12, 2, 0], "kind": "qpdf"},
    ]


def test_codec_revalidates_a_mutated_record() -> None:
    record = _completed_record()
    object.__setattr__(record, "event", "PRIVATE-DOCUMENT-TEXT")
    with pytest.raises(DiagnosticError) as raised:
        encode_diagnostic_record(record)
    assert raised.value.code is DiagnosticErrorCode.RECORD_INVALID


def test_logger_mirrors_json_lines_and_keeps_a_bounded_snapshot() -> None:
    sink = BytesIO()
    logger = SanitizedDiagnosticLogger(maximum_records=2, sink=sink)
    records = tuple(
        DiagnosticRecord(
            occurred_at=datetime(2026, 8, 9, 12, 34, second, tzinfo=UTC),
            event=DiagnosticEventCode.RECOVERY_SCAN_STARTED,
        )
        for second in (1, 2, 3)
    )
    for record in records:
        logger.emit(record)

    assert logger.dropped_records == 1
    assert logger.sink_failures == 0
    assert tuple(item.occurred_at.second for item in logger.snapshot_records()) == (2, 3)
    assert sink.getvalue().count(b"\n") == 3
    assert all(line.endswith(b"\n") for line in logger.snapshot_lines())
    assert "BytesIO" not in repr(logger)


def test_logger_copies_a_record_before_retaining_or_writing_it() -> None:
    record = _completed_record()
    logger = SanitizedDiagnosticLogger()
    logger.emit(record)
    original_line = logger.snapshot_lines()[0]

    object.__setattr__(record, "event", "PRIVATE-METADATA-CANARY")
    object.__setattr__(record.versions[0], "components", (999,))

    assert logger.snapshot_lines()[0] == original_line
    snapshot = logger.snapshot_records()[0]
    assert snapshot.event is DiagnosticEventCode.OPERATION_COMPLETED
    assert snapshot.versions[0].components == (0, 0, 0)


class _FailingFlush(BytesIO):
    def flush(self) -> Never:
        raise OSError("PRIVATE-SINK-PATH:C:\\private\\applicant.pdf")


class _ShortWriter(BytesIO):
    def write(self, data: Buffer, /) -> int:
        view = memoryview(data)
        return super().write(view[: max(0, len(view) - 1)])


@pytest.mark.parametrize("sink", (_FailingFlush(), _ShortWriter()))
def test_sink_failures_are_sanitized_and_the_safe_record_remains_buffered(
    sink: BinaryIO,
) -> None:
    logger = SanitizedDiagnosticLogger(sink=sink)
    with pytest.raises(DiagnosticError) as raised:
        logger.emit(_completed_record())

    assert raised.value.code is DiagnosticErrorCode.SINK_FAILED
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "PRIVATE-SINK" not in f"{raised.value!s}\n{raised.value!r}\n{raised.value.args!r}"
    assert logger.sink_failures == 1
    assert len(logger.snapshot_records()) == 1


@pytest.mark.parametrize("maximum_records", (0, -1, 4_097, True, 1.5))
def test_logger_rejects_invalid_capacity_without_rendering_it(maximum_records: object) -> None:
    with pytest.raises(DiagnosticError) as raised:
        SanitizedDiagnosticLogger(maximum_records=cast(int, maximum_records))
    assert raised.value.code is DiagnosticErrorCode.CONFIGURATION_INVALID


def test_logger_rejects_non_binary_sink_and_non_boolean_flush_option() -> None:
    with pytest.raises(DiagnosticError):
        SanitizedDiagnosticLogger(sink=cast(BinaryIO, "C:\\private\\applicant.log"))
    with pytest.raises(DiagnosticError):
        SanitizedDiagnosticLogger(flush_on_emit=cast(bool, 1))


def test_logger_serializes_concurrent_emitters_without_losing_records() -> None:
    logger = SanitizedDiagnosticLogger(maximum_records=64)

    def emit(index: int) -> None:
        logger.emit(
            DiagnosticRecord(
                occurred_at=datetime(2026, 8, 9, 12, 0, index, tzinfo=UTC),
                event=DiagnosticEventCode.RECOVERY_SCAN_STARTED,
            )
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        tuple(executor.map(emit, range(60)))

    assert len(logger.snapshot_records()) == 60
    assert len(logger.snapshot_lines()) == 60
    assert logger.dropped_records == 0

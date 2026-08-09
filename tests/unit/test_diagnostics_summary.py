"""Unit tests for the immutable sanitized diagnostic summary."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.diagnostics.errors import DiagnosticError, DiagnosticErrorCode
from paperworkshell.diagnostics.logging import SanitizedDiagnosticLogger
from paperworkshell.diagnostics.models import (
    DiagnosticCount,
    DiagnosticCountKind,
    DiagnosticEventCode,
    DiagnosticFailureCode,
    DiagnosticOperationKind,
    DiagnosticOutcome,
    DiagnosticRecord,
    DiagnosticResource,
    DiagnosticResourceKind,
    DiagnosticVersion,
    DiagnosticVersionKind,
)
from paperworkshell.diagnostics.summary import (
    DIAGNOSTIC_SUMMARY_VERSION,
    DiagnosticSummary,
    build_diagnostic_summary,
    copy_diagnostic_summary,
    render_diagnostic_summary,
)

_NOW = datetime(2026, 8, 9, 12, 34, 56, 789, tzinfo=UTC)
_OPERATION_A = UUID("00000000-0000-4000-8000-000000000001")
_OPERATION_B = UUID("00000000-0000-4000-8000-000000000002")
_CASE_ID = UUID("00000000-0000-4000-8000-000000000003")


def _summary() -> DiagnosticSummary:
    return DiagnosticSummary(
        generated_at=_NOW,
        record_count=7,
        dropped_record_count=2,
        sink_failure_count=1,
        versions=(
            DiagnosticVersion(DiagnosticVersionKind.QPDF, (12, 2, 0)),
            DiagnosticVersion(DiagnosticVersionKind.APPLICATION, (0, 0, 0)),
        ),
        operation_ids=(_OPERATION_B, _OPERATION_A),
        counts=(
            DiagnosticCount(DiagnosticCountKind.CASES_RECOVERED, 2),
            DiagnosticCount(DiagnosticCountKind.CASES_SCANNED, 3),
        ),
        codes=(
            DiagnosticFailureCode.STORAGE_FAILED,
            DiagnosticFailureCode.CASE_INVALID,
        ),
        resources=(
            DiagnosticResource(DiagnosticResourceKind.PEAK_MEMORY_BYTES, 4_096),
            DiagnosticResource(DiagnosticResourceKind.DURATION_MILLISECONDS, 25),
        ),
    )


def test_summary_copies_and_canonically_orders_every_safe_category() -> None:
    summary = _summary()
    assert summary.summary_version == DIAGNOSTIC_SUMMARY_VERSION
    assert tuple(item.kind for item in summary.versions) == (
        DiagnosticVersionKind.APPLICATION,
        DiagnosticVersionKind.QPDF,
    )
    assert summary.operation_ids == (_OPERATION_A, _OPERATION_B)
    assert tuple(item.kind for item in summary.counts) == (
        DiagnosticCountKind.CASES_RECOVERED,
        DiagnosticCountKind.CASES_SCANNED,
    )
    assert summary.codes == (
        DiagnosticFailureCode.CASE_INVALID,
        DiagnosticFailureCode.STORAGE_FAILED,
    )


def test_human_summary_contains_only_exact_documented_fields() -> None:
    rendered = render_diagnostic_summary(_summary())
    assert rendered == (
        "PaperWorkShell diagnostic summary\n"
        "Summary version: 1\n"
        "Generated UTC: 2026-08-09T12:34:56.000789Z\n"
        "Record count: 7\n"
        "Dropped record count: 2\n"
        "Sink failure count: 1\n"
        "Versions:\n"
        "- Application: 0.0.0\n"
        "- qpdf: 12.2.0\n"
        "Operation IDs:\n"
        "- 00000000-0000-4000-8000-000000000001\n"
        "- 00000000-0000-4000-8000-000000000002\n"
        "Counts:\n"
        "- Cases recovered: 2\n"
        "- Cases scanned: 3\n"
        "Codes:\n"
        "- CASE_INVALID\n"
        "- STORAGE_FAILED\n"
        "Resources:\n"
        "- Duration milliseconds: 25\n"
        "- Peak memory bytes: 4096\n"
    )
    assert "DiagnosticSummary(record_count=7" in repr(_summary())
    assert "00000000" not in repr(_summary())


def test_empty_safe_categories_have_a_deterministic_human_inventory() -> None:
    rendered = render_diagnostic_summary(DiagnosticSummary(generated_at=_NOW))
    assert rendered.count("- None") == 5


@pytest.mark.parametrize(
    "changes",
    (
        {"generated_at": datetime(2026, 8, 9)},
        {"record_count": True},
        {"summary_version": 2},
        {"operation_ids": (_OPERATION_A, _OPERATION_A)},
        {
            "versions": (
                DiagnosticVersion(DiagnosticVersionKind.APPLICATION, (1,)),
                DiagnosticVersion(DiagnosticVersionKind.APPLICATION, (2,)),
            )
        },
        {
            "codes": (
                DiagnosticFailureCode.CASE_INVALID,
                DiagnosticFailureCode.CASE_INVALID,
            )
        },
        {"counts": []},
    ),
)
def test_summary_rejects_invalid_duplicate_or_non_tuple_fields(
    changes: dict[str, object],
) -> None:
    arguments: dict[str, object] = {"generated_at": _NOW}
    arguments.update(changes)
    with pytest.raises(DiagnosticError) as raised:
        DiagnosticSummary(**arguments)  # type: ignore[arg-type]
    assert raised.value.code in (
        DiagnosticErrorCode.SUMMARY_INVALID,
        DiagnosticErrorCode.RECORD_INVALID,
    )


def test_summary_owns_nested_values_and_copy_is_independent() -> None:
    supplied = DiagnosticVersion(DiagnosticVersionKind.APPLICATION, (1, 2, 3))
    summary = DiagnosticSummary(generated_at=_NOW, versions=(supplied,))
    copied = copy_diagnostic_summary(summary)

    object.__setattr__(supplied, "components", (9, 9, 9))
    object.__setattr__(summary.versions[0], "components", (8, 8, 8))

    assert copied.versions[0].components == (1, 2, 3)
    assert summary.versions[0].components == (8, 8, 8)


def test_summary_builder_aggregates_one_frozen_typed_record_snapshot() -> None:
    first = DiagnosticRecord(
        occurred_at=_NOW,
        event=DiagnosticEventCode.OPERATION_COMPLETED,
        case_id=_CASE_ID,
        operation_id=_OPERATION_A,
        operation_kind=DiagnosticOperationKind.PDF_EXPORT,
        outcome=DiagnosticOutcome.SUCCESS,
        versions=(DiagnosticVersion(DiagnosticVersionKind.APPLICATION, (1, 2, 3)),),
        counts=(DiagnosticCount(DiagnosticCountKind.PAGES_PROCESSED, 2),),
        resources=(
            DiagnosticResource(DiagnosticResourceKind.DURATION_MILLISECONDS, 10),
            DiagnosticResource(DiagnosticResourceKind.PEAK_MEMORY_BYTES, 100),
        ),
    )
    second = DiagnosticRecord(
        occurred_at=_NOW,
        event=DiagnosticEventCode.OPERATION_COMPLETED,
        case_id=_CASE_ID,
        operation_id=_OPERATION_B,
        operation_kind=DiagnosticOperationKind.PDF_EXPORT,
        outcome=DiagnosticOutcome.PARTIAL,
        failure_code=DiagnosticFailureCode.STORAGE_FAILED,
        versions=(DiagnosticVersion(DiagnosticVersionKind.APPLICATION, (1, 2, 3)),),
        counts=(DiagnosticCount(DiagnosticCountKind.PAGES_PROCESSED, 3),),
        resources=(
            DiagnosticResource(DiagnosticResourceKind.DURATION_MILLISECONDS, 20),
            DiagnosticResource(DiagnosticResourceKind.PEAK_MEMORY_BYTES, 80),
            DiagnosticResource(DiagnosticResourceKind.STORAGE_BYTES_RELEASED, 4),
        ),
    )

    summary = build_diagnostic_summary((first, second), generated_at=_NOW)

    assert summary.record_count == 2
    assert summary.operation_ids == (_OPERATION_A, _OPERATION_B)
    assert summary.codes == (DiagnosticFailureCode.STORAGE_FAILED,)
    assert {item.kind: item.value for item in summary.counts} == {
        DiagnosticCountKind.PAGES_PROCESSED: 5
    }
    assert {item.kind: item.value for item in summary.resources} == {
        DiagnosticResourceKind.DURATION_MILLISECONDS: 30,
        DiagnosticResourceKind.PEAK_MEMORY_BYTES: 100,
        DiagnosticResourceKind.STORAGE_BYTES_RELEASED: 4,
    }

    logger = SanitizedDiagnosticLogger(maximum_records=1)
    logger.emit(first)
    logger.emit(second)
    frozen = logger.snapshot_summary(generated_at=_NOW)
    assert frozen.record_count == 1
    assert frozen.dropped_record_count == 1
    assert frozen.operation_ids == (_OPERATION_B,)


def test_summary_builder_rejects_conflicting_versions() -> None:
    first = DiagnosticRecord(
        occurred_at=_NOW,
        event=DiagnosticEventCode.APPLICATION_STARTED,
        versions=(DiagnosticVersion(DiagnosticVersionKind.APPLICATION, (1,)),),
    )
    second = DiagnosticRecord(
        occurred_at=_NOW,
        event=DiagnosticEventCode.APPLICATION_STARTED,
        versions=(DiagnosticVersion(DiagnosticVersionKind.APPLICATION, (2,)),),
    )

    with pytest.raises(DiagnosticError) as raised:
        build_diagnostic_summary((first, second), generated_at=_NOW)

    assert raised.value.code is DiagnosticErrorCode.SUMMARY_INVALID


def test_summary_builder_rejects_an_unbounded_record_snapshot() -> None:
    record = DiagnosticRecord(
        occurred_at=_NOW,
        event=DiagnosticEventCode.APPLICATION_STARTED,
    )

    with pytest.raises(DiagnosticError) as raised:
        build_diagnostic_summary((record,) * 4_097, generated_at=_NOW)

    assert raised.value.code is DiagnosticErrorCode.SUMMARY_INVALID


def test_logger_summary_accepts_every_unique_operation_in_its_bounded_history() -> None:
    logger = SanitizedDiagnosticLogger(maximum_records=4_096)
    for index in range(1, 1_026):
        logger.emit(
            DiagnosticRecord(
                occurred_at=_NOW,
                event=DiagnosticEventCode.OPERATION_STARTED,
                case_id=_CASE_ID,
                operation_id=UUID(int=index, version=4),
                operation_kind=DiagnosticOperationKind.PDF_EXPORT,
            )
        )

    summary = logger.snapshot_summary(generated_at=_NOW)

    assert summary.record_count == 1_025
    assert len(summary.operation_ids) == 1_025
    assert summary.operation_ids == tuple(sorted(summary.operation_ids, key=lambda item: item.hex))


def test_renderer_revalidates_a_mutated_summary() -> None:
    summary = _summary()
    object.__setattr__(summary, "codes", cast(object, ("PRIVATE-FORM-VALUE",)))
    with pytest.raises(DiagnosticError) as raised:
        render_diagnostic_summary(summary)
    assert raised.value.code is DiagnosticErrorCode.SUMMARY_INVALID

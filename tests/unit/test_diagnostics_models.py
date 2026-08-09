"""Unit tests for the closed sanitized diagnostic record contract."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid1

import pytest

from paperworkshell.diagnostics.errors import DiagnosticError
from paperworkshell.diagnostics.models import (
    DIAGNOSTIC_RECORD_VERSION,
    MAX_DIAGNOSTIC_VERSION_COMPONENT,
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
    copy_diagnostic_record,
)

_NOW = datetime(2026, 8, 9, 12, 34, 56, 789, tzinfo=UTC)
_CASE_ID = UUID("00000000-0000-4000-8000-000000000001")
_OPERATION_ID = UUID("00000000-0000-4000-8000-000000000002")


def _record(event: DiagnosticEventCode) -> DiagnosticRecord:
    if event in (DiagnosticEventCode.OPERATION_STARTED, DiagnosticEventCode.OPERATION_COMPLETED):
        return DiagnosticRecord(
            occurred_at=_NOW,
            event=event,
            case_id=_CASE_ID,
            operation_id=_OPERATION_ID,
            operation_kind=DiagnosticOperationKind.PDF_EXPORT,
            outcome=(
                DiagnosticOutcome.SUCCESS
                if event is DiagnosticEventCode.OPERATION_COMPLETED
                else None
            ),
        )
    if event in (
        DiagnosticEventCode.CASE_RECOVERY_COMPLETED,
        DiagnosticEventCode.CLEANUP_COMPLETED,
        DiagnosticEventCode.WORKER_STOPPED,
    ):
        return DiagnosticRecord(
            occurred_at=_NOW,
            event=event,
            case_id=_CASE_ID,
            outcome=DiagnosticOutcome.SUCCESS,
        )
    if event is DiagnosticEventCode.WORKER_STARTED:
        return DiagnosticRecord(occurred_at=_NOW, event=event, case_id=_CASE_ID)
    if event is DiagnosticEventCode.WORKER_FAILED:
        return DiagnosticRecord(
            occurred_at=_NOW,
            event=event,
            case_id=_CASE_ID,
            outcome=DiagnosticOutcome.FAILED,
            failure_code=DiagnosticFailureCode.WORKER_TERMINATED,
        )
    if event in (
        DiagnosticEventCode.APPLICATION_STOPPED,
        DiagnosticEventCode.RECOVERY_SCAN_COMPLETED,
    ):
        return DiagnosticRecord(occurred_at=_NOW, event=event, outcome=DiagnosticOutcome.SUCCESS)
    return DiagnosticRecord(occurred_at=_NOW, event=event)


@pytest.mark.parametrize("event", tuple(DiagnosticEventCode))
def test_every_closed_event_has_a_valid_field_combination(event: DiagnosticEventCode) -> None:
    record = _record(event)
    assert record.event is event
    assert record.record_version == DIAGNOSTIC_RECORD_VERSION


def test_record_accepts_only_typed_bounded_unique_safe_entries() -> None:
    record = DiagnosticRecord(
        occurred_at=_NOW,
        event=DiagnosticEventCode.RECOVERY_SCAN_COMPLETED,
        outcome=DiagnosticOutcome.PARTIAL,
        failure_code=DiagnosticFailureCode.CASE_INVALID,
        versions=(DiagnosticVersion(DiagnosticVersionKind.APPLICATION, (0, 0, 0)),),
        counts=(DiagnosticCount(DiagnosticCountKind.CASES_SCANNED, 4),),
        resources=(DiagnosticResource(DiagnosticResourceKind.DURATION_MILLISECONDS, 25),),
    )
    assert record.counts[0].value == 4
    assert "00000000" not in repr(record)

    with pytest.raises(DiagnosticError):
        DiagnosticRecord(
            occurred_at=_NOW,
            event=DiagnosticEventCode.RECOVERY_SCAN_STARTED,
            counts=(
                DiagnosticCount(DiagnosticCountKind.CASES_SCANNED, 1),
                DiagnosticCount(DiagnosticCountKind.CASES_SCANNED, 2),
            ),
        )


@pytest.mark.parametrize(
    "components",
    (
        (),
        (True,),
        (1.0,),
        (1, 2, 3, 4, 5),
        (MAX_DIAGNOSTIC_VERSION_COMPONENT + 1,),
    ),
)
def test_version_components_reject_non_numeric_or_unbounded_values(
    components: tuple[object, ...],
) -> None:
    with pytest.raises(DiagnosticError):
        DiagnosticVersion(
            DiagnosticVersionKind.QPDF,
            cast(tuple[int, ...], components),
        )


@pytest.mark.parametrize("value", (-1, True, 1.5))
def test_count_and_resource_reject_non_integer_values(value: object) -> None:
    with pytest.raises(DiagnosticError):
        DiagnosticCount(DiagnosticCountKind.CASES_SCANNED, cast(int, value))
    with pytest.raises(DiagnosticError):
        DiagnosticResource(
            DiagnosticResourceKind.DURATION_MILLISECONDS,
            cast(int, value),
        )


@pytest.mark.parametrize(
    "changes",
    (
        {"occurred_at": datetime(2026, 8, 9)},
        {"case_id": uuid1()},
        {"record_version": 2},
        {"event": "RECOVERY_SCAN_STARTED"},
        {"counts": []},
    ),
)
def test_record_rejects_wrong_runtime_types_without_echoing_values(
    changes: dict[str, object],
) -> None:
    arguments: dict[str, object] = {
        "occurred_at": _NOW,
        "event": DiagnosticEventCode.RECOVERY_SCAN_STARTED,
    }
    arguments.update(changes)
    with pytest.raises(DiagnosticError) as raised:
        DiagnosticRecord(**arguments)  # type: ignore[arg-type]
    assert raised.value.code.value == "DIAGNOSTIC_RECORD_INVALID"


@pytest.mark.parametrize(
    "record",
    (
        lambda: DiagnosticRecord(
            occurred_at=_NOW,
            event=DiagnosticEventCode.OPERATION_STARTED,
            case_id=_CASE_ID,
        ),
        lambda: DiagnosticRecord(
            occurred_at=_NOW,
            event=DiagnosticEventCode.RECOVERY_SCAN_STARTED,
            case_id=_CASE_ID,
        ),
        lambda: DiagnosticRecord(
            occurred_at=_NOW,
            event=DiagnosticEventCode.RECOVERY_SCAN_COMPLETED,
        ),
        lambda: DiagnosticRecord(
            occurred_at=_NOW,
            event=DiagnosticEventCode.RECOVERY_SCAN_COMPLETED,
            outcome=DiagnosticOutcome.FAILED,
        ),
        lambda: DiagnosticRecord(
            occurred_at=_NOW,
            event=DiagnosticEventCode.RECOVERY_SCAN_COMPLETED,
            outcome=DiagnosticOutcome.SUCCESS,
            failure_code=DiagnosticFailureCode.INTERNAL_ERROR,
        ),
    ),
)
def test_event_matrix_rejects_incomplete_or_inconsistent_fields(
    record: Callable[[], DiagnosticRecord],
) -> None:
    with pytest.raises(DiagnosticError):
        record()


class _ExplosiveValue:
    def __str__(self) -> str:
        raise AssertionError("diagnostics rendered a rejected value")

    def __repr__(self) -> str:
        raise AssertionError("diagnostics represented a rejected value")


def test_rejected_objects_are_never_stringified_or_represented() -> None:
    with pytest.raises(DiagnosticError):
        DiagnosticRecord(
            occurred_at=_NOW,
            event=cast(DiagnosticEventCode, _ExplosiveValue()),
        )


def test_record_and_copy_own_independent_nested_snapshots() -> None:
    supplied = DiagnosticVersion(DiagnosticVersionKind.APPLICATION, (1, 2, 3))
    record = DiagnosticRecord(
        occurred_at=_NOW,
        event=DiagnosticEventCode.APPLICATION_STARTED,
        versions=(supplied,),
    )
    copied = copy_diagnostic_record(record)

    object.__setattr__(supplied, "components", (9, 9, 9))
    object.__setattr__(record.versions[0], "components", (8, 8, 8))

    assert copied.versions[0].components == (1, 2, 3)
    assert record.versions[0].components == (8, 8, 8)

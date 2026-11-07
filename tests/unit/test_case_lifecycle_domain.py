"""Unit coverage for pure case-lifecycle contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.domain.case_lifecycle import (
    CURRENT_CASE_MANIFEST_VERSION,
    CaseDeletionAssurance,
    CaseDeletionResult,
    CaseDeletionStatus,
    CaseIndex,
    CaseIndexIssue,
    CaseLifecycleError,
    CaseLifecycleErrorCode,
    CaseSummary,
    require_case_id,
    require_case_name,
)

FIRST_ID = UUID("10000000-0000-4000-8000-000000000001")
SECOND_ID = UUID("20000000-0000-4000-8000-000000000002")
NOW = datetime(2026, 8, 2, 10, 0, tzinfo=UTC)


@pytest.mark.parametrize("value", ["Helsinki – vuokra 2026", "Åäö é", "a", "a" * 200])
def test_case_name_accepts_normal_unicode_display_text(value: str) -> None:
    assert require_case_name(f"  {value}  ") == value


@pytest.mark.parametrize("value", ["", "   ", "x" * 201, "a\nb", "a\x00b", 123])
def test_case_name_invalid_input_is_sanitized(value: object) -> None:
    with pytest.raises(CaseLifecycleError) as raised:
        require_case_name(value)
    assert raised.value.code is CaseLifecycleErrorCode.CASE_NAME_INVALID
    if len(str(value).strip()) > 3:
        assert str(value) not in repr(raised.value)


def test_case_summary_never_formats_private_name() -> None:
    summary = CaseSummary(FIRST_ID, 4, 0, NOW, NOW, case_name="PRIVATE_CASE_NAME_TOKEN")
    assert summary.case_name == "PRIVATE_CASE_NAME_TOKEN"
    assert "PRIVATE_CASE_NAME_TOKEN" not in repr(summary)


def _summary(case_id: UUID = FIRST_ID, *, revision: int = 0) -> CaseSummary:
    return CaseSummary(
        case_id=case_id,
        manifest_version=CURRENT_CASE_MANIFEST_VERSION,
        revision=revision,
        created_at=NOW,
        updated_at=NOW + timedelta(minutes=revision),
    )


def test_case_summary_is_path_free_and_immutable() -> None:
    summary = _summary()

    assert summary.case_id == FIRST_ID
    assert summary.manifest_version == CURRENT_CASE_MANIFEST_VERSION
    assert "path" not in repr(summary).casefold()
    with pytest.raises(FrozenInstanceError):
        summary.revision = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    ("replacement", "expected_code"),
    [
        (
            {"case_id": UUID("10000000-0000-1000-8000-000000000001")},
            CaseLifecycleErrorCode.CASE_ID_INVALID,
        ),
        (
            {"manifest_version": CURRENT_CASE_MANIFEST_VERSION + 1},
            CaseLifecycleErrorCode.MANIFEST_VERSION_UNSUPPORTED,
        ),
        ({"revision": True}, CaseLifecycleErrorCode.MANIFEST_INVALID),
        ({"revision": -1}, CaseLifecycleErrorCode.MANIFEST_INVALID),
        ({"created_at": NOW.replace(tzinfo=None)}, CaseLifecycleErrorCode.MANIFEST_INVALID),
        (
            {"created_at": NOW + timedelta(seconds=1)},
            CaseLifecycleErrorCode.MANIFEST_INVALID,
        ),
    ],
)
def test_case_summary_rejects_invalid_lifecycle_metadata(
    replacement: dict[str, object],
    expected_code: CaseLifecycleErrorCode,
) -> None:
    values: dict[str, object] = {
        "case_id": FIRST_ID,
        "manifest_version": 1,
        "revision": 0,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(replacement)

    with pytest.raises(CaseLifecycleError) as raised:
        CaseSummary(**values)  # type: ignore[arg-type]

    assert raised.value.code is expected_code


def test_case_summary_accepts_equivalent_zero_offset_timestamp() -> None:
    zero_offset = timezone(timedelta(0), name="synthetic-zero-offset")

    summary = CaseSummary(
        case_id=FIRST_ID,
        manifest_version=1,
        revision=0,
        created_at=NOW.astimezone(zero_offset),
        updated_at=NOW.astimezone(zero_offset),
    )

    assert summary.created_at.utcoffset() == timedelta(0)


def test_case_index_sorts_internal_ids_and_keeps_unavailable_entries_separate() -> None:
    index = CaseIndex(
        cases=(_summary(SECOND_ID), _summary(FIRST_ID)),
        unavailable=(
            CaseIndexIssue(
                UUID("30000000-0000-4000-8000-000000000003"),
                CaseLifecycleErrorCode.CASE_INCOMPLETE,
            ),
        ),
    )

    assert tuple(item.case_id for item in index.cases) == (FIRST_ID, SECOND_ID)
    assert index.unavailable[0].code is CaseLifecycleErrorCode.CASE_INCOMPLETE


@pytest.mark.parametrize("kind", ["duplicate-valid", "duplicate-unavailable", "overlap"])
def test_case_index_rejects_duplicate_or_overlapping_ids(kind: str) -> None:
    first_summary = _summary()
    first_issue = CaseIndexIssue(FIRST_ID, CaseLifecycleErrorCode.CASE_INCOMPLETE)
    cases: tuple[CaseSummary, ...]
    unavailable: tuple[CaseIndexIssue, ...]
    if kind == "duplicate-valid":
        cases = (first_summary, first_summary)
        unavailable = ()
    elif kind == "duplicate-unavailable":
        cases = ()
        unavailable = (first_issue, first_issue)
    else:
        cases = (first_summary,)
        unavailable = (first_issue,)

    with pytest.raises(CaseLifecycleError) as raised:
        CaseIndex(cases=cases, unavailable=unavailable)

    assert raised.value.code is CaseLifecycleErrorCode.LIST_FAILED


def test_complete_deletion_result_states_limited_assurance() -> None:
    result = CaseDeletionResult(
        case_id=FIRST_ID,
        status=CaseDeletionStatus.COMPLETE,
        removed_files=3,
        removed_directories=7,
        released_bytes=4096,
    )

    assert result.assurance is CaseDeletionAssurance.APPLICATION_STORAGE_REMOVAL_ONLY
    assert result.failure_code is None
    assert "does not guarantee forensic erasure" in result.safe_message
    assert "securely erased" not in result.safe_message.casefold()
    assert "unrecoverable" not in result.safe_message.casefold()


def test_partial_deletion_requires_failure_code_and_reports_remaining_risk() -> None:
    with pytest.raises(CaseLifecycleError) as missing_failure:
        CaseDeletionResult(
            case_id=FIRST_ID,
            status=CaseDeletionStatus.PARTIAL,
            removed_files=1,
            removed_directories=0,
            released_bytes=10,
        )
    with pytest.raises(CaseLifecycleError) as complete_with_failure:
        CaseDeletionResult(
            case_id=FIRST_ID,
            status=CaseDeletionStatus.COMPLETE,
            removed_files=1,
            removed_directories=1,
            released_bytes=10,
            failure_code=CaseLifecycleErrorCode.DELETE_FAILED,
        )

    partial = CaseDeletionResult(
        case_id=FIRST_ID,
        status=CaseDeletionStatus.PARTIAL,
        removed_files=1,
        removed_directories=0,
        released_bytes=10,
        failure_code=CaseLifecycleErrorCode.DELETE_FAILED,
    )

    assert missing_failure.value.code is CaseLifecycleErrorCode.DELETE_FAILED
    assert complete_with_failure.value.code is CaseLifecycleErrorCode.DELETE_FAILED
    assert "Some files may remain" in partial.safe_message


@pytest.mark.parametrize("count", [-1, True])
def test_deletion_result_rejects_invalid_counts(count: int) -> None:
    with pytest.raises(CaseLifecycleError) as raised:
        CaseDeletionResult(
            case_id=FIRST_ID,
            status=CaseDeletionStatus.COMPLETE,
            removed_files=count,
            removed_directories=0,
            released_bytes=0,
        )

    assert raised.value.code is CaseLifecycleErrorCode.DELETE_FAILED


def test_deletion_result_rejects_string_status_even_when_value_matches() -> None:
    with pytest.raises(CaseLifecycleError) as raised:
        CaseDeletionResult(
            case_id=FIRST_ID,
            status=cast(CaseDeletionStatus, "complete"),
            removed_files=0,
            removed_directories=0,
            released_bytes=0,
        )

    assert raised.value.code is CaseLifecycleErrorCode.DELETE_FAILED


def test_lifecycle_error_uses_closed_message_and_sanitizes_context() -> None:
    prohibited = r"C:\private\applicant-name.pdf"
    error = CaseLifecycleError(CaseLifecycleErrorCode.CASE_NOT_FOUND, prohibited)

    assert error.context == "case.lifecycle"
    assert prohibited not in str(error)
    assert prohibited not in repr(error)
    assert prohibited not in repr(error.args)
    assert error.safe_message == "The selected case is not available."
    error.__traceback__ = None


def test_require_case_id_rejects_text_without_echoing_it() -> None:
    prohibited = r"C:\private\case-name"

    with pytest.raises(CaseLifecycleError) as raised:
        require_case_id(prohibited)

    assert raised.value.code is CaseLifecycleErrorCode.CASE_ID_INVALID
    assert prohibited not in str(raised.value)

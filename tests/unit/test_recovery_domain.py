from __future__ import annotations

from uuid import UUID

import pytest

from paperworkshell.domain.recovery import (
    CaseRecoveryResult,
    CaseRecoveryStatus,
    ExportHistoryRecoveryResult,
    RecoveryError,
    RecoveryErrorCode,
    RecoveryIssueCode,
    StartupRecoveryResult,
    StartupRecoveryStatus,
)

CASE_A = UUID("10000000-0000-4000-8000-000000000001")
CASE_B = UUID("20000000-0000-4000-8000-000000000002")


def test_startup_result_orders_cases_and_reports_safe_aggregate_counts() -> None:
    unavailable = CaseRecoveryResult(
        case_id=CASE_B,
        status=CaseRecoveryStatus.UNAVAILABLE,
        manifest_revision=None,
        issues=(RecoveryIssueCode.MANIFEST_INVALID,),
    )
    recovered = CaseRecoveryResult(
        case_id=CASE_A,
        status=CaseRecoveryStatus.RECOVERED,
        manifest_revision=3,
        removed_source_orphans=1,
        removed_thumbnail_stages=2,
        removed_temp_files=3,
        removed_temp_directories=1,
        released_bytes=4096,
    )

    result = StartupRecoveryResult((unavailable, recovered))

    assert result.cases == (recovered, unavailable)
    assert result.status is StartupRecoveryStatus.ATTENTION_REQUIRED
    assert result.scanned_cases == 2
    assert result.recovered_cases == 1
    assert result.unavailable_cases == 1


def test_available_cleanup_issues_are_deduplicated_and_sorted_only_when_valid() -> None:
    result = CaseRecoveryResult(
        case_id=CASE_A,
        status=CaseRecoveryStatus.CURRENT,
        manifest_revision=0,
        issues=(
            RecoveryIssueCode.TEMP_CLEANUP_PARTIAL,
            RecoveryIssueCode.SOURCE_ARTIFACTS_RETAINED,
        ),
        retained_source_artifacts=1,
    )

    assert result.issues == (
        RecoveryIssueCode.SOURCE_ARTIFACTS_RETAINED,
        RecoveryIssueCode.TEMP_CLEANUP_PARTIAL,
    )
    assert StartupRecoveryResult((result,)).status is StartupRecoveryStatus.ATTENTION_REQUIRED

    with pytest.raises(RecoveryError) as duplicate:
        CaseRecoveryResult(
            case_id=CASE_A,
            status=CaseRecoveryStatus.CURRENT,
            manifest_revision=0,
            issues=(
                RecoveryIssueCode.TEMP_CLEANUP_PARTIAL,
                RecoveryIssueCode.TEMP_CLEANUP_PARTIAL,
            ),
        )
    assert duplicate.value.code is RecoveryErrorCode.MODEL_INVALID


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "case_id": CASE_A,
            "status": CaseRecoveryStatus.UNAVAILABLE,
            "manifest_revision": None,
        },
        {
            "case_id": CASE_A,
            "status": CaseRecoveryStatus.CURRENT,
            "manifest_revision": None,
        },
        {
            "case_id": CASE_A,
            "status": CaseRecoveryStatus.UNAVAILABLE,
            "manifest_revision": None,
            "issues": (RecoveryIssueCode.MANIFEST_INVALID,),
            "removed_temp_files": 1,
        },
    ],
)
def test_case_result_rejects_ambiguous_unavailable_or_cleanup_state(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(RecoveryError) as raised:
        CaseRecoveryResult(**kwargs)  # type: ignore[arg-type]

    assert raised.value.code is RecoveryErrorCode.MODEL_INVALID
    assert "C:\\private\\fixture.pdf" not in str(raised.value)
    assert "C:\\private\\fixture.pdf" not in repr(raised.value)


def test_startup_result_rejects_duplicate_case_ids() -> None:
    current = CaseRecoveryResult(CASE_A, CaseRecoveryStatus.CURRENT, 0)
    recovered = CaseRecoveryResult(CASE_A, CaseRecoveryStatus.RECOVERED, 1)

    with pytest.raises(RecoveryError) as raised:
        StartupRecoveryResult((current, recovered))

    assert raised.value.code is RecoveryErrorCode.MODEL_INVALID


def test_export_history_recovery_counts_map_to_closed_attention_codes() -> None:
    export = ExportHistoryRecoveryResult(
        repaired_entries=1,
        pending_entries=2,
        ambiguous_entries=3,
        invalid_entries=4,
        recovery_failed=True,
    )

    assert export.issues == (
        RecoveryIssueCode.EXPORT_COMMIT_AMBIGUOUS,
        RecoveryIssueCode.EXPORT_HISTORY_PENDING,
        RecoveryIssueCode.EXPORT_RETRY_INVALID,
        RecoveryIssueCode.EXPORT_RECOVERY_FAILED,
    )
    case = CaseRecoveryResult(
        case_id=CASE_A,
        status=CaseRecoveryStatus.RECOVERED,
        manifest_revision=2,
        issues=export.issues,
        repaired_export_history=export.repaired_entries,
        pending_export_history=export.pending_entries,
        ambiguous_export_commits=export.ambiguous_entries,
        invalid_export_retries=export.invalid_entries,
    )
    assert StartupRecoveryResult((case,)).status is StartupRecoveryStatus.ATTENTION_REQUIRED

    with pytest.raises(RecoveryError):
        ExportHistoryRecoveryResult(pending_entries=-1)

    with pytest.raises(RecoveryError):
        ExportHistoryRecoveryResult(recovery_failed=1)  # type: ignore[arg-type]

    with pytest.raises(RecoveryError):
        CaseRecoveryResult(
            case_id=CASE_A,
            status=CaseRecoveryStatus.CURRENT,
            manifest_revision=2,
            ambiguous_export_commits=1,
        )

    with pytest.raises(RecoveryError):
        CaseRecoveryResult(
            case_id=CASE_A,
            status=CaseRecoveryStatus.CURRENT,
            manifest_revision=2,
            repaired_export_history=1,
        )

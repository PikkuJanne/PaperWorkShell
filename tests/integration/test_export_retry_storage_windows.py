"""Windows restart evidence for protected PDF export retry receipts."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.__main__ import build_windows_startup_recovery_service
from paperworkshell.app.export_history import (
    PdfExportHistoryService,
    PdfExportRetryRecoveryService,
)
from paperworkshell.domain.pdf_export import (
    PdfExportHistoryRetryReceipt,
    PdfExportOutcomeStatus,
    PdfExportPage,
    PdfExportPlan,
    PdfExportRotation,
    PdfExportSource,
    PdfExportSourceSignatureState,
    PdfExportVerificationReport,
)
from paperworkshell.domain.pdf_export_history import (
    PdfExportHistoryDocument,
    PdfExportHistoryError,
    build_pdf_export_history_record_for_commit,
    build_pdf_export_history_retry_receipt,
    canonical_pdf_export_history_bytes,
    decode_pdf_export_history_record,
)
from paperworkshell.domain.recovery import (
    CaseRecoveryStatus,
    RecoveryIssueCode,
    StartupRecoveryStatus,
)
from paperworkshell.storage import export_retry as export_retry_module
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.export_history import WindowsPdfExportHistoryRepository
from paperworkshell.storage.export_retry import (
    WindowsPdfExportRetryRepository,
    WindowsPreparedPdfExportRetry,
)
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.recovery import WindowsStartupRecoveryStore
from paperworkshell.storage.workspace import (
    CaseWorkspaceManager,
    CleanupTarget,
    WorkspaceCapacityReservation,
)

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0113 retry persistence targets the supported Windows storage boundary",
)

CASE_ID = UUID("10000000-0000-4000-8000-000000000813")
SOURCE_ID = UUID("20000000-0000-4000-8000-000000000813")
PAGE_ID = UUID("30000000-0000-4000-8000-000000000813")
EXPORT_ID = UUID("40000000-0000-4000-8000-000000000813")
SECOND_EXPORT_ID = UUID("41000000-0000-4000-8000-000000000813")
PREPARED_STAGE_ID = UUID("50000000-0000-4000-8000-000000000813")
COMMITTED_STAGE_ID = UUID("60000000-0000-4000-8000-000000000813")
RETIRED_HISTORY_ID = UUID("70000000-0000-4000-8000-000000000813")
SECOND_RETIRED_HISTORY_ID = UUID("71000000-0000-4000-8000-000000000813")
CRASH_STAGE_ID = UUID("80000000-0000-4000-8000-000000000813")
RECORDED_AT = datetime(2026, 8, 9, 12, 34, 56, 123456, tzinfo=UTC)


def _manager(tmp_path: Path) -> CaseWorkspaceManager:
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=WorkspaceQuota(
            maximum_case_bytes=4_000_000,
            maximum_cache_bytes=1_000_000,
            maximum_temp_bytes=1_000_000,
            maximum_entries=256,
            maximum_depth=16,
        ),
        id_factory=lambda: CASE_ID,
    )
    lease = WindowsCaseLifecycleStore(manager).create_case()
    lease.close()
    return manager


def _receipt(
    *,
    export_id: UUID = EXPORT_ID,
    output_sha256: str = "c" * 64,
    recorded_at: datetime | None = None,
) -> PdfExportHistoryRetryReceipt:
    plan = PdfExportPlan(
        case_id=CASE_ID,
        manifest_revision=0,
        manifest_sha256="b" * 64,
        manifest_cas_token="retry_storage_cas_token_0001",
        sources=(
            PdfExportSource(
                source_id=SOURCE_ID,
                byte_size=99,
                page_count=1,
                signature_state=PdfExportSourceSignatureState.NONE_DETECTED,
                sha256="a" * 64,
            ),
        ),
        pages=(
            PdfExportPage(
                output_page_index=0,
                page_id=PAGE_ID,
                source_id=SOURCE_ID,
                source_page_index=0,
                additional_rotation=PdfExportRotation.DEGREES_0,
            ),
        ),
    )
    report = PdfExportVerificationReport.for_verified_plan(
        plan,
        output_sha256=output_sha256,
        output_byte_size=1_234,
    )
    record = build_pdf_export_history_record_for_commit(
        plan,
        export_id=export_id,
        report=report,
        recorded_at=RECORDED_AT if recorded_at is None else recorded_at,
    )
    return build_pdf_export_history_retry_receipt(plan, record, report)


def _repository(manager: CaseWorkspaceManager) -> WindowsPdfExportRetryRepository:
    identifiers = iter((PREPARED_STAGE_ID, COMMITTED_STAGE_ID))
    return WindowsPdfExportRetryRepository(manager, id_factory=lambda: next(identifiers))


def test_prepared_receipt_survives_restart_and_generic_temp_cleanup(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    receipt = _receipt()
    lease = _repository(manager).prepare(receipt)

    lease.close()
    restarted = WindowsPdfExportRetryRepository(manager)
    before_cleanup = restarted.inventory(CASE_ID)

    assert before_cleanup.prepared == (receipt,)
    assert before_cleanup.committed == ()
    assert before_cleanup.reserved_export_ids == frozenset({EXPORT_ID})
    assert manager.cleanup_disposable(CASE_ID, CleanupTarget.TEMP).status.value == "complete"
    assert restarted.inventory(CASE_ID).prepared == (receipt,)
    assert restarted.discard_prepared(CASE_ID, EXPORT_ID, receipt.record_sha256)
    assert restarted.inventory(CASE_ID).reserved_export_ids == frozenset()


def test_committed_receipt_reopens_exactly_and_clears_idempotently(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    receipt = _receipt()
    lease = _repository(manager).prepare(receipt)

    assert lease.mark_committed()
    lease.close()
    restarted = WindowsPdfExportRetryRepository(manager)
    inventory = restarted.inventory(CASE_ID)

    assert inventory.committed == (receipt,)
    assert inventory.prepared == ()
    assert inventory.invalid_entries == 0
    assert restarted.discard_committed(CASE_ID, EXPORT_ID, receipt.record_sha256)
    assert restarted.discard_committed(CASE_ID, EXPORT_ID, receipt.record_sha256)
    assert restarted.inventory(CASE_ID).reserved_export_ids == frozenset()


def test_close_retries_one_shot_release_failure_and_releases_all_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    lease = _repository(manager).prepare(_receipt())
    assert type(lease) is WindowsPreparedPdfExportRetry
    committed = lease._committed_reservation
    prepared = lease._prepared_reservation
    authority = lease._authority
    original_release = WorkspaceCapacityReservation.release
    failed_once = False

    def flaky_release(reservation: WorkspaceCapacityReservation) -> None:
        nonlocal failed_once
        if reservation is committed and not failed_once:
            failed_once = True
            raise KeyboardInterrupt
        original_release(reservation)

    monkeypatch.setattr(WorkspaceCapacityReservation, "release", flaky_release)

    lease.close()

    assert failed_once
    assert not committed.is_active
    assert not prepared.is_active
    assert not authority.is_open
    assert "closed=True" in repr(lease)


def test_failed_prepare_releases_both_reservations_after_one_shot_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    reservations: list[WorkspaceCapacityReservation] = []
    original_reserve = CaseWorkspaceManager.reserve_capacity
    original_release = WorkspaceCapacityReservation.release
    failed_once = False

    def capture_reservation(
        selected: CaseWorkspaceManager,
        *args: object,
        **kwargs: object,
    ) -> WorkspaceCapacityReservation:
        reserve = cast(Callable[..., WorkspaceCapacityReservation], original_reserve)
        reservation = reserve(selected, *args, **kwargs)
        if selected is manager:
            reservations.append(reservation)
        return reservation

    def flaky_release(reservation: WorkspaceCapacityReservation) -> None:
        nonlocal failed_once
        if reservations and reservation is reservations[0] and not failed_once:
            failed_once = True
            raise KeyboardInterrupt
        original_release(reservation)

    def fail_write(_path: Path, _payload: bytes) -> object:
        raise OSError("synthetic write failure")

    monkeypatch.setattr(CaseWorkspaceManager, "reserve_capacity", capture_reservation)
    monkeypatch.setattr(WorkspaceCapacityReservation, "release", flaky_release)
    monkeypatch.setattr(export_retry_module, "write_new_file", fail_write)

    with pytest.raises(PdfExportHistoryError):
        _repository(manager).prepare(_receipt())

    assert failed_once
    assert len(reservations) == 2
    assert all(not reservation.is_active for reservation in reservations)
    with manager.authenticate_case(CASE_ID, for_deletion=True):
        pass


def test_invalid_owned_entry_is_isolated_and_its_id_remains_reserved(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    layout = manager.layout(CASE_ID)
    invalid = layout.journal / f"export-retry-{EXPORT_ID}.committed.json"
    invalid.write_bytes(b"not-a-retry-entry")

    inventory = WindowsPdfExportRetryRepository(manager).inventory(CASE_ID)

    assert inventory.committed == ()
    assert inventory.prepared == ()
    assert inventory.invalid_entries == 1
    assert inventory.invalid_export_ids == (EXPORT_ID,)
    assert inventory.reserved_export_ids == frozenset({EXPORT_ID})
    assert invalid.read_bytes() == b"not-a-retry-entry"


def test_malformed_owned_name_is_reported_and_reserves_its_parseable_id(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    layout = manager.layout(CASE_ID)
    malformed = layout.journal / f"export-retry-{EXPORT_ID}.future.json"
    malformed.write_bytes(b"synthetic-future-owner-entry")

    inventory = WindowsPdfExportRetryRepository(manager).inventory(CASE_ID)

    assert inventory.invalid_entries == 1
    assert inventory.invalid_export_ids == (EXPORT_ID,)
    assert inventory.reserved_export_ids == frozenset({EXPORT_ID})
    assert malformed.read_bytes() == b"synthetic-future-owner-entry"


def test_fresh_startup_replays_committed_history_without_export_or_destination(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    receipt = _receipt()
    lease = _repository(manager).prepare(receipt)
    assert lease.mark_committed()
    lease.close()

    retries = WindowsPdfExportRetryRepository(manager)
    history_repository = WindowsPdfExportHistoryRepository(manager)
    manifests = WindowsManifestRepository(manager)
    recovery = PdfExportRetryRecoveryService(
        PdfExportHistoryService(history_repository, manifests),
        retries,
    )

    result = WindowsStartupRecoveryStore(
        manager,
        settle_exports=recovery.recover_case,
    ).scan_and_recover()

    case = result.cases[0]
    assert case.status is CaseRecoveryStatus.RECOVERED
    assert case.repaired_export_history == 1
    assert case.pending_export_history == 0
    assert case.ambiguous_export_commits == 0
    assert retries.inventory(CASE_ID).reserved_export_ids == frozenset()
    records = history_repository.load(CASE_ID).document.records
    assert len(records) == 1
    assert records[0].export_id == EXPORT_ID
    manifest_history = manifests.load(CASE_ID).snapshot.document["export_history"]
    assert type(manifest_history) is dict
    assert manifest_history["entries"] == [records[0].manifest_reference]


def test_fresh_startup_clears_prepared_receipt_with_exact_downstream_proof(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    receipt = _receipt()
    lease = _repository(manager).prepare(receipt)
    lease.close()
    retries = WindowsPdfExportRetryRepository(manager)
    history_repository = WindowsPdfExportHistoryRepository(manager)
    manifests = WindowsManifestRepository(manager)
    history = PdfExportHistoryService(history_repository, manifests)
    assert (
        history.retry_record_and_link(CASE_ID, receipt).status
        is PdfExportOutcomeStatus.COMMITTED_RECORDED
    )

    recovery = PdfExportRetryRecoveryService(history, retries)
    result = WindowsStartupRecoveryStore(
        manager,
        settle_exports=recovery.recover_case,
    ).scan_and_recover()

    assert result.cases[0].repaired_export_history == 1
    assert retries.inventory(CASE_ID).reserved_export_ids == frozenset()
    assert len(history_repository.load(CASE_ID).document.records) == 1


def test_cold_start_restores_retired_history_before_replaying_committed_receipt(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    layout = manager.layout(CASE_ID)
    manifests = WindowsManifestRepository(manager)
    history_repository = WindowsPdfExportHistoryRepository(manager)
    history = PdfExportHistoryService(history_repository, manifests)
    first_receipt = _receipt()
    assert (
        history.retry_record_and_link(CASE_ID, first_receipt).status
        is PdfExportOutcomeStatus.COMMITTED_RECORDED
    )
    first_record = decode_pdf_export_history_record(first_receipt.record_payload)

    second_receipt = _receipt(
        export_id=SECOND_EXPORT_ID,
        output_sha256="d" * 64,
        recorded_at=RECORDED_AT.replace(second=57),
    )
    second_record = decode_pdf_export_history_record(second_receipt.record_payload)
    retry_lease = _repository(manager).prepare(second_receipt)
    assert retry_lease.mark_committed()
    retry_lease.close()

    retired = layout.temp / f"{RETIRED_HISTORY_ID}.retired"
    interrupted_stage = layout.temp / f"{CRASH_STAGE_ID}.tmp"
    os.replace(layout.export_history, retired)
    interrupted_stage.write_bytes(
        canonical_pdf_export_history_bytes(PdfExportHistoryDocument((first_record, second_record)))
    )

    startup = build_windows_startup_recovery_service(manager).recover_startup()

    case = startup.cases[0]
    assert startup.status is StartupRecoveryStatus.COMPLETE
    assert case.status is CaseRecoveryStatus.RECOVERED
    assert case.repaired_export_history == 1
    assert history_repository.load(CASE_ID).document.records == (first_record, second_record)
    manifest_history = manifests.load(CASE_ID).snapshot.document["export_history"]
    assert type(manifest_history) is dict
    assert manifest_history["entries"] == [
        first_record.manifest_reference,
        second_record.manifest_reference,
    ]
    assert WindowsPdfExportRetryRepository(manager).inventory(CASE_ID).reserved_export_ids == (
        frozenset()
    )
    assert tuple(layout.temp.iterdir()) == ()


def test_retired_history_restore_marks_case_recovered_without_counting_receipt_replay(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    layout = manager.layout(CASE_ID)
    manifests = WindowsManifestRepository(manager)
    history_repository = WindowsPdfExportHistoryRepository(manager)
    history = PdfExportHistoryService(history_repository, manifests)
    receipt = _receipt()
    assert (
        history.retry_record_and_link(CASE_ID, receipt).status
        is PdfExportOutcomeStatus.COMMITTED_RECORDED
    )
    record = decode_pdf_export_history_record(receipt.record_payload)
    retired = layout.temp / f"{RETIRED_HISTORY_ID}.retired"
    os.replace(layout.export_history, retired)

    startup = build_windows_startup_recovery_service(manager).recover_startup()

    case = startup.cases[0]
    assert case.status is CaseRecoveryStatus.RECOVERED
    assert case.repaired_export_history == 0
    assert history_repository.load(CASE_ID).document.records == (record,)
    assert not retired.exists()


def test_stable_history_allows_strict_prefix_retirement_cleanup(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    layout = manager.layout(CASE_ID)
    manifests = WindowsManifestRepository(manager)
    history_repository = WindowsPdfExportHistoryRepository(manager)
    history = PdfExportHistoryService(history_repository, manifests)
    first_receipt = _receipt()
    second_receipt = _receipt(
        export_id=SECOND_EXPORT_ID,
        output_sha256="d" * 64,
        recorded_at=RECORDED_AT.replace(second=57),
    )
    assert (
        history.retry_record_and_link(CASE_ID, first_receipt).status
        is PdfExportOutcomeStatus.COMMITTED_RECORDED
    )
    assert (
        history.retry_record_and_link(CASE_ID, second_receipt).status
        is PdfExportOutcomeStatus.COMMITTED_RECORDED
    )
    first_record = decode_pdf_export_history_record(first_receipt.record_payload)
    second_record = decode_pdf_export_history_record(second_receipt.record_payload)
    retired = layout.temp / f"{RETIRED_HISTORY_ID}.retired"
    unrelated_temp = layout.temp / f"{CRASH_STAGE_ID}.tmp"
    retired.write_bytes(
        canonical_pdf_export_history_bytes(PdfExportHistoryDocument((first_record,)))
    )
    unrelated_temp.write_bytes(b"synthetic-cleanable-temp")

    startup = build_windows_startup_recovery_service(manager).recover_startup()

    case = startup.cases[0]
    assert startup.status is StartupRecoveryStatus.COMPLETE
    assert case.status is CaseRecoveryStatus.CURRENT
    assert case.removed_temp_files == 2
    assert history_repository.load(CASE_ID).document.records == (first_record, second_record)
    assert tuple(layout.temp.iterdir()) == ()


def test_linked_retirement_blocks_cleanup_and_preserves_all_evidence(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    layout = manager.layout(CASE_ID)
    manifests = WindowsManifestRepository(manager)
    history_repository = WindowsPdfExportHistoryRepository(manager)
    history = PdfExportHistoryService(history_repository, manifests)
    receipt = _receipt()
    assert (
        history.retry_record_and_link(CASE_ID, receipt).status
        is PdfExportOutcomeStatus.COMMITTED_RECORDED
    )
    stable_bytes = layout.export_history.read_bytes()
    outside = tmp_path / "outside-history-sentinel.bin"
    outside.write_bytes(stable_bytes)
    linked_retirement = layout.temp / f"{RETIRED_HISTORY_ID}.retired"
    unrelated_temp = layout.temp / f"{CRASH_STAGE_ID}.tmp"
    os.link(outside, linked_retirement)
    unrelated_temp.write_bytes(b"synthetic-unrelated-temp-evidence")

    startup = build_windows_startup_recovery_service(manager).recover_startup()

    case = startup.cases[0]
    assert startup.status is StartupRecoveryStatus.ATTENTION_REQUIRED
    assert case.status is CaseRecoveryStatus.CURRENT
    assert case.issues == (RecoveryIssueCode.EXPORT_RECOVERY_FAILED,)
    assert outside.read_bytes() == stable_bytes
    assert linked_retirement.exists()
    assert unrelated_temp.read_bytes() == b"synthetic-unrelated-temp-evidence"
    assert layout.export_history.read_bytes() == stable_bytes


def test_corrupt_stable_history_does_not_promote_valid_retirement_or_clean_temp(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    layout = manager.layout(CASE_ID)
    manifests = WindowsManifestRepository(manager)
    history_repository = WindowsPdfExportHistoryRepository(manager)
    history = PdfExportHistoryService(history_repository, manifests)
    receipt = _receipt()
    assert (
        history.retry_record_and_link(CASE_ID, receipt).status
        is PdfExportOutcomeStatus.COMMITTED_RECORDED
    )
    stable_bytes = layout.export_history.read_bytes()
    retired = layout.temp / f"{RETIRED_HISTORY_ID}.retired"
    temp_evidence = layout.temp / f"{CRASH_STAGE_ID}.tmp"
    retired.write_bytes(stable_bytes)
    layout.export_history.write_bytes(b"synthetic-corrupt-stable-history")
    temp_evidence.write_bytes(b"synthetic-corrupt-target-evidence")

    startup = build_windows_startup_recovery_service(manager).recover_startup()

    case = startup.cases[0]
    assert startup.status is StartupRecoveryStatus.ATTENTION_REQUIRED
    assert case.issues == (RecoveryIssueCode.EXPORT_RECOVERY_FAILED,)
    assert layout.export_history.read_bytes() == b"synthetic-corrupt-stable-history"
    assert retired.read_bytes() == stable_bytes
    assert temp_evidence.read_bytes() == b"synthetic-corrupt-target-evidence"


def test_divergent_retirement_is_unresolved_and_preserves_all_temp(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    layout = manager.layout(CASE_ID)
    manifests = WindowsManifestRepository(manager)
    history_repository = WindowsPdfExportHistoryRepository(manager)
    history = PdfExportHistoryService(history_repository, manifests)
    first_receipt = _receipt()
    second_receipt = _receipt(
        export_id=SECOND_EXPORT_ID,
        output_sha256="d" * 64,
        recorded_at=RECORDED_AT.replace(second=57),
    )
    assert (
        history.retry_record_and_link(CASE_ID, first_receipt).status
        is PdfExportOutcomeStatus.COMMITTED_RECORDED
    )
    assert (
        history.retry_record_and_link(CASE_ID, second_receipt).status
        is PdfExportOutcomeStatus.COMMITTED_RECORDED
    )
    second_record = decode_pdf_export_history_record(second_receipt.record_payload)
    retired = layout.temp / f"{RETIRED_HISTORY_ID}.retired"
    temp_evidence = layout.temp / f"{CRASH_STAGE_ID}.tmp"
    retired.write_bytes(
        canonical_pdf_export_history_bytes(PdfExportHistoryDocument((second_record,)))
    )
    temp_evidence.write_bytes(b"synthetic-divergent-history-evidence")

    startup = build_windows_startup_recovery_service(manager).recover_startup()

    case = startup.cases[0]
    assert startup.status is StartupRecoveryStatus.ATTENTION_REQUIRED
    assert case.issues == (RecoveryIssueCode.EXPORT_RECOVERY_FAILED,)
    assert retired.exists()
    assert temp_evidence.read_bytes() == b"synthetic-divergent-history-evidence"


def test_malformed_retirement_owner_is_unresolved_and_preserves_all_temp(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    layout = manager.layout(CASE_ID)
    malformed = layout.temp / "not-a-transaction.retired"
    temp_evidence = layout.temp / f"{CRASH_STAGE_ID}.tmp"
    malformed.write_bytes(b"synthetic-malformed-retirement-owner")
    temp_evidence.write_bytes(b"synthetic-malformed-owner-evidence")

    startup = build_windows_startup_recovery_service(manager).recover_startup()

    case = startup.cases[0]
    assert startup.status is StartupRecoveryStatus.ATTENTION_REQUIRED
    assert case.issues == (RecoveryIssueCode.EXPORT_RECOVERY_FAILED,)
    assert malformed.read_bytes() == b"synthetic-malformed-retirement-owner"
    assert temp_evidence.read_bytes() == b"synthetic-malformed-owner-evidence"


def test_multiple_retirements_are_ambiguous_and_preserve_all_temp(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    layout = manager.layout(CASE_ID)
    manifests = WindowsManifestRepository(manager)
    history_repository = WindowsPdfExportHistoryRepository(manager)
    history = PdfExportHistoryService(history_repository, manifests)
    receipt = _receipt()
    assert (
        history.retry_record_and_link(CASE_ID, receipt).status
        is PdfExportOutcomeStatus.COMMITTED_RECORDED
    )
    stable_bytes = layout.export_history.read_bytes()
    first_retired = layout.temp / f"{RETIRED_HISTORY_ID}.retired"
    second_retired = layout.temp / f"{SECOND_RETIRED_HISTORY_ID}.retired"
    unrelated_temp = layout.temp / f"{CRASH_STAGE_ID}.tmp"
    first_retired.write_bytes(stable_bytes)
    second_retired.write_bytes(stable_bytes)
    unrelated_temp.write_bytes(b"synthetic-ambiguous-temp-evidence")

    startup = build_windows_startup_recovery_service(manager).recover_startup()

    case = startup.cases[0]
    assert startup.status is StartupRecoveryStatus.ATTENTION_REQUIRED
    assert case.issues == (RecoveryIssueCode.EXPORT_RECOVERY_FAILED,)
    assert first_retired.read_bytes() == stable_bytes
    assert second_retired.read_bytes() == stable_bytes
    assert unrelated_temp.read_bytes() == b"synthetic-ambiguous-temp-evidence"


def test_missing_history_with_manifest_reference_requires_attention_and_preserves_temp(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    layout = manager.layout(CASE_ID)
    manifests = WindowsManifestRepository(manager)
    history_repository = WindowsPdfExportHistoryRepository(manager)
    history = PdfExportHistoryService(history_repository, manifests)
    receipt = _receipt()
    assert (
        history.retry_record_and_link(CASE_ID, receipt).status
        is PdfExportOutcomeStatus.COMMITTED_RECORDED
    )
    layout.export_history.unlink()
    temp_evidence = layout.temp / f"{CRASH_STAGE_ID}.tmp"
    temp_evidence.write_bytes(b"synthetic-unbound-history-stage")

    startup = build_windows_startup_recovery_service(manager).recover_startup()

    case = startup.cases[0]
    assert startup.status is StartupRecoveryStatus.ATTENTION_REQUIRED
    assert case.status is CaseRecoveryStatus.CURRENT
    assert case.issues == (RecoveryIssueCode.EXPORT_RECOVERY_FAILED,)
    assert temp_evidence.read_bytes() == b"synthetic-unbound-history-stage"
    assert not layout.export_history.exists()


def test_history_reference_mismatch_requires_attention_without_retry_inventory(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    layout = manager.layout(CASE_ID)
    manifests = WindowsManifestRepository(manager)
    history_repository = WindowsPdfExportHistoryRepository(manager)
    history = PdfExportHistoryService(history_repository, manifests)
    first_receipt = _receipt()
    assert (
        history.retry_record_and_link(CASE_ID, first_receipt).status
        is PdfExportOutcomeStatus.COMMITTED_RECORDED
    )
    mismatched_receipt = _receipt(
        export_id=SECOND_EXPORT_ID,
        output_sha256="d" * 64,
        recorded_at=RECORDED_AT.replace(second=57),
    )
    mismatched_record = decode_pdf_export_history_record(mismatched_receipt.record_payload)
    mismatched_bytes = canonical_pdf_export_history_bytes(
        PdfExportHistoryDocument((mismatched_record,))
    )
    layout.export_history.write_bytes(mismatched_bytes)
    temp_evidence = layout.temp / f"{CRASH_STAGE_ID}.tmp"
    temp_evidence.write_bytes(b"synthetic-reference-mismatch-evidence")

    startup = build_windows_startup_recovery_service(manager).recover_startup()

    case = startup.cases[0]
    assert startup.status is StartupRecoveryStatus.ATTENTION_REQUIRED
    assert case.issues == (RecoveryIssueCode.EXPORT_RECOVERY_FAILED,)
    assert layout.export_history.read_bytes() == mismatched_bytes
    assert temp_evidence.read_bytes() == b"synthetic-reference-mismatch-evidence"

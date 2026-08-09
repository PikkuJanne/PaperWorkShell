"""Windows restart evidence for protected PDF export retry receipts."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

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
    build_pdf_export_history_record_for_commit,
    build_pdf_export_history_retry_receipt,
)
from paperworkshell.domain.recovery import CaseRecoveryStatus
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.export_history import WindowsPdfExportHistoryRepository
from paperworkshell.storage.export_retry import WindowsPdfExportRetryRepository
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.recovery import WindowsStartupRecoveryStore
from paperworkshell.storage.workspace import (
    CaseWorkspaceManager,
    CleanupTarget,
)

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0113 retry persistence targets the supported Windows storage boundary",
)

CASE_ID = UUID("10000000-0000-4000-8000-000000000813")
SOURCE_ID = UUID("20000000-0000-4000-8000-000000000813")
PAGE_ID = UUID("30000000-0000-4000-8000-000000000813")
EXPORT_ID = UUID("40000000-0000-4000-8000-000000000813")
PREPARED_STAGE_ID = UUID("50000000-0000-4000-8000-000000000813")
COMMITTED_STAGE_ID = UUID("60000000-0000-4000-8000-000000000813")


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


def _receipt() -> PdfExportHistoryRetryReceipt:
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
        output_sha256="c" * 64,
        output_byte_size=1_234,
    )
    record = build_pdf_export_history_record_for_commit(
        plan,
        export_id=EXPORT_ID,
        report=report,
        recorded_at=datetime(2026, 8, 9, 12, 34, 56, 123456, tzinfo=UTC),
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

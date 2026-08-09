from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from paperworkshell.app.export_history import PdfExportRetryRecoveryService
from paperworkshell.domain.pdf_export import (
    PdfExportHistoryRetryReceipt,
    PdfExportOutcome,
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
from paperworkshell.domain.pdf_export_retry import (
    PdfExportRetryInventory,
    PreparedPdfExportRetry,
)
from paperworkshell.domain.recovery import RecoveryIssueCode

CASE_ID = UUID("10000000-0000-4000-8000-000000000714")
SOURCE_ID = UUID("20000000-0000-4000-8000-000000000714")
PAGE_ID = UUID("30000000-0000-4000-8000-000000000714")
EXPORT_ID = UUID("40000000-0000-4000-8000-000000000714")


def _receipt() -> PdfExportHistoryRetryReceipt:
    plan = PdfExportPlan(
        case_id=CASE_ID,
        manifest_revision=3,
        manifest_sha256="b" * 64,
        manifest_cas_token="retry_recovery_manifest_cas_0001",
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


def _recorded(receipt: PdfExportHistoryRetryReceipt) -> PdfExportOutcome:
    return PdfExportOutcome(
        export_id=receipt.export_id,
        status=PdfExportOutcomeStatus.COMMITTED_RECORDED,
        report=receipt.report,
        destination_reopened=True,
        history_retry=receipt,
    )


class _History:
    def __init__(self) -> None:
        self.proof: object = False
        self.outcome: object = _recorded(_receipt())
        self.proof_calls = 0
        self.retry_calls = 0

    def has_commit_proof(
        self,
        case_id: UUID,
        receipt: PdfExportHistoryRetryReceipt,
    ) -> bool:
        assert case_id == CASE_ID and receipt == _receipt()
        self.proof_calls += 1
        if isinstance(self.proof, BaseException):
            raise self.proof
        return cast(bool, self.proof)

    def retry_record_and_link(
        self,
        case_id: UUID,
        receipt: PdfExportHistoryRetryReceipt,
    ) -> PdfExportOutcome:
        assert case_id == CASE_ID and receipt == _receipt()
        self.retry_calls += 1
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return cast(PdfExportOutcome, self.outcome)


class _Retries:
    def __init__(self, inventory: PdfExportRetryInventory) -> None:
        self.inventory_value: object = inventory
        self.discard_result: object = True
        self.inventory_calls = 0
        self.committed_discards = 0
        self.prepared_discards = 0

    def prepare(
        self,
        receipt: PdfExportHistoryRetryReceipt,
    ) -> PreparedPdfExportRetry:
        del receipt
        raise AssertionError("startup recovery must not prepare a new receipt")

    def inventory(self, case_id: UUID) -> PdfExportRetryInventory:
        assert case_id == CASE_ID
        self.inventory_calls += 1
        if isinstance(self.inventory_value, BaseException):
            raise self.inventory_value
        return cast(PdfExportRetryInventory, self.inventory_value)

    def discard_committed(
        self,
        case_id: UUID,
        export_id: UUID,
        record_sha256: str,
    ) -> bool:
        assert case_id == CASE_ID and export_id == EXPORT_ID
        assert record_sha256 == _receipt().record_sha256
        self.committed_discards += 1
        return cast(bool, self.discard_result)

    def discard_prepared(
        self,
        case_id: UUID,
        export_id: UUID,
        record_sha256: str,
    ) -> bool:
        assert case_id == CASE_ID and export_id == EXPORT_ID
        assert record_sha256 == _receipt().record_sha256
        self.prepared_discards += 1
        return cast(bool, self.discard_result)


def test_committed_receipt_replays_history_and_clears_exact_marker() -> None:
    receipt = _receipt()
    history = _History()
    retries = _Retries(PdfExportRetryInventory(committed=(receipt,)))

    result = PdfExportRetryRecoveryService(history, retries).recover_case(CASE_ID)

    assert result.repaired_entries == 1
    assert result.pending_entries == 0
    assert result.ambiguous_entries == 0
    assert history.proof_calls == 0
    assert history.retry_calls == 1
    assert retries.committed_discards == 1
    assert retries.prepared_discards == 0


def test_prepared_receipt_without_proof_remains_ambiguous_and_inert() -> None:
    receipt = _receipt()
    history = _History()
    retries = _Retries(PdfExportRetryInventory(prepared=(receipt,)))

    result = PdfExportRetryRecoveryService(history, retries).recover_case(CASE_ID)

    assert result.ambiguous_entries == 1
    assert result.pending_entries == 0
    assert result.repaired_entries == 0
    assert history.proof_calls == 1
    assert history.retry_calls == 0
    assert retries.prepared_discards == 0


def test_prepared_receipt_with_downstream_proof_repairs_idempotently() -> None:
    receipt = _receipt()
    history = _History()
    history.proof = True
    retries = _Retries(PdfExportRetryInventory(prepared=(receipt,)))

    result = PdfExportRetryRecoveryService(history, retries).recover_case(CASE_ID)

    assert result.repaired_entries == 1
    assert history.proof_calls == 1
    assert history.retry_calls == 1
    assert retries.prepared_discards == 1
    assert retries.committed_discards == 0


def test_truthy_non_boolean_proof_cannot_authorize_prepared_replay() -> None:
    receipt = _receipt()
    history = _History()
    history.proof = 1
    retries = _Retries(PdfExportRetryInventory(prepared=(receipt,)))

    result = PdfExportRetryRecoveryService(history, retries).recover_case(CASE_ID)

    assert result.pending_entries == 1
    assert result.ambiguous_entries == 0
    assert result.repaired_entries == 0
    assert history.proof_calls == 1
    assert history.retry_calls == 0
    assert retries.prepared_discards == 0


def test_truthy_non_boolean_marker_discard_is_reported_as_pending() -> None:
    receipt = _receipt()
    history = _History()
    retries = _Retries(PdfExportRetryInventory(committed=(receipt,)))
    retries.discard_result = 1

    result = PdfExportRetryRecoveryService(history, retries).recover_case(CASE_ID)

    assert result.pending_entries == 1
    assert result.repaired_entries == 0
    assert history.retry_calls == 1
    assert retries.committed_discards == 1


def test_invalid_inventory_blocks_all_history_mutation() -> None:
    receipt = _receipt()
    history = _History()
    retries = _Retries(
        PdfExportRetryInventory(
            invalid_export_ids=(receipt.export_id,),
            invalid_entries=1,
        )
    )

    result = PdfExportRetryRecoveryService(history, retries).recover_case(CASE_ID)

    assert result.invalid_entries == 1
    assert history.proof_calls == 0
    assert history.retry_calls == 0
    assert retries.committed_discards == 0
    assert retries.prepared_discards == 0


def test_invalid_sibling_does_not_block_independent_committed_repair() -> None:
    receipt = _receipt()
    history = _History()
    retries = _Retries(
        PdfExportRetryInventory(
            committed=(receipt,),
            invalid_export_ids=(PAGE_ID,),
            invalid_entries=1,
        )
    )

    result = PdfExportRetryRecoveryService(history, retries).recover_case(CASE_ID)

    assert result.repaired_entries == 1
    assert result.invalid_entries == 1
    assert history.retry_calls == 1
    assert retries.committed_discards == 1


def test_inventory_failure_is_not_mislabeled_as_an_invalid_receipt() -> None:
    receipt = _receipt()
    history = _History()
    retries = _Retries(PdfExportRetryInventory(committed=(receipt,)))

    for inventory_value in (RuntimeError("private inventory failure"), object()):
        retries.inventory_value = inventory_value
        result = PdfExportRetryRecoveryService(history, retries).recover_case(CASE_ID)

        assert result.recovery_failed
        assert result.invalid_entries == 0
        assert result.repaired_entries == 0
        assert result.issues == (RecoveryIssueCode.EXPORT_RECOVERY_FAILED,)
        assert history.proof_calls == 0
        assert history.retry_calls == 0
        assert retries.committed_discards == 0
        assert retries.prepared_discards == 0


def test_failed_history_or_marker_cleanup_stays_pending_for_next_startup() -> None:
    receipt = _receipt()
    history = _History()
    history.outcome = PdfExportOutcome(
        export_id=receipt.export_id,
        status=PdfExportOutcomeStatus.COMMITTED_HISTORY_PENDING,
        report=receipt.report,
        destination_reopened=True,
        history_retry=receipt,
    )
    retries = _Retries(PdfExportRetryInventory(committed=(receipt,)))

    pending = PdfExportRetryRecoveryService(history, retries).recover_case(CASE_ID)
    assert pending.pending_entries == 1
    assert retries.committed_discards == 0

    history.outcome = _recorded(receipt)
    retries.discard_result = False
    uncleared = PdfExportRetryRecoveryService(history, retries).recover_case(CASE_ID)
    assert uncleared.pending_entries == 1
    assert retries.committed_discards == 1

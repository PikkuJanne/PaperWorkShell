"""Record-first, retryable manifest linking for destination-committed PDF exports."""

from __future__ import annotations

import hmac
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol, cast, runtime_checkable
from uuid import UUID

from paperworkshell.domain.manifest_repository import (
    MAX_SAFE_MANIFEST_INTEGER,
    ManifestLoadResult,
    ManifestRepository,
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
    ManifestSaveResult,
    ManifestSaveStatus,
)
from paperworkshell.domain.pdf_export import (
    PdfExportHistoryRetryReceipt,
    PdfExportOutcome,
    PdfExportOutcomeStatus,
    PdfExportPlan,
)
from paperworkshell.domain.pdf_export_history import (
    MAX_PDF_EXPORT_HISTORY_RECORDS,
    PdfExportHistoryError,
    PdfExportHistoryErrorCode,
    PdfExportHistoryRecord,
    PdfExportHistoryRepository,
    PdfExportHistorySnapshot,
    canonical_pdf_export_history_record_bytes,
    decode_pdf_export_history_record,
    reject_pdf_export_history,
)
from paperworkshell.domain.pdf_export_retry import (
    PdfExportRetryInventory,
    PdfExportRetryRepository,
)
from paperworkshell.domain.recovery import ExportHistoryRecoveryResult

_MAX_MANIFEST_LINK_ATTEMPTS: Final = 3


class PdfExportHistoryService:
    """Persist a verified record before atomically linking it from the manifest."""

    __slots__ = ("_history", "_manifests")

    def __init__(
        self,
        history: PdfExportHistoryRepository,
        manifests: ManifestRepository,
    ) -> None:
        if not isinstance(history, PdfExportHistoryRepository) or not isinstance(
            manifests, ManifestRepository
        ):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MODEL_INVALID,
                "export_history.service",
            )
        self._history = history
        self._manifests = manifests

    def reserved_export_ids(self, case_id: UUID) -> frozenset[UUID]:
        """Return every durable history ID before a new destination commit."""

        if type(case_id) is not UUID or case_id.version != 4:
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MODEL_INVALID,
                "export_history.reserved_ids",
            )
        snapshot = self._history.load(case_id)
        if type(snapshot) is not PdfExportHistorySnapshot:
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.DOCUMENT_INVALID,
                "export_history.reserved_ids",
            )
        return frozenset(record.export_id for record in snapshot.document.records)

    def record_and_link(
        self,
        plan: PdfExportPlan,
        record: PdfExportHistoryRecord,
        outcome: PdfExportOutcome,
    ) -> PdfExportOutcome:
        """Append first, then CAS-link; a retry never recreates the exported PDF."""

        if (
            type(plan) is not PdfExportPlan
            or type(record) is not PdfExportHistoryRecord
            or type(outcome) is not PdfExportOutcome
        ):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MODEL_INVALID,
                "export_history.record_request",
            )
        if (
            record.manifest_revision != plan.manifest_revision
            or record.manifest_sha256 != plan.manifest_sha256
            or record.semantic_fingerprint != plan.semantic_fingerprint
        ):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.RECORD_MISMATCH,
                "export_history.plan_correlation",
            )
        case_id = plan.case_id
        self._require_outcome_correlation(record, outcome)
        self._require_receipt_correlation(case_id, record, outcome.history_retry)
        return self._append_and_link(case_id, record, outcome)

    def retry_record_and_link(
        self,
        case_id: UUID,
        receipt: PdfExportHistoryRetryReceipt,
    ) -> PdfExportOutcome:
        """Append and link one exact receipt without recreating its PDF."""

        if (
            type(case_id) is not UUID
            or case_id.version != 4
            or type(receipt) is not PdfExportHistoryRetryReceipt
            or receipt.case_id != case_id
        ):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MODEL_INVALID,
                "export_history.receipt_retry",
            )
        try:
            record = decode_pdf_export_history_record(receipt.record_payload)
        except PdfExportHistoryError:
            raise
        except Exception:
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.DOCUMENT_INVALID,
                "export_history.receipt_decode",
            )
        outcome = PdfExportOutcome(
            export_id=receipt.export_id,
            status=PdfExportOutcomeStatus.COMMITTED_HISTORY_PENDING,
            report=receipt.report,
            destination_reopened=True,
            history_retry=receipt,
        )
        self._require_outcome_correlation(record, outcome)
        self._require_receipt_correlation(case_id, record, receipt)
        return self._append_and_link(case_id, record, outcome)

    def has_commit_proof(
        self,
        case_id: UUID,
        receipt: PdfExportHistoryRetryReceipt,
    ) -> bool:
        """Return whether exact durable case state independently proves publication."""

        if (
            type(case_id) is not UUID
            or case_id.version != 4
            or type(receipt) is not PdfExportHistoryRetryReceipt
            or receipt.case_id != case_id
        ):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MODEL_INVALID,
                "export_history.commit_proof",
            )
        record = decode_pdf_export_history_record(receipt.record_payload)
        self._require_receipt_correlation(case_id, record, receipt)
        snapshot = self._history.load(case_id)
        if type(snapshot) is not PdfExportHistorySnapshot:
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.DOCUMENT_INVALID,
                "export_history.commit_proof_history",
            )
        for current in snapshot.document.records:
            if current.export_id != record.export_id:
                continue
            if hmac.compare_digest(current.sha256, record.sha256):
                return True
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.RECORD_MISMATCH,
                "export_history.commit_proof_history",
            )
        loaded = self._manifests.load(case_id)
        if type(loaded) is not ManifestLoadResult or loaded.snapshot.case_id != case_id:
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MANIFEST_LINK_FAILED,
                "export_history.commit_proof_manifest",
            )
        return _manifest_has_commit_proof(loaded, record)

    def _append_and_link(
        self,
        case_id: UUID,
        record: PdfExportHistoryRecord,
        outcome: PdfExportOutcome,
    ) -> PdfExportOutcome:
        try:
            self._history.append(case_id, record)
        except PdfExportHistoryError as error:
            if error.code in {
                PdfExportHistoryErrorCode.RECORD_MISMATCH,
                PdfExportHistoryErrorCode.DOCUMENT_INVALID,
                PdfExportHistoryErrorCode.VERSION_UNSUPPORTED,
            }:
                raise
            return _with_history_status(outcome, recorded=False)
        except Exception:
            return _with_history_status(outcome, recorded=False)
        return self._link_record(case_id, record, outcome)

    def retry_manifest_link(
        self,
        case_id: UUID,
        export_id: UUID,
        record_sha256: str,
        outcome: PdfExportOutcome,
    ) -> PdfExportOutcome:
        """Link one exact orphan record without invoking export or destination code."""

        if (
            type(case_id) is not UUID
            or case_id.version != 4
            or type(export_id) is not UUID
            or export_id.version != 4
            or type(outcome) is not PdfExportOutcome
            or outcome.export_id != export_id
        ):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MODEL_INVALID,
                "export_history.retry",
            )
        try:
            snapshot = self._history.load(case_id)
            record = snapshot.document.find(export_id, record_sha256)
        except PdfExportHistoryError as error:
            if error.code in {
                PdfExportHistoryErrorCode.RECORD_MISMATCH,
                PdfExportHistoryErrorCode.DOCUMENT_INVALID,
                PdfExportHistoryErrorCode.VERSION_UNSUPPORTED,
            }:
                raise
            return _with_history_status(outcome, recorded=False)
        except Exception:
            return _with_history_status(outcome, recorded=False)
        self._require_outcome_correlation(record, outcome)
        self._require_receipt_correlation(case_id, record, outcome.history_retry)
        return self._link_record(case_id, record, outcome)

    def _link_record(
        self,
        case_id: UUID,
        record: PdfExportHistoryRecord,
        outcome: PdfExportOutcome,
    ) -> PdfExportOutcome:
        for _attempt in range(_MAX_MANIFEST_LINK_ATTEMPTS):
            try:
                loaded = self._manifests.load(case_id)
                if type(loaded) is not ManifestLoadResult or loaded.snapshot.case_id != case_id:
                    return _with_history_status(outcome, recorded=False)
                candidate, already_linked = _manifest_candidate(loaded, record)
                if already_linked:
                    return _with_history_status(outcome, recorded=True)
                saved = self._manifests.save(
                    case_id,
                    candidate,
                    expected_revision=loaded.snapshot.revision,
                    expected_sha256=loaded.snapshot.sha256,
                    expected_cas_token=loaded.snapshot.cas_token,
                )
                if (
                    type(saved) is ManifestSaveResult
                    and saved.status is ManifestSaveStatus.SAVED
                    and _snapshot_has_reference(saved, record)
                ):
                    return _with_history_status(outcome, recorded=True)
                return _with_history_status(outcome, recorded=False)
            except ManifestRepositoryError as error:
                if error.code is ManifestRepositoryErrorCode.REVISION_CONFLICT:
                    continue
                return _with_history_status(outcome, recorded=False)
            except PdfExportHistoryError as error:
                if error.code is PdfExportHistoryErrorCode.RECORD_MISMATCH:
                    raise
                return _with_history_status(outcome, recorded=False)
            except Exception:
                return _with_history_status(outcome, recorded=False)
        return _with_history_status(outcome, recorded=False)

    @staticmethod
    def _require_outcome_correlation(
        record: PdfExportHistoryRecord,
        outcome: PdfExportOutcome,
    ) -> None:
        if (
            type(record) is not PdfExportHistoryRecord
            or type(outcome) is not PdfExportOutcome
            or record.export_id != outcome.export_id
            or record.semantic_fingerprint != outcome.report.semantic_fingerprint
            or record.output_sha256 != outcome.report.output_sha256
            or record.output_byte_size != outcome.report.output_byte_size
            or record.page_count != outcome.report.page_count
            or record.unsigned_derivative != outcome.report.unsigned_derivative
            or record.check_codes != tuple(check.code for check in outcome.checks)
        ):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.RECORD_MISMATCH,
                "export_history.outcome_correlation",
            )

    @staticmethod
    def _require_receipt_correlation(
        case_id: UUID,
        record: PdfExportHistoryRecord,
        receipt: PdfExportHistoryRetryReceipt,
    ) -> None:
        if (
            type(case_id) is not UUID
            or case_id.version != 4
            or type(record) is not PdfExportHistoryRecord
            or type(receipt) is not PdfExportHistoryRetryReceipt
            or receipt.case_id != case_id
            or receipt.export_id != record.export_id
            or receipt.record_sha256 != record.sha256
            or receipt.record_payload != canonical_pdf_export_history_record_bytes(record)
        ):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.RECORD_MISMATCH,
                "export_history.receipt_correlation",
            )


@runtime_checkable
class PdfExportHistoryRecoveryPort(Protocol):
    """History-only operations used during restart recovery."""

    def has_commit_proof(
        self,
        case_id: UUID,
        receipt: PdfExportHistoryRetryReceipt,
    ) -> bool: ...

    def retry_record_and_link(
        self,
        case_id: UUID,
        receipt: PdfExportHistoryRetryReceipt,
    ) -> PdfExportOutcome: ...


class PdfExportRetryRecoveryService:
    """Settle replayable receipts without invoking worker or destination code."""

    __slots__ = ("_history", "_retries")

    def __init__(
        self,
        history: PdfExportHistoryRecoveryPort,
        retries: PdfExportRetryRepository,
    ) -> None:
        if not isinstance(history, PdfExportHistoryRecoveryPort) or not isinstance(
            retries,
            PdfExportRetryRepository,
        ):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MODEL_INVALID,
                "export_retry.recovery_service",
            )
        self._history = history
        self._retries = retries

    def recover_case(self, case_id: UUID) -> ExportHistoryRecoveryResult:
        """Repair exact committed receipts; retain ambiguous preparations."""

        if type(case_id) is not UUID or case_id.version != 4:
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MODEL_INVALID,
                "export_retry.recovery_case",
            )
        try:
            inventory = self._retries.inventory(case_id)
        except Exception:
            return ExportHistoryRecoveryResult(recovery_failed=True)
        if type(inventory) is not PdfExportRetryInventory:
            return ExportHistoryRecoveryResult(recovery_failed=True)
        repaired = 0
        pending = 0
        ambiguous = 0
        for receipt in inventory.committed:
            if self._retry_and_clear(case_id, receipt, committed=True):
                repaired += 1
            else:
                pending += 1
        for receipt in inventory.prepared:
            try:
                proven: object = self._history.has_commit_proof(case_id, receipt)
            except Exception:
                pending += 1
                continue
            if type(proven) is not bool:
                pending += 1
                continue
            if not proven:
                ambiguous += 1
                continue
            if self._retry_and_clear(case_id, receipt, committed=False):
                repaired += 1
            else:
                pending += 1
        return ExportHistoryRecoveryResult(
            repaired_entries=repaired,
            pending_entries=pending,
            ambiguous_entries=ambiguous,
            invalid_entries=inventory.invalid_entries,
        )

    def _retry_and_clear(
        self,
        case_id: UUID,
        receipt: PdfExportHistoryRetryReceipt,
        *,
        committed: bool,
    ) -> bool:
        try:
            outcome = self._history.retry_record_and_link(case_id, receipt)
            if (
                type(outcome) is not PdfExportOutcome
                or outcome.status is not PdfExportOutcomeStatus.COMMITTED_RECORDED
                or outcome.export_id != receipt.export_id
                or outcome.history_retry != receipt
                or not outcome.destination_reopened
            ):
                return False
            if committed:
                discarded = self._retries.discard_committed(
                    case_id,
                    receipt.export_id,
                    receipt.record_sha256,
                )
            else:
                discarded = self._retries.discard_prepared(
                    case_id,
                    receipt.export_id,
                    receipt.record_sha256,
                )
            return discarded is True
        except Exception:
            return False


def _manifest_has_commit_proof(
    loaded: ManifestLoadResult,
    record: PdfExportHistoryRecord,
) -> bool:
    document = loaded.snapshot.document
    history = document.get("export_history")
    if type(history) is not dict or set(history) != {"storage_name", "entries"}:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.MANIFEST_LINK_FAILED,
            "export_history.commit_proof_shape",
        )
    if history.get("storage_name") != "export-history.json":
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.MANIFEST_LINK_FAILED,
            "export_history.commit_proof_storage",
        )
    entries = history.get("entries")
    if type(entries) is not list:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.MANIFEST_LINK_FAILED,
            "export_history.commit_proof_entries",
        )
    reference = record.manifest_reference
    for value in entries:
        if type(value) is not dict:
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MANIFEST_LINK_FAILED,
                "export_history.commit_proof_entry",
            )
        entry = cast(dict[str, object], value)
        if entry.get("export_id") != str(record.export_id):
            continue
        if entry == reference:
            return True
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.RECORD_MISMATCH,
            "export_history.commit_proof_conflict",
        )
    return False


def _manifest_candidate(
    loaded: ManifestLoadResult,
    record: PdfExportHistoryRecord,
) -> tuple[dict[str, object], bool]:
    snapshot = loaded.snapshot
    document = snapshot.document
    history = document.get("export_history")
    if type(history) is not dict or set(history) != {"storage_name", "entries"}:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.MANIFEST_LINK_FAILED,
            "export_history.manifest_shape",
        )
    if history.get("storage_name") != "export-history.json":
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.MANIFEST_LINK_FAILED,
            "export_history.manifest_storage",
        )
    entries = history.get("entries")
    if type(entries) is not list:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.MANIFEST_LINK_FAILED,
            "export_history.manifest_entries",
        )
    reference = record.manifest_reference
    for value in entries:
        if type(value) is not dict:
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MANIFEST_LINK_FAILED,
                "export_history.manifest_entry",
            )
        entry = cast(dict[str, object], value)
        if entry.get("export_id") != str(record.export_id):
            continue
        if entry == reference:
            return document, True
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.RECORD_MISMATCH,
            "export_history.manifest_conflict",
        )
    if len(entries) >= MAX_PDF_EXPORT_HISTORY_RECORDS:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.CAPACITY_EXCEEDED,
            "export_history.manifest_capacity",
        )
    if snapshot.revision >= MAX_SAFE_MANIFEST_INTEGER:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.CAPACITY_EXCEEDED,
            "export_history.manifest_revision",
        )
    entries.append(reference)
    current_updated = _parse_manifest_timestamp(document.get("updated_at"))
    recorded_at = _parse_manifest_timestamp(record.recorded_at)
    next_updated = max(recorded_at, current_updated + timedelta(microseconds=1))
    document["revision"] = snapshot.revision + 1
    document["updated_at"] = _render_manifest_timestamp(next_updated)
    return document, False


def _snapshot_has_reference(
    result: ManifestSaveResult,
    record: PdfExportHistoryRecord,
) -> bool:
    document = result.snapshot.document
    history = document.get("export_history")
    if type(history) is not dict:
        return False
    entries = history.get("entries")
    return type(entries) is list and record.manifest_reference in entries


def _with_history_status(outcome: PdfExportOutcome, *, recorded: bool) -> PdfExportOutcome:
    return PdfExportOutcome(
        export_id=outcome.export_id,
        status=(
            PdfExportOutcomeStatus.COMMITTED_RECORDED
            if recorded
            else PdfExportOutcomeStatus.COMMITTED_HISTORY_PENDING
        ),
        report=outcome.report,
        destination_reopened=True,
        history_retry=outcome.history_retry,
    )


def _parse_manifest_timestamp(value: object) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.MANIFEST_LINK_FAILED,
            "export_history.manifest_timestamp",
        )
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.MANIFEST_LINK_FAILED,
            "export_history.manifest_timestamp",
        )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.MANIFEST_LINK_FAILED,
            "export_history.manifest_timestamp",
        )
    return parsed.astimezone(UTC)


def _render_manifest_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")

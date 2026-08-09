"""Pure contracts for restart-safe PDF export-history retry receipts.

The protected case journal distinguishes a receipt prepared before an external
destination commit from one whose commit was confirmed.  Startup recovery may
replay only the latter.  Neither phase stores a destination path or filename.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol, cast, runtime_checkable
from uuid import UUID

from paperworkshell.domain.pdf_export import (
    PDF_EXPORT_HISTORY_RETRY_RECEIPT_VERSION,
    PdfExportCheck,
    PdfExportCheckCode,
    PdfExportHistoryRetryReceipt,
    PdfExportVerificationReport,
)
from paperworkshell.domain.pdf_export_history import (
    PdfExportHistoryError,
    PdfExportHistoryErrorCode,
    PdfExportHistoryRecord,
    canonical_pdf_export_history_record_bytes,
    decode_pdf_export_history_record,
    reject_pdf_export_history,
)

PDF_EXPORT_RETRY_JOURNAL_VERSION: Final = 1
MAX_PDF_EXPORT_RETRY_JOURNAL_BYTES: Final = 16 * 1024
MAX_PDF_EXPORT_RETRY_RECEIPTS: Final = 4_096
MAX_PDF_EXPORT_RETRY_JOURNAL_ENTRIES: Final = 2 * MAX_PDF_EXPORT_RETRY_RECEIPTS
MAX_PDF_EXPORT_RETRY_JSON_DEPTH: Final = 12
MAX_PDF_EXPORT_RETRY_JSON_NODES: Final = 256

_ENTRY_KEYS: Final = frozenset(
    {
        "case_id",
        "journal_version",
        "phase",
        "receipt_version",
        "record",
        "record_sha256",
    }
)
_COUNTED_CHECKS: Final = frozenset(
    {
        PdfExportCheckCode.PAGE_COUNT_MATCHED,
        PdfExportCheckCode.ALL_PAGES_RENDERED,
        PdfExportCheckCode.ORDER_ROTATION_MATCHED,
    }
)


class PdfExportRetryPhase(StrEnum):
    """Durable phase of one exact retry receipt."""

    PREPARED = "prepared"
    COMMITTED = "committed"


@dataclass(frozen=True, slots=True, repr=False)
class PdfExportRetryJournalEntry:
    """One strict path-free journal envelope."""

    phase: PdfExportRetryPhase
    receipt: PdfExportHistoryRetryReceipt
    journal_version: int = PDF_EXPORT_RETRY_JOURNAL_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.phase) is not PdfExportRetryPhase
            or type(self.receipt) is not PdfExportHistoryRetryReceipt
            or type(self.journal_version) is not int
            or self.journal_version != PDF_EXPORT_RETRY_JOURNAL_VERSION
        ):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MODEL_INVALID,
                "export_retry.entry",
            )
        _require_exact_receipt(self.receipt)

    def __repr__(self) -> str:
        return (
            "PdfExportRetryJournalEntry("
            f"phase={self.phase.value!r}, journal_version={self.journal_version}, "
            f"byte_size={len(self.receipt.record_payload)})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class PdfExportRetryInventory:
    """Bounded startup inventory with invalid values reduced to a count."""

    committed: tuple[PdfExportHistoryRetryReceipt, ...] = ()
    prepared: tuple[PdfExportHistoryRetryReceipt, ...] = ()
    invalid_export_ids: tuple[UUID, ...] = ()
    invalid_entries: int = 0

    def __post_init__(self) -> None:
        if (
            type(self.committed) is not tuple
            or type(self.prepared) is not tuple
            or type(self.invalid_export_ids) is not tuple
            or len(self.committed) > MAX_PDF_EXPORT_RETRY_RECEIPTS
            or len(self.prepared) > MAX_PDF_EXPORT_RETRY_RECEIPTS
            or any(type(value) is not PdfExportHistoryRetryReceipt for value in self.committed)
            or any(type(value) is not PdfExportHistoryRetryReceipt for value in self.prepared)
            or any(not _is_uuid4(value) for value in self.invalid_export_ids)
            or type(self.invalid_entries) is not int
            or not 0 <= self.invalid_entries <= MAX_PDF_EXPORT_RETRY_JOURNAL_ENTRIES
        ):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MODEL_INVALID,
                "export_retry.inventory",
            )
        committed = tuple(sorted(self.committed, key=lambda item: str(item.export_id)))
        prepared = tuple(sorted(self.prepared, key=lambda item: str(item.export_id)))
        invalid_ids = tuple(sorted(self.invalid_export_ids, key=str))
        committed_ids = tuple(item.export_id for item in committed)
        prepared_ids = tuple(item.export_id for item in prepared)
        if (
            len(set(committed_ids)) != len(committed_ids)
            or len(set(prepared_ids)) != len(prepared_ids)
            or not set(committed_ids).isdisjoint(prepared_ids)
            or len(set(invalid_ids)) != len(invalid_ids)
            or len(invalid_ids) > self.invalid_entries
            or len(set((*committed_ids, *prepared_ids, *invalid_ids)))
            > MAX_PDF_EXPORT_RETRY_RECEIPTS
        ):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MODEL_INVALID,
                "export_retry.inventory_ids",
            )
        object.__setattr__(self, "committed", committed)
        object.__setattr__(self, "prepared", prepared)
        object.__setattr__(self, "invalid_export_ids", invalid_ids)

    @property
    def prepared_export_ids(self) -> tuple[UUID, ...]:
        """Return commit-ambiguous IDs without exposing receipt payloads."""

        return tuple(receipt.export_id for receipt in self.prepared)

    @property
    def reserved_export_ids(self) -> frozenset[UUID]:
        """Reserve both replayable and commit-uncertain identifiers."""

        return frozenset(
            (
                *self.prepared_export_ids,
                *self.invalid_export_ids,
                *(receipt.export_id for receipt in self.committed),
            )
        )

    def __repr__(self) -> str:
        return (
            "PdfExportRetryInventory("
            f"committed={len(self.committed)}, "
            f"prepared={len(self.prepared)}, "
            f"invalid_ids={len(self.invalid_export_ids)}, "
            f"invalid_entries={self.invalid_entries})"
        )


@runtime_checkable
class PreparedPdfExportRetry(Protocol):
    """Single-use durable receipt lease held across destination publication."""

    @property
    def receipt(self) -> PdfExportHistoryRetryReceipt: ...

    def mark_committed(self) -> bool:
        """Publish the replayable phase; false means the phase is uncertain."""

    def resolve(self) -> bool:
        """Remove a committed receipt after exact history repair."""

    def discard_prepared(self) -> bool:
        """Remove a preparation only when destination non-commit is certain."""

    def close(self) -> None:
        """Release authority while preserving any unresolved durable phase."""


@runtime_checkable
class PdfExportRetryRepository(Protocol):
    """Protected case-journal storage for exact retry receipts."""

    def prepare(
        self,
        receipt: PdfExportHistoryRetryReceipt,
    ) -> PreparedPdfExportRetry:
        """Durably prepare both phases before an external destination commit."""

    def inventory(self, case_id: UUID) -> PdfExportRetryInventory:
        """Return replayable receipts and commit-uncertain identifiers."""

    def discard_committed(
        self,
        case_id: UUID,
        export_id: UUID,
        record_sha256: str,
    ) -> bool:
        """Remove one exact replayable receipt after idempotent repair."""

    def discard_prepared(
        self,
        case_id: UUID,
        export_id: UUID,
        record_sha256: str,
    ) -> bool:
        """Remove one exact inert receipt only after independent commit proof."""


def canonical_pdf_export_retry_entry_bytes(
    entry: PdfExportRetryJournalEntry,
) -> bytes:
    """Encode one exact current-version protected journal entry."""

    if type(entry) is not PdfExportRetryJournalEntry:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.MODEL_INVALID,
            "export_retry.encode",
        )
    receipt = _require_exact_receipt(entry.receipt)
    record = decode_pdf_export_history_record(receipt.record_payload)
    document: dict[str, object] = {
        "case_id": str(receipt.case_id),
        "journal_version": entry.journal_version,
        "phase": entry.phase.value,
        "receipt_version": receipt.receipt_version,
        "record": record.to_document(),
        "record_sha256": receipt.record_sha256,
    }
    payload = _canonical_json_bytes(document)
    if len(payload) > MAX_PDF_EXPORT_RETRY_JOURNAL_BYTES:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.CAPACITY_EXCEEDED,
            "export_retry.bytes",
        )
    return payload


def decode_pdf_export_retry_entry(
    payload: bytes,
    *,
    expected_case_id: UUID | None = None,
    expected_export_id: UUID | None = None,
    expected_phase: PdfExportRetryPhase | None = None,
) -> PdfExportRetryJournalEntry:
    """Decode one bounded canonical journal entry and reconstruct its report."""

    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_PDF_EXPORT_RETRY_JOURNAL_BYTES:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_retry.payload",
        )
    try:
        loaded = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except PdfExportHistoryError:
        raise
    except Exception:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_retry.json",
        )
    _require_bounded_shape(loaded)
    if type(loaded) is not dict or set(loaded) != _ENTRY_KEYS:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_retry.shape",
        )
    document = cast(dict[str, object], loaded)
    version = document.get("journal_version")
    if type(version) is int and version != PDF_EXPORT_RETRY_JOURNAL_VERSION:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.VERSION_UNSUPPORTED,
            "export_retry.version",
        )
    if version != PDF_EXPORT_RETRY_JOURNAL_VERSION:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_retry.version",
        )
    receipt_version = document.get("receipt_version")
    if (
        type(receipt_version) is not int
        or receipt_version != PDF_EXPORT_HISTORY_RETRY_RECEIPT_VERSION
    ):
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.VERSION_UNSUPPORTED,
            "export_retry.receipt_version",
        )
    raw_phase = document.get("phase")
    try:
        case_id = UUID(cast(str, document.get("case_id")))
        if type(raw_phase) is not str:
            raise ValueError
        phase = PdfExportRetryPhase(raw_phase)
    except Exception:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_retry.values",
        )
    if not _is_uuid4(case_id) or str(case_id) != document.get("case_id"):
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_retry.case_id",
        )
    record_document = document.get("record")
    if type(record_document) is not dict:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_retry.record",
        )
    record_payload = _canonical_json_bytes(record_document) + b"\n"
    record = decode_pdf_export_history_record(record_payload)
    if document.get("record_sha256") != record.sha256:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.RECORD_MISMATCH,
            "export_retry.record_digest",
        )
    receipt = _receipt_from_record(case_id, record)
    entry = PdfExportRetryJournalEntry(phase=phase, receipt=receipt)
    if (
        (expected_case_id is not None and receipt.case_id != expected_case_id)
        or (expected_export_id is not None and receipt.export_id != expected_export_id)
        or (expected_phase is not None and phase is not expected_phase)
    ):
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.RECORD_MISMATCH,
            "export_retry.expected",
        )
    if canonical_pdf_export_retry_entry_bytes(entry) != payload:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_retry.canonical",
        )
    return entry


def _receipt_from_record(
    case_id: UUID,
    record: PdfExportHistoryRecord,
) -> PdfExportHistoryRetryReceipt:
    worker_codes = record.check_codes[:-1]
    checks = tuple(
        PdfExportCheck(
            code=code,
            page_count=record.page_count if code in _COUNTED_CHECKS else None,
        )
        for code in worker_codes
    )
    report = PdfExportVerificationReport(
        semantic_fingerprint=record.semantic_fingerprint,
        output_sha256=record.output_sha256,
        output_byte_size=record.output_byte_size,
        page_count=record.page_count,
        checks=checks,
        unsigned_derivative=record.unsigned_derivative,
    )
    payload = canonical_pdf_export_history_record_bytes(record)
    return PdfExportHistoryRetryReceipt(
        case_id=case_id,
        export_id=record.export_id,
        report=report,
        record_sha256=record.sha256,
        record_payload=payload,
    )


def _require_exact_receipt(
    receipt: PdfExportHistoryRetryReceipt,
) -> PdfExportHistoryRetryReceipt:
    if type(receipt) is not PdfExportHistoryRetryReceipt:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.MODEL_INVALID,
            "export_retry.receipt",
        )
    try:
        record = decode_pdf_export_history_record(receipt.record_payload)
        expected = _receipt_from_record(receipt.case_id, record)
    except PdfExportHistoryError:
        raise
    except Exception:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_retry.receipt",
        )
    if receipt != expected:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.RECORD_MISMATCH,
            "export_retry.receipt",
        )
    return receipt


def _is_uuid4(value: object) -> bool:
    return type(value) is UUID and value.version == 4


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except Exception:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_retry.json",
        )


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.DOCUMENT_INVALID,
                "export_retry.duplicate",
            )
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    reject_pdf_export_history(
        PdfExportHistoryErrorCode.DOCUMENT_INVALID,
        "export_retry.constant",
    )


def _require_bounded_shape(value: object) -> None:
    remaining = [MAX_PDF_EXPORT_RETRY_JSON_NODES]
    active: set[int] = set()

    def visit(current: object, depth: int) -> None:
        if depth > MAX_PDF_EXPORT_RETRY_JSON_DEPTH or remaining[0] <= 0:
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.DOCUMENT_INVALID,
                "export_retry.limits",
            )
        remaining[0] -= 1
        if current is None or type(current) in {bool, int, str}:
            return
        if type(current) is dict:
            identity = id(current)
            if identity in active:
                reject_pdf_export_history(
                    PdfExportHistoryErrorCode.DOCUMENT_INVALID,
                    "export_retry.limits",
                )
            active.add(identity)
            try:
                for key, member in current.items():
                    if type(key) is not str:
                        reject_pdf_export_history(
                            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
                            "export_retry.limits",
                        )
                    visit(member, depth + 1)
            finally:
                active.remove(identity)
            return
        if type(current) is list:
            for member in current:
                visit(member, depth + 1)
            return
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_retry.limits",
        )

    visit(value, 0)

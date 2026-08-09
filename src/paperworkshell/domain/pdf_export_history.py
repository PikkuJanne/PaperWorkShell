"""Pure, path-free contracts for verified PDF export history.

The authoritative case manifest stores only a reference to each record.  The
reference digest is the SHA-256 of :func:`canonical_pdf_export_history_record_bytes`.
Neither the record nor this module accepts a destination path or filename.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Never, Protocol, cast, runtime_checkable
from uuid import UUID

from paperworkshell.domain.manifest_repository import MAX_SAFE_MANIFEST_INTEGER
from paperworkshell.domain.pdf_export import (
    MAX_PDF_EXPORT_HISTORY_RETRY_RECORD_BYTES,
    MAX_PDF_EXPORT_PAGES,
    PdfExportCheckCode,
    PdfExportHistoryRetryReceipt,
    PdfExportOutcome,
    PdfExportPlan,
    PdfExportVerificationReport,
)

PDF_EXPORT_HISTORY_SCHEMA_VERSION: Final = 1
PDF_EXPORT_HISTORY_RECORD_VERSION: Final = 1
MAX_PDF_EXPORT_HISTORY_RECORDS: Final = 4_096
MAX_PDF_EXPORT_HISTORY_BYTES: Final = 8 * 1024 * 1024
MAX_PDF_EXPORT_HISTORY_CHECKS: Final = 8
MAX_PDF_EXPORT_HISTORY_JSON_DEPTH: Final = 16
MAX_PDF_EXPORT_HISTORY_JSON_NODES: Final = 150_000
MAX_PDF_EXPORT_HISTORY_JSON_STRING_BYTES: Final = 128
MAX_PDF_EXPORT_HISTORY_JSON_TOTAL_STRING_BYTES: Final = 3 * 1024 * 1024
MAX_PDF_EXPORT_HISTORY_JSON_TOKEN_BYTES: Final = 128

_CONTEXT_TOKEN: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")
_UTC_TIMESTAMP: Final = re.compile(
    r"(?:[0-9]{4})-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]\.[0-9]{6}Z\Z"
)
_RECORD_KEYS: Final = frozenset(
    {
        "export_id",
        "record_version",
        "recorded_at",
        "manifest_revision",
        "manifest_sha256",
        "semantic_fingerprint",
        "output_sha256",
        "output_byte_size",
        "page_count",
        "check_codes",
        "unsigned_derivative",
    }
)
_DOCUMENT_KEYS: Final = frozenset({"schema_version", "records"})
_BASE_CHECK_CODES: Final = (
    PdfExportCheckCode.PDF_REOPENED,
    PdfExportCheckCode.STRUCTURE_CLEAN,
    PdfExportCheckCode.PAGE_COUNT_MATCHED,
    PdfExportCheckCode.UNENCRYPTED,
    PdfExportCheckCode.ALL_PAGES_RENDERED,
    PdfExportCheckCode.ORDER_ROTATION_MATCHED,
)


class PdfExportHistoryErrorCode(StrEnum):
    """Closed registry of path-free history failures."""

    MODEL_INVALID = "PDF_EXPORT_HISTORY_MODEL_INVALID"
    DOCUMENT_INVALID = "PDF_EXPORT_HISTORY_DOCUMENT_INVALID"
    VERSION_UNSUPPORTED = "PDF_EXPORT_HISTORY_VERSION_UNSUPPORTED"
    CAPACITY_EXCEEDED = "PDF_EXPORT_HISTORY_CAPACITY_EXCEEDED"
    RECORD_MISMATCH = "PDF_EXPORT_HISTORY_RECORD_MISMATCH"
    STORAGE_FAILED = "PDF_EXPORT_HISTORY_STORAGE_FAILED"
    COMMIT_UNCERTAIN = "PDF_EXPORT_HISTORY_COMMIT_UNCERTAIN"
    MANIFEST_LINK_FAILED = "PDF_EXPORT_HISTORY_MANIFEST_LINK_FAILED"


_SAFE_MESSAGES: Final[dict[PdfExportHistoryErrorCode, str]] = {
    PdfExportHistoryErrorCode.MODEL_INVALID: (
        "PaperWorkShell received invalid export-history data."
    ),
    PdfExportHistoryErrorCode.DOCUMENT_INVALID: (
        "The case export history is invalid and was not changed."
    ),
    PdfExportHistoryErrorCode.VERSION_UNSUPPORTED: (
        "The case export history uses an unsupported version and was not changed."
    ),
    PdfExportHistoryErrorCode.CAPACITY_EXCEEDED: (
        "The case export history reached its configured safety limit."
    ),
    PdfExportHistoryErrorCode.RECORD_MISMATCH: (
        "The export identifier already refers to a different history record."
    ),
    PdfExportHistoryErrorCode.STORAGE_FAILED: (
        "PaperWorkShell could not save the export history safely."
    ),
    PdfExportHistoryErrorCode.COMMIT_UNCERTAIN: (
        "PaperWorkShell could not confirm whether the export history was saved."
    ),
    PdfExportHistoryErrorCode.MANIFEST_LINK_FAILED: (
        "The verified export is saved, but its case-history reference is still pending."
    ),
}


@dataclass(slots=True, repr=False)
class PdfExportHistoryError(ValueError):
    """Stable failure containing no path, filename, digest, or record value."""

    code: PdfExportHistoryErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not PdfExportHistoryErrorCode:
            self.code = PdfExportHistoryErrorCode.STORAGE_FAILED
        if type(self.context) is not str or _CONTEXT_TOKEN.fullmatch(self.context) is None:
            self.context = "export_history.repository"
        self.safe_message = _SAFE_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"PdfExportHistoryError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_pdf_export_history(code: PdfExportHistoryErrorCode, context: str) -> Never:
    """Raise one closed history failure without retaining raw exceptions."""

    error = PdfExportHistoryError(code=code, context=context)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


@dataclass(frozen=True, slots=True, repr=False)
class PdfExportHistoryRecord:
    """One immutable record for a destination-committed, verified derivative."""

    export_id: UUID
    recorded_at: str
    manifest_revision: int
    manifest_sha256: str
    semantic_fingerprint: str
    output_sha256: str
    output_byte_size: int
    page_count: int
    check_codes: tuple[PdfExportCheckCode, ...]
    unsigned_derivative: bool
    record_version: int = PDF_EXPORT_HISTORY_RECORD_VERSION

    def __post_init__(self) -> None:
        if (
            not _is_uuid4(self.export_id)
            or type(self.recorded_at) is not str
            or not _is_utc_timestamp(self.recorded_at)
            or type(self.manifest_revision) is not int
            or not 0 <= self.manifest_revision <= MAX_SAFE_MANIFEST_INTEGER
            or not _is_sha256(self.manifest_sha256)
            or not _is_sha256(self.semantic_fingerprint)
            or not _is_sha256(self.output_sha256)
            or type(self.output_byte_size) is not int
            or not 1 <= self.output_byte_size <= MAX_SAFE_MANIFEST_INTEGER
            or type(self.page_count) is not int
            or not 1 <= self.page_count <= MAX_PDF_EXPORT_PAGES
            or type(self.check_codes) is not tuple
            or not 1 <= len(self.check_codes) <= MAX_PDF_EXPORT_HISTORY_CHECKS
            or any(type(code) is not PdfExportCheckCode for code in self.check_codes)
            or len(set(self.check_codes)) != len(self.check_codes)
            or type(self.unsigned_derivative) is not bool
            or type(self.record_version) is not int
            or self.record_version != PDF_EXPORT_HISTORY_RECORD_VERSION
        ):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MODEL_INVALID,
                "export_history.record",
            )
        expected: tuple[PdfExportCheckCode, ...] = _BASE_CHECK_CODES
        if self.unsigned_derivative:
            expected += (PdfExportCheckCode.UNSIGNED_DERIVATIVE,)
        expected += (PdfExportCheckCode.OUTPUT_COMMIT_REOPENED,)
        if self.check_codes != expected:
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MODEL_INVALID,
                "export_history.checks",
            )

    def __repr__(self) -> str:
        return (
            "PdfExportHistoryRecord("
            f"record_version={self.record_version}, page_count={self.page_count}, "
            f"output_byte_size={self.output_byte_size}, "
            f"unsigned_derivative={self.unsigned_derivative})"
        )

    @property
    def sha256(self) -> str:
        """Return the canonical per-record digest used by the manifest reference."""

        return hashlib.sha256(canonical_pdf_export_history_record_bytes(self)).hexdigest()

    @property
    def manifest_reference(self) -> dict[str, object]:
        """Return the exact existing case-manifest v1 reference shape."""

        return {
            "export_id": str(self.export_id),
            "record_version": self.record_version,
            "sha256": self.sha256,
        }

    def to_document(self) -> dict[str, object]:
        """Return a new JSON-compatible, path-free record object."""

        return {
            "export_id": str(self.export_id),
            "record_version": self.record_version,
            "recorded_at": self.recorded_at,
            "manifest_revision": self.manifest_revision,
            "manifest_sha256": self.manifest_sha256,
            "semantic_fingerprint": self.semantic_fingerprint,
            "output_sha256": self.output_sha256,
            "output_byte_size": self.output_byte_size,
            "page_count": self.page_count,
            "check_codes": [code.value for code in self.check_codes],
            "unsigned_derivative": self.unsigned_derivative,
        }


@dataclass(frozen=True, slots=True, repr=False)
class PdfExportHistoryDocument:
    """Current version of the bounded case-internal history file."""

    records: tuple[PdfExportHistoryRecord, ...] = ()
    schema_version: int = PDF_EXPORT_HISTORY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != PDF_EXPORT_HISTORY_SCHEMA_VERSION
            or type(self.records) is not tuple
            or len(self.records) > MAX_PDF_EXPORT_HISTORY_RECORDS
            or any(type(record) is not PdfExportHistoryRecord for record in self.records)
        ):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MODEL_INVALID,
                "export_history.document",
            )
        identifiers = tuple(record.export_id for record in self.records)
        if len(set(identifiers)) != len(identifiers):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.RECORD_MISMATCH,
                "export_history.identifiers",
            )

    def __repr__(self) -> str:
        return (
            "PdfExportHistoryDocument("
            f"schema_version={self.schema_version}, record_count={len(self.records)})"
        )

    def append(
        self,
        record: PdfExportHistoryRecord,
    ) -> tuple[PdfExportHistoryDocument, bool]:
        """Append once; return ``False`` for an exact idempotent retry."""

        if type(record) is not PdfExportHistoryRecord:
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MODEL_INVALID,
                "export_history.append",
            )
        for current in self.records:
            if current.export_id != record.export_id:
                continue
            if hmac.compare_digest(current.sha256, record.sha256):
                return self, False
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.RECORD_MISMATCH,
                "export_history.append_conflict",
            )
        if len(self.records) >= MAX_PDF_EXPORT_HISTORY_RECORDS:
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.CAPACITY_EXCEEDED,
                "export_history.record_capacity",
            )
        return PdfExportHistoryDocument(records=(*self.records, record)), True

    def find(self, export_id: UUID, record_sha256: str) -> PdfExportHistoryRecord:
        """Return one exact orphan record for idempotent manifest-link retry."""

        if not _is_uuid4(export_id) or not _is_sha256(record_sha256):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MODEL_INVALID,
                "export_history.find",
            )
        for record in self.records:
            if record.export_id == export_id:
                if hmac.compare_digest(record.sha256, record_sha256):
                    return record
                break
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.RECORD_MISMATCH,
            "export_history.find_mismatch",
        )

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "records": [record.to_document() for record in self.records],
        }


class PdfExportHistoryAppendStatus(StrEnum):
    """Durable append outcome."""

    PUBLISHED = "published"
    ALREADY_PRESENT = "already_present"


@dataclass(frozen=True, slots=True, repr=False)
class PdfExportHistoryAppendResult:
    """Path-free result of one record publication or exact retry."""

    status: PdfExportHistoryAppendStatus
    export_id: UUID
    record_sha256: str
    record_count: int

    def __post_init__(self) -> None:
        if (
            type(self.status) is not PdfExportHistoryAppendStatus
            or not _is_uuid4(self.export_id)
            or not _is_sha256(self.record_sha256)
            or type(self.record_count) is not int
            or not 1 <= self.record_count <= MAX_PDF_EXPORT_HISTORY_RECORDS
        ):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MODEL_INVALID,
                "export_history.append_result",
            )

    def __repr__(self) -> str:
        return (
            "PdfExportHistoryAppendResult("
            f"status={self.status.value!r}, record_count={self.record_count})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class PdfExportHistorySnapshot:
    """One authenticated, path-free history observation."""

    document: PdfExportHistoryDocument
    sha256: str
    cas_token: str

    def __post_init__(self) -> None:
        if (
            type(self.document) is not PdfExportHistoryDocument
            or not _is_sha256(self.sha256)
            or not _is_sha256(self.cas_token)
        ):
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.MODEL_INVALID,
                "export_history.snapshot",
            )

    def __repr__(self) -> str:
        return f"PdfExportHistorySnapshot(record_count={len(self.document.records)})"


@runtime_checkable
class PdfExportHistoryRepository(Protocol):
    """Storage port for one case-scoped authoritative history file."""

    def load(self, case_id: UUID) -> PdfExportHistorySnapshot:
        """Load the current file, or the canonical empty document when absent."""

    def append(
        self,
        case_id: UUID,
        record: PdfExportHistoryRecord,
    ) -> PdfExportHistoryAppendResult:
        """Atomically append one new record or confirm one exact retry."""


def build_pdf_export_history_record(
    plan: PdfExportPlan,
    outcome: PdfExportOutcome,
    *,
    recorded_at: datetime,
) -> PdfExportHistoryRecord:
    """Bind a trusted UTC instant to one destination-reopened export outcome."""

    if type(plan) is not PdfExportPlan or type(outcome) is not PdfExportOutcome:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.MODEL_INVALID,
            "export_history.verified_outcome",
        )
    return build_pdf_export_history_record_for_commit(
        plan,
        export_id=outcome.export_id,
        report=outcome.report,
        recorded_at=recorded_at,
    )


def build_pdf_export_history_record_for_commit(
    plan: PdfExportPlan,
    *,
    export_id: UUID,
    report: PdfExportVerificationReport,
    recorded_at: datetime,
) -> PdfExportHistoryRecord:
    """Prepare the exact record before the no-replace destination commit."""

    utc_offset = recorded_at.utcoffset() if type(recorded_at) is datetime else None
    if (
        type(plan) is not PdfExportPlan
        or not _is_uuid4(export_id)
        or type(report) is not PdfExportVerificationReport
        or type(recorded_at) is not datetime
        or recorded_at.tzinfo is None
        or utc_offset is None
        or utc_offset.total_seconds() != 0
        or report.semantic_fingerprint != plan.semantic_fingerprint
        or report.page_count != plan.page_count
        or report.unsigned_derivative != plan.contains_signature_markers
    ):
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.MODEL_INVALID,
            "export_history.verified_commit",
        )
    return PdfExportHistoryRecord(
        export_id=export_id,
        recorded_at=_render_utc_timestamp(recorded_at),
        manifest_revision=plan.manifest_revision,
        manifest_sha256=plan.manifest_sha256,
        semantic_fingerprint=plan.semantic_fingerprint,
        output_sha256=report.output_sha256,
        output_byte_size=report.output_byte_size,
        page_count=report.page_count,
        check_codes=tuple(check.code for check in report.checks)
        + (PdfExportCheckCode.OUTPUT_COMMIT_REOPENED,),
        unsigned_derivative=report.unsigned_derivative,
    )


def build_pdf_export_history_retry_receipt(
    plan: PdfExportPlan,
    record: PdfExportHistoryRecord,
    report: PdfExportVerificationReport,
) -> PdfExportHistoryRetryReceipt:
    """Return one path-free receipt that can append this exact record later."""

    if (
        type(plan) is not PdfExportPlan
        or type(record) is not PdfExportHistoryRecord
        or type(report) is not PdfExportVerificationReport
        or record.manifest_revision != plan.manifest_revision
        or record.manifest_sha256 != plan.manifest_sha256
        or record.semantic_fingerprint != report.semantic_fingerprint
        or record.output_sha256 != report.output_sha256
        or record.output_byte_size != report.output_byte_size
        or record.page_count != report.page_count
        or record.unsigned_derivative != report.unsigned_derivative
    ):
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.MODEL_INVALID,
            "export_history.retry_receipt",
        )
    payload = canonical_pdf_export_history_record_bytes(record)
    return PdfExportHistoryRetryReceipt(
        case_id=plan.case_id,
        export_id=record.export_id,
        report=report,
        record_sha256=record.sha256,
        record_payload=payload,
    )


def canonical_pdf_export_history_record_bytes(record: PdfExportHistoryRecord) -> bytes:
    """Return the canonical bytes whose digest is stored in the case manifest."""

    if type(record) is not PdfExportHistoryRecord:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.MODEL_INVALID,
            "export_history.canonical_record",
        )
    payload = _canonical_json_bytes(record.to_document())
    if len(payload) > MAX_PDF_EXPORT_HISTORY_RETRY_RECORD_BYTES:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.CAPACITY_EXCEEDED,
            "export_history.record_bytes",
        )
    return payload


def canonical_pdf_export_history_bytes(document: PdfExportHistoryDocument) -> bytes:
    """Return bounded canonical bytes for the complete history file."""

    if type(document) is not PdfExportHistoryDocument:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.MODEL_INVALID,
            "export_history.canonical_document",
        )
    payload = _canonical_json_bytes(document.to_document())
    if len(payload) > MAX_PDF_EXPORT_HISTORY_BYTES:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.CAPACITY_EXCEEDED,
            "export_history.document_bytes",
        )
    return payload


def decode_pdf_export_history(payload: bytes) -> PdfExportHistoryDocument:
    """Decode one current, strict, bounded history document."""

    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_PDF_EXPORT_HISTORY_BYTES:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_history.payload",
        )
    _require_bounded_json_shape(payload)
    try:
        loaded = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except PdfExportHistoryError:
        raise
    except Exception:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_history.json",
        )
    if type(loaded) is not dict:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_history.object",
        )
    document = cast(dict[str, object], loaded)
    version = document.get("schema_version")
    if type(version) is int and version != PDF_EXPORT_HISTORY_SCHEMA_VERSION:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.VERSION_UNSUPPORTED,
            "export_history.version",
        )
    if version != PDF_EXPORT_HISTORY_SCHEMA_VERSION or set(document) != _DOCUMENT_KEYS:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_history.shape",
        )
    raw_records = document.get("records")
    if type(raw_records) is not list or len(raw_records) > MAX_PDF_EXPORT_HISTORY_RECORDS:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_history.records",
        )
    records = tuple(_record_from_document(value) for value in raw_records)
    result = PdfExportHistoryDocument(records=records)
    if canonical_pdf_export_history_bytes(result) != payload:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_history.canonical",
        )
    return result


def decode_pdf_export_history_record(payload: bytes) -> PdfExportHistoryRecord:
    """Decode one exact canonical record from a bounded retry receipt."""

    if (
        type(payload) is not bytes
        or not 1 <= len(payload) <= MAX_PDF_EXPORT_HISTORY_RETRY_RECORD_BYTES
    ):
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_history.record_payload",
        )
    _require_bounded_json_shape(payload)
    try:
        loaded = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except PdfExportHistoryError:
        raise
    except Exception:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_history.record_json",
        )
    record = _record_from_document(loaded)
    if canonical_pdf_export_history_record_bytes(record) != payload:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_history.record_canonical",
        )
    return record


def _require_bounded_json_shape(payload: bytes) -> None:
    """Bound JSON allocation work before the standard decoder materializes a tree."""

    depth = 0
    nodes = 0
    total_string_bytes = 0
    index = 0
    length = len(payload)
    whitespace = b" \t\r\n"
    token_delimiters = b' \t\r\n,:]}{["'

    while index < length:
        value = payload[index]
        if value > 0x7F:
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.DOCUMENT_INVALID,
                "export_history.json_limits",
            )
        if value in whitespace:
            index += 1
            continue
        if value == ord('"'):
            nodes += 1
            index += 1
            string_bytes = 0
            terminated = False
            while index < length:
                value = payload[index]
                if value > 0x7F:
                    reject_pdf_export_history(
                        PdfExportHistoryErrorCode.DOCUMENT_INVALID,
                        "export_history.json_limits",
                    )
                if value == ord('"'):
                    terminated = True
                    index += 1
                    break
                if value == ord("\\"):
                    if index + 1 >= length:
                        break
                    string_bytes += 2
                    index += 2
                else:
                    string_bytes += 1
                    index += 1
                if string_bytes > MAX_PDF_EXPORT_HISTORY_JSON_STRING_BYTES:
                    reject_pdf_export_history(
                        PdfExportHistoryErrorCode.DOCUMENT_INVALID,
                        "export_history.json_limits",
                    )
            if not terminated:
                reject_pdf_export_history(
                    PdfExportHistoryErrorCode.DOCUMENT_INVALID,
                    "export_history.json_limits",
                )
            total_string_bytes += string_bytes
            if total_string_bytes > MAX_PDF_EXPORT_HISTORY_JSON_TOTAL_STRING_BYTES:
                reject_pdf_export_history(
                    PdfExportHistoryErrorCode.DOCUMENT_INVALID,
                    "export_history.json_limits",
                )
        elif value in (ord("{"), ord("[")):
            nodes += 1
            depth += 1
            if depth > MAX_PDF_EXPORT_HISTORY_JSON_DEPTH:
                reject_pdf_export_history(
                    PdfExportHistoryErrorCode.DOCUMENT_INVALID,
                    "export_history.json_limits",
                )
            index += 1
        elif value in (ord("}"), ord("]")):
            depth -= 1
            if depth < 0:
                reject_pdf_export_history(
                    PdfExportHistoryErrorCode.DOCUMENT_INVALID,
                    "export_history.json_limits",
                )
            index += 1
        elif value in (ord(","), ord(":")):
            index += 1
        else:
            nodes += 1
            token_start = index
            while index < length and payload[index] not in token_delimiters:
                if payload[index] > 0x7F:
                    reject_pdf_export_history(
                        PdfExportHistoryErrorCode.DOCUMENT_INVALID,
                        "export_history.json_limits",
                    )
                index += 1
            if (
                index == token_start
                or index - token_start > MAX_PDF_EXPORT_HISTORY_JSON_TOKEN_BYTES
            ):
                reject_pdf_export_history(
                    PdfExportHistoryErrorCode.DOCUMENT_INVALID,
                    "export_history.json_limits",
                )
        if nodes > MAX_PDF_EXPORT_HISTORY_JSON_NODES:
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.DOCUMENT_INVALID,
                "export_history.json_limits",
            )
    if depth != 0:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_history.json_limits",
        )


def _record_from_document(value: object) -> PdfExportHistoryRecord:
    if type(value) is not dict:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_history.record_shape",
        )
    raw = cast(dict[str, object], value)
    version = raw.get("record_version")
    if type(version) is int and version != PDF_EXPORT_HISTORY_RECORD_VERSION:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.VERSION_UNSUPPORTED,
            "export_history.record_version",
        )
    if version != PDF_EXPORT_HISTORY_RECORD_VERSION or set(raw) != _RECORD_KEYS:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_history.record_shape",
        )
    raw_checks = raw.get("check_codes")
    if type(raw_checks) is not list or len(raw_checks) > MAX_PDF_EXPORT_HISTORY_CHECKS:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_history.record_checks",
        )
    try:
        export_id = UUID(cast(str, raw.get("export_id")))
        checks = tuple(PdfExportCheckCode(value) for value in raw_checks)
        record = PdfExportHistoryRecord(
            export_id=export_id,
            record_version=version,
            recorded_at=cast(str, raw.get("recorded_at")),
            manifest_revision=cast(int, raw.get("manifest_revision")),
            manifest_sha256=cast(str, raw.get("manifest_sha256")),
            semantic_fingerprint=cast(str, raw.get("semantic_fingerprint")),
            output_sha256=cast(str, raw.get("output_sha256")),
            output_byte_size=cast(int, raw.get("output_byte_size")),
            page_count=cast(int, raw.get("page_count")),
            check_codes=checks,
            unsigned_derivative=cast(bool, raw.get("unsigned_derivative")),
        )
    except PdfExportHistoryError:
        raise
    except Exception:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.DOCUMENT_INVALID,
            "export_history.record_value",
        )
    return record


def _canonical_json_bytes(value: Mapping[str, object]) -> bytes:
    try:
        return (
            json.dumps(
                dict(value),
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
            + b"\n"
        )
    except Exception:
        reject_pdf_export_history(
            PdfExportHistoryErrorCode.MODEL_INVALID,
            "export_history.canonical_json",
        )


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            reject_pdf_export_history(
                PdfExportHistoryErrorCode.DOCUMENT_INVALID,
                "export_history.duplicate_key",
            )
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> Never:
    reject_pdf_export_history(
        PdfExportHistoryErrorCode.DOCUMENT_INVALID,
        "export_history.json_number",
    )


def _render_utc_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _is_utc_timestamp(value: str) -> bool:
    if _UTC_TIMESTAMP.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256.fullmatch(value) is not None


def _is_uuid4(value: object) -> bool:
    return type(value) is UUID and value.version == 4 and str(value) == str(value).lower()

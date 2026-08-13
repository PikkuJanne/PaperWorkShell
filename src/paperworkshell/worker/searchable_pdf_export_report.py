"""Small correlated worker report for searchable mixed-PDF export."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Final, cast
from uuid import UUID

from paperworkshell.domain.manifest_repository import MAX_SAFE_MANIFEST_INTEGER
from paperworkshell.domain.ocr import (
    TRUSTED_OCR_ENGINE_PROFILE_SHA256,
    TRUSTED_OCRMYPDF_VERSION,
    TRUSTED_TESSERACT_VERSION,
)
from paperworkshell.domain.searchable_pdf_export import (
    SearchablePdfExportErrorCode,
    reject_searchable_pdf_export,
)
from paperworkshell.worker.pdf_export_report import (
    PdfExportWorkerReport,
    decode_pdf_export_worker_report,
    encode_pdf_export_worker_report,
)
from paperworkshell.worker.protocol.models import MAX_SEARCHABLE_PDF_EXPORT_STATUS_BYTES

SEARCHABLE_PDF_WORKER_REPORT_VERSION: Final = 1
MAX_SEARCHABLE_PDF_WORKER_REPORT_BYTES: Final = 4_096
_ROOT_MEMBERS: Final = frozenset(
    {
        "worker_report_version",
        "operation_id",
        "output_object_id",
        "status_object_id",
        "status_byte_size",
        "status_sha256",
        "ocrmypdf_version",
        "tesseract_version",
        "ocr_engine_profile_sha256",
        "base_report_b64",
    }
)


@dataclass(frozen=True, slots=True, repr=False)
class SearchablePdfWorkerReport:
    """Bind both staging sections and the closed engine profiles."""

    operation_id: UUID
    output_object_id: UUID
    status_object_id: UUID
    status_byte_size: int
    status_sha256: str = field(repr=False)
    base_report: PdfExportWorkerReport = field(repr=False)
    ocrmypdf_version: str = TRUSTED_OCRMYPDF_VERSION
    tesseract_version: str = TRUSTED_TESSERACT_VERSION
    ocr_engine_profile_sha256: str = field(
        default=TRUSTED_OCR_ENGINE_PROFILE_SHA256,
        repr=False,
    )
    worker_report_version: int = SEARCHABLE_PDF_WORKER_REPORT_VERSION

    def __post_init__(self) -> None:
        identifiers = (self.operation_id, self.output_object_id, self.status_object_id)
        if (
            any(type(value) is not UUID or value.version != 4 for value in identifiers)
            or len(set(identifiers)) != len(identifiers)
            or type(self.status_byte_size) is not int
            or not 1 <= self.status_byte_size <= MAX_SEARCHABLE_PDF_EXPORT_STATUS_BYTES
            or not _sha256(self.status_sha256)
            or type(self.base_report) is not PdfExportWorkerReport
            or self.base_report.operation_id != self.operation_id
            or self.base_report.output_object_id != self.output_object_id
            or self.ocrmypdf_version != TRUSTED_OCRMYPDF_VERSION
            or self.tesseract_version != TRUSTED_TESSERACT_VERSION
            or self.ocr_engine_profile_sha256 != TRUSTED_OCR_ENGINE_PROFILE_SHA256
            or type(self.worker_report_version) is not int
            or self.worker_report_version != SEARCHABLE_PDF_WORKER_REPORT_VERSION
        ):
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.REPORT_INVALID,
                "searchable.worker_report",
            )

    def __repr__(self) -> str:
        return (
            "SearchablePdfWorkerReport("
            f"status_byte_size={self.status_byte_size}, "
            f"worker_report_version={self.worker_report_version})"
        )


def encode_searchable_pdf_worker_report(value: SearchablePdfWorkerReport) -> bytes:
    """Encode one canonical report that remains below the result-pipe bound."""

    if type(value) is not SearchablePdfWorkerReport:
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.REPORT_INVALID,
            "searchable.worker_report_encode",
        )
    base = base64.urlsafe_b64encode(encode_pdf_export_worker_report(value.base_report)).decode(
        "ascii"
    )
    document = {
        "worker_report_version": value.worker_report_version,
        "operation_id": str(value.operation_id),
        "output_object_id": str(value.output_object_id),
        "status_object_id": str(value.status_object_id),
        "status_byte_size": value.status_byte_size,
        "status_sha256": value.status_sha256,
        "ocrmypdf_version": value.ocrmypdf_version,
        "tesseract_version": value.tesseract_version,
        "ocr_engine_profile_sha256": value.ocr_engine_profile_sha256,
        "base_report_b64": base,
    }
    try:
        payload = json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except Exception:
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.REPORT_INVALID,
            "searchable.worker_report_encode",
        )
    if not 1 <= len(payload) <= MAX_SEARCHABLE_PDF_WORKER_REPORT_BYTES:
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.LIMIT_EXCEEDED,
            "searchable.worker_report_size",
        )
    return payload


def decode_searchable_pdf_worker_report(payload: bytes) -> SearchablePdfWorkerReport:
    """Decode canonical bytes and re-run every engine and correlation invariant."""

    if (
        type(payload) is not bytes
        or not 1 <= len(payload) <= MAX_SEARCHABLE_PDF_WORKER_REPORT_BYTES
    ):
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.REPORT_INVALID,
            "searchable.worker_report_size",
        )
    try:
        raw = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_unique_object,
            parse_int=_bounded_integer,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
        root = _object(raw, _ROOT_MEMBERS)
        encoded_base = _string(root["base_report_b64"])
        base_payload = base64.b64decode(encoded_base, altchars=b"-_", validate=True)
        result = SearchablePdfWorkerReport(
            operation_id=_uuid(root["operation_id"]),
            output_object_id=_uuid(root["output_object_id"]),
            status_object_id=_uuid(root["status_object_id"]),
            status_byte_size=_integer(root["status_byte_size"]),
            status_sha256=_string(root["status_sha256"]),
            base_report=decode_pdf_export_worker_report(base_payload),
            ocrmypdf_version=_string(root["ocrmypdf_version"]),
            tesseract_version=_string(root["tesseract_version"]),
            ocr_engine_profile_sha256=_string(root["ocr_engine_profile_sha256"]),
            worker_report_version=_integer(root["worker_report_version"]),
        )
        if encode_searchable_pdf_worker_report(result) != payload:
            raise ValueError
        return result
    except Exception:
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.REPORT_INVALID,
            "searchable.worker_report_decode",
        )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise ValueError
        result[key] = value
    return result


def _bounded_integer(value: str) -> int:
    parsed = int(value)
    if not -MAX_SAFE_MANIFEST_INTEGER <= parsed <= MAX_SAFE_MANIFEST_INTEGER:
        raise ValueError
    return parsed


def _reject_number(_value: str) -> None:
    raise ValueError


def _object(value: object, members: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != members:
        raise ValueError
    return cast(dict[str, object], value)


def _integer(value: object) -> int:
    if type(value) is not int:
        raise ValueError
    return value


def _string(value: object) -> str:
    if type(value) is not str or len(value) > 6_000:
        raise ValueError
    return value


def _uuid(value: object) -> UUID:
    text = _string(value)
    parsed = UUID(text)
    if parsed.version != 4 or str(parsed) != text:
        raise ValueError
    return parsed


def _sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )

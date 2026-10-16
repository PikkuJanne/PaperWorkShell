"""Canonical bounded records for private page reconstruction, never export approval."""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode, reject_pdf_export
from paperworkshell.domain.redaction_reconstruction import (
    RedactionReconstructionReport,
    RedactionReconstructionRequest,
)
from paperworkshell.worker.protocol.models import MAX_PDF_EXPORT_PLAN_BYTES

MAX_RECONSTRUCTION_REPORT_BYTES = 4096


def _encode(value: object, maximum: int) -> bytes:
    try:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    except Exception:
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "reconstruction.wire")
    if not 1 <= len(payload) <= maximum:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "reconstruction.wire_size")
    return payload


def _pairs(values: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _decode(payload: bytes, maximum: int) -> dict[str, object]:
    if type(payload) is not bytes or not 1 <= len(payload) <= maximum:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "reconstruction.wire_size")
    try:
        value = json.loads(
            payload,
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        if type(value) is not dict or _encode(value, maximum) != payload:
            raise ValueError
        return value
    except PdfExportError:
        raise
    except Exception:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "reconstruction.wire")


def encode_reconstruction_plan(request: RedactionReconstructionRequest) -> bytes:
    if type(request) is not RedactionReconstructionRequest:
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "reconstruction.request")
    return _encode(request.to_record(), MAX_PDF_EXPORT_PLAN_BYTES)


def decode_reconstruction_plan(payload: bytes) -> RedactionReconstructionRequest:
    return RedactionReconstructionRequest.from_record(_decode(payload, MAX_PDF_EXPORT_PLAN_BYTES))


@dataclass(frozen=True, slots=True, repr=False)
class ReconstructionWorkerReport:
    operation_id: UUID
    output_object_id: UUID
    report: RedactionReconstructionReport


def encode_reconstruction_report(value: ReconstructionWorkerReport) -> bytes:
    if (
        type(value) is not ReconstructionWorkerReport
        or type(value.operation_id) is not UUID
        or value.operation_id.version != 4
        or type(value.output_object_id) is not UUID
        or value.output_object_id.version != 4
        or value.operation_id == value.output_object_id
        or type(value.report) is not RedactionReconstructionReport
    ):
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "reconstruction.report")
    return _encode(
        {
            "worker_report_version": 1,
            "operation_id": str(value.operation_id),
            "output_object_id": str(value.output_object_id),
            "report": value.report.to_record(),
        },
        MAX_RECONSTRUCTION_REPORT_BYTES,
    )


def decode_reconstruction_report(payload: bytes) -> ReconstructionWorkerReport:
    value = _decode(payload, MAX_RECONSTRUCTION_REPORT_BYTES)
    try:
        if (
            set(value) != {"worker_report_version", "operation_id", "output_object_id", "report"}
            or type(value["worker_report_version"]) is not int
            or value["worker_report_version"] != 1
        ):
            raise ValueError
        ids = tuple(UUID(str(value[key])) for key in ("operation_id", "output_object_id"))
        result = ReconstructionWorkerReport(
            ids[0], ids[1], RedactionReconstructionReport.from_record(value["report"])
        )
        if encode_reconstruction_report(result) != payload:
            raise ValueError
        return result
    except PdfExportError:
        raise
    except Exception:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "reconstruction.report")

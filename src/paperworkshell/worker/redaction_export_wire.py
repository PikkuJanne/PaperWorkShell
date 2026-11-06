"""Closed provenance chain for a privately prepared removal derivative.

Records are evidence, never a publication capability or independent approval.
"""

from __future__ import annotations

from uuid import UUID

from paperworkshell.domain.pdf_export import PdfExportErrorCode, reject_pdf_export
from paperworkshell.domain.redaction_export_candidate import RedactionExportWorkerReport
from paperworkshell.domain.redaction_reconstruction import (
    RedactionReconstructionReport,
    RedactionReconstructionRequest,
)
from paperworkshell.domain.redaction_sanitization import (
    RedactionSanitizationReport,
    RedactionSanitizationRequest,
)
from paperworkshell.domain.redaction_verification import (
    RedactionVerificationReport,
    RedactionVerificationRequest,
)
from paperworkshell.worker.protocol.models import MAX_PDF_EXPORT_PLAN_BYTES
from paperworkshell.worker.redaction_sanitization_wire import _decode, _encode

MAX_REDACTION_EXPORT_REPORT_BYTES = 16384


def encode_redaction_export_plan(request: RedactionReconstructionRequest) -> bytes:
    if type(request) is not RedactionReconstructionRequest:
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "redaction_export.request")
    return _encode(request.to_record(), MAX_PDF_EXPORT_PLAN_BYTES)


def decode_redaction_export_plan(payload: bytes) -> RedactionReconstructionRequest:
    return RedactionReconstructionRequest.from_record(_decode(payload, MAX_PDF_EXPORT_PLAN_BYTES))


def encode_redaction_export_report(report: RedactionExportWorkerReport) -> bytes:
    if type(report) is not RedactionExportWorkerReport:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "redaction_export.report")
    return _encode(report.to_record(), MAX_REDACTION_EXPORT_REPORT_BYTES)


def decode_redaction_export_report(payload: bytes) -> RedactionExportWorkerReport:
    value = _decode(payload, MAX_REDACTION_EXPORT_REPORT_BYTES)
    if (
        set(value)
        != {
            "worker_report_version",
            "operation_id",
            "output_object_id",
            "reconstruction",
            "sanitization_request",
            "sanitization",
            "verification_request",
            "verification",
        }
        or type(value["worker_report_version"]) is not int
        or value["worker_report_version"] != 1
    ):
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "redaction_export.report")
    try:
        result = RedactionExportWorkerReport(
            UUID(str(value["operation_id"])),
            UUID(str(value["output_object_id"])),
            RedactionReconstructionReport.from_record(value["reconstruction"]),
            RedactionSanitizationRequest.from_record(value["sanitization_request"]),
            RedactionSanitizationReport.from_record(value["sanitization"]),
            RedactionVerificationRequest.from_record(value["verification_request"]),
            RedactionVerificationReport.from_record(value["verification"]),
        )
    except ValueError, TypeError, AttributeError:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "redaction_export.report")
    if encode_redaction_export_report(result) != payload:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "redaction_export.report")
    return result

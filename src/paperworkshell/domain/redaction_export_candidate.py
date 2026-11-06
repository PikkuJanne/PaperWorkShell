"""Value-free provenance for a privately prepared removal derivative.

Evidence never grants publication authority or independent release approval.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from paperworkshell.domain.pdf_export import PdfExportErrorCode, reject_pdf_export
from paperworkshell.domain.redaction_reconstruction import (
    RedactionReconstructionReport,
    RedactionReconstructionRequest,
    validate_reconstruction_report,
)
from paperworkshell.domain.redaction_sanitization import (
    RedactionSanitizationReport,
    RedactionSanitizationRequest,
    validate_sanitization_report,
)
from paperworkshell.domain.redaction_verification import (
    RUNTIME_VERIFICATION_PROFILE,
    VERIFICATION_PROFILE,
    RedactionVerificationReport,
    RedactionVerificationRequest,
    validate_verification_report,
)


@dataclass(frozen=True, slots=True, repr=False)
class RedactionExportWorkerReport:
    operation_id: UUID
    output_object_id: UUID
    reconstruction: RedactionReconstructionReport
    sanitization_request: RedactionSanitizationRequest
    sanitization: RedactionSanitizationReport
    verification_request: RedactionVerificationRequest
    verification: RedactionVerificationReport

    @property
    def output_byte_size(self) -> int:
        return self.sanitization.output_byte_size

    @property
    def output_sha256(self) -> str:
        return self.sanitization.output_sha256

    def __post_init__(self) -> None:
        if (
            type(self.operation_id) is not UUID
            or self.operation_id.version != 4
            or type(self.output_object_id) is not UUID
            or self.output_object_id.version != 4
            or self.operation_id == self.output_object_id
            or type(self.reconstruction) is not RedactionReconstructionReport
            or type(self.sanitization_request) is not RedactionSanitizationRequest
            or type(self.sanitization) is not RedactionSanitizationReport
            or type(self.verification_request) is not RedactionVerificationRequest
            or type(self.verification) is not RedactionVerificationReport
        ):
            reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "redaction_export.report")
        self.reconstruction.__post_init__()
        validate_sanitization_report(self.sanitization_request, self.sanitization)
        validate_verification_report(self.verification_request, self.verification)
        if (
            self.sanitization_request.source_sha256 != self.reconstruction.output_sha256
            or self.sanitization_request.source_byte_size != self.reconstruction.output_byte_size
            or self.sanitization_request.source_page_count != 1
            or self.verification_request.reference_sha256 != self.reconstruction.output_sha256
            or self.verification_request.reference_byte_size != self.reconstruction.output_byte_size
            or self.verification_request.candidate_sha256 != self.output_sha256
            or self.verification_request.candidate_byte_size != self.output_byte_size
            or self.verification_request.intent_sha256 != self.reconstruction.request_sha256
            or self.verification_request.page_count != 1
            or self.verification_request.affected_pages != (0,)
            or self.verification_request.profile_id
            not in {VERIFICATION_PROFILE, RUNTIME_VERIFICATION_PROFILE}
        ):
            reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "redaction_export.chain")

    def to_record(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "worker_report_version": 1,
            "operation_id": str(self.operation_id),
            "output_object_id": str(self.output_object_id),
            "reconstruction": self.reconstruction.to_record(),
            "sanitization_request": self.sanitization_request.to_record(),
            "sanitization": self.sanitization.to_record(),
            "verification_request": self.verification_request.to_record(),
            "verification": self.verification.to_record(),
        }

    def __repr__(self) -> str:
        return "RedactionExportWorkerReport(unsigned_derivative=True)"


def validate_redaction_export_worker_report(
    request: RedactionReconstructionRequest, report: RedactionExportWorkerReport
) -> None:
    if (
        type(request) is not RedactionReconstructionRequest
        or type(report) is not RedactionExportWorkerReport
    ):
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "redaction_export.report")
    request.__post_init__()
    report.__post_init__()
    validate_reconstruction_report(request, report.reconstruction)
    if report.sanitization_request.case_id != request.case_id:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "redaction_export.case")

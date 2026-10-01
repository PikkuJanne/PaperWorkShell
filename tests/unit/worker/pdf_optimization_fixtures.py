"""Explicit optimizer double for worker transport tests with synthetic assemblers."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from pathlib import Path

from paperworkshell.domain.pdf_export import (
    OPTIMIZED_PDF_EXPORT_REPORT_VERSION,
    PdfExportError,
    PdfExportVerificationReport,
)
from paperworkshell.domain.pdf_optimization import (
    PdfOptimizationOutcome,
    PdfOptimizationProfile,
    PdfOptimizationReport,
)
from paperworkshell.pdf.export_adapter import PdfExportAdapterControl, PdfExportAdapterLimits
from paperworkshell.worker.pdf_optimization import PdfOptimizationSelection
from paperworkshell.worker.private_temp import WorkerPrivateTempStorage
from paperworkshell.worker.protocol.models import ResourceLimits


@dataclass(frozen=True)
class OptimizationCall:
    baseline_path: Path
    report: PdfExportVerificationReport
    resource_limits: ResourceLimits
    adapter_limits: PdfExportAdapterLimits
    reserved_temporary_bytes: int


@dataclass
class OptimizationStub:
    """Keep a fake baseline unless a transport test explicitly selects other bytes."""

    output: bytes | None = None
    failure: PdfExportError | None = None
    calls: list[OptimizationCall] = field(default_factory=list)

    def __call__(
        self,
        baseline_path: Path,
        report: PdfExportVerificationReport,
        *,
        operation_temp: Path,
        resource_limits: ResourceLimits,
        adapter_limits: PdfExportAdapterLimits,
        control: PdfExportAdapterControl,
        private_temp_storage: WorkerPrivateTempStorage,
        reserved_temporary_bytes: int = 0,
    ) -> PdfOptimizationSelection:
        assert baseline_path.parent == operation_temp
        assert baseline_path.stat().st_size == report.output_byte_size
        assert not control.cancellation_requested() and not control.deadline_expired()
        assert private_temp_storage is not None
        self.calls.append(
            OptimizationCall(
                baseline_path, report, resource_limits, adapter_limits, reserved_temporary_bytes
            )
        )
        if self.failure is not None:
            raise self.failure
        optimization = PdfOptimizationReport(
            profile=PdfOptimizationProfile.SAFE_OBJECT_STREAM_V1,
            outcome=(
                PdfOptimizationOutcome.NO_REDUCTION
                if self.output is None
                else PdfOptimizationOutcome.OPTIMIZED
            ),
            baseline_byte_size=report.output_byte_size,
            candidate_byte_size=None if self.output is None else len(self.output),
            selected_byte_size=report.output_byte_size if self.output is None else len(self.output),
            verified_page_count=0 if self.output is None else report.page_count,
        )
        if self.output is None:
            return PdfOptimizationSelection(
                baseline_path,
                replace(
                    report,
                    report_version=OPTIMIZED_PDF_EXPORT_REPORT_VERSION,
                    optimization=optimization,
                ),
            )
        selected = operation_temp / "optimized.pdf"
        with selected.open("xb") as stream:
            stream.write(self.output)
        return PdfOptimizationSelection(
            selected,
            replace(
                report,
                output_byte_size=len(self.output),
                output_sha256=hashlib.sha256(self.output).hexdigest(),
                report_version=OPTIMIZED_PDF_EXPORT_REPORT_VERSION,
                optimization=optimization,
            ),
        )

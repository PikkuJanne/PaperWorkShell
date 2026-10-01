"""Select a smaller, verified PDF inside one existing restricted worker job."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from paperworkshell.domain.pdf_export import (
    OPTIMIZED_PDF_EXPORT_REPORT_VERSION,
    PdfExportErrorCode,
    PdfExportVerificationReport,
    reject_pdf_export,
)
from paperworkshell.domain.pdf_optimization import (
    PdfOptimizationOutcome,
    PdfOptimizationProfile,
    PdfOptimizationReport,
)
from paperworkshell.pdf.export_adapter import PdfExportAdapterControl, PdfExportAdapterLimits
from paperworkshell.pdf.optimization_adapter import (
    PdfOptimizationAdapter,
    PdfOptimizationAdapterResult,
)
from paperworkshell.pdf.qpdf_checker import QpdfCommandChecker
from paperworkshell.worker.private_temp import (
    WorkerPrivateTempStorage,
    WorkerPrivateTempStorageError,
)
from paperworkshell.worker.protocol.models import ResourceLimits

_CHUNK_BYTES = 1024 * 1024
_MAXIMUM_TEMP_DEPTH = 64


class VerifiedPdfOptimizer(Protocol):
    """The worker's narrow, replaceable optimization adapter seam."""

    def optimize(
        self,
        baseline_path: Path,
        candidate_path: Path,
        *,
        baseline_report: PdfExportVerificationReport,
        limits: PdfExportAdapterLimits,
        control: PdfExportAdapterControl,
        maximum_candidate_bytes: int,
        maximum_semantic_bytes: int,
    ) -> PdfOptimizationAdapterResult: ...


@dataclass(frozen=True, slots=True, repr=False)
class PdfOptimizationSelection:
    """Private selected path and path-free facts to publish after worker exit."""

    path: Path = field(repr=False)
    report: PdfExportVerificationReport


def optimize_verified_pdf(
    baseline_path: Path,
    report: PdfExportVerificationReport,
    *,
    operation_temp: Path,
    resource_limits: ResourceLimits,
    adapter_limits: PdfExportAdapterLimits,
    control: PdfExportAdapterControl,
    private_temp_storage: WorkerPrivateTempStorage,
    reserved_temporary_bytes: int = 0,
    optimizer: VerifiedPdfOptimizer | None = None,
) -> PdfOptimizationSelection:
    """Try one candidate without replacing the verified baseline or raising budgets.

    Actual private disk extents, the transferred output section, and caller-owned
    status/raster sections share the existing operation temporary-storage quota.
    An attempt reserves the full smaller-than-baseline extent up front. If that
    cannot fit, keep the baseline and report that no candidate was attempted.
    """

    if (
        not isinstance(baseline_path, Path)
        or not isinstance(operation_temp, Path)
        or not operation_temp.is_absolute()
        or baseline_path.parent != operation_temp
        or type(report) is not PdfExportVerificationReport
        or report.optimization is not None
        or type(resource_limits) is not ResourceLimits
        or type(adapter_limits) is not PdfExportAdapterLimits
        or type(control) is not PdfExportAdapterControl
        or type(reserved_temporary_bytes) is not int
        or reserved_temporary_bytes < 0
    ):
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "optimization.worker_input")
    _checkpoint(control)
    _authenticate_baseline(baseline_path, report, control)
    used_bytes = _private_temp_bytes(
        operation_temp,
        maximum_objects=adapter_limits.inspection.maximum_objects,
        maximum_bytes=resource_limits.temporary_storage_bytes,
        control=control,
    )
    candidate_limit = report.output_byte_size - 1
    remaining = (
        resource_limits.temporary_storage_bytes
        - used_bytes
        - resource_limits.output_bytes
        - reserved_temporary_bytes
    )
    if remaining < 0:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "optimization.temp_reservation")
    if candidate_limit < 1 or remaining < candidate_limit:
        outcome = (
            PdfOptimizationOutcome.NO_REDUCTION
            if candidate_limit < 1
            else PdfOptimizationOutcome.SKIPPED_BUDGET
        )
        optimization = PdfOptimizationReport(
            profile=PdfOptimizationProfile.SAFE_OBJECT_STREAM_V1,
            outcome=outcome,
            baseline_byte_size=report.output_byte_size,
            candidate_byte_size=None,
            selected_byte_size=report.output_byte_size,
            verified_page_count=0,
        )
        return PdfOptimizationSelection(
            baseline_path,
            replace(
                report,
                report_version=OPTIMIZED_PDF_EXPORT_REPORT_VERSION,
                optimization=optimization,
            ),
        )

    try:
        private_temp_storage.require_capacity(operation_temp, candidate_limit)
    except WorkerPrivateTempStorageError:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "optimization.disk_capacity")
    candidate_path = operation_temp / f"{uuid4().hex}.pdf"
    if optimizer is None:
        configured_qpdf = os.environ.get("PWS_QPDF_EXECUTABLE")
        checker = (
            QpdfCommandChecker()
            if configured_qpdf is None
            else QpdfCommandChecker(executable=Path(configured_qpdf))
        )
        selected_optimizer: VerifiedPdfOptimizer = PdfOptimizationAdapter(qpdf_checker=checker)
    else:
        selected_optimizer = optimizer
    result = selected_optimizer.optimize(
        baseline_path,
        candidate_path,
        baseline_report=report,
        limits=adapter_limits,
        control=control,
        maximum_candidate_bytes=candidate_limit,
        # Reserve memory for native engines, graph bookkeeping and render buffers.
        # This is a stricter derivative of the operation's ResourcePolicy budget.
        maximum_semantic_bytes=max(1, resource_limits.memory_bytes // 4),
    )
    _checkpoint(control)
    _authenticate_baseline(baseline_path, report, control)
    if (
        type(result) is not PdfOptimizationAdapterResult
        or result.report.baseline_byte_size != report.output_byte_size
    ):
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "optimization.adapter_result")
    if result.report.outcome is PdfOptimizationOutcome.OPTIMIZED:
        if result.candidate_sha256 is None:
            reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "optimization.candidate_digest")
        selected_report = replace(
            report,
            output_sha256=result.candidate_sha256,
            output_byte_size=result.report.selected_byte_size,
            report_version=OPTIMIZED_PDF_EXPORT_REPORT_VERSION,
            optimization=result.report,
        )
        _authenticate_baseline(candidate_path, selected_report, control)
        return PdfOptimizationSelection(candidate_path, selected_report)
    if result.candidate_sha256 is not None or candidate_path.exists():
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "optimization.fallback_cleanup")
    return PdfOptimizationSelection(
        baseline_path,
        replace(
            report,
            report_version=OPTIMIZED_PDF_EXPORT_REPORT_VERSION,
            optimization=result.report,
        ),
    )


def _checkpoint(control: PdfExportAdapterControl) -> None:
    if control.cancellation_requested():
        reject_pdf_export(PdfExportErrorCode.CANCELLED, "optimization.cancelled")
    if control.deadline_expired():
        reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "optimization.deadline")


def _is_plain_file(value: os.stat_result) -> bool:
    return (
        stat.S_ISREG(value.st_mode)
        and value.st_nlink == 1
        and not getattr(value, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _authenticate_baseline(
    path: Path,
    report: PdfExportVerificationReport,
    control: PdfExportAdapterControl,
) -> None:
    try:
        before = path.lstat()
        if not _is_plain_file(before) or before.st_size != report.output_byte_size:
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "optimization.baseline")
        digest = hashlib.sha256()
        total = 0
        with path.open("rb") as source:
            descriptor = os.fstat(source.fileno())
            if (descriptor.st_dev, descriptor.st_ino) != (before.st_dev, before.st_ino):
                reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "optimization.baseline")
            while True:
                _checkpoint(control)
                block = source.read(_CHUNK_BYTES)
                if not block:
                    break
                total += len(block)
                if total > report.output_byte_size:
                    reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "optimization.baseline")
                digest.update(block)
            after_descriptor = os.fstat(source.fileno())
        after = path.lstat()
        if (
            total != report.output_byte_size
            or digest.hexdigest() != report.output_sha256
            or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            or after_descriptor.st_size != before.st_size
            or after_descriptor.st_mtime_ns != before.st_mtime_ns
            or not _is_plain_file(after)
        ):
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "optimization.baseline")
    except OSError:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "optimization.baseline_read")


def _private_temp_bytes(
    root: Path,
    *,
    maximum_objects: int,
    maximum_bytes: int,
    control: PdfExportAdapterControl,
) -> int:
    count = 0
    total = 0

    def visit(directory: Path, depth: int) -> None:
        nonlocal count, total
        _checkpoint(control)
        info = directory.lstat()
        if (
            depth > _MAXIMUM_TEMP_DEPTH
            or not stat.S_ISDIR(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "optimization.temp_tree")
        with os.scandir(directory) as entries:
            for entry in entries:
                _checkpoint(control)
                count += 1
                if count > maximum_objects:
                    reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "optimization.temp_tree")
                # Windows DirEntry.stat omits link counts and file identities.
                # Path.lstat obtains those facts before an extent is accepted.
                item = Path(entry.path).lstat()
                if stat.S_ISDIR(item.st_mode):
                    visit(Path(entry.path), depth + 1)
                elif _is_plain_file(item):
                    total += item.st_size
                    if total > maximum_bytes:
                        reject_pdf_export(
                            PdfExportErrorCode.LIMIT_EXCEEDED, "optimization.temp_bytes"
                        )
                else:
                    reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "optimization.temp_tree")

    try:
        visit(root, 0)
    except OSError:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "optimization.temp_inventory")
    return total

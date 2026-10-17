"""Handle-only private OCR operation producing one unverified masked page."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Protocol, cast
from uuid import UUID, uuid4

from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode, reject_pdf_export
from paperworkshell.domain.pdf_inspection import MAX_PDF_INSPECTION_OBJECTS
from paperworkshell.domain.redaction_ocr import (
    RedactionOcrReport,
    RedactionOcrRequest,
    validate_redaction_ocr_report,
)
from paperworkshell.pdf.redaction_ocr import RedactionOcrAdapter
from paperworkshell.pdf.redaction_reconstruction import RedactionReconstructionLimits
from paperworkshell.worker.operations import (
    OperationContext,
    resource_limited_operation_result,
    timed_out_operation_result,
)
from paperworkshell.worker.pdf_export_buffers import (
    read_pdf_export_plan_section,
    write_pdf_export_output_section,
)
from paperworkshell.worker.pdf_export_operation import (
    _MINIMUM_OBJECT_LIMIT,
    _OBJECTS_PER_PAGE_LIMIT,
    _absolute_deadline,
    _copy_and_verify_source,
    _materialize_worker_private_temp,
    _OwnedAuthorities,
    _require_active,
    _worker_private_operation_temp,
    _write_result_pipe,
)
from paperworkshell.worker.pdf_thumbnail_operation import _require_non_inheritable
from paperworkshell.worker.private_temp import (
    OsWorkerPrivateTempStorage,
    WorkerPrivateTempStorage,
    WorkerPrivateTempStorageError,
)
from paperworkshell.worker.protocol.models import (
    REDACTION_OCR_CAPABILITY,
    REDACTION_OCR_OUTPUT_CONTENT_KIND,
    CapabilityDescriptor,
    CaseObjectReference,
    ErrorCategory,
    ErrorCode,
    ErrorPayload,
    OperationOutcome,
    OperationProgressPayload,
    OperationResultPayload,
    OperationStartPayload,
    ProgressStage,
    ProgressUnit,
    ReferenceArea,
    RetryAction,
)
from paperworkshell.worker.protocol.redaction_ocr import (
    redaction_ocr_binding,
    validate_redaction_ocr_result,
)
from paperworkshell.worker.redaction_ocr_wire import (
    RedactionOcrWorkerReport,
    decode_redaction_ocr_plan,
    encode_redaction_ocr_report,
)


@dataclass(frozen=True, slots=True, repr=False)
class RedactionOcrOperationRequest:
    source_handle: int
    plan_handle: int
    output_handle: int
    result_handle: int
    output_object_id: UUID

    @property
    def handles(self) -> tuple[int, ...]:
        return (self.source_handle, self.plan_handle, self.output_handle, self.result_handle)


@dataclass(frozen=True, slots=True, repr=False)
class RedactionOcrControl:
    context: OperationContext

    def cancellation_requested(self) -> bool:
        return self.context.cancel_requested.is_set()

    def deadline_expired(self) -> bool:
        return self.context.deadline_expired()


class RedactionOcrRegenerator(Protocol):
    def regenerate(
        self,
        source: BinaryIO,
        output_path: Path,
        scratch_directory: Path,
        request: RedactionOcrRequest,
        limits: RedactionReconstructionLimits,
        control: RedactionOcrControl,
    ) -> RedactionOcrReport: ...


@dataclass(frozen=True, slots=True, repr=False)
class WindowsRedactionOcrOperationAdapter:
    regenerator: RedactionOcrRegenerator
    private_temp_storage: WorkerPrivateTempStorage = field(
        default_factory=OsWorkerPrivateTempStorage
    )

    def discard_handles(self, request: RedactionOcrOperationRequest) -> None:
        _OwnedAuthorities(set(request.handles)).close_all()

    def regenerate(
        self, context: OperationContext, request: RedactionOcrOperationRequest
    ) -> CaseObjectReference:
        import msvcrt
        import os

        authorities = _OwnedAuthorities(set(request.handles))
        try:
            if request != _request(context):
                reject_pdf_export(
                    PdfExportErrorCode.RESULT_INVALID, "redaction_ocr.request_binding"
                )
            deadline = _absolute_deadline(context)
            _require_active(context, absolute_deadline=deadline)
            plan_reference, source_reference = context.inputs
            payload = read_pdf_export_plan_section(
                authorities.transfer(request.plan_handle),
                byte_count=plan_reference.expected_byte_size or 0,
                expected_sha256=plan_reference.expected_sha256 or "",
            )
            intent = decode_redaction_ocr_plan(payload)
            if (
                intent.reconstruction.case_id != context.case_id
                or intent.reconstruction.source_id != source_reference.object_id
                or intent.reconstruction.source_sha256 != source_reference.expected_sha256
                or intent.reconstruction.source_byte_size != source_reference.expected_byte_size
                or intent.reconstruction.source_page_count > context.limits.page_count
            ):
                reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "redaction_ocr.source_binding")
            root = _materialize_worker_private_temp()
            self.private_temp_storage.require_capacity(root, context.limits.temporary_storage_bytes)
            with _worker_private_operation_temp(root) as scratch:
                source_handle = authorities.transfer(request.source_handle)
                descriptor: int | None = None
                try:
                    _require_non_inheritable(source_handle, context="redaction_ocr.source_handle")
                    descriptor = msvcrt.open_osfhandle(source_handle, os.O_RDONLY | os.O_BINARY)
                except Exception:
                    if descriptor is None:
                        _OwnedAuthorities({source_handle}).close_all()
                    raise
                with os.fdopen(descriptor, "rb", buffering=0) as original:
                    # Duplicated file handles share their cursor with the retained
                    # lease. Every retry authenticates the whole immutable source.
                    if original.seek(0) != 0:
                        reject_pdf_export(
                            PdfExportErrorCode.SOURCE_CHANGED, "redaction_ocr.source_seek"
                        )
                    staged, staged_path = self.private_temp_storage.open_named(
                        scratch, suffix=".pdf"
                    )
                    with staged:
                        _copy_and_verify_source(
                            context,
                            cast(BinaryIO, original),
                            staged,
                            expected_size=intent.reconstruction.source_byte_size,
                            expected_digest=intent.reconstruction.source_sha256,
                            reference=source_reference,
                            absolute_deadline=deadline,
                        )
                output_path = scratch / f"{uuid4().hex}.pdf"
                remaining = (
                    context.limits.temporary_storage_bytes
                    - context.limits.output_bytes
                    - len(payload)
                )
                raster_bytes = min(
                    context.limits.pixel_count * 4, context.limits.memory_bytes // 4, remaining // 3
                )
                if raster_bytes < 4:
                    reject_pdf_export(
                        PdfExportErrorCode.LIMIT_EXCEEDED, "redaction_ocr.raster_budget"
                    )
                limits = RedactionReconstructionLimits(
                    maximum_source_bytes=intent.reconstruction.source_byte_size,
                    maximum_pages=context.limits.page_count,
                    maximum_width_pixels=min(32768, raster_bytes // 4),
                    maximum_height_pixels=min(32768, raster_bytes // 4),
                    maximum_pixels=raster_bytes // 4,
                    maximum_raster_bytes=raster_bytes,
                    maximum_memory_bytes=context.limits.memory_bytes,
                    maximum_output_bytes=context.limits.output_bytes,
                    maximum_temporary_storage_bytes=remaining,
                    maximum_pdf_objects=min(
                        MAX_PDF_INSPECTION_OBJECTS,
                        max(
                            _MINIMUM_OBJECT_LIMIT,
                            context.limits.page_count * _OBJECTS_PER_PAGE_LIMIT,
                        ),
                    ),
                    wall_time_ms=context.limits.wall_time_ms,
                )
                with staged_path.open("rb", buffering=0) as source:
                    report = self.regenerator.regenerate(
                        cast(BinaryIO, source),
                        output_path,
                        scratch,
                        intent,
                        limits,
                        RedactionOcrControl(context),
                    )
                validate_redaction_ocr_report(intent, report)
                _require_active(context, absolute_deadline=deadline)
                # Authenticate the actual private candidate during bounded transfer.
                with output_path.open("rb", buffering=0) as candidate:
                    output = write_pdf_export_output_section(
                        authorities.transfer(request.output_handle),
                        cast(BinaryIO, candidate),
                        byte_count=report.output_byte_size,
                        expected_sha256=report.output_sha256,
                        capacity=context.limits.output_bytes,
                        checkpoint=lambda: _require_active(context, absolute_deadline=deadline),
                    )
                _write_result_pipe(
                    authorities.transfer(request.result_handle),
                    encode_redaction_ocr_report(
                        RedactionOcrWorkerReport(
                            context.operation_id, request.output_object_id, report
                        )
                    ),
                )
                _require_active(context, absolute_deadline=deadline)
                return CaseObjectReference(
                    ReferenceArea.OPERATION_STAGING,
                    request.output_object_id,
                    operation_id=context.operation_id,
                    content_kind=REDACTION_OCR_OUTPUT_CONTENT_KIND,
                    expected_byte_size=output.byte_size,
                    expected_sha256=output.sha256,
                )
        except PdfExportError:
            raise
        except WorkerPrivateTempStorageError, MemoryError, RecursionError:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "redaction_ocr.resources")
        except Exception:
            reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "redaction_ocr.operation")
        finally:
            authorities.close_all()


@dataclass(frozen=True, slots=True)
class RedactionOcrOperationHandler:
    adapter: WindowsRedactionOcrOperationAdapter

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return REDACTION_OCR_CAPABILITY

    def execute(self, context: OperationContext) -> OperationResultPayload:
        request = _request(context)
        context.report_progress(
            OperationProgressPayload(ProgressStage.PROCESSING, 0, 1, ProgressUnit.PAGES)
        )
        if context.cancel_requested.is_set() or context.deadline_expired():
            self.adapter.discard_handles(request)
            return (
                OperationResultPayload(OperationOutcome.CANCELLED, (), None)
                if context.cancel_requested.is_set()
                else timed_out_operation_result()
            )
        try:
            output = self.adapter.regenerate(context, request)
        except PdfExportError as error:
            if error.code is PdfExportErrorCode.CANCELLED:
                return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
            if error.code is PdfExportErrorCode.TIMED_OUT:
                return timed_out_operation_result()
            if error.code is PdfExportErrorCode.LIMIT_EXCEEDED:
                return resource_limited_operation_result()
            if error.code is PdfExportErrorCode.OCR_LANGUAGE_UNAVAILABLE:
                return OperationResultPayload(
                    OperationOutcome.FAILED,
                    (),
                    ErrorPayload(
                        ErrorCode.WORKER_OPERATION_FAILED,
                        ErrorCategory.DEPENDENCY,
                        RetryAction.USER_ACTION,
                        False,
                        (),
                    ),
                )
            raise
        if context.cancel_requested.is_set():
            return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
        if context.deadline_expired():
            return timed_out_operation_result()
        result = OperationResultPayload(OperationOutcome.SUCCEEDED, (output,), None)
        validate_redaction_ocr_result(
            result,
            operation_id=context.operation_id,
            output_object_id=request.output_object_id,
            maximum_output_bytes=context.limits.output_bytes,
        )
        context.report_progress(
            OperationProgressPayload(ProgressStage.PROCESSING, 1, 1, ProgressUnit.PAGES)
        )
        return result


def _request(context: OperationContext) -> RedactionOcrOperationRequest:
    payload = OperationStartPayload(
        REDACTION_OCR_CAPABILITY.capability_id,
        1,
        context.inputs,
        context.parameters,
        context.limits,
    )
    output_id = redaction_ocr_binding(payload)
    ids = (
        context.case_id,
        context.operation_id,
        *(item.object_id for item in context.inputs),
        output_id,
    )
    if len(set(ids)) != len(ids):
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "redaction_ocr.scope")
    values = {parameter.name: parameter.value for parameter in context.parameters}
    return RedactionOcrOperationRequest(
        cast(int, values["source_handle"]),
        cast(int, values["plan_handle"]),
        cast(int, values["output_handle"]),
        cast(int, values["result_handle"]),
        output_id,
    )


def build_redaction_ocr_operation_handler() -> RedactionOcrOperationHandler | None:
    # Reuse the existing exact engine version/runtime/language admission. Only
    # the regenerator is registered; the ordinary OCR operation is never exposed.
    from paperworkshell.ocr.ocrmypdf_adapter import OcrMyPdfAdapter
    from paperworkshell.worker.ocr_operation import (
        WindowsOcrProcessAdapter,
        build_ocr_process_operation_handler,
    )

    admitted = build_ocr_process_operation_handler()
    if (
        admitted is None
        or type(admitted.adapter) is not WindowsOcrProcessAdapter
        or type(admitted.adapter.candidate_processor) is not OcrMyPdfAdapter
    ):
        return None
    return RedactionOcrOperationHandler(
        WindowsRedactionOcrOperationAdapter(
            regenerator=RedactionOcrAdapter(ocr=admitted.adapter.candidate_processor)
        )
    )

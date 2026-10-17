"""Handle-only isolated operation producing one unverified sanitized derivative."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Protocol, cast
from uuid import UUID, uuid4

from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode, reject_pdf_export
from paperworkshell.domain.pdf_inspection import MAX_PDF_INSPECTION_OBJECTS
from paperworkshell.domain.redaction_sanitization import (
    RedactionSanitizationReport,
    RedactionSanitizationRequest,
    validate_sanitization_report,
)
from paperworkshell.pdf.redaction_reconstruction import RedactionReconstructionLimits
from paperworkshell.pdf.redaction_sanitization import PikepdfRedactionSanitizer
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
    REDACTION_SANITIZATION_CAPABILITY,
    REDACTION_SANITIZATION_OUTPUT_CONTENT_KIND,
    CapabilityDescriptor,
    CaseObjectReference,
    OperationOutcome,
    OperationProgressPayload,
    OperationResultPayload,
    OperationStartPayload,
    ProgressStage,
    ProgressUnit,
    ReferenceArea,
)
from paperworkshell.worker.protocol.redaction_sanitization import (
    sanitization_binding,
    validate_sanitization_result,
)
from paperworkshell.worker.redaction_sanitization_wire import (
    SanitizationWorkerReport,
    decode_sanitization_plan,
    encode_sanitization_report,
)


@dataclass(frozen=True, slots=True, repr=False)
class SanitizationOperationRequest:
    source_handle: int
    plan_handle: int
    output_handle: int
    result_handle: int
    output_object_id: UUID

    @property
    def handles(self) -> tuple[int, ...]:
        return (self.source_handle, self.plan_handle, self.output_handle, self.result_handle)


@dataclass(frozen=True, slots=True, repr=False)
class SanitizationControl:
    context: OperationContext

    def cancellation_requested(self) -> bool:
        return self.context.cancel_requested.is_set()

    def deadline_expired(self) -> bool:
        return self.context.deadline_expired()


class RedactionSanitizer(Protocol):
    def sanitize(
        self,
        source: BinaryIO,
        output_path: Path,
        scratch_directory: Path,
        request: RedactionSanitizationRequest,
        limits: RedactionReconstructionLimits,
        control: SanitizationControl,
    ) -> RedactionSanitizationReport: ...


@dataclass(frozen=True, slots=True, repr=False)
class WindowsSanitizationOperationAdapter:
    sanitizer: RedactionSanitizer = field(default_factory=PikepdfRedactionSanitizer)
    private_temp_storage: WorkerPrivateTempStorage = field(
        default_factory=OsWorkerPrivateTempStorage
    )

    def discard_handles(self, request: SanitizationOperationRequest) -> None:
        _OwnedAuthorities(set(request.handles)).close_all()

    def sanitize(
        self, context: OperationContext, request: SanitizationOperationRequest
    ) -> CaseObjectReference:
        import msvcrt
        import os

        authorities = _OwnedAuthorities(set(request.handles))
        try:
            if request != _request(context):
                reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "sanitization.request_binding")
            deadline = _absolute_deadline(context)
            _require_active(context, absolute_deadline=deadline)
            plan_reference, source_reference = context.inputs
            payload = read_pdf_export_plan_section(
                authorities.transfer(request.plan_handle),
                byte_count=plan_reference.expected_byte_size or 0,
                expected_sha256=plan_reference.expected_sha256 or "",
            )
            intent = decode_sanitization_plan(payload)
            if (
                intent.case_id != context.case_id
                or intent.source_id != source_reference.object_id
                or intent.source_sha256 != source_reference.expected_sha256
                or intent.source_byte_size != source_reference.expected_byte_size
                or intent.source_page_count > context.limits.page_count
            ):
                reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "sanitization.source_binding")
            root = _materialize_worker_private_temp()
            self.private_temp_storage.require_capacity(root, context.limits.temporary_storage_bytes)
            with _worker_private_operation_temp(root) as scratch:
                source_handle = authorities.transfer(request.source_handle)
                descriptor: int | None = None
                try:
                    _require_non_inheritable(source_handle, context="sanitization.source_handle")
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
                            PdfExportErrorCode.SOURCE_CHANGED, "sanitization.source_seek"
                        )
                    staged, staged_path = self.private_temp_storage.open_named(
                        scratch, suffix=".pdf"
                    )
                    with staged:
                        _copy_and_verify_source(
                            context,
                            cast(BinaryIO, original),
                            staged,
                            expected_size=intent.source_byte_size,
                            expected_digest=intent.source_sha256,
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
                        PdfExportErrorCode.LIMIT_EXCEEDED, "sanitization.raster_budget"
                    )
                limits = RedactionReconstructionLimits(
                    maximum_source_bytes=intent.source_byte_size,
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
                    report = self.sanitizer.sanitize(
                        cast(BinaryIO, source),
                        output_path,
                        scratch,
                        intent,
                        limits,
                        SanitizationControl(context),
                    )
                validate_sanitization_report(intent, report)
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
                    encode_sanitization_report(
                        SanitizationWorkerReport(
                            context.operation_id, request.output_object_id, report
                        )
                    ),
                )
                _require_active(context, absolute_deadline=deadline)
                return CaseObjectReference(
                    ReferenceArea.OPERATION_STAGING,
                    request.output_object_id,
                    operation_id=context.operation_id,
                    content_kind=REDACTION_SANITIZATION_OUTPUT_CONTENT_KIND,
                    expected_byte_size=output.byte_size,
                    expected_sha256=output.sha256,
                )
        except PdfExportError:
            raise
        except WorkerPrivateTempStorageError, MemoryError, RecursionError:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "sanitization.resources")
        except Exception:
            reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "sanitization.operation")
        finally:
            authorities.close_all()


@dataclass(frozen=True, slots=True)
class SanitizationOperationHandler:
    adapter: WindowsSanitizationOperationAdapter

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return REDACTION_SANITIZATION_CAPABILITY

    def execute(self, context: OperationContext) -> OperationResultPayload:
        request = _request(context)
        context.report_progress(
            OperationProgressPayload(ProgressStage.PROCESSING, 0, 1, ProgressUnit.STEPS)
        )
        if context.cancel_requested.is_set() or context.deadline_expired():
            self.adapter.discard_handles(request)
            return (
                OperationResultPayload(OperationOutcome.CANCELLED, (), None)
                if context.cancel_requested.is_set()
                else timed_out_operation_result()
            )
        try:
            output = self.adapter.sanitize(context, request)
        except PdfExportError as error:
            if error.code is PdfExportErrorCode.CANCELLED:
                return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
            if error.code is PdfExportErrorCode.TIMED_OUT:
                return timed_out_operation_result()
            if error.code is PdfExportErrorCode.LIMIT_EXCEEDED:
                return resource_limited_operation_result()
            raise
        if context.cancel_requested.is_set():
            return OperationResultPayload(OperationOutcome.CANCELLED, (), None)
        if context.deadline_expired():
            return timed_out_operation_result()
        result = OperationResultPayload(OperationOutcome.SUCCEEDED, (output,), None)
        validate_sanitization_result(
            result,
            operation_id=context.operation_id,
            output_object_id=request.output_object_id,
            maximum_output_bytes=context.limits.output_bytes,
        )
        context.report_progress(
            OperationProgressPayload(ProgressStage.PROCESSING, 1, 1, ProgressUnit.STEPS)
        )
        return result


def _request(context: OperationContext) -> SanitizationOperationRequest:
    payload = OperationStartPayload(
        REDACTION_SANITIZATION_CAPABILITY.capability_id,
        1,
        context.inputs,
        context.parameters,
        context.limits,
    )
    output_id = sanitization_binding(payload)
    ids = (
        context.case_id,
        context.operation_id,
        *(item.object_id for item in context.inputs),
        output_id,
    )
    if len(set(ids)) != len(ids):
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "sanitization.scope")
    values = {parameter.name: parameter.value for parameter in context.parameters}
    return SanitizationOperationRequest(
        cast(int, values["source_handle"]),
        cast(int, values["plan_handle"]),
        cast(int, values["output_handle"]),
        cast(int, values["result_handle"]),
        output_id,
    )


def build_sanitization_operation_handler() -> SanitizationOperationHandler:
    return SanitizationOperationHandler(WindowsSanitizationOperationAdapter())

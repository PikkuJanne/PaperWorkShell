"""One restricted-worker analysis or explicitly requested image stage."""

from __future__ import annotations

import hashlib
import io
import os
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID, uuid4

from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportSourceMediaType,
    PdfExportVerificationReport,
    reject_pdf_export,
)
from paperworkshell.domain.pdf_image_stages import (
    PdfImageStageRequest,
    validate_pdf_image_stage_report,
)
from paperworkshell.pdf.export_adapter import PdfExportAdapter, PdfExportAdapterControl
from paperworkshell.pdf.image_stage_adapter import (
    PdfImageStageAdapter,
    PdfImageStageAdapterLimits,
    PdfImageStageAdapterResult,
)
from paperworkshell.pdf.qpdf_checker import QpdfCommandChecker
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
    PdfExportCandidateAssembler,
    _absolute_deadline,
    _adapter_limits,
    _ContextMonotonic,
    _materialize_worker_private_temp,
    _OwnedAuthorities,
    _require_active,
    _stage_sources,
    _worker_private_operation_temp,
    _write_result_pipe,
)
from paperworkshell.worker.pdf_image_stage_wire import (
    PdfImageStageWorkerPlan,
    PdfImageStageWorkerResult,
    decode_pdf_image_stage_plan,
    encode_pdf_image_stage_evidence,
    encode_pdf_image_stage_result,
)
from paperworkshell.worker.pdf_optimization import (
    _authenticate_baseline,
    _private_temp_bytes,
    optimize_verified_pdf,
)
from paperworkshell.worker.private_temp import (
    OsWorkerPrivateTempStorage,
    WorkerPrivateTempStorage,
    WorkerPrivateTempStorageError,
)
from paperworkshell.worker.protocol.image_stage import (
    pdf_image_stage_binding,
    validate_pdf_image_stage_result,
)
from paperworkshell.worker.protocol.models import (
    PDF_IMAGE_STAGE_CAPABILITY,
    PDF_IMAGE_STAGE_EVIDENCE_CONTENT_KIND,
    PDF_IMAGE_STAGE_OUTPUT_CONTENT_KIND,
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


@dataclass(frozen=True, slots=True, repr=False)
class PdfImageStageOperationRequest:
    plan_handle: int
    output_handle: int
    evidence_handle: int
    result_handle: int
    output_object_id: UUID
    evidence_object_id: UUID
    evidence_capacity_bytes: int

    @property
    def handles(self) -> tuple[int, ...]:
        return (self.plan_handle, self.output_handle, self.evidence_handle, self.result_handle)


class ImageStageProcessor(Protocol):
    def process(
        self,
        baseline_path: Path,
        candidate_path: Path,
        *,
        baseline_report: PdfExportVerificationReport,
        request: PdfImageStageRequest,
        limits: PdfImageStageAdapterLimits,
        control: PdfExportAdapterControl,
        progress: Callable[[int, int], None],
    ) -> PdfImageStageAdapterResult: ...


@dataclass(frozen=True, slots=True, repr=False)
class WindowsPdfImageStageOperationAdapter:
    baseline_adapter: PdfExportCandidateAssembler = field(
        default_factory=PdfExportAdapter, repr=False
    )
    stage_adapter: ImageStageProcessor = field(default_factory=PdfImageStageAdapter, repr=False)
    private_temp_storage: WorkerPrivateTempStorage = field(
        default_factory=OsWorkerPrivateTempStorage, repr=False
    )

    def discard_handles(self, request: PdfImageStageOperationRequest) -> None:
        _OwnedAuthorities(set(request.handles)).close_all()

    def process(
        self, context: OperationContext, request: PdfImageStageOperationRequest
    ) -> tuple[CaseObjectReference, CaseObjectReference]:
        authorities = _OwnedAuthorities(set(request.handles))
        try:
            if type(request) is not PdfImageStageOperationRequest or request != _request(context):
                reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "image_stage.request_binding")
            absolute_deadline = _absolute_deadline(context)
            _require_active(context, absolute_deadline=absolute_deadline)
            plan_reference = context.inputs[0]
            plan_handle = authorities.transfer(request.plan_handle)
            payload = read_pdf_export_plan_section(
                plan_handle,
                byte_count=plan_reference.expected_byte_size or 0,
                expected_sha256=plan_reference.expected_sha256 or "",
            )
            worker_plan = decode_pdf_image_stage_plan(payload)
            base = worker_plan.base
            handles = tuple(source.worker_handle for source in base.sources)
            authorities.add_discovered(handles, already_consumed={plan_handle})
            if set(handles) & set(request.handles):
                reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "image_stage.handle_collision")
            references = _bind_sources(context, worker_plan)
            source_total = sum(source.source.byte_size for source in base.sources)
            # Parent-owned output/evidence sections remain live during baseline assembly.
            working_limits = replace(
                context.limits,
                temporary_storage_bytes=context.limits.temporary_storage_bytes
                - context.limits.output_bytes
                - request.evidence_capacity_bytes,
            )
            baseline_limits = _adapter_limits(
                replace(context, limits=working_limits), base.plan, source_total=source_total
            )
            control = PdfExportAdapterControl(
                deadline=absolute_deadline,
                cancellation_probe=context.cancel_requested.is_set,
                monotonic=_ContextMonotonic(context, absolute_deadline),
            )
            temp_root = _materialize_worker_private_temp()
            self.private_temp_storage.require_capacity(
                temp_root, context.limits.temporary_storage_bytes
            )
            with _worker_private_operation_temp(temp_root) as operation_temp:
                staged = _stage_sources(
                    context,
                    base,
                    references,
                    authorities,
                    private_temp_storage=self.private_temp_storage,
                    operation_temp=operation_temp,
                    absolute_deadline=absolute_deadline,
                )
                baseline_path = operation_temp / f"{uuid4().hex}.pdf"
                baseline_report = self.baseline_adapter.assemble_and_verify(
                    base.plan, staged, baseline_path, baseline_limits, control
                )
                selection = optimize_verified_pdf(
                    baseline_path,
                    baseline_report,
                    operation_temp=operation_temp,
                    resource_limits=context.limits,
                    adapter_limits=baseline_limits,
                    control=control,
                    private_temp_storage=self.private_temp_storage,
                    reserved_temporary_bytes=request.evidence_capacity_bytes,
                )
                baseline_path, baseline_report = selection.path, selection.report
                used = _private_temp_bytes(
                    operation_temp,
                    maximum_objects=baseline_limits.inspection.maximum_objects,
                    maximum_bytes=context.limits.temporary_storage_bytes,
                    control=control,
                )
                remaining = (
                    context.limits.temporary_storage_bytes
                    - used
                    - context.limits.output_bytes
                    - request.evidence_capacity_bytes
                )
                if remaining < 0:
                    reject_pdf_export(
                        PdfExportErrorCode.LIMIT_EXCEEDED, "image_stage.temp_reservation"
                    )
                candidate_limit = min(
                    baseline_report.output_byte_size - 1, context.limits.output_bytes, remaining
                )
                if worker_plan.request.profile is not None and candidate_limit > 0:
                    self.private_temp_storage.require_capacity(operation_temp, candidate_limit)
                memory = context.limits.memory_bytes // 2
                stage_limits = PdfImageStageAdapterLimits(
                    export=baseline_limits,
                    maximum_memory_bytes=memory,
                    maximum_temporary_bytes=remaining,
                    maximum_candidate_bytes=candidate_limit,
                    maximum_source_pixels=min(context.limits.pixel_count, memory // 4),
                )
                candidate_path = operation_temp / f"{uuid4().hex}.pdf"
                total_steps = 3 * base.plan.page_count + len(worker_plan.request.selected_asset_ids)
                completed_steps = 0

                def report_stage_progress(completed: int, total: int) -> None:
                    nonlocal completed_steps
                    _require_active(context, absolute_deadline=absolute_deadline)
                    if (
                        type(completed) is not int
                        or type(total) is not int
                        or total != total_steps
                        or not completed_steps <= completed <= total
                    ):
                        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "image_stage.progress")
                    if completed > completed_steps:
                        context.report_progress(
                            OperationProgressPayload(
                                ProgressStage.PROCESSING,
                                completed * context.limits.page_count // total,
                                context.limits.page_count,
                                ProgressUnit.PAGES,
                            )
                        )
                        completed_steps = completed

                result = self.stage_adapter.process(
                    baseline_path,
                    candidate_path,
                    baseline_report=baseline_report,
                    request=worker_plan.request,
                    limits=stage_limits,
                    control=control,
                    progress=report_stage_progress,
                )
                _require_active(context, absolute_deadline=absolute_deadline)
                _authenticate_baseline(baseline_path, baseline_report, control)
                if type(result) is not PdfImageStageAdapterResult:
                    reject_pdf_export(
                        PdfExportErrorCode.REPORT_INVALID, "image_stage.adapter_result"
                    )
                validate_pdf_image_stage_report(worker_plan.request, result.analysis, result.report)
                report = result.report
                if (
                    report.semantic_fingerprint != base.plan.semantic_fingerprint
                    or report.baseline_sha256 != baseline_report.output_sha256
                    or report.baseline_byte_size != baseline_report.output_byte_size
                    or report.page_count != base.plan.page_count
                    or result.analysis.baseline_sha256 != baseline_report.output_sha256
                ):
                    reject_pdf_export(
                        PdfExportErrorCode.REPORT_INVALID, "image_stage.baseline_binding"
                    )
                if result.candidate_sha256 is None:
                    if (
                        candidate_path.exists()
                        or report.output_sha256 != baseline_report.output_sha256
                        or report.output_byte_size != baseline_report.output_byte_size
                    ):
                        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "image_stage.fallback")
                    selected_path = baseline_path
                else:
                    if (
                        result.candidate_sha256 != report.output_sha256
                        or report.output_byte_size >= baseline_report.output_byte_size
                    ):
                        reject_pdf_export(
                            PdfExportErrorCode.REPORT_INVALID, "image_stage.candidate"
                        )
                    selected_path = candidate_path
                evidence = encode_pdf_image_stage_evidence(result.analysis, report)
                if len(evidence) > request.evidence_capacity_bytes:
                    reject_pdf_export(
                        PdfExportErrorCode.LIMIT_EXCEEDED, "image_stage.evidence_capacity"
                    )
                # The bounded section writer authenticates selected bytes while copying.
                with selected_path.open("rb", buffering=0) as source:
                    output = write_pdf_export_output_section(
                        authorities.transfer(request.output_handle),
                        source,
                        byte_count=report.output_byte_size,
                        expected_sha256=report.output_sha256,
                        capacity=context.limits.output_bytes,
                        checkpoint=lambda: _require_active(
                            context, absolute_deadline=absolute_deadline
                        ),
                    )
                evidence_sha = hashlib.sha256(evidence).hexdigest()
                with io.BytesIO(evidence) as evidence_source:
                    evidence_output = write_pdf_export_output_section(
                        authorities.transfer(request.evidence_handle),
                        evidence_source,
                        byte_count=len(evidence),
                        expected_sha256=evidence_sha,
                        capacity=request.evidence_capacity_bytes,
                        checkpoint=lambda: _require_active(
                            context, absolute_deadline=absolute_deadline
                        ),
                    )
                header = PdfImageStageWorkerResult(
                    context.operation_id,
                    request.output_object_id,
                    request.evidence_object_id,
                    baseline_report,
                    output.byte_size,
                    output.sha256,
                    evidence_output.byte_size,
                    evidence_output.sha256,
                )
                _write_result_pipe(
                    authorities.transfer(request.result_handle),
                    encode_pdf_image_stage_result(header),
                )
                _require_active(context, absolute_deadline=absolute_deadline)
                return (
                    CaseObjectReference(
                        ReferenceArea.OPERATION_STAGING,
                        request.output_object_id,
                        operation_id=context.operation_id,
                        content_kind=PDF_IMAGE_STAGE_OUTPUT_CONTENT_KIND,
                        expected_byte_size=output.byte_size,
                        expected_sha256=output.sha256,
                    ),
                    CaseObjectReference(
                        ReferenceArea.OPERATION_STAGING,
                        request.evidence_object_id,
                        operation_id=context.operation_id,
                        content_kind=PDF_IMAGE_STAGE_EVIDENCE_CONTENT_KIND,
                        expected_byte_size=evidence_output.byte_size,
                        expected_sha256=evidence_output.sha256,
                    ),
                )
        except PdfExportError:
            raise
        except WorkerPrivateTempStorageError, MemoryError, RecursionError:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "image_stage.resources")
        except OSError:
            reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "image_stage.io")
        except Exception:
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "image_stage.operation")
        finally:
            authorities.close_all()


def _bind_sources(
    context: OperationContext, worker_plan: PdfImageStageWorkerPlan
) -> dict[UUID, CaseObjectReference]:
    base = worker_plan.base
    if base.plan.case_id != context.case_id or base.plan.page_count > context.limits.page_count:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "image_stage.plan_binding")
    references = {reference.object_id: reference for reference in context.inputs[1:]}
    if len(references) != len(context.inputs) - 1 or set(references) != {
        source.source.source_id for source in base.sources
    }:
        reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "image_stage.sources")
    for source in base.sources:
        reference = references[source.source.source_id]
        if (
            reference.expected_byte_size != source.source.byte_size
            or reference.expected_sha256 != source.source.sha256
            or reference.content_kind
            != {
                PdfExportSourceMediaType.PDF: "pdf",
                PdfExportSourceMediaType.JPEG: "jpeg",
                PdfExportSourceMediaType.PNG: "png",
            }[source.source.media_type]
        ):
            reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "image_stage.source_binding")
    return references


@dataclass(frozen=True, slots=True)
class PdfImageStageOperationHandler:
    adapter: WindowsPdfImageStageOperationAdapter

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return PDF_IMAGE_STAGE_CAPABILITY

    def execute(self, context: OperationContext) -> OperationResultPayload:
        request = _request(context)
        context.report_progress(
            OperationProgressPayload(
                ProgressStage.PROCESSING, 0, context.limits.page_count, ProgressUnit.PAGES
            )
        )
        if context.cancel_requested.is_set() or context.deadline_expired():
            self.adapter.discard_handles(request)
            return (
                OperationResultPayload(OperationOutcome.CANCELLED, (), None)
                if context.cancel_requested.is_set()
                else timed_out_operation_result()
            )
        try:
            outputs = self.adapter.process(context, request)
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
        result = OperationResultPayload(OperationOutcome.SUCCEEDED, outputs, None)
        validate_pdf_image_stage_result(
            result,
            operation_id=context.operation_id,
            output_object_id=request.output_object_id,
            evidence_object_id=request.evidence_object_id,
            maximum_output_bytes=context.limits.output_bytes,
            maximum_evidence_bytes=request.evidence_capacity_bytes,
        )
        context.report_progress(
            OperationProgressPayload(
                ProgressStage.PROCESSING,
                context.limits.page_count,
                context.limits.page_count,
                ProgressUnit.PAGES,
            )
        )
        return result


def _request(context: OperationContext) -> PdfImageStageOperationRequest:
    """Validate the direct adapter port as well as the normal protocol dispatcher."""

    if type(context) is not OperationContext:
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "image_stage.operation_context")
    if context.inputs and context.inputs[0].operation_id is not None:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "image_stage.operation_scope")
    payload = OperationStartPayload(
        PDF_IMAGE_STAGE_CAPABILITY.capability_id,
        1,
        context.inputs,
        context.parameters,
        context.limits,
    )
    output_id, evidence_id, capacity = pdf_image_stage_binding(payload)
    if context.case_id == context.operation_id or {context.case_id, context.operation_id} & {
        output_id,
        evidence_id,
        *(item.object_id for item in context.inputs),
    }:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "image_stage.operation_scope")
    values = {parameter.name: parameter.value for parameter in context.parameters}
    return PdfImageStageOperationRequest(
        cast(int, values["plan_handle"]),
        cast(int, values["output_handle"]),
        cast(int, values["evidence_handle"]),
        cast(int, values["result_handle"]),
        output_id,
        evidence_id,
        capacity,
    )


def build_pdf_image_stage_operation_handler() -> PdfImageStageOperationHandler:
    configured = os.environ.get("PWS_QPDF_EXECUTABLE")
    checker = (
        QpdfCommandChecker()
        if configured is None
        else QpdfCommandChecker(executable=Path(configured))
    )
    return PdfImageStageOperationHandler(
        WindowsPdfImageStageOperationAdapter(
            baseline_adapter=PdfExportAdapter(qpdf_checker=checker),
            stage_adapter=PdfImageStageAdapter(qpdf_checker=checker),
        )
    )

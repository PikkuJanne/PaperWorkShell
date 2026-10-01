"""Handle-only broker for transient, explicitly requested PDF image stages."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field, replace
from typing import Self
from uuid import UUID, uuid4

from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportPlan,
    PdfExportProgress,
    PdfExportProgressPhase,
    PdfExportVerificationReport,
    reject_pdf_export,
)
from paperworkshell.domain.pdf_image_stages import (
    PdfImageAnalysis,
    PdfImageStageReport,
    PdfImageStageRequest,
    validate_pdf_image_stage_report,
)
from paperworkshell.domain.source_ingest import SourceIngestError
from paperworkshell.worker._windows_broker import WindowsResultPipe
from paperworkshell.worker.client.errors import WorkerClientError, WorkerClientErrorCode
from paperworkshell.worker.client.models import (
    WorkerClientSnapshot,
    WorkerLifecyclePhase,
    WorkerOperationResultSnapshot,
)
from paperworkshell.worker.client.process import BrokeredHandlePurpose
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.pdf_export_broker import (
    MAX_PDF_EXPORT_CANDIDATE_READ_BYTES,
    CancellationProbe,
    DeadlineProbe,
    OutputBufferFactory,
    PdfExportBrokerPolicy,
    PdfExportOutputBuffer,
    PdfExportProgressSink,
    PdfExportSourceBatchLease,
    PdfExportWorkerCoordinator,
    PlanBufferFactory,
    ResultPipeFactory,
    _cancellation_requested,
    _deadline_expired,
    _operation_limits,
    _raise_source_error,
    _report_progress,
    _require_active,
    _require_source_binding,
)
from paperworkshell.worker.pdf_export_buffers import (
    WindowsPdfExportOutputBuffer,
    WindowsPdfExportPlanBuffer,
)
from paperworkshell.worker.pdf_image_stage_wire import (
    decode_pdf_image_stage_evidence,
    decode_pdf_image_stage_result,
    encode_pdf_image_stage_plan,
)
from paperworkshell.worker.protocol.image_stage import (
    validate_pdf_image_stage_result,
    validate_pdf_image_stage_start,
)
from paperworkshell.worker.protocol.models import (
    MAX_PDF_IMAGE_STAGE_EVIDENCE_BYTES,
    PDF_IMAGE_STAGE_CAPABILITY,
    PDF_IMAGE_STAGE_PLAN_CONTENT_KIND,
    CancelReason,
    CaseObjectReference,
    ErrorCategory,
    OperationOutcome,
    OperationResultPayload,
    OperationStartPayload,
    ProgressStage,
    ProgressUnit,
    ReferenceArea,
    ResourceLimits,
    ScalarValueType,
    TaggedScalar,
)
from paperworkshell.worker.searchable_pdf_export_broker import (
    _authenticate_buffer,
    _require_output_buffer,
)


def _ignore_progress(_value: PdfExportProgress) -> None:
    pass


@dataclass(frozen=True, slots=True)
class PdfImageStageBrokerPolicy:
    limits: ResourceLimits
    evidence_capacity_bytes: int = MAX_PDF_IMAGE_STAGE_EVIDENCE_BYTES
    pump_interval_seconds: float = 0.050

    def __post_init__(self) -> None:
        PdfExportBrokerPolicy(self.limits, self.pump_interval_seconds)
        if (
            type(self.evidence_capacity_bytes) is not int
            or not 1
            <= self.evidence_capacity_bytes
            <= min(MAX_PDF_IMAGE_STAGE_EVIDENCE_BYTES, self.limits.memory_bytes // 8)
            or self.evidence_capacity_bytes + 2 * self.limits.output_bytes
            >= self.limits.temporary_storage_bytes
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "image_stage.broker_policy")


class VerifiedPdfImageStageCandidate(AbstractContextManager["VerifiedPdfImageStageCandidate"]):
    """Single-pass authenticated private bytes; no destination or history mutation."""

    def __init__(
        self,
        buffer: PdfExportOutputBuffer,
        analysis: PdfImageAnalysis,
        report: PdfImageStageReport,
        baseline_report: PdfExportVerificationReport,
    ) -> None:
        self._buffer = buffer
        self.analysis = analysis
        self.report = report
        self.baseline_report = baseline_report
        self._remaining = report.output_byte_size
        self._chunks = buffer.iter_chunks(
            self._remaining, chunk_bytes=MAX_PDF_EXPORT_CANDIDATE_READ_BYTES
        )
        self._pending = b""
        self._closed = False

    def __repr__(self) -> str:
        return f"VerifiedPdfImageStageCandidate(byte_size={self.report.output_byte_size}, page_count={self.report.page_count})"

    def __enter__(self) -> Self:
        if self._closed:
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "image_stage.candidate_closed")
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._buffer.close()
            self._closed = True
            self._pending = b""

    def read(self, size: int, /) -> bytes:
        if (
            self._closed
            or type(size) is not int
            or not 1 <= size <= MAX_PDF_EXPORT_CANDIDATE_READ_BYTES
        ):
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "image_stage.candidate_read")
        requested = min(size, self._remaining)
        parts: list[bytes] = []
        completed = 0
        while completed < requested:
            if not self._pending:
                self._pending = next(self._chunks, b"")
            if (
                type(self._pending) is not bytes
                or not self._pending
                or len(self._pending) > self._remaining - completed
            ):
                reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "image_stage.candidate_chunk")
            count = min(len(self._pending), requested - completed)
            parts.append(self._pending[:count])
            self._pending = self._pending[count:]
            completed += count
        self._remaining -= completed
        return b"".join(parts)


@dataclass(slots=True, repr=False)
class BrokeredPdfImageStageProcessor:
    _worker: PdfExportWorkerCoordinator
    _policy: PdfImageStageBrokerPolicy
    heavy_operations: HeavyOperationCoordinator
    pipe_factory: ResultPipeFactory = WindowsResultPipe.create
    plan_buffer_factory: PlanBufferFactory = WindowsPdfExportPlanBuffer.create
    output_buffer_factory: OutputBufferFactory = WindowsPdfExportOutputBuffer.create
    _id_factory: Callable[[], UUID] = uuid4
    _revoked: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if (
            type(self._policy) is not PdfImageStageBrokerPolicy
            or type(self.heavy_operations) is not HeavyOperationCoordinator
            or any(
                not callable(value)
                for value in (
                    self.pipe_factory,
                    self.plan_buffer_factory,
                    self.output_buffer_factory,
                    self._id_factory,
                )
            )
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "image_stage.broker_configuration")

    @property
    def revoked(self) -> bool:
        return self._revoked

    def _revoke_permanently(self) -> None:
        self._revoked = True

    def process(
        self,
        base_plan: PdfExportPlan,
        sources: PdfExportSourceBatchLease,
        request: PdfImageStageRequest,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: PdfExportProgressSink = _ignore_progress,
    ) -> VerifiedPdfImageStageCandidate:
        if self._revoked:
            reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "image_stage.composition_closed")
        failure: tuple[PdfExportErrorCode, str] | None = None
        try:
            if (
                type(base_plan) is not PdfExportPlan
                or type(request) is not PdfImageStageRequest
                or not callable(progress)
            ):
                reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "image_stage.request")
            if (
                request.semantic_fingerprint is not None
                and request.semantic_fingerprint != base_plan.semantic_fingerprint
            ):
                reject_pdf_export(PdfExportErrorCode.PLAN_STALE, "image_stage.plan_fingerprint")
            while True:
                _require_active(deadline, cancellation)
                lease = self.heavy_operations.try_acquire(self._policy.pump_interval_seconds)
                if lease is not None:
                    break
            with lease:
                return self._process(
                    base_plan,
                    sources,
                    request,
                    deadline=deadline,
                    cancellation=cancellation,
                    progress=progress,
                )
        except PdfExportError:
            raise
        except SourceIngestError as error:
            _raise_source_error(error)
        except WorkerClientError as error:
            failure = (
                PdfExportErrorCode.TIMED_OUT
                if error.code
                in {
                    WorkerClientErrorCode.HANDSHAKE_TIMED_OUT,
                    WorkerClientErrorCode.OPERATION_TIMED_OUT,
                }
                else PdfExportErrorCode.WORKER_FAILED,
                "image_stage.worker",
            )
        except Exception:
            failure = (PdfExportErrorCode.INTERNAL_ERROR, "image_stage.broker")
        assert failure is not None
        reject_pdf_export(*failure)

    def _process(
        self,
        plan: PdfExportPlan,
        sources: PdfExportSourceBatchLease,
        request: PdfImageStageRequest,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: PdfExportProgressSink,
    ) -> VerifiedPdfImageStageCandidate:
        handles = _require_source_binding(plan, sources)
        limits = replace(
            _operation_limits(self._policy.limits, plan),
            pixel_count=self._policy.limits.pixel_count,
        )
        if (
            sum(source.byte_size for source in plan.sources)
            + 2 * limits.output_bytes
            + self._policy.evidence_capacity_bytes
            > limits.temporary_storage_bytes
        ):
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "image_stage.operation_limits")
        forbidden = {
            plan.case_id,
            *(source.source_id for source in plan.sources),
            *(page.page_id for page in plan.pages),
        }
        ids: list[UUID] = []
        for _ in range(4):
            value = self._new_id(forbidden)
            ids.append(value)
            forbidden.add(value)
        operation_id, plan_id, output_id, evidence_id = ids
        output_buffer: PdfExportOutputBuffer | None = None
        evidence_buffer: PdfExportOutputBuffer | None = None
        transferred = False
        try:
            output_buffer = self.output_buffer_factory(limits.output_bytes)
            _require_output_buffer(output_buffer, limits.output_bytes, "image_stage.output_buffer")
            evidence_buffer = self.output_buffer_factory(self._policy.evidence_capacity_bytes)
            _require_output_buffer(
                evidence_buffer, self._policy.evidence_capacity_bytes, "image_stage.evidence_buffer"
            )
            if (
                output_buffer is evidence_buffer
                or output_buffer.write_handle_value == evidence_buffer.write_handle_value
            ):
                reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "image_stage.buffer_alias")
            with self.pipe_factory() as pipe:
                attempted = False
                try:
                    _require_active(deadline, cancellation)
                    attempted = True
                    generation = self._worker.start(plan.case_id)
                    ready = self._wait_ready(
                        plan.case_id, generation, deadline=deadline, cancellation=cancellation
                    )
                    if PDF_IMAGE_STAGE_CAPABILITY not in ready.capabilities:
                        reject_pdf_export(
                            PdfExportErrorCode.WORKER_FAILED, "image_stage.capability"
                        )
                    transferred_sources = {
                        source.source_id: self._worker.duplicate_handle(
                            handles[source.source_id], BrokeredHandlePurpose.READ_SOURCE
                        )
                        for source in plan.sources
                    }
                    payload = encode_pdf_image_stage_plan(plan, transferred_sources, request)
                    with self.plan_buffer_factory(payload) as plan_buffer:
                        if (
                            plan_buffer.payload_size != len(payload)
                            or type(plan_buffer.read_handle_value) is not int
                            or plan_buffer.read_handle_value <= 0
                        ):
                            reject_pdf_export(
                                PdfExportErrorCode.RESULT_INVALID, "image_stage.plan_buffer"
                            )
                        transfer_handles = (
                            self._worker.duplicate_handle(
                                plan_buffer.read_handle_value,
                                BrokeredHandlePurpose.READ_BOUNDED_INPUT_SECTION,
                            ),
                            self._worker.duplicate_handle(
                                output_buffer.write_handle_value,
                                BrokeredHandlePurpose.WRITE_BOUNDED_OUTPUT_SECTION,
                            ),
                            self._worker.duplicate_handle(
                                evidence_buffer.write_handle_value,
                                BrokeredHandlePurpose.WRITE_BOUNDED_OUTPUT_SECTION,
                            ),
                            self._worker.duplicate_handle(
                                pipe.write_handle_value, BrokeredHandlePurpose.WRITE_RESULT_PIPE
                            ),
                        )
                        all_handles = (*transferred_sources.values(), *transfer_handles)
                        if any(
                            type(value) is not int or value <= 0 for value in all_handles
                        ) or len(set(all_handles)) != len(all_handles):
                            reject_pdf_export(
                                PdfExportErrorCode.RESULT_INVALID, "image_stage.handle_alias"
                            )
                        pipe.close_local_writer()
                        start = _payload(
                            plan,
                            operation_id,
                            plan_id,
                            output_id,
                            evidence_id,
                            payload,
                            transfer_handles,
                            self._policy.evidence_capacity_bytes,
                            limits,
                        )
                        validate_pdf_image_stage_start(start)
                        if (
                            self._worker.start_operation(start, operation_id=operation_id)
                            != operation_id
                        ):
                            reject_pdf_export(
                                PdfExportErrorCode.REPORT_INVALID, "image_stage.operation_binding"
                            )
                        result = self._wait_result(
                            operation_id,
                            generation,
                            page_count=plan.page_count,
                            worker_page_capacity=limits.page_count,
                            deadline=deadline,
                            cancellation=cancellation,
                            progress=progress,
                        )
                finally:
                    if attempted:
                        self._worker.terminate()
                if result.generation != generation or result.operation_id != operation_id:
                    reject_pdf_export(
                        PdfExportErrorCode.REPORT_INVALID, "image_stage.result_binding"
                    )
                if result.outcome is OperationOutcome.CANCELLED:
                    reject_pdf_export(PdfExportErrorCode.CANCELLED, "image_stage.cancelled")
                if result.outcome is OperationOutcome.TIMED_OUT:
                    reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "image_stage.timeout")
                if result.outcome is not OperationOutcome.SUCCEEDED:
                    reject_pdf_export(
                        PdfExportErrorCode.LIMIT_EXCEEDED
                        if result.error_category is ErrorCategory.RESOURCE_LIMIT
                        else PdfExportErrorCode.WORKER_FAILED,
                        "image_stage.worker_result",
                    )
                validate_pdf_image_stage_result(
                    OperationResultPayload(OperationOutcome.SUCCEEDED, result.outputs, None),
                    operation_id=operation_id,
                    output_object_id=output_id,
                    evidence_object_id=evidence_id,
                    maximum_output_bytes=limits.output_bytes,
                    maximum_evidence_bytes=self._policy.evidence_capacity_bytes,
                )
                _require_source_binding(plan, sources)
                sources.revalidate(deadline=deadline, cancellation=cancellation)
                _require_source_binding(plan, sources)
                _require_active(deadline, cancellation)
                header = decode_pdf_image_stage_result(pipe.read_complete_frame())
                output_reference, evidence_reference = result.outputs
                baseline = header.baseline_report
                if (
                    header.operation_id != operation_id
                    or header.output_object_id != output_id
                    or header.evidence_object_id != evidence_id
                    or baseline.semantic_fingerprint != plan.semantic_fingerprint
                    or baseline.page_count != plan.page_count
                    or baseline.optimization is None
                    or baseline.unsigned_derivative != plan.contains_signature_markers
                    or header.output_byte_size != output_reference.expected_byte_size
                    or header.output_sha256 != output_reference.expected_sha256
                    or header.evidence_byte_size != evidence_reference.expected_byte_size
                    or header.evidence_sha256 != evidence_reference.expected_sha256
                ):
                    reject_pdf_export(
                        PdfExportErrorCode.REPORT_INVALID, "image_stage.header_binding"
                    )
                _authenticate_buffer(
                    output_buffer,
                    byte_size=header.output_byte_size,
                    sha256=header.output_sha256,
                    deadline=deadline,
                    cancellation=cancellation,
                    capture=False,
                    context="image_stage.output",
                )
                evidence = _authenticate_buffer(
                    evidence_buffer,
                    byte_size=header.evidence_byte_size,
                    sha256=header.evidence_sha256,
                    deadline=deadline,
                    cancellation=cancellation,
                    capture=True,
                    context="image_stage.evidence",
                )
                assert evidence is not None
                analysis, report = decode_pdf_image_stage_evidence(evidence)
                validate_pdf_image_stage_report(request, analysis, report)
                if (
                    report.semantic_fingerprint != plan.semantic_fingerprint
                    or analysis.baseline_sha256 != baseline.output_sha256
                    or analysis.baseline_byte_size != baseline.output_byte_size
                    or analysis.page_count != plan.page_count
                    or report.output_byte_size != header.output_byte_size
                    or report.output_sha256 != header.output_sha256
                ):
                    reject_pdf_export(
                        PdfExportErrorCode.REPORT_INVALID, "image_stage.evidence_binding"
                    )
                _require_active(deadline, cancellation)
                evidence_buffer.close()
                evidence_buffer = None
                # A terminal RPC frame may replace the last progress frame in one
                # pump. Completion follows authentication of both output sections
                # and the retained source inventory.
                _report_progress(
                    progress,
                    PdfExportProgress(
                        PdfExportProgressPhase.ASSEMBLING_AND_VERIFYING,
                        plan.page_count,
                        plan.page_count,
                    ),
                )
                _require_active(deadline, cancellation)
                candidate = VerifiedPdfImageStageCandidate(
                    output_buffer, analysis, report, baseline
                )
                transferred = True
                return candidate
        finally:
            failed = False
            for buffer in (evidence_buffer, None if transferred else output_buffer):
                if buffer is not None:
                    try:
                        buffer.close()
                    except BaseException:
                        failed = True
            if failed:
                reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "image_stage.buffer_cleanup")

    def _wait_ready(
        self,
        case_id: UUID,
        generation: int,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> WorkerClientSnapshot:
        if type(generation) is not int or generation < 1:
            reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.generation")
        while True:
            _require_active(deadline, cancellation)
            snapshot = self._worker.query()
            if snapshot.generation != generation:
                reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.generation")
            if snapshot.phase is WorkerLifecyclePhase.READY:
                if snapshot.case_id != case_id:
                    reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.case_binding")
                return snapshot
            if snapshot.phase in {WorkerLifecyclePhase.STOPPED, WorkerLifecyclePhase.FAULTED}:
                reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.handshake")
            self._worker.pump(self._policy.pump_interval_seconds)

    def _wait_result(
        self,
        operation_id: UUID,
        generation: int,
        *,
        page_count: int,
        worker_page_capacity: int,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: PdfExportProgressSink,
    ) -> WorkerOperationResultSnapshot:
        cancellation_sent = False
        last_progress: PdfExportProgress | None = None
        while True:
            snapshot = self._worker.query()
            if snapshot.generation != generation:
                reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.generation")
            result = snapshot.last_result
            if result is not None:
                if result.operation_id != operation_id:
                    reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "export.result_binding")
                if cancellation_sent and result.outcome is OperationOutcome.SUCCEEDED:
                    reject_pdf_export(PdfExportErrorCode.CANCELLED, "export.cancelled")
                return result
            active = snapshot.active_operation
            if active is not None and active.operation_id == operation_id:
                current = active.progress
                if current is not None:
                    if (
                        current.stage is not ProgressStage.PROCESSING
                        or current.unit is not ProgressUnit.PAGES
                        or current.total_units != worker_page_capacity
                        or current.completed_units > worker_page_capacity
                    ):
                        reject_pdf_export(
                            PdfExportErrorCode.REPORT_INVALID,
                            "export.progress_binding",
                        )
                    domain_progress = PdfExportProgress(
                        PdfExportProgressPhase.ASSEMBLING_AND_VERIFYING,
                        # Worker capacity also bounds complete source parsing;
                        # UI progress stays tied to the exact output-page plan.
                        current.completed_units * page_count // worker_page_capacity,
                        page_count,
                    )
                    if domain_progress != last_progress:
                        _report_progress(progress, domain_progress)
                        last_progress = domain_progress
            if snapshot.phase in {
                WorkerLifecyclePhase.READY,
                WorkerLifecyclePhase.STOPPED,
                WorkerLifecyclePhase.FAULTED,
            }:
                reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.worker_exit")
            if _deadline_expired(deadline):
                if not cancellation_sent:
                    self._worker.cancel(CancelReason.SHUTDOWN)
                reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "export.deadline")
            if _cancellation_requested(cancellation) and not cancellation_sent:
                receipt = self._worker.cancel(CancelReason.USER_REQUESTED)
                # The client returns a cancellation request ID. Protocol state
                # binds its acknowledgement to this active operation.
                if type(receipt) is not UUID or receipt.version != 4:
                    reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "export.cancel_binding")
                cancellation_sent = True
            self._worker.pump(self._policy.pump_interval_seconds)

    def _new_id(self, forbidden: set[UUID]) -> UUID:
        for _ in range(32):
            try:
                value = self._id_factory()
            except BaseException:
                reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.identifier")
            if type(value) is UUID and value.version == 4 and value not in forbidden:
                return value
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.identifier")


def _payload(
    plan: PdfExportPlan,
    operation_id: UUID,
    plan_id: UUID,
    output_id: UUID,
    evidence_id: UUID,
    payload: bytes,
    handles: tuple[int, ...],
    capacity: int,
    limits: ResourceLimits,
) -> OperationStartPayload:
    return OperationStartPayload(
        PDF_IMAGE_STAGE_CAPABILITY.capability_id,
        1,
        (
            CaseObjectReference(
                ReferenceArea.SOURCE_READ,
                plan_id,
                operation_id=None,
                content_kind=PDF_IMAGE_STAGE_PLAN_CONTENT_KIND,
                expected_byte_size=len(payload),
                expected_sha256=hashlib.sha256(payload).hexdigest(),
            ),
            *(
                CaseObjectReference(
                    ReferenceArea.SOURCE_READ,
                    source.source_id,
                    operation_id=None,
                    content_kind=source.media_type.value.removeprefix("image/").removeprefix(
                        "application/"
                    ),
                    expected_byte_size=source.byte_size,
                    expected_sha256=source.sha256,
                )
                for source in plan.sources
            ),
        ),
        (
            *(
                TaggedScalar(name, ScalarValueType.INTEGER, handle)
                for name, handle in zip(
                    ("plan_handle", "output_handle", "evidence_handle", "result_handle"),
                    handles,
                    strict=True,
                )
            ),
            TaggedScalar("output_object_id", ScalarValueType.UUID, output_id),
            TaggedScalar("evidence_object_id", ScalarValueType.UUID, evidence_id),
            TaggedScalar("evidence_capacity_bytes", ScalarValueType.INTEGER, capacity),
        ),
        limits,
    )

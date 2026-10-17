"""Private derivative sanitization broker; candidates never grant export authority."""

from __future__ import annotations

import hashlib
from contextlib import AbstractContextManager
from dataclasses import replace
from typing import Protocol, Self
from uuid import UUID

from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportProgress,
    PdfExportProgressPhase,
    reject_pdf_export,
)
from paperworkshell.domain.redaction_sanitization import (
    RedactionSanitizationReport,
    RedactionSanitizationRequest,
    validate_sanitization_report,
)
from paperworkshell.domain.source_ingest import SourceIngestError
from paperworkshell.worker._windows_broker import WindowsResultPipe
from paperworkshell.worker.client.errors import WorkerClientError
from paperworkshell.worker.client.models import WorkerLifecyclePhase, WorkerOperationResultSnapshot
from paperworkshell.worker.client.process import BrokeredHandlePurpose
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.pdf_export_broker import (
    MAX_PDF_EXPORT_CANDIDATE_READ_BYTES,
    BrokeredPdfExporter,
    CancellationProbe,
    DeadlineProbe,
    OutputBufferFactory,
    PdfExportBrokerPolicy,
    PdfExportOutputBuffer,
    PdfExportProgressSink,
    PdfExportWorkerCoordinator,
    PlanBufferFactory,
    ResultPipeFactory,
    _cancellation_requested,
    _deadline_expired,
    _raise_source_error,
    _report_progress,
    _require_active,
)
from paperworkshell.worker.pdf_export_buffers import (
    WindowsPdfExportOutputBuffer,
    WindowsPdfExportPlanBuffer,
)
from paperworkshell.worker.pdf_thumbnail_broker import HandleValue
from paperworkshell.worker.protocol.models import (
    REDACTION_SANITIZATION_CAPABILITY,
    REDACTION_SANITIZATION_PLAN_CONTENT_KIND,
    CancelReason,
    CaseObjectReference,
    ErrorCategory,
    OperationOutcome,
    OperationResultPayload,
    OperationStartPayload,
    ProgressStage,
    ProgressUnit,
    ReferenceArea,
    ScalarValueType,
    TaggedScalar,
)
from paperworkshell.worker.protocol.redaction_sanitization import (
    sanitization_binding,
    validate_sanitization_result,
)
from paperworkshell.worker.redaction_sanitization_wire import (
    decode_sanitization_report,
    encode_sanitization_plan,
)
from paperworkshell.worker.searchable_pdf_export_broker import _authenticate_buffer


class RetainedSanitizationSourceLease(Protocol):
    """Caller-owned authenticated input derivative and retained read-only authority."""

    @property
    def handle(self) -> HandleValue: ...

    @property
    def case_id(self) -> UUID: ...

    @property
    def source_id(self) -> UUID: ...

    @property
    def source_page_count(self) -> int: ...

    @property
    def source_byte_size(self) -> int: ...

    @property
    def source_sha256(self) -> str: ...


class UnverifiedSanitizationCandidate(AbstractContextManager["UnverifiedSanitizationCandidate"]):
    """Bounded single-pass private sanitized derivative, still awaiting security checks."""

    def __init__(self, buffer: PdfExportOutputBuffer, report: RedactionSanitizationReport) -> None:
        self._buffer = buffer
        self.report = report
        self._chunks = buffer.iter_chunks(
            report.output_byte_size, chunk_bytes=MAX_PDF_EXPORT_CANDIDATE_READ_BYTES
        )
        self._pending = b""
        self._position = 0
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def read(self, size: int, /) -> bytes:
        if (
            self._closed
            or type(size) is not int
            or not 1 <= size <= MAX_PDF_EXPORT_CANDIDATE_READ_BYTES
        ):
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "sanitization.candidate_read")
        wanted = min(size, self.report.output_byte_size - self._position)
        parts: list[bytes] = []
        count = 0
        while count < wanted:
            try:
                chunk = self._pending or next(self._chunks)
            except Exception:
                reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "sanitization.candidate_short")
            if (
                type(chunk) is not bytes
                or not chunk
                or len(chunk) > self.report.output_byte_size - self._position - count
            ):
                reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "sanitization.candidate_chunk")
            take = min(len(chunk), wanted - count)
            parts.append(chunk[:take])
            self._pending = chunk[take:]
            count += take
        self._position += count
        return b"".join(parts)

    def close(self) -> None:
        if not self._closed:
            self._buffer.close()
            self._pending = b""
            self._closed = True

    def __enter__(self) -> Self:
        if self._closed:
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "sanitization.candidate_closed")
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return "UnverifiedSanitizationCandidate(status='awaiting_verification')"


def _ignore_progress(_value: object) -> None:
    pass


class BrokeredRedactionSanitizer:
    def __init__(
        self,
        worker: PdfExportWorkerCoordinator,
        policy: PdfExportBrokerPolicy,
        *,
        heavy_operations: HeavyOperationCoordinator,
        pipe_factory: ResultPipeFactory = WindowsResultPipe.create,
        plan_buffer_factory: PlanBufferFactory = WindowsPdfExportPlanBuffer.create,
        output_buffer_factory: OutputBufferFactory = WindowsPdfExportOutputBuffer.create,
    ) -> None:
        self._worker = worker
        self._policy = policy
        self._heavy = heavy_operations
        self._pipe_factory = pipe_factory
        self._plan_factory = plan_buffer_factory
        self._output_factory = output_buffer_factory
        self._revoked = False
        self._candidate: UnverifiedSanitizationCandidate | None = None
        # Reuse the already-tested bounded handshake, progress, cancellation and
        # process-generation loops; no normal-export plan is constructed.
        self._waiter = BrokeredPdfExporter(worker, policy, heavy_operations=heavy_operations)

    @property
    def revoked(self) -> bool:
        return self._revoked

    def _revoke_permanently(self) -> None:
        self._revoked = True

    def _close_candidate(self) -> None:
        if self._candidate is not None:
            self._candidate.close()

    def sanitize(
        self,
        request: RedactionSanitizationRequest,
        source: RetainedSanitizationSourceLease,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: PdfExportProgressSink = _ignore_progress,
    ) -> UnverifiedSanitizationCandidate:
        if (
            self._revoked
            or type(request) is not RedactionSanitizationRequest
            or not callable(progress)
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "sanitization.broker_request")
        _require_active(deadline, cancellation)
        try:
            while True:
                lease = self._heavy.try_acquire(self._policy.pump_interval_seconds)
                if lease is not None:
                    break
                _require_active(deadline, cancellation)
            with lease:
                _require_active(deadline, cancellation)
                if self._candidate is not None and not self._candidate.closed:
                    reject_pdf_export(
                        PdfExportErrorCode.LIMIT_EXCEEDED, "sanitization.candidate_capacity"
                    )
                self._candidate = None
                return self._sanitize(
                    request, source, deadline=deadline, cancellation=cancellation, progress=progress
                )
        except PdfExportError:
            raise
        except SourceIngestError as error:
            _raise_source_error(error)
        except WorkerClientError:
            reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "sanitization.worker")
        except Exception:
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "sanitization.broker")

    def _sanitize(
        self,
        request: RedactionSanitizationRequest,
        source: RetainedSanitizationSourceLease,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: PdfExportProgressSink,
    ) -> UnverifiedSanitizationCandidate:
        request.__post_init__()
        handle = _source_handle(request, source)
        limits = replace(self._policy.limits, page_count=request.source_page_count)
        if request.source_page_count > self._policy.limits.page_count:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "sanitization.page_limit")
        forbidden = {request.case_id, request.source_id}
        ids: list[UUID] = []
        for _ in range(3):
            selected = self._waiter._new_id(forbidden)
            forbidden.add(selected)
            ids.append(selected)
        operation_id, plan_id, output_id = ids
        plan_payload = encode_sanitization_plan(request)
        plan_reference = CaseObjectReference(
            ReferenceArea.SOURCE_READ,
            plan_id,
            operation_id=None,
            content_kind=REDACTION_SANITIZATION_PLAN_CONTENT_KIND,
            expected_byte_size=len(plan_payload),
            expected_sha256=hashlib.sha256(plan_payload).hexdigest(),
        )
        source_reference = CaseObjectReference(
            ReferenceArea.SOURCE_READ,
            request.source_id,
            operation_id=None,
            content_kind="pdf",
            expected_byte_size=request.source_byte_size,
            expected_sha256=request.source_sha256,
        )
        buffer = self._output_factory(limits.output_bytes)
        transferred = False
        try:
            if (
                buffer.capacity != limits.output_bytes
                or type(buffer.write_handle_value) is not int
                or buffer.write_handle_value < 1
            ):
                reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "sanitization.output_buffer")
            with self._pipe_factory() as pipe:
                started = False
                try:
                    _require_active(deadline, cancellation)
                    started = True
                    generation = self._worker.start(request.case_id)
                    ready = self._waiter._wait_ready(
                        request.case_id, generation, deadline=deadline, cancellation=cancellation
                    )
                    if REDACTION_SANITIZATION_CAPABILITY not in ready.capabilities:
                        reject_pdf_export(
                            PdfExportErrorCode.WORKER_FAILED, "sanitization.capability"
                        )
                    with self._plan_factory(plan_payload) as plan:
                        if plan.payload_size != len(plan_payload):
                            reject_pdf_export(
                                PdfExportErrorCode.RESULT_INVALID, "sanitization.plan_buffer"
                            )
                        handles = (
                            self._worker.duplicate_handle(
                                handle, BrokeredHandlePurpose.READ_SOURCE
                            ),
                            self._worker.duplicate_handle(
                                plan.read_handle_value,
                                BrokeredHandlePurpose.READ_BOUNDED_INPUT_SECTION,
                            ),
                            self._worker.duplicate_handle(
                                buffer.write_handle_value,
                                BrokeredHandlePurpose.WRITE_BOUNDED_OUTPUT_SECTION,
                            ),
                            self._worker.duplicate_handle(
                                pipe.write_handle_value, BrokeredHandlePurpose.WRITE_RESULT_PIPE
                            ),
                        )
                        parameters = tuple(
                            TaggedScalar(name, ScalarValueType.INTEGER, value)
                            for name, value in zip(
                                ("source_handle", "plan_handle", "output_handle", "result_handle"),
                                handles,
                                strict=True,
                            )
                        ) + (TaggedScalar("output_object_id", ScalarValueType.UUID, output_id),)
                        payload = OperationStartPayload(
                            REDACTION_SANITIZATION_CAPABILITY.capability_id,
                            1,
                            (plan_reference, source_reference),
                            parameters,
                            limits,
                        )
                        sanitization_binding(payload)
                        pipe.close_local_writer()
                        if (
                            self._worker.start_operation(payload, operation_id=operation_id)
                            != operation_id
                        ):
                            reject_pdf_export(
                                PdfExportErrorCode.REPORT_INVALID,
                                "sanitization.operation_binding",
                            )
                        # One bounded document sanitization operation; page counts
                        # remain authenticated in the private plan and report.
                        result = self._wait_result(
                            operation_id,
                            generation,
                            deadline=deadline,
                            cancellation=cancellation,
                            progress=progress,
                        )
                finally:
                    if started:
                        self._worker.terminate()
                _require_active(deadline, cancellation)
                if result.generation != generation or result.operation_id != operation_id:
                    reject_pdf_export(
                        PdfExportErrorCode.REPORT_INVALID, "sanitization.result_binding"
                    )
                if result.outcome is not OperationOutcome.SUCCEEDED:
                    code = (
                        PdfExportErrorCode.CANCELLED
                        if result.outcome is OperationOutcome.CANCELLED
                        else PdfExportErrorCode.TIMED_OUT
                        if result.outcome is OperationOutcome.TIMED_OUT
                        else PdfExportErrorCode.LIMIT_EXCEEDED
                        if result.error_category is ErrorCategory.RESOURCE_LIMIT
                        else PdfExportErrorCode.WORKER_FAILED
                    )
                    reject_pdf_export(code, "sanitization.worker_result")
                validate_sanitization_result(
                    OperationResultPayload(result.outcome, result.outputs, None),
                    operation_id=operation_id,
                    output_object_id=output_id,
                    maximum_output_bytes=limits.output_bytes,
                )
                if _source_handle(request, source) != handle:
                    reject_pdf_export(
                        PdfExportErrorCode.SOURCE_CHANGED, "sanitization.source_handle"
                    )
                header = decode_sanitization_report(pipe.read_complete_frame())
                report = header.report
                validate_sanitization_report(request, report)
                output = result.outputs[0]
                if (
                    header.operation_id != operation_id
                    or header.output_object_id != output_id
                    or output.expected_byte_size != report.output_byte_size
                    or output.expected_sha256 != report.output_sha256
                ):
                    reject_pdf_export(
                        PdfExportErrorCode.REPORT_INVALID, "sanitization.report_binding"
                    )
                _authenticate_buffer(
                    buffer,
                    byte_size=report.output_byte_size,
                    sha256=report.output_sha256,
                    deadline=deadline,
                    cancellation=cancellation,
                    capture=False,
                    context="sanitization.output",
                )
                candidate = UnverifiedSanitizationCandidate(buffer, report)
                self._candidate = candidate
                transferred = True
                return candidate
        finally:
            if not transferred:
                buffer.close()

    def _wait_result(
        self,
        operation_id: UUID,
        generation: int,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: PdfExportProgressSink,
    ) -> WorkerOperationResultSnapshot:
        cancellation_sent = False
        last_completed: int | None = None
        while True:
            snapshot = self._worker.query()
            if snapshot.generation != generation:
                reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "sanitization.generation")
            result = snapshot.last_result
            if result is not None:
                if result.operation_id != operation_id:
                    reject_pdf_export(
                        PdfExportErrorCode.REPORT_INVALID, "sanitization.result_binding"
                    )
                if cancellation_sent and snapshot.phase is WorkerLifecyclePhase.CANCELLING:
                    # A terminal result may precede the acknowledgement of the
                    # already-sent cancellation. Drain that bounded exchange
                    # before teardown so its measured acknowledgement survives.
                    # WorkerClient enforces the cancellation grace deadline.
                    self._worker.pump(self._policy.pump_interval_seconds)
                    continue
                if cancellation_sent and result.outcome is OperationOutcome.SUCCEEDED:
                    reject_pdf_export(PdfExportErrorCode.CANCELLED, "sanitization.cancelled")
                return result
            active = snapshot.active_operation
            if active is not None:
                if active.operation_id != operation_id:
                    reject_pdf_export(
                        PdfExportErrorCode.REPORT_INVALID, "sanitization.progress_binding"
                    )
                current = active.progress
                if current is not None:
                    if (
                        current.stage is not ProgressStage.PROCESSING
                        or current.unit is not ProgressUnit.STEPS
                        or current.total_units != 1
                        or current.completed_units not in {0, 1}
                    ):
                        reject_pdf_export(
                            PdfExportErrorCode.REPORT_INVALID, "sanitization.progress_binding"
                        )
                    if last_completed != current.completed_units:
                        _report_progress(
                            progress,
                            PdfExportProgress(
                                PdfExportProgressPhase.ASSEMBLING_AND_VERIFYING,
                                current.completed_units,
                                1,
                            ),
                        )
                        last_completed = current.completed_units
            if snapshot.phase in {
                WorkerLifecyclePhase.READY,
                WorkerLifecyclePhase.STOPPED,
                WorkerLifecyclePhase.FAULTED,
            }:
                reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "sanitization.worker_exit")
            if _deadline_expired(deadline):
                if not cancellation_sent:
                    self._worker.cancel(CancelReason.SHUTDOWN)
                reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "sanitization.deadline")
            if _cancellation_requested(cancellation) and not cancellation_sent:
                # WorkerClient.cancel returns the cancellation request ID; the
                # client itself binds its target to the active operation.
                self._worker.cancel(CancelReason.USER_REQUESTED)
                cancellation_sent = True
            self._worker.pump(self._policy.pump_interval_seconds)


def _source_handle(
    request: RedactionSanitizationRequest, source: RetainedSanitizationSourceLease
) -> int:
    if any(
        getattr(source, name) != getattr(request, name)
        for name in (
            "case_id",
            "source_id",
            "source_page_count",
            "source_byte_size",
            "source_sha256",
        )
    ):
        reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "sanitization.source_lease")
    handle = source.handle.value
    if type(handle) is not int or handle < 1:
        reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "sanitization.source_handle")
    return handle

"""Private derivative redaction_export broker; candidates never grant export authority."""

from __future__ import annotations

import hashlib
from contextlib import AbstractContextManager
from dataclasses import replace
from typing import Self
from uuid import UUID

from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportProgress,
    PdfExportProgressPhase,
    reject_pdf_export,
)
from paperworkshell.domain.redaction_export_candidate import (
    RedactionExportWorkerReport,
    validate_redaction_export_worker_report,
)
from paperworkshell.domain.redaction_reconstruction import RedactionReconstructionRequest
from paperworkshell.domain.redaction_verification import (
    RUNTIME_VERIFICATION_PROFILE,
    VERIFICATION_PROFILE,
    VerificationMethod,
    VerificationStatus,
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
from paperworkshell.worker.pdf_thumbnail_broker import CommittedPdfPageLease
from paperworkshell.worker.protocol.models import (
    REDACTION_EXPORT_CAPABILITY,
    REDACTION_EXPORT_PLAN_CONTENT_KIND,
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
from paperworkshell.worker.protocol.redaction_export import (
    redaction_export_binding,
    validate_redaction_export_result,
)
from paperworkshell.worker.redaction_export_wire import (
    decode_redaction_export_report,
    encode_redaction_export_plan,
    encode_redaction_export_report,
)
from paperworkshell.worker.searchable_pdf_export_broker import _authenticate_buffer

_CANDIDATE_TOKEN = object()


class PreparedRedactionCandidate(AbstractContextManager["PreparedRedactionCandidate"]):
    """Bounded single-pass private sanitized derivative, still awaiting security checks."""

    def __init__(
        self, buffer: PdfExportOutputBuffer, report: RedactionExportWorkerReport, *, _token: object
    ) -> None:
        if _token is not _CANDIDATE_TOKEN:
            reject_pdf_export(
                PdfExportErrorCode.RESULT_INVALID, "redaction_export.candidate_authority"
            )
        self._buffer = buffer
        self._report = report
        self._evidence = encode_redaction_export_report(report)
        self._chunks = buffer.iter_chunks(
            report.output_byte_size, chunk_bytes=MAX_PDF_EXPORT_CANDIDATE_READ_BYTES
        )
        self._pending = b""
        self._position = 0
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def report(self) -> RedactionExportWorkerReport:
        return self._report

    def read(self, size: int, /) -> bytes:
        if (
            self._closed
            or type(size) is not int
            or not 1 <= size <= MAX_PDF_EXPORT_CANDIDATE_READ_BYTES
        ):
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "redaction_export.candidate_read")
        wanted = min(size, self.report.output_byte_size - self._position)
        parts: list[bytes] = []
        count = 0
        while count < wanted:
            try:
                chunk = self._pending or next(self._chunks)
            except Exception:
                reject_pdf_export(
                    PdfExportErrorCode.RESULT_INVALID, "redaction_export.candidate_short"
                )
            if (
                type(chunk) is not bytes
                or not chunk
                or len(chunk) > self.report.output_byte_size - self._position - count
            ):
                reject_pdf_export(
                    PdfExportErrorCode.RESULT_INVALID, "redaction_export.candidate_chunk"
                )
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
            reject_pdf_export(
                PdfExportErrorCode.RESULT_INVALID, "redaction_export.candidate_closed"
            )
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return "PreparedRedactionCandidate(status='awaiting_verification')"


def _ignore_progress(_value: object) -> None:
    pass


def _require_supported_evidence(
    report: RedactionExportWorkerReport, *, runtime_ocr: bool = False
) -> None:
    # Only the admitted OCR-enabled composition may report runtime OCR evidence.
    # No worker frame can acquire human viewer testimony by asserting success.
    expected = RUNTIME_VERIFICATION_PROFILE if runtime_ocr else VERIFICATION_PROFILE
    if report.verification_request.profile_id != expected or any(
        item.status is VerificationStatus.PASS
        for item in report.verification.methods
        if item.method is VerificationMethod.VIEWERS
        or item.method is VerificationMethod.OCR
        and not runtime_ocr
    ):
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "redaction_export.external_evidence")


class BrokeredRedactionExporter:
    def __init__(
        self,
        worker: PdfExportWorkerCoordinator,
        policy: PdfExportBrokerPolicy,
        *,
        heavy_operations: HeavyOperationCoordinator,
        pipe_factory: ResultPipeFactory = WindowsResultPipe.create,
        plan_buffer_factory: PlanBufferFactory = WindowsPdfExportPlanBuffer.create,
        output_buffer_factory: OutputBufferFactory = WindowsPdfExportOutputBuffer.create,
        runtime_ocr: bool = False,
    ) -> None:
        self._worker = worker
        self._policy = policy
        self._heavy = heavy_operations
        self._pipe_factory = pipe_factory
        self._plan_factory = plan_buffer_factory
        self._output_factory = output_buffer_factory
        if type(runtime_ocr) is not bool:
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "redaction_export.profile")
        self._runtime_ocr = runtime_ocr
        self._revoked = False
        self._candidate: PreparedRedactionCandidate | None = None
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

    def validate_candidate(
        self,
        candidate: object,
        request: RedactionReconstructionRequest,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> None:
        """Authenticate only this broker's current unused candidate and provenance."""
        _require_active(deadline, cancellation)
        if (
            self._revoked
            or type(candidate) is not PreparedRedactionCandidate
            or self._candidate is not candidate
            or candidate.closed
            or candidate._position != 0
            or encode_redaction_export_report(candidate.report) != candidate._evidence
        ):
            reject_pdf_export(
                PdfExportErrorCode.RESULT_INVALID, "redaction_export.candidate_authority"
            )
        validate_redaction_export_worker_report(request, candidate.report)
        _require_supported_evidence(candidate.report, runtime_ocr=self._runtime_ocr)
        _authenticate_buffer(
            candidate._buffer,
            byte_size=candidate.report.output_byte_size,
            sha256=candidate.report.output_sha256,
            deadline=deadline,
            cancellation=cancellation,
            capture=False,
            context="redaction_export.candidate",
        )

    def prepare(
        self,
        request: RedactionReconstructionRequest,
        source: CommittedPdfPageLease,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: PdfExportProgressSink = _ignore_progress,
    ) -> PreparedRedactionCandidate:
        if (
            self._revoked
            or type(request) is not RedactionReconstructionRequest
            or not callable(progress)
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "redaction_export.broker_request")
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
                        PdfExportErrorCode.LIMIT_EXCEEDED, "redaction_export.candidate_capacity"
                    )
                self._candidate = None
                return self._prepare(
                    request, source, deadline=deadline, cancellation=cancellation, progress=progress
                )
        except PdfExportError:
            raise
        except SourceIngestError as error:
            _raise_source_error(error)
        except WorkerClientError:
            reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "redaction_export.worker")
        except Exception:
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "redaction_export.broker")

    def _prepare(
        self,
        request: RedactionReconstructionRequest,
        source: CommittedPdfPageLease,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: PdfExportProgressSink,
    ) -> PreparedRedactionCandidate:
        request.__post_init__()
        handle = _source_handle(request, source)
        limits = replace(self._policy.limits, page_count=request.source_page_count)
        if request.source_page_count > self._policy.limits.page_count:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "redaction_export.page_limit")
        forbidden = {
            request.case_id,
            request.source_id,
            request.page_id,
            *(mark.mark_id for mark in request.marks),
        }
        ids: list[UUID] = []
        for _ in range(3):
            selected = self._waiter._new_id(forbidden)
            forbidden.add(selected)
            ids.append(selected)
        operation_id, plan_id, output_id = ids
        plan_payload = encode_redaction_export_plan(request)
        if (
            request.source_byte_size + len(plan_payload) + 3 * limits.output_bytes
            > limits.temporary_storage_bytes
        ):
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "redaction_export.storage_budget")
        plan_reference = CaseObjectReference(
            ReferenceArea.SOURCE_READ,
            plan_id,
            operation_id=None,
            content_kind=REDACTION_EXPORT_PLAN_CONTENT_KIND,
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
                reject_pdf_export(
                    PdfExportErrorCode.RESULT_INVALID, "redaction_export.output_buffer"
                )
            with self._pipe_factory() as pipe:
                started = False
                try:
                    _require_active(deadline, cancellation)
                    started = True
                    generation = self._worker.start(request.case_id)
                    ready = self._waiter._wait_ready(
                        request.case_id, generation, deadline=deadline, cancellation=cancellation
                    )
                    if REDACTION_EXPORT_CAPABILITY not in ready.capabilities:
                        reject_pdf_export(
                            PdfExportErrorCode.WORKER_FAILED, "redaction_export.capability"
                        )
                    with self._plan_factory(plan_payload) as plan:
                        if plan.payload_size != len(plan_payload):
                            reject_pdf_export(
                                PdfExportErrorCode.RESULT_INVALID, "redaction_export.plan_buffer"
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
                            REDACTION_EXPORT_CAPABILITY.capability_id,
                            1,
                            (plan_reference, source_reference),
                            parameters,
                            limits,
                        )
                        redaction_export_binding(payload)
                        pipe.close_local_writer()
                        if (
                            self._worker.start_operation(payload, operation_id=operation_id)
                            != operation_id
                        ):
                            reject_pdf_export(
                                PdfExportErrorCode.REPORT_INVALID,
                                "redaction_export.operation_binding",
                            )
                        # One bounded document redaction_export operation; page counts
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
                        PdfExportErrorCode.REPORT_INVALID, "redaction_export.result_binding"
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
                    reject_pdf_export(code, "redaction_export.worker_result")
                validate_redaction_export_result(
                    OperationResultPayload(result.outcome, result.outputs, None),
                    operation_id=operation_id,
                    output_object_id=output_id,
                    maximum_output_bytes=limits.output_bytes,
                )
                if _source_handle(request, source) != handle:
                    reject_pdf_export(
                        PdfExportErrorCode.SOURCE_CHANGED, "redaction_export.source_handle"
                    )
                header = decode_redaction_export_report(pipe.read_complete_frame())
                report = header
                validate_redaction_export_worker_report(request, report)
                _require_supported_evidence(report, runtime_ocr=self._runtime_ocr)
                output = result.outputs[0]
                if (
                    report.operation_id != operation_id
                    or report.output_object_id != output_id
                    or output.expected_byte_size != report.output_byte_size
                    or output.expected_sha256 != report.output_sha256
                ):
                    reject_pdf_export(
                        PdfExportErrorCode.REPORT_INVALID, "redaction_export.report_binding"
                    )
                _authenticate_buffer(
                    buffer,
                    byte_size=report.output_byte_size,
                    sha256=report.output_sha256,
                    deadline=deadline,
                    cancellation=cancellation,
                    capture=False,
                    context="redaction_export.output",
                )
                candidate = PreparedRedactionCandidate(buffer, report, _token=_CANDIDATE_TOKEN)
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
                reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "redaction_export.generation")
            result = snapshot.last_result
            if result is not None:
                if result.operation_id != operation_id:
                    reject_pdf_export(
                        PdfExportErrorCode.REPORT_INVALID, "redaction_export.result_binding"
                    )
                if cancellation_sent and snapshot.phase is WorkerLifecyclePhase.CANCELLING:
                    # A terminal result may precede the acknowledgement of the
                    # already-sent cancellation. Drain that bounded exchange
                    # before teardown so its measured acknowledgement survives.
                    # WorkerClient enforces the cancellation grace deadline.
                    self._worker.pump(self._policy.pump_interval_seconds)
                    continue
                if cancellation_sent and result.outcome is OperationOutcome.SUCCEEDED:
                    reject_pdf_export(PdfExportErrorCode.CANCELLED, "redaction_export.cancelled")
                return result
            active = snapshot.active_operation
            if active is not None:
                if active.operation_id != operation_id:
                    reject_pdf_export(
                        PdfExportErrorCode.REPORT_INVALID, "redaction_export.progress_binding"
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
                            PdfExportErrorCode.REPORT_INVALID, "redaction_export.progress_binding"
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
                reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "redaction_export.worker_exit")
            if _deadline_expired(deadline):
                if not cancellation_sent:
                    self._worker.cancel(CancelReason.SHUTDOWN)
                reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "redaction_export.deadline")
            if _cancellation_requested(cancellation) and not cancellation_sent:
                # WorkerClient.cancel returns the cancellation request ID; the
                # client itself binds its target to the active operation.
                self._worker.cancel(CancelReason.USER_REQUESTED)
                cancellation_sent = True
            self._worker.pump(self._policy.pump_interval_seconds)


def _source_handle(request: RedactionReconstructionRequest, source: CommittedPdfPageLease) -> int:
    if any(
        getattr(source, name) != getattr(request, name)
        for name in (
            "case_id",
            "source_id",
            "page_id",
            "source_page_index",
            "source_page_count",
            "source_byte_size",
            "source_sha256",
        )
    ):
        reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "redaction_export.source_lease")
    handle = source.handle.value
    if type(handle) is not int or handle < 1:
        reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "redaction_export.source_handle")
    return handle

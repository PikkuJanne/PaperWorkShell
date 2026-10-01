"""Trusted handle-only broker for searchable mixed-document PDF export.

The GUI process retains every path. The one-shot worker receives authenticated
read handles plus four reduced-right transfer handles, writes an exact PDF and
content-free page-status section, and is terminated before retained sources or
either output are authenticated.
"""

from __future__ import annotations

import hashlib
import hmac
import math
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from typing import Final, Never, Protocol, Self
from uuid import UUID, uuid4

from paperworkshell.domain.manifest_repository import MAX_SAFE_MANIFEST_INTEGER
from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportProgress,
    PdfExportProgressPhase,
    reject_pdf_export,
)
from paperworkshell.domain.pdf_inspection import MAX_PDF_INSPECTION_REPORT_BYTES
from paperworkshell.domain.searchable_pdf_export import (
    MAX_SEARCHABLE_PDF_PAGES,
    SearchablePdfExportError,
    SearchablePdfExportErrorCode,
    SearchablePdfExportPlan,
    SearchablePdfSourceEncryptionState,
    SearchablePdfSourceMediaType,
    SearchablePdfSourceSignatureState,
    SearchablePdfVerificationReport,
)
from paperworkshell.domain.source_ingest import SourceIngestError, SourceIngestErrorCode
from paperworkshell.worker._windows_broker import WindowsResultPipe
from paperworkshell.worker.client.errors import WorkerClientError, WorkerClientErrorCode
from paperworkshell.worker.client.models import (
    WorkerClientSnapshot,
    WorkerLifecyclePhase,
    WorkerOperationResultSnapshot,
)
from paperworkshell.worker.client.process import BrokeredHandlePurpose
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator, HeavyOperationLease
from paperworkshell.worker.pdf_export_broker import (
    MAX_PDF_EXPORT_CANDIDATE_READ_BYTES,
    PDF_EXPORT_VERIFICATION_PIXELS,
    PDF_EXPORT_VERIFICATION_RGBA_BYTES,
    CancellationProbe,
    DeadlineProbe,
    PdfExportOutputBuffer,
    PdfExportPlanBuffer,
    PdfExportResultPipe,
    PdfExportWorkerCoordinator,
)
from paperworkshell.worker.pdf_export_buffers import (
    WindowsPdfExportOutputBuffer,
    WindowsPdfExportPlanBuffer,
)
from paperworkshell.worker.protocol.models import (
    MAX_SEARCHABLE_PDF_EXPORT_STATUS_BYTES,
    SEARCHABLE_PDF_EXPORT_CAPABILITY,
    SEARCHABLE_PDF_EXPORT_OUTPUT_CONTENT_KIND,
    SEARCHABLE_PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
    SEARCHABLE_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
    SEARCHABLE_PDF_EXPORT_PLAN_CONTENT_KIND,
    SEARCHABLE_PDF_EXPORT_PLAN_HANDLE_PARAMETER,
    SEARCHABLE_PDF_EXPORT_RESULT_HANDLE_PARAMETER,
    SEARCHABLE_PDF_EXPORT_STATUS_CAPACITY_PARAMETER,
    SEARCHABLE_PDF_EXPORT_STATUS_CONTENT_KIND,
    SEARCHABLE_PDF_EXPORT_STATUS_HANDLE_PARAMETER,
    SEARCHABLE_PDF_EXPORT_STATUS_OBJECT_ID_PARAMETER,
    CancelReason,
    CaseObjectReference,
    ErrorCategory,
    ErrorCode,
    OperationOutcome,
    OperationStartPayload,
    ProgressStage,
    ProgressUnit,
    ReferenceArea,
    ResourceLimits,
    ScalarValueType,
    TaggedScalar,
)
from paperworkshell.worker.searchable_pdf_export_plan import (
    encode_searchable_pdf_worker_plan,
)
from paperworkshell.worker.searchable_pdf_export_report import (
    SearchablePdfWorkerReport,
    decode_searchable_pdf_worker_report,
)
from paperworkshell.worker.searchable_pdf_export_status import (
    SearchablePdfPageStatusDocument,
    decode_searchable_pdf_page_status,
)

_MAXIMUM_IDENTIFIER_ATTEMPTS: Final = 32


class SearchablePdfExportSourceLease(Protocol):
    """Retained authority for one exact committed PDF or image source."""

    @property
    def source_id(self) -> UUID: ...

    @property
    def media_type(self) -> SearchablePdfSourceMediaType: ...

    @property
    def byte_size(self) -> int: ...

    @property
    def page_count(self) -> int: ...

    @property
    def sha256(self) -> str: ...

    @property
    def signature_state(self) -> SearchablePdfSourceSignatureState: ...

    @property
    def encryption_state(self) -> SearchablePdfSourceEncryptionState: ...

    @property
    def handle_value(self) -> int: ...


class SearchablePdfExportSourceBatchLease(Protocol):
    """Manifest-bound mixed-source batch retained across one worker attempt."""

    @property
    def case_id(self) -> UUID: ...

    @property
    def manifest_revision(self) -> int: ...

    @property
    def manifest_sha256(self) -> str: ...

    @property
    def manifest_cas_token(self) -> str: ...

    @property
    def sources(self) -> tuple[SearchablePdfExportSourceLease, ...]: ...

    def revalidate(
        self,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> None: ...


class SearchablePdfExportProgressSink(Protocol):
    def __call__(self, progress: PdfExportProgress, /) -> None: ...


ResultPipeFactory = Callable[[], PdfExportResultPipe]
PlanBufferFactory = Callable[[bytes], PdfExportPlanBuffer]
OutputBufferFactory = Callable[[int], PdfExportOutputBuffer]


def _ignore_progress(_progress: PdfExportProgress) -> None:
    return


@dataclass(frozen=True, slots=True)
class SearchablePdfExportBrokerPolicy:
    """Trusted upper bounds for one mixed searchable-output attempt."""

    limits: ResourceLimits
    status_capacity_bytes: int = MAX_SEARCHABLE_PDF_EXPORT_STATUS_BYTES
    pump_interval_seconds: float = 0.050

    def __post_init__(self) -> None:
        interval = self.pump_interval_seconds
        if (
            type(self.limits) is not ResourceLimits
            or type(self.status_capacity_bytes) is not int
            or not 1 <= self.status_capacity_bytes <= MAX_SEARCHABLE_PDF_EXPORT_STATUS_BYTES
            or type(interval) is not float
            or not math.isfinite(interval)
            or not 0 < interval <= 1.0
            or self.limits.page_count > MAX_SEARCHABLE_PDF_PAGES
            or self.limits.pixel_count < PDF_EXPORT_VERIFICATION_PIXELS
            or self.limits.memory_bytes < PDF_EXPORT_VERIFICATION_RGBA_BYTES
            or self.limits.output_bytes >= self.limits.temporary_storage_bytes
            or self.status_capacity_bytes
            > self.limits.temporary_storage_bytes - self.limits.output_bytes
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "searchable.broker_policy")


class BrokeredSearchablePdfExportCandidate(
    AbstractContextManager["BrokeredSearchablePdfExportCandidate"]
):
    """Single-pass view over an authenticated searchable PDF candidate."""

    __slots__ = (
        "_buffer",
        "_byte_size",
        "_chunks",
        "_closed",
        "_pending",
        "_position",
        "maximum_output_bytes",
        "output_object_id",
        "report",
        "status_object_id",
    )

    def __init__(
        self,
        buffer: PdfExportOutputBuffer,
        *,
        output_object_id: UUID,
        status_object_id: UUID,
        report: SearchablePdfVerificationReport,
        maximum_output_bytes: int,
    ) -> None:
        if (
            not callable(getattr(buffer, "iter_chunks", None))
            or not callable(getattr(buffer, "close", None))
            or type(output_object_id) is not UUID
            or output_object_id.version != 4
            or type(status_object_id) is not UUID
            or status_object_id.version != 4
            or output_object_id == status_object_id
            or type(report) is not SearchablePdfVerificationReport
            or type(maximum_output_bytes) is not int
            or not report.base_report.output_byte_size <= maximum_output_bytes <= buffer.capacity
        ):
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "searchable.candidate")
        self._buffer = buffer
        self._byte_size = report.base_report.output_byte_size
        self._chunks = buffer.iter_chunks(
            self._byte_size,
            chunk_bytes=MAX_PDF_EXPORT_CANDIDATE_READ_BYTES,
        )
        self._closed = False
        self._pending = b""
        self._position = 0
        self.output_object_id = output_object_id
        self.status_object_id = status_object_id
        self.report = report
        self.maximum_output_bytes = maximum_output_bytes

    def __repr__(self) -> str:
        return (
            "BrokeredSearchablePdfExportCandidate("
            f"byte_size={self._byte_size}, page_count={self.report.plan.page_count}, "
            f"maximum_output_bytes={self.maximum_output_bytes})"
        )

    def __enter__(self) -> Self:
        if self._closed:
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "searchable.candidate_closed")
        return self

    def __exit__(self, *args: object) -> None:
        del args
        self.close()

    def read(self, size: int, /) -> bytes:
        if self._closed:
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "searchable.candidate_closed")
        if type(size) is not int or not 1 <= size <= MAX_PDF_EXPORT_CANDIDATE_READ_BYTES:
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "searchable.candidate_read")
        remaining = self._byte_size - self._position
        if remaining == 0:
            return b""
        requested = min(size, remaining)
        parts: list[bytes] = []
        completed = 0
        while completed < requested:
            if self._pending:
                chunk = self._pending
                self._pending = b""
            else:
                try:
                    chunk = next(self._chunks)
                except StopIteration:
                    reject_pdf_export(
                        PdfExportErrorCode.RESULT_INVALID,
                        "searchable.candidate_short",
                    )
                except PdfExportError:
                    raise
                except Exception:
                    reject_pdf_export(
                        PdfExportErrorCode.RESULT_INVALID,
                        "searchable.candidate_read",
                    )
            if type(chunk) is not bytes or not chunk:
                reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "searchable.candidate_chunk")
            if len(chunk) > self._byte_size - self._position - completed:
                reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "searchable.candidate_chunk")
            needed = requested - completed
            if len(chunk) > needed:
                parts.append(chunk[:needed])
                self._pending = chunk[needed:]
                completed += needed
            else:
                parts.append(chunk)
                completed += len(chunk)
        self._position += completed
        return b"".join(parts)

    def close(self) -> None:
        if self._closed:
            return
        self._buffer.close()
        self._closed = True
        self._pending = b""


class BrokeredSearchablePdfExporter:
    """Run one mixed-source worker attempt and expose only verified bytes."""

    __slots__ = (
        "_heavy_operations",
        "_id_factory",
        "_output_buffer_factory",
        "_pipe_factory",
        "_plan_buffer_factory",
        "_policy",
        "_revoked",
        "_status_buffer_factory",
        "_worker",
    )

    def __init__(
        self,
        worker: PdfExportWorkerCoordinator,
        policy: SearchablePdfExportBrokerPolicy,
        *,
        heavy_operations: HeavyOperationCoordinator,
        pipe_factory: ResultPipeFactory = WindowsResultPipe.create,
        plan_buffer_factory: PlanBufferFactory = WindowsPdfExportPlanBuffer.create,
        output_buffer_factory: OutputBufferFactory = WindowsPdfExportOutputBuffer.create,
        status_buffer_factory: OutputBufferFactory = WindowsPdfExportOutputBuffer.create,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if (
            type(policy) is not SearchablePdfExportBrokerPolicy
            or type(heavy_operations) is not HeavyOperationCoordinator
            or not callable(pipe_factory)
            or not callable(plan_buffer_factory)
            or not callable(output_buffer_factory)
            or not callable(status_buffer_factory)
            or not callable(id_factory)
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "searchable.broker_config")
        self._worker = worker
        self._policy = policy
        self._heavy_operations = heavy_operations
        self._pipe_factory = pipe_factory
        self._plan_buffer_factory = plan_buffer_factory
        self._output_buffer_factory = output_buffer_factory
        self._status_buffer_factory = status_buffer_factory
        self._id_factory = id_factory
        self._revoked = False

    @property
    def revoked(self) -> bool:
        return self._revoked

    @property
    def maximum_output_bytes(self) -> int:
        """Expose the exact destination preflight bound used by this broker."""

        return self._policy.limits.output_bytes

    def _revoke_permanently(self) -> None:
        self._revoked = True

    def export(
        self,
        plan: SearchablePdfExportPlan,
        sources: SearchablePdfExportSourceBatchLease,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: SearchablePdfExportProgressSink = _ignore_progress,
        reserved_temporary_bytes: int = 0,
    ) -> BrokeredSearchablePdfExportCandidate:
        if self._revoked:
            reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "searchable.composition_closed")
        try:
            with self._acquire_heavy_operation(deadline, cancellation):
                return self._export(
                    plan,
                    sources,
                    deadline=deadline,
                    cancellation=cancellation,
                    progress=progress,
                    reserved_temporary_bytes=reserved_temporary_bytes,
                )
        except PdfExportError:
            raise
        except SearchablePdfExportError as error:
            _raise_searchable_error(error)
        except SourceIngestError as error:
            _raise_source_error(error)
        except WorkerClientError as error:
            if error.code in {
                WorkerClientErrorCode.HANDSHAKE_TIMED_OUT,
                WorkerClientErrorCode.OPERATION_TIMED_OUT,
            }:
                reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "searchable.worker_timeout")
            reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "searchable.worker")
        except Exception:
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "searchable.broker")

    def _acquire_heavy_operation(
        self,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> HeavyOperationLease:
        while True:
            lease = self._heavy_operations.try_acquire(self._policy.pump_interval_seconds)
            if lease is not None:
                return lease
            _require_active(deadline, cancellation)

    def _export(
        self,
        plan: SearchablePdfExportPlan,
        sources: SearchablePdfExportSourceBatchLease,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: SearchablePdfExportProgressSink,
        reserved_temporary_bytes: int,
    ) -> BrokeredSearchablePdfExportCandidate:
        if type(plan) is not SearchablePdfExportPlan or not callable(progress):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "searchable.broker_input")
        _require_active(deadline, cancellation)
        local_handles = _require_source_binding(plan, sources)
        limits = _operation_limits(
            self._policy, plan, reserved_temporary_bytes=reserved_temporary_bytes
        )
        forbidden = {
            plan.case_id,
            *(source.source_id for source in plan.sources),
            *(page.page_id for page in plan.pages),
        }
        operation_id = self._new_id(forbidden)
        forbidden.add(operation_id)
        plan_object_id = self._new_id(forbidden)
        forbidden.add(plan_object_id)
        output_object_id = self._new_id(forbidden)
        forbidden.add(output_object_id)
        status_object_id = self._new_id(forbidden)

        output_buffer = self._output_buffer_factory(limits.output_bytes)
        status_buffer: PdfExportOutputBuffer | None = None
        transferred = False
        try:
            _require_output_buffer(output_buffer, limits.output_bytes, "searchable.output_buffer")
            status_buffer = self._status_buffer_factory(self._policy.status_capacity_bytes)
            _require_output_buffer(
                status_buffer,
                self._policy.status_capacity_bytes,
                "searchable.status_buffer",
            )
            if (
                status_buffer is output_buffer
                or status_buffer.write_handle_value == output_buffer.write_handle_value
            ):
                reject_pdf_export(
                    PdfExportErrorCode.RESULT_INVALID,
                    "searchable.output_sections",
                )
            result: WorkerOperationResultSnapshot | None = None
            generation: int | None = None
            start_attempted = False
            with self._pipe_factory() as pipe:
                try:
                    _require_active(deadline, cancellation)
                    start_attempted = True
                    generation = self._worker.start(plan.case_id)
                    ready = self._wait_ready(
                        plan.case_id,
                        generation,
                        deadline=deadline,
                        cancellation=cancellation,
                    )
                    if SEARCHABLE_PDF_EXPORT_CAPABILITY not in ready.capabilities:
                        reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "searchable.capability")
                    worker_source_handles = {
                        source.source_id: self._worker.duplicate_handle(
                            local_handles[source.source_id],
                            BrokeredHandlePurpose.READ_SOURCE,
                        )
                        for source in plan.sources
                    }
                    plan_payload = encode_searchable_pdf_worker_plan(
                        plan,
                        worker_source_handles,
                    )
                    plan_digest = hashlib.sha256(plan_payload).hexdigest()
                    with self._plan_buffer_factory(plan_payload) as plan_buffer:
                        if (
                            plan_buffer.payload_size != len(plan_payload)
                            or type(plan_buffer.read_handle_value) is not int
                            or plan_buffer.read_handle_value <= 0
                        ):
                            reject_pdf_export(
                                PdfExportErrorCode.RESULT_INVALID,
                                "searchable.plan_buffer",
                            )
                        plan_handle = self._worker.duplicate_handle(
                            plan_buffer.read_handle_value,
                            BrokeredHandlePurpose.READ_BOUNDED_INPUT_SECTION,
                        )
                        output_handle = self._worker.duplicate_handle(
                            output_buffer.write_handle_value,
                            BrokeredHandlePurpose.WRITE_BOUNDED_OUTPUT_SECTION,
                        )
                        status_handle = self._worker.duplicate_handle(
                            status_buffer.write_handle_value,
                            BrokeredHandlePurpose.WRITE_BOUNDED_OUTPUT_SECTION,
                        )
                        result_handle = self._worker.duplicate_handle(
                            pipe.write_handle_value,
                            BrokeredHandlePurpose.WRITE_RESULT_PIPE,
                        )
                        local_authorities = (
                            *local_handles.values(),
                            plan_buffer.read_handle_value,
                            output_buffer.write_handle_value,
                            status_buffer.write_handle_value,
                            pipe.write_handle_value,
                        )
                        if len(set(local_authorities)) != len(local_authorities):
                            reject_pdf_export(
                                PdfExportErrorCode.RESULT_INVALID,
                                "searchable.local_handles",
                            )
                        all_worker_handles = (
                            *worker_source_handles.values(),
                            plan_handle,
                            output_handle,
                            status_handle,
                            result_handle,
                        )
                        if any(
                            type(handle) is not int or handle <= 0 for handle in all_worker_handles
                        ) or len(set(all_worker_handles)) != len(all_worker_handles):
                            reject_pdf_export(
                                PdfExportErrorCode.RESULT_INVALID,
                                "searchable.worker_handles",
                            )
                        pipe.close_local_writer()
                        payload = _operation_payload(
                            plan,
                            plan_object_id=plan_object_id,
                            output_object_id=output_object_id,
                            status_object_id=status_object_id,
                            plan_byte_size=len(plan_payload),
                            plan_sha256=plan_digest,
                            plan_handle=plan_handle,
                            output_handle=output_handle,
                            status_handle=status_handle,
                            result_handle=result_handle,
                            status_capacity_bytes=self._policy.status_capacity_bytes,
                            limits=limits,
                        )
                        selected = self._worker.start_operation(
                            payload,
                            operation_id=operation_id,
                        )
                        if selected != operation_id:
                            reject_pdf_export(
                                PdfExportErrorCode.REPORT_INVALID,
                                "searchable.operation_binding",
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
                    if start_attempted:
                        self._worker.terminate()

                if result is None or generation is None:
                    reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "searchable.no_result")
                output, status = _require_successful_outputs(
                    result,
                    generation=generation,
                    operation_id=operation_id,
                    output_object_id=output_object_id,
                    status_object_id=status_object_id,
                    maximum_output_bytes=limits.output_bytes,
                    maximum_status_bytes=self._policy.status_capacity_bytes,
                )
                _require_source_binding(plan, sources)
                sources.revalidate(deadline=deadline, cancellation=cancellation)
                _require_source_binding(plan, sources)
                _require_active(deadline, cancellation)
                try:
                    report_payload = pipe.read_complete_frame()
                except PdfExportError:
                    raise
                except Exception:
                    reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "searchable.report_read")
                worker_report = decode_searchable_pdf_worker_report(report_payload)
                _require_worker_report(
                    worker_report,
                    plan=plan,
                    operation_id=operation_id,
                    output_object_id=output_object_id,
                    status_object_id=status_object_id,
                    output=output,
                    status=status,
                )
                _authenticate_buffer(
                    output_buffer,
                    byte_size=worker_report.base_report.report.output_byte_size,
                    sha256=worker_report.base_report.report.output_sha256,
                    deadline=deadline,
                    cancellation=cancellation,
                    capture=False,
                    context="searchable.output",
                )
                status_payload = _authenticate_buffer(
                    status_buffer,
                    byte_size=worker_report.status_byte_size,
                    sha256=worker_report.status_sha256,
                    deadline=deadline,
                    cancellation=cancellation,
                    capture=True,
                    context="searchable.status",
                )
                if status_payload is None:
                    reject_pdf_export(
                        PdfExportErrorCode.RESULT_INVALID,
                        "searchable.status_payload",
                    )
                status_document = decode_searchable_pdf_page_status(status_payload)
                report = _require_status_and_build_report(
                    worker_report,
                    status_document,
                    plan=plan,
                    operation_id=operation_id,
                    status_object_id=status_object_id,
                )
                status_buffer.close()
                status_buffer = None
                candidate = BrokeredSearchablePdfExportCandidate(
                    output_buffer,
                    output_object_id=output_object_id,
                    status_object_id=status_object_id,
                    report=report,
                    maximum_output_bytes=limits.output_bytes,
                )
                transferred = True
                return candidate
        finally:
            cleanup_failure: tuple[PdfExportErrorCode, str] | None = None
            if status_buffer is not None:
                try:
                    status_buffer.close()
                except BaseException as error:
                    cleanup_failure = _buffer_cleanup_failure(
                        error,
                        "searchable.status_cleanup",
                    )
            if not transferred:
                try:
                    output_buffer.close()
                except BaseException as error:
                    if cleanup_failure is None:
                        cleanup_failure = _buffer_cleanup_failure(
                            error,
                            "searchable.output_cleanup",
                        )
            if cleanup_failure is not None:
                reject_pdf_export(*cleanup_failure)

    def _wait_ready(
        self,
        case_id: UUID,
        generation: int,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> WorkerClientSnapshot:
        if type(generation) is not int or generation < 1:
            reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "searchable.generation")
        while True:
            _require_active(deadline, cancellation)
            snapshot = self._worker.query()
            if snapshot.generation != generation:
                reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "searchable.generation")
            if snapshot.phase is WorkerLifecyclePhase.READY:
                if snapshot.case_id != case_id:
                    reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "searchable.case_binding")
                return snapshot
            if snapshot.phase in {WorkerLifecyclePhase.STOPPED, WorkerLifecyclePhase.FAULTED}:
                reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "searchable.handshake")
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
        progress: SearchablePdfExportProgressSink,
    ) -> WorkerOperationResultSnapshot:
        cancellation_sent = False
        last_progress: PdfExportProgress | None = None
        while True:
            snapshot = self._worker.query()
            if snapshot.generation != generation:
                reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "searchable.generation")
            result = snapshot.last_result
            if result is not None:
                if result.operation_id != operation_id:
                    reject_pdf_export(
                        PdfExportErrorCode.REPORT_INVALID, "searchable.result_binding"
                    )
                if cancellation_sent and result.outcome is OperationOutcome.SUCCEEDED:
                    reject_pdf_export(PdfExportErrorCode.CANCELLED, "searchable.cancelled")
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
                            "searchable.progress_binding",
                        )
                    domain_progress = PdfExportProgress(
                        PdfExportProgressPhase.ASSEMBLING_AND_VERIFYING,
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
                reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "searchable.worker_exit")
            if _deadline_expired(deadline):
                if not cancellation_sent:
                    self._worker.cancel(CancelReason.SHUTDOWN)
                reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "searchable.deadline")
            if _cancellation_requested(cancellation) and not cancellation_sent:
                # WorkerClient.cancel() returns the new cancel request ID. The
                # client itself binds that request to its single authenticated
                # active operation; result correlation below remains against the
                # original operation ID.
                self._worker.cancel(CancelReason.USER_REQUESTED)
                cancellation_sent = True
            self._worker.pump(self._policy.pump_interval_seconds)

    def _new_id(self, forbidden: set[UUID]) -> UUID:
        for _ in range(_MAXIMUM_IDENTIFIER_ATTEMPTS):
            try:
                value = self._id_factory()
            except BaseException:
                reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "searchable.identifier")
            if type(value) is UUID and value.version == 4 and value not in forbidden:
                return value
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "searchable.identifier")


def _require_source_binding(
    plan: SearchablePdfExportPlan,
    sources: SearchablePdfExportSourceBatchLease,
) -> dict[UUID, int]:
    try:
        if (
            sources.case_id != plan.case_id
            or sources.manifest_revision != plan.manifest_revision
            or sources.manifest_sha256 != plan.manifest_sha256
            or sources.manifest_cas_token != plan.manifest_cas_token
        ):
            reject_pdf_export(PdfExportErrorCode.PLAN_STALE, "searchable.source_manifest")
        leased_sources = sources.sources
        if type(leased_sources) is not tuple or len(leased_sources) != len(plan.sources):
            reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "searchable.source_batch")
        handles: dict[UUID, int] = {}
        for expected, leased in zip(plan.sources, leased_sources, strict=True):
            if (
                leased.source_id != expected.source_id
                or leased.media_type is not expected.media_type
                or leased.byte_size != expected.byte_size
                or leased.page_count != expected.page_count
                or leased.sha256 != expected.sha256
                or leased.signature_state is not expected.signature_state
                or leased.encryption_state is not expected.encryption_state
                or type(leased.handle_value) is not int
                or leased.handle_value <= 0
            ):
                reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "searchable.source_binding")
            handles[expected.source_id] = leased.handle_value
        if len(set(handles.values())) != len(handles):
            reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "searchable.source_handles")
        return handles
    except PdfExportError, SourceIngestError:
        raise
    except Exception:
        reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "searchable.source_binding")


def _operation_limits(
    policy: SearchablePdfExportBrokerPolicy,
    plan: SearchablePdfExportPlan,
    *,
    reserved_temporary_bytes: int = 0,
) -> ResourceLimits:
    if type(reserved_temporary_bytes) is not int or reserved_temporary_bytes < 0:
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "searchable.temp_reservation")
    remaining = policy.limits.temporary_storage_bytes - reserved_temporary_bytes
    if remaining <= 0:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "searchable.temp_reservation")
    source_bytes = sum(source.byte_size for source in plan.sources)
    required_page_capacity = max(plan.page_count, *(source.page_count for source in plan.sources))
    if (
        required_page_capacity > policy.limits.page_count
        or source_bytes + policy.status_capacity_bytes >= remaining
    ):
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "searchable.operation_limits")
    plan_bytes = len(
        encode_searchable_pdf_worker_plan(
            plan,
            {
                source.source_id: MAX_SAFE_MANIFEST_INTEGER - index
                for index, source in enumerate(plan.sources)
            },
        )
    )
    available = remaining - source_bytes - policy.status_capacity_bytes - plan_bytes
    # Two copies cover the worker PDF and its shared section (then the section
    # and destination stage). A third share leaves sequential page/OCR scratch.
    output_bytes = min(policy.limits.output_bytes, available // 3)
    if output_bytes < MAX_PDF_INSPECTION_REPORT_BYTES:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "searchable.operation_limits")
    limits = replace(policy.limits, temporary_storage_bytes=remaining, output_bytes=output_bytes)
    return ResourceLimits(
        wall_time_ms=limits.wall_time_ms,
        cpu_time_ms=limits.cpu_time_ms,
        memory_bytes=limits.memory_bytes,
        temporary_storage_bytes=limits.temporary_storage_bytes,
        output_bytes=limits.output_bytes,
        # Parsing a retained source needs its full page capacity even when only
        # selected pages are exported. Exact output count stays bound to plan.
        page_count=required_page_capacity,
        pixel_count=limits.pixel_count,
    )


def _operation_payload(
    plan: SearchablePdfExportPlan,
    *,
    plan_object_id: UUID,
    output_object_id: UUID,
    status_object_id: UUID,
    plan_byte_size: int,
    plan_sha256: str,
    plan_handle: int,
    output_handle: int,
    status_handle: int,
    result_handle: int,
    status_capacity_bytes: int,
    limits: ResourceLimits,
) -> OperationStartPayload:
    content_kinds = {
        SearchablePdfSourceMediaType.PDF: "pdf",
        SearchablePdfSourceMediaType.JPEG: "jpeg",
        SearchablePdfSourceMediaType.PNG: "png",
    }
    return OperationStartPayload(
        operation_kind=SEARCHABLE_PDF_EXPORT_CAPABILITY.capability_id,
        operation_version=SEARCHABLE_PDF_EXPORT_CAPABILITY.capability_version,
        inputs=(
            CaseObjectReference(
                area=ReferenceArea.SOURCE_READ,
                object_id=plan_object_id,
                operation_id=None,
                content_kind=SEARCHABLE_PDF_EXPORT_PLAN_CONTENT_KIND,
                expected_byte_size=plan_byte_size,
                expected_sha256=plan_sha256,
            ),
            *(
                CaseObjectReference(
                    area=ReferenceArea.SOURCE_READ,
                    object_id=source.source_id,
                    operation_id=None,
                    content_kind=content_kinds[source.media_type],
                    expected_byte_size=source.byte_size,
                    expected_sha256=source.sha256,
                )
                for source in plan.sources
            ),
        ),
        parameters=(
            TaggedScalar(
                SEARCHABLE_PDF_EXPORT_PLAN_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                plan_handle,
            ),
            TaggedScalar(
                SEARCHABLE_PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                output_handle,
            ),
            TaggedScalar(
                SEARCHABLE_PDF_EXPORT_STATUS_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                status_handle,
            ),
            TaggedScalar(
                SEARCHABLE_PDF_EXPORT_RESULT_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                result_handle,
            ),
            TaggedScalar(
                SEARCHABLE_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
                ScalarValueType.UUID,
                output_object_id,
            ),
            TaggedScalar(
                SEARCHABLE_PDF_EXPORT_STATUS_OBJECT_ID_PARAMETER,
                ScalarValueType.UUID,
                status_object_id,
            ),
            TaggedScalar(
                SEARCHABLE_PDF_EXPORT_STATUS_CAPACITY_PARAMETER,
                ScalarValueType.INTEGER,
                status_capacity_bytes,
            ),
        ),
        requested_limits=limits,
    )


def _require_successful_outputs(
    result: WorkerOperationResultSnapshot,
    *,
    generation: int,
    operation_id: UUID,
    output_object_id: UUID,
    status_object_id: UUID,
    maximum_output_bytes: int,
    maximum_status_bytes: int,
) -> tuple[CaseObjectReference, CaseObjectReference]:
    if (
        type(result) is not WorkerOperationResultSnapshot
        or result.generation != generation
        or result.operation_id != operation_id
    ):
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "searchable.worker_result")
    if result.outcome is OperationOutcome.CANCELLED:
        reject_pdf_export(PdfExportErrorCode.CANCELLED, "searchable.worker_result")
    if result.outcome is OperationOutcome.TIMED_OUT:
        reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "searchable.worker_result")
    if (
        result.outcome is OperationOutcome.FAILED
        and result.error_code is ErrorCode.WORKER_OPERATION_FAILED
        and result.error_category is ErrorCategory.RESOURCE_LIMIT
    ):
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "searchable.worker_resource_limit")
    if result.outcome is not OperationOutcome.SUCCEEDED or len(result.outputs) != 2:
        reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "searchable.worker_result")
    output, status = result.outputs
    if (
        output.area is not ReferenceArea.OPERATION_STAGING
        or output.object_id != output_object_id
        or output.operation_id != operation_id
        or output.content_kind != SEARCHABLE_PDF_EXPORT_OUTPUT_CONTENT_KIND
        or output.expected_byte_size is None
        or not 1 <= output.expected_byte_size <= maximum_output_bytes
        or output.expected_sha256 is None
        or status.area is not ReferenceArea.OPERATION_STAGING
        or status.object_id != status_object_id
        or status.operation_id != operation_id
        or status.content_kind != SEARCHABLE_PDF_EXPORT_STATUS_CONTENT_KIND
        or status.expected_byte_size is None
        or not 1 <= status.expected_byte_size <= maximum_status_bytes
        or status.expected_sha256 is None
    ):
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "searchable.worker_outputs")
    return output, status


def _require_worker_report(
    value: SearchablePdfWorkerReport,
    *,
    plan: SearchablePdfExportPlan,
    operation_id: UUID,
    output_object_id: UUID,
    status_object_id: UUID,
    output: CaseObjectReference,
    status: CaseObjectReference,
) -> None:
    report = value.base_report.report
    if (
        value.operation_id != operation_id
        or value.output_object_id != output_object_id
        or value.status_object_id != status_object_id
        or report.semantic_fingerprint != plan.semantic_fingerprint
        or report.page_count != plan.page_count
        or report.unsigned_derivative != plan.requires_unsigned_derivative
        or output.expected_byte_size != report.output_byte_size
        or output.expected_sha256 is None
        or not hmac.compare_digest(output.expected_sha256, report.output_sha256)
        or status.expected_byte_size != value.status_byte_size
        or status.expected_sha256 is None
        or not hmac.compare_digest(status.expected_sha256, value.status_sha256)
    ):
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "searchable.report_binding")


def _require_status_and_build_report(
    worker_report: SearchablePdfWorkerReport,
    status: SearchablePdfPageStatusDocument,
    *,
    plan: SearchablePdfExportPlan,
    operation_id: UUID,
    status_object_id: UUID,
) -> SearchablePdfVerificationReport:
    if (
        status.operation_id != operation_id
        or status.status_object_id != status_object_id
        or status.semantic_fingerprint != plan.semantic_fingerprint
        or len(status.page_outcomes) != plan.page_count
    ):
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "searchable.status_binding")
    try:
        return SearchablePdfVerificationReport(
            plan,
            worker_report.base_report.report,
            status.page_outcomes,
        )
    except SearchablePdfExportError:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "searchable.status_report")


def _authenticate_buffer(
    buffer: PdfExportOutputBuffer,
    *,
    byte_size: int,
    sha256: str,
    deadline: DeadlineProbe,
    cancellation: CancellationProbe,
    capture: bool,
    context: str,
) -> bytes | None:
    digest = hashlib.sha256()
    completed = 0
    parts: list[bytes] | None = [] if capture else None
    try:
        for chunk in buffer.iter_chunks(
            byte_size,
            chunk_bytes=MAX_PDF_EXPORT_CANDIDATE_READ_BYTES,
        ):
            _require_active(deadline, cancellation)
            if type(chunk) is not bytes or not chunk or len(chunk) > byte_size - completed:
                reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, f"{context}_chunk")
            completed += len(chunk)
            digest.update(chunk)
            if parts is not None:
                parts.append(chunk)
    except PdfExportError:
        raise
    except Exception:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, f"{context}_read")
    _require_active(deadline, cancellation)
    if completed != byte_size or not hmac.compare_digest(digest.hexdigest(), sha256):
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, f"{context}_digest")
    return None if parts is None else b"".join(parts)


def _require_output_buffer(buffer: PdfExportOutputBuffer, capacity: int, context: str) -> None:
    if (
        type(buffer.capacity) is not int
        or buffer.capacity != capacity
        or type(buffer.write_handle_value) is not int
        or buffer.write_handle_value <= 0
    ):
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, context)


def _buffer_cleanup_failure(
    error: BaseException,
    default_context: str,
) -> tuple[PdfExportErrorCode, str]:
    """Reduce one hostile cleanup failure to closed, content-free facts."""

    if type(error) is PdfExportError:
        return error.code, error.context
    return PdfExportErrorCode.INTERNAL_ERROR, default_context


def _require_active(deadline: DeadlineProbe, cancellation: CancellationProbe) -> None:
    if _deadline_expired(deadline):
        reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "searchable.deadline")
    if _cancellation_requested(cancellation):
        reject_pdf_export(PdfExportErrorCode.CANCELLED, "searchable.cancelled")


def _deadline_expired(probe: DeadlineProbe) -> bool:
    try:
        value = probe()
    except SourceIngestError:
        raise
    except BaseException:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "searchable.deadline_probe")
    if type(value) is not bool:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "searchable.deadline_probe")
    return value


def _cancellation_requested(probe: CancellationProbe) -> bool:
    try:
        value = probe()
    except SourceIngestError:
        raise
    except BaseException:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "searchable.cancel_probe")
    if type(value) is not bool:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "searchable.cancel_probe")
    return value


def _report_progress(
    sink: SearchablePdfExportProgressSink,
    progress: PdfExportProgress,
) -> None:
    try:
        sink(progress)
    except PdfExportError:
        raise
    except BaseException:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "searchable.progress")


def _raise_source_error(error: SourceIngestError) -> Never:
    if error.code is SourceIngestErrorCode.CANCELLED:
        reject_pdf_export(PdfExportErrorCode.CANCELLED, "searchable.source")
    if error.code is SourceIngestErrorCode.TIMED_OUT:
        reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "searchable.source")
    if error.code is SourceIngestErrorCode.CAPACITY_EXCEEDED:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "searchable.source")
    reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "searchable.source")


def _raise_searchable_error(error: SearchablePdfExportError) -> Never:
    if error.code is SearchablePdfExportErrorCode.LIMIT_EXCEEDED:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "searchable.codec")
    if error.code is SearchablePdfExportErrorCode.MODEL_INVALID:
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "searchable.codec")
    if error.code is SearchablePdfExportErrorCode.REPORT_INVALID:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "searchable.codec")
    if error.code is SearchablePdfExportErrorCode.RESULT_INVALID:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "searchable.codec")
    reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "searchable.codec")

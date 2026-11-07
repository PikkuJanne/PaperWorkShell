"""Trusted GUI-process broker for one deterministic, verified PDF export.

The broker never gives a worker a path.  It binds one immutable export plan to
read-only source handles, transfers a read-only plan section and write-only
output/report authorities, terminates the one-shot worker tree, revalidates the
retained sources, and only then exposes a bounded candidate byte stream.
"""

from __future__ import annotations

import hashlib
import hmac
import math
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from types import TracebackType
from typing import Final, Never, Protocol, Self
from uuid import UUID, uuid4

from paperworkshell.domain.pdf_export import (
    MAX_PDF_EXPORT_PAGES,
    PdfExportError,
    PdfExportErrorCode,
    PdfExportPlan,
    PdfExportProgress,
    PdfExportProgressPhase,
    PdfExportSourceEncryptionState,
    PdfExportSourceMediaType,
    PdfExportSourceSignatureState,
    PdfExportVerificationReport,
    reject_pdf_export,
)
from paperworkshell.domain.source_ingest import SourceIngestError, SourceIngestErrorCode
from paperworkshell.worker._windows_broker import WindowsResultPipe, retire_runtime_on_failure
from paperworkshell.worker.client.errors import WorkerClientError, WorkerClientErrorCode
from paperworkshell.worker.client.models import (
    WorkerClientSnapshot,
    WorkerLifecyclePhase,
    WorkerOperationResultSnapshot,
)
from paperworkshell.worker.client.process import BrokeredHandlePurpose
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator, HeavyOperationLease
from paperworkshell.worker.pdf_export_buffers import (
    WindowsPdfExportOutputBuffer,
    WindowsPdfExportPlanBuffer,
)
from paperworkshell.worker.pdf_export_plan import encode_pdf_export_worker_plan
from paperworkshell.worker.pdf_export_report import (
    PdfExportWorkerReport,
    decode_pdf_export_worker_report,
)
from paperworkshell.worker.protocol.models import (
    IMAGE_PDF_EXPORT_CAPABILITY,
    IMAGE_PDF_EXPORT_PLAN_CONTENT_KIND,
    PDF_EXPORT_CAPABILITY,
    PDF_EXPORT_OUTPUT_CONTENT_KIND,
    PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
    PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
    PDF_EXPORT_PLAN_CONTENT_KIND,
    PDF_EXPORT_PLAN_HANDLE_PARAMETER,
    PDF_EXPORT_RESULT_HANDLE_PARAMETER,
    PORTAL_PDF_EXPORT_CAPABILITY,
    PORTAL_PDF_EXPORT_PLAN_CONTENT_KIND,
    CancelReason,
    CaseObjectReference,
    OperationOutcome,
    OperationStartPayload,
    ProgressStage,
    ProgressUnit,
    ReferenceArea,
    ResourceLimits,
    ScalarValueType,
    TaggedScalar,
)

PDF_EXPORT_VERIFICATION_WIDTH_PIXELS: Final = 1_024
PDF_EXPORT_VERIFICATION_HEIGHT_PIXELS: Final = 1_024
PDF_EXPORT_VERIFICATION_PIXELS: Final = (
    PDF_EXPORT_VERIFICATION_WIDTH_PIXELS * PDF_EXPORT_VERIFICATION_HEIGHT_PIXELS
)
PDF_EXPORT_VERIFICATION_RGBA_BYTES: Final = PDF_EXPORT_VERIFICATION_PIXELS * 4
MAX_PDF_EXPORT_CANDIDATE_READ_BYTES: Final = 1_024 * 1_024
_MAXIMUM_IDENTIFIER_ATTEMPTS: Final = 32


class DeadlineProbe(Protocol):
    """Return whether the enclosing export deadline has expired."""

    def __call__(self) -> bool: ...


class CancellationProbe(Protocol):
    """Return whether cooperative cancellation was requested."""

    def __call__(self) -> bool: ...


class PdfExportSourceLease(Protocol):
    """Path-free retained authority for one exact committed PDF source."""

    @property
    def source_id(self) -> UUID: ...

    @property
    def media_type(self) -> PdfExportSourceMediaType: ...

    @property
    def byte_size(self) -> int: ...

    @property
    def page_count(self) -> int: ...

    @property
    def sha256(self) -> str: ...

    @property
    def signature_state(self) -> PdfExportSourceSignatureState: ...

    @property
    def encryption_state(self) -> PdfExportSourceEncryptionState: ...

    @property
    def handle_value(self) -> int: ...


class PdfExportSourceBatchLease(Protocol):
    """Manifest-bound retained source batch consumed by one broker attempt."""

    @property
    def case_id(self) -> UUID: ...

    @property
    def manifest_revision(self) -> int: ...

    @property
    def manifest_sha256(self) -> str: ...

    @property
    def manifest_cas_token(self) -> str: ...

    @property
    def sources(self) -> tuple[PdfExportSourceLease, ...]: ...

    def revalidate(
        self,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> None: ...


class PdfExportWorkerCoordinator(Protocol):
    """Narrow single-owner worker client surface used by this broker."""

    def start(self, case_id: UUID) -> int: ...

    def query(self) -> WorkerClientSnapshot: ...

    def pump(self, timeout_seconds: float = 0.0) -> WorkerClientSnapshot: ...

    def duplicate_handle(
        self,
        source_handle: int,
        purpose: BrokeredHandlePurpose,
    ) -> int: ...

    def start_operation(
        self,
        payload: OperationStartPayload,
        *,
        operation_id: UUID | None = None,
    ) -> UUID: ...

    def cancel(self, reason: CancelReason = CancelReason.USER_REQUESTED) -> UUID: ...

    def terminate(self) -> None: ...


class PdfExportProgressSink(Protocol):
    """Receive validated numeric progress from the worker client."""

    def __call__(self, progress: PdfExportProgress, /) -> None: ...


class PdfExportResultPipe(Protocol):
    """Broker-owned bounded report channel."""

    @property
    def write_handle_value(self) -> int: ...

    def close_local_writer(self) -> None: ...

    def read_complete_frame(self) -> bytes: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class PdfExportPlanBuffer(Protocol):
    """Broker-owned plan section transferred read-only to the worker."""

    @property
    def payload_size(self) -> int: ...

    @property
    def read_handle_value(self) -> int: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class PdfExportOutputBuffer(Protocol):
    """Broker-owned exact-capacity candidate section."""

    @property
    def capacity(self) -> int: ...

    @property
    def write_handle_value(self) -> int: ...

    def iter_chunks(self, byte_count: int, *, chunk_bytes: int) -> Iterator[bytes]: ...

    def close(self) -> None: ...


ResultPipeFactory = Callable[[], PdfExportResultPipe]
PlanBufferFactory = Callable[[bytes], PdfExportPlanBuffer]
OutputBufferFactory = Callable[[int], PdfExportOutputBuffer]


def _ignore_progress(_progress: PdfExportProgress) -> None:
    return


@dataclass(frozen=True, slots=True)
class PdfExportBrokerPolicy:
    """Trusted upper bounds and responsiveness for one export attempt."""

    limits: ResourceLimits
    pump_interval_seconds: float = 0.050

    def __post_init__(self) -> None:
        interval = self.pump_interval_seconds
        if (
            type(self.limits) is not ResourceLimits
            or type(interval) is not float
            or not math.isfinite(interval)
            or not 0 < interval <= 1.0
            or self.limits.page_count > MAX_PDF_EXPORT_PAGES
            or self.limits.pixel_count < PDF_EXPORT_VERIFICATION_PIXELS
            or self.limits.memory_bytes < PDF_EXPORT_VERIFICATION_RGBA_BYTES
            or self.limits.output_bytes >= self.limits.temporary_storage_bytes
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.broker_policy")


class BrokeredPdfExportCandidate(AbstractContextManager["BrokeredPdfExportCandidate"]):
    """Single-pass bounded source over one authenticated worker output section."""

    __slots__ = (
        "_buffer",
        "_byte_size",
        "_chunks",
        "_closed",
        "_pending",
        "_position",
        "output_object_id",
        "report",
    )

    def __init__(
        self,
        buffer: PdfExportOutputBuffer,
        *,
        output_object_id: UUID,
        report: PdfExportVerificationReport,
    ) -> None:
        if (
            not callable(getattr(buffer, "iter_chunks", None))
            or not callable(getattr(buffer, "close", None))
            or type(output_object_id) is not UUID
            or output_object_id.version != 4
            or type(report) is not PdfExportVerificationReport
            or buffer.capacity < report.output_byte_size
        ):
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.candidate")
        self._buffer = buffer
        self._byte_size = report.output_byte_size
        self._chunks = buffer.iter_chunks(
            self._byte_size,
            chunk_bytes=MAX_PDF_EXPORT_CANDIDATE_READ_BYTES,
        )
        self._closed = False
        self._pending = b""
        self._position = 0
        self.output_object_id = output_object_id
        self.report = report

    def __repr__(self) -> str:
        return (
            "BrokeredPdfExportCandidate("
            f"byte_size={self._byte_size}, page_count={self.report.page_count})"
        )

    def __enter__(self) -> Self:
        if self._closed:
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.candidate_closed")
        return self

    def __exit__(self, *args: object) -> None:
        del args
        self.close()

    def read(self, size: int, /) -> bytes:
        """Return exactly the requested bounded prefix, or EOF after the report size."""

        if self._closed:
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.candidate_closed")
        if type(size) is not int or not 1 <= size <= MAX_PDF_EXPORT_CANDIDATE_READ_BYTES:
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.candidate_read")
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
                    reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.candidate_short")
                except PdfExportError:
                    raise
                except Exception:
                    reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.candidate_read")
            if type(chunk) is not bytes or not chunk:
                reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.candidate_chunk")
            if len(chunk) > self._byte_size - self._position - completed:
                reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.candidate_chunk")
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


class BrokeredPdfExporter:
    """Run one handle-only PDF export and return only verified bounded bytes."""

    __slots__ = (
        "_id_factory",
        "_heavy_operations",
        "_output_buffer_factory",
        "_pipe_factory",
        "_plan_buffer_factory",
        "_policy",
        "_revoked",
        "_worker",
    )

    def __init__(
        self,
        worker: PdfExportWorkerCoordinator,
        policy: PdfExportBrokerPolicy,
        *,
        heavy_operations: HeavyOperationCoordinator,
        pipe_factory: ResultPipeFactory = WindowsResultPipe.create,
        plan_buffer_factory: PlanBufferFactory = WindowsPdfExportPlanBuffer.create,
        output_buffer_factory: OutputBufferFactory = WindowsPdfExportOutputBuffer.create,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if (
            type(policy) is not PdfExportBrokerPolicy
            or type(heavy_operations) is not HeavyOperationCoordinator
            or not callable(pipe_factory)
            or not callable(plan_buffer_factory)
            or not callable(output_buffer_factory)
            or not callable(id_factory)
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.broker_configuration")
        self._worker = worker
        self._policy = policy
        self._heavy_operations = heavy_operations
        self._pipe_factory = pipe_factory
        self._plan_buffer_factory = plan_buffer_factory
        self._output_buffer_factory = output_buffer_factory
        self._id_factory = id_factory
        self._revoked = False

    @property
    def revoked(self) -> bool:
        """Report whether the owning composition permanently revoked this port."""

        return self._revoked

    def _revoke_permanently(self) -> None:
        """Prevent this already-exposed port from launching another worker."""

        self._revoked = True

    @retire_runtime_on_failure
    def export(
        self,
        plan: PdfExportPlan,
        sources: PdfExportSourceBatchLease,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: PdfExportProgressSink = _ignore_progress,
    ) -> BrokeredPdfExportCandidate:
        """Return a candidate only after worker termination and source revalidation."""

        if self._revoked:
            reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.composition_closed")
        try:
            with self._acquire_heavy_operation(deadline, cancellation):
                return self._export(
                    plan,
                    sources,
                    deadline=deadline,
                    cancellation=cancellation,
                    progress=progress,
                )
        except PdfExportError:
            raise
        except SourceIngestError as error:
            _raise_source_error(error)
        except WorkerClientError as error:
            if error.code in {
                WorkerClientErrorCode.HANDSHAKE_TIMED_OUT,
                WorkerClientErrorCode.OPERATION_TIMED_OUT,
            }:
                reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "export.worker_timeout")
            reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.worker")
        except Exception:
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.broker")

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
        plan: PdfExportPlan,
        sources: PdfExportSourceBatchLease,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: PdfExportProgressSink,
    ) -> BrokeredPdfExportCandidate:
        if type(plan) is not PdfExportPlan or not callable(progress):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.broker_input")
        _require_active(deadline, cancellation)
        local_handles = _require_source_binding(plan, sources)
        limits = _operation_limits(self._policy.limits, plan)
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

        output_buffer = self._output_buffer_factory(limits.output_bytes)
        transferred = False
        try:
            if (
                type(output_buffer.capacity) is not int
                or output_buffer.capacity != limits.output_bytes
                or type(output_buffer.write_handle_value) is not int
                or output_buffer.write_handle_value <= 0
            ):
                reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.output_buffer")
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
                    capability = {
                        1: PDF_EXPORT_CAPABILITY,
                        2: IMAGE_PDF_EXPORT_CAPABILITY,
                        3: PORTAL_PDF_EXPORT_CAPABILITY,
                    }[plan.plan_version]
                    if capability not in ready.capabilities:
                        reject_pdf_export(
                            PdfExportErrorCode.WORKER_FAILED,
                            "export.capability",
                        )
                    worker_source_handles = {
                        source.source_id: self._worker.duplicate_handle(
                            local_handles[source.source_id],
                            BrokeredHandlePurpose.READ_SOURCE,
                        )
                        for source in plan.sources
                    }
                    plan_payload = encode_pdf_export_worker_plan(plan, worker_source_handles)
                    plan_digest = hashlib.sha256(plan_payload).hexdigest()
                    with self._plan_buffer_factory(plan_payload) as plan_buffer:
                        if (
                            plan_buffer.payload_size != len(plan_payload)
                            or type(plan_buffer.read_handle_value) is not int
                            or plan_buffer.read_handle_value <= 0
                        ):
                            reject_pdf_export(
                                PdfExportErrorCode.RESULT_INVALID,
                                "export.plan_buffer",
                            )
                        plan_handle = self._worker.duplicate_handle(
                            plan_buffer.read_handle_value,
                            BrokeredHandlePurpose.READ_BOUNDED_INPUT_SECTION,
                        )
                        output_handle = self._worker.duplicate_handle(
                            output_buffer.write_handle_value,
                            BrokeredHandlePurpose.WRITE_BOUNDED_OUTPUT_SECTION,
                        )
                        result_handle = self._worker.duplicate_handle(
                            pipe.write_handle_value,
                            BrokeredHandlePurpose.WRITE_RESULT_PIPE,
                        )
                        all_worker_handles = (
                            *worker_source_handles.values(),
                            plan_handle,
                            output_handle,
                            result_handle,
                        )
                        if any(
                            type(handle) is not int or handle <= 0 for handle in all_worker_handles
                        ) or len(set(all_worker_handles)) != len(all_worker_handles):
                            reject_pdf_export(
                                PdfExportErrorCode.RESULT_INVALID,
                                "export.worker_handles",
                            )
                        pipe.close_local_writer()
                        payload = _operation_payload(
                            plan,
                            operation_id=operation_id,
                            plan_object_id=plan_object_id,
                            output_object_id=output_object_id,
                            plan_byte_size=len(plan_payload),
                            plan_sha256=plan_digest,
                            plan_handle=plan_handle,
                            output_handle=output_handle,
                            result_handle=result_handle,
                            limits=limits,
                        )
                        selected = self._worker.start_operation(
                            payload,
                            operation_id=operation_id,
                        )
                        if selected != operation_id:
                            reject_pdf_export(
                                PdfExportErrorCode.REPORT_INVALID,
                                "export.operation_binding",
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
                    reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.no_result")
                output = _require_successful_output(
                    result,
                    generation=generation,
                    operation_id=operation_id,
                    output_object_id=output_object_id,
                    maximum_output_bytes=limits.output_bytes,
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
                    reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "export.report_read")
                worker_report = decode_pdf_export_worker_report(report_payload)
                report = _require_report(
                    worker_report,
                    plan=plan,
                    operation_id=operation_id,
                    output_object_id=output_object_id,
                    output=output,
                )
                _authenticate_output_buffer(output_buffer, report)
                candidate = BrokeredPdfExportCandidate(
                    output_buffer,
                    output_object_id=output_object_id,
                    report=report,
                )
                transferred = True
                return candidate
        finally:
            if not transferred:
                output_buffer.close()

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
                selected = self._worker.cancel(CancelReason.USER_REQUESTED)
                if selected != operation_id:
                    reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "export.cancel_binding")
                cancellation_sent = True
            self._worker.pump(self._policy.pump_interval_seconds)

    def _new_id(self, forbidden: set[UUID]) -> UUID:
        for _ in range(_MAXIMUM_IDENTIFIER_ATTEMPTS):
            try:
                value = self._id_factory()
            except BaseException:
                reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.identifier")
            if type(value) is UUID and value.version == 4 and value not in forbidden:
                return value
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.identifier")


def _require_source_binding(
    plan: PdfExportPlan,
    sources: PdfExportSourceBatchLease,
) -> dict[UUID, int]:
    try:
        if (
            sources.case_id != plan.case_id
            or sources.manifest_revision != plan.manifest_revision
            or sources.manifest_sha256 != plan.manifest_sha256
            or sources.manifest_cas_token != plan.manifest_cas_token
        ):
            reject_pdf_export(PdfExportErrorCode.PLAN_STALE, "export.source_manifest")
        leased_sources = sources.sources
        if type(leased_sources) is not tuple or len(leased_sources) != len(plan.sources):
            reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.source_batch")
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
                reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.source_binding")
            handles[expected.source_id] = leased.handle_value
        if len(set(handles.values())) != len(handles):
            reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.source_handles")
        return handles
    except PdfExportError:
        raise
    except SourceIngestError:
        raise
    except Exception:
        reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.source_binding")


def _operation_limits(policy: ResourceLimits, plan: PdfExportPlan) -> ResourceLimits:
    source_bytes = sum(source.byte_size for source in plan.sources)
    required_page_capacity = max(plan.page_count, *(source.page_count for source in plan.sources))
    if (
        required_page_capacity > policy.page_count
        or source_bytes > policy.temporary_storage_bytes
        or policy.output_bytes > policy.temporary_storage_bytes - source_bytes
    ):
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.operation_limits")
    return ResourceLimits(
        wall_time_ms=policy.wall_time_ms,
        cpu_time_ms=policy.cpu_time_ms,
        memory_bytes=policy.memory_bytes,
        temporary_storage_bytes=policy.temporary_storage_bytes,
        output_bytes=policy.output_bytes,
        # A page subset still parses complete retained sources; output count is
        # independently constrained and verified against the immutable plan.
        page_count=required_page_capacity,
        pixel_count=policy.pixel_count
        if plan.plan_version in {2, 3}
        else PDF_EXPORT_VERIFICATION_PIXELS,
    )


def _operation_payload(
    plan: PdfExportPlan,
    *,
    operation_id: UUID,
    plan_object_id: UUID,
    output_object_id: UUID,
    plan_byte_size: int,
    plan_sha256: str,
    plan_handle: int,
    output_handle: int,
    result_handle: int,
    limits: ResourceLimits,
) -> OperationStartPayload:
    return OperationStartPayload(
        operation_kind=PDF_EXPORT_CAPABILITY.capability_id,
        operation_version=plan.plan_version,
        inputs=(
            CaseObjectReference(
                area=ReferenceArea.OPERATION_TEMP,
                object_id=plan_object_id,
                operation_id=operation_id,
                content_kind={
                    1: PDF_EXPORT_PLAN_CONTENT_KIND,
                    2: IMAGE_PDF_EXPORT_PLAN_CONTENT_KIND,
                    3: PORTAL_PDF_EXPORT_PLAN_CONTENT_KIND,
                }[plan.plan_version],
                expected_byte_size=plan_byte_size,
                expected_sha256=plan_sha256,
            ),
            *(
                CaseObjectReference(
                    area=ReferenceArea.SOURCE_READ,
                    object_id=source.source_id,
                    operation_id=None,
                    content_kind={
                        PdfExportSourceMediaType.PDF: "pdf",
                        PdfExportSourceMediaType.JPEG: "jpeg",
                        PdfExportSourceMediaType.PNG: "png",
                    }[source.media_type],
                    expected_byte_size=source.byte_size,
                    expected_sha256=source.sha256,
                )
                for source in plan.sources
            ),
        ),
        parameters=(
            TaggedScalar(
                PDF_EXPORT_PLAN_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                plan_handle,
            ),
            TaggedScalar(
                PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                output_handle,
            ),
            TaggedScalar(
                PDF_EXPORT_RESULT_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                result_handle,
            ),
            TaggedScalar(
                PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
                ScalarValueType.UUID,
                output_object_id,
            ),
        ),
        requested_limits=limits,
    )


def _require_successful_output(
    result: WorkerOperationResultSnapshot,
    *,
    generation: int,
    operation_id: UUID,
    output_object_id: UUID,
    maximum_output_bytes: int,
) -> CaseObjectReference:
    if (
        type(result) is not WorkerOperationResultSnapshot
        or result.generation != generation
        or result.operation_id != operation_id
    ):
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "export.worker_result")
    if result.outcome is OperationOutcome.CANCELLED:
        reject_pdf_export(PdfExportErrorCode.CANCELLED, "export.worker_result")
    if result.outcome is OperationOutcome.TIMED_OUT:
        reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "export.worker_result")
    if result.outcome is not OperationOutcome.SUCCEEDED or len(result.outputs) != 1:
        reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.worker_result")
    output = result.outputs[0]
    if (
        output.area is not ReferenceArea.OPERATION_STAGING
        or output.object_id != output_object_id
        or output.operation_id != operation_id
        or output.content_kind != PDF_EXPORT_OUTPUT_CONTENT_KIND
        or output.expected_byte_size is None
        or not 1 <= output.expected_byte_size <= maximum_output_bytes
        or output.expected_sha256 is None
    ):
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "export.worker_output")
    return output


def _require_report(
    value: PdfExportWorkerReport,
    *,
    plan: PdfExportPlan,
    operation_id: UUID,
    output_object_id: UUID,
    output: CaseObjectReference,
) -> PdfExportVerificationReport:
    report = value.report
    if (
        value.operation_id != operation_id
        or value.output_object_id != output_object_id
        or report.semantic_fingerprint != plan.semantic_fingerprint
        or report.portal_profile_verified != plan.profile.is_portal
        or report.page_count != plan.page_count
        or report.unsigned_derivative != plan.contains_signature_markers
        or output.expected_byte_size != report.output_byte_size
        or output.expected_sha256 is None
        or not hmac.compare_digest(output.expected_sha256, report.output_sha256)
    ):
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "export.report_binding")
    return report


def _authenticate_output_buffer(
    output_buffer: PdfExportOutputBuffer,
    report: PdfExportVerificationReport,
) -> None:
    digest = hashlib.sha256()
    completed = 0
    try:
        for chunk in output_buffer.iter_chunks(
            report.output_byte_size,
            chunk_bytes=MAX_PDF_EXPORT_CANDIDATE_READ_BYTES,
        ):
            if (
                type(chunk) is not bytes
                or not chunk
                or len(chunk) > report.output_byte_size - completed
            ):
                reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.output_chunk")
            completed += len(chunk)
            digest.update(chunk)
    except PdfExportError:
        raise
    except Exception:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.output_read")
    if completed != report.output_byte_size or not hmac.compare_digest(
        digest.hexdigest(),
        report.output_sha256,
    ):
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.output_digest")


def _require_active(deadline: DeadlineProbe, cancellation: CancellationProbe) -> None:
    if _deadline_expired(deadline):
        reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "export.deadline")
    if _cancellation_requested(cancellation):
        reject_pdf_export(PdfExportErrorCode.CANCELLED, "export.cancelled")


def _deadline_expired(probe: DeadlineProbe) -> bool:
    try:
        value = probe()
    except SourceIngestError:
        raise
    except BaseException:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.deadline_probe")
    if type(value) is not bool:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.deadline_probe")
    return value


def _cancellation_requested(probe: CancellationProbe) -> bool:
    try:
        value = probe()
    except SourceIngestError:
        raise
    except BaseException:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.cancel_probe")
    if type(value) is not bool:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.cancel_probe")
    return value


def _report_progress(sink: PdfExportProgressSink, progress: PdfExportProgress) -> None:
    try:
        sink(progress)
    except PdfExportError:
        raise
    except BaseException:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.progress")


def _raise_source_error(error: SourceIngestError) -> Never:
    if error.code is SourceIngestErrorCode.CANCELLED:
        reject_pdf_export(PdfExportErrorCode.CANCELLED, "export.source")
    if error.code is SourceIngestErrorCode.TIMED_OUT:
        reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "export.source")
    if error.code is SourceIngestErrorCode.CAPACITY_EXCEEDED:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.source")
    reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.source")

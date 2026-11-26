"""Trusted GUI-process broker for one overlay-flattened PDF derivative.

The broker owns every transient section and never gives the worker a path. It
copies exact, already-authenticated RGBA8 signature rasters into operation-only
sections, binds those sections and retained PDF source handles into one
versioned plan, terminates the one-shot worker, revalidates the sources, and
only then authenticates and exposes the bounded PDF candidate.
"""

from __future__ import annotations

import hashlib
import hmac
import math
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass, replace
from types import TracebackType
from typing import Final, Never, Protocol, Self, cast
from uuid import UUID, uuid4

from paperworkshell.domain.manifest_repository import MAX_SAFE_MANIFEST_INTEGER
from paperworkshell.domain.pdf_export import (
    MAX_PDF_EXPORT_PAGES,
    PdfExportError,
    PdfExportErrorCode,
    PdfExportProgress,
    PdfExportProgressPhase,
)
from paperworkshell.domain.source_ingest import SourceIngestError, SourceIngestErrorCode
from paperworkshell.overlays.exporting import (
    FlattenedPdfExportError,
    FlattenedPdfExportErrorCode,
    FlattenedPdfExportPlan,
    FlattenedPdfExportVerificationReport,
    reject_flattened_pdf_export,
)
from paperworkshell.overlays.signature_rasters import SignatureImageOverlayRasterSource
from paperworkshell.worker._windows_broker import WindowsResultPipe, retire_runtime_on_failure
from paperworkshell.worker.client.errors import WorkerClientError, WorkerClientErrorCode
from paperworkshell.worker.client.models import (
    WorkerClientSnapshot,
    WorkerLifecyclePhase,
    WorkerOperationResultSnapshot,
)
from paperworkshell.worker.client.process import BrokeredHandlePurpose
from paperworkshell.worker.flattened_pdf_export_buffers import (
    WindowsFlattenedPdfRasterBuffer,
)
from paperworkshell.worker.flattened_pdf_export_plan import (
    encode_flattened_pdf_worker_plan,
)
from paperworkshell.worker.flattened_pdf_export_report import (
    FlattenedPdfExportWorkerReport,
    decode_flattened_pdf_worker_report,
)
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
    PdfExportSourceBatchLease,
    PdfExportWorkerCoordinator,
)
from paperworkshell.worker.pdf_export_buffers import (
    WindowsPdfExportOutputBuffer,
    WindowsPdfExportPlanBuffer,
)
from paperworkshell.worker.protocol.models import (
    FLATTENED_PDF_EXPORT_CAPABILITY,
    FLATTENED_PDF_EXPORT_OUTPUT_CONTENT_KIND,
    FLATTENED_PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
    FLATTENED_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
    FLATTENED_PDF_EXPORT_PLAN_CONTENT_KIND,
    FLATTENED_PDF_EXPORT_PLAN_HANDLE_PARAMETER,
    FLATTENED_PDF_EXPORT_RASTER_CONTENT_KIND,
    FLATTENED_PDF_EXPORT_RESULT_HANDLE_PARAMETER,
    MAX_FLATTENED_PDF_EXPORT_INPUT_REFERENCES,
    CancelReason,
    CaseObjectReference,
    ErrorCategory,
    OperationOutcome,
    OperationStartPayload,
    ProgressStage,
    ProgressUnit,
    ReferenceArea,
    ResourceLimits,
    ScalarValueType,
    TaggedScalar,
)

_MAXIMUM_IDENTIFIER_ATTEMPTS: Final = 32
_RASTER_COPY_CHUNK_BYTES: Final = 1024 * 1024


class FlattenedPdfExportProgressSink(Protocol):
    """Receive only validated numeric progress for the current plan."""

    def __call__(self, progress: PdfExportProgress, /) -> None: ...


class FlattenedPdfRasterBuffer(Protocol):
    """Broker-owned exact RGBA8 section transferred read-only."""

    @property
    def payload_size(self) -> int: ...

    @property
    def payload_sha256(self) -> str: ...

    @property
    def read_handle_value(self) -> int: ...

    def close(self) -> None: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


ResultPipeFactory = Callable[[], PdfExportResultPipe]
PlanBufferFactory = Callable[[bytes], PdfExportPlanBuffer]
OutputBufferFactory = Callable[[int], PdfExportOutputBuffer]
RasterBufferFactory = Callable[[bytes], FlattenedPdfRasterBuffer]


def _ignore_progress(_progress: PdfExportProgress) -> None:
    return


@dataclass(frozen=True, slots=True)
class FlattenedPdfExportBrokerPolicy:
    """Trusted central limits and responsiveness for one flattened export."""

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
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.MODEL_INVALID,
                "flattened.broker_policy",
            )


class BrokeredFlattenedPdfExportCandidate(
    AbstractContextManager["BrokeredFlattenedPdfExportCandidate"]
):
    """Single-pass view over one post-termination authenticated PDF section."""

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
    )

    def __init__(
        self,
        buffer: PdfExportOutputBuffer,
        *,
        output_object_id: UUID,
        report: FlattenedPdfExportVerificationReport,
        maximum_output_bytes: int,
    ) -> None:
        try:
            capacity = buffer.capacity
        except Exception:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.RESULT_INVALID,
                "flattened.candidate",
            )
        if (
            not callable(getattr(buffer, "iter_chunks", None))
            or not callable(getattr(buffer, "close", None))
            or type(output_object_id) is not UUID
            or output_object_id.version != 4
            or type(report) is not FlattenedPdfExportVerificationReport
            or type(maximum_output_bytes) is not int
            or not report.base_report.output_byte_size <= maximum_output_bytes <= capacity
        ):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.RESULT_INVALID,
                "flattened.candidate",
            )
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
        self.report = report
        self.maximum_output_bytes = maximum_output_bytes

    def __repr__(self) -> str:
        return (
            "BrokeredFlattenedPdfExportCandidate("
            f"byte_size={self._byte_size}, page_count={self.report.plan.page_count}, "
            f"maximum_output_bytes={self.maximum_output_bytes})"
        )

    def __enter__(self) -> Self:
        if self._closed:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                "flattened.candidate_closed",
            )
        return self

    def __exit__(self, *args: object) -> None:
        del args
        self.close()

    def read(self, size: int, /) -> bytes:
        """Read a bounded prefix without exposing capacity bytes past the report."""

        if self._closed:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                "flattened.candidate_closed",
            )
        if type(size) is not int or not 1 <= size <= MAX_PDF_EXPORT_CANDIDATE_READ_BYTES:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.RESULT_INVALID,
                "flattened.candidate_read",
            )
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
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.RESULT_INVALID,
                        "flattened.candidate_short",
                    )
                except FlattenedPdfExportError:
                    raise
                except PdfExportError as error:
                    _raise_pdf_export_error(error, "flattened.candidate_read")
                except Exception:
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.RESULT_INVALID,
                        "flattened.candidate_read",
                    )
            if type(chunk) is not bytes or not chunk:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.RESULT_INVALID,
                    "flattened.candidate_chunk",
                )
            if len(chunk) > self._byte_size - self._position - completed:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.RESULT_INVALID,
                    "flattened.candidate_chunk",
                )
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
        try:
            self._buffer.close()
        except FlattenedPdfExportError:
            raise
        except PdfExportError as error:
            _raise_pdf_export_error(error, "flattened.candidate_cleanup")
        except Exception:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                "flattened.candidate_cleanup",
            )
        self._closed = True
        self._pending = b""


@dataclass(frozen=True, slots=True, repr=False)
class _RasterBinding:
    asset_id: UUID
    source: SignatureImageOverlayRasterSource


class BrokeredFlattenedPdfExporter:
    """Run one handle-only flattened export and expose only verified bytes."""

    __slots__ = (
        "_heavy_operations",
        "_id_factory",
        "_output_buffer_factory",
        "_pipe_factory",
        "_plan_buffer_factory",
        "_policy",
        "_raster_buffer_factory",
        "_revoked",
        "_worker",
    )

    def __init__(
        self,
        worker: PdfExportWorkerCoordinator,
        policy: FlattenedPdfExportBrokerPolicy,
        *,
        heavy_operations: HeavyOperationCoordinator,
        pipe_factory: ResultPipeFactory = WindowsResultPipe.create,
        plan_buffer_factory: PlanBufferFactory = WindowsPdfExportPlanBuffer.create,
        output_buffer_factory: OutputBufferFactory = WindowsPdfExportOutputBuffer.create,
        raster_buffer_factory: RasterBufferFactory = WindowsFlattenedPdfRasterBuffer.create,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if (
            type(policy) is not FlattenedPdfExportBrokerPolicy
            or type(heavy_operations) is not HeavyOperationCoordinator
            or not callable(pipe_factory)
            or not callable(plan_buffer_factory)
            or not callable(output_buffer_factory)
            or not callable(raster_buffer_factory)
            or not callable(id_factory)
        ):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.MODEL_INVALID,
                "flattened.broker_configuration",
            )
        self._worker = worker
        self._policy = policy
        self._heavy_operations = heavy_operations
        self._pipe_factory = pipe_factory
        self._plan_buffer_factory = plan_buffer_factory
        self._output_buffer_factory = output_buffer_factory
        self._raster_buffer_factory = raster_buffer_factory
        self._id_factory = id_factory
        self._revoked = False

    @property
    def maximum_output_bytes(self) -> int:
        """Expose the exact app-side destination preflight limit."""

        return self._policy.limits.output_bytes

    @property
    def maximum_input_references(self) -> int:
        """Expose the exact additive RPC input-reference capacity."""

        return MAX_FLATTENED_PDF_EXPORT_INPUT_REFERENCES

    @property
    def revoked(self) -> bool:
        return self._revoked

    def _revoke_permanently(self) -> None:
        self._revoked = True

    @retire_runtime_on_failure
    def export(
        self,
        plan: FlattenedPdfExportPlan,
        sources: PdfExportSourceBatchLease,
        rasters: Mapping[UUID, SignatureImageOverlayRasterSource],
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: FlattenedPdfExportProgressSink = _ignore_progress,
        reserved_temporary_bytes: int = 0,
    ) -> BrokeredFlattenedPdfExportCandidate:
        """Return a candidate only after termination and retained-source revalidation."""

        if self._revoked:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                "flattened.composition_closed",
            )
        try:
            with self._acquire_heavy_operation(deadline, cancellation):
                return self._export(
                    plan,
                    sources,
                    rasters,
                    deadline=deadline,
                    cancellation=cancellation,
                    progress=progress,
                    reserved_temporary_bytes=reserved_temporary_bytes,
                )
        except FlattenedPdfExportError:
            raise
        except PdfExportError as error:
            _raise_pdf_export_error(error, "flattened.broker")
        except SourceIngestError as error:
            _raise_source_error(error)
        except WorkerClientError as error:
            _raise_worker_error(error)
        except MemoryError, RecursionError:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.broker_resources",
            )
        except Exception:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                "flattened.broker",
            )

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
        plan: FlattenedPdfExportPlan,
        sources: PdfExportSourceBatchLease,
        rasters: Mapping[UUID, SignatureImageOverlayRasterSource],
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: FlattenedPdfExportProgressSink,
        reserved_temporary_bytes: int,
    ) -> BrokeredFlattenedPdfExportCandidate:
        if type(plan) is not FlattenedPdfExportPlan or not callable(progress):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.MODEL_INVALID,
                "flattened.broker_input",
            )
        policy_limits = _reserved_operation_limits(self._policy.limits, reserved_temporary_bytes)
        _require_active(deadline, cancellation)
        _require_input_reference_capacity(plan)
        local_source_handles = _require_source_binding(plan, sources)
        raster_bindings = _require_raster_binding(plan, rasters)
        policy_limits = _preflight_operation_limits(policy_limits, plan, raster_bindings)
        forbidden = _plan_identifiers(plan)
        operation_id = self._new_id(forbidden)
        forbidden.add(operation_id)
        plan_object_id = self._new_id(forbidden)
        forbidden.add(plan_object_id)
        output_object_id = self._new_id(forbidden)

        output_buffer = self._output_buffer_factory(policy_limits.output_bytes)
        transferred = False
        try:
            _require_output_buffer(
                output_buffer,
                policy_limits.output_bytes,
                "flattened.output_buffer",
            )
            result: WorkerOperationResultSnapshot | None = None
            generation: int | None = None
            start_attempted = False
            with self._pipe_factory() as pipe, ExitStack() as raster_stack:
                raster_buffers = self._create_raster_buffers(
                    raster_bindings,
                    raster_stack,
                    deadline=deadline,
                    cancellation=cancellation,
                )
                _require_distinct_handles(
                    (
                        *local_source_handles.values(),
                        *(buffer.read_handle_value for buffer in raster_buffers.values()),
                        output_buffer.write_handle_value,
                        pipe.write_handle_value,
                    ),
                    "flattened.local_handles",
                )
                try:
                    _require_active(deadline, cancellation)
                    start_attempted = True
                    generation = self._worker.start(plan.base_plan.case_id)
                    ready = self._wait_ready(
                        plan.base_plan.case_id,
                        generation,
                        deadline=deadline,
                        cancellation=cancellation,
                    )
                    if FLATTENED_PDF_EXPORT_CAPABILITY not in ready.capabilities:
                        reject_flattened_pdf_export(
                            FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                            "flattened.capability",
                        )
                    worker_source_handles = {
                        source.source_id: self._worker.duplicate_handle(
                            local_source_handles[source.source_id],
                            BrokeredHandlePurpose.READ_SOURCE,
                        )
                        for source in plan.base_plan.sources
                    }
                    worker_raster_handles = {
                        binding.asset_id: self._worker.duplicate_handle(
                            raster_buffers[binding.asset_id].read_handle_value,
                            BrokeredHandlePurpose.READ_BOUNDED_INPUT_SECTION,
                        )
                        for binding in raster_bindings
                    }
                    _require_distinct_handles(
                        (
                            *worker_source_handles.values(),
                            *worker_raster_handles.values(),
                        ),
                        "flattened.worker_handles",
                    )
                    plan_payload = encode_flattened_pdf_worker_plan(
                        plan,
                        worker_source_handles,
                        worker_raster_handles,
                    )
                    plan_digest = hashlib.sha256(plan_payload).hexdigest()
                    limits = _operation_limits(
                        policy_limits,
                        plan,
                        raster_bindings,
                        plan_byte_size=len(plan_payload),
                    )
                    with self._plan_buffer_factory(plan_payload) as plan_buffer:
                        _require_plan_buffer(plan_buffer, plan_payload)
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
                        local_authorities = (
                            *local_source_handles.values(),
                            *(buffer.read_handle_value for buffer in raster_buffers.values()),
                            plan_buffer.read_handle_value,
                            output_buffer.write_handle_value,
                            pipe.write_handle_value,
                        )
                        worker_authorities = (
                            *worker_source_handles.values(),
                            *worker_raster_handles.values(),
                            plan_handle,
                            output_handle,
                            result_handle,
                        )
                        _require_distinct_handles(
                            local_authorities,
                            "flattened.local_handles",
                        )
                        _require_distinct_handles(
                            worker_authorities,
                            "flattened.worker_handles",
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
                            reject_flattened_pdf_export(
                                FlattenedPdfExportErrorCode.REPORT_INVALID,
                                "flattened.operation_binding",
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
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                        "flattened.no_result",
                    )
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
                except FlattenedPdfExportError:
                    raise
                except PdfExportError as error:
                    _raise_pdf_export_error(error, "flattened.report_read")
                except Exception:
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.REPORT_INVALID,
                        "flattened.report_read",
                    )
                worker_report = decode_flattened_pdf_worker_report(
                    report_payload,
                    plan=plan,
                )
                report = _require_report(
                    worker_report,
                    plan=plan,
                    operation_id=operation_id,
                    output_object_id=output_object_id,
                    output=output,
                )
                _authenticate_output_buffer(
                    output_buffer,
                    report,
                    deadline=deadline,
                    cancellation=cancellation,
                )
                candidate = BrokeredFlattenedPdfExportCandidate(
                    output_buffer,
                    output_object_id=output_object_id,
                    report=report,
                    maximum_output_bytes=limits.output_bytes,
                )
                transferred = True
                return candidate
        finally:
            if not transferred:
                try:
                    output_buffer.close()
                except FlattenedPdfExportError:
                    raise
                except PdfExportError as error:
                    _raise_pdf_export_error(error, "flattened.output_cleanup")
                except Exception:
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                        "flattened.output_cleanup",
                    )

    def _create_raster_buffers(
        self,
        bindings: tuple[_RasterBinding, ...],
        stack: ExitStack,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> dict[UUID, FlattenedPdfRasterBuffer]:
        buffers: dict[UUID, FlattenedPdfRasterBuffer] = {}
        for binding in bindings:
            _require_active(deadline, cancellation)
            payload = _read_exact_raster(
                binding,
                deadline=deadline,
                cancellation=cancellation,
            )
            created = self._raster_buffer_factory(payload)
            del payload
            _require_raster_buffer(created, binding)
            selected = stack.enter_context(
                cast(AbstractContextManager[FlattenedPdfRasterBuffer], created)
            )
            if selected is not created:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.RESULT_INVALID,
                    "flattened.raster_buffer_context",
                )
            buffers[binding.asset_id] = selected
        return buffers

    def _wait_ready(
        self,
        case_id: UUID,
        generation: int,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> WorkerClientSnapshot:
        if type(generation) is not int or generation < 1:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                "flattened.generation",
            )
        while True:
            _require_active(deadline, cancellation)
            snapshot = self._worker.query()
            if snapshot.generation != generation:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                    "flattened.generation",
                )
            if snapshot.phase is WorkerLifecyclePhase.READY:
                if snapshot.case_id != case_id:
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                        "flattened.case_binding",
                    )
                return snapshot
            if snapshot.phase in {WorkerLifecyclePhase.STOPPED, WorkerLifecyclePhase.FAULTED}:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                    "flattened.handshake",
                )
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
        progress: FlattenedPdfExportProgressSink,
    ) -> WorkerOperationResultSnapshot:
        cancellation_sent = False
        last_progress: PdfExportProgress | None = None
        while True:
            snapshot = self._worker.query()
            if snapshot.generation != generation:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                    "flattened.generation",
                )
            result = snapshot.last_result
            if result is not None:
                if result.operation_id != operation_id:
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.REPORT_INVALID,
                        "flattened.result_binding",
                    )
                if cancellation_sent and result.outcome is OperationOutcome.SUCCEEDED:
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.CANCELLED,
                        "flattened.cancelled",
                    )
                return result
            active = snapshot.active_operation
            if active is not None:
                if active.operation_id != operation_id:
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.REPORT_INVALID,
                        "flattened.active_binding",
                    )
                current = active.progress
                if current is not None:
                    if (
                        current.stage is not ProgressStage.PROCESSING
                        or current.unit is not ProgressUnit.PAGES
                        or current.total_units != worker_page_capacity
                        or current.completed_units > worker_page_capacity
                    ):
                        reject_flattened_pdf_export(
                            FlattenedPdfExportErrorCode.REPORT_INVALID,
                            "flattened.progress_binding",
                        )
                    domain_progress = PdfExportProgress(
                        PdfExportProgressPhase.ASSEMBLING_AND_VERIFYING,
                        # The closed worker reports its bounded parse capacity,
                        # including complete sources for an output-page subset.
                        # Present only the corresponding exact output-page work.
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
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                    "flattened.worker_exit",
                )
            if _deadline_expired(deadline):
                if not cancellation_sent:
                    self._worker.cancel(CancelReason.SHUTDOWN)
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.TIMED_OUT,
                    "flattened.deadline",
                )
            if _cancellation_requested(cancellation) and not cancellation_sent:
                self._worker.cancel(CancelReason.USER_REQUESTED)
                cancellation_sent = True
            self._worker.pump(self._policy.pump_interval_seconds)

    def _new_id(self, forbidden: set[UUID]) -> UUID:
        for _ in range(_MAXIMUM_IDENTIFIER_ATTEMPTS):
            try:
                value = self._id_factory()
            except BaseException:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                    "flattened.identifier",
                )
            if type(value) is UUID and value.version == 4 and value not in forbidden:
                return value
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.INTERNAL_ERROR,
            "flattened.identifier",
        )


def _plan_identifiers(plan: FlattenedPdfExportPlan) -> set[UUID]:
    values = (
        plan.base_plan.case_id,
        *(source.source_id for source in plan.base_plan.sources),
        *(page.page_id for page in plan.pages),
        *(record.overlay_id for page in plan.pages for record in page.overlays),
        *(asset.asset_id for asset in plan.assets),
    )
    if len(set(values)) != len(values):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.MODEL_INVALID,
            "flattened.plan_identifiers",
        )
    return set(values)


def _require_source_binding(
    plan: FlattenedPdfExportPlan,
    sources: PdfExportSourceBatchLease,
) -> dict[UUID, int]:
    base = plan.base_plan
    try:
        if (
            sources.case_id != base.case_id
            or sources.manifest_revision != base.manifest_revision
            or sources.manifest_sha256 != base.manifest_sha256
            or sources.manifest_cas_token != base.manifest_cas_token
        ):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.SOURCE_CHANGED,
                "flattened.source_manifest",
            )
        leased_sources = sources.sources
        if type(leased_sources) is not tuple or len(leased_sources) != len(base.sources):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.SOURCE_CHANGED,
                "flattened.source_batch",
            )
        handles: dict[UUID, int] = {}
        for expected, leased in zip(base.sources, leased_sources, strict=True):
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
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.SOURCE_CHANGED,
                    "flattened.source_binding",
                )
            handles[expected.source_id] = leased.handle_value
        if len(set(handles.values())) != len(handles):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.SOURCE_CHANGED,
                "flattened.source_handles",
            )
        return handles
    except FlattenedPdfExportError:
        raise
    except PdfExportError as error:
        _raise_pdf_export_error(error, "flattened.source_binding")
    except SourceIngestError:
        raise
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.SOURCE_CHANGED,
            "flattened.source_binding",
        )


def _require_raster_binding(
    plan: FlattenedPdfExportPlan,
    rasters: Mapping[UUID, SignatureImageOverlayRasterSource],
) -> tuple[_RasterBinding, ...]:
    if not isinstance(rasters, Mapping):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RASTER_METADATA_INVALID,
            "flattened.raster_mapping",
        )
    expected = tuple(asset.asset_id for asset in plan.assets)
    try:
        supplied = tuple(rasters.keys())
        supplied_ids = set(supplied)
        expected_ids = set(expected)
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RASTER_METADATA_INVALID,
            "flattened.raster_mapping",
        )
    if expected_ids - supplied_ids:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RASTER_METADATA_MISSING,
            "flattened.raster_mapping",
        )
    if supplied_ids - expected_ids or len(supplied) != len(expected):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RASTER_METADATA_EXTRA,
            "flattened.raster_mapping",
        )
    selected: list[_RasterBinding] = []
    for asset in plan.assets:
        try:
            source = rasters[asset.asset_id]
            metadata = source.metadata
        except Exception:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.RASTER_METADATA_MISSING,
                "flattened.raster_source",
            )
        if type(source) is not SignatureImageOverlayRasterSource or metadata != asset.raster_asset:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.RASTER_METADATA_INVALID,
                "flattened.raster_metadata",
            )
        selected.append(_RasterBinding(asset.asset_id, source))
    return tuple(selected)


def _read_exact_raster(
    binding: _RasterBinding,
    *,
    deadline: DeadlineProbe,
    cancellation: CancellationProbe,
) -> bytes:
    source = binding.source
    metadata = source.metadata
    try:
        original = source.tell()
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.ASSET_CHANGED,
            "flattened.raster_state",
        )
    if type(original) is not int or original < 0:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.ASSET_CHANGED,
            "flattened.raster_state",
        )
    parts: list[bytes] = []
    digest = hashlib.sha256()
    completed = 0
    operation_error: FlattenedPdfExportError | None = None
    try:
        if source.seek(0) != 0:
            raise OSError
        while completed < metadata.byte_count:
            _require_active(deadline, cancellation)
            wanted = min(_RASTER_COPY_CHUNK_BYTES, metadata.byte_count - completed)
            chunk = source.read(wanted)
            if type(chunk) is not bytes or not chunk or len(chunk) > wanted:
                raise OSError
            completed += len(chunk)
            digest.update(chunk)
            parts.append(chunk)
        if source.read(1) != b"":
            raise OSError
        _require_active(deadline, cancellation)
        if not hmac.compare_digest(digest.hexdigest(), metadata.pixel_sha256):
            raise OSError
    except FlattenedPdfExportError as error:
        operation_error = error
    except MemoryError, RecursionError:
        operation_error = FlattenedPdfExportError(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.raster_copy",
        )
    except Exception:
        operation_error = FlattenedPdfExportError(
            FlattenedPdfExportErrorCode.ASSET_CHANGED,
            "flattened.raster_copy",
        )
    try:
        restored = source.seek(original)
    except Exception:
        restored = None
    if restored != original:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.ASSET_CHANGED,
            "flattened.raster_state",
        )
    if operation_error is not None:
        raise operation_error from None
    try:
        return b"".join(parts)
    except MemoryError:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.raster_copy",
        )


def _reserved_operation_limits(
    policy: ResourceLimits, reserved_temporary_bytes: int
) -> ResourceLimits:
    """Leave caller-retained package stages inside the original operation quota."""

    if type(reserved_temporary_bytes) is not int or reserved_temporary_bytes < 0:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.MODEL_INVALID, "flattened.temp_reservation"
        )
    remaining = policy.temporary_storage_bytes - reserved_temporary_bytes
    if remaining <= 0:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED, "flattened.temp_reservation"
        )
    return replace(policy, temporary_storage_bytes=remaining)


def _preflight_operation_limits(
    policy: ResourceLimits,
    plan: FlattenedPdfExportPlan,
    rasters: tuple[_RasterBinding, ...],
) -> ResourceLimits:
    source_bytes = sum(source.byte_size for source in plan.base_plan.sources)
    raster_bytes = sum(binding.source.metadata.byte_count for binding in rasters)
    if (
        plan.page_count > policy.page_count
        or any(source.page_count > policy.page_count for source in plan.base_plan.sources)
        or raster_bytes > policy.memory_bytes
        or raster_bytes // 4 > policy.pixel_count
        or source_bytes > policy.temporary_storage_bytes
        or raster_bytes > policy.temporary_storage_bytes - source_bytes
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.operation_limits",
        )
    # The canonical envelope differs only in its integer handle values after
    # worker startup. Distinct maximum-width values bound its exact byte size
    # before any section allocation without reserving the full schema ceiling.
    source_handles = {
        source.source_id: MAX_SAFE_MANIFEST_INTEGER - index
        for index, source in enumerate(plan.base_plan.sources)
    }
    raster_handles = {
        binding.asset_id: MAX_SAFE_MANIFEST_INTEGER - len(source_handles) - index
        for index, binding in enumerate(rasters)
    }
    plan_bytes = len(encode_flattened_pdf_worker_plan(plan, source_handles, raster_handles))
    output_bytes = min(
        policy.output_bytes,
        (policy.temporary_storage_bytes - source_bytes - raster_bytes - plan_bytes) // 2,
    )
    if output_bytes < 1:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.operation_limits",
        )
    # The worker candidate and shared section overlap; the section and private
    # destination stage overlap later. Both copies stay in the remaining quota.
    return replace(policy, output_bytes=output_bytes)


def _require_input_reference_capacity(plan: FlattenedPdfExportPlan) -> None:
    if (
        1 + len(plan.base_plan.sources) + len(plan.assets)
        > MAX_FLATTENED_PDF_EXPORT_INPUT_REFERENCES
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.input_references",
        )


def _operation_limits(
    policy: ResourceLimits,
    plan: FlattenedPdfExportPlan,
    rasters: tuple[_RasterBinding, ...],
    *,
    plan_byte_size: int,
) -> ResourceLimits:
    source_bytes = sum(source.byte_size for source in plan.base_plan.sources)
    raster_bytes = sum(binding.source.metadata.byte_count for binding in rasters)
    input_bytes = source_bytes + raster_bytes + plan_byte_size
    required_page_capacity = max(
        plan.page_count, *(source.page_count for source in plan.base_plan.sources)
    )
    if (
        type(plan_byte_size) is not int
        or plan_byte_size < 1
        or required_page_capacity > policy.page_count
        or input_bytes > policy.temporary_storage_bytes
        or 2 * policy.output_bytes > policy.temporary_storage_bytes - input_bytes
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.operation_limits",
        )
    return ResourceLimits(
        wall_time_ms=policy.wall_time_ms,
        cpu_time_ms=policy.cpu_time_ms,
        memory_bytes=policy.memory_bytes,
        temporary_storage_bytes=policy.temporary_storage_bytes,
        output_bytes=policy.output_bytes,
        # Source inspection must admit the complete immutable source even when
        # this PDF uses only a subset. Output count is still verified from plan.
        page_count=required_page_capacity,
        pixel_count=policy.pixel_count,
    )


def _operation_payload(
    plan: FlattenedPdfExportPlan,
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
        operation_kind=FLATTENED_PDF_EXPORT_CAPABILITY.capability_id,
        operation_version=FLATTENED_PDF_EXPORT_CAPABILITY.capability_version,
        inputs=(
            CaseObjectReference(
                area=ReferenceArea.OPERATION_TEMP,
                object_id=plan_object_id,
                operation_id=operation_id,
                content_kind=FLATTENED_PDF_EXPORT_PLAN_CONTENT_KIND,
                expected_byte_size=plan_byte_size,
                expected_sha256=plan_sha256,
            ),
            *(
                CaseObjectReference(
                    area=ReferenceArea.SOURCE_READ,
                    object_id=source.source_id,
                    operation_id=None,
                    content_kind="pdf",
                    expected_byte_size=source.byte_size,
                    expected_sha256=source.sha256,
                )
                for source in plan.base_plan.sources
            ),
            *(
                CaseObjectReference(
                    area=ReferenceArea.OPERATION_TEMP,
                    object_id=asset.asset_id,
                    operation_id=operation_id,
                    content_kind=FLATTENED_PDF_EXPORT_RASTER_CONTENT_KIND,
                    expected_byte_size=asset.raster_asset.byte_count,
                    expected_sha256=asset.raster_asset.pixel_sha256,
                )
                for asset in plan.assets
            ),
        ),
        parameters=(
            TaggedScalar(
                FLATTENED_PDF_EXPORT_PLAN_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                plan_handle,
            ),
            TaggedScalar(
                FLATTENED_PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                output_handle,
            ),
            TaggedScalar(
                FLATTENED_PDF_EXPORT_RESULT_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                result_handle,
            ),
            TaggedScalar(
                FLATTENED_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
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
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.REPORT_INVALID,
            "flattened.worker_result",
        )
    if result.outcome is OperationOutcome.CANCELLED:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.CANCELLED,
            "flattened.worker_result",
        )
    if result.outcome is OperationOutcome.TIMED_OUT:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.TIMED_OUT,
            "flattened.worker_result",
        )
    if result.outcome is OperationOutcome.FAILED and result.error_category is (
        ErrorCategory.RESOURCE_LIMIT
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.worker_result",
        )
    if result.outcome is not OperationOutcome.SUCCEEDED or len(result.outputs) != 1:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
            "flattened.worker_result",
        )
    output = result.outputs[0]
    if (
        output.area is not ReferenceArea.OPERATION_STAGING
        or output.object_id != output_object_id
        or output.operation_id != operation_id
        or output.content_kind != FLATTENED_PDF_EXPORT_OUTPUT_CONTENT_KIND
        or output.expected_byte_size is None
        or not 1 <= output.expected_byte_size <= maximum_output_bytes
        or output.expected_sha256 is None
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.REPORT_INVALID,
            "flattened.worker_output",
        )
    return output


def _require_report(
    value: FlattenedPdfExportWorkerReport,
    *,
    plan: FlattenedPdfExportPlan,
    operation_id: UUID,
    output_object_id: UUID,
    output: CaseObjectReference,
) -> FlattenedPdfExportVerificationReport:
    report = value.report
    base = report.base_report
    if (
        value.operation_id != operation_id
        or value.output_object_id != output_object_id
        or report.plan is not plan
        or base.semantic_fingerprint != plan.semantic_fingerprint
        or base.page_count != plan.page_count
        or base.unsigned_derivative != plan.contains_signature_markers
        or output.expected_byte_size != base.output_byte_size
        or output.expected_sha256 is None
        or not hmac.compare_digest(output.expected_sha256, base.output_sha256)
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.REPORT_INVALID,
            "flattened.report_binding",
        )
    return report


def _authenticate_output_buffer(
    output_buffer: PdfExportOutputBuffer,
    report: FlattenedPdfExportVerificationReport,
    *,
    deadline: DeadlineProbe,
    cancellation: CancellationProbe,
) -> None:
    expected_size = report.base_report.output_byte_size
    expected_digest = report.base_report.output_sha256
    digest = hashlib.sha256()
    completed = 0
    try:
        for chunk in output_buffer.iter_chunks(
            expected_size,
            chunk_bytes=MAX_PDF_EXPORT_CANDIDATE_READ_BYTES,
        ):
            _require_active(deadline, cancellation)
            if type(chunk) is not bytes or not chunk or len(chunk) > expected_size - completed:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.RESULT_INVALID,
                    "flattened.output_chunk",
                )
            completed += len(chunk)
            digest.update(chunk)
    except FlattenedPdfExportError:
        raise
    except PdfExportError as error:
        _raise_pdf_export_error(error, "flattened.output_read")
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RESULT_INVALID,
            "flattened.output_read",
        )
    _require_active(deadline, cancellation)
    if completed != expected_size or not hmac.compare_digest(
        digest.hexdigest(),
        expected_digest,
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RESULT_INVALID,
            "flattened.output_digest",
        )


def _require_output_buffer(
    buffer: PdfExportOutputBuffer,
    capacity: int,
    context: str,
) -> None:
    try:
        valid = (
            type(buffer.capacity) is int
            and buffer.capacity == capacity
            and type(buffer.write_handle_value) is int
            and buffer.write_handle_value > 0
            and callable(getattr(buffer, "iter_chunks", None))
            and callable(getattr(buffer, "close", None))
        )
    except Exception:
        valid = False
    if not valid:
        reject_flattened_pdf_export(FlattenedPdfExportErrorCode.RESULT_INVALID, context)


def _require_plan_buffer(buffer: PdfExportPlanBuffer, payload: bytes) -> None:
    try:
        valid = (
            buffer.payload_size == len(payload)
            and type(buffer.read_handle_value) is int
            and buffer.read_handle_value > 0
        )
    except Exception:
        valid = False
    if not valid:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RESULT_INVALID,
            "flattened.plan_buffer",
        )


def _require_raster_buffer(
    buffer: FlattenedPdfRasterBuffer,
    binding: _RasterBinding,
) -> None:
    metadata = binding.source.metadata
    try:
        valid = (
            buffer.payload_size == metadata.byte_count
            and hmac.compare_digest(buffer.payload_sha256, metadata.pixel_sha256)
            and type(buffer.read_handle_value) is int
            and buffer.read_handle_value > 0
            and callable(getattr(buffer, "close", None))
            and callable(getattr(buffer, "__enter__", None))
            and callable(getattr(buffer, "__exit__", None))
        )
    except Exception:
        valid = False
    if not valid:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RESULT_INVALID,
            "flattened.raster_buffer",
        )


def _require_distinct_handles(handles: tuple[int, ...], context: str) -> None:
    if any(type(handle) is not int or handle <= 0 for handle in handles) or len(
        set(handles)
    ) != len(handles):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RESULT_INVALID,
            context,
        )


def _require_active(deadline: DeadlineProbe, cancellation: CancellationProbe) -> None:
    if _deadline_expired(deadline):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.TIMED_OUT,
            "flattened.deadline",
        )
    if _cancellation_requested(cancellation):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.CANCELLED,
            "flattened.cancelled",
        )


def _deadline_expired(probe: DeadlineProbe) -> bool:
    try:
        value = probe()
    except SourceIngestError:
        raise
    except BaseException:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.INTERNAL_ERROR,
            "flattened.deadline_probe",
        )
    if type(value) is not bool:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.INTERNAL_ERROR,
            "flattened.deadline_probe",
        )
    return value


def _cancellation_requested(probe: CancellationProbe) -> bool:
    try:
        value = probe()
    except SourceIngestError:
        raise
    except BaseException:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.INTERNAL_ERROR,
            "flattened.cancel_probe",
        )
    if type(value) is not bool:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.INTERNAL_ERROR,
            "flattened.cancel_probe",
        )
    return value


def _report_progress(
    sink: FlattenedPdfExportProgressSink,
    progress: PdfExportProgress,
) -> None:
    try:
        sink(progress)
    except FlattenedPdfExportError:
        raise
    except PdfExportError as error:
        _raise_pdf_export_error(error, "flattened.progress")
    except BaseException:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.INTERNAL_ERROR,
            "flattened.progress",
        )


def _raise_source_error(error: SourceIngestError) -> Never:
    if error.code is SourceIngestErrorCode.CANCELLED:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.CANCELLED,
            "flattened.source",
        )
    if error.code is SourceIngestErrorCode.TIMED_OUT:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.TIMED_OUT,
            "flattened.source",
        )
    if error.code is SourceIngestErrorCode.CAPACITY_EXCEEDED:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.source",
        )
    reject_flattened_pdf_export(
        FlattenedPdfExportErrorCode.SOURCE_CHANGED,
        "flattened.source",
    )


def _raise_worker_error(error: WorkerClientError) -> Never:
    if error.code in {
        WorkerClientErrorCode.HANDSHAKE_TIMED_OUT,
        WorkerClientErrorCode.OPERATION_TIMED_OUT,
        WorkerClientErrorCode.CANCELLATION_TIMED_OUT,
    }:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.TIMED_OUT,
            "flattened.worker_timeout",
        )
    reject_flattened_pdf_export(
        FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
        "flattened.worker",
    )


def _raise_pdf_export_error(error: PdfExportError, context: str) -> Never:
    direct = {
        PdfExportErrorCode.MODEL_INVALID: FlattenedPdfExportErrorCode.MODEL_INVALID,
        PdfExportErrorCode.MANIFEST_INVALID: FlattenedPdfExportErrorCode.MANIFEST_INVALID,
        PdfExportErrorCode.PLAN_EMPTY: FlattenedPdfExportErrorCode.PLAN_EMPTY,
        PdfExportErrorCode.SOURCE_UNSUPPORTED: FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
        PdfExportErrorCode.SOURCE_ENCRYPTED: FlattenedPdfExportErrorCode.SOURCE_ENCRYPTED,
        PdfExportErrorCode.SOURCE_ENCRYPTION_UNKNOWN: (
            FlattenedPdfExportErrorCode.SOURCE_ENCRYPTION_UNKNOWN
        ),
        PdfExportErrorCode.SOURCE_SIGNATURE_UNKNOWN: (
            FlattenedPdfExportErrorCode.SOURCE_SIGNATURE_UNKNOWN
        ),
        PdfExportErrorCode.SOURCE_INCONSISTENT: (FlattenedPdfExportErrorCode.SOURCE_INCONSISTENT),
        PdfExportErrorCode.SOURCE_CHANGED: FlattenedPdfExportErrorCode.SOURCE_CHANGED,
        PdfExportErrorCode.TRANSFORM_UNSUPPORTED: (
            FlattenedPdfExportErrorCode.TRANSFORM_UNSUPPORTED
        ),
        PdfExportErrorCode.LIMIT_EXCEEDED: FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
        PdfExportErrorCode.CANCELLED: FlattenedPdfExportErrorCode.CANCELLED,
        PdfExportErrorCode.TIMED_OUT: FlattenedPdfExportErrorCode.TIMED_OUT,
        PdfExportErrorCode.ASSEMBLY_FAILED: FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
        PdfExportErrorCode.VERIFICATION_REOPEN_FAILED: (
            FlattenedPdfExportErrorCode.VERIFICATION_REOPEN_FAILED
        ),
        PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED: (
            FlattenedPdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED
        ),
        PdfExportErrorCode.VERIFICATION_PAGE_COUNT_MISMATCH: (
            FlattenedPdfExportErrorCode.VERIFICATION_PAGE_COUNT_MISMATCH
        ),
        PdfExportErrorCode.VERIFICATION_ENCRYPTION_FAILED: (
            FlattenedPdfExportErrorCode.VERIFICATION_ENCRYPTION_FAILED
        ),
        PdfExportErrorCode.VERIFICATION_RENDER_FAILED: (
            FlattenedPdfExportErrorCode.VERIFICATION_RENDER_FAILED
        ),
        PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH: (
            FlattenedPdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH
        ),
        PdfExportErrorCode.DESTINATION_FAILED: FlattenedPdfExportErrorCode.DESTINATION_FAILED,
        PdfExportErrorCode.REPORT_INVALID: FlattenedPdfExportErrorCode.REPORT_INVALID,
        PdfExportErrorCode.RESULT_INVALID: FlattenedPdfExportErrorCode.RESULT_INVALID,
    }
    if error.code is PdfExportErrorCode.PLAN_STALE:
        selected = FlattenedPdfExportErrorCode.SOURCE_CHANGED
    elif error.code is PdfExportErrorCode.WORKER_FAILED:
        selected = FlattenedPdfExportErrorCode.ASSEMBLY_FAILED
    else:
        selected = direct.get(error.code, FlattenedPdfExportErrorCode.INTERNAL_ERROR)
    reject_flattened_pdf_export(selected, context)

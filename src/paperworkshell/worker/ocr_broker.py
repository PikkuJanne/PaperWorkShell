"""GUI-process broker for one-shot, handle-bound local OCR processing."""

from __future__ import annotations

import hashlib
import hmac
import math
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from types import TracebackType
from typing import Never, Protocol, Self
from uuid import UUID, uuid4

from paperworkshell.domain.ocr import (
    MAX_OCR_WORKER_REPORT_BYTES,
    TRUSTED_OCR_ENGINE_PROFILE_SHA256,
    TRUSTED_OCRMYPDF_VERSION,
    TRUSTED_TESSERACT_VERSION,
    OcrError,
    OcrErrorCode,
    OcrProcessRequest,
    OcrProgress,
    OcrProgressPhase,
    OcrSourceEncryptionState,
    OcrSourceKind,
    OcrSourceSignatureState,
    OcrWorkerReport,
    decode_ocr_worker_report,
    reject_ocr,
)
from paperworkshell.domain.pdf_thumbnails import PdfThumbnailError
from paperworkshell.domain.source_ingest import SourceIngestError, SourceIngestErrorCode
from paperworkshell.worker._windows_broker import (
    WindowsBoundedOutputBuffer,
    WindowsResultPipe,
)
from paperworkshell.worker.client.errors import WorkerClientError, WorkerClientErrorCode
from paperworkshell.worker.client.models import (
    WorkerClientSnapshot,
    WorkerLifecyclePhase,
    WorkerOperationResultSnapshot,
)
from paperworkshell.worker.client.process import BrokeredHandlePurpose
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator, HeavyOperationLease
from paperworkshell.worker.protocol.models import (
    OCR_PROCESS_CAPABILITY,
    OCR_PROCESS_LANGUAGE_SET_ID_PARAMETER,
    OCR_PROCESS_OUTPUT_CONTENT_KIND,
    OCR_PROCESS_OUTPUT_HANDLE_PARAMETER,
    OCR_PROCESS_OUTPUT_OBJECT_ID_PARAMETER,
    OCR_PROCESS_PAGE_COUNT_PARAMETER,
    OCR_PROCESS_PROFILE_ID_PARAMETER,
    OCR_PROCESS_RESULT_HANDLE_PARAMETER,
    OCR_PROCESS_SOURCE_HANDLE_PARAMETER,
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

_MAXIMUM_IDENTIFIER_ATTEMPTS = 32
MAX_OCR_CANDIDATE_READ_BYTES = 1024 * 1024

DeadlineProbe = Callable[[], bool]
CancellationProbe = Callable[[], bool]


class OcrSourceLease(Protocol):
    """Retained, authenticated authority for one exact committed source."""

    @property
    def case_id(self) -> UUID: ...

    @property
    def source_id(self) -> UUID: ...

    @property
    def source_kind(self) -> OcrSourceKind: ...

    @property
    def byte_size(self) -> int: ...

    @property
    def page_count(self) -> int: ...

    @property
    def sha256(self) -> str: ...

    @property
    def signature_state(self) -> OcrSourceSignatureState: ...

    @property
    def encryption_state(self) -> OcrSourceEncryptionState: ...

    @property
    def handle_value(self) -> int: ...

    def revalidate(
        self,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> None: ...


class OcrWorkerCoordinator(Protocol):
    """Narrow one-shot worker client surface consumed by this broker."""

    def start(self, case_id: UUID) -> int: ...

    def query(self) -> WorkerClientSnapshot: ...

    def pump(self, timeout_seconds: float = 0.0) -> WorkerClientSnapshot: ...

    def duplicate_handle(self, source_handle: int, purpose: BrokeredHandlePurpose) -> int: ...

    def start_operation(
        self,
        payload: OperationStartPayload,
        *,
        operation_id: UUID | None = None,
    ) -> UUID: ...

    def cancel(self, reason: CancelReason = CancelReason.USER_REQUESTED) -> UUID: ...

    def terminate(self) -> None: ...


class OcrResultPipe(Protocol):
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


class OcrOutputBuffer(Protocol):
    @property
    def capacity(self) -> int: ...

    @property
    def write_handle_value(self) -> int: ...

    def iter_chunks(self, byte_count: int, *, chunk_bytes: int) -> Iterator[bytes]: ...

    def close(self) -> None: ...


class OcrProgressSink(Protocol):
    """Receive one already validated, content-free OCR progress record."""

    def __call__(self, progress: OcrProgress, /) -> object: ...


ResultPipeFactory = Callable[[], OcrResultPipe]
OutputBufferFactory = Callable[[int], OcrOutputBuffer]


@dataclass(frozen=True, slots=True)
class OcrBrokerPolicy:
    """Trusted worker limits and GUI pump cadence for one OCR attempt."""

    limits: ResourceLimits
    pump_interval_seconds: float = 0.050

    def __post_init__(self) -> None:
        if (
            type(self.limits) is not ResourceLimits
            or type(self.pump_interval_seconds) is not float
            or not math.isfinite(self.pump_interval_seconds)
            or not 0 < self.pump_interval_seconds <= 1.0
            or self.limits.output_bytes >= self.limits.temporary_storage_bytes
            or self.limits.output_bytes < 5
            or self.limits.page_count < 1
        ):
            reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.broker_policy")


class BrokeredOcrCandidate(AbstractContextManager["BrokeredOcrCandidate"]):
    """Single-pass bounded view over one authenticated volatile PDF candidate."""

    __slots__ = (
        "_buffer",
        "_chunks",
        "_closed",
        "_pending",
        "_position",
        "output_object_id",
        "report",
    )

    def __init__(
        self,
        buffer: OcrOutputBuffer,
        *,
        output_object_id: UUID,
        report: OcrWorkerReport,
    ) -> None:
        if (
            not callable(getattr(buffer, "iter_chunks", None))
            or not callable(getattr(buffer, "close", None))
            or type(output_object_id) is not UUID
            or output_object_id.version != 4
            or type(report) is not OcrWorkerReport
            or report.output_object_id != output_object_id
            or buffer.capacity < report.output_byte_size
        ):
            reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.candidate")
        self._buffer = buffer
        self._chunks = buffer.iter_chunks(
            report.output_byte_size,
            chunk_bytes=MAX_OCR_CANDIDATE_READ_BYTES,
        )
        self._closed = False
        self._pending = b""
        self._position = 0
        self.output_object_id = output_object_id
        self.report = report

    def __repr__(self) -> str:
        return (
            "BrokeredOcrCandidate("
            f"byte_size={self.report.output_byte_size}, "
            f"page_count={self.report.page_count})"
        )

    def __enter__(self) -> Self:
        if self._closed:
            reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.candidate_closed")
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def read(self, size: int, /) -> bytes:
        if self._closed:
            reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.candidate_closed")
        if type(size) is not int or not 1 <= size <= MAX_OCR_CANDIDATE_READ_BYTES:
            reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.candidate_read")
        remaining = self.report.output_byte_size - self._position
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
                    reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.candidate_short")
                except OcrError:
                    raise
                except Exception:
                    reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.candidate_read")
            if type(chunk) is not bytes or not chunk:
                reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.candidate_chunk")
            available = self.report.output_byte_size - self._position - completed
            if len(chunk) > available:
                reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.candidate_chunk")
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
        except OcrError:
            raise
        except Exception:
            reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.candidate_cleanup")
        self._closed = True
        self._pending = b""


class BrokeredOcrProcessor:
    """Run OCR once and expose output only after termination and authentication."""

    __slots__ = (
        "_heavy_operations",
        "_id_factory",
        "_output_buffer_factory",
        "_pipe_factory",
        "_policy",
        "_revoked",
        "_worker",
    )

    def __init__(
        self,
        worker: OcrWorkerCoordinator,
        policy: OcrBrokerPolicy,
        *,
        heavy_operations: HeavyOperationCoordinator,
        pipe_factory: ResultPipeFactory = WindowsResultPipe.create,
        output_buffer_factory: OutputBufferFactory = WindowsBoundedOutputBuffer.create,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if (
            type(policy) is not OcrBrokerPolicy
            or type(heavy_operations) is not HeavyOperationCoordinator
            or not callable(pipe_factory)
            or not callable(output_buffer_factory)
            or not callable(id_factory)
        ):
            reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.broker_configuration")
        self._worker = worker
        self._policy = policy
        self._heavy_operations = heavy_operations
        self._pipe_factory = pipe_factory
        self._output_buffer_factory = output_buffer_factory
        self._id_factory = id_factory
        self._revoked = False

    @property
    def revoked(self) -> bool:
        return self._revoked

    def _revoke_permanently(self) -> None:
        self._revoked = True

    def process(
        self,
        request: OcrProcessRequest,
        source: OcrSourceLease,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: OcrProgressSink = lambda _progress: None,
    ) -> BrokeredOcrCandidate:
        if self._revoked:
            reject_ocr(OcrErrorCode.ENGINE_UNAVAILABLE, "ocr.composition_closed")
        try:
            with self._acquire_heavy_operation(deadline, cancellation):
                return self._process(
                    request,
                    source,
                    deadline=deadline,
                    cancellation=cancellation,
                    progress=progress,
                )
        except OcrError:
            raise
        except SourceIngestError as error:
            _raise_source_error(error)
        except WorkerClientError as error:
            if error.code in {
                WorkerClientErrorCode.HANDSHAKE_TIMED_OUT,
                WorkerClientErrorCode.OPERATION_TIMED_OUT,
                WorkerClientErrorCode.CANCELLATION_TIMED_OUT,
            }:
                reject_ocr(OcrErrorCode.TIMED_OUT, "ocr.worker_timeout")
            reject_ocr(OcrErrorCode.ENGINE_FAILED, "ocr.worker")
        except PdfThumbnailError:
            reject_ocr(OcrErrorCode.OUTPUT_INVALID, "ocr.output_buffer")
        except Exception:
            reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.broker")

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

    def _process(
        self,
        request: OcrProcessRequest,
        source: OcrSourceLease,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: OcrProgressSink,
    ) -> BrokeredOcrCandidate:
        if type(request) is not OcrProcessRequest or not callable(progress):
            reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.broker_input")
        _require_source_binding(request, source)
        _require_active(deadline, cancellation)
        limits = _operation_limits(self._policy.limits, request)
        operation_id = self._new_id({request.case_id, request.source_id})
        output_object_id = self._new_id({request.case_id, request.source_id, operation_id})
        output_buffer = self._output_buffer_factory(limits.output_bytes)
        transferred = False
        try:
            if (
                type(output_buffer.capacity) is not int
                or output_buffer.capacity != limits.output_bytes
                or type(output_buffer.write_handle_value) is not int
                or output_buffer.write_handle_value <= 0
            ):
                reject_ocr(OcrErrorCode.OUTPUT_INVALID, "ocr.output_buffer")
            result: WorkerOperationResultSnapshot | None = None
            generation: int | None = None
            start_attempted = False
            with self._pipe_factory() as pipe:
                try:
                    _report_progress(
                        progress,
                        OcrProgress(
                            operation_id,
                            OcrProgressPhase.PREPARING,
                            0,
                            request.source_page_count,
                        ),
                    )
                    start_attempted = True
                    generation = self._worker.start(request.case_id)
                    ready = self._wait_ready(
                        request.case_id,
                        generation,
                        deadline=deadline,
                        cancellation=cancellation,
                    )
                    if OCR_PROCESS_CAPABILITY not in ready.capabilities:
                        reject_ocr(OcrErrorCode.ENGINE_UNAVAILABLE, "ocr.capability")
                    source_handle = self._worker.duplicate_handle(
                        source.handle_value,
                        BrokeredHandlePurpose.READ_SOURCE,
                    )
                    output_handle = self._worker.duplicate_handle(
                        output_buffer.write_handle_value,
                        BrokeredHandlePurpose.WRITE_BOUNDED_OUTPUT_SECTION,
                    )
                    result_handle = self._worker.duplicate_handle(
                        pipe.write_handle_value,
                        BrokeredHandlePurpose.WRITE_RESULT_PIPE,
                    )
                    worker_handles = (source_handle, output_handle, result_handle)
                    if any(type(handle) is not int or handle <= 0 for handle in worker_handles) or (
                        len(set(worker_handles)) != len(worker_handles)
                    ):
                        reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.worker_handles")
                    pipe.close_local_writer()
                    payload = _operation_payload(
                        request,
                        source_handle=source_handle,
                        output_handle=output_handle,
                        result_handle=result_handle,
                        output_object_id=output_object_id,
                        limits=limits,
                    )
                    selected = self._worker.start_operation(
                        payload,
                        operation_id=operation_id,
                    )
                    if selected != operation_id:
                        reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.operation_binding")
                    result = self._wait_result(
                        operation_id,
                        generation,
                        page_count=request.source_page_count,
                        deadline=deadline,
                        cancellation=cancellation,
                        progress=progress,
                    )
                finally:
                    if start_attempted:
                        self._worker.terminate()
                if result is None or generation is None:
                    reject_ocr(OcrErrorCode.ENGINE_FAILED, "ocr.no_result")
                output = _require_successful_output(
                    result,
                    generation=generation,
                    operation_id=operation_id,
                    output_object_id=output_object_id,
                    maximum_output_bytes=limits.output_bytes,
                )
                _require_source_binding(request, source)
                source.revalidate(deadline=deadline, cancellation=cancellation)
                _require_source_binding(request, source)
                _require_active(deadline, cancellation)
                _report_progress(
                    progress,
                    OcrProgress(
                        operation_id,
                        OcrProgressPhase.VALIDATING,
                        request.source_page_count,
                        request.source_page_count,
                    ),
                )
                try:
                    report_payload = pipe.read_complete_frame()
                except Exception:
                    reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.report_read")
                if len(report_payload) > MAX_OCR_WORKER_REPORT_BYTES:
                    reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.report_size")
                report = decode_ocr_worker_report(report_payload)
                _require_report(
                    report,
                    request=request,
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
                _report_progress(
                    progress,
                    OcrProgress(
                        operation_id,
                        OcrProgressPhase.COMPLETE,
                        request.source_page_count,
                        request.source_page_count,
                    ),
                )
                _require_active(deadline, cancellation)
                candidate = BrokeredOcrCandidate(
                    output_buffer,
                    output_object_id=output_object_id,
                    report=report,
                )
                transferred = True
                return candidate
        finally:
            if not transferred:
                try:
                    output_buffer.close()
                except OcrError:
                    raise
                except Exception:
                    reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.output_cleanup")

    def _wait_ready(
        self,
        case_id: UUID,
        generation: int,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> WorkerClientSnapshot:
        if type(generation) is not int or generation < 1:
            reject_ocr(OcrErrorCode.ENGINE_FAILED, "ocr.generation")
        while True:
            _require_active(deadline, cancellation)
            snapshot = self._worker.query()
            if snapshot.generation != generation:
                reject_ocr(OcrErrorCode.ENGINE_FAILED, "ocr.generation")
            if snapshot.phase is WorkerLifecyclePhase.READY:
                if snapshot.case_id != case_id:
                    reject_ocr(OcrErrorCode.ENGINE_FAILED, "ocr.case_binding")
                return snapshot
            if snapshot.phase in {WorkerLifecyclePhase.STOPPED, WorkerLifecyclePhase.FAULTED}:
                reject_ocr(OcrErrorCode.ENGINE_FAILED, "ocr.handshake")
            self._worker.pump(self._policy.pump_interval_seconds)

    def _wait_result(
        self,
        operation_id: UUID,
        generation: int,
        *,
        page_count: int,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: OcrProgressSink,
    ) -> WorkerOperationResultSnapshot:
        cancellation_sent = False
        last_completed = -1
        while True:
            snapshot = self._worker.query()
            if snapshot.generation != generation:
                reject_ocr(OcrErrorCode.ENGINE_FAILED, "ocr.generation")
            result = snapshot.last_result
            if result is not None:
                if result.operation_id != operation_id:
                    reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.result_binding")
                if cancellation_sent and result.outcome is OperationOutcome.SUCCEEDED:
                    reject_ocr(OcrErrorCode.CANCELLED, "ocr.cancelled")
                return result
            active = snapshot.active_operation
            if active is not None and active.operation_id == operation_id:
                current = active.progress
                if current is not None:
                    if (
                        current.stage is not ProgressStage.PROCESSING
                        or current.unit is not ProgressUnit.PAGES
                        or current.total_units != page_count
                        or current.completed_units > page_count
                    ):
                        reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.progress_binding")
                    if current.completed_units != last_completed:
                        _report_progress(
                            progress,
                            OcrProgress(
                                operation_id,
                                OcrProgressPhase.RECOGNIZING,
                                current.completed_units,
                                page_count,
                            ),
                        )
                        last_completed = current.completed_units
            if snapshot.phase in {
                WorkerLifecyclePhase.READY,
                WorkerLifecyclePhase.STOPPED,
                WorkerLifecyclePhase.FAULTED,
            }:
                reject_ocr(OcrErrorCode.ENGINE_FAILED, "ocr.worker_exit")
            if _deadline_expired(deadline):
                if not cancellation_sent:
                    self._worker.cancel(CancelReason.SHUTDOWN)
                reject_ocr(OcrErrorCode.TIMED_OUT, "ocr.deadline")
            if not cancellation_sent and _cancellation_requested(cancellation):
                selected = self._worker.cancel(CancelReason.USER_REQUESTED)
                if selected != operation_id:
                    reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.cancel_binding")
                cancellation_sent = True
            self._worker.pump(self._policy.pump_interval_seconds)

    def _new_id(self, forbidden: set[UUID]) -> UUID:
        for _ in range(_MAXIMUM_IDENTIFIER_ATTEMPTS):
            try:
                value = self._id_factory()
            except BaseException:
                reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.identifier")
            if type(value) is UUID and value.version == 4 and value not in forbidden:
                return value
        reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.identifier")


def _operation_limits(policy: ResourceLimits, request: OcrProcessRequest) -> ResourceLimits:
    if (
        request.source_page_count > policy.page_count
        or request.source_byte_size >= policy.temporary_storage_bytes
        or policy.output_bytes > policy.temporary_storage_bytes - request.source_byte_size
    ):
        reject_ocr(OcrErrorCode.LIMIT_EXCEEDED, "ocr.limits")
    return ResourceLimits(
        wall_time_ms=policy.wall_time_ms,
        cpu_time_ms=policy.cpu_time_ms,
        memory_bytes=policy.memory_bytes,
        temporary_storage_bytes=policy.temporary_storage_bytes,
        output_bytes=policy.output_bytes,
        page_count=request.source_page_count,
        pixel_count=policy.pixel_count,
    )


def _operation_payload(
    request: OcrProcessRequest,
    *,
    source_handle: int,
    output_handle: int,
    result_handle: int,
    output_object_id: UUID,
    limits: ResourceLimits,
) -> OperationStartPayload:
    return OperationStartPayload(
        operation_kind=OCR_PROCESS_CAPABILITY.capability_id,
        operation_version=OCR_PROCESS_CAPABILITY.capability_version,
        inputs=(
            CaseObjectReference(
                area=ReferenceArea.SOURCE_READ,
                object_id=request.source_id,
                operation_id=None,
                content_kind=request.source_kind.value,
                expected_byte_size=request.source_byte_size,
                expected_sha256=request.source_sha256,
            ),
        ),
        parameters=(
            TaggedScalar(
                OCR_PROCESS_SOURCE_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                source_handle,
            ),
            TaggedScalar(
                OCR_PROCESS_OUTPUT_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                output_handle,
            ),
            TaggedScalar(
                OCR_PROCESS_RESULT_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                result_handle,
            ),
            TaggedScalar(
                OCR_PROCESS_OUTPUT_OBJECT_ID_PARAMETER,
                ScalarValueType.UUID,
                output_object_id,
            ),
            TaggedScalar(
                OCR_PROCESS_PAGE_COUNT_PARAMETER,
                ScalarValueType.INTEGER,
                request.source_page_count,
            ),
            TaggedScalar(
                OCR_PROCESS_LANGUAGE_SET_ID_PARAMETER,
                ScalarValueType.TOKEN,
                request.language_set_id,
            ),
            TaggedScalar(
                OCR_PROCESS_PROFILE_ID_PARAMETER,
                ScalarValueType.TOKEN,
                request.profile_id,
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
        reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.worker_result")
    if result.outcome is OperationOutcome.CANCELLED:
        reject_ocr(OcrErrorCode.CANCELLED, "ocr.worker_result")
    if result.outcome is OperationOutcome.TIMED_OUT:
        reject_ocr(OcrErrorCode.TIMED_OUT, "ocr.worker_result")
    if result.outcome is not OperationOutcome.SUCCEEDED or len(result.outputs) != 1:
        reject_ocr(OcrErrorCode.ENGINE_FAILED, "ocr.worker_result")
    output = result.outputs[0]
    if (
        output.area is not ReferenceArea.OPERATION_STAGING
        or output.object_id != output_object_id
        or output.operation_id != operation_id
        or output.content_kind != OCR_PROCESS_OUTPUT_CONTENT_KIND
        or output.expected_byte_size is None
        or not 1 <= output.expected_byte_size <= maximum_output_bytes
        or output.expected_sha256 is None
    ):
        reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.worker_output")
    return output


def _require_report(
    report: OcrWorkerReport,
    *,
    request: OcrProcessRequest,
    operation_id: UUID,
    output_object_id: UUID,
    output: CaseObjectReference,
) -> None:
    if (
        report.operation_id != operation_id
        or report.source_id != request.source_id
        or report.output_object_id != output_object_id
        or report.source_kind is not request.source_kind
        or report.page_count != request.source_page_count
        or report.processed_page_count != request.source_page_count
        or report.language_set_id != request.language_set_id
        or report.profile_id != request.profile_id
        or report.ocrmypdf_version != TRUSTED_OCRMYPDF_VERSION
        or report.tesseract_version != TRUSTED_TESSERACT_VERSION
        or not hmac.compare_digest(
            report.engine_profile_sha256,
            TRUSTED_OCR_ENGINE_PROFILE_SHA256,
        )
        or report.output_byte_size != output.expected_byte_size
        or output.expected_sha256 is None
        or not hmac.compare_digest(report.output_sha256, output.expected_sha256)
    ):
        reject_ocr(OcrErrorCode.RESULT_INVALID, "ocr.report_binding")


def _authenticate_output_buffer(
    buffer: OcrOutputBuffer,
    report: OcrWorkerReport,
    *,
    deadline: DeadlineProbe,
    cancellation: CancellationProbe,
) -> None:
    digest = hashlib.sha256()
    completed = 0
    try:
        _require_active(deadline, cancellation)
        for chunk in buffer.iter_chunks(
            report.output_byte_size,
            chunk_bytes=MAX_OCR_CANDIDATE_READ_BYTES,
        ):
            _require_active(deadline, cancellation)
            if type(chunk) is not bytes or not chunk:
                reject_ocr(OcrErrorCode.OUTPUT_INVALID, "ocr.output_chunk")
            completed += len(chunk)
            if completed > report.output_byte_size:
                reject_ocr(OcrErrorCode.OUTPUT_INVALID, "ocr.output_size")
            digest.update(chunk)
            _require_active(deadline, cancellation)
    except OcrError:
        raise
    except Exception:
        reject_ocr(OcrErrorCode.OUTPUT_INVALID, "ocr.output_read")
    if completed != report.output_byte_size or not hmac.compare_digest(
        digest.hexdigest(), report.output_sha256
    ):
        reject_ocr(OcrErrorCode.OUTPUT_INVALID, "ocr.output_digest")
    _require_active(deadline, cancellation)


def _require_source_binding(request: OcrProcessRequest, source: OcrSourceLease) -> None:
    try:
        valid = (
            source.case_id == request.case_id
            and source.source_id == request.source_id
            and source.source_kind is request.source_kind
            and source.byte_size == request.source_byte_size
            and source.page_count == request.source_page_count
            and source.sha256 == request.source_sha256
            and source.signature_state is request.signature_state
            and source.encryption_state is request.encryption_state
            and type(source.handle_value) is int
            and source.handle_value > 0
        )
    except SourceIngestError:
        raise
    except Exception:
        valid = False
    if not valid:
        reject_ocr(OcrErrorCode.SOURCE_INVALID, "ocr.source_binding")


def _report_progress(sink: OcrProgressSink, progress: OcrProgress) -> None:
    try:
        value = sink(progress)
    except OcrError:
        raise
    except BaseException:
        reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.progress")
    if value is not None:
        reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.progress")


def _require_active(deadline: DeadlineProbe, cancellation: CancellationProbe) -> None:
    if _deadline_expired(deadline):
        reject_ocr(OcrErrorCode.TIMED_OUT, "ocr.deadline")
    if _cancellation_requested(cancellation):
        reject_ocr(OcrErrorCode.CANCELLED, "ocr.cancellation")


def _deadline_expired(probe: DeadlineProbe) -> bool:
    try:
        value = probe()
    except BaseException:
        reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.deadline_probe")
    if type(value) is not bool:
        reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.deadline_probe")
    return value


def _cancellation_requested(probe: CancellationProbe) -> bool:
    try:
        value = probe()
    except BaseException:
        reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.cancellation_probe")
    if type(value) is not bool:
        reject_ocr(OcrErrorCode.INTERNAL_ERROR, "ocr.cancellation_probe")
    return value


def _raise_source_error(error: SourceIngestError) -> Never:
    if error.code is SourceIngestErrorCode.CANCELLED:
        reject_ocr(OcrErrorCode.CANCELLED, "ocr.source")
    if error.code is SourceIngestErrorCode.TIMED_OUT:
        reject_ocr(OcrErrorCode.TIMED_OUT, "ocr.source")
    if error.code is SourceIngestErrorCode.CAPACITY_EXCEEDED:
        reject_ocr(OcrErrorCode.LIMIT_EXCEEDED, "ocr.source")
    reject_ocr(OcrErrorCode.SOURCE_INVALID, "ocr.source")

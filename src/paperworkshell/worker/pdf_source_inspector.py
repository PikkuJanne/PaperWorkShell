"""GUI-process broker for one-shot, handle-bound PDF structural inspection."""

from __future__ import annotations

import math
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID, uuid4

from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.pdf_inspection import (
    MAX_PDF_INSPECTION_FRAME_BYTES,
    MAX_PDF_INSPECTION_PAGES,
    PdfFeatureState,
    PdfInspectionError,
    PdfInspectionErrorCode,
    PdfInspectionResult,
    PdfOpenState,
    decode_pdf_inspection_frame,
    reject_pdf_inspection,
)
from paperworkshell.domain.source_ingest import (
    CompletedSourceInspection,
    PreparedSourceSummary,
    SourceEncryptionState,
    SourceImportProgress,
    SourceIngestError,
    SourceIngestErrorCode,
    SourceProgressPhase,
    SourceSignatureState,
    reject_source_ingest,
)
from paperworkshell.worker._windows_broker import WindowsResultPipe
from paperworkshell.worker.client.errors import WorkerClientError, WorkerClientErrorCode
from paperworkshell.worker.client.models import (
    WorkerClientSnapshot,
    WorkerLifecyclePhase,
    WorkerOperationResultSnapshot,
)
from paperworkshell.worker.client.process import BrokeredHandlePurpose
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator, HeavyOperationLease
from paperworkshell.worker.protocol.models import (
    PDF_INSPECT_CAPABILITY,
    PDF_INSPECT_RESULT_HANDLE_PARAMETER,
    PDF_INSPECT_SOURCE_HANDLE_PARAMETER,
    CancelReason,
    CaseObjectReference,
    OperationOutcome,
    OperationStartPayload,
    ReferenceArea,
    ResourceLimits,
    ScalarValueType,
    TaggedScalar,
)


class DeadlineProbe(Protocol):
    """Return whether the enclosing source-import deadline has expired."""

    def __call__(self) -> bool: ...


class CancellationProbe(Protocol):
    """Return whether cooperative cancellation has been requested."""

    def __call__(self) -> bool: ...


class ProgressSink(Protocol):
    """Receive one already validated, path-free import progress record."""

    def __call__(self, progress: SourceImportProgress, /) -> None: ...


class PreparedSourceHandleLease(Protocol):
    """Independent local read authority for one authenticated pending source."""

    @property
    def summary(self) -> PreparedSourceSummary: ...

    @property
    def handle_value(self) -> int: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class PreparedSourceHandleBroker(Protocol):
    """Resolve one full prepared summary to its exact retained file object."""

    def borrow_prepared_source(
        self,
        summary: PreparedSourceSummary,
    ) -> PreparedSourceHandleLease: ...


class ResultPipe(Protocol):
    """Bounded out-of-band result channel whose reader remains broker-owned."""

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


class ResultPipeFactory(Protocol):
    """Create one non-inheritable result channel per worker generation."""

    def __call__(self) -> ResultPipe: ...


class PdfWorkerCoordinator(Protocol):
    """Narrow single-owner client surface required by this one-shot broker."""

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


@dataclass(frozen=True, slots=True)
class PdfInspectionBrokerPolicy:
    """Trusted limits and responsiveness for one brokered PDF operation."""

    limits: ResourceLimits
    pump_interval_seconds: float = 0.050

    def __post_init__(self) -> None:
        interval = self.pump_interval_seconds
        if (
            type(self.limits) is not ResourceLimits
            or type(interval) is not float
            or not math.isfinite(interval)
            or not 0 < interval <= 1.0
            or self.limits.page_count > MAX_PDF_INSPECTION_PAGES
            or self.limits.output_bytes < MAX_PDF_INSPECTION_FRAME_BYTES
        ):
            reject_pdf_inspection(PdfInspectionErrorCode.MODEL_INVALID, "broker.policy")


class BrokeredPdfSourceInspector:
    """Inspect one sealed PDF with exact handles and a fresh worker generation."""

    __slots__ = (
        "_broker",
        "_heavy_operations",
        "_id_factory",
        "_pipe_factory",
        "_policy",
        "_worker",
    )

    def __init__(
        self,
        worker: PdfWorkerCoordinator,
        broker: PreparedSourceHandleBroker,
        policy: PdfInspectionBrokerPolicy,
        *,
        heavy_operations: HeavyOperationCoordinator,
        pipe_factory: ResultPipeFactory = WindowsResultPipe.create,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if (
            type(policy) is not PdfInspectionBrokerPolicy
            or type(heavy_operations) is not HeavyOperationCoordinator
            or not callable(pipe_factory)
            or not callable(id_factory)
        ):
            reject_pdf_inspection(PdfInspectionErrorCode.MODEL_INVALID, "broker.configuration")
        self._worker = worker
        self._broker = broker
        self._policy = policy
        self._heavy_operations = heavy_operations
        self._pipe_factory = pipe_factory
        self._id_factory = id_factory

    def inspect(
        self,
        source: PreparedSourceSummary,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ProgressSink,
    ) -> CompletedSourceInspection:
        """Implement the application SourceInspector port with conservative metadata."""

        if (
            type(source) is not PreparedSourceSummary
            or source.media_type is not DetectedMediaType.PDF
        ):
            reject_source_ingest(SourceIngestErrorCode.INSPECTION_INVALID, "inspection.pdf_source")
        _report_progress(progress, source.pending_id, 0)
        try:
            result = self.inspect_structural(
                source,
                deadline=deadline,
                cancellation=cancellation,
            )
        except SourceIngestError:
            raise
        except PdfInspectionError as error:
            _raise_source_error(error)

        if result.open_state is PdfOpenState.ENCRYPTED:
            reject_source_ingest(
                SourceIngestErrorCode.INSPECTION_INVALID,
                "inspection.pdf_encrypted",
            )
        if result.open_state is PdfOpenState.MALFORMED:
            reject_source_ingest(
                SourceIngestErrorCode.INSPECTION_INVALID,
                "inspection.pdf_malformed",
            )
        if result.page_count is None:
            reject_source_ingest(SourceIngestErrorCode.INSPECTION_INVALID, "inspection.pdf_result")
        signature_state = (
            SourceSignatureState.MARKERS_DETECTED
            if result.features.signature_markers is PdfFeatureState.DETECTED
            else SourceSignatureState.NONE_DETECTED
        )
        completed = CompletedSourceInspection(
            pending_id=source.pending_id,
            media_type=source.media_type,
            page_count=result.page_count,
            signature_state=signature_state,
            encryption_state=SourceEncryptionState.UNENCRYPTED,
        )
        _report_progress(progress, source.pending_id, 1)
        return completed

    def inspect_structural(
        self,
        source: PreparedSourceSummary,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> PdfInspectionResult:
        """Return one correlated typed report after the worker tree is terminated."""

        try:
            with self._acquire_heavy_operation(deadline, cancellation):
                return self._inspect_structural(
                    source, deadline=deadline, cancellation=cancellation
                )
        except PdfInspectionError, SourceIngestError:
            raise
        except WorkerClientError as error:
            if error.code in {
                WorkerClientErrorCode.HANDSHAKE_TIMED_OUT,
                WorkerClientErrorCode.OPERATION_TIMED_OUT,
            }:
                reject_pdf_inspection(PdfInspectionErrorCode.TIMED_OUT, "broker.worker_timeout")
            reject_pdf_inspection(PdfInspectionErrorCode.WORKER_FAILED, "broker.worker")
        except Exception:
            reject_pdf_inspection(PdfInspectionErrorCode.INTERNAL_ERROR, "broker.operation")

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

    def _inspect_structural(
        self,
        source: PreparedSourceSummary,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> PdfInspectionResult:
        if (
            type(source) is not PreparedSourceSummary
            or source.media_type is not DetectedMediaType.PDF
        ):
            reject_pdf_inspection(PdfInspectionErrorCode.SOURCE_INVALID, "broker.source")
        if source.byte_size > self._policy.limits.temporary_storage_bytes:
            reject_pdf_inspection(PdfInspectionErrorCode.LIMIT_EXCEEDED, "broker.source_size")
        operation_id = self._new_id()
        if operation_id == source.pending_id:
            reject_pdf_inspection(PdfInspectionErrorCode.INTERNAL_ERROR, "broker.identifier")
        operation_result: WorkerOperationResultSnapshot | None = None
        start_attempted = False

        with ExitStack() as authorities:
            pipe = authorities.enter_context(self._pipe_factory())
            borrowed = authorities.enter_context(self._broker.borrow_prepared_source(source))
            if borrowed.summary != source:
                reject_pdf_inspection(
                    PdfInspectionErrorCode.SOURCE_CHANGED, "broker.source_binding"
                )
            try:
                _require_active(deadline, cancellation)
                start_attempted = True
                self._worker.start(source.case_id)
                ready = self._wait_ready(deadline, cancellation)
                if PDF_INSPECT_CAPABILITY not in ready.capabilities:
                    reject_pdf_inspection(
                        PdfInspectionErrorCode.DEPENDENCY_UNAVAILABLE,
                        "broker.capability",
                    )

                source_handle = self._worker.duplicate_handle(
                    borrowed.handle_value,
                    BrokeredHandlePurpose.READ_SOURCE,
                )
                result_handle = self._worker.duplicate_handle(
                    pipe.write_handle_value,
                    BrokeredHandlePurpose.WRITE_RESULT_PIPE,
                )
                pipe.close_local_writer()
                payload = _operation_payload(
                    source,
                    source_handle=source_handle,
                    result_handle=result_handle,
                    limits=self._policy.limits,
                )
                selected_operation = self._worker.start_operation(
                    payload,
                    operation_id=operation_id,
                )
                if selected_operation != operation_id:
                    reject_pdf_inspection(
                        PdfInspectionErrorCode.REPORT_INVALID,
                        "broker.operation_binding",
                    )
                operation_result = self._wait_result(
                    operation_id,
                    deadline=deadline,
                    cancellation=cancellation,
                )
            finally:
                if start_attempted:
                    # The one-shot worker tree is gone before local source and
                    # result-channel authorities can be released or reused.
                    self._worker.terminate()

            if operation_result is None:
                reject_pdf_inspection(PdfInspectionErrorCode.WORKER_FAILED, "broker.no_result")
            if operation_result.outcome is OperationOutcome.CANCELLED:
                reject_pdf_inspection(PdfInspectionErrorCode.CANCELLED, "broker.cancelled")
            if operation_result.outcome is OperationOutcome.TIMED_OUT:
                reject_pdf_inspection(PdfInspectionErrorCode.TIMED_OUT, "broker.timed_out")
            if operation_result.outcome is not OperationOutcome.SUCCEEDED:
                reject_pdf_inspection(PdfInspectionErrorCode.WORKER_FAILED, "broker.failed")

            envelope = decode_pdf_inspection_frame(pipe.read_complete_frame())
            if (
                envelope.operation_id != operation_id
                or envelope.source_object_id != source.pending_id
            ):
                reject_pdf_inspection(PdfInspectionErrorCode.REPORT_INVALID, "broker.correlation")
            return envelope.result

    def _wait_ready(
        self,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> WorkerClientSnapshot:
        while True:
            _require_active(deadline, cancellation)
            snapshot = self._worker.query()
            if snapshot.phase is WorkerLifecyclePhase.READY:
                return snapshot
            if snapshot.phase in {WorkerLifecyclePhase.STOPPED, WorkerLifecyclePhase.FAULTED}:
                reject_pdf_inspection(PdfInspectionErrorCode.WORKER_FAILED, "broker.handshake")
            self._worker.pump(self._policy.pump_interval_seconds)

    def _wait_result(
        self,
        operation_id: UUID,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> WorkerOperationResultSnapshot:
        cancellation_sent = False
        while True:
            if _deadline_expired(deadline):
                if not cancellation_sent:
                    self._worker.cancel(CancelReason.SHUTDOWN)
                reject_pdf_inspection(PdfInspectionErrorCode.TIMED_OUT, "broker.deadline")
            if _cancellation_requested(cancellation) and not cancellation_sent:
                self._worker.cancel(CancelReason.USER_REQUESTED)
                cancellation_sent = True

            snapshot = self._worker.query()
            result = snapshot.last_result
            if result is not None:
                if result.operation_id != operation_id:
                    reject_pdf_inspection(
                        PdfInspectionErrorCode.REPORT_INVALID, "broker.result_binding"
                    )
                if cancellation_sent and result.outcome is OperationOutcome.SUCCEEDED:
                    reject_pdf_inspection(PdfInspectionErrorCode.CANCELLED, "broker.cancelled")
                return result
            if snapshot.phase in {WorkerLifecyclePhase.STOPPED, WorkerLifecyclePhase.FAULTED}:
                reject_pdf_inspection(PdfInspectionErrorCode.WORKER_FAILED, "broker.worker_exit")
            self._worker.pump(self._policy.pump_interval_seconds)

    def _new_id(self) -> UUID:
        try:
            value = self._id_factory()
        except BaseException:
            reject_pdf_inspection(PdfInspectionErrorCode.INTERNAL_ERROR, "broker.identifier")
        if type(value) is not UUID or value.version != 4:
            reject_pdf_inspection(PdfInspectionErrorCode.INTERNAL_ERROR, "broker.identifier")
        return value


def _operation_payload(
    source: PreparedSourceSummary,
    *,
    source_handle: int,
    result_handle: int,
    limits: ResourceLimits,
) -> OperationStartPayload:
    return OperationStartPayload(
        operation_kind=PDF_INSPECT_CAPABILITY.capability_id,
        operation_version=PDF_INSPECT_CAPABILITY.capability_version,
        inputs=(
            CaseObjectReference(
                area=ReferenceArea.SOURCE_READ,
                object_id=source.pending_id,
                operation_id=None,
                content_kind="pdf",
                expected_byte_size=source.byte_size,
                expected_sha256=source.sha256,
            ),
        ),
        parameters=(
            TaggedScalar(
                PDF_INSPECT_SOURCE_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                source_handle,
            ),
            TaggedScalar(
                PDF_INSPECT_RESULT_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                result_handle,
            ),
        ),
        requested_limits=limits,
    )


def _require_active(deadline: DeadlineProbe, cancellation: CancellationProbe) -> None:
    if _deadline_expired(deadline):
        reject_pdf_inspection(PdfInspectionErrorCode.TIMED_OUT, "broker.deadline")
    if _cancellation_requested(cancellation):
        reject_pdf_inspection(PdfInspectionErrorCode.CANCELLED, "broker.cancelled")


def _deadline_expired(probe: DeadlineProbe) -> bool:
    try:
        value = probe()
    except SourceIngestError:
        raise
    except BaseException:
        reject_pdf_inspection(PdfInspectionErrorCode.INTERNAL_ERROR, "broker.deadline_probe")
    if type(value) is not bool:
        reject_pdf_inspection(PdfInspectionErrorCode.INTERNAL_ERROR, "broker.deadline_probe")
    return value


def _cancellation_requested(probe: CancellationProbe) -> bool:
    try:
        value = probe()
    except SourceIngestError:
        raise
    except BaseException:
        reject_pdf_inspection(PdfInspectionErrorCode.INTERNAL_ERROR, "broker.cancel_probe")
    if type(value) is not bool:
        reject_pdf_inspection(PdfInspectionErrorCode.INTERNAL_ERROR, "broker.cancel_probe")
    return value


def _report_progress(sink: ProgressSink, operation_id: UUID, completed: int) -> None:
    try:
        sink(SourceImportProgress(operation_id, SourceProgressPhase.INSPECTING, completed, 1))
    except SourceIngestError:
        raise
    except BaseException:
        reject_source_ingest(SourceIngestErrorCode.PROGRESS_FAILED, "inspection.progress")


def _raise_source_error(error: PdfInspectionError) -> None:
    if error.code is PdfInspectionErrorCode.CANCELLED:
        reject_source_ingest(SourceIngestErrorCode.CANCELLED, "inspection.cancelled")
    if error.code is PdfInspectionErrorCode.TIMED_OUT:
        reject_source_ingest(SourceIngestErrorCode.TIMED_OUT, "inspection.timed_out")
    if error.code in {
        PdfInspectionErrorCode.SOURCE_INVALID,
        PdfInspectionErrorCode.SOURCE_CHANGED,
    }:
        reject_source_ingest(SourceIngestErrorCode.SOURCE_CHANGED, "inspection.source")
    if error.code is PdfInspectionErrorCode.LIMIT_EXCEEDED:
        reject_source_ingest(SourceIngestErrorCode.INSPECTION_INVALID, "inspection.limit")
    reject_source_ingest(SourceIngestErrorCode.INSPECT_FAILED, "inspection.worker")

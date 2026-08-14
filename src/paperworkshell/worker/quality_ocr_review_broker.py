"""One-shot broker for exact, content-free OCR quality observations."""

from __future__ import annotations

import hmac
import math
from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Never, Protocol, Self
from uuid import UUID, uuid4

from paperworkshell.domain.pdf_inspection import PdfInspectionError, PdfInspectionErrorCode
from paperworkshell.domain.quality_ocr_review import (
    MAX_QUALITY_OCR_REVIEW_REPORT_BYTES,
    MAX_QUALITY_OCR_REVIEW_TSV_BYTES,
    QualityOcrReviewError,
    QualityOcrReviewErrorCode,
    QualityOcrReviewReport,
    QualityOcrReviewRequest,
    decode_quality_ocr_review_report,
    reject_quality_ocr_review,
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
    QUALITY_OCR_REVIEW_CAPABILITY,
    QUALITY_OCR_REVIEW_HEIGHT_PARAMETER,
    QUALITY_OCR_REVIEW_LANGUAGE_SET_ID_PARAMETER,
    QUALITY_OCR_REVIEW_MANIFEST_REVISION_PARAMETER,
    QUALITY_OCR_REVIEW_PAGE_ID_PARAMETER,
    QUALITY_OCR_REVIEW_PRESENTATION_SHA256_PARAMETER,
    QUALITY_OCR_REVIEW_PROFILE_ID_PARAMETER,
    QUALITY_OCR_REVIEW_RESULT_HANDLE_PARAMETER,
    QUALITY_OCR_REVIEW_ROW_STRIDE_PARAMETER,
    QUALITY_OCR_REVIEW_SAMPLE_BYTES_PARAMETER,
    QUALITY_OCR_REVIEW_SAMPLE_HANDLE_PARAMETER,
    QUALITY_OCR_REVIEW_SAMPLE_SHA256_PARAMETER,
    QUALITY_OCR_REVIEW_SHA256_TOKEN_PREFIX,
    QUALITY_OCR_REVIEW_WIDTH_PARAMETER,
    CancelReason,
    ErrorCategory,
    ErrorCode,
    OperationOutcome,
    OperationStartPayload,
    ProgressStage,
    ProgressUnit,
    ResourceLimits,
    ScalarValueType,
    TaggedScalar,
)
from paperworkshell.worker.quality_ocr_review_buffers import WindowsQualityOcrInputBuffer

_MAXIMUM_IDENTIFIER_ATTEMPTS = 32

DeadlineProbe = Callable[[], bool]
CancellationProbe = Callable[[], bool]


class QualityOcrReviewWorkerCoordinator(Protocol):
    """Narrow one-shot client surface used by the OCR-review broker."""

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


class QualityOcrInputBuffer(Protocol):
    @property
    def payload_size(self) -> int: ...

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


class QualityOcrResultPipe(Protocol):
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


InputBufferFactory = Callable[[bytes], QualityOcrInputBuffer]
ResultPipeFactory = Callable[[], QualityOcrResultPipe]


@dataclass(frozen=True, slots=True)
class QualityOcrReviewBrokerPolicy:
    """Central-policy-derived bounds for one 256-pixel page observation."""

    limits: ResourceLimits
    pump_interval_seconds: float = 0.050

    def __post_init__(self) -> None:
        if (
            type(self.limits) is not ResourceLimits
            or self.limits.page_count != 1
            or not 1 <= self.limits.output_bytes <= MAX_QUALITY_OCR_REVIEW_TSV_BYTES
            or self.limits.pixel_count < 1
            or type(self.pump_interval_seconds) is not float
            or not math.isfinite(self.pump_interval_seconds)
            or not 0.0 < self.pump_interval_seconds <= 1.0
        ):
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.MODEL_INVALID,
                "quality_ocr.broker_policy",
            )


class BrokeredQualityOcrReviewer:
    """Launch exactly one isolated worker and authenticate its private report."""

    __slots__ = (
        "_heavy_operations",
        "_id_factory",
        "_input_buffer_factory",
        "_pipe_factory",
        "_policy",
        "_revoked",
        "_worker",
    )

    def __init__(
        self,
        worker: QualityOcrReviewWorkerCoordinator,
        policy: QualityOcrReviewBrokerPolicy,
        *,
        heavy_operations: HeavyOperationCoordinator,
        input_buffer_factory: InputBufferFactory = WindowsQualityOcrInputBuffer.create,
        pipe_factory: ResultPipeFactory | None = None,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if pipe_factory is None:
            from paperworkshell.worker._windows_broker import WindowsResultPipe

            pipe_factory = WindowsResultPipe.create
        if (
            type(policy) is not QualityOcrReviewBrokerPolicy
            or type(heavy_operations) is not HeavyOperationCoordinator
            or not callable(input_buffer_factory)
            or not callable(pipe_factory)
            or not callable(id_factory)
        ):
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.MODEL_INVALID,
                "quality_ocr.broker",
            )
        self._worker = worker
        self._policy = policy
        self._heavy_operations = heavy_operations
        self._input_buffer_factory = input_buffer_factory
        self._pipe_factory = pipe_factory
        self._id_factory = id_factory
        self._revoked = False

    @property
    def revoked(self) -> bool:
        return self._revoked

    def _revoke_permanently(self) -> None:
        self._revoked = True

    def review(
        self,
        request: QualityOcrReviewRequest,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> QualityOcrReviewReport:
        if self._revoked:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.ENGINE_UNAVAILABLE,
                "quality_ocr.composition_closed",
            )
        if (
            type(request) is not QualityOcrReviewRequest
            or not callable(deadline)
            or not callable(cancellation)
        ):
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.MODEL_INVALID,
                "quality_ocr.broker_request",
            )
        try:
            with self._acquire_heavy_operation(deadline, cancellation):
                return self._review(request, deadline=deadline, cancellation=cancellation)
        except QualityOcrReviewError:
            raise
        except WorkerClientError as error:
            _rebuild_worker_client_error(error)
        except PdfInspectionError as error:
            _rebuild_report_pipe_error(error)
        except Exception:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.INTERNAL_ERROR,
                "quality_ocr.broker",
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

    def _review(
        self,
        request: QualityOcrReviewRequest,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> QualityOcrReviewReport:
        _require_active(deadline, cancellation)
        limits = _operation_limits(self._policy.limits, request)
        operation_id = self._new_id({request.case_id, request.page_id})
        with self._input_buffer_factory(request.pixels) as input_buffer:
            if (
                type(input_buffer.payload_size) is not int
                or input_buffer.payload_size != request.sample_byte_size
                or type(input_buffer.read_handle_value) is not int
                or input_buffer.read_handle_value <= 0
            ):
                reject_quality_ocr_review(
                    QualityOcrReviewErrorCode.RESULT_INVALID,
                    "quality_ocr.input_buffer",
                )
            result: WorkerOperationResultSnapshot | None = None
            generation: int | None = None
            start_attempted = False
            with self._pipe_factory() as pipe:
                try:
                    start_attempted = True
                    generation = self._worker.start(request.case_id)
                    ready = self._wait_ready(
                        request.case_id,
                        generation,
                        deadline=deadline,
                        cancellation=cancellation,
                    )
                    if QUALITY_OCR_REVIEW_CAPABILITY not in ready.capabilities:
                        reject_quality_ocr_review(
                            QualityOcrReviewErrorCode.ENGINE_UNAVAILABLE,
                            "quality_ocr.capability",
                        )
                    sample_handle = self._worker.duplicate_handle(
                        input_buffer.read_handle_value,
                        BrokeredHandlePurpose.READ_BOUNDED_INPUT_SECTION,
                    )
                    result_handle = self._worker.duplicate_handle(
                        pipe.write_handle_value,
                        BrokeredHandlePurpose.WRITE_RESULT_PIPE,
                    )
                    if (
                        type(sample_handle) is not int
                        or sample_handle <= 0
                        or type(result_handle) is not int
                        or result_handle <= 0
                        or sample_handle == result_handle
                    ):
                        reject_quality_ocr_review(
                            QualityOcrReviewErrorCode.INTERNAL_ERROR,
                            "quality_ocr.worker_handles",
                        )
                    pipe.close_local_writer()
                    selected = self._worker.start_operation(
                        _operation_payload(
                            request,
                            sample_handle=sample_handle,
                            result_handle=result_handle,
                            limits=limits,
                        ),
                        operation_id=operation_id,
                    )
                    if selected != operation_id:
                        reject_quality_ocr_review(
                            QualityOcrReviewErrorCode.RESULT_INVALID,
                            "quality_ocr.operation_binding",
                        )
                    result = self._wait_result(
                        operation_id,
                        generation,
                        deadline=deadline,
                        cancellation=cancellation,
                    )
                finally:
                    if start_attempted:
                        self._worker.terminate()
                _require_success(result, generation, operation_id)
                _require_active(deadline, cancellation)
                try:
                    report_payload = pipe.read_complete_frame()
                except Exception:
                    reject_quality_ocr_review(
                        QualityOcrReviewErrorCode.RESULT_INVALID,
                        "quality_ocr.report_read",
                    )
                if len(report_payload) > MAX_QUALITY_OCR_REVIEW_REPORT_BYTES:
                    reject_quality_ocr_review(
                        QualityOcrReviewErrorCode.RESULT_INVALID,
                        "quality_ocr.report_size",
                    )
                report = decode_quality_ocr_review_report(report_payload)
                _require_report_binding(report, request, operation_id)
                _require_active(deadline, cancellation)
                return report

    def _wait_ready(
        self,
        case_id: UUID,
        generation: int,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> WorkerClientSnapshot:
        while True:
            _require_active(deadline, cancellation)
            snapshot = self._worker.query()
            if snapshot.generation != generation:
                reject_quality_ocr_review(
                    QualityOcrReviewErrorCode.INTERNAL_ERROR,
                    "quality_ocr.generation",
                )
            if snapshot.phase is WorkerLifecyclePhase.READY:
                if snapshot.case_id != case_id:
                    reject_quality_ocr_review(
                        QualityOcrReviewErrorCode.RESULT_INVALID,
                        "quality_ocr.case_binding",
                    )
                return snapshot
            if snapshot.phase in {WorkerLifecyclePhase.STOPPED, WorkerLifecyclePhase.FAULTED}:
                reject_quality_ocr_review(
                    QualityOcrReviewErrorCode.INTERNAL_ERROR,
                    "quality_ocr.handshake",
                )
            self._worker.pump(self._policy.pump_interval_seconds)

    def _wait_result(
        self,
        operation_id: UUID,
        generation: int,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> WorkerOperationResultSnapshot:
        cancellation_sent = False
        while True:
            snapshot = self._worker.query()
            if snapshot.generation != generation:
                reject_quality_ocr_review(
                    QualityOcrReviewErrorCode.INTERNAL_ERROR,
                    "quality_ocr.generation",
                )
            result = snapshot.last_result
            if result is not None:
                if result.operation_id != operation_id:
                    reject_quality_ocr_review(
                        QualityOcrReviewErrorCode.RESULT_INVALID,
                        "quality_ocr.result_binding",
                    )
                if cancellation_sent and result.outcome is OperationOutcome.SUCCEEDED:
                    reject_quality_ocr_review(
                        QualityOcrReviewErrorCode.CANCELLED,
                        "quality_ocr.cancelled",
                    )
                return result
            active = snapshot.active_operation
            if active is not None and active.operation_id == operation_id:
                progress = active.progress
                if progress is not None and (
                    progress.stage is not ProgressStage.PROCESSING
                    or progress.unit is not ProgressUnit.PAGES
                    or progress.total_units != 1
                    or not 0 <= progress.completed_units <= 1
                ):
                    reject_quality_ocr_review(
                        QualityOcrReviewErrorCode.RESULT_INVALID,
                        "quality_ocr.progress",
                    )
            if snapshot.phase in {
                WorkerLifecyclePhase.READY,
                WorkerLifecyclePhase.STOPPED,
                WorkerLifecyclePhase.FAULTED,
            }:
                reject_quality_ocr_review(
                    QualityOcrReviewErrorCode.INTERNAL_ERROR,
                    "quality_ocr.worker_exit",
                )
            if _deadline_expired(deadline):
                if not cancellation_sent:
                    self._worker.cancel(CancelReason.SHUTDOWN)
                reject_quality_ocr_review(
                    QualityOcrReviewErrorCode.TIMED_OUT,
                    "quality_ocr.deadline",
                )
            if not cancellation_sent and _cancellation_requested(cancellation):
                selected = self._worker.cancel(CancelReason.USER_REQUESTED)
                if selected != operation_id:
                    reject_quality_ocr_review(
                        QualityOcrReviewErrorCode.RESULT_INVALID,
                        "quality_ocr.cancel_binding",
                    )
                cancellation_sent = True
            self._worker.pump(self._policy.pump_interval_seconds)

    def _new_id(self, forbidden: set[UUID]) -> UUID:
        for _ in range(_MAXIMUM_IDENTIFIER_ATTEMPTS):
            try:
                value = self._id_factory()
            except BaseException:
                reject_quality_ocr_review(
                    QualityOcrReviewErrorCode.INTERNAL_ERROR,
                    "quality_ocr.identifier",
                )
            if type(value) is UUID and value.version == 4 and value not in forbidden:
                return value
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.INTERNAL_ERROR,
            "quality_ocr.identifier",
        )


def _operation_limits(
    policy: ResourceLimits,
    request: QualityOcrReviewRequest,
) -> ResourceLimits:
    ppm_bytes = 32 + request.width_pixels * request.height_pixels * 3
    if (
        request.width_pixels * request.height_pixels > policy.pixel_count
        or request.sample_byte_size > policy.memory_bytes
        or policy.output_bytes > MAX_QUALITY_OCR_REVIEW_TSV_BYTES
        or policy.temporary_storage_bytes
        < request.sample_byte_size + ppm_bytes + policy.output_bytes
    ):
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.LIMIT_EXCEEDED,
            "quality_ocr.limits",
        )
    return ResourceLimits(
        wall_time_ms=policy.wall_time_ms,
        cpu_time_ms=policy.cpu_time_ms,
        memory_bytes=policy.memory_bytes,
        temporary_storage_bytes=policy.temporary_storage_bytes,
        output_bytes=policy.output_bytes,
        page_count=1,
        pixel_count=request.width_pixels * request.height_pixels,
    )


def _operation_payload(
    request: QualityOcrReviewRequest,
    *,
    sample_handle: int,
    result_handle: int,
    limits: ResourceLimits,
) -> OperationStartPayload:
    return OperationStartPayload(
        operation_kind=QUALITY_OCR_REVIEW_CAPABILITY.capability_id,
        operation_version=QUALITY_OCR_REVIEW_CAPABILITY.capability_version,
        inputs=(),
        parameters=(
            TaggedScalar(
                QUALITY_OCR_REVIEW_SAMPLE_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                sample_handle,
            ),
            TaggedScalar(
                QUALITY_OCR_REVIEW_RESULT_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                result_handle,
            ),
            TaggedScalar(
                QUALITY_OCR_REVIEW_PAGE_ID_PARAMETER,
                ScalarValueType.UUID,
                request.page_id,
            ),
            TaggedScalar(
                QUALITY_OCR_REVIEW_MANIFEST_REVISION_PARAMETER,
                ScalarValueType.INTEGER,
                request.manifest_revision,
            ),
            TaggedScalar(
                QUALITY_OCR_REVIEW_WIDTH_PARAMETER,
                ScalarValueType.INTEGER,
                request.width_pixels,
            ),
            TaggedScalar(
                QUALITY_OCR_REVIEW_HEIGHT_PARAMETER,
                ScalarValueType.INTEGER,
                request.height_pixels,
            ),
            TaggedScalar(
                QUALITY_OCR_REVIEW_ROW_STRIDE_PARAMETER,
                ScalarValueType.INTEGER,
                request.row_stride_bytes,
            ),
            TaggedScalar(
                QUALITY_OCR_REVIEW_SAMPLE_BYTES_PARAMETER,
                ScalarValueType.INTEGER,
                request.sample_byte_size,
            ),
            TaggedScalar(
                QUALITY_OCR_REVIEW_SAMPLE_SHA256_PARAMETER,
                ScalarValueType.TOKEN,
                f"{QUALITY_OCR_REVIEW_SHA256_TOKEN_PREFIX}{request.sample_sha256}",
            ),
            TaggedScalar(
                QUALITY_OCR_REVIEW_PRESENTATION_SHA256_PARAMETER,
                ScalarValueType.TOKEN,
                f"{QUALITY_OCR_REVIEW_SHA256_TOKEN_PREFIX}{request.presentation_sha256}",
            ),
            TaggedScalar(
                QUALITY_OCR_REVIEW_LANGUAGE_SET_ID_PARAMETER,
                ScalarValueType.TOKEN,
                request.language_set_id,
            ),
            TaggedScalar(
                QUALITY_OCR_REVIEW_PROFILE_ID_PARAMETER,
                ScalarValueType.TOKEN,
                request.profile_id,
            ),
        ),
        requested_limits=limits,
    )


def _require_success(
    result: WorkerOperationResultSnapshot | None,
    generation: int | None,
    operation_id: UUID,
) -> None:
    if (
        type(result) is not WorkerOperationResultSnapshot
        or type(generation) is not int
        or result.generation != generation
        or result.operation_id != operation_id
        or result.outputs
    ):
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.RESULT_INVALID,
            "quality_ocr.worker_result",
        )
    if result.outcome is OperationOutcome.CANCELLED:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.CANCELLED,
            "quality_ocr.worker_result",
        )
    if result.outcome is OperationOutcome.TIMED_OUT:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.TIMED_OUT,
            "quality_ocr.worker_result",
        )
    if result.outcome is OperationOutcome.FAILED:
        if (
            result.error_code is not ErrorCode.WORKER_OPERATION_FAILED
            or result.error_category is None
        ):
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.RESULT_INVALID,
                "quality_ocr.worker_result",
            )
        if result.error_category is ErrorCategory.RESOURCE_LIMIT:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.LIMIT_EXCEEDED,
                "quality_ocr.worker_result",
            )
        if result.error_category is ErrorCategory.PROTOCOL:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.RESULT_INVALID,
                "quality_ocr.worker_result",
            )
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.INTERNAL_ERROR,
            "quality_ocr.worker_result",
        )
    if (
        result.outcome is not OperationOutcome.SUCCEEDED
        or result.error_code is not None
        or result.error_category is not None
    ):
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.RESULT_INVALID,
            "quality_ocr.worker_result",
        )


def _rebuild_worker_client_error(error: WorkerClientError) -> Never:
    if type(error) is not WorkerClientError:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.INTERNAL_ERROR,
            "quality_ocr.worker",
        )
    if error.code in {
        WorkerClientErrorCode.HANDSHAKE_TIMED_OUT,
        WorkerClientErrorCode.OPERATION_TIMED_OUT,
        WorkerClientErrorCode.CANCELLATION_TIMED_OUT,
    }:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.TIMED_OUT,
            "quality_ocr.worker",
        )
    if error.code is WorkerClientErrorCode.PROTOCOL_FAILED:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.RESULT_INVALID,
            "quality_ocr.worker",
        )
    reject_quality_ocr_review(
        QualityOcrReviewErrorCode.INTERNAL_ERROR,
        "quality_ocr.worker",
    )


def _rebuild_report_pipe_error(error: PdfInspectionError) -> Never:
    if type(error) is not PdfInspectionError:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.INTERNAL_ERROR,
            "quality_ocr.report_pipe",
        )
    if error.code in {
        PdfInspectionErrorCode.HANDLE_INVALID,
        PdfInspectionErrorCode.REPORT_INVALID,
    }:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.RESULT_INVALID,
            "quality_ocr.report_pipe",
        )
    reject_quality_ocr_review(
        QualityOcrReviewErrorCode.INTERNAL_ERROR,
        "quality_ocr.report_pipe",
    )


def _require_report_binding(
    report: QualityOcrReviewReport,
    request: QualityOcrReviewRequest,
    operation_id: UUID,
) -> None:
    if (
        type(report) is not QualityOcrReviewReport
        or report.operation_id != operation_id
        or report.case_id != request.case_id
        or report.manifest_revision != request.manifest_revision
        or report.page_id != request.page_id
        or not hmac.compare_digest(report.sample_sha256, request.sample_sha256)
        or not hmac.compare_digest(
            report.presentation_sha256,
            request.presentation_sha256,
        )
        or report.language_set_id != request.language_set_id
        or report.profile_id != request.profile_id
    ):
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.RESULT_INVALID,
            "quality_ocr.report_binding",
        )


def _require_active(deadline: DeadlineProbe, cancellation: CancellationProbe) -> None:
    if _deadline_expired(deadline):
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.TIMED_OUT,
            "quality_ocr.deadline",
        )
    if _cancellation_requested(cancellation):
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.CANCELLED,
            "quality_ocr.cancellation",
        )


def _deadline_expired(probe: DeadlineProbe) -> bool:
    try:
        value = probe()
    except BaseException:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.INTERNAL_ERROR,
            "quality_ocr.deadline_probe",
        )
    if type(value) is not bool:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.INTERNAL_ERROR,
            "quality_ocr.deadline_probe",
        )
    return value


def _cancellation_requested(probe: CancellationProbe) -> bool:
    try:
        value = probe()
    except BaseException:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.INTERNAL_ERROR,
            "quality_ocr.cancellation_probe",
        )
    if type(value) is not bool:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.INTERNAL_ERROR,
            "quality_ocr.cancellation_probe",
        )
    return value

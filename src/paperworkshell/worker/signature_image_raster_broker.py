"""Owner-thread broker for restricted straight-alpha signature-image decoding."""

from __future__ import annotations

import hashlib
import hmac
import math
from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Never, Protocol, Self, cast
from uuid import UUID, uuid4

from paperworkshell.domain.image_pages import (
    MAX_IMAGE_DIMENSION_PIXELS,
    MAX_IMAGE_MEMORY_BYTES,
    MAX_IMAGE_SOURCE_PIXELS,
    MAX_IMAGE_WALL_TIME_MS,
    ImagePageError,
    ImagePageErrorCode,
)
from paperworkshell.domain.signature_images import (
    SIGNATURE_IMAGE_RASTER_ARTIFACT_HEADER_BYTES,
    SignatureImageError,
    SignatureImageErrorCode,
    SignatureImageMediaType,
    SignatureImageProgress,
    SignatureImageProgressPhase,
    SignatureImageRasterArtifact,
    SignatureImageRasterRequest,
    decode_signature_image_raster_artifact,
    reject_signature_image,
)
from paperworkshell.overlays.signature_rasters import (
    SignatureImageOverlayRasterSource,
    SignatureImagePreview,
)
from paperworkshell.worker._windows_broker import (
    WindowsBoundedOutputBuffer,
    retire_runtime_on_failure,
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
    SIGNATURE_IMAGE_RASTER_CAPABILITY,
    SIGNATURE_IMAGE_RASTER_MAXIMUM_HEIGHT_PARAMETER,
    SIGNATURE_IMAGE_RASTER_MAXIMUM_WIDTH_PARAMETER,
    SIGNATURE_IMAGE_RASTER_OUTPUT_CONTENT_KIND,
    SIGNATURE_IMAGE_RASTER_OUTPUT_HANDLE_PARAMETER,
    SIGNATURE_IMAGE_RASTER_OUTPUT_OBJECT_ID_PARAMETER,
    SIGNATURE_IMAGE_RASTER_SOURCE_HANDLE_PARAMETER,
    CancelReason,
    CaseObjectReference,
    ErrorCategory,
    ErrorCode,
    OperationOutcome,
    OperationStartPayload,
    ReferenceArea,
    ResourceLimits,
    ScalarValueType,
    TaggedScalar,
)

_MAXIMUM_IDENTIFIER_ATTEMPTS = 32


class DeadlineProbe(Protocol):
    def __call__(self) -> bool: ...


class CancellationProbe(Protocol):
    def __call__(self) -> bool: ...


class SignatureImageProgressSink(Protocol):
    def __call__(self, progress: SignatureImageProgress, /) -> None: ...


class SignatureImageAssetLease(Protocol):
    @property
    def case_id(self) -> UUID: ...

    @property
    def asset_id(self) -> UUID: ...

    @property
    def media_type(self) -> SignatureImageMediaType: ...

    @property
    def byte_size(self) -> int: ...

    @property
    def sha256(self) -> str: ...

    @property
    def handle_value(self) -> int: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class CommittedSignatureImageAssetStore(Protocol):
    def borrow_committed_signature_image(
        self,
        case_id: UUID,
        asset_id: UUID,
        *,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
    ) -> SignatureImageAssetLease: ...


class BoundedSignatureImageOutput(Protocol):
    @property
    def capacity(self) -> int: ...

    @property
    def write_handle_value(self) -> int: ...

    def read_exact(self, byte_count: int) -> bytes: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class SignatureImageWorkerCoordinator(Protocol):
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
class SignatureImageRasterBrokerPolicy:
    """Trusted source, raster, worker, and responsiveness budgets."""

    limits: ResourceLimits
    preview_edge_pixels: int = 512
    pump_interval_seconds: float = 0.050

    def __post_init__(self) -> None:
        if (
            type(self.limits) is not ResourceLimits
            or type(self.preview_edge_pixels) is not int
            or not 1 <= self.preview_edge_pixels <= MAX_IMAGE_DIMENSION_PIXELS
            or type(self.pump_interval_seconds) is not float
            or not math.isfinite(self.pump_interval_seconds)
            or not 0 < self.pump_interval_seconds <= 1.0
            or self.limits.wall_time_ms > MAX_IMAGE_WALL_TIME_MS
            or self.limits.page_count != 1
            or self.limits.pixel_count > MAX_IMAGE_SOURCE_PIXELS
            or self.limits.memory_bytes > MAX_IMAGE_MEMORY_BYTES
            or self.preview_edge_pixels > self.limits.pixel_count // self.preview_edge_pixels
        ):
            reject_signature_image(
                SignatureImageErrorCode.MODEL_INVALID,
                "signature.broker_policy",
            )


class BrokeredSignatureImageRasterizer:
    """Decode only authenticated asset handles in a one-shot network-denied worker."""

    __slots__ = (
        "_assets",
        "_heavy_operations",
        "_id_factory",
        "_output_buffer_factory",
        "_policy",
        "_worker",
    )

    def __init__(
        self,
        worker: SignatureImageWorkerCoordinator,
        assets: CommittedSignatureImageAssetStore,
        policy: SignatureImageRasterBrokerPolicy,
        *,
        heavy_operations: HeavyOperationCoordinator,
        id_factory: Callable[[], UUID] = uuid4,
        output_buffer_factory: Callable[[int], BoundedSignatureImageOutput] = (
            WindowsBoundedOutputBuffer.create
        ),
    ) -> None:
        if (
            type(policy) is not SignatureImageRasterBrokerPolicy
            or type(heavy_operations) is not HeavyOperationCoordinator
            or not callable(getattr(assets, "borrow_committed_signature_image", None))
            or not callable(id_factory)
            or not callable(output_buffer_factory)
        ):
            reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, "signature.broker")
        self._worker = worker
        self._assets = assets
        self._policy = policy
        self._heavy_operations = heavy_operations
        self._id_factory = id_factory
        self._output_buffer_factory = output_buffer_factory

    @retire_runtime_on_failure
    def preview_prepared(
        self,
        request: SignatureImageRasterRequest,
        lease: SignatureImageAssetLease,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: SignatureImageProgressSink | None = None,
    ) -> SignatureImagePreview:
        """Decode one retained pending asset without committing or exposing its path."""

        if type(request) is not SignatureImageRasterRequest:
            reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, "signature.request")
        preview_request = SignatureImageRasterRequest(
            request.case_id,
            request.asset_id,
            request.media_type,
            request.source_byte_count,
            request.source_sha256,
            min(request.maximum_width_pixels, self._policy.preview_edge_pixels),
            min(request.maximum_height_pixels, self._policy.preview_edge_pixels),
        )
        artifact = self._rasterize_lease(
            preview_request,
            lease,
            deadline=deadline,
            cancellation=cancellation,
            progress=progress,
        )
        return SignatureImagePreview.from_artifact(preview_request, artifact)

    @retire_runtime_on_failure
    def rasterize_committed(
        self,
        request: SignatureImageRasterRequest,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: SignatureImageProgressSink | None = None,
    ) -> SignatureImageOverlayRasterSource:
        """Re-authenticate a committed asset and return exact renderer-ready pixels."""

        if type(request) is not SignatureImageRasterRequest:
            reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, "signature.request")
        try:
            with self._assets.borrow_committed_signature_image(
                request.case_id,
                request.asset_id,
                cancellation=cancellation,
                deadline=deadline,
            ) as lease:
                artifact = self._rasterize_lease(
                    request,
                    lease,
                    deadline=deadline,
                    cancellation=cancellation,
                    progress=progress,
                )
            return SignatureImageOverlayRasterSource.from_artifact(request, artifact)
        except SignatureImageError:
            raise
        except Exception:
            reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.rasterize")

    def _rasterize_lease(
        self,
        request: SignatureImageRasterRequest,
        lease: SignatureImageAssetLease,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: SignatureImageProgressSink | None,
    ) -> SignatureImageRasterArtifact:
        if type(request) is not SignatureImageRasterRequest or not _lease_matches(request, lease):
            reject_signature_image(SignatureImageErrorCode.ASSET_CHANGED, "signature.lease")
        try:
            with self._acquire_heavy_operation(deadline, cancellation):
                return self._run(
                    request,
                    lease.handle_value,
                    deadline=deadline,
                    cancellation=cancellation,
                    progress=progress,
                )
        except SignatureImageError:
            raise
        except ImagePageError as error:
            _map_image_error(error)
        except WorkerClientError as error:
            if error.code in {
                WorkerClientErrorCode.HANDSHAKE_TIMED_OUT,
                WorkerClientErrorCode.OPERATION_TIMED_OUT,
                WorkerClientErrorCode.CANCELLATION_TIMED_OUT,
            }:
                reject_signature_image(SignatureImageErrorCode.TIMED_OUT, "signature.worker")
            reject_signature_image(SignatureImageErrorCode.DECODE_FAILED, "signature.worker")
        except Exception:
            reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.broker")

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

    def _run(
        self,
        request: SignatureImageRasterRequest,
        source_handle_value: int,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: SignatureImageProgressSink | None,
    ) -> SignatureImageRasterArtifact:
        _require_active(deadline, cancellation)
        limits = _operation_limits(self._policy.limits, request)
        output_id = self._new_id({request.case_id, request.asset_id})
        operation_id = self._new_id({request.case_id, request.asset_id, output_id})
        _report(progress, operation_id, SignatureImageProgressPhase.DECODING, 0, 1)
        result: WorkerOperationResultSnapshot | None = None
        start_attempted = False
        with self._output_buffer_factory(limits.output_bytes) as output_buffer:
            if output_buffer.capacity != limits.output_bytes:
                reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.output")
            try:
                start_attempted = True
                self._worker.start(request.case_id)
                ready = self._wait_ready(deadline, cancellation)
                if SIGNATURE_IMAGE_RASTER_CAPABILITY not in ready.capabilities:
                    reject_signature_image(
                        SignatureImageErrorCode.DECODE_FAILED,
                        "signature.capability",
                    )
                source_handle = self._worker.duplicate_handle(
                    source_handle_value,
                    BrokeredHandlePurpose.READ_SOURCE,
                )
                output_handle = self._worker.duplicate_handle(
                    output_buffer.write_handle_value,
                    BrokeredHandlePurpose.WRITE_BOUNDED_OUTPUT_SECTION,
                )
                payload = _operation_payload(
                    request,
                    source_handle=source_handle,
                    output_handle=output_handle,
                    output_id=output_id,
                    limits=limits,
                )
                selected = self._worker.start_operation(payload, operation_id=operation_id)
                if selected != operation_id:
                    reject_signature_image(
                        SignatureImageErrorCode.RESULT_INVALID,
                        "signature.operation_id",
                    )
                result = self._wait_result(
                    operation_id,
                    deadline=deadline,
                    cancellation=cancellation,
                )
            finally:
                if start_attempted:
                    self._worker.terminate()
            output = _require_successful_output(result, operation_id, output_id)
            byte_count = output.expected_byte_size
            if type(byte_count) is not int or not 1 <= byte_count <= limits.output_bytes:
                reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.output")
            encoded = output_buffer.read_exact(byte_count)
        _require_active(deadline, cancellation)
        if output.expected_sha256 is None or not hmac.compare_digest(
            hashlib.sha256(encoded).hexdigest(), output.expected_sha256
        ):
            reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.output")
        artifact = decode_signature_image_raster_artifact(
            encoded,
            expected_operation_id=operation_id,
            expected_output_id=output_id,
        )
        _require_active(deadline, cancellation)
        artifact.header.result.validate_for(request)
        _report(progress, operation_id, SignatureImageProgressPhase.PREVIEW_READY, 1, 1)
        return artifact

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
                reject_signature_image(SignatureImageErrorCode.DECODE_FAILED, "signature.handshake")
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
            snapshot = self._worker.query()
            result = snapshot.last_result
            if result is not None:
                if result.operation_id != operation_id:
                    reject_signature_image(
                        SignatureImageErrorCode.RESULT_INVALID,
                        "signature.result_id",
                    )
                if cancellation_sent and result.outcome is OperationOutcome.SUCCEEDED:
                    reject_signature_image(SignatureImageErrorCode.CANCELLED, "signature.cancelled")
                return result
            if snapshot.phase in {
                WorkerLifecyclePhase.READY,
                WorkerLifecyclePhase.STOPPED,
                WorkerLifecyclePhase.FAULTED,
            }:
                reject_signature_image(
                    SignatureImageErrorCode.DECODE_FAILED, "signature.worker_exit"
                )
            if _deadline_expired(deadline):
                if not cancellation_sent:
                    self._worker.cancel(CancelReason.SHUTDOWN)
                reject_signature_image(SignatureImageErrorCode.TIMED_OUT, "signature.deadline")
            if _cancellation_requested(cancellation) and not cancellation_sent:
                self._worker.cancel(CancelReason.USER_REQUESTED)
                cancellation_sent = True
            self._worker.pump(self._policy.pump_interval_seconds)

    def _new_id(self, forbidden: set[UUID]) -> UUID:
        for _ in range(_MAXIMUM_IDENTIFIER_ATTEMPTS):
            try:
                candidate = self._id_factory()
            except BaseException:
                continue
            if type(candidate) is UUID and candidate.version == 4 and candidate not in forbidden:
                return candidate
        reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.identifier")


def _lease_matches(request: SignatureImageRasterRequest, lease: object) -> bool:
    candidate = cast(SignatureImageAssetLease, lease)
    try:
        return (
            candidate.case_id == request.case_id
            and candidate.asset_id == request.asset_id
            and candidate.media_type is request.media_type
            and candidate.byte_size == request.source_byte_count
            and hmac.compare_digest(candidate.sha256, request.source_sha256)
            and type(candidate.handle_value) is int
            and candidate.handle_value > 0
        )
    except BaseException:
        return False


def _operation_limits(
    policy: ResourceLimits,
    request: SignatureImageRasterRequest,
) -> ResourceLimits:
    pixels = request.maximum_width_pixels * request.maximum_height_pixels
    output_bytes = pixels * 4
    artifact_bytes = SIGNATURE_IMAGE_RASTER_ARTIFACT_HEADER_BYTES + output_bytes
    if (
        pixels > policy.pixel_count
        or artifact_bytes > policy.output_bytes
        or output_bytes > policy.temporary_storage_bytes
        or output_bytes > policy.memory_bytes
    ):
        reject_signature_image(SignatureImageErrorCode.LIMIT_EXCEEDED, "signature.limits")
    return ResourceLimits(
        wall_time_ms=policy.wall_time_ms,
        cpu_time_ms=policy.cpu_time_ms,
        memory_bytes=policy.memory_bytes,
        temporary_storage_bytes=output_bytes,
        output_bytes=artifact_bytes,
        page_count=1,
        pixel_count=policy.pixel_count,
    )


def _operation_payload(
    request: SignatureImageRasterRequest,
    *,
    source_handle: int,
    output_handle: int,
    output_id: UUID,
    limits: ResourceLimits,
) -> OperationStartPayload:
    content_kind = "jpeg" if request.media_type is SignatureImageMediaType.JPEG else "png"
    return OperationStartPayload(
        operation_kind=SIGNATURE_IMAGE_RASTER_CAPABILITY.capability_id,
        operation_version=SIGNATURE_IMAGE_RASTER_CAPABILITY.capability_version,
        inputs=(
            CaseObjectReference(
                area=ReferenceArea.ASSET_READ,
                object_id=request.asset_id,
                operation_id=None,
                content_kind=content_kind,
                expected_byte_size=request.source_byte_count,
                expected_sha256=request.source_sha256,
            ),
        ),
        parameters=(
            TaggedScalar(
                SIGNATURE_IMAGE_RASTER_SOURCE_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                source_handle,
            ),
            TaggedScalar(
                SIGNATURE_IMAGE_RASTER_OUTPUT_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                output_handle,
            ),
            TaggedScalar(
                SIGNATURE_IMAGE_RASTER_OUTPUT_OBJECT_ID_PARAMETER,
                ScalarValueType.UUID,
                output_id,
            ),
            TaggedScalar(
                SIGNATURE_IMAGE_RASTER_MAXIMUM_WIDTH_PARAMETER,
                ScalarValueType.INTEGER,
                request.maximum_width_pixels,
            ),
            TaggedScalar(
                SIGNATURE_IMAGE_RASTER_MAXIMUM_HEIGHT_PARAMETER,
                ScalarValueType.INTEGER,
                request.maximum_height_pixels,
            ),
        ),
        requested_limits=limits,
    )


def _require_successful_output(
    result: WorkerOperationResultSnapshot | None,
    operation_id: UUID,
    output_id: UUID,
) -> CaseObjectReference:
    if result is None or type(result) is not WorkerOperationResultSnapshot:
        reject_signature_image(SignatureImageErrorCode.DECODE_FAILED, "signature.no_result")
    if result.operation_id != operation_id:
        reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.result_id")
    if result.outcome is OperationOutcome.CANCELLED:
        reject_signature_image(SignatureImageErrorCode.CANCELLED, "signature.result")
    if result.outcome is OperationOutcome.TIMED_OUT:
        reject_signature_image(SignatureImageErrorCode.TIMED_OUT, "signature.result")
    if (
        result.outcome is OperationOutcome.FAILED
        and result.error_code is ErrorCode.WORKER_OPERATION_FAILED
        and result.error_category is ErrorCategory.UNSUPPORTED
    ):
        reject_signature_image(
            SignatureImageErrorCode.COLOR_PROFILE_UNSUPPORTED,
            "signature.color_profile",
        )
    if (
        result.outcome is OperationOutcome.FAILED
        and result.error_code is ErrorCode.WORKER_OPERATION_FAILED
        and result.error_category is ErrorCategory.RESOURCE_LIMIT
    ):
        reject_signature_image(SignatureImageErrorCode.LIMIT_EXCEEDED, "signature.limits")
    if result.outcome is not OperationOutcome.SUCCEEDED or len(result.outputs) != 1:
        reject_signature_image(SignatureImageErrorCode.DECODE_FAILED, "signature.result")
    output = result.outputs[0]
    if (
        output.area is not ReferenceArea.OPERATION_STAGING
        or output.object_id != output_id
        or output.operation_id != operation_id
        or output.content_kind != SIGNATURE_IMAGE_RASTER_OUTPUT_CONTENT_KIND
        or output.expected_byte_size is None
        or output.expected_sha256 is None
    ):
        reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.output")
    return output


def _report(
    sink: SignatureImageProgressSink | None,
    operation_id: UUID,
    phase: SignatureImageProgressPhase,
    completed: int,
    total: int,
) -> None:
    if sink is None:
        return
    try:
        sink(SignatureImageProgress(operation_id, phase, completed, total))
    except BaseException:
        reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.progress")


def _require_active(deadline: DeadlineProbe, cancellation: CancellationProbe) -> None:
    if _cancellation_requested(cancellation):
        reject_signature_image(SignatureImageErrorCode.CANCELLED, "signature.cancelled")
    if _deadline_expired(deadline):
        reject_signature_image(SignatureImageErrorCode.TIMED_OUT, "signature.deadline")


def _cancellation_requested(probe: CancellationProbe) -> bool:
    try:
        value = probe()
    except BaseException:
        reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.cancellation")
    if type(value) is not bool:
        reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.cancellation")
    return value


def _deadline_expired(probe: DeadlineProbe) -> bool:
    try:
        value = probe()
    except BaseException:
        reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.deadline")
    if type(value) is not bool:
        reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.deadline")
    return value


def _map_image_error(error: ImagePageError) -> Never:
    mapping = {
        ImagePageErrorCode.CANCELLED: SignatureImageErrorCode.CANCELLED,
        ImagePageErrorCode.TIMED_OUT: SignatureImageErrorCode.TIMED_OUT,
        ImagePageErrorCode.LIMIT_EXCEEDED: SignatureImageErrorCode.LIMIT_EXCEEDED,
        ImagePageErrorCode.MALFORMED: SignatureImageErrorCode.MALFORMED,
        ImagePageErrorCode.UNSUPPORTED: (
            SignatureImageErrorCode.COLOR_PROFILE_UNSUPPORTED
            if error.context
            in {
                "image.signature_icc_profile",
                "image.signature_color_profile",
            }
            else SignatureImageErrorCode.MALFORMED
        ),
        ImagePageErrorCode.FORMAT_MISMATCH: SignatureImageErrorCode.SOURCE_CHANGED,
        ImagePageErrorCode.RESULT_INVALID: SignatureImageErrorCode.RESULT_INVALID,
    }
    reject_signature_image(
        mapping.get(error.code, SignatureImageErrorCode.DECODE_FAILED), "signature.decode"
    )

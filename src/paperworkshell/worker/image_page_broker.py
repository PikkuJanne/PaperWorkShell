"""Owner-thread broker for one-shot JPEG/PNG normalization and preview."""

from __future__ import annotations

import hashlib
import hmac
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from types import TracebackType
from typing import Never, Protocol, Self
from uuid import UUID, uuid4

from paperworkshell.domain.image_pages import (
    IMAGE_PAGE_ARTIFACT_HEADER_BYTES,
    MAX_IMAGE_DECOMPRESSED_BYTES,
    MAX_IMAGE_DIMENSION_PIXELS,
    MAX_IMAGE_MEMORY_BYTES,
    MAX_IMAGE_METADATA_BYTES,
    MAX_IMAGE_METADATA_RECORDS,
    MAX_IMAGE_OUTPUT_BYTES,
    MAX_IMAGE_SOURCE_BYTES,
    MAX_IMAGE_SOURCE_PIXELS,
    MAX_IMAGE_WALL_TIME_MS,
    ImageNormalizationLimits,
    ImageNormalizationRequest,
    ImagePageArtifact,
    ImagePageError,
    ImagePageErrorCode,
    ImagePagePreviewProgressPhase,
    ImagePagePreviewRequest,
    ImagePagePreviewResult,
    ImageQuarterTurn,
    ImageSourceFormat,
    decode_image_page_artifact,
    image_page_preview_result_from_artifact,
    reject_image_page,
    validate_image_normalization_result,
)
from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.pdf_thumbnails import PdfThumbnailError
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
    IMAGE_NORMALIZE_ADDITIONAL_ROTATION_PARAMETER,
    IMAGE_NORMALIZE_CAPABILITY,
    IMAGE_NORMALIZE_MAXIMUM_HEIGHT_PARAMETER,
    IMAGE_NORMALIZE_MAXIMUM_WIDTH_PARAMETER,
    IMAGE_NORMALIZE_OUTPUT_CONTENT_KIND,
    IMAGE_NORMALIZE_OUTPUT_HANDLE_PARAMETER,
    IMAGE_NORMALIZE_OUTPUT_OBJECT_ID_PARAMETER,
    IMAGE_NORMALIZE_SOURCE_HANDLE_PARAMETER,
    CancelReason,
    CaseObjectReference,
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


class ImagePreviewPhaseSink(Protocol):
    def __call__(self, phase: ImagePagePreviewProgressPhase, /) -> None: ...


class SourceProgressSink(Protocol):
    def __call__(self, progress: SourceImportProgress, /) -> None: ...


class HandleValue(Protocol):
    @property
    def value(self) -> int: ...


class PreparedImageSourceLease(Protocol):
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


class PreparedImageSourceStore(Protocol):
    def borrow_prepared_source(
        self,
        summary: PreparedSourceSummary,
    ) -> PreparedImageSourceLease: ...


class CommittedImagePageLease(Protocol):
    @property
    def handle(self) -> HandleValue: ...

    @property
    def case_id(self) -> UUID: ...

    @property
    def source_id(self) -> UUID: ...

    @property
    def source_media_type(self) -> DetectedMediaType: ...

    @property
    def page_id(self) -> UUID: ...

    @property
    def source_page_index(self) -> int: ...

    @property
    def source_page_count(self) -> int: ...

    @property
    def source_byte_size(self) -> int: ...

    @property
    def source_sha256(self) -> str: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class CommittedImagePageStore(Protocol):
    def borrow_committed_image_page(
        self,
        case_id: UUID,
        page_id: UUID,
        *,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
    ) -> CommittedImagePageLease: ...


class BoundedImageOutput(Protocol):
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


class ImageWorkerCoordinator(Protocol):
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
class ImageNormalizeBrokerPolicy:
    """Trusted image-source, raster, worker, and responsiveness budgets."""

    limits: ResourceLimits
    pump_interval_seconds: float = 0.050

    def __post_init__(self) -> None:
        limits = self.limits
        interval = self.pump_interval_seconds
        if (
            type(limits) is not ResourceLimits
            or type(interval) is not float
            or not math.isfinite(interval)
            or not 0 < interval <= 1.0
            or limits.wall_time_ms > MAX_IMAGE_WALL_TIME_MS
            or limits.page_count != 1
            or limits.pixel_count > MAX_IMAGE_SOURCE_PIXELS
            or not IMAGE_PAGE_ARTIFACT_HEADER_BYTES + 4
            <= limits.output_bytes
            <= IMAGE_PAGE_ARTIFACT_HEADER_BYTES + MAX_IMAGE_OUTPUT_BYTES
            or limits.memory_bytes > MAX_IMAGE_MEMORY_BYTES
            or limits.temporary_storage_bytes > MAX_IMAGE_OUTPUT_BYTES
        ):
            reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.broker_policy")


@dataclass(frozen=True, slots=True, repr=False)
class _ResolvedSource:
    case_id: UUID
    source_id: UUID
    media_type: DetectedMediaType
    byte_size: int
    sha256: str = field(repr=False)
    handle_value: int = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.case_id) is not UUID
            or self.case_id.version != 4
            or type(self.source_id) is not UUID
            or self.source_id.version != 4
            or self.case_id == self.source_id
            or self.media_type not in {DetectedMediaType.JPEG, DetectedMediaType.PNG}
            or type(self.byte_size) is not int
            or not 1 <= self.byte_size <= MAX_IMAGE_SOURCE_BYTES
            or type(self.sha256) is not str
            or len(self.sha256) != 64
            or type(self.handle_value) is not int
            or self.handle_value <= 0
        ):
            reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "image.source_binding")


class _ImageNormalizeBrokerCore:
    __slots__ = (
        "_heavy_operations",
        "_id_factory",
        "_output_buffer_factory",
        "_policy",
        "_worker",
    )

    def __init__(
        self,
        worker: ImageWorkerCoordinator,
        policy: ImageNormalizeBrokerPolicy,
        *,
        heavy_operations: HeavyOperationCoordinator,
        id_factory: Callable[[], UUID] = uuid4,
        output_buffer_factory: Callable[[int], BoundedImageOutput] = (
            WindowsBoundedOutputBuffer.create
        ),
    ) -> None:
        if (
            type(policy) is not ImageNormalizeBrokerPolicy
            or type(heavy_operations) is not HeavyOperationCoordinator
            or not callable(id_factory)
            or not callable(output_buffer_factory)
        ):
            reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.broker_config")
        self._worker = worker
        self._policy = policy
        self._heavy_operations = heavy_operations
        self._id_factory = id_factory
        self._output_buffer_factory = output_buffer_factory

    @retire_runtime_on_failure
    def normalize(
        self,
        source: _ResolvedSource,
        *,
        maximum_width_pixels: int,
        maximum_height_pixels: int,
        additional_rotation: ImageQuarterTurn,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ImagePreviewPhaseSink | None,
        forbidden_ids: set[UUID],
    ) -> ImagePageArtifact:
        try:
            with self._acquire_heavy_operation(deadline, cancellation):
                return self._normalize(
                    source,
                    maximum_width_pixels=maximum_width_pixels,
                    maximum_height_pixels=maximum_height_pixels,
                    additional_rotation=additional_rotation,
                    deadline=deadline,
                    cancellation=cancellation,
                    progress=progress,
                    forbidden_ids=forbidden_ids,
                )
        except ImagePageError:
            raise
        except PdfThumbnailError as error:
            del error
            reject_image_page(ImagePageErrorCode.DESTINATION_FAILED, "image.output_buffer")
        except WorkerClientError as error:
            if error.code in {
                WorkerClientErrorCode.HANDSHAKE_TIMED_OUT,
                WorkerClientErrorCode.OPERATION_TIMED_OUT,
                WorkerClientErrorCode.CANCELLATION_TIMED_OUT,
            }:
                reject_image_page(ImagePageErrorCode.TIMED_OUT, "image.worker")
            reject_image_page(ImagePageErrorCode.DEPENDENCY_FAILED, "image.worker")
        except Exception:
            reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.broker")

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

    def _normalize(
        self,
        source: _ResolvedSource,
        *,
        maximum_width_pixels: int,
        maximum_height_pixels: int,
        additional_rotation: ImageQuarterTurn,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ImagePreviewPhaseSink | None,
        forbidden_ids: set[UUID],
    ) -> ImagePageArtifact:
        _require_active(deadline, cancellation)
        if progress is not None:
            _report_preview_phase(progress, ImagePagePreviewProgressPhase.LOOKUP)
        operation_limits = _operation_limits(
            self._policy.limits,
            maximum_width_pixels,
            maximum_height_pixels,
        )
        output_id = self._new_id({*forbidden_ids, source.case_id, source.source_id})
        operation_id = self._new_id({*forbidden_ids, source.case_id, source.source_id, output_id})
        output, encoded = self._run_worker(
            source,
            limits=operation_limits,
            maximum_width_pixels=maximum_width_pixels,
            maximum_height_pixels=maximum_height_pixels,
            additional_rotation=additional_rotation,
            output_id=output_id,
            operation_id=operation_id,
            deadline=deadline,
            cancellation=cancellation,
            progress=progress,
        )
        if progress is not None:
            _report_preview_phase(progress, ImagePagePreviewProgressPhase.VALIDATING)
        _require_active(deadline, cancellation)
        _authenticate_output_bytes(encoded, output)
        try:
            artifact = decode_image_page_artifact(
                encoded,
                expected_operation_id=operation_id,
                expected_output_id=output_id,
            )
            validation_request = ImageNormalizationRequest(
                expected_source_format=_source_format(source.media_type),
                expected_source_bytes=source.byte_size,
                expected_source_sha256=source.sha256,
                maximum_width_pixels=maximum_width_pixels,
                maximum_height_pixels=maximum_height_pixels,
                additional_rotation=additional_rotation,
            )
            validate_image_normalization_result(
                validation_request,
                _normalization_limits(
                    operation_limits,
                    maximum_width_pixels,
                    maximum_height_pixels,
                    source.byte_size,
                ),
                artifact.header.result,
            )
        except ImagePageError:
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.worker_artifact")
        _require_active(deadline, cancellation)
        return artifact

    def _run_worker(
        self,
        source: _ResolvedSource,
        *,
        limits: ResourceLimits,
        maximum_width_pixels: int,
        maximum_height_pixels: int,
        additional_rotation: ImageQuarterTurn,
        output_id: UUID,
        operation_id: UUID,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ImagePreviewPhaseSink | None,
    ) -> tuple[CaseObjectReference, bytes]:
        result: WorkerOperationResultSnapshot | None = None
        start_attempted = False
        with self._output_buffer_factory(limits.output_bytes) as output_buffer:
            if (
                type(output_buffer.capacity) is not int
                or output_buffer.capacity != limits.output_bytes
                or type(output_buffer.write_handle_value) is not int
                or output_buffer.write_handle_value <= 0
            ):
                reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.output_buffer")
            try:
                _require_active(deadline, cancellation)
                start_attempted = True
                self._worker.start(source.case_id)
                ready = self._wait_ready(deadline, cancellation)
                if IMAGE_NORMALIZE_CAPABILITY not in ready.capabilities:
                    reject_image_page(
                        ImagePageErrorCode.DEPENDENCY_UNAVAILABLE,
                        "image.capability",
                    )
                source_handle = self._worker.duplicate_handle(
                    source.handle_value,
                    BrokeredHandlePurpose.READ_SOURCE,
                )
                output_handle = self._worker.duplicate_handle(
                    output_buffer.write_handle_value,
                    BrokeredHandlePurpose.WRITE_BOUNDED_OUTPUT_SECTION,
                )
                payload = _operation_payload(
                    source,
                    source_handle=source_handle,
                    output_handle=output_handle,
                    output_id=output_id,
                    maximum_width_pixels=maximum_width_pixels,
                    maximum_height_pixels=maximum_height_pixels,
                    additional_rotation=additional_rotation,
                    limits=limits,
                )
                if progress is not None:
                    _report_preview_phase(progress, ImagePagePreviewProgressPhase.GENERATING)
                selected = self._worker.start_operation(payload, operation_id=operation_id)
                if selected != operation_id:
                    reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.operation_id")
                result = self._wait_result(
                    operation_id,
                    deadline=deadline,
                    cancellation=cancellation,
                )
            finally:
                if start_attempted:
                    self._worker.terminate()
            if result is None:
                reject_image_page(ImagePageErrorCode.DEPENDENCY_FAILED, "image.no_result")
            output = _require_successful_output(
                result,
                operation_id=operation_id,
                output_id=output_id,
            )
            byte_count = output.expected_byte_size
            if type(byte_count) is not int or not 1 <= byte_count <= limits.output_bytes:
                reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.output_size")
            return output, output_buffer.read_exact(byte_count)

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
                reject_image_page(ImagePageErrorCode.DEPENDENCY_FAILED, "image.handshake")
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
                    reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.result_id")
                if cancellation_sent and result.outcome is OperationOutcome.SUCCEEDED:
                    reject_image_page(ImagePageErrorCode.CANCELLED, "image.cancelled")
                return result
            if snapshot.phase in {
                WorkerLifecyclePhase.READY,
                WorkerLifecyclePhase.STOPPED,
                WorkerLifecyclePhase.FAULTED,
            }:
                reject_image_page(ImagePageErrorCode.DEPENDENCY_FAILED, "image.worker_exit")
            if _deadline_expired(deadline):
                if not cancellation_sent:
                    self._worker.cancel(CancelReason.SHUTDOWN)
                reject_image_page(ImagePageErrorCode.TIMED_OUT, "image.deadline")
            if _cancellation_requested(cancellation) and not cancellation_sent:
                self._worker.cancel(CancelReason.USER_REQUESTED)
                cancellation_sent = True
            self._worker.pump(self._policy.pump_interval_seconds)

    def _new_id(self, forbidden: set[UUID]) -> UUID:
        for _ in range(_MAXIMUM_IDENTIFIER_ATTEMPTS):
            try:
                value = self._id_factory()
            except BaseException:
                reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.identifier")
            if type(value) is UUID and value.version == 4 and value not in forbidden:
                return value
        reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.identifier")


class BrokeredImagePageGenerator:
    """Resolve a committed image page and return one authenticated preview snapshot."""

    __slots__ = ("_core", "_sources")

    def __init__(
        self,
        core: _ImageNormalizeBrokerCore,
        sources: CommittedImagePageStore,
    ) -> None:
        self._core = core
        self._sources = sources

    @property
    def _worker(self) -> ImageWorkerCoordinator:
        return self._core._worker

    @property
    def _heavy_operations(self) -> HeavyOperationCoordinator:
        return self._core._heavy_operations

    @retire_runtime_on_failure
    def get_or_generate(
        self,
        request: ImagePagePreviewRequest,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ImagePreviewPhaseSink,
    ) -> ImagePagePreviewResult:
        if type(request) is not ImagePagePreviewRequest:
            reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.preview_request")
        try:
            with self._sources.borrow_committed_image_page(
                request.case_id,
                request.page_id,
                cancellation=cancellation,
                deadline=deadline,
            ) as lease:
                source = _resolved_committed_source(request, lease)
                artifact = self._core.normalize(
                    source,
                    maximum_width_pixels=request.maximum_width_pixels,
                    maximum_height_pixels=request.maximum_height_pixels,
                    additional_rotation=request.additional_rotation,
                    deadline=deadline,
                    cancellation=cancellation,
                    progress=progress,
                    forbidden_ids={request.page_id},
                )
            return image_page_preview_result_from_artifact(request, artifact)
        except ImagePageError:
            raise
        except SourceIngestError as error:
            _raise_image_source_error(error)
        except Exception:
            reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.preview")


class BrokeredImageSourceInspector:
    """Decode a prepared JPEG/PNG in the worker before approving one-page import."""

    __slots__ = ("_core", "_sources")

    def __init__(
        self,
        core: _ImageNormalizeBrokerCore,
        sources: PreparedImageSourceStore,
    ) -> None:
        self._core = core
        self._sources = sources

    @property
    def _worker(self) -> ImageWorkerCoordinator:
        return self._core._worker

    @property
    def _heavy_operations(self) -> HeavyOperationCoordinator:
        return self._core._heavy_operations

    @retire_runtime_on_failure
    def inspect(
        self,
        source: PreparedSourceSummary,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: SourceProgressSink,
    ) -> CompletedSourceInspection:
        if type(source) is not PreparedSourceSummary or source.media_type not in {
            DetectedMediaType.JPEG,
            DetectedMediaType.PNG,
        }:
            reject_source_ingest(
                SourceIngestErrorCode.INSPECTION_INVALID, "inspection.image_source"
            )
        _report_source_progress(progress, source.pending_id, 0)
        image_failure: ImagePageErrorCode | None = None
        try:
            with self._sources.borrow_prepared_source(source) as lease:
                if lease.summary != source:
                    reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "image.prepared_binding")
                resolved = _ResolvedSource(
                    case_id=source.case_id,
                    source_id=source.source_id,
                    media_type=source.media_type,
                    byte_size=source.byte_size,
                    sha256=source.sha256,
                    handle_value=lease.handle_value,
                )
                self._core.normalize(
                    resolved,
                    maximum_width_pixels=1,
                    maximum_height_pixels=1,
                    additional_rotation=ImageQuarterTurn.DEGREES_0,
                    deadline=deadline,
                    cancellation=cancellation,
                    progress=None,
                    forbidden_ids={source.pending_id},
                )
        except SourceIngestError:
            raise
        except ImagePageError as error:
            image_failure = error.code
        if image_failure is not None:
            _raise_source_inspection_error(image_failure)
        completed = CompletedSourceInspection(
            pending_id=source.pending_id,
            media_type=source.media_type,
            page_count=1,
            signature_state=SourceSignatureState.NOT_APPLICABLE,
            encryption_state=SourceEncryptionState.NOT_APPLICABLE,
        )
        _report_source_progress(progress, source.pending_id, 1)
        return completed


def create_image_page_brokers(
    worker: ImageWorkerCoordinator,
    prepared_sources: PreparedImageSourceStore,
    committed_pages: CommittedImagePageStore,
    policy: ImageNormalizeBrokerPolicy,
    *,
    heavy_operations: HeavyOperationCoordinator,
) -> tuple[BrokeredImageSourceInspector, BrokeredImagePageGenerator]:
    """Build both public ports around one worker and one shared heavy-operation gate."""

    core = _ImageNormalizeBrokerCore(
        worker,
        policy,
        heavy_operations=heavy_operations,
    )
    return (
        BrokeredImageSourceInspector(core, prepared_sources),
        BrokeredImagePageGenerator(core, committed_pages),
    )


def _resolved_committed_source(
    request: ImagePagePreviewRequest,
    lease: CommittedImagePageLease,
) -> _ResolvedSource:
    if (
        lease.case_id != request.case_id
        or lease.page_id != request.page_id
        or lease.source_page_index != 0
        or lease.source_page_count != 1
    ):
        reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "image.committed_binding")
    return _ResolvedSource(
        case_id=lease.case_id,
        source_id=lease.source_id,
        media_type=lease.source_media_type,
        byte_size=lease.source_byte_size,
        sha256=lease.source_sha256,
        handle_value=lease.handle.value,
    )


def _operation_limits(
    policy: ResourceLimits,
    maximum_width_pixels: int,
    maximum_height_pixels: int,
) -> ResourceLimits:
    if (
        type(maximum_width_pixels) is not int
        or type(maximum_height_pixels) is not int
        or not 1 <= maximum_width_pixels <= MAX_IMAGE_DIMENSION_PIXELS
        or not 1 <= maximum_height_pixels <= MAX_IMAGE_DIMENSION_PIXELS
    ):
        reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.preview_dimensions")
    output_pixels = maximum_width_pixels * maximum_height_pixels
    output_bytes = output_pixels * 4
    artifact_bytes = IMAGE_PAGE_ARTIFACT_HEADER_BYTES + output_bytes
    if (
        output_pixels > policy.pixel_count
        or artifact_bytes > policy.output_bytes
        or output_bytes > policy.temporary_storage_bytes
        or output_bytes > policy.memory_bytes
    ):
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.operation_limits")
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
    source: _ResolvedSource,
    *,
    source_handle: int,
    output_handle: int,
    output_id: UUID,
    maximum_width_pixels: int,
    maximum_height_pixels: int,
    additional_rotation: ImageQuarterTurn,
    limits: ResourceLimits,
) -> OperationStartPayload:
    return OperationStartPayload(
        operation_kind=IMAGE_NORMALIZE_CAPABILITY.capability_id,
        operation_version=IMAGE_NORMALIZE_CAPABILITY.capability_version,
        inputs=(
            CaseObjectReference(
                area=ReferenceArea.SOURCE_READ,
                object_id=source.source_id,
                operation_id=None,
                content_kind=_source_format(source.media_type).value,
                expected_byte_size=source.byte_size,
                expected_sha256=source.sha256,
            ),
        ),
        parameters=(
            TaggedScalar(
                IMAGE_NORMALIZE_SOURCE_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                source_handle,
            ),
            TaggedScalar(
                IMAGE_NORMALIZE_OUTPUT_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                output_handle,
            ),
            TaggedScalar(
                IMAGE_NORMALIZE_OUTPUT_OBJECT_ID_PARAMETER,
                ScalarValueType.UUID,
                output_id,
            ),
            TaggedScalar(
                IMAGE_NORMALIZE_MAXIMUM_WIDTH_PARAMETER,
                ScalarValueType.INTEGER,
                maximum_width_pixels,
            ),
            TaggedScalar(
                IMAGE_NORMALIZE_MAXIMUM_HEIGHT_PARAMETER,
                ScalarValueType.INTEGER,
                maximum_height_pixels,
            ),
            TaggedScalar(
                IMAGE_NORMALIZE_ADDITIONAL_ROTATION_PARAMETER,
                ScalarValueType.INTEGER,
                int(additional_rotation),
            ),
        ),
        requested_limits=limits,
    )


def _require_successful_output(
    result: WorkerOperationResultSnapshot,
    *,
    operation_id: UUID,
    output_id: UUID,
) -> CaseObjectReference:
    if type(result) is not WorkerOperationResultSnapshot or result.operation_id != operation_id:
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.worker_result")
    if result.outcome is OperationOutcome.CANCELLED:
        reject_image_page(ImagePageErrorCode.CANCELLED, "image.worker_result")
    if result.outcome is OperationOutcome.TIMED_OUT:
        reject_image_page(ImagePageErrorCode.TIMED_OUT, "image.worker_result")
    if result.outcome is OperationOutcome.FAILED:
        reject_image_page(ImagePageErrorCode.DEPENDENCY_FAILED, "image.worker_result")
    if result.outcome is not OperationOutcome.SUCCEEDED or len(result.outputs) != 1:
        reject_image_page(ImagePageErrorCode.DEPENDENCY_FAILED, "image.worker_result")
    output = result.outputs[0]
    if (
        output.area is not ReferenceArea.OPERATION_STAGING
        or output.object_id != output_id
        or output.operation_id != operation_id
        or output.content_kind != IMAGE_NORMALIZE_OUTPUT_CONTENT_KIND
        or output.expected_byte_size is None
        or output.expected_sha256 is None
    ):
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.worker_output")
    return output


def _authenticate_output_bytes(payload: bytes, output: CaseObjectReference) -> None:
    if (
        type(payload) is not bytes
        or output.expected_byte_size != len(payload)
        or output.expected_sha256 is None
        or not hmac.compare_digest(
            output.expected_sha256,
            hashlib.sha256(payload).hexdigest(),
        )
    ):
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.output_digest")


def _normalization_limits(
    limits: ResourceLimits,
    maximum_width_pixels: int,
    maximum_height_pixels: int,
    source_bytes: int,
) -> ImageNormalizationLimits:
    pixel_bytes = limits.output_bytes - IMAGE_PAGE_ARTIFACT_HEADER_BYTES
    return ImageNormalizationLimits(
        maximum_source_bytes=source_bytes,
        maximum_encoded_width_pixels=MAX_IMAGE_DIMENSION_PIXELS,
        maximum_encoded_height_pixels=MAX_IMAGE_DIMENSION_PIXELS,
        maximum_source_pixels=min(limits.pixel_count, MAX_IMAGE_SOURCE_PIXELS),
        maximum_decompressed_bytes=min(limits.memory_bytes, MAX_IMAGE_DECOMPRESSED_BYTES),
        maximum_metadata_records=MAX_IMAGE_METADATA_RECORDS,
        maximum_metadata_bytes=min(MAX_IMAGE_METADATA_BYTES, limits.memory_bytes),
        maximum_output_width_pixels=maximum_width_pixels,
        maximum_output_height_pixels=maximum_height_pixels,
        maximum_output_pixels=min(
            limits.pixel_count,
            maximum_width_pixels * maximum_height_pixels,
            pixel_bytes // 4,
        ),
        maximum_output_bytes=min(pixel_bytes, limits.temporary_storage_bytes),
        maximum_memory_bytes=limits.memory_bytes,
        wall_time_ms=limits.wall_time_ms,
    )


def _source_format(media_type: DetectedMediaType) -> ImageSourceFormat:
    if media_type is DetectedMediaType.JPEG:
        return ImageSourceFormat.JPEG
    if media_type is DetectedMediaType.PNG:
        return ImageSourceFormat.PNG
    reject_image_page(ImagePageErrorCode.FORMAT_MISMATCH, "image.source_format")


def _report_preview_phase(
    sink: ImagePreviewPhaseSink,
    phase: ImagePagePreviewProgressPhase,
) -> None:
    try:
        sink(phase)
    except ImagePageError:
        raise
    except BaseException:
        reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.progress")


def _report_source_progress(sink: SourceProgressSink, operation_id: UUID, completed: int) -> None:
    try:
        sink(
            SourceImportProgress(
                operation_id=operation_id,
                phase=SourceProgressPhase.INSPECTING,
                completed_units=completed,
                total_units=1,
            )
        )
    except SourceIngestError:
        raise
    except BaseException:
        reject_source_ingest(SourceIngestErrorCode.PROGRESS_FAILED, "inspection.image_progress")


def _require_active(deadline: DeadlineProbe, cancellation: CancellationProbe) -> None:
    if _deadline_expired(deadline):
        reject_image_page(ImagePageErrorCode.TIMED_OUT, "image.deadline")
    if _cancellation_requested(cancellation):
        reject_image_page(ImagePageErrorCode.CANCELLED, "image.cancelled")


def _deadline_expired(probe: DeadlineProbe) -> bool:
    try:
        value = probe()
    except ImagePageError:
        raise
    except BaseException:
        reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.deadline_probe")
    if type(value) is not bool:
        reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.deadline_probe")
    return value


def _cancellation_requested(probe: CancellationProbe) -> bool:
    try:
        value = probe()
    except ImagePageError:
        raise
    except BaseException:
        reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.cancellation_probe")
    if type(value) is not bool:
        reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.cancellation_probe")
    return value


def _raise_image_source_error(error: SourceIngestError) -> Never:
    if error.code is SourceIngestErrorCode.CANCELLED:
        reject_image_page(ImagePageErrorCode.CANCELLED, "image.source")
    if error.code is SourceIngestErrorCode.TIMED_OUT:
        reject_image_page(ImagePageErrorCode.TIMED_OUT, "image.source")
    if error.code is SourceIngestErrorCode.CAPACITY_EXCEEDED:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.source")
    reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "image.source")


def _raise_source_inspection_error(code: ImagePageErrorCode) -> Never:
    if code is ImagePageErrorCode.CANCELLED:
        reject_source_ingest(SourceIngestErrorCode.CANCELLED, "inspection.image_cancelled")
    if code is ImagePageErrorCode.TIMED_OUT:
        reject_source_ingest(SourceIngestErrorCode.TIMED_OUT, "inspection.image_timed_out")
    if code is ImagePageErrorCode.LIMIT_EXCEEDED:
        reject_source_ingest(SourceIngestErrorCode.CAPACITY_EXCEEDED, "inspection.image_limit")
    if code is ImagePageErrorCode.SOURCE_INVALID:
        reject_source_ingest(
            SourceIngestErrorCode.INSPECTION_MISMATCH,
            "inspection.image_source",
        )
    if code in {
        ImagePageErrorCode.FORMAT_MISMATCH,
        ImagePageErrorCode.MALFORMED,
        ImagePageErrorCode.METADATA_INVALID,
        ImagePageErrorCode.GEOMETRY_INVALID,
        ImagePageErrorCode.UNSUPPORTED,
    }:
        reject_source_ingest(SourceIngestErrorCode.INSPECTION_INVALID, "inspection.image_invalid")
    if code is ImagePageErrorCode.DEPENDENCY_UNAVAILABLE:
        reject_source_ingest(
            SourceIngestErrorCode.INSPECT_FAILED,
            "inspection.image_dependency_unavailable",
        )
    if code is ImagePageErrorCode.DEPENDENCY_FAILED:
        reject_source_ingest(
            SourceIngestErrorCode.INSPECT_FAILED,
            "inspection.image_dependency_failed",
        )
    if code in {
        ImagePageErrorCode.DESTINATION_FAILED,
        ImagePageErrorCode.CACHE_INVALID,
    }:
        reject_source_ingest(SourceIngestErrorCode.INSPECT_FAILED, "inspection.image_destination")
    reject_source_ingest(SourceIngestErrorCode.INSPECT_FAILED, "inspection.image_internal")

"""Owner-thread broker for bounded, authenticated scan-image transforms."""

from __future__ import annotations

import hashlib
import hmac
import math
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Never, Protocol
from uuid import UUID, uuid4

from paperworkshell.diagnostics import phase_observer
from paperworkshell.domain.image_pages import (
    MAX_IMAGE_DIMENSION_PIXELS,
    MAX_IMAGE_MEMORY_BYTES,
    MAX_IMAGE_OUTPUT_BYTES,
    MAX_IMAGE_SOURCE_PIXELS,
    MAX_IMAGE_WALL_TIME_MS,
    ImagePageError,
    ImagePageErrorCode,
    ImagePagePreviewProgressPhase,
    ImageSourceFormat,
    reject_image_page,
)
from paperworkshell.domain.image_transforms import (
    IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES,
    MAX_IMAGE_TRANSFORM_ARTIFACT_BYTES,
    ImagePageTransformRequest,
    ImagePageTransformResult,
    ImageTransformArtifact,
    ImageTransformLookupKey,
    decode_image_transform_artifact,
    image_page_transform_result_from_artifact,
)
from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.pdf_thumbnails import PdfThumbnailError, PdfThumbnailErrorCode
from paperworkshell.domain.source_ingest import SourceIngestError, SourceIngestErrorCode
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
from paperworkshell.worker.image_page_broker import (
    BoundedImageOutput,
    CancellationProbe,
    CommittedImagePageLease,
    CommittedImagePageStore,
    DeadlineProbe,
    ImagePreviewPhaseSink,
    ImageWorkerCoordinator,
)
from paperworkshell.worker.protocol.models import (
    IMAGE_TRANSFORM_BACKGROUND_CLEANUP_PARAMETER,
    IMAGE_TRANSFORM_CAPABILITY,
    IMAGE_TRANSFORM_CONTRAST_PARAMETER,
    IMAGE_TRANSFORM_CROP_HEIGHT_PARAMETER,
    IMAGE_TRANSFORM_CROP_WIDTH_PARAMETER,
    IMAGE_TRANSFORM_CROP_X_PARAMETER,
    IMAGE_TRANSFORM_CROP_Y_PARAMETER,
    IMAGE_TRANSFORM_DESKEW_PARAMETER,
    IMAGE_TRANSFORM_MAXIMUM_HEIGHT_PARAMETER,
    IMAGE_TRANSFORM_MAXIMUM_WIDTH_PARAMETER,
    IMAGE_TRANSFORM_OUTPUT_CONTENT_KIND,
    IMAGE_TRANSFORM_OUTPUT_HANDLE_PARAMETER,
    IMAGE_TRANSFORM_OUTPUT_OBJECT_ID_PARAMETER,
    IMAGE_TRANSFORM_PAGE_ID_PARAMETER,
    IMAGE_TRANSFORM_QUARTER_TURNS_PARAMETER,
    IMAGE_TRANSFORM_SOURCE_HANDLE_PARAMETER,
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


class ImageTransformCacheLookup(Protocol):
    @property
    def is_hit(self) -> bool: ...

    @property
    def corrupt_removed(self) -> bool: ...

    @property
    def artifact(self) -> ImageTransformArtifact | None: ...


class ImageTransformCachePublication(Protocol):
    @property
    def artifact(self) -> ImageTransformArtifact: ...

    @property
    def published(self) -> bool: ...


class ImageTransformCacheStage(AbstractContextManager["ImageTransformCacheStage"], Protocol):
    @property
    def key(self) -> ImageTransformLookupKey: ...

    @property
    def maximum_bytes(self) -> int: ...

    def write_artifact(self, payload: bytes) -> None: ...

    def validate(
        self, *, expected_operation_id: UUID, expected_output_id: UUID
    ) -> ImageTransformArtifact: ...

    def publish(self) -> ImageTransformCachePublication: ...


class ImageTransformCacheStore(Protocol):
    def lookup_image_transform(self, key: ImageTransformLookupKey) -> ImageTransformCacheLookup: ...

    def create_image_transform_stage(
        self, key: ImageTransformLookupKey
    ) -> ImageTransformCacheStage: ...


@dataclass(frozen=True, slots=True)
class ImageTransformBrokerPolicy:
    """Trusted transform, worker, and responsiveness budgets."""

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
            or not IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES + 4
            <= limits.output_bytes
            <= MAX_IMAGE_TRANSFORM_ARTIFACT_BYTES
            or limits.memory_bytes > MAX_IMAGE_MEMORY_BYTES
            or limits.temporary_storage_bytes > 2 * MAX_IMAGE_OUTPUT_BYTES
        ):
            reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.transform_broker_policy")


@dataclass(frozen=True, slots=True, repr=False)
class _ResolvedTransformSource:
    case_id: UUID
    source_id: UUID
    page_id: UUID
    content_kind: str
    byte_size: int
    sha256: str = field(repr=False)
    handle_value: int = field(repr=False)

    def __post_init__(self) -> None:
        identifiers = (self.case_id, self.source_id, self.page_id)
        if (
            any(type(value) is not UUID or value.version != 4 for value in identifiers)
            or len(set(identifiers)) != 3
            or self.content_kind not in {"jpeg", "png"}
            or type(self.byte_size) is not int
            or self.byte_size < 1
            or type(self.sha256) is not str
            or len(self.sha256) != 64
            or type(self.handle_value) is not int
            or self.handle_value <= 0
        ):
            reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "image.transform_source")


class BrokeredImagePageTransformer:
    """Execute canonical scan transforms against one immutable committed image."""

    __slots__ = (
        "_cache",
        "_heavy_operations",
        "_id_factory",
        "_output_buffer_factory",
        "_policy",
        "_sources",
        "_worker",
    )

    def __init__(
        self,
        worker: ImageWorkerCoordinator,
        sources: CommittedImagePageStore,
        policy: ImageTransformBrokerPolicy,
        *,
        heavy_operations: HeavyOperationCoordinator,
        cache: ImageTransformCacheStore | None = None,
        id_factory: Callable[[], UUID] = uuid4,
        output_buffer_factory: Callable[[int], BoundedImageOutput] = (
            WindowsBoundedOutputBuffer.create
        ),
    ) -> None:
        if (
            type(policy) is not ImageTransformBrokerPolicy
            or type(heavy_operations) is not HeavyOperationCoordinator
            or not callable(id_factory)
            or not callable(output_buffer_factory)
        ):
            reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.transform_broker")
        self._worker = worker
        self._cache = cache
        self._sources = sources
        self._policy = policy
        self._heavy_operations = heavy_operations
        self._id_factory = id_factory
        self._output_buffer_factory = output_buffer_factory

    @retire_runtime_on_failure
    def transform(
        self,
        request: ImagePageTransformRequest,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ImagePreviewPhaseSink,
    ) -> ImagePageTransformResult:
        if type(request) is not ImagePageTransformRequest:
            reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.transform_request")
        try:
            with self._acquire_heavy_operation(deadline, cancellation):
                with self._sources.borrow_committed_image_page(
                    request.case_id,
                    request.page_id,
                    cancellation=cancellation,
                    deadline=deadline,
                ) as lease:
                    source = _resolved_source(request, lease)
                    artifact = self._get_or_generate(
                        source,
                        request,
                        deadline=deadline,
                        cancellation=cancellation,
                        progress=progress,
                    )
                return image_page_transform_result_from_artifact(request, artifact)
        except ImagePageError:
            raise
        except SourceIngestError as error:
            _raise_image_source_error(error)
        except PdfThumbnailError as error:
            code = (
                ImagePageErrorCode.LIMIT_EXCEEDED
                if error.code is PdfThumbnailErrorCode.LIMIT_EXCEEDED
                else ImagePageErrorCode.DESTINATION_FAILED
            )
            reject_image_page(code, "image.transform_output")
        except WorkerClientError as error:
            if error.code in {
                WorkerClientErrorCode.HANDSHAKE_TIMED_OUT,
                WorkerClientErrorCode.OPERATION_TIMED_OUT,
                WorkerClientErrorCode.CANCELLATION_TIMED_OUT,
            }:
                reject_image_page(ImagePageErrorCode.TIMED_OUT, "image.transform_worker")
            reject_image_page(ImagePageErrorCode.DEPENDENCY_FAILED, "image.transform_worker")
        except Exception:
            reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.transform_broker")

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
        source: _ResolvedTransformSource,
        request: ImagePageTransformRequest,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ImagePreviewPhaseSink,
    ) -> tuple[ImageTransformArtifact, bytes]:
        _require_active(deadline, cancellation)
        limits = _operation_limits(
            self._policy.limits,
            request.maximum_width_pixels,
            request.maximum_height_pixels,
        )
        output_id = self._new_id({source.case_id, source.source_id, source.page_id})
        operation_id = self._new_id({source.case_id, source.source_id, source.page_id, output_id})
        output, encoded = self._run_worker(
            source,
            request,
            limits=limits,
            output_id=output_id,
            operation_id=operation_id,
            deadline=deadline,
            cancellation=cancellation,
            progress=progress,
        )
        _report_phase(progress, ImagePagePreviewProgressPhase.VALIDATING)
        _require_active(deadline, cancellation)
        _authenticate_output_bytes(encoded, output)
        try:
            artifact = decode_image_transform_artifact(
                encoded,
                expected_operation_id=operation_id,
                expected_output_id=output_id,
            )
            key = artifact.header.cache_key
            if (
                key.case_id != source.case_id
                or key.source_id != source.source_id
                or key.page_id != source.page_id
                or key.source_byte_size != source.byte_size
                or not hmac.compare_digest(key.source_sha256, source.sha256)
                or key.source_format.value != source.content_kind
                or key.intent != request.intent
                or key.maximum_width_pixels != request.maximum_width_pixels
                or key.maximum_height_pixels != request.maximum_height_pixels
            ):
                reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_binding")
        except ImagePageError:
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_artifact")
        _require_active(deadline, cancellation)
        return artifact, encoded

    def _get_or_generate(
        self,
        source: _ResolvedTransformSource,
        request: ImagePageTransformRequest,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ImagePreviewPhaseSink,
    ) -> ImageTransformArtifact:
        _require_active(deadline, cancellation)
        _report_phase(progress, ImagePagePreviewProgressPhase.LOOKUP)
        if self._cache is None:
            artifact, _encoded = self._run(
                source, request, deadline=deadline, cancellation=cancellation, progress=progress
            )
            return artifact
        limits = _operation_limits(
            self._policy.limits, request.maximum_width_pixels, request.maximum_height_pixels
        )
        key = ImageTransformLookupKey(
            case_id=source.case_id,
            source_id=source.source_id,
            page_id=source.page_id,
            source_byte_size=source.byte_size,
            source_sha256=source.sha256,
            source_format=ImageSourceFormat(source.content_kind),
            intent=request.intent,
            maximum_width_pixels=request.maximum_width_pixels,
            maximum_height_pixels=request.maximum_height_pixels,
        )
        lookup = self._cache.lookup_image_transform(key)
        if type(lookup.is_hit) is not bool or type(lookup.corrupt_removed) is not bool:
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_cache_lookup")
        _require_active(deadline, cancellation)
        if lookup.is_hit:
            artifact = _require_cached_artifact(lookup.artifact, key)
            _report_phase(progress, ImagePagePreviewProgressPhase.VALIDATING)
            # The factory revalidates the unchanged request/snapshot contract.
            image_page_transform_result_from_artifact(request, artifact)
            return artifact
        if lookup.artifact is not None:
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_cache_miss")
        scope = phase_observer.current_scope()
        if scope is not None and not lookup.corrupt_removed:
            scope[0].record(phase_observer.Event.TRANSFORM_CACHE_MISS)
        with self._cache.create_image_transform_stage(key) as stage:
            if (
                stage.key != key
                or type(stage.maximum_bytes) is not int
                or (stage.maximum_bytes != limits.output_bytes)
            ):
                reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_cache_stage")
            artifact, encoded = self._run(
                source, request, deadline=deadline, cancellation=cancellation, progress=progress
            )
            _require_active(deadline, cancellation)
            stage.write_artifact(encoded)
            validated = stage.validate(
                expected_operation_id=artifact.header.operation_id,
                expected_output_id=artifact.header.output_id,
            )
            validated = _require_cached_artifact(validated, key)
            if validated != artifact:
                reject_image_page(
                    ImagePageErrorCode.RESULT_INVALID, "image.transform_cache_changed"
                )
            _require_active(deadline, cancellation)
            publication = stage.publish()
            if type(publication.published) is not bool:
                reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_publication")
            published = _require_cached_artifact(publication.artifact, key)
            _require_active(deadline, cancellation)
            return published

    def _run_worker(
        self,
        source: _ResolvedTransformSource,
        request: ImagePageTransformRequest,
        *,
        limits: ResourceLimits,
        output_id: UUID,
        operation_id: UUID,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ImagePreviewPhaseSink,
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
                reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_output")
            try:
                _require_active(deadline, cancellation)
                start_attempted = True
                self._worker.start(source.case_id)
                ready = self._wait_ready(deadline, cancellation)
                if IMAGE_TRANSFORM_CAPABILITY not in ready.capabilities:
                    reject_image_page(
                        ImagePageErrorCode.DEPENDENCY_UNAVAILABLE,
                        "image.transform_capability",
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
                    request,
                    source_handle=source_handle,
                    output_handle=output_handle,
                    output_id=output_id,
                    limits=limits,
                )
                _report_phase(progress, ImagePagePreviewProgressPhase.GENERATING)
                selected = self._worker.start_operation(payload, operation_id=operation_id)
                if selected != operation_id:
                    reject_image_page(
                        ImagePageErrorCode.RESULT_INVALID, "image.transform_operation"
                    )
                result = self._wait_result(
                    operation_id,
                    deadline=deadline,
                    cancellation=cancellation,
                )
            finally:
                if start_attempted:
                    self._worker.terminate()
            if result is None:
                reject_image_page(ImagePageErrorCode.DEPENDENCY_FAILED, "image.transform_result")
            output = _require_successful_output(
                result,
                operation_id=operation_id,
                output_id=output_id,
            )
            byte_count = output.expected_byte_size
            if type(byte_count) is not int or not 1 <= byte_count <= limits.output_bytes:
                reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_output_size")
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
                reject_image_page(ImagePageErrorCode.DEPENDENCY_FAILED, "image.transform_handshake")
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
                    reject_image_page(
                        ImagePageErrorCode.RESULT_INVALID, "image.transform_result_id"
                    )
                if cancellation_sent and result.outcome is OperationOutcome.SUCCEEDED:
                    reject_image_page(ImagePageErrorCode.CANCELLED, "image.transform_cancelled")
                return result
            if snapshot.phase in {
                WorkerLifecyclePhase.READY,
                WorkerLifecyclePhase.STOPPED,
                WorkerLifecyclePhase.FAULTED,
            }:
                reject_image_page(
                    ImagePageErrorCode.DEPENDENCY_FAILED, "image.transform_worker_exit"
                )
            if _deadline_expired(deadline):
                if not cancellation_sent:
                    self._worker.cancel(CancelReason.SHUTDOWN)
                reject_image_page(ImagePageErrorCode.TIMED_OUT, "image.transform_deadline")
            if _cancellation_requested(cancellation) and not cancellation_sent:
                self._worker.cancel(CancelReason.USER_REQUESTED)
                cancellation_sent = True
            self._worker.pump(self._policy.pump_interval_seconds)

    def _new_id(self, forbidden: set[UUID]) -> UUID:
        for _ in range(_MAXIMUM_IDENTIFIER_ATTEMPTS):
            try:
                value = self._id_factory()
            except BaseException:
                reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.transform_identifier")
            if type(value) is UUID and value.version == 4 and value not in forbidden:
                return value
        reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.transform_identifier")


def _require_cached_artifact(
    artifact: object, key: ImageTransformLookupKey
) -> ImageTransformArtifact:
    if type(artifact) is not ImageTransformArtifact or not key.matches(artifact.header.cache_key):
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_cache_binding")
    return artifact


def _resolved_source(
    request: ImagePageTransformRequest,
    lease: CommittedImagePageLease,
) -> _ResolvedTransformSource:
    if (
        lease.case_id != request.case_id
        or lease.page_id != request.page_id
        or lease.source_page_index != 0
        or lease.source_page_count != 1
    ):
        reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "image.transform_committed")
    if lease.source_media_type is DetectedMediaType.JPEG:
        content_kind = "jpeg"
    elif lease.source_media_type is DetectedMediaType.PNG:
        content_kind = "png"
    else:
        reject_image_page(ImagePageErrorCode.FORMAT_MISMATCH, "image.transform_format")
    return _ResolvedTransformSource(
        case_id=lease.case_id,
        source_id=lease.source_id,
        page_id=lease.page_id,
        content_kind=content_kind,
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
        reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.transform_dimensions")
    output_pixels = maximum_width_pixels * maximum_height_pixels
    output_bytes = output_pixels * 4
    artifact_bytes = IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES + output_bytes
    if (
        output_pixels > policy.pixel_count
        or artifact_bytes > policy.output_bytes
        or artifact_bytes > MAX_IMAGE_TRANSFORM_ARTIFACT_BYTES
        or output_bytes > policy.memory_bytes
    ):
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.transform_limits")
    return ResourceLimits(
        wall_time_ms=policy.wall_time_ms,
        cpu_time_ms=policy.cpu_time_ms,
        memory_bytes=policy.memory_bytes,
        temporary_storage_bytes=policy.temporary_storage_bytes,
        output_bytes=artifact_bytes,
        page_count=1,
        pixel_count=policy.pixel_count,
    )


def _operation_payload(
    source: _ResolvedTransformSource,
    request: ImagePageTransformRequest,
    *,
    source_handle: int,
    output_handle: int,
    output_id: UUID,
    limits: ResourceLimits,
) -> OperationStartPayload:
    intent = request.intent
    crop = intent.crop
    values = (
        (IMAGE_TRANSFORM_SOURCE_HANDLE_PARAMETER, ScalarValueType.INTEGER, source_handle),
        (IMAGE_TRANSFORM_OUTPUT_HANDLE_PARAMETER, ScalarValueType.INTEGER, output_handle),
        (IMAGE_TRANSFORM_OUTPUT_OBJECT_ID_PARAMETER, ScalarValueType.UUID, output_id),
        (IMAGE_TRANSFORM_PAGE_ID_PARAMETER, ScalarValueType.UUID, source.page_id),
        (
            IMAGE_TRANSFORM_MAXIMUM_WIDTH_PARAMETER,
            ScalarValueType.INTEGER,
            request.maximum_width_pixels,
        ),
        (
            IMAGE_TRANSFORM_MAXIMUM_HEIGHT_PARAMETER,
            ScalarValueType.INTEGER,
            request.maximum_height_pixels,
        ),
        (
            IMAGE_TRANSFORM_QUARTER_TURNS_PARAMETER,
            ScalarValueType.INTEGER,
            int(intent.additional_rotation) // 90,
        ),
        (IMAGE_TRANSFORM_CROP_X_PARAMETER, ScalarValueType.INTEGER, crop.x),
        (IMAGE_TRANSFORM_CROP_Y_PARAMETER, ScalarValueType.INTEGER, crop.y),
        (IMAGE_TRANSFORM_CROP_WIDTH_PARAMETER, ScalarValueType.INTEGER, crop.width),
        (IMAGE_TRANSFORM_CROP_HEIGHT_PARAMETER, ScalarValueType.INTEGER, crop.height),
        (
            IMAGE_TRANSFORM_DESKEW_PARAMETER,
            ScalarValueType.INTEGER,
            intent.deskew_angle_millidegrees_clockwise,
        ),
        (
            IMAGE_TRANSFORM_CONTRAST_PARAMETER,
            ScalarValueType.INTEGER,
            intent.contrast_factor_millionths,
        ),
        (
            IMAGE_TRANSFORM_BACKGROUND_CLEANUP_PARAMETER,
            ScalarValueType.INTEGER,
            intent.background_cleanup_strength_millionths,
        ),
    )
    return OperationStartPayload(
        operation_kind=IMAGE_TRANSFORM_CAPABILITY.capability_id,
        operation_version=IMAGE_TRANSFORM_CAPABILITY.capability_version,
        inputs=(
            CaseObjectReference(
                area=ReferenceArea.SOURCE_READ,
                object_id=source.source_id,
                operation_id=None,
                content_kind=source.content_kind,
                expected_byte_size=source.byte_size,
                expected_sha256=source.sha256,
            ),
        ),
        parameters=tuple(TaggedScalar(name, kind, value) for name, kind, value in values),
        requested_limits=limits,
    )


def _require_successful_output(
    result: WorkerOperationResultSnapshot,
    *,
    operation_id: UUID,
    output_id: UUID,
) -> CaseObjectReference:
    if type(result) is not WorkerOperationResultSnapshot or result.operation_id != operation_id:
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_worker_result")
    if result.outcome is OperationOutcome.CANCELLED:
        reject_image_page(ImagePageErrorCode.CANCELLED, "image.transform_worker_result")
    if result.outcome is OperationOutcome.TIMED_OUT:
        reject_image_page(ImagePageErrorCode.TIMED_OUT, "image.transform_worker_result")
    if result.outcome is not OperationOutcome.SUCCEEDED or len(result.outputs) != 1:
        reject_image_page(ImagePageErrorCode.DEPENDENCY_FAILED, "image.transform_worker_result")
    output = result.outputs[0]
    if (
        output.area is not ReferenceArea.OPERATION_STAGING
        or output.object_id != output_id
        or output.operation_id != operation_id
        or output.content_kind != IMAGE_TRANSFORM_OUTPUT_CONTENT_KIND
        or output.expected_byte_size is None
        or output.expected_sha256 is None
    ):
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_worker_output")
    return output


def _authenticate_output_bytes(payload: bytes, output: CaseObjectReference) -> None:
    if (
        type(payload) is not bytes
        or output.expected_byte_size != len(payload)
        or output.expected_sha256 is None
        or not hmac.compare_digest(output.expected_sha256, hashlib.sha256(payload).hexdigest())
    ):
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_output_digest")


def _report_phase(
    sink: ImagePreviewPhaseSink,
    phase: ImagePagePreviewProgressPhase,
) -> None:
    try:
        sink(phase)
    except ImagePageError:
        raise
    except BaseException:
        reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.transform_progress")


def _require_active(deadline: DeadlineProbe, cancellation: CancellationProbe) -> None:
    if _deadline_expired(deadline):
        reject_image_page(ImagePageErrorCode.TIMED_OUT, "image.transform_deadline")
    if _cancellation_requested(cancellation):
        reject_image_page(ImagePageErrorCode.CANCELLED, "image.transform_cancellation")


def _deadline_expired(probe: DeadlineProbe) -> bool:
    try:
        value = probe()
    except BaseException:
        reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.transform_deadline_probe")
    if type(value) is not bool:
        reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.transform_deadline_probe")
    return value


def _cancellation_requested(probe: CancellationProbe) -> bool:
    try:
        value = probe()
    except BaseException:
        reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.transform_cancellation_probe")
    if type(value) is not bool:
        reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.transform_cancellation_probe")
    return value


def _raise_image_source_error(error: SourceIngestError) -> Never:
    if error.code is SourceIngestErrorCode.CANCELLED:
        reject_image_page(ImagePageErrorCode.CANCELLED, "image.transform_source")
    if error.code is SourceIngestErrorCode.TIMED_OUT:
        reject_image_page(ImagePageErrorCode.TIMED_OUT, "image.transform_source")
    if error.code is SourceIngestErrorCode.CAPACITY_EXCEEDED:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.transform_source")
    reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "image.transform_source")

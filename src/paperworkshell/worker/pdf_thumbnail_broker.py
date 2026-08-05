"""GUI-process broker for case-scoped, one-shot PDF thumbnail generation."""

from __future__ import annotations

import hashlib
import hmac
import math
from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Never, Protocol, Self
from uuid import UUID, uuid4

from paperworkshell.domain.pdf_rendering import (
    MAX_PDF_RENDER_BYTES,
    MAX_PDF_RENDER_PAGES,
    MAX_PDF_RENDER_PIXELS,
    MAX_PDF_RENDER_WALL_TIME_MS,
)
from paperworkshell.domain.pdf_thumbnails import (
    MAX_PDF_THUMBNAIL_ARTIFACT_BYTES,
    PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES,
    PdfThumbnailArtifact,
    PdfThumbnailCacheKey,
    PdfThumbnailCacheStatus,
    PdfThumbnailError,
    PdfThumbnailErrorCode,
    PdfThumbnailProgressPhase,
    PdfThumbnailRequest,
    PdfThumbnailResult,
    ResolvedPdfThumbnailRequest,
    encode_pdf_thumbnail_artifact,
    pdf_thumbnail_result_from_artifact,
    reject_pdf_thumbnail,
)
from paperworkshell.domain.source_ingest import SourceIngestError, SourceIngestErrorCode
from paperworkshell.worker.client.errors import WorkerClientError, WorkerClientErrorCode
from paperworkshell.worker.client.models import (
    WorkerClientSnapshot,
    WorkerLifecyclePhase,
    WorkerOperationResultSnapshot,
)
from paperworkshell.worker.client.process import BrokeredHandlePurpose
from paperworkshell.worker.pdf_thumbnail_profile import (
    PDF_THUMBNAIL_RENDERER_PROFILE_SHA256,
)
from paperworkshell.worker.protocol.models import (
    PDF_THUMBNAIL_ADDITIONAL_ROTATION_PARAMETER,
    PDF_THUMBNAIL_CACHE_HANDLE_PARAMETER,
    PDF_THUMBNAIL_CACHE_OBJECT_ID_PARAMETER,
    PDF_THUMBNAIL_CAPABILITY,
    PDF_THUMBNAIL_CONTENT_KIND,
    PDF_THUMBNAIL_MAXIMUM_HEIGHT_PARAMETER,
    PDF_THUMBNAIL_MAXIMUM_WIDTH_PARAMETER,
    PDF_THUMBNAIL_PAGE_BOX_PARAMETER,
    PDF_THUMBNAIL_PAGE_ID_PARAMETER,
    PDF_THUMBNAIL_SOURCE_HANDLE_PARAMETER,
    PDF_THUMBNAIL_SOURCE_PAGE_INDEX_PARAMETER,
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
    """Return whether the enclosing application deadline has expired."""

    def __call__(self) -> bool: ...


class CancellationProbe(Protocol):
    """Return whether cooperative cancellation has been requested."""

    def __call__(self) -> bool: ...


class ThumbnailPhaseSink(Protocol):
    """Receive one path- and content-free page phase."""

    def __call__(self, phase: PdfThumbnailProgressPhase, /) -> None: ...


class HandleValue(Protocol):
    """One retained exact OS handle; no path is exposed to the broker contract."""

    @property
    def value(self) -> int: ...


class CommittedPdfPageLease(Protocol):
    """Manifest-bound immutable PDF page and retained exact source authority."""

    @property
    def handle(self) -> HandleValue: ...

    @property
    def case_id(self) -> UUID: ...

    @property
    def source_id(self) -> UUID: ...

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


class CommittedPdfPageStore(Protocol):
    """Resolve and integrity-check one current manifest page mapping."""

    def borrow_committed_pdf_page(
        self,
        case_id: UUID,
        page_id: UUID,
        *,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
    ) -> CommittedPdfPageLease: ...


class ThumbnailCacheLookup(Protocol):
    """Path-free result of one authenticated cache lookup."""

    @property
    def is_hit(self) -> bool: ...

    @property
    def corrupt_removed(self) -> bool: ...

    @property
    def artifact(self) -> PdfThumbnailArtifact | None: ...


class ThumbnailCachePublication(Protocol):
    """Validated final artifact after publish or safe no-replace collision reuse."""

    @property
    def artifact(self) -> PdfThumbnailArtifact: ...

    @property
    def published(self) -> bool: ...


class ThumbnailCacheStage(Protocol):
    """Exact quota-reserved stage that remains non-authoritative until publish."""

    @property
    def key(self) -> PdfThumbnailCacheKey: ...

    @property
    def cache_object_id(self) -> UUID: ...

    @property
    def write_handle(self) -> HandleValue: ...

    def validate(self, *, expected_operation_id: UUID) -> PdfThumbnailArtifact: ...

    def publish(self) -> ThumbnailCachePublication: ...

    def retain_untrusted(self) -> None: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class ThumbnailCacheStore(Protocol):
    """Storage-independent port for lookup, staging, validation, and publication."""

    def lookup(self, key: PdfThumbnailCacheKey) -> ThumbnailCacheLookup: ...

    def create_stage(self, key: PdfThumbnailCacheKey) -> ThumbnailCacheStage: ...


class PdfThumbnailWorkerCoordinator(Protocol):
    """Narrow single-owner worker client used by the one-shot broker."""

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
class PdfThumbnailBrokerPolicy:
    """Trusted page, pixel, storage, memory, and responsiveness limits."""

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
            or limits.wall_time_ms > MAX_PDF_RENDER_WALL_TIME_MS
            or limits.page_count > MAX_PDF_RENDER_PAGES
            or limits.pixel_count > MAX_PDF_RENDER_PIXELS
            or not PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES + 4
            <= limits.output_bytes
            <= MAX_PDF_THUMBNAIL_ARTIFACT_BYTES
            or limits.memory_bytes > MAX_PDF_RENDER_BYTES
        ):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "thumbnail.broker_policy")


class BrokeredPdfThumbnailGenerator:
    """Authenticate a page, reuse its cache or render it in a fresh worker."""

    __slots__ = (
        "_cache",
        "_id_factory",
        "_policy",
        "_sources",
        "_worker",
    )

    def __init__(
        self,
        worker: PdfThumbnailWorkerCoordinator,
        sources: CommittedPdfPageStore,
        cache: ThumbnailCacheStore,
        policy: PdfThumbnailBrokerPolicy,
        *,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if type(policy) is not PdfThumbnailBrokerPolicy or not callable(id_factory):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "thumbnail.broker_config")
        self._worker = worker
        self._sources = sources
        self._cache = cache
        self._policy = policy
        self._id_factory = id_factory

    def get_or_generate(
        self,
        request: PdfThumbnailRequest,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ThumbnailPhaseSink,
    ) -> PdfThumbnailResult:
        """Return a validated cache snapshot only after worker-tree termination."""

        if type(request) is not PdfThumbnailRequest:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "thumbnail.broker_request")
        try:
            return self._get_or_generate(
                request,
                deadline=deadline,
                cancellation=cancellation,
                progress=progress,
            )
        except PdfThumbnailError:
            raise
        except SourceIngestError as error:
            _raise_thumbnail_source_error(error)
        except WorkerClientError as error:
            if error.code in {
                WorkerClientErrorCode.HANDSHAKE_TIMED_OUT,
                WorkerClientErrorCode.OPERATION_TIMED_OUT,
                WorkerClientErrorCode.CANCELLATION_TIMED_OUT,
            }:
                reject_pdf_thumbnail(PdfThumbnailErrorCode.TIMED_OUT, "thumbnail.worker")
            reject_pdf_thumbnail(PdfThumbnailErrorCode.WORKER_FAILED, "thumbnail.worker")
        except Exception:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.broker")

    def _get_or_generate(
        self,
        request: PdfThumbnailRequest,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ThumbnailPhaseSink,
    ) -> PdfThumbnailResult:
        _require_active(deadline, cancellation)
        with self._sources.borrow_committed_pdf_page(
            request.case_id,
            request.page_id,
            cancellation=cancellation,
            deadline=deadline,
        ) as source:
            resolved = _resolved_request(request, source)
            operation_limits = _operation_limits(self._policy.limits, resolved)
            key = resolved.cache_key
            lookup = self._cache.lookup(key)
            if type(lookup.is_hit) is not bool or type(lookup.corrupt_removed) is not bool:
                reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.cache_lookup")
            if lookup.is_hit:
                artifact = lookup.artifact
                if type(artifact) is not PdfThumbnailArtifact or artifact.header.cache_key != key:
                    reject_pdf_thumbnail(
                        PdfThumbnailErrorCode.RESULT_INVALID,
                        "thumbnail.cache_hit",
                    )
                _report_phase(progress, PdfThumbnailProgressPhase.VALIDATING)
                _require_active(deadline, cancellation)
                return pdf_thumbnail_result_from_artifact(
                    request,
                    artifact,
                    PdfThumbnailCacheStatus.HIT,
                )
            if lookup.artifact is not None:
                reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.cache_miss")
            rebuild = lookup.corrupt_removed

            with self._cache.create_stage(key) as stage:
                if stage.key != key:
                    reject_pdf_thumbnail(
                        PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.stage_key"
                    )
                cache_object_id = _require_uuid4(
                    stage.cache_object_id,
                    context="thumbnail.stage_object",
                )
                operation_id = self._new_id(
                    {
                        request.case_id,
                        request.page_id,
                        resolved.source_id,
                        cache_object_id,
                    }
                )
                operation_result = self._run_worker(
                    resolved,
                    operation_limits,
                    stage,
                    source_handle_value=source.handle.value,
                    operation_id=operation_id,
                    deadline=deadline,
                    cancellation=cancellation,
                    progress=progress,
                )
                output = _require_successful_output(
                    operation_result,
                    operation_id=operation_id,
                    cache_object_id=cache_object_id,
                )
                _report_phase(progress, PdfThumbnailProgressPhase.VALIDATING)
                _require_active(deadline, cancellation)
                artifact = stage.validate(expected_operation_id=operation_id)
                _require_worker_reference(output, artifact, key)
                _require_active(deadline, cancellation)
                publication = stage.publish()
                if type(publication.published) is not bool:
                    reject_pdf_thumbnail(
                        PdfThumbnailErrorCode.RESULT_INVALID,
                        "thumbnail.publication",
                    )
                published_artifact = publication.artifact
                if (
                    type(published_artifact) is not PdfThumbnailArtifact
                    or published_artifact.header.cache_key != key
                ):
                    reject_pdf_thumbnail(
                        PdfThumbnailErrorCode.RESULT_INVALID,
                        "thumbnail.publication",
                    )
                return pdf_thumbnail_result_from_artifact(
                    request,
                    published_artifact,
                    (
                        PdfThumbnailCacheStatus.REBUILT
                        if rebuild
                        else PdfThumbnailCacheStatus.GENERATED
                    ),
                )

    def _run_worker(
        self,
        resolved: ResolvedPdfThumbnailRequest,
        limits: ResourceLimits,
        stage: ThumbnailCacheStage,
        *,
        source_handle_value: int,
        operation_id: UUID,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ThumbnailPhaseSink,
    ) -> WorkerOperationResultSnapshot:
        result: WorkerOperationResultSnapshot | None = None
        start_attempted = False
        try:
            _require_active(deadline, cancellation)
            start_attempted = True
            self._worker.start(resolved.request.case_id)
            ready = self._wait_ready(deadline, cancellation)
            if PDF_THUMBNAIL_CAPABILITY not in ready.capabilities:
                reject_pdf_thumbnail(PdfThumbnailErrorCode.WORKER_FAILED, "thumbnail.capability")
            source_handle = self._worker.duplicate_handle(
                source_handle_value,
                BrokeredHandlePurpose.READ_SOURCE,
            )
            cache_handle = self._worker.duplicate_handle(
                stage.write_handle.value,
                BrokeredHandlePurpose.WRITE_OPERATION_CACHE,
            )
            payload = _operation_payload(
                resolved,
                source_handle=source_handle,
                cache_handle=cache_handle,
                cache_object_id=stage.cache_object_id,
                limits=limits,
            )
            _report_phase(progress, PdfThumbnailProgressPhase.RENDERING)
            selected = self._worker.start_operation(payload, operation_id=operation_id)
            if selected != operation_id:
                reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.operation_id")
            result = self._wait_result(
                operation_id,
                deadline=deadline,
                cancellation=cancellation,
            )
        finally:
            if start_attempted:
                try:
                    self._worker.terminate()
                except BaseException:
                    stage.retain_untrusted()
                    raise
        if result is None:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.WORKER_FAILED, "thumbnail.no_result")
        return result

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
                reject_pdf_thumbnail(PdfThumbnailErrorCode.WORKER_FAILED, "thumbnail.handshake")
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
                reject_pdf_thumbnail(PdfThumbnailErrorCode.TIMED_OUT, "thumbnail.deadline")
            if _cancellation_requested(cancellation) and not cancellation_sent:
                self._worker.cancel(CancelReason.USER_REQUESTED)
                cancellation_sent = True
            snapshot = self._worker.query()
            result = snapshot.last_result
            if result is not None:
                if result.operation_id != operation_id:
                    reject_pdf_thumbnail(
                        PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.result_id"
                    )
                if cancellation_sent and result.outcome is OperationOutcome.SUCCEEDED:
                    reject_pdf_thumbnail(PdfThumbnailErrorCode.CANCELLED, "thumbnail.cancelled")
                return result
            if snapshot.phase in {WorkerLifecyclePhase.STOPPED, WorkerLifecyclePhase.FAULTED}:
                reject_pdf_thumbnail(PdfThumbnailErrorCode.WORKER_FAILED, "thumbnail.worker_exit")
            self._worker.pump(self._policy.pump_interval_seconds)

    def _new_id(self, forbidden: set[UUID]) -> UUID:
        for _ in range(_MAXIMUM_IDENTIFIER_ATTEMPTS):
            try:
                value = self._id_factory()
            except BaseException:
                reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.identifier")
            if type(value) is UUID and value.version == 4 and value not in forbidden:
                return value
        reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.identifier")


def _resolved_request(
    request: PdfThumbnailRequest,
    source: CommittedPdfPageLease,
) -> ResolvedPdfThumbnailRequest:
    if (
        source.case_id != request.case_id
        or source.page_id != request.page_id
        or type(source.handle.value) is not int
        or source.handle.value <= 0
    ):
        reject_pdf_thumbnail(PdfThumbnailErrorCode.SOURCE_INVALID, "thumbnail.source_binding")
    return ResolvedPdfThumbnailRequest(
        request=request,
        source_id=source.source_id,
        source_page_index=source.source_page_index,
        source_page_count=source.source_page_count,
        source_byte_size=source.source_byte_size,
        source_sha256=source.source_sha256,
        renderer_profile_sha256=PDF_THUMBNAIL_RENDERER_PROFILE_SHA256,
    )


def _operation_limits(
    policy: ResourceLimits,
    resolved: ResolvedPdfThumbnailRequest,
) -> ResourceLimits:
    request = resolved.request
    maximum_pixels = request.maximum_width_pixels * request.maximum_height_pixels
    maximum_raster_bytes = maximum_pixels * 4
    if (
        resolved.source_page_count > policy.page_count
        or maximum_pixels > policy.pixel_count
        or maximum_raster_bytes > policy.memory_bytes
        or PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES + maximum_raster_bytes > policy.output_bytes
        or resolved.source_byte_size + maximum_raster_bytes > policy.temporary_storage_bytes
    ):
        reject_pdf_thumbnail(PdfThumbnailErrorCode.LIMIT_EXCEEDED, "thumbnail.operation_limits")
    return ResourceLimits(
        wall_time_ms=policy.wall_time_ms,
        cpu_time_ms=policy.cpu_time_ms,
        memory_bytes=policy.memory_bytes,
        temporary_storage_bytes=policy.temporary_storage_bytes,
        output_bytes=policy.output_bytes,
        page_count=resolved.source_page_count,
        pixel_count=policy.pixel_count,
    )


def _operation_payload(
    resolved: ResolvedPdfThumbnailRequest,
    *,
    source_handle: int,
    cache_handle: int,
    cache_object_id: UUID,
    limits: ResourceLimits,
) -> OperationStartPayload:
    request = resolved.request
    render_request = resolved.render_request
    return OperationStartPayload(
        operation_kind=PDF_THUMBNAIL_CAPABILITY.capability_id,
        operation_version=PDF_THUMBNAIL_CAPABILITY.capability_version,
        inputs=(
            CaseObjectReference(
                area=ReferenceArea.SOURCE_READ,
                object_id=resolved.source_id,
                operation_id=None,
                content_kind="pdf",
                expected_byte_size=resolved.source_byte_size,
                expected_sha256=resolved.source_sha256,
            ),
        ),
        parameters=(
            TaggedScalar(
                PDF_THUMBNAIL_SOURCE_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                source_handle,
            ),
            TaggedScalar(
                PDF_THUMBNAIL_CACHE_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                cache_handle,
            ),
            TaggedScalar(
                PDF_THUMBNAIL_CACHE_OBJECT_ID_PARAMETER,
                ScalarValueType.UUID,
                cache_object_id,
            ),
            TaggedScalar(
                PDF_THUMBNAIL_PAGE_ID_PARAMETER,
                ScalarValueType.UUID,
                request.page_id,
            ),
            TaggedScalar(
                PDF_THUMBNAIL_SOURCE_PAGE_INDEX_PARAMETER,
                ScalarValueType.INTEGER,
                resolved.source_page_index,
            ),
            TaggedScalar(
                PDF_THUMBNAIL_MAXIMUM_WIDTH_PARAMETER,
                ScalarValueType.INTEGER,
                request.maximum_width_pixels,
            ),
            TaggedScalar(
                PDF_THUMBNAIL_MAXIMUM_HEIGHT_PARAMETER,
                ScalarValueType.INTEGER,
                request.maximum_height_pixels,
            ),
            TaggedScalar(
                PDF_THUMBNAIL_PAGE_BOX_PARAMETER,
                ScalarValueType.TOKEN,
                render_request.page_box.value,
            ),
            TaggedScalar(
                PDF_THUMBNAIL_ADDITIONAL_ROTATION_PARAMETER,
                ScalarValueType.INTEGER,
                int(request.additional_rotation),
            ),
        ),
        requested_limits=limits,
    )


def _require_successful_output(
    result: WorkerOperationResultSnapshot,
    *,
    operation_id: UUID,
    cache_object_id: UUID,
) -> CaseObjectReference:
    if type(result) is not WorkerOperationResultSnapshot or result.operation_id != operation_id:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.worker_result")
    if result.outcome is OperationOutcome.CANCELLED:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.CANCELLED, "thumbnail.worker_result")
    if result.outcome is OperationOutcome.TIMED_OUT:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.TIMED_OUT, "thumbnail.worker_result")
    if result.outcome is not OperationOutcome.SUCCEEDED or len(result.outputs) != 1:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.WORKER_FAILED, "thumbnail.worker_result")
    output = result.outputs[0]
    if (
        output.area is not ReferenceArea.OPERATION_STAGING
        or output.object_id != cache_object_id
        or output.operation_id != operation_id
        or output.content_kind != PDF_THUMBNAIL_CONTENT_KIND
        or output.expected_byte_size is None
        or output.expected_sha256 is None
    ):
        reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.worker_output")
    return output


def _require_worker_reference(
    output: CaseObjectReference,
    artifact: PdfThumbnailArtifact,
    key: PdfThumbnailCacheKey,
) -> None:
    if type(artifact) is not PdfThumbnailArtifact or artifact.header.cache_key != key:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.stage_artifact")
    encoded = encode_pdf_thumbnail_artifact(artifact.header, artifact.payload)
    digest = hashlib.sha256(encoded).hexdigest()
    if (
        output.expected_byte_size != len(encoded)
        or output.expected_sha256 is None
        or not hmac.compare_digest(output.expected_sha256, digest)
    ):
        reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.output_digest")


def _report_phase(sink: ThumbnailPhaseSink, phase: PdfThumbnailProgressPhase) -> None:
    try:
        sink(phase)
    except PdfThumbnailError:
        raise
    except BaseException:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.progress")


def _require_active(deadline: DeadlineProbe, cancellation: CancellationProbe) -> None:
    if _deadline_expired(deadline):
        reject_pdf_thumbnail(PdfThumbnailErrorCode.TIMED_OUT, "thumbnail.deadline")
    if _cancellation_requested(cancellation):
        reject_pdf_thumbnail(PdfThumbnailErrorCode.CANCELLED, "thumbnail.cancelled")


def _deadline_expired(probe: DeadlineProbe) -> bool:
    try:
        value = probe()
    except PdfThumbnailError:
        raise
    except BaseException:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.deadline_probe")
    if type(value) is not bool:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.deadline_probe")
    return value


def _cancellation_requested(probe: CancellationProbe) -> bool:
    try:
        value = probe()
    except PdfThumbnailError:
        raise
    except BaseException:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.cancel_probe")
    if type(value) is not bool:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.cancel_probe")
    return value


def _require_uuid4(value: object, *, context: str) -> UUID:
    if type(value) is not UUID or value.version != 4:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, context)
    return value


def _raise_thumbnail_source_error(error: SourceIngestError) -> Never:
    if error.code is SourceIngestErrorCode.CANCELLED:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.CANCELLED, "thumbnail.source")
    if error.code is SourceIngestErrorCode.TIMED_OUT:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.TIMED_OUT, "thumbnail.source")
    if error.code is SourceIngestErrorCode.CAPACITY_EXCEEDED:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.LIMIT_EXCEEDED, "thumbnail.source")
    if error.code in {
        SourceIngestErrorCode.SOURCE_UNAVAILABLE,
        SourceIngestErrorCode.SOURCE_CHANGED,
        SourceIngestErrorCode.CASE_UNAVAILABLE,
    }:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.SOURCE_INVALID, "thumbnail.source")
    reject_pdf_thumbnail(PdfThumbnailErrorCode.SOURCE_INVALID, "thumbnail.source")

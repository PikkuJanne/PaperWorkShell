"""Transient, handle-only comparison rendering through fresh thumbnail workers."""

from __future__ import annotations

import math
import os
import threading
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, BinaryIO, Self
from uuid import UUID, uuid4

from paperworkshell.domain.compression_review import CompressionPageChange
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.pdf_export import (
    PdfExportPlan,
    PdfExportVerificationReport,
    build_pdf_export_plan_for_pages,
)
from paperworkshell.domain.pdf_rendering import PdfRenderRotation
from paperworkshell.domain.pdf_thumbnails import (
    PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES,
    PdfThumbnailArtifact,
    PdfThumbnailCacheKey,
    PdfThumbnailErrorCode,
    PdfThumbnailRequest,
    PdfThumbnailResult,
    decode_pdf_thumbnail_artifact,
    reject_pdf_thumbnail,
)
from paperworkshell.domain.resource_policy import ResourcePolicy
from paperworkshell.worker._windows_broker import (
    drain_windows_bounded_output_cleanup,
    drain_windows_result_pipe_cleanup,
)
from paperworkshell.worker.client._windows import WindowsProcessLauncher
from paperworkshell.worker.client.client import WorkerClient
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.pdf_thumbnail_broker import (
    BrokeredPdfThumbnailGenerator,
    CancellationProbe,
    CommittedPdfPageStore,
    DeadlineProbe,
    PdfThumbnailBrokerPolicy,
)
from paperworkshell.worker.protocol.models import PDF_THUMBNAIL_CAPABILITY, ResourceLimits

if TYPE_CHECKING:
    from paperworkshell.worker.desktop_composition import DesktopWorkerCompositionConfig

if os.name == "nt":
    import msvcrt


@dataclass(frozen=True, slots=True, repr=False)
class _Handle:
    value: int


@dataclass(frozen=True, slots=True, repr=False)
class _PageLease:
    handle: _Handle
    case_id: UUID
    source_id: UUID
    page_id: UUID
    source_page_index: int
    source_page_count: int
    source_byte_size: int
    source_sha256: str
    _owner: ExitStack = field(default_factory=ExitStack)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self._owner.close()


@dataclass(frozen=True, slots=True, repr=False)
class _OriginalPageStore:
    sources: CommittedPdfPageStore
    plan: PdfExportPlan

    def borrow_committed_pdf_page(
        self,
        case_id: UUID,
        page_id: UUID,
        *,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
    ) -> _PageLease:
        page, expected = self.plan.pages[0], self.plan.sources[0]
        if case_id != self.plan.case_id or page_id != page.page_id:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.SOURCE_INVALID, "compression_preview.page")
        with ExitStack() as owner:
            source = owner.enter_context(
                self.sources.borrow_committed_pdf_page(
                    case_id, page_id, cancellation=cancellation, deadline=deadline
                )
            )
            if (
                source.case_id != case_id
                or source.page_id != page_id
                or source.source_id != expected.source_id
                or source.source_page_index != page.source_page_index
                or source.source_page_count != expected.page_count
                or source.source_byte_size != expected.byte_size
                or source.source_sha256 != expected.sha256
            ):
                reject_pdf_thumbnail(
                    PdfThumbnailErrorCode.SOURCE_INVALID, "compression_preview.original_binding"
                )
            return _PageLease(
                _Handle(source.handle.value),
                case_id,
                expected.source_id,
                page_id,
                page.source_page_index,
                expected.page_count,
                expected.byte_size,
                expected.sha256,
                owner.pop_all(),
            )


@dataclass(frozen=True, slots=True, repr=False)
class _ProposedPageStore:
    source: BinaryIO
    report: PdfExportVerificationReport
    case_id: UUID
    page_id: UUID
    page_index: int
    source_id: UUID = field(default_factory=uuid4)

    def borrow_committed_pdf_page(
        self,
        case_id: UUID,
        page_id: UUID,
        *,
        cancellation: CancellationProbe,
        deadline: DeadlineProbe,
    ) -> _PageLease:
        _require_active(deadline, cancellation)
        if case_id != self.case_id or page_id != self.page_id:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.SOURCE_INVALID, "compression_preview.page")
        # The prepared transaction owns this read-only stream. The thumbnail broker
        # duplicates its exact handle with SOURCE_READ rights for the child.
        return _PageLease(
            _Handle(_stream_handle(self.source)),
            case_id,
            self.source_id,
            page_id,
            self.page_index,
            self.report.page_count,
            self.report.output_byte_size,
            self.report.output_sha256,
        )


def _stream_handle(source: BinaryIO) -> int:
    try:
        if os.name != "nt" or source.closed or source.writable() or not source.readable():
            raise ValueError
        handle = msvcrt.get_osfhandle(source.fileno())
        if type(handle) is not int or handle <= 0:
            raise ValueError
        return handle
    except Exception:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.SOURCE_INVALID, "compression_preview.stream")


@dataclass(frozen=True, slots=True)
class _CacheMiss:
    is_hit: bool = False
    corrupt_removed: bool = False
    artifact: PdfThumbnailArtifact | None = None


@dataclass(frozen=True, slots=True, repr=False)
class _Publication:
    artifact: PdfThumbnailArtifact
    published: bool = True


class _VolatileStage:
    """One bounded artifact, discarded on exit without filesystem publication."""

    def __init__(self, key: PdfThumbnailCacheKey) -> None:
        self.key = key
        self.cache_object_id = uuid4()
        self.maximum_bytes = (
            PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES
            + key.maximum_width_pixels * key.maximum_height_pixels * 4
        )
        self._payload: bytes | None = None
        self._artifact: PdfThumbnailArtifact | None = None
        self._closed = False

    def __enter__(self) -> Self:
        self._require_open()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._payload = None
        self._artifact = None
        self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "compression_preview.closed")

    def write_artifact(self, payload: bytes) -> None:
        self._require_open()
        if type(payload) is not bytes or len(payload) > self.maximum_bytes:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.LIMIT_EXCEEDED, "compression_preview.cache")
        if self._payload is not None:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "compression_preview.cache")
        self._payload = payload

    def validate(self, *, expected_operation_id: UUID) -> PdfThumbnailArtifact:
        self._require_open()
        if self._payload is None:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "compression_preview.cache")
        self._artifact = decode_pdf_thumbnail_artifact(
            self._payload,
            expected_key=self.key,
            expected_operation_id=expected_operation_id,
            expected_cache_object_id=self.cache_object_id,
        )
        return self._artifact

    def publish(self) -> _Publication:
        self._require_open()
        if self._artifact is None:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "compression_preview.cache")
        return _Publication(self._artifact)


class _VolatileCache:
    def lookup(self, key: PdfThumbnailCacheKey) -> _CacheMiss:
        return _CacheMiss()

    def create_stage(self, key: PdfThumbnailCacheKey) -> _VolatileStage:
        return _VolatileStage(key)


class NativeCompressionPreviewRenderer:
    """Retain configuration only; each page render owns and closes a fresh worker."""

    def __init__(
        self,
        config: DesktopWorkerCompositionConfig,
        original_sources: CommittedPdfPageStore,
        policy: ResourcePolicy,
        *,
        heavy_operations: HeavyOperationCoordinator,
    ) -> None:
        from paperworkshell.worker.desktop_composition import DesktopWorkerCompositionConfig

        if (
            type(config) is not DesktopWorkerCompositionConfig
            or type(policy) is not ResourcePolicy
            or type(heavy_operations) is not HeavyOperationCoordinator
            or heavy_operations.snapshot().maximum_concurrency
            != policy.maximum_heavy_operation_concurrency
            or config.maximum_heavy_operation_concurrency
            != policy.maximum_heavy_operation_concurrency
            or not callable(getattr(original_sources, "borrow_committed_pdf_page", None))
        ):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "compression_preview.config")
        self._config = config
        self._sources = original_sources
        self._policy = policy
        self._heavy_operations = heavy_operations
        self._operation = threading.Lock()

    def render_comparison(
        self,
        snapshot: ManifestSnapshot,
        change: CompressionPageChange,
        proposed_source: BinaryIO,
        proposed_report: PdfExportVerificationReport,
        *,
        reserved_staged_bytes: int,
        maximum_width_pixels: int,
        maximum_height_pixels: int,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> tuple[PdfThumbnailResult, PdfThumbnailResult]:
        """Render the bound original and proposed pages sequentially, without caching."""

        if not self._operation.acquire(blocking=False):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.LIMIT_EXCEEDED, "compression_preview.busy")
        try:
            _require_active(deadline, cancellation)
            if (
                type(snapshot) is not ManifestSnapshot
                or type(change) is not CompressionPageChange
                or type(proposed_report) is not PdfExportVerificationReport
                or change.page_index >= proposed_report.page_count
                or type(reserved_staged_bytes) is not int
                or reserved_staged_bytes < proposed_report.output_byte_size
            ):
                reject_pdf_thumbnail(
                    PdfThumbnailErrorCode.MODEL_INVALID, "compression_preview.input"
                )
            plan = build_pdf_export_plan_for_pages(snapshot, (change.page_id,))
            # Validate caller dimensions before scaling, including bool rejection.
            requested = PdfThumbnailRequest(
                plan.case_id, change.page_id, maximum_width_pixels, maximum_height_pixels
            )
            limits, width, height = self._render_limits(requested, reserved_staged_bytes)
            largest_source = max(plan.sources[0].byte_size, proposed_report.output_byte_size)
            if (
                largest_source + width * height * 4 > limits.temporary_storage_bytes
                or largest_source > self._policy.maximum_source_bytes
                or max(plan.sources[0].page_count, proposed_report.page_count) > limits.page_count
            ):
                reject_pdf_thumbnail(
                    PdfThumbnailErrorCode.LIMIT_EXCEEDED, "compression_preview.source"
                )
            _stream_handle(proposed_source)
            original = self._render(
                PdfThumbnailRequest(
                    plan.case_id,
                    change.page_id,
                    width,
                    height,
                    PdfRenderRotation(plan.pages[0].additional_rotation.value),
                ),
                _OriginalPageStore(self._sources, plan),
                limits,
                deadline,
                cancellation,
            )
            proposed = self._render(
                PdfThumbnailRequest(plan.case_id, change.page_id, width, height),
                _ProposedPageStore(
                    proposed_source,
                    proposed_report,
                    plan.case_id,
                    change.page_id,
                    change.page_index,
                ),
                limits,
                deadline,
                cancellation,
            )
            return original, proposed
        finally:
            self._operation.release()

    def _render_limits(
        self, request: PdfThumbnailRequest, reserved: int
    ) -> tuple[ResourceLimits, int, int]:
        policy, configured = self._policy, self._config.thumbnail_policy.limits
        remaining = policy.maximum_temporary_storage_bytes - reserved
        pixels = min(
            policy.maximum_preview_pixels_per_page,
            policy.thumbnail_preview_cache_bytes // 32,
            configured.pixel_count,
            (configured.output_bytes - PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES) // 4,
            configured.memory_bytes // 4,
            policy.worker_process_memory_bytes // 4,
        )
        if remaining < 1 or pixels < 1:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.LIMIT_EXCEEDED, "compression_preview.budget")
        width, height = request.maximum_width_pixels, request.maximum_height_pixels
        if width * height > pixels:
            scale = math.sqrt(pixels / (width * height))
            width, height = max(1, int(width * scale)), max(1, int(height * scale))
            if width * height > pixels:
                width = min(width, pixels)
                height = min(height, pixels // width)
        return (
            replace(
                configured,
                cpu_time_ms=min(configured.cpu_time_ms, policy.worker_cpu_time_ms),
                memory_bytes=min(configured.memory_bytes, policy.worker_process_memory_bytes),
                temporary_storage_bytes=min(configured.temporary_storage_bytes, remaining),
                output_bytes=PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES + width * height * 4,
                page_count=min(configured.page_count, policy.maximum_page_count),
                pixel_count=width * height,
            ),
            width,
            height,
        )

    def _render(
        self,
        request: PdfThumbnailRequest,
        sources: CommittedPdfPageStore,
        limits: ResourceLimits,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> PdfThumbnailResult:
        _require_active(deadline, cancellation)
        config, policy = self._config, self._policy
        worker = WorkerClient(
            WindowsProcessLauncher(config._launch_config),
            replace(
                config.resource_policy,
                process_memory_bytes=min(
                    config.resource_policy.process_memory_bytes, policy.worker_process_memory_bytes
                ),
                job_memory_bytes=min(
                    config.resource_policy.job_memory_bytes, policy.worker_job_memory_bytes
                ),
                cpu_time_ms=min(config.resource_policy.cpu_time_ms, policy.worker_cpu_time_ms),
                maximum_processes=min(
                    config.resource_policy.maximum_processes, policy.worker_maximum_processes
                ),
            ),
            config.timeout_policy,
            required_capabilities=(PDF_THUMBNAIL_CAPABILITY,),
        )
        # ExitStack attempts every cleanup even if teardown itself fails.
        with ExitStack() as owner:
            owner.callback(drain_windows_result_pipe_cleanup)
            owner.callback(drain_windows_bounded_output_cleanup)
            owner.callback(worker.terminate)
            generator = BrokeredPdfThumbnailGenerator(
                worker,
                sources,
                _VolatileCache(),
                PdfThumbnailBrokerPolicy(limits, config.thumbnail_policy.pump_interval_seconds),
                heavy_operations=self._heavy_operations,
            )
            return generator.get_or_generate(
                request, deadline=deadline, cancellation=cancellation, progress=lambda _phase: None
            )


def create_compression_preview_renderer(
    config: DesktopWorkerCompositionConfig,
    original_sources: CommittedPdfPageStore,
    policy: ResourcePolicy,
    *,
    heavy_operations: HeavyOperationCoordinator,
) -> NativeCompressionPreviewRenderer:
    """Build a renderer that retains no worker, source handle, or preview pixels."""

    return NativeCompressionPreviewRenderer(
        config, original_sources, policy, heavy_operations=heavy_operations
    )


def _require_active(deadline: DeadlineProbe, cancellation: CancellationProbe) -> None:
    try:
        expired, cancelled = deadline(), cancellation()
    except Exception:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "compression_preview.probe")
    if type(expired) is not bool or type(cancelled) is not bool:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "compression_preview.probe")
    if expired:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.TIMED_OUT, "compression_preview.deadline")
    if cancelled:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.CANCELLED, "compression_preview.cancelled")

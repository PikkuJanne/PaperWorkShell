"""Owner-thread composition for the desktop document-worker services."""

from __future__ import annotations

import threading
from contextlib import AbstractContextManager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Self, cast

from paperworkshell.domain.image_pages import IMAGE_PAGE_ARTIFACT_HEADER_BYTES
from paperworkshell.domain.signature_images import (
    SIGNATURE_IMAGE_RASTER_ARTIFACT_HEADER_BYTES,
)
from paperworkshell.worker._windows_broker import (
    drain_windows_bounded_output_cleanup,
    drain_windows_result_pipe_cleanup,
)
from paperworkshell.worker.client._windows import (
    WindowsLaunchConfig,
    WindowsProcessLauncher,
)
from paperworkshell.worker.client.client import WorkerClient
from paperworkshell.worker.client.errors import (
    WorkerClientContext,
    WorkerClientErrorCode,
    reject_client,
)
from paperworkshell.worker.client.models import (
    WorkerResourceSnapshot,
    WorkerTimeoutPolicy,
)
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.flattened_pdf_export_composition import (
    FLATTENED_PDF_EXPORT_WORKER_MAXIMUM_PROCESSES as FLATTENED_PDF_EXPORT_WORKER_MAXIMUM_PROCESSES,
)
from paperworkshell.worker.flattened_pdf_export_composition import (
    FlattenedPdfExportWorkerComposition as FlattenedPdfExportWorkerComposition,
)
from paperworkshell.worker.flattened_pdf_export_composition import (
    FlattenedPdfExportWorkerCompositionConfig as FlattenedPdfExportWorkerCompositionConfig,
)
from paperworkshell.worker.flattened_pdf_export_composition import (
    create_flattened_pdf_export_worker_composition as create_flattened_pdf_export_worker_composition,
)
from paperworkshell.worker.flattened_pdf_export_composition import (
    create_flattened_pdf_export_worker_composition_config as create_flattened_pdf_export_worker_composition_config,
)
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator, HeavyOperationSnapshot
from paperworkshell.worker.image_page_broker import (
    BrokeredImagePageGenerator,
    BrokeredImageSourceInspector,
    CommittedImagePageStore,
    ImageNormalizeBrokerPolicy,
    create_image_page_brokers,
)
from paperworkshell.worker.image_transform_broker import (
    BrokeredImagePageTransformer,
    ImageTransformBrokerPolicy,
)
from paperworkshell.worker.ocr_composition import (
    OCR_WORKER_MAXIMUM_PROCESSES as OCR_WORKER_MAXIMUM_PROCESSES,
)
from paperworkshell.worker.ocr_composition import (
    OcrWorkerComposition as OcrWorkerComposition,
)
from paperworkshell.worker.ocr_composition import (
    OcrWorkerCompositionConfig as OcrWorkerCompositionConfig,
)
from paperworkshell.worker.ocr_composition import (
    create_ocr_worker_composition as create_ocr_worker_composition,
)
from paperworkshell.worker.ocr_composition import (
    create_ocr_worker_composition_config as create_ocr_worker_composition_config,
)
from paperworkshell.worker.pdf_export_broker import (
    BrokeredPdfExporter,
    PdfExportBrokerPolicy,
)
from paperworkshell.worker.pdf_source_inspector import (
    BrokeredPdfSourceInspector,
    PdfInspectionBrokerPolicy,
    PreparedSourceHandleBroker,
)
from paperworkshell.worker.pdf_thumbnail_broker import (
    BrokeredPdfThumbnailGenerator,
    CommittedPdfPageStore,
    PdfThumbnailBrokerPolicy,
    ThumbnailCacheStore,
)
from paperworkshell.worker.protocol.models import (
    IMAGE_NORMALIZE_CAPABILITY,
    IMAGE_TRANSFORM_CAPABILITY,
    PDF_EXPORT_CAPABILITY,
    PDF_INSPECT_CAPABILITY,
    PDF_THUMBNAIL_CAPABILITY,
    SIGNATURE_IMAGE_RASTER_CAPABILITY,
    ResourceLimits,
)
from paperworkshell.worker.quality_ocr_review_composition import (
    QUALITY_OCR_REVIEW_WORKER_MAXIMUM_PROCESSES as QUALITY_OCR_REVIEW_WORKER_MAXIMUM_PROCESSES,
)
from paperworkshell.worker.quality_ocr_review_composition import (
    QualityOcrReviewWorkerComposition as QualityOcrReviewWorkerComposition,
)
from paperworkshell.worker.quality_ocr_review_composition import (
    QualityOcrReviewWorkerCompositionConfig as QualityOcrReviewWorkerCompositionConfig,
)
from paperworkshell.worker.quality_ocr_review_composition import (
    create_quality_ocr_review_worker_composition as create_quality_ocr_review_worker_composition,
)
from paperworkshell.worker.quality_ocr_review_composition import (
    create_quality_ocr_review_worker_composition_config as create_quality_ocr_review_worker_composition_config,
)
from paperworkshell.worker.searchable_pdf_export_composition import (
    SEARCHABLE_PDF_EXPORT_WORKER_MAXIMUM_PROCESSES as SEARCHABLE_PDF_EXPORT_WORKER_MAXIMUM_PROCESSES,
)
from paperworkshell.worker.searchable_pdf_export_composition import (
    SearchablePdfExportWorkerComposition as SearchablePdfExportWorkerComposition,
)
from paperworkshell.worker.searchable_pdf_export_composition import (
    SearchablePdfExportWorkerCompositionConfig as SearchablePdfExportWorkerCompositionConfig,
)
from paperworkshell.worker.searchable_pdf_export_composition import (
    create_searchable_pdf_export_worker_composition as create_searchable_pdf_export_worker_composition,
)
from paperworkshell.worker.searchable_pdf_export_composition import (
    create_searchable_pdf_export_worker_composition_config as create_searchable_pdf_export_worker_composition_config,
)
from paperworkshell.worker.signature_image_raster_broker import (
    BrokeredSignatureImageRasterizer,
    CommittedSignatureImageAssetStore,
    SignatureImageRasterBrokerPolicy,
)

_REQUIRED_DESKTOP_CAPABILITIES = (
    IMAGE_NORMALIZE_CAPABILITY,
    IMAGE_TRANSFORM_CAPABILITY,
    PDF_INSPECT_CAPABILITY,
    PDF_THUMBNAIL_CAPABILITY,
    SIGNATURE_IMAGE_RASTER_CAPABILITY,
)
_REQUIRED_PDF_EXPORT_CAPABILITIES = (PDF_EXPORT_CAPABILITY,)

# The desktop composition root may import this GUI-safe client module, but it
# must not import the worker-host coordinator module directly.  Keep creation
# and the public type alias at this established seam.
DesktopHeavyOperationCoordinator = HeavyOperationCoordinator


def _signature_image_raster_limits(image_limits: ResourceLimits) -> ResourceLimits:
    """Preserve raw-pixel capacity while budgeting the signature artifact header."""

    return replace(
        image_limits,
        output_bytes=(
            image_limits.output_bytes
            + max(
                0,
                SIGNATURE_IMAGE_RASTER_ARTIFACT_HEADER_BYTES - IMAGE_PAGE_ARTIFACT_HEADER_BYTES,
            )
        ),
    )


def create_desktop_heavy_operation_coordinator(
    maximum_concurrency: int,
) -> DesktopHeavyOperationCoordinator:
    """Create the app-scoped heavy-work gate behind the desktop seam."""

    return HeavyOperationCoordinator(maximum_concurrency)


@dataclass(frozen=True, slots=True, repr=False)
class DesktopWorkerCompositionConfig:
    """Validated fixed resources and policies for the shared desktop worker client."""

    python_executable: Path = field(repr=False)
    bootstrap_script: Path = field(repr=False)
    runtime_roots: tuple[Path, ...] = field(repr=False)
    code_roots: tuple[Path, ...] = field(repr=False)
    working_directory: Path = field(repr=False)
    resource_policy: WorkerResourcePolicy
    timeout_policy: WorkerTimeoutPolicy
    maximum_heavy_operation_concurrency: int
    inspection_policy: PdfInspectionBrokerPolicy
    image_policy: ImageNormalizeBrokerPolicy
    image_transform_policy: ImageTransformBrokerPolicy
    thumbnail_policy: PdfThumbnailBrokerPolicy
    venv_launcher: Path | None = field(default=None, repr=False)
    _launch_config: WindowsLaunchConfig = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            type(self.runtime_roots) is not tuple
            or type(self.code_roots) is not tuple
            or type(self.resource_policy) is not WorkerResourcePolicy
            or type(self.timeout_policy) is not WorkerTimeoutPolicy
            or type(self.maximum_heavy_operation_concurrency) is not int
            or not 1
            <= self.maximum_heavy_operation_concurrency
            <= self.resource_policy.maximum_processes
            or type(self.inspection_policy) is not PdfInspectionBrokerPolicy
            or type(self.image_policy) is not ImageNormalizeBrokerPolicy
            or type(self.image_transform_policy) is not ImageTransformBrokerPolicy
            or type(self.thumbnail_policy) is not PdfThumbnailBrokerPolicy
        ):
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.CONFIGURATION,
            )
        launch_config = WindowsLaunchConfig(
            python_executable=self.python_executable,
            bootstrap_script=self.bootstrap_script,
            runtime_roots=self.runtime_roots,
            code_roots=self.code_roots,
            working_directory=self.working_directory,
            venv_launcher=self.venv_launcher,
        )
        object.__setattr__(self, "_launch_config", launch_config)

    def __repr__(self) -> str:
        return "DesktopWorkerCompositionConfig(<validated fixed resources>)"


@dataclass(frozen=True, slots=True, repr=False)
class PdfExportWorkerCompositionConfig:
    """Validated resources for one separately privileged export worker."""

    python_executable: Path = field(repr=False)
    bootstrap_script: Path = field(repr=False)
    runtime_roots: tuple[Path, ...] = field(repr=False)
    code_roots: tuple[Path, ...] = field(repr=False)
    working_directory: Path = field(repr=False)
    qpdf_executable: Path = field(repr=False)
    resource_policy: WorkerResourcePolicy
    timeout_policy: WorkerTimeoutPolicy
    maximum_heavy_operation_concurrency: int
    export_policy: PdfExportBrokerPolicy
    venv_launcher: Path | None = field(default=None, repr=False)
    _launch_config: WindowsLaunchConfig = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            type(self.runtime_roots) is not tuple
            or type(self.code_roots) is not tuple
            or type(self.resource_policy) is not WorkerResourcePolicy
            or type(self.timeout_policy) is not WorkerTimeoutPolicy
            or type(self.maximum_heavy_operation_concurrency) is not int
            or not 1
            <= self.maximum_heavy_operation_concurrency
            <= self.resource_policy.maximum_processes
            or type(self.export_policy) is not PdfExportBrokerPolicy
        ):
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.CONFIGURATION,
            )
        launch_config = WindowsLaunchConfig(
            python_executable=self.python_executable,
            bootstrap_script=self.bootstrap_script,
            runtime_roots=self.runtime_roots,
            code_roots=self.code_roots,
            working_directory=self.working_directory,
            venv_launcher=self.venv_launcher,
            qpdf_executable=self.qpdf_executable,
        )
        object.__setattr__(self, "_launch_config", launch_config)

    def __repr__(self) -> str:
        return "PdfExportWorkerCompositionConfig(<validated fixed resources>)"


class DesktopWorkerComposition(AbstractContextManager["DesktopWorkerComposition"]):
    """Own one shared worker client and its structural inspection/rendering ports."""

    __slots__ = (
        "_closed",
        "_heavy_operations",
        "_image_page_generator",
        "_image_source_inspector",
        "_image_page_transformer",
        "_signature_image_rasterizer",
        "_owner_thread_id",
        "_source_inspector",
        "_thumbnail_generator",
        "_worker",
    )

    def __init__(
        self,
        worker: WorkerClient,
        source_inspector: BrokeredPdfSourceInspector,
        thumbnail_generator: BrokeredPdfThumbnailGenerator,
        image_source_inspector: BrokeredImageSourceInspector,
        image_page_generator: BrokeredImagePageGenerator,
        image_page_transformer: BrokeredImagePageTransformer,
        signature_image_rasterizer: BrokeredSignatureImageRasterizer,
        heavy_operations: HeavyOperationCoordinator,
    ) -> None:
        self._worker = worker
        self._source_inspector = source_inspector
        self._thumbnail_generator = thumbnail_generator
        self._image_source_inspector = image_source_inspector
        self._image_page_generator = image_page_generator
        self._image_page_transformer = image_page_transformer
        self._signature_image_rasterizer = signature_image_rasterizer
        self._heavy_operations = heavy_operations
        self._owner_thread_id = threading.get_ident()
        self._closed = False

    @property
    def source_inspector(self) -> BrokeredPdfSourceInspector:
        """Expose the source-inspection application port without launcher details."""

        return self._source_inspector

    @property
    def thumbnail_generator(self) -> BrokeredPdfThumbnailGenerator:
        """Expose the page-thumbnail application port without launcher details."""

        return self._thumbnail_generator

    @property
    def image_source_inspector(self) -> BrokeredImageSourceInspector:
        """Expose worker-decoded JPEG/PNG inspection without launcher details."""

        return self._image_source_inspector

    @property
    def image_page_generator(self) -> BrokeredImagePageGenerator:
        """Expose bounded committed-image preview generation."""

        return self._image_page_generator

    @property
    def image_page_transformer(self) -> BrokeredImagePageTransformer:
        """Expose bounded canonical transforms over committed image pages."""

        return self._image_page_transformer

    @property
    def signature_image_rasterizer(self) -> BrokeredSignatureImageRasterizer:
        """Expose authenticated straight-alpha signature-image preparation."""

        return self._signature_image_rasterizer

    def resource_snapshot(self) -> WorkerResourceSnapshot | None:
        """Forward safe worker accounting without exposing process authority."""

        self._require_owner()
        return self._worker.resource_snapshot()

    def last_cancellation_acknowledgement_ms(self) -> int | None:
        """Return the last protocol acknowledgement latency, excluding teardown."""

        self._require_owner()
        return self._worker.query().last_cancellation_acknowledgement_ms

    def heavy_operation_snapshot(self) -> HeavyOperationSnapshot:
        """Expose shared content-free backpressure accounting."""

        return self._heavy_operations.snapshot()

    @property
    def heavy_operations(self) -> HeavyOperationCoordinator:
        """Share the application-wide gate with a separate export composition."""

        return self._heavy_operations

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        """Idempotently terminate the shared client on its construction thread."""

        self._require_owner()
        if self._closed:
            return
        pending: BaseException | None = None
        try:
            self._worker.terminate()
        except BaseException as error:
            pending = error
        for drain in (
            drain_windows_bounded_output_cleanup,
            drain_windows_result_pipe_cleanup,
        ):
            try:
                drain()
            except BaseException as error:
                if pending is None:
                    pending = error
        if pending is not None:
            raise pending
        self._closed = True

    def __enter__(self) -> Self:
        self._require_owner()
        if self._closed:
            reject_client(
                WorkerClientErrorCode.STATE_INVALID,
                WorkerClientContext.STATE,
            )
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"DesktopWorkerComposition(closed={self._closed})"

    def _require_owner(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            reject_client(
                WorkerClientErrorCode.OWNER_THREAD_REQUIRED,
                WorkerClientContext.OWNER,
            )


class PdfExportWorkerComposition(AbstractContextManager["PdfExportWorkerComposition"]):
    """Own one export-only worker client and its handle-only broker port."""

    __slots__ = (
        "_closed",
        "_heavy_operations",
        "_owner_thread_id",
        "_pdf_exporter",
        "_worker",
    )

    def __init__(
        self,
        worker: WorkerClient,
        pdf_exporter: BrokeredPdfExporter,
        heavy_operations: HeavyOperationCoordinator,
    ) -> None:
        self._worker = worker
        self._pdf_exporter = pdf_exporter
        self._heavy_operations = heavy_operations
        self._owner_thread_id = threading.get_ident()
        self._closed = False

    @property
    def pdf_exporter(self) -> BrokeredPdfExporter:
        """Expose the verified export application port without launcher details."""

        return self._pdf_exporter

    def resource_snapshot(self) -> WorkerResourceSnapshot | None:
        """Forward safe export-worker accounting without launcher authority."""

        self._require_owner()
        return self._worker.resource_snapshot()

    def last_cancellation_acknowledgement_ms(self) -> int | None:
        """Return the last protocol acknowledgement latency, excluding teardown."""

        self._require_owner()
        return self._worker.query().last_cancellation_acknowledgement_ms

    def heavy_operation_snapshot(self) -> HeavyOperationSnapshot:
        """Expose shared content-free backpressure accounting."""

        return self._heavy_operations.snapshot()

    @property
    def heavy_operations(self) -> HeavyOperationCoordinator:
        """Expose only the app-scoped gate identity used by this composition."""

        return self._heavy_operations

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        """Idempotently terminate the export client on its construction thread."""

        self._require_owner()
        if self._closed:
            return
        pending: BaseException | None = None
        try:
            self._pdf_exporter._revoke_permanently()
        except BaseException as error:
            pending = error
        try:
            self._worker.terminate()
        except BaseException as error:
            if pending is None:
                pending = error
        for drain in (
            drain_windows_bounded_output_cleanup,
            drain_windows_result_pipe_cleanup,
        ):
            try:
                drain()
            except BaseException as error:
                if pending is None:
                    pending = error
        if pending is not None:
            raise pending
        self._closed = True

    def __enter__(self) -> Self:
        self._require_owner()
        if self._closed or self._pdf_exporter.revoked:
            reject_client(
                WorkerClientErrorCode.STATE_INVALID,
                WorkerClientContext.STATE,
            )
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"PdfExportWorkerComposition(closed={self._closed})"

    def _require_owner(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            reject_client(
                WorkerClientErrorCode.OWNER_THREAD_REQUIRED,
                WorkerClientContext.OWNER,
            )


def create_desktop_worker_composition(
    config: DesktopWorkerCompositionConfig,
    *,
    prepared_sources: PreparedSourceHandleBroker,
    committed_pages: CommittedPdfPageStore,
    committed_signature_images: CommittedSignatureImageAssetStore,
    committed_image_pages: CommittedImagePageStore | None = None,
    thumbnail_cache: ThumbnailCacheStore,
    heavy_operations: HeavyOperationCoordinator,
) -> DesktopWorkerComposition:
    """Build the fixed launcher, shared client, and both desktop broker ports."""

    if type(config) is not DesktopWorkerCompositionConfig:
        reject_client(
            WorkerClientErrorCode.CONFIGURATION_INVALID,
            WorkerClientContext.CONFIGURATION,
        )
    _require_heavy_operations(heavy_operations, config.maximum_heavy_operation_concurrency)
    _require_port(prepared_sources, ("borrow_prepared_source",))
    _require_port(committed_pages, ("borrow_committed_pdf_page",))
    selected_image_pages = (
        cast(CommittedImagePageStore, committed_pages)
        if committed_image_pages is None
        else committed_image_pages
    )
    _require_port(selected_image_pages, ("borrow_committed_image_page",))
    _require_port(committed_signature_images, ("borrow_committed_signature_image",))
    _require_port(thumbnail_cache, ("lookup", "create_stage"))

    launcher = WindowsProcessLauncher(config._launch_config)
    worker = WorkerClient(
        launcher,
        config.resource_policy,
        config.timeout_policy,
        required_capabilities=_REQUIRED_DESKTOP_CAPABILITIES,
    )
    inspector = BrokeredPdfSourceInspector(
        worker,
        prepared_sources,
        config.inspection_policy,
        heavy_operations=heavy_operations,
    )
    generator = BrokeredPdfThumbnailGenerator(
        worker,
        committed_pages,
        thumbnail_cache,
        config.thumbnail_policy,
        heavy_operations=heavy_operations,
    )
    image_inspector, image_generator = create_image_page_brokers(
        worker,
        prepared_sources,
        selected_image_pages,
        config.image_policy,
        heavy_operations=heavy_operations,
    )
    image_transformer = BrokeredImagePageTransformer(
        worker,
        selected_image_pages,
        config.image_transform_policy,
        heavy_operations=heavy_operations,
    )
    signature_image_rasterizer = BrokeredSignatureImageRasterizer(
        worker,
        committed_signature_images,
        SignatureImageRasterBrokerPolicy(
            _signature_image_raster_limits(config.image_policy.limits)
        ),
        heavy_operations=heavy_operations,
    )
    return DesktopWorkerComposition(
        worker,
        inspector,
        generator,
        image_inspector,
        image_generator,
        image_transformer,
        signature_image_rasterizer,
        heavy_operations,
    )


def create_desktop_worker_composition_config(
    *,
    python_executable: Path,
    bootstrap_script: Path,
    runtime_roots: tuple[Path, ...],
    code_roots: tuple[Path, ...],
    working_directory: Path,
    resource_policy: WorkerResourcePolicy,
    timeout_policy: WorkerTimeoutPolicy,
    maximum_heavy_operation_concurrency: int,
    inspection_limits: ResourceLimits,
    image_limits: ResourceLimits,
    image_transform_limits: ResourceLimits,
    thumbnail_limits: ResourceLimits,
    venv_launcher: Path | None = None,
) -> DesktopWorkerCompositionConfig:
    """Build broker policies behind the sole desktop-worker composition boundary."""

    return DesktopWorkerCompositionConfig(
        python_executable=python_executable,
        bootstrap_script=bootstrap_script,
        runtime_roots=runtime_roots,
        code_roots=code_roots,
        working_directory=working_directory,
        resource_policy=resource_policy,
        timeout_policy=timeout_policy,
        maximum_heavy_operation_concurrency=maximum_heavy_operation_concurrency,
        inspection_policy=PdfInspectionBrokerPolicy(inspection_limits),
        image_policy=ImageNormalizeBrokerPolicy(image_limits),
        image_transform_policy=ImageTransformBrokerPolicy(image_transform_limits),
        thumbnail_policy=PdfThumbnailBrokerPolicy(thumbnail_limits),
        venv_launcher=venv_launcher,
    )


def create_pdf_export_worker_composition(
    config: PdfExportWorkerCompositionConfig,
    *,
    heavy_operations: HeavyOperationCoordinator,
) -> PdfExportWorkerComposition:
    """Build a separate export-only client with explicit qpdf authority."""

    if type(config) is not PdfExportWorkerCompositionConfig:
        reject_client(
            WorkerClientErrorCode.CONFIGURATION_INVALID,
            WorkerClientContext.CONFIGURATION,
        )
    _require_heavy_operations(heavy_operations, config.maximum_heavy_operation_concurrency)
    launcher = WindowsProcessLauncher(config._launch_config)
    worker = WorkerClient(
        launcher,
        config.resource_policy,
        config.timeout_policy,
        required_capabilities=_REQUIRED_PDF_EXPORT_CAPABILITIES,
    )
    return PdfExportWorkerComposition(
        worker,
        BrokeredPdfExporter(
            worker,
            config.export_policy,
            heavy_operations=heavy_operations,
        ),
        heavy_operations,
    )


def create_pdf_export_worker_composition_config(
    *,
    python_executable: Path,
    bootstrap_script: Path,
    runtime_roots: tuple[Path, ...],
    code_roots: tuple[Path, ...],
    working_directory: Path,
    qpdf_executable: Path,
    resource_policy: WorkerResourcePolicy,
    timeout_policy: WorkerTimeoutPolicy,
    maximum_heavy_operation_concurrency: int,
    export_limits: ResourceLimits,
    pump_interval_seconds: float = 0.050,
    venv_launcher: Path | None = None,
) -> PdfExportWorkerCompositionConfig:
    """Build the export broker policy behind its least-privilege composition."""

    return PdfExportWorkerCompositionConfig(
        python_executable=python_executable,
        bootstrap_script=bootstrap_script,
        runtime_roots=runtime_roots,
        code_roots=code_roots,
        working_directory=working_directory,
        qpdf_executable=qpdf_executable,
        resource_policy=resource_policy,
        timeout_policy=timeout_policy,
        maximum_heavy_operation_concurrency=maximum_heavy_operation_concurrency,
        export_policy=PdfExportBrokerPolicy(
            export_limits,
            pump_interval_seconds=pump_interval_seconds,
        ),
        venv_launcher=venv_launcher,
    )


def _require_port(value: object, methods: tuple[str, ...]) -> None:
    try:
        valid = all(callable(getattr(value, method, None)) for method in methods)
    except BaseException:
        valid = False
    if not valid:
        reject_client(
            WorkerClientErrorCode.CONFIGURATION_INVALID,
            WorkerClientContext.CONFIGURATION,
        )


def _require_heavy_operations(value: object, expected_maximum: int) -> None:
    if (
        type(value) is not HeavyOperationCoordinator
        or value.snapshot().maximum_concurrency != expected_maximum
    ):
        reject_client(
            WorkerClientErrorCode.CONFIGURATION_INVALID,
            WorkerClientContext.CONFIGURATION,
        )

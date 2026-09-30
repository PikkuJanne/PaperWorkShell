"""PaperWorkShell desktop composition entrypoint."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from paperworkshell.app.case_lifecycle import CaseLifecycleService
from paperworkshell.app.checklist import ChecklistWorkflowService
from paperworkshell.app.export_history import (
    PdfExportHistoryService,
    PdfExportRetryRecoveryService,
)
from paperworkshell.app.flattened_pdf_export import (
    FlattenedPdfExportProgressSink,
    FlattenedPdfExportService,
    FlattenedPdfRasterizer,
    VerifiedFlattenedPdfCandidate,
)
from paperworkshell.app.ocr import CommittedSourcePageStore, OcrService
from paperworkshell.app.ocr_languages import (
    SUPPORTED_OCR_LANGUAGE_CODES,
    OcrLanguageDiscoveryResult,
    OcrLanguageManager,
    OcrLanguageRuntimeStatus,
    unavailable_ocr_language_manager,
)
from paperworkshell.app.organizer import OrganizerBackend
from paperworkshell.app.package_pdf_export import (
    PackagePdfCandidateErrorCode,
    PackagePdfCandidateProducer,
    PackagePdfSourceStore,
    reject_package_pdf_candidate,
)
from paperworkshell.app.package_workflow import PackageWorkflowService
from paperworkshell.app.page_previews import PagePreviewService
from paperworkshell.app.pdf_export import PdfExportProgressSink, PdfExportService
from paperworkshell.app.portal_constraints import PortalConstraintWorkflowService
from paperworkshell.app.quality_analysis import QualityAnalysisService
from paperworkshell.app.quality_ocr_review import (
    CancellationProbe as QualityOcrCancellationProbe,
)
from paperworkshell.app.quality_ocr_review import DeadlineProbe as QualityOcrDeadlineProbe
from paperworkshell.app.quality_ocr_review import QualityOcrReviewService
from paperworkshell.app.recovery import (
    StartupRecoveryDiagnosticSink,
    StartupRecoveryService,
)
from paperworkshell.app.searchable_pdf_export import (
    SearchablePdfExportProgressSink,
    SearchablePdfExportService,
    VerifiedSearchablePdfCandidate,
)
from paperworkshell.app.signature_images import SignatureImageWorkflowCoordinator
from paperworkshell.app.source_ingest import (
    CancellationProbe,
    CommittedPdfSourceBatchLease,
    CommittedPdfSourceBatchStore,
    CommittedSearchablePdfSourceBatchLease,
    CommittedSearchablePdfSourceBatchStore,
    DeadlineProbe,
    SourceIngestService,
)
from paperworkshell.app.source_inspection import SourceInspectorRouter
from paperworkshell.domain.image_pages import IMAGE_PAGE_ARTIFACT_HEADER_BYTES
from paperworkshell.domain.image_transforms import IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES
from paperworkshell.domain.import_precheck import ImportLimits
from paperworkshell.domain.ocr import OcrError, OcrErrorCode, reject_ocr
from paperworkshell.domain.pdf_export import (
    DEFAULT_PDF_EXPORT_PROFILE,
    IMAGE_PDF_EXPORT_PROFILE,
    PdfExportErrorCode,
    PdfExportOutcome,
    PdfExportProfile,
    reject_pdf_export,
)
from paperworkshell.domain.pdf_inspection import MAX_PDF_INSPECTION_FRAME_BYTES
from paperworkshell.domain.quality_ocr_review import (
    MAX_QUALITY_OCR_REVIEW_PIXELS,
    MAX_QUALITY_OCR_REVIEW_TSV_BYTES,
    QualityOcrReviewErrorCode,
    QualityOcrReviewReport,
    QualityOcrReviewRequest,
    reject_quality_ocr_review,
)
from paperworkshell.domain.recovery import RecoveryErrorCode, reject_recovery
from paperworkshell.domain.resource_policy import ResourcePolicy, select_resource_policy
from paperworkshell.domain.searchable_pdf_export import (
    SearchablePdfExportErrorCode,
    SearchablePdfExportPlan,
    reject_searchable_pdf_export,
)
from paperworkshell.export.package_service import WindowsPackageExportService
from paperworkshell.export.package_summary import build_package_summary
from paperworkshell.export.pdf_destination import WindowsVerifiedPdfDestination
from paperworkshell.overlays.exporting import (
    FlattenedPdfExportErrorCode,
    FlattenedPdfExportPlan,
    reject_flattened_pdf_export,
)
from paperworkshell.overlays.signature_rasters import SignatureImageOverlayRasterSource
from paperworkshell.storage._windows import MAXIMUM_STREAM_CHUNK_BYTES
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.export_destination import WindowsExportDestination
from paperworkshell.storage.export_history import WindowsPdfExportHistoryRepository
from paperworkshell.storage.export_retry import WindowsPdfExportRetryRepository
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.recovery import WindowsStartupRecoveryStore
from paperworkshell.storage.signature_images import WindowsSignatureImageAssetStore
from paperworkshell.storage.source_ingest import WindowsSourceIngestStore
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.thumbnail_cache import WindowsThumbnailCacheStore
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.templates.catalog import (
    GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
    built_in_template_repository,
)
from paperworkshell.templates.loader import SafeTemplateLoader
from paperworkshell.ui.application import run_desktop_application
from paperworkshell.worker.client.models import WorkerTimeoutPolicy
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.client.tesseract_runtime import (
    TesseractRuntimeAdmissionError,
    admit_packaged_tesseract_runtime,
    tesseract_runtime_authority_id,
)
from paperworkshell.worker.desktop_composition import (
    FLATTENED_PDF_EXPORT_WORKER_MAXIMUM_PROCESSES,
    OCR_WORKER_MAXIMUM_PROCESSES,
    QUALITY_OCR_REVIEW_WORKER_MAXIMUM_PROCESSES,
    SEARCHABLE_PDF_EXPORT_WORKER_MAXIMUM_PROCESSES,
    DesktopHeavyOperationCoordinator,
    DesktopWorkerCompositionConfig,
    FlattenedPdfExportWorkerComposition,
    FlattenedPdfExportWorkerCompositionConfig,
    OcrWorkerComposition,
    OcrWorkerCompositionConfig,
    PdfExportWorkerComposition,
    PdfExportWorkerCompositionConfig,
    QualityOcrReviewWorkerComposition,
    QualityOcrReviewWorkerCompositionConfig,
    SearchablePdfExportWorkerComposition,
    SearchablePdfExportWorkerCompositionConfig,
    create_desktop_heavy_operation_coordinator,
    create_desktop_worker_composition,
    create_desktop_worker_composition_config,
    create_flattened_pdf_export_worker_composition,
    create_flattened_pdf_export_worker_composition_config,
    create_ocr_worker_composition,
    create_ocr_worker_composition_config,
    create_pdf_export_worker_composition,
    create_pdf_export_worker_composition_config,
    create_quality_ocr_review_worker_composition,
    create_quality_ocr_review_worker_composition_config,
    create_searchable_pdf_export_worker_composition,
    create_searchable_pdf_export_worker_composition_config,
)
from paperworkshell.worker.protocol.models import (
    MAX_FLATTENED_PDF_EXPORT_INPUT_REFERENCES,
    ResourceLimits,
)

_PDF_EXPORT_OUTPUT_BYTES = 512 * 1024 * 1024
_FLATTENED_PDF_OUTPUT_BYTES = 512 * 1024 * 1024
_OCR_OUTPUT_BYTES = 128 * 1024 * 1024
_SEARCHABLE_PDF_OUTPUT_BYTES = 128 * 1024 * 1024
_SEARCHABLE_PDF_STATUS_BYTES = 2 * 1024 * 1024
_QUALITY_OCR_REVIEW_TEMPORARY_BYTES = 4 * 1024 * 1024
_DEFAULT_PDF_EXPORT_QPDF_EXECUTABLE = Path(r"C:\Program Files\qpdf 12.3.2\bin\qpdf.exe")


@dataclass(frozen=True, slots=True)
class DesktopApplicationResourceContext:
    """App-scoped policy and heavy-work gate selected once at process startup."""

    resource_policy: ResourcePolicy
    heavy_operations: DesktopHeavyOperationCoordinator
    ocr_languages: OcrLanguageManager = field(
        default_factory=unavailable_ocr_language_manager,
        repr=False,
    )
    _ocr_executable: Path | None = field(default=None, repr=False)
    _ocr_tessdata_root: Path | None = field(default=None, repr=False)
    _ocr_runtime_authority_id: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.resource_policy) is not ResourcePolicy
            or type(self.heavy_operations) is not DesktopHeavyOperationCoordinator
            or self.heavy_operations.snapshot().maximum_concurrency
            != self.resource_policy.maximum_heavy_operation_concurrency
            or type(self.ocr_languages) is not OcrLanguageManager
            or (self._ocr_executable is not None and not isinstance(self._ocr_executable, Path))
            or (
                self._ocr_tessdata_root is not None
                and not isinstance(self._ocr_tessdata_root, Path)
            )
            or self.ocr_languages.runtime_authority_id != self._ocr_runtime_authority_id
        ):
            raise ValueError("application resource context is invalid")


@dataclass(frozen=True, slots=True, repr=False)
class _PackagedOcrLanguageDiscovery:
    """Translate exact local runtime admission into path-free UI facts."""

    executable: Path | None
    tessdata_root: Path | None

    def discover_ocr_languages(self) -> OcrLanguageDiscoveryResult:
        if self.executable is None and self.tessdata_root is None:
            return OcrLanguageDiscoveryResult(
                OcrLanguageRuntimeStatus.UNAVAILABLE,
                (),
            )
        if not isinstance(self.executable, Path) or not isinstance(self.tessdata_root, Path):
            return OcrLanguageDiscoveryResult(
                OcrLanguageRuntimeStatus.INVALID,
                (),
            )
        try:
            admission = admit_packaged_tesseract_runtime(
                self.executable,
                self.tessdata_root,
            )
        except TesseractRuntimeAdmissionError:
            return OcrLanguageDiscoveryResult(
                OcrLanguageRuntimeStatus.INVALID,
                (),
            )
        installed = frozenset(admission.installed_language_codes)
        return OcrLanguageDiscoveryResult(
            OcrLanguageRuntimeStatus.AVAILABLE,
            tuple(code for code in SUPPORTED_OCR_LANGUAGE_CODES if code in installed),
        )

    def __repr__(self) -> str:
        configured = self.executable is not None or self.tessdata_root is not None
        return f"_PackagedOcrLanguageDiscovery(configured={configured})"


def create_desktop_application_resource_context(
    resource_policy: ResourcePolicy | None = None,
    *,
    tesseract_executable: Path | None = None,
    tessdata_root: Path | None = None,
) -> DesktopApplicationResourceContext:
    """Select one policy and construct its sole app-scoped heavy-work gate."""

    selected_policy = _require_resource_policy(resource_policy)
    selected_executable, selected_tessdata_root = _configured_optional_ocr_resources(
        tesseract_executable,
        tessdata_root,
    )
    authority_id = (
        tesseract_runtime_authority_id(selected_executable, selected_tessdata_root)
        if selected_executable is not None and selected_tessdata_root is not None
        else None
    )
    selected_languages = OcrLanguageManager(
        _PackagedOcrLanguageDiscovery(selected_executable, selected_tessdata_root),
        runtime_authority_id=authority_id,
    )
    return DesktopApplicationResourceContext(
        selected_policy,
        create_desktop_heavy_operation_coordinator(
            selected_policy.maximum_heavy_operation_concurrency
        ),
        selected_languages,
        selected_executable,
        selected_tessdata_root,
        authority_id,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def build_windows_startup_recovery_service(
    manager: CaseWorkspaceManager,
    *,
    diagnostics: StartupRecoveryDiagnosticSink | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> StartupRecoveryService:
    """Compose complete Windows recovery at the desktop application root."""

    if not isinstance(manager, CaseWorkspaceManager):
        reject_recovery(RecoveryErrorCode.MODEL_INVALID, "recovery.composition")
    manifests = WindowsManifestRepository(manager)
    history = PdfExportHistoryService(
        WindowsPdfExportHistoryRepository(manager),
        manifests,
    )
    retries = WindowsPdfExportRetryRepository(manager)
    exports = PdfExportRetryRecoveryService(history, retries)
    store = WindowsStartupRecoveryStore(
        manager,
        settle_exports=exports.recover_case,
    )
    return StartupRecoveryService(
        store,
        diagnostics=diagnostics,
        clock=clock,
    )


def build_windows_pdf_export_service(
    manager: CaseWorkspaceManager,
    sources: CommittedPdfSourceBatchStore,
    composition: PdfExportWorkerComposition,
    *,
    resources: DesktopApplicationResourceContext,
    profile: PdfExportProfile = DEFAULT_PDF_EXPORT_PROFILE,
) -> PdfExportService:
    """Wire concrete Windows persistence around one export-only composition."""

    if (
        not isinstance(manager, CaseWorkspaceManager)
        or not isinstance(sources, CommittedPdfSourceBatchStore)
        or type(composition) is not PdfExportWorkerComposition
        or composition.closed
        or type(resources) is not DesktopApplicationResourceContext
        or composition.heavy_operations is not resources.heavy_operations
    ):
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.composition")
    selected_policy = resources.resource_policy
    manifests = WindowsManifestRepository(manager)
    return PdfExportService(
        manifests,
        sources,
        composition.pdf_exporter,
        WindowsVerifiedPdfDestination(
            WindowsExportDestination(
                minimum_free_space_reserve_bytes=(
                    selected_policy.minimum_export_free_space_reserve_bytes
                )
            )
        ),
        PdfExportHistoryService(
            WindowsPdfExportHistoryRepository(manager),
            manifests,
        ),
        WindowsPdfExportRetryRepository(manager),
        profile=profile,
    )


def build_windows_flattened_pdf_export_service(
    manager: CaseWorkspaceManager,
    sources: CommittedPdfSourceBatchStore,
    rasterizer: FlattenedPdfRasterizer,
    composition: FlattenedPdfExportWorkerComposition,
    *,
    resources: DesktopApplicationResourceContext,
) -> FlattenedPdfExportService:
    """Wire case assets and persistence around the flattened export worker."""

    if (
        not isinstance(manager, CaseWorkspaceManager)
        or not isinstance(sources, CommittedPdfSourceBatchStore)
        or not isinstance(rasterizer, FlattenedPdfRasterizer)
        or type(composition) is not FlattenedPdfExportWorkerComposition
        or composition.closed
        or type(resources) is not DesktopApplicationResourceContext
        or composition.heavy_operations is not resources.heavy_operations
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.MODEL_INVALID,
            "flattened.composition",
        )
    selected_policy = resources.resource_policy
    manifests = WindowsManifestRepository(manager)
    return FlattenedPdfExportService(
        manifests,
        sources,
        rasterizer,
        composition.exporter,
        WindowsVerifiedPdfDestination(
            WindowsExportDestination(
                minimum_free_space_reserve_bytes=(
                    selected_policy.minimum_export_free_space_reserve_bytes
                )
            )
        ),
        PdfExportHistoryService(
            WindowsPdfExportHistoryRepository(manager),
            manifests,
        ),
        WindowsPdfExportRetryRepository(manager),
        selected_policy,
    )


def build_windows_searchable_pdf_export_service(
    manager: CaseWorkspaceManager,
    sources: CommittedSearchablePdfSourceBatchStore,
    composition: SearchablePdfExportWorkerComposition,
    *,
    resources: DesktopApplicationResourceContext,
    ocr_languages: OcrLanguageManager | None = None,
) -> SearchablePdfExportService:
    """Wire mixed-source persistence around the combined OCR/export worker."""

    if (
        not isinstance(manager, CaseWorkspaceManager)
        or not isinstance(sources, CommittedSearchablePdfSourceBatchStore)
        or type(composition) is not SearchablePdfExportWorkerComposition
        or composition.closed
        or type(resources) is not DesktopApplicationResourceContext
        or composition.heavy_operations is not resources.heavy_operations
        or composition.ocr_runtime_authority_id != resources._ocr_runtime_authority_id
        or (ocr_languages is not None and ocr_languages is not resources.ocr_languages)
    ):
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.MODEL_INVALID,
            "searchable.composition",
        )
    selected_policy = resources.resource_policy
    manifests = WindowsManifestRepository(manager)
    return SearchablePdfExportService(
        manifests,
        sources,
        composition.exporter,
        WindowsVerifiedPdfDestination(
            WindowsExportDestination(
                minimum_free_space_reserve_bytes=(
                    selected_policy.minimum_export_free_space_reserve_bytes
                )
            )
        ),
        PdfExportHistoryService(
            WindowsPdfExportHistoryRepository(manager),
            manifests,
        ),
        WindowsPdfExportRetryRepository(manager),
        ocr_languages=resources.ocr_languages,
    )


class _LazyPackageFlattenedBroker:
    """Admit a PDF-only worker only when this package uses that pipeline."""

    def __init__(
        self,
        resources: DesktopApplicationResourceContext,
        stack: ExitStack,
    ) -> None:
        self._resources = resources
        self._stack = stack
        self._composition: FlattenedPdfExportWorkerComposition | None = None

    @property
    def maximum_output_bytes(self) -> int:
        return build_windows_flattened_pdf_export_limits(
            self._resources.resource_policy
        ).output_bytes

    @property
    def maximum_input_references(self) -> int:
        return MAX_FLATTENED_PDF_EXPORT_INPUT_REFERENCES

    def export(
        self,
        plan: FlattenedPdfExportPlan,
        sources: CommittedPdfSourceBatchLease,
        rasters: Mapping[UUID, SignatureImageOverlayRasterSource],
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: FlattenedPdfExportProgressSink,
    ) -> VerifiedFlattenedPdfCandidate:
        _require_package_runtime_active(deadline, cancellation)
        if self._composition is None:
            composition = build_windows_flattened_pdf_export_worker_composition(self._resources)
            self._stack.callback(composition.close)
            if (
                composition.closed
                or composition.heavy_operations is not self._resources.heavy_operations
            ):
                reject_package_pdf_candidate(
                    PackagePdfCandidateErrorCode.MODEL_INVALID, "package.runtime_composition"
                )
            self._composition = composition
        _require_package_runtime_active(deadline, cancellation)
        return self._composition.exporter.export(
            plan,
            sources,
            rasters,
            deadline=deadline,
            cancellation=cancellation,
            progress=progress,
        )


class _LazyPackageSearchableBroker:
    """Keep optional OCR admission out of startup and PDF-only exports."""

    def __init__(
        self,
        resources: DesktopApplicationResourceContext,
        stack: ExitStack,
    ) -> None:
        self._resources = resources
        self._stack = stack
        self._composition: SearchablePdfExportWorkerComposition | None = None

    @property
    def maximum_output_bytes(self) -> int:
        return build_windows_searchable_pdf_export_limits(
            self._resources.resource_policy
        ).output_bytes

    def export(
        self,
        plan: SearchablePdfExportPlan,
        sources: CommittedSearchablePdfSourceBatchLease,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: SearchablePdfExportProgressSink,
    ) -> VerifiedSearchablePdfCandidate:
        _require_package_runtime_active(deadline, cancellation)
        if self._composition is None:
            composition = build_windows_searchable_pdf_export_worker_composition(self._resources)
            self._stack.callback(composition.close)
            if (
                composition.closed
                or composition.heavy_operations is not self._resources.heavy_operations
                or composition.ocr_runtime_authority_id != self._resources._ocr_runtime_authority_id
            ):
                reject_package_pdf_candidate(
                    PackagePdfCandidateErrorCode.MODEL_INVALID, "package.runtime_composition"
                )
            self._composition = composition
        _require_package_runtime_active(deadline, cancellation)
        return self._composition.exporter.export(
            plan,
            sources,
            deadline=deadline,
            cancellation=cancellation,
            progress=progress,
        )


@contextmanager
def _package_pdf_producer_context(
    sources: PackagePdfSourceStore,
    rasterizer: FlattenedPdfRasterizer,
    resources: DesktopApplicationResourceContext,
) -> Iterator[PackagePdfCandidateProducer]:
    """Retain at most one composition per pipeline for one package attempt."""

    selected_resources = _require_application_resource_context(resources, required=True)
    with ExitStack() as stack:
        yield PackagePdfCandidateProducer(
            sources,
            rasterizer,
            _LazyPackageFlattenedBroker(selected_resources, stack),
            _LazyPackageSearchableBroker(selected_resources, stack),
            selected_resources.resource_policy,
            ocr_languages=selected_resources.ocr_languages,
        )


def _require_package_runtime_active(
    deadline: DeadlineProbe,
    cancellation: CancellationProbe,
) -> None:
    try:
        cancelled = cancellation()
        expired = deadline()
    except BaseException:
        reject_package_pdf_candidate(
            PackagePdfCandidateErrorCode.INTERNAL_ERROR, "package.runtime_control"
        )
    if type(cancelled) is not bool or type(expired) is not bool:
        reject_package_pdf_candidate(
            PackagePdfCandidateErrorCode.INTERNAL_ERROR, "package.runtime_control"
        )
    if cancelled:
        reject_package_pdf_candidate(
            PackagePdfCandidateErrorCode.CANCELLED, "package.runtime_cancelled"
        )
    if expired:
        reject_package_pdf_candidate(
            PackagePdfCandidateErrorCode.TIMED_OUT, "package.runtime_deadline"
        )


def build_windows_package_export_service(
    sources: PackagePdfSourceStore,
    rasterizer: FlattenedPdfRasterizer,
    *,
    resources: DesktopApplicationResourceContext,
) -> WindowsPackageExportService:
    """Bind package export and its companion summary to the same resource policy."""

    selected_resources = _require_application_resource_context(resources, required=True)
    if not isinstance(sources, PackagePdfSourceStore) or not isinstance(
        rasterizer, FlattenedPdfRasterizer
    ):
        reject_package_pdf_candidate(
            PackagePdfCandidateErrorCode.MODEL_INVALID, "package.runtime_service"
        )
    return WindowsPackageExportService(
        lambda: _package_pdf_producer_context(sources, rasterizer, selected_resources),
        WindowsExportDestination(
            minimum_free_space_reserve_bytes=(
                selected_resources.resource_policy.minimum_export_free_space_reserve_bytes
            )
        ),
        selected_resources.resource_policy,
    )


def build_backend(
    resources: DesktopApplicationResourceContext | None = None,
) -> OrganizerBackend:
    """Construct the complete backend on the caller's permanent owner thread."""

    selected_resources = _require_application_resource_context(resources)
    resource_policy = selected_resources.resource_policy
    manager = CaseWorkspaceManager.for_current_user(
        quota=_workspace_quota(resource_policy),
        minimum_free_space_reserve_bytes=(resource_policy.minimum_export_free_space_reserve_bytes),
    )
    return _build_backend(manager, resources=selected_resources)


class _BackendWorkerResources:
    """Idempotently close every worker composition owned by one backend."""

    __slots__ = ("_closed", "_desktop", "_quality_ocr", "_pdf_export")

    def __init__(
        self,
        desktop: Callable[[], None],
        quality_ocr: Callable[[], None] | None = None,
    ) -> None:
        if not callable(desktop) or (quality_ocr is not None and not callable(quality_ocr)):
            raise ValueError("backend worker resource closer is invalid")
        self._desktop = desktop
        self._quality_ocr = quality_ocr
        self._pdf_export: Callable[[], None] | None = None
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def add_quality_ocr(self, closer: Callable[[], None]) -> None:
        if self._closed or self._quality_ocr is not None or not callable(closer):
            raise ValueError("backend OCR review resource closer is invalid")
        self._quality_ocr = closer

    def close(self) -> None:
        if self._closed:
            return
        pending: BaseException | None = None
        if self._pdf_export is not None:
            try:
                self._pdf_export()
            except BaseException as error:
                pending = error
        quality_ocr = self._quality_ocr
        if quality_ocr is not None:
            try:
                quality_ocr()
            except BaseException as error:
                if pending is None:
                    pending = error
        try:
            self._desktop()
        except BaseException as error:
            if pending is None:
                pending = error
        if pending is not None:
            raise pending
        self._closed = True

    def add_pdf_export(self, closer: Callable[[], None]) -> None:
        if self._closed or self._pdf_export is not None or not callable(closer):
            raise ValueError("backend PDF export resource closer is invalid")
        self._pdf_export = closer


class _LazyPagesPdfExportService:
    """Admit the ordinary PDF export worker on the backend thread when first used."""

    def __init__(
        self,
        manager: CaseWorkspaceManager,
        sources: CommittedPdfSourceBatchStore,
        resources: DesktopApplicationResourceContext,
        worker_resources: _BackendWorkerResources,
    ) -> None:
        self._manager = manager
        self._sources = sources
        self._resources = resources
        self._worker_resources = worker_resources
        self._service: PdfExportService | None = None

    def export_pdf(
        self,
        case_id: UUID,
        destination: Path,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: PdfExportProgressSink,
    ) -> PdfExportOutcome:
        if self._worker_resources.closed:
            reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.runtime_closed")
        if cancellation():
            reject_pdf_export(PdfExportErrorCode.CANCELLED, "export.runtime_cancelled")
        if deadline():
            reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "export.runtime_deadline")
        if self._service is None:
            composition = build_windows_pdf_export_worker_composition(
                self._resources, profile=IMAGE_PDF_EXPORT_PROFILE
            )
            try:
                service = build_windows_pdf_export_service(
                    self._manager,
                    self._sources,
                    composition,
                    resources=self._resources,
                    profile=IMAGE_PDF_EXPORT_PROFILE,
                )
                self._worker_resources.add_pdf_export(composition.close)
            except BaseException:
                composition.close()
                raise
            self._service = service
        return self._service.export_pdf(
            case_id,
            destination,
            deadline=deadline,
            cancellation=cancellation,
            progress=progress,
        )


class _LazyQualityOcrReviewProcessor:
    """Admit optional OCR resources only inside an explicit quality review.

    The authenticated runtime inventory is fixed-work but has no elapsed-time
    guarantee.  Keeping it out of backend initialization prevents it from
    becoming an optional startup gate.  The backend owner thread creates and
    later closes the retained composition.
    """

    __slots__ = ("_composition", "_resources", "_unavailable", "_worker_resources")

    def __init__(
        self,
        resources: DesktopApplicationResourceContext,
        worker_resources: _BackendWorkerResources,
    ) -> None:
        if (
            type(resources) is not DesktopApplicationResourceContext
            or type(worker_resources) is not _BackendWorkerResources
        ):
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.MODEL_INVALID,
                "quality_ocr.lazy_processor",
            )
        self._resources = resources
        self._worker_resources = worker_resources
        self._composition: QualityOcrReviewWorkerComposition | None = None
        self._unavailable = False

    def review(
        self,
        request: QualityOcrReviewRequest,
        *,
        deadline: QualityOcrDeadlineProbe,
        cancellation: QualityOcrCancellationProbe,
    ) -> QualityOcrReviewReport:
        if (
            type(request) is not QualityOcrReviewRequest
            or not callable(deadline)
            or not callable(cancellation)
        ):
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.MODEL_INVALID,
                "quality_ocr.lazy_request",
            )
        _require_lazy_quality_ocr_active(deadline, cancellation)
        composition = self._composition
        if composition is None:
            if self._unavailable or self._worker_resources.closed:
                reject_quality_ocr_review(
                    QualityOcrReviewErrorCode.ENGINE_UNAVAILABLE,
                    "quality_ocr.lazy_unavailable",
                )
            try:
                created = _optional_quality_ocr_review_composition(self._resources)
            except OcrError as error:
                if type(error) is OcrError and error.code is OcrErrorCode.ENGINE_UNAVAILABLE:
                    self._unavailable = True
                    reject_quality_ocr_review(
                        QualityOcrReviewErrorCode.ENGINE_UNAVAILABLE,
                        "quality_ocr.lazy_admission",
                    )
                reject_quality_ocr_review(
                    QualityOcrReviewErrorCode.INTERNAL_ERROR,
                    "quality_ocr.lazy_admission",
                )
            if created is None:
                # Admission is expensive fixed-work. Latch this session closed
                # instead of retrying once per page; restarting establishes a
                # fresh runtime authority and permits a new admission attempt.
                self._unavailable = True
                reject_quality_ocr_review(
                    QualityOcrReviewErrorCode.ENGINE_UNAVAILABLE,
                    "quality_ocr.lazy_admission",
                )
            try:
                _require_lazy_quality_ocr_active(deadline, cancellation)
                self._worker_resources.add_quality_ocr(created.close)
            except BaseException:
                created.close()
                raise
            self._composition = created
            composition = created
        _require_lazy_quality_ocr_active(deadline, cancellation)
        return composition.reviewer.review(
            request,
            deadline=deadline,
            cancellation=cancellation,
        )

    def __repr__(self) -> str:
        return "_LazyQualityOcrReviewProcessor(<local optional runtime>)"


def _require_lazy_quality_ocr_active(
    deadline: QualityOcrDeadlineProbe,
    cancellation: QualityOcrCancellationProbe,
) -> None:
    try:
        cancelled = cancellation()
        expired = deadline()
    except BaseException:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.INTERNAL_ERROR,
            "quality_ocr.lazy_control",
        )
    if type(cancelled) is not bool or type(expired) is not bool:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.INTERNAL_ERROR,
            "quality_ocr.lazy_control",
        )
    if cancelled:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.CANCELLED,
            "quality_ocr.lazy_cancelled",
        )
    if expired:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.TIMED_OUT,
            "quality_ocr.lazy_deadline",
        )


def _build_backend(
    manager: CaseWorkspaceManager,
    *,
    resources: DesktopApplicationResourceContext | None = None,
    ocr_languages: OcrLanguageManager | None = None,
) -> OrganizerBackend:
    """Recover local cases before constructing document-worker resources."""

    startup_recovery = build_windows_startup_recovery_service(manager).recover_startup()
    selected_resources = _require_application_resource_context(resources)
    selected_ocr_languages = selected_resources.ocr_languages
    if ocr_languages is not None and ocr_languages is not selected_ocr_languages:
        reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.language_composition")
    selected_policy = selected_resources.resource_policy
    lifecycle = CaseLifecycleService(WindowsCaseLifecycleStore(manager))
    repository = WindowsManifestRepository(manager)
    source_store = WindowsSourceIngestStore(
        manager,
        WindowsSourcePrechecker(_import_limits(selected_policy)),
        maximum_page_count=selected_policy.maximum_page_count,
    )
    signature_image_store = WindowsSignatureImageAssetStore(
        manager,
        WindowsSourcePrechecker(_import_limits(selected_policy)),
    )
    thumbnail_cache = WindowsThumbnailCacheStore(manager)
    composition = create_desktop_worker_composition(
        _desktop_worker_config(selected_policy),
        prepared_sources=source_store,
        committed_inspection_sources=source_store,
        committed_pages=source_store,
        committed_signature_images=signature_image_store,
        thumbnail_cache=thumbnail_cache,
        heavy_operations=selected_resources.heavy_operations,
    )
    owned_resources = _BackendWorkerResources(composition.close)
    try:
        source_ingest = SourceIngestService(
            source_store,
            SourceInspectorRouter(
                composition.source_inspector,
                composition.image_source_inspector,
            ),
        )
        thumbnails = PagePreviewService(
            composition.thumbnail_generator,
            composition.image_page_generator,
            composition.image_page_transformer,
        )
        quality_ocr = QualityOcrReviewService(
            _LazyQualityOcrReviewProcessor(selected_resources, owned_resources),
            language_sets=selected_ocr_languages,
        )
        template_loader = SafeTemplateLoader(built_in_template_repository())
        template_as_of = datetime.now(UTC).date()
        return OrganizerBackend(
            lifecycle,
            repository,
            source_ingest,
            thumbnails,
            ocr_languages=selected_ocr_languages,
            quality_analysis=QualityAnalysisService(
                thumbnails,
                maximum_page_count=selected_policy.maximum_page_count,
                ocr_review=quality_ocr,
            ),
            close_resources=owned_resources.close,
            startup_recovery=startup_recovery,
            pages_pdf_export=_LazyPagesPdfExportService(
                manager, source_store, selected_resources, owned_resources
            ),
            signature_images=SignatureImageWorkflowCoordinator(
                signature_image_store,
                composition.signature_image_rasterizer,
            ),
            checklist=ChecklistWorkflowService(
                template_loader,
                GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
                as_of=template_as_of,
            ),
            portal_constraints=PortalConstraintWorkflowService(
                template_loader,
                GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
                as_of=template_as_of,
            ),
            package_workflow=PackageWorkflowService(
                repository,
                template_loader,
                GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
                as_of=template_as_of,
                exporter=build_windows_package_export_service(
                    source_store,
                    composition.signature_image_rasterizer,
                    resources=selected_resources,
                ),
                summary_builder=build_package_summary,
                source_inspector=composition.source_inspector,
            ),
        )
    except BaseException:
        owned_resources.close()
        raise


def _optional_quality_ocr_review_composition(
    resources: DesktopApplicationResourceContext,
) -> QualityOcrReviewWorkerComposition | None:
    """Admit the exact app runtime or leave a truthful failed-fact producer."""

    executable = resources._ocr_executable
    tessdata_root = resources._ocr_tessdata_root
    if executable is None or tessdata_root is None:
        return None
    availability = resources.ocr_languages.snapshot
    if availability.runtime_status is not OcrLanguageRuntimeStatus.AVAILABLE:
        return None
    composition = create_quality_ocr_review_worker_composition(
        _quality_ocr_review_worker_config(
            resources.resource_policy,
            executable,
            tessdata_root,
        ),
        heavy_operations=resources.heavy_operations,
    )
    if (
        composition.runtime_authority_id != resources._ocr_runtime_authority_id
        or composition.runtime_authority_id != resources.ocr_languages.runtime_authority_id
    ):
        composition.close()
        reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.quality_runtime_authority")
    return composition


def _quality_ocr_review_worker_config(
    resource_policy: ResourcePolicy,
    tesseract_executable: Path,
    tessdata_root: Path,
) -> QualityOcrReviewWorkerCompositionConfig:
    selected_policy = _require_resource_policy(resource_policy)
    (
        worker_executable,
        venv_launcher,
        bootstrap_script,
        runtime_roots,
        code_roots,
        working_directory,
    ) = _worker_runtime_resources()
    return create_quality_ocr_review_worker_composition_config(
        python_executable=worker_executable,
        venv_launcher=venv_launcher,
        bootstrap_script=bootstrap_script,
        runtime_roots=runtime_roots,
        code_roots=code_roots,
        working_directory=working_directory,
        tesseract_executable=tesseract_executable,
        tessdata_root=tessdata_root,
        resource_policy=WorkerResourcePolicy(
            process_memory_bytes=selected_policy.worker_process_memory_bytes,
            job_memory_bytes=selected_policy.worker_job_memory_bytes,
            cpu_time_ms=selected_policy.worker_cpu_time_ms,
            maximum_processes=QUALITY_OCR_REVIEW_WORKER_MAXIMUM_PROCESSES,
        ),
        timeout_policy=WorkerTimeoutPolicy(
            handshake_seconds=20.0,
            cancellation_grace_seconds=(selected_policy.cancellation_acknowledgement_ms / 1_000),
            shutdown_grace_seconds=10.0,
        ),
        maximum_heavy_operation_concurrency=(selected_policy.maximum_heavy_operation_concurrency),
        limits=ResourceLimits(
            wall_time_ms=60_000,
            cpu_time_ms=min(45_000, selected_policy.worker_cpu_time_ms),
            memory_bytes=selected_policy.worker_process_memory_bytes,
            temporary_storage_bytes=min(
                _QUALITY_OCR_REVIEW_TEMPORARY_BYTES,
                selected_policy.maximum_temporary_storage_bytes,
            ),
            output_bytes=MAX_QUALITY_OCR_REVIEW_TSV_BYTES,
            page_count=1,
            pixel_count=MAX_QUALITY_OCR_REVIEW_PIXELS,
        ),
    )


def _workspace_quota(resource_policy: ResourcePolicy | None = None) -> WorkspaceQuota:
    selected_policy = _require_resource_policy(resource_policy)
    return WorkspaceQuota(
        maximum_case_bytes=selected_policy.maximum_case_bytes,
        maximum_cache_bytes=selected_policy.maximum_cache_bytes,
        maximum_temp_bytes=selected_policy.maximum_temporary_storage_bytes,
        maximum_entries=50_000,
        maximum_depth=32,
    )


def _import_limits(resource_policy: ResourcePolicy | None = None) -> ImportLimits:
    selected_policy = _require_resource_policy(resource_policy)
    _import_io_chunk_bytes(selected_policy)
    return ImportLimits(
        maximum_source_bytes=selected_policy.maximum_source_bytes,
        maximum_pdf_pages=selected_policy.maximum_page_count,
        maximum_image_width=32_768,
        maximum_image_height=32_768,
        maximum_image_pixels=268_435_456,
        maximum_decompressed_bytes=1024 * 1024 * 1024,
        decompression_warning_ratio=100,
        maximum_decompression_ratio=2_000,
        maximum_header_bytes=65_536,
        maximum_structure_records=256,
    )


def _import_io_chunk_bytes(resource_policy: ResourcePolicy | None = None) -> int:
    """Prove the central import budget is the storage adapter's enforced chunk."""

    selected_policy = _require_resource_policy(resource_policy)
    if selected_policy.import_io_chunk_bytes != MAXIMUM_STREAM_CHUNK_BYTES:
        raise ValueError("resource policy does not match the bounded storage I/O adapter")
    return MAXIMUM_STREAM_CHUNK_BYTES


def build_windows_pdf_export_limits(
    resource_policy: ResourcePolicy | None = None,
    *,
    profile: PdfExportProfile = DEFAULT_PDF_EXPORT_PROFILE,
) -> ResourceLimits:
    """Return the fixed bounded production policy for one verified PDF export."""

    selected_policy = _require_resource_policy(resource_policy)
    output_bytes = min(
        _PDF_EXPORT_OUTPUT_BYTES,
        selected_policy.maximum_temporary_storage_bytes // 2,
    )
    return ResourceLimits(
        wall_time_ms=120_000,
        cpu_time_ms=90_000,
        memory_bytes=min(512 * 1024 * 1024, selected_policy.worker_process_memory_bytes),
        temporary_storage_bytes=selected_policy.maximum_temporary_storage_bytes,
        output_bytes=output_bytes,
        page_count=selected_policy.maximum_page_count,
        pixel_count=(
            min(268_435_456, selected_policy.worker_process_memory_bytes // 4)
            if profile == IMAGE_PDF_EXPORT_PROFILE
            else 1_048_576
        ),
    )


def build_windows_pdf_export_worker_config(
    qpdf_executable: Path | None = None,
    *,
    resource_policy: ResourcePolicy | None = None,
    profile: PdfExportProfile = DEFAULT_PDF_EXPORT_PROFILE,
) -> PdfExportWorkerCompositionConfig:
    """Select the trusted qpdf path and compose the export-only worker policy."""

    selected_policy = _require_resource_policy(resource_policy)
    selected_qpdf = _configured_qpdf_executable(qpdf_executable)
    (
        worker_executable,
        venv_launcher,
        bootstrap_script,
        runtime_roots,
        code_roots,
        working_directory,
    ) = _worker_runtime_resources()
    return create_pdf_export_worker_composition_config(
        python_executable=worker_executable,
        venv_launcher=venv_launcher,
        bootstrap_script=bootstrap_script,
        runtime_roots=runtime_roots,
        code_roots=code_roots,
        working_directory=working_directory,
        qpdf_executable=selected_qpdf,
        resource_policy=WorkerResourcePolicy(
            process_memory_bytes=selected_policy.worker_process_memory_bytes,
            job_memory_bytes=selected_policy.worker_job_memory_bytes,
            cpu_time_ms=selected_policy.worker_cpu_time_ms,
            maximum_processes=selected_policy.worker_maximum_processes,
        ),
        timeout_policy=WorkerTimeoutPolicy(
            handshake_seconds=20.0,
            cancellation_grace_seconds=(selected_policy.cancellation_acknowledgement_ms / 1_000),
            shutdown_grace_seconds=10.0,
        ),
        maximum_heavy_operation_concurrency=(selected_policy.maximum_heavy_operation_concurrency),
        export_limits=build_windows_pdf_export_limits(selected_policy, profile=profile),
        operation_version=profile.profile_version,
    )


def build_windows_pdf_export_worker_composition(
    resources: DesktopApplicationResourceContext,
    qpdf_executable: Path | None = None,
    *,
    profile: PdfExportProfile = DEFAULT_PDF_EXPORT_PROFILE,
) -> PdfExportWorkerComposition:
    """Compose export with the exact app-scoped policy and heavy-work gate."""

    selected_resources = _require_application_resource_context(resources, required=True)
    return create_pdf_export_worker_composition(
        build_windows_pdf_export_worker_config(
            qpdf_executable,
            resource_policy=selected_resources.resource_policy,
            profile=profile,
        ),
        heavy_operations=selected_resources.heavy_operations,
    )


def build_windows_flattened_pdf_export_limits(
    resource_policy: ResourcePolicy | None = None,
) -> ResourceLimits:
    """Return bounded source, raster, candidate, and verification budgets."""

    selected_policy = _require_resource_policy(resource_policy)
    output_bytes = min(
        _FLATTENED_PDF_OUTPUT_BYTES,
        selected_policy.maximum_temporary_storage_bytes // 2,
    )
    return ResourceLimits(
        wall_time_ms=120_000,
        cpu_time_ms=90_000,
        memory_bytes=min(512 * 1024 * 1024, selected_policy.worker_process_memory_bytes),
        temporary_storage_bytes=selected_policy.maximum_temporary_storage_bytes,
        output_bytes=output_bytes,
        page_count=selected_policy.maximum_page_count,
        pixel_count=selected_policy.maximum_preview_pixels_per_page,
    )


def build_windows_flattened_pdf_export_worker_config(
    qpdf_executable: Path | None = None,
    *,
    resource_policy: ResourcePolicy | None = None,
) -> FlattenedPdfExportWorkerCompositionConfig:
    """Compose flattened export with fixed qpdf and central resource policy."""

    selected_policy = _require_resource_policy(resource_policy)
    selected_qpdf = _configured_qpdf_executable(qpdf_executable)
    (
        worker_executable,
        venv_launcher,
        bootstrap_script,
        runtime_roots,
        code_roots,
        working_directory,
    ) = _worker_runtime_resources()
    return create_flattened_pdf_export_worker_composition_config(
        python_executable=worker_executable,
        venv_launcher=venv_launcher,
        bootstrap_script=bootstrap_script,
        runtime_roots=runtime_roots,
        code_roots=code_roots,
        working_directory=working_directory,
        qpdf_executable=selected_qpdf,
        resource_policy=WorkerResourcePolicy(
            process_memory_bytes=selected_policy.worker_process_memory_bytes,
            job_memory_bytes=selected_policy.worker_job_memory_bytes,
            cpu_time_ms=selected_policy.worker_cpu_time_ms,
            maximum_processes=FLATTENED_PDF_EXPORT_WORKER_MAXIMUM_PROCESSES,
        ),
        timeout_policy=WorkerTimeoutPolicy(
            handshake_seconds=20.0,
            cancellation_grace_seconds=(selected_policy.cancellation_acknowledgement_ms / 1_000),
            shutdown_grace_seconds=10.0,
        ),
        maximum_heavy_operation_concurrency=(selected_policy.maximum_heavy_operation_concurrency),
        resource_sampling_interval_ms=selected_policy.resource_sampling_interval_ms,
        pump_interval_seconds=selected_policy.resource_sampling_interval_ms / 1_000,
        limits=build_windows_flattened_pdf_export_limits(selected_policy),
    )


def build_windows_flattened_pdf_export_worker_composition(
    resources: DesktopApplicationResourceContext,
    qpdf_executable: Path | None = None,
) -> FlattenedPdfExportWorkerComposition:
    """Compose flattened export with the sole app-scoped heavy-work gate."""

    selected_resources = _require_application_resource_context(resources, required=True)
    return create_flattened_pdf_export_worker_composition(
        build_windows_flattened_pdf_export_worker_config(
            qpdf_executable,
            resource_policy=selected_resources.resource_policy,
        ),
        heavy_operations=selected_resources.heavy_operations,
    )


def build_windows_ocr_limits(
    resource_policy: ResourcePolicy | None = None,
) -> ResourceLimits:
    """Return the bounded production policy for one local OCR candidate."""

    selected_policy = _require_resource_policy(resource_policy)
    return ResourceLimits(
        wall_time_ms=300_000,
        cpu_time_ms=selected_policy.worker_cpu_time_ms,
        memory_bytes=selected_policy.worker_process_memory_bytes,
        temporary_storage_bytes=selected_policy.maximum_temporary_storage_bytes,
        output_bytes=min(
            _OCR_OUTPUT_BYTES,
            selected_policy.maximum_temporary_storage_bytes // 4,
        ),
        page_count=selected_policy.maximum_page_count,
        pixel_count=min(
            268_435_456,
            selected_policy.worker_process_memory_bytes // 4,
        ),
    )


def build_windows_ocr_worker_config(
    tesseract_executable: Path | None = None,
    tessdata_root: Path | None = None,
    *,
    resource_policy: ResourcePolicy | None = None,
) -> OcrWorkerCompositionConfig:
    """Compose the OCR-only worker from explicit admitted engine resources."""

    selected_policy = _require_resource_policy(resource_policy)
    selected_tesseract, selected_tessdata = _configured_ocr_resources(
        tesseract_executable,
        tessdata_root,
    )
    (
        worker_executable,
        venv_launcher,
        bootstrap_script,
        runtime_roots,
        code_roots,
        working_directory,
    ) = _worker_runtime_resources()
    return create_ocr_worker_composition_config(
        python_executable=worker_executable,
        venv_launcher=venv_launcher,
        bootstrap_script=bootstrap_script,
        runtime_roots=runtime_roots,
        code_roots=code_roots,
        working_directory=working_directory,
        tesseract_executable=selected_tesseract,
        tessdata_root=selected_tessdata,
        resource_policy=WorkerResourcePolicy(
            process_memory_bytes=selected_policy.worker_process_memory_bytes,
            job_memory_bytes=selected_policy.worker_job_memory_bytes,
            cpu_time_ms=selected_policy.worker_cpu_time_ms,
            maximum_processes=OCR_WORKER_MAXIMUM_PROCESSES,
        ),
        timeout_policy=WorkerTimeoutPolicy(
            handshake_seconds=20.0,
            cancellation_grace_seconds=(selected_policy.cancellation_acknowledgement_ms / 1_000),
            shutdown_grace_seconds=10.0,
        ),
        maximum_heavy_operation_concurrency=(selected_policy.maximum_heavy_operation_concurrency),
        limits=build_windows_ocr_limits(selected_policy),
    )


def build_windows_ocr_worker_composition(
    resources: DesktopApplicationResourceContext,
    tesseract_executable: Path | None = None,
    tessdata_root: Path | None = None,
) -> OcrWorkerComposition:
    """Compose OCR with the exact app-scoped policy and heavy-work gate."""

    selected_resources = _require_application_resource_context(resources, required=True)
    selected_executable, selected_tessdata_root = _resource_bound_ocr_resources(
        selected_resources,
        tesseract_executable,
        tessdata_root,
    )
    return create_ocr_worker_composition(
        build_windows_ocr_worker_config(
            selected_executable,
            selected_tessdata_root,
            resource_policy=selected_resources.resource_policy,
        ),
        heavy_operations=selected_resources.heavy_operations,
    )


def build_windows_ocr_service(
    sources: CommittedSourcePageStore,
    composition: OcrWorkerComposition,
    *,
    resources: DesktopApplicationResourceContext,
) -> OcrService:
    """Bind direct OCR to the same app-scoped language authority as its UI."""

    if (
        not isinstance(sources, CommittedSourcePageStore)
        or type(composition) is not OcrWorkerComposition
        or composition.closed
        or type(resources) is not DesktopApplicationResourceContext
        or composition.heavy_operations is not resources.heavy_operations
        or composition.ocr_runtime_authority_id != resources._ocr_runtime_authority_id
    ):
        reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.composition")
    return OcrService(
        sources,
        composition.ocr_processor,
        language_sets=resources.ocr_languages,
    )


def build_windows_searchable_pdf_export_limits(
    resource_policy: ResourcePolicy | None = None,
) -> ResourceLimits:
    """Return the constrained sequential OCR+assembly production policy."""

    selected_policy = _require_resource_policy(resource_policy)
    output_bytes = min(
        _SEARCHABLE_PDF_OUTPUT_BYTES,
        max(1, selected_policy.maximum_temporary_storage_bytes // 4),
    )
    return ResourceLimits(
        wall_time_ms=300_000,
        cpu_time_ms=selected_policy.worker_cpu_time_ms,
        memory_bytes=selected_policy.worker_process_memory_bytes,
        temporary_storage_bytes=selected_policy.maximum_temporary_storage_bytes,
        output_bytes=output_bytes,
        page_count=selected_policy.maximum_page_count,
        pixel_count=min(
            268_435_456,
            selected_policy.worker_process_memory_bytes // 4,
        ),
    )


def build_windows_searchable_pdf_export_worker_config(
    qpdf_executable: Path | None = None,
    tesseract_executable: Path | None = None,
    tessdata_root: Path | None = None,
    *,
    resource_policy: ResourcePolicy | None = None,
) -> SearchablePdfExportWorkerCompositionConfig:
    """Compose the one-shot worker with both fixed executable authorities."""

    selected_policy = _require_resource_policy(resource_policy)
    selected_qpdf = _configured_qpdf_executable(qpdf_executable)
    selected_tesseract, selected_tessdata = _configured_ocr_resources(
        tesseract_executable,
        tessdata_root,
    )
    (
        worker_executable,
        venv_launcher,
        bootstrap_script,
        runtime_roots,
        code_roots,
        working_directory,
    ) = _worker_runtime_resources()
    return create_searchable_pdf_export_worker_composition_config(
        python_executable=worker_executable,
        venv_launcher=venv_launcher,
        bootstrap_script=bootstrap_script,
        runtime_roots=runtime_roots,
        code_roots=code_roots,
        working_directory=working_directory,
        qpdf_executable=selected_qpdf,
        tesseract_executable=selected_tesseract,
        tessdata_root=selected_tessdata,
        resource_policy=WorkerResourcePolicy(
            process_memory_bytes=selected_policy.worker_process_memory_bytes,
            job_memory_bytes=selected_policy.worker_job_memory_bytes,
            cpu_time_ms=selected_policy.worker_cpu_time_ms,
            maximum_processes=SEARCHABLE_PDF_EXPORT_WORKER_MAXIMUM_PROCESSES,
        ),
        timeout_policy=WorkerTimeoutPolicy(
            handshake_seconds=20.0,
            cancellation_grace_seconds=(selected_policy.cancellation_acknowledgement_ms / 1_000),
            shutdown_grace_seconds=10.0,
        ),
        maximum_heavy_operation_concurrency=(selected_policy.maximum_heavy_operation_concurrency),
        maximum_active_full_resolution_page_buffers=(
            selected_policy.maximum_active_full_resolution_page_buffers
        ),
        resource_sampling_interval_ms=selected_policy.resource_sampling_interval_ms,
        pump_interval_seconds=selected_policy.resource_sampling_interval_ms / 1_000,
        limits=build_windows_searchable_pdf_export_limits(selected_policy),
        status_capacity_bytes=_SEARCHABLE_PDF_STATUS_BYTES,
    )


def build_windows_searchable_pdf_export_worker_composition(
    resources: DesktopApplicationResourceContext,
    qpdf_executable: Path | None = None,
    tesseract_executable: Path | None = None,
    tessdata_root: Path | None = None,
) -> SearchablePdfExportWorkerComposition:
    """Compose mixed export with the sole app-scoped heavy-work gate."""

    selected_resources = _require_application_resource_context(resources, required=True)
    selected_tesseract, selected_tessdata_root = _resource_bound_ocr_resources(
        selected_resources,
        tesseract_executable,
        tessdata_root,
    )
    return create_searchable_pdf_export_worker_composition(
        build_windows_searchable_pdf_export_worker_config(
            qpdf_executable,
            selected_tesseract,
            selected_tessdata_root,
            resource_policy=selected_resources.resource_policy,
        ),
        heavy_operations=selected_resources.heavy_operations,
    )


def _desktop_worker_config(
    resource_policy: ResourcePolicy | None = None,
) -> DesktopWorkerCompositionConfig:
    selected_policy = _require_resource_policy(resource_policy)
    (
        worker_executable,
        venv_launcher,
        bootstrap_script,
        runtime_roots,
        code_roots,
        working_directory,
    ) = _worker_runtime_resources()
    return create_desktop_worker_composition_config(
        # A venv's python.exe is a launcher that creates the base interpreter as
        # a child. The worker job deliberately permits one process, so launch the
        # approved base interpreter directly. CPython consumes the fixed venv
        # launcher hint before worker code audits the otherwise minimal environment.
        python_executable=worker_executable,
        venv_launcher=venv_launcher,
        bootstrap_script=bootstrap_script,
        runtime_roots=runtime_roots,
        code_roots=code_roots,
        working_directory=working_directory,
        resource_policy=WorkerResourcePolicy(
            process_memory_bytes=selected_policy.worker_process_memory_bytes,
            job_memory_bytes=selected_policy.worker_job_memory_bytes,
            cpu_time_ms=selected_policy.worker_cpu_time_ms,
            maximum_processes=min(
                selected_policy.worker_maximum_processes,
                selected_policy.maximum_heavy_operation_concurrency,
            ),
        ),
        timeout_policy=WorkerTimeoutPolicy(
            handshake_seconds=20.0,
            cancellation_grace_seconds=(selected_policy.cancellation_acknowledgement_ms / 1_000),
            shutdown_grace_seconds=10.0,
        ),
        maximum_heavy_operation_concurrency=(selected_policy.maximum_heavy_operation_concurrency),
        inspection_limits=ResourceLimits(
            wall_time_ms=60_000,
            cpu_time_ms=45_000,
            memory_bytes=min(512 * 1024 * 1024, selected_policy.worker_process_memory_bytes),
            temporary_storage_bytes=64 * 1024 * 1024,
            output_bytes=MAX_PDF_INSPECTION_FRAME_BYTES,
            page_count=selected_policy.maximum_page_count,
            pixel_count=1,
        ),
        image_limits=ResourceLimits(
            wall_time_ms=60_000,
            cpu_time_ms=45_000,
            memory_bytes=min(512 * 1024 * 1024, selected_policy.worker_process_memory_bytes),
            temporary_storage_bytes=(selected_policy.maximum_preview_pixels_per_page * 4),
            output_bytes=(
                IMAGE_PAGE_ARTIFACT_HEADER_BYTES
                + selected_policy.maximum_preview_pixels_per_page * 4
            ),
            page_count=1,
            pixel_count=min(
                268_435_456,
                selected_policy.worker_process_memory_bytes // 4,
            ),
        ),
        image_transform_limits=ResourceLimits(
            wall_time_ms=60_000,
            cpu_time_ms=45_000,
            memory_bytes=min(512 * 1024 * 1024, selected_policy.worker_process_memory_bytes),
            temporary_storage_bytes=min(
                selected_policy.maximum_temporary_storage_bytes,
                selected_policy.worker_process_memory_bytes,
            ),
            output_bytes=(
                IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES
                + selected_policy.maximum_preview_pixels_per_page * 4
            ),
            page_count=1,
            pixel_count=min(
                268_435_456,
                selected_policy.worker_process_memory_bytes // 4,
            ),
        ),
        thumbnail_limits=ResourceLimits(
            wall_time_ms=60_000,
            cpu_time_ms=45_000,
            memory_bytes=min(512 * 1024 * 1024, selected_policy.worker_process_memory_bytes),
            temporary_storage_bytes=64 * 1024 * 1024,
            output_bytes=4 * 1024 * 1024,
            page_count=selected_policy.maximum_page_count,
            pixel_count=min(1_048_576, selected_policy.maximum_preview_pixels_per_page),
        ),
    )


def _require_resource_policy(resource_policy: ResourcePolicy | None) -> ResourcePolicy:
    if resource_policy is None:
        return select_resource_policy()
    if type(resource_policy) is not ResourcePolicy:
        raise ValueError("resource_policy must be a ResourcePolicy")
    return resource_policy


def _require_application_resource_context(
    resources: DesktopApplicationResourceContext | None,
    *,
    required: bool = False,
) -> DesktopApplicationResourceContext:
    if resources is None:
        if required:
            raise ValueError("application resource context is required")
        return create_desktop_application_resource_context()
    if type(resources) is not DesktopApplicationResourceContext:
        raise ValueError("resources must be a DesktopApplicationResourceContext")
    return resources


def _configured_qpdf_executable(explicit: Path | None) -> Path:
    selected = explicit
    if selected is None:
        configured = os.environ.get("PWS_QPDF_EXECUTABLE")
        try:
            selected = Path(configured) if configured else _DEFAULT_PDF_EXPORT_QPDF_EXECUTABLE
        except OSError, TypeError, ValueError:
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.qpdf_configuration")
    if not isinstance(selected, Path):
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.qpdf_configuration")
    return selected


def _configured_ocr_resources(
    executable: Path | None,
    tessdata_root: Path | None,
) -> tuple[Path, Path]:
    selected_executable, selected_tessdata = _configured_optional_ocr_resources(
        executable,
        tessdata_root,
    )
    if selected_executable is None or selected_tessdata is None:
        reject_ocr(OcrErrorCode.ENGINE_UNAVAILABLE, "ocr.engine_configuration")
    return selected_executable, selected_tessdata


def _configured_optional_ocr_resources(
    executable: Path | None,
    tessdata_root: Path | None,
) -> tuple[Path | None, Path | None]:
    """Capture normalized optional OCR configuration once without starting a worker."""

    selected_executable: Path | str | None = executable
    selected_tessdata: Path | str | None = tessdata_root
    if selected_executable is None:
        selected_executable = os.environ.get("PWS_TESSERACT_EXECUTABLE") or None
    if selected_tessdata is None:
        selected_tessdata = os.environ.get("PWS_TESSDATA_ROOT") or None
    try:
        normalized_executable = (
            None
            if selected_executable is None
            else Path(os.path.abspath(os.fspath(selected_executable)))
        )
        normalized_tessdata = (
            None
            if selected_tessdata is None
            else Path(os.path.abspath(os.fspath(selected_tessdata)))
        )
    except OSError, TypeError, ValueError:
        reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.engine_configuration")
    return normalized_executable, normalized_tessdata


def _resource_bound_ocr_resources(
    resources: DesktopApplicationResourceContext,
    executable: Path | None,
    tessdata_root: Path | None,
) -> tuple[Path, Path]:
    """Resolve OCR paths only from the app authority and reject split configurations."""

    captured = (resources._ocr_executable, resources._ocr_tessdata_root)
    if executable is None and tessdata_root is None:
        selected = captured
    else:
        if not isinstance(executable, Path) or not isinstance(tessdata_root, Path):
            reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.language_composition")
        try:
            selected = (
                Path(os.path.abspath(os.fspath(executable))),
                Path(os.path.abspath(os.fspath(tessdata_root))),
            )
        except OSError, TypeError, ValueError:
            reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.language_composition")
        if selected != captured:
            reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.language_composition")
    if selected[0] is None or selected[1] is None:
        reject_ocr(OcrErrorCode.ENGINE_UNAVAILABLE, "ocr.engine_configuration")
    return selected[0], selected[1]


def _worker_runtime_resources() -> tuple[
    Path,
    Path | None,
    Path,
    tuple[Path, ...],
    tuple[Path, ...],
    Path,
]:
    package_root = Path(__file__).resolve().parent
    code_root = package_root.parent
    runtime_root = Path(sys.prefix).resolve()
    base_runtime_root = Path(sys.base_prefix).resolve()
    worker_executable = (base_runtime_root / "python.exe").resolve()
    venv_launcher = Path(sys.executable).resolve() if runtime_root != base_runtime_root else None
    runtime_roots = (
        (runtime_root,) if base_runtime_root == runtime_root else (runtime_root, base_runtime_root)
    )
    return (
        worker_executable,
        venv_launcher,
        (package_root / "worker" / "host_bootstrap.py").resolve(),
        runtime_roots,
        (code_root,),
        code_root,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the desktop application."""

    resources = create_desktop_application_resource_context()
    return run_desktop_application(
        lambda: build_backend(resources),
        sys.argv if argv is None else argv,
        resource_policy=resources.resource_policy,
    )


if __name__ == "__main__":
    raise SystemExit(main())

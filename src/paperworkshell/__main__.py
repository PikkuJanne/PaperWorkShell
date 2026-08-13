"""PaperWorkShell desktop composition entrypoint."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from paperworkshell.app.case_lifecycle import CaseLifecycleService
from paperworkshell.app.export_history import (
    PdfExportHistoryService,
    PdfExportRetryRecoveryService,
)
from paperworkshell.app.organizer import OrganizerBackend
from paperworkshell.app.page_previews import PagePreviewService
from paperworkshell.app.pdf_export import PdfExportService
from paperworkshell.app.recovery import (
    StartupRecoveryDiagnosticSink,
    StartupRecoveryService,
)
from paperworkshell.app.searchable_pdf_export import SearchablePdfExportService
from paperworkshell.app.source_ingest import (
    CommittedPdfSourceBatchStore,
    CommittedSearchablePdfSourceBatchStore,
    SourceIngestService,
)
from paperworkshell.app.source_inspection import SourceInspectorRouter
from paperworkshell.domain.image_pages import IMAGE_PAGE_ARTIFACT_HEADER_BYTES
from paperworkshell.domain.image_transforms import IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES
from paperworkshell.domain.import_precheck import ImportLimits
from paperworkshell.domain.ocr import OcrErrorCode, reject_ocr
from paperworkshell.domain.pdf_export import (
    PdfExportErrorCode,
    reject_pdf_export,
)
from paperworkshell.domain.pdf_inspection import MAX_PDF_INSPECTION_FRAME_BYTES
from paperworkshell.domain.recovery import RecoveryErrorCode, reject_recovery
from paperworkshell.domain.resource_policy import ResourcePolicy, select_resource_policy
from paperworkshell.domain.searchable_pdf_export import (
    SearchablePdfExportErrorCode,
    reject_searchable_pdf_export,
)
from paperworkshell.export.pdf_destination import WindowsVerifiedPdfDestination
from paperworkshell.storage._windows import MAXIMUM_STREAM_CHUNK_BYTES
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.export_destination import WindowsExportDestination
from paperworkshell.storage.export_history import WindowsPdfExportHistoryRepository
from paperworkshell.storage.export_retry import WindowsPdfExportRetryRepository
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.recovery import WindowsStartupRecoveryStore
from paperworkshell.storage.source_ingest import WindowsSourceIngestStore
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.thumbnail_cache import WindowsThumbnailCacheStore
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.ui.application import run_desktop_application
from paperworkshell.worker.client.models import WorkerTimeoutPolicy
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.desktop_composition import (
    OCR_WORKER_MAXIMUM_PROCESSES,
    SEARCHABLE_PDF_EXPORT_WORKER_MAXIMUM_PROCESSES,
    DesktopHeavyOperationCoordinator,
    DesktopWorkerCompositionConfig,
    OcrWorkerComposition,
    OcrWorkerCompositionConfig,
    PdfExportWorkerComposition,
    PdfExportWorkerCompositionConfig,
    SearchablePdfExportWorkerComposition,
    SearchablePdfExportWorkerCompositionConfig,
    create_desktop_heavy_operation_coordinator,
    create_desktop_worker_composition,
    create_desktop_worker_composition_config,
    create_ocr_worker_composition,
    create_ocr_worker_composition_config,
    create_pdf_export_worker_composition,
    create_pdf_export_worker_composition_config,
    create_searchable_pdf_export_worker_composition,
    create_searchable_pdf_export_worker_composition_config,
)
from paperworkshell.worker.protocol.models import ResourceLimits

_PDF_EXPORT_OUTPUT_BYTES = 512 * 1024 * 1024
_OCR_OUTPUT_BYTES = 128 * 1024 * 1024
_SEARCHABLE_PDF_OUTPUT_BYTES = 128 * 1024 * 1024
_SEARCHABLE_PDF_STATUS_BYTES = 2 * 1024 * 1024
_DEFAULT_PDF_EXPORT_QPDF_EXECUTABLE = Path(r"C:\Program Files\qpdf 12.3.2\bin\qpdf.exe")


@dataclass(frozen=True, slots=True)
class DesktopApplicationResourceContext:
    """App-scoped policy and heavy-work gate selected once at process startup."""

    resource_policy: ResourcePolicy
    heavy_operations: DesktopHeavyOperationCoordinator

    def __post_init__(self) -> None:
        if (
            type(self.resource_policy) is not ResourcePolicy
            or type(self.heavy_operations) is not DesktopHeavyOperationCoordinator
            or self.heavy_operations.snapshot().maximum_concurrency
            != self.resource_policy.maximum_heavy_operation_concurrency
        ):
            raise ValueError("application resource context is invalid")


def create_desktop_application_resource_context(
    resource_policy: ResourcePolicy | None = None,
) -> DesktopApplicationResourceContext:
    """Select one policy and construct its sole app-scoped heavy-work gate."""

    selected_policy = _require_resource_policy(resource_policy)
    return DesktopApplicationResourceContext(
        selected_policy,
        create_desktop_heavy_operation_coordinator(
            selected_policy.maximum_heavy_operation_concurrency
        ),
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
    )


def build_windows_searchable_pdf_export_service(
    manager: CaseWorkspaceManager,
    sources: CommittedSearchablePdfSourceBatchStore,
    composition: SearchablePdfExportWorkerComposition,
    *,
    resources: DesktopApplicationResourceContext,
) -> SearchablePdfExportService:
    """Wire mixed-source persistence around the combined OCR/export worker."""

    if (
        not isinstance(manager, CaseWorkspaceManager)
        or not isinstance(sources, CommittedSearchablePdfSourceBatchStore)
        or type(composition) is not SearchablePdfExportWorkerComposition
        or composition.closed
        or type(resources) is not DesktopApplicationResourceContext
        or composition.heavy_operations is not resources.heavy_operations
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


def _build_backend(
    manager: CaseWorkspaceManager,
    *,
    resources: DesktopApplicationResourceContext | None = None,
) -> OrganizerBackend:
    """Recover local cases before constructing document-worker resources."""

    startup_recovery = build_windows_startup_recovery_service(manager).recover_startup()
    selected_resources = _require_application_resource_context(resources)
    selected_policy = selected_resources.resource_policy
    lifecycle = CaseLifecycleService(WindowsCaseLifecycleStore(manager))
    repository = WindowsManifestRepository(manager)
    source_store = WindowsSourceIngestStore(
        manager,
        WindowsSourcePrechecker(_import_limits(selected_policy)),
        maximum_page_count=selected_policy.maximum_page_count,
    )
    thumbnail_cache = WindowsThumbnailCacheStore(manager)
    composition = create_desktop_worker_composition(
        _desktop_worker_config(selected_policy),
        prepared_sources=source_store,
        committed_pages=source_store,
        thumbnail_cache=thumbnail_cache,
        heavy_operations=selected_resources.heavy_operations,
    )
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
        return OrganizerBackend(
            lifecycle,
            repository,
            source_ingest,
            thumbnails,
            close_resources=composition.close,
            startup_recovery=startup_recovery,
        )
    except BaseException:
        composition.close()
        raise


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
        pixel_count=1_048_576,
    )


def build_windows_pdf_export_worker_config(
    qpdf_executable: Path | None = None,
    *,
    resource_policy: ResourcePolicy | None = None,
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
        export_limits=build_windows_pdf_export_limits(selected_policy),
    )


def build_windows_pdf_export_worker_composition(
    resources: DesktopApplicationResourceContext,
    qpdf_executable: Path | None = None,
) -> PdfExportWorkerComposition:
    """Compose export with the exact app-scoped policy and heavy-work gate."""

    selected_resources = _require_application_resource_context(resources, required=True)
    return create_pdf_export_worker_composition(
        build_windows_pdf_export_worker_config(
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
    return create_ocr_worker_composition(
        build_windows_ocr_worker_config(
            tesseract_executable,
            tessdata_root,
            resource_policy=selected_resources.resource_policy,
        ),
        heavy_operations=selected_resources.heavy_operations,
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
    return create_searchable_pdf_export_worker_composition(
        build_windows_searchable_pdf_export_worker_config(
            qpdf_executable,
            tesseract_executable,
            tessdata_root,
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
    selected_executable = executable
    selected_tessdata = tessdata_root
    if selected_executable is None:
        configured = os.environ.get("PWS_TESSERACT_EXECUTABLE")
        try:
            selected_executable = Path(configured) if configured else None
        except OSError, TypeError, ValueError:
            reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.tesseract_configuration")
    if selected_tessdata is None:
        configured = os.environ.get("PWS_TESSDATA_ROOT")
        try:
            selected_tessdata = Path(configured) if configured else None
        except OSError, TypeError, ValueError:
            reject_ocr(OcrErrorCode.MODEL_INVALID, "ocr.tessdata_configuration")
    if not isinstance(selected_executable, Path) or not isinstance(selected_tessdata, Path):
        reject_ocr(OcrErrorCode.ENGINE_UNAVAILABLE, "ocr.engine_configuration")
    return selected_executable, selected_tessdata


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

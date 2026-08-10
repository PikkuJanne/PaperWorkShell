"""PaperWorkShell desktop composition entrypoint."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from paperworkshell.app.case_lifecycle import CaseLifecycleService
from paperworkshell.app.export_history import (
    PdfExportHistoryService,
    PdfExportRetryRecoveryService,
)
from paperworkshell.app.organizer import OrganizerBackend
from paperworkshell.app.pdf_export import PdfExportService
from paperworkshell.app.pdf_thumbnails import PdfThumbnailService
from paperworkshell.app.recovery import (
    StartupRecoveryDiagnosticSink,
    StartupRecoveryService,
)
from paperworkshell.app.source_ingest import (
    CommittedPdfSourceBatchStore,
    SourceIngestService,
)
from paperworkshell.app.source_inspection import SourceInspectorRouter
from paperworkshell.domain.import_precheck import ImportLimits
from paperworkshell.domain.pdf_export import (
    PdfExportErrorCode,
    reject_pdf_export,
)
from paperworkshell.domain.pdf_inspection import MAX_PDF_INSPECTION_FRAME_BYTES
from paperworkshell.domain.recovery import RecoveryErrorCode, reject_recovery
from paperworkshell.export.pdf_destination import WindowsVerifiedPdfDestination
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
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
    DesktopWorkerCompositionConfig,
    PdfExportWorkerComposition,
    PdfExportWorkerCompositionConfig,
    create_desktop_worker_composition,
    create_desktop_worker_composition_config,
    create_pdf_export_worker_composition_config,
)
from paperworkshell.worker.protocol.models import ResourceLimits

_MAXIMUM_CASE_BYTES = 4 * 1024 * 1024 * 1024
_MAXIMUM_CACHE_BYTES = 512 * 1024 * 1024
_MAXIMUM_TEMP_BYTES = 1024 * 1024 * 1024
_MAXIMUM_SOURCE_BYTES = 512 * 1024 * 1024
_MAXIMUM_PAGES = 1_000
_WORKER_MEMORY_BYTES = 768 * 1024 * 1024
_WORKER_JOB_MEMORY_BYTES = 1024 * 1024 * 1024
_PDF_EXPORT_OUTPUT_BYTES = 512 * 1024 * 1024
_DEFAULT_PDF_EXPORT_QPDF_EXECUTABLE = Path(r"C:\Program Files\qpdf 12.3.2\bin\qpdf.exe")


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
) -> PdfExportService:
    """Wire concrete Windows persistence around one export-only composition."""

    if (
        not isinstance(manager, CaseWorkspaceManager)
        or not isinstance(sources, CommittedPdfSourceBatchStore)
        or type(composition) is not PdfExportWorkerComposition
        or composition.closed
    ):
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.composition")
    manifests = WindowsManifestRepository(manager)
    return PdfExportService(
        manifests,
        sources,
        composition.pdf_exporter,
        WindowsVerifiedPdfDestination(),
        PdfExportHistoryService(
            WindowsPdfExportHistoryRepository(manager),
            manifests,
        ),
        WindowsPdfExportRetryRepository(manager),
    )


def build_backend() -> OrganizerBackend:
    """Construct the complete backend on the caller's permanent owner thread."""

    manager = CaseWorkspaceManager.for_current_user(quota=_workspace_quota())
    return _build_backend(manager)


def _build_backend(manager: CaseWorkspaceManager) -> OrganizerBackend:
    """Recover local cases before constructing document-worker resources."""

    startup_recovery = build_windows_startup_recovery_service(manager).recover_startup()
    lifecycle = CaseLifecycleService(WindowsCaseLifecycleStore(manager))
    repository = WindowsManifestRepository(manager)
    source_store = WindowsSourceIngestStore(
        manager,
        WindowsSourcePrechecker(_import_limits()),
        maximum_page_count=_MAXIMUM_PAGES,
    )
    thumbnail_cache = WindowsThumbnailCacheStore(manager)
    composition = create_desktop_worker_composition(
        _desktop_worker_config(),
        prepared_sources=source_store,
        committed_pages=source_store,
        thumbnail_cache=thumbnail_cache,
    )
    try:
        source_ingest = SourceIngestService(
            source_store,
            SourceInspectorRouter(composition.source_inspector),
        )
        thumbnails = PdfThumbnailService(composition.thumbnail_generator)
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


def _workspace_quota() -> WorkspaceQuota:
    return WorkspaceQuota(
        maximum_case_bytes=_MAXIMUM_CASE_BYTES,
        maximum_cache_bytes=_MAXIMUM_CACHE_BYTES,
        maximum_temp_bytes=_MAXIMUM_TEMP_BYTES,
        maximum_entries=50_000,
        maximum_depth=32,
    )


def _import_limits() -> ImportLimits:
    return ImportLimits(
        maximum_source_bytes=_MAXIMUM_SOURCE_BYTES,
        maximum_pdf_pages=_MAXIMUM_PAGES,
        maximum_image_width=32_768,
        maximum_image_height=32_768,
        maximum_image_pixels=268_435_456,
        maximum_decompressed_bytes=1024 * 1024 * 1024,
        decompression_warning_ratio=100,
        maximum_decompression_ratio=2_000,
        maximum_header_bytes=65_536,
        maximum_structure_records=256,
    )


def build_windows_pdf_export_limits() -> ResourceLimits:
    """Return the fixed bounded production policy for one verified PDF export."""

    return ResourceLimits(
        wall_time_ms=120_000,
        cpu_time_ms=90_000,
        memory_bytes=512 * 1024 * 1024,
        temporary_storage_bytes=_MAXIMUM_TEMP_BYTES,
        output_bytes=_PDF_EXPORT_OUTPUT_BYTES,
        page_count=_MAXIMUM_PAGES,
        pixel_count=1_048_576,
    )


def build_windows_pdf_export_worker_config(
    qpdf_executable: Path | None = None,
) -> PdfExportWorkerCompositionConfig:
    """Select the trusted qpdf path and compose the export-only worker policy."""

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
            process_memory_bytes=_WORKER_MEMORY_BYTES,
            job_memory_bytes=_WORKER_JOB_MEMORY_BYTES,
            cpu_time_ms=120_000,
            maximum_processes=2,
        ),
        timeout_policy=WorkerTimeoutPolicy(
            handshake_seconds=20.0,
            cancellation_grace_seconds=5.0,
            shutdown_grace_seconds=10.0,
        ),
        export_limits=build_windows_pdf_export_limits(),
    )


def _desktop_worker_config() -> DesktopWorkerCompositionConfig:
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
            process_memory_bytes=_WORKER_MEMORY_BYTES,
            job_memory_bytes=_WORKER_JOB_MEMORY_BYTES,
            cpu_time_ms=120_000,
            maximum_processes=1,
        ),
        timeout_policy=WorkerTimeoutPolicy(
            handshake_seconds=20.0,
            cancellation_grace_seconds=5.0,
            shutdown_grace_seconds=10.0,
        ),
        inspection_limits=ResourceLimits(
            wall_time_ms=60_000,
            cpu_time_ms=45_000,
            memory_bytes=512 * 1024 * 1024,
            temporary_storage_bytes=64 * 1024 * 1024,
            output_bytes=MAX_PDF_INSPECTION_FRAME_BYTES,
            page_count=_MAXIMUM_PAGES,
            pixel_count=1,
        ),
        thumbnail_limits=ResourceLimits(
            wall_time_ms=60_000,
            cpu_time_ms=45_000,
            memory_bytes=512 * 1024 * 1024,
            temporary_storage_bytes=64 * 1024 * 1024,
            output_bytes=4 * 1024 * 1024,
            page_count=_MAXIMUM_PAGES,
            pixel_count=1_048_576,
        ),
    )


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

    return run_desktop_application(build_backend, sys.argv if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())

"""PaperWorkShell desktop composition entrypoint."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from paperworkshell.app.case_lifecycle import CaseLifecycleService
from paperworkshell.app.organizer import OrganizerBackend
from paperworkshell.app.pdf_thumbnails import PdfThumbnailService
from paperworkshell.app.source_ingest import SourceIngestService
from paperworkshell.app.source_inspection import SourceInspectorRouter
from paperworkshell.domain.import_precheck import ImportLimits
from paperworkshell.domain.pdf_inspection import MAX_PDF_INSPECTION_FRAME_BYTES
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.source_ingest import WindowsSourceIngestStore
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.thumbnail_cache import WindowsThumbnailCacheStore
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.ui.application import run_desktop_application
from paperworkshell.worker.client.models import WorkerTimeoutPolicy
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.desktop_composition import (
    DesktopWorkerCompositionConfig,
    create_desktop_worker_composition,
    create_desktop_worker_composition_config,
)
from paperworkshell.worker.protocol.models import ResourceLimits

_MAXIMUM_CASE_BYTES = 4 * 1024 * 1024 * 1024
_MAXIMUM_CACHE_BYTES = 512 * 1024 * 1024
_MAXIMUM_TEMP_BYTES = 1024 * 1024 * 1024
_MAXIMUM_SOURCE_BYTES = 512 * 1024 * 1024
_MAXIMUM_PAGES = 1_000
_WORKER_MEMORY_BYTES = 768 * 1024 * 1024
_WORKER_JOB_MEMORY_BYTES = 1024 * 1024 * 1024


def build_backend() -> OrganizerBackend:
    """Construct the complete backend on the caller's permanent owner thread."""

    manager = CaseWorkspaceManager.for_current_user(quota=_workspace_quota())
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


def _desktop_worker_config() -> DesktopWorkerCompositionConfig:
    package_root = Path(__file__).resolve().parent
    code_root = package_root.parent
    runtime_root = Path(sys.prefix).resolve()
    base_runtime_root = Path(sys.base_prefix).resolve()
    worker_executable = (base_runtime_root / "python.exe").resolve()
    venv_launcher = Path(sys.executable).resolve() if runtime_root != base_runtime_root else None
    runtime_roots = (
        (runtime_root,) if base_runtime_root == runtime_root else (runtime_root, base_runtime_root)
    )
    return create_desktop_worker_composition_config(
        # A venv's python.exe is a launcher that creates the base interpreter as
        # a child. The worker job deliberately permits one process, so launch the
        # approved base interpreter directly. CPython consumes the fixed venv
        # launcher hint before worker code audits the otherwise minimal environment.
        python_executable=worker_executable,
        venv_launcher=venv_launcher,
        bootstrap_script=(package_root / "worker" / "host_bootstrap.py").resolve(),
        runtime_roots=runtime_roots,
        code_roots=(code_root,),
        working_directory=code_root,
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


def main(argv: Sequence[str] | None = None) -> int:
    """Run the desktop application."""

    return run_desktop_application(build_backend, sys.argv if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())

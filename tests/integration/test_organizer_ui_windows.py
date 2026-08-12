from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from paperworkshell.__main__ import _desktop_worker_config
from paperworkshell.app.case_lifecycle import CaseLifecycleService
from paperworkshell.app.organizer import OrganizerBackend, OrganizerViewState
from paperworkshell.app.pdf_thumbnails import (
    DEFAULT_PDF_THUMBNAIL_TIMEOUT_SECONDS,
    PdfThumbnailService,
    ThumbnailProgressSink,
)
from paperworkshell.app.pdf_thumbnails import (
    CancellationProbe as ThumbnailCancellationProbe,
)
from paperworkshell.app.source_ingest import SourceIngestService
from paperworkshell.app.source_inspection import SourceInspectorRouter
from paperworkshell.domain.import_precheck import DetectedMediaType, ImportLimits
from paperworkshell.domain.pdf_thumbnails import PdfThumbnailRequest, PdfThumbnailResult
from paperworkshell.domain.source_ingest import (
    CompletedSourceInspection,
    PreparedSourceSummary,
    SourceEncryptionState,
    SourceImportProgress,
    SourceProgressPhase,
    SourceSignatureState,
)
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.source_ingest import WindowsSourceIngestStore
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.thumbnail_cache import WindowsThumbnailCacheStore
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.ui.backend import (
    OperationCancellation,
    OrganizerBackendCommands,
    OrganizerBackendWorker,
)
from paperworkshell.ui.window import PaperWorkShellWindow
from paperworkshell.worker.desktop_composition import create_desktop_worker_composition
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "synthetic"
    / "v1"
    / "pdf"
    / "render-calibration.pdf"
)


class _TwoPagePdfInspector:
    def inspect(
        self,
        source: PreparedSourceSummary,
        *,
        deadline: object,
        cancellation: object,
        progress: object,
    ) -> CompletedSourceInspection:
        assert callable(deadline) and not deadline()
        assert callable(cancellation) and not cancellation()
        assert callable(progress)
        progress(SourceImportProgress(source.pending_id, SourceProgressPhase.INSPECTING, 1, 1))
        return CompletedSourceInspection(
            source.pending_id,
            DetectedMediaType.PDF,
            2,
            SourceSignatureState.NONE_DETECTED,
            SourceEncryptionState.UNENCRYPTED,
        )


class _EmptyThumbnailService:
    def get_thumbnails(
        self,
        requests: tuple[PdfThumbnailRequest, ...],
        *,
        timeout_seconds: float = DEFAULT_PDF_THUMBNAIL_TIMEOUT_SECONDS,
        cancellation: ThumbnailCancellationProbe | None = None,
        progress: ThumbnailProgressSink | None = None,
    ) -> tuple[PdfThumbnailResult, ...]:
        del requests, timeout_seconds, cancellation, progress
        return ()


def _quota() -> WorkspaceQuota:
    return WorkspaceQuota(
        maximum_case_bytes=32_000_000,
        maximum_cache_bytes=4_000_000,
        maximum_temp_bytes=8_000_000,
        maximum_entries=1_024,
        maximum_depth=16,
    )


def _limits() -> ImportLimits:
    return ImportLimits(
        maximum_source_bytes=4_000_000,
        maximum_pdf_pages=16,
        maximum_image_width=10_000,
        maximum_image_height=10_000,
        maximum_image_pixels=50_000_000,
        maximum_decompressed_bytes=200_000_000,
        decompression_warning_ratio=100,
        maximum_decompression_ratio=2_000,
        maximum_header_bytes=4_096,
        maximum_structure_records=64,
    )


def _backend(profile: Path) -> OrganizerBackend:
    manager = CaseWorkspaceManager(profile_root=profile, quota=_quota())
    source_store = WindowsSourceIngestStore(
        manager,
        WindowsSourcePrechecker(_limits()),
        maximum_page_count=16,
    )
    return OrganizerBackend(
        CaseLifecycleService(WindowsCaseLifecycleStore(manager)),
        WindowsManifestRepository(manager),
        SourceIngestService(source_store, _TwoPagePdfInspector()),
        _EmptyThumbnailService(),
    )


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows workspace integration")
def test_keyboard_organizer_autosaves_and_reloads_exact_state(tmp_path: Path) -> None:
    app = _application()
    profile = tmp_path / "profile"
    profile.mkdir()
    source_before = FIXTURE.read_bytes()
    backend = _backend(profile)
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(lambda: backend)
    commands.connect_worker(worker)
    window = PaperWorkShellWindow(commands, worker)
    states: list[OrganizerViewState] = []
    worker.state_changed.connect(states.append)
    window.show()
    worker.initialize()
    worker.create_case()
    assert states[-1].case_id is not None
    case_id: UUID = states[-1].case_id

    worker.import_source(1, FIXTURE, "Synthetic calibration.pdf", OperationCancellation())
    app.processEvents()
    imported = states[-1]
    assert len(imported.pages) == 2
    first_page_id = imported.pages[0].page_id

    QTest.keyClick(window, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(window, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
    QTest.keyClick(window, Qt.Key.Key_Delete)
    QTest.keyClick(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    QTest.qWait(550)
    app.processEvents()

    edited = states[-1]
    assert len(edited.pages) == 2
    assert edited.pages[-1].page_id == first_page_id
    assert edited.pages[-1].rotation_degrees == 90
    assert not edited.autosave_pending

    worker.close_case()
    backend.shutdown()
    fresh = _backend(profile)
    reloaded = fresh.open_case(case_id)

    assert tuple((page.page_id, page.rotation_degrees) for page in reloaded.pages) == tuple(
        (page.page_id, page.rotation_degrees) for page in edited.pages
    )
    assert not reloaded.can_undo
    assert not reloaded.can_redo
    assert FIXTURE.read_bytes() == source_before

    fresh.shutdown()
    window._allow_close = True
    window.close()
    window.deleteLater()
    app.processEvents()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows worker integration")
def test_live_composition_imports_and_renders_bounded_organizer_previews(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(profile_root=profile, quota=_quota())
    source_store = WindowsSourceIngestStore(
        manager,
        WindowsSourcePrechecker(_limits()),
        maximum_page_count=16,
    )
    cache = WindowsThumbnailCacheStore(manager)
    composition = create_desktop_worker_composition(
        _desktop_worker_config(),
        prepared_sources=source_store,
        committed_pages=source_store,
        thumbnail_cache=cache,
        heavy_operations=HeavyOperationCoordinator(1),
    )
    backend = OrganizerBackend(
        CaseLifecycleService(WindowsCaseLifecycleStore(manager)),
        WindowsManifestRepository(manager),
        SourceIngestService(source_store, SourceInspectorRouter(composition.source_inspector)),
        PdfThumbnailService(composition.thumbnail_generator),
        close_resources=composition.close,
    )
    source_before = FIXTURE.read_bytes()

    try:
        backend.create_case()
        outcome = backend.import_source(FIXTURE, "Synthetic live calibration.pdf")
        imported = outcome.committed_import
        previews = backend.get_thumbnails(
            maximum_width_pixels=80,
            maximum_height_pixels=100,
            page_ids=imported.page_ids,
        )

        assert len(imported.page_ids) == 2
        assert tuple(result.snapshot.page_id for result in previews) == imported.page_ids
        assert all(result.snapshot.width_pixels <= 80 for result in previews)
        assert all(result.snapshot.height_pixels <= 100 for result in previews)
        assert all(result.snapshot.pixels for result in previews)
        assert FIXTURE.read_bytes() == source_before
    finally:
        backend.shutdown()

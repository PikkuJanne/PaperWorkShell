"""Live Milestone 1 case-to-export-and-recovery gate on Windows."""

from __future__ import annotations

import hashlib
import importlib
import io
import json
import logging
import os
import platform
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pikepdf
import pytest
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, qVersion
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication

from paperworkshell.__main__ import (
    DesktopApplicationResourceContext,
    _desktop_worker_config,
    _import_limits,
    _workspace_quota,
    build_windows_pdf_export_service,
    build_windows_pdf_export_worker_config,
    build_windows_startup_recovery_service,
)
from paperworkshell.app.case_lifecycle import CaseLifecycleService
from paperworkshell.app.organizer import OrganizerBackend, OrganizerViewState
from paperworkshell.app.page_previews import (
    PagePreviewProgress,
    PagePreviewProgressPhase,
    PagePreviewResult,
    PagePreviewService,
)
from paperworkshell.app.source_ingest import SourceIngestService
from paperworkshell.app.source_inspection import SourceInspectorRouter
from paperworkshell.diagnostics.logging import SanitizedDiagnosticLogger
from paperworkshell.diagnostics.summary import render_diagnostic_summary
from paperworkshell.domain.manifest_repository import CURRENT_MANIFEST_SCHEMA_VERSION
from paperworkshell.domain.pdf_export import (
    PDF_EXPORT_PROFILE_VERSION,
    PdfExportCheckCode,
    PdfExportOutcome,
    PdfExportOutcomeStatus,
    build_pdf_export_plan,
)
from paperworkshell.domain.pdf_thumbnails import (
    PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES,
    PdfThumbnailError,
    PdfThumbnailErrorCode,
    PdfThumbnailResult,
)
from paperworkshell.domain.recovery import CaseRecoveryStatus, StartupRecoveryStatus
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.export_history import WindowsPdfExportHistoryRepository
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.quotas import (
    QuotaReservationObservationSnapshot,
    QuotaReservationSnapshot,
    WorkspaceUsage,
)
from paperworkshell.storage.source_ingest import WindowsSourceIngestStore
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.thumbnail_cache import WindowsThumbnailCacheStore
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.ui.models import OrganizerPageListModel
from paperworkshell.worker.client.models import WorkerResourceSnapshot
from paperworkshell.worker.desktop_composition import (
    DesktopWorkerComposition,
    create_desktop_worker_composition,
    create_pdf_export_worker_composition,
)
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.pdf_thumbnail_profile import (
    PDF_THUMBNAIL_RENDERER_PROFILE_SHA256,
)
from paperworkshell.worker.protocol.models import PROTOCOL_VERSION
from tools.m1_vertical_slice_benchmark import (
    M1Phase,
    PhaseMeasurement,
    PhaseOutcome,
    WorkerOperation,
    WorkerResourceMeasurement,
    WorkspaceCheckpoint,
    WorkspaceUsageMeasurement,
)
from tools.m1_vertical_slice_live_runner import (
    emit_live_child_artifacts,
    production_qpdf_version_identity,
    production_worker_config_digest,
)

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0114 exercises the supported Windows AppContainer worker boundary",
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PDF_FIXTURES = _PROJECT_ROOT / "tests" / "fixtures" / "synthetic" / "v1" / "pdf"
_FIRST_FIXTURE = _PDF_FIXTURES / "render-calibration.pdf"
_SECOND_FIXTURE = _PDF_FIXTURES / "born-digital.pdf"
_SOURCE_FILENAME_CANARY = "PRIVATE-M1-SOURCE-FILENAME-7A19"
_DISPLAY_NAME_CANARY = "PRIVATE-M1-DISPLAY-NAME-4C82"
_DESTINATION_NAME_CANARY = "PRIVATE-M1-DESTINATION-NAME-93D5"
_DOCUMENT_TEXT_CANARY = "PRIVATE-M1-DOCUMENT-TEXT-B607"
_METADATA_CANARY = "PRIVATE-M1-METADATA-E214"
_DIAGNOSTIC_TIME = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
_EXPORT_TIMER_INTERVAL_MILLISECONDS = 50
_CANCELLATION_RENDER_DIMENSION_PIXELS = 900
_VERIFIED_CHECKS = (
    PdfExportCheckCode.PDF_REOPENED,
    PdfExportCheckCode.STRUCTURE_CLEAN,
    PdfExportCheckCode.PAGE_COUNT_MATCHED,
    PdfExportCheckCode.UNENCRYPTED,
    PdfExportCheckCode.ALL_PAGES_RENDERED,
    PdfExportCheckCode.ORDER_ROTATION_MATCHED,
    PdfExportCheckCode.OUTPUT_COMMIT_REOPENED,
)


@dataclass(frozen=True, slots=True)
class _VerticalSliceStack:
    manager: CaseWorkspaceManager
    sources: WindowsSourceIngestStore
    backend: OrganizerBackend
    desktop: DesktopWorkerComposition


@dataclass(frozen=True, slots=True)
class _ResponsiveExportResult:
    outcome: PdfExportOutcome
    heartbeat_count: int
    maximum_heartbeat_gap_ns: int
    worker_resource_snapshot: WorkerResourceSnapshot
    peak_cache_bytes: int
    peak_temp_bytes: int


def _conservative_workspace_peaks(
    before: WorkspaceUsage,
    after: WorkspaceUsage,
    claims: QuotaReservationObservationSnapshot,
) -> tuple[int, int]:
    """Bound cache/temp growth from strict quiescent inventories and all claims."""

    assert claims.current.is_empty, "the completed export retained a capacity claim"
    return (
        max(before.cache_bytes, after.cache_bytes) + claims.high_water.cache_bytes,
        max(before.temp_bytes, after.temp_bytes) + claims.high_water.temp_bytes,
    )


def _build_stack(
    profile: Path,
    *,
    diagnostics: SanitizedDiagnosticLogger,
) -> _VerticalSliceStack:
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=_workspace_quota(CONSTRAINED_RESOURCE_POLICY_V1),
        minimum_free_space_reserve_bytes=(
            CONSTRAINED_RESOURCE_POLICY_V1.minimum_export_free_space_reserve_bytes
        ),
    )
    limits = _import_limits(CONSTRAINED_RESOURCE_POLICY_V1)
    sources = WindowsSourceIngestStore(
        manager,
        WindowsSourcePrechecker(limits),
        maximum_page_count=limits.maximum_pdf_pages,
    )
    startup_recovery = build_windows_startup_recovery_service(
        manager,
        diagnostics=diagnostics,
        clock=lambda: _DIAGNOSTIC_TIME,
    ).recover_startup()
    desktop = create_desktop_worker_composition(
        _desktop_worker_config(CONSTRAINED_RESOURCE_POLICY_V1),
        prepared_sources=sources,
        committed_pages=sources,
        thumbnail_cache=WindowsThumbnailCacheStore(manager),
        heavy_operations=HeavyOperationCoordinator(
            CONSTRAINED_RESOURCE_POLICY_V1.maximum_heavy_operation_concurrency
        ),
    )
    try:
        backend = OrganizerBackend(
            CaseLifecycleService(WindowsCaseLifecycleStore(manager)),
            WindowsManifestRepository(manager),
            SourceIngestService(
                sources,
                SourceInspectorRouter(
                    desktop.source_inspector,
                    desktop.image_source_inspector,
                ),
            ),
            PagePreviewService(
                desktop.thumbnail_generator,
                desktop.image_page_generator,
                desktop.image_page_transformer,
            ),
            close_resources=desktop.close,
            startup_recovery=startup_recovery,
        )
    except BaseException:
        desktop.close()
        raise
    return _VerticalSliceStack(manager, sources, backend, desktop)


def _export_pdf_while_event_loop_remains_responsive(
    application: QCoreApplication,
    manager: CaseWorkspaceManager,
    sources: WindowsSourceIngestStore,
    case_id: UUID,
    destination: Path,
    heavy_operations: HeavyOperationCoordinator,
    gui_cache_bytes: int,
) -> _ResponsiveExportResult:
    """Run one production-composed export off-thread while sampling the UI loop."""

    assert threading.current_thread() is threading.main_thread()
    assert QCoreApplication.instance() is application
    event_loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(_EXPORT_TIMER_INTERVAL_MILLISECONDS)
    timer.setSingleShot(False)
    export_finished = threading.Event()
    outcomes: list[PdfExportOutcome] = []
    resource_snapshots: list[WorkerResourceSnapshot] = []
    errors: list[BaseException] = []
    heartbeat_times_ns: list[int] = []
    finished_times_ns: list[int] = []
    export_thread_ids: list[int] = []
    heartbeat_thread_ids: list[int] = []
    composition_closed_states: list[bool] = []
    main_thread_id = threading.get_ident()
    timeline_lock = threading.Lock()
    workspace_before = manager.measure_usage(case_id)
    capacity_observation = manager.observe_capacity_claims(case_id)
    if not capacity_observation.snapshot.current.is_empty:
        capacity_observation.close()
        raise AssertionError("the controlled export began with a retained capacity claim")

    def run_export() -> None:
        try:
            export_thread_ids.append(threading.get_ident())
            with create_pdf_export_worker_composition(
                build_windows_pdf_export_worker_config(
                    resource_policy=CONSTRAINED_RESOURCE_POLICY_V1
                ),
                heavy_operations=heavy_operations,
            ) as export_composition:
                service = build_windows_pdf_export_service(
                    manager,
                    sources,
                    export_composition,
                    resources=DesktopApplicationResourceContext(
                        CONSTRAINED_RESOURCE_POLICY_V1,
                        heavy_operations,
                    ),
                )
                outcomes.append(
                    service.export_pdf(
                        case_id,
                        destination,
                        deadline=lambda: False,
                        cancellation=lambda: False,
                    )
                )
                resource_snapshot = export_composition.resource_snapshot()
                assert resource_snapshot is not None
                resource_snapshots.append(resource_snapshot)
            composition_closed_states.append(export_composition.closed)
        except BaseException as error:
            errors.append(error)
        finally:
            with timeline_lock:
                finished_times_ns.append(time.monotonic_ns())
                export_finished.set()

    def record_responsive_tick() -> None:
        should_quit = False
        with timeline_lock:
            sampled_at_ns = time.monotonic_ns()
            if export_finished.is_set():
                should_quit = True
            else:
                heartbeat_thread_ids.append(threading.get_ident())
                heartbeat_times_ns.append(sampled_at_ns)
        if should_quit:
            event_loop.quit()

    export_thread = threading.Thread(
        target=run_export,
        name="pws-m1-export-gate",
        daemon=False,
    )
    started_at_ns = 0
    capacity_claims: QuotaReservationObservationSnapshot | None = None
    try:
        timer.timeout.connect(record_responsive_tick)
        timer.start()
        started_at_ns = time.monotonic_ns()
        try:
            export_thread.start()
            event_loop.exec()
        finally:
            if export_thread.ident is not None:
                export_thread.join()

        assert export_finished.is_set()
        assert not export_thread.is_alive(), "the completed background export did not join"
        if errors:
            raise errors[0]
        workspace_after = manager.measure_usage(case_id)
    finally:
        timer.stop()
        capacity_claims = capacity_observation.close()

    assert capacity_claims is not None
    peak_cache_bytes, peak_temp_bytes = _conservative_workspace_peaks(
        workspace_before,
        workspace_after,
        capacity_claims,
    )
    assert len(outcomes) == 1
    assert len(resource_snapshots) == 1
    assert len(finished_times_ns) == 1
    assert export_thread.ident is not None
    assert export_thread_ids == [export_thread.ident]
    assert export_thread.ident != main_thread_id
    assert heartbeat_times_ns
    assert heartbeat_times_ns[-1] <= finished_times_ns[0]
    assert set(heartbeat_thread_ids) == {main_thread_id}
    assert composition_closed_states == [True]
    responsiveness_samples_ns = (
        started_at_ns,
        *heartbeat_times_ns,
        finished_times_ns[0],
    )
    heartbeat_gaps_ns = tuple(
        later - earlier for earlier, later in pairwise(responsiveness_samples_ns)
    )
    return _ResponsiveExportResult(
        outcome=outcomes[0],
        heartbeat_count=len(heartbeat_times_ns),
        maximum_heartbeat_gap_ns=max(heartbeat_gaps_ns),
        worker_resource_snapshot=resource_snapshots[0],
        peak_cache_bytes=peak_cache_bytes + gui_cache_bytes,
        peak_temp_bytes=peak_temp_bytes,
    )


def _measure_active_thumbnail_cancellation(
    stack: _VerticalSliceStack,
    case_id: UUID,
    page_id: UUID,
) -> int:
    """Cancel one active worker render and measure its bounded acknowledgement."""

    cancellation = threading.Event()
    rendering_started_ns = 0
    failure: PdfThumbnailError | None = None
    cache_before = frozenset(stack.manager.layout(case_id).thumbnails.iterdir())

    def progress(observation: PagePreviewProgress) -> None:
        nonlocal rendering_started_ns
        if observation.phase is PagePreviewProgressPhase.GENERATING and rendering_started_ns == 0:
            rendering_started_ns = time.perf_counter_ns()
            cancellation.set()

    try:
        stack.backend.get_thumbnails(
            _CANCELLATION_RENDER_DIMENSION_PIXELS,
            _CANCELLATION_RENDER_DIMENSION_PIXELS,
            page_ids=(page_id,),
            # AppContainer profile construction and authenticated resource
            # inventory are outside the cancellation acknowledgement window.
            # Give startup its production-sized budget, then start the measured
            # two-second window only at the RENDERING progress boundary.
            timeout_seconds=120.0,
            cancellation=cancellation.is_set,
            progress=progress,
        )
    except PdfThumbnailError as error:
        failure = error
    else:
        raise AssertionError("the active thumbnail render ignored cancellation")

    assert failure is not None
    assert rendering_started_ns > 0, (failure.code.value, failure.context)
    assert failure.code is PdfThumbnailErrorCode.CANCELLED
    acknowledgement_ms = stack.desktop.last_cancellation_acknowledgement_ms()
    assert acknowledgement_ms is not None
    assert acknowledgement_ms <= CONSTRAINED_RESOURCE_POLICY_V1.cancellation_acknowledgement_ms
    assert stack.desktop.resource_snapshot() is not None
    coordinator = stack.desktop.heavy_operation_snapshot()
    assert coordinator.active_operations == 0
    assert coordinator.waiting_operations == 0
    assert frozenset(stack.manager.layout(case_id).thumbnails.iterdir()) == cache_before
    return acknowledgement_ms


def _write_source_with_content_and_metadata_canaries(destination: Path) -> None:
    """Create one synthetic PDF whose private values exercise parsed data seams."""

    with pikepdf.Pdf.open(
        _SECOND_FIXTURE,
        attempt_recovery=False,
        password="",
    ) as document:
        document.pages[0].obj[pikepdf.Name("/Contents")] = pikepdf.Stream(
            document,
            (f"BT /F1 12 Tf 54 738 Td ({_DOCUMENT_TEXT_CANARY}) Tj ET\n").encode("ascii"),
        )
        document.docinfo[pikepdf.Name("/Title")] = _METADATA_CANARY
        document.save(destination, deterministic_id=True)


def _retain_gui_previews(
    model: OrganizerPageListModel,
    state: OrganizerViewState,
    results: tuple[PdfThumbnailResult, ...],
) -> int:
    """Exercise the production QPixmap LRU and return its decoded-byte charge."""

    model.set_organizer_state(state)
    for result in results:
        snapshot = result.snapshot
        image = QImage(
            snapshot.pixels,
            snapshot.width_pixels,
            snapshot.height_pixels,
            snapshot.row_stride_bytes,
            QImage.Format.Format_RGBA8888,
        ).copy()
        assert not image.isNull()
        model.set_preview(snapshot.page_id, QPixmap.fromImage(image))
    return model.retained_preview_bytes


def _require_pdf_previews(
    results: tuple[PagePreviewResult, ...],
) -> tuple[PdfThumbnailResult, ...]:
    """Narrow the canonical PDF-only M1 result batch at its runtime boundary."""

    narrowed: list[PdfThumbnailResult] = []
    for result in results:
        assert type(result) is PdfThumbnailResult
        narrowed.append(result)
    return tuple(narrowed)


def _privacy_forms(value: str) -> tuple[str, ...]:
    """Return raw and canonical JSON-escaped forms without duplicate scans."""

    escaped = json.dumps(value, ensure_ascii=True)[1:-1]
    return tuple(dict.fromkeys((value, escaped)))


def _measurement(phase: M1Phase, started_ns: int) -> PhaseMeasurement:
    return PhaseMeasurement(
        phase,
        PhaseOutcome.COMPLETED,
        max(1, time.perf_counter_ns() - started_ns),
    )


def _set_digest(paths: tuple[Path, ...]) -> str:
    identities = tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in paths)
    return hashlib.sha256(_canonical_identity_bytes(identities)).hexdigest()


def _canonical_identity_bytes(values: object) -> bytes:
    return json.dumps(
        values,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=repr,
    ).encode("ascii")


def _pdfium_version_identity() -> str:
    module = importlib.import_module("pypdfium2.version")
    return str(module.PDFIUM_INFO.version)


def _page_state(state: OrganizerViewState) -> tuple[tuple[UUID, int, int], ...]:
    return tuple((page.page_id, page.position, page.rotation_degrees) for page in state.pages)


def _thumbnail_fingerprints(
    results: tuple[PdfThumbnailResult, ...],
) -> tuple[tuple[UUID, int, int, int, str], ...]:
    return tuple(
        (
            result.snapshot.page_id,
            result.snapshot.width_pixels,
            result.snapshot.height_pixels,
            int(result.snapshot.additional_rotation),
            hashlib.sha256(result.snapshot.pixels).hexdigest(),
        )
        for result in results
    )


def _assert_verified(outcome: PdfExportOutcome, *, page_count: int) -> None:
    assert outcome.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert outcome.destination_reopened
    assert outcome.report.page_count == page_count
    assert tuple(check.code for check in outcome.checks) == _VERIFIED_CHECKS
    assert all(
        check.page_count == page_count
        for check in outcome.checks
        if check.code
        in {
            PdfExportCheckCode.PAGE_COUNT_MATCHED,
            PdfExportCheckCode.ALL_PAGES_RENDERED,
            PdfExportCheckCode.ORDER_ROTATION_MATCHED,
        }
    )


def _application() -> QApplication:
    """Return a process-wide application that remains safe for later widget tests."""

    existing = QApplication.instance()
    if existing is None:
        return QApplication([])
    assert isinstance(existing, QApplication), (
        "the integration process already owns a non-widget QCoreApplication"
    )
    return existing


def test_vertical_slice_event_loop_uses_widget_capable_process_application() -> None:
    application = _application()

    assert QCoreApplication.instance() is application
    assert isinstance(application, QApplication)


def test_workspace_peak_bound_combines_quiescent_usage_and_all_capacity_claims() -> None:
    before = WorkspaceUsage(100, 10, 2, 8, 2)
    after = WorkspaceUsage(110, 15, 3, 9, 2)
    claims = QuotaReservationObservationSnapshot(
        current=QuotaReservationSnapshot(0, 0, 0, 0),
        high_water=QuotaReservationSnapshot(11, 4, 7, 2),
    )

    assert _conservative_workspace_peaks(before, after, claims) == (19, 10)


def test_workspace_peak_bound_rejects_a_retained_capacity_claim() -> None:
    usage = WorkspaceUsage(100, 10, 2, 8, 2)
    claims = QuotaReservationObservationSnapshot(
        current=QuotaReservationSnapshot(1, 0, 1, 1),
        high_water=QuotaReservationSnapshot(1, 0, 1, 1),
    )

    with pytest.raises(AssertionError, match="retained a capacity claim"):
        _conservative_workspace_peaks(usage, usage, claims)


def test_active_cancellation_probe_fits_the_production_thumbnail_output_budget() -> None:
    """Keep the active-worker probe below every pre-render allocation gate."""

    limits = _desktop_worker_config(CONSTRAINED_RESOURCE_POLICY_V1).thumbnail_policy.limits
    raster_bytes = _CANCELLATION_RENDER_DIMENSION_PIXELS**2 * 4

    assert _CANCELLATION_RENDER_DIMENSION_PIXELS > 96
    assert raster_bytes <= limits.memory_bytes
    assert raster_bytes + PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES <= limits.output_bytes
    assert _CANCELLATION_RENDER_DIMENSION_PIXELS**2 <= limits.pixel_count


def test_active_thumbnail_worker_cancellation_is_acknowledged_within_policy(
    tmp_path: Path,
) -> None:
    """Prove the canonical cancellation probe reaches and cancels a real worker."""

    profile = tmp_path / "profile"
    profile.mkdir()
    source = tmp_path / "source.pdf"
    shutil.copyfile(_FIRST_FIXTURE, source)
    stack = _build_stack(
        profile,
        diagnostics=SanitizedDiagnosticLogger(sink=io.BytesIO()),
    )
    try:
        created = stack.backend.create_case()
        assert created.case_id is not None
        imported = stack.backend.import_source(source, "Synthetic source.pdf").committed_import

        acknowledgement_ms = _measure_active_thumbnail_cancellation(
            stack,
            created.case_id,
            imported.page_ids[0],
        )

        assert (
            1
            <= acknowledgement_ms
            <= (CONSTRAINED_RESOURCE_POLICY_V1.cancellation_acknowledgement_ms)
        )
    finally:
        stack.backend.shutdown()


def test_m1_vertical_slice_is_deterministic_recoverable_and_private(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Exercise the real M1 packet path twice across a cold application restart."""

    caplog.set_level(logging.DEBUG)
    application = _application()
    external = tmp_path / "external"
    external.mkdir()
    first_source = external / f"{_SOURCE_FILENAME_CANARY}.pdf"
    second_source = external / "synthetic-second-source.pdf"
    shutil.copyfile(_FIRST_FIXTURE, first_source)
    _write_source_with_content_and_metadata_canaries(second_source)
    external_before = {
        first_source: first_source.read_bytes(),
        second_source: second_source.read_bytes(),
    }
    with pikepdf.Pdf.open(second_source, attempt_recovery=False, password="") as canary_pdf:
        assert (
            _DOCUMENT_TEXT_CANARY.encode("ascii")
            in canary_pdf.pages[0].obj[pikepdf.Name("/Contents")].read_bytes()
        )
        assert str(canary_pdf.docinfo[pikepdf.Name("/Title")]) == _METADATA_CANARY
    profile = tmp_path / "profile"
    profile.mkdir()
    first_output = tmp_path / f"{_DESTINATION_NAME_CANARY}-first.pdf"
    second_output = tmp_path / f"{_DESTINATION_NAME_CANARY}-second.pdf"
    diagnostic_sink = io.BytesIO()
    diagnostics = SanitizedDiagnosticLogger(sink=diagnostic_sink)
    overall_started_ns = time.perf_counter_ns()
    stack = _build_stack(profile, diagnostics=diagnostics)
    restarted: _VerticalSliceStack | None = None
    observed_safe_objects: list[object] = []
    phase_measurements: list[PhaseMeasurement] = []
    workspace_measurements: list[WorkspaceUsageMeasurement] = []
    combined_cache_measurements: list[int] = []
    preview_model = OrganizerPageListModel(
        maximum_preview_cache_bytes=(CONSTRAINED_RESOURCE_POLICY_V1.thumbnail_preview_cache_bytes)
    )

    try:
        phase_started_ns = time.perf_counter_ns()
        created = stack.backend.create_case()
        assert created.case_id is not None
        case_id = created.case_id
        phase_measurements.append(_measurement(M1Phase.CASE_CREATE, phase_started_ns))
        workspace_measurements.append(
            WorkspaceUsageMeasurement.from_usage(
                WorkspaceCheckpoint.CREATED,
                stack.manager.measure_usage(case_id),
            )
        )

        phase_started_ns = time.perf_counter_ns()
        first_import = stack.backend.import_source(
            first_source,
            f"{_DISPLAY_NAME_CANARY}.pdf",
        )
        second_import = stack.backend.import_source(
            second_source,
            "Synthetic second source.pdf",
        )
        observed_safe_objects.extend((first_import, second_import))
        first = first_import.committed_import
        second = second_import.committed_import
        assert len(first.page_ids) == 2
        assert len(second.page_ids) == 1
        assert first.sha256 == hashlib.sha256(external_before[first_source]).hexdigest()
        assert second.sha256 == hashlib.sha256(external_before[second_source]).hexdigest()
        source_inspection_worker_snapshot = stack.desktop.resource_snapshot()
        assert source_inspection_worker_snapshot is not None
        phase_measurements.append(_measurement(M1Phase.PDF_IMPORT, phase_started_ns))
        workspace_measurements.append(
            WorkspaceUsageMeasurement.from_usage(
                WorkspaceCheckpoint.IMPORTED,
                stack.manager.measure_usage(case_id),
            )
        )

        initial_ids = (*first.page_ids, *second.page_ids)
        initial = stack.backend.state
        assert tuple(page.page_id for page in initial.pages) == initial_ids
        phase_started_ns = time.perf_counter_ns()
        initial_previews = _require_pdf_previews(
            stack.backend.get_thumbnails(
                96,
                96,
                page_ids=initial_ids,
            )
        )
        assert tuple(result.snapshot.page_id for result in initial_previews) == initial_ids
        assert all(result.snapshot.pixels for result in initial_previews)
        assert all(
            result.snapshot.width_pixels <= 96 and result.snapshot.height_pixels <= 96
            for result in initial_previews
        )
        gui_cache_bytes = _retain_gui_previews(preview_model, initial, initial_previews)
        combined_cache_measurements.append(
            stack.manager.measure_usage(case_id).cache_bytes + gui_cache_bytes
        )
        assert all(
            result.cache_key.renderer_profile_sha256 == PDF_THUMBNAIL_RENDERER_PROFILE_SHA256
            for result in initial_previews
        )
        phase_measurements.append(_measurement(M1Phase.THUMBNAIL_RENDER, phase_started_ns))

        phase_started_ns = time.perf_counter_ns()
        reordered_ids = (second.page_ids[0], first.page_ids[1], first.page_ids[0])
        reordered = stack.backend.reorder_pages(reordered_ids)
        assert tuple(page.page_id for page in reordered.pages) == reordered_ids
        undone = stack.backend.undo()
        assert tuple(page.page_id for page in undone.pages) == initial_ids
        assert undone.can_redo
        redone = stack.backend.redo()
        assert tuple(page.page_id for page in redone.pages) == reordered_ids
        assert redone.can_undo and not redone.can_redo

        rotated = stack.backend.rotate_clockwise(first.page_ids[1])
        assert rotated.pages[1].rotation_degrees == 90
        removed = stack.backend.remove_page(first.page_ids[0])
        final_ids = (second.page_ids[0], first.page_ids[1])
        assert tuple(page.page_id for page in removed.pages) == final_ids
        assert removed.autosave_pending
        saved = stack.backend.flush_autosave()
        assert not saved.autosave_pending
        expected_page_state = (
            (second.page_ids[0], 1, 0),
            (first.page_ids[1], 2, 90),
        )
        assert _page_state(saved) == expected_page_state
        final_previews = _require_pdf_previews(
            stack.backend.get_thumbnails(96, 96, page_ids=final_ids)
        )
        final_thumbnail_fingerprints = _thumbnail_fingerprints(final_previews)
        gui_cache_bytes = _retain_gui_previews(preview_model, saved, final_previews)
        combined_cache_measurements.append(
            stack.manager.measure_usage(case_id).cache_bytes + gui_cache_bytes
        )
        thumbnail_worker_snapshot = stack.desktop.resource_snapshot()
        assert thumbnail_worker_snapshot is not None
        phase_measurements.append(_measurement(M1Phase.ORGANIZE, phase_started_ns))
        workspace_measurements.append(
            WorkspaceUsageMeasurement.from_usage(
                WorkspaceCheckpoint.ORGANIZED,
                stack.manager.measure_usage(case_id),
            )
        )
        observed_safe_objects.extend(
            (
                saved,
                initial_previews,
                final_previews,
                source_inspection_worker_snapshot,
                thumbnail_worker_snapshot,
            )
        )

        layout = stack.manager.layout(case_id)
        committed_paths = {
            first.source_id: layout.originals / f"{first.source_id}.bin",
            second.source_id: layout.originals / f"{second.source_id}.bin",
        }
        committed_before = {
            source_id: path.read_bytes() for source_id, path in committed_paths.items()
        }
        assert committed_before[first.source_id] == external_before[first_source]
        assert committed_before[second.source_id] == external_before[second_source]

        first_snapshot = WindowsManifestRepository(stack.manager).load(case_id).snapshot
        first_plan = build_pdf_export_plan(first_snapshot)
        assert tuple(page.page_id for page in first_plan.pages) == final_ids
        assert tuple(
            (page.source_id, page.source_page_index, int(page.additional_rotation))
            for page in first_plan.pages
        ) == (
            (second.source_id, 0, 0),
            (first.source_id, 1, 90),
        )
        phase_started_ns = time.perf_counter_ns()
        first_export_result = _export_pdf_while_event_loop_remains_responsive(
            application,
            stack.manager,
            stack.sources,
            case_id,
            first_output,
            stack.desktop.heavy_operations,
            gui_cache_bytes,
        )
        first_export = first_export_result.outcome
        _assert_verified(first_export, page_count=2)
        assert first_export.report.semantic_fingerprint == first_plan.semantic_fingerprint
        phase_measurements.append(_measurement(M1Phase.VERIFIED_EXPORT, phase_started_ns))
        workspace_measurements.append(
            WorkspaceUsageMeasurement.from_usage(
                WorkspaceCheckpoint.EXPORTED,
                stack.manager.measure_usage(case_id),
            )
        )
        observed_safe_objects.extend((first_plan, first_export_result))

        phase_started_ns = time.perf_counter_ns()
        stack.backend.shutdown()
        preview_model.evict_previews(0)
        restarted = _build_stack(profile, diagnostics=diagnostics)
        recovery = restarted.backend.startup_recovery_result
        assert recovery.status is StartupRecoveryStatus.COMPLETE
        assert recovery.scanned_cases == 1
        assert recovery.cases[0].case_id == case_id
        assert recovery.cases[0].status is CaseRecoveryStatus.CURRENT
        assert not recovery.cases[0].issues

        reopened = restarted.backend.open_case(case_id)
        assert _page_state(reopened) == expected_page_state
        assert not reopened.autosave_pending
        assert not reopened.recovery_review_required
        recovered_previews = _require_pdf_previews(
            restarted.backend.get_thumbnails(96, 96, page_ids=final_ids)
        )
        assert _thumbnail_fingerprints(recovered_previews) == final_thumbnail_fingerprints
        preview_model = OrganizerPageListModel(
            maximum_preview_cache_bytes=(
                CONSTRAINED_RESOURCE_POLICY_V1.thumbnail_preview_cache_bytes
            )
        )
        gui_cache_bytes = _retain_gui_previews(preview_model, reopened, recovered_previews)
        combined_cache_measurements.append(
            restarted.manager.measure_usage(case_id).cache_bytes + gui_cache_bytes
        )

        recovered_snapshot = WindowsManifestRepository(restarted.manager).load(case_id).snapshot
        recovered_plan = build_pdf_export_plan(recovered_snapshot)
        assert recovered_plan.semantic_fingerprint == first_plan.semantic_fingerprint
        assert tuple(page.page_id for page in recovered_plan.pages) == final_ids
        phase_measurements.append(_measurement(M1Phase.CASE_REOPEN_RECOVERY, phase_started_ns))
        workspace_measurements.append(
            WorkspaceUsageMeasurement.from_usage(
                WorkspaceCheckpoint.RECOVERED,
                restarted.manager.measure_usage(case_id),
            )
        )
        second_export_result = _export_pdf_while_event_loop_remains_responsive(
            application,
            restarted.manager,
            restarted.sources,
            case_id,
            second_output,
            restarted.desktop.heavy_operations,
            gui_cache_bytes,
        )
        second_export = second_export_result.outcome
        _assert_verified(second_export, page_count=2)
        assert second_export.report.semantic_fingerprint == first_plan.semantic_fingerprint
        assert second_export.report.output_sha256 == first_export.report.output_sha256
        assert second_output.read_bytes() == first_output.read_bytes()
        cancellation_acknowledgement_ms = _measure_active_thumbnail_cancellation(
            restarted,
            case_id,
            final_ids[0],
        )
        observed_safe_objects.extend(
            (recovery, reopened, recovered_previews, recovered_plan, second_export_result)
        )

        with pikepdf.Pdf.open(first_output, attempt_recovery=False, password="") as first_pdf:
            assert len(first_pdf.pages) == 2
            assert not first_pdf.is_encrypted
        with pikepdf.Pdf.open(second_output, attempt_recovery=False, password="") as second_pdf:
            assert len(second_pdf.pages) == 2
            assert not second_pdf.is_encrypted

        history = WindowsPdfExportHistoryRepository(restarted.manager).load(case_id).document
        assert len(history.records) == 2
        linked_history = (
            WindowsManifestRepository(restarted.manager)
            .load(case_id)
            .snapshot.document["export_history"]
        )
        assert type(linked_history) is dict
        assert len(linked_history["entries"]) == 2
    finally:
        if restarted is not None:
            restarted.backend.shutdown()
        stack.backend.shutdown()

    for source_path, before in external_before.items():
        assert source_path.read_bytes() == before
    for source_id, path in committed_paths.items():
        assert path.read_bytes() == committed_before[source_id]

    layout = stack.manager.layout(case_id)
    assert tuple(layout.temp.iterdir()) == ()
    assert tuple(layout.case_root.rglob("*.tmp")) == ()
    assert tuple(first_output.parent.glob(".*.pws-export.tmp")) == ()

    diagnostic_records = diagnostics.snapshot_records()
    diagnostic_jsonl_bytes = diagnostic_sink.getvalue()
    assert len(diagnostic_records) == 4
    assert diagnostics.dropped_records == 0
    assert diagnostics.sink_failures == 0
    assert diagnostic_jsonl_bytes == b"".join(diagnostics.snapshot_lines())
    diagnostic_jsonl = diagnostic_jsonl_bytes.decode("ascii")
    diagnostic_summary = diagnostics.snapshot_summary(generated_at=_DIAGNOSTIC_TIME)
    assert diagnostic_summary.record_count == len(diagnostic_records)
    rendered_summary = render_diagnostic_summary(diagnostic_summary)
    captured_streams = capfd.readouterr()
    private_tokens = (
        _SOURCE_FILENAME_CANARY,
        _DISPLAY_NAME_CANARY,
        _DESTINATION_NAME_CANARY,
        _DOCUMENT_TEXT_CANARY,
        _METADATA_CANARY,
        str(first_source),
        str(second_source),
        str(first_output),
        str(second_output),
        str(profile),
    )
    safe_representations = "\n".join(repr(value) for value in observed_safe_objects)
    scanned_surfaces = (
        caplog.text,
        diagnostic_jsonl,
        rendered_summary,
        captured_streams.out,
        captured_streams.err,
        safe_representations,
    )
    for token in private_tokens:
        for form in _privacy_forms(token):
            assert all(form not in surface for surface in scanned_surfaces)

    selected_export_snapshot = max(
        (
            first_export_result.worker_resource_snapshot,
            second_export_result.worker_resource_snapshot,
        ),
        key=lambda snapshot: (
            snapshot.peak_job_memory_used_bytes,
            snapshot.peak_process_memory_used_bytes,
            snapshot.job_memory_limit_bytes,
            snapshot.process_memory_limit_bytes,
        ),
    )
    worker_measurements = (
        WorkerResourceMeasurement.from_snapshot(
            WorkerOperation.SOURCE_INSPECTION,
            source_inspection_worker_snapshot,
        ),
        WorkerResourceMeasurement.from_snapshot(
            WorkerOperation.THUMBNAIL_RENDER,
            thumbnail_worker_snapshot,
        ),
        WorkerResourceMeasurement.from_snapshot(
            WorkerOperation.VERIFIED_EXPORT,
            selected_export_snapshot,
        ),
    )
    emit_live_child_artifacts(
        phases=tuple(phase_measurements),
        workers=worker_measurements,
        workspaces=tuple(workspace_measurements),
        evidence={
            "fixture_set_sha256": _set_digest((_FIRST_FIXTURE, _SECOND_FIXTURE)),
            "source_set_sha256": _set_digest((first_source, second_source)),
            "python_version": platform.python_version(),
            "pikepdf_version": pikepdf.__version__,
            "qpdf_version": production_qpdf_version_identity(),
            "pdfium_version": _pdfium_version_identity(),
            "qt_version": qVersion(),
            "worker_config_sha256": production_worker_config_digest(),
            "renderer_profile_sha256": PDF_THUMBNAIL_RENDERER_PROFILE_SHA256,
            "manifest_schema_version": CURRENT_MANIFEST_SCHEMA_VERSION,
            "worker_protocol_version": PROTOCOL_VERSION,
            "export_profile_version": PDF_EXPORT_PROFILE_VERSION,
            "workload_version": 1,
            "total_elapsed_ns": max(1, time.perf_counter_ns() - overall_started_ns),
            "source_count": 2,
            "input_bytes": first_source.stat().st_size + second_source.stat().st_size,
            "source_page_count": 3,
            "active_page_count": 2,
            "preview_count": len(initial_previews),
            "output_count": 2,
            "output_page_count": first_export.report.page_count,
            "output_bytes": second_output.stat().st_size,
            "output_sha256": second_export.report.output_sha256,
            "semantic_fingerprint": second_export.report.semantic_fingerprint,
            "deterministic_semantic_equal": True,
            "deterministic_output_equal": True,
            "verifier_codes": tuple(code.value for code in _VERIFIED_CHECKS),
            "recovery_status": recovery.cases[0].status.value,
            "case_reopened": True,
            "reopened_page_count": len(reopened.pages),
            "reopened_sequence_equal": _page_state(reopened) == expected_page_state,
            "privacy_scan_status": "passed",
            "privacy_canary_count": 5,
            "background_export_completed": True,
            "heartbeat_count": (
                first_export_result.heartbeat_count + second_export_result.heartbeat_count
            ),
            "maximum_heartbeat_gap_ns": max(
                first_export_result.maximum_heartbeat_gap_ns,
                second_export_result.maximum_heartbeat_gap_ns,
            ),
            "peak_cache_bytes": max(
                *combined_cache_measurements,
                *(item.cache_bytes for item in workspace_measurements),
                first_export_result.peak_cache_bytes,
                second_export_result.peak_cache_bytes,
            ),
            "max_heavy_operation_concurrency": max(
                stack.desktop.heavy_operation_snapshot().maximum_observed_concurrency,
                restarted.desktop.heavy_operation_snapshot().maximum_observed_concurrency,
            ),
            "cancellation_acknowledgement_ms": cancellation_acknowledgement_ms,
            "workers_terminated": True,
            "worker_recycle_policy": "one-shot",
            "peak_temp_bytes": max(
                *(item.temp_bytes for item in workspace_measurements),
                first_export_result.peak_temp_bytes,
                second_export_result.peak_temp_bytes,
            ),
        },
        derivative_source=second_output,
    )

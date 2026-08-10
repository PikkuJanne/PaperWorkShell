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
from PySide6.QtWidgets import QApplication

from paperworkshell.__main__ import (
    _desktop_worker_config,
    _import_limits,
    _workspace_quota,
    build_windows_pdf_export_service,
    build_windows_pdf_export_worker_config,
    build_windows_startup_recovery_service,
)
from paperworkshell.app.case_lifecycle import CaseLifecycleService
from paperworkshell.app.organizer import OrganizerBackend, OrganizerViewState
from paperworkshell.app.pdf_thumbnails import PdfThumbnailService
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
from paperworkshell.domain.pdf_thumbnails import PdfThumbnailResult
from paperworkshell.domain.recovery import CaseRecoveryStatus, StartupRecoveryStatus
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.export_history import WindowsPdfExportHistoryRepository
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.source_ingest import WindowsSourceIngestStore
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.thumbnail_cache import WindowsThumbnailCacheStore
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.worker.client.models import WorkerResourceSnapshot
from paperworkshell.worker.desktop_composition import (
    DesktopWorkerComposition,
    create_desktop_worker_composition,
    create_pdf_export_worker_composition,
)
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


def _build_stack(
    profile: Path,
    *,
    diagnostics: SanitizedDiagnosticLogger,
) -> _VerticalSliceStack:
    manager = CaseWorkspaceManager(profile_root=profile, quota=_workspace_quota())
    limits = _import_limits()
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
        _desktop_worker_config(),
        prepared_sources=sources,
        committed_pages=sources,
        thumbnail_cache=WindowsThumbnailCacheStore(manager),
    )
    try:
        backend = OrganizerBackend(
            CaseLifecycleService(WindowsCaseLifecycleStore(manager)),
            WindowsManifestRepository(manager),
            SourceIngestService(sources, SourceInspectorRouter(desktop.source_inspector)),
            PdfThumbnailService(desktop.thumbnail_generator),
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

    def run_export() -> None:
        try:
            export_thread_ids.append(threading.get_ident())
            with create_pdf_export_worker_composition(
                build_windows_pdf_export_worker_config()
            ) as export_composition:
                service = build_windows_pdf_export_service(
                    manager,
                    sources,
                    export_composition,
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
    timer.timeout.connect(record_responsive_tick)
    timer.start()
    started_at_ns = time.monotonic_ns()
    try:
        export_thread.start()
        event_loop.exec()
    finally:
        timer.stop()

    assert export_finished.is_set()
    export_thread.join()
    assert not export_thread.is_alive(), "the completed background export did not join"
    if errors:
        raise errors[0]
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
    )


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
        initial_previews = stack.backend.get_thumbnails(
            96,
            96,
            page_ids=initial_ids,
        )
        assert tuple(result.snapshot.page_id for result in initial_previews) == initial_ids
        assert all(result.snapshot.pixels for result in initial_previews)
        assert all(
            result.snapshot.width_pixels <= 96 and result.snapshot.height_pixels <= 96
            for result in initial_previews
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
        final_previews = stack.backend.get_thumbnails(96, 96, page_ids=final_ids)
        final_thumbnail_fingerprints = _thumbnail_fingerprints(final_previews)
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
        recovered_previews = restarted.backend.get_thumbnails(96, 96, page_ids=final_ids)
        assert _thumbnail_fingerprints(recovered_previews) == final_thumbnail_fingerprints

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
        )
        second_export = second_export_result.outcome
        _assert_verified(second_export, page_count=2)
        assert second_export.report.semantic_fingerprint == first_plan.semantic_fingerprint
        assert second_export.report.output_sha256 == first_export.report.output_sha256
        assert second_output.read_bytes() == first_output.read_bytes()
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
        },
        derivative_source=second_output,
    )

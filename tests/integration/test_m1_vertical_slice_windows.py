"""Live Milestone 1 case-to-export-and-recovery gate on Windows."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pikepdf
import pytest

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
from paperworkshell.app.pdf_export import PdfExportService
from paperworkshell.app.pdf_thumbnails import PdfThumbnailService
from paperworkshell.app.source_ingest import SourceIngestService
from paperworkshell.app.source_inspection import SourceInspectorRouter
from paperworkshell.diagnostics.logging import SanitizedDiagnosticLogger
from paperworkshell.diagnostics.summary import render_diagnostic_summary
from paperworkshell.domain.pdf_export import (
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
from paperworkshell.worker.desktop_composition import (
    create_desktop_worker_composition,
    create_pdf_export_worker_composition,
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
    exports: PdfExportService


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
        export = create_pdf_export_worker_composition(build_windows_pdf_export_worker_config())
    except BaseException:
        desktop.close()
        raise

    def close_resources() -> None:
        try:
            export.close()
        finally:
            desktop.close()

    try:
        backend = OrganizerBackend(
            CaseLifecycleService(WindowsCaseLifecycleStore(manager)),
            WindowsManifestRepository(manager),
            SourceIngestService(sources, SourceInspectorRouter(desktop.source_inspector)),
            PdfThumbnailService(desktop.thumbnail_generator),
            close_resources=close_resources,
            startup_recovery=startup_recovery,
        )
        exports = build_windows_pdf_export_service(manager, sources, export)
    except BaseException:
        close_resources()
        raise
    return _VerticalSliceStack(manager, sources, backend, exports)


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


def test_m1_vertical_slice_is_deterministic_recoverable_and_private(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Exercise the real M1 packet path twice across a cold application restart."""

    caplog.set_level(logging.DEBUG)
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
    stack = _build_stack(profile, diagnostics=diagnostics)
    restarted: _VerticalSliceStack | None = None
    observed_safe_objects: list[object] = []

    try:
        created = stack.backend.create_case()
        assert created.case_id is not None
        case_id = created.case_id

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

        initial_ids = (*first.page_ids, *second.page_ids)
        initial = stack.backend.state
        assert tuple(page.page_id for page in initial.pages) == initial_ids
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
        observed_safe_objects.extend((saved, initial_previews, final_previews))

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
        first_export = stack.exports.export_pdf(
            case_id,
            first_output,
            deadline=lambda: False,
            cancellation=lambda: False,
        )
        _assert_verified(first_export, page_count=2)
        assert first_export.report.semantic_fingerprint == first_plan.semantic_fingerprint
        observed_safe_objects.extend((first_plan, first_export))

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
        second_export = restarted.exports.export_pdf(
            case_id,
            second_output,
            deadline=lambda: False,
            cancellation=lambda: False,
        )
        _assert_verified(second_export, page_count=2)
        assert second_export.report.semantic_fingerprint == first_plan.semantic_fingerprint
        assert second_export.report.output_sha256 == first_export.report.output_sha256
        assert second_output.read_bytes() == first_output.read_bytes()
        observed_safe_objects.extend(
            (recovery, reopened, recovered_previews, recovered_plan, second_export)
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

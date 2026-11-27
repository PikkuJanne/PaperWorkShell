"""Live page-range export through the production desktop backend and worker."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from pathlib import Path

import pikepdf
import pypdfium2  # type: ignore[import-untyped]
import pytest

import paperworkshell.__main__ as desktop
from paperworkshell.app.organizer import OrganizerPagesPdfExportOutcome
from paperworkshell.domain.pdf_export import (
    PdfExportCheckCode,
    PdfExportError,
    PdfExportErrorCode,
    PdfExportSelection,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.storage.export_history import WindowsPdfExportHistoryRepository
from paperworkshell.storage.workspace import CaseWorkspaceManager
from tests.integration.test_package_pilot import _configure_staged_runtime
from tests.worker_resources import WorkerTestResources

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows restricted document worker")

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/synthetic/v1/pdf"


def _render_digest(path: Path, index: int, *, rotation: int = 0) -> tuple[tuple[int, int], str]:
    """Read the output with an independent document open and pixel comparison."""

    with pypdfium2.PdfDocument(path) as reader:
        page = reader[index]
        try:
            bitmap = page.render(scale=1, rotation=rotation)
            try:
                with bitmap.to_pil() as image:
                    return image.size, hashlib.sha256(image.tobytes()).hexdigest()
            finally:
                bitmap.close()
        finally:
            page.close()


def test_ordinary_range_exports_only_the_reordered_pages_and_preserves_the_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_test_resources: WorkerTestResources,
) -> None:
    _configure_staged_runtime(monkeypatch, worker_test_resources)
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    resources = desktop.create_desktop_application_resource_context(policy)
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=desktop._workspace_quota(policy),
        minimum_free_space_reserve_bytes=policy.minimum_export_free_space_reserve_bytes,
    )
    external = tmp_path / "sources"
    external.mkdir()
    calibration = external / "render-calibration.pdf"
    digital = external / "born-digital.pdf"
    for path in (calibration, digital):
        path.write_bytes((_FIXTURES / path.name).read_bytes())
    source_before = {path: path.read_bytes() for path in (calibration, digital)}
    out = tmp_path / "exports"
    out.mkdir()
    history = WindowsPdfExportHistoryRepository(manager)
    backend = desktop._build_backend(manager, resources=resources)
    try:
        created = backend.create_case("Synthetic range")
        assert created.case_id is not None
        case_id = created.case_id
        first = backend.import_source(calibration, calibration.name).committed_import
        second = backend.import_source(digital, digital.name).committed_import
        skipped_id, rotated_id = first.page_ids
        (digital_id,) = second.page_ids
        assembled = (skipped_id, digital_id, rotated_id)
        backend.reorder_pages(assembled)
        backend.rotate_clockwise(rotated_id)
        saved = backend.flush_autosave()
        assert saved.revision is not None
        assert tuple(page.page_id for page in saved.pages) == assembled
        assert saved.pages[2].rotation_degrees == 90

        layout = manager.layout(case_id)
        originals = {
            layout.originals / f"{first.source_id}.bin": source_before[calibration],
            layout.originals / f"{second.source_id}.bin": source_before[digital],
        }
        manifest_before = layout.manifest.read_bytes()
        selection = PdfExportSelection(case_id, saved.revision, (digital_id, rotated_id))

        def export(
            destination: Path,
            selected: PdfExportSelection,
            *,
            cancellation: Callable[[], bool] = lambda: False,
        ) -> OrganizerPagesPdfExportOutcome:
            return backend.export_pages_pdf(
                destination,
                selection=selected,
                cancellation=cancellation,
                progress=lambda _value: None,
            )

        occupied = out / "occupied.pdf"
        sentinel = b"synthetic occupied destination"
        occupied.write_bytes(sentinel)
        noncontiguous = PdfExportSelection(case_id, saved.revision, (skipped_id, rotated_id))
        with pytest.raises(PdfExportError) as invalid:
            export(out / "invalid.pdf", noncontiguous)
        assert invalid.value.code is PdfExportErrorCode.RANGE_INVALID
        with pytest.raises(PdfExportError) as cancelled:
            export(out / "cancelled.pdf", selection, cancellation=lambda: True)
        assert cancelled.value.code is PdfExportErrorCode.CANCELLED
        with pytest.raises(PdfExportError) as collision:
            export(occupied, selection)
        assert collision.value.code is PdfExportErrorCode.DESTINATION_EXISTS
        assert tuple(out.iterdir()) == (occupied,)
        assert occupied.read_bytes() == sentinel
        assert layout.manifest.read_bytes() == manifest_before
        assert history.load(case_id).document.records == ()

        destination = out / "selected-range.pdf"
        result = export(destination, selection)
        verified = result.committed_export
        assert verified.report.page_count == 2
        assert verified.report.output_sha256 == hashlib.sha256(destination.read_bytes()).hexdigest()
        assert {
            PdfExportCheckCode.PDF_REOPENED,
            PdfExportCheckCode.ALL_PAGES_RENDERED,
            PdfExportCheckCode.OUTPUT_COMMIT_REOPENED,
        }.issubset(check.code for check in verified.checks)
        with (
            pikepdf.Pdf.open(destination, attempt_recovery=False) as pdf,
            pikepdf.Pdf.open(digital, attempt_recovery=False) as expected_first,
            pikepdf.Pdf.open(calibration, attempt_recovery=False) as expected_last,
        ):
            assert len(pdf.pages) == 2
            assert not pdf.is_encrypted
            assert tuple(pdf.pages[0].mediabox) == tuple(expected_first.pages[0].mediabox)
            assert tuple(pdf.pages[1].mediabox) == tuple(expected_last.pages[1].mediabox)
            assert pdf.pages[0].rotation == expected_first.pages[0].rotation
            assert pdf.pages[1].rotation == (expected_last.pages[1].rotation + 90) % 360
        assert _render_digest(destination, 0) == _render_digest(digital, 0)
        assert _render_digest(destination, 1) == _render_digest(calibration, 1, rotation=90)
        assert tuple(page.page_id for page in result.state.pages) == assembled
        (record,) = history.load(case_id).document.records
        assert record.manifest_revision == saved.revision
        assert record.page_count == 2
        assert record.output_sha256 == verified.report.output_sha256

        with pytest.raises(PdfExportError) as stale:
            export(out / "stale.pdf", selection)
        assert stale.value.code is PdfExportErrorCode.PLAN_STALE
        assert {path.name for path in out.iterdir()} == {"occupied.pdf", "selected-range.pdf"}
        assert occupied.read_bytes() == sentinel
        for path, expected in (source_before | originals).items():
            assert path.read_bytes() == expected

        backend.close_case()
        reopened = backend.open_case(case_id)
        assert tuple(page.page_id for page in reopened.pages) == assembled
        assert reopened.pages[2].rotation_degrees == 90
        assert reopened.trace_saved_export is not None
        assert reopened.trace_saved_export.page_count == 2
        assert resources.heavy_operations.snapshot().active_operations == 0
    finally:
        backend.shutdown()

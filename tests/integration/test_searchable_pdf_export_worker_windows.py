"""Live PWS-0206 proof through the constrained mixed searchable-PDF worker."""

from __future__ import annotations

import copy
import hashlib
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import cast
from uuid import UUID

import pikepdf
import pypdfium2  # type: ignore[import-untyped]
import pytest
from packaging.requirements import Requirement

from paperworkshell.app.export_history import PdfExportHistoryService
from paperworkshell.app.searchable_pdf_export import SearchablePdfExportService
from paperworkshell.app.source_ingest import SourceIngestService
from paperworkshell.domain.import_precheck import DetectedMediaType, ImportLimits
from paperworkshell.domain.pdf_export import (
    PdfExportOutcomeStatus,
    PdfExportProgress,
    PdfExportProgressPhase,
)
from paperworkshell.domain.searchable_pdf_export import (
    SearchablePdfPageOutcomeCode,
    SearchablePdfPageSearchability,
)
from paperworkshell.domain.source_ingest import (
    CompletedSourceInspection,
    PreparedSourceSummary,
    SourceEncryptionState,
    SourceSignatureState,
)
from paperworkshell.export.pdf_destination import WindowsVerifiedPdfDestination
from paperworkshell.pdf.qpdf_checker import DEFAULT_QPDF_EXECUTABLE
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.export_history import WindowsPdfExportHistoryRepository
from paperworkshell.storage.export_retry import WindowsPdfExportRetryRepository
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.source_ingest import WindowsSourceIngestStore
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.worker.client.models import WorkerTimeoutPolicy
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.protocol.models import ResourceLimits
from paperworkshell.worker.searchable_pdf_export_composition import (
    create_searchable_pdf_export_worker_composition,
    create_searchable_pdf_export_worker_composition_config,
)
from tests.worker_resources import WorkerTestResources

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0206 targets the supported Windows AppContainer boundary",
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MIXED_FIXTURE = (
    _PROJECT_ROOT / "tests" / "fixtures" / "synthetic" / "v1" / "pdf" / "ocr-readable-mixed.pdf"
)
_IMAGE_FIXTURE = (
    _PROJECT_ROOT / "tests" / "fixtures" / "synthetic" / "v1" / "image" / "ocr-raster-text.png"
)
_CASE_ID = UUID("20600000-0000-4000-8000-000000000206")
_NOW = datetime(2026, 8, 13, 16, 0, tzinfo=UTC)
_BORN_DIGITAL_TOKEN = "DIGITAL PAGE 206"


class _SequentialUuidFactory:
    def __init__(self, prefix: int) -> None:
        self._prefix = prefix
        self._next = 1

    def __call__(self) -> UUID:
        value = UUID(f"{self._prefix:08x}-0000-4000-8000-{self._next:012x}")
        self._next += 1
        return value


@dataclass(frozen=True, slots=True)
class _MixedInspector:
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
        if source.media_type is DetectedMediaType.PDF:
            return CompletedSourceInspection(
                pending_id=source.pending_id,
                media_type=source.media_type,
                page_count=2,
                signature_state=SourceSignatureState.NONE_DETECTED,
                encryption_state=SourceEncryptionState.UNENCRYPTED,
            )
        assert source.media_type is DetectedMediaType.PNG
        return CompletedSourceInspection(
            pending_id=source.pending_id,
            media_type=source.media_type,
            page_count=1,
            signature_state=SourceSignatureState.NOT_APPLICABLE,
            encryption_state=SourceEncryptionState.NOT_APPLICABLE,
        )


def _stage_ocr_python_closure(resources: WorkerTestResources) -> None:
    destination_root = resources.runtime_root / "Lib" / "site-packages"
    destination_root.mkdir(exist_ok=True)
    pending = ["ocrmypdf"]
    completed: set[str] = set()
    while pending:
        selected = pending.pop()
        normalized = selected.casefold().replace("_", "-")
        if normalized in completed:
            continue
        try:
            package = distribution(selected)
        except PackageNotFoundError:
            pytest.fail("the locked OCRmyPDF test dependency closure is unavailable")
        completed.add(normalized)
        for raw in package.requires or ():
            requirement = Requirement(raw)
            if requirement.marker is None or requirement.marker.evaluate():
                pending.append(requirement.name)
        package_root = Path(str(package.locate_file(""))).resolve()
        for entry in package.files or ():
            relative = Path(entry)
            source = Path(str(package.locate_file(entry))).resolve()
            try:
                contained = source.relative_to(package_root)
            except ValueError:
                continue
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not source.is_file()
                or source.suffix.casefold() in {".pyc", ".pyo"}
                or "__pycache__" in contained.parts
            ):
                continue
            destination = destination_root / contained
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                assert destination.is_file()
                assert source.read_bytes() == destination.read_bytes()
            else:
                shutil.copyfile(source, destination)


def _runtime_from_environment() -> tuple[Path, Path]:
    executable = os.environ.get("PWS_TESSERACT_EXECUTABLE")
    tessdata = os.environ.get("PWS_TESSDATA_ROOT")
    if not executable or not tessdata:
        pytest.skip("the admitted PWS-0204 Tesseract runtime is not staged")
    return Path(executable), Path(tessdata)


def _qpdf_executable() -> Path:
    configured = os.environ.get("PWS_QPDF_EXECUTABLE")
    selected = Path(configured) if configured else DEFAULT_QPDF_EXECUTABLE
    assert selected.is_absolute() and selected.is_file(), "required qpdf 12.3.2 is unavailable"
    return selected


def _import_limits() -> ImportLimits:
    return ImportLimits(
        maximum_source_bytes=8_000_000,
        maximum_pdf_pages=10,
        maximum_image_width=10_000,
        maximum_image_height=10_000,
        maximum_image_pixels=20_000_000,
        maximum_decompressed_bytes=80_000_000,
        decompression_warning_ratio=100,
        maximum_decompression_ratio=2_000,
        maximum_header_bytes=4_096,
        maximum_structure_records=128,
    )


def _workspace_quota() -> WorkspaceQuota:
    return WorkspaceQuota(
        maximum_case_bytes=128_000_000,
        maximum_cache_bytes=16_000_000,
        maximum_temp_bytes=64_000_000,
        maximum_entries=2_048,
        maximum_depth=16,
    )


def _document_without_history_facts(document: dict[str, object]) -> dict[str, object]:
    selected = copy.deepcopy(document)
    selected.pop("revision")
    selected.pop("updated_at")
    selected.pop("export_history")
    return selected


def _extract_page_text(path: Path) -> tuple[str, ...]:
    document = pypdfium2.PdfDocument(path)
    try:
        result: list[str] = []
        for index in range(len(document)):
            page = document[index]
            try:
                text_page = page.get_textpage()
                try:
                    result.append(text_page.get_text_range())
                finally:
                    text_page.close()
            finally:
                page.close()
        return tuple(result)
    finally:
        document.close()


def _rendered_page_bytes(path: Path, page_index: int) -> bytes:
    document = pypdfium2.PdfDocument(path)
    try:
        page = document[page_index]
        try:
            bitmap = page.render(scale=1.0)
            try:
                image = bitmap.to_pil()
                try:
                    return cast(bytes, image.convert("RGB").tobytes())
                finally:
                    image.close()
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        document.close()


def test_live_searchable_mixed_export_preserves_text_ocrs_images_and_verifies_output(
    tmp_path: Path,
    worker_test_resources: WorkerTestResources,
) -> None:
    _stage_ocr_python_closure(worker_test_resources)
    tesseract, tessdata = _runtime_from_environment()
    qpdf = _qpdf_executable()
    profile_root = tmp_path / "profile"
    profile_root.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile_root,
        quota=_workspace_quota(),
        id_factory=lambda: _CASE_ID,
    )
    WindowsCaseLifecycleStore(manager, clock=lambda: _NOW).create_case().close()
    sources = WindowsSourceIngestStore(
        manager,
        WindowsSourcePrechecker(_import_limits()),
        maximum_page_count=10,
        clock=lambda: _NOW,
        id_factory=_SequentialUuidFactory(0x20610000),
        temp_id_factory=_SequentialUuidFactory(0x20620000),
    )
    ingest = SourceIngestService(sources, _MixedInspector())
    imported_pdf = ingest.import_source(
        _CASE_ID,
        _MIXED_FIXTURE,
        "PRIVATE-MIXED-OUTPUT-CANARY.pdf",
    )
    imported_image = ingest.import_source(
        _CASE_ID,
        _IMAGE_FIXTURE,
        "PRIVATE-IMAGE-OUTPUT-CANARY.png",
    )
    external_bytes = (_MIXED_FIXTURE.read_bytes(), _IMAGE_FIXTURE.read_bytes())
    case_paths = (
        manager.layout(_CASE_ID).originals / f"{imported_pdf.source_id}.bin",
        manager.layout(_CASE_ID).originals / f"{imported_image.source_id}.bin",
    )
    case_bytes = tuple(path.read_bytes() for path in case_paths)
    manifests = WindowsManifestRepository(
        manager,
        id_factory=_SequentialUuidFactory(0x20630000),
    )
    manifest_before = manifests.load(_CASE_ID).snapshot.document

    heavy_operations = HeavyOperationCoordinator(1)
    limits = ResourceLimits(
        wall_time_ms=180_000,
        cpu_time_ms=150_000,
        memory_bytes=512 * 1024 * 1024,
        temporary_storage_bytes=256 * 1024 * 1024,
        output_bytes=32 * 1024 * 1024,
        page_count=10,
        pixel_count=20_000_000,
    )
    composition = create_searchable_pdf_export_worker_composition(
        create_searchable_pdf_export_worker_composition_config(
            python_executable=worker_test_resources.python_executable,
            bootstrap_script=(
                worker_test_resources.source_root
                / "paperworkshell"
                / "worker"
                / "host_bootstrap.py"
            ),
            runtime_roots=(worker_test_resources.runtime_root,),
            code_roots=(worker_test_resources.source_root,),
            working_directory=worker_test_resources.source_root,
            qpdf_executable=qpdf,
            tesseract_executable=tesseract,
            tessdata_root=tessdata,
            resource_policy=WorkerResourcePolicy(
                process_memory_bytes=512 * 1024 * 1024,
                job_memory_bytes=768 * 1024 * 1024,
                cpu_time_ms=150_000,
                maximum_processes=2,
            ),
            timeout_policy=WorkerTimeoutPolicy(
                handshake_seconds=20.0,
                cancellation_grace_seconds=5.0,
                shutdown_grace_seconds=10.0,
            ),
            maximum_heavy_operation_concurrency=1,
            maximum_active_full_resolution_page_buffers=1,
            resource_sampling_interval_ms=10,
            limits=limits,
            status_capacity_bytes=1024 * 1024,
            pump_interval_seconds=0.01,
        ),
        heavy_operations=heavy_operations,
    )
    history_repository = WindowsPdfExportHistoryRepository(
        manager,
        id_factory=_SequentialUuidFactory(0x20640000),
    )
    service = SearchablePdfExportService(
        manifests,
        sources,
        composition.exporter,
        WindowsVerifiedPdfDestination(),
        PdfExportHistoryService(history_repository, manifests),
        WindowsPdfExportRetryRepository(
            manager,
            id_factory=_SequentialUuidFactory(0x20650000),
        ),
        clock=lambda: _NOW,
        id_factory=_SequentialUuidFactory(0x20660000),
    )
    output = tmp_path / "verified-searchable-mixed.pdf"
    progress: list[PdfExportProgress] = []
    try:
        outcome = service.export_pdf(
            _CASE_ID,
            output,
            deadline=lambda: False,
            cancellation=lambda: False,
            progress=progress.append,
        )
    finally:
        composition.close()

    assert outcome.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert outcome.destination_reopened is True
    assert outcome.report.all_pages_have_extractable_text is True
    assert outcome.report.existing_text_page_count == 1
    assert outcome.report.ocr_text_page_count == 2
    assert outcome.report.needs_review_page_count == 0
    optimization = outcome.report.base_report.optimization
    assert optimization is not None
    assert optimization.selected_byte_size == output.stat().st_size
    assert optimization.selected_byte_size <= optimization.baseline_byte_size
    assert tuple(item.outcome for item in outcome.report.page_outcomes) == (
        SearchablePdfPageOutcomeCode.EXISTING_TEXT_PRESERVED,
        SearchablePdfPageOutcomeCode.OCR_TEXT_VERIFIED,
        SearchablePdfPageOutcomeCode.OCR_TEXT_VERIFIED,
    )
    assert tuple(item.searchability for item in outcome.report.page_outcomes) == (
        SearchablePdfPageSearchability.EXTRACTABLE_TEXT_PRESENT_EXISTING,
        SearchablePdfPageSearchability.EXTRACTABLE_TEXT_PRESENT_OCR,
        SearchablePdfPageSearchability.EXTRACTABLE_TEXT_PRESENT_OCR,
    )
    texts = _extract_page_text(output)
    assert _BORN_DIGITAL_TOKEN in texts[0]
    # OCR accuracy is not a PWS-0206 claim. The trusted worker reports only
    # that pinned PDFium found non-whitespace extractable text on both pages.
    assert texts[1].strip()
    assert texts[2].strip()
    assert _rendered_page_bytes(_MIXED_FIXTURE, 0) == _rendered_page_bytes(output, 0)
    with pikepdf.Pdf.open(output, attempt_recovery=False, password="") as document:
        assert len(document.pages) == 3
        assert not document.is_encrypted
        page = document.pages[0]
        assert "/Font" in page.obj.Resources
        assert not page.get_images()

    assert progress[0].phase is PdfExportProgressPhase.PREPARING
    assert progress[-1].phase is PdfExportProgressPhase.COMPLETE
    assert _MIXED_FIXTURE.read_bytes() == external_bytes[0]
    assert _IMAGE_FIXTURE.read_bytes() == external_bytes[1]
    assert tuple(path.read_bytes() for path in case_paths) == case_bytes
    manifest_after = manifests.load(_CASE_ID).snapshot.document
    assert _document_without_history_facts(manifest_after) == _document_without_history_facts(
        manifest_before
    )
    assert len(history_repository.load(_CASE_ID).document.records) == 1
    assert manager.measure_usage(_CASE_ID).temp_bytes == 0
    assert (
        hashlib.sha256(output.read_bytes()).hexdigest() == outcome.report.base_report.output_sha256
    )

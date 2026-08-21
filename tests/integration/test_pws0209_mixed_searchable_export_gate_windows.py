"""Live PWS-0209 mixed-source searchable-export milestone evidence."""

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
from PIL import Image

from paperworkshell.__main__ import (
    _SEARCHABLE_PDF_STATUS_BYTES,
    _import_limits,
    _workspace_quota,
    build_windows_searchable_pdf_export_limits,
    build_windows_searchable_pdf_export_service,
    create_desktop_application_resource_context,
)
from paperworkshell.app.source_ingest import SourceIngestService
from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.pdf_export import (
    PdfExportCheckCode,
    PdfExportError,
    PdfExportOutcomeStatus,
    PdfExportProgress,
    PdfExportProgressPhase,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
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
from paperworkshell.pdf.qpdf_checker import DEFAULT_QPDF_EXECUTABLE
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.export_history import WindowsPdfExportHistoryRepository
from paperworkshell.storage.export_retry import WindowsPdfExportRetryRepository
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.source_ingest import WindowsSourceIngestStore
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.worker.client.models import WorkerTimeoutPolicy
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.searchable_pdf_export_composition import (
    create_searchable_pdf_export_worker_composition,
    create_searchable_pdf_export_worker_composition_config,
)
from tests.worker_resources import WorkerTestResources

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0209 live OCR evidence targets the supported Windows AppContainer",
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_MIXED_PDF = _PROJECT_ROOT / "tests/fixtures/synthetic/v1/pdf/ocr-readable-mixed.pdf"
_REPOSITORY_PNG = _PROJECT_ROOT / "tests/fixtures/synthetic/v1/image/ocr-raster-text.png"
_ADMITTED_STAGE_NAME = "pws0209-admitted-runtime-20260821"
_CASE_ID = UUID("20900000-0000-4000-8000-000000000209")
_NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
_BORN_DIGITAL_TOKEN = "DIGITAL PAGE 206"
_RASTER_TOKEN = "SCAN PAGE 206"
_JPEG_SHA256 = "5d84a0181e2ae81c695123f3c8db712d7e359c3765fc525fb1bc07e9e2f9cbd9"
_PRIVATE_DISPLAY_NAMES = (
    "PRIVATE-PWS0209-MIXED-CANARY.pdf",
    "PRIVATE-PWS0209-JPEG-CANARY.jpg",
    "PRIVATE-PWS0209-PNG-CANARY.png",
)


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
    """Return exact metadata for the three already content-admitted sources."""

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
                source.pending_id,
                source.media_type,
                2,
                SourceSignatureState.NONE_DETECTED,
                SourceEncryptionState.UNENCRYPTED,
            )
        assert source.media_type in {DetectedMediaType.JPEG, DetectedMediaType.PNG}
        return CompletedSourceInspection(
            source.pending_id,
            source.media_type,
            1,
            SourceSignatureState.NOT_APPLICABLE,
            SourceEncryptionState.NOT_APPLICABLE,
        )


def _stage_ocr_python_closure(resources: WorkerTestResources) -> None:
    """Copy only OCRmyPDF's locked transitive Python distributions."""

    destination_root = resources.runtime_root / "Lib/site-packages"
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


def _admitted_ocr_runtime() -> tuple[Path, Path]:
    """Select only the maintainer-provided PWS-0209 runtime stage."""

    local_app_data = os.environ.get("LOCALAPPDATA")
    if type(local_app_data) is not str or not local_app_data:
        pytest.skip("the exact PWS-0209 admitted OCR environment is absent")
    root = Path(local_app_data) / "Temp" / _ADMITTED_STAGE_NAME
    executable = root / "bin/tesseract.exe"
    tessdata = root / "share/tessdata"
    required = (
        executable,
        tessdata,
        tessdata / "eng.traineddata",
        tessdata / "configs/pdf",
        tessdata / "configs/hocr",
        tessdata / "configs/txt",
    )
    if not root.exists():
        pytest.skip("the exact PWS-0209 admitted OCR environment is absent")
    assert root.is_absolute() and root.is_dir()
    assert executable.is_file()
    assert tessdata.is_dir()
    assert all(path.is_file() for path in required[2:])
    return executable.resolve(strict=True), tessdata.resolve(strict=True)


def _qpdf_executable() -> Path:
    configured = os.environ.get("PWS_QPDF_EXECUTABLE")
    selected = Path(configured) if configured else DEFAULT_QPDF_EXECUTABLE
    assert selected.is_absolute() and selected.is_file(), "required qpdf 12.3.2 is unavailable"
    return selected.resolve(strict=True)


def _write_deterministic_jpeg(path: Path) -> bytes:
    with Image.open(_REPOSITORY_PNG) as source:
        image = source.convert("RGB")
    try:
        image.save(
            path,
            format="JPEG",
            quality=95,
            subsampling=0,
            optimize=False,
            progressive=False,
        )
    finally:
        image.close()
    payload = path.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == _JPEG_SHA256
    return payload


def _manifest_without_export_history(document: dict[str, object]) -> dict[str, object]:
    selected = copy.deepcopy(document)
    selected.pop("revision")
    selected.pop("updated_at")
    selected.pop("export_history")
    return selected


def _extract_all_page_text(path: Path) -> tuple[str, ...]:
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


def _render_all_page_sha256(path: Path) -> tuple[str, ...]:
    document = pypdfium2.PdfDocument(path)
    try:
        rendered: list[str] = []
        for index in range(len(document)):
            page = document[index]
            try:
                bitmap = page.render(scale=1.0)
                try:
                    image = bitmap.to_pil()
                    try:
                        rgb = image.convert("RGB")
                        try:
                            payload = cast(bytes, rgb.tobytes())
                            assert rgb.width > 0 and rgb.height > 0 and payload
                            rendered.append(hashlib.sha256(payload).hexdigest())
                        finally:
                            rgb.close()
                    finally:
                        image.close()
                finally:
                    bitmap.close()
            finally:
                page.close()
        return tuple(rendered)
    finally:
        document.close()


def _assert_retry_inventory_empty(repository: WindowsPdfExportRetryRepository) -> None:
    inventory = repository.inventory(_CASE_ID)
    assert not inventory.committed
    assert not inventory.prepared
    assert not inventory.invalid_export_ids
    assert inventory.invalid_entries == 0


def _assert_content_free(payload: str | bytes, forbidden: tuple[str, ...]) -> None:
    selected = payload.encode("utf-8") if isinstance(payload, str) else payload
    folded = selected.lower()
    for token in forbidden:
        assert token.encode("utf-8").lower() not in folded


def test_live_mixed_pdf_jpeg_png_export_is_searchable_verified_and_content_free(
    tmp_path: Path,
    worker_test_resources: WorkerTestResources,
) -> None:
    """Exercise one complete production mixed-source export in AppContainer."""

    tesseract, tessdata = _admitted_ocr_runtime()
    qpdf = _qpdf_executable()
    _stage_ocr_python_closure(worker_test_resources)
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    application_resources = create_desktop_application_resource_context(
        policy,
        tesseract_executable=tesseract,
        tessdata_root=tessdata,
    )
    assert application_resources.ocr_languages.refresh().selected_available is True

    jpeg = tmp_path / "private-pws0209-generated-canary.jpg"
    jpeg_bytes = _write_deterministic_jpeg(jpeg)
    external_paths = (_MIXED_PDF, jpeg, _REPOSITORY_PNG)
    external_bytes = (_MIXED_PDF.read_bytes(), jpeg_bytes, _REPOSITORY_PNG.read_bytes())

    profile_root = tmp_path / "profile"
    profile_root.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile_root,
        quota=_workspace_quota(policy),
        id_factory=lambda: _CASE_ID,
    )
    WindowsCaseLifecycleStore(manager, clock=lambda: _NOW).create_case().close()
    sources = WindowsSourceIngestStore(
        manager,
        WindowsSourcePrechecker(_import_limits(policy)),
        maximum_page_count=policy.maximum_page_count,
        clock=lambda: _NOW,
        id_factory=_SequentialUuidFactory(0x20910000),
        temp_id_factory=_SequentialUuidFactory(0x20920000),
    )
    ingest = SourceIngestService(sources, _MixedInspector())
    imported = tuple(
        ingest.import_source(_CASE_ID, path, display_name)
        for path, display_name in zip(external_paths, _PRIVATE_DISPLAY_NAMES, strict=True)
    )
    assert tuple(item.media_type for item in imported) == (
        DetectedMediaType.PDF,
        DetectedMediaType.JPEG,
        DetectedMediaType.PNG,
    )
    assert tuple(len(item.page_ids) for item in imported) == (2, 1, 1)
    layout = manager.layout(_CASE_ID)
    case_paths = tuple(layout.originals / f"{item.source_id}.bin" for item in imported)
    case_bytes = tuple(path.read_bytes() for path in case_paths)
    manifests = WindowsManifestRepository(manager)
    manifest_before = manifests.load(_CASE_ID).snapshot

    composition = create_searchable_pdf_export_worker_composition(
        create_searchable_pdf_export_worker_composition_config(
            python_executable=worker_test_resources.python_executable,
            bootstrap_script=(
                worker_test_resources.source_root / "paperworkshell/worker/host_bootstrap.py"
            ),
            runtime_roots=(worker_test_resources.runtime_root,),
            code_roots=(worker_test_resources.source_root,),
            working_directory=worker_test_resources.source_root,
            qpdf_executable=qpdf,
            tesseract_executable=tesseract,
            tessdata_root=tessdata,
            resource_policy=WorkerResourcePolicy(
                process_memory_bytes=policy.worker_process_memory_bytes,
                job_memory_bytes=policy.worker_job_memory_bytes,
                cpu_time_ms=policy.worker_cpu_time_ms,
                maximum_processes=policy.worker_maximum_processes,
            ),
            timeout_policy=WorkerTimeoutPolicy(
                handshake_seconds=20.0,
                cancellation_grace_seconds=policy.cancellation_acknowledgement_ms / 1_000,
                shutdown_grace_seconds=10.0,
            ),
            maximum_heavy_operation_concurrency=policy.maximum_heavy_operation_concurrency,
            maximum_active_full_resolution_page_buffers=(
                policy.maximum_active_full_resolution_page_buffers
            ),
            resource_sampling_interval_ms=policy.resource_sampling_interval_ms,
            pump_interval_seconds=policy.resource_sampling_interval_ms / 1_000,
            limits=build_windows_searchable_pdf_export_limits(policy),
            status_capacity_bytes=_SEARCHABLE_PDF_STATUS_BYTES,
        ),
        heavy_operations=application_resources.heavy_operations,
    )
    output = tmp_path / "private-pws0209-verified-output-canary.pdf"
    progress: list[PdfExportProgress] = []
    try:
        try:
            outcome = build_windows_searchable_pdf_export_service(
                manager,
                sources,
                composition,
                resources=application_resources,
            ).export_pdf(
                _CASE_ID,
                output,
                deadline=lambda: False,
                cancellation=lambda: False,
                progress=progress.append,
            )
        except PdfExportError as error:
            pytest.fail(
                "the searchable worker failed with content-free state: "
                f"{error!r}; result={composition.last_operation_result()!r}; "
                f"resource={composition.resource_snapshot()!r}; "
                f"private_temp={composition.private_temp_snapshot()!r}; "
                f"tree_terminated={composition.worker_tree_termination_confirmed()!r}; "
                f"progress={tuple(progress)!r}"
            )
        worker_result = composition.last_operation_result()
        private_temp = composition.private_temp_snapshot()
        worker_tree_terminated = composition.worker_tree_termination_confirmed()
    finally:
        composition.close()

    assert outcome.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert outcome.destination_reopened is True
    assert outcome.report.plan.page_count == 4
    assert outcome.report.all_pages_have_extractable_text is True
    assert outcome.report.existing_text_page_count == 1
    assert outcome.report.ocr_text_page_count == 3
    assert outcome.report.needs_review_page_count == 0
    assert outcome.report.base_report.unsigned_derivative is False
    assert tuple(item.outcome for item in outcome.report.page_outcomes) == (
        SearchablePdfPageOutcomeCode.EXISTING_TEXT_PRESERVED,
        SearchablePdfPageOutcomeCode.OCR_TEXT_VERIFIED,
        SearchablePdfPageOutcomeCode.OCR_TEXT_VERIFIED,
        SearchablePdfPageOutcomeCode.OCR_TEXT_VERIFIED,
    )
    assert tuple(item.searchability for item in outcome.report.page_outcomes) == (
        SearchablePdfPageSearchability.EXTRACTABLE_TEXT_PRESENT_EXISTING,
        SearchablePdfPageSearchability.EXTRACTABLE_TEXT_PRESENT_OCR,
        SearchablePdfPageSearchability.EXTRACTABLE_TEXT_PRESENT_OCR,
        SearchablePdfPageSearchability.EXTRACTABLE_TEXT_PRESENT_OCR,
    )
    assert tuple(check.code for check in outcome.checks) == (
        PdfExportCheckCode.PDF_REOPENED,
        PdfExportCheckCode.STRUCTURE_CLEAN,
        PdfExportCheckCode.PAGE_COUNT_MATCHED,
        PdfExportCheckCode.UNENCRYPTED,
        PdfExportCheckCode.ALL_PAGES_RENDERED,
        PdfExportCheckCode.ORDER_ROTATION_MATCHED,
        PdfExportCheckCode.OUTPUT_COMMIT_REOPENED,
    )
    assert progress[0].phase is PdfExportProgressPhase.PREPARING
    assert progress[-1].phase is PdfExportProgressPhase.COMPLETE
    assert progress[-1].completed_pages == 4

    texts = _extract_all_page_text(output)
    assert len(texts) == 4
    assert _BORN_DIGITAL_TOKEN in texts[0]
    assert all(text.strip() for text in texts)
    rendered = _render_all_page_sha256(output)
    assert len(rendered) == 4 and all(len(digest) == 64 for digest in rendered)
    assert _render_all_page_sha256(_MIXED_PDF)[0] == rendered[0]
    with pikepdf.Pdf.open(output, attempt_recovery=False, password="") as reopened:
        assert len(reopened.pages) == 4
        assert not reopened.is_encrypted
    assert hashlib.sha256(output.read_bytes()).hexdigest() == (
        outcome.report.base_report.output_sha256
    )

    assert tuple(path.read_bytes() for path in external_paths) == external_bytes
    assert tuple(path.read_bytes() for path in case_paths) == case_bytes
    manifest_after = manifests.load(_CASE_ID).snapshot
    assert manifest_after.revision == manifest_before.revision + 1
    assert _manifest_without_export_history(
        manifest_after.document
    ) == _manifest_without_export_history(manifest_before.document)
    history = WindowsPdfExportHistoryRepository(manager).load(_CASE_ID).document
    assert len(history.records) == 1
    record = history.records[0]
    assert record.export_id == outcome.export_id
    assert record.manifest_revision == manifest_before.revision
    assert record.manifest_sha256 == manifest_before.sha256
    assert record.output_sha256 == outcome.report.base_report.output_sha256
    manifest_history = manifest_after.document["export_history"]
    assert type(manifest_history) is dict
    assert manifest_history["entries"] == [record.manifest_reference]

    assert worker_result is not None
    assert worker_tree_terminated is True
    assert private_temp is not None
    assert private_temp.measurement_complete is True
    assert not tuple(layout.temp.iterdir())
    assert manager.measure_usage(_CASE_ID).temp_bytes == 0
    retry_repository = WindowsPdfExportRetryRepository(manager)
    _assert_retry_inventory_empty(retry_repository)

    report_surface = "\n".join(
        (
            repr(outcome),
            repr(outcome.report),
            repr(outcome.report.base_report),
            repr(outcome.report.page_outcomes),
            repr(worker_result),
            repr(history),
            *(item.claim for item in outcome.checks),
            *outcome.claims,
            *(repr(item) for item in progress),
        )
    )
    forbidden = (
        *_PRIVATE_DISPLAY_NAMES,
        jpeg.name,
        output.name,
        str(tmp_path),
        str(profile_root),
        *(str(path) for path in case_paths),
        _BORN_DIGITAL_TOKEN,
        _RASTER_TOKEN,
    )
    _assert_content_free(report_surface, forbidden)
    _assert_content_free(layout.export_history.read_bytes(), forbidden)

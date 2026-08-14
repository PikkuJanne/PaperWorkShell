"""Measured PWS-0208 signed-source and incremental-resource evidence workload."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass, replace
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pikepdf
import pytest
from packaging.requirements import Requirement

import paperworkshell.worker.searchable_pdf_export_operation as worker_operation
from paperworkshell.__main__ import (
    DesktopApplicationResourceContext,
    _import_limits,
    _workspace_quota,
    build_windows_searchable_pdf_export_limits,
    build_windows_searchable_pdf_export_service,
    create_desktop_application_resource_context,
)
from paperworkshell.app.signed_source_policy import (
    SignedSourceConfirmationChoice,
    SignedSourceConfirmationOutcomeStatus,
    SignedSourceConfirmationService,
)
from paperworkshell.app.source_ingest import SourceIngestService
from paperworkshell.domain.incremental_processing import IncrementalProcessingSnapshot
from paperworkshell.domain.ocr import OcrAdapterResult
from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportOutcomeStatus,
    PdfExportProgress,
    PdfExportProgressPhase,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.domain.searchable_pdf_export import (
    SearchablePdfExportPlan,
    SearchablePdfPage,
    SearchablePdfSource,
    SearchablePdfSourceEncryptionState,
    SearchablePdfSourceMediaType,
    SearchablePdfSourceSignatureState,
)
from paperworkshell.domain.signed_source_policy import (
    SignedSourceModificationSource,
    SignedSourceReviewStatus,
    SignedSourceSignatureState,
)
from paperworkshell.domain.source_ingest import (
    CompletedSourceInspection,
    PreparedSourceSummary,
    SourceEncryptionState,
    SourceSignatureState,
)
from paperworkshell.pdf.qpdf_checker import DEFAULT_QPDF_EXECUTABLE, QpdfCommandChecker
from paperworkshell.pdf.searchable_export_adapter import (
    SearchablePdfExportAdapter,
    SearchablePdfExportAdapterControl,
)
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.export_history import WindowsPdfExportHistoryRepository
from paperworkshell.storage.export_retry import WindowsPdfExportRetryRepository
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.source_ingest import WindowsSourceIngestStore
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.worker.client.models import (
    WorkerPrivateTempSnapshot,
    WorkerResourceSnapshot,
    WorkerTimeoutPolicy,
)
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.protocol.models import (
    ErrorCategory,
    ErrorCode,
    OperationOutcome,
    ResourceLimits,
)
from paperworkshell.worker.searchable_pdf_export_composition import (
    SearchablePdfExportWorkerComposition,
    create_searchable_pdf_export_worker_composition,
    create_searchable_pdf_export_worker_composition_config,
)
from tests.worker_resources import WorkerTestResources
from tools.pws0208_resource_evidence import (
    PWS0208_WORKLOAD_PAGE_COUNT,
    PWS0208_WORKLOAD_SOURCE_COUNT,
    Pws0208ResourceEvidence,
    encode_pws0208_resource_evidence,
    validate_workload,
)
from tools.pws0208_resource_live_runner import (
    CHILD_ADAPTER_PROBE_FILENAME,
    CHILD_ARTIFACT_DIRECTORY_ENVIRONMENT,
    CHILD_DERIVATIVE_FILENAME,
    CHILD_REPORT_FILENAME,
    CHILD_REVISION_ENVIRONMENT,
)

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0208 live evidence targets the supported Windows AppContainer boundary",
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKLOAD_PATH = PROJECT_ROOT / "benchmarks/pws0208-searchable-mixed-workload-v1.json"
MIXED_FIXTURE = PROJECT_ROOT / "tests/fixtures/synthetic/v1/pdf/ocr-readable-mixed.pdf"
IMAGE_FIXTURE = PROJECT_ROOT / "tests/fixtures/synthetic/v1/image/ocr-raster-text.png"
MARKER_FIXTURE = PROJECT_ROOT / "tests/fixtures/synthetic/v1/pdf/signed-marker.pdf"
CASE_ID = UUID("20800000-0000-4000-8000-000000000001")
_STATUS_CAPACITY_BYTES = 2 * 1024 * 1024


def _uuid(value: int) -> UUID:
    return UUID(f"20800000-0000-4000-8000-{value:012x}")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact_directory() -> Path:
    raw = os.environ.get(CHILD_ARTIFACT_DIRECTORY_ENVIRONMENT)
    revision = os.environ.get(CHILD_REVISION_ENVIRONMENT)
    if not raw or not revision:
        pytest.skip("the PWS-0208 live runner did not provide its private artifact stage")
    selected = Path(raw)
    assert selected.is_absolute() and selected.is_dir()
    return selected


def _revision() -> str:
    selected = os.environ.get(CHILD_REVISION_ENVIRONMENT)
    assert selected is not None and len(selected) == 40
    assert all(character in "0123456789abcdef" for character in selected)
    return selected


def _require_exact_public_workload() -> None:
    validate_workload(WORKLOAD_PATH)
    document = json.loads(WORKLOAD_PATH.read_text(encoding="utf-8"))
    expected = {item["fixture_id"]: item["sha256"] for item in document["sources"]}
    assert expected == {
        "pdf-ocr-readable-mixed": _sha256(MIXED_FIXTURE),
        "png-ocr-raster-text": _sha256(IMAGE_FIXTURE),
        "pdf-signed-marker": _sha256(MARKER_FIXTURE),
    }
    assert document["source_count"] == PWS0208_WORKLOAD_SOURCE_COUNT
    assert document["page_count"] == PWS0208_WORKLOAD_PAGE_COUNT


def _derive_ten_page_pdf(destination: Path) -> None:
    with (
        pikepdf.Pdf.open(MIXED_FIXTURE, attempt_recovery=False, password="") as source,
        pikepdf.Pdf.new() as output,
    ):
        assert len(source.pages) == 2
        for _repeat in range(5):
            output.pages.append(source.pages[0])
            output.pages.append(source.pages[1])
        output.save(destination, deterministic_id=True)
    with pikepdf.Pdf.open(destination, attempt_recovery=False, password="") as reopened:
        assert len(reopened.pages) == 10


def _source(
    source_id: UUID,
    path: Path,
    media_type: SearchablePdfSourceMediaType,
    page_count: int,
    signature_state: SearchablePdfSourceSignatureState,
    encryption_state: SearchablePdfSourceEncryptionState,
) -> SearchablePdfSource:
    payload = path.read_bytes()
    return SearchablePdfSource(
        source_id=source_id,
        media_type=media_type,
        byte_size=len(payload),
        page_count=page_count,
        sha256=hashlib.sha256(payload).hexdigest(),
        signature_state=signature_state,
        encryption_state=encryption_state,
    )


def _direct_workload_plan(
    derived: Path,
) -> tuple[SearchablePdfExportPlan, dict[UUID, Path]]:
    pdf_id, image_id, marker_id = _uuid(10), _uuid(11), _uuid(12)
    sources = (
        _source(
            pdf_id,
            derived,
            SearchablePdfSourceMediaType.PDF,
            10,
            SearchablePdfSourceSignatureState.NONE_DETECTED,
            SearchablePdfSourceEncryptionState.UNENCRYPTED,
        ),
        _source(
            image_id,
            IMAGE_FIXTURE,
            SearchablePdfSourceMediaType.PNG,
            1,
            SearchablePdfSourceSignatureState.NOT_APPLICABLE,
            SearchablePdfSourceEncryptionState.NOT_APPLICABLE,
        ),
        _source(
            marker_id,
            MARKER_FIXTURE,
            SearchablePdfSourceMediaType.PDF,
            1,
            SearchablePdfSourceSignatureState.MARKERS_DETECTED,
            SearchablePdfSourceEncryptionState.UNENCRYPTED,
        ),
    )
    references = tuple((pdf_id, index) for index in range(10)) + (
        (image_id, 0),
        (marker_id, 0),
    )
    pages = tuple(
        SearchablePdfPage(
            output_page_index=index,
            page_id=_uuid(100 + index),
            source_id=source_id,
            source_page_index=source_page,
            quarter_turns_clockwise=0,
            crop=None,
            deskew_angle_millidegrees_clockwise=0,
            contrast_factor_millionths=1_000_000,
            background_cleanup_strength_millionths=0,
        )
        for index, (source_id, source_page) in enumerate(references)
    )
    plan = SearchablePdfExportPlan(
        case_id=_uuid(2),
        manifest_revision=1,
        manifest_sha256="a" * 64,
        manifest_cas_token="pws0208-adapter-probe-binding",
        sources=sources,
        pages=pages,
    )
    return plan, {
        pdf_id: derived.resolve(),
        image_id: IMAGE_FIXTURE.resolve(),
        marker_id: MARKER_FIXTURE.resolve(),
    }


@dataclass(frozen=True, slots=True)
class _SyntheticEvidenceOcr:
    """Deterministic test seam; the live AppContainer test uses admitted OCRmyPDF."""

    def process(self, source_path: Path, output_path: Path, **_kwargs: object) -> OcrAdapterResult:
        with pikepdf.Pdf.open(
            source_path,
            attempt_recovery=False,
            suppress_warnings=True,
        ) as document:
            page = document.pages[0]
            resources = page.obj.get(pikepdf.Name.Resources)
            if not isinstance(resources, pikepdf.Dictionary):
                resources = pikepdf.Dictionary()
                page.obj.Resources = resources
            fonts = resources.get(pikepdf.Name.Font)
            if not isinstance(fonts, pikepdf.Dictionary):
                fonts = pikepdf.Dictionary()
                resources.Font = fonts
            fonts.Fprobe = document.make_indirect(
                pikepdf.Dictionary(
                    Type=pikepdf.Name.Font,
                    Subtype=pikepdf.Name.Type1,
                    BaseFont=pikepdf.Name.Helvetica,
                )
            )
            page.contents_add(b"q BT /Fprobe 8 Tf 3 Tr 1 1 Td (probe) Tj ET Q\n")
            document.save(output_path, deterministic_id=True)
        payload = output_path.read_bytes()
        return OcrAdapterResult(
            byte_size=len(payload),
            page_count=1,
            sha256=hashlib.sha256(payload).hexdigest(),
            ocrmypdf_version="17.10.0",
            tesseract_version="5.5.3",
        )


def _qpdf_executable() -> Path:
    configured = os.environ.get("PWS_QPDF_EXECUTABLE")
    selected = Path(configured) if configured else DEFAULT_QPDF_EXECUTABLE
    assert selected.is_absolute() and selected.is_file(), "required qpdf 12.3.2 is unavailable"
    return selected


def test_production_adapter_reports_incremental_scheduler_peaks(tmp_path: Path) -> None:
    """Observe scheduler peaks at the production adapter seam, outside RPC."""

    _artifact_directory()
    _require_exact_public_workload()
    derived = tmp_path / "derived-ten-pages.pdf"
    _derive_ten_page_pdf(derived)
    plan, source_paths = _direct_workload_plan(derived)
    limits = build_windows_searchable_pdf_export_limits(CONSTRAINED_RESOURCE_POLICY_V1)
    adapter_limits = worker_operation._adapter_limits(
        cast(Any, SimpleNamespace(limits=limits)),
        plan,
        _STATUS_CAPACITY_BYTES,
    )
    snapshots: list[IncrementalProcessingSnapshot] = []
    temporary = tmp_path / "adapter-temp"
    temporary.mkdir()
    output = tmp_path / "adapter-output.pdf"
    report = SearchablePdfExportAdapter(
        ocr_processor=cast(Any, _SyntheticEvidenceOcr()),
        qpdf_checker=QpdfCommandChecker(executable=_qpdf_executable()),
    ).assemble_and_verify(
        plan,
        source_paths,
        output,
        temporary,
        languages=("eng",),
        limits=adapter_limits,
        control=SearchablePdfExportAdapterControl(time.monotonic() + 300),
        resource_observer=snapshots.append,
    )
    assert report.plan.page_count == 12
    assert report.base_report.unsigned_derivative is True
    assert snapshots
    assert snapshots[-1].completed_pages == 12
    peak_buffers = max(item.peak_full_resolution_page_buffers for item in snapshots)
    peak_results = max(item.peak_page_results for item in snapshots)
    assert peak_buffers == 1
    assert peak_results == 1
    assert all(
        item.active_full_resolution_page_buffers <= 1 and item.active_page_results <= 1
        for item in snapshots
    )
    assert not tuple(temporary.iterdir())
    probe = (
        json.dumps(
            {
                "peak_active_full_resolution_page_buffers": peak_buffers,
                "peak_active_page_results": peak_results,
            },
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        + b"\n"
    )
    with (_artifact_directory() / CHILD_ADAPTER_PROBE_FILENAME).open("xb") as stream:
        stream.write(probe)


@dataclass(frozen=True, slots=True)
class _WorkloadInspector:
    derived_sha256: str

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
        if source.media_type.value == "png":
            return CompletedSourceInspection(
                source.pending_id,
                source.media_type,
                1,
                SourceSignatureState.NOT_APPLICABLE,
                SourceEncryptionState.NOT_APPLICABLE,
            )
        if source.sha256 == self.derived_sha256:
            return CompletedSourceInspection(
                source.pending_id,
                source.media_type,
                10,
                SourceSignatureState.NONE_DETECTED,
                SourceEncryptionState.UNENCRYPTED,
            )
        assert source.sha256 == _sha256(MARKER_FIXTURE)
        return CompletedSourceInspection(
            source.pending_id,
            source.media_type,
            1,
            SourceSignatureState.MARKERS_DETECTED,
            SourceEncryptionState.UNENCRYPTED,
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


def _ocr_runtime() -> tuple[Path, Path]:
    executable = os.environ.get("PWS_TESSERACT_EXECUTABLE")
    tessdata = os.environ.get("PWS_TESSDATA_ROOT")
    if not executable or not tessdata:
        pytest.skip("the admitted PWS-0204 Tesseract runtime is not staged")
    selected_executable, selected_data = Path(executable), Path(tessdata)
    assert selected_executable.is_absolute() and selected_executable.is_file()
    assert selected_data.is_absolute() and selected_data.is_dir()
    return selected_executable, selected_data


def _composition(
    resources: WorkerTestResources,
    application_resources: DesktopApplicationResourceContext,
    qpdf: Path,
    tesseract: Path,
    tessdata: Path,
    limits: ResourceLimits,
) -> SearchablePdfExportWorkerComposition:
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    config = create_searchable_pdf_export_worker_composition_config(
        python_executable=resources.python_executable,
        bootstrap_script=resources.source_root / "paperworkshell/worker/host_bootstrap.py",
        runtime_roots=(resources.runtime_root,),
        code_roots=(resources.source_root,),
        working_directory=resources.source_root,
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
        limits=limits,
        status_capacity_bytes=_STATUS_CAPACITY_BYTES,
        maximum_active_full_resolution_page_buffers=(
            policy.maximum_active_full_resolution_page_buffers
        ),
        resource_sampling_interval_ms=policy.resource_sampling_interval_ms,
        pump_interval_seconds=policy.resource_sampling_interval_ms / 1_000,
    )
    return create_searchable_pdf_export_worker_composition(
        config,
        heavy_operations=application_resources.heavy_operations,
    )


def _capture_snapshots(
    composition: SearchablePdfExportWorkerComposition,
) -> tuple[WorkerResourceSnapshot, WorkerPrivateTempSnapshot]:
    resource = composition.resource_snapshot()
    private_temp = composition.private_temp_snapshot()
    assert resource is not None
    assert private_temp is not None
    assert private_temp.sample_count >= 1
    assert private_temp.measurement_complete is True
    return resource, private_temp


def _manifest_core(document: dict[str, object]) -> dict[str, object]:
    selected = copy.deepcopy(document)
    for name in ("revision", "updated_at", "confirmations", "export_history"):
        selected.pop(name)
    return selected


def _retry_inventory_empty(repository: WindowsPdfExportRetryRepository) -> bool:
    inventory = repository.inventory(CASE_ID)
    return (
        not inventory.committed
        and not inventory.prepared
        and not inventory.invalid_export_ids
        and inventory.invalid_entries == 0
    )


def _write_child_artifact(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def test_live_constrained_signed_source_resource_workflow(
    tmp_path: Path,
    worker_test_resources: WorkerTestResources,
) -> None:
    """Measure success, page-boundary cancel, correlated limit, and fresh retry."""

    workflow_started_ns = time.perf_counter_ns()
    artifact_directory = _artifact_directory()
    _require_exact_public_workload()
    probe = json.loads(
        (artifact_directory / CHILD_ADAPTER_PROBE_FILENAME).read_text(encoding="ascii")
    )
    assert set(probe) == {
        "peak_active_full_resolution_page_buffers",
        "peak_active_page_results",
    }
    assert probe["peak_active_full_resolution_page_buffers"] == 1
    assert probe["peak_active_page_results"] == 1
    _stage_ocr_python_closure(worker_test_resources)
    tesseract, tessdata = _ocr_runtime()
    qpdf = _qpdf_executable()
    derived = tmp_path / "derived-ten-pages.pdf"
    _derive_ten_page_pdf(derived)
    imported_external_paths = (derived, IMAGE_FIXTURE, MARKER_FIXTURE)
    protected_external_paths = (MIXED_FIXTURE, *imported_external_paths)
    external_bytes = tuple(path.read_bytes() for path in protected_external_paths)
    input_bytes = sum(path.stat().st_size for path in imported_external_paths)

    profile_root = tmp_path / "profile"
    profile_root.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile_root,
        quota=_workspace_quota(CONSTRAINED_RESOURCE_POLICY_V1),
        id_factory=lambda: CASE_ID,
    )
    WindowsCaseLifecycleStore(manager).create_case().close()
    sources = WindowsSourceIngestStore(
        manager,
        WindowsSourcePrechecker(_import_limits(CONSTRAINED_RESOURCE_POLICY_V1)),
        maximum_page_count=CONSTRAINED_RESOURCE_POLICY_V1.maximum_page_count,
    )
    ingest = SourceIngestService(sources, _WorkloadInspector(_sha256(derived)))
    imported_pdf = ingest.import_source(CASE_ID, derived, "workload-pdf.pdf")
    imported_image = ingest.import_source(CASE_ID, IMAGE_FIXTURE, "workload-image.png")
    imported_marker = ingest.import_source(CASE_ID, MARKER_FIXTURE, "workload-marker.pdf")
    assert (
        sum(map(len, (imported_pdf.page_ids, imported_image.page_ids, imported_marker.page_ids)))
        == 12
    )
    case_paths = tuple(
        manager.layout(CASE_ID).originals / f"{source_id}.bin"
        for source_id in (
            imported_pdf.source_id,
            imported_image.source_id,
            imported_marker.source_id,
        )
    )
    case_bytes = tuple(path.read_bytes() for path in case_paths)
    manifests = WindowsManifestRepository(manager)
    baseline = manifests.load(CASE_ID).snapshot
    confirmation = SignedSourceConfirmationService(manifests)
    review_sources = (
        SignedSourceModificationSource(
            imported_pdf.source_id,
            SignedSourceSignatureState.NONE_DETECTED,
        ),
        SignedSourceModificationSource(
            imported_image.source_id,
            SignedSourceSignatureState.NOT_APPLICABLE,
        ),
        SignedSourceModificationSource(
            imported_marker.source_id,
            SignedSourceSignatureState.MARKERS_DETECTED,
        ),
    )
    review = confirmation.review(CASE_ID, review_sources)
    assert review.status is SignedSourceReviewStatus.CONFIRMATION_REQUIRED
    assert review.pending_source_ids == (imported_marker.source_id,)
    confirmed = confirmation.decide(
        review,
        SignedSourceConfirmationChoice.CREATE_UNSIGNED_DERIVATIVE,
    )
    assert confirmed.status is SignedSourceConfirmationOutcomeStatus.CONFIRMED
    after_confirmation = manifests.load(CASE_ID).snapshot
    assert after_confirmation.revision == baseline.revision + 1
    assert _manifest_core(after_confirmation.document) == _manifest_core(baseline.document)
    confirmations = cast(list[object], after_confirmation.document["confirmations"])
    assert len(confirmations) == 1
    preparing_duration_ms = max(
        1,
        (time.perf_counter_ns() - workflow_started_ns) // 1_000_000,
    )

    application_resources = create_desktop_application_resource_context(
        CONSTRAINED_RESOURCE_POLICY_V1
    )
    normal_limits = build_windows_searchable_pdf_export_limits(CONSTRAINED_RESOURCE_POLICY_V1)
    resource_snapshots: list[WorkerResourceSnapshot] = []
    temp_snapshots: list[WorkerPrivateTempSnapshot] = []

    success_output = tmp_path / "confirmed-success.pdf"
    success_composition = _composition(
        worker_test_resources,
        application_resources,
        qpdf,
        tesseract,
        tessdata,
        normal_limits,
    )
    success_started_ns = time.perf_counter_ns()
    try:
        success = build_windows_searchable_pdf_export_service(
            manager,
            sources,
            success_composition,
            resources=cast(Any, application_resources),
        ).export_pdf(
            CASE_ID,
            success_output,
            deadline=lambda: False,
            cancellation=lambda: False,
        )
        resource, private = _capture_snapshots(success_composition)
        resource_snapshots.append(resource)
        temp_snapshots.append(private)
    finally:
        success_composition.close()
    confirmed_success_duration_ms = max(
        1,
        (time.perf_counter_ns() - success_started_ns) // 1_000_000,
    )
    assert success.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert success.report.plan.page_count == 12
    assert success.report.base_report.unsigned_derivative is True
    assert success.destination_reopened is True
    after_success = manifests.load(CASE_ID).snapshot
    success_output.unlink()

    cancellation_requested = False
    cancellation_trigger = 0
    cancellation_progress: list[PdfExportProgress] = []

    def observe_progress(value: PdfExportProgress) -> None:
        nonlocal cancellation_requested, cancellation_trigger
        cancellation_progress.append(value)
        if (
            not cancellation_requested
            and value.phase is PdfExportProgressPhase.ASSEMBLING_AND_VERIFYING
            and value.completed_pages == 4
        ):
            cancellation_trigger = value.completed_pages
            cancellation_requested = True

    cancel_output = tmp_path / "cancelled.pdf"
    cancel_composition = _composition(
        worker_test_resources,
        application_resources,
        qpdf,
        tesseract,
        tessdata,
        normal_limits,
    )
    cancellation_started_ns = time.perf_counter_ns()
    try:
        with pytest.raises(PdfExportError) as cancelled:
            build_windows_searchable_pdf_export_service(
                manager,
                sources,
                cancel_composition,
                resources=cast(Any, application_resources),
            ).export_pdf(
                CASE_ID,
                cancel_output,
                deadline=lambda: False,
                cancellation=lambda: cancellation_requested,
                progress=observe_progress,
            )
        resource, private = _capture_snapshots(cancel_composition)
        resource_snapshots.append(resource)
        temp_snapshots.append(private)
        cancellation_acknowledgement_ms = cancel_composition.last_cancellation_acknowledgement_ms()
        worker_tree_terminated_after_cancel = cancel_composition.worker_tree_termination_confirmed()
    finally:
        cancel_composition.close()
    cancellation_duration_ms = max(
        1,
        (time.perf_counter_ns() - cancellation_started_ns) // 1_000_000,
    )
    assert cancelled.value.code is PdfExportErrorCode.CANCELLED
    assert cancellation_trigger == 4
    assert any(
        item.phase is PdfExportProgressPhase.ASSEMBLING_AND_VERIFYING and item.completed_pages == 4
        for item in cancellation_progress
    )
    assert type(cancellation_acknowledgement_ms) is int
    assert 0 <= cancellation_acknowledgement_ms <= 2_000
    assert worker_tree_terminated_after_cancel is True
    assert not cancel_output.exists()
    after_cancel = manifests.load(CASE_ID).snapshot
    assert after_cancel.document == after_success.document
    retries = WindowsPdfExportRetryRepository(manager)
    cancel_cleanup_complete = manager.measure_usage(CASE_ID).temp_bytes == 0
    assert cancel_cleanup_complete and _retry_inventory_empty(retries)

    one_byte_limits = replace(normal_limits, output_bytes=1)
    limit_output = tmp_path / "limited.pdf"
    limit_composition = _composition(
        worker_test_resources,
        application_resources,
        qpdf,
        tesseract,
        tessdata,
        one_byte_limits,
    )
    limit_started_ns = time.perf_counter_ns()
    try:
        with pytest.raises(PdfExportError) as limited:
            build_windows_searchable_pdf_export_service(
                manager,
                sources,
                limit_composition,
                resources=cast(Any, application_resources),
            ).export_pdf(
                CASE_ID,
                limit_output,
                deadline=lambda: False,
                cancellation=lambda: False,
            )
        resource, private = _capture_snapshots(limit_composition)
        resource_snapshots.append(resource)
        temp_snapshots.append(private)
        correlated = limit_composition.last_operation_result()
        worker_tree_terminated_after_limit = limit_composition.worker_tree_termination_confirmed()
    finally:
        limit_composition.close()
    limit_failure_duration_ms = max(
        1,
        (time.perf_counter_ns() - limit_started_ns) // 1_000_000,
    )
    assert limited.value.code is PdfExportErrorCode.LIMIT_EXCEEDED
    assert limited.value.context == "searchable.worker_resource_limit"
    assert correlated is not None
    assert correlated.operation_id.version == 4
    assert correlated.outcome is OperationOutcome.FAILED
    assert correlated.error_code is ErrorCode.WORKER_OPERATION_FAILED
    assert correlated.error_category is ErrorCategory.RESOURCE_LIMIT
    assert worker_tree_terminated_after_limit is True
    assert not limit_output.exists()
    after_limit = manifests.load(CASE_ID).snapshot
    assert after_limit.document == after_cancel.document
    limit_cleanup_complete = manager.measure_usage(CASE_ID).temp_bytes == 0
    assert limit_cleanup_complete and _retry_inventory_empty(retries)

    retry_output = tmp_path / "fresh-retry.pdf"
    retry_composition = _composition(
        worker_test_resources,
        application_resources,
        qpdf,
        tesseract,
        tessdata,
        normal_limits,
    )
    retry_started_ns = time.perf_counter_ns()
    try:
        retry = build_windows_searchable_pdf_export_service(
            manager,
            sources,
            retry_composition,
            resources=cast(Any, application_resources),
        ).export_pdf(
            CASE_ID,
            retry_output,
            deadline=lambda: False,
            cancellation=lambda: False,
        )
        resource, private = _capture_snapshots(retry_composition)
        resource_snapshots.append(resource)
        temp_snapshots.append(private)
    finally:
        retry_composition.close()
    retry_duration_ms = max(
        1,
        (time.perf_counter_ns() - retry_started_ns) // 1_000_000,
    )
    assert retry.status is PdfExportOutcomeStatus.COMMITTED_RECORDED
    assert retry.report.plan.page_count == 12
    assert retry.report.base_report.unsigned_derivative is True
    assert retry.destination_reopened is True
    assert retry.report.base_report.output_sha256 == _sha256(retry_output)
    with pikepdf.Pdf.open(retry_output, attempt_recovery=False, password="") as document:
        assert len(document.pages) == 12
        assert not document.is_encrypted
    final_manifest = manifests.load(CASE_ID).snapshot
    assert final_manifest.revision == baseline.revision + 3
    assert _manifest_core(final_manifest.document) == _manifest_core(baseline.document)
    assert final_manifest.document["confirmations"] == after_confirmation.document["confirmations"]
    final_history = cast(list[object], final_manifest.document["export_history"])
    assert len(final_history) == 2
    assert len(WindowsPdfExportHistoryRepository(manager).load(CASE_ID).document.records) == 2
    originals_unchanged = (
        tuple(path.read_bytes() for path in protected_external_paths) == external_bytes
        and tuple(path.read_bytes() for path in case_paths) == case_bytes
    )
    assert originals_unchanged
    assert manager.measure_usage(CASE_ID).temp_bytes == 0
    assert _retry_inventory_empty(retries)

    peak_process = max(item.peak_process_memory_used_bytes for item in resource_snapshots)
    peak_job = max(item.peak_job_memory_used_bytes for item in resource_snapshots)
    peak_private_temp = max(item.sampled_peak_bytes for item in temp_snapshots)
    private_samples = sum(item.sample_count for item in temp_snapshots)
    assert peak_process >= 1 and peak_job >= peak_process
    assert peak_private_temp >= 1 and private_samples >= len(temp_snapshots)
    assert all(item.measurement_complete for item in temp_snapshots)
    peak_heavy_concurrency = (
        application_resources.heavy_operations.snapshot().maximum_observed_concurrency
    )
    assert peak_heavy_concurrency == 1
    measured_total_duration_ms = max(
        1,
        (time.perf_counter_ns() - workflow_started_ns) // 1_000_000,
    )
    summed_durations_ms = (
        preparing_duration_ms
        + confirmed_success_duration_ms
        + cancellation_duration_ms
        + limit_failure_duration_ms
        + retry_duration_ms
    )
    total_duration_ms = max(measured_total_duration_ms, summed_durations_ms)
    report = Pws0208ResourceEvidence(
        revision=_revision(),
        input_bytes=input_bytes,
        output_bytes=retry_output.stat().st_size,
        output_sha256=retry.report.base_report.output_sha256,
        preparing_duration_ms=preparing_duration_ms,
        confirmed_success_duration_ms=confirmed_success_duration_ms,
        cancellation_duration_ms=cancellation_duration_ms,
        limit_failure_duration_ms=limit_failure_duration_ms,
        retry_duration_ms=retry_duration_ms,
        total_duration_ms=total_duration_ms,
        peak_complete_process_tree_rss_bytes=1,
        complete_process_tree_rss_sample_count=1,
        peak_worker_process_commit_bytes=peak_process,
        peak_worker_job_commit_bytes=peak_job,
        sampled_worker_private_temp_peak_bytes=peak_private_temp,
        worker_private_temp_sample_count=private_samples,
        worker_private_temp_measurement_complete=True,
        peak_active_full_resolution_page_buffers=cast(
            int,
            probe["peak_active_full_resolution_page_buffers"],
        ),
        peak_active_page_results=cast(int, probe["peak_active_page_results"]),
        peak_heavy_operation_concurrency=peak_heavy_concurrency,
        cancellation_acknowledgement_ms=cancellation_acknowledgement_ms,
        limit_failure_code=limited.value.code.value,
        limit_failure_operation_correlated=True,
        worker_tree_terminated_after_cancel=worker_tree_terminated_after_cancel,
        worker_tree_terminated_after_limit=worker_tree_terminated_after_limit,
        cancel_cleanup_complete=cancel_cleanup_complete,
        limit_cleanup_complete=limit_cleanup_complete,
        retry_succeeded=True,
        originals_unchanged=originals_unchanged,
        manifest_expected_mutations_only=True,
        manifest_unchanged_after_cancel_and_limit=True,
        destination_absent_after_cancel=not cancel_output.exists(),
        destination_absent_after_limit=not limit_output.exists(),
        overall_pass=True,
    )
    _write_child_artifact(
        artifact_directory / CHILD_DERIVATIVE_FILENAME,
        retry_output.read_bytes(),
    )
    _write_child_artifact(
        artifact_directory / CHILD_REPORT_FILENAME,
        encode_pws0208_resource_evidence(report),
    )

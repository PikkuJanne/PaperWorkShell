"""Live PWS-0307 evidence through the restricted flattened-PDF worker."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from dataclasses import dataclass, replace
from importlib.metadata import PackageNotFoundError, distribution
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pikepdf
import pytest
from packaging.requirements import Requirement

from paperworkshell.__main__ import build_windows_flattened_pdf_export_limits
from paperworkshell.app.source_ingest import CancellationProbe, DeadlineProbe, SourceIngestService
from paperworkshell.domain.import_precheck import ImportLimits
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.pdf_export import build_pdf_export_plan
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.domain.source_ingest import (
    CompletedSourceInspection,
    PreparedSourceSummary,
    SourceEncryptionState,
    SourceSignatureState,
)
from paperworkshell.overlays.exporting import (
    FlattenedPdfExportAsset,
    FlattenedPdfExportCheckCode,
    FlattenedPdfExportError,
    FlattenedPdfExportErrorCode,
    FlattenedPdfExportPage,
    FlattenedPdfExportPlan,
)
from paperworkshell.overlays.models import (
    OverlayAssetKind,
    OverlayAssetMediaType,
    OverlayAssetRecord,
    OverlayRecord,
    SignatureImageOverlayPayload,
)
from paperworkshell.overlays.rendering import OverlayRasterAsset
from paperworkshell.overlays.signature_rasters import SignatureImageOverlayRasterSource
from paperworkshell.pdf.qpdf_checker import DEFAULT_QPDF_EXECUTABLE
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.source_ingest import (
    CommittedPdfSourceBatchReadLease,
    CommittedPdfSourceReadLease,
    WindowsSourceIngestStore,
)
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.worker.client.models import WorkerTimeoutPolicy
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.flattened_pdf_export_broker import (
    BrokeredFlattenedPdfExportCandidate,
)
from paperworkshell.worker.flattened_pdf_export_buffers import (
    WindowsFlattenedPdfRasterBuffer,
    drain_flattened_pdf_raster_cleanup,
)
from paperworkshell.worker.flattened_pdf_export_composition import (
    FlattenedPdfExportWorkerComposition,
    create_flattened_pdf_export_worker_composition,
    create_flattened_pdf_export_worker_composition_config,
)
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.protocol.models import ErrorCategory, OperationOutcome, ResourceLimits
from tests.worker_resources import WorkerTestResources

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0307 targets the supported Windows AppContainer boundary",
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_FIXTURE = _PROJECT_ROOT / "tests/fixtures/synthetic/v1/pdf/born-digital.pdf"
_CASE_ID = UUID("30700000-0000-4000-8000-000000000301")
_ASSET_ID = UUID("30700000-0000-4000-8000-000000000302")
_OVERLAY_ID = UUID("30700000-0000-4000-8000-000000000303")
_PRIVATE_LOG_CANARY = "PWS-0307-PRIVATE-DISPLAY-CANARY.pdf"
_PYTHON_DISTRIBUTIONS = (
    "ocrmypdf",
    "fpdf2",
    "fonttools",
    "uharfbuzz",
    "pikepdf",
    "pypdfium2",
    "pillow",
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
class _OnePagePdfInspector:
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
        return CompletedSourceInspection(
            pending_id=source.pending_id,
            media_type=source.media_type,
            page_count=1,
            signature_state=SourceSignatureState.NONE_DETECTED,
            encryption_state=SourceEncryptionState.UNENCRYPTED,
        )


@dataclass(slots=True)
class _RevalidationCountingLease:
    delegate: CommittedPdfSourceBatchReadLease
    revalidation_calls: int = 0

    @property
    def case_id(self) -> UUID:
        return self.delegate.case_id

    @property
    def manifest_revision(self) -> int:
        return self.delegate.manifest_revision

    @property
    def manifest_sha256(self) -> str:
        return self.delegate.manifest_sha256

    @property
    def manifest_cas_token(self) -> str:
        return self.delegate.manifest_cas_token

    @property
    def sources(self) -> tuple[CommittedPdfSourceReadLease, ...]:
        return self.delegate.sources

    def revalidate(
        self,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> None:
        self.revalidation_calls += 1
        self.delegate.revalidate(deadline=deadline, cancellation=cancellation)


def _stage_python_closure(resources: WorkerTestResources) -> None:
    """Stage the exact locked PDF/overlay closure and its distribution metadata."""

    destination_root = resources.runtime_root / "Lib" / "site-packages"
    destination_root.mkdir(exist_ok=True)
    pending = list(_PYTHON_DISTRIBUTIONS)
    completed: set[str] = set()
    while pending:
        selected = pending.pop()
        normalized = selected.casefold().replace("_", "-")
        if normalized in completed:
            continue
        try:
            package = distribution(selected)
        except PackageNotFoundError:
            pytest.fail("the locked flattened-PDF test dependency closure is unavailable")
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


def _qpdf_executable() -> Path:
    configured = os.environ.get("PWS_QPDF_EXECUTABLE")
    selected = Path(configured) if configured else DEFAULT_QPDF_EXECUTABLE
    assert selected.is_absolute() and selected.is_file(), "required qpdf 12.3.2 is unavailable"
    return selected


def _limits() -> ResourceLimits:
    """Tighten every mutable dimension of the central constrained profile."""

    return replace(
        build_windows_flattened_pdf_export_limits(CONSTRAINED_RESOURCE_POLICY_V1),
        wall_time_ms=30_000,
        cpu_time_ms=25_000,
        memory_bytes=128 * 1024 * 1024,
        temporary_storage_bytes=32 * 1024 * 1024,
        output_bytes=4 * 1024 * 1024,
        page_count=4,
        pixel_count=1_048_576,
    )


def _composition(
    resources: WorkerTestResources,
    qpdf_executable: Path,
    limits: ResourceLimits,
) -> FlattenedPdfExportWorkerComposition:
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    config = create_flattened_pdf_export_worker_composition_config(
        python_executable=resources.python_executable,
        bootstrap_script=resources.source_root / "paperworkshell/worker/host_bootstrap.py",
        runtime_roots=(resources.runtime_root,),
        code_roots=(resources.source_root,),
        working_directory=resources.source_root,
        qpdf_executable=qpdf_executable,
        resource_policy=WorkerResourcePolicy(
            process_memory_bytes=policy.worker_process_memory_bytes,
            job_memory_bytes=policy.worker_job_memory_bytes,
            cpu_time_ms=policy.worker_cpu_time_ms,
            maximum_processes=2,
        ),
        timeout_policy=WorkerTimeoutPolicy(
            handshake_seconds=20.0,
            cancellation_grace_seconds=policy.cancellation_acknowledgement_ms / 1_000,
            shutdown_grace_seconds=10.0,
        ),
        maximum_heavy_operation_concurrency=1,
        limits=limits,
        resource_sampling_interval_ms=10,
        pump_interval_seconds=0.010,
    )
    return create_flattened_pdf_export_worker_composition(
        config,
        heavy_operations=HeavyOperationCoordinator(1),
    )


def _workspace(
    tmp_path: Path,
) -> tuple[
    CaseWorkspaceManager,
    WindowsSourceIngestStore,
    FlattenedPdfExportPlan,
    dict[UUID, SignatureImageOverlayRasterSource],
    Path,
    bytes,
]:
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=WorkspaceQuota(
            maximum_case_bytes=32 * 1024 * 1024,
            maximum_cache_bytes=4 * 1024 * 1024,
            maximum_temp_bytes=16 * 1024 * 1024,
            maximum_entries=512,
            maximum_depth=16,
        ),
        id_factory=lambda: _CASE_ID,
    )
    case = WindowsCaseLifecycleStore(manager).create_case()
    case.close()
    sources = WindowsSourceIngestStore(
        manager,
        WindowsSourcePrechecker(
            ImportLimits(
                maximum_source_bytes=4 * 1024 * 1024,
                maximum_pdf_pages=4,
                maximum_image_width=4_096,
                maximum_image_height=4_096,
                maximum_image_pixels=4_096 * 4_096,
                maximum_decompressed_bytes=32 * 1024 * 1024,
                decompression_warning_ratio=100,
                maximum_decompression_ratio=2_000,
                maximum_header_bytes=4_096,
                maximum_structure_records=64,
            )
        ),
        maximum_page_count=4,
        id_factory=_SequentialUuidFactory(0x30710000),
        temp_id_factory=_SequentialUuidFactory(0x30720000),
    )
    imported = SourceIngestService(sources, _OnePagePdfInspector()).import_source(
        _CASE_ID,
        _SOURCE_FIXTURE,
        _PRIVATE_LOG_CANARY,
    )
    snapshot = WindowsManifestRepository(manager).load(_CASE_ID).snapshot
    base_plan = build_pdf_export_plan(snapshot)
    assert len(base_plan.pages) == 1
    base_page = base_plan.pages[0]
    pixels = bytes(
        (
            255,
            32,
            32,
            255,
            32,
            64,
            255,
            192,
            32,
            192,
            64,
            160,
            240,
            240,
            32,
            255,
        )
    )
    raster = OverlayRasterAsset(
        asset_id=_ASSET_ID,
        width_pixels=2,
        height_pixels=2,
        row_stride_bytes=8,
        byte_count=len(pixels),
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
    )
    encoded = OverlayAssetRecord(
        asset_id=_ASSET_ID,
        kind=OverlayAssetKind.SIGNATURE_IMAGE,
        storage_name=f"{_ASSET_ID}.bin",
        media_type=OverlayAssetMediaType.PNG,
        byte_size=67,
        sha256=hashlib.sha256(b"synthetic encoded signature asset").hexdigest(),
    )
    overlay = OverlayRecord(
        overlay_id=_OVERLAY_ID,
        page_id=base_page.page_id,
        box=NormalizedPageBox(200_000, 250_000, 400_000, 200_000),
        z_index=0,
        payload=SignatureImageOverlayPayload(_ASSET_ID),
    )
    plan = FlattenedPdfExportPlan(
        base_plan=base_plan,
        pages=(
            FlattenedPdfExportPage(
                base_page=base_page,
                active_crop=NormalizedPageBox.full_page(),
                overlays=(overlay,),
            ),
        ),
        assets=(FlattenedPdfExportAsset(encoded, raster),),
    )
    committed_path = manager.layout(_CASE_ID).originals / f"{imported.source_id}.bin"
    committed_bytes = committed_path.read_bytes()
    assert hashlib.sha256(committed_bytes).hexdigest() == imported.sha256
    return (
        manager,
        sources,
        plan,
        {_ASSET_ID: SignatureImageOverlayRasterSource(raster, pixels)},
        committed_path,
        committed_bytes,
    )


def _track_raster_buffers(
    composition: FlattenedPdfExportWorkerComposition,
) -> list[WindowsFlattenedPdfRasterBuffer]:
    created: list[WindowsFlattenedPdfRasterBuffer] = []

    def factory(payload: bytes) -> WindowsFlattenedPdfRasterBuffer:
        buffer = WindowsFlattenedPdfRasterBuffer.create(payload)
        created.append(buffer)
        return buffer

    composition.exporter._raster_buffer_factory = factory
    return created


def _assert_raster_buffers_closed(buffers: list[WindowsFlattenedPdfRasterBuffer]) -> None:
    assert len(buffers) == 1
    with pytest.raises(FlattenedPdfExportError) as raised:
        _ = buffers[0].read_handle_value
    assert raised.value.code is FlattenedPdfExportErrorCode.INTERNAL_ERROR
    assert raised.value.context == "flattened.raster_handle"
    drain_flattened_pdf_raster_cleanup()


def _read_candidate(candidate: BrokeredFlattenedPdfExportCandidate) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = candidate.read(64 * 1024)
        assert type(chunk) is bytes
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def test_live_constrained_flattened_export_authenticates_raster_and_revalidates_source(
    tmp_path: Path,
    worker_test_resources: WorkerTestResources,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    _stage_python_closure(worker_test_resources)
    fixture_before = _SOURCE_FIXTURE.read_bytes()
    manager, sources, plan, rasters, committed_path, committed_before = _workspace(tmp_path)
    composition = _composition(worker_test_resources, _qpdf_executable(), _limits())
    raster_buffers = _track_raster_buffers(composition)
    counting: _RevalidationCountingLease | None = None
    output = b""

    try:
        batch = sources.borrow_committed_pdf_sources(
            plan.base_plan,
            deadline=lambda: False,
            cancellation=lambda: False,
        )
        with batch as retained:
            counting = _RevalidationCountingLease(retained)
            candidate_context = composition.exporter.export(
                plan,
                counting,
                rasters,
                deadline=lambda: False,
                cancellation=lambda: False,
            )
            with candidate_context as candidate:
                report = candidate.report
                output = _read_candidate(candidate)

        result = composition.last_operation_result()
        private_temp = composition.private_temp_snapshot()
        assert composition.worker_tree_termination_confirmed()
    finally:
        composition.close()

    assert counting is not None and counting.revalidation_calls == 1
    assert report.plan is plan
    assert report.base_report.page_count == 1
    assert report.plan.visible_overlay_count == 1
    assert report.plan.overlay_page_count == 1
    assert report.plan.placed_signature_image_count == 1
    assert hashlib.sha256(output).hexdigest() == report.base_report.output_sha256
    assert len(output) == report.base_report.output_byte_size
    assert tuple(check.code for check in report.checks) == (
        FlattenedPdfExportCheckCode.VISIBLE_OVERLAYS_FLATTENED,
        FlattenedPdfExportCheckCode.OVERLAY_PRESENCE_MATCHED,
        FlattenedPdfExportCheckCode.TEXT_FONT_POLICY_MATCHED,
        FlattenedPdfExportCheckCode.SIGNATURE_IMAGE_TERMINOLOGY_PRESERVED,
    )
    assert result is not None and result.outcome is OperationOutcome.SUCCEEDED
    assert result.outputs[0].expected_sha256 == report.base_report.output_sha256
    assert private_temp is not None and private_temp.measurement_complete
    assert private_temp.sampled_peak_bytes <= _limits().temporary_storage_bytes
    _assert_raster_buffers_closed(raster_buffers)
    assert manager.measure_usage(_CASE_ID).temp_bytes == 0
    assert _SOURCE_FIXTURE.read_bytes() == fixture_before
    assert committed_path.read_bytes() == committed_before
    assert (
        hashlib.sha256(committed_path.read_bytes()).hexdigest() == plan.base_plan.sources[0].sha256
    )
    assert _PRIVATE_LOG_CANARY not in caplog.text
    with pikepdf.Pdf.open(BytesIO(output), attempt_recovery=False, password="") as document:
        assert len(document.pages) == 1
        xobjects = document.pages[0].obj.Resources.XObject
        names = [name for name in xobjects.keys() if str(name).startswith("/PWSOverlay")]
        assert len(names) == 1
        form = xobjects[names[0]]
        assert isinstance(form, pikepdf.Stream)
        assert len(form.Resources.XObject) == 1


def test_live_output_bound_failure_is_correlated_terminated_clean_and_private(
    tmp_path: Path,
    worker_test_resources: WorkerTestResources,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    _stage_python_closure(worker_test_resources)
    fixture_before = _SOURCE_FIXTURE.read_bytes()
    manager, sources, plan, rasters, committed_path, committed_before = _workspace(tmp_path)
    composition = _composition(
        worker_test_resources,
        _qpdf_executable(),
        replace(_limits(), output_bytes=1),
    )
    raster_buffers = _track_raster_buffers(composition)

    try:
        batch = sources.borrow_committed_pdf_sources(
            plan.base_plan,
            deadline=lambda: False,
            cancellation=lambda: False,
        )
        with batch as retained:
            with pytest.raises(FlattenedPdfExportError) as raised:
                composition.exporter.export(
                    plan,
                    retained,
                    rasters,
                    deadline=lambda: False,
                    cancellation=lambda: False,
                )
        result = composition.last_operation_result()
        private_temp = composition.private_temp_snapshot()
        assert composition.worker_tree_termination_confirmed()
    finally:
        composition.close()

    assert raised.value.code is FlattenedPdfExportErrorCode.LIMIT_EXCEEDED
    assert _PRIVATE_LOG_CANARY not in str(raised.value)
    assert _PRIVATE_LOG_CANARY not in repr(raised.value)
    assert result is not None
    assert result.outcome is OperationOutcome.FAILED
    assert result.error_category is ErrorCategory.RESOURCE_LIMIT
    assert private_temp is not None and private_temp.measurement_complete
    assert private_temp.sampled_peak_bytes <= _limits().temporary_storage_bytes
    _assert_raster_buffers_closed(raster_buffers)
    assert manager.measure_usage(_CASE_ID).temp_bytes == 0
    assert _SOURCE_FIXTURE.read_bytes() == fixture_before
    assert committed_path.read_bytes() == committed_before
    assert _PRIVATE_LOG_CANARY not in caplog.text

"""Live AppContainer evidence for the bounded PDF thumbnail pipeline."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pikepdf
import pypdfium2  # type: ignore[import-untyped]
import pypdfium2_raw  # type: ignore[import-untyped]
import pytest
from PIL import Image

from paperworkshell.app.pdf_thumbnails import PdfThumbnailService
from paperworkshell.app.source_ingest import SourceIngestService
from paperworkshell.domain.import_precheck import ImportLimits
from paperworkshell.domain.pdf_rendering import PdfRenderRotation
from paperworkshell.domain.pdf_thumbnails import (
    PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES,
    PdfThumbnailCacheStatus,
    PdfThumbnailError,
    PdfThumbnailErrorCode,
    PdfThumbnailProgress,
    PdfThumbnailProgressPhase,
    PdfThumbnailRequest,
)
from paperworkshell.domain.source_ingest import (
    CompletedSourceInspection,
    PreparedSourceSummary,
    SourceEncryptionState,
    SourceSignatureState,
)
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.source_ingest import WindowsSourceIngestStore
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.thumbnail_cache import WindowsThumbnailCacheStore
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.worker.client._windows import WindowsLaunchConfig, WindowsProcessLauncher
from paperworkshell.worker.client.client import WorkerClient
from paperworkshell.worker.client.models import WorkerLifecyclePhase, WorkerTimeoutPolicy
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.pdf_thumbnail_broker import (
    BrokeredPdfThumbnailGenerator,
    PdfThumbnailBrokerPolicy,
)
from paperworkshell.worker.protocol.models import (
    PDF_THUMBNAIL_CAPABILITY,
    ResourceLimits,
)
from tests.worker_resources import WorkerTestResources

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0108 uses the supported Windows AppContainer worker boundary",
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_ROOT = _PROJECT_ROOT / "tests" / "fixtures" / "synthetic" / "v1"
_GOLDEN_ROOT = _PROJECT_ROOT / "tests" / "golden" / "renderer-v1"
_CASE_ID = UUID("10800000-0000-4000-8000-000000000108")
_NOW = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
_PRIVATE_DISPLAY_NAME = "PRIVATE-APPLICANT-NAME-CANARY.pdf"
_PACKAGE_DIRECTORIES = (
    "pikepdf",
    "pikepdf.libs",
    "lxml",
    "PIL",
    "packaging",
    "pypdfium2",
    "pypdfium2_cfg",
    "pypdfium2_raw",
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
class _PdfInspector:
    page_count: int

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
            page_count=self.page_count,
            signature_state=SourceSignatureState.NONE_DETECTED,
            encryption_state=SourceEncryptionState.UNENCRYPTED,
        )


@dataclass(frozen=True, slots=True)
class _LiveSystem:
    manager: CaseWorkspaceManager
    sources: WindowsSourceIngestStore
    cache: WindowsThumbnailCacheStore
    worker: WorkerClient
    service: PdfThumbnailService


def _stage_pdf_engines(resources: WorkerTestResources) -> None:
    """Stage the exact pinned parser and renderer packages in the disposable runtime."""

    roots = {
        Path(pikepdf.__file__).resolve().parent.parent,
        Path(pypdfium2.__file__).resolve().parent.parent,
        Path(pypdfium2_raw.__file__).resolve().parent.parent,
    }
    assert len(roots) == 1
    source_root = roots.pop()
    destination_root = resources.runtime_root / "Lib" / "site-packages"
    destination_root.mkdir(exist_ok=True)
    for name in _PACKAGE_DIRECTORIES:
        source = source_root / name
        destination = destination_root / name
        assert source.is_dir()
        if destination.is_dir():
            continue
        assert not destination.exists()
        shutil.copytree(
            source,
            destination,
            copy_function=shutil.copyfile,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )


def _launch_config(resources: WorkerTestResources) -> WindowsLaunchConfig:
    return WindowsLaunchConfig(
        python_executable=resources.python_executable,
        bootstrap_script=resources.source_root / "paperworkshell" / "worker" / "host_bootstrap.py",
        runtime_roots=(resources.runtime_root,),
        code_roots=(resources.source_root,),
        working_directory=resources.source_root,
    )


def _workspace_quota() -> WorkspaceQuota:
    return WorkspaceQuota(
        maximum_case_bytes=48_000_000,
        maximum_cache_bytes=16_000_000,
        maximum_temp_bytes=16_000_000,
        maximum_entries=1_024,
        maximum_depth=16,
    )


def _import_limits() -> ImportLimits:
    return ImportLimits(
        maximum_source_bytes=3_000_000,
        maximum_pdf_pages=100,
        maximum_image_width=10_000,
        maximum_image_height=10_000,
        maximum_image_pixels=50_000_000,
        maximum_decompressed_bytes=200_000_000,
        decompression_warning_ratio=100,
        maximum_decompression_ratio=2_000,
        maximum_header_bytes=4_096,
        maximum_structure_records=64,
    )


def _broker_policy(*, maximum_pixels: int = 1_048_576) -> PdfThumbnailBrokerPolicy:
    maximum_raster_bytes = maximum_pixels * 4
    return PdfThumbnailBrokerPolicy(
        ResourceLimits(
            wall_time_ms=20_000,
            cpu_time_ms=15_000,
            memory_bytes=16 * 1024 * 1024,
            temporary_storage_bytes=16 * 1024 * 1024,
            output_bytes=PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES + maximum_raster_bytes,
            page_count=100,
            pixel_count=maximum_pixels,
        ),
        pump_interval_seconds=0.01,
    )


def _worker(resources: WorkerTestResources) -> WorkerClient:
    return WorkerClient(
        WindowsProcessLauncher(_launch_config(resources)),
        WorkerResourcePolicy(
            process_memory_bytes=512 * 1024 * 1024,
            job_memory_bytes=768 * 1024 * 1024,
            cpu_time_ms=30_000,
            maximum_processes=1,
        ),
        WorkerTimeoutPolicy(
            handshake_seconds=20.0,
            cancellation_grace_seconds=5.0,
            shutdown_grace_seconds=10.0,
        ),
        required_capabilities=(PDF_THUMBNAIL_CAPABILITY,),
    )


def _live_system(
    tmp_path: Path,
    resources: WorkerTestResources,
    *,
    page_count: int,
    maximum_pixels: int = 1_048_576,
) -> _LiveSystem:
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=_workspace_quota(),
        id_factory=lambda: _CASE_ID,
    )
    case = WindowsCaseLifecycleStore(manager, clock=lambda: _NOW).create_case()
    case.close()
    sources = WindowsSourceIngestStore(
        manager,
        WindowsSourcePrechecker(_import_limits()),
        maximum_page_count=100,
        clock=lambda: _NOW,
        id_factory=_SequentialUuidFactory(0x20800000),
        temp_id_factory=_SequentialUuidFactory(0x30800000),
    )
    cache = WindowsThumbnailCacheStore(
        manager,
        id_factory=_SequentialUuidFactory(0x40800000),
    )
    worker = _worker(resources)
    generator = BrokeredPdfThumbnailGenerator(
        worker,
        sources,
        cache,
        _broker_policy(maximum_pixels=maximum_pixels),
        id_factory=_SequentialUuidFactory(0x50800000),
    )
    service = PdfThumbnailService(
        generator,
        id_factory=_SequentialUuidFactory(0x60800000),
    )
    assert page_count >= 1
    return _LiveSystem(manager, sources, cache, worker, service)


def _import_pdf(
    system: _LiveSystem,
    fixture_name: str,
    *,
    page_count: int,
) -> tuple[Path, bytes, tuple[UUID, ...]]:
    source = (_FIXTURE_ROOT / "pdf" / fixture_name).resolve()
    before = source.read_bytes()
    result = SourceIngestService(system.sources, _PdfInspector(page_count)).import_source(
        _CASE_ID,
        source,
        _PRIVATE_DISPLAY_NAME,
    )
    assert result.page_ids
    assert source.read_bytes() == before
    return source, before, result.page_ids


def _request(page_id: UUID, width: int, height: int) -> PdfThumbnailRequest:
    return PdfThumbnailRequest(
        case_id=_CASE_ID,
        page_id=page_id,
        maximum_width_pixels=width,
        maximum_height_pixels=height,
    )


def test_live_service_renders_rotated_multipage_pdf_hits_cache_and_rebuilds_corruption(
    tmp_path: Path,
    worker_test_resources: WorkerTestResources,
) -> None:
    _stage_pdf_engines(worker_test_resources)
    system = _live_system(tmp_path, worker_test_resources, page_count=2)
    source, source_bytes, page_ids = _import_pdf(
        system,
        "render-calibration.pdf",
        page_count=2,
    )
    requests = (
        _request(page_ids[0], 40, 60),
        _request(page_ids[1], 72, 96),
    )
    progress: list[PdfThumbnailProgress] = []

    first = system.service.get_thumbnails(requests, progress=progress.append)

    assert [item.cache_status for item in first] == [
        PdfThumbnailCacheStatus.GENERATED,
        PdfThumbnailCacheStatus.GENERATED,
    ]
    assert first[0].snapshot.intrinsic_rotation is PdfRenderRotation.DEGREES_90
    assert first[1].snapshot.intrinsic_rotation is PdfRenderRotation.DEGREES_0
    expected_first = Image.open(_GOLDEN_ROOT / "render-calibration-crop-rotate.png").convert("RGBA")
    expected_second = Image.open(_GOLDEN_ROOT / "render-calibration-media-box.png").convert("RGBA")
    assert (first[0].snapshot.width_pixels, first[0].snapshot.height_pixels) == expected_first.size
    assert (first[1].snapshot.width_pixels, first[1].snapshot.height_pixels) == expected_second.size
    assert first[0].snapshot.pixels == expected_first.tobytes()
    assert first[1].snapshot.pixels == expected_second.tobytes()
    assert [item.phase for item in progress] == [
        PdfThumbnailProgressPhase.LOOKUP,
        PdfThumbnailProgressPhase.RENDERING,
        PdfThumbnailProgressPhase.VALIDATING,
        PdfThumbnailProgressPhase.LOOKUP,
        PdfThumbnailProgressPhase.RENDERING,
        PdfThumbnailProgressPhase.VALIDATING,
        PdfThumbnailProgressPhase.COMPLETE,
    ]
    assert system.worker.query().phase is WorkerLifecyclePhase.STOPPED
    first_generation = system.worker.generation
    assert first_generation == 2

    second = system.service.get_thumbnails(requests)

    assert [item.cache_status for item in second] == [
        PdfThumbnailCacheStatus.HIT,
        PdfThumbnailCacheStatus.HIT,
    ]
    assert system.worker.generation == first_generation
    assert tuple(item.snapshot.pixels for item in second) == tuple(
        item.snapshot.pixels for item in first
    )

    corrupt_path = system.manager.layout(_CASE_ID).thumbnails / first[0].cache_key.internal_name
    corrupt_path.write_bytes(b"corrupt cache canary")
    rebuilt = system.service.get_thumbnails((requests[0],))

    assert rebuilt[0].cache_status is PdfThumbnailCacheStatus.REBUILT
    assert rebuilt[0].snapshot.pixels == expected_first.tobytes()
    assert system.worker.generation == first_generation + 1
    assert system.worker.query().phase is WorkerLifecyclePhase.STOPPED
    assert source.read_bytes() == source_bytes
    cache_names = tuple(path.name for path in system.manager.layout(_CASE_ID).thumbnails.iterdir())
    assert _PRIVATE_DISPLAY_NAME not in "\n".join(cache_names)
    assert all(not name.endswith(".tmp") for name in cache_names)


def test_live_malformed_pdf_fails_safely_and_discards_unpublished_stage(
    tmp_path: Path,
    worker_test_resources: WorkerTestResources,
) -> None:
    _stage_pdf_engines(worker_test_resources)
    system = _live_system(tmp_path, worker_test_resources, page_count=1)
    source, source_bytes, page_ids = _import_pdf(
        system,
        "render-malformed-page-tree.pdf",
        page_count=1,
    )

    with pytest.raises(PdfThumbnailError) as raised:
        system.service.get_thumbnails((_request(page_ids[0], 64, 64),))

    assert raised.value.code is PdfThumbnailErrorCode.WORKER_FAILED
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert _PRIVATE_DISPLAY_NAME not in str(raised.value)
    assert str(source) not in str(raised.value)
    assert source.read_bytes() == source_bytes
    assert system.worker.query().phase is WorkerLifecyclePhase.STOPPED
    assert tuple(system.manager.layout(_CASE_ID).thumbnails.iterdir()) == ()


def test_high_dimension_request_exceeding_pixel_policy_fails_before_worker_launch(
    tmp_path: Path,
    worker_test_resources: WorkerTestResources,
) -> None:
    _stage_pdf_engines(worker_test_resources)
    system = _live_system(
        tmp_path,
        worker_test_resources,
        page_count=1,
        maximum_pixels=1_048_576,
    )
    source, source_bytes, page_ids = _import_pdf(
        system,
        "render-high-dimension.pdf",
        page_count=1,
    )

    with pytest.raises(PdfThumbnailError) as raised:
        system.service.get_thumbnails((_request(page_ids[0], 1_025, 1_025),))

    assert raised.value.code is PdfThumbnailErrorCode.LIMIT_EXCEEDED
    assert system.worker.generation == 0
    assert system.worker.query().phase is WorkerLifecyclePhase.STOPPED
    assert source.read_bytes() == source_bytes
    assert tuple(system.manager.layout(_CASE_ID).thumbnails.iterdir()) == ()


def test_live_large_page_renders_inside_exact_requested_bound(
    tmp_path: Path,
    worker_test_resources: WorkerTestResources,
) -> None:
    _stage_pdf_engines(worker_test_resources)
    system = _live_system(tmp_path, worker_test_resources, page_count=1)
    source, source_bytes, page_ids = _import_pdf(
        system,
        "render-high-dimension.pdf",
        page_count=1,
    )

    result = system.service.get_thumbnails((_request(page_ids[0], 128, 128),))[0]

    assert result.cache_status is PdfThumbnailCacheStatus.GENERATED
    assert (result.snapshot.width_pixels, result.snapshot.height_pixels) == (128, 128)
    assert len(result.snapshot.pixels) == 128 * 128 * 4
    assert result.snapshot.pixels[3::4] == b"\xff" * (128 * 128)
    assert any(
        result.snapshot.pixels[offset : offset + 3] != b"\xff\xff\xff"
        for offset in range(0, len(result.snapshot.pixels), 4)
    )
    assert system.worker.query().phase is WorkerLifecyclePhase.STOPPED
    assert source.read_bytes() == source_bytes
    cache_entries = tuple(system.manager.layout(_CASE_ID).thumbnails.iterdir())
    assert len(cache_entries) == 1
    assert all(not path.name.endswith(".tmp") for path in cache_entries)


def test_live_cancellation_after_stage_creation_leaves_manifest_and_cache_clean(
    tmp_path: Path,
    worker_test_resources: WorkerTestResources,
) -> None:
    _stage_pdf_engines(worker_test_resources)
    system = _live_system(tmp_path, worker_test_resources, page_count=2)
    source, source_bytes, page_ids = _import_pdf(
        system,
        "render-calibration.pdf",
        page_count=2,
    )
    manifest = system.manager.layout(_CASE_ID).manifest
    manifest_before = manifest.read_bytes()
    rendering_seen = False
    progress: list[PdfThumbnailProgress] = []

    def capture(value: PdfThumbnailProgress) -> None:
        nonlocal rendering_seen
        progress.append(value)
        if value.phase is PdfThumbnailProgressPhase.RENDERING:
            rendering_seen = True

    with pytest.raises(PdfThumbnailError) as raised:
        system.service.get_thumbnails(
            (_request(page_ids[0], 1_024, 1_024),),
            cancellation=lambda: rendering_seen,
            progress=capture,
        )

    assert raised.value.code is PdfThumbnailErrorCode.CANCELLED
    assert rendering_seen
    assert PdfThumbnailProgressPhase.COMPLETE not in {item.phase for item in progress}
    assert manifest.read_bytes() == manifest_before
    assert source.read_bytes() == source_bytes
    assert system.worker.generation == 1
    assert system.worker.query().phase is WorkerLifecyclePhase.STOPPED
    assert tuple(system.manager.layout(_CASE_ID).thumbnails.iterdir()) == ()

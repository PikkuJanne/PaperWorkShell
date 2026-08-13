"""Live Windows worker evidence for immutable JPEG/PNG normalization."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from PIL import Image

from paperworkshell.app.source_ingest import SourceIngestService
from paperworkshell.domain.image_pages import (
    IMAGE_PAGE_ARTIFACT_HEADER_BYTES,
    ImagePagePreviewProgressPhase,
    ImagePagePreviewRequest,
    ImageSourceColorModel,
)
from paperworkshell.domain.image_transforms import (
    IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES,
    ImageNormalizedCrop,
    ImagePageTransformRequest,
    ImageTransformIntent,
)
from paperworkshell.domain.import_precheck import ImportLimits
from paperworkshell.domain.source_ingest import SourceIngestError, SourceIngestErrorCode
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.source_ingest import WindowsSourceIngestStore
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.workspace import CaseWorkspaceManager
from paperworkshell.worker.client._windows import WindowsLaunchConfig, WindowsProcessLauncher
from paperworkshell.worker.client.client import WorkerClient
from paperworkshell.worker.client.models import WorkerLifecyclePhase, WorkerTimeoutPolicy
from paperworkshell.worker.client.process import WorkerResourcePolicy
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.image_page_broker import (
    BrokeredImagePageGenerator,
    BrokeredImageSourceInspector,
    ImageNormalizeBrokerPolicy,
    create_image_page_brokers,
)
from paperworkshell.worker.image_transform_broker import (
    BrokeredImagePageTransformer,
    ImageTransformBrokerPolicy,
)
from paperworkshell.worker.protocol.models import (
    IMAGE_NORMALIZE_CAPABILITY,
    IMAGE_TRANSFORM_CAPABILITY,
    ResourceLimits,
)
from tests.worker_resources import WorkerTestResources
from tools.image_transform_goldens import (
    GOLDEN_DIRECTORY as IMAGE_TRANSFORM_GOLDEN_DIRECTORY,
)
from tools.image_transform_goldens import (
    PROJECT_ROOT,
    SYNTHETIC_MARKER,
    canonical_rgba_png,
    decode_canonical_rgba_png,
    source_image,
)

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0201 uses the supported Windows AppContainer worker boundary",
)

_CASE_ID = UUID("20900000-0000-4000-8000-000000000201")
_NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
_PRIVATE_DISPLAY_NAME = "PRIVATE-IMAGE-SOURCE-CANARY.png"


class _SequentialUuidFactory:
    def __init__(self, prefix: int) -> None:
        self._prefix = prefix
        self._next = 1

    def __call__(self) -> UUID:
        value = UUID(f"{self._prefix:08x}-0000-4000-8000-{self._next:012x}")
        self._next += 1
        return value


def _import_limits() -> ImportLimits:
    return ImportLimits(
        maximum_source_bytes=2_000_000,
        maximum_pdf_pages=10,
        maximum_image_width=10_000,
        maximum_image_height=10_000,
        maximum_image_pixels=10_000_000,
        maximum_decompressed_bytes=64_000_000,
        decompression_warning_ratio=100,
        maximum_decompression_ratio=2_000,
        maximum_header_bytes=4_096,
        maximum_structure_records=64,
    )


def _broker_policy() -> ImageNormalizeBrokerPolicy:
    maximum_pixels = 1_048_576
    return ImageNormalizeBrokerPolicy(
        ResourceLimits(
            wall_time_ms=20_000,
            cpu_time_ms=15_000,
            memory_bytes=64 * 1024 * 1024,
            temporary_storage_bytes=maximum_pixels * 4,
            output_bytes=IMAGE_PAGE_ARTIFACT_HEADER_BYTES + maximum_pixels * 4,
            page_count=1,
            pixel_count=10_000_000,
        ),
        pump_interval_seconds=0.01,
    )


def _transform_policy() -> ImageTransformBrokerPolicy:
    maximum_pixels = 1_048_576
    return ImageTransformBrokerPolicy(
        ResourceLimits(
            wall_time_ms=20_000,
            cpu_time_ms=15_000,
            memory_bytes=64 * 1024 * 1024,
            temporary_storage_bytes=maximum_pixels * 8,
            output_bytes=IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES + maximum_pixels * 4,
            page_count=1,
            pixel_count=maximum_pixels,
        ),
        pump_interval_seconds=0.01,
    )


def _live_system(
    tmp_path: Path,
    resources: WorkerTestResources,
) -> tuple[
    CaseWorkspaceManager,
    WindowsSourceIngestStore,
    WorkerClient,
    BrokeredImageSourceInspector,
    BrokeredImagePageGenerator,
    BrokeredImagePageTransformer,
]:
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=WorkspaceQuota(
            maximum_case_bytes=32_000_000,
            maximum_cache_bytes=8_000_000,
            maximum_temp_bytes=8_000_000,
            maximum_entries=1_024,
            maximum_depth=16,
        ),
        id_factory=lambda: _CASE_ID,
    )
    case = WindowsCaseLifecycleStore(manager, clock=lambda: _NOW).create_case()
    case.close()
    store = WindowsSourceIngestStore(
        manager,
        WindowsSourcePrechecker(_import_limits()),
        maximum_page_count=10,
        clock=lambda: _NOW,
        id_factory=_SequentialUuidFactory(0x30900000),
        temp_id_factory=_SequentialUuidFactory(0x40900000),
    )
    worker = WorkerClient(
        WindowsProcessLauncher(
            WindowsLaunchConfig(
                python_executable=resources.python_executable,
                bootstrap_script=(
                    resources.source_root / "paperworkshell" / "worker" / "host_bootstrap.py"
                ),
                runtime_roots=(resources.runtime_root,),
                code_roots=(resources.source_root,),
                working_directory=resources.source_root,
            )
        ),
        WorkerResourcePolicy(
            process_memory_bytes=256 * 1024 * 1024,
            job_memory_bytes=384 * 1024 * 1024,
            cpu_time_ms=30_000,
            maximum_processes=1,
        ),
        WorkerTimeoutPolicy(
            handshake_seconds=20.0,
            cancellation_grace_seconds=5.0,
            shutdown_grace_seconds=10.0,
        ),
        required_capabilities=(IMAGE_NORMALIZE_CAPABILITY, IMAGE_TRANSFORM_CAPABILITY),
    )
    heavy_operations = HeavyOperationCoordinator(1)
    inspector, generator = create_image_page_brokers(
        worker,
        store,
        store,
        _broker_policy(),
        heavy_operations=heavy_operations,
    )
    transformer = BrokeredImagePageTransformer(
        worker,
        store,
        _transform_policy(),
        heavy_operations=heavy_operations,
    )
    return manager, store, worker, inspector, generator, transformer


def test_live_import_decodes_prepared_copy_then_normalizes_committed_page(
    tmp_path: Path,
    worker_test_resources: WorkerTestResources,
) -> None:
    manager, store, worker, inspector, generator, _transformer = _live_system(
        tmp_path,
        worker_test_resources,
    )
    source = tmp_path / "private-source.png"
    image = Image.new("RGBA", (4, 2), (200, 20, 10, 96))
    image.save(source, format="PNG")
    image.close()
    original = source.read_bytes()

    imported = SourceIngestService(store, inspector).import_source(
        _CASE_ID,
        source,
        _PRIVATE_DISPLAY_NAME,
    )
    case_copy = manager.layout(_CASE_ID).originals / f"{imported.source_id}.bin"
    committed_before_preview = case_copy.read_bytes()
    phases: list[ImagePagePreviewProgressPhase] = []
    preview = generator.get_or_generate(
        ImagePagePreviewRequest(
            _CASE_ID,
            imported.page_ids[0],
            maximum_width_pixels=2,
            maximum_height_pixels=2,
        ),
        deadline=lambda: False,
        cancellation=lambda: False,
        progress=phases.append,
    )

    assert (preview.snapshot.width_pixels, preview.snapshot.height_pixels) == (2, 1)
    assert preview.snapshot.metadata.source_color_model is ImageSourceColorModel.RGBA
    assert preview.snapshot.metadata.has_alpha
    assert preview.snapshot.pixels[3::4] == b"\xff" * 2
    assert phases == [
        ImagePagePreviewProgressPhase.LOOKUP,
        ImagePagePreviewProgressPhase.GENERATING,
        ImagePagePreviewProgressPhase.VALIDATING,
    ]
    assert worker.query().phase is WorkerLifecyclePhase.STOPPED
    assert worker.generation == 2
    assert source.read_bytes() == original
    assert committed_before_preview == original
    assert case_copy.read_bytes() == committed_before_preview
    assert hashlib.sha256(committed_before_preview).hexdigest() == imported.sha256
    assert tuple(manager.layout(_CASE_ID).thumbnails.iterdir()) == ()


def test_live_malformed_png_is_rejected_before_commit_without_private_diagnostics(
    tmp_path: Path,
    worker_test_resources: WorkerTestResources,
) -> None:
    manager, store, worker, inspector, _generator, _transformer = _live_system(
        tmp_path,
        worker_test_resources,
    )
    source = tmp_path / "malformed-private.png"
    image = Image.new("RGB", (4, 2), (1, 2, 3))
    image.save(source, format="PNG")
    image.close()
    corrupted = bytearray(source.read_bytes())
    idat = corrupted.index(b"IDAT") + 4
    corrupted[idat] ^= 0x01
    source.write_bytes(corrupted)
    original = source.read_bytes()

    with pytest.raises(SourceIngestError) as caught:
        SourceIngestService(store, inspector).import_source(
            _CASE_ID,
            source,
            _PRIVATE_DISPLAY_NAME,
        )

    assert caught.value.code is SourceIngestErrorCode.INSPECT_FAILED
    assert caught.value.context == "inspection.image_dependency_failed"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert _PRIVATE_DISPLAY_NAME not in str(caught.value)
    assert str(source) not in str(caught.value)
    assert source.read_bytes() == original
    assert worker.query().phase is WorkerLifecyclePhase.STOPPED
    assert tuple(manager.layout(_CASE_ID).originals.iterdir()) == ()


def test_live_transform_matches_reviewed_golden_without_mutating_source_bytes(
    tmp_path: Path,
    worker_test_resources: WorkerTestResources,
) -> None:
    manager, store, worker, inspector, _generator, transformer = _live_system(
        tmp_path,
        worker_test_resources,
    )
    source = tmp_path / "transform-source.png"
    source.write_bytes(
        canonical_rgba_png(
            source_image("crop"),
            description=f"{SYNTHETIC_MARKER}: crop-grid-16x12-v1",
        )
    )
    external_before = source.read_bytes()
    imported = SourceIngestService(store, inspector).import_source(
        _CASE_ID,
        source,
        _PRIVATE_DISPLAY_NAME,
    )
    case_copy = manager.layout(_CASE_ID).originals / f"{imported.source_id}.bin"
    committed_before = case_copy.read_bytes()
    phases: list[ImagePagePreviewProgressPhase] = []

    transformed = transformer.transform(
        ImagePageTransformRequest(
            case_id=_CASE_ID,
            page_id=imported.page_ids[0],
            intent=ImageTransformIntent(
                crop=ImageNormalizedCrop(250_000, 250_000, 500_000, 500_000),
            ),
            maximum_width_pixels=8,
            maximum_height_pixels=6,
        ),
        deadline=lambda: False,
        cancellation=lambda: False,
        progress=phases.append,
    )

    expected = decode_canonical_rgba_png(
        (PROJECT_ROOT / IMAGE_TRANSFORM_GOLDEN_DIRECTORY / "crop.png").read_bytes()
    )
    assert (transformed.snapshot.width_pixels, transformed.snapshot.height_pixels) == (
        expected.width,
        expected.height,
    )
    assert transformed.pixels == expected.pixels
    assert phases == [
        ImagePagePreviewProgressPhase.LOOKUP,
        ImagePagePreviewProgressPhase.GENERATING,
        ImagePagePreviewProgressPhase.VALIDATING,
    ]
    assert worker.query().phase is WorkerLifecyclePhase.STOPPED
    assert worker.generation == 2
    assert source.read_bytes() == external_before
    assert case_copy.read_bytes() == committed_before
    assert hashlib.sha256(committed_before).hexdigest() == imported.sha256
    assert tuple(manager.layout(_CASE_ID).thumbnails.iterdir()) == ()
    assert tuple(manager.layout(_CASE_ID).temp.iterdir()) == ()

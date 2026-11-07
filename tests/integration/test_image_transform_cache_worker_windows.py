"""A real one-shot image artifact is reusable after reopen without another launch."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from pathlib import Path
from secrets import token_hex
from typing import Never

import pytest

from paperworkshell.app.source_ingest import SourceIngestService
from paperworkshell.domain.image_pages import ImagePagePreviewProgressPhase
from paperworkshell.domain.image_transforms import (
    ImageNormalizedCrop,
    ImagePageTransformRequest,
    ImageTransformIntent,
)
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.thumbnail_cache import WindowsThumbnailCacheStore
from paperworkshell.worker.client import _windows as windows_module
from paperworkshell.worker.client._acl_recovery import AclRecoveryJournal
from paperworkshell.worker.client._windows import WindowsProcessLauncher
from paperworkshell.worker.client.models import WorkerLifecyclePhase
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.image_transform_broker import BrokeredImagePageTransformer
from tests.integration.test_image_normalize_worker_windows import (
    _CASE_ID,
    _live_system,
    _transform_policy,
)
from tests.worker_resources import WorkerTestResources, create_worker_test_recovery_path
from tools.image_transform_goldens import SYNTHETIC_MARKER, canonical_rgba_png, source_image

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native Windows image worker evidence")


@pytest.fixture(autouse=True)
def _private_recovery_authority(
    monkeypatch: pytest.MonkeyPatch,
    worker_test_resources: WorkerTestResources,
) -> Iterator[None]:
    journal = create_worker_test_recovery_path(worker_test_resources, "image-cache")
    monkeypatch.setattr(windows_module, "_acl_recovery_directory_path", lambda: journal)
    monkeypatch.setattr(
        windows_module,
        "_ACL_MUTATION_MUTEX_NAME",
        f"Global\\PaperWorkShell.Test.ImageCache.{token_hex(12)}",
    )
    yield
    if journal.exists():
        try:
            clean = AclRecoveryJournal(journal).inventory() == ()
        except BaseException:
            worker_test_resources.cleanup_guard.cleanup_safe = False
            raise
        if not clean:
            worker_test_resources.cleanup_guard.cleanup_safe = False
        assert clean


def test_real_image_artifact_survives_case_reopen_with_every_worker_launch_forbidden(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker_test_resources: WorkerTestResources,
) -> None:
    manager, sources, worker, inspector, _generator, _transformer = _live_system(
        tmp_path, worker_test_resources
    )
    external = tmp_path / "synthetic-cache-source.png"
    external.write_bytes(
        canonical_rgba_png(
            source_image("crop"), description=f"{SYNTHETIC_MARKER}: cache verification crop grid"
        )
    )
    imported = SourceIngestService(sources, inspector).import_source(
        _CASE_ID, external, "Synthetic cache source"
    )
    committed = manager.layout(_CASE_ID).originals / f"{imported.source_id}.bin"
    external_hash = hashlib.sha256(external.read_bytes()).hexdigest()
    committed_hash = hashlib.sha256(committed.read_bytes()).hexdigest()
    assert external_hash == committed_hash == imported.sha256
    cache = WindowsThumbnailCacheStore(manager)
    request = ImagePageTransformRequest(
        case_id=_CASE_ID,
        page_id=imported.page_ids[0],
        intent=ImageTransformIntent(crop=ImageNormalizedCrop(250_000, 250_000, 500_000, 500_000)),
        maximum_width_pixels=8,
        maximum_height_pixels=6,
    )
    first_broker = BrokeredImagePageTransformer(
        worker,
        sources,
        _transform_policy(),
        heavy_operations=HeavyOperationCoordinator(1),
        cache=cache,
    )
    try:
        generated = first_broker.transform(
            request,
            deadline=lambda: False,
            cancellation=lambda: False,
            progress=lambda _phase: None,
        )
        assert worker.query().phase is WorkerLifecyclePhase.STOPPED and worker.generation == 2
        final_entries = tuple(manager.layout(_CASE_ID).thumbnails.iterdir())
        assert len(final_entries) == 1 and final_entries[0].suffix == ".pwstransform"
        reopened = WindowsCaseLifecycleStore(manager).open_case(_CASE_ID)
        reopened.close()
        fresh_broker = BrokeredImagePageTransformer(
            worker,
            sources,
            _transform_policy(),
            heavy_operations=HeavyOperationCoordinator(1),
            cache=WindowsThumbnailCacheStore(manager),
        )
        phases: list[ImagePagePreviewProgressPhase] = []

        def forbidden_launch(_owner: object, _request: object) -> Never:
            raise AssertionError("A validated warm image cache hit must not launch a worker.")

        with monkeypatch.context() as no_worker:
            no_worker.setattr(WindowsProcessLauncher, "launch", forbidden_launch)
            hit = fresh_broker.transform(
                request,
                deadline=lambda: False,
                cancellation=lambda: False,
                progress=phases.append,
            )
        assert hit == generated
        assert phases == [
            ImagePagePreviewProgressPhase.LOOKUP,
            ImagePagePreviewProgressPhase.VALIDATING,
        ]
        assert worker.query().phase is WorkerLifecyclePhase.STOPPED and worker.generation == 2
        assert hashlib.sha256(external.read_bytes()).hexdigest() == external_hash
        assert hashlib.sha256(committed.read_bytes()).hexdigest() == committed_hash
        assert tuple(manager.layout(_CASE_ID).temp.iterdir()) == ()
        assert tuple(manager.layout(_CASE_ID).thumbnails.iterdir()) == final_entries
    finally:
        worker.terminate()

"""Native image pixel cache safety, shared quota and disposable recovery contracts."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from paperworkshell.domain.image_pages import ImagePageError
from paperworkshell.domain.image_transforms import (
    IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES,
    ImageTransformLookupKey,
    decode_image_transform_artifact,
)
from paperworkshell.domain.pdf_thumbnails import (
    PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES,
    PdfThumbnailError,
    PdfThumbnailErrorCode,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.storage._windows import (
    WindowsHandle,
    WindowsPendingThumbnailFile,
    publish_new_thumbnail_file,
)
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.errors import WorkspaceError, WorkspaceErrorCode
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.thumbnail_cache import (
    ImageTransformCachePublication,
    ThumbnailCacheLookupStatus,
    WindowsThumbnailCacheStore,
)
from paperworkshell.storage.workspace import CaseWorkspaceManager
from tests.security.test_case_workspace_security import _create_junction, _remove_junction
from tests.unit.test_thumbnail_cache_storage import _key as _pdf_key
from tests.unit.worker.test_image_transform_broker import _artifact, _uuid

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows authenticated cache storage")


def _encoded() -> bytes:
    return _artifact(_uuid(20), _uuid(21))


def _key() -> ImageTransformLookupKey:
    return ImageTransformLookupKey.from_cache_key(
        decode_image_transform_artifact(_encoded()).header.cache_key
    )


def _stack(
    tmp_path: Path,
    *,
    cache_limit: int | None = None,
    available: Callable[[Path], int] | None = None,
) -> tuple[CaseWorkspaceManager, WindowsThumbnailCacheStore]:
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=_quota(cache_limit),
        id_factory=lambda: _key().case_id,
        minimum_free_space_reserve_bytes=0,
        available_space_probe=available,
    )
    lease = WindowsCaseLifecycleStore(
        manager, clock=lambda: datetime(2026, 11, 6, 12, tzinfo=UTC)
    ).create_case()
    lease.close()
    return manager, WindowsThumbnailCacheStore(manager)


def _quota(cache_limit: int | None = None) -> WorkspaceQuota:
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    return WorkspaceQuota(
        maximum_case_bytes=policy.maximum_case_bytes,
        maximum_cache_bytes=policy.maximum_cache_bytes if cache_limit is None else cache_limit,
        maximum_temp_bytes=policy.maximum_temporary_storage_bytes,
        maximum_entries=512,
        maximum_depth=16,
    )


def _publish(store: WindowsThumbnailCacheStore) -> ImageTransformCachePublication:
    with store.create_image_transform_stage(_key()) as stage:
        assert not hasattr(stage, "write_handle")
        assert stage.maximum_bytes == IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES + 16
        stage.write_artifact(_encoded())
        stage.validate(expected_operation_id=_uuid(20), expected_output_id=_uuid(21))
        return stage.publish()


def test_restart_image_hit_uses_native_pixels_and_leaves_manifest_unchanged(tmp_path: Path) -> None:
    manager, store = _stack(tmp_path)
    layout = manager.layout(_key().case_id)
    manifest = layout.manifest.read_bytes()
    assert _publish(store).published
    restarted = WindowsThumbnailCacheStore(
        CaseWorkspaceManager(profile_root=manager.profile_root, quota=_quota())
    )
    hit = restarted.lookup_image_transform(_key())
    assert hit.is_hit and hit.artifact is not None
    assert hit.artifact.payload == bytes((10, 20, 30, 255)) * 4
    assert layout.manifest.read_bytes() == manifest
    assert list(layout.thumbnails.iterdir()) == [layout.thumbnails / _key().internal_name]


@pytest.mark.parametrize("fault", ("truncated", "pixels", "future_version", "wrong_source"))
def test_corrupt_native_image_cache_is_removed_before_rebuild(tmp_path: Path, fault: str) -> None:
    manager, store = _stack(tmp_path)
    _publish(store)
    final = manager.layout(_key().case_id).thumbnails / _key().internal_name
    payload = bytearray(_encoded())
    if fault == "truncated":
        payload = payload[:10]
    elif fault == "pixels":
        payload[-4] ^= 1
    elif fault == "future_version":
        payload[9] = 2
    else:
        other_key = replace(_key(), source_sha256="c" * 64)
        final.rename(final.with_name(other_key.internal_name))
        final = final.with_name(other_key.internal_name)
        outcome = store.lookup_image_transform(other_key)
        assert outcome.corrupt_removed and not final.exists()
        assert _publish(store).published
        return
    final.write_bytes(payload)
    assert store.lookup_image_transform(_key()).status is ThumbnailCacheLookupStatus.CORRUPT_REMOVED
    assert not final.exists()
    assert _publish(store).published
    assert store.lookup_image_transform(_key()).is_hit


def test_image_publication_reuses_valid_collision_without_replacing_prior_bytes(
    tmp_path: Path,
) -> None:
    manager, store = _stack(tmp_path)
    _publish(store)
    final = manager.layout(_key().case_id).thumbnails / _key().internal_name
    before = final.read_bytes()
    identity = final.stat().st_ino
    collision = _publish(store)
    assert not collision.published
    assert final.read_bytes() == before and final.stat().st_ino == identity
    assert len(list(final.parent.iterdir())) == 1


@pytest.mark.parametrize("alias", ("hardlink", "ads"))
def test_image_cache_aliases_are_rejected_without_deleting_referenced_objects(
    tmp_path: Path, alias: str
) -> None:
    manager, store = _stack(tmp_path)
    _publish(store)
    final = manager.layout(_key().case_id).thumbnails / _key().internal_name
    outside = tmp_path / "outside.bin"
    if alias == "hardlink":
        os.link(final, outside)
    else:
        Path(f"{final}:private-canary").write_bytes(b"private stream")
    with pytest.raises(PdfThumbnailError) as caught:
        store.lookup_image_transform(_key())
    assert caught.value.code is PdfThumbnailErrorCode.CACHE_INVALID
    assert final.read_bytes() == _encoded()
    if alias == "hardlink":
        assert outside.read_bytes() == _encoded()


def test_image_cache_has_no_cross_case_lookup_and_reprs_do_not_expose_pixels_or_hashes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    manager, store = _stack(tmp_path)
    _publish(store)
    hit = store.lookup_image_transform(_key())
    rendered = repr(hit) + repr(hit.artifact) + repr(_key()) + caplog.text
    assert _key().source_sha256 not in rendered
    assert _encoded().hex() not in rendered
    with pytest.raises(PdfThumbnailError):
        store.lookup_image_transform(replace(_key(), case_id=_uuid(90)))
    assert (
        manager.layout(_key().case_id).thumbnails / _key().internal_name
    ).read_bytes() == _encoded()


def test_pdf_and_image_stages_share_one_tightened_constrained_cache_reservation(
    tmp_path: Path,
) -> None:
    image_size = IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES + 16
    pdf_size = PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES + 16
    manager, store = _stack(tmp_path, cache_limit=image_size + pdf_size - 1)
    pdf_key = replace(
        _pdf_key(width=2),
        case_id=_key().case_id,
        source_id=_key().source_id,
        page_id=_uuid(4),
        maximum_height_pixels=2,
    )
    with store.create_image_transform_stage(_key()):
        with pytest.raises(PdfThumbnailError) as caught:
            store.create_stage(pdf_key)
        assert caught.value.code is PdfThumbnailErrorCode.LIMIT_EXCEEDED
    assert list(manager.layout(_key().case_id).thumbnails.iterdir()) == []
    with store.create_stage(pdf_key):
        with pytest.raises(PdfThumbnailError):
            store.create_image_transform_stage(_key())
    assert list(manager.layout(_key().case_id).thumbnails.iterdir()) == []


def test_wrong_correlated_native_stage_never_publishes_and_cleans_exact_temp(
    tmp_path: Path,
) -> None:
    manager, store = _stack(tmp_path)
    with pytest.raises(ImagePageError):
        with store.create_image_transform_stage(_key()) as stage:
            stage.write_artifact(_encoded())
            stage.validate(expected_operation_id=_uuid(22), expected_output_id=_uuid(21))
    assert list(manager.layout(_key().case_id).thumbnails.iterdir()) == []


def test_image_page_invalidation_and_stale_stage_recovery_keep_unrelated_entries(
    tmp_path: Path,
) -> None:
    manager, store = _stack(tmp_path)
    _publish(store)
    root = manager.layout(_key().case_id).thumbnails
    sibling = root / "unrelated.bin"
    sibling.write_bytes(b"keep exact unrelated bytes")
    stale = root / f"{_uuid(30)}.tmp"
    stale.write_bytes(b"interrupted image generation")
    restarted = WindowsThumbnailCacheStore(manager)
    report = restarted.cleanup_stale_stages(_key().case_id)
    assert report.removed_entries == 1 and not stale.exists()
    removed = restarted.invalidate_page(_key().case_id, _key().page_id)
    assert removed.removed_entries == 1 and not (root / _key().internal_name).exists()
    assert sibling.read_bytes() == b"keep exact unrelated bytes"


def test_low_disk_refuses_image_reservation_without_temp_or_manifest_change(tmp_path: Path) -> None:
    free = [CONSTRAINED_RESOURCE_POLICY_V1.maximum_case_bytes]
    manager, store = _stack(tmp_path, available=lambda _path: free[0])
    layout = manager.layout(_key().case_id)
    manifest = layout.manifest.read_bytes()
    free[0] = 1
    with pytest.raises(PdfThumbnailError) as caught:
        store.create_image_transform_stage(_key())
    assert caught.value.code is PdfThumbnailErrorCode.LIMIT_EXCEEDED
    assert list(layout.thumbnails.iterdir()) == []
    assert layout.manifest.read_bytes() == manifest


def test_disk_full_discards_partial_image_stage_and_restart_is_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, store = _stack(tmp_path)
    layout = manager.layout(_key().case_id)
    manifest = layout.manifest.read_bytes()
    original_write = WindowsPendingThumbnailFile.write_artifact

    def partial_then_full(stage: WindowsPendingThumbnailFile, payload: bytes) -> None:
        original_write(stage, payload[: len(payload) // 2])
        raise WorkspaceError(WorkspaceErrorCode.QUOTA_EXCEEDED, "test.full", "Synthetic disk full.")

    monkeypatch.setattr(WindowsPendingThumbnailFile, "write_artifact", partial_then_full)
    with pytest.raises(PdfThumbnailError) as caught:
        _publish(store)
    assert caught.value.code is PdfThumbnailErrorCode.LIMIT_EXCEEDED
    assert list(layout.thumbnails.iterdir()) == []
    assert not WindowsThumbnailCacheStore(manager).lookup_image_transform(_key()).is_hit
    assert layout.manifest.read_bytes() == manifest


def test_image_cache_junction_is_rejected_without_reading_or_mutating_outside(
    tmp_path: Path,
) -> None:
    manager, store = _stack(tmp_path)
    root = manager.layout(_key().case_id).thumbnails
    saved = tmp_path / "preserved-thumbnails"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / _key().internal_name
    sentinel.write_bytes(_encoded())
    root.rename(saved)
    _create_junction(root, outside)
    try:
        with pytest.raises(PdfThumbnailError):
            store.lookup_image_transform(_key())
        with pytest.raises(PdfThumbnailError):
            store.create_image_transform_stage(_key())
        assert sentinel.read_bytes() == _encoded()
        assert list(outside.iterdir()) == [sentinel]
    finally:
        _remove_junction(root)
        saved.rename(root)


@pytest.mark.parametrize("suffix", (".exe", ":stream", "/child", ".."))
def test_native_image_publication_rejects_unknown_suffixes_and_path_forms(
    tmp_path: Path, suffix: str
) -> None:
    manager, store = _stack(tmp_path)
    with store.create_image_transform_stage(_key()) as stage:
        stage.write_artifact(_encoded())
        stage.validate(expected_operation_id=_uuid(20), expected_output_id=_uuid(21))
        with pytest.raises(WorkspaceError) as caught:
            publish_new_thumbnail_file(
                stage._pending, stage._thumbnails_root, storage_name=_key().internal_name + suffix
            )
        assert caught.value.code is WorkspaceErrorCode.NAME_INVALID
    assert list(manager.layout(_key().case_id).thumbnails.iterdir()) == []


def test_open_image_cache_identity_blocks_concurrent_content_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, store = _stack(tmp_path)
    _publish(store)
    final = manager.layout(_key().case_id).thumbnails / _key().internal_name
    original_read = WindowsHandle.read_bytes
    checked: list[bool] = []

    def read_during_peer_write(handle: WindowsHandle, *, maximum_bytes: int) -> bytes:
        if handle.info.final_path == final:
            with pytest.raises(PermissionError):
                final.write_bytes(b"raced replacement must never publish")
            checked.append(True)
        return original_read(handle, maximum_bytes=maximum_bytes)

    monkeypatch.setattr(WindowsHandle, "read_bytes", read_during_peer_write)
    assert store.lookup_image_transform(_key()).is_hit
    assert checked == [True]
    assert final.read_bytes() == _encoded()

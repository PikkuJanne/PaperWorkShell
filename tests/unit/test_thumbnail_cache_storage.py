"""Focused unit evidence for PWS-0108 thumbnail-cache storage state."""

from __future__ import annotations

import hashlib
from typing import Any, cast
from uuid import UUID

import pytest

from paperworkshell.domain.pdf_rendering import (
    MAX_PDF_RENDER_DIMENSION_PIXELS,
    PdfRenderRotation,
)
from paperworkshell.domain.pdf_thumbnails import (
    PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES,
    PdfThumbnailCacheKey,
    PdfThumbnailError,
    PdfThumbnailErrorCode,
)
from paperworkshell.storage import thumbnail_cache as cache_storage
from paperworkshell.storage.thumbnail_cache import (
    ThumbnailCacheLookup,
    ThumbnailCacheLookupStatus,
    WindowsThumbnailCacheStage,
    WindowsThumbnailCacheStore,
)

CASE_ID = UUID("10000000-0000-4000-8000-000000000108")
SOURCE_ID = UUID("20000000-0000-4000-8000-000000000108")
PAGE_ID = UUID("30000000-0000-4000-8000-000000000108")
CACHE_OBJECT_ID = UUID("40000000-0000-4000-8000-000000000108")


def _key(*, width: int = 32, height: int = 24) -> PdfThumbnailCacheKey:
    return PdfThumbnailCacheKey(
        case_id=CASE_ID,
        source_id=SOURCE_ID,
        page_id=PAGE_ID,
        source_page_index=0,
        source_byte_size=29,
        source_sha256=hashlib.sha256(b"synthetic source").hexdigest(),
        maximum_width_pixels=width,
        maximum_height_pixels=height,
        additional_rotation=PdfRenderRotation.DEGREES_0,
        renderer_profile_sha256=hashlib.sha256(b"synthetic renderer profile").hexdigest(),
    )


class _FakeStack:
    def __init__(self) -> None:
        self.closes = 0

    def close(self) -> None:
        self.closes += 1


class _FakePending:
    def __init__(self) -> None:
        self.deletions = 0
        self.maximum_bytes = 64
        self.payloads: list[bytes] = []

    def mark_for_deletion(self) -> None:
        self.deletions += 1

    def write_artifact(self, payload: bytes) -> None:
        self.payloads.append(payload)


def _stage(
    store: WindowsThumbnailCacheStore,
    stack: _FakeStack,
    pending: _FakePending,
) -> WindowsThumbnailCacheStage:
    return WindowsThumbnailCacheStage(
        store=store,
        stack=cast(Any, stack),
        pending=cast(Any, pending),
        thumbnails_root=cast(Any, object()),
        key=_key(),
        cache_object_id=CACHE_OBJECT_ID,
    )


def test_maximum_stage_claim_is_exact_header_plus_bounded_rgba() -> None:
    key = _key(width=32, height=24)

    assert cache_storage._maximum_artifact_bytes(key) == (
        PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES + 32 * 24 * 4
    )


def test_oversized_stage_claim_fails_before_quota_or_filesystem_access() -> None:
    key = _key(
        width=MAX_PDF_RENDER_DIMENSION_PIXELS,
        height=MAX_PDF_RENDER_DIMENSION_PIXELS,
    )

    with pytest.raises(PdfThumbnailError) as raised:
        cache_storage._maximum_artifact_bytes(key)

    assert raised.value.code is PdfThumbnailErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "thumbnail.cache_size"


def test_lookup_result_requires_artifact_only_for_a_hit() -> None:
    miss = ThumbnailCacheLookup(ThumbnailCacheLookupStatus.MISS)
    corrupt = ThumbnailCacheLookup(ThumbnailCacheLookupStatus.CORRUPT_REMOVED)
    assert miss.artifact is None
    assert not miss.is_hit
    assert not miss.corrupt_removed
    assert not corrupt.is_hit
    assert corrupt.corrupt_removed

    with pytest.raises(PdfThumbnailError) as raised:
        ThumbnailCacheLookup(ThumbnailCacheLookupStatus.HIT)

    assert raised.value.code is PdfThumbnailErrorCode.INTERNAL_ERROR


def test_stage_exposes_only_bounded_trusted_artifact_write() -> None:
    store = WindowsThumbnailCacheStore(cast(Any, object()))
    stack = _FakeStack()
    pending = _FakePending()
    stage = _stage(store, stack, pending)

    assert stage.maximum_bytes == 64
    assert not hasattr(stage, "write_handle")

    stage.write_artifact(b"bounded artifact")

    assert pending.payloads == [b"bounded artifact"]


def test_trusted_close_deletes_stage_and_releases_live_registry() -> None:
    store = WindowsThumbnailCacheStore(cast(Any, object()))
    stack = _FakeStack()
    pending = _FakePending()
    stage = _stage(store, stack, pending)
    store._live_stage_ids.add(CACHE_OBJECT_ID)

    stage.close()
    stage.close()

    assert pending.deletions == 1
    assert stack.closes == 1
    assert CACHE_OBJECT_ID not in store._live_stage_ids
    assert CACHE_OBJECT_ID not in store._quarantined_stage_ids


def test_quarantine_preserves_stage_and_registry_across_context_cleanup() -> None:
    store = WindowsThumbnailCacheStore(cast(Any, object()))
    stack = _FakeStack()
    pending = _FakePending()
    stage = _stage(store, stack, pending)
    store._live_stage_ids.add(CACHE_OBJECT_ID)

    stage.quarantine()
    stage.close()

    assert stage.is_quarantined
    assert pending.deletions == 0
    assert stack.closes == 1
    assert CACHE_OBJECT_ID in store._live_stage_ids
    assert CACHE_OBJECT_ID in store._quarantined_stage_ids
    assert "synthetic source" not in repr(stage)
    assert _key().source_sha256 not in repr(stage)

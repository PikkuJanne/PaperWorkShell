"""Warm image reopen uses authenticated raw pixels without another parser launch."""

from __future__ import annotations

from dataclasses import replace
from types import TracebackType
from typing import Self
from uuid import UUID

import pytest

from paperworkshell.domain.image_pages import ImagePageError, ImagePageErrorCode
from paperworkshell.domain.image_transforms import (
    IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES,
    ImagePageTransformResult,
    ImageTransformArtifact,
    ImageTransformLookupKey,
    decode_image_transform_artifact,
)
from paperworkshell.domain.pdf_thumbnails import PdfThumbnailError, PdfThumbnailErrorCode
from paperworkshell.storage.thumbnail_cache import (
    ImageTransformCacheLookup,
    ImageTransformCachePublication,
    ThumbnailCacheLookupStatus,
)
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.image_transform_broker import (
    BrokeredImagePageTransformer,
    ImageTransformBrokerPolicy,
)
from tests.unit.worker.test_image_transform_broker import (
    _OutputBuffer,
    _policy,
    _request,
    _SourceLease,
    _SourceStore,
    _uuid,
    _Worker,
)


class _Cache:
    def __init__(self) -> None:
        self.entries: dict[ImageTransformLookupKey, bytes] = {}
        self.events: list[str] = []
        self.worker: _Worker | None = None
        self.override: ImageTransformArtifact | None = None
        self.cancelled = False
        self.cancel_after_lookup = False
        self.cancel_after_validate = False
        self.fail_write = False

    def lookup_image_transform(self, key: ImageTransformLookupKey) -> ImageTransformCacheLookup:
        self.events.append("cache.lookup")
        self.cancelled = self.cancel_after_lookup
        if self.override is not None:
            return ImageTransformCacheLookup(ThumbnailCacheLookupStatus.HIT, self.override)
        encoded = self.entries.get(key)
        if encoded is None:
            return ImageTransformCacheLookup(ThumbnailCacheLookupStatus.MISS)
        return ImageTransformCacheLookup(
            ThumbnailCacheLookupStatus.HIT,
            decode_image_transform_artifact(encoded, expected_lookup_key=key),
        )

    def create_image_transform_stage(self, key: ImageTransformLookupKey) -> _Stage:
        self.events.append("cache.stage")
        return _Stage(self, key)


class _Stage:
    def __init__(self, cache: _Cache, key: ImageTransformLookupKey) -> None:
        self.cache = cache
        self.key = key
        # The public stage reserves exactly the requested bounded RGBA envelope.
        self.maximum_bytes = IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES + 2 * 2 * 4
        self.encoded = b""

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.cache.events.append("cache.stage.close")

    def write_artifact(self, payload: bytes) -> None:
        assert self.cache.worker is not None and self.cache.worker.terminated
        self.cache.events.append("cache.write.after_tree_zero")
        if self.cache.fail_write:
            raise PdfThumbnailError(PdfThumbnailErrorCode.DESTINATION_FAILED, "test.disk_full")
        self.encoded = payload

    def validate(
        self, *, expected_operation_id: UUID, expected_output_id: UUID
    ) -> ImageTransformArtifact:
        artifact = decode_image_transform_artifact(
            self.encoded,
            expected_operation_id=expected_operation_id,
            expected_output_id=expected_output_id,
            expected_lookup_key=self.key,
        )
        self.cache.cancelled = self.cache.cancel_after_validate
        return artifact

    def publish(self) -> ImageTransformCachePublication:
        self.cache.events.append("cache.publish")
        self.cache.entries[self.key] = self.encoded
        return ImageTransformCachePublication(
            decode_image_transform_artifact(self.encoded, expected_lookup_key=self.key), True
        )


def _system(
    cache: _Cache, *, changed_source: bool = False, policy: ImageTransformBrokerPolicy | None = None
) -> tuple[BrokeredImagePageTransformer, _Worker, list[str]]:
    events = cache.events
    worker = _Worker(events)
    cache.worker = worker
    identifiers = iter((_uuid(20), _uuid(21)))

    class Sources(_SourceStore):
        def borrow_committed_image_page(self, *args: object, **kwargs: object) -> _SourceLease:
            del args, kwargs
            self.events.append("source.borrow")
            source = _SourceLease(self.events)
            if changed_source:
                source.source_sha256 = "c" * 64
            return source

    return (
        BrokeredImagePageTransformer(
            worker,
            Sources(events),
            _policy() if policy is None else policy,
            heavy_operations=HeavyOperationCoordinator(1),
            cache=cache,
            id_factory=lambda: next(identifiers),
            output_buffer_factory=lambda capacity: _OutputBuffer(capacity, worker, events),
        ),
        worker,
        events,
    )


def _transform(
    transformer: BrokeredImagePageTransformer, cache: _Cache
) -> ImagePageTransformResult:
    return transformer.transform(
        _request(),
        deadline=lambda: False,
        cancellation=lambda: cache.cancelled,
        progress=lambda _phase: None,
    )


def test_saved_raw_image_cache_survives_fresh_broker_without_any_worker_launch() -> None:
    cache = _Cache()
    transformer, worker, events = _system(cache)
    generated = _transform(transformer, cache)
    assert worker.terminated and len(cache.entries) == 1
    assert events.index("worker.terminate") < events.index("cache.write.after_tree_zero")
    assert events.index("cache.publish") < events.index("source.exit")
    events.clear()

    reopened, next_worker, _events = _system(cache)
    assert _transform(reopened, cache) == generated
    assert events == ["source.borrow", "source.enter", "cache.lookup", "source.exit"]
    assert next_worker.payload is None and not next_worker.terminated


def test_changed_authenticated_source_never_reuses_old_pixels() -> None:
    cache = _Cache()
    first, _worker, events = _system(cache)
    _transform(first, cache)
    original_entries = dict(cache.entries)
    events.clear()

    changed, worker, _events = _system(cache, changed_source=True)
    with pytest.raises(ImagePageError) as caught:
        _transform(changed, cache)
    assert caught.value.code is ImagePageErrorCode.RESULT_INVALID
    assert "worker.start" in events and worker.terminated
    assert cache.entries == original_entries


def test_current_resource_limits_are_enforced_before_even_a_valid_warm_hit() -> None:
    cache = _Cache()
    first, _worker, events = _system(cache)
    _transform(first, cache)
    events.clear()
    limited = ImageTransformBrokerPolicy(
        replace(_policy().limits, output_bytes=IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES + 4)
    )
    reopened, worker, _events = _system(cache, policy=limited)
    with pytest.raises(ImagePageError) as caught:
        _transform(reopened, cache)
    assert caught.value.code is ImagePageErrorCode.LIMIT_EXCEEDED
    assert "cache.lookup" not in events and worker.payload is None


def test_typed_but_wrong_source_cache_hit_is_rejected_before_worker_or_publication() -> None:
    cache = _Cache()
    first, _worker, events = _system(cache)
    _transform(first, cache)
    encoded = next(iter(cache.entries.values()))
    artifact = decode_image_transform_artifact(encoded)
    wrong = replace(artifact.header.cache_key, source_sha256="c" * 64)
    cache.override = ImageTransformArtifact(
        replace(artifact.header, cache_key=wrong), artifact.payload
    )
    events.clear()
    reopened, worker, _events = _system(cache)

    with pytest.raises(ImagePageError) as caught:
        _transform(reopened, cache)
    assert caught.value.code is ImagePageErrorCode.RESULT_INVALID
    assert "worker.start" not in events and "cache.publish" not in events
    assert worker.payload is None and events[-1] == "source.exit"


@pytest.mark.parametrize("point", ("lookup", "validate"))
def test_cancellation_during_cache_work_never_publishes_or_returns_pixels(point: str) -> None:
    cache = _Cache()
    if point == "lookup":
        first, _worker, _events = _system(cache)
        _transform(first, cache)
        cache.cancel_after_lookup = True
    else:
        cache.cancel_after_validate = True
    cache.events.clear()
    original_entries = dict(cache.entries)
    transformer, worker, events = _system(cache)
    with pytest.raises(ImagePageError) as caught:
        _transform(transformer, cache)
    assert caught.value.code is ImagePageErrorCode.CANCELLED
    assert cache.entries == original_entries and "cache.publish" not in events
    assert events[-1] == "source.exit"
    if point == "lookup":
        assert "worker.start" not in events
    else:
        assert worker.terminated and "cache.stage.close" in events


def test_disk_full_during_cache_write_cleans_stage_after_stopped_worker() -> None:
    cache = _Cache()
    cache.fail_write = True
    transformer, worker, events = _system(cache)
    with pytest.raises(ImagePageError) as caught:
        _transform(transformer, cache)
    assert caught.value.code is ImagePageErrorCode.DESTINATION_FAILED
    assert worker.terminated and not cache.entries
    assert "cache.stage.close" in events and "cache.publish" not in events
    assert events[-1] == "source.exit"

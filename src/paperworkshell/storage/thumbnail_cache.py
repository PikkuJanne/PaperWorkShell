"""Authenticated, quota-bounded storage for fixed-layout PDF and image pixels."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Final, Self
from uuid import UUID, uuid4

from paperworkshell.domain.image_pages import ImagePageError
from paperworkshell.domain.image_transforms import (
    IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES,
    MAX_IMAGE_TRANSFORM_ARTIFACT_BYTES,
    ImageTransformArtifact,
    ImageTransformLookupKey,
    decode_image_transform_artifact,
)
from paperworkshell.domain.pdf_thumbnails import (
    MAX_PDF_THUMBNAIL_ARTIFACT_BYTES,
    PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES,
    PdfThumbnailArtifact,
    PdfThumbnailCacheKey,
    PdfThumbnailError,
    PdfThumbnailErrorCode,
    decode_pdf_thumbnail_artifact,
    reject_pdf_thumbnail,
)
from paperworkshell.storage._windows import (
    CachePublishState,
    WindowsHandle,
    WindowsPendingThumbnailFile,
    create_pending_thumbnail_file,
    open_handle,
    publish_new_thumbnail_file,
    require_expected_handle,
    require_no_alternate_streams,
    verify_private_acl,
)
from paperworkshell.storage.errors import WorkspaceError, WorkspaceErrorCode
from paperworkshell.storage.quotas import QuotaArea
from paperworkshell.storage.workspace import (
    AuthenticatedCase,
    CaseWorkspaceManager,
    WorkspaceCapacityReservation,
    validate_internal_component,
)

_MAXIMUM_CACHE_OBJECT_ID_ATTEMPTS: Final = 32
_DEFAULT_MAXIMUM_CLEANUP_ENTRIES: Final = 4_096
_CACHE_ENTRY_NAME: Final = re.compile(
    r"(?P<page>[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-"
    r"[0-9a-f]{12})\.(?P<key>[0-9a-f]{64})\.(?:pwsthumb|pwstransform)\Z"
)
_CACHE_STAGE_NAME: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-"
    r"[0-9a-f]{12}\.tmp\Z"
)


class ThumbnailCacheLookupStatus(StrEnum):
    """Concrete outcome of one authenticated cache lookup."""

    MISS = "miss"
    HIT = "hit"
    CORRUPT_REMOVED = "corrupt_removed"


@dataclass(frozen=True, slots=True)
class ThumbnailCacheLookup:
    """Path-free lookup result; pixel bytes remain hidden by the artifact repr."""

    status: ThumbnailCacheLookupStatus
    artifact: PdfThumbnailArtifact | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.status) is not ThumbnailCacheLookupStatus
            or (
                self.status is ThumbnailCacheLookupStatus.HIT
                and type(self.artifact) is not PdfThumbnailArtifact
            )
            or (self.status is not ThumbnailCacheLookupStatus.HIT and self.artifact is not None)
        ):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.cache_lookup")

    @property
    def is_hit(self) -> bool:
        return self.status is ThumbnailCacheLookupStatus.HIT

    @property
    def corrupt_removed(self) -> bool:
        return self.status is ThumbnailCacheLookupStatus.CORRUPT_REMOVED


@dataclass(frozen=True, slots=True)
class ThumbnailCachePublication:
    """Validated publication result, including safe reuse after a key collision."""

    artifact: PdfThumbnailArtifact = field(repr=False)
    published: bool
    delayed_exception: BaseException | None = field(
        default=None,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True, slots=True)
class ImageTransformCacheLookup:
    """Image-only lookup result; never a source parser or PDF artifact."""

    status: ThumbnailCacheLookupStatus
    artifact: ImageTransformArtifact | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.status) is not ThumbnailCacheLookupStatus
            or (
                self.status is ThumbnailCacheLookupStatus.HIT
                and type(self.artifact) is not ImageTransformArtifact
            )
            or (self.status is not ThumbnailCacheLookupStatus.HIT and self.artifact is not None)
        ):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.image_lookup")

    @property
    def is_hit(self) -> bool:
        return self.status is ThumbnailCacheLookupStatus.HIT

    @property
    def corrupt_removed(self) -> bool:
        return self.status is ThumbnailCacheLookupStatus.CORRUPT_REMOVED


@dataclass(frozen=True, slots=True)
class ImageTransformCachePublication:
    artifact: ImageTransformArtifact = field(repr=False)
    published: bool
    delayed_exception: BaseException | None = field(default=None, repr=False, compare=False)


class ThumbnailCleanupStatus(StrEnum):
    """Whether a bounded exact-entry cleanup completed its selected inventory."""

    COMPLETE = "complete"
    PARTIAL = "partial"


@dataclass(frozen=True, slots=True)
class ThumbnailCleanupReport:
    """Content-free accounting for page invalidation or stale-stage cleanup."""

    status: ThumbnailCleanupStatus
    examined_entries: int
    removed_entries: int
    retained_entries: int
    released_bytes: int
    failure_code: PdfThumbnailErrorCode | None = None
    unexpected_failure: bool = False


class _WindowsArtifactCacheStage[
    K: (PdfThumbnailCacheKey, ImageTransformLookupKey),
    A: (PdfThumbnailArtifact, ImageTransformArtifact),
    P,
](AbstractContextManager["_WindowsArtifactCacheStage[K, A, P]"]):
    """Quota-reserved exact cache stage retained through validation and publication."""

    __slots__ = (
        "_artifact",
        "_cache_object_id",
        "_closed",
        "_key",
        "_pending",
        "_published",
        "_quarantined",
        "_stack",
        "_store",
        "_thumbnails_root",
    )

    def __init__(
        self,
        *,
        store: WindowsThumbnailCacheStore,
        stack: ExitStack,
        pending: WindowsPendingThumbnailFile,
        thumbnails_root: WindowsHandle,
        key: K,
        cache_object_id: UUID,
    ) -> None:
        self._store = store
        self._stack = stack
        self._pending = pending
        self._thumbnails_root = thumbnails_root
        self._key: K = key
        self._cache_object_id = cache_object_id
        self._artifact: A | None = None
        self._published = False
        self._quarantined = False
        self._closed = False

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"case_id={self._key.case_id!r}, page_id={self._key.page_id!r}, "
            f"cache_object_id={self._cache_object_id!r})"
        )

    def __enter__(self) -> Self:
        self._require_open(context="thumbnail.stage_enter")
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @property
    def key(self) -> K:
        return self._key

    @property
    def cache_object_id(self) -> UUID:
        return self._cache_object_id

    @property
    def maximum_bytes(self) -> int:
        """Return the exact reserved upper bound for this artifact stage."""

        self._require_open(context="thumbnail.stage_limit")
        return self._pending.maximum_bytes

    def write_artifact(self, payload: bytes) -> None:
        """Persist one already-bounded broker-validated artifact in trusted storage."""

        self._require_open(context="thumbnail.stage_write")
        if self._quarantined or self._artifact is not None:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.DESTINATION_FAILED, "thumbnail.stage_state")
        if type(payload) is not bytes or not payload or len(payload) > self._pending.maximum_bytes:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.LIMIT_EXCEEDED, "thumbnail.stage_size")
        try:
            self._pending.write_artifact(payload)
        except WorkspaceError as error:
            code = (
                PdfThumbnailErrorCode.LIMIT_EXCEEDED
                if error.code is WorkspaceErrorCode.QUOTA_EXCEEDED
                else PdfThumbnailErrorCode.DESTINATION_FAILED
            )
            reject_pdf_thumbnail(code, "thumbnail.stage_write")
        except Exception:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.DESTINATION_FAILED, "thumbnail.stage_write")

    @property
    def is_quarantined(self) -> bool:
        return self._quarantined

    def validate(self, *, expected_operation_id: UUID, expected_output_id: UUID | None = None) -> A:
        """Seal and fully validate exact broker-staged output before publication."""

        self._require_open(context="thumbnail.stage_validate")
        if self._quarantined or self._artifact is not None:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.DESTINATION_FAILED, "thumbnail.stage_state")
        try:
            self._pending.seal()
            payload = self._pending.read_bytes()
            artifact = self._decode(
                payload,
                expected_operation_id=expected_operation_id,
                expected_output_id=expected_output_id,
            )
        except PdfThumbnailError, ImagePageError:
            raise
        except WorkspaceError:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.stage_storage")
        except Exception:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.stage_storage")
        self._artifact = artifact
        return artifact

    def publish(self) -> P:
        """No-replace publish the validated stage or reuse an equivalent valid collision."""

        self._require_open(context="thumbnail.stage_publish")
        if self._quarantined or self._artifact is None:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.DESTINATION_FAILED, "thumbnail.stage_state")
        try:
            result = publish_new_thumbnail_file(
                self._pending,
                self._thumbnails_root,
                storage_name=self._key.internal_name,
            )
            if result.state is CachePublishState.DESTINATION_EXISTS:
                artifact = self._lookup_collision()
                if artifact is not None:
                    publication = self._publication(artifact, False, result.delayed_exception)
                    self.discard()
                    return publication
                result = publish_new_thumbnail_file(
                    self._pending,
                    self._thumbnails_root,
                    storage_name=self._key.internal_name,
                )
            if result.state is CachePublishState.PUBLISHED:
                publication = self._publication(self._artifact, True, result.delayed_exception)
                self._published = True
                self.close()
                return publication
            if result.state is CachePublishState.UNKNOWN:
                self.quarantine()
            reject_pdf_thumbnail(PdfThumbnailErrorCode.DESTINATION_FAILED, "thumbnail.publish")
        except PdfThumbnailError, ImagePageError:
            raise
        except WorkspaceError:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.DESTINATION_FAILED, "thumbnail.publish")
        except Exception:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.DESTINATION_FAILED, "thumbnail.publish")

    def quarantine(self) -> None:
        """Retain the stage only when persistent publication state is uncertain."""

        self._require_open(context="thumbnail.stage_quarantine")
        self._quarantined = True
        self._store._quarantine_stage(self._cache_object_id)

    def discard(self) -> None:
        """Delete only a trusted unpublished stage; quarantined stages are preserved."""

        if self._closed:
            return
        if not self._published and not self._quarantined:
            try:
                self._pending.mark_for_deletion()
            except BaseException:
                pass
        self._closed = True
        try:
            self._stack.close()
        except BaseException:
            pass
        if not self._quarantined:
            self._store._release_stage(self._cache_object_id)

    def close(self) -> None:
        self.discard()

    def _require_open(self, *, context: str) -> None:
        if self._closed:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.DESTINATION_FAILED, context)

    def _decode(
        self, payload: bytes, *, expected_operation_id: UUID, expected_output_id: UUID | None
    ) -> A:
        raise NotImplementedError

    def _lookup_collision(self) -> A | None:
        raise NotImplementedError

    def _publication(self, artifact: A, published: bool, delayed: BaseException | None) -> P:
        raise NotImplementedError


class WindowsThumbnailCacheStage(
    _WindowsArtifactCacheStage[
        PdfThumbnailCacheKey, PdfThumbnailArtifact, ThumbnailCachePublication
    ]
):
    """The unchanged PDF artifact boundary over the shared secure file lifecycle."""

    __slots__ = ()

    def _decode(
        self, payload: bytes, *, expected_operation_id: UUID, expected_output_id: UUID | None
    ) -> PdfThumbnailArtifact:
        if expected_output_id is not None and expected_output_id != self._cache_object_id:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.stage_output")
        return decode_pdf_thumbnail_artifact(
            payload,
            expected_key=self._key,
            expected_operation_id=expected_operation_id,
            expected_cache_object_id=self._cache_object_id,
        )

    def _lookup_collision(self) -> PdfThumbnailArtifact | None:
        return self._store._lookup_under_root(self._key, self._thumbnails_root).artifact

    def _publication(
        self, artifact: PdfThumbnailArtifact, published: bool, delayed: BaseException | None
    ) -> ThumbnailCachePublication:
        return ThumbnailCachePublication(artifact, published, delayed)


class WindowsImageTransformCacheStage(
    _WindowsArtifactCacheStage[
        ImageTransformLookupKey, ImageTransformArtifact, ImageTransformCachePublication
    ]
):
    """Strict native image artifacts, without introducing an image file decoder."""

    __slots__ = ()

    def _decode(
        self, payload: bytes, *, expected_operation_id: UUID, expected_output_id: UUID | None
    ) -> ImageTransformArtifact:
        if expected_output_id is None:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.image_output")
        return decode_image_transform_artifact(
            payload,
            expected_lookup_key=self._key,
            expected_operation_id=expected_operation_id,
            expected_output_id=expected_output_id,
        )

    def _lookup_collision(self) -> ImageTransformArtifact | None:
        return self._store._lookup_image_under_root(self._key, self._thumbnails_root).artifact

    def _publication(
        self, artifact: ImageTransformArtifact, published: bool, delayed: BaseException | None
    ) -> ImageTransformCachePublication:
        return ImageTransformCachePublication(artifact, published, delayed)


class WindowsThumbnailCacheStore:
    """Case-scoped broker for validated thumbnail snapshots and disposable cleanup."""

    __slots__ = (
        "_id_factory",
        "_live_stage_ids",
        "_manager",
        "_maximum_cleanup_entries",
        "_quarantined_stage_ids",
        "_stage_lock",
    )

    def __init__(
        self,
        manager: CaseWorkspaceManager,
        *,
        id_factory: Callable[[], UUID] = uuid4,
        maximum_cleanup_entries: int = _DEFAULT_MAXIMUM_CLEANUP_ENTRIES,
    ) -> None:
        if not callable(id_factory) or (
            type(maximum_cleanup_entries) is not int
            or not 1 <= maximum_cleanup_entries <= _DEFAULT_MAXIMUM_CLEANUP_ENTRIES
        ):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "thumbnail.cache_config")
        self._manager = manager
        self._id_factory = id_factory
        self._maximum_cleanup_entries = maximum_cleanup_entries
        self._stage_lock = RLock()
        self._live_stage_ids: set[UUID] = set()
        self._quarantined_stage_ids: set[UUID] = set()

    def lookup(self, key: PdfThumbnailCacheKey) -> ThumbnailCacheLookup:
        """Open, read, and validate one exact final cache entry; corruption becomes a miss."""

        safe_key = _require_cache_key(key)
        try:
            with ExitStack() as stack:
                authority = stack.enter_context(self._manager.authenticate_case(safe_key.case_id))
                _cache_root, thumbnails_root = _open_thumbnail_roots(stack, authority)
                return self._lookup_under_root(safe_key, thumbnails_root)
        except PdfThumbnailError:
            raise
        except WorkspaceError:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.lookup_storage")
        except Exception:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.lookup_storage")

    def create_stage(self, key: PdfThumbnailCacheKey) -> WindowsThumbnailCacheStage:
        """Reserve the maximum artifact size and create one random exact cache stage."""

        safe_key = _require_cache_key(key)
        return self._create_stage(
            safe_key,
            _maximum_artifact_bytes(safe_key),
            lambda stack, pending, root, object_id: WindowsThumbnailCacheStage(
                store=self,
                stack=stack,
                pending=pending,
                thumbnails_root=root,
                key=safe_key,
                cache_object_id=object_id,
            ),
        )

    def lookup_image_transform(self, key: ImageTransformLookupKey) -> ImageTransformCacheLookup:
        """Validate one case-private native RGBA image artifact before returning a hit."""

        safe_key = _require_image_lookup_key(key)
        try:
            with ExitStack() as stack:
                authority = stack.enter_context(self._manager.authenticate_case(safe_key.case_id))
                _cache_root, thumbnails_root = _open_thumbnail_roots(stack, authority)
                return self._lookup_image_under_root(safe_key, thumbnails_root)
        except PdfThumbnailError, ImagePageError:
            raise
        except Exception:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.image_lookup")

    def create_image_transform_stage(
        self, key: ImageTransformLookupKey
    ) -> WindowsImageTransformCacheStage:
        safe_key = _require_image_lookup_key(key)
        return self._create_stage(
            safe_key,
            _maximum_image_artifact_bytes(safe_key),
            lambda stack, pending, root, object_id: WindowsImageTransformCacheStage(
                store=self,
                stack=stack,
                pending=pending,
                thumbnails_root=root,
                key=safe_key,
                cache_object_id=object_id,
            ),
        )

    def _create_stage[S](
        self,
        safe_key: PdfThumbnailCacheKey | ImageTransformLookupKey,
        maximum_bytes: int,
        build: Callable[[ExitStack, WindowsPendingThumbnailFile, WindowsHandle, UUID], S],
    ) -> S:
        """One reservation, identity, publication guard and live-stage registry for both codecs."""

        cache_object_id = self._allocate_cache_object_id(safe_key)
        stage_name = f"{cache_object_id}.tmp"
        relative_path = f"cache/thumbnails/{stage_name}"
        reservation: WorkspaceCapacityReservation | None = None
        stack = ExitStack()
        complete = False
        try:
            reservation = self._manager.reserve_capacity(
                safe_key.case_id,
                QuotaArea.CACHE,
                relative_path=relative_path,
                is_directory=False,
                byte_count=maximum_bytes,
            )
            stack.enter_context(reservation)
            authority = stack.enter_context(
                self._manager.authenticate_case(safe_key.case_id, retain_lease=True)
            )
            stack.enter_context(
                reservation.publication_guard(
                    case_identity=authority.identity,
                    relative_parts=("cache", "thumbnails", stage_name),
                    is_directory=False,
                    byte_count=maximum_bytes,
                    entry_count=1,
                )
            )
            _cache_root, thumbnails_root = _open_thumbnail_roots(stack, authority)
            pending = create_pending_thumbnail_file(
                authority.layout.thumbnails / stage_name,
                thumbnails_root=thumbnails_root,
                maximum_bytes=maximum_bytes,
            )
            stack.enter_context(pending)
            stage = build(stack, pending, thumbnails_root, cache_object_id)
            with self._stage_lock:
                self._live_stage_ids.add(cache_object_id)
            complete = True
            return stage
        except PdfThumbnailError, ImagePageError:
            raise
        except WorkspaceError as error:
            code = (
                PdfThumbnailErrorCode.LIMIT_EXCEEDED
                if error.code is WorkspaceErrorCode.QUOTA_EXCEEDED
                else PdfThumbnailErrorCode.DESTINATION_FAILED
            )
            reject_pdf_thumbnail(code, "thumbnail.stage_create")
        except Exception:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.DESTINATION_FAILED, "thumbnail.stage_create")
        finally:
            if not complete:
                try:
                    stack.close()
                except BaseException:
                    pass

    def invalidate_key(self, key: PdfThumbnailCacheKey) -> bool:
        """Delete one exact safe cache key without touching siblings or unknown names."""

        return self._invalidate_key(_require_cache_key(key))

    def invalidate_image_transform_key(self, key: ImageTransformLookupKey) -> bool:
        return self._invalidate_key(_require_image_lookup_key(key))

    def _invalidate_key(self, safe_key: PdfThumbnailCacheKey | ImageTransformLookupKey) -> bool:
        try:
            with ExitStack() as stack:
                authority = stack.enter_context(self._manager.authenticate_case(safe_key.case_id))
                _cache_root, thumbnails_root = _open_thumbnail_roots(stack, authority)
                path = authority.layout.thumbnails / safe_key.internal_name
                removed, _released = _delete_exact_regular(path, expected_root=thumbnails_root)
                return removed
        except PdfThumbnailError:
            raise
        except WorkspaceError:
            reject_pdf_thumbnail(
                PdfThumbnailErrorCode.DESTINATION_FAILED, "thumbnail.invalidate_key"
            )
        except Exception:
            reject_pdf_thumbnail(
                PdfThumbnailErrorCode.DESTINATION_FAILED, "thumbnail.invalidate_key"
            )

    def invalidate_page(self, case_id: UUID, page_id: UUID) -> ThumbnailCleanupReport:
        """Boundedly delete final cache keys whose internal page UUID exactly matches."""

        selected_case = _require_uuid4(case_id, context="thumbnail.invalidate_case")
        selected_page = _require_uuid4(page_id, context="thumbnail.invalidate_page")
        if selected_case == selected_page:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "thumbnail.invalidate_ids")
        try:
            with ExitStack() as stack:
                authority = stack.enter_context(self._manager.authenticate_case(selected_case))
                _cache_root, thumbnails_root = _open_thumbnail_roots(stack, authority)
                names = self._bounded_names(authority.layout.thumbnails)
                selected = [
                    name
                    for name in names
                    if (match := _CACHE_ENTRY_NAME.fullmatch(name)) is not None
                    and match.group("page") == str(selected_page)
                ]
                return _delete_selected_names(
                    selected,
                    root_path=authority.layout.thumbnails,
                    root_handle=thumbnails_root,
                )
        except PdfThumbnailError:
            raise
        except WorkspaceError:
            reject_pdf_thumbnail(
                PdfThumbnailErrorCode.DESTINATION_FAILED, "thumbnail.invalidate_page"
            )
        except Exception:
            reject_pdf_thumbnail(
                PdfThumbnailErrorCode.DESTINATION_FAILED, "thumbnail.invalidate_page"
            )

    def cleanup_stale_stages(self, case_id: UUID) -> ThumbnailCleanupReport:
        """Boundedly remove inactive UUID-named stages while retaining live or unsafe entries."""

        selected_case = _require_uuid4(case_id, context="thumbnail.cleanup_case")
        report: ThumbnailCleanupReport | None = None
        selected: list[str] = []
        registry_retained = 0
        removed = 0
        retained = 0
        released = 0
        failure: PdfThumbnailErrorCode | None = None
        report_ready = False
        unexpected_failure = False
        try:
            with ExitStack() as stack:
                authority = stack.enter_context(self._manager.authenticate_case(selected_case))
                _cache_root, thumbnails_root = _open_thumbnail_roots(stack, authority)
                names = self._bounded_names(authority.layout.thumbnails)
                with self._stage_lock:
                    retained_ids = self._live_stage_ids | self._quarantined_stage_ids
                for name in names:
                    if _CACHE_STAGE_NAME.fullmatch(name) is None:
                        continue
                    stage_id = UUID(name.removesuffix(".tmp"))
                    if stage_id in retained_ids:
                        registry_retained += 1
                    else:
                        selected.append(name)
                retained = registry_retained
                report_ready = True
                for index, name in enumerate(selected):
                    try:
                        was_removed, byte_count = _delete_exact_regular(
                            authority.layout.thumbnails / name,
                            expected_root=thumbnails_root,
                        )
                    except PdfThumbnailError, WorkspaceError:
                        retained += 1
                        failure = PdfThumbnailErrorCode.DESTINATION_FAILED
                        continue
                    except Exception:
                        retained += len(selected) - index
                        failure = PdfThumbnailErrorCode.DESTINATION_FAILED
                        unexpected_failure = True
                        break
                    if was_removed:
                        removed += 1
                        released += byte_count
                report = ThumbnailCleanupReport(
                    status=(
                        ThumbnailCleanupStatus.COMPLETE
                        if failure is None
                        else ThumbnailCleanupStatus.PARTIAL
                    ),
                    examined_entries=len(selected) + registry_retained,
                    removed_entries=removed,
                    retained_entries=retained,
                    released_bytes=released,
                    failure_code=failure,
                    unexpected_failure=unexpected_failure,
                )
        except PdfThumbnailError:
            if report_ready:
                return _partial_thumbnail_cleanup_report(
                    selected_entries=len(selected),
                    registry_retained=registry_retained,
                    removed_entries=removed,
                    retained_entries=retained,
                    released_bytes=released,
                )
            raise
        except WorkspaceError:
            if report_ready:
                return _partial_thumbnail_cleanup_report(
                    selected_entries=len(selected),
                    registry_retained=registry_retained,
                    removed_entries=removed,
                    retained_entries=retained,
                    released_bytes=released,
                )
            reject_pdf_thumbnail(
                PdfThumbnailErrorCode.DESTINATION_FAILED, "thumbnail.cleanup_stage"
            )
        except Exception:
            if report_ready:
                return _partial_thumbnail_cleanup_report(
                    selected_entries=len(selected),
                    registry_retained=registry_retained,
                    removed_entries=removed,
                    retained_entries=retained,
                    released_bytes=released,
                )
            reject_pdf_thumbnail(
                PdfThumbnailErrorCode.DESTINATION_FAILED, "thumbnail.cleanup_stage"
            )
        if report is None:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.cleanup_report")
        return report

    def _lookup_under_root(
        self,
        key: PdfThumbnailCacheKey,
        thumbnails_root: WindowsHandle,
    ) -> ThumbnailCacheLookup:
        status, artifact = self._read_under_root(
            key,
            thumbnails_root,
            minimum_bytes=PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES,
            maximum_bytes=_maximum_artifact_bytes(key),
            decode=lambda payload: decode_pdf_thumbnail_artifact(payload, expected_key=key),
        )
        return ThumbnailCacheLookup(status, artifact)

    def _lookup_image_under_root(
        self,
        key: ImageTransformLookupKey,
        thumbnails_root: WindowsHandle,
    ) -> ImageTransformCacheLookup:
        status, artifact = self._read_under_root(
            key,
            thumbnails_root,
            minimum_bytes=IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES,
            maximum_bytes=_maximum_image_artifact_bytes(key),
            decode=lambda payload: decode_image_transform_artifact(
                payload, expected_lookup_key=key
            ),
        )
        return ImageTransformCacheLookup(status, artifact)

    def _read_under_root[A](
        self,
        key: PdfThumbnailCacheKey | ImageTransformLookupKey,
        thumbnails_root: WindowsHandle,
        *,
        minimum_bytes: int,
        maximum_bytes: int,
        decode: Callable[[bytes], A],
    ) -> tuple[ThumbnailCacheLookupStatus, A | None]:
        path = thumbnails_root.info.final_path / key.internal_name
        if not os.path.lexists(path):
            return ThumbnailCacheLookupStatus.MISS, None
        try:
            with open_handle(path, read=True, delete=True, share_delete=True) as handle:
                require_expected_handle(handle, path, directory=False)
                initial = handle.info
                if initial.is_sparse or initial.link_count != 1:
                    reject_pdf_thumbnail(
                        PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.cache_identity"
                    )
                require_no_alternate_streams(path)
                if not (minimum_bytes <= initial.logical_size <= maximum_bytes):
                    try:
                        handle.mark_for_deletion()
                    except WorkspaceError:
                        reject_pdf_thumbnail(
                            PdfThumbnailErrorCode.CACHE_INVALID,
                            "thumbnail.cache_cleanup",
                        )
                    return ThumbnailCacheLookupStatus.CORRUPT_REMOVED, None
                handle.rewind()
                payload = handle.read_bytes(maximum_bytes=maximum_bytes)
                current = handle.refresh_info()
                if (
                    current.identity != initial.identity
                    or current.logical_size != initial.logical_size
                    or current.is_sparse
                    or current.link_count != 1
                ):
                    reject_pdf_thumbnail(
                        PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.cache_changed"
                    )
                require_no_alternate_streams(path)
                try:
                    artifact = decode(payload)
                except PdfThumbnailError, ImagePageError:
                    try:
                        handle.mark_for_deletion()
                    except WorkspaceError:
                        reject_pdf_thumbnail(
                            PdfThumbnailErrorCode.CACHE_INVALID,
                            "thumbnail.cache_cleanup",
                        )
                    return ThumbnailCacheLookupStatus.CORRUPT_REMOVED, None
                return ThumbnailCacheLookupStatus.HIT, artifact
        except PdfThumbnailError:
            raise
        except WorkspaceError:
            if not os.path.lexists(path):
                return ThumbnailCacheLookupStatus.MISS, None
            raise

    def _bounded_names(self, path: Path) -> tuple[str, ...]:
        names: list[str] = []
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    if len(names) >= self._maximum_cleanup_entries:
                        reject_pdf_thumbnail(
                            PdfThumbnailErrorCode.LIMIT_EXCEEDED,
                            "thumbnail.cleanup_limit",
                        )
                    validate_internal_component(entry.name)
                    names.append(entry.name)
        except PdfThumbnailError:
            raise
        except WorkspaceError:
            raise
        except OSError:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.DESTINATION_FAILED, "thumbnail.cleanup_scan")
        names.sort(key=str.casefold)
        return tuple(names)

    def _allocate_cache_object_id(
        self, key: PdfThumbnailCacheKey | ImageTransformLookupKey
    ) -> UUID:
        used = {key.case_id, key.source_id, key.page_id}
        for _ in range(_MAXIMUM_CACHE_OBJECT_ID_ATTEMPTS):
            try:
                candidate = self._id_factory()
            except BaseException:
                reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.cache_id")
            if type(candidate) is UUID and candidate.version == 4 and candidate not in used:
                with self._stage_lock:
                    if (
                        candidate not in self._live_stage_ids
                        and candidate not in self._quarantined_stage_ids
                    ):
                        return candidate
        reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.cache_id")

    def _quarantine_stage(self, cache_object_id: UUID) -> None:
        with self._stage_lock:
            if cache_object_id in self._live_stage_ids:
                self._quarantined_stage_ids.add(cache_object_id)

    def _release_stage(self, cache_object_id: UUID) -> None:
        with self._stage_lock:
            self._live_stage_ids.discard(cache_object_id)


def _partial_thumbnail_cleanup_report(
    *,
    selected_entries: int,
    registry_retained: int,
    removed_entries: int,
    retained_entries: int,
    released_bytes: int,
) -> ThumbnailCleanupReport:
    return ThumbnailCleanupReport(
        status=ThumbnailCleanupStatus.PARTIAL,
        examined_entries=selected_entries + registry_retained,
        removed_entries=removed_entries,
        retained_entries=retained_entries,
        released_bytes=released_bytes,
        failure_code=PdfThumbnailErrorCode.DESTINATION_FAILED,
        unexpected_failure=True,
    )


def _open_thumbnail_roots(
    stack: ExitStack,
    authority: AuthenticatedCase,
) -> tuple[WindowsHandle, WindowsHandle]:
    cache_root = stack.enter_context(open_handle(authority.layout.cache))
    require_expected_handle(cache_root, authority.layout.cache, directory=True)
    verify_private_acl(cache_root)
    require_no_alternate_streams(authority.layout.cache)
    thumbnails_root = stack.enter_context(open_handle(authority.layout.thumbnails))
    require_expected_handle(thumbnails_root, authority.layout.thumbnails, directory=True)
    verify_private_acl(thumbnails_root)
    require_no_alternate_streams(authority.layout.thumbnails)
    return cache_root, thumbnails_root


def _delete_exact_regular(
    path: Path,
    *,
    expected_root: WindowsHandle,
) -> tuple[bool, int]:
    if path.parent != expected_root.info.final_path:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.cleanup_scope")
    if not os.path.lexists(path):
        return False, 0
    with open_handle(path, delete=True) as handle:
        require_expected_handle(handle, path, directory=False)
        initial = handle.info
        if initial.is_sparse or initial.link_count != 1:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.cleanup_identity")
        require_no_alternate_streams(path)
        current = handle.refresh_info()
        if (
            current.identity != initial.identity
            or current.logical_size != initial.logical_size
            or current.is_sparse
            or current.link_count != 1
        ):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.cleanup_changed")
        require_no_alternate_streams(path)
        handle.mark_for_deletion()
        return True, current.logical_size


def _delete_selected_names(
    names: list[str],
    *,
    root_path: Path,
    root_handle: WindowsHandle,
) -> ThumbnailCleanupReport:
    removed = 0
    released = 0
    for name in names:
        try:
            was_removed, byte_count = _delete_exact_regular(
                root_path / name,
                expected_root=root_handle,
            )
        except PdfThumbnailError, WorkspaceError:
            return ThumbnailCleanupReport(
                status=ThumbnailCleanupStatus.PARTIAL,
                examined_entries=len(names),
                removed_entries=removed,
                retained_entries=len(names) - removed,
                released_bytes=released,
                failure_code=PdfThumbnailErrorCode.DESTINATION_FAILED,
            )
        if was_removed:
            removed += 1
            released += byte_count
    return ThumbnailCleanupReport(
        status=ThumbnailCleanupStatus.COMPLETE,
        examined_entries=len(names),
        removed_entries=removed,
        retained_entries=0,
        released_bytes=released,
    )


def _maximum_artifact_bytes(key: PdfThumbnailCacheKey) -> int:
    maximum_pixels = key.maximum_width_pixels * key.maximum_height_pixels
    maximum_bytes = PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES + maximum_pixels * 4
    if maximum_bytes > MAX_PDF_THUMBNAIL_ARTIFACT_BYTES:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.LIMIT_EXCEEDED, "thumbnail.cache_size")
    return maximum_bytes


def _maximum_image_artifact_bytes(key: ImageTransformLookupKey) -> int:
    maximum_bytes = (
        IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES
        + key.maximum_width_pixels * key.maximum_height_pixels * 4
    )
    if maximum_bytes > MAX_IMAGE_TRANSFORM_ARTIFACT_BYTES:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.LIMIT_EXCEEDED, "thumbnail.image_cache_size")
    return maximum_bytes


def _require_image_lookup_key(value: object) -> ImageTransformLookupKey:
    if type(value) is not ImageTransformLookupKey:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "thumbnail.image_cache_key")
    return value


def _require_cache_key(value: object) -> PdfThumbnailCacheKey:
    if type(value) is not PdfThumbnailCacheKey:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "thumbnail.cache_key")
    return value


def _require_uuid4(value: object, *, context: str) -> UUID:
    if type(value) is not UUID or value.version != 4:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, context)
    return value

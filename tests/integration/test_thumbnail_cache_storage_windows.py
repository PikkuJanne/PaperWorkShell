"""Windows integration evidence for PWS-0108 source leases and thumbnail cache storage."""

from __future__ import annotations

import ctypes
import hashlib
import os
from collections.abc import Callable
from ctypes import wintypes
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from paperworkshell.app.source_ingest import SourceIngestService
from paperworkshell.domain.import_precheck import ImportLimits
from paperworkshell.domain.pdf_rendering import PdfRenderRotation
from paperworkshell.domain.pdf_thumbnails import (
    PdfThumbnailArtifactHeader,
    PdfThumbnailCacheKey,
    PdfThumbnailError,
    PdfThumbnailErrorCode,
    encode_pdf_thumbnail_artifact,
)
from paperworkshell.domain.source_ingest import (
    CompletedSourceInspection,
    PreparedSourceSummary,
    SourceEncryptionState,
    SourceImportProgress,
    SourceIngestError,
    SourceIngestErrorCode,
    SourceSignatureState,
)
from paperworkshell.storage import _windows as windows_module
from paperworkshell.storage._windows import WindowsHandle
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.source_ingest import WindowsSourceIngestStore
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.thumbnail_cache import (
    ThumbnailCacheLookupStatus,
    ThumbnailCleanupStatus,
    WindowsThumbnailCacheStore,
)
from paperworkshell.storage.workspace import CaseWorkspaceManager

pytestmark = pytest.mark.skipif(os.name != "nt", reason="PWS-0108 storage is Windows-first")

CASE_ID = UUID("10000000-0000-4000-8000-000000000208")
SOURCE_ID = UUID("20000000-0000-4000-8000-000000000208")
PAGE_ID = UUID("30000000-0000-4000-8000-000000000208")
OTHER_PAGE_ID = UUID("40000000-0000-4000-8000-000000000208")
OPERATION_ID = UUID("50000000-0000-4000-8000-000000000208")
NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)

CancellationProbe = Callable[[], bool]
DeadlineProbe = Callable[[], bool]
ProgressSink = Callable[[SourceImportProgress], None]


class _SequentialIds:
    def __init__(self, prefix: int) -> None:
        self._prefix = prefix
        self._next = 1

    def __call__(self) -> UUID:
        value = UUID(f"{self._prefix:08x}-0000-4000-8000-{self._next:012x}")
        self._next += 1
        return value


class _PdfInspector:
    def inspect(
        self,
        source: PreparedSourceSummary,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ProgressSink,
    ) -> CompletedSourceInspection:
        assert not deadline()
        assert not cancellation()
        del progress
        return CompletedSourceInspection(
            pending_id=source.pending_id,
            media_type=source.media_type,
            page_count=2,
            signature_state=SourceSignatureState.NONE_DETECTED,
            encryption_state=SourceEncryptionState.UNENCRYPTED,
        )


def _quota() -> WorkspaceQuota:
    return WorkspaceQuota(
        maximum_case_bytes=16_000_000,
        maximum_cache_bytes=4_000_000,
        maximum_temp_bytes=4_000_000,
        maximum_entries=512,
        maximum_depth=16,
    )


def _manager(tmp_path: Path) -> CaseWorkspaceManager:
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=_quota(),
        id_factory=lambda: CASE_ID,
    )
    lease = WindowsCaseLifecycleStore(manager, clock=lambda: NOW).create_case()
    lease.close()
    return manager


def _key(
    *,
    page_id: UUID = PAGE_ID,
    width: int = 8,
    source_id: UUID = SOURCE_ID,
    source_bytes: bytes = b"synthetic immutable PDF bytes",
) -> PdfThumbnailCacheKey:
    return PdfThumbnailCacheKey(
        case_id=CASE_ID,
        source_id=source_id,
        page_id=page_id,
        source_page_index=0 if page_id == PAGE_ID else 1,
        source_byte_size=len(source_bytes),
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
        maximum_width_pixels=width,
        maximum_height_pixels=4,
        additional_rotation=PdfRenderRotation.DEGREES_0,
        renderer_profile_sha256=hashlib.sha256(b"fixed renderer profile").hexdigest(),
    )


def _artifact_bytes(
    key: PdfThumbnailCacheKey,
    *,
    cache_object_id: UUID,
    operation_id: UUID = OPERATION_ID,
) -> bytes:
    width = min(2, key.maximum_width_pixels)
    height = 2
    pixels = bytes((17, 34, 51, 255)) * (width * height)
    header = PdfThumbnailArtifactHeader(
        operation_id=operation_id,
        cache_object_id=cache_object_id,
        cache_key=key,
        width_pixels=width,
        height_pixels=height,
        row_stride_bytes=width * 4,
        payload_bytes=len(pixels),
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
        intrinsic_rotation=PdfRenderRotation.DEGREES_0,
        additional_rotation=key.additional_rotation,
    )
    return encode_pdf_thumbnail_artifact(header, pixels)


def _write_handle(handle: WindowsHandle, payload: bytes) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    write_file = kernel32.WriteFile
    write_file.argtypes = (
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    )
    write_file.restype = wintypes.BOOL
    buffer = ctypes.create_string_buffer(payload)
    written = wintypes.DWORD()
    assert write_file(
        wintypes.HANDLE(handle.value),
        buffer,
        len(payload),
        ctypes.byref(written),
        None,
    )
    assert written.value == len(payload)


def _duplicate_handle(handle: WindowsHandle) -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    current_process = kernel32.GetCurrentProcess
    current_process.restype = wintypes.HANDLE
    duplicate = kernel32.DuplicateHandle
    duplicate.argtypes = (
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    )
    duplicate.restype = wintypes.BOOL
    process = current_process()
    copied = wintypes.HANDLE()
    assert duplicate(process, handle.value, process, ctypes.byref(copied), 0, False, 2)
    assert copied.value is not None
    return int(copied.value)


def _close_raw_handle(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    assert close_handle(wintypes.HANDLE(handle))


def _publish(store: WindowsThumbnailCacheStore, key: PdfThumbnailCacheKey) -> None:
    with store.create_stage(key) as stage:
        _write_handle(
            stage.write_handle,
            _artifact_bytes(key, cache_object_id=stage.cache_object_id),
        )
        stage.validate(expected_operation_id=OPERATION_ID)
        publication = stage.publish()
        assert publication.artifact.header.cache_key == key


def test_stage_publish_lookup_corruption_rebuild_and_manifest_independence(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    layout = manager.layout(CASE_ID)
    store = WindowsThumbnailCacheStore(manager, id_factory=_SequentialIds(0x70000000))
    key = _key()
    manifest_before = layout.manifest.read_bytes()

    _publish(store, key)

    final = layout.thumbnails / key.internal_name
    assert final.is_file()
    hit = store.lookup(key)
    assert hit.status is ThumbnailCacheLookupStatus.HIT
    assert hit.is_hit
    assert hit.artifact is not None
    assert hit.artifact.header.cache_key == key
    assert layout.manifest.read_bytes() == manifest_before

    final.write_bytes(b"corrupt synthetic cache entry")
    corrupt = store.lookup(key)
    assert corrupt.status is ThumbnailCacheLookupStatus.CORRUPT_REMOVED
    assert corrupt.corrupt_removed
    assert not final.exists()
    assert layout.manifest.read_bytes() == manifest_before

    _publish(store, key)
    assert store.lookup(key).is_hit
    assert layout.manifest.read_bytes() == manifest_before


def test_exact_key_and_page_invalidation_leave_unrelated_entries(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    store = WindowsThumbnailCacheStore(manager, id_factory=_SequentialIds(0x71000000))
    first = _key(width=8)
    second = _key(width=9)
    unrelated = _key(page_id=OTHER_PAGE_ID, width=8)
    for key in (first, second, unrelated):
        _publish(store, key)

    assert store.invalidate_key(first)
    assert not store.invalidate_key(first)
    assert store.lookup(second).is_hit
    assert store.lookup(unrelated).is_hit

    report = store.invalidate_page(CASE_ID, PAGE_ID)
    assert report.status is ThumbnailCleanupStatus.COMPLETE
    assert report.removed_entries == 1
    assert not store.lookup(second).is_hit
    assert store.lookup(unrelated).is_hit


def test_no_replace_publication_reuses_valid_collision_and_rebuilds_corrupt_collision(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    layout = manager.layout(CASE_ID)
    store = WindowsThumbnailCacheStore(manager, id_factory=_SequentialIds(0x71500000))
    key = _key()
    _publish(store, key)
    first = store.lookup(key)
    assert first.artifact is not None
    first_cache_object_id = first.artifact.header.cache_object_id

    with store.create_stage(key) as stage:
        _write_handle(
            stage.write_handle,
            _artifact_bytes(key, cache_object_id=stage.cache_object_id),
        )
        stage.validate(expected_operation_id=OPERATION_ID)
        collision = stage.publish()

    assert not collision.published
    assert collision.artifact.header.cache_object_id == first_cache_object_id
    assert len(list(layout.thumbnails.iterdir())) == 1

    (layout.thumbnails / key.internal_name).write_bytes(b"corrupt collision")
    with store.create_stage(key) as stage:
        _write_handle(
            stage.write_handle,
            _artifact_bytes(key, cache_object_id=stage.cache_object_id),
        )
        stage.validate(expected_operation_id=OPERATION_ID)
        rebuilt = stage.publish()

    assert rebuilt.published
    assert store.lookup(key).is_hit
    assert len(list(layout.thumbnails.iterdir())) == 1


def test_interruption_after_native_rename_resolves_as_published(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    layout = manager.layout(CASE_ID)
    store = WindowsThumbnailCacheStore(manager, id_factory=_SequentialIds(0x71600000))
    key = _key()
    real_rename = windows_module._rename_open_file
    interruption = KeyboardInterrupt()

    def rename_then_interrupt(
        source_handle: WindowsHandle,
        *,
        destination: Path,
        replace: bool,
    ) -> tuple[bool, int]:
        outcome = real_rename(source_handle, destination=destination, replace=replace)
        assert outcome[0]
        raise interruption

    monkeypatch.setattr(windows_module, "_rename_open_file", rename_then_interrupt)
    with store.create_stage(key) as stage:
        _write_handle(
            stage.write_handle,
            _artifact_bytes(key, cache_object_id=stage.cache_object_id),
        )
        stage.validate(expected_operation_id=OPERATION_ID)
        publication = stage.publish()

    assert publication.published
    assert publication.delayed_exception is interruption
    assert (layout.thumbnails / key.internal_name).is_file()
    assert store.lookup(key).is_hit


def test_interruption_before_native_rename_discards_only_unpublished_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    layout = manager.layout(CASE_ID)
    store = WindowsThumbnailCacheStore(manager, id_factory=_SequentialIds(0x71700000))
    key = _key()
    interruption = KeyboardInterrupt()

    def interrupt_before_rename(
        _source_handle: WindowsHandle,
        *,
        destination: Path,
        replace: bool,
    ) -> tuple[bool, int]:
        del destination, replace
        raise interruption

    monkeypatch.setattr(windows_module, "_rename_open_file", interrupt_before_rename)
    with pytest.raises(KeyboardInterrupt) as raised:
        with store.create_stage(key) as stage:
            _write_handle(
                stage.write_handle,
                _artifact_bytes(key, cache_object_id=stage.cache_object_id),
            )
            stage.validate(expected_operation_id=OPERATION_ID)
            stage.publish()

    assert raised.value is interruption
    assert list(layout.thumbnails.iterdir()) == []
    assert not store.lookup(key).is_hit


def test_stage_rejects_wrong_cache_object_correlation_before_publication(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    layout = manager.layout(CASE_ID)
    store = WindowsThumbnailCacheStore(manager, id_factory=_SequentialIds(0x71800000))
    key = _key()
    wrong_cache_object = UUID("71900000-0000-4000-8000-000000000001")

    with pytest.raises(PdfThumbnailError):
        with store.create_stage(key) as stage:
            stage_name = f"{stage.cache_object_id}.tmp"
            _write_handle(
                stage.write_handle,
                _artifact_bytes(key, cache_object_id=wrong_cache_object),
            )
            stage.validate(expected_operation_id=OPERATION_ID)

    assert not (layout.thumbnails / stage_name).exists()
    assert not (layout.thumbnails / key.internal_name).exists()


def test_unknown_publication_state_quarantines_instead_of_deleting_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    layout = manager.layout(CASE_ID)
    store = WindowsThumbnailCacheStore(manager, id_factory=_SequentialIds(0x71900000))
    key = _key()
    real_resolver = windows_module._resolve_cache_publish_state
    resolutions = 0

    def first_not_published_then_unknown(
        source_handle: WindowsHandle,
        *,
        initial_path: Path,
        initial_identity: windows_module.FileIdentity,
        destination: Path,
    ) -> windows_module.CachePublishState:
        nonlocal resolutions
        resolutions += 1
        if resolutions == 1:
            return real_resolver(
                source_handle,
                initial_path=initial_path,
                initial_identity=initial_identity,
                destination=destination,
            )
        return windows_module.CachePublishState.UNKNOWN

    def interrupt_rename(
        _source_handle: WindowsHandle,
        *,
        destination: Path,
        replace: bool,
    ) -> tuple[bool, int]:
        del destination, replace
        raise KeyboardInterrupt()

    monkeypatch.setattr(
        windows_module,
        "_resolve_cache_publish_state",
        first_not_published_then_unknown,
    )
    monkeypatch.setattr(windows_module, "_rename_open_file", interrupt_rename)
    with pytest.raises(PdfThumbnailError):
        with store.create_stage(key) as stage:
            stage_name = f"{stage.cache_object_id}.tmp"
            _write_handle(
                stage.write_handle,
                _artifact_bytes(key, cache_object_id=stage.cache_object_id),
            )
            stage.validate(expected_operation_id=OPERATION_ID)
            stage.publish()

    assert (layout.thumbnails / stage_name).is_file()
    retained = store.cleanup_stale_stages(CASE_ID)
    assert retained.removed_entries == 0
    assert retained.retained_entries == 1


def test_live_and_quarantined_stages_are_retained_until_worker_authority_closes(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    store = WindowsThumbnailCacheStore(manager, id_factory=_SequentialIds(0x72000000))
    stage = store.create_stage(_key())
    duplicate = _duplicate_handle(stage.write_handle)
    try:
        live = store.cleanup_stale_stages(CASE_ID)
        assert live.status is ThumbnailCleanupStatus.COMPLETE
        assert live.removed_entries == 0
        assert live.retained_entries == 1

        stage.retain_untrusted()
        stage.close()
        quarantined = store.cleanup_stale_stages(CASE_ID)
        assert quarantined.status is ThumbnailCleanupStatus.COMPLETE
        assert quarantined.removed_entries == 0
        assert quarantined.retained_entries == 1

        fresh_store = WindowsThumbnailCacheStore(manager)
        still_writable = fresh_store.cleanup_stale_stages(CASE_ID)
        assert still_writable.status is ThumbnailCleanupStatus.PARTIAL
        assert still_writable.removed_entries == 0
        assert still_writable.retained_entries == 1
    finally:
        _close_raw_handle(duplicate)

    cleaned = WindowsThumbnailCacheStore(manager).cleanup_stale_stages(CASE_ID)
    assert cleaned.status is ThumbnailCleanupStatus.COMPLETE
    assert cleaned.removed_entries == 1
    assert list(manager.layout(CASE_ID).thumbnails.iterdir()) == []


def test_context_cancellation_discards_only_the_exact_unpublished_stage(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    layout = manager.layout(CASE_ID)
    store = WindowsThumbnailCacheStore(manager, id_factory=_SequentialIds(0x72500000))
    sibling = layout.thumbnails / f"{UUID('73000000-0000-4000-8000-000000000001')}.tmp"
    sibling.write_bytes(b"unrelated stale stage")

    with store.create_stage(_key()) as stage:
        stage_name = f"{stage.cache_object_id}.tmp"
        _write_handle(stage.write_handle, b"partial worker bytes")

    assert not (layout.thumbnails / stage_name).exists()
    assert sibling.read_bytes() == b"unrelated stale stage"
    assert layout.manifest.is_file()


def test_stale_cleanup_inventory_limit_fails_before_deleting_any_stage(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    layout = manager.layout(CASE_ID)
    names = (
        f"{UUID('73100000-0000-4000-8000-000000000001')}.tmp",
        f"{UUID('73100000-0000-4000-8000-000000000002')}.tmp",
    )
    for name in names:
        (layout.thumbnails / name).write_bytes(b"stale stage")
    store = WindowsThumbnailCacheStore(manager, maximum_cleanup_entries=1)

    with pytest.raises(PdfThumbnailError) as raised:
        store.cleanup_stale_stages(CASE_ID)

    assert raised.value.code is PdfThumbnailErrorCode.LIMIT_EXCEEDED
    assert sorted(path.name for path in layout.thumbnails.iterdir()) == sorted(names)


def test_committed_pdf_page_lease_pins_current_mapping_and_verifies_source_bytes(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    limits = ImportLimits(
        maximum_source_bytes=1_000_000,
        maximum_pdf_pages=100,
        maximum_image_width=10_000,
        maximum_image_height=10_000,
        maximum_image_pixels=50_000_000,
        maximum_decompressed_bytes=100_000_000,
        decompression_warning_ratio=100,
        maximum_decompression_ratio=2_000,
        maximum_header_bytes=4_096,
        maximum_structure_records=64,
    )
    store = WindowsSourceIngestStore(
        manager,
        WindowsSourcePrechecker(limits),
        maximum_page_count=16,
        clock=lambda: NOW,
        id_factory=_SequentialIds(0x60000000),
        temp_id_factory=_SequentialIds(0x61000000),
    )
    service = SourceIngestService(store, _PdfInspector())
    source = tmp_path / "synthetic.pdf"
    source_bytes = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\nstartxref\n0\n%%EOF\n"
    source.write_bytes(source_bytes)
    result = service.import_source(CASE_ID, source, "Synthetic PDF.pdf")

    with store.borrow_committed_pdf_page(
        CASE_ID,
        result.page_ids[1],
        cancellation=lambda: False,
        deadline=lambda: False,
    ) as lease:
        assert repr(lease) == "CommittedPdfPageReadLease(<redacted>)"
        assert lease.case_id == CASE_ID
        assert lease.source_id == result.source_id
        assert lease.page_id == result.page_ids[1]
        assert lease.source_page_index == 1
        assert lease.source_page_count == 2
        assert lease.source_byte_size == len(source_bytes)
        assert lease.source_sha256 == hashlib.sha256(source_bytes).hexdigest()
        assert lease.handle.read_exact(4) == b"%PDF"

    with pytest.raises(SourceIngestError) as cancelled:
        store.borrow_committed_pdf_page(
            CASE_ID,
            result.page_ids[0],
            cancellation=lambda: True,
            deadline=lambda: False,
        )
    assert cancelled.value.code is SourceIngestErrorCode.CANCELLED

    case_copy = manager.layout(CASE_ID).originals / f"{result.source_id}.bin"
    alias = tmp_path / "case-copy-alias.bin"
    with store.borrow_committed_pdf_page(
        CASE_ID,
        result.page_ids[0],
        cancellation=lambda: False,
        deadline=lambda: False,
    ) as lease:
        os.link(case_copy, alias)
        with pytest.raises(SourceIngestError) as changed_alias:
            _ = lease.handle
        assert changed_alias.value.code is SourceIngestErrorCode.SOURCE_CHANGED
    alias.unlink()

    case_copy.write_bytes(source_bytes + b"changed")
    with pytest.raises(SourceIngestError) as raised:
        store.borrow_committed_pdf_page(
            CASE_ID,
            result.page_ids[0],
            cancellation=lambda: False,
            deadline=lambda: False,
        )

    assert raised.value.code is SourceIngestErrorCode.SOURCE_CHANGED
    assert "Synthetic PDF.pdf" not in str(raised.value)

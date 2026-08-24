"""Windows integration evidence for immutable signature-image asset storage."""

from __future__ import annotations

import hashlib
import os
import struct
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.domain.import_precheck import ImportLimits
from paperworkshell.domain.manifest_repository import (
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
)
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.recovery import ExportHistoryRecoveryResult
from paperworkshell.domain.signature_images import (
    SignatureImageCleanupStatus,
    SignatureImageError,
    SignatureImageErrorCode,
    SignatureImageProgress,
    SignatureImageProgressPhase,
)
from paperworkshell.overlays.models import (
    OverlayAssetKind,
    OverlayAssetMediaType,
    OverlayAssetRecord,
)
from paperworkshell.overlays.signature_commands import (
    CreateSignatureImageCommand,
    DeleteSignatureImageCommand,
    SignatureAssetCleanupDisposition,
    SignatureAssetDeletePolicy,
    SignatureImageManifestChange,
    create_signature_image_candidate,
    delete_signature_image_candidate,
)
from paperworkshell.storage import signature_images as storage_module
from paperworkshell.storage._windows import MAXIMUM_STREAM_CHUNK_BYTES
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.manifest_codec import render_utc_timestamp
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.recovery import WindowsStartupRecoveryStore
from paperworkshell.storage.signature_images import (
    WindowsPreparedSignatureImageLease,
    WindowsSignatureImageAssetStore,
)
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.workspace import CaseWorkspaceManager, CaseWorkspacePaths

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0305 signature-image storage targets supported Windows volumes",
)

ROOT = Path(__file__).resolve().parents[2]
SIGNATURE_FIXTURE = ROOT / "tests" / "fixtures" / "synthetic" / "v1" / "image" / "transparent.png"
CASE_ID = UUID("11000000-0000-4000-8000-000000000305")
SOURCE_ID = UUID("21000000-0000-4000-8000-000000000305")
PAGE_ID = UUID("31000000-0000-4000-8000-000000000305")
OVERLAY_ID = UUID("61000000-0000-4000-8000-000000000305")
CREATED_AT = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)
PAGE_SEEDED_AT = CREATED_AT + timedelta(minutes=1)
PLACED_AT = PAGE_SEEDED_AT + timedelta(minutes=1)
DELETED_AT = PLACED_AT + timedelta(minutes=1)
BOX = NormalizedPageBox(120_000, 220_000, 320_000, 120_000)
PAGE_SOURCE_PAYLOAD = b"synthetic-page-source-for-signature-storage"


class _SequentialUuidFactory:
    def __init__(self, prefix: int) -> None:
        self._prefix = prefix
        self._next = 1
        self._lock = Lock()

    def __call__(self) -> UUID:
        with self._lock:
            value = UUID(f"{self._prefix:08x}-0000-4000-8000-{self._next:012x}")
            self._next += 1
        return value


@dataclass(frozen=True, slots=True)
class _Stack:
    manager: CaseWorkspaceManager
    layout: CaseWorkspacePaths
    store: WindowsSignatureImageAssetStore


def _quota() -> WorkspaceQuota:
    return WorkspaceQuota(
        maximum_case_bytes=24_000_000,
        maximum_cache_bytes=2_000_000,
        maximum_temp_bytes=8_000_000,
        maximum_entries=512,
        maximum_depth=16,
    )


def _limits() -> ImportLimits:
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


def _seed_active_page(manager: CaseWorkspaceManager, layout: CaseWorkspacePaths) -> None:
    source_path = layout.originals / f"{SOURCE_ID}.bin"
    source_path.write_bytes(PAGE_SOURCE_PAYLOAD)
    repository = WindowsManifestRepository(manager)
    opened = repository.load(CASE_ID).snapshot
    candidate = opened.document
    candidate["revision"] = 1
    candidate["updated_at"] = render_utc_timestamp(PAGE_SEEDED_AT)
    candidate["sources"] = [
        {
            "source_id": str(SOURCE_ID),
            "display_name": "Synthetic page source.png",
            "storage_name": source_path.name,
            "detected_media_type": "image/png",
            "byte_size": len(PAGE_SOURCE_PAYLOAD),
            "page_count": 1,
            "sha256": hashlib.sha256(PAGE_SOURCE_PAYLOAD).hexdigest(),
            "signature_state": "not_applicable",
            "encryption_state": "not_applicable",
            "import_state": "complete",
            "imported_at": render_utc_timestamp(PAGE_SEEDED_AT),
        }
    ]
    candidate["pages"] = [
        {
            "page_id": str(PAGE_ID),
            "source_id": str(SOURCE_ID),
            "source_page_index": 0,
        }
    ]
    candidate["page_sequence_base"] = [str(PAGE_ID)]
    candidate["page_sequence"] = [str(PAGE_ID)]
    repository.save(
        CASE_ID,
        candidate,
        expected_revision=opened.revision,
        expected_sha256=opened.sha256,
        expected_cas_token=opened.cas_token,
    )


def _stack(tmp_path: Path) -> _Stack:
    profile = (tmp_path / "profile").resolve()
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=_quota(),
        id_factory=lambda: CASE_ID,
    )
    lease = WindowsCaseLifecycleStore(manager, clock=lambda: CREATED_AT).create_case()
    lease.close()
    layout = manager.layout(CASE_ID)
    _seed_active_page(manager, layout)
    store = WindowsSignatureImageAssetStore(
        manager,
        WindowsSourcePrechecker(_limits()),
        clock=lambda: PLACED_AT,
        id_factory=_SequentialUuidFactory(0x41000000),
        temp_id_factory=_SequentialUuidFactory(0x71000000),
    )
    return _Stack(manager, layout, store)


def _write_signature_source(
    tmp_path: Path,
    *,
    name: str = "synthetic-signature.png",
    suffix: bytes = b"",
) -> tuple[Path, bytes]:
    payload = SIGNATURE_FIXTURE.read_bytes() + suffix
    path = (tmp_path / name).resolve()
    path.write_bytes(payload)
    return path, payload


def _write_large_jpeg_signature_source(tmp_path: Path) -> tuple[Path, bytes]:
    components = b"\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    frame = b"\x08" + struct.pack(">HHB", 12, 24, 3) + components
    scan = b"\x03\x01\x00\x02\x00\x03\x00\x00\x3f\x00"
    payload = (
        b"\xff\xd8\xff\xc0"
        + struct.pack(">H", len(frame) + 2)
        + frame
        + b"\xff\xda"
        + struct.pack(">H", len(scan) + 2)
        + scan
        + (b"x" * (MAXIMUM_STREAM_CHUNK_BYTES + 31))
        + b"\xff\xd9"
    )
    path = (tmp_path / "large-synthetic-signature.jpg").resolve()
    path.write_bytes(payload)
    return path, payload


def _prepare(
    stack: _Stack,
    source: Path,
) -> tuple[WindowsPreparedSignatureImageLease, list[SignatureImageProgress]]:
    progress: list[SignatureImageProgress] = []
    prepared = stack.store.prepare(
        CASE_ID,
        source,
        cancellation=lambda: False,
        deadline=lambda: False,
        progress=progress.append,
    )
    return prepared, progress


def _create_change(
    prepared: WindowsPreparedSignatureImageLease,
) -> tuple[OverlayAssetRecord, SignatureImageManifestChange]:
    summary = prepared.summary
    asset = OverlayAssetRecord(
        summary.asset_id,
        OverlayAssetKind.SIGNATURE_IMAGE,
        f"{summary.asset_id}.bin",
        (
            OverlayAssetMediaType.JPEG
            if summary.media_type.value == "image/jpeg"
            else OverlayAssetMediaType.PNG
        ),
        summary.byte_size,
        summary.sha256,
    )
    change = create_signature_image_candidate(
        prepared.manifest_snapshot,
        CreateSignatureImageCommand(
            CASE_ID,
            prepared.manifest_snapshot.revision,
            asset,
            OVERLAY_ID,
            PAGE_ID,
            BOX,
        ),
    )
    return asset, change


def _commit(
    prepared: WindowsPreparedSignatureImageLease,
    change: SignatureImageManifestChange,
    progress: list[SignatureImageProgress],
) -> None:
    prepared.commit(
        change,
        cancellation=lambda: False,
        deadline=lambda: False,
        progress=progress.append,
    )


def _delete_manifest_asset(stack: _Stack, overlay_id: UUID) -> OverlayAssetRecord:
    repository = WindowsManifestRepository(stack.manager)
    snapshot = repository.load(CASE_ID).snapshot
    change = delete_signature_image_candidate(
        snapshot,
        DeleteSignatureImageCommand(
            CASE_ID,
            snapshot.revision,
            overlay_id,
            PAGE_ID,
            SignatureAssetDeletePolicy.REMOVE_IF_UNUSED,
        ),
    )
    assert change.cleanup_intent.disposition is SignatureAssetCleanupDisposition.REMOVE_UNUSED
    asset = change.cleanup_intent.asset
    assert type(asset) is OverlayAssetRecord
    candidate = change.candidate_document
    candidate["revision"] = snapshot.revision + 1
    candidate["updated_at"] = render_utc_timestamp(DELETED_AT)
    repository.save(
        CASE_ID,
        candidate,
        expected_revision=snapshot.revision,
        expected_sha256=snapshot.sha256,
        expected_cas_token=snapshot.cas_token,
    )
    return asset


def test_prepare_commit_borrow_and_explicit_unused_cleanup(tmp_path: Path) -> None:
    stack = _stack(tmp_path)
    source, payload = _write_signature_source(tmp_path)
    prepared, progress = _prepare(stack, source)
    asset, change = _create_change(prepared)
    pending_path = stack.layout.temp / f"{prepared.summary.pending_id}.tmp"
    asset_path = stack.layout.originals / asset.storage_name

    assert pending_path.is_file()
    assert pending_path.stat().st_size == len(payload)
    assert not asset_path.exists()
    result = prepared.commit(
        change,
        cancellation=lambda: False,
        deadline=lambda: False,
        progress=progress.append,
    )

    assert result.asset_id == asset.asset_id
    assert result.overlay_id == OVERLAY_ID
    assert result.manifest_revision == 2
    assert source.read_bytes() == payload
    assert asset_path.read_bytes() == payload
    assert not pending_path.exists()
    assert {item.phase for item in progress} == {
        SignatureImageProgressPhase.COPYING,
        SignatureImageProgressPhase.VERIFYING,
        SignatureImageProgressPhase.COMMITTING,
    }

    with stack.store.borrow_committed_signature_image(
        CASE_ID,
        asset.asset_id,
        cancellation=lambda: False,
        deadline=lambda: False,
    ) as borrowed:
        assert borrowed.handle_value > 0
        assert borrowed.byte_size == len(payload)
        assert borrowed.sha256 == hashlib.sha256(payload).hexdigest()
        assert asset_path.read_bytes() == payload
        with pytest.raises(OSError):
            asset_path.write_bytes(b"mutation-must-be-denied-while-borrowed")
    with pytest.raises(SignatureImageError) as closed:
        _ = borrowed.handle_value
    assert closed.value.code is SignatureImageErrorCode.ASSET_CHANGED

    retained_progress: list[SignatureImageProgress] = []
    retained = stack.store.cleanup_unused_asset(
        CASE_ID,
        asset,
        cancellation=lambda: False,
        deadline=lambda: False,
        progress=retained_progress.append,
    )
    assert retained.status is SignatureImageCleanupStatus.RETAINED
    assert asset_path.read_bytes() == payload
    assert [item.completed_units for item in retained_progress] == [0, len(payload) + 1]
    assert all(item.phase is SignatureImageProgressPhase.CLEANING_UP for item in retained_progress)
    assert retained_progress[0].operation_id not in {CASE_ID, asset.asset_id}

    cleanup_asset = _delete_manifest_asset(stack, OVERLAY_ID)
    removed_progress: list[SignatureImageProgress] = []

    def fail_after_removed(item: SignatureImageProgress) -> None:
        removed_progress.append(item)
        if item.completed_units == item.total_units:
            raise RuntimeError("terminal cleanup progress failure")

    removed = stack.store.cleanup_unused_asset(
        CASE_ID,
        cleanup_asset,
        cancellation=lambda: False,
        deadline=lambda: False,
        progress=fail_after_removed,
    )
    absent_progress: list[SignatureImageProgress] = []
    absent = stack.store.cleanup_unused_asset(
        CASE_ID,
        cleanup_asset,
        cancellation=lambda: False,
        deadline=lambda: False,
        progress=absent_progress.append,
    )
    assert removed.status is SignatureImageCleanupStatus.REMOVED
    assert absent.status is SignatureImageCleanupStatus.ALREADY_ABSENT
    assert not asset_path.exists()
    assert [item.completed_units for item in removed_progress] == [
        0,
        len(payload),
        len(payload) + 1,
    ]
    assert [item.completed_units for item in absent_progress] == [0, len(payload) + 1]


def test_committed_result_survives_terminal_progress_failure(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    stack = _stack(tmp_path)
    source, payload = _write_signature_source(tmp_path)
    prepared, progress = _prepare(stack, source)
    asset, change = _create_change(prepared)
    privacy_canary = "PRIVATE-TERMINAL-PROGRESS-PWS0305-CANARY"

    def fail_at_terminal_commit(item: SignatureImageProgress) -> None:
        progress.append(item)
        if (
            item.phase is SignatureImageProgressPhase.COMMITTING
            and item.completed_units == item.total_units == 1
        ):
            raise RuntimeError(privacy_canary)

    result = prepared.commit(
        change,
        cancellation=lambda: False,
        deadline=lambda: False,
        progress=fail_at_terminal_commit,
    )

    current = WindowsManifestRepository(stack.manager).load(CASE_ID).snapshot
    manifest_assets = cast(list[dict[str, object]], current.document["assets"])
    assert result.manifest_revision == 2
    assert result.asset_id == asset.asset_id
    assert current.revision == 2
    assert [item["asset_id"] for item in manifest_assets] == [str(asset.asset_id)]
    assert (stack.layout.originals / asset.storage_name).read_bytes() == payload
    assert privacy_canary not in caplog.text


@pytest.mark.parametrize(
    ("control", "expected_code"),
    (
        ("cancellation", SignatureImageErrorCode.CANCELLED),
        ("deadline", SignatureImageErrorCode.TIMED_OUT),
    ),
)
def test_cleanup_control_interrupts_bounded_hash_before_deletion(
    tmp_path: Path,
    control: str,
    expected_code: SignatureImageErrorCode,
) -> None:
    stack = _stack(tmp_path)
    source, payload = _write_large_jpeg_signature_source(tmp_path)
    prepared, progress = _prepare(stack, source)
    asset, change = _create_change(prepared)
    _commit(prepared, change, progress)
    cleanup_asset = _delete_manifest_asset(stack, OVERLAY_ID)
    asset_path = stack.layout.originals / asset.storage_name
    stop_requested = False
    cleanup_progress: list[SignatureImageProgress] = []

    def record_progress(item: SignatureImageProgress) -> None:
        nonlocal stop_requested
        cleanup_progress.append(item)
        if item.completed_units >= MAXIMUM_STREAM_CHUNK_BYTES:
            stop_requested = True

    with pytest.raises(SignatureImageError) as raised:
        stack.store.cleanup_unused_asset(
            CASE_ID,
            cleanup_asset,
            cancellation=lambda: control == "cancellation" and stop_requested,
            deadline=lambda: control == "deadline" and stop_requested,
            progress=record_progress,
        )

    assert raised.value.code is expected_code
    assert asset_path.read_bytes() == payload
    assert [item.completed_units for item in cleanup_progress] == [
        0,
        MAXIMUM_STREAM_CHUNK_BYTES,
    ]
    assert all(item.phase is SignatureImageProgressPhase.CLEANING_UP for item in cleanup_progress)
    assert cleanup_progress[0].operation_id not in {CASE_ID, asset.asset_id}


def test_stale_prepared_commit_fails_before_asset_publication(tmp_path: Path) -> None:
    stack = _stack(tmp_path)
    source, payload = _write_signature_source(tmp_path)
    prepared, progress = _prepare(stack, source)
    asset, change = _create_change(prepared)
    repository = WindowsManifestRepository(stack.manager)
    current = repository.load(CASE_ID).snapshot
    advanced = current.document
    advanced["revision"] = current.revision + 1
    advanced["updated_at"] = render_utc_timestamp(PLACED_AT)
    repository.save(
        CASE_ID,
        advanced,
        expected_revision=current.revision,
        expected_sha256=current.sha256,
        expected_cas_token=current.cas_token,
    )

    with pytest.raises(SignatureImageError) as raised:
        _commit(prepared, change, progress)

    assert raised.value.code is SignatureImageErrorCode.CASE_CHANGED
    assert source.read_bytes() == payload
    assert not (stack.layout.originals / asset.storage_name).exists()
    assert list(stack.layout.temp.iterdir()) == []


def test_uncertain_manifest_commit_preserves_published_asset_for_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = _stack(tmp_path)
    source, payload = _write_signature_source(tmp_path)
    prepared, progress = _prepare(stack, source)
    asset, change = _create_change(prepared)

    def uncertain_commit(**kwargs: object) -> object:
        before_checkpoint = cast(Callable[[], None], kwargs["before_checkpoint"])
        before_checkpoint()
        raise ManifestRepositoryError(
            ManifestRepositoryErrorCode.COMMIT_UNCERTAIN,
            "manifest.commit_uncertain",
        )

    monkeypatch.setattr(storage_module, "commit_manifest_transaction", uncertain_commit)

    with pytest.raises(SignatureImageError) as raised:
        _commit(prepared, change, progress)

    asset_path = stack.layout.originals / asset.storage_name
    assert raised.value.code is SignatureImageErrorCode.COMMIT_FAILED
    assert asset_path.read_bytes() == payload
    current = WindowsManifestRepository(stack.manager).load(CASE_ID).snapshot
    assert current.revision == 1
    assert cast(list[object], current.document["assets"]) == []

    recovered = WindowsStartupRecoveryStore(
        stack.manager,
        settle_exports=lambda _case_id: ExportHistoryRecoveryResult(),
    ).scan_and_recover()

    assert recovered.cases[0].removed_source_orphans == 1
    assert not asset_path.exists()

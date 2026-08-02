"""Windows end-to-end acceptance evidence for PWS-0104 immutable ingest."""

from __future__ import annotations

import hashlib
import json
import os
import struct
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.app.case_lifecycle import CaseLifecycleService
from paperworkshell.app.source_ingest import SourceIngestService
from paperworkshell.domain.import_precheck import DetectedMediaType, ImportLimits
from paperworkshell.domain.source_ingest import (
    CompletedSourceInspection,
    PreparedSourceSummary,
    SourceEncryptionState,
    SourceImportProgress,
    SourceIngestError,
    SourceIngestErrorCode,
    SourceIntegrityStatus,
    SourceProgressPhase,
    SourceSignatureState,
    reject_source_ingest,
)
from paperworkshell.storage._windows import MAXIMUM_STREAM_CHUNK_BYTES
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.source_ingest import WindowsSourceIngestStore
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.workspace import (
    CaseWorkspaceManager,
    CaseWorkspacePaths,
)
from tools.validate_manifests import load_manifest_schema, validate_manifest_data

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0104 targets the supported Windows storage boundary"
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CASE_ID = UUID("10000000-0000-4000-8000-000000000104")
CREATED_AT = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
IMPORTED_AT = datetime(2026, 8, 2, 12, 1, tzinfo=UTC)

CancellationProbe = Callable[[], bool]
ProgressSink = Callable[[SourceImportProgress], None]


class _SequentialUuidFactory:
    def __init__(self, prefix: int) -> None:
        self._prefix = prefix
        self._next = 1

    def __call__(self) -> UUID:
        value = UUID(f"{self._prefix:08x}-0000-4000-8000-{self._next:012x}")
        self._next += 1
        return value


class _SyntheticImageInspector:
    """Stand in for the later restricted image worker using typed output only."""

    def inspect(
        self,
        source: PreparedSourceSummary,
        *,
        cancellation: CancellationProbe,
        progress: ProgressSink,
    ) -> CompletedSourceInspection:
        if cancellation():
            reject_source_ingest(SourceIngestErrorCode.CANCELLED, "test.inspection")
        progress(
            SourceImportProgress(
                source.pending_id,
                SourceProgressPhase.INSPECTING,
                1,
                1,
            )
        )
        return CompletedSourceInspection(
            pending_id=source.pending_id,
            media_type=source.media_type,
            page_count=1,
            signature_state=SourceSignatureState.NOT_APPLICABLE,
            encryption_state=SourceEncryptionState.NOT_APPLICABLE,
        )


@dataclass(frozen=True)
class _Stack:
    profile: Path
    manager: CaseWorkspaceManager
    layout: CaseWorkspacePaths
    store: WindowsSourceIngestStore
    service: SourceIngestService


def _quota(*, maximum_case_bytes: int = 24_000_000) -> WorkspaceQuota:
    return WorkspaceQuota(
        maximum_case_bytes=maximum_case_bytes,
        maximum_cache_bytes=min(1_000_000, maximum_case_bytes),
        maximum_temp_bytes=min(8_000_000, maximum_case_bytes),
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


def _stack(tmp_path: Path, *, maximum_case_bytes: int = 24_000_000) -> _Stack:
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=_quota(maximum_case_bytes=maximum_case_bytes),
        id_factory=lambda: CASE_ID,
    )
    lifecycle = WindowsCaseLifecycleStore(manager, clock=lambda: CREATED_AT)
    case_lease = lifecycle.create_case()
    case_id = case_lease.summary.case_id
    case_lease.close()
    layout = manager.layout(case_id)
    store = WindowsSourceIngestStore(
        manager,
        WindowsSourcePrechecker(_limits()),
        maximum_page_count=16,
        clock=lambda: IMPORTED_AT,
        id_factory=_SequentialUuidFactory(0x40000000),
        temp_id_factory=_SequentialUuidFactory(0x50000000),
    )
    return _Stack(
        profile=profile,
        manager=manager,
        layout=layout,
        store=store,
        service=SourceIngestService(store, _SyntheticImageInspector()),
    )


def _jpeg() -> bytes:
    components = b"\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    frame = b"\x08" + struct.pack(">HHB", 12, 24, 3) + components
    scan = b"\x03\x01\x00\x02\x00\x03\x00\x00\x3f\x00"
    return (
        b"\xff\xd8\xff\xc0"
        + struct.pack(">H", len(frame) + 2)
        + frame
        + b"\xff\xda"
        + struct.pack(">H", len(scan) + 2)
        + scan
        + b"\xff\xd9"
    )


def _jpeg_of_size(byte_size: int, *, fill: bytes = b"U") -> bytes:
    base = _jpeg()
    if byte_size < len(base) or not fill:
        raise ValueError("synthetic JPEG size and fill must be usable")
    remaining = byte_size - len(base)
    repeated = (fill * ((remaining + len(fill) - 1) // len(fill)))[:remaining]
    return base[:-2] + repeated + base[-2:]


def _manifest(layout: CaseWorkspacePaths) -> dict[str, object]:
    loaded = json.loads(layout.manifest.read_bytes())
    assert type(loaded) is dict
    return loaded


def _external_snapshot(path: Path) -> tuple[str, int, int, int]:
    stat = path.stat(follow_symlinks=False)
    return (
        hashlib.sha256(path.read_bytes()).hexdigest(),
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ino,
    )


def _assert_no_staged_files(stack: _Stack) -> None:
    assert list(stack.layout.temp.iterdir()) == []


@pytest.mark.parametrize(
    "byte_size",
    [
        MAXIMUM_STREAM_CHUNK_BYTES - 1,
        MAXIMUM_STREAM_CHUNK_BYTES,
        MAXIMUM_STREAM_CHUNK_BYTES + 17,
    ],
)
def test_import_varied_copy_boundaries_commits_exact_manifest_and_fresh_reopen(
    tmp_path: Path,
    byte_size: int,
) -> None:
    stack = _stack(tmp_path)
    source = (tmp_path / f"boundary-{byte_size}.jpg").resolve()
    payload = _jpeg_of_size(byte_size)
    source.write_bytes(payload)
    before = _external_snapshot(source)

    result = stack.service.import_source(CASE_ID, source, "Synthetic boundary image.jpg")

    assert result.case_id == CASE_ID
    assert result.media_type is DetectedMediaType.JPEG
    assert result.byte_size == byte_size
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert result.manifest_revision == 1
    assert not result.duplicate.detected
    assert _external_snapshot(source) == before
    assert source.read_bytes() == payload

    stored = stack.layout.originals / f"{result.source_id}.bin"
    assert stored.read_bytes() == payload
    assert hashlib.sha256(stored.read_bytes()).hexdigest() == result.sha256
    document = _manifest(stack.layout)
    sources = document["sources"]
    pages = document["pages"]
    assert type(sources) is list and len(sources) == 1
    assert type(pages) is list and len(pages) == 1
    assert sources[0] == {
        "source_id": str(result.source_id),
        "display_name": "Synthetic boundary image.jpg",
        "storage_name": f"{result.source_id}.bin",
        "detected_media_type": DetectedMediaType.JPEG.value,
        "byte_size": byte_size,
        "page_count": 1,
        "sha256": result.sha256,
        "signature_state": SourceSignatureState.NOT_APPLICABLE.value,
        "encryption_state": SourceEncryptionState.NOT_APPLICABLE.value,
        "import_state": "complete",
        "imported_at": "2026-08-02T12:01:00Z",
    }
    assert pages[0] == {
        "page_id": str(result.page_ids[0]),
        "source_id": str(result.source_id),
        "source_page_index": 0,
    }
    rendered_pages = [str(page_id) for page_id in result.page_ids]
    assert document["revision"] == 1
    assert document["page_sequence_base"] == rendered_pages
    assert document["page_sequence"] == rendered_pages
    assert document["page_operations"] == []
    _assert_no_staged_files(stack)

    schema, schema_issues = load_manifest_schema(PROJECT_ROOT)
    assert schema is not None
    assert schema_issues == []
    assert validate_manifest_data(document, schema) == []

    committed_manifest = stack.layout.manifest.read_bytes()
    fresh_manager = CaseWorkspaceManager(
        profile_root=stack.profile,
        quota=_quota(),
    )
    fresh_service = CaseLifecycleService(
        WindowsCaseLifecycleStore(fresh_manager, clock=lambda: CREATED_AT)
    )
    index = fresh_service.list_cases()
    reopened = fresh_service.open_case(CASE_ID)
    assert index.unavailable == ()
    assert len(index.cases) == 1
    assert index.cases[0].revision == 1
    assert reopened.revision == 1
    fresh_service.close_case(CASE_ID)
    assert stack.layout.manifest.read_bytes() == committed_manifest


def test_duplicate_content_keeps_distinct_copies_and_returns_advisory(
    tmp_path: Path,
) -> None:
    stack = _stack(tmp_path)
    payload = _jpeg_of_size(8_192, fill=b"duplicate-synthetic-")
    first_path = (tmp_path / "first.jpg").resolve()
    second_path = (tmp_path / "second.jpg").resolve()
    first_path.write_bytes(payload)
    second_path.write_bytes(payload)
    first_before = _external_snapshot(first_path)
    second_before = _external_snapshot(second_path)

    first = stack.service.import_source(CASE_ID, first_path, "First synthetic.jpg")
    second = stack.service.import_source(CASE_ID, second_path, "Second synthetic.jpg")

    assert first.source_id != second.source_id
    assert first.page_ids != second.page_ids
    assert first.manifest_revision == 1
    assert not first.duplicate.detected
    assert second.manifest_revision == 2
    assert second.duplicate.detected
    assert second.duplicate.matching_source_ids == (first.source_id,)
    assert _external_snapshot(first_path) == first_before
    assert _external_snapshot(second_path) == second_before
    first_copy = stack.layout.originals / f"{first.source_id}.bin"
    second_copy = stack.layout.originals / f"{second.source_id}.bin"
    assert first_copy != second_copy
    assert first_copy.read_bytes() == second_copy.read_bytes() == payload
    assert sorted(path.name for path in stack.layout.originals.iterdir()) == sorted(
        [f"{first.source_id}.bin", f"{second.source_id}.bin"]
    )
    document = _manifest(stack.layout)
    assert document["revision"] == 2
    sources = document["sources"]
    pages = document["pages"]
    assert isinstance(sources, list)
    assert isinstance(pages, list)
    assert len(sources) == 2
    assert len(pages) == 2
    assert document["page_sequence_base"] == [
        str(first.page_ids[0]),
        str(second.page_ids[0]),
    ]
    assert document["page_sequence"] == document["page_sequence_base"]
    _assert_no_staged_files(stack)


def test_cancellation_after_first_copy_chunk_leaves_no_record_or_case_copy(
    tmp_path: Path,
) -> None:
    stack = _stack(tmp_path)
    source = (tmp_path / "cancelled.jpg").resolve()
    payload = _jpeg_of_size(MAXIMUM_STREAM_CHUNK_BYTES + 257)
    source.write_bytes(payload)
    external_before = _external_snapshot(source)
    manifest_before = stack.layout.manifest.read_bytes()
    cancelled = False

    def progress(value: SourceImportProgress) -> None:
        nonlocal cancelled
        if value.phase is SourceProgressPhase.COPYING and value.completed_units > 0:
            cancelled = True

    with pytest.raises(SourceIngestError) as raised:
        stack.service.import_source(
            CASE_ID,
            source,
            "Cancelled synthetic.jpg",
            cancellation=lambda: cancelled,
            progress=progress,
        )

    assert raised.value.code is SourceIngestErrorCode.CANCELLED
    assert stack.layout.manifest.read_bytes() == manifest_before
    assert list(stack.layout.originals.iterdir()) == []
    _assert_no_staged_files(stack)
    assert _external_snapshot(source) == external_before


def test_capacity_exhaustion_leaves_no_record_or_partial_copy(tmp_path: Path) -> None:
    stack = _stack(tmp_path, maximum_case_bytes=4_096)
    source = (tmp_path / "capacity.jpg").resolve()
    payload = _jpeg_of_size(8_192)
    source.write_bytes(payload)
    external_before = _external_snapshot(source)
    manifest_before = stack.layout.manifest.read_bytes()

    with pytest.raises(SourceIngestError) as raised:
        stack.service.import_source(CASE_ID, source, "Capacity synthetic.jpg")

    assert raised.value.code is SourceIngestErrorCode.CAPACITY_EXCEEDED
    assert stack.layout.manifest.read_bytes() == manifest_before
    assert list(stack.layout.originals.iterdir()) == []
    _assert_no_staged_files(stack)
    assert _external_snapshot(source) == external_before


def test_integrity_verification_detects_same_size_append_truncate_and_missing(
    tmp_path: Path,
) -> None:
    stack = _stack(tmp_path)
    source = (tmp_path / "integrity.jpg").resolve()
    payload = _jpeg_of_size(8_192, fill=b"integrity-synthetic-")
    source.write_bytes(payload)
    result = stack.service.import_source(CASE_ID, source, "Integrity synthetic.jpg")
    case_copy = stack.layout.originals / f"{result.source_id}.bin"
    manifest_before = stack.layout.manifest.read_bytes()

    assert (
        stack.service.verify_integrity(CASE_ID, result.source_id).status
        is SourceIntegrityStatus.VERIFIED
    )

    case_copy.write_bytes(bytes([payload[0] ^ 1]) + payload[1:])
    assert (
        stack.service.verify_integrity(CASE_ID, result.source_id).status
        is SourceIntegrityStatus.MISMATCH
    )

    case_copy.write_bytes(payload)
    assert (
        stack.service.verify_integrity(CASE_ID, result.source_id).status
        is SourceIntegrityStatus.VERIFIED
    )
    case_copy.write_bytes(payload + b"appended")
    assert (
        stack.service.verify_integrity(CASE_ID, result.source_id).status
        is SourceIntegrityStatus.MISMATCH
    )

    case_copy.write_bytes(payload[:-1])
    assert (
        stack.service.verify_integrity(CASE_ID, result.source_id).status
        is SourceIntegrityStatus.MISMATCH
    )

    case_copy.write_bytes(payload)
    case_copy.unlink()
    assert (
        stack.service.verify_integrity(CASE_ID, result.source_id).status
        is SourceIntegrityStatus.MISSING
    )
    assert stack.layout.manifest.read_bytes() == manifest_before


def test_manifest_conflict_aborts_stale_pending_copy_without_record(
    tmp_path: Path,
) -> None:
    stack = _stack(tmp_path)
    stale_source = (tmp_path / "stale.jpg").resolve()
    winning_source = (tmp_path / "winning.jpg").resolve()
    stale_source.write_bytes(_jpeg_of_size(4_096, fill=b"stale-"))
    winning_source.write_bytes(_jpeg_of_size(4_097, fill=b"winner-"))

    with stack.store.prepare(
        CASE_ID,
        stale_source,
        "Stale synthetic.jpg",
        cancellation=lambda: False,
        progress=lambda _value: None,
    ) as pending:
        stale_id = pending.summary.source_id
        winner = stack.service.import_source(
            CASE_ID,
            winning_source,
            "Winning synthetic.jpg",
        )
        inspection = _SyntheticImageInspector().inspect(
            pending.summary,
            cancellation=lambda: False,
            progress=lambda _value: None,
        )
        with pytest.raises(SourceIngestError) as raised:
            pending.commit(
                inspection,
                cancellation=lambda: False,
                progress=lambda _value: None,
            )

    assert raised.value.code is SourceIngestErrorCode.MANIFEST_CONFLICT
    document = _manifest(stack.layout)
    assert document["revision"] == 1
    sources = document["sources"]
    assert isinstance(sources, list)
    assert all(type(source) is dict for source in sources)
    typed_sources = cast(list[dict[str, object]], sources)
    assert [source["source_id"] for source in typed_sources] == [str(winner.source_id)]
    assert not (stack.layout.originals / f"{stale_id}.bin").exists()
    assert (stack.layout.originals / f"{winner.source_id}.bin").is_file()
    _assert_no_staged_files(stack)

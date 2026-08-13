"""Windows end-to-end acceptance evidence for PWS-0104 immutable ingest."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import struct
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier, Event, Lock
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.app.case_lifecycle import CaseLifecycleService
from paperworkshell.app.source_ingest import (
    CommittedPdfSourceBatchLease,
    CommittedPdfSourceBatchStore,
    CommittedPdfSourceLease,
    SourceIngestService,
)
from paperworkshell.domain.import_precheck import DetectedMediaType, ImportLimits
from paperworkshell.domain.pdf_export import (
    PdfExportPage,
    PdfExportPlan,
    PdfExportRotation,
    PdfExportSource,
    PdfExportSourceSignatureState,
    build_pdf_export_plan,
)
from paperworkshell.domain.source_ingest import (
    CompletedSourceInspection,
    PreparedSourceSummary,
    SourceEncryptionState,
    SourceImportProgress,
    SourceImportResult,
    SourceIngestError,
    SourceIngestErrorCode,
    SourceIntegrityStatus,
    SourceProgressPhase,
    SourceSignatureState,
    reject_source_ingest,
)
from paperworkshell.storage import manifest_repository as repository_storage
from paperworkshell.storage import source_ingest as source_ingest_storage
from paperworkshell.storage._windows import (
    MAXIMUM_STREAM_CHUNK_BYTES,
    ManifestReplaceResult,
    ManifestReplaceState,
    WindowsHandle,
    WindowsPendingSourceFile,
    replace_manifest_atomically,
)
from paperworkshell.storage._windows import (
    open_handle as windows_open_handle,
)
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.errors import WorkspaceError, WorkspaceErrorCode
from paperworkshell.storage.manifest_repository import (
    WindowsManifestRepository,
    canonical_manifest_bytes,
)
from paperworkshell.storage.quotas import QuotaArea, WorkspaceQuota
from paperworkshell.storage.source_ingest import (
    WindowsPreparedSourceLease,
    WindowsSourceIngestStore,
)
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.workspace import (
    CaseWorkspaceManager,
    CaseWorkspacePaths,
)
from tools.validate_manifests import load_current_manifest_schema, validate_manifest_data

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0104 targets the supported Windows storage boundary"
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CASE_ID = UUID("10000000-0000-4000-8000-000000000104")
CREATED_AT = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
IMPORTED_AT = datetime(2026, 8, 2, 12, 1, tzinfo=UTC)

CancellationProbe = Callable[[], bool]
DeadlineProbe = Callable[[], bool]
ProgressSink = Callable[[SourceImportProgress], None]


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


class _SyntheticImageInspector:
    """Stand in for the later restricted image worker using typed output only."""

    def inspect(
        self,
        source: PreparedSourceSummary,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ProgressSink,
    ) -> CompletedSourceInspection:
        if deadline():
            reject_source_ingest(SourceIngestErrorCode.TIMED_OUT, "test.inspection")
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


class _SyntheticPdfInspector:
    """Stand in for the restricted PDF worker for committed-source lease tests."""

    def inspect(
        self,
        source: PreparedSourceSummary,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ProgressSink,
    ) -> CompletedSourceInspection:
        if deadline():
            reject_source_ingest(SourceIngestErrorCode.TIMED_OUT, "test.inspection")
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
            media_type=DetectedMediaType.PDF,
            page_count=2,
            signature_state=SourceSignatureState.NONE_DETECTED,
            encryption_state=SourceEncryptionState.UNENCRYPTED,
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


def _stack(
    tmp_path: Path,
    *,
    maximum_case_bytes: int = 24_000_000,
    available_space_bytes: int | None = None,
    available_space_probe: Callable[[Path], int] | None = None,
    free_space_reserve_bytes: int = 0,
) -> _Stack:
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=_quota(maximum_case_bytes=maximum_case_bytes),
        id_factory=lambda: CASE_ID,
        minimum_free_space_reserve_bytes=free_space_reserve_bytes,
        available_space_probe=(
            available_space_probe
            if available_space_probe is not None
            else None
            if available_space_bytes is None
            else lambda _path: available_space_bytes
        ),
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


def _pdf(marker: bytes) -> bytes:
    return b"%PDF-1.7\n% synthetic-" + marker + b"\n1 0 obj\n<<>>\nendobj\nstartxref\n0\n%%EOF\n"


def _advance_duplicated_handle(source_handle: int) -> None:
    """Model one worker read through a duplicated broker handle, then terminate it."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    current_process = kernel32.GetCurrentProcess
    current_process.argtypes = ()
    current_process.restype = wintypes.HANDLE
    duplicate_handle = kernel32.DuplicateHandle
    duplicate_handle.argtypes = (
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    )
    duplicate_handle.restype = wintypes.BOOL
    process = current_process()
    duplicated = wintypes.HANDLE()
    assert duplicate_handle(
        process,
        wintypes.HANDLE(source_handle),
        process,
        ctypes.byref(duplicated),
        0,
        False,
        0x00000002,
    )
    read_file = kernel32.ReadFile
    read_file.argtypes = (
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    )
    read_file.restype = wintypes.BOOL
    buffer = ctypes.create_string_buffer(8)
    received = wintypes.DWORD()
    try:
        assert read_file(
            duplicated,
            buffer,
            len(buffer),
            ctypes.byref(received),
            None,
        )
        assert received.value == len(buffer)
    finally:
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        assert close_handle(duplicated)


def _import_pdf(
    stack: _Stack,
    tmp_path: Path,
    *,
    name: str,
    marker: bytes,
) -> SourceImportResult:
    source = (tmp_path / name).resolve()
    source.write_bytes(_pdf(marker))
    return SourceIngestService(stack.store, _SyntheticPdfInspector()).import_source(
        CASE_ID,
        source,
        name,
    )


def _reverse_source_order(plan: PdfExportPlan) -> PdfExportPlan:
    source_order = tuple(reversed(plan.sources))
    source_positions = {source.source_id: index for index, source in enumerate(source_order)}
    ordered_pages = sorted(
        plan.pages,
        key=lambda page: (source_positions[page.source_id], page.source_page_index),
    )
    pages = tuple(
        PdfExportPage(
            output_page_index=index,
            page_id=page.page_id,
            source_id=page.source_id,
            source_page_index=page.source_page_index,
            additional_rotation=page.additional_rotation,
        )
        for index, page in enumerate(ordered_pages)
    )
    return PdfExportPlan(
        case_id=plan.case_id,
        manifest_revision=plan.manifest_revision,
        manifest_sha256=plan.manifest_sha256,
        manifest_cas_token=plan.manifest_cas_token,
        sources=source_order,
        pages=pages,
        profile=plan.profile,
    )


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

    schema, schema_issues = load_current_manifest_schema(PROJECT_ROOT)
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


def test_volume_preflight_rejects_import_before_copy_and_preserves_authority(
    tmp_path: Path,
) -> None:
    available = [24_000_000]
    stack = _stack(
        tmp_path,
        available_space_probe=lambda _path: available[0],
        free_space_reserve_bytes=900,
    )
    available[0] = 1_000
    source = (tmp_path / "volume-capacity.jpg").resolve()
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


def test_disk_full_during_import_copy_removes_partial_stage_and_preserves_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = _stack(tmp_path)
    source = (tmp_path / "copy-disk-full.jpg").resolve()
    payload = _jpeg_of_size(MAXIMUM_STREAM_CHUNK_BYTES + 4_096)
    source.write_bytes(payload)
    external_before = _external_snapshot(source)
    manifest_before = stack.layout.manifest.read_bytes()
    write_chunk = WindowsPendingSourceFile.write_chunk
    calls = 0

    def fail_after_first_chunk(stage: WindowsPendingSourceFile, chunk: bytes) -> None:
        nonlocal calls
        calls += 1
        write_chunk(stage, chunk)
        if calls == 1:
            raise WorkspaceError(
                WorkspaceErrorCode.QUOTA_EXCEEDED,
                "source_stage.synthetic_disk_full",
                "The synthetic storage budget was exhausted.",
            )

    monkeypatch.setattr(WindowsPendingSourceFile, "write_chunk", fail_after_first_chunk)

    with pytest.raises(SourceIngestError) as raised:
        stack.service.import_source(CASE_ID, source, "Disk full synthetic.jpg")

    assert raised.value.code is SourceIngestErrorCode.CAPACITY_EXCEEDED
    assert calls == 1
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
        deadline=lambda: False,
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
            deadline=lambda: False,
            cancellation=lambda: False,
            progress=lambda _value: None,
        )
        with pytest.raises(SourceIngestError) as raised:
            pending.commit(
                inspection,
                deadline=lambda: False,
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


def test_prepared_source_registry_lends_only_independent_read_authority(
    tmp_path: Path,
) -> None:
    stack = _stack(tmp_path)
    source = (PROJECT_ROOT / "tests/fixtures/synthetic/v1/pdf/born-digital.pdf").resolve()
    expected = source.read_bytes()
    pending = stack.store.prepare(
        CASE_ID,
        source,
        "Public synthetic.pdf",
        deadline=lambda: False,
        cancellation=lambda: False,
        progress=lambda _value: None,
    )
    summary = pending.summary

    with stack.store.borrow_prepared_source(summary) as borrowed:
        assert repr(borrowed) == "PreparedSourceReadLease(<redacted>)"
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        read_file = kernel32.ReadFile
        read_file.argtypes = (
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        )
        read_file.restype = wintypes.BOOL
        prefix = ctypes.create_string_buffer(16)
        received = wintypes.DWORD()
        assert read_file(
            wintypes.HANDLE(borrowed.handle_value),
            prefix,
            len(prefix),
            ctypes.byref(received),
            None,
        )
        assert received.value == len(prefix)
        assert prefix.raw == expected[: len(prefix)]

        write_file = kernel32.WriteFile
        write_file.argtypes = (
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        )
        write_file.restype = wintypes.BOOL
        marker = ctypes.create_string_buffer(b"x")
        written = wintypes.DWORD()
        ctypes.set_last_error(0)
        assert not write_file(
            wintypes.HANDLE(borrowed.handle_value),
            marker,
            1,
            ctypes.byref(written),
            None,
        )
        assert ctypes.get_last_error() == 5

    mismatched = PreparedSourceSummary(
        pending_id=summary.pending_id,
        case_id=summary.case_id,
        source_id=summary.source_id,
        media_type=summary.media_type,
        byte_size=summary.byte_size,
        sha256="0" * 64,
    )
    with pytest.raises(SourceIngestError) as mismatch:
        stack.store.borrow_prepared_source(mismatched)
    assert mismatch.value.code is SourceIngestErrorCode.INSPECT_FAILED

    pending.abort()
    with pytest.raises(SourceIngestError) as stale:
        stack.store.borrow_prepared_source(summary)
    assert stale.value.code is SourceIngestErrorCode.INSPECT_FAILED
    _assert_no_staged_files(stack)


def test_committed_image_page_lease_authenticates_media_mapping_and_source_bytes(
    tmp_path: Path,
) -> None:
    stack = _stack(tmp_path)
    source = (tmp_path / "synthetic-image.jpg").resolve()
    payload = _jpeg_of_size(8_192, fill=b"image-page-lease-")
    source.write_bytes(payload)
    result = stack.service.import_source(CASE_ID, source, "Synthetic image.jpg")

    with stack.store.borrow_committed_image_page(
        CASE_ID,
        result.page_ids[0],
        deadline=lambda: False,
        cancellation=lambda: False,
    ) as lease:
        assert repr(lease) == "CommittedImagePageReadLease(<redacted>)"
        assert lease.case_id == CASE_ID
        assert lease.source_id == result.source_id
        assert lease.source_media_type is DetectedMediaType.JPEG
        assert lease.page_id == result.page_ids[0]
        assert lease.source_page_index == 0
        assert lease.source_page_count == 1
        assert lease.source_byte_size == len(payload)
        assert lease.source_sha256 == hashlib.sha256(payload).hexdigest()
        assert lease.handle.read_exact(2) == b"\xff\xd8"

    with pytest.raises(SourceIngestError) as wrong_decoder:
        stack.store.borrow_committed_pdf_page(
            CASE_ID,
            result.page_ids[0],
            deadline=lambda: False,
            cancellation=lambda: False,
        )
    assert wrong_decoder.value.code is SourceIngestErrorCode.SOURCE_UNAVAILABLE


@pytest.mark.parametrize("interrupt_after_replace", [False, True])
def test_confirmed_manifest_replace_wins_over_late_cancellation_or_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    interrupt_after_replace: bool,
) -> None:
    stack = _stack(tmp_path)
    source = (tmp_path / "late-signal.jpg").resolve()
    payload = _jpeg_of_size(8_193, fill=b"late-signal-")
    source.write_bytes(payload)
    manifest_before = stack.layout.manifest.read_bytes()
    cancelled = False
    real_replace = replace_manifest_atomically

    def replace_then_signal(
        candidate_handle: WindowsHandle,
        case_root: WindowsHandle,
        expected_manifest_handle: WindowsHandle,
    ) -> ManifestReplaceResult:
        nonlocal cancelled
        outcome = real_replace(candidate_handle, case_root, expected_manifest_handle)
        assert outcome.state is ManifestReplaceState.COMMITTED
        cancelled = True
        if interrupt_after_replace:
            raise KeyboardInterrupt
        return outcome

    monkeypatch.setattr(
        "paperworkshell.storage.manifest_repository.replace_manifest_atomically",
        replace_then_signal,
    )

    result = stack.service.import_source(
        CASE_ID,
        source,
        "Late signal synthetic.jpg",
        cancellation=lambda: cancelled,
    )

    assert cancelled
    assert result.manifest_revision == 1
    assert stack.layout.manifest.read_bytes() != manifest_before
    document = _manifest(stack.layout)
    sources = document["sources"]
    assert isinstance(sources, list)
    assert [item["source_id"] for item in sources if isinstance(item, dict)] == [
        str(result.source_id)
    ]
    assert (stack.layout.originals / f"{result.source_id}.bin").read_bytes() == payload
    _assert_no_staged_files(stack)


def test_interrupted_unpublished_checkpoint_removes_unreferenced_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = _stack(tmp_path)
    source = (tmp_path / "checkpoint-interrupt.jpg").resolve()
    source.write_bytes(_jpeg_of_size(8_194, fill=b"checkpoint-interrupt-"))
    manifest_before = stack.layout.manifest.read_bytes()
    interruption = KeyboardInterrupt()

    def interrupt_checkpoint(*_args: object, **_kwargs: object) -> object:
        raise interruption

    monkeypatch.setattr(
        "paperworkshell.storage.manifest_repository.publish_new_journal_checkpoint",
        interrupt_checkpoint,
    )

    with pytest.raises(KeyboardInterrupt) as raised:
        stack.service.import_source(CASE_ID, source, "Checkpoint interrupt synthetic.jpg")

    assert raised.value is interruption
    assert stack.layout.manifest.read_bytes() == manifest_before
    assert list(stack.layout.journal.iterdir()) == []
    assert list(stack.layout.originals.iterdir()) == []
    _assert_no_staged_files(stack)


def test_interruption_during_journal_setup_removes_unreferenced_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = _stack(tmp_path)
    source = (tmp_path / "journal-setup-interrupt.jpg").resolve()
    source.write_bytes(_jpeg_of_size(8_194, fill=b"journal-setup-interrupt-"))
    manifest_before = stack.layout.manifest.read_bytes()
    interruption = KeyboardInterrupt()
    real_open_handle = windows_open_handle

    def interrupt_journal(path: Path, *args: object, **kwargs: object) -> object:
        if path == stack.layout.journal:
            raise interruption
        return real_open_handle(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(repository_storage, "open_handle", interrupt_journal)

    with pytest.raises(KeyboardInterrupt) as raised:
        stack.service.import_source(CASE_ID, source, "Journal setup interrupt synthetic.jpg")

    assert raised.value is interruption
    assert stack.layout.manifest.read_bytes() == manifest_before
    assert list(stack.layout.journal.iterdir()) == []
    assert list(stack.layout.originals.iterdir()) == []
    _assert_no_staged_files(stack)


def test_next_import_recovers_pending_source_checkpoint_before_appending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = _stack(tmp_path)
    first_source = (tmp_path / "pending-first.jpg").resolve()
    second_source = (tmp_path / "pending-second.jpg").resolve()
    first_source.write_bytes(_jpeg_of_size(8_194, fill=b"pending-first-"))
    second_source.write_bytes(_jpeg_of_size(8_195, fill=b"pending-second-"))

    monkeypatch.setattr(
        repository_storage,
        "replace_manifest_atomically",
        lambda *_args, **_kwargs: ManifestReplaceResult(ManifestReplaceState.NOT_COMMITTED),
    )
    with pytest.raises(SourceIngestError):
        stack.service.import_source(CASE_ID, first_source, "Pending first synthetic.jpg")

    first_originals = tuple(stack.layout.originals.iterdir())
    assert len(first_originals) == 1
    assert (stack.layout.journal / "latest.json").is_file()
    monkeypatch.undo()

    second = stack.service.import_source(
        CASE_ID,
        second_source,
        "Pending second synthetic.jpg",
    )

    document = _manifest(stack.layout)
    sources = document["sources"]
    assert isinstance(sources, list)
    assert document["revision"] == 2
    assert len(sources) == 2
    assert {record["source_id"] for record in sources if isinstance(record, dict)} == {
        first_originals[0].stem,
        str(second.source_id),
    }
    assert {path.stem for path in stack.layout.originals.iterdir()} == {
        first_originals[0].stem,
        str(second.source_id),
    }
    assert stack.layout.manifest.read_bytes() == (stack.layout.journal / "latest.json").read_bytes()
    _assert_no_staged_files(stack)


@pytest.mark.parametrize(
    ("replace_state", "expected_staged_files"),
    [
        (ManifestReplaceState.NOT_COMMITTED, 0),
        (ManifestReplaceState.UNKNOWN, 1),
    ],
)
def test_central_manifest_replace_outcome_controls_recoverable_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replace_state: ManifestReplaceState,
    expected_staged_files: int,
) -> None:
    stack = _stack(tmp_path)
    source = (tmp_path / "replace-outcome.jpg").resolve()
    source.write_bytes(_jpeg_of_size(8_194, fill=b"replace-outcome-"))
    manifest_before = stack.layout.manifest.read_bytes()

    def forced_replace_outcome(
        _candidate_handle: WindowsHandle,
        _case_root: WindowsHandle,
        _expected_manifest_handle: WindowsHandle,
    ) -> ManifestReplaceResult:
        return ManifestReplaceResult(replace_state)

    monkeypatch.setattr(
        "paperworkshell.storage.manifest_repository.replace_manifest_atomically",
        forced_replace_outcome,
    )

    with pytest.raises(SourceIngestError) as raised:
        stack.service.import_source(CASE_ID, source, "Replace outcome synthetic.jpg")

    assert raised.value.code is SourceIngestErrorCode.COMMIT_FAILED
    assert raised.value.context == "source.commit_storage"
    assert stack.layout.manifest.read_bytes() == manifest_before
    assert _manifest(stack.layout)["sources"] == []
    originals = list(stack.layout.originals.iterdir())
    staged = list(stack.layout.temp.iterdir())
    assert len(originals) == 1
    assert originals[0].suffix == ".bin"
    assert UUID(originals[0].stem).version == 4
    assert len(staged) == expected_staged_files
    if staged:
        assert staged[0].suffix == ".tmp"
        assert UUID(staged[0].stem).version == 4
    checkpoint = stack.layout.journal / "latest.json"
    assert checkpoint.is_file()

    monkeypatch.undo()
    fresh_manager = CaseWorkspaceManager(profile_root=stack.profile, quota=_quota())
    fresh_service = CaseLifecycleService(WindowsCaseLifecycleStore(fresh_manager))
    reopened = fresh_service.open_case(CASE_ID)
    assert reopened.revision == 1
    assert stack.layout.manifest.read_bytes() == checkpoint.read_bytes()
    recovered_sources = _manifest(stack.layout)["sources"]
    assert isinstance(recovered_sources, list)
    assert [record["source_id"] for record in recovered_sources if isinstance(record, dict)] == [
        originals[0].stem
    ]
    fresh_service.close_case(CASE_ID)


def test_unknown_manifest_outcome_preserves_recovery_state_and_delayed_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = _stack(tmp_path)
    source = (tmp_path / "unknown-interrupt.jpg").resolve()
    source.write_bytes(_jpeg_of_size(8_195, fill=b"unknown-interrupt-"))
    manifest_before = stack.layout.manifest.read_bytes()
    interruption = KeyboardInterrupt()

    monkeypatch.setattr(
        "paperworkshell.storage.manifest_repository.replace_manifest_atomically",
        lambda *_args, **_kwargs: ManifestReplaceResult(
            ManifestReplaceState.UNKNOWN,
            delayed_exception=interruption,
        ),
    )

    with pytest.raises(KeyboardInterrupt) as raised:
        stack.service.import_source(CASE_ID, source, "Unknown interrupt synthetic.jpg")

    assert raised.value is interruption
    assert stack.layout.manifest.read_bytes() == manifest_before
    assert _manifest(stack.layout)["sources"] == []
    originals = list(stack.layout.originals.iterdir())
    staged = list(stack.layout.temp.iterdir())
    assert len(originals) == len(staged) == 1
    assert originals[0].suffix == ".bin"
    assert staged[0].suffix == ".tmp"
    checkpoint = stack.layout.journal / "latest.json"
    assert checkpoint.is_file()

    monkeypatch.undo()
    fresh_manager = CaseWorkspaceManager(profile_root=stack.profile, quota=_quota())
    fresh_service = CaseLifecycleService(WindowsCaseLifecycleStore(fresh_manager))
    reopened = fresh_service.open_case(CASE_ID)
    assert reopened.revision == 1
    assert stack.layout.manifest.read_bytes() == checkpoint.read_bytes()
    recovered_sources = _manifest(stack.layout)["sources"]
    assert isinstance(recovered_sources, list)
    assert [record["source_id"] for record in recovered_sources if isinstance(record, dict)] == [
        originals[0].stem
    ]
    fresh_service.close_case(CASE_ID)


def test_concurrent_commits_from_one_snapshot_allow_exactly_one_winner(
    tmp_path: Path,
) -> None:
    stack = _stack(tmp_path)
    first_path = (tmp_path / "concurrent-first.jpg").resolve()
    second_path = (tmp_path / "concurrent-second.jpg").resolve()
    first_path.write_bytes(_jpeg_of_size(8_195, fill=b"concurrent-first-"))
    second_path.write_bytes(_jpeg_of_size(8_196, fill=b"concurrent-second-"))
    first_before = _external_snapshot(first_path)
    second_before = _external_snapshot(second_path)
    first = stack.store.prepare(
        CASE_ID,
        first_path,
        "Concurrent first synthetic.jpg",
        deadline=lambda: False,
        cancellation=lambda: False,
        progress=lambda _value: None,
    )
    second = stack.store.prepare(
        CASE_ID,
        second_path,
        "Concurrent second synthetic.jpg",
        deadline=lambda: False,
        cancellation=lambda: False,
        progress=lambda _value: None,
    )
    commit_barrier = Barrier(2)

    def attempt(
        pending: WindowsPreparedSourceLease,
    ) -> SourceImportResult | SourceIngestError:
        inspection = _SyntheticImageInspector().inspect(
            pending.summary,
            deadline=lambda: False,
            cancellation=lambda: False,
            progress=lambda _value: None,
        )

        def progress(value: SourceImportProgress) -> None:
            if value.phase is SourceProgressPhase.COMMITTING and value.completed_units == 0:
                commit_barrier.wait(timeout=10)

        try:
            return pending.commit(
                inspection,
                deadline=lambda: False,
                cancellation=lambda: False,
                progress=progress,
            )
        except SourceIngestError as error:
            return error

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(attempt, first)
            second_future = executor.submit(attempt, second)
            outcomes = (first_future.result(), second_future.result())
    finally:
        first.abort()
        second.abort()

    committed = tuple(outcome for outcome in outcomes if isinstance(outcome, SourceImportResult))
    rejected = tuple(outcome for outcome in outcomes if isinstance(outcome, SourceIngestError))
    assert len(committed) == len(rejected) == 1
    assert rejected[0].code is SourceIngestErrorCode.MANIFEST_CONFLICT
    winner = committed[0]
    document = _manifest(stack.layout)
    sources = document["sources"]
    assert isinstance(sources, list)
    assert document["revision"] == 1
    assert [item["source_id"] for item in sources if isinstance(item, dict)] == [
        str(winner.source_id)
    ]
    assert sorted(path.name for path in stack.layout.originals.iterdir()) == [
        f"{winner.source_id}.bin"
    ]
    _assert_no_staged_files(stack)
    assert _external_snapshot(first_path) == first_before
    assert _external_snapshot(second_path) == second_before


def test_import_deadline_expires_while_case_coordination_is_contended(
    tmp_path: Path,
) -> None:
    stack = _stack(tmp_path)
    source = (tmp_path / "coordination-timeout.jpg").resolve()
    source.write_bytes(_jpeg_of_size(8_197, fill=b"coordination-timeout-"))
    source_before = _external_snapshot(source)
    manifest_before = stack.layout.manifest.read_bytes()
    reservation_name = "90000000-0000-4000-8000-000000000104.tmp"
    reservation = stack.manager.reserve_capacity(
        CASE_ID,
        QuotaArea.TEMP,
        relative_path=f"temp/{reservation_name}",
        is_directory=False,
        byte_count=1,
    )
    lock_held = Event()
    release_lock = Event()

    def hold_case_coordination() -> None:
        with reservation.publication_guard(
            case_identity=reservation.case_identity,
            relative_parts=reservation.relative_parts,
            is_directory=False,
            byte_count=1,
            entry_count=1,
        ):
            lock_held.set()
            assert release_lock.wait(timeout=5)

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            holder = executor.submit(hold_case_coordination)
            assert lock_held.wait(timeout=2)
            try:
                with pytest.raises(SourceIngestError) as raised:
                    stack.service.import_source(
                        CASE_ID,
                        source,
                        "Coordination timeout synthetic.jpg",
                        timeout_seconds=0.02,
                    )
            finally:
                release_lock.set()
            holder.result(timeout=2)
    finally:
        release_lock.set()
        reservation.release()

    assert raised.value.code is SourceIngestErrorCode.TIMED_OUT
    assert stack.layout.manifest.read_bytes() == manifest_before
    assert list(stack.layout.originals.iterdir()) == []
    assert list(stack.layout.temp.iterdir()) == []
    assert _external_snapshot(source) == source_before


def test_committed_pdf_batch_lease_preserves_plan_order_and_revalidates_all_handles(
    tmp_path: Path,
) -> None:
    stack = _stack(tmp_path)
    first = _import_pdf(stack, tmp_path, name="batch-private-first.pdf", marker=b"first")
    second = _import_pdf(stack, tmp_path, name="batch-private-second.pdf", marker=b"second")
    snapshot = WindowsManifestRepository(stack.manager).load(CASE_ID).snapshot
    plan = _reverse_source_order(build_pdf_export_plan(snapshot))

    assert isinstance(stack.store, CommittedPdfSourceBatchStore)
    with stack.store.borrow_committed_pdf_sources(
        plan,
        cancellation=lambda: False,
        deadline=lambda: False,
    ) as lease:
        assert isinstance(lease, CommittedPdfSourceBatchLease)
        assert repr(lease) == "CommittedPdfSourceBatchReadLease(<redacted>)"
        assert lease.case_id == plan.case_id
        assert lease.manifest_revision == plan.manifest_revision
        assert lease.manifest_sha256 == plan.manifest_sha256
        assert lease.manifest_cas_token == plan.manifest_cas_token
        assert tuple(source.source_id for source in lease.sources) == tuple(
            source.source_id for source in plan.sources
        )
        retained = lease.sources
        for source, expected in zip(retained, plan.sources, strict=True):
            assert isinstance(source, CommittedPdfSourceLease)
            assert repr(source) == "CommittedPdfSourceReadLease(<redacted>)"
            assert source.source_id == expected.source_id
            assert source.media_type is expected.media_type
            assert source.byte_size == expected.byte_size
            assert source.page_count == expected.page_count
            assert source.sha256 == expected.sha256
            assert source.signature_state is expected.signature_state
            assert source.encryption_state is expected.encryption_state
            assert source.handle_value > 0
            _advance_duplicated_handle(source.handle_value)
        lease.revalidate(cancellation=lambda: False, deadline=lambda: False)

    for source in retained:
        with pytest.raises(SourceIngestError):
            _ = source.handle_value

    active = stack.store.borrow_committed_pdf_sources(
        plan,
        cancellation=lambda: False,
        deadline=lambda: False,
    )
    active_sources = active.sources
    alias = tmp_path / "private-batch-link.bin"
    case_copy = stack.layout.originals / f"{first.source_id}.bin"
    os.link(case_copy, alias)
    try:
        with pytest.raises(SourceIngestError) as changed:
            active.revalidate(cancellation=lambda: False, deadline=lambda: False)
        assert changed.value.code is SourceIngestErrorCode.SOURCE_CHANGED
        for source in active_sources:
            with pytest.raises(SourceIngestError):
                _ = source.handle_value
    finally:
        alias.unlink()
        active.close()

    assert second.source_id != first.source_id
    assert "batch-private" not in repr(active)


def test_committed_pdf_batch_rejects_stale_duplicate_missing_and_changed_plan_facts(
    tmp_path: Path,
) -> None:
    stack = _stack(tmp_path)
    _import_pdf(stack, tmp_path, name="batch-plan-private.pdf", marker=b"plan")
    snapshot = WindowsManifestRepository(stack.manager).load(CASE_ID).snapshot
    plan = build_pdf_export_plan(snapshot)

    stale = PdfExportPlan(
        case_id=plan.case_id,
        manifest_revision=plan.manifest_revision + 1,
        manifest_sha256=plan.manifest_sha256,
        manifest_cas_token=plan.manifest_cas_token,
        sources=plan.sources,
        pages=plan.pages,
    )
    with pytest.raises(SourceIngestError) as stale_error:
        stack.store.borrow_committed_pdf_sources(
            stale,
            cancellation=lambda: False,
            deadline=lambda: False,
        )
    assert stale_error.value.code is SourceIngestErrorCode.MANIFEST_CONFLICT

    duplicate = build_pdf_export_plan(snapshot)
    object.__setattr__(duplicate, "sources", (duplicate.sources[0], duplicate.sources[0]))
    with pytest.raises(SourceIngestError) as duplicate_error:
        stack.store.borrow_committed_pdf_sources(
            duplicate,
            cancellation=lambda: False,
            deadline=lambda: False,
        )
    assert duplicate_error.value.code is SourceIngestErrorCode.MODEL_INVALID

    missing_id = UUID("99000000-0000-4000-8000-000000000112")
    missing_source = PdfExportSource(
        source_id=missing_id,
        byte_size=plan.sources[0].byte_size,
        page_count=plan.sources[0].page_count,
        sha256=plan.sources[0].sha256,
        signature_state=plan.sources[0].signature_state,
    )
    missing_page = PdfExportPage(
        output_page_index=0,
        page_id=UUID("99000000-0000-4000-8000-000000000113"),
        source_id=missing_id,
        source_page_index=0,
        additional_rotation=plan.pages[0].additional_rotation,
    )
    missing = PdfExportPlan(
        case_id=plan.case_id,
        manifest_revision=plan.manifest_revision,
        manifest_sha256=plan.manifest_sha256,
        manifest_cas_token=plan.manifest_cas_token,
        sources=(missing_source,),
        pages=(missing_page,),
    )
    with pytest.raises(SourceIngestError) as missing_error:
        stack.store.borrow_committed_pdf_sources(
            missing,
            cancellation=lambda: False,
            deadline=lambda: False,
        )
    assert missing_error.value.code is SourceIngestErrorCode.SOURCE_UNAVAILABLE

    changed_source = PdfExportSource(
        source_id=plan.sources[0].source_id,
        byte_size=plan.sources[0].byte_size + 1,
        page_count=plan.sources[0].page_count,
        sha256=plan.sources[0].sha256,
        signature_state=plan.sources[0].signature_state,
    )
    changed = PdfExportPlan(
        case_id=plan.case_id,
        manifest_revision=plan.manifest_revision,
        manifest_sha256=plan.manifest_sha256,
        manifest_cas_token=plan.manifest_cas_token,
        sources=(changed_source,),
        pages=plan.pages,
    )
    with pytest.raises(SourceIngestError) as changed_error:
        stack.store.borrow_committed_pdf_sources(
            changed,
            cancellation=lambda: False,
            deadline=lambda: False,
        )
    assert changed_error.value.code is SourceIngestErrorCode.SOURCE_CHANGED
    assert "batch-plan-private" not in str(changed_error.value)


@pytest.mark.parametrize("encryption_state", ("encrypted", "unknown"))
def test_committed_pdf_batch_rejects_encrypted_or_unknown_manifest_state(
    tmp_path: Path,
    encryption_state: str,
) -> None:
    stack = _stack(tmp_path)
    imported = _import_pdf(
        stack,
        tmp_path,
        name="batch-encryption-private.pdf",
        marker=b"encryption",
    )
    document = _manifest(stack.layout)
    sources = cast(list[dict[str, object]], document["sources"])
    sources[0]["encryption_state"] = encryption_state
    stack.layout.manifest.write_bytes(canonical_manifest_bytes(document))
    snapshot = WindowsManifestRepository(stack.manager).load(CASE_ID).snapshot
    source = PdfExportSource(
        source_id=imported.source_id,
        byte_size=imported.byte_size,
        page_count=2,
        sha256=imported.sha256,
        signature_state=PdfExportSourceSignatureState.NONE_DETECTED,
    )
    plan = PdfExportPlan(
        case_id=CASE_ID,
        manifest_revision=snapshot.revision,
        manifest_sha256=snapshot.sha256,
        manifest_cas_token=snapshot.cas_token,
        sources=(source,),
        pages=(
            PdfExportPage(
                output_page_index=0,
                page_id=imported.page_ids[0],
                source_id=imported.source_id,
                source_page_index=0,
                additional_rotation=PdfExportRotation.DEGREES_0,
            ),
        ),
    )

    with pytest.raises(SourceIngestError) as raised:
        stack.store.borrow_committed_pdf_sources(
            plan,
            cancellation=lambda: False,
            deadline=lambda: False,
        )

    assert raised.value.code is SourceIngestErrorCode.SOURCE_UNAVAILABLE
    assert "batch-encryption-private" not in str(raised.value)


def test_committed_pdf_batch_closes_prior_handles_when_a_later_source_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = _stack(tmp_path)
    _import_pdf(stack, tmp_path, name="batch-close-first.pdf", marker=b"close-first")
    second = _import_pdf(stack, tmp_path, name="batch-close-second.pdf", marker=b"close-second")
    plan = build_pdf_export_plan(WindowsManifestRepository(stack.manager).load(CASE_ID).snapshot)
    second_copy = stack.layout.originals / f"{second.source_id}.bin"
    second_copy.write_bytes(second_copy.read_bytes() + b"changed")
    opened_sources: list[WindowsHandle] = []
    real_open_handle = windows_open_handle

    def track_source_handles(path: Path, *args: object, **kwargs: object) -> WindowsHandle:
        handle = real_open_handle(path, *args, **kwargs)  # type: ignore[arg-type]
        if path.parent == stack.layout.originals and path.suffix == ".bin":
            opened_sources.append(handle)
        return handle

    monkeypatch.setattr(source_ingest_storage, "open_handle", track_source_handles)

    with pytest.raises(SourceIngestError) as raised:
        stack.store.borrow_committed_pdf_sources(
            plan,
            cancellation=lambda: False,
            deadline=lambda: False,
        )

    assert raised.value.code is SourceIngestErrorCode.SOURCE_CHANGED
    assert len(opened_sources) == 2
    assert all(handle.value == 0 for handle in opened_sources)


def test_committed_pdf_batch_rejects_non_pdf_and_non_ready_sources(
    tmp_path: Path,
) -> None:
    stack = _stack(tmp_path)
    image = (tmp_path / "batch-image-private.jpg").resolve()
    image.write_bytes(_jpeg())
    imported = stack.service.import_source(CASE_ID, image, "Batch image private.jpg")
    snapshot = WindowsManifestRepository(stack.manager).load(CASE_ID).snapshot
    source = PdfExportSource(
        source_id=imported.source_id,
        byte_size=imported.byte_size,
        page_count=1,
        sha256=imported.sha256,
        signature_state=PdfExportSourceSignatureState.NONE_DETECTED,
    )
    page = PdfExportPage(
        output_page_index=0,
        page_id=imported.page_ids[0],
        source_id=imported.source_id,
        source_page_index=0,
        additional_rotation=PdfExportRotation.DEGREES_0,
    )
    plan = PdfExportPlan(
        case_id=CASE_ID,
        manifest_revision=snapshot.revision,
        manifest_sha256=snapshot.sha256,
        manifest_cas_token=snapshot.cas_token,
        sources=(source,),
        pages=(page,),
    )
    with pytest.raises(SourceIngestError) as media_error:
        stack.store.borrow_committed_pdf_sources(
            plan,
            cancellation=lambda: False,
            deadline=lambda: False,
        )
    assert media_error.value.code is SourceIngestErrorCode.SOURCE_UNAVAILABLE

    document = _manifest(stack.layout)
    sources = cast(list[dict[str, object]], document["sources"])
    sources[0]["import_state"] = "pending"
    stack.layout.manifest.write_bytes(canonical_manifest_bytes(document))
    with pytest.raises(SourceIngestError) as readiness_error:
        stack.store.borrow_committed_pdf_sources(
            plan,
            cancellation=lambda: False,
            deadline=lambda: False,
        )
    assert readiness_error.value.code is SourceIngestErrorCode.MANIFEST_INVALID

"""Security and privacy evidence for PWS-0104 immutable source ingest."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import struct
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from paperworkshell.app.source_ingest import SourceIngestService
from paperworkshell.domain.import_precheck import ImportLimits
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
from paperworkshell.storage._windows import MAXIMUM_STREAM_CHUNK_BYTES, WindowsHandle
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.source_ingest import WindowsSourceIngestStore
from paperworkshell.storage.source_precheck import WindowsSourcePrechecker
from paperworkshell.storage.workspace import (
    CaseWorkspaceManager,
    CaseWorkspacePaths,
)

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0104 targets the supported Windows storage boundary"
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CASE_ID = UUID("10000000-0000-4000-8000-000000000204")
NOW = datetime(2026, 8, 2, 13, 0, tzinfo=UTC)

CancellationProbe = Callable[[], bool]
DeadlineProbe = Callable[[], bool]
ProgressSink = Callable[[SourceImportProgress], None]


class _SequentialUuidFactory:
    def __init__(self, prefix: int) -> None:
        self._prefix = prefix
        self._next = 1

    def __call__(self) -> UUID:
        result = UUID(f"{self._prefix:08x}-0000-4000-8000-{self._next:012x}")
        self._next += 1
        return result


class _SyntheticImageInspector:
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


@dataclass(frozen=True)
class _Stack:
    layout: CaseWorkspacePaths
    service: SourceIngestService


def _quota() -> WorkspaceQuota:
    return WorkspaceQuota(
        maximum_case_bytes=24_000_000,
        maximum_cache_bytes=1_000_000,
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


def _stack(tmp_path: Path) -> _Stack:
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=_quota(),
        id_factory=lambda: CASE_ID,
    )
    lease = WindowsCaseLifecycleStore(manager, clock=lambda: NOW).create_case()
    lease.close()
    store = WindowsSourceIngestStore(
        manager,
        WindowsSourcePrechecker(_limits()),
        maximum_page_count=16,
        clock=lambda: NOW,
        id_factory=_SequentialUuidFactory(0x60000000),
        temp_id_factory=_SequentialUuidFactory(0x70000000),
    )
    return _Stack(
        layout=manager.layout(CASE_ID),
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


def _jpeg_of_size(byte_size: int, *, fill: bytes) -> bytes:
    base = _jpeg()
    if byte_size < len(base) or not fill:
        raise ValueError("synthetic JPEG size and fill must be usable")
    remaining = byte_size - len(base)
    repeated = (fill * ((remaining + len(fill) - 1) // len(fill)))[:remaining]
    return base[:-2] + repeated + base[-2:]


def _external_snapshot(path: Path) -> tuple[str, int, int, int]:
    stat = path.stat(follow_symlinks=False)
    return (
        hashlib.sha256(path.read_bytes()).hexdigest(),
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ino,
    )


def test_private_source_names_paths_and_bytes_do_not_become_internal_names_or_logs(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    stack = _stack(tmp_path)
    private_parent_token = "PRIVATE-PARENT-PWS0104-CANARY"
    private_filename_token = "PRIVATE-ORIGINAL-PWS0104-CANARY.jpg"
    private_content_token = b"PRIVATE-CONTENT-PWS0104-CANARY"
    display_name = "R\u00e9sum\u00e9 priv\u00e9 du demandeur.jpg"
    source_parent = (tmp_path / private_parent_token).resolve()
    source_parent.mkdir()
    source = (source_parent / private_filename_token).resolve()
    payload = _jpeg_of_size(8_192, fill=private_content_token)
    source.write_bytes(payload)
    external_before = _external_snapshot(source)

    result = stack.service.import_source(CASE_ID, source, display_name)

    assert _external_snapshot(source) == external_before
    assert source.read_bytes() == payload
    manifest_text = stack.layout.manifest.read_text(encoding="utf-8")
    document = json.loads(manifest_text)
    assert document["sources"][0]["display_name"] == display_name
    assert display_name in manifest_text
    assert str(source) not in manifest_text
    assert private_parent_token not in manifest_text
    assert private_filename_token not in manifest_text

    internal_paths = [
        path.relative_to(stack.layout.case_root).as_posix()
        for path in stack.layout.case_root.rglob("*")
    ]
    internal_names = "\n".join(internal_paths).casefold()
    for private_value in (
        private_parent_token,
        private_filename_token,
        display_name,
    ):
        assert private_value.casefold() not in internal_names

    originals = list(stack.layout.originals.iterdir())
    assert originals == [stack.layout.originals / f"{result.source_id}.bin"]
    assert originals[0].suffix == ".bin"
    assert UUID(originals[0].stem).version == 4
    assert originals[0].read_bytes() == payload
    assert list(stack.layout.temp.iterdir()) == []

    routine_text = f"{result!r}\n{result.duplicate!r}\n{caplog.text}"
    assert str(source) not in routine_text
    assert private_parent_token not in routine_text
    assert private_filename_token not in routine_text
    assert display_name not in routine_text
    assert private_content_token.decode("ascii") not in routine_text
    assert result.sha256 not in routine_text


def test_source_hardlink_created_during_copy_fails_closed_without_record(
    tmp_path: Path,
) -> None:
    stack = _stack(tmp_path)
    source = (tmp_path / "source-change.jpg").resolve()
    alias = (tmp_path / "source-change-alias.jpg").resolve()
    payload = _jpeg_of_size(
        MAXIMUM_STREAM_CHUNK_BYTES + 257,
        fill=b"source-change-synthetic-",
    )
    source.write_bytes(payload)
    external_before = _external_snapshot(source)
    manifest_before = stack.layout.manifest.read_bytes()
    linked = False

    def progress(value: SourceImportProgress) -> None:
        nonlocal linked
        if value.phase is SourceProgressPhase.COPYING and not linked:
            os.link(source, alias)
            linked = True

    try:
        with pytest.raises(SourceIngestError) as raised:
            stack.service.import_source(
                CASE_ID,
                source,
                "Source change synthetic.jpg",
                progress=progress,
            )

        assert linked
        assert raised.value.code is SourceIngestErrorCode.SOURCE_CHANGED
        assert stack.layout.manifest.read_bytes() == manifest_before
        assert list(stack.layout.originals.iterdir()) == []
        assert list(stack.layout.temp.iterdir()) == []
        assert _external_snapshot(source) == external_before
        assert alias.read_bytes() == payload
    finally:
        alias.unlink(missing_ok=True)


def test_integrity_verification_rejects_case_copy_hardlink_alias(tmp_path: Path) -> None:
    stack = _stack(tmp_path)
    source = (tmp_path / "integrity-hardlink.jpg").resolve()
    source.write_bytes(_jpeg_of_size(8_192, fill=b"case-copy-hardlink-"))
    result = stack.service.import_source(CASE_ID, source, "Hardlink synthetic.jpg")
    case_copy = stack.layout.originals / f"{result.source_id}.bin"
    alias = stack.layout.originals / "untrusted-alias.bin"

    os.link(case_copy, alias)
    try:
        assert (
            stack.service.verify_integrity(CASE_ID, result.source_id).status
            is SourceIntegrityStatus.MISMATCH
        )
    finally:
        alias.unlink(missing_ok=True)

    assert (
        stack.service.verify_integrity(CASE_ID, result.source_id).status
        is SourceIntegrityStatus.VERIFIED
    )


def test_post_publication_failure_cleans_only_new_case_objects_and_preserves_sentinels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = _stack(tmp_path)
    source = (tmp_path / "publication-failure.jpg").resolve()
    source.write_bytes(_jpeg_of_size(8_193, fill=b"publication-failure-"))
    source_before = _external_snapshot(source)
    manifest_before = stack.layout.manifest.read_bytes()
    sibling_root = stack.layout.case_root.parent / "20000000-0000-4000-8000-000000000204"
    sibling_root.mkdir()
    sibling = sibling_root / "sibling-sentinel.bin"
    outside = tmp_path / "outside-sentinel.bin"
    sibling.write_bytes(b"sibling-sentinel-content")
    outside.write_bytes(b"outside-sentinel-content")
    sibling_before = _external_snapshot(sibling)
    outside_before = _external_snapshot(outside)
    raw_failure_token = f"RAW-PUBLICATION-FAILURE:{outside}"

    def fail_after_source_publication(
        _candidate_handle: WindowsHandle,
        _case_root: WindowsHandle,
        _expected_manifest_handle: WindowsHandle,
    ) -> object:
        raise OSError(raw_failure_token)

    monkeypatch.setattr(
        "paperworkshell.storage.manifest_repository.replace_manifest_atomically",
        fail_after_source_publication,
    )

    with pytest.raises(SourceIngestError) as raised:
        stack.service.import_source(CASE_ID, source, "Publication failure synthetic.jpg")

    assert raised.value.code is SourceIngestErrorCode.COMMIT_FAILED
    assert raised.value.context == "source.commit_storage"
    assert raw_failure_token not in str(raised.value)
    assert raw_failure_token not in repr(raised.value)
    assert str(outside) not in str(raised.value)
    assert stack.layout.manifest.read_bytes() == manifest_before
    checkpoint = json.loads((stack.layout.journal / "latest.json").read_bytes())
    assert type(checkpoint) is dict
    sources = checkpoint["sources"]
    assert type(sources) is list and len(sources) == 1
    source_record = sources[0]
    assert type(source_record) is dict
    storage_name = source_record["storage_name"]
    assert type(storage_name) is str
    case_copy = stack.layout.originals / storage_name
    assert case_copy.read_bytes() == source.read_bytes()
    assert list(stack.layout.temp.iterdir()) == []
    assert _external_snapshot(source) == source_before
    assert _external_snapshot(sibling) == sibling_before
    assert _external_snapshot(outside) == outside_before

    monkeypatch.undo()
    recovered = WindowsManifestRepository(
        CaseWorkspaceManager(profile_root=tmp_path / "profile", quota=_quota())
    ).load(CASE_ID)
    assert recovered.snapshot.document == checkpoint
    assert stack.layout.manifest.read_bytes() == (stack.layout.journal / "latest.json").read_bytes()


def _qualified_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value)
        if parent is not None:
            return f"{parent}.{node.attr}"
    return None


def test_ingest_layers_import_no_shell_or_network_capabilities() -> None:
    product_files = (
        PROJECT_ROOT / "src/paperworkshell/domain/source_ingest.py",
        PROJECT_ROOT / "src/paperworkshell/app/source_ingest.py",
        PROJECT_ROOT / "src/paperworkshell/storage/source_ingest.py",
    )
    prohibited_import_roots = {
        "ftplib",
        "http",
        "requests",
        "socket",
        "subprocess",
        "urllib",
        "webbrowser",
    }
    prohibited_calls = {
        "os.popen",
        "os.startfile",
        "os.system",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.Popen",
        "subprocess.run",
    }

    violations: list[str] = []
    for path in product_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.partition(".")[0] in prohibited_import_roots:
                        violations.append(f"{path.name}:{node.lineno}:import:{alias.name}")
            elif isinstance(node, ast.ImportFrom):
                root = "" if node.module is None else node.module.partition(".")[0]
                if root in prohibited_import_roots:
                    violations.append(f"{path.name}:{node.lineno}:from:{node.module}")
            elif isinstance(node, ast.Call):
                called = _qualified_name(node.func)
                if called in prohibited_calls:
                    violations.append(f"{path.name}:{node.lineno}:call:{called}")

    assert violations == []

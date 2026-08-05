"""Security and privacy evidence for PWS-0108 thumbnail cache storage."""

from __future__ import annotations

import ast
import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from paperworkshell.domain.pdf_rendering import PdfRenderRotation
from paperworkshell.domain.pdf_thumbnails import (
    PdfThumbnailArtifactHeader,
    PdfThumbnailCacheKey,
    PdfThumbnailError,
    PdfThumbnailErrorCode,
    encode_pdf_thumbnail_artifact,
)
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.thumbnail_cache import WindowsThumbnailCacheStore
from paperworkshell.storage.workspace import CaseWorkspaceManager

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0108 storage security evidence targets Windows"
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CASE_ID = UUID("10000000-0000-4000-8000-000000000308")
SOURCE_ID = UUID("20000000-0000-4000-8000-000000000308")
PAGE_ID = UUID("30000000-0000-4000-8000-000000000308")
OPERATION_ID = UUID("40000000-0000-4000-8000-000000000308")
CACHE_OBJECT_ID = UUID("50000000-0000-4000-8000-000000000308")
NOW = datetime(2026, 8, 5, 13, 0, tzinfo=UTC)


def _stack(tmp_path: Path) -> tuple[CaseWorkspaceManager, WindowsThumbnailCacheStore]:
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=WorkspaceQuota(
            maximum_case_bytes=8_000_000,
            maximum_cache_bytes=2_000_000,
            maximum_temp_bytes=2_000_000,
            maximum_entries=256,
            maximum_depth=16,
        ),
        id_factory=lambda: CASE_ID,
    )
    lease = WindowsCaseLifecycleStore(manager, clock=lambda: NOW).create_case()
    lease.close()
    return manager, WindowsThumbnailCacheStore(manager)


def _key() -> PdfThumbnailCacheKey:
    return PdfThumbnailCacheKey(
        case_id=CASE_ID,
        source_id=SOURCE_ID,
        page_id=PAGE_ID,
        source_page_index=0,
        source_byte_size=31,
        source_sha256=hashlib.sha256(b"synthetic hostile source").hexdigest(),
        maximum_width_pixels=2,
        maximum_height_pixels=2,
        additional_rotation=PdfRenderRotation.DEGREES_0,
        renderer_profile_sha256=hashlib.sha256(b"fixed profile").hexdigest(),
    )


def _artifact(key: PdfThumbnailCacheKey) -> bytes:
    pixels = b"PRIVATE-PIXEL-CANARY"[:3] + b"\xff"
    pixels *= 4
    header = PdfThumbnailArtifactHeader(
        operation_id=OPERATION_ID,
        cache_object_id=CACHE_OBJECT_ID,
        cache_key=key,
        width_pixels=2,
        height_pixels=2,
        row_stride_bytes=8,
        payload_bytes=len(pixels),
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
        intrinsic_rotation=PdfRenderRotation.DEGREES_0,
        additional_rotation=PdfRenderRotation.DEGREES_0,
    )
    return encode_pdf_thumbnail_artifact(header, pixels)


def test_internal_cache_name_and_public_reprs_exclude_private_tokens(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager, store = _stack(tmp_path)
    key = _key()
    private_path_token = "PRIVATE-APPLICANT-NAME.pdf"
    final = manager.layout(CASE_ID).thumbnails / key.internal_name
    final.write_bytes(_artifact(key))

    lookup = store.lookup(key)

    assert lookup.is_hit
    rendered = f"{lookup!r}\n{lookup.artifact!r}\n{caplog.text}"
    assert private_path_token not in final.name
    assert private_path_token not in rendered
    assert "PRIVATE-PIXEL-CANARY" not in rendered
    assert key.source_sha256 not in rendered
    assert key.renderer_profile_sha256 not in rendered


def test_hard_linked_cache_entry_is_rejected_without_deleting_either_name(
    tmp_path: Path,
) -> None:
    manager, store = _stack(tmp_path)
    key = _key()
    final = manager.layout(CASE_ID).thumbnails / key.internal_name
    final.write_bytes(_artifact(key))
    alias = tmp_path / "outside-alias.bin"
    os.link(final, alias)

    with pytest.raises(PdfThumbnailError) as raised:
        store.lookup(key)

    assert raised.value.code is PdfThumbnailErrorCode.CACHE_INVALID
    assert final.exists()
    assert alias.exists()
    assert final.read_bytes() == alias.read_bytes()


def test_alternate_data_stream_is_rejected_and_not_reported(
    tmp_path: Path,
) -> None:
    manager, store = _stack(tmp_path)
    key = _key()
    final = manager.layout(CASE_ID).thumbnails / key.internal_name
    final.write_bytes(_artifact(key))
    ads_token = "PRIVATE-ADS-CANARY"
    with Path(f"{final}:{ads_token}").open("wb") as stream:
        stream.write(b"private alternate bytes")

    with pytest.raises(PdfThumbnailError) as raised:
        store.lookup(key)

    assert raised.value.code is PdfThumbnailErrorCode.CACHE_INVALID
    assert ads_token not in str(raised.value)
    assert ads_token not in repr(raised.value)
    assert final.exists()


def test_stale_cleanup_retains_hard_linked_stage_and_reports_partial(
    tmp_path: Path,
) -> None:
    manager, store = _stack(tmp_path)
    stage_id = UUID("60000000-0000-4000-8000-000000000308")
    stage = manager.layout(CASE_ID).thumbnails / f"{stage_id}.tmp"
    stage.write_bytes(b"untrusted partial cache bytes")
    alias = tmp_path / "outside-stage-alias.bin"
    os.link(stage, alias)

    report = store.cleanup_stale_stages(CASE_ID)

    assert report.status.value == "partial"
    assert report.removed_entries == 0
    assert report.retained_entries == 1
    assert stage.exists()
    assert alias.exists()


def test_worker_cannot_receive_persistent_cache_handle_and_stage_write_is_bounded(
    tmp_path: Path,
) -> None:
    manager, store = _stack(tmp_path)
    key = _key()

    with store.create_stage(key) as stage:
        stage_path = manager.layout(CASE_ID).thumbnails / f"{stage.cache_object_id}.tmp"
        assert not hasattr(stage, "write_handle")
        with pytest.raises(PdfThumbnailError) as raised:
            stage.write_artifact(b"x" * (stage.maximum_bytes + 1))

        assert raised.value.code is PdfThumbnailErrorCode.LIMIT_EXCEEDED
        assert raised.value.context == "thumbnail.stage_size"
        assert stage_path.stat().st_size == 0

    assert not stage_path.exists()


def test_storage_slice_imports_no_worker_shell_or_network_capability() -> None:
    product_files = (
        PROJECT_ROOT / "src/paperworkshell/storage/thumbnail_cache.py",
        PROJECT_ROOT / "src/paperworkshell/storage/source_ingest.py",
    )
    prohibited_roots = {
        "ftplib",
        "http",
        "requests",
        "socket",
        "subprocess",
        "urllib",
        "webbrowser",
        "worker",
    }
    violations: list[str] = []
    for path in product_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.partition(".")[0] in prohibited_roots:
                        violations.append(f"{path.name}:{node.lineno}:{alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                parts = node.module.split(".")
                if parts[0] in prohibited_roots or "worker" in parts:
                    violations.append(f"{path.name}:{node.lineno}:{node.module}")

    assert violations == []

"""Security and privacy evidence for immutable signature-image case storage."""

from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path

import pytest

from paperworkshell.domain.signature_images import (
    SignatureImageError,
    SignatureImageErrorCode,
    SignatureImagePlacementCommitResult,
    SignatureImageProgress,
)
from paperworkshell.overlays.models import (
    OverlayAssetKind,
    OverlayAssetMediaType,
    OverlayAssetRecord,
)
from paperworkshell.storage.signature_images import WindowsSignatureImageAssetStore
from tests.integration.test_signature_image_storage_windows import (
    CASE_ID,
    PAGE_SOURCE_PAYLOAD,
    SIGNATURE_FIXTURE,
    SOURCE_ID,
    _create_change,
    _prepare,
    _stack,
)

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0305 signature-image storage targets supported Windows volumes",
)


def _external_snapshot(path: Path) -> tuple[str, int, int, int]:
    stat = path.stat(follow_symlinks=False)
    return (
        hashlib.sha256(path.read_bytes()).hexdigest(),
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ino,
    )


def _commit_asset(
    store: WindowsSignatureImageAssetStore,
    source: Path,
) -> tuple[OverlayAssetRecord, SignatureImagePlacementCommitResult]:
    progress: list[SignatureImageProgress] = []
    prepared = store.prepare(
        CASE_ID,
        source,
        cancellation=lambda: False,
        deadline=lambda: False,
        progress=progress.append,
    )
    asset, change = _create_change(prepared)
    result = prepared.commit(
        change,
        cancellation=lambda: False,
        deadline=lambda: False,
        progress=progress.append,
    )
    return asset, result


def _jpeg_with_content(content: bytes) -> bytes:
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
        + content
        + b"\xff\xd9"
    )


def test_private_path_name_and_image_canary_never_reach_repr_logs_or_manifest(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    stack = _stack(tmp_path)
    parent_marker = "SENSITIVE-SIGNATURE-PARENT-PWS0305-CANARY"
    filename_token = "PRIVATE-SIGNATURE-NAME-PWS0305-CANARY.jpg"
    content_token = b"PRIVATE-SIGNATURE-IMAGE-PWS0305-CANARY"
    private_parent = (tmp_path / parent_marker).resolve()
    private_parent.mkdir()
    source = (private_parent / filename_token).resolve()
    payload = _jpeg_with_content(content_token)
    source.write_bytes(payload)
    before = _external_snapshot(source)

    prepared, progress = _prepare(stack, source)
    asset, change = _create_change(prepared)
    result = prepared.commit(
        change,
        cancellation=lambda: False,
        deadline=lambda: False,
        progress=progress.append,
    )
    with pytest.raises(SignatureImageError) as closed:
        _ = prepared.handle_value

    manifest_text = stack.layout.manifest.read_text(encoding="utf-8")
    rendered = "\n".join(
        (
            repr(prepared),
            repr(prepared.summary),
            repr(change),
            repr(result),
            repr(closed.value),
            str(closed.value),
            repr(progress),
            caplog.text,
        )
    )
    assert _external_snapshot(source) == before
    assert (stack.layout.originals / asset.storage_name).read_bytes() == payload
    assert str(source) not in manifest_text
    assert parent_marker not in manifest_text
    assert filename_token not in manifest_text
    assert content_token.decode() not in manifest_text
    for token in (str(source), parent_marker, filename_token, content_token.decode()):
        assert token not in rendered


def test_cleanup_cannot_delete_a_source_owned_original_via_forged_asset_record(
    tmp_path: Path,
) -> None:
    stack = _stack(tmp_path)
    source_path = stack.layout.originals / f"{SOURCE_ID}.bin"
    forged = OverlayAssetRecord(
        SOURCE_ID,
        OverlayAssetKind.SIGNATURE_IMAGE,
        source_path.name,
        OverlayAssetMediaType.PNG,
        len(PAGE_SOURCE_PAYLOAD),
        hashlib.sha256(PAGE_SOURCE_PAYLOAD).hexdigest(),
    )

    result = stack.store.cleanup_unused_asset(
        CASE_ID,
        forged,
        cancellation=lambda: False,
        deadline=lambda: False,
        progress=lambda _progress: None,
    )

    assert result.status.value == "retained"
    assert source_path.read_bytes() == PAGE_SOURCE_PAYLOAD


def test_borrow_rejects_a_hardlinked_committed_asset_without_disclosing_paths(
    tmp_path: Path,
) -> None:
    stack = _stack(tmp_path)
    source = (tmp_path / "synthetic-signature.png").resolve()
    source.write_bytes(SIGNATURE_FIXTURE.read_bytes())
    asset, _result = _commit_asset(stack.store, source)
    asset_path = stack.layout.originals / asset.storage_name
    alias_token = "PRIVATE-SIGNATURE-HARDLINK-PWS0305-CANARY.bin"
    alias = stack.layout.originals / alias_token
    os.link(asset_path, alias)

    with pytest.raises(SignatureImageError) as raised:
        stack.store.borrow_committed_signature_image(
            CASE_ID,
            asset.asset_id,
            cancellation=lambda: False,
            deadline=lambda: False,
        )

    assert raised.value.code is SignatureImageErrorCode.ASSET_CHANGED
    assert str(asset_path) not in str(raised.value)
    assert alias_token not in str(raised.value)
    assert asset_path.read_bytes() == SIGNATURE_FIXTURE.read_bytes()

"""Focused pure evidence for the signature-image Windows storage boundary."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.domain.import_precheck import (
    ImportPrecheckError,
    ImportPrecheckErrorCode,
    ImportPreliminaryState,
)
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.signature_images import (
    PreparedSignatureImageSummary,
    SignatureImageError,
    SignatureImageErrorCode,
    SignatureImageMediaType,
)
from paperworkshell.overlays.models import (
    OverlayAssetKind,
    OverlayAssetMediaType,
    OverlayAssetRecord,
)
from paperworkshell.overlays.signature_commands import (
    CreateSignatureImageCommand,
    SignatureAssetCleanupDisposition,
    create_signature_image_candidate,
)
from paperworkshell.storage import signature_images as storage_module

ROOT = Path(__file__).resolve().parents[2]
REPRESENTATIVE = ROOT / "schemas" / "examples" / "case-manifest-v2-representative.json"
CASE_ID = UUID("10000000-0000-4000-8000-000000000002")
PAGE_ID = UUID("30000000-0000-4000-8000-000000000001")
ASSET_ID = UUID("4a000000-0000-4000-8000-000000000305")
OVERLAY_ID = UUID("6a000000-0000-4000-8000-000000000305")
PENDING_ID = UUID("7a000000-0000-4000-8000-000000000305")
PAYLOAD = b"synthetic-signature-image-storage-unit"


def _document() -> dict[str, object]:
    loaded = json.loads(REPRESENTATIVE.read_bytes())
    assert type(loaded) is dict
    return cast(dict[str, object], loaded)


def _snapshot() -> ManifestSnapshot:
    document = _document()
    return ManifestSnapshot(
        CASE_ID,
        2,
        7,
        document,
        "a" * 64,
        "signature-storage-unit-token",
    )


def _summary() -> PreparedSignatureImageSummary:
    return PreparedSignatureImageSummary(
        PENDING_ID,
        CASE_ID,
        ASSET_ID,
        SignatureImageMediaType.PNG,
        len(PAYLOAD),
        hashlib.sha256(PAYLOAD).hexdigest(),
    )


def _asset() -> OverlayAssetRecord:
    summary = _summary()
    return OverlayAssetRecord(
        ASSET_ID,
        OverlayAssetKind.SIGNATURE_IMAGE,
        f"{ASSET_ID}.bin",
        OverlayAssetMediaType.PNG,
        summary.byte_size,
        summary.sha256,
    )


def _candidate() -> dict[str, object]:
    change = create_signature_image_candidate(
        _snapshot(),
        CreateSignatureImageCommand(
            CASE_ID,
            7,
            _asset(),
            OVERLAY_ID,
            PAGE_ID,
            NormalizedPageBox(100_000, 200_000, 300_000, 100_000),
        ),
    )
    return change.candidate_document


def _assert_signature_error(
    code: SignatureImageErrorCode,
    action: Callable[[], object],
) -> SignatureImageError:
    with pytest.raises(SignatureImageError) as raised:
        action()
    assert raised.value.code is code
    return raised.value


def test_prepared_candidate_accepts_only_exact_asset_overlay_append() -> None:
    asset, overlay_id = storage_module._require_prepared_candidate(
        _candidate(),
        _summary(),
        source_document=_snapshot().document,
        cleanup_disposition=SignatureAssetCleanupDisposition.NONE,
    )

    assert asset == _asset()
    assert overlay_id == OVERLAY_ID


def test_prepared_candidate_rejects_unrelated_manifest_mutation() -> None:
    candidate = _candidate()
    candidate["updated_at"] = "2026-08-01T10:31:00Z"

    error = _assert_signature_error(
        SignatureImageErrorCode.CASE_CHANGED,
        lambda: storage_module._require_prepared_candidate(
            candidate,
            _summary(),
            source_document=_snapshot().document,
            cleanup_disposition=SignatureAssetCleanupDisposition.NONE,
        ),
    )

    assert error.context == "signature.candidate_scope"


def test_prepared_candidate_rejects_cleanup_intent_on_create() -> None:
    _assert_signature_error(
        SignatureImageErrorCode.CASE_CHANGED,
        lambda: storage_module._require_prepared_candidate(
            _candidate(),
            _summary(),
            source_document=_snapshot().document,
            cleanup_disposition=SignatureAssetCleanupDisposition.KEPT_BY_POLICY,
        ),
    )


@pytest.mark.parametrize(
    ("precheck_code", "expected"),
    (
        (
            ImportPrecheckErrorCode.SOURCE_LOCATION_UNSUPPORTED,
            SignatureImageErrorCode.SOURCE_PATH_INVALID,
        ),
        (ImportPrecheckErrorCode.EXTENSION_MISMATCH, SignatureImageErrorCode.MEDIA_UNSUPPORTED),
        (
            ImportPrecheckErrorCode.IMAGE_PIXEL_LIMIT_EXCEEDED,
            SignatureImageErrorCode.LIMIT_EXCEEDED,
        ),
        (ImportPrecheckErrorCode.HEADER_MALFORMED, SignatureImageErrorCode.MALFORMED),
        (ImportPrecheckErrorCode.SOURCE_READ_FAILED, SignatureImageErrorCode.SOURCE_UNAVAILABLE),
    ),
)
def test_precheck_errors_map_to_closed_signature_errors(
    precheck_code: ImportPrecheckErrorCode,
    expected: SignatureImageErrorCode,
) -> None:
    source = ImportPrecheckError(
        precheck_code,
        ImportPreliminaryState.SOURCE_UNAVAILABLE,
        "source.synthetic",
        "Synthetic lower-layer detail.",
    )

    error = _assert_signature_error(expected, lambda: storage_module._map_precheck_error(source))

    assert "Synthetic lower-layer detail" not in str(error)


def test_original_reference_check_covers_sources_and_assets() -> None:
    document = _document()
    source_name = cast(list[dict[str, object]], document["sources"])[0]["storage_name"]
    asset_name = cast(list[dict[str, object]], document["assets"])[0]["storage_name"]
    assert type(source_name) is str
    assert type(asset_name) is str

    assert storage_module._manifest_references_original(document, source_name)
    assert storage_module._manifest_references_original(document, asset_name)
    assert not storage_module._manifest_references_original(document, f"{ASSET_ID}.bin")


def test_identifier_inventory_reserves_nested_group_and_export_ids() -> None:
    document = _document()
    export_history = cast(dict[str, object], document["export_history"])
    export_record = cast(list[dict[str, object]], export_history["entries"])[0]
    export_id = UUID(cast(str, export_record["export_id"]))

    identifiers = storage_module._manifest_identifiers(document)

    assert UUID("90000000-0000-4000-8000-000000000001") in identifiers
    assert export_id in identifiers

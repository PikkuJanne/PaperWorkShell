"""Focused unit evidence for pure PWS-0305 signature-image manifest commands."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

import paperworkshell.overlays.signature_commands as signature_module
from paperworkshell.domain.manifest_repository import (
    MAX_SAFE_MANIFEST_INTEGER,
    ManifestSnapshot,
)
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.overlays.models import (
    OverlayAssetKind,
    OverlayAssetMediaType,
    OverlayAssetRecord,
    OverlayCollection,
    OverlayError,
    OverlayErrorCode,
    OverlayRecord,
    SignatureImageOverlayPayload,
)
from paperworkshell.overlays.signature_commands import (
    CreateSignatureImageCommand,
    DeleteSignatureImageCommand,
    SignatureAssetCleanupDisposition,
    SignatureAssetDeletePolicy,
    apply_signature_image_command,
    create_signature_image_candidate,
    delete_signature_image_candidate,
)

ROOT = Path(__file__).resolve().parents[2]
REPRESENTATIVE = ROOT / "schemas" / "examples" / "case-manifest-v2-representative.json"
CASE_ID = UUID("10000000-0000-4000-8000-000000000002")
PAGE_ONE = UUID("30000000-0000-4000-8000-000000000001")
PAGE_TWO_INACTIVE = UUID("30000000-0000-4000-8000-000000000002")
PAGE_THREE = UUID("30000000-0000-4000-8000-000000000003")
EXISTING_ASSET_ID = UUID("40000000-0000-4000-8000-000000000001")
EXISTING_SIGNATURE_ID = UUID("60000000-0000-4000-8000-000000000002")
ASSET_ID = UUID("4f000000-0000-4000-8000-000000000501")
OVERLAY_ID = UUID("6f000000-0000-4000-8000-000000000501")
BOX = NormalizedPageBox(125_000, 240_000, 300_000, 90_000)


def _document() -> dict[str, object]:
    loaded = json.loads(REPRESENTATIVE.read_bytes())
    assert type(loaded) is dict
    return cast(dict[str, object], loaded)


def _snapshot(document: dict[str, object] | None = None) -> ManifestSnapshot:
    selected = _document() if document is None else document
    case_id = selected["case_id"]
    revision = selected["revision"]
    schema_version = selected["schema_version"]
    assert type(case_id) is str
    assert type(revision) is int
    assert type(schema_version) is int
    return ManifestSnapshot(
        UUID(case_id),
        schema_version,
        revision,
        selected,
        "d" * 64,
        "signature-command-unit-token",
    )


def _asset(asset_id: UUID = ASSET_ID) -> OverlayAssetRecord:
    return OverlayAssetRecord(
        asset_id,
        OverlayAssetKind.SIGNATURE_IMAGE,
        f"{asset_id}.bin",
        OverlayAssetMediaType.PNG,
        321,
        "e" * 64,
    )


def _create_command(
    *,
    asset: OverlayAssetRecord | None = None,
    overlay_id: UUID = OVERLAY_ID,
    page_id: UUID = PAGE_ONE,
    revision: int = 7,
    case_id: UUID = CASE_ID,
) -> CreateSignatureImageCommand:
    return CreateSignatureImageCommand(
        case_id,
        revision,
        _asset() if asset is None else asset,
        overlay_id,
        page_id,
        BOX,
    )


def _assert_error(code: OverlayErrorCode, action: Callable[[], object]) -> OverlayError:
    with pytest.raises(OverlayError) as raised:
        action()
    assert raised.value.code is code
    return raised.value


def _collection(document: dict[str, object]) -> OverlayCollection:
    return OverlayCollection.from_manifest_document(document)


def test_create_appends_typed_asset_and_visual_mark_on_active_page() -> None:
    snapshot = _snapshot()
    before = snapshot.document
    result = apply_signature_image_command(snapshot, _create_command())
    candidate = result.candidate_document
    collection = _collection(candidate)

    assert collection.assets[-1] == _asset()
    created = collection.overlays[-1]
    assert created == OverlayRecord(
        OVERLAY_ID,
        PAGE_ONE,
        BOX,
        5,
        SignatureImageOverlayPayload(ASSET_ID),
    )
    assert created.payload.to_manifest() == {
        "kind": "signature_image",
        "asset_id": str(ASSET_ID),
        "terminology": "visual_mark_not_digital_signature",
    }
    assert result.cleanup_intent.disposition is SignatureAssetCleanupDisposition.NONE
    assert not result.cleanup_intent.requires_file_removal
    assert snapshot.document == before
    assert {
        key: value for key, value in candidate.items() if key not in {"assets", "overlays"}
    } == {key: value for key, value in before.items() if key not in {"assets", "overlays"}}

    cast(list[object], candidate["assets"]).clear()
    assert len(cast(list[object], result.candidate_document["assets"])) == 2


@pytest.mark.parametrize(
    ("policy", "expected_disposition", "asset_remains"),
    (
        (
            SignatureAssetDeletePolicy.KEEP_ASSET,
            SignatureAssetCleanupDisposition.KEPT_BY_POLICY,
            True,
        ),
        (
            SignatureAssetDeletePolicy.REMOVE_IF_UNUSED,
            SignatureAssetCleanupDisposition.REMOVE_UNUSED,
            False,
        ),
    ),
)
def test_delete_policy_removes_exact_placement_and_derives_cleanup(
    policy: SignatureAssetDeletePolicy,
    expected_disposition: SignatureAssetCleanupDisposition,
    asset_remains: bool,
) -> None:
    snapshot = _snapshot()
    result = delete_signature_image_candidate(
        snapshot,
        DeleteSignatureImageCommand(
            CASE_ID,
            snapshot.revision,
            EXISTING_SIGNATURE_ID,
            PAGE_ONE,
            policy,
        ),
    )
    collection = _collection(result.candidate_document)

    assert EXISTING_SIGNATURE_ID not in {record.overlay_id for record in collection.overlays}
    assert (EXISTING_ASSET_ID in {asset.asset_id for asset in collection.assets}) is asset_remains
    assert result.cleanup_intent.disposition is expected_disposition
    assert result.cleanup_intent.requires_file_removal is (not asset_remains)
    if asset_remains:
        assert result.cleanup_intent.asset is None
    else:
        assert result.cleanup_intent.asset is not None
        assert result.cleanup_intent.asset.asset_id == EXISTING_ASSET_ID


def test_remove_if_unused_retains_asset_referenced_on_another_page() -> None:
    document = _document()
    overlays = cast(list[dict[str, object]], document["overlays"])
    shared_id = UUID("6f000000-0000-4000-8000-000000000502")
    overlays.append(
        OverlayRecord(
            shared_id,
            PAGE_THREE,
            BOX,
            3,
            SignatureImageOverlayPayload(EXISTING_ASSET_ID),
        ).to_manifest()
    )
    snapshot = _snapshot(document)
    result = delete_signature_image_candidate(
        snapshot,
        DeleteSignatureImageCommand(
            CASE_ID,
            snapshot.revision,
            EXISTING_SIGNATURE_ID,
            PAGE_ONE,
            SignatureAssetDeletePolicy.REMOVE_IF_UNUSED,
        ),
    )
    collection = _collection(result.candidate_document)

    assert shared_id in {record.overlay_id for record in collection.overlays}
    assert EXISTING_ASSET_ID in {asset.asset_id for asset in collection.assets}
    assert result.cleanup_intent.disposition is SignatureAssetCleanupDisposition.RETAINED_SHARED
    assert not result.cleanup_intent.requires_file_removal


def test_exact_binding_active_page_kind_and_page_are_enforced() -> None:
    snapshot = _snapshot()
    _assert_error(
        OverlayErrorCode.CONCURRENT_CHANGE,
        lambda: create_signature_image_candidate(snapshot, _create_command(revision=8)),
    )
    _assert_error(
        OverlayErrorCode.CONCURRENT_CHANGE,
        lambda: create_signature_image_candidate(
            snapshot,
            _create_command(case_id=UUID("1f000000-0000-4000-8000-000000000509")),
        ),
    )
    _assert_error(
        OverlayErrorCode.PAGE_UNKNOWN,
        lambda: create_signature_image_candidate(
            snapshot,
            _create_command(page_id=PAGE_TWO_INACTIVE),
        ),
    )
    _assert_error(
        OverlayErrorCode.CONCURRENT_CHANGE,
        lambda: delete_signature_image_candidate(
            snapshot,
            DeleteSignatureImageCommand(
                CASE_ID,
                snapshot.revision,
                EXISTING_SIGNATURE_ID,
                PAGE_THREE,
                SignatureAssetDeletePolicy.KEEP_ASSET,
            ),
        ),
    )
    _assert_error(
        OverlayErrorCode.EDIT_KIND_MISMATCH,
        lambda: delete_signature_image_candidate(
            snapshot,
            DeleteSignatureImageCommand(
                CASE_ID,
                snapshot.revision,
                UUID("60000000-0000-4000-8000-000000000001"),
                PAGE_ONE,
                SignatureAssetDeletePolicy.KEEP_ASSET,
            ),
        ),
    )


def test_create_reserves_complete_manifest_namespace() -> None:
    snapshot = _snapshot()
    source_id = UUID("20000000-0000-4000-8000-000000000001")
    assignment_id = UUID("70000000-0000-4000-8000-000000000001")

    _assert_error(
        OverlayErrorCode.IDENTIFIER_COLLISION,
        lambda: create_signature_image_candidate(
            snapshot,
            _create_command(asset=_asset(source_id)),
        ),
    )
    _assert_error(
        OverlayErrorCode.IDENTIFIER_COLLISION,
        lambda: create_signature_image_candidate(
            snapshot,
            _create_command(overlay_id=assignment_id),
        ),
    )


def test_create_enforces_asset_overlay_and_z_index_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    monkeypatch.setattr(signature_module, "MAX_OVERLAY_ASSETS", 1)
    _assert_error(
        OverlayErrorCode.CAPACITY_EXCEEDED,
        lambda: create_signature_image_candidate(snapshot, _create_command()),
    )
    monkeypatch.setattr(signature_module, "MAX_OVERLAY_ASSETS", 4_096)
    monkeypatch.setattr(signature_module, "MAX_OVERLAYS", 8)
    _assert_error(
        OverlayErrorCode.CAPACITY_EXCEEDED,
        lambda: create_signature_image_candidate(snapshot, _create_command()),
    )

    document = _document()
    overlays = cast(list[dict[str, object]], document["overlays"])
    overlays[4]["z_index"] = MAX_SAFE_MANIFEST_INTEGER
    monkeypatch.setattr(signature_module, "MAX_OVERLAYS", 4_096)
    _assert_error(
        OverlayErrorCode.Z_INDEX_INVALID,
        lambda: create_signature_image_candidate(_snapshot(document), _create_command()),
    )


def test_command_models_reject_wrong_types_and_cross_identifier_collisions() -> None:
    _assert_error(
        OverlayErrorCode.MODEL_INVALID,
        lambda: apply_signature_image_command(
            _snapshot(), cast(CreateSignatureImageCommand, "create")
        ),
    )
    _assert_error(
        OverlayErrorCode.IDENTIFIER_COLLISION,
        lambda: _create_command(overlay_id=ASSET_ID),
    )
    _assert_error(
        OverlayErrorCode.MODEL_INVALID,
        lambda: DeleteSignatureImageCommand(
            CASE_ID,
            7,
            EXISTING_SIGNATURE_ID,
            PAGE_ONE,
            cast(SignatureAssetDeletePolicy, "remove_if_unused"),
        ),
    )

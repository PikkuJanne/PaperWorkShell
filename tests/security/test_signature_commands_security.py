"""Privacy and fail-closed evidence for PWS-0305 signature-image commands."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.overlays.models import (
    OverlayAssetKind,
    OverlayAssetMediaType,
    OverlayAssetRecord,
    OverlayError,
    OverlayErrorCode,
)
from paperworkshell.overlays.signature_commands import (
    CreateSignatureImageCommand,
    DeleteSignatureImageCommand,
    SignatureAssetCleanupDisposition,
    SignatureAssetCleanupIntent,
    SignatureAssetDeletePolicy,
    create_signature_image_candidate,
    delete_signature_image_candidate,
)

ROOT = Path(__file__).resolve().parents[2]
REPRESENTATIVE = ROOT / "schemas" / "examples" / "case-manifest-v2-representative.json"
PRIVATE_CANARY = "SYNTHETIC-PRIVATE-SIGNATURE-C:/private/applicant.png"
CASE_ID = UUID("10000000-0000-4000-8000-000000000002")
PAGE_ID = UUID("30000000-0000-4000-8000-000000000001")
ASSET_ID = UUID("4f000000-0000-4000-8000-000000000601")
OVERLAY_ID = UUID("6f000000-0000-4000-8000-000000000601")
EXISTING_ASSET_ID = UUID("40000000-0000-4000-8000-000000000001")
EXISTING_OVERLAY_ID = UUID("60000000-0000-4000-8000-000000000002")
PRIVATE_DIGEST = "abcdef0123456789" * 4
BOX = NormalizedPageBox(100_000, 100_000, 300_000, 75_000)


def _document() -> dict[str, object]:
    loaded = json.loads(REPRESENTATIVE.read_bytes())
    assert type(loaded) is dict
    return cast(dict[str, object], loaded)


def _snapshot(document: dict[str, object] | None = None) -> ManifestSnapshot:
    selected = _document() if document is None else document
    case_id = selected["case_id"]
    revision = selected["revision"]
    assert type(case_id) is str
    assert type(revision) is int
    return ManifestSnapshot(
        UUID(case_id),
        2,
        revision,
        selected,
        "f" * 64,
        "signature-command-security-token",
    )


def _asset() -> OverlayAssetRecord:
    return OverlayAssetRecord(
        ASSET_ID,
        OverlayAssetKind.SIGNATURE_IMAGE,
        f"{ASSET_ID}.bin",
        OverlayAssetMediaType.PNG,
        512,
        PRIVATE_DIGEST,
    )


def _create(snapshot: ManifestSnapshot) -> CreateSignatureImageCommand:
    return CreateSignatureImageCommand(
        CASE_ID,
        snapshot.revision,
        _asset(),
        OVERLAY_ID,
        PAGE_ID,
        BOX,
    )


def _assert_error(code: OverlayErrorCode, action: Callable[[], object]) -> OverlayError:
    with pytest.raises(OverlayError) as raised:
        action()
    assert raised.value.code is code
    return raised.value


def test_asset_commands_changes_cleanup_errors_and_logs_hide_private_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.clear()
    document = _document()
    document["synthetic_test_canary"] = PRIVATE_CANARY
    snapshot = _snapshot(document)
    command = _create(snapshot)
    created = create_signature_image_candidate(snapshot, command)
    deleted = delete_signature_image_candidate(
        _snapshot(),
        DeleteSignatureImageCommand(
            CASE_ID,
            7,
            EXISTING_OVERLAY_ID,
            PAGE_ID,
            SignatureAssetDeletePolicy.REMOVE_IF_UNUSED,
        ),
    )
    stale_error = _assert_error(
        OverlayErrorCode.CONCURRENT_CHANGE,
        lambda: create_signature_image_candidate(
            snapshot,
            CreateSignatureImageCommand(
                CASE_ID,
                snapshot.revision + 1,
                _asset(),
                OVERLAY_ID,
                PAGE_ID,
                BOX,
            ),
        ),
    )

    rendered = " ".join(
        (
            repr(_asset()),
            repr(command),
            repr(created),
            repr(created.cleanup_intent),
            repr(deleted),
            repr(deleted.cleanup_intent),
            repr(deleted.cleanup_intent.asset),
            repr(stale_error),
            str(stale_error),
            repr(stale_error.args),
        )
    )
    for private_value in (
        PRIVATE_CANARY,
        PRIVATE_DIGEST,
        str(ASSET_ID),
        str(OVERLAY_ID),
        f"{ASSET_ID}.bin",
        str(EXISTING_ASSET_ID),
        "applicant.png",
    ):
        assert private_value not in rendered
        assert private_value not in caplog.text
    assert not caplog.records


def test_malformed_snapshot_identity_and_collections_fail_closed_without_mutation() -> None:
    identity_snapshot = _snapshot()
    identity_document = cast(
        dict[str, object], object.__getattribute__(identity_snapshot, "_document")
    )
    identity_document["case_id"] = "1f000000-0000-4000-8000-000000000699"
    identity_before = identity_snapshot.document
    identity_error = _assert_error(
        OverlayErrorCode.MANIFEST_INVALID,
        lambda: create_signature_image_candidate(identity_snapshot, _create(identity_snapshot)),
    )
    assert identity_snapshot.document == identity_before

    malformed_document = _document()
    malformed_document["assets"] = [PRIVATE_CANARY]
    malformed_snapshot = _snapshot(malformed_document)
    malformed_before = malformed_snapshot.document
    malformed_error = _assert_error(
        OverlayErrorCode.MANIFEST_INVALID,
        lambda: create_signature_image_candidate(malformed_snapshot, _create(malformed_snapshot)),
    )
    assert malformed_snapshot.document == malformed_before

    rendered = (
        f"{identity_error!r} {identity_error} {identity_error.args!r} "
        f"{malformed_error!r} {malformed_error} {malformed_error.args!r}"
    )
    assert PRIVATE_CANARY not in rendered
    assert "1f000000-0000-4000-8000-000000000699" not in rendered


def test_cleanup_intent_and_delete_policy_cannot_be_forged() -> None:
    _assert_error(
        OverlayErrorCode.MODEL_INVALID,
        lambda: SignatureAssetCleanupIntent(
            SignatureAssetCleanupDisposition.KEPT_BY_POLICY,
            _asset(),
        ),
    )
    _assert_error(
        OverlayErrorCode.MODEL_INVALID,
        lambda: SignatureAssetCleanupIntent(
            SignatureAssetCleanupDisposition.REMOVE_UNUSED,
        ),
    )
    _assert_error(
        OverlayErrorCode.MODEL_INVALID,
        lambda: DeleteSignatureImageCommand(
            CASE_ID,
            7,
            EXISTING_OVERLAY_ID,
            PAGE_ID,
            cast(SignatureAssetDeletePolicy, "remove_if_unused"),
        ),
    )

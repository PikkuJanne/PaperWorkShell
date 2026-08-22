"""Hostile-input and privacy evidence for the pure PWS-0302 overlay model."""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterator, Mapping
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.overlays.models import (
    ORDINARY_SHAPE_INTENT,
    SIGNATURE_IMAGE_TERMINOLOGY,
    MarkOverlayPayload,
    OverlayAssetKind,
    OverlayAssetMediaType,
    OverlayAssetRecord,
    OverlayCollection,
    OverlayError,
    OverlayErrorCode,
    OverlayKind,
    OverlayPayload,
    OverlayRecord,
    RgbaColor,
    ShapeOverlayPayload,
    ShapePrimitive,
    SignatureImageOverlayPayload,
    TextualOverlayPayload,
    overlay_payload_from_manifest,
)

PAGE_ID = UUID("30000000-0000-4000-8000-000000000301")
OVERLAY_ID = UUID("60000000-0000-4000-8000-000000000301")
ASSET_ID = UUID("40000000-0000-4000-8000-000000000301")
PRIVATE_CANARY = "SYNTHETIC-PRIVATE-OVERLAY-CANARY"


class _HostileMapping(Mapping[str, object]):
    def __iter__(self) -> Iterator[str]:
        raise RuntimeError(PRIVATE_CANARY)

    def __len__(self) -> int:
        return 1

    def __getitem__(self, _key: str) -> object:
        raise RuntimeError(PRIVATE_CANARY)


class _HostileEquality:
    def __eq__(self, _other: object) -> bool:
        raise RuntimeError(PRIVATE_CANARY)

    def __ne__(self, _other: object) -> bool:
        raise RuntimeError(PRIVATE_CANARY)


def _asset() -> OverlayAssetRecord:
    return OverlayAssetRecord(
        ASSET_ID,
        OverlayAssetKind.SIGNATURE_IMAGE,
        f"{ASSET_ID}.bin",
        OverlayAssetMediaType.PNG,
        1_024,
        "a" * 64,
    )


def _text_payload(value: str = "SYNTHETIC VALUE") -> TextualOverlayPayload:
    return TextualOverlayPayload(
        OverlayKind.TEXT,
        value,
        "paperworkshell-default",
        12,
        RgbaColor("#102030ff"),
    )


def _record(payload: OverlayPayload | None = None) -> OverlayRecord:
    selected = _text_payload() if payload is None else payload
    return OverlayRecord(
        OVERLAY_ID,
        PAGE_ID,
        NormalizedPageBox(100_000, 200_000, 300_000, 100_000),
        7,
        selected,
    )


def _text_manifest(value: str = "SYNTHETIC VALUE") -> dict[str, object]:
    return {
        "kind": "text",
        "value": value,
        "font_token": "paperworkshell-default",
        "font_size_points": 12,
        "color": "#102030ff",
    }


def _signature_manifest() -> dict[str, object]:
    return {
        "kind": "signature_image",
        "asset_id": str(ASSET_ID),
        "terminology": SIGNATURE_IMAGE_TERMINOLOGY,
    }


def _shape_manifest() -> dict[str, object]:
    return {
        "kind": "shape",
        "shape": "rectangle",
        "fill_color": "#000000ff",
        "stroke_color": "#ffffffff",
        "intent": ORDINARY_SHAPE_INTENT,
    }


def _record_manifest(payload: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "overlay_id": str(OVERLAY_ID),
        "overlay_version": 1,
        "page_id": str(PAGE_ID),
        "box": {
            "coordinate_space": "normalized_page_millionths_v1",
            "x": 100_000,
            "y": 200_000,
            "width": 300_000,
            "height": 100_000,
        },
        "z_index": 7,
        "payload": _text_manifest() if payload is None else payload,
    }


def _assert_error(
    code: OverlayErrorCode,
    action: Callable[[], object],
) -> OverlayError:
    with pytest.raises(OverlayError) as raised:
        action()
    assert raised.value.code is code
    return raised.value


@pytest.mark.parametrize(
    ("payload_factory", "field"),
    [
        (_text_manifest, "script"),
        (_text_manifest, "command"),
        (_text_manifest, "font_path"),
        (_text_manifest, "uri"),
        (_signature_manifest, "asset_path"),
        (_signature_manifest, "is_digitally_signed"),
        (_signature_manifest, "cryptographic_status"),
        (_signature_manifest, "certificate"),
        (_signature_manifest, "qualified_signature"),
        (_shape_manifest, "redaction"),
        (_shape_manifest, "redaction_state"),
        (_shape_manifest, "permanent"),
        (_shape_manifest, "removes_content"),
    ],
)
def test_closed_payloads_reject_claim_executable_and_path_fields(
    payload_factory: Callable[[], dict[str, object]],
    field: str,
) -> None:
    payload = payload_factory()
    payload[field] = PRIVATE_CANARY
    before = copy.deepcopy(payload)

    error = _assert_error(
        OverlayErrorCode.MANIFEST_INVALID,
        lambda: overlay_payload_from_manifest(payload),
    )

    assert payload == before
    rendered = f"{error!r} {error} {error.args!r}"
    assert field not in rendered
    assert PRIVATE_CANARY not in rendered


@pytest.mark.parametrize("kind", ["digital_signature", "qualified_signature", "redaction"])
def test_unsupported_signature_and_removal_kinds_fail_closed(kind: str) -> None:
    error = _assert_error(
        OverlayErrorCode.PAYLOAD_UNSUPPORTED,
        lambda: overlay_payload_from_manifest({"kind": kind, "value": PRIVATE_CANARY}),
    )

    assert kind not in f"{error!r} {error} {error.args!r}"


@pytest.mark.parametrize(
    "claim",
    [
        "digital_signature",
        "qualified_electronic_signature",
        "cryptographically_signed",
    ],
)
def test_signature_image_terminology_is_fixed(claim: str) -> None:
    direct = _assert_error(
        OverlayErrorCode.SIGNATURE_TERMINOLOGY_INVALID,
        lambda: SignatureImageOverlayPayload(ASSET_ID, claim),
    )
    manifest = _signature_manifest()
    manifest["terminology"] = claim
    decoded = _assert_error(
        OverlayErrorCode.SIGNATURE_TERMINOLOGY_INVALID,
        lambda: SignatureImageOverlayPayload.from_manifest(manifest),
    )

    assert claim not in f"{direct!r} {direct} {decoded!r} {decoded}"
    valid = SignatureImageOverlayPayload(ASSET_ID)
    assert valid.to_manifest() == _signature_manifest()


@pytest.mark.parametrize(
    "claim",
    ["permanent_redaction", "secure_removal", "underlying_content_removed"],
)
def test_ordinary_shape_intent_is_fixed(claim: str) -> None:
    direct = _assert_error(
        OverlayErrorCode.SHAPE_INTENT_INVALID,
        lambda: ShapeOverlayPayload(
            OverlayKind.SHAPE,
            ShapePrimitive.RECTANGLE,
            RgbaColor("#000000ff"),
            RgbaColor("#ffffffff"),
            claim,
        ),
    )
    manifest = _shape_manifest()
    manifest["intent"] = claim
    decoded = _assert_error(
        OverlayErrorCode.SHAPE_INTENT_INVALID,
        lambda: ShapeOverlayPayload.from_manifest(manifest),
    )

    assert claim not in f"{direct!r} {direct} {decoded!r} {decoded}"
    valid = ShapeOverlayPayload(
        OverlayKind.SHAPE,
        ShapePrimitive.RECTANGLE,
        RgbaColor("#000000ff"),
        RgbaColor("#ffffffff"),
    )
    assert valid.to_manifest() == _shape_manifest()


def test_shell_path_and_claim_looking_user_text_remains_inert_data() -> None:
    value = (
        "$(synthetic-command); C:\\synthetic\\form.pdf | https://invalid.example/; "
        "digital signature; permanent redaction\n<not-executable>"
    )
    manifest = _text_manifest(value)

    payload = TextualOverlayPayload.from_manifest(manifest)

    assert payload.value == value
    assert payload.to_manifest() == manifest


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ({"font_token": "../../private-font.ttf"}, OverlayErrorCode.FONT_INVALID),
        ({"font_size_points": True}, OverlayErrorCode.FONT_SIZE_INVALID),
        ({"font_size_points": 12.0}, OverlayErrorCode.FONT_SIZE_INVALID),
        ({"color": "#000000ff\n"}, OverlayErrorCode.COLOR_INVALID),
        ({"value": object()}, OverlayErrorCode.MANIFEST_INVALID),
    ],
)
def test_malformed_text_values_raise_only_closed_errors(
    mutation: dict[str, object],
    expected_code: OverlayErrorCode,
) -> None:
    manifest = _text_manifest()
    manifest.update(mutation)

    error = _assert_error(expected_code, lambda: TextualOverlayPayload.from_manifest(manifest))

    assert PRIVATE_CANARY not in f"{error!r} {error} {error.args!r}"


def test_path_instead_of_case_internal_asset_id_is_rejected() -> None:
    payload = _signature_manifest()
    payload["asset_id"] = "C:\\synthetic\\signature.png"

    error = _assert_error(
        OverlayErrorCode.IDENTIFIER_INVALID,
        lambda: SignatureImageOverlayPayload.from_manifest(payload),
    )

    assert "signature.png" not in f"{error!r} {error} {error.args!r}"


def test_malformed_record_box_and_numeric_values_fail_closed() -> None:
    invalid_box = _record_manifest()
    box = invalid_box["box"]
    assert type(box) is dict
    box["width"] = 900_001
    _assert_error(
        OverlayErrorCode.BOX_INVALID,
        lambda: OverlayRecord.from_manifest(invalid_box),
    )

    invalid_z = _record_manifest()
    invalid_z["z_index"] = True
    _assert_error(
        OverlayErrorCode.Z_INDEX_INVALID,
        lambda: OverlayRecord.from_manifest(invalid_z),
    )


def test_hostile_mapping_failure_is_sanitized_unchained_and_unlogged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.clear()

    error = _assert_error(
        OverlayErrorCode.MANIFEST_INVALID,
        lambda: overlay_payload_from_manifest(_HostileMapping()),
    )

    rendered = f"{error!r} {error} {error.args!r}"
    assert PRIVATE_CANARY not in rendered
    assert error.__cause__ is None
    assert error.__context__ is None
    assert PRIVATE_CANARY not in caplog.text
    assert not caplog.records


@pytest.mark.parametrize("target", ["signature", "shape"])
def test_hostile_fixed_metadata_values_raise_closed_overlay_errors(target: str) -> None:
    hostile = _HostileEquality()
    action: Callable[[], object]
    if target == "signature":
        action = lambda: SignatureImageOverlayPayload(  # noqa: E731
            ASSET_ID,
            cast(str, hostile),
        )
        expected = OverlayErrorCode.SIGNATURE_TERMINOLOGY_INVALID
    else:
        action = lambda: ShapeOverlayPayload(  # noqa: E731
            OverlayKind.SHAPE,
            ShapePrimitive.RECTANGLE,
            RgbaColor("#000000ff"),
            RgbaColor("#ffffffff"),
            cast(str, hostile),
        )
        expected = OverlayErrorCode.SHAPE_INTENT_INVALID

    error = _assert_error(expected, action)

    assert PRIVATE_CANARY not in f"{error!r} {error} {error.args!r}"
    assert error.__cause__ is None
    assert error.__context__ is None


def test_value_bearing_model_reprs_and_logs_hide_private_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.clear()
    payload = _text_payload(PRIVATE_CANARY)
    record = _record(payload)
    asset = _asset()
    collection = OverlayCollection(
        [asset],
        [record],
        known_page_ids=[PAGE_ID],
        active_page_ids=[PAGE_ID],
    )
    projection = collection.visible_stack(
        PAGE_ID,
        active_crop=NormalizedPageBox.full_page(),
    )[0]

    rendered = " ".join(
        repr(value)
        for value in (
            payload,
            record,
            asset,
            collection,
            projection,
            collection.assets,
            collection.overlays,
        )
    )
    for prohibited in (
        PRIVATE_CANARY,
        str(PAGE_ID),
        str(OVERLAY_ID),
        str(ASSET_ID),
        f"{ASSET_ID}.bin",
        "a" * 64,
        "paperworkshell-default",
        "#102030ff",
    ):
        assert prohibited not in rendered
        assert prohibited not in caplog.text
    assert not caplog.records


def test_successful_parse_and_serialization_defensively_copy_nested_records() -> None:
    manifest = _record_manifest(_text_manifest(PRIVATE_CANARY))
    before = copy.deepcopy(manifest)

    record = OverlayRecord.from_manifest(manifest)

    assert manifest == before
    box = manifest["box"]
    payload = manifest["payload"]
    assert type(box) is dict and type(payload) is dict
    box["x"] = 0
    payload["value"] = "CHANGED CALLER VALUE"
    assert record.to_manifest() == before

    rendered = record.to_manifest()
    rendered_box = rendered["box"]
    rendered_payload = rendered["payload"]
    assert type(rendered_box) is dict and type(rendered_payload) is dict
    rendered_box["x"] = 999_999
    rendered_payload["value"] = "CHANGED SERIALIZED VALUE"
    assert record.to_manifest() == before


def test_failed_parse_does_not_mutate_caller_mapping() -> None:
    payload = _signature_manifest()
    payload["script"] = PRIVATE_CANARY
    before = copy.deepcopy(payload)

    _assert_error(
        OverlayErrorCode.MANIFEST_INVALID,
        lambda: SignatureImageOverlayPayload.from_manifest(payload),
    )

    assert payload == before


def test_collection_copies_caller_sequences_and_returns_fresh_manifest_trees() -> None:
    assets = [_asset()]
    overlays = [_record(SignatureImageOverlayPayload(ASSET_ID))]
    collection = OverlayCollection(
        assets,
        overlays,
        known_page_ids=[PAGE_ID],
        active_page_ids=[PAGE_ID],
    )

    assets.clear()
    overlays.clear()
    assert len(collection.assets) == 1
    assert len(collection.overlays) == 1

    first_assets = collection.to_manifest_assets()
    first_overlays = collection.to_manifest_overlays()
    first_assets[0]["sha256"] = PRIVATE_CANARY
    first_payload = first_overlays[0]["payload"]
    assert type(first_payload) is dict
    first_payload["terminology"] = PRIVATE_CANARY

    assert collection.to_manifest_assets()[0]["sha256"] == "a" * 64
    assert (
        collection.to_manifest_overlays()[0]["payload"]
        == SignatureImageOverlayPayload(ASSET_ID).to_manifest()
    )


def test_mark_payload_rejects_executable_extension_without_mutation() -> None:
    manifest: dict[str, object] = {
        "kind": "checkmark",
        "color": "#000000ff",
        "command": PRIVATE_CANARY,
    }
    before = copy.deepcopy(manifest)

    _assert_error(
        OverlayErrorCode.MANIFEST_INVALID,
        lambda: MarkOverlayPayload.from_manifest(manifest),
    )

    assert manifest == before

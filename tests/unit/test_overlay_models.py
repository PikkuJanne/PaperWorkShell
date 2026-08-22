"""Focused unit evidence for the pure visual-overlay domain model."""

from __future__ import annotations

import copy
import json
import random
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.domain.manifest_repository import MAX_SAFE_MANIFEST_INTEGER
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.overlays.models import (
    DEFAULT_FONT_TOKEN,
    MAX_FONT_SIZE_POINTS,
    MAX_FONT_TOKEN_CODE_POINTS,
    MAX_OVERLAY_ASSETS,
    MAX_OVERLAY_TEXT_CODE_POINTS,
    MAX_OVERLAYS,
    MIN_FONT_SIZE_POINTS,
    ORDINARY_SHAPE_INTENT,
    OVERLAY_VERSION,
    SIGNATURE_IMAGE_TERMINOLOGY,
    MarkOverlayPayload,
    OverlayAssetKind,
    OverlayAssetMediaType,
    OverlayAssetRecord,
    OverlayCollection,
    OverlayError,
    OverlayErrorCode,
    OverlayKind,
    OverlayProjection,
    OverlayRecord,
    OverlayVisibility,
    RgbaColor,
    SignatureImageOverlayPayload,
    TextualOverlayPayload,
    overlay_payload_from_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
REPRESENTATIVE_MANIFEST = ROOT / "schemas" / "examples" / "case-manifest-v2-representative.json"
PAYLOAD_CASE_FIXTURE = ROOT / "tests" / "fixtures" / "overlays" / "overlay-v1-payload-cases.json"

PAGE_A = UUID("30000000-0000-4000-8000-000000000001")
PAGE_B = UUID("30000000-0000-4000-8000-000000000002")
PAGE_UNKNOWN = UUID("30000000-0000-4000-8000-000000000099")
ASSET_A = UUID("40000000-0000-4000-8000-000000000001")

FULL_PAGE = NormalizedPageBox.full_page()
DEFAULT_BOX = NormalizedPageBox(100_000, 100_000, 300_000, 100_000)


def _overlay_id(index: int) -> UUID:
    return UUID(f"60000000-0000-4000-8000-{index:012x}")


def _asset_wire(
    *,
    asset_id: UUID = ASSET_A,
    media_type: str = "image/png",
    byte_size: object = 1_024,
) -> dict[str, object]:
    return {
        "asset_id": str(asset_id),
        "kind": "signature_image",
        "storage_name": f"{asset_id}.bin",
        "media_type": media_type,
        "byte_size": byte_size,
        "sha256": "c" * 64,
    }


def _asset_model(*, byte_size: int = 1_024) -> OverlayAssetRecord:
    return OverlayAssetRecord(
        ASSET_A,
        OverlayAssetKind.SIGNATURE_IMAGE,
        f"{ASSET_A}.bin",
        OverlayAssetMediaType.PNG,
        byte_size,
        "c" * 64,
    )


def _mark_payload(kind: OverlayKind = OverlayKind.CHECKMARK) -> MarkOverlayPayload:
    return MarkOverlayPayload(kind, RgbaColor("#102030ff"))


def _model_record(
    index: int,
    *,
    page_id: UUID = PAGE_A,
    box: NormalizedPageBox = DEFAULT_BOX,
    z_index: int | None = None,
) -> OverlayRecord:
    return OverlayRecord(
        _overlay_id(index),
        page_id,
        box,
        index if z_index is None else z_index,
        _mark_payload(),
    )


def _record_wire(
    index: int,
    payload: dict[str, object],
    *,
    page_id: UUID = PAGE_A,
    box: NormalizedPageBox = DEFAULT_BOX,
    z_index: int | None = None,
) -> dict[str, object]:
    return {
        "overlay_id": str(_overlay_id(index)),
        "overlay_version": OVERLAY_VERSION,
        "page_id": str(page_id),
        "box": box.to_manifest(),
        "z_index": index if z_index is None else z_index,
        "payload": payload,
    }


def _document(
    overlays: list[dict[str, object]],
    *,
    assets: list[dict[str, object]] | None = None,
    page_ids: tuple[UUID, ...] = (PAGE_A, PAGE_B),
    active_page_ids: tuple[UUID, ...] = (PAGE_A,),
    schema_version: object = 2,
) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "pages": [{"page_id": str(page_id)} for page_id in page_ids],
        "page_sequence": [str(page_id) for page_id in active_page_ids],
        "assets": [] if assets is None else assets,
        "overlays": overlays,
    }


def _assert_error(code: OverlayErrorCode, call: Callable[[], object]) -> OverlayError:
    with pytest.raises(OverlayError) as raised:
        call()
    assert raised.value.code is code
    return raised.value


REPRESENTATIVE_PAYLOADS: tuple[dict[str, object], ...] = (
    {
        "kind": "text",
        "value": "SYNTHETIC TEXT",
        "font_token": DEFAULT_FONT_TOKEN,
        "font_size_points": 12,
        "color": "#A0b1C2d3",
    },
    {
        "kind": "date",
        "value": "31/12/2030",
        "font_token": DEFAULT_FONT_TOKEN,
        "font_size_points": 10,
        "color": "#000000ff",
    },
    {
        "kind": "checkmark",
        "color": "#008000ff",
    },
    {
        "kind": "cross",
        "color": "#800000ff",
    },
    {
        "kind": "initials",
        "value": "XY",
        "font_token": DEFAULT_FONT_TOKEN,
        "font_size_points": 14,
        "color": "#010203ff",
    },
    {
        "kind": "signature_image",
        "asset_id": str(ASSET_A),
        "terminology": SIGNATURE_IMAGE_TERMINOLOGY,
    },
    {
        "kind": "highlight",
        "shape": "rectangle",
        "fill_color": "#ffff0080",
        "stroke_color": "#ffff00ff",
        "intent": ORDINARY_SHAPE_INTENT,
    },
    {
        "kind": "shape",
        "shape": "ellipse",
        "fill_color": "#00000000",
        "stroke_color": "#112233ff",
        "intent": ORDINARY_SHAPE_INTENT,
    },
)


def test_checked_in_payload_fixture_covers_all_kinds_with_exact_positive_round_trips() -> None:
    loaded = json.loads(PAYLOAD_CASE_FIXTURE.read_text(encoding="utf-8"))
    assert type(loaded) is dict
    assert loaded.get("fixture_version") == 1
    positive = loaded.get("positive")
    assert type(positive) is list

    observed: list[OverlayKind] = []
    for raw_case in positive:
        assert type(raw_case) is dict
        payload = raw_case.get("payload")
        assert type(payload) is dict
        decoded = overlay_payload_from_manifest(payload)
        assert decoded.to_manifest() == payload
        observed.append(decoded.kind)

    assert observed == list(OverlayKind)


def test_checked_in_payload_fixture_negative_cases_return_the_recorded_closed_code() -> None:
    loaded = json.loads(PAYLOAD_CASE_FIXTURE.read_text(encoding="utf-8"))
    assert type(loaded) is dict
    negative = loaded.get("negative")
    assert type(negative) is list

    for raw_case in negative:
        assert type(raw_case) is dict
        payload = raw_case.get("payload")
        expected_code = raw_case.get("expected_code")
        assert type(payload) is dict
        assert type(expected_code) is str
        _assert_error(
            OverlayErrorCode(expected_code),
            partial(overlay_payload_from_manifest, payload),
        )


@pytest.mark.parametrize("payload", REPRESENTATIVE_PAYLOADS)
def test_all_eight_overlay_kinds_have_exact_representative_wire_round_trip(
    payload: dict[str, object],
) -> None:
    index = REPRESENTATIVE_PAYLOADS.index(payload) + 1
    wire = _record_wire(index, copy.deepcopy(payload), z_index=index - 1)

    record = OverlayRecord.from_manifest(wire)

    assert record.kind.value == payload["kind"]
    assert record.to_manifest() == wire
    assert record.to_manifest() is not wire
    assert record.to_manifest()["payload"] is not wire["payload"]


def test_all_overlay_kinds_form_one_valid_collection_and_preserve_wire_order() -> None:
    wires = [
        _record_wire(index, copy.deepcopy(payload), z_index=index - 1)
        for index, payload in enumerate(REPRESENTATIVE_PAYLOADS, start=1)
    ]
    records = [OverlayRecord.from_manifest(wire) for wire in wires]
    asset_wire = _asset_wire()
    asset = OverlayAssetRecord.from_manifest(asset_wire)

    collection = OverlayCollection(
        [asset],
        records,
        known_page_ids=[PAGE_A],
        active_page_ids=[PAGE_A],
    )

    assert collection.to_manifest_assets() == [asset_wire]
    assert collection.to_manifest_overlays() == wires
    assert tuple(record.kind for record in collection.stack_for_page(PAGE_A)) == tuple(
        OverlayKind(cast(str, payload["kind"])) for payload in REPRESENTATIVE_PAYLOADS
    )


def test_current_representative_manifest_overlay_surfaces_round_trip_exactly() -> None:
    loaded = json.loads(REPRESENTATIVE_MANIFEST.read_text(encoding="utf-8"))
    assert type(loaded) is dict
    document = cast(dict[str, object], loaded)
    assets = copy.deepcopy(document["assets"])
    overlays = copy.deepcopy(document["overlays"])

    collection = OverlayCollection.from_manifest_document(document)

    assert collection.to_manifest_assets() == assets
    assert collection.to_manifest_overlays() == overlays


@pytest.mark.parametrize("kind", [OverlayKind.TEXT, OverlayKind.DATE, OverlayKind.INITIALS])
@pytest.mark.parametrize("font_size", [MIN_FONT_SIZE_POINTS, MAX_FONT_SIZE_POINTS])
def test_textual_constructor_accepts_released_value_and_font_boundaries(
    kind: OverlayKind,
    font_size: int,
) -> None:
    value = "x" * MAX_OVERLAY_TEXT_CODE_POINTS
    font_token = "f" * MAX_FONT_TOKEN_CODE_POINTS

    payload = TextualOverlayPayload(
        kind,
        value,
        font_token,
        font_size,
        RgbaColor("#AAbbCCdd"),
    )

    assert payload.to_manifest() == {
        "kind": kind.value,
        "value": value,
        "font_token": font_token,
        "font_size_points": font_size,
        "color": "#AAbbCCdd",
    }


@pytest.mark.parametrize("value", ["", "bad token", "UPPER", "a" * 129, 1, None])
def test_invalid_font_tokens_are_rejected(value: object) -> None:
    _assert_error(
        OverlayErrorCode.FONT_INVALID,
        lambda: TextualOverlayPayload(
            OverlayKind.TEXT,
            "value",
            cast(str, value),
            12,
            RgbaColor("#000000ff"),
        ),
    )


@pytest.mark.parametrize("value", [0, MAX_FONT_SIZE_POINTS + 1, True, 12.5, "12"])
def test_invalid_font_sizes_are_rejected(value: object) -> None:
    _assert_error(
        OverlayErrorCode.FONT_SIZE_INVALID,
        lambda: TextualOverlayPayload(
            OverlayKind.TEXT,
            "value",
            DEFAULT_FONT_TOKEN,
            cast(int, value),
            RgbaColor("#000000ff"),
        ),
    )


def test_empty_text_is_valid_but_over_capacity_or_non_text_values_are_rejected() -> None:
    assert (
        TextualOverlayPayload(
            OverlayKind.TEXT,
            "",
            DEFAULT_FONT_TOKEN,
            12,
            RgbaColor("#000000ff"),
        ).value
        == ""
    )
    _assert_error(
        OverlayErrorCode.TEXT_INVALID,
        lambda: TextualOverlayPayload(
            OverlayKind.TEXT,
            "x" * (MAX_OVERLAY_TEXT_CODE_POINTS + 1),
            DEFAULT_FONT_TOKEN,
            12,
            RgbaColor("#000000ff"),
        ),
    )
    _assert_error(
        OverlayErrorCode.TEXT_INVALID,
        lambda: TextualOverlayPayload(
            OverlayKind.TEXT,
            cast(str, 12),
            DEFAULT_FONT_TOKEN,
            12,
            RgbaColor("#000000ff"),
        ),
    )


@pytest.mark.parametrize(
    "value",
    ["#0000000", "#000000000", "00000000", "#gg000000", "#0000000z", 1, None],
)
def test_invalid_rgba_values_are_rejected(value: object) -> None:
    _assert_error(OverlayErrorCode.COLOR_INVALID, lambda: RgbaColor(cast(str, value)))


@pytest.mark.parametrize("z_index", [0, MAX_SAFE_MANIFEST_INTEGER])
def test_overlay_record_accepts_released_z_index_boundaries(z_index: int) -> None:
    record = OverlayRecord(
        _overlay_id(1),
        PAGE_A,
        FULL_PAGE,
        z_index,
        _mark_payload(),
    )
    assert record.z_index == z_index


@pytest.mark.parametrize("value", [-1, MAX_SAFE_MANIFEST_INTEGER + 1, True, 1.0, "1"])
def test_overlay_record_rejects_invalid_z_indices(value: object) -> None:
    _assert_error(
        OverlayErrorCode.Z_INDEX_INVALID,
        lambda: OverlayRecord(
            _overlay_id(1),
            PAGE_A,
            FULL_PAGE,
            cast(int, value),
            _mark_payload(),
        ),
    )


@pytest.mark.parametrize("value", [0, 2, True, "1"])
def test_overlay_record_rejects_unsupported_versions(value: object) -> None:
    _assert_error(
        OverlayErrorCode.VERSION_UNSUPPORTED,
        lambda: OverlayRecord(
            _overlay_id(1),
            PAGE_A,
            FULL_PAGE,
            0,
            _mark_payload(),
            cast(int, value),
        ),
    )


@pytest.mark.parametrize("byte_size", [1, MAX_SAFE_MANIFEST_INTEGER])
@pytest.mark.parametrize(
    "media_type",
    [OverlayAssetMediaType.JPEG, OverlayAssetMediaType.PNG],
)
def test_asset_constructor_and_codec_accept_released_boundaries(
    byte_size: int,
    media_type: OverlayAssetMediaType,
) -> None:
    asset = OverlayAssetRecord(
        ASSET_A,
        OverlayAssetKind.SIGNATURE_IMAGE,
        f"{ASSET_A}.bin",
        media_type,
        byte_size,
        "c" * 64,
    )
    assert OverlayAssetRecord.from_manifest(asset.to_manifest()) == asset


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (0, OverlayErrorCode.ASSET_INVALID),
        (MAX_SAFE_MANIFEST_INTEGER + 1, OverlayErrorCode.MANIFEST_INVALID),
        (True, OverlayErrorCode.ASSET_INVALID),
        (1.0, OverlayErrorCode.ASSET_INVALID),
        ("1", OverlayErrorCode.ASSET_INVALID),
    ],
)
def test_asset_rejects_invalid_byte_sizes(value: object, code: OverlayErrorCode) -> None:
    wire = _asset_wire(byte_size=value)
    _assert_error(code, lambda: OverlayAssetRecord.from_manifest(wire))


def test_asset_rejects_storage_digest_media_and_kind_mismatches() -> None:
    mutations: tuple[tuple[str, object, OverlayErrorCode], ...] = (
        ("storage_name", f"{PAGE_A}.bin", OverlayErrorCode.ASSET_INVALID),
        ("sha256", "c" * 63, OverlayErrorCode.ASSET_INVALID),
        ("media_type", "image/gif", OverlayErrorCode.ASSET_INVALID),
        ("kind", "digital_signature", OverlayErrorCode.ASSET_INCOMPATIBLE),
    )
    for field, value, code in mutations:
        wire = _asset_wire()
        wire[field] = value
        _assert_error(code, partial(OverlayAssetRecord.from_manifest, wire))


def test_sparse_layer_order_is_low_to_high_and_manifest_array_order_is_retained() -> None:
    input_records = [
        _model_record(1, z_index=100),
        _model_record(2, z_index=0),
        _model_record(3, z_index=50),
    ]
    collection = OverlayCollection(
        [],
        input_records,
        known_page_ids=[PAGE_A],
        active_page_ids=[PAGE_A],
    )

    assert [record.z_index for record in collection.overlays] == [100, 0, 50]
    assert [record.z_index for record in collection.stack_for_page(PAGE_A)] == [0, 50, 100]


def test_same_layer_on_different_pages_is_valid_but_same_page_collision_is_rejected() -> None:
    first = _model_record(1, page_id=PAGE_A, z_index=7)
    other_page = _model_record(2, page_id=PAGE_B, z_index=7)
    collection = OverlayCollection(
        [],
        [first, other_page],
        known_page_ids=[PAGE_A, PAGE_B],
        active_page_ids=[PAGE_A, PAGE_B],
    )
    assert collection.stack_for_page(PAGE_A) == (first,)
    assert collection.stack_for_page(PAGE_B) == (other_page,)

    collision = _model_record(3, page_id=PAGE_A, z_index=7)
    _assert_error(
        OverlayErrorCode.Z_INDEX_COLLISION,
        lambda: OverlayCollection(
            [],
            [first, collision],
            known_page_ids=[PAGE_A],
            active_page_ids=[PAGE_A],
        ),
    )


def test_identifier_collisions_are_rejected_across_collection_namespaces() -> None:
    duplicate = OverlayRecord(
        _overlay_id(1),
        PAGE_A,
        FULL_PAGE,
        2,
        _mark_payload(),
    )
    _assert_error(
        OverlayErrorCode.IDENTIFIER_COLLISION,
        lambda: OverlayCollection(
            [],
            [_model_record(1, z_index=1), duplicate],
            known_page_ids=[PAGE_A],
            active_page_ids=[PAGE_A],
        ),
    )
    _assert_error(
        OverlayErrorCode.IDENTIFIER_COLLISION,
        lambda: OverlayCollection(
            [],
            [],
            known_page_ids=[PAGE_A, PAGE_A],
            active_page_ids=[PAGE_A],
        ),
    )


def test_unknown_overlay_and_active_pages_fail_closed() -> None:
    record = _model_record(1, page_id=PAGE_UNKNOWN)
    _assert_error(
        OverlayErrorCode.PAGE_UNKNOWN,
        lambda: OverlayCollection(
            [],
            [record],
            known_page_ids=[PAGE_A],
            active_page_ids=[PAGE_A],
        ),
    )
    _assert_error(
        OverlayErrorCode.PAGE_UNKNOWN,
        lambda: OverlayCollection(
            [],
            [],
            known_page_ids=[PAGE_A],
            active_page_ids=[PAGE_UNKNOWN],
        ),
    )

    valid = OverlayCollection(
        [],
        [],
        known_page_ids=[PAGE_A],
        active_page_ids=[PAGE_A],
    )
    _assert_error(OverlayErrorCode.PAGE_UNKNOWN, lambda: valid.stack_for_page(PAGE_UNKNOWN))


def test_signature_image_requires_one_resolving_case_internal_asset() -> None:
    payload = SignatureImageOverlayPayload(ASSET_A)
    signature = OverlayRecord(_overlay_id(1), PAGE_A, FULL_PAGE, 0, payload)

    _assert_error(
        OverlayErrorCode.ASSET_UNKNOWN,
        lambda: OverlayCollection(
            [],
            [signature],
            known_page_ids=[PAGE_A],
            active_page_ids=[PAGE_A],
        ),
    )

    collection = OverlayCollection(
        [_asset_model()],
        [signature],
        known_page_ids=[PAGE_A],
        active_page_ids=[PAGE_A],
    )
    assert collection.overlays == (signature,)


def test_visibility_is_derived_from_page_activity_and_crop_without_mutating_intent() -> None:
    contained = _model_record(
        1,
        box=NormalizedPageBox(350_000, 250_000, 100_000, 100_000),
        z_index=1,
    )
    partial = _model_record(
        2,
        box=NormalizedPageBox(100_000, 100_000, 400_000, 400_000),
        z_index=2,
    )
    outside = _model_record(
        3,
        box=NormalizedPageBox(800_000, 800_000, 100_000, 100_000),
        z_index=3,
    )
    inactive = _model_record(
        4,
        page_id=PAGE_B,
        box=NormalizedPageBox(350_000, 250_000, 100_000, 100_000),
        z_index=1,
    )
    records = [outside, inactive, partial, contained]
    saved_before = [record.to_manifest() for record in records]
    collection = OverlayCollection(
        [],
        records,
        known_page_ids=[PAGE_A, PAGE_B],
        active_page_ids=[PAGE_A],
    )
    crop = NormalizedPageBox(300_000, 200_000, 400_000, 400_000)

    active_projections = collection.projections_for_page(PAGE_A, active_crop=crop)
    inactive_projections = collection.projections_for_page(PAGE_B, active_crop=crop)

    assert [projection.visibility for projection in active_projections] == [
        OverlayVisibility.VISIBLE,
        OverlayVisibility.PARTIALLY_VISIBLE,
        OverlayVisibility.OUTSIDE_ACTIVE_CROP,
    ]
    assert active_projections[0].visible_box == contained.box
    assert active_projections[1].visible_box == NormalizedPageBox(
        300_000,
        200_000,
        200_000,
        300_000,
    )
    assert active_projections[2].visible_box is None
    assert [
        (item.record, item.visible_box)
        for item in collection.visible_stack(PAGE_A, active_crop=crop)
    ] == [
        (contained, contained.box),
        (partial, NormalizedPageBox(300_000, 200_000, 200_000, 300_000)),
    ]
    assert len(inactive_projections) == 1
    assert inactive_projections[0].visibility is OverlayVisibility.PAGE_INACTIVE
    assert inactive_projections[0].visible_box is None
    assert [record.to_manifest() for record in records] == saved_before


def test_projection_constructor_enforces_visibility_box_presence() -> None:
    record = _model_record(1)
    assert (
        OverlayProjection(record, OverlayVisibility.VISIBLE, record.box).visible_box == record.box
    )
    for visibility in (
        OverlayVisibility.PAGE_INACTIVE,
        OverlayVisibility.OUTSIDE_ACTIVE_CROP,
    ):
        _assert_error(
            OverlayErrorCode.MODEL_INVALID,
            partial(OverlayProjection, record, visibility, record.box),
        )
    _assert_error(
        OverlayErrorCode.MODEL_INVALID,
        lambda: OverlayProjection(record, OverlayVisibility.VISIBLE, None),
    )


def test_projection_constructor_enforces_full_and_partial_geometry_meaning() -> None:
    record = _model_record(
        1,
        box=NormalizedPageBox(100_000, 100_000, 400_000, 400_000),
    )
    partial = NormalizedPageBox(200_000, 200_000, 100_000, 100_000)

    assert (
        OverlayProjection(
            record,
            OverlayVisibility.PARTIALLY_VISIBLE,
            partial,
        ).visible_box
        == partial
    )
    _assert_error(
        OverlayErrorCode.MODEL_INVALID,
        lambda: OverlayProjection(record, OverlayVisibility.VISIBLE, partial),
    )
    _assert_error(
        OverlayErrorCode.MODEL_INVALID,
        lambda: OverlayProjection(
            record,
            OverlayVisibility.PARTIALLY_VISIBLE,
            record.box,
        ),
    )
    _assert_error(
        OverlayErrorCode.MODEL_INVALID,
        lambda: OverlayProjection(
            record,
            OverlayVisibility.PARTIALLY_VISIBLE,
            NormalizedPageBox(800_000, 800_000, 100_000, 100_000),
        ),
    )


def test_fixed_seed_reorders_always_project_the_same_sparse_stack() -> None:
    records = [_model_record(index + 1, z_index=index * 10) for index in range(32)]
    expected = tuple(records)
    randomizer = random.Random(30_02)

    for _ in range(64):
        shuffled = list(records)
        randomizer.shuffle(shuffled)
        collection = OverlayCollection(
            [],
            shuffled,
            known_page_ids=[PAGE_A],
            active_page_ids=[PAGE_A],
        )
        assert collection.stack_for_page(PAGE_A) == expected
        assert tuple(collection.overlays) == tuple(shuffled)


@pytest.mark.parametrize(
    "surface",
    ["assets", "overlays", "known_pages", "active_pages"],
)
def test_collection_capacity_is_enforced_before_unbounded_graph_work(surface: str) -> None:
    assets: list[OverlayAssetRecord] = []
    overlays: list[OverlayRecord] = []
    known_pages = [PAGE_A]
    active_pages = [PAGE_A]
    if surface == "assets":
        assets = [_asset_model()] * (MAX_OVERLAY_ASSETS + 1)
    elif surface == "overlays":
        overlays = [_model_record(1)] * (MAX_OVERLAYS + 1)
    elif surface == "known_pages":
        known_pages = [PAGE_A] * (MAX_OVERLAYS + 1)
    else:
        active_pages = [PAGE_A] * (MAX_OVERLAYS + 1)

    _assert_error(
        OverlayErrorCode.CAPACITY_EXCEEDED,
        lambda: OverlayCollection(
            assets,
            overlays,
            known_page_ids=known_pages,
            active_page_ids=active_pages,
        ),
    )


def test_malformed_record_members_version_ids_box_and_z_fail_closed() -> None:
    base = _record_wire(1, copy.deepcopy(REPRESENTATIVE_PAYLOADS[0]))

    missing = copy.deepcopy(base)
    del missing["box"]
    _assert_error(OverlayErrorCode.MANIFEST_INVALID, lambda: OverlayRecord.from_manifest(missing))

    extra = copy.deepcopy(base)
    extra["script"] = "do-not-run"
    _assert_error(OverlayErrorCode.MANIFEST_INVALID, lambda: OverlayRecord.from_manifest(extra))

    mutations: tuple[tuple[str, object, OverlayErrorCode], ...] = (
        ("overlay_version", 2, OverlayErrorCode.VERSION_UNSUPPORTED),
        ("overlay_version", True, OverlayErrorCode.VERSION_UNSUPPORTED),
        (
            "overlay_id",
            "6abcdef0-0000-4000-8000-00000000000A",
            OverlayErrorCode.IDENTIFIER_INVALID,
        ),
        ("page_id", "not-an-id", OverlayErrorCode.IDENTIFIER_INVALID),
        ("box", None, OverlayErrorCode.BOX_INVALID),
        ("z_index", -1, OverlayErrorCode.Z_INDEX_INVALID),
        ("z_index", True, OverlayErrorCode.Z_INDEX_INVALID),
    )
    for field, value, code in mutations:
        wire = copy.deepcopy(base)
        wire[field] = value
        _assert_error(code, partial(OverlayRecord.from_manifest, wire))

    invalid_box = copy.deepcopy(base)
    box = cast(dict[str, object], invalid_box["box"])
    box["width"] = 0
    _assert_error(OverlayErrorCode.BOX_INVALID, lambda: OverlayRecord.from_manifest(invalid_box))


def test_malformed_payload_fields_and_types_fail_closed() -> None:
    cases: list[tuple[dict[str, object], OverlayErrorCode]] = []

    text = copy.deepcopy(REPRESENTATIVE_PAYLOADS[0])
    text["value"] = 1
    cases.append((text, OverlayErrorCode.TEXT_INVALID))
    text = copy.deepcopy(REPRESENTATIVE_PAYLOADS[0])
    text["font_size_points"] = True
    cases.append((text, OverlayErrorCode.FONT_SIZE_INVALID))
    text = copy.deepcopy(REPRESENTATIVE_PAYLOADS[0])
    text["font_token"] = "../font"
    cases.append((text, OverlayErrorCode.FONT_INVALID))
    text = copy.deepcopy(REPRESENTATIVE_PAYLOADS[0])
    text["color"] = "black"
    cases.append((text, OverlayErrorCode.COLOR_INVALID))
    text = copy.deepcopy(REPRESENTATIVE_PAYLOADS[0])
    text["extra"] = "member"
    cases.append((text, OverlayErrorCode.MANIFEST_INVALID))

    shape = copy.deepcopy(REPRESENTATIVE_PAYLOADS[6])
    shape["shape"] = "polygon"
    cases.append((shape, OverlayErrorCode.SHAPE_INVALID))
    shape = copy.deepcopy(REPRESENTATIVE_PAYLOADS[6])
    shape["intent"] = "permanent_redaction"
    cases.append((shape, OverlayErrorCode.SHAPE_INTENT_INVALID))
    shape = copy.deepcopy(REPRESENTATIVE_PAYLOADS[6])
    shape["stroke_color"] = "#xyz"
    cases.append((shape, OverlayErrorCode.COLOR_INVALID))

    signature = copy.deepcopy(REPRESENTATIVE_PAYLOADS[5])
    signature["terminology"] = "digitally_signed"
    cases.append((signature, OverlayErrorCode.SIGNATURE_TERMINOLOGY_INVALID))
    signature = copy.deepcopy(REPRESENTATIVE_PAYLOADS[5])
    signature["asset_id"] = "not-an-id"
    cases.append((signature, OverlayErrorCode.IDENTIFIER_INVALID))

    cases.append(({"kind": "digital_signature"}, OverlayErrorCode.PAYLOAD_UNSUPPORTED))
    cases.append(({"kind": 1}, OverlayErrorCode.PAYLOAD_UNSUPPORTED))

    for payload, code in cases:
        _assert_error(code, partial(overlay_payload_from_manifest, payload))


def test_signature_and_shape_payloads_reject_prohibited_claim_fields() -> None:
    signature = copy.deepcopy(REPRESENTATIVE_PAYLOADS[5])
    signature["cryptographic_status"] = "signed"
    _assert_error(
        OverlayErrorCode.MANIFEST_INVALID,
        lambda: overlay_payload_from_manifest(signature),
    )

    shape = copy.deepcopy(REPRESENTATIVE_PAYLOADS[6])
    shape["redaction"] = True
    _assert_error(
        OverlayErrorCode.MANIFEST_INVALID,
        lambda: overlay_payload_from_manifest(shape),
    )


def test_manifest_document_dispatch_and_required_overlay_surfaces_fail_closed() -> None:
    valid = _document([])
    assert OverlayCollection.from_manifest_document(valid).overlays == ()

    for version, code in (
        (True, OverlayErrorCode.MANIFEST_INVALID),
        (3, OverlayErrorCode.VERSION_UNSUPPORTED),
        ("2", OverlayErrorCode.MANIFEST_INVALID),
    ):
        document = _document([], schema_version=version)
        _assert_error(code, partial(OverlayCollection.from_manifest_document, document))

    for field in ("pages", "page_sequence", "assets", "overlays"):
        document = _document([])
        document[field] = None
        _assert_error(
            OverlayErrorCode.MANIFEST_INVALID,
            partial(OverlayCollection.from_manifest_document, document),
        )


def test_sensitive_values_and_asset_details_are_absent_from_representations_and_errors() -> None:
    marker = "SYNTHETIC-PRIVATE-FORM-VALUE"
    payload = TextualOverlayPayload(
        OverlayKind.TEXT,
        marker,
        DEFAULT_FONT_TOKEN,
        12,
        RgbaColor("#000000ff"),
    )
    record = OverlayRecord(_overlay_id(1), PAGE_A, FULL_PAGE, 0, payload)
    asset = _asset_model()
    collection = OverlayCollection([], [record], known_page_ids=[PAGE_A], active_page_ids=[PAGE_A])

    for rendered in (repr(payload), repr(record), repr(asset), repr(collection)):
        assert marker not in rendered
        assert str(ASSET_A) not in rendered
        assert "c" * 64 not in rendered

    error = _assert_error(
        OverlayErrorCode.TEXT_INVALID,
        lambda: TextualOverlayPayload(
            OverlayKind.TEXT,
            marker + ("x" * MAX_OVERLAY_TEXT_CODE_POINTS),
            DEFAULT_FONT_TOKEN,
            12,
            RgbaColor("#000000ff"),
        ),
    )
    assert marker not in str(error)
    assert marker not in repr(error)

from __future__ import annotations

import hashlib
import random
from dataclasses import replace
from uuid import UUID

import pytest

from paperworkshell.domain.image_pages import (
    ImageExifOrientation,
    ImagePageError,
    ImagePageErrorCode,
    ImageQuarterTurn,
    ImageRgba8Layout,
    ImageSourceFormat,
)
from paperworkshell.domain.image_transforms import (
    IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES,
    IMAGE_TRANSFORM_BACKGROUND_THRESHOLD,
    IMAGE_TRANSFORM_CACHE_KEY_BYTES,
    IMAGE_TRANSFORM_ENGINE_PROFILE_SHA256,
    IMAGE_TRANSFORM_INTENT_BYTES,
    IMAGE_TRANSFORM_ORDER,
    ImageNormalizedCrop,
    ImagePageTransformRequest,
    ImageTransformArtifactHeader,
    ImageTransformCacheKey,
    ImageTransformIntent,
    ImageTransformLimits,
    ImageTransformResult,
    decode_image_transform_artifact,
    decode_image_transform_artifact_header,
    decode_image_transform_cache_key,
    decode_image_transform_intent,
    encode_image_transform_artifact,
    encode_image_transform_artifact_header,
    image_page_transform_result_from_artifact,
    plan_image_transform,
    validate_image_transform_result,
)

CASE_ID = UUID("10000000-0000-4000-8000-000000000001")
SOURCE_ID = UUID("20000000-0000-4000-8000-000000000001")
PAGE_ID = UUID("30000000-0000-4000-8000-000000000001")
OPERATION_ID = UUID("40000000-0000-4000-8000-000000000001")
OUTPUT_ID = UUID("50000000-0000-4000-8000-000000000001")


def _layout(width: int = 100, height: int = 80) -> ImageRgba8Layout:
    return ImageRgba8Layout(width, height, width * 4, width * height, width * height * 4)


def _limits(**changes: int) -> ImageTransformLimits:
    values = {
        "maximum_source_width_pixels": 1_000,
        "maximum_source_height_pixels": 1_000,
        "maximum_source_pixels": 1_000_000,
        "maximum_intermediate_pixels": 1_000_000,
        "maximum_output_width_pixels": 1_000,
        "maximum_output_height_pixels": 1_000,
        "maximum_output_pixels": 1_000_000,
        "maximum_output_bytes": 4_000_000,
        "maximum_memory_bytes": 8_000_000,
        "maximum_temporary_storage_bytes": 4_000_000,
        "wall_time_ms": 10_000,
    }
    values.update(changes)
    return ImageTransformLimits(**values)


def _intent(**changes: object) -> ImageTransformIntent:
    values: dict[str, object] = {
        "crop": ImageNormalizedCrop(100_000, 200_000, 500_000, 500_000),
        "additional_rotation": ImageQuarterTurn.DEGREES_90,
        "deskew_angle_millidegrees_clockwise": 2_500,
        "contrast_factor_millionths": 1_250_000,
        "background_cleanup_strength_millionths": 500_000,
    }
    values.update(changes)
    return ImageTransformIntent(**values)  # type: ignore[arg-type]


def _cache_key(intent: ImageTransformIntent | None = None) -> ImageTransformCacheKey:
    selected_intent = _intent() if intent is None else intent
    pixels = bytes((10, 20, 30, 255)) * _layout().pixel_count
    return ImageTransformCacheKey(
        case_id=CASE_ID,
        source_id=SOURCE_ID,
        page_id=PAGE_ID,
        source_byte_size=1_024,
        source_sha256="a" * 64,
        source_format=ImageSourceFormat.PNG,
        source_layout=_layout(),
        source_pixel_sha256=hashlib.sha256(pixels).hexdigest(),
        exif_orientation=ImageExifOrientation.NORMAL,
        intent=selected_intent,
        maximum_width_pixels=30,
        maximum_height_pixels=30,
    )


def _header() -> tuple[ImageTransformArtifactHeader, bytes]:
    key = _cache_key()
    plan = plan_image_transform(key.source_layout, key.intent, 30, 30, _limits())
    pixels = bytes((40, 50, 60, 255)) * plan.output_layout.pixel_count
    result = ImageTransformResult(
        source_width_pixels=plan.source_layout.width_pixels,
        source_height_pixels=plan.source_layout.height_pixels,
        crop_left_pixels=plan.crop_left_pixels,
        crop_top_pixels=plan.crop_top_pixels,
        crop_right_pixels=plan.crop_right_pixels,
        crop_bottom_pixels=plan.crop_bottom_pixels,
        rotated_width_pixels=plan.rotated_width_pixels,
        rotated_height_pixels=plan.rotated_height_pixels,
        width_pixels=plan.output_layout.width_pixels,
        height_pixels=plan.output_layout.height_pixels,
        row_stride_bytes=plan.output_layout.row_stride_bytes,
        bytes_written=plan.output_layout.byte_count,
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
        intent_sha256=key.intent.intent_sha256,
    )
    validate_image_transform_result(plan, result)
    return ImageTransformArtifactHeader(OPERATION_ID, OUTPUT_ID, key, result), pixels


def test_transform_profile_and_order_are_closed() -> None:
    assert IMAGE_TRANSFORM_ORDER == (
        "exif",
        "crop",
        "quarter_turn",
        "deskew",
        "contrast",
        "background_cleanup",
        "fit",
        "serialize",
    )
    assert IMAGE_TRANSFORM_BACKGROUND_THRESHOLD == 192
    assert len(IMAGE_TRANSFORM_ENGINE_PROFILE_SHA256) == 64


def test_intent_has_unique_fixed_encoding_and_round_trips() -> None:
    intent = _intent()
    encoded = intent.canonical_bytes()

    assert len(encoded) == IMAGE_TRANSFORM_INTENT_BYTES
    assert decode_image_transform_intent(encoded) == intent
    assert intent.intent_sha256 == hashlib.sha256(encoded).hexdigest()
    assert _intent(contrast_factor_millionths=1_250_001).intent_sha256 != intent.intent_sha256


@pytest.mark.parametrize(
    "changes",
    (
        {"deskew_angle_millidegrees_clockwise": -15_001},
        {"deskew_angle_millidegrees_clockwise": 15_001},
        {"contrast_factor_millionths": -1},
        {"contrast_factor_millionths": 4_000_001},
        {"background_cleanup_strength_millionths": -1},
        {"background_cleanup_strength_millionths": 1_000_001},
    ),
)
def test_intent_rejects_exact_out_of_range_values(changes: dict[str, object]) -> None:
    with pytest.raises(ImagePageError) as captured:
        _intent(**changes)

    assert captured.value.code is ImagePageErrorCode.MODEL_INVALID


@pytest.mark.parametrize(
    "crop",
    (
        ImageNormalizedCrop(0, 0, 1, 1),
        ImageNormalizedCrop(999_999, 999_999, 1, 1),
        ImageNormalizedCrop(),
    ),
)
def test_positive_normalized_crop_maps_to_at_least_one_pixel(crop: ImageNormalizedCrop) -> None:
    plan = plan_image_transform(_layout(17, 13), _intent(crop=crop), 100, 100, _limits())

    assert plan.crop_right_pixels > plan.crop_left_pixels
    assert plan.crop_bottom_pixels > plan.crop_top_pixels


def test_plan_applies_crop_then_quarter_turn_then_fixed_canvas_deskew_then_fit() -> None:
    plan = plan_image_transform(_layout(), _intent(), 30, 30, _limits())

    assert plan.crop_box_pixels == (10, 16, 60, 56)
    assert (plan.rotated_width_pixels, plan.rotated_height_pixels) == (40, 50)
    assert (plan.output_layout.width_pixels, plan.output_layout.height_pixels) == (24, 30)
    assert plan.required_temporary_storage_bytes == 34_880
    assert plan.estimated_peak_memory_bytes == 85_520


def test_plan_accepts_exact_memory_and_temp_limits_and_rejects_minus_one() -> None:
    exact = plan_image_transform(_layout(), _intent(), 30, 30, _limits())
    plan_image_transform(
        _layout(),
        _intent(),
        30,
        30,
        _limits(
            maximum_memory_bytes=exact.estimated_peak_memory_bytes,
            maximum_temporary_storage_bytes=exact.required_temporary_storage_bytes,
        ),
    )

    with pytest.raises(ImagePageError) as memory_error:
        plan_image_transform(
            _layout(),
            _intent(),
            30,
            30,
            _limits(maximum_memory_bytes=exact.estimated_peak_memory_bytes - 1),
        )
    with pytest.raises(ImagePageError) as temp_error:
        plan_image_transform(
            _layout(),
            _intent(),
            30,
            30,
            _limits(maximum_temporary_storage_bytes=exact.required_temporary_storage_bytes - 1),
        )

    assert memory_error.value.context == "image.transform_memory"
    assert temp_error.value.context == "image.transform_temp"


def test_cache_key_fixed_codec_binds_every_semantic_input() -> None:
    key = _cache_key()
    encoded = key.canonical_bytes()

    assert len(encoded) == IMAGE_TRANSFORM_CACHE_KEY_BYTES
    assert decode_image_transform_cache_key(encoded) == key
    assert key.key_sha256 == hashlib.sha256(encoded).hexdigest()
    changed = replace(key, maximum_width_pixels=31)
    assert changed.key_sha256 != key.key_sha256
    assert changed.internal_name != key.internal_name


def test_artifact_header_and_complete_artifact_round_trip_with_exact_binding() -> None:
    header, pixels = _header()
    encoded_header = encode_image_transform_artifact_header(header)
    encoded = encode_image_transform_artifact(header, pixels)

    assert len(encoded_header) == IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES
    assert decode_image_transform_artifact_header(encoded_header) == header
    artifact = decode_image_transform_artifact(
        encoded,
        expected_operation_id=OPERATION_ID,
        expected_output_id=OUTPUT_ID,
        expected_cache_key=header.cache_key,
    )
    request = ImagePageTransformRequest(CASE_ID, PAGE_ID, _intent(), 30, 30)
    application_result = image_page_transform_result_from_artifact(request, artifact)
    assert application_result.pixels == pixels
    assert application_result.snapshot.source_id == SOURCE_ID
    assert application_result.snapshot.cache_key_sha256 == header.cache_key.key_sha256


@pytest.mark.parametrize("byte_index", (0, IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES, -1))
def test_artifact_rejects_header_or_pixel_tampering(byte_index: int) -> None:
    header, pixels = _header()
    encoded = bytearray(encode_image_transform_artifact(header, pixels))
    encoded[byte_index] ^= 1

    with pytest.raises(ImagePageError) as captured:
        decode_image_transform_artifact(bytes(encoded))

    assert captured.value.code is ImagePageErrorCode.CACHE_INVALID


def test_seeded_geometry_grid_is_deterministic_and_bounded() -> None:
    randomizer = random.Random(0x0202)
    for _ in range(200):
        width = randomizer.randint(1, 512)
        height = randomizer.randint(1, 512)
        x = randomizer.randint(0, 999_999)
        y = randomizer.randint(0, 999_999)
        crop_width = randomizer.randint(1, 1_000_000 - x)
        crop_height = randomizer.randint(1, 1_000_000 - y)
        intent = ImageTransformIntent(
            crop=ImageNormalizedCrop(x, y, crop_width, crop_height),
            additional_rotation=randomizer.choice(tuple(ImageQuarterTurn)),
            deskew_angle_millidegrees_clockwise=randomizer.randint(-15_000, 15_000),
            contrast_factor_millionths=randomizer.randint(0, 4_000_000),
            background_cleanup_strength_millionths=randomizer.randint(0, 1_000_000),
        )
        maximum_width = randomizer.randint(1, 256)
        maximum_height = randomizer.randint(1, 256)
        first = plan_image_transform(
            _layout(width, height), intent, maximum_width, maximum_height, _limits()
        )
        second = plan_image_transform(
            _layout(width, height), intent, maximum_width, maximum_height, _limits()
        )

        assert first == second
        assert first.output_layout.width_pixels <= maximum_width
        assert first.output_layout.height_pixels <= maximum_height

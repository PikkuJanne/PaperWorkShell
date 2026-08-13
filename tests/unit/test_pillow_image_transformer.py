from __future__ import annotations

import hashlib
from collections.abc import Buffer
from io import BytesIO
from unittest.mock import patch

import pytest

from paperworkshell.domain.image_pages import (
    ImagePageError,
    ImagePageErrorCode,
    ImageQuarterTurn,
    ImageRgba8Layout,
)
from paperworkshell.domain.image_transforms import (
    IMAGE_TRANSFORM_ENGINE_PROFILE_SHA256,
    IMAGE_TRANSFORM_PILLOW_VERSION,
    ImageNormalizedCrop,
    ImageTransformIntent,
    ImageTransformLimits,
    ImageTransformResult,
    plan_image_transform,
)
from paperworkshell.imaging.pillow_decoder import EXPECTED_PILLOW_VERSION
from paperworkshell.imaging.pillow_transformer import PillowImageTransformer


class _Control:
    def __init__(self, *, cancel_after: int | None = None, expire_after: int | None = None) -> None:
        self.cancel_after = cancel_after
        self.expire_after = expire_after
        self.cancel_checks = 0
        self.deadline_checks = 0

    def cancellation_requested(self) -> bool:
        self.cancel_checks += 1
        return self.cancel_after is not None and self.cancel_checks >= self.cancel_after

    def deadline_expired(self) -> bool:
        self.deadline_checks += 1
        return self.expire_after is not None and self.deadline_checks >= self.expire_after


def test_decoder_and_transform_profile_pin_the_same_pillow_version() -> None:
    assert IMAGE_TRANSFORM_PILLOW_VERSION == EXPECTED_PILLOW_VERSION


def _layout(width: int = 4, height: int = 3) -> ImageRgba8Layout:
    return ImageRgba8Layout(width, height, width * 4, width * height, width * height * 4)


def _limits(**changes: int) -> ImageTransformLimits:
    values = {
        "maximum_source_width_pixels": 512,
        "maximum_source_height_pixels": 512,
        "maximum_source_pixels": 262_144,
        "maximum_intermediate_pixels": 262_144,
        "maximum_output_width_pixels": 512,
        "maximum_output_height_pixels": 512,
        "maximum_output_pixels": 262_144,
        "maximum_output_bytes": 1_048_576,
        "maximum_memory_bytes": 4_194_304,
        "maximum_temporary_storage_bytes": 2_097_152,
        "wall_time_ms": 10_000,
    }
    values.update(changes)
    return ImageTransformLimits(**values)


def _intent(**changes: object) -> ImageTransformIntent:
    values: dict[str, object] = {
        "crop": ImageNormalizedCrop(),
        "additional_rotation": ImageQuarterTurn.DEGREES_0,
        "deskew_angle_millidegrees_clockwise": 0,
        "contrast_factor_millionths": 1_000_000,
        "background_cleanup_strength_millionths": 0,
    }
    values.update(changes)
    return ImageTransformIntent(**values)  # type: ignore[arg-type]


def _pixels() -> bytes:
    values = (
        (10, 20, 30, 255),
        (40, 50, 60, 255),
        (70, 80, 90, 255),
        (100, 110, 120, 255),
        (130, 140, 150, 255),
        (160, 170, 180, 255),
        (190, 200, 210, 255),
        (220, 230, 240, 255),
        (250, 245, 235, 255),
        (32, 64, 96, 255),
        (128, 128, 128, 255),
        (255, 255, 255, 255),
    )
    return bytes(component for pixel in values for component in pixel)


def _transform(
    intent: ImageTransformIntent,
    *,
    control: _Control | None = None,
    maximum_width: int = 512,
    maximum_height: int = 512,
) -> tuple[ImageTransformResult, bytes, BytesIO]:
    source = BytesIO(_pixels())
    source.seek(7)
    destination = BytesIO()
    result = PillowImageTransformer().transform(
        source,
        destination,
        source_layout=_layout(),
        intent=intent,
        maximum_width_pixels=maximum_width,
        maximum_height_pixels=maximum_height,
        limits=_limits(),
        control=_Control() if control is None else control,
    )
    return result, destination.getvalue(), source


def test_identity_transform_is_byte_exact_and_restores_source_position() -> None:
    result, output, source = _transform(_intent())

    assert output == _pixels()
    assert source.tell() == 7
    assert result.pixel_sha256 == hashlib.sha256(output).hexdigest()
    assert result.engine_profile_sha256 == IMAGE_TRANSFORM_ENGINE_PROFILE_SHA256


def test_crop_then_clockwise_quarter_turn_has_exact_geometry_and_pixels() -> None:
    intent = _intent(
        crop=ImageNormalizedCrop(250_000, 0, 500_000, 1_000_000),
        additional_rotation=ImageQuarterTurn.DEGREES_90,
    )

    result, output, _ = _transform(intent)

    assert (result.width_pixels, result.height_pixels) == (3, 2)
    expected_source_pixel_indexes = (9, 5, 1, 10, 6, 2)
    expected = b"".join(
        _pixels()[index * 4 : index * 4 + 4] for index in expected_source_pixel_indexes
    )
    assert output == expected


def test_deskew_is_fixed_canvas_opaque_and_deterministic() -> None:
    intent = _intent(deskew_angle_millidegrees_clockwise=5_000)

    first_result, first, _ = _transform(intent)
    second_result, second, _ = _transform(intent)

    assert first == second
    assert first_result == second_result
    assert (first_result.width_pixels, first_result.height_pixels) == (4, 3)
    assert all(alpha == 255 for alpha in first[3::4])


@pytest.mark.parametrize(
    ("factor", "source_value", "expected_value"),
    (
        (0, 10, 128),
        (1_000_000, 10, 10),
        (2_000_000, 100, 72),
        (4_000_000, 220, 255),
    ),
)
def test_contrast_uses_exact_fixed_integer_midpoint_lut(
    factor: int,
    source_value: int,
    expected_value: int,
) -> None:
    source = bytes((source_value, source_value, source_value, 255))
    destination = BytesIO()
    layout = _layout(1, 1)
    PillowImageTransformer().transform(
        BytesIO(source),
        destination,
        source_layout=layout,
        intent=_intent(contrast_factor_millionths=factor),
        maximum_width_pixels=1,
        maximum_height_pixels=1,
        limits=_limits(),
        control=_Control(),
    )

    assert destination.getvalue() == bytes((expected_value, expected_value, expected_value, 255))


def test_background_cleanup_changes_only_values_above_closed_threshold() -> None:
    source = bytes((191, 192, 193, 255, 220, 240, 254, 255))
    destination = BytesIO()
    PillowImageTransformer().transform(
        BytesIO(source),
        destination,
        source_layout=_layout(2, 1),
        intent=_intent(background_cleanup_strength_millionths=1_000_000),
        maximum_width_pixels=2,
        maximum_height_pixels=1,
        limits=_limits(),
        control=_Control(),
    )

    output = destination.getvalue()
    assert output[:3] == bytes((191, 192, 194))
    assert output[4:7] == bytes((236, 251, 255))
    assert output[3::4] == bytes((255, 255))


def test_fit_is_last_and_never_upscales() -> None:
    identity_result, _, _ = _transform(_intent(), maximum_width=100, maximum_height=100)
    fitted_result, output, _ = _transform(_intent(), maximum_width=2, maximum_height=2)

    assert (identity_result.width_pixels, identity_result.height_pixels) == (4, 3)
    assert (fitted_result.width_pixels, fitted_result.height_pixels) == (2, 2)
    assert len(output) == 16


@pytest.mark.parametrize(
    ("control", "code"),
    (
        (_Control(cancel_after=1), ImagePageErrorCode.CANCELLED),
        (_Control(expire_after=1), ImagePageErrorCode.TIMED_OUT),
        (_Control(cancel_after=8), ImagePageErrorCode.CANCELLED),
    ),
)
def test_cancellation_and_deadline_fail_without_success_result(
    control: _Control,
    code: ImagePageErrorCode,
) -> None:
    destination = BytesIO()

    with pytest.raises(ImagePageError) as captured:
        PillowImageTransformer().transform(
            BytesIO(_pixels()),
            destination,
            source_layout=_layout(),
            intent=_intent(deskew_angle_millidegrees_clockwise=5_000),
            maximum_width_pixels=4,
            maximum_height_pixels=3,
            limits=_limits(),
            control=control,
        )

    assert captured.value.code is code


def test_invalid_alpha_or_exact_source_length_is_rejected() -> None:
    bad_alpha = bytearray(_pixels())
    bad_alpha[3] = 254
    cases = (bytes(bad_alpha), _pixels()[:-1], _pixels() + b"x")
    for payload in cases:
        with pytest.raises(ImagePageError) as captured:
            PillowImageTransformer().transform(
                BytesIO(payload),
                BytesIO(),
                source_layout=_layout(),
                intent=_intent(),
                maximum_width_pixels=4,
                maximum_height_pixels=3,
                limits=_limits(),
                control=_Control(),
            )
        assert captured.value.code is ImagePageErrorCode.SOURCE_INVALID


def test_dependency_version_is_checked_before_source_access() -> None:
    source = BytesIO(_pixels())
    with patch("paperworkshell.imaging.pillow_transformer.PIL.__version__", "0.0.0"):
        with pytest.raises(ImagePageError) as captured:
            PillowImageTransformer().transform(
                source,
                BytesIO(),
                source_layout=_layout(),
                intent=_intent(),
                maximum_width_pixels=4,
                maximum_height_pixels=3,
                limits=_limits(),
                control=_Control(),
            )

    assert captured.value.code is ImagePageErrorCode.DEPENDENCY_UNAVAILABLE
    assert source.tell() == 0


def test_planner_rejects_memory_and_temp_limit_before_source_read() -> None:
    plan = plan_image_transform(_layout(), _intent(), 4, 3, _limits())
    source = BytesIO(_pixels())
    for changes in (
        {"maximum_memory_bytes": plan.estimated_peak_memory_bytes - 1},
        {"maximum_temporary_storage_bytes": plan.required_temporary_storage_bytes - 1},
    ):
        source.seek(5)
        with pytest.raises(ImagePageError) as captured:
            PillowImageTransformer().transform(
                source,
                BytesIO(),
                source_layout=_layout(),
                intent=_intent(),
                maximum_width_pixels=4,
                maximum_height_pixels=3,
                limits=_limits(**changes),
                control=_Control(),
            )
        assert captured.value.code is ImagePageErrorCode.LIMIT_EXCEEDED
        assert source.tell() == 5


def test_partial_destination_write_is_supported_and_zero_write_is_rejected() -> None:
    class _ShortWriter(BytesIO):
        def write(self, payload: Buffer, /) -> int:
            copied = bytes(payload)
            return super().write(copied[: max(1, len(copied) // 2)])

    destination = _ShortWriter()
    result = PillowImageTransformer().transform(
        BytesIO(_pixels()),
        destination,
        source_layout=_layout(),
        intent=_intent(),
        maximum_width_pixels=4,
        maximum_height_pixels=3,
        limits=_limits(),
        control=_Control(),
    )
    assert destination.getvalue() == _pixels()
    assert result.pixel_sha256 == hashlib.sha256(_pixels()).hexdigest()

    class _ZeroWriter(BytesIO):
        def write(self, payload: Buffer, /) -> int:
            del payload
            return 0

    with pytest.raises(ImagePageError) as captured:
        PillowImageTransformer().transform(
            BytesIO(_pixels()),
            _ZeroWriter(),
            source_layout=_layout(),
            intent=_intent(),
            maximum_width_pixels=4,
            maximum_height_pixels=3,
            limits=_limits(),
            control=_Control(),
        )
    assert captured.value.code is ImagePageErrorCode.DESTINATION_FAILED

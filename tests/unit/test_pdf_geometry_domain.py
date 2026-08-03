from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest

from paperworkshell.domain.pdf_geometry import (
    MAX_PDF_PAGE_COORDINATE_MAGNITUDE_UNITS,
    MAX_PDF_PAGE_SPAN_UNITS,
    MAX_PDF_RENDER_SCALE_PIXELS_PER_UNIT,
    MAX_PDF_USER_UNIT,
    PdfPageBoxInsets,
    PdfPageBoxTransform,
    PdfPageGeometryLimits,
    PdfPageRectangle,
    PdfResolvedPageGeometry,
    combine_pdf_rotations,
    fit_pdf_page_geometry,
    resolve_pdf_page_geometry,
)
from paperworkshell.domain.pdf_rendering import (
    PdfPageBox,
    PdfPageRenderLimits,
    PdfPageRenderRequest,
    PdfRenderError,
    PdfRenderErrorCode,
    PdfRenderRotation,
)


def _geometry_limits(**changes: int) -> PdfPageGeometryLimits:
    values = {
        "maximum_coordinate_magnitude_units": 1_000_000,
        "maximum_box_span_units": 1_000_000,
    }
    values.update(changes)
    return PdfPageGeometryLimits(**values)


def _render_limits(**changes: int) -> PdfPageRenderLimits:
    values = {
        "maximum_pages": 10,
        "maximum_width_pixels": 2_000,
        "maximum_height_pixels": 2_000,
        "maximum_pixels": 4_000_000,
        "maximum_bitmap_bytes": 16_000_000,
        "maximum_destination_bytes": 16_000_000,
        "maximum_memory_bytes": 16_000_000,
        "wall_time_ms": 10_000,
    }
    values.update(changes)
    return PdfPageRenderLimits(**values)


def _resolved(
    *,
    media: tuple[object, ...] = (0, 0, 200, 100),
    crop: tuple[object, ...] | None = None,
    rotation: object | None = None,
    user_unit: object | None = None,
    page_box: PdfPageBox = PdfPageBox.CROP,
) -> PdfResolvedPageGeometry:
    return resolve_pdf_page_geometry(
        media,
        crop,
        rotation,
        user_unit,
        page_box,
        _geometry_limits(),
    )


def test_media_and_crop_boxes_are_resolved_and_intersected_in_pdf_space() -> None:
    geometry = _resolved(
        media=(0, 0, 200, 100),
        crop=(-20, 10, 150, 120),
    )

    assert geometry.media_box == PdfPageRectangle(0.0, 0.0, 200.0, 100.0)
    assert geometry.crop_box == PdfPageRectangle(-20.0, 10.0, 150.0, 120.0)
    assert geometry.effective_box == PdfPageRectangle(0.0, 10.0, 150.0, 100.0)


def test_absent_crop_box_falls_back_to_media_box() -> None:
    geometry = _resolved(media=(-10, -20, 300, 400))

    assert geometry.crop_box is geometry.media_box
    assert geometry.effective_box == geometry.media_box


@pytest.mark.parametrize(
    "box",
    (
        (200, 0, 0, 100),
        (0, 100, 200, 0),
        (200, 100, 0, 0),
    ),
)
def test_pdf_rectangles_accept_and_normalize_reversed_corner_pairs(
    box: tuple[object, ...],
) -> None:
    geometry = _resolved(media=box)

    assert geometry.media_box == PdfPageRectangle(0.0, 0.0, 200.0, 100.0)


@pytest.mark.parametrize(
    ("box", "context"),
    (
        ((0, 0, 100), "render.geometry.media_box"),
        ((0, 0, 0, 100), "render.geometry.media_box"),
        ((0, 0, 100, 0), "render.geometry.media_box"),
        ((0, 0, True, 100), "render.geometry.media_box"),
        ((0, 0, "100", 100), "render.geometry.media_box"),
        ((0, 0, float("nan"), 100), "render.geometry.media_box"),
        ((0, 0, float("inf"), 100), "render.geometry.media_box"),
    ),
)
def test_invalid_or_empty_pdf_rectangles_fail_with_stable_geometry_error(
    box: tuple[object, ...],
    context: str,
) -> None:
    with pytest.raises(PdfRenderError) as raised:
        _resolved(media=box)

    assert raised.value.code is PdfRenderErrorCode.GEOMETRY_INVALID
    assert raised.value.context == context


def test_disjoint_crop_is_rejected_only_when_crop_is_the_selected_page_box() -> None:
    with pytest.raises(PdfRenderError) as raised:
        _resolved(crop=(300, 300, 400, 400), page_box=PdfPageBox.CROP)

    assert raised.value.code is PdfRenderErrorCode.GEOMETRY_INVALID
    assert raised.value.context == "render.geometry.crop_intersection"

    media_geometry = _resolved(crop=(300, 300, 400, 400), page_box=PdfPageBox.MEDIA)
    assert media_geometry.effective_box is media_geometry.media_box


@pytest.mark.parametrize(
    ("rotation", "expected"),
    (
        (None, PdfRenderRotation.DEGREES_0),
        (0, PdfRenderRotation.DEGREES_0),
        (450, PdfRenderRotation.DEGREES_90),
        (-90, PdfRenderRotation.DEGREES_270),
        (720, PdfRenderRotation.DEGREES_0),
    ),
)
def test_intrinsic_rotation_is_an_integer_quarter_turn_normalized_modulo_360(
    rotation: object | None,
    expected: PdfRenderRotation,
) -> None:
    assert _resolved(rotation=rotation).intrinsic_rotation is expected


@pytest.mark.parametrize(
    "rotation",
    (45, 90.0, True, "90", pytest.param(10**10_000, id="huge-integer")),
)
def test_non_integer_non_quarter_or_excessive_rotation_is_rejected(rotation: object) -> None:
    with pytest.raises(PdfRenderError) as raised:
        _resolved(rotation=rotation)

    assert raised.value.code is PdfRenderErrorCode.GEOMETRY_INVALID
    assert raised.value.context == "render.geometry.rotate"


def test_user_unit_defaults_to_one_and_records_non_default_without_affecting_fit() -> None:
    default = _resolved(user_unit=None)
    non_default = _resolved(user_unit=2.5)
    request = PdfPageRenderRequest(0, 123, 100)

    default_plan = fit_pdf_page_geometry(default, request, _render_limits())
    non_default_plan = fit_pdf_page_geometry(non_default, request, _render_limits())

    assert default.user_unit == 1.0
    assert not default.uses_non_default_user_unit
    assert non_default.user_unit == 2.5
    assert non_default.uses_non_default_user_unit
    assert non_default_plan.layout == default_plan.layout
    assert non_default_plan.scale_pixels_per_unit == default_plan.scale_pixels_per_unit


@pytest.mark.parametrize(
    "user_unit",
    (
        0,
        -1,
        float("nan"),
        float("inf"),
        True,
        "1",
        MAX_PDF_USER_UNIT + 0.1,
        pytest.param(10**10_000, id="huge-integer"),
    ),
)
def test_invalid_user_unit_is_rejected_without_conversion_overflow(user_unit: object) -> None:
    with pytest.raises(PdfRenderError) as raised:
        _resolved(user_unit=user_unit)

    assert raised.value.code is PdfRenderErrorCode.GEOMETRY_INVALID
    assert raised.value.context == "render.geometry.user_unit"


@pytest.mark.parametrize(
    ("changes", "value"),
    (
        ({"maximum_coordinate_magnitude_units": 0}, 0),
        (
            {"maximum_coordinate_magnitude_units": MAX_PDF_PAGE_COORDINATE_MAGNITUDE_UNITS + 1},
            0,
        ),
        ({"maximum_box_span_units": MAX_PDF_PAGE_SPAN_UNITS + 1}, 0),
        ({"maximum_box_span_units": True}, 0),
    ),
)
def test_geometry_limits_are_positive_bounded_integers(
    changes: dict[str, object],
    value: int,
) -> None:
    del value
    with pytest.raises(PdfRenderError) as raised:
        replace(_geometry_limits(), **cast(Any, changes))

    assert raised.value.code is PdfRenderErrorCode.MODEL_INVALID
    assert raised.value.context == "render.geometry_limits"


def test_coordinate_and_span_policy_limits_fail_before_large_number_conversion() -> None:
    with pytest.raises(PdfRenderError) as coordinate_error:
        resolve_pdf_page_geometry(
            (0, 0, 10**10_000, 100),
            None,
            None,
            None,
            PdfPageBox.CROP,
            _geometry_limits(maximum_coordinate_magnitude_units=1_000),
        )
    assert coordinate_error.value.code is PdfRenderErrorCode.LIMIT_EXCEEDED
    assert coordinate_error.value.context == "render.geometry.media_box"

    with pytest.raises(PdfRenderError) as span_error:
        resolve_pdf_page_geometry(
            (-400, 0, 400, 100),
            None,
            None,
            None,
            PdfPageBox.CROP,
            _geometry_limits(maximum_box_span_units=500),
        )
    assert span_error.value.code is PdfRenderErrorCode.LIMIT_EXCEEDED
    assert span_error.value.context == "render.geometry.media_box"


def test_exact_fraction_ceil_fit_is_deterministic_and_stays_within_request() -> None:
    geometry = _resolved(media=(0, 0, 200, 100))
    plan = fit_pdf_page_geometry(
        geometry,
        PdfPageRenderRequest(0, 123, 100),
        _render_limits(),
    )

    assert plan.layout.width_pixels == 123
    assert plan.layout.height_pixels == 62
    assert plan.scale_pixels_per_unit == pytest.approx(0.615)
    assert plan.layout.width_pixels <= 123
    assert plan.layout.height_pixels <= 100


def test_intrinsic_and_requested_quarter_turns_swap_fit_axes() -> None:
    geometry = _resolved(media=(0, 0, 200, 100), rotation=90)
    request = PdfPageRenderRequest(
        0,
        80,
        160,
        additional_rotation=PdfRenderRotation.DEGREES_180,
    )

    plan = fit_pdf_page_geometry(geometry, request, _render_limits())

    assert plan.combined_rotation is PdfRenderRotation.DEGREES_270
    assert plan.rotated_width_units == 100.0
    assert plan.rotated_height_units == 200.0
    assert (plan.layout.width_pixels, plan.layout.height_pixels) == (80, 160)


@pytest.mark.parametrize(
    ("intrinsic", "additional", "expected"),
    (
        (PdfRenderRotation.DEGREES_0, PdfRenderRotation.DEGREES_270, 270),
        (PdfRenderRotation.DEGREES_90, PdfRenderRotation.DEGREES_270, 0),
        (PdfRenderRotation.DEGREES_180, PdfRenderRotation.DEGREES_270, 90),
        (PdfRenderRotation.DEGREES_270, PdfRenderRotation.DEGREES_270, 180),
    ),
)
def test_quarter_turn_combination_is_modulo_360(
    intrinsic: PdfRenderRotation,
    additional: PdfRenderRotation,
    expected: int,
) -> None:
    assert int(combine_pdf_rotations(intrinsic, additional)) == expected


def test_fit_reuses_checked_raster_overflow_and_allocation_limits() -> None:
    geometry = _resolved(media=(0, 0, 200, 100))

    with pytest.raises(PdfRenderError) as raised:
        fit_pdf_page_geometry(
            geometry,
            PdfPageRenderRequest(0, 200, 100),
            _render_limits(maximum_pixels=19_999),
        )

    assert raised.value.code is PdfRenderErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "render.pixels"


def test_tiny_page_scale_is_hard_bounded_without_fraction_conversion_overflow() -> None:
    geometry = _resolved(media=(0, 0, 1e-305, 1e-305))

    with pytest.raises(PdfRenderError) as raised:
        fit_pdf_page_geometry(
            geometry,
            PdfPageRenderRequest(0, 2_000, 2_000),
            _render_limits(),
        )

    assert MAX_PDF_RENDER_SCALE_PIXELS_PER_UNIT == 1_000_000
    assert raised.value.code is PdfRenderErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "render.geometry.scale"


@pytest.mark.parametrize(
    ("rotation", "maximum", "expected_corners", "expected_insets"),
    (
        (
            PdfRenderRotation.DEGREES_0,
            (160, 70),
            ((0, 70), (0, 0), (160, 70), (160, 0)),
            PdfPageBoxInsets(10.0, 20.0, 30.0, 10.0),
        ),
        (
            PdfRenderRotation.DEGREES_90,
            (70, 160),
            ((0, 0), (70, 0), (0, 160), (70, 160)),
            PdfPageBoxInsets(20.0, 30.0, 10.0, 10.0),
        ),
        (
            PdfRenderRotation.DEGREES_180,
            (160, 70),
            ((160, 0), (160, 70), (0, 0), (0, 70)),
            PdfPageBoxInsets(30.0, 10.0, 10.0, 20.0),
        ),
        (
            PdfRenderRotation.DEGREES_270,
            (70, 160),
            ((70, 160), (0, 160), (70, 0), (0, 0)),
            PdfPageBoxInsets(10.0, 10.0, 20.0, 30.0),
        ),
    ),
)
def test_box_transform_and_post_rotation_insets_cover_every_quarter_turn(
    rotation: PdfRenderRotation,
    maximum: tuple[int, int],
    expected_corners: tuple[tuple[int, int], ...],
    expected_insets: PdfPageBoxInsets,
) -> None:
    geometry = _resolved(
        media=(0, 0, 200, 100),
        crop=(10, 20, 170, 90),
        rotation=int(rotation),
    )
    plan = fit_pdf_page_geometry(
        geometry,
        PdfPageRenderRequest(0, *maximum),
        _render_limits(),
    )
    box = geometry.effective_box
    corners = (
        plan.transform.map_point(box.left, box.bottom),
        plan.transform.map_point(box.left, box.top),
        plan.transform.map_point(box.right, box.bottom),
        plan.transform.map_point(box.right, box.top),
    )

    for actual, expected in zip(corners, expected_corners, strict=True):
        assert actual == pytest.approx(expected)
    assert plan.post_rotation_insets == expected_insets


def test_transform_rejects_huge_input_and_nonfinite_computed_output() -> None:
    identity = PdfPageBoxTransform(1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    with pytest.raises(PdfRenderError) as huge_input:
        identity.map_point(10**10_000, 0)
    assert huge_input.value.code is PdfRenderErrorCode.MODEL_INVALID
    assert huge_input.value.context == "render.geometry.point"

    overflowing = PdfPageBoxTransform(1e308, 0.0, 0.0, 0.0, 1.0, 0.0)
    with pytest.raises(PdfRenderError) as mapped_overflow:
        overflowing.map_point(MAX_PDF_PAGE_COORDINATE_MAGNITUDE_UNITS, 0)
    assert mapped_overflow.value.code is PdfRenderErrorCode.LIMIT_EXCEEDED
    assert mapped_overflow.value.context == "render.geometry.transform_result"


def test_geometry_representations_hide_pdf_values_and_user_unit() -> None:
    geometry = _resolved(
        media=(1234.567, 20, 1434.567, 120),
        crop=(1240, 30, 1400, 100),
        user_unit=2.5,
    )
    plan = fit_pdf_page_geometry(
        geometry,
        PdfPageRenderRequest(0, 160, 70),
        _render_limits(),
    )

    representations = (
        repr(geometry.media_box),
        repr(geometry),
        repr(plan.post_rotation_insets),
        repr(plan.transform),
        repr(plan),
    )
    assert all("1234.567" not in value and "2.5" not in value for value in representations)

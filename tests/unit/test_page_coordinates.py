from __future__ import annotations

import json
import math
import random
from collections.abc import Callable, Iterator
from functools import partial
from pathlib import Path

import pytest
from PIL import Image

from paperworkshell.domain.page_coordinates import (
    NORMALIZED_PAGE_COORDINATE_SPACE,
    NORMALIZED_PAGE_MILLIONTHS,
    BoxQuantization,
    ClockwiseRotation,
    DevicePixelPoint,
    NormalizedBoxClipPolicy,
    NormalizedPageBox,
    NormalizedPagePoint,
    PageCoordinateError,
    PageCoordinateErrorCode,
    PageCoordinateTransform,
    PdfPageBoxSelection,
    PdfPageCoordinateGeometry,
    PdfTypographicPoint,
    PdfUserSpaceBox,
    PdfUserSpacePoint,
    RenderedImagePoint,
    RenderedImageSize,
    ScreenPoint,
    ScreenViewport,
)
from paperworkshell.domain.page_operations import (
    NormalizedPageBox as PageOperationNormalizedPageBox,
)

_ROOT = Path(__file__).parents[2]
_EXAMPLES = _ROOT / "schemas" / "examples"
_GOLDENS = _ROOT / "tests" / "golden" / "renderer-v1"


def _assert_error(
    code: PageCoordinateErrorCode,
    action: Callable[[], object],
) -> PageCoordinateError:
    with pytest.raises(PageCoordinateError) as raised:
        action()
    assert raised.value.code is code
    return raised.value


def _geometry(
    rotation: ClockwiseRotation = ClockwiseRotation.DEGREES_0,
    *,
    user_unit: float = 1.0,
    page_box: PdfPageBoxSelection = PdfPageBoxSelection.CROP,
) -> PdfPageCoordinateGeometry:
    return PdfPageCoordinateGeometry(
        media_box=PdfUserSpaceBox(-20.0, -10.0, 520.0, 860.0),
        crop_box=PdfUserSpaceBox(10.0, 150.0, 510.0, 850.0),
        page_box=page_box,
        intrinsic_rotation=rotation,
        user_unit=user_unit,
    )


def _manifest_boxes(document: dict[str, object]) -> Iterator[dict[str, object]]:
    operations = document["page_operations"]
    assert type(operations) is list
    for operation in operations:
        assert type(operation) is dict
        payload = operation["payload"]
        assert type(payload) is dict
        box = payload.get("box")
        if type(box) is dict:
            yield box
    overlays = document["overlays"]
    assert type(overlays) is list
    for overlay in overlays:
        assert type(overlay) is dict
        box = overlay["box"]
        assert type(box) is dict
        yield box


def test_manifest_codec_is_exact_and_shared_by_operations_and_overlays() -> None:
    assert PageOperationNormalizedPageBox is NormalizedPageBox
    found = 0
    for version in (1, 2):
        document = json.loads(
            (_EXAMPLES / f"case-manifest-v{version}-representative.json").read_bytes()
        )
        assert type(document) is dict
        for raw_box in _manifest_boxes(document):
            decoded = NormalizedPageBox.from_manifest(raw_box)
            assert decoded.to_manifest() == raw_box
            assert decoded.coordinate_space == NORMALIZED_PAGE_COORDINATE_SPACE
            found += 1
    assert found >= 8


@pytest.mark.parametrize(
    "value",
    [
        {},
        {
            "coordinate_space": NORMALIZED_PAGE_COORDINATE_SPACE,
            "x": 0,
            "y": 0,
            "width": 1,
            "height": 1,
            "extra": 1,
        },
        {
            "coordinate_space": "future_coordinate_space",
            "x": 0,
            "y": 0,
            "width": 1,
            "height": 1,
        },
        {
            "coordinate_space": NORMALIZED_PAGE_COORDINATE_SPACE,
            "x": False,
            "y": 0,
            "width": 1,
            "height": 1,
        },
        {
            "coordinate_space": NORMALIZED_PAGE_COORDINATE_SPACE,
            "x": 0.0,
            "y": 0,
            "width": 1,
            "height": 1,
        },
        {
            "coordinate_space": NORMALIZED_PAGE_COORDINATE_SPACE,
            "x": 0,
            "y": 0,
            "width": 0,
            "height": 1,
        },
        {
            "coordinate_space": NORMALIZED_PAGE_COORDINATE_SPACE,
            "x": NORMALIZED_PAGE_MILLIONTHS,
            "y": 0,
            "width": 1,
            "height": 1,
        },
    ],
)
def test_manifest_codec_rejects_non_exact_or_invalid_boxes(value: dict[str, object]) -> None:
    _assert_error(
        PageCoordinateErrorCode.MANIFEST_INVALID,
        lambda: NormalizedPageBox.from_manifest(value),
    )


def test_normalized_box_requires_explicit_clipping_and_rejects_inversion() -> None:
    _assert_error(
        PageCoordinateErrorCode.BOX_INVALID,
        lambda: NormalizedPageBox.from_edges(-10, 20, 30, 40),
    )
    assert NormalizedPageBox.from_edges(
        -10,
        20,
        30,
        NORMALIZED_PAGE_MILLIONTHS + 50,
        clip_policy=NormalizedBoxClipPolicy.CLIP_TO_PAGE,
    ) == NormalizedPageBox(0, 20, 30, NORMALIZED_PAGE_MILLIONTHS - 20)
    for edges in ((30, 20, 10, 40), (10, 40, 30, 20), (-30, 10, -10, 20)):
        _assert_error(
            PageCoordinateErrorCode.BOX_INVALID,
            partial(
                NormalizedPageBox.from_edges,
                *edges,
                clip_policy=NormalizedBoxClipPolicy.CLIP_TO_PAGE,
            ),
        )


def test_box_intersection_is_explicit_and_does_not_change_persisted_intent() -> None:
    transform = PageCoordinateTransform(
        manifest_crop=NormalizedPageBox(200_000, 100_000, 500_000, 600_000)
    )
    intent = NormalizedPageBox(100_000, 200_000, 300_000, 600_000)
    before = intent.to_manifest()
    assert transform.visible_part(intent) == NormalizedPageBox(200_000, 200_000, 200_000, 500_000)
    assert intent.to_manifest() == before
    assert transform.visible_part(NormalizedPageBox(800_000, 800_000, 100_000, 100_000)) is None
    _assert_error(
        PageCoordinateErrorCode.BOX_INVALID,
        lambda: transform.normalized_box_to_rendered(intent, RenderedImageSize(500, 600)),
    )


@pytest.mark.parametrize(
    ("rotation", "expected"),
    [
        (
            ClockwiseRotation.DEGREES_0,
            ((10.0, 850.0), (510.0, 850.0), (510.0, 150.0), (10.0, 150.0)),
        ),
        (
            ClockwiseRotation.DEGREES_90,
            ((10.0, 150.0), (10.0, 850.0), (510.0, 850.0), (510.0, 150.0)),
        ),
        (
            ClockwiseRotation.DEGREES_180,
            ((510.0, 150.0), (10.0, 150.0), (10.0, 850.0), (510.0, 850.0)),
        ),
        (
            ClockwiseRotation.DEGREES_270,
            ((510.0, 850.0), (510.0, 150.0), (10.0, 150.0), (10.0, 850.0)),
        ),
    ],
)
def test_intrinsic_pdf_rotation_maps_normalized_corners_after_source_orientation(
    rotation: ClockwiseRotation,
    expected: tuple[tuple[float, float], ...],
) -> None:
    transform = PageCoordinateTransform(pdf_geometry=_geometry(rotation))
    corners = (
        NormalizedPagePoint(0, 0),
        NormalizedPagePoint(NORMALIZED_PAGE_MILLIONTHS, 0),
        NormalizedPagePoint(NORMALIZED_PAGE_MILLIONTHS, NORMALIZED_PAGE_MILLIONTHS),
        NormalizedPagePoint(0, NORMALIZED_PAGE_MILLIONTHS),
    )
    for point, raw in zip(corners, expected, strict=True):
        mapped = transform.normalized_to_pdf(point)
        assert (mapped.x, mapped.y) == raw
        assert transform.pdf_to_normalized(mapped) == point


def test_crop_then_manifest_rotation_then_raster_then_screen_worked_example() -> None:
    transform = PageCoordinateTransform(
        pdf_geometry=_geometry(),
        manifest_crop=NormalizedPageBox(100_000, 200_000, 600_000, 500_000),
        manifest_rotation=ClockwiseRotation.DEGREES_90,
    )
    point = NormalizedPagePoint(250_000, 400_000)
    pdf = transform.normalized_to_pdf(point)
    assert (pdf.x, pdf.y) == (135.0, 570.0)

    size = RenderedImageSize(700, 600)
    rendered = transform.normalized_to_rendered(point, size)
    assert (rendered.x, rendered.y) == (420.0, 150.0)

    viewport = ScreenViewport(40.0, 30.0, 350.0, 300.0, 1.5)
    screen = transform.rendered_to_screen(rendered, size, viewport)
    assert (screen.x, screen.y) == (250.0, 105.0)
    assert transform.screen_to_normalized(screen, size, viewport) == point

    device = viewport.logical_to_device(screen)
    assert device == DevicePixelPoint(375.0, 157.5)
    assert viewport.device_to_logical(device) == screen
    assert (
        transform.normalized_to_pdf(transform.screen_to_normalized(screen, size, viewport)) == pdf
    )


def test_intrinsic_orientation_precedes_manifest_crop_and_user_rotation() -> None:
    transform = PageCoordinateTransform(
        pdf_geometry=_geometry(ClockwiseRotation.DEGREES_90),
        manifest_crop=NormalizedPageBox(100_000, 200_000, 600_000, 500_000),
        manifest_rotation=ClockwiseRotation.DEGREES_90,
    )
    point = NormalizedPagePoint(250_000, 400_000)

    assert transform.normalized_to_pdf(point) == PdfUserSpacePoint(210.0, 325.0)
    assert transform.normalized_to_rendered(point, RenderedImageSize(700, 600)) == (
        RenderedImagePoint(420.0, 150.0)
    )


@pytest.mark.parametrize(
    ("rotation", "expected"),
    [
        (ClockwiseRotation.DEGREES_0, (60.0, 40.0)),
        (ClockwiseRotation.DEGREES_90, (120.0, 30.0)),
        (ClockwiseRotation.DEGREES_180, (140.0, 60.0)),
        (ClockwiseRotation.DEGREES_270, (80.0, 70.0)),
    ],
)
def test_manifest_quarter_turns_use_actual_raster_edges(
    rotation: ClockwiseRotation,
    expected: tuple[float, float],
) -> None:
    transform = PageCoordinateTransform(manifest_rotation=rotation)
    mapped = transform.normalized_to_rendered(
        NormalizedPagePoint(300_000, 400_000),
        RenderedImageSize(200, 100),
    )
    assert (mapped.x, mapped.y) == expected


def test_pdf_media_crop_intersection_and_mixed_page_sizes_are_isolated() -> None:
    crop_geometry = PdfPageCoordinateGeometry(
        media_box=PdfUserSpaceBox(20, 30, 620, 830),
        crop_box=PdfUserSpaceBox(-10, 100, 500, 900),
        page_box=PdfPageBoxSelection.CROP,
        intrinsic_rotation=ClockwiseRotation.DEGREES_0,
    )
    media_geometry = PdfPageCoordinateGeometry(
        media_box=PdfUserSpaceBox(20, 30, 620, 830),
        crop_box=PdfUserSpaceBox(-10, 100, 500, 900),
        page_box=PdfPageBoxSelection.MEDIA,
        intrinsic_rotation=ClockwiseRotation.DEGREES_0,
    )
    assert crop_geometry.effective_box == PdfUserSpaceBox(20, 100, 500, 830)
    assert media_geometry.effective_box == PdfUserSpaceBox(20, 30, 620, 830)

    center = NormalizedPagePoint(500_000, 500_000)
    crop_point = PageCoordinateTransform(crop_geometry).normalized_to_pdf(center)
    media_point = PageCoordinateTransform(media_geometry).normalized_to_pdf(center)
    assert crop_point == PdfUserSpacePoint(260, 465)
    assert media_point == PdfUserSpacePoint(320, 430)

    letter = PageCoordinateTransform(
        PdfPageCoordinateGeometry(
            PdfUserSpaceBox(0, 0, 612, 792),
            None,
            PdfPageBoxSelection.MEDIA,
            ClockwiseRotation.DEGREES_0,
        )
    )
    a4 = PageCoordinateTransform(
        PdfPageCoordinateGeometry(
            PdfUserSpaceBox(-25, 40, 570, 882),
            None,
            PdfPageBoxSelection.MEDIA,
            ClockwiseRotation.DEGREES_0,
        )
    )
    assert letter.normalized_to_pdf(center) == PdfUserSpacePoint(306, 396)
    assert a4.normalized_to_pdf(center) == PdfUserSpacePoint(272.5, 461)


def test_user_unit_is_explicit_and_does_not_change_normalized_placement() -> None:
    raw = PdfUserSpacePoint(18.0, 12.0)
    unit_one = PdfPageCoordinateGeometry(
        PdfUserSpaceBox(0, 0, 36, 24),
        None,
        PdfPageBoxSelection.MEDIA,
        ClockwiseRotation.DEGREES_0,
        1.0,
    )
    unit_two = PdfPageCoordinateGeometry(
        PdfUserSpaceBox(0, 0, 36, 24),
        None,
        PdfPageBoxSelection.MEDIA,
        ClockwiseRotation.DEGREES_0,
        2.0,
    )
    assert unit_one.user_to_typographic(raw) == PdfTypographicPoint(18.0, 12.0)
    assert unit_two.user_to_typographic(raw) == PdfTypographicPoint(36.0, 24.0)
    assert unit_two.typographic_to_user(PdfTypographicPoint(36.0, 24.0)) == raw
    assert PageCoordinateTransform(unit_one).pdf_to_normalized(raw) == PageCoordinateTransform(
        unit_two
    ).pdf_to_normalized(raw)


def test_user_unit_boundary_round_trip_normalizes_only_proved_float_residue() -> None:
    box = PdfUserSpaceBox(
        -66_413_478.42430927,
        -7_696_744.808554351,
        -65_640_230.12609849,
        -7_357_496.182442562,
    )
    geometry = PdfPageCoordinateGeometry(
        box,
        None,
        PdfPageBoxSelection.MEDIA,
        ClockwiseRotation.DEGREES_0,
        1.5937029058214372e-60,
    )
    raw = PdfUserSpacePoint(-65_897_815.16290761, box.bottom)

    recovered = geometry.typographic_to_user(geometry.user_to_typographic(raw))
    assert recovered.y == box.bottom
    assert math.isclose(recovered.x, raw.x, rel_tol=0, abs_tol=2 * math.ulp(raw.x))


def test_randomized_round_trips_cover_boxes_rotations_scales_and_nonzero_origins() -> None:
    randomizer = random.Random(0x0301)
    for index in range(800):
        left = randomizer.uniform(-50_000.0, 50_000.0)
        bottom = randomizer.uniform(-50_000.0, 50_000.0)
        width = randomizer.uniform(20.0, 20_000.0)
        height = randomizer.uniform(20.0, 20_000.0)
        media = PdfUserSpaceBox(left - 5, bottom - 7, left + width + 11, bottom + height + 13)
        crop = PdfUserSpaceBox(left, bottom, left + width, bottom + height)
        intrinsic = ClockwiseRotation((index % 4) * 90)
        manifest_rotation = ClockwiseRotation(((index // 4) % 4) * 90)
        crop_left = randomizer.randint(0, 250_000)
        crop_top = randomizer.randint(0, 250_000)
        crop_right = randomizer.randint(750_000, NORMALIZED_PAGE_MILLIONTHS)
        crop_bottom = randomizer.randint(750_000, NORMALIZED_PAGE_MILLIONTHS)
        manifest_crop = NormalizedPageBox.from_edges(
            crop_left,
            crop_top,
            crop_right,
            crop_bottom,
        )
        transform = PageCoordinateTransform(
            PdfPageCoordinateGeometry(
                media,
                crop,
                PdfPageBoxSelection.CROP,
                intrinsic,
                randomizer.uniform(0.25, 4.0),
            ),
            manifest_crop,
            manifest_rotation,
        )
        point = NormalizedPagePoint(
            randomizer.randint(crop_left, crop_right),
            randomizer.randint(crop_top, crop_bottom),
        )
        size = RenderedImageSize(
            randomizer.randint(17, 4_000),
            randomizer.randint(17, 4_000),
        )
        viewport = ScreenViewport(
            randomizer.uniform(-500.0, 500.0),
            randomizer.uniform(-500.0, 500.0),
            randomizer.uniform(10.0, 2_000.0),
            randomizer.uniform(10.0, 2_000.0),
            randomizer.choice((1.0, 1.25, 1.5, 1.75, 2.0)),
        )

        pdf_round_trip = transform.pdf_to_normalized(transform.normalized_to_pdf(point))
        rendered_round_trip = transform.rendered_to_normalized(
            transform.normalized_to_rendered(point, size),
            size,
        )
        screen_round_trip = transform.screen_to_normalized(
            transform.normalized_to_screen(point, size, viewport),
            size,
            viewport,
        )
        for recovered in (pdf_round_trip, rendered_round_trip, screen_round_trip):
            assert abs(recovered.x - point.x) <= 1
            assert abs(recovered.y - point.y) <= 1

        screen = transform.normalized_to_screen(point, size, viewport)
        logical = viewport.device_to_logical(viewport.logical_to_device(screen))
        assert math.isclose(logical.x, screen.x, rel_tol=1e-14, abs_tol=1e-10)
        assert math.isclose(logical.y, screen.y, rel_tol=1e-14, abs_tol=1e-10)

        raw = PdfUserSpacePoint(
            randomizer.uniform(crop.left, crop.right),
            randomizer.uniform(crop.bottom, crop.top),
        )
        recovered_raw = transform.normalized_to_pdf(transform.pdf_to_normalized(raw))
        tolerance = transform.pdf_round_trip_tolerance()
        assert abs(recovered_raw.x - raw.x) <= tolerance.x_user_units
        assert abs(recovered_raw.y - raw.y) <= tolerance.y_user_units


@pytest.mark.parametrize("rotation", tuple(ClockwiseRotation))
def test_box_round_trip_and_outward_quantization(rotation: ClockwiseRotation) -> None:
    transform = PageCoordinateTransform(
        manifest_crop=NormalizedPageBox(100_000, 200_000, 800_000, 700_000),
        manifest_rotation=rotation,
    )
    original = NormalizedPageBox(250_000, 300_000, 400_000, 350_000)
    size = RenderedImageSize(1_600, 1_400)
    rendered = transform.normalized_box_to_rendered(original, size)
    outward = transform.rendered_box_to_normalized(rendered, size)
    assert outward.x <= original.x
    assert outward.y <= original.y
    assert outward.right >= original.right
    assert outward.bottom >= original.bottom
    assert original.x - outward.x <= 1
    assert original.y - outward.y <= 1
    assert outward.right - original.right <= 1
    assert outward.bottom - original.bottom <= 1
    nearest = transform.rendered_box_to_normalized(
        rendered,
        size,
        quantization=BoxQuantization.NEAREST,
    )
    assert nearest == original


def test_screen_box_round_trip_uses_page_content_viewport_not_outer_widget() -> None:
    transform = PageCoordinateTransform(
        manifest_crop=NormalizedPageBox(100_000, 200_000, 800_000, 700_000),
        manifest_rotation=ClockwiseRotation.DEGREES_90,
    )
    original = NormalizedPageBox(250_000, 300_000, 400_000, 350_000)
    size = RenderedImageSize(1_600, 1_400)
    viewport = ScreenViewport(125, 75, 800, 700, 2.0)

    screen = transform.normalized_box_to_screen(original, size, viewport)
    assert (screen.left, screen.top, screen.right, screen.bottom) == pytest.approx(
        (410.7142857142857, 206.25, 810.7142857142857, 556.25)
    )
    assert (
        transform.screen_box_to_normalized(
            screen,
            size,
            viewport,
            quantization=BoxQuantization.NEAREST,
        )
        == original
    )


def test_screen_right_and_bottom_edges_round_trip_exactly_after_float_mapping() -> None:
    crop = NormalizedPageBox.from_edges(313_230, 71_713, 865_126, 682_421)
    transform = PageCoordinateTransform(manifest_crop=crop)
    size = RenderedImageSize(7_788, 1_360)
    viewport = ScreenViewport(
        886.8134467023067,
        -557.6504265116826,
        3_288.7952778387466,
        1_518.9889027904678,
        2.0,
    )
    point = NormalizedPagePoint(crop.right, 639_726)

    screen = transform.normalized_to_screen(point, size, viewport)
    assert screen.x == viewport.right
    assert transform.screen_to_normalized(screen, size, viewport) == point

    box = NormalizedPageBox(865_100, 639_726, 26, 18_474)
    screen_box = transform.normalized_box_to_screen(box, size, viewport)
    assert (
        transform.screen_box_to_normalized(
            screen_box,
            size,
            viewport,
            quantization=BoxQuantization.NEAREST,
        )
        == box
    )


def test_strict_points_scales_rotations_and_geometry_fail_closed() -> None:
    for point in (
        lambda: NormalizedPagePoint(False, 0),
        lambda: NormalizedPagePoint(-1, 0),
        lambda: NormalizedPagePoint(0, NORMALIZED_PAGE_MILLIONTHS + 1),
    ):
        _assert_error(PageCoordinateErrorCode.POINT_OUT_OF_BOUNDS, point)
    for value in (-90, 45, 360, False, 90.0):
        _assert_error(
            PageCoordinateErrorCode.ROTATION_INVALID,
            partial(ClockwiseRotation.from_degrees, value),
        )
    for ratio in (0.0, -1.0, math.nan, math.inf, True):
        _assert_error(
            PageCoordinateErrorCode.SCALE_INVALID
            if type(ratio) is float and math.isfinite(ratio)
            else PageCoordinateErrorCode.MODEL_INVALID
            if type(ratio) is bool
            else PageCoordinateErrorCode.GEOMETRY_INVALID,
            partial(ScreenViewport, 0, 0, 100, 100, ratio),
        )
    _assert_error(
        PageCoordinateErrorCode.GEOMETRY_INVALID,
        lambda: PdfUserSpaceBox(10, 0, 10, 20),
    )
    pdf_origin = 999_999_999.0
    pdf_ulp = math.ulp(pdf_origin)
    _assert_error(
        PageCoordinateErrorCode.GEOMETRY_INVALID,
        lambda: PdfUserSpaceBox(
            pdf_origin,
            pdf_origin,
            pdf_origin + pdf_ulp,
            pdf_origin + pdf_ulp,
        ),
    )
    _assert_error(
        PageCoordinateErrorCode.SCALE_INVALID,
        lambda: ScreenViewport(1_000_000_000.0, 0, 0.001, 100),
    )
    _assert_error(
        PageCoordinateErrorCode.SCALE_INVALID,
        lambda: ScreenViewport(1_000_000_000_000_000.0, 0, 0.01, 100),
    )
    _assert_error(
        PageCoordinateErrorCode.SCALE_INVALID,
        lambda: ScreenViewport(900_000_000_000_000.0, 0, 900_000_000_000_000.0, 100),
    )
    _assert_error(
        PageCoordinateErrorCode.SCALE_INVALID,
        lambda: ScreenViewport(900_000_000_000_000.0, 0, 10_000_000_000_000.0, 100, 2.0),
    )
    _assert_error(
        PageCoordinateErrorCode.SCALE_INVALID,
        lambda: ScreenViewport(0, 0, 100, 100, 5e-324),
    )
    _assert_error(
        PageCoordinateErrorCode.GEOMETRY_INVALID,
        lambda: PdfPageCoordinateGeometry(
            PdfUserSpaceBox(0, 0, 10, 10),
            PdfUserSpaceBox(20, 20, 30, 30),
            PdfPageBoxSelection.CROP,
            ClockwiseRotation.DEGREES_0,
        ),
    )
    _assert_error(
        PageCoordinateErrorCode.GEOMETRY_INVALID,
        lambda: PdfPageCoordinateGeometry(
            PdfUserSpaceBox(0, 0, 100, 100),
            None,
            PdfPageBoxSelection.MEDIA,
            ClockwiseRotation.DEGREES_0,
            5e-324,
        ),
    )

    transform = PageCoordinateTransform()
    _assert_error(
        PageCoordinateErrorCode.MODEL_INVALID,
        lambda: transform.normalized_to_pdf(NormalizedPagePoint(0, 0)),
    )
    _assert_error(
        PageCoordinateErrorCode.POINT_OUT_OF_BOUNDS,
        lambda: transform.rendered_to_normalized(
            RenderedImagePoint(101, 0),
            RenderedImageSize(100, 100),
        ),
    )
    _assert_error(
        PageCoordinateErrorCode.POINT_OUT_OF_BOUNDS,
        lambda: transform.screen_to_rendered(
            ScreenPoint(5, 5),
            RenderedImageSize(100, 100),
            ScreenViewport(10, 10, 100, 100),
        ),
    )


def test_known_pdf_quadrant_centers_land_on_approved_rotation_goldens() -> None:
    geometry_box = PdfUserSpaceBox(3, 5, 33, 25)
    raw_samples = {
        PdfUserSpacePoint(10.5, 20): (0, 0, 255),
        PdfUserSpacePoint(25.5, 20): (255, 255, 0),
        PdfUserSpacePoint(10.5, 10): (255, 0, 0),
        PdfUserSpacePoint(25.5, 10): (0, 255, 0),
    }
    for rotation in ClockwiseRotation:
        path = _GOLDENS / f"render-geometry-inherited-rotate-{int(rotation)}.png"
        with Image.open(path) as source:
            image = source.convert("RGB")
            size = RenderedImageSize(*image.size)
            geometry = PdfPageCoordinateGeometry(
                geometry_box,
                geometry_box,
                PdfPageBoxSelection.CROP,
                rotation,
            )
            transform = PageCoordinateTransform(geometry)
            for raw, expected_color in raw_samples.items():
                normalized = transform.pdf_to_normalized(raw)
                rendered = transform.normalized_to_rendered(normalized, size)
                pixel = (
                    min(int(rendered.x), image.width - 1),
                    min(int(rendered.y), image.height - 1),
                )
                assert image.getpixel(pixel) == expected_color


def test_manifest_rotation_matches_approved_additional_rotation_goldens() -> None:
    geometry_box = PdfUserSpaceBox(3, 5, 33, 25)
    raw_samples = {
        PdfUserSpacePoint(10.5, 20): (0, 0, 255),
        PdfUserSpacePoint(25.5, 20): (255, 255, 0),
        PdfUserSpacePoint(10.5, 10): (255, 0, 0),
        PdfUserSpacePoint(25.5, 10): (0, 255, 0),
    }
    geometry = PdfPageCoordinateGeometry(
        geometry_box,
        geometry_box,
        PdfPageBoxSelection.CROP,
        ClockwiseRotation.DEGREES_0,
    )
    for rotation in (
        ClockwiseRotation.DEGREES_90,
        ClockwiseRotation.DEGREES_180,
        ClockwiseRotation.DEGREES_270,
    ):
        path = _GOLDENS / f"render-geometry-additional-rotate-{int(rotation)}.png"
        with Image.open(path) as source:
            image = source.convert("RGB")
            size = RenderedImageSize(*image.size)
            transform = PageCoordinateTransform(geometry, manifest_rotation=rotation)
            for raw, expected_color in raw_samples.items():
                rendered = transform.normalized_to_rendered(
                    transform.pdf_to_normalized(raw),
                    size,
                )
                pixel = (
                    min(int(rendered.x), image.width - 1),
                    min(int(rendered.y), image.height - 1),
                )
                assert image.getpixel(pixel) == expected_color

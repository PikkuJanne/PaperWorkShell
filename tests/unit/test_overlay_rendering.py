"""Focused contract and live-adapter evidence for PWS-0303 overlay rendering."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, replace
from decimal import Decimal
from io import BytesIO
from math import ceil, floor
from typing import Final
from uuid import UUID

import pikepdf
import pypdfium2  # type: ignore[import-untyped]
import pytest

from paperworkshell.domain.page_coordinates import (
    ClockwiseRotation,
    NormalizedPageBox,
    NormalizedPagePoint,
    PageCoordinateTransform,
    PdfPageBoxSelection,
    PdfPageCoordinateGeometry,
    PdfUserSpaceBox,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.overlays.models import (
    DEFAULT_FONT_TOKEN,
    MarkOverlayPayload,
    OverlayAssetKind,
    OverlayAssetMediaType,
    OverlayAssetRecord,
    OverlayCollection,
    OverlayKind,
    OverlayRecord,
    RgbaColor,
    ShapeOverlayPayload,
    ShapePrimitive,
    SignatureImageOverlayPayload,
    TextualOverlayPayload,
)
from paperworkshell.overlays.rendering import (
    MAX_OVERLAY_CANVAS_POINTS,
    OverlayCanvasBox,
    OverlayPageGeometry,
    OverlayPageRenderRequest,
    OverlayPageRenderResult,
    OverlayPdfPlacementMatrix,
    OverlayRasterAsset,
    OverlayRenderError,
    OverlayRenderErrorCode,
    OverlayRenderLimits,
)
from paperworkshell.pdf.overlay_renderer import FpdfOverlayRenderer

PAGE_ID: Final = UUID("30000000-0000-4000-8000-000000000031")
ASSET_ID: Final = UUID("40000000-0000-4000-8000-000000000031")
FULL_PAGE: Final = NormalizedPageBox.full_page()


@dataclass(frozen=True, slots=True)
class _Control:
    cancelled: bool = False
    timed_out: bool = False

    def cancellation_requested(self) -> bool:
        return self.cancelled

    def deadline_expired(self) -> bool:
        return self.timed_out


class _RasterSource(BytesIO):
    def __init__(self, metadata: OverlayRasterAsset, value: bytes) -> None:
        super().__init__(value)
        self._metadata = metadata

    @property
    def metadata(self) -> OverlayRasterAsset:
        return self._metadata


def _overlay_id(index: int) -> UUID:
    return UUID(f"60000000-0000-4000-8000-{index:012x}")


def _record(
    index: int,
    payload: (
        TextualOverlayPayload
        | MarkOverlayPayload
        | SignatureImageOverlayPayload
        | ShapeOverlayPayload
    ),
    box: NormalizedPageBox,
) -> OverlayRecord:
    return OverlayRecord(_overlay_id(index), PAGE_ID, box, index, payload)


def _geometry(
    rotation: ClockwiseRotation = ClockwiseRotation.DEGREES_0,
) -> PdfPageCoordinateGeometry:
    media = PdfUserSpaceBox(10.0, 20.0, 330.0, 440.0)
    crop = PdfUserSpaceBox(20.0, 40.0, 320.0, 400.0)
    return PdfPageCoordinateGeometry(
        media,
        crop,
        PdfPageBoxSelection.CROP,
        rotation,
        user_unit=2.0,
    )


def _request(
    records: tuple[OverlayRecord, ...],
    *,
    assets: tuple[OverlayAssetRecord, ...] = (),
    active_crop: NormalizedPageBox = FULL_PAGE,
) -> OverlayPageRenderRequest:
    collection = OverlayCollection(
        assets,
        records,
        known_page_ids=(PAGE_ID,),
        active_page_ids=(PAGE_ID,),
    )
    return OverlayPageRenderRequest.from_collection(
        collection,
        PAGE_ID,
        geometry=OverlayPageGeometry.from_pdf_geometry(_geometry()),
        active_crop=active_crop,
    )


def _render(
    request: OverlayPageRenderRequest,
    *,
    raster_sources: dict[UUID, _RasterSource] | None = None,
    control: _Control | None = None,
) -> tuple[bytes, OverlayPageRenderResult]:
    destination = BytesIO()
    result = FpdfOverlayRenderer().render(
        request,
        {} if raster_sources is None else raster_sources,
        destination,
        OverlayRenderLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1),
        _Control() if control is None else control,
    )
    return destination.getvalue(), result


def _content_commands(data: bytes) -> tuple[tuple[str, tuple[str, ...]], ...]:
    with pikepdf.Pdf.open(BytesIO(data), attempt_recovery=False) as document:
        return tuple(
            (
                str(instruction.operator),
                tuple(str(operand) for operand in instruction.operands),
            )
            for instruction in pikepdf.parse_content_stream(document.pages[0])
        )


def _rendered_rgb(data: bytes) -> tuple[bytes, int, int]:
    document = pypdfium2.PdfDocument(data)
    try:
        page = document[0]
        try:
            bitmap = page.render(scale=1.0)
            try:
                image = bitmap.to_pil()
                try:
                    rgb_image = image.convert("RGB")
                    try:
                        width, height = rgb_image.size
                        return rgb_image.tobytes(), width, height
                    finally:
                        rgb_image.close()
                finally:
                    image.close()
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        document.close()


def _nonwhite_pixels(
    rgb: bytes,
    width: int,
    height: int,
    box: OverlayCanvasBox,
) -> int:
    left = max(0, floor(box.left))
    top = max(0, floor(box.top))
    right = min(width, ceil(box.right))
    bottom = min(height, ceil(box.bottom))
    return sum(
        rgb[offset : offset + 3] != b"\xff\xff\xff"
        for y in range(top, bottom)
        for offset in range((y * width + left) * 3, (y * width + right) * 3, 3)
    )


@pytest.mark.parametrize("rotation", tuple(ClockwiseRotation))
def test_overlay_pdf_placement_matches_normalized_coordinate_transform(
    rotation: ClockwiseRotation,
) -> None:
    pdf_geometry = _geometry(rotation)
    overlay_geometry = OverlayPageGeometry.from_pdf_geometry(pdf_geometry)
    transform = PageCoordinateTransform(pdf_geometry)

    for point in (
        NormalizedPagePoint(0, 0),
        NormalizedPagePoint(1_000_000, 0),
        NormalizedPagePoint(0, 1_000_000),
        NormalizedPagePoint(1_000_000, 1_000_000),
        NormalizedPagePoint(250_000, 750_000),
    ):
        emitted_x = overlay_geometry.canvas_width_points * point.x / 1_000_000
        emitted_y = overlay_geometry.canvas_height_points * (1 - point.y / 1_000_000)
        mapped = overlay_geometry.pdf_placement.apply(emitted_x, emitted_y)
        expected = transform.normalized_to_pdf(point)
        assert mapped.x == pytest.approx(expected.x)
        assert mapped.y == pytest.approx(expected.y)


@pytest.mark.parametrize(
    ("box", "expected"),
    (
        (
            NormalizedPageBox(500_000, 500_000, 1, 1),
            (300.0, 360.0, 300.01, 360.01),
        ),
        (
            NormalizedPageBox(8, 6, 1, 1),
            (0.0, 0.0, 0.01, 0.01),
        ),
        (
            NormalizedPageBox(999_999, 999_999, 1, 1),
            (599.99, 719.99, 600.0, 720.0),
        ),
        (
            NormalizedPageBox(100_000, 200_000, 300_000, 400_000),
            (60.0, 144.0, 240.0, 432.0),
        ),
    ),
)
def test_positive_normalized_boxes_have_noncollapsed_representational_canvas_boxes(
    box: NormalizedPageBox,
    expected: tuple[float, float, float, float],
) -> None:
    geometry = OverlayPageGeometry.from_pdf_geometry(_geometry())

    actual = geometry.box_for(box)

    assert (actual.left, actual.top, actual.right, actual.bottom) == expected


@pytest.mark.parametrize(
    ("x", "y"),
    (
        (True, 0.0),
        (0.0, False),
        (float("nan"), 0.0),
        (0.0, float("inf")),
        (MAX_OVERLAY_CANVAS_POINTS + 1, 0.0),
    ),
)
def test_overlay_pdf_placement_rejects_non_exact_or_unbounded_points(x: object, y: object) -> None:
    matrix = OverlayPdfPlacementMatrix(1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

    with pytest.raises(OverlayRenderError) as raised:
        matrix.apply(x, y)  # type: ignore[arg-type]

    assert raised.value.code is OverlayRenderErrorCode.GEOMETRY_INVALID


def test_resource_policy_factory_reserves_shared_working_set() -> None:
    policy = CONSTRAINED_RESOURCE_POLICY_V1
    limits = OverlayRenderLimits.from_resource_policy(policy)

    assert limits.maximum_total_raster_bytes <= policy.worker_process_memory_bytes // 32
    assert limits.maximum_output_bytes <= policy.worker_process_memory_bytes // 16
    assert limits.maximum_total_text_code_points <= policy.worker_process_memory_bytes // 1_024
    assert limits.maximum_overlays <= policy.worker_process_memory_bytes // 4_096
    assert limits.maximum_memory_bytes == policy.worker_process_memory_bytes


def test_resource_limit_model_rejects_an_overcommitted_shared_peak() -> None:
    limits = OverlayRenderLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1)

    with pytest.raises(OverlayRenderError) as raised:
        replace(
            limits,
            maximum_output_bytes=CONSTRAINED_RESOURCE_POLICY_V1.worker_process_memory_bytes // 12,
        )

    assert raised.value.code is OverlayRenderErrorCode.MODEL_INVALID


def test_live_renderer_embeds_and_extracts_representative_shaped_unicode() -> None:
    sample = "Résumé Ελληνικά Русский हिन्दी"
    request = _request(
        (
            _record(
                1,
                TextualOverlayPayload(
                    OverlayKind.TEXT,
                    sample,
                    DEFAULT_FONT_TOKEN,
                    20,
                    RgbaColor("#102030ff"),
                ),
                NormalizedPageBox(50_000, 50_000, 900_000, 120_000),
            ),
        )
    )

    first, first_result = _render(request)
    second, second_result = _render(request)

    assert first == second
    assert first_result == second_result
    assert first_result.embedded_font_count == 1
    assert first_result.placements[0].text_metrics is not None
    assert first_result.placements[0].text_metrics.glyph_count > 0
    document = pypdfium2.PdfDocument(first)
    try:
        page = document[0]
        text_page = page.get_textpage()
        try:
            assert text_page.get_text_range() == sample
        finally:
            text_page.close()
            page.close()
    finally:
        document.close()


def test_leading_negative_side_bearing_is_shifted_inside_without_clipping() -> None:
    request = _request(
        (
            _record(
                1,
                TextualOverlayPayload(
                    OverlayKind.TEXT,
                    "J",
                    DEFAULT_FONT_TOKEN,
                    20,
                    RgbaColor("#000000ff"),
                ),
                NormalizedPageBox(100_000, 100_000, 100_000, 100_000),
            ),
        )
    )

    _data, result = _render(request)

    placement = result.placements[0]
    painted = placement.painted_box
    metrics = placement.text_metrics
    assert painted is not None
    assert metrics is not None
    assert painted.left == placement.requested_box.left
    assert painted.width == pytest.approx(5.2)
    assert metrics.advance_width_points == 5.46
    assert metrics.ascent_points == 14.28
    assert metrics.descent_points == 3.8


def test_trailing_ink_overhang_shifts_only_enough_to_fit_the_right_edge() -> None:
    request = _request(
        (
            _record(
                1,
                TextualOverlayPayload(
                    OverlayKind.TEXT,
                    "f",
                    DEFAULT_FONT_TOKEN,
                    20,
                    RgbaColor("#000000ff"),
                ),
                # 12,500 millionths maps to an exact 7.50-point box.  The
                # glyph's 7.44-point ink fits, but its 7.74-point right edge at
                # the preferred logical origin does not.
                NormalizedPageBox(100_000, 100_000, 12_500, 100_000),
            ),
        )
    )

    _data, result = _render(request)

    placement = result.placements[0]
    painted = placement.painted_box
    metrics = placement.text_metrics
    assert painted is not None
    assert metrics is not None
    assert painted.left == pytest.approx(placement.requested_box.left + 0.06)
    assert painted.right == placement.requested_box.right
    assert painted.width == pytest.approx(7.44)
    assert metrics.advance_width_points == 6.88


def test_combining_mark_offsets_define_ink_metrics_and_preserve_exact_text() -> None:
    sample = "\u0418\u0301"
    request = _request(
        (
            _record(
                1,
                TextualOverlayPayload(
                    OverlayKind.TEXT,
                    sample,
                    DEFAULT_FONT_TOKEN,
                    20,
                    RgbaColor("#000000ff"),
                ),
                NormalizedPageBox(100_000, 100_000, 200_000, 100_000),
            ),
        )
    )

    first, first_result = _render(request)
    second, second_result = _render(request)

    assert first == second
    assert first_result == second_result
    placement = first_result.placements[0]
    painted = placement.painted_box
    metrics = placement.text_metrics
    assert painted is not None
    assert metrics is not None
    assert metrics.glyph_count == 2
    # The acute is positioned at x=-101/y=178 font units. Its absolute Tm is
    # emitted on the renderer's two-decimal grid and the returned metrics/
    # painted box conservatively enclose that rounding.
    assert metrics.ascent_points == 18.89
    assert metrics.descent_points == 0.0
    reported_baseline = Decimal(str(metrics.baseline_y_points))
    assert reported_baseline == reported_baseline.quantize(Decimal("0.01"))
    assert painted.height == pytest.approx(18.89)
    assert painted.top <= metrics.baseline_y_points - 18.88
    assert painted.bottom >= metrics.baseline_y_points
    matrices = tuple(
        operands for operator, operands in _content_commands(first) if operator == "Tm"
    )
    assert matrices == (
        ("1", "0", "0", "1", "73.34", "606.12"),
        ("1", "0", "0", "1", "75.36", "602.56"),
    )
    document = pypdfium2.PdfDocument(first)
    try:
        page = document[0]
        text_page = page.get_textpage()
        try:
            assert text_page.get_text_range() == sample
        finally:
            text_page.close()
            page.close()
    finally:
        document.close()


def test_forced_tm_rounding_rejects_raw_hindi_width_and_accepts_next_quantum() -> None:
    sample = "हिन्दी नमस्ते"

    def request(width_millionths: int) -> OverlayPageRenderRequest:
        return _request(
            (
                _record(
                    1,
                    TextualOverlayPayload(
                        OverlayKind.TEXT,
                        sample,
                        DEFAULT_FONT_TOKEN,
                        65,
                        RgbaColor("#000000ff"),
                    ),
                    NormalizedPageBox(
                        100_000,
                        100_000,
                        width_millionths,
                        200_000,
                    ),
                ),
            )
        )

    # 461,933 millionths quantizes to the exact raw 277.16-point
    # HarfBuzz-ink width. The forced Tm after the third glyph rounds its
    # 71.435-point pen position up to 71.44, so later ink exceeds that box.
    with pytest.raises(OverlayRenderError) as raised:
        _render(request(461_933))

    assert raised.value.code is OverlayRenderErrorCode.TEXT_DOES_NOT_FIT
    assert raised.value.context == "overlay.text_fit"

    data, result = _render(request(461_950))

    placement = result.placements[0]
    painted = placement.painted_box
    metrics = placement.text_metrics
    assert painted is not None
    assert metrics is not None
    assert placement.requested_box.width == pytest.approx(277.17)
    matrices = tuple(operands for operator, operands in _content_commands(data) if operator == "Tm")
    assert matrices == (("1", "0", "0", "1", "131.44", "551.49"),)

    # The reset occurs after font-unit x=1099; the last ink edge is at
    # x=4264. The actual emitted edge is therefore 131.44 + 3165*65/1000,
    # while painted_box rounds outward and remains inside the accepted box.
    actual_left = placement.requested_box.left
    actual_top = metrics.baseline_y_points - 58.24
    actual_right = 131.44 + (4_264 - 1_099) * 65 / 1_000
    actual_bottom = metrics.baseline_y_points + 9.23
    assert painted.left <= actual_left
    assert painted.top <= actual_top
    assert painted.right >= actual_right
    assert painted.bottom >= actual_bottom
    assert painted.right == placement.requested_box.right
    document = pypdfium2.PdfDocument(data)
    try:
        page = document[0]
        text_page = page.get_textpage()
        try:
            assert text_page.get_text_range() == sample
        finally:
            text_page.close()
            page.close()
    finally:
        document.close()


def test_reported_text_baseline_matches_the_emitted_pdf_coordinate() -> None:
    request = _request(
        (
            _record(
                1,
                TextualOverlayPayload(
                    OverlayKind.TEXT,
                    "A",
                    DEFAULT_FONT_TOKEN,
                    19,
                    RgbaColor("#000000ff"),
                ),
                NormalizedPageBox(100_000, 100_000, 200_000, 100_000),
            ),
        )
    )

    data, result = _render(request)

    metrics = result.placements[0].text_metrics
    assert metrics is not None
    reported_baseline = Decimal(str(metrics.baseline_y_points))
    assert reported_baseline == reported_baseline.quantize(Decimal("0.01"))
    text_positions = tuple(
        operands for operator, operands in _content_commands(data) if operator == "Td"
    )
    assert len(text_positions) == 1
    emitted_y = Decimal(text_positions[0][1])
    canvas_height = Decimal(str(request.geometry.canvas_height_points))
    assert canvas_height - emitted_y == reported_baseline


def test_glyph_beyond_hhea_ascent_fits_by_actual_vertical_ink_bounds() -> None:
    request = _request(
        (
            _record(
                1,
                TextualOverlayPayload(
                    OverlayKind.TEXT,
                    "\ua8f0",
                    DEFAULT_FONT_TOKEN,
                    20,
                    RgbaColor("#000000ff"),
                ),
                # Exact 26.10-point height: this glyph sequence's ink height.
                NormalizedPageBox(100_000, 100_000, 100_000, 36_250),
            ),
        )
    )

    _data, result = _render(request)

    placement = result.placements[0]
    painted = placement.painted_box
    metrics = placement.text_metrics
    assert painted is not None
    assert metrics is not None
    assert painted.top == placement.requested_box.top
    assert painted.bottom == placement.requested_box.bottom
    assert painted.height == pytest.approx(26.1)
    assert metrics.ascent_points == 26.94
    assert metrics.descent_points == 0.0


def test_actual_trailing_ink_width_rejects_box_that_only_fits_advance() -> None:
    request = _request(
        (
            _record(
                1,
                TextualOverlayPayload(
                    OverlayKind.TEXT,
                    "f",
                    DEFAULT_FONT_TOKEN,
                    20,
                    RgbaColor("#000000ff"),
                ),
                # Quantizes to 6.88 points: the advance fits exactly, while the
                # actual outline is 7.44 points wide.
                NormalizedPageBox(100_000, 100_000, 11_467, 100_000),
            ),
        )
    )

    with pytest.raises(OverlayRenderError) as raised:
        _render(request)

    assert raised.value.code is OverlayRenderErrorCode.TEXT_DOES_NOT_FIT
    assert raised.value.context == "overlay.text_fit"


def test_actual_vertical_ink_rejects_a_box_one_quantum_too_short() -> None:
    request = _request(
        (
            _record(
                1,
                TextualOverlayPayload(
                    OverlayKind.TEXT,
                    "\ua8f0",
                    DEFAULT_FONT_TOKEN,
                    20,
                    RgbaColor("#000000ff"),
                ),
                NormalizedPageBox(100_000, 100_000, 100_000, 36_236),
            ),
        )
    )

    with pytest.raises(OverlayRenderError) as raised:
        _render(request)

    assert raised.value.code is OverlayRenderErrorCode.TEXT_DOES_NOT_FIT
    assert raised.value.context == "overlay.text_fit"


def test_consecutive_identical_text_styles_reestablish_font_color_and_visible_ink() -> None:
    value = "STATE MATCH"
    records = tuple(
        _record(
            index,
            TextualOverlayPayload(
                OverlayKind.TEXT,
                value,
                DEFAULT_FONT_TOKEN,
                20,
                RgbaColor("#ff0000ff"),
            ),
            NormalizedPageBox(50_000, top, 800_000, 120_000),
        )
        for index, top in enumerate((50_000, 250_000), start=1)
    )

    data, result = _render(_request(records))
    commands = _content_commands(data)

    assert commands.count(("Tf", ("/F1", "20.00"))) == 2
    assert commands.count(("rg", ("1", "0", "0"))) == 2
    document = pypdfium2.PdfDocument(data)
    try:
        page = document[0]
        text_page = page.get_textpage()
        try:
            assert text_page.get_text_range() == f"{value}\r\n{value}"
        finally:
            text_page.close()
            page.close()
    finally:
        document.close()
    rgb, width, height = _rendered_rgb(data)
    assert all(
        _nonwhite_pixels(rgb, width, height, placement.requested_box) > 100
        for placement in result.placements
    )


@pytest.mark.parametrize("kind", (OverlayKind.CHECKMARK, OverlayKind.CROSS))
def test_consecutive_identical_marks_reestablish_stroke_state(kind: OverlayKind) -> None:
    records = tuple(
        _record(
            index,
            MarkOverlayPayload(kind, RgbaColor("#ff0000ff")),
            NormalizedPageBox(100_000, top, 100_000, 100_000),
        )
        for index, top in enumerate((100_000, 300_000), start=1)
    )

    data, _result = _render(_request(records))
    commands = _content_commands(data)

    assert commands.count(("RG", ("1", "0", "0"))) == 2
    assert commands.count(("w", ("6.00",))) == 2


@pytest.mark.parametrize(
    "primitive",
    (ShapePrimitive.RECTANGLE, ShapePrimitive.ELLIPSE, ShapePrimitive.LINE),
)
def test_consecutive_identical_shapes_reestablish_graphics_state(
    primitive: ShapePrimitive,
) -> None:
    fill_color = (
        RgbaColor("#00000000") if primitive is ShapePrimitive.LINE else RgbaColor("#00ff0080")
    )
    records = tuple(
        _record(
            index,
            ShapeOverlayPayload(
                OverlayKind.SHAPE,
                primitive,
                fill_color,
                RgbaColor("#0000ffff"),
            ),
            NormalizedPageBox(100_000, top, 400_000, 200_000),
        )
        for index, top in enumerate((100_000, 500_000), start=1)
    )

    data, _result = _render(_request(records))
    commands = _content_commands(data)

    assert commands.count(("RG", ("0", "0", "1"))) == 2
    assert commands.count(("w", ("4.00",))) == 2
    if primitive is not ShapePrimitive.LINE:
        assert commands.count(("rg", ("0", "1", "0"))) == 2


@pytest.mark.parametrize("size", (1, 5, 8, 10))
@pytest.mark.parametrize("origin", ("center", "rounding-boundary", "lower-right"))
def test_tiny_released_overlay_types_are_origin_invariant_and_deterministic(
    size: int,
    origin: str,
) -> None:
    if origin == "center":
        x, y = 500_000, 500_000
    elif origin == "rounding-boundary":
        x, y = 8, 6
    else:
        x = y = 1_000_000 - size
    box = NormalizedPageBox(x, y, size, size)
    pixels = bytes((255, 0, 0, 255))
    raster = OverlayRasterAsset(
        ASSET_ID,
        width_pixels=1,
        height_pixels=1,
        row_stride_bytes=4,
        byte_count=len(pixels),
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
    )
    source = _RasterSource(raster, pixels)
    source.seek(2)
    asset_record = OverlayAssetRecord(
        ASSET_ID,
        OverlayAssetKind.SIGNATURE_IMAGE,
        f"{ASSET_ID}.bin",
        OverlayAssetMediaType.PNG,
        128,
        "a" * 64,
    )
    payloads: tuple[
        TextualOverlayPayload
        | MarkOverlayPayload
        | ShapeOverlayPayload
        | SignatureImageOverlayPayload,
        ...,
    ] = (
        TextualOverlayPayload(
            OverlayKind.TEXT,
            "",
            DEFAULT_FONT_TOKEN,
            12,
            RgbaColor("#000000ff"),
        ),
        TextualOverlayPayload(
            OverlayKind.DATE,
            " ",
            DEFAULT_FONT_TOKEN,
            12,
            RgbaColor("#000000ff"),
        ),
        TextualOverlayPayload(
            OverlayKind.INITIALS,
            "X",
            DEFAULT_FONT_TOKEN,
            12,
            RgbaColor("#00000000"),
        ),
        MarkOverlayPayload(OverlayKind.CHECKMARK, RgbaColor("#102030ff")),
        MarkOverlayPayload(OverlayKind.CROSS, RgbaColor("#405060ff")),
        MarkOverlayPayload(OverlayKind.CHECKMARK, RgbaColor("#10203000")),
        ShapeOverlayPayload(
            OverlayKind.HIGHLIGHT,
            ShapePrimitive.RECTANGLE,
            RgbaColor("#ffff0080"),
            RgbaColor("#00000000"),
        ),
        ShapeOverlayPayload(
            OverlayKind.SHAPE,
            ShapePrimitive.RECTANGLE,
            RgbaColor("#00000000"),
            RgbaColor("#204060ff"),
        ),
        ShapeOverlayPayload(
            OverlayKind.SHAPE,
            ShapePrimitive.ELLIPSE,
            RgbaColor("#0080ffff"),
            RgbaColor("#00000000"),
        ),
        ShapeOverlayPayload(
            OverlayKind.SHAPE,
            ShapePrimitive.LINE,
            RgbaColor("#00000000"),
            RgbaColor("#603090ff"),
        ),
        ShapeOverlayPayload(
            OverlayKind.SHAPE,
            ShapePrimitive.RECTANGLE,
            RgbaColor("#10203000"),
            RgbaColor("#40506000"),
        ),
        SignatureImageOverlayPayload(ASSET_ID),
    )
    records = tuple(_record(index, payload, box) for index, payload in enumerate(payloads, start=1))
    request = _request(records, assets=(asset_record,))

    first, first_result = _render(request, raster_sources={ASSET_ID: source})
    second, second_result = _render(request, raster_sources={ASSET_ID: source})

    assert first == second
    assert first_result == second_result
    assert source.tell() == 2
    assert first_result.embedded_font_count == 1
    assert first_result.image_count == 1
    assert len(first_result.placements) == len(records)
    no_paint_indexes = {0, 1, 2, 5, 10}
    for index, (record, placement) in enumerate(zip(records, first_result.placements, strict=True)):
        assert placement.overlay_id == record.overlay_id
        assert placement.kind is record.kind
        assert placement.z_index == record.z_index
        assert placement.requested_box.width == pytest.approx(0.01)
        assert placement.requested_box.height == pytest.approx(0.01)
        assert placement.clip_box == placement.requested_box
        assert (placement.painted_box is None) is (index in no_paint_indexes)
        if placement.painted_box is not None:
            assert placement.painted_box == placement.requested_box


def test_tiny_partial_crop_uses_a_clip_inside_the_representational_box() -> None:
    record = _record(
        1,
        MarkOverlayPayload(OverlayKind.CHECKMARK, RgbaColor("#ff0000ff")),
        NormalizedPageBox(500_000, 500_000, 10, 10),
    )
    request = _request(
        (record,),
        active_crop=NormalizedPageBox(500_009, 0, 499_991, 1_000_000),
    )

    _data, result = _render(request)

    placement = result.placements[0]
    assert placement.requested_box == OverlayCanvasBox(300.0, 360.0, 300.01, 360.01)
    assert placement.clip_box == placement.requested_box
    assert placement.painted_box == placement.requested_box


def test_tiny_mixed_shape_fallback_prefers_visible_fill_over_low_alpha_stroke() -> None:
    request = _request(
        (
            _record(
                1,
                ShapeOverlayPayload(
                    OverlayKind.SHAPE,
                    ShapePrimitive.RECTANGLE,
                    RgbaColor("#00ff00ff"),
                    RgbaColor("#ff000001"),
                ),
                NormalizedPageBox(500_000, 500_000, 1, 1),
            ),
        )
    )

    data, result = _render(request)
    commands = _content_commands(data)

    assert result.placements[0].painted_box == result.placements[0].requested_box
    assert ("rg", ("0", "1", "0")) in commands
    assert ("rg", ("1", "0", "0")) not in commands


def test_extreme_signature_aspect_ratio_uses_one_contained_emitted_cell() -> None:
    width = 100
    pixels = bytes((255, 0, 0, 255)) * width
    raster = OverlayRasterAsset(
        ASSET_ID,
        width_pixels=width,
        height_pixels=1,
        row_stride_bytes=width * 4,
        byte_count=len(pixels),
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
    )
    source = _RasterSource(raster, pixels)
    asset_record = OverlayAssetRecord(
        ASSET_ID,
        OverlayAssetKind.SIGNATURE_IMAGE,
        f"{ASSET_ID}.bin",
        OverlayAssetMediaType.PNG,
        128,
        "a" * 64,
    )
    request = _request(
        (
            _record(
                1,
                SignatureImageOverlayPayload(ASSET_ID),
                NormalizedPageBox(500_000, 500_000, 1, 1),
            ),
        ),
        assets=(asset_record,),
    )

    _data, result = _render(request, raster_sources={ASSET_ID: source})

    placement = result.placements[0]
    assert placement.requested_box == OverlayCanvasBox(300.0, 360.0, 300.01, 360.01)
    assert placement.painted_box == placement.requested_box


def test_live_renderer_draws_all_non_image_primitives_and_sanitizes_catalog() -> None:
    records = (
        _record(
            1,
            TextualOverlayPayload(
                OverlayKind.DATE,
                "31/12/2030",
                DEFAULT_FONT_TOKEN,
                16,
                RgbaColor("#000000ff"),
            ),
            NormalizedPageBox(50_000, 50_000, 350_000, 90_000),
        ),
        _record(
            2,
            TextualOverlayPayload(
                OverlayKind.INITIALS,
                "XY",
                DEFAULT_FONT_TOKEN,
                18,
                RgbaColor("#000080ff"),
            ),
            NormalizedPageBox(450_000, 50_000, 150_000, 90_000),
        ),
        _record(
            3,
            MarkOverlayPayload(OverlayKind.CHECKMARK, RgbaColor("#008000ff")),
            NormalizedPageBox(50_000, 220_000, 100_000, 100_000),
        ),
        _record(
            4,
            MarkOverlayPayload(OverlayKind.CROSS, RgbaColor("#800000ff")),
            NormalizedPageBox(190_000, 220_000, 100_000, 100_000),
        ),
        _record(
            5,
            ShapeOverlayPayload(
                OverlayKind.HIGHLIGHT,
                ShapePrimitive.RECTANGLE,
                RgbaColor("#ffff0080"),
                RgbaColor("#c0a000ff"),
            ),
            NormalizedPageBox(50_000, 400_000, 300_000, 100_000),
        ),
        _record(
            6,
            ShapeOverlayPayload(
                OverlayKind.SHAPE,
                ShapePrimitive.ELLIPSE,
                RgbaColor("#0080ff40"),
                RgbaColor("#004080ff"),
            ),
            NormalizedPageBox(420_000, 400_000, 250_000, 180_000),
        ),
        _record(
            7,
            ShapeOverlayPayload(
                OverlayKind.SHAPE,
                ShapePrimitive.LINE,
                RgbaColor("#00000000"),
                RgbaColor("#603090ff"),
            ),
            NormalizedPageBox(50_000, 650_000, 500_000, 100_000),
        ),
    )

    data, result = _render(_request(records))

    assert len(result.placements) == len(records)
    assert result.embedded_font_count == 1
    assert result.image_count == 0
    with pikepdf.Pdf.open(BytesIO(data), attempt_recovery=False) as document:
        assert len(document.pages) == 1
        assert "/OpenAction" not in document.Root
        assert "/Names" not in document.Root
        assert "/AcroForm" not in document.Root
        assert "/Metadata" not in document.Root
        assert "/Info" not in document.trailer


def test_partial_crop_clips_original_overlay_without_rescaling_it() -> None:
    record = _record(
        1,
        ShapeOverlayPayload(
            OverlayKind.SHAPE,
            ShapePrimitive.RECTANGLE,
            RgbaColor("#80c0ff80"),
            RgbaColor("#204060ff"),
        ),
        NormalizedPageBox(100_000, 100_000, 400_000, 200_000),
    )
    request = _request(
        (record,),
        active_crop=NormalizedPageBox(250_000, 0, 750_000, 1_000_000),
    )

    _data, result = _render(request)

    placement = result.placements[0]
    assert placement.requested_box.left == 60.0
    assert placement.requested_box.right == 300.0
    assert placement.clip_box.left == 150.0
    assert placement.clip_box.right == 300.0
    assert placement.painted_box == placement.requested_box


def test_text_that_cannot_fit_fails_closed_without_partial_output() -> None:
    request = _request(
        (
            _record(
                1,
                TextualOverlayPayload(
                    OverlayKind.TEXT,
                    "SYNTHETIC TEXT TOO WIDE",
                    DEFAULT_FONT_TOKEN,
                    40,
                    RgbaColor("#000000ff"),
                ),
                NormalizedPageBox(10_000, 10_000, 20_000, 20_000),
            ),
        )
    )
    destination = BytesIO()

    with pytest.raises(OverlayRenderError) as raised:
        FpdfOverlayRenderer().render(
            request,
            {},
            destination,
            OverlayRenderLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1),
            _Control(),
        )

    assert raised.value.code is OverlayRenderErrorCode.TEXT_DOES_NOT_FIT
    assert destination.getvalue() == b""


def test_live_renderer_preserves_v1_rgba_layout_in_device_rgb_and_smask() -> None:
    top_row_rgba = bytes((255, 0, 0, 255, 0, 255, 0, 128))
    bottom_row_rgba = bytes((0, 0, 255, 64, 10, 20, 30, 32))
    pixels = top_row_rgba + bottom_row_rgba
    raster = OverlayRasterAsset(
        ASSET_ID,
        width_pixels=2,
        height_pixels=2,
        row_stride_bytes=8,
        byte_count=len(pixels),
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
    )
    source = _RasterSource(raster, pixels)
    source.seek(3)
    asset_record = OverlayAssetRecord(
        ASSET_ID,
        OverlayAssetKind.SIGNATURE_IMAGE,
        f"{ASSET_ID}.bin",
        OverlayAssetMediaType.PNG,
        128,
        "a" * 64,
    )
    request = _request(
        (
            _record(
                1,
                SignatureImageOverlayPayload(ASSET_ID),
                NormalizedPageBox(100_000, 100_000, 500_000, 250_000),
            ),
        ),
        assets=(asset_record,),
    )

    data, result = _render(request, raster_sources={ASSET_ID: source})

    assert source.tell() == 3
    assert result.image_count == 1
    assert result.placements[0].painted_box is not None
    with pikepdf.Pdf.open(BytesIO(data), attempt_recovery=False) as document:
        assert "/OutputIntents" not in document.Root
        resources = document.pages[0].obj["/Resources"]
        assert isinstance(resources, pikepdf.Dictionary)
        images = resources["/XObject"]
        assert isinstance(images, pikepdf.Dictionary)
        image = images[next(iter(images.keys()))]
        assert isinstance(image, pikepdf.Stream)
        assert image["/Subtype"] == "/Image"
        assert image["/Width"] == 2
        assert image["/Height"] == 2
        assert image["/BitsPerComponent"] == 8
        assert image["/ColorSpace"] == pikepdf.Name("/DeviceRGB")
        assert image.read_bytes() == bytes(
            (
                255,
                0,
                0,
                0,
                255,
                0,
                0,
                0,
                255,
                10,
                20,
                30,
            )
        )
        assert "/Metadata" not in image
        assert "/Intent" not in image
        soft_mask = image["/SMask"]
        assert isinstance(soft_mask, pikepdf.Stream)
        assert soft_mask["/Width"] == 2
        assert soft_mask["/Height"] == 2
        assert soft_mask["/BitsPerComponent"] == 8
        assert soft_mask["/ColorSpace"] == pikepdf.Name("/DeviceGray")
        assert soft_mask.read_bytes() == bytes((255, 128, 64, 32))
        assert "/Metadata" not in soft_mask


def test_unsupported_glyph_fails_before_engine_logging(caplog: pytest.LogCaptureFixture) -> None:
    canary = "CANARY-NOT-LOGGED-7f06-😀"
    request = _request(
        (
            _record(
                1,
                TextualOverlayPayload(
                    OverlayKind.TEXT,
                    canary,
                    DEFAULT_FONT_TOKEN,
                    14,
                    RgbaColor("#000000ff"),
                ),
                NormalizedPageBox(50_000, 50_000, 900_000, 100_000),
            ),
        )
    )
    caplog.set_level(logging.DEBUG)

    with pytest.raises(OverlayRenderError) as raised:
        _render(request)

    assert raised.value.code is OverlayRenderErrorCode.GLYPH_UNSUPPORTED
    assert canary not in caplog.text
    assert canary not in str(raised.value)
    assert canary not in repr(raised.value)


@pytest.mark.parametrize(
    ("control", "code"),
    (
        (_Control(cancelled=True), OverlayRenderErrorCode.CANCELLED),
        (_Control(timed_out=True), OverlayRenderErrorCode.TIMED_OUT),
    ),
)
def test_control_stops_before_renderer_access(
    control: _Control, code: OverlayRenderErrorCode
) -> None:
    request = _request(
        (
            _record(
                1,
                MarkOverlayPayload(OverlayKind.CHECKMARK, RgbaColor("#000000ff")),
                NormalizedPageBox(10_000, 10_000, 100_000, 100_000),
            ),
        )
    )

    with pytest.raises(OverlayRenderError) as raised:
        _render(request, control=control)

    assert raised.value.code is code

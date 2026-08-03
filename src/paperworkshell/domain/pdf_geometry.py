"""Pure PDF page-geometry resolution and renderer-neutral fit mathematics."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Final

from paperworkshell.domain.pdf_rendering import (
    PdfPageBox,
    PdfPageRenderLimits,
    PdfPageRenderRequest,
    PdfRasterLayout,
    PdfRenderErrorCode,
    PdfRenderRotation,
    checked_pdf_raster_layout,
    reject_pdf_rendering,
    validate_pdf_render_request,
)

MAX_PDF_PAGE_COORDINATE_MAGNITUDE_UNITS: Final = 1_000_000_000
MAX_PDF_PAGE_SPAN_UNITS: Final = 1_000_000_000
MAX_PDF_USER_UNIT: Final = 75_000
MAX_PDF_ROTATION_MAGNITUDE: Final = 2_147_483_647
MAX_PDF_RENDER_SCALE_PIXELS_PER_UNIT: Final = 1_000_000


@dataclass(frozen=True, slots=True)
class PdfPageGeometryLimits:
    """Trusted bounds for PDF-space coordinates before renderer allocation."""

    maximum_coordinate_magnitude_units: int
    maximum_box_span_units: int

    def __post_init__(self) -> None:
        if (
            type(self.maximum_coordinate_magnitude_units) is not int
            or not 1
            <= self.maximum_coordinate_magnitude_units
            <= MAX_PDF_PAGE_COORDINATE_MAGNITUDE_UNITS
            or type(self.maximum_box_span_units) is not int
            or not 1 <= self.maximum_box_span_units <= MAX_PDF_PAGE_SPAN_UNITS
        ):
            reject_pdf_rendering(PdfRenderErrorCode.MODEL_INVALID, "render.geometry_limits")


@dataclass(frozen=True, slots=True, repr=False)
class PdfPageRectangle:
    """One normalized, finite PDF rectangle in unrotated user space."""

    left: float
    bottom: float
    right: float
    top: float

    def __post_init__(self) -> None:
        coordinates = (self.left, self.bottom, self.right, self.top)
        if (
            any(type(value) is not float or not math.isfinite(value) for value in coordinates)
            or not self.left < self.right
            or not self.bottom < self.top
            or any(abs(value) > MAX_PDF_PAGE_COORDINATE_MAGNITUDE_UNITS for value in coordinates)
        ):
            reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.rectangle")

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.top - self.bottom

    def __repr__(self) -> str:
        return "PdfPageRectangle(<page-values-hidden>)"


@dataclass(frozen=True, slots=True, repr=False)
class PdfResolvedPageGeometry:
    """Renderer-neutral geometry after page-tree inheritance and box selection."""

    media_box: PdfPageRectangle
    crop_box: PdfPageRectangle
    effective_box: PdfPageRectangle
    page_box: PdfPageBox
    intrinsic_rotation: PdfRenderRotation
    user_unit: float

    def __post_init__(self) -> None:
        if (
            type(self.media_box) is not PdfPageRectangle
            or type(self.crop_box) is not PdfPageRectangle
            or type(self.effective_box) is not PdfPageRectangle
            or type(self.page_box) is not PdfPageBox
            or type(self.intrinsic_rotation) is not PdfRenderRotation
            or type(self.user_unit) is not float
            or not math.isfinite(self.user_unit)
            or not 0 < self.user_unit <= MAX_PDF_USER_UNIT
            or not _contains(self.media_box, self.effective_box)
        ):
            reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.result")
        if self.page_box is PdfPageBox.MEDIA:
            if self.effective_box != self.media_box:
                reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.result")
        elif not _contains(self.crop_box, self.effective_box):
            reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.result")

    @property
    def uses_non_default_user_unit(self) -> bool:
        return self.user_unit != 1.0

    def __repr__(self) -> str:
        return (
            "PdfResolvedPageGeometry("
            f"page_box={self.page_box.value!r}, "
            f"intrinsic_rotation={int(self.intrinsic_rotation)!r}, "
            "page_values=<hidden>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class PdfPageBoxInsets:
    """Non-negative box insets in post-rotation PDF user-space order."""

    left: float
    bottom: float
    right: float
    top: float

    def __post_init__(self) -> None:
        values = (self.left, self.bottom, self.right, self.top)
        if any(
            type(value) is not float or not math.isfinite(value) or value < 0 for value in values
        ):
            reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.insets")

    def __repr__(self) -> str:
        return "PdfPageBoxInsets(<page-values-hidden>)"


@dataclass(frozen=True, slots=True, repr=False)
class PdfPageBoxTransform:
    """Affine map from unrotated PDF coordinates to top-left bitmap coordinates."""

    xx: float
    xy: float
    x_offset: float
    yx: float
    yy: float
    y_offset: float

    def __post_init__(self) -> None:
        coefficients = (self.xx, self.xy, self.x_offset, self.yx, self.yy, self.y_offset)
        if any(type(value) is not float or not math.isfinite(value) for value in coefficients):
            reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.transform")

    def map_point(self, x: int | float, y: int | float) -> tuple[float, float]:
        """Map a finite PDF point without accepting third-party numeric objects."""

        numeric_x = _bounded_point_coordinate(x)
        numeric_y = _bounded_point_coordinate(y)
        mapped = (
            self.xx * numeric_x + self.xy * numeric_y + self.x_offset,
            self.yx * numeric_x + self.yy * numeric_y + self.y_offset,
        )
        if not math.isfinite(mapped[0]) or not math.isfinite(mapped[1]):
            reject_pdf_rendering(
                PdfRenderErrorCode.LIMIT_EXCEEDED,
                "render.geometry.transform_result",
            )
        return mapped

    def __repr__(self) -> str:
        return "PdfPageBoxTransform(<page-values-hidden>)"


@dataclass(frozen=True, slots=True, repr=False)
class PdfPageRenderGeometry:
    """Checked fit, rotation, crop, transform, and raster allocation plan."""

    resolved: PdfResolvedPageGeometry
    combined_rotation: PdfRenderRotation
    rotated_width_units: float
    rotated_height_units: float
    scale_pixels_per_unit: float
    layout: PdfRasterLayout
    post_rotation_insets: PdfPageBoxInsets
    transform: PdfPageBoxTransform

    def __post_init__(self) -> None:
        if (
            type(self.resolved) is not PdfResolvedPageGeometry
            or type(self.combined_rotation) is not PdfRenderRotation
            or type(self.rotated_width_units) is not float
            or not math.isfinite(self.rotated_width_units)
            or self.rotated_width_units <= 0
            or type(self.rotated_height_units) is not float
            or not math.isfinite(self.rotated_height_units)
            or self.rotated_height_units <= 0
            or type(self.scale_pixels_per_unit) is not float
            or not math.isfinite(self.scale_pixels_per_unit)
            or self.scale_pixels_per_unit <= 0
            or type(self.layout) is not PdfRasterLayout
            or type(self.post_rotation_insets) is not PdfPageBoxInsets
            or type(self.transform) is not PdfPageBoxTransform
        ):
            reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.plan")

    def __repr__(self) -> str:
        return (
            "PdfPageRenderGeometry("
            f"combined_rotation={int(self.combined_rotation)!r}, "
            f"width_pixels={self.layout.width_pixels!r}, "
            f"height_pixels={self.layout.height_pixels!r}, "
            "page_values=<hidden>)"
        )


def resolve_pdf_page_geometry(
    media_box: tuple[object, ...],
    crop_box: tuple[object, ...] | None,
    intrinsic_rotation: object | None,
    user_unit: object | None,
    page_box: PdfPageBox,
    limits: PdfPageGeometryLimits,
) -> PdfResolvedPageGeometry:
    """Validate inherited built-in values and select the authoritative visible box."""

    if type(page_box) is not PdfPageBox or type(limits) is not PdfPageGeometryLimits:
        reject_pdf_rendering(PdfRenderErrorCode.MODEL_INVALID, "render.geometry.input")

    resolved_media = _normalize_rectangle(
        media_box,
        limits=limits,
        context="render.geometry.media_box",
    )
    resolved_crop = (
        resolved_media
        if crop_box is None
        else _normalize_rectangle(
            crop_box,
            limits=limits,
            context="render.geometry.crop_box",
        )
    )
    rotation = _normalize_rotation(intrinsic_rotation)
    resolved_user_unit = _normalize_user_unit(user_unit)

    if page_box is PdfPageBox.MEDIA:
        effective = resolved_media
    else:
        left = max(resolved_media.left, resolved_crop.left)
        bottom = max(resolved_media.bottom, resolved_crop.bottom)
        right = min(resolved_media.right, resolved_crop.right)
        top = min(resolved_media.top, resolved_crop.top)
        if left >= right or bottom >= top:
            reject_pdf_rendering(
                PdfRenderErrorCode.GEOMETRY_INVALID,
                "render.geometry.crop_intersection",
            )
        effective = PdfPageRectangle(left, bottom, right, top)

    return PdfResolvedPageGeometry(
        media_box=resolved_media,
        crop_box=resolved_crop,
        effective_box=effective,
        page_box=page_box,
        intrinsic_rotation=rotation,
        user_unit=resolved_user_unit,
    )


def fit_pdf_page_geometry(
    geometry: PdfResolvedPageGeometry,
    request: PdfPageRenderRequest,
    limits: PdfPageRenderLimits,
) -> PdfPageRenderGeometry:
    """Ceil-fit one rotated box into the request and prove its packed RGBA layout."""

    if (
        type(geometry) is not PdfResolvedPageGeometry
        or type(request) is not PdfPageRenderRequest
        or type(limits) is not PdfPageRenderLimits
        or geometry.page_box is not request.page_box
    ):
        reject_pdf_rendering(PdfRenderErrorCode.MODEL_INVALID, "render.geometry.fit_input")
    validate_pdf_render_request(request, limits)

    combined_rotation = combine_pdf_rotations(
        geometry.intrinsic_rotation,
        request.additional_rotation,
    )
    box = geometry.effective_box
    if combined_rotation in (PdfRenderRotation.DEGREES_90, PdfRenderRotation.DEGREES_270):
        rotated_width = box.height
        rotated_height = box.width
    else:
        rotated_width = box.width
        rotated_height = box.height

    width_fraction = Fraction.from_float(rotated_width)
    height_fraction = Fraction.from_float(rotated_height)
    scale_fraction = min(
        Fraction(request.maximum_width_pixels, 1) / width_fraction,
        Fraction(request.maximum_height_pixels, 1) / height_fraction,
    )
    if scale_fraction > MAX_PDF_RENDER_SCALE_PIXELS_PER_UNIT:
        reject_pdf_rendering(PdfRenderErrorCode.LIMIT_EXCEEDED, "render.geometry.scale")
    width_pixels = _ceil_fraction(width_fraction * scale_fraction)
    height_pixels = _ceil_fraction(height_fraction * scale_fraction)
    layout = checked_pdf_raster_layout(width_pixels, height_pixels, limits)

    try:
        scale = float(scale_fraction)
    except OverflowError, ValueError:
        reject_pdf_rendering(PdfRenderErrorCode.LIMIT_EXCEEDED, "render.geometry.scale")
    if not math.isfinite(scale) or scale <= 0:
        reject_pdf_rendering(PdfRenderErrorCode.LIMIT_EXCEEDED, "render.geometry.scale")
    insets = _post_rotation_insets(geometry, combined_rotation)
    transform = _page_box_transform(box, combined_rotation, scale)
    return PdfPageRenderGeometry(
        resolved=geometry,
        combined_rotation=combined_rotation,
        rotated_width_units=rotated_width,
        rotated_height_units=rotated_height,
        scale_pixels_per_unit=scale,
        layout=layout,
        post_rotation_insets=insets,
        transform=transform,
    )


def combine_pdf_rotations(
    intrinsic: PdfRenderRotation,
    additional: PdfRenderRotation,
) -> PdfRenderRotation:
    """Combine two clockwise quarter-turns without renderer-specific behavior."""

    if type(intrinsic) is not PdfRenderRotation or type(additional) is not PdfRenderRotation:
        reject_pdf_rendering(PdfRenderErrorCode.MODEL_INVALID, "render.geometry.rotation")
    return PdfRenderRotation((int(intrinsic) + int(additional)) % 360)


def _normalize_rectangle(
    values: tuple[object, ...],
    *,
    limits: PdfPageGeometryLimits,
    context: str,
) -> PdfPageRectangle:
    if type(values) is not tuple or len(values) != 4:
        reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, context)

    coordinates: list[Fraction] = []
    for value in values:
        if type(value) is int:
            integer = value
            if abs(integer) > limits.maximum_coordinate_magnitude_units:
                reject_pdf_rendering(PdfRenderErrorCode.LIMIT_EXCEEDED, context)
            coordinate = Fraction(integer, 1)
        elif type(value) is float:
            if not math.isfinite(value):
                reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, context)
            if abs(value) > limits.maximum_coordinate_magnitude_units:
                reject_pdf_rendering(PdfRenderErrorCode.LIMIT_EXCEEDED, context)
            coordinate = Fraction.from_float(value)
        elif type(value) is Decimal:
            if not value.is_finite():
                reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, context)
            if abs(value) > limits.maximum_coordinate_magnitude_units:
                reject_pdf_rendering(PdfRenderErrorCode.LIMIT_EXCEEDED, context)
            coordinate = Fraction(value)
        else:
            reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, context)
        coordinates.append(coordinate)

    first_x, first_y, second_x, second_y = coordinates
    left, right = sorted((first_x, second_x))
    bottom, top = sorted((first_y, second_y))
    width = right - left
    height = top - bottom
    if width <= 0 or height <= 0:
        reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, context)
    if width > limits.maximum_box_span_units or height > limits.maximum_box_span_units:
        reject_pdf_rendering(PdfRenderErrorCode.LIMIT_EXCEEDED, context)
    return PdfPageRectangle(float(left), float(bottom), float(right), float(top))


def _normalize_rotation(value: object | None) -> PdfRenderRotation:
    if value is None:
        return PdfRenderRotation.DEGREES_0
    if type(value) is not int or abs(value) > MAX_PDF_ROTATION_MAGNITUDE or value % 90 != 0:
        reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.rotate")
    return PdfRenderRotation(value % 360)


def _normalize_user_unit(value: object | None) -> float:
    if value is None:
        return 1.0
    if type(value) is int:
        integer = value
        if not 0 < integer <= MAX_PDF_USER_UNIT:
            reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.user_unit")
        return float(integer)
    if type(value) is Decimal:
        if not value.is_finite() or not 0 < value <= MAX_PDF_USER_UNIT:
            reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.user_unit")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric <= 0:
            reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.user_unit")
        return numeric
    if type(value) is not float:
        reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.user_unit")
    numeric = value
    if not math.isfinite(numeric) or not 0 < numeric <= MAX_PDF_USER_UNIT:
        reject_pdf_rendering(PdfRenderErrorCode.GEOMETRY_INVALID, "render.geometry.user_unit")
    return numeric


def _bounded_point_coordinate(value: int | float) -> float:
    if type(value) is int:
        if abs(value) > MAX_PDF_PAGE_COORDINATE_MAGNITUDE_UNITS:
            reject_pdf_rendering(PdfRenderErrorCode.MODEL_INVALID, "render.geometry.point")
        return float(value)
    if (
        type(value) is not float
        or not math.isfinite(value)
        or abs(value) > MAX_PDF_PAGE_COORDINATE_MAGNITUDE_UNITS
    ):
        reject_pdf_rendering(PdfRenderErrorCode.MODEL_INVALID, "render.geometry.point")
    return value


def _contains(outer: PdfPageRectangle, inner: PdfPageRectangle) -> bool:
    return (
        outer.left <= inner.left
        and outer.bottom <= inner.bottom
        and inner.right <= outer.right
        and inner.top <= outer.top
    )


def _ceil_fraction(value: Fraction) -> int:
    return -(-value.numerator // value.denominator)


def _post_rotation_insets(
    geometry: PdfResolvedPageGeometry,
    rotation: PdfRenderRotation,
) -> PdfPageBoxInsets:
    media = geometry.media_box
    selected = geometry.effective_box
    unrotated = (
        selected.left - media.left,
        selected.bottom - media.bottom,
        media.right - selected.right,
        media.top - selected.top,
    )
    left, bottom, right, top = unrotated
    if rotation is PdfRenderRotation.DEGREES_0:
        rotated = (left, bottom, right, top)
    elif rotation is PdfRenderRotation.DEGREES_90:
        rotated = (bottom, right, top, left)
    elif rotation is PdfRenderRotation.DEGREES_180:
        rotated = (right, top, left, bottom)
    else:
        rotated = (top, left, bottom, right)
    return PdfPageBoxInsets(*(float(value) for value in rotated))


def _page_box_transform(
    box: PdfPageRectangle,
    rotation: PdfRenderRotation,
    scale: float,
) -> PdfPageBoxTransform:
    if rotation is PdfRenderRotation.DEGREES_0:
        coefficients = (scale, 0.0, -scale * box.left, 0.0, -scale, scale * box.top)
    elif rotation is PdfRenderRotation.DEGREES_90:
        coefficients = (0.0, scale, -scale * box.bottom, scale, 0.0, -scale * box.left)
    elif rotation is PdfRenderRotation.DEGREES_180:
        coefficients = (-scale, 0.0, scale * box.right, 0.0, scale, -scale * box.bottom)
    else:
        coefficients = (0.0, -scale, scale * box.top, -scale, 0.0, scale * box.right)
    return PdfPageBoxTransform(*coefficients)

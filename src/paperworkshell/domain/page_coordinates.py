"""Pure coordinate types and staged page transforms.

The persisted coordinate space is deliberately integer based.  PDF user space,
rendered-image edges, and logical screen coordinates are named separately so a
caller cannot accidentally treat one space as another.  This module performs no
I/O and imports no UI, renderer, PDF-engine, or image-engine implementation.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from fractions import Fraction
from typing import Final, Never, Self, cast

from paperworkshell.domain.manifest_repository import copy_manifest_document

NORMALIZED_PAGE_MILLIONTHS: Final = 1_000_000
NORMALIZED_PAGE_COORDINATE_SPACE: Final = "normalized_page_millionths_v1"
MAX_PDF_USER_SPACE_COORDINATE: Final = 1_000_000_000
MAX_PDF_USER_UNIT: Final = 75_000
MAX_RASTER_DIMENSION: Final = 2_147_483_647
MAX_CONTINUOUS_COORDINATE: Final = 1_000_000_000_000_000
MAX_DEVICE_PIXEL_RATIO: Final = 64.0
FLOAT_ULP_ALLOWANCE: Final = 64

_BOX_MEMBERS: Final = frozenset({"coordinate_space", "x", "y", "width", "height"})
_CONTEXT_TOKEN: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")


class PageCoordinateErrorCode(StrEnum):
    """Closed registry of non-disclosing coordinate failures."""

    MODEL_INVALID = "PAGE_COORDINATE_MODEL_INVALID"
    MANIFEST_INVALID = "PAGE_COORDINATE_MANIFEST_INVALID"
    BOX_INVALID = "PAGE_COORDINATE_BOX_INVALID"
    GEOMETRY_INVALID = "PAGE_COORDINATE_GEOMETRY_INVALID"
    POINT_OUT_OF_BOUNDS = "PAGE_COORDINATE_POINT_OUT_OF_BOUNDS"
    ROTATION_INVALID = "PAGE_COORDINATE_ROTATION_INVALID"
    SCALE_INVALID = "PAGE_COORDINATE_SCALE_INVALID"


_SAFE_ERROR_MESSAGES: Final[dict[PageCoordinateErrorCode, str]] = {
    PageCoordinateErrorCode.MODEL_INVALID: (
        "PaperWorkShell received invalid page-coordinate data."
    ),
    PageCoordinateErrorCode.MANIFEST_INVALID: (
        "The saved page-coordinate record is invalid and was not changed."
    ),
    PageCoordinateErrorCode.BOX_INVALID: (
        "The requested page area is empty, inverted, or outside its coordinate space."
    ),
    PageCoordinateErrorCode.GEOMETRY_INVALID: ("The page geometry cannot be mapped safely."),
    PageCoordinateErrorCode.POINT_OUT_OF_BOUNDS: (
        "The requested point is outside the active page area."
    ),
    PageCoordinateErrorCode.ROTATION_INVALID: (
        "Choose a clockwise rotation of 0, 90, 180, or 270 degrees."
    ),
    PageCoordinateErrorCode.SCALE_INVALID: ("The page display scale cannot be mapped safely."),
}


@dataclass(slots=True, repr=False)
class PageCoordinateError(ValueError):
    """Stable coordinate failure that never includes document values."""

    code: PageCoordinateErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not PageCoordinateErrorCode:
            self.code = PageCoordinateErrorCode.MODEL_INVALID
        if type(self.context) is not str or _CONTEXT_TOKEN.fullmatch(self.context) is None:
            self.context = "page.coordinate"
        self.safe_message = _SAFE_ERROR_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"PageCoordinateError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_page_coordinate(code: PageCoordinateErrorCode, context: str) -> Never:
    """Raise one closed, non-disclosing coordinate failure."""

    raise PageCoordinateError(code=code, context=context)


class ClockwiseRotation(IntEnum):
    """A clockwise page rotation expressed in degrees."""

    DEGREES_0 = 0
    DEGREES_90 = 90
    DEGREES_180 = 180
    DEGREES_270 = 270

    @classmethod
    def from_degrees(cls, value: object) -> Self:
        if type(value) is not int or value not in {0, 90, 180, 270}:
            reject_page_coordinate(PageCoordinateErrorCode.ROTATION_INVALID, "coordinate.rotation")
        return cls(value)

    @classmethod
    def from_quarter_turns(cls, value: object) -> Self:
        if type(value) is not int or not 0 <= value <= 3:
            reject_page_coordinate(PageCoordinateErrorCode.ROTATION_INVALID, "coordinate.rotation")
        return cls(value * 90)


class PdfPageBoxSelection(StrEnum):
    """The PDF page box used as the full logical page."""

    MEDIA = "media"
    CROP = "crop"


class NormalizedBoxClipPolicy(StrEnum):
    """Explicit policy for constructing a normalized box from raw edges."""

    REJECT = "reject"
    CLIP_TO_PAGE = "clip_to_page"


class BoxQuantization(StrEnum):
    """Policy for converting continuous box edges to integer millionths."""

    NEAREST = "nearest"
    OUTWARD = "outward"


@dataclass(frozen=True, slots=True)
class NormalizedPagePoint:
    """One closed-edge point in full-page normalized millionths."""

    x: int
    y: int

    def __post_init__(self) -> None:
        if (
            type(self.x) is not int
            or type(self.y) is not int
            or not 0 <= self.x <= NORMALIZED_PAGE_MILLIONTHS
            or not 0 <= self.y <= NORMALIZED_PAGE_MILLIONTHS
        ):
            reject_page_coordinate(PageCoordinateErrorCode.POINT_OUT_OF_BOUNDS, "normalized.point")


@dataclass(frozen=True, slots=True)
class NormalizedPageBox:
    """Positive half-open box in the released manifest coordinate space."""

    x: int
    y: int
    width: int
    height: int
    coordinate_space: str = NORMALIZED_PAGE_COORDINATE_SPACE

    def __post_init__(self) -> None:
        values = (self.x, self.y, self.width, self.height)
        if (
            type(self.coordinate_space) is not str
            or self.coordinate_space != NORMALIZED_PAGE_COORDINATE_SPACE
            or any(type(value) is not int for value in values)
            or not 0 <= self.x < NORMALIZED_PAGE_MILLIONTHS
            or not 0 <= self.y < NORMALIZED_PAGE_MILLIONTHS
            or not 1 <= self.width <= NORMALIZED_PAGE_MILLIONTHS
            or not 1 <= self.height <= NORMALIZED_PAGE_MILLIONTHS
            or self.right > NORMALIZED_PAGE_MILLIONTHS
            or self.bottom > NORMALIZED_PAGE_MILLIONTHS
        ):
            reject_page_coordinate(PageCoordinateErrorCode.BOX_INVALID, "normalized.box")

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @classmethod
    def full_page(cls) -> Self:
        return cls(0, 0, NORMALIZED_PAGE_MILLIONTHS, NORMALIZED_PAGE_MILLIONTHS)

    @classmethod
    def from_edges(
        cls,
        left: int,
        top: int,
        right: int,
        bottom: int,
        *,
        clip_policy: NormalizedBoxClipPolicy = NormalizedBoxClipPolicy.REJECT,
    ) -> Self:
        """Build from ordered edges, clipping only when explicitly requested."""

        if (
            any(type(value) is not int for value in (left, top, right, bottom))
            or type(clip_policy) is not NormalizedBoxClipPolicy
            or left >= right
            or top >= bottom
        ):
            reject_page_coordinate(PageCoordinateErrorCode.BOX_INVALID, "normalized.box_edges")
        if clip_policy is NormalizedBoxClipPolicy.CLIP_TO_PAGE:
            left = max(0, min(NORMALIZED_PAGE_MILLIONTHS, left))
            top = max(0, min(NORMALIZED_PAGE_MILLIONTHS, top))
            right = max(0, min(NORMALIZED_PAGE_MILLIONTHS, right))
            bottom = max(0, min(NORMALIZED_PAGE_MILLIONTHS, bottom))
        if (
            left < 0
            or top < 0
            or right > NORMALIZED_PAGE_MILLIONTHS
            or bottom > NORMALIZED_PAGE_MILLIONTHS
            or left >= right
            or top >= bottom
        ):
            reject_page_coordinate(PageCoordinateErrorCode.BOX_INVALID, "normalized.box_edges")
        return cls(left, top, right - left, bottom - top)

    @classmethod
    def from_manifest(cls, value: Mapping[str, object]) -> Self:
        """Decode exactly the released normalized-box manifest shape."""

        try:
            copied = copy_manifest_document(value, context="coordinate.manifest")
        except Exception:
            reject_page_coordinate(
                PageCoordinateErrorCode.MANIFEST_INVALID,
                "coordinate.manifest",
            )
        if set(copied) != _BOX_MEMBERS:
            reject_page_coordinate(PageCoordinateErrorCode.MANIFEST_INVALID, "coordinate.manifest")
        coordinate_space = copied["coordinate_space"]
        integers = (copied["x"], copied["y"], copied["width"], copied["height"])
        if type(coordinate_space) is not str or any(type(item) is not int for item in integers):
            reject_page_coordinate(PageCoordinateErrorCode.MANIFEST_INVALID, "coordinate.manifest")
        x, y, width, height = cast(tuple[int, int, int, int], integers)
        try:
            return cls(x, y, width, height, coordinate_space)
        except PageCoordinateError:
            reject_page_coordinate(PageCoordinateErrorCode.MANIFEST_INVALID, "coordinate.manifest")

    def to_manifest(self) -> dict[str, object]:
        """Encode exactly the released normalized-box manifest shape."""

        return {
            "coordinate_space": self.coordinate_space,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }

    def contains_point(self, point: NormalizedPagePoint) -> bool:
        if type(point) is not NormalizedPagePoint:
            reject_page_coordinate(PageCoordinateErrorCode.MODEL_INVALID, "normalized.contains")
        return self.x <= point.x <= self.right and self.y <= point.y <= self.bottom

    def contains_box(self, other: NormalizedPageBox) -> bool:
        if type(other) is not NormalizedPageBox:
            reject_page_coordinate(PageCoordinateErrorCode.MODEL_INVALID, "normalized.contains")
        return (
            self.x <= other.x
            and self.y <= other.y
            and other.right <= self.right
            and other.bottom <= self.bottom
        )

    def intersection(self, other: NormalizedPageBox) -> NormalizedPageBox | None:
        """Return the explicit half-open intersection without mutating either box."""

        if type(other) is not NormalizedPageBox:
            reject_page_coordinate(PageCoordinateErrorCode.MODEL_INVALID, "normalized.intersection")
        left = max(self.x, other.x)
        top = max(self.y, other.y)
        right = min(self.right, other.right)
        bottom = min(self.bottom, other.bottom)
        if left >= right or top >= bottom:
            return None
        return NormalizedPageBox.from_edges(left, top, right, bottom)


@dataclass(frozen=True, slots=True, repr=False)
class PdfUserSpacePoint:
    """A point in raw PDF default user space (not physical 1/72-inch points)."""

    x: float
    y: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "x",
            _finite_number(self.x, MAX_PDF_USER_SPACE_COORDINATE, "pdf.point"),
        )
        object.__setattr__(
            self,
            "y",
            _finite_number(self.y, MAX_PDF_USER_SPACE_COORDINATE, "pdf.point"),
        )

    def __repr__(self) -> str:
        return "PdfUserSpacePoint(<page-values-hidden>)"


@dataclass(frozen=True, slots=True, repr=False)
class PdfTypographicPoint:
    """A PDF coordinate scaled to physical 1/72-inch typographic points."""

    x: float
    y: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "x",
            _finite_number(self.x, MAX_CONTINUOUS_COORDINATE, "pdf.typographic_point"),
        )
        object.__setattr__(
            self,
            "y",
            _finite_number(self.y, MAX_CONTINUOUS_COORDINATE, "pdf.typographic_point"),
        )

    def __repr__(self) -> str:
        return "PdfTypographicPoint(<page-values-hidden>)"


@dataclass(frozen=True, slots=True, repr=False)
class PdfUserSpaceBox:
    """A positive PDF rectangle in unrotated bottom-left user space."""

    left: float
    bottom: float
    right: float
    top: float

    def __post_init__(self) -> None:
        for name in ("left", "bottom", "right", "top"):
            object.__setattr__(
                self,
                name,
                _finite_number(
                    getattr(self, name),
                    MAX_PDF_USER_SPACE_COORDINATE,
                    "pdf.box",
                ),
            )
        if self.left >= self.right or self.bottom >= self.top:
            reject_page_coordinate(PageCoordinateErrorCode.GEOMETRY_INVALID, "pdf.box")
        _require_resolvable_span(self.left, self.right, "pdf.box")
        _require_resolvable_span(self.bottom, self.top, "pdf.box")

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.top - self.bottom

    def intersection(self, other: PdfUserSpaceBox) -> PdfUserSpaceBox | None:
        if type(other) is not PdfUserSpaceBox:
            reject_page_coordinate(PageCoordinateErrorCode.MODEL_INVALID, "pdf.intersection")
        left = max(self.left, other.left)
        bottom = max(self.bottom, other.bottom)
        right = min(self.right, other.right)
        top = min(self.top, other.top)
        if left >= right or bottom >= top:
            return None
        return PdfUserSpaceBox(left, bottom, right, top)

    def __repr__(self) -> str:
        return "PdfUserSpaceBox(<page-values-hidden>)"


@dataclass(frozen=True, slots=True, repr=False)
class PdfPageCoordinateGeometry:
    """Resolved PDF boxes, source orientation, and raw-to-physical scale."""

    media_box: PdfUserSpaceBox
    crop_box: PdfUserSpaceBox | None
    page_box: PdfPageBoxSelection
    intrinsic_rotation: ClockwiseRotation
    user_unit: float = 1.0
    effective_box: PdfUserSpaceBox = field(init=False)

    def __post_init__(self) -> None:
        if (
            type(self.media_box) is not PdfUserSpaceBox
            or (self.crop_box is not None and type(self.crop_box) is not PdfUserSpaceBox)
            or type(self.page_box) is not PdfPageBoxSelection
            or type(self.intrinsic_rotation) is not ClockwiseRotation
        ):
            reject_page_coordinate(PageCoordinateErrorCode.MODEL_INVALID, "pdf.geometry")
        unit = _finite_number(self.user_unit, MAX_PDF_USER_UNIT, "pdf.user_unit")
        if unit <= 0:
            reject_page_coordinate(PageCoordinateErrorCode.GEOMETRY_INVALID, "pdf.user_unit")
        object.__setattr__(self, "user_unit", unit)
        if self.page_box is PdfPageBoxSelection.MEDIA:
            effective = self.media_box
        else:
            crop = self.media_box if self.crop_box is None else self.crop_box
            intersection = self.media_box.intersection(crop)
            if intersection is None:
                reject_page_coordinate(PageCoordinateErrorCode.GEOMETRY_INVALID, "pdf.crop_box")
            effective = intersection
        object.__setattr__(self, "effective_box", effective)
        physical_edges = (
            effective.left * unit,
            effective.bottom * unit,
            effective.right * unit,
            effective.top * unit,
        )
        if any(
            not math.isfinite(edge) or abs(edge) > MAX_CONTINUOUS_COORDINATE
            for edge in physical_edges
        ):
            reject_page_coordinate(PageCoordinateErrorCode.GEOMETRY_INVALID, "pdf.user_unit")
        _require_resolvable_span(physical_edges[0], physical_edges[2], "pdf.user_unit")
        _require_resolvable_span(physical_edges[1], physical_edges[3], "pdf.user_unit")

    @property
    def logical_width_user_units(self) -> float:
        if self.intrinsic_rotation in {
            ClockwiseRotation.DEGREES_90,
            ClockwiseRotation.DEGREES_270,
        }:
            return self.effective_box.height
        return self.effective_box.width

    @property
    def logical_height_user_units(self) -> float:
        if self.intrinsic_rotation in {
            ClockwiseRotation.DEGREES_90,
            ClockwiseRotation.DEGREES_270,
        }:
            return self.effective_box.width
        return self.effective_box.height

    def user_to_typographic(self, point: PdfUserSpacePoint) -> PdfTypographicPoint:
        if type(point) is not PdfUserSpacePoint:
            reject_page_coordinate(PageCoordinateErrorCode.MODEL_INVALID, "pdf.typographic")
        if not self._contains_user_point(point):
            reject_page_coordinate(PageCoordinateErrorCode.POINT_OUT_OF_BOUNDS, "pdf.typographic")
        box = self.effective_box
        physical_left, physical_bottom, physical_right, physical_top = self._typographic_edges()
        if point.x == box.left:
            physical_x = physical_left
        elif point.x == box.right:
            physical_x = physical_right
        else:
            physical_x = point.x * self.user_unit
        if point.y == box.bottom:
            physical_y = physical_bottom
        elif point.y == box.top:
            physical_y = physical_top
        else:
            physical_y = point.y * self.user_unit
        return PdfTypographicPoint(physical_x, physical_y)

    def typographic_to_user(self, point: PdfTypographicPoint) -> PdfUserSpacePoint:
        if type(point) is not PdfTypographicPoint:
            reject_page_coordinate(PageCoordinateErrorCode.MODEL_INVALID, "pdf.typographic")
        physical_left, physical_bottom, physical_right, physical_top = self._typographic_edges()
        if (
            not physical_left <= point.x <= physical_right
            or not physical_bottom <= point.y <= physical_top
        ):
            reject_page_coordinate(PageCoordinateErrorCode.POINT_OUT_OF_BOUNDS, "pdf.typographic")
        box = self.effective_box
        if point.x == physical_left:
            raw_x = box.left
        elif point.x == physical_right:
            raw_x = box.right
        else:
            raw_x = min(box.right, max(box.left, point.x / self.user_unit))
        if point.y == physical_bottom:
            raw_y = box.bottom
        elif point.y == physical_top:
            raw_y = box.top
        else:
            raw_y = min(box.top, max(box.bottom, point.y / self.user_unit))
        return PdfUserSpacePoint(raw_x, raw_y)

    def _contains_user_point(self, point: PdfUserSpacePoint) -> bool:
        box = self.effective_box
        return box.left <= point.x <= box.right and box.bottom <= point.y <= box.top

    def _typographic_edges(self) -> tuple[float, float, float, float]:
        box = self.effective_box
        return (
            box.left * self.user_unit,
            box.bottom * self.user_unit,
            box.right * self.user_unit,
            box.top * self.user_unit,
        )

    def __repr__(self) -> str:
        return (
            "PdfPageCoordinateGeometry("
            f"page_box={self.page_box.value!r}, "
            f"intrinsic_rotation={int(self.intrinsic_rotation)!r}, "
            "page_values=<hidden>)"
        )


@dataclass(frozen=True, slots=True)
class RenderedImageSize:
    """Actual integer raster allocation; point coordinates use its outer edges."""

    width_pixels: int
    height_pixels: int

    def __post_init__(self) -> None:
        if (
            type(self.width_pixels) is not int
            or type(self.height_pixels) is not int
            or not 1 <= self.width_pixels <= MAX_RASTER_DIMENSION
            or not 1 <= self.height_pixels <= MAX_RASTER_DIMENSION
        ):
            reject_page_coordinate(PageCoordinateErrorCode.GEOMETRY_INVALID, "rendered.size")


@dataclass(frozen=True, slots=True)
class RenderedImagePoint:
    """A continuous top-left raster-edge coordinate, not an integer pixel index."""

    x: float
    y: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "x", _finite_number(self.x, MAX_CONTINUOUS_COORDINATE, "rendered.point")
        )
        object.__setattr__(
            self, "y", _finite_number(self.y, MAX_CONTINUOUS_COORDINATE, "rendered.point")
        )


@dataclass(frozen=True, slots=True)
class RenderedImageBox:
    """A positive half-open box in continuous raster-edge coordinates."""

    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self) -> None:
        for name in ("left", "top", "right", "bottom"):
            object.__setattr__(
                self,
                name,
                _finite_number(getattr(self, name), MAX_CONTINUOUS_COORDINATE, "rendered.box"),
            )
        if self.left < 0 or self.top < 0 or self.left >= self.right or self.top >= self.bottom:
            reject_page_coordinate(PageCoordinateErrorCode.BOX_INVALID, "rendered.box")


@dataclass(frozen=True, slots=True)
class ScreenPoint:
    """A point in logical screen/widget coordinates."""

    x: float
    y: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "x", _finite_number(self.x, MAX_CONTINUOUS_COORDINATE, "screen.point")
        )
        object.__setattr__(
            self, "y", _finite_number(self.y, MAX_CONTINUOUS_COORDINATE, "screen.point")
        )


@dataclass(frozen=True, slots=True)
class DevicePixelPoint:
    """A point after explicit logical-to-device display scaling."""

    x: float
    y: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "x", _finite_number(self.x, MAX_CONTINUOUS_COORDINATE, "device.point")
        )
        object.__setattr__(
            self, "y", _finite_number(self.y, MAX_CONTINUOUS_COORDINATE, "device.point")
        )


@dataclass(frozen=True, slots=True)
class ScreenBox:
    """A positive half-open box in logical screen/widget coordinates."""

    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self) -> None:
        for name in ("left", "top", "right", "bottom"):
            object.__setattr__(
                self,
                name,
                _finite_number(getattr(self, name), MAX_CONTINUOUS_COORDINATE, "screen.box"),
            )
        if self.left >= self.right or self.top >= self.bottom:
            reject_page_coordinate(PageCoordinateErrorCode.BOX_INVALID, "screen.box")


@dataclass(frozen=True, slots=True)
class ScreenViewport:
    """Actual page-content rectangle in logical screen coordinates."""

    left: float
    top: float
    width: float
    height: float
    device_pixel_ratio: float = 1.0

    def __post_init__(self) -> None:
        for name in ("left", "top"):
            object.__setattr__(
                self,
                name,
                _finite_number(getattr(self, name), MAX_CONTINUOUS_COORDINATE, "screen.viewport"),
            )
        for name in ("width", "height"):
            numeric = _finite_number(
                getattr(self, name),
                MAX_CONTINUOUS_COORDINATE,
                "screen.viewport",
            )
            if numeric <= 0:
                reject_page_coordinate(PageCoordinateErrorCode.SCALE_INVALID, "screen.viewport")
            object.__setattr__(self, name, numeric)
        ratio = _finite_number(
            self.device_pixel_ratio,
            MAX_DEVICE_PIXEL_RATIO,
            "screen.device_scale",
        )
        if ratio <= 0:
            reject_page_coordinate(PageCoordinateErrorCode.SCALE_INVALID, "screen.device_scale")
        object.__setattr__(self, "device_pixel_ratio", ratio)
        right = self.left + self.width
        bottom = self.top + self.height
        device_edges = (
            self.left * ratio,
            self.top * ratio,
            right * ratio,
            bottom * ratio,
        )
        if (
            not math.isfinite(right)
            or not math.isfinite(bottom)
            or abs(right) > MAX_CONTINUOUS_COORDINATE
            or abs(bottom) > MAX_CONTINUOUS_COORDINATE
            or any(
                not math.isfinite(edge) or abs(edge) > MAX_CONTINUOUS_COORDINATE
                for edge in device_edges
            )
        ):
            reject_page_coordinate(PageCoordinateErrorCode.SCALE_INVALID, "screen.viewport")
        _require_resolvable_span(self.left, right, "screen.viewport")
        _require_resolvable_span(self.top, bottom, "screen.viewport")
        _require_resolvable_span(device_edges[0], device_edges[2], "screen.device_scale")
        _require_resolvable_span(device_edges[1], device_edges[3], "screen.device_scale")

    @property
    def right(self) -> float:
        return self.left + self.width

    @property
    def bottom(self) -> float:
        return self.top + self.height

    def logical_to_device(self, point: ScreenPoint) -> DevicePixelPoint:
        if type(point) is not ScreenPoint:
            reject_page_coordinate(PageCoordinateErrorCode.MODEL_INVALID, "screen.device_scale")
        return DevicePixelPoint(
            point.x * self.device_pixel_ratio,
            point.y * self.device_pixel_ratio,
        )

    def device_to_logical(self, point: DevicePixelPoint) -> ScreenPoint:
        if type(point) is not DevicePixelPoint:
            reject_page_coordinate(PageCoordinateErrorCode.MODEL_INVALID, "screen.device_scale")
        return ScreenPoint(
            point.x / self.device_pixel_ratio,
            point.y / self.device_pixel_ratio,
        )


@dataclass(frozen=True, slots=True)
class PdfRoundTripTolerance:
    """Absolute raw-user-space tolerance after nearest-millionth quantization."""

    x_user_units: float
    y_user_units: float

    def __post_init__(self) -> None:
        for name in ("x_user_units", "y_user_units"):
            numeric = _finite_number(
                getattr(self, name),
                MAX_PDF_USER_SPACE_COORDINATE,
                "pdf.tolerance",
            )
            if numeric < 0:
                reject_page_coordinate(PageCoordinateErrorCode.MODEL_INVALID, "pdf.tolerance")
            object.__setattr__(self, name, numeric)


@dataclass(frozen=True, slots=True, repr=False)
class PageCoordinateTransform:
    """Staged map from source PDF space through manifest and display spaces."""

    pdf_geometry: PdfPageCoordinateGeometry | None = None
    manifest_crop: NormalizedPageBox = field(default_factory=NormalizedPageBox.full_page)
    manifest_rotation: ClockwiseRotation = ClockwiseRotation.DEGREES_0

    def __post_init__(self) -> None:
        if (
            self.pdf_geometry is not None
            and type(self.pdf_geometry) is not PdfPageCoordinateGeometry
        ) or (
            type(self.manifest_crop) is not NormalizedPageBox
            or type(self.manifest_rotation) is not ClockwiseRotation
        ):
            reject_page_coordinate(PageCoordinateErrorCode.MODEL_INVALID, "coordinate.transform")

    def normalized_to_pdf(self, point: NormalizedPagePoint) -> PdfUserSpacePoint:
        """Map post-intrinsic full-page intent to raw PDF default user space."""

        if type(point) is not NormalizedPagePoint:
            reject_page_coordinate(PageCoordinateErrorCode.MODEL_INVALID, "coordinate.pdf")
        geometry = self._require_pdf_geometry()
        u = Fraction(point.x, NORMALIZED_PAGE_MILLIONTHS)
        v = Fraction(point.y, NORMALIZED_PAGE_MILLIONTHS)
        box = geometry.effective_box
        left = Fraction.from_float(box.left)
        bottom = Fraction.from_float(box.bottom)
        right = Fraction.from_float(box.right)
        top = Fraction.from_float(box.top)
        width = right - left
        height = top - bottom
        rotation = geometry.intrinsic_rotation
        if rotation is ClockwiseRotation.DEGREES_0:
            x, y = left + u * width, top - v * height
        elif rotation is ClockwiseRotation.DEGREES_90:
            x, y = left + v * width, bottom + u * height
        elif rotation is ClockwiseRotation.DEGREES_180:
            x, y = right - u * width, bottom + v * height
        else:
            x, y = right - v * width, top - u * height
        return PdfUserSpacePoint(float(x), float(y))

    def pdf_to_normalized(self, point: PdfUserSpacePoint) -> NormalizedPagePoint:
        """Map a raw PDF point and quantize nearest to persisted millionths."""

        u, v = self._pdf_to_normalized_fractions(point)
        return NormalizedPagePoint(
            _quantize_normalized(u, "coordinate.pdf"),
            _quantize_normalized(v, "coordinate.pdf"),
        )

    def normalized_to_rendered(
        self,
        point: NormalizedPagePoint,
        size: RenderedImageSize,
    ) -> RenderedImagePoint:
        """Map visible full-page intent to actual raster outer-edge coordinates."""

        ox, oy = self._normalized_to_oriented_fractions(point)
        _require_rendered_size(size)
        return RenderedImagePoint(float(ox * size.width_pixels), float(oy * size.height_pixels))

    def rendered_to_normalized(
        self,
        point: RenderedImagePoint,
        size: RenderedImageSize,
    ) -> NormalizedPagePoint:
        """Invert a raster-edge point and quantize nearest to persisted millionths."""

        u, v = self._rendered_to_normalized_millionths(point, size)
        return NormalizedPagePoint(
            _round_half_up(u),
            _round_half_up(v),
        )

    def rendered_to_screen(
        self,
        point: RenderedImagePoint,
        size: RenderedImageSize,
        viewport: ScreenViewport,
    ) -> ScreenPoint:
        """Map through the actual page-content viewport, excluding letterboxing."""

        _require_rendered_point_in_size(point, size)
        _require_viewport(viewport)
        if point.x == 0:
            screen_x = viewport.left
        elif point.x == size.width_pixels:
            screen_x = viewport.right
        else:
            screen_x = viewport.left + point.x * viewport.width / size.width_pixels
        if point.y == 0:
            screen_y = viewport.top
        elif point.y == size.height_pixels:
            screen_y = viewport.bottom
        else:
            screen_y = viewport.top + point.y * viewport.height / size.height_pixels
        return ScreenPoint(
            screen_x,
            screen_y,
        )

    def screen_to_rendered(
        self,
        point: ScreenPoint,
        size: RenderedImageSize,
        viewport: ScreenViewport,
    ) -> RenderedImagePoint:
        """Invert a logical viewport point without applying device scaling twice."""

        if type(point) is not ScreenPoint:
            reject_page_coordinate(PageCoordinateErrorCode.MODEL_INVALID, "coordinate.screen")
        _require_rendered_size(size)
        _require_viewport(viewport)
        if (
            not viewport.left <= point.x <= viewport.right
            or not viewport.top <= point.y <= viewport.bottom
        ):
            reject_page_coordinate(PageCoordinateErrorCode.POINT_OUT_OF_BOUNDS, "coordinate.screen")
        if point.x == viewport.left:
            rendered_x = 0.0
        elif point.x == viewport.right:
            rendered_x = float(size.width_pixels)
        else:
            rendered_x = (point.x - viewport.left) * size.width_pixels / viewport.width
        if point.y == viewport.top:
            rendered_y = 0.0
        elif point.y == viewport.bottom:
            rendered_y = float(size.height_pixels)
        else:
            rendered_y = (point.y - viewport.top) * size.height_pixels / viewport.height
        return RenderedImagePoint(
            min(float(size.width_pixels), max(0.0, rendered_x)),
            min(float(size.height_pixels), max(0.0, rendered_y)),
        )

    def normalized_to_screen(
        self,
        point: NormalizedPagePoint,
        size: RenderedImageSize,
        viewport: ScreenViewport,
    ) -> ScreenPoint:
        return self.rendered_to_screen(self.normalized_to_rendered(point, size), size, viewport)

    def screen_to_normalized(
        self,
        point: ScreenPoint,
        size: RenderedImageSize,
        viewport: ScreenViewport,
    ) -> NormalizedPagePoint:
        return self.rendered_to_normalized(self.screen_to_rendered(point, size, viewport), size)

    def visible_part(self, box: NormalizedPageBox) -> NormalizedPageBox | None:
        """Explicitly intersect render visibility without changing persisted intent."""

        if type(box) is not NormalizedPageBox:
            reject_page_coordinate(PageCoordinateErrorCode.MODEL_INVALID, "coordinate.visible_box")
        return self.manifest_crop.intersection(box)

    def normalized_box_to_rendered(
        self,
        box: NormalizedPageBox,
        size: RenderedImageSize,
    ) -> RenderedImageBox:
        """Map a box wholly inside the active crop; callers clip explicitly first."""

        if type(box) is not NormalizedPageBox:
            reject_page_coordinate(PageCoordinateErrorCode.MODEL_INVALID, "coordinate.rendered_box")
        if not self.manifest_crop.contains_box(box):
            reject_page_coordinate(PageCoordinateErrorCode.BOX_INVALID, "coordinate.rendered_box")
        _require_rendered_size(size)
        points = (
            NormalizedPagePoint(box.x, box.y),
            NormalizedPagePoint(box.right, box.y),
            NormalizedPagePoint(box.right, box.bottom),
            NormalizedPagePoint(box.x, box.bottom),
        )
        mapped = [self.normalized_to_rendered(point, size) for point in points]
        return RenderedImageBox(
            min(point.x for point in mapped),
            min(point.y for point in mapped),
            max(point.x for point in mapped),
            max(point.y for point in mapped),
        )

    def rendered_box_to_normalized(
        self,
        box: RenderedImageBox,
        size: RenderedImageSize,
        *,
        quantization: BoxQuantization = BoxQuantization.OUTWARD,
    ) -> NormalizedPageBox:
        """Invert a rendered box using an explicit edge-quantization policy."""

        if type(box) is not RenderedImageBox or type(quantization) is not BoxQuantization:
            reject_page_coordinate(PageCoordinateErrorCode.MODEL_INVALID, "coordinate.rendered_box")
        _require_rendered_size(size)
        if box.right > size.width_pixels or box.bottom > size.height_pixels:
            reject_page_coordinate(PageCoordinateErrorCode.BOX_INVALID, "coordinate.rendered_box")
        corners = (
            RenderedImagePoint(box.left, box.top),
            RenderedImagePoint(box.right, box.top),
            RenderedImagePoint(box.right, box.bottom),
            RenderedImagePoint(box.left, box.bottom),
        )
        mapped = [self._rendered_to_normalized_millionths(point, size) for point in corners]
        left_value = min(point[0] for point in mapped)
        top_value = min(point[1] for point in mapped)
        right_value = max(point[0] for point in mapped)
        bottom_value = max(point[1] for point in mapped)
        if quantization is BoxQuantization.OUTWARD:
            left = _floor_fraction(left_value)
            top = _floor_fraction(top_value)
            right = _ceil_fraction(right_value)
            bottom = _ceil_fraction(bottom_value)
        else:
            left = _round_half_up(left_value)
            top = _round_half_up(top_value)
            right = _round_half_up(right_value)
            bottom = _round_half_up(bottom_value)
        return NormalizedPageBox.from_edges(left, top, right, bottom)

    def normalized_box_to_screen(
        self,
        box: NormalizedPageBox,
        size: RenderedImageSize,
        viewport: ScreenViewport,
    ) -> ScreenBox:
        rendered = self.normalized_box_to_rendered(box, size)
        top_left = self.rendered_to_screen(
            RenderedImagePoint(rendered.left, rendered.top), size, viewport
        )
        bottom_right = self.rendered_to_screen(
            RenderedImagePoint(rendered.right, rendered.bottom),
            size,
            viewport,
        )
        return ScreenBox(top_left.x, top_left.y, bottom_right.x, bottom_right.y)

    def screen_box_to_normalized(
        self,
        box: ScreenBox,
        size: RenderedImageSize,
        viewport: ScreenViewport,
        *,
        quantization: BoxQuantization = BoxQuantization.OUTWARD,
    ) -> NormalizedPageBox:
        if type(box) is not ScreenBox:
            reject_page_coordinate(PageCoordinateErrorCode.MODEL_INVALID, "coordinate.screen_box")
        top_left = self.screen_to_rendered(ScreenPoint(box.left, box.top), size, viewport)
        bottom_right = self.screen_to_rendered(ScreenPoint(box.right, box.bottom), size, viewport)
        rendered = RenderedImageBox(top_left.x, top_left.y, bottom_right.x, bottom_right.y)
        return self.rendered_box_to_normalized(rendered, size, quantization=quantization)

    def pdf_round_trip_tolerance(self) -> PdfRoundTripTolerance:
        """Return the documented absolute bound for PDF→millionths→PDF."""

        geometry = self._require_pdf_geometry()
        box = geometry.effective_box
        x_float = FLOAT_ULP_ALLOWANCE * max(
            math.ulp(box.left),
            math.ulp(box.right),
            math.ulp(box.width),
        )
        y_float = FLOAT_ULP_ALLOWANCE * max(
            math.ulp(box.bottom),
            math.ulp(box.top),
            math.ulp(box.height),
        )
        return PdfRoundTripTolerance(
            box.width / (2 * NORMALIZED_PAGE_MILLIONTHS) + x_float,
            box.height / (2 * NORMALIZED_PAGE_MILLIONTHS) + y_float,
        )

    def _pdf_to_normalized_fractions(
        self,
        point: PdfUserSpacePoint,
    ) -> tuple[Fraction, Fraction]:
        if type(point) is not PdfUserSpacePoint:
            reject_page_coordinate(PageCoordinateErrorCode.MODEL_INVALID, "coordinate.pdf")
        geometry = self._require_pdf_geometry()
        box = geometry.effective_box
        if not box.left <= point.x <= box.right or not box.bottom <= point.y <= box.top:
            reject_page_coordinate(PageCoordinateErrorCode.POINT_OUT_OF_BOUNDS, "coordinate.pdf")
        x = Fraction.from_float(point.x)
        y = Fraction.from_float(point.y)
        left = Fraction.from_float(box.left)
        bottom = Fraction.from_float(box.bottom)
        right = Fraction.from_float(box.right)
        top = Fraction.from_float(box.top)
        width = right - left
        height = top - bottom
        rotation = geometry.intrinsic_rotation
        if rotation is ClockwiseRotation.DEGREES_0:
            u, v = (x - left) / width, (top - y) / height
        elif rotation is ClockwiseRotation.DEGREES_90:
            u, v = (y - bottom) / height, (x - left) / width
        elif rotation is ClockwiseRotation.DEGREES_180:
            u, v = (right - x) / width, (y - bottom) / height
        else:
            u, v = (top - y) / height, (right - x) / width
        return u, v

    def _normalized_to_oriented_fractions(
        self,
        point: NormalizedPagePoint,
    ) -> tuple[Fraction, Fraction]:
        if type(point) is not NormalizedPagePoint:
            reject_page_coordinate(PageCoordinateErrorCode.MODEL_INVALID, "coordinate.rendered")
        crop = self.manifest_crop
        if not crop.contains_point(point):
            reject_page_coordinate(PageCoordinateErrorCode.POINT_OUT_OF_BOUNDS, "coordinate.crop")
        a = Fraction(point.x - crop.x, crop.width)
        b = Fraction(point.y - crop.y, crop.height)
        return _rotate_clockwise(a, b, self.manifest_rotation)

    def _rendered_to_normalized_millionths(
        self,
        point: RenderedImagePoint,
        size: RenderedImageSize,
    ) -> tuple[Fraction, Fraction]:
        _require_rendered_point_in_size(point, size)
        ox = Fraction.from_float(point.x) / size.width_pixels
        oy = Fraction.from_float(point.y) / size.height_pixels
        a, b = _inverse_rotate_clockwise(ox, oy, self.manifest_rotation)
        crop = self.manifest_crop
        return Fraction(crop.x, 1) + a * crop.width, Fraction(crop.y, 1) + b * crop.height

    def _require_pdf_geometry(self) -> PdfPageCoordinateGeometry:
        geometry = self.pdf_geometry
        if geometry is None:
            reject_page_coordinate(PageCoordinateErrorCode.MODEL_INVALID, "coordinate.pdf")
        return geometry

    def __repr__(self) -> str:
        return (
            "PageCoordinateTransform("
            f"manifest_rotation={int(self.manifest_rotation)!r}, "
            "page_values=<hidden>)"
        )


def _rotate_clockwise(
    x: Fraction,
    y: Fraction,
    rotation: ClockwiseRotation,
) -> tuple[Fraction, Fraction]:
    if rotation is ClockwiseRotation.DEGREES_0:
        return x, y
    if rotation is ClockwiseRotation.DEGREES_90:
        return 1 - y, x
    if rotation is ClockwiseRotation.DEGREES_180:
        return 1 - x, 1 - y
    return y, 1 - x


def _inverse_rotate_clockwise(
    x: Fraction,
    y: Fraction,
    rotation: ClockwiseRotation,
) -> tuple[Fraction, Fraction]:
    if rotation is ClockwiseRotation.DEGREES_0:
        return x, y
    if rotation is ClockwiseRotation.DEGREES_90:
        return y, 1 - x
    if rotation is ClockwiseRotation.DEGREES_180:
        return 1 - x, 1 - y
    return 1 - y, x


def _quantize_normalized(value: Fraction, context: str) -> int:
    if value < 0 or value > 1:
        reject_page_coordinate(PageCoordinateErrorCode.POINT_OUT_OF_BOUNDS, context)
    return _round_half_up(value * NORMALIZED_PAGE_MILLIONTHS)


def _round_half_up(value: Fraction) -> int:
    if value < 0:
        return -_round_half_up(-value)
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def _floor_fraction(value: Fraction) -> int:
    return value.numerator // value.denominator


def _ceil_fraction(value: Fraction) -> int:
    return -(-value.numerator // value.denominator)


def _finite_number(value: object, maximum_magnitude: float | int, context: str) -> float:
    if type(value) is int:
        if abs(value) > maximum_magnitude:
            reject_page_coordinate(PageCoordinateErrorCode.GEOMETRY_INVALID, context)
        numeric = float(value)
    elif type(value) is float:
        numeric = value
    else:
        reject_page_coordinate(PageCoordinateErrorCode.MODEL_INVALID, context)
    if not math.isfinite(numeric) or abs(numeric) > maximum_magnitude:
        reject_page_coordinate(PageCoordinateErrorCode.GEOMETRY_INVALID, context)
    return numeric


def _require_resolvable_span(start: float, end: float, context: str) -> None:
    """Reject a float span that cannot preserve nearest-millionth placement."""

    span = end - start
    uncertainty = FLOAT_ULP_ALLOWANCE * max(
        math.ulp(start),
        math.ulp(end),
        math.ulp(span),
    )
    if (
        not math.isfinite(span)
        or span <= 0
        or uncertainty * (2 * NORMALIZED_PAGE_MILLIONTHS) > span
    ):
        code = (
            PageCoordinateErrorCode.SCALE_INVALID
            if context.startswith("screen.")
            else PageCoordinateErrorCode.GEOMETRY_INVALID
        )
        reject_page_coordinate(code, context)


def _require_rendered_size(value: RenderedImageSize) -> None:
    if type(value) is not RenderedImageSize:
        reject_page_coordinate(PageCoordinateErrorCode.MODEL_INVALID, "rendered.size")


def _require_viewport(value: ScreenViewport) -> None:
    if type(value) is not ScreenViewport:
        reject_page_coordinate(PageCoordinateErrorCode.MODEL_INVALID, "screen.viewport")


def _require_rendered_point_in_size(
    point: RenderedImagePoint,
    size: RenderedImageSize,
) -> None:
    if type(point) is not RenderedImagePoint:
        reject_page_coordinate(PageCoordinateErrorCode.MODEL_INVALID, "rendered.point")
    _require_rendered_size(size)
    if not 0 <= point.x <= size.width_pixels or not 0 <= point.y <= size.height_pixels:
        reject_page_coordinate(PageCoordinateErrorCode.POINT_OUT_OF_BOUNDS, "rendered.point")

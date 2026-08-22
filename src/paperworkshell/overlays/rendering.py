"""Pure contracts for bounded, deterministic visual-overlay PDF rendering.

The concrete font, image, and PDF engines remain in the worker-side PDF
adapter.  This module names only page geometry, caller-owned streams, resource
budgets, placements, and content-free results.
"""

from __future__ import annotations

import math
import re
from collections.abc import Buffer, Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from typing import Final, Never, Protocol, Self
from uuid import UUID

from paperworkshell.domain.page_coordinates import (
    NORMALIZED_PAGE_MILLIONTHS,
    ClockwiseRotation,
    NormalizedPageBox,
    PageCoordinateError,
    PdfPageCoordinateGeometry,
    PdfUserSpacePoint,
)
from paperworkshell.domain.resource_policy import ResourcePolicy
from paperworkshell.overlays.models import (
    MAX_OVERLAY_TEXT_CODE_POINTS,
    MAX_OVERLAYS,
    OverlayCollection,
    OverlayKind,
    OverlayProjection,
    OverlayRecord,
    OverlayVisibility,
    SignatureImageOverlayPayload,
    TextualOverlayPayload,
)

OVERLAY_RENDER_REQUEST_VERSION: Final = 1
OVERLAY_RENDER_RESULT_VERSION: Final = 1
OVERLAY_RASTER_ASSET_VERSION: Final = 1
OVERLAY_RENDER_POINT_QUANTUM: Final = Decimal("0.01")
MAX_OVERLAY_CANVAS_POINTS: Final = 14_400
MAX_OVERLAY_RENDER_DIMENSION_PIXELS: Final = 32_768
MAX_OVERLAY_RENDER_PIXELS: Final = 268_435_456
MAX_OVERLAY_RENDER_BYTES: Final = 1_073_741_824
MAX_OVERLAY_RENDER_OBJECTS: Final = 100_000
MAX_OVERLAY_RENDER_WALL_TIME_MS: Final = 3_600_000
OVERLAY_RGBA8_BYTES_PER_PIXEL: Final = 4
OVERLAY_RASTER_PIXEL_LAYOUT_V1: Final = "rgba8-top-down-straight-alpha"
OVERLAY_RASTER_COLOR_INTERPRETATION_V1: Final = "device-rgb-no-profile"
OVERLAY_RASTER_PEAK_MULTIPLIER: Final = 10
OVERLAY_OUTPUT_PEAK_MULTIPLIER: Final = 6
OVERLAY_ENGINE_MEMORY_RESERVE_DIVISOR: Final = 4

_CONTEXT_TOKEN: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")


class OverlayRenderErrorCode(StrEnum):
    """Closed registry of safe overlay-rendering failures."""

    MODEL_INVALID = "OVERLAY_RENDER_MODEL_INVALID"
    GEOMETRY_INVALID = "OVERLAY_RENDER_GEOMETRY_INVALID"
    FONT_UNAVAILABLE = "OVERLAY_RENDER_FONT_UNAVAILABLE"
    FONT_UNSAFE = "OVERLAY_RENDER_FONT_UNSAFE"
    GLYPH_UNSUPPORTED = "OVERLAY_RENDER_GLYPH_UNSUPPORTED"
    TEXT_UNSUPPORTED = "OVERLAY_RENDER_TEXT_UNSUPPORTED"
    TEXT_DOES_NOT_FIT = "OVERLAY_RENDER_TEXT_DOES_NOT_FIT"
    ASSET_MISSING = "OVERLAY_RENDER_ASSET_MISSING"
    ASSET_CHANGED = "OVERLAY_RENDER_ASSET_CHANGED"
    ASSET_UNSUPPORTED = "OVERLAY_RENDER_ASSET_UNSUPPORTED"
    LIMIT_EXCEEDED = "OVERLAY_RENDER_LIMIT_EXCEEDED"
    CANCELLED = "OVERLAY_RENDER_CANCELLED"
    TIMED_OUT = "OVERLAY_RENDER_TIMED_OUT"
    DEPENDENCY_UNAVAILABLE = "OVERLAY_RENDER_DEPENDENCY_UNAVAILABLE"
    DEPENDENCY_UNSAFE = "OVERLAY_RENDER_DEPENDENCY_UNSAFE"
    DEPENDENCY_FAILED = "OVERLAY_RENDER_DEPENDENCY_FAILED"
    DESTINATION_FAILED = "OVERLAY_RENDER_DESTINATION_FAILED"
    SECURITY_VERIFICATION_FAILED = "OVERLAY_RENDER_SECURITY_VERIFICATION_FAILED"
    RESULT_INVALID = "OVERLAY_RENDER_RESULT_INVALID"
    INTERNAL_ERROR = "OVERLAY_RENDER_INTERNAL_ERROR"


_SAFE_ERROR_MESSAGES: Final[dict[OverlayRenderErrorCode, str]] = {
    OverlayRenderErrorCode.MODEL_INVALID: (
        "PaperWorkShell received invalid visual-overlay rendering data."
    ),
    OverlayRenderErrorCode.GEOMETRY_INVALID: (
        "The visual-overlay page geometry cannot be rendered safely."
    ),
    OverlayRenderErrorCode.FONT_UNAVAILABLE: ("The selected visual-overlay font is not available."),
    OverlayRenderErrorCode.FONT_UNSAFE: (
        "The selected visual-overlay font does not match the approved profile."
    ),
    OverlayRenderErrorCode.GLYPH_UNSUPPORTED: (
        "The selected visual-overlay font cannot display every requested character."
    ),
    OverlayRenderErrorCode.TEXT_UNSUPPORTED: (
        "The visual-overlay text contains unsupported formatting characters."
    ),
    OverlayRenderErrorCode.TEXT_DOES_NOT_FIT: (
        "The visual-overlay text does not fit its current area at the selected size."
    ),
    OverlayRenderErrorCode.ASSET_MISSING: ("A required visual-overlay image is not available."),
    OverlayRenderErrorCode.ASSET_CHANGED: (
        "A visual-overlay image changed before rendering completed."
    ),
    OverlayRenderErrorCode.ASSET_UNSUPPORTED: (
        "The visual-overlay image cannot be rendered safely."
    ),
    OverlayRenderErrorCode.LIMIT_EXCEEDED: (
        "Visual-overlay rendering exceeds a configured safety limit."
    ),
    OverlayRenderErrorCode.CANCELLED: ("Visual-overlay rendering was cancelled before completion."),
    OverlayRenderErrorCode.TIMED_OUT: (
        "Visual-overlay rendering exceeded its configured time limit."
    ),
    OverlayRenderErrorCode.DEPENDENCY_UNAVAILABLE: (
        "A required visual-overlay rendering component is not available."
    ),
    OverlayRenderErrorCode.DEPENDENCY_UNSAFE: (
        "A visual-overlay rendering component does not match the approved profile."
    ),
    OverlayRenderErrorCode.DEPENDENCY_FAILED: (
        "A visual-overlay rendering component could not complete safely."
    ),
    OverlayRenderErrorCode.DESTINATION_FAILED: (
        "PaperWorkShell could not write the visual-overlay page safely."
    ),
    OverlayRenderErrorCode.SECURITY_VERIFICATION_FAILED: (
        "The generated visual-overlay page did not pass its security checks."
    ),
    OverlayRenderErrorCode.RESULT_INVALID: (
        "PaperWorkShell received an invalid visual-overlay rendering result."
    ),
    OverlayRenderErrorCode.INTERNAL_ERROR: (
        "PaperWorkShell could not render the visual-overlay page safely."
    ),
}


@dataclass(slots=True, repr=False)
class OverlayRenderError(ValueError):
    """Stable failure containing no text, image bytes, font paths, or native detail."""

    code: OverlayRenderErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not OverlayRenderErrorCode:
            self.code = OverlayRenderErrorCode.INTERNAL_ERROR
        if type(self.context) is not str or _CONTEXT_TOKEN.fullmatch(self.context) is None:
            self.context = "overlay.rendering"
        self.safe_message = _SAFE_ERROR_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"OverlayRenderError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_overlay_rendering(code: OverlayRenderErrorCode, context: str) -> Never:
    """Raise one closed failure and discard any active engine exception chain."""

    error = OverlayRenderError(code=code, context=context)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


def _point_value(value: object, *, context: str, positive: bool = False) -> float:
    if type(value) not in {int, float, Decimal} or isinstance(value, bool):
        reject_overlay_rendering(OverlayRenderErrorCode.GEOMETRY_INVALID, context)
    try:
        numeric = Decimal(str(value))
    except InvalidOperation, ValueError:
        reject_overlay_rendering(OverlayRenderErrorCode.GEOMETRY_INVALID, context)
    if not numeric.is_finite() or abs(numeric) > MAX_OVERLAY_CANVAS_POINTS:
        reject_overlay_rendering(OverlayRenderErrorCode.GEOMETRY_INVALID, context)
    quantized = numeric.quantize(OVERLAY_RENDER_POINT_QUANTUM, rounding=ROUND_HALF_UP)
    if positive and quantized <= 0:
        reject_overlay_rendering(OverlayRenderErrorCode.GEOMETRY_INVALID, context)
    return float(quantized)


@dataclass(frozen=True, slots=True, repr=False)
class OverlayCanvasBox:
    """A positive top-left-origin rectangle on the logical overlay canvas."""

    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self) -> None:
        for name in ("left", "top", "right", "bottom"):
            object.__setattr__(
                self,
                name,
                _point_value(getattr(self, name), context="overlay.canvas_box"),
            )
        if self.left >= self.right or self.top >= self.bottom:
            reject_overlay_rendering(
                OverlayRenderErrorCode.GEOMETRY_INVALID,
                "overlay.canvas_box",
            )

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    def __repr__(self) -> str:
        return "OverlayCanvasBox(<page-values-hidden>)"


@dataclass(frozen=True, slots=True, repr=False)
class OverlayPdfPlacementMatrix:
    """Map emitted PDF bottom-left points to the source page's raw user space.

    The overlay canvas models top-left coordinates for authoring, while the
    generated one-page PDF uses ordinary bottom-left PDF coordinates.  This
    matrix is therefore applied to the generated page/Form coordinates during
    later composition, never directly to :class:`OverlayCanvasBox` edges.
    """

    a: float
    b: float
    c: float
    d: float
    e: float
    f: float

    def __post_init__(self) -> None:
        for name in ("a", "b", "c", "d", "e", "f"):
            value = getattr(self, name)
            if type(value) not in {int, float} or not math.isfinite(value):
                reject_overlay_rendering(
                    OverlayRenderErrorCode.GEOMETRY_INVALID,
                    "overlay.pdf_matrix",
                )
            object.__setattr__(self, name, float(value))

    def apply(self, x_points: float, y_points: float) -> PdfUserSpacePoint:
        if (
            type(x_points) not in {int, float}
            or type(y_points) not in {int, float}
            or not math.isfinite(x_points)
            or not math.isfinite(y_points)
            or abs(x_points) > MAX_OVERLAY_CANVAS_POINTS
            or abs(y_points) > MAX_OVERLAY_CANVAS_POINTS
        ):
            reject_overlay_rendering(
                OverlayRenderErrorCode.GEOMETRY_INVALID,
                "overlay.pdf_matrix",
            )
        x = float(x_points)
        y = float(y_points)
        try:
            return PdfUserSpacePoint(
                self.a * x + self.c * y + self.e,
                self.b * x + self.d * y + self.f,
            )
        except PageCoordinateError:
            reject_overlay_rendering(
                OverlayRenderErrorCode.GEOMETRY_INVALID,
                "overlay.pdf_matrix",
            )

    def __repr__(self) -> str:
        return "OverlayPdfPlacementMatrix(<page-values-hidden>)"


@dataclass(frozen=True, slots=True, repr=False)
class OverlayPageGeometry:
    """One full logical page after intrinsic orientation, before manifest edits."""

    canvas_width_points: float
    canvas_height_points: float
    pdf_placement: OverlayPdfPlacementMatrix

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "canvas_width_points",
            _point_value(
                self.canvas_width_points,
                context="overlay.canvas_size",
                positive=True,
            ),
        )
        object.__setattr__(
            self,
            "canvas_height_points",
            _point_value(
                self.canvas_height_points,
                context="overlay.canvas_size",
                positive=True,
            ),
        )
        if type(self.pdf_placement) is not OverlayPdfPlacementMatrix:
            reject_overlay_rendering(
                OverlayRenderErrorCode.GEOMETRY_INVALID,
                "overlay.pdf_matrix",
            )

    @classmethod
    def from_pdf_geometry(cls, geometry: PdfPageCoordinateGeometry) -> Self:
        """Create a logical canvas and exact edge-preserving raw-PDF placement."""

        if type(geometry) is not PdfPageCoordinateGeometry:
            reject_overlay_rendering(
                OverlayRenderErrorCode.GEOMETRY_INVALID,
                "overlay.pdf_geometry",
            )
        box = geometry.effective_box
        width = _point_value(
            geometry.logical_width_user_units * geometry.user_unit,
            context="overlay.canvas_size",
            positive=True,
        )
        height = _point_value(
            geometry.logical_height_user_units * geometry.user_unit,
            context="overlay.canvas_size",
            positive=True,
        )
        raw_width = box.width
        raw_height = box.height
        rotation = geometry.intrinsic_rotation
        if rotation is ClockwiseRotation.DEGREES_0:
            matrix = OverlayPdfPlacementMatrix(
                raw_width / width,
                0.0,
                0.0,
                raw_height / height,
                box.left,
                box.bottom,
            )
        elif rotation is ClockwiseRotation.DEGREES_90:
            matrix = OverlayPdfPlacementMatrix(
                0.0,
                raw_height / width,
                -raw_width / height,
                0.0,
                box.right,
                box.bottom,
            )
        elif rotation is ClockwiseRotation.DEGREES_180:
            matrix = OverlayPdfPlacementMatrix(
                -raw_width / width,
                0.0,
                0.0,
                -raw_height / height,
                box.right,
                box.top,
            )
        else:
            matrix = OverlayPdfPlacementMatrix(
                0.0,
                -raw_height / width,
                raw_width / height,
                0.0,
                box.left,
                box.top,
            )
        return cls(width, height, matrix)

    def box_for(self, box: NormalizedPageBox) -> OverlayCanvasBox:
        """Map one full-page normalized box without applying crop or rotation."""

        if type(box) is not NormalizedPageBox:
            reject_overlay_rendering(
                OverlayRenderErrorCode.GEOMETRY_INVALID,
                "overlay.normalized_box",
            )
        scale = Decimal(NORMALIZED_PAGE_MILLIONTHS)
        width = Decimal(str(self.canvas_width_points))
        height = Decimal(str(self.canvas_height_points))
        left = Decimal(box.x) * width / scale
        top = Decimal(box.y) * height / scale
        right = Decimal(box.right) * width / scale
        bottom = Decimal(box.bottom) * height / scale
        return OverlayCanvasBox(float(left), float(top), float(right), float(bottom))

    def __repr__(self) -> str:
        return "OverlayPageGeometry(<page-values-hidden>)"


@dataclass(frozen=True, slots=True)
class OverlayRenderLimits:
    """Trusted per-page budgets derived from the central resource policy."""

    maximum_overlays: int
    maximum_total_text_code_points: int
    maximum_raster_width_pixels: int
    maximum_raster_height_pixels: int
    maximum_raster_pixels: int
    maximum_raster_bytes: int
    maximum_total_raster_bytes: int
    maximum_output_bytes: int
    maximum_memory_bytes: int
    maximum_pdf_objects: int
    io_chunk_bytes: int
    wall_time_ms: int

    def __post_init__(self) -> None:
        values = tuple(getattr(self, name) for name in self.__dataclass_fields__)
        if any(type(value) is not int or value < 1 for value in values):
            reject_overlay_rendering(OverlayRenderErrorCode.MODEL_INVALID, "overlay.limits")
        if (
            self.maximum_overlays > MAX_OVERLAYS
            or self.maximum_total_text_code_points > MAX_OVERLAYS * MAX_OVERLAY_TEXT_CODE_POINTS
            or self.maximum_raster_width_pixels > MAX_OVERLAY_RENDER_DIMENSION_PIXELS
            or self.maximum_raster_height_pixels > MAX_OVERLAY_RENDER_DIMENSION_PIXELS
            or self.maximum_raster_pixels > MAX_OVERLAY_RENDER_PIXELS
            or self.maximum_raster_bytes > MAX_OVERLAY_RENDER_BYTES
            or self.maximum_total_raster_bytes > MAX_OVERLAY_RENDER_BYTES
            or self.maximum_output_bytes > MAX_OVERLAY_RENDER_BYTES
            or self.maximum_memory_bytes > MAX_OVERLAY_RENDER_BYTES
            or self.maximum_pdf_objects > MAX_OVERLAY_RENDER_OBJECTS
            or self.io_chunk_bytes > MAX_OVERLAY_RENDER_BYTES
            or self.wall_time_ms > MAX_OVERLAY_RENDER_WALL_TIME_MS
        ):
            reject_overlay_rendering(OverlayRenderErrorCode.MODEL_INVALID, "overlay.limits")
        if self.maximum_raster_bytes > self.maximum_memory_bytes:
            reject_overlay_rendering(OverlayRenderErrorCode.MODEL_INVALID, "overlay.limits")
        if self.maximum_total_raster_bytes > self.maximum_memory_bytes:
            reject_overlay_rendering(OverlayRenderErrorCode.MODEL_INVALID, "overlay.limits")
        if self.maximum_raster_bytes > self.maximum_total_raster_bytes:
            reject_overlay_rendering(OverlayRenderErrorCode.MODEL_INVALID, "overlay.limits")
        shared_peak = (
            self.maximum_total_raster_bytes * OVERLAY_RASTER_PEAK_MULTIPLIER
            + self.maximum_output_bytes * OVERLAY_OUTPUT_PEAK_MULTIPLIER
        )
        available_peak = (
            self.maximum_memory_bytes
            * (OVERLAY_ENGINE_MEMORY_RESERVE_DIVISOR - 1)
            // OVERLAY_ENGINE_MEMORY_RESERVE_DIVISOR
        )
        if shared_peak > available_peak:
            reject_overlay_rendering(OverlayRenderErrorCode.MODEL_INVALID, "overlay.limits")

    @classmethod
    def from_resource_policy(cls, policy: ResourcePolicy) -> Self:
        """Bind conservative shared-working-set limits to the process policy.

        The adapter can temporarily hold decoded RGBA, encoded PNG, engine image
        state, raw PDF, pikepdf input/output, and immutable result copies at the
        same time.  The enforced peak equation reserves ten copies of the raster
        allowance, six copies of the output allowance, and one quarter of worker
        memory for fonts/engines/models.  Overlay count assumes at least 4 KiB of
        state per item; text receives at most one code point per KiB.
        """

        if type(policy) is not ResourcePolicy:
            reject_overlay_rendering(OverlayRenderErrorCode.MODEL_INVALID, "overlay.policy")
        pixels = min(policy.maximum_preview_pixels_per_page, MAX_OVERLAY_RENDER_PIXELS)
        raster_bytes = min(
            pixels * OVERLAY_RGBA8_BYTES_PER_PIXEL,
            policy.worker_process_memory_bytes // 32,
        )
        return cls(
            maximum_overlays=min(
                MAX_OVERLAYS,
                max(1, policy.worker_process_memory_bytes // 4_096),
            ),
            maximum_total_text_code_points=min(
                MAX_OVERLAYS * MAX_OVERLAY_TEXT_CODE_POINTS,
                max(1, policy.worker_process_memory_bytes // 1_024),
            ),
            maximum_raster_width_pixels=MAX_OVERLAY_RENDER_DIMENSION_PIXELS,
            maximum_raster_height_pixels=MAX_OVERLAY_RENDER_DIMENSION_PIXELS,
            maximum_raster_pixels=pixels,
            maximum_raster_bytes=raster_bytes,
            maximum_total_raster_bytes=min(
                policy.maximum_source_bytes,
                policy.worker_process_memory_bytes // 32,
            ),
            maximum_output_bytes=min(
                policy.maximum_temporary_storage_bytes,
                policy.worker_process_memory_bytes // 16,
            ),
            maximum_memory_bytes=policy.worker_process_memory_bytes,
            maximum_pdf_objects=MAX_OVERLAY_RENDER_OBJECTS,
            io_chunk_bytes=policy.import_io_chunk_bytes,
            wall_time_ms=policy.worker_cpu_time_ms,
        )


@dataclass(frozen=True, slots=True, repr=False)
class OverlayRasterAsset:
    """Authenticated pixels in the fixed v1 layout supplied by the image boundary.

    Rows run top-to-bottom without padding. Each pixel is four 8-bit samples in
    red, green, blue, alpha order. RGB samples are preserved as unprofiled PDF
    DeviceRGB values and alpha is straight/unassociated, never premultiplied.
    """

    asset_id: UUID
    width_pixels: int
    height_pixels: int
    row_stride_bytes: int
    byte_count: int
    pixel_sha256: str = field(repr=False)
    asset_version: int = OVERLAY_RASTER_ASSET_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.asset_id) is not UUID
            or self.asset_id.version != 4
            or type(self.width_pixels) is not int
            or type(self.height_pixels) is not int
            or type(self.row_stride_bytes) is not int
            or type(self.byte_count) is not int
            or type(self.pixel_sha256) is not str
            or _SHA256.fullmatch(self.pixel_sha256) is None
            or type(self.asset_version) is not int
            or self.asset_version != OVERLAY_RASTER_ASSET_VERSION
            or not 1 <= self.width_pixels <= MAX_OVERLAY_RENDER_DIMENSION_PIXELS
            or not 1 <= self.height_pixels <= MAX_OVERLAY_RENDER_DIMENSION_PIXELS
            or self.row_stride_bytes != self.width_pixels * OVERLAY_RGBA8_BYTES_PER_PIXEL
            or self.byte_count != self.row_stride_bytes * self.height_pixels
            or not 1 <= self.byte_count <= MAX_OVERLAY_RENDER_BYTES
        ):
            reject_overlay_rendering(OverlayRenderErrorCode.MODEL_INVALID, "overlay.raster")

    def __repr__(self) -> str:
        return "OverlayRasterAsset(format='rgba8', details_hidden=True)"


class OverlayRasterSource(Protocol):
    """Caller-owned v1-layout pixels; the adapter restores the original position."""

    @property
    def metadata(self) -> OverlayRasterAsset: ...

    def read(self, size: int = -1, /) -> bytes: ...

    def seek(self, offset: int, whence: int = 0, /) -> int: ...

    def tell(self) -> int: ...


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class OverlayRasterSourceSnapshot:
    """One captured source identity and its value-copied, immutable metadata."""

    asset_id: UUID
    source: OverlayRasterSource = field(repr=False)
    metadata: OverlayRasterAsset = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.asset_id) is not UUID
            or self.asset_id.version != 4
            or type(self.metadata) is not OverlayRasterAsset
            or self.metadata.asset_id != self.asset_id
        ):
            reject_overlay_rendering(OverlayRenderErrorCode.MODEL_INVALID, "overlay.raster_source")

    def __repr__(self) -> str:
        return "OverlayRasterSourceSnapshot(format='rgba8', details_hidden=True)"


class OverlayPdfDestination(Protocol):
    """Caller-owned empty operation sink; paths are never accepted.

    The adapter verifies a zero initial extent, writes the complete PDF, and
    makes a best-effort attempt to truncate back to zero on any post-write
    failure.  The caller publishes this staging sink only after the metadata
    result is returned and must discard the entire sink after every exception.
    """

    def write(self, data: Buffer, /) -> int | None: ...

    def flush(self) -> None: ...

    def seek(self, offset: int, whence: int = 0, /) -> int: ...

    def tell(self) -> int: ...

    def truncate(self, size: int | None = None, /) -> int: ...


class OverlayRenderControl(Protocol):
    """Cooperative probes backed by the owning worker deadline and cancellation."""

    def cancellation_requested(self) -> bool: ...

    def deadline_expired(self) -> bool: ...


@dataclass(frozen=True, slots=True, repr=False)
class OverlayPageRenderRequest:
    """One deterministic, low-to-high visible stack on a full logical page."""

    page_id: UUID
    geometry: OverlayPageGeometry
    active_crop: NormalizedPageBox
    projections: tuple[OverlayProjection, ...]
    request_version: int = OVERLAY_RENDER_REQUEST_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.page_id) is not UUID
            or self.page_id.version != 4
            or type(self.geometry) is not OverlayPageGeometry
            or type(self.active_crop) is not NormalizedPageBox
            or type(self.projections) is not tuple
            or type(self.request_version) is not int
            or self.request_version != OVERLAY_RENDER_REQUEST_VERSION
        ):
            reject_overlay_rendering(OverlayRenderErrorCode.MODEL_INVALID, "overlay.request")
        if any(type(item) is not OverlayProjection for item in self.projections):
            reject_overlay_rendering(OverlayRenderErrorCode.MODEL_INVALID, "overlay.request")
        prior_z = -1
        for projection in self.projections:
            record = projection.record
            if record.page_id != self.page_id or record.z_index <= prior_z:
                reject_overlay_rendering(OverlayRenderErrorCode.MODEL_INVALID, "overlay.stack")
            prior_z = record.z_index
            expected_visible = self.active_crop.intersection(record.box)
            if expected_visible is None:
                reject_overlay_rendering(OverlayRenderErrorCode.MODEL_INVALID, "overlay.visibility")
            expected_visibility = (
                OverlayVisibility.VISIBLE
                if expected_visible == record.box
                else OverlayVisibility.PARTIALLY_VISIBLE
            )
            if (
                projection.visibility is not expected_visibility
                or projection.visible_box != expected_visible
            ):
                reject_overlay_rendering(OverlayRenderErrorCode.MODEL_INVALID, "overlay.visibility")

    @classmethod
    def from_collection(
        cls,
        collection: OverlayCollection,
        page_id: UUID,
        *,
        geometry: OverlayPageGeometry,
        active_crop: NormalizedPageBox,
    ) -> Self:
        if type(collection) is not OverlayCollection:
            reject_overlay_rendering(OverlayRenderErrorCode.MODEL_INVALID, "overlay.collection")
        return cls(
            page_id=page_id,
            geometry=geometry,
            active_crop=active_crop,
            projections=collection.visible_stack(page_id, active_crop=active_crop),
        )

    @property
    def records(self) -> tuple[OverlayRecord, ...]:
        return tuple(projection.record for projection in self.projections)

    def __repr__(self) -> str:
        return f"OverlayPageRenderRequest(overlay_count={len(self.projections)})"


class OverlayTextDirection(StrEnum):
    LEFT_TO_RIGHT = "left_to_right"


@dataclass(frozen=True, slots=True, repr=False)
class OverlayTextMetrics:
    """Content-free metrics for the exact shaped line placed in one overlay box."""

    font_size_points: int
    advance_width_points: float
    ascent_points: float
    descent_points: float
    baseline_y_points: float
    glyph_count: int
    direction: OverlayTextDirection = OverlayTextDirection.LEFT_TO_RIGHT

    def __post_init__(self) -> None:
        if (
            type(self.font_size_points) is not int
            or self.font_size_points < 1
            or type(self.glyph_count) is not int
            or self.glyph_count < 0
            or type(self.direction) is not OverlayTextDirection
        ):
            reject_overlay_rendering(OverlayRenderErrorCode.RESULT_INVALID, "overlay.metrics")
        for name in (
            "advance_width_points",
            "ascent_points",
            "descent_points",
            "baseline_y_points",
        ):
            value = getattr(self, name)
            if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
                reject_overlay_rendering(OverlayRenderErrorCode.RESULT_INVALID, "overlay.metrics")
            object.__setattr__(self, name, float(value))

    def __repr__(self) -> str:
        return (
            "OverlayTextMetrics("
            f"font_size_points={self.font_size_points}, glyph_count={self.glyph_count})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class OverlayRenderedPlacement:
    """Neutral placement evidence for one input overlay, without its content."""

    overlay_id: UUID
    kind: OverlayKind
    z_index: int
    requested_box: OverlayCanvasBox
    clip_box: OverlayCanvasBox
    painted_box: OverlayCanvasBox | None
    text_metrics: OverlayTextMetrics | None = None

    def __post_init__(self) -> None:
        if (
            type(self.overlay_id) is not UUID
            or self.overlay_id.version != 4
            or type(self.kind) is not OverlayKind
            or type(self.z_index) is not int
            or self.z_index < 0
            or type(self.requested_box) is not OverlayCanvasBox
            or type(self.clip_box) is not OverlayCanvasBox
            or (self.painted_box is not None and type(self.painted_box) is not OverlayCanvasBox)
            or (self.text_metrics is not None and type(self.text_metrics) is not OverlayTextMetrics)
        ):
            reject_overlay_rendering(OverlayRenderErrorCode.RESULT_INVALID, "overlay.placement")
        is_text = self.kind in {OverlayKind.TEXT, OverlayKind.DATE, OverlayKind.INITIALS}
        if is_text != (self.text_metrics is not None):
            reject_overlay_rendering(OverlayRenderErrorCode.RESULT_INVALID, "overlay.placement")

    def __repr__(self) -> str:
        return f"OverlayRenderedPlacement(kind={self.kind.value!r}, z_index={self.z_index})"


@dataclass(frozen=True, slots=True, repr=False)
class OverlayPageRenderResult:
    """Verified one-page PDF facts returned only after the exact bytes are flushed."""

    page_id: UUID
    canvas_width_points: float
    canvas_height_points: float
    pdf_placement: OverlayPdfPlacementMatrix
    placements: tuple[OverlayRenderedPlacement, ...]
    bytes_written: int
    pdf_sha256: str = field(repr=False)
    embedded_font_count: int
    image_count: int
    result_version: int = OVERLAY_RENDER_RESULT_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.page_id) is not UUID
            or self.page_id.version != 4
            or type(self.pdf_placement) is not OverlayPdfPlacementMatrix
            or type(self.placements) is not tuple
            or any(type(item) is not OverlayRenderedPlacement for item in self.placements)
            or type(self.bytes_written) is not int
            or not 1 <= self.bytes_written <= MAX_OVERLAY_RENDER_BYTES
            or type(self.pdf_sha256) is not str
            or _SHA256.fullmatch(self.pdf_sha256) is None
            or type(self.embedded_font_count) is not int
            or self.embedded_font_count not in {0, 1}
            or type(self.image_count) is not int
            or not 0 <= self.image_count <= MAX_OVERLAYS
            or type(self.result_version) is not int
            or self.result_version != OVERLAY_RENDER_RESULT_VERSION
        ):
            reject_overlay_rendering(OverlayRenderErrorCode.RESULT_INVALID, "overlay.result")
        object.__setattr__(
            self,
            "canvas_width_points",
            _point_value(
                self.canvas_width_points,
                context="overlay.result_geometry",
                positive=True,
            ),
        )
        object.__setattr__(
            self,
            "canvas_height_points",
            _point_value(
                self.canvas_height_points,
                context="overlay.result_geometry",
                positive=True,
            ),
        )

    def __repr__(self) -> str:
        return (
            "OverlayPageRenderResult("
            f"placement_count={len(self.placements)}, bytes_written={self.bytes_written})"
        )


class OverlayPageRenderer(Protocol):
    """Replaceable worker adapter with no third-party boundary objects."""

    def render(
        self,
        request: OverlayPageRenderRequest,
        raster_sources: Mapping[UUID, OverlayRasterSource],
        destination: OverlayPdfDestination,
        limits: OverlayRenderLimits,
        control: OverlayRenderControl,
    ) -> OverlayPageRenderResult:
        """Render, verify, and flush one transparent vector-overlay PDF page."""


def validate_overlay_render_request(
    request: OverlayPageRenderRequest,
    raster_sources: Mapping[UUID, OverlayRasterSource],
    limits: OverlayRenderLimits,
) -> tuple[OverlayRasterSourceSnapshot, ...]:
    """Validate and capture exactly one bounded, immutable raster-source view."""

    if (
        type(request) is not OverlayPageRenderRequest
        or not isinstance(raster_sources, Mapping)
        or type(limits) is not OverlayRenderLimits
    ):
        reject_overlay_rendering(OverlayRenderErrorCode.MODEL_INVALID, "overlay.request_limits")
    if len(request.projections) > limits.maximum_overlays:
        reject_overlay_rendering(OverlayRenderErrorCode.LIMIT_EXCEEDED, "overlay.count")
    text_points = sum(
        len(payload.value)
        for payload in (projection.record.payload for projection in request.projections)
        if type(payload) is TextualOverlayPayload
    )
    if text_points > limits.maximum_total_text_code_points:
        reject_overlay_rendering(OverlayRenderErrorCode.LIMIT_EXCEEDED, "overlay.text_count")
    required_ids = {
        payload.asset_id
        for payload in (projection.record.payload for projection in request.projections)
        if type(payload) is SignatureImageOverlayPayload
    }
    captured: dict[UUID, OverlayRasterSourceSnapshot] = {}
    total_bytes = 0
    try:
        source_ids = iter(raster_sources)
    except Exception:
        reject_overlay_rendering(OverlayRenderErrorCode.MODEL_INVALID, "overlay.raster_sources")
    while True:
        try:
            asset_id = next(source_ids)
        except StopIteration:
            break
        except Exception:
            reject_overlay_rendering(OverlayRenderErrorCode.MODEL_INVALID, "overlay.raster_sources")
        if type(asset_id) is not UUID or asset_id not in required_ids or asset_id in captured:
            reject_overlay_rendering(OverlayRenderErrorCode.ASSET_MISSING, "overlay.raster_sources")
        try:
            source = raster_sources[asset_id]
            metadata = source.metadata
        except Exception:
            reject_overlay_rendering(OverlayRenderErrorCode.ASSET_MISSING, "overlay.raster_source")
        if type(metadata) is not OverlayRasterAsset or metadata.asset_id != asset_id:
            reject_overlay_rendering(OverlayRenderErrorCode.MODEL_INVALID, "overlay.raster_source")
        metadata_snapshot = OverlayRasterAsset(
            asset_id=metadata.asset_id,
            width_pixels=metadata.width_pixels,
            height_pixels=metadata.height_pixels,
            row_stride_bytes=metadata.row_stride_bytes,
            byte_count=metadata.byte_count,
            pixel_sha256=metadata.pixel_sha256,
            asset_version=metadata.asset_version,
        )
        pixels = metadata_snapshot.width_pixels * metadata_snapshot.height_pixels
        if (
            metadata_snapshot.width_pixels > limits.maximum_raster_width_pixels
            or metadata_snapshot.height_pixels > limits.maximum_raster_height_pixels
            or pixels > limits.maximum_raster_pixels
            or metadata_snapshot.byte_count > limits.maximum_raster_bytes
        ):
            reject_overlay_rendering(OverlayRenderErrorCode.LIMIT_EXCEEDED, "overlay.raster")
        total_bytes += metadata_snapshot.byte_count
        if total_bytes > limits.maximum_total_raster_bytes:
            reject_overlay_rendering(OverlayRenderErrorCode.LIMIT_EXCEEDED, "overlay.raster_total")
        captured[asset_id] = OverlayRasterSourceSnapshot(
            asset_id=asset_id,
            source=source,
            metadata=metadata_snapshot,
        )
    if len(captured) != len(required_ids):
        reject_overlay_rendering(OverlayRenderErrorCode.ASSET_MISSING, "overlay.raster_sources")
    return tuple(captured[asset_id] for asset_id in sorted(required_ids, key=str))


def require_overlay_render_active(control: OverlayRenderControl) -> None:
    """Fail closed when a typed cancellation or deadline probe is active or invalid."""

    try:
        cancellation = control.cancellation_requested
        deadline = control.deadline_expired
    except Exception:
        reject_overlay_rendering(OverlayRenderErrorCode.INTERNAL_ERROR, "overlay.control")
    if _control_probe(cancellation, "overlay.cancel_probe"):
        reject_overlay_rendering(OverlayRenderErrorCode.CANCELLED, "overlay.cancelled")
    if _control_probe(deadline, "overlay.deadline_probe"):
        reject_overlay_rendering(OverlayRenderErrorCode.TIMED_OUT, "overlay.deadline")


def _control_probe(probe: object, context: str) -> bool:
    if not callable(probe):
        reject_overlay_rendering(OverlayRenderErrorCode.INTERNAL_ERROR, context)
    try:
        result = probe()
    except Exception:
        reject_overlay_rendering(OverlayRenderErrorCode.INTERNAL_ERROR, context)
    if type(result) is not bool:
        reject_overlay_rendering(OverlayRenderErrorCode.INTERNAL_ERROR, context)
    return result

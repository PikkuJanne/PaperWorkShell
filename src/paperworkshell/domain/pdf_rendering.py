"""Pure, renderer-neutral contracts for bounded PDF page rasterization."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Final, Never

MAX_PDF_RENDER_PAGES: Final = 4_096
MAX_PDF_RENDER_DIMENSION_PIXELS: Final = 32_768
MAX_PDF_RENDER_PIXELS: Final = 268_435_456
MAX_PDF_RENDER_BYTES: Final = 1_073_741_824
MAX_PDF_RENDER_WALL_TIME_MS: Final = 3_600_000
PDF_RENDER_RGBA8_BYTES_PER_PIXEL: Final = 4

_CONTEXT_TOKEN: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")


class PdfPageBox(StrEnum):
    """PDF page boundary selected before the fit-box raster calculation."""

    CROP = "crop"
    MEDIA = "media"


class PdfRenderRotation(IntEnum):
    """Clockwise rotation added after the page's intrinsic rotation."""

    DEGREES_0 = 0
    DEGREES_90 = 90
    DEGREES_180 = 180
    DEGREES_270 = 270


class PdfRasterFormat(StrEnum):
    """Closed, adapter-independent raster formats."""

    RGBA8 = "rgba8"


class PdfRasterBackground(StrEnum):
    """Page background applied before transparent PDF content is composited."""

    OPAQUE_WHITE = "opaque_white"


class PdfAnnotationMode(StrEnum):
    """Fixed annotation rendering profile; forms and XFA are never initialized."""

    STORED_APPEARANCES = "stored_appearances"


class PdfRenderErrorCode(StrEnum):
    """Closed registry of safe PDF-rendering failures."""

    MODEL_INVALID = "PDF_RENDER_MODEL_INVALID"
    SOURCE_INVALID = "PDF_RENDER_SOURCE_INVALID"
    GEOMETRY_INVALID = "PDF_RENDER_GEOMETRY_INVALID"
    PAGE_OUT_OF_RANGE = "PDF_RENDER_PAGE_OUT_OF_RANGE"
    LIMIT_EXCEEDED = "PDF_RENDER_LIMIT_EXCEEDED"
    CANCELLED = "PDF_RENDER_CANCELLED"
    TIMED_OUT = "PDF_RENDER_TIMED_OUT"
    DEPENDENCY_UNAVAILABLE = "PDF_RENDER_DEPENDENCY_UNAVAILABLE"
    DEPENDENCY_UNSAFE = "PDF_RENDER_DEPENDENCY_UNSAFE"
    DEPENDENCY_FAILED = "PDF_RENDER_DEPENDENCY_FAILED"
    DESTINATION_FAILED = "PDF_RENDER_DESTINATION_FAILED"
    RESULT_INVALID = "PDF_RENDER_RESULT_INVALID"
    INTERNAL_ERROR = "PDF_RENDER_INTERNAL_ERROR"


_SAFE_ERROR_MESSAGES: Final[dict[PdfRenderErrorCode, str]] = {
    PdfRenderErrorCode.MODEL_INVALID: "PaperWorkShell received invalid page-rendering data.",
    PdfRenderErrorCode.SOURCE_INVALID: "The selected source is not a renderable PDF.",
    PdfRenderErrorCode.GEOMETRY_INVALID: "The selected PDF page has invalid geometry.",
    PdfRenderErrorCode.PAGE_OUT_OF_RANGE: "The requested PDF page is not available.",
    PdfRenderErrorCode.LIMIT_EXCEEDED: (
        "The requested page rendering exceeds a configured safety limit."
    ),
    PdfRenderErrorCode.CANCELLED: "Page rendering was cancelled before completion.",
    PdfRenderErrorCode.TIMED_OUT: "Page rendering exceeded its configured time limit.",
    PdfRenderErrorCode.DEPENDENCY_UNAVAILABLE: (
        "A required page-rendering component is not available."
    ),
    PdfRenderErrorCode.DEPENDENCY_UNSAFE: (
        "The available page-rendering component does not match the approved profile."
    ),
    PdfRenderErrorCode.DEPENDENCY_FAILED: (
        "A required page-rendering component could not complete safely."
    ),
    PdfRenderErrorCode.DESTINATION_FAILED: (
        "PaperWorkShell could not write the rendered page safely."
    ),
    PdfRenderErrorCode.RESULT_INVALID: (
        "PaperWorkShell received an invalid page-rendering result."
    ),
    PdfRenderErrorCode.INTERNAL_ERROR: "PaperWorkShell could not render the page safely.",
}


@dataclass(slots=True, repr=False)
class PdfRenderError(ValueError):
    """Stable failure containing no path, filename, pixels, or native exception text."""

    code: PdfRenderErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not PdfRenderErrorCode:
            self.code = PdfRenderErrorCode.INTERNAL_ERROR
        if type(self.context) is not str or _CONTEXT_TOKEN.fullmatch(self.context) is None:
            self.context = "pdf.rendering"
        self.safe_message = _SAFE_ERROR_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"PdfRenderError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_pdf_rendering(code: PdfRenderErrorCode, context: str) -> Never:
    """Raise one closed, sanitized page-rendering failure."""

    raise PdfRenderError(code=code, context=context)


@dataclass(frozen=True, slots=True)
class PdfPageRenderRequest:
    """One zero-based page request fitted within a bounded pixel rectangle."""

    page_index: int
    maximum_width_pixels: int
    maximum_height_pixels: int
    page_box: PdfPageBox = PdfPageBox.CROP
    additional_rotation: PdfRenderRotation = PdfRenderRotation.DEGREES_0
    request_version: int = 1

    def __post_init__(self) -> None:
        if (
            type(self.page_index) is not int
            or not 0 <= self.page_index < MAX_PDF_RENDER_PAGES
            or type(self.maximum_width_pixels) is not int
            or not 1 <= self.maximum_width_pixels <= MAX_PDF_RENDER_DIMENSION_PIXELS
            or type(self.maximum_height_pixels) is not int
            or not 1 <= self.maximum_height_pixels <= MAX_PDF_RENDER_DIMENSION_PIXELS
            or type(self.page_box) is not PdfPageBox
            or type(self.additional_rotation) is not PdfRenderRotation
            or type(self.request_version) is not int
            or self.request_version != 1
        ):
            reject_pdf_rendering(PdfRenderErrorCode.MODEL_INVALID, "render.request")


@dataclass(frozen=True, slots=True)
class PdfPageRenderLimits:
    """Trusted operation budgets checked before native bitmap allocation."""

    maximum_pages: int
    maximum_width_pixels: int
    maximum_height_pixels: int
    maximum_pixels: int
    maximum_bitmap_bytes: int
    maximum_destination_bytes: int
    maximum_memory_bytes: int
    wall_time_ms: int

    def __post_init__(self) -> None:
        values = (
            self.maximum_pages,
            self.maximum_width_pixels,
            self.maximum_height_pixels,
            self.maximum_pixels,
            self.maximum_bitmap_bytes,
            self.maximum_destination_bytes,
            self.maximum_memory_bytes,
            self.wall_time_ms,
        )
        if any(type(value) is not int or value < 1 for value in values):
            reject_pdf_rendering(PdfRenderErrorCode.MODEL_INVALID, "render.limits")
        if (
            self.maximum_pages > MAX_PDF_RENDER_PAGES
            or self.maximum_width_pixels > MAX_PDF_RENDER_DIMENSION_PIXELS
            or self.maximum_height_pixels > MAX_PDF_RENDER_DIMENSION_PIXELS
            or self.maximum_pixels > MAX_PDF_RENDER_PIXELS
            or self.maximum_bitmap_bytes > MAX_PDF_RENDER_BYTES
            or self.maximum_destination_bytes > MAX_PDF_RENDER_BYTES
            or self.maximum_memory_bytes > MAX_PDF_RENDER_BYTES
            or self.wall_time_ms > MAX_PDF_RENDER_WALL_TIME_MS
        ):
            reject_pdf_rendering(PdfRenderErrorCode.MODEL_INVALID, "render.limits")


def validate_pdf_render_request(
    request: PdfPageRenderRequest,
    limits: PdfPageRenderLimits,
) -> None:
    """Reject a request outside trusted page and fit-box bounds before opening a PDF."""

    if type(request) is not PdfPageRenderRequest or type(limits) is not PdfPageRenderLimits:
        reject_pdf_rendering(PdfRenderErrorCode.MODEL_INVALID, "render.request_limits")
    if request.page_index >= limits.maximum_pages:
        reject_pdf_rendering(PdfRenderErrorCode.LIMIT_EXCEEDED, "render.pages")
    if (
        request.maximum_width_pixels > limits.maximum_width_pixels
        or request.maximum_height_pixels > limits.maximum_height_pixels
    ):
        reject_pdf_rendering(PdfRenderErrorCode.LIMIT_EXCEEDED, "render.dimensions")


@dataclass(frozen=True, slots=True)
class PdfRasterLayout:
    """Checked packed-raster allocation established before entering native code."""

    width_pixels: int
    height_pixels: int
    row_stride_bytes: int
    pixel_count: int
    byte_count: int

    def __post_init__(self) -> None:
        if (
            type(self.width_pixels) is not int
            or type(self.height_pixels) is not int
            or type(self.row_stride_bytes) is not int
            or type(self.pixel_count) is not int
            or type(self.byte_count) is not int
            or not 1 <= self.width_pixels <= MAX_PDF_RENDER_DIMENSION_PIXELS
            or not 1 <= self.height_pixels <= MAX_PDF_RENDER_DIMENSION_PIXELS
            or self.row_stride_bytes != self.width_pixels * PDF_RENDER_RGBA8_BYTES_PER_PIXEL
            or self.pixel_count != self.width_pixels * self.height_pixels
            or not 1 <= self.pixel_count <= MAX_PDF_RENDER_PIXELS
            or self.byte_count != self.row_stride_bytes * self.height_pixels
            or not 1 <= self.byte_count <= MAX_PDF_RENDER_BYTES
        ):
            reject_pdf_rendering(PdfRenderErrorCode.MODEL_INVALID, "render.layout")


def checked_pdf_raster_layout(
    width_pixels: int,
    height_pixels: int,
    limits: PdfPageRenderLimits,
) -> PdfRasterLayout:
    """Validate a packed RGBA8 allocation against every relevant operation budget."""

    if type(limits) is not PdfPageRenderLimits:
        reject_pdf_rendering(PdfRenderErrorCode.MODEL_INVALID, "render.layout_limits")
    if (
        type(width_pixels) is not int
        or type(height_pixels) is not int
        or not 1 <= width_pixels <= MAX_PDF_RENDER_DIMENSION_PIXELS
        or not 1 <= height_pixels <= MAX_PDF_RENDER_DIMENSION_PIXELS
        or width_pixels > limits.maximum_width_pixels
        or height_pixels > limits.maximum_height_pixels
    ):
        reject_pdf_rendering(PdfRenderErrorCode.LIMIT_EXCEEDED, "render.dimensions")
    row_stride = width_pixels * PDF_RENDER_RGBA8_BYTES_PER_PIXEL
    pixel_count = width_pixels * height_pixels
    byte_count = row_stride * height_pixels
    if pixel_count > limits.maximum_pixels:
        reject_pdf_rendering(PdfRenderErrorCode.LIMIT_EXCEEDED, "render.pixels")
    if byte_count > limits.maximum_bitmap_bytes:
        reject_pdf_rendering(PdfRenderErrorCode.LIMIT_EXCEEDED, "render.bitmap")
    if byte_count > limits.maximum_destination_bytes:
        reject_pdf_rendering(PdfRenderErrorCode.LIMIT_EXCEEDED, "render.destination")
    if byte_count > limits.maximum_memory_bytes:
        reject_pdf_rendering(PdfRenderErrorCode.LIMIT_EXCEEDED, "render.memory")
    return PdfRasterLayout(
        width_pixels=width_pixels,
        height_pixels=height_pixels,
        row_stride_bytes=row_stride,
        pixel_count=pixel_count,
        byte_count=byte_count,
    )


@dataclass(frozen=True, slots=True)
class PdfPageRenderResult:
    """Renderer-neutral metadata for raw pixels written to a caller-owned sink."""

    page_index: int
    width_pixels: int
    height_pixels: int
    row_stride_bytes: int
    bytes_written: int
    pixel_sha256: str = field(repr=False)
    page_box: PdfPageBox
    intrinsic_rotation: PdfRenderRotation
    additional_rotation: PdfRenderRotation
    pixel_format: PdfRasterFormat = PdfRasterFormat.RGBA8
    background: PdfRasterBackground = PdfRasterBackground.OPAQUE_WHITE
    annotations: PdfAnnotationMode = PdfAnnotationMode.STORED_APPEARANCES
    result_version: int = 1

    def __post_init__(self) -> None:
        if (
            type(self.page_index) is not int
            or not 0 <= self.page_index < MAX_PDF_RENDER_PAGES
            or type(self.width_pixels) is not int
            or type(self.height_pixels) is not int
            or type(self.row_stride_bytes) is not int
            or type(self.bytes_written) is not int
            or type(self.pixel_sha256) is not str
            or _SHA256.fullmatch(self.pixel_sha256) is None
            or type(self.page_box) is not PdfPageBox
            or type(self.intrinsic_rotation) is not PdfRenderRotation
            or type(self.additional_rotation) is not PdfRenderRotation
            or type(self.pixel_format) is not PdfRasterFormat
            or self.pixel_format is not PdfRasterFormat.RGBA8
            or type(self.background) is not PdfRasterBackground
            or self.background is not PdfRasterBackground.OPAQUE_WHITE
            or type(self.annotations) is not PdfAnnotationMode
            or self.annotations is not PdfAnnotationMode.STORED_APPEARANCES
            or type(self.result_version) is not int
            or self.result_version != 1
        ):
            reject_pdf_rendering(PdfRenderErrorCode.RESULT_INVALID, "render.result")
        try:
            layout = PdfRasterLayout(
                self.width_pixels,
                self.height_pixels,
                self.row_stride_bytes,
                self.width_pixels * self.height_pixels,
                self.bytes_written,
            )
        except PdfRenderError:
            reject_pdf_rendering(PdfRenderErrorCode.RESULT_INVALID, "render.result_layout")
        if layout.byte_count != self.bytes_written:
            reject_pdf_rendering(PdfRenderErrorCode.RESULT_INVALID, "render.result_bytes")

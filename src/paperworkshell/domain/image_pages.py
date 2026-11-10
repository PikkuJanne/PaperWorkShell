"""Pure, decoder-neutral contracts for bounded JPEG/PNG page normalization."""

from __future__ import annotations

import hashlib
import hmac
import re
import struct
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Final, Never
from uuid import UUID

IMAGE_PAGE_METADATA_VERSION: Final = 1
IMAGE_NORMALIZATION_REQUEST_VERSION: Final = 1
IMAGE_NORMALIZATION_LIMITS_VERSION: Final = 1
IMAGE_NORMALIZATION_RESULT_VERSION: Final = 1
IMAGE_PAGE_ARTIFACT_VERSION: Final = 1
IMAGE_PAGE_PREVIEW_REQUEST_VERSION: Final = 1
IMAGE_PAGE_PREVIEW_SNAPSHOT_VERSION: Final = 1
IMAGE_PAGE_PREVIEW_RESULT_VERSION: Final = 1

MAX_IMAGE_SOURCE_BYTES: Final = 536_870_912
MAX_IMAGE_DIMENSION_PIXELS: Final = 32_768
MAX_IMAGE_SOURCE_PIXELS: Final = 268_435_456
MAX_IMAGE_DECOMPRESSED_BYTES: Final = 1_073_741_824
MAX_IMAGE_METADATA_RECORDS: Final = 4_096
MAX_IMAGE_METADATA_BYTES: Final = 16_777_216
MAX_IMAGE_OUTPUT_PIXELS: Final = 268_435_456
MAX_IMAGE_OUTPUT_BYTES: Final = 1_073_741_824
MAX_IMAGE_MEMORY_BYTES: Final = 1_073_741_824
MAX_IMAGE_WALL_TIME_MS: Final = 3_600_000
IMAGE_RGBA8_BYTES_PER_PIXEL: Final = 4
ORGANIZER_PREVIEW_WIDTH_PIXELS: Final = 240
ORGANIZER_PREVIEW_HEIGHT_PIXELS: Final = 320

_CONTEXT_TOKEN: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")
_ARTIFACT_MAGIC: Final = b"PWS-IMG1"
_ARTIFACT_PREFIX_STRUCT: Final = struct.Struct(">8sHHI16s16s")
_ARTIFACT_RESULT_STRUCT: Final = struct.Struct(">BBBBHBBIIIIIIIHHIIQQHHI32s")
IMAGE_PAGE_ARTIFACT_HEADER_BYTES: Final = (
    _ARTIFACT_PREFIX_STRUCT.size + _ARTIFACT_RESULT_STRUCT.size
)
MAX_IMAGE_PAGE_ARTIFACT_BYTES: Final = IMAGE_PAGE_ARTIFACT_HEADER_BYTES + MAX_IMAGE_OUTPUT_BYTES

_FORMAT_CODES: Final = {"jpeg": 1, "png": 2}
_COLOR_CODES: Final = {
    "bilevel": 1,
    "grayscale": 2,
    "grayscale_alpha": 3,
    "palette": 4,
    "palette_alpha": 5,
    "rgb": 6,
    "rgba": 7,
    "cmyk": 8,
    "grayscale_transparency": 9,
    "rgb_transparency": 10,
}
_ALPHA_POLICY_CODE: Final = 1
_PIXEL_FORMAT_CODE: Final = 1
_BACKGROUND_CODE: Final = 1
_FLAG_SOURCE_ALPHA: Final = 1 << 0
_FLAG_EMBEDDED_COLOR_PROFILE: Final = 1 << 1
_FLAG_EXIF: Final = 1 << 2
_KNOWN_FLAGS: Final = _FLAG_SOURCE_ALPHA | _FLAG_EMBEDDED_COLOR_PROFILE | _FLAG_EXIF

# Normalization is deliberately planned against the decoder's large-buffer lifetime,
# not only the final raster.  These are algorithm overheads rather than policy
# budgets: the caller-owned ImageNormalizationLimits remains authoritative.
_IMAGE_NORMALIZATION_SCAN_SCRATCH_BYTES: Final = 64 * 1024
_IMAGE_METADATA_RECORD_OVERHEAD_BYTES: Final = 512
_IMAGE_NORMALIZATION_HEADROOM_DIVISOR: Final = 4


class ImageSourceFormat(StrEnum):
    """Closed source encodings admitted by PWS-D007."""

    JPEG = "jpeg"
    PNG = "png"


class ImageSourceColorModel(StrEnum):
    """Closed, value-free source color classifications."""

    BILEVEL = "bilevel"
    GRAYSCALE = "grayscale"
    GRAYSCALE_TRANSPARENCY = "grayscale_transparency"
    GRAYSCALE_ALPHA = "grayscale_alpha"
    PALETTE = "palette"
    PALETTE_ALPHA = "palette_alpha"
    RGB = "rgb"
    RGB_TRANSPARENCY = "rgb_transparency"
    RGBA = "rgba"
    CMYK = "cmyk"


class ImageExifOrientation(IntEnum):
    """The eight TIFF/EXIF orientation intents, preserved non-destructively."""

    NORMAL = 1
    MIRROR_HORIZONTAL = 2
    ROTATE_180 = 3
    MIRROR_VERTICAL = 4
    TRANSPOSE = 5
    ROTATE_90_CLOCKWISE = 6
    TRANSVERSE = 7
    ROTATE_270_CLOCKWISE = 8

    @property
    def swaps_axes(self) -> bool:
        """Return whether applying this orientation exchanges width and height."""

        return self in {
            ImageExifOrientation.TRANSPOSE,
            ImageExifOrientation.ROTATE_90_CLOCKWISE,
            ImageExifOrientation.TRANSVERSE,
            ImageExifOrientation.ROTATE_270_CLOCKWISE,
        }

    @property
    def is_mirrored(self) -> bool:
        """Return whether this intent includes a reflection or transpose."""

        return self in {
            ImageExifOrientation.MIRROR_HORIZONTAL,
            ImageExifOrientation.MIRROR_VERTICAL,
            ImageExifOrientation.TRANSPOSE,
            ImageExifOrientation.TRANSVERSE,
        }


class ImageQuarterTurn(IntEnum):
    """Caller-owned clockwise rotation applied after EXIF orientation."""

    DEGREES_0 = 0
    DEGREES_90 = 90
    DEGREES_180 = 180
    DEGREES_270 = 270

    @property
    def swaps_axes(self) -> bool:
        return self in {ImageQuarterTurn.DEGREES_90, ImageQuarterTurn.DEGREES_270}


class ImageAlphaNormalizationPolicy(StrEnum):
    """Closed policies for deterministic RGBA8 normalization."""

    COMPOSITE_ON_OPAQUE_WHITE = "composite_on_opaque_white"
    PRESERVE_STRAIGHT_ALPHA = "preserve_straight_alpha"


class ImagePixelFormat(StrEnum):
    """Closed cacheable normalized-pixel formats."""

    RGBA8 = "rgba8"


class ImageRasterBackground(StrEnum):
    """Closed normalized raster background."""

    OPAQUE_WHITE = "opaque_white"
    PRESERVED_ALPHA = "preserved_alpha"


class ImagePagePreviewProgressPhase(StrEnum):
    """Closed content-free phases emitted while producing one image preview."""

    LOOKUP = "lookup"
    GENERATING = "generating"
    VALIDATING = "validating"
    COMPLETE = "complete"


class ImagePageErrorCode(StrEnum):
    """Closed registry of sanitized image-page failures."""

    MODEL_INVALID = "IMAGE_PAGE_MODEL_INVALID"
    SOURCE_INVALID = "IMAGE_PAGE_SOURCE_INVALID"
    FORMAT_MISMATCH = "IMAGE_PAGE_FORMAT_MISMATCH"
    METADATA_INVALID = "IMAGE_PAGE_METADATA_INVALID"
    GEOMETRY_INVALID = "IMAGE_PAGE_GEOMETRY_INVALID"
    LIMIT_EXCEEDED = "IMAGE_PAGE_LIMIT_EXCEEDED"
    PREVIEW_STORAGE_CAPACITY = "IMAGE_PAGE_PREVIEW_STORAGE_CAPACITY"
    MALFORMED = "IMAGE_PAGE_MALFORMED"
    UNSUPPORTED = "IMAGE_PAGE_UNSUPPORTED"
    CANCELLED = "IMAGE_PAGE_CANCELLED"
    TIMED_OUT = "IMAGE_PAGE_TIMED_OUT"
    DEPENDENCY_UNAVAILABLE = "IMAGE_PAGE_DEPENDENCY_UNAVAILABLE"
    DEPENDENCY_FAILED = "IMAGE_PAGE_DEPENDENCY_FAILED"
    DESTINATION_FAILED = "IMAGE_PAGE_DESTINATION_FAILED"
    RESULT_INVALID = "IMAGE_PAGE_RESULT_INVALID"
    CACHE_INVALID = "IMAGE_PAGE_CACHE_INVALID"
    INTERNAL_ERROR = "IMAGE_PAGE_INTERNAL_ERROR"


_SAFE_ERROR_MESSAGES: Final[dict[ImagePageErrorCode, str]] = {
    ImagePageErrorCode.MODEL_INVALID: "PaperWorkShell received invalid image-operation data.",
    ImagePageErrorCode.SOURCE_INVALID: "The selected image source is not available.",
    ImagePageErrorCode.FORMAT_MISMATCH: "The image content does not match the prepared source.",
    ImagePageErrorCode.METADATA_INVALID: "The image contains invalid metadata.",
    ImagePageErrorCode.GEOMETRY_INVALID: "The image has invalid page geometry.",
    ImagePageErrorCode.LIMIT_EXCEEDED: "The image exceeds a configured safety limit.",
    ImagePageErrorCode.PREVIEW_STORAGE_CAPACITY: "There is not enough space to prepare the image.",
    ImagePageErrorCode.MALFORMED: "The image is malformed or incomplete.",
    ImagePageErrorCode.UNSUPPORTED: "The image uses an unsupported encoding or color model.",
    ImagePageErrorCode.CANCELLED: "Image processing was cancelled before completion.",
    ImagePageErrorCode.TIMED_OUT: "Image processing exceeded its configured time limit.",
    ImagePageErrorCode.DEPENDENCY_UNAVAILABLE: (
        "A required image-processing component is not available."
    ),
    ImagePageErrorCode.DEPENDENCY_FAILED: (
        "A required image-processing component could not complete safely."
    ),
    ImagePageErrorCode.DESTINATION_FAILED: (
        "PaperWorkShell could not store the normalized image safely."
    ),
    ImagePageErrorCode.RESULT_INVALID: (
        "PaperWorkShell received an invalid image-processing result."
    ),
    ImagePageErrorCode.CACHE_INVALID: (
        "A cached image page could not be verified and must be rebuilt."
    ),
    ImagePageErrorCode.INTERNAL_ERROR: (
        "PaperWorkShell could not complete image processing safely."
    ),
}


@dataclass(slots=True, repr=False)
class ImagePageError(ValueError):
    """Stable failure containing no paths, names, source values, or pixels."""

    code: ImagePageErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not ImagePageErrorCode:
            self.code = ImagePageErrorCode.INTERNAL_ERROR
        if type(self.context) is not str or _CONTEXT_TOKEN.fullmatch(self.context) is None:
            self.context = "image.page"
        self.safe_message = _SAFE_ERROR_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"ImagePageError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_image_page(code: ImagePageErrorCode, context: str) -> Never:
    """Raise one closed, sanitized image failure without retaining a source exception."""

    error = ImagePageError(code=code, context=context)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


def _require_positive_integer(
    value: object,
    *,
    maximum: int,
    context: str,
    code: ImagePageErrorCode = ImagePageErrorCode.MODEL_INVALID,
) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        reject_image_page(code, context)
    return value


def _require_nonnegative_integer(
    value: object,
    *,
    maximum: int,
    context: str,
    code: ImagePageErrorCode,
) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        reject_image_page(code, context)
    return value


def _require_sha256(
    value: object,
    *,
    context: str,
    code: ImagePageErrorCode,
) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        reject_image_page(code, context)
    return value


def _require_uuid4(value: object, *, context: str, code: ImagePageErrorCode) -> UUID:
    if type(value) is not UUID or value.version != 4:
        reject_image_page(code, context)
    return value


@dataclass(frozen=True, slots=True)
class ImagePageMetadata:
    """Immutable, value-free facts about one decoded JPEG or PNG page."""

    source_format: ImageSourceFormat
    source_color_model: ImageSourceColorModel
    alpha_policy: ImageAlphaNormalizationPolicy
    exif_orientation: ImageExifOrientation
    encoded_width_pixels: int
    encoded_height_pixels: int
    oriented_width_pixels: int
    oriented_height_pixels: int
    component_count: int
    bits_per_component: int
    frame_count: int
    metadata_record_count: int
    metadata_byte_count: int
    has_alpha: bool
    has_embedded_color_profile: bool
    has_exif: bool
    metadata_version: int = IMAGE_PAGE_METADATA_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.source_format) is not ImageSourceFormat
            or type(self.source_color_model) is not ImageSourceColorModel
            or type(self.alpha_policy) is not ImageAlphaNormalizationPolicy
            or type(self.exif_orientation) is not ImageExifOrientation
            or type(self.has_alpha) is not bool
            or type(self.has_embedded_color_profile) is not bool
            or type(self.has_exif) is not bool
            or type(self.metadata_version) is not int
            or self.metadata_version != IMAGE_PAGE_METADATA_VERSION
        ):
            reject_image_page(ImagePageErrorCode.METADATA_INVALID, "image.metadata")
        width = _require_positive_integer(
            self.encoded_width_pixels,
            maximum=MAX_IMAGE_DIMENSION_PIXELS,
            context="image.metadata_encoded_width",
            code=ImagePageErrorCode.METADATA_INVALID,
        )
        height = _require_positive_integer(
            self.encoded_height_pixels,
            maximum=MAX_IMAGE_DIMENSION_PIXELS,
            context="image.metadata_encoded_height",
            code=ImagePageErrorCode.METADATA_INVALID,
        )
        if width * height > MAX_IMAGE_SOURCE_PIXELS:
            reject_image_page(ImagePageErrorCode.METADATA_INVALID, "image.metadata_pixels")
        expected_oriented = (height, width) if self.exif_orientation.swaps_axes else (width, height)
        if (self.oriented_width_pixels, self.oriented_height_pixels) != expected_oriented:
            reject_image_page(ImagePageErrorCode.GEOMETRY_INVALID, "image.metadata_orientation")
        expected_components = {
            ImageSourceColorModel.BILEVEL: 1,
            ImageSourceColorModel.GRAYSCALE: 1,
            ImageSourceColorModel.GRAYSCALE_TRANSPARENCY: 1,
            ImageSourceColorModel.GRAYSCALE_ALPHA: 2,
            ImageSourceColorModel.PALETTE: 1,
            ImageSourceColorModel.PALETTE_ALPHA: 1,
            ImageSourceColorModel.RGB: 3,
            ImageSourceColorModel.RGB_TRANSPARENCY: 3,
            ImageSourceColorModel.RGBA: 4,
            ImageSourceColorModel.CMYK: 4,
        }[self.source_color_model]
        alpha_models = {
            ImageSourceColorModel.GRAYSCALE_ALPHA,
            ImageSourceColorModel.GRAYSCALE_TRANSPARENCY,
            ImageSourceColorModel.PALETTE_ALPHA,
            ImageSourceColorModel.RGB_TRANSPARENCY,
            ImageSourceColorModel.RGBA,
        }
        if self.component_count != expected_components or self.has_alpha != (
            self.source_color_model in alpha_models
        ):
            reject_image_page(ImagePageErrorCode.METADATA_INVALID, "image.metadata_color")
        allowed_bits = {
            ImageSourceColorModel.BILEVEL: {1},
            ImageSourceColorModel.GRAYSCALE: {1, 2, 4, 8, 16},
            ImageSourceColorModel.GRAYSCALE_TRANSPARENCY: {1, 2, 4, 8, 16},
            ImageSourceColorModel.GRAYSCALE_ALPHA: {8, 16},
            ImageSourceColorModel.PALETTE: {1, 2, 4, 8},
            ImageSourceColorModel.PALETTE_ALPHA: {1, 2, 4, 8},
            ImageSourceColorModel.RGB: {8, 16},
            ImageSourceColorModel.RGB_TRANSPARENCY: {8, 16},
            ImageSourceColorModel.RGBA: {8, 16},
            ImageSourceColorModel.CMYK: {8},
        }[self.source_color_model]
        if self.bits_per_component not in allowed_bits:
            reject_image_page(ImagePageErrorCode.METADATA_INVALID, "image.metadata_bits")
        if self.source_format is ImageSourceFormat.JPEG:
            if (
                self.source_color_model
                not in {
                    ImageSourceColorModel.GRAYSCALE,
                    ImageSourceColorModel.RGB,
                    ImageSourceColorModel.CMYK,
                }
                or self.bits_per_component != 8
                or self.has_alpha
            ):
                reject_image_page(ImagePageErrorCode.METADATA_INVALID, "image.metadata_jpeg")
        elif self.source_color_model is ImageSourceColorModel.CMYK:
            reject_image_page(ImagePageErrorCode.METADATA_INVALID, "image.metadata_png")
        if type(self.frame_count) is not int or self.frame_count != 1:
            reject_image_page(ImagePageErrorCode.UNSUPPORTED, "image.metadata_frames")
        records = _require_nonnegative_integer(
            self.metadata_record_count,
            maximum=MAX_IMAGE_METADATA_RECORDS,
            context="image.metadata_records",
            code=ImagePageErrorCode.METADATA_INVALID,
        )
        metadata_bytes = _require_nonnegative_integer(
            self.metadata_byte_count,
            maximum=MAX_IMAGE_METADATA_BYTES,
            context="image.metadata_bytes",
            code=ImagePageErrorCode.METADATA_INVALID,
        )
        if (records == 0) != (metadata_bytes == 0):
            reject_image_page(ImagePageErrorCode.METADATA_INVALID, "image.metadata_counts")
        if not self.has_exif and self.exif_orientation is not ImageExifOrientation.NORMAL:
            reject_image_page(ImagePageErrorCode.METADATA_INVALID, "image.metadata_exif")

    @property
    def source_pixel_count(self) -> int:
        return self.encoded_width_pixels * self.encoded_height_pixels

    @property
    def estimated_decompressed_bytes(self) -> int:
        bytes_per_component = (self.bits_per_component + 7) // 8
        source_bytes = self.source_pixel_count * self.component_count * bytes_per_component
        return max(source_bytes, self.source_pixel_count * IMAGE_RGBA8_BYTES_PER_PIXEL)


@dataclass(frozen=True, slots=True, repr=False)
class ImageNormalizationRequest:
    """Expected immutable-source identity and bounded normalization intent."""

    expected_source_format: ImageSourceFormat
    expected_source_bytes: int
    expected_source_sha256: str = field(repr=False)
    maximum_width_pixels: int
    maximum_height_pixels: int
    additional_rotation: ImageQuarterTurn = ImageQuarterTurn.DEGREES_0
    alpha_policy: ImageAlphaNormalizationPolicy = (
        ImageAlphaNormalizationPolicy.COMPOSITE_ON_OPAQUE_WHITE
    )
    request_version: int = IMAGE_NORMALIZATION_REQUEST_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.expected_source_format) is not ImageSourceFormat
            or type(self.additional_rotation) is not ImageQuarterTurn
            or type(self.alpha_policy) is not ImageAlphaNormalizationPolicy
            or type(self.request_version) is not int
            or self.request_version != IMAGE_NORMALIZATION_REQUEST_VERSION
        ):
            reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.request")
        _require_positive_integer(
            self.expected_source_bytes,
            maximum=MAX_IMAGE_SOURCE_BYTES,
            context="image.request_source_bytes",
        )
        _require_sha256(
            self.expected_source_sha256,
            context="image.request_source_digest",
            code=ImagePageErrorCode.MODEL_INVALID,
        )
        _require_positive_integer(
            self.maximum_width_pixels,
            maximum=MAX_IMAGE_DIMENSION_PIXELS,
            context="image.request_width",
        )
        _require_positive_integer(
            self.maximum_height_pixels,
            maximum=MAX_IMAGE_DIMENSION_PIXELS,
            context="image.request_height",
        )


@dataclass(frozen=True, slots=True)
class ImageNormalizationLimits:
    """Trusted hard operation budgets checked before decoder allocation."""

    maximum_source_bytes: int
    maximum_encoded_width_pixels: int
    maximum_encoded_height_pixels: int
    maximum_source_pixels: int
    maximum_decompressed_bytes: int
    maximum_metadata_records: int
    maximum_metadata_bytes: int
    maximum_output_width_pixels: int
    maximum_output_height_pixels: int
    maximum_output_pixels: int
    maximum_output_bytes: int
    maximum_memory_bytes: int
    wall_time_ms: int
    limits_version: int = IMAGE_NORMALIZATION_LIMITS_VERSION

    def __post_init__(self) -> None:
        ceilings = (
            (self.maximum_source_bytes, MAX_IMAGE_SOURCE_BYTES),
            (self.maximum_encoded_width_pixels, MAX_IMAGE_DIMENSION_PIXELS),
            (self.maximum_encoded_height_pixels, MAX_IMAGE_DIMENSION_PIXELS),
            (self.maximum_source_pixels, MAX_IMAGE_SOURCE_PIXELS),
            (self.maximum_decompressed_bytes, MAX_IMAGE_DECOMPRESSED_BYTES),
            (self.maximum_metadata_records, MAX_IMAGE_METADATA_RECORDS),
            (self.maximum_metadata_bytes, MAX_IMAGE_METADATA_BYTES),
            (self.maximum_output_width_pixels, MAX_IMAGE_DIMENSION_PIXELS),
            (self.maximum_output_height_pixels, MAX_IMAGE_DIMENSION_PIXELS),
            (self.maximum_output_pixels, MAX_IMAGE_OUTPUT_PIXELS),
            (self.maximum_output_bytes, MAX_IMAGE_OUTPUT_BYTES),
            (self.maximum_memory_bytes, MAX_IMAGE_MEMORY_BYTES),
            (self.wall_time_ms, MAX_IMAGE_WALL_TIME_MS),
        )
        if any(type(value) is not int or not 1 <= value <= maximum for value, maximum in ceilings):
            reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.limits")
        if (
            type(self.limits_version) is not int
            or self.limits_version != IMAGE_NORMALIZATION_LIMITS_VERSION
        ):
            reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.limits_version")


def validate_image_normalization_request(
    request: ImageNormalizationRequest,
    limits: ImageNormalizationLimits,
) -> None:
    """Reject request facts outside trusted budgets before opening a decoder."""

    if (
        type(request) is not ImageNormalizationRequest
        or type(limits) is not ImageNormalizationLimits
    ):
        reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.request_limits")
    if request.expected_source_bytes > limits.maximum_source_bytes:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.limit_source_bytes")
    if (
        request.maximum_width_pixels > limits.maximum_output_width_pixels
        or request.maximum_height_pixels > limits.maximum_output_height_pixels
    ):
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.limit_output_dimensions")


def validate_image_page_metadata(
    metadata: ImagePageMetadata,
    limits: ImageNormalizationLimits,
) -> None:
    """Reject authoritative decoded facts outside trusted source budgets."""

    if type(metadata) is not ImagePageMetadata or type(limits) is not ImageNormalizationLimits:
        reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.metadata_limits")
    if (
        metadata.encoded_width_pixels > limits.maximum_encoded_width_pixels
        or metadata.encoded_height_pixels > limits.maximum_encoded_height_pixels
    ):
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.limit_source_dimensions")
    if metadata.source_pixel_count > limits.maximum_source_pixels:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.limit_source_pixels")
    if metadata.estimated_decompressed_bytes > limits.maximum_decompressed_bytes:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.limit_decompressed_bytes")
    if metadata.metadata_record_count > limits.maximum_metadata_records:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.limit_metadata_records")
    if metadata.metadata_byte_count > limits.maximum_metadata_bytes:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.limit_metadata_bytes")


@dataclass(frozen=True, slots=True)
class ImageRgba8Layout:
    """Checked packed opaque-RGBA8 output allocation."""

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
            or not 1 <= self.width_pixels <= MAX_IMAGE_DIMENSION_PIXELS
            or not 1 <= self.height_pixels <= MAX_IMAGE_DIMENSION_PIXELS
            or self.row_stride_bytes != self.width_pixels * IMAGE_RGBA8_BYTES_PER_PIXEL
            or self.pixel_count != self.width_pixels * self.height_pixels
            or not 1 <= self.pixel_count <= MAX_IMAGE_OUTPUT_PIXELS
            or self.byte_count != self.row_stride_bytes * self.height_pixels
            or not 1 <= self.byte_count <= MAX_IMAGE_OUTPUT_BYTES
        ):
            reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.layout")


def checked_image_rgba8_layout(
    width_pixels: int,
    height_pixels: int,
    limits: ImageNormalizationLimits,
) -> ImageRgba8Layout:
    """Validate one packed output allocation against every relevant budget."""

    if type(limits) is not ImageNormalizationLimits:
        reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.layout_limits")
    if (
        type(width_pixels) is not int
        or type(height_pixels) is not int
        or not 1 <= width_pixels <= limits.maximum_output_width_pixels
        or not 1 <= height_pixels <= limits.maximum_output_height_pixels
    ):
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.limit_output_dimensions")
    row_stride = width_pixels * IMAGE_RGBA8_BYTES_PER_PIXEL
    pixel_count = width_pixels * height_pixels
    byte_count = row_stride * height_pixels
    if pixel_count > limits.maximum_output_pixels:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.limit_output_pixels")
    if byte_count > limits.maximum_output_bytes:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.limit_output_bytes")
    if byte_count > limits.maximum_memory_bytes:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.limit_memory")
    return ImageRgba8Layout(width_pixels, height_pixels, row_stride, pixel_count, byte_count)


def plan_image_normalization_layout(
    metadata: ImagePageMetadata,
    request: ImageNormalizationRequest,
    limits: ImageNormalizationLimits,
) -> ImageRgba8Layout:
    """Plan the unique no-upscale fit after EXIF and caller rotation."""

    validate_image_normalization_request(request, limits)
    validate_image_page_metadata(metadata, limits)
    if metadata.source_format is not request.expected_source_format:
        reject_image_page(ImagePageErrorCode.FORMAT_MISMATCH, "image.source_format")
    if metadata.alpha_policy is not request.alpha_policy:
        reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.alpha_policy")
    width = metadata.oriented_width_pixels
    height = metadata.oriented_height_pixels
    if request.additional_rotation.swaps_axes:
        width, height = height, width
    maximum_width = min(request.maximum_width_pixels, limits.maximum_output_width_pixels)
    maximum_height = min(request.maximum_height_pixels, limits.maximum_output_height_pixels)
    output_width, output_height = _fit_image_dimensions(
        width,
        height,
        maximum_width,
        maximum_height,
    )
    layout = checked_image_rgba8_layout(output_width, output_height, limits)
    if _estimated_image_normalization_peak_bytes(metadata, layout) > limits.maximum_memory_bytes:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.limit_memory")
    return layout


def _estimated_image_normalization_peak_bytes(
    metadata: ImagePageMetadata,
    layout: ImageRgba8Layout,
) -> int:
    """Conservatively bound simultaneous normalization allocations.

    The linear decoder closes each superseded Pillow image before starting the
    next stage.  Its remaining large-buffer peaks are therefore: three decoded
    source-sized images during alpha compositing, hidden resize work bounded by
    that same three-source charge because output never upscales, and output image
    plus serialized bytes.  One complete caller-owned output is charged
    throughout.  Bounded scan scratch, decoded metadata/record objects, and 25
    percent allocator/native headroom are also reserved.
    """

    source_bytes = metadata.estimated_decompressed_bytes
    output_bytes = layout.byte_count
    metadata_bytes = (
        metadata.metadata_byte_count
        + metadata.metadata_record_count * _IMAGE_METADATA_RECORD_OVERHEAD_BYTES
    )
    stage_bytes = max(
        _IMAGE_NORMALIZATION_SCAN_SCRATCH_BYTES,
        source_bytes + metadata_bytes,
        3 * source_bytes,
        source_bytes + output_bytes,
        2 * output_bytes,
    )
    known_peak = output_bytes + stage_bytes
    headroom = (
        known_peak + _IMAGE_NORMALIZATION_HEADROOM_DIVISOR - 1
    ) // _IMAGE_NORMALIZATION_HEADROOM_DIVISOR
    return known_peak + headroom


def _fit_image_dimensions(
    width: int,
    height: int,
    maximum_width: int,
    maximum_height: int,
) -> tuple[int, int]:
    """Return the unique integer no-upscale fit for already validated dimensions."""

    if width > maximum_width or height > maximum_height:
        if maximum_width * height <= maximum_height * width:
            output_width = maximum_width
            output_height = max(1, (height * maximum_width + width // 2) // width)
            output_height = min(output_height, maximum_height)
        else:
            output_height = maximum_height
            output_width = max(1, (width * maximum_height + height // 2) // height)
            output_width = min(output_width, maximum_width)
    else:
        output_width, output_height = width, height
    return output_width, output_height


@dataclass(frozen=True, slots=True, repr=False)
class ImageNormalizationResult:
    """Decoder-neutral facts for one normalized packed RGBA8 raster."""

    metadata: ImagePageMetadata
    additional_rotation: ImageQuarterTurn
    width_pixels: int
    height_pixels: int
    row_stride_bytes: int
    bytes_written: int
    pixel_sha256: str = field(repr=False)
    alpha_policy: ImageAlphaNormalizationPolicy = (
        ImageAlphaNormalizationPolicy.COMPOSITE_ON_OPAQUE_WHITE
    )
    pixel_format: ImagePixelFormat = ImagePixelFormat.RGBA8
    background: ImageRasterBackground = ImageRasterBackground.OPAQUE_WHITE
    result_version: int = IMAGE_NORMALIZATION_RESULT_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.metadata) is not ImagePageMetadata
            or type(self.additional_rotation) is not ImageQuarterTurn
            or type(self.alpha_policy) is not ImageAlphaNormalizationPolicy
            or self.alpha_policy is not self.metadata.alpha_policy
            or self.pixel_format is not ImagePixelFormat.RGBA8
            or (
                self.alpha_policy is ImageAlphaNormalizationPolicy.COMPOSITE_ON_OPAQUE_WHITE
                and self.background is not ImageRasterBackground.OPAQUE_WHITE
            )
            or (
                self.alpha_policy is ImageAlphaNormalizationPolicy.PRESERVE_STRAIGHT_ALPHA
                and self.background is not ImageRasterBackground.PRESERVED_ALPHA
            )
            or type(self.result_version) is not int
            or self.result_version != IMAGE_NORMALIZATION_RESULT_VERSION
        ):
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.result")
        try:
            layout = ImageRgba8Layout(
                self.width_pixels,
                self.height_pixels,
                self.row_stride_bytes,
                self.width_pixels * self.height_pixels,
                self.bytes_written,
            )
        except ImagePageError:
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.result_layout")
        if layout.byte_count != self.bytes_written:
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.result_bytes")
        _require_sha256(
            self.pixel_sha256,
            context="image.result_pixel_digest",
            code=ImagePageErrorCode.RESULT_INVALID,
        )

    def __repr__(self) -> str:
        return (
            "ImageNormalizationResult("
            f"source_format={self.metadata.source_format.value!r}, "
            f"width_pixels={self.width_pixels!r}, height_pixels={self.height_pixels!r}, "
            f"additional_rotation={int(self.additional_rotation)!r})"
        )


def validate_image_normalization_result(
    request: ImageNormalizationRequest,
    limits: ImageNormalizationLimits,
    result: ImageNormalizationResult,
) -> None:
    """Bind one result to the expected request, metadata, and exact fit plan."""

    if type(result) is not ImageNormalizationResult:
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.result_type")
    expected_layout = plan_image_normalization_layout(result.metadata, request, limits)
    if (
        result.additional_rotation is not request.additional_rotation
        or result.alpha_policy is not request.alpha_policy
        or result.width_pixels != expected_layout.width_pixels
        or result.height_pixels != expected_layout.height_pixels
        or result.row_stride_bytes != expected_layout.row_stride_bytes
        or result.bytes_written != expected_layout.byte_count
    ):
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.result_binding")


@dataclass(frozen=True, slots=True, repr=False)
class ImagePageArtifactHeader:
    """Fixed-layout metadata bound to one operation, output, and pixel result."""

    operation_id: UUID
    output_id: UUID
    result: ImageNormalizationResult = field(repr=False)
    artifact_version: int = IMAGE_PAGE_ARTIFACT_VERSION

    def __post_init__(self) -> None:
        operation = _require_uuid4(
            self.operation_id,
            context="image.header_operation",
            code=ImagePageErrorCode.RESULT_INVALID,
        )
        output = _require_uuid4(
            self.output_id,
            context="image.header_output",
            code=ImagePageErrorCode.RESULT_INVALID,
        )
        if operation == output or type(self.result) is not ImageNormalizationResult:
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.header_binding")
        if (
            type(self.artifact_version) is not int
            or self.artifact_version != IMAGE_PAGE_ARTIFACT_VERSION
        ):
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.header_version")

    def __repr__(self) -> str:
        return (
            "ImagePageArtifactHeader("
            f"operation_id={self.operation_id!r}, output_id={self.output_id!r}, "
            f"width_pixels={self.result.width_pixels!r}, "
            f"height_pixels={self.result.height_pixels!r})"
        )


def _enum_code(value: StrEnum, codes: dict[str, int]) -> int:
    return codes[value.value]


def encode_image_page_artifact_header(header: ImagePageArtifactHeader) -> bytes:
    """Encode one exact current-version image artifact header."""

    if (
        type(header) is not ImagePageArtifactHeader
        or header.result.alpha_policy is not ImageAlphaNormalizationPolicy.COMPOSITE_ON_OPAQUE_WHITE
        or header.result.background is not ImageRasterBackground.OPAQUE_WHITE
    ):
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.header")
    metadata = header.result.metadata
    flags = 0
    if metadata.has_alpha:
        flags |= _FLAG_SOURCE_ALPHA
    if metadata.has_embedded_color_profile:
        flags |= _FLAG_EMBEDDED_COLOR_PROFILE
    if metadata.has_exif:
        flags |= _FLAG_EXIF
    prefix = _ARTIFACT_PREFIX_STRUCT.pack(
        _ARTIFACT_MAGIC,
        header.artifact_version,
        IMAGE_PAGE_ARTIFACT_HEADER_BYTES,
        0,
        header.operation_id.bytes,
        header.output_id.bytes,
    )
    result = _ARTIFACT_RESULT_STRUCT.pack(
        _enum_code(metadata.source_format, _FORMAT_CODES),
        _enum_code(metadata.source_color_model, _COLOR_CODES),
        _ALPHA_POLICY_CODE,
        int(metadata.exif_orientation),
        int(header.result.additional_rotation),
        flags,
        0,
        metadata.encoded_width_pixels,
        metadata.encoded_height_pixels,
        metadata.oriented_width_pixels,
        metadata.oriented_height_pixels,
        header.result.width_pixels,
        header.result.height_pixels,
        header.result.row_stride_bytes,
        metadata.component_count,
        metadata.bits_per_component,
        metadata.frame_count,
        metadata.metadata_record_count,
        metadata.metadata_byte_count,
        header.result.bytes_written,
        metadata.metadata_version,
        header.result.result_version,
        0,
        bytes.fromhex(header.result.pixel_sha256),
    )
    encoded = prefix + result
    if len(encoded) != IMAGE_PAGE_ARTIFACT_HEADER_BYTES:
        reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.header_size")
    return encoded


def _decode_enum[T: StrEnum](code: int, enum_type: type[T], codes: dict[str, int]) -> T:
    reverse = {value: key for key, value in codes.items()}
    return enum_type(reverse[code])


def decode_image_page_artifact_header(payload: bytes) -> ImagePageArtifactHeader:
    """Decode and validate one exact fixed-layout image artifact header."""

    if type(payload) is not bytes or len(payload) != IMAGE_PAGE_ARTIFACT_HEADER_BYTES:
        reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.header_bytes")
    prefix_end = _ARTIFACT_PREFIX_STRUCT.size
    try:
        magic, artifact_version, header_bytes, reserved, operation_bytes, output_bytes = (
            _ARTIFACT_PREFIX_STRUCT.unpack(payload[:prefix_end])
        )
        if magic != _ARTIFACT_MAGIC:
            reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.header_magic")
        if (
            artifact_version != IMAGE_PAGE_ARTIFACT_VERSION
            or header_bytes != IMAGE_PAGE_ARTIFACT_HEADER_BYTES
        ):
            reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.header_version")
        if reserved != 0:
            reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.header_reserved")
        (
            source_format,
            source_color_model,
            alpha_policy,
            exif_orientation,
            additional_rotation,
            flags,
            result_reserved,
            encoded_width,
            encoded_height,
            oriented_width,
            oriented_height,
            output_width,
            output_height,
            row_stride,
            component_count,
            bits_per_component,
            frame_count,
            metadata_records,
            metadata_bytes,
            payload_bytes,
            metadata_version,
            result_version,
            final_reserved,
            pixel_sha256,
        ) = _ARTIFACT_RESULT_STRUCT.unpack(payload[prefix_end:])
        if (
            alpha_policy != _ALPHA_POLICY_CODE
            or flags & ~_KNOWN_FLAGS
            or result_reserved != 0
            or final_reserved != 0
        ):
            reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.header_profile")
        metadata = ImagePageMetadata(
            source_format=_decode_enum(source_format, ImageSourceFormat, _FORMAT_CODES),
            source_color_model=_decode_enum(
                source_color_model, ImageSourceColorModel, _COLOR_CODES
            ),
            alpha_policy=ImageAlphaNormalizationPolicy.COMPOSITE_ON_OPAQUE_WHITE,
            exif_orientation=ImageExifOrientation(exif_orientation),
            encoded_width_pixels=encoded_width,
            encoded_height_pixels=encoded_height,
            oriented_width_pixels=oriented_width,
            oriented_height_pixels=oriented_height,
            component_count=component_count,
            bits_per_component=bits_per_component,
            frame_count=frame_count,
            metadata_record_count=metadata_records,
            metadata_byte_count=metadata_bytes,
            has_alpha=bool(flags & _FLAG_SOURCE_ALPHA),
            has_embedded_color_profile=bool(flags & _FLAG_EMBEDDED_COLOR_PROFILE),
            has_exif=bool(flags & _FLAG_EXIF),
            metadata_version=metadata_version,
        )
        result = ImageNormalizationResult(
            metadata=metadata,
            additional_rotation=ImageQuarterTurn(additional_rotation),
            width_pixels=output_width,
            height_pixels=output_height,
            row_stride_bytes=row_stride,
            bytes_written=payload_bytes,
            pixel_sha256=pixel_sha256.hex(),
            result_version=result_version,
        )
        return ImagePageArtifactHeader(
            operation_id=UUID(bytes=operation_bytes),
            output_id=UUID(bytes=output_bytes),
            result=result,
        )
    except ImagePageError as error:
        if error.code is ImagePageErrorCode.CACHE_INVALID:
            raise
        reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.header_decode")
    except Exception:
        reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.header_decode")


def _validate_opaque_rgba8_payload(
    payload: bytes,
    *,
    expected_bytes: int,
    code: ImagePageErrorCode,
    context: str,
) -> None:
    if type(payload) is not bytes or len(payload) != expected_bytes:
        reject_image_page(code, f"{context}_length")
    if any(alpha != 255 for alpha in memoryview(payload)[3::IMAGE_RGBA8_BYTES_PER_PIXEL]):
        reject_image_page(code, f"{context}_alpha")


def _validate_opaque_pixels(
    payload: bytes,
    *,
    expected_bytes: int,
    expected_sha256: str,
    code: ImagePageErrorCode,
) -> None:
    _validate_opaque_rgba8_payload(
        payload,
        expected_bytes=expected_bytes,
        code=code,
        context="image.artifact",
    )
    if not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), expected_sha256):
        reject_image_page(code, "image.artifact_digest")


@dataclass(frozen=True, slots=True, repr=False)
class ImagePageArtifact:
    """A validated fixed header plus exact opaque RGBA8 pixels."""

    header: ImagePageArtifactHeader
    payload: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.header) is not ImagePageArtifactHeader:
            reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.artifact")
        _validate_opaque_pixels(
            self.payload,
            expected_bytes=self.header.result.bytes_written,
            expected_sha256=self.header.result.pixel_sha256,
            code=ImagePageErrorCode.CACHE_INVALID,
        )

    def __repr__(self) -> str:
        return (
            "ImagePageArtifact("
            f"output_id={self.header.output_id!r}, "
            f"width_pixels={self.header.result.width_pixels!r}, "
            f"height_pixels={self.header.result.height_pixels!r})"
        )


def encode_image_page_artifact(header: ImagePageArtifactHeader, payload: bytes) -> bytes:
    """Encode a canonical complete artifact after payload integrity validation."""

    artifact = ImagePageArtifact(header, payload)
    encoded = encode_image_page_artifact_header(artifact.header) + artifact.payload
    if len(encoded) > MAX_IMAGE_PAGE_ARTIFACT_BYTES:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.artifact_size")
    return encoded


def decode_image_page_artifact(
    payload: bytes,
    *,
    expected_operation_id: UUID | None = None,
    expected_output_id: UUID | None = None,
    expected_result: ImageNormalizationResult | None = None,
) -> ImagePageArtifact:
    """Validate a complete cache artifact before exposing its bounded pixels."""

    if (
        type(payload) is not bytes
        or len(payload) < IMAGE_PAGE_ARTIFACT_HEADER_BYTES
        or len(payload) > MAX_IMAGE_PAGE_ARTIFACT_BYTES
    ):
        reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.artifact_bytes")
    header = decode_image_page_artifact_header(payload[:IMAGE_PAGE_ARTIFACT_HEADER_BYTES])
    expected_length = IMAGE_PAGE_ARTIFACT_HEADER_BYTES + header.result.bytes_written
    if len(payload) != expected_length:
        reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.artifact_length")
    if expected_operation_id is not None:
        if (
            type(expected_operation_id) is not UUID
            or expected_operation_id.version != 4
            or header.operation_id != expected_operation_id
        ):
            reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.artifact_operation")
    if expected_output_id is not None:
        if (
            type(expected_output_id) is not UUID
            or expected_output_id.version != 4
            or header.output_id != expected_output_id
        ):
            reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.artifact_output")
    if expected_result is not None:
        if (
            type(expected_result) is not ImageNormalizationResult
            or header.result != expected_result
        ):
            reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.artifact_result")
    return ImagePageArtifact(
        header=header,
        payload=payload[IMAGE_PAGE_ARTIFACT_HEADER_BYTES:],
    )


@dataclass(frozen=True, slots=True)
class ImagePagePreviewRequest:
    """One path-free organizer request for a bounded normalized image page."""

    case_id: UUID
    page_id: UUID
    maximum_width_pixels: int
    maximum_height_pixels: int
    additional_rotation: ImageQuarterTurn = ImageQuarterTurn.DEGREES_0
    request_version: int = IMAGE_PAGE_PREVIEW_REQUEST_VERSION

    def __post_init__(self) -> None:
        case_id = _require_uuid4(
            self.case_id,
            context="image.preview_request_case",
            code=ImagePageErrorCode.MODEL_INVALID,
        )
        page_id = _require_uuid4(
            self.page_id,
            context="image.preview_request_page",
            code=ImagePageErrorCode.MODEL_INVALID,
        )
        if case_id == page_id:
            reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.preview_request_ids")
        _require_positive_integer(
            self.maximum_width_pixels,
            maximum=MAX_IMAGE_DIMENSION_PIXELS,
            context="image.preview_request_width",
        )
        _require_positive_integer(
            self.maximum_height_pixels,
            maximum=MAX_IMAGE_DIMENSION_PIXELS,
            context="image.preview_request_height",
        )
        if (
            type(self.additional_rotation) is not ImageQuarterTurn
            or type(self.request_version) is not int
            or self.request_version != IMAGE_PAGE_PREVIEW_REQUEST_VERSION
        ):
            reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.preview_request")


@dataclass(frozen=True, slots=True, repr=False)
class ImagePagePreviewSnapshot:
    """UI-safe bounded opaque RGBA8 pixels from one validated image artifact."""

    case_id: UUID
    page_id: UUID
    width_pixels: int
    height_pixels: int
    row_stride_bytes: int
    pixels: bytes = field(repr=False)
    metadata: ImagePageMetadata = field(repr=False)
    additional_rotation: ImageQuarterTurn
    snapshot_version: int = IMAGE_PAGE_PREVIEW_SNAPSHOT_VERSION

    def __post_init__(self) -> None:
        case_id = _require_uuid4(
            self.case_id,
            context="image.preview_snapshot_case",
            code=ImagePageErrorCode.RESULT_INVALID,
        )
        page_id = _require_uuid4(
            self.page_id,
            context="image.preview_snapshot_page",
            code=ImagePageErrorCode.RESULT_INVALID,
        )
        if case_id == page_id:
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.preview_snapshot_ids")
        if (
            type(self.metadata) is not ImagePageMetadata
            or type(self.additional_rotation) is not ImageQuarterTurn
            or type(self.snapshot_version) is not int
            or self.snapshot_version != IMAGE_PAGE_PREVIEW_SNAPSHOT_VERSION
        ):
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.preview_snapshot")
        try:
            layout = ImageRgba8Layout(
                self.width_pixels,
                self.height_pixels,
                self.row_stride_bytes,
                self.width_pixels * self.height_pixels,
                self.row_stride_bytes * self.height_pixels,
            )
        except ImagePageError, TypeError:
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.preview_snapshot_layout")
        _validate_opaque_rgba8_payload(
            self.pixels,
            expected_bytes=layout.byte_count,
            code=ImagePageErrorCode.RESULT_INVALID,
            context="image.preview_snapshot",
        )

    def __repr__(self) -> str:
        return (
            "ImagePagePreviewSnapshot("
            f"case_id={self.case_id!r}, page_id={self.page_id!r}, "
            f"width_pixels={self.width_pixels!r}, height_pixels={self.height_pixels!r}, "
            f"additional_rotation={int(self.additional_rotation)!r})"
        )


@dataclass(frozen=True, slots=True)
class ImagePagePreviewResult:
    """Exact organizer request plus its validated in-memory image snapshot."""

    request: ImagePagePreviewRequest
    snapshot: ImagePagePreviewSnapshot
    result_version: int = IMAGE_PAGE_PREVIEW_RESULT_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.request) is not ImagePagePreviewRequest
            or type(self.snapshot) is not ImagePagePreviewSnapshot
            or type(self.result_version) is not int
            or self.result_version != IMAGE_PAGE_PREVIEW_RESULT_VERSION
        ):
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.preview_result")
        if (
            self.snapshot.case_id != self.request.case_id
            or self.snapshot.page_id != self.request.page_id
            or self.snapshot.additional_rotation is not self.request.additional_rotation
        ):
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.preview_result_binding")
        width = self.snapshot.metadata.oriented_width_pixels
        height = self.snapshot.metadata.oriented_height_pixels
        if self.request.additional_rotation.swaps_axes:
            width, height = height, width
        expected_width, expected_height = _fit_image_dimensions(
            width,
            height,
            self.request.maximum_width_pixels,
            self.request.maximum_height_pixels,
        )
        if (
            self.snapshot.width_pixels != expected_width
            or self.snapshot.height_pixels != expected_height
            or self.snapshot.row_stride_bytes != expected_width * IMAGE_RGBA8_BYTES_PER_PIXEL
        ):
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.preview_result_geometry")


def image_page_preview_result_from_artifact(
    request: ImagePagePreviewRequest,
    artifact: ImagePageArtifact,
) -> ImagePagePreviewResult:
    """Build one bounded organizer snapshot from an already authenticated artifact."""

    if type(request) is not ImagePagePreviewRequest or type(artifact) is not ImagePageArtifact:
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.preview_artifact")
    normalized = artifact.header.result
    snapshot = ImagePagePreviewSnapshot(
        case_id=request.case_id,
        page_id=request.page_id,
        width_pixels=normalized.width_pixels,
        height_pixels=normalized.height_pixels,
        row_stride_bytes=normalized.row_stride_bytes,
        pixels=artifact.payload,
        metadata=normalized.metadata,
        additional_rotation=normalized.additional_rotation,
    )
    return ImagePagePreviewResult(request=request, snapshot=snapshot)

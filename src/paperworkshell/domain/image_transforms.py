"""Pure contracts for deterministic, bounded, non-destructive image transforms."""

from __future__ import annotations

import hashlib
import hmac
import struct
from dataclasses import dataclass, field
from typing import Final
from uuid import UUID

from paperworkshell.domain.image_pages import (
    IMAGE_RGBA8_BYTES_PER_PIXEL,
    MAX_IMAGE_DIMENSION_PIXELS,
    MAX_IMAGE_MEMORY_BYTES,
    MAX_IMAGE_OUTPUT_BYTES,
    MAX_IMAGE_OUTPUT_PIXELS,
    MAX_IMAGE_SOURCE_BYTES,
    MAX_IMAGE_WALL_TIME_MS,
    ImageExifOrientation,
    ImageNormalizationLimits,
    ImagePageError,
    ImagePageErrorCode,
    ImagePixelFormat,
    ImageQuarterTurn,
    ImageRasterBackground,
    ImageRgba8Layout,
    ImageSourceFormat,
    checked_image_rgba8_layout,
    reject_image_page,
)
from paperworkshell.domain.page_coordinates import (
    NORMALIZED_PAGE_MILLIONTHS as NORMALIZED_PAGE_MILLIONTHS,
)

IMAGE_TRANSFORM_INTENT_VERSION: Final = 1
IMAGE_TRANSFORM_LIMITS_VERSION: Final = 1
IMAGE_TRANSFORM_PLAN_VERSION: Final = 1
IMAGE_TRANSFORM_CACHE_KEY_VERSION: Final = 1
IMAGE_TRANSFORM_LOOKUP_KEY_VERSION: Final = 1
IMAGE_TRANSFORM_RESULT_VERSION: Final = 1
IMAGE_TRANSFORM_ARTIFACT_VERSION: Final = 1
IMAGE_PAGE_TRANSFORM_REQUEST_VERSION: Final = 1
IMAGE_PAGE_TRANSFORM_SNAPSHOT_VERSION: Final = 1
IMAGE_PAGE_TRANSFORM_RESULT_VERSION: Final = 1
IMAGE_TRANSFORM_PROFILE_VERSION: Final = 1
IMAGE_TRANSFORM_PILLOW_VERSION: Final = "12.3.0"

MAX_DESKEW_ANGLE_MILLIDEGREES: Final = 15_000
MAX_CONTRAST_FACTOR_MILLIONTHS: Final = 4_000_000
MAX_BACKGROUND_CLEANUP_STRENGTH_MILLIONTHS: Final = 1_000_000
IDENTITY_CONTRAST_FACTOR_MILLIONTHS: Final = 1_000_000

# These constants are semantic parts of profile v1. Changing any of them requires
# a new profile and therefore a different cache identity.
IMAGE_TRANSFORM_CONTRAST_MIDPOINT: Final = 128
IMAGE_TRANSFORM_BACKGROUND_THRESHOLD: Final = 192
IMAGE_TRANSFORM_DESKEW_RESAMPLING: Final = "bicubic"
IMAGE_TRANSFORM_FIT_RESAMPLING: Final = "lanczos"
IMAGE_TRANSFORM_DESKEW_CANVAS: Final = "fixed_opaque_white"
IMAGE_TRANSFORM_ORDER: Final = (
    "exif",
    "crop",
    "quarter_turn",
    "deskew",
    "contrast",
    "background_cleanup",
    "fit",
    "serialize",
)
_ENGINE_PROFILE_BYTES: Final = (
    b"paperworkshell.image-transform.v1\0"
    b"pillow=" + IMAGE_TRANSFORM_PILLOW_VERSION.encode("ascii") + b"\0"
    b"order=exif,crop,quarter_turn,deskew,contrast,background_cleanup,fit,serialize\0"
    b"deskew=bicubic,fixed_opaque_white\0"
    b"contrast=fixed_midpoint_128,integer_half_away\0"
    b"background=per_channel_threshold_192,integer_half_up\0"
    b"fit=lanczos,no_upscale,reducing_gap_3\0"
    b"pixels=opaque_rgba8"
)
IMAGE_TRANSFORM_ENGINE_PROFILE_SHA256: Final = hashlib.sha256(_ENGINE_PROFILE_BYTES).hexdigest()
IMAGE_TRANSFORM_NORMALIZATION_PROFILE_SHA256: Final = hashlib.sha256(
    b"paperworkshell.image-transform.normalization.v1\0"
    b"pillow=" + IMAGE_TRANSFORM_PILLOW_VERSION.encode("ascii") + b"\0"
    b"source=jpeg-or-png,strict-structural-scan\0"
    b"orientation=exif-all-eight\0"
    b"alpha=opaque-white\0"
    b"pixels=rgba8,no-fit,no-user-rotation"
).hexdigest()

_SHA256_BYTES: Final = 32
_INTENT_MAGIC: Final = b"PWSITF1\0"
_CACHE_KEY_MAGIC: Final = b"PWSITK1\0"
_ARTIFACT_MAGIC: Final = b"PWSITA1\0"
_INTENT_STRUCT: Final = struct.Struct(">8sHIIIIHiIIH")
_CACHE_PREFIX_STRUCT: Final = struct.Struct(">8sHHH16s16s16sQBIIB")
_CACHE_SUFFIX_STRUCT: Final = struct.Struct(">II32s32s32s")
_LOOKUP_STRUCT: Final = struct.Struct(">8sHHH16s16s16sQBII32s32s")
_ARTIFACT_PREFIX_STRUCT: Final = struct.Struct(">8sHHI16s16s")
_ARTIFACT_RESULT_STRUCT: Final = struct.Struct(">HHIIIIIIIIIIIQ32s32s32s")

IMAGE_TRANSFORM_INTENT_BYTES: Final = _INTENT_STRUCT.size
IMAGE_TRANSFORM_CACHE_KEY_BYTES: Final = (
    _CACHE_PREFIX_STRUCT.size + IMAGE_TRANSFORM_INTENT_BYTES + _CACHE_SUFFIX_STRUCT.size
)
IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES: Final = (
    _ARTIFACT_PREFIX_STRUCT.size + IMAGE_TRANSFORM_CACHE_KEY_BYTES + _ARTIFACT_RESULT_STRUCT.size
)
MAX_IMAGE_TRANSFORM_ARTIFACT_BYTES: Final = (
    IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES + MAX_IMAGE_OUTPUT_BYTES
)
MAX_IMAGE_TRANSFORM_TEMPORARY_BYTES: Final = 2 * MAX_IMAGE_OUTPUT_BYTES

_MEMORY_HEADROOM_DIVISOR: Final = 4
_TRANSFORM_SCRATCH_BYTES: Final = 64 * 1024


def _require_uuid4(value: object, context: str) -> UUID:
    if type(value) is not UUID or value.version != 4:
        reject_image_page(ImagePageErrorCode.MODEL_INVALID, context)
    return value


def _require_sha256(value: object, context: str) -> str:
    if (
        type(value) is not str
        or len(value) != _SHA256_BYTES * 2
        or any(character not in "0123456789abcdef" for character in value)
    ):
        reject_image_page(ImagePageErrorCode.MODEL_INVALID, context)
    return value


def _require_positive(value: object, maximum: int, context: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        reject_image_page(ImagePageErrorCode.MODEL_INVALID, context)
    return value


def _round_divide(numerator: int, denominator: int) -> int:
    return (numerator + denominator // 2) // denominator


@dataclass(frozen=True, slots=True)
class ImageNormalizedCrop:
    """A half-open crop rectangle expressed in normalized page millionths."""

    x: int = 0
    y: int = 0
    width: int = NORMALIZED_PAGE_MILLIONTHS
    height: int = NORMALIZED_PAGE_MILLIONTHS

    def __post_init__(self) -> None:
        values = (self.x, self.y, self.width, self.height)
        if (
            any(type(value) is not int for value in values)
            or not 0 <= self.x < NORMALIZED_PAGE_MILLIONTHS
            or not 0 <= self.y < NORMALIZED_PAGE_MILLIONTHS
            or not 1 <= self.width <= NORMALIZED_PAGE_MILLIONTHS
            or not 1 <= self.height <= NORMALIZED_PAGE_MILLIONTHS
            or self.x + self.width > NORMALIZED_PAGE_MILLIONTHS
            or self.y + self.height > NORMALIZED_PAGE_MILLIONTHS
        ):
            reject_image_page(ImagePageErrorCode.GEOMETRY_INVALID, "image.transform_crop")

    @property
    def is_full_page(self) -> bool:
        return (
            self.x == 0
            and self.y == 0
            and self.width == NORMALIZED_PAGE_MILLIONTHS
            and self.height == NORMALIZED_PAGE_MILLIONTHS
        )


@dataclass(frozen=True, slots=True, repr=False)
class ImageTransformIntent:
    """Canonical latest-wins transform intent, independent of a decoder or UI."""

    crop: ImageNormalizedCrop = ImageNormalizedCrop()
    additional_rotation: ImageQuarterTurn = ImageQuarterTurn.DEGREES_0
    deskew_angle_millidegrees_clockwise: int = 0
    contrast_factor_millionths: int = IDENTITY_CONTRAST_FACTOR_MILLIONTHS
    background_cleanup_strength_millionths: int = 0
    intent_version: int = IMAGE_TRANSFORM_INTENT_VERSION
    _intent_sha256: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.crop) is not ImageNormalizedCrop
            or type(self.additional_rotation) is not ImageQuarterTurn
            or type(self.deskew_angle_millidegrees_clockwise) is not int
            or not -MAX_DESKEW_ANGLE_MILLIDEGREES
            <= self.deskew_angle_millidegrees_clockwise
            <= MAX_DESKEW_ANGLE_MILLIDEGREES
            or type(self.contrast_factor_millionths) is not int
            or not 0 <= self.contrast_factor_millionths <= MAX_CONTRAST_FACTOR_MILLIONTHS
            or type(self.background_cleanup_strength_millionths) is not int
            or not 0
            <= self.background_cleanup_strength_millionths
            <= MAX_BACKGROUND_CLEANUP_STRENGTH_MILLIONTHS
            or type(self.intent_version) is not int
            or self.intent_version != IMAGE_TRANSFORM_INTENT_VERSION
        ):
            reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.transform_intent")
        object.__setattr__(
            self, "_intent_sha256", hashlib.sha256(self.canonical_bytes()).hexdigest()
        )

    def canonical_bytes(self) -> bytes:
        """Return the unique fixed-width profile-v1 intent encoding."""

        return _INTENT_STRUCT.pack(
            _INTENT_MAGIC,
            self.intent_version,
            self.crop.x,
            self.crop.y,
            self.crop.width,
            self.crop.height,
            int(self.additional_rotation),
            self.deskew_angle_millidegrees_clockwise,
            self.contrast_factor_millionths,
            self.background_cleanup_strength_millionths,
            0,
        )

    @property
    def intent_sha256(self) -> str:
        return self._intent_sha256

    def __repr__(self) -> str:
        return (
            "ImageTransformIntent("
            f"crop={self.crop!r}, additional_rotation={int(self.additional_rotation)!r}, "
            f"deskew_angle_millidegrees_clockwise="
            f"{self.deskew_angle_millidegrees_clockwise!r}, "
            f"contrast_factor_millionths={self.contrast_factor_millionths!r}, "
            "background_cleanup_strength_millionths="
            f"{self.background_cleanup_strength_millionths!r})"
        )


def decode_image_transform_intent(payload: bytes) -> ImageTransformIntent:
    """Decode exactly one current canonical intent."""

    if type(payload) is not bytes or len(payload) != IMAGE_TRANSFORM_INTENT_BYTES:
        reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.transform_intent_bytes")
    try:
        (
            magic,
            version,
            x,
            y,
            width,
            height,
            rotation,
            deskew,
            contrast,
            cleanup,
            reserved,
        ) = _INTENT_STRUCT.unpack(payload)
        if magic != _INTENT_MAGIC or reserved != 0:
            reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.transform_intent_profile")
        return ImageTransformIntent(
            crop=ImageNormalizedCrop(x, y, width, height),
            additional_rotation=ImageQuarterTurn(rotation),
            deskew_angle_millidegrees_clockwise=deskew,
            contrast_factor_millionths=contrast,
            background_cleanup_strength_millionths=cleanup,
            intent_version=version,
        )
    except ImagePageError as error:
        if error.code is ImagePageErrorCode.CACHE_INVALID:
            raise
        reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.transform_intent_decode")
    except Exception:
        reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.transform_intent_decode")


@dataclass(frozen=True, slots=True)
class ImageTransformLimits:
    """Trusted operation budgets used before every transform allocation."""

    maximum_source_width_pixels: int
    maximum_source_height_pixels: int
    maximum_source_pixels: int
    maximum_intermediate_pixels: int
    maximum_output_width_pixels: int
    maximum_output_height_pixels: int
    maximum_output_pixels: int
    maximum_output_bytes: int
    maximum_memory_bytes: int
    maximum_temporary_storage_bytes: int
    wall_time_ms: int
    limits_version: int = IMAGE_TRANSFORM_LIMITS_VERSION

    def __post_init__(self) -> None:
        ceilings = (
            (self.maximum_source_width_pixels, MAX_IMAGE_DIMENSION_PIXELS),
            (self.maximum_source_height_pixels, MAX_IMAGE_DIMENSION_PIXELS),
            (self.maximum_source_pixels, MAX_IMAGE_OUTPUT_PIXELS),
            (self.maximum_intermediate_pixels, MAX_IMAGE_OUTPUT_PIXELS),
            (self.maximum_output_width_pixels, MAX_IMAGE_DIMENSION_PIXELS),
            (self.maximum_output_height_pixels, MAX_IMAGE_DIMENSION_PIXELS),
            (self.maximum_output_pixels, MAX_IMAGE_OUTPUT_PIXELS),
            (self.maximum_output_bytes, MAX_IMAGE_OUTPUT_BYTES),
            (self.maximum_memory_bytes, MAX_IMAGE_MEMORY_BYTES),
            (
                self.maximum_temporary_storage_bytes,
                MAX_IMAGE_TRANSFORM_TEMPORARY_BYTES,
            ),
            (self.wall_time_ms, MAX_IMAGE_WALL_TIME_MS),
        )
        if any(type(value) is not int or not 1 <= value <= ceiling for value, ceiling in ceilings):
            reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.transform_limits")
        if (
            type(self.limits_version) is not int
            or self.limits_version != IMAGE_TRANSFORM_LIMITS_VERSION
        ):
            reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.transform_limits_version")


@dataclass(frozen=True, slots=True)
class ImageTransformPlan:
    """Exact pixel geometry and resource reservation for the fixed transform order."""

    source_layout: ImageRgba8Layout
    intent: ImageTransformIntent
    crop_left_pixels: int
    crop_top_pixels: int
    crop_right_pixels: int
    crop_bottom_pixels: int
    rotated_width_pixels: int
    rotated_height_pixels: int
    output_layout: ImageRgba8Layout
    estimated_peak_memory_bytes: int
    required_temporary_storage_bytes: int
    plan_version: int = IMAGE_TRANSFORM_PLAN_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.source_layout) is not ImageRgba8Layout
            or type(self.intent) is not ImageTransformIntent
            or type(self.output_layout) is not ImageRgba8Layout
            or type(self.plan_version) is not int
            or self.plan_version != IMAGE_TRANSFORM_PLAN_VERSION
        ):
            reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.transform_plan")
        left, top, right, bottom = (
            self.crop_left_pixels,
            self.crop_top_pixels,
            self.crop_right_pixels,
            self.crop_bottom_pixels,
        )
        if (
            any(type(value) is not int for value in (left, top, right, bottom))
            or not 0 <= left < right <= self.source_layout.width_pixels
            or not 0 <= top < bottom <= self.source_layout.height_pixels
        ):
            reject_image_page(ImagePageErrorCode.GEOMETRY_INVALID, "image.transform_crop_pixels")
        crop_width, crop_height = right - left, bottom - top
        expected_rotated = (
            (crop_height, crop_width)
            if self.intent.additional_rotation.swaps_axes
            else (crop_width, crop_height)
        )
        if (
            (self.rotated_width_pixels, self.rotated_height_pixels) != expected_rotated
            or type(self.estimated_peak_memory_bytes) is not int
            or type(self.required_temporary_storage_bytes) is not int
            or self.estimated_peak_memory_bytes < self.output_layout.byte_count
            or self.required_temporary_storage_bytes
            != self.source_layout.byte_count + self.output_layout.byte_count
        ):
            reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.transform_plan_geometry")

    @property
    def crop_box_pixels(self) -> tuple[int, int, int, int]:
        return (
            self.crop_left_pixels,
            self.crop_top_pixels,
            self.crop_right_pixels,
            self.crop_bottom_pixels,
        )


def plan_image_transform(
    source_layout: ImageRgba8Layout,
    intent: ImageTransformIntent,
    maximum_width_pixels: int,
    maximum_height_pixels: int,
    limits: ImageTransformLimits,
) -> ImageTransformPlan:
    """Plan crop, quarter-turn, fixed-canvas deskew, cleanup, fit, and serialization."""

    if (
        type(source_layout) is not ImageRgba8Layout
        or type(intent) is not ImageTransformIntent
        or type(limits) is not ImageTransformLimits
    ):
        reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.transform_request")
    if (
        source_layout.width_pixels > limits.maximum_source_width_pixels
        or source_layout.height_pixels > limits.maximum_source_height_pixels
        or source_layout.pixel_count > limits.maximum_source_pixels
    ):
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.transform_source_limit")
    if (
        type(maximum_width_pixels) is not int
        or type(maximum_height_pixels) is not int
        or not 1 <= maximum_width_pixels <= limits.maximum_output_width_pixels
        or not 1 <= maximum_height_pixels <= limits.maximum_output_height_pixels
    ):
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.transform_output_dimensions")

    width, height = source_layout.width_pixels, source_layout.height_pixels
    crop = intent.crop
    left = crop.x * width // NORMALIZED_PAGE_MILLIONTHS
    top = crop.y * height // NORMALIZED_PAGE_MILLIONTHS
    right = (
        (crop.x + crop.width) * width + NORMALIZED_PAGE_MILLIONTHS - 1
    ) // NORMALIZED_PAGE_MILLIONTHS
    bottom = (
        (crop.y + crop.height) * height + NORMALIZED_PAGE_MILLIONTHS - 1
    ) // NORMALIZED_PAGE_MILLIONTHS
    left, top = min(left, width - 1), min(top, height - 1)
    right, bottom = min(max(right, left + 1), width), min(max(bottom, top + 1), height)
    cropped_width, cropped_height = right - left, bottom - top
    rotated_width, rotated_height = (
        (cropped_height, cropped_width)
        if intent.additional_rotation.swaps_axes
        else (cropped_width, cropped_height)
    )
    intermediate_pixels = rotated_width * rotated_height
    if intermediate_pixels > limits.maximum_intermediate_pixels:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.transform_intermediate")
    output_width, output_height = _fit_dimensions(
        rotated_width,
        rotated_height,
        maximum_width_pixels,
        maximum_height_pixels,
    )
    output_layout = checked_image_rgba8_layout(
        output_width,
        output_height,
        _as_normalization_limits(limits),
    )
    source_bytes = source_layout.byte_count
    intermediate_bytes = intermediate_pixels * IMAGE_RGBA8_BYTES_PER_PIXEL
    output_bytes = output_layout.byte_count
    stage_peak = max(
        _TRANSFORM_SCRATCH_BYTES,
        2 * source_bytes,
        source_bytes + intermediate_bytes,
        2 * intermediate_bytes,
        intermediate_bytes + output_bytes,
        2 * output_bytes,
    )
    known_peak = output_bytes + stage_peak
    estimated_peak = (
        known_peak + (known_peak + _MEMORY_HEADROOM_DIVISOR - 1) // _MEMORY_HEADROOM_DIVISOR
    )
    required_temp = source_bytes + output_bytes
    if estimated_peak > limits.maximum_memory_bytes:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.transform_memory")
    if required_temp > limits.maximum_temporary_storage_bytes:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.transform_temp")
    return ImageTransformPlan(
        source_layout=source_layout,
        intent=intent,
        crop_left_pixels=left,
        crop_top_pixels=top,
        crop_right_pixels=right,
        crop_bottom_pixels=bottom,
        rotated_width_pixels=rotated_width,
        rotated_height_pixels=rotated_height,
        output_layout=output_layout,
        estimated_peak_memory_bytes=estimated_peak,
        required_temporary_storage_bytes=required_temp,
    )


def _as_normalization_limits(limits: ImageTransformLimits) -> ImageNormalizationLimits:
    return ImageNormalizationLimits(
        maximum_source_bytes=1,
        maximum_encoded_width_pixels=limits.maximum_source_width_pixels,
        maximum_encoded_height_pixels=limits.maximum_source_height_pixels,
        maximum_source_pixels=limits.maximum_source_pixels,
        maximum_decompressed_bytes=min(limits.maximum_memory_bytes, MAX_IMAGE_OUTPUT_BYTES),
        maximum_metadata_records=1,
        maximum_metadata_bytes=1,
        maximum_output_width_pixels=limits.maximum_output_width_pixels,
        maximum_output_height_pixels=limits.maximum_output_height_pixels,
        maximum_output_pixels=limits.maximum_output_pixels,
        maximum_output_bytes=limits.maximum_output_bytes,
        maximum_memory_bytes=limits.maximum_memory_bytes,
        wall_time_ms=limits.wall_time_ms,
    )


def _fit_dimensions(
    width: int, height: int, maximum_width: int, maximum_height: int
) -> tuple[int, int]:
    if width <= maximum_width and height <= maximum_height:
        return width, height
    if maximum_width * height <= maximum_height * width:
        return maximum_width, min(
            maximum_height,
            max(1, _round_divide(height * maximum_width, width)),
        )
    return (
        min(maximum_width, max(1, _round_divide(width * maximum_height, height))),
        maximum_height,
    )


@dataclass(frozen=True, slots=True, repr=False)
class ImageTransformLookupKey:
    """Case-private lookup identity available before any image decoding.

    The immutable source digest and fixed engine profile bind source geometry,
    EXIF and decoded pixels. Those independently validated facts remain in the
    native artifact's full cache key; they need not be discovered in the GUI.
    This disposable lookup namespace does not change the v1 artifact format.
    """

    case_id: UUID
    source_id: UUID
    page_id: UUID
    source_byte_size: int
    source_sha256: str = field(repr=False)
    source_format: ImageSourceFormat
    intent: ImageTransformIntent
    maximum_width_pixels: int
    maximum_height_pixels: int
    engine_profile_sha256: str = field(default=IMAGE_TRANSFORM_ENGINE_PROFILE_SHA256, repr=False)
    artifact_version: int = IMAGE_TRANSFORM_ARTIFACT_VERSION
    key_version: int = IMAGE_TRANSFORM_LOOKUP_KEY_VERSION
    _key_sha256: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        identifiers = tuple(
            _require_uuid4(value, "image.transform_lookup_ids")
            for value in (self.case_id, self.source_id, self.page_id)
        )
        if len(set(identifiers)) != 3:
            reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.transform_lookup_ids")
        _require_positive(
            self.source_byte_size, MAX_IMAGE_SOURCE_BYTES, "image.transform_lookup_source"
        )
        _require_sha256(self.source_sha256, "image.transform_lookup_source_digest")
        _require_sha256(self.engine_profile_sha256, "image.transform_lookup_engine")
        if (
            type(self.source_format) is not ImageSourceFormat
            or type(self.intent) is not ImageTransformIntent
            or self.engine_profile_sha256 != IMAGE_TRANSFORM_ENGINE_PROFILE_SHA256
            or type(self.maximum_width_pixels) is not int
            or not 1 <= self.maximum_width_pixels <= MAX_IMAGE_DIMENSION_PIXELS
            or type(self.maximum_height_pixels) is not int
            or not 1 <= self.maximum_height_pixels <= MAX_IMAGE_DIMENSION_PIXELS
            or type(self.artifact_version) is not int
            or self.artifact_version != IMAGE_TRANSFORM_ARTIFACT_VERSION
            or type(self.key_version) is not int
            or self.key_version != IMAGE_TRANSFORM_LOOKUP_KEY_VERSION
        ):
            reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.transform_lookup")
        encoded = (
            _LOOKUP_STRUCT.pack(
                b"PWSITL1\0",
                self.key_version,
                self.artifact_version,
                IMAGE_TRANSFORM_PROFILE_VERSION,
                self.case_id.bytes,
                self.source_id.bytes,
                self.page_id.bytes,
                self.source_byte_size,
                1 if self.source_format is ImageSourceFormat.JPEG else 2,
                self.maximum_width_pixels,
                self.maximum_height_pixels,
                bytes.fromhex(self.source_sha256),
                bytes.fromhex(self.engine_profile_sha256),
            )
            + self.intent.canonical_bytes()
        )
        object.__setattr__(self, "_key_sha256", hashlib.sha256(encoded).hexdigest())

    @classmethod
    def from_cache_key(cls, key: ImageTransformCacheKey) -> ImageTransformLookupKey:
        if type(key) is not ImageTransformCacheKey:
            reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.transform_lookup_full_key")
        return cls(
            case_id=key.case_id,
            source_id=key.source_id,
            page_id=key.page_id,
            source_byte_size=key.source_byte_size,
            source_sha256=key.source_sha256,
            source_format=key.source_format,
            intent=key.intent,
            maximum_width_pixels=key.maximum_width_pixels,
            maximum_height_pixels=key.maximum_height_pixels,
            engine_profile_sha256=key.engine_profile_sha256,
            artifact_version=key.artifact_version,
        )

    def matches(self, key: ImageTransformCacheKey) -> bool:
        return type(key) is ImageTransformCacheKey and hmac.compare_digest(
            self.key_sha256, type(self).from_cache_key(key).key_sha256
        )

    @property
    def key_sha256(self) -> str:
        return self._key_sha256

    @property
    def internal_name(self) -> str:
        return f"{self.page_id}.{self._key_sha256}.pwstransform"

    def __repr__(self) -> str:
        return (
            "ImageTransformLookupKey("
            f"case_id={self.case_id!r}, source_id={self.source_id!r}, "
            f"page_id={self.page_id!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class ImageTransformCacheKey:
    """Canonical disposable-cache identity for one exact source and transform."""

    case_id: UUID
    source_id: UUID
    page_id: UUID
    source_byte_size: int
    source_sha256: str = field(repr=False)
    source_format: ImageSourceFormat
    source_layout: ImageRgba8Layout
    source_pixel_sha256: str = field(repr=False)
    exif_orientation: ImageExifOrientation
    intent: ImageTransformIntent
    maximum_width_pixels: int
    maximum_height_pixels: int
    engine_profile_sha256: str = field(
        default=IMAGE_TRANSFORM_ENGINE_PROFILE_SHA256,
        repr=False,
    )
    artifact_version: int = IMAGE_TRANSFORM_ARTIFACT_VERSION
    key_version: int = IMAGE_TRANSFORM_CACHE_KEY_VERSION
    _key_sha256: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        identifiers = tuple(
            _require_uuid4(value, "image.transform_key_ids")
            for value in (self.case_id, self.source_id, self.page_id)
        )
        if len(set(identifiers)) != len(identifiers):
            reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.transform_key_ids")
        _require_positive(
            self.source_byte_size, MAX_IMAGE_SOURCE_BYTES, "image.transform_key_source"
        )
        _require_sha256(self.source_sha256, "image.transform_key_source_digest")
        _require_sha256(self.source_pixel_sha256, "image.transform_key_pixel_digest")
        _require_sha256(self.engine_profile_sha256, "image.transform_key_engine")
        if (
            type(self.source_format) is not ImageSourceFormat
            or type(self.source_layout) is not ImageRgba8Layout
            or type(self.exif_orientation) is not ImageExifOrientation
            or type(self.intent) is not ImageTransformIntent
            or self.engine_profile_sha256 != IMAGE_TRANSFORM_ENGINE_PROFILE_SHA256
            or type(self.maximum_width_pixels) is not int
            or not 1 <= self.maximum_width_pixels <= MAX_IMAGE_DIMENSION_PIXELS
            or type(self.maximum_height_pixels) is not int
            or not 1 <= self.maximum_height_pixels <= MAX_IMAGE_DIMENSION_PIXELS
            or type(self.artifact_version) is not int
            or self.artifact_version != IMAGE_TRANSFORM_ARTIFACT_VERSION
            or type(self.key_version) is not int
            or self.key_version != IMAGE_TRANSFORM_CACHE_KEY_VERSION
        ):
            reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.transform_key")
        object.__setattr__(self, "_key_sha256", hashlib.sha256(self.canonical_bytes()).hexdigest())

    def canonical_bytes(self) -> bytes:
        prefix = _CACHE_PREFIX_STRUCT.pack(
            _CACHE_KEY_MAGIC,
            self.key_version,
            self.artifact_version,
            IMAGE_TRANSFORM_PROFILE_VERSION,
            self.case_id.bytes,
            self.source_id.bytes,
            self.page_id.bytes,
            self.source_byte_size,
            1 if self.source_format is ImageSourceFormat.JPEG else 2,
            self.source_layout.width_pixels,
            self.source_layout.height_pixels,
            int(self.exif_orientation),
        )
        suffix = _CACHE_SUFFIX_STRUCT.pack(
            self.maximum_width_pixels,
            self.maximum_height_pixels,
            bytes.fromhex(self.source_sha256),
            bytes.fromhex(self.source_pixel_sha256),
            bytes.fromhex(self.engine_profile_sha256),
        )
        return prefix + self.intent.canonical_bytes() + suffix

    @property
    def key_sha256(self) -> str:
        return self._key_sha256

    @property
    def internal_name(self) -> str:
        return f"{self.page_id}.{self._key_sha256}.pwstransform"

    def __repr__(self) -> str:
        return (
            "ImageTransformCacheKey("
            f"case_id={self.case_id!r}, source_id={self.source_id!r}, "
            f"page_id={self.page_id!r})"
        )


def decode_image_transform_cache_key(payload: bytes) -> ImageTransformCacheKey:
    """Decode and validate one exact fixed-width cache key."""

    if type(payload) is not bytes or len(payload) != IMAGE_TRANSFORM_CACHE_KEY_BYTES:
        reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.transform_key_bytes")
    try:
        prefix_end = _CACHE_PREFIX_STRUCT.size
        intent_end = prefix_end + IMAGE_TRANSFORM_INTENT_BYTES
        (
            magic,
            key_version,
            artifact_version,
            profile_version,
            case_bytes,
            source_bytes,
            page_bytes,
            source_byte_size,
            source_format,
            source_width,
            source_height,
            orientation,
        ) = _CACHE_PREFIX_STRUCT.unpack(payload[:prefix_end])
        (
            maximum_width,
            maximum_height,
            source_sha,
            source_pixel_sha,
            engine_sha,
        ) = _CACHE_SUFFIX_STRUCT.unpack(payload[intent_end:])
        if magic != _CACHE_KEY_MAGIC or profile_version != IMAGE_TRANSFORM_PROFILE_VERSION:
            reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.transform_key_profile")
        source_layout = ImageRgba8Layout(
            source_width,
            source_height,
            source_width * IMAGE_RGBA8_BYTES_PER_PIXEL,
            source_width * source_height,
            source_width * source_height * IMAGE_RGBA8_BYTES_PER_PIXEL,
        )
        return ImageTransformCacheKey(
            case_id=UUID(bytes=case_bytes),
            source_id=UUID(bytes=source_bytes),
            page_id=UUID(bytes=page_bytes),
            source_byte_size=source_byte_size,
            source_sha256=source_sha.hex(),
            source_format={1: ImageSourceFormat.JPEG, 2: ImageSourceFormat.PNG}[source_format],
            source_layout=source_layout,
            source_pixel_sha256=source_pixel_sha.hex(),
            exif_orientation=ImageExifOrientation(orientation),
            intent=decode_image_transform_intent(payload[prefix_end:intent_end]),
            maximum_width_pixels=maximum_width,
            maximum_height_pixels=maximum_height,
            engine_profile_sha256=engine_sha.hex(),
            artifact_version=artifact_version,
            key_version=key_version,
        )
    except ImagePageError as error:
        if error.code is ImagePageErrorCode.CACHE_INVALID:
            raise
        reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.transform_key_decode")
    except Exception:
        reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.transform_key_decode")


@dataclass(frozen=True, slots=True, repr=False)
class ImageTransformResult:
    """Decoder-neutral facts for one transformed opaque RGBA8 page."""

    source_width_pixels: int
    source_height_pixels: int
    crop_left_pixels: int
    crop_top_pixels: int
    crop_right_pixels: int
    crop_bottom_pixels: int
    rotated_width_pixels: int
    rotated_height_pixels: int
    width_pixels: int
    height_pixels: int
    row_stride_bytes: int
    bytes_written: int
    pixel_sha256: str = field(repr=False)
    intent_sha256: str = field(repr=False)
    engine_profile_sha256: str = field(
        default=IMAGE_TRANSFORM_ENGINE_PROFILE_SHA256,
        repr=False,
    )
    pixel_format: ImagePixelFormat = ImagePixelFormat.RGBA8
    background: ImageRasterBackground = ImageRasterBackground.OPAQUE_WHITE
    result_version: int = IMAGE_TRANSFORM_RESULT_VERSION

    def __post_init__(self) -> None:
        _require_sha256(self.pixel_sha256, "image.transform_result_digest")
        _require_sha256(self.intent_sha256, "image.transform_result_intent")
        _require_sha256(self.engine_profile_sha256, "image.transform_result_engine")
        try:
            output = ImageRgba8Layout(
                self.width_pixels,
                self.height_pixels,
                self.row_stride_bytes,
                self.width_pixels * self.height_pixels,
                self.bytes_written,
            )
            source = ImageRgba8Layout(
                self.source_width_pixels,
                self.source_height_pixels,
                self.source_width_pixels * IMAGE_RGBA8_BYTES_PER_PIXEL,
                self.source_width_pixels * self.source_height_pixels,
                self.source_width_pixels * self.source_height_pixels * IMAGE_RGBA8_BYTES_PER_PIXEL,
            )
        except ImagePageError:
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_result_layout")
        if (
            output.byte_count != self.bytes_written
            or any(
                type(value) is not int
                for value in (
                    self.crop_left_pixels,
                    self.crop_top_pixels,
                    self.crop_right_pixels,
                    self.crop_bottom_pixels,
                    self.rotated_width_pixels,
                    self.rotated_height_pixels,
                )
            )
            or not 0 <= self.crop_left_pixels < self.crop_right_pixels <= source.width_pixels
            or not 0 <= self.crop_top_pixels < self.crop_bottom_pixels <= source.height_pixels
            or self.rotated_width_pixels < 1
            or self.rotated_height_pixels < 1
            or self.engine_profile_sha256 != IMAGE_TRANSFORM_ENGINE_PROFILE_SHA256
            or self.pixel_format is not ImagePixelFormat.RGBA8
            or self.background is not ImageRasterBackground.OPAQUE_WHITE
            or type(self.result_version) is not int
            or self.result_version != IMAGE_TRANSFORM_RESULT_VERSION
        ):
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_result")

    def __repr__(self) -> str:
        return (
            "ImageTransformResult("
            f"width_pixels={self.width_pixels!r}, height_pixels={self.height_pixels!r})"
        )


def validate_image_transform_result(plan: ImageTransformPlan, result: ImageTransformResult) -> None:
    """Bind a result to the exact canonical plan and transform profile."""

    if type(plan) is not ImageTransformPlan or type(result) is not ImageTransformResult:
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_result_type")
    expected = plan.output_layout
    if (
        result.source_width_pixels != plan.source_layout.width_pixels
        or result.source_height_pixels != plan.source_layout.height_pixels
        or result.crop_left_pixels != plan.crop_left_pixels
        or result.crop_top_pixels != plan.crop_top_pixels
        or result.crop_right_pixels != plan.crop_right_pixels
        or result.crop_bottom_pixels != plan.crop_bottom_pixels
        or result.rotated_width_pixels != plan.rotated_width_pixels
        or result.rotated_height_pixels != plan.rotated_height_pixels
        or result.width_pixels != expected.width_pixels
        or result.height_pixels != expected.height_pixels
        or result.row_stride_bytes != expected.row_stride_bytes
        or result.bytes_written != expected.byte_count
        or not hmac.compare_digest(result.intent_sha256, plan.intent.intent_sha256)
    ):
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_result_binding")


@dataclass(frozen=True, slots=True, repr=False)
class ImageTransformArtifactHeader:
    """Fixed metadata binding one worker operation, cache key, and result."""

    operation_id: UUID
    output_id: UUID
    cache_key: ImageTransformCacheKey = field(repr=False)
    result: ImageTransformResult = field(repr=False)
    artifact_version: int = IMAGE_TRANSFORM_ARTIFACT_VERSION

    def __post_init__(self) -> None:
        operation = _require_uuid4(self.operation_id, "image.transform_header_operation")
        output = _require_uuid4(self.output_id, "image.transform_header_output")
        if (
            operation == output
            or type(self.cache_key) is not ImageTransformCacheKey
            or type(self.result) is not ImageTransformResult
            or self.cache_key.artifact_version != self.artifact_version
            or not hmac.compare_digest(
                self.cache_key.intent.intent_sha256,
                self.result.intent_sha256,
            )
            or not hmac.compare_digest(
                self.cache_key.engine_profile_sha256,
                self.result.engine_profile_sha256,
            )
            or type(self.artifact_version) is not int
            or self.artifact_version != IMAGE_TRANSFORM_ARTIFACT_VERSION
        ):
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_header")
        plan = plan_image_transform(
            self.cache_key.source_layout,
            self.cache_key.intent,
            self.cache_key.maximum_width_pixels,
            self.cache_key.maximum_height_pixels,
            ImageTransformLimits(
                maximum_source_width_pixels=MAX_IMAGE_DIMENSION_PIXELS,
                maximum_source_height_pixels=MAX_IMAGE_DIMENSION_PIXELS,
                maximum_source_pixels=MAX_IMAGE_OUTPUT_PIXELS,
                maximum_intermediate_pixels=MAX_IMAGE_OUTPUT_PIXELS,
                maximum_output_width_pixels=MAX_IMAGE_DIMENSION_PIXELS,
                maximum_output_height_pixels=MAX_IMAGE_DIMENSION_PIXELS,
                maximum_output_pixels=MAX_IMAGE_OUTPUT_PIXELS,
                maximum_output_bytes=MAX_IMAGE_OUTPUT_BYTES,
                maximum_memory_bytes=MAX_IMAGE_MEMORY_BYTES,
                maximum_temporary_storage_bytes=MAX_IMAGE_TRANSFORM_TEMPORARY_BYTES,
                wall_time_ms=MAX_IMAGE_WALL_TIME_MS,
            ),
        )
        validate_image_transform_result(plan, self.result)


def encode_image_transform_artifact_header(header: ImageTransformArtifactHeader) -> bytes:
    """Encode one exact current transform artifact header."""

    if type(header) is not ImageTransformArtifactHeader:
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_header")
    result = header.result
    encoded = (
        _ARTIFACT_PREFIX_STRUCT.pack(
            _ARTIFACT_MAGIC,
            header.artifact_version,
            IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES,
            0,
            header.operation_id.bytes,
            header.output_id.bytes,
        )
        + header.cache_key.canonical_bytes()
        + _ARTIFACT_RESULT_STRUCT.pack(
            result.result_version,
            IMAGE_TRANSFORM_PROFILE_VERSION,
            result.source_width_pixels,
            result.source_height_pixels,
            result.crop_left_pixels,
            result.crop_top_pixels,
            result.crop_right_pixels,
            result.crop_bottom_pixels,
            result.rotated_width_pixels,
            result.rotated_height_pixels,
            result.width_pixels,
            result.height_pixels,
            result.row_stride_bytes,
            result.bytes_written,
            bytes.fromhex(result.intent_sha256),
            bytes.fromhex(result.engine_profile_sha256),
            bytes.fromhex(result.pixel_sha256),
        )
    )
    if len(encoded) != IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES:
        reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.transform_header_size")
    return encoded


def decode_image_transform_artifact_header(payload: bytes) -> ImageTransformArtifactHeader:
    """Decode one exact current transform artifact header."""

    if type(payload) is not bytes or len(payload) != IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES:
        reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.transform_header_bytes")
    try:
        prefix_end = _ARTIFACT_PREFIX_STRUCT.size
        key_end = prefix_end + IMAGE_TRANSFORM_CACHE_KEY_BYTES
        magic, version, header_bytes, reserved, operation_bytes, output_bytes = (
            _ARTIFACT_PREFIX_STRUCT.unpack(payload[:prefix_end])
        )
        if (
            magic != _ARTIFACT_MAGIC
            or version != IMAGE_TRANSFORM_ARTIFACT_VERSION
            or header_bytes != IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES
            or reserved != 0
        ):
            reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.transform_header_profile")
        values = _ARTIFACT_RESULT_STRUCT.unpack(payload[key_end:])
        (
            result_version,
            profile_version,
            source_width,
            source_height,
            crop_left,
            crop_top,
            crop_right,
            crop_bottom,
            rotated_width,
            rotated_height,
            output_width,
            output_height,
            row_stride,
            bytes_written,
            intent_sha,
            engine_sha,
            pixel_sha,
        ) = values
        if profile_version != IMAGE_TRANSFORM_PROFILE_VERSION:
            reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.transform_header_profile")
        cache_key = decode_image_transform_cache_key(payload[prefix_end:key_end])
        result = ImageTransformResult(
            source_width_pixels=source_width,
            source_height_pixels=source_height,
            crop_left_pixels=crop_left,
            crop_top_pixels=crop_top,
            crop_right_pixels=crop_right,
            crop_bottom_pixels=crop_bottom,
            rotated_width_pixels=rotated_width,
            rotated_height_pixels=rotated_height,
            width_pixels=output_width,
            height_pixels=output_height,
            row_stride_bytes=row_stride,
            bytes_written=bytes_written,
            intent_sha256=intent_sha.hex(),
            engine_profile_sha256=engine_sha.hex(),
            pixel_sha256=pixel_sha.hex(),
            result_version=result_version,
        )
        return ImageTransformArtifactHeader(
            operation_id=UUID(bytes=operation_bytes),
            output_id=UUID(bytes=output_bytes),
            cache_key=cache_key,
            result=result,
            artifact_version=version,
        )
    except ImagePageError as error:
        if error.code is ImagePageErrorCode.CACHE_INVALID:
            raise
        reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.transform_header_decode")
    except Exception:
        reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.transform_header_decode")


@dataclass(frozen=True, slots=True, repr=False)
class ImageTransformArtifact:
    header: ImageTransformArtifactHeader
    payload: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.header) is not ImageTransformArtifactHeader:
            reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.transform_artifact")
        _validate_transform_pixels(
            self.payload,
            expected_bytes=self.header.result.bytes_written,
            expected_sha256=self.header.result.pixel_sha256,
            code=ImagePageErrorCode.CACHE_INVALID,
        )


def encode_image_transform_artifact(
    header: ImageTransformArtifactHeader,
    payload: bytes,
) -> bytes:
    """Encode a complete artifact after exact opaque-pixel validation."""

    if type(header) is not ImageTransformArtifactHeader:
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_artifact")
    _validate_transform_pixels(
        payload,
        expected_bytes=header.result.bytes_written,
        expected_sha256=header.result.pixel_sha256,
        code=ImagePageErrorCode.RESULT_INVALID,
    )
    return encode_image_transform_artifact_header(header) + payload


def decode_image_transform_artifact(
    payload: bytes,
    *,
    expected_operation_id: UUID | None = None,
    expected_output_id: UUID | None = None,
    expected_cache_key: ImageTransformCacheKey | None = None,
    expected_lookup_key: ImageTransformLookupKey | None = None,
) -> ImageTransformArtifact:
    """Authenticate one complete artifact before exposing its pixels."""

    if (
        type(payload) is not bytes
        or not IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES
        < len(payload)
        <= MAX_IMAGE_TRANSFORM_ARTIFACT_BYTES
    ):
        reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.transform_artifact_bytes")
    header = decode_image_transform_artifact_header(payload[:IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES])
    if expected_operation_id is not None and header.operation_id != _require_uuid4(
        expected_operation_id, "image.transform_expected_operation"
    ):
        reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.transform_artifact_operation")
    if expected_output_id is not None and header.output_id != _require_uuid4(
        expected_output_id, "image.transform_expected_output"
    ):
        reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.transform_artifact_output")
    if expected_cache_key is not None and (
        type(expected_cache_key) is not ImageTransformCacheKey
        or not hmac.compare_digest(header.cache_key.key_sha256, expected_cache_key.key_sha256)
    ):
        reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.transform_artifact_key")
    if expected_lookup_key is not None and (
        type(expected_lookup_key) is not ImageTransformLookupKey
        or not expected_lookup_key.matches(header.cache_key)
    ):
        reject_image_page(ImagePageErrorCode.CACHE_INVALID, "image.transform_artifact_lookup")
    return ImageTransformArtifact(
        header=header,
        payload=payload[IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES:],
    )


def _validate_transform_pixels(
    payload: bytes,
    *,
    expected_bytes: int,
    expected_sha256: str,
    code: ImagePageErrorCode,
) -> None:
    if (
        type(payload) is not bytes
        or len(payload) != expected_bytes
        or len(payload) % IMAGE_RGBA8_BYTES_PER_PIXEL != 0
        or any(alpha != 255 for alpha in memoryview(payload)[3::IMAGE_RGBA8_BYTES_PER_PIXEL])
        or not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), expected_sha256)
    ):
        reject_image_page(code, "image.transform_artifact_pixels")


@dataclass(frozen=True, slots=True)
class ImagePageTransformRequest:
    """Application-facing transform request containing no source path or bytes."""

    case_id: UUID
    page_id: UUID
    intent: ImageTransformIntent
    maximum_width_pixels: int
    maximum_height_pixels: int
    request_version: int = IMAGE_PAGE_TRANSFORM_REQUEST_VERSION

    def __post_init__(self) -> None:
        case_id = _require_uuid4(self.case_id, "image.transform_request_case")
        page_id = _require_uuid4(self.page_id, "image.transform_request_page")
        if (
            case_id == page_id
            or type(self.intent) is not ImageTransformIntent
            or type(self.maximum_width_pixels) is not int
            or not 1 <= self.maximum_width_pixels <= MAX_IMAGE_DIMENSION_PIXELS
            or type(self.maximum_height_pixels) is not int
            or not 1 <= self.maximum_height_pixels <= MAX_IMAGE_DIMENSION_PIXELS
            or type(self.request_version) is not int
            or self.request_version != IMAGE_PAGE_TRANSFORM_REQUEST_VERSION
        ):
            reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.transform_request")


@dataclass(frozen=True, slots=True, repr=False)
class ImagePageTransformSnapshot:
    """Value-free application snapshot for one authenticated transformed raster."""

    case_id: UUID
    source_id: UUID
    page_id: UUID
    intent: ImageTransformIntent
    width_pixels: int
    height_pixels: int
    row_stride_bytes: int
    bytes_written: int
    pixel_sha256: str = field(repr=False)
    cache_key_sha256: str = field(repr=False)
    pixel_format: ImagePixelFormat = ImagePixelFormat.RGBA8
    background: ImageRasterBackground = ImageRasterBackground.OPAQUE_WHITE
    snapshot_version: int = IMAGE_PAGE_TRANSFORM_SNAPSHOT_VERSION

    def __post_init__(self) -> None:
        identifiers = tuple(
            _require_uuid4(value, "image.transform_snapshot_ids")
            for value in (self.case_id, self.source_id, self.page_id)
        )
        _require_sha256(self.pixel_sha256, "image.transform_snapshot_pixel")
        _require_sha256(self.cache_key_sha256, "image.transform_snapshot_key")
        try:
            layout = ImageRgba8Layout(
                self.width_pixels,
                self.height_pixels,
                self.row_stride_bytes,
                self.width_pixels * self.height_pixels,
                self.bytes_written,
            )
        except ImagePageError:
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_snapshot_layout")
        if (
            len(set(identifiers)) != len(identifiers)
            or type(self.intent) is not ImageTransformIntent
            or layout.byte_count != self.bytes_written
            or self.pixel_format is not ImagePixelFormat.RGBA8
            or self.background is not ImageRasterBackground.OPAQUE_WHITE
            or type(self.snapshot_version) is not int
            or self.snapshot_version != IMAGE_PAGE_TRANSFORM_SNAPSHOT_VERSION
        ):
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_snapshot")


@dataclass(frozen=True, slots=True, repr=False)
class ImagePageTransformResult:
    """Application request, value-free snapshot, and bounded opaque preview pixels."""

    request: ImagePageTransformRequest
    snapshot: ImagePageTransformSnapshot
    pixels: bytes = field(repr=False)
    result_version: int = IMAGE_PAGE_TRANSFORM_RESULT_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.request) is not ImagePageTransformRequest
            or type(self.snapshot) is not ImagePageTransformSnapshot
            or self.request.case_id != self.snapshot.case_id
            or self.request.page_id != self.snapshot.page_id
            or self.request.intent != self.snapshot.intent
            or self.snapshot.width_pixels > self.request.maximum_width_pixels
            or self.snapshot.height_pixels > self.request.maximum_height_pixels
            or type(self.result_version) is not int
            or self.result_version != IMAGE_PAGE_TRANSFORM_RESULT_VERSION
        ):
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_page_result")
        _validate_transform_pixels(
            self.pixels,
            expected_bytes=self.snapshot.bytes_written,
            expected_sha256=self.snapshot.pixel_sha256,
            code=ImagePageErrorCode.RESULT_INVALID,
        )


def image_page_transform_result_from_artifact(
    request: ImagePageTransformRequest,
    artifact: ImageTransformArtifact,
) -> ImagePageTransformResult:
    """Convert a validated internal artifact to the narrow application result."""

    if (
        type(request) is not ImagePageTransformRequest
        or type(artifact) is not ImageTransformArtifact
    ):
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_page_result")
    key = artifact.header.cache_key
    result = artifact.header.result
    if (
        key.case_id != request.case_id
        or key.page_id != request.page_id
        or key.intent != request.intent
        or key.maximum_width_pixels != request.maximum_width_pixels
        or key.maximum_height_pixels != request.maximum_height_pixels
    ):
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_page_binding")
    snapshot = ImagePageTransformSnapshot(
        case_id=key.case_id,
        source_id=key.source_id,
        page_id=key.page_id,
        intent=key.intent,
        width_pixels=result.width_pixels,
        height_pixels=result.height_pixels,
        row_stride_bytes=result.row_stride_bytes,
        bytes_written=result.bytes_written,
        pixel_sha256=result.pixel_sha256,
        cache_key_sha256=key.key_sha256,
    )
    return ImagePageTransformResult(request=request, snapshot=snapshot, pixels=artifact.payload)

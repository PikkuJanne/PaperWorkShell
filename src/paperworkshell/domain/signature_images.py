"""Pure contracts for private signature-image intake and straight-alpha rasters."""

from __future__ import annotations

import hashlib
import hmac
import re
import struct
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Never
from uuid import UUID

from paperworkshell.domain.image_pages import (
    MAX_IMAGE_DIMENSION_PIXELS,
    MAX_IMAGE_METADATA_BYTES,
    MAX_IMAGE_OUTPUT_BYTES,
    MAX_IMAGE_SOURCE_BYTES,
    ImageAlphaNormalizationPolicy,
    ImageExifOrientation,
    ImageNormalizationRequest,
    ImageNormalizationResult,
    ImageRasterBackground,
    ImageSourceFormat,
)

SIGNATURE_IMAGE_RASTER_ARTIFACT_VERSION: Final = 1
SIGNATURE_IMAGE_RASTER_PROFILE_VERSION: Final = 1
SIGNATURE_IMAGE_RGBA8_BYTES_PER_PIXEL: Final = 4

_CONTEXT_TOKEN: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")
_ARTIFACT_MAGIC: Final = b"PWS-SIG1"
_ARTIFACT_PREFIX: Final = struct.Struct(">8sHHI16s16s")
_ARTIFACT_RESULT: Final = struct.Struct(">BBBBQQ32sIIIIIIIQ32s")
SIGNATURE_IMAGE_RASTER_ARTIFACT_HEADER_BYTES: Final = _ARTIFACT_PREFIX.size + _ARTIFACT_RESULT.size
MAX_SIGNATURE_IMAGE_RASTER_ARTIFACT_BYTES: Final = (
    SIGNATURE_IMAGE_RASTER_ARTIFACT_HEADER_BYTES + MAX_IMAGE_OUTPUT_BYTES
)

_FORMAT_CODES: Final = {ImageSourceFormat.JPEG: 1, ImageSourceFormat.PNG: 2}
_COLOR_INTERPRETATION_SRGB_DEVICE_RGB: Final = 1
_FLAG_SOURCE_ALPHA: Final = 1 << 0
_FLAG_SOURCE_ICC: Final = 1 << 1
_FLAG_SOURCE_EXIF: Final = 1 << 2
_KNOWN_FLAGS: Final = _FLAG_SOURCE_ALPHA | _FLAG_SOURCE_ICC | _FLAG_SOURCE_EXIF


class SignatureImageErrorCode(StrEnum):
    """Closed stable failures for signature-image operations."""

    MODEL_INVALID = "SIGNATURE_IMAGE_MODEL_INVALID"
    SOURCE_PATH_INVALID = "SIGNATURE_IMAGE_SOURCE_PATH_INVALID"
    SOURCE_UNAVAILABLE = "SIGNATURE_IMAGE_SOURCE_UNAVAILABLE"
    MEDIA_UNSUPPORTED = "SIGNATURE_IMAGE_MEDIA_UNSUPPORTED"
    COLOR_PROFILE_UNSUPPORTED = "SIGNATURE_IMAGE_COLOR_PROFILE_UNSUPPORTED"
    SOURCE_CHANGED = "SIGNATURE_IMAGE_SOURCE_CHANGED"
    LIMIT_EXCEEDED = "SIGNATURE_IMAGE_LIMIT_EXCEEDED"
    MALFORMED = "SIGNATURE_IMAGE_MALFORMED"
    COPY_FAILED = "SIGNATURE_IMAGE_COPY_FAILED"
    DECODE_FAILED = "SIGNATURE_IMAGE_DECODE_FAILED"
    CANCELLED = "SIGNATURE_IMAGE_CANCELLED"
    TIMED_OUT = "SIGNATURE_IMAGE_TIMED_OUT"
    CAPACITY_EXCEEDED = "SIGNATURE_IMAGE_CAPACITY_EXCEEDED"
    CASE_CHANGED = "SIGNATURE_IMAGE_CASE_CHANGED"
    PUBLISH_FAILED = "SIGNATURE_IMAGE_PUBLISH_FAILED"
    ASSET_UNKNOWN = "SIGNATURE_IMAGE_ASSET_UNKNOWN"
    ASSET_CHANGED = "SIGNATURE_IMAGE_ASSET_CHANGED"
    ASSET_REFERENCED = "SIGNATURE_IMAGE_ASSET_REFERENCED"
    CONFIRMATION_REQUIRED = "SIGNATURE_IMAGE_CONFIRMATION_REQUIRED"
    COMMIT_FAILED = "SIGNATURE_IMAGE_COMMIT_FAILED"
    CLEANUP_FAILED = "SIGNATURE_IMAGE_CLEANUP_FAILED"
    RESULT_INVALID = "SIGNATURE_IMAGE_RESULT_INVALID"
    INTERNAL_ERROR = "SIGNATURE_IMAGE_INTERNAL_ERROR"


_SAFE_MESSAGES: Final[dict[SignatureImageErrorCode, str]] = {
    SignatureImageErrorCode.MODEL_INVALID: (
        "PaperWorkShell received invalid signature-image operation data."
    ),
    SignatureImageErrorCode.SOURCE_PATH_INVALID: "Select a local JPEG or PNG signature image.",
    SignatureImageErrorCode.SOURCE_UNAVAILABLE: "The selected signature image is not available.",
    SignatureImageErrorCode.MEDIA_UNSUPPORTED: "Select a local JPEG or PNG signature image.",
    SignatureImageErrorCode.COLOR_PROFILE_UNSUPPORTED: (
        "The signature image uses color-profile metadata that is not supported safely."
    ),
    SignatureImageErrorCode.SOURCE_CHANGED: (
        "The selected signature image changed while it was being checked."
    ),
    SignatureImageErrorCode.LIMIT_EXCEEDED: (
        "The signature image exceeds a configured safety limit."
    ),
    SignatureImageErrorCode.MALFORMED: (
        "The signature image is malformed or uses an unsupported encoding."
    ),
    SignatureImageErrorCode.COPY_FAILED: (
        "PaperWorkShell could not copy and verify the signature image in this case."
    ),
    SignatureImageErrorCode.DECODE_FAILED: (
        "PaperWorkShell could not safely prepare the signature image."
    ),
    SignatureImageErrorCode.CANCELLED: "Signature-image processing was cancelled.",
    SignatureImageErrorCode.TIMED_OUT: (
        "Signature-image processing exceeded its configured time limit."
    ),
    SignatureImageErrorCode.CAPACITY_EXCEEDED: (
        "The case does not have enough configured capacity for this signature image."
    ),
    SignatureImageErrorCode.CASE_CHANGED: (
        "The case changed before the signature image could be placed. Review and try again."
    ),
    SignatureImageErrorCode.PUBLISH_FAILED: (
        "PaperWorkShell could not store the signature image safely."
    ),
    SignatureImageErrorCode.ASSET_UNKNOWN: "The stored signature image is not available.",
    SignatureImageErrorCode.ASSET_CHANGED: ("The stored signature image could not be verified."),
    SignatureImageErrorCode.ASSET_REFERENCED: (
        "This image is still used by another placed signature image."
    ),
    SignatureImageErrorCode.CONFIRMATION_REQUIRED: (
        "Confirm that the image is a visual mark before placing it."
    ),
    SignatureImageErrorCode.COMMIT_FAILED: (
        "PaperWorkShell could not save the signature-image placement."
    ),
    SignatureImageErrorCode.CLEANUP_FAILED: (
        "The placed signature image was removed, but an unused case copy could not be removed."
    ),
    SignatureImageErrorCode.RESULT_INVALID: (
        "PaperWorkShell received an invalid signature-image processing result."
    ),
    SignatureImageErrorCode.INTERNAL_ERROR: (
        "PaperWorkShell could not complete the signature-image operation safely."
    ),
}


@dataclass(slots=True, repr=False)
class SignatureImageError(ValueError):
    """Sanitized failure that never retains paths, names, digests, or pixels."""

    code: SignatureImageErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not SignatureImageErrorCode:
            self.code = SignatureImageErrorCode.INTERNAL_ERROR
        if type(self.context) is not str or _CONTEXT_TOKEN.fullmatch(self.context) is None:
            self.context = "signature.image"
        self.safe_message = _SAFE_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"SignatureImageError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_signature_image(code: SignatureImageErrorCode, context: str) -> Never:
    """Raise one closed error without retaining an unsafe lower-layer exception."""

    error = SignatureImageError(code, context)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


class SignatureImageProgressPhase(StrEnum):
    """Content-free progress phases for one bounded asset workflow."""

    COPYING = "copying"
    VERIFYING = "verifying"
    DECODING = "decoding"
    PREVIEW_READY = "preview_ready"
    COMMITTING = "committing"
    CLEANING_UP = "cleaning_up"


class SignatureImageMediaType(StrEnum):
    """Closed encoded formats admitted for a visual signature asset."""

    JPEG = "image/jpeg"
    PNG = "image/png"


@dataclass(frozen=True, slots=True)
class SignatureImageProgress:
    operation_id: UUID
    phase: SignatureImageProgressPhase
    completed_units: int
    total_units: int

    def __post_init__(self) -> None:
        if (
            type(self.operation_id) is not UUID
            or self.operation_id.version != 4
            or type(self.phase) is not SignatureImageProgressPhase
            or type(self.completed_units) is not int
            or type(self.total_units) is not int
            or not 0 <= self.completed_units <= self.total_units
            or self.total_units < 1
        ):
            reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, "signature.progress")


@dataclass(frozen=True, slots=True, repr=False)
class PreparedSignatureImageSummary:
    """Path-free identity of one sealed but uncommitted case copy."""

    pending_id: UUID
    case_id: UUID
    asset_id: UUID
    media_type: SignatureImageMediaType
    byte_size: int
    sha256: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.pending_id) is not UUID
            or self.pending_id.version != 4
            or type(self.case_id) is not UUID
            or self.case_id.version != 4
            or type(self.asset_id) is not UUID
            or self.asset_id.version != 4
            or type(self.media_type) is not SignatureImageMediaType
            or type(self.byte_size) is not int
            or not 1 <= self.byte_size <= MAX_IMAGE_SOURCE_BYTES
            or type(self.sha256) is not str
            or _SHA256.fullmatch(self.sha256) is None
            or len({self.pending_id, self.case_id, self.asset_id}) != 3
        ):
            reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, "signature.summary")

    def __repr__(self) -> str:
        return "PreparedSignatureImageSummary(details_hidden=True)"


@dataclass(frozen=True, slots=True, repr=False)
class SignatureImagePlacementCommitResult:
    """Path-free durable result for one asset-plus-overlay commit."""

    case_id: UUID
    asset_id: UUID
    overlay_id: UUID
    manifest_revision: int

    def __post_init__(self) -> None:
        if (
            any(
                type(value) is not UUID or value.version != 4
                for value in (
                    self.case_id,
                    self.asset_id,
                    self.overlay_id,
                )
            )
            or len({self.case_id, self.asset_id, self.overlay_id}) != 3
            or type(self.manifest_revision) is not int
            or self.manifest_revision < 1
        ):
            reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, "signature.commit")

    def __repr__(self) -> str:
        return (
            "SignatureImagePlacementCommitResult("
            f"manifest_revision={self.manifest_revision!r}, details_hidden=True)"
        )


class SignatureImageCleanupStatus(StrEnum):
    """Honest outcome of post-manifest unused-asset cleanup."""

    NOT_REQUESTED = "not_requested"
    REMOVED = "removed"
    ALREADY_ABSENT = "already_absent"
    RETAINED = "retained"


@dataclass(frozen=True, slots=True)
class SignatureImageCleanupResult:
    case_id: UUID
    asset_id: UUID
    status: SignatureImageCleanupStatus

    def __post_init__(self) -> None:
        if (
            type(self.case_id) is not UUID
            or self.case_id.version != 4
            or type(self.asset_id) is not UUID
            or self.asset_id.version != 4
            or self.case_id == self.asset_id
            or type(self.status) is not SignatureImageCleanupStatus
        ):
            reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, "signature.cleanup")


@dataclass(frozen=True, slots=True, repr=False)
class SignatureImageRasterRequest:
    """Exact encoded asset identity and bounded straight-alpha output intent."""

    case_id: UUID
    asset_id: UUID
    media_type: SignatureImageMediaType
    source_byte_count: int
    source_sha256: str = field(repr=False)
    maximum_width_pixels: int
    maximum_height_pixels: int
    request_version: int = SIGNATURE_IMAGE_RASTER_PROFILE_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.case_id) is not UUID
            or self.case_id.version != 4
            or type(self.asset_id) is not UUID
            or self.asset_id.version != 4
            or self.asset_id == self.case_id
            or type(self.media_type) is not SignatureImageMediaType
            or type(self.source_byte_count) is not int
            or not 1 <= self.source_byte_count <= MAX_IMAGE_SOURCE_BYTES
            or type(self.source_sha256) is not str
            or _SHA256.fullmatch(self.source_sha256) is None
            or type(self.maximum_width_pixels) is not int
            or not 1 <= self.maximum_width_pixels <= MAX_IMAGE_DIMENSION_PIXELS
            or type(self.maximum_height_pixels) is not int
            or not 1 <= self.maximum_height_pixels <= MAX_IMAGE_DIMENSION_PIXELS
            or type(self.request_version) is not int
            or self.request_version != SIGNATURE_IMAGE_RASTER_PROFILE_VERSION
        ):
            reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, "signature.request")

    @property
    def source_format(self) -> ImageSourceFormat:
        return (
            ImageSourceFormat.JPEG
            if self.media_type is SignatureImageMediaType.JPEG
            else ImageSourceFormat.PNG
        )

    def image_request(self) -> ImageNormalizationRequest:
        """Return the shared decoder plan while retaining this separate RPC profile."""

        return ImageNormalizationRequest(
            expected_source_format=self.source_format,
            expected_source_bytes=self.source_byte_count,
            expected_source_sha256=self.source_sha256,
            maximum_width_pixels=self.maximum_width_pixels,
            maximum_height_pixels=self.maximum_height_pixels,
            alpha_policy=ImageAlphaNormalizationPolicy.PRESERVE_STRAIGHT_ALPHA,
        )

    def __repr__(self) -> str:
        return "SignatureImageRasterRequest(details_hidden=True)"


@dataclass(frozen=True, slots=True, repr=False)
class SignatureImageRasterResult:
    """Content-free identity of one metadata-free top-down straight RGBA8 raster."""

    source_format: ImageSourceFormat
    source_byte_count: int
    source_sha256: str = field(repr=False)
    exif_orientation: ImageExifOrientation
    encoded_width_pixels: int
    encoded_height_pixels: int
    oriented_width_pixels: int
    oriented_height_pixels: int
    width_pixels: int
    height_pixels: int
    row_stride_bytes: int
    pixel_byte_count: int
    pixel_sha256: str = field(repr=False)
    source_has_alpha: bool
    source_had_embedded_color_profile: bool
    source_had_exif: bool
    source_metadata_byte_count: int
    result_version: int = SIGNATURE_IMAGE_RASTER_PROFILE_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.source_format) is not ImageSourceFormat
            or type(self.source_byte_count) is not int
            or not 1 <= self.source_byte_count <= MAX_IMAGE_SOURCE_BYTES
            or type(self.source_sha256) is not str
            or _SHA256.fullmatch(self.source_sha256) is None
            or type(self.exif_orientation) is not ImageExifOrientation
            or type(self.source_has_alpha) is not bool
            or type(self.source_had_embedded_color_profile) is not bool
            or self.source_had_embedded_color_profile
            or type(self.source_had_exif) is not bool
            or type(self.source_metadata_byte_count) is not int
            or not 0 <= self.source_metadata_byte_count <= MAX_IMAGE_METADATA_BYTES
            or type(self.result_version) is not int
            or self.result_version != SIGNATURE_IMAGE_RASTER_PROFILE_VERSION
        ):
            reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.result")
        dimensions = (
            self.encoded_width_pixels,
            self.encoded_height_pixels,
            self.oriented_width_pixels,
            self.oriented_height_pixels,
            self.width_pixels,
            self.height_pixels,
        )
        if any(
            type(value) is not int or not 1 <= value <= MAX_IMAGE_DIMENSION_PIXELS
            for value in dimensions
        ):
            reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.dimensions")
        expected_oriented = (
            (self.encoded_height_pixels, self.encoded_width_pixels)
            if self.exif_orientation.swaps_axes
            else (self.encoded_width_pixels, self.encoded_height_pixels)
        )
        if (self.oriented_width_pixels, self.oriented_height_pixels) != expected_oriented:
            reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.orientation")
        if (
            type(self.row_stride_bytes) is not int
            or self.row_stride_bytes != self.width_pixels * SIGNATURE_IMAGE_RGBA8_BYTES_PER_PIXEL
            or type(self.pixel_byte_count) is not int
            or self.pixel_byte_count != self.row_stride_bytes * self.height_pixels
            or not 1 <= self.pixel_byte_count <= MAX_IMAGE_OUTPUT_BYTES
            or type(self.pixel_sha256) is not str
            or _SHA256.fullmatch(self.pixel_sha256) is None
        ):
            reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.layout")

    @classmethod
    def from_normalization(
        cls,
        request: SignatureImageRasterRequest,
        normalized: ImageNormalizationResult,
    ) -> SignatureImageRasterResult:
        """Bind a worker decoder result to the exact encoded asset request."""

        if (
            type(request) is not SignatureImageRasterRequest
            or type(normalized) is not ImageNormalizationResult
            or normalized.alpha_policy is not ImageAlphaNormalizationPolicy.PRESERVE_STRAIGHT_ALPHA
            or normalized.background is not ImageRasterBackground.PRESERVED_ALPHA
        ):
            reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.normalize")
        metadata = normalized.metadata
        return cls(
            source_format=metadata.source_format,
            source_byte_count=request.source_byte_count,
            source_sha256=request.source_sha256,
            exif_orientation=metadata.exif_orientation,
            encoded_width_pixels=metadata.encoded_width_pixels,
            encoded_height_pixels=metadata.encoded_height_pixels,
            oriented_width_pixels=metadata.oriented_width_pixels,
            oriented_height_pixels=metadata.oriented_height_pixels,
            width_pixels=normalized.width_pixels,
            height_pixels=normalized.height_pixels,
            row_stride_bytes=normalized.row_stride_bytes,
            pixel_byte_count=normalized.bytes_written,
            pixel_sha256=normalized.pixel_sha256,
            source_has_alpha=metadata.has_alpha,
            source_had_embedded_color_profile=metadata.has_embedded_color_profile,
            source_had_exif=metadata.has_exif,
            source_metadata_byte_count=metadata.metadata_byte_count,
        )

    def validate_for(self, request: SignatureImageRasterRequest) -> None:
        """Authenticate source identity and the unique no-upscale fit plan."""

        if type(request) is not SignatureImageRasterRequest:
            reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, "signature.request")
        expected_width, expected_height = _fit_dimensions(
            self.oriented_width_pixels,
            self.oriented_height_pixels,
            request.maximum_width_pixels,
            request.maximum_height_pixels,
        )
        if (
            self.source_format is not request.source_format
            or self.source_byte_count != request.source_byte_count
            or not hmac.compare_digest(self.source_sha256, request.source_sha256)
            or self.width_pixels != expected_width
            or self.height_pixels != expected_height
        ):
            reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.binding")

    def __repr__(self) -> str:
        return (
            "SignatureImageRasterResult("
            f"width_pixels={self.width_pixels!r}, height_pixels={self.height_pixels!r}, "
            "details_hidden=True)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class SignatureImageRasterArtifactHeader:
    operation_id: UUID
    output_id: UUID
    result: SignatureImageRasterResult = field(repr=False)
    artifact_version: int = SIGNATURE_IMAGE_RASTER_ARTIFACT_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.operation_id) is not UUID
            or self.operation_id.version != 4
            or type(self.output_id) is not UUID
            or self.output_id.version != 4
            or self.operation_id == self.output_id
            or type(self.result) is not SignatureImageRasterResult
            or type(self.artifact_version) is not int
            or self.artifact_version != SIGNATURE_IMAGE_RASTER_ARTIFACT_VERSION
        ):
            reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.header")

    def __repr__(self) -> str:
        return "SignatureImageRasterArtifactHeader(details_hidden=True)"


@dataclass(frozen=True, slots=True, repr=False)
class SignatureImageRasterArtifact:
    """Validated metadata-free top-down straight-alpha RGBA8 pixels."""

    header: SignatureImageRasterArtifactHeader
    payload: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.header) is not SignatureImageRasterArtifactHeader:
            reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.artifact")
        result = self.header.result
        if (
            type(self.payload) is not bytes
            or len(self.payload) != result.pixel_byte_count
            or not hmac.compare_digest(
                hashlib.sha256(self.payload).hexdigest(), result.pixel_sha256
            )
        ):
            reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.pixels")
        pixels = memoryview(self.payload)
        if any(
            pixels[offset + 3] == 0 and any(pixels[offset : offset + 3])
            for offset in range(0, len(pixels), SIGNATURE_IMAGE_RGBA8_BYTES_PER_PIXEL)
        ):
            reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.hidden_rgb")

    def __repr__(self) -> str:
        return "SignatureImageRasterArtifact(details_hidden=True)"


def encode_signature_image_raster_artifact_header(
    header: SignatureImageRasterArtifactHeader,
) -> bytes:
    """Encode one fixed, content-free signature-raster header."""

    if type(header) is not SignatureImageRasterArtifactHeader:
        reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.header")
    result = header.result
    flags = 0
    if result.source_has_alpha:
        flags |= _FLAG_SOURCE_ALPHA
    if result.source_had_embedded_color_profile:
        flags |= _FLAG_SOURCE_ICC
    if result.source_had_exif:
        flags |= _FLAG_SOURCE_EXIF
    prefix = _ARTIFACT_PREFIX.pack(
        _ARTIFACT_MAGIC,
        header.artifact_version,
        SIGNATURE_IMAGE_RASTER_ARTIFACT_HEADER_BYTES,
        0,
        header.operation_id.bytes,
        header.output_id.bytes,
    )
    body = _ARTIFACT_RESULT.pack(
        _FORMAT_CODES[result.source_format],
        _COLOR_INTERPRETATION_SRGB_DEVICE_RGB,
        int(result.exif_orientation),
        flags,
        result.source_byte_count,
        result.source_metadata_byte_count,
        bytes.fromhex(result.source_sha256),
        result.encoded_width_pixels,
        result.encoded_height_pixels,
        result.oriented_width_pixels,
        result.oriented_height_pixels,
        result.width_pixels,
        result.height_pixels,
        result.row_stride_bytes,
        result.pixel_byte_count,
        bytes.fromhex(result.pixel_sha256),
    )
    encoded = prefix + body
    if len(encoded) != SIGNATURE_IMAGE_RASTER_ARTIFACT_HEADER_BYTES:
        reject_signature_image(SignatureImageErrorCode.INTERNAL_ERROR, "signature.header_size")
    return encoded


def decode_signature_image_raster_artifact(
    encoded: bytes,
    *,
    expected_operation_id: UUID | None = None,
    expected_output_id: UUID | None = None,
) -> SignatureImageRasterArtifact:
    """Decode, correlate, and authenticate one complete worker artifact."""

    for expected in (expected_operation_id, expected_output_id):
        if expected is not None and (type(expected) is not UUID or expected.version != 4):
            reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, "signature.correlation")
    if (
        type(encoded) is not bytes
        or not SIGNATURE_IMAGE_RASTER_ARTIFACT_HEADER_BYTES
        <= len(encoded)
        <= MAX_SIGNATURE_IMAGE_RASTER_ARTIFACT_BYTES
    ):
        reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.artifact_size")
    header_bytes = encoded[:SIGNATURE_IMAGE_RASTER_ARTIFACT_HEADER_BYTES]
    prefix = header_bytes[: _ARTIFACT_PREFIX.size]
    body = header_bytes[_ARTIFACT_PREFIX.size :]
    try:
        magic, version, size, reserved, operation_raw, output_raw = _ARTIFACT_PREFIX.unpack(prefix)
        (
            format_code,
            color_interpretation,
            orientation,
            flags,
            source_bytes,
            metadata_bytes,
            source_sha256,
            encoded_width,
            encoded_height,
            oriented_width,
            oriented_height,
            width,
            height,
            row_stride,
            pixel_bytes,
            pixel_sha256,
        ) = _ARTIFACT_RESULT.unpack(body)
        if (
            magic != _ARTIFACT_MAGIC
            or version != SIGNATURE_IMAGE_RASTER_ARTIFACT_VERSION
            or size != SIGNATURE_IMAGE_RASTER_ARTIFACT_HEADER_BYTES
            or reserved != 0
            or color_interpretation != _COLOR_INTERPRETATION_SRGB_DEVICE_RGB
            or flags & ~_KNOWN_FLAGS
        ):
            reject_signature_image(
                SignatureImageErrorCode.RESULT_INVALID, "signature.header_profile"
            )
        formats = {value: key for key, value in _FORMAT_CODES.items()}
        result = SignatureImageRasterResult(
            source_format=formats[format_code],
            source_byte_count=source_bytes,
            source_sha256=source_sha256.hex(),
            exif_orientation=ImageExifOrientation(orientation),
            encoded_width_pixels=encoded_width,
            encoded_height_pixels=encoded_height,
            oriented_width_pixels=oriented_width,
            oriented_height_pixels=oriented_height,
            width_pixels=width,
            height_pixels=height,
            row_stride_bytes=row_stride,
            pixel_byte_count=pixel_bytes,
            pixel_sha256=pixel_sha256.hex(),
            source_has_alpha=bool(flags & _FLAG_SOURCE_ALPHA),
            source_had_embedded_color_profile=bool(flags & _FLAG_SOURCE_ICC),
            source_had_exif=bool(flags & _FLAG_SOURCE_EXIF),
            source_metadata_byte_count=metadata_bytes,
        )
        header = SignatureImageRasterArtifactHeader(
            UUID(bytes=operation_raw),
            UUID(bytes=output_raw),
            result,
            version,
        )
    except SignatureImageError:
        raise
    except Exception:
        reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.header_decode")
    if (expected_operation_id is not None and header.operation_id != expected_operation_id) or (
        expected_output_id is not None and header.output_id != expected_output_id
    ):
        reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.correlation")
    return SignatureImageRasterArtifact(
        header,
        encoded[SIGNATURE_IMAGE_RASTER_ARTIFACT_HEADER_BYTES:],
    )


def _fit_dimensions(
    width: int,
    height: int,
    maximum_width: int,
    maximum_height: int,
) -> tuple[int, int]:
    if width > maximum_width or height > maximum_height:
        if maximum_width * height <= maximum_height * width:
            output_width = maximum_width
            output_height = max(1, (height * maximum_width + width // 2) // width)
            output_height = min(output_height, maximum_height)
        else:
            output_height = maximum_height
            output_width = max(1, (width * maximum_height + height // 2) // height)
            output_width = min(output_width, maximum_width)
        return output_width, output_height
    return width, height

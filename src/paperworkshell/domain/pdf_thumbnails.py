"""Pure, path-free contracts for bounded PDF thumbnail cache snapshots."""

from __future__ import annotations

import hashlib
import hmac
import re
import struct
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Never
from uuid import UUID

from paperworkshell.domain.pdf_rendering import (
    MAX_PDF_RENDER_BYTES,
    MAX_PDF_RENDER_DIMENSION_PIXELS,
    MAX_PDF_RENDER_PAGES,
    PdfAnnotationMode,
    PdfPageBox,
    PdfPageRenderRequest,
    PdfPageRenderResult,
    PdfRasterBackground,
    PdfRasterFormat,
    PdfRasterLayout,
    PdfRenderError,
    PdfRenderRotation,
)
from paperworkshell.domain.source_ingest import MAX_SAFE_SOURCE_INTEGER

PDF_THUMBNAIL_CACHE_KEY_VERSION: Final = 1
PDF_THUMBNAIL_ARTIFACT_VERSION: Final = 1
PDF_THUMBNAIL_REQUEST_VERSION: Final = 1
PDF_THUMBNAIL_RESOLVED_REQUEST_VERSION: Final = 1
PDF_THUMBNAIL_RENDER_RESULT_VERSION: Final = 1
PDF_THUMBNAIL_SNAPSHOT_VERSION: Final = 1
PDF_THUMBNAIL_PROGRESS_VERSION: Final = 1
PDF_THUMBNAIL_RESULT_VERSION: Final = 1

_CACHE_KEY_MAGIC: Final = b"PWS-THK1"
_ARTIFACT_MAGIC: Final = b"PWS-THM1"
_CONTEXT_TOKEN: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")

_PAGE_BOX_CROP_CODE: Final = 1
_PIXEL_FORMAT_RGBA8_CODE: Final = 1
_BACKGROUND_OPAQUE_WHITE_CODE: Final = 1
_ANNOTATIONS_STORED_APPEARANCES_CODE: Final = 1

# The key is a fixed, renderer-neutral serialization. The profile digest supplied by
# composition is opaque here and can change whenever the selected renderer or profile changes.
_CACHE_KEY_STRUCT: Final = struct.Struct(">8sHH16s16s16sIQIIHBBBB32s32s")
_ARTIFACT_PREFIX_STRUCT: Final = struct.Struct(">8sHHI16s16s")
_ARTIFACT_RESULT_STRUCT: Final = struct.Struct(">32sIIIQHHBBBBHH32s")
PDF_THUMBNAIL_CACHE_KEY_BYTES: Final = _CACHE_KEY_STRUCT.size
PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES: Final = (
    _ARTIFACT_PREFIX_STRUCT.size + _CACHE_KEY_STRUCT.size + _ARTIFACT_RESULT_STRUCT.size
)
MAX_PDF_THUMBNAIL_ARTIFACT_BYTES: Final = PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES + MAX_PDF_RENDER_BYTES


class PdfThumbnailErrorCode(StrEnum):
    """Closed registry of safe thumbnail-service failures."""

    MODEL_INVALID = "PDF_THUMBNAIL_MODEL_INVALID"
    SOURCE_INVALID = "PDF_THUMBNAIL_SOURCE_INVALID"
    PAGE_UNAVAILABLE = "PDF_THUMBNAIL_PAGE_UNAVAILABLE"
    CACHE_INVALID = "PDF_THUMBNAIL_CACHE_INVALID"
    LIMIT_EXCEEDED = "PDF_THUMBNAIL_LIMIT_EXCEEDED"
    CANCELLED = "PDF_THUMBNAIL_CANCELLED"
    TIMED_OUT = "PDF_THUMBNAIL_TIMED_OUT"
    WORKER_FAILED = "PDF_THUMBNAIL_WORKER_FAILED"
    DESTINATION_FAILED = "PDF_THUMBNAIL_DESTINATION_FAILED"
    RESULT_INVALID = "PDF_THUMBNAIL_RESULT_INVALID"
    INTERNAL_ERROR = "PDF_THUMBNAIL_INTERNAL_ERROR"


_SAFE_ERROR_MESSAGES: Final[dict[PdfThumbnailErrorCode, str]] = {
    PdfThumbnailErrorCode.MODEL_INVALID: (
        "PaperWorkShell received invalid thumbnail-operation data."
    ),
    PdfThumbnailErrorCode.SOURCE_INVALID: (
        "The selected source is not available for thumbnail rendering."
    ),
    PdfThumbnailErrorCode.PAGE_UNAVAILABLE: "The selected page is not available.",
    PdfThumbnailErrorCode.CACHE_INVALID: (
        "A cached thumbnail could not be verified and must be rebuilt."
    ),
    PdfThumbnailErrorCode.LIMIT_EXCEEDED: (
        "The requested thumbnail exceeds a configured safety limit."
    ),
    PdfThumbnailErrorCode.CANCELLED: "Thumbnail generation was cancelled before completion.",
    PdfThumbnailErrorCode.TIMED_OUT: ("Thumbnail generation exceeded its configured time limit."),
    PdfThumbnailErrorCode.WORKER_FAILED: (
        "The document worker could not generate the thumbnail safely."
    ),
    PdfThumbnailErrorCode.DESTINATION_FAILED: (
        "PaperWorkShell could not store the thumbnail safely."
    ),
    PdfThumbnailErrorCode.RESULT_INVALID: ("PaperWorkShell received an invalid thumbnail result."),
    PdfThumbnailErrorCode.INTERNAL_ERROR: (
        "PaperWorkShell could not complete the thumbnail operation safely."
    ),
}


@dataclass(slots=True, repr=False)
class PdfThumbnailError(ValueError):
    """Stable failure containing no paths, filenames, digests, or pixel values."""

    code: PdfThumbnailErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not PdfThumbnailErrorCode:
            self.code = PdfThumbnailErrorCode.INTERNAL_ERROR
        if type(self.context) is not str or _CONTEXT_TOKEN.fullmatch(self.context) is None:
            self.context = "pdf.thumbnail"
        self.safe_message = _SAFE_ERROR_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"PdfThumbnailError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_pdf_thumbnail(code: PdfThumbnailErrorCode, context: str) -> Never:
    """Raise one closed, sanitized thumbnail failure without retaining an exception chain."""

    error = PdfThumbnailError(code=code, context=context)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


def _require_uuid4(
    value: object,
    *,
    context: str,
    code: PdfThumbnailErrorCode = PdfThumbnailErrorCode.MODEL_INVALID,
) -> UUID:
    if type(value) is not UUID or value.version != 4:
        reject_pdf_thumbnail(code, context)
    return value


def _require_sha256(
    value: object,
    *,
    context: str,
    code: PdfThumbnailErrorCode = PdfThumbnailErrorCode.MODEL_INVALID,
) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        reject_pdf_thumbnail(code, context)
    return value


def _require_positive_integer(value: object, *, maximum: int, context: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, context)
    return value


@dataclass(frozen=True, slots=True)
class PdfThumbnailRequest:
    """One UI-safe page request containing internal identities and bounded geometry only."""

    case_id: UUID
    page_id: UUID
    maximum_width_pixels: int
    maximum_height_pixels: int
    additional_rotation: PdfRenderRotation = PdfRenderRotation.DEGREES_0
    request_version: int = PDF_THUMBNAIL_REQUEST_VERSION

    def __post_init__(self) -> None:
        _require_uuid4(self.case_id, context="thumbnail.request_case")
        _require_uuid4(self.page_id, context="thumbnail.request_page")
        if self.case_id == self.page_id:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "thumbnail.request_ids")
        _require_positive_integer(
            self.maximum_width_pixels,
            maximum=MAX_PDF_RENDER_DIMENSION_PIXELS,
            context="thumbnail.request_width",
        )
        _require_positive_integer(
            self.maximum_height_pixels,
            maximum=MAX_PDF_RENDER_DIMENSION_PIXELS,
            context="thumbnail.request_height",
        )
        if (
            type(self.additional_rotation) is not PdfRenderRotation
            or type(self.request_version) is not int
            or self.request_version != PDF_THUMBNAIL_REQUEST_VERSION
        ):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "thumbnail.request")


@dataclass(frozen=True, slots=True, repr=False)
class ResolvedPdfThumbnailRequest:
    """Broker-resolved immutable PDF source facts hidden from UI request construction."""

    request: PdfThumbnailRequest
    source_id: UUID
    source_page_index: int
    source_page_count: int
    source_byte_size: int
    source_sha256: str = field(repr=False)
    renderer_profile_sha256: str = field(repr=False)
    resolved_version: int = PDF_THUMBNAIL_RESOLVED_REQUEST_VERSION

    def __post_init__(self) -> None:
        if type(self.request) is not PdfThumbnailRequest:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "thumbnail.resolved_request")
        source_id = _require_uuid4(self.source_id, context="thumbnail.resolved_source")
        if source_id in {self.request.case_id, self.request.page_id}:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "thumbnail.resolved_ids")
        if (
            type(self.source_page_index) is not int
            or type(self.source_page_count) is not int
            or not 1 <= self.source_page_count <= MAX_PDF_RENDER_PAGES
            or not 0 <= self.source_page_index < self.source_page_count
        ):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "thumbnail.resolved_page")
        _require_positive_integer(
            self.source_byte_size,
            maximum=MAX_SAFE_SOURCE_INTEGER,
            context="thumbnail.resolved_source_size",
        )
        _require_sha256(self.source_sha256, context="thumbnail.resolved_source_digest")
        _require_sha256(
            self.renderer_profile_sha256,
            context="thumbnail.resolved_profile_digest",
        )
        if (
            type(self.resolved_version) is not int
            or self.resolved_version != PDF_THUMBNAIL_RESOLVED_REQUEST_VERSION
        ):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "thumbnail.resolved_version")

    @property
    def render_request(self) -> PdfPageRenderRequest:
        """Return the fixed CropBox renderer request after broker resolution."""

        return PdfPageRenderRequest(
            page_index=self.source_page_index,
            maximum_width_pixels=self.request.maximum_width_pixels,
            maximum_height_pixels=self.request.maximum_height_pixels,
            page_box=PdfPageBox.CROP,
            additional_rotation=self.request.additional_rotation,
        )

    @property
    def cache_key(self) -> PdfThumbnailCacheKey:
        """Derive the exact versioned cache identity for this resolved request."""

        return PdfThumbnailCacheKey(
            case_id=self.request.case_id,
            source_id=self.source_id,
            page_id=self.request.page_id,
            source_page_index=self.source_page_index,
            source_byte_size=self.source_byte_size,
            source_sha256=self.source_sha256,
            maximum_width_pixels=self.request.maximum_width_pixels,
            maximum_height_pixels=self.request.maximum_height_pixels,
            additional_rotation=self.request.additional_rotation,
            renderer_profile_sha256=self.renderer_profile_sha256,
        )

    def __repr__(self) -> str:
        return (
            "ResolvedPdfThumbnailRequest("
            f"case_id={self.request.case_id!r}, page_id={self.request.page_id!r}, "
            f"source_id={self.source_id!r}, source_page_index={self.source_page_index!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class PdfThumbnailCacheKey:
    """Canonical disposable-cache identity; never a filesystem authorization token."""

    case_id: UUID
    source_id: UUID
    page_id: UUID
    source_page_index: int
    source_byte_size: int
    source_sha256: str = field(repr=False)
    maximum_width_pixels: int
    maximum_height_pixels: int
    additional_rotation: PdfRenderRotation
    renderer_profile_sha256: str = field(repr=False)
    page_box: PdfPageBox = PdfPageBox.CROP
    pixel_format: PdfRasterFormat = PdfRasterFormat.RGBA8
    background: PdfRasterBackground = PdfRasterBackground.OPAQUE_WHITE
    annotations: PdfAnnotationMode = PdfAnnotationMode.STORED_APPEARANCES
    artifact_version: int = PDF_THUMBNAIL_ARTIFACT_VERSION
    key_version: int = PDF_THUMBNAIL_CACHE_KEY_VERSION
    _key_sha256: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        identifiers = (
            _require_uuid4(self.case_id, context="thumbnail.key_case"),
            _require_uuid4(self.source_id, context="thumbnail.key_source"),
            _require_uuid4(self.page_id, context="thumbnail.key_page"),
        )
        if len(set(identifiers)) != len(identifiers):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "thumbnail.key_ids")
        if (
            type(self.source_page_index) is not int
            or not 0 <= self.source_page_index < MAX_PDF_RENDER_PAGES
        ):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "thumbnail.key_page_index")
        _require_positive_integer(
            self.source_byte_size,
            maximum=MAX_SAFE_SOURCE_INTEGER,
            context="thumbnail.key_source_size",
        )
        _require_sha256(self.source_sha256, context="thumbnail.key_source_digest")
        _require_positive_integer(
            self.maximum_width_pixels,
            maximum=MAX_PDF_RENDER_DIMENSION_PIXELS,
            context="thumbnail.key_width",
        )
        _require_positive_integer(
            self.maximum_height_pixels,
            maximum=MAX_PDF_RENDER_DIMENSION_PIXELS,
            context="thumbnail.key_height",
        )
        _require_sha256(
            self.renderer_profile_sha256,
            context="thumbnail.key_profile_digest",
        )
        if (
            type(self.additional_rotation) is not PdfRenderRotation
            or self.page_box is not PdfPageBox.CROP
            or self.pixel_format is not PdfRasterFormat.RGBA8
            or self.background is not PdfRasterBackground.OPAQUE_WHITE
            or self.annotations is not PdfAnnotationMode.STORED_APPEARANCES
            or type(self.artifact_version) is not int
            or self.artifact_version != PDF_THUMBNAIL_ARTIFACT_VERSION
            or type(self.key_version) is not int
            or self.key_version != PDF_THUMBNAIL_CACHE_KEY_VERSION
        ):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "thumbnail.key_profile")
        digest = hashlib.sha256(self.canonical_bytes()).hexdigest()
        object.__setattr__(self, "_key_sha256", digest)

    def canonical_bytes(self) -> bytes:
        """Return the unique fixed-width serialization covered by the cache digest."""

        return _CACHE_KEY_STRUCT.pack(
            _CACHE_KEY_MAGIC,
            self.key_version,
            self.artifact_version,
            self.case_id.bytes,
            self.source_id.bytes,
            self.page_id.bytes,
            self.source_page_index,
            self.source_byte_size,
            self.maximum_width_pixels,
            self.maximum_height_pixels,
            int(self.additional_rotation),
            _PAGE_BOX_CROP_CODE,
            _PIXEL_FORMAT_RGBA8_CODE,
            _BACKGROUND_OPAQUE_WHITE_CODE,
            _ANNOTATIONS_STORED_APPEARANCES_CODE,
            bytes.fromhex(self.source_sha256),
            bytes.fromhex(self.renderer_profile_sha256),
        )

    @property
    def key_sha256(self) -> str:
        """Return the full opaque internal cache-key digest."""

        return self._key_sha256

    @property
    def internal_name(self) -> str:
        """Return a path-component-safe name containing internal identifiers only."""

        return f"{self.page_id}.{self._key_sha256}.pwsthumb"

    def __repr__(self) -> str:
        return (
            "PdfThumbnailCacheKey("
            f"case_id={self.case_id!r}, source_id={self.source_id!r}, "
            f"page_id={self.page_id!r}, source_page_index={self.source_page_index!r})"
        )


def decode_pdf_thumbnail_cache_key(payload: bytes) -> PdfThumbnailCacheKey:
    """Decode one exact canonical key without accepting alternate or future layouts."""

    if type(payload) is not bytes or len(payload) != PDF_THUMBNAIL_CACHE_KEY_BYTES:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.key_bytes")
    try:
        (
            magic,
            key_version,
            artifact_version,
            case_bytes,
            source_bytes,
            page_bytes,
            source_page_index,
            source_byte_size,
            maximum_width_pixels,
            maximum_height_pixels,
            additional_rotation,
            page_box,
            pixel_format,
            background,
            annotations,
            source_sha256,
            renderer_profile_sha256,
        ) = _CACHE_KEY_STRUCT.unpack(payload)
        if magic != _CACHE_KEY_MAGIC:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.key_magic")
        if (
            key_version != PDF_THUMBNAIL_CACHE_KEY_VERSION
            or artifact_version != PDF_THUMBNAIL_ARTIFACT_VERSION
        ):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.key_version")
        if (
            page_box != _PAGE_BOX_CROP_CODE
            or pixel_format != _PIXEL_FORMAT_RGBA8_CODE
            or background != _BACKGROUND_OPAQUE_WHITE_CODE
            or annotations != _ANNOTATIONS_STORED_APPEARANCES_CODE
        ):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.key_profile")
        return PdfThumbnailCacheKey(
            case_id=UUID(bytes=case_bytes),
            source_id=UUID(bytes=source_bytes),
            page_id=UUID(bytes=page_bytes),
            source_page_index=source_page_index,
            source_byte_size=source_byte_size,
            source_sha256=source_sha256.hex(),
            maximum_width_pixels=maximum_width_pixels,
            maximum_height_pixels=maximum_height_pixels,
            additional_rotation=PdfRenderRotation(additional_rotation),
            renderer_profile_sha256=renderer_profile_sha256.hex(),
        )
    except PdfThumbnailError as error:
        if error.code is PdfThumbnailErrorCode.CACHE_INVALID:
            raise
        reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.key_decode")
    except Exception:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.key_decode")


@dataclass(frozen=True, slots=True, repr=False)
class PdfThumbnailArtifactHeader:
    """Fixed-layout metadata bound to one operation, cache key, and raw raster."""

    operation_id: UUID
    cache_object_id: UUID
    cache_key: PdfThumbnailCacheKey = field(repr=False)
    width_pixels: int
    height_pixels: int
    row_stride_bytes: int
    payload_bytes: int
    pixel_sha256: str = field(repr=False)
    intrinsic_rotation: PdfRenderRotation
    additional_rotation: PdfRenderRotation
    page_box: PdfPageBox = PdfPageBox.CROP
    pixel_format: PdfRasterFormat = PdfRasterFormat.RGBA8
    background: PdfRasterBackground = PdfRasterBackground.OPAQUE_WHITE
    annotations: PdfAnnotationMode = PdfAnnotationMode.STORED_APPEARANCES
    render_result_version: int = PDF_THUMBNAIL_RENDER_RESULT_VERSION
    artifact_version: int = PDF_THUMBNAIL_ARTIFACT_VERSION

    def __post_init__(self) -> None:
        operation_id = _require_uuid4(
            self.operation_id,
            context="thumbnail.header_operation",
            code=PdfThumbnailErrorCode.RESULT_INVALID,
        )
        cache_object_id = _require_uuid4(
            self.cache_object_id,
            context="thumbnail.header_cache_object",
            code=PdfThumbnailErrorCode.RESULT_INVALID,
        )
        if type(self.cache_key) is not PdfThumbnailCacheKey:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.header_key")
        if (
            len(
                {
                    operation_id,
                    cache_object_id,
                    self.cache_key.case_id,
                    self.cache_key.source_id,
                    self.cache_key.page_id,
                }
            )
            != 5
        ):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.header_ids")
        _require_valid_layout(
            self.width_pixels,
            self.height_pixels,
            self.row_stride_bytes,
            self.payload_bytes,
            code=PdfThumbnailErrorCode.RESULT_INVALID,
            context="thumbnail.header_layout",
        )
        if (
            self.width_pixels > self.cache_key.maximum_width_pixels
            or self.height_pixels > self.cache_key.maximum_height_pixels
        ):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.header_bounds")
        _require_sha256(
            self.pixel_sha256,
            context="thumbnail.header_pixel_digest",
            code=PdfThumbnailErrorCode.RESULT_INVALID,
        )
        if (
            type(self.intrinsic_rotation) is not PdfRenderRotation
            or type(self.additional_rotation) is not PdfRenderRotation
            or self.additional_rotation is not self.cache_key.additional_rotation
            or self.page_box is not self.cache_key.page_box
            or self.pixel_format is not self.cache_key.pixel_format
            or self.background is not self.cache_key.background
            or self.annotations is not self.cache_key.annotations
            or type(self.render_result_version) is not int
            or self.render_result_version != PDF_THUMBNAIL_RENDER_RESULT_VERSION
            or type(self.artifact_version) is not int
            or self.artifact_version != PDF_THUMBNAIL_ARTIFACT_VERSION
        ):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.header_profile")

    def __repr__(self) -> str:
        return (
            "PdfThumbnailArtifactHeader("
            f"operation_id={self.operation_id!r}, cache_object_id={self.cache_object_id!r}, "
            f"page_id={self.cache_key.page_id!r}, "
            f"width_pixels={self.width_pixels!r}, height_pixels={self.height_pixels!r})"
        )


def pdf_thumbnail_header_from_render_result(
    operation_id: UUID,
    cache_object_id: UUID,
    cache_key: PdfThumbnailCacheKey,
    result: PdfPageRenderResult,
) -> PdfThumbnailArtifactHeader:
    """Bind renderer-neutral metadata to one exact cache operation."""

    if type(cache_key) is not PdfThumbnailCacheKey or type(result) is not PdfPageRenderResult:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.render_result")
    if (
        result.page_index != cache_key.source_page_index
        or result.page_box is not cache_key.page_box
        or result.additional_rotation is not cache_key.additional_rotation
        or result.pixel_format is not cache_key.pixel_format
        or result.background is not cache_key.background
        or result.annotations is not cache_key.annotations
    ):
        reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.render_binding")
    return PdfThumbnailArtifactHeader(
        operation_id=operation_id,
        cache_object_id=cache_object_id,
        cache_key=cache_key,
        width_pixels=result.width_pixels,
        height_pixels=result.height_pixels,
        row_stride_bytes=result.row_stride_bytes,
        payload_bytes=result.bytes_written,
        pixel_sha256=result.pixel_sha256,
        intrinsic_rotation=result.intrinsic_rotation,
        additional_rotation=result.additional_rotation,
        page_box=result.page_box,
        pixel_format=result.pixel_format,
        background=result.background,
        annotations=result.annotations,
        render_result_version=result.result_version,
    )


def encode_pdf_thumbnail_artifact_header(header: PdfThumbnailArtifactHeader) -> bytes:
    """Encode one exact current-version artifact header."""

    if type(header) is not PdfThumbnailArtifactHeader:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.header")
    prefix = _ARTIFACT_PREFIX_STRUCT.pack(
        _ARTIFACT_MAGIC,
        header.artifact_version,
        PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES,
        0,
        header.operation_id.bytes,
        header.cache_object_id.bytes,
    )
    result = _ARTIFACT_RESULT_STRUCT.pack(
        bytes.fromhex(header.cache_key.key_sha256),
        header.width_pixels,
        header.height_pixels,
        header.row_stride_bytes,
        header.payload_bytes,
        int(header.intrinsic_rotation),
        int(header.additional_rotation),
        _PAGE_BOX_CROP_CODE,
        _PIXEL_FORMAT_RGBA8_CODE,
        _BACKGROUND_OPAQUE_WHITE_CODE,
        _ANNOTATIONS_STORED_APPEARANCES_CODE,
        header.render_result_version,
        0,
        bytes.fromhex(header.pixel_sha256),
    )
    encoded = prefix + header.cache_key.canonical_bytes() + result
    if len(encoded) != PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.header_size")
    return encoded


def decode_pdf_thumbnail_artifact_header(payload: bytes) -> PdfThumbnailArtifactHeader:
    """Decode and validate one exact fixed-layout raw-thumbnail header."""

    if type(payload) is not bytes or len(payload) != PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.header_bytes")
    prefix_end = _ARTIFACT_PREFIX_STRUCT.size
    key_end = prefix_end + PDF_THUMBNAIL_CACHE_KEY_BYTES
    try:
        magic, artifact_version, header_bytes, reserved, operation_bytes, cache_object_bytes = (
            _ARTIFACT_PREFIX_STRUCT.unpack(payload[:prefix_end])
        )
        if magic != _ARTIFACT_MAGIC:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.header_magic")
        if (
            artifact_version != PDF_THUMBNAIL_ARTIFACT_VERSION
            or header_bytes != PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES
        ):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.header_version")
        if reserved != 0:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.header_reserved")
        cache_key = decode_pdf_thumbnail_cache_key(payload[prefix_end:key_end])
        (
            key_sha256,
            width_pixels,
            height_pixels,
            row_stride_bytes,
            payload_bytes,
            intrinsic_rotation,
            additional_rotation,
            page_box,
            pixel_format,
            background,
            annotations,
            render_result_version,
            result_reserved,
            pixel_sha256,
        ) = _ARTIFACT_RESULT_STRUCT.unpack(payload[key_end:])
        if not hmac.compare_digest(key_sha256, bytes.fromhex(cache_key.key_sha256)):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.header_key_digest")
        if (
            page_box != _PAGE_BOX_CROP_CODE
            or pixel_format != _PIXEL_FORMAT_RGBA8_CODE
            or background != _BACKGROUND_OPAQUE_WHITE_CODE
            or annotations != _ANNOTATIONS_STORED_APPEARANCES_CODE
        ):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.header_profile")
        if result_reserved != 0:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.header_reserved")
        return PdfThumbnailArtifactHeader(
            operation_id=UUID(bytes=operation_bytes),
            cache_object_id=UUID(bytes=cache_object_bytes),
            cache_key=cache_key,
            width_pixels=width_pixels,
            height_pixels=height_pixels,
            row_stride_bytes=row_stride_bytes,
            payload_bytes=payload_bytes,
            pixel_sha256=pixel_sha256.hex(),
            intrinsic_rotation=PdfRenderRotation(intrinsic_rotation),
            additional_rotation=PdfRenderRotation(additional_rotation),
            render_result_version=render_result_version,
        )
    except PdfThumbnailError as error:
        if error.code is PdfThumbnailErrorCode.CACHE_INVALID:
            raise
        reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.header_decode")
    except Exception:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.header_decode")


@dataclass(frozen=True, slots=True, repr=False)
class PdfThumbnailArtifact:
    """Validated fixed header plus exact raw RGBA8 payload."""

    header: PdfThumbnailArtifactHeader
    payload: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.header) is not PdfThumbnailArtifactHeader or type(self.payload) is not bytes:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.artifact")
        _validate_thumbnail_payload(
            self.payload,
            expected_bytes=self.header.payload_bytes,
            expected_sha256=self.header.pixel_sha256,
            code=PdfThumbnailErrorCode.CACHE_INVALID,
        )

    def __repr__(self) -> str:
        return (
            "PdfThumbnailArtifact("
            f"page_id={self.header.cache_key.page_id!r}, "
            f"width_pixels={self.header.width_pixels!r}, "
            f"height_pixels={self.header.height_pixels!r})"
        )


def encode_pdf_thumbnail_artifact(
    header: PdfThumbnailArtifactHeader,
    payload: bytes,
) -> bytes:
    """Encode one complete bounded artifact after payload integrity validation."""

    artifact = PdfThumbnailArtifact(header, payload)
    encoded = encode_pdf_thumbnail_artifact_header(artifact.header) + artifact.payload
    if len(encoded) > MAX_PDF_THUMBNAIL_ARTIFACT_BYTES:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.LIMIT_EXCEEDED, "thumbnail.artifact_size")
    return encoded


def decode_pdf_thumbnail_artifact(
    payload: bytes,
    *,
    expected_key: PdfThumbnailCacheKey | None = None,
    expected_operation_id: UUID | None = None,
    expected_cache_object_id: UUID | None = None,
) -> PdfThumbnailArtifact:
    """Validate one complete cache snapshot before exposing its fixed-layout pixels."""

    if (
        type(payload) is not bytes
        or len(payload) < PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES
        or len(payload) > MAX_PDF_THUMBNAIL_ARTIFACT_BYTES
    ):
        reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.artifact_bytes")
    header = decode_pdf_thumbnail_artifact_header(payload[:PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES])
    expected_length = PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES + header.payload_bytes
    if len(payload) != expected_length:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.artifact_length")
    if expected_key is not None:
        if type(expected_key) is not PdfThumbnailCacheKey or header.cache_key != expected_key:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.artifact_key")
    if expected_operation_id is not None:
        if (
            type(expected_operation_id) is not UUID
            or expected_operation_id.version != 4
            or header.operation_id != expected_operation_id
        ):
            reject_pdf_thumbnail(
                PdfThumbnailErrorCode.CACHE_INVALID,
                "thumbnail.artifact_operation",
            )
    if expected_cache_object_id is not None:
        if (
            type(expected_cache_object_id) is not UUID
            or expected_cache_object_id.version != 4
            or header.cache_object_id != expected_cache_object_id
        ):
            reject_pdf_thumbnail(
                PdfThumbnailErrorCode.CACHE_INVALID,
                "thumbnail.artifact_cache_object",
            )
    return PdfThumbnailArtifact(
        header=header,
        payload=payload[PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES:],
    )


class PdfThumbnailCacheStatus(StrEnum):
    """How a validated snapshot was obtained."""

    HIT = "hit"
    GENERATED = "generated"
    REBUILT = "rebuilt"


@dataclass(frozen=True, slots=True)
class PdfThumbnailSnapshot:
    """UI-safe bounded pixels from one already validated cache artifact."""

    case_id: UUID
    page_id: UUID
    width_pixels: int
    height_pixels: int
    row_stride_bytes: int
    pixels: bytes = field(repr=False)
    intrinsic_rotation: PdfRenderRotation
    additional_rotation: PdfRenderRotation
    pixel_format: PdfRasterFormat = PdfRasterFormat.RGBA8
    background: PdfRasterBackground = PdfRasterBackground.OPAQUE_WHITE
    annotations: PdfAnnotationMode = PdfAnnotationMode.STORED_APPEARANCES
    snapshot_version: int = PDF_THUMBNAIL_SNAPSHOT_VERSION

    def __post_init__(self) -> None:
        case_id = _require_uuid4(
            self.case_id,
            context="thumbnail.snapshot_case",
            code=PdfThumbnailErrorCode.RESULT_INVALID,
        )
        page_id = _require_uuid4(
            self.page_id,
            context="thumbnail.snapshot_page",
            code=PdfThumbnailErrorCode.RESULT_INVALID,
        )
        if case_id == page_id:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.snapshot_ids")
        if type(self.pixels) is not bytes:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.snapshot_pixels")
        _require_valid_layout(
            self.width_pixels,
            self.height_pixels,
            self.row_stride_bytes,
            len(self.pixels),
            code=PdfThumbnailErrorCode.RESULT_INVALID,
            context="thumbnail.snapshot_layout",
        )
        _require_opaque_rgba(
            self.pixels,
            code=PdfThumbnailErrorCode.RESULT_INVALID,
            context="thumbnail.snapshot_alpha",
        )
        if (
            type(self.intrinsic_rotation) is not PdfRenderRotation
            or type(self.additional_rotation) is not PdfRenderRotation
            or self.pixel_format is not PdfRasterFormat.RGBA8
            or self.background is not PdfRasterBackground.OPAQUE_WHITE
            or self.annotations is not PdfAnnotationMode.STORED_APPEARANCES
            or type(self.snapshot_version) is not int
            or self.snapshot_version != PDF_THUMBNAIL_SNAPSHOT_VERSION
        ):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.snapshot_profile")


@dataclass(frozen=True, slots=True, repr=False)
class PdfThumbnailResult:
    """One path-free service result containing a validated snapshot and cache identity."""

    request: PdfThumbnailRequest
    cache_key: PdfThumbnailCacheKey = field(repr=False)
    snapshot: PdfThumbnailSnapshot = field(repr=False)
    cache_status: PdfThumbnailCacheStatus
    result_version: int = PDF_THUMBNAIL_RESULT_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.request) is not PdfThumbnailRequest
            or type(self.cache_key) is not PdfThumbnailCacheKey
            or type(self.snapshot) is not PdfThumbnailSnapshot
            or type(self.cache_status) is not PdfThumbnailCacheStatus
            or type(self.result_version) is not int
            or self.result_version != PDF_THUMBNAIL_RESULT_VERSION
        ):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.result")
        if (
            self.request.case_id != self.cache_key.case_id
            or self.request.page_id != self.cache_key.page_id
            or self.request.case_id != self.snapshot.case_id
            or self.request.page_id != self.snapshot.page_id
            or self.request.maximum_width_pixels != self.cache_key.maximum_width_pixels
            or self.request.maximum_height_pixels != self.cache_key.maximum_height_pixels
            or self.request.additional_rotation is not self.cache_key.additional_rotation
            or self.request.additional_rotation is not self.snapshot.additional_rotation
            or self.snapshot.width_pixels > self.request.maximum_width_pixels
            or self.snapshot.height_pixels > self.request.maximum_height_pixels
        ):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.result_binding")

    def __repr__(self) -> str:
        return (
            "PdfThumbnailResult("
            f"case_id={self.request.case_id!r}, page_id={self.request.page_id!r}, "
            f"cache_status={self.cache_status.value!r})"
        )


def pdf_thumbnail_result_from_artifact(
    request: PdfThumbnailRequest,
    artifact: PdfThumbnailArtifact,
    cache_status: PdfThumbnailCacheStatus,
) -> PdfThumbnailResult:
    """Create a UI-safe result from one already verified fixed-layout artifact."""

    if (
        type(request) is not PdfThumbnailRequest
        or type(artifact) is not PdfThumbnailArtifact
        or type(cache_status) is not PdfThumbnailCacheStatus
    ):
        reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.result_factory")
    header = artifact.header
    key = header.cache_key
    if request.case_id != key.case_id or request.page_id != key.page_id:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.result_factory")
    snapshot = PdfThumbnailSnapshot(
        case_id=key.case_id,
        page_id=key.page_id,
        width_pixels=header.width_pixels,
        height_pixels=header.height_pixels,
        row_stride_bytes=header.row_stride_bytes,
        pixels=artifact.payload,
        intrinsic_rotation=header.intrinsic_rotation,
        additional_rotation=header.additional_rotation,
        pixel_format=header.pixel_format,
        background=header.background,
        annotations=header.annotations,
    )
    return PdfThumbnailResult(request, key, snapshot, cache_status)


class PdfThumbnailProgressPhase(StrEnum):
    """Closed page-generation progress phases."""

    LOOKUP = "lookup"
    RENDERING = "rendering"
    VALIDATING = "validating"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class PdfThumbnailProgress:
    """Path- and content-free aggregate page progress."""

    operation_id: UUID
    phase: PdfThumbnailProgressPhase
    completed_pages: int
    total_pages: int
    progress_version: int = PDF_THUMBNAIL_PROGRESS_VERSION

    def __post_init__(self) -> None:
        _require_uuid4(self.operation_id, context="thumbnail.progress_operation")
        if (
            type(self.phase) is not PdfThumbnailProgressPhase
            or type(self.completed_pages) is not int
            or type(self.total_pages) is not int
            or not 1 <= self.total_pages <= MAX_PDF_RENDER_PAGES
            or not 0 <= self.completed_pages <= self.total_pages
            or (
                self.phase is PdfThumbnailProgressPhase.COMPLETE
                and self.completed_pages != self.total_pages
            )
            or type(self.progress_version) is not int
            or self.progress_version != PDF_THUMBNAIL_PROGRESS_VERSION
        ):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "thumbnail.progress")


def _require_valid_layout(
    width_pixels: object,
    height_pixels: object,
    row_stride_bytes: object,
    payload_bytes: object,
    *,
    code: PdfThumbnailErrorCode,
    context: str,
) -> PdfRasterLayout:
    if (
        type(width_pixels) is not int
        or type(height_pixels) is not int
        or type(row_stride_bytes) is not int
        or type(payload_bytes) is not int
    ):
        reject_pdf_thumbnail(code, context)
    try:
        return PdfRasterLayout(
            width_pixels=width_pixels,
            height_pixels=height_pixels,
            row_stride_bytes=row_stride_bytes,
            pixel_count=width_pixels * height_pixels,
            byte_count=payload_bytes,
        )
    except PdfRenderError:
        reject_pdf_thumbnail(code, context)


def _validate_thumbnail_payload(
    payload: bytes,
    *,
    expected_bytes: int,
    expected_sha256: str,
    code: PdfThumbnailErrorCode,
) -> None:
    if len(payload) != expected_bytes:
        reject_pdf_thumbnail(code, "thumbnail.payload_length")
    digest = hashlib.sha256(payload).hexdigest()
    if not hmac.compare_digest(digest, expected_sha256):
        reject_pdf_thumbnail(code, "thumbnail.payload_digest")
    _require_opaque_rgba(payload, code=code, context="thumbnail.payload_alpha")


def _require_opaque_rgba(
    payload: bytes,
    *,
    code: PdfThumbnailErrorCode,
    context: str,
) -> None:
    try:
        alpha = memoryview(payload)[3::4]
        if any(value != 255 for value in alpha):
            reject_pdf_thumbnail(code, context)
    except PdfThumbnailError:
        raise
    except Exception:
        reject_pdf_thumbnail(code, context)

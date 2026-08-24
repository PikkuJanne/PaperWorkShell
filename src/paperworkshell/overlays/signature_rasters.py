"""Bridge verified signature-image artifacts to renderer-owned raster sources."""

from __future__ import annotations

import hashlib
import hmac
import io
from dataclasses import dataclass, field
from typing import Self
from uuid import UUID

from paperworkshell.domain.signature_images import (
    SignatureImageErrorCode,
    SignatureImageRasterArtifact,
    SignatureImageRasterRequest,
    reject_signature_image,
)
from paperworkshell.overlays.rendering import OverlayRasterAsset


@dataclass(frozen=True, slots=True, repr=False)
class SignatureImagePreview:
    """Bounded metadata-free preview pixels with explicit background semantics."""

    asset_id: UUID
    width_pixels: int
    height_pixels: int
    row_stride_bytes: int
    pixels: bytes = field(repr=False)
    source_has_alpha: bool
    source_had_embedded_color_profile: bool
    source_had_exif: bool
    oriented_source_width_pixels: int
    oriented_source_height_pixels: int

    def __post_init__(self) -> None:
        if (
            type(self.asset_id) is not UUID
            or self.asset_id.version != 4
            or type(self.width_pixels) is not int
            or type(self.height_pixels) is not int
            or type(self.row_stride_bytes) is not int
            or self.row_stride_bytes != self.width_pixels * 4
            or type(self.pixels) is not bytes
            or len(self.pixels) != self.row_stride_bytes * self.height_pixels
            or type(self.source_has_alpha) is not bool
            or type(self.source_had_embedded_color_profile) is not bool
            or type(self.source_had_exif) is not bool
            or type(self.oriented_source_width_pixels) is not int
            or self.oriented_source_width_pixels < 1
            or type(self.oriented_source_height_pixels) is not int
            or self.oriented_source_height_pixels < 1
        ):
            reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.preview")

    @classmethod
    def from_artifact(
        cls,
        request: SignatureImageRasterRequest,
        artifact: SignatureImageRasterArtifact,
    ) -> Self:
        if (
            type(request) is not SignatureImageRasterRequest
            or type(artifact) is not SignatureImageRasterArtifact
        ):
            reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, "signature.preview")
        artifact.header.result.validate_for(request)
        result = artifact.header.result
        return cls(
            asset_id=request.asset_id,
            width_pixels=result.width_pixels,
            height_pixels=result.height_pixels,
            row_stride_bytes=result.row_stride_bytes,
            pixels=artifact.payload,
            source_has_alpha=result.source_has_alpha,
            source_had_embedded_color_profile=result.source_had_embedded_color_profile,
            source_had_exif=result.source_had_exif,
            oriented_source_width_pixels=result.oriented_width_pixels,
            oriented_source_height_pixels=result.oriented_height_pixels,
        )

    def __repr__(self) -> str:
        return (
            "SignatureImagePreview("
            f"width_pixels={self.width_pixels!r}, height_pixels={self.height_pixels!r}, "
            "details_hidden=True)"
        )


class SignatureImageOverlayRasterSource:
    """Seekable exact pixels whose position is restored by the overlay renderer."""

    __slots__ = ("_stream", "_metadata")

    def __init__(self, metadata: OverlayRasterAsset, pixels: bytes) -> None:
        if type(metadata) is not OverlayRasterAsset or type(pixels) is not bytes:
            reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, "signature.raster")
        if len(pixels) != metadata.byte_count or not hmac.compare_digest(
            hashlib.sha256(pixels).hexdigest(), metadata.pixel_sha256
        ):
            reject_signature_image(SignatureImageErrorCode.RESULT_INVALID, "signature.raster")
        self._metadata = metadata
        self._stream = io.BytesIO(pixels)

    @classmethod
    def from_artifact(
        cls,
        request: SignatureImageRasterRequest,
        artifact: SignatureImageRasterArtifact,
    ) -> Self:
        if (
            type(request) is not SignatureImageRasterRequest
            or type(artifact) is not SignatureImageRasterArtifact
        ):
            reject_signature_image(SignatureImageErrorCode.MODEL_INVALID, "signature.raster")
        result = artifact.header.result
        result.validate_for(request)
        metadata = OverlayRasterAsset(
            asset_id=request.asset_id,
            width_pixels=result.width_pixels,
            height_pixels=result.height_pixels,
            row_stride_bytes=result.row_stride_bytes,
            byte_count=result.pixel_byte_count,
            pixel_sha256=result.pixel_sha256,
        )
        return cls(metadata, artifact.payload)

    @property
    def metadata(self) -> OverlayRasterAsset:
        return self._metadata

    def read(self, size: int = -1, /) -> bytes:
        return self._stream.read(size)

    def seek(self, offset: int, whence: int = 0, /) -> int:
        return self._stream.seek(offset, whence)

    def tell(self) -> int:
        return self._stream.tell()

    def copy(self) -> Self:
        """Return an independently positioned source over the same immutable value."""

        position = self.tell()
        self.seek(0)
        try:
            payload = self.read()
        finally:
            self.seek(position)
        return type(self)(self._metadata, payload)

    def __repr__(self) -> str:
        return "SignatureImageOverlayRasterSource(details_hidden=True)"

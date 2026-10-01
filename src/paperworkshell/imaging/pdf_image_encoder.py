"""Bounded Pillow work on admitted RGB/gray samples from the PDF adapter.

This helper receives no PDF objects, paths, or worker controls. Embedded-image
axes are authoritative: photo-intake EXIF and alpha normalization do not apply.
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Callable
from dataclasses import dataclass, field

import PIL
from PIL import Image, ImageFile

from paperworkshell.domain.image_transforms import IMAGE_TRANSFORM_PILLOW_VERSION
from paperworkshell.domain.pdf_export import PdfExportErrorCode, reject_pdf_export
from paperworkshell.domain.pdf_image_stages import (
    PdfImageAssetAnalysis,
    PdfImageColorMode,
    PdfImageStageGeometry,
    PdfImageStageProfile,
)


@dataclass(frozen=True, slots=True)
class EncodedImage:
    data: bytes = field(repr=False)
    sha256: str = field(repr=False)
    geometry: PdfImageStageGeometry


class _JpegBuffer(io.BytesIO):
    def __init__(self, capacity: int, checkpoint: Callable[[], None]) -> None:
        super().__init__()
        self.capacity = capacity
        self.checkpoint = checkpoint
        self.overflow = False

    def write(self, data: object, /) -> int:
        self.checkpoint()
        view = memoryview(data)  # type: ignore[arg-type]
        if self.tell() + view.nbytes > self.capacity:
            self.overflow = True
            raise OSError
        return super().write(view)


def required_image_memory(asset: PdfImageAssetAnalysis, geometry: PdfImageStageGeometry) -> int:
    """Account for native RGB cores, input/output copies and encoder buffers.

    Pillow RGB cores use four bytes per pixel. Two cores/copies of both extents
    plus encoded source/output copies are admitted before native decode. The
    worker reserves additional native/bookkeeping headroom outside this budget.
    """

    return (
        asset.width_pixels * asset.height_pixels * 8
        + geometry.width_pixels * geometry.height_pixels * 8
        + asset.encoded_byte_size * 4
        + 65_536
    )


def encode_pdf_image_samples(
    data: bytes | memoryview,
    *,
    jpeg_encoded: bool,
    asset: PdfImageAssetAnalysis,
    geometry: PdfImageStageGeometry,
    profile: PdfImageStageProfile,
    maximum_bytes: int,
    checkpoint: Callable[[], None],
) -> EncodedImage | None:
    """Decode admitted samples, resize once, and reopen a bounded JPEG.

    The caller admits encoded/pixel sizes and aggregate memory before calling.
    The source buffer is consumed synchronously; no image or buffer is cached.
    """

    checkpoint()
    if PIL.__version__ != IMAGE_TRANSFORM_PILLOW_VERSION or ImageFile.LOAD_TRUNCATED_IMAGES:
        reject_pdf_export(PdfExportErrorCode.SOURCE_UNSUPPORTED, "image_stage.pillow_runtime")
    mode = "RGB" if asset.color_mode is PdfImageColorMode.RGB else "L"
    size = (asset.width_pixels, asset.height_pixels)
    if jpeg_encoded:
        with io.BytesIO(data) as source:
            with Image.open(source) as opened:
                if (
                    opened.format != "JPEG"
                    or opened.mode != mode
                    or opened.size != size
                    or getattr(opened, "n_frames", 1) != 1
                    or any(key in opened.info for key in ("icc_profile", "adobe_transform"))
                ):
                    reject_pdf_export(PdfExportErrorCode.SOURCE_UNSUPPORTED, "image_stage.jpeg")
                checkpoint()
                opened.load()
                # EXIF metadata may exist but is never applied to PDF image axes.
                return _resize_encode(opened, geometry, profile, maximum_bytes, checkpoint)
    channels = 3 if mode == "RGB" else 1
    if len(data) != asset.width_pixels * asset.height_pixels * channels:
        reject_pdf_export(
            PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "image_stage.image_samples"
        )
    with Image.frombytes(mode, size, data) as opened:
        # The PDF adapter passes its decoded buffer directly into this call. Drop
        # the only Python sample reference before allocating the resized image.
        del data
        return _resize_encode(opened, geometry, profile, maximum_bytes, checkpoint)


def _resize_encode(
    opened: Image.Image,
    geometry: PdfImageStageGeometry,
    profile: PdfImageStageProfile,
    maximum_bytes: int,
    checkpoint: Callable[[], None],
) -> EncodedImage | None:
    checkpoint()
    target = (geometry.width_pixels, geometry.height_pixels)
    resized = opened if opened.size == target else opened.resize(target, Image.Resampling.LANCZOS)
    try:
        checkpoint()
        with _JpegBuffer(maximum_bytes, checkpoint) as destination:
            try:
                resized.save(
                    destination,
                    format="JPEG",
                    quality=profile.jpeg_quality,
                    subsampling=profile.jpeg_subsampling,
                    optimize=False,
                    progressive=False,
                    exif=b"",
                    icc_profile=None,
                )
            except Exception:
                if not destination.overflow:
                    raise
            checkpoint()
            if destination.overflow:
                return None
            data = destination.getvalue()
        with io.BytesIO(data) as source, Image.open(source) as verify:
            if verify.format != "JPEG" or verify.mode != opened.mode or verify.size != target:
                reject_pdf_export(
                    PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "image_stage.encoded_image"
                )
            verify.load()
        checkpoint()
        return EncodedImage(data, hashlib.sha256(data).hexdigest(), geometry)
    finally:
        if resized is not opened:
            resized.close()

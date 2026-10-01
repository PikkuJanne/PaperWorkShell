"""Extract bounded PDF image buffers for the existing imaging adapter layer."""

from __future__ import annotations

import pikepdf

from paperworkshell.domain.pdf_image_stages import (
    PdfImageAssetAnalysis,
    PdfImageColorMode,
    PdfImageStageGeometry,
    PdfImageStageProfile,
)
from paperworkshell.imaging.pdf_image_encoder import EncodedImage as EncodedImage
from paperworkshell.imaging.pdf_image_encoder import encode_pdf_image_samples
from paperworkshell.imaging.pdf_image_encoder import required_image_memory as required_image_memory
from paperworkshell.pdf.export_adapter import PdfExportAdapterControl, _checkpoint
from paperworkshell.pdf.image_stage_analysis import (
    decoded_plain_stream,
    filter_name,
    raw_stream,
)


def encode_image(
    stream: pikepdf.Stream,
    asset: PdfImageAssetAnalysis,
    geometry: PdfImageStageGeometry,
    profile: PdfImageStageProfile,
    *,
    maximum_bytes: int,
    control: PdfExportAdapterControl,
) -> EncodedImage | None:
    """Return only a strictly smaller, reopened JPEG; None means no reduction."""

    _checkpoint(control)
    jpeg_encoded = filter_name(stream) == "/DCTDecode"
    channels = 3 if asset.color_mode is PdfImageColorMode.RGB else 1
    # Pass the bounded buffer directly so no caller reference keeps raw samples
    # live after the imaging helper creates its native raster core.
    return encode_pdf_image_samples(
        raw_stream(stream, asset.encoded_byte_size)
        if jpeg_encoded
        else decoded_plain_stream(
            stream,
            asset.width_pixels * asset.height_pixels * channels,
            control,
            maximum_encoded_bytes=asset.encoded_byte_size,
        ),
        jpeg_encoded=jpeg_encoded,
        asset=asset,
        geometry=geometry,
        profile=profile,
        maximum_bytes=maximum_bytes,
        checkpoint=lambda: _checkpoint(control),
    )

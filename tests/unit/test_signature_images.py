"""Pure signature-image artifact and straight-alpha profile tests."""

from __future__ import annotations

import hashlib
from uuid import UUID

import pytest

from paperworkshell.domain.image_pages import (
    ImageAlphaNormalizationPolicy,
    ImageExifOrientation,
    ImageNormalizationResult,
    ImagePageArtifactHeader,
    ImagePageError,
    ImagePageErrorCode,
    ImagePageMetadata,
    ImageQuarterTurn,
    ImageRasterBackground,
    ImageSourceColorModel,
    ImageSourceFormat,
    encode_image_page_artifact_header,
)
from paperworkshell.domain.signature_images import (
    SignatureImageError,
    SignatureImageErrorCode,
    SignatureImageMediaType,
    SignatureImageRasterArtifact,
    SignatureImageRasterArtifactHeader,
    SignatureImageRasterRequest,
    SignatureImageRasterResult,
    decode_signature_image_raster_artifact,
    encode_signature_image_raster_artifact_header,
)


def _uuid(number: int) -> UUID:
    return UUID(f"7d000000-0000-4000-8000-{number:012d}")


def _request() -> SignatureImageRasterRequest:
    return SignatureImageRasterRequest(
        case_id=_uuid(1),
        asset_id=_uuid(2),
        media_type=SignatureImageMediaType.PNG,
        source_byte_count=91,
        source_sha256="a" * 64,
        maximum_width_pixels=2,
        maximum_height_pixels=1,
    )


def _pixels() -> bytes:
    return bytes((0, 0, 0, 0, 10, 20, 30, 128))


def _result(*, embedded_profile: bool = False) -> SignatureImageRasterResult:
    pixels = _pixels()
    return SignatureImageRasterResult(
        source_format=ImageSourceFormat.PNG,
        source_byte_count=91,
        source_sha256="a" * 64,
        exif_orientation=ImageExifOrientation.NORMAL,
        encoded_width_pixels=2,
        encoded_height_pixels=1,
        oriented_width_pixels=2,
        oriented_height_pixels=1,
        width_pixels=2,
        height_pixels=1,
        row_stride_bytes=8,
        pixel_byte_count=8,
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
        source_has_alpha=True,
        source_had_embedded_color_profile=embedded_profile,
        source_had_exif=False,
        source_metadata_byte_count=0,
    )


def test_signature_artifact_round_trip_binds_correlation_and_exact_pixels() -> None:
    header = SignatureImageRasterArtifactHeader(_uuid(10), _uuid(11), _result())
    encoded = encode_signature_image_raster_artifact_header(header) + _pixels()

    artifact = decode_signature_image_raster_artifact(
        encoded,
        expected_operation_id=_uuid(10),
        expected_output_id=_uuid(11),
    )

    artifact.header.result.validate_for(_request())
    assert artifact.payload == _pixels()
    assert artifact.header.result.pixel_sha256 == hashlib.sha256(_pixels()).hexdigest()
    assert "a" * 64 not in repr(artifact)
    assert repr(_request()) == "SignatureImageRasterRequest(details_hidden=True)"


def test_artifact_rejects_hidden_rgb_and_embedded_profile_without_retaining_values() -> None:
    hidden = bytes((77, 88, 99, 0, 10, 20, 30, 128))
    result = _result()
    hidden_result = SignatureImageRasterResult(
        source_format=result.source_format,
        source_byte_count=result.source_byte_count,
        source_sha256=result.source_sha256,
        exif_orientation=result.exif_orientation,
        encoded_width_pixels=result.encoded_width_pixels,
        encoded_height_pixels=result.encoded_height_pixels,
        oriented_width_pixels=result.oriented_width_pixels,
        oriented_height_pixels=result.oriented_height_pixels,
        width_pixels=result.width_pixels,
        height_pixels=result.height_pixels,
        row_stride_bytes=result.row_stride_bytes,
        pixel_byte_count=len(hidden),
        pixel_sha256=hashlib.sha256(hidden).hexdigest(),
        source_has_alpha=True,
        source_had_embedded_color_profile=False,
        source_had_exif=False,
        source_metadata_byte_count=0,
    )

    with pytest.raises(SignatureImageError) as caught:
        SignatureImageRasterArtifact(
            SignatureImageRasterArtifactHeader(_uuid(10), _uuid(11), hidden_result),
            hidden,
        )

    assert caught.value.code is SignatureImageErrorCode.RESULT_INVALID
    assert caught.value.context == "signature.hidden_rgb"
    assert "77" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None

    with pytest.raises(SignatureImageError) as caught:
        _result(embedded_profile=True)
    assert caught.value.code is SignatureImageErrorCode.RESULT_INVALID


def test_preserved_alpha_uses_a_separate_artifact_profile() -> None:
    request = _request().image_request()
    pixels = _pixels()
    metadata = ImagePageMetadata(
        source_format=ImageSourceFormat.PNG,
        source_color_model=ImageSourceColorModel.RGBA,
        alpha_policy=ImageAlphaNormalizationPolicy.PRESERVE_STRAIGHT_ALPHA,
        exif_orientation=ImageExifOrientation.NORMAL,
        encoded_width_pixels=2,
        encoded_height_pixels=1,
        oriented_width_pixels=2,
        oriented_height_pixels=1,
        component_count=4,
        bits_per_component=8,
        frame_count=1,
        metadata_record_count=0,
        metadata_byte_count=0,
        has_alpha=True,
        has_embedded_color_profile=False,
        has_exif=False,
    )
    normalized = ImageNormalizationResult(
        metadata=metadata,
        additional_rotation=ImageQuarterTurn.DEGREES_0,
        width_pixels=2,
        height_pixels=1,
        row_stride_bytes=8,
        bytes_written=8,
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
        alpha_policy=ImageAlphaNormalizationPolicy.PRESERVE_STRAIGHT_ALPHA,
        background=ImageRasterBackground.PRESERVED_ALPHA,
    )

    signature = SignatureImageRasterResult.from_normalization(_request(), normalized)
    signature.validate_for(_request())
    assert request.alpha_policy is ImageAlphaNormalizationPolicy.PRESERVE_STRAIGHT_ALPHA

    with pytest.raises(ImagePageError) as caught:
        encode_image_page_artifact_header(ImagePageArtifactHeader(_uuid(10), _uuid(11), normalized))
    assert caught.value.code is ImagePageErrorCode.RESULT_INVALID
    assert caught.value.context == "image.header"


def test_invalid_expected_correlation_is_a_sanitized_model_failure() -> None:
    encoded = (
        encode_signature_image_raster_artifact_header(
            SignatureImageRasterArtifactHeader(_uuid(10), _uuid(11), _result())
        )
        + _pixels()
    )

    with pytest.raises(SignatureImageError) as caught:
        decode_signature_image_raster_artifact(
            encoded,
            expected_operation_id=object(),  # type: ignore[arg-type]
        )

    assert caught.value.code is SignatureImageErrorCode.MODEL_INVALID
    assert caught.value.context == "signature.correlation"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None

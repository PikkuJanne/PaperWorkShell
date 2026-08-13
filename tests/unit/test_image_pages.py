from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any, cast
from uuid import UUID

import pytest

from paperworkshell.domain.image_pages import (
    IMAGE_PAGE_ARTIFACT_HEADER_BYTES,
    MAX_IMAGE_DIMENSION_PIXELS,
    ImageAlphaNormalizationPolicy,
    ImageExifOrientation,
    ImageNormalizationLimits,
    ImageNormalizationRequest,
    ImageNormalizationResult,
    ImagePageArtifact,
    ImagePageArtifactHeader,
    ImagePageError,
    ImagePageErrorCode,
    ImagePageMetadata,
    ImagePagePreviewProgressPhase,
    ImagePagePreviewRequest,
    ImagePagePreviewResult,
    ImagePagePreviewSnapshot,
    ImageQuarterTurn,
    ImageSourceColorModel,
    ImageSourceFormat,
    decode_image_page_artifact,
    decode_image_page_artifact_header,
    encode_image_page_artifact,
    encode_image_page_artifact_header,
    image_page_preview_result_from_artifact,
    plan_image_normalization_layout,
    reject_image_page,
    validate_image_normalization_request,
    validate_image_normalization_result,
    validate_image_page_metadata,
)

OPERATION_ID = UUID("10000000-0000-4000-8000-000000000001")
OUTPUT_ID = UUID("20000000-0000-4000-8000-000000000001")
OTHER_ID = UUID("30000000-0000-4000-8000-000000000001")
CASE_ID = UUID("40000000-0000-4000-8000-000000000001")
PAGE_ID = UUID("50000000-0000-4000-8000-000000000001")
SOURCE_SHA256 = "a" * 64


def _metadata(**changes: object) -> ImagePageMetadata:
    orientation = cast(
        ImageExifOrientation,
        changes.get("exif_orientation", ImageExifOrientation.NORMAL),
    )
    oriented_width, oriented_height = (20, 40) if orientation.swaps_axes else (40, 20)
    values: dict[str, object] = {
        "source_format": ImageSourceFormat.JPEG,
        "source_color_model": ImageSourceColorModel.RGB,
        "alpha_policy": ImageAlphaNormalizationPolicy.COMPOSITE_ON_OPAQUE_WHITE,
        "exif_orientation": orientation,
        "encoded_width_pixels": 40,
        "encoded_height_pixels": 20,
        "oriented_width_pixels": oriented_width,
        "oriented_height_pixels": oriented_height,
        "component_count": 3,
        "bits_per_component": 8,
        "frame_count": 1,
        "metadata_record_count": 1,
        "metadata_byte_count": 32,
        "has_alpha": False,
        "has_embedded_color_profile": False,
        "has_exif": orientation is not ImageExifOrientation.NORMAL,
    }
    values.update(changes)
    return ImagePageMetadata(**values)  # type: ignore[arg-type]


def _request(**changes: object) -> ImageNormalizationRequest:
    values: dict[str, object] = {
        "expected_source_format": ImageSourceFormat.JPEG,
        "expected_source_bytes": 1_024,
        "expected_source_sha256": SOURCE_SHA256,
        "maximum_width_pixels": 30,
        "maximum_height_pixels": 30,
        "additional_rotation": ImageQuarterTurn.DEGREES_0,
    }
    values.update(changes)
    return ImageNormalizationRequest(**values)  # type: ignore[arg-type]


def _limits(**changes: object) -> ImageNormalizationLimits:
    values: dict[str, object] = {
        "maximum_source_bytes": 2_048,
        "maximum_encoded_width_pixels": 100,
        "maximum_encoded_height_pixels": 100,
        "maximum_source_pixels": 10_000,
        "maximum_decompressed_bytes": 100_000,
        "maximum_metadata_records": 10,
        "maximum_metadata_bytes": 1_024,
        "maximum_output_width_pixels": 100,
        "maximum_output_height_pixels": 100,
        "maximum_output_pixels": 10_000,
        "maximum_output_bytes": 40_000,
        "maximum_memory_bytes": 100_000,
        "wall_time_ms": 1_000,
    }
    values.update(changes)
    return ImageNormalizationLimits(**values)  # type: ignore[arg-type]


def _result(
    *,
    metadata: ImagePageMetadata | None = None,
    request: ImageNormalizationRequest | None = None,
    limits: ImageNormalizationLimits | None = None,
    pixels: bytes | None = None,
) -> tuple[ImageNormalizationResult, bytes]:
    selected_metadata = _metadata() if metadata is None else metadata
    selected_request = _request() if request is None else request
    selected_limits = _limits() if limits is None else limits
    layout = plan_image_normalization_layout(
        selected_metadata,
        selected_request,
        selected_limits,
    )
    selected_pixels = bytes((10, 20, 30, 255)) * layout.pixel_count if pixels is None else pixels
    result = ImageNormalizationResult(
        metadata=selected_metadata,
        additional_rotation=selected_request.additional_rotation,
        width_pixels=layout.width_pixels,
        height_pixels=layout.height_pixels,
        row_stride_bytes=layout.row_stride_bytes,
        bytes_written=len(selected_pixels),
        pixel_sha256=hashlib.sha256(selected_pixels).hexdigest(),
    )
    return result, selected_pixels


def _artifact() -> tuple[ImagePageArtifactHeader, bytes]:
    result, pixels = _result()
    return ImagePageArtifactHeader(OPERATION_ID, OUTPUT_ID, result), pixels


def _preview_request(**changes: object) -> ImagePagePreviewRequest:
    values: dict[str, object] = {
        "case_id": CASE_ID,
        "page_id": PAGE_ID,
        "maximum_width_pixels": 30,
        "maximum_height_pixels": 30,
        "additional_rotation": ImageQuarterTurn.DEGREES_0,
    }
    values.update(changes)
    return ImagePagePreviewRequest(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("orientation", tuple(ImageExifOrientation))
def test_all_exif_orientation_intents_preserve_expected_oriented_geometry(
    orientation: ImageExifOrientation,
) -> None:
    metadata = _metadata(
        exif_orientation=orientation,
        has_exif=orientation is not ImageExifOrientation.NORMAL,
    )

    expected = (20, 40) if orientation.swaps_axes else (40, 20)
    assert (metadata.oriented_width_pixels, metadata.oriented_height_pixels) == expected
    assert metadata.exif_orientation is orientation


def test_orientation_registry_distinguishes_all_four_reflections_and_transposes() -> None:
    mirrored = {orientation for orientation in ImageExifOrientation if orientation.is_mirrored}

    assert mirrored == {
        ImageExifOrientation.MIRROR_HORIZONTAL,
        ImageExifOrientation.MIRROR_VERTICAL,
        ImageExifOrientation.TRANSPOSE,
        ImageExifOrientation.TRANSVERSE,
    }


def test_image_preview_progress_phases_are_closed_content_free_tokens() -> None:
    assert tuple(phase.value for phase in ImagePagePreviewProgressPhase) == (
        "lookup",
        "generating",
        "validating",
        "complete",
    )


def test_fit_is_deterministic_after_exif_and_caller_quarter_turn() -> None:
    ordinary = plan_image_normalization_layout(_metadata(), _request(), _limits())
    rotated_request = _request(additional_rotation=ImageQuarterTurn.DEGREES_90)
    rotated = plan_image_normalization_layout(_metadata(), rotated_request, _limits())

    assert (ordinary.width_pixels, ordinary.height_pixels) == (30, 15)
    assert (rotated.width_pixels, rotated.height_pixels) == (15, 30)
    result, _ = _result(request=rotated_request)
    validate_image_normalization_result(rotated_request, _limits(), result)


@pytest.mark.parametrize(
    ("metadata_changes", "limit_changes", "exact_peak_bytes"),
    (
        ({}, {}, 162_500),
        (
            {"metadata_record_count": 10, "metadata_byte_count": 50_000},
            {"maximum_metadata_records": 10, "maximum_metadata_bytes": 50_000},
            162_500,
        ),
    ),
)
def test_memory_plan_has_exact_conservative_multibuffer_and_metadata_boundary(
    metadata_changes: dict[str, object],
    limit_changes: dict[str, object],
    exact_peak_bytes: int,
) -> None:
    metadata = _metadata(
        encoded_width_pixels=100,
        encoded_height_pixels=100,
        oriented_width_pixels=100,
        oriented_height_pixels=100,
        **metadata_changes,
    )
    request = _request(maximum_width_pixels=50, maximum_height_pixels=50)
    accepted_limits = _limits(
        maximum_memory_bytes=exact_peak_bytes,
        **limit_changes,
    )

    # Both cases charge three 40,000-byte decoded source rasters, the 10,000-byte
    # destination, and 25% headroom.  The metadata-heavy case proves metadata
    # remains covered without changing the dominant full-frame transform peak.
    layout = plan_image_normalization_layout(metadata, request, accepted_limits)
    assert layout.byte_count == 10_000

    with pytest.raises(ImagePageError) as raised:
        plan_image_normalization_layout(
            metadata,
            request,
            replace(accepted_limits, maximum_memory_bytes=exact_peak_bytes - 1),
        )

    assert raised.value.code is ImagePageErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "image.limit_memory"


@pytest.mark.parametrize(
    ("change", "context"),
    (
        ({"oriented_width_pixels": 39}, "image.metadata_orientation"),
        ({"component_count": 4}, "image.metadata_color"),
        ({"bits_per_component": 16}, "image.metadata_jpeg"),
        ({"frame_count": 2}, "image.metadata_frames"),
        ({"metadata_byte_count": 0}, "image.metadata_counts"),
        (
            {
                "exif_orientation": ImageExifOrientation.ROTATE_180,
                "has_exif": False,
            },
            "image.metadata_exif",
        ),
    ),
)
def test_metadata_rejects_inconsistent_geometry_color_and_safe_facts(
    change: dict[str, object], context: str
) -> None:
    with pytest.raises(ImagePageError) as raised:
        _metadata(**change)

    assert raised.value.context == context


def test_png_alpha_and_jpeg_cmyk_have_closed_predictable_metadata_models() -> None:
    png = _metadata(
        source_format=ImageSourceFormat.PNG,
        source_color_model=ImageSourceColorModel.RGBA,
        component_count=4,
        has_alpha=True,
    )
    cmyk = _metadata(
        source_color_model=ImageSourceColorModel.CMYK,
        component_count=4,
    )

    assert png.alpha_policy is ImageAlphaNormalizationPolicy.COMPOSITE_ON_OPAQUE_WHITE
    assert png.has_alpha is True
    assert cmyk.source_color_model is ImageSourceColorModel.CMYK


@pytest.mark.parametrize(
    ("color_model", "component_count", "bits"),
    (
        (ImageSourceColorModel.GRAYSCALE_TRANSPARENCY, 1, 2),
        (ImageSourceColorModel.RGB_TRANSPARENCY, 3, 8),
    ),
)
def test_png_transparency_keys_preserve_encoded_channels_and_semantic_alpha(
    color_model: ImageSourceColorModel,
    component_count: int,
    bits: int,
) -> None:
    metadata = _metadata(
        source_format=ImageSourceFormat.PNG,
        source_color_model=color_model,
        component_count=component_count,
        bits_per_component=bits,
        has_alpha=True,
    )

    assert metadata.component_count == component_count
    assert metadata.has_alpha is True


@pytest.mark.parametrize(
    ("factory", "change"),
    (
        (_request, {"expected_source_sha256": "A" * 64}),
        (_request, {"maximum_width_pixels": MAX_IMAGE_DIMENSION_PIXELS + 1}),
        (_limits, {"maximum_source_bytes": False}),
        (_limits, {"maximum_output_height_pixels": MAX_IMAGE_DIMENSION_PIXELS + 1}),
        (_limits, {"limits_version": 2}),
    ),
)
def test_request_and_limits_reject_invalid_geometry_digests_and_untyped_values(
    factory: Any, change: dict[str, object]
) -> None:
    with pytest.raises(ImagePageError) as raised:
        factory(**change)

    assert raised.value.code is ImagePageErrorCode.MODEL_INVALID


def test_independent_request_and_decoded_metadata_budgets_fail_closed() -> None:
    with pytest.raises(ImagePageError) as request_error:
        validate_image_normalization_request(_request(), _limits(maximum_source_bytes=1_000))
    with pytest.raises(ImagePageError) as metadata_error:
        validate_image_page_metadata(_metadata(), _limits(maximum_source_pixels=799))

    assert request_error.value.code is ImagePageErrorCode.LIMIT_EXCEEDED
    assert metadata_error.value.code is ImagePageErrorCode.LIMIT_EXCEEDED


def test_result_rejects_invalid_digest_and_request_binding() -> None:
    with pytest.raises(ImagePageError) as digest_error:
        result, _ = _result()
        replace(result, pixel_sha256="z" * 64)
    result, _ = _result()
    with pytest.raises(ImagePageError) as binding_error:
        validate_image_normalization_result(
            _request(additional_rotation=ImageQuarterTurn.DEGREES_180),
            _limits(),
            result,
        )

    assert digest_error.value.code is ImagePageErrorCode.RESULT_INVALID
    assert binding_error.value.code is ImagePageErrorCode.RESULT_INVALID
    assert SOURCE_SHA256 not in repr(result)
    assert result.pixel_sha256 not in repr(result)


def test_artifact_codec_is_fixed_canonical_and_round_trips_exactly() -> None:
    header, pixels = _artifact()
    encoded = encode_image_page_artifact(header, pixels)
    decoded = decode_image_page_artifact(
        encoded,
        expected_operation_id=OPERATION_ID,
        expected_output_id=OUTPUT_ID,
        expected_result=header.result,
    )

    assert len(encode_image_page_artifact_header(header)) == IMAGE_PAGE_ARTIFACT_HEADER_BYTES
    assert decode_image_page_artifact_header(encoded[:IMAGE_PAGE_ARTIFACT_HEADER_BYTES]) == header
    assert decoded == ImagePageArtifact(header, pixels)
    assert encode_image_page_artifact(decoded.header, decoded.payload) == encoded
    assert repr(pixels) not in repr(decoded)
    assert header.result.pixel_sha256 not in repr(decoded.header)


@pytest.mark.parametrize("change", ("truncate", "surplus", "digest", "version"))
def test_artifact_decoder_rejects_truncated_surplus_or_corrupt_bytes(change: str) -> None:
    header, pixels = _artifact()
    encoded = bytearray(encode_image_page_artifact(header, pixels))
    if change == "truncate":
        encoded.pop()
    elif change == "surplus":
        encoded.append(0)
    elif change == "digest":
        encoded[-4] ^= 0xFF
    else:
        encoded[8] ^= 0x01

    with pytest.raises(ImagePageError) as raised:
        decode_image_page_artifact(bytes(encoded))

    assert raised.value.code is ImagePageErrorCode.CACHE_INVALID


def test_artifact_codec_rejects_nonopaque_alpha_even_with_matching_digest() -> None:
    result, opaque = _result()
    nonopaque = bytearray(opaque)
    nonopaque[3] = 0
    changed_result = replace(
        result,
        pixel_sha256=hashlib.sha256(nonopaque).hexdigest(),
    )
    changed_header = ImagePageArtifactHeader(OPERATION_ID, OUTPUT_ID, changed_result)
    manually_framed = encode_image_page_artifact_header(changed_header) + bytes(nonopaque)

    with pytest.raises(ImagePageError) as encode_error:
        encode_image_page_artifact(changed_header, bytes(nonopaque))
    with pytest.raises(ImagePageError) as decode_error:
        decode_image_page_artifact(manually_framed)

    assert encode_error.value.context == "image.artifact_alpha"
    assert decode_error.value.context == "image.artifact_alpha"


def test_artifact_expected_correlations_reject_id_and_result_mismatch() -> None:
    header, pixels = _artifact()
    encoded = encode_image_page_artifact(header, pixels)
    changed_result = replace(header.result, additional_rotation=ImageQuarterTurn.DEGREES_180)

    for kwargs, context in (
        ({"expected_operation_id": OTHER_ID}, "image.artifact_operation"),
        ({"expected_output_id": OTHER_ID}, "image.artifact_output"),
        ({"expected_result": changed_result}, "image.artifact_result"),
    ):
        with pytest.raises(ImagePageError) as raised:
            decode_image_page_artifact(encoded, **cast(Any, kwargs))
        assert raised.value.context == context


def test_preview_helper_exposes_one_exact_path_free_opaque_snapshot() -> None:
    header, pixels = _artifact()
    preview = image_page_preview_result_from_artifact(
        _preview_request(),
        ImagePageArtifact(header, pixels),
    )

    assert isinstance(preview, ImagePagePreviewResult)
    assert preview.request == _preview_request()
    assert preview.snapshot.case_id == CASE_ID
    assert preview.snapshot.page_id == PAGE_ID
    assert preview.snapshot.pixels == pixels
    assert preview.snapshot.metadata == header.result.metadata
    assert (preview.snapshot.width_pixels, preview.snapshot.height_pixels) == (30, 15)
    assert not hasattr(preview, "cache_status")
    assert not hasattr(preview.request, "source_path")
    assert repr(pixels) not in repr(preview.snapshot)
    assert repr(pixels) not in repr(preview)


@pytest.mark.parametrize(
    ("change", "context"),
    (
        ({"case_id": UUID("40000000-0000-5000-8000-000000000001")}, "image.preview_request_case"),
        ({"page_id": CASE_ID}, "image.preview_request_ids"),
        ({"maximum_width_pixels": 0}, "image.preview_request_width"),
        ({"maximum_height_pixels": True}, "image.preview_request_height"),
        ({"additional_rotation": 90}, "image.preview_request"),
        ({"request_version": 2}, "image.preview_request"),
    ),
)
def test_preview_request_rejects_invalid_ids_bounds_and_versions(
    change: dict[str, object], context: str
) -> None:
    with pytest.raises(ImagePageError) as raised:
        _preview_request(**change)

    assert raised.value.code is ImagePageErrorCode.MODEL_INVALID
    assert raised.value.context == context


def test_preview_snapshot_rejects_misaligned_or_nonopaque_pixels() -> None:
    result, pixels = _result()
    values = {
        "case_id": CASE_ID,
        "page_id": PAGE_ID,
        "width_pixels": result.width_pixels,
        "height_pixels": result.height_pixels,
        "row_stride_bytes": result.row_stride_bytes,
        "pixels": pixels,
        "metadata": result.metadata,
        "additional_rotation": result.additional_rotation,
    }
    nonopaque = bytearray(pixels)
    nonopaque[3] = 0

    with pytest.raises(ImagePageError) as stride_error:
        ImagePagePreviewSnapshot(**(values | {"row_stride_bytes": 1}))  # type: ignore[arg-type]
    with pytest.raises(ImagePageError) as alpha_error:
        ImagePagePreviewSnapshot(**(values | {"pixels": bytes(nonopaque)}))  # type: ignore[arg-type]

    assert stride_error.value.context == "image.preview_snapshot_layout"
    assert alpha_error.value.context == "image.preview_snapshot_alpha"


def test_preview_result_rejects_identity_rotation_and_exact_fit_mismatch() -> None:
    header, pixels = _artifact()
    artifact = ImagePageArtifact(header, pixels)
    valid = image_page_preview_result_from_artifact(_preview_request(), artifact)
    other_page = UUID("60000000-0000-4000-8000-000000000001")
    wrong_identity = replace(valid.snapshot, page_id=other_page)
    smaller_pixels = bytes((1, 2, 3, 255)) * (29 * 15)
    wrong_geometry = replace(
        valid.snapshot,
        width_pixels=29,
        row_stride_bytes=29 * 4,
        pixels=smaller_pixels,
    )

    with pytest.raises(ImagePageError) as identity_error:
        ImagePagePreviewResult(valid.request, wrong_identity)
    with pytest.raises(ImagePageError) as geometry_error:
        ImagePagePreviewResult(valid.request, wrong_geometry)
    with pytest.raises(ImagePageError) as rotation_error:
        image_page_preview_result_from_artifact(
            _preview_request(additional_rotation=ImageQuarterTurn.DEGREES_90),
            artifact,
        )

    assert identity_error.value.context == "image.preview_result_binding"
    assert geometry_error.value.context == "image.preview_result_geometry"
    assert rotation_error.value.context == "image.preview_result_binding"


def test_errors_are_closed_sanitized_and_drop_source_exception_links() -> None:
    private_value = "private-name-and-path"
    try:
        raise RuntimeError(private_value)
    except RuntimeError:
        with pytest.raises(ImagePageError) as raised:
            reject_image_page(ImagePageErrorCode.MALFORMED, "image.decode")

    rendered = f"{raised.value!r} {raised.value}"
    assert private_value not in rendered
    assert raised.value.__context__ is None
    assert raised.value.__cause__ is None

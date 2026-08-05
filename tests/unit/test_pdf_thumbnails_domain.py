from __future__ import annotations

import hashlib
import traceback
from dataclasses import replace
from typing import Any, cast
from uuid import UUID

import pytest

from paperworkshell.domain.pdf_rendering import (
    MAX_PDF_RENDER_DIMENSION_PIXELS,
    PdfPageBox,
    PdfPageRenderResult,
    PdfRasterBackground,
    PdfRasterFormat,
    PdfRenderRotation,
)
from paperworkshell.domain.pdf_thumbnails import (
    PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES,
    PDF_THUMBNAIL_CACHE_KEY_BYTES,
    PdfThumbnailArtifact,
    PdfThumbnailCacheStatus,
    PdfThumbnailError,
    PdfThumbnailErrorCode,
    PdfThumbnailProgress,
    PdfThumbnailProgressPhase,
    PdfThumbnailRequest,
    PdfThumbnailResult,
    PdfThumbnailSnapshot,
    ResolvedPdfThumbnailRequest,
    decode_pdf_thumbnail_artifact,
    decode_pdf_thumbnail_artifact_header,
    decode_pdf_thumbnail_cache_key,
    encode_pdf_thumbnail_artifact,
    encode_pdf_thumbnail_artifact_header,
    pdf_thumbnail_header_from_render_result,
    pdf_thumbnail_result_from_artifact,
    reject_pdf_thumbnail,
)

CASE_ID = UUID("10000000-0000-4000-8000-000000000001")
SOURCE_ID = UUID("20000000-0000-4000-8000-000000000001")
PAGE_ID = UUID("30000000-0000-4000-8000-000000000001")
OPERATION_ID = UUID("40000000-0000-4000-8000-000000000001")
OTHER_OPERATION_ID = UUID("40000000-0000-4000-8000-000000000002")
CACHE_OBJECT_ID = UUID("50000000-0000-4000-8000-000000000001")
OTHER_CACHE_OBJECT_ID = UUID("50000000-0000-4000-8000-000000000002")
SOURCE_SHA256 = "a" * 64
PROFILE_SHA256 = "b" * 64


def _request(**changes: object) -> PdfThumbnailRequest:
    values: dict[str, object] = {
        "case_id": CASE_ID,
        "page_id": PAGE_ID,
        "maximum_width_pixels": 320,
        "maximum_height_pixels": 240,
        "additional_rotation": PdfRenderRotation.DEGREES_90,
    }
    values.update(changes)
    return PdfThumbnailRequest(**values)  # type: ignore[arg-type]


def _resolved(**changes: object) -> ResolvedPdfThumbnailRequest:
    values: dict[str, object] = {
        "request": _request(),
        "source_id": SOURCE_ID,
        "source_page_index": 2,
        "source_page_count": 4,
        "source_byte_size": 12_345,
        "source_sha256": SOURCE_SHA256,
        "renderer_profile_sha256": PROFILE_SHA256,
    }
    values.update(changes)
    return ResolvedPdfThumbnailRequest(**values)  # type: ignore[arg-type]


def _pixels() -> bytes:
    return bytes((10, 20, 30, 255)) * 8


def _render_result(**changes: object) -> PdfPageRenderResult:
    pixels = _pixels()
    values: dict[str, object] = {
        "page_index": 2,
        "width_pixels": 4,
        "height_pixels": 2,
        "row_stride_bytes": 16,
        "bytes_written": len(pixels),
        "pixel_sha256": hashlib.sha256(pixels).hexdigest(),
        "page_box": PdfPageBox.CROP,
        "intrinsic_rotation": PdfRenderRotation.DEGREES_270,
        "additional_rotation": PdfRenderRotation.DEGREES_90,
    }
    values.update(changes)
    return PdfPageRenderResult(**values)  # type: ignore[arg-type]


def _artifact() -> PdfThumbnailArtifact:
    header = pdf_thumbnail_header_from_render_result(
        OPERATION_ID,
        CACHE_OBJECT_ID,
        _resolved().cache_key,
        _render_result(),
    )
    return PdfThumbnailArtifact(header, _pixels())


def test_public_request_contains_only_internal_ids_and_bounded_render_intent() -> None:
    request = _request()

    assert request.case_id == CASE_ID
    assert request.page_id == PAGE_ID
    assert request.additional_rotation is PdfRenderRotation.DEGREES_90
    assert not hasattr(request, "source_path")
    assert not hasattr(request, "source_sha256")
    assert not hasattr(request, "renderer")


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("case_id", UUID("10000000-0000-5000-8000-000000000001")),
        ("page_id", CASE_ID),
        ("maximum_width_pixels", 0),
        ("maximum_width_pixels", MAX_PDF_RENDER_DIMENSION_PIXELS + 1),
        ("maximum_height_pixels", True),
        ("additional_rotation", 90),
        ("request_version", 2),
    ),
)
def test_request_rejects_invalid_or_untyped_values(field: str, value: object) -> None:
    with pytest.raises(PdfThumbnailError) as raised:
        _request(**{field: value})

    assert raised.value.code is PdfThumbnailErrorCode.MODEL_INVALID
    assert raised.value.context.startswith("thumbnail.request")


def test_resolved_request_hides_digests_and_produces_fixed_crop_render_request() -> None:
    resolved = _resolved()
    render = resolved.render_request

    assert render.page_index == 2
    assert render.page_box is PdfPageBox.CROP
    assert render.maximum_width_pixels == 320
    assert render.maximum_height_pixels == 240
    assert render.additional_rotation is PdfRenderRotation.DEGREES_90
    assert SOURCE_SHA256 not in repr(resolved)
    assert PROFILE_SHA256 not in repr(resolved)


@pytest.mark.parametrize(
    ("field", "value", "context"),
    (
        ("source_id", CASE_ID, "thumbnail.resolved_ids"),
        ("source_page_index", 4, "thumbnail.resolved_page"),
        ("source_page_count", 0, "thumbnail.resolved_page"),
        ("source_byte_size", False, "thumbnail.resolved_source_size"),
        ("source_sha256", "A" * 64, "thumbnail.resolved_source_digest"),
        ("renderer_profile_sha256", "x", "thumbnail.resolved_profile_digest"),
        ("resolved_version", 2, "thumbnail.resolved_version"),
    ),
)
def test_resolved_request_rejects_untrusted_source_facts(
    field: str, value: object, context: str
) -> None:
    with pytest.raises(PdfThumbnailError) as raised:
        _resolved(**{field: value})

    assert raised.value.code is PdfThumbnailErrorCode.MODEL_INVALID
    assert raised.value.context == context


def test_cache_key_is_fixed_width_deterministic_and_round_trips() -> None:
    key = _resolved().cache_key
    encoded = key.canonical_bytes()
    decoded = decode_pdf_thumbnail_cache_key(encoded)

    assert len(encoded) == PDF_THUMBNAIL_CACHE_KEY_BYTES
    assert decoded == key
    assert decoded.key_sha256 == hashlib.sha256(encoded).hexdigest()
    assert decoded.internal_name == f"{PAGE_ID}.{decoded.key_sha256}.pwsthumb"
    assert SOURCE_SHA256 not in repr(decoded)
    assert PROFILE_SHA256 not in repr(decoded)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("source_id", UUID("20000000-0000-4000-8000-000000000002")),
        ("page_id", UUID("30000000-0000-4000-8000-000000000002")),
        ("source_page_index", 1),
        ("source_byte_size", 12_346),
        ("source_sha256", "c" * 64),
        ("maximum_width_pixels", 321),
        ("maximum_height_pixels", 241),
        ("additional_rotation", PdfRenderRotation.DEGREES_180),
        ("renderer_profile_sha256", "d" * 64),
    ),
)
def test_each_semantic_cache_key_field_invalidates_the_key(field: str, value: object) -> None:
    key = _resolved().cache_key
    changed = replace(key, **cast(Any, {field: value}))

    assert changed.key_sha256 != key.key_sha256
    assert changed.canonical_bytes() != key.canonical_bytes()


@pytest.mark.parametrize(
    "payload",
    (
        b"",
        b"x" * (PDF_THUMBNAIL_CACHE_KEY_BYTES - 1),
        b"x" * (PDF_THUMBNAIL_CACHE_KEY_BYTES + 1),
    ),
)
def test_cache_key_decoder_rejects_wrong_length(payload: bytes) -> None:
    with pytest.raises(PdfThumbnailError) as raised:
        decode_pdf_thumbnail_cache_key(payload)

    assert raised.value.code is PdfThumbnailErrorCode.CACHE_INVALID


def test_cache_key_decoder_rejects_magic_version_profile_and_uuid_corruption() -> None:
    key = _resolved().cache_key
    offsets = (0, 8, 84, 18)

    for offset in offsets:
        payload = bytearray(key.canonical_bytes())
        payload[offset] ^= 0xFF
        with pytest.raises(PdfThumbnailError) as raised:
            decode_pdf_thumbnail_cache_key(bytes(payload))
        assert raised.value.code is PdfThumbnailErrorCode.CACHE_INVALID


def test_header_binds_exact_renderer_result_and_round_trips_without_digest_repr() -> None:
    key = _resolved().cache_key
    header = pdf_thumbnail_header_from_render_result(
        OPERATION_ID,
        CACHE_OBJECT_ID,
        key,
        _render_result(),
    )
    encoded = encode_pdf_thumbnail_artifact_header(header)
    decoded = decode_pdf_thumbnail_artifact_header(encoded)

    assert len(encoded) == PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES
    assert decoded == header
    assert decoded.cache_object_id == CACHE_OBJECT_ID
    assert decoded.cache_key == key
    assert decoded.payload_bytes == len(_pixels())
    assert SOURCE_SHA256 not in repr(decoded)
    assert PROFILE_SHA256 not in repr(decoded)
    assert header.pixel_sha256 not in repr(decoded)


def test_reusable_cache_key_is_independent_of_operation_staging_identity() -> None:
    key = _resolved().cache_key
    first = pdf_thumbnail_header_from_render_result(
        OPERATION_ID,
        CACHE_OBJECT_ID,
        key,
        _render_result(),
    )
    second = pdf_thumbnail_header_from_render_result(
        OTHER_OPERATION_ID,
        OTHER_CACHE_OBJECT_ID,
        key,
        _render_result(),
    )

    assert first.operation_id != second.operation_id
    assert first.cache_object_id != second.cache_object_id
    assert first.cache_key == second.cache_key
    assert first.cache_key.canonical_bytes() == second.cache_key.canonical_bytes()


@pytest.mark.parametrize(
    "cache_object_id",
    (
        OPERATION_ID,
        CASE_ID,
        SOURCE_ID,
        PAGE_ID,
        UUID("50000000-0000-5000-8000-000000000001"),
    ),
)
def test_header_rejects_non_v4_or_non_distinct_cache_object_id(
    cache_object_id: UUID,
) -> None:
    with pytest.raises(PdfThumbnailError) as raised:
        pdf_thumbnail_header_from_render_result(
            OPERATION_ID,
            cache_object_id,
            _resolved().cache_key,
            _render_result(),
        )

    assert raised.value.code is PdfThumbnailErrorCode.RESULT_INVALID


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("page_index", 1),
        ("page_box", PdfPageBox.MEDIA),
        ("additional_rotation", PdfRenderRotation.DEGREES_180),
    ),
)
def test_header_factory_rejects_renderer_result_that_does_not_match_key(
    field: str, value: object
) -> None:
    with pytest.raises(PdfThumbnailError) as raised:
        pdf_thumbnail_header_from_render_result(
            OPERATION_ID,
            CACHE_OBJECT_ID,
            _resolved().cache_key,
            _render_result(**{field: value}),
        )

    assert raised.value.code is PdfThumbnailErrorCode.RESULT_INVALID


def test_artifact_codec_round_trips_exact_fixed_layout_rgba() -> None:
    artifact = _artifact()
    encoded = encode_pdf_thumbnail_artifact(artifact.header, artifact.payload)
    decoded = decode_pdf_thumbnail_artifact(
        encoded,
        expected_key=artifact.header.cache_key,
        expected_operation_id=OPERATION_ID,
        expected_cache_object_id=CACHE_OBJECT_ID,
    )

    assert decoded == artifact
    assert decoded.payload == _pixels()
    assert _pixels().hex() not in repr(decoded)


@pytest.mark.parametrize("change", ("truncate", "surplus", "digest", "alpha"))
def test_artifact_decoder_rejects_partial_surplus_changed_or_nonopaque_payload(change: str) -> None:
    artifact = _artifact()
    encoded = bytearray(encode_pdf_thumbnail_artifact(artifact.header, artifact.payload))
    if change == "truncate":
        encoded.pop()
    elif change == "surplus":
        encoded.append(0)
    elif change == "digest":
        encoded[-4] ^= 0xFF
    else:
        encoded[-1] = 0

    with pytest.raises(PdfThumbnailError) as raised:
        decode_pdf_thumbnail_artifact(bytes(encoded))

    assert raised.value.code is PdfThumbnailErrorCode.CACHE_INVALID


def test_header_decoder_rejects_unknown_version_reserved_bytes_and_key_digest_mismatch() -> None:
    header = _artifact().header
    encoded = bytearray(encode_pdf_thumbnail_artifact_header(header))
    cache_object_version_offset = encoded.index(header.cache_object_id.bytes) + 6
    key_digest_offset = encoded.index(bytes.fromhex(header.cache_key.key_sha256))
    corruptions = (
        (8, 0x01),
        (12, 0x01),
        (cache_object_version_offset, 0xFF),
        (key_digest_offset, 0x01),
    )

    for offset, mask in corruptions:
        payload = bytearray(encoded)
        payload[offset] ^= mask
        with pytest.raises(PdfThumbnailError) as raised:
            decode_pdf_thumbnail_artifact_header(bytes(payload))
        assert raised.value.code is PdfThumbnailErrorCode.CACHE_INVALID


def test_artifact_expected_correlations_reject_wrong_values() -> None:
    artifact = _artifact()
    encoded = encode_pdf_thumbnail_artifact(artifact.header, artifact.payload)
    wrong_key = replace(
        artifact.header.cache_key,
        maximum_width_pixels=artifact.header.cache_key.maximum_width_pixels + 1,
    )

    with pytest.raises(PdfThumbnailError) as wrong_key_error:
        decode_pdf_thumbnail_artifact(encoded, expected_key=wrong_key)
    with pytest.raises(PdfThumbnailError) as wrong_operation_error:
        decode_pdf_thumbnail_artifact(encoded, expected_operation_id=OTHER_OPERATION_ID)
    with pytest.raises(PdfThumbnailError) as wrong_cache_object_error:
        decode_pdf_thumbnail_artifact(
            encoded,
            expected_cache_object_id=OTHER_CACHE_OBJECT_ID,
        )

    assert wrong_key_error.value.context == "thumbnail.artifact_key"
    assert wrong_operation_error.value.context == "thumbnail.artifact_operation"
    assert wrong_cache_object_error.value.context == "thumbnail.artifact_cache_object"


def test_snapshot_and_result_expose_only_validated_bounded_pixels() -> None:
    artifact = _artifact()
    result = pdf_thumbnail_result_from_artifact(
        _request(), artifact, PdfThumbnailCacheStatus.GENERATED
    )

    assert isinstance(result, PdfThumbnailResult)
    assert isinstance(result.snapshot, PdfThumbnailSnapshot)
    assert result.snapshot.pixels == _pixels()
    assert result.snapshot.pixel_format is PdfRasterFormat.RGBA8
    assert result.snapshot.background is PdfRasterBackground.OPAQUE_WHITE
    assert result.cache_status is PdfThumbnailCacheStatus.GENERATED
    assert _pixels().hex() not in repr(result)
    assert SOURCE_SHA256 not in repr(result)
    assert PROFILE_SHA256 not in repr(result)
    assert repr(_pixels()) not in repr(result.snapshot)


def test_snapshot_rejects_nonopaque_or_misaligned_pixels() -> None:
    artifact = _artifact()
    values = {
        "case_id": CASE_ID,
        "page_id": PAGE_ID,
        "width_pixels": 4,
        "height_pixels": 2,
        "row_stride_bytes": 16,
        "pixels": _pixels(),
        "intrinsic_rotation": PdfRenderRotation.DEGREES_270,
        "additional_rotation": PdfRenderRotation.DEGREES_90,
    }

    with pytest.raises(PdfThumbnailError) as wrong_length:
        PdfThumbnailSnapshot(**(values | {"pixels": artifact.payload[:-1]}))  # type: ignore[arg-type]
    changed = bytearray(artifact.payload)
    changed[3] = 0
    with pytest.raises(PdfThumbnailError) as wrong_alpha:
        PdfThumbnailSnapshot(**(values | {"pixels": bytes(changed)}))  # type: ignore[arg-type]

    assert wrong_length.value.context == "thumbnail.snapshot_layout"
    assert wrong_alpha.value.context == "thumbnail.snapshot_alpha"


def test_result_rejects_request_snapshot_or_cache_key_mismatch() -> None:
    result = pdf_thumbnail_result_from_artifact(
        _request(), _artifact(), PdfThumbnailCacheStatus.HIT
    )
    wrong_request = replace(result.request, page_id=UUID("30000000-0000-4000-8000-000000000002"))

    with pytest.raises(PdfThumbnailError) as raised:
        replace(result, request=wrong_request)

    assert raised.value.code is PdfThumbnailErrorCode.RESULT_INVALID
    assert raised.value.context == "thumbnail.result_binding"


def test_progress_is_bounded_and_complete_means_all_pages_finished() -> None:
    progress = PdfThumbnailProgress(
        OPERATION_ID,
        PdfThumbnailProgressPhase.RENDERING,
        2,
        4,
    )
    complete = replace(progress, phase=PdfThumbnailProgressPhase.COMPLETE, completed_pages=4)

    assert progress.completed_pages == 2
    assert complete.completed_pages == complete.total_pages


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("operation_id", UUID("40000000-0000-5000-8000-000000000001")),
        ("phase", "rendering"),
        ("completed_pages", -1),
        ("completed_pages", 5),
        ("total_pages", 0),
        ("progress_version", 2),
    ),
)
def test_progress_rejects_invalid_values(field: str, value: object) -> None:
    progress = PdfThumbnailProgress(
        OPERATION_ID,
        PdfThumbnailProgressPhase.RENDERING,
        1,
        4,
    )

    with pytest.raises(PdfThumbnailError) as raised:
        replace(progress, **cast(Any, {field: value}))

    assert raised.value.code is PdfThumbnailErrorCode.MODEL_INVALID


def test_incomplete_complete_progress_is_rejected() -> None:
    with pytest.raises(PdfThumbnailError) as raised:
        PdfThumbnailProgress(
            OPERATION_ID,
            PdfThumbnailProgressPhase.COMPLETE,
            3,
            4,
        )

    assert raised.value.context == "thumbnail.progress"


def test_error_sanitizes_values_and_discards_active_exception_chain() -> None:
    secret = "PRIVATE-PDF-PATH C:\\private\\applicant.pdf"
    invalid = PdfThumbnailError("bad", secret)  # type: ignore[arg-type]

    assert invalid.code is PdfThumbnailErrorCode.INTERNAL_ERROR
    assert invalid.context == "pdf.thumbnail"
    assert secret not in repr(invalid)
    assert secret not in str(invalid)

    try:
        raise OSError(secret)
    except OSError:
        with pytest.raises(PdfThumbnailError) as raised:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.CACHE_INVALID, "thumbnail.cache")

    rendered = "".join(traceback.format_exception(raised.value))
    assert raised.value.__context__ is None
    assert raised.value.__cause__ is None
    assert secret not in rendered

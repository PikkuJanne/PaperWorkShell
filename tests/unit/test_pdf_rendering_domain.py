from __future__ import annotations

import traceback
from dataclasses import replace
from typing import Any, cast

import pytest

from paperworkshell.domain.pdf_rendering import (
    MAX_PDF_RENDER_BYTES,
    MAX_PDF_RENDER_DIMENSION_PIXELS,
    MAX_PDF_RENDER_PAGES,
    MAX_PDF_RENDER_PIXELS,
    MAX_PDF_RENDER_WALL_TIME_MS,
    PDF_RENDER_RGBA8_BYTES_PER_PIXEL,
    PdfAnnotationMode,
    PdfPageBox,
    PdfPageRenderLimits,
    PdfPageRenderRequest,
    PdfPageRenderResult,
    PdfRasterBackground,
    PdfRasterFormat,
    PdfRasterLayout,
    PdfRenderError,
    PdfRenderErrorCode,
    PdfRenderRotation,
    checked_pdf_raster_layout,
    reject_pdf_rendering,
    validate_pdf_render_request,
)


def _limits(**changes: int) -> PdfPageRenderLimits:
    values = {
        "maximum_pages": 100,
        "maximum_width_pixels": 2_048,
        "maximum_height_pixels": 2_048,
        "maximum_pixels": 4_000_000,
        "maximum_bitmap_bytes": 16_000_000,
        "maximum_destination_bytes": 16_000_000,
        "maximum_memory_bytes": 64_000_000,
        "wall_time_ms": 10_000,
    }
    values.update(changes)
    return PdfPageRenderLimits(**values)


def _result(**changes: object) -> PdfPageRenderResult:
    values: dict[str, object] = {
        "page_index": 0,
        "width_pixels": 16,
        "height_pixels": 8,
        "row_stride_bytes": 64,
        "bytes_written": 512,
        "pixel_sha256": "a" * 64,
        "page_box": PdfPageBox.CROP,
        "intrinsic_rotation": PdfRenderRotation.DEGREES_90,
        "additional_rotation": PdfRenderRotation.DEGREES_270,
    }
    values.update(changes)
    return PdfPageRenderResult(**values)  # type: ignore[arg-type]


def test_render_request_is_zero_based_bounded_and_renderer_neutral() -> None:
    request = PdfPageRenderRequest(
        page_index=2,
        maximum_width_pixels=800,
        maximum_height_pixels=600,
        page_box=PdfPageBox.MEDIA,
        additional_rotation=PdfRenderRotation.DEGREES_90,
    )

    assert request.page_index == 2
    assert request.request_version == 1
    assert request.page_box is PdfPageBox.MEDIA
    assert request.additional_rotation is PdfRenderRotation.DEGREES_90


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("page_index", -1),
        ("page_index", MAX_PDF_RENDER_PAGES),
        ("page_index", True),
        ("maximum_width_pixels", 0),
        ("maximum_width_pixels", MAX_PDF_RENDER_DIMENSION_PIXELS + 1),
        ("maximum_height_pixels", False),
        ("page_box", "crop"),
        ("additional_rotation", 90),
        ("request_version", 2),
    ),
)
def test_render_request_rejects_invalid_or_untyped_values(field: str, value: object) -> None:
    request = PdfPageRenderRequest(0, 320, 240)

    with pytest.raises(PdfRenderError) as raised:
        replace(request, **cast(Any, {field: value}))

    assert raised.value.code is PdfRenderErrorCode.MODEL_INVALID
    assert raised.value.context == "render.request"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("maximum_pages", MAX_PDF_RENDER_PAGES + 1),
        ("maximum_width_pixels", MAX_PDF_RENDER_DIMENSION_PIXELS + 1),
        ("maximum_height_pixels", 0),
        ("maximum_pixels", MAX_PDF_RENDER_PIXELS + 1),
        ("maximum_bitmap_bytes", MAX_PDF_RENDER_BYTES + 1),
        ("maximum_destination_bytes", True),
        ("maximum_memory_bytes", MAX_PDF_RENDER_BYTES + 1),
        ("wall_time_ms", MAX_PDF_RENDER_WALL_TIME_MS + 1),
    ),
)
def test_render_limits_reject_invalid_values(field: str, value: object) -> None:
    limits = _limits()

    with pytest.raises(PdfRenderError) as raised:
        replace(limits, **cast(Any, {field: value}))

    assert raised.value.code is PdfRenderErrorCode.MODEL_INVALID
    assert raised.value.context == "render.limits"


@pytest.mark.parametrize(
    ("render_request", "limits", "expected_context"),
    (
        (PdfPageRenderRequest(1, 320, 200), _limits(maximum_pages=1), "render.pages"),
        (
            PdfPageRenderRequest(0, 321, 200),
            _limits(maximum_width_pixels=320),
            "render.dimensions",
        ),
        (
            PdfPageRenderRequest(0, 320, 201),
            _limits(maximum_height_pixels=200),
            "render.dimensions",
        ),
    ),
)
def test_request_is_checked_against_trusted_limits_before_pdf_open(
    render_request: PdfPageRenderRequest,
    limits: PdfPageRenderLimits,
    expected_context: str,
) -> None:
    with pytest.raises(PdfRenderError) as raised:
        validate_pdf_render_request(render_request, limits)

    assert raised.value.code is PdfRenderErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == expected_context


def test_request_within_trusted_limits_is_accepted() -> None:
    validate_pdf_render_request(PdfPageRenderRequest(0, 320, 200), _limits())


def test_checked_layout_proves_tightly_packed_rgba8_size() -> None:
    layout = checked_pdf_raster_layout(320, 200, _limits())

    assert layout == PdfRasterLayout(
        width_pixels=320,
        height_pixels=200,
        row_stride_bytes=320 * PDF_RENDER_RGBA8_BYTES_PER_PIXEL,
        pixel_count=64_000,
        byte_count=256_000,
    )


@pytest.mark.parametrize(
    ("limits", "expected_context"),
    (
        ({"maximum_width_pixels": 319}, "render.dimensions"),
        ({"maximum_height_pixels": 199}, "render.dimensions"),
        ({"maximum_pixels": 63_999}, "render.pixels"),
        ({"maximum_bitmap_bytes": 255_999}, "render.bitmap"),
        ({"maximum_destination_bytes": 255_999}, "render.destination"),
        ({"maximum_memory_bytes": 255_999}, "render.memory"),
    ),
)
def test_checked_layout_enforces_each_allocation_budget(
    limits: dict[str, int], expected_context: str
) -> None:
    with pytest.raises(PdfRenderError) as raised:
        checked_pdf_raster_layout(320, 200, _limits(**limits))

    assert raised.value.code is PdfRenderErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == expected_context


@pytest.mark.parametrize(("width", "height"), ((0, 1), (1, 0), (True, 1), (1, False)))
def test_checked_layout_rejects_invalid_dimensions(width: object, height: object) -> None:
    with pytest.raises(PdfRenderError) as raised:
        checked_pdf_raster_layout(width, height, _limits())  # type: ignore[arg-type]

    assert raised.value.code is PdfRenderErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "render.dimensions"


def test_render_result_contains_metadata_only_and_hides_pixel_digest() -> None:
    result = _result()

    assert result.pixel_format is PdfRasterFormat.RGBA8
    assert result.background is PdfRasterBackground.OPAQUE_WHITE
    assert result.annotations is PdfAnnotationMode.STORED_APPEARANCES
    assert result.bytes_written == result.row_stride_bytes * result.height_pixels
    assert "a" * 64 not in repr(result)
    assert not hasattr(result, "pixels")


@pytest.mark.parametrize(
    ("field", "value", "context"),
    (
        ("page_index", True, "render.result"),
        ("pixel_sha256", "A" * 64, "render.result"),
        ("pixel_format", "rgba8", "render.result"),
        ("background", "opaque_white", "render.result"),
        ("annotations", "stored_appearances", "render.result"),
        ("row_stride_bytes", 63, "render.result_layout"),
        ("bytes_written", 511, "render.result_layout"),
    ),
)
def test_render_result_rejects_invalid_metadata(field: str, value: object, context: str) -> None:
    with pytest.raises(PdfRenderError) as raised:
        _result(**{field: value})

    assert raised.value.code is PdfRenderErrorCode.RESULT_INVALID
    assert raised.value.context == context


def test_error_sanitizes_invalid_code_context_and_never_includes_document_values() -> None:
    secret = "PRIVATE-PDF-CONTENT"
    error = PdfRenderError("bad", secret)  # type: ignore[arg-type]

    assert error.code is PdfRenderErrorCode.INTERNAL_ERROR
    assert error.context == "pdf.rendering"
    assert secret not in repr(error)
    assert secret not in str(error)


def test_rejection_discards_active_raw_exception_chain_and_traceback_values() -> None:
    secret = "PRIVATE-PDF-PATH-CANARY C:\\private\\applicant.pdf"

    try:
        raise OSError(secret)
    except OSError:
        with pytest.raises(PdfRenderError) as raised:
            reject_pdf_rendering(PdfRenderErrorCode.SOURCE_INVALID, "render.source")

    rendered = "".join(traceback.format_exception(raised.value))
    assert raised.value.__context__ is None
    assert raised.value.__cause__ is None
    assert secret not in rendered

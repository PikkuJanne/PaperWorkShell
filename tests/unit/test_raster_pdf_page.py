"""Focused tests for the deterministic fixed-300-DPI raster page wrapper."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pikepdf
import pytest

from paperworkshell.domain.image_pages import ImageRgba8Layout
from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.pdf.raster_pdf_page import (
    RASTER_PDF_DPI,
    RasterPdfPageAdapter,
    RasterPdfPageGeometry,
    RasterPdfPageLimits,
)
from paperworkshell.pdf.text_presence import (
    PdfiumTextPresenceInspector,
    PdfTextPresenceLimits,
)


class _Control:
    def __init__(self, *, cancelled: bool = False) -> None:
        self.cancelled = cancelled

    def cancellation_requested(self) -> bool:
        return self.cancelled

    def deadline_expired(self) -> bool:
        return False


def _layout(width: int = 100, height: int = 100) -> ImageRgba8Layout:
    return ImageRgba8Layout(width, height, width * 4, width * height, width * height * 4)


def _limits(**changes: int) -> RasterPdfPageLimits:
    values = {
        "maximum_width_pixels": 100,
        "maximum_height_pixels": 100,
        "maximum_pixels": 10_000,
        "maximum_input_bytes": 40_000,
        "maximum_output_bytes": 1_000_000,
        "maximum_memory_bytes": 1_000_000,
        "maximum_pdf_objects": 1_000,
    }
    values.update(changes)
    return RasterPdfPageLimits(**values)


def _write_raster(path: Path, layout: ImageRgba8Layout, *, alpha: int = 255) -> None:
    payload = bytearray()
    for index in range(layout.pixel_count):
        payload.extend((index % 251, (index * 3) % 251, (index * 7) % 251, alpha))
    path.write_bytes(payload)


def test_wrapper_is_deterministic_fixed_300_dpi_and_strictly_reopens(tmp_path: Path) -> None:
    layout = _layout()
    source = tmp_path / "source.rgba"
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    _write_raster(source, layout)

    first_result = RasterPdfPageAdapter().build(
        source.resolve(), first.resolve(), layout, _limits(), _Control()
    )
    second_result = RasterPdfPageAdapter().build(
        source.resolve(), second.resolve(), layout, _limits(), _Control()
    )

    assert first.read_bytes() == second.read_bytes()
    assert (
        first_result.sha256
        == second_result.sha256
        == hashlib.sha256(first.read_bytes()).hexdigest()
    )
    assert first_result.dpi == RASTER_PDF_DPI
    with pikepdf.open(first, attempt_recovery=False, suppress_warnings=True) as document:
        assert len(document.pages) == 1
        assert not document.is_encrypted
        assert tuple(map(float, document.pages[0].MediaBox)) == (0.0, 0.0, 24.0, 24.0)
    fact = PdfiumTextPresenceInspector().inspect(
        first.resolve(),
        (0,),
        PdfTextPresenceLimits(1, 1_000_000, 100),
        _Control(),
    )[0]
    assert not fact.has_extractable_text


def test_explicit_physical_geometry_preserves_page_size_independent_of_raster_fit(
    tmp_path: Path,
) -> None:
    layout = _layout(width=100, height=50)
    source = tmp_path / "source.rgba"
    output = tmp_path / "physical.pdf"
    _write_raster(source, layout)

    result = RasterPdfPageAdapter().build(
        source.resolve(),
        output.resolve(),
        layout,
        _limits(),
        _Control(),
        geometry=RasterPdfPageGeometry(595.0, 842.0),
    )

    assert (result.width_pixels, result.height_pixels) == (100, 50)
    with pikepdf.open(output, attempt_recovery=False, suppress_warnings=True) as document:
        assert tuple(map(float, document.pages[0].MediaBox)) == (0.0, 0.0, 595.0, 842.0)


def test_wrapper_rejects_transparency_and_removes_candidate(tmp_path: Path) -> None:
    layout = _layout()
    source = tmp_path / "transparent.rgba"
    output = tmp_path / "candidate.pdf"
    _write_raster(source, layout, alpha=254)

    with pytest.raises(PdfExportError) as raised:
        RasterPdfPageAdapter().build(
            source.resolve(), output.resolve(), layout, _limits(), _Control()
        )

    assert raised.value.code is PdfExportErrorCode.SOURCE_UNSUPPORTED
    assert not output.exists()


def test_wrapper_enforces_memory_and_candidate_budgets(tmp_path: Path) -> None:
    layout = _layout()
    source = tmp_path / "source.rgba"
    _write_raster(source, layout)

    with pytest.raises(PdfExportError) as memory:
        RasterPdfPageAdapter().build(
            source.resolve(),
            (tmp_path / "memory.pdf").resolve(),
            layout,
            _limits(maximum_memory_bytes=1),
            _Control(),
        )
    with pytest.raises(PdfExportError) as output:
        RasterPdfPageAdapter().build(
            source.resolve(),
            (tmp_path / "output.pdf").resolve(),
            layout,
            _limits(maximum_output_bytes=10),
            _Control(),
        )

    assert memory.value.code is PdfExportErrorCode.LIMIT_EXCEEDED
    assert output.value.code is PdfExportErrorCode.LIMIT_EXCEEDED
    assert not (tmp_path / "memory.pdf").exists()
    assert not (tmp_path / "output.pdf").exists()


def test_existing_output_is_never_overwritten(tmp_path: Path) -> None:
    layout = _layout()
    source = tmp_path / "source.rgba"
    output = tmp_path / "existing.pdf"
    _write_raster(source, layout)
    output.write_bytes(b"preserve")

    with pytest.raises(PdfExportError) as raised:
        RasterPdfPageAdapter().build(
            source.resolve(), output.resolve(), layout, _limits(), _Control()
        )

    assert raised.value.code is PdfExportErrorCode.DESTINATION_EXISTS
    assert output.read_bytes() == b"preserve"


def test_cancellation_stops_before_candidate_creation(tmp_path: Path) -> None:
    layout = _layout()
    source = tmp_path / "source.rgba"
    output = tmp_path / "candidate.pdf"
    _write_raster(source, layout)

    with pytest.raises(PdfExportError) as raised:
        RasterPdfPageAdapter().build(
            source.resolve(), output.resolve(), layout, _limits(), _Control(cancelled=True)
        )

    assert raised.value.code is PdfExportErrorCode.CANCELLED
    assert not output.exists()

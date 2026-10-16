"""Bounded row mechanics and failure cleanup for private reconstruction."""

from __future__ import annotations

import errno
import hashlib
from collections.abc import Buffer
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pikepdf
import pytest

from paperworkshell.domain.image_pages import ImageRgba8Layout
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.domain.pdf_rendering import (
    PdfPageRenderLimits,
    PdfPageRenderRequest,
    PdfPageRenderResult,
)
from paperworkshell.domain.redaction import RedactionMark
from paperworkshell.domain.redaction_reconstruction import RedactionReconstructionRequest
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.pdf.pdfium_renderer import PdfiumPageRenderer
from paperworkshell.pdf.raster_pdf_page import (
    RasterPdfPageAdapter,
    RasterPdfPageControl,
    RasterPdfPageGeometry,
    RasterPdfPageLimits,
    RasterPdfPageResult,
    _BoundedOutput,
)
from paperworkshell.pdf.redaction_reconstruction import (
    RedactionReconstructionAdapter,
    RedactionReconstructionLimits,
    _pixel_box,
    _SanitizingSink,
    _transform_sanitized,
)
from paperworkshell.pdf.renderer import PdfRasterDestination, PdfRenderControl, PdfRenderSource


class _Control:
    def __init__(self, *, cancel_at: int = 0, expired: bool = False) -> None:
        self.calls, self.cancel_at, self.expired = 0, cancel_at, expired

    def cancellation_requested(self) -> bool:
        self.calls += 1
        return self.cancel_at != 0 and self.calls >= self.cancel_at

    def deadline_expired(self) -> bool:
        return self.expired


def _source() -> bytes:
    with pikepdf.Pdf.new() as document:
        page = document.add_blank_page(page_size=(72, 48))
        page.Contents = pikepdf.Stream(document, b"0 0 1 rg 0 0 72 48 re f\n")
        result = BytesIO()
        document.save(result, deterministic_id=True)
        return result.getvalue()


def _request(content: bytes) -> RedactionReconstructionRequest:
    page_id = uuid4()
    return RedactionReconstructionRequest(
        case_id=uuid4(),
        manifest_revision=1,
        source_id=uuid4(),
        page_id=page_id,
        source_sha256=hashlib.sha256(content).hexdigest(),
        source_byte_size=len(content),
        source_page_index=0,
        source_page_count=1,
        marks=(
            RedactionMark(uuid4(), page_id, NormalizedPageBox(200_000, 200_000, 300_000, 300_000)),
        ),
    )


@pytest.mark.parametrize("rotation", [0, 1, 2, 3])
def test_row_transform_uses_exact_pixels_for_every_quarter_turn(
    tmp_path: Path, rotation: int
) -> None:
    width, height = 7, 5
    pixels = [(index, index + 1, index + 2, 255) for index in range(width * height)]
    source = tmp_path / "sanitized.rgba"
    source.write_bytes(bytes(value for pixel in pixels for value in pixel))
    target = tmp_path / "transformed.rgba"
    crop = (1, 1, 6, 4)
    rows = [[pixels[y * width + x] for x in range(1, 6)] for y in range(1, 4)]
    for _ in range(rotation):
        rows = list(map(list, zip(*rows[::-1], strict=True)))
    expected = bytes(value for row in rows for pixel in row for value in pixel)
    expected_rgb = bytes(value for row in rows for pixel in row for value in pixel[:3])

    layout, rgba_hash, rgb_hash = _transform_sanitized(
        source, target, width, height, crop, rotation, _Control()
    )

    assert target.read_bytes() == expected
    assert layout.width_pixels == len(rows[0])
    assert layout.height_pixels == len(rows)
    assert rgba_hash == hashlib.sha256(expected).hexdigest()
    assert rgb_hash == hashlib.sha256(expected_rgb).hexdigest()


def test_mask_rounding_is_outward_and_extra_pixel_is_clamped() -> None:
    assert _pixel_box(NormalizedPageBox(1, 1, 1, 1), 300, 200, margin=1) == (0, 0, 2, 2)
    assert _pixel_box(NormalizedPageBox.full_page(), 300, 200, margin=1) == (0, 0, 300, 200)
    assert _pixel_box(
        NormalizedPageBox(100_000, 200_000, 300_000, 400_000), 300, 200, margin=0
    ) == (30, 40, 120, 120)


def test_private_reconstruction_reopens_renders_and_leaves_only_candidate(tmp_path: Path) -> None:
    content = _source()
    request = _request(content)
    output = tmp_path / "candidate.pdf"
    stream = BytesIO(content)
    stream.seek(9)

    report = RedactionReconstructionAdapter().reconstruct(
        stream,
        output,
        tmp_path,
        request,
        RedactionReconstructionLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1),
        _Control(),
    )

    assert report.status == "awaiting_verification"
    assert report.unsigned_derivative
    assert (report.width_pixels, report.height_pixels) == (300, 200)
    assert (report.width_points, report.height_points) == (72.0, 48.0)
    assert stream.tell() == 9
    assert stream.getvalue() == content
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.parametrize("failure", ["cancel", "deadline", "hash", "raster", "temp", "output"])
def test_failure_is_closed_preserves_source_and_cleans_scratch(
    tmp_path: Path, failure: str
) -> None:
    content = _source()
    request = _request(content)
    limits = RedactionReconstructionLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1)
    control = _Control()
    if failure == "cancel":
        control = _Control(cancel_at=45)
    elif failure == "deadline":
        control = _Control(expired=True)
    elif failure == "hash":
        request = replace(request, source_sha256="0" * 64)
    elif failure == "raster":
        limits = replace(limits, maximum_raster_bytes=16)
    elif failure == "temp":
        limits = replace(limits, maximum_temporary_storage_bytes=16)
    else:
        limits = replace(limits, maximum_output_bytes=16)
    source = BytesIO(content)
    output = tmp_path / "candidate.pdf"

    with pytest.raises(PdfExportError):
        RedactionReconstructionAdapter().reconstruct(
            source, output, tmp_path, request, limits, control
        )

    assert source.getvalue() == content
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("write_stage", ["sanitized_raster", "new_pdf"])
def test_disk_full_discards_candidate_preserves_source_and_allows_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, write_stage: str
) -> None:
    content = _source()
    source = BytesIO(content)
    request = _request(content)
    limits = RedactionReconstructionLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1)
    output = tmp_path / "candidate.pdf"

    def full_disk(_self: object, _data: Buffer, /) -> int:
        raise OSError(errno.ENOSPC, "prohibited-fixture-path-and-value")

    with monkeypatch.context() as patch:
        patch.setattr(
            _SanitizingSink if write_stage == "sanitized_raster" else _BoundedOutput,
            "write",
            full_disk,
        )
        with pytest.raises(PdfExportError) as caught:
            RedactionReconstructionAdapter().reconstruct(
                source, output, tmp_path, request, limits, _Control()
            )
    assert "prohibited-fixture" not in str(caught.value)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert not list(tmp_path.iterdir())
    assert source.getvalue() == content

    result = RedactionReconstructionAdapter().reconstruct(
        source, output, tmp_path, request, limits, _Control()
    )
    assert result.status == "awaiting_verification"
    assert source.getvalue() == content
    assert list(tmp_path.iterdir()) == [output]


def test_existing_destination_is_preserved(tmp_path: Path) -> None:
    content = _source()
    output = tmp_path / "candidate.pdf"
    output.write_bytes(b"previous-good-output")
    with pytest.raises(PdfExportError) as raised:
        RedactionReconstructionAdapter().reconstruct(
            BytesIO(content),
            output,
            tmp_path,
            _request(content),
            RedactionReconstructionLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1),
            _Control(),
        )
    assert raised.value.code is PdfExportErrorCode.DESTINATION_EXISTS
    assert output.read_bytes() == b"previous-good-output"


def test_cancel_during_last_report_checkpoint_still_removes_candidate(tmp_path: Path) -> None:
    content = _source()
    request = _request(content)
    limits = RedactionReconstructionLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1)
    successful = _Control()
    output = tmp_path / "candidate.pdf"
    RedactionReconstructionAdapter().reconstruct(
        BytesIO(content), output, tmp_path, request, limits, successful
    )
    output.unlink()

    with pytest.raises(PdfExportError) as raised:
        RedactionReconstructionAdapter().reconstruct(
            BytesIO(content),
            output,
            tmp_path,
            request,
            limits,
            _Control(cancel_at=successful.calls),
        )

    assert raised.value.code is PdfExportErrorCode.CANCELLED
    assert not list(tmp_path.iterdir())


def test_raced_destination_is_never_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = RasterPdfPageAdapter.build

    def race(
        self: RasterPdfPageAdapter,
        rgba_path: Path,
        output_path: Path,
        layout: ImageRgba8Layout,
        limits: RasterPdfPageLimits,
        control: RasterPdfPageControl,
        *,
        geometry: RasterPdfPageGeometry | None = None,
    ) -> RasterPdfPageResult:
        output_path.write_bytes(b"previous-output-created-during-render")
        return original(self, rgba_path, output_path, layout, limits, control, geometry=geometry)

    monkeypatch.setattr(RasterPdfPageAdapter, "build", race)
    content = _source()
    output = tmp_path / "candidate.pdf"
    with pytest.raises(PdfExportError) as raised:
        RedactionReconstructionAdapter().reconstruct(
            BytesIO(content),
            output,
            tmp_path,
            _request(content),
            RedactionReconstructionLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1),
            _Control(),
        )

    assert raised.value.code is PdfExportErrorCode.DESTINATION_EXISTS
    assert output.read_bytes() == b"previous-output-created-during-render"
    assert list(tmp_path.iterdir()) == [output]


def test_independent_final_renderer_pixel_mismatch_removes_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = PdfiumPageRenderer.render
    calls = 0

    def corrupt(
        self: PdfiumPageRenderer,
        source: PdfRenderSource,
        destination: PdfRasterDestination,
        request: PdfPageRenderRequest,
        limits: PdfPageRenderLimits,
        control: PdfRenderControl,
    ) -> PdfPageRenderResult:
        nonlocal calls
        result = original(self, source, destination, request, limits, control)
        calls += 1
        return replace(result, pixel_sha256="0" * 64) if calls == 2 else result

    monkeypatch.setattr(PdfiumPageRenderer, "render", corrupt)
    content = _source()
    with pytest.raises(PdfExportError) as raised:
        RedactionReconstructionAdapter().reconstruct(
            BytesIO(content),
            tmp_path / "candidate.pdf",
            tmp_path,
            _request(content),
            RedactionReconstructionLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1),
            _Control(),
        )
    assert raised.value.code is PdfExportErrorCode.VERIFICATION_RENDER_FAILED
    assert not list(tmp_path.iterdir())


def test_source_inspection_io_error_is_sanitized(tmp_path: Path) -> None:
    class BrokenPosition(BytesIO):
        calls = 0

        def tell(self) -> int:
            self.calls += 1
            if self.calls == 2:
                raise OSError("prohibited-native-path-and-value")
            return super().tell()

    content = _source()
    with pytest.raises(PdfExportError) as raised:
        RedactionReconstructionAdapter().reconstruct(
            BrokenPosition(content),
            tmp_path / "candidate.pdf",
            tmp_path,
            _request(content),
            RedactionReconstructionLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1),
            _Control(),
        )
    assert "prohibited-native" not in str(raised.value)
    assert raised.value.__context__ is None
    assert raised.value.__cause__ is None
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("user_unit", [0.5, 2.0])
def test_user_unit_preserves_physical_page_size_at_reconstruction_resolution(
    tmp_path: Path, user_unit: float
) -> None:
    with pikepdf.Pdf.open(BytesIO(_source()), attempt_recovery=False) as document:
        document.pages[0].obj.UserUnit = user_unit
        encoded = BytesIO()
        document.save(encoded, min_version="1.6", deterministic_id=True)
    content = encoded.getvalue()
    result = RedactionReconstructionAdapter().reconstruct(
        BytesIO(content),
        tmp_path / "candidate.pdf",
        tmp_path,
        _request(content),
        RedactionReconstructionLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1),
        _Control(),
    )
    assert result.width_points == pytest.approx(72.0 * user_unit)
    assert result.height_points == pytest.approx(48.0 * user_unit)
    assert result.width_pixels == round(300 * user_unit)
    assert result.height_pixels == round(200 * user_unit)

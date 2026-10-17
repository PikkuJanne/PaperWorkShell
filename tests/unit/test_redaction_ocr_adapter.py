"""Private masked-input OCR, spatial rejection and atomic failure behavior."""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pikepdf
import pypdfium2 as pdfium  # type: ignore[import-untyped]
import pytest

from paperworkshell.domain.ocr import OcrError, OcrErrorCode
from paperworkshell.domain.page_coordinates import ClockwiseRotation, NormalizedPageBox
from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.domain.redaction import RedactionMark
from paperworkshell.domain.redaction_ocr import RedactionOcrRequest
from paperworkshell.domain.redaction_reconstruction import RedactionReconstructionRequest
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.ocr.ocrmypdf_adapter import OcrMyPdfAdapter
from paperworkshell.pdf.redaction_ocr import RedactionOcrAdapter, _exclusion_boxes
from paperworkshell.pdf.redaction_reconstruction import (
    RedactionReconstructionAdapter,
    RedactionReconstructionLimits,
)


class _Control:
    def __init__(self) -> None:
        self.cancelled = self.expired = False

    def cancellation_requested(self) -> bool:
        return self.cancelled

    def deadline_expired(self) -> bool:
        return self.expired


def _source() -> bytes:
    with pikepdf.Pdf.new() as document:
        page = document.add_blank_page(page_size=(72, 48))
        page.Resources = pikepdf.Dictionary(
            Font=pikepdf.Dictionary(
                F1=pikepdf.Dictionary(
                    Type=pikepdf.Name.Font,
                    Subtype=pikepdf.Name.Type1,
                    BaseFont=pikepdf.Name.Helvetica,
                )
            )
        )
        page.Contents = pikepdf.Stream(
            document,
            b"BT /F1 3 Tf 20 30 Td (ORIGINAL_SECRET_CANARY) Tj ET\n"
            b"BT /F1 3 Tf 3 Tr 20 30 Td (HIDDEN_OCR_SECRET) Tj ET\n",
        )
        output = BytesIO()
        document.save(output)
        return output.getvalue()


def _request(content: bytes) -> RedactionOcrRequest:
    page = uuid4()
    return RedactionOcrRequest(
        RedactionReconstructionRequest(
            case_id=uuid4(),
            manifest_revision=1,
            source_id=uuid4(),
            page_id=page,
            source_sha256=hashlib.sha256(content).hexdigest(),
            source_byte_size=len(content),
            source_page_index=0,
            source_page_count=1,
            marks=(
                RedactionMark(uuid4(), page, NormalizedPageBox(200_000, 200_000, 600_000, 300_000)),
            ),
        )
    )


def _limits() -> RedactionReconstructionLimits:
    return RedactionReconstructionLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1)


def _text(content: bytes) -> str:
    document = pdfium.PdfDocument(content)
    try:
        page = document[0]
        try:
            text = page.get_textpage()
            try:
                return cast(str, text.get_text_range())
            finally:
                text.close()
        finally:
            page.close()
    finally:
        document.close()


class _Boundary:
    def __init__(self, failure: str = "", source: BytesIO | None = None) -> None:
        self.failure, self.source = failure, source
        self.calls = 0
        self.input_payload = b""

    def process(self, source_path: Path, output_path: Path, **kwargs: Any) -> tuple[str, str]:
        self.calls += 1
        assert kwargs["total_pages"] == 1
        assert kwargs["languages"] in {("eng",), ("deu",), ("eng", "deu")}
        self.input_payload = source_path.read_bytes()
        # OCR receives the fresh single-image page, never original text/objects.
        assert _text(self.input_payload) == ""
        assert b"ORIGINAL_SECRET_CANARY" not in self.input_payload
        assert b"HIDDEN_OCR_SECRET" not in self.input_payload
        if self.failure in {"cancel", "timeout", "language", "engine", "limit"}:
            code = {
                "cancel": OcrErrorCode.CANCELLED,
                "timeout": OcrErrorCode.TIMED_OUT,
                "language": OcrErrorCode.LANGUAGE_UNAVAILABLE,
                "engine": OcrErrorCode.ENGINE_FAILED,
                "limit": OcrErrorCode.LIMIT_EXCEEDED,
            }[self.failure]
            # Native implementations sometimes leave nested private work at failure.
            nested = output_path.parent / "leftover"
            nested.mkdir()
            (nested / "private.txt").write_bytes(b"PROHIBITED_NATIVE_EXCEPTION_CANARY")
            raise OcrError(code, "ocr.process")
        if self.failure == "original":
            assert self.source is not None
            output_path.write_bytes(self.source.getvalue())
            return "17.10.0", "5.5.3"
        with pikepdf.Pdf.open(source_path) as document:
            page = document.pages[0]
            page.Resources.Font = pikepdf.Dictionary(
                F1=pikepdf.Dictionary(
                    Type=pikepdf.Name.Font,
                    Subtype=pikepdf.Name.Type1,
                    BaseFont=pikepdf.Name.Helvetica,
                )
            )
            text = b"BT /F1 3 Tf 3 Tr 2 5 Td (RETAINED SEARCHABLE) Tj ET\n"
            if self.failure == "overlap":
                text = b"BT /F1 3 Tf 3 Tr 20 30 Td (RECREATED) Tj ET\n"
            if self.failure == "visible":
                text = b"BT /F1 8 Tf 0 Tr 2 5 Td (CHANGED) Tj ET\n"
            page.contents_add(pikepdf.Stream(document, text))
            if self.failure == "image":
                image = page.Resources.XObject.Im0
                pixels = bytearray(image.read_bytes())
                pixels[:3] = b"\xff\x00\x00"
                image.write(bytes(pixels))
            if self.failure == "geometry":
                page.MediaBox = [0, 0, 73, 48]
            document.save(output_path)
        if self.failure == "incremental":
            payload = output_path.read_bytes()
            with pikepdf.Pdf.open(output_path) as saved:
                object_number, root = int(saved.trailer.Size), saved.Root.objgen
            previous = int(re.findall(rb"startxref\s+(\d+)", payload)[-1])
            added = f"\n{object_number} 0 obj\n<< /Producer (Synthetic generated OCR revision) >>\nendobj\n".encode(
                "ascii"
            )
            offset = len(payload) + len(added)
            added += (
                f"xref\n{object_number} 1\n{len(payload) + 1:010} 00000 n \n"
                f"trailer\n<< /Size {object_number + 1} /Root {root[0]} {root[1]} R /Info {object_number} 0 R /Prev {previous} >>\n"
                f"startxref\n{offset}\n%%EOF\n"
            ).encode("ascii")
            output_path.write_bytes(payload + added)
            with pikepdf.Pdf.open(output_path, attempt_recovery=False) as saved:
                assert "/Prev" in saved.trailer
        if self.failure == "masked_changed":
            source_path.write_bytes(b"changed")
        if self.failure == "source_changed":
            assert self.source is not None
            self.source.seek(0)
            self.source.write(b"MUTATED")
        return "17.10.0", "5.5.3"


def _adapter(boundary: _Boundary, tmp_path: Path) -> RedactionOcrAdapter:
    return RedactionOcrAdapter(
        OcrMyPdfAdapter(
            tesseract_executable=tmp_path / "tesseract.exe",
            tessdata_directory=tmp_path,
            boundary=boundary,
        )
    )


def test_private_masked_ocr_retains_searchability_and_unverified_state(tmp_path: Path) -> None:
    content, boundary = _source(), _Boundary()
    source = BytesIO(content)
    source.seek(7)
    request = _request(content)
    output = tmp_path / "candidate.pdf"
    result = _adapter(boundary, tmp_path).regenerate(
        source, output, tmp_path, request, _limits(), _Control()
    )
    assert result.status == "awaiting_verification" and result.unsigned_derivative
    assert result.reconstruction.source_sha256 == hashlib.sha256(content).hexdigest()
    assert result.output_sha256 == hashlib.sha256(output.read_bytes()).hexdigest()
    assert result.output_byte_size == output.stat().st_size
    assert boundary.calls == 1
    assert "RETAINED SEARCHABLE" in _text(output.read_bytes())
    assert "ORIGINAL_SECRET_CANARY" not in _text(output.read_bytes())
    assert "HIDDEN_OCR_SECRET" not in _text(output.read_bytes())
    assert source.getvalue() == content and source.tell() == 7
    assert list(tmp_path.iterdir()) == [output]


def test_generated_incremental_ocr_is_fully_rewritten_without_source_history(
    tmp_path: Path,
) -> None:
    content, boundary = _source(), _Boundary("incremental")
    output = tmp_path / "candidate.pdf"
    report = _adapter(boundary, tmp_path).regenerate(
        BytesIO(content), output, tmp_path, _request(content), _limits(), _Control()
    )
    candidate = output.read_bytes()
    assert report.status == "awaiting_verification"
    assert candidate.count(b"%%EOF") == 1
    assert "RETAINED SEARCHABLE" in _text(candidate)
    assert "ORIGINAL_SECRET_CANARY" not in _text(candidate)
    assert "HIDDEN_OCR_SECRET" not in _text(candidate)
    with pikepdf.Pdf.open(output, attempt_recovery=False) as saved:
        assert "/Prev" not in saved.trailer
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.parametrize(
    "failure",
    [
        "cancel",
        "timeout",
        "language",
        "engine",
        "limit",
        "image",
        "geometry",
        "original",
        "overlap",
        "visible",
        "masked_changed",
        "source_changed",
    ],
)
def test_ocr_failure_discards_output_and_every_private_intermediate(
    tmp_path: Path, failure: str
) -> None:
    content = _source()
    source = BytesIO(content)
    boundary = _Boundary(failure, source)
    output = tmp_path / "candidate.pdf"
    with pytest.raises(PdfExportError) as caught:
        _adapter(boundary, tmp_path).regenerate(
            source, output, tmp_path, _request(content), _limits(), _Control()
        )
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert "CANARY" not in str(caught.value)
    if failure in {"cancel", "timeout", "limit", "language"}:
        assert (
            caught.value.code
            is {
                "cancel": PdfExportErrorCode.CANCELLED,
                "timeout": PdfExportErrorCode.TIMED_OUT,
                "limit": PdfExportErrorCode.LIMIT_EXCEEDED,
                "language": PdfExportErrorCode.OCR_LANGUAGE_UNAVAILABLE,
            }[failure]
        )
    if failure != "source_changed":
        assert source.getvalue() == content
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("failure", ["existing", "hash", "low_temp", "cancel", "timeout"])
def test_preflight_refusal_never_calls_ocr(tmp_path: Path, failure: str) -> None:
    content, boundary = _source(), _Boundary()
    request, limits, control = _request(content), _limits(), _Control()
    output = tmp_path / "candidate.pdf"
    if failure == "existing":
        output.write_bytes(b"previous-good-candidate")
    elif failure == "hash":
        request = replace(
            request, reconstruction=replace(request.reconstruction, source_sha256="0" * 64)
        )
    elif failure == "low_temp":
        limits = replace(limits, maximum_temporary_storage_bytes=1)
    elif failure == "cancel":
        control.cancelled = True
    else:
        control.expired = True
    with pytest.raises(PdfExportError):
        _adapter(boundary, tmp_path).regenerate(
            BytesIO(content), output, tmp_path, request, limits, control
        )
    assert boundary.calls == 0
    if failure == "existing":
        assert output.read_bytes() == b"previous-good-candidate"
    else:
        assert list(tmp_path.iterdir()) == []


def test_failed_private_directory_creation_preserves_preexisting_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = uuid4()
    private = tmp_path / selected.hex
    private.mkdir()
    retained = private / "existing-private-data"
    retained.write_bytes(b"preserve-existing-data")
    monkeypatch.setattr("paperworkshell.pdf.redaction_ocr.uuid4", lambda: selected)
    content, boundary = _source(), _Boundary()
    with pytest.raises(PdfExportError):
        _adapter(boundary, tmp_path).regenerate(
            BytesIO(content),
            tmp_path / "candidate.pdf",
            tmp_path,
            _request(content),
            _limits(),
            _Control(),
        )
    assert boundary.calls == 0
    assert retained.read_bytes() == b"preserve-existing-data"
    assert list(tmp_path.iterdir()) == [private]


@pytest.mark.parametrize("rotation", list(ClockwiseRotation))
def test_exclusion_boxes_follow_exact_mask_crop_and_quarter_turn(
    tmp_path: Path, rotation: ClockwiseRotation
) -> None:
    content = _source()
    request = _request(content)
    request = replace(
        request,
        reconstruction=replace(
            request.reconstruction,
            rotation=rotation,
        ),
    )
    masked = tmp_path / "masked.pdf"
    report = RedactionReconstructionAdapter().reconstruct(
        BytesIO(content), masked, tmp_path, request.reconstruction, _limits(), _Control()
    )
    boxes = _exclusion_boxes(BytesIO(content), request, report, _limits())
    assert len(boxes) == 1
    x0, y0, x1, y1 = boxes[0]
    assert 0 <= x0 < x1 <= report.width_points
    assert 0 <= y0 < y1 <= report.height_points
    with pikepdf.Pdf.open(masked) as document:
        image = document.pages[0].Resources.XObject.Im0
        pixels = image.read_bytes()
        width, height = int(image.Width), int(image.Height)
        # Independent PDF-bottom-left to raster-top-left midpoint conversion.
        x = int((x0 + x1) / 2 / report.width_points * width)
        y = int((1 - (y0 + y1) / 2 / report.height_points) * height)
        offset = (y * width + x) * 3
        assert pixels[offset : offset + 3] == b"\x00\x00\x00"


@pytest.mark.parametrize(
    "rotation, expected",
    [
        (ClockwiseRotation.DEGREES_0, (6.96, 18.96, 50.64, 33.84)),
        (ClockwiseRotation.DEGREES_90, (18.96, 6.96, 33.84, 50.64)),
        (ClockwiseRotation.DEGREES_180, (6.96, 4.56, 50.64, 19.44)),
        (ClockwiseRotation.DEGREES_270, (4.56, 6.96, 19.44, 50.64)),
    ],
)
def test_fractional_crop_exclusion_uses_outward_quantization(
    tmp_path: Path, rotation: ClockwiseRotation, expected: tuple[float, float, float, float]
) -> None:
    content = _source()
    request = _request(content)
    masked = tmp_path / "masked.pdf"
    report = RedactionReconstructionAdapter().reconstruct(
        BytesIO(content), masked, tmp_path, request.reconstruction, _limits(), _Control()
    )
    rotated = int(rotation) % 180 != 0
    report = replace(
        report,
        width_pixels=160 if rotated else 240,
        height_pixels=240 if rotated else 160,
        width_points=38.4 if rotated else 57.6,
        height_points=57.6 if rotated else 38.4,
    )
    request = replace(
        request,
        reconstruction=replace(
            request.reconstruction,
            crop=NormalizedPageBox(100_000, 100_000, 800_000, 800_000),
            rotation=rotation,
        ),
    )
    assert _exclusion_boxes(BytesIO(content), request, report, _limits())[0] == pytest.approx(
        expected
    )

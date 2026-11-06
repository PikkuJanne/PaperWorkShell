"""Independent extraction/rendering probes and bounded failure behavior."""

from __future__ import annotations

from dataclasses import replace
from io import BytesIO
from typing import Any

import pikepdf
import pypdfium2 as pdfium  # type: ignore[import-untyped]
import pytest

from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.pdf.redaction_reconstruction import RedactionReconstructionLimits
from paperworkshell.pdf.redaction_verification_visual import (
    VisualVerificationResult,
    verify_visual,
)

_TOKEN = "SYNTHETIC_REMOVED_VISUAL_0606"


class _Control:
    def __init__(self, *, cancelled: bool = False, expired: bool = False) -> None:
        self.cancelled, self.expired = cancelled, expired

    def cancellation_requested(self) -> bool:
        return self.cancelled

    def deadline_expired(self) -> bool:
        return self.expired


def _pdf(
    *,
    text: str = "VISIBLE SENTINEL",
    color: bytes = b"\x20\x70\xb0",
    move: bool = False,
    extra_image: bool = False,
    extra_page: bool = False,
    move_text: bool = False,
) -> bytes:
    with pikepdf.Pdf.new() as document:
        page = document.add_blank_page(page_size=(72, 48))
        image = pikepdf.Stream(document, color * (12 * 8))
        image.Type, image.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
        image.Width, image.Height = 12, 8
        image.ColorSpace, image.BitsPerComponent = pikepdf.Name.DeviceRGB, 8
        page.Resources = pikepdf.Dictionary(
            XObject=pikepdf.Dictionary(Im0=image),
            Font=pikepdf.Dictionary(
                F1=pikepdf.Dictionary(
                    Type=pikepdf.Name.Font,
                    Subtype=pikepdf.Name.Type1,
                    BaseFont=pikepdf.Name.Helvetica,
                )
            ),
        )
        content = b"q 72 0 0 48 " + (b"5" if move else b"0") + b" 0 cm /Im0 Do Q\n"
        if text:
            # Invisible OCR still must be extracted and checked.
            content += (
                b"BT /F1 3 Tf 3 Tr 2 "
                + (b"5" if move_text else b"20")
                + b" Td ("
                + text.encode("ascii")
                + b") Tj ET\n"
            )
        if extra_image:
            second = pikepdf.Stream(document, b"\xff\x00\x00" * (12 * 8))
            for key in (
                "/Type",
                "/Subtype",
                "/Width",
                "/Height",
                "/ColorSpace",
                "/BitsPerComponent",
            ):
                second[key] = image[key]
            page.Resources.XObject.Im1 = second
            content += b"q 1 0 0 1 1000 1000 cm /Im1 Do Q\n"
        page.Contents = pikepdf.Stream(document, content)
        if extra_page:
            document.add_blank_page(page_size=(72, 48))
        target = BytesIO()
        document.save(target, deterministic_id=True)
        return target.getvalue()


def _verify(candidate: bytes, reference: bytes, **kwargs: Any) -> VisualVerificationResult:
    return verify_visual(
        BytesIO(candidate),
        BytesIO(reference),
        forbidden_tokens=kwargs.pop("forbidden_tokens", (_TOKEN,)),
        affected_pages=kwargs.pop("affected_pages", (0,)),
        limits=kwargs.pop(
            "limits",
            RedactionReconstructionLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1),
        ),
        control=kwargs.pop("control", _Control()),
        **kwargs,
    )


def test_identical_sanitized_pixels_and_regenerated_text_pass() -> None:
    reference = _pdf()
    assert _verify(reference, reference) == VisualVerificationResult(None, None, None)


def test_success_preserves_caller_bytes_positions_and_stream_ownership() -> None:
    content = _pdf()
    candidate, reference = BytesIO(content), BytesIO(content)
    candidate.seek(17)
    reference.seek(29)
    result = verify_visual(
        candidate,
        reference,
        forbidden_tokens=(_TOKEN,),
        affected_pages=(0,),
        limits=RedactionReconstructionLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1),
        control=_Control(),
    )
    assert result == VisualVerificationResult(None, None, None)
    assert (candidate.tell(), reference.tell()) == (17, 29)
    assert candidate.getvalue() == reference.getvalue() == content
    assert not candidate.closed and not reference.closed


def test_hidden_text_canary_is_recovered_without_pixel_difference() -> None:
    result = _verify(_pdf(text=_TOKEN.lower()), _pdf())
    assert result.text_code == "RV_CANARY_RECOVERED"
    assert result.image_code is None and result.render_code is None
    assert _TOKEN not in repr(result) and _TOKEN.lower() not in repr(result)


def test_unexpected_hidden_text_fails_even_without_a_matching_token() -> None:
    result = _verify(_pdf(text="UNEXPECTED OCR"), _pdf())
    assert result.text_code == "RV_TEXT_MISMATCH"
    assert result.image_code is None and result.render_code is None


def test_native_search_is_exercised_independently_of_extracted_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pdfium.PdfTextPage, "get_text_range", lambda *_args, **_kwargs: "VISIBLE")
    result = _verify(_pdf(text=_TOKEN), _pdf())
    assert result.text_code == "RV_CANARY_RECOVERED"
    assert result.image_code is None and result.render_code is None


def test_invisible_text_geometry_cannot_move_into_removed_pixels() -> None:
    result = _verify(_pdf(move_text=True), _pdf())
    assert result.text_code == "RV_TEXT_MISMATCH"
    assert result.image_code is None and result.render_code is None


def test_changed_decoded_image_and_render_fail_independently() -> None:
    result = _verify(_pdf(color=b"\x00\x00\x00"), _pdf())
    assert result.text_code is None
    assert result.image_code == "RV_IMAGE_MISMATCH"
    assert result.render_code == "RV_RENDER_MISMATCH"


def test_retained_original_image_outside_canvas_fails_extraction() -> None:
    result = _verify(_pdf(extra_image=True), _pdf())
    assert result.image_code == "RV_IMAGE_MISMATCH"
    assert result.render_code is None


def test_image_extraction_descends_into_nested_form_objects() -> None:
    reference = _pdf()
    with pikepdf.Pdf.open(BytesIO(reference)) as document:
        page = document.pages[0]
        retained = pikepdf.Stream(document, b"\xff\x00\x00" * (12 * 8))
        for key in ("/Type", "/Subtype", "/Width", "/Height", "/ColorSpace", "/BitsPerComponent"):
            retained[key] = page.Resources.XObject.Im0[key]
        nested = pikepdf.Stream(document, b"q 1 0 0 1 1000 1000 cm /Im1 Do Q\n")
        nested.Type, nested.Subtype = pikepdf.Name.XObject, pikepdf.Name.Form
        nested.BBox = pikepdf.Array([0, 0, 1200, 1200])
        nested.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im1=retained))
        outer = pikepdf.Stream(document, b"/Nested Do\n")
        outer.Type, outer.Subtype = pikepdf.Name.XObject, pikepdf.Name.Form
        outer.BBox = pikepdf.Array([0, 0, 1200, 1200])
        outer.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Nested=nested))
        page.Resources.XObject.Outer = outer
        page.Contents = pikepdf.Stream(document, page.Contents.read_bytes() + b"/Outer Do\n")
        target = BytesIO()
        document.save(target)
    result = _verify(target.getvalue(), reference)
    assert result.image_code == "RV_IMAGE_MISMATCH"
    assert result.render_code is None


def test_moved_sanitized_image_fails_render_even_when_extraction_matches() -> None:
    result = _verify(_pdf(move=True), _pdf())
    assert result.image_code is None
    assert result.render_code == "RV_RENDER_MISMATCH"


def test_page_count_disagreement_fails_every_method() -> None:
    result = _verify(_pdf(extra_page=True), _pdf())
    assert result == VisualVerificationResult(*(3 * ("RV_STRUCTURE_UNSUPPORTED",)))


def test_affected_page_without_sanitized_raster_fails() -> None:
    with pikepdf.Pdf.new() as document:
        document.add_blank_page(page_size=(72, 48))
        target = BytesIO()
        document.save(target)
    result = _verify(target.getvalue(), target.getvalue())
    assert result.image_code == "RV_STRUCTURE_UNSUPPORTED"


def test_native_image_decode_is_not_called_after_dimension_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_decode(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("A dimension-limited image reached native decoding")

    monkeypatch.setattr(pdfium.PdfImage, "get_bitmap", forbidden_decode)
    limits = replace(
        RedactionReconstructionLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1),
        maximum_pixels=12 * 8 - 1,
    )
    content = _pdf()
    result = _verify(content, content, limits=limits)
    assert result.image_code == result.render_code == "RV_LIMIT_EXCEEDED"


@pytest.mark.parametrize(
    ("control", "expected"),
    [(_Control(cancelled=True), "RV_CANCELLED"), (_Control(expired=True), "RV_TIMED_OUT")],
)
def test_cancel_and_deadline_are_closed_codes(control: _Control, expected: str) -> None:
    content = _pdf()
    assert _verify(content, content, control=control) == VisualVerificationResult(
        *(3 * (expected,))
    )


def test_input_byte_limit_is_checked_before_native_open(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_open(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("A byte-limited PDF reached native parsing")

    monkeypatch.setattr(pdfium, "PdfDocument", forbidden_open)
    content = _pdf()
    limits = replace(
        RedactionReconstructionLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1),
        maximum_output_bytes=len(content) - 1,
    )
    assert _verify(content, content, limits=limits) == VisualVerificationResult(
        *(3 * ("RV_LIMIT_EXCEEDED",))
    )


def test_character_count_limit_precedes_native_text_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pdfium.PdfTextPage, "count_chars", lambda *_args: 1_000_001)
    content = _pdf()
    result = _verify(content, content)
    assert result == VisualVerificationResult(*(3 * ("RV_LIMIT_EXCEEDED",)))


def test_raw_exception_values_do_not_escape_results_or_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail_extraction(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError(_TOKEN + " C:/private/input.pdf")

    monkeypatch.setattr(pdfium.PdfTextPage, "get_text_range", fail_extraction)
    content = _pdf()
    result = _verify(content, content)
    assert result.text_code == "RV_METHOD_FAILED"
    assert result.image_code is None and result.render_code is None
    output = capsys.readouterr()
    assert _TOKEN not in repr(result) + output.out + output.err
    assert "private" not in repr(result) + output.out + output.err


def test_global_sanitization_without_affected_pages_checks_every_method() -> None:
    content = _pdf()
    assert _verify(content, content, affected_pages=()) == VisualVerificationResult(
        None, None, None
    )
    assert _verify(_pdf(text=_TOKEN), content, affected_pages=()).text_code == "RV_CANARY_RECOVERED"


@pytest.mark.parametrize("pages", [(0, 0), (True,), (-1,), (9,)])
def test_invalid_or_missing_page_binding_fails_closed(pages: tuple[int, ...]) -> None:
    content = _pdf()
    assert _verify(content, content, affected_pages=pages) == VisualVerificationResult(
        *(3 * ("RV_STRUCTURE_UNSUPPORTED",))
    )

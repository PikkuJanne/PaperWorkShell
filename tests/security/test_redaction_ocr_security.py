"""Recognition cannot reintroduce a glyph or word across a removal mask.

PYTEST_DONT_REWRITE

These are synthetic extraction controls; native canary evidence exercises the
admitted OCR engines separately. This stage does not establish OCR certainty or
approve a permanent-removal export.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pypdfium2 as pdfium  # type: ignore[import-untyped]
import pytest

from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.pdf.redaction_ocr import _verify_text_exclusion
from paperworkshell.pdf.redaction_reconstruction import RedactionReconstructionLimits


class _Control:
    def cancellation_requested(self) -> bool:
        return False

    def deadline_expired(self) -> bool:
        return False


def _pdf(path: Path, text: bytes) -> None:
    with pikepdf.Pdf.new() as document:
        page = document.add_blank_page(page_size=(100, 100))
        page.Resources = pikepdf.Dictionary(
            Font=pikepdf.Dictionary(
                F1=pikepdf.Dictionary(
                    Type=pikepdf.Name.Font,
                    Subtype=pikepdf.Name.Type1,
                    BaseFont=pikepdf.Name.Helvetica,
                )
            )
        )
        page.Contents = pikepdf.Stream(document, text)
        document.save(path)


@pytest.mark.parametrize("mode", [0, 3])
def test_visible_and_hidden_recognized_text_cannot_touch_a_mask(tmp_path: Path, mode: int) -> None:
    path = tmp_path / "candidate.pdf"
    _pdf(path, f"BT /F1 10 Tf {mode} Tr 20 30 Td (PRIVATE_RECOGNIZED_CANARY) Tj ET".encode("ascii"))
    with pytest.raises(PdfExportError) as caught:
        _verify_text_exclusion(
            path,
            ((22.0, 30.0, 30.0, 40.0),),
            RedactionReconstructionLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1),
            _Control(),
        )
    assert caught.value.code is PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH
    assert caught.value.context == "redaction_ocr.text_exclusion"
    assert "PRIVATE_RECOGNIZED_CANARY" not in str(caught.value)
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_whole_word_box_cannot_bridge_a_mask_between_individual_glyphs(tmp_path: Path) -> None:
    path = tmp_path / "candidate.pdf"
    _pdf(path, b"BT /F1 10 Tf 3 Tr 20 30 Td (II) Tj ET")
    document = pdfium.PdfDocument(path)
    try:
        page = document[0]
        try:
            textpage = page.get_textpage()
            try:
                assert textpage.get_text_range() == "II", "OCR_EXCLUSION_SOURCE_CONTROL"
                left, right = textpage.get_charbox(0), textpage.get_charbox(1)
                assert left[2] < right[0], "OCR_EXCLUSION_GLYPH_GAP_CONTROL"
                gap = (left[2] + right[0]) / 2
                mask = (gap - 0.001, 29.0, gap + 0.001, 40.0)
                assert not (left[0] <= mask[2] and left[2] >= mask[0])
                assert not (right[0] <= mask[2] and right[2] >= mask[0])
            finally:
                textpage.close()
        finally:
            page.close()
    finally:
        document.close()
    with pytest.raises(PdfExportError) as caught:
        _verify_text_exclusion(
            path,
            (mask,),
            RedactionReconstructionLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1),
            _Control(),
        )
    assert caught.value.context == "redaction_ocr.text_exclusion"


def test_nonredacted_hidden_text_is_allowed_without_a_verification_claim(tmp_path: Path) -> None:
    path = tmp_path / "candidate.pdf"
    _pdf(path, b"BT /F1 10 Tf 3 Tr 20 30 Td (SEARCHABLE) Tj ET")
    _verify_text_exclusion(
        path,
        ((70.0, 70.0, 80.0, 80.0),),
        RedactionReconstructionLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1),
        _Control(),
    )

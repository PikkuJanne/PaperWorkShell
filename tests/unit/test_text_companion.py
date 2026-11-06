"""PWS-0706 bounded final-derivative text and privacy/error contracts."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from io import BytesIO
from typing import BinaryIO, cast

import pikepdf
import pytest

from paperworkshell.domain.resource_policy import (
    CONSTRAINED_RESOURCE_POLICY_V1,
    NORMAL_RESOURCE_POLICY_V1,
)
from paperworkshell.domain.text_companion import (
    TextCompanionError,
    TextCompanionErrorCode,
    TextCompanionLimits,
    sanitize_companion_text,
)
from paperworkshell.pdf.text_companion import _checked_page_box, extract_verified_pdf_text


def _pdf(*, text: bytes = b"PWSKEEP EXPORTED TEXT", blank: bool = False) -> bytes:
    with pikepdf.Pdf.new() as pdf:
        page = pdf.add_blank_page(page_size=(612, 792))
        font = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica
            )
        )
        page.obj.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
        if not blank:
            page.obj.Contents = pikepdf.Stream(pdf, b"BT /F1 18 Tf 40 700 Td (" + text + b") Tj ET")
        pdf.docinfo["/Title"] = "PRIVATE-METADATA-MUST-NOT-BE-EXPORTED"
        page.obj.Annots = pikepdf.Array(
            [
                pdf.make_indirect(
                    pikepdf.Dictionary(
                        Type=pikepdf.Name.Annot,
                        Subtype=pikepdf.Name.Text,
                        Rect=pikepdf.Array([10, 10, 30, 30]),
                        Contents="PRIVATE-ANNOTATION-MUST-NOT-BE-EXPORTED",
                    )
                )
            ]
        )
        output = BytesIO()
        pdf.save(output)
        return output.getvalue()


def _extract(
    payload: bytes,
    *,
    limits: TextCompanionLimits | None = None,
    cancelled: bool = False,
    expired: bool = False,
    expected_digest: str | None = None,
    expected_pages: int = 1,
) -> bytes:
    source, target = BytesIO(payload), BytesIO()
    size, digest = extract_verified_pdf_text(
        cast(BinaryIO, source),
        cast(BinaryIO, target),
        expected_byte_size=len(payload),
        expected_sha256=hashlib.sha256(payload).hexdigest()
        if expected_digest is None
        else expected_digest,
        expected_page_count=expected_pages,
        limits=limits or TextCompanionLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1),
        deadline=lambda: expired,
        cancellation=lambda: cancelled,
    )
    result = target.getvalue()
    assert size == len(result) and digest == hashlib.sha256(result).hexdigest()
    return result


def test_only_final_pdf_page_text_is_extracted_without_private_metadata_or_annotation() -> None:
    result = _extract(_pdf())
    assert b"Page 1" in result and b"PWSKEEP EXPORTED TEXT" in result
    assert b"PRIVATE" not in result


def test_blank_and_reconstructed_image_pages_are_explicitly_identified() -> None:
    assert b"No extractable text on this page" in _extract(_pdf(blank=True))


@pytest.mark.parametrize("policy", (NORMAL_RESOURCE_POLICY_V1, CONSTRAINED_RESOURCE_POLICY_V1))
def test_resource_policy_sets_real_companion_bounds(policy: object) -> None:
    assert isinstance(policy, type(CONSTRAINED_RESOURCE_POLICY_V1))
    limits = TextCompanionLimits.from_resource_policy(policy)
    assert limits.maximum_output_bytes <= policy.thumbnail_preview_cache_bytes // 8
    assert limits.maximum_output_bytes <= policy.maximum_temporary_storage_bytes // 16
    assert limits.maximum_pages == policy.maximum_page_count


@pytest.mark.parametrize(
    ("cancelled", "expired", "code"),
    (
        (True, False, TextCompanionErrorCode.CANCELLED),
        (False, True, TextCompanionErrorCode.TIMED_OUT),
    ),
)
def test_cancellation_and_timeout_are_sanitized(
    cancelled: bool, expired: bool, code: TextCompanionErrorCode
) -> None:
    with pytest.raises(TextCompanionError) as caught:
        _extract(_pdf(), cancelled=cancelled, expired=expired)
    assert caught.value.code is code and caught.value.__context__ is None
    assert "PRIVATE" not in repr(caught.value) + str(caught.value) + repr(caught.value.args)


@pytest.mark.parametrize("which", ("digest", "pages", "malformed", "budget"))
def test_failure_never_falls_back_to_source_or_cached_ocr(which: str) -> None:
    payload = _pdf()
    options: dict[str, object] = {}
    if which == "digest":
        options["expected_digest"] = "a" * 64
    elif which == "pages":
        options["expected_pages"] = 2
    elif which == "malformed":
        payload = b"PRIVATE-MALFORMED-PDF"
    else:
        options["limits"] = replace(
            TextCompanionLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1),
            maximum_output_bytes=5,
        )
    with pytest.raises(TextCompanionError) as caught:
        _extract(payload, **options)  # type: ignore[arg-type]
    assert caught.value.code in {
        TextCompanionErrorCode.EXPORT_CHANGED,
        TextCompanionErrorCode.EXTRACTION_FAILED,
        TextCompanionErrorCode.LIMIT_EXCEEDED,
    }
    assert "PRIVATE" not in str(caught.value) + repr(caught.value.args)


def test_plain_text_preserves_unicode_paragraphs_and_literal_markup_without_invisible_commands() -> (
    None
):
    assert (
        sanitize_companion_text("ä <b>literal</b>\r\nالعربية\t漢字\x00\u202eend")
        == "ä <b>literal</b>\nالعربية\t漢字end"
    )


def test_joining_characters_preserve_persian_indic_and_emoji_meaning() -> None:
    text = "می\u200cروم क्\u200cष 👩\u200d💻"
    assert sanitize_companion_text(text + "\u202e") == text


def _cropped_pdf(rotation: int = 0) -> bytes:
    with pikepdf.Pdf.open(BytesIO(_pdf())) as pdf:
        page = pdf.pages[0]
        page.obj.CropBox = pikepdf.Array([0, 0, 300, 792])
        page.obj.Rotate = rotation
        page.obj.Contents = pikepdf.Stream(
            pdf,
            b"BT /F1 18 Tf 40 700 Td (PWSKEEPVISIBLE) Tj ET\nBT /F1 18 Tf 400 600 Td (PWSCROPPEDHIDDEN) Tj ET\nBT /F1 18 Tf 299 500 Td (PWSPARTIAL) Tj ET",
        )
        output = BytesIO()
        pdf.save(output)
        return output.getvalue()


@pytest.mark.parametrize("rotation", (0, 90, 180, 270))
def test_cropped_hidden_and_partially_visible_glyphs_never_escape(rotation: int) -> None:
    result = _extract(_cropped_pdf(rotation))
    assert b"PWSKEEPVISIBLE" in result
    assert b"PWSCROPPEDHIDDEN" not in result
    assert b"PWSPARTIAL" not in result


@pytest.mark.parametrize(
    "box", ((0, 0, 0, 10), (0, 10, 10, 0), (0, 0, float("nan"), 10), (0, 0, 10, float("inf")))
)
def test_empty_or_invalid_page_box_fails_without_media_box_fallback(box: object) -> None:
    with pytest.raises(TextCompanionError):
        _checked_page_box(box)


def test_supplementary_unicode_joiners_and_expanded_ligatures_survive_actual_pdfium_extraction() -> (
    None
):
    with pikepdf.Pdf.open(BytesIO(_pdf())) as pdf:
        page = pdf.pages[0]
        cmap = b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def /CMapName /SyntheticUTF def /CMapType 2 def 1 begincodespacerange <00> <FF> endcodespacerange 4 beginbfchar <41> <D83DDC69> <42> <200D> <43> <D83DDCBB> <44> <006600660069> endbfchar endcmap CMapName currentdict /CMap defineresource pop end end"
        page.obj.Resources.Font.F1.ToUnicode = pikepdf.Stream(pdf, cmap)
        page.obj.Contents = pikepdf.Stream(pdf, b"BT /F1 18 Tf 40 700 Td <4142432044> Tj ET")
        output = BytesIO()
        pdf.save(output)
        assert "👩\u200d💻 ffi" in _extract(output.getvalue()).decode("utf-8")

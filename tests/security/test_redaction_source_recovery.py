"""Independently recover source canaries; passing these tests proves no removal claim.

PYTEST_DONT_REWRITE

Native parsers are used only in the test process. Product GUI parsing and release
enablement are unchanged by the PWS-0601 source-corpus work.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from contextlib import contextmanager
from io import BytesIO
from typing import cast
from xml.etree import ElementTree

import pikepdf
import pypdfium2 as pdfium  # type: ignore[import-untyped]
import pytest
from PIL import Image

from tools.generate_synthetic_fixtures import CORPUS_DIRECTORY, PROJECT_ROOT
from tools.redaction_corpus import (
    RedactionCase,
    build_redaction_fixtures,
    redaction_cases,
)

# This recognition oracle deliberately does not import the generator's glyph table.
# Each row describes a conventional five-column bitmap; glyph cells are sampled
# from the decoded actual pixels, rather than trusting PDF/PNG metadata or hashes.
_GLYPH_ROWS = {
    "A": "01110/10001/10001/11111/10001/10001/10001",
    "B": "11110/10001/10001/11110/10001/10001/11110",
    "C": "01111/10000/10000/10000/10000/10000/01111",
    "D": "11110/10001/10001/10001/10001/10001/11110",
    "E": "11111/10000/10000/11110/10000/10000/11111",
    "F": "11111/10000/10000/11110/10000/10000/10000",
    "G": "01110/10001/10000/10111/10001/10001/01110",
    "H": "10001/10001/10001/11111/10001/10001/10001",
    "I": "11111/00100/00100/00100/00100/00100/11111",
    "J": "00111/00010/00010/00010/10010/10010/01100",
    "K": "10001/10010/10100/11000/10100/10010/10001",
    "L": "10000/10000/10000/10000/10000/10000/11111",
    "M": "10001/11011/10101/10101/10001/10001/10001",
    "N": "10001/11001/11001/10101/10011/10011/10001",
    "O": "01110/10001/10001/10001/10001/10001/01110",
    "P": "11110/10001/10001/11110/10000/10000/10000",
    "Q": "01110/10001/10001/10001/10101/10010/01101",
    "R": "11110/10001/10001/11110/10100/10010/10001",
    "S": "01111/10000/10000/01110/00001/00001/11110",
    "T": "11111/00100/00100/00100/00100/00100/00100",
    "U": "10001/10001/10001/10001/10001/10001/01110",
    "V": "10001/10001/10001/10001/10001/01010/00100",
    "W": "10001/10001/10001/10101/10101/10101/01010",
    "X": "10001/10001/01010/00100/01010/10001/10001",
    "Y": "10001/10001/01010/00100/00100/00100/00100",
    "Z": "11111/00001/00010/00100/01000/10000/11111",
}
_GLYPHS = {value.replace("/", ""): key for key, value in _GLYPH_ROWS.items()}
_CASES = {case.fixture_id: case for case in redaction_cases()}
_FIXTURES = {fixture.fixture_id: fixture for fixture in build_redaction_fixtures()}


def _source(fixture_id: str) -> bytes:
    fixture = _FIXTURES[fixture_id]
    return (PROJECT_ROOT / CORPUS_DIRECTORY / fixture.relative_path).read_bytes()


@contextmanager
def _open_pdf(content: bytes) -> Iterator[pikepdf.Pdf]:
    try:
        with pikepdf.Pdf.open(
            BytesIO(content), attempt_recovery=False, suppress_warnings=True
        ) as document:
            yield document
    except pikepdf.PdfError:
        raise AssertionError("REDACTION_SOURCE_PARSE_FAILED") from None


def _text(content: bytes, page_index: int = 0) -> str:
    document = pdfium.PdfDocument(content)
    try:
        page = document[page_index]
        try:
            textpage = page.get_textpage()
            try:
                return cast(str, textpage.get_text_range())
            finally:
                textpage.close()
        finally:
            page.close()
    finally:
        document.close()


def _render(content: bytes, page_index: int = 0) -> Image.Image:
    document = pdfium.PdfDocument(content)
    try:
        page = document[page_index]
        try:
            bitmap = page.render(scale=1)
            try:
                return cast(Image.Image, bitmap.to_pil().convert("RGB"))
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        document.close()


def _decode_pixel_line(image: Image.Image, origin_y: int) -> str:
    assert image.mode == "L", "REDACTION_SOURCE_ORACLE_FAILED"
    result = []
    for origin_x in range(24, image.width - 5 * 8 + 1, 6 * 8):
        pattern = "".join(
            "1"
            if cast(int, image.getpixel((origin_x + x * 8 + 4, origin_y + y * 8 + 4))) < 128
            else "0"
            for y in range(7)
            for x in range(5)
        )
        if "1" not in pattern:
            break
        assert pattern in _GLYPHS, "decoded pixels must contain a recognized synthetic glyph"
        result.append(_GLYPHS[pattern])
    return "".join(result)


def _character_boxes(
    content: bytes, page_index: int, token: str
) -> tuple[tuple[float, float, float, float], ...]:
    document = pdfium.PdfDocument(content)
    try:
        page = document[page_index]
        try:
            textpage = page.get_textpage()
            try:
                start = textpage.get_text_range().index(token)
                return tuple(
                    cast(tuple[float, float, float, float], textpage.get_charbox(index))
                    for index in range(start, start + len(token))
                )
            finally:
                textpage.close()
        finally:
            page.close()
    finally:
        document.close()


def _assert_target_and_keep_pixels(image: Image.Image, case: RedactionCase) -> None:
    assert image.size == (1024, 320), "REDACTION_SOURCE_ORACLE_FAILED"
    assert _decode_pixel_line(image, 48) == case.canaries[0].token, "REDACTION_SOURCE_ORACLE_FAILED"
    assert _decode_pixel_line(image, 208) in case.preserve_tokens, "REDACTION_SOURCE_ORACLE_FAILED"


def test_born_digital_target_and_outside_keep_control_extract_with_pdfium() -> None:
    fixture_id = "pdf-redaction-born-digital"
    text = _text(_source(fixture_id))
    case = _CASES[fixture_id]
    assert case.canaries[0].token in text, "REDACTION_SOURCE_ORACLE_FAILED"
    assert all(token in text for token in case.preserve_tokens), "REDACTION_SOURCE_ORACLE_FAILED"


@pytest.mark.parametrize("fixture_id", ["pdf-redaction-scan", "pdf-redaction-embedded-image"])
def test_raster_canaries_decode_from_reachable_image_pixels(fixture_id: str) -> None:
    content = _source(fixture_id)
    case = _CASES[fixture_id]
    assert case.canaries[0].token.encode("ascii") not in content, "REDACTION_SOURCE_ORACLE_FAILED"
    assert case.canaries[0].token not in _text(content), "REDACTION_SOURCE_ORACLE_FAILED"
    with _open_pdf(content) as document:
        images = [
            value
            for _, value in document.pages[0].Resources.XObject.items()
            if value.get("/Subtype") == pikepdf.Name.Image
        ]
        assert len(images) == 1, "REDACTION_SOURCE_ORACLE_FAILED"
        image = images[0]
        assert image.Width == 1024 and image.Height == 320, "REDACTION_SOURCE_ORACLE_FAILED"
        assert image.ColorSpace == pikepdf.Name.DeviceGray and image.BitsPerComponent == 8, (
            "REDACTION_SOURCE_ORACLE_FAILED"
        )
        pixels = image.read_bytes()
        assert hashlib.sha256(pixels).hexdigest() == case.canaries[0].pixel_sha256, (
            "REDACTION_SOURCE_ORACLE_FAILED"
        )
        _assert_target_and_keep_pixels(Image.frombytes("L", (1024, 320), pixels), case)


def test_png_scan_canary_is_actual_pixel_text_without_a_plaintext_metadata_copy() -> None:
    fixture_id = "png-redaction-scan"
    content = _source(fixture_id)
    case = _CASES[fixture_id]
    assert case.canaries[0].token.encode("ascii") not in content, "REDACTION_SOURCE_ORACLE_FAILED"
    with Image.open(BytesIO(content)) as source:
        source.load()
        image = source.convert("L")
    assert hashlib.sha256(image.tobytes()).hexdigest() == case.canaries[0].pixel_sha256, (
        "REDACTION_SOURCE_ORACLE_FAILED"
    )
    _assert_target_and_keep_pixels(image, case)


def test_annotation_contents_are_reachable_from_the_page_annots_array() -> None:
    fixture_id = "pdf-redaction-annotation"
    with _open_pdf(_source(fixture_id)) as document:
        annotations = document.pages[0].Annots
        assert len(annotations) == 1, "REDACTION_SOURCE_ORACLE_FAILED"
        assert annotations[0].Type == pikepdf.Name.Annot, "REDACTION_SOURCE_ORACLE_FAILED"
        assert _CASES[fixture_id].canaries[0].token == str(annotations[0].Contents), (
            "REDACTION_SOURCE_ORACLE_FAILED"
        )
        appearance = annotations[0].AP.N
        assert appearance.Subtype == pikepdf.Name.Form, "REDACTION_APPEARANCE_TYPE_FAILED"
        assert tuple(float(value) for value in appearance.BBox) == (0, 0, 468, 50), (
            "REDACTION_APPEARANCE_BOX_FAILED"
        )
        assert _CASES[fixture_id].canaries[0].token.encode("ascii") in appearance.read_bytes(), (
            "REDACTION_APPEARANCE_TOKEN_MISSING"
        )


def test_form_value_is_reachable_through_both_acroform_fields_and_page_widget() -> None:
    fixture_id = "pdf-redaction-form"
    with _open_pdf(_source(fixture_id)) as document:
        fields = document.Root.AcroForm.Fields
        assert len(fields) == 1, "REDACTION_SOURCE_ORACLE_FAILED"
        field = fields[0]
        assert field.FT == pikepdf.Name.Tx, "REDACTION_SOURCE_ORACLE_FAILED"
        assert field.Subtype == pikepdf.Name.Widget, "REDACTION_SOURCE_ORACLE_FAILED"
        assert field.objgen == document.pages[0].Annots[0].objgen, "REDACTION_SOURCE_ORACLE_FAILED"
        assert _CASES[fixture_id].canaries[0].token == str(field.V), (
            "REDACTION_SOURCE_ORACLE_FAILED"
        )
        appearance = field.AP.N
        assert appearance.Subtype == pikepdf.Name.Form, "REDACTION_APPEARANCE_TYPE_FAILED"
        assert tuple(float(value) for value in appearance.BBox) == (0, 0, 468, 50), (
            "REDACTION_APPEARANCE_BOX_FAILED"
        )
        assert _CASES[fixture_id].canaries[0].token.encode("ascii") in appearance.read_bytes(), (
            "REDACTION_APPEARANCE_TOKEN_MISSING"
        )


def test_info_and_xmp_canaries_are_in_reachable_metadata_values() -> None:
    fixture_id = "pdf-redaction-metadata"
    with _open_pdf(_source(fixture_id)) as document:
        info_values = {str(value) for _, value in document.docinfo.items()}
        xmp = document.Root.Metadata
        assert xmp.Type == pikepdf.Name.Metadata, "REDACTION_SOURCE_ORACLE_FAILED"
        assert xmp.Subtype == pikepdf.Name.XML, "REDACTION_SOURCE_ORACLE_FAILED"
        xml_values = set(ElementTree.fromstring(xmp.read_bytes()).itertext())
        canaries = _CASES[fixture_id].canaries
        assert len(canaries) == 2, "REDACTION_SOURCE_ORACLE_FAILED"
        assert any(canary.token in info_values for canary in canaries), (
            "REDACTION_SOURCE_ORACLE_FAILED"
        )
        assert any(canary.token in xml_values for canary in canaries), (
            "REDACTION_SOURCE_ORACLE_FAILED"
        )
        assert {canary.token for canary in canaries}.issubset(info_values | xml_values), (
            "REDACTION_SOURCE_ORACLE_FAILED"
        )


def test_embedded_file_canary_is_recoverable_from_the_catalog_name_tree() -> None:
    fixture_id = "pdf-redaction-attachment"
    with _open_pdf(_source(fixture_id)) as document:
        names = document.Root.Names.EmbeddedFiles.Names
        assert len(names) == 2, "REDACTION_SOURCE_ORACLE_FAILED"
        filespec = names[1]
        assert filespec.Type == pikepdf.Name.Filespec, "REDACTION_SOURCE_ORACLE_FAILED"
        embedded = filespec.EF.F
        assert embedded.Type == pikepdf.Name.EmbeddedFile, "REDACTION_SOURCE_ORACLE_FAILED"
        assert _CASES[fixture_id].canaries[0].token.encode("ascii") in embedded.read_bytes(), (
            "REDACTION_SOURCE_ORACLE_FAILED"
        )


def test_hidden_optional_content_is_reachable_and_changes_rendering_when_enabled() -> None:
    fixture_id = "pdf-redaction-hidden-layer"
    content = _source(fixture_id)
    with _open_pdf(content) as document:
        properties = document.Root.OCProperties
        ocg = properties.OCGs[0]
        assert ocg.Type == pikepdf.Name.OCG, "REDACTION_SOURCE_ORACLE_FAILED"
        assert properties.D.OFF[0].objgen == ocg.objgen, "REDACTION_SOURCE_ORACLE_FAILED"
        assert any(
            value.objgen == ocg.objgen
            for _, value in document.pages[0].Resources.Properties.items()
        ), "REDACTION_SOURCE_ORACLE_FAILED"
        stream = document.pages[0].Contents.read_bytes()
        assert b"BDC" in stream and b"EMC" in stream, "REDACTION_SOURCE_ORACLE_FAILED"
        assert _CASES[fixture_id].canaries[0].token.encode("ascii") in stream, (
            "REDACTION_SOURCE_ORACLE_FAILED"
        )
        properties.D.OFF = pikepdf.Array()
        properties.D.ON = pikepdf.Array([ocg])
        enabled = BytesIO()
        document.save(enabled)
    assert _render(content).tobytes() != _render(enabled.getvalue()).tobytes(), (
        "REDACTION_SOURCE_ORACLE_FAILED"
    )


def test_incremental_source_has_a_real_older_revision_absent_from_current_extraction() -> None:
    fixture_id = "pdf-redaction-incremental"
    content = _source(fixture_id)
    token = _CASES[fixture_id].canaries[0].token
    xref_offsets = [int(value) for value in re.findall(rb"startxref\n([0-9]+)", content)]
    assert len(xref_offsets) == 2, "REDACTION_SOURCE_ORACLE_FAILED"
    assert content.count(b"%%EOF") == 2, "REDACTION_SOURCE_ORACLE_FAILED"
    assert f"/Prev {xref_offsets[0]}".encode("ascii") in content[xref_offsets[1] :], (
        "REDACTION_SOURCE_ORACLE_FAILED"
    )
    for offset in xref_offsets:
        assert content[offset : offset + 4] == b"xref", "REDACTION_SOURCE_ORACLE_FAILED"
    earlier = content[: content.index(b"%%EOF") + len(b"%%EOF")]
    assert token in _text(earlier), "REDACTION_SOURCE_ORACLE_FAILED"
    assert token not in _text(content), "REDACTION_SOURCE_ORACLE_FAILED"
    assert token.encode("ascii") in content, "REDACTION_SOURCE_ORACLE_FAILED"
    with _open_pdf(content) as document:
        assert document.trailer.Prev == xref_offsets[0], "REDACTION_SOURCE_ORACLE_FAILED"
        assert token.encode("ascii") not in document.pages[0].Contents.read_bytes(), (
            "REDACTION_SOURCE_ORACLE_FAILED"
        )
    assert all(value in _text(content) for value in _CASES[fixture_id].preserve_tokens), (
        "REDACTION_SOURCE_ORACLE_FAILED"
    )


def test_rotated_cropped_sources_keep_extractable_targets_at_every_quarter_turn() -> None:
    fixture_id = "pdf-redaction-geometry"
    content = _source(fixture_id)
    case = _CASES[fixture_id]
    with _open_pdf(content) as document:
        assert len(document.pages) == 4, "REDACTION_SOURCE_ORACLE_FAILED"
        assert len(case.canaries) == 4, "REDACTION_SOURCE_ORACLE_FAILED"
        for index, (page, canary) in enumerate(zip(document.pages, case.canaries, strict=True)):
            assert int(page.Rotate) == index * 90, "REDACTION_SOURCE_ORACLE_FAILED"
            crop = tuple(float(value) for value in page.CropBox)
            media = tuple(float(value) for value in page.MediaBox)
            assert crop[0] > media[0] and crop[1] > media[1], "REDACTION_SOURCE_ORACLE_FAILED"
            assert crop[2] < media[2] and crop[3] < media[3], "REDACTION_SOURCE_ORACLE_FAILED"
            assert canary.token in _text(content, index), "REDACTION_SOURCE_ORACLE_FAILED"
            assert case.preserve_tokens[index] in _text(content, index), (
                "REDACTION_KEEP_TOKEN_MISSING"
            )
            region = case.target_regions[index]
            width, height = crop[2] - crop[0], crop[3] - crop[1]
            for left, bottom, right, top in _character_boxes(content, index, canary.token):
                assert (
                    region.x0 <= (left - crop[0]) / width
                    and (right - crop[0]) / width <= region.x1
                    and region.y0 <= (crop[3] - top) / height
                    and (crop[3] - bottom) / height <= region.y1
                ), "REDACTION_TARGET_REGION_MISALIGNED"
            for left, bottom, right, top in _character_boxes(
                content, index, case.preserve_tokens[index]
            ):
                assert (
                    (right - crop[0]) / width < region.x0
                    or (left - crop[0]) / width > region.x1
                    or (crop[3] - bottom) / height < region.y0
                    or (crop[3] - top) / height > region.y1
                ), "REDACTION_KEEP_CONTROL_INSIDE_TARGET"
            rendered = _render(content, index)
            expected = (int(crop[2] - crop[0]), int(crop[3] - crop[1]))
            assert rendered.size == (expected[::-1] if index % 2 else expected), (
                "REDACTION_SOURCE_ORACLE_FAILED"
            )


def test_invisible_ocr_token_can_extract_even_without_visible_text_rendering() -> None:
    fixture_id = "pdf-redaction-hidden-ocr"
    content = _source(fixture_id)
    assert _CASES[fixture_id].canaries[0].token in _text(content), "REDACTION_SOURCE_ORACLE_FAILED"
    with _open_pdf(content) as document:
        stream = document.pages[0].Contents.read_bytes()
        assert b"3 Tr" in stream, "REDACTION_SOURCE_ORACLE_FAILED"
        assert _CASES[fixture_id].canaries[0].token.encode("ascii") in stream, (
            "REDACTION_SOURCE_ORACLE_FAILED"
        )
        assert document.pages[0].Resources.XObject, "REDACTION_SOURCE_ORACLE_FAILED"
        image = document.pages[0].Resources.XObject.Im0
        pixels = image.read_bytes()
        assert hashlib.sha256(pixels).hexdigest() == _CASES[fixture_id].canaries[0].pixel_sha256, (
            "REDACTION_PIXEL_DIGEST_MISMATCH"
        )
        _assert_target_and_keep_pixels(
            Image.frombytes("L", (1024, 320), pixels), _CASES[fixture_id]
        )


def test_malformed_source_is_rejected_without_parser_recovery() -> None:
    with pytest.raises(pikepdf.PdfError):
        pikepdf.Pdf.open(BytesIO(_source("pdf-redaction-malformed")), attempt_recovery=False)


def test_visual_cover_is_a_negative_control_with_target_text_still_extractable() -> None:
    fixture_id = "pdf-redaction-visual-cover"
    content = _source(fixture_id)
    case = _CASES[fixture_id]
    assert case.negative_control is True, "REDACTION_SOURCE_ORACLE_FAILED"
    assert case.canaries[0].token in _text(content), "REDACTION_SOURCE_ORACLE_FAILED"
    assert all(token in _text(content) for token in case.preserve_tokens), (
        "REDACTION_SOURCE_ORACLE_FAILED"
    )
    with _open_pdf(content) as document:
        stream = document.pages[0].Contents.read_bytes()
        token_offset = stream.index(case.canaries[0].token.encode("ascii"))
        assert stream.index(b"re f", token_offset) > token_offset, "REDACTION_SOURCE_ORACLE_FAILED"
    region = case.target_regions[0]
    image = _render(content)
    center_x = int((region.x0 + region.x1) * image.width / 2)
    center_y = int((region.y0 + region.y1) * image.height / 2)
    assert image.getpixel((center_x, center_y)) == (0, 0, 0), "REDACTION_SOURCE_ORACLE_FAILED"

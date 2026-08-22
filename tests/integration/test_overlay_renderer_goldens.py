"""Approved visual and extraction checks for the PWS-0303 production adapter."""

from __future__ import annotations

import hashlib
import json

import pypdfium2  # type: ignore[import-untyped]
from PIL import Image

from tools.overlay_render_goldens import (
    GOLDEN_ROOT,
    approved_index,
    build_artifacts,
    validate_committed_goldens,
)


def test_generated_golden_profile_binds_complete_output_closure() -> None:
    assert approved_index(())["engine_profile"] == {
        "fpdf2": "2.8.8",
        "fonttools": "4.63.0",
        "uharfbuzz": "0.56.0",
        "pillow": "12.3.0",
        "pikepdf": "10.10.0",
        "qpdf": "12.3.2",
        "ocrmypdf": "17.10.0",
        "zlib_compile": "1.3.1.zlib-ng",
        "zlib_runtime": "1.3.1.zlib-ng",
        "zlib_ng": "2.2.4",
        "font_version": "Version 2.007",
        "font_sha256": "2ec33f84606cbaa0a1a944488e14f97faf2f6a25ecdd8354f5358f06da13c7d9",
        "pypdfium2": "5.12.1",
        "pdfium": "152.0.7947.0",
    }


def test_production_overlay_renderer_matches_complete_approved_golden_set() -> None:
    validate_committed_goldens()
    index = json.loads((GOLDEN_ROOT / "index.json").read_text(encoding="utf-8"))
    references = {record["id"]: record for record in index["references"]}

    for artifact in build_artifacts():
        record = references[artifact.case_id]
        expected = Image.open(GOLDEN_ROOT / record["path"]).convert("RGB")
        rendered = Image.frombytes(
            "RGBA",
            (artifact.width_pixels, artifact.height_pixels),
            artifact.rgba_bytes,
        ).convert("RGB")

        assert rendered.size == expected.size
        assert rendered.tobytes() == expected.tobytes(), artifact.case_id
        assert hashlib.sha256(artifact.pdf_bytes).hexdigest() == record["pdf_sha256"]
        assert hashlib.sha256(artifact.png_bytes).hexdigest() == record["png_sha256"]
        assert hashlib.sha256(artifact.rgba_bytes).hexdigest() == record["rgba_sha256"]


def test_golden_overlay_pdfs_extract_exact_codepoints_in_logical_order() -> None:
    for artifact in build_artifacts():
        document = pypdfium2.PdfDocument(artifact.pdf_bytes)
        try:
            page = document[0]
            text_page = page.get_textpage()
            try:
                extracted = text_page.get_text_range()
                assert extracted == artifact.expected_text, artifact.case_id
            finally:
                text_page.close()
                page.close()
        finally:
            document.close()


def test_golden_overlay_pdfs_keep_the_unpainted_backdrop_transparent() -> None:
    alpha_by_case: dict[str, bytes] = {}
    for artifact in build_artifacts():
        document = pypdfium2.PdfDocument(artifact.pdf_bytes)
        try:
            page = document[0]
            try:
                bitmap = page.render(
                    scale=1.0,
                    fill_color=(0, 0, 0, 0),
                    rev_byteorder=True,
                    maybe_alpha=True,
                )
                try:
                    image = bitmap.to_pil().convert("RGBA")
                    try:
                        assert image.size == (artifact.width_pixels, artifact.height_pixels)
                        alpha = image.getchannel("A").tobytes()
                    finally:
                        image.close()
                finally:
                    bitmap.close()
            finally:
                page.close()
        finally:
            document.close()
        alpha_by_case[artifact.case_id] = alpha
        assert alpha[-1] == 0, artifact.case_id

    # Interior of the #ffff0070 highlight in the primitive recipe.  This
    # complements the untouched-corner check by proving that intended alpha is
    # retained rather than converted to an opaque paint operation.
    highlight_alpha = alpha_by_case["primitives"][201 * 480 + 333]
    assert 100 <= highlight_alpha <= 120


def test_unicode_golden_contains_visible_devanagari_ink_inside_approved_region() -> None:
    with Image.open(GOLDEN_ROOT / "unicode-shaping.png") as source:
        image = source.convert("RGB")
    # The fourth line occupies y=378..444 at the locked 480x600 raster.  This
    # content-level guard complements exact golden comparison and catches an
    # accidentally empty or clipped Devanagari line with a direct diagnosis.
    devanagari = image.crop((35, 370, 450, 455))
    rgb = devanagari.tobytes()
    nonwhite = sum(rgb[offset : offset + 3] != b"\xff\xff\xff" for offset in range(0, len(rgb), 3))

    assert nonwhite > 500

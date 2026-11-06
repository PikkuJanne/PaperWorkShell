"""Closed, deterministic adversarial sources for the disabled M6 redaction program.

These are deliberately *recoverable* source canaries, not sanitized derivatives.
The inventory records how to recover each carrier and where later removal tests
must act.  Generating these public synthetic bytes does not enable redaction.
"""

from __future__ import annotations

import hashlib
import json
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from tools.generate_synthetic_fixtures import (
    CORPUS_DIRECTORY,
    SYNTHETIC_MARKER,
    GeneratedFixture,
    JsonValue,
    _pdf_literal,
    _pdf_stream,
    _png_chunk,
    _serialize_pdf,
    _stored_zlib,
)

CANARY_INVENTORY_PATH: Final = Path("tests/fixtures/redaction/v1/canaries.json")
REDACTION_CORPUS_ID: Final = "paperworkshell-redaction-adversarial-v1"
RASTER_WIDTH: Final = 1024
RASTER_HEIGHT: Final = 320
RASTER_GLYPH_SCALE: Final = 8
RASTER_TARGET_ORIGIN: Final = (24, 48)
RASTER_KEEP_ORIGIN: Final = (24, 208)
RASTER_GLYPHS: Final[dict[str, tuple[str, ...]]] = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01110"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "11001", "10101", "10011", "10011", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
}


@dataclass(frozen=True)
class RedactionRegion:
    """A target in top-left normalized CropBox space before intrinsic rotation."""

    page_index: int
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class RedactionCanary:
    """One unique synthetic value and an independently inspectable carrier."""

    canary_id: str
    token: str
    carrier: str
    page_index: int | None
    extraction_methods: tuple[str, ...]
    object_numbers: tuple[int, ...]
    pixel_sha256: str | None = None


@dataclass(frozen=True)
class RedactionCase:
    """A closed source oracle; no entry claims successful permanent removal."""

    fixture_id: str
    classes: tuple[str, ...]
    canaries: tuple[RedactionCanary, ...]
    expected_open: str
    expected_error_code: str | None
    negative_control: bool
    target_regions: tuple[RedactionRegion, ...]
    preserve_tokens: tuple[str, ...]


def _raster_pixels(token: str, keep_token: str) -> bytes:
    pixels = bytearray(b"\xff" * RASTER_WIDTH * RASTER_HEIGHT)
    for text, (origin_x, origin_y) in (
        (token, RASTER_TARGET_ORIGIN),
        (keep_token, RASTER_KEEP_ORIGIN),
    ):
        if len(text) > 20 or not text.isascii() or not text.isalpha() or not text.isupper():
            raise AssertionError("redaction glyph input is outside the closed recipe")
        for index, character in enumerate(text):
            for glyph_y, row in enumerate(RASTER_GLYPHS[character]):
                for glyph_x, cell in enumerate(row):
                    if cell != "1":
                        continue
                    x = origin_x + (index * 6 + glyph_x) * RASTER_GLYPH_SCALE
                    y = origin_y + glyph_y * RASTER_GLYPH_SCALE
                    for offset_y in range(RASTER_GLYPH_SCALE):
                        start = (y + offset_y) * RASTER_WIDTH + x
                        pixels[start : start + RASTER_GLYPH_SCALE] = b"\x00" * RASTER_GLYPH_SCALE
    return bytes(pixels)


def _image_stream(token: str, keep_token: str) -> bytes:
    return _pdf_stream(
        _stored_zlib(_raster_pixels(token, keep_token)),
        b"/Type /XObject /Subtype /Image /Width 1024 /Height 320 "
        b"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode /Interpolate false",
    )


def _raster_png(token: str, keep_token: str) -> bytes:
    pixels = _raster_pixels(token, keep_token)
    raw = b"".join(
        b"\x00" + pixels[y * RASTER_WIDTH : (y + 1) * RASTER_WIDTH] for y in range(RASTER_HEIGHT)
    )
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1024, 320, 8, 0, 0, 0, 0)),
            _png_chunk(b"tEXt", b"Comment\x00" + SYNTHETIC_MARKER.encode("ascii")),
            _png_chunk(b"IDAT", _stored_zlib(raw)),
            _png_chunk(b"IEND", b""),
        )
    )


def _text(token: str, *, x: int = 72, y: int = 400, size: int = 20) -> bytes:
    return f"BT /F1 {size} Tf {x} {y} Td (".encode("ascii") + _pdf_literal(token) + b") Tj ET"


def _page_pdf(
    content: bytes,
    *,
    keep_token: str | None = None,
    catalog_extra: bytes = b"",
    page_extra: bytes = b"",
    resource_extra: bytes = b"",
    info_extra: bytes = b"",
    extra_objects: Sequence[bytes] = (),
    pdf_version: bytes = b"1.4",
) -> bytes:
    if keep_token is not None:
        content += b"\n" + _text(keep_token, y=700, size=14)
    return _serialize_pdf(
        (
            b"<< /Type /Catalog /Pages 2 0 R /Lang (en-US) " + catalog_extra + b" >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> "
            + resource_extra
            + b" >> /Contents 5 0 R "
            + page_extra
            + b" >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            _pdf_stream(content),
            b"<< /Producer (" + _pdf_literal(SYNTHETIC_MARKER) + b") " + info_extra + b" >>",
            *extra_objects,
        ),
        info_object=6,
        pdf_version=pdf_version,
    )


def _annotation_pdf() -> bytes:
    token = "PWSREDANNOTDELTA"
    appearance = _pdf_stream(
        _text(token, x=4, y=16),
        b"/Type /XObject /Subtype /Form /BBox [0 0 468 50] /Resources << /Font << /F1 4 0 R >> >>",
    )
    return _page_pdf(
        b"",
        keep_token="PWSKEEPANNOT",
        page_extra=b"/Annots [7 0 R]",
        extra_objects=(
            b"<< /Type /Annot /Subtype /FreeText /Rect [72 380 540 430] "
            b"/Contents (" + _pdf_literal(token) + b") /DA (/F1 20 Tf 0 g) /F 4 "
            b"/AP << /N 8 0 R >> >>",
            appearance,
        ),
    )


def _form_pdf() -> bytes:
    token = "PWSREDFORMECHO"
    return _page_pdf(
        b"",
        keep_token="PWSKEEPFORM",
        catalog_extra=b"/AcroForm << /Fields [7 0 R] /NeedAppearances false "
        b"/DR << /Font << /F1 4 0 R >> >> /DA (/F1 20 Tf 0 g) >>",
        page_extra=b"/Annots [7 0 R]",
        extra_objects=(
            b"<< /Type /Annot /Subtype /Widget /FT /Tx /T (synthetic-field) /Rect [72 380 540 430] "
            b"/P 3 0 R /F 4 /V ("
            + _pdf_literal(token)
            + b") /DV ("
            + _pdf_literal(token)
            + b") /AP << /N 8 0 R >> >>",
            _pdf_stream(
                _text(token, x=4, y=16),
                b"/Type /XObject /Subtype /Form /BBox [0 0 468 50] "
                b"/Resources << /Font << /F1 4 0 R >> >>",
            ),
        ),
    )


def _metadata_pdf() -> bytes:
    xmp = (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:description><rdf:Alt><rdf:li xml:lang="x-default">PWSREDXMPGOLF'
        "</rdf:li></rdf:Alt></dc:description></rdf:Description></rdf:RDF></x:xmpmeta>\n"
        '<?xpacket end="w"?>'
    ).encode("utf-8")
    return _page_pdf(
        b"",
        keep_token="PWSKEEPMETA",
        catalog_extra=b"/Metadata 7 0 R",
        info_extra=b"/Subject (PWSREDINFOFOXTROT)",
        extra_objects=(_pdf_stream(xmp, b"/Type /Metadata /Subtype /XML"),),
    )


def _attachment_pdf() -> bytes:
    return _page_pdf(
        b"",
        keep_token="PWSKEEPFILE",
        catalog_extra=b"/Names << /EmbeddedFiles << /Names [(synthetic-canary.txt) 7 0 R] >> >>",
        extra_objects=(
            b"<< /Type /Filespec /F (synthetic-canary.txt) /UF (synthetic-canary.txt) "
            b"/EF << /F 8 0 R >> >>",
            _pdf_stream(
                b"PWSREDFILEHOTEL\n",
                b"/Type /EmbeddedFile /Subtype /text#2Fplain /Params << /Size 16 >>",
            ),
        ),
    )


def _hidden_layer_pdf() -> bytes:
    return _page_pdf(
        b"/OC /Hidden BDC\n" + _text("PWSREDLAYERINDIA") + b"\nEMC",
        keep_token="PWSKEEPLAYER",
        catalog_extra=b"/OCProperties << /OCGs [7 0 R] "
        b"/D << /BaseState /ON /OFF [7 0 R] /Order [7 0 R] >> >>",
        resource_extra=b"/Properties << /Hidden 7 0 R >>",
        extra_objects=(b"<< /Type /OCG /Name (Synthetic hidden layer) >>",),
        pdf_version=b"1.6",
    )


def _incremental_pdf() -> bytes:
    base = _page_pdf(_text("PWSREDOLDJULIET"), keep_token="PWSKEEPREVISION")
    previous_xref = int(base.rsplit(b"startxref\n", maxsplit=1)[1].splitlines()[0])
    replacement = _pdf_stream(_text("PWSKEEPREVISION", y=700, size=14))
    offset = len(base)
    appended = b"5 0 obj\n" + replacement + b"\nendobj\n"
    xref_offset = offset + len(appended)
    appended += (
        f"xref\n5 1\n{offset:010d} 00000 n \n"
        f"trailer\n<< /Size 7 /Root 1 0 R /Info 6 0 R /Prev {previous_xref} >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")
    return base + appended


def _geometry_pdf() -> bytes:
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R 5 0 R 6 0 R] /Count 4 >>",
    ]
    for index, rotation in enumerate((0, 90, 180, 270)):
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [20 30 632 822] "
            b"/CropBox [50 70 590 750] "
            + f"/Rotate {rotation} /Resources << /Font << /F1 7 0 R >> >> ".encode("ascii")
            + f"/Contents {index + 8} 0 R >>".encode("ascii")
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for suffix in ("ZERO", "ONE", "TWO", "THREE"):
        objects.append(
            _pdf_stream(
                _text("PWSREDGEO" + suffix, x=82)
                + b"\n"
                + _text("PWSKEEPGEO" + suffix, x=82, y=690, size=14)
            )
        )
    objects.append(b"<< /Producer (" + _pdf_literal(SYNTHETIC_MARKER) + b") >>")
    return _serialize_pdf(objects, info_object=12)


def _scan_pdf(token: str, keep_token: str, *, hidden_ocr: bool = False) -> bytes:
    content = b"q 540 0 0 168.75 36 320 cm /Im0 Do Q"
    if hidden_ocr:
        content += b"\nBT /F1 20 Tf 3 Tr 48 440 Td (" + _pdf_literal(token) + b") Tj ET"
        content += b"\nBT /F1 14 Tf 3 Tr 48 355 Td (" + _pdf_literal(keep_token) + b") Tj ET"
    return _page_pdf(
        content,
        resource_extra=b"/XObject << /Im0 7 0 R >>",
        extra_objects=(_image_stream(token, keep_token),),
    )


def _malformed_pdf() -> bytes:
    return (
        b"%PDF-1.4\n% "
        + SYNTHETIC_MARKER.encode("ascii")
        + b"\n1 0 obj\n<< /Type /Catalog /PWSCanary (PWSREDBADMALFORMED) /Pages 2 0 R"
    )


def build_redaction_fixtures() -> tuple[GeneratedFixture, ...]:
    """Build only the fourteen bounded M6 sources, without file or engine access."""

    plans: tuple[tuple[str, tuple[str, ...], bytes, dict[str, JsonValue]], ...] = (
        (
            "born-digital",
            ("born-digital",),
            _page_pdf(_text("PWSREDTXTALPHA"), keep_token="PWSKEEPTEXT"),
            {},
        ),
        (
            "scan",
            ("image-only",),
            _scan_pdf("PWSREDSCANBRAVO", "PWSKEEPSCAN"),
            {"image_xobject_count": 1, "has_text_operator": False},
        ),
        (
            "embedded-image",
            ("image-only",),
            _scan_pdf("PWSREDIMAGECHARLIE", "PWSKEEPIMAGE"),
            {"image_xobject_count": 1, "has_text_operator": False},
        ),
        ("annotation", ("annotation",), _annotation_pdf(), {"annotation_count": 1}),
        ("form", ("annotation",), _form_pdf(), {"form_field_count": 1}),
        ("metadata", ("born-digital",), _metadata_pdf(), {"xmp_stream_count": 1}),
        ("attachment", ("attachment",), _attachment_pdf(), {"embedded_file_count": 1}),
        ("hidden-layer", ("mixed",), _hidden_layer_pdf(), {"off_optional_content_groups": 1}),
        (
            "incremental",
            ("incremental",),
            _incremental_pdf(),
            {"revision_count": 2, "canary_only_in_older_revision": True},
        ),
        (
            "geometry",
            ("crop",),
            _geometry_pdf(),
            {"page_count": 4, "rotations_degrees": "0,90,180,270", "nonzero_box_origins": True},
        ),
        (
            "hidden-ocr",
            ("mixed",),
            _scan_pdf("PWSREDOCRLIMA", "PWSKEEPOCR", hidden_ocr=True),
            {"image_xobject_count": 1, "invisible_text_mode": 3},
        ),
        (
            "malformed",
            ("malformed",),
            _malformed_pdf(),
            {"page_count": 0, "expected_open": "reject", "expected_error_code": "PDF_MALFORMED"},
        ),
        (
            "visual-cover",
            ("born-digital",),
            _page_pdf(
                _text("PWSREDCOVERMIKE") + b"\n0 g 60 370 500 80 re f", keep_token="PWSKEEPCOVER"
            ),
            {"visual_cover_negative_control": True, "canary_recoverable": True},
        ),
    )
    fixtures = []
    for suffix, categories, content, expected in plans:
        fixture_id = "pdf-redaction-" + suffix
        assertions: dict[str, JsonValue] = {"page_count": 1, "redaction_source_only": True}
        assertions.update(expected)
        fixtures.append(
            GeneratedFixture(
                fixture_id,
                PurePosixPath("pdf/redaction-" + suffix + ".pdf"),
                "application/pdf",
                categories,
                "redaction-" + suffix + "-pdf-v1",
                assertions,
                content,
            )
        )
    fixtures.append(
        GeneratedFixture(
            "png-redaction-scan",
            PurePosixPath("image/redaction-scan.png"),
            "image/png",
            ("image-only",),
            "redaction-scan-png-v1",
            {
                "width": RASTER_WIDTH,
                "height": RASTER_HEIGHT,
                "color_type": 0,
                "external_font_asset": False,
                "redaction_source_only": True,
            },
            _raster_png("PWSREDPNGOSCAR", "PWSKEEPPNG"),
        )
    )
    return tuple(sorted(fixtures, key=lambda fixture: fixture.fixture_id))


def redaction_cases() -> tuple[RedactionCase, ...]:
    """Return source expectations independently of the synthetic index schema."""

    text_region = (RedactionRegion(0, 0.10, 0.45, 0.95, 0.55),)
    raster_region = (RedactionRegion(0, 0.05, 0.40, 0.95, 0.46),)

    def canary(
        suffix: str,
        token: str,
        carrier: str,
        methods: tuple[str, ...],
        objects: tuple[int, ...],
        *,
        page: int | None = 0,
        keep: str | None = None,
    ) -> RedactionCanary:
        digest = hashlib.sha256(_raster_pixels(token, keep)).hexdigest() if keep else None
        return RedactionCanary("rc-" + suffix, token, carrier, page, methods, objects, digest)

    def case(
        suffix: str,
        classes: tuple[str, ...],
        canaries: tuple[RedactionCanary, ...],
        keep: str,
        *,
        regions: tuple[RedactionRegion, ...] = text_region,
        negative: bool = False,
    ) -> RedactionCase:
        return RedactionCase(
            "pdf-redaction-" + suffix, classes, canaries, "accept", None, negative, regions, (keep,)
        )

    cases = [
        case(
            "born-digital",
            ("born-digital-text",),
            (
                canary(
                    "text",
                    "PWSREDTXTALPHA",
                    "page-content",
                    ("text-extraction", "decoded-streams"),
                    (5,),
                ),
            ),
            "PWSKEEPTEXT",
        ),
        case(
            "scan",
            ("scanned-text",),
            (
                canary(
                    "scan",
                    "PWSREDSCANBRAVO",
                    "raster-image",
                    ("decoded-image-pixels", "ocr"),
                    (7,),
                    keep="PWSKEEPSCAN",
                ),
            ),
            "PWSKEEPSCAN",
            regions=raster_region,
        ),
        case(
            "embedded-image",
            ("embedded-images",),
            (
                canary(
                    "image",
                    "PWSREDIMAGECHARLIE",
                    "raster-image",
                    ("decoded-image-pixels", "ocr"),
                    (7,),
                    keep="PWSKEEPIMAGE",
                ),
            ),
            "PWSKEEPIMAGE",
            regions=raster_region,
        ),
        case(
            "annotation",
            ("annotations",),
            (
                canary(
                    "annotation",
                    "PWSREDANNOTDELTA",
                    "annotation",
                    ("annotation-values", "appearance-streams"),
                    (7, 8),
                ),
            ),
            "PWSKEEPANNOT",
        ),
        case(
            "form",
            ("form-values",),
            (
                canary(
                    "form",
                    "PWSREDFORMECHO",
                    "acroform-value",
                    ("form-values", "appearance-streams"),
                    (7, 8),
                ),
            ),
            "PWSKEEPFORM",
        ),
        case(
            "metadata",
            ("metadata",),
            (
                canary(
                    "info", "PWSREDINFOFOXTROT", "info-metadata", ("info-values",), (6,), page=None
                ),
                canary("xmp", "PWSREDXMPGOLF", "xmp-metadata", ("xmp-values",), (7,), page=None),
            ),
            "PWSKEEPMETA",
            regions=(),
        ),
        case(
            "attachment",
            ("attachments",),
            (
                canary(
                    "attachment",
                    "PWSREDFILEHOTEL",
                    "embedded-file",
                    ("embedded-file-extraction",),
                    (7, 8),
                    page=None,
                ),
            ),
            "PWSKEEPFILE",
            regions=(),
        ),
        case(
            "hidden-layer",
            ("hidden-layers",),
            (
                canary(
                    "layer",
                    "PWSREDLAYERINDIA",
                    "off-optional-content-group",
                    ("decoded-streams", "optional-content-inspection"),
                    (5, 7),
                ),
            ),
            "PWSKEEPLAYER",
        ),
        case(
            "incremental",
            ("incremental-revisions",),
            (
                canary(
                    "revision",
                    "PWSREDOLDJULIET",
                    "previous-revision-page-content",
                    ("previous-revision-extraction",),
                    (5,),
                ),
            ),
            "PWSKEEPREVISION",
        ),
        case(
            "geometry",
            ("rotated-pages", "cropped-pages"),
            tuple(
                canary(
                    "geometry-" + suffix.lower(),
                    "PWSREDGEO" + suffix,
                    "page-content",
                    ("text-extraction", "decoded-streams"),
                    (index + 8,),
                    page=index,
                )
                for index, suffix in enumerate(("ZERO", "ONE", "TWO", "THREE"))
            ),
            "PWSKEEPGEOZERO",
            regions=tuple(RedactionRegion(index, 0.04, 0.45, 0.95, 0.55) for index in range(4)),
        ),
        case(
            "hidden-ocr",
            ("hidden-ocr", "scanned-text"),
            (
                canary(
                    "ocr",
                    "PWSREDOCRLIMA",
                    "invisible-ocr-and-raster",
                    ("text-extraction", "decoded-image-pixels", "ocr"),
                    (5, 7),
                    keep="PWSKEEPOCR",
                ),
            ),
            "PWSKEEPOCR",
            regions=raster_region,
        ),
        RedactionCase(
            "pdf-redaction-malformed",
            ("malformed-files",),
            (
                canary(
                    "malformed",
                    "PWSREDBADMALFORMED",
                    "malformed-source-bytes",
                    ("raw-source-inventory",),
                    (1,),
                    page=None,
                ),
            ),
            "reject",
            "PDF_MALFORMED",
            False,
            (),
            (),
        ),
        case(
            "visual-cover",
            ("visual-cover-negative-control",),
            (
                canary(
                    "cover",
                    "PWSREDCOVERMIKE",
                    "visually-covered-page-content",
                    ("text-extraction", "decoded-streams"),
                    (5,),
                ),
            ),
            "PWSKEEPCOVER",
            negative=True,
        ),
        RedactionCase(
            "png-redaction-scan",
            ("scanned-text", "raster-source"),
            (
                canary(
                    "png",
                    "PWSREDPNGOSCAR",
                    "png-pixels",
                    ("decoded-image-pixels", "ocr"),
                    (),
                    keep="PWSKEEPPNG",
                ),
            ),
            "accept",
            None,
            False,
            (RedactionRegion(0, 0.02, 0.14, 0.98, 0.34),),
            ("PWSKEEPPNG",),
        ),
    ]
    # Every geometry page has a separate outside-region preservation oracle.
    geometry = next(item for item in cases if item.fixture_id == "pdf-redaction-geometry")
    cases[cases.index(geometry)] = RedactionCase(
        geometry.fixture_id,
        geometry.classes,
        geometry.canaries,
        geometry.expected_open,
        geometry.expected_error_code,
        geometry.negative_control,
        geometry.target_regions,
        tuple("PWSKEEPGEO" + suffix for suffix in ("ZERO", "ONE", "TWO", "THREE")),
    )
    return tuple(sorted(cases, key=lambda item: item.fixture_id))


def build_canary_inventory() -> dict[str, JsonValue]:
    """Return the closed v1 inventory authenticated by the fixture validator."""

    fixtures = {fixture.fixture_id: fixture for fixture in build_redaction_fixtures()}
    entries: list[JsonValue] = []
    for case in redaction_cases():
        fixture = fixtures[case.fixture_id]
        entries.append(
            {
                "fixture_id": case.fixture_id,
                "fixture_path": fixture.relative_path.as_posix(),
                "fixture_sha256": hashlib.sha256(fixture.content).hexdigest(),
                "classes": list(case.classes),
                "expected_open": case.expected_open,
                "expected_error_code": case.expected_error_code,
                "negative_control": case.negative_control,
                "preserve_tokens": list(case.preserve_tokens),
                "target_regions": [
                    {
                        "page_index": region.page_index,
                        "box": ",".join(
                            str(value) for value in (region.x0, region.y0, region.x1, region.y1)
                        ),
                    }
                    for region in case.target_regions
                ],
                "canaries": [
                    {
                        "id": canary.canary_id,
                        "token": canary.token,
                        "carrier": canary.carrier,
                        "page_index": canary.page_index,
                        "object_numbers": list(canary.object_numbers),
                        "extraction_methods": list(canary.extraction_methods),
                        "pixel_sha256": canary.pixel_sha256,
                    }
                    for canary in case.canaries
                ],
            }
        )
    return {
        "schema_version": 1,
        "corpus_id": REDACTION_CORPUS_ID,
        "task_id": "PWS-0601",
        "classification": "public-synthetic-only",
        "fixture_root": CORPUS_DIRECTORY.as_posix(),
        "external_source_material": False,
        "redaction_feature_enabled": False,
        "successful_removal_claimed": False,
        "region_space": "normalized-top-left-crop-before-intrinsic-rotation",
        "region_encoding": "CSV x0,y0,x1,y1 decimal ratios; parse exactly four finite values",
        "pixel_digest_format": "sha256-decoded-gray8-row-major",
        "raster_recipe": {
            "width": RASTER_WIDTH,
            "height": RASTER_HEIGHT,
            "glyph_scale": RASTER_GLYPH_SCALE,
            "glyph_advance": 48,
            "target_origin": "24,48",
            "keep_origin": "24,208",
        },
        "cases": entries,
    }


def canonical_canary_inventory_bytes() -> bytes:
    """Use the repository's stable UTF-8 JSON formatting profile."""

    return (json.dumps(build_canary_inventory(), ensure_ascii=True, indent=2) + "\n").encode(
        "utf-8"
    )

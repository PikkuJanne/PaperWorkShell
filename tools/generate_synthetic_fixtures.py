"""Generate the deterministic, public-only PaperWorkShell document corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import struct
import sys
import zlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, Final

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
CORPUS_DIRECTORY: Final = Path("tests/fixtures/synthetic/v1")
INDEX_PATH: Final = CORPUS_DIRECTORY / "index.json"
GOLDEN_DIRECTORY: Final = Path("tests/golden/renderer-v1")
GOLDEN_INDEX_PATH: Final = GOLDEN_DIRECTORY / "index.json"
SCHEMA_PATH: Final = Path("schemas/synthetic-fixture-index-v1.schema.json")
SYNTHETIC_MARKER: Final = "PWS-SYNTHETIC-FIXTURE"
CORPUS_ID: Final = "paperworkshell-public-synthetic-v1"
GENERATOR_ID: Final = "paperworkshell-stdlib-fixtures"
GENERATOR_VERSION: Final = 1
OVERSIZED_PDF_DEFAULT_BYTES: Final = 52_428_801
OVERSIZED_PDF_MAX_BYTES: Final = 268_435_456
LONG_PATH_COMPONENT: Final = "pws-synthetic-long-path-segment"
LONG_PATH_COMPONENT_COUNT: Final = 8
MAX_COMMITTED_OUTPUT_ENTRIES: Final = 256

type JsonScalar = str | int | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


class FixtureGenerationError(Exception):
    """A stable generation failure that is safe to show to a developer."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class GeneratedFixture:
    """One deterministic committed fixture and its reviewed metadata."""

    fixture_id: str
    relative_path: PurePosixPath
    media_type: str
    categories: tuple[str, ...]
    recipe: str
    expected: Mapping[str, JsonValue]
    content: bytes


@dataclass(frozen=True)
class GeneratedGoldenReference:
    """One deterministic analytical renderer oracle mirrored from the public corpus."""

    reference_id: str
    relative_path: PurePosixPath
    source_fixture_id: str
    reference_fixture_id: str
    page_index: int
    pixels_per_point: int
    width: int
    height: int
    maximum_channel_delta: int
    content: bytes


def _pdf_literal(text: str) -> bytes:
    encoded = text.encode("ascii")
    return encoded.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _pdf_stream(content: bytes, attributes: bytes = b"") -> bytes:
    separator = b" " if attributes else b""
    return (
        b"<< /Length "
        + str(len(content)).encode("ascii")
        + separator
        + attributes
        + b" >>\nstream\n"
        + content
        + b"\nendstream"
    )


def _serialize_pdf(
    objects: Sequence[bytes],
    *,
    root_object: int = 1,
    info_object: int | None = None,
    trailer_entries: bytes = b"",
    header_comment: bytes = b"",
) -> bytes:
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    if header_comment:
        output.extend(b"% " + header_comment + b"\n")
    offsets = [0]
    for object_number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{object_number} 0 obj\n".encode("ascii"))
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    trailer = f"trailer\n<< /Size {len(objects) + 1} /Root {root_object} 0 R".encode("ascii")
    if info_object is not None:
        trailer += f" /Info {info_object} 0 R".encode("ascii")
    if trailer_entries:
        trailer += b" " + trailer_entries
    output.extend(trailer + b" >>\n")
    output.extend(f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii"))
    return bytes(output)


def _text_content(label: str) -> bytes:
    return b"BT /F1 12 Tf 54 738 Td (" + _pdf_literal(label) + b") Tj ET"


def _text_pdf(label: str) -> bytes:
    content = _text_content(label)
    marker = f"{SYNTHETIC_MARKER}: algorithmic PDF; no external source material"
    return _serialize_pdf(
        (
            b"<< /Type /Catalog /Pages 2 0 R /Lang (en-US) >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
            ),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            _pdf_stream(content),
            b"<< /Producer (" + _pdf_literal(marker) + b") /Title (Public synthetic fixture) >>",
        ),
        info_object=6,
    )


def _stored_zlib(content: bytes) -> bytes:
    output = bytearray(b"\x78\x01")
    if not content:
        output.extend(b"\x01\x00\x00\xff\xff")
    else:
        cursor = 0
        while cursor < len(content):
            block = content[cursor : cursor + 65_535]
            cursor += len(block)
            output.append(1 if cursor == len(content) else 0)
            output.extend(struct.pack("<H", len(block)))
            output.extend(struct.pack("<H", 0xFFFF ^ len(block)))
            output.extend(block)
    output.extend(struct.pack(">I", zlib.adler32(content) & 0xFFFFFFFF))
    return bytes(output)


def _checkerboard_rgb(width: int, height: int) -> bytes:
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            if (x + y) % 2:
                pixels.extend((38, 88, 148))
            else:
                pixels.extend((222, 230, 238))
    return bytes(pixels)


def _image_only_pdf() -> bytes:
    width = 8
    height = 8
    compressed = _stored_zlib(_checkerboard_rgb(width, height))
    image = _pdf_stream(
        compressed,
        (
            b"/Type /XObject /Subtype /Image /Width 8 /Height 8 "
            b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode"
        ),
    )
    paint = b"q 192 0 0 192 210 300 cm /Im0 Do Q"
    return _serialize_pdf(
        (
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>"
            ),
            image,
            _pdf_stream(paint),
            b"<< /Producer (" + _pdf_literal(SYNTHETIC_MARKER) + b") >>",
        ),
        info_object=6,
    )


def _mixed_pdf() -> bytes:
    compressed = _stored_zlib(_checkerboard_rgb(8, 8))
    return _serialize_pdf(
        (
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /Font << /F1 5 0 R >> >> /Contents 7 0 R >>"
            ),
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /XObject << /Im0 6 0 R >> >> /Contents 8 0 R >>"
            ),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            _pdf_stream(
                compressed,
                (
                    b"/Type /XObject /Subtype /Image /Width 8 /Height 8 "
                    b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode"
                ),
            ),
            _pdf_stream(_text_content(f"{SYNTHETIC_MARKER}: born-digital page")),
            _pdf_stream(b"q 192 0 0 192 210 300 cm /Im0 Do Q"),
            b"<< /Producer (" + _pdf_literal(SYNTHETIC_MARKER) + b") >>",
        ),
        info_object=9,
    )


def _geometry_pdf() -> bytes:
    content_a = _text_content(f"{SYNTHETIC_MARKER}: rotated and cropped A4 page")
    content_b = _text_content(f"{SYNTHETIC_MARKER}: letter-size page")
    return _serialize_pdf(
        (
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                b"/CropBox [24 36 571 806] /Rotate 90 "
                b"/Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>"
            ),
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /Font << /F1 5 0 R >> >> /Contents 7 0 R >>"
            ),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            _pdf_stream(content_a),
            _pdf_stream(content_b),
            b"<< /Producer (" + _pdf_literal(SYNTHETIC_MARKER) + b") >>",
        ),
        info_object=8,
    )


def _render_calibration_pdf() -> bytes:
    page_one = b"\n".join(
        (
            b"1 1 1 rg 0 0 80 60 re f",
            b"1 0 0 rg 10 10 30 20 re f",
            b"0 1 0 rg 40 10 30 20 re f",
            b"0 0 1 rg 10 30 30 20 re f",
            b"1 1 0 rg 40 30 30 20 re f",
        )
    )
    page_two = b"\n".join(
        (
            b"1 1 1 rg 0 0 72 96 re f",
            b"0 0 0 rg 0 0 36 48 re f",
            b"0 1 1 rg 36 0 36 48 re f",
            b"1 0 1 rg 0 48 36 48 re f",
            b"1 1 0 rg 36 48 36 48 re f",
        )
    )
    marker = f"{SYNTHETIC_MARKER}: font-free analytical renderer calibration"
    return _serialize_pdf(
        (
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 80 60] "
                b"/CropBox [10 10 70 50] /Rotate 90 /Resources << >> /Contents 5 0 R >>"
            ),
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 96] "
                b"/Resources << >> /Contents 6 0 R >>"
            ),
            _pdf_stream(page_one),
            _pdf_stream(page_two),
            b"<< /Producer (" + _pdf_literal(marker) + b") >>",
        ),
        info_object=7,
    )


def _render_transparency_pdf() -> bytes:
    content = b"\n".join(
        (
            b"1 1 1 rg 0 0 64 64 re f",
            b"0 0 1 rg 8 8 40 40 re f",
            b"q /GS1 gs 1 0 0 rg 24 24 32 32 re f Q",
        )
    )
    marker = f"{SYNTHETIC_MARKER}: font-free PDF transparency calibration"
    return _serialize_pdf(
        (
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 64 64] "
                b"/Resources << /ExtGState << /GS1 4 0 R >> >> /Contents 5 0 R >>"
            ),
            b"<< /Type /ExtGState /ca 0.5 /CA 0.5 >>",
            _pdf_stream(content),
            b"<< /Producer (" + _pdf_literal(marker) + b") >>",
        ),
        info_object=6,
    )


def _render_cmyk_pdf() -> bytes:
    image_pixels = bytes(
        (
            255,
            0,
            0,
            0,
            0,
            255,
            0,
            0,
            0,
            0,
            255,
            0,
            0,
            0,
            0,
            255,
        )
    )
    image = _pdf_stream(
        _stored_zlib(image_pixels),
        (
            b"/Type /XObject /Subtype /Image /Width 4 /Height 1 "
            b"/ColorSpace /DeviceCMYK /BitsPerComponent 8 /Interpolate false "
            b"/Filter /FlateDecode"
        ),
    )
    content = b"\n".join(
        (
            b"0 0 0 0 k 0 0 64 64 re f",
            b"1 0 0 0 k 0 32 16 32 re f",
            b"0 1 0 0 k 16 32 16 32 re f",
            b"0 0 1 0 k 32 32 16 32 re f",
            b"0 0 0 1 k 48 32 16 32 re f",
            b"q 64 0 0 16 0 0 cm /Im0 Do Q",
        )
    )
    marker = f"{SYNTHETIC_MARKER}: font-free DeviceCMYK renderer calibration"
    return _serialize_pdf(
        (
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 64 64] "
                b"/Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>"
            ),
            image,
            _pdf_stream(content),
            b"<< /Producer (" + _pdf_literal(marker) + b") >>",
        ),
        info_object=6,
    )


def _render_high_dimension_pdf() -> bytes:
    marker = f"{SYNTHETIC_MARKER}: high-dimension renderer limit calibration"
    return _serialize_pdf(
        (
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 14400 14400] "
                b"/Resources << >> /Contents 4 0 R >>"
            ),
            _pdf_stream(b"0 0 0 rg 0 0 72 72 re f"),
            b"<< /Producer (" + _pdf_literal(marker) + b") >>",
        ),
        info_object=5,
    )


def _blank_pdf() -> bytes:
    return _serialize_pdf(
        (
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << >> >>",
            b"<< /Producer (" + _pdf_literal(SYNTHETIC_MARKER) + b") >>",
        ),
        info_object=4,
    )


def _signed_marker_pdf() -> bytes:
    content = _text_content(f"{SYNTHETIC_MARKER}: unsigned signature-field marker")
    return _serialize_pdf(
        (
            b"<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [6 0 R] /SigFlags 1 >> >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R /Annots [6 0 R] >>"
            ),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            _pdf_stream(content),
            (
                b"<< /Type /Annot /Subtype /Widget /FT /Sig "
                b"/T (PWS unsigned signature marker) /Rect [72 650 260 690] /P 3 0 R /F 4 >>"
            ),
            b"<< /Producer (" + _pdf_literal(SYNTHETIC_MARKER) + b") >>",
        ),
        info_object=7,
    )


def _attachment_pdf() -> bytes:
    attachment = (SYNTHETIC_MARKER + ": inert attachment payload\n").encode("ascii")
    content = _text_content(f"{SYNTHETIC_MARKER}: attachment carrier")
    return _serialize_pdf(
        (
            (
                b"<< /Type /Catalog /Pages 2 0 R /Names << /EmbeddedFiles "
                b"<< /Names [(synthetic-note.txt) 7 0 R] >> >> >>"
            ),
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
            ),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            _pdf_stream(content),
            _pdf_stream(attachment, b"/Type /EmbeddedFile /Subtype /text#2Fplain"),
            (
                b"<< /Type /Filespec /F (synthetic-note.txt) /UF (synthetic-note.txt) "
                b"/Desc (Public synthetic attachment) /EF << /F 6 0 R >> >>"
            ),
            b"<< /Producer (" + _pdf_literal(SYNTHETIC_MARKER) + b") >>",
        ),
        info_object=8,
    )


def _annotation_pdf() -> bytes:
    content = _text_content(f"{SYNTHETIC_MARKER}: annotation carrier")
    return _serialize_pdf(
        (
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R /Annots [6 0 R] >>"
            ),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            _pdf_stream(content),
            (
                b"<< /Type /Annot /Subtype /Text /Rect [72 650 92 670] "
                b"/Contents (PWS synthetic annotation marker) /F 4 >>"
            ),
            b"<< /Producer (" + _pdf_literal(SYNTHETIC_MARKER) + b") >>",
        ),
        info_object=7,
    )


def _script_marker_pdf() -> bytes:
    content = _text_content(f"{SYNTHETIC_MARKER}: dormant script-token carrier")
    return _serialize_pdf(
        (
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
            ),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            _pdf_stream(content),
            (
                b"<< /S /JavaScript /JS (PWS synthetic inert marker; intentionally orphaned) "
                b"/PWSInert true >>"
            ),
            b"<< /Producer (" + _pdf_literal(SYNTHETIC_MARKER) + b") >>",
        ),
        info_object=7,
    )


def _action_marker_pdf() -> bytes:
    content = _text_content(f"{SYNTHETIC_MARKER}: inert action carrier")
    return _serialize_pdf(
        (
            b"<< /Type /Catalog /Pages 2 0 R /OpenAction 6 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
            ),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            _pdf_stream(content),
            b"<< /S /GoTo /D [3 0 R /Fit] /PWSInert true >>",
            b"<< /Producer (" + _pdf_literal(SYNTHETIC_MARKER) + b") >>",
        ),
        info_object=7,
    )


_PDF_PASSWORD_PADDING: Final = bytes.fromhex(
    "28bf4e5e4e758a4164004e56fffa01082e2e00b6d0683e802f0ca9fe6453697a"
)


def _pad_pdf_password(password: bytes) -> bytes:
    return (password + _PDF_PASSWORD_PADDING)[:32]


def _rc4(key: bytes, content: bytes) -> bytes:
    state = list(range(256))
    cursor = 0
    for index in range(256):
        cursor = (cursor + state[index] + key[index % len(key)]) & 0xFF
        state[index], state[cursor] = state[cursor], state[index]
    output = bytearray()
    left = 0
    right = 0
    for value in content:
        left = (left + 1) & 0xFF
        right = (right + state[left]) & 0xFF
        state[left], state[right] = state[right], state[left]
        output.append(value ^ state[(state[left] + state[right]) & 0xFF])
    return bytes(output)


def _pdf_rc4_object_key(file_key: bytes, object_number: int) -> bytes:
    material = file_key + object_number.to_bytes(3, "little") + b"\x00\x00"
    return hashlib.md5(material, usedforsecurity=False).digest()[: min(len(file_key) + 5, 16)]


def _protected_pdf() -> bytes:
    """Return a deterministic password-required Standard Security Handler fixture."""

    user_password = _pad_pdf_password(b"locked")
    owner_password = _pad_pdf_password(b"owner")
    owner_key = hashlib.md5(owner_password, usedforsecurity=False).digest()[:5]
    owner_entry = _rc4(owner_key, user_password)
    permissions = -4
    document_id = hashlib.sha256(
        f"{SYNTHETIC_MARKER}: deterministic protected PDF".encode("ascii")
    ).digest()[:16]
    file_key = hashlib.md5(
        user_password + owner_entry + struct.pack("<i", permissions) + document_id,
        usedforsecurity=False,
    ).digest()[:5]
    user_entry = _rc4(file_key, _PDF_PASSWORD_PADDING)
    content = _text_content(f"{SYNTHETIC_MARKER}: password-required carrier")
    encrypted_content = _rc4(_pdf_rc4_object_key(file_key, 5), content)
    encrypt_dictionary = (
        b"<< /Filter /Standard /V 1 /R 2 /Length 40 /P -4 /O <"
        + owner_entry.hex().encode("ascii")
        + b"> /U <"
        + user_entry.hex().encode("ascii")
        + b"> >>"
    )
    identifier = document_id.hex().encode("ascii")
    return _serialize_pdf(
        (
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
            ),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            _pdf_stream(encrypted_content),
            encrypt_dictionary,
        ),
        trailer_entries=b"/Encrypt 6 0 R /ID [<" + identifier + b"><" + identifier + b">]",
        header_comment=f"{SYNTHETIC_MARKER}: deterministic protected fixture".encode("ascii"),
    )


def _incremental_pdf() -> bytes:
    base = _text_pdf(f"{SYNTHETIC_MARKER}: incremental base revision")
    previous_xref = int(base.rsplit(b"startxref\n", maxsplit=1)[1].splitlines()[0])
    object_number = 7
    object_offset = len(base)
    appended = bytearray()
    appended.extend(f"{object_number} 0 obj\n".encode("ascii"))
    appended.extend(
        b"<< /Producer ("
        + _pdf_literal(f"{SYNTHETIC_MARKER}: deterministic incremental revision")
        + b") >>\nendobj\n"
    )
    xref_offset = len(base) + len(appended)
    appended.extend(f"xref\n{object_number} 1\n".encode("ascii"))
    appended.extend(f"{object_offset:010d} 00000 n \n".encode("ascii"))
    appended.extend(
        (
            f"trailer\n<< /Size 8 /Root 1 0 R /Info 7 0 R /Prev {previous_xref} >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return base + bytes(appended)


def _malformed_pdf() -> bytes:
    return (
        b"%PDF-1.4\n% "
        + SYNTHETIC_MARKER.encode("ascii")
        + b": deliberately truncated parser fixture\n1 0 obj\n<< /Type /Catalog"
    )


def _malformed_page_tree_pdf() -> bytes:
    marker = f"{SYNTHETIC_MARKER}: complete framing with a missing page-tree object"
    return _serialize_pdf(
        (
            b"<< /Type /Catalog /Pages 99 0 R >>",
            b"<< /Producer (" + _pdf_literal(marker) + b") >>",
        ),
        info_object=2,
    )


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def _png(
    width: int,
    height: int,
    *,
    color_type: int,
    rows: Sequence[bytes],
    description: str,
) -> bytes:
    expected_channels = 4 if color_type == 6 else 3
    if len(rows) != height or any(len(row) != width * expected_channels for row in rows):
        raise AssertionError("invalid internal PNG row dimensions")
    raw = b"".join(b"\x00" + row for row in rows)
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)),
            _png_chunk(b"tEXt", b"Comment\x00" + description.encode("latin-1")),
            _png_chunk(b"IDAT", _stored_zlib(raw)),
            _png_chunk(b"IEND", b""),
        )
    )


def _transparent_png() -> bytes:
    rows: list[bytes] = []
    for y in range(16):
        row = bytearray()
        for x in range(16):
            row.extend((24 + x * 8, 72 + y * 6, 144, (x + y) * 8))
        rows.append(bytes(row))
    return _png(
        16,
        16,
        color_type=6,
        rows=rows,
        description=f"{SYNTHETIC_MARKER}: algorithmic transparency",
    )


def _render_calibration_crop_rotate_png() -> bytes:
    rows: list[bytes] = []
    for y in range(60):
        row = bytearray()
        for x in range(40):
            if y < 30:
                color = (255, 0, 0) if x < 20 else (0, 0, 255)
            else:
                color = (0, 255, 0) if x < 20 else (255, 255, 0)
            row.extend(color)
        rows.append(bytes(row))
    return _png(
        40,
        60,
        color_type=2,
        rows=rows,
        description=f"{SYNTHETIC_MARKER}: analytical crop and clockwise-rotation oracle",
    )


def _render_calibration_media_box_png() -> bytes:
    rows: list[bytes] = []
    for y in range(96):
        row = bytearray()
        for x in range(72):
            if y < 48:
                color = (255, 0, 255) if x < 36 else (255, 255, 0)
            else:
                color = (0, 0, 0) if x < 36 else (0, 255, 255)
            row.extend(color)
        rows.append(bytes(row))
    return _png(
        72,
        96,
        color_type=2,
        rows=rows,
        description=f"{SYNTHETIC_MARKER}: analytical MediaBox oracle",
    )


def _render_transparency_png() -> bytes:
    rows: list[bytes] = []
    for y in range(64):
        pdf_y = 63 - y
        row = bytearray()
        for x in range(64):
            blue = 8 <= x < 48 and 8 <= pdf_y < 48
            translucent_red = 24 <= x < 56 and 24 <= pdf_y < 56
            if translucent_red:
                color = (128, 0, 128) if blue else (255, 128, 128)
            elif blue:
                color = (0, 0, 255)
            else:
                color = (255, 255, 255)
            row.extend(color)
        rows.append(bytes(row))
    return _png(
        64,
        64,
        color_type=2,
        rows=rows,
        description=f"{SYNTHETIC_MARKER}: analytical PDF alpha-compositing oracle",
    )


def _low_resolution_png() -> bytes:
    pixels = _checkerboard_rgb(8, 8)
    rows = tuple(pixels[index : index + 24] for index in range(0, len(pixels), 24))
    return _png(
        8,
        8,
        color_type=2,
        rows=rows,
        description=f"{SYNTHETIC_MARKER}: algorithmic low-resolution raster",
    )


def _draw_line(
    pixels: bytearray,
    width: int,
    height: int,
    start: tuple[int, int],
    end: tuple[int, int],
) -> None:
    x0, y0 = start
    x1, y1 = end
    dx = abs(x1 - x0)
    sx = 1 if x0 < x1 else -1
    dy = -abs(y1 - y0)
    sy = 1 if y0 < y1 else -1
    error = dx + dy
    while True:
        if 0 <= x0 < width and 0 <= y0 < height:
            offset = (y0 * width + x0) * 3
            pixels[offset : offset + 3] = b"\x20\x4a\x75"
        if x0 == x1 and y0 == y1:
            break
        doubled = 2 * error
        if doubled >= dy:
            error += dy
            x0 += sx
        if doubled <= dx:
            error += dx
            y0 += sy


def _handwriting_demonstration_png() -> bytes:
    width = 192
    height = 64
    pixels = bytearray(b"\xf7\xf7\xf2" * width * height)
    points = ((12, 42), (28, 18), (40, 45), (58, 20), (72, 43), (92, 24), (112, 42))
    for start, end in zip(points, points[1:], strict=False):
        _draw_line(pixels, width, height, start, end)
    _draw_line(pixels, width, height, (10, 52), (180, 52))
    rows = tuple(
        bytes(pixels[index : index + width * 3]) for index in range(0, len(pixels), width * 3)
    )
    return _png(
        width,
        height,
        color_type=2,
        rows=rows,
        description=f"{SYNTHETIC_MARKER}: generic handwriting demo; NOT A SIGNATURE",
    )


def _fixture(
    fixture_id: str,
    path: str,
    media_type: str,
    categories: Sequence[str],
    recipe: str,
    expected: Mapping[str, JsonValue],
    generator: Callable[[], bytes],
) -> GeneratedFixture:
    return GeneratedFixture(
        fixture_id=fixture_id,
        relative_path=PurePosixPath(path),
        media_type=media_type,
        categories=tuple(categories),
        recipe=recipe,
        expected=expected,
        content=generator(),
    )


def build_committed_fixtures() -> tuple[GeneratedFixture, ...]:
    """Build every reviewed committed fixture without reading external input."""

    duplicate = _text_pdf(f"{SYNTHETIC_MARKER}: exact duplicate pair")
    fixtures = (
        _fixture(
            "pdf-born-digital",
            "pdf/born-digital.pdf",
            "application/pdf",
            ("born-digital",),
            "text-pdf-v1",
            {"page_count": 1, "has_text_operator": True},
            lambda: _text_pdf(f"{SYNTHETIC_MARKER}: born-digital text"),
        ),
        _fixture(
            "pdf-unicode-named",
            "pdf/unicode-named-café.pdf",
            "application/pdf",
            ("unicode",),
            "unicode-named-path-pdf-v1",
            {"page_count": 1, "path_is_nfc": True, "rendered_unicode_content": False},
            lambda: _text_pdf(f"{SYNTHETIC_MARKER}: Unicode-named path carrier"),
        ),
        _fixture(
            "pdf-image-only",
            "pdf/image-only.pdf",
            "application/pdf",
            ("image-only",),
            "image-only-pdf-v1",
            {"page_count": 1, "image_xobject_count": 1, "has_text_operator": False},
            _image_only_pdf,
        ),
        _fixture(
            "pdf-mixed",
            "pdf/mixed.pdf",
            "application/pdf",
            ("mixed",),
            "mixed-pdf-v1",
            {"page_count": 2, "text_page_count": 1, "image_page_count": 1},
            _mixed_pdf,
        ),
        _fixture(
            "pdf-page-geometry",
            "pdf/page-geometry.pdf",
            "application/pdf",
            ("crop", "page-size", "rotated"),
            "geometry-pdf-v1",
            {"page_count": 2, "rotation_degrees": 90, "distinct_media_boxes": 2},
            _geometry_pdf,
        ),
        _fixture(
            "pdf-render-calibration",
            "pdf/render-calibration.pdf",
            "application/pdf",
            ("crop", "page-size", "rotated"),
            "render-calibration-pdf-v1",
            {
                "page_count": 2,
                "font_resource_count": 0,
                "page_one_crop_box": "10 10 70 50",
                "page_one_rotation_degrees": 90,
                "page_two_media_box": "0 0 72 96",
            },
            _render_calibration_pdf,
        ),
        _fixture(
            "pdf-render-transparency",
            "pdf/render-transparency.pdf",
            "application/pdf",
            ("transparency",),
            "render-transparency-pdf-v1",
            {
                "page_count": 1,
                "font_resource_count": 0,
                "ext_gstate_count": 1,
                "fill_alpha_numerator": 1,
                "fill_alpha_denominator": 2,
            },
            _render_transparency_pdf,
        ),
        _fixture(
            "pdf-render-cmyk",
            "pdf/render-cmyk.pdf",
            "application/pdf",
            ("cmyk",),
            "render-cmyk-pdf-v1",
            {
                "page_count": 1,
                "font_resource_count": 0,
                "device_cmyk_vector_swatches": 4,
                "device_cmyk_image_pixels": 4,
                "image_interpolation": False,
            },
            _render_cmyk_pdf,
        ),
        _fixture(
            "pdf-render-high-dimension",
            "pdf/render-high-dimension.pdf",
            "application/pdf",
            ("page-size",),
            "render-high-dimension-pdf-v1",
            {
                "page_count": 1,
                "media_box_width_points": 14400,
                "media_box_height_points": 14400,
                "pixels_at_96_dpi": 368640000,
            },
            _render_high_dimension_pdf,
        ),
        _fixture(
            "pdf-blank",
            "pdf/blank.pdf",
            "application/pdf",
            ("blank",),
            "blank-pdf-v1",
            {"page_count": 1, "content_stream_count": 0},
            _blank_pdf,
        ),
        GeneratedFixture(
            "pdf-duplicate-a",
            PurePosixPath("pdf/duplicate-a.pdf"),
            "application/pdf",
            ("duplicate",),
            "duplicate-pdf-v1",
            {"duplicate_group": "pdf-exact-pair-v1"},
            duplicate,
        ),
        GeneratedFixture(
            "pdf-duplicate-b",
            PurePosixPath("pdf/duplicate-b.pdf"),
            "application/pdf",
            ("duplicate",),
            "duplicate-pdf-v1",
            {"duplicate_group": "pdf-exact-pair-v1"},
            duplicate,
        ),
        _fixture(
            "pdf-signed-marker",
            "pdf/signed-marker.pdf",
            "application/pdf",
            ("signed-marker",),
            "unsigned-signature-field-pdf-v1",
            {"page_count": 1, "signature_field_marker": True, "cryptographically_signed": False},
            _signed_marker_pdf,
        ),
        _fixture(
            "pdf-attachment",
            "pdf/attachment.pdf",
            "application/pdf",
            ("attachment",),
            "attachment-pdf-v1",
            {"page_count": 1, "embedded_file_count": 1},
            _attachment_pdf,
        ),
        _fixture(
            "pdf-annotation",
            "pdf/annotation.pdf",
            "application/pdf",
            ("annotation",),
            "annotation-pdf-v1",
            {"page_count": 1, "annotation_count": 1},
            _annotation_pdf,
        ),
        _fixture(
            "pdf-script-marker",
            "pdf/script-marker.pdf",
            "application/pdf",
            ("script-marker",),
            "orphan-script-marker-pdf-v1",
            {"page_count": 1, "script_marker_count": 1, "script_reachable_from_catalog": False},
            _script_marker_pdf,
        ),
        _fixture(
            "pdf-action-marker",
            "pdf/action-marker.pdf",
            "application/pdf",
            ("script-marker",),
            "catalog-action-marker-pdf-v1",
            {"page_count": 1, "action_marker_count": 1, "javascript_marker_count": 0},
            _action_marker_pdf,
        ),
        _fixture(
            "pdf-protected",
            "pdf/password-required.pdf",
            "application/pdf",
            ("protected",),
            "standard-security-handler-r2-pdf-v1",
            {
                "password_required": True,
                "test_only_weak_encryption": True,
                "user_password_fixture_token": "locked",
            },
            _protected_pdf,
        ),
        _fixture(
            "pdf-malformed",
            "pdf/malformed-truncated.pdf",
            "application/pdf",
            ("malformed",),
            "truncated-pdf-v1",
            {"parse_expected": False, "truncated": True},
            _malformed_pdf,
        ),
        _fixture(
            "pdf-render-malformed-page-tree",
            "pdf/render-malformed-page-tree.pdf",
            "application/pdf",
            ("malformed",),
            "render-malformed-page-tree-pdf-v1",
            {
                "parse_expected": False,
                "header_complete": True,
                "xref_complete": True,
                "missing_pages_object": 99,
            },
            _malformed_page_tree_pdf,
        ),
        _fixture(
            "pdf-incremental",
            "pdf/incremental-update.pdf",
            "application/pdf",
            ("incremental",),
            "incremental-pdf-v1",
            {"page_count": 1, "revision_count": 2, "prev_trailer_count": 1},
            _incremental_pdf,
        ),
        _fixture(
            "png-transparent",
            "image/transparent.png",
            "image/png",
            ("transparency",),
            "transparent-png-v1",
            {"width": 16, "height": 16, "color_type": 6},
            _transparent_png,
        ),
        _fixture(
            "png-render-calibration-crop-rotate",
            "image/render-calibration-crop-rotate.png",
            "image/png",
            ("crop", "page-size", "rotated"),
            "render-calibration-crop-rotate-png-v1",
            {
                "width": 40,
                "height": 60,
                "color_type": 2,
                "golden_for_fixture": "pdf-render-calibration",
                "page_index": 0,
            },
            _render_calibration_crop_rotate_png,
        ),
        _fixture(
            "png-render-calibration-media-box",
            "image/render-calibration-media-box.png",
            "image/png",
            ("page-size",),
            "render-calibration-media-box-png-v1",
            {
                "width": 72,
                "height": 96,
                "color_type": 2,
                "golden_for_fixture": "pdf-render-calibration",
                "page_index": 1,
            },
            _render_calibration_media_box_png,
        ),
        _fixture(
            "png-render-transparency",
            "image/render-transparency.png",
            "image/png",
            ("transparency",),
            "render-transparency-png-v1",
            {
                "width": 64,
                "height": 64,
                "color_type": 2,
                "golden_for_fixture": "pdf-render-transparency",
                "page_index": 0,
                "maximum_channel_delta": 1,
            },
            _render_transparency_png,
        ),
        _fixture(
            "png-low-resolution",
            "image/low-resolution.png",
            "image/png",
            ("low-resolution",),
            "low-resolution-png-v1",
            {"width": 8, "height": 8, "color_type": 2},
            _low_resolution_png,
        ),
        _fixture(
            "png-handwriting-demonstration",
            "image/handwriting-demonstration.png",
            "image/png",
            ("handwriting-demonstration",),
            "handwriting-demonstration-png-v1",
            {"width": 192, "height": 64, "is_signature": False},
            _handwriting_demonstration_png,
        ),
    )
    return tuple(sorted(fixtures, key=lambda item: item.fixture_id))


def _coverage() -> list[JsonValue]:
    committed: dict[str, list[str]] = {}
    for fixture in build_committed_fixtures():
        for category in fixture.categories:
            committed.setdefault(category, []).append(fixture.fixture_id)
    scheduled = {
        "multilingual": ("PWS-0303", "font-engine-required"),
        "rtl": ("PWS-0704", "rtl-layout-engine-required"),
    }
    required_categories = (
        "annotation",
        "attachment",
        "blank",
        "born-digital",
        "cmyk",
        "crop",
        "duplicate",
        "handwriting-demonstration",
        "image-only",
        "incremental",
        "long-path",
        "low-resolution",
        "malformed",
        "mixed",
        "multilingual",
        "page-size",
        "protected",
        "rotated",
        "rtl",
        "script-marker",
        "signed-marker",
        "transparency",
        "unicode",
        "very-large",
    )
    coverage: list[JsonValue] = []
    for category in required_categories:
        if category in committed:
            fixture_ids: list[JsonValue] = [
                fixture_id for fixture_id in sorted(committed[category])
            ]
            coverage.append(
                {
                    "category": category,
                    "status": "committed",
                    "fixture_ids": fixture_ids,
                }
            )
        elif category == "long-path":
            coverage.append(
                {
                    "category": category,
                    "status": "on-demand",
                    "recipe_id": "long-path-tree-v1",
                }
            )
        elif category == "very-large":
            coverage.append(
                {
                    "category": category,
                    "status": "on-demand",
                    "recipe_id": "oversized-pdf-v1",
                }
            )
        else:
            task_id, reason_code = scheduled[category]
            coverage.append(
                {
                    "category": category,
                    "status": "scheduled",
                    "scheduled_task": task_id,
                    "reason_code": reason_code,
                }
            )
    return coverage


def build_index(fixtures: Sequence[GeneratedFixture] | None = None) -> dict[str, JsonValue]:
    """Return the canonical metadata index for the generated fixture bytes."""

    selected = tuple(fixtures) if fixtures is not None else build_committed_fixtures()
    fixture_entries: list[JsonValue] = []
    for fixture in selected:
        fixture_entries.append(
            {
                "id": fixture.fixture_id,
                "path": fixture.relative_path.as_posix(),
                "media_type": fixture.media_type,
                "bytes": len(fixture.content),
                "sha256": hashlib.sha256(fixture.content).hexdigest(),
                "categories": list(fixture.categories),
                "recipe": fixture.recipe,
                "construction_assertions": dict(fixture.expected),
                "provenance": {
                    "kind": "algorithmic",
                    "source_material": "none",
                    "contains_personal_data": False,
                    "review_decision": "PWS-D035",
                },
            }
        )
    return {
        "schema_version": 1,
        "corpus_id": CORPUS_ID,
        "generator": {"id": GENERATOR_ID, "version": GENERATOR_VERSION},
        "policy": {
            "classification": "public-synthetic-only",
            "marker": SYNTHETIC_MARKER,
            "external_source_material": False,
            "decision_ref": "PWS-D035",
        },
        "coverage": _coverage(),
        "fixtures": fixture_entries,
        "on_demand_recipes": [
            {
                "id": "long-path-tree-v1",
                "kind": "long-path",
                "committed": False,
                "default_component_count": LONG_PATH_COMPONENT_COUNT,
                "expected_minimum_path_characters": 260,
            },
            {
                "id": "oversized-pdf-v1",
                "kind": "large-hostile",
                "committed": False,
                "default_bytes": OVERSIZED_PDF_DEFAULT_BYTES,
                "maximum_bytes": OVERSIZED_PDF_MAX_BYTES,
            },
        ],
    }


def canonical_index_bytes(fixtures: Sequence[GeneratedFixture] | None = None) -> bytes:
    """Serialize the index with one stable formatting profile."""

    return (json.dumps(build_index(fixtures), ensure_ascii=True, indent=2) + "\n").encode("utf-8")


def build_renderer_golden_references(
    fixtures: Sequence[GeneratedFixture] | None = None,
) -> tuple[GeneratedGoldenReference, ...]:
    """Build the exact renderer oracles mirrored beneath ``tests/golden``."""

    selected = tuple(fixtures) if fixtures is not None else build_committed_fixtures()
    by_id = {fixture.fixture_id: fixture for fixture in selected}
    specifications = (
        (
            "render-calibration-crop-rotate",
            "render-calibration-crop-rotate.png",
            "pdf-render-calibration",
            "png-render-calibration-crop-rotate",
            0,
            40,
            60,
            0,
        ),
        (
            "render-calibration-media-box",
            "render-calibration-media-box.png",
            "pdf-render-calibration",
            "png-render-calibration-media-box",
            1,
            72,
            96,
            0,
        ),
        (
            "render-transparency",
            "render-transparency.png",
            "pdf-render-transparency",
            "png-render-transparency",
            0,
            64,
            64,
            1,
        ),
    )
    references: list[GeneratedGoldenReference] = []
    for (
        reference_id,
        path,
        source_fixture_id,
        reference_fixture_id,
        page_index,
        width,
        height,
        maximum_channel_delta,
    ) in specifications:
        try:
            source = by_id[source_fixture_id]
            reference = by_id[reference_fixture_id]
        except KeyError as error:
            raise AssertionError("renderer golden source fixture is unavailable") from error
        if source.media_type != "application/pdf" or reference.media_type != "image/png":
            raise AssertionError("renderer golden media types are invalid")
        references.append(
            GeneratedGoldenReference(
                reference_id=reference_id,
                relative_path=PurePosixPath(path),
                source_fixture_id=source_fixture_id,
                reference_fixture_id=reference_fixture_id,
                page_index=page_index,
                pixels_per_point=1,
                width=width,
                height=height,
                maximum_channel_delta=maximum_channel_delta,
                content=reference.content,
            )
        )
    return tuple(sorted(references, key=lambda item: item.reference_id))


def build_renderer_golden_index(
    fixtures: Sequence[GeneratedFixture] | None = None,
) -> dict[str, JsonValue]:
    """Return the closed metadata contract for analytical renderer references."""

    selected = tuple(fixtures) if fixtures is not None else build_committed_fixtures()
    by_id = {fixture.fixture_id: fixture for fixture in selected}
    entries: list[JsonValue] = []
    for reference in build_renderer_golden_references(selected):
        source = by_id[reference.source_fixture_id]
        image = by_id[reference.reference_fixture_id]
        entries.append(
            {
                "id": reference.reference_id,
                "path": reference.relative_path.as_posix(),
                "source_fixture_id": reference.source_fixture_id,
                "source_sha256": hashlib.sha256(source.content).hexdigest(),
                "page_index": reference.page_index,
                "pixels_per_point": reference.pixels_per_point,
                "pixel_layout": "rgb8-top-down",
                "background": "opaque-white",
                "width": reference.width,
                "height": reference.height,
                "reference_fixture_id": reference.reference_fixture_id,
                "reference_sha256": hashlib.sha256(image.content).hexdigest(),
                "comparison": {
                    "mode": (
                        "exact" if reference.maximum_channel_delta == 0 else "bounded-channel-delta"
                    ),
                    "maximum_channel_delta": reference.maximum_channel_delta,
                },
            }
        )
    return {
        "schema_version": 1,
        "golden_set_id": "paperworkshell-pdf-renderer-analytical-v1",
        "source_corpus_id": CORPUS_ID,
        "policy": {
            "classification": "public-synthetic-only",
            "derivation": "stdlib-analytical-oracle",
            "captured_from_renderer": False,
            "decision_ref": "PWS-D035",
        },
        "references": entries,
    }


def canonical_renderer_golden_index_bytes(
    fixtures: Sequence[GeneratedFixture] | None = None,
) -> bytes:
    """Serialize the analytical renderer-reference index deterministically."""

    return (
        json.dumps(build_renderer_golden_index(fixtures), ensure_ascii=True, indent=2) + "\n"
    ).encode("utf-8")


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def _ensure_safe_directory_chain(repository_root: Path, relative_path: Path) -> Path:
    try:
        if _is_link_or_reparse(repository_root) or not repository_root.is_dir():
            raise OSError("repository root is unsafe")
        resolved_root = repository_root.resolve(strict=True)
        current = repository_root
        for component in relative_path.parts:
            current = current / component
            if os.path.lexists(current):
                if _is_link_or_reparse(current) or not current.is_dir():
                    raise OSError("output directory is unsafe")
            else:
                current.mkdir()
                if _is_link_or_reparse(current) or not current.is_dir():
                    raise OSError("created output directory is unsafe")
            resolved_current = current.resolve(strict=True)
            if not resolved_current.is_relative_to(resolved_root):
                raise OSError("output directory escapes repository")
        return current
    except OSError as error:
        raise FixtureGenerationError(
            "FIXTURE_OUTPUT_UNSAFE", "fixture output contains an unsafe directory path"
        ) from error


def _bounded_output_files(corpus_root: Path) -> set[Path]:
    files: set[Path] = set()
    stack = [corpus_root]
    entry_count = 0
    try:
        while stack:
            directory = stack.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    entry_count += 1
                    if entry_count > MAX_COMMITTED_OUTPUT_ENTRIES:
                        raise FixtureGenerationError(
                            "FIXTURE_OUTPUT_INVENTORY_MISMATCH",
                            "fixture output exceeds its reviewed entry limit",
                        )
                    candidate = Path(entry.path)
                    if _is_link_or_reparse(candidate):
                        raise OSError("output entry is unsafe")
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(candidate)
                    elif entry.is_file(follow_symlinks=False):
                        files.add(candidate.relative_to(corpus_root))
                    else:
                        raise OSError("output entry is not regular")
    except FixtureGenerationError:
        raise
    except OSError as error:
        raise FixtureGenerationError(
            "FIXTURE_OUTPUT_UNREADABLE", "fixture output cannot be inspected safely"
        ) from error
    return files


def _write_exact(path: Path, content: bytes, output_root: Path) -> None:
    try:
        resolved_output_root = output_root.resolve(strict=True)
        resolved_parent = path.parent.resolve(strict=True)
    except OSError as error:
        raise FixtureGenerationError(
            "FIXTURE_OUTPUT_UNSAFE", "fixture output parent cannot be resolved safely"
        ) from error
    if _is_link_or_reparse(path.parent) or not resolved_parent.is_relative_to(resolved_output_root):
        raise FixtureGenerationError(
            "FIXTURE_OUTPUT_UNSAFE", "fixture output parent escapes the reviewed corpus"
        )
    if os.path.lexists(path):
        try:
            metadata = path.lstat()
        except OSError as error:
            raise FixtureGenerationError(
                "FIXTURE_OUTPUT_UNSAFE", "fixture output cannot be inspected safely"
            ) from error
        if (
            _is_link_or_reparse(path)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
        ):
            raise FixtureGenerationError(
                "FIXTURE_OUTPUT_UNSAFE", "fixture output contains a non-regular path"
            )
    temporary = path.with_name(f".{path.name}.pws-fixture-tmp")
    if os.path.lexists(temporary):
        temporary_metadata = temporary.lstat()
        if (
            _is_link_or_reparse(temporary)
            or not stat.S_ISREG(temporary_metadata.st_mode)
            or temporary_metadata.st_nlink != 1
        ):
            raise FixtureGenerationError(
                "FIXTURE_OUTPUT_UNSAFE", "fixture output contains an unsafe temporary path"
            )
        temporary.unlink()
    try:
        with temporary.open("xb") as stream:
            written = stream.write(content)
        if written != len(content):
            raise OSError("short fixture output write")
        if (
            _is_link_or_reparse(temporary)
            or temporary.resolve(strict=True).parent != resolved_parent
            or path.parent.resolve(strict=True) != resolved_parent
        ):
            raise OSError("fixture output path changed")
        os.replace(temporary, path)
        if _is_link_or_reparse(path) or path.resolve(strict=True).parent != resolved_parent:
            raise OSError("fixture output replacement escaped")
    except OSError as error:
        raise FixtureGenerationError(
            "FIXTURE_OUTPUT_UNWRITABLE", "fixture output could not be written safely"
        ) from error
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _write_renderer_goldens(
    repository_root: Path,
    fixtures: Sequence[GeneratedFixture],
) -> int:
    references = build_renderer_golden_references(fixtures)
    golden_root = _ensure_safe_directory_chain(repository_root, GOLDEN_DIRECTORY)
    expected = {Path(*reference.relative_path.parts) for reference in references}
    expected.add(Path("index.json"))
    unexpected = _bounded_output_files(golden_root) - expected
    if unexpected:
        raise FixtureGenerationError(
            "FIXTURE_GOLDEN_INVENTORY_MISMATCH",
            "renderer golden output differs from its reviewed inventory",
        )
    for reference in references:
        relative = Path(*reference.relative_path.parts)
        _ensure_safe_directory_chain(repository_root, GOLDEN_DIRECTORY / relative.parent)
        _write_exact(golden_root / relative, reference.content, golden_root)
    _write_exact(
        golden_root / "index.json",
        canonical_renderer_golden_index_bytes(fixtures),
        golden_root,
    )
    return len(references)


def write_committed_corpus(root: Path = PROJECT_ROOT) -> int:
    """Regenerate the reviewed corpus and its analytical renderer references."""

    fixtures = build_committed_fixtures()
    try:
        repository_root = root.resolve(strict=True)
    except OSError as error:
        raise FixtureGenerationError(
            "FIXTURE_OUTPUT_UNSAFE", "repository root cannot be resolved safely"
        ) from error
    if _is_link_or_reparse(root) or not repository_root.is_dir():
        raise FixtureGenerationError(
            "FIXTURE_OUTPUT_UNSAFE", "repository root must be one non-linked directory"
        )
    corpus_root = _ensure_safe_directory_chain(repository_root, CORPUS_DIRECTORY)
    expected = {Path(*item.relative_path.parts) for item in fixtures}
    expected.add(Path("index.json"))
    actual = _bounded_output_files(corpus_root)
    unexpected = {
        path for path in actual - expected if not path.parts or path.parts[0] != "invalid"
    }
    if unexpected:
        raise FixtureGenerationError(
            "FIXTURE_OUTPUT_INVENTORY_MISMATCH",
            "fixture output has unexpected files; remove them after review",
        )
    for fixture in fixtures:
        relative = Path(*fixture.relative_path.parts)
        _ensure_safe_directory_chain(repository_root, CORPUS_DIRECTORY / relative.parent)
        _write_exact(corpus_root / relative, fixture.content, corpus_root)
    _write_exact(corpus_root / "index.json", canonical_index_bytes(fixtures), corpus_root)
    _write_renderer_goldens(repository_root, fixtures)
    return len(fixtures)


def _ensure_external_output(output_directory: Path, repository_root: Path) -> Path:
    if os.path.lexists(output_directory) and _is_link_or_reparse(output_directory):
        raise FixtureGenerationError(
            "FIXTURE_OUTPUT_UNSAFE", "on-demand output must be one non-linked directory"
        )
    try:
        repository = repository_root.resolve(strict=True)
        destination = output_directory.resolve(strict=False)
    except OSError as error:
        raise FixtureGenerationError(
            "FIXTURE_OUTPUT_UNREADABLE", "on-demand output cannot be resolved"
        ) from error
    if destination == repository or destination.is_relative_to(repository):
        raise FixtureGenerationError(
            "FIXTURE_OUTPUT_INSIDE_REPOSITORY",
            "on-demand hostile fixtures must remain outside the repository",
        )
    return destination


def _ensure_external_directory(output_directory: Path, repository_root: Path) -> Path:
    destination = _ensure_external_output(output_directory, repository_root)
    try:
        destination.mkdir(parents=True, exist_ok=True)
        if _is_link_or_reparse(destination) or not destination.is_dir():
            raise OSError("on-demand destination is unsafe")
        resolved_destination = destination.resolve(strict=True)
        repository = repository_root.resolve(strict=True)
    except OSError as error:
        raise FixtureGenerationError(
            "FIXTURE_OUTPUT_UNSAFE", "on-demand output contains an unsafe directory path"
        ) from error
    if resolved_destination == repository or resolved_destination.is_relative_to(repository):
        raise FixtureGenerationError(
            "FIXTURE_OUTPUT_INSIDE_REPOSITORY",
            "on-demand hostile fixtures must remain outside the repository",
        )
    return resolved_destination


def _remove_created_external_file(
    path: Path,
    expected_parent: Path,
    expected_identity: tuple[int, int] | None,
) -> None:
    if expected_identity is None:
        return
    try:
        metadata = path.lstat()
        if (
            path.parent.resolve(strict=True) != expected_parent
            or _is_link_or_reparse(path)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or (metadata.st_dev, metadata.st_ino) != expected_identity
        ):
            return
        path.unlink()
    except OSError:
        pass


def _write_oversized_payload(
    stream: IO[bytes],
    prefix: bytes,
    suffix: bytes,
    fill_bytes: int,
) -> None:
    stream.write(prefix)
    block = b"0" * min(1_048_576, fill_bytes)
    remaining = fill_bytes
    while remaining:
        chunk = block[: min(len(block), remaining)]
        stream.write(chunk)
        remaining -= len(chunk)
    stream.write(suffix)


def _write_external_bytes(stream: IO[bytes], content: bytes) -> None:
    if stream.write(content) != len(content):
        raise OSError("short on-demand fixture write")


def generate_oversized_pdf(
    output_directory: Path,
    *,
    repository_root: Path = PROJECT_ROOT,
    byte_count: int = OVERSIZED_PDF_DEFAULT_BYTES,
) -> Path:
    """Create one deterministic large parser-limit input outside the repository."""

    if byte_count < 1_048_576 or byte_count > OVERSIZED_PDF_MAX_BYTES:
        raise FixtureGenerationError(
            "FIXTURE_ON_DEMAND_SIZE_INVALID",
            "requested byte count is outside the safe recipe range",
        )
    destination = _ensure_external_directory(output_directory, repository_root)
    path = destination / "pws-synthetic-oversized.pdf"
    if os.path.lexists(path):
        raise FixtureGenerationError(
            "FIXTURE_OUTPUT_EXISTS", "on-demand generation does not overwrite existing output"
        )
    prefix = f"%PDF-1.4\n% {SYNTHETIC_MARKER}: oversized parser-limit recipe\n".encode("ascii")
    suffix = b"\n%%EOF\n"
    fill_bytes = byte_count - len(prefix) - len(suffix)
    if fill_bytes < 0:
        raise FixtureGenerationError(
            "FIXTURE_ON_DEMAND_SIZE_INVALID", "requested byte count is too small for the recipe"
        )
    created_identity: tuple[int, int] | None = None
    succeeded = False
    try:
        with path.open("xb") as stream:
            opened = os.fstat(stream.fileno())
            created_identity = (opened.st_dev, opened.st_ino)
            _write_oversized_payload(stream, prefix, suffix, fill_bytes)
        final = path.lstat()
        if (
            _is_link_or_reparse(path)
            or not stat.S_ISREG(final.st_mode)
            or final.st_nlink != 1
            or (final.st_dev, final.st_ino) != created_identity
            or final.st_size != byte_count
            or path.parent.resolve(strict=True) != destination
            or path.resolve(strict=True).parent != destination
        ):
            raise OSError("on-demand fixture path changed")
        succeeded = True
    except OSError as error:
        raise FixtureGenerationError(
            "FIXTURE_OUTPUT_UNWRITABLE", "on-demand fixture could not be written"
        ) from error
    finally:
        if not succeeded:
            _remove_created_external_file(path, destination, created_identity)
    return path


def generate_long_path_tree(
    output_directory: Path,
    *,
    repository_root: Path = PROJECT_ROOT,
    component_count: int = LONG_PATH_COMPONENT_COUNT,
) -> Path:
    """Create a deterministic long-path carrier outside the repository."""

    if component_count < 2 or component_count > 16:
        raise FixtureGenerationError(
            "FIXTURE_ON_DEMAND_SIZE_INVALID", "component count is outside the safe recipe range"
        )
    destination = _ensure_external_directory(output_directory, repository_root)
    nested = _ensure_safe_directory_chain(
        destination,
        Path(*(LONG_PATH_COMPONENT for _ in range(component_count))),
    )
    path = nested / "pws-synthetic-long-path.pdf"
    if os.path.lexists(path):
        raise FixtureGenerationError(
            "FIXTURE_OUTPUT_EXISTS", "on-demand generation does not overwrite existing output"
        )
    content = _text_pdf(f"{SYNTHETIC_MARKER}: long-path carrier")
    created_identity: tuple[int, int] | None = None
    resolved_nested: Path | None = None
    succeeded = False
    try:
        resolved_nested = nested.resolve(strict=True)
        repository = repository_root.resolve(strict=True)
        if (
            not resolved_nested.is_relative_to(destination)
            or resolved_nested == repository
            or resolved_nested.is_relative_to(repository)
        ):
            raise OSError("on-demand output escaped its reviewed destination")
        with path.open("xb") as stream:
            opened = os.fstat(stream.fileno())
            created_identity = (opened.st_dev, opened.st_ino)
            _write_external_bytes(stream, content)
        final = path.lstat()
        if (
            _is_link_or_reparse(path)
            or not stat.S_ISREG(final.st_mode)
            or final.st_nlink != 1
            or (final.st_dev, final.st_ino) != created_identity
            or path.parent.resolve(strict=True) != resolved_nested
            or path.resolve(strict=True).parent != resolved_nested
        ):
            raise OSError("on-demand fixture path changed")
        succeeded = True
    except OSError as error:
        raise FixtureGenerationError(
            "FIXTURE_OUTPUT_UNWRITABLE", "on-demand fixture could not be written"
        ) from error
    finally:
        if not succeeded and resolved_nested is not None:
            _remove_created_external_file(path, resolved_nested, created_identity)
    return path


def main(argv: Sequence[str] | None = None) -> int:
    """Regenerate the committed corpus or create one reviewed on-demand recipe."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT, help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("regenerate", help="write the exact reviewed committed corpus")
    on_demand = subparsers.add_parser(
        "on-demand", help="create an uncommitted hostile fixture outside the repository"
    )
    on_demand.add_argument("recipe", choices=("long-path", "oversized-pdf"))
    on_demand.add_argument("--output-directory", type=Path, required=True)
    on_demand.add_argument("--bytes", type=int, default=OVERSIZED_PDF_DEFAULT_BYTES)
    on_demand.add_argument("--components", type=int, default=LONG_PATH_COMPONENT_COUNT)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "regenerate":
            count = write_committed_corpus(arguments.root)
            golden_count = len(build_renderer_golden_references())
            print(
                f"OK: regenerated {count} deterministic public synthetic fixtures "
                f"and {golden_count} analytical renderer references."
            )
        elif arguments.recipe == "oversized-pdf":
            path = generate_oversized_pdf(
                arguments.output_directory,
                repository_root=arguments.root,
                byte_count=arguments.bytes,
            )
            print(
                f"OK: generated oversized fixture ({path.stat().st_size} bytes) outside repository."
            )
        else:
            path = generate_long_path_tree(
                arguments.output_directory,
                repository_root=arguments.root,
                component_count=arguments.components,
            )
            print(f"OK: generated long-path fixture ({len(str(path.resolve()))} characters).")
    except FixtureGenerationError as error:
        print(f"ERROR [{error.code}] {error.message}.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

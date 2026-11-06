"""Worker-only streaming extraction from an authenticated final PDF derivative."""

from __future__ import annotations

import hashlib
import hmac
import math
from collections.abc import Callable
from contextlib import closing
from typing import BinaryIO

import pypdfium2 as pdfium  # type: ignore[import-untyped]
import pypdfium2.raw as pdfium_c  # type: ignore[import-untyped]

from paperworkshell.domain.text_companion import (
    TextCompanionError,
    TextCompanionErrorCode,
    TextCompanionLimits,
    reject_text_companion,
    require_companion_active,
    sanitize_companion_text,
)
from paperworkshell.localization.catalog import tr
from paperworkshell.pdf.pdfium_renderer import _require_approved_runtime


def _checked_page_box(value: object) -> tuple[float, float, float, float]:
    if (
        not isinstance(value, tuple)
        or len(value) != 4
        or any(type(part) not in {int, float} or not math.isfinite(part) for part in value)
    ):
        reject_text_companion(TextCompanionErrorCode.EXTRACTION_FAILED)
    left, bottom, right, top = (float(part) for part in value)
    if right <= left or top <= bottom:
        reject_text_companion(TextCompanionErrorCode.EXTRACTION_FAILED)
    return left, bottom, right, top


def _glyph_inside(box: object, visible: tuple[float, float, float, float]) -> bool:
    if (
        not isinstance(box, tuple)
        or len(box) != 4
        or any(type(part) not in {int, float} or not math.isfinite(part) for part in box)
    ):
        reject_text_companion(TextCompanionErrorCode.EXTRACTION_FAILED)
    left, bottom, right, top = (float(part) for part in box)
    return visible[0] <= left < right <= visible[2] and visible[1] <= bottom < top <= visible[3]


def extract_verified_pdf_text(
    source: BinaryIO,
    destination: BinaryIO,
    *,
    expected_byte_size: int,
    expected_sha256: str,
    expected_page_count: int,
    limits: TextCompanionLimits,
    deadline: Callable[[], bool],
    cancellation: Callable[[], bool],
) -> tuple[int, str]:
    """Write only the final derivative's text, one bounded page/chunk at a time.

    Neither PDF metadata, attachments nor a source-side OCR cache is read. There
    is no fallback to originals if the exact final derivative cannot be parsed.
    """
    try:
        if (
            type(limits) is not TextCompanionLimits
            or type(expected_byte_size) is not int
            or not 1 <= expected_byte_size <= limits.maximum_source_bytes
            or type(expected_page_count) is not int
            or not 1 <= expected_page_count <= limits.maximum_pages
            or type(expected_sha256) is not str
            or len(expected_sha256) != 64
        ):
            reject_text_companion(TextCompanionErrorCode.INVALID)
        require_companion_active(deadline, cancellation)
        _require_approved_runtime()
        source.seek(0)
        digest = hashlib.sha256()
        measured = 0
        while payload := source.read(min(64 * 1024, expected_byte_size - measured + 1)):
            require_companion_active(deadline, cancellation)
            measured += len(payload)
            if measured > expected_byte_size:
                reject_text_companion(TextCompanionErrorCode.EXPORT_CHANGED)
            digest.update(payload)
        if measured != expected_byte_size or not hmac.compare_digest(
            digest.hexdigest(), expected_sha256
        ):
            reject_text_companion(TextCompanionErrorCode.EXPORT_CHANGED)
        source.seek(0)
        output_digest = hashlib.sha256()
        output_size = 0

        def write(value: str) -> None:
            nonlocal output_size
            require_companion_active(deadline, cancellation)
            encoded = value.encode("utf-8", errors="strict")
            if len(encoded) > limits.maximum_output_bytes - output_size:
                reject_text_companion(TextCompanionErrorCode.LIMIT_EXCEEDED)
            if destination.write(encoded) != len(encoded):
                reject_text_companion(TextCompanionErrorCode.EXTRACTION_FAILED)
            output_size += len(encoded)
            output_digest.update(encoded)

        with pdfium.PdfDocument(source, password="", autoclose=False) as document:
            if len(document) != expected_page_count:
                reject_text_companion(TextCompanionErrorCode.EXPORT_CHANGED)
            for page_index in range(expected_page_count):
                require_companion_active(deadline, cancellation)
                write(tr("\nPage {value_0}\n\n", value_0=page_index + 1))
                with (
                    closing(document.get_page(page_index)) as page,
                    closing(page.get_textpage()) as textpage,
                ):
                    count = textpage.count_chars()
                    if type(count) is not int or count < 0 or count > limits.maximum_output_bytes:
                        reject_text_companion(TextCompanionErrorCode.LIMIT_EXCEEDED)
                    # PDFium character coordinates and this MediaBox/CropBox
                    # intersection share unrotated PDF coordinates, including
                    # on rotated pages. Range extraction alone would disclose
                    # still-present text outside the exported visible crop.
                    visible = _checked_page_box(page.get_bbox())
                    has_text = False
                    previous_visible = False
                    buffered: list[str] = []
                    index = 0
                    next_probe = 0
                    while index < count:
                        if index >= next_probe:
                            require_companion_active(deadline, cancellation)
                            next_probe = index + 256
                        codepoint = pdfium_c.FPDFText_GetUnicode(textpage, index)
                        if type(codepoint) is not int or not 0 <= codepoint <= 0x10FFFF:
                            reject_text_companion(TextCompanionErrorCode.EXTRACTION_FAILED)
                        glyph_indices: tuple[int, ...] = (index,)
                        if 0xD800 <= codepoint <= 0xDBFF:
                            if index + 1 >= count:
                                reject_text_companion(TextCompanionErrorCode.EXTRACTION_FAILED)
                            following = pdfium_c.FPDFText_GetUnicode(textpage, index + 1)
                            if type(following) is not int or not 0xDC00 <= following <= 0xDFFF:
                                reject_text_companion(TextCompanionErrorCode.EXTRACTION_FAILED)
                            codepoint = 0x10000 + ((codepoint - 0xD800) << 10) + following - 0xDC00
                            glyph_indices = (index, index + 1)
                        elif 0xDC00 <= codepoint <= 0xDFFF:
                            reject_text_companion(TextCompanionErrorCode.EXTRACTION_FAILED)
                        index += len(glyph_indices)
                        character = sanitize_companion_text(chr(codepoint))
                        if not character:
                            continue
                        if (
                            character.isspace()
                            or character in {"\u200c", "\u200d"}
                            or 0xFE00 <= codepoint <= 0xFE0F
                            or 0xE0100 <= codepoint <= 0xE01EF
                        ):
                            if previous_visible:
                                buffered.append(character)
                        elif all(
                            _glyph_inside(textpage.get_charbox(glyph_index), visible)
                            for glyph_index in glyph_indices
                        ):
                            buffered.append(character)
                            has_text = previous_visible = True
                        else:
                            if previous_visible:
                                buffered.append("\n")
                            previous_visible = False
                        if len(buffered) >= 4096:
                            write("".join(buffered))
                            buffered.clear()
                    if buffered:
                        write("".join(buffered))
                    if not has_text:
                        write(tr("[No extractable text on this page.]\n"))
                    write("\n")
        require_companion_active(deadline, cancellation)
        return output_size, output_digest.hexdigest()
    except TextCompanionError:
        raise
    except MemoryError, RecursionError:
        reject_text_companion(TextCompanionErrorCode.LIMIT_EXCEEDED)
    except Exception:
        reject_text_companion(TextCompanionErrorCode.EXTRACTION_FAILED)

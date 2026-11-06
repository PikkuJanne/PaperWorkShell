"""Independent, bounded PDFium checks of a private redaction candidate.

The reference is the caller-authenticated reconstruction/OCR artifact, never the
imported original. Equality preserves its non-removed pixels and regenerated
text. Structural inspection must separately reject unreachable objects, masks,
alternate carriers and revisions; PDFium enumerates rendered page objects only.
This adapter grants no export or release authority.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Buffer, Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from typing import Any, BinaryIO

import pypdfium2.raw as pdfium_c  # type: ignore[import-untyped]

from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.domain.pdf_rendering import (
    PdfPageRenderRequest,
    PdfRenderError,
    PdfRenderErrorCode,
)
from paperworkshell.domain.redaction_reconstruction import RECONSTRUCTION_DPI
from paperworkshell.pdf.pdfium_renderer import (
    PdfiumPageRenderer,
    _ExactReadSource,
    _open_document,
    _require_approved_runtime,
    _serialized_pdfium,
)
from paperworkshell.pdf.redaction_reconstruction import (
    RedactionReconstructionControl,
    RedactionReconstructionLimits,
    _checkpoint,
    _measure_source,
    _render_limits,
)


@dataclass(frozen=True, slots=True)
class VisualVerificationResult:
    """Closed, value-free method outcomes; ``None`` means that method passed."""

    text_code: str | None
    image_code: str | None
    render_code: str | None


class _VisualFault(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def verify_visual(
    candidate: BinaryIO,
    reference: BinaryIO,
    *,
    forbidden_tokens: tuple[str, ...],
    affected_pages: tuple[int, ...],
    limits: RedactionReconstructionLimits,
    control: RedactionReconstructionControl,
) -> VisualVerificationResult:
    """Compare exact trusted text/pixels and probe selection/search extraction.

    Pages are zero based. Only one image or rendered page is decoded at a time.
    Stream ownership and positions remain with the caller. Native processing
    additionally requires the existing restricted worker's process budgets.
    """

    positions: list[tuple[BinaryIO, int]] = []
    result = VisualVerificationResult(*(3 * ("RV_METHOD_FAILED",)))
    try:
        if type(limits) is not RedactionReconstructionLimits:
            raise _VisualFault("RV_STRUCTURE_UNSUPPORTED")
        limits.__post_init__()
        _checkpoint(control)
        _validate_arguments(forbidden_tokens, affected_pages, limits)
        for stream in (candidate, reference):
            position = stream.tell()
            if type(position) is not int or position < 0:
                raise _VisualFault("RV_METHOD_FAILED")
            positions.append((stream, position))
            _measure_source(stream, limits.maximum_output_bytes, control)
        _require_approved_runtime()
        text = _run(
            lambda: _verify_text(
                candidate, reference, forbidden_tokens, affected_pages, limits, control
            )
        )
        if text in ("RV_CANCELLED", "RV_TIMED_OUT", "RV_LIMIT_EXCEEDED"):
            result = VisualVerificationResult(text, text, text)
        else:
            image = _run(
                lambda: _verify_images(candidate, reference, affected_pages, limits, control)
            )
            if image in ("RV_CANCELLED", "RV_TIMED_OUT", "RV_LIMIT_EXCEEDED"):
                result = VisualVerificationResult(text, image, image)
            else:
                render = _run(
                    lambda: _verify_rendering(candidate, reference, affected_pages, limits, control)
                )
                result = VisualVerificationResult(text, image, render)
        _checkpoint(control)
    except Exception as error:
        code = _failure_code(error)
        result = VisualVerificationResult(code, code, code)
    finally:
        for stream, position in positions:
            try:
                if stream.seek(position) != position:
                    raise OSError
            except Exception:
                result = VisualVerificationResult(*(3 * ("RV_METHOD_FAILED",)))
    return result


def _validate_arguments(
    tokens: tuple[str, ...], pages: tuple[int, ...], limits: RedactionReconstructionLimits
) -> None:
    if (
        type(tokens) is not tuple
        or len(tokens) > limits.maximum_pdf_objects
        or any(type(token) is not str or not token or "\x00" in token for token in tokens)
        or type(pages) is not tuple
        or len(pages) > limits.maximum_pages
        or any(type(page) is not int or page < 0 for page in pages)
        or len(set(pages)) != len(pages)
    ):
        raise _VisualFault("RV_STRUCTURE_UNSUPPORTED")
    if sum(map(len, tokens)) * 8 > limits.maximum_memory_bytes:
        raise _VisualFault("RV_LIMIT_EXCEEDED")


def _run(operation: Callable[[], None]) -> str | None:
    try:
        operation()
    except Exception as error:
        return _failure_code(error)
    return None


def _failure_code(error: Exception) -> str:
    if isinstance(error, _VisualFault):
        return error.code
    if isinstance(error, (MemoryError, RecursionError)):
        return "RV_LIMIT_EXCEEDED"
    if isinstance(error, PdfExportError):
        return {
            PdfExportErrorCode.CANCELLED: "RV_CANCELLED",
            PdfExportErrorCode.TIMED_OUT: "RV_TIMED_OUT",
            PdfExportErrorCode.LIMIT_EXCEEDED: "RV_LIMIT_EXCEEDED",
        }.get(error.code, "RV_METHOD_FAILED")
    if isinstance(error, PdfRenderError):
        return {
            PdfRenderErrorCode.CANCELLED: "RV_CANCELLED",
            PdfRenderErrorCode.TIMED_OUT: "RV_TIMED_OUT",
            PdfRenderErrorCode.LIMIT_EXCEEDED: "RV_LIMIT_EXCEEDED",
        }.get(error.code, "RV_METHOD_FAILED")
    return "RV_METHOD_FAILED"


@contextmanager
def _documents(
    candidate: BinaryIO,
    reference: BinaryIO,
    pages: tuple[int, ...],
    limits: RedactionReconstructionLimits,
    control: RedactionReconstructionControl,
) -> Iterator[tuple[Any, Any, int]]:
    with _serialized_pdfium(control):
        candidate_source, reference_source = (
            _ExactReadSource(candidate),
            _ExactReadSource(reference),
        )
        with (
            _open_document(candidate_source) as actual,
            _open_document(reference_source) as expected,
        ):
            count = len(expected)
            if (
                type(count) is not int
                or count < 1
                or len(actual) != count
                or count > limits.maximum_pages
                or max(pages, default=-1) >= count
            ):
                raise _VisualFault("RV_STRUCTURE_UNSUPPORTED")
            yield actual, expected, count
            _checkpoint(control)
            candidate_source.raise_if_faulted()
            reference_source.raise_if_faulted()
        candidate_source.raise_if_faulted()
        reference_source.raise_if_faulted()


def _verify_text(
    candidate: BinaryIO,
    reference: BinaryIO,
    tokens: tuple[str, ...],
    pages: tuple[int, ...],
    limits: RedactionReconstructionLimits,
    control: RedactionReconstructionControl,
) -> None:
    with _documents(candidate, reference, pages, limits, control) as (actual, expected, count):
        for index in range(count):
            _checkpoint(control)
            with closing(actual[index]) as page, closing(expected[index]) as trusted_page:
                with (
                    closing(page.get_textpage()) as textpage,
                    closing(trusted_page.get_textpage()) as trusted_textpage,
                ):
                    text, character_count = _extract_text(textpage, limits)
                    trusted_text, trusted_count = _extract_text(trusted_textpage, limits)
                    folded = text.casefold()
                    for token in tokens:
                        _checkpoint(control)
                        # Extraction and PDFium's native search are separate probes.
                        if token.casefold() in folded:
                            raise _VisualFault("RV_CANARY_RECOVERED")
                        with closing(textpage.search(token, match_case=False)) as searcher:
                            if searcher.get_next() is not None:
                                raise _VisualFault("RV_CANARY_RECOVERED")
                    if text != trusted_text or character_count != trusted_count:
                        raise _VisualFault("RV_TEXT_MISMATCH")
                    # Invisible OCR can preserve text and appearance while moving
                    # selectable glyphs into a removed region. Bind its geometry
                    # to the authenticated exclusion-checked OCR reference too.
                    for character in range(character_count):
                        _checkpoint(control)
                        box = textpage.get_charbox(character)
                        trusted_box = trusted_textpage.get_charbox(character)
                        if (
                            len(box) != 4
                            or len(trusted_box) != 4
                            or any(not math.isfinite(value) for value in (*box, *trusted_box))
                            or any(
                                not math.isclose(value, trusted, rel_tol=0, abs_tol=0.00001)
                                for value, trusted in zip(box, trusted_box, strict=True)
                            )
                        ):
                            raise _VisualFault("RV_TEXT_MISMATCH")


def _extract_text(textpage: Any, limits: RedactionReconstructionLimits) -> tuple[str, int]:
    count = textpage.count_chars()
    maximum = min(limits.maximum_pdf_objects, limits.maximum_memory_bytes // 16)
    if type(count) is not int or not 0 <= count <= maximum:
        raise _VisualFault("RV_LIMIT_EXCEEDED")
    text = textpage.get_text_range(0, count, errors="strict") if count else ""
    if type(text) is not str or len(text) > maximum:
        raise _VisualFault("RV_LIMIT_EXCEEDED")
    return text, count


def _verify_images(
    candidate: BinaryIO,
    reference: BinaryIO,
    pages: tuple[int, ...],
    limits: RedactionReconstructionLimits,
    control: RedactionReconstructionControl,
) -> None:
    with _documents(candidate, reference, pages, limits, control) as (actual, expected, count):
        for index in range(count):
            _checkpoint(control)
            with closing(actual[index]) as page, closing(expected[index]) as trusted_page:
                trusted = _image_identities(trusted_page, limits, control)
                if index in pages and not trusted:
                    raise _VisualFault("RV_STRUCTURE_UNSUPPORTED")
                if _image_identities(page, limits, control) != trusted:
                    raise _VisualFault("RV_IMAGE_MISMATCH")


def _image_identities(
    page: Any, limits: RedactionReconstructionLimits, control: RedactionReconstructionControl
) -> list[tuple[int, int, str]]:
    result: list[tuple[int, int, str]] = []
    # Explicit iterator stack avoids recursion and silent max_depth truncation.
    stack = [iter(page.get_objects(max_depth=1))]
    examined = 0
    while stack:
        _checkpoint(control)
        item = next(stack[-1], None)
        if item is None:
            stack.pop()
            continue
        examined += 1
        if examined > limits.maximum_pdf_objects:
            raise _VisualFault("RV_LIMIT_EXCEEDED")
        if item.type == pdfium_c.FPDF_PAGEOBJ_FORM:
            stack.append(iter(page.get_objects(max_depth=1, form=item)))
        elif item.type == pdfium_c.FPDF_PAGEOBJ_IMAGE:
            width, height = item.get_px_size()
            _check_layout(width, height, limits)
            with closing(item.get_bitmap(render=False)) as bitmap:
                if (bitmap.width, bitmap.height) != (width, height):
                    raise _VisualFault("RV_IMAGE_MISMATCH")
                result.append((width, height, _bitmap_identity(bitmap, limits, control)))
    return sorted(result)


def _check_layout(width: int, height: int, limits: RedactionReconstructionLimits) -> None:
    if (
        type(width) is not int
        or type(height) is not int
        or not 0 < width <= limits.maximum_width_pixels
        or not 0 < height <= limits.maximum_height_pixels
        or width * height > limits.maximum_pixels
        or width * height * 4 > min(limits.maximum_raster_bytes, limits.maximum_memory_bytes // 4)
    ):
        raise _VisualFault("RV_LIMIT_EXCEEDED")


def _bitmap_identity(
    bitmap: Any, limits: RedactionReconstructionLimits, control: RedactionReconstructionControl
) -> str:
    mode = bitmap.mode
    channels = {"L": 1, "BGR": 3, "RGB": 3, "BGRX": 4, "RGBX": 4, "BGRA": 4, "RGBA": 4}
    if mode not in channels:
        raise _VisualFault("RV_STRUCTURE_UNSUPPORTED")
    width, height, stride = bitmap.width, bitmap.height, bitmap.stride
    size = channels[mode]
    if (
        type(stride) is not int
        or stride < width * size
        or stride * height > limits.maximum_raster_bytes
    ):
        raise _VisualFault("RV_LIMIT_EXCEEDED")
    view = memoryview(bitmap.buffer).cast("B")
    if view.nbytes != stride * height:
        raise _VisualFault("RV_IMAGE_MISMATCH")
    digest = hashlib.sha256()
    for row in range(height):
        _checkpoint(control)
        values = view[row * stride : row * stride + width * size]
        rgba = bytearray(b"\xff" * (width * 4))
        for channel in range(3):
            source_channel = (
                0 if mode == "L" else 2 - channel if mode.startswith("BGR") else channel
            )
            rgba[channel::4] = values[source_channel::size]
        if mode.endswith("A"):
            rgba[3::4] = values[3::size]
        digest.update(rgba)
    return digest.hexdigest()


class _Discard:
    def write(self, data: Buffer, /) -> int:
        return memoryview(data).nbytes

    def flush(self) -> None:
        pass


def _verify_rendering(
    candidate: BinaryIO,
    reference: BinaryIO,
    pages: tuple[int, ...],
    limits: RedactionReconstructionLimits,
    control: RedactionReconstructionControl,
) -> None:
    dimensions: list[tuple[int, int]] = []
    with _documents(candidate, reference, pages, limits, control) as (actual, expected, count):
        for index in range(count):
            _checkpoint(control)
            with closing(actual[index]) as page, closing(expected[index]) as trusted_page:
                size, trusted_size = page.get_size(), trusted_page.get_size()
                if (
                    len(size) != 2
                    or len(trusted_size) != 2
                    or any(
                        not math.isfinite(value) or value <= 0 for value in (*size, *trusted_size)
                    )
                    or any(
                        not math.isclose(value, trusted, rel_tol=0, abs_tol=0.00001)
                        for value, trusted in zip(size, trusted_size, strict=True)
                    )
                ):
                    raise _VisualFault("RV_RENDER_MISMATCH")
                width, height = (
                    math.ceil(value * RECONSTRUCTION_DPI / 72) for value in trusted_size
                )
                _check_layout(width, height, limits)
                dimensions.append((width, height))
    renderer = PdfiumPageRenderer()
    for index, (width, height) in enumerate(dimensions):
        _checkpoint(control)
        request = PdfPageRenderRequest(index, width, height)
        expected_result = renderer.render(
            reference, _Discard(), request, _render_limits(limits), control
        )
        actual_result = renderer.render(
            candidate, _Discard(), request, _render_limits(limits), control
        )
        if (
            actual_result.width_pixels != expected_result.width_pixels
            or actual_result.height_pixels != expected_result.height_pixels
            or actual_result.pixel_sha256 != expected_result.pixel_sha256
        ):
            raise _VisualFault("RV_RENDER_MISMATCH")

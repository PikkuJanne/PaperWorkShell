"""Private OCR of a fresh page whose marked source pixels are already absent.

The black input mask is the exclusion strategy. OCR never receives the original
PDF, its text layer, or an unmasked raster. This stage checks appearance and
identity, but cannot approve the later permanent-removal security gate.
"""

from __future__ import annotations

import hashlib
import math
import os
import stat
import zlib
from collections.abc import Buffer
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import BinaryIO, Never, Protocol, cast
from uuid import uuid4

import pikepdf
import pypdfium2 as pdfium  # type: ignore[import-untyped]

from paperworkshell.domain.image_pages import ImageRgba8Layout
from paperworkshell.domain.ocr import (
    DEFAULT_OCR_PROFILE_ID,
    OcrAdapterProgressSink,
    OcrAdapterResult,
    OcrError,
    OcrErrorCode,
)
from paperworkshell.domain.ocr_languages import resolve_ocr_language_set
from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode, reject_pdf_export
from paperworkshell.domain.pdf_geometry import (
    MAX_PDF_PAGE_COORDINATE_MAGNITUDE_UNITS,
    MAX_PDF_PAGE_SPAN_UNITS,
    PdfPageGeometryLimits,
)
from paperworkshell.domain.pdf_rendering import PdfPageBox, PdfPageRenderRequest, PdfRenderError
from paperworkshell.domain.redaction_ocr import (
    RedactionOcrReport,
    RedactionOcrRequest,
    validate_redaction_ocr_report,
)
from paperworkshell.domain.redaction_reconstruction import (
    RECONSTRUCTION_DPI,
    RedactionReconstructionReport,
    validate_reconstruction_report,
)
from paperworkshell.pdf.page_geometry import PikePdfPageGeometryResolver
from paperworkshell.pdf.raster_pdf_page import _BoundedOutput
from paperworkshell.pdf.redaction_reconstruction import (
    RedactionReconstructionAdapter,
    RedactionReconstructionControl,
    RedactionReconstructionLimits,
    _checkpoint,
    _DiscardSink,
    _map_render_error,
    _measure_path,
    _measure_source,
    _pixel_box,
    _render_limits,
    _validate_paths,
    _verify_generated_structure,
)

_CHUNK = 64 * 1024
_REPARSE_POINT = 0x400
_MAXIMUM_PRIVATE_DEPTH = 32


class RedactionOcrProcessor(Protocol):
    """Narrow worker-injected OCR authority; no native engine import is needed."""

    def process(
        self,
        source_path: Path,
        output_path: Path,
        *,
        temporary_directory: Path,
        languages: tuple[str, ...],
        profile_id: str,
        expected_page_count: int,
        maximum_output_bytes: int,
        maximum_image_pixels: int,
        maximum_temporary_storage_bytes: int,
        control: RedactionReconstructionControl,
        progress: OcrAdapterProgressSink,
    ) -> OcrAdapterResult: ...


@dataclass(frozen=True, slots=True, repr=False)
class RedactionOcrAdapter:
    """Reconstruct, recognize masked pixels and return only unverified evidence."""

    ocr: RedactionOcrProcessor = field(repr=False)
    reconstructor: RedactionReconstructionAdapter = field(
        default_factory=RedactionReconstructionAdapter
    )

    def regenerate(
        self,
        source: BinaryIO,
        output_path: Path,
        scratch_directory: Path,
        request: RedactionOcrRequest,
        limits: RedactionReconstructionLimits,
        control: RedactionReconstructionControl,
    ) -> RedactionOcrReport:
        _validate_paths(output_path, scratch_directory)
        if (
            type(request) is not RedactionOcrRequest
            or type(limits) is not RedactionReconstructionLimits
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "redaction_ocr.request")
        request.__post_init__()
        limits.__post_init__()
        _checkpoint(control)
        original = _measure_source(source, limits.maximum_source_bytes, control)
        intent = request.reconstruction
        if original != (intent.source_byte_size, intent.source_sha256):
            reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "redaction_ocr.source")
        # Reconstruction's own source/raster/output calculation additionally
        # reserves the maximum second PDF. OCR monitors the complete shared root.
        reconstruction_temp = limits.maximum_temporary_storage_bytes - limits.maximum_output_bytes
        if reconstruction_temp < 1:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "redaction_ocr.temp_budget")
        private = scratch_directory / uuid4().hex
        masked, recognized = private / f"{uuid4().hex}.pdf", private / f"{uuid4().hex}.pdf"
        private_owned = output_owned = completed = False
        try:
            private.mkdir()
            private_owned = True
            reconstruction = self.reconstructor.reconstruct(
                source,
                masked,
                private,
                intent,
                replace(limits, maximum_temporary_storage_bytes=reconstruction_temp),
                control,
            )
            validate_reconstruction_report(intent, reconstruction)
            expected_masked = (reconstruction.output_byte_size, reconstruction.output_sha256)
            if _measure_path(masked, limits.maximum_output_bytes, control) != expected_masked:
                reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "redaction_ocr.masked_binding")
            identity = _image_identity(masked, reconstruction, limits, control)
            layout = ImageRgba8Layout(
                reconstruction.width_pixels,
                reconstruction.height_pixels,
                reconstruction.width_pixels * 4,
                reconstruction.width_pixels * reconstruction.height_pixels,
                reconstruction.width_pixels * reconstruction.height_pixels * 4,
            )
            _verify_generated_structure(masked, layout, identity, limits, control)
            exclusions = _exclusion_boxes(source, request, reconstruction, limits)
            rendered = _render_digest(self.reconstructor, masked, reconstruction, limits, control)
            _checkpoint(control)
            result = self.ocr.process(
                masked,
                recognized,
                temporary_directory=private,
                languages=resolve_ocr_language_set(request.language_set_id),
                profile_id=DEFAULT_OCR_PROFILE_ID,
                expected_page_count=1,
                maximum_output_bytes=limits.maximum_output_bytes,
                maximum_image_pixels=limits.maximum_pixels,
                maximum_temporary_storage_bytes=(
                    limits.maximum_temporary_storage_bytes - intent.source_byte_size
                ),
                control=control,
                progress=lambda *_values: _checkpoint(control),
            )
            _checkpoint(control)
            if _measure_path(masked, limits.maximum_output_bytes, control) != expected_masked:
                reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "redaction_ocr.masked_changed")
            measured = _measure_path(recognized, limits.maximum_output_bytes, control)
            if measured != (result.byte_size, result.sha256) or result.page_count != 1:
                reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "redaction_ocr.output_binding")
            # OCRmyPDF grafts text incrementally onto this already-masked input.
            # Rewrite that generated artifact before applying the strict no-history
            # policy. Its predecessor is never the imported source PDF. Removing
            # the masked input first keeps this rewrite within two PDF candidates.
            masked.unlink()
            rewritten = private / f"{uuid4().hex}.pdf"
            _rewrite_generated_ocr(recognized, rewritten, limits, control)
            recognized.unlink()
            recognized = rewritten
            measured = _measure_path(recognized, limits.maximum_output_bytes, control)
            if _image_identity(recognized, reconstruction, limits, control) != identity:
                reject_pdf_export(
                    PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH, "redaction_ocr.image_changed"
                )
            if (
                _render_digest(self.reconstructor, recognized, reconstruction, limits, control)
                != rendered
            ):
                reject_pdf_export(
                    PdfExportErrorCode.VERIFICATION_RENDER_FAILED, "redaction_ocr.appearance"
                )
            _verify_text_exclusion(recognized, exclusions, limits, control)
            if _measure_source(source, limits.maximum_source_bytes, control) != original:
                reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "redaction_ocr.source_changed")
            # The OCR adapter's file stays private. The caller's new candidate is
            # created exclusively only after every input/output check succeeds.
            with output_path.open("xb") as destination:
                output_owned = True
                with recognized.open("rb") as candidate:
                    copied, digest = 0, hashlib.sha256()
                    while block := candidate.read(_CHUNK):
                        _checkpoint(control)
                        copied += len(block)
                        if copied > limits.maximum_output_bytes or destination.write(block) != len(
                            block
                        ):
                            reject_pdf_export(
                                PdfExportErrorCode.LIMIT_EXCEEDED, "redaction_ocr.copy"
                            )
                        digest.update(block)
                destination.flush()
            if (copied, digest.hexdigest()) != measured:
                reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "redaction_ocr.copy_binding")
            if _measure_source(source, limits.maximum_source_bytes, control) != original:
                reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "redaction_ocr.source_changed")
            report = RedactionOcrReport(
                request_sha256=request.fingerprint,
                reconstruction=reconstruction,
                output_sha256=measured[1],
                output_byte_size=measured[0],
                language_set_id=request.language_set_id,
                profile_id=request.profile_id,
                ocrmypdf_version=result.ocrmypdf_version,
                tesseract_version=result.tesseract_version,
            )
            validate_redaction_ocr_report(request, report)
            _checkpoint(control)
            _remove_tree(private, limits.maximum_pdf_objects)
            _checkpoint(control)
            completed = True
            return report
        except OcrError as error:
            _map_ocr_error(error)
        except PdfRenderError as error:
            _map_render_error(error)
        except PdfExportError:
            raise
        except MemoryError, RecursionError:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "redaction_ocr.resources")
        except Exception:
            reject_pdf_export(PdfExportErrorCode.ASSEMBLY_FAILED, "redaction_ocr.adapter")
        finally:
            try:
                if private_owned:
                    _remove_tree(private, limits.maximum_pdf_objects)
            finally:
                if output_owned and not completed:
                    try:
                        output_path.unlink(missing_ok=True)
                    except OSError:
                        reject_pdf_export(
                            PdfExportErrorCode.ASSEMBLY_FAILED, "redaction_ocr.cleanup"
                        )


def _image_identity(
    path: Path,
    report: RedactionReconstructionReport,
    limits: RedactionReconstructionLimits,
    control: RedactionReconstructionControl,
) -> str:
    """Bounded decoded-image identity and fresh one-page geometry checks."""
    with pikepdf.Pdf.open(path, attempt_recovery=False, suppress_warnings=True) as document:
        _checkpoint(control)
        if (
            document.is_encrypted
            or len(document.pages) != 1
            or len(document.objects) > limits.maximum_pdf_objects
        ):
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "redaction_ocr.structure"
            )
        page = document.pages[0].obj
        expected = (0.0, 0.0, report.width_points, report.height_points)
        boxes = (page.MediaBox, page.get("/CropBox", page.MediaBox))
        if (
            any(
                len(box) != 4
                or any(
                    not math.isclose(float(value), target, rel_tol=0, abs_tol=0.00001)
                    for value, target in zip(box, expected, strict=True)
                )
                for box in boxes
            )
            or page.get("/Rotate", 0) != 0
            or page.get("/UserUnit", 1) != 1
        ):
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH, "redaction_ocr.geometry"
            )
        if (
            "/Prev" in document.trailer
            or any(
                name in document.Root
                for name in ("/AcroForm", "/Names", "/AF", "/OpenAction", "/AA")
            )
            or any(name in page for name in ("/Annots", "/AA", "/AF"))
        ):
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "redaction_ocr.active_objects"
            )
        images = []
        for item in document.objects:
            _checkpoint(control)
            if isinstance(item, pikepdf.Stream) and item.get("/Subtype") == pikepdf.Name.Image:
                images.append(item)
                if len(images) > 1:
                    reject_pdf_export(
                        PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "redaction_ocr.images"
                    )
        if len(images) != 1:
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "redaction_ocr.images"
            )
        image = images[0]
        if (
            image.Width != report.width_pixels
            or image.Height != report.height_pixels
            or image.ColorSpace != pikepdf.Name.DeviceRGB
            or image.BitsPerComponent != 8
            or image.Filter != pikepdf.Name.FlateDecode
            or any(key in image for key in ("/Mask", "/SMask", "/Decode", "/DecodeParms"))
        ):
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "redaction_ocr.image_encoding"
            )
        encoded = image.read_raw_bytes()
        if len(encoded) > limits.maximum_output_bytes:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "redaction_ocr.image_bytes")
        expected_bytes = report.width_pixels * report.height_pixels * 3
        if expected_bytes > limits.maximum_raster_bytes:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "redaction_ocr.image_pixels")
        decoder, digest, count = zlib.decompressobj(), hashlib.sha256(), 0
        for offset in range(0, len(encoded), _CHUNK):
            _checkpoint(control)
            decoded = decoder.decompress(
                encoded[offset : offset + _CHUNK], expected_bytes - count + 1
            )
            count += len(decoded)
            if count > expected_bytes or decoder.unconsumed_tail:
                reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "redaction_ocr.image_pixels")
            digest.update(decoded)
        if count != expected_bytes or not decoder.eof or decoder.unused_data:
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH, "redaction_ocr.image_pixels"
            )
        return digest.hexdigest()


class _RewriteOutput(_BoundedOutput):
    """Keep full-save writes bounded and cancellation aware."""

    def __init__(
        self, stream: BinaryIO, maximum_bytes: int, control: RedactionReconstructionControl
    ) -> None:
        super().__init__(stream, maximum_bytes)
        self._control = control

    def write(self, data: Buffer, /) -> int:
        _checkpoint(self._control)
        return super().write(data)


def _rewrite_generated_ocr(
    source: Path,
    output: Path,
    limits: RedactionReconstructionLimits,
    control: RedactionReconstructionControl,
) -> None:
    """Full-save only the authenticated OCR artifact; never import source objects."""
    _checkpoint(control)
    bounded: _RewriteOutput | None = None
    try:
        with pikepdf.Pdf.open(source, attempt_recovery=False, suppress_warnings=True) as document:
            if (
                document.is_encrypted
                or len(document.pages) != 1
                or len(document.objects) > limits.maximum_pdf_objects
            ):
                reject_pdf_export(
                    PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
                    "redaction_ocr.rewrite_structure",
                )
            with output.open("xb") as raw:
                bounded = _RewriteOutput(raw, limits.maximum_output_bytes, control)
                document.save(
                    cast(BinaryIO, bounded),
                    deterministic_id=True,
                    encryption=False,
                    compress_streams=True,
                    object_stream_mode=pikepdf.ObjectStreamMode.generate,
                    normalize_content=False,
                    linearize=False,
                )
                bounded.flush()
    except PdfExportError:
        raise
    except Exception:
        if bounded is not None and bounded.limit_exceeded:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "redaction_ocr.rewrite_bytes")
        raise
    _checkpoint(control)


def _render_digest(
    reconstructor: RedactionReconstructionAdapter,
    path: Path,
    report: RedactionReconstructionReport,
    limits: RedactionReconstructionLimits,
    control: RedactionReconstructionControl,
) -> str:
    with path.open("rb") as source:
        result = reconstructor.renderer.render(
            source,
            _DiscardSink(),
            PdfPageRenderRequest(0, report.width_pixels, report.height_pixels),
            _render_limits(limits),
            control,
        )
    if (result.width_pixels, result.height_pixels) != (report.width_pixels, report.height_pixels):
        reject_pdf_export(
            PdfExportErrorCode.VERIFICATION_RENDER_FAILED, "redaction_ocr.render_geometry"
        )
    return result.pixel_sha256


def _map_ocr_error(error: OcrError) -> Never:
    code = {
        OcrErrorCode.CANCELLED: PdfExportErrorCode.CANCELLED,
        OcrErrorCode.TIMED_OUT: PdfExportErrorCode.TIMED_OUT,
        OcrErrorCode.LIMIT_EXCEEDED: PdfExportErrorCode.LIMIT_EXCEEDED,
    }.get(error.code, PdfExportErrorCode.ASSEMBLY_FAILED)
    if error.code is OcrErrorCode.LANGUAGE_UNAVAILABLE:
        code = PdfExportErrorCode.OCR_LANGUAGE_UNAVAILABLE
    context = (
        "redaction_ocr.language_unavailable"
        if error.code is OcrErrorCode.LANGUAGE_UNAVAILABLE
        else "redaction_ocr.engine_failed"
    )
    reject_pdf_export(code, context)


def _exclusion_boxes(
    source: BinaryIO,
    request: RedactionOcrRequest,
    report: RedactionReconstructionReport,
    limits: RedactionReconstructionLimits,
) -> tuple[tuple[float, float, float, float], ...]:
    """Replay the exact outward pixel mask, crop and clockwise quarter turns."""
    intent = request.reconstruction
    geometry = PikePdfPageGeometryResolver().resolve(
        source,
        PdfPageRenderRequest(intent.source_page_index, 1, 1, PdfPageBox.CROP),
        _render_limits(limits),
        PdfPageGeometryLimits(MAX_PDF_PAGE_COORDINATE_MAGNITUDE_UNITS, MAX_PDF_PAGE_SPAN_UNITS),
    )
    width = geometry.effective_box.width * geometry.user_unit
    height = geometry.effective_box.height * geometry.user_unit
    if int(geometry.intrinsic_rotation) % 180:
        width, height = height, width
    full_width, full_height = (
        math.ceil(width * RECONSTRUCTION_DPI / 72),
        math.ceil(height * RECONSTRUCTION_DPI / 72),
    )
    left, top, right, bottom = _pixel_box(intent.crop, full_width, full_height, margin=0)
    final_width, final_height = right - left, bottom - top
    result = []
    for mark in intent.marks:
        x0, y0, x1, y1 = _pixel_box(mark.box, full_width, full_height, margin=1)
        x0, y0, x1, y1 = (
            max(x0, left) - left,
            max(y0, top) - top,
            min(x1, right) - left,
            min(y1, bottom) - top,
        )
        if x0 >= x1 or y0 >= y1:
            continue
        pixel_width, pixel_height = final_width, final_height
        for _ in range(int(intent.rotation) // 90):
            x0, y0, x1, y1 = pixel_height - y1, x0, pixel_height - y0, x1
            pixel_width, pixel_height = pixel_height, pixel_width
        if (pixel_width, pixel_height) != (report.width_pixels, report.height_pixels):
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "redaction_ocr.mask_geometry")
        # PDF text coordinates start at bottom left; raster coordinates at top left.
        result.append(
            (
                x0 * report.width_points / pixel_width,
                (pixel_height - y1) * report.height_points / pixel_height,
                x1 * report.width_points / pixel_width,
                (pixel_height - y0) * report.height_points / pixel_height,
            )
        )
    return tuple(result)


def _verify_text_exclusion(
    path: Path,
    exclusions: tuple[tuple[float, float, float, float], ...],
    limits: RedactionReconstructionLimits,
    control: RedactionReconstructionControl,
) -> None:
    """Reject hidden/visible glyphs or complete word boxes touching a mask."""

    def check(box: tuple[float, float, float, float]) -> None:
        if any(
            box[0] <= region[2]
            and box[2] >= region[0]
            and box[1] <= region[3]
            and box[3] >= region[1]
            for region in exclusions
        ):
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH, "redaction_ocr.text_exclusion"
            )

    document = pdfium.PdfDocument(path)
    try:
        page = document[0]
        try:
            textpage = page.get_textpage()
            try:
                count = textpage.count_chars()
                if type(count) is not int or not 0 <= count <= limits.maximum_pdf_objects:
                    reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "redaction_ocr.text_count")
                word: tuple[float, float, float, float] | None = None
                for index in range(count):
                    _checkpoint(control)
                    text = textpage.get_text_range(index, 1, errors="strict")
                    if type(text) is not str or not text:
                        reject_pdf_export(
                            PdfExportErrorCode.RESULT_INVALID, "redaction_ocr.text_bounds"
                        )
                    if text.isspace():
                        if word is not None:
                            check(word)
                        word = None
                        continue
                    values = tuple(float(value) for value in textpage.get_charbox(index))
                    if (
                        len(values) != 4
                        or any(not math.isfinite(value) for value in values)
                        or values[0] >= values[2]
                        or values[1] >= values[3]
                    ):
                        reject_pdf_export(
                            PdfExportErrorCode.RESULT_INVALID, "redaction_ocr.text_bounds"
                        )
                    box = (values[0], values[1], values[2], values[3])
                    check(box)
                    word = (
                        box
                        if word is None
                        else (
                            min(word[0], box[0]),
                            min(word[1], box[1]),
                            max(word[2], box[2]),
                            max(word[3], box[3]),
                        )
                    )
                if word is not None:
                    check(word)
            finally:
                textpage.close()
        finally:
            page.close()
    finally:
        document.close()


def _remove_tree(root: Path, maximum_entries: int) -> None:
    """Remove only our random private subtree, without following reparse points."""
    remaining = [maximum_entries]

    def remove(path: Path, depth: int) -> None:
        if depth > _MAXIMUM_PRIVATE_DEPTH or remaining[0] < 1:
            raise OSError
        remaining[0] -= 1
        info = path.lstat()
        reparse = bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)
        if stat.S_ISDIR(info.st_mode) and not reparse:
            with os.scandir(path) as entries:
                for entry in entries:
                    remove(Path(entry.path), depth + 1)
            path.rmdir()
        elif stat.S_ISDIR(info.st_mode):
            path.rmdir()
        elif stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            path.unlink()
        else:
            raise OSError

    try:
        if root.exists() or root.is_symlink():
            remove(root, 0)
    except OSError:
        reject_pdf_export(PdfExportErrorCode.ASSEMBLY_FAILED, "redaction_ocr.cleanup")

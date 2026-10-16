"""Worker-private, lossless reconstruction of one affected PDF page.

Only sanitized opaque pixels cross into the fresh page builder. This component
does not publish a file, regenerate OCR, or approve the permanent-removal gate.
"""

from __future__ import annotations

import hashlib
import math
import mmap
import os
import stat
import zlib
from collections.abc import Buffer
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Never, Protocol
from uuid import uuid4

import pikepdf

from paperworkshell.domain.image_pages import ImageRgba8Layout
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode, reject_pdf_export
from paperworkshell.domain.pdf_geometry import (
    MAX_PDF_PAGE_COORDINATE_MAGNITUDE_UNITS,
    MAX_PDF_PAGE_SPAN_UNITS,
    PdfPageGeometryLimits,
    fit_pdf_page_geometry,
)
from paperworkshell.domain.pdf_rendering import (
    MAX_PDF_RENDER_BYTES,
    MAX_PDF_RENDER_DIMENSION_PIXELS,
    MAX_PDF_RENDER_PAGES,
    MAX_PDF_RENDER_PIXELS,
    MAX_PDF_RENDER_WALL_TIME_MS,
    PdfPageBox,
    PdfPageRenderLimits,
    PdfPageRenderRequest,
    PdfRenderError,
    PdfRenderErrorCode,
)
from paperworkshell.domain.redaction_reconstruction import (
    RECONSTRUCTION_DPI,
    RedactionReconstructionReport,
    RedactionReconstructionRequest,
    validate_reconstruction_report,
)
from paperworkshell.domain.resource_policy import ResourcePolicy
from paperworkshell.pdf.page_geometry import PikePdfPageGeometryResolver
from paperworkshell.pdf.pdfium_renderer import PdfiumPageRenderer
from paperworkshell.pdf.raster_pdf_page import (
    RasterPdfPageAdapter,
    RasterPdfPageGeometry,
    RasterPdfPageLimits,
)
from paperworkshell.pdf.renderer import PdfPageRenderer

_MILLION = 1_000_000
_CHUNK_BYTES = 1024 * 1024
_REPARSE_POINT = 0x400
_MASK_MARGIN_PIXELS = 1


class RedactionReconstructionControl(Protocol):
    def cancellation_requested(self) -> bool: ...

    def deadline_expired(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class RedactionReconstructionLimits:
    maximum_source_bytes: int
    maximum_pages: int
    maximum_width_pixels: int
    maximum_height_pixels: int
    maximum_pixels: int
    maximum_raster_bytes: int
    maximum_memory_bytes: int
    maximum_output_bytes: int
    maximum_temporary_storage_bytes: int
    maximum_pdf_objects: int
    wall_time_ms: int

    def __post_init__(self) -> None:
        if any(
            type(getattr(self, name)) is not int or getattr(self, name) < 1
            for name in self.__dataclass_fields__
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "reconstruction.limits")
        if (
            self.maximum_pages > MAX_PDF_RENDER_PAGES
            or max(self.maximum_width_pixels, self.maximum_height_pixels)
            > MAX_PDF_RENDER_DIMENSION_PIXELS
            or self.maximum_pixels > MAX_PDF_RENDER_PIXELS
            or self.maximum_raster_bytes > MAX_PDF_RENDER_BYTES
            or self.wall_time_ms > MAX_PDF_RENDER_WALL_TIME_MS
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "reconstruction.limits")

    @classmethod
    def from_resource_policy(cls, policy: ResourcePolicy) -> RedactionReconstructionLimits:
        if type(policy) is not ResourcePolicy:
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "reconstruction.policy")
        raster = min(
            MAX_PDF_RENDER_BYTES,
            policy.worker_process_memory_bytes // 4,
            policy.maximum_temporary_storage_bytes // 3,
        )
        pixels = min(MAX_PDF_RENDER_PIXELS, raster // 4)
        dimension = min(MAX_PDF_RENDER_DIMENSION_PIXELS, pixels)
        return cls(
            maximum_source_bytes=policy.maximum_source_bytes,
            maximum_pages=min(MAX_PDF_RENDER_PAGES, policy.maximum_page_count),
            maximum_width_pixels=dimension,
            maximum_height_pixels=dimension,
            maximum_pixels=pixels,
            maximum_raster_bytes=raster,
            maximum_memory_bytes=policy.worker_process_memory_bytes,
            maximum_output_bytes=min(
                policy.maximum_source_bytes, policy.maximum_temporary_storage_bytes // 4
            ),
            maximum_temporary_storage_bytes=policy.maximum_temporary_storage_bytes,
            maximum_pdf_objects=100_000,
            wall_time_ms=min(MAX_PDF_RENDER_WALL_TIME_MS, policy.worker_cpu_time_ms),
        )


@dataclass(frozen=True, slots=True)
class RedactionReconstructionAdapter:
    renderer: PdfPageRenderer = field(default_factory=PdfiumPageRenderer)
    page_builder: RasterPdfPageAdapter = field(default_factory=RasterPdfPageAdapter)

    def reconstruct(
        self,
        source: BinaryIO,
        output_path: Path,
        scratch_directory: Path,
        request: RedactionReconstructionRequest,
        limits: RedactionReconstructionLimits,
        control: RedactionReconstructionControl,
    ) -> RedactionReconstructionReport:
        _validate_paths(output_path, scratch_directory)
        if (
            type(request) is not RedactionReconstructionRequest
            or type(limits) is not RedactionReconstructionLimits
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "reconstruction.request")
        request.__post_init__()
        limits.__post_init__()
        _checkpoint(control)
        initial = _measure_source(source, limits.maximum_source_bytes, control)
        if initial != (request.source_byte_size, request.source_sha256):
            reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "reconstruction.source")
        signed = _inspect_source(source, request, limits, control)
        raw_path = scratch_directory / f"{uuid4().hex}.rgba"
        transformed_path = scratch_directory / f"{uuid4().hex}.rgba"
        candidate_owned = False
        completed = False
        try:
            render_limits = _render_limits(limits)
            probe = PdfPageRenderRequest(request.source_page_index, 1, 1, PdfPageBox.CROP)
            geometry = PikePdfPageGeometryResolver().resolve(
                source,
                probe,
                render_limits,
                PdfPageGeometryLimits(
                    MAX_PDF_PAGE_COORDINATE_MAGNITUDE_UNITS, MAX_PDF_PAGE_SPAN_UNITS
                ),
            )
            base_width = geometry.effective_box.width * geometry.user_unit
            base_height = geometry.effective_box.height * geometry.user_unit
            if int(geometry.intrinsic_rotation) % 180:
                base_width, base_height = base_height, base_width
            width_pixels = math.ceil(base_width * RECONSTRUCTION_DPI / 72)
            height_pixels = math.ceil(base_height * RECONSTRUCTION_DPI / 72)
            if (
                width_pixels > limits.maximum_width_pixels
                or height_pixels > limits.maximum_height_pixels
            ):
                reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "reconstruction.dimensions")
            render_request = PdfPageRenderRequest(
                request.source_page_index, width_pixels, height_pixels, PdfPageBox.CROP
            )
            render_geometry = fit_pdf_page_geometry(geometry, render_request, render_limits)
            layout = render_geometry.layout
            if (
                request.source_byte_size + layout.byte_count * 2 + limits.maximum_output_bytes
                > limits.maximum_temporary_storage_bytes
            ):
                reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "reconstruction.scratch")
            boxes = tuple(
                _pixel_box(
                    mark.box, layout.width_pixels, layout.height_pixels, margin=_MASK_MARGIN_PIXELS
                )
                for mark in request.marks
            )
            with raw_path.open("xb") as raw:
                sink = _SanitizingSink(
                    raw, layout.width_pixels, layout.height_pixels, boxes, control
                )
                rendered = self.renderer.render(
                    source, sink, render_request, render_limits, control
                )
                sink.finish()
            if (rendered.width_pixels, rendered.height_pixels, rendered.bytes_written) != (
                layout.width_pixels,
                layout.height_pixels,
                layout.byte_count,
            ):
                reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "reconstruction.render_result")
            cropped = _pixel_box(request.crop, layout.width_pixels, layout.height_pixels, margin=0)
            final_layout, sanitized_sha256, sanitized_rgb_sha256 = _transform_sanitized(
                raw_path,
                transformed_path,
                layout.width_pixels,
                layout.height_pixels,
                cropped,
                int(request.rotation) // 90,
                control,
            )
            raw_path.unlink()
            width_points = base_width * request.crop.width / _MILLION
            height_points = base_height * request.crop.height / _MILLION
            if int(request.rotation) % 180:
                width_points, height_points = height_points, width_points
            page_limits = RasterPdfPageLimits(
                limits.maximum_width_pixels,
                limits.maximum_height_pixels,
                limits.maximum_pixels,
                limits.maximum_raster_bytes,
                limits.maximum_output_bytes,
                limits.maximum_memory_bytes,
                limits.maximum_pdf_objects,
            )
            result = self.page_builder.build(
                transformed_path,
                output_path,
                final_layout,
                page_limits,
                control,
                geometry=RasterPdfPageGeometry(float(width_points), float(height_points)),
            )
            candidate_owned = True
            _verify_generated_structure(
                output_path, final_layout, sanitized_rgb_sha256, limits, control
            )
            _verify_render(
                self.renderer, output_path, final_layout, sanitized_sha256, render_limits, control
            )
            if _measure_source(source, limits.maximum_source_bytes, control) != initial:
                reject_pdf_export(
                    PdfExportErrorCode.SOURCE_CHANGED, "reconstruction.source_changed"
                )
            if _measure_path(output_path, limits.maximum_output_bytes, control) != (
                result.byte_size,
                result.sha256,
            ):
                reject_pdf_export(
                    PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH,
                    "reconstruction.output_changed",
                )
            report = RedactionReconstructionReport(
                request_sha256=request.fingerprint,
                page_id=request.page_id,
                source_sha256=request.source_sha256,
                output_sha256=result.sha256,
                output_byte_size=result.byte_size,
                width_pixels=final_layout.width_pixels,
                height_pixels=final_layout.height_pixels,
                width_points=float(width_points),
                height_points=float(height_points),
                mark_count=len(request.marks),
                unsigned_derivative=True,
                source_signature_detected=signed,
            )
            validate_reconstruction_report(request, report)
            _checkpoint(control)
            _remove_private(raw_path)
            _remove_private(transformed_path)
            completed = True
            return report
        except PdfRenderError as error:
            _map_render_error(error)
        except PdfExportError:
            raise
        except MemoryError, RecursionError:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "reconstruction.resources")
        except Exception:
            reject_pdf_export(PdfExportErrorCode.ASSEMBLY_FAILED, "reconstruction.adapter")
        finally:
            # A successful return owns only the private PDF; every raw pixel plane
            # is disposable, including on cancellation and parser failure.
            try:
                _remove_private(raw_path)
            finally:
                try:
                    _remove_private(transformed_path)
                finally:
                    if candidate_owned and not completed:
                        _remove_private(output_path)


def _validate_paths(output: Path, scratch: Path) -> None:
    if (
        not isinstance(output, Path)
        or not output.is_absolute()
        or not isinstance(scratch, Path)
        or not scratch.is_absolute()
    ):
        reject_pdf_export(PdfExportErrorCode.DESTINATION_INVALID, "reconstruction.paths")
    try:
        for directory in (output.parent, scratch):
            info = directory.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
            ):
                raise OSError
        if output.exists() or output.is_symlink():
            reject_pdf_export(PdfExportErrorCode.DESTINATION_EXISTS, "reconstruction.exists")
    except PdfExportError:
        raise
    except OSError:
        reject_pdf_export(PdfExportErrorCode.DESTINATION_INVALID, "reconstruction.paths")


def _render_limits(limits: RedactionReconstructionLimits) -> PdfPageRenderLimits:
    return PdfPageRenderLimits(
        limits.maximum_pages,
        limits.maximum_width_pixels,
        limits.maximum_height_pixels,
        limits.maximum_pixels,
        limits.maximum_raster_bytes,
        limits.maximum_raster_bytes,
        min(MAX_PDF_RENDER_BYTES, limits.maximum_memory_bytes),
        limits.wall_time_ms,
    )


def _checkpoint(control: RedactionReconstructionControl) -> None:
    try:
        cancelled, expired = control.cancellation_requested(), control.deadline_expired()
    except Exception:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "reconstruction.control")
    if type(cancelled) is not bool or type(expired) is not bool:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "reconstruction.control")
    if cancelled:
        reject_pdf_export(PdfExportErrorCode.CANCELLED, "reconstruction.cancelled")
    if expired:
        reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "reconstruction.deadline")


def _measure_source(
    source: BinaryIO, maximum: int, control: RedactionReconstructionControl
) -> tuple[int, str]:
    original: int | None = None
    try:
        original = source.tell()
        if type(original) is not int or original < 0:
            raise OSError
        source.seek(0)
        digest, size = hashlib.sha256(), 0
        while True:
            _checkpoint(control)
            block = source.read(min(_CHUNK_BYTES, maximum - size + 1))
            if type(block) is not bytes:
                raise OSError
            if not block:
                break
            size += len(block)
            if size > maximum:
                reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "reconstruction.source_bytes")
            digest.update(block)
        return size, digest.hexdigest()
    except PdfExportError:
        raise
    except Exception:
        reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "reconstruction.source_read")
    finally:
        if original is not None:
            try:
                if source.seek(original) != original:
                    raise OSError
            except Exception:
                reject_pdf_export(
                    PdfExportErrorCode.SOURCE_CHANGED, "reconstruction.source_restore"
                )


def _measure_path(
    path: Path, maximum: int, control: RedactionReconstructionControl
) -> tuple[int, str]:
    with path.open("rb") as source:
        return _measure_source(source, maximum, control)


def _inspect_source(
    source: BinaryIO,
    request: RedactionReconstructionRequest,
    limits: RedactionReconstructionLimits,
    control: RedactionReconstructionControl,
) -> bool:
    original: int | None = None
    try:
        original = source.tell()
        if type(original) is not int or original < 0 or source.seek(0) != 0:
            raise OSError
        with pikepdf.Pdf.open(
            source, attempt_recovery=False, suppress_warnings=True, inherit_page_attributes=False
        ) as document:
            if document.is_encrypted:
                reject_pdf_export(PdfExportErrorCode.SOURCE_UNSUPPORTED, "reconstruction.encrypted")
            if (
                len(document.pages) != request.source_page_count
                or request.source_page_count > limits.maximum_pages
            ):
                reject_pdf_export(PdfExportErrorCode.SOURCE_INCONSISTENT, "reconstruction.pages")
            if len(document.objects) > limits.maximum_pdf_objects:
                reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "reconstruction.objects")
            if document.check_pdf_syntax(progress=lambda _value: _checkpoint(control)):
                reject_pdf_export(
                    PdfExportErrorCode.SOURCE_INCONSISTENT, "reconstruction.source_syntax"
                )
            signed = False
            for item in document.objects:
                _checkpoint(control)
                if isinstance(item, pikepdf.Dictionary):
                    signed |= (
                        item.get("/FT") == pikepdf.Name.Sig
                        or item.get("/Type") == pikepdf.Name.Sig
                        or "/ByteRange" in item
                    )
            return signed
    except PdfExportError:
        raise
    except MemoryError, RecursionError:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "reconstruction.source_resources")
    except Exception:
        reject_pdf_export(PdfExportErrorCode.SOURCE_INCONSISTENT, "reconstruction.source_pdf")
    finally:
        if original is not None:
            try:
                if source.seek(original) != original:
                    raise OSError
            except Exception:
                reject_pdf_export(
                    PdfExportErrorCode.SOURCE_CHANGED, "reconstruction.source_restore"
                )


def _pixel_box(
    box: NormalizedPageBox, width: int, height: int, *, margin: int
) -> tuple[int, int, int, int]:
    return (
        max(0, box.x * width // _MILLION - margin),
        max(0, box.y * height // _MILLION - margin),
        min(width, (box.right * width + _MILLION - 1) // _MILLION + margin),
        min(height, (box.bottom * height + _MILLION - 1) // _MILLION + margin),
    )


class _SanitizingSink:
    """Burn each row before it can reach a raw-raster file or encoder."""

    def __init__(
        self,
        destination: BinaryIO,
        width: int,
        height: int,
        boxes: tuple[tuple[int, int, int, int], ...],
        control: RedactionReconstructionControl,
    ) -> None:
        self.destination, self.width, self.height = destination, width, height
        self.control, self.row = control, 0
        # Sweep the bounded mark set. Coincident/overlapping rectangles are
        # burned once, so thousands of full-page marks do not multiply pixel
        # allocation and write work on every row.
        self.events: dict[int, list[tuple[tuple[int, int], int]]] = {}
        self.active: dict[tuple[int, int], int] = {}
        self.ranges: tuple[tuple[int, int], ...] = ()
        for left, top, right, bottom in boxes:
            _checkpoint(control)
            self.events.setdefault(top, []).append(((left, right), 1))
            self.events.setdefault(bottom, []).append(((left, right), -1))

    def write(self, data: Buffer, /) -> int:
        _checkpoint(self.control)
        view = memoryview(data).cast("B")
        if view.nbytes != self.width * 4 or self.row >= self.height:
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "reconstruction.raster_row")
        row = bytearray(view)
        if row[3::4].count(255) != self.width:
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "reconstruction.raster_alpha")
        events = self.events.pop(self.row, ())
        if events:
            for interval, delta in events:
                count = self.active.get(interval, 0) + delta
                if count:
                    self.active[interval] = count
                else:
                    del self.active[interval]
            merged: list[tuple[int, int]] = []
            for left, right in sorted(self.active):
                if merged and left <= merged[-1][1]:
                    merged[-1] = merged[-1][0], max(merged[-1][1], right)
                else:
                    merged.append((left, right))
            self.ranges = tuple(merged)
        for left, right in self.ranges:
            row[left * 4 : right * 4] = b"\x00\x00\x00\xff" * (right - left)
        if self.destination.write(row) != len(row):
            reject_pdf_export(PdfExportErrorCode.ASSEMBLY_FAILED, "reconstruction.raster_write")
        self.row += 1
        return len(row)

    def flush(self) -> None:
        self.destination.flush()

    def finish(self) -> None:
        if self.row != self.height:
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "reconstruction.raster_length")
        self.flush()


def _transform_sanitized(
    source: Path,
    destination: Path,
    width: int,
    height: int,
    crop: tuple[int, int, int, int],
    rotation: int,
    control: RedactionReconstructionControl,
) -> tuple[ImageRgba8Layout, str, str]:
    """Copy cropped quarter-turn pixels using one mapped page and one output row."""
    left, top, right, bottom = crop
    out_width, out_height = right - left, bottom - top
    if rotation % 2:
        out_width, out_height = out_height, out_width
    layout = ImageRgba8Layout(
        out_width, out_height, out_width * 4, out_width * out_height, out_width * out_height * 4
    )
    digest = hashlib.sha256()
    rgb_digest = hashlib.sha256()
    with source.open("rb") as raw, destination.open("xb") as target:
        if os.fstat(raw.fileno()).st_size != width * height * 4:
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "reconstruction.raster_length")
        with mmap.mmap(raw.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
            for y in range(out_height):
                _checkpoint(control)
                if rotation == 0:
                    start, step = ((top + y) * width + left) * 4, 4
                elif rotation == 1:
                    start, step = ((bottom - 1) * width + left + y) * 4, -width * 4
                elif rotation == 2:
                    start, step = ((bottom - 1 - y) * width + right - 1) * 4, -4
                else:
                    start, step = (top * width + right - 1 - y) * 4, width * 4
                row = bytearray(layout.row_stride_bytes)
                for channel in range(4):
                    stop = start + channel + step * out_width
                    row[channel::4] = mapped[start + channel : None if stop < 0 else stop : step]
                if len(row) != layout.row_stride_bytes or target.write(row) != len(row):
                    reject_pdf_export(
                        PdfExportErrorCode.ASSEMBLY_FAILED, "reconstruction.transform_write"
                    )
                digest.update(row)
                rgb = bytearray(out_width * 3)
                for channel in range(3):
                    rgb[channel::3] = row[channel::4]
                rgb_digest.update(rgb)
            target.flush()
    return layout, digest.hexdigest(), rgb_digest.hexdigest()


def _verify_generated_structure(
    path: Path,
    layout: ImageRgba8Layout,
    rgb_sha256: str,
    limits: RedactionReconstructionLimits,
    control: RedactionReconstructionControl,
) -> None:
    with pikepdf.Pdf.open(
        path, attempt_recovery=False, suppress_warnings=True, inherit_page_attributes=False
    ) as document:
        _checkpoint(control)
        if (
            document.is_encrypted
            or len(document.pages) != 1
            or len(document.objects) > limits.maximum_pdf_objects
        ):
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "reconstruction.reopen"
            )
        page = document.pages[0].obj
        if set(map(str, page.keys())) != {
            "/Type",
            "/MediaBox",
            "/Parent",
            "/Resources",
            "/Contents",
        }:
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "reconstruction.page_objects"
            )
        if set(map(str, page.Resources.keys())) != {"/XObject"} or set(
            map(str, page.Resources.XObject.keys())
        ) != {"/Im0"}:
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "reconstruction.resources"
            )
        image = page.Resources.XObject.Im0
        if (
            image.Subtype != pikepdf.Name.Image
            or image.Width != layout.width_pixels
            or image.Height != layout.height_pixels
        ):
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "reconstruction.image"
            )
        if (
            set(map(str, image.keys()))
            != {
                "/Type",
                "/Subtype",
                "/Width",
                "/Height",
                "/ColorSpace",
                "/BitsPerComponent",
                "/Interpolate",
                "/Filter",
                "/Length",
            }
            or image.get(pikepdf.Name.Interpolate, False) is not False
        ):
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "reconstruction.image_objects"
            )
        parents = document.Root.Pages
        if (
            set(map(str, parents.keys())) != {"/Type", "/Count", "/Kids"}
            or parents.Count != 1
            or len(parents.Kids) != 1
        ):
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "reconstruction.page_tree"
            )
        operations = pikepdf.parse_content_stream(document.pages[0])
        if (
            len(operations) != 4
            or [str(item.operator) for item in operations] != ["q", "cm", "Do", "Q"]
            or tuple(operations[0].operands) != ()
            or tuple(operations[2].operands) != (pikepdf.Name.Im0,)
            or tuple(operations[3].operands) != ()
        ):
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "reconstruction.content"
            )
        matrix = tuple(float(value) for value in operations[1].operands)
        media = tuple(float(value) for value in page.MediaBox)
        if matrix != (media[2], 0.0, 0.0, media[3], 0.0, 0.0):
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "reconstruction.placement"
            )
        expected_ids = {
            item.objgen for item in (document.Root, parents, page, page.Contents, image)
        }
        for item in document.objects:
            _checkpoint(control)
            if item.objgen in expected_ids:
                continue
            if not isinstance(item, pikepdf.Stream) or item.get("/Type") not in {
                pikepdf.Name.ObjStm,
                pikepdf.Name.XRef,
            }:
                reject_pdf_export(
                    PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "reconstruction.extra_objects"
                )
        if set(map(str, document.Root.keys())) != {"/Type", "/Pages"}:
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "reconstruction.catalog"
            )
        if "/Info" in document.trailer or "/Prev" in document.trailer:
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "reconstruction.trailer"
            )
        if (
            image.Filter != pikepdf.Name.FlateDecode
            or image.ColorSpace != pikepdf.Name.DeviceRGB
            or image.BitsPerComponent != 8
        ):
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "reconstruction.image_encoding"
            )
        encoded = image.read_raw_bytes()
        if len(encoded) > limits.maximum_output_bytes:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "reconstruction.image_bytes")
        decompressor = zlib.decompressobj()
        digest = hashlib.sha256()
        expected_bytes, consumed = layout.pixel_count * 3, 0
        for offset in range(0, len(encoded), 64 * 1024):
            _checkpoint(control)
            decoded = decompressor.decompress(
                encoded[offset : offset + 64 * 1024], expected_bytes - consumed + 1
            )
            consumed += len(decoded)
            if consumed > expected_bytes or decompressor.unconsumed_tail:
                reject_pdf_export(
                    PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH, "reconstruction.image_pixels"
                )
            digest.update(decoded)
        if (
            consumed != expected_bytes
            or not decompressor.eof
            or decompressor.unused_data
            or digest.hexdigest() != rgb_sha256
        ):
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH, "reconstruction.image_pixels"
            )


class _DiscardSink:
    def write(self, data: Buffer, /) -> int:
        return memoryview(data).nbytes

    def flush(self) -> None:
        return None


def _verify_render(
    renderer: PdfPageRenderer,
    path: Path,
    layout: ImageRgba8Layout,
    rgba_sha256: str,
    limits: PdfPageRenderLimits,
    control: RedactionReconstructionControl,
) -> None:
    with path.open("rb") as source:
        result = renderer.render(
            source,
            _DiscardSink(),
            PdfPageRenderRequest(0, layout.width_pixels, layout.height_pixels),
            limits,
            control,
        )
    if (
        result.width_pixels != layout.width_pixels
        or result.height_pixels != layout.height_pixels
        or result.pixel_sha256 != rgba_sha256
    ):
        reject_pdf_export(
            PdfExportErrorCode.VERIFICATION_RENDER_FAILED, "reconstruction.render_verify"
        )


def _map_render_error(error: PdfRenderError) -> Never:
    code = {
        PdfRenderErrorCode.CANCELLED: PdfExportErrorCode.CANCELLED,
        PdfRenderErrorCode.TIMED_OUT: PdfExportErrorCode.TIMED_OUT,
        PdfRenderErrorCode.LIMIT_EXCEEDED: PdfExportErrorCode.LIMIT_EXCEEDED,
    }.get(error.code, PdfExportErrorCode.ASSEMBLY_FAILED)
    reject_pdf_export(code, "reconstruction.renderer")


def _remove_private(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        reject_pdf_export(PdfExportErrorCode.ASSEMBLY_FAILED, "reconstruction.cleanup")

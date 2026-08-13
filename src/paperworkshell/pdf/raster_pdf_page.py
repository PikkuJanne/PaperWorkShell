"""Deterministic one-page PDF wrapper for one opaque transformed RGBA raster."""

from __future__ import annotations

import hashlib
import math
import os
import stat
import struct
import zlib
from collections.abc import Buffer
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import BinaryIO, Final, Protocol, cast

import pikepdf

from paperworkshell.domain.image_pages import ImageRgba8Layout
from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    reject_pdf_export,
)

RASTER_PDF_PAGE_VERSION: Final = 1
RASTER_PDF_DPI: Final = 300
MAX_RASTER_PDF_PHYSICAL_POINTS: Final = 1_000_000_000.0
_READ_ROWS: Final = 16
_FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x0000_0400
_NAME_METADATA: Final = pikepdf.Name("/Metadata")


class RasterPdfPageControl(Protocol):
    """Cooperative probes supplied by the owning one-shot worker."""

    def cancellation_requested(self) -> bool: ...

    def deadline_expired(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class RasterPdfPageLimits:
    """Trusted raster, candidate, structure, and memory ceilings."""

    maximum_width_pixels: int
    maximum_height_pixels: int
    maximum_pixels: int
    maximum_input_bytes: int
    maximum_output_bytes: int
    maximum_memory_bytes: int
    maximum_pdf_objects: int

    def __post_init__(self) -> None:
        values = (
            self.maximum_width_pixels,
            self.maximum_height_pixels,
            self.maximum_pixels,
            self.maximum_input_bytes,
            self.maximum_output_bytes,
            self.maximum_memory_bytes,
            self.maximum_pdf_objects,
        )
        if any(type(value) is not int or value < 1 for value in values):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.raster_limits")


@dataclass(frozen=True, slots=True, repr=False)
class RasterPdfPageResult:
    """Content-free facts for one strictly reopened raster-backed page."""

    width_pixels: int
    height_pixels: int
    byte_size: int
    sha256: str = field(repr=False)
    dpi: int = RASTER_PDF_DPI
    result_version: int = RASTER_PDF_PAGE_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.width_pixels) is not int
            or self.width_pixels < 1
            or type(self.height_pixels) is not int
            or self.height_pixels < 1
            or type(self.byte_size) is not int
            or self.byte_size < 1
            or type(self.sha256) is not str
            or len(self.sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.sha256)
            or type(self.dpi) is not int
            or self.dpi != RASTER_PDF_DPI
            or type(self.result_version) is not int
            or self.result_version != RASTER_PDF_PAGE_VERSION
        ):
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.raster_result")


@dataclass(frozen=True, slots=True, repr=False)
class RasterPdfPageGeometry:
    """Exact physical page size requested for one transformed raster."""

    width_points: float
    height_points: float

    def __post_init__(self) -> None:
        if (
            type(self.width_points) is not float
            or not math.isfinite(self.width_points)
            or not 0 < self.width_points <= MAX_RASTER_PDF_PHYSICAL_POINTS
            or type(self.height_points) is not float
            or not math.isfinite(self.height_points)
            or not 0 < self.height_points <= MAX_RASTER_PDF_PHYSICAL_POINTS
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.raster_geometry")

    def __repr__(self) -> str:
        return "RasterPdfPageGeometry(<physical-page-values-hidden>)"


class _CandidateLimitExceeded(OSError):
    def __init__(self) -> None:
        super().__init__()


class _BoundedOutput:
    """Seekable pikepdf output that cannot grow beyond the trusted cap."""

    __slots__ = ("_extent", "_limit_exceeded", "_maximum_bytes", "_stream")

    def __init__(self, stream: BinaryIO, maximum_bytes: int) -> None:
        self._stream = stream
        self._maximum_bytes = maximum_bytes
        self._extent = 0
        self._limit_exceeded = False

    @property
    def limit_exceeded(self) -> bool:
        return self._limit_exceeded

    def write(self, data: Buffer, /) -> int:
        try:
            size = memoryview(data).nbytes
            position = self._stream.tell()
        except BufferError, OSError, TypeError, ValueError:
            raise OSError from None
        self._require_extent(position + size)
        written = self._stream.write(data)
        if type(written) is not int or not 0 <= written <= size:
            raise OSError from None
        self._extent = max(self._extent, position + written)
        return written

    def seek(self, offset: int, whence: int = os.SEEK_SET, /) -> int:
        try:
            if whence == os.SEEK_SET:
                target = offset
            elif whence == os.SEEK_CUR:
                target = self._stream.tell() + offset
            elif whence == os.SEEK_END:
                target = self._extent + offset
            else:
                raise OSError from None
        except OSError:
            raise OSError from None
        if type(target) is not int or target < 0:
            raise OSError from None
        self._require_extent(target)
        position = self._stream.seek(offset, whence)
        if position != target:
            raise OSError from None
        return position

    def tell(self) -> int:
        position = self._stream.tell()
        if type(position) is not int or not 0 <= position <= self._maximum_bytes:
            raise OSError from None
        return position

    def truncate(self, size: int | None = None, /) -> int:
        target = self.tell() if size is None else size
        if type(target) is not int or target < 0:
            raise OSError from None
        self._require_extent(target)
        result = self._stream.truncate(target)
        if result != target:
            raise OSError from None
        self._extent = target
        return target

    def flush(self) -> None:
        self._stream.flush()

    def fileno(self) -> int:
        return self._stream.fileno()

    def readable(self) -> bool:
        return False

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def _require_extent(self, extent: int) -> None:
        if extent <= self._maximum_bytes:
            return
        self._limit_exceeded = True
        raise _CandidateLimitExceeded from None


@dataclass(frozen=True, slots=True)
class RasterPdfPageAdapter:
    """Wrap one authenticated raw page and strictly verify the private PDF."""

    def build(
        self,
        rgba_path: Path,
        output_path: Path,
        layout: ImageRgba8Layout,
        limits: RasterPdfPageLimits,
        control: RasterPdfPageControl,
        *,
        geometry: RasterPdfPageGeometry | None = None,
    ) -> RasterPdfPageResult:
        """Create a private page at fixed image DPI or an exact physical page size."""

        _validate_request(rgba_path, output_path, layout, limits, control, geometry)
        _require_active(control)
        rgb_bytes = layout.pixel_count * 3
        maximum_compressed_bytes = _maximum_zlib_bytes(rgb_bytes)
        # The input stays on disk. Account for compressed chunks, their joined
        # value, pikepdf's native stream copy, and one bounded row conversion.
        if (
            maximum_compressed_bytes * 3 + layout.row_stride_bytes * _READ_ROWS
            > limits.maximum_memory_bytes
        ):
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.raster_memory")
        compressed_rgb, rgb_sha256 = _read_and_compress_opaque_rgb(
            rgba_path,
            layout,
            maximum_compressed_bytes,
            control,
        )

        try:
            stream = output_path.open("x+b")
        except FileExistsError:
            reject_pdf_export(PdfExportErrorCode.DESTINATION_EXISTS, "export.raster_exists")
        except OSError:
            reject_pdf_export(PdfExportErrorCode.DESTINATION_INVALID, "export.raster_path")

        bounded: _BoundedOutput | None = None
        try:
            with stream:
                bounded = _BoundedOutput(stream, limits.maximum_output_bytes)
                _write_pdf(
                    cast(BinaryIO, bounded),
                    compressed_rgb,
                    layout,
                    control,
                    geometry,
                )
                del compressed_rgb
                _flush_output(bounded)
            measured = _measure_file(output_path, limits.maximum_output_bytes, control)
            _strict_verify(output_path, layout, rgb_sha256, limits, control, geometry)
            final = _measure_file(output_path, limits.maximum_output_bytes, control)
            if final != measured:
                reject_pdf_export(
                    PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
                    "export.raster_changed",
                )
            return RasterPdfPageResult(
                width_pixels=layout.width_pixels,
                height_pixels=layout.height_pixels,
                byte_size=final[0],
                sha256=final[1],
            )
        except PdfExportError:
            _remove_candidate(output_path)
            raise
        except MemoryError, RecursionError:
            _remove_candidate(output_path)
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.raster_resources")
        except Exception:
            limit_exceeded = bounded is not None and bounded.limit_exceeded
            _remove_candidate(output_path)
            reject_pdf_export(
                PdfExportErrorCode.LIMIT_EXCEEDED
                if limit_exceeded
                else PdfExportErrorCode.ASSEMBLY_FAILED,
                "export.raster_write",
            )


def _validate_request(
    rgba_path: Path,
    output_path: Path,
    layout: ImageRgba8Layout,
    limits: RasterPdfPageLimits,
    control: RasterPdfPageControl,
    geometry: RasterPdfPageGeometry | None,
) -> None:
    if (
        not isinstance(rgba_path, Path)
        or not rgba_path.is_absolute()
        or not isinstance(output_path, Path)
        or not output_path.is_absolute()
        or rgba_path == output_path
        or type(layout) is not ImageRgba8Layout
        or type(limits) is not RasterPdfPageLimits
        or (geometry is not None and type(geometry) is not RasterPdfPageGeometry)
        or not callable(getattr(control, "cancellation_requested", None))
        or not callable(getattr(control, "deadline_expired", None))
    ):
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.raster_request")
    if (
        layout.width_pixels > limits.maximum_width_pixels
        or layout.height_pixels > limits.maximum_height_pixels
        or layout.pixel_count > limits.maximum_pixels
        or layout.byte_count > limits.maximum_input_bytes
    ):
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.raster_layout")
    try:
        info = os.lstat(rgba_path)
        parent_info = os.lstat(output_path.parent)
    except OSError:
        reject_pdf_export(PdfExportErrorCode.DESTINATION_INVALID, "export.raster_path")
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_size != layout.byte_count
        or getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        or not stat.S_ISDIR(parent_info.st_mode)
        or getattr(parent_info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
    ):
        reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.raster_source")


def _read_and_compress_opaque_rgb(
    rgba_path: Path,
    layout: ImageRgba8Layout,
    maximum_compressed_bytes: int,
    control: RasterPdfPageControl,
) -> tuple[bytes, str]:
    compressor = zlib.compressobj(level=9, wbits=zlib.MAX_WBITS)
    compressed_parts: list[bytes] = []
    compressed_count = 0
    rgb_digest = hashlib.sha256()
    read_size = layout.row_stride_bytes * _READ_ROWS
    try:
        with rgba_path.open("rb") as source:
            consumed = 0
            while consumed < layout.byte_count:
                _require_active(control)
                block = source.read(min(read_size, layout.byte_count - consumed))
                if type(block) is not bytes or not block or len(block) % 4:
                    reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.raster_read")
                alpha = block[3::4]
                if alpha.count(255) != len(alpha):
                    reject_pdf_export(
                        PdfExportErrorCode.SOURCE_UNSUPPORTED,
                        "export.raster_alpha",
                    )
                target = bytearray((len(block) // 4) * 3)
                target[0::3] = block[0::4]
                target[1::3] = block[1::4]
                target[2::3] = block[2::4]
                rgb_digest.update(target)
                compressed = compressor.compress(target)
                del target
                if compressed:
                    compressed_count += len(compressed)
                    if compressed_count > maximum_compressed_bytes:
                        reject_pdf_export(
                            PdfExportErrorCode.LIMIT_EXCEEDED,
                            "export.raster_compressed",
                        )
                    compressed_parts.append(compressed)
                consumed += len(block)
            if source.read(1) != b"":
                reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.raster_read")
    except PdfExportError:
        raise
    except OSError:
        reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.raster_read")
    final = compressor.flush()
    compressed_count += len(final)
    if compressed_count > maximum_compressed_bytes:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.raster_compressed")
    compressed_parts.append(final)
    return b"".join(compressed_parts), rgb_digest.hexdigest()


def _write_pdf(
    destination: BinaryIO,
    compressed_rgb: bytes,
    layout: ImageRgba8Layout,
    control: RasterPdfPageControl,
    geometry: RasterPdfPageGeometry | None,
) -> None:
    width_points, height_points = _page_size_points(layout, geometry)
    width_ascii = _decimal_ascii(width_points)
    height_ascii = _decimal_ascii(height_points)
    content = f"q {width_ascii} 0 0 {height_ascii} 0 0 cm /Im0 Do Q\n".encode("ascii")
    try:
        with pikepdf.Pdf.new() as document:
            image = pikepdf.Stream(
                document,
                compressed_rgb,
                Type=pikepdf.Name.XObject,
                Subtype=pikepdf.Name.Image,
                Width=layout.width_pixels,
                Height=layout.height_pixels,
                ColorSpace=pikepdf.Name.DeviceRGB,
                BitsPerComponent=8,
                Interpolate=False,
                Filter=pikepdf.Name.FlateDecode,
            )
            page = pikepdf.Dictionary(
                Type=pikepdf.Name.Page,
                MediaBox=pikepdf.Array([0, 0, width_points, height_points]),
                Resources=pikepdf.Dictionary(
                    XObject=pikepdf.Dictionary(Im0=image),
                ),
                Contents=pikepdf.Stream(document, content),
            )
            document.pages.append(pikepdf.Page(page))
            if _NAME_METADATA in document.Root:
                del document.Root[_NAME_METADATA]
            _require_active(control)
            document.save(
                destination,
                deterministic_id=True,
                encryption=False,
                compress_streams=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
                normalize_content=False,
                linearize=False,
            )
            _require_active(control)
    except PdfExportError:
        raise
    except MemoryError, RecursionError:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.raster_resources")
    except Exception:
        raise


def _strict_verify(
    output_path: Path,
    layout: ImageRgba8Layout,
    expected_rgb_sha256: str,
    limits: RasterPdfPageLimits,
    control: RasterPdfPageControl,
    geometry: RasterPdfPageGeometry | None,
) -> None:
    expected_width, expected_height = _page_size_points(layout, geometry)
    try:
        with output_path.open("rb") as source:
            with pikepdf.Pdf.open(
                source,
                password="",
                attempt_recovery=False,
                suppress_warnings=True,
                inherit_page_attributes=False,
            ) as document:
                _require_active(control)
                if document.is_encrypted or len(document.pages) != 1:
                    reject_pdf_export(
                        PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
                        "export.raster_reopen",
                    )
                if len(document.objects) > limits.maximum_pdf_objects:
                    reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.raster_objects")
                media_box = tuple(document.pages[0].obj.MediaBox)
                if (
                    len(media_box) != 4
                    or Decimal(str(media_box[0])) != 0
                    or Decimal(str(media_box[1])) != 0
                    or Decimal(str(media_box[2])) != expected_width
                    or Decimal(str(media_box[3])) != expected_height
                ):
                    reject_pdf_export(
                        PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH,
                        "export.raster_geometry",
                    )
                page = document.pages[0].obj
                image = page.Resources.XObject.Im0
                if (
                    image.Type != pikepdf.Name.XObject
                    or image.Subtype != pikepdf.Name.Image
                    or image.Width != layout.width_pixels
                    or image.Height != layout.height_pixels
                    or image.ColorSpace != pikepdf.Name.DeviceRGB
                    or image.BitsPerComponent != 8
                    or image.get(pikepdf.Name.Interpolate, False) is not False
                ):
                    reject_pdf_export(
                        PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH,
                        "export.raster_image",
                    )
                _verify_compressed_image_payload(
                    image,
                    layout.pixel_count * 3,
                    expected_rgb_sha256,
                    control,
                )
                warnings = document.check_pdf_syntax(
                    progress=lambda _value: _require_active(control)
                )
                if type(warnings) is not list or warnings:
                    reject_pdf_export(
                        PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
                        "export.raster_syntax",
                    )
    except PdfExportError:
        raise
    except pikepdf.PasswordError:
        reject_pdf_export(
            PdfExportErrorCode.VERIFICATION_ENCRYPTION_FAILED,
            "export.raster_encrypted",
        )
    except pikepdf.PdfError, RuntimeError, ValueError, OSError:
        reject_pdf_export(
            PdfExportErrorCode.VERIFICATION_REOPEN_FAILED,
            "export.raster_reopen",
        )


def _measure_file(
    path: Path,
    maximum_bytes: int,
    control: RasterPdfPageControl,
) -> tuple[int, str]:
    digest = hashlib.sha256()
    consumed = 0
    try:
        with path.open("rb") as source:
            while True:
                _require_active(control)
                block = source.read(1024 * 1024)
                if type(block) is not bytes:
                    raise OSError
                if not block:
                    break
                consumed += len(block)
                if consumed > maximum_bytes:
                    reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.raster_bytes")
                digest.update(block)
    except PdfExportError:
        raise
    except OSError:
        reject_pdf_export(PdfExportErrorCode.VERIFICATION_REOPEN_FAILED, "export.raster_read")
    if consumed < 1:
        reject_pdf_export(PdfExportErrorCode.ASSEMBLY_FAILED, "export.raster_empty")
    return consumed, digest.hexdigest()


def _verify_compressed_image_payload(
    image: object,
    expected_bytes: int,
    expected_sha256: str,
    control: RasterPdfPageControl,
) -> None:
    try:
        encoded = image.read_raw_bytes()  # type: ignore[attr-defined]
        if type(encoded) is not bytes:
            raise OSError
        decompressor = zlib.decompressobj(wbits=zlib.MAX_WBITS)
        digest = hashlib.sha256()
        consumed = 0
        for offset in range(0, len(encoded), 64 * 1024):
            _require_active(control)
            decoded = decompressor.decompress(encoded[offset : offset + 64 * 1024])
            consumed += len(decoded)
            if consumed > expected_bytes:
                raise OSError
            digest.update(decoded)
        final = decompressor.flush()
        consumed += len(final)
        digest.update(final)
        if (
            consumed != expected_bytes
            or not decompressor.eof
            or decompressor.unused_data
            or digest.hexdigest() != expected_sha256
        ):
            raise OSError
    except PdfExportError:
        raise
    except Exception:
        reject_pdf_export(
            PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH,
            "export.raster_pixels",
        )


def _maximum_zlib_bytes(source_bytes: int) -> int:
    # zlib's documented deflate bound for the default memory level.
    return source_bytes + ((source_bytes + 7) >> 3) + ((source_bytes + 63) >> 6) + 11


def _fixed_dpi_points(pixels: int) -> Decimal:
    """Return the exact decimal value of PDFium's nearest binary32 point size."""

    exact_points = float(Decimal(pixels * 6) / Decimal(25))
    native_points = struct.unpack(">f", struct.pack(">f", exact_points))[0]
    return Decimal(format(Decimal.from_float(native_points), ".15g"))


def _page_size_points(
    layout: ImageRgba8Layout,
    geometry: RasterPdfPageGeometry | None,
) -> tuple[Decimal, Decimal]:
    if geometry is None:
        return (
            _fixed_dpi_points(layout.width_pixels),
            _fixed_dpi_points(layout.height_pixels),
        )
    return (
        Decimal(format(geometry.width_points, ".15g")),
        Decimal(format(geometry.height_points, ".15g")),
    )


def _flush_output(stream: _BoundedOutput) -> None:
    try:
        stream.flush()
        os.fsync(stream.fileno())
    except OSError:
        reject_pdf_export(PdfExportErrorCode.ASSEMBLY_FAILED, "export.raster_flush")


def _decimal_ascii(value: Decimal) -> str:
    encoded = format(value, "f")
    if "." in encoded:
        encoded = encoded.rstrip("0").rstrip(".")
    return encoded or "0"


def _require_active(control: RasterPdfPageControl) -> None:
    try:
        cancelled = control.cancellation_requested()
        expired = control.deadline_expired()
    except Exception:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.raster_control")
    if type(cancelled) is not bool or type(expired) is not bool:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.raster_control")
    if cancelled:
        reject_pdf_export(PdfExportErrorCode.CANCELLED, "export.raster_operation")
    if expired:
        reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "export.raster_operation")


def _remove_candidate(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.raster_cleanup")

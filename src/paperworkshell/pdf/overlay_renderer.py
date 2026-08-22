"""Bounded exact-closure adapter for deterministic vector overlay pages."""

from __future__ import annotations

import hashlib
import hmac
import logging
import math
import os
import struct
import threading
import unicodedata
import zlib
from collections.abc import Buffer, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation
from importlib import metadata as importlib_metadata
from io import BytesIO
from pathlib import Path
from typing import Any, Final, Never, cast
from uuid import UUID

import fontTools  # type: ignore[import-untyped]
import fpdf
import pikepdf
import uharfbuzz  # type: ignore[import-untyped]
from fontTools.ttLib import TTFont, TTLibError  # type: ignore[import-untyped]
from fpdf import FPDF
from fpdf.errors import FPDFException
from fpdf.fonts import HarfBuzzFont, TTFFont
from fpdf.line_break import Fragment

from paperworkshell.overlays.models import (
    DEFAULT_FONT_TOKEN,
    MarkOverlayPayload,
    OverlayKind,
    OverlayRecord,
    RgbaColor,
    ShapeOverlayPayload,
    ShapePrimitive,
    SignatureImageOverlayPayload,
    TextualOverlayPayload,
)
from paperworkshell.overlays.rendering import (
    OVERLAY_RENDER_POINT_QUANTUM,
    OverlayCanvasBox,
    OverlayPageRenderRequest,
    OverlayPageRenderResult,
    OverlayPdfDestination,
    OverlayRasterAsset,
    OverlayRasterSource,
    OverlayRasterSourceSnapshot,
    OverlayRenderControl,
    OverlayRenderedPlacement,
    OverlayRenderError,
    OverlayRenderErrorCode,
    OverlayRenderLimits,
    OverlayTextMetrics,
    reject_overlay_rendering,
    require_overlay_render_active,
    validate_overlay_render_request,
)

EXPECTED_FPDF2_VERSION: Final = "2.8.8"
EXPECTED_FONTTOOLS_VERSION: Final = "4.63.0"
EXPECTED_UHARFBUZZ_VERSION: Final = "0.56.0"
EXPECTED_PILLOW_VERSION: Final = "12.3.0"
EXPECTED_PIKEPDF_VERSION: Final = "10.10.0"
EXPECTED_QPDF_VERSION: Final = "12.3.2"
EXPECTED_OCRMYPDF_VERSION: Final = "17.10.0"
EXPECTED_ZLIB_VERSION: Final = "1.3.1.zlib-ng"
EXPECTED_ZLIB_RUNTIME_VERSION: Final = "1.3.1.zlib-ng"
EXPECTED_ZLIBNG_VERSION: Final = "2.2.4"

OVERLAY_FONT_RESOURCE_DISTRIBUTION: Final = "ocrmypdf"
OVERLAY_FONT_RESOURCE_PATH: Final = "ocrmypdf/data/NotoSans-Regular.ttf"
OVERLAY_FONT_RESOURCE_SIZE: Final = 556_216
OVERLAY_FONT_RESOURCE_SHA256: Final = (
    "2ec33f84606cbaa0a1a944488e14f97faf2f6a25ecdd8354f5358f06da13c7d9"
)
OVERLAY_FONT_FAMILY: Final = "Noto Sans"
OVERLAY_FONT_FULL_NAME: Final = "Noto Sans"
OVERLAY_FONT_VERSION: Final = "Version 2.007"
OVERLAY_FONT_FPDF_FAMILY: Final = "PWSNotoSans"
OVERLAY_FONT_LICENSE: Final = "OFL-1.1"

_FIXED_CREATION_DATE: Final = datetime(1970, 1, 1, tzinfo=UTC)
_FIXED_PRODUCER: Final = "PaperWorkShell vector overlay renderer v1"
_PRIVATE_OUTPUT_LOGGER_NAMESPACES: Final = ("fpdf", "fontTools.subset")
_PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
_PNG_COLOR_TYPE_RGBA: Final = 6
_PNG_BIT_DEPTH: Final = 8
_MARK_STROKE_DIVISOR: Final = 8.0
_SHAPE_STROKE_DIVISOR: Final = 32.0
_MINIMUM_MARK_STROKE_POINTS: Final = 1.0
_MAXIMUM_MARK_STROKE_POINTS: Final = 6.0
_MINIMUM_SHAPE_STROKE_POINTS: Final = 0.5
_MAXIMUM_SHAPE_STROKE_POINTS: Final = 4.0
_LOCK_POLL_SECONDS: Final = 0.05
_EMITTED_POSITION_HALF_QUANTUM: Final = OVERLAY_RENDER_POINT_QUANTUM / 2

_NAME_ART_BOX: Final = pikepdf.Name("/ArtBox")
_NAME_BLEED_BOX: Final = pikepdf.Name("/BleedBox")
_NAME_CROP_BOX: Final = pikepdf.Name("/CropBox")
_NAME_MEDIA_BOX: Final = pikepdf.Name("/MediaBox")
_NAME_PARENT: Final = pikepdf.Name("/Parent")
_NAME_ROTATE: Final = pikepdf.Name("/Rotate")
_NAME_TRIM_BOX: Final = pikepdf.Name("/TrimBox")
_NAME_USER_UNIT: Final = pikepdf.Name("/UserUnit")
_UNEXPECTED_PAGE_BOXES: Final = (_NAME_ART_BOX, _NAME_BLEED_BOX, _NAME_TRIM_BOX)

_NAME_ROOT_REMOVE: Final = (
    pikepdf.Name("/OpenAction"),
    pikepdf.Name("/PageLayout"),
    pikepdf.Name("/PageMode"),
    pikepdf.Name("/Metadata"),
)
_PROHIBITED_KEYS: Final = frozenset(
    {
        "/A",
        "/AA",
        "/AcroForm",
        "/AF",
        "/Annots",
        "/ByteRange",
        "/EmbeddedFiles",
        "/Encrypt",
        "/EF",
        "/JavaScript",
        "/JS",
        "/Names",
        "/OpenAction",
        "/Perms",
        "/SubmitForm",
        "/URI",
        "/XFA",
    }
)
_PROHIBITED_TYPES: Final = frozenset({"/EmbeddedFile", "/Filespec", "/Sig", "/Action"})
_PROHIBITED_SUBTYPES: Final = frozenset(
    {
        "/FileAttachment",
        "/Link",
        "/Movie",
        "/Redact",
        "/RichMedia",
        "/Screen",
        "/Sound",
        "/Widget",
    }
)
_PROHIBITED_ACTIONS: Final = frozenset(
    {
        "/GoToR",
        "/ImportData",
        "/JavaScript",
        "/Launch",
        "/Rendition",
        "/SubmitForm",
        "/URI",
    }
)

_RENDER_LOCK: Final = threading.Lock()


@dataclass(frozen=True, slots=True, repr=False)
class _FontProfile:
    font_bytes: bytes
    cmap: frozenset[int]
    units_per_em: int

    def __repr__(self) -> str:
        return "_FontProfile(family='Noto Sans', details_hidden=True)"


class _ImmutableFontStream:
    """Minimal read-only seekable view over one authenticated immutable snapshot."""

    __slots__ = ("_closed", "_data", "_position")

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._position = 0
        self._closed = False

    def __str__(self) -> str:
        return "authenticated-overlay-font.ttf"

    @property
    def closed(self) -> bool:
        return self._closed

    def read(self, size: int = -1, /) -> bytes:
        if self._closed:
            raise ValueError from None
        if type(size) is not int:
            raise TypeError from None
        if size < 0:
            size = len(self._data) - self._position
        start = self._position
        end = min(len(self._data), start + size)
        self._position = end
        return self._data[start:end]

    def seek(self, offset: int, whence: int = os.SEEK_SET, /) -> int:
        if self._closed:
            raise ValueError from None
        if type(offset) is not int or type(whence) is not int:
            raise TypeError from None
        if whence == os.SEEK_SET:
            base = 0
        elif whence == os.SEEK_CUR:
            base = self._position
        elif whence == os.SEEK_END:
            base = len(self._data)
        else:
            raise ValueError from None
        position = base + offset
        if position < 0:
            raise ValueError from None
        self._position = position
        return position

    def tell(self) -> int:
        if self._closed:
            raise ValueError from None
        return self._position

    def close(self) -> None:
        self._closed = True

    def uses_snapshot(self, data: bytes) -> bool:
        """Prove identity with the private immutable bytes retained by this stream."""

        return self._data is data


@dataclass(frozen=True, slots=True, repr=False)
class _ShapedTextInk:
    """Conservative bounds for the exact fpdf2/HarfBuzz glyph sequence."""

    advance_points: Decimal
    glyph_count: int
    left_points: Decimal | None
    bottom_points: Decimal | None
    right_points: Decimal | None
    top_points: Decimal | None

    @property
    def has_ink(self) -> bool:
        return self.left_points is not None


@dataclass(frozen=True, slots=True, repr=False)
class _PreparedRaster:
    metadata: OverlayRasterAsset
    png_bytes: bytes

    def __repr__(self) -> str:
        return "_PreparedRaster(format='png-rgba8', details_hidden=True)"


@dataclass(frozen=True, slots=True, repr=False)
class _GeneratedOverlay:
    pdf_bytes: bytes
    placements: tuple[OverlayRenderedPlacement, ...]
    embedded_font_count: int
    # Public result evidence counts signature placements, not deduplicated PDF resources.
    image_count: int


class _OutputLimitExceeded(OSError):
    """Content-free sentinel raised by the in-memory bounded PDF sink."""

    def __init__(self) -> None:
        super().__init__()


class _DestinationCallbackFailed(OSError):
    """Content-free sentinel for an untrusted destination callback failure."""

    def __init__(self) -> None:
        super().__init__()


class _RasterCallbackFailed(OSError):
    """Content-free sentinel for an untrusted raster-source callback failure."""

    def __init__(self) -> None:
        super().__init__()


class _BoundedBytesSink:
    """Seekable BytesIO facade that cannot grow past a trusted extent."""

    __slots__ = ("_extent", "_limit_exceeded", "_maximum_bytes", "_stream")

    def __init__(self, maximum_bytes: int) -> None:
        self._stream = BytesIO()
        self._maximum_bytes = maximum_bytes
        self._extent = 0
        self._limit_exceeded = False

    @property
    def limit_exceeded(self) -> bool:
        return self._limit_exceeded

    def write(self, data: Buffer, /) -> int:
        try:
            count = memoryview(data).nbytes
            position = self._stream.tell()
        except BufferError, TypeError, ValueError:
            raise OSError from None
        self._require_extent(position + count)
        written = self._stream.write(data)
        if type(written) is not int or not 0 <= written <= count:
            raise OSError from None
        self._extent = max(self._extent, position + written)
        return written

    def seek(self, offset: int, whence: int = os.SEEK_SET, /) -> int:
        if type(offset) is not int or type(whence) is not int:
            raise OSError from None
        if whence == os.SEEK_SET:
            target = offset
        elif whence == os.SEEK_CUR:
            target = self.tell() + offset
        elif whence == os.SEEK_END:
            target = self._extent + offset
        else:
            raise OSError from None
        if target < 0:
            raise OSError from None
        self._require_extent(target)
        result = self._stream.seek(offset, whence)
        if type(result) is not int or result != target:
            raise OSError from None
        return result

    def tell(self) -> int:
        result = self._stream.tell()
        if type(result) is not int or not 0 <= result <= self._maximum_bytes:
            raise OSError from None
        return result

    def truncate(self, size: int | None = None, /) -> int:
        target = self.tell() if size is None else size
        if type(target) is not int or target < 0:
            raise OSError from None
        self._require_extent(target)
        result = self._stream.truncate(target)
        if type(result) is not int or result != target:
            raise OSError from None
        self._extent = target
        return result

    def flush(self) -> None:
        self._stream.flush()

    def readable(self) -> bool:
        return False

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def getvalue(self) -> bytes:
        return self._stream.getvalue()

    def _require_extent(self, extent: int) -> None:
        if extent <= self._maximum_bytes:
            return
        self._limit_exceeded = True
        raise _OutputLimitExceeded from None


@dataclass(frozen=True, slots=True)
class FpdfOverlayRenderer:
    """Generate one transparent, sanitized, self-contained vector overlay page."""

    def render(
        self,
        request: OverlayPageRenderRequest,
        raster_sources: Mapping[UUID, OverlayRasterSource],
        destination: OverlayPdfDestination,
        limits: OverlayRenderLimits,
        control: OverlayRenderControl,
    ) -> OverlayPageRenderResult:
        """Render only after validating exact engines, font, assets, and output structure."""

        raster_snapshot = validate_overlay_render_request(request, raster_sources, limits)
        _validate_empty_destination(destination)
        require_overlay_render_active(control)
        _require_approved_runtime()
        require_overlay_render_active(control)

        try:
            font_profile = _load_font_profile(request, limits, control)
            prepared_rasters = _prepare_rasters(raster_snapshot, limits, control)
            require_overlay_render_active(control)
            with _serialized_renderer(control):
                generated = _generate_overlay(
                    request,
                    prepared_rasters,
                    font_profile,
                    limits,
                    control,
                )
            digest = _write_exact_pdf(
                destination,
                generated.pdf_bytes,
                limits,
                control,
            )
            try:
                return OverlayPageRenderResult(
                    page_id=request.page_id,
                    canvas_width_points=request.geometry.canvas_width_points,
                    canvas_height_points=request.geometry.canvas_height_points,
                    pdf_placement=request.geometry.pdf_placement,
                    placements=generated.placements,
                    bytes_written=len(generated.pdf_bytes),
                    pdf_sha256=digest,
                    embedded_font_count=generated.embedded_font_count,
                    image_count=generated.image_count,
                )
            except BaseException:
                _discard_destination(destination)
                raise
        except OverlayRenderError:
            raise
        except MemoryError, RecursionError:
            reject_overlay_rendering(OverlayRenderErrorCode.LIMIT_EXCEEDED, "overlay.memory")
        except KeyboardInterrupt, SystemExit, GeneratorExit:
            raise
        except Exception:
            reject_overlay_rendering(OverlayRenderErrorCode.INTERNAL_ERROR, "overlay.adapter")


def _validate_empty_destination(destination: OverlayPdfDestination) -> None:
    try:
        write = destination.write
        flush = destination.flush
        seek = destination.seek
        tell = destination.tell
        truncate = destination.truncate
    except Exception:
        reject_overlay_rendering(OverlayRenderErrorCode.MODEL_INVALID, "overlay.destination")
    if not all(callable(value) for value in (write, flush, seek, tell, truncate)):
        reject_overlay_rendering(OverlayRenderErrorCode.MODEL_INVALID, "overlay.destination")
    try:
        original = destination.tell()
        extent = destination.seek(0, os.SEEK_END)
        restored = destination.seek(original, os.SEEK_SET)
    except Exception:
        reject_overlay_rendering(
            OverlayRenderErrorCode.DESTINATION_FAILED,
            "overlay.destination_state",
        )
    if (
        type(original) is not int
        or type(extent) is not int
        or type(restored) is not int
        or original != 0
        or extent != 0
        or restored != 0
    ):
        reject_overlay_rendering(
            OverlayRenderErrorCode.DESTINATION_FAILED,
            "overlay.destination_state",
        )


def _require_approved_runtime() -> None:
    """Bind production behavior to the exact locked renderer closure."""

    try:
        versions = (
            fpdf.__version__,
            fontTools.__version__,
            uharfbuzz.__version__,
            importlib_metadata.version("pillow"),
            pikepdf.__version__,
            pikepdf.__libqpdf_version__,
            importlib_metadata.version(OVERLAY_FONT_RESOURCE_DISTRIBUTION),
            zlib.ZLIB_VERSION,
            zlib.ZLIB_RUNTIME_VERSION,
            zlib.ZLIBNG_VERSION,
        )
    except Exception:
        reject_overlay_rendering(
            OverlayRenderErrorCode.DEPENDENCY_UNAVAILABLE,
            "overlay.runtime",
        )
    if versions != (
        EXPECTED_FPDF2_VERSION,
        EXPECTED_FONTTOOLS_VERSION,
        EXPECTED_UHARFBUZZ_VERSION,
        EXPECTED_PILLOW_VERSION,
        EXPECTED_PIKEPDF_VERSION,
        EXPECTED_QPDF_VERSION,
        EXPECTED_OCRMYPDF_VERSION,
        EXPECTED_ZLIB_VERSION,
        EXPECTED_ZLIB_RUNTIME_VERSION,
        EXPECTED_ZLIBNG_VERSION,
    ):
        reject_overlay_rendering(OverlayRenderErrorCode.DEPENDENCY_UNSAFE, "overlay.runtime")


def _load_font_profile(
    request: OverlayPageRenderRequest,
    limits: OverlayRenderLimits,
    control: OverlayRenderControl,
) -> _FontProfile | None:
    text_payloads = tuple(
        payload
        for payload in (projection.record.payload for projection in request.projections)
        if type(payload) is TextualOverlayPayload
    )
    if any(payload.font_token != DEFAULT_FONT_TOKEN for payload in text_payloads):
        reject_overlay_rendering(OverlayRenderErrorCode.FONT_UNAVAILABLE, "overlay.font_token")
    nonempty = tuple(payload for payload in text_payloads if payload.value)
    if not nonempty:
        return None
    require_overlay_render_active(control)
    font_bytes = _load_authenticated_font_bytes(control, limits)
    try:
        font = TTFont(
            _ImmutableFontStream(font_bytes),
            lazy=False,
            checkChecksums=2,
            recalcBBoxes=False,
            recalcTimestamp=False,
        )
    except MemoryError:
        reject_overlay_rendering(OverlayRenderErrorCode.LIMIT_EXCEEDED, "overlay.font_memory")
    except TTLibError, OSError, KeyError, ValueError:
        reject_overlay_rendering(OverlayRenderErrorCode.FONT_UNSAFE, "overlay.font_parse")
    try:
        cmap = font.getBestCmap()
        name = font["name"]
        head = font["head"]
        os2 = font["OS/2"]
        if (
            not cmap
            or name.getBestFamilyName() != OVERLAY_FONT_FAMILY
            or name.getBestFullName() != OVERLAY_FONT_FULL_NAME
            or name.getDebugName(5) != OVERLAY_FONT_VERSION
            or type(head.unitsPerEm) is not int
            or head.unitsPerEm < 16
            or os2.fsType != 0
        ):
            reject_overlay_rendering(OverlayRenderErrorCode.FONT_UNSAFE, "overlay.font_profile")
        selected = _FontProfile(
            font_bytes=font_bytes,
            cmap=frozenset(cmap),
            units_per_em=head.unitsPerEm,
        )
    except OverlayRenderError:
        raise
    except Exception:
        reject_overlay_rendering(OverlayRenderErrorCode.FONT_UNSAFE, "overlay.font_profile")
    finally:
        try:
            font.close()
        except Exception:
            pass
    for payload in nonempty:
        _preflight_text(payload.value, selected)
    return selected


def _load_authenticated_font_bytes(
    control: OverlayRenderControl,
    limits: OverlayRenderLimits,
) -> bytes:
    if OVERLAY_FONT_RESOURCE_SIZE > limits.maximum_memory_bytes:
        reject_overlay_rendering(OverlayRenderErrorCode.LIMIT_EXCEEDED, "overlay.font_memory")
    try:
        distribution = importlib_metadata.distribution(OVERLAY_FONT_RESOURCE_DISTRIBUTION)
        located_root = Path(os.fspath(cast(os.PathLike[str], distribution.locate_file(""))))
        located_path = Path(
            os.fspath(
                cast(
                    os.PathLike[str],
                    distribution.locate_file(OVERLAY_FONT_RESOURCE_PATH),
                )
            )
        )
        _reject_symlinked_font_resource(located_root, located_path)
        root = located_root.resolve(strict=True)
        path = located_path.resolve(strict=True)
        _reject_symlinked_font_resource(located_root, located_path)
        if not path.is_relative_to(root):
            raise OSError
        before = path.stat()
        if not path.is_file() or before.st_size != OVERLAY_FONT_RESOURCE_SIZE:
            raise OSError
        digest = hashlib.sha256()
        data = bytearray()
        total = 0
        with path.open("rb") as stream:
            while True:
                require_overlay_render_active(control)
                requested = min(
                    limits.io_chunk_bytes,
                    OVERLAY_FONT_RESOURCE_SIZE + 1 - total,
                )
                chunk = stream.read(requested)
                if type(chunk) is not bytes or len(chunk) > requested:
                    raise OSError
                if not chunk:
                    break
                total += len(chunk)
                if total > OVERLAY_FONT_RESOURCE_SIZE:
                    raise OSError
                data.extend(chunk)
                digest.update(chunk)
            descriptor = os.fstat(stream.fileno())
        after = path.stat()
    except OverlayRenderError:
        raise
    except Exception:
        reject_overlay_rendering(OverlayRenderErrorCode.FONT_UNAVAILABLE, "overlay.font_resource")
    if (
        total != OVERLAY_FONT_RESOURCE_SIZE
        or not hmac.compare_digest(digest.hexdigest(), OVERLAY_FONT_RESOURCE_SHA256)
        or descriptor.st_size != before.st_size
        or after.st_size != before.st_size
        or descriptor.st_mtime_ns != before.st_mtime_ns
        or after.st_mtime_ns != before.st_mtime_ns
    ):
        reject_overlay_rendering(OverlayRenderErrorCode.FONT_UNSAFE, "overlay.font_identity")
    try:
        return bytes(data)
    except MemoryError:
        reject_overlay_rendering(OverlayRenderErrorCode.LIMIT_EXCEEDED, "overlay.font_memory")


def _reject_symlinked_font_resource(located_root: Path, located_path: Path) -> None:
    """Reject links below the distribution root without first following them."""

    try:
        relative = located_path.relative_to(located_root)
    except ValueError as error:
        raise OSError from error
    if not relative.parts or any(part == os.pardir for part in relative.parts):
        raise OSError
    candidate = located_root
    for part in relative.parts:
        candidate /= part
        if candidate.is_symlink():
            raise OSError


def _preflight_text(value: str, profile: _FontProfile) -> None:
    """Reject before fpdf2 can log a user character or emit a ``.notdef`` glyph."""

    for character in value:
        codepoint = ord(character)
        category = unicodedata.category(character)
        if category.startswith("C"):
            reject_overlay_rendering(OverlayRenderErrorCode.TEXT_UNSUPPORTED, "overlay.text")
        if codepoint not in profile.cmap:
            reject_overlay_rendering(OverlayRenderErrorCode.GLYPH_UNSUPPORTED, "overlay.glyph")
        if unicodedata.bidirectional(character) in {"R", "AL", "AN"}:
            reject_overlay_rendering(OverlayRenderErrorCode.TEXT_UNSUPPORTED, "overlay.direction")


def _prepare_rasters(
    snapshots: tuple[OverlayRasterSourceSnapshot, ...],
    limits: OverlayRenderLimits,
    control: OverlayRenderControl,
) -> dict[UUID, _PreparedRaster]:
    prepared: dict[UUID, _PreparedRaster] = {}
    memory_bytes = 0
    for snapshot in snapshots:
        require_overlay_render_active(control)
        asset_id = snapshot.asset_id
        source = snapshot.source
        metadata = snapshot.metadata
        rgba = _read_exact_raster(source, metadata, limits, control)
        png = _encode_rgba_png(metadata, rgba, limits, control)
        memory_bytes += len(rgba) + len(png)
        if memory_bytes > limits.maximum_memory_bytes:
            reject_overlay_rendering(OverlayRenderErrorCode.LIMIT_EXCEEDED, "overlay.raster_memory")
        prepared[asset_id] = _PreparedRaster(metadata, png)
    return prepared


def _read_exact_raster(
    source: OverlayRasterSource,
    metadata: OverlayRasterAsset,
    limits: OverlayRenderLimits,
    control: OverlayRenderControl,
) -> bytes:
    try:
        original = _raster_callback(source, "tell")
    except Exception:
        reject_overlay_rendering(OverlayRenderErrorCode.ASSET_CHANGED, "overlay.raster_position")
    if type(original) is not int or original < 0:
        reject_overlay_rendering(OverlayRenderErrorCode.ASSET_CHANGED, "overlay.raster_position")
    data = bytearray()
    digest = hashlib.sha256()
    operation_error: BaseException | None = None
    try:
        if _raster_callback(source, "seek", 0) != 0:
            raise OSError
        while len(data) < metadata.byte_count:
            require_overlay_render_active(control)
            requested = min(limits.io_chunk_bytes, metadata.byte_count - len(data))
            block = _raster_callback(source, "read", requested)
            if type(block) is not bytes or not 1 <= len(block) <= requested:
                raise OSError
            data.extend(block)
            digest.update(block)
        extra = _raster_callback(source, "read", 1)
        if type(extra) is not bytes or extra:
            raise OSError
    except BaseException as error:
        operation_error = error
    try:
        restored = _raster_callback(source, "seek", original)
    except Exception:
        restored = None
    if type(restored) is not int or restored != original:
        reject_overlay_rendering(OverlayRenderErrorCode.ASSET_CHANGED, "overlay.raster_restore")
    if operation_error is not None:
        if isinstance(operation_error, OverlayRenderError):
            raise operation_error
        if isinstance(operation_error, (MemoryError, RecursionError)):
            raise operation_error
        if isinstance(operation_error, (KeyboardInterrupt, SystemExit, GeneratorExit)):
            raise operation_error
        reject_overlay_rendering(OverlayRenderErrorCode.ASSET_CHANGED, "overlay.raster_read")
    if len(data) != metadata.byte_count or not hmac.compare_digest(
        digest.hexdigest(), metadata.pixel_sha256
    ):
        reject_overlay_rendering(OverlayRenderErrorCode.ASSET_CHANGED, "overlay.raster_identity")
    return bytes(data)


def _raster_callback(source: object, method_name: str, *args: object) -> object:
    try:
        callback = getattr(source, method_name)
        if not callable(callback):
            raise TypeError
        return callback(*args)
    except MemoryError, RecursionError:
        raise
    except KeyboardInterrupt, SystemExit, GeneratorExit:
        raise
    except Exception:
        raise _RasterCallbackFailed from None


def _encode_rgba_png(
    metadata: OverlayRasterAsset,
    rgba: bytes,
    limits: OverlayRenderLimits,
    control: OverlayRenderControl,
) -> bytes:
    if len(rgba) != metadata.byte_count:
        reject_overlay_rendering(OverlayRenderErrorCode.ASSET_CHANGED, "overlay.raster_length")
    compressor = zlib.compressobj(level=9, method=zlib.DEFLATED, wbits=zlib.MAX_WBITS)
    compressed = bytearray()
    for start in range(0, metadata.byte_count, metadata.row_stride_bytes):
        require_overlay_render_active(control)
        compressed.extend(
            compressor.compress(b"\x00" + rgba[start : start + metadata.row_stride_bytes])
        )
        if len(compressed) > limits.maximum_output_bytes:
            reject_overlay_rendering(OverlayRenderErrorCode.LIMIT_EXCEEDED, "overlay.png_bytes")
    compressed.extend(compressor.flush())
    header = struct.pack(
        ">IIBBBBB",
        metadata.width_pixels,
        metadata.height_pixels,
        _PNG_BIT_DEPTH,
        _PNG_COLOR_TYPE_RGBA,
        0,
        0,
        0,
    )
    png = (
        _PNG_SIGNATURE
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", bytes(compressed))
        + _png_chunk(b"IEND", b"")
    )
    if len(png) > limits.maximum_output_bytes:
        reject_overlay_rendering(OverlayRenderErrorCode.LIMIT_EXCEEDED, "overlay.png_bytes")
    return png


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    payload = kind + data
    return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload))


@contextmanager
def _serialized_renderer(control: OverlayRenderControl) -> Iterator[None]:
    acquired = False
    try:
        while not acquired:
            require_overlay_render_active(control)
            acquired = _RENDER_LOCK.acquire(timeout=_LOCK_POLL_SECONDS)
        require_overlay_render_active(control)
        yield
    finally:
        if acquired:
            _RENDER_LOCK.release()


def _generate_overlay(
    request: OverlayPageRenderRequest,
    rasters: Mapping[UUID, _PreparedRaster],
    font_profile: _FontProfile | None,
    limits: OverlayRenderLimits,
    control: OverlayRenderControl,
) -> _GeneratedOverlay:
    try:
        document = FPDF(
            unit="pt",
            format=(
                request.geometry.canvas_width_points,
                request.geometry.canvas_height_points,
            ),
        )
        document.set_creation_date(_FIXED_CREATION_DATE)
        document.set_producer(_FIXED_PRODUCER)
        document.set_margins(0, 0, 0)
        document.c_margin = 0.0
        document.set_auto_page_break(auto=False, margin=0)
        document.add_page()
        if font_profile is not None:
            _add_authenticated_fpdf_font(document, font_profile, control)
        placements: list[OverlayRenderedPlacement] = []
        signature_placement_count = 0
        for projection in request.projections:
            require_overlay_render_active(control)
            record = projection.record
            requested_box = request.geometry.box_for(record.box)
            visible_box = projection.visible_box
            if visible_box is None:
                reject_overlay_rendering(OverlayRenderErrorCode.MODEL_INVALID, "overlay.visibility")
            clip_box = request.geometry.box_for(visible_box)
            placement = _draw_record(
                document,
                record,
                requested_box,
                clip_box,
                rasters,
                font_profile,
            )
            placements.append(placement)
            if record.kind is OverlayKind.SIGNATURE_IMAGE:
                signature_placement_count += 1
        require_overlay_render_active(control)
        raw = _output_with_private_warnings_suppressed(document)
        require_overlay_render_active(control)
        if len(raw) > limits.maximum_output_bytes:
            reject_overlay_rendering(OverlayRenderErrorCode.LIMIT_EXCEEDED, "overlay.output_bytes")
        if font_profile is not None:
            for font in document.fonts.values():
                missing = getattr(font, "missing_glyphs", ())
                if missing:
                    reject_overlay_rendering(
                        OverlayRenderErrorCode.GLYPH_UNSUPPORTED, "overlay.glyph"
                    )
        expected_fonts = (
            1
            if any(
                type(projection.record.payload) is TextualOverlayPayload
                and bool(projection.record.payload.value)
                for projection in request.projections
            )
            else 0
        )
        expected_image_resources = len({raster.png_bytes for raster in rasters.values()})
        sanitized = _sanitize_and_verify(
            raw,
            width=request.geometry.canvas_width_points,
            height=request.geometry.canvas_height_points,
            expected_fonts=expected_fonts,
            expected_image_resources=expected_image_resources,
            limits=limits,
            control=control,
        )
        return _GeneratedOverlay(
            pdf_bytes=sanitized,
            placements=tuple(placements),
            embedded_font_count=expected_fonts,
            image_count=signature_placement_count,
        )
    except OverlayRenderError:
        raise
    except MemoryError, RecursionError:
        reject_overlay_rendering(OverlayRenderErrorCode.LIMIT_EXCEEDED, "overlay.generate_memory")
    except FPDFException, TTLibError, OSError, TypeError, ValueError:
        reject_overlay_rendering(OverlayRenderErrorCode.DEPENDENCY_FAILED, "overlay.fpdf")


def _add_authenticated_fpdf_font(
    document: FPDF,
    profile: _FontProfile,
    control: OverlayRenderControl,
) -> None:
    """Bind fpdf2/fontTools/HarfBuzz to one private immutable font snapshot."""

    require_overlay_render_active(control)
    font_key = OVERLAY_FONT_FPDF_FAMILY.casefold()
    stream = _ImmutableFontStream(profile.font_bytes)
    try:
        loaded_font = cast(Any, TTFFont)(
            document,
            cast(Any, stream),
            font_key,
            "",
            None,
            None,
            None,
            0,
        )
        blob = uharfbuzz.Blob(profile.font_bytes)
        loaded_font._hbfont = HarfBuzzFont(uharfbuzz.Face(blob, 0))
        _authenticate_loaded_fpdf_font(loaded_font, stream, profile, control)
        document.fonts[font_key] = loaded_font
    except OverlayRenderError:
        raise
    except MemoryError, RecursionError:
        reject_overlay_rendering(OverlayRenderErrorCode.LIMIT_EXCEEDED, "overlay.font_memory")
    except Exception:
        reject_overlay_rendering(OverlayRenderErrorCode.FONT_UNSAFE, "overlay.font_loaded")


def _authenticate_loaded_fpdf_font(
    loaded_font: object,
    stream: _ImmutableFontStream,
    profile: _FontProfile,
    control: OverlayRenderControl,
) -> None:
    """Verify that every retained font engine input is the authenticated snapshot."""

    try:
        require_overlay_render_active(control)
        font = cast(Any, loaded_font)
        ttfont = font.ttfont
        reader = ttfont.reader
        if (
            font.ttffile is not stream
            or reader.file is not stream
            or not stream.uses_snapshot(profile.font_bytes)
            or font.is_compressed is not False
            or font.collection_font_number != 0
            or frozenset(font.cmap) != profile.cmap
            or ttfont["head"].unitsPerEm != profile.units_per_em
        ):
            raise OSError
        hb_bytes = font.hbfont.face.blob.data
        if type(hb_bytes) is not bytes:
            raise OSError
    except OverlayRenderError:
        raise
    except MemoryError, RecursionError:
        reject_overlay_rendering(OverlayRenderErrorCode.LIMIT_EXCEEDED, "overlay.font_memory")
    except Exception:
        reject_overlay_rendering(OverlayRenderErrorCode.FONT_UNSAFE, "overlay.font_loaded")
    if len(hb_bytes) != OVERLAY_FONT_RESOURCE_SIZE or not hmac.compare_digest(
        hashlib.sha256(hb_bytes).hexdigest(), OVERLAY_FONT_RESOURCE_SHA256
    ):
        reject_overlay_rendering(OverlayRenderErrorCode.FONT_UNSAFE, "overlay.font_loaded")


def _output_with_private_warnings_suppressed(document: FPDF) -> bytes:
    with _private_output_logging_suppressed():
        output = document.output()
    if type(output) is not bytearray:
        reject_overlay_rendering(OverlayRenderErrorCode.DEPENDENCY_FAILED, "overlay.fpdf_output")
    return bytes(output)


@contextmanager
def _private_output_logging_suppressed() -> Iterator[None]:
    """Contain content-derived fpdf2/fontTools records during the locked output step."""

    roots = tuple(logging.getLogger(name) for name in _PRIVATE_OUTPUT_LOGGER_NAMESPACES)
    selected = set(roots)
    for name, value in tuple(logging.Logger.manager.loggerDict.items()):
        if isinstance(value, logging.Logger) and any(
            name == namespace or name.startswith(f"{namespace}.")
            for namespace in _PRIVATE_OUTPUT_LOGGER_NAMESPACES
        ):
            selected.add(value)
    loggers = tuple(sorted(selected, key=lambda item: item.name))
    states = tuple(
        (
            logger,
            logger.disabled,
            logger.level,
            logger.propagate,
            tuple(logger.handlers),
        )
        for logger in loggers
    )
    try:
        for logger in loggers:
            logger.disabled = True
        for logger in roots:
            logger.setLevel(logging.CRITICAL + 1)
            logger.propagate = False
            logger.handlers.clear()
        yield
    finally:
        for logger, disabled, level, propagate, handlers in states:
            logger.handlers[:] = handlers
            logger.propagate = propagate
            logger.setLevel(level)
            logger.disabled = disabled


def _draw_record(
    document: FPDF,
    record: OverlayRecord,
    box: OverlayCanvasBox,
    clip: OverlayCanvasBox,
    rasters: Mapping[UUID, _PreparedRaster],
    font_profile: _FontProfile | None,
) -> OverlayRenderedPlacement:
    payload = record.payload
    painted: OverlayCanvasBox | None
    metrics: OverlayTextMetrics | None = None
    # rect_clip() saves/restores the PDF graphics state with q/Q, but it does
    # not save fpdf2's matching style cache.  Keep a public fpdf2 graphics-state
    # context around every clipped record so a following identical style is
    # emitted again after the clip restores the underlying PDF state.
    with document.local_context():
        with document.rect_clip(clip.left, clip.top, clip.width, clip.height):
            if type(payload) is TextualOverlayPayload:
                painted, metrics = _draw_text(document, payload, box, font_profile)
            elif type(payload) is MarkOverlayPayload:
                painted = _draw_mark(document, payload, box)
            elif type(payload) is SignatureImageOverlayPayload:
                raster = rasters.get(payload.asset_id)
                if raster is None:
                    reject_overlay_rendering(OverlayRenderErrorCode.ASSET_MISSING, "overlay.raster")
                painted = _draw_signature_image(document, raster, box)
            elif type(payload) is ShapeOverlayPayload:
                painted = _draw_shape(document, payload, box)
            else:
                reject_overlay_rendering(OverlayRenderErrorCode.MODEL_INVALID, "overlay.payload")
    return OverlayRenderedPlacement(
        overlay_id=record.overlay_id,
        kind=record.kind,
        z_index=record.z_index,
        requested_box=box,
        clip_box=clip,
        painted_box=painted,
        text_metrics=metrics,
    )


def _draw_text(
    document: FPDF,
    payload: TextualOverlayPayload,
    box: OverlayCanvasBox,
    font_profile: _FontProfile | None,
) -> tuple[OverlayCanvasBox | None, OverlayTextMetrics]:
    if not payload.value:
        return None, OverlayTextMetrics(
            font_size_points=payload.font_size_points,
            advance_width_points=0.0,
            ascent_points=0.0,
            descent_points=0.0,
            baseline_y_points=box.top,
            glyph_count=0,
        )
    if font_profile is None:
        reject_overlay_rendering(OverlayRenderErrorCode.FONT_UNAVAILABLE, "overlay.font")
    document.set_font(OVERLAY_FONT_FPDF_FAMILY, size=payload.font_size_points)
    document.set_text_shaping(True, direction="ltr")
    shaped = _shape_text_ink(document, payload, font_profile)
    box_left = Decimal(str(box.left))
    box_top = Decimal(str(box.top))
    box_right = Decimal(str(box.right))
    box_bottom = Decimal(str(box.bottom))
    origin_x = box_left
    baseline = (box_top + box_bottom) / 2
    painted: OverlayCanvasBox | None = None
    ascent = Decimal(0)
    descent = Decimal(0)
    if shaped.has_ink:
        ink_left = cast(Decimal, shaped.left_points)
        ink_bottom = cast(Decimal, shaped.bottom_points)
        ink_right = cast(Decimal, shaped.right_points)
        ink_top = cast(Decimal, shaped.top_points)
        if ink_right - ink_left > box_right - box_left or ink_top - ink_bottom > (
            box_bottom - box_top
        ):
            reject_overlay_rendering(OverlayRenderErrorCode.TEXT_DOES_NOT_FIT, "overlay.text_fit")

        # Preserve the logical left origin whenever possible.  If a glyph's
        # side bearing extends beyond either edge, project that origin onto the
        # exact interval that keeps every shaped outline inside the box.
        origin_x = _point_on_emitted_grid(
            preferred=box_left,
            lower=box_left - ink_left,
            upper=box_right - ink_right,
        )
        desired_baseline = box_top + ((box_bottom - box_top) - (ink_top - ink_bottom)) / 2 + ink_top
        baseline = _point_on_emitted_grid(
            preferred=desired_baseline,
            lower=box_top + ink_top,
            upper=box_bottom + ink_bottom,
        )
        actual_left = origin_x + ink_left
        actual_top = baseline - ink_top
        actual_right = origin_x + ink_right
        actual_bottom = baseline - ink_bottom
        painted = OverlayCanvasBox(
            float(actual_left.quantize(OVERLAY_RENDER_POINT_QUANTUM, rounding=ROUND_FLOOR)),
            float(actual_top.quantize(OVERLAY_RENDER_POINT_QUANTUM, rounding=ROUND_FLOOR)),
            float(actual_right.quantize(OVERLAY_RENDER_POINT_QUANTUM, rounding=ROUND_CEILING)),
            float(actual_bottom.quantize(OVERLAY_RENDER_POINT_QUANTUM, rounding=ROUND_CEILING)),
        )
        ascent = max(Decimal(0), ink_top)
        descent = max(Decimal(0), -ink_bottom)
    else:
        baseline = baseline.quantize(OVERLAY_RENDER_POINT_QUANTUM, rounding=ROUND_HALF_UP)

    color = _rgba(payload.color)
    document.set_text_color(*color[:3])
    with document.local_context(fill_opacity=color[3]):
        _emit_whole_shaped_line(document, payload, origin_x, baseline)
    return painted, OverlayTextMetrics(
        font_size_points=payload.font_size_points,
        advance_width_points=float(
            shaped.advance_points.quantize(OVERLAY_RENDER_POINT_QUANTUM, rounding=ROUND_HALF_UP)
        ),
        ascent_points=float(ascent.quantize(OVERLAY_RENDER_POINT_QUANTUM, rounding=ROUND_HALF_UP)),
        descent_points=float(
            descent.quantize(OVERLAY_RENDER_POINT_QUANTUM, rounding=ROUND_HALF_UP)
        ),
        baseline_y_points=float(baseline),
        glyph_count=shaped.glyph_count,
    )


def _whole_line_shaping_parameters(document: FPDF) -> dict[str, Any]:
    shaping = document.text_shaping
    if not isinstance(shaping, dict) or shaping.get("direction") is None:
        raise ValueError
    shaping_params = dict(shaping)
    # fpdf2 normally assigns this after bidi/script fragmentation.  The
    # renderer already rejects RTL input, so shape one unfragmented line and
    # preserve combining clusters under the same forced LTR direction.
    shaping_params["fragment_direction"] = shaping_params["direction"]
    shaping_params["paragraph_direction"] = shaping_params["direction"]
    return shaping_params


def _emit_whole_shaped_line(
    document: FPDF,
    payload: TextualOverlayPayload,
    origin_x: Decimal,
    baseline: Decimal,
) -> None:
    """Emit one already-approved LTR line with fpdf2's shaped-text routine."""

    try:
        shaping_params = _whole_line_shaping_parameters(document)
        graphics_state = document._get_current_graphics_state()
        graphics_state.text_shaping = shaping_params
        fragment = Fragment(payload.value, graphics_state, document.k)
        # shape_text() mutates only the bounded font subset, but native
        # diagnostics could otherwise expose the line.  Contain them around
        # both shaping and command generation.
        with _private_output_logging_suppressed():
            rendered = fragment.render_with_text_shaping(
                float(origin_x),
                float(baseline),
                document.h,
                0.0,
            )
        if type(rendered) is not str or not rendered:
            raise ValueError
        font = cast(Any, document.current_font)
        document._out(_actual_text_marker(payload.value))
        if not document.current_font_is_set_on_page:
            document._out(
                document._set_font_for_page(
                    font,
                    payload.font_size_points,
                )
            )
        text_color = document.text_color
        if text_color is None:
            raise ValueError
        if text_color != document.fill_color:
            document._out(text_color.serialize().lower())
        document._out(
            f"BT {float(origin_x):.2f} {document.h - float(baseline):.2f} Td {rendered} ET"
        )
        document._out("EMC")
    except OverlayRenderError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception:
        reject_overlay_rendering(OverlayRenderErrorCode.DEPENDENCY_FAILED, "overlay.text_shape")


def _shape_text_ink(
    document: FPDF,
    payload: TextualOverlayPayload,
    font_profile: _FontProfile,
) -> _ShapedTextInk:
    """Measure the exact glyph sequence that fpdf2 will send to HarfBuzz."""

    font = cast(Any, document.current_font)
    try:
        shaping_params = _whole_line_shaping_parameters(document)
        glyph_infos, glyph_positions = font.perform_harfbuzz_shaping(
            payload.value,
            payload.font_size_points,
            shaping_params,
        )
        hb_font = font.hbfont
        if hb_font.scale != (font_profile.units_per_em, font_profile.units_per_em):
            raise ValueError
        if not glyph_infos or len(glyph_infos) != len(glyph_positions):
            reject_overlay_rendering(OverlayRenderErrorCode.GLYPH_UNSUPPORTED, "overlay.glyph")

        scale = Decimal(payload.font_size_points) / font_profile.units_per_em
        pen_x = 0
        pen_y = 0
        left: Decimal | None = None
        bottom: Decimal | None = None
        right: Decimal | None = None
        top: Decimal | None = None
        position_uses_rounded_matrix = False
        for info, position in zip(glyph_infos, glyph_positions, strict=True):
            values = (
                info.codepoint,
                info.cluster,
                position.x_advance,
                position.y_advance,
                position.x_offset,
                position.y_offset,
            )
            if any(type(value) is not int for value in values):
                raise ValueError
            glyph_id = info.codepoint
            glyph_name = font.ttfont.getGlyphName(glyph_id)
            if (
                glyph_id <= 0
                or info.cluster < 0
                or info.cluster >= len(payload.value)
                or glyph_name == ".notdef"
            ):
                reject_overlay_rendering(OverlayRenderErrorCode.GLYPH_UNSUPPORTED, "overlay.glyph")
            nominal_width = round(font.scale * font.ttfont["hmtx"].metrics[glyph_name][0])
            if type(nominal_width) is not int:
                raise ValueError
            has_offset = position.x_offset != 0 or position.y_offset != 0
            glyph_uses_rounded_matrix = has_offset or position_uses_rounded_matrix
            extents = hb_font.get_glyph_extents(glyph_id)
            if extents is not None:
                extent_values = (
                    extents.x_bearing,
                    extents.y_bearing,
                    extents.width,
                    extents.height,
                )
                if any(type(value) is not int for value in extent_values):
                    raise ValueError
                glyph_x_1 = pen_x + position.x_offset + extents.x_bearing
                glyph_x_2 = glyph_x_1 + extents.width
                # fpdf2 stores pos_y in top-origin canvas coordinates and
                # increments it by HarfBuzz y_advance. Convert that pen motion
                # back to HarfBuzz/PDF y-up before adding the glyph offset.
                glyph_y_1 = -pen_y + position.y_offset + extents.y_bearing
                glyph_y_2 = glyph_y_1 + extents.height
                glyph_left = Decimal(min(glyph_x_1, glyph_x_2)) * scale
                glyph_right = Decimal(max(glyph_x_1, glyph_x_2)) * scale
                glyph_bottom = Decimal(min(glyph_y_1, glyph_y_2)) * scale
                glyph_top = Decimal(max(glyph_y_1, glyph_y_2)) * scale
                if glyph_left < glyph_right and glyph_bottom < glyph_top:
                    if glyph_uses_rounded_matrix:
                        # fpdf2 emits an absolute two-decimal Tm before every
                        # offset glyph and after every glyph whose shaped
                        # advance differs from its nominal width. That reset
                        # replaces, rather than accumulates, an earlier error.
                        # Enclose its unknown rounding direction by one half
                        # output quantum on both axes. PDF y is opposite the
                        # canvas y direction, so the symmetric y envelope is
                        # applied before the later baseline-to-canvas flip.
                        glyph_left -= _EMITTED_POSITION_HALF_QUANTUM
                        glyph_right += _EMITTED_POSITION_HALF_QUANTUM
                        glyph_bottom -= _EMITTED_POSITION_HALF_QUANTUM
                        glyph_top += _EMITTED_POSITION_HALF_QUANTUM
                    left = glyph_left if left is None else min(left, glyph_left)
                    bottom = glyph_bottom if bottom is None else min(bottom, glyph_bottom)
                    right = glyph_right if right is None else max(right, glyph_right)
                    top = glyph_top if top is None else max(top, glyph_top)
            pen_x += position.x_advance
            pen_y += position.y_advance
            if nominal_width != position.x_advance or has_offset or position.y_advance != 0:
                position_uses_rounded_matrix = True
        if pen_x < 0:
            raise ValueError
        return _ShapedTextInk(
            advance_points=Decimal(pen_x) * scale,
            glyph_count=len(glyph_infos),
            left_points=left,
            bottom_points=bottom,
            right_points=right,
            top_points=top,
        )
    except OverlayRenderError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception:
        reject_overlay_rendering(OverlayRenderErrorCode.DEPENDENCY_FAILED, "overlay.text_shape")


def _point_on_emitted_grid(*, preferred: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    """Select the nearest two-decimal PDF coordinate inside a closed interval."""

    minimum = lower.quantize(OVERLAY_RENDER_POINT_QUANTUM, rounding=ROUND_CEILING)
    maximum = upper.quantize(OVERLAY_RENDER_POINT_QUANTUM, rounding=ROUND_FLOOR)
    if minimum > maximum:
        reject_overlay_rendering(OverlayRenderErrorCode.TEXT_DOES_NOT_FIT, "overlay.text_fit")
    emitted_preference = preferred.quantize(
        OVERLAY_RENDER_POINT_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    return min(max(emitted_preference, minimum), maximum)


def _draw_mark(
    document: FPDF, payload: MarkOverlayPayload, box: OverlayCanvasBox
) -> OverlayCanvasBox:
    color = _rgba(payload.color)
    stroke = _quantize_point(
        min(
            _MAXIMUM_MARK_STROKE_POINTS,
            max(_MINIMUM_MARK_STROKE_POINTS, min(box.width, box.height) / _MARK_STROKE_DIVISOR),
        )
    )
    inset = stroke / 2
    left = box.left + inset
    top = box.top + inset
    right = box.right - inset
    bottom = box.bottom - inset
    document.set_draw_color(*color[:3])
    document.set_line_width(stroke)
    with document.local_context(stroke_opacity=color[3]):
        if payload.kind is OverlayKind.CHECKMARK:
            document.line(
                _lerp(left, right, 0.12),
                _lerp(top, bottom, 0.55),
                _lerp(left, right, 0.40),
                _lerp(top, bottom, 0.82),
            )
            document.line(
                _lerp(left, right, 0.40),
                _lerp(top, bottom, 0.82),
                _lerp(left, right, 0.88),
                _lerp(top, bottom, 0.18),
            )
        else:
            document.line(left, top, right, bottom)
            document.line(right, top, left, bottom)
    return box


def _draw_signature_image(
    document: FPDF,
    raster: _PreparedRaster,
    box: OverlayCanvasBox,
) -> OverlayCanvasBox:
    width_ratio = box.width / raster.metadata.width_pixels
    height_ratio = box.height / raster.metadata.height_pixels
    scale = min(width_ratio, height_ratio)
    width = _quantize_point(raster.metadata.width_pixels * scale)
    height = _quantize_point(raster.metadata.height_pixels * scale)
    left = _quantize_point(box.left + (box.width - width) / 2)
    top = _quantize_point(box.top + (box.height - height) / 2)
    document.image(BytesIO(raster.png_bytes), x=left, y=top, w=width, h=height)
    return OverlayCanvasBox(left, top, left + width, top + height)


def _draw_shape(
    document: FPDF,
    payload: ShapeOverlayPayload,
    box: OverlayCanvasBox,
) -> OverlayCanvasBox | None:
    fill = _rgba(payload.fill_color)
    stroke_color = _rgba(payload.stroke_color)
    stroke = _quantize_point(
        min(
            _MAXIMUM_SHAPE_STROKE_POINTS,
            max(
                _MINIMUM_SHAPE_STROKE_POINTS,
                min(box.width, box.height) / _SHAPE_STROKE_DIVISOR,
            ),
        )
    )
    document.set_fill_color(*fill[:3])
    document.set_draw_color(*stroke_color[:3])
    document.set_line_width(stroke)
    if payload.shape is ShapePrimitive.LINE:
        if stroke_color[3] == 0:
            return None
        inset = stroke / 2
        with document.local_context(stroke_opacity=stroke_color[3]):
            document.line(
                box.left + inset,
                box.top + inset,
                box.right - inset,
                box.bottom - inset,
            )
        return box
    draw_fill = fill[3] > 0
    draw_stroke = stroke_color[3] > 0
    if not draw_fill and not draw_stroke:
        return None
    style = "DF" if draw_fill and draw_stroke else "F" if draw_fill else "D"
    inset = stroke / 2 if draw_stroke else 0.0
    left = box.left + inset
    top = box.top + inset
    width = box.width - 2 * inset
    height = box.height - 2 * inset
    if width <= 0 or height <= 0:
        reject_overlay_rendering(OverlayRenderErrorCode.GEOMETRY_INVALID, "overlay.shape_box")
    with document.local_context(fill_opacity=fill[3], stroke_opacity=stroke_color[3]):
        if payload.shape is ShapePrimitive.RECTANGLE:
            document.rect(left, top, width, height, style=style)
        else:
            document.ellipse(left, top, width, height, style=style)
    return box


def _rgba(color: RgbaColor) -> tuple[int, int, int, float]:
    value = color.value
    return (
        int(value[1:3], 16),
        int(value[3:5], 16),
        int(value[5:7], 16),
        int(value[7:9], 16) / 255.0,
    )


def _actual_text_marker(value: str) -> str:
    """Preserve exact extraction when a shaped cluster emits auxiliary glyphs."""

    encoded = (b"\xfe\xff" + value.encode("utf-16-be")).hex().upper()
    return f"/Span <</ActualText <{encoded}>>> BDC"


def _lerp(start: float, end: float, fraction: float) -> float:
    return _quantize_point(start + (end - start) * fraction)


def _quantize_point(value: float) -> float:
    return round(float(value) + 1e-12, 2)


def _sanitize_and_verify(
    raw: bytes,
    *,
    width: float,
    height: float,
    expected_fonts: int,
    expected_image_resources: int,
    limits: OverlayRenderLimits,
    control: OverlayRenderControl,
) -> bytes:
    sink = _BoundedBytesSink(limits.maximum_output_bytes)
    try:
        with pikepdf.Pdf.open(
            BytesIO(raw),
            password="",
            attempt_recovery=False,
            suppress_warnings=True,
            inherit_page_attributes=False,
        ) as document:
            require_overlay_render_active(control)
            if document.is_encrypted or len(document.pages) != 1:
                reject_overlay_rendering(
                    OverlayRenderErrorCode.SECURITY_VERIFICATION_FAILED,
                    "overlay.pdf_structure",
                )
            if len(document.objects) > limits.maximum_pdf_objects:
                reject_overlay_rendering(
                    OverlayRenderErrorCode.LIMIT_EXCEEDED,
                    "overlay.pdf_objects",
                )
            for key in _NAME_ROOT_REMOVE:
                if key in document.Root:
                    del document.Root[key]
            if pikepdf.Name("/Info") in document.trailer:
                del document.trailer[pikepdf.Name("/Info")]
            document.save(
                cast(Any, sink),
                deterministic_id=True,
                encryption=False,
                compress_streams=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
                normalize_content=False,
                linearize=False,
            )
            require_overlay_render_active(control)
    except OverlayRenderError:
        raise
    except _OutputLimitExceeded:
        reject_overlay_rendering(OverlayRenderErrorCode.LIMIT_EXCEEDED, "overlay.output_bytes")
    except MemoryError, RecursionError:
        reject_overlay_rendering(OverlayRenderErrorCode.LIMIT_EXCEEDED, "overlay.pdf_memory")
    except pikepdf.PasswordError, pikepdf.PdfError, OSError, RuntimeError, ValueError:
        if sink.limit_exceeded:
            reject_overlay_rendering(OverlayRenderErrorCode.LIMIT_EXCEEDED, "overlay.output_bytes")
        reject_overlay_rendering(OverlayRenderErrorCode.DEPENDENCY_FAILED, "overlay.pdf_sanitize")
    sanitized = sink.getvalue()
    if not sanitized or len(sanitized) > limits.maximum_output_bytes:
        reject_overlay_rendering(OverlayRenderErrorCode.LIMIT_EXCEEDED, "overlay.output_bytes")
    _verify_sanitized_pdf(
        sanitized,
        width=width,
        height=height,
        expected_fonts=expected_fonts,
        expected_image_resources=expected_image_resources,
        limits=limits,
        control=control,
    )
    return sanitized


def _verify_sanitized_pdf(
    data: bytes,
    *,
    width: float,
    height: float,
    expected_fonts: int,
    expected_image_resources: int,
    limits: OverlayRenderLimits,
    control: OverlayRenderControl,
) -> None:
    try:
        with pikepdf.Pdf.open(
            BytesIO(data),
            password="",
            attempt_recovery=False,
            suppress_warnings=True,
            inherit_page_attributes=False,
        ) as document:
            require_overlay_render_active(control)
            if document.is_encrypted or len(document.pages) != 1:
                _security_failure("overlay.pdf_structure")
            if len(document.objects) > limits.maximum_pdf_objects:
                reject_overlay_rendering(
                    OverlayRenderErrorCode.LIMIT_EXCEEDED,
                    "overlay.pdf_objects",
                )
            _inspect_objects(document, limits, control)
            page = document.pages[0]
            _verify_page_geometry(page, width, height, limits)
            resources = page.obj.get(pikepdf.Name("/Resources"))
            if not isinstance(resources, pikepdf.Dictionary):
                _security_failure("overlay.pdf_resources")
            fonts = resources.get(pikepdf.Name("/Font"), pikepdf.Dictionary())
            if not isinstance(fonts, pikepdf.Dictionary) or len(fonts) != expected_fonts:
                _security_failure("overlay.pdf_fonts")
            for key in fonts.keys():
                _verify_embedded_font(fonts[key])
            xobjects = resources.get(pikepdf.Name("/XObject"), pikepdf.Dictionary())
            if not isinstance(xobjects, pikepdf.Dictionary):
                _security_failure("overlay.pdf_images")
            if len(xobjects) != expected_image_resources:
                _security_failure("overlay.pdf_images")
            for key in xobjects.keys():
                value = xobjects[key]
                if not (
                    isinstance(value, pikepdf.Stream)
                    and str(value.get(pikepdf.Name("/Type"), "")) == "/XObject"
                    and str(value.get(pikepdf.Name("/Subtype"), "")) == "/Image"
                ):
                    _security_failure("overlay.pdf_images")
    except OverlayRenderError:
        raise
    except MemoryError, RecursionError:
        reject_overlay_rendering(OverlayRenderErrorCode.LIMIT_EXCEEDED, "overlay.pdf_memory")
    except pikepdf.PasswordError, pikepdf.PdfError, OSError, RuntimeError, ValueError:
        reject_overlay_rendering(
            OverlayRenderErrorCode.SECURITY_VERIFICATION_FAILED,
            "overlay.pdf_reopen",
        )


def _inspect_objects(
    document: pikepdf.Pdf,
    limits: OverlayRenderLimits,
    control: OverlayRenderControl,
) -> None:
    """Inspect the complete PDF value graph without trusting indirectness.

    qpdf exposes indirect references as dereferenced objects.  Tracking their
    object-generation pair breaks page-tree and other legal indirect cycles,
    while direct dictionaries and arrays form an acyclic serialization tree.
    Every distinct indirect value and every direct child value consumes the
    existing PDF-object budget so a small indirect-object table cannot hide an
    unbounded direct graph from verification.
    """

    pending: list[object] = []
    seen_indirect: set[tuple[int, int]] = set()
    graph_nodes = 0

    def schedule(value: object) -> None:
        nonlocal graph_nodes

        require_overlay_render_active(control)
        if getattr(value, "is_indirect", False) is True:
            objgen = cast(Any, value).objgen
            key = (int(objgen[0]), int(objgen[1]))
            if key in seen_indirect:
                return
            seen_indirect.add(key)
        graph_nodes += 1
        if graph_nodes > limits.maximum_pdf_objects:
            reject_overlay_rendering(
                OverlayRenderErrorCode.LIMIT_EXCEEDED,
                "overlay.pdf_objects",
            )
        if isinstance(value, (pikepdf.Array, pikepdf.Dictionary, pikepdf.Stream)):
            pending.append(value)

    schedule(document.trailer)
    schedule(document.Root)
    for indirect_value in document.objects:
        schedule(indirect_value)

    while pending:
        require_overlay_render_active(control)
        current = pending.pop()
        if isinstance(current, pikepdf.Array):
            for child in current:
                schedule(child)
            continue

        dictionary = cast(pikepdf.Dictionary, current)
        object_type = str(dictionary.get(pikepdf.Name("/Type"), ""))
        subtype = str(dictionary.get(pikepdf.Name("/Subtype"), ""))
        action = str(dictionary.get(pikepdf.Name("/S"), ""))
        field_type = str(dictionary.get(pikepdf.Name("/FT"), ""))
        if (
            object_type in _PROHIBITED_TYPES
            or subtype in _PROHIBITED_SUBTYPES
            or action in _PROHIBITED_ACTIONS
            or field_type == "/Sig"
        ):
            _security_failure("overlay.pdf_prohibited")
        for key in dictionary.keys():
            if str(key) in _PROHIBITED_KEYS:
                _security_failure("overlay.pdf_prohibited")
            schedule(dictionary[key])


def _verify_page_geometry(
    page: pikepdf.Page,
    width: float,
    height: float,
    limits: OverlayRenderLimits,
) -> None:
    """Require one untransformed full-canvas page across inherited attributes."""

    expected = (0.0, 0.0, width, height)
    current = page.obj
    seen: set[tuple[int, int]] = set()
    found_media_box = False
    visited = 0

    while True:
        visited += 1
        if visited > limits.maximum_pdf_objects:
            _security_failure("overlay.pdf_geometry")
        identity = current.objgen
        if identity == (0, 0) or identity in seen:
            _security_failure("overlay.pdf_geometry")
        seen.add(identity)

        if any(name in current for name in _UNEXPECTED_PAGE_BOXES):
            _security_failure("overlay.pdf_geometry")
        media = current.get(_NAME_MEDIA_BOX)
        if media is not None:
            found_media_box = True
            _require_exact_page_box(media, expected)
        crop = current.get(_NAME_CROP_BOX)
        if crop is not None:
            _require_exact_page_box(crop, expected)
        _require_default_page_number(current.get(_NAME_ROTATE), 0.0)
        _require_default_page_number(current.get(_NAME_USER_UNIT), 1.0)

        parent = current.get(_NAME_PARENT)
        if parent is None:
            break
        if not isinstance(parent, pikepdf.Dictionary):
            _security_failure("overlay.pdf_geometry")
        current = parent

    if not found_media_box:
        _security_failure("overlay.pdf_geometry")


def _require_exact_page_box(value: object, expected: tuple[float, float, float, float]) -> None:
    if not isinstance(value, pikepdf.Array) or len(value) != 4:
        _security_failure("overlay.pdf_geometry")
    try:
        actual = tuple(Decimal(str(item)) for item in value)
        exact_expected = tuple(Decimal(str(item)) for item in expected)
    except InvalidOperation, TypeError, ValueError:
        _security_failure("overlay.pdf_geometry")
    if len(actual) != 4 or any(not item.is_finite() for item in actual):
        _security_failure("overlay.pdf_geometry")
    if actual != exact_expected:
        _security_failure("overlay.pdf_geometry")


def _require_default_page_number(value: object | None, expected: float) -> None:
    if value is None:
        return
    if type(value) not in (int, float, Decimal):
        _security_failure("overlay.pdf_geometry")
    try:
        actual = float(cast(int | float | Decimal, value))
    except TypeError, ValueError, OverflowError:
        _security_failure("overlay.pdf_geometry")
    if not math.isfinite(actual) or actual != expected:
        _security_failure("overlay.pdf_geometry")


def _verify_embedded_font(value: object) -> None:
    if not isinstance(value, pikepdf.Dictionary):
        _security_failure("overlay.pdf_fonts")
    if (
        str(value.get(pikepdf.Name("/Subtype"), "")) != "/Type0"
        or pikepdf.Name("/ToUnicode") not in value
        or not str(value.get(pikepdf.Name("/BaseFont"), "")).endswith("NotoSans")
    ):
        _security_failure("overlay.pdf_fonts")
    descendants = value.get(pikepdf.Name("/DescendantFonts"))
    if not isinstance(descendants, pikepdf.Array) or len(descendants) != 1:
        _security_failure("overlay.pdf_fonts")
    descendant = descendants[0]
    if (
        not isinstance(descendant, pikepdf.Dictionary)
        or str(descendant.get(pikepdf.Name("/Subtype"), "")) != "/CIDFontType2"
    ):
        _security_failure("overlay.pdf_fonts")
    descriptor = descendant.get(pikepdf.Name("/FontDescriptor"))
    if (
        not isinstance(descriptor, pikepdf.Dictionary)
        or pikepdf.Name("/FontFile2") not in descriptor
    ):
        _security_failure("overlay.pdf_fonts")


def _security_failure(context: str) -> Never:
    reject_overlay_rendering(OverlayRenderErrorCode.SECURITY_VERIFICATION_FAILED, context)


def _write_exact_pdf(
    destination: OverlayPdfDestination,
    data: bytes,
    limits: OverlayRenderLimits,
    control: OverlayRenderControl,
) -> str:
    if not 1 <= len(data) <= limits.maximum_output_bytes:
        reject_overlay_rendering(OverlayRenderErrorCode.LIMIT_EXCEEDED, "overlay.output_bytes")
    digest = hashlib.sha256()
    view = memoryview(data)
    offset = 0
    try:
        while offset < len(data):
            require_overlay_render_active(control)
            end = min(offset + limits.io_chunk_bytes, len(data))
            written = _destination_callback(destination, "write", view[offset:end])
            if type(written) is not int or not 1 <= written <= end - offset:
                reject_overlay_rendering(
                    OverlayRenderErrorCode.DESTINATION_FAILED,
                    "overlay.destination_write",
                )
            digest.update(view[offset : offset + written])
            offset += written
        require_overlay_render_active(control)
        _destination_callback(destination, "flush")
        require_overlay_render_active(control)
        result = digest.hexdigest()
        position = _destination_callback(destination, "tell")
        extent = _destination_callback(destination, "seek", 0, os.SEEK_END)
        restored = _destination_callback(destination, "seek", len(data), os.SEEK_SET)
        truncated = _destination_callback(destination, "truncate", len(data))
        if (
            type(position) is not int
            or type(extent) is not int
            or type(restored) is not int
            or type(truncated) is not int
            or position != len(data)
            or extent != len(data)
            or restored != len(data)
            or truncated != len(data)
        ):
            reject_overlay_rendering(
                OverlayRenderErrorCode.DESTINATION_FAILED,
                "overlay.destination_extent",
            )
        _destination_callback(destination, "flush")
        require_overlay_render_active(control)
    except OverlayRenderError:
        _discard_destination(destination)
        raise
    except MemoryError, RecursionError:
        _discard_destination(destination)
        raise
    except KeyboardInterrupt, SystemExit, GeneratorExit:
        _discard_destination(destination)
        raise
    except Exception:
        _discard_destination(destination)
        reject_overlay_rendering(
            OverlayRenderErrorCode.DESTINATION_FAILED,
            "overlay.destination_write",
        )
    return result


def _destination_callback(destination: object, method_name: str, *args: object) -> object:
    try:
        callback = getattr(destination, method_name)
        if not callable(callback):
            raise TypeError
        return callback(*args)
    except MemoryError, RecursionError:
        raise
    except KeyboardInterrupt, SystemExit, GeneratorExit:
        raise
    except Exception:
        raise _DestinationCallbackFailed from None


def _discard_destination(destination: OverlayPdfDestination) -> None:
    """Best-effort rollback for the caller-owned operation-scoped staging sink."""

    try:
        destination.seek(0, os.SEEK_SET)
        destination.truncate(0)
        destination.flush()
    except BaseException:
        pass

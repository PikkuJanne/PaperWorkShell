"""Bounded Pillow adapter for one-shot JPEG/PNG page normalization."""

from __future__ import annotations

import hashlib
import hmac
import os
import threading
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import BinaryIO, Final, Literal, Protocol, cast
from zlib import crc32

import PIL
from PIL import Image, ImageFile, PngImagePlugin, UnidentifiedImageError

from paperworkshell.domain.image_pages import (
    ImageAlphaNormalizationPolicy,
    ImageExifOrientation,
    ImageNormalizationLimits,
    ImageNormalizationRequest,
    ImageNormalizationResult,
    ImagePageError,
    ImagePageErrorCode,
    ImagePageMetadata,
    ImageQuarterTurn,
    ImageRasterBackground,
    ImageSourceColorModel,
    ImageSourceFormat,
    plan_image_normalization_layout,
    reject_image_page,
    validate_image_normalization_request,
    validate_image_normalization_result,
)

EXPECTED_PILLOW_VERSION: Final = "12.3.0"

_PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE: Final = b"\xff\xd8"
_PNG_CRITICAL_CHUNKS: Final = {b"IHDR", b"PLTE", b"IDAT", b"IEND"}
_PNG_METADATA_CHUNKS: Final = {
    b"cHRM",
    b"eXIf",
    b"gAMA",
    b"hIST",
    b"iCCP",
    b"iTXt",
    b"pHYs",
    b"PLTE",
    b"sBIT",
    b"sPLT",
    b"sRGB",
    b"tEXt",
    b"tIME",
    b"tRNS",
    b"zTXt",
}
_PNG_APNG_CHUNKS: Final = {b"acTL", b"fcTL", b"fdAT"}
_PNG_REPEATABLE_METADATA_CHUNKS: Final = {b"iTXt", b"sPLT", b"tEXt", b"zTXt"}
_PNG_MAX_SINGLE_TEXT_BYTES: Final = 1_048_576
_JPEG_STANDALONE_MARKERS: Final = frozenset((*range(0xD0, 0xDA), 0x01))
_JPEG_SOF_MARKERS: Final = frozenset(
    marker for marker in range(0xC0, 0xD0) if marker not in {0xC4, 0xC8, 0xCC}
)
_JPEG_METADATA_MARKERS: Final = frozenset((*range(0xE0, 0xF0), 0xFE))
_JPEG_EXIF_SIGNATURE: Final = b"Exif\x00\x00"
_JPEG_ICC_SIGNATURE: Final = b"ICC_PROFILE\x00"
_EXIF_ORIENTATION_TAG: Final = 274
_SCAN_BLOCK_BYTES: Final = 64 * 1024

_DECODER_LOCK: Final = threading.Lock()
_LOCK_POLL_SECONDS: Final = 0.05


class ImageNormalizationControl(Protocol):
    """Cooperative worker probes checked between every bounded phase."""

    def cancellation_requested(self) -> bool: ...

    def deadline_expired(self) -> bool: ...


class _SourceFault(ValueError):
    """Content-free marker for a caller-owned source protocol failure."""


@dataclass(frozen=True, slots=True)
class _MetadataScan:
    record_count: int
    byte_count: int
    has_embedded_color_profile: bool
    has_non_srgb_color_profile: bool
    has_exif: bool
    exif_orientation: ImageExifOrientation


@dataclass(frozen=True, slots=True)
class _SourceFacts:
    byte_size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class PillowImageDecoder:
    """Normalize one exact immutable source to opaque raw RGBA8 pixels."""

    def normalize(
        self,
        source: BinaryIO,
        destination: BinaryIO,
        *,
        expected_format: ImageSourceFormat,
        request: ImageNormalizationRequest,
        limits: ImageNormalizationLimits,
        control: ImageNormalizationControl,
    ) -> ImageNormalizationResult:
        """Decode, orient, fit, and write one page without owning either stream."""

        if (
            type(expected_format) is not ImageSourceFormat
            or type(request) is not ImageNormalizationRequest
            or expected_format is not request.expected_source_format
        ):
            reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.decoder_request")
        validate_image_normalization_request(request, limits)
        _require_approved_runtime()
        _require_active(control)

        original_position = _source_position(source)
        result: ImageNormalizationResult | None = None
        operation_error: BaseException | None = None
        try:
            facts = _authenticate_source(source, request, limits, control)
            _seek_exact(source, 0, "image.source_seek")
            scan = _scan_metadata(source, expected_format, facts.byte_size, limits, control)
            _require_supported_color_profile(scan, request)
            _seek_exact(source, 0, "image.source_seek")
            with _serialized_decoder(control):
                with _pillow_limits(limits, scan):
                    result = _decode_and_normalize(
                        source,
                        destination,
                        expected_format,
                        request,
                        limits,
                        control,
                        scan,
                    )
        except BaseException as error:
            operation_error = error

        restore_error = _restore_source(source, original_position)
        if restore_error is not None:
            raise restore_error
        if operation_error is not None:
            if isinstance(operation_error, ImagePageError):
                raise operation_error
            if isinstance(operation_error, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                raise operation_error
            _map_decoder_failure(operation_error, "image.decoder")
        if result is None:
            reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.decoder_result")
        return result


def _decode_and_normalize(
    source: BinaryIO,
    destination: BinaryIO,
    expected_format: ImageSourceFormat,
    request: ImageNormalizationRequest,
    limits: ImageNormalizationLimits,
    control: ImageNormalizationControl,
    scan: _MetadataScan,
) -> ImageNormalizationResult:
    image: Image.Image | None = None
    working: Image.Image | None = None
    try:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                image = Image.open(
                    cast(BinaryIO, _BorrowedSource(source)),
                    formats=[_pillow_format(expected_format)],
                )
        except Image.DecompressionBombError, Image.DecompressionBombWarning, MemoryError:
            reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.limit_decode")
        except UnidentifiedImageError, SyntaxError, OSError, ValueError:
            reject_image_page(ImagePageErrorCode.MALFORMED, "image.decoder_open")

        _require_active(control)
        actual_format = _source_format(image)
        if actual_format is not expected_format:
            reject_image_page(ImagePageErrorCode.FORMAT_MISMATCH, "image.source_format")
        frame_count = _frame_count(image)
        if frame_count != 1 or bool(getattr(image, "is_animated", False)):
            reject_image_page(ImagePageErrorCode.UNSUPPORTED, "image.metadata_frames")

        width, height = _image_size(image)
        color_model, components, bits, has_alpha = _classify_image(image, expected_format)
        orientation = scan.exif_orientation
        metadata = ImagePageMetadata(
            source_format=expected_format,
            source_color_model=color_model,
            alpha_policy=request.alpha_policy,
            exif_orientation=orientation,
            encoded_width_pixels=width,
            encoded_height_pixels=height,
            oriented_width_pixels=height if orientation.swaps_axes else width,
            oriented_height_pixels=width if orientation.swaps_axes else height,
            component_count=components,
            bits_per_component=bits,
            frame_count=frame_count,
            metadata_record_count=scan.record_count,
            metadata_byte_count=scan.byte_count,
            has_alpha=has_alpha,
            has_embedded_color_profile=scan.has_embedded_color_profile,
            has_exif=scan.has_exif,
        )
        layout = plan_image_normalization_layout(metadata, request, limits)
        _require_active(control)

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                image.load()
        except Image.DecompressionBombError, Image.DecompressionBombWarning, MemoryError:
            reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.limit_decode")
        except EOFError, OSError, SyntaxError, ValueError:
            reject_image_page(ImagePageErrorCode.MALFORMED, "image.decoder_load")
        _require_active(control)

        _discard_unneeded_metadata(image)
        working = image
        image = None
        oriented = _apply_exif_orientation(working, orientation)
        working = _replace_image(working, oriented)
        rotated = _apply_quarter_turn(working, request.additional_rotation)
        working = _replace_image(working, rotated)
        _require_active(control)
        converted = _convert_to_rgba(working)
        working = _replace_image(working, converted)
        if request.alpha_policy is ImageAlphaNormalizationPolicy.COMPOSITE_ON_OPAQUE_WHITE:
            opaque = _composite_opaque_white(working, has_alpha=has_alpha)
            working = _replace_image(working, opaque)
        _require_active(control)
        if working.size != (layout.width_pixels, layout.height_pixels):
            try:
                resized = working.resize(
                    (layout.width_pixels, layout.height_pixels),
                    resample=Image.Resampling.LANCZOS,
                    reducing_gap=3.0,
                )
            except MemoryError:
                reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.limit_memory")
            working = _replace_image(working, resized)
        _require_active(control)
        pixels = working.tobytes("raw", "RGBA")
        _close_image_no_raise(working)
        working = None
        if request.alpha_policy is ImageAlphaNormalizationPolicy.PRESERVE_STRAIGHT_ALPHA:
            pixels = _canonicalize_fully_transparent_rgb(
                pixels,
                row_stride=layout.row_stride_bytes,
                control=control,
            )
        if len(pixels) != layout.byte_count or (
            request.alpha_policy is ImageAlphaNormalizationPolicy.COMPOSITE_ON_OPAQUE_WHITE
            and any(alpha != 255 for alpha in memoryview(pixels)[3::4])
        ):
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.decoder_pixels")
        digest = _write_exact(destination, pixels, layout.row_stride_bytes, control)
        del pixels
        result = ImageNormalizationResult(
            metadata=metadata,
            additional_rotation=request.additional_rotation,
            width_pixels=layout.width_pixels,
            height_pixels=layout.height_pixels,
            row_stride_bytes=layout.row_stride_bytes,
            bytes_written=layout.byte_count,
            pixel_sha256=digest,
            alpha_policy=request.alpha_policy,
            background=(
                ImageRasterBackground.OPAQUE_WHITE
                if request.alpha_policy is ImageAlphaNormalizationPolicy.COMPOSITE_ON_OPAQUE_WHITE
                else ImageRasterBackground.PRESERVED_ALPHA
            ),
        )
        validate_image_normalization_result(request, limits, result)
        return result
    except ImagePageError:
        raise
    except MemoryError:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.limit_memory")
    except Exception:
        reject_image_page(ImagePageErrorCode.DEPENDENCY_FAILED, "image.decoder_dependency")
    finally:
        _close_image_no_raise(working)
        _close_image_no_raise(image)


def _require_approved_runtime() -> None:
    """Fail closed before source access when Pillow is not the admitted build."""

    try:
        version = PIL.__version__
    except Exception:
        reject_image_page(
            ImagePageErrorCode.DEPENDENCY_UNAVAILABLE,
            "image.decoder_runtime",
        )
    if type(version) is not str or version != EXPECTED_PILLOW_VERSION:
        reject_image_page(
            ImagePageErrorCode.DEPENDENCY_UNAVAILABLE,
            "image.decoder_runtime",
        )


def _authenticate_source(
    source: BinaryIO,
    request: ImageNormalizationRequest,
    limits: ImageNormalizationLimits,
    control: ImageNormalizationControl,
) -> _SourceFacts:
    _seek_exact(source, 0, "image.source_seek")
    digest = hashlib.sha256()
    total = 0
    while True:
        _require_active(control)
        chunk = _read(source, min(_SCAN_BLOCK_BYTES, limits.maximum_source_bytes + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > limits.maximum_source_bytes or total > request.expected_source_bytes:
            reject_image_page(ImagePageErrorCode.FORMAT_MISMATCH, "image.source_length")
        digest.update(chunk)
    if total != request.expected_source_bytes or not hmac.compare_digest(
        digest.hexdigest(), request.expected_source_sha256
    ):
        reject_image_page(ImagePageErrorCode.FORMAT_MISMATCH, "image.source_identity")
    return _SourceFacts(total, digest.hexdigest())


class _BorrowedSource:
    """Forward decoder I/O while making ``close`` a caller-ownership no-op."""

    __slots__ = ("_source",)

    def __init__(self, source: BinaryIO) -> None:
        self._source = source

    def read(self, size: int = -1, /) -> bytes:
        return self._source.read(size)

    def seek(self, offset: int, whence: int = os.SEEK_SET, /) -> int:
        return self._source.seek(offset, whence)

    def tell(self) -> int:
        return self._source.tell()

    def close(self) -> None:
        return


def _scan_metadata(
    source: BinaryIO,
    expected: ImageSourceFormat,
    byte_size: int,
    limits: ImageNormalizationLimits,
    control: ImageNormalizationControl,
) -> _MetadataScan:
    if expected is ImageSourceFormat.PNG:
        return _scan_png(source, byte_size, limits, control)
    if expected is ImageSourceFormat.JPEG:
        return _scan_jpeg(source, byte_size, limits, control)
    reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.source_format")


def _require_supported_color_profile(
    scan: _MetadataScan,
    request: ImageNormalizationRequest,
) -> None:
    """Keep the straight-alpha signature profile deterministic without parsing ICC data.

    The released opaque page-normalization profile retains its existing behavior.  Signature
    rasters instead become unprofiled DeviceRGB values, so an embedded ICC profile or PNG
    chromaticity/gamma declaration must fail before Pillow parses the image.  A well-formed PNG
    ``sRGB`` rendering-intent chunk is compatible with that fixed interpretation.
    """

    if request.alpha_policy is not ImageAlphaNormalizationPolicy.PRESERVE_STRAIGHT_ALPHA:
        return
    if scan.has_embedded_color_profile:
        reject_image_page(ImagePageErrorCode.UNSUPPORTED, "image.signature_icc_profile")
    if scan.has_non_srgb_color_profile:
        reject_image_page(ImagePageErrorCode.UNSUPPORTED, "image.signature_color_profile")


def _scan_png(
    source: BinaryIO,
    byte_size: int,
    limits: ImageNormalizationLimits,
    control: ImageNormalizationControl,
) -> _MetadataScan:
    if _read_exact(source, 8) != _PNG_SIGNATURE:
        reject_image_page(ImagePageErrorCode.FORMAT_MISMATCH, "image.png_signature")
    offset = 8
    structure_records = 0
    metadata_records = 0
    metadata_bytes = 0
    saw_ihdr = False
    saw_idat = False
    idat_complete = False
    saw_iend = False
    saw_plte = False
    saw_trns = False
    png_bit_depth: int | None = None
    png_color_type: int | None = None
    palette_entries: int | None = None
    seen_singleton_metadata: set[bytes] = set()
    has_icc = False
    has_non_srgb_color_profile = False
    has_exif = False
    orientation = ImageExifOrientation.NORMAL
    while offset < byte_size:
        _require_active(control)
        header = _read_exact(source, 8)
        offset += 8
        length = int.from_bytes(header[:4], "big")
        chunk_type = header[4:]
        if length > byte_size - offset - 4:
            reject_image_page(ImagePageErrorCode.MALFORMED, "image.png_chunk")
        if not _valid_png_chunk_type(chunk_type):
            reject_image_page(ImagePageErrorCode.MALFORMED, "image.png_chunk_type")
        structure_records += 1
        _require_record_budget(structure_records, limits)
        if not saw_ihdr and (chunk_type != b"IHDR" or length != 13):
            reject_image_page(ImagePageErrorCode.MALFORMED, "image.png_ihdr")
        if saw_ihdr and chunk_type == b"IHDR":
            reject_image_page(ImagePageErrorCode.MALFORMED, "image.png_ihdr")
        if saw_iend:
            reject_image_page(ImagePageErrorCode.MALFORMED, "image.png_trailing")
        if chunk_type in _PNG_APNG_CHUNKS:
            reject_image_page(ImagePageErrorCode.UNSUPPORTED, "image.metadata_frames")
        if chunk_type[0] & 0x20 == 0 and chunk_type not in _PNG_CRITICAL_CHUNKS:
            reject_image_page(ImagePageErrorCode.UNSUPPORTED, "image.png_critical")
        if chunk_type == b"PLTE":
            if (
                saw_plte
                or saw_trns
                or saw_idat
                or png_color_type not in {2, 3, 6}
                or not 3 <= length <= 768
                or length % 3 != 0
            ):
                reject_image_page(ImagePageErrorCode.MALFORMED, "image.png_plte")
            palette_entries = length // 3
            if png_color_type == 3 and (
                png_bit_depth is None
                or png_bit_depth not in {1, 2, 4, 8}
                or palette_entries > 1 << png_bit_depth
            ):
                reject_image_page(ImagePageErrorCode.MALFORMED, "image.png_plte")
            saw_plte = True
        if chunk_type == b"tRNS":
            if saw_trns or saw_idat:
                reject_image_page(ImagePageErrorCode.MALFORMED, "image.png_transparency")
            if (
                (png_color_type == 0 and length != 2)
                or (png_color_type == 2 and length != 6)
                or (
                    png_color_type == 3
                    and (
                        not saw_plte
                        or palette_entries is None
                        or not 1 <= length <= palette_entries
                    )
                )
                or png_color_type not in {0, 2, 3}
            ):
                reject_image_page(ImagePageErrorCode.MALFORMED, "image.png_transparency")
            saw_trns = True
        if chunk_type in _PNG_METADATA_CHUNKS and chunk_type not in {
            *_PNG_REPEATABLE_METADATA_CHUNKS,
            b"eXIf",
            b"tRNS",
        }:
            if chunk_type in seen_singleton_metadata:
                reject_image_page(ImagePageErrorCode.METADATA_INVALID, "image.png_metadata")
            seen_singleton_metadata.add(chunk_type)
        if chunk_type == b"IDAT":
            if png_color_type == 3 and not saw_plte:
                reject_image_page(ImagePageErrorCode.MALFORMED, "image.png_plte")
            if idat_complete:
                reject_image_page(ImagePageErrorCode.MALFORMED, "image.png_idat")
        elif saw_idat:
            idat_complete = True
        if chunk_type in _PNG_METADATA_CHUNKS or chunk_type not in _PNG_CRITICAL_CHUNKS:
            metadata_records += 1
            metadata_bytes += length
            _require_metadata_budget(metadata_bytes, limits)
        if chunk_type in _PNG_METADATA_CHUNKS:
            has_icc = has_icc or chunk_type == b"iCCP"
            has_non_srgb_color_profile = has_non_srgb_color_profile or chunk_type in {
                b"cHRM",
                b"gAMA",
            }
            if chunk_type == b"eXIf":
                if has_exif:
                    reject_image_page(
                        ImagePageErrorCode.METADATA_INVALID,
                        "image.exif_duplicate",
                    )
                has_exif = True
                orientation = _read_tiff_orientation(
                    source,
                    payload_length=length,
                    tiff_offset=0,
                    limits=limits,
                    control=control,
                )
        payload_crc = crc32(chunk_type)
        ihdr_payload: bytes | None = None
        srgb_payload: bytes | None = None
        if chunk_type == b"sRGB" and length != 1:
            reject_image_page(ImagePageErrorCode.METADATA_INVALID, "image.png_srgb")
        remaining = length
        while remaining:
            _require_active(control)
            chunk = _read_exact(source, min(remaining, _SCAN_BLOCK_BYTES))
            if chunk_type == b"IHDR":
                ihdr_payload = chunk
            elif chunk_type == b"sRGB":
                srgb_payload = chunk
            payload_crc = crc32(chunk, payload_crc)
            remaining -= len(chunk)
        expected_crc = int.from_bytes(_read_exact(source, 4), "big")
        offset += length + 4
        if payload_crc & 0xFFFF_FFFF != expected_crc:
            reject_image_page(ImagePageErrorCode.MALFORMED, "image.png_crc")
        if chunk_type == b"IHDR":
            if ihdr_payload is None or len(ihdr_payload) != 13:
                reject_image_page(ImagePageErrorCode.MALFORMED, "image.png_ihdr")
            png_bit_depth = ihdr_payload[8]
            png_color_type = ihdr_payload[9]
        elif chunk_type == b"sRGB" and (srgb_payload is None or srgb_payload[0] not in range(4)):
            reject_image_page(ImagePageErrorCode.METADATA_INVALID, "image.png_srgb")
        saw_ihdr = saw_ihdr or chunk_type == b"IHDR"
        saw_idat = saw_idat or chunk_type == b"IDAT"
        if chunk_type == b"IEND":
            if length != 0:
                reject_image_page(ImagePageErrorCode.MALFORMED, "image.png_iend")
            saw_iend = True
            break
    if not (saw_ihdr and saw_idat and saw_iend) or offset != byte_size:
        reject_image_page(ImagePageErrorCode.MALFORMED, "image.png_framing")
    return _MetadataScan(
        metadata_records,
        metadata_bytes,
        has_icc,
        has_non_srgb_color_profile,
        has_exif,
        orientation,
    )


def _valid_png_chunk_type(chunk_type: bytes) -> bool:
    """Accept the PNG letter-only type grammar with the reserved bit clear."""

    return len(chunk_type) == 4 and all(
        (65 <= value <= 90 or 97 <= value <= 122) and (index != 2 or 65 <= value <= 90)
        for index, value in enumerate(chunk_type)
    )


def _scan_jpeg(
    source: BinaryIO,
    byte_size: int,
    limits: ImageNormalizationLimits,
    control: ImageNormalizationControl,
) -> _MetadataScan:
    if _read_exact(source, 2) != _JPEG_SIGNATURE:
        reject_image_page(ImagePageErrorCode.FORMAT_MISMATCH, "image.jpeg_signature")
    offset = 2
    structure_records = 0
    metadata_records = 0
    metadata_bytes = 0
    has_icc = False
    has_exif = False
    orientation = ImageExifOrientation.NORMAL
    saw_sof = False
    saw_scan = False
    pending_marker: int | None = None
    while pending_marker is not None or offset < byte_size:
        _require_active(control)
        if pending_marker is None:
            marker_prefix = _read_exact(source, 1)
            offset += 1
            if marker_prefix != b"\xff":
                reject_image_page(ImagePageErrorCode.MALFORMED, "image.jpeg_marker")
            while True:
                marker = _read_exact(source, 1)[0]
                offset += 1
                if marker != 0xFF:
                    break
        else:
            marker = pending_marker
            pending_marker = None

        if marker == 0xD9:
            if not saw_scan:
                reject_image_page(ImagePageErrorCode.MALFORMED, "image.jpeg_framing")
            _require_jpeg_trailing(source, byte_size - offset, control)
            return _MetadataScan(
                metadata_records,
                metadata_bytes,
                has_icc,
                False,
                has_exif,
                orientation,
            )
        structure_records += 1
        _require_record_budget(structure_records, limits)
        if marker in {0x00, 0xD8}:
            reject_image_page(ImagePageErrorCode.MALFORMED, "image.jpeg_marker")
        if marker in _JPEG_STANDALONE_MARKERS:
            continue
        segment_length = int.from_bytes(_read_exact(source, 2), "big")
        offset += 2
        if segment_length < 2 or segment_length - 2 > byte_size - offset:
            reject_image_page(ImagePageErrorCode.MALFORMED, "image.jpeg_segment")
        payload_length = segment_length - 2
        if marker in _JPEG_METADATA_MARKERS:
            metadata_records += 1
            metadata_bytes += payload_length
            _require_metadata_budget(metadata_bytes, limits)
            prefix_length = min(payload_length, len(_JPEG_ICC_SIGNATURE))
            prefix = _read_exact(source, prefix_length)
            if marker == 0xE1 and prefix.startswith(_JPEG_EXIF_SIGNATURE):
                if has_exif:
                    reject_image_page(
                        ImagePageErrorCode.METADATA_INVALID,
                        "image.exif_duplicate",
                    )
                has_exif = True
                _seek_exact(source, offset, "image.exif_seek")
                orientation = _read_tiff_orientation(
                    source,
                    payload_length=payload_length,
                    tiff_offset=len(_JPEG_EXIF_SIGNATURE),
                    limits=limits,
                    control=control,
                )
                prefix = _read_exact(source, prefix_length)
            if marker == 0xE2 and prefix.startswith(_JPEG_ICC_SIGNATURE):
                has_icc = True
            _discard_exact(source, payload_length - prefix_length, control)
            offset += payload_length
            continue
        if marker in _JPEG_SOF_MARKERS:
            if saw_sof:
                reject_image_page(ImagePageErrorCode.UNSUPPORTED, "image.metadata_frames")
            saw_sof = True
        if marker == 0xDA:
            _discard_exact(source, payload_length, control)
            offset += payload_length
            if not saw_sof:
                reject_image_page(ImagePageErrorCode.MALFORMED, "image.jpeg_scan")
            saw_scan = True
            pending_marker, offset = _scan_jpeg_entropy(
                source,
                offset,
                byte_size,
                control,
            )
            continue
        _discard_exact(source, payload_length, control)
        offset += payload_length
    reject_image_page(ImagePageErrorCode.MALFORMED, "image.jpeg_framing")


def _scan_jpeg_entropy(
    source: BinaryIO,
    offset: int,
    byte_size: int,
    control: ImageNormalizationControl,
) -> tuple[int, int]:
    saw_marker_prefix = False
    while offset < byte_size:
        _require_active(control)
        chunk_start = offset
        chunk = _read_exact(source, min(byte_size - offset, _SCAN_BLOCK_BYTES))
        offset += len(chunk)
        for index, value in enumerate(chunk):
            if not saw_marker_prefix:
                saw_marker_prefix = value == 0xFF
                continue
            if value == 0xFF:
                continue
            saw_marker_prefix = False
            if value == 0x00 or value == 0x01 or 0xD0 <= value <= 0xD7:
                continue
            marker_end = chunk_start + index + 1
            if marker_end != offset:
                _seek_exact(source, marker_end, "image.source_seek")
            return value, marker_end
    reject_image_page(ImagePageErrorCode.MALFORMED, "image.jpeg_eoi")


def _require_jpeg_trailing(
    source: BinaryIO,
    remaining: int,
    control: ImageNormalizationControl,
) -> None:
    while remaining:
        _require_active(control)
        chunk = _read_exact(source, min(remaining, _SCAN_BLOCK_BYTES))
        remaining -= len(chunk)
        if chunk.strip(b"\x00\t\n\x0c\r "):
            reject_image_page(ImagePageErrorCode.MALFORMED, "image.jpeg_trailing")


def _read_tiff_orientation(
    source: BinaryIO,
    *,
    payload_length: int,
    tiff_offset: int,
    limits: ImageNormalizationLimits,
    control: ImageNormalizationControl,
) -> ImageExifOrientation:
    """Read only TIFF IFD0 orientation and restore the payload start."""

    payload_start = _source_position(source)
    payload_end = payload_start + payload_length
    tiff_start = payload_start + tiff_offset
    try:
        if payload_length - tiff_offset < 8:
            reject_image_page(ImagePageErrorCode.METADATA_INVALID, "image.exif_header")
        _seek_exact(source, tiff_start, "image.exif_seek")
        byte_order = _read_exact(source, 2)
        endian: Literal["little", "big"]
        if byte_order == b"II":
            endian = "little"
        elif byte_order == b"MM":
            endian = "big"
        else:
            reject_image_page(ImagePageErrorCode.METADATA_INVALID, "image.exif_byte_order")
        if int.from_bytes(_read_exact(source, 2), endian) != 42:
            reject_image_page(ImagePageErrorCode.METADATA_INVALID, "image.exif_header")
        ifd_offset = int.from_bytes(_read_exact(source, 4), endian)
        ifd_start = tiff_start + ifd_offset
        if ifd_offset < 8 or ifd_start + 2 > payload_end:
            reject_image_page(ImagePageErrorCode.METADATA_INVALID, "image.exif_ifd")
        _seek_exact(source, ifd_start, "image.exif_seek")
        entry_count = int.from_bytes(_read_exact(source, 2), endian)
        if entry_count > limits.maximum_metadata_records:
            reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.limit_metadata_records")
        if ifd_start + 2 + entry_count * 12 > payload_end:
            reject_image_page(ImagePageErrorCode.METADATA_INVALID, "image.exif_ifd")
        orientation: ImageExifOrientation | None = None
        for _index in range(entry_count):
            _require_active(control)
            entry = _read_exact(source, 12)
            if int.from_bytes(entry[0:2], endian) != _EXIF_ORIENTATION_TAG:
                continue
            if orientation is not None:
                reject_image_page(ImagePageErrorCode.METADATA_INVALID, "image.exif_duplicate")
            if int.from_bytes(entry[2:4], endian) != 3 or int.from_bytes(entry[4:8], endian) != 1:
                reject_image_page(ImagePageErrorCode.METADATA_INVALID, "image.exif_orientation")
            value = int.from_bytes(entry[8:10], endian)
            if value not in range(1, 9):
                reject_image_page(ImagePageErrorCode.METADATA_INVALID, "image.exif_orientation")
            orientation = ImageExifOrientation(value)
        return ImageExifOrientation.NORMAL if orientation is None else orientation
    finally:
        _seek_exact(source, payload_start, "image.exif_restore")


def _classify_image(
    image: Image.Image,
    source_format: ImageSourceFormat,
) -> tuple[ImageSourceColorModel, int, int, bool]:
    mode = image.mode
    transparency = "transparency" in image.info
    classifications: dict[str, tuple[ImageSourceColorModel, int, int, bool]] = {
        "1": (ImageSourceColorModel.BILEVEL, 1, 1, False),
        "L": (
            ImageSourceColorModel.GRAYSCALE_TRANSPARENCY
            if transparency
            else ImageSourceColorModel.GRAYSCALE,
            1,
            8,
            transparency,
        ),
        "LA": (ImageSourceColorModel.GRAYSCALE_ALPHA, 2, 8, True),
        "P": (
            ImageSourceColorModel.PALETTE_ALPHA if transparency else ImageSourceColorModel.PALETTE,
            1,
            _png_palette_bits(image),
            transparency,
        ),
        "RGB": (
            ImageSourceColorModel.RGB_TRANSPARENCY if transparency else ImageSourceColorModel.RGB,
            3,
            8,
            transparency,
        ),
        "RGBA": (ImageSourceColorModel.RGBA, 4, 8, True),
        "CMYK": (ImageSourceColorModel.CMYK, 4, 8, False),
        "I;16": (ImageSourceColorModel.GRAYSCALE, 1, 16, False),
        "I;16B": (ImageSourceColorModel.GRAYSCALE, 1, 16, False),
        "I;16L": (ImageSourceColorModel.GRAYSCALE, 1, 16, False),
    }
    result = classifications.get(mode)
    if result is None:
        reject_image_page(ImagePageErrorCode.UNSUPPORTED, "image.source_color")
    if source_format is ImageSourceFormat.JPEG and result[0] not in {
        ImageSourceColorModel.GRAYSCALE,
        ImageSourceColorModel.RGB,
        ImageSourceColorModel.CMYK,
    }:
        reject_image_page(ImagePageErrorCode.UNSUPPORTED, "image.jpeg_color")
    return result


def _png_palette_bits(image: Image.Image) -> int:
    value = image.info.get("bits", 8)
    return value if type(value) is int and value in {1, 2, 4, 8} else 8


def _apply_exif_orientation(image: Image.Image, orientation: ImageExifOrientation) -> Image.Image:
    transforms: dict[ImageExifOrientation, Image.Transpose | None] = {
        ImageExifOrientation.NORMAL: None,
        ImageExifOrientation.MIRROR_HORIZONTAL: Image.Transpose.FLIP_LEFT_RIGHT,
        ImageExifOrientation.ROTATE_180: Image.Transpose.ROTATE_180,
        ImageExifOrientation.MIRROR_VERTICAL: Image.Transpose.FLIP_TOP_BOTTOM,
        ImageExifOrientation.TRANSPOSE: Image.Transpose.TRANSPOSE,
        ImageExifOrientation.ROTATE_90_CLOCKWISE: Image.Transpose.ROTATE_270,
        ImageExifOrientation.TRANSVERSE: Image.Transpose.TRANSVERSE,
        ImageExifOrientation.ROTATE_270_CLOCKWISE: Image.Transpose.ROTATE_90,
    }
    transform = transforms[orientation]
    return image if transform is None else image.transpose(transform)


def _apply_quarter_turn(image: Image.Image, rotation: ImageQuarterTurn) -> Image.Image:
    transform = {
        ImageQuarterTurn.DEGREES_0: None,
        ImageQuarterTurn.DEGREES_90: Image.Transpose.ROTATE_270,
        ImageQuarterTurn.DEGREES_180: Image.Transpose.ROTATE_180,
        ImageQuarterTurn.DEGREES_270: Image.Transpose.ROTATE_90,
    }[rotation]
    return image if transform is None else image.transpose(transform)


def _replace_image(current: Image.Image, replacement: Image.Image) -> Image.Image:
    """Transfer the working reference and promptly release a superseded core."""

    if replacement is not current:
        _close_image_no_raise(current)
    return replacement


def _close_image_no_raise(image: Image.Image | None) -> None:
    if image is None:
        return
    try:
        image.close()
    except Exception:
        pass


def _discard_unneeded_metadata(image: Image.Image) -> None:
    """Retain only pixel interpretation state after metadata facts are captured."""

    try:
        sentinel = object()
        transparency = image.info.get("transparency", sentinel)
        image.info.clear()
        if transparency is not sentinel:
            image.info["transparency"] = transparency
        text = getattr(image, "text", None)
        if type(text) is dict:
            text.clear()
    except Exception:
        reject_image_page(ImagePageErrorCode.DEPENDENCY_FAILED, "image.decoder_metadata")


def _convert_to_rgba(image: Image.Image) -> Image.Image:
    try:
        return image.convert("RGBA")
    except MemoryError:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.limit_memory")
    except Exception:
        reject_image_page(ImagePageErrorCode.DEPENDENCY_FAILED, "image.decoder_color")


def _composite_opaque_white(image: Image.Image, *, has_alpha: bool) -> Image.Image:
    if not has_alpha:
        return image
    white: Image.Image | None = None
    try:
        white = Image.new("RGBA", image.size, (255, 255, 255, 255))
        white.alpha_composite(image)
        return white
    except MemoryError:
        _close_image_no_raise(white)
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.limit_memory")
    except Exception:
        _close_image_no_raise(white)
        reject_image_page(ImagePageErrorCode.DEPENDENCY_FAILED, "image.decoder_color")


def _canonicalize_fully_transparent_rgb(
    pixels: bytes,
    *,
    row_stride: int,
    control: ImageNormalizationControl,
) -> bytes:
    """Clear hidden RGB samples where alpha is zero while retaining straight alpha."""

    if (
        type(pixels) is not bytes
        or type(row_stride) is not int
        or row_stride < 4
        or row_stride % 4
        or len(pixels) % row_stride
    ):
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.decoder_pixels")
    view = memoryview(pixels)
    found_transparent = False
    for row_start in range(0, len(pixels), row_stride):
        _require_active(control)
        if any(alpha == 0 for alpha in view[row_start + 3 : row_start + row_stride : 4]):
            found_transparent = True
            break
    if not found_transparent:
        return pixels
    try:
        canonical = bytearray(pixels)
    except MemoryError:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.limit_memory")
    for row_start in range(0, len(canonical), row_stride):
        _require_active(control)
        for offset in range(row_start, row_start + row_stride, 4):
            if canonical[offset + 3] == 0:
                canonical[offset : offset + 3] = b"\x00\x00\x00"
    try:
        return bytes(canonical)
    except MemoryError:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.limit_memory")


def _write_exact(
    destination: BinaryIO,
    pixels: bytes,
    row_stride: int,
    control: ImageNormalizationControl,
) -> str:
    digest = hashlib.sha256()
    view = memoryview(pixels)
    offset = 0
    try:
        while offset < len(pixels):
            _require_active(control)
            end = min(offset + row_stride, len(pixels))
            row = bytes(view[offset:end])
            count = destination.write(row)
            if type(count) is not int or not 1 <= count <= end - offset:
                reject_image_page(ImagePageErrorCode.DESTINATION_FAILED, "image.destination_write")
            digest.update(view[offset : offset + count])
            offset += count
        _require_active(control)
        destination.flush()
        _require_active(control)
    except ImagePageError:
        raise
    except Exception:
        reject_image_page(ImagePageErrorCode.DESTINATION_FAILED, "image.destination_write")
    return digest.hexdigest()


@contextmanager
def _serialized_decoder(control: ImageNormalizationControl) -> Iterator[None]:
    acquired = False
    while not acquired:
        _require_active(control)
        try:
            acquired = _DECODER_LOCK.acquire(timeout=_LOCK_POLL_SECONDS)
        except Exception:
            reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.decoder_lock")
    try:
        yield
    finally:
        try:
            _DECODER_LOCK.release()
        except Exception:
            reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.decoder_unlock")


@contextmanager
def _pillow_limits(
    limits: ImageNormalizationLimits,
    scan: _MetadataScan,
) -> Iterator[None]:
    previous_pixels = Image.MAX_IMAGE_PIXELS
    previous_truncated = ImageFile.LOAD_TRUNCATED_IMAGES
    previous_text_chunk = PngImagePlugin.MAX_TEXT_CHUNK
    previous_text_memory = PngImagePlugin.MAX_TEXT_MEMORY
    Image.MAX_IMAGE_PIXELS = limits.maximum_source_pixels
    ImageFile.LOAD_TRUNCATED_IMAGES = False
    # The structural walk has already bounded the complete encoded metadata
    # payload.  Do not let compressed PNG text expand beyond those charged bytes.
    text_memory_bytes = min(limits.maximum_metadata_bytes, scan.byte_count)
    PngImagePlugin.MAX_TEXT_CHUNK = min(text_memory_bytes, _PNG_MAX_SINGLE_TEXT_BYTES)
    PngImagePlugin.MAX_TEXT_MEMORY = text_memory_bytes
    try:
        yield
    finally:
        Image.MAX_IMAGE_PIXELS = previous_pixels
        ImageFile.LOAD_TRUNCATED_IMAGES = previous_truncated
        PngImagePlugin.MAX_TEXT_CHUNK = previous_text_chunk
        PngImagePlugin.MAX_TEXT_MEMORY = previous_text_memory


def _require_active(control: ImageNormalizationControl) -> None:
    try:
        cancellation = control.cancellation_requested()
    except ImagePageError:
        raise
    except Exception:
        reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.cancel_probe")
    if type(cancellation) is not bool:
        reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.cancel_probe")
    if cancellation:
        reject_image_page(ImagePageErrorCode.CANCELLED, "image.cancelled")
    try:
        expired = control.deadline_expired()
    except ImagePageError:
        raise
    except Exception:
        reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.deadline_probe")
    if type(expired) is not bool:
        reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.deadline_probe")
    if expired:
        reject_image_page(ImagePageErrorCode.TIMED_OUT, "image.deadline")


def _source_position(source: BinaryIO) -> int:
    try:
        position = source.tell()
    except Exception:
        reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "image.source_position")
    if type(position) is not int or position < 0:
        reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "image.source_position")
    return position


def _restore_source(source: BinaryIO, position: int) -> ImagePageError | None:
    try:
        restored = source.seek(position, os.SEEK_SET)
        if type(restored) is not int or restored != position:
            raise _SourceFault
    except BaseException:
        return ImagePageError(ImagePageErrorCode.SOURCE_INVALID, "image.source_restore")
    return None


def _seek_exact(source: BinaryIO, offset: int, context: str) -> None:
    try:
        actual = source.seek(offset, os.SEEK_SET)
    except Exception:
        reject_image_page(ImagePageErrorCode.SOURCE_INVALID, context)
    if type(actual) is not int or actual != offset:
        reject_image_page(ImagePageErrorCode.SOURCE_INVALID, context)


def _read(source: BinaryIO, size: int) -> bytes:
    if size < 1:
        return b""
    try:
        payload = source.read(size)
    except Exception:
        reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "image.source_read")
    if type(payload) is not bytes or len(payload) > size:
        reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "image.source_read")
    return payload


def _read_exact(source: BinaryIO, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = _read(source, remaining)
        if not chunk:
            reject_image_page(ImagePageErrorCode.MALFORMED, "image.source_truncated")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _discard_exact(source: BinaryIO, size: int, control: ImageNormalizationControl) -> None:
    remaining = size
    while remaining:
        _require_active(control)
        chunk = _read_exact(source, min(remaining, _SCAN_BLOCK_BYTES))
        remaining -= len(chunk)


def _require_record_budget(records: int, limits: ImageNormalizationLimits) -> None:
    if records > limits.maximum_metadata_records:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.limit_metadata_records")


def _require_metadata_budget(total: int, limits: ImageNormalizationLimits) -> None:
    if total > limits.maximum_metadata_bytes:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.limit_metadata_bytes")


def _pillow_format(source_format: ImageSourceFormat) -> str:
    return "JPEG" if source_format is ImageSourceFormat.JPEG else "PNG"


def _source_format(image: Image.Image) -> ImageSourceFormat:
    value = image.format
    if value == "JPEG":
        return ImageSourceFormat.JPEG
    if value == "PNG":
        return ImageSourceFormat.PNG
    reject_image_page(ImagePageErrorCode.FORMAT_MISMATCH, "image.source_format")


def _frame_count(image: Image.Image) -> int:
    try:
        count = getattr(image, "n_frames", 1)
    except EOFError, OSError, SyntaxError, ValueError:
        reject_image_page(ImagePageErrorCode.MALFORMED, "image.metadata_frames")
    if type(count) is not int or count < 1:
        reject_image_page(ImagePageErrorCode.MALFORMED, "image.metadata_frames")
    return count


def _image_size(image: Image.Image) -> tuple[int, int]:
    try:
        width, height = image.size
    except Exception:
        reject_image_page(ImagePageErrorCode.GEOMETRY_INVALID, "image.source_dimensions")
    if type(width) is not int or type(height) is not int or width < 1 or height < 1:
        reject_image_page(ImagePageErrorCode.GEOMETRY_INVALID, "image.source_dimensions")
    return width, height


def _map_decoder_failure(error: BaseException, context: str) -> None:
    if isinstance(error, MemoryError):
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.limit_memory")
    reject_image_page(ImagePageErrorCode.DEPENDENCY_FAILED, context)

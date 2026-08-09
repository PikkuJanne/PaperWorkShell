"""Bounded content sniffing over one authenticated read-only source lease."""

from __future__ import annotations

import os
import re
import unicodedata
from binascii import crc32
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath
from typing import Final, Protocol, Self

from paperworkshell.domain.import_precheck import (
    DeclaredMediaType,
    DetectedMediaType,
    FeatureCheckState,
    ImportLimits,
    ImportPrecheckError,
    ImportPrecheckErrorCode,
    ImportPrecheckResult,
    ImportPrecheckWarningCode,
    ImportPreliminaryState,
    PdfEncryptionState,
    PendingImportCheck,
    PreliminaryImageInfo,
    reject_import,
)
from paperworkshell.storage._source_windows import (
    AuthenticatedSourceHandle,
    SourceFileInfo,
    SourceHandleFailure,
    is_fixed_local_drive,
    open_authenticated_source,
)
from paperworkshell.storage._windows import FileIdentity

_PDF_SIGNATURE: Final = bytes((0x25, 0x50, 0x44, 0x46, 0x2D))
_PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE: Final = b"\xff\xd8"
_PNG_IEND: Final = b"\x00\x00\x00\x00IEND\xaeB`\x82"
_PNG_IHDR_LENGTH: Final = 13
_PNG_HEADER_BYTES: Final = 33
_JPEG_TAIL_BYTES: Final = 64
_PDF_HEADER_BYTES: Final = 9
_MAXIMUM_HEADER_READ_OPERATIONS: Final = 1_024
_SUPPORTED_PDF_VERSIONS: Final = frozenset(
    {*(f"1.{minor}".encode("ascii") for minor in range(8)), b"2.0"}
)

_JPEG_SOF_MARKERS: Final = frozenset(
    {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
)
_JPEG_STANDALONE_MARKERS: Final = frozenset({0x01, *range(0xD0, 0xD8)})
_PNG_COLOR_DEPTHS: Final = {
    0: frozenset({1, 2, 4, 8, 16}),
    2: frozenset({8, 16}),
    3: frozenset({1, 2, 4, 8}),
    4: frozenset({8, 16}),
    6: frozenset({8, 16}),
}
_PNG_SOURCE_COMPONENTS: Final = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
_PNG_EXPANDED_COMPONENTS: Final = {0: 2, 2: 4, 3: 4, 4: 2, 6: 4}
_ADAM7_PASSES: Final = (
    (0, 0, 8, 8),
    (4, 0, 8, 8),
    (0, 4, 4, 8),
    (2, 0, 4, 4),
    (0, 2, 2, 4),
    (1, 0, 2, 2),
    (0, 1, 1, 2),
)

_WINDOWS_FORBIDDEN_CHARACTERS: Final = frozenset('<>:"/\\|?*')
_MAXIMUM_SOURCE_PATH_COMPONENTS: Final = 64
_MAXIMUM_WINDOWS_PATH_UNITS: Final = 32_767
_WINDOWS_RESERVED_STEMS: Final = frozenset(
    {
        "aux",
        "clock$",
        "con",
        "conin$",
        "conout$",
        "nul",
        "prn",
        *(f"com{number}" for number in range(1, 10)),
        *(f"lpt{number}" for number in range(1, 10)),
        "com¹",
        "com²",
        "com³",
        "lpt¹",
        "lpt²",
        "lpt³",
    }
)
_UNSUPPORTED_SOURCE_ATTRIBUTES: Final = 0x0000_1000 | 0x0004_0000 | 0x0040_0000


class BoundedBinarySource(Protocol):
    """Path-free random-access input used by pure preliminary parsing."""

    @property
    def byte_size(self) -> int:
        """Return the authenticated logical size."""

    def read_at(self, offset: int, count: int) -> bytes:
        """Return exactly one caller-bounded byte range."""


class _BudgetedReader:
    __slots__ = ("_byte_size", "_consumed", "_maximum", "_operations", "_source")

    def __init__(self, source: BoundedBinarySource, maximum: int, byte_size: int) -> None:
        self._source = source
        self._maximum = maximum
        self._byte_size = byte_size
        self._consumed = 0
        self._operations = 0

    @property
    def byte_size(self) -> int:
        return self._byte_size

    @property
    def remaining(self) -> int:
        return self._maximum - self._consumed

    def read_exact(self, offset: int, count: int) -> bytes:
        if type(offset) is not int or type(count) is not int or offset < 0 or count < 0:
            _reject_malformed("header.range", "The selected file has an invalid header range.")
        if count > self.remaining:
            reject_import(
                ImportPrecheckErrorCode.HEADER_LIMIT_EXCEEDED,
                ImportPreliminaryState.LIMIT_EXCEEDED,
                "limits.header",
                "The selected file exceeds the bounded header-inspection budget.",
            )
        if self._operations >= _MAXIMUM_HEADER_READ_OPERATIONS:
            reject_import(
                ImportPrecheckErrorCode.HEADER_LIMIT_EXCEEDED,
                ImportPreliminaryState.LIMIT_EXCEEDED,
                "limits.header_operations",
                "The selected file exceeds the bounded preliminary read-operation limit.",
            )
        if offset > self.byte_size - count:
            reject_import(
                ImportPrecheckErrorCode.HEADER_TRUNCATED,
                ImportPreliminaryState.MALFORMED,
                "header.truncated",
                "The selected file ends before its preliminary header is complete.",
            )
        self._consumed += count
        self._operations += 1
        try:
            value = self._source.read_at(offset, count)
        except ImportPrecheckError:
            raise
        except Exception:
            reject_import(
                ImportPrecheckErrorCode.SOURCE_READ_FAILED,
                ImportPreliminaryState.SOURCE_UNAVAILABLE,
                "source.read",
                "PaperWorkShell could not read the selected file safely.",
            )
        if type(value) is not bytes or len(value) != count:
            reject_import(
                ImportPrecheckErrorCode.SOURCE_CHANGED,
                ImportPreliminaryState.SOURCE_UNAVAILABLE,
                "source.short_read",
                "The selected file changed while it was being checked.",
            )
        return value


class PrecheckedSource:
    """A live same-handle source lease suitable for later PWS-0104 streaming."""

    __slots__ = ("_expected_path", "_handle", "_result")

    def __init__(self, handle: AuthenticatedSourceHandle, expected_path: Path) -> None:
        self._handle = handle
        self._expected_path = expected_path
        self._result: ImportPrecheckResult | None = None

    @property
    def byte_size(self) -> int:
        return self._handle.initial_info.logical_size

    @property
    def identity(self) -> FileIdentity:
        """Return the live handle identity for a later same-lease ingest."""

        return self._handle.initial_info.identity

    @property
    def result(self) -> ImportPrecheckResult:
        """Return the bounded result after successful precheck dispatch."""

        if self._result is None:
            reject_import(
                ImportPrecheckErrorCode.SOURCE_CHANGED,
                ImportPreliminaryState.SOURCE_UNAVAILABLE,
                "source.result",
                "The selected file does not have a completed preliminary check.",
            )
        return self._result

    @property
    def is_open(self) -> bool:
        return self._handle.is_open

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        # Ownership belongs to WindowsSourcePrechecker.open so terminal verification runs first.
        return None

    def __repr__(self) -> str:
        result = self._result
        if result is None:
            return "PrecheckedSource(state='prechecking')"
        return (
            "PrecheckedSource("
            f"media_type={result.media_type.value!r}, byte_size={result.byte_size}, "
            f"state={result.preliminary_state.value!r})"
        )

    def read_at(self, offset: int, count: int) -> bytes:
        """Read from the pinned source without reopening its path."""

        try:
            return self._handle.read_at(offset, count)
        except SourceHandleFailure as error:
            code = (
                ImportPrecheckErrorCode.SOURCE_CHANGED
                if error.operation in {"source.closed", "source.short_read"}
                else ImportPrecheckErrorCode.SOURCE_READ_FAILED
            )
            reject_import(
                code,
                ImportPreliminaryState.SOURCE_UNAVAILABLE,
                "source.read",
                "PaperWorkShell could not read the selected file safely.",
            )
        except Exception:
            reject_import(
                ImportPrecheckErrorCode.SOURCE_READ_FAILED,
                ImportPreliminaryState.SOURCE_UNAVAILABLE,
                "source.read",
                "PaperWorkShell could not read the selected file safely.",
            )

    def verify_unchanged(self) -> None:
        """Requery terminal handle facts before a caller may publish any later result."""

        try:
            current = self._handle.current_info()
        except Exception:
            reject_import(
                ImportPrecheckErrorCode.SOURCE_CHANGED,
                ImportPreliminaryState.SOURCE_UNAVAILABLE,
                "source.identity",
                "The selected file changed while it was being checked.",
            )
        initial = self._handle.initial_info
        if (
            current != initial
            or _normalized(current.final_path) != _normalized(self._expected_path)
            or current.link_count != 1
            or current.delete_pending
            or current.is_directory
            or current.is_reparse_point
        ):
            reject_import(
                ImportPrecheckErrorCode.SOURCE_CHANGED,
                ImportPreliminaryState.SOURCE_UNAVAILABLE,
                "source.identity",
                "The selected file changed while it was being checked.",
            )

    def _bind_result(self, result: ImportPrecheckResult) -> None:
        self._result = result


class WindowsSourcePrechecker:
    """Authenticate one fixed-local Windows file and keep its prechecked handle live."""

    def __init__(self, limits: ImportLimits) -> None:
        self._limits = limits

    @contextmanager
    def open(self, source_path: Path) -> Iterator[PrecheckedSource]:
        """Yield one live lease; a returned result alone never authorizes later ingest."""

        validated_path = _validate_source_path(source_path)
        try:
            if not is_fixed_local_drive(validated_path):
                _reject_location()
        except Exception:
            _reject_location()
        try:
            handle = open_authenticated_source(validated_path)
        except SourceHandleFailure as error:
            _reject_source_open(error)
        except Exception:
            reject_import(
                ImportPrecheckErrorCode.SOURCE_UNAVAILABLE,
                ImportPreliminaryState.SOURCE_UNAVAILABLE,
                "source.open",
                "Make sure the selected file is available and unchanged, then try again.",
            )
        primary_error: BaseException | None = None
        try:
            _require_supported_source(handle.initial_info, validated_path)
            source = PrecheckedSource(handle, validated_path)
            result = inspect_bounded_source(
                source,
                _declared_media_type(validated_path),
                self._limits,
            )
            source._bind_result(result)
            yield source
            source.verify_unchanged()
        except BaseException as error:
            primary_error = error
            raise
        finally:
            try:
                handle.close()
            except Exception:
                if primary_error is None:
                    reject_import(
                        ImportPrecheckErrorCode.SOURCE_READ_FAILED,
                        ImportPreliminaryState.SOURCE_UNAVAILABLE,
                        "source.close",
                        "PaperWorkShell could not finish checking the selected file safely.",
                    )


def inspect_bounded_source(
    source: BoundedBinarySource,
    declared_media_type: DeclaredMediaType,
    limits: ImportLimits,
) -> ImportPrecheckResult:
    """Apply deterministic bounded signature/header checks to a path-free source."""

    try:
        byte_size = source.byte_size
    except ImportPrecheckError:
        raise
    except Exception:
        reject_import(
            ImportPrecheckErrorCode.SOURCE_READ_FAILED,
            ImportPreliminaryState.SOURCE_UNAVAILABLE,
            "source.size",
            "PaperWorkShell could not inspect the selected file size safely.",
        )
    if type(byte_size) is not int or byte_size < 0:
        _reject_malformed("source.size", "The selected file has an invalid byte size.")
    if byte_size == 0:
        reject_import(
            ImportPrecheckErrorCode.SOURCE_EMPTY,
            ImportPreliminaryState.MALFORMED,
            "source.size",
            "The selected file is empty.",
        )
    if byte_size > limits.maximum_source_bytes:
        reject_import(
            ImportPrecheckErrorCode.SOURCE_TOO_LARGE,
            ImportPreliminaryState.LIMIT_EXCEEDED,
            "limits.source_bytes",
            "The selected file exceeds the configured byte limit.",
        )
    reader = _BudgetedReader(source, limits.maximum_header_bytes, byte_size)
    prefix = reader.read_exact(0, min(_PDF_HEADER_BYTES, byte_size))
    media_type = _detect_media_type(prefix)
    _require_matching_extension(media_type, declared_media_type)
    if media_type is DetectedMediaType.PDF:
        return _inspect_pdf(reader, prefix)
    if media_type is DetectedMediaType.PNG:
        image_info = _inspect_png(reader, limits)
    else:
        image_info = _inspect_jpeg(reader, limits)
    warnings = _require_image_limits(image_info, byte_size, limits)
    return ImportPrecheckResult(
        media_type=media_type,
        byte_size=byte_size,
        page_count=1,
        preliminary_state=ImportPreliminaryState.NEEDS_WORKER_INSPECTION,
        encryption_state=PdfEncryptionState.NOT_APPLICABLE,
        signature_state=FeatureCheckState.NOT_APPLICABLE,
        image_info=image_info,
        pending_checks=(PendingImportCheck.IMAGE_DECODE, PendingImportCheck.IMAGE_METADATA),
        warnings=warnings,
    )


def _detect_media_type(prefix: bytes) -> DetectedMediaType:
    if prefix.startswith(_PDF_SIGNATURE):
        if len(prefix) < _PDF_HEADER_BYTES:
            _reject_truncated()
        return DetectedMediaType.PDF
    if prefix.startswith(_PNG_SIGNATURE):
        return DetectedMediaType.PNG
    if prefix.startswith(_JPEG_SIGNATURE):
        return DetectedMediaType.JPEG
    if any(
        signature.startswith(prefix)
        for signature in (_PDF_SIGNATURE, _PNG_SIGNATURE, _JPEG_SIGNATURE)
        if prefix
    ):
        _reject_truncated()
    reject_import(
        ImportPrecheckErrorCode.MEDIA_UNSUPPORTED,
        ImportPreliminaryState.UNSUPPORTED,
        "header.signature",
        "Select a PDF, JPEG, or PNG file whose content matches a supported format.",
    )


def _require_matching_extension(
    detected: DetectedMediaType,
    declared: DeclaredMediaType,
) -> None:
    expected = {
        DetectedMediaType.PDF: DeclaredMediaType.PDF,
        DetectedMediaType.JPEG: DeclaredMediaType.JPEG,
        DetectedMediaType.PNG: DeclaredMediaType.PNG,
    }[detected]
    if declared not in {DeclaredMediaType.NONE, expected}:
        reject_import(
            ImportPrecheckErrorCode.EXTENSION_MISMATCH,
            ImportPreliminaryState.UNSUPPORTED,
            "header.extension",
            "Rename or select a file whose extension matches its detected content.",
        )


def _inspect_pdf(reader: _BudgetedReader, prefix: bytes) -> ImportPrecheckResult:
    if (
        len(prefix) < _PDF_HEADER_BYTES
        or prefix[:5] != _PDF_SIGNATURE
        or prefix[5:8] not in _SUPPORTED_PDF_VERSIONS
        or prefix[8:9] not in {b"\r", b"\n"}
    ):
        _reject_malformed("header.pdf", "The selected PDF has an invalid version header.")
    tail_count = min(reader.byte_size, reader.remaining)
    if tail_count < 6:
        reject_import(
            ImportPrecheckErrorCode.HEADER_LIMIT_EXCEEDED,
            ImportPreliminaryState.LIMIT_EXCEEDED,
            "limits.header",
            "The selected PDF exceeds the bounded framing-check budget.",
        )
    tail = reader.read_exact(reader.byte_size - tail_count, tail_count)
    stripped_tail = tail.rstrip(b"\x00\t\n\x0c\r ")
    marker_offset = len(stripped_tail) - len(b"%%EOF")
    if (
        marker_offset < 1
        or not stripped_tail.endswith(b"%%EOF")
        or stripped_tail[marker_offset - 1 : marker_offset] not in {b"\r", b"\n"}
    ):
        _reject_malformed("header.pdf_eof", "The selected PDF has incomplete trailing framing.")
    return ImportPrecheckResult(
        media_type=DetectedMediaType.PDF,
        byte_size=reader.byte_size,
        page_count=None,
        preliminary_state=ImportPreliminaryState.NEEDS_WORKER_INSPECTION,
        encryption_state=PdfEncryptionState.NOT_CHECKED,
        signature_state=FeatureCheckState.NOT_CHECKED,
        image_info=None,
        pending_checks=(
            PendingImportCheck.PDF_STRUCTURE,
            PendingImportCheck.PDF_PAGE_COUNT,
            PendingImportCheck.PDF_ENCRYPTION,
            PendingImportCheck.PDF_SIGNATURE,
        ),
        warnings=(),
    )


def _inspect_png(reader: _BudgetedReader, limits: ImportLimits) -> PreliminaryImageInfo:
    header = reader.read_exact(0, _PNG_HEADER_BYTES)
    if header[:8] != _PNG_SIGNATURE:
        _reject_malformed("header.png_signature", "The selected PNG signature is invalid.")
    length = int.from_bytes(header[8:12], "big")
    chunk_type = header[12:16]
    ihdr = header[16:29]
    expected_crc = int.from_bytes(header[29:33], "big")
    if length != _PNG_IHDR_LENGTH or chunk_type != b"IHDR":
        _reject_malformed("header.png_ihdr", "The selected PNG has an invalid first chunk.")
    if crc32(chunk_type + ihdr) & 0xFFFF_FFFF != expected_crc:
        _reject_malformed("header.png_crc", "The selected PNG header checksum is invalid.")
    width = int.from_bytes(ihdr[0:4], "big")
    height = int.from_bytes(ihdr[4:8], "big")
    bit_depth = ihdr[8]
    color_type = ihdr[9]
    compression = ihdr[10]
    filter_method = ihdr[11]
    interlace_method = ihdr[12]
    if width == 0 or height == 0 or width > 0x7FFF_FFFF or height > 0x7FFF_FFFF:
        _reject_malformed("header.png_dimensions", "The selected PNG has empty dimensions.")
    if color_type not in _PNG_COLOR_DEPTHS or bit_depth not in _PNG_COLOR_DEPTHS[color_type]:
        _reject_malformed("header.png_color", "The selected PNG has an unsupported color header.")
    if compression != 0 or filter_method != 0 or interlace_method not in {0, 1}:
        _reject_malformed("header.png_methods", "The selected PNG has unsupported header methods.")
    if reader.read_exact(reader.byte_size - len(_PNG_IEND), len(_PNG_IEND)) != _PNG_IEND:
        _reject_malformed("header.png_iend", "The selected PNG has incomplete trailing framing.")
    source_components = _PNG_SOURCE_COMPONENTS[color_type]
    filtered_bytes = _png_filtered_bytes(
        width,
        height,
        source_components * bit_depth,
        interlace_method == 1,
    )
    expanded_components = _PNG_EXPANDED_COMPONENTS[color_type]
    expanded_depth = max(8, bit_depth) if color_type in {0, 3} else bit_depth
    expanded_bytes = width * height * expanded_components * max(1, (expanded_depth + 7) // 8)
    return PreliminaryImageInfo(
        width=width,
        height=height,
        pixel_count=width * height,
        estimated_decompressed_bytes=max(filtered_bytes, expanded_bytes),
        component_count=expanded_components,
        bits_per_component=expanded_depth,
        interlaced=interlace_method == 1,
    )


def _inspect_jpeg(reader: _BudgetedReader, limits: ImportLimits) -> PreliminaryImageInfo:
    if reader.read_exact(0, 2) != _JPEG_SIGNATURE:
        _reject_malformed("header.jpeg_signature", "The selected JPEG signature is invalid.")
    offset = 2
    records = 0
    frame_info: PreliminaryImageInfo | None = None
    frame_component_ids: frozenset[int] = frozenset()
    while True:
        if records >= limits.maximum_structure_records:
            reject_import(
                ImportPrecheckErrorCode.HEADER_LIMIT_EXCEEDED,
                ImportPreliminaryState.LIMIT_EXCEEDED,
                "limits.structure_records",
                "The selected JPEG exceeds the bounded marker-inspection limit.",
            )
        if offset >= reader.byte_size:
            _reject_truncated()
        if offset >= limits.maximum_header_bytes:
            reject_import(
                ImportPrecheckErrorCode.HEADER_LIMIT_EXCEEDED,
                ImportPreliminaryState.LIMIT_EXCEEDED,
                "limits.header",
                "The selected JPEG dimensions are beyond the bounded header budget.",
            )
        if reader.read_exact(offset, 1) != b"\xff":
            _reject_malformed(
                "header.jpeg_marker", "The selected JPEG has an invalid marker sequence."
            )
        while True:
            offset += 1
            if offset >= reader.byte_size:
                _reject_truncated()
            if offset >= limits.maximum_header_bytes:
                reject_import(
                    ImportPrecheckErrorCode.HEADER_LIMIT_EXCEEDED,
                    ImportPreliminaryState.LIMIT_EXCEEDED,
                    "limits.header",
                    "The selected JPEG dimensions are beyond the bounded header budget.",
                )
            marker = reader.read_exact(offset, 1)[0]
            if marker != 0xFF:
                break
            records += 1
            if records >= limits.maximum_structure_records:
                reject_import(
                    ImportPrecheckErrorCode.HEADER_LIMIT_EXCEEDED,
                    ImportPreliminaryState.LIMIT_EXCEEDED,
                    "limits.structure_records",
                    "The selected JPEG exceeds the bounded marker-inspection limit.",
                )
        offset += 1
        records += 1
        if marker == 0x00 or marker == 0xD8:
            _reject_malformed(
                "header.jpeg_marker", "The selected JPEG has an invalid marker order."
            )
        if marker == 0xD9:
            context = "header.jpeg_dimensions" if frame_info is None else "header.jpeg_scan"
            _reject_malformed(context, "The selected JPEG ends before its image scan begins.")
        if marker in _JPEG_STANDALONE_MARKERS:
            continue
        segment_length = int.from_bytes(reader.read_exact(offset, 2), "big")
        if segment_length < 2:
            _reject_malformed(
                "header.jpeg_length", "The selected JPEG has an invalid segment length."
            )
        segment_end = offset + segment_length
        if segment_end > reader.byte_size:
            _reject_truncated()
        if segment_end > limits.maximum_header_bytes:
            reject_import(
                ImportPrecheckErrorCode.HEADER_LIMIT_EXCEEDED,
                ImportPreliminaryState.LIMIT_EXCEEDED,
                "limits.header",
                "The selected JPEG dimensions are beyond the bounded header budget.",
            )
        if marker == 0xDA:
            if frame_info is None:
                _reject_malformed(
                    "header.jpeg_scan", "The selected JPEG declares a scan before its frame."
                )
            assert frame_info is not None
            scan = reader.read_exact(offset + 2, segment_length - 2)
            scan_components = scan[0] if scan else 0
            if not 1 <= scan_components <= 4 or segment_length != 6 + 2 * scan_components:
                _reject_malformed(
                    "header.jpeg_scan", "The selected JPEG has an invalid scan header."
                )
            component_ids = scan[1 : 1 + 2 * scan_components : 2]
            selectors = scan[2 : 1 + 2 * scan_components : 2]
            if (
                len(set(component_ids)) != scan_components
                or not set(component_ids) <= frame_component_ids
                or any((selector >> 4) > 3 or (selector & 0x0F) > 3 for selector in selectors)
            ):
                _reject_malformed(
                    "header.jpeg_scan", "The selected JPEG has an invalid scan component header."
                )
            _require_jpeg_eoi(reader)
            return frame_info
        if marker in _JPEG_SOF_MARKERS:
            if frame_info is not None:
                _reject_malformed(
                    "header.jpeg_sof", "The selected JPEG declares more than one frame."
                )
            if segment_length < 8:
                _reject_malformed(
                    "header.jpeg_sof", "The selected JPEG has an incomplete frame header."
                )
            frame = reader.read_exact(offset + 2, segment_length - 2)
            precision = frame[0]
            height = int.from_bytes(frame[1:3], "big")
            width = int.from_bytes(frame[3:5], "big")
            components = frame[5]
            if (
                not _valid_jpeg_precision(marker, precision)
                or width == 0
                or not 1 <= components <= 4
                or segment_length != 8 + 3 * components
            ):
                _reject_malformed(
                    "header.jpeg_sof", "The selected JPEG has an invalid frame header."
                )
            if height == 0:
                reject_import(
                    ImportPrecheckErrorCode.IMAGE_DIMENSIONS_UNAVAILABLE,
                    ImportPreliminaryState.UNSUPPORTED,
                    "header.jpeg_dnl",
                    "Select a JPEG whose dimensions are declared in its frame header.",
                )
            component_records = frame[6:]
            component_ids = component_records[0::3]
            sampling_values = component_records[1::3]
            table_selectors = component_records[2::3]
            if (
                len(set(component_ids)) != components
                or any(
                    not 1 <= sampling >> 4 <= 4 or not 1 <= sampling & 0x0F <= 4
                    for sampling in sampling_values
                )
                or any(selector > 3 for selector in table_selectors)
            ):
                _reject_malformed(
                    "header.jpeg_components",
                    "The selected JPEG has an invalid frame component header.",
                )
            frame_component_ids = frozenset(component_ids)
            frame_info = PreliminaryImageInfo(
                width=width,
                height=height,
                pixel_count=width * height,
                estimated_decompressed_bytes=(width * height * components * ((precision + 7) // 8)),
                component_count=components,
                bits_per_component=precision,
                interlaced=False,
            )
        offset = segment_end


def _valid_jpeg_precision(marker: int, precision: int) -> bool:
    if marker == 0xC0:
        return precision == 8
    if marker in {0xC1, 0xC2, 0xC5, 0xC6, 0xC9, 0xCA, 0xCD, 0xCE}:
        return precision in {8, 12}
    return 2 <= precision <= 16


def _require_jpeg_eoi(reader: _BudgetedReader) -> None:
    tail_count = min(_JPEG_TAIL_BYTES, reader.byte_size, reader.remaining)
    if tail_count < 2:
        reject_import(
            ImportPrecheckErrorCode.HEADER_LIMIT_EXCEEDED,
            ImportPreliminaryState.LIMIT_EXCEEDED,
            "limits.header",
            "The selected JPEG exceeds the bounded framing-check budget.",
        )
    tail = reader.read_exact(reader.byte_size - tail_count, tail_count)
    if not tail.rstrip(b"\x00\t\n\x0c\r ").endswith(b"\xff\xd9"):
        _reject_malformed("header.jpeg_eoi", "The selected JPEG has incomplete trailing framing.")


def _require_image_limits(
    image: PreliminaryImageInfo,
    source_bytes: int,
    limits: ImportLimits,
) -> tuple[ImportPrecheckWarningCode, ...]:
    if image.width > limits.maximum_image_width or image.height > limits.maximum_image_height:
        reject_import(
            ImportPrecheckErrorCode.IMAGE_DIMENSION_LIMIT_EXCEEDED,
            ImportPreliminaryState.LIMIT_EXCEEDED,
            "limits.image_dimensions",
            "The selected image exceeds the configured width or height limit.",
        )
    if image.pixel_count > limits.maximum_image_pixels:
        reject_import(
            ImportPrecheckErrorCode.IMAGE_PIXEL_LIMIT_EXCEEDED,
            ImportPreliminaryState.LIMIT_EXCEEDED,
            "limits.image_pixels",
            "The selected image exceeds the configured total-pixel limit.",
        )
    if (
        image.estimated_decompressed_bytes > limits.maximum_decompressed_bytes
        or image.estimated_decompressed_bytes > source_bytes * limits.maximum_decompression_ratio
    ):
        reject_import(
            ImportPrecheckErrorCode.DECOMPRESSION_LIMIT_EXCEEDED,
            ImportPreliminaryState.LIMIT_EXCEEDED,
            "limits.decompression",
            "The selected image exceeds the configured decompression limit.",
        )
    if image.estimated_decompressed_bytes > source_bytes * limits.decompression_warning_ratio:
        return (ImportPrecheckWarningCode.DECOMPRESSION_NEEDS_REVIEW,)
    return ()


def _png_filtered_bytes(width: int, height: int, bits_per_pixel: int, interlaced: bool) -> int:
    if not interlaced:
        return height * (1 + _ceil_div(width * bits_per_pixel, 8))
    total = 0
    for start_x, start_y, step_x, step_y in _ADAM7_PASSES:
        pass_width = _pass_extent(width, start_x, step_x)
        pass_height = _pass_extent(height, start_y, step_y)
        if pass_width and pass_height:
            total += pass_height * (1 + _ceil_div(pass_width * bits_per_pixel, 8))
    return total


def _pass_extent(size: int, start: int, step: int) -> int:
    return 0 if size <= start else 1 + (size - start - 1) // step


def _ceil_div(value: int, divisor: int) -> int:
    return (value + divisor - 1) // divisor


def _declared_media_type(path: Path) -> DeclaredMediaType:
    suffix = path.suffix.casefold()
    if not suffix:
        return DeclaredMediaType.NONE
    if suffix == ".pdf":
        return DeclaredMediaType.PDF
    if suffix in {".jpg", ".jpeg"}:
        return DeclaredMediaType.JPEG
    if suffix == ".png":
        return DeclaredMediaType.PNG
    return DeclaredMediaType.OTHER


def _validate_source_path(value: Path) -> Path:
    raw = os.fspath(value)
    if type(raw) is not str or not raw:
        _reject_path()
    try:
        path_units = len(raw.encode("utf-16-le")) // 2
    except UnicodeEncodeError:
        _reject_path()
    if path_units > _MAXIMUM_WINDOWS_PATH_UNITS:
        _reject_path()
    windows_text = raw.replace("/", "\\")
    lowered = windows_text.casefold()
    if (
        "\x00" in windows_text
        or windows_text.startswith("\\\\")
        or lowered.startswith(("\\\\?\\", "\\\\.\\", "\\??\\"))
        or "globalroot" in lowered.split("\\")
    ):
        _reject_location()
    parsed = PureWindowsPath(windows_text)
    if (
        not parsed.is_absolute()
        or not re.fullmatch(r"[A-Za-z]:", parsed.drive)
        or parsed.root != "\\"
        or ":" in windows_text[2:]
    ):
        _reject_path()
    components = windows_text[3:].split("\\") if len(windows_text) > 3 else []
    if (
        not components
        or len(components) > _MAXIMUM_SOURCE_PATH_COMPONENTS
        or any(component in {"", ".", ".."} for component in components)
    ):
        _reject_path()
    for component in components:
        if (
            component.endswith((".", " "))
            or any(character in _WINDOWS_FORBIDDEN_CHARACTERS for character in component)
            or any(
                ord(character) < 32 or unicodedata.category(character) in {"Cc", "Cf", "Cs"}
                for character in component
            )
            or component.partition(".")[0].casefold() in _WINDOWS_RESERVED_STEMS
            or len(component.encode("utf-16-le")) // 2 > 240
        ):
            _reject_path()
    return Path(os.path.abspath(value))


def _require_supported_source(info: SourceFileInfo, expected_path: Path) -> None:
    if info.is_directory:
        reject_import(
            ImportPrecheckErrorCode.SOURCE_NOT_REGULAR,
            ImportPreliminaryState.SOURCE_UNAVAILABLE,
            "source.type",
            "Select a regular local file rather than a directory or special object.",
        )
    if info.is_reparse_point:
        reject_import(
            ImportPrecheckErrorCode.SOURCE_REPARSE_POINT,
            ImportPreliminaryState.SOURCE_UNAVAILABLE,
            "source.reparse",
            "Linked or redirected source files are not supported.",
        )
    if (
        info.link_count != 1
        or info.delete_pending
        or _normalized(info.final_path) != _normalized(expected_path)
    ):
        reject_import(
            ImportPrecheckErrorCode.SOURCE_ALIAS_DETECTED,
            ImportPreliminaryState.SOURCE_UNAVAILABLE,
            "source.identity",
            "Select a source file that does not alias or redirect another object.",
        )
    if info.file_attributes & _UNSUPPORTED_SOURCE_ATTRIBUTES:
        _reject_location()


def _normalized(value: Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(value)))


def _reject_path() -> None:
    reject_import(
        ImportPrecheckErrorCode.SOURCE_PATH_INVALID,
        ImportPreliminaryState.SOURCE_UNAVAILABLE,
        "source.path",
        "Select a bounded drive-qualified local file path.",
    )


def _reject_location() -> None:
    reject_import(
        ImportPrecheckErrorCode.SOURCE_LOCATION_UNSUPPORTED,
        ImportPreliminaryState.SOURCE_UNAVAILABLE,
        "source.location",
        "Select a file stored on a supported fixed local drive.",
    )


def _reject_source_open(error: SourceHandleFailure) -> None:
    if error.operation == "source.ancestor_reparse":
        reject_import(
            ImportPrecheckErrorCode.SOURCE_REPARSE_POINT,
            ImportPreliminaryState.SOURCE_UNAVAILABLE,
            "source.ancestor_reparse",
            "Linked or redirected source locations are not supported.",
        )
    if error.operation in {"source.ancestor_alias", "source.ancestor_changed"}:
        reject_import(
            ImportPrecheckErrorCode.SOURCE_ALIAS_DETECTED,
            ImportPreliminaryState.SOURCE_UNAVAILABLE,
            "source.ancestor_identity",
            "Select a source file that does not alias or redirect another object.",
        )
    if error.operation == "source.ancestor_location":
        _reject_location()
    if error.operation == "source.ancestor_type":
        _reject_path()
    reject_import(
        ImportPrecheckErrorCode.SOURCE_UNAVAILABLE,
        ImportPreliminaryState.SOURCE_UNAVAILABLE,
        "source.open",
        "Make sure the selected file is available and unchanged, then try again.",
    )


def _reject_truncated() -> None:
    reject_import(
        ImportPrecheckErrorCode.HEADER_TRUNCATED,
        ImportPreliminaryState.MALFORMED,
        "header.truncated",
        "The selected file ends before its preliminary header is complete.",
    )


def _reject_malformed(context: str, safe_message: str) -> None:
    reject_import(
        ImportPrecheckErrorCode.HEADER_MALFORMED,
        ImportPreliminaryState.MALFORMED,
        context,
        safe_message,
    )

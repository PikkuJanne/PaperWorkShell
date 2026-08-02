"""Pure PWS-0103 media sniffing and resource-policy tests."""

from __future__ import annotations

import struct
from binascii import crc32
from dataclasses import FrozenInstanceError

import pytest

from paperworkshell.domain.import_precheck import (
    MAX_SAFE_IMPORT_INTEGER,
    MAXIMUM_HEADER_BUDGET,
    MAXIMUM_STRUCTURE_RECORDS,
    DeclaredMediaType,
    DetectedMediaType,
    FeatureCheckState,
    ImportLimits,
    ImportPrecheckError,
    ImportPrecheckErrorCode,
    ImportPrecheckWarningCode,
    ImportPreliminaryState,
    PdfEncryptionState,
    PendingImportCheck,
    require_observed_page_count,
)
from paperworkshell.storage.source_precheck import inspect_bounded_source


class BytesSource:
    def __init__(self, value: bytes) -> None:
        self.value = value
        self.reads: list[tuple[int, int]] = []

    @property
    def byte_size(self) -> int:
        return len(self.value)

    def read_at(self, offset: int, count: int) -> bytes:
        self.reads.append((offset, count))
        return self.value[offset : offset + count]


def _limits(**overrides: int) -> ImportLimits:
    values = {
        "maximum_source_bytes": 1_000_000,
        "maximum_pdf_pages": 100,
        "maximum_image_width": 10_000,
        "maximum_image_height": 10_000,
        "maximum_image_pixels": 50_000_000,
        "maximum_decompressed_bytes": 200_000_000,
        "decompression_warning_ratio": 100,
        "maximum_decompression_ratio": 1_000,
        "maximum_header_bytes": 4_096,
        "maximum_structure_records": 64,
    }
    values.update(overrides)
    return ImportLimits(**values)


def _pdf(*, version: bytes = b"1.7", body: bytes = b"1 0 obj\n<<>>\nendobj\n") -> bytes:
    return b"%PDF-" + version + b"\n" + body + b"startxref\n0\n%%EOF\n"


def _png(
    *,
    width: int = 16,
    height: int = 8,
    bit_depth: int = 8,
    color_type: int = 6,
    compression: int = 0,
    filter_method: int = 0,
    interlace: int = 0,
    corrupt_crc: bool = False,
) -> bytes:
    ihdr = struct.pack(
        ">IIBBBBB",
        width,
        height,
        bit_depth,
        color_type,
        compression,
        filter_method,
        interlace,
    )
    checksum = crc32(b"IHDR" + ihdr) & 0xFFFF_FFFF
    if corrupt_crc:
        checksum ^= 1
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", len(ihdr))
        + b"IHDR"
        + ihdr
        + struct.pack(">I", checksum)
        + b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def _jpeg(
    *,
    width: int = 24,
    height: int = 12,
    precision: int = 8,
    components: int = 3,
    sof_marker: int = 0xC0,
    app_payload: bytes = b"",
    include_eoi: bool = True,
) -> bytes:
    app = b""
    if app_payload:
        app = b"\xff\xe0" + struct.pack(">H", len(app_payload) + 2) + app_payload
    component_records = b"".join(bytes((index + 1, 0x11, 0)) for index in range(components))
    frame = bytes((precision,)) + struct.pack(">HHB", height, width, components) + component_records
    scan_components = b"".join(bytes((index + 1, 0)) for index in range(components))
    scan = bytes((components,)) + scan_components + b"\x00\x3f\x00"
    result = (
        b"\xff\xd8"
        + app
        + b"\xff"
        + bytes((sof_marker,))
        + struct.pack(">H", len(frame) + 2)
        + frame
        + b"\xff\xda"
        + struct.pack(">H", len(scan) + 2)
        + scan
    )
    return result + (b"\xff\xd9" if include_eoi else b"")


def _invalid_jpeg_frames() -> list[bytes]:
    baseline = bytearray(_jpeg())
    invalid: list[bytes] = []
    for offset, replacement in ((6, 12), (13, 0x51), (14, 4), (15, 1)):
        changed = baseline.copy()
        changed[offset] = replacement
        invalid.append(bytes(changed))
    overlong = baseline.copy()
    scan_offset = overlong.index(b"\xff\xda")
    frame_length = int.from_bytes(overlong[4:6], "big") + 1
    overlong[4:6] = frame_length.to_bytes(2, "big")
    overlong[scan_offset:scan_offset] = b"\x00"
    invalid.append(bytes(overlong))
    return invalid


@pytest.mark.parametrize(
    ("value", "declared", "expected", "page_count"),
    [
        (_pdf(), DeclaredMediaType.PDF, DetectedMediaType.PDF, None),
        (_jpeg(), DeclaredMediaType.JPEG, DetectedMediaType.JPEG, 1),
        (_png(), DeclaredMediaType.PNG, DetectedMediaType.PNG, 1),
        (_pdf(), DeclaredMediaType.NONE, DetectedMediaType.PDF, None),
        (_jpeg(), DeclaredMediaType.NONE, DetectedMediaType.JPEG, 1),
        (_png(), DeclaredMediaType.NONE, DetectedMediaType.PNG, 1),
    ],
)
def test_supported_content_drives_media_type_and_missing_suffix_is_allowed(
    value: bytes,
    declared: DeclaredMediaType,
    expected: DetectedMediaType,
    page_count: int | None,
) -> None:
    result = inspect_bounded_source(BytesSource(value), declared, _limits())

    assert result.media_type is expected
    assert result.page_count == page_count
    assert result.preliminary_state is ImportPreliminaryState.NEEDS_WORKER_INSPECTION
    assert result.precheck_version == 1


def test_pdf_precheck_never_guesses_page_encryption_signature_or_structure() -> None:
    hostile_tokens = b"/Count 999 /Type /Page /Encrypt <<>> /Sig"
    result = inspect_bounded_source(
        BytesSource(_pdf(body=hostile_tokens)),
        DeclaredMediaType.PDF,
        _limits(maximum_pdf_pages=1),
    )

    assert result.page_count is None
    assert result.encryption_state is PdfEncryptionState.NOT_CHECKED
    assert result.signature_state is FeatureCheckState.NOT_CHECKED
    assert result.image_info is None
    assert result.pending_checks == (
        PendingImportCheck.PDF_STRUCTURE,
        PendingImportCheck.PDF_PAGE_COUNT,
        PendingImportCheck.PDF_ENCRYPTION,
        PendingImportCheck.PDF_SIGNATURE,
    )
    assert {state.value for state in PdfEncryptionState} == {
        "not_checked",
        "encrypted",
        "unencrypted",
        "not_applicable",
    }


@pytest.mark.parametrize(
    ("value", "declared"),
    [
        (_pdf(), DeclaredMediaType.JPEG),
        (_pdf(), DeclaredMediaType.PNG),
        (_pdf(), DeclaredMediaType.OTHER),
        (_jpeg(), DeclaredMediaType.PDF),
        (_jpeg(), DeclaredMediaType.PNG),
        (_png(), DeclaredMediaType.PDF),
        (_png(), DeclaredMediaType.JPEG),
    ],
)
def test_declared_extension_cannot_override_detected_content(
    value: bytes,
    declared: DeclaredMediaType,
) -> None:
    with pytest.raises(ImportPrecheckError) as raised:
        inspect_bounded_source(BytesSource(value), declared, _limits())

    assert raised.value.code is ImportPrecheckErrorCode.EXTENSION_MISMATCH
    assert raised.value.state is ImportPreliminaryState.UNSUPPORTED


@pytest.mark.parametrize(
    "value",
    [
        b"MZ" + _pdf(),
        b"\xef\xbb\xbf" + _pdf(),
        b"plain text %PDF-1.7 %%EOF",
        b"GIF89a",
        b"PK\x03\x04",
        b"\x00" * 32,
    ],
)
def test_embedded_or_unsupported_signatures_are_never_admitted(value: bytes) -> None:
    with pytest.raises(ImportPrecheckError) as raised:
        inspect_bounded_source(BytesSource(value), DeclaredMediaType.NONE, _limits())

    assert raised.value.code is ImportPrecheckErrorCode.MEDIA_UNSUPPORTED


@pytest.mark.parametrize("length", range(1, len(b"%PDF-") + 1))
def test_partial_pdf_signature_is_truncated(length: int) -> None:
    with pytest.raises(ImportPrecheckError) as raised:
        inspect_bounded_source(BytesSource(b"%PDF-"[:length]), DeclaredMediaType.NONE, _limits())

    assert raised.value.code is ImportPrecheckErrorCode.HEADER_TRUNCATED


@pytest.mark.parametrize("version", [*(f"1.{minor}".encode() for minor in range(8)), b"2.0"])
def test_defined_pdf_versions_are_accepted(version: bytes) -> None:
    result = inspect_bounded_source(
        BytesSource(_pdf(version=version)),
        DeclaredMediaType.PDF,
        _limits(),
    )

    assert result.media_type is DetectedMediaType.PDF


@pytest.mark.parametrize(
    "version",
    [b"0.9", b"1.8", b"1.9", b"2.1", b"2.9", b"3.0", b"1-x", b"1.a", b"11.7"],
)
def test_invalid_pdf_version_header_is_malformed(version: bytes) -> None:
    with pytest.raises(ImportPrecheckError) as raised:
        inspect_bounded_source(BytesSource(_pdf(version=version)), DeclaredMediaType.PDF, _limits())

    assert raised.value.code is ImportPrecheckErrorCode.HEADER_MALFORMED


@pytest.mark.parametrize(
    "value",
    [
        b"%PDF-1.7X\ngarbage\n%%EOF\n",
        b"%PDF-1.7\ngarbage%%EOF\n",
        b"%PDF-1.7\ngarbage\n %%EOF\n",
    ],
)
def test_pdf_header_and_eof_markers_must_occupy_defined_lines(value: bytes) -> None:
    with pytest.raises(ImportPrecheckError) as raised:
        inspect_bounded_source(BytesSource(value), DeclaredMediaType.PDF, _limits())

    assert raised.value.code is ImportPrecheckErrorCode.HEADER_MALFORMED


def test_pdf_missing_trailing_eof_is_preliminarily_malformed() -> None:
    with pytest.raises(ImportPrecheckError) as raised:
        inspect_bounded_source(
            BytesSource(b"%PDF-1.7\n1 0 obj\n<<>>"),
            DeclaredMediaType.PDF,
            _limits(),
        )

    assert raised.value.code is ImportPrecheckErrorCode.HEADER_MALFORMED
    assert raised.value.context == "header.pdf_eof"


@pytest.mark.parametrize(
    "value",
    [
        *(b"\x89PNG\r\n\x1a\n"[:length] for length in range(1, 8)),
        b"\xff",
    ],
)
def test_partial_png_and_jpeg_signatures_are_truncated(value: bytes) -> None:
    with pytest.raises(ImportPrecheckError) as raised:
        inspect_bounded_source(BytesSource(value), DeclaredMediaType.NONE, _limits())

    assert raised.value.code is ImportPrecheckErrorCode.HEADER_TRUNCATED


@pytest.mark.parametrize("length", range(8, 33))
def test_png_header_truncation_is_distinct_from_unsupported_content(length: int) -> None:
    with pytest.raises(ImportPrecheckError) as raised:
        inspect_bounded_source(
            BytesSource(_png()[:length]),
            DeclaredMediaType.PNG,
            _limits(),
        )

    assert raised.value.code is ImportPrecheckErrorCode.HEADER_TRUNCATED


@pytest.mark.parametrize(
    ("bit_depth", "color_type"),
    [
        (1, 0),
        (2, 0),
        (4, 0),
        (8, 0),
        (16, 0),
        (8, 2),
        (16, 2),
        (1, 3),
        (2, 3),
        (4, 3),
        (8, 3),
        (8, 4),
        (16, 4),
        (8, 6),
        (16, 6),
    ],
)
def test_valid_png_color_depth_combinations_are_bounded(
    bit_depth: int,
    color_type: int,
) -> None:
    result = inspect_bounded_source(
        BytesSource(_png(bit_depth=bit_depth, color_type=color_type)),
        DeclaredMediaType.PNG,
        _limits(),
    )

    assert result.image_info is not None
    assert result.image_info.pixel_count == 128
    assert result.image_info.estimated_decompressed_bytes > 0


@pytest.mark.parametrize(
    "value",
    [
        _png(corrupt_crc=True),
        _png(width=0),
        _png(height=0),
        _png(bit_depth=4, color_type=2),
        _png(bit_depth=16, color_type=3),
        _png(compression=1),
        _png(filter_method=1),
        _png(interlace=2),
    ],
)
def test_invalid_png_ihdr_values_fail_without_decompression(value: bytes) -> None:
    with pytest.raises(ImportPrecheckError) as raised:
        inspect_bounded_source(BytesSource(value), DeclaredMediaType.PNG, _limits())

    assert raised.value.code is ImportPrecheckErrorCode.HEADER_MALFORMED


def test_png_requires_complete_iend_framing() -> None:
    with pytest.raises(ImportPrecheckError) as raised:
        inspect_bounded_source(
            BytesSource(_png()[:-12]),
            DeclaredMediaType.PNG,
            _limits(),
        )

    assert raised.value.code is ImportPrecheckErrorCode.HEADER_MALFORMED
    assert raised.value.context == "header.png_iend"


@pytest.mark.parametrize("dimension", [0x8000_0000, 0xFFFF_FFFF])
def test_png_dimensions_above_the_format_maximum_are_malformed(dimension: int) -> None:
    with pytest.raises(ImportPrecheckError) as raised:
        inspect_bounded_source(
            BytesSource(_png(width=dimension)),
            DeclaredMediaType.PNG,
            _limits(maximum_image_width=MAX_SAFE_IMPORT_INTEGER),
        )

    assert raised.value.code is ImportPrecheckErrorCode.HEADER_MALFORMED


def test_png_format_maximum_dimension_is_syntactically_accepted() -> None:
    dimension = 0x7FFF_FFFF
    result = inspect_bounded_source(
        BytesSource(_png(width=dimension, height=1)),
        DeclaredMediaType.PNG,
        _limits(
            maximum_image_width=dimension,
            maximum_image_pixels=dimension,
            maximum_decompressed_bytes=dimension * 4 + 1,
            maximum_decompression_ratio=MAX_SAFE_IMPORT_INTEGER,
        ),
    )

    assert result.image_info is not None
    assert result.image_info.width == dimension


@pytest.mark.parametrize(
    ("color_type", "expected_components", "expected_bytes"),
    [(0, 2, 200), (2, 4, 400)],
)
def test_png_expansion_conservatively_allows_for_trns_alpha(
    color_type: int,
    expected_components: int,
    expected_bytes: int,
) -> None:
    result = inspect_bounded_source(
        BytesSource(_png(width=10, height=10, color_type=color_type)),
        DeclaredMediaType.PNG,
        _limits(),
    )

    assert result.image_info is not None
    assert result.image_info.component_count == expected_components
    assert result.image_info.estimated_decompressed_bytes == expected_bytes


def test_adam7_expansion_is_deterministic_and_larger_than_zero() -> None:
    result = inspect_bounded_source(
        BytesSource(_png(width=17, height=15, color_type=6, interlace=1)),
        DeclaredMediaType.PNG,
        _limits(),
    )

    assert result.image_info is not None
    assert result.image_info.interlaced
    assert result.image_info.estimated_decompressed_bytes == 1_049


@pytest.mark.parametrize("sof_marker", [0xC0, 0xC2])
def test_jpeg_marker_scan_skips_app_payload_and_accepts_bounded_sof(
    sof_marker: int,
) -> None:
    result = inspect_bounded_source(
        BytesSource(_jpeg(sof_marker=sof_marker, app_payload=b"synthetic")),
        DeclaredMediaType.JPEG,
        _limits(),
    )

    assert result.image_info is not None
    assert result.image_info.width == 24
    assert result.image_info.height == 12
    assert result.image_info.component_count == 3
    assert result.image_info.estimated_decompressed_bytes == 864


def test_progressive_jpeg_allows_defined_twelve_bit_precision() -> None:
    result = inspect_bounded_source(
        BytesSource(_jpeg(sof_marker=0xC2, precision=12)),
        DeclaredMediaType.JPEG,
        _limits(),
    )

    assert result.image_info is not None
    assert result.image_info.bits_per_component == 12


@pytest.mark.parametrize("value", _invalid_jpeg_frames())
def test_jpeg_frame_grammar_rejects_invalid_precision_components_and_length(value: bytes) -> None:
    with pytest.raises(ImportPrecheckError) as raised:
        inspect_bounded_source(BytesSource(value), DeclaredMediaType.JPEG, _limits())

    assert raised.value.code is ImportPrecheckErrorCode.HEADER_MALFORMED


@pytest.mark.parametrize(
    "value",
    [
        b"\xff\xd8\xff\xd9",
        b"\xff\xd8\xff\xda\x00\x02\xff\xd9",
        b"\xff\xd8\xff\xe0\x00\x01\xff\xd9",
        b"\xff\xd8\xff\xe0\xff\xff",
        _jpeg(width=0),
        _jpeg(precision=0),
        _jpeg(components=0),
        _jpeg(include_eoi=False),
    ],
)
def test_malformed_or_truncated_jpeg_headers_fail_closed(value: bytes) -> None:
    with pytest.raises(ImportPrecheckError) as raised:
        inspect_bounded_source(BytesSource(value), DeclaredMediaType.JPEG, _limits())

    assert raised.value.code in {
        ImportPrecheckErrorCode.HEADER_MALFORMED,
        ImportPrecheckErrorCode.HEADER_TRUNCATED,
    }


def test_jpeg_dnl_dimensions_are_unsupported_not_misreported_as_malformed() -> None:
    with pytest.raises(ImportPrecheckError) as raised:
        inspect_bounded_source(
            BytesSource(_jpeg(height=0)),
            DeclaredMediaType.JPEG,
            _limits(),
        )

    assert raised.value.code is ImportPrecheckErrorCode.IMAGE_DIMENSIONS_UNAVAILABLE
    assert raised.value.state is ImportPreliminaryState.UNSUPPORTED


@pytest.mark.parametrize("value", [b"\xff\xd8", b"\xff\xd8\xff\x01"])
def test_jpeg_natural_eof_is_truncated_not_a_resource_limit(value: bytes) -> None:
    with pytest.raises(ImportPrecheckError) as raised:
        inspect_bounded_source(BytesSource(value), DeclaredMediaType.JPEG, _limits())

    assert raised.value.code is ImportPrecheckErrorCode.HEADER_TRUNCATED


def test_jpeg_requires_a_scan_header_after_the_frame() -> None:
    value = _jpeg()
    scan_offset = value.index(b"\xff\xda")
    without_scan = value[:scan_offset] + b"\xff\xd9"

    with pytest.raises(ImportPrecheckError) as raised:
        inspect_bounded_source(BytesSource(without_scan), DeclaredMediaType.JPEG, _limits())

    assert raised.value.code is ImportPrecheckErrorCode.HEADER_MALFORMED
    assert raised.value.context == "header.jpeg_scan"


def test_jpeg_structure_record_limit_stops_marker_bomb() -> None:
    markers = b"\xff\x01" * 8
    value = b"\xff\xd8" + markers + _jpeg()[2:]

    with pytest.raises(ImportPrecheckError) as raised:
        inspect_bounded_source(
            BytesSource(value),
            DeclaredMediaType.JPEG,
            _limits(maximum_structure_records=4),
        )

    assert raised.value.code is ImportPrecheckErrorCode.HEADER_LIMIT_EXCEEDED
    assert raised.value.context == "limits.structure_records"


def test_jpeg_fill_bytes_consume_structure_and_operation_budgets() -> None:
    source = BytesSource(b"\xff\xd8" + b"\xff" * 10_000 + b"\xc0")

    with pytest.raises(ImportPrecheckError) as raised:
        inspect_bounded_source(
            source,
            DeclaredMediaType.JPEG,
            _limits(maximum_structure_records=64),
        )

    assert raised.value.code is ImportPrecheckErrorCode.HEADER_LIMIT_EXCEEDED
    assert raised.value.context == "limits.structure_records"
    assert len(source.reads) <= 70


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"maximum_image_width": 23}, ImportPrecheckErrorCode.IMAGE_DIMENSION_LIMIT_EXCEEDED),
        ({"maximum_image_height": 11}, ImportPrecheckErrorCode.IMAGE_DIMENSION_LIMIT_EXCEEDED),
        ({"maximum_image_pixels": 287}, ImportPrecheckErrorCode.IMAGE_PIXEL_LIMIT_EXCEEDED),
        (
            {"maximum_decompressed_bytes": 863},
            ImportPrecheckErrorCode.DECOMPRESSION_LIMIT_EXCEEDED,
        ),
        (
            {"maximum_decompression_ratio": 1, "decompression_warning_ratio": 1},
            ImportPrecheckErrorCode.DECOMPRESSION_LIMIT_EXCEEDED,
        ),
    ],
)
def test_image_limits_are_inclusive_and_fail_at_limit_plus_one(
    overrides: dict[str, int],
    expected: ImportPrecheckErrorCode,
) -> None:
    with pytest.raises(ImportPrecheckError) as raised:
        inspect_bounded_source(BytesSource(_jpeg()), DeclaredMediaType.JPEG, _limits(**overrides))

    assert raised.value.code is expected


def test_image_and_structure_limits_accept_exact_boundaries() -> None:
    value = _jpeg(app_payload=b"x" * 31)
    source = BytesSource(value)
    result = inspect_bounded_source(
        source,
        DeclaredMediaType.JPEG,
        _limits(
            maximum_source_bytes=len(value),
            maximum_image_width=24,
            maximum_image_height=12,
            maximum_image_pixels=288,
            maximum_decompressed_bytes=864,
            decompression_warning_ratio=12,
            maximum_decompression_ratio=12,
            maximum_structure_records=3,
        ),
    )

    assert result.image_info is not None
    assert result.image_info.estimated_decompressed_bytes == 864
    assert result.warnings == ()


def test_jpeg_structure_record_limit_is_inclusive() -> None:
    result = inspect_bounded_source(
        BytesSource(_jpeg()),
        DeclaredMediaType.JPEG,
        _limits(maximum_structure_records=2),
    )
    assert result.media_type is DetectedMediaType.JPEG

    with pytest.raises(ImportPrecheckError) as raised:
        inspect_bounded_source(
            BytesSource(_jpeg()),
            DeclaredMediaType.JPEG,
            _limits(maximum_structure_records=1),
        )
    assert raised.value.code is ImportPrecheckErrorCode.HEADER_LIMIT_EXCEEDED


def test_decompression_ratio_warning_is_typed_and_nonblocking() -> None:
    value = _jpeg(width=100, height=100)
    result = inspect_bounded_source(
        BytesSource(value),
        DeclaredMediaType.JPEG,
        _limits(decompression_warning_ratio=1, maximum_decompression_ratio=2_000),
    )

    assert result.warnings == (ImportPrecheckWarningCode.DECOMPRESSION_NEEDS_REVIEW,)


def test_oversized_source_fails_before_any_content_read() -> None:
    source = BytesSource(_pdf())

    with pytest.raises(ImportPrecheckError) as raised:
        inspect_bounded_source(
            source,
            DeclaredMediaType.PDF,
            _limits(maximum_source_bytes=len(source.value) - 1),
        )

    assert raised.value.code is ImportPrecheckErrorCode.SOURCE_TOO_LARGE
    assert source.reads == []


def test_every_content_read_is_bounded_by_the_configured_header_budget() -> None:
    source = BytesSource(_jpeg(app_payload=b"x" * 128))
    limits = _limits(maximum_header_bytes=2_048)

    inspect_bounded_source(source, DeclaredMediaType.JPEG, limits)

    assert source.reads
    assert all(0 <= count <= limits.maximum_header_bytes for _, count in source.reads)
    assert sum(count for _, count in source.reads) <= limits.maximum_header_bytes


@pytest.mark.parametrize(
    "field",
    [
        "maximum_source_bytes",
        "maximum_pdf_pages",
        "maximum_image_width",
        "maximum_image_height",
        "maximum_image_pixels",
        "maximum_decompressed_bytes",
        "decompression_warning_ratio",
        "maximum_decompression_ratio",
        "maximum_header_bytes",
        "maximum_structure_records",
    ],
)
@pytest.mark.parametrize("invalid", [False, 0, -1, MAX_SAFE_IMPORT_INTEGER + 1])
def test_limit_configuration_rejects_nonpositive_boolean_or_unbounded_values(
    field: str,
    invalid: int,
) -> None:
    overrides = {field: invalid}

    with pytest.raises(ImportPrecheckError) as raised:
        _limits(**overrides)

    assert raised.value.code is ImportPrecheckErrorCode.LIMIT_INVALID


def test_limit_configuration_rejects_incoherent_warning_and_tiny_header_budget() -> None:
    for overrides in (
        {"decompression_warning_ratio": 11, "maximum_decompression_ratio": 10},
        {"maximum_header_bytes": 2_047},
    ):
        with pytest.raises(ImportPrecheckError) as raised:
            _limits(**overrides)

        assert raised.value.code is ImportPrecheckErrorCode.LIMIT_INVALID


def test_header_budget_accepts_adapter_cap_and_rejects_cap_plus_one() -> None:
    assert _limits(maximum_header_bytes=MAXIMUM_HEADER_BUDGET).maximum_header_bytes == (
        MAXIMUM_HEADER_BUDGET
    )

    with pytest.raises(ImportPrecheckError) as raised:
        _limits(maximum_header_bytes=MAXIMUM_HEADER_BUDGET + 1)

    assert raised.value.code is ImportPrecheckErrorCode.LIMIT_INVALID


def test_structure_record_budget_accepts_hard_cap_and_rejects_cap_plus_one() -> None:
    assert _limits(
        maximum_structure_records=MAXIMUM_STRUCTURE_RECORDS
    ).maximum_structure_records == (MAXIMUM_STRUCTURE_RECORDS)

    with pytest.raises(ImportPrecheckError) as raised:
        _limits(maximum_structure_records=MAXIMUM_STRUCTURE_RECORDS + 1)

    assert raised.value.code is ImportPrecheckErrorCode.LIMIT_INVALID


def test_later_page_observation_uses_same_inclusive_policy() -> None:
    limits = _limits(maximum_pdf_pages=2)
    require_observed_page_count(limits, 1)
    require_observed_page_count(limits, 2)

    with pytest.raises(ImportPrecheckError) as raised:
        require_observed_page_count(limits, 3)

    assert raised.value.code is ImportPrecheckErrorCode.PAGE_LIMIT_EXCEEDED


@pytest.mark.parametrize("invalid", [False, 0, -1])
def test_invalid_later_page_observation_is_malformed(invalid: int) -> None:
    with pytest.raises(ImportPrecheckError) as raised:
        require_observed_page_count(_limits(), invalid)

    assert raised.value.code is ImportPrecheckErrorCode.HEADER_MALFORMED


def test_results_are_immutable_and_errors_are_sanitized() -> None:
    result = inspect_bounded_source(BytesSource(_png()), DeclaredMediaType.PNG, _limits())
    with pytest.raises(FrozenInstanceError):
        result.byte_size = 0  # type: ignore[misc]

    prohibited = "private-source-name.exe"
    with pytest.raises(ImportPrecheckError) as raised:
        inspect_bounded_source(
            BytesSource(prohibited.encode("ascii")),
            DeclaredMediaType.OTHER,
            _limits(),
        )

    rendered = f"{raised.value!s} {raised.value!r}"
    assert prohibited not in rendered
    assert "C:\\" not in rendered
    assert raised.value.code is ImportPrecheckErrorCode.MEDIA_UNSUPPORTED

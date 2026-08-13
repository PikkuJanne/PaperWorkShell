from __future__ import annotations

import hashlib
import zlib
from collections.abc import Buffer
from io import BytesIO
from typing import Never, cast
from unittest.mock import patch

import pytest
from PIL import Image, PngImagePlugin

from paperworkshell.domain.image_pages import (
    ImageExifOrientation,
    ImageNormalizationLimits,
    ImageNormalizationRequest,
    ImageNormalizationResult,
    ImagePageError,
    ImagePageErrorCode,
    ImageQuarterTurn,
    ImageSourceColorModel,
    ImageSourceFormat,
)
from paperworkshell.imaging import pillow_decoder
from paperworkshell.imaging.pillow_decoder import PillowImageDecoder


class _Control:
    def __init__(self, *, cancel_after: int | None = None, expire_after: int | None = None) -> None:
        self.cancel_after = cancel_after
        self.expire_after = expire_after
        self.cancel_checks = 0
        self.deadline_checks = 0

    def cancellation_requested(self) -> bool:
        self.cancel_checks += 1
        return self.cancel_after is not None and self.cancel_checks >= self.cancel_after

    def deadline_expired(self) -> bool:
        self.deadline_checks += 1
        return self.expire_after is not None and self.deadline_checks >= self.expire_after


def _limits(**changes: int) -> ImageNormalizationLimits:
    values = {
        "maximum_source_bytes": 1_000_000,
        "maximum_encoded_width_pixels": 256,
        "maximum_encoded_height_pixels": 256,
        "maximum_source_pixels": 65_536,
        "maximum_decompressed_bytes": 262_144,
        "maximum_metadata_records": 256,
        "maximum_metadata_bytes": 65_536,
        "maximum_output_width_pixels": 256,
        "maximum_output_height_pixels": 256,
        "maximum_output_pixels": 65_536,
        "maximum_output_bytes": 262_144,
        "maximum_memory_bytes": 524_288,
        "wall_time_ms": 10_000,
    }
    values.update(changes)
    return ImageNormalizationLimits(**values)


def _encode(
    mode: str = "RGB",
    size: tuple[int, int] = (3, 2),
    *,
    format: str = "PNG",
    orientation: int | None = None,
    transparency: int | tuple[int, int, int] | None = None,
    pnginfo: PngImagePlugin.PngInfo | None = None,
    progressive: bool = False,
    bits: int | None = None,
) -> bytes:
    if mode == "P":
        image = Image.new(mode, size)
        image.putpalette([255, 0, 0, 0, 255, 0] + [0] * 762)
    else:
        color: int | tuple[int, ...]
        color = 128 if mode in {"1", "L", "I;16"} else tuple([32] * len(mode))
        image = Image.new(mode, size, color)
    exif = Image.Exif()
    if orientation is not None:
        exif[274] = orientation
    sink = BytesIO()
    save_options: dict[str, object] = {}
    if orientation is not None:
        save_options["exif"] = exif
    if transparency is not None:
        save_options["transparency"] = transparency
    if pnginfo is not None:
        save_options["pnginfo"] = pnginfo
    if progressive:
        save_options["progressive"] = True
    if bits is not None:
        save_options["bits"] = bits
    image.save(sink, format=format, **save_options)
    image.close()
    return sink.getvalue()


def _jpeg_segment(marker: int, payload: bytes) -> bytes:
    return b"\xff" + bytes((marker,)) + (len(payload) + 2).to_bytes(2, "big") + payload


def _png_chunk(chunk_type: bytes, payload: bytes = b"") -> bytes:
    checksum = zlib.crc32(payload, zlib.crc32(chunk_type)) & 0xFFFF_FFFF
    return len(payload).to_bytes(4, "big") + chunk_type + payload + checksum.to_bytes(4, "big")


def _insert_png_chunk_before_iend(source: bytes, chunk: bytes) -> bytes:
    iend = source.rfind(b"IEND") - 4
    assert iend >= 8
    return source[:iend] + chunk + source[iend:]


def _replace_first_png_chunk(
    source: bytes,
    chunk_type: bytes,
    replacement_payload: bytes | None,
) -> bytes:
    assert source.startswith(b"\x89PNG\r\n\x1a\n")
    output = bytearray(source[:8])
    offset = 8
    replaced = False
    while offset < len(source):
        length = int.from_bytes(source[offset : offset + 4], "big")
        record_end = offset + 12 + length
        assert record_end <= len(source)
        current_type = source[offset + 4 : offset + 8]
        if not replaced and current_type == chunk_type:
            replaced = True
            if replacement_payload is not None:
                output.extend(_png_chunk(chunk_type, replacement_payload))
        else:
            output.extend(source[offset:record_end])
        offset = record_end
    assert offset == len(source) and replaced
    return bytes(output)


def _insert_png_chunk_after(
    source: bytes,
    preceding_type: bytes,
    chunk: bytes,
) -> bytes:
    assert source.startswith(b"\x89PNG\r\n\x1a\n")
    offset = 8
    while offset < len(source):
        length = int.from_bytes(source[offset : offset + 4], "big")
        record_end = offset + 12 + length
        assert record_end <= len(source)
        if source[offset + 4 : offset + 8] == preceding_type:
            return source[:record_end] + chunk + source[record_end:]
        offset = record_end
    raise AssertionError("preceding PNG chunk is unavailable")


def _first_inter_scan_marker(payload: bytes) -> tuple[int, int]:
    """Return the first marker boundary after scan data and the prior record count."""

    assert payload[:2] == b"\xff\xd8"
    offset = 2
    records_before_entropy = 0
    while offset < len(payload):
        assert payload[offset] == 0xFF
        offset += 1
        while payload[offset] == 0xFF:
            offset += 1
        marker = payload[offset]
        offset += 1
        records_before_entropy += 1
        assert marker not in {*range(0xD0, 0xDA), 0x01, 0x00}
        segment_length = int.from_bytes(payload[offset : offset + 2], "big")
        assert segment_length >= 2
        offset += segment_length
        if marker == 0xDA:
            break

    saw_prefix = False
    marker_start = -1
    while offset < len(payload):
        value = payload[offset]
        if not saw_prefix:
            if value == 0xFF:
                saw_prefix = True
                marker_start = offset
            offset += 1
            continue
        if value == 0xFF:
            offset += 1
            continue
        saw_prefix = False
        offset += 1
        if value == 0x00 or value == 0x01 or 0xD0 <= value <= 0xD7:
            continue
        assert value != 0xD9, "fixture must contain more than one JPEG scan"
        return marker_start, records_before_entropy
    raise AssertionError("fixture has no inter-scan marker")


def _normalize(
    payload: bytes,
    format: ImageSourceFormat,
    *,
    rotation: ImageQuarterTurn = ImageQuarterTurn.DEGREES_0,
    maximum_size: tuple[int, int] = (256, 256),
    limits: ImageNormalizationLimits | None = None,
    control: _Control | None = None,
) -> tuple[ImageNormalizationResult, bytes]:
    request = ImageNormalizationRequest(
        expected_source_format=format,
        expected_source_bytes=len(payload),
        expected_source_sha256=hashlib.sha256(payload).hexdigest(),
        maximum_width_pixels=maximum_size[0],
        maximum_height_pixels=maximum_size[1],
        additional_rotation=rotation,
    )
    sink = BytesIO()
    result = PillowImageDecoder().normalize(
        BytesIO(payload),
        sink,
        expected_format=format,
        request=request,
        limits=_limits() if limits is None else limits,
        control=_Control() if control is None else control,
    )
    return result, sink.getvalue()


@pytest.mark.parametrize("orientation", range(1, 9))
def test_all_exif_orientation_intents_are_applied_without_mutating_source(
    orientation: int,
) -> None:
    payload = _encode(format="JPEG", orientation=orientation)
    source = BytesIO(payload)
    source.seek(1)
    request = ImageNormalizationRequest(
        ImageSourceFormat.JPEG,
        len(payload),
        hashlib.sha256(payload).hexdigest(),
        256,
        256,
    )
    sink = BytesIO()

    result = PillowImageDecoder().normalize(
        source,
        sink,
        expected_format=ImageSourceFormat.JPEG,
        request=request,
        limits=_limits(),
        control=_Control(),
    )

    expected = ImageExifOrientation(orientation)
    assert source.tell() == 1
    assert source.getvalue() == payload
    assert result.metadata.exif_orientation is expected
    assert (result.metadata.oriented_width_pixels, result.metadata.oriented_height_pixels) == (
        (2, 3) if expected.swaps_axes else (3, 2)
    )
    assert len(sink.getvalue()) == result.bytes_written
    assert set(sink.getvalue()[3::4]) == {255}


@pytest.mark.parametrize(
    ("payload", "format", "model", "has_alpha"),
    (
        (_encode("CMYK", format="JPEG"), ImageSourceFormat.JPEG, ImageSourceColorModel.CMYK, False),
        (_encode("RGBA"), ImageSourceFormat.PNG, ImageSourceColorModel.RGBA, True),
        (_encode("LA"), ImageSourceFormat.PNG, ImageSourceColorModel.GRAYSCALE_ALPHA, True),
        (_encode("P"), ImageSourceFormat.PNG, ImageSourceColorModel.PALETTE, False),
        (
            _encode("P", transparency=0),
            ImageSourceFormat.PNG,
            ImageSourceColorModel.PALETTE_ALPHA,
            True,
        ),
        (
            _encode("L", transparency=128),
            ImageSourceFormat.PNG,
            ImageSourceColorModel.GRAYSCALE_TRANSPARENCY,
            True,
        ),
        (
            _encode("RGB", transparency=(32, 32, 32)),
            ImageSourceFormat.PNG,
            ImageSourceColorModel.RGB_TRANSPARENCY,
            True,
        ),
    ),
)
def test_color_and_alpha_models_normalize_predictably(
    payload: bytes,
    format: ImageSourceFormat,
    model: ImageSourceColorModel,
    has_alpha: bool,
) -> None:
    result, pixels = _normalize(payload, format)

    assert result.metadata.source_color_model is model
    assert result.metadata.has_alpha is has_alpha
    assert set(pixels[3::4]) == {255}


def test_unrelated_jpeg_app1_and_app2_are_not_reported_as_exif_or_icc() -> None:
    payload = _encode(format="JPEG")
    payload = (
        payload[:2]
        + _jpeg_segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00synthetic")
        + _jpeg_segment(0xE2, b"unrelated-profile")
        + payload[2:]
    )

    result, _pixels = _normalize(payload, ImageSourceFormat.JPEG)

    assert not result.metadata.has_exif
    assert not result.metadata.has_embedded_color_profile
    assert result.metadata.metadata_record_count >= 2


def test_png_rejects_duplicate_ihdr_before_decoder_access() -> None:
    original = _encode(format="PNG")
    ihdr = original[8:33]
    payload = original[:33] + ihdr + original[33:]

    with pytest.raises(ImagePageError) as raised:
        _normalize(payload, ImageSourceFormat.PNG)

    assert raised.value.code is ImagePageErrorCode.MALFORMED
    assert raised.value.context == "image.png_ihdr"


@pytest.mark.parametrize(
    ("chunk_type", "chunk_payload", "context"),
    (
        (b"PLTE", b"\x00\x00\x00", "image.png_plte"),
        (b"tRNS", b"\x00\x00\x00\x00\x00\x00", "image.png_transparency"),
    ),
)
def test_png_rejects_palette_or_transparency_chunks_after_image_data(
    chunk_type: bytes,
    chunk_payload: bytes,
    context: str,
) -> None:
    payload = _insert_png_chunk_before_iend(
        _encode(format="PNG"),
        _png_chunk(chunk_type, chunk_payload),
    )

    with pytest.raises(ImagePageError) as raised:
        _normalize(payload, ImageSourceFormat.PNG)

    assert raised.value.code is ImagePageErrorCode.MALFORMED
    assert raised.value.context == context


def test_png_rejects_noncontiguous_idat_chunks() -> None:
    payload = _insert_png_chunk_before_iend(
        _encode(format="PNG"),
        _png_chunk(b"tEXt", b"synthetic\x00value") + _png_chunk(b"IDAT"),
    )

    with pytest.raises(ImagePageError) as raised:
        _normalize(payload, ImageSourceFormat.PNG)

    assert raised.value.code is ImagePageErrorCode.MALFORMED
    assert raised.value.context == "image.png_idat"


@pytest.mark.parametrize(
    ("chunk_type", "code", "context"),
    (
        (b"ABCD", ImagePageErrorCode.UNSUPPORTED, "image.png_critical"),
        (b"1BAD", ImagePageErrorCode.MALFORMED, "image.png_chunk_type"),
        (b"abca", ImagePageErrorCode.MALFORMED, "image.png_chunk_type"),
    ),
)
def test_png_rejects_unknown_critical_or_invalid_chunk_types(
    chunk_type: bytes,
    code: ImagePageErrorCode,
    context: str,
) -> None:
    original = _encode(format="PNG")
    payload = original[:33] + _png_chunk(chunk_type) + original[33:]

    with pytest.raises(ImagePageError) as raised:
        _normalize(payload, ImageSourceFormat.PNG)

    assert raised.value.code is code
    assert raised.value.context == context


@pytest.mark.parametrize("palette", (b"", b"\x00" * 2, b"\x00" * 4, b"\x00" * 769))
def test_png_rejects_out_of_bounds_palette_payloads_before_decoder_access(
    palette: bytes,
) -> None:
    original = _encode("RGB")
    payload = original[:33] + _png_chunk(b"PLTE", palette) + original[33:]

    with pytest.raises(ImagePageError) as raised:
        _normalize(payload, ImageSourceFormat.PNG)

    assert raised.value.code is ImagePageErrorCode.MALFORMED
    assert raised.value.context == "image.png_plte"


@pytest.mark.parametrize("mode", ("L", "LA"))
def test_png_rejects_palette_for_color_types_that_forbid_it(mode: str) -> None:
    original = _encode(mode)
    payload = original[:33] + _png_chunk(b"PLTE", b"\x00\x00\x00") + original[33:]

    with pytest.raises(ImagePageError) as raised:
        _normalize(payload, ImageSourceFormat.PNG)

    assert raised.value.code is ImagePageErrorCode.MALFORMED
    assert raised.value.context == "image.png_plte"


def test_png_requires_a_bounded_palette_for_indexed_color() -> None:
    original = _encode("P", bits=1)
    without_palette = _replace_first_png_chunk(original, b"PLTE", None)
    too_many_entries = _replace_first_png_chunk(original, b"PLTE", b"\x00" * 9)

    for payload in (without_palette, too_many_entries):
        with pytest.raises(ImagePageError) as raised:
            _normalize(payload, ImageSourceFormat.PNG)
        assert raised.value.code is ImagePageErrorCode.MALFORMED
        assert raised.value.context == "image.png_plte"


def test_png_palette_payload_is_charged_to_metadata_and_memory_planning() -> None:
    payload = _encode("P", bits=1)

    result, _pixels = _normalize(payload, ImageSourceFormat.PNG)

    assert result.metadata.metadata_record_count >= 1
    assert result.metadata.metadata_byte_count >= 6
    with pytest.raises(ImagePageError) as raised:
        _normalize(
            payload,
            ImageSourceFormat.PNG,
            limits=_limits(maximum_metadata_bytes=5),
        )
    assert raised.value.code is ImagePageErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "image.limit_metadata_bytes"


@pytest.mark.parametrize(
    ("mode", "transparency"),
    (
        ("L", b"\x00"),
        ("L", b"\x00" * 3),
        ("RGB", b"\x00" * 5),
        ("RGB", b"\x00" * 7),
        ("LA", b"\x00" * 2),
        ("RGBA", b"\x00" * 6),
    ),
)
def test_png_rejects_transparency_payloads_outside_color_type_grammar(
    mode: str,
    transparency: bytes,
) -> None:
    original = _encode(mode)
    payload = original[:33] + _png_chunk(b"tRNS", transparency) + original[33:]

    with pytest.raises(ImagePageError) as raised:
        _normalize(payload, ImageSourceFormat.PNG)

    assert raised.value.code is ImagePageErrorCode.MALFORMED
    assert raised.value.context == "image.png_transparency"


def test_png_requires_indexed_transparency_after_and_within_the_palette() -> None:
    original = _encode("P", bits=1)
    before_palette = original[:33] + _png_chunk(b"tRNS", b"\x00") + original[33:]
    too_many_entries = _insert_png_chunk_after(
        original,
        b"PLTE",
        _png_chunk(b"tRNS", b"\x00" * 3),
    )

    for payload in (before_palette, too_many_entries):
        with pytest.raises(ImagePageError) as raised:
            _normalize(payload, ImageSourceFormat.PNG)
        assert raised.value.code is ImagePageErrorCode.MALFORMED
        assert raised.value.context == "image.png_transparency"


def test_progressive_jpeg_post_sos_metadata_is_counted_and_byte_bounded() -> None:
    original = _encode(size=(32, 32), format="JPEG", progressive=True)
    marker_offset, _records_before_entropy = _first_inter_scan_marker(original)
    comment = b"bounded-post-sos-comment"
    payload = original[:marker_offset] + _jpeg_segment(0xFE, comment) + original[marker_offset:]

    original_result, _original_pixels = _normalize(original, ImageSourceFormat.JPEG)
    result, _pixels = _normalize(payload, ImageSourceFormat.JPEG)

    assert (
        result.metadata.metadata_record_count == original_result.metadata.metadata_record_count + 1
    )
    assert (
        result.metadata.metadata_byte_count
        == original_result.metadata.metadata_byte_count + len(comment)
    )

    with pytest.raises(ImagePageError) as raised:
        _normalize(
            payload,
            ImageSourceFormat.JPEG,
            limits=_limits(
                maximum_metadata_bytes=result.metadata.metadata_byte_count - 1,
            ),
        )
    assert raised.value.code is ImagePageErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "image.limit_metadata_bytes"


def test_progressive_jpeg_post_sos_markers_use_structural_record_budget() -> None:
    payload = _encode(size=(32, 32), format="JPEG", progressive=True)
    _marker_offset, records_before_entropy = _first_inter_scan_marker(payload)

    with pytest.raises(ImagePageError) as raised:
        _normalize(
            payload,
            ImageSourceFormat.JPEG,
            limits=_limits(maximum_metadata_records=records_before_entropy),
        )

    assert raised.value.code is ImagePageErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "image.limit_metadata_records"


def test_progressive_jpeg_post_sos_segment_length_is_validated() -> None:
    original = _encode(size=(32, 32), format="JPEG", progressive=True)
    marker_offset, _records_before_entropy = _first_inter_scan_marker(original)
    payload = original[:marker_offset] + b"\xff\xfe\x00\x01" + original[marker_offset:]

    with pytest.raises(ImagePageError) as raised:
        _normalize(payload, ImageSourceFormat.JPEG)

    assert raised.value.code is ImagePageErrorCode.MALFORMED
    assert raised.value.context == "image.jpeg_segment"


def test_progressive_jpeg_entropy_preserves_stuffing_and_restart_markers() -> None:
    size = (64, 64)
    source_pixels = bytes(
        (index * 73 + index // 11 * 29) & 0xFF for index in range(size[0] * size[1] * 3)
    )
    image = Image.frombytes("RGB", size, source_pixels)
    sink = BytesIO()
    image.save(
        sink,
        format="JPEG",
        progressive=True,
        restart_marker_blocks=4,
        quality=85,
    )
    image.close()
    payload = sink.getvalue()

    assert b"\xff\x00" in payload
    assert any(b"\xff" + bytes((marker,)) in payload for marker in range(0xD0, 0xD8))

    result, pixels = _normalize(payload, ImageSourceFormat.JPEG)

    assert (result.width_pixels, result.height_pixels) == size
    assert len(pixels) == size[0] * size[1] * 4


def test_rotation_then_aspect_fit_uses_canonical_layout() -> None:
    result, pixels = _normalize(
        _encode(size=(40, 20)),
        ImageSourceFormat.PNG,
        rotation=ImageQuarterTurn.DEGREES_90,
        maximum_size=(10, 10),
    )

    assert (result.width_pixels, result.height_pixels) == (5, 10)
    assert len(pixels) == 200
    assert hashlib.sha256(pixels).hexdigest() == result.pixel_sha256


def test_noop_transforms_reuse_the_loaded_image_without_full_raster_copies() -> None:
    image = Image.new("RGB", (3, 2), (1, 2, 3))
    try:
        assert pillow_decoder._apply_exif_orientation(image, ImageExifOrientation.NORMAL) is image
        assert pillow_decoder._apply_quarter_turn(image, ImageQuarterTurn.DEGREES_0) is image
    finally:
        image.close()


class _TrackedImageCore:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_working_image_replacement_promptly_releases_only_superseded_core() -> None:
    original = _TrackedImageCore()
    replacement = _TrackedImageCore()
    original_image = cast(Image.Image, original)
    replacement_image = cast(Image.Image, replacement)

    selected = pillow_decoder._replace_image(original_image, replacement_image)

    assert selected is replacement_image
    assert original.closed
    assert not replacement.closed
    assert pillow_decoder._replace_image(replacement_image, replacement_image) is replacement_image
    assert not replacement.closed


def test_metadata_release_keeps_only_transparency_needed_for_pixel_conversion() -> None:
    image = Image.new("P", (1, 1))
    image.info.update(
        {
            "transparency": 0,
            "icc_profile": b"synthetic-profile",
            "comment": "synthetic-comment",
        }
    )
    image.text = {"synthetic": "metadata"}  # type: ignore[attr-defined]
    try:
        pillow_decoder._discard_unneeded_metadata(image)

        assert image.info == {"transparency": 0}
        assert image.text == {}  # type: ignore[attr-defined]
    finally:
        image.close()


@pytest.mark.parametrize(
    ("mutation", "code"),
    (
        (lambda payload: payload[:-12], ImagePageErrorCode.MALFORMED),
        (lambda payload: b"not-an-image", ImagePageErrorCode.FORMAT_MISMATCH),
    ),
)
def test_malformed_and_truncated_inputs_fail_closed(
    mutation: object, code: ImagePageErrorCode
) -> None:
    payload = mutation(_encode())  # type: ignore[operator]
    with pytest.raises(ImagePageError) as raised:
        _normalize(payload, ImageSourceFormat.PNG)
    assert raised.value.code is code
    assert raised.value.__context__ is None


def test_format_and_exact_identity_mismatches_are_rejected() -> None:
    payload = _encode()
    request = ImageNormalizationRequest(
        ImageSourceFormat.JPEG,
        len(payload),
        hashlib.sha256(payload).hexdigest(),
        256,
        256,
    )
    with pytest.raises(ImagePageError) as raised:
        PillowImageDecoder().normalize(
            BytesIO(payload),
            BytesIO(),
            expected_format=ImageSourceFormat.JPEG,
            request=request,
            limits=_limits(),
            control=_Control(),
        )
    assert raised.value.code is ImagePageErrorCode.FORMAT_MISMATCH

    changed = payload[:-1] + bytes((payload[-1] ^ 1,))
    request = ImageNormalizationRequest(
        ImageSourceFormat.PNG,
        len(changed),
        hashlib.sha256(payload).hexdigest(),
        256,
        256,
    )
    with pytest.raises(ImagePageError) as raised:
        PillowImageDecoder().normalize(
            BytesIO(changed),
            BytesIO(),
            expected_format=ImageSourceFormat.PNG,
            request=request,
            limits=_limits(),
            control=_Control(),
        )
    assert raised.value.code is ImagePageErrorCode.FORMAT_MISMATCH


@pytest.mark.parametrize(
    "limits",
    (
        _limits(maximum_encoded_width_pixels=2),
        _limits(maximum_source_pixels=5),
        _limits(maximum_decompressed_bytes=23),
        _limits(maximum_output_pixels=5),
        _limits(maximum_output_bytes=23),
        _limits(maximum_memory_bytes=47),
    ),
)
def test_geometry_decompression_output_and_memory_limits_are_enforced(
    limits: ImageNormalizationLimits,
) -> None:
    with pytest.raises(ImagePageError) as raised:
        _normalize(_encode(), ImageSourceFormat.PNG, limits=limits)
    assert raised.value.code is ImagePageErrorCode.LIMIT_EXCEEDED


def test_constrained_full_frame_peak_boundary_rejects_then_recovers_cleanly() -> None:
    payload = _encode(size=(256, 256))
    exact_peak_bytes = 1_064_960

    result, pixels = _normalize(
        payload,
        ImageSourceFormat.PNG,
        maximum_size=(128, 128),
        limits=_limits(maximum_memory_bytes=exact_peak_bytes),
    )

    assert (result.width_pixels, result.height_pixels) == (128, 128)
    assert len(pixels) == 65_536
    with pytest.raises(ImagePageError) as raised:
        _normalize(
            payload,
            ImageSourceFormat.PNG,
            maximum_size=(128, 128),
            limits=_limits(maximum_memory_bytes=exact_peak_bytes - 1),
        )
    assert raised.value.code is ImagePageErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "image.limit_memory"

    retried, retried_pixels = _normalize(
        payload,
        ImageSourceFormat.PNG,
        maximum_size=(128, 128),
        limits=_limits(maximum_memory_bytes=exact_peak_bytes),
    )
    assert retried.pixel_sha256 == result.pixel_sha256
    assert retried_pixels == pixels


def test_rgba_alpha_composite_requires_exact_three_source_raster_peak() -> None:
    payload = _encode(mode="RGBA", size=(128, 128))
    exact_peak_bytes = 266_240

    result, pixels = _normalize(
        payload,
        ImageSourceFormat.PNG,
        maximum_size=(64, 64),
        limits=_limits(maximum_memory_bytes=exact_peak_bytes),
    )

    assert result.metadata.source_color_model is ImageSourceColorModel.RGBA
    assert result.metadata.has_alpha
    assert (result.width_pixels, result.height_pixels) == (64, 64)
    assert set(pixels[3::4]) == {255}
    with pytest.raises(ImagePageError) as raised:
        _normalize(
            payload,
            ImageSourceFormat.PNG,
            maximum_size=(64, 64),
            limits=_limits(maximum_memory_bytes=exact_peak_bytes - 1),
        )
    assert raised.value.code is ImagePageErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "image.limit_memory"


def test_metadata_record_and_byte_budgets_reject_metadata_heavy_png() -> None:
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("synthetic", "x" * 100)
    payload = _encode(pnginfo=metadata)

    with pytest.raises(ImagePageError) as raised:
        _normalize(payload, ImageSourceFormat.PNG, limits=_limits(maximum_metadata_bytes=50))
    assert raised.value.code is ImagePageErrorCode.LIMIT_EXCEEDED

    with pytest.raises(ImagePageError) as raised:
        _normalize(payload, ImageSourceFormat.PNG, limits=_limits(maximum_metadata_records=2))
    assert raised.value.code is ImagePageErrorCode.LIMIT_EXCEEDED


@pytest.mark.parametrize(
    ("control", "code"),
    (
        (_Control(cancel_after=2), ImagePageErrorCode.CANCELLED),
        (_Control(expire_after=2), ImagePageErrorCode.TIMED_OUT),
    ),
)
def test_cancellation_and_deadline_are_checked_between_phases(
    control: _Control, code: ImagePageErrorCode
) -> None:
    with pytest.raises(ImagePageError) as raised:
        _normalize(_encode(size=(20, 20)), ImageSourceFormat.PNG, control=control)
    assert raised.value.code is code


class _FailingDestination(BytesIO):
    def write(self, _data: Buffer) -> Never:
        raise OSError("private-path-and-pixel-data")


class _ExactBytesDestination(BytesIO):
    def write(self, data: Buffer, /) -> int:
        assert type(data) is bytes
        return super().write(data)


def test_decoder_writes_exact_bytes_accepted_by_the_mapped_worker_boundary() -> None:
    payload = _encode()
    request = ImageNormalizationRequest(
        ImageSourceFormat.PNG,
        len(payload),
        hashlib.sha256(payload).hexdigest(),
        256,
        256,
    )
    destination = _ExactBytesDestination()

    result = PillowImageDecoder().normalize(
        BytesIO(payload),
        destination,
        expected_format=ImageSourceFormat.PNG,
        request=request,
        limits=_limits(),
        control=_Control(),
    )

    assert len(destination.getvalue()) == result.bytes_written


def test_destination_and_probe_exceptions_are_sanitized() -> None:
    payload = _encode()
    request = ImageNormalizationRequest(
        ImageSourceFormat.PNG,
        len(payload),
        hashlib.sha256(payload).hexdigest(),
        256,
        256,
    )
    with pytest.raises(ImagePageError) as raised:
        PillowImageDecoder().normalize(
            BytesIO(payload),
            _FailingDestination(),
            expected_format=ImageSourceFormat.PNG,
            request=request,
            limits=_limits(),
            control=_Control(),
        )
    assert raised.value.code is ImagePageErrorCode.DESTINATION_FAILED
    assert "private-path" not in str(raised.value)
    assert raised.value.__context__ is None


class _UnreadableSource(BytesIO):
    def read(self, _size: int | None = -1, /) -> Never:
        raise AssertionError("runtime admission must precede source access")

    def tell(self) -> Never:
        raise AssertionError("runtime admission must precede source access")


def test_unapproved_pillow_runtime_fails_before_source_access_or_decoder_open() -> None:
    request = ImageNormalizationRequest(
        ImageSourceFormat.PNG,
        1,
        hashlib.sha256(b"x").hexdigest(),
        1,
        1,
    )

    with (
        patch("paperworkshell.imaging.pillow_decoder.PIL.__version__", "private-version"),
        patch("paperworkshell.imaging.pillow_decoder.Image.open") as open_image,
        pytest.raises(ImagePageError) as raised,
    ):
        PillowImageDecoder().normalize(
            _UnreadableSource(),
            BytesIO(),
            expected_format=ImageSourceFormat.PNG,
            request=request,
            limits=_limits(),
            control=_Control(),
        )

    assert raised.value.code is ImagePageErrorCode.DEPENDENCY_UNAVAILABLE
    assert raised.value.context == "image.decoder_runtime"
    assert "private-version" not in str(raised.value)
    assert raised.value.__context__ is None
    open_image.assert_not_called()

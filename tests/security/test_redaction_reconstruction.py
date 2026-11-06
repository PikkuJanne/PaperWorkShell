"""Independent source-recovery controls for private PWS-0603 reconstruction.

PYTEST_DONT_REWRITE

These tests inspect actual decoded candidate pixels and PDF objects. They do not
provide the later OCR, whole-derivative sanitization, export or release approval.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import cast
from uuid import UUID

import pikepdf
import pypdfium2 as pdfium  # type: ignore[import-untyped]
import pytest
from PIL import Image, ImageChops, ImageDraw, ImageStat

from paperworkshell.domain.page_coordinates import ClockwiseRotation, NormalizedPageBox
from paperworkshell.domain.pdf_export import PdfExportError
from paperworkshell.domain.redaction import PERMANENT_REDACTION_ENABLED, RedactionMark
from paperworkshell.domain.redaction_reconstruction import (
    RedactionReconstructionReport,
    RedactionReconstructionRequest,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.pdf.redaction_reconstruction import (
    RedactionReconstructionAdapter,
    RedactionReconstructionLimits,
)
from tools.redaction_corpus import RedactionRegion, build_redaction_fixtures, redaction_cases

_CASES = {case.fixture_id: case for case in redaction_cases()}
_FIXTURES = {fixture.fixture_id: fixture for fixture in build_redaction_fixtures()}
_CASE_ID = UUID("00000000-0000-4000-8000-000000000001")
_SOURCE_ID = UUID("00000000-0000-4000-8000-000000000002")
_PAGE_ID = UUID("00000000-0000-4000-8000-000000000003")
_MARK_ID = UUID("00000000-0000-4000-8000-000000000004")


class _Control:
    def cancellation_requested(self) -> bool:
        return False

    def deadline_expired(self) -> bool:
        return False


def _text(content: bytes, page_index: int = 0) -> str:
    document = pdfium.PdfDocument(content)
    try:
        page = document[page_index]
        try:
            textpage = page.get_textpage()
            try:
                return cast(str, textpage.get_text_range())
            finally:
                textpage.close()
        finally:
            page.close()
    finally:
        document.close()


def _render(
    content: bytes, page_index: int, *, scale: float = math.nextafter(300 / 72, 0)
) -> Image.Image:
    document = pdfium.PdfDocument(content)
    try:
        page = document[page_index]
        try:
            bitmap = page.render(scale=scale)
            try:
                return cast(Image.Image, bitmap.to_pil().convert("RGB"))
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        document.close()


def _rotate_box(region: RedactionRegion, quarter_turns: int) -> NormalizedPageBox:
    # Independent display-coordinate formula. Corpus boxes precede intrinsic
    # rotation, while persisted removal intent follows that rotation.
    left, top, right, bottom = region.x0, region.y0, region.x1, region.y1
    for _ in range(quarter_turns):
        left, top, right, bottom = 1 - bottom, left, 1 - top, right
    values = tuple(round(value * 1_000_000) for value in (left, top, right, bottom))
    return NormalizedPageBox.from_edges(*values)


def _request(
    fixture_id: str,
    *,
    page_index: int = 0,
    crop: NormalizedPageBox | None = None,
    rotation: ClockwiseRotation = ClockwiseRotation.DEGREES_0,
) -> RedactionReconstructionRequest:
    content = _FIXTURES[fixture_id].content
    case = _CASES[fixture_id]
    region = next(region for region in case.target_regions if region.page_index == page_index)
    intrinsic_turns = page_index if fixture_id == "pdf-redaction-geometry" else 0
    return RedactionReconstructionRequest(
        case_id=_CASE_ID,
        manifest_revision=0,
        source_id=_SOURCE_ID,
        page_id=_PAGE_ID,
        source_sha256=hashlib.sha256(content).hexdigest(),
        source_byte_size=len(content),
        source_page_index=page_index,
        source_page_count=4 if fixture_id == "pdf-redaction-geometry" else 1,
        marks=(RedactionMark(_MARK_ID, _PAGE_ID, _rotate_box(region, intrinsic_turns)),),
        crop=crop or NormalizedPageBox.full_page(),
        rotation=rotation,
    )


def _limits() -> RedactionReconstructionLimits:
    return RedactionReconstructionLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1)


def _run(
    tmp_path: Path,
    fixture_id: str,
    request: RedactionReconstructionRequest | None = None,
) -> tuple[bytes, RedactionReconstructionReport]:
    original = _FIXTURES[fixture_id].content
    source = BytesIO(original)
    output = tmp_path / "candidate.pdf"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    report = RedactionReconstructionAdapter().reconstruct(
        source,
        output,
        scratch,
        request or _request(fixture_id),
        _limits(),
        _Control(),
    )
    assert source.getvalue() == original, "RECONSTRUCTION_SOURCE_MUTATED"
    assert list(scratch.iterdir()) == [], "RECONSTRUCTION_SCRATCH_RETAINED"
    candidate = output.read_bytes()
    assert report.output_sha256 == hashlib.sha256(candidate).hexdigest()
    assert report.output_byte_size == len(candidate)
    assert report.unsigned_derivative is True
    assert report.status == "awaiting_verification"
    assert PERMANENT_REDACTION_ENABLED is True
    return candidate, report


def _decoded_streams(content: bytes) -> tuple[bytes, ...]:
    with pikepdf.Pdf.open(BytesIO(content), attempt_recovery=False) as document:
        return tuple(
            obj.read_bytes() for obj in document.objects if isinstance(obj, pikepdf.Stream)
        )


def _candidate_image(content: bytes) -> Image.Image:
    with pikepdf.Pdf.open(BytesIO(content), attempt_recovery=False) as document:
        assert len(document.pages) == 1, "RECONSTRUCTION_PAGE_COUNT"
        page = document.pages[0]
        resources = page.Resources
        assert "/Font" not in resources, "RECONSTRUCTION_SOURCE_FONT_RETAINED"
        assert "/Annots" not in page.obj, "RECONSTRUCTION_SOURCE_ANNOTATION_RETAINED"
        assert "/OCProperties" not in document.Root, "RECONSTRUCTION_SOURCE_LAYER_RETAINED"
        assert "/Prev" not in document.trailer, "RECONSTRUCTION_REVISION_RETAINED"
        assert int(page.obj.get("/Rotate", 0)) == 0, "RECONSTRUCTION_ROTATION_NOT_FLATTENED"
        images = [value for _, value in resources.XObject.items()]
        assert len(images) == 1, "RECONSTRUCTION_IMAGE_COUNT"
        image = images[0]
        assert image.Subtype == pikepdf.Name.Image, "RECONSTRUCTION_NONIMAGE_XOBJECT"
        assert image.ColorSpace == pikepdf.Name.DeviceRGB and image.BitsPerComponent == 8
        assert "/SMask" not in image and "/Mask" not in image, "RECONSTRUCTION_HIDDEN_PIXELS"
        operators = {str(item.operator) for item in pikepdf.parse_content_stream(page)}
        assert operators <= {"q", "Q", "cm", "Do"}, "RECONSTRUCTION_SOURCE_OPERATORS_RETAINED"
        return Image.frombytes("RGB", (int(image.Width), int(image.Height)), image.read_bytes())


def _pixel_box(box: NormalizedPageBox, image: Image.Image) -> tuple[int, int, int, int]:
    return (
        math.floor(box.x * image.width / 1_000_000),
        math.floor(box.y * image.height / 1_000_000),
        math.ceil(box.right * image.width / 1_000_000),
        math.ceil(box.bottom * image.height / 1_000_000),
    )


def _assert_sanitized_pixels(
    candidate: Image.Image, source: Image.Image, box: NormalizedPageBox
) -> None:
    assert candidate.size == source.size, "RECONSTRUCTION_PIXEL_GEOMETRY_CHANGED"
    left, top, right, bottom = _pixel_box(box, candidate)
    # One pixel around the conservative outer mask remains outside this exact
    # interior check; the complete region still participates in preservation QA.
    interior = (left + 1, top + 1, right - 1, bottom - 1)
    assert all(extrema == (0, 0) for extrema in candidate.crop(interior).getextrema()), (
        "RECONSTRUCTION_TARGET_PIXELS_RECOVERABLE"
    )
    difference = ImageChops.difference(candidate, source)
    ImageDraw.Draw(difference).rectangle((left - 1, top - 1, right + 1, bottom + 1), fill=0)
    # A mean of one RGB level permits renderer edge quantization. Check the
    # non-redacted ink bounding box separately so a missing small keep-control
    # line cannot disappear into the mostly white full-page mean.
    assert max(ImageStat.Stat(difference).mean) <= 1.0, "RECONSTRUCTION_VISIBLE_CONTENT_CHANGED"
    preserved = ImageChops.invert(source)
    ImageDraw.Draw(preserved).rectangle((left - 1, top - 1, right + 1, bottom + 1), fill=0)
    keep_box = preserved.getbbox()
    assert keep_box is not None, "RECONSTRUCTION_KEEP_CONTROL_MISSING"
    assert max(ImageStat.Stat(difference.crop(keep_box)).mean) <= 1.0, (
        "RECONSTRUCTION_KEEP_CONTROL_CHANGED"
    )
    assert candidate.getextrema() == ((0, 255), (0, 255), (0, 255))


@pytest.mark.parametrize(
    "fixture_id",
    [
        "pdf-redaction-born-digital",
        "pdf-redaction-hidden-ocr",
        "pdf-redaction-hidden-layer",
        "pdf-redaction-scan",
        "pdf-redaction-embedded-image",
    ],
)
def test_rebuilt_page_has_only_sanitized_pixels_and_no_source_page_streams(
    tmp_path: Path, fixture_id: str
) -> None:
    source = _FIXTURES[fixture_id].content
    token = _CASES[fixture_id].canaries[0].token
    source_streams = _decoded_streams(source)
    # Positive recovery is mandatory: absence in the derivative is meaningful
    # only when the input actually contains recoverable source content.
    if fixture_id in {"pdf-redaction-born-digital", "pdf-redaction-hidden-ocr"}:
        assert token in _text(source), "RECONSTRUCTION_SOURCE_CONTROL_FAILED"
    elif fixture_id == "pdf-redaction-hidden-layer":
        assert any(token.encode("ascii") in stream for stream in source_streams), (
            "RECONSTRUCTION_SOURCE_CONTROL_FAILED"
        )
    else:
        pixel_digest = _CASES[fixture_id].canaries[0].pixel_sha256
        assert pixel_digest and any(
            hashlib.sha256(stream).hexdigest() == pixel_digest for stream in source_streams
        ), "RECONSTRUCTION_SOURCE_CONTROL_FAILED"
    candidate, report = _run(tmp_path, fixture_id)
    candidate_streams = _decoded_streams(candidate)
    assert not set(source_streams).intersection(candidate_streams), (
        "RECONSTRUCTION_SOURCE_STREAM_REUSED"
    )
    assert token not in _text(candidate), "RECONSTRUCTION_TEXT_RECOVERABLE"
    assert token.encode("ascii") not in candidate, "RECONSTRUCTION_RAW_TOKEN_RECOVERABLE"
    assert all(token.encode("ascii") not in stream for stream in candidate_streams)
    image = _candidate_image(candidate)
    assert image.size == (report.width_pixels, report.height_pixels)
    _assert_sanitized_pixels(image, _render(source, 0), _request(fixture_id).marks[0].box)


def test_genuine_previous_revision_canary_is_absent_from_all_candidate_bytes(
    tmp_path: Path,
) -> None:
    fixture_id = "pdf-redaction-incremental"
    source = _FIXTURES[fixture_id].content
    token = _CASES[fixture_id].canaries[0].token
    earlier = source[: source.index(b"%%EOF") + len(b"%%EOF")]
    assert source.count(b"%%EOF") == 2 and token in _text(earlier)
    assert token not in _text(source), "RECONSTRUCTION_REVISION_CONTROL_FAILED"
    candidate, _ = _run(tmp_path, fixture_id)
    assert candidate.count(b"%%EOF") == 1, "RECONSTRUCTION_MULTIPLE_REVISIONS"
    assert token.encode("ascii") not in candidate, "RECONSTRUCTION_PREVIOUS_REVISION_RECOVERABLE"
    assert all(token.encode("ascii") not in stream for stream in _decoded_streams(candidate))
    assert not set(_decoded_streams(source)).intersection(_decoded_streams(candidate))
    _candidate_image(candidate)


@pytest.mark.parametrize("page_index", range(4))
def test_nonzero_crop_origins_and_every_intrinsic_rotation_keep_visible_geometry(
    tmp_path: Path, page_index: int
) -> None:
    fixture_id = "pdf-redaction-geometry"
    source = _FIXTURES[fixture_id].content
    case = _CASES[fixture_id]
    assert case.canaries[page_index].token in _text(source, page_index)
    assert case.preserve_tokens[page_index] in _text(source, page_index)
    request = _request(fixture_id, page_index=page_index)
    candidate, report = _run(tmp_path, fixture_id, request)
    expected_points = (540.0, 680.0) if page_index % 2 == 0 else (680.0, 540.0)
    assert (report.width_points, report.height_points) == pytest.approx(expected_points, abs=1e-6)
    assert all(canary.token not in _text(candidate) for canary in case.canaries)
    _assert_sanitized_pixels(
        _candidate_image(candidate), _render(source, page_index), request.marks[0].box
    )


@pytest.mark.parametrize("page_index", range(4))
@pytest.mark.parametrize("rotation", tuple(ClockwiseRotation))
def test_manifest_crop_and_rotation_follow_burn_in_for_every_intrinsic_orientation(
    tmp_path: Path, page_index: int, rotation: ClockwiseRotation
) -> None:
    fixture_id = "pdf-redaction-geometry"
    crop = NormalizedPageBox(50_000, 50_000, 900_000, 900_000)
    request = _request(fixture_id, page_index=page_index, crop=crop, rotation=rotation)
    source = _render(_FIXTURES[fixture_id].content, page_index)
    left, top, right, bottom = _pixel_box(request.marks[0].box, source)
    preserved = ImageChops.invert(source)
    ImageDraw.Draw(preserved).rectangle((left - 1, top - 1, right + 1, bottom + 1), fill=0)
    preserved = preserved.crop(_pixel_box(crop, source))
    ImageDraw.Draw(source).rectangle((left - 1, top - 1, right, bottom), fill=(0, 0, 0))
    expected = source.crop(_pixel_box(crop, source))
    transposes = {
        ClockwiseRotation.DEGREES_90: Image.Transpose.ROTATE_270,
        ClockwiseRotation.DEGREES_180: Image.Transpose.ROTATE_180,
        ClockwiseRotation.DEGREES_270: Image.Transpose.ROTATE_90,
    }
    if rotation in transposes:
        expected = expected.transpose(transposes[rotation])
        preserved = preserved.transpose(transposes[rotation])
    candidate, report = _run(tmp_path, fixture_id, request)
    actual = _candidate_image(candidate)
    assert actual.size == expected.size, "RECONSTRUCTION_TRANSFORM_GEOMETRY_CHANGED"
    assert max(ImageStat.Stat(ImageChops.difference(actual, expected)).mean) <= 1.0, (
        "RECONSTRUCTION_TRANSFORM_PIXELS_CHANGED"
    )
    keep_box = preserved.getbbox()
    assert keep_box is not None, "RECONSTRUCTION_KEEP_CONTROL_MISSING"
    assert (
        max(ImageStat.Stat(ImageChops.difference(actual, expected).crop(keep_box)).mean) <= 1.0
    ), "RECONSTRUCTION_TRANSFORM_KEEP_CONTROL_CHANGED"
    width, height = (540.0, 680.0) if page_index % 2 == 0 else (680.0, 540.0)
    if int(rotation) % 180:
        width, height = height, width
    assert (report.width_points, report.height_points) == pytest.approx(
        (width * 0.9, height * 0.9), abs=1e-6
    )
    # Reopening and rendering the encoded PDF must agree with its declared page
    # geometry; inspecting the embedded image alone would miss a wrong PDF CTM.
    rendered = _render(candidate, 0, scale=1.0)
    assert abs(rendered.width - report.width_points) <= 1
    assert abs(rendered.height - report.height_points) <= 1


def test_ordinary_visual_cover_remains_an_unsafe_positive_recovery_control(tmp_path: Path) -> None:
    fixture_id = "pdf-redaction-visual-cover"
    source = _FIXTURES[fixture_id].content
    case = _CASES[fixture_id]
    assert case.negative_control is True
    assert case.canaries[0].token in _text(source), "RECONSTRUCTION_COVER_CONTROL_FAILED"
    request = _request(fixture_id)
    source_image = _render(source, 0)
    left, top, right, bottom = _pixel_box(request.marks[0].box, source_image)
    assert source_image.getpixel(((left + right) // 2, (top + bottom) // 2)) == (0, 0, 0)
    candidate, _ = _run(tmp_path, fixture_id, request)
    assert case.canaries[0].token not in _text(candidate)
    assert all(
        case.canaries[0].token.encode("ascii") not in stream
        for stream in _decoded_streams(candidate)
    )
    _candidate_image(candidate)


@pytest.mark.parametrize("mismatch", ["hash", "byte_size", "page_count"])
def test_source_identity_mismatch_fails_without_candidate_or_retained_scratch(
    tmp_path: Path, mismatch: str
) -> None:
    fixture_id = "pdf-redaction-born-digital"
    original = _FIXTURES[fixture_id].content
    request = _request(fixture_id)
    if mismatch == "hash":
        request = replace(request, source_sha256="0" * 64)
    elif mismatch == "byte_size":
        request = replace(request, source_byte_size=len(original) + 1)
    else:
        request = replace(request, source_page_count=2)
    source = BytesIO(original)
    output, scratch = tmp_path / "candidate.pdf", tmp_path / "scratch"
    scratch.mkdir()
    with pytest.raises(PdfExportError) as caught:
        RedactionReconstructionAdapter().reconstruct(
            source, output, scratch, request, _limits(), _Control()
        )
    assert original == source.getvalue()
    assert not output.exists() and list(scratch.iterdir()) == []
    assert all(canary.token not in str(caught.value) for canary in _CASES[fixture_id].canaries)


def test_readonly_original_file_preserves_bytes_and_stream_position(tmp_path: Path) -> None:
    fixture_id = "pdf-redaction-born-digital"
    original = _FIXTURES[fixture_id].content
    original_path = tmp_path / "immutable-source.pdf"
    original_path.write_bytes(original)
    before = original_path.stat()
    output, scratch = tmp_path / "candidate.pdf", tmp_path / "scratch"
    scratch.mkdir()
    with original_path.open("rb") as source:
        source.seek(7)
        report = RedactionReconstructionAdapter().reconstruct(
            source, output, scratch, _request(fixture_id), _limits(), _Control()
        )
        assert source.tell() == 7 and not source.writable()
    after = original_path.stat()
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
    assert original_path.read_bytes() == original
    assert hashlib.sha256(original_path.read_bytes()).hexdigest() == report.source_sha256
    assert output.read_bytes() != original and report.unsigned_derivative is True
    assert list(scratch.iterdir()) == []

"""Run the whole public adversarial corpus through independent verification.

PYTEST_DONT_REWRITE

The source controls deliberately fail verification. Successful private stages
remain unapproved for release while OCR or the human viewer matrix is unknown.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pikepdf
import pytest
from PIL import ImageChops, ImageDraw, ImageStat

from paperworkshell.domain.image_pages import (
    ImageNormalizationLimits,
    ImageNormalizationRequest,
    ImageRgba8Layout,
    ImageSourceFormat,
)
from paperworkshell.domain.redaction import PERMANENT_REDACTION_ENABLED, RedactionMark
from paperworkshell.domain.redaction_reconstruction import RedactionReconstructionRequest
from paperworkshell.domain.redaction_sanitization import RedactionSanitizationRequest
from paperworkshell.domain.redaction_verification import (
    RedactionVerificationRequest,
    VerificationMethod,
    VerificationStatus,
    verification_probe_digest,
)
from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.imaging.pillow_decoder import PillowImageDecoder
from paperworkshell.pdf.raster_pdf_page import RasterPdfPageAdapter, RasterPdfPageLimits
from paperworkshell.pdf.redaction_reconstruction import (
    RedactionReconstructionAdapter,
    RedactionReconstructionLimits,
)
from paperworkshell.pdf.redaction_sanitization import PikepdfRedactionSanitizer
from paperworkshell.pdf.redaction_verification import verify_redaction
from tests.security.test_redaction_reconstruction import _Control, _render, _rotate_box
from tools.redaction_corpus import RedactionCase, build_redaction_fixtures, redaction_cases

_CASES = redaction_cases()
_FIXTURES = {fixture.fixture_id: fixture.content for fixture in build_redaction_fixtures()}


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _limits() -> RedactionReconstructionLimits:
    return RedactionReconstructionLimits.from_resource_policy(CONSTRAINED_RESOURCE_POLICY_V1)


def _png_pdf(content: bytes, workspace: Path) -> bytes:
    """Use the admitted normalization and fixed-DPI PDF adapters for PNG input."""
    limits = _limits()
    normalization = ImageNormalizationLimits(
        maximum_source_bytes=limits.maximum_source_bytes,
        maximum_encoded_width_pixels=32768,
        maximum_encoded_height_pixels=32768,
        maximum_source_pixels=limits.maximum_pixels,
        maximum_decompressed_bytes=limits.maximum_memory_bytes,
        maximum_metadata_records=4096,
        maximum_metadata_bytes=16 * 1024 * 1024,
        maximum_output_width_pixels=1024,
        maximum_output_height_pixels=320,
        maximum_output_pixels=limits.maximum_pixels,
        maximum_output_bytes=limits.maximum_raster_bytes,
        maximum_memory_bytes=limits.maximum_memory_bytes,
        wall_time_ms=limits.wall_time_ms,
    )
    raw_path, pdf_path = workspace / "normalized.rgba", workspace / "normalized.pdf"
    with raw_path.open("xb") as raw:
        result = PillowImageDecoder().normalize(
            BytesIO(content),
            raw,
            expected_format=ImageSourceFormat.PNG,
            request=ImageNormalizationRequest(
                ImageSourceFormat.PNG, len(content), _digest(content), 1024, 320
            ),
            limits=normalization,
            control=_Control(),
        )
    RasterPdfPageAdapter().build(
        raw_path,
        pdf_path,
        ImageRgba8Layout(
            result.width_pixels,
            result.height_pixels,
            result.row_stride_bytes,
            result.width_pixels * result.height_pixels,
            result.bytes_written,
        ),
        RasterPdfPageLimits(
            limits.maximum_width_pixels,
            limits.maximum_height_pixels,
            limits.maximum_pixels,
            limits.maximum_raster_bytes,
            limits.maximum_output_bytes,
            limits.maximum_memory_bytes,
            limits.maximum_pdf_objects,
        ),
        _Control(),
    )
    raw_path.unlink()
    normalized = pdf_path.read_bytes()
    pdf_path.unlink()
    return normalized


@dataclass(frozen=True)
class _Prepared:
    source: bytes
    reference: bytes
    candidate: bytes
    affected_pages: tuple[int, ...]
    forbidden_streams: tuple[str, ...]


def _source_stream_probes(source: bytes, case: RedactionCase) -> tuple[str, ...]:
    """Fingerprint payloads even when their values are absent from the inventory.

    Fixed raster placement instructions may be identical after reconstruction;
    their image payload is fingerprinted instead. They contain no source pixels,
    text, names, metadata, or alternate appearance.
    """
    forbidden: set[str] = set()
    with pikepdf.Pdf.open(BytesIO(source), attempt_recovery=False) as document:
        affected = {region.page_index for region in case.target_regions}
        for value in document.objects:
            if isinstance(value, pikepdf.Stream):
                decoded = value.read_bytes()
                digest = _digest(decoded)
                if value.get("/Subtype") in {pikepdf.Name.Image, pikepdf.Name.Form} or any(
                    canary.token.encode("ascii") in decoded or digest == canary.pixel_sha256
                    for canary in case.canaries
                ):
                    forbidden.add(digest)
        for page_index in affected:
            page = document.pages[page_index]
            operations = pikepdf.parse_content_stream(page)
            if any(
                str(operation.operator) not in {"q", "Q", "cm", "Do"} for operation in operations
            ):
                contents = page.Contents
                streams = list(contents) if isinstance(contents, pikepdf.Array) else [contents]
                forbidden.update(_digest(stream.read_bytes()) for stream in streams)
    return tuple(sorted(forbidden))


def _prepare(case: RedactionCase, workspace: Path) -> _Prepared:
    """Process one page at a time; never retain full-resolution packet rasters."""
    original = _FIXTURES[case.fixture_id]
    source = _png_pdf(original, workspace) if case.fixture_id.startswith("png-") else original
    affected = tuple(sorted({region.page_index for region in case.target_regions}))
    forbidden = _source_stream_probes(source, case)
    with pikepdf.Pdf.open(BytesIO(source), attempt_recovery=False) as document:
        page_count = len(document.pages)
        if not affected:
            reference = source
        else:
            with pikepdf.Pdf.new() as assembled:
                for page_index in range(page_count):
                    page = document.pages[page_index]
                    if page_index not in affected:
                        assembled.pages.append(page)
                        continue
                    page_id = uuid4()
                    turns = int(page.obj.get("/Rotate", 0)) // 90
                    marks = tuple(
                        RedactionMark(uuid4(), page_id, _rotate_box(region, turns))
                        for region in case.target_regions
                        if region.page_index == page_index
                    )
                    request = RedactionReconstructionRequest(
                        uuid4(),
                        0,
                        uuid4(),
                        page_id,
                        _digest(source),
                        len(source),
                        page_index,
                        page_count,
                        marks,
                    )
                    page_path = workspace / "page.pdf"
                    RedactionReconstructionAdapter().reconstruct(
                        BytesIO(source), page_path, workspace, request, _limits(), _Control()
                    )
                    with pikepdf.Pdf.open(page_path, attempt_recovery=False) as rebuilt:
                        assembled.pages.append(rebuilt.pages[0])
                    page_path.unlink()
                output = BytesIO()
                assembled.save(output, deterministic_id=True)
                reference = output.getvalue()
    candidate_path = workspace / "candidate.pdf"
    report = PikepdfRedactionSanitizer().sanitize(
        BytesIO(reference),
        candidate_path,
        workspace,
        RedactionSanitizationRequest(
            uuid4(), uuid4(), _digest(reference), len(reference), page_count
        ),
        _limits(),
        _Control(),
    )
    candidate = candidate_path.read_bytes()
    candidate_path.unlink()
    assert report.after.empty, "CORPUS_SANITIZATION_FEATURE_RETAINED"
    assert report.unsigned_derivative, "CORPUS_DERIVATIVE_STATUS"
    assert _FIXTURES[case.fixture_id] == original, "CORPUS_SOURCE_MUTATED"
    assert not list(workspace.iterdir()), "CORPUS_STAGE_TEMP_RETAINED"
    return _Prepared(source, reference, candidate, affected, forbidden)


def _verification_request(
    candidate: bytes,
    reference: bytes,
    affected_pages: tuple[int, ...],
    page_count: int,
    tokens: tuple[str, ...] = (),
    streams: tuple[str, ...] = (),
) -> RedactionVerificationRequest:
    return RedactionVerificationRequest(
        _digest(candidate),
        len(candidate),
        _digest(reference),
        len(reference),
        page_count,
        affected_pages,
        _digest(b"public-synthetic-corpus-intent-v1"),
        probes_sha256=verification_probe_digest(tokens, streams),
    )


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.fixture_id)
def test_every_untreated_source_is_rejected_by_the_verifier(
    tmp_path: Path, case: RedactionCase
) -> None:
    original = _FIXTURES[case.fixture_id]
    source = _png_pdf(original, tmp_path) if case.fixture_id.startswith("png-") else original
    tokens = tuple(canary.token for canary in case.canaries)
    streams = _source_stream_probes(source, case) if case.expected_open == "accept" else ()
    report = verify_redaction(
        BytesIO(source),
        BytesIO(source),
        _verification_request(
            source,
            source,
            tuple(sorted({region.page_index for region in case.target_regions})),
            4 if case.fixture_id == "pdf-redaction-geometry" else 1,
            tokens,
            streams,
        ),
        tmp_path,
        _limits(),
        _Control(),
        forbidden_tokens=tokens,
        forbidden_stream_sha256=streams,
    )
    assert not report.automated_passed, "CORPUS_UNSAFE_SOURCE_ACCEPTED"
    assert not report.release_ready, "CORPUS_UNSAFE_RELEASE_ACCEPTED"
    assert any(item.status is VerificationStatus.FAIL for item in report.methods), (
        "CORPUS_UNSAFE_FAILURE_MISSING"
    )
    assert all(
        canary.token not in repr(report) and canary.token not in str(report.to_record())
        for canary in case.canaries
    ), "CORPUS_REPORT_VALUE_LEAKED"
    assert _FIXTURES[case.fixture_id] == original, "CORPUS_SOURCE_MUTATED"
    assert not list(tmp_path.iterdir()), "CORPUS_VERIFIER_TEMP_RETAINED"


@pytest.mark.parametrize(
    "case",
    tuple(case for case in _CASES if case.expected_open == "accept"),
    ids=lambda case: case.fixture_id,
)
def test_complete_reconstructed_sanitized_corpus_passes_automated_methods(
    tmp_path: Path, case: RedactionCase
) -> None:
    prepared = _prepare(case, tmp_path)
    page_count = 4 if case.fixture_id == "pdf-redaction-geometry" else 1
    tokens = tuple(canary.token for canary in case.canaries)
    report = verify_redaction(
        BytesIO(prepared.candidate),
        BytesIO(prepared.reference),
        _verification_request(
            prepared.candidate,
            prepared.reference,
            prepared.affected_pages,
            page_count,
            tokens,
            prepared.forbidden_streams,
        ),
        tmp_path,
        _limits(),
        _Control(),
        forbidden_tokens=tokens,
        forbidden_stream_sha256=prepared.forbidden_streams,
    )
    assert report.automated_passed, "CORPUS_AUTOMATED_METHOD_FAILED"
    assert not report.release_ready, "CORPUS_MISSING_REQUIRED_METHOD_ACCEPTED"
    assert {
        item.method for item in report.methods if item.status is VerificationStatus.UNKNOWN
    } == {VerificationMethod.OCR, VerificationMethod.VIEWERS}, "CORPUS_REQUIRED_METHOD_NOT_UNKNOWN"
    assert PERMANENT_REDACTION_ENABLED is True, "CORPUS_RELEASE_GATE_ENABLED"
    assert not list(tmp_path.iterdir()), "CORPUS_VERIFIER_TEMP_RETAINED"
    # Independent visual preservation checks compare against the untreated source.
    # A blank page cannot pass by deleting both the target and visible keep controls.
    for page_index in range(page_count):
        source_image = _render(prepared.source, page_index)
        candidate_image = _render(prepared.candidate, page_index)
        assert source_image.size == candidate_image.size, "CORPUS_PAGE_GEOMETRY_CHANGED"
        difference = ImageChops.difference(source_image, candidate_image)
        remaining_ink = ImageChops.invert(source_image)
        for region in case.target_regions:
            if region.page_index != page_index:
                continue
            box = _rotate_box(
                region, page_index if case.fixture_id == "pdf-redaction-geometry" else 0
            )
            left, top, right, bottom = (
                box.x * candidate_image.width // 1_000_000,
                box.y * candidate_image.height // 1_000_000,
                box.right * candidate_image.width // 1_000_000,
                box.bottom * candidate_image.height // 1_000_000,
            )
            assert all(
                extrema == (0, 0)
                for extrema in candidate_image.crop(
                    (left + 2, top + 2, right - 2, bottom - 2)
                ).getextrema()
            ), "CORPUS_MASK_PIXELS_RECOVERABLE"
            for image in (difference, remaining_ink):
                ImageDraw.Draw(image).rectangle((left - 2, top - 2, right + 2, bottom + 2), fill=0)
        keep_box = remaining_ink.getbbox()
        assert keep_box is not None, "CORPUS_KEEP_CONTROL_MISSING"
        assert max(ImageStat.Stat(difference.crop(keep_box)).mean) <= 1.0, (
            "CORPUS_KEEP_CONTROL_CHANGED"
        )

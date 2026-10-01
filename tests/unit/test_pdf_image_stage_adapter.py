"""Synthetic image-heavy/mixed corpus for explicit staged PDF image processing."""

from __future__ import annotations

import hashlib
import io
import random
import zlib
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pikepdf
import pytest
from PIL import Image

from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.domain.pdf_image_stages import (
    PdfImageColorMode,
    PdfImageEligibility,
    PdfImageStageOutcome,
    PdfImageStageProfile,
    PdfImageStageRequest,
    stage_image_geometry,
)
from paperworkshell.domain.pdf_rendering import PdfRenderRotation
from paperworkshell.pdf.export_adapter import _render_one
from paperworkshell.pdf.image_stage_adapter import (
    PdfImageStageAdapter,
    PdfImageStageAdapterLimits,
    PdfImageStageAdapterResult,
)
from paperworkshell.pdf.image_stage_encoder import required_image_memory
from paperworkshell.pdf.pdfium_renderer import PdfiumPageRenderer
from tests.unit.test_pdf_optimization_adapter import CleanChecker, baseline_report, control, limits


def image_limits() -> PdfImageStageAdapterLimits:
    return PdfImageStageAdapterLimits(limits(), 16_000_000, 8_000_000, 1_999_999, 1_000_000)


def make_image_baseline(
    path: Path,
    *,
    mode: str = "RGB",
    encoding: str = "raw",
    mutate: Callable[[pikepdf.Pdf, pikepdf.Stream, pikepdf.Page], None] | None = None,
) -> None:
    """Reusable small synthetic target: gradients, detail, text, and vector strokes."""

    width, height = 600, 480
    samples = bytes(
        ((x * (channel + 3) + y * (channel + 7)) % 256)
        for y in range(height)
        for x in range(width)
        for channel in range(3 if mode == "RGB" else 1)
    )
    with pikepdf.Pdf.new() as document:
        stream = document.make_stream(samples)
        if encoding == "flate":
            stream.write(zlib.compress(samples), filter=pikepdf.Name.FlateDecode)
        elif encoding == "jpeg":
            with Image.frombytes(mode, (width, height), samples) as image, io.BytesIO() as output:
                image.save(output, format="JPEG", quality=98, subsampling=0)
                stream.write(output.getvalue(), filter=pikepdf.Name.DCTDecode)
        stream.Type = pikepdf.Name.XObject
        stream.Subtype = pikepdf.Name.Image
        stream.Width, stream.Height = width, height
        stream.BitsPerComponent = 8
        stream.ColorSpace = pikepdf.Name.DeviceRGB if mode == "RGB" else pikepdf.Name.DeviceGray
        font = document.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica
            )
        )
        page = document.add_blank_page(page_size=(100, 80))
        page.obj.Resources = pikepdf.Dictionary(
            XObject=pikepdf.Dictionary(Pixels=stream), Font=pikepdf.Dictionary(F1=font)
        )
        page.obj.Contents = document.make_stream(
            b"q 72 0 0 57.6 2 2 cm /Pixels Do Q\nBT /F1 7 Tf 2 70 Td (SYNTHETIC TEXT) Tj ET\n0 0 1 RG 1 w 1 1 74 60 re S\n"
        )
        vector = document.add_blank_page(page_size=(100, 80))
        vector.obj.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
        vector.obj.Contents = document.make_stream(
            b"BT /F1 8 Tf 2 60 Td (VECTOR AND TEXT) Tj ET\n0 1 0 rg 10 10 20 20 re f\n"
        )
        document.docinfo.Title = "SYNTHETIC METADATA"
        if mutate is not None:
            mutate(document, stream, page)
        document.save(
            path,
            compress_streams=False,
            object_stream_mode=pikepdf.ObjectStreamMode.disable,
            deterministic_id=True,
        )


def run_image_stage(
    baseline: Path,
    candidate: Path,
    *,
    profile: PdfImageStageProfile | None = None,
    selected: tuple[int, ...] = (0,),
    selected_limits: PdfImageStageAdapterLimits | None = None,
    **kwargs: object,
) -> PdfImageStageAdapterResult:
    report = baseline_report(baseline)
    request = (
        PdfImageStageRequest()
        if profile is None
        else PdfImageStageRequest(
            profile, selected, report.output_sha256, report.semantic_fingerprint
        )
    )
    return PdfImageStageAdapter(qpdf_checker=CleanChecker()).process(
        baseline,
        candidate,
        baseline_report=report,
        request=request,
        limits=selected_limits or image_limits(),
        control=control(),
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("mode", ["RGB", "L"])
@pytest.mark.parametrize("encoding", ["raw", "flate", "jpeg"])
def test_analysis_reports_unique_assets_dpi_and_largest_coverage_without_writing(
    tmp_path: Path, mode: str, encoding: str
) -> None:
    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    make_image_baseline(baseline, mode=mode, encoding=encoding)
    original = baseline.read_bytes()
    result = run_image_stage(baseline, candidate)
    asset = result.analysis.assets[0]
    assert asset.eligibility is PdfImageEligibility.ELIGIBLE
    assert (asset.width_pixels, asset.height_pixels) == (600, 480)
    assert (asset.effective_dpi_x_milli, asset.effective_dpi_y_milli) == (600_000, 600_000)
    assert asset.color_mode is (
        PdfImageColorMode.RGB if mode == "RGB" else PdfImageColorMode.GRAYSCALE
    )
    assert result.analysis.pages[0].image_coverage_ppm == 518_400
    assert result.analysis.pages[0].image_heavy is True
    assert result.analysis.pages[1].image_heavy is False
    assert result.analysis.pages[1].asset_ids == ()
    assert result.report.outcome is PdfImageStageOutcome.UNCHANGED
    assert baseline.read_bytes() == original and not candidate.exists()


@pytest.mark.parametrize("profile", list(PdfImageStageProfile))
@pytest.mark.parametrize("mode", ["RGB", "L"])
@pytest.mark.parametrize("encoding", ["raw", "flate", "jpeg"])
def test_each_profile_preserves_text_vector_color_and_reports_measured_selection(
    tmp_path: Path, profile: PdfImageStageProfile, mode: str, encoding: str
) -> None:
    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    make_image_baseline(baseline, mode=mode, encoding=encoding)
    original = baseline.read_bytes()
    progress: list[tuple[int, int]] = []
    result = run_image_stage(
        baseline,
        candidate,
        profile=profile,
        progress=lambda done, total: progress.append((done, total)),
    )
    if mode == "L" and encoding == "flate":
        # This repetitive grayscale stream compresses better with lossless Flate
        # than with every attempted JPEG stage. Honest retention is the result.
        assert result.report.outcome is PdfImageStageOutcome.NO_REDUCTION
        assert result.report.assets[0].output_width_pixels == 600
        assert result.report.output_sha256 == hashlib.sha256(original).hexdigest()
        assert baseline.read_bytes() == original and not candidate.exists()
        assert progress[-1] == (7, 7)
        return
    assert result.report.outcome is PdfImageStageOutcome.REENCODED
    assert result.report.output_byte_size == candidate.stat().st_size < len(original)
    assert result.report.output_sha256 == hashlib.sha256(candidate.read_bytes()).hexdigest()
    impact = result.report.assets[0]
    assert impact.output_width_pixels == profile.target_dpi
    assert impact.output_height_pixels == profile.target_dpi * 4 // 5
    assert impact.output_effective_dpi_x_milli == profile.target_dpi * 1000
    assert (
        impact.candidate_encoded_byte_size
        == impact.selected_encoded_byte_size
        < impact.original_encoded_byte_size
    )
    assert baseline.read_bytes() == original
    assert progress == sorted(progress) and progress[0] == (0, 7) and progress[-1] == (7, 7)
    with pikepdf.Pdf.open(baseline) as before, pikepdf.Pdf.open(candidate) as after:
        assert before.docinfo == after.docinfo
        for left, right in zip(before.pages, after.pages, strict=True):
            assert left.obj.Contents.read_bytes() == right.obj.Contents.read_bytes()
        image = after.pages[0].obj.Resources.XObject.Pixels
        assert image.ColorSpace == (
            pikepdf.Name.DeviceRGB if mode == "RGB" else pikepdf.Name.DeviceGray
        )
        assert image.Filter == pikepdf.Name.DCTDecode and image.BitsPerComponent == 8
    renders = []
    for path in (baseline, candidate):
        renders.append(
            tuple(
                _render_one(
                    PdfiumPageRenderer(),
                    path,
                    page_index=index,
                    rotation=PdfRenderRotation.DEGREES_0,
                    limits=limits(),
                    control=control(),
                ).pixel_sha256
                for index in (0, 1)
            )
        )
    assert renders[0][0] != renders[1][0]  # Lossy changed page; no equality claim.
    assert renders[0][1] == renders[1][1]  # Untouched vector/text page.


@pytest.mark.parametrize(
    "kind",
    [
        "shared",
        "mask",
        "icc",
        "cmyk",
        "indexed",
        "decode",
        "predictor",
        "form",
        "inline",
        "clip",
        "default_color",
        "metadata_alias",
        "page_tree_alias",
        "fake_page_alias",
        "unused_parent_resources",
        "inherited_resources",
    ],
)
def test_unsupported_assets_remain_inventoried_and_unchanged(tmp_path: Path, kind: str) -> None:
    def mutate(document: pikepdf.Pdf, image: pikepdf.Stream, page: pikepdf.Page) -> None:
        if kind == "shared":
            document.pages[1].obj.Resources.XObject = pikepdf.Dictionary(Pixels=image)
        elif kind == "mask":
            image.Mask = pikepdf.Array([0, 0, 0, 0, 0, 0])
        elif kind == "icc":
            image.ColorSpace = pikepdf.Array(
                [pikepdf.Name.ICCBased, document.make_stream(b"SYNTHETIC ICC")]
            )
        elif kind == "cmyk":
            image.ColorSpace = pikepdf.Name.DeviceCMYK
        elif kind == "indexed":
            image.ColorSpace = pikepdf.Array(
                [pikepdf.Name.Indexed, pikepdf.Name.DeviceRGB, 0, pikepdf.String(b"abc")]
            )
        elif kind == "decode":
            image.Decode = pikepdf.Array([0, 1] * 3)
        elif kind == "predictor":
            image.DecodeParms = pikepdf.Dictionary(Predictor=12)
        elif kind == "form":
            form = document.make_stream(b"q /Pixels Do Q")
            form.Type, form.Subtype = pikepdf.Name.XObject, pikepdf.Name.Form
            form.BBox = pikepdf.Array([0, 0, 100, 80])
            form.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Pixels=image))
            page.obj.Resources.XObject = pikepdf.Dictionary(Form=form)
            page.obj.Contents.write(b"q /Form Do Q")
        elif kind == "inline":
            page.obj.Contents.write(
                page.obj.Contents.read_bytes() + b"BI /W 1 /H 1 /BPC 8 /CS /G ID x EI\n"
            )
        elif kind == "clip":
            page.obj.Contents.write(b"0 0 1 1 re W n\n" + page.obj.Contents.read_bytes())
        elif kind == "default_color":
            page.obj.Resources.ColorSpace = pikepdf.Dictionary(DefaultRGB=pikepdf.Name.DeviceRGB)
        elif kind == "metadata_alias":
            document.Root.PrivateImageAlias = image
        elif kind == "page_tree_alias":
            document.Root.Pages.PrivateImageAlias = image
        elif kind == "fake_page_alias":
            document.Root.FakePage = pikepdf.Dictionary(
                Type=pikepdf.Name.Page, PrivateImageAlias=image
            )
        elif kind == "unused_parent_resources":
            document.Root.Pages.Resources = pikepdf.Dictionary(
                XObject=pikepdf.Dictionary(Alias=image)
            )
        elif kind == "inherited_resources":
            document.Root.Pages.Resources = page.obj.Resources
            del page.obj.Resources

    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    make_image_baseline(baseline, mutate=mutate)
    result = run_image_stage(baseline, candidate)
    assert result.analysis.assets[0].eligibility is not PdfImageEligibility.ELIGIBLE
    assert not candidate.exists()
    with pytest.raises(PdfExportError):
        run_image_stage(baseline, candidate, profile=PdfImageStageProfile.PRESERVE_300_DPI_Q90)


@pytest.mark.parametrize(
    "command", [b"1 1 0 0 1 0 0 cm", b"1 /Pixels Do", b"Q", b"q", b"1 q", b"q 1 Q"]
)
def test_malformed_placement_state_fails_closed(tmp_path: Path, command: bytes) -> None:
    def mutate(document: pikepdf.Pdf, image: pikepdf.Stream, page: pikepdf.Page) -> None:
        page.obj.Contents.write(command)

    baseline = tmp_path / "baseline.pdf"
    make_image_baseline(baseline, mutate=mutate)
    with pytest.raises(PdfExportError) as caught:
        run_image_stage(baseline, tmp_path / "candidate.pdf")
    assert caught.value.code is PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED


def test_arbitrary_orthogonal_rotation_retains_dpi_but_has_unknown_coverage(tmp_path: Path) -> None:
    def mutate(document: pikepdf.Pdf, image: pikepdf.Stream, page: pikepdf.Page) -> None:
        page.obj.Contents.write(b"q 50 50 -40 40 40 0 cm /Pixels Do Q")

    baseline = tmp_path / "baseline.pdf"
    make_image_baseline(baseline, mutate=mutate)
    result = run_image_stage(baseline, tmp_path / "candidate.pdf")
    assert result.analysis.assets[0].eligibility is PdfImageEligibility.ELIGIBLE
    assert result.analysis.pages[0].image_coverage_ppm is None


@pytest.mark.parametrize("budget", ["zero_temp", "zero_candidate", "pixels", "memory", "temp"])
def test_constrained_stage_keeps_exact_baseline_when_work_cannot_fit(
    tmp_path: Path, budget: str
) -> None:
    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    make_image_baseline(baseline)
    selected_limits = {
        "zero_temp": replace(image_limits(), maximum_temporary_bytes=0),
        "zero_candidate": replace(image_limits(), maximum_candidate_bytes=0),
        "pixels": replace(image_limits(), maximum_source_pixels=10),
        "memory": replace(image_limits(), maximum_memory_bytes=100_000),
        "temp": replace(image_limits(), maximum_temporary_bytes=1_000_000),
    }[budget]
    result = run_image_stage(
        baseline,
        candidate,
        profile=PdfImageStageProfile.PRESERVE_300_DPI_Q90,
        selected_limits=selected_limits,
    )
    assert result.report.outcome is PdfImageStageOutcome.SKIPPED_BUDGET
    assert result.candidate_sha256 is None and not candidate.exists()


def test_candidate_capacity_overflow_restores_original_impact_dimensions(tmp_path: Path) -> None:
    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    make_image_baseline(baseline)
    result = run_image_stage(
        baseline,
        candidate,
        profile=PdfImageStageProfile.PRESERVE_300_DPI_Q90,
        selected_limits=replace(image_limits(), maximum_candidate_bytes=100),
    )
    assert result.report.outcome is PdfImageStageOutcome.NO_REDUCTION
    impact = result.report.assets[0]
    assert impact.output_width_pixels == impact.original_width_pixels == 600
    assert impact.candidate_encoded_byte_size is not None
    assert result.report.candidate_byte_size is None and not candidate.exists()


def test_retained_compressed_replacements_reduce_later_image_memory_admission(
    tmp_path: Path,
) -> None:
    def mutate(document: pikepdf.Pdf, image: pikepdf.Stream, page: pikepdf.Page) -> None:
        other = document.make_stream(image.read_bytes())
        for key in ("/Type", "/Subtype", "/Width", "/Height", "/BitsPerComponent", "/ColorSpace"):
            other[key] = image[key]
        page.obj.Resources.XObject.Zother = other
        page.obj.Contents.write(
            page.obj.Contents.read_bytes() + b"q 72 0 0 57.6 2 2 cm /Zother Do Q\n"
        )

    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    make_image_baseline(baseline, mutate=mutate)
    analysis = run_image_stage(baseline, candidate).analysis
    profile = PdfImageStageProfile.PRESERVE_300_DPI_Q90
    admitted = required_image_memory(
        analysis.assets[0], stage_image_geometry(analysis.assets[0], profile)
    )
    # Exactly one prospective image fits. The second must account for the first
    # replacement's Python/native encoded copies even though no raster is kept.
    result = run_image_stage(
        baseline,
        candidate,
        profile=profile,
        selected=(0, 1),
        selected_limits=replace(image_limits(), maximum_memory_bytes=admitted),
    )
    assert result.report.outcome is PdfImageStageOutcome.REENCODED
    assert [asset.outcome for asset in result.report.assets] == [
        PdfImageStageOutcome.REENCODED,
        PdfImageStageOutcome.SKIPPED_BUDGET,
    ]
    assert result.report.assets[1].output_width_pixels == 600


@pytest.mark.parametrize(
    "placement,unit,dpi",
    [
        (b"q 72 0 0 57.6 2 2 cm /Pixels Do Q q 144 0 0 115.2 2 2 cm /Pixels Do Q", 1, 300_000),
        (b"q 72 0 0 57.6 2 2 cm /Pixels Do Q", 2, 300_000),
        (b"q 2 0 0 2 0 0 cm q 36 0 0 28.8 1 1 cm /Pixels Do Q Q", 1, 600_000),
        (b"q 0 72 -57.6 0 80 2 cm /Pixels Do Q", 1, 600_000),
        (b"q -72 0 0 57.6 74 2 cm /Pixels Do Q", 1, 600_000),
    ],
)
def test_dpi_accounts_for_all_uses_user_unit_nested_matrices_and_axis_rotation(
    tmp_path: Path, placement: bytes, unit: int, dpi: int
) -> None:
    def mutate(document: pikepdf.Pdf, image: pikepdf.Stream, page: pikepdf.Page) -> None:
        page.obj.Contents.write(placement)
        page.obj.UserUnit = unit

    baseline = tmp_path / "baseline.pdf"
    make_image_baseline(baseline, mutate=mutate)
    result = run_image_stage(baseline, tmp_path / "candidate.pdf")
    assert result.analysis.assets[0].effective_dpi_x_milli == dpi
    assert result.analysis.assets[0].effective_dpi_y_milli == dpi
    assert result.analysis.pages[0].image_coverage_ppm is not None


def test_incompressible_flate_admits_encoded_overhead_separately_from_pixel_expansion(
    tmp_path: Path,
) -> None:
    def mutate(document: pikepdf.Pdf, image: pikepdf.Stream, page: pikepdf.Page) -> None:
        samples = random.Random(503).randbytes(600 * 480 * 3)
        encoded = zlib.compress(samples)
        assert len(encoded) > len(samples)
        image.write(encoded, filter=pikepdf.Name.FlateDecode)

    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    make_image_baseline(baseline, mutate=mutate)
    result = run_image_stage(baseline, candidate, profile=PdfImageStageProfile.PRESERVE_300_DPI_Q90)
    assert result.report.outcome is PdfImageStageOutcome.REENCODED


def test_embedded_jpeg_exif_does_not_rotate_pdf_image_axes(tmp_path: Path) -> None:
    def mutate(document: pikepdf.Pdf, image: pikepdf.Stream, page: pikepdf.Page) -> None:
        samples = image.read_bytes()
        with Image.frombytes("RGB", (600, 480), samples) as pixels, io.BytesIO() as encoded:
            exif = Image.Exif()
            exif[274] = 6
            pixels.save(encoded, format="JPEG", quality=98, subsampling=0, exif=exif)
            image.write(encoded.getvalue(), filter=pikepdf.Name.DCTDecode)

    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    make_image_baseline(baseline, mutate=mutate)
    result = run_image_stage(baseline, candidate, profile=PdfImageStageProfile.PRESERVE_300_DPI_Q90)
    assert result.report.outcome is PdfImageStageOutcome.REENCODED
    with pikepdf.Pdf.open(candidate) as document:
        stream = document.pages[0].obj.Resources.XObject.Pixels
        with Image.open(io.BytesIO(stream.read_raw_bytes())) as decoded:
            assert decoded.size == (300, 240)
            assert not decoded.getexif()


def test_unselected_image_encoding_is_exact_and_page_actual_bytes_match_reopened_pdf(
    tmp_path: Path,
) -> None:
    def mutate(document: pikepdf.Pdf, image: pikepdf.Stream, page: pikepdf.Page) -> None:
        other = document.make_stream(b"abc" * 16)
        other.Type, other.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
        other.Width = other.Height = 4
        other.BitsPerComponent = 8
        other.ColorSpace = pikepdf.Name.DeviceRGB
        page.obj.Resources.XObject.Zother = other
        page.obj.Contents.write(
            page.obj.Contents.read_bytes() + b"q 1 0 0 1 90 2 cm /Zother Do Q\n"
        )

    baseline, candidate = tmp_path / "baseline.pdf", tmp_path / "candidate.pdf"
    make_image_baseline(baseline, mutate=mutate)
    result = run_image_stage(baseline, candidate, profile=PdfImageStageProfile.PRESERVE_300_DPI_Q90)
    assert result.report.outcome is PdfImageStageOutcome.REENCODED
    with pikepdf.Pdf.open(baseline) as before, pikepdf.Pdf.open(candidate) as after:
        original = before.pages[0].obj.Resources.XObject.Zother
        current = after.pages[0].obj.Resources.XObject.Zother
        assert original.read_raw_bytes() == current.read_raw_bytes() == b"abc" * 16
        assert original.get("/Filter") is None and current.get("/Filter") is None
        assert original.Length == current.Length == 48
        actual = int(after.pages[0].obj.Resources.XObject.Pixels.Length) + int(current.Length)
        assert result.report.pages[0].actual_image_byte_size == actual

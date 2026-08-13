"""Focused tests for sequential searchable mixed-PDF assembly and verification."""

from __future__ import annotations

import hashlib
import io
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pikepdf
import pytest
from PIL import Image

import paperworkshell.pdf.searchable_export_adapter as searchable_adapter
from paperworkshell.domain.image_pages import ImageNormalizationLimits
from paperworkshell.domain.image_transforms import ImageTransformLimits
from paperworkshell.domain.ocr import OcrError, OcrErrorCode
from paperworkshell.domain.page_operations import NormalizedPageBox
from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.domain.pdf_inspection import PdfInspectionLimits, PdfStructuralState
from paperworkshell.domain.pdf_rendering import PdfPageRenderLimits
from paperworkshell.domain.searchable_pdf_export import (
    SearchablePdfExportPlan,
    SearchablePdfPage,
    SearchablePdfPageEligibilityCode,
    SearchablePdfPageOutcomeCode,
    SearchablePdfPageSearchability,
    SearchablePdfSource,
    SearchablePdfSourceEncryptionState,
    SearchablePdfSourceMediaType,
    SearchablePdfSourceSignatureState,
)
from paperworkshell.ocr.ocrmypdf_adapter import OcrAdapterResult
from paperworkshell.pdf.pdfium_renderer import PdfiumPageRenderer
from paperworkshell.pdf.raster_pdf_page import RasterPdfPageLimits
from paperworkshell.pdf.searchable_export_adapter import (
    SearchablePdfExportAdapter,
    SearchablePdfExportAdapterControl,
    SearchablePdfExportAdapterLimits,
)
from paperworkshell.pdf.text_presence import (
    PdfiumTextPresenceInspector,
    PdfTextPresenceLimits,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PDF_FIXTURES = PROJECT_ROOT / "tests/fixtures/synthetic/v1/pdf"


class _CleanChecker:
    def __init__(self) -> None:
        self.calls = 0

    def check(self, *_args: object, **_kwargs: object) -> PdfStructuralState:
        self.calls += 1
        return PdfStructuralState.CLEAN


class _NeverOcr:
    def __init__(self) -> None:
        self.calls = 0

    def process(self, *_args: object, **_kwargs: object) -> object:
        self.calls += 1
        raise AssertionError("OCR must not be invoked")


class _RecognitionFailureOcr:
    def __init__(self) -> None:
        self.calls = 0

    def process(self, *_args: object, **_kwargs: object) -> object:
        self.calls += 1
        raise OcrError(OcrErrorCode.ENGINE_FAILED, "ocr.tesseract_exit")


class _ScratchObservingRecognitionFailureOcr:
    def __init__(self) -> None:
        self.calls = 0
        self.temporary_directories: list[Path] = []
        self.maximum_temporary_storage_bytes: list[int] = []
        self.page_entries: list[tuple[str, ...]] = []

    def process(self, source_path: Path, _output_path: Path, **kwargs: object) -> object:
        self.calls += 1
        self.temporary_directories.append(cast(Path, kwargs["temporary_directory"]))
        self.maximum_temporary_storage_bytes.append(
            cast(int, kwargs["maximum_temporary_storage_bytes"])
        )
        self.page_entries.append(tuple(sorted(path.name for path in source_path.parent.iterdir())))
        raise OcrError(OcrErrorCode.ENGINE_FAILED, "ocr.tesseract_exit")


class _FatalOcr:
    def process(self, *_args: object, **_kwargs: object) -> object:
        raise OcrError(OcrErrorCode.LANGUAGE_UNAVAILABLE, "ocr.languages")


class _SuccessfulOcr:
    def __init__(self) -> None:
        self.calls = 0
        self.input_page_sizes_points: list[tuple[float, float]] = []

    def process(self, source_path: Path, output_path: Path, **_kwargs: object) -> object:
        self.calls += 1
        with pikepdf.open(source_path, attempt_recovery=False, suppress_warnings=True) as document:
            page = document.pages[0]
            self.input_page_sizes_points.append(
                (
                    float(page.MediaBox[2]) - float(page.MediaBox[0]),
                    float(page.MediaBox[3]) - float(page.MediaBox[1]),
                )
            )
            resources = page.obj.get(pikepdf.Name.Resources)
            if not isinstance(resources, pikepdf.Dictionary):
                resources = pikepdf.Dictionary()
                page.obj.Resources = resources
            fonts = resources.get(pikepdf.Name.Font)
            if not isinstance(fonts, pikepdf.Dictionary):
                fonts = pikepdf.Dictionary()
                resources.Font = fonts
            fonts.Focr = document.make_indirect(
                pikepdf.Dictionary(
                    Type=pikepdf.Name.Font,
                    Subtype=pikepdf.Name.Type1,
                    BaseFont=pikepdf.Name.Helvetica,
                )
            )
            # Rendering mode 3 makes the synthetic OCR text non-painting while
            # leaving it extractable for the fixed text-presence profile.
            page.contents_add(b"q BT /Focr 10 Tf 3 Tr 1 1 Td (synthetic OCR) Tj ET Q\n")
            document.save(output_path, deterministic_id=True)
        payload = output_path.read_bytes()
        return OcrAdapterResult(
            byte_size=len(payload),
            page_count=1,
            sha256=hashlib.sha256(payload).hexdigest(),
            ocrmypdf_version="17.10.0",
            tesseract_version="5.5.3",
        )


class _GeometryChangingOcr(_SuccessfulOcr):
    def process(self, source_path: Path, output_path: Path, **kwargs: object) -> object:
        super().process(source_path, output_path, **kwargs)
        payload = output_path.read_bytes()
        with pikepdf.open(
            io.BytesIO(payload),
            attempt_recovery=False,
            suppress_warnings=True,
        ) as document:
            page = document.pages[0]
            media_box = tuple(map(float, page.MediaBox))
            page.MediaBox = pikepdf.Array(
                [
                    media_box[0],
                    media_box[1],
                    media_box[0] + 2 * (media_box[2] - media_box[0]),
                    media_box[1] + 2 * (media_box[3] - media_box[1]),
                ]
            )
            document.save(output_path, deterministic_id=True)
        changed = output_path.read_bytes()
        return OcrAdapterResult(
            byte_size=len(changed),
            page_count=1,
            sha256=hashlib.sha256(changed).hexdigest(),
            ocrmypdf_version="17.10.0",
            tesseract_version="5.5.3",
        )


def _limits() -> SearchablePdfExportAdapterLimits:
    normalization = ImageNormalizationLimits(
        maximum_source_bytes=20_000_000,
        maximum_encoded_width_pixels=4096,
        maximum_encoded_height_pixels=4096,
        maximum_source_pixels=16_000_000,
        maximum_decompressed_bytes=64_000_000,
        maximum_metadata_records=4096,
        maximum_metadata_bytes=16_000_000,
        maximum_output_width_pixels=4096,
        maximum_output_height_pixels=4096,
        maximum_output_pixels=16_000_000,
        maximum_output_bytes=64_000_000,
        maximum_memory_bytes=128_000_000,
        wall_time_ms=60_000,
    )
    transform = ImageTransformLimits(
        maximum_source_width_pixels=4096,
        maximum_source_height_pixels=4096,
        maximum_source_pixels=16_000_000,
        maximum_intermediate_pixels=16_000_000,
        maximum_output_width_pixels=4096,
        maximum_output_height_pixels=4096,
        maximum_output_pixels=16_000_000,
        maximum_output_bytes=64_000_000,
        maximum_memory_bytes=128_000_000,
        maximum_temporary_storage_bytes=128_000_000,
        wall_time_ms=60_000,
    )
    return SearchablePdfExportAdapterLimits(
        inspection=PdfInspectionLimits(
            maximum_pages=100,
            maximum_objects=100_000,
            maximum_input_bytes=100_000_000,
            maximum_temporary_bytes=256_000_000,
            maximum_output_bytes=100_000_000,
            maximum_tool_output_bytes=1_000_000,
        ),
        rendering=PdfPageRenderLimits(
            maximum_pages=100,
            maximum_width_pixels=4096,
            maximum_height_pixels=4096,
            maximum_pixels=16_000_000,
            maximum_bitmap_bytes=64_000_000,
            maximum_destination_bytes=64_000_000,
            maximum_memory_bytes=128_000_000,
            wall_time_ms=60_000,
        ),
        text_presence=PdfTextPresenceLimits(100, 100_000_000, 1_000_000),
        image_normalization=normalization,
        image_transform=transform,
        raster_pdf=RasterPdfPageLimits(
            4096,
            4096,
            16_000_000,
            64_000_000,
            100_000_000,
            256_000_000,
            100_000,
        ),
        maximum_total_source_bytes=100_000_000,
        maximum_page_scratch_bytes=256_000_000,
        maximum_ocr_output_bytes=100_000_000,
        maximum_ocr_image_pixels=16_000_000,
        maximum_ocr_temporary_storage_bytes=100_000_000,
        pdf_transform_width_pixels=1024,
        pdf_transform_height_pixels=1024,
        verification_width_pixels=256,
        verification_height_pixels=256,
    )


def _control() -> SearchablePdfExportAdapterControl:
    return SearchablePdfExportAdapterControl(time.monotonic() + 30)


def _page_count(path: Path) -> int:
    with pikepdf.open(path, attempt_recovery=False, suppress_warnings=True) as document:
        return len(document.pages)


def _source(
    path: Path,
    source_id: UUID,
    *,
    signature: SearchablePdfSourceSignatureState = (
        SearchablePdfSourceSignatureState.NONE_DETECTED
    ),
) -> SearchablePdfSource:
    payload = path.read_bytes()
    return SearchablePdfSource(
        source_id=source_id,
        media_type=SearchablePdfSourceMediaType.PDF,
        byte_size=len(payload),
        page_count=_page_count(path),
        sha256=hashlib.sha256(payload).hexdigest(),
        signature_state=signature,
        encryption_state=SearchablePdfSourceEncryptionState.UNENCRYPTED,
    )


def _plan(
    path: Path,
    page_indices: tuple[int, ...],
    *,
    signature: SearchablePdfSourceSignatureState = (
        SearchablePdfSourceSignatureState.NONE_DETECTED
    ),
) -> tuple[SearchablePdfExportPlan, dict[UUID, Path]]:
    source_id = uuid4()
    pages = tuple(
        SearchablePdfPage(
            output_page_index=output_index,
            page_id=uuid4(),
            source_id=source_id,
            source_page_index=source_index,
            quarter_turns_clockwise=0,
            crop=None,
            deskew_angle_millidegrees_clockwise=0,
            contrast_factor_millionths=1_000_000,
            background_cleanup_strength_millionths=0,
        )
        for output_index, source_index in enumerate(page_indices)
    )
    plan = SearchablePdfExportPlan(
        case_id=uuid4(),
        manifest_revision=1,
        manifest_sha256="a" * 64,
        manifest_cas_token="manifest-cas-token-v2",
        sources=(_source(path, source_id, signature=signature),),
        pages=pages,
    )
    return plan, {source_id: path.resolve()}


def _plan_with_transform(
    path: Path,
    *,
    crop: NormalizedPageBox | None = None,
    quarter_turns_clockwise: int = 0,
) -> tuple[SearchablePdfExportPlan, dict[UUID, Path]]:
    plan, paths = _plan(path, (0,))
    page = replace(
        plan.pages[0],
        crop=crop,
        quarter_turns_clockwise=quarter_turns_clockwise,
        contrast_factor_millionths=1_100_000,
    )
    return (
        SearchablePdfExportPlan(
            case_id=plan.case_id,
            manifest_revision=plan.manifest_revision,
            manifest_sha256=plan.manifest_sha256,
            manifest_cas_token=plan.manifest_cas_token,
            sources=plan.sources,
            pages=(page,),
            profile=plan.profile,
        ),
        paths,
    )


def _write_blank_pdf(
    path: Path,
    *,
    media_box: tuple[float, float, float, float],
    crop_box: tuple[float, float, float, float] | None = None,
    user_unit: float | None = None,
    rotation: int = 0,
) -> None:
    width = media_box[2] - media_box[0]
    height = media_box[3] - media_box[1]
    with pikepdf.Pdf.new() as document:
        page = document.add_blank_page(page_size=(width, height)).obj
        page.MediaBox = pikepdf.Array(media_box)
        if crop_box is not None:
            page.CropBox = pikepdf.Array(crop_box)
        if user_unit is not None:
            page.UserUnit = user_unit
        if rotation:
            page.Rotate = rotation
        document.save(path, deterministic_id=True)


def _image_plan(
    path: Path,
    *,
    media_type: SearchablePdfSourceMediaType = SearchablePdfSourceMediaType.PNG,
) -> tuple[SearchablePdfExportPlan, dict[UUID, Path]]:
    source_id = uuid4()
    payload = path.read_bytes()
    source = SearchablePdfSource(
        source_id=source_id,
        media_type=media_type,
        byte_size=len(payload),
        page_count=1,
        sha256=hashlib.sha256(payload).hexdigest(),
        signature_state=SearchablePdfSourceSignatureState.NOT_APPLICABLE,
        encryption_state=SearchablePdfSourceEncryptionState.NOT_APPLICABLE,
    )
    page = SearchablePdfPage(
        output_page_index=0,
        page_id=uuid4(),
        source_id=source_id,
        source_page_index=0,
        quarter_turns_clockwise=1,
        crop=None,
        deskew_angle_millidegrees_clockwise=0,
        contrast_factor_millionths=1_000_000,
        background_cleanup_strength_millionths=0,
    )
    return (
        SearchablePdfExportPlan(
            case_id=uuid4(),
            manifest_revision=1,
            manifest_sha256="a" * 64,
            manifest_cas_token="manifest-cas-token-v2",
            sources=(source,),
            pages=(page,),
        ),
        {source_id: path.resolve()},
    )


def _run(
    tmp_path: Path,
    plan: SearchablePdfExportPlan,
    paths: dict[UUID, Path],
    ocr: Any,
) -> tuple[Path, Any, _CleanChecker]:
    checker = _CleanChecker()
    output = (tmp_path / "output.pdf").resolve()
    report = SearchablePdfExportAdapter(
        ocr_processor=ocr,
        qpdf_checker=checker,
        renderer=PdfiumPageRenderer(),
    ).assemble_and_verify(
        plan,
        paths,
        output,
        tmp_path.resolve(),
        languages=("eng",),
        limits=_limits(),
        control=_control(),
    )
    return output, report, checker


def test_born_digital_page_is_object_copied_without_ocr_and_text_survives(
    tmp_path: Path,
) -> None:
    source = (PDF_FIXTURES / "born-digital.pdf").resolve()
    plan, paths = _plan(source, (0,))
    ocr = _NeverOcr()

    output, report, checker = _run(tmp_path, plan, paths, ocr)

    assert ocr.calls == 0
    assert checker.calls == 1
    assert report.page_outcomes[0].outcome is SearchablePdfPageOutcomeCode.EXISTING_TEXT_PRESERVED
    assert report.page_outcomes[0].has_extractable_text
    assert (
        PdfiumTextPresenceInspector()
        .inspect(
            output,
            (0,),
            _limits().text_presence,
            _control(),
        )[0]
        .has_extractable_text
    )
    assert hashlib.sha256(source.read_bytes()).hexdigest() == plan.sources[0].sha256


def test_mixed_document_continues_only_exact_page_local_recognition_failure(
    tmp_path: Path,
) -> None:
    source = (PDF_FIXTURES / "mixed.pdf").resolve()
    plan, paths = _plan(source, (0, 1))
    ocr = _RecognitionFailureOcr()

    output, report, _checker = _run(tmp_path, plan, paths, ocr)

    assert ocr.calls == 1
    assert tuple(page.outcome for page in report.page_outcomes) == (
        SearchablePdfPageOutcomeCode.EXISTING_TEXT_PRESERVED,
        SearchablePdfPageOutcomeCode.OCR_FAILED_RETAINED,
    )
    assert tuple(
        fact.has_extractable_text
        for fact in PdfiumTextPresenceInspector().inspect(
            output,
            (0, 1),
            _limits().text_presence,
            _control(),
        )
    ) == (True, False)
    assert not tuple(tmp_path.glob("pws-searchable-page-*"))


def test_image_only_pdf_gains_verified_ocr_text_when_ocr_succeeds(tmp_path: Path) -> None:
    source = (PDF_FIXTURES / "image-only.pdf").resolve()
    plan, paths = _plan(source, (0,))
    ocr = _SuccessfulOcr()

    output, report, _checker = _run(tmp_path, plan, paths, ocr)

    assert ocr.calls == 1
    assert report.page_outcomes[0].outcome is SearchablePdfPageOutcomeCode.OCR_TEXT_VERIFIED
    assert report.page_outcomes[0].has_extractable_text
    assert (
        PdfiumTextPresenceInspector()
        .inspect(
            output,
            (0,),
            _limits().text_presence,
            _control(),
        )[0]
        .has_extractable_text
    )


def test_png_runs_pillow_transform_fixed_dpi_wrapper_and_verified_ocr(tmp_path: Path) -> None:
    source = (PROJECT_ROOT / "tests/fixtures/synthetic/v1/image/ocr-raster-text.png").resolve()
    plan, paths = _image_plan(source)
    ocr = _SuccessfulOcr()

    output, report, _checker = _run(tmp_path, plan, paths, ocr)

    assert ocr.calls == 1
    assert report.page_outcomes[0].outcome is SearchablePdfPageOutcomeCode.OCR_TEXT_VERIFIED
    with pikepdf.open(output, attempt_recovery=False, suppress_warnings=True) as document:
        assert len(document.pages) == 1
        width = float(document.pages[0].MediaBox[2])
        height = float(document.pages[0].MediaBox[3])
        assert width == pytest.approx(240 * 72 / 300)
        assert height == pytest.approx(720 * 72 / 300)
    assert not tuple(tmp_path.glob("pws-searchable-page-*"))


def test_in_memory_jpeg_runs_normalization_fixed_dpi_ocr_and_status(
    tmp_path: Path,
) -> None:
    encoded = io.BytesIO()
    Image.new("RGB", (80, 40), (240, 248, 255)).save(
        encoded,
        format="JPEG",
        quality=90,
        optimize=False,
        progressive=False,
    )
    source = (tmp_path / "synthetic.jpg").resolve()
    source.write_bytes(encoded.getvalue())
    plan, paths = _image_plan(
        source,
        media_type=SearchablePdfSourceMediaType.JPEG,
    )
    ocr = _SuccessfulOcr()

    output, report, _checker = _run(tmp_path, plan, paths, ocr)

    outcome = report.page_outcomes[0]
    assert plan.sources[0].media_type is SearchablePdfSourceMediaType.JPEG
    assert ocr.calls == 1
    # The planned quarter-turn swaps 80x40 before the fixed 300-DPI wrapper
    # reaches the fake OCR boundary.
    assert len(ocr.input_page_sizes_points) == 1
    input_width, input_height = ocr.input_page_sizes_points[0]
    assert input_width == pytest.approx(40 * 72 / 300)
    assert input_height == pytest.approx(80 * 72 / 300)
    assert outcome.eligibility.code is SearchablePdfPageEligibilityCode.OCR_REQUIRED_IMAGE_SOURCE
    assert outcome.outcome is SearchablePdfPageOutcomeCode.OCR_TEXT_VERIFIED
    assert outcome.searchability is SearchablePdfPageSearchability.EXTRACTABLE_TEXT_PRESENT_OCR
    assert report.all_pages_have_extractable_text
    with pikepdf.open(output, attempt_recovery=False, suppress_warnings=True) as document:
        assert len(document.pages) == 1
        assert float(document.pages[0].MediaBox[2]) == pytest.approx(40 * 72 / 300)
        assert float(document.pages[0].MediaBox[3]) == pytest.approx(80 * 72 / 300)
    assert not tuple(tmp_path.glob("pws-searchable-page-*"))


def test_a4_contrast_only_transform_preserves_physical_page_size(tmp_path: Path) -> None:
    source = (tmp_path / "a4.pdf").resolve()
    _write_blank_pdf(source, media_box=(0.0, 0.0, 595.0, 842.0))
    plan, paths = _plan_with_transform(source)

    output, report, _checker = _run(tmp_path, plan, paths, _SuccessfulOcr())

    assert report.page_outcomes[0].outcome is SearchablePdfPageOutcomeCode.OCR_TEXT_VERIFIED
    with pikepdf.open(output, attempt_recovery=False, suppress_warnings=True) as document:
        assert tuple(map(float, document.pages[0].MediaBox)) == (0.0, 0.0, 595.0, 842.0)


@pytest.mark.parametrize(
    ("quarter_turns_clockwise", "expected_size"),
    (
        (0, (100.0, 200.0)),
        (1, (200.0, 100.0)),
        (2, (100.0, 200.0)),
        (3, (200.0, 100.0)),
    ),
)
def test_pdf_crop_user_unit_and_all_quarter_turns_preserve_physical_geometry(
    tmp_path: Path,
    quarter_turns_clockwise: int,
    expected_size: tuple[float, float],
) -> None:
    source = (tmp_path / f"crop-turn-{quarter_turns_clockwise}.pdf").resolve()
    _write_blank_pdf(
        source,
        media_box=(0.0, 0.0, 300.0, 200.0),
        crop_box=(10.0, 20.0, 210.0, 120.0),
        user_unit=2.0,
        rotation=90,
    )
    crop = NormalizedPageBox(250_000, 100_000, 500_000, 500_000)
    plan, paths = _plan_with_transform(
        source,
        crop=crop,
        quarter_turns_clockwise=quarter_turns_clockwise,
    )

    output, report, _checker = _run(tmp_path, plan, paths, _SuccessfulOcr())

    assert report.page_outcomes[0].outcome is SearchablePdfPageOutcomeCode.OCR_TEXT_VERIFIED
    with pikepdf.open(output, attempt_recovery=False, suppress_warnings=True) as document:
        media_box = tuple(map(float, document.pages[0].MediaBox))
        assert media_box[2] - media_box[0] == pytest.approx(expected_size[0])
        assert media_box[3] - media_box[1] == pytest.approx(expected_size[1])


def test_ocr_candidate_physical_geometry_change_is_rejected(tmp_path: Path) -> None:
    source = (tmp_path / "a4.pdf").resolve()
    _write_blank_pdf(source, media_box=(0.0, 0.0, 595.0, 842.0))
    plan, paths = _plan_with_transform(source)
    output = (tmp_path / "output.pdf").resolve()

    with pytest.raises(PdfExportError) as raised:
        SearchablePdfExportAdapter(
            ocr_processor=cast(Any, _GeometryChangingOcr()),
            qpdf_checker=_CleanChecker(),
            renderer=PdfiumPageRenderer(),
        ).assemble_and_verify(
            plan,
            paths,
            output,
            tmp_path.resolve(),
            languages=("eng",),
            limits=_limits(),
            control=_control(),
        )

    assert raised.value.code is PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH
    assert raised.value.context == "export.ocr_geometry_mismatch"
    assert not output.exists()


def test_final_output_physical_geometry_change_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = (PDF_FIXTURES / "born-digital.pdf").resolve()
    plan, paths = _plan(source, (0,))
    output = (tmp_path / "output.pdf").resolve()
    save_output = searchable_adapter._save_output

    def save_with_changed_geometry(
        document: pikepdf.Pdf,
        output_path: Path,
        limits: SearchablePdfExportAdapterLimits,
        control: SearchablePdfExportAdapterControl,
    ) -> None:
        assert output_path == output
        save_output(document, output_path, limits, control)
        payload = output.read_bytes()
        with pikepdf.open(
            io.BytesIO(payload),
            attempt_recovery=False,
            suppress_warnings=True,
        ) as document:
            page = document.pages[0]
            media_box = tuple(map(float, page.MediaBox))
            page.MediaBox = pikepdf.Array(
                [media_box[0], media_box[1], media_box[2] * 2, media_box[3] * 2]
            )
            page.CropBox = pikepdf.Array(
                [media_box[0], media_box[1], media_box[2] * 2, media_box[3] * 2]
            )
            document.save(output, deterministic_id=True)

    monkeypatch.setattr(searchable_adapter, "_save_output", save_with_changed_geometry)

    with pytest.raises(PdfExportError) as raised:
        SearchablePdfExportAdapter(
            ocr_processor=cast(Any, _NeverOcr()),
            qpdf_checker=_CleanChecker(),
            renderer=PdfiumPageRenderer(),
        ).assemble_and_verify(
            plan,
            paths,
            output,
            tmp_path.resolve(),
            languages=("eng",),
            limits=_limits(),
            control=_control(),
        )

    assert raised.value.code is PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH
    assert raised.value.context == "export.geometry_mismatch"
    assert not output.exists()


def test_limits_prove_each_pre_ocr_artifact_pair_fits_aggregate_scratch() -> None:
    limits = _limits()
    maximum_source_raster_bytes = max(
        limits.image_normalization.maximum_output_bytes,
        limits.image_transform.maximum_source_pixels * 4,
    )
    maximum_pre_ocr_bytes = max(
        maximum_source_raster_bytes + limits.image_transform.maximum_output_bytes,
        limits.image_transform.maximum_output_bytes + limits.raster_pdf.maximum_output_bytes,
    )

    with pytest.raises(PdfExportError) as raised:
        replace(
            limits,
            maximum_page_scratch_bytes=maximum_pre_ocr_bytes - 1,
        )

    assert raised.value.code is PdfExportErrorCode.MODEL_INVALID
    assert raised.value.context == "export.searchable_limits"


def test_actual_pre_ocr_image_tree_is_bounded_before_processor_starts(
    tmp_path: Path,
) -> None:
    source = (PROJECT_ROOT / "tests/fixtures/synthetic/v1/image/ocr-raster-text.png").resolve()
    plan, paths = _image_plan(source)
    limits = _limits()
    # Simulate a corrupted boundary object after construction. The adapter
    # independently authenticates each exact pairwise tree before OCR starts.
    object.__setattr__(limits, "maximum_page_scratch_bytes", 1)
    ocr = _NeverOcr()
    output = (tmp_path / "output.pdf").resolve()

    with pytest.raises(PdfExportError) as raised:
        SearchablePdfExportAdapter(
            ocr_processor=cast(Any, ocr),
            qpdf_checker=_CleanChecker(),
            renderer=PdfiumPageRenderer(),
        ).assemble_and_verify(
            plan,
            paths,
            output,
            tmp_path.resolve(),
            languages=("eng",),
            limits=limits,
            control=_control(),
        )

    assert raised.value.code is PdfExportErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "export.page_scratch_bytes"
    assert ocr.calls == 0
    assert not output.exists()
    assert not tuple(tmp_path.glob("pws-searchable-page-*"))


def test_ocr_monitors_operation_root_after_superseded_image_rasters_are_removed(
    tmp_path: Path,
) -> None:
    source = (PROJECT_ROOT / "tests/fixtures/synthetic/v1/image/ocr-raster-text.png").resolve()
    plan, paths = _image_plan(source)
    ocr = _ScratchObservingRecognitionFailureOcr()

    output, report, _checker = _run(tmp_path, plan, paths, ocr)

    assert output.exists()
    assert report.page_outcomes[0].outcome is SearchablePdfPageOutcomeCode.OCR_FAILED_RETAINED
    assert ocr.calls == 1
    assert ocr.temporary_directories == [tmp_path.resolve()]
    assert ocr.maximum_temporary_storage_bytes == [_limits().maximum_ocr_temporary_storage_bytes]
    assert ocr.page_entries == [("base.pdf",)]
    assert not tuple(tmp_path.glob("pws-searchable-page-*"))


@pytest.mark.parametrize(
    ("artifact_name", "source_kind"),
    (
        ("normalized.rgba", "image"),
        ("transformed.rgba", "image"),
        ("pdf-source.rgba", "pdf"),
        ("pdf-transformed.rgba", "pdf"),
    ),
)
def test_superseded_raster_cleanup_failure_is_authoritative_and_page_tree_is_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact_name: str,
    source_kind: str,
) -> None:
    if source_kind == "image":
        source = (PROJECT_ROOT / "tests/fixtures/synthetic/v1/image/ocr-raster-text.png").resolve()
        plan, paths = _image_plan(source)
    else:
        source = (PDF_FIXTURES / "image-only.pdf").resolve()
        plan, paths = _plan_with_transform(source)
    output = (tmp_path / "output.pdf").resolve()
    ocr = _NeverOcr()
    original_unlink = Path.unlink

    def fail_selected_unlink(
        path: Path,
        missing_ok: bool = False,
    ) -> None:
        if path.name == artifact_name:
            raise PermissionError
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_selected_unlink)

    with pytest.raises(PdfExportError) as raised:
        SearchablePdfExportAdapter(
            ocr_processor=cast(Any, ocr),
            qpdf_checker=_CleanChecker(),
            renderer=PdfiumPageRenderer(),
        ).assemble_and_verify(
            plan,
            paths,
            output,
            tmp_path.resolve(),
            languages=("eng",),
            limits=_limits(),
            control=_control(),
        )

    assert raised.value.code is PdfExportErrorCode.WORKER_FAILED
    assert raised.value.context == "export.page_scratch_cleanup"
    assert ocr.calls == 0
    assert not output.exists()
    assert not tuple(tmp_path.glob("pws-searchable-page-*"))


def test_signed_or_unknown_textless_page_falls_back_without_invoking_ocr(
    tmp_path: Path,
) -> None:
    source = (PDF_FIXTURES / "image-only.pdf").resolve()
    for index, signature in enumerate(
        (
            SearchablePdfSourceSignatureState.MARKERS_DETECTED,
            SearchablePdfSourceSignatureState.UNKNOWN,
        )
    ):
        operation = tmp_path / str(index)
        operation.mkdir()
        plan, paths = _plan(source, (0,), signature=signature)
        ocr = _NeverOcr()

        _output, report, _checker = _run(operation, plan, paths, ocr)

        assert ocr.calls == 0
        assert report.base_report.unsigned_derivative
        assert (
            report.page_outcomes[0].outcome
            is SearchablePdfPageOutcomeCode.OCR_BLOCKED_SIGNED_SOURCE_RETAINED
        )


def test_adapter_rechecks_signed_pixel_transform_before_native_work(
    tmp_path: Path,
) -> None:
    source = (PDF_FIXTURES / "image-only.pdf").resolve()
    clear_plan, paths = _plan(source, (0,))
    # Construct an impossible worker-boundary state without weakening the domain
    # constructor: the adapter must still fail before render, transform, or OCR.
    signed_source = replace(
        clear_plan.sources[0],
        signature_state=SearchablePdfSourceSignatureState.UNKNOWN,
    )
    transformed_page = replace(
        clear_plan.pages[0],
        deskew_angle_millidegrees_clockwise=100,
    )
    object.__setattr__(clear_plan, "sources", (signed_source,))
    object.__setattr__(clear_plan, "pages", (transformed_page,))
    ocr = _NeverOcr()
    output = (tmp_path / "output.pdf").resolve()

    with pytest.raises(PdfExportError) as raised:
        SearchablePdfExportAdapter(
            ocr_processor=cast(Any, ocr),
            qpdf_checker=_CleanChecker(),
        ).assemble_and_verify(
            clear_plan,
            paths,
            output,
            tmp_path.resolve(),
            languages=("eng",),
            limits=_limits(),
            control=_control(),
        )

    assert raised.value.code is PdfExportErrorCode.TRANSFORM_UNSUPPORTED
    assert ocr.calls == 0
    assert not output.exists()


def test_nonrecognition_ocr_failure_aborts_and_removes_output(tmp_path: Path) -> None:
    source = (PDF_FIXTURES / "image-only.pdf").resolve()
    plan, paths = _plan(source, (0,))
    output = (tmp_path / "output.pdf").resolve()

    with pytest.raises(OcrError) as raised:
        SearchablePdfExportAdapter(
            ocr_processor=cast(Any, _FatalOcr()),
            qpdf_checker=_CleanChecker(),
        ).assemble_and_verify(
            plan,
            paths,
            output,
            tmp_path.resolve(),
            languages=("eng",),
            limits=_limits(),
            control=_control(),
        )

    assert raised.value.code is OcrErrorCode.LANGUAGE_UNAVAILABLE
    assert not output.exists()


def test_final_render_mismatch_blocks_candidate(tmp_path: Path) -> None:
    source = (PDF_FIXTURES / "born-digital.pdf").resolve()
    plan, paths = _plan(source, (0,))

    class _TamperingChecker(_CleanChecker):
        def check(self, *args: object, **_kwargs: object) -> PdfStructuralState:
            self.calls += 1
            source_path = args[0]
            assert isinstance(source_path, Path)
            with pikepdf.open(source_path) as document:
                document.pages[0].obj.Rotate = 90
                replacement = source_path.with_name("tampered.pdf")
                document.save(replacement)
            replacement.replace(source_path)
            return PdfStructuralState.CLEAN

    output = (tmp_path / "output.pdf").resolve()
    with pytest.raises(PdfExportError) as raised:
        SearchablePdfExportAdapter(
            ocr_processor=cast(Any, _NeverOcr()),
            qpdf_checker=_TamperingChecker(),
        ).assemble_and_verify(
            plan,
            paths,
            output,
            tmp_path.resolve(),
            languages=("eng",),
            limits=_limits(),
            control=_control(),
        )

    assert raised.value.code is PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH
    assert not output.exists()

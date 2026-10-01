"""Explicit image policy preserves selection, floor, color and numeric evidence."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from paperworkshell.domain.image_pages import MAX_IMAGE_DIMENSION_PIXELS
from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.domain.pdf_image_stages import (
    PdfImageAnalysis,
    PdfImageAssetAnalysis,
    PdfImageAssetImpact,
    PdfImageColorMode,
    PdfImageEligibility,
    PdfImagePageAnalysis,
    PdfImagePageImpact,
    PdfImageStageOutcome,
    PdfImageStageProfile,
    PdfImageStageReport,
    PdfImageStageRequest,
    stage_image_geometry,
    validate_pdf_image_stage_report,
    validate_pdf_image_stage_request,
)

_BASELINE = "a" * 64
_OUTPUT = "b" * 64
_SEMANTIC = "c" * 64
_PROFILE = PdfImageStageProfile.PRESERVE_300_DPI_Q90


def _asset() -> PdfImageAssetAnalysis:
    return PdfImageAssetAnalysis(
        asset_id=0,
        page_indexes=(0,),
        width_pixels=1_200,
        height_pixels=1_800,
        effective_dpi_x_milli=600_000,
        effective_dpi_y_milli=600_000,
        color_mode=PdfImageColorMode.RGB,
        encoded_byte_size=80_000,
        eligibility=PdfImageEligibility.ELIGIBLE,
    )


def _analysis() -> PdfImageAnalysis:
    return PdfImageAnalysis(
        baseline_sha256=_BASELINE,
        baseline_byte_size=90_000,
        page_count=2,
        assets=(_asset(),),
        pages=(
            PdfImagePageAnalysis(0, (0,), 80_000, 900_000),
            PdfImagePageAnalysis(1, (), 0, 0),
        ),
    )


def _request() -> PdfImageStageRequest:
    return PdfImageStageRequest(_PROFILE, (0,), _BASELINE, _SEMANTIC)


def _impact() -> PdfImageAssetImpact:
    return PdfImageAssetImpact(
        asset_id=0,
        page_indexes=(0,),
        original_width_pixels=1_200,
        original_height_pixels=1_800,
        output_width_pixels=600,
        output_height_pixels=900,
        original_effective_dpi_x_milli=600_000,
        original_effective_dpi_y_milli=600_000,
        output_effective_dpi_x_milli=300_000,
        output_effective_dpi_y_milli=300_000,
        color_mode=PdfImageColorMode.RGB,
        original_encoded_byte_size=80_000,
        estimated_encoded_byte_size=20_000,
        candidate_encoded_byte_size=25_000,
        selected_encoded_byte_size=25_000,
        outcome=PdfImageStageOutcome.REENCODED,
    )


def _report() -> PdfImageStageReport:
    return PdfImageStageReport(
        profile=_PROFILE,
        outcome=PdfImageStageOutcome.REENCODED,
        semantic_fingerprint=_SEMANTIC,
        baseline_sha256=_BASELINE,
        baseline_byte_size=90_000,
        candidate_byte_size=35_000,
        output_sha256=_OUTPUT,
        output_byte_size=35_000,
        page_count=2,
        assets=(_impact(),),
        pages=(
            PdfImagePageImpact(0, (0,), 80_000, 20_000, 25_000),
            PdfImagePageImpact(1, (), 0, 0, 0),
        ),
        verified_page_count=2,
    )


def _retained_impact(
    outcome: PdfImageStageOutcome = PdfImageStageOutcome.NO_REDUCTION,
) -> PdfImageAssetImpact:
    return replace(
        _impact(),
        output_width_pixels=1_200,
        output_height_pixels=1_800,
        output_effective_dpi_x_milli=600_000,
        output_effective_dpi_y_milli=600_000,
        candidate_encoded_byte_size=(
            25_000 if outcome is PdfImageStageOutcome.NO_REDUCTION else None
        ),
        selected_encoded_byte_size=80_000,
        outcome=outcome,
    )


def test_analysis_only_is_default_and_cannot_authorize_mutation() -> None:
    request = PdfImageStageRequest()
    assert request.analysis_only
    validate_pdf_image_stage_request(request, _analysis(), _SEMANTIC)
    report = replace(
        _report(),
        profile=None,
        outcome=PdfImageStageOutcome.UNCHANGED,
        candidate_byte_size=None,
        output_sha256=_BASELINE,
        output_byte_size=90_000,
        assets=(),
        pages=(
            PdfImagePageImpact(0, (), 80_000, 80_000, 80_000),
            PdfImagePageImpact(1, (), 0, 0, 0),
        ),
    )
    validate_pdf_image_stage_report(request, _analysis(), report)


def test_apply_request_and_report_bind_complete_selection_and_baseline() -> None:
    validate_pdf_image_stage_request(_request(), _analysis(), _SEMANTIC)
    validate_pdf_image_stage_report(_request(), _analysis(), _report())
    with pytest.raises(FrozenInstanceError):
        _request().profile = None  # type: ignore[misc]


def test_explicit_empty_selection_is_valid_noop() -> None:
    validate_pdf_image_stage_request(
        replace(_request(), selected_asset_ids=()), _analysis(), _SEMANTIC
    )


@pytest.mark.parametrize(
    ("profile", "dpi", "quality"),
    (
        (PdfImageStageProfile.PRESERVE_300_DPI_Q90, 300, 90),
        (PdfImageStageProfile.PRESERVE_240_DPI_Q85, 240, 85),
        (PdfImageStageProfile.PRESERVE_200_DPI_Q80, 200, 80),
    ),
)
def test_profiles_are_fixed_color_preserving_policies(
    profile: PdfImageStageProfile, dpi: int, quality: int
) -> None:
    assert (profile.target_dpi, profile.jpeg_quality) == (dpi, quality)
    assert profile.floor_dpi == 200
    assert profile.jpeg_subsampling == 0
    geometry = stage_image_geometry(_asset(), profile)
    assert geometry.width_pixels == 2 * dpi
    assert geometry.height_pixels == 3 * dpi
    assert geometry.effective_dpi_x_milli == geometry.effective_dpi_y_milli == dpi * 1_000


def test_geometry_ceil_rounding_preserves_floor_and_anisotropic_minimum() -> None:
    asset = replace(
        _asset(),
        width_pixels=1_001,
        height_pixels=1_503,
        effective_dpi_x_milli=600_001,
        effective_dpi_y_milli=400_003,
    )
    geometry = stage_image_geometry(asset, PdfImageStageProfile.PRESERVE_200_DPI_Q80)
    assert (geometry.width_pixels, geometry.height_pixels) == (501, 752)
    assert geometry.effective_dpi_x_milli >= 300_000
    assert geometry.effective_dpi_y_milli >= 200_000
    assert geometry.estimated_encoded_byte_size == (80_000 * 501 * 752 + 1_001 * 1_503 - 1) // (
        1_001 * 1_503
    )


def test_existing_lower_resolution_is_never_upscaled() -> None:
    asset = replace(_asset(), effective_dpi_x_milli=220_000, effective_dpi_y_milli=240_000)
    geometry = stage_image_geometry(asset, _PROFILE)
    assert (geometry.width_pixels, geometry.height_pixels) == (1_200, 1_800)
    assert (geometry.effective_dpi_x_milli, geometry.effective_dpi_y_milli) == (220_000, 240_000)
    assert geometry.estimated_encoded_byte_size == 80_000


def test_estimate_does_not_pretend_to_predict_jpeg_quality_or_whole_pdf_size() -> None:
    asset = replace(_asset(), effective_dpi_x_milli=200_000, effective_dpi_y_milli=200_000)
    assert {
        stage_image_geometry(asset, profile).estimated_encoded_byte_size
        for profile in PdfImageStageProfile
    } == {80_000}
    assert _report().pages[0].estimated_image_byte_size != _report().candidate_byte_size
    assert (
        _report().assets[0].estimated_encoded_byte_size
        != _report().assets[0].candidate_encoded_byte_size
    )


@pytest.mark.parametrize("candidate_byte_size", (None, 90_000, 100_000))
def test_whole_pdf_fallback_reports_attempt_separately_from_selected_output(
    candidate_byte_size: int | None,
) -> None:
    report = replace(
        _report(),
        outcome=PdfImageStageOutcome.NO_REDUCTION,
        candidate_byte_size=candidate_byte_size,
        output_sha256=_BASELINE,
        output_byte_size=90_000,
        assets=(_retained_impact(),),
        pages=(
            PdfImagePageImpact(0, (0,), 80_000, 20_000, 80_000),
            PdfImagePageImpact(1, (), 0, 0, 0),
        ),
    )
    validate_pdf_image_stage_report(_request(), _analysis(), report)
    assert report.assets[0].candidate_encoded_byte_size == 25_000
    assert report.assets[0].selected_encoded_byte_size == 80_000
    assert report.assets[0].output_width_pixels == 1_200


def test_budget_skip_has_no_candidate_and_keeps_exact_baseline() -> None:
    report = replace(
        _report(),
        outcome=PdfImageStageOutcome.SKIPPED_BUDGET,
        candidate_byte_size=None,
        output_sha256=_BASELINE,
        output_byte_size=90_000,
        assets=(_retained_impact(PdfImageStageOutcome.SKIPPED_BUDGET),),
        pages=(
            PdfImagePageImpact(0, (0,), 80_000, 20_000, 80_000),
            PdfImagePageImpact(1, (), 0, 0, 0),
        ),
    )
    validate_pdf_image_stage_report(_request(), _analysis(), report)


def test_shared_asset_is_counted_once_within_each_page_but_cannot_be_selected() -> None:
    asset = replace(_asset(), page_indexes=(0, 1), eligibility=PdfImageEligibility.SHARED)
    analysis = replace(
        _analysis(),
        assets=(asset,),
        pages=(
            PdfImagePageAnalysis(0, (0,), 80_000, None),
            PdfImagePageAnalysis(1, (0,), 80_000, None),
        ),
    )
    assert (
        sum(page.image_encoded_byte_size for page in analysis.pages) > analysis.baseline_byte_size
    )
    with pytest.raises(PdfExportError):
        validate_pdf_image_stage_request(_request(), analysis, _SEMANTIC)


@pytest.mark.parametrize(
    ("coverage", "expected"),
    ((None, None), (0, False), (499_999, False), (500_000, True), (1_000_000, True)),
)
def test_image_heavy_classification_keeps_unknown_and_exact_boundary_distinct(
    coverage: int | None, expected: bool | None
) -> None:
    assert PdfImagePageAnalysis(0, (0,), 80_000, coverage).image_heavy is expected


@pytest.mark.parametrize(
    "changes",
    (
        {"profile": "preserve_300_dpi_q90_v1"},
        {"profile": None},
        {"baseline_sha256": None},
        {"baseline_sha256": "PRIVATE_SOURCE_CANARY"},
        {"semantic_fingerprint": None},
        {"selected_asset_ids": (0, 0)},
        {"selected_asset_ids": (1, 0)},
        {"selected_asset_ids": (True,)},
        {"selected_asset_ids": [0]},
        {"request_version": 2},
        {"request_version": True},
    ),
)
def test_request_rejects_ambiguous_or_future_intent(changes: dict[str, object]) -> None:
    with pytest.raises(PdfExportError) as captured:
        replace(_request(), **changes)  # type: ignore[arg-type]
    assert "PRIVATE_SOURCE_CANARY" not in repr(captured.value)
    assert captured.value.__context__ is None


@pytest.mark.parametrize("field", ("baseline_sha256", "semantic_fingerprint"))
def test_stale_apply_fails_closed(field: str) -> None:
    request = (
        replace(_request(), baseline_sha256="d" * 64)
        if field == "baseline_sha256"
        else replace(_request(), semantic_fingerprint="d" * 64)
    )
    with pytest.raises(PdfExportError) as captured:
        validate_pdf_image_stage_request(request, _analysis(), _SEMANTIC)
    assert captured.value.code is PdfExportErrorCode.PLAN_STALE


def test_unknown_asset_id_is_not_silently_ignored() -> None:
    with pytest.raises(PdfExportError):
        validate_pdf_image_stage_request(
            replace(_request(), selected_asset_ids=(1,)), _analysis(), _SEMANTIC
        )


@pytest.mark.parametrize(
    "changes",
    (
        {"asset_id": True},
        {"page_indexes": ()},
        {"page_indexes": (0, 0)},
        {"page_indexes": (0, 1)},
        {"width_pixels": 0},
        {"width_pixels": MAX_IMAGE_DIMENSION_PIXELS + 1},
        {"encoded_byte_size": False},
        {"color_mode": PdfImageColorMode.UNSUPPORTED},
        {"color_mode": "rgb"},
        {"effective_dpi_x_milli": None},
        {"effective_dpi_x_milli": 199_999},
        {"eligibility": PdfImageEligibility.BELOW_FLOOR},
        {"eligibility": PdfImageEligibility.UNKNOWN_GEOMETRY},
        {"eligibility": PdfImageEligibility.SHARED},
    ),
)
def test_asset_rejects_contradictory_support_or_geometry(changes: dict[str, object]) -> None:
    with pytest.raises(PdfExportError):
        replace(_asset(), **changes)  # type: ignore[arg-type]


def test_unknown_geometry_and_below_floor_are_reportable_but_not_changeable() -> None:
    unsupported = (
        replace(
            _asset(),
            effective_dpi_x_milli=None,
            effective_dpi_y_milli=None,
            eligibility=PdfImageEligibility.UNKNOWN_GEOMETRY,
        ),
        replace(
            _asset(), effective_dpi_x_milli=199_999, eligibility=PdfImageEligibility.BELOW_FLOOR
        ),
        replace(
            _asset(),
            color_mode=PdfImageColorMode.UNSUPPORTED,
            eligibility=PdfImageEligibility.UNSUPPORTED,
        ),
    )
    for asset in unsupported:
        with pytest.raises(PdfExportError):
            stage_image_geometry(asset, _PROFILE)


@pytest.mark.parametrize(
    "changes",
    (
        {"report_version": 2},
        {"report_version": True},
        {"page_count": 1},
        {"assets": (_asset(), _asset())},
        {"assets": (replace(_asset(), asset_id=1),)},
        {"pages": (PdfImagePageAnalysis(0, (), 0, 0), PdfImagePageAnalysis(1, (), 0, 0))},
        {"pages": (PdfImagePageAnalysis(0, (0,), 1, 0), PdfImagePageAnalysis(1, (), 0, 0))},
    ),
)
def test_analysis_rejects_unbound_asset_and_page_counts(changes: dict[str, object]) -> None:
    with pytest.raises(PdfExportError):
        replace(_analysis(), **changes)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    (
        {"output_width_pixels": 1_201},
        {"output_effective_dpi_x_milli": 199_999},
        {"output_effective_dpi_x_milli": 400_000},
        {"selected_encoded_byte_size": 80_000, "candidate_encoded_byte_size": 80_000},
        {"candidate_encoded_byte_size": None},
        {"page_indexes": (0, 1)},
        {"outcome": PdfImageStageOutcome.NO_REDUCTION},
        {"color_mode": PdfImageColorMode.UNSUPPORTED},
    ),
)
def test_impact_cannot_claim_unmeasured_or_ineffective_changes(changes: dict[str, object]) -> None:
    with pytest.raises(PdfExportError):
        replace(_impact(), **changes)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    (
        {"profile": None},
        {"outcome": PdfImageStageOutcome.NO_REDUCTION},
        {"candidate_byte_size": None},
        {"output_byte_size": 90_000, "candidate_byte_size": 90_000},
        {"output_sha256": _BASELINE},
        {"verified_page_count": 1},
        {"verified_page_count": True},
        {"report_version": 2},
        {"report_version": True},
        {"assets": (_impact(), _impact())},
        {
            "pages": (
                PdfImagePageImpact(0, (), 80_000, 80_000, 80_000),
                PdfImagePageImpact(1, (), 0, 0, 0),
            )
        },
        {
            "pages": (
                PdfImagePageImpact(0, (0,), 80_000, 20_001, 25_000),
                PdfImagePageImpact(1, (), 0, 0, 0),
            )
        },
        {
            "pages": (
                PdfImagePageImpact(0, (0,), 80_000, 20_000, 25_001),
                PdfImagePageImpact(1, (), 0, 0, 0),
            )
        },
    ),
)
def test_report_rejects_contradictory_counts_or_claims(changes: dict[str, object]) -> None:
    with pytest.raises(PdfExportError):
        replace(_report(), **changes)  # type: ignore[arg-type]


def test_report_binding_rejects_color_conversion_or_different_policy() -> None:
    for report in (
        replace(_report(), assets=(replace(_impact(), color_mode=PdfImageColorMode.GRAYSCALE),)),
        replace(_report(), profile=PdfImageStageProfile.PRESERVE_200_DPI_Q80),
    ):
        with pytest.raises(PdfExportError):
            validate_pdf_image_stage_report(_request(), _analysis(), report)


def test_report_binding_rejects_changed_original_or_selection() -> None:
    changed_original = replace(_analysis(), baseline_byte_size=90_001)
    with pytest.raises(PdfExportError):
        validate_pdf_image_stage_report(_request(), changed_original, _report())
    with pytest.raises(PdfExportError):
        validate_pdf_image_stage_report(
            replace(_request(), selected_asset_ids=()), _analysis(), _report()
        )


def test_report_binding_rejects_valid_geometry_from_different_profile() -> None:
    impact = replace(
        _impact(),
        output_width_pixels=400,
        output_height_pixels=600,
        output_effective_dpi_x_milli=200_000,
        output_effective_dpi_y_milli=200_000,
    )
    with pytest.raises(PdfExportError):
        validate_pdf_image_stage_report(
            _request(), _analysis(), replace(_report(), assets=(impact,))
        )


def test_digest_fields_are_not_exposed_by_routine_representations() -> None:
    text = repr((_analysis(), _request(), _report()))
    assert all(value not in text for value in (_BASELINE, _OUTPUT, _SEMANTIC))

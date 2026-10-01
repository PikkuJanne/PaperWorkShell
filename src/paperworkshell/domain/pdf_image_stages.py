"""Explicit, bounded image-stage intent and content-free PDF evidence.

Per-page byte counts describe unique referenced image streams. A shared stream
appears in every referencing page, so those counts are not a PDF size estimate.
The fixed resolution floor is an engineering limit, not a legibility guarantee.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from paperworkshell.domain.image_pages import MAX_IMAGE_DIMENSION_PIXELS, MAX_IMAGE_SOURCE_PIXELS
from paperworkshell.domain.manifest_repository import MAX_SAFE_MANIFEST_INTEGER
from paperworkshell.domain.page_operations import MAX_PAGE_COUNT
from paperworkshell.domain.pdf_export import PdfExportErrorCode, reject_pdf_export
from paperworkshell.domain.pdf_inspection import MAX_PDF_INSPECTION_OBJECTS

PDF_IMAGE_ANALYSIS_VERSION: Final = 1
PDF_IMAGE_STAGE_REQUEST_VERSION: Final = 1
PDF_IMAGE_STAGE_REPORT_VERSION: Final = 1
PDF_IMAGE_STAGE_PROFILE_VERSION: Final = 1
PDF_IMAGE_STAGE_FLOOR_DPI: Final = 200
PDF_IMAGE_HEAVY_MIN_COVERAGE_PPM: Final = 500_000
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")
_PPM: Final = 1_000_000


class PdfImageStageProfile(StrEnum):
    """Versioned color-preserving JPEG policies; none is a default export policy."""

    PRESERVE_300_DPI_Q90 = "preserve_300_dpi_q90_v1"
    PRESERVE_240_DPI_Q85 = "preserve_240_dpi_q85_v1"
    PRESERVE_200_DPI_Q80 = "preserve_200_dpi_q80_v1"

    @property
    def target_dpi(self) -> int:
        return {
            self.PRESERVE_300_DPI_Q90: 300,
            self.PRESERVE_240_DPI_Q85: 240,
            self.PRESERVE_200_DPI_Q80: 200,
        }[self]

    @property
    def jpeg_quality(self) -> int:
        return {
            self.PRESERVE_300_DPI_Q90: 90,
            self.PRESERVE_240_DPI_Q85: 85,
            self.PRESERVE_200_DPI_Q80: 80,
        }[self]

    @property
    def floor_dpi(self) -> int:
        return PDF_IMAGE_STAGE_FLOOR_DPI

    @property
    def jpeg_subsampling(self) -> int:
        """Pillow's explicit 4:4:4 setting; grayscale stays grayscale."""

        return 0


class PdfImageColorMode(StrEnum):
    RGB = "rgb"
    GRAYSCALE = "grayscale"
    UNSUPPORTED = "unsupported"


class PdfImageEligibility(StrEnum):
    ELIGIBLE = "eligible"
    UNSUPPORTED = "unsupported"
    SHARED = "shared"
    UNKNOWN_GEOMETRY = "unknown_geometry"
    BELOW_FLOOR = "below_floor"


class PdfImageStageOutcome(StrEnum):
    REENCODED = "reencoded"
    NO_REDUCTION = "no_reduction"
    SKIPPED_BUDGET = "skipped_budget"
    UNCHANGED = "unchanged"


def _invalid(context: str) -> None:
    reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, f"image_stage.{context}")


def _integer(value: object, *, maximum: int = MAX_SAFE_MANIFEST_INTEGER, minimum: int = 0) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _digest(value: object) -> bool:
    return type(value) is str and _SHA256.fullmatch(value) is not None


def _indexes(value: object, *, maximum: int, empty: bool = True) -> bool:
    return (
        type(value) is tuple
        and len(value) <= maximum
        and (empty or len(value) > 0)
        and all(_integer(item, maximum=maximum - 1) for item in value)
        and tuple(sorted(set(value))) == value
    )


@dataclass(frozen=True, slots=True)
class PdfImageAssetAnalysis:
    """One baseline-local image ordinal, never a raw PDF name or object number."""

    asset_id: int
    page_indexes: tuple[int, ...]
    width_pixels: int
    height_pixels: int
    effective_dpi_x_milli: int | None
    effective_dpi_y_milli: int | None
    color_mode: PdfImageColorMode
    encoded_byte_size: int
    eligibility: PdfImageEligibility

    def __post_init__(self) -> None:
        if (
            not _integer(self.asset_id, maximum=MAX_PDF_INSPECTION_OBJECTS - 1)
            or not _indexes(self.page_indexes, maximum=MAX_PAGE_COUNT, empty=False)
            or not _integer(self.width_pixels, minimum=1)
            or not _integer(self.height_pixels, minimum=1)
            or not _integer(self.encoded_byte_size, minimum=1)
            or type(self.color_mode) is not PdfImageColorMode
            or type(self.eligibility) is not PdfImageEligibility
            or any(
                value is not None and not _integer(value, minimum=1)
                for value in (self.effective_dpi_x_milli, self.effective_dpi_y_milli)
            )
            or (self.effective_dpi_x_milli is None) != (self.effective_dpi_y_milli is None)
            or (len(self.page_indexes) > 1) != (self.eligibility is PdfImageEligibility.SHARED)
        ):
            _invalid("asset")
        if self.eligibility is PdfImageEligibility.ELIGIBLE and (
            self.color_mode is PdfImageColorMode.UNSUPPORTED
            or self.effective_dpi_x_milli is None
            or self.effective_dpi_y_milli is None
            or min(self.effective_dpi_x_milli, self.effective_dpi_y_milli)
            < PDF_IMAGE_STAGE_FLOOR_DPI * 1_000
            or max(self.width_pixels, self.height_pixels) > MAX_IMAGE_DIMENSION_PIXELS
            or self.width_pixels * self.height_pixels > MAX_IMAGE_SOURCE_PIXELS
        ):
            _invalid("eligible_asset")
        if self.eligibility is PdfImageEligibility.BELOW_FLOOR and (
            self.effective_dpi_x_milli is None
            or self.effective_dpi_y_milli is None
            or min(self.effective_dpi_x_milli, self.effective_dpi_y_milli)
            >= PDF_IMAGE_STAGE_FLOOR_DPI * 1_000
        ):
            _invalid("floor")
        if self.eligibility is PdfImageEligibility.UNKNOWN_GEOMETRY and (
            self.effective_dpi_x_milli is not None or self.effective_dpi_y_milli is not None
        ):
            _invalid("unknown_geometry")


@dataclass(frozen=True, slots=True)
class PdfImagePageAnalysis:
    """Unique image references and largest known placement coverage for one page."""

    page_index: int
    asset_ids: tuple[int, ...]
    image_encoded_byte_size: int
    image_coverage_ppm: int | None

    def __post_init__(self) -> None:
        if (
            not _integer(self.page_index, maximum=MAX_PAGE_COUNT - 1)
            or not _indexes(self.asset_ids, maximum=MAX_PDF_INSPECTION_OBJECTS)
            or not _integer(self.image_encoded_byte_size)
            or (
                self.image_coverage_ppm is not None
                and not _integer(self.image_coverage_ppm, maximum=_PPM)
            )
            or (not self.asset_ids and self.image_encoded_byte_size != 0)
        ):
            _invalid("page")

    @property
    def image_heavy(self) -> bool | None:
        """Advisory largest-placement classification, never an exact area union."""

        if self.image_coverage_ppm is None:
            return None
        return self.image_coverage_ppm >= PDF_IMAGE_HEAVY_MIN_COVERAGE_PPM


@dataclass(frozen=True, slots=True)
class PdfImageAnalysis:
    baseline_sha256: str = field(repr=False)
    baseline_byte_size: int
    page_count: int
    assets: tuple[PdfImageAssetAnalysis, ...]
    pages: tuple[PdfImagePageAnalysis, ...]
    report_version: int = PDF_IMAGE_ANALYSIS_VERSION

    def __post_init__(self) -> None:
        if (
            not _digest(self.baseline_sha256)
            or not _integer(self.baseline_byte_size, minimum=1)
            or not _integer(self.page_count, minimum=1, maximum=MAX_PAGE_COUNT)
            or type(self.assets) is not tuple
            or len(self.assets) > MAX_PDF_INSPECTION_OBJECTS
            or any(type(asset) is not PdfImageAssetAnalysis for asset in self.assets)
            or type(self.pages) is not tuple
            or len(self.pages) != self.page_count
            or any(type(page) is not PdfImagePageAnalysis for page in self.pages)
            or type(self.report_version) is not int
            or self.report_version != PDF_IMAGE_ANALYSIS_VERSION
        ):
            _invalid("analysis")
        if tuple(asset.asset_id for asset in self.assets) != tuple(range(len(self.assets))):
            _invalid("asset_order")
        if tuple(page.page_index for page in self.pages) != tuple(range(self.page_count)):
            _invalid("page_order")
        if sum(len(asset.page_indexes) for asset in self.assets) > MAX_PDF_INSPECTION_OBJECTS:
            _invalid("reference_count")
        expected: list[list[int]] = [[] for _ in self.pages]
        for asset in self.assets:
            if asset.page_indexes[-1] >= self.page_count:
                _invalid("asset_pages")
            for page_index in asset.page_indexes:
                expected[page_index].append(asset.asset_id)
        for page, asset_ids in zip(self.pages, expected, strict=True):
            if page.asset_ids != tuple(asset_ids) or page.image_encoded_byte_size != sum(
                self.assets[asset_id].encoded_byte_size for asset_id in asset_ids
            ):
                _invalid("page_binding")


@dataclass(frozen=True, slots=True)
class PdfImageStageRequest:
    """No-profile requests analyze only; apply requests bind an analyzed baseline."""

    profile: PdfImageStageProfile | None = None
    selected_asset_ids: tuple[int, ...] = ()
    baseline_sha256: str | None = field(default=None, repr=False)
    semantic_fingerprint: str | None = field(default=None, repr=False)
    request_version: int = PDF_IMAGE_STAGE_REQUEST_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.request_version) is not int
            or self.request_version != PDF_IMAGE_STAGE_REQUEST_VERSION
            or not _indexes(self.selected_asset_ids, maximum=MAX_PDF_INSPECTION_OBJECTS)
        ):
            _invalid("request")
        if self.profile is None:
            if (
                self.selected_asset_ids
                or self.baseline_sha256 is not None
                or (self.semantic_fingerprint is not None)
            ):
                _invalid("analysis_request")
        elif (
            type(self.profile) is not PdfImageStageProfile
            or not _digest(self.baseline_sha256)
            or not _digest(self.semantic_fingerprint)
        ):
            _invalid("apply_request")

    @property
    def analysis_only(self) -> bool:
        return self.profile is None


@dataclass(frozen=True, slots=True)
class PdfImageStageGeometry:
    width_pixels: int
    height_pixels: int
    effective_dpi_x_milli: int
    effective_dpi_y_milli: int
    estimated_encoded_byte_size: int

    def __post_init__(self) -> None:
        if (
            not _integer(self.width_pixels, minimum=1, maximum=MAX_IMAGE_DIMENSION_PIXELS)
            or not _integer(self.height_pixels, minimum=1, maximum=MAX_IMAGE_DIMENSION_PIXELS)
            or self.width_pixels * self.height_pixels > MAX_IMAGE_SOURCE_PIXELS
            or not _integer(self.effective_dpi_x_milli, minimum=PDF_IMAGE_STAGE_FLOOR_DPI * 1_000)
            or not _integer(self.effective_dpi_y_milli, minimum=PDF_IMAGE_STAGE_FLOOR_DPI * 1_000)
            or not _integer(self.estimated_encoded_byte_size, minimum=1)
        ):
            _invalid("geometry")


def stage_image_geometry(
    asset: PdfImageAssetAnalysis, profile: PdfImageStageProfile
) -> PdfImageStageGeometry:
    """Ceil each scaled extent; never upscale or cross the fixed floor.

    The encoded-byte estimate is the original byte count times the pixel-count
    ratio, rounded up. It excludes JPEG-quality effects and PDF overhead, and can
    be either higher or lower than the eventual measured result.
    """

    if (
        type(asset) is not PdfImageAssetAnalysis
        or type(profile) is not PdfImageStageProfile
        or asset.eligibility is not PdfImageEligibility.ELIGIBLE
        or asset.effective_dpi_x_milli is None
        or asset.effective_dpi_y_milli is None
    ):
        _invalid("geometry_input")
    assert asset.effective_dpi_x_milli is not None
    assert asset.effective_dpi_y_milli is not None
    denominator = min(asset.effective_dpi_x_milli, asset.effective_dpi_y_milli)
    numerator = min(denominator, profile.target_dpi * 1_000)
    width = (asset.width_pixels * numerator + denominator - 1) // denominator
    height = (asset.height_pixels * numerator + denominator - 1) // denominator
    original_pixels = asset.width_pixels * asset.height_pixels
    estimate = (asset.encoded_byte_size * width * height + original_pixels - 1) // original_pixels
    return PdfImageStageGeometry(
        width,
        height,
        asset.effective_dpi_x_milli * width // asset.width_pixels,
        asset.effective_dpi_y_milli * height // asset.height_pixels,
        estimate,
    )


def validate_pdf_image_stage_request(
    request: PdfImageStageRequest, analysis: PdfImageAnalysis, semantic_fingerprint: str
) -> None:
    """Bind explicit intent to fresh baseline facts before mutating a candidate."""

    if (
        type(request) is not PdfImageStageRequest
        or type(analysis) is not PdfImageAnalysis
        or not _digest(semantic_fingerprint)
    ):
        _invalid("request_binding")
    if request.analysis_only:
        return
    if request.baseline_sha256 != analysis.baseline_sha256 or (
        request.semantic_fingerprint != semantic_fingerprint
    ):
        reject_pdf_export(PdfExportErrorCode.PLAN_STALE, "image_stage.baseline_binding")
    if any(
        asset_id >= len(analysis.assets)
        or analysis.assets[asset_id].eligibility is not PdfImageEligibility.ELIGIBLE
        for asset_id in request.selected_asset_ids
    ):
        _invalid("selection")


@dataclass(frozen=True, slots=True)
class PdfImageAssetImpact:
    """Selected output facts, with attempted candidate bytes kept separately."""

    asset_id: int
    page_indexes: tuple[int, ...]
    original_width_pixels: int
    original_height_pixels: int
    output_width_pixels: int
    output_height_pixels: int
    original_effective_dpi_x_milli: int
    original_effective_dpi_y_milli: int
    output_effective_dpi_x_milli: int
    output_effective_dpi_y_milli: int
    color_mode: PdfImageColorMode
    original_encoded_byte_size: int
    estimated_encoded_byte_size: int | None
    candidate_encoded_byte_size: int | None
    selected_encoded_byte_size: int
    outcome: PdfImageStageOutcome

    def __post_init__(self) -> None:
        if (
            not _integer(self.asset_id, maximum=MAX_PDF_INSPECTION_OBJECTS - 1)
            or not _indexes(self.page_indexes, maximum=MAX_PAGE_COUNT, empty=False)
            or len(self.page_indexes) != 1
            or any(
                not _integer(value, minimum=1, maximum=MAX_IMAGE_DIMENSION_PIXELS)
                for value in (
                    self.original_width_pixels,
                    self.original_height_pixels,
                    self.output_width_pixels,
                    self.output_height_pixels,
                )
            )
            or self.original_width_pixels * self.original_height_pixels > MAX_IMAGE_SOURCE_PIXELS
            or self.output_width_pixels > self.original_width_pixels
            or self.output_height_pixels > self.original_height_pixels
            or any(
                not _integer(value, minimum=PDF_IMAGE_STAGE_FLOOR_DPI * 1_000)
                for value in (
                    self.original_effective_dpi_x_milli,
                    self.original_effective_dpi_y_milli,
                    self.output_effective_dpi_x_milli,
                    self.output_effective_dpi_y_milli,
                )
            )
            or type(self.color_mode) is not PdfImageColorMode
            or self.color_mode is PdfImageColorMode.UNSUPPORTED
            or not _integer(self.original_encoded_byte_size, minimum=1)
            or not _integer(self.selected_encoded_byte_size, minimum=1)
            or any(
                value is not None and not _integer(value, minimum=1)
                for value in (self.estimated_encoded_byte_size, self.candidate_encoded_byte_size)
            )
            or type(self.outcome) is not PdfImageStageOutcome
        ):
            _invalid("asset_impact")
        if (
            self.output_effective_dpi_x_milli
            != self.original_effective_dpi_x_milli
            * self.output_width_pixels
            // self.original_width_pixels
            or self.output_effective_dpi_y_milli
            != self.original_effective_dpi_y_milli
            * self.output_height_pixels
            // self.original_height_pixels
        ):
            _invalid("impact_dpi")
        if self.outcome is PdfImageStageOutcome.REENCODED:
            if (
                self.candidate_encoded_byte_size is None
                or self.selected_encoded_byte_size != self.candidate_encoded_byte_size
                or self.selected_encoded_byte_size >= self.original_encoded_byte_size
            ):
                _invalid("impact_reduction")
        elif (
            self.selected_encoded_byte_size != self.original_encoded_byte_size
            or self.output_width_pixels != self.original_width_pixels
            or self.output_height_pixels != self.original_height_pixels
            or (
                self.outcome
                in (PdfImageStageOutcome.SKIPPED_BUDGET, PdfImageStageOutcome.UNCHANGED)
                and self.candidate_encoded_byte_size is not None
            )
        ):
            _invalid("impact_retained")


@dataclass(frozen=True, slots=True)
class PdfImagePageImpact:
    page_index: int
    selected_asset_ids: tuple[int, ...]
    original_image_byte_size: int
    estimated_image_byte_size: int | None
    actual_image_byte_size: int

    def __post_init__(self) -> None:
        if (
            not _integer(self.page_index, maximum=MAX_PAGE_COUNT - 1)
            or not _indexes(self.selected_asset_ids, maximum=MAX_PDF_INSPECTION_OBJECTS)
            or not _integer(self.original_image_byte_size)
            or not _integer(self.actual_image_byte_size)
            or self.actual_image_byte_size > self.original_image_byte_size
            or (
                self.estimated_image_byte_size is not None
                and not _integer(self.estimated_image_byte_size)
            )
        ):
            _invalid("page_impact")


@dataclass(frozen=True, slots=True)
class PdfImageStageReport:
    """Measured candidate selection, without source-pixel-equality claims."""

    profile: PdfImageStageProfile | None
    outcome: PdfImageStageOutcome
    semantic_fingerprint: str = field(repr=False)
    baseline_sha256: str = field(repr=False)
    baseline_byte_size: int
    candidate_byte_size: int | None
    output_sha256: str = field(repr=False)
    output_byte_size: int
    page_count: int
    assets: tuple[PdfImageAssetImpact, ...]
    pages: tuple[PdfImagePageImpact, ...]
    verified_page_count: int
    report_version: int = PDF_IMAGE_STAGE_REPORT_VERSION

    def __post_init__(self) -> None:
        if (
            (self.profile is not None and type(self.profile) is not PdfImageStageProfile)
            or type(self.outcome) is not PdfImageStageOutcome
            or not all(
                _digest(value)
                for value in (self.semantic_fingerprint, self.baseline_sha256, self.output_sha256)
            )
            or not _integer(self.baseline_byte_size, minimum=1)
            or not _integer(self.output_byte_size, minimum=1)
            or (
                self.candidate_byte_size is not None
                and not _integer(self.candidate_byte_size, minimum=1)
            )
            or not _integer(self.page_count, minimum=1, maximum=MAX_PAGE_COUNT)
            or type(self.verified_page_count) is not int
            or self.verified_page_count != self.page_count
            or type(self.assets) is not tuple
            or len(self.assets) > MAX_PDF_INSPECTION_OBJECTS
            or any(type(asset) is not PdfImageAssetImpact for asset in self.assets)
            or type(self.pages) is not tuple
            or len(self.pages) != self.page_count
            or any(type(page) is not PdfImagePageImpact for page in self.pages)
            or type(self.report_version) is not int
            or self.report_version != PDF_IMAGE_STAGE_REPORT_VERSION
        ):
            _invalid("report")
        asset_ids = tuple(asset.asset_id for asset in self.assets)
        if not _indexes(asset_ids, maximum=MAX_PDF_INSPECTION_OBJECTS) or tuple(
            page.page_index for page in self.pages
        ) != tuple(range(self.page_count)):
            _invalid("report_order")
        if self.profile is None and (
            self.outcome is not PdfImageStageOutcome.UNCHANGED or self.assets
        ):
            _invalid("analysis_report")
        if (not self.assets) != (self.outcome is PdfImageStageOutcome.UNCHANGED):
            _invalid("report_attempt")
        if self.outcome is PdfImageStageOutcome.SKIPPED_BUDGET and any(
            asset.outcome is not PdfImageStageOutcome.SKIPPED_BUDGET for asset in self.assets
        ):
            _invalid("report_budget")
        changed = any(asset.outcome is PdfImageStageOutcome.REENCODED for asset in self.assets)
        if self.outcome is PdfImageStageOutcome.REENCODED:
            if (
                self.profile is None
                or not changed
                or self.candidate_byte_size != self.output_byte_size
                or self.output_byte_size >= self.baseline_byte_size
                or self.output_sha256 == self.baseline_sha256
            ):
                _invalid("report_reduction")
        elif (
            changed
            or self.output_byte_size != self.baseline_byte_size
            or self.output_sha256 != self.baseline_sha256
            or (
                self.outcome
                in (PdfImageStageOutcome.SKIPPED_BUDGET, PdfImageStageOutcome.UNCHANGED)
                and self.candidate_byte_size is not None
            )
            or (
                self.candidate_byte_size is not None
                and self.candidate_byte_size < self.baseline_byte_size
            )
        ):
            _invalid("report_retained")
        by_page: list[list[PdfImageAssetImpact]] = [[] for _ in self.pages]
        for asset in self.assets:
            if asset.page_indexes[0] >= self.page_count:
                _invalid("impact_page")
            by_page[asset.page_indexes[0]].append(asset)
        for page, impacts in zip(self.pages, by_page, strict=True):
            if page.selected_asset_ids != tuple(asset.asset_id for asset in impacts):
                _invalid("impact_selection")
            original_selected = sum(asset.original_encoded_byte_size for asset in impacts)
            if original_selected > page.original_image_byte_size or (
                page.actual_image_byte_size
                != page.original_image_byte_size
                - original_selected
                + sum(asset.selected_encoded_byte_size for asset in impacts)
            ):
                _invalid("impact_total")
            estimates = tuple(asset.estimated_encoded_byte_size for asset in impacts)
            expected_estimate = (
                None
                if any(value is None for value in estimates)
                else page.original_image_byte_size
                - original_selected
                + sum(value for value in estimates if value is not None)
            )
            if page.estimated_image_byte_size != expected_estimate:
                _invalid("impact_estimate")


def validate_pdf_image_stage_report(
    request: PdfImageStageRequest, analysis: PdfImageAnalysis, report: PdfImageStageReport
) -> None:
    """Require complete intent, baseline, original facts and selected-stage binding."""

    if type(report) is not PdfImageStageReport:
        _invalid("report_binding")
    validate_pdf_image_stage_request(request, analysis, report.semantic_fingerprint)
    if (
        report.profile != request.profile
        or report.baseline_sha256 != analysis.baseline_sha256
        or report.baseline_byte_size != analysis.baseline_byte_size
        or report.page_count != analysis.page_count
        or tuple(asset.asset_id for asset in report.assets) != request.selected_asset_ids
    ):
        _invalid("report_binding")
    for page, original_page in zip(report.pages, analysis.pages, strict=True):
        if page.original_image_byte_size != original_page.image_encoded_byte_size:
            _invalid("report_page_binding")
    for impact in report.assets:
        original = analysis.assets[impact.asset_id]
        if (
            impact.page_indexes != original.page_indexes
            or impact.original_width_pixels != original.width_pixels
            or impact.original_height_pixels != original.height_pixels
            or impact.original_effective_dpi_x_milli != original.effective_dpi_x_milli
            or impact.original_effective_dpi_y_milli != original.effective_dpi_y_milli
            or impact.color_mode != original.color_mode
            or impact.original_encoded_byte_size != original.encoded_byte_size
        ):
            _invalid("report_asset_binding")
        assert request.profile is not None
        geometry = stage_image_geometry(original, request.profile)
        if impact.estimated_encoded_byte_size != geometry.estimated_encoded_byte_size:
            _invalid("report_estimate_binding")
        if impact.outcome is PdfImageStageOutcome.REENCODED and (
            impact.output_width_pixels != geometry.width_pixels
            or impact.output_height_pixels != geometry.height_pixels
            or impact.output_effective_dpi_x_milli != geometry.effective_dpi_x_milli
            or impact.output_effective_dpi_y_milli != geometry.effective_dpi_y_milli
        ):
            _invalid("report_geometry_binding")

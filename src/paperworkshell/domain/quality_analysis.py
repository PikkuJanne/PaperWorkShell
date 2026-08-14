"""Pure, bounded contracts for advisory page-quality review.

The module consumes only already validated, bounded RGBA8 page snapshots and
content-free OCR observations.  Findings are deterministic suggestions: they
cannot certify a page, complete a requirement, or mutate case state.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Never, Protocol, assert_never
from uuid import UUID

QUALITY_ANALYSIS_VERSION: Final = 1
QUALITY_ANALYSIS_PROFILE_VERSION: Final = 1
QUALITY_PAGE_SAMPLE_VERSION: Final = 1
QUALITY_FINDING_VERSION: Final = 1
QUALITY_PAGE_ANALYSIS_VERSION: Final = 1
QUALITY_ANALYSIS_PROGRESS_VERSION: Final = 1
QUALITY_ANALYSIS_PROFILE_ID: Final = "scan-quality-v1"
MAX_QUALITY_SAMPLE_EDGE_PIXELS: Final = 1_024
MAX_QUALITY_SAMPLE_PIXELS: Final = 1_048_576
MAX_QUALITY_FINDINGS_PER_PAGE: Final = 8
MAX_QUALITY_PAGE_POSITION: Final = 4_096
QUALITY_PARTS_PER_MILLION: Final = 1_000_000
_MAX_PROJECTION_RATIO_PPM: Final = 10_000_000_000

_PROFILE_TOKEN: Final = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
_MAX_PROFILE_ID_CHARACTERS: Final = 128
_CONTEXT_TOKEN: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")


class QualityAnalysisErrorCode(StrEnum):
    """Closed failures for the quality-analysis boundary."""

    MODEL_INVALID = "QUALITY_MODEL_INVALID"
    CANCELLED = "QUALITY_CANCELLED"
    TIMED_OUT = "QUALITY_TIMED_OUT"
    PREVIEW_FAILED = "QUALITY_PREVIEW_FAILED"
    LIMIT_EXCEEDED = "QUALITY_LIMIT_EXCEEDED"
    RESULT_INVALID = "QUALITY_RESULT_INVALID"
    INTERNAL_ERROR = "QUALITY_INTERNAL_ERROR"


_SAFE_ERROR_MESSAGES: Final[dict[QualityAnalysisErrorCode, str]] = {
    QualityAnalysisErrorCode.MODEL_INVALID: (
        "PaperWorkShell received invalid page-quality review data."
    ),
    QualityAnalysisErrorCode.CANCELLED: (
        "Page-quality review was cancelled. No review results were applied."
    ),
    QualityAnalysisErrorCode.TIMED_OUT: ("Page-quality review exceeded its configured time limit."),
    QualityAnalysisErrorCode.PREVIEW_FAILED: (
        "A page preview needed for quality review could not be produced safely."
    ),
    QualityAnalysisErrorCode.LIMIT_EXCEEDED: (
        "Page-quality review exceeded a configured safety limit."
    ),
    QualityAnalysisErrorCode.RESULT_INVALID: (
        "PaperWorkShell received an invalid page-quality review result."
    ),
    QualityAnalysisErrorCode.INTERNAL_ERROR: (
        "PaperWorkShell could not complete page-quality review safely."
    ),
}


@dataclass(slots=True, repr=False)
class QualityAnalysisError(ValueError):
    """Stable path-free failure with a closed user-safe message."""

    code: QualityAnalysisErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not QualityAnalysisErrorCode:
            self.code = QualityAnalysisErrorCode.INTERNAL_ERROR
        if type(self.context) is not str or _CONTEXT_TOKEN.fullmatch(self.context) is None:
            self.context = "quality.internal"
            self.code = QualityAnalysisErrorCode.INTERNAL_ERROR
        self.safe_message = _SAFE_ERROR_MESSAGES[self.code]
        ValueError.__init__(self, self.safe_message)

    def __repr__(self) -> str:
        return f"QualityAnalysisError(code={self.code.value!r}, context={self.context!r})"


def reject_quality_analysis(code: QualityAnalysisErrorCode, context: str) -> Never:
    """Raise one sanitized quality-analysis failure."""

    error = QualityAnalysisError(code, context)
    raise error from None


class QualityPageKind(StrEnum):
    """Closed source families used by truthful heuristic applicability rules."""

    PDF = "pdf"
    IMAGE = "image"


class QualityFindingQualifier(StrEnum):
    """Uncertainty labels permitted for automatic findings."""

    LIKELY = "likely"
    SUSPECTED = "suspected"
    NEEDS_REVIEW = "needs_review"


class QualityRemediation(StrEnum):
    """Closed user actions associated with review findings."""

    CHECK_SOURCE_PIXELS = "check_source_pixels"
    ZOOM_AND_COMPARE = "zoom_and_compare"
    CHECK_PAGE_EDGES = "check_page_edges"
    CONFIRM_PAGE_CONTENT = "confirm_page_content"
    REVIEW_ORIENTATION = "review_orientation"
    COMPARE_PAGES = "compare_pages"
    RETRY_OCR = "retry_ocr"
    VERIFY_OCR = "verify_ocr"


class QualityEvidenceKind(StrEnum):
    """Content-free integer evidence carried by one finding."""

    SOURCE_SHORT_EDGE_PIXELS = "source_short_edge_pixels"
    SOURCE_PIXEL_COUNT = "source_pixel_count"
    LAPLACIAN_MEAN_SQUARE = "laplacian_mean_square"
    BORDER_CONTACT_PPM = "border_contact_ppm"
    CENTRAL_INK_PPM = "central_ink_ppm"
    PROJECTION_RATIO_PPM = "projection_ratio_ppm"
    DHASH_HAMMING_DISTANCE = "dhash_hamming_distance"
    OCR_STATUS = "ocr_status"
    OCR_MEAN_CONFIDENCE_MILLI = "ocr_mean_confidence_milli"


class QualityFindingCode(StrEnum):
    """Closed, canonically ordered page-review signals."""

    LIKELY_LOW_RESOLUTION = "likely_low_resolution"
    LIKELY_BLUR = "likely_blur"
    SUSPECTED_CLIPPING = "suspected_clipping"
    LIKELY_BLANK = "likely_blank"
    SUSPECTED_SIDEWAYS_ORIENTATION = "suspected_sideways_orientation"
    SUSPECTED_DUPLICATE = "suspected_duplicate"
    OCR_FAILED = "ocr_failed"
    OCR_LOW_CONFIDENCE = "ocr_low_confidence"


_FINDING_EVIDENCE_KINDS: Final[dict[QualityFindingCode, frozenset[QualityEvidenceKind]]] = {
    QualityFindingCode.LIKELY_LOW_RESOLUTION: frozenset(
        {
            QualityEvidenceKind.SOURCE_SHORT_EDGE_PIXELS,
            QualityEvidenceKind.SOURCE_PIXEL_COUNT,
        }
    ),
    QualityFindingCode.LIKELY_BLUR: frozenset({QualityEvidenceKind.LAPLACIAN_MEAN_SQUARE}),
    QualityFindingCode.SUSPECTED_CLIPPING: frozenset({QualityEvidenceKind.BORDER_CONTACT_PPM}),
    QualityFindingCode.LIKELY_BLANK: frozenset({QualityEvidenceKind.CENTRAL_INK_PPM}),
    QualityFindingCode.SUSPECTED_SIDEWAYS_ORIENTATION: frozenset(
        {QualityEvidenceKind.PROJECTION_RATIO_PPM}
    ),
    QualityFindingCode.SUSPECTED_DUPLICATE: frozenset({QualityEvidenceKind.DHASH_HAMMING_DISTANCE}),
    QualityFindingCode.OCR_FAILED: frozenset({QualityEvidenceKind.OCR_STATUS}),
    QualityFindingCode.OCR_LOW_CONFIDENCE: frozenset(
        {QualityEvidenceKind.OCR_MEAN_CONFIDENCE_MILLI}
    ),
}


_FINDING_PRESENTATION: Final[
    dict[QualityFindingCode, tuple[QualityFindingQualifier, QualityRemediation, str, str]]
] = {
    QualityFindingCode.LIKELY_LOW_RESOLUTION: (
        QualityFindingQualifier.LIKELY,
        QualityRemediation.CHECK_SOURCE_PIXELS,
        "Likely low source resolution",
        "The source image is below a configured pixel threshold. Review it at the intended output size.",
    ),
    QualityFindingCode.LIKELY_BLUR: (
        QualityFindingQualifier.LIKELY,
        QualityRemediation.ZOOM_AND_COMPARE,
        "Likely blur",
        "This page may be blurry. Zoom in and compare important details with the unchanged source.",
    ),
    QualityFindingCode.SUSPECTED_CLIPPING: (
        QualityFindingQualifier.SUSPECTED,
        QualityRemediation.CHECK_PAGE_EDGES,
        "Suspected clipping",
        "Content may touch a page edge. Review every edge for missing information.",
    ),
    QualityFindingCode.LIKELY_BLANK: (
        QualityFindingQualifier.LIKELY,
        QualityRemediation.CONFIRM_PAGE_CONTENT,
        "Likely blank page",
        "This page appears mostly blank. Confirm whether it belongs in the packet.",
    ),
    QualityFindingCode.SUSPECTED_SIDEWAYS_ORIENTATION: (
        QualityFindingQualifier.SUSPECTED,
        QualityRemediation.REVIEW_ORIENTATION,
        "Suspected sideways orientation",
        "The page content may be sideways. Review its orientation before export.",
    ),
    QualityFindingCode.SUSPECTED_DUPLICATE: (
        QualityFindingQualifier.SUSPECTED,
        QualityRemediation.COMPARE_PAGES,
        "Suspected duplicate page",
        "This page and another page look similar. Compare both pages before removing anything.",
    ),
    QualityFindingCode.OCR_FAILED: (
        QualityFindingQualifier.NEEDS_REVIEW,
        QualityRemediation.RETRY_OCR,
        "OCR result needs review",
        "The limited OCR review probe did not produce a verified result for this page. "
        "Review the page and OCR language settings, then try again. Restart PaperWorkShell "
        "first if local OCR resources changed or could not be admitted.",
    ),
    QualityFindingCode.OCR_LOW_CONFIDENCE: (
        QualityFindingQualifier.NEEDS_REVIEW,
        QualityRemediation.VERIFY_OCR,
        "OCR confidence needs review",
        "The limited OCR review probe reported low aggregate confidence for this page. Compare later searchable text with the page before relying on it.",
    ),
}


class OcrReviewStatus(StrEnum):
    """Closed content-free terminal OCR observations for one exact page."""

    SUCCEEDED = "succeeded"
    NO_TEXT = "no_text"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class OcrReviewFact:
    """Page-correlated OCR counts with no recognized text or geometry."""

    page_id: UUID
    status: OcrReviewStatus
    recognized_word_count: int
    mean_confidence_milli: int | None

    def __post_init__(self) -> None:
        _require_uuid4(self.page_id, "quality.ocr_page")
        if (
            type(self.status) is not OcrReviewStatus
            or type(self.recognized_word_count) is not int
            or not 0 <= self.recognized_word_count <= 1_000_000
            or (
                self.mean_confidence_milli is not None
                and (
                    type(self.mean_confidence_milli) is not int
                    or not 0 <= self.mean_confidence_milli <= 100_000
                )
            )
        ):
            reject_quality_analysis(QualityAnalysisErrorCode.MODEL_INVALID, "quality.ocr_fact")
        if self.status is OcrReviewStatus.SUCCEEDED:
            if self.recognized_word_count < 1:
                reject_quality_analysis(
                    QualityAnalysisErrorCode.MODEL_INVALID, "quality.ocr_terminal"
                )
        elif self.recognized_word_count != 0 or self.mean_confidence_milli is not None:
            reject_quality_analysis(QualityAnalysisErrorCode.MODEL_INVALID, "quality.ocr_terminal")


@dataclass(frozen=True, slots=True, repr=False)
class QualityAnalysisProfile:
    """Versioned integer-only heuristic thresholds, separate from resource budgets."""

    profile_id: str = QUALITY_ANALYSIS_PROFILE_ID
    source_short_edge_pixels_min: int = 1_000
    source_pixel_count_min: int = 1_000_000
    ink_luma_max: int = 224
    blank_central_ink_ppm_max: int = 2_500
    blank_mean_darkness_ppm_max: int = 12_000
    blur_laplacian_mean_square_max: int = 500
    blur_minimum_ink_ppm: int = 10_000
    clipping_band_ppm: int = 20_000
    clipping_side_contact_ppm_min: int = 30_000
    clipping_band_ink_ppm_min: int = 50_000
    sideways_projection_ratio_ppm_min: int = 2_000_000
    duplicate_hamming_distance_max: int = 4
    duplicate_aspect_delta_ppm_max: int = 10_000
    duplicate_ink_delta_ppm_max: int = 20_000
    ocr_minimum_words_for_confidence: int = 3
    ocr_mean_confidence_milli_min: int = 70_000
    profile_version: int = QUALITY_ANALYSIS_PROFILE_VERSION
    _profile_sha256: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        integer_fields = (
            self.source_short_edge_pixels_min,
            self.source_pixel_count_min,
            self.ink_luma_max,
            self.blank_central_ink_ppm_max,
            self.blank_mean_darkness_ppm_max,
            self.blur_laplacian_mean_square_max,
            self.blur_minimum_ink_ppm,
            self.clipping_band_ppm,
            self.clipping_side_contact_ppm_min,
            self.clipping_band_ink_ppm_min,
            self.sideways_projection_ratio_ppm_min,
            self.duplicate_hamming_distance_max,
            self.duplicate_aspect_delta_ppm_max,
            self.duplicate_ink_delta_ppm_max,
            self.ocr_minimum_words_for_confidence,
            self.ocr_mean_confidence_milli_min,
            self.profile_version,
        )
        if (
            type(self.profile_id) is not str
            or len(self.profile_id) > _MAX_PROFILE_ID_CHARACTERS
            or _PROFILE_TOKEN.fullmatch(self.profile_id) is None
            or any(type(value) is not int for value in integer_fields)
            or not 1 <= self.source_short_edge_pixels_min <= 100_000
            or not 1 <= self.source_pixel_count_min <= 10_000_000_000
            or not 0 <= self.ink_luma_max <= 254
            or not 0 <= self.blank_central_ink_ppm_max <= QUALITY_PARTS_PER_MILLION
            or not 0 <= self.blank_mean_darkness_ppm_max <= QUALITY_PARTS_PER_MILLION
            or not 0 <= self.blur_laplacian_mean_square_max <= 1_000_000
            or not 0 <= self.blur_minimum_ink_ppm <= QUALITY_PARTS_PER_MILLION
            or not 1 <= self.clipping_band_ppm <= 250_000
            or not 0 <= self.clipping_side_contact_ppm_min <= QUALITY_PARTS_PER_MILLION
            or not 0 <= self.clipping_band_ink_ppm_min <= QUALITY_PARTS_PER_MILLION
            or not 1 <= self.sideways_projection_ratio_ppm_min <= 100_000_000
            or not 0 <= self.duplicate_hamming_distance_max <= 64
            or not 0 <= self.duplicate_aspect_delta_ppm_max <= QUALITY_PARTS_PER_MILLION
            or not 0 <= self.duplicate_ink_delta_ppm_max <= QUALITY_PARTS_PER_MILLION
            or not 1 <= self.ocr_minimum_words_for_confidence <= 1_000_000
            or not 0 <= self.ocr_mean_confidence_milli_min <= 100_000
            or self.profile_version != QUALITY_ANALYSIS_PROFILE_VERSION
        ):
            reject_quality_analysis(QualityAnalysisErrorCode.MODEL_INVALID, "quality.profile")
        document = {
            "profile_id": self.profile_id,
            "source_short_edge_pixels_min": self.source_short_edge_pixels_min,
            "source_pixel_count_min": self.source_pixel_count_min,
            "ink_luma_max": self.ink_luma_max,
            "blank_central_ink_ppm_max": self.blank_central_ink_ppm_max,
            "blank_mean_darkness_ppm_max": self.blank_mean_darkness_ppm_max,
            "blur_laplacian_mean_square_max": self.blur_laplacian_mean_square_max,
            "blur_minimum_ink_ppm": self.blur_minimum_ink_ppm,
            "clipping_band_ppm": self.clipping_band_ppm,
            "clipping_side_contact_ppm_min": self.clipping_side_contact_ppm_min,
            "clipping_band_ink_ppm_min": self.clipping_band_ink_ppm_min,
            "sideways_projection_ratio_ppm_min": self.sideways_projection_ratio_ppm_min,
            "duplicate_hamming_distance_max": self.duplicate_hamming_distance_max,
            "duplicate_aspect_delta_ppm_max": self.duplicate_aspect_delta_ppm_max,
            "duplicate_ink_delta_ppm_max": self.duplicate_ink_delta_ppm_max,
            "ocr_minimum_words_for_confidence": self.ocr_minimum_words_for_confidence,
            "ocr_mean_confidence_milli_min": self.ocr_mean_confidence_milli_min,
            "profile_version": self.profile_version,
        }
        payload = json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        object.__setattr__(self, "_profile_sha256", hashlib.sha256(payload).hexdigest())

    @property
    def profile_sha256(self) -> str:
        """Return the content identity of every threshold and semantic version."""

        return self._profile_sha256

    def __repr__(self) -> str:
        return (
            f"QualityAnalysisProfile(profile_id={self.profile_id!r}, "
            f"profile_version={self.profile_version!r})"
        )


DEFAULT_QUALITY_ANALYSIS_PROFILE: Final = QualityAnalysisProfile()


@dataclass(frozen=True, slots=True, repr=False)
class QualityPageSample:
    """One bounded opaque RGBA8 page plane plus exact optional source dimensions."""

    page_id: UUID
    page_position: int
    page_kind: QualityPageKind
    width_pixels: int
    height_pixels: int
    row_stride_bytes: int
    pixels: bytes = field(repr=False)
    source_width_pixels: int | None = None
    source_height_pixels: int | None = None
    sample_version: int = QUALITY_PAGE_SAMPLE_VERSION

    def __post_init__(self) -> None:
        _require_uuid4(self.page_id, "quality.sample_page")
        if (
            type(self.page_position) is not int
            or not 1 <= self.page_position <= MAX_QUALITY_PAGE_POSITION
            or type(self.page_kind) is not QualityPageKind
            or type(self.width_pixels) is not int
            or not 1 <= self.width_pixels <= MAX_QUALITY_SAMPLE_EDGE_PIXELS
            or type(self.height_pixels) is not int
            or not 1 <= self.height_pixels <= MAX_QUALITY_SAMPLE_EDGE_PIXELS
            or self.width_pixels * self.height_pixels > MAX_QUALITY_SAMPLE_PIXELS
            or type(self.row_stride_bytes) is not int
            or not self.width_pixels * 4 <= self.row_stride_bytes <= self.width_pixels * 4 + 4_096
            or type(self.pixels) is not bytes
            or len(self.pixels) != self.row_stride_bytes * self.height_pixels
            or type(self.sample_version) is not int
            or self.sample_version != QUALITY_PAGE_SAMPLE_VERSION
        ):
            reject_quality_analysis(QualityAnalysisErrorCode.MODEL_INVALID, "quality.sample")
        source_values = (self.source_width_pixels, self.source_height_pixels)
        if (source_values[0] is None) != (source_values[1] is None):
            reject_quality_analysis(QualityAnalysisErrorCode.MODEL_INVALID, "quality.source_size")
        if source_values[0] is not None:
            if self.page_kind is not QualityPageKind.IMAGE or any(
                type(value) is not int or not 1 <= value <= 100_000 for value in source_values
            ):
                reject_quality_analysis(
                    QualityAnalysisErrorCode.MODEL_INVALID, "quality.source_size"
                )
        for row in range(self.height_pixels):
            start = row * self.row_stride_bytes
            for offset in range(start + 3, start + self.width_pixels * 4, 4):
                if self.pixels[offset] != 255:
                    reject_quality_analysis(
                        QualityAnalysisErrorCode.MODEL_INVALID, "quality.sample_alpha"
                    )

    def __repr__(self) -> str:
        return (
            "QualityPageSample("
            f"page_id={self.page_id!r}, page_position={self.page_position!r}, "
            f"page_kind={self.page_kind!r}, width_pixels={self.width_pixels!r}, "
            f"height_pixels={self.height_pixels!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class QualityPageFingerprint:
    """Private bounded perceptual identity retained only during one review."""

    page_id: UUID
    page_position: int
    difference_hash: int = field(repr=False)
    aspect_ratio_ppm: int = field(repr=False)
    ink_fraction_ppm: int = field(repr=False)

    def __post_init__(self) -> None:
        _require_uuid4(self.page_id, "quality.fingerprint_page")
        if (
            type(self.page_position) is not int
            or not 1 <= self.page_position <= MAX_QUALITY_PAGE_POSITION
            or type(self.difference_hash) is not int
            or not 0 <= self.difference_hash < 1 << 64
            or type(self.aspect_ratio_ppm) is not int
            or not 1 <= self.aspect_ratio_ppm <= 1_024_000_000
            or type(self.ink_fraction_ppm) is not int
            or not 0 <= self.ink_fraction_ppm <= QUALITY_PARTS_PER_MILLION
        ):
            reject_quality_analysis(QualityAnalysisErrorCode.RESULT_INVALID, "quality.fingerprint")

    def __repr__(self) -> str:
        return (
            f"QualityPageFingerprint(page_id={self.page_id!r}, "
            f"page_position={self.page_position!r})"
        )


def _finding_evidence_is_consistent(
    code: QualityFindingCode,
    observed_value: int,
    threshold_value: int,
) -> bool:
    """Validate the comparison direction and closed terminal values for one signal."""

    if code in {
        QualityFindingCode.LIKELY_LOW_RESOLUTION,
        QualityFindingCode.LIKELY_BLUR,
        QualityFindingCode.OCR_LOW_CONFIDENCE,
    }:
        return observed_value < threshold_value
    if code in {
        QualityFindingCode.SUSPECTED_CLIPPING,
        QualityFindingCode.SUSPECTED_SIDEWAYS_ORIENTATION,
    }:
        return observed_value >= threshold_value
    if code in {
        QualityFindingCode.LIKELY_BLANK,
        QualityFindingCode.SUSPECTED_DUPLICATE,
    }:
        return observed_value <= threshold_value
    if code is QualityFindingCode.OCR_FAILED:
        return observed_value in {1, 2} and threshold_value == 0
    assert_never(code)


@dataclass(frozen=True, slots=True)
class QualityFinding:
    """One advisory, page-correlated finding with closed presentation text."""

    page_id: UUID
    page_position: int
    code: QualityFindingCode
    evidence_kind: QualityEvidenceKind
    observed_value: int
    threshold_value: int
    related_page_id: UUID | None = None
    related_page_position: int | None = None
    finding_version: int = QUALITY_FINDING_VERSION

    def __post_init__(self) -> None:
        _require_uuid4(self.page_id, "quality.finding_page")
        if (
            type(self.page_position) is not int
            or not 1 <= self.page_position <= MAX_QUALITY_PAGE_POSITION
            or type(self.code) is not QualityFindingCode
            or type(self.evidence_kind) is not QualityEvidenceKind
            or type(self.observed_value) is not int
            or not 0 <= self.observed_value <= 10_000_000_000
            or type(self.threshold_value) is not int
            or not 0 <= self.threshold_value <= 10_000_000_000
            or type(self.finding_version) is not int
            or self.finding_version != QUALITY_FINDING_VERSION
            or self.evidence_kind not in _FINDING_EVIDENCE_KINDS.get(self.code, frozenset())
            or not _finding_evidence_is_consistent(
                self.code,
                self.observed_value,
                self.threshold_value,
            )
        ):
            reject_quality_analysis(QualityAnalysisErrorCode.RESULT_INVALID, "quality.finding")
        related = self.related_page_id is not None or self.related_page_position is not None
        if self.code is QualityFindingCode.SUSPECTED_DUPLICATE:
            if (
                not related
                or self.related_page_id is None
                or self.related_page_position is None
                or self.related_page_id == self.page_id
                or self.related_page_position >= self.page_position
            ):
                reject_quality_analysis(
                    QualityAnalysisErrorCode.RESULT_INVALID, "quality.finding_duplicate"
                )
            _require_uuid4(self.related_page_id, "quality.finding_related")
            if not 1 <= self.related_page_position <= MAX_QUALITY_PAGE_POSITION:
                reject_quality_analysis(
                    QualityAnalysisErrorCode.RESULT_INVALID, "quality.finding_related"
                )
        elif related:
            reject_quality_analysis(
                QualityAnalysisErrorCode.RESULT_INVALID, "quality.finding_related"
            )

    @property
    def qualifier(self) -> QualityFindingQualifier:
        return _FINDING_PRESENTATION[self.code][0]

    @property
    def remediation(self) -> QualityRemediation:
        return _FINDING_PRESENTATION[self.code][1]

    @property
    def title(self) -> str:
        return _FINDING_PRESENTATION[self.code][2]

    @property
    def message(self) -> str:
        return _FINDING_PRESENTATION[self.code][3]


@dataclass(frozen=True, slots=True, repr=False)
class QualityPageAnalysis:
    """Deterministic per-page findings and private duplicate fingerprint."""

    page_id: UUID
    page_position: int
    findings: tuple[QualityFinding, ...]
    fingerprint: QualityPageFingerprint = field(repr=False)
    analysis_version: int = QUALITY_PAGE_ANALYSIS_VERSION

    def __post_init__(self) -> None:
        _require_uuid4(self.page_id, "quality.analysis_page")
        if (
            type(self.page_position) is not int
            or not 1 <= self.page_position <= MAX_QUALITY_PAGE_POSITION
            or type(self.findings) is not tuple
            or not 0 <= len(self.findings) <= MAX_QUALITY_FINDINGS_PER_PAGE
            or any(type(finding) is not QualityFinding for finding in self.findings)
            or type(self.fingerprint) is not QualityPageFingerprint
            or self.fingerprint.page_id != self.page_id
            or self.fingerprint.page_position != self.page_position
            or type(self.analysis_version) is not int
            or self.analysis_version != QUALITY_PAGE_ANALYSIS_VERSION
            or any(
                finding.page_id != self.page_id or finding.page_position != self.page_position
                for finding in self.findings
            )
            or tuple(self.findings) != tuple(sorted(self.findings, key=quality_finding_sort_key))
            or len({finding.code for finding in self.findings}) != len(self.findings)
        ):
            reject_quality_analysis(QualityAnalysisErrorCode.RESULT_INVALID, "quality.analysis")

    def __repr__(self) -> str:
        return (
            f"QualityPageAnalysis(page_id={self.page_id!r}, "
            f"page_position={self.page_position!r}, findings={self.findings!r})"
        )


class QualityAnalysisProgressPhase(StrEnum):
    """Content-free application phases for a sequential quality review."""

    PREVIEWING = "previewing"
    ANALYZING = "analyzing"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class QualityAnalysisProgress:
    """Bounded page-count progress with no document values."""

    phase: QualityAnalysisProgressPhase
    completed_pages: int
    total_pages: int
    progress_version: int = QUALITY_ANALYSIS_PROGRESS_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.phase) is not QualityAnalysisProgressPhase
            or type(self.completed_pages) is not int
            or type(self.total_pages) is not int
            or not 0 <= self.completed_pages <= self.total_pages <= MAX_QUALITY_PAGE_POSITION
            or self.total_pages < 1
            or type(self.progress_version) is not int
            or self.progress_version != QUALITY_ANALYSIS_PROGRESS_VERSION
            or (
                self.phase is QualityAnalysisProgressPhase.COMPLETE
                and self.completed_pages != self.total_pages
            )
        ):
            reject_quality_analysis(QualityAnalysisErrorCode.MODEL_INVALID, "quality.progress")


class QualityAnalysisProgressSink(Protocol):
    """Application callback for content-free sequential progress."""

    def __call__(self, progress: QualityAnalysisProgress, /) -> None: ...


def analyze_quality_page(
    sample: QualityPageSample,
    *,
    ocr_fact: OcrReviewFact | None = None,
    profile: QualityAnalysisProfile = DEFAULT_QUALITY_ANALYSIS_PROFILE,
) -> QualityPageAnalysis:
    """Analyze one bounded page plane without retaining or exposing its pixels."""

    if type(sample) is not QualityPageSample or type(profile) is not QualityAnalysisProfile:
        reject_quality_analysis(QualityAnalysisErrorCode.MODEL_INVALID, "quality.analyze")
    if ocr_fact is not None and (
        type(ocr_fact) is not OcrReviewFact or ocr_fact.page_id != sample.page_id
    ):
        reject_quality_analysis(QualityAnalysisErrorCode.MODEL_INVALID, "quality.ocr_binding")
    luma = _luma_plane(sample)
    pixel_count = sample.width_pixels * sample.height_pixels
    ink_count = sum(1 for value in luma if value <= profile.ink_luma_max)
    ink_ppm = _ppm(ink_count, pixel_count)
    central_ink_ppm = _central_ink_ppm(sample, luma, profile.ink_luma_max)
    darkness = sum(255 - value for value in luma)
    mean_darkness_ppm = _ppm(darkness, 255 * pixel_count)
    blank = (
        central_ink_ppm <= profile.blank_central_ink_ppm_max
        and mean_darkness_ppm <= profile.blank_mean_darkness_ppm_max
    )
    findings: list[QualityFinding] = []
    _append_low_resolution(findings, sample, profile)
    if blank:
        findings.append(
            _finding(
                sample,
                QualityFindingCode.LIKELY_BLANK,
                QualityEvidenceKind.CENTRAL_INK_PPM,
                central_ink_ppm,
                profile.blank_central_ink_ppm_max,
            )
        )
    else:
        if ink_ppm >= profile.blur_minimum_ink_ppm:
            laplacian = _laplacian_mean_square(sample, luma)
            if laplacian < profile.blur_laplacian_mean_square_max:
                findings.append(
                    _finding(
                        sample,
                        QualityFindingCode.LIKELY_BLUR,
                        QualityEvidenceKind.LAPLACIAN_MEAN_SQUARE,
                        laplacian,
                        profile.blur_laplacian_mean_square_max,
                    )
                )
        clipping = _clipping_evidence(sample, luma, profile)
        if clipping is not None:
            findings.append(
                _finding(
                    sample,
                    QualityFindingCode.SUSPECTED_CLIPPING,
                    QualityEvidenceKind.BORDER_CONTACT_PPM,
                    clipping,
                    profile.clipping_side_contact_ppm_min,
                )
            )
        if ink_ppm >= profile.blur_minimum_ink_ppm:
            projection_ratio = _sideways_projection_ratio(sample, luma, profile.ink_luma_max)
            if projection_ratio >= profile.sideways_projection_ratio_ppm_min:
                findings.append(
                    _finding(
                        sample,
                        QualityFindingCode.SUSPECTED_SIDEWAYS_ORIENTATION,
                        QualityEvidenceKind.PROJECTION_RATIO_PPM,
                        projection_ratio,
                        profile.sideways_projection_ratio_ppm_min,
                    )
                )
    if ocr_fact is not None:
        if ocr_fact.status is OcrReviewStatus.FAILED or (
            ocr_fact.status is OcrReviewStatus.NO_TEXT and not blank
        ):
            findings.append(
                _finding(
                    sample,
                    QualityFindingCode.OCR_FAILED,
                    QualityEvidenceKind.OCR_STATUS,
                    1 if ocr_fact.status is OcrReviewStatus.FAILED else 2,
                    0,
                )
            )
        elif (
            ocr_fact.recognized_word_count >= profile.ocr_minimum_words_for_confidence
            and ocr_fact.mean_confidence_milli is not None
            and ocr_fact.mean_confidence_milli < profile.ocr_mean_confidence_milli_min
        ):
            findings.append(
                _finding(
                    sample,
                    QualityFindingCode.OCR_LOW_CONFIDENCE,
                    QualityEvidenceKind.OCR_MEAN_CONFIDENCE_MILLI,
                    ocr_fact.mean_confidence_milli,
                    profile.ocr_mean_confidence_milli_min,
                )
            )
    findings.sort(key=quality_finding_sort_key)
    if len(findings) > MAX_QUALITY_FINDINGS_PER_PAGE:
        reject_quality_analysis(QualityAnalysisErrorCode.RESULT_INVALID, "quality.finding_count")
    fingerprint = QualityPageFingerprint(
        page_id=sample.page_id,
        page_position=sample.page_position,
        difference_hash=_difference_hash(sample, luma),
        aspect_ratio_ppm=_ppm(sample.width_pixels, sample.height_pixels),
        ink_fraction_ppm=ink_ppm,
    )
    return QualityPageAnalysis(
        page_id=sample.page_id,
        page_position=sample.page_position,
        findings=tuple(findings),
        fingerprint=fingerprint,
    )


def suspected_duplicate_finding(
    current: QualityPageFingerprint,
    prior: QualityPageFingerprint,
    *,
    profile: QualityAnalysisProfile = DEFAULT_QUALITY_ANALYSIS_PROFILE,
) -> QualityFinding | None:
    """Return one later-page suggestion when two private fingerprints are close."""

    if (
        type(current) is not QualityPageFingerprint
        or type(prior) is not QualityPageFingerprint
        or type(profile) is not QualityAnalysisProfile
        or current.page_id == prior.page_id
        or current.page_position == prior.page_position
    ):
        reject_quality_analysis(QualityAnalysisErrorCode.MODEL_INVALID, "quality.duplicate")
    aspect_delta = _relative_delta_ppm(current.aspect_ratio_ppm, prior.aspect_ratio_ppm)
    ink_delta = abs(current.ink_fraction_ppm - prior.ink_fraction_ppm)
    hamming = (current.difference_hash ^ prior.difference_hash).bit_count()
    if (
        aspect_delta > profile.duplicate_aspect_delta_ppm_max
        or ink_delta > profile.duplicate_ink_delta_ppm_max
        or hamming > profile.duplicate_hamming_distance_max
    ):
        return None
    later, earlier = (
        (current, prior) if current.page_position > prior.page_position else (prior, current)
    )
    return QualityFinding(
        page_id=later.page_id,
        page_position=later.page_position,
        code=QualityFindingCode.SUSPECTED_DUPLICATE,
        evidence_kind=QualityEvidenceKind.DHASH_HAMMING_DISTANCE,
        observed_value=hamming,
        threshold_value=profile.duplicate_hamming_distance_max,
        related_page_id=earlier.page_id,
        related_page_position=earlier.page_position,
    )


def quality_finding_sort_key(finding: QualityFinding) -> tuple[int, int, int]:
    """Return the canonical page/code/related-page ordering key."""

    if type(finding) is not QualityFinding:
        reject_quality_analysis(QualityAnalysisErrorCode.MODEL_INVALID, "quality.sort")
    code_order = tuple(QualityFindingCode).index(finding.code)
    return finding.page_position, code_order, finding.related_page_position or 0


def _require_uuid4(value: object, context: str) -> UUID:
    if type(value) is not UUID or value.version != 4:
        reject_quality_analysis(QualityAnalysisErrorCode.MODEL_INVALID, context)
    return value


def _ppm(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        return 0
    return (numerator * QUALITY_PARTS_PER_MILLION + denominator // 2) // denominator


def _relative_delta_ppm(first: int, second: int) -> int:
    return _ppm(abs(first - second), max(first, second))


def _luma_plane(sample: QualityPageSample) -> bytearray:
    plane = bytearray(sample.width_pixels * sample.height_pixels)
    target = 0
    for row in range(sample.height_pixels):
        start = row * sample.row_stride_bytes
        end = start + sample.width_pixels * 4
        for offset in range(start, end, 4):
            red, green, blue = sample.pixels[offset : offset + 3]
            plane[target] = (77 * red + 150 * green + 29 * blue + 128) >> 8
            target += 1
    return plane


def _central_ink_ppm(sample: QualityPageSample, luma: bytearray, ink_max: int) -> int:
    margin_x = min(16, max(1, (sample.width_pixels * 2 + 99) // 100))
    margin_y = min(16, max(1, (sample.height_pixels * 2 + 99) // 100))
    left = min(margin_x, sample.width_pixels // 2)
    top = min(margin_y, sample.height_pixels // 2)
    right = max(left + 1, sample.width_pixels - left)
    bottom = max(top + 1, sample.height_pixels - top)
    ink = 0
    total = 0
    for row in range(top, bottom):
        base = row * sample.width_pixels
        for column in range(left, right):
            total += 1
            if luma[base + column] <= ink_max:
                ink += 1
    return _ppm(ink, total)


def _laplacian_mean_square(sample: QualityPageSample, luma: bytearray) -> int:
    if sample.width_pixels < 3 or sample.height_pixels < 3:
        return 0
    total = 0
    count = 0
    width = sample.width_pixels
    for row in range(1, sample.height_pixels - 1):
        base = row * width
        for column in range(1, width - 1):
            index = base + column
            value = (
                4 * luma[index]
                - luma[index - 1]
                - luma[index + 1]
                - luma[index - width]
                - luma[index + width]
            )
            total += value * value
            count += 1
    return (total + count // 2) // count if count else 0


def _clipping_evidence(
    sample: QualityPageSample,
    luma: bytearray,
    profile: QualityAnalysisProfile,
) -> int | None:
    width = sample.width_pixels
    height = sample.height_pixels
    band_x = min(16, max(2, (width * profile.clipping_band_ppm + 999_999) // 1_000_000))
    band_y = min(16, max(2, (height * profile.clipping_band_ppm + 999_999) // 1_000_000))
    band_x = min(width, band_x)
    band_y = min(height, band_y)
    side_values: list[tuple[int, int]] = []
    top_contact = _ink_count_region(luma, width, 0, 1, 0, width, profile.ink_luma_max)
    top_band = _ink_count_region(luma, width, 0, band_y, 0, width, profile.ink_luma_max)
    side_values.append((_ppm(top_contact, width), _ppm(top_band, width * band_y)))
    bottom_contact = _ink_count_region(
        luma, width, height - 1, height, 0, width, profile.ink_luma_max
    )
    bottom_band = _ink_count_region(
        luma,
        width,
        height - band_y,
        height,
        0,
        width,
        profile.ink_luma_max,
    )
    side_values.append((_ppm(bottom_contact, width), _ppm(bottom_band, width * band_y)))
    left_contact = _ink_count_region(luma, width, 0, height, 0, 1, profile.ink_luma_max)
    left_band = _ink_count_region(luma, width, 0, height, 0, band_x, profile.ink_luma_max)
    side_values.append((_ppm(left_contact, height), _ppm(left_band, height * band_x)))
    right_contact = _ink_count_region(
        luma, width, 0, height, width - 1, width, profile.ink_luma_max
    )
    right_band = _ink_count_region(
        luma,
        width,
        0,
        height,
        width - band_x,
        width,
        profile.ink_luma_max,
    )
    side_values.append((_ppm(right_contact, height), _ppm(right_band, height * band_x)))
    candidates = [
        contact
        for contact, band in side_values
        if contact >= profile.clipping_side_contact_ppm_min
        and band >= profile.clipping_band_ink_ppm_min
    ]
    return max(candidates) if candidates else None


def _ink_count_region(
    luma: bytearray,
    width: int,
    row_start: int,
    row_end: int,
    column_start: int,
    column_end: int,
    ink_max: int,
) -> int:
    count = 0
    for row in range(row_start, row_end):
        base = row * width
        for column in range(column_start, column_end):
            if luma[base + column] <= ink_max:
                count += 1
    return count


def _sideways_projection_ratio(
    sample: QualityPageSample,
    luma: bytearray,
    ink_max: int,
) -> int:
    width = sample.width_pixels
    height = sample.height_pixels
    row_counts = [0] * height
    column_counts = [0] * width
    for row in range(height):
        base = row * width
        for column in range(width):
            if luma[base + column] <= ink_max:
                row_counts[row] += 1
                column_counts[column] += 1
    horizontal = _normalized_projection_variance(row_counts, width)
    vertical = _normalized_projection_variance(column_counts, height)
    return min(_MAX_PROJECTION_RATIO_PPM, _ppm(vertical, max(1, horizontal)))


def _normalized_projection_variance(values: list[int], maximum: int) -> int:
    if not values or maximum <= 0:
        return 0
    total = sum(values)
    count = len(values)
    numerator = sum((value * count - total) ** 2 for value in values)
    return _ppm(numerator, count * count * count * maximum * maximum)


def _difference_hash(sample: QualityPageSample, luma: bytearray) -> int:
    width = sample.width_pixels
    height = sample.height_pixels
    grid: list[int] = []
    for grid_y in range(8):
        y0 = grid_y * height // 8
        y1 = max(y0 + 1, (grid_y + 1) * height // 8)
        for grid_x in range(9):
            x0 = grid_x * width // 9
            x1 = max(x0 + 1, (grid_x + 1) * width // 9)
            total = 0
            count = 0
            for row in range(y0, min(y1, height)):
                base = row * width
                for column in range(x0, min(x1, width)):
                    total += luma[base + column]
                    count += 1
            grid.append((total + count // 2) // count if count else 255)
    result = 0
    for row in range(8):
        base = row * 9
        for column in range(8):
            result = (result << 1) | int(grid[base + column] > grid[base + column + 1])
    return result


def _append_low_resolution(
    findings: list[QualityFinding],
    sample: QualityPageSample,
    profile: QualityAnalysisProfile,
) -> None:
    if sample.source_width_pixels is None or sample.source_height_pixels is None:
        return
    short_edge = min(sample.source_width_pixels, sample.source_height_pixels)
    source_pixels = sample.source_width_pixels * sample.source_height_pixels
    if short_edge < profile.source_short_edge_pixels_min:
        findings.append(
            _finding(
                sample,
                QualityFindingCode.LIKELY_LOW_RESOLUTION,
                QualityEvidenceKind.SOURCE_SHORT_EDGE_PIXELS,
                short_edge,
                profile.source_short_edge_pixels_min,
            )
        )
    elif source_pixels < profile.source_pixel_count_min:
        findings.append(
            _finding(
                sample,
                QualityFindingCode.LIKELY_LOW_RESOLUTION,
                QualityEvidenceKind.SOURCE_PIXEL_COUNT,
                source_pixels,
                profile.source_pixel_count_min,
            )
        )


def _finding(
    sample: QualityPageSample,
    code: QualityFindingCode,
    evidence: QualityEvidenceKind,
    observed: int,
    threshold: int,
) -> QualityFinding:
    return QualityFinding(
        page_id=sample.page_id,
        page_position=sample.page_position,
        code=code,
        evidence_kind=evidence,
        observed_value=observed,
        threshold_value=threshold,
    )

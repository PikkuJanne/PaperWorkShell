"""Pure contract and deterministic-heuristic tests for PWS-0207."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast
from uuid import UUID

import pytest

from paperworkshell.domain.quality_analysis import (
    DEFAULT_QUALITY_ANALYSIS_PROFILE,
    MAX_QUALITY_FINDINGS_PER_PAGE,
    OcrReviewFact,
    OcrReviewStatus,
    QualityAnalysisError,
    QualityAnalysisErrorCode,
    QualityAnalysisProfile,
    QualityAnalysisProgress,
    QualityAnalysisProgressPhase,
    QualityEvidenceKind,
    QualityFinding,
    QualityFindingCode,
    QualityFindingQualifier,
    QualityPageAnalysis,
    QualityPageKind,
    QualityPageSample,
    QualityRemediation,
    analyze_quality_page,
    quality_finding_sort_key,
    reject_quality_analysis,
    suspected_duplicate_finding,
)

PAGE_ONE = UUID("31000000-0000-4000-8000-000000000001")
PAGE_TWO = UUID("31000000-0000-4000-8000-000000000002")
PAGE_THREE = UUID("31000000-0000-4000-8000-000000000003")


def _rgba_plane(
    width: int,
    height: int,
    pixel: tuple[int, int, int, int] = (255, 255, 255, 255),
    *,
    padding: int = 0,
    paint: dict[tuple[int, int], tuple[int, int, int, int]] | None = None,
    padding_byte: int = 0,
) -> tuple[int, bytes]:
    stride = width * 4 + padding
    rows = bytearray()
    painted = {} if paint is None else paint
    for row in range(height):
        for column in range(width):
            rows.extend(painted.get((column, row), pixel))
        rows.extend(bytes((padding_byte,)) * padding)
    return stride, bytes(rows)


def _sample(
    *,
    page_id: UUID = PAGE_ONE,
    page_position: int = 1,
    width: int = 64,
    height: int = 64,
    pixel: tuple[int, int, int, int] = (255, 255, 255, 255),
    padding: int = 0,
    paint: dict[tuple[int, int], tuple[int, int, int, int]] | None = None,
    page_kind: QualityPageKind = QualityPageKind.PDF,
    source_size: tuple[int, int] | None = None,
    padding_byte: int = 0,
) -> QualityPageSample:
    stride, pixels = _rgba_plane(
        width,
        height,
        pixel,
        padding=padding,
        paint=paint,
        padding_byte=padding_byte,
    )
    source_width, source_height = (None, None) if source_size is None else source_size
    return QualityPageSample(
        page_id=page_id,
        page_position=page_position,
        page_kind=page_kind,
        width_pixels=width,
        height_pixels=height,
        row_stride_bytes=stride,
        pixels=pixels,
        source_width_pixels=source_width,
        source_height_pixels=source_height,
    )


def _codes(analysis: QualityPageAnalysis) -> tuple[QualityFindingCode, ...]:
    return tuple(finding.code for finding in analysis.findings)


def _finding(
    code: QualityFindingCode,
    *,
    page_id: UUID = PAGE_ONE,
    page_position: int = 1,
    related_page_id: UUID | None = None,
    related_page_position: int | None = None,
) -> QualityFinding:
    evidence_by_code = {
        QualityFindingCode.LIKELY_LOW_RESOLUTION: (
            QualityEvidenceKind.SOURCE_SHORT_EDGE_PIXELS,
            0,
            1,
        ),
        QualityFindingCode.LIKELY_BLUR: (
            QualityEvidenceKind.LAPLACIAN_MEAN_SQUARE,
            0,
            1,
        ),
        QualityFindingCode.SUSPECTED_CLIPPING: (
            QualityEvidenceKind.BORDER_CONTACT_PPM,
            1,
            1,
        ),
        QualityFindingCode.LIKELY_BLANK: (
            QualityEvidenceKind.CENTRAL_INK_PPM,
            0,
            0,
        ),
        QualityFindingCode.SUSPECTED_SIDEWAYS_ORIENTATION: (
            QualityEvidenceKind.PROJECTION_RATIO_PPM,
            1,
            1,
        ),
        QualityFindingCode.SUSPECTED_DUPLICATE: (
            QualityEvidenceKind.DHASH_HAMMING_DISTANCE,
            0,
            4,
        ),
        QualityFindingCode.OCR_FAILED: (QualityEvidenceKind.OCR_STATUS, 1, 0),
        QualityFindingCode.OCR_LOW_CONFIDENCE: (
            QualityEvidenceKind.OCR_MEAN_CONFIDENCE_MILLI,
            0,
            1,
        ),
    }
    evidence_kind, observed_value, threshold_value = evidence_by_code[code]
    return QualityFinding(
        page_id=page_id,
        page_position=page_position,
        code=code,
        evidence_kind=evidence_kind,
        observed_value=observed_value,
        threshold_value=threshold_value,
        related_page_id=related_page_id,
        related_page_position=related_page_position,
    )


def test_default_profile_has_stable_identity_and_repr_hides_thresholds() -> None:
    profile = DEFAULT_QUALITY_ANALYSIS_PROFILE

    assert profile.profile_id == "scan-quality-v1"
    assert profile.profile_version == 1
    assert len(profile.profile_sha256) == 64
    assert profile.profile_sha256 == QualityAnalysisProfile().profile_sha256
    assert (
        profile.profile_sha256
        != replace(
            profile,
            blur_laplacian_mean_square_max=profile.blur_laplacian_mean_square_max + 1,
        ).profile_sha256
    )
    assert repr(profile) == (
        "QualityAnalysisProfile(profile_id='scan-quality-v1', profile_version=1)"
    )
    assert str(profile.blur_laplacian_mean_square_max) not in repr(profile)


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("profile_id", "Unsafe profile"),
        ("profile_id", "a" * 129),
        ("source_short_edge_pixels_min", 0),
        ("source_pixel_count_min", 0),
        ("ink_luma_max", 255),
        ("blank_central_ink_ppm_max", 1_000_001),
        ("blank_mean_darkness_ppm_max", -1),
        ("blur_laplacian_mean_square_max", -1),
        ("blur_minimum_ink_ppm", 1_000_001),
        ("clipping_band_ppm", 0),
        ("clipping_side_contact_ppm_min", -1),
        ("clipping_band_ink_ppm_min", 1_000_001),
        ("sideways_projection_ratio_ppm_min", 0),
        ("duplicate_hamming_distance_max", 65),
        ("duplicate_aspect_delta_ppm_max", -1),
        ("duplicate_ink_delta_ppm_max", 1_000_001),
        ("ocr_minimum_words_for_confidence", 0),
        ("ocr_mean_confidence_milli_min", 100_001),
        ("profile_version", 2),
    ),
)
def test_profile_rejects_invalid_or_future_configuration(
    field_name: str,
    value: object,
) -> None:
    with pytest.raises(QualityAnalysisError) as raised:
        replace(
            DEFAULT_QUALITY_ANALYSIS_PROFILE,
            **cast(Any, {field_name: value}),
        )

    assert raised.value.code is QualityAnalysisErrorCode.MODEL_INVALID
    assert raised.value.context == "quality.profile"


def test_error_registry_is_closed_sanitized_and_repr_safe() -> None:
    prohibited = "Applicant-Jane-Doe-C:/private/page.png"

    with pytest.raises(QualityAnalysisError) as raised:
        reject_quality_analysis(QualityAnalysisErrorCode.PREVIEW_FAILED, prohibited)

    error = raised.value
    assert error.code is QualityAnalysisErrorCode.INTERNAL_ERROR
    assert error.context == "quality.internal"
    assert prohibited not in str(error)
    assert prohibited not in repr(error)
    assert "page.png" not in error.safe_message
    assert set(QualityAnalysisErrorCode) == {
        QualityAnalysisErrorCode.MODEL_INVALID,
        QualityAnalysisErrorCode.CANCELLED,
        QualityAnalysisErrorCode.TIMED_OUT,
        QualityAnalysisErrorCode.PREVIEW_FAILED,
        QualityAnalysisErrorCode.LIMIT_EXCEEDED,
        QualityAnalysisErrorCode.RESULT_INVALID,
        QualityAnalysisErrorCode.INTERNAL_ERROR,
    }


def test_sample_padding_is_ignored_but_pixel_alpha_and_bounds_are_enforced() -> None:
    packed = _sample()
    padded = _sample(padding=17, padding_byte=19)

    assert analyze_quality_page(packed).findings == analyze_quality_page(padded).findings
    assert analyze_quality_page(packed).fingerprint == analyze_quality_page(padded).fingerprint

    nonopaque = bytearray(packed.pixels)
    nonopaque[3] = 254
    with pytest.raises(QualityAnalysisError) as alpha_error:
        replace(packed, pixels=bytes(nonopaque))
    assert alpha_error.value.context == "quality.sample_alpha"

    invalid_samples = (
        {"row_stride_bytes": packed.width_pixels * 4 - 1},
        {"pixels": packed.pixels[:-1]},
        {"width_pixels": 1_025},
        {"height_pixels": 0},
        {"page_position": 4_097},
        {"sample_version": 2},
    )
    for changes in invalid_samples:
        with pytest.raises(QualityAnalysisError) as raised:
            replace(packed, **changes)
        assert raised.value.code is QualityAnalysisErrorCode.MODEL_INVALID


def test_declared_extreme_sample_aspect_ratio_produces_a_valid_fingerprint() -> None:
    sample = _sample(width=1_024, height=1)

    analysis = analyze_quality_page(sample)

    assert analysis.fingerprint.aspect_ratio_ppm == 1_024_000_000


def test_sample_source_dimensions_are_exact_optional_image_facts_only() -> None:
    image = _sample(
        page_kind=QualityPageKind.IMAGE,
        source_size=(1_200, 1_600),
    )
    assert image.source_width_pixels == 1_200
    assert image.source_height_pixels == 1_600

    for changes in (
        {"source_width_pixels": 1_200, "source_height_pixels": None},
        {
            "page_kind": QualityPageKind.PDF,
            "source_width_pixels": 1_200,
            "source_height_pixels": 1_600,
        },
        {"source_width_pixels": 0, "source_height_pixels": 1_600},
    ):
        with pytest.raises(QualityAnalysisError) as raised:
            replace(image, **changes)
        assert raised.value.context == "quality.source_size"


def test_pixels_and_private_fingerprint_never_appear_in_representations() -> None:
    token = b"SECRETPAGE"
    padding = token + b"!" * (32 - len(token))
    sample = _sample(padding=len(padding), padding_byte=1)
    raw = bytearray(sample.pixels)
    stride = sample.row_stride_bytes
    for row in range(sample.height_pixels):
        start = row * stride + sample.width_pixels * 4
        raw[start : start + len(padding)] = padding
    sample = replace(sample, pixels=bytes(raw))
    analysis = analyze_quality_page(sample)

    assert token.decode("ascii") not in repr(sample)
    assert token.decode("ascii") not in repr(analysis)
    fingerprint_repr = repr(analysis.fingerprint)
    assert "difference_hash=" not in fingerprint_repr
    assert "aspect_ratio_ppm=" not in fingerprint_repr
    assert "ink_fraction_ppm=" not in fingerprint_repr


def test_low_resolution_uses_only_exact_source_dimensions_and_strict_boundaries() -> None:
    profile = replace(
        DEFAULT_QUALITY_ANALYSIS_PROFILE,
        source_short_edge_pixels_min=1_000,
        source_pixel_count_min=1_000_000,
    )
    below = _sample(
        page_kind=QualityPageKind.IMAGE,
        source_size=(999, 2_000),
    )
    exact = _sample(
        page_kind=QualityPageKind.IMAGE,
        source_size=(1_000, 1_000),
    )
    unknown = _sample(page_kind=QualityPageKind.IMAGE)

    below_finding = next(
        finding
        for finding in analyze_quality_page(below, profile=profile).findings
        if finding.code is QualityFindingCode.LIKELY_LOW_RESOLUTION
    )
    assert below_finding.evidence_kind is QualityEvidenceKind.SOURCE_SHORT_EDGE_PIXELS
    assert below_finding.observed_value == 999
    assert below_finding.threshold_value == 1_000
    assert QualityFindingCode.LIKELY_LOW_RESOLUTION not in _codes(
        analyze_quality_page(exact, profile=profile)
    )
    assert QualityFindingCode.LIKELY_LOW_RESOLUTION not in _codes(
        analyze_quality_page(unknown, profile=profile)
    )

    pixel_profile = replace(
        profile,
        source_short_edge_pixels_min=10,
        source_pixel_count_min=500,
    )
    pixel_below = _sample(
        page_kind=QualityPageKind.IMAGE,
        source_size=(20, 24),
    )
    pixel_finding = next(
        finding
        for finding in analyze_quality_page(pixel_below, profile=pixel_profile).findings
        if finding.code is QualityFindingCode.LIKELY_LOW_RESOLUTION
    )
    assert pixel_finding.evidence_kind is QualityEvidenceKind.SOURCE_PIXEL_COUNT
    assert pixel_finding.observed_value == 480


def test_blank_page_is_labeled_likely_and_suppresses_other_pixel_findings() -> None:
    analysis = analyze_quality_page(_sample())

    assert _codes(analysis) == (QualityFindingCode.LIKELY_BLANK,)
    finding = analysis.findings[0]
    assert finding.qualifier is QualityFindingQualifier.LIKELY
    assert finding.remediation is QualityRemediation.CONFIRM_PAGE_CONTENT
    assert "Likely" in finding.title
    assert "Confirm" in finding.message
    assert not {
        QualityFindingCode.LIKELY_BLUR,
        QualityFindingCode.SUSPECTED_CLIPPING,
        QualityFindingCode.SUSPECTED_SIDEWAYS_ORIENTATION,
    } & set(_codes(analysis))


def test_blank_threshold_is_inclusive_and_mean_darkness_must_also_match() -> None:
    white = _sample()
    exact = replace(
        DEFAULT_QUALITY_ANALYSIS_PROFILE,
        blank_central_ink_ppm_max=0,
        blank_mean_darkness_ppm_max=0,
    )
    assert QualityFindingCode.LIKELY_BLANK in _codes(analyze_quality_page(white, profile=exact))

    almost_white = _sample(pixel=(254, 254, 254, 255))
    assert QualityFindingCode.LIKELY_BLANK not in _codes(
        analyze_quality_page(almost_white, profile=exact)
    )


def test_uniform_nonblank_page_produces_likely_blur_below_strict_threshold() -> None:
    sample = _sample(pixel=(200, 200, 200, 255))
    profile = replace(
        DEFAULT_QUALITY_ANALYSIS_PROFILE,
        blur_laplacian_mean_square_max=1,
        clipping_side_contact_ppm_min=1_000_000,
        clipping_band_ink_ppm_min=1_000_000,
        sideways_projection_ratio_ppm_min=100_000_000,
    )
    analysis = analyze_quality_page(sample, profile=profile)

    finding = next(
        finding for finding in analysis.findings if finding.code is QualityFindingCode.LIKELY_BLUR
    )
    assert finding.evidence_kind is QualityEvidenceKind.LAPLACIAN_MEAN_SQUARE
    assert finding.observed_value == 0
    assert finding.threshold_value == 1
    assert finding.qualifier is QualityFindingQualifier.LIKELY

    at_boundary = replace(profile, blur_laplacian_mean_square_max=0)
    assert QualityFindingCode.LIKELY_BLUR not in _codes(
        analyze_quality_page(sample, profile=at_boundary)
    )


def test_dark_content_touching_an_edge_produces_suspected_clipping() -> None:
    paint = {(column, row): (0, 0, 0, 255) for row in range(2) for column in range(64)}
    analysis = analyze_quality_page(_sample(paint=paint))

    finding = next(
        finding
        for finding in analysis.findings
        if finding.code is QualityFindingCode.SUSPECTED_CLIPPING
    )
    assert finding.evidence_kind is QualityEvidenceKind.BORDER_CONTACT_PPM
    assert finding.observed_value == 1_000_000
    assert finding.qualifier is QualityFindingQualifier.SUSPECTED
    assert finding.remediation is QualityRemediation.CHECK_PAGE_EDGES

    exact_profile = replace(
        DEFAULT_QUALITY_ANALYSIS_PROFILE,
        clipping_side_contact_ppm_min=finding.observed_value,
        clipping_band_ink_ppm_min=1_000_000,
    )
    assert QualityFindingCode.SUSPECTED_CLIPPING in _codes(
        analyze_quality_page(_sample(paint=paint), profile=exact_profile)
    )
    above_profile = replace(exact_profile, clipping_side_contact_ppm_min=1_000_000)
    no_band = {(column, 0): (0, 0, 0, 255) for column in range(64)}
    assert QualityFindingCode.SUSPECTED_CLIPPING not in _codes(
        analyze_quality_page(_sample(paint=no_band), profile=above_profile)
    )


def test_vertical_projection_produces_only_a_suspected_sideways_signal() -> None:
    paint = {(column, row): (0, 0, 0, 255) for column in range(20, 44, 4) for row in range(4, 60)}
    profile = replace(
        DEFAULT_QUALITY_ANALYSIS_PROFILE,
        clipping_side_contact_ppm_min=1_000_000,
        clipping_band_ink_ppm_min=1_000_000,
    )
    analysis = analyze_quality_page(_sample(paint=paint), profile=profile)

    finding = next(
        finding
        for finding in analysis.findings
        if finding.code is QualityFindingCode.SUSPECTED_SIDEWAYS_ORIENTATION
    )
    assert finding.evidence_kind is QualityEvidenceKind.PROJECTION_RATIO_PPM
    assert finding.observed_value >= finding.threshold_value
    assert finding.qualifier is QualityFindingQualifier.SUSPECTED
    assert "may be sideways" in finding.message


def test_zero_horizontal_projection_variance_is_saturated_without_failure() -> None:
    paint = {(column, row): (0, 0, 0, 255) for column in range(20, 44, 4) for row in range(64)}
    profile = replace(
        DEFAULT_QUALITY_ANALYSIS_PROFILE,
        clipping_side_contact_ppm_min=1_000_000,
        clipping_band_ink_ppm_min=1_000_000,
    )

    finding = next(
        finding
        for finding in analyze_quality_page(_sample(paint=paint), profile=profile).findings
        if finding.code is QualityFindingCode.SUSPECTED_SIDEWAYS_ORIENTATION
    )

    assert finding.observed_value == 10_000_000_000


@pytest.mark.parametrize("status", (OcrReviewStatus.FAILED, OcrReviewStatus.NO_TEXT))
def test_explicit_ocr_failure_or_no_text_produces_review_finding(
    status: OcrReviewStatus,
) -> None:
    sample = _sample(pixel=(200, 200, 200, 255))
    fact = OcrReviewFact(sample.page_id, status, 0, None)
    analysis = analyze_quality_page(sample, ocr_fact=fact)

    finding = next(
        finding for finding in analysis.findings if finding.code is QualityFindingCode.OCR_FAILED
    )
    assert finding.qualifier is QualityFindingQualifier.NEEDS_REVIEW
    assert finding.remediation is QualityRemediation.RETRY_OCR
    assert finding.evidence_kind is QualityEvidenceKind.OCR_STATUS
    assert QualityFindingCode.OCR_LOW_CONFIDENCE not in _codes(analysis)


def test_ocr_low_confidence_requires_explicit_numeric_evidence_and_threshold_words() -> None:
    sample = _sample()
    profile = DEFAULT_QUALITY_ANALYSIS_PROFILE
    below = OcrReviewFact(
        sample.page_id,
        OcrReviewStatus.SUCCEEDED,
        profile.ocr_minimum_words_for_confidence,
        profile.ocr_mean_confidence_milli_min - 1,
    )
    exact = replace(below, mean_confidence_milli=profile.ocr_mean_confidence_milli_min)
    too_few = replace(
        below,
        recognized_word_count=profile.ocr_minimum_words_for_confidence - 1,
    )
    absent = OcrReviewFact(sample.page_id, OcrReviewStatus.SUCCEEDED, 1, None)

    finding = next(
        finding
        for finding in analyze_quality_page(sample, ocr_fact=below).findings
        if finding.code is QualityFindingCode.OCR_LOW_CONFIDENCE
    )
    assert finding.observed_value == profile.ocr_mean_confidence_milli_min - 1
    assert finding.threshold_value == profile.ocr_mean_confidence_milli_min
    assert finding.qualifier is QualityFindingQualifier.NEEDS_REVIEW
    for fact in (exact, too_few, absent):
        assert QualityFindingCode.OCR_LOW_CONFIDENCE not in _codes(
            analyze_quality_page(sample, ocr_fact=fact)
        )


@pytest.mark.parametrize(
    "fact",
    (
        OcrReviewFact(PAGE_ONE, OcrReviewStatus.SUCCEEDED, 1, 0),
        OcrReviewFact(PAGE_ONE, OcrReviewStatus.SUCCEEDED, 1, 100_000),
        OcrReviewFact(PAGE_ONE, OcrReviewStatus.NO_TEXT, 0, None),
        OcrReviewFact(PAGE_ONE, OcrReviewStatus.FAILED, 0, None),
    ),
)
def test_valid_ocr_fact_states_are_closed_and_content_free(fact: OcrReviewFact) -> None:
    assert "text" not in vars(fact) if hasattr(fact, "__dict__") else True
    assert repr(fact).count("page_id") == 1


def test_ocr_fact_rejects_inconsistent_terminal_states_and_wrong_page_binding() -> None:
    invalid = (
        (OcrReviewStatus.FAILED, 1, None),
        (OcrReviewStatus.NO_TEXT, 0, 1),
        (OcrReviewStatus.SUCCEEDED, 0, None),
        (OcrReviewStatus.SUCCEEDED, 0, 1),
        (OcrReviewStatus.SUCCEEDED, 1, 100_001),
    )
    for status, words, confidence in invalid:
        with pytest.raises(QualityAnalysisError):
            OcrReviewFact(PAGE_ONE, status, words, confidence)

    sample = _sample()
    fact = OcrReviewFact(PAGE_TWO, OcrReviewStatus.FAILED, 0, None)
    with pytest.raises(QualityAnalysisError) as raised:
        analyze_quality_page(sample, ocr_fact=fact)
    assert raised.value.context == "quality.ocr_binding"


def test_duplicate_threshold_is_inclusive_and_result_targets_later_page() -> None:
    first = analyze_quality_page(_sample())
    second = analyze_quality_page(_sample(page_id=PAGE_TWO, page_position=2))
    exact = suspected_duplicate_finding(second.fingerprint, first.fingerprint)

    assert exact is not None
    assert exact.code is QualityFindingCode.SUSPECTED_DUPLICATE
    assert exact.page_id == PAGE_TWO
    assert exact.page_position == 2
    assert exact.related_page_id == PAGE_ONE
    assert exact.related_page_position == 1
    assert exact.observed_value == 0

    threshold = DEFAULT_QUALITY_ANALYSIS_PROFILE.duplicate_hamming_distance_max
    at_boundary = replace(
        second.fingerprint,
        difference_hash=first.fingerprint.difference_hash ^ ((1 << threshold) - 1),
    )
    over_boundary = replace(
        second.fingerprint,
        difference_hash=first.fingerprint.difference_hash ^ ((1 << (threshold + 1)) - 1),
    )
    at_finding = suspected_duplicate_finding(at_boundary, first.fingerprint)
    assert at_finding is not None
    assert at_finding.observed_value == threshold
    assert suspected_duplicate_finding(over_boundary, first.fingerprint) is None

    reverse = suspected_duplicate_finding(first.fingerprint, second.fingerprint)
    assert reverse == exact


def test_duplicate_requires_hash_aspect_and_ink_similarity() -> None:
    first = analyze_quality_page(_sample()).fingerprint
    second = analyze_quality_page(_sample(page_id=PAGE_TWO, page_position=2)).fingerprint
    profile = DEFAULT_QUALITY_ANALYSIS_PROFILE

    aspect_mismatch = replace(
        second,
        aspect_ratio_ppm=second.aspect_ratio_ppm + profile.duplicate_aspect_delta_ppm_max + 20_000,
    )
    ink_mismatch = replace(
        second,
        ink_fraction_ppm=min(
            1_000_000,
            second.ink_fraction_ppm + profile.duplicate_ink_delta_ppm_max + 1,
        ),
    )
    assert suspected_duplicate_finding(aspect_mismatch, first) is None
    assert suspected_duplicate_finding(ink_mismatch, first) is None


def test_finding_order_is_closed_and_analysis_rejects_noncanonical_or_oversized_sets() -> None:
    sample = _sample(
        pixel=(200, 200, 200, 255),
        page_kind=QualityPageKind.IMAGE,
        source_size=(10, 10),
    )
    ocr = OcrReviewFact(PAGE_ONE, OcrReviewStatus.FAILED, 0, None)
    analysis = analyze_quality_page(sample, ocr_fact=ocr)
    assert analysis.findings == tuple(sorted(analysis.findings, key=quality_finding_sort_key))

    first, second = analysis.findings[:2]
    with pytest.raises(QualityAnalysisError) as unsorted:
        QualityPageAnalysis(
            analysis.page_id,
            analysis.page_position,
            (second, first),
            analysis.fingerprint,
        )
    assert unsorted.value.code is QualityAnalysisErrorCode.RESULT_INVALID

    repeated = tuple(first for _ in range(MAX_QUALITY_FINDINGS_PER_PAGE + 1))
    with pytest.raises(QualityAnalysisError) as oversized:
        QualityPageAnalysis(
            analysis.page_id,
            analysis.page_position,
            repeated,
            analysis.fingerprint,
        )
    assert oversized.value.code is QualityAnalysisErrorCode.RESULT_INVALID


def test_finding_presentation_is_closed_for_every_code() -> None:
    duplicate = _finding(
        QualityFindingCode.SUSPECTED_DUPLICATE,
        page_id=PAGE_TWO,
        page_position=2,
        related_page_id=PAGE_ONE,
        related_page_position=1,
    )
    ordinary = tuple(
        _finding(code)
        for code in QualityFindingCode
        if code is not QualityFindingCode.SUSPECTED_DUPLICATE
    )

    for finding in (*ordinary, duplicate):
        assert type(finding.qualifier) is QualityFindingQualifier
        assert type(finding.remediation) is QualityRemediation
        assert finding.title
        assert finding.message
        wording = (finding.title + finding.message).lower()
        assert "definitely" not in wording
        assert "requirement complete" not in wording

    with pytest.raises(QualityAnalysisError):
        _finding(
            QualityFindingCode.LIKELY_BLANK,
            related_page_id=PAGE_TWO,
            related_page_position=2,
        )
    with pytest.raises(QualityAnalysisError):
        _finding(QualityFindingCode.SUSPECTED_DUPLICATE)


def test_findings_reject_mismatched_evidence_late_related_pages_and_duplicate_codes() -> None:
    with pytest.raises(QualityAnalysisError):
        QualityFinding(
            PAGE_ONE,
            1,
            QualityFindingCode.LIKELY_BLUR,
            QualityEvidenceKind.OCR_STATUS,
            1,
            0,
        )
    with pytest.raises(QualityAnalysisError):
        _finding(
            QualityFindingCode.SUSPECTED_DUPLICATE,
            page_id=PAGE_ONE,
            page_position=1,
            related_page_id=PAGE_TWO,
            related_page_position=2,
        )

    sample = _sample()
    analysis = analyze_quality_page(sample)
    finding = _finding(QualityFindingCode.OCR_FAILED)
    with pytest.raises(QualityAnalysisError):
        QualityPageAnalysis(
            analysis.page_id,
            analysis.page_position,
            (finding, finding),
            analysis.fingerprint,
        )


def test_analysis_is_byte_for_byte_deterministic_for_equivalent_samples() -> None:
    paint = {(column, row): (20, 40, 60, 255) for row in range(8, 56, 6) for column in range(5, 59)}
    first = analyze_quality_page(_sample(paint=paint))
    second = analyze_quality_page(_sample(paint=dict(reversed(tuple(paint.items())))))

    assert first == second
    assert repr(first) == repr(second)
    assert first.findings == tuple(sorted(first.findings, key=quality_finding_sort_key))


def test_progress_is_content_free_bounded_and_complete_is_terminal() -> None:
    progress = QualityAnalysisProgress(QualityAnalysisProgressPhase.ANALYZING, 2, 3)
    complete = QualityAnalysisProgress(QualityAnalysisProgressPhase.COMPLETE, 3, 3)

    assert progress.completed_pages == 2
    assert complete.completed_pages == complete.total_pages
    for invalid in (
        (QualityAnalysisProgressPhase.ANALYZING, 4, 3),
        (QualityAnalysisProgressPhase.COMPLETE, 2, 3),
        (QualityAnalysisProgressPhase.PREVIEWING, 0, 0),
    ):
        with pytest.raises(QualityAnalysisError) as raised:
            QualityAnalysisProgress(*invalid)
        assert raised.value.context == "quality.progress"

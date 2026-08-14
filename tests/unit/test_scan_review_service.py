from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.app.scan_review import (
    SCAN_REVIEW_PREVIEW_VERSION,
    SCAN_REVIEW_VERSION,
    ScanReviewDraft,
    ScanReviewError,
    ScanReviewErrorCode,
    ScanReviewPreviewResult,
    ScanReviewPreviewVariant,
)
from paperworkshell.domain.image_pages import (
    ImageAlphaNormalizationPolicy,
    ImageExifOrientation,
    ImagePageMetadata,
    ImagePagePreviewRequest,
    ImagePagePreviewResult,
    ImagePagePreviewSnapshot,
    ImageQuarterTurn,
    ImageSourceColorModel,
    ImageSourceFormat,
)
from paperworkshell.domain.image_transforms import (
    ImagePageTransformRequest,
    ImagePageTransformResult,
    ImagePageTransformSnapshot,
    ImageTransformIntent,
)

CASE_ID = UUID("81000000-0000-4000-8000-000000000001")
PAGE_ID = UUID("81000000-0000-4000-8000-000000000002")
SOURCE_ID = UUID("81000000-0000-4000-8000-000000000003")


def _draft(*, candidate: ImageTransformIntent | None = None) -> ScanReviewDraft:
    base = ImageTransformIntent()
    return ScanReviewDraft(
        CASE_ID,
        PAGE_ID,
        7,
        base,
        base if candidate is None else candidate,
    )


def _metadata() -> ImagePageMetadata:
    return ImagePageMetadata(
        source_format=ImageSourceFormat.PNG,
        source_color_model=ImageSourceColorModel.RGB,
        alpha_policy=ImageAlphaNormalizationPolicy.COMPOSITE_ON_OPAQUE_WHITE,
        exif_orientation=ImageExifOrientation.NORMAL,
        encoded_width_pixels=2,
        encoded_height_pixels=1,
        oriented_width_pixels=2,
        oriented_height_pixels=1,
        component_count=3,
        bits_per_component=8,
        frame_count=1,
        metadata_record_count=0,
        metadata_byte_count=0,
        has_alpha=False,
        has_embedded_color_profile=False,
        has_exif=False,
    )


def _original_result(draft: ScanReviewDraft) -> ImagePagePreviewResult:
    request = ImagePagePreviewRequest(draft.case_id, draft.page_id, 32, 32)
    pixels = bytes((10, 20, 30, 255)) * 2
    snapshot = ImagePagePreviewSnapshot(
        draft.case_id,
        draft.page_id,
        2,
        1,
        8,
        pixels,
        _metadata(),
        ImageQuarterTurn.DEGREES_0,
    )
    return ImagePagePreviewResult(request, snapshot)


def _adjusted_result(draft: ScanReviewDraft) -> ImagePageTransformResult:
    request = ImagePageTransformRequest(
        draft.case_id,
        draft.page_id,
        draft.candidate_intent,
        32,
        32,
    )
    pixels = bytes((40, 50, 60, 255))
    snapshot = ImagePageTransformSnapshot(
        case_id=draft.case_id,
        source_id=SOURCE_ID,
        page_id=draft.page_id,
        intent=draft.candidate_intent,
        width_pixels=1,
        height_pixels=1,
        row_stride_bytes=4,
        bytes_written=4,
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
        cache_key_sha256="c" * 64,
    )
    return ImagePageTransformResult(request, snapshot, pixels)


def test_draft_is_immutable_provisional_state_with_explicit_reset() -> None:
    base = ImageTransformIntent(contrast_factor_millionths=1_100_000)
    suggested = ImageTransformIntent(
        deskew_angle_millidegrees_clockwise=1_250,
        contrast_factor_millionths=1_250_000,
    )
    draft = ScanReviewDraft(CASE_ID, PAGE_ID, 4, base, suggested, True)

    changed = draft.with_candidate(
        ImageTransformIntent(background_cleanup_strength_millionths=200_000)
    )
    reset = draft.reset_to_original()

    assert draft.candidate_intent is suggested
    assert draft.suggestion_present
    assert changed.base_intent is base
    assert not changed.suggestion_present
    assert reset.candidate_intent == ImageTransformIntent()
    assert reset.base_intent is base
    assert not reset.suggestion_present
    assert draft.review_version == SCAN_REVIEW_VERSION


def test_draft_and_preview_representations_hide_ids_intents_digests_and_pixels() -> None:
    canaries = (
        str(CASE_ID),
        str(PAGE_ID),
        "1250000",
        "40000000",
        "c" * 64,
    )
    draft = _draft(candidate=ImageTransformIntent(contrast_factor_millionths=1_250_000))
    preview = ScanReviewPreviewResult(
        draft,
        ScanReviewPreviewVariant.ADJUSTED,
        _adjusted_result(draft),
    )

    represented = repr(draft) + repr(preview)

    assert all(canary not in represented for canary in canaries)
    assert repr(preview) == ("ScanReviewPreviewResult(variant='adjusted', preview_version=1)")


def test_original_and_adjusted_results_require_exact_variant_binding() -> None:
    candidate = ImageTransformIntent(contrast_factor_millionths=1_250_000)
    draft = _draft(candidate=candidate)

    original = ScanReviewPreviewResult(
        draft,
        ScanReviewPreviewVariant.ORIGINAL,
        _original_result(draft),
    )
    adjusted = ScanReviewPreviewResult(
        draft,
        ScanReviewPreviewVariant.ADJUSTED,
        _adjusted_result(draft),
    )

    assert original.preview is original.result
    assert adjusted.preview is adjusted.result
    assert original.preview_version == SCAN_REVIEW_PREVIEW_VERSION
    with pytest.raises(ScanReviewError) as wrong_variant:
        ScanReviewPreviewResult(
            draft,
            ScanReviewPreviewVariant.ORIGINAL,
            _adjusted_result(draft),
        )
    assert wrong_variant.value.code is ScanReviewErrorCode.PREVIEW_RESULT_INVALID

    changed = draft.with_candidate(ImageTransformIntent(contrast_factor_millionths=900_000))
    with pytest.raises(ScanReviewError) as stale_adjusted:
        ScanReviewPreviewResult(
            changed,
            ScanReviewPreviewVariant.ADJUSTED,
            _adjusted_result(draft),
        )
    assert stale_adjusted.value.code is ScanReviewErrorCode.PREVIEW_RESULT_INVALID


@pytest.mark.parametrize(
    "factory",
    (
        lambda: ScanReviewDraft(
            PAGE_ID,
            PAGE_ID,
            7,
            ImageTransformIntent(),
            ImageTransformIntent(),
        ),
        lambda: ScanReviewDraft(
            CASE_ID,
            PAGE_ID,
            True,
            ImageTransformIntent(),
            ImageTransformIntent(),
        ),
        lambda: ScanReviewDraft(
            CASE_ID,
            PAGE_ID,
            7,
            ImageTransformIntent(),
            ImageTransformIntent(),
            cast(bool, 1),
        ),
        lambda: ScanReviewDraft(
            CASE_ID,
            PAGE_ID,
            7,
            ImageTransformIntent(),
            ImageTransformIntent(),
            review_version=SCAN_REVIEW_VERSION + 1,
        ),
    ),
)
def test_invalid_draft_values_fail_with_closed_sanitized_error(
    factory: Callable[[], ScanReviewDraft],
) -> None:
    with pytest.raises(ScanReviewError) as raised:
        factory()

    assert raised.value.code is ScanReviewErrorCode.MODEL_INVALID
    assert str(CASE_ID) not in str(raised.value)
    assert str(PAGE_ID) not in repr(raised.value)

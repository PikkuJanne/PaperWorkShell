from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.app.ocr_languages import unavailable_ocr_language_manager
from paperworkshell.app.page_previews import (
    CancellationProbe as PreviewCancellationProbe,
)
from paperworkshell.app.page_previews import (
    PagePreviewError,
    PagePreviewErrorCode,
    PagePreviewProgressSink,
    PagePreviewRequest,
    PagePreviewResult,
)
from paperworkshell.app.quality_analysis import (
    QUALITY_ANALYSIS_METADATA_RASTER_SIZE_PIXELS,
    QUALITY_ANALYSIS_RASTER_SIZE_PIXELS,
    QualityAnalysisError,
    QualityAnalysisErrorCode,
    QualityAnalysisProgress,
    QualityAnalysisProgressPhase,
    QualityAnalysisRequest,
    QualityAnalysisService,
    QualityPageReviewKind,
    QualityPageReviewRequest,
    QualityReviewSnapshot,
)
from paperworkshell.app.quality_ocr_review import QualityOcrReviewService
from paperworkshell.domain.image_pages import (
    ImageAlphaNormalizationPolicy,
    ImageExifOrientation,
    ImagePageError,
    ImagePageErrorCode,
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
from paperworkshell.domain.pdf_rendering import PdfRenderRotation
from paperworkshell.domain.pdf_thumbnails import (
    PdfThumbnailCacheKey,
    PdfThumbnailCacheStatus,
    PdfThumbnailError,
    PdfThumbnailErrorCode,
    PdfThumbnailRequest,
    PdfThumbnailResult,
    PdfThumbnailSnapshot,
)
from paperworkshell.domain.quality_analysis import (
    DEFAULT_QUALITY_ANALYSIS_PROFILE,
    OcrReviewFact,
    OcrReviewStatus,
    QualityAnalysisProfile,
    QualityEvidenceKind,
    QualityFinding,
    QualityFindingCode,
    QualityPageFingerprint,
    QualityPageSample,
)
from paperworkshell.domain.quality_ocr_review import (
    QualityOcrReviewErrorCode,
    reject_quality_ocr_review,
)

CASE_ID = UUID("91000000-0000-4000-8000-000000000001")
PDF_PAGE_ID = UUID("91000000-0000-4000-8000-000000000002")
IMAGE_PAGE_ID = UUID("91000000-0000-4000-8000-000000000003")
PDF_SOURCE_ID = UUID("91000000-0000-4000-8000-000000000004")
IMAGE_SOURCE_ID = UUID("91000000-0000-4000-8000-000000000005")


def _pixels(width: int, height: int, value: int = 255) -> bytes:
    return bytes((value, value, value, 255)) * width * height


def _request() -> QualityAnalysisRequest:
    return QualityAnalysisRequest(
        case_id=CASE_ID,
        manifest_revision=7,
        pages=(
            QualityPageReviewRequest(
                PDF_PAGE_ID,
                QualityPageReviewKind.PDF,
                pdf_rotation=PdfRenderRotation.DEGREES_0,
            ),
            QualityPageReviewRequest(
                IMAGE_PAGE_ID,
                QualityPageReviewKind.IMAGE,
                image_intent=ImageTransformIntent(),
            ),
        ),
    )


def _pdf_result(request: PdfThumbnailRequest) -> PdfThumbnailResult:
    width = height = 64
    key = PdfThumbnailCacheKey(
        case_id=request.case_id,
        source_id=PDF_SOURCE_ID,
        page_id=request.page_id,
        source_page_index=0,
        source_byte_size=512,
        source_sha256="a" * 64,
        maximum_width_pixels=request.maximum_width_pixels,
        maximum_height_pixels=request.maximum_height_pixels,
        additional_rotation=request.additional_rotation,
        renderer_profile_sha256="b" * 64,
    )
    snapshot = PdfThumbnailSnapshot(
        case_id=request.case_id,
        page_id=request.page_id,
        width_pixels=width,
        height_pixels=height,
        row_stride_bytes=width * 4,
        pixels=_pixels(width, height),
        intrinsic_rotation=PdfRenderRotation.DEGREES_0,
        additional_rotation=request.additional_rotation,
    )
    return PdfThumbnailResult(request, key, snapshot, PdfThumbnailCacheStatus.GENERATED)


def _metadata() -> ImagePageMetadata:
    return ImagePageMetadata(
        source_format=ImageSourceFormat.PNG,
        source_color_model=ImageSourceColorModel.RGB,
        alpha_policy=ImageAlphaNormalizationPolicy.COMPOSITE_ON_OPAQUE_WHITE,
        exif_orientation=ImageExifOrientation.NORMAL,
        encoded_width_pixels=800,
        encoded_height_pixels=700,
        oriented_width_pixels=800,
        oriented_height_pixels=700,
        component_count=3,
        bits_per_component=8,
        frame_count=1,
        metadata_record_count=0,
        metadata_byte_count=0,
        has_alpha=False,
        has_embedded_color_profile=False,
        has_exif=False,
    )


def _image_metadata_result(request: ImagePagePreviewRequest) -> ImagePagePreviewResult:
    snapshot = ImagePagePreviewSnapshot(
        case_id=request.case_id,
        page_id=request.page_id,
        width_pixels=1,
        height_pixels=1,
        row_stride_bytes=4,
        pixels=_pixels(1, 1),
        metadata=_metadata(),
        additional_rotation=ImageQuarterTurn.DEGREES_0,
    )
    return ImagePagePreviewResult(request, snapshot)


def _image_transform_result(request: ImagePageTransformRequest) -> ImagePageTransformResult:
    width = height = 64
    pixels = _pixels(width, height)
    snapshot = ImagePageTransformSnapshot(
        case_id=request.case_id,
        source_id=IMAGE_SOURCE_ID,
        page_id=request.page_id,
        intent=request.intent,
        width_pixels=width,
        height_pixels=height,
        row_stride_bytes=width * 4,
        bytes_written=len(pixels),
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
        cache_key_sha256="c" * 64,
    )
    return ImagePageTransformResult(request, snapshot, pixels)


class _Previews:
    def __init__(self) -> None:
        self.requests: list[PagePreviewRequest] = []
        self.error: BaseException | None = None
        self.before_cancellation: Callable[[], None] | None = None

    def get_previews(
        self,
        requests: tuple[PagePreviewRequest, ...],
        *,
        timeout_seconds: float,
        cancellation: PreviewCancellationProbe | None = None,
        progress: PagePreviewProgressSink | None = None,
    ) -> tuple[PagePreviewResult, ...]:
        assert len(requests) == 1
        assert timeout_seconds > 0.0
        assert progress is None
        request = requests[0]
        self.requests.append(request)
        if self.before_cancellation is not None:
            self.before_cancellation()
        if cancellation is not None and cancellation():
            raise PagePreviewError(PagePreviewErrorCode.CANCELLED, "preview.test")
        if self.error is not None:
            raise self.error
        if type(request) is PdfThumbnailRequest:
            return (_pdf_result(request),)
        if type(request) is ImagePagePreviewRequest:
            return (_image_metadata_result(request),)
        if type(request) is ImagePageTransformRequest:
            return (_image_transform_result(request),)
        raise AssertionError("unexpected request")


class _OcrReview:
    def __init__(self, status: OcrReviewStatus = OcrReviewStatus.FAILED) -> None:
        self.status = status
        self.calls: list[tuple[UUID, int, str, UUID, str]] = []

    def review_sample(
        self,
        case_id: UUID,
        manifest_revision: int,
        presentation_sha256: str,
        sample: QualityPageSample,
        *,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
    ) -> OcrReviewFact:
        assert type(sample) is QualityPageSample
        assert not deadline()
        assert not cancellation()
        self.calls.append(
            (
                case_id,
                manifest_revision,
                presentation_sha256,
                sample.page_id,
                hashlib.sha256(sample.pixels).hexdigest(),
            )
        )
        if self.status is OcrReviewStatus.SUCCEEDED:
            return OcrReviewFact(sample.page_id, self.status, 4, 84_000)
        return OcrReviewFact(sample.page_id, self.status, 0, None)


def test_service_reviews_exact_revision_sequentially_with_fixed_bounded_rasters() -> None:
    previews = _Previews()
    progress: list[QualityAnalysisProgress] = []

    snapshot = QualityAnalysisService(previews).analyze(_request(), progress=progress.append)

    assert snapshot.case_id == CASE_ID
    assert snapshot.manifest_revision == 7
    assert snapshot.page_ids == (PDF_PAGE_ID, IMAGE_PAGE_ID)
    assert snapshot.profile_id == DEFAULT_QUALITY_ANALYSIS_PROFILE.profile_id
    assert snapshot.profile_sha256 == DEFAULT_QUALITY_ANALYSIS_PROFILE.profile_sha256
    assert [type(request) for request in previews.requests] == [
        PdfThumbnailRequest,
        ImagePagePreviewRequest,
        ImagePageTransformRequest,
    ]
    assert all(len((request,)) == 1 for request in previews.requests)
    pdf_request = cast(PdfThumbnailRequest, previews.requests[0])
    metadata_request = cast(ImagePagePreviewRequest, previews.requests[1])
    transform_request = cast(ImagePageTransformRequest, previews.requests[2])
    assert (pdf_request.maximum_width_pixels, pdf_request.maximum_height_pixels) == (
        QUALITY_ANALYSIS_RASTER_SIZE_PIXELS,
        QUALITY_ANALYSIS_RASTER_SIZE_PIXELS,
    )
    assert (
        metadata_request.maximum_width_pixels,
        metadata_request.maximum_height_pixels,
    ) == (
        QUALITY_ANALYSIS_METADATA_RASTER_SIZE_PIXELS,
        QUALITY_ANALYSIS_METADATA_RASTER_SIZE_PIXELS,
    )
    assert (
        transform_request.maximum_width_pixels,
        transform_request.maximum_height_pixels,
    ) == (
        QUALITY_ANALYSIS_RASTER_SIZE_PIXELS,
        QUALITY_ANALYSIS_RASTER_SIZE_PIXELS,
    )
    codes_by_page = {
        page_id: tuple(finding.code for finding in snapshot.findings if finding.page_id == page_id)
        for page_id in snapshot.page_ids
    }
    assert QualityFindingCode.LIKELY_LOW_RESOLUTION not in codes_by_page[PDF_PAGE_ID]
    assert QualityFindingCode.LIKELY_LOW_RESOLUTION in codes_by_page[IMAGE_PAGE_ID]
    assert QualityFindingCode.SUSPECTED_DUPLICATE in codes_by_page[IMAGE_PAGE_ID]
    duplicate = next(
        finding
        for finding in snapshot.findings
        if finding.code is QualityFindingCode.SUSPECTED_DUPLICATE
    )
    assert duplicate.related_page_id == PDF_PAGE_ID
    assert [(item.phase, item.completed_pages, item.total_pages) for item in progress] == [
        (QualityAnalysisProgressPhase.PREVIEWING, 0, 2),
        (QualityAnalysisProgressPhase.ANALYZING, 0, 2),
        (QualityAnalysisProgressPhase.PREVIEWING, 1, 2),
        (QualityAnalysisProgressPhase.ANALYZING, 1, 2),
        (QualityAnalysisProgressPhase.COMPLETE, 2, 2),
    ]
    assert "pixels" not in repr(snapshot)
    assert snapshot.profile_sha256 not in repr(snapshot)


def test_snapshot_rejects_more_than_eight_findings_on_one_page() -> None:
    page_ids = tuple(UUID(int=index, version=4) for index in range(1, 11))
    findings = tuple(
        QualityFinding(
            page_id=page_ids[-1],
            page_position=len(page_ids),
            code=QualityFindingCode.SUSPECTED_DUPLICATE,
            evidence_kind=QualityEvidenceKind.DHASH_HAMMING_DISTANCE,
            observed_value=0,
            threshold_value=4,
            related_page_id=related_page_id,
            related_page_position=position,
        )
        for position, related_page_id in enumerate(page_ids[:-1], start=1)
    )

    with pytest.raises(QualityAnalysisError) as rejected:
        QualityReviewSnapshot(
            case_id=CASE_ID,
            manifest_revision=7,
            page_ids=page_ids,
            findings=findings,
            profile_id=DEFAULT_QUALITY_ANALYSIS_PROFILE.profile_id,
            profile_sha256=DEFAULT_QUALITY_ANALYSIS_PROFILE.profile_sha256,
        )

    assert rejected.value.code is QualityAnalysisErrorCode.RESULT_INVALID


def test_service_uses_only_earliest_prior_duplicate_for_each_later_page() -> None:
    third_page = UUID("91000000-0000-4000-8000-000000000006")
    request = QualityAnalysisRequest(
        CASE_ID,
        8,
        (
            QualityPageReviewRequest(
                PDF_PAGE_ID,
                QualityPageReviewKind.PDF,
                pdf_rotation=PdfRenderRotation.DEGREES_0,
            ),
            QualityPageReviewRequest(
                third_page,
                QualityPageReviewKind.PDF,
                pdf_rotation=PdfRenderRotation.DEGREES_0,
            ),
            QualityPageReviewRequest(
                IMAGE_PAGE_ID,
                QualityPageReviewKind.IMAGE,
                image_intent=ImageTransformIntent(),
            ),
        ),
    )

    snapshot = QualityAnalysisService(_Previews()).analyze(request)

    duplicates = tuple(
        finding
        for finding in snapshot.findings
        if finding.code is QualityFindingCode.SUSPECTED_DUPLICATE
    )
    assert tuple(finding.page_id for finding in duplicates) == (third_page, IMAGE_PAGE_ID)
    assert all(finding.related_page_id == PDF_PAGE_ID for finding in duplicates)


def test_service_rejects_cancel_timeout_and_preview_failure_without_partial_snapshot() -> None:
    previews = _Previews()
    service = QualityAnalysisService(previews)
    with pytest.raises(QualityAnalysisError) as cancelled:
        service.analyze(_request(), cancellation=lambda: True)
    assert cancelled.value.code is QualityAnalysisErrorCode.CANCELLED
    assert previews.requests == []

    class _Clock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

    clock = _Clock()
    previews = _Previews()
    previews.before_cancellation = lambda: setattr(clock, "value", 2.0)
    with pytest.raises(QualityAnalysisError) as timed_out:
        QualityAnalysisService(previews, monotonic=clock).analyze(
            _request(),
            timeout_seconds=1.0,
        )
    assert timed_out.value.code is QualityAnalysisErrorCode.TIMED_OUT

    previews = _Previews()
    previews.error = RuntimeError("C:/private/OCR_TEXT.txt")
    with pytest.raises(QualityAnalysisError) as failed:
        QualityAnalysisService(previews).analyze(_request())
    assert failed.value.code is QualityAnalysisErrorCode.PREVIEW_FAILED
    assert "private" not in str(failed.value).lower()
    assert "ocr_text" not in str(failed.value).lower()


def test_service_rejects_selected_page_limit_before_preview() -> None:
    previews = _Previews()

    with pytest.raises(QualityAnalysisError) as rejected:
        QualityAnalysisService(previews, maximum_page_count=1).analyze(_request())

    assert rejected.value.code is QualityAnalysisErrorCode.LIMIT_EXCEEDED
    assert previews.requests == []


def test_preview_capacity_failure_leaves_no_snapshot_and_fresh_retry_succeeds() -> None:
    previews = _Previews()
    service = QualityAnalysisService(previews)
    previews.error = PagePreviewError(
        PagePreviewErrorCode.GENERATOR_FAILED,
        "preview.cache_capacity",
    )

    with pytest.raises(QualityAnalysisError) as failed:
        service.analyze(_request())

    assert failed.value.code is QualityAnalysisErrorCode.PREVIEW_FAILED
    previews.error = None
    snapshot = service.analyze(_request())
    assert snapshot.page_ids == (PDF_PAGE_ID, IMAGE_PAGE_ID)
    assert len(previews.requests) == 4


def test_preview_output_cleanup_failure_leaves_no_snapshot_and_fresh_retry_succeeds() -> None:
    previews = _Previews()
    service = QualityAnalysisService(previews)
    previews.error = PdfThumbnailError(
        PdfThumbnailErrorCode.DESTINATION_FAILED,
        "broker.output_cleanup",
    )

    with pytest.raises(QualityAnalysisError) as rejected:
        service.analyze(_request())

    assert rejected.value.code is QualityAnalysisErrorCode.PREVIEW_FAILED
    previews.error = None
    snapshot = service.analyze(_request())
    assert snapshot.page_ids == (PDF_PAGE_ID, IMAGE_PAGE_ID)
    assert len(previews.requests) == 4


@pytest.mark.parametrize("target", ["pdf", "image-metadata", "image-transform"])
def test_typed_preview_limits_abort_without_snapshot_and_allow_fresh_retry(
    target: str,
) -> None:
    if target == "pdf":
        target_type: type[object] = PdfThumbnailRequest
        failure: BaseException = PdfThumbnailError(
            PdfThumbnailErrorCode.LIMIT_EXCEEDED,
            "thumbnail.limit",
        )
        expected_request_count = 4
    elif target == "image-metadata":
        target_type = ImagePagePreviewRequest
        failure = ImagePageError(
            ImagePageErrorCode.LIMIT_EXCEEDED,
            "image.limit",
        )
        expected_request_count = 5
    else:
        target_type = ImagePageTransformRequest
        failure = ImagePageError(
            ImagePageErrorCode.LIMIT_EXCEEDED,
            "image.limit",
        )
        expected_request_count = 6

    class _FailOncePreviews(_Previews):
        def __init__(self) -> None:
            super().__init__()
            self.failed = False

        def get_previews(
            self,
            requests: tuple[PagePreviewRequest, ...],
            *,
            timeout_seconds: float,
            cancellation: PreviewCancellationProbe | None = None,
            progress: PagePreviewProgressSink | None = None,
        ) -> tuple[PagePreviewResult, ...]:
            if not self.failed and type(requests[0]) is target_type:
                self.failed = True
                self.error = failure
                try:
                    return super().get_previews(
                        requests,
                        timeout_seconds=timeout_seconds,
                        cancellation=cancellation,
                        progress=progress,
                    )
                finally:
                    self.error = None
            return super().get_previews(
                requests,
                timeout_seconds=timeout_seconds,
                cancellation=cancellation,
                progress=progress,
            )

    previews = _FailOncePreviews()
    service = QualityAnalysisService(previews)

    with pytest.raises(QualityAnalysisError) as rejected:
        service.analyze(_request())

    assert rejected.value.code is QualityAnalysisErrorCode.LIMIT_EXCEEDED
    snapshot = service.analyze(_request())
    assert snapshot.page_ids == (PDF_PAGE_ID, IMAGE_PAGE_ID)
    assert len(previews.requests) == expected_request_count


@pytest.mark.parametrize(
    "failure",
    [
        PdfThumbnailError(PdfThumbnailErrorCode.WORKER_FAILED, "thumbnail.worker"),
        ImagePageError(ImagePageErrorCode.DEPENDENCY_FAILED, "image.dependency"),
    ],
)
def test_nonlimit_typed_preview_failures_remain_preview_failed(
    failure: BaseException,
) -> None:
    previews = _Previews()
    previews.error = failure

    with pytest.raises(QualityAnalysisError) as rejected:
        QualityAnalysisService(previews).analyze(_request())

    assert rejected.value.code is QualityAnalysisErrorCode.PREVIEW_FAILED


def test_cancellation_is_observed_during_more_than_32_prior_duplicate_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import paperworkshell.app.quality_analysis as quality_module

    page_ids = tuple(UUID(f"93000000-0000-4000-8000-{number:012d}") for number in range(1, 35))
    request = QualityAnalysisRequest(
        CASE_ID,
        9,
        tuple(
            QualityPageReviewRequest(
                page_id,
                QualityPageReviewKind.PDF,
                pdf_rotation=PdfRenderRotation.DEGREES_0,
            )
            for page_id in page_ids
        ),
    )
    cancellation = False
    final_page_checks = 0

    def no_duplicate(
        current: QualityPageFingerprint,
        _prior: QualityPageFingerprint,
        *,
        profile: QualityAnalysisProfile,
    ) -> None:
        nonlocal cancellation, final_page_checks
        assert type(profile) is QualityAnalysisProfile
        if current.page_position == 34:
            final_page_checks += 1
            if final_page_checks == 32:
                cancellation = True
        return None

    monkeypatch.setattr(quality_module, "suspected_duplicate_finding", no_duplicate)

    with pytest.raises(QualityAnalysisError) as rejected:
        QualityAnalysisService(_Previews()).analyze(
            request,
            cancellation=lambda: cancellation,
        )

    assert rejected.value.code is QualityAnalysisErrorCode.CANCELLED
    assert final_page_checks == 32


def test_service_produces_ocr_facts_from_each_exact_current_sample() -> None:
    previews = _Previews()
    ocr_review = _OcrReview(OcrReviewStatus.SUCCEEDED)
    progress: list[QualityAnalysisProgress] = []

    snapshot = QualityAnalysisService(previews, ocr_review=ocr_review).analyze(
        _request(),
        progress=progress.append,
    )

    assert tuple(call[:2] for call in ocr_review.calls) == ((CASE_ID, 7), (CASE_ID, 7))
    assert tuple(call[3] for call in ocr_review.calls) == (PDF_PAGE_ID, IMAGE_PAGE_ID)
    assert all(len(call[2]) == 64 for call in ocr_review.calls)
    assert all(len(call[4]) == 64 for call in ocr_review.calls)
    assert tuple(item.phase for item in progress) == (
        QualityAnalysisProgressPhase.PREVIEWING,
        QualityAnalysisProgressPhase.OCR_REVIEWING,
        QualityAnalysisProgressPhase.ANALYZING,
        QualityAnalysisProgressPhase.PREVIEWING,
        QualityAnalysisProgressPhase.OCR_REVIEWING,
        QualityAnalysisProgressPhase.ANALYZING,
        QualityAnalysisProgressPhase.COMPLETE,
    )
    assert not any(
        finding.code in {QualityFindingCode.OCR_FAILED, QualityFindingCode.OCR_LOW_CONFIDENCE}
        for finding in snapshot.findings
    )


def test_default_missing_engine_path_emits_per_page_ocr_failed_findings() -> None:
    ocr_review = QualityOcrReviewService(
        None,
        language_sets=unavailable_ocr_language_manager(),
    )

    snapshot = QualityAnalysisService(
        _Previews(),
        ocr_review=ocr_review,
    ).analyze(_request())

    failed_pages = tuple(
        finding.page_id
        for finding in snapshot.findings
        if finding.code is QualityFindingCode.OCR_FAILED
    )
    assert failed_pages == (PDF_PAGE_ID, IMAGE_PAGE_ID)


def test_ocr_infrastructure_failure_aborts_without_partial_snapshot() -> None:
    class _FailingOcrReview:
        def review_sample(self, *_args: object, **_kwargs: object) -> OcrReviewFact:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.INTERNAL_ERROR,
                "quality_ocr.worker",
            )

    previews = _Previews()

    with pytest.raises(QualityAnalysisError) as rejected:
        QualityAnalysisService(
            previews,
            ocr_review=_FailingOcrReview(),
        ).analyze(_request())

    assert rejected.value.code is QualityAnalysisErrorCode.INTERNAL_ERROR
    assert len(previews.requests) == 1


def test_empty_saved_case_returns_exact_empty_snapshot_without_preview_or_progress() -> None:
    previews = _Previews()
    progress: list[QualityAnalysisProgress] = []
    request = QualityAnalysisRequest(CASE_ID, 0, ())

    snapshot = QualityAnalysisService(previews).analyze(request, progress=progress.append)

    assert snapshot.page_ids == ()
    assert snapshot.findings == ()
    assert previews.requests == []
    assert progress == []


def test_snapshot_rejects_finding_whose_position_is_not_bound_to_page_tuple() -> None:
    ordinary = QualityAnalysisService(_Previews()).analyze(_request())
    finding = next(finding for finding in ordinary.findings if finding.page_id == IMAGE_PAGE_ID)
    malformed = type(finding)(
        page_id=finding.page_id,
        page_position=1,
        code=finding.code,
        evidence_kind=finding.evidence_kind,
        observed_value=finding.observed_value,
        threshold_value=finding.threshold_value,
        related_page_id=finding.related_page_id,
        related_page_position=finding.related_page_position,
    )

    with pytest.raises(QualityAnalysisError) as raised:
        QualityReviewSnapshot(
            CASE_ID,
            7,
            (PDF_PAGE_ID, IMAGE_PAGE_ID),
            (malformed,),
            DEFAULT_QUALITY_ANALYSIS_PROFILE.profile_id,
            DEFAULT_QUALITY_ANALYSIS_PROFILE.profile_sha256,
        )

    assert raised.value.code is QualityAnalysisErrorCode.RESULT_INVALID


def test_snapshot_rejects_duplicate_finding_code_for_one_page() -> None:
    ordinary = QualityAnalysisService(_Previews()).analyze(_request())
    finding = next(finding for finding in ordinary.findings if finding.page_id == IMAGE_PAGE_ID)

    with pytest.raises(QualityAnalysisError) as rejected:
        QualityReviewSnapshot(
            CASE_ID,
            7,
            (PDF_PAGE_ID, IMAGE_PAGE_ID),
            (finding, finding),
            DEFAULT_QUALITY_ANALYSIS_PROFILE.profile_id,
            DEFAULT_QUALITY_ANALYSIS_PROFILE.profile_sha256,
        )

    assert rejected.value.code is QualityAnalysisErrorCode.RESULT_INVALID

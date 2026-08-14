"""Application seam from an exact quality raster to a content-free OCR fact."""

from __future__ import annotations

import re
from typing import Final, Protocol, runtime_checkable
from uuid import UUID

from paperworkshell.app.ocr import OcrLanguageAvailability
from paperworkshell.domain.manifest_repository import MAX_SAFE_MANIFEST_INTEGER
from paperworkshell.domain.ocr import OcrError, OcrErrorCode
from paperworkshell.domain.quality_analysis import (
    OcrReviewFact,
    OcrReviewStatus,
    QualityPageSample,
)
from paperworkshell.domain.quality_ocr_review import (
    QualityOcrReviewError,
    QualityOcrReviewErrorCode,
    QualityOcrReviewReport,
    QualityOcrReviewRequest,
    QualityOcrReviewStatus,
    reject_quality_ocr_review,
)

_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")


class DeadlineProbe(Protocol):
    def __call__(self) -> bool: ...


class CancellationProbe(Protocol):
    def __call__(self) -> bool: ...


@runtime_checkable
class QualityOcrReviewProcessor(Protocol):
    """Handle-only one-shot worker port."""

    def review(
        self,
        request: QualityOcrReviewRequest,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> QualityOcrReviewReport: ...


@runtime_checkable
class QualityOcrReviewProducer(Protocol):
    """Produce one fact from the exact current page sample and presentation."""

    def review_sample(
        self,
        case_id: UUID,
        manifest_revision: int,
        presentation_sha256: str,
        sample: QualityPageSample,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> OcrReviewFact: ...


class QualityOcrReviewService:
    """Select the active language set and collapse engine absence to review-needed."""

    __slots__ = ("_language_sets", "_processor")

    def __init__(
        self,
        processor: QualityOcrReviewProcessor | None,
        *,
        language_sets: OcrLanguageAvailability,
    ) -> None:
        if (
            processor is not None and not isinstance(processor, QualityOcrReviewProcessor)
        ) or not isinstance(language_sets, OcrLanguageAvailability):
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.MODEL_INVALID,
                "quality_ocr.service",
            )
        self._processor = processor
        self._language_sets = language_sets

    def review_sample(
        self,
        case_id: UUID,
        manifest_revision: int,
        presentation_sha256: str,
        sample: QualityPageSample,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
    ) -> OcrReviewFact:
        if (
            type(case_id) is not UUID
            or case_id.version != 4
            or type(manifest_revision) is not int
            or not 0 <= manifest_revision <= MAX_SAFE_MANIFEST_INTEGER
            or type(presentation_sha256) is not str
            or _SHA256.fullmatch(presentation_sha256) is None
            or type(sample) is not QualityPageSample
            or sample.page_id == case_id
            or not callable(deadline)
            or not callable(cancellation)
        ):
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.MODEL_INVALID,
                "quality_ocr.service_request",
            )
        _require_active(deadline, cancellation)
        try:
            language_set_id = self._language_sets.require_selected_available()
        except OcrError as error:
            if type(error) is OcrError and error.code is OcrErrorCode.LANGUAGE_UNAVAILABLE:
                return _failed_fact(sample.page_id)
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.INTERNAL_ERROR,
                "quality_ocr.language",
            )
        except Exception:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.INTERNAL_ERROR,
                "quality_ocr.language",
            )
        processor = self._processor
        if processor is None:
            return _failed_fact(sample.page_id)
        request = QualityOcrReviewRequest(
            case_id=case_id,
            manifest_revision=manifest_revision,
            page_id=sample.page_id,
            width_pixels=sample.width_pixels,
            height_pixels=sample.height_pixels,
            row_stride_bytes=sample.row_stride_bytes,
            pixels=sample.pixels,
            presentation_sha256=presentation_sha256,
            language_set_id=language_set_id,
        )
        try:
            report = processor.review(
                request,
                deadline=deadline,
                cancellation=cancellation,
            )
        except QualityOcrReviewError as error:
            if type(error) is not QualityOcrReviewError:
                reject_quality_ocr_review(
                    QualityOcrReviewErrorCode.INTERNAL_ERROR,
                    "quality_ocr.processor",
                )
            if error.code in {
                QualityOcrReviewErrorCode.ENGINE_UNAVAILABLE,
                QualityOcrReviewErrorCode.ENGINE_FAILED,
            }:
                return _failed_fact(sample.page_id)
            reject_quality_ocr_review(error.code, error.context)
        except Exception:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.INTERNAL_ERROR,
                "quality_ocr.processor",
            )
        if type(report) is not QualityOcrReviewReport:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.RESULT_INVALID,
                "quality_ocr.service_result",
            )
        if (
            report.case_id != request.case_id
            or report.manifest_revision != request.manifest_revision
            or report.page_id != request.page_id
            or report.sample_sha256 != request.sample_sha256
            or report.presentation_sha256 != request.presentation_sha256
            or report.language_set_id != request.language_set_id
            or report.profile_id != request.profile_id
        ):
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.RESULT_INVALID,
                "quality_ocr.service_binding",
            )
        if report.status is QualityOcrReviewStatus.FAILED:
            return _failed_fact(sample.page_id)
        if report.status is QualityOcrReviewStatus.NO_TEXT:
            return OcrReviewFact(sample.page_id, OcrReviewStatus.NO_TEXT, 0, None)
        return OcrReviewFact(
            sample.page_id,
            OcrReviewStatus.SUCCEEDED,
            report.recognized_word_count,
            report.mean_confidence_milli,
        )


def _failed_fact(page_id: UUID) -> OcrReviewFact:
    return OcrReviewFact(page_id, OcrReviewStatus.FAILED, 0, None)


def _require_active(deadline: DeadlineProbe, cancellation: CancellationProbe) -> None:
    try:
        expired = deadline()
        cancelled = cancellation()
    except Exception:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.INTERNAL_ERROR,
            "quality_ocr.callback",
        )
    if type(expired) is not bool or type(cancelled) is not bool:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.INTERNAL_ERROR,
            "quality_ocr.callback",
        )
    if cancelled:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.CANCELLED,
            "quality_ocr.cancelled",
        )
    if expired:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.TIMED_OUT,
            "quality_ocr.deadline",
        )

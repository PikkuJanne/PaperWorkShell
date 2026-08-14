from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest

from paperworkshell.app.quality_ocr_review import QualityOcrReviewService
from paperworkshell.domain.ocr import OcrErrorCode, reject_ocr
from paperworkshell.domain.ocr_languages import (
    GERMAN_OCR_LANGUAGE_SET_ID,
)
from paperworkshell.domain.quality_analysis import (
    OcrReviewStatus,
    QualityPageKind,
    QualityPageSample,
)
from paperworkshell.domain.quality_ocr_review import (
    MAX_QUALITY_OCR_REVIEW_REPORT_BYTES,
    QUALITY_OCR_REVIEW_PROFILE_ID,
    QualityOcrReviewError,
    QualityOcrReviewErrorCode,
    QualityOcrReviewReport,
    QualityOcrReviewRequest,
    QualityOcrReviewStatus,
    decode_quality_ocr_review_report,
    encode_quality_ocr_review_report,
    reject_quality_ocr_review,
)

CASE_ID = UUID("94000000-0000-4000-8000-000000000001")
PAGE_ID = UUID("94000000-0000-4000-8000-000000000002")
OPERATION_ID = UUID("94000000-0000-4000-8000-000000000003")
PRESENTATION_SHA256 = "a" * 64
PRIVATE_PIXEL_CANARY = b"private-word"


def _pixels() -> bytes:
    return bytes((12, 34, 56, 255)) * 16


def _sample() -> QualityPageSample:
    return QualityPageSample(
        page_id=PAGE_ID,
        page_position=1,
        page_kind=QualityPageKind.PDF,
        width_pixels=4,
        height_pixels=4,
        row_stride_bytes=16,
        pixels=_pixels(),
    )


def _request(**changes: object) -> QualityOcrReviewRequest:
    values: dict[str, object] = {
        "case_id": CASE_ID,
        "manifest_revision": 7,
        "page_id": PAGE_ID,
        "width_pixels": 4,
        "height_pixels": 4,
        "row_stride_bytes": 16,
        "pixels": _pixels(),
        "presentation_sha256": PRESENTATION_SHA256,
        "language_set_id": GERMAN_OCR_LANGUAGE_SET_ID,
    }
    values.update(changes)
    return QualityOcrReviewRequest(**values)  # type: ignore[arg-type]


def _report(
    *,
    status: QualityOcrReviewStatus = QualityOcrReviewStatus.SUCCEEDED,
    word_count: int = 4,
    confidence: int | None = 82_500,
    request: QualityOcrReviewRequest | None = None,
) -> QualityOcrReviewReport:
    selected = _request() if request is None else request
    return QualityOcrReviewReport(
        operation_id=OPERATION_ID,
        case_id=selected.case_id,
        manifest_revision=selected.manifest_revision,
        page_id=selected.page_id,
        sample_sha256=selected.sample_sha256,
        presentation_sha256=selected.presentation_sha256,
        language_set_id=selected.language_set_id,
        profile_id=selected.profile_id,
        status=status,
        recognized_word_count=word_count,
        mean_confidence_milli=confidence,
    )


class _Languages:
    def __init__(self, language_set_id: str = GERMAN_OCR_LANGUAGE_SET_ID) -> None:
        self.language_set_id = language_set_id

    def require_available(self, language_set_id: str) -> None:
        assert language_set_id == self.language_set_id

    def require_selected_available(self) -> str:
        return self.language_set_id


class _UnavailableLanguages(_Languages):
    def require_selected_available(self) -> str:
        reject_ocr(OcrErrorCode.LANGUAGE_UNAVAILABLE, "ocr.language_preflight")


class _Processor:
    def __init__(
        self,
        status: QualityOcrReviewStatus,
        word_count: int,
        confidence: int | None,
    ) -> None:
        self.status = status
        self.word_count = word_count
        self.confidence = confidence
        self.requests: list[QualityOcrReviewRequest] = []
        self.report_mutation: str | None = None

    def review(
        self,
        request: QualityOcrReviewRequest,
        *,
        deadline: object,
        cancellation: object,
    ) -> QualityOcrReviewReport:
        assert callable(deadline)
        assert callable(cancellation)
        self.requests.append(request)
        report = _report(
            status=self.status,
            word_count=self.word_count,
            confidence=self.confidence,
            request=request,
        )
        mutation = self.report_mutation
        if mutation == "case_id":
            return replace(report, case_id=UUID("95000000-0000-4000-8000-000000000001"))
        if mutation == "revision":
            return replace(report, manifest_revision=request.manifest_revision + 1)
        if mutation == "page_id":
            return replace(report, page_id=UUID("95000000-0000-4000-8000-000000000002"))
        if mutation == "sample_sha256":
            return replace(report, sample_sha256="b" * 64)
        if mutation == "presentation_sha256":
            return replace(report, presentation_sha256="c" * 64)
        if mutation == "language_set_id":
            return replace(report, language_set_id="latin-default-v1")
        if mutation == "profile_id":
            object.__setattr__(report, "profile_id", "other-profile-v1")
            return report
        return report


def test_request_is_exactly_bound_and_repr_omits_pixels_and_digests() -> None:
    request = _request()

    assert request.sample_sha256
    assert request.sample_byte_size == 64
    rendered = repr(request)
    assert request.sample_sha256 not in rendered
    assert request.presentation_sha256 not in rendered
    assert _pixels().hex() not in rendered


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"profile_id": "a" * 129}, QualityOcrReviewErrorCode.MODEL_INVALID),
        ({"profile_id": "other-profile-v1"}, QualityOcrReviewErrorCode.MODEL_INVALID),
        ({"language_set_id": "a" * 129}, QualityOcrReviewErrorCode.MODEL_INVALID),
        ({"presentation_sha256": "f" * 65}, QualityOcrReviewErrorCode.MODEL_INVALID),
    ],
)
def test_request_rejects_long_or_unbounded_tokens(
    change: dict[str, object],
    expected: QualityOcrReviewErrorCode,
) -> None:
    with pytest.raises(QualityOcrReviewError) as rejected:
        _request(**change)
    assert rejected.value.code is expected


def test_report_codec_round_trips_only_the_closed_content_free_shape() -> None:
    report = _report()
    payload = encode_quality_ocr_review_report(report)

    assert decode_quality_ocr_review_report(payload) == report
    assert b"text" not in payload
    assert b"geometry" not in payload
    assert b"path" not in payload


@pytest.mark.parametrize("profile_id", ["other-profile-v1", "a" * 129])
def test_report_rejects_forged_or_unbounded_profile_identity(profile_id: str) -> None:
    with pytest.raises(QualityOcrReviewError) as rejected:
        replace(_report(), profile_id=profile_id)
    assert rejected.value.code is QualityOcrReviewErrorCode.RESULT_INVALID


@pytest.mark.parametrize("mutation", ["duplicate", "future", "noncanonical", "oversized"])
def test_report_codec_rejects_duplicate_future_noncanonical_and_oversized_payloads(
    mutation: str,
) -> None:
    payload = encode_quality_ocr_review_report(_report())
    if mutation == "duplicate":
        selected = payload[:-1] + b',"status":"failed"}'
    elif mutation == "future":
        selected = payload[:-1] + b',"future":1}'
    elif mutation == "noncanonical":
        selected = b" " + payload
    else:
        selected = b"{" + b"x" * MAX_QUALITY_OCR_REVIEW_REPORT_BYTES + b"}"

    with pytest.raises(QualityOcrReviewError) as rejected:
        decode_quality_ocr_review_report(selected)

    assert rejected.value.code is QualityOcrReviewErrorCode.RESULT_INVALID


@pytest.mark.parametrize(
    ("status", "count", "confidence", "expected"),
    [
        (QualityOcrReviewStatus.SUCCEEDED, 4, 82_500, OcrReviewStatus.SUCCEEDED),
        (QualityOcrReviewStatus.SUCCEEDED, 4, 42_000, OcrReviewStatus.SUCCEEDED),
        (QualityOcrReviewStatus.NO_TEXT, 0, None, OcrReviewStatus.NO_TEXT),
        (QualityOcrReviewStatus.FAILED, 0, None, OcrReviewStatus.FAILED),
    ],
)
def test_service_uses_selected_language_and_maps_closed_terminal_fact(
    status: QualityOcrReviewStatus,
    count: int,
    confidence: int | None,
    expected: OcrReviewStatus,
) -> None:
    processor = _Processor(status, count, confidence)
    fact = QualityOcrReviewService(processor, language_sets=_Languages()).review_sample(
        CASE_ID,
        7,
        PRESENTATION_SHA256,
        _sample(),
        deadline=lambda: False,
        cancellation=lambda: False,
    )

    assert fact.status is expected
    assert processor.requests[0].language_set_id == GERMAN_OCR_LANGUAGE_SET_ID
    assert processor.requests[0].profile_id == QUALITY_OCR_REVIEW_PROFILE_ID


@pytest.mark.parametrize(
    "mutation",
    [
        "case_id",
        "revision",
        "page_id",
        "sample_sha256",
        "presentation_sha256",
        "language_set_id",
        "profile_id",
    ],
)
def test_service_rejects_every_hostile_processor_report_binding_mutation(
    mutation: str,
) -> None:
    processor = _Processor(QualityOcrReviewStatus.SUCCEEDED, 4, 82_500)
    processor.report_mutation = mutation

    with pytest.raises(QualityOcrReviewError) as rejected:
        QualityOcrReviewService(processor, language_sets=_Languages()).review_sample(
            CASE_ID,
            7,
            PRESENTATION_SHA256,
            _sample(),
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert rejected.value.code is QualityOcrReviewErrorCode.RESULT_INVALID


def test_missing_runtime_or_processor_yields_truthful_failed_fact_without_inspection() -> None:
    processor = _Processor(QualityOcrReviewStatus.SUCCEEDED, 4, 82_500)
    unavailable = QualityOcrReviewService(
        processor,
        language_sets=_UnavailableLanguages(),
    ).review_sample(
        CASE_ID,
        7,
        PRESENTATION_SHA256,
        _sample(),
        deadline=lambda: False,
        cancellation=lambda: False,
    )
    missing_processor = QualityOcrReviewService(
        None,
        language_sets=_Languages(),
    ).review_sample(
        CASE_ID,
        7,
        PRESENTATION_SHA256,
        _sample(),
        deadline=lambda: False,
        cancellation=lambda: False,
    )

    assert unavailable.status is OcrReviewStatus.FAILED
    assert missing_processor.status is OcrReviewStatus.FAILED
    assert processor.requests == []


@pytest.mark.parametrize(
    ("case_id", "manifest_revision", "presentation_sha256"),
    [
        (UUID(int=1), 7, PRESENTATION_SHA256),
        (CASE_ID, -1, PRESENTATION_SHA256),
        (CASE_ID, 7, "A" * 64),
    ],
)
def test_service_validates_exact_binding_before_unavailable_early_returns(
    case_id: UUID,
    manifest_revision: int,
    presentation_sha256: str,
) -> None:
    processor = _Processor(QualityOcrReviewStatus.SUCCEEDED, 4, 82_500)
    services = (
        QualityOcrReviewService(processor, language_sets=_UnavailableLanguages()),
        QualityOcrReviewService(None, language_sets=_Languages()),
    )

    for service in services:
        with pytest.raises(QualityOcrReviewError) as rejected:
            service.review_sample(
                case_id,
                manifest_revision,
                presentation_sha256,
                _sample(),
                deadline=lambda: False,
                cancellation=lambda: False,
            )
        assert rejected.value.code is QualityOcrReviewErrorCode.MODEL_INVALID

    assert processor.requests == []


def test_service_propagates_processor_infrastructure_failure_without_advisory_fact() -> None:
    class _FailingProcessor:
        def review(self, *_args: object, **_kwargs: object) -> QualityOcrReviewReport:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.INTERNAL_ERROR,
                "quality_ocr.worker",
            )

    with pytest.raises(QualityOcrReviewError) as rejected:
        QualityOcrReviewService(
            _FailingProcessor(),
            language_sets=_Languages(),
        ).review_sample(
            CASE_ID,
            7,
            PRESENTATION_SHA256,
            _sample(),
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert rejected.value.code is QualityOcrReviewErrorCode.INTERNAL_ERROR


@pytest.mark.parametrize(
    ("deadline", "cancellation", "expected"),
    [
        (lambda: True, lambda: False, QualityOcrReviewErrorCode.TIMED_OUT),
        (lambda: False, lambda: True, QualityOcrReviewErrorCode.CANCELLED),
    ],
)
def test_service_honors_deadline_and_cancellation_before_inspection(
    deadline: object,
    cancellation: object,
    expected: QualityOcrReviewErrorCode,
) -> None:
    processor = _Processor(QualityOcrReviewStatus.SUCCEEDED, 4, 82_500)
    with pytest.raises(QualityOcrReviewError) as rejected:
        QualityOcrReviewService(processor, language_sets=_Languages()).review_sample(
            CASE_ID,
            7,
            PRESENTATION_SHA256,
            _sample(),
            deadline=deadline,  # type: ignore[arg-type]
            cancellation=cancellation,  # type: ignore[arg-type]
        )

    assert rejected.value.code is expected
    assert processor.requests == []


def test_errors_do_not_retain_private_values_or_chained_parser_details() -> None:
    private = "secret OCR word at C:/private/source.tsv"
    with pytest.raises(QualityOcrReviewError) as rejected:
        decode_quality_ocr_review_report(private.encode("ascii"))

    rendered = f"{rejected.value!r} {rejected.value}"
    assert "secret" not in rendered.lower()
    assert "source.tsv" not in rendered.lower()
    assert rejected.value.__cause__ is None


def test_error_context_is_bounded_even_when_each_segment_is_well_formed() -> None:
    context = f"quality_ocr.{'a' * 60}.{'b' * 60}"

    error = QualityOcrReviewError(QualityOcrReviewErrorCode.MODEL_INVALID, context)

    assert len(context) > 128
    assert error.code is QualityOcrReviewErrorCode.INTERNAL_ERROR
    assert error.context == "quality_ocr.internal"

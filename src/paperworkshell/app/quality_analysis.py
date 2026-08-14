"""Bounded, revision-bound orchestration for advisory page-quality review.

The service consumes only worker-produced, validated preview rasters.  Review
snapshots are ephemeral: they contain typed findings and exact internal page
identities, but never pixels, source paths, filenames, OCR text, or manifest
containers.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Never, Protocol, cast
from uuid import UUID

from paperworkshell.app.page_previews import (
    MAX_PAGE_PREVIEW_TIMEOUT_SECONDS,
    PagePreviewError,
    PagePreviewErrorCode,
    PagePreviewProgressSink,
    PagePreviewRequest,
    PagePreviewResult,
)
from paperworkshell.app.page_previews import (
    CancellationProbe as PreviewCancellationProbe,
)
from paperworkshell.app.quality_ocr_review import QualityOcrReviewProducer
from paperworkshell.domain.image_pages import (
    ImagePageError,
    ImagePageErrorCode,
    ImagePagePreviewRequest,
    ImagePagePreviewResult,
    ImageQuarterTurn,
)
from paperworkshell.domain.image_transforms import (
    ImagePageTransformRequest,
    ImagePageTransformResult,
    ImageTransformIntent,
)
from paperworkshell.domain.manifest_repository import MAX_SAFE_MANIFEST_INTEGER
from paperworkshell.domain.page_operations import MAX_PAGE_COUNT
from paperworkshell.domain.pdf_rendering import PdfRenderRotation
from paperworkshell.domain.pdf_thumbnails import (
    PdfThumbnailError,
    PdfThumbnailErrorCode,
    PdfThumbnailRequest,
    PdfThumbnailResult,
)
from paperworkshell.domain.quality_analysis import (
    DEFAULT_QUALITY_ANALYSIS_PROFILE,
    MAX_QUALITY_FINDINGS_PER_PAGE,
    OcrReviewFact,
    QualityAnalysisProfile,
    QualityFinding,
    QualityFindingCode,
    QualityPageFingerprint,
    QualityPageKind,
    QualityPageSample,
    analyze_quality_page,
    quality_finding_sort_key,
    suspected_duplicate_finding,
)
from paperworkshell.domain.quality_analysis import (
    QualityAnalysisError as DomainQualityAnalysisError,
)
from paperworkshell.domain.quality_analysis import (
    QualityAnalysisErrorCode as DomainQualityAnalysisErrorCode,
)
from paperworkshell.domain.quality_ocr_review import (
    QualityOcrReviewError,
    QualityOcrReviewErrorCode,
)

QUALITY_ANALYSIS_RASTER_SIZE_PIXELS: Final = 256
QUALITY_ANALYSIS_METADATA_RASTER_SIZE_PIXELS: Final = 1
DEFAULT_QUALITY_ANALYSIS_TIMEOUT_SECONDS: Final = MAX_PAGE_PREVIEW_TIMEOUT_SECONDS
MAX_QUALITY_ANALYSIS_TIMEOUT_SECONDS: Final = MAX_PAGE_PREVIEW_TIMEOUT_SECONDS
QUALITY_ANALYSIS_REVIEW_VERSION: Final = 1
QUALITY_ANALYSIS_PROGRESS_VERSION: Final = 1
_CONTEXT_TOKEN: Final = re.compile(r"(?=.{1,128}\Z)quality(?:\.[a-z][a-z0-9_]*)+\Z")
_PROFILE_TOKEN: Final = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
_MAX_PROFILE_ID_CHARACTERS: Final = 128
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")


class QualityAnalysisErrorCode(StrEnum):
    """Closed registry of sanitized quality-review orchestration failures."""

    MODEL_INVALID = "QUALITY_ANALYSIS_MODEL_INVALID"
    CANCELLED = "QUALITY_ANALYSIS_CANCELLED"
    TIMED_OUT = "QUALITY_ANALYSIS_TIMED_OUT"
    PREVIEW_FAILED = "QUALITY_ANALYSIS_PREVIEW_FAILED"
    LIMIT_EXCEEDED = "QUALITY_ANALYSIS_LIMIT_EXCEEDED"
    RESULT_INVALID = "QUALITY_ANALYSIS_RESULT_INVALID"
    SAVE_PENDING = "QUALITY_ANALYSIS_SAVE_PENDING"
    STALE = "QUALITY_ANALYSIS_STALE"
    INTERNAL_ERROR = "QUALITY_ANALYSIS_INTERNAL_ERROR"


_SAFE_ERROR_MESSAGES: Final[dict[QualityAnalysisErrorCode, str]] = {
    QualityAnalysisErrorCode.MODEL_INVALID: (
        "PaperWorkShell received invalid page-quality review data."
    ),
    QualityAnalysisErrorCode.CANCELLED: "Page-quality review was cancelled.",
    QualityAnalysisErrorCode.TIMED_OUT: ("Page-quality review exceeded its configured time limit."),
    QualityAnalysisErrorCode.PREVIEW_FAILED: (
        "PaperWorkShell could not prepare a page for quality review safely."
    ),
    QualityAnalysisErrorCode.LIMIT_EXCEEDED: (
        "Page-quality review exceeds the selected resource-policy limit."
    ),
    QualityAnalysisErrorCode.RESULT_INVALID: (
        "PaperWorkShell received an invalid page-quality review result."
    ),
    QualityAnalysisErrorCode.SAVE_PENDING: (
        "Wait for the current case changes to finish saving, then review page quality."
    ),
    QualityAnalysisErrorCode.STALE: (
        "The case changed before page-quality review finished. Start the review again."
    ),
    QualityAnalysisErrorCode.INTERNAL_ERROR: (
        "PaperWorkShell could not complete page-quality review safely."
    ),
}


@dataclass(slots=True, repr=False)
class QualityAnalysisError(ValueError):
    """Stable failure retaining no paths, pixels, OCR text, or native details."""

    code: QualityAnalysisErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not QualityAnalysisErrorCode:
            self.code = QualityAnalysisErrorCode.INTERNAL_ERROR
        if type(self.context) is not str or _CONTEXT_TOKEN.fullmatch(self.context) is None:
            self.context = "quality.operation"
        self.safe_message = _SAFE_ERROR_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"QualityAnalysisError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_quality_analysis(code: QualityAnalysisErrorCode, context: str) -> Never:
    """Raise one closed quality-review failure without an exception chain."""

    error = QualityAnalysisError(code, context)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


class QualityAnalysisProgressPhase(StrEnum):
    """Content-free aggregate phases for one complete review snapshot."""

    PREVIEWING = "previewing"
    OCR_REVIEWING = "ocr_reviewing"
    ANALYZING = "analyzing"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class QualityAnalysisProgress:
    """One path- and content-free progress checkpoint."""

    phase: QualityAnalysisProgressPhase
    completed_pages: int
    total_pages: int
    progress_version: int = QUALITY_ANALYSIS_PROGRESS_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.phase) is not QualityAnalysisProgressPhase
            or type(self.completed_pages) is not int
            or type(self.total_pages) is not int
            or not 1 <= self.total_pages <= MAX_PAGE_COUNT
            or not 0 <= self.completed_pages <= self.total_pages
            or (
                self.phase is QualityAnalysisProgressPhase.COMPLETE
                and self.completed_pages != self.total_pages
            )
            or type(self.progress_version) is not int
            or self.progress_version != QUALITY_ANALYSIS_PROGRESS_VERSION
        ):
            reject_quality_analysis(QualityAnalysisErrorCode.MODEL_INVALID, "quality.progress")


class QualityAnalysisProgressSink(Protocol):
    def __call__(self, progress: QualityAnalysisProgress, /) -> None: ...


class QualityAnalysisCancellationProbe(Protocol):
    def __call__(self) -> bool: ...


class QualityPageReviewKind(StrEnum):
    """Closed source families required to construct the correct preview request."""

    PDF = "pdf"
    IMAGE = "image"


@dataclass(frozen=True, slots=True, repr=False)
class QualityPageReviewRequest:
    """One active page's current presentation intent, without document content."""

    page_id: UUID
    kind: QualityPageReviewKind
    pdf_rotation: PdfRenderRotation | None = None
    image_intent: ImageTransformIntent | None = None

    def __post_init__(self) -> None:
        if type(self.page_id) is not UUID or self.page_id.version != 4:
            reject_quality_analysis(QualityAnalysisErrorCode.MODEL_INVALID, "quality.page_id")
        if type(self.kind) is not QualityPageReviewKind:
            reject_quality_analysis(QualityAnalysisErrorCode.MODEL_INVALID, "quality.page_kind")
        if self.kind is QualityPageReviewKind.PDF:
            if type(self.pdf_rotation) is not PdfRenderRotation or self.image_intent is not None:
                reject_quality_analysis(QualityAnalysisErrorCode.MODEL_INVALID, "quality.page")
        elif self.pdf_rotation is not None or type(self.image_intent) is not ImageTransformIntent:
            reject_quality_analysis(QualityAnalysisErrorCode.MODEL_INVALID, "quality.page")

    @property
    def domain_kind(self) -> QualityPageKind:
        """Translate app request routing to the pure-domain source family."""

        return (
            QualityPageKind.PDF if self.kind is QualityPageReviewKind.PDF else QualityPageKind.IMAGE
        )

    def __repr__(self) -> str:
        return f"QualityPageReviewRequest(kind={self.kind.value!r})"


@dataclass(frozen=True, slots=True, repr=False)
class QualityAnalysisRequest:
    """Exact saved case revision and complete ordered active-page coverage."""

    case_id: UUID
    manifest_revision: int
    pages: tuple[QualityPageReviewRequest, ...]

    def __post_init__(self) -> None:
        if (
            type(self.case_id) is not UUID
            or self.case_id.version != 4
            or type(self.manifest_revision) is not int
            or not 0 <= self.manifest_revision <= MAX_SAFE_MANIFEST_INTEGER
            or type(self.pages) is not tuple
            or any(type(page) is not QualityPageReviewRequest for page in self.pages)
            or len(self.pages) > MAX_PAGE_COUNT
        ):
            reject_quality_analysis(QualityAnalysisErrorCode.MODEL_INVALID, "quality.request")
        page_ids = tuple(page.page_id for page in self.pages)
        if len(set(page_ids)) != len(page_ids) or self.case_id in page_ids:
            reject_quality_analysis(QualityAnalysisErrorCode.MODEL_INVALID, "quality.pages")

    @property
    def page_ids(self) -> tuple[UUID, ...]:
        return tuple(page.page_id for page in self.pages)

    def __repr__(self) -> str:
        return (
            "QualityAnalysisRequest("
            f"manifest_revision={self.manifest_revision!r}, page_count={len(self.pages)})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class QualityReviewSnapshot:
    """Ephemeral findings bound to one exact saved organizer revision."""

    case_id: UUID
    manifest_revision: int
    page_ids: tuple[UUID, ...]
    findings: tuple[QualityFinding, ...]
    profile_id: str
    profile_sha256: str = field(repr=False)
    review_version: int = QUALITY_ANALYSIS_REVIEW_VERSION

    def __post_init__(self) -> None:
        if type(self.page_ids) is not tuple or any(
            type(page_id) is not UUID or page_id.version != 4 for page_id in self.page_ids
        ):
            reject_quality_analysis(QualityAnalysisErrorCode.RESULT_INVALID, "quality.snapshot")
        position_by_id = {
            page_id: position for position, page_id in enumerate(self.page_ids, start=1)
        }
        if (
            type(self.case_id) is not UUID
            or self.case_id.version != 4
            or type(self.manifest_revision) is not int
            or not 0 <= self.manifest_revision <= MAX_SAFE_MANIFEST_INTEGER
            or len(set(self.page_ids)) != len(self.page_ids)
            or len(self.page_ids) > MAX_PAGE_COUNT
            or self.case_id in self.page_ids
            or type(self.findings) is not tuple
            or any(type(finding) is not QualityFinding for finding in self.findings)
            or type(self.profile_id) is not str
            or len(self.profile_id) > _MAX_PROFILE_ID_CHARACTERS
            or _PROFILE_TOKEN.fullmatch(self.profile_id) is None
            or type(self.profile_sha256) is not str
            or _SHA256.fullmatch(self.profile_sha256) is None
            or type(self.review_version) is not int
            or self.review_version != QUALITY_ANALYSIS_REVIEW_VERSION
            or len(self.findings) > MAX_QUALITY_FINDINGS_PER_PAGE * len(self.page_ids)
        ):
            reject_quality_analysis(QualityAnalysisErrorCode.RESULT_INVALID, "quality.snapshot")
        if tuple(self.findings) != tuple(
            sorted(self.findings, key=quality_finding_sort_key)
        ) or len(set(self.findings)) != len(self.findings):
            reject_quality_analysis(QualityAnalysisErrorCode.RESULT_INVALID, "quality.findings")
        finding_counts_by_page: dict[UUID, int] = {}
        finding_codes_by_page: dict[UUID, set[QualityFindingCode]] = {}
        for finding in self.findings:
            if position_by_id.get(finding.page_id) != finding.page_position:
                reject_quality_analysis(
                    QualityAnalysisErrorCode.RESULT_INVALID,
                    "quality.finding_binding",
                )
            finding_counts_by_page[finding.page_id] = (
                finding_counts_by_page.get(finding.page_id, 0) + 1
            )
            page_codes = finding_codes_by_page.setdefault(finding.page_id, set())
            if finding.code in page_codes:
                reject_quality_analysis(
                    QualityAnalysisErrorCode.RESULT_INVALID,
                    "quality.page_finding_codes",
                )
            page_codes.add(finding.code)
            if finding_counts_by_page[finding.page_id] > MAX_QUALITY_FINDINGS_PER_PAGE:
                reject_quality_analysis(
                    QualityAnalysisErrorCode.RESULT_INVALID,
                    "quality.page_finding_count",
                )
            if (
                finding.related_page_id is not None
                and position_by_id.get(finding.related_page_id) != finding.related_page_position
            ):
                reject_quality_analysis(
                    QualityAnalysisErrorCode.RESULT_INVALID,
                    "quality.related_binding",
                )

    def __repr__(self) -> str:
        return (
            "QualityReviewSnapshot("
            f"manifest_revision={self.manifest_revision!r}, "
            f"page_count={len(self.page_ids)}, finding_count={len(self.findings)}, "
            f"profile_id={self.profile_id!r})"
        )


class _PagePreviewServicePort(Protocol):
    def get_previews(
        self,
        requests: tuple[PagePreviewRequest, ...],
        *,
        timeout_seconds: float,
        cancellation: PreviewCancellationProbe | None = None,
        progress: PagePreviewProgressSink | None = None,
    ) -> tuple[PagePreviewResult, ...]: ...


class MonotonicClock(Protocol):
    def __call__(self) -> float: ...


class _QualityOperation:
    """Own one total deadline and safely probe caller cancellation."""

    __slots__ = ("_cancellation", "_expires_at", "_monotonic")

    def __init__(
        self,
        timeout_seconds: float,
        cancellation: QualityAnalysisCancellationProbe | None,
        monotonic: MonotonicClock,
    ) -> None:
        timeout = _require_timeout(timeout_seconds)
        self._monotonic = monotonic
        self._cancellation = cancellation
        expires_at = _read_monotonic(monotonic) + timeout
        if not math.isfinite(expires_at):
            reject_quality_analysis(
                QualityAnalysisErrorCode.INTERNAL_ERROR,
                "quality.deadline",
            )
        self._expires_at = expires_at

    def require_active(self, context: str) -> None:
        if self.remaining_seconds(context) <= 0.0:
            reject_quality_analysis(QualityAnalysisErrorCode.TIMED_OUT, context)
        callback = self._cancellation
        if callback is not None:
            try:
                cancelled = callback()
            except BaseException:
                reject_quality_analysis(
                    QualityAnalysisErrorCode.INTERNAL_ERROR,
                    "quality.cancel_probe",
                )
            if type(cancelled) is not bool:
                reject_quality_analysis(
                    QualityAnalysisErrorCode.INTERNAL_ERROR,
                    "quality.cancel_probe",
                )
            if cancelled:
                reject_quality_analysis(QualityAnalysisErrorCode.CANCELLED, context)
        if self.remaining_seconds(context) <= 0.0:
            reject_quality_analysis(QualityAnalysisErrorCode.TIMED_OUT, context)

    def remaining_seconds(self, context: str) -> float:
        remaining = self._expires_at - _read_monotonic(self._monotonic)
        if not math.isfinite(remaining):
            reject_quality_analysis(QualityAnalysisErrorCode.INTERNAL_ERROR, context)
        return max(0.0, remaining)

    def deadline_expired(self) -> bool:
        return self.remaining_seconds("quality.ocr_deadline") <= 0.0

    def cancellation_requested(self) -> bool:
        callback = self._cancellation
        if callback is None:
            return False
        try:
            value = callback()
        except BaseException:
            reject_quality_analysis(
                QualityAnalysisErrorCode.INTERNAL_ERROR,
                "quality.cancel_probe",
            )
        if type(value) is not bool:
            reject_quality_analysis(
                QualityAnalysisErrorCode.INTERNAL_ERROR,
                "quality.cancel_probe",
            )
        return value


class _DeferredQualityFailure:
    """Translate safe callback failure into a cancellation signal for preview ports."""

    __slots__ = ("_failure", "_operation")

    def __init__(self, operation: _QualityOperation) -> None:
        self._operation = operation
        self._failure: tuple[QualityAnalysisErrorCode, str] | None = None

    def __call__(self) -> bool:
        if self._failure is not None:
            return True
        try:
            self._operation.require_active("quality.preview_active")
        except QualityAnalysisError as error:
            self._failure = (error.code, error.context)
            return True
        except BaseException:
            self._failure = (
                QualityAnalysisErrorCode.INTERNAL_ERROR,
                "quality.preview_active",
            )
            return True
        return False

    def raise_if_pending(self) -> None:
        if self._failure is not None:
            reject_quality_analysis(*self._failure)


class QualityAnalysisService:
    """Analyze exact active-page coverage while retaining at most one page raster."""

    __slots__ = (
        "_maximum_page_count",
        "_monotonic",
        "_ocr_review",
        "_previews",
        "_profile",
    )

    def __init__(
        self,
        previews: _PagePreviewServicePort,
        *,
        profile: QualityAnalysisProfile = DEFAULT_QUALITY_ANALYSIS_PROFILE,
        maximum_page_count: int = 1_000,
        ocr_review: QualityOcrReviewProducer | None = None,
        monotonic: MonotonicClock = time.monotonic,
    ) -> None:
        if (
            not callable(getattr(previews, "get_previews", None))
            or type(profile) is not QualityAnalysisProfile
            or type(maximum_page_count) is not int
            or not 1 <= maximum_page_count <= MAX_PAGE_COUNT
            or (ocr_review is not None and not isinstance(ocr_review, QualityOcrReviewProducer))
            or not callable(monotonic)
        ):
            reject_quality_analysis(QualityAnalysisErrorCode.MODEL_INVALID, "quality.service")
        self._previews = previews
        self._profile = profile
        self._maximum_page_count = maximum_page_count
        self._ocr_review = ocr_review
        self._monotonic = monotonic

    @property
    def profile_id(self) -> str:
        return self._profile.profile_id

    @property
    def profile_sha256(self) -> str:
        return self._profile.profile_sha256

    def analyze(
        self,
        request: QualityAnalysisRequest,
        *,
        timeout_seconds: float = DEFAULT_QUALITY_ANALYSIS_TIMEOUT_SECONDS,
        cancellation: QualityAnalysisCancellationProbe | None = None,
        progress: QualityAnalysisProgressSink | None = None,
    ) -> QualityReviewSnapshot:
        """Return exact, advisory findings without retaining page pixels."""

        if (
            type(request) is not QualityAnalysisRequest
            or (cancellation is not None and not callable(cancellation))
            or (progress is not None and not callable(progress))
        ):
            reject_quality_analysis(QualityAnalysisErrorCode.MODEL_INVALID, "quality.analyze")
        if len(request.pages) > self._maximum_page_count:
            reject_quality_analysis(
                QualityAnalysisErrorCode.LIMIT_EXCEEDED,
                "quality.page_limit",
            )
        timeout = _require_timeout(timeout_seconds)
        operation = _QualityOperation(timeout, cancellation, self._monotonic)
        operation.require_active("quality.before_analysis")
        total_pages = len(request.pages)
        if total_pages == 0:
            return QualityReviewSnapshot(
                case_id=request.case_id,
                manifest_revision=request.manifest_revision,
                page_ids=(),
                findings=(),
                profile_id=self._profile.profile_id,
                profile_sha256=self._profile.profile_sha256,
            )

        findings: list[QualityFinding] = []
        prior_fingerprints: list[QualityPageFingerprint] = []
        for index, page in enumerate(request.pages):
            operation.require_active("quality.before_page")
            self._emit_progress(
                progress,
                operation,
                QualityAnalysisProgress(
                    QualityAnalysisProgressPhase.PREVIEWING,
                    index,
                    total_pages,
                ),
            )
            sample = self._sample_for_page(
                request.case_id,
                page,
                page_position=index + 1,
                operation=operation,
            )
            ocr_fact: OcrReviewFact | None = None
            if self._ocr_review is not None:
                self._emit_progress(
                    progress,
                    operation,
                    QualityAnalysisProgress(
                        QualityAnalysisProgressPhase.OCR_REVIEWING,
                        index,
                        total_pages,
                    ),
                )
                ocr_fact = self._ocr_fact_for_sample(
                    request,
                    page,
                    sample,
                    operation=operation,
                )
            self._emit_progress(
                progress,
                operation,
                QualityAnalysisProgress(
                    QualityAnalysisProgressPhase.ANALYZING,
                    index,
                    total_pages,
                ),
            )
            try:
                analysis = analyze_quality_page(
                    sample,
                    ocr_fact=ocr_fact,
                    profile=self._profile,
                )
            except DomainQualityAnalysisError as error:
                _rebuild_domain_error(error)
            except BaseException:
                reject_quality_analysis(
                    QualityAnalysisErrorCode.INTERNAL_ERROR,
                    "quality.domain_analyzer",
                )
            finally:
                del sample
            if analysis.page_id != page.page_id or analysis.page_position != index + 1:
                reject_quality_analysis(
                    QualityAnalysisErrorCode.RESULT_INVALID,
                    "quality.domain_binding",
                )
            page_findings = list(analysis.findings)
            for prior_index, prior in enumerate(prior_fingerprints):
                if prior_index % 32 == 0:
                    operation.require_active("quality.duplicate_scan")
                try:
                    duplicate = suspected_duplicate_finding(
                        analysis.fingerprint,
                        prior,
                        profile=self._profile,
                    )
                except DomainQualityAnalysisError as error:
                    _rebuild_domain_error(error)
                except BaseException:
                    reject_quality_analysis(
                        QualityAnalysisErrorCode.INTERNAL_ERROR,
                        "quality.domain_duplicate",
                    )
                if duplicate is not None:
                    page_findings.append(duplicate)
                    break
            if len(page_findings) > MAX_QUALITY_FINDINGS_PER_PAGE:
                reject_quality_analysis(
                    QualityAnalysisErrorCode.RESULT_INVALID,
                    "quality.page_finding_count",
                )
            findings.extend(page_findings)
            prior_fingerprints.append(analysis.fingerprint)
            del analysis
            operation.require_active("quality.after_page")

        findings.sort(key=quality_finding_sort_key)
        self._emit_progress(
            progress,
            operation,
            QualityAnalysisProgress(
                QualityAnalysisProgressPhase.COMPLETE,
                total_pages,
                total_pages,
            ),
        )
        return QualityReviewSnapshot(
            case_id=request.case_id,
            manifest_revision=request.manifest_revision,
            page_ids=request.page_ids,
            findings=tuple(findings),
            profile_id=self._profile.profile_id,
            profile_sha256=self._profile.profile_sha256,
        )

    def _ocr_fact_for_sample(
        self,
        request: QualityAnalysisRequest,
        page: QualityPageReviewRequest,
        sample: QualityPageSample,
        *,
        operation: _QualityOperation,
    ) -> OcrReviewFact:
        producer = self._ocr_review
        if producer is None:
            reject_quality_analysis(
                QualityAnalysisErrorCode.INTERNAL_ERROR,
                "quality.ocr_service",
            )
        try:
            fact = producer.review_sample(
                request.case_id,
                request.manifest_revision,
                _presentation_sha256(page),
                sample,
                deadline=operation.deadline_expired,
                cancellation=operation.cancellation_requested,
            )
        except QualityOcrReviewError as error:
            _rebuild_ocr_review_error(error)
        except BaseException:
            reject_quality_analysis(
                QualityAnalysisErrorCode.INTERNAL_ERROR,
                "quality.ocr_service",
            )
        if type(fact) is not OcrReviewFact or fact.page_id != page.page_id:
            reject_quality_analysis(
                QualityAnalysisErrorCode.RESULT_INVALID,
                "quality.ocr_binding",
            )
        operation.require_active("quality.after_ocr_review")
        return fact

    def _sample_for_page(
        self,
        case_id: UUID,
        page: QualityPageReviewRequest,
        *,
        page_position: int,
        operation: _QualityOperation,
    ) -> QualityPageSample:
        """Build one bounded sample, dropping each preview result before return."""

        if page.kind is QualityPageReviewKind.PDF:
            request = PdfThumbnailRequest(
                case_id=case_id,
                page_id=page.page_id,
                maximum_width_pixels=QUALITY_ANALYSIS_RASTER_SIZE_PIXELS,
                maximum_height_pixels=QUALITY_ANALYSIS_RASTER_SIZE_PIXELS,
                additional_rotation=cast(PdfRenderRotation, page.pdf_rotation),
            )
            result = self._get_one(request, operation)
            if type(result) is not PdfThumbnailResult:
                reject_quality_analysis(
                    QualityAnalysisErrorCode.RESULT_INVALID,
                    "quality.pdf_result",
                )
            pdf_snapshot = result.snapshot
            sample = QualityPageSample(
                page_id=page.page_id,
                page_position=page_position,
                page_kind=QualityPageKind.PDF,
                width_pixels=pdf_snapshot.width_pixels,
                height_pixels=pdf_snapshot.height_pixels,
                row_stride_bytes=pdf_snapshot.row_stride_bytes,
                pixels=pdf_snapshot.pixels,
            )
            del result
            return sample

        metadata_request = ImagePagePreviewRequest(
            case_id=case_id,
            page_id=page.page_id,
            maximum_width_pixels=QUALITY_ANALYSIS_METADATA_RASTER_SIZE_PIXELS,
            maximum_height_pixels=QUALITY_ANALYSIS_METADATA_RASTER_SIZE_PIXELS,
            additional_rotation=ImageQuarterTurn.DEGREES_0,
        )
        metadata_result = self._get_one(metadata_request, operation)
        if type(metadata_result) is not ImagePagePreviewResult:
            reject_quality_analysis(
                QualityAnalysisErrorCode.RESULT_INVALID,
                "quality.image_metadata_result",
            )
        metadata = metadata_result.snapshot.metadata
        source_width_pixels = metadata.encoded_width_pixels
        source_height_pixels = metadata.encoded_height_pixels
        del metadata_result
        transform_request = ImagePageTransformRequest(
            case_id=case_id,
            page_id=page.page_id,
            intent=cast(ImageTransformIntent, page.image_intent),
            maximum_width_pixels=QUALITY_ANALYSIS_RASTER_SIZE_PIXELS,
            maximum_height_pixels=QUALITY_ANALYSIS_RASTER_SIZE_PIXELS,
        )
        transform_result = self._get_one(transform_request, operation)
        if type(transform_result) is not ImagePageTransformResult:
            reject_quality_analysis(
                QualityAnalysisErrorCode.RESULT_INVALID,
                "quality.image_transform_result",
            )
        image_snapshot = transform_result.snapshot
        sample = QualityPageSample(
            page_id=page.page_id,
            page_position=page_position,
            page_kind=QualityPageKind.IMAGE,
            width_pixels=image_snapshot.width_pixels,
            height_pixels=image_snapshot.height_pixels,
            row_stride_bytes=image_snapshot.row_stride_bytes,
            pixels=transform_result.pixels,
            source_width_pixels=source_width_pixels,
            source_height_pixels=source_height_pixels,
        )
        del transform_result
        return sample

    def _get_one(
        self,
        request: PagePreviewRequest,
        operation: _QualityOperation,
    ) -> PagePreviewResult:
        """Render exactly one page request under the remaining total deadline."""

        operation.require_active("quality.before_preview")
        remaining = operation.remaining_seconds("quality.preview_deadline")
        if remaining <= 0.0:
            reject_quality_analysis(
                QualityAnalysisErrorCode.TIMED_OUT,
                "quality.preview_deadline",
            )
        cancellation = _DeferredQualityFailure(operation)
        try:
            results = self._previews.get_previews(
                (request,),
                timeout_seconds=min(MAX_PAGE_PREVIEW_TIMEOUT_SECONDS, remaining),
                cancellation=cancellation,
                progress=None,
            )
        except PagePreviewError as error:
            cancellation.raise_if_pending()
            _rebuild_preview_error(error)
        except ImagePageError as error:
            cancellation.raise_if_pending()
            _rebuild_image_error(error)
        except PdfThumbnailError as error:
            cancellation.raise_if_pending()
            _rebuild_pdf_error(error)
        except QualityAnalysisError:
            cancellation.raise_if_pending()
            raise
        except BaseException:
            cancellation.raise_if_pending()
            reject_quality_analysis(
                QualityAnalysisErrorCode.PREVIEW_FAILED,
                "quality.preview_service",
            )
        cancellation.raise_if_pending()
        operation.require_active("quality.after_preview")
        if (
            type(results) is not tuple
            or len(results) != 1
            or type(results[0])
            not in {
                # The exact result/request binding is also enforced by each
                # domain result model and the mixed-preview service.
                PdfThumbnailResult,
                ImagePagePreviewResult,
                ImagePageTransformResult,
            }
            or results[0].request != request
        ):
            reject_quality_analysis(
                QualityAnalysisErrorCode.RESULT_INVALID,
                "quality.preview_result",
            )
        return results[0]

    def _emit_progress(
        self,
        sink: QualityAnalysisProgressSink | None,
        operation: _QualityOperation,
        progress: QualityAnalysisProgress,
    ) -> None:
        operation.require_active("quality.before_progress")
        if sink is not None:
            try:
                sink(progress)
            except BaseException:
                reject_quality_analysis(
                    QualityAnalysisErrorCode.INTERNAL_ERROR,
                    "quality.progress_sink",
                )
        operation.require_active("quality.after_progress")


def _presentation_sha256(page: QualityPageReviewRequest) -> str:
    """Bind OCR facts to the exact rotation/transform that produced the sample."""

    digest = hashlib.sha256()
    digest.update(b"paperworkshell-quality-presentation-v1\0")
    digest.update(page.kind.value.encode("ascii"))
    digest.update(b"\0")
    if page.kind is QualityPageReviewKind.PDF:
        rotation = cast(PdfRenderRotation, page.pdf_rotation)
        digest.update(int(rotation).to_bytes(2, "big", signed=False))
    else:
        intent = cast(ImageTransformIntent, page.image_intent)
        digest.update(bytes.fromhex(intent.intent_sha256))
    return digest.hexdigest()


def _rebuild_ocr_review_error(error: QualityOcrReviewError) -> Never:
    if type(error) is not QualityOcrReviewError:
        reject_quality_analysis(
            QualityAnalysisErrorCode.INTERNAL_ERROR,
            "quality.ocr_service",
        )
    if error.code is QualityOcrReviewErrorCode.CANCELLED:
        reject_quality_analysis(QualityAnalysisErrorCode.CANCELLED, "quality.ocr_review")
    if error.code is QualityOcrReviewErrorCode.TIMED_OUT:
        reject_quality_analysis(QualityAnalysisErrorCode.TIMED_OUT, "quality.ocr_review")
    if error.code is QualityOcrReviewErrorCode.LIMIT_EXCEEDED:
        reject_quality_analysis(QualityAnalysisErrorCode.LIMIT_EXCEEDED, "quality.ocr_review")
    if error.code is QualityOcrReviewErrorCode.RESULT_INVALID:
        reject_quality_analysis(QualityAnalysisErrorCode.RESULT_INVALID, "quality.ocr_review")
    reject_quality_analysis(QualityAnalysisErrorCode.INTERNAL_ERROR, "quality.ocr_review")


def _rebuild_domain_error(error: DomainQualityAnalysisError) -> Never:
    if type(error) is not DomainQualityAnalysisError:
        reject_quality_analysis(
            QualityAnalysisErrorCode.INTERNAL_ERROR,
            "quality.domain_error",
        )
    if error.code in {
        DomainQualityAnalysisErrorCode.MODEL_INVALID,
        DomainQualityAnalysisErrorCode.LIMIT_EXCEEDED,
        DomainQualityAnalysisErrorCode.RESULT_INVALID,
    }:
        reject_quality_analysis(
            QualityAnalysisErrorCode.RESULT_INVALID,
            "quality.domain_result",
        )
    reject_quality_analysis(
        QualityAnalysisErrorCode.INTERNAL_ERROR,
        "quality.domain_error",
    )


def _require_timeout(value: object) -> float:
    if type(value) not in {int, float}:
        reject_quality_analysis(QualityAnalysisErrorCode.MODEL_INVALID, "quality.timeout")
    timeout = float(cast(int | float, value))
    if not math.isfinite(timeout) or not 0.0 < timeout <= MAX_QUALITY_ANALYSIS_TIMEOUT_SECONDS:
        reject_quality_analysis(QualityAnalysisErrorCode.MODEL_INVALID, "quality.timeout")
    return timeout


def _read_monotonic(monotonic: MonotonicClock) -> float:
    try:
        value = monotonic()
    except BaseException:
        reject_quality_analysis(
            QualityAnalysisErrorCode.INTERNAL_ERROR,
            "quality.clock",
        )
    if type(value) not in {int, float}:
        reject_quality_analysis(
            QualityAnalysisErrorCode.INTERNAL_ERROR,
            "quality.clock",
        )
    selected = float(value)
    if not math.isfinite(selected):
        reject_quality_analysis(
            QualityAnalysisErrorCode.INTERNAL_ERROR,
            "quality.clock",
        )
    return selected


def _rebuild_preview_error(error: PagePreviewError) -> Never:
    if type(error) is not PagePreviewError:
        reject_quality_analysis(
            QualityAnalysisErrorCode.PREVIEW_FAILED,
            "quality.preview_service",
        )
    if error.code is PagePreviewErrorCode.CANCELLED:
        reject_quality_analysis(QualityAnalysisErrorCode.CANCELLED, "quality.preview")
    if error.code is PagePreviewErrorCode.TIMED_OUT:
        reject_quality_analysis(QualityAnalysisErrorCode.TIMED_OUT, "quality.preview")
    if error.code is PagePreviewErrorCode.RESULT_INVALID:
        reject_quality_analysis(QualityAnalysisErrorCode.RESULT_INVALID, "quality.preview")
    reject_quality_analysis(QualityAnalysisErrorCode.PREVIEW_FAILED, "quality.preview")


def _rebuild_image_error(error: ImagePageError) -> Never:
    if type(error) is not ImagePageError:
        reject_quality_analysis(
            QualityAnalysisErrorCode.PREVIEW_FAILED,
            "quality.image_preview",
        )
    if error.code is ImagePageErrorCode.CANCELLED:
        reject_quality_analysis(QualityAnalysisErrorCode.CANCELLED, "quality.image_preview")
    if error.code is ImagePageErrorCode.TIMED_OUT:
        reject_quality_analysis(QualityAnalysisErrorCode.TIMED_OUT, "quality.image_preview")
    if error.code is ImagePageErrorCode.LIMIT_EXCEEDED:
        reject_quality_analysis(
            QualityAnalysisErrorCode.LIMIT_EXCEEDED,
            "quality.image_preview",
        )
    if error.code is ImagePageErrorCode.RESULT_INVALID:
        reject_quality_analysis(
            QualityAnalysisErrorCode.RESULT_INVALID,
            "quality.image_preview",
        )
    reject_quality_analysis(QualityAnalysisErrorCode.PREVIEW_FAILED, "quality.image_preview")


def _rebuild_pdf_error(error: PdfThumbnailError) -> Never:
    if type(error) is not PdfThumbnailError:
        reject_quality_analysis(
            QualityAnalysisErrorCode.PREVIEW_FAILED,
            "quality.pdf_preview",
        )
    if error.code is PdfThumbnailErrorCode.CANCELLED:
        reject_quality_analysis(QualityAnalysisErrorCode.CANCELLED, "quality.pdf_preview")
    if error.code is PdfThumbnailErrorCode.TIMED_OUT:
        reject_quality_analysis(QualityAnalysisErrorCode.TIMED_OUT, "quality.pdf_preview")
    if error.code is PdfThumbnailErrorCode.LIMIT_EXCEEDED:
        reject_quality_analysis(
            QualityAnalysisErrorCode.LIMIT_EXCEEDED,
            "quality.pdf_preview",
        )
    if error.code is PdfThumbnailErrorCode.RESULT_INVALID:
        reject_quality_analysis(
            QualityAnalysisErrorCode.RESULT_INVALID,
            "quality.pdf_preview",
        )
    reject_quality_analysis(QualityAnalysisErrorCode.PREVIEW_FAILED, "quality.pdf_preview")

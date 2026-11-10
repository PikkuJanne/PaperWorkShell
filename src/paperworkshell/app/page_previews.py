"""Toolkit-free orchestration for bounded, ordered PDF and image previews."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Never, Protocol, cast
from uuid import UUID, uuid4

from paperworkshell.app.pdf_thumbnails import (
    DEFAULT_PDF_THUMBNAIL_TIMEOUT_SECONDS,
    MAX_PDF_THUMBNAIL_TIMEOUT_SECONDS,
)
from paperworkshell.domain.image_pages import (
    ImagePageError,
    ImagePagePreviewProgressPhase,
    ImagePagePreviewRequest,
    ImagePagePreviewResult,
    reject_image_page,
)
from paperworkshell.domain.image_transforms import (
    ImagePageTransformRequest,
    ImagePageTransformResult,
)
from paperworkshell.domain.pdf_thumbnails import (
    PdfThumbnailError,
    PdfThumbnailProgressPhase,
    PdfThumbnailRequest,
    PdfThumbnailResult,
    reject_pdf_thumbnail,
)

DEFAULT_PAGE_PREVIEW_TIMEOUT_SECONDS: Final = DEFAULT_PDF_THUMBNAIL_TIMEOUT_SECONDS
MAX_PAGE_PREVIEW_TIMEOUT_SECONDS: Final = MAX_PDF_THUMBNAIL_TIMEOUT_SECONDS
MAX_PAGE_PREVIEW_BATCH_PAGES: Final = 4_096
PAGE_PREVIEW_PROGRESS_VERSION: Final = 1

PagePreviewRequest = PdfThumbnailRequest | ImagePagePreviewRequest | ImagePageTransformRequest
PagePreviewResult = PdfThumbnailResult | ImagePagePreviewResult | ImagePageTransformResult


class PagePreviewErrorCode(StrEnum):
    """Closed registry of sanitized mixed-preview orchestration failures."""

    MODEL_INVALID = "PAGE_PREVIEW_MODEL_INVALID"
    CANCELLED = "PAGE_PREVIEW_CANCELLED"
    TIMED_OUT = "PAGE_PREVIEW_TIMED_OUT"
    GENERATOR_FAILED = "PAGE_PREVIEW_GENERATOR_FAILED"
    RESULT_INVALID = "PAGE_PREVIEW_RESULT_INVALID"
    INTERNAL_ERROR = "PAGE_PREVIEW_INTERNAL_ERROR"


_SAFE_ERROR_MESSAGES: Final[dict[PagePreviewErrorCode, str]] = {
    PagePreviewErrorCode.MODEL_INVALID: (
        "PaperWorkShell received invalid page-preview operation data."
    ),
    PagePreviewErrorCode.CANCELLED: "Page preview generation was cancelled.",
    PagePreviewErrorCode.TIMED_OUT: ("Page preview generation exceeded its configured time limit."),
    PagePreviewErrorCode.GENERATOR_FAILED: (
        "The document worker could not generate the page preview safely."
    ),
    PagePreviewErrorCode.RESULT_INVALID: (
        "PaperWorkShell received an invalid page-preview result."
    ),
    PagePreviewErrorCode.INTERNAL_ERROR: (
        "PaperWorkShell could not complete page preview generation safely."
    ),
}


@dataclass(slots=True, repr=False)
class PagePreviewError(ValueError):
    """Stable failure containing no paths, source names, pixels, or native details."""

    code: PagePreviewErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not PagePreviewErrorCode:
            self.code = PagePreviewErrorCode.INTERNAL_ERROR
        if (
            type(self.context) is not str
            or not 1 <= len(self.context) <= 64
            or not self.context.startswith("preview.")
            or not self.context.replace(".", "").replace("_", "").isalnum()
            or self.context.lower() != self.context
        ):
            self.context = "preview.operation"
        self.safe_message = _SAFE_ERROR_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"PagePreviewError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_page_preview(code: PagePreviewErrorCode, context: str) -> Never:
    """Raise one closed error without retaining an underlying document exception."""

    error = PagePreviewError(code, context)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


class PagePreviewProgressPhase(StrEnum):
    """Content-free aggregate phases shared by PDF and image pages."""

    LOOKUP = "lookup"
    GENERATING = "generating"
    VALIDATING = "validating"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class PagePreviewProgress:
    """One path-free progress checkpoint for an ordered mixed-page batch."""

    operation_id: UUID
    phase: PagePreviewProgressPhase
    completed_pages: int
    total_pages: int
    progress_version: int = PAGE_PREVIEW_PROGRESS_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.operation_id) is not UUID
            or self.operation_id.version != 4
            or type(self.phase) is not PagePreviewProgressPhase
            or type(self.completed_pages) is not int
            or type(self.total_pages) is not int
            or not 1 <= self.total_pages <= MAX_PAGE_PREVIEW_BATCH_PAGES
            or not 0 <= self.completed_pages <= self.total_pages
            or (
                self.phase is PagePreviewProgressPhase.COMPLETE
                and self.completed_pages != self.total_pages
            )
            or type(self.progress_version) is not int
            or self.progress_version != PAGE_PREVIEW_PROGRESS_VERSION
        ):
            reject_page_preview(PagePreviewErrorCode.MODEL_INVALID, "preview.progress")


class DeadlineProbe(Protocol):
    def __call__(self) -> bool: ...


class CancellationProbe(Protocol):
    def __call__(self) -> bool: ...


class PagePreviewProgressSink(Protocol):
    def __call__(self, progress: PagePreviewProgress, /) -> None: ...


class PdfPreviewPhaseSink(Protocol):
    def __call__(self, phase: PdfThumbnailProgressPhase, /) -> None: ...


class ImagePreviewPhaseSink(Protocol):
    def __call__(self, phase: ImagePagePreviewProgressPhase, /) -> None: ...


class PdfPagePreviewGenerator(Protocol):
    """Existing narrow PDF cache/worker broker port."""

    def get_or_generate(
        self,
        request: PdfThumbnailRequest,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: PdfPreviewPhaseSink,
    ) -> PdfThumbnailResult: ...


class ImagePagePreviewGenerator(Protocol):
    """Narrow image worker/staging broker port."""

    def get_or_generate(
        self,
        request: ImagePagePreviewRequest,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ImagePreviewPhaseSink,
    ) -> ImagePagePreviewResult: ...


class ImagePageTransformGenerator(Protocol):
    """Narrow committed-image transform worker/broker port."""

    def transform(
        self,
        request: ImagePageTransformRequest,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ImagePreviewPhaseSink,
    ) -> ImagePageTransformResult: ...


class MonotonicClock(Protocol):
    def __call__(self) -> float: ...


class _MonotonicDeadline:
    __slots__ = ("_expires_at", "_monotonic")

    def __init__(self, timeout_seconds: float, monotonic: MonotonicClock) -> None:
        self._monotonic = monotonic
        expires_at = _read_monotonic(monotonic) + timeout_seconds
        if not math.isfinite(expires_at):
            reject_page_preview(PagePreviewErrorCode.INTERNAL_ERROR, "preview.deadline")
        self._expires_at = expires_at

    def __call__(self) -> bool:
        return _read_monotonic(self._monotonic) >= self._expires_at


class _SafeCancellationProbe:
    __slots__ = ("_callback", "_deadline")

    def __init__(
        self,
        callback: CancellationProbe | None,
        deadline: DeadlineProbe,
    ) -> None:
        self._callback = callback
        self._deadline = deadline

    def __call__(self) -> bool:
        _require_not_timed_out(self._deadline, "preview.cancellation")
        callback = self._callback
        if callback is None:
            return False
        try:
            value = callback()
        except BaseException:
            reject_page_preview(PagePreviewErrorCode.INTERNAL_ERROR, "preview.cancel_probe")
        if type(value) is not bool:
            reject_page_preview(PagePreviewErrorCode.INTERNAL_ERROR, "preview.cancel_probe")
        _require_not_timed_out(self._deadline, "preview.cancellation")
        return value


class _SafeProgressSink:
    __slots__ = ("_callback", "_deadline")

    def __init__(
        self,
        callback: PagePreviewProgressSink | None,
        deadline: DeadlineProbe,
    ) -> None:
        self._callback = callback
        self._deadline = deadline

    def __call__(self, progress: PagePreviewProgress) -> None:
        if type(progress) is not PagePreviewProgress:
            reject_page_preview(PagePreviewErrorCode.INTERNAL_ERROR, "preview.progress")
        _require_not_timed_out(self._deadline, "preview.progress")
        callback = self._callback
        if callback is None:
            return
        try:
            callback(progress)
        except BaseException:
            reject_page_preview(PagePreviewErrorCode.INTERNAL_ERROR, "preview.progress_sink")
        _require_not_timed_out(self._deadline, "preview.progress")


class _DeferredCallbackFailure:
    """Retain one sanitized app failure without leaking it through a generator port."""

    __slots__ = ("_failure",)

    def __init__(self) -> None:
        self._failure: tuple[PagePreviewErrorCode, str] | None = None

    @property
    def pending(self) -> bool:
        return self._failure is not None

    def capture(self, error: PagePreviewError, *, fallback_context: str) -> None:
        if self._failure is not None:
            return
        if type(error) is PagePreviewError:
            self._failure = (error.code, error.context)
        else:
            self._failure = (PagePreviewErrorCode.INTERNAL_ERROR, fallback_context)

    def capture_internal(self, context: str) -> None:
        if self._failure is None:
            self._failure = (PagePreviewErrorCode.INTERNAL_ERROR, context)

    def raise_if_pending(self) -> None:
        if self._failure is not None:
            reject_page_preview(*self._failure)


class _GeneratorCancellationProbe:
    """Keep aggregate timeout/failure exceptions inside the application boundary."""

    __slots__ = ("_failure", "_probe")

    def __init__(
        self,
        probe: CancellationProbe,
        failure: _DeferredCallbackFailure,
    ) -> None:
        self._probe = probe
        self._failure = failure

    def __call__(self) -> bool:
        if self._failure.pending:
            return True
        try:
            return self._probe()
        except PagePreviewError as error:
            self._failure.capture(error, fallback_context="preview.cancel_probe")
        except BaseException:
            self._failure.capture_internal("preview.cancel_probe")
        return True


class _GeneratorDeadlineProbe:
    """Return an expiry signal while deferring aggregate clock failures to the app."""

    __slots__ = ("_failure", "_probe")

    def __init__(
        self,
        probe: DeadlineProbe,
        failure: _DeferredCallbackFailure,
    ) -> None:
        self._probe = probe
        self._failure = failure

    def __call__(self) -> bool:
        if self._failure.pending:
            return True
        try:
            return self._probe()
        except PagePreviewError as error:
            self._failure.capture(error, fallback_context="preview.deadline_probe")
        except BaseException:
            self._failure.capture_internal("preview.deadline_probe")
        return True


def _defer_generator_progress(
    failure: _DeferredCallbackFailure,
    callback: Callable[[], None],
) -> None:
    """Capture a generator-originated progress failure for the app caller to receive."""

    if failure.pending:
        return
    try:
        callback()
    except PagePreviewError as error:
        failure.capture(error, fallback_context="preview.progress")
    except BaseException:
        failure.capture_internal("preview.progress")


class PagePreviewService:
    """Generate one ordered mixed batch under a shared deadline and cancellation probe."""

    __slots__ = ("_id_factory", "_image", "_monotonic", "_pdf", "_transform")

    def __init__(
        self,
        pdf: PdfPagePreviewGenerator,
        image: ImagePagePreviewGenerator,
        transform: ImagePageTransformGenerator | None = None,
        *,
        monotonic: MonotonicClock = time.monotonic,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if (
            not callable(getattr(pdf, "get_or_generate", None))
            or not callable(getattr(image, "get_or_generate", None))
            or (transform is not None and not callable(getattr(transform, "transform", None)))
            or not callable(monotonic)
            or not callable(id_factory)
        ):
            reject_page_preview(PagePreviewErrorCode.MODEL_INVALID, "preview.service")
        self._pdf = pdf
        self._image = image
        self._transform = transform
        self._monotonic = monotonic
        self._id_factory = id_factory

    def get_previews(
        self,
        requests: tuple[PagePreviewRequest, ...],
        *,
        timeout_seconds: float = DEFAULT_PAGE_PREVIEW_TIMEOUT_SECONDS,
        cancellation: CancellationProbe | None = None,
        progress: PagePreviewProgressSink | None = None,
        initial_image_preview: ImagePagePreviewResult | None = None,
    ) -> tuple[PagePreviewResult, ...]:
        """Return exact ordered snapshots or fail without changing authoritative state."""

        selected = _require_request_batch(requests)
        if initial_image_preview is not None and (
            type(initial_image_preview) is not ImagePagePreviewResult
            or selected != (initial_image_preview.request,)
        ):
            reject_page_preview(PagePreviewErrorCode.RESULT_INVALID, "preview.initial_image")
        timeout = _require_timeout(timeout_seconds)
        operation_id = self._new_operation_id()
        deadline = _MonotonicDeadline(timeout, self._monotonic)
        cancellation_probe = _SafeCancellationProbe(cancellation, deadline)
        progress_sink = _SafeProgressSink(progress, deadline)
        callback_failure = _DeferredCallbackFailure()
        generator_cancellation = _GeneratorCancellationProbe(
            cancellation_probe,
            callback_failure,
        )
        generator_deadline = _GeneratorDeadlineProbe(deadline, callback_failure)
        _require_active(deadline, cancellation_probe, "preview.before_batch")

        total = len(selected)
        results: list[PagePreviewResult] = []
        for index, request in enumerate(selected):
            _require_active(deadline, cancellation_probe, "preview.before_page")
            progress_sink(
                PagePreviewProgress(
                    operation_id,
                    PagePreviewProgressPhase.LOOKUP,
                    index,
                    total,
                )
            )
            if initial_image_preview is not None:
                result: PagePreviewResult = initial_image_preview
            elif type(request) is PdfThumbnailRequest:

                def report_pdf_phase(
                    phase: PdfThumbnailProgressPhase,
                    *,
                    completed: int = index,
                ) -> None:
                    _defer_generator_progress(
                        callback_failure,
                        lambda: progress_sink(
                            PagePreviewProgress(
                                operation_id,
                                _pdf_phase(phase),
                                completed,
                                total,
                            )
                        ),
                    )

                try:
                    pdf_result = self._pdf.get_or_generate(
                        request,
                        deadline=generator_deadline,
                        cancellation=generator_cancellation,
                        progress=report_pdf_phase,
                    )
                except PdfThumbnailError as error:
                    callback_failure.raise_if_pending()
                    _rebuild_pdf_generator_error(error)
                except PagePreviewError as error:
                    callback_failure.raise_if_pending()
                    _rebuild_page_preview_error(error, "preview.pdf_generator")
                except Exception:
                    callback_failure.raise_if_pending()
                    reject_page_preview(
                        PagePreviewErrorCode.GENERATOR_FAILED,
                        "preview.pdf_generator",
                    )
                callback_failure.raise_if_pending()
                if type(pdf_result) is not PdfThumbnailResult or pdf_result.request != request:
                    reject_page_preview(PagePreviewErrorCode.RESULT_INVALID, "preview.pdf_result")
                result = pdf_result
            elif type(request) is ImagePagePreviewRequest:

                def report_image_phase(
                    phase: ImagePagePreviewProgressPhase,
                    *,
                    completed: int = index,
                ) -> None:
                    _defer_generator_progress(
                        callback_failure,
                        lambda: progress_sink(
                            PagePreviewProgress(
                                operation_id,
                                _image_phase(phase),
                                completed,
                                total,
                            )
                        ),
                    )

                try:
                    image_result = self._image.get_or_generate(
                        request,
                        deadline=generator_deadline,
                        cancellation=generator_cancellation,
                        progress=report_image_phase,
                    )
                except ImagePageError as error:
                    callback_failure.raise_if_pending()
                    _rebuild_image_generator_error(error)
                except PagePreviewError as error:
                    callback_failure.raise_if_pending()
                    _rebuild_page_preview_error(error, "preview.image_generator")
                except Exception:
                    callback_failure.raise_if_pending()
                    reject_page_preview(
                        PagePreviewErrorCode.GENERATOR_FAILED,
                        "preview.image_generator",
                    )
                callback_failure.raise_if_pending()
                if (
                    type(image_result) is not ImagePagePreviewResult
                    or image_result.request != request
                ):
                    reject_page_preview(
                        PagePreviewErrorCode.RESULT_INVALID,
                        "preview.image_result",
                    )
                result = image_result
            elif type(request) is ImagePageTransformRequest:
                transform = self._transform
                if transform is None:
                    reject_page_preview(
                        PagePreviewErrorCode.GENERATOR_FAILED,
                        "preview.transform_generator",
                    )

                def report_transform_phase(
                    phase: ImagePagePreviewProgressPhase,
                    *,
                    completed: int = index,
                ) -> None:
                    _defer_generator_progress(
                        callback_failure,
                        lambda: progress_sink(
                            PagePreviewProgress(
                                operation_id,
                                _image_phase(phase),
                                completed,
                                total,
                            )
                        ),
                    )

                try:
                    transform_result = transform.transform(
                        request,
                        deadline=generator_deadline,
                        cancellation=generator_cancellation,
                        progress=report_transform_phase,
                    )
                except ImagePageError as error:
                    callback_failure.raise_if_pending()
                    _rebuild_image_generator_error(error)
                except PagePreviewError as error:
                    callback_failure.raise_if_pending()
                    _rebuild_page_preview_error(error, "preview.transform_generator")
                except Exception:
                    callback_failure.raise_if_pending()
                    reject_page_preview(
                        PagePreviewErrorCode.GENERATOR_FAILED,
                        "preview.transform_generator",
                    )
                callback_failure.raise_if_pending()
                if (
                    type(transform_result) is not ImagePageTransformResult
                    or transform_result.request != request
                ):
                    reject_page_preview(
                        PagePreviewErrorCode.RESULT_INVALID,
                        "preview.transform_result",
                    )
                result = transform_result
            else:
                reject_page_preview(PagePreviewErrorCode.INTERNAL_ERROR, "preview.request_type")
            _require_active(deadline, cancellation_probe, "preview.after_page")
            results.append(result)

        progress_sink(
            PagePreviewProgress(
                operation_id,
                PagePreviewProgressPhase.COMPLETE,
                total,
                total,
            )
        )
        return tuple(results)

    def _new_operation_id(self) -> UUID:
        try:
            value = self._id_factory()
        except BaseException:
            reject_page_preview(PagePreviewErrorCode.INTERNAL_ERROR, "preview.operation_id")
        if type(value) is not UUID or value.version != 4:
            reject_page_preview(PagePreviewErrorCode.INTERNAL_ERROR, "preview.operation_id")
        return value


def _require_request_batch(
    requests: tuple[PagePreviewRequest, ...],
) -> tuple[PagePreviewRequest, ...]:
    if (
        type(requests) is not tuple
        or not 1 <= len(requests) <= MAX_PAGE_PREVIEW_BATCH_PAGES
        or any(
            type(request)
            not in {PdfThumbnailRequest, ImagePagePreviewRequest, ImagePageTransformRequest}
            for request in requests
        )
    ):
        reject_page_preview(PagePreviewErrorCode.MODEL_INVALID, "preview.request_batch")
    case_ids = {request.case_id for request in requests}
    page_ids = {request.page_id for request in requests}
    if len(case_ids) != 1 or len(page_ids) != len(requests):
        reject_page_preview(PagePreviewErrorCode.MODEL_INVALID, "preview.request_batch_ids")
    return requests


def _rebuild_pdf_generator_error(error: PdfThumbnailError) -> Never:
    """Rebuild one PDF error so generator-owned state cannot cross the boundary."""

    if type(error) is not PdfThumbnailError:
        reject_page_preview(
            PagePreviewErrorCode.GENERATOR_FAILED,
            "preview.pdf_generator",
        )
    reject_pdf_thumbnail(error.code, error.context)


def _rebuild_image_generator_error(error: ImagePageError) -> Never:
    """Rebuild one image error so generator-owned state cannot cross the boundary."""

    if type(error) is not ImagePageError:
        reject_page_preview(
            PagePreviewErrorCode.GENERATOR_FAILED,
            "preview.image_generator",
        )
    reject_image_page(error.code, error.context)


def _rebuild_page_preview_error(error: PagePreviewError, context: str) -> Never:
    """Rebuild one aggregate error without retaining a generator traceback or chain."""

    if type(error) is not PagePreviewError:
        reject_page_preview(PagePreviewErrorCode.GENERATOR_FAILED, context)
    reject_page_preview(error.code, error.context)


def _pdf_phase(phase: object) -> PagePreviewProgressPhase:
    if phase is PdfThumbnailProgressPhase.LOOKUP:
        return PagePreviewProgressPhase.LOOKUP
    if phase is PdfThumbnailProgressPhase.RENDERING:
        return PagePreviewProgressPhase.GENERATING
    if phase is PdfThumbnailProgressPhase.VALIDATING:
        return PagePreviewProgressPhase.VALIDATING
    reject_page_preview(PagePreviewErrorCode.RESULT_INVALID, "preview.pdf_progress")


def _image_phase(phase: object) -> PagePreviewProgressPhase:
    if phase is ImagePagePreviewProgressPhase.LOOKUP:
        return PagePreviewProgressPhase.LOOKUP
    if phase is ImagePagePreviewProgressPhase.GENERATING:
        return PagePreviewProgressPhase.GENERATING
    if phase is ImagePagePreviewProgressPhase.VALIDATING:
        return PagePreviewProgressPhase.VALIDATING
    reject_page_preview(PagePreviewErrorCode.RESULT_INVALID, "preview.image_progress")


def _require_timeout(value: object) -> float:
    if type(value) not in {int, float}:
        reject_page_preview(PagePreviewErrorCode.MODEL_INVALID, "preview.timeout")
    try:
        timeout = float(cast(int | float, value))
    except OverflowError, ValueError:
        reject_page_preview(PagePreviewErrorCode.MODEL_INVALID, "preview.timeout")
    if not math.isfinite(timeout) or not 0 < timeout <= MAX_PAGE_PREVIEW_TIMEOUT_SECONDS:
        reject_page_preview(PagePreviewErrorCode.MODEL_INVALID, "preview.timeout")
    return timeout


def _read_monotonic(monotonic: MonotonicClock) -> float:
    try:
        value = monotonic()
    except BaseException:
        reject_page_preview(PagePreviewErrorCode.INTERNAL_ERROR, "preview.deadline_clock")
    if type(value) not in {int, float}:
        reject_page_preview(PagePreviewErrorCode.INTERNAL_ERROR, "preview.deadline_clock")
    try:
        observed = float(value)
    except OverflowError, ValueError:
        reject_page_preview(PagePreviewErrorCode.INTERNAL_ERROR, "preview.deadline_clock")
    if not math.isfinite(observed):
        reject_page_preview(PagePreviewErrorCode.INTERNAL_ERROR, "preview.deadline_clock")
    return observed


def _require_not_timed_out(deadline: DeadlineProbe, context: str) -> None:
    try:
        expired = deadline()
    except PagePreviewError:
        raise
    except BaseException:
        reject_page_preview(PagePreviewErrorCode.INTERNAL_ERROR, "preview.deadline_probe")
    if type(expired) is not bool:
        reject_page_preview(PagePreviewErrorCode.INTERNAL_ERROR, "preview.deadline_probe")
    if expired:
        reject_page_preview(PagePreviewErrorCode.TIMED_OUT, context)


def _require_active(
    deadline: DeadlineProbe,
    cancellation: CancellationProbe,
    context: str,
) -> None:
    _require_not_timed_out(deadline, context)
    try:
        cancelled = cancellation()
    except PagePreviewError:
        raise
    except BaseException:
        reject_page_preview(PagePreviewErrorCode.INTERNAL_ERROR, "preview.cancel_probe")
    if type(cancelled) is not bool:
        reject_page_preview(PagePreviewErrorCode.INTERNAL_ERROR, "preview.cancel_probe")
    if cancelled:
        reject_page_preview(PagePreviewErrorCode.CANCELLED, context)

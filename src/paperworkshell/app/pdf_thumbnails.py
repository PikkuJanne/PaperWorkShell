"""Toolkit-free orchestration for bounded, page-at-a-time PDF thumbnails."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Final, Protocol, cast
from uuid import UUID, uuid4

from paperworkshell.domain.pdf_rendering import MAX_PDF_RENDER_PAGES
from paperworkshell.domain.pdf_thumbnails import (
    PdfThumbnailError,
    PdfThumbnailErrorCode,
    PdfThumbnailProgress,
    PdfThumbnailProgressPhase,
    PdfThumbnailRequest,
    PdfThumbnailResult,
    reject_pdf_thumbnail,
)

DEFAULT_PDF_THUMBNAIL_TIMEOUT_SECONDS: Final = 120.0
MAX_PDF_THUMBNAIL_TIMEOUT_SECONDS: Final = 3_600.0


class DeadlineProbe(Protocol):
    """Return whether the immutable application deadline has expired."""

    def __call__(self) -> bool: ...


class CancellationProbe(Protocol):
    """Return whether the caller has requested cooperative cancellation."""

    def __call__(self) -> bool: ...


class ThumbnailPhaseSink(Protocol):
    """Receive a content-free phase for the page currently being generated."""

    def __call__(self, phase: PdfThumbnailProgressPhase, /) -> None: ...


class ThumbnailProgressSink(Protocol):
    """Receive one validated aggregate progress record."""

    def __call__(self, progress: PdfThumbnailProgress, /) -> None: ...


class MonotonicClock(Protocol):
    """Trusted monotonic clock seam used to enforce one batch deadline."""

    def __call__(self) -> float: ...


class ThumbnailPageGenerator(Protocol):
    """Narrow broker port for one cache lookup or worker render."""

    def get_or_generate(
        self,
        request: PdfThumbnailRequest,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ThumbnailPhaseSink,
    ) -> PdfThumbnailResult: ...


class _MonotonicDeadline:
    __slots__ = ("_expires_at", "_monotonic")

    def __init__(self, timeout_seconds: float, monotonic: MonotonicClock) -> None:
        self._monotonic = monotonic
        expires_at = _read_monotonic(monotonic) + timeout_seconds
        if not math.isfinite(expires_at):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.deadline")
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
        _require_not_timed_out(self._deadline, "thumbnail.cancellation")
        callback = self._callback
        if callback is None:
            return False
        try:
            value = callback()
        except BaseException:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.cancel_probe")
        if type(value) is not bool:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.cancel_probe")
        _require_not_timed_out(self._deadline, "thumbnail.cancellation")
        return value


class _SafeProgressSink:
    __slots__ = ("_callback", "_deadline")

    def __init__(
        self,
        callback: ThumbnailProgressSink | None,
        deadline: DeadlineProbe,
    ) -> None:
        self._callback = callback
        self._deadline = deadline

    def __call__(self, progress: PdfThumbnailProgress) -> None:
        if type(progress) is not PdfThumbnailProgress:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.progress")
        _require_not_timed_out(self._deadline, "thumbnail.progress")
        callback = self._callback
        if callback is None:
            return
        try:
            callback(progress)
        except BaseException:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.progress_sink")
        _require_not_timed_out(self._deadline, "thumbnail.progress")


class PdfThumbnailService:
    """Generate a deterministic request batch one bounded page at a time."""

    __slots__ = ("_generator", "_id_factory", "_monotonic")

    def __init__(
        self,
        generator: ThumbnailPageGenerator,
        *,
        monotonic: MonotonicClock = time.monotonic,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if not callable(monotonic) or not callable(id_factory):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "thumbnail.service")
        self._generator = generator
        self._monotonic = monotonic
        self._id_factory = id_factory

    def get_thumbnails(
        self,
        requests: tuple[PdfThumbnailRequest, ...],
        *,
        timeout_seconds: float = DEFAULT_PDF_THUMBNAIL_TIMEOUT_SECONDS,
        cancellation: CancellationProbe | None = None,
        progress: ThumbnailProgressSink | None = None,
    ) -> tuple[PdfThumbnailResult, ...]:
        """Return validated page snapshots or fail without changing authoritative state."""

        selected = _require_request_batch(requests)
        timeout = _require_timeout(timeout_seconds)
        operation_id = self._new_operation_id()
        deadline = _MonotonicDeadline(timeout, self._monotonic)
        cancellation_probe = _SafeCancellationProbe(cancellation, deadline)
        progress_sink = _SafeProgressSink(progress, deadline)
        _require_active(deadline, cancellation_probe, "thumbnail.before_batch")

        total = len(selected)
        results: list[PdfThumbnailResult] = []
        for index, request in enumerate(selected):
            _require_active(deadline, cancellation_probe, "thumbnail.before_page")

            def report_phase(
                phase: PdfThumbnailProgressPhase,
                *,
                completed: int = index,
            ) -> None:
                if (
                    type(phase) is not PdfThumbnailProgressPhase
                    or phase is PdfThumbnailProgressPhase.COMPLETE
                ):
                    reject_pdf_thumbnail(
                        PdfThumbnailErrorCode.RESULT_INVALID,
                        "thumbnail.page_progress",
                    )
                progress_sink(PdfThumbnailProgress(operation_id, phase, completed, total))

            report_phase(PdfThumbnailProgressPhase.LOOKUP)
            try:
                result = self._generator.get_or_generate(
                    request,
                    deadline=deadline,
                    cancellation=cancellation_probe,
                    progress=report_phase,
                )
            except PdfThumbnailError:
                raise
            except Exception:
                reject_pdf_thumbnail(
                    PdfThumbnailErrorCode.WORKER_FAILED,
                    "thumbnail.page_generator",
                )
            if type(result) is not PdfThumbnailResult or result.request != request:
                reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.page_result")
            _require_active(deadline, cancellation_probe, "thumbnail.after_page")
            results.append(result)

        progress_sink(
            PdfThumbnailProgress(
                operation_id,
                PdfThumbnailProgressPhase.COMPLETE,
                total,
                total,
            )
        )
        return tuple(results)

    def _new_operation_id(self) -> UUID:
        try:
            value = self._id_factory()
        except BaseException:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.operation_id")
        if type(value) is not UUID or value.version != 4:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.operation_id")
        return value


def _require_request_batch(
    requests: tuple[PdfThumbnailRequest, ...],
) -> tuple[PdfThumbnailRequest, ...]:
    if (
        type(requests) is not tuple
        or not 1 <= len(requests) <= MAX_PDF_RENDER_PAGES
        or any(type(request) is not PdfThumbnailRequest for request in requests)
    ):
        reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "thumbnail.request_batch")
    case_ids = {request.case_id for request in requests}
    page_ids = {request.page_id for request in requests}
    if len(case_ids) != 1 or len(page_ids) != len(requests):
        reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "thumbnail.request_batch_ids")
    return requests


def _require_timeout(value: object) -> float:
    if type(value) not in {int, float}:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "thumbnail.timeout")
    try:
        timeout = float(cast(int | float, value))
    except OverflowError, ValueError:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "thumbnail.timeout")
    if not math.isfinite(timeout) or not 0 < timeout <= MAX_PDF_THUMBNAIL_TIMEOUT_SECONDS:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "thumbnail.timeout")
    return timeout


def _read_monotonic(monotonic: MonotonicClock) -> float:
    try:
        value = monotonic()
    except BaseException:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.deadline_clock")
    if type(value) not in {int, float}:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.deadline_clock")
    try:
        observed = float(value)
    except OverflowError, ValueError:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.deadline_clock")
    if not math.isfinite(observed):
        reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.deadline_clock")
    return observed


def _require_not_timed_out(deadline: DeadlineProbe, context: str) -> None:
    try:
        expired = deadline()
    except PdfThumbnailError:
        raise
    except BaseException:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.deadline_probe")
    if type(expired) is not bool:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.deadline_probe")
    if expired:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.TIMED_OUT, context)


def _require_active(
    deadline: DeadlineProbe,
    cancellation: CancellationProbe,
    context: str,
) -> None:
    _require_not_timed_out(deadline, context)
    try:
        cancelled = cancellation()
    except PdfThumbnailError:
        raise
    except BaseException:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.cancel_probe")
    if type(cancelled) is not bool:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.cancel_probe")
    if cancelled:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.CANCELLED, context)

"""Content-free scheduling for bounded one-page-at-a-time document work."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from types import TracebackType
from typing import Final, Literal, Never, Self

INCREMENTAL_PROCESSING_POLICY_VERSION: Final = 1

_CONTEXT = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")


class IncrementalProcessingErrorCode(StrEnum):
    """Closed failures for the page-lifetime scheduler."""

    MODEL_INVALID = "INCREMENTAL_PROCESSING_MODEL_INVALID"
    PAGE_ORDER_INVALID = "INCREMENTAL_PROCESSING_PAGE_ORDER_INVALID"
    PAGE_STATE_INVALID = "INCREMENTAL_PROCESSING_PAGE_STATE_INVALID"
    RESOURCE_LIMIT = "INCREMENTAL_PROCESSING_RESOURCE_LIMIT"
    OBSERVER_FAILED = "INCREMENTAL_PROCESSING_OBSERVER_FAILED"


_SAFE_MESSAGES: Final[dict[IncrementalProcessingErrorCode, str]] = {
    IncrementalProcessingErrorCode.MODEL_INVALID: (
        "PaperWorkShell received invalid incremental-processing data."
    ),
    IncrementalProcessingErrorCode.PAGE_ORDER_INVALID: (
        "PaperWorkShell could not continue the page operation in the required order."
    ),
    IncrementalProcessingErrorCode.PAGE_STATE_INVALID: (
        "PaperWorkShell could not confirm that the prior page work was released."
    ),
    IncrementalProcessingErrorCode.RESOURCE_LIMIT: (
        "The current page operation exceeds a configured resource limit."
    ),
    IncrementalProcessingErrorCode.OBSERVER_FAILED: (
        "PaperWorkShell could not verify incremental resource use."
    ),
}


@dataclass(slots=True, repr=False)
class IncrementalProcessingError(ValueError):
    """Stable failure containing no path, source name, digest, or document text."""

    code: IncrementalProcessingErrorCode
    context: str
    page_index: int | None = None
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not IncrementalProcessingErrorCode:
            self.code = IncrementalProcessingErrorCode.MODEL_INVALID
        if type(self.context) is not str or _CONTEXT.fullmatch(self.context) is None:
            self.context = "incremental.processing"
        if self.page_index is not None and (
            type(self.page_index) is not int or self.page_index < 0
        ):
            self.page_index = None
        self.safe_message = _SAFE_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.page_index, self.safe_message)
        self.__cause__ = None
        self.__context__ = None

    def __repr__(self) -> str:
        return (
            "IncrementalProcessingError("
            f"code={self.code.value!r}, context={self.context!r}, "
            f"page_index={self.page_index!r})"
        )

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_incremental_processing(
    code: IncrementalProcessingErrorCode,
    context: str,
    *,
    page_index: int | None = None,
) -> Never:
    """Raise one sanitized scheduler failure."""

    error = IncrementalProcessingError(code, context, page_index)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


@dataclass(frozen=True, slots=True)
class IncrementalProcessingLimits:
    """Versioned central-policy limits applied to one ordered page stream."""

    maximum_pages: int
    maximum_full_resolution_page_buffers: int
    maximum_page_results: int
    maximum_temporary_storage_bytes: int
    policy_version: int = INCREMENTAL_PROCESSING_POLICY_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.maximum_pages) is not int
            or self.maximum_pages < 1
            or type(self.maximum_full_resolution_page_buffers) is not int
            or self.maximum_full_resolution_page_buffers != 1
            or type(self.maximum_page_results) is not int
            or self.maximum_page_results != 1
            or type(self.maximum_temporary_storage_bytes) is not int
            or self.maximum_temporary_storage_bytes < 1
            or type(self.policy_version) is not int
            or self.policy_version != INCREMENTAL_PROCESSING_POLICY_VERSION
        ):
            reject_incremental_processing(
                IncrementalProcessingErrorCode.MODEL_INVALID,
                "incremental.limits",
            )


@dataclass(frozen=True, slots=True)
class IncrementalProcessingSnapshot:
    """Content-free current and high-water scheduler facts."""

    total_pages: int
    completed_pages: int
    active_page_index: int | None
    active_full_resolution_page_buffers: int
    peak_full_resolution_page_buffers: int
    active_page_results: int
    peak_page_results: int
    active_temporary_storage_bytes: int
    peak_temporary_storage_bytes: int
    policy_version: int = INCREMENTAL_PROCESSING_POLICY_VERSION

    def __post_init__(self) -> None:
        integers = (
            self.total_pages,
            self.completed_pages,
            self.active_full_resolution_page_buffers,
            self.peak_full_resolution_page_buffers,
            self.active_page_results,
            self.peak_page_results,
            self.active_temporary_storage_bytes,
            self.peak_temporary_storage_bytes,
        )
        if (
            any(type(value) is not int or value < 0 for value in integers)
            or self.total_pages < 1
            or self.completed_pages > self.total_pages
            or (
                self.active_page_index is not None
                and (
                    type(self.active_page_index) is not int
                    or not self.completed_pages <= self.active_page_index < self.total_pages
                )
            )
            or self.active_full_resolution_page_buffers > self.peak_full_resolution_page_buffers
            or self.active_page_results > self.peak_page_results
            or self.active_temporary_storage_bytes > self.peak_temporary_storage_bytes
            or type(self.policy_version) is not int
            or self.policy_version != INCREMENTAL_PROCESSING_POLICY_VERSION
        ):
            reject_incremental_processing(
                IncrementalProcessingErrorCode.MODEL_INVALID,
                "incremental.snapshot",
            )

    @property
    def active_pages(self) -> int:
        """Return the exact active page count (zero or one)."""

        return int(self.active_page_index is not None)


IncrementalProcessingObserver = Callable[[IncrementalProcessingSnapshot], None]


def _ignore_observation(_snapshot: IncrementalProcessingSnapshot) -> None:
    return


class IncrementalPageScheduler:
    """Admit pages in order and require deterministic release before advancing."""

    __slots__ = (
        "_active_buffers",
        "_active_page_index",
        "_active_results",
        "_active_temporary_bytes",
        "_completed_pages",
        "_limits",
        "_observer",
        "_peak_buffers",
        "_peak_results",
        "_peak_temporary_bytes",
        "_total_pages",
    )

    def __init__(
        self,
        total_pages: int,
        limits: IncrementalProcessingLimits,
        *,
        observer: IncrementalProcessingObserver = _ignore_observation,
    ) -> None:
        if (
            type(total_pages) is not int
            or not isinstance(limits, IncrementalProcessingLimits)
            or not 1 <= total_pages <= limits.maximum_pages
            or not callable(observer)
        ):
            reject_incremental_processing(
                IncrementalProcessingErrorCode.MODEL_INVALID,
                "incremental.scheduler",
            )
        self._total_pages = total_pages
        self._limits = limits
        self._observer = observer
        self._completed_pages = 0
        self._active_page_index: int | None = None
        self._active_buffers = 0
        self._peak_buffers = 0
        self._active_results = 0
        self._peak_results = 0
        self._active_temporary_bytes = 0
        self._peak_temporary_bytes = 0
        self._publish()

    @property
    def limits(self) -> IncrementalProcessingLimits:
        return self._limits

    def begin_page(self, page_index: int) -> IncrementalPageLease:
        """Acquire the next exact page; a second active page is never queued."""

        if type(page_index) is not int or page_index < 0:
            reject_incremental_processing(
                IncrementalProcessingErrorCode.MODEL_INVALID,
                "incremental.page_index",
            )
        if page_index != self._completed_pages:
            reject_incremental_processing(
                IncrementalProcessingErrorCode.PAGE_ORDER_INVALID,
                "incremental.page_order",
                page_index=page_index,
            )
        if self._active_page_index is not None:
            reject_incremental_processing(
                IncrementalProcessingErrorCode.PAGE_STATE_INVALID,
                "incremental.page_active",
                page_index=page_index,
            )
        self._active_page_index = page_index
        try:
            self._publish()
        except BaseException:
            # No lease escaped, so restore the idle state before propagating the
            # sanitized observer failure. A later retry starts the same page.
            self._active_page_index = None
            raise
        return IncrementalPageLease(self, page_index)

    def snapshot(self) -> IncrementalProcessingSnapshot:
        """Return current numeric facts without querying files or document content."""

        return IncrementalProcessingSnapshot(
            total_pages=self._total_pages,
            completed_pages=self._completed_pages,
            active_page_index=self._active_page_index,
            active_full_resolution_page_buffers=self._active_buffers,
            peak_full_resolution_page_buffers=self._peak_buffers,
            active_page_results=self._active_results,
            peak_page_results=self._peak_results,
            active_temporary_storage_bytes=self._active_temporary_bytes,
            peak_temporary_storage_bytes=self._peak_temporary_bytes,
        )

    def require_complete(self) -> IncrementalProcessingSnapshot:
        """Require every page complete and all page-local resources released."""

        snapshot = self.snapshot()
        if (
            snapshot.completed_pages != snapshot.total_pages
            or snapshot.active_page_index is not None
            or snapshot.active_full_resolution_page_buffers != 0
            or snapshot.active_page_results != 0
            or snapshot.active_temporary_storage_bytes != 0
        ):
            reject_incremental_processing(
                IncrementalProcessingErrorCode.PAGE_STATE_INVALID,
                "incremental.incomplete",
                page_index=snapshot.active_page_index,
            )
        return snapshot

    def _observe(
        self,
        page_index: int,
        *,
        full_resolution_page_buffers: int,
        page_results: int,
        temporary_storage_bytes: int,
    ) -> None:
        if self._active_page_index != page_index:
            reject_incremental_processing(
                IncrementalProcessingErrorCode.PAGE_STATE_INVALID,
                "incremental.observe_page",
                page_index=page_index,
            )
        if (
            type(full_resolution_page_buffers) is not int
            or full_resolution_page_buffers < 0
            or type(page_results) is not int
            or page_results < 0
            or type(temporary_storage_bytes) is not int
            or temporary_storage_bytes < 0
        ):
            reject_incremental_processing(
                IncrementalProcessingErrorCode.MODEL_INVALID,
                "incremental.observation",
                page_index=page_index,
            )
        if (
            full_resolution_page_buffers > self._limits.maximum_full_resolution_page_buffers
            or page_results > self._limits.maximum_page_results
            or temporary_storage_bytes > self._limits.maximum_temporary_storage_bytes
        ):
            reject_incremental_processing(
                IncrementalProcessingErrorCode.RESOURCE_LIMIT,
                "incremental.page_resources",
                page_index=page_index,
            )
        previous = (
            self._active_buffers,
            self._active_results,
            self._active_temporary_bytes,
            self._peak_buffers,
            self._peak_results,
            self._peak_temporary_bytes,
        )
        self._active_buffers = full_resolution_page_buffers
        self._active_results = page_results
        self._active_temporary_bytes = temporary_storage_bytes
        self._peak_buffers = max(self._peak_buffers, full_resolution_page_buffers)
        self._peak_results = max(self._peak_results, page_results)
        self._peak_temporary_bytes = max(
            self._peak_temporary_bytes,
            temporary_storage_bytes,
        )
        try:
            self._publish()
        except BaseException:
            (
                self._active_buffers,
                self._active_results,
                self._active_temporary_bytes,
                self._peak_buffers,
                self._peak_results,
                self._peak_temporary_bytes,
            ) = previous
            raise

    def _finish(self, page_index: int, *, completed: bool) -> None:
        if self._active_page_index != page_index:
            reject_incremental_processing(
                IncrementalProcessingErrorCode.PAGE_STATE_INVALID,
                "incremental.finish_page",
                page_index=page_index,
            )
        if completed and (
            self._active_buffers != 0
            or self._active_results != 0
            or self._active_temporary_bytes != 0
        ):
            reject_incremental_processing(
                IncrementalProcessingErrorCode.PAGE_STATE_INVALID,
                "incremental.page_release",
                page_index=page_index,
            )
        previous = (
            self._completed_pages,
            self._active_page_index,
            self._active_buffers,
            self._active_results,
            self._active_temporary_bytes,
        )
        self._active_page_index = None
        self._active_buffers = 0
        self._active_results = 0
        self._active_temporary_bytes = 0
        if completed:
            self._completed_pages += 1
        try:
            self._publish()
        except BaseException:
            (
                self._completed_pages,
                self._active_page_index,
                self._active_buffers,
                self._active_results,
                self._active_temporary_bytes,
            ) = previous
            raise

    def _publish(self) -> None:
        try:
            self._observer(self.snapshot())
        except IncrementalProcessingError:
            raise
        except BaseException:
            reject_incremental_processing(
                IncrementalProcessingErrorCode.OBSERVER_FAILED,
                "incremental.observer",
                page_index=self._active_page_index,
            )

    def __repr__(self) -> str:
        snapshot = self.snapshot()
        return (
            "IncrementalPageScheduler("
            f"completed_pages={snapshot.completed_pages}, total_pages={snapshot.total_pages}, "
            f"active_pages={snapshot.active_pages})"
        )


class IncrementalPageLease:
    """Single-page authority returned only by ``IncrementalPageScheduler``."""

    __slots__ = ("_closed", "_page_index", "_scheduler")

    def __init__(self, scheduler: IncrementalPageScheduler, page_index: int) -> None:
        self._scheduler = scheduler
        self._page_index = page_index
        self._closed = False

    @property
    def page_index(self) -> int:
        return self._page_index

    def observe(
        self,
        *,
        full_resolution_page_buffers: int,
        page_results: int,
        temporary_storage_bytes: int,
    ) -> None:
        if self._closed:
            reject_incremental_processing(
                IncrementalProcessingErrorCode.PAGE_STATE_INVALID,
                "incremental.lease_closed",
                page_index=self._page_index,
            )
        self._scheduler._observe(
            self._page_index,
            full_resolution_page_buffers=full_resolution_page_buffers,
            page_results=page_results,
            temporary_storage_bytes=temporary_storage_bytes,
        )

    def complete(self) -> None:
        if self._closed:
            reject_incremental_processing(
                IncrementalProcessingErrorCode.PAGE_STATE_INVALID,
                "incremental.lease_closed",
                page_index=self._page_index,
            )
        self._scheduler._finish(self._page_index, completed=True)
        self._closed = True

    def abort(self) -> None:
        if self._closed:
            return
        self._scheduler._finish(self._page_index, completed=False)
        self._closed = True

    def __enter__(self) -> Self:
        if self._closed:
            reject_incremental_processing(
                IncrementalProcessingErrorCode.PAGE_STATE_INVALID,
                "incremental.lease_closed",
                page_index=self._page_index,
            )
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> Literal[False]:
        if exception_type is None:
            self.complete()
        else:
            self.abort()
        return False

    def __repr__(self) -> str:
        return f"IncrementalPageLease(page_index={self._page_index}, closed={self._closed})"

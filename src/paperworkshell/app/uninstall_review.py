"""One-shot uninstall consent over a bounded, authenticated case-storage port."""

from __future__ import annotations

from collections.abc import Callable
from threading import RLock
from typing import Protocol
from uuid import UUID, uuid4

from paperworkshell.domain.uninstall_review import (
    UninstallCaseInventory,
    UninstallCaseReference,
    UninstallDeletionConfirmation,
    UninstallRemovalResult,
    UninstallReview,
    UninstallReviewError,
    UninstallReviewErrorCode,
)


class UninstallCaseStore(Protocol):
    def inspect(self, *, cancellation: Callable[[], bool]) -> UninstallCaseInventory: ...

    def delete(self, case: UninstallCaseReference) -> bool:
        """Return true only after complete authenticated case removal."""
        ...


class UninstallReviewService:
    """Preservation requires no storage access; deletion needs fresh one-shot consent.

    The service retains only bounded case IDs and manifest hashes. Each storage
    operation handles one case; cancellation is checked between operations. There
    is no persistent destructive intent and no automatic retry after restart.
    """

    def __init__(self, store: UninstallCaseStore) -> None:
        self._store = store
        self._review: UninstallReview | None = None
        self._lock = RLock()

    def prepare(self, *, cancellation: Callable[[], bool] = lambda: False) -> UninstallReview:
        with self._lock:
            self._review = None
            try:
                inventory = self._store.inspect(cancellation=cancellation)
                if type(inventory) is not UninstallCaseInventory:
                    raise UninstallReviewError(UninstallReviewErrorCode.INVALID)
            except UninstallReviewError as error:
                if error.code is UninstallReviewErrorCode.CANCELLED:
                    raise
                raise UninstallReviewError(UninstallReviewErrorCode.INSPECTION_FAILED) from None
            except Exception:
                raise UninstallReviewError(UninstallReviewErrorCode.INSPECTION_FAILED) from None
            self._review = UninstallReview(uuid4(), inventory)
            return self._review

    def discard(self) -> None:
        with self._lock:
            self._review = None

    def confirm(
        self,
        review_id: UUID,
        confirmation: UninstallDeletionConfirmation,
        *,
        cancellation: Callable[[], bool],
        progress: Callable[[int, int], None] | None = None,
    ) -> UninstallRemovalResult:
        with self._lock:
            review = self._review
            self._review = None
            if confirmation is not UninstallDeletionConfirmation.DELETE_REVIEWED_MANAGED_CASES:
                raise UninstallReviewError(UninstallReviewErrorCode.CONFIRMATION_REQUIRED)
            if review is None or type(review_id) is not UUID or review.review_id != review_id:
                raise UninstallReviewError(UninstallReviewErrorCode.REVIEW_STALE)
            if not callable(cancellation) or (progress is not None and not callable(progress)):
                raise UninstallReviewError(UninstallReviewErrorCode.INVALID)
            total = len(review.inventory.cases)
            deleted = failed = 0
            cancelled = False
            for completed, case in enumerate(review.inventory.cases):
                try:
                    cancelled = bool(cancellation())
                except Exception:
                    cancelled = True
                if cancelled:
                    break
                try:
                    complete = self._store.delete(case)
                except Exception:
                    complete = False
                if complete is True:
                    deleted += 1
                else:
                    failed += 1
                if progress is not None:
                    try:
                        progress(completed + 1, total)
                    except Exception:
                        # A presentation failure cannot authorize another delete.
                        cancelled = True
                        break
            if not cancelled:
                try:
                    cancelled = bool(cancellation())
                except Exception:
                    cancelled = True
            return UninstallRemovalResult(
                deleted,
                review.inventory.unavailable_count + total - deleted,
                failed,
                cancelled,
            )

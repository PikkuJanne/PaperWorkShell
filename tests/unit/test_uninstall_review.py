"""One-shot, bounded and content-free uninstall deletion orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from paperworkshell.app.uninstall_review import UninstallReviewService
from paperworkshell.domain.uninstall_review import (
    MAX_UNINSTALL_CASES,
    UninstallCaseInventory,
    UninstallCaseReference,
    UninstallDeletionConfirmation,
    UninstallRemovalResult,
    UninstallReviewError,
    UninstallReviewErrorCode,
)

CONFIRM = UninstallDeletionConfirmation.DELETE_REVIEWED_MANAGED_CASES


class Store:
    def __init__(self, count: int = 3, unavailable: int = 0) -> None:
        self.inventory = UninstallCaseInventory(
            tuple(UninstallCaseReference(uuid4(), "a" * 64, "b" * 64) for _ in range(count)),
            unavailable,
        )
        self.calls: list[UUID] = []
        self.inspections = 0
        self.failure: Exception | None = None
        self.complete = True

    def inspect(self, *, cancellation: Callable[[], bool]) -> UninstallCaseInventory:
        self.inspections += 1
        if cancellation():
            raise UninstallReviewError(UninstallReviewErrorCode.CANCELLED)
        if self.failure is not None:
            raise self.failure
        return self.inventory

    def delete(self, case: UninstallCaseReference) -> bool:
        self.calls.append(case.case_id)
        if self.failure is not None:
            raise self.failure
        return self.complete


def test_preserve_and_discard_never_access_storage() -> None:
    store = Store()
    service = UninstallReviewService(store)
    service.discard()
    assert store.inspections == 0 and store.calls == []


def test_preparation_is_read_only_and_holds_bounded_fingerprints() -> None:
    store = Store(unavailable=2)
    review = UninstallReviewService(store).prepare()
    assert review.inventory is store.inventory
    assert store.inspections == 1 and store.calls == []
    assert "a" * 64 not in repr(review)


def test_complete_deletion_is_sequential_and_reports_unavailable_cases() -> None:
    store = Store(unavailable=2)
    service = UninstallReviewService(store)
    review = service.prepare()
    events: list[tuple[int, int]] = []
    result = service.confirm(
        review.review_id,
        CONFIRM,
        cancellation=lambda: False,
        progress=lambda a, b: events.append((a, b)),
    )
    assert store.calls == [item.case_id for item in store.inventory.cases]
    assert events == [(1, 3), (2, 3), (3, 3)]
    assert result == UninstallRemovalResult(3, 2, 0, False)


def test_confirmation_requires_current_review_and_cannot_repeat() -> None:
    store = Store()
    service = UninstallReviewService(store)
    review = service.prepare()
    service.confirm(review.review_id, CONFIRM, cancellation=lambda: False)
    with pytest.raises(UninstallReviewError) as raised:
        service.confirm(review.review_id, CONFIRM, cancellation=lambda: False)
    assert raised.value.code is UninstallReviewErrorCode.REVIEW_STALE
    assert len(store.calls) == 3


def test_invalid_confirmation_consumes_review_before_any_delete() -> None:
    store = Store()
    service = UninstallReviewService(store)
    review = service.prepare()
    with pytest.raises(UninstallReviewError) as raised:
        service.confirm(review.review_id, True, cancellation=lambda: False)  # type: ignore[arg-type]
    assert raised.value.code is UninstallReviewErrorCode.CONFIRMATION_REQUIRED
    with pytest.raises(UninstallReviewError):
        service.confirm(review.review_id, CONFIRM, cancellation=lambda: False)
    assert store.calls == []


def test_wrong_review_does_not_delete_and_consumes_consent() -> None:
    store = Store()
    service = UninstallReviewService(store)
    review = service.prepare()
    with pytest.raises(UninstallReviewError):
        service.confirm(uuid4(), CONFIRM, cancellation=lambda: False)
    with pytest.raises(UninstallReviewError):
        service.confirm(review.review_id, CONFIRM, cancellation=lambda: False)
    assert store.calls == []


def test_cancellation_before_first_case_preserves_every_case() -> None:
    store = Store(unavailable=1)
    service = UninstallReviewService(store)
    review = service.prepare()
    result = service.confirm(review.review_id, CONFIRM, cancellation=lambda: True)
    assert result == UninstallRemovalResult(0, 4, 0, True)
    assert store.calls == []


def test_cancellation_between_cases_preserves_unattempted_cases() -> None:
    store = Store(unavailable=1)
    service = UninstallReviewService(store)
    review = service.prepare()
    result = service.confirm(review.review_id, CONFIRM, cancellation=lambda: len(store.calls) >= 1)
    assert result == UninstallRemovalResult(1, 3, 0, True)
    assert len(store.calls) == 1


@pytest.mark.parametrize("fail", [False, True])
def test_partial_or_failed_cases_remain_counted(fail: bool) -> None:
    store = Store()
    service = UninstallReviewService(store)
    review = service.prepare()
    store.complete = False
    if fail:
        store.failure = OSError("PRIVATE_TOKEN external-file.pdf")
    result = service.confirm(review.review_id, CONFIRM, cancellation=lambda: False)
    assert result == UninstallRemovalResult(0, 3, 3, False)
    assert "PRIVATE_TOKEN" not in repr(result)


def test_inspection_failure_is_sanitized_and_discards_old_review() -> None:
    store = Store()
    service = UninstallReviewService(store)
    review = service.prepare()
    store.failure = OSError("PRIVATE_TOKEN external-file.pdf")
    with pytest.raises(UninstallReviewError) as raised:
        service.prepare()
    assert raised.value.code is UninstallReviewErrorCode.INSPECTION_FAILED
    assert "PRIVATE_TOKEN" not in str(raised.value)
    with pytest.raises(UninstallReviewError):
        service.confirm(review.review_id, CONFIRM, cancellation=lambda: False)
    assert store.calls == []


def test_cancelled_preparation_does_not_authorize_deletion() -> None:
    store = Store()
    service = UninstallReviewService(store)
    with pytest.raises(UninstallReviewError) as raised:
        service.prepare(cancellation=lambda: True)
    assert raised.value.code is UninstallReviewErrorCode.CANCELLED
    assert store.calls == []


def test_progress_failure_stops_further_deletion() -> None:
    store = Store()
    service = UninstallReviewService(store)
    review = service.prepare()

    def broken(_completed: int, _total: int) -> None:
        raise RuntimeError("PRIVATE_TOKEN")

    result = service.confirm(review.review_id, CONFIRM, cancellation=lambda: False, progress=broken)
    assert result == UninstallRemovalResult(1, 2, 0, True)


def test_inventory_rejects_unbounded_or_duplicate_references() -> None:
    case = UninstallCaseReference(uuid4(), "a" * 64, "b" * 64)
    with pytest.raises(UninstallReviewError):
        UninstallCaseInventory((case, case))
    with pytest.raises(UninstallReviewError):
        UninstallCaseInventory((), MAX_UNINSTALL_CASES + 1)
    with pytest.raises(UninstallReviewError):
        replace(case, manifest_sha256="PRIVATE_TOKEN")


def test_empty_inventory_is_valid_without_any_destructive_call() -> None:
    store = Store(count=0, unavailable=1)
    service = UninstallReviewService(store)
    review = service.prepare()
    assert service.confirm(
        review.review_id, CONFIRM, cancellation=lambda: False
    ) == UninstallRemovalResult(0, 1, 0, False)
    assert store.calls == []

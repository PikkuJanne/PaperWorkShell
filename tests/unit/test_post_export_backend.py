"""Serialized Qt bridge results stay correlated and reflect actual case closure."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest

from paperworkshell.app.organizer import OrganizerBackend, OrganizerViewState
from paperworkshell.app.post_export_deletion import PostExportDeletionService
from paperworkshell.domain.case_lifecycle import CaseDeletionResult, CaseDeletionStatus
from paperworkshell.domain.post_export_deletion import (
    PostExportDeletionErrorCode,
    PostExportDeletionInventory,
)
from paperworkshell.ui.backend import OperationCancellation, OrganizerBackendWorker, UiFailure
from tests.accessibility.test_accessibility_baseline import _application
from tests.unit.test_assisted_organizer import _harness
from tests.unit.test_organizer_backend import CASE_ID
from tests.unit.test_post_export_deletion import Store


@dataclass
class Events:
    reviews: list[tuple[int, object]] = field(default_factory=list)
    results: list[tuple[int, object]] = field(default_factory=list)
    failures: list[tuple[int, str, UiFailure]] = field(default_factory=list)
    states: list[OrganizerViewState] = field(default_factory=list)


def _worker() -> tuple[OrganizerBackendWorker, OrganizerBackend, Store, Events]:
    _application()
    backend, policy, _policies, _repository, _lifecycle, _ingest = _harness()
    backend.open_case(CASE_ID)
    store = Store(CASE_ID)

    @contextmanager
    def guard(
        *,
        cancellation: Callable[[], bool] | None = None,
        deadline: Callable[[], bool] | None = None,
    ) -> Iterator[None]:
        yield

    def close(case_id: UUID) -> None:
        assert case_id == CASE_ID
        backend.close_case()

    service = PostExportDeletionService(
        store,
        require_access=policy.require_access,
        close_case=close,
    )
    backend._post_export_deletion = service
    assert backend._session is not None
    service.remember_verified_export(
        backend._session.snapshot,
        export_count=1,
        export_names=("packet.pdf",),
        guard_exports=guard,
    )
    worker = OrganizerBackendWorker(lambda: backend)
    events = Events()
    worker.post_export_deletion_review_ready.connect(
        lambda operation_id, review: events.reviews.append((operation_id, review))
    )
    worker.post_export_deletion_finished.connect(
        lambda operation_id, result: events.results.append((operation_id, result))
    )
    worker.post_export_failed.connect(
        lambda operation_id, phase, failure: events.failures.append((operation_id, phase, failure))
    )
    worker.state_changed.connect(events.states.append)
    worker.initialize()
    return worker, backend, store, events


def test_success_publishes_correlated_result_after_empty_case_state() -> None:
    worker, backend, store, events = _worker()
    worker.begin_post_export_deletion(11, OperationCancellation())
    assert len(events.reviews) == 1
    request_id, review = events.reviews[0]
    assert request_id == 11 and type(review) is PostExportDeletionInventory
    worker.confirm_post_export_deletion(12, review.review_id)
    assert events.failures == []
    assert len(events.results) == 1
    assert events.results[0][0] == 12
    result = events.results[0][1]
    assert type(result) is CaseDeletionResult and result.status is CaseDeletionStatus.COMPLETE
    assert backend.state.case_id is None
    assert events.states[-1].case_id is None
    assert store.calls == ["inspect", "inspect", "delete"]


def test_cancelled_queued_inventory_request_performs_no_work() -> None:
    worker, backend, store, events = _worker()
    token = OperationCancellation()
    token.cancel()
    worker.begin_post_export_deletion(13, token)
    assert events.reviews == events.results == []
    assert store.calls == []
    assert backend.state.case_id == CASE_ID
    assert len(events.failures) == 1
    assert events.failures[0][0:2] == (13, "inventory")


def test_cancel_after_preparation_discards_review_without_closing_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, backend, store, events = _worker()
    token = OperationCancellation()
    original = OrganizerBackend.begin_post_export_deletion

    def prepare(
        self: OrganizerBackend,
        *,
        cancellation: Callable[[], bool] | None = None,
    ) -> PostExportDeletionInventory:
        result = original(self, cancellation=cancellation)
        token.cancel()
        return result

    monkeypatch.setattr(OrganizerBackend, "begin_post_export_deletion", prepare)
    worker.begin_post_export_deletion(14, token)
    assert events.reviews == events.results == []
    assert backend.state.case_id == CASE_ID
    assert "delete" not in store.calls
    assert len(events.failures) == 1


def test_wrong_review_id_is_safe_correlated_failure_and_preserves_active_case() -> None:
    worker, backend, store, events = _worker()
    worker.begin_post_export_deletion(15, OperationCancellation())
    worker.confirm_post_export_deletion(16, uuid4())
    assert events.results == []
    assert backend.state.case_id == CASE_ID
    assert "delete" not in store.calls
    request_id, phase, failure = events.failures[-1]
    assert (request_id, phase) == (16, "delete")
    assert failure.operation_id == 16
    assert failure.code == PostExportDeletionErrorCode.REVIEW_STALE.value


@pytest.mark.parametrize("request_id", [0, -1, True])
def test_invalid_request_id_cannot_start_removal(request_id: int) -> None:
    worker, backend, store, events = _worker()
    worker.begin_post_export_deletion(request_id, OperationCancellation())
    assert events.reviews == events.results == []
    assert store.calls == []
    assert backend.state.case_id == CASE_ID


def test_storage_failure_after_close_publishes_closed_state_and_sanitized_message() -> None:
    worker, backend, store, events = _worker()
    store.fail_delete = True
    worker.begin_post_export_deletion(17, OperationCancellation())
    review = events.reviews[-1][1]
    assert type(review) is PostExportDeletionInventory
    worker.confirm_post_export_deletion(18, review.review_id)
    assert events.results == []
    assert backend.state.case_id is None
    assert events.states[-1].case_id is None
    failure = events.failures[-1][2]
    assert failure.code == PostExportDeletionErrorCode.STORAGE_FAILED.value
    assert "PRIVATE_TOKEN" not in failure.message

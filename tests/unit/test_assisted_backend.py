"""Correlated, cancelable assisted unlock requests on the real Qt backend bridge."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

import pytest
from PySide6.QtCore import Qt

from paperworkshell.app import assisted_use as assisted_application
from paperworkshell.app.organizer import OrganizerBackend, OrganizerViewState
from paperworkshell.domain.assisted_use import (
    AssistedUseErrorCode,
    AssistedUseStatus,
)
from paperworkshell.ui.backend import (
    OperationCancellation,
    OrganizerBackendCommands,
    OrganizerBackendWorker,
)
from tests.accessibility.test_accessibility_baseline import _application
from tests.unit.test_assisted_organizer import _PASSPHRASE, _harness
from tests.unit.test_organizer_backend import CASE_ID


@dataclass
class _Events:
    succeeded: list[tuple[int, object]] = field(default_factory=list)
    failures: list[tuple[int, object]] = field(default_factory=list)
    finished: list[int] = field(default_factory=list)
    states: list[object] = field(default_factory=list)
    generic_failures: list[object] = field(default_factory=list)


def _worker() -> tuple[OrganizerBackendWorker, _Events]:
    _application()
    backend, service, _store, _repository, _lifecycle, _ingest = _harness()
    service.enroll(CASE_ID, 7, _PASSPHRASE, consent=True)
    service.lock()
    worker = OrganizerBackendWorker(lambda: backend)
    events = _Events()
    worker.assisted_unlocked.connect(
        lambda request_id, state: events.succeeded.append((request_id, state))
    )
    worker.assisted_unlock_failed.connect(
        lambda request_id, code: events.failures.append((request_id, code))
    )
    worker.assisted_unlock_finished.connect(events.finished.append)
    worker.state_changed.connect(events.states.append)
    worker.failed.connect(events.generic_failures.append)
    worker.initialize()
    assert not events.generic_failures
    return worker, events


def test_cancelled_queued_request_never_derives_opens_or_grants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, events = _worker()
    backend = worker._backend
    assert backend is not None and backend.assisted_use is not None
    commands = OrganizerBackendCommands()
    commands.unlock_assisted_case_requested.connect(
        worker.unlock_assisted_case, Qt.ConnectionType.QueuedConnection
    )
    calls: list[str] = []
    monkeypatch.setattr(
        assisted_application, "_derive", lambda _secret, _salt: calls.append("derive")
    )
    token = OperationCancellation()
    commands.unlock_assisted_case_requested.emit(17, CASE_ID, _PASSPHRASE, token)
    token.cancel()
    worker.lock_assisted_immediately()
    _application().processEvents()
    assert calls == []
    assert backend.state.case_id is None
    assert backend.assisted_use.snapshot is not None
    assert backend.assisted_use.snapshot.status is AssistedUseStatus.LOCKED
    assert events.succeeded == events.failures == events.states == events.generic_failures == []
    assert events.finished == [17]


@pytest.mark.parametrize("moment", ("before_derive", "after_derive"))
@pytest.mark.parametrize("revoke_immediately", (False, True))
def test_cancellation_during_authentication_never_opens_or_emits_content(
    moment: str, revoke_immediately: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker, events = _worker()
    backend = worker._backend
    assert backend is not None and backend.assisted_use is not None
    token = OperationCancellation()
    derive = assisted_application._derive

    def cancelled_derive(secret: bytes, salt: bytes) -> bytes:
        if moment == "before_derive":
            token.cancel()
            if revoke_immediately:
                worker.lock_assisted_immediately()
        result = derive(secret, salt)
        if moment == "after_derive":
            token.cancel()
            if revoke_immediately:
                worker.lock_assisted_immediately()
        return result

    monkeypatch.setattr(assisted_application, "_derive", cancelled_derive)
    worker.unlock_assisted_case(18, CASE_ID, _PASSPHRASE, token)
    assert backend.state.case_id is None
    assert backend.assisted_use.snapshot is not None
    assert backend.assisted_use.snapshot.status is AssistedUseStatus.LOCKED
    assert events.succeeded == events.failures == events.states == events.generic_failures == []
    assert events.finished == [18]


def test_cancelled_open_finishes_locked_without_publishing_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, events = _worker()
    backend = worker._backend
    assert backend is not None and backend.assisted_use is not None
    token = OperationCancellation()
    open_case = backend.open_case
    opened: list[bool] = []

    def cancelled_open(self: OrganizerBackend, case_id: UUID) -> OrganizerViewState:
        assert self is backend
        opened.append(True)
        assert case_id == CASE_ID
        result = open_case(CASE_ID)
        token.cancel()
        return result

    monkeypatch.setattr(OrganizerBackend, "open_case", cancelled_open)
    worker.unlock_assisted_case(19, CASE_ID, _PASSPHRASE, token)
    assert opened == [True]
    assert backend.assisted_use.snapshot is not None
    assert backend.assisted_use.snapshot.status is AssistedUseStatus.LOCKED
    assert events.succeeded == events.failures == events.states == events.generic_failures == []
    assert events.finished == [19]


def test_wrong_phrase_emits_only_correlated_safe_failure_and_finished() -> None:
    worker, events = _worker()
    worker.unlock_assisted_case(20, CASE_ID, "wrong synthetic passphrase", OperationCancellation())
    assert events.failures == [(20, AssistedUseErrorCode.UNLOCK_FAILED)]
    assert events.finished == [20]
    assert events.succeeded == events.states == events.generic_failures == []


def test_unknown_failure_is_correlated_and_never_stringified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, events = _worker()
    backend = worker._backend
    assert backend is not None and backend.assisted_use is not None

    def private_failure(_self: OrganizerBackend, _case_id: UUID) -> OrganizerViewState:
        raise RuntimeError("PRIVATE_VALUE C:\\PRIVATE_PATH\\document.pdf")

    monkeypatch.setattr(OrganizerBackend, "open_case", private_failure)
    worker.unlock_assisted_case(21, CASE_ID, _PASSPHRASE, OperationCancellation())
    assert events.failures == [(21, None)]
    assert events.finished == [21]
    assert events.succeeded == events.states == events.generic_failures == []
    assert "PRIVATE" not in repr(events)
    assert backend.assisted_use.snapshot is not None
    assert backend.assisted_use.snapshot.status is AssistedUseStatus.LOCKED


def test_current_valid_grant_publishes_one_correlated_success_and_finished() -> None:
    worker, events = _worker()
    worker.unlock_assisted_case(22, CASE_ID, _PASSPHRASE, OperationCancellation())
    assert len(events.succeeded) == 1
    assert events.succeeded[0][0] == 22
    assert events.finished == [22]
    assert len(events.states) == 1
    assert events.failures == events.generic_failures == []


@pytest.mark.parametrize("token", (None, object(), "cancelled"))
def test_wrong_cancellation_type_fails_closed_without_opening(token: object) -> None:
    worker, events = _worker()
    backend = worker._backend
    assert backend is not None
    worker.unlock_assisted_case(23, CASE_ID, _PASSPHRASE, token)
    assert events.failures == [(23, None)]
    assert events.finished == [23]
    assert backend.state.case_id is None
    assert events.succeeded == events.states == events.generic_failures == []


def test_uninitialized_backend_finishes_with_correlated_safe_failure() -> None:
    _application()
    worker = OrganizerBackendWorker(lambda: _harness()[0])
    failures: list[tuple[int, object]] = []
    finished: list[int] = []
    generic: list[object] = []
    worker.assisted_unlock_failed.connect(
        lambda request_id, code: failures.append((request_id, code))
    )
    worker.assisted_unlock_finished.connect(finished.append)
    worker.failed.connect(generic.append)
    worker.unlock_assisted_case(24, CASE_ID, _PASSPHRASE, OperationCancellation())
    assert failures == [(24, None)]
    assert finished == [24]
    assert generic == []


@pytest.mark.parametrize("request_id", (0, False, True, -1))
def test_request_id_must_be_an_exact_positive_integer_before_authentication(
    request_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker, events = _worker()
    backend = worker._backend
    assert backend is not None and backend.assisted_use is not None
    calls: list[bool] = []
    monkeypatch.setattr(assisted_application, "_derive", lambda _secret, _salt: calls.append(True))
    worker.unlock_assisted_case(request_id, CASE_ID, _PASSPHRASE, OperationCancellation())
    assert calls == []
    assert backend.state.case_id is None
    assert backend.assisted_use.snapshot is not None
    assert backend.assisted_use.snapshot.status is AssistedUseStatus.LOCKED
    assert events.failures == [(int(request_id), None)]
    assert events.finished == [int(request_id)]
    assert events.succeeded == events.states == events.generic_failures == []


def test_canceled_old_queued_request_preserves_a_new_current_grant() -> None:
    worker, events = _worker()
    backend = worker._backend
    assert backend is not None and backend.assisted_use is not None
    snapshot = backend.assisted_use.unlock(CASE_ID, _PASSPHRASE)
    backend.open_case(CASE_ID)
    token = OperationCancellation()
    token.cancel()
    worker.unlock_assisted_case(25, CASE_ID, _PASSPHRASE, token)
    assert backend.assisted_use.snapshot == snapshot
    assert events.finished == [25]
    assert events.succeeded == events.failures == events.states == events.generic_failures == []

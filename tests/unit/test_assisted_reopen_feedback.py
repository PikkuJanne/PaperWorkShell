"""Pure Qt regression: accepted reopening must replace closed feedback."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent

from paperworkshell.app.organizer import OrganizerViewState
from paperworkshell.domain.assisted_use import AssistedUseStatus
from tests.accessibility.test_accessibility_baseline import _state, _window
from tests.unit.test_assisted_session import _snapshot, _submit_unlock

CLOSED = "Case closed. All queued changes were saved."


@pytest.mark.parametrize("pages", (0, 2))
def test_correlated_reopen_announces_accepted_page_count_only_after_completion(
    monkeypatch: pytest.MonkeyPatch, pages: int
) -> None:
    messages: list[str] = []

    def capture(event: object) -> None:
        if isinstance(event, QAccessibleAnnouncementEvent):
            messages.append(event.message())

    monkeypatch.setattr(QAccessible, "updateAccessibility", capture)
    with _window() as (window, commands, worker):
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        commands.unlock_assisted_case_requested.disconnect(worker.unlock_assisted_case)
        worker.initialized.emit()
        worker.state_changed.emit(OrganizerViewState.empty())
        worker.status_changed.emit(CLOSED)
        controller = window._assisted_session
        controller.changed(_snapshot(AssistedUseStatus.LOCKED))
        controller._timer.stop()
        controller.unlock()
        _submit_unlock(controller)
        request = controller._unlock_request_id
        assert request is not None
        monkeypatch.setattr(worker, "current_assisted_snapshot", lambda: _snapshot())
        messages.clear()
        # Use the actual backend's ordered signals: grant, state, finally-ack.
        worker.assisted_unlocked.emit(request, _snapshot())
        assert not controller.locked
        assert window._status_label.text() == CLOSED
        state = _state()
        if pages == 0:
            state = replace(state, pages=())
        worker.state_changed.emit(state)
        assert window._state is state
        assert window._status_label.text() == CLOSED
        worker.assisted_unlock_finished.emit(request - 1)
        assert window._status_label.text() == CLOSED
        worker.assisted_unlock_finished.emit(request)
        expected = f"Case opened with {pages} active pages."
        assert window._status_label.text() == expected
        assert messages == [expected]
        # An old duplicate completion must neither repeat speech nor free new work.
        worker.assisted_unlock_finished.emit(request)
        assert messages == [expected]
        controller._timer.stop()


@pytest.mark.parametrize(
    "boundary",
    (
        "cancel",
        "relock",
        "live-locked",
        "wrong-case",
        "no-state",
        "expired",
        "clock",
        "closing",
        "close-pending",
    ),
)
def test_rejected_or_obsolete_reopen_never_announces_success(
    monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    messages: list[str] = []

    def capture(event: object) -> None:
        if isinstance(event, QAccessibleAnnouncementEvent):
            messages.append(event.message())

    monkeypatch.setattr(QAccessible, "updateAccessibility", capture)
    with _window() as (window, commands, worker):
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        commands.unlock_assisted_case_requested.disconnect(worker.unlock_assisted_case)
        worker.initialized.emit()
        worker.state_changed.emit(OrganizerViewState.empty())
        worker.status_changed.emit(CLOSED)
        controller = window._assisted_session
        controller.changed(_snapshot(AssistedUseStatus.LOCKED))
        controller._timer.stop()
        controller.unlock()
        dialog = _submit_unlock(controller)
        request = controller._unlock_request_id
        assert request is not None
        current = _snapshot()
        monkeypatch.setattr(worker, "current_assisted_snapshot", lambda: current)
        if boundary == "cancel":
            dialog.reject()
        worker.assisted_unlocked.emit(request, _snapshot())
        state = _state()
        if boundary == "wrong-case":
            state = replace(state, case_id=UUID("60000000-0000-4000-8000-000000000099"))
        if boundary != "no-state":
            worker.state_changed.emit(state)
        if boundary == "relock":
            controller.cover()
        elif boundary == "live-locked":
            current = _snapshot(AssistedUseStatus.LOCKED)
        elif boundary == "expired":
            controller._snapshot = replace(
                _snapshot(), expires_at=datetime.now(UTC) - timedelta(seconds=1)
            )
        elif boundary == "clock":
            controller._last_wall = datetime.now(UTC) + timedelta(minutes=1)
        elif boundary == "closing":
            window._closing_requested = True
        elif boundary == "close-pending":
            window._case_close_pending = True
        messages.clear()
        worker.assisted_unlock_finished.emit(request)
        assert not any(
            message.startswith(("Case opened", "Case recovered")) for message in messages
        )
        assert window._status_label.text() == CLOSED
        assert controller._unlock_request_id is None
        controller._timer.stop()


def test_recovered_protected_case_announces_existing_review_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _window() as (window, commands, worker):
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        commands.unlock_assisted_case_requested.disconnect(worker.unlock_assisted_case)
        worker.initialized.emit()
        worker.status_changed.emit(CLOSED)
        controller = window._assisted_session
        controller.changed(_snapshot(AssistedUseStatus.LOCKED))
        controller._timer.stop()
        controller.unlock()
        _submit_unlock(controller)
        request = controller._unlock_request_id
        assert request is not None
        monkeypatch.setattr(worker, "current_assisted_snapshot", lambda: _snapshot())
        worker.assisted_unlocked.emit(request, _snapshot())
        worker.state_changed.emit(_state(recovery_review_required=True))
        worker.assisted_unlock_finished.emit(request)
        assert window._status_label.text() == (
            "Case recovered from the validated save journal. "
            "Review the page organizer before continuing."
        )
        controller._timer.stop()

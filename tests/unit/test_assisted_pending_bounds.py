"""Bounded Qt acknowledgements for assisted checks and asynchronous case close."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

from paperworkshell.app.organizer import OrganizerViewState
from paperworkshell.ui.backend import OrganizerBackendWorker, UiFailure
from tests.accessibility.test_accessibility_baseline import (
    _application,
    _state,
    _unused_backend_factory,
    _window,
)
from tests.unit.test_assisted_organizer import _PASSPHRASE, _harness
from tests.unit.test_assisted_session import _snapshot, _submit_unlock
from tests.unit.test_organizer_backend import CASE_ID


@pytest.mark.parametrize("reply", ("generic", "unlock"))
def test_generic_or_unlock_reply_cannot_acknowledge_a_pending_scheduler_check(
    reply: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _window() as (window, commands, worker):
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        commands.unlock_assisted_case_requested.disconnect(worker.unlock_assisted_case)
        requested: list[object] = []
        commands.assisted_check_requested.connect(requested.append)
        controller = window._assisted_session
        controller.changed(_snapshot())
        controller._timer.stop()
        controller.tick()
        assert len(requested) == 1
        if reply == "generic":
            worker.assisted_use_changed.emit(_snapshot())
        else:
            controller.cover()
            controller.unlock()
            _submit_unlock(controller)
            monkeypatch.setattr(worker, "current_assisted_snapshot", lambda: _snapshot())
            controller._unlocked(controller._privacy_epoch, _snapshot())
        controller._timer.stop()
        for _ in range(3):
            controller.tick()
        assert len(requested) == 1
        worker.assisted_check_finished.emit()
        controller.tick()
        assert len(requested) == 2
        controller.changed(None)


def test_selection_after_close_request_cannot_restart_preview_queue() -> None:
    with _window() as (window, commands, worker):
        commands.thumbnails_requested.disconnect(worker.get_thumbnails)
        commands.close_case_requested.disconnect(worker.close_case)
        events: list[str] = []
        commands.close_case_requested.connect(lambda: events.append("close"))
        commands.thumbnails_requested.connect(lambda *_args: events.append("preview"))
        worker.initialized.emit()
        state = _state()
        worker.state_changed.emit(
            replace(state, pages=tuple(replace(p, preview_supported=True) for p in state.pages))
        )
        window._preview_refresh_timer.stop()
        _application().processEvents()
        assert window._active_operation is None and window._current_preview_page_ids()
        window._request_close_case()
        assert events == ["close"]
        window._page_view.setCurrentIndex(window._model.index(1, 0))
        assert (window._case_close_pending, window._preview_refresh_timer.isActive()) == (
            True,
            False,
        )
        window._preview_refresh_timer.timeout.emit()
        assert events == ["close"]
        worker.state_changed.emit(OrganizerViewState.empty())
        worker.case_close_finished.emit()
        assert not window._case_close_pending and not window._preview_refresh_timer.isActive()
        worker.state_changed.emit(
            replace(state, pages=tuple(replace(p, preview_supported=True) for p in state.pages))
        )
        assert window._preview_refresh_timer.isActive() and events == ["close"]
        window._preview_refresh_timer.stop()
        window._preview_refresh_timer.timeout.emit()
        assert events == ["close", "preview"]


def test_scheduler_acknowledgement_occurs_even_when_backend_is_unavailable() -> None:
    worker = OrganizerBackendWorker(_unused_backend_factory)
    acknowledgements: list[bool] = []
    worker.assisted_check_finished.connect(lambda: acknowledgements.append(True))
    worker.check_assisted_use()
    assert acknowledgements == [True]


@pytest.mark.parametrize("outcome", ("success", "storage_failure", "invalid_id"))
def test_scheduler_always_acknowledges_real_service_outcomes(
    outcome: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, service, store, *_rest = _harness()
    service.enroll(CASE_ID, 7, _PASSPHRASE, consent=True)
    worker = OrganizerBackendWorker(lambda: backend)
    worker._backend = backend
    if outcome == "storage_failure":
        now = service._clock()
        monkeypatch.setattr(service, "_clock", lambda: now + timedelta(minutes=1))
        store.fail_save = True
    acknowledgements: list[bool] = []
    failures: list[object] = []
    worker.assisted_check_finished.connect(lambda: acknowledgements.append(True))
    worker.failed.connect(failures.append)
    worker.check_assisted_use("invalid" if outcome == "invalid_id" else CASE_ID)
    assert acknowledgements == [True]
    assert bool(failures) is (outcome != "success")


def test_close_acknowledgement_allows_recovery_after_backend_failure() -> None:
    with _window() as (window, commands, worker):
        commands.thumbnails_requested.disconnect(worker.get_thumbnails)
        commands.close_case_requested.disconnect(worker.close_case)
        events: list[str] = []
        commands.thumbnails_requested.connect(lambda *_args: events.append("preview"))
        worker.initialized.emit()
        state = _state()
        worker.state_changed.emit(
            replace(state, pages=tuple(replace(p, preview_supported=True) for p in state.pages))
        )
        window._preview_refresh_timer.stop()
        _application().processEvents()
        window._request_close_case()
        assert (window._case_close_pending, window._controls_enabled) == (True, False)
        window._request_close_case()
        window._page_view.setCurrentIndex(window._model.index(1, 0))
        assert (window._case_close_pending, window._preview_refresh_timer.isActive()) == (
            True,
            False,
        )
        window._preview_refresh_timer.timeout.emit()
        assert events == []
        worker.close_case()
        assert not window._case_close_pending and window._controls_enabled
        window._page_view.setCurrentIndex(window._model.index(0, 0))
        assert window._preview_refresh_timer.isActive() and events == []
        window._preview_refresh_timer.stop()
        window._preview_refresh_timer.timeout.emit()
        assert events == ["preview"]


def test_pending_assisted_close_keeps_immediate_keyboard_hiding_and_unrelated_failures_do_not_ack() -> (
    None
):
    with _window() as (window, commands, worker):
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        commands.close_case_requested.disconnect(worker.close_case)
        worker.initialized.emit()
        worker.state_changed.emit(_state())
        window._assisted_session.changed(_snapshot())
        window._assisted_session._timer.stop()
        window._request_close_case()
        assert window._case_close_pending and not window._controls_enabled
        assert not window._import_button.isEnabled()
        assert window._assisted_lock_button.isEnabled() and window._assisted_lock_button.hasFocus()
        assert (
            window._status_label.text() == "Closing the case. Saving changes and finishing cleanup."
        )
        worker.failed.emit(UiFailure("UNRELATED", "The previous action failed.", True))
        assert window._case_close_pending and not window._controls_enabled
        QTest.keyClick(window._assisted_lock_button, Qt.Key.Key_Space)
        assert window._assisted_session.locked and not window._application_scroll.isVisible()
        window._assisted_session.changed(None)


def test_failed_retention_close_locks_immediately_and_still_acknowledges() -> None:
    backend, service, store, *_rest = _harness()
    service.enroll(CASE_ID, 7, _PASSPHRASE, consent=True)
    backend.open_case(CASE_ID)
    with _window() as (window, commands, worker):
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        worker._backend = backend
        worker.initialized.emit()
        worker.state_changed.emit(backend.state)
        worker.assisted_use_changed.emit(service.snapshot)
        window._assisted_session._timer.stop()
        store.fail_save = True
        window._request_close_case()
        assert not window._case_close_pending
        assert window._assisted_session.locked
        assert not window._application_scroll.isVisible()
        window._assisted_session.changed(None)


def test_old_pending_state_cannot_queue_autosave_or_retry_behind_close() -> None:
    with _window() as (window, commands, worker):
        commands.close_case_requested.disconnect(worker.close_case)
        commands.flush_requested.disconnect(worker.flush_autosave)
        commands.retry_save_requested.disconnect(worker.retry_autosave)
        requested: list[str] = []
        commands.flush_requested.connect(lambda: requested.append("flush"))
        commands.retry_save_requested.connect(lambda: requested.append("retry"))
        worker.initialized.emit()
        worker.state_changed.emit(_state())
        window._request_close_case()
        worker.state_changed.emit(_state(autosave_pending=True))
        window._request_autosave_flush()
        window._request_retry_save()
        assert requested == []


def test_successful_close_keeps_handover_focus_and_ordinary_close_restores_chooser() -> None:
    backend, service, _store, *_rest = _harness()
    service.enroll(CASE_ID, 7, _PASSPHRASE, consent=True)
    backend.open_case(CASE_ID)
    with _window() as (window, commands, worker):
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        worker._backend = backend
        worker.initialized.emit()
        worker.state_changed.emit(backend.state)
        worker.assisted_use_changed.emit(service.snapshot)
        window._assisted_session._timer.stop()
        window._request_close_case()
        _application().processEvents()
        handover = _application().activeModalWidget()
        assert isinstance(handover, QDialog) and handover.objectName() == "assistedHandoverDialog"
        focused = _application().focusWidget()
        assert focused is not None and handover.isAncestorOf(focused)
        assert not window._case_close_pending
        handover.reject()
        _application().processEvents()
    with _window() as (window, commands, worker):
        commands.close_case_requested.disconnect(worker.close_case)
        worker.initialized.emit()
        worker.state_changed.emit(_state())
        window._request_close_case()
        worker.state_changed.emit(OrganizerViewState.empty())
        worker.case_close_finished.emit()
        assert window._controls_enabled and window._case_list.hasFocus()

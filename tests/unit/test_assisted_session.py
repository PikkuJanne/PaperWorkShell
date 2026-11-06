"""PWS-0705 privacy curtain behavior with real owned Qt window surfaces."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QPushButton, QVBoxLayout

from paperworkshell.app.organizer import OrganizerViewState
from paperworkshell.domain.assisted_use import (
    AssistedUseErrorCode,
    AssistedUseSnapshot,
    AssistedUseStatus,
)
from paperworkshell.ui.assisted_session import AssistedSessionController, _PrivacyCurtain
from paperworkshell.ui.assisted_use import AssistedLockScreen, AssistedUnlockDialog
from paperworkshell.ui.backend import OperationCancellation, UiFailure, UiProgress
from paperworkshell.ui.window import PaperWorkShellWindow, _LongOperationKind
from tests.accessibility.test_accessibility_baseline import CASE_ID, _application, _state, _window
from tests.unit.test_assisted_organizer import _PASSPHRASE, _harness
from tests.unit.test_organizer_backend import CASE_ID as BACKEND_CASE_ID


def _snapshot(status: AssistedUseStatus = AssistedUseStatus.UNLOCKED) -> AssistedUseSnapshot:
    return AssistedUseSnapshot(CASE_ID, status, datetime.now(UTC) + timedelta(days=7), 7)


def _locked(controller: AssistedSessionController) -> bool:
    return controller.locked


def _unlock_prompt(controller: AssistedSessionController) -> AssistedUnlockDialog | None:
    return controller._unlock_dialog


def _assert_pending_unlock(dialog: AssistedUnlockDialog) -> None:
    secret = dialog.findChild(QLineEdit, "assistedUnlockPassphrase")
    submit = dialog.findChild(QPushButton, "assistedUnlockSubmit")
    cancel = dialog.findChild(QPushButton, "assistedUnlockCancel")
    assert secret is not None and submit is not None and cancel is not None
    assert dialog.isEnabled()
    assert not secret.isEnabled() and not submit.isEnabled()
    assert cancel.isEnabled()


def _submit_unlock(controller: AssistedSessionController) -> AssistedUnlockDialog:
    dialog = controller._unlock_dialog
    assert dialog is not None
    secret = dialog.findChild(QLineEdit, "assistedUnlockPassphrase")
    assert secret is not None
    secret.setText("synthetic local case passphrase")
    dialog.accept()
    return dialog


def _content_dialog(parent: PaperWorkShellWindow) -> QDialog:
    dialog = QDialog(parent)
    dialog.setObjectName("syntheticDocumentDialog")
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel("SYNTHETIC PRIVATE DOCUMENT VALUE"))
    return dialog


def test_lock_hides_main_surface_owned_dialogs_and_later_dialog_shows() -> None:
    with _window() as (window, commands, worker):
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        worker.initialized.emit()
        worker.state_changed.emit(_state())
        window._assisted_session.changed(_snapshot())
        window._assisted_session._timer.stop()
        dialog = _content_dialog(window)
        dialog.show()
        _application().processEvents()
        try:
            assert dialog.isVisible()
            window._assisted_session.cover()
            assert window._assisted_session.locked
            assert not window._application_scroll.isVisible()
            assert not window.menuBar().isVisible()
            assert not dialog.isVisible()
            assert all(not shortcut.isEnabled() for shortcut in window._shortcuts)
            dialog.show()
            _application().processEvents()
            assert not dialog.isVisible()
        finally:
            dialog.hide()
            dialog.deleteLater()
            window._assisted_session.changed(None)


@pytest.mark.parametrize("dismissal", ("escape", "close", "reject"))
def test_privacy_curtain_cannot_be_dismissed_to_reveal_case(dismissal: str) -> None:
    with _window() as (window, commands, worker):
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        worker.state_changed.emit(_state())
        controller = window._assisted_session
        controller.changed(_snapshot(AssistedUseStatus.LOCKED))
        controller._timer.stop()
        _application().processEvents()
        curtain = controller._curtain
        assert curtain is not None
        if dismissal == "escape":
            QTest.keyClick(curtain, Qt.Key.Key_Escape)
        elif dismissal == "close":
            curtain.close()
        else:
            curtain.reject()
        _application().processEvents()
        assert curtain.isVisible()
        assert controller.locked
        assert not window._application_scroll.isVisible()
        controller.changed(None)


def test_unrelated_or_delayed_unlocked_snapshots_never_remove_the_curtain() -> None:
    with _window() as (window, commands, worker):
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        worker.state_changed.emit(_state())
        controller = window._assisted_session
        controller.changed(_snapshot(AssistedUseStatus.LOCKED))
        controller._timer.stop()
        controller.changed(_snapshot())
        assert controller.locked
        assert not window._application_scroll.isVisible()
        controller._unlocked(controller._privacy_epoch, _snapshot())
        assert controller.locked  # no active password prompt means no grant
        controller.unlock()
        assert controller._unlock_dialog is not None
        controller._unlocked(controller._privacy_epoch - 1, _snapshot())
        assert controller.locked
        assert not window._application_scroll.isVisible()
        controller.changed(None)


def test_unlock_cancel_leaves_content_hidden_and_exact_correlated_grant_restores_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _window() as (window, commands, worker):
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        commands.unlock_assisted_case_requested.disconnect(worker.unlock_assisted_case)
        worker.state_changed.emit(_state())
        controller = window._assisted_session
        controller.changed(_snapshot(AssistedUseStatus.LOCKED))
        controller._timer.stop()
        controller.unlock()
        dialog = controller._unlock_dialog
        assert dialog is not None
        dialog.reject()
        assert _locked(controller)
        assert controller._unlock_dialog is None
        assert not window._application_scroll.isVisible()
        controller.unlock()
        monkeypatch.setattr(worker, "current_assisted_snapshot", lambda: _snapshot())
        _submit_unlock(controller)
        controller._unlocked(controller._privacy_epoch, _snapshot())
        assert not _locked(controller)
        assert window._application_scroll.isVisible()
        assert controller._unlock_dialog is None
        assert controller._curtain is None
        controller._timer.stop()


def test_queued_unlock_success_after_backend_relock_cannot_reveal_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _window() as (window, commands, worker):
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        commands.unlock_assisted_case_requested.disconnect(worker.unlock_assisted_case)
        worker.state_changed.emit(_state())
        controller = window._assisted_session
        controller.changed(_snapshot(AssistedUseStatus.LOCKED))
        controller._timer.stop()
        controller.unlock()
        _submit_unlock(controller)
        monkeypatch.setattr(
            worker, "current_assisted_snapshot", lambda: _snapshot(AssistedUseStatus.LOCKED)
        )
        controller._unlocked(controller._privacy_epoch, _snapshot())
        assert controller.locked
        assert not window._application_scroll.isVisible()
        controller.changed(None)


def test_password_submission_transfers_exact_secret_once_and_keeps_case_covered() -> None:
    with _window() as (window, commands, worker):
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        commands.unlock_assisted_case_requested.disconnect(worker.unlock_assisted_case)
        submitted: list[tuple[int, object, str, object]] = []
        commands.unlock_assisted_case_requested.connect(
            lambda request_id, case_id, secret, cancellation: submitted.append(
                (request_id, case_id, secret, cancellation)
            )
        )
        controller = window._assisted_session
        controller.changed(_snapshot(AssistedUseStatus.LOCKED))
        controller._timer.stop()
        controller.unlock()
        dialog = controller._unlock_dialog
        assert dialog is not None
        secret = dialog.findChild(QLineEdit, "assistedUnlockPassphrase")
        assert secret is not None
        secret.setText("synthetic local case passphrase")
        dialog.accept()
        _application().processEvents()
        assert len(submitted) == 1
        assert submitted[0][:3] == (
            controller._privacy_epoch,
            CASE_ID,
            "synthetic local case passphrase",
        )
        assert isinstance(submitted[0][3], OperationCancellation)
        assert not submitted[0][3].cancelled()
        assert secret.text() == ""
        assert dialog.isVisible()
        _assert_pending_unlock(dialog)
        assert controller.locked
        assert not window._application_scroll.isVisible()
        controller._unlock_failed(controller._privacy_epoch, None)
        assert dialog.isEnabled()
        assert secret.text() == ""
        controller.changed(None)


def test_end_or_reset_never_restores_hidden_case_dialogs() -> None:
    with _window() as (window, commands, worker):
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        worker.state_changed.emit(_state())
        controller = window._assisted_session
        controller.changed(_snapshot())
        controller._timer.stop()
        dialog = _content_dialog(window)
        dialog.show()
        _application().processEvents()
        controller.cover()
        assert not dialog.isVisible()
        controller.changed(None)
        assert (
            not window._application_scroll.isVisible()
            or window._state is None
            or window._state.case_id is None
        )
        _application().processEvents()
        # A deleted wrapper also proves that there is no restored content window.
        try:
            assert not dialog.isVisible()
            dialog.deleteLater()
        except RuntimeError:
            pass
        assert not controller.active


def test_locked_case_suppresses_status_error_progress_and_late_state_announcements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[str] = []

    def capture(event: object) -> None:
        if isinstance(event, QAccessibleAnnouncementEvent):
            messages.append(event.message())

    monkeypatch.setattr(QAccessible, "updateAccessibility", capture)
    with _window() as (window, commands, worker):
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        worker.initialized.emit()
        worker.state_changed.emit(_state())
        controller = window._assisted_session
        controller.changed(_snapshot())
        controller._timer.stop()
        operation = window._begin_long_operation(_LongOperationKind.IMPORT)
        controller.cover()
        messages.clear()
        prior = window._state
        worker.status_changed.emit("SYNTHETIC PRIVATE STATUS")
        worker.failed.emit(UiFailure("SYNTHETIC_FAILURE", "SYNTHETIC PRIVATE ERROR", True))
        worker.progress_changed.emit(
            UiProgress("Import", "SYNTHETIC PRIVATE PHASE", 1, 3, True, operation.operation_id)
        )
        worker.state_changed.emit(_state(autosave_pending=True))
        _application().processEvents()
        assert messages == []
        assert window._state is prior
        assert not window._application_scroll.isVisible()
        controller.changed(None)


def test_show_event_filter_hides_only_owned_content_not_the_unlock_prompt() -> None:
    with _window() as (window, commands, worker):
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        controller = window._assisted_session
        controller.changed(_snapshot(AssistedUseStatus.LOCKED))
        controller._timer.stop()
        controller.unlock()
        dialog = controller._unlock_dialog
        assert isinstance(dialog, AssistedUnlockDialog)
        _application().processEvents()
        assert dialog.isVisible()
        secret = dialog.findChild(QLineEdit, "assistedUnlockPassphrase")
        assert secret is not None
        assert not controller.eventFilter(secret, QEvent(QEvent.Type.KeyPress))
        assert controller.eventFilter(window._import_button, QEvent(QEvent.Type.KeyPress))
        curtain = cast(_PrivacyCurtain, controller._curtain)
        assert curtain.isVisible()
        controller.changed(None)


@pytest.mark.parametrize("surface", ("case_chooser", "active_assistance"))
def test_assisted_actions_follow_the_visual_order_in_the_main_keyboard_chain(surface: str) -> None:
    with _window() as (window, commands, worker):
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        worker.initialized.emit()
        if surface == "active_assistance":
            worker.state_changed.emit(_state())
            window._assisted_session.changed(_snapshot())
            window._assisted_session._timer.stop()
            before, action = window._import_button, window._assisted_lock_button
        else:
            before, action = window._refresh_cases_button, window._assisted_create_button
        window.activateWindow()
        _application().processEvents()
        assert before.isVisible() and before.isEnabled()
        assert action.isVisible() and action.isEnabled()
        before.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(before, Qt.Key.Key_Tab)
        assert action.hasFocus()
        if surface == "active_assistance":
            QTest.keyClick(action, Qt.Key.Key_Tab)
            assert window._close_case_button.hasFocus()


def test_periodic_locked_checkpoints_do_not_revoke_an_inflight_unlock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _window() as (window, commands, worker):
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        commands.unlock_assisted_case_requested.disconnect(worker.unlock_assisted_case)
        controller = window._assisted_session
        controller.changed(_snapshot(AssistedUseStatus.LOCKED))
        controller._timer.stop()
        controller.unlock()
        dialog = controller._unlock_dialog
        assert dialog is not None
        secret = dialog.findChild(QLineEdit, "assistedUnlockPassphrase")
        assert secret is not None
        secret.setText("synthetic local case passphrase")
        dialog.accept()
        assert dialog.isVisible()
        revocations: list[bool] = []
        monkeypatch.setattr(worker, "lock_assisted_immediately", lambda: revocations.append(True))
        controller._last_input -= 3600
        for _ in range(3):
            controller.tick()
            controller.changed(_snapshot(AssistedUseStatus.LOCKED))
            controller.changed(_snapshot())
        assert revocations == []
        assert dialog.isVisible()
        _assert_pending_unlock(dialog)
        monkeypatch.setattr(worker, "current_assisted_snapshot", lambda: _snapshot())
        controller._unlocked(controller._privacy_epoch, _snapshot())
        assert not controller.locked
        assert window._application_scroll.isVisible()
        controller._timer.stop()


@pytest.mark.parametrize("reason", ("expiry", "clock_rollback"))
def test_late_unlock_success_obeys_local_deadline_and_clock_recovery(
    reason: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _window() as (window, commands, worker):
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        commands.unlock_assisted_case_requested.disconnect(worker.unlock_assisted_case)
        controller = window._assisted_session
        controller.changed(_snapshot(AssistedUseStatus.LOCKED))
        controller._timer.stop()
        controller.unlock()
        dialog = controller._unlock_dialog
        assert dialog is not None
        secret = dialog.findChild(QLineEdit, "assistedUnlockPassphrase")
        assert secret is not None
        secret.setText("synthetic local case passphrase")
        dialog.accept()
        assert dialog.isVisible()
        request_id = controller._privacy_epoch
        deadline = datetime.now(UTC) + timedelta(days=7)
        if reason == "expiry":
            deadline = datetime.now(UTC) - timedelta(seconds=1)
            controller._snapshot = AssistedUseSnapshot(
                CASE_ID, AssistedUseStatus.LOCKED, deadline, 7
            )
        else:
            controller._last_wall = datetime.now(UTC) + timedelta(minutes=1)
        late = AssistedUseSnapshot(CASE_ID, AssistedUseStatus.UNLOCKED, deadline, 7)
        monkeypatch.setattr(worker, "current_assisted_snapshot", lambda: late)
        controller._unlocked(controller._privacy_epoch, late)
        assert controller.locked
        assert not window._application_scroll.isVisible()
        assert not dialog.isVisible()
        assert controller._unlock_dialog is None
        assert secret.text() == ""
        curtain = controller._curtain
        assert curtain is not None
        screen = curtain.findChild(AssistedLockScreen)
        assert screen is not None
        unlock = screen.findChild(QPushButton, "assistedLockUnlock")
        end = screen.findChild(QPushButton, "assistedLockEnd")
        assert unlock is not None and end is not None
        assert not unlock.isEnabled() and end.isEnabled()
        controller._unlock_finished(request_id)
        if reason == "expiry":
            assert not unlock.isEnabled()
            assert screen.accessibleName() == "Assisted case expired"
        else:
            assert unlock.isEnabled()
            assert "clock review" in screen.accessibleName()
        controller.changed(None)


def test_cancel_pending_unlock_revokes_once_and_waits_for_ack_before_new_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _window() as (window, commands, worker):
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        commands.unlock_assisted_case_requested.disconnect(worker.unlock_assisted_case)
        submitted: list[tuple[int, object, str, object]] = []
        commands.unlock_assisted_case_requested.connect(
            lambda request_id, case_id, secret, cancellation: submitted.append(
                (request_id, case_id, secret, cancellation)
            )
        )
        controller = window._assisted_session
        controller.changed(_snapshot(AssistedUseStatus.LOCKED))
        controller._timer.stop()
        controller.unlock()
        old_dialog = _submit_unlock(controller)
        _assert_pending_unlock(old_dialog)
        old_id = submitted[-1][0]
        old_token = submitted[-1][3]
        assert isinstance(old_token, OperationCancellation)
        revocations: list[bool] = []
        monkeypatch.setattr(worker, "lock_assisted_immediately", lambda: revocations.append(True))
        cancel = old_dialog.findChild(QPushButton, "assistedUnlockCancel")
        assert cancel is not None
        QTest.keyClick(cancel, Qt.Key.Key_Space)
        assert old_token.cancelled()
        assert revocations
        assert _locked(controller)
        assert controller._unlock_dialog is None
        controller.unlock()
        assert controller._unlock_dialog is None
        assert len(submitted) == 1
        controller._unlock_finished(old_id)
        controller.unlock()
        new_dialog = _submit_unlock(controller)
        _assert_pending_unlock(new_dialog)
        new_id = submitted[-1][0]
        assert new_id != old_id
        revocations.clear()
        controller._unlock_failed(old_id, AssistedUseErrorCode.UNLOCK_FAILED)
        monkeypatch.setattr(worker, "current_assisted_snapshot", lambda: _snapshot())
        controller._unlocked(old_id, _snapshot())
        assert revocations == []
        _assert_pending_unlock(new_dialog)
        assert _locked(controller)
        controller._unlocked(new_id, _snapshot())
        controller._unlock_finished(new_id)
        assert not _locked(controller)
        assert window._application_scroll.isVisible()
        controller._timer.stop()


def test_foreground_loss_during_unlock_keeps_recovery_and_ignores_late_outcomes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _window() as (window, commands, worker):
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        commands.unlock_assisted_case_requested.disconnect(worker.unlock_assisted_case)
        submitted: list[tuple[int, object, str, object]] = []
        commands.unlock_assisted_case_requested.connect(
            lambda request_id, case_id, secret, cancellation: submitted.append(
                (request_id, case_id, secret, cancellation)
            )
        )
        controller = window._assisted_session
        controller.changed(_snapshot(AssistedUseStatus.LOCKED))
        controller._timer.stop()
        controller.unlock()
        _submit_unlock(controller)
        old_id = submitted[-1][0]
        token = submitted[-1][3]
        assert isinstance(token, OperationCancellation)
        application = _application()
        monkeypatch.setattr(
            type(application),
            "applicationState",
            staticmethod(lambda: Qt.ApplicationState.ApplicationInactive),
        )
        controller.eventFilter(application, QEvent(QEvent.Type.ApplicationStateChange))
        assert token.cancelled()
        assert controller._unlock_dialog is None
        assert _locked(controller)
        assert not window._application_scroll.isVisible()
        controller._unlock_failed(old_id, AssistedUseErrorCode.UNLOCK_FAILED)
        controller._unlocked(old_id, _snapshot())
        assert _locked(controller)
        assert controller._unlock_dialog is None
        controller._unlock_finished(old_id)
        controller.unlock()
        recovered = _unlock_prompt(controller)
        assert recovered is not None and recovered.isEnabled()
        recovered.reject()
        controller.changed(None)


def test_reopen_checkpoint_failure_preserves_locked_recovery_and_case_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, service, store, _repository, _lifecycle, _ingest = _harness()
    now = [datetime.now(UTC)]
    monkeypatch.setattr(service, "_clock", lambda: now[0])
    service.enroll(CASE_ID, 7, _PASSPHRASE, consent=True)
    service.release()
    with _window() as (window, commands, worker):
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        worker._backend = backend
        worker.open_case(CASE_ID)
        controller = window._assisted_session
        controller._timer.stop()
        assert controller.active and controller.locked
        now[0] += timedelta(minutes=1)
        store.fail_save = True
        worker.check_assisted_use(CASE_ID)
        assert controller.active and controller.locked
        assert not window._application_scroll.isVisible()
        assert controller._curtain is not None and controller._curtain.isVisible()
        assert backend.list_cases().cases == ()
        assert backend.state.case_id is None
        assert service.snapshot is not None
        assert service.snapshot.status is AssistedUseStatus.LOCKED
        controller.changed(None)


@pytest.mark.parametrize("delivery", ("while_locked", "after_unlock"))
def test_checklist_lock_refusal_never_survives_as_an_unlocked_recovery_error(
    delivery: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _window() as (window, commands, worker):
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        commands.unlock_assisted_case_requested.disconnect(worker.unlock_assisted_case)
        commands.checklist_begin_requested.disconnect(worker.begin_checklist)
        worker.initialized.emit()
        worker.state_changed.emit(_state())
        controller = window._assisted_session
        controller.changed(_snapshot())
        controller._timer.stop()
        window._request_checklist()
        operation_id = window._checklist_start_operation_id
        assert operation_id is not None and not window._checklist_button.isEnabled()
        controller.cover()
        failure = UiFailure(
            AssistedUseErrorCode.LOCKED.value,
            "This assisted case is locked. Unlock it to continue.",
            True,
            operation_id,
        )
        if delivery == "while_locked":
            worker.checklist_start_failed.emit(operation_id, failure)
        controller.unlock()
        _submit_unlock(controller)
        monkeypatch.setattr(worker, "current_assisted_snapshot", lambda: _snapshot())
        controller._unlocked(controller._privacy_epoch, _snapshot())
        controller._timer.stop()
        if delivery == "after_unlock":
            worker.checklist_start_failed.emit(operation_id, failure)
        assert not controller.locked
        assert window._checklist_start_operation_id is None
        assert window._checklist_button.isEnabled()
        assert not window._error_label.isVisible()
        assert AssistedUseErrorCode.LOCKED.value not in window._error_label.text()
        controller.changed(None)


@pytest.mark.parametrize("assisted,stable_locked", ((False, True), (True, False)))
def test_checklist_other_failure_presentations_are_preserved(
    assisted: bool, stable_locked: bool
) -> None:
    with _window() as (window, commands, worker):
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        worker.initialized.emit()
        worker.state_changed.emit(_state())
        if assisted:
            window._assisted_session.changed(_snapshot())
            window._assisted_session._timer.stop()
        window._checklist_start_operation_id = 73
        code = AssistedUseErrorCode.LOCKED.value if stable_locked else "CHECKLIST_STORAGE_FAILED"
        worker.checklist_start_failed.emit(
            73, UiFailure(code, "The action could not finish.", True)
        )
        assert not window._checklist_is_opening_or_active()
        assert window._error_label.isVisible()
        assert code in window._error_label.text()
        window._assisted_session.changed(None)


def test_untyped_checklist_lock_like_failure_is_still_reported() -> None:
    with _window() as (window, commands, worker):
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        worker.initialized.emit()
        worker.state_changed.emit(_state())
        window._assisted_session.changed(_snapshot())
        window._assisted_session._timer.stop()
        window._checklist_start_operation_id = 74
        worker.checklist_start_failed.emit(
            74,
            SimpleNamespace(code=AssistedUseErrorCode.LOCKED.value, message="untrusted message"),
        )
        assert not window._checklist_is_opening_or_active()
        assert window._error_label.isVisible()
        assert "PWS_UI_CHECKLIST_RESPONSE_INVALID" in window._error_label.text()
        assert "untrusted message" not in window._error_label.text()
        window._assisted_session.changed(None)


def test_queued_scheduler_check_after_close_cannot_hide_handover_or_reselect_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, service, _store, _repository, _lifecycle, _ingest = _harness()
    monkeypatch.setattr(service, "_clock", lambda: datetime.now(UTC))
    service.enroll(BACKEND_CASE_ID, 7, _PASSPHRASE, consent=True)
    backend.open_case(BACKEND_CASE_ID)
    with _window() as (window, commands, worker):
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        worker._backend = backend
        worker.state_changed.emit(backend.state)
        worker.assisted_use_changed.emit(service.snapshot)
        window._assisted_session._timer.stop()
        worker.close_case()
        _application().processEvents()
        handover = window.findChild(QDialog, "assistedHandoverDialog")
        assert handover is not None and handover.isVisible()
        assert not window._assisted_session.active
        worker.check_assisted_use(BACKEND_CASE_ID)
        _application().processEvents()
        assert not window._assisted_session.active
        assert not window._assisted_session.locked
        assert backend.state.case_id is None
        assert service.check() is None
        assert handover.isVisible()


@pytest.mark.parametrize("assisted", (False, True))
def test_pending_preview_timer_cannot_enqueue_work_behind_a_case_close_request(
    assisted: bool,
) -> None:
    with _window() as (window, commands, worker):
        commands.assisted_check_requested.disconnect(worker.check_assisted_use)
        commands.thumbnails_requested.disconnect(worker.get_thumbnails)
        commands.close_case_requested.disconnect(worker.close_case)
        events: list[str] = []
        commands.close_case_requested.connect(lambda: events.append("close"))
        commands.thumbnails_requested.connect(lambda *_args: events.append("preview"))
        worker.initialized.emit()
        state = _state()
        worker.state_changed.emit(
            replace(
                state, pages=tuple(replace(page, preview_supported=True) for page in state.pages)
            )
        )
        if assisted:
            window._assisted_session.changed(_snapshot())
            window._assisted_session._timer.stop()
        window._preview_refresh_timer.stop()
        _application().processEvents()
        assert window._active_operation is None
        assert window._current_preview_page_ids()
        # Deliver a genuinely pending timer in the event loop after the close
        # command, while the backend's empty-state acknowledgement is delayed.
        window._preview_refresh_timer.start(0)
        window._request_close_case()
        assert events == ["close"]
        QTest.qWait(5)
        _application().processEvents()
        assert events == ["close"]
        assert window._state is not None and window._state.case_id is not None
        worker.state_changed.emit(OrganizerViewState.empty())
        _application().processEvents()
        assert events == ["close"]
        window._assisted_session.changed(None)

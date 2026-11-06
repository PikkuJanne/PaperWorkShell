"""Qt event and native name regressions for PWS-0702."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent
from PySide6.QtWidgets import QLabel

from paperworkshell.ui.announcements import ScreenReaderAnnouncer
from paperworkshell.ui.backend import UiFailure, UiProgress
from paperworkshell.ui.window import _LongOperationKind
from tests.accessibility.test_accessibility_baseline import CASE_ID, _application, _state, _window


def _speech(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, object]]:
    messages: list[tuple[str, object]] = []

    def capture(event: object) -> None:
        if isinstance(event, QAccessibleAnnouncementEvent):
            messages.append((event.message(), event.politeness()))

    monkeypatch.setattr(QAccessible, "updateAccessibility", capture)
    return messages


def test_progress_is_sampled_but_current_values_and_terminal_outcomes_remain_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _application()
    messages = _speech(monkeypatch)
    target = QLabel()
    now = [0.0]
    announcer = ScreenReaderAnnouncer(target, clock=lambda: now[0])
    for completed in range(101):
        now[0] = completed / 20
        announcer.progress(
            f"Processing {completed} of 100", key="copy", completed=completed, total=100
        )
    assert 1 <= len(messages) <= 3
    assert messages[0][0] == "Processing 0 of 100"
    announcer.announce("Cancellation requested. Wait for cleanup.", assertive=True)
    announcer.announce("Cancellation requested. Wait for cleanup.", assertive=True)
    assert messages[-1] == (
        "Cancellation requested. Wait for cleanup.",
        QAccessible.AnnouncementPoliteness.Assertive,
    )
    assert sum(message.startswith("Cancellation") for message, _ in messages) == 1
    announcer.announce("Cancelled. No output was saved.")
    assert messages[-1:] == [
        ("Cancelled. No output was saved.", QAccessible.AnnouncementPoliteness.Polite)
    ]
    announcer.reset_progress()
    assert announcer.progress("New operation", key="copy", completed=0, total=100)


def test_duplicate_terminal_callbacks_and_invalid_progress_do_not_repeat_speech(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _application()
    messages = _speech(monkeypatch)
    target = QLabel()
    now = [0.0]
    announcer = ScreenReaderAnnouncer(target, clock=lambda: now[0])
    assert announcer.progress("Processing 1 of 1", key="render", completed=1, total=1)
    now[0] = 30
    assert not announcer.progress("Processing 1 of 1", key="render", completed=1, total=1)
    assert not announcer.progress("Invalid", key="render", completed=2, total=1)
    assert not announcer.progress("Invalid", key="render", completed=0, total=0)
    assert len(messages) == 1
    # A numeric callback is never substituted for the operation's verified outcome.
    announcer.announce("Verification failed. Choose another destination.", assertive=True)
    assert len(messages) == 2


def test_main_window_exposes_current_case_status_and_error_in_native_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = _speech(monkeypatch)
    with _window() as (window, _commands, worker):
        worker.initialized.emit()
        worker.state_changed.emit(_state())
        interface = QAccessible.queryAccessibleInterface(window)
        assert interface is not None
        assert str(CASE_ID) in interface.text(QAccessible.Text.Name)
        operation = window._begin_long_operation(_LongOperationKind.IMPORT)
        window._announcer = ScreenReaderAnnouncer(window._status_label, clock=lambda: 0)
        for completed in range(101):
            worker.progress_changed.emit(
                UiProgress("Import", "copying", completed, 100, True, operation.operation_id)
            )
        assert window._progress.value() == 100
        status = QAccessible.queryAccessibleInterface(window._status_label)
        assert status is not None
        assert "100 of 100" in status.text(QAccessible.Text.Name)
        assert sum(message.startswith("Import copying:") for message, _ in messages) == 1
        worker.failed.emit(
            UiFailure(
                "PWS_TEST_FAILURE",
                "Choose Import document to try again.",
                True,
                operation.operation_id,
            )
        )
        error = QAccessible.queryAccessibleInterface(window._error_label)
        assert error is not None
        assert "Choose Import document" in error.text(QAccessible.Text.Name)
        worker.operation_finished.emit(operation.operation_id)
        _application().processEvents()
        assert window._import_button.hasFocus()
        worker.state_changed.emit(type(_state())(None, None, (), False, False, False))
        assert window.accessibleName() == "PaperWorkShell guided packet builder"


def test_error_name_tracks_different_recovery_surfaces() -> None:
    with _window() as (window, _commands, worker):
        worker.failed.emit(UiFailure("PWS_TEST_FIRST", "First recovery message.", True))
        window._show_scan_review_failure(
            UiFailure("PWS_TEST_SECOND", "Update preview to try again.", True)
        )
        interface = QAccessible.queryAccessibleInterface(window._error_label)
        assert interface is not None
        assert window._error_label.text() in interface.text(QAccessible.Text.Name)
        assert "First recovery message" not in interface.text(QAccessible.Text.Name)


@pytest.mark.parametrize(
    ("handler_name", "button_name"),
    (
        ("_show_core_overlay_tools_failure", "_core_overlay_tools_button"),
        ("_show_redaction_tools_failure", "_redaction_tools_button"),
        ("_show_checklist_failure", "_checklist_button"),
        ("_show_portal_constraints_failure", "_portal_constraints_button"),
        ("_show_scan_review_failure", "_scan_review_button"),
    ),
)
def test_editor_start_error_focuses_actionable_recovery(
    handler_name: str, button_name: str
) -> None:
    with _window() as (window, _commands, worker):
        worker.initialized.emit()
        worker.state_changed.emit(_state())
        recovery = getattr(window, button_name)
        # The failure surface may be invoked before any document capability is
        # available. Exercise its available action and its disabled fallback.
        recovery.setEnabled(True)
        getattr(window, handler_name)(UiFailure("PWS_TEST_FAILURE", "Try this action again.", True))
        assert recovery.hasFocus()
        assert window._error_label.text() in window._error_label.accessibleName()
        recovery.setEnabled(False)
        getattr(window, handler_name)(UiFailure("PWS_TEST_FAILURE", "Try this action again.", True))
        assert not recovery.hasFocus()
        assert window.focusWidget() is not window._error_label

"""PWS-0701 keyboard traversal, reference discovery and dialog isolation."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QMessageLogContext, Qt, QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QAction, QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from paperworkshell.ui.checklist import ChecklistEditorDialog
from paperworkshell.ui.constraints import ConstraintEditorDialog
from paperworkshell.ui.keyboard_help import KEYBOARD_REFERENCE, KeyboardHelpDialog
from paperworkshell.ui.window import PageOrderDialog
from tests.accessibility.test_accessibility_baseline import (
    PAGE_ONE,
    PAGE_TWO,
    _application,
    _state,
    _window,
)
from tests.accessibility.test_checklist_accessibility import _empty_state as _checklist_state
from tests.accessibility.test_constraint_editor_accessibility import _state as _constraint_state


def _settle() -> None:
    _application().processEvents()


def test_programmatic_dialog_status_focus_is_revealed_inside_its_scroll_area() -> None:
    with _window() as (window, _commands, _worker):
        dialog = ConstraintEditorDialog(_constraint_state(), window)
        dialog.resize(640, 400)
        dialog.show()
        dialog.activateWindow()
        _settle()
        try:
            dialog._scroll.verticalScrollBar().setValue(0)
            dialog._status.setFocus(Qt.FocusReason.OtherFocusReason)
            _settle()
            _settle()
            assert dialog._status.hasFocus()
            viewport = dialog._scroll.viewport()
            center = dialog._status.mapTo(viewport, dialog._status.rect().center())
            assert 0 <= center.y() < viewport.height()
        finally:
            dialog.hide()
            dialog.deleteLater()
            _settle()


@pytest.mark.parametrize("surface", ("returned_button", "checklist_scroll"))
def test_non_tab_focus_uses_a_dashed_frame_on_buttons_and_scroll_containers(surface: str) -> None:
    with _window() as (window, _commands, worker):
        worker.initialized.emit()
        worker.state_changed.emit(_state())
        dialog = None
        control: QWidget
        if surface == "returned_button":
            window.activateWindow()
            control = window._core_overlay_tools_button
        else:
            dialog = ChecklistEditorDialog(_checklist_state(), window)
            dialog.show()
            dialog.activateWindow()
            _settle()
            control = dialog._scroll
        try:
            control.setFocus(Qt.FocusReason.OtherFocusReason)
            _settle()
            assert control.hasFocus()
            rendered = control.grab().toImage()
            # A repeating dash/gap pattern remains distinguishable from a solid
            # default-button border or a selected row's fill color.
            colors = [rendered.pixel(x, 1) for x in range(8, rendered.width() - 8)]
            transitions = sum(
                first != second for first, second in zip(colors, colors[1:], strict=False)
            )
            assert transitions >= 4
            other = dialog._cancel_button if dialog is not None else window._import_button
            other.setFocus()
            _settle()
            blurred = control.grab().toImage()
            assert colors != [blurred.pixel(x, 1) for x in range(8, blurred.width() - 8)]
        finally:
            if dialog is not None:
                dialog.hide()
                dialog.deleteLater()
                _settle()


@pytest.mark.parametrize("surface", ("empty_case_list", "active_pages"))
def test_list_focus_has_a_visible_frame_even_without_a_visible_selected_row(surface: str) -> None:
    with _window() as (window, _commands, worker):
        worker.initialized.emit()
        if surface == "active_pages":
            worker.state_changed.emit(_state())
            control = window._page_view
            other = window._import_button
        else:
            control = window._case_list
            other = window._create_case_button
        window.activateWindow()
        control.setFocus()
        _settle()
        focused = control.grab().toImage()
        other.setFocus()
        _settle()
        unfocused = control.grab().toImage()
        # Check the actual frame, not a selected row's focus-dependent fill color.
        edges = (
            *((x, 0) for x in range(focused.width())),
            *((x, focused.height() - 1) for x in range(focused.width())),
            *((0, y) for y in range(focused.height())),
            *((focused.width() - 1, y) for y in range(focused.height())),
        )
        assert any(focused.pixel(x, y) != unfocused.pixel(x, y) for x, y in edges)


def test_shared_status_tab_order_is_wired_after_widgets_have_the_same_window() -> None:
    messages: list[str] = []

    def capture(_kind: QtMsgType, _context: QMessageLogContext, message: str) -> None:
        if "setTabOrder" in message:
            messages.append(message)

    previous = qInstallMessageHandler(capture)
    try:
        with _window():
            assert not messages, "Qt rejected a requested keyboard focus link"
    finally:
        qInstallMessageHandler(previous)


def test_status_recovery_follows_workflow_actions_in_the_actual_tab_order() -> None:
    with _window() as (window, _commands, worker):
        worker.initialized.emit()
        worker.state_changed.emit(_state())
        window.activateWindow()
        window._error_label.setText("Synthetic keyboard recovery message")
        window._error_label.setVisible(True)
        window._cancel_button.setVisible(True)
        window._quality_check_button.setFocus()
        _settle()
        for target in (
            window._error_label,
            window._cancel_button,
            window._ocr_language_button,
            window._import_button,
            window._close_case_button,
            window._page_view,
        ):
            current = QApplication.focusWidget()
            assert current is not None
            QTest.keyClick(current, Qt.Key.Key_Tab)
            assert QApplication.focusWidget() is target


def test_f1_guide_is_discoverable_readable_and_restores_the_invoking_control() -> None:
    with _window() as (window, _commands, worker):
        worker.initialized.emit()
        worker.state_changed.emit(_state())
        window.activateWindow()
        window._rotate_button.setFocus()
        _settle()
        action = window.findChild(QAction, "keyboardHelpAction")
        assert action is not None and action.shortcut().toString() == "F1"
        assert any("Help" in item.text() for item in window.menuBar().actions())
        QTest.keyClick(window, Qt.Key.Key_F1)
        _settle()
        dialog = window._keyboard_help_dialog
        assert isinstance(dialog, KeyboardHelpDialog)
        assert QApplication.focusWidget() is dialog._reference
        for shortcut in window.findChildren(QShortcut):
            assert shortcut.key().toString() in KEYBOARD_REFERENCE
        for export_action in (
            window._package_export_action,
            window._pages_pdf_export_action,
            window._removal_export_action,
        ):
            assert export_action.shortcut().toString() in KEYBOARD_REFERENCE
        QTest.keyClick(dialog._reference, Qt.Key.Key_End, Qt.KeyboardModifier.ControlModifier)
        assert dialog._reference.textCursor().atEnd()
        assert dialog._reference.verticalScrollBar().value() > 0
        QTest.keyClicks(dialog._reference, "must not edit")
        assert dialog._reference.toPlainText() == KEYBOARD_REFERENCE
        QTest.keyClick(dialog._reference, Qt.Key.Key_Tab)
        assert QApplication.focusWidget() is dialog._close_button
        QTest.keyClick(dialog._close_button, Qt.Key.Key_Backtab)
        assert QApplication.focusWidget() is dialog._reference
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        _settle()
        assert window._keyboard_help_dialog is None
        assert QApplication.focusWidget() is window._rotate_button


def test_help_does_not_run_page_shortcuts_or_restore_a_disabled_control() -> None:
    with _window() as (window, commands, worker):
        worker.initialized.emit()
        worker.state_changed.emit(_state())
        rotations: list[object] = []
        removals: list[object] = []
        commands.rotate_requested.connect(rotations.append)
        commands.remove_requested.connect(removals.append)
        window.activateWindow()
        window._rotate_button.setFocus()
        _settle()
        QTest.keyClick(window, Qt.Key.Key_F1)
        _settle()
        dialog = window._keyboard_help_dialog
        assert dialog is not None
        QTest.keyClick(dialog._reference, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(dialog._reference, Qt.Key.Key_Delete)
        assert rotations == [] and removals == []
        # Simulate a background state change while the reference is open.
        window._rotate_button.setEnabled(False)
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        _settle()
        assert QApplication.focusWidget() is window._page_view


def test_main_window_tab_and_backtab_visit_every_available_action_without_trapping() -> None:
    with _window() as (window, _commands, worker):
        worker.initialized.emit()
        worker.state_changed.emit(_state())
        window.activateWindow()
        window._page_view.setFocus()
        _settle()
        # Walk the real focus chain, including the OCR settings above the workflow.
        seen: list[QWidget] = []
        for _ in range(60):
            focus = QApplication.focusWidget()
            assert isinstance(focus, QWidget)
            assert focus.isVisible() and focus.isEnabled()
            if focus in seen:
                assert focus is window._page_view
                break
            seen.append(focus)
            QTest.keyClick(focus, Qt.Key.Key_Tab)
        else:
            raise AssertionError("Tab did not complete the bounded main-window focus chain")
        expected = [
            widget
            for widget in window.findChildren(QWidget)
            if widget.isVisible()
            and widget.isEnabled()
            and widget.focusPolicy() & Qt.FocusPolicy.TabFocus
            and widget.window() is window
            and widget.focusProxy() is None
        ]
        # Exclude internal list scrollbars: they are reached by arrow/page keys.
        expected = [widget for widget in expected if not widget.inherits("QScrollBar")]
        assert set(expected).issubset(seen)
        for previous in reversed(seen):
            focus = QApplication.focusWidget()
            assert focus is not None
            QTest.keyClick(focus, Qt.Key.Key_Backtab)
            assert QApplication.focusWidget() is previous


def test_page_order_boundary_keeps_focus_on_list_for_continued_navigation() -> None:
    app = _application()
    dialog = PageOrderDialog(((PAGE_ONE, 1), (PAGE_TWO, 2)))
    dialog.show()
    dialog.activateWindow()
    app.processEvents()
    try:
        dialog._move_later.setFocus()
        QTest.keyClick(dialog._move_later, Qt.Key.Key_Space)
        assert dialog.page_ids == (PAGE_TWO, PAGE_ONE)
        assert QApplication.focusWidget() is dialog._page_list
        QTest.keyClick(dialog._page_list, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
        assert dialog.page_ids == (PAGE_ONE, PAGE_TWO)
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        assert not dialog.isVisible()
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()

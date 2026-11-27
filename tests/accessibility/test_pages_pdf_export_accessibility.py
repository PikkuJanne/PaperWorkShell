"""Keyboard, recovery focus, and enlarged-text checks for ordinary PDF export."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible, QAction, QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QScrollArea,
)

from paperworkshell.app.organizer import OrganizerPageView
from paperworkshell.domain.pdf_export import PdfExportSelection
from paperworkshell.ui.backend import OperationCancellation, UiFailure
from paperworkshell.ui.window import _PdfExportOptions
from tests.unit.test_package_export_window import _application, _window
from tests.unit.test_pws0811_window_journeys import _drive_modal


def test_export_shortcut_exposes_named_button_and_returns_focus_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _window() as (window, commands, worker):
        monkeypatch.setattr(window, "_ask_pdf_export_options", lambda: _PdfExportOptions(False))
        commands.pages_pdf_export_requested.disconnect(worker.export_pages_pdf)
        requests: list[tuple[int, Path, OperationCancellation]] = []
        commands.pages_pdf_export_requested.connect(
            lambda operation, destination, cancellation: requests.append(
                (operation, destination, cancellation)
            )
        )
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", lambda *_args, **_kwargs: ("synthetic.pdf", "")
        )
        button = window._pages_pdf_export_button
        interface = QAccessible.queryAccessibleInterface(button)
        assert interface is not None
        assert interface.role() is QAccessible.Role.Button
        assert interface.text(QAccessible.Text.Name) == "Export 1-page PDF…"
        assert "Ctrl+Shift+E" in interface.text(QAccessible.Text.Description)
        assert interface.state().focusable
        action = window.findChild(QAction, "pagesPdfExportMenuAction")
        assert action is not None and action.shortcut().toString() == "Ctrl+Shift+E"
        window.activateWindow()
        button.setFocus()
        _application().processEvents()
        QTest.keyClick(
            window,
            Qt.Key.Key_E,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
        )
        _application().processEvents()
        assert len(requests) == 1
        assert window._cancel_button.hasFocus()
        worker.failed.emit(
            UiFailure("PDF_EXPORT_DESTINATION_EXISTS", "Choose another name.", True, requests[0][0])
        )
        worker.operation_finished.emit(requests[0][0])
        _application().processEvents()
        assert button.hasFocus()
        assert "Choose another name" in window._error_label.text()
        assert interface.state().focused


def test_doubled_text_keeps_pdf_export_and_cancel_scroll_reachable() -> None:
    app = _application()
    original = QFont(app.font())
    enlarged = QFont(original)
    enlarged.setPointSizeF(max(24.0, original.pointSizeF() * 2.0))
    app.setFont(enlarged)
    try:
        with _window() as (window, _commands, _worker):
            window.resize(960, 540)
            app.processEvents()
            scroll = window.findChild(QScrollArea, "applicationScrollArea")
            assert scroll is not None and scroll.widgetResizable()
            assert scroll.verticalScrollBar().maximum() > 0
            button = window._pages_pdf_export_button
            scroll.ensureWidgetVisible(button)
            app.processEvents()
            assert (
                scroll.viewport()
                .rect()
                .contains(button.mapTo(scroll.viewport(), button.rect().center()))
            )
            lines = button.text().split("\n")
            assert button.height() >= len(lines) * button.fontMetrics().lineSpacing()
            assert button.width() >= max(
                button.fontMetrics().horizontalAdvance(line) for line in lines
            )
            button.setFocus()
            assert button.hasFocus()
    finally:
        app.setFont(original)


@pytest.mark.parametrize("cancel", [False, True])
def test_keyboard_range_review_validates_positions_and_dispatches_reviewed_ids(
    monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    app = _application()
    with _window() as (window, commands, worker):
        state = window._state
        assert state is not None and state.case_id is not None and state.revision is not None
        pages = tuple(OrganizerPageView(uuid4(), number, 0, False) for number in range(1, 4))
        worker.state_changed.emit(replace(state, pages=pages))
        commands.page_range_pdf_export_requested.disconnect()
        ranges: list[tuple[int, Path, OperationCancellation, PdfExportSelection, bool]] = []
        commands.page_range_pdf_export_requested.connect(lambda *args: ranges.append(args))
        destinations: list[Path] = []

        def pick_destination(*_args: object, **_kwargs: object) -> tuple[str, str]:
            path = Path("synthetic-range.pdf")
            destinations.append(path)
            return str(path), ""

        monkeypatch.setattr(QFileDialog, "getSaveFileName", pick_destination)

        def review(dialog: QDialog) -> None:
            toggle = dialog.findChild(QCheckBox, "exportPageRange")
            first = dialog.findChild(QLineEdit, "exportFirstPage")
            last = dialog.findChild(QLineEdit, "exportLastPage")
            status = dialog.findChild(QLabel, "exportRangeStatus")
            buttons = dialog.findChild(QDialogButtonBox)
            assert toggle is not None and first is not None and last is not None
            assert status is not None and buttons is not None
            choose = buttons.button(QDialogButtonBox.StandardButton.Save)
            assert first.accessibleName() == "First page to export"
            assert last.accessibleName() == "Last page to export"
            assert not toggle.isChecked() and not first.isVisible() and choose.isEnabled()

            toggle.setFocus()
            QTest.keyClick(toggle, Qt.Key.Key_Space)
            QTest.keyClick(toggle, Qt.Key.Key_Tab)
            app.processEvents()
            assert first.hasFocus()
            assert first.text() == "1" and last.text() == "3"
            labels = {label.text().replace("&", ""): label for label in dialog.findChildren(QLabel)}
            for name, field in (("First page", first), ("Last page", last)):
                label = labels[name]
                assert label.isVisible() and label.buddy() is field
                assert label.width() > 0 and label.height() >= label.fontMetrics().height()

            first.setText("4")
            assert not choose.isEnabled()
            first.setText("2")
            last.setText("1")
            assert not choose.isEnabled() and status.text() == "Enter an ordered range from 1 to 3."
            if cancel:
                QTest.keyClick(last, Qt.Key.Key_Escape)
                return

            last.setText("3")
            assert choose.isEnabled() and status.text() == "Pages 2–3 will be exported."
            choose.setFocus()
            QTest.keyClick(choose, Qt.Key.Key_Space)

        _drive_modal(window._pages_pdf_export_button.click, review)
        if cancel:
            assert not destinations and not ranges
        else:
            assert destinations == [Path("synthetic-range.pdf")]
            assert len(ranges) == 1
            operation, destination, cancellation, selection, searchable = ranges[0]
            assert operation > 0 and destination == destinations[0] and not cancellation.cancelled()
            assert selection == PdfExportSelection(
                state.case_id, state.revision, tuple(page.page_id for page in pages[1:])
            )
            assert not searchable

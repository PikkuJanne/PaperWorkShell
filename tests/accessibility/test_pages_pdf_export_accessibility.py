"""Keyboard, recovery focus, and enlarged-text checks for ordinary PDF export."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible, QAction, QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QScrollArea

from paperworkshell.ui.backend import OperationCancellation, UiFailure
from tests.unit.test_package_export_window import _application, _window


def test_export_shortcut_exposes_named_button_and_returns_focus_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _window() as (window, commands, worker):
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
        assert interface.text(QAccessible.Text.Name) == "Export pages as PDF"
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

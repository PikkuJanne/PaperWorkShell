"""Keyboard consent, accessible inventory and 200-percent review reflow."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible, QFont
from PySide6.QtTest import QTest

from paperworkshell.app.diagnostic_bundle import DiagnosticBundleService
from paperworkshell.diagnostics.logging import SanitizedDiagnosticLogger
from paperworkshell.ui.diagnostic_bundle import DiagnosticBundleDialog
from tests.unit.test_diagnostic_bundle import Writer
from tests.unit.test_diagnostic_bundle_dialog import app


def test_keyboard_review_consent_and_cancel_are_accessible_without_saving() -> None:
    application = app()
    writer = Writer()
    dialog = DiagnosticBundleDialog(DiagnosticBundleService(SanitizedDiagnosticLogger(), writer))
    dialog.show()
    dialog.activateWindow()
    application.processEvents()
    try:
        dialog._inventory.setFocus()
        interface = QAccessible.queryAccessibleInterface(dialog._inventory)
        assert interface is not None and interface.text(QAccessible.Text.Name)
        QTest.keyClick(dialog._inventory, Qt.Key.Key_Tab)
        assert dialog._consent.hasFocus()
        QTest.keyClick(dialog._consent, Qt.Key.Key_Space)
        assert dialog._consent.isChecked()
        QTest.keyClick(dialog._consent, Qt.Key.Key_Tab)
        assert dialog._save.hasFocus()
        assert not dialog._save.isDefault()
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        assert not dialog.isVisible() and writer.writes == []
    finally:
        dialog.deleteLater()
        application.processEvents()


def test_large_text_controls_reflow_and_focus_reveals_save_and_close() -> None:
    application = app()
    dialog = DiagnosticBundleDialog(DiagnosticBundleService(SanitizedDiagnosticLogger(), Writer()))
    font = QFont(dialog.font())
    font.setPointSizeF(font.pointSizeF() * 2)
    dialog.setFont(font)
    dialog.resize(640, 480)
    dialog.show()
    dialog.activateWindow()
    application.processEvents()
    try:
        dialog._consent.setChecked(True)
        for control in (dialog._inventory, dialog._consent, dialog._save, dialog._cancel):
            assert control.accessibleName()
            control.setFocus()
            application.processEvents()
            application.processEvents()
            point = control.mapTo(dialog._scroll.viewport(), control.rect().center())
            assert 0 <= point.y() < dialog._scroll.viewport().height(), control.objectName()
        assert dialog._scroll.horizontalScrollBar().maximum() == 0
    finally:
        dialog.close()
        dialog.deleteLater()
        application.processEvents()

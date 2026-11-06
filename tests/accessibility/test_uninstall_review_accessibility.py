"""Safe keyboard defaults, explicit consent and scalable uninstall review."""

from __future__ import annotations

import os
from time import monotonic
from typing import cast

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible, QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QPushButton

from paperworkshell.app.uninstall_review import UninstallReviewService
from paperworkshell.ui.uninstall_review import UninstallReviewDialog
from tests.unit.test_uninstall_review import Store


def _app() -> QApplication:
    current = QApplication.instance()
    return QApplication([]) if current is None else cast(QApplication, current)


def _idle(app: QApplication, dialog: UninstallReviewDialog) -> None:
    end = monotonic() + 10
    while dialog._thread is not None and monotonic() < end:
        app.processEvents()
        QTest.qWait(1)
    assert dialog._thread is None
    app.processEvents()


def test_default_enter_preserves_without_storage_access() -> None:
    app = _app()
    store = Store()
    dialog = UninstallReviewDialog(UninstallReviewService(store))
    dialog.show()
    app.processEvents()
    try:
        keep = dialog.findChild(QPushButton, "uninstallContinueButton")
        choice = dialog.findChild(QCheckBox, "uninstallDeleteChoice")
        remove = dialog.findChild(QPushButton, "uninstallDeleteButton")
        assert keep is not None and choice is not None and remove is not None
        assert dialog.focusWidget() is keep and keep.isDefault()
        assert not choice.isChecked() and not remove.isEnabled() and not remove.isDefault()
        QTest.keyClick(keep, Qt.Key.Key_Return)
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert store.inspections == 0 and store.calls == []
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_review_separate_confirmation_and_final_keyboard_action() -> None:
    app = _app()
    store = Store(count=1)
    dialog = UninstallReviewDialog(UninstallReviewService(store))
    dialog.show()
    app.processEvents()
    try:
        choice = dialog.findChild(QCheckBox, "uninstallDeleteChoice")
        inspect = dialog.findChild(QPushButton, "uninstallInspectButton")
        confirm = dialog.findChild(QCheckBox, "uninstallDeletionConfirmation")
        remove = dialog.findChild(QPushButton, "uninstallDeleteButton")
        keep = dialog.findChild(QPushButton, "uninstallContinueButton")
        assert choice is not None and inspect is not None and confirm is not None
        assert remove is not None and keep is not None
        choice.setFocus()
        QTest.keyClick(choice, Qt.Key.Key_Space)
        QTest.keyClick(choice, Qt.Key.Key_Tab)
        assert inspect.hasFocus()
        QTest.keyClick(inspect, Qt.Key.Key_Space)
        _idle(app, dialog)
        assert store.inspections == 1 and store.calls == []
        assert confirm.isEnabled() and not confirm.isChecked() and not remove.isEnabled()
        confirm.setFocus()
        QTest.keyClick(confirm, Qt.Key.Key_Space)
        assert remove.isEnabled() and dialog.isVisible()
        QTest.keyClick(confirm, Qt.Key.Key_Tab)
        assert remove.hasFocus()
        QTest.keyClick(remove, Qt.Key.Key_Space)
        _idle(app, dialog)
        assert len(store.calls) == 1
        assert dialog.isVisible() and keep.hasFocus()
        assert not remove.isEnabled() and not choice.isEnabled()
        QTest.keyClick(keep, Qt.Key.Key_Return)
        assert dialog.result() == QDialog.DialogCode.Accepted
    finally:
        _idle(app, dialog)
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("scale", [1, 2])
def test_controls_have_accessible_names_and_remain_reachable_when_enlarged(scale: int) -> None:
    app = _app()
    dialog = UninstallReviewDialog(UninstallReviewService(Store()))
    font = QFont(dialog.font())
    font.setPointSizeF(font.pointSizeF() * scale)
    dialog.setFont(font)
    dialog.show()
    app.processEvents()
    try:
        for control in dialog.findChildren(QPushButton) + dialog.findChildren(QCheckBox):
            interface = QAccessible.queryAccessibleInterface(control)
            assert interface is not None and interface.text(QAccessible.Text.Name)
            assert control.focusPolicy() != Qt.FocusPolicy.NoFocus
        keep = dialog.findChild(QPushButton, "uninstallContinueButton")
        cancel = dialog.findChild(QPushButton, "uninstallCancelButton")
        assert keep is not None and cancel is not None
        assert dialog.rect().contains(keep.geometry()) and dialog.rect().contains(cancel.geometry())
    finally:
        dialog.deleteLater()
        app.processEvents()

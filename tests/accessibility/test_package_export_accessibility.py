from __future__ import annotations

import os
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QWidget,
)

from paperworkshell.domain.package_summary import PackageSummary, PackageSummaryStatus
from paperworkshell.preflight.portal_constraints import PortalOutputMode
from paperworkshell.ui.package_export import PackageExportDialog


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def _widget[T: QWidget](dialog: PackageExportDialog, kind: type[T], name: str) -> T:
    result = dialog.findChild(kind, name)
    assert result is not None
    return result


def test_safe_default_and_keyboard_path_reach_review_acknowledgement_and_export() -> None:
    app = _application()
    dialog = PackageExportDialog(tuple(PortalOutputMode))
    dialog.show_review(
        PackageSummary(PackageSummaryStatus.PLANNED),
        export_allowed=True,
        human_review_required=True,
    )
    dialog.show()
    app.processEvents()
    try:
        close = _widget(dialog, QPushButton, "packageExportCloseButton")
        scroll = _widget(dialog, QScrollArea, "packageExportScrollArea")
        assert dialog.focusWidget() is close
        assert close.isDefault()
        QTest.keyClick(close, Qt.Key.Key_Tab)
        assert dialog.focusWidget() is scroll
        QTest.keyClick(scroll, Qt.Key.Key_Tab)
        assert dialog.focusWidget() is _widget(dialog, QComboBox, "packageExportModeCombo")
        summary = _widget(dialog, QPlainTextEdit, "packageExportSummaryText")
        summary.setFocus()
        QTest.keyClick(summary, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        assert summary.textCursor().selectedText()
        QTest.keyClick(summary, Qt.Key.Key_Tab)
        acknowledgement = _widget(dialog, QCheckBox, "packageExportHumanReviewCheck")
        assert dialog.focusWidget() is acknowledgement
        QTest.keyClick(acknowledgement, Qt.Key.Key_Space)
        start = _widget(dialog, QPushButton, "packageExportStartButton")
        assert start.isEnabled()
        assert not start.isDefault()
    finally:
        dialog.hide()
        dialog.deleteLater()
        app.processEvents()


def test_labels_roles_descriptions_and_plain_text_are_available_without_color() -> None:
    app = _application()
    dialog = PackageExportDialog(tuple(PortalOutputMode))
    try:
        for name in (
            "packageExportModeCombo",
            "packageExportRefreshButton",
            "packageExportSummaryText",
            "packageExportHumanReviewCheck",
            "packageExportUnsignedDerivativeCheck",
            "packageExportStatus",
            "packageExportStartButton",
            "packageExportSaveSummaryButton",
            "packageExportCancelButton",
            "packageExportCloseButton",
            "packageExportScrollArea",
        ):
            control = dialog.findChild(QWidget, name)
            assert control is not None
            assert control.accessibleName()
            assert control.accessibleDescription()
        for label in dialog.findChildren(QLabel):
            if "&" in label.text():
                assert label.buddy() is not None
        status = _widget(dialog, QLabel, "packageExportStatus")
        assert status.textFormat() is Qt.TextFormat.PlainText
        assert status.focusPolicy() is Qt.FocusPolicy.StrongFocus
        assert "focus" in status.styleSheet()
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_two_hundred_percent_text_keeps_close_visible_and_review_controls_scrollable() -> None:
    app = _application()
    original = QFont(app.font())
    enlarged = QFont(original)
    enlarged.setPointSizeF(max(24.0, original.pointSizeF() * 2.0))
    app.setFont(enlarged)
    dialog = PackageExportDialog(tuple(PortalOutputMode))
    dialog.show_review(
        PackageSummary(PackageSummaryStatus.PLANNED),
        export_allowed=True,
        human_review_required=True,
        signed_source_confirmation_required=True,
    )
    dialog.resize(520, 360)
    dialog.show()
    app.processEvents()
    try:
        close = _widget(dialog, QPushButton, "packageExportCloseButton")
        scroll = _widget(dialog, QScrollArea, "packageExportScrollArea")
        assert close.isVisible()
        assert dialog.rect().contains(close.mapTo(dialog, close.rect().center()))
        assert scroll.widgetResizable()
        assert scroll.verticalScrollBar().maximum() > 0
        assert scroll.horizontalScrollBar().maximum() == 0
        start = _widget(dialog, QPushButton, "packageExportStartButton")
        scroll.ensureWidgetVisible(start)
        app.processEvents()
        assert (
            scroll.viewport().rect().contains(start.mapTo(scroll.viewport(), start.rect().center()))
        )
    finally:
        dialog.hide()
        dialog.deleteLater()
        app.setFont(original)
        app.processEvents()

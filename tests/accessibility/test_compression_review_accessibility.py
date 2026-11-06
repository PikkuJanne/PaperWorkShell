from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QWidget,
)

from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.ui.compression_review import CompressionReviewDialog
from tests.helpers.compression_review import compression_review
from tests.unit.test_compression_review_dialog import (
    application,
    dialog_context,
    load_pair,
    press,
    widget,
)


def test_keyboard_path_has_safe_close_default_and_explicit_preview_review_and_export() -> None:
    with dialog_context() as dialog:
        close = widget(dialog, QPushButton, "compressionReviewCloseButton")
        assert dialog.focusWidget() is close
        assert close.isDefault()
        QTest.keyClick(close, Qt.Key.Key_Tab)
        assert dialog.focusWidget() is widget(dialog, QScrollArea, "compressionReviewScrollArea")
        QTest.keyClick(dialog.focusWidget(), Qt.Key.Key_Tab)
        assert dialog.focusWidget() is widget(dialog, QCheckBox, "compressionReviewGrayscaleCheck")
        dialog.show_review(compression_review())
        summary = widget(dialog, QPlainTextEdit, "compressionReviewSummaryText")
        assert dialog.focusWidget() is summary
        QTest.keyClick(summary, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        assert summary.textCursor().selectedText()
        QTest.keyClick(summary, Qt.Key.Key_Tab)
        pages = widget(dialog, QComboBox, "compressionReviewPageCombo")
        assert dialog.focusWidget() is pages
        QTest.keyClick(pages, Qt.Key.Key_Down)
        assert pages.currentIndex() == 1
        load_pair(dialog)
        before = widget(dialog, QRadioButton, "previewComparisonBeforeChoice")
        before.setFocus()
        QTest.keyClick(before, Qt.Key.Key_Space)
        assert widget(dialog, QWidget, "previewComparisonBeforeImage").isVisible()
        assert not widget(dialog, QWidget, "previewComparisonProposedImage").isVisible()
        proposed = widget(dialog, QRadioButton, "previewComparisonProposedChoice")
        proposed.setFocus()
        QTest.keyClick(proposed, Qt.Key.Key_Space)
        assert widget(dialog, QWidget, "previewComparisonProposedImage").isVisible()
        ack = widget(dialog, QCheckBox, "compressionReviewAcknowledgementCheck")
        ack.setFocus()
        QTest.keyClick(ack, Qt.Key.Key_Space)
        start = widget(dialog, QPushButton, "compressionReviewExportButton")
        assert start.isEnabled() and not start.isDefault()
        exported: list[object] = []
        dialog.export_requested.connect(exported.append)
        press(start)
        assert len(exported) == 1


def test_controls_have_names_descriptions_buddies_and_visible_recovery_focus() -> None:
    with dialog_context() as dialog:
        for control in dialog.findChildren(QWidget):
            if control.objectName().startswith(("compressionReview", "previewComparison")) and (
                isinstance(
                    control,
                    (QCheckBox, QComboBox, QPushButton, QRadioButton, QScrollArea, QPlainTextEdit),
                )
                or control.focusPolicy() is Qt.FocusPolicy.StrongFocus
            ):
                assert control.accessibleName(), control.objectName()
                assert control.accessibleDescription(), control.objectName()
        for label in dialog.findChildren(QLabel):
            if "&" in label.text():
                assert label.buddy() is not None
            assert label.textFormat() is Qt.TextFormat.PlainText or label.buddy() is not None
        status = widget(dialog, QLabel, "compressionReviewStatus")
        assert status.focusPolicy() is Qt.FocusPolicy.StrongFocus
        assert "focus" in status.styleSheet()
        dialog.set_busy("preparing")
        cancel = widget(dialog, QPushButton, "compressionReviewCancelButton")
        assert dialog.focusWidget() is cancel
        QTest.keyClick(cancel, Qt.Key.Key_Space)
        assert "Cancellation requested" in status.text()
        assert not cancel.isEnabled()
        dialog.show_failure()
        assert dialog.focusWidget() is widget(dialog, QPushButton, "compressionReviewPrepareButton")
        assert widget(dialog, QPushButton, "compressionReviewPrepareButton").isEnabled()


def test_two_hundred_percent_text_and_small_window_keep_controls_scrollable_and_close_visible() -> (
    None
):
    app = application()
    original = QFont(app.font())
    enlarged = QFont(original)
    enlarged.setPointSizeF(max(24.0, original.pointSizeF() * 2.0))
    app.setFont(enlarged)
    dialog = CompressionReviewDialog(CONSTRAINED_RESOURCE_POLICY_V1)
    dialog.show_review(compression_review(grayscale=True, floor=True))
    dialog.resize(520, 360)
    dialog.show()
    app.processEvents()
    try:
        close = widget(dialog, QPushButton, "compressionReviewCloseButton")
        scroll = widget(dialog, QScrollArea, "compressionReviewScrollArea")
        assert close.isVisible()
        assert dialog.rect().contains(close.mapTo(dialog, close.rect().center()))
        assert scroll.widgetResizable()
        assert scroll.verticalScrollBar().maximum() > 0
        assert scroll.horizontalScrollBar().maximum() == 0
        for name in (
            "compressionReviewGrayscaleCheck",
            "compressionReviewPrepareButton",
            "compressionReviewSummaryText",
            "compressionReviewPageCombo",
            "compressionReviewShowPageButton",
            "previewComparisonBeforeImage",
            "previewComparisonProposedImage",
            "compressionReviewAcknowledgementCheck",
            "compressionReviewExportButton",
            "compressionReviewStatus",
        ):
            control = widget(dialog, QWidget, name)
            content = scroll.widget()
            assert content is not None
            center = control.mapTo(content, control.rect().center())
            scroll.ensureVisible(center.x(), center.y(), 8, 8)
            app.processEvents()
            assert (
                scroll.viewport()
                .rect()
                .contains(control.mapTo(scroll.viewport(), control.rect().center()))
            ), name
        dialog.set_busy("preview")
        app.processEvents()
        cancel = widget(dialog, QPushButton, "compressionReviewCancelButton")
        assert dialog.rect().contains(cancel.mapTo(dialog, cancel.rect().center()))
        assert scroll.horizontalScrollBar().maximum() == 0
    finally:
        dialog.finish_operation()
        dialog.reject()
        dialog.deleteLater()
        app.setFont(original)
        app.processEvents()

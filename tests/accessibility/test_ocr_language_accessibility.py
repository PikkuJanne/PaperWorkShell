"""PWS-0205 keyboard, focus, naming, and enlarged-text checks."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QLabel,
    QPushButton,
    QScrollArea,
    QWidget,
)

from paperworkshell.app.ocr_languages import (
    OcrLanguageDiscoveryResult,
    OcrLanguageManager,
    OcrLanguageRuntimeStatus,
    OcrLanguageSnapshot,
)
from paperworkshell.ui.ocr_languages import OcrLanguageDialog


class _Discovery:
    def discover_ocr_languages(self) -> OcrLanguageDiscoveryResult:
        return OcrLanguageDiscoveryResult(
            OcrLanguageRuntimeStatus.AVAILABLE,
            ("eng", "deu"),
        )


def _snapshot() -> OcrLanguageSnapshot:
    return OcrLanguageManager(_Discovery()).refresh()


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


@contextmanager
def _dialog() -> Iterator[OcrLanguageDialog]:
    app = _application()
    identifiers = iter((101, 102))
    dialog = OcrLanguageDialog(lambda: next(identifiers))
    dialog.show()
    app.processEvents()
    try:
        yield dialog
    finally:
        dialog.discard_for_shutdown()
        dialog.deleteLater()
        app.processEvents()


def _widget(dialog: OcrLanguageDialog, widget_type: type[QWidget], name: str) -> QWidget:
    widget = dialog.findChild(widget_type, name)
    assert widget is not None
    return widget


def test_dialog_controls_have_names_descriptions_and_safe_initial_focus() -> None:
    with _dialog() as dialog:
        assert dialog.accessibleName() == "Manage OCR languages"
        assert "does not download" in dialog.accessibleDescription()
        for widget_type, name in (
            (QCheckBox, "ocrLanguageChoiceENG"),
            (QCheckBox, "ocrLanguageChoiceDEU"),
            (QPushButton, "ocrLanguageRefreshButton"),
            (QPushButton, "ocrLanguageSaveButton"),
            (QPushButton, "ocrLanguageCancelButton"),
        ):
            widget = _widget(dialog, widget_type, name)
            assert widget.accessibleName()
            assert widget.accessibleDescription()
        cancel = _widget(dialog, QPushButton, "ocrLanguageCancelButton")
        assert cancel.hasFocus()


def test_language_choices_are_keyboard_operable_with_visible_state() -> None:
    with _dialog() as dialog:
        dialog.request_refresh()
        assert dialog.set_snapshot(101, _snapshot())
        german = cast(
            QCheckBox,
            _widget(dialog, QCheckBox, "ocrLanguageChoiceDEU"),
        )
        save = _widget(dialog, QPushButton, "ocrLanguageSaveButton")

        german.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(german, Qt.Key.Key_Space)

        assert german.hasFocus()
        assert german.isChecked()
        assert save.isEnabled()
        assert "Selected" in german.accessibleDescription()


def test_missing_selection_moves_focus_to_plain_language_recovery() -> None:
    manager = OcrLanguageManager(_Discovery())
    manager.refresh()
    manager.select_language_codes(("eng", "deu"))

    class _EnglishOnly:
        def discover_ocr_languages(self) -> OcrLanguageDiscoveryResult:
            return OcrLanguageDiscoveryResult(
                OcrLanguageRuntimeStatus.AVAILABLE,
                ("eng",),
            )

    manager._discovery = _EnglishOnly()
    missing = manager.refresh()
    with _dialog() as dialog:
        dialog.request_refresh()
        assert dialog.set_snapshot(101, missing)
        _application().processEvents()
        warning = _widget(dialog, QLabel, "ocrLanguageSelectionWarning")

        assert warning.hasFocus()
        assert "not installed and verified" in cast(QLabel, warning).text()
        assert warning.focusPolicy() is Qt.FocusPolicy.StrongFocus

        QTest.keyClick(warning, Qt.Key.Key_Tab)
        scroll = _widget(dialog, QScrollArea, "ocrLanguageScrollArea")
        assert scroll.hasFocus()
        QTest.keyClick(scroll, Qt.Key.Key_Tab)
        refresh = _widget(dialog, QPushButton, "ocrLanguageRefreshButton")
        assert refresh.hasFocus()
        QTest.keyClick(
            refresh,
            Qt.Key.Key_Backtab,
            Qt.KeyboardModifier.ShiftModifier,
        )
        assert scroll.hasFocus()
        QTest.keyClick(
            scroll,
            Qt.Key.Key_Backtab,
            Qt.KeyboardModifier.ShiftModifier,
        )
        assert warning.hasFocus()


def test_visible_error_is_in_tab_order_before_fixed_actions() -> None:
    with _dialog() as dialog:
        dialog.request_refresh()
        assert dialog.set_failure(101, "OCR_INTERNAL_ERROR", "Try Refresh again.")
        _application().processEvents()
        error = _widget(dialog, QLabel, "ocrLanguageError")
        scroll = _widget(dialog, QScrollArea, "ocrLanguageScrollArea")
        refresh = _widget(dialog, QPushButton, "ocrLanguageRefreshButton")
        assert error.hasFocus()

        QTest.keyClick(error, Qt.Key.Key_Tab)
        assert scroll.hasFocus()
        QTest.keyClick(scroll, Qt.Key.Key_Tab)
        assert refresh.hasFocus()
        QTest.keyClick(
            refresh,
            Qt.Key.Key_Backtab,
            Qt.KeyboardModifier.ShiftModifier,
        )
        QTest.keyClick(
            scroll,
            Qt.Key.Key_Backtab,
            Qt.KeyboardModifier.ShiftModifier,
        )
        assert error.hasFocus()


def test_enlarged_text_keeps_content_scrollable_and_actions_visible() -> None:
    with _dialog() as dialog:
        font = QFont(dialog.font())
        font.setPointSize(max(24, font.pointSize() * 2))
        dialog.setFont(font)
        dialog.resize(360, 280)
        dialog.request_refresh()
        assert dialog.set_snapshot(101, _snapshot())
        _application().processEvents()
        scroll = cast(
            QScrollArea,
            _widget(dialog, QScrollArea, "ocrLanguageScrollArea"),
        )

        assert scroll.verticalScrollBar().maximum() > 0
        scroll.verticalScrollBar().setValue(0)
        scroll.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(scroll, Qt.Key.Key_PageDown)
        _application().processEvents()
        assert scroll.verticalScrollBar().value() > 0
        for name in (
            "ocrLanguageRefreshButton",
            "ocrLanguageSaveButton",
            "ocrLanguageCancelButton",
        ):
            button = _widget(dialog, QPushButton, name)
            assert button.isVisible()
            top_left = button.mapTo(dialog, QPoint(0, 0))
            bottom_right = button.mapTo(dialog, button.rect().bottomRight())
            assert dialog.rect().contains(top_left)
            assert dialog.rect().contains(bottom_right)

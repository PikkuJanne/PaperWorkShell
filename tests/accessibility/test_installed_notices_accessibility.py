"""Bounded plain-text reading, visible keyboard controls and scaled locales."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QAccessible, QScreen
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox, QPlainTextEdit, QPushButton

from paperworkshell.app.installed_notices import NOTICE_PAGE_CHARACTERS
from paperworkshell.ui.notices import NoticesDialog
from tests.accessibility.test_localization_layout import _display_locale, _settle
from tests.unit.test_installed_notices import notice_catalog
from tests.unit.test_notices_dialog import _app


def test_readonly_semantics_tab_exit_paging_and_escape(tmp_path: Path) -> None:
    app = _app()
    dialog = NoticesDialog(notice_catalog(tmp_path, "x" * (NOTICE_PAGE_CHARACTERS + 30)))
    dialog.show()
    dialog.activateWindow()
    app.processEvents()
    try:
        view = dialog.findChild(QPlainTextEdit, "noticeText")
        next_page = dialog.findChild(QPushButton, "noticeNext")
        selector = dialog.findChild(QComboBox, "noticeFile")
        assert view is not None and next_page is not None and selector is not None
        interface = QAccessible.queryAccessibleInterface(view)
        assert interface is not None and interface.state().readOnly
        assert view.accessibleName() and selector.accessibleName() and not selector.isEditable()
        view.setFocus()
        QTest.keyClick(view, Qt.Key.Key_Tab)
        assert next_page.hasFocus()
        QTest.keyClick(next_page, Qt.Key.Key_Space)
        assert dialog._page == 1 and len(view.toPlainText()) == 30
        assert not next_page.isEnabled() and dialog._previous.isEnabled()
        dialog._close.setFocus()
        QTest.keyClick(dialog._close, Qt.Key.Key_Space)
        assert not dialog.isVisible()
    finally:
        dialog.deleteLater()
        app.processEvents()


class SmallScreen:
    def availableGeometry(self) -> QRect:  # noqa: N802
        return QRect(0, 0, 640, 480)


@pytest.mark.parametrize("locale", ["en", "en-XA", "ar-XB"])
def test_small_screen_and_large_text_keep_close_reachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, locale: str
) -> None:
    monkeypatch.setattr(NoticesDialog, "screen", lambda _self: cast(QScreen, SmallScreen()))
    catalog = notice_catalog(tmp_path)
    with _display_locale(locale, 18):
        dialog = NoticesDialog(catalog)
        dialog.show()
        _settle()
        try:
            assert dialog.width() <= 640 and dialog.height() <= 480
            close = dialog.findChild(QPushButton, "noticeClose")
            assert close is not None and close.isVisible() and close.isEnabled()
            assert dialog.rect().contains(close.geometry())
            close.setFocus()
            QTest.keyClick(close, Qt.Key.Key_Space)
            assert not dialog.isVisible()
        finally:
            dialog.deleteLater()
            _app().processEvents()

"""Trusted-text notice selection, keyboard search, errors and isolated entry."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QPlainTextEdit

from paperworkshell.app.installed_notices import NOTICE_PAGE_CHARACTERS
from paperworkshell.ui import notices
from tests.unit.test_installed_notices import notice_catalog


def _app() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


def test_selectable_plain_page_search_reaches_later_text_without_default_close(
    tmp_path: Path,
) -> None:
    app = _app()
    text = (
        "<b>literal synthetic text</b>\n" + "a" * NOTICE_PAGE_CHARACTERS + "\n😀 target-licence\n"
    )
    dialog = notices.NoticesDialog(notice_catalog(tmp_path, text))
    dialog.show()
    app.processEvents()
    try:
        view = dialog.findChild(QPlainTextEdit, "noticeText")
        query = dialog.findChild(QLineEdit, "noticeFind")
        assert view is not None and query is not None
        assert view.isReadOnly() and "<b>literal" in view.toPlainText()
        assert len(view.toPlainText()) <= NOTICE_PAGE_CHARACTERS
        query.setText("target-licence")
        query.setFocus()
        QTest.keyClick(query, Qt.Key.Key_Return)
        app.processEvents()
        assert dialog.isVisible() and dialog._page == 1
        assert view.textCursor().selectedText() == "target-licence"
        assert view.hasFocus()
        assert "Page 2 of 2" in dialog._status.text()
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_selector_opens_local_first_party_and_recipient_texts(tmp_path: Path) -> None:
    app = _app()
    dialog = notices.NoticesDialog(notice_catalog(tmp_path))
    try:
        selector = dialog.findChild(QComboBox, "noticeFile")
        assert selector is not None and not selector.isEditable()
        for label, expected in (
            ("PaperWorkShell-MIT-LICENSE.txt", "first-party"),
            ("Microsoft-runtime-recipient-terms.txt", "recipient"),
        ):
            selector.setCurrentIndex(selector.findText(label))
            assert expected in dialog._view.toPlainText()
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_search_crossing_page_boundary_explains_the_continuation(tmp_path: Path) -> None:
    app = _app()
    text = "x" * (NOTICE_PAGE_CHARACTERS - 3) + "boundary-match"
    dialog = notices.NoticesDialog(notice_catalog(tmp_path, text))
    dialog.show()
    app.processEvents()
    try:
        dialog._find.setText("boundary-match")
        dialog._search()
        assert dialog._view.textCursor().selectedText() == "bou"
        assert "continues on the next page" in dialog._status.text()
        dialog._next.click()
        assert dialog._view.toPlainText() == "ndary-match"
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_resource_failure_is_visible_path_free_and_close_stays_available(tmp_path: Path) -> None:
    app = _app()
    catalog = notice_catalog(tmp_path)
    (catalog.root / "COMPONENTS.txt").write_bytes(b"PRIVATE_TOKEN changed")
    dialog = notices.NoticesDialog(catalog)
    try:
        assert dialog.failed and "INSTALLED_NOTICES_UNAVAILABLE" in dialog._view.toPlainText()
        assert (
            "PRIVATE_TOKEN" not in dialog._view.toPlainText()
            and str(tmp_path) not in dialog._view.toPlainText()
        )
        assert dialog._close.isEnabled() and not dialog._find_next.isEnabled()
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_entry_rejects_all_options_before_catalog_or_guard(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden() -> None:
        raise AssertionError("must not touch installed or case state")

    monkeypatch.setattr(notices, "current_notice_catalog", forbidden)
    monkeypatch.setattr(notices, "uninstall_application_guard", forbidden)
    assert notices.main(["PRIVATE_TOKEN.pdf"]) == 2
    error = capsys.readouterr().err
    assert "NOTICES_ARGUMENT_INVALID" in error and "PRIVATE_TOKEN" not in error


def test_busy_lifecycle_message_offers_close_and_retry_without_repair() -> None:
    app = _app()
    dialog = notices.NoticesDialog(None, busy=True)
    try:
        text = dialog._view.toPlainText()
        assert "NOTICES_APPLICATION_BUSY" in text and "Close PaperWorkShell" in text
        assert "Repair" not in text and dialog.failed and dialog._close.isEnabled()
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_main_uses_normal_application_loop_with_only_substituted_resource_and_mutex(
    tmp_path: Path,
) -> None:
    script = r"""
import json, os, sys
from contextlib import contextmanager
from types import SimpleNamespace
os.environ['QT_QPA_PLATFORM']='offscreen'
sys.path.insert(0,sys.argv[1])
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from paperworkshell.ui import notices as n
from paperworkshell.app.installed_notices import NoticeCatalog,NoticeFile,NoticeText
state={'held':False,'exec':0,'reads':0}
@contextmanager
def guard():
 state['held']=True
 try:yield
 finally:state['held']=False
def catalog():
 assert state['held'];state['reads']+=1
 return NoticeCatalog('0.0.0','synthetic',(NoticeFile('COMPONENTS.txt',10,()),),__import__('pathlib').Path('/unused'))
class Application(QApplication):
 def exec(self):
  assert state['held'];state['exec']+=1;return super().exec()
Original=n.NoticesDialog
class Dialog(Original):
 def __init__(self,value,**kwargs):
  assert state['held'];super().__init__(value,**kwargs);QTimer.singleShot(0,self.accept)
n.os=SimpleNamespace(name='nt');n.QApplication=Application;n.NoticesDialog=Dialog
n.uninstall_application_guard=guard;n.current_notice_catalog=catalog
n.read_notice=lambda *_:NoticeText('Synthetic fixed notice')
assert n.main([])==0
assert state=={'held':False,'exec':1,'reads':1}
print(json.dumps(state))
"""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            script,
            str(Path(__file__).resolve().parents[2] / "src"),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip()) == {"held": False, "exec": 1, "reads": 1}

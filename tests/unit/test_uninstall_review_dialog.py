"""Asynchronous refusal, cancellation and safe entrypoint behavior."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from threading import Event
from time import monotonic
from typing import cast

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QPlainTextEdit, QPushButton

from paperworkshell.app.uninstall_review import UninstallReviewService
from paperworkshell.domain.uninstall_review import UninstallCaseInventory
from paperworkshell.ui.uninstall_review import UninstallReviewDialog, main
from tests.unit.test_uninstall_review import Store


def _app() -> QApplication:
    current = QApplication.instance()
    return QApplication([]) if current is None else cast(QApplication, current)


def _wait(app: QApplication, condition: Callable[[], bool]) -> None:
    end = monotonic() + 10
    while not condition() and monotonic() < end:
        app.processEvents()
        QTest.qWait(1)
    assert condition()
    app.processEvents()


def _inspect(app: QApplication, dialog: UninstallReviewDialog) -> None:
    choose = dialog.findChild(QCheckBox, "uninstallDeleteChoice")
    inspect = dialog.findChild(QPushButton, "uninstallInspectButton")
    assert choose is not None and inspect is not None
    choose.setChecked(True)
    inspect.click()
    _wait(app, lambda: dialog._thread is None)


def test_failed_inspection_shows_safe_message_and_preserves_default() -> None:
    app = _app()
    store = Store()
    store.failure = OSError("PRIVATE_TOKEN synthetic-private.bin")
    dialog = UninstallReviewDialog(UninstallReviewService(store))
    dialog.show()
    app.processEvents()
    try:
        _inspect(app, dialog)
        inventory = dialog.findChild(QPlainTextEdit, "uninstallCaseInventory")
        keep = dialog.findChild(QPushButton, "uninstallContinueButton")
        confirm = dialog.findChild(QCheckBox, "uninstallDeletionConfirmation")
        assert inventory is not None and keep is not None and confirm is not None
        assert "could not finish safely" in inventory.toPlainText()
        assert "PRIVATE_TOKEN" not in inventory.toPlainText()
        assert not confirm.isEnabled() and keep.isEnabled()
        assert store.calls == []
        keep.click()
        assert dialog.result() == QDialog.DialogCode.Accepted
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_partial_deletion_waits_for_explicit_result_acknowledgement() -> None:
    app = _app()
    store = Store(count=1)
    dialog = UninstallReviewDialog(UninstallReviewService(store))
    dialog.show()
    app.processEvents()
    try:
        _inspect(app, dialog)
        store.complete = False
        confirm = dialog.findChild(QCheckBox, "uninstallDeletionConfirmation")
        remove = dialog.findChild(QPushButton, "uninstallDeleteButton")
        keep = dialog.findChild(QPushButton, "uninstallContinueButton")
        inventory = dialog.findChild(QPlainTextEdit, "uninstallCaseInventory")
        assert confirm is not None and remove is not None and keep is not None
        assert inventory is not None
        confirm.setChecked(True)
        remove.click()
        _wait(app, lambda: dialog._thread is None)
        assert "fully deleted: 0" in inventory.toPlainText()
        assert "data remaining: 1" in inventory.toPlainText()
        assert dialog.isVisible() and not remove.isEnabled()
        assert "keep remaining data" in keep.text()
        keep.click()
        assert dialog.result() == QDialog.DialogCode.Accepted
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_cancel_during_review_keeps_thread_alive_until_safe_completion() -> None:
    class SlowStore(Store):
        def __init__(self) -> None:
            super().__init__()
            self.started = Event()
            self.release = Event()

        def inspect(self, *, cancellation: Callable[[], bool]) -> UninstallCaseInventory:
            self.started.set()
            assert self.release.wait(10)
            return super().inspect(cancellation=cancellation)

    app = _app()
    store = SlowStore()
    dialog = UninstallReviewDialog(UninstallReviewService(store))
    dialog.show()
    app.processEvents()
    try:
        choose = dialog.findChild(QCheckBox, "uninstallDeleteChoice")
        inspect = dialog.findChild(QPushButton, "uninstallInspectButton")
        keep = dialog.findChild(QPushButton, "uninstallContinueButton")
        assert choose is not None and inspect is not None and keep is not None
        choose.setChecked(True)
        inspect.click()
        _wait(app, store.started.is_set)
        dialog.close()
        app.processEvents()
        assert dialog.isVisible() and dialog._thread is not None and not keep.isEnabled()
        store.release.set()
        _wait(app, lambda: dialog._thread is None)
        assert store.calls == [] and dialog.isVisible()
        inventory = dialog.findChild(QPlainTextEdit, "uninstallCaseInventory")
        assert inventory is not None and "cancelled" in inventory.toPlainText()
        dialog.reject()
        assert dialog.result() == QDialog.DialogCode.Rejected
    finally:
        store.release.set()
        _wait(app, lambda: dialog._thread is None)
        dialog.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("arguments", [["--silent"], ["--delete-data"], ["PRIVATE_TOKEN"]])
def test_entrypoint_rejects_all_switches_and_paths_without_echoing_values(
    arguments: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(arguments) == 2
    output = capsys.readouterr()
    assert "UNINSTALL_ARGUMENT_INVALID" in output.err
    assert "PRIVATE_TOKEN" not in output.err and output.out == ""


_MAIN_ENTRY_ROOT = Path(__file__).absolute().parents[2]

_MAIN_ENTRY_CHILD = r"""
import json
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]) / "src"))
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QPushButton
from paperworkshell.storage import uninstall_review as storage
from paperworkshell.ui import uninstall_review as review

mode = sys.argv[2]
state = dict(guard_held=False, guard_entries=0, guard_exits=0,
             store_factory_calls=0, storage_calls=0, app_exec_calls=0,
             callback_called=False, callback_guard_held=False,
             callback_in_application_exec=False, control_named=False)

class Store:
    def inspect(self, **kwargs):
        state["storage_calls"] += 1
        raise AssertionError("HOST_STORAGE_INSPECTION_FORBIDDEN")

    def delete(self, case):
        state["storage_calls"] += 1
        raise AssertionError("HOST_STORAGE_DELETION_FORBIDDEN")

store = Store()

def current_store():
    assert state["guard_held"]
    state["store_factory_calls"] += 1
    return store

@contextmanager
def application_guard():
    assert not state["guard_held"]
    state["guard_held"] = True
    state["guard_entries"] += 1
    try:
        yield
    finally:
        state["guard_held"] = False
        state["guard_exits"] += 1

real_dialog = review.UninstallReviewDialog

class Application(QApplication):
    def exec(self):
        assert state["guard_held"]
        state["app_exec_calls"] += 1
        state["inside_application_exec"] = True
        try:
            return super().exec()
        finally:
            state["inside_application_exec"] = False

class Dialog(real_dialog):
    def __init__(self, service):
        assert service._store is store and state["guard_held"]
        super().__init__(service)
        QTimer.singleShot(0, self.finish_queued)

    def finish_queued(self):
        state["callback_called"] = True
        state["callback_guard_held"] = state["guard_held"]
        state["callback_in_application_exec"] = state.get("inside_application_exec", False)
        name = "uninstallContinueButton" if mode == "accepted" else "uninstallCancelButton"
        button = self.findChild(QPushButton, name)
        state["control_named"] = button is not None and bool(button.accessibleName())
        if button is None:
            self.reject()
        else:
            button.click()

# Replace BOTH module aliases before main can construct any real store or mutex.
storage.current_user_uninstall_store = current_store
storage.uninstall_application_guard = application_guard
review.current_user_uninstall_store = current_store
review.uninstall_application_guard = application_guard
review.QApplication = Application
review.UninstallReviewDialog = Dialog
assert QApplication.instance() is None
result = review.main([])
state["return_code"] = result
print(json.dumps(state, sort_keys=True), flush=True)
assert result == (0 if mode == "accepted" else 1), "ENTRY_EXIT_MAPPING"
assert state["guard_entries"] == state["guard_exits"] == 1
assert not state["guard_held"] and state["storage_calls"] == 0
assert state["store_factory_calls"] == 1 and state["callback_called"]
assert state["callback_guard_held"] and state["control_named"]
assert state["app_exec_calls"] == 1, "MAIN_APPLICATION_EVENT_LOOP_NOT_ENTERED"
assert state["callback_in_application_exec"], "CALLBACK_OUTSIDE_MAIN_APPLICATION_LOOP"
"""


@pytest.mark.skipif(os.name != "nt", reason="Windows-only removal entry point")
@pytest.mark.parametrize("mode", ["accepted", "rejected"])
def test_main_uses_real_application_loop_with_guard_and_no_storage(mode: str) -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["PWS_UI_LOCALE"] = "en"
    child = subprocess.run(
        [sys.executable, "-I", "-B", "-c", _MAIN_ENTRY_CHILD, str(_MAIN_ENTRY_ROOT), mode],
        cwd=_MAIN_ENTRY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert child.returncode == 0, child.stdout + child.stderr
    result = json.loads(child.stdout)
    assert result["app_exec_calls"] == 1
    assert result["callback_in_application_exec"] is True
    assert result["storage_calls"] == 0

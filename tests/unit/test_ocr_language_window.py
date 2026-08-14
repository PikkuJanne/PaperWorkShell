"""PWS-0205 persistent OCR language window integration tests."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from paperworkshell.app.ocr_languages import (
    OcrLanguageDiscoveryResult,
    OcrLanguageManager,
    OcrLanguageRuntimeStatus,
    OcrLanguageSnapshot,
)
from paperworkshell.app.organizer import OrganizerBackend, OrganizerViewState
from paperworkshell.ui.backend import OrganizerBackendCommands, OrganizerBackendWorker
from paperworkshell.ui.ocr_languages import OcrLanguageDialog
from paperworkshell.ui.window import PaperWorkShellWindow, _LongOperationKind

CASE_ID = UUID("e1000000-0000-4000-8000-000000000001")


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


def _unused_backend_factory() -> OrganizerBackend:
    raise AssertionError("the window test does not initialize a backend")


@contextmanager
def _window() -> Iterator[
    tuple[PaperWorkShellWindow, OrganizerBackendCommands, OrganizerBackendWorker]
]:
    app = _application()
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(_unused_backend_factory)
    window = PaperWorkShellWindow(commands, worker)
    window.show()
    worker.initialized.emit()
    app.processEvents()
    try:
        yield window, commands, worker
    finally:
        window._allow_close = True
        window.close()
        window.deleteLater()
        app.processEvents()


def _settings_button(window: PaperWorkShellWindow) -> QPushButton:
    button = window.findChild(QPushButton, "ocrLanguageSettingsButton")
    assert button is not None
    return button


def test_settings_entry_is_persistent_on_chooser_and_organizer() -> None:
    with _window() as (window, _commands, worker):
        button = _settings_button(window)
        assert button.isVisible()
        assert button.isEnabled()
        assert button.text() == "Manage OCR languages…"
        assert button.accessibleName() == "Manage OCR languages"
        assert "Ctrl+Shift+L" in button.accessibleDescription()

        worker.state_changed.emit(OrganizerViewState(CASE_ID, 0, (), False, False, False))
        _application().processEvents()

        assert button.isVisible()
        assert button.isEnabled()


def test_dialog_forwards_correlated_refresh_and_rejects_stale_worker_result() -> None:
    with _window() as (window, commands, worker):
        refreshes: list[int] = []
        commands.ocr_languages_refresh_requested.connect(refreshes.append)

        window.activateWindow()
        _settings_button(window).setFocus(Qt.FocusReason.OtherFocusReason)
        QTest.keyClick(
            window,
            Qt.Key.Key_L,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
        )
        _application().processEvents()
        dialog = window.findChild(OcrLanguageDialog, "ocrLanguageDialog")
        assert dialog is not None
        assert dialog.isVisible()
        assert refreshes == [1]
        assert not _settings_button(window).isEnabled()

        worker.ocr_languages_changed.emit(99, _snapshot())
        assert bool(dialog.snapshot is None)
        worker.ocr_languages_changed.emit(1, _snapshot())
        assert dialog.snapshot == _snapshot()

        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        _application().processEvents()
        assert not dialog.isVisible()
        assert window._ocr_language_dialog is None
        assert _settings_button(window).isEnabled()
        assert _settings_button(window).hasFocus()


def test_settings_entry_is_guarded_during_long_work_review_and_shutdown() -> None:
    with _window() as (window, _commands, _worker):
        button = _settings_button(window)
        operation = window._begin_long_operation(_LongOperationKind.PREVIEW)
        assert operation.operation_id == 1
        assert not button.isEnabled()
        window._finish_active_operation()
        assert button.isEnabled()

        window._pending_scan_review_page_id = UUID("e1000000-0000-4000-8000-000000000002")
        window._update_import_availability()
        assert not button.isEnabled()
        window._pending_scan_review_page_id = None
        window._update_import_availability()
        assert button.isEnabled()

        window.request_shutdown()
        assert not button.isEnabled()


def test_shutdown_dismisses_language_dialog_and_late_results_are_ignored() -> None:
    with _window() as (window, _commands, worker):
        QTest.mouseClick(_settings_button(window), Qt.MouseButton.LeftButton)
        _application().processEvents()
        dialog = window.findChild(OcrLanguageDialog, "ocrLanguageDialog")
        assert dialog is not None
        request_id = dialog.pending_request_id
        assert request_id is not None

        window.request_shutdown()
        _application().processEvents()

        assert not dialog.isVisible()
        assert window._ocr_language_dialog is None
        worker.ocr_languages_changed.emit(request_id, _snapshot())
        assert window._ocr_language_dialog is None

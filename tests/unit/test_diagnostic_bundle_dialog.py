"""No implicit export, exact preview, safe failure and cancellation UI lifecycle."""

from __future__ import annotations

import os
from pathlib import Path
from threading import Event
from time import monotonic
from typing import cast
from unittest.mock import ANY, Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from paperworkshell.app.diagnostic_bundle import DiagnosticBundleService
from paperworkshell.diagnostics.errors import (
    DiagnosticErrorCode,
    DiagnosticErrorContext,
    reject_diagnostic,
)
from paperworkshell.diagnostics.logging import SanitizedDiagnosticLogger
from paperworkshell.ui.diagnostic_bundle import DiagnosticBundleDialog
from tests.unit.test_diagnostic_bundle import Writer
from tests.unit.test_scan_review_window import _window


def app() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def idle(dialog: DiagnosticBundleDialog) -> None:
    end = monotonic() + 5
    while dialog.busy and monotonic() < end:
        app().processEvents()
        QTest.qWait(1)
    assert not dialog.busy
    app().processEvents()


def test_opening_and_default_enter_never_publish_and_consent_gates_save() -> None:
    application = app()
    writer = Writer()
    dialog = DiagnosticBundleDialog(DiagnosticBundleService(SanitizedDiagnosticLogger(), writer))
    dialog.show()
    application.processEvents()
    try:
        assert not dialog._consent.isChecked() and not dialog._save.isEnabled()
        assert "summary.txt" in dialog._inventory.toPlainText()
        assert dialog._preview is not None
        assert dialog._preview.sha256 in dialog._inventory.toPlainText()
        QTest.keyClick(dialog._inventory, Qt.Key.Key_Return)
        dialog._start_save(Path("unused.zip"))
        assert writer.writes == [] and not dialog.busy
        dialog._consent.setChecked(True)
        assert dialog._save.isEnabled()
        dialog._start_save(Path("unused.zip"))
        idle(dialog)
        assert writer.writes == [dialog._preview.archive]
        assert "saved locally" in dialog._status.text()
    finally:
        dialog.close()
        dialog.deleteLater()
        application.processEvents()


def test_permission_failure_has_actionable_sanitized_status() -> None:
    class FailedWriter(Writer):
        def write(self, preview: object, destination: object, *, cancellation: object) -> None:
            raise PermissionError("C:\\private\\prohibited0805")

    application = app()
    dialog = DiagnosticBundleDialog(
        DiagnosticBundleService(SanitizedDiagnosticLogger(), FailedWriter())
    )
    dialog.show()
    try:
        dialog._consent.setChecked(True)
        dialog._start_save(Path("unused.zip"))
        idle(dialog)
        assert "private" not in dialog._status.text()
        assert "Choose another local folder" in dialog._status.text()
        assert dialog._save.isEnabled()
    finally:
        dialog.close()
        dialog.deleteLater()
        application.processEvents()


def test_escape_requests_cancel_and_retains_thread_until_cleanup() -> None:
    started = Event()
    finish = Event()

    class WaitingWriter(Writer):
        def write(self, preview: object, destination: object, *, cancellation: object) -> None:
            assert callable(cancellation)
            started.set()
            assert finish.wait(5)
            assert cancellation()
            reject_diagnostic(DiagnosticErrorCode.CANCELLED, DiagnosticErrorContext.BUNDLE)

    application = app()
    dialog = DiagnosticBundleDialog(
        DiagnosticBundleService(SanitizedDiagnosticLogger(), WaitingWriter())
    )
    dialog.show()
    try:
        dialog._consent.setChecked(True)
        dialog._start_save(Path("unused.zip"))
        assert started.wait(2)
        dialog.reject()
        assert dialog.busy and dialog.isVisible()
        assert dialog._cancellation.is_set()
        finish.set()
        idle(dialog)
        assert not dialog.isVisible()
    finally:
        finish.set()
        idle(dialog)
        dialog.deleteLater()
        application.processEvents()


def test_help_action_opens_single_preview_and_returns_to_case_chooser() -> None:
    with _window() as (window, _commands):
        window._diagnostic_bundle_action.trigger()
        app().processEvents()
        dialog = window._diagnostic_bundle_dialog
        assert dialog is not None and dialog._preview is not None
        assert b"Python runtime:" in dialog._preview.archive
        window._diagnostic_bundle_action.trigger()
        assert window._diagnostic_bundle_dialog is dialog
        dialog.reject()
        app().processEvents()
        assert window._diagnostic_bundle_dialog is None


def test_canceling_location_picker_never_starts_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = app()
    writer = Writer()
    dialog = DiagnosticBundleDialog(DiagnosticBundleService(SanitizedDiagnosticLogger(), writer))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args, **kwargs: ("", ""))
    try:
        dialog._consent.setChecked(True)
        dialog._choose_save()
        assert not dialog.busy and writer.writes == []
    finally:
        dialog.deleteLater()
        application.processEvents()


def test_save_picker_starts_in_user_folder_with_no_prefilled_filename(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    application = app()
    writer = Writer()
    write = Mock(wraps=writer.write)
    monkeypatch.setattr(writer, "write", write)
    dialog = DiagnosticBundleDialog(DiagnosticBundleService(SanitizedDiagnosticLogger(), writer))
    home = tmp_path / "user-home"
    home.mkdir()
    selected = home / "reviewed.zip"
    monkeypatch.setattr(Path, "home", lambda: home)

    def choose_path(
        _parent: DiagnosticBundleDialog,
        _caption: str,
        initial_path: str,
        file_filter: str,
        *,
        options: QFileDialog.Option,
    ) -> tuple[str, str]:
        assert Path(initial_path) == home
        assert options & QFileDialog.Option.DontConfirmOverwrite
        return str(selected), file_filter

    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose_path)
    try:
        assert dialog._preview is not None
        dialog._consent.setChecked(True)
        dialog._choose_save()
        idle(dialog)
        write.assert_called_once_with(dialog._preview, selected, cancellation=ANY)
        assert writer.writes == [dialog._preview.archive]
    finally:
        dialog.deleteLater()
        application.processEvents()


def test_window_shutdown_waits_for_support_thread_cleanup() -> None:
    started = Event()
    finish = Event()

    class WaitingWriter(Writer):
        def write(self, preview: object, destination: object, *, cancellation: object) -> None:
            started.set()
            assert finish.wait(5)
            assert callable(cancellation) and cancellation()
            reject_diagnostic(DiagnosticErrorCode.CANCELLED, DiagnosticErrorContext.BUNDLE)

    with _window() as (window, _commands):
        window._diagnostic_bundle_service = DiagnosticBundleService(
            SanitizedDiagnosticLogger(), WaitingWriter()
        )
        window._diagnostic_bundle_action.trigger()
        dialog = window._diagnostic_bundle_dialog
        assert dialog is not None
        try:
            dialog._consent.setChecked(True)
            dialog._start_save(Path("unused.zip"))
            assert started.wait(2)
            window.request_shutdown()
            assert not window._closing_requested
            assert dialog.busy and dialog._cancellation.is_set()
            finish.set()
            idle(dialog)
            app().processEvents()
            assert window._diagnostic_bundle_dialog is None
            assert window._closing_requested
        finally:
            finish.set()
            idle(dialog)

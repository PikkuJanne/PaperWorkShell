"""Keyboard action, destination consent, and terminal-inventory lifecycle evidence."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QFileDialog, QLabel, QPushButton

from paperworkshell.app.organizer import OrganizerBackend, OrganizerPageView, OrganizerViewState
from paperworkshell.app.package_workflow import PackageReviewState
from paperworkshell.domain.package_summary import PackageSummary, PackageSummaryStatus
from paperworkshell.preflight.portal_constraints import PortalOutputMode
from paperworkshell.ui.backend import (
    OperationCancellation,
    OrganizerBackendCommands,
    OrganizerBackendWorker,
    UiFailure,
)
from paperworkshell.ui.package_export import PackageExportDialog
from paperworkshell.ui.window import PaperWorkShellWindow

CASE_ID = UUID("a4000000-0000-4000-8000-000000000011")
REVIEW_ID = UUID("a4000000-0000-4000-8000-000000000012")
PAGE_ID = UUID("a4000000-0000-4000-8000-000000000013")


def _application() -> QApplication:
    current = QApplication.instance()
    return QApplication([]) if current is None else cast(QApplication, current)


def _unused_backend() -> OrganizerBackend:
    raise AssertionError("No backend is created by the presentation test.")


def _review() -> PackageReviewState:
    return PackageReviewState(
        REVIEW_ID,
        PackageSummary(PackageSummaryStatus.PLANNED),
        tuple(PortalOutputMode),
        PortalOutputMode.COMBINED_PDF,
        True,
        False,
        True,
    )


@contextmanager
def _window() -> Iterator[
    tuple[PaperWorkShellWindow, OrganizerBackendCommands, OrganizerBackendWorker]
]:
    app = _application()
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(_unused_backend)
    commands.connect_worker(worker)
    commands.package_review_begin_requested.disconnect(worker.begin_package_review)
    commands.package_review_refresh_requested.disconnect(worker.refresh_package_review)
    commands.package_export_requested.disconnect(worker.export_package)
    commands.package_review_cancel_requested.disconnect(worker.cancel_package_review)
    commands.package_summary_save_requested.disconnect(worker.save_package_summary)
    commands.shutdown_requested.disconnect(worker.shutdown)
    window = PaperWorkShellWindow(commands, worker)
    window.show()
    worker.initialized.emit()
    worker.state_changed.emit(
        OrganizerViewState(
            CASE_ID,
            3,
            (OrganizerPageView(PAGE_ID, 1, 0, False),),
            False,
            False,
            False,
        )
    )
    app.processEvents()
    try:
        yield window, commands, worker
    finally:
        window._allow_close = True
        window.close()
        window.deleteLater()
        app.processEvents()


def _press(button: QPushButton) -> None:
    button.setFocus(Qt.FocusReason.OtherFocusReason)
    QTest.keyClick(button, Qt.Key.Key_Space)
    _application().processEvents()


def _open(
    window: PaperWorkShellWindow,
    commands: OrganizerBackendCommands,
    worker: OrganizerBackendWorker,
) -> PackageExportDialog:
    requests: list[int] = []
    commands.package_review_begin_requested.connect(
        lambda operation, _mode: requests.append(operation)
    )
    button = window.findChild(QPushButton, "packageExportButton")
    assert button is not None and button.isEnabled()
    _press(button)
    assert len(requests) == 1
    worker.package_review_ready.emit(requests[0], _review())
    _application().processEvents()
    dialog = window._package_export_dialog
    assert dialog is not None and dialog.isVisible()
    return dialog


def _acknowledge(dialog: PackageExportDialog) -> QPushButton:
    checkbox = dialog.findChild(QCheckBox, "packageExportHumanReviewCheck")
    assert checkbox is not None
    checkbox.setChecked(True)
    button = dialog.findChild(QPushButton, "packageExportStartButton")
    assert button is not None and button.isEnabled()
    return button


def test_menu_keyboard_shortcut_and_close_hold_mutations_until_backend_release() -> None:
    with _window() as (window, commands, worker):
        action = window.findChild(QAction, "packageExportMenuAction")
        assert action is not None and action.shortcut().toString() == "Ctrl+E"
        dialog = _open(window, commands, worker)
        releases: list[int] = []
        commands.package_review_cancel_requested.connect(releases.append)
        imported = window._import_button
        assert not imported.isEnabled()
        dialog.reject()
        assert len(releases) == 1
        assert not imported.isEnabled()
        worker.package_review_cancelled.emit(releases[0])
        _application().processEvents()
        assert imported.isEnabled()
        assert window._package_export_button.hasFocus()


def test_destination_cancel_clears_acknowledgements_and_never_requests_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _window() as (window, commands, worker):
        dialog = _open(window, commands, worker)
        requests: list[object] = []
        commands.package_export_requested.connect(lambda *args: requests.append(args))
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args, **_kwargs: "")
        _press(_acknowledge(dialog))
        assert requests == []
        assert not dialog.busy
        ack = dialog.findChild(QCheckBox, "packageExportHumanReviewCheck")
        assert ack is not None and not ack.isChecked()


def test_export_carries_review_identity_and_keeps_partial_result_visible_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _window() as (window, commands, worker):
        dialog = _open(window, commands, worker)
        requests: list[tuple[object, ...]] = []
        shutdowns: list[bool] = []
        commands.package_export_requested.connect(lambda *args: requests.append(args))
        commands.shutdown_requested.connect(lambda: shutdowns.append(True))
        monkeypatch.setattr(
            QFileDialog, "getExistingDirectory", lambda *_args, **_kwargs: "C:/synthetic-output"
        )
        _press(_acknowledge(dialog))
        assert len(requests) == 1
        operation, review_id, destination, signed, human, cancellation = requests[0]
        assert review_id == REVIEW_ID
        assert destination == Path("C:/synthetic-output")
        assert signed is False and human is True
        assert type(cancellation) is OperationCancellation
        window.request_shutdown()
        assert cancellation.cancelled()
        assert shutdowns == []
        assert dialog.isVisible()
        worker.package_export_ready.emit(
            cast(int, operation) + 1, PackageSummary(PackageSummaryStatus.UNCERTAIN)
        )
        stale_result_busy = dialog.busy
        assert stale_result_busy
        worker.package_export_ready.emit(
            cast(int, operation), PackageSummary(PackageSummaryStatus.PARTIAL)
        )
        result_busy = dialog.busy
        assert not result_busy
        assert dialog.summary is not None and dialog.summary.status is PackageSummaryStatus.PARTIAL
        assert dialog.isVisible()
        assert shutdowns == []


def test_summary_save_requires_explicit_destination_and_failure_preserves_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _window() as (window, commands, worker):
        dialog = _open(window, commands, worker)
        terminal = PackageSummary(PackageSummaryStatus.NOT_COMMITTED)
        window._package_export_operation_id = 55
        worker.package_export_ready.emit(55, terminal)
        requests: list[tuple[int, object, object]] = []
        commands.package_summary_save_requested.connect(
            lambda operation, path, cancellation: requests.append((operation, path, cancellation))
        )
        monkeypatch.setattr(
            QFileDialog,
            "getSaveFileName",
            lambda *_args, **_kwargs: ("C:/synthetic-summary.txt", ""),
        )
        save = dialog.findChild(QPushButton, "packageExportSaveSummaryButton")
        assert save is not None
        assert requests == []
        _press(save)
        operation, path, cancellation = requests[0]
        assert path == Path("C:/synthetic-summary.txt")
        assert type(cancellation) is OperationCancellation
        worker.package_failed.emit(operation, "save", UiFailure("PRIVATE", "PRIVATE", True))
        assert dialog.summary is terminal
        assert save.isEnabled()
        assert "PRIVATE" not in " ".join(item.text() for item in dialog.findChildren(QLabel))


def test_release_failure_provides_keyboard_retry_and_keeps_mutations_disabled() -> None:
    with _window() as (window, commands, worker):
        dialog = _open(window, commands, worker)
        requests: list[int] = []
        commands.package_review_cancel_requested.connect(requests.append)
        dialog.reject()
        worker.package_failed.emit(requests[0], "cancel", UiFailure("PRIVATE", "PRIVATE", True))
        retry = window._package_export_button
        assert retry.isEnabled() and "Retry closing" in retry.text()
        assert not window._import_button.isEnabled()
        _press(retry)
        assert len(requests) == 2
        worker.package_review_cancelled.emit(requests[1])
        assert window._import_button.isEnabled()
        assert retry.text() == "Review and export package"


def test_summary_save_close_requests_cancellation_without_losing_package_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _window() as (window, commands, worker):
        dialog = _open(window, commands, worker)
        terminal = PackageSummary(PackageSummaryStatus.PARTIAL)
        window._package_export_operation_id = 80
        worker.package_export_ready.emit(80, terminal)
        saves: list[tuple[int, object]] = []
        commands.package_summary_save_requested.connect(
            lambda operation, _path, cancellation: saves.append((operation, cancellation))
        )
        monkeypatch.setattr(
            QFileDialog,
            "getSaveFileName",
            lambda *_args, **_kwargs: ("C:/synthetic-summary.txt", ""),
        )
        save = dialog.findChild(QPushButton, "packageExportSaveSummaryButton")
        assert save is not None
        _press(save)
        operation, cancellation = saves[0]
        assert type(cancellation) is OperationCancellation
        assert not cancellation.cancelled()
        dialog.reject()
        assert cancellation.cancelled()
        assert dialog.isVisible()
        assert dialog.summary is terminal
        worker.package_failed.emit(
            operation, "save", UiFailure("PACKAGE_WORKFLOW_CANCELLED", "PRIVATE", True)
        )
        assert dialog.isVisible()
        assert dialog.summary is terminal
        assert not dialog.busy
        assert save.isEnabled()
        assert window._package_cancellation is None
        status = dialog.findChild(QLabel, "packageExportStatus")
        assert status is not None
        assert "cancellation request" in status.text()
        assert "summary was saved" not in status.text()

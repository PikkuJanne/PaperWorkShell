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
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QLabel,
    QPlainTextEdit,
    QPushButton,
)

from paperworkshell.app.organizer import OrganizerBackend, OrganizerPageView, OrganizerViewState
from paperworkshell.app.package_workflow import PackageReviewState
from paperworkshell.domain.package_summary import (
    PackageSummary,
    PackageSummaryOutput,
    PackageSummaryOutputState,
    PackageSummaryStatus,
)
from paperworkshell.preflight.portal_constraints import PortalOutputMode
from paperworkshell.ui.backend import (
    OperationCancellation,
    OrganizerBackendCommands,
    OrganizerBackendWorker,
    UiFailure,
)
from paperworkshell.ui.package_export import PackageExportDialog
from paperworkshell.ui.window import PaperWorkShellWindow, _LongOperationKind

CASE_ID = UUID("a4000000-0000-4000-8000-000000000011")
REVIEW_ID = UUID("a4000000-0000-4000-8000-000000000012")
PAGE_ID = UUID("a4000000-0000-4000-8000-000000000013")


def _application() -> QApplication:
    current = QApplication.instance()
    return QApplication([]) if current is None else cast(QApplication, current)


def _assert_trace_export_focus(window: PaperWorkShellWindow) -> None:
    route = window.findChild(QPushButton, "traceWorkflow-export")
    assert route is not None and route.isVisible() and route.isEnabled()
    assert QApplication.focusWidget() is route


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
    *,
    review: PackageReviewState | None = None,
) -> PackageExportDialog:
    requests: list[int] = []
    commands.package_review_begin_requested.connect(
        lambda operation, _mode: requests.append(operation)
    )
    button = window.findChild(QPushButton, "packageExportButton")
    assert button is not None and button.isEnabled()
    _press(button)
    assert len(requests) == 1
    worker.package_review_ready.emit(requests[0], _review() if review is None else review)
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


def test_package_review_distinguishes_case_positions_from_planned_package_order() -> None:
    with _window() as (window, commands, worker):
        pages = (
            OrganizerPageView(
                PAGE_ID,
                1,
                0,
                False,
                document_number=2,
                source_page_number=3,
                source_display_name="Identity Ää.pdf",
            ),
            OrganizerPageView(
                UUID("a4000000-0000-4000-8000-000000000014"),
                2,
                0,
                False,
                document_number=1,
                source_page_number=1,
                source_display_name="Application.pdf",
            ),
        )
        worker.state_changed.emit(OrganizerViewState(CASE_ID, 4, pages, False, False, False))
        review = PackageReviewState(
            REVIEW_ID,
            PackageSummary(
                PackageSummaryStatus.PLANNED,
                outputs=(
                    PackageSummaryOutput(
                        "package.pdf",
                        PackageSummaryOutputState.PLANNED,
                        2,
                        categories=("Application", "Identity"),
                    ),
                ),
                active_page_count=2,
            ),
            tuple(PortalOutputMode),
            PortalOutputMode.COMBINED_PDF,
            True,
            False,
        )
        dialog = _open(window, commands, worker, review=review)
        names = dialog.findChild(QPlainTextEdit, "packageExportInputNames")
        summary = dialog.findChild(QPlainTextEdit, "packageExportSummaryText")
        assert names is not None and summary is not None
        assert names.accessibleName() == "Files and pages in case order"
        assert names.toPlainText().splitlines() == [
            "Identity Ää.pdf — source page 3, case page 1",
            "Application.pdf — source page 1, case page 2",
        ]
        assert "Assigned categories: Application; Identity." in summary.toPlainText()


def test_cancelled_initial_inspection_discards_a_late_ready_signal_and_releases_review() -> None:
    with _window() as (window, commands, worker):
        requests: list[tuple[int, object, OperationCancellation]] = []
        releases: list[int] = []
        commands.package_review_begin_requested.connect(lambda *args: requests.append(args))
        commands.package_review_cancel_requested.connect(releases.append)
        _press(window._package_export_button)
        operation, _mode, cancellation = requests[0]
        assert window._cancel_button.hasFocus()
        _press(window._cancel_button)
        assert cancellation.cancelled()
        worker.package_review_ready.emit(operation, _review())
        _application().processEvents()
        assert window._package_export_dialog is None
        assert window._package_review_state is None
        assert window._active_operation is None
        assert len(releases) == 1
        worker.package_review_cancelled.emit(releases[0])
        _application().processEvents()
        assert window._package_export_button.isEnabled()
        _assert_trace_export_focus(window)


def test_cancelled_refresh_clears_summary_and_retry_uses_a_new_cancellation_token() -> None:
    with _window() as (window, commands, worker):
        dialog = _open(window, commands, worker)
        requests: list[tuple[int, object, OperationCancellation]] = []
        commands.package_review_refresh_requested.connect(lambda *args: requests.append(args))
        refresh = dialog.findChild(QPushButton, "packageExportRefreshButton")
        cancel = dialog.findChild(QPushButton, "packageExportCancelButton")
        assert refresh is not None and cancel is not None
        _press(refresh)
        first_operation, _mode, first_token = requests[0]
        assert cancel.hasFocus() and not bool(dialog.summary)
        _press(cancel)
        assert first_token.cancelled()
        worker.package_failed.emit(
            first_operation, "review", UiFailure("PACKAGE_REVIEW_CANCELLED", "PRIVATE", True)
        )
        _application().processEvents()
        assert refresh.isEnabled() and refresh.hasFocus() and not dialog.busy
        assert window._package_review_state is None
        _press(refresh)
        second_operation, _mode, second_token = requests[1]
        assert second_operation != first_operation and second_token is not first_token
        assert not second_token.cancelled()
        worker.package_review_ready.emit(second_operation, _review())
        _application().processEvents()
        assert dialog.summary is not None and not dialog.busy


@pytest.mark.parametrize("cancel_queued", [False, True])
def test_package_inspection_waits_for_preview_termination_and_queued_cancel_is_effective(
    cancel_queued: bool,
) -> None:
    with _window() as (window, commands, worker):
        requests: list[tuple[int, object, OperationCancellation]] = []
        commands.package_review_begin_requested.connect(lambda *args: requests.append(args))
        preview = window._begin_long_operation(_LongOperationKind.PREVIEW)
        _press(window._package_export_button)
        assert bool(window._pending_package_review) and preview.cancellation.cancelled()
        assert requests == []
        if cancel_queued:
            _press(window._cancel_button)
            assert not bool(window._pending_package_review)
        worker.operation_finished.emit(preview.operation_id)
        _application().processEvents()
        if cancel_queued:
            assert requests == []
            assert window._package_export_button.isEnabled()
            _assert_trace_export_focus(window)
        else:
            assert len(requests) == 1
            assert window._active_operation is not None
            assert window._active_operation.kind is _LongOperationKind.PACKAGE_REVIEW


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
        _assert_trace_export_focus(window)


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
        assert retry.text() == "Export checklist package…"


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


def _open_after_ordinary_export(
    window: PaperWorkShellWindow,
    commands: OrganizerBackendCommands,
    worker: OrganizerBackendWorker,
) -> PackageExportDialog:
    """Retain a real ordinary-export presentation, then show inspection progress."""

    previous = window._begin_long_operation(_LongOperationKind.PDF_EXPORT)
    window._pdf_export_destination = Path("C:/synthetic-output/earlier.pdf")
    worker.pages_pdf_export_saved.emit(previous.operation_id, 1)
    worker.operation_finished.emit(previous.operation_id)
    assert window._open_pdf_button.isVisible()
    commands.package_review_begin_requested.connect(
        lambda operation, _mode, _cancel: worker.package_progress.emit(operation, 4, 4)
    )
    return _open(window, commands, worker)


def _close_and_release(
    dialog: PackageExportDialog,
    commands: OrganizerBackendCommands,
    worker: OrganizerBackendWorker,
) -> None:
    releases: list[int] = []
    commands.package_review_cancel_requested.connect(releases.append)
    dialog.reject()
    assert len(releases) == 1
    worker.package_review_cancelled.emit(releases[0])
    _application().processEvents()


@pytest.mark.parametrize(
    ("status", "output_state", "saved"),
    [
        (PackageSummaryStatus.COMMITTED, PackageSummaryOutputState.COMMITTED, 1),
        (PackageSummaryStatus.CONSTRAINTS_BLOCKED, PackageSummaryOutputState.NOT_COMMITTED, 0),
        (PackageSummaryStatus.NOT_COMMITTED, PackageSummaryOutputState.NOT_COMMITTED, 0),
        (PackageSummaryStatus.PARTIAL, PackageSummaryOutputState.COMMITTED, 1),
        (PackageSummaryStatus.UNCERTAIN, PackageSummaryOutputState.UNKNOWN, 0),
    ],
)
def test_package_terminal_inventory_replaces_stale_progress_and_ordinary_open_actions(
    status: PackageSummaryStatus,
    output_state: PackageSummaryOutputState,
    saved: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _window() as (window, commands, worker):
        dialog = _open_after_ordinary_export(window, commands, worker)
        requests: list[int] = []
        commands.package_export_requested.connect(
            lambda operation, *_args: requests.append(operation)
        )
        monkeypatch.setattr(
            QFileDialog, "getExistingDirectory", lambda *_args, **_kwargs: "C:/synthetic-package"
        )
        _press(_acknowledge(dialog))
        summary = PackageSummary(
            status,
            # A summary filename is display-only, never authority for an Open action.
            (PackageSummaryOutput("../display-only.pdf", output_state, 4, 4137),),
            active_page_count=4,
        )
        worker.package_export_ready.emit(requests[0], summary)
        expected = summary.status_message + f" Files planned: 1. Files saved and reopened: {saved}."
        assert window._status_label.text() == expected
        assert expected in window._status_label.accessibleName()
        assert window._last_result_label.text() == expected
        assert window._last_result_label.toolTip() == ""
        assert window._last_export_path is None
        assert window._open_pdf_button.isHidden()
        assert window._open_export_folder_button.isHidden()
        assert not window._progress.isVisible()
        _close_and_release(dialog, commands, worker)
        assert window._status_label.text() == expected
        preview = window._begin_long_operation(_LongOperationKind.PREVIEW)
        worker.operation_status_changed.emit(preview.operation_id, "Page previews ready.")
        worker.operation_finished.emit(preview.operation_id)
        assert window._last_result_label.text() == expected
        assert window._open_pdf_button.isHidden()


@pytest.mark.parametrize(
    ("phase", "code", "expected"),
    [
        ("review", "PACKAGE_REVIEW_CANCELLED", "Package inspection was cancelled."),
        ("review", "PACKAGE_REVIEW_TIMED_OUT", "Package inspection exceeded its time limit."),
        ("review", "PRIVATE", "Package review could not be prepared."),
        (
            "export",
            "PACKAGE_WORKFLOW_CANCELLED",
            "Package export was cancelled before writing files.",
        ),
        ("export", "PACKAGE_EXPORT_CANCELLED", "Package export was cancelled before completion."),
        ("export", "PRIVATE", "Package export did not finish safely."),
    ],
)
def test_package_failure_and_cancellation_remain_honest_after_closing_review(
    phase: str, code: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _window() as (window, commands, worker):
        dialog = _open_after_ordinary_export(window, commands, worker)
        requests: list[int] = []
        if phase == "review":
            commands.package_review_refresh_requested.connect(
                lambda operation, *_args: requests.append(operation)
            )
            refresh = dialog.findChild(QPushButton, "packageExportRefreshButton")
            assert refresh is not None
            _press(refresh)
        else:
            commands.package_export_requested.connect(
                lambda operation, *_args: requests.append(operation)
            )
            monkeypatch.setattr(
                QFileDialog,
                "getExistingDirectory",
                lambda *_args, **_kwargs: "C:/synthetic-package",
            )
            _press(_acknowledge(dialog))
        worker.package_failed.emit(requests[0], phase, UiFailure(code, "PRIVATE", True))
        assert expected in window._status_label.text()
        assert "PRIVATE" not in window._status_label.text()
        terminal = window._last_result_label.text()
        assert expected in terminal
        assert window._last_export_path is None
        assert window._open_pdf_button.isHidden()
        _close_and_release(dialog, commands, worker)
        assert window._status_label.text() == terminal
        assert window._last_result_label.text() == terminal


def test_closing_review_without_export_ends_inspection_without_claiming_saved_files() -> None:
    with _window() as (window, commands, worker):
        dialog = _open_after_ordinary_export(window, commands, worker)
        _close_and_release(dialog, commands, worker)
        assert "No files have been saved by this attempt." in window._status_label.text()
        assert "processed" not in window._status_label.text()
        assert window._last_result_label.text() == window._status_label.text()
        assert window._last_export_path is None
        assert window._open_pdf_button.isHidden()
        assert window._open_export_folder_button.isHidden()
        assert not window._progress.isVisible()


def test_completed_inspection_stops_reporting_running_progress_while_review_is_open() -> None:
    with _window() as (window, commands, worker):
        dialog = _open_after_ordinary_export(window, commands, worker)
        assert dialog.isVisible()
        assert window._active_operation is None
        assert window._status_label.text() == _review().summary.status_message
        assert not window._progress.isVisible()


@pytest.mark.parametrize("summary", [None, PackageSummary(PackageSummaryStatus.PLANNED)])
def test_invalid_package_terminal_signal_cannot_publish_success_or_an_open_target(
    summary: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _window() as (window, commands, worker):
        dialog = _open_after_ordinary_export(window, commands, worker)
        requests: list[int] = []
        commands.package_export_requested.connect(
            lambda operation, *_args: requests.append(operation)
        )
        monkeypatch.setattr(
            QFileDialog, "getExistingDirectory", lambda *_args, **_kwargs: "C:/synthetic-package"
        )
        _press(_acknowledge(dialog))
        previous_result = window._last_result_label.text()
        worker.package_export_ready.emit(requests[0] + 1, summary)
        stale_result_busy = dialog.busy
        assert stale_result_busy and window._last_result_label.text() == previous_result
        worker.package_export_ready.emit(requests[0], summary)
        terminal_busy = dialog.busy
        assert not terminal_busy
        assert "Package export did not finish safely." in window._last_result_label.text()
        assert window._last_export_path is None
        assert window._open_pdf_button.isHidden()
        _close_and_release(dialog, commands, worker)
        assert "Package export did not finish safely." in window._status_label.text()


def test_a_new_ordinary_export_restores_only_its_own_verified_open_target() -> None:
    with _window() as (window, commands, worker):
        dialog = _open_after_ordinary_export(window, commands, worker)
        _close_and_release(dialog, commands, worker)
        assert window._open_pdf_button.isHidden()
        operation = window._begin_long_operation(_LongOperationKind.PDF_EXPORT)
        destination = Path("C:/synthetic-output/current.pdf")
        window._pdf_export_destination = destination
        worker.pages_pdf_export_saved.emit(operation.operation_id, 4)
        worker.operation_finished.emit(operation.operation_id)
        assert window._last_export_path == destination
        assert window._last_result_label.text() == "Saved current.pdf — 4 pages."
        assert window._open_pdf_button.isVisible()
        assert window._open_export_folder_button.isVisible()


def test_invalid_inspection_response_releases_review_and_reports_a_main_window_failure() -> None:
    with _window() as (window, commands, worker):
        inspections: list[int] = []
        releases: list[int] = []
        commands.package_review_begin_requested.connect(
            lambda operation, *_args: inspections.append(operation)
        )
        commands.package_review_cancel_requested.connect(releases.append)
        _press(window._package_export_button)
        worker.package_progress.emit(inspections[0], 4, 4)
        worker.package_review_ready.emit(inspections[0], None)
        assert window._active_operation is None
        assert window._package_export_dialog is None
        assert len(releases) == 1
        assert "Package review could not be prepared." in window._status_label.text()
        worker.package_review_cancelled.emit(releases[0])
        assert window._status_label.text() == window._last_result_label.text()

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import cast
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QListView,
    QPushButton,
    QScrollArea,
)

from paperworkshell.app.organizer import OrganizerBackend, OrganizerPageView, OrganizerViewState
from paperworkshell.domain.case_lifecycle import (
    CaseIndex,
    CaseIndexIssue,
    CaseLifecycleErrorCode,
)
from paperworkshell.domain.recovery import (
    CaseRecoveryResult,
    CaseRecoveryStatus,
    RecoveryIssueCode,
    StartupRecoveryResult,
)
from paperworkshell.ui.backend import (
    OrganizerBackendCommands,
    OrganizerBackendWorker,
    ShutdownRefusalKind,
    UiFailure,
    UiProgress,
)
from paperworkshell.ui.models import PreviewState
from paperworkshell.ui.window import (
    PageOrderDialog,
    PaperWorkShellWindow,
    _LongOperationKind,
    _startup_recovery_notice,
)
from tools.dev import COMMANDS

PAGE_ONE = UUID("50000000-0000-4000-8000-000000000001")
PAGE_TWO = UUID("50000000-0000-4000-8000-000000000002")
CASE_ID = UUID("60000000-0000-4000-8000-000000000001")


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def _unused_backend_factory() -> OrganizerBackend:
    raise AssertionError("the accessibility surface test does not initialize its backend")


@contextmanager
def _window() -> Iterator[
    tuple[PaperWorkShellWindow, OrganizerBackendCommands, OrganizerBackendWorker]
]:
    app = _application()
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(_unused_backend_factory)
    commands.connect_worker(worker)
    window = PaperWorkShellWindow(commands, worker)
    window.show()
    app.processEvents()
    try:
        yield window, commands, worker
    finally:
        window._allow_close = True
        window.close()
        window.deleteLater()
        app.processEvents()


def _state(
    *,
    autosave_pending: bool = False,
    recovery_review_required: bool = False,
) -> OrganizerViewState:
    return OrganizerViewState(
        case_id=CASE_ID,
        revision=3,
        pages=(
            OrganizerPageView(PAGE_ONE, 1, 0, False),
            OrganizerPageView(PAGE_TWO, 2, 90, False),
        ),
        can_undo=True,
        can_redo=True,
        autosave_pending=autosave_pending,
        recovery_review_required=recovery_review_required,
    )


def test_accessibility_lane_is_registered() -> None:
    specification = COMMANDS["test-accessibility"]

    assert specification.include_in_check
    assert specification.arguments == ("pytest", "tests/accessibility")


def test_core_controls_have_names_descriptions_and_no_drag_requirement() -> None:
    with _window() as (window, _commands, worker):
        worker.state_changed.emit(_state())
        _application().processEvents()

        page_list = window.findChild(QListView, "pageList")
        assert page_list is not None
        assert page_list.accessibleName() == "Active pages"
        assert "visible organizer actions" in page_list.accessibleDescription()
        assert not page_list.dragEnabled()
        assert not page_list.acceptDrops()
        assert page_list.dragDropMode() is QAbstractItemView.DragDropMode.NoDragDrop

        for object_name in (
            "importButton",
            "closeCaseButton",
            "moveEarlierButton",
            "moveLaterButton",
            "rotateButton",
            "removeButton",
            "changeOrderButton",
            "undoButton",
            "redoButton",
            "cancelButton",
            "retryPreviewButton",
            "retrySaveButton",
            "retryCloseButton",
        ):
            button = window.findChild(QPushButton, object_name)
            assert button is not None
            assert button.accessibleName() or button.text()
            assert button.accessibleDescription()


def test_keyboard_shortcuts_cover_core_page_actions() -> None:
    with _window() as (window, commands, worker):
        moves: list[tuple[UUID, int]] = []
        rotations: list[UUID] = []
        removals: list[UUID] = []
        undos: list[bool] = []
        commands.move_requested.connect(lambda page_id, offset: moves.append((page_id, offset)))
        commands.rotate_requested.connect(lambda page_id: rotations.append(page_id))
        commands.remove_requested.connect(lambda page_id: removals.append(page_id))
        commands.undo_requested.connect(lambda: undos.append(True))
        worker.initialized.emit()
        worker.state_changed.emit(_state())
        _application().processEvents()

        QTest.keyClick(window, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
        QTest.keyClick(window, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(window, Qt.Key.Key_Delete)
        QTest.keyClick(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)

        assert moves == [(PAGE_ONE, 1)]
        assert rotations == [PAGE_ONE]
        assert removals == [PAGE_ONE]
        assert undos == [True]


def test_window_shortcuts_respect_edge_history_disabled_and_shutdown_guards() -> None:
    with _window() as (window, commands, worker):
        moves: list[tuple[UUID, int]] = []
        rotations: list[UUID] = []
        removals: list[UUID] = []
        undos: list[bool] = []
        redos: list[bool] = []
        creates: list[bool] = []
        commands.move_requested.connect(lambda page_id, offset: moves.append((page_id, offset)))
        commands.rotate_requested.connect(lambda page_id: rotations.append(page_id))
        commands.remove_requested.connect(lambda page_id: removals.append(page_id))
        commands.undo_requested.connect(lambda: undos.append(True))
        commands.redo_requested.connect(lambda: redos.append(True))
        commands.create_case_requested.connect(lambda: creates.append(True))
        worker.initialized.emit()
        worker.state_changed.emit(replace(_state(), can_undo=False, can_redo=False))
        _application().processEvents()

        QTest.keyClick(window, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
        QTest.keyClick(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(window, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(window, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier)
        assert moves == []
        assert undos == []
        assert redos == []
        assert creates == []

        window._set_controls_enabled(False)
        QTest.keyClick(window, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
        QTest.keyClick(window, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(window, Qt.Key.Key_Delete)
        assert moves == []
        assert rotations == []
        assert removals == []


def test_progress_error_and_cancellation_are_textual_focusable_and_announced() -> None:
    with _window() as (window, _commands, worker):
        worker.state_changed.emit(_state())
        operation = window._begin_long_operation(_LongOperationKind.IMPORT)
        worker.progress_changed.emit(
            UiProgress("Import", "copying", 2, 5, True, operation.operation_id)
        )
        _application().processEvents()

        cancel = window.findChild(QPushButton, "cancelButton")
        assert cancel is not None
        assert cancel.isVisible()
        assert cancel.isEnabled()
        assert cancel.focusPolicy() is not Qt.FocusPolicy.NoFocus
        assert "Import copying: 2 of 5" in window._status_label.text()
        assert "Import: copying" in window._progress.text()

        worker.failed.emit(UiFailure("PWS_TEST_FAILURE", "Try the action again.", True))
        _application().processEvents()
        assert window._error_label.isVisible()
        assert window._error_label.text() == "PWS_TEST_FAILURE: Try the action again."
        assert "Action failed" in window._status_label.text()


def test_startup_recovery_is_one_path_free_accessibility_announcement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    announcements: list[tuple[str, bool]] = []
    original_announce = PaperWorkShellWindow._announce

    def record_announcement(
        window: PaperWorkShellWindow,
        message: str,
        *,
        assertive: bool = False,
    ) -> None:
        announcements.append((message, assertive))
        original_announce(window, message, assertive=assertive)

    monkeypatch.setattr(PaperWorkShellWindow, "_announce", record_announcement)

    with _window() as (_window_instance, _commands, worker):
        worker.startup_recovery_changed.emit(StartupRecoveryResult())
        _application().processEvents()

        assert len(announcements) == 1
        assert announcements[0][1] is False
        assert "Startup recovery complete" in announcements[0][0]

    announcements.clear()
    hidden_case_id = UUID("61000000-0000-4000-8000-000000000001")
    result = StartupRecoveryResult(
        (
            CaseRecoveryResult(
                case_id=hidden_case_id,
                status=CaseRecoveryStatus.CURRENT,
                manifest_revision=0,
                issues=(RecoveryIssueCode.SOURCE_ARTIFACTS_RETAINED,),
                retained_source_artifacts=1,
            ),
        )
    )
    with _window() as (window, _commands, worker):
        new_case_id = UUID("61000000-0000-4000-8000-000000000002")
        worker.cases_changed.emit(
            CaseIndex(
                unavailable=(CaseIndexIssue(new_case_id, CaseLifecycleErrorCode.MANIFEST_INVALID),)
            )
        )
        worker.startup_recovery_changed.emit(StartupRecoveryResult())
        _application().processEvents()

        notice = window._status_label.text()
        assert announcements == [(notice, True)]
        assert notice == (
            "Startup recovery needs attention. Checked 1 local case; 1 needs review. "
            "Uncertain leftover work data was preserved."
        )
        assert str(new_case_id) not in notice
        assert window._pending_startup_unavailable_case_ids == frozenset()

    announcements.clear()
    with _window() as (window, _commands, worker):
        worker.cases_changed.emit(
            CaseIndex(
                unavailable=(
                    CaseIndexIssue(hidden_case_id, CaseLifecycleErrorCode.MANIFEST_INVALID),
                )
            )
        )
        worker.startup_recovery_changed.emit(result)
        _application().processEvents()

        notice = window._status_label.text()
        assert announcements == [(notice, True)]
        assert notice == (
            "Startup recovery needs attention. Checked 1 local case; 1 needs review. "
            "Uncertain leftover work data was preserved."
        )
        assert str(hidden_case_id) not in notice
        assert "\\" not in notice
        assert window._pending_startup_unavailable_case_ids == frozenset()


def test_startup_recovery_notice_pluralizes_case_counts() -> None:
    cases = tuple(
        CaseRecoveryResult(
            case_id=UUID(f"62000000-0000-4000-8000-{number:012d}"),
            status=CaseRecoveryStatus.CURRENT,
            manifest_revision=0,
            issues=(RecoveryIssueCode.SOURCE_ARTIFACTS_RETAINED,),
            retained_source_artifacts=1,
        )
        for number in (1, 2)
    )

    assert _startup_recovery_notice(StartupRecoveryResult(cases)) == (
        "Startup recovery needs attention. Checked 2 local cases; 2 need review. "
        "Uncertain leftover work data was preserved.",
        True,
    )


def test_preview_cancel_and_failure_leave_no_page_claiming_to_be_loading() -> None:
    with _window() as (window, _commands, worker):
        supported = replace(
            _state(),
            pages=tuple(replace(page, preview_supported=True) for page in _state().pages),
        )
        worker.initialized.emit()
        worker.state_changed.emit(supported)
        _application().processEvents()
        window._preview_refresh_timer.stop()

        cancelled = window._begin_long_operation(_LongOperationKind.PREVIEW)
        window._active_preview_page_ids = (PAGE_ONE,)
        window._model.set_preview_loading((PAGE_ONE,))
        window._cancel_active_preview()
        assert window._model.preview_state(PAGE_ONE) is PreviewState.NOT_LOADED
        window._operation_finished(cancelled.operation_id)

        failed = window._begin_long_operation(_LongOperationKind.PREVIEW)
        window._active_preview_page_ids = (PAGE_ONE,)
        window._model.set_preview_loading((PAGE_ONE,))
        window._failed(
            UiFailure(
                "PDF_THUMBNAIL_TIMED_OUT",
                "The preview took too long.",
                True,
                failed.operation_id,
            )
        )
        window._operation_finished(failed.operation_id)
        assert window._model.preview_state(PAGE_ONE) is PreviewState.FAILED


def test_failed_preview_focuses_keyboard_retry_and_reissues_a_new_correlation() -> None:
    app = _application()
    with _window() as (window, commands, worker):
        supported = replace(
            _state(),
            pages=tuple(replace(page, preview_supported=True) for page in _state().pages),
        )
        worker.initialized.emit()
        worker.state_changed.emit(supported)
        app.processEvents()
        window._preview_refresh_timer.stop()
        commands.thumbnails_requested.disconnect(worker.get_thumbnails)
        requests: list[tuple[object, ...]] = []
        commands.thumbnails_requested.connect(lambda *request: requests.append(request))

        failed = window._begin_long_operation(_LongOperationKind.PREVIEW)
        window._active_preview_page_ids = (PAGE_ONE,)
        window._model.set_preview_loading((PAGE_ONE,))
        window._failed(
            UiFailure(
                "PDF_THUMBNAIL_WORKER_FAILED",
                "The document worker could not generate the thumbnail safely.",
                True,
                failed.operation_id,
            )
        )
        assert window._retry_preview_button.isVisible()
        assert not window._retry_preview_button.isEnabled()

        window._operation_finished(failed.operation_id)
        app.processEvents()

        assert window._retry_preview_button.isVisible()
        assert window._retry_preview_button.isEnabled()
        assert app.focusWidget() is window._retry_preview_button

        QTest.keyClick(window._retry_preview_button, Qt.Key.Key_Space)
        app.processEvents()

        retry = window._active_operation
        assert retry is not None
        assert retry.operation_id != failed.operation_id
        assert retry.kind is _LongOperationKind.PREVIEW
        assert window._model.preview_state(PAGE_ONE) is PreviewState.LOADING
        assert len(requests) == 1
        assert requests[0][0] == retry.operation_id
        assert requests[0][2] == (PAGE_ONE,)
        assert app.focusWidget() is window._page_view
        assert window._status_label.text() == "Retrying the selected page preview."


def test_import_failure_restores_focus_to_the_import_recovery_action() -> None:
    app = _application()
    with _window() as (window, _commands, worker):
        worker.initialized.emit()
        worker.state_changed.emit(_state())
        app.processEvents()
        window._preview_refresh_timer.stop()

        failed = window._begin_long_operation(_LongOperationKind.IMPORT)
        window._failed(
            UiFailure(
                "SOURCE_UNAVAILABLE",
                "The selected source file is not available.",
                False,
                failed.operation_id,
            )
        )
        assert app.focusWidget() is window._error_label

        window._operation_finished(failed.operation_id)
        app.processEvents()

        assert window._import_button.isEnabled()
        assert app.focusWidget() is window._import_button
        assert window._error_label.isVisible()


def test_pending_save_shutdown_refusal_keeps_the_existing_retry_save_action() -> None:
    app = _application()
    with _window() as (window, _commands, worker):
        worker.initialized.emit()
        worker.state_changed.emit(_state(autosave_pending=True))
        app.processEvents()
        window._autosave_timer.stop()
        window._closing_requested = True
        window._set_controls_enabled(False)

        window._shutdown_refused(ShutdownRefusalKind.SAVE_PENDING)
        app.processEvents()

        assert window._status_label.text() == (
            "The application remains open because queued changes could not be saved. "
            "Retry saving changes."
        )
        assert window._retry_save_button.isVisible()
        assert window._retry_save_button.isEnabled()
        assert not window._retry_close_button.isVisible()
        assert app.focusWidget() is window._retry_save_button


def test_cleanup_shutdown_refusal_requires_typed_signal_and_retries_truthfully() -> None:
    app = _application()
    with _window() as (window, commands, worker):
        worker.initialized.emit()
        worker.state_changed.emit(OrganizerViewState.empty())
        app.processEvents()
        window._closing_requested = True
        window._set_controls_enabled(False)

        window._shutdown_refused(object())
        app.processEvents()

        assert window._closing_requested
        assert not window._create_case_button.isEnabled()
        assert not window._retry_save_button.isVisible()
        assert not window._retry_close_button.isVisible()

        window._shutdown_refused(ShutdownRefusalKind.CLEANUP_RETRY)
        app.processEvents()

        assert window._status_label.text() == (
            "The application remains open because secure cleanup could not finish. "
            "Retry closing PaperWorkShell."
        )
        assert window._retry_close_button.isVisible()
        assert window._retry_close_button.isEnabled()
        assert not window._retry_save_button.isVisible()
        assert app.focusWidget() is window._retry_close_button

        requested: list[bool] = []
        commands.shutdown_requested.disconnect(worker.shutdown)
        commands.shutdown_requested.connect(lambda: requested.append(True))
        QTest.keyClick(window._retry_close_button, Qt.Key.Key_Space)
        app.processEvents()

        assert requested == [True]
        assert window._closing_requested
        assert window._status_label.text() == (
            "Retrying secure cleanup before closing PaperWorkShell."
        )
        assert not window._retry_close_button.isVisible()


def test_committed_import_reopen_outcome_locks_stale_actions_and_focuses_close() -> None:
    app = _application()
    with _window() as (window, commands, worker):
        supported = replace(
            _state(),
            pages=tuple(replace(page, preview_supported=True) for page in _state().pages),
        )
        mutations: list[str] = []
        preview_requests: list[object] = []
        commands.move_requested.connect(lambda _page_id, _offset: mutations.append("move"))
        commands.rotate_requested.connect(lambda _page_id: mutations.append("rotate"))
        commands.remove_requested.connect(lambda _page_id: mutations.append("remove"))
        commands.undo_requested.connect(lambda: mutations.append("undo"))
        commands.redo_requested.connect(lambda: mutations.append("redo"))
        commands.thumbnails_requested.connect(lambda *request: preview_requests.append(request))
        worker.initialized.emit()
        worker.state_changed.emit(supported)
        app.processEvents()
        window._preview_refresh_timer.stop()

        operation = window._begin_long_operation(_LongOperationKind.IMPORT)
        message = "Import committed. Close and reopen this case before continuing."
        worker.case_reopen_required.emit(operation.operation_id, message)
        app.processEvents()

        assert window._case_reopen_required
        assert window._status_label.text() == message
        assert not window._error_label.isVisible()
        assert not window._retry_save_button.isVisible()
        assert not window._import_button.isEnabled()
        assert window._close_case_button.isEnabled()
        assert app.focusWidget() is window._close_case_button
        for action in (
            window._move_earlier_button,
            window._move_later_button,
            window._rotate_button,
            window._remove_button,
            window._change_order_button,
            window._undo_button,
            window._redo_button,
        ):
            assert not action.isEnabled()

        QTest.keyClick(window, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
        QTest.keyClick(window, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(window, Qt.Key.Key_Delete)
        QTest.keyClick(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(window, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        assert mutations == []

        window._operation_finished(operation.operation_id)
        window._schedule_preview_refresh()
        QTest.qWait(100)
        app.processEvents()
        assert preview_requests == []
        assert app.focusWidget() is window._close_case_button


def test_status_and_safe_error_are_visible_and_focusable_on_each_workflow_page() -> None:
    app = _application()
    with _window() as (window, _commands, worker):
        assert window._stack.currentWidget() is window._welcome_page
        assert window._status_surface.isVisible()
        assert window._status_label.isVisible()
        assert window._status_label.accessibleName() == "Current operation status"
        for label in (window._status_label, window._error_label):
            assert "border: 2px solid transparent" in label.styleSheet()
            assert "QLabel:focus" in label.styleSheet()
            assert "border-color: palette(highlight)" in label.styleSheet()

        worker.status_changed.emit("Case list is ready.")
        app.processEvents()
        assert window._status_label.text() == "Case list is ready."

        worker.failed.emit(
            UiFailure(
                "PWS_TEST_FAILURE",
                "PaperWorkShell could not open the case list. Try again.",
                True,
            )
        )
        app.processEvents()
        assert window._error_label.isVisible()
        assert window._error_label.accessibleName() == "Action error"
        assert "PWS_TEST_FAILURE" in window._error_label.text()
        assert app.focusWidget() is window._error_label
        assert "path" not in window._error_label.text().casefold()

        worker.status_changed.emit("Ready to retry.")
        app.processEvents()
        assert not window._error_label.isVisible()
        assert app.focusWidget() is window._status_label

        worker.initialized.emit()
        worker.state_changed.emit(_state())
        worker.failed.emit(UiFailure("PWS_TEST_FAILURE", "Try the action again.", True))
        app.processEvents()
        assert window._stack.currentWidget() is window._organizer_page
        assert window._status_surface.isVisible()
        assert window._error_label.isVisible()
        assert app.focusWidget() is window._page_view


def test_state_updates_preserve_focus_and_hidden_cancel_moves_focus_safely() -> None:
    app = _application()
    with _window() as (window, _commands, worker):
        worker.initialized.emit()
        worker.state_changed.emit(OrganizerViewState(CASE_ID, 0, (), False, False, False))
        app.processEvents()
        assert app.focusWidget() is window._import_button

        worker.state_changed.emit(_state(autosave_pending=True))
        app.processEvents()
        window._rotate_button.setFocus()
        app.processEvents()
        assert app.focusWidget() is window._rotate_button

        worker.state_changed.emit(_state(autosave_pending=False))
        app.processEvents()
        assert app.focusWidget() is window._rotate_button

        window._remove_button.setFocus()
        worker.state_changed.emit(OrganizerViewState(CASE_ID, 1, (), True, False, False))
        app.processEvents()
        assert app.focusWidget() is window._import_button

        worker.state_changed.emit(_state())
        app.processEvents()

        operation = window._begin_long_operation(_LongOperationKind.PREVIEW)
        window._cancel_button.setFocus()
        app.processEvents()
        assert app.focusWidget() is window._cancel_button

        window._operation_finished(operation.operation_id)
        app.processEvents()
        assert not window._cancel_button.isVisible()
        assert app.focusWidget() is not window._cancel_button
        assert app.focusWidget() is window._page_view


def test_recovered_case_has_visible_and_announced_review_status() -> None:
    with _window() as (window, _commands, worker):
        worker.initialized.emit()
        worker.state_changed.emit(_state(recovery_review_required=True))
        worker.review_required.emit(
            "Case recovered from the validated save journal. Review the page organizer."
        )
        _application().processEvents()

        assert "recovered; review needed" in window._case_heading.text()
        assert "Review the page organizer" in window._status_label.text()
        assert "path" not in window._status_label.text().casefold()


def test_page_order_dialog_and_scaled_layout_remain_keyboard_reachable() -> None:
    app = _application()
    original_font = app.font()
    enlarged = app.font()
    enlarged.setPointSizeF(max(18.0, original_font.pointSizeF() * 2.0))
    app.setFont(enlarged)
    try:
        dialog = PageOrderDialog(((PAGE_ONE, 1), (PAGE_TWO, 2)))
        assert dialog._page_list.dragDropMode() is QAbstractItemView.DragDropMode.NoDragDrop
        assert not dialog._move_earlier.isEnabled()
        assert dialog._move_later.isEnabled()
        dialog._move(1)
        assert dialog.page_ids == (PAGE_TWO, PAGE_ONE)
        assert dialog._move_earlier.isEnabled()
        assert not dialog._move_later.isEnabled()
        assert dialog._move_status.text() == "Page 1 moved to position 2 of 2."
        dialog.show()
        app.processEvents()
        dialog._page_list.setFocus()
        QTest.keyClick(dialog._page_list, Qt.Key.Key_Tab)
        app.processEvents()
        assert app.focusWidget() is dialog._move_earlier
        dialog.close()

        with _window() as (window, _commands, worker):
            window.resize(960, 540)
            worker.initialized.emit()
            app.processEvents()
            scroll = window.findChild(QScrollArea, "applicationScrollArea")
            assert scroll is not None
            assert scroll.widgetResizable()
            assert window.width() <= 960
            assert window.height() <= 540

            chooser_controls = (
                window._case_list,
                window._create_case_button,
                window._open_case_button,
                window._refresh_cases_button,
                window._status_surface,
            )
            for chooser_widget in chooser_controls:
                scroll.ensureWidgetVisible(chooser_widget, 8, 8)
                app.processEvents()
                chooser_origin = chooser_widget.mapTo(scroll.viewport(), QPoint(0, 0))
                assert QRect(chooser_origin, chooser_widget.size()).intersects(
                    scroll.viewport().rect()
                )

            worker.state_changed.emit(_state())
            app.processEvents()
            window._page_view.setFocus()
            QTest.keyClick(window._page_view, Qt.Key.Key_Tab)
            app.processEvents()
            # The first page cannot move earlier, so Qt correctly skips that disabled action.
            assert app.focusWidget() is window._move_later_button

            operation = window._begin_long_operation(_LongOperationKind.IMPORT)
            worker.state_changed.emit(_state(autosave_pending=True))
            worker.failed.emit(
                UiFailure(
                    "PWS_TEST_FAILURE",
                    "Retry the saved organizer change.",
                    True,
                    operation.operation_id,
                )
            )
            app.processEvents()
            organizer_controls = (
                window._import_button,
                window._close_case_button,
                window._page_view,
                window._move_earlier_button,
                window._move_later_button,
                window._rotate_button,
                window._remove_button,
                window._change_order_button,
                window._undo_button,
                window._redo_button,
                window._status_surface,
                window._cancel_button,
                window._retry_save_button,
            )
            assert scroll.verticalScrollBar().maximum() > 0
            for organizer_widget in organizer_controls:
                assert organizer_widget.isVisible()
                scroll.ensureWidgetVisible(organizer_widget, 8, 8)
                app.processEvents()
                organizer_origin = organizer_widget.mapTo(scroll.viewport(), QPoint(0, 0))
                assert QRect(organizer_origin, organizer_widget.size()).intersects(
                    scroll.viewport().rect()
                )
            assert window._import_button.maximumHeight() >= 16_777_215
    finally:
        app.setFont(original_font)

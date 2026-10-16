"""Qt window lifecycle evidence for the PWS-0406 constraint editor."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import cast
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QLineEdit, QPushButton

from paperworkshell.app.organizer import (
    OrganizerBackend,
    OrganizerPageView,
    OrganizerViewState,
)
from paperworkshell.app.portal_constraints import (
    ConstraintEditorState,
    PortalConstraintWorkflowService,
)
from paperworkshell.domain.manifest_repository import (
    CURRENT_MANIFEST_SCHEMA_VERSION,
    ManifestSnapshot,
)
from paperworkshell.templates.catalog import (
    GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
    built_in_template_repository,
)
from paperworkshell.templates.loader import SafeTemplateLoader
from paperworkshell.ui.backend import (
    OrganizerBackendCommands,
    OrganizerBackendWorker,
    ShutdownRefusalKind,
    UiFailure,
)
from paperworkshell.ui.constraints import ConstraintEditorDialog
from paperworkshell.ui.window import PaperWorkShellWindow

CASE_ID = UUID("81000000-0000-4000-8000-000000000101")
PAGE_ID = UUID("82000000-0000-4000-8000-000000000101")
EDITOR_ID = UUID("83000000-0000-4000-8000-000000000101")
PRIVATE_ANNOUNCEMENT = "PRIVATE_CONSTRAINT_ANNOUNCEMENT_C:\\Applicant\\packet.pdf"


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def _unused_backend() -> OrganizerBackend:
    raise AssertionError("this presentation test does not construct an organizer backend")


def _editor() -> ConstraintEditorState:
    document: dict[str, object] = {
        "schema_version": CURRENT_MANIFEST_SCHEMA_VERSION,
        "case_id": str(CASE_ID),
        "revision": 4,
        "created_at": "2026-08-28T10:00:00Z",
        "updated_at": "2026-08-28T10:01:00Z",
        "template_ref": None,
        "sources": [],
        "pages": [],
        "page_sequence_base": [],
        "page_sequence": [],
        "page_operations": [],
        "assets": [],
        "overlays": [],
        "redaction_marks": [],
        "assignments": [],
        "constraints": {
            "constraints_version": 1,
            "allowed_output_modes": ["combined_pdf"],
            "encryption_policy": "must_be_unencrypted",
        },
        "confirmations": [],
        "export_history": {"storage_name": "export-history.json", "entries": []},
    }
    snapshot = ManifestSnapshot(
        CASE_ID, CURRENT_MANIFEST_SCHEMA_VERSION, 4, document, "a" * 64, "b" * 64
    )
    workflow = PortalConstraintWorkflowService(
        SafeTemplateLoader(built_in_template_repository()),
        GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
        as_of=date(2026, 8, 28),
        id_factory=lambda: EDITOR_ID,
    )
    return workflow.begin(snapshot)


def _organizer_state(
    editor: ConstraintEditorState,
    *,
    revision: int | None = None,
) -> OrganizerViewState:
    return OrganizerViewState(
        editor.case_id,
        editor.manifest_revision if revision is None else revision,
        (OrganizerPageView(PAGE_ID, 1, 0, False),),
        False,
        False,
        False,
    )


@contextmanager
def _window() -> Iterator[
    tuple[PaperWorkShellWindow, OrganizerBackendCommands, OrganizerBackendWorker]
]:
    app = _application()
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(_unused_backend)
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


def _publish_ready(
    window: PaperWorkShellWindow,
    worker: OrganizerBackendWorker,
    editor: ConstraintEditorState,
) -> QPushButton:
    worker.initialized.emit()
    worker.state_changed.emit(_organizer_state(editor))
    _application().processEvents()
    button = window.findChild(QPushButton, "portalConstraintsButton")
    assert button is not None and button.isEnabled()
    return button


def _press(button: QPushButton) -> None:
    button.setFocus(Qt.FocusReason.OtherFocusReason)
    QTest.keyClick(button, Qt.Key.Key_Space)
    _application().processEvents()


def _open_dialog(
    window: PaperWorkShellWindow,
    worker: OrganizerBackendWorker,
    editor: ConstraintEditorState,
    begin_requests: list[int],
) -> ConstraintEditorDialog:
    button = _publish_ready(window, worker, editor)
    _press(button)
    assert len(begin_requests) == 1
    worker.portal_constraints_started.emit(begin_requests[0], editor)
    _application().processEvents()
    dialog = window.findChild(ConstraintEditorDialog, "portalConstraintEditorDialog")
    assert dialog is window._portal_constraints_dialog
    assert dialog is not None and dialog.isVisible()
    return dialog


def test_window_keyboard_cancel_waits_for_backend_before_restoring_focus() -> None:
    editor = _editor()
    with _window() as (window, commands, worker):
        begin_requests: list[int] = []
        cancel_requests: list[tuple[int, object]] = []
        commands.portal_constraints_begin_requested.disconnect(worker.begin_portal_constraints)
        commands.portal_constraints_cancel_requested.disconnect(worker.cancel_portal_constraints)
        commands.portal_constraints_begin_requested.connect(begin_requests.append)
        commands.portal_constraints_cancel_requested.connect(
            lambda operation_id, state: cancel_requests.append((operation_id, state))
        )

        dialog = _open_dialog(window, worker, editor, begin_requests)
        button = window.findChild(QPushButton, "portalConstraintsButton")
        assert button is not None
        dialog.reject()
        _application().processEvents()

        assert len(cancel_requests) == 1
        cancel_operation_id, cancelled_state = cancel_requests[0]
        assert cancelled_state is editor
        assert not button.isEnabled()

        worker.portal_constraints_cancelled.emit(cancel_operation_id)
        _application().processEvents()
        assert button.isEnabled()
        assert button.hasFocus()


def test_window_apply_closes_only_after_correlated_saved_state() -> None:
    editor = _editor()
    with _window() as (window, commands, worker):
        begin_requests: list[int] = []
        apply_requests: list[tuple[int, ConstraintEditorState]] = []
        commands.portal_constraints_begin_requested.disconnect(worker.begin_portal_constraints)
        commands.portal_constraints_apply_requested.disconnect(worker.apply_portal_constraints)
        commands.portal_constraints_begin_requested.connect(begin_requests.append)
        commands.portal_constraints_apply_requested.connect(
            lambda operation_id, state: apply_requests.append((operation_id, state))
        )
        dialog = _open_dialog(window, worker, editor, begin_requests)

        zip_mode = dialog.findChild(QCheckBox, "constraintZipCheck")
        apply_button = dialog.findChild(QPushButton, "constraintApplyButton")
        assert zip_mode is not None and apply_button is not None
        zip_mode.setChecked(True)
        _press(apply_button)

        assert len(apply_requests) == 1
        operation_id, requested = apply_requests[0]
        assert requested is dialog.state
        assert requested.changed
        assert dialog.apply_pending

        saved = _organizer_state(editor, revision=editor.manifest_revision + 1)
        worker.state_changed.emit(saved)
        worker.portal_constraints_applied.emit(operation_id, saved)
        _application().processEvents()

        button = window.findChild(QPushButton, "portalConstraintsButton")
        assert window._portal_constraints_dialog is None
        assert button is not None and button.isEnabled() and button.hasFocus()


def test_window_aborts_forged_constraint_announcement_without_rendering_it() -> None:
    editor = _editor()
    forged = editor
    object.__setattr__(forged, "announcement", PRIVATE_ANNOUNCEMENT)
    with _window() as (window, commands, worker):
        begin_requests: list[int] = []
        abort_requests: list[int] = []
        commands.portal_constraints_begin_requested.disconnect(worker.begin_portal_constraints)
        commands.portal_constraints_abort_requested.disconnect(worker.abort_portal_constraints)
        commands.portal_constraints_begin_requested.connect(begin_requests.append)
        commands.portal_constraints_abort_requested.connect(abort_requests.append)
        _publish_ready(window, worker, editor)

        window._request_portal_constraints()
        worker.portal_constraints_started.emit(begin_requests[0], forged)
        _application().processEvents()

        assert window._portal_constraints_dialog is None
        assert len(abort_requests) == 1
        assert PRIVATE_ANNOUNCEMENT not in window._status_label.text()
        assert PRIVATE_ANNOUNCEMENT not in window._error_label.text()


def test_shutdown_validates_and_queues_latest_retained_constraint_draft_first() -> None:
    editor = _editor()
    with _window() as (window, commands, worker):
        begin_requests: list[int] = []
        ordered_requests: list[tuple[str, object]] = []
        commands.portal_constraints_begin_requested.disconnect(worker.begin_portal_constraints)
        commands.portal_constraints_apply_requested.disconnect(worker.apply_portal_constraints)
        commands.shutdown_requested.disconnect(worker.shutdown)
        commands.portal_constraints_begin_requested.connect(begin_requests.append)
        commands.portal_constraints_apply_requested.connect(
            lambda _operation_id, state: ordered_requests.append(("apply", state))
        )
        commands.shutdown_requested.connect(lambda: ordered_requests.append(("shutdown", object())))
        dialog = _open_dialog(window, worker, editor, begin_requests)

        zip_mode = dialog.findChild(QCheckBox, "constraintZipCheck")
        apply_button = dialog.findChild(QPushButton, "constraintApplyButton")
        count_enabled = dialog.findChild(QCheckBox, "constraintFileCountEnabled")
        count_input = dialog.findChild(QLineEdit, "constraintFileCountInput")
        assert all(
            widget is not None for widget in (zip_mode, apply_button, count_enabled, count_input)
        )
        assert zip_mode is not None
        assert apply_button is not None
        assert count_enabled is not None
        assert count_input is not None

        zip_mode.setChecked(True)
        _press(apply_button)
        first_operation_id = window._portal_constraints_apply_operation_id
        assert first_operation_id is not None
        first_draft = cast(ConstraintEditorState, ordered_requests[0][1])
        worker.portal_constraints_apply_failed.emit(
            first_operation_id,
            UiFailure("PWS_CONSTRAINT_SAVE_FAILED", "Try saving again.", True),
        )
        _application().processEvents()
        assert not dialog.apply_pending and dialog.commit_started

        count_enabled.setChecked(True)
        count_input.setText("2")
        window.request_shutdown()

        assert [kind for kind, _value in ordered_requests] == [
            "apply",
            "apply",
            "shutdown",
        ]
        latest = cast(ConstraintEditorState, ordered_requests[1][1])
        assert latest is not first_draft
        assert latest.constraints.maximum_file_count == 2
        assert latest.changed


def test_invalid_live_constraint_field_blocks_shutdown_and_focuses_recovery() -> None:
    editor = _editor()
    with _window() as (window, commands, worker):
        begin_requests: list[int] = []
        shutdown_requests: list[bool] = []
        commands.portal_constraints_begin_requested.disconnect(worker.begin_portal_constraints)
        commands.shutdown_requested.disconnect(worker.shutdown)
        commands.portal_constraints_begin_requested.connect(begin_requests.append)
        commands.shutdown_requested.connect(lambda: shutdown_requests.append(True))
        dialog = _open_dialog(window, worker, editor, begin_requests)

        count_enabled = dialog.findChild(QCheckBox, "constraintFileCountEnabled")
        count_input = dialog.findChild(QLineEdit, "constraintFileCountInput")
        assert count_enabled is not None and count_input is not None
        count_enabled.setChecked(True)
        count_input.clear()

        window.request_shutdown()
        _application().processEvents()

        assert shutdown_requests == []
        assert not window._closing_requested
        assert window._portal_constraints_dialog is dialog
        assert dialog.isVisible()
        assert dialog.focusWidget() is count_input


def test_shutdown_refusal_dismisses_constraint_retry_and_focuses_save_retry() -> None:
    editor = _editor()
    with _window() as (window, commands, worker):
        begin_requests: list[int] = []
        apply_requests: list[tuple[int, object]] = []
        commands.portal_constraints_begin_requested.disconnect(worker.begin_portal_constraints)
        commands.portal_constraints_apply_requested.disconnect(worker.apply_portal_constraints)
        commands.portal_constraints_begin_requested.connect(begin_requests.append)
        commands.portal_constraints_apply_requested.connect(
            lambda operation_id, state: apply_requests.append((operation_id, state))
        )
        dialog = _open_dialog(window, worker, editor, begin_requests)
        apply_button = dialog.findChild(QPushButton, "constraintApplyButton")
        assert apply_button is not None

        _press(apply_button)
        assert apply_requests and dialog.apply_pending
        window._closing_requested = True
        window._shutdown_refused(ShutdownRefusalKind.SAVE_PENDING)
        _application().processEvents()

        assert window._portal_constraints_dialog is None
        assert window._portal_constraints_apply_operation_id is None
        assert window._retry_save_button.isVisible()
        assert window._retry_save_button.hasFocus()

"""Thread and main-window evidence for saving inert PWS-0602 removal marks."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import cast
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from paperworkshell.app.organizer import OrganizerBackend, OrganizerPageView, OrganizerViewState
from paperworkshell.app.redaction_tools import RedactionToolsState
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.redaction import RedactionError, RedactionErrorCode
from paperworkshell.ui.backend import (
    OrganizerBackendCommands,
    OrganizerBackendWorker,
    ShutdownRefusalKind,
    UiFailure,
    sanitize_ui_failure,
)
from paperworkshell.ui.window import PaperWorkShellWindow

ROOT = Path(__file__).resolve().parents[2]
REPRESENTATIVE = ROOT / "schemas" / "examples" / "case-manifest-v3-representative.json"


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def _unused_backend() -> OrganizerBackend:
    raise AssertionError("presentation test must not construct an organizer backend")


def _editor() -> RedactionToolsState:
    document = cast(dict[str, object], json.loads(REPRESENTATIVE.read_text(encoding="utf-8")))
    document["assets"] = []
    document["overlays"] = []
    snapshot = ManifestSnapshot(
        UUID(cast(str, document["case_id"])),
        cast(int, document["schema_version"]),
        cast(int, document["revision"]),
        document,
        "d" * 64,
        "redaction_window_test_cas",
    )
    return RedactionToolsState.from_snapshot(
        snapshot, UUID(cast(list[str], document["page_sequence"])[0])
    )


def _organizer_state(
    editor: RedactionToolsState, *, revision: int | None = None
) -> OrganizerViewState:
    return OrganizerViewState(
        editor.case_id,
        editor.manifest_revision if revision is None else revision,
        (OrganizerPageView(editor.page_id, 1, 0, True),),
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


def _open(
    window: PaperWorkShellWindow,
    commands: OrganizerBackendCommands,
    worker: OrganizerBackendWorker,
    editor: RedactionToolsState,
) -> None:
    requests: list[tuple[int, object]] = []
    commands.redaction_tools_begin_requested.disconnect(worker.begin_redaction_tools)
    commands.redaction_tools_begin_requested.connect(
        lambda operation_id, page_id: requests.append((operation_id, page_id))
    )
    worker.initialized.emit()
    worker.state_changed.emit(_organizer_state(editor))
    _application().processEvents()
    window._preview_refresh_timer.stop()
    QTest.keyClick(
        window,
        Qt.Key.Key_P,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    _application().processEvents()
    assert len(requests) == 1 and requests[0][1] == editor.page_id
    worker.redaction_tools_started.emit(requests[0][0], editor)
    _application().processEvents()
    assert window._redaction_tools_dialog is not None


def test_window_exposes_distinct_action_and_excludes_overlapping_mutations_until_cancel() -> None:
    editor = _editor()
    with _window() as (window, commands, worker):
        cancellations: list[tuple[int, object]] = []
        commands.redaction_tools_cancel_requested.disconnect(worker.cancel_redaction_tools)
        commands.redaction_tools_cancel_requested.connect(
            lambda operation_id, state: cancellations.append((operation_id, state))
        )
        _open(window, commands, worker, editor)
        button = window.findChild(QPushButton, "redactionToolsButton")
        assert button is not None
        assert button.text() == "Permanently remove information…"
        assert button.accessibleName() == "Permanently remove information"
        assert "Save marks only" in button.accessibleDescription()
        assert not window._core_overlay_tools_button.isEnabled()
        assert not window._package_export_button.isEnabled()
        assert not window._import_button.isEnabled()
        assert not window._quality_check_button.isEnabled()
        assert not window._preview_refresh_timer.isActive()
        dialog = window._redaction_tools_dialog
        assert dialog is not None
        dialog.reject()
        _application().processEvents()
        assert len(cancellations) == 1
        assert not button.isEnabled()
        worker.redaction_tools_cancelled.emit(cancellations[0][0])
        _application().processEvents()
        assert button.isEnabled() and button.hasFocus()
        assert "No information has been removed" in window._status_label.text()


def test_window_accepts_only_correlated_save_and_never_claims_removal() -> None:
    editor = _editor().create()
    with _window() as (window, commands, worker):
        saves: list[tuple[int, object]] = []
        commands.redaction_tools_save_requested.disconnect(worker.save_redaction_tools)
        commands.redaction_tools_save_requested.connect(
            lambda operation_id, state: saves.append((operation_id, state))
        )
        _open(window, commands, worker, editor)
        dialog = window._redaction_tools_dialog
        assert dialog is not None
        dialog.save_requested.emit(editor)
        assert saves == [(saves[0][0], editor)] and bool(dialog.save_pending)
        operation_id = saves[0][0]
        worker.redaction_tools_saved.emit(operation_id + 1, _organizer_state(editor))
        assert bool(dialog.save_pending)
        worker.redaction_tools_saved.emit(operation_id, object())
        assert not bool(dialog.save_pending)
        assert cast(object, window._redaction_tools_dialog) is dialog
        dialog.save_requested.emit(editor)
        result = _organizer_state(editor, revision=editor.manifest_revision + 1)
        worker.state_changed.emit(result)
        worker.redaction_tools_saved.emit(saves[-1][0], result)
        _application().processEvents()
        assert cast(object, window._redaction_tools_dialog) is None
        assert (
            "Redaction marks saved. No information has been removed." in window._status_label.text()
        )


def test_forged_start_announcement_aborts_authority_without_displaying_content() -> None:
    editor = _editor()
    forged = replace(editor)
    object.__setattr__(forged, "announcement", "PRIVATE-TOKEN-C:\\Sensitive")
    with _window() as (window, commands, worker):
        beginnings: list[tuple[int, object]] = []
        aborts: list[int] = []
        commands.redaction_tools_begin_requested.disconnect(worker.begin_redaction_tools)
        commands.redaction_tools_abort_requested.disconnect(worker.abort_redaction_tools)
        commands.redaction_tools_begin_requested.connect(
            lambda operation_id, page_id: beginnings.append((operation_id, page_id))
        )
        commands.redaction_tools_abort_requested.connect(aborts.append)
        worker.initialized.emit()
        worker.state_changed.emit(_organizer_state(editor))
        window._request_redaction_tools()
        worker.redaction_tools_started.emit(beginnings[0][0], forged)
        assert cast(object, window._redaction_tools_dialog) is None and len(aborts) == 1
        assert "PRIVATE-TOKEN" not in window._status_label.text() + window._error_label.text()


def test_malformed_save_failure_keeps_safe_retry_and_shutdown_refusal_restores_retry_surface() -> (
    None
):
    editor = _editor().create()
    with _window() as (window, commands, worker):
        saves: list[tuple[int, object]] = []
        commands.redaction_tools_save_requested.disconnect(worker.save_redaction_tools)
        commands.redaction_tools_save_requested.connect(
            lambda operation_id, state: saves.append((operation_id, state))
        )
        _open(window, commands, worker, editor)
        dialog = window._redaction_tools_dialog
        assert dialog is not None
        dialog.save_requested.emit(editor)
        worker.redaction_tools_save_failed.emit(saves[-1][0], object())
        assert not bool(dialog.save_pending)
        assert cast(object, window._redaction_tools_dialog) is dialog
        dialog.save_requested.emit(editor)
        assert bool(dialog.save_pending)
        window._closing_requested = True
        window._shutdown_refused(ShutdownRefusalKind.SAVE_PENDING)
        _application().processEvents()
        assert cast(object, window._redaction_tools_dialog) is None
        assert window._redaction_tools_save_operation_id is None
        assert window._retry_save_button.isVisible() and window._retry_save_button.hasFocus()


def test_shutdown_retries_exact_failed_save_draft_before_backend_shutdown() -> None:
    editor = _editor().create()
    with _window() as (window, commands, worker):
        requests: list[tuple[str, object]] = []
        commands.redaction_tools_save_requested.disconnect(worker.save_redaction_tools)
        commands.shutdown_requested.disconnect(worker.shutdown)
        commands.redaction_tools_save_requested.connect(
            lambda _operation_id, state: requests.append(("save", state))
        )
        commands.shutdown_requested.connect(lambda: requests.append(("shutdown", None)))
        _open(window, commands, worker, editor)
        dialog = window._redaction_tools_dialog
        assert dialog is not None
        dialog.save_requested.emit(editor)
        dialog.save_failed("MANIFEST_SAVE_FAILED", "Try saving again.")
        window._redaction_tools_save_operation_id = None
        window.request_shutdown()
        assert requests == [("save", editor), ("save", editor), ("shutdown", None)]


class _BeginBackend:
    def __init__(self, editor: RedactionToolsState, state: OrganizerViewState) -> None:
        self.editor = editor
        self.state = state
        self.aborted = False

    def abort_redaction_tools(self) -> None:
        self.aborted = True

    def begin_redaction_tools(self, page_id: UUID) -> RedactionToolsState:
        assert page_id == self.editor.page_id
        return self.editor


class _SaveBackend:
    def __init__(self, state: OrganizerViewState) -> None:
        self.state = state

    def save_redaction_tools(self, _editor: RedactionToolsState) -> OrganizerViewState:
        return self.state


def test_worker_publishes_flushed_state_before_editor_and_refuses_uncorrelated_begin() -> None:
    editor = _editor()
    backend = _BeginBackend(editor, _organizer_state(editor))
    worker = OrganizerBackendWorker(_unused_backend)
    worker._backend = cast(OrganizerBackend, backend)
    events: list[tuple[str, object]] = []
    worker.state_changed.connect(lambda state: events.append(("state", state)))
    worker.redaction_tools_started.connect(
        lambda _operation_id, state: events.append(("started", state))
    )
    worker.redaction_tools_start_failed.connect(
        lambda _operation_id, failure: events.append(("failure", failure))
    )
    worker.begin_redaction_tools(1, editor.page_id)
    assert events == [("state", backend.state), ("started", editor)]
    events.clear()
    backend.state = replace(backend.state, revision=editor.manifest_revision + 1)
    worker.begin_redaction_tools(2, editor.page_id)
    assert len(events) == 1 and events[0][0] == "failure"
    assert backend.aborted


def test_worker_save_rejects_wrong_case_revision_page_and_pending_results() -> None:
    editor = _editor().create()
    expected = _organizer_state(editor, revision=editor.manifest_revision + 1)
    backend = _SaveBackend(expected)
    worker = OrganizerBackendWorker(_unused_backend)
    worker._backend = cast(OrganizerBackend, backend)
    results: list[tuple[int, object]] = []
    failures: list[tuple[int, object]] = []
    worker.redaction_tools_saved.connect(
        lambda operation_id, state: results.append((operation_id, state))
    )
    worker.redaction_tools_save_failed.connect(
        lambda operation_id, failure: failures.append((operation_id, failure))
    )
    invalid = (
        replace(expected, case_id=UUID("10000000-0000-4000-8000-000000000099")),
        replace(expected, revision=editor.manifest_revision),
        replace(expected, pages=()),
        replace(expected, autosave_pending=True),
    )
    for operation_id, state in enumerate(invalid, 1):
        backend.state = state
        worker.save_redaction_tools(operation_id, editor)
    assert not results and len(failures) == len(invalid)
    backend.state = expected
    worker.save_redaction_tools(9, editor)
    assert results == [(9, expected)]


def test_redaction_error_sanitization_rebuilds_closed_message() -> None:
    error = RedactionError(RedactionErrorCode.CONCURRENT_CHANGE, "redaction.editor")
    error.safe_message = "PRIVATE-TOKEN-C:\\Sensitive"
    failure = sanitize_ui_failure(error, operation_id=7)
    assert type(failure) is UiFailure
    assert failure.code == RedactionErrorCode.CONCURRENT_CHANGE.value
    assert failure.operation_id == 7
    assert "PRIVATE-TOKEN" not in failure.message


class _CancelBackend:
    def __init__(self) -> None:
        self.cancelled: list[RedactionToolsState] = []
        self.aborted = False

    def cancel_redaction_tools(self, editor: RedactionToolsState) -> None:
        self.cancelled.append(editor)

    def abort_redaction_tools(self) -> None:
        self.aborted = True


def test_worker_validates_cancel_and_abort_requests_before_releasing_authority() -> None:
    editor = _editor()
    backend = _CancelBackend()
    worker = OrganizerBackendWorker(_unused_backend)
    worker._backend = cast(OrganizerBackend, backend)
    cancelled: list[int] = []
    failures: list[tuple[int, object]] = []
    worker.redaction_tools_cancelled.connect(cancelled.append)
    worker.redaction_tools_cancel_failed.connect(
        lambda operation_id, failure: failures.append((operation_id, failure))
    )
    worker.cancel_redaction_tools(0, editor)
    worker.cancel_redaction_tools(1, object())
    worker.abort_redaction_tools(0)
    assert not backend.cancelled and not bool(backend.aborted)
    assert len(failures) == 3 and not cancelled
    worker.cancel_redaction_tools(2, editor)
    worker.abort_redaction_tools(3)
    assert backend.cancelled == [editor] and backend.aborted
    assert cancelled == [2, 3]


def test_start_failure_uses_fixed_recovery_message_without_reflecting_backend_values() -> None:
    editor = _editor()
    with _window() as (window, commands, worker):
        beginnings: list[tuple[int, object]] = []
        commands.redaction_tools_begin_requested.disconnect(worker.begin_redaction_tools)
        commands.redaction_tools_begin_requested.connect(
            lambda operation_id, page_id: beginnings.append((operation_id, page_id))
        )
        worker.initialized.emit()
        worker.state_changed.emit(_organizer_state(editor))
        window._request_redaction_tools()
        worker.redaction_tools_start_failed.emit(
            beginnings[0][0], UiFailure("PRIVATE-TOKEN", "PRIVATE-TOKEN-C:\\Sensitive", True)
        )
        assert "PRIVATE-TOKEN" not in window._error_label.text()
        assert "No information has been removed" in window._error_label.text()
        assert window._redaction_tools_button.isEnabled()

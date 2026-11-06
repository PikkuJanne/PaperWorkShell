"""Current-request binding and explicit consent for optional post-export work."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QLabel

from paperworkshell.app.organizer import OrganizerBackend, OrganizerViewState
from paperworkshell.domain.assisted_use import AssistedUseSnapshot, AssistedUseStatus
from paperworkshell.domain.case_lifecycle import (
    CaseDeletionResult,
    CaseDeletionStatus,
    CaseLifecycleErrorCode,
)
from paperworkshell.domain.package_summary import (
    PackageSummary,
    PackageSummaryOutput,
    PackageSummaryOutputState,
    PackageSummaryStatus,
)
from paperworkshell.domain.post_export_deletion import (
    PostExportCaseInventory,
    PostExportDeletionInventory,
)
from paperworkshell.ui.backend import (
    OperationCancellation,
    OrganizerBackendCommands,
    OrganizerBackendWorker,
    UiFailure,
)
from paperworkshell.ui.package_export import PackageExportDialog
from paperworkshell.ui.window import PaperWorkShellWindow
from tests.unit.test_package_export_window import CASE_ID, _application, _open, _press, _window

_REVIEW_ID = UUID("a7000000-0000-4000-8000-000000000706")


def _inventory() -> PostExportDeletionInventory:
    return PostExportDeletionInventory(
        _REVIEW_ID,
        CASE_ID,
        3,
        PostExportCaseInventory(1, 1, 0, 0, 1_000, 20, 30, 10),
        1,
        ("packet.pdf",),
    )


def _assisted(window: PaperWorkShellWindow) -> None:
    window._commands.assisted_check_requested.disconnect(window._worker.check_assisted_use)
    window._assisted_session._snapshot = AssistedUseSnapshot(
        CASE_ID, AssistedUseStatus.UNLOCKED, datetime.now(UTC) + timedelta(days=7), 7
    )


@contextmanager
def _terminal() -> Iterator[
    tuple[
        PaperWorkShellWindow, OrganizerBackendCommands, OrganizerBackendWorker, PackageExportDialog
    ]
]:
    with _window() as (window, commands, worker):
        commands.post_export_deletion_review_requested.disconnect(worker.begin_post_export_deletion)
        commands.post_export_deletion_confirm_requested.disconnect(
            worker.confirm_post_export_deletion
        )
        commands.post_export_deletion_cancel_requested.disconnect(
            worker.cancel_post_export_deletion
        )
        commands.text_companion_save_requested.disconnect(worker.save_text_companion)
        dialog = _open(window, commands, worker)
        window._package_export_operation_id = 706
        worker.package_export_ready.emit(
            706,
            PackageSummary(
                PackageSummaryStatus.COMMITTED,
                (PackageSummaryOutput("packet.pdf", PackageSummaryOutputState.COMMITTED, 1, 100),),
                active_page_count=1,
            ),
        )
        try:
            yield window, commands, worker, dialog
        finally:
            window._assisted_session.changed(None)
            if window._post_export_deletion_dialog is not None:
                window._post_export_deletion_dialog.reject()


def _review(
    window: PaperWorkShellWindow,
    commands: OrganizerBackendCommands,
    worker: OrganizerBackendWorker,
    dialog: PackageExportDialog,
) -> tuple[int, OperationCancellation]:
    requests: list[tuple[int, OperationCancellation]] = []
    commands.post_export_deletion_review_requested.connect(lambda *args: requests.append(args))
    _press(dialog._delete_case)
    assert len(requests) == 1
    operation, cancellation = requests[0]
    assert dialog.busy and not cancellation.cancelled()
    worker.post_export_deletion_review_ready.emit(operation, _inventory())
    _application().processEvents()
    assert window._post_export_deletion_dialog is not None
    return operation, cancellation


@pytest.mark.parametrize(
    "status",
    (
        PackageSummaryStatus.PLANNED,
        PackageSummaryStatus.NEEDS_REVIEW,
        PackageSummaryStatus.NOT_COMMITTED,
        PackageSummaryStatus.PARTIAL,
        PackageSummaryStatus.UNCERTAIN,
    ),
)
def test_only_a_committed_summary_can_request_deletion(status: PackageSummaryStatus) -> None:
    with _terminal() as (window, commands, _worker, dialog):
        dialog._summary = PackageSummary(status)
        requests: list[object] = []
        commands.post_export_deletion_review_requested.connect(lambda *args: requests.append(args))
        window._request_post_export_deletion()
        assert requests == []
        assert window._post_export_review_operation_id is None


@pytest.mark.parametrize("action", ("keep", "escape", "unchecked_accept"))
def test_inventory_requires_checked_consent_and_a_separate_delete_action(action: str) -> None:
    with _terminal() as (window, commands, worker, parent):
        confirmations: list[object] = []
        cancellations: list[bool] = []
        commands.post_export_deletion_confirm_requested.connect(
            lambda *args: confirmations.append(args)
        )
        commands.post_export_deletion_cancel_requested.connect(lambda: cancellations.append(True))
        _review(window, commands, worker, parent)
        dialog = window._post_export_deletion_dialog
        assert dialog is not None
        assert not dialog.confirmed and not dialog._remove.isEnabled()
        assert dialog._keep.isDefault()
        if action == "unchecked_accept":
            dialog.accept()
        elif action == "escape":
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
        else:
            _press(dialog._keep)
        assert confirmations == [] and cancellations == [True]
        assert window._post_export_deletion_dialog is None
        assert not bool(parent.busy)
        assert "Case kept" in parent._status.text()


@pytest.mark.parametrize("partial", (False, True))
@pytest.mark.parametrize("locked_before_result", (False, True))
def test_explicit_delete_keeps_the_actual_result_readable_after_case_closure(
    partial: bool, locked_before_result: bool
) -> None:
    with _terminal() as (window, commands, worker, parent):
        if locked_before_result:
            _assisted(window)
        confirmations: list[tuple[int, object]] = []
        commands.post_export_deletion_confirm_requested.connect(
            lambda *args: confirmations.append(args)
        )
        _review(window, commands, worker, parent)
        dialog = window._post_export_deletion_dialog
        assert dialog is not None
        dialog._ack.setChecked(True)
        _press(dialog._remove)
        assert len(confirmations) == 1
        operation, review_id = confirmations[0]
        assert review_id == _REVIEW_ID
        assert bool(parent.busy) and not parent._cancel.isEnabled()
        if locked_before_result:
            window._assisted_session.cover()
        worker.state_changed.emit(OrganizerViewState.empty())
        if locked_before_result:
            worker.assisted_use_changed.emit(None)
            assert window._package_export_dialog is None
            assert not parent.isVisible()
        result = CaseDeletionResult(
            CASE_ID,
            CaseDeletionStatus.PARTIAL if partial else CaseDeletionStatus.COMPLETE,
            1,
            0,
            100,
            failure_code=CaseLifecycleErrorCode.DELETE_FAILED if partial else None,
        )
        worker.post_export_deletion_finished.emit(operation + 1, result)
        if not locked_before_result:
            assert bool(parent.busy)
        worker.post_export_deletion_finished.emit(operation, result)
        if locked_before_result:
            assert window._package_export_dialog is None
            assert not parent.isVisible()
            assert not bool(window._post_export_pending_feedback)
            status = window._status_label.text()
            assert "packet.pdf" not in status
        else:
            assert not bool(parent.busy) and parent.isVisible()
            assert (
                parent.summary is not None
                and parent.summary.status is PackageSummaryStatus.COMMITTED
            )
            assert not parent._delete_case.isVisible() and not parent._companion.isVisible()
            status = parent._status.text()
        assert "Forensic erasure is not guaranteed" in status
        assert "remain" in status
        assert ("incomplete" in status) is partial
        if partial:
            assert str(CASE_ID) in status


def test_case_revision_change_after_inventory_discards_checked_confirmation() -> None:
    with _terminal() as (window, commands, worker, parent):
        confirmations: list[object] = []
        commands.post_export_deletion_confirm_requested.connect(
            lambda *args: confirmations.append(args)
        )
        _review(window, commands, worker, parent)
        dialog = window._post_export_deletion_dialog
        assert dialog is not None and window._state is not None
        dialog._ack.setChecked(True)
        worker.state_changed.emit(replace(window._state, revision=4))
        _press(dialog._remove)
        assert confirmations == [] and window._post_export_deletion_dialog is None
        assert "Case kept" in parent._status.text()


def test_cancelled_inventory_preparation_discards_its_late_result() -> None:
    with _terminal() as (window, commands, worker, parent):
        requests: list[tuple[int, OperationCancellation]] = []
        commands.post_export_deletion_review_requested.connect(lambda *args: requests.append(args))
        _press(parent._delete_case)
        operation, cancellation = requests[0]
        _press(parent._cancel)
        assert cancellation.cancelled()
        worker.post_export_deletion_review_ready.emit(operation, _inventory())
        assert window._post_export_deletion_dialog is None
        assert not bool(parent.busy) and "cancelled" in parent._status.text()


@pytest.mark.parametrize("changed", ("revision", "case", "request"))
def test_inventory_callbacks_cannot_replace_the_bound_current_case(changed: str) -> None:
    with _terminal() as (window, commands, worker, parent):
        requests: list[tuple[int, object]] = []
        commands.post_export_deletion_review_requested.connect(lambda *args: requests.append(args))
        _press(parent._delete_case)
        operation = requests[0][0]
        review = _inventory()
        if changed == "revision":
            review = replace(review, revision=4)
        elif changed == "case":
            review = replace(review, case_id=UUID("a7000000-0000-4000-8000-000000000707"))
        else:
            operation += 1
        worker.post_export_deletion_review_ready.emit(operation, review)
        assert window._post_export_deletion_dialog is None
        assert window._post_export_delete_operation_id is None


@pytest.mark.parametrize("pending", (True, False))
def test_privacy_lock_cancels_review_and_cannot_restore_destructive_consent(pending: bool) -> None:
    with _terminal() as (window, commands, worker, parent):
        _assisted(window)
        requests: list[tuple[int, OperationCancellation]] = []
        confirmations: list[object] = []
        commands.post_export_deletion_review_requested.connect(lambda *args: requests.append(args))
        commands.post_export_deletion_confirm_requested.connect(
            lambda *args: confirmations.append(args)
        )
        parent._text_privacy.setChecked(True)
        _press(parent._delete_case)
        operation, cancellation = requests[0]
        if not pending:
            worker.post_export_deletion_review_ready.emit(operation, _inventory())
            review_dialog = window._post_export_deletion_dialog
            assert review_dialog is not None
            review_dialog._ack.setChecked(True)
        window._assisted_session.cover()
        assert pending is False or cancellation.cancelled()
        assert not parent.text_privacy_confirmed
        assert window._post_export_review_operation_id is None
        assert window._post_export_deletion_dialog is None
        window._assisted_session._uncover()
        if not pending:
            assert review_dialog is not None
            assert review_dialog not in window._assisted_session._hidden
            assert not review_dialog.isVisible()
        worker.post_export_deletion_review_ready.emit(operation, _inventory())
        assert window._post_export_deletion_dialog is None and confirmations == []
        assert not bool(parent.busy)


def test_text_companion_picker_rechecks_privacy_epoch_after_delayed_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _terminal() as (window, commands, _worker, parent):
        _assisted(window)
        saves: list[object] = []
        commands.text_companion_save_requested.connect(lambda *args: saves.append(args))

        def picker(*_args: object, **_kwargs: object) -> tuple[str, str]:
            window._assisted_session.cover()
            window._assisted_session._uncover()
            return "C:/synthetic-companion.txt", ""

        monkeypatch.setattr(QFileDialog, "getSaveFileName", picker)
        parent._text_privacy.setChecked(True)
        _press(parent._companion)
        assert saves == [] and not parent.busy
        assert not parent.text_privacy_confirmed


def test_text_companion_result_is_correlated_and_preserves_the_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _terminal() as (window, commands, worker, parent):
        saves: list[tuple[int, Path, bool, OperationCancellation]] = []
        commands.text_companion_save_requested.connect(lambda *args: saves.append(args))
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: ("C:/text.txt", ""))
        summary = parent.summary
        parent._text_privacy.setChecked(True)
        _press(parent._companion)
        operation, path, consent, cancellation = saves[0]
        assert path == Path("C:/text.txt") and consent is True
        assert not cancellation.cancelled()
        worker.text_companion_saved.emit(operation + 1)
        assert bool(parent.busy)
        worker.text_companion_saved.emit(operation)
        assert not bool(parent.busy) and parent.summary is summary
        assert "Plain-text companion saved" in parent._status.text()
        assert window._text_companion_operation_id is None


def test_unknown_optional_failure_never_displays_raw_backend_message() -> None:
    with _terminal() as (window, _commands, worker, parent):
        window._text_companion_operation_id = 901
        parent.set_busy("preparing")
        worker.post_export_failed.emit(901, "text", UiFailure("UNKNOWN", "PRIVATE CONTENT", True))
        assert not bool(parent.busy)
        assert all("PRIVATE CONTENT" not in label.text() for label in parent.findChildren(QLabel))


@pytest.mark.parametrize("phase", ("inventory", "text", "delete"))
def test_shutdown_waits_for_pending_optional_operation_and_preserves_final_result(
    phase: str,
) -> None:
    with _terminal() as (window, commands, worker, parent):
        shutdowns: list[bool] = []
        commands.shutdown_requested.connect(lambda: shutdowns.append(True))
        field = {
            "inventory": "_post_export_review_operation_id",
            "text": "_text_companion_operation_id",
            "delete": "_post_export_delete_operation_id",
        }[phase]
        setattr(window, field, 902)
        window._package_cancellation = OperationCancellation()
        token = window._package_cancellation
        parent.set_busy("preparing")
        window.request_shutdown()
        assert token.cancelled() and shutdowns == [] and parent.isVisible()
        worker.post_export_failed.emit(902, phase, UiFailure("UNKNOWN", "PRIVATE", True))
        assert not bool(parent.busy) and parent.isVisible()


def test_completion_feedback_waits_for_privacy_curtain_to_be_removed() -> None:
    with _terminal() as (window, _commands, worker, parent):
        _assisted(window)
        window._text_companion_operation_id = 903
        parent.set_busy("preparing")
        window._assisted_session.cover()
        old_status = parent._status.text()
        worker.text_companion_saved.emit(903)
        assert parent._status.text() == old_status
        assert bool(window._post_export_pending_feedback)
        window._assisted_session._uncover()
        assert not bool(window._post_export_pending_feedback)
        assert "Plain-text companion saved" in parent._status.text()


def test_case_release_discards_pending_private_summary_instead_of_restoring_feedback() -> None:
    with _terminal() as (window, _commands, worker, parent):
        _assisted(window)
        window._text_companion_operation_id = 905
        parent.set_busy("preparing")
        window._assisted_session.cover()
        worker.text_companion_saved.emit(905)
        assert bool(window._post_export_pending_feedback)
        worker.assisted_use_changed.emit(None)
        assert window._package_export_dialog is None
        assert not parent.isVisible()
        assert not bool(window._post_export_pending_feedback)
        window._assisted_session._uncover()
        worker.text_companion_saved.emit(905)
        assert window._package_export_dialog is None
        assert not parent.isVisible()
        assert "packet.pdf" not in window._status_label.text()


def test_duplicate_programmatic_request_cannot_unlock_an_active_operation() -> None:
    with _terminal() as (window, commands, _worker, parent):
        requests: list[object] = []
        commands.post_export_deletion_review_requested.connect(lambda *args: requests.append(args))
        _press(parent._delete_case)
        window._request_post_export_deletion()
        assert len(requests) == 1 and parent.busy


@pytest.mark.parametrize("compressed", (False, True))
def test_export_bridge_publishes_advanced_revision_before_result_and_deletion_review(
    compressed: bool,
) -> None:
    with _terminal() as (window, commands, worker, parent):
        assert window._state is not None and parent.summary is not None
        state, summary = window._state, parent.summary
        backend = SimpleNamespace(state=state)

        def advance(*_args: object, **_kwargs: object) -> PackageSummary:
            # Saving signed-source consent can advance the case during export.
            backend.state = replace(state, revision=4)
            return summary

        backend.export_package = advance
        backend.commit_compression = advance
        worker._backend = cast(OrganizerBackend, backend)
        events: list[tuple[str, int]] = []
        worker.state_changed.connect(lambda current: events.append(("state", current.revision)))
        worker.package_export_ready.connect(
            lambda operation, _summary: events.append(("result", operation))
        )
        window._package_export_operation_id = 904
        if compressed:
            worker.commit_compression(904, _REVIEW_ID, True, OperationCancellation())
        else:
            worker.export_package(
                904, _REVIEW_ID, Path("C:/synthetic-exports"), True, True, OperationCancellation()
            )
        assert events == [("state", 4), ("result", 904)]
        assert window._state.revision == 4
        requests: list[tuple[int, OperationCancellation]] = []
        commands.post_export_deletion_review_requested.connect(lambda *args: requests.append(args))
        _press(parent._delete_case)
        worker.post_export_deletion_review_ready.emit(
            requests[0][0], replace(_inventory(), revision=4)
        )
        assert window._post_export_deletion_dialog is not None

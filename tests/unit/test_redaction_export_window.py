"""Unqualified-build reports use current intent, with no invented verification."""

from __future__ import annotations

from dataclasses import replace
from typing import cast
from uuid import UUID

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton

from paperworkshell.app.organizer import OrganizerPageView, OrganizerViewState
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.page_operations import PageOperationError
from paperworkshell.domain.redaction import RedactionMark
from paperworkshell.domain.redaction_export import RedactionExportCode
from paperworkshell.domain.redaction_verification import VerificationStatus
from paperworkshell.ui.backend import UiFailure
from paperworkshell.ui.window import _LongOperationKind
from tests.unit.test_organizer_backend import CASE_ID, _backend, _document
from tests.unit.test_package_export_window import (
    _application,
    _open,
    _press,
    _window,
)


@pytest.fixture(autouse=True)
def _unqualified_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "paperworkshell.domain.redaction_verification.QUALIFIED_RUNTIME_PROFILE", None
    )


def _marked_state(state: OrganizerViewState) -> OrganizerViewState:
    return replace(state, removal_page_ids=(state.pages[0].page_id,))


def test_organizer_projects_only_active_marked_pages_in_export_order() -> None:
    document = _document(page_count=3)
    sequence = cast(list[str], document["page_sequence"])
    first, second, third = (UUID(value) for value in sequence)
    document["redaction_marks"] = [
        RedactionMark(
            UUID(int=90 + position, version=4), page_id, NormalizedPageBox(0, 0, 100_000, 100_000)
        ).to_manifest_record()
        for position, page_id in enumerate((third, first, third))
    ]
    backend, _, _, _, _ = _backend(document)
    assert backend.open_case(CASE_ID).removal_page_ids == (first, third)
    assert backend.reorder_pages((third, second, first)).removal_page_ids == (third, first)
    assert backend.remove_page(third).removal_page_ids == (first,)
    assert backend.undo().removal_page_ids == (third, first)


@pytest.mark.parametrize(
    "marked",
    [
        [UUID(int=2, version=4)],
        (UUID(int=2, version=4), UUID(int=2, version=4)),
        (UUID(int=3, version=4),),
        ("PRIVATE",),
    ],
)
def test_organizer_rejects_forged_removal_page_projection(marked: object) -> None:
    state = OrganizerViewState(
        UUID(int=1, version=4),
        0,
        (OrganizerPageView(UUID(int=2, version=4), 1, 0, True),),
        False,
        False,
        False,
    )
    with pytest.raises(PageOperationError):
        replace(state, removal_page_ids=cast(tuple[UUID, ...], marked))


def test_normal_export_failure_waits_for_terminal_operation_then_opens_unknown_report() -> None:
    with _window() as (window, _commands, worker):
        assert window._state is not None
        worker.state_changed.emit(_marked_state(window._state))
        operation = window._begin_long_operation(_LongOperationKind.PDF_EXPORT)
        worker.failed.emit(
            UiFailure("REDACTION_PIPELINE_DISABLED", "PRIVATE", False, operation.operation_id)
        )
        assert cast(object, window._redaction_export_dialog) is None
        worker.operation_finished.emit(operation.operation_id)
        _application().processEvents()
        dialog = window._redaction_export_dialog
        assert dialog is not None and dialog.isVisible()
        assert dialog.report.code is RedactionExportCode.RELEASE_PENDING
        assert dialog.report.verification is None
        assert all(method.status is VerificationStatus.UNKNOWN for method in dialog.report.methods)
        assert "PRIVATE" not in dialog.report.user_text
        assert not window._import_button.isEnabled()
        assert not window._package_export_button.isEnabled()
        retry = dialog.findChild(QPushButton, "redactionExportRetryButton")
        assert retry is not None and not retry.isEnabled()
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        _application().processEvents()
        assert cast(object, window._redaction_export_dialog) is None
        assert window._redaction_tools_button.isEnabled()
        assert window._redaction_tools_button.hasFocus()


def test_review_marks_selects_current_affected_page_and_never_exports() -> None:
    with _window() as (window, commands, worker):
        assert window._state is not None
        state = _marked_state(window._state)
        affected = state.pages[0].page_id
        other = UUID(int=98765, version=4)
        state = replace(state, pages=(*state.pages, OrganizerPageView(other, 2, 0, False)))
        worker.state_changed.emit(state)
        window._restore_page_selection(other)
        beginnings: list[tuple[int, UUID]] = []
        commands.redaction_tools_begin_requested.disconnect(worker.begin_redaction_tools)
        commands.redaction_tools_begin_requested.connect(
            lambda operation_id, page_id: beginnings.append((operation_id, page_id))
        )
        worker.failed.emit(UiFailure("REDACTION_PIPELINE_DISABLED", "PRIVATE", False))
        _application().processEvents()
        dialog = window._redaction_export_dialog
        assert dialog is not None
        review = dialog.findChild(QPushButton, "redactionExportReviewButton")
        assert review is not None and review.hasFocus()
        _press(review)
        _application().processEvents()
        assert cast(object, window._redaction_export_dialog) is None
        assert len(beginnings) == 1 and beginnings[0][1] == affected
        assert window._selected_page_id() == affected
        assert window._redaction_tools_start_operation_id == beginnings[0][0]
        assert window._active_operation is None


@pytest.mark.parametrize(
    "phase",
    ("review", "export", "compression_prepare", "compression_preview", "compression_export"),
)
def test_package_failure_releases_authority_before_report_and_mark_review(phase: str) -> None:
    with _window() as (window, commands, worker):
        assert window._state is not None
        worker.state_changed.emit(_marked_state(window._state))
        _open(window, commands, worker)
        releases: list[int] = []
        commands.package_review_cancel_requested.connect(releases.append)
        operation = 987
        if phase == "review":
            window._package_review_operation_id = operation
        else:
            window._package_export_operation_id = operation
        if phase.startswith("compression_"):
            window._compression_phase = {
                "compression_prepare": "preparing",
                "compression_preview": "preview",
                "compression_export": "export",
            }[phase]
        worker.package_failed.emit(
            operation, phase, UiFailure("REDACTION_PIPELINE_DISABLED", "PRIVATE", False)
        )
        _application().processEvents()
        assert window._package_export_dialog is None
        assert cast(object, window._redaction_export_dialog) is None
        assert len(releases) == 1
        worker.package_review_cancelled.emit(releases[0] + 1)
        assert cast(object, window._redaction_export_dialog) is None
        worker.package_review_cancelled.emit(releases[0])
        _application().processEvents()
        assert window._redaction_export_dialog is not None
        assert window._package_cancel_operation_id is None
        assert window._package_review_state is None


def test_package_release_failure_keeps_report_pending_and_cannot_start_mark_editor() -> None:
    with _window() as (window, commands, worker):
        assert window._state is not None
        worker.state_changed.emit(_marked_state(window._state))
        _open(window, commands, worker)
        releases: list[int] = []
        commands.package_review_cancel_requested.connect(releases.append)
        window._package_export_operation_id = 987
        worker.package_failed.emit(
            987, "export", UiFailure("REDACTION_PIPELINE_DISABLED", "PRIVATE", False)
        )
        worker.package_failed.emit(
            releases[0], "cancel", UiFailure("PACKAGE_RELEASE_FAILED", "PRIVATE", True)
        )
        _application().processEvents()
        assert window._package_release_failed
        assert window._redaction_export_dialog is None
        assert window._pending_redaction_export_report is not None
        assert window._redaction_tools_start_operation_id is None
        assert not window._show_pending_redaction_export_report()


def test_stale_report_after_package_release_and_shutdown_do_not_reopen_review() -> None:
    with _window() as (window, commands, worker):
        assert window._state is not None
        state = _marked_state(window._state)
        worker.state_changed.emit(state)
        _open(window, commands, worker)
        releases: list[int] = []
        commands.package_review_cancel_requested.connect(releases.append)
        window._package_export_operation_id = 987
        worker.package_failed.emit(
            987, "export", UiFailure("REDACTION_PIPELINE_DISABLED", "PRIVATE", False)
        )
        worker.state_changed.emit(replace(state, revision=4))
        worker.package_review_cancelled.emit(releases[0])
        _application().processEvents()
        assert window._redaction_export_dialog is None
        assert window._pending_redaction_export_report is None
        worker.failed.emit(UiFailure("REDACTION_PIPELINE_DISABLED", "PRIVATE", False))
        _application().processEvents()
        assert cast(object, window._redaction_export_dialog) is not None
        window.request_shutdown()
        _application().processEvents()
        assert window._redaction_export_dialog is None
        assert window._pending_redaction_export_report is None
        assert window._pending_redaction_review is None


def test_queued_mark_review_rechecks_case_revision_before_opening_editor() -> None:
    with _window() as (window, commands, worker):
        assert window._state is not None
        state = _marked_state(window._state)
        worker.state_changed.emit(state)
        beginnings: list[tuple[int, UUID]] = []
        commands.redaction_tools_begin_requested.disconnect(worker.begin_redaction_tools)
        commands.redaction_tools_begin_requested.connect(
            lambda operation_id, page_id: beginnings.append((operation_id, page_id))
        )
        worker.failed.emit(UiFailure("REDACTION_PIPELINE_DISABLED", "PRIVATE", False))
        assert window._redaction_export_dialog is not None
        window._review_redaction_export_marks()
        worker.state_changed.emit(replace(state, revision=4))
        _application().processEvents()
        assert beginnings == []
        assert window._redaction_tools_start_operation_id is None
        assert window._pending_redaction_review is None


def test_stale_operation_or_state_does_not_show_unbound_report() -> None:
    with _window() as (window, _commands, worker):
        assert window._state is not None
        marked = _marked_state(window._state)
        worker.state_changed.emit(marked)
        operation = window._begin_long_operation(_LongOperationKind.PDF_EXPORT)
        worker.failed.emit(
            UiFailure("REDACTION_PIPELINE_DISABLED", "PRIVATE", False, operation.operation_id + 1)
        )
        assert window._pending_redaction_export_report is None
        worker.failed.emit(
            UiFailure("REDACTION_PIPELINE_DISABLED", "PRIVATE", False, operation.operation_id)
        )
        worker.state_changed.emit(replace(marked, revision=marked.revision + 1))  # type: ignore[operator]
        worker.operation_finished.emit(operation.operation_id)
        _application().processEvents()
        assert window._redaction_export_dialog is None
        assert window._pending_redaction_export_report is None

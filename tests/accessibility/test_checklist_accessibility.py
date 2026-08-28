from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import cast
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QItemSelectionModel, QMimeData, QModelIndex, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QLabel,
    QListView,
    QPushButton,
    QScrollArea,
    QWidget,
)

from paperworkshell.app.checklist import ChecklistEditorState, ChecklistWorkflowService
from paperworkshell.domain.checklist import (
    AssignmentOrigin,
    AssignmentReviewState,
    ChecklistStatus,
)
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.templates.catalog import built_in_template_repository
from paperworkshell.templates.loader import SafeTemplateLoader
from paperworkshell.templates.models import TemplateKey
from paperworkshell.ui.checklist import (
    CHECKLIST_PAGE_MIME_TYPE,
    ChecklistEditorDialog,
    ChecklistPageListModel,
    ChecklistRequirementListModel,
)

CASE_ID = UUID("11000000-0000-4000-8000-000000000001")
SOURCE_ID = UUID("22000000-0000-4000-8000-000000000001")
PAGE_IDS = tuple(UUID(f"33000000-0000-4000-8000-{number:012d}") for number in range(1, 7))
EDITOR_ID = UUID("44000000-0000-4000-8000-000000000001")
ASSIGNMENT_IDS = tuple(UUID(f"55000000-0000-4000-8000-{number:012d}") for number in range(1, 9))
CONFIRMATION_IDS = tuple(UUID(f"66000000-0000-4000-8000-{number:012d}") for number in range(1, 9))
PRIVATE_CANARY = "private-source-name-and-path-canary.pdf"
TIMES = tuple(datetime(2026, 8, 28, 12, minute, tzinfo=UTC) for minute in range(10))


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def _document() -> dict[str, object]:
    return {
        "schema_version": 2,
        "case_id": str(CASE_ID),
        "revision": 7,
        "created_at": "2026-08-28T10:00:00Z",
        "updated_at": "2026-08-28T11:59:00Z",
        "template_ref": None,
        "sources": [
            {
                "source_id": str(SOURCE_ID),
                "display_name": PRIVATE_CANARY,
                "storage_name": f"{SOURCE_ID}.bin",
                "detected_media_type": "application/pdf",
                "byte_size": 4096,
                "page_count": len(PAGE_IDS),
                "sha256": "a" * 64,
                "signature_state": "none_detected",
                "encryption_state": "unencrypted",
                "import_state": "complete",
                "imported_at": "2026-08-28T10:01:00Z",
            }
        ],
        "pages": [
            {
                "page_id": str(page_id),
                "source_id": str(SOURCE_ID),
                "source_page_index": index,
            }
            for index, page_id in enumerate(PAGE_IDS)
        ],
        "page_sequence_base": [str(page_id) for page_id in PAGE_IDS],
        "page_sequence": [str(page_id) for page_id in PAGE_IDS],
        "page_operations": [],
        "assets": [],
        "overlays": [],
        "assignments": [],
        "constraints": {
            "constraints_version": 1,
            "allowed_output_modes": ["combined_pdf"],
            "encryption_policy": "must_be_unencrypted",
        },
        "confirmations": [],
        "export_history": {"storage_name": "export-history.json", "entries": []},
    }


def _snapshot() -> ManifestSnapshot:
    return ManifestSnapshot(
        CASE_ID,
        2,
        7,
        _document(),
        "b" * 64,
        "c" * 64,
    )


def _single_id(value: UUID) -> Callable[[], UUID]:
    return lambda: value


def _empty_state() -> ChecklistEditorState:
    service = ChecklistWorkflowService(
        SafeTemplateLoader(built_in_template_repository()),
        TemplateKey("generic-portal-application", "1.1.0"),
        as_of=date(2026, 8, 28),
        clock=lambda: TIMES[0],
        id_factory=_single_id(EDITOR_ID),
    )
    return service.begin(_snapshot())


def _mixed_state() -> ChecklistEditorState:
    state = _empty_state()
    state = state.add_pages(
        "application-form",
        (PAGE_IDS[0],),
        clock=lambda: TIMES[1],
        id_factory=_single_id(ASSIGNMENT_IDS[0]),
    )
    state = state.suggest_pages(
        "identity-evidence",
        (PAGE_IDS[1],),
        clock=lambda: TIMES[2],
        id_factory=_single_id(ASSIGNMENT_IDS[1]),
    )
    state = state.add_pages(
        "address-evidence",
        (PAGE_IDS[2],),
        clock=lambda: TIMES[3],
        id_factory=_single_id(ASSIGNMENT_IDS[2]),
    )
    return state.confirm_assignment(
        ASSIGNMENT_IDS[2],
        clock=lambda: TIMES[4],
        id_factory=_single_id(CONFIRMATION_IDS[0]),
    )


def _ordered_state() -> ChecklistEditorState:
    state = _empty_state()
    state = state.add_pages(
        "identity-evidence",
        PAGE_IDS[:2],
        clock=lambda: TIMES[1],
        id_factory=_single_id(ASSIGNMENT_IDS[0]),
    )
    return state.add_pages(
        "identity-evidence",
        (PAGE_IDS[2],),
        clock=lambda: TIMES[2],
        id_factory=_single_id(ASSIGNMENT_IDS[1]),
    )


def _inactive_assignment_state() -> ChecklistEditorState:
    service = ChecklistWorkflowService(
        SafeTemplateLoader(built_in_template_repository()),
        TemplateKey("generic-portal-application", "1.1.0"),
        as_of=date(2026, 8, 28),
        clock=lambda: TIMES[0],
        id_factory=_single_id(EDITOR_ID),
    )
    snapshot = _snapshot()
    opening = service.begin(snapshot)
    added = opening.add_pages(
        "application-form",
        (PAGE_IDS[0],),
        clock=lambda: TIMES[1],
        id_factory=_single_id(ASSIGNMENT_IDS[0]),
    )
    confirmed = added.confirm_assignment(
        ASSIGNMENT_IDS[0],
        clock=lambda: TIMES[2],
        id_factory=_single_id(CONFIRMATION_IDS[0]),
    )
    candidate = service.to_candidate(opening, confirmed, snapshot)
    candidate["page_sequence"] = [str(page_id) for page_id in PAGE_IDS[1:]]
    candidate["updated_at"] = TIMES[3].isoformat().replace("+00:00", "Z")
    service.complete(opening, confirmed)
    reopened_service = ChecklistWorkflowService(
        SafeTemplateLoader(built_in_template_repository()),
        TemplateKey("generic-portal-application", "1.1.0"),
        as_of=date(2026, 8, 28),
        clock=lambda: TIMES[4],
        id_factory=_single_id(EDITOR_ID),
    )
    return reopened_service.begin(ManifestSnapshot(CASE_ID, 2, 7, candidate, "d" * 64, "e" * 64))


@contextmanager
def _dialog(state: ChecklistEditorState) -> Iterator[ChecklistEditorDialog]:
    app = _application()
    dialog = ChecklistEditorDialog(state)
    dialog.show()
    app.processEvents()
    try:
        yield dialog
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def _button(dialog: ChecklistEditorDialog, name: str) -> QPushButton:
    button = dialog.findChild(QPushButton, name)
    assert button is not None
    return button


def _list(dialog: ChecklistEditorDialog, name: str) -> QListView:
    view = dialog.findChild(QListView, name)
    assert view is not None
    return view


def _select_row(view: QListView, row: int, *, add: bool = False) -> None:
    model = view.model()
    selection = view.selectionModel()
    assert model is not None
    assert selection is not None
    index = model.index(row, 0)
    flags = (
        QItemSelectionModel.SelectionFlag.Select
        if add
        else QItemSelectionModel.SelectionFlag.ClearAndSelect
    ) | QItemSelectionModel.SelectionFlag.Rows
    selection.setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)
    selection.select(index, flags)


def _assignment_row(dialog: ChecklistEditorDialog, assignment_id: UUID) -> int:
    model = _list(dialog, "checklistAssignmentList").model()
    assert model is not None
    return next(
        row
        for row in range(model.rowCount())
        if model.data(model.index(row, 0), int(Qt.ItemDataRole.UserRole)) == assignment_id
    )


def test_textual_status_guidance_and_controls_are_accessible_without_private_names() -> None:
    app = _application()
    with _dialog(_mixed_state()) as dialog:
        assert dialog.isModal()
        assert dialog.windowTitle() == "Review checklist assignments"
        assert "Cancel is the safe default" in dialog.accessibleDescription()

        requirement_view = _list(dialog, "checklistRequirementList")
        model = requirement_view.model()
        assert model is not None
        rows = [str(model.data(model.index(row, 0))) for row in range(model.rowCount())]
        joined = " ".join(rows)
        assert "Missing" in joined
        assert "Added — confirmation required" in joined
        assert "Needs review" in joined
        assert "Ready" in joined
        assert "confirmed" in joined

        guidance = dialog.findChild(QLabel, "checklistRequirementGuidance")
        assert guidance is not None
        assert guidance.isVisible()
        assert guidance.wordWrap()
        assert "Application form" in guidance.text()
        _select_row(requirement_view, 1)
        app.processEvents()
        assert "Identity evidence" in guidance.text()
        assert "front and back" in guidance.text()
        assert guidance.accessibleDescription() == guidance.text()

        interactive_names = (
            "checklistRequirementList",
            "checklistAvailablePageList",
            "checklistAddPagesButton",
            "checklistAssignmentList",
            "checklistConfirmButton",
            "checklistAcceptSuggestionButton",
            "checklistRejectSuggestionButton",
            "checklistRemoveAssignmentButton",
            "checklistMoveTargetCombo",
            "checklistMoveCategoryButton",
            "checklistDocumentEarlierButton",
            "checklistDocumentLaterButton",
            "checklistAssignedPageList",
            "checklistPageEarlierButton",
            "checklistPageLaterButton",
            "checklistUndoButton",
            "checklistRedoButton",
            "checklistEditorStatus",
            "checklistApplyButton",
            "checklistCancelButton",
        )
        for object_name in interactive_names:
            widget = dialog.findChild(QWidget, object_name)
            assert widget is not None
            assert widget.accessibleName()
            assert widget.accessibleDescription()

        visible_text = " ".join(label.text() for label in dialog.findChildren(QLabel))
        for list_name in (
            "checklistRequirementList",
            "checklistAvailablePageList",
            "checklistAssignmentList",
            "checklistAssignedPageList",
        ):
            list_model = _list(dialog, list_name).model()
            assert list_model is not None
            visible_text += " ".join(
                str(list_model.data(list_model.index(row, 0)))
                for row in range(list_model.rowCount())
            )
        assert PRIVATE_CANARY not in visible_text
        assert str(SOURCE_ID) not in visible_text
        assert all(str(page_id) not in visible_text for page_id in PAGE_IDS)


def test_safe_default_tab_order_and_enlarged_text_keep_actions_reachable() -> None:
    app = _application()
    original_font = QFont(app.font())
    enlarged_font = QFont(original_font)
    enlarged_font.setPointSizeF(max(24.0, original_font.pointSizeF() * 2.0))
    app.setFont(enlarged_font)
    try:
        with _dialog(_empty_state()) as dialog:
            cancel = _button(dialog, "checklistCancelButton")
            apply = _button(dialog, "checklistApplyButton")
            scroll = dialog.findChild(QScrollArea, "checklistEditorScrollArea")
            assert scroll is not None
            assert cancel.isDefault()
            assert dialog.focusWidget() is cancel

            QTest.keyClick(cancel, Qt.Key.Key_Tab)
            app.processEvents()
            assert dialog.focusWidget() is scroll

            dialog.resize(520, 360)
            app.processEvents()
            assert scroll.widgetResizable()
            assert scroll.verticalScrollBar().maximum() > 0
            assert apply.isVisible()
            assert cancel.isVisible()
            assert dialog.rect().contains(apply.mapTo(dialog, apply.rect().center()))
            assert dialog.rect().contains(cancel.mapTo(dialog, cancel.rect().center()))
    finally:
        app.setFont(original_font)


def test_keyboard_add_and_uuid_only_drop_use_the_same_pending_add_semantics() -> None:
    app = _application()
    with _dialog(_empty_state()) as keyboard_dialog:
        requirements = _list(keyboard_dialog, "checklistRequirementList")
        pages = _list(keyboard_dialog, "checklistAvailablePageList")
        _select_row(requirements, 0)
        _select_row(pages, 0)
        _select_row(pages, 1, add=True)
        add = _button(keyboard_dialog, "checklistAddPagesButton")
        assert add.isEnabled()
        QTest.keyClick(add, Qt.Key.Key_Space)
        app.processEvents()

        keyboard_assignment = keyboard_dialog.state.assignments[0]
        assert keyboard_assignment.page_ids == PAGE_IDS[:2]
        assert keyboard_assignment.origin is AssignmentOrigin.USER
        assert keyboard_assignment.review_state is AssignmentReviewState.SUGGESTED
        assert keyboard_dialog.state.requirements[0].status is ChecklistStatus.ADDED
        status = keyboard_dialog.findChild(QLabel, "checklistEditorStatus")
        assert status is not None
        assert "confirm" in status.text().casefold()
        assert keyboard_dialog.focusWidget() is _list(keyboard_dialog, "checklistAssignmentList")

    with _dialog(_empty_state()) as drag_dialog:
        page_view = _list(drag_dialog, "checklistAvailablePageList")
        requirement_view = _list(drag_dialog, "checklistRequirementList")
        page_model = cast(ChecklistPageListModel, page_view.model())
        requirement_model = cast(ChecklistRequirementListModel, requirement_view.model())
        page_indexes = [page_model.index(0, 0), page_model.index(1, 0)]
        mime = page_model.mimeData(page_indexes)
        assert mime.formats() == [CHECKLIST_PAGE_MIME_TYPE]
        payload = cast(bytes, mime.data(CHECKLIST_PAGE_MIME_TYPE).data()).decode("ascii")
        assert payload.splitlines() == [str(PAGE_IDS[0]), str(PAGE_IDS[1])]
        assert PRIVATE_CANARY not in payload

        assert requirement_model.dropMimeData(
            mime,
            Qt.DropAction.CopyAction,
            -1,
            -1,
            requirement_model.index(0, 0),
        )
        app.processEvents()
        dragged = drag_dialog.state.assignments[0]
        assert dragged.page_ids == keyboard_assignment.page_ids
        assert dragged.origin is keyboard_assignment.origin
        assert dragged.review_state is keyboard_assignment.review_state
        assert drag_dialog.state.requirements[0].status is ChecklistStatus.ADDED

        forged = QMimeData()
        forged.setData(CHECKLIST_PAGE_MIME_TYPE, PRIVATE_CANARY.encode("ascii"))
        before = drag_dialog.state
        assert not requirement_model.dropMimeData(
            forged,
            Qt.DropAction.CopyAction,
            -1,
            -1,
            requirement_model.index(1, 0),
        )
        assert drag_dialog.state is before

        pending_row = page_model.row_for_id(PAGE_IDS[0])
        assert pending_row >= 0
        assert not (
            page_model.flags(page_model.index(pending_row, 0)) & Qt.ItemFlag.ItemIsDragEnabled
        )


def test_confirm_accept_reject_move_and_unassign_have_keyboard_paths() -> None:
    app = _application()
    with _dialog(_mixed_state()) as dialog:
        requirements = _list(dialog, "checklistRequirementList")
        assignments = _list(dialog, "checklistAssignmentList")
        confirm = _button(dialog, "checklistConfirmButton")
        accept = _button(dialog, "checklistAcceptSuggestionButton")
        reject = _button(dialog, "checklistRejectSuggestionButton")
        remove = _button(dialog, "checklistRemoveAssignmentButton")

        _select_row(requirements, 0)
        _select_row(assignments, _assignment_row(dialog, ASSIGNMENT_IDS[0]))
        assert confirm.isEnabled()
        assert not accept.isEnabled()
        assert not reject.isEnabled()
        QTest.keyClick(confirm, Qt.Key.Key_Space)
        app.processEvents()
        confirmed = next(
            item for item in dialog.state.assignments if item.assignment_id == ASSIGNMENT_IDS[0]
        )
        assert confirmed.review_state is AssignmentReviewState.USER_CONFIRMED

        _select_row(requirements, 1)
        _select_row(assignments, _assignment_row(dialog, ASSIGNMENT_IDS[1]))
        assert not confirm.isEnabled()
        assert accept.isEnabled()
        assert reject.isEnabled()
        target = dialog.findChild(QComboBox, "checklistMoveTargetCombo")
        move = _button(dialog, "checklistMoveCategoryButton")
        assert target is not None
        target.setCurrentIndex(target.findData("supporting-evidence"))
        assert not move.isEnabled()
        QTest.keyClick(accept, Qt.Key.Key_Space)
        app.processEvents()
        suggestion = next(
            item for item in dialog.state.assignments if item.assignment_id == ASSIGNMENT_IDS[1]
        )
        assert suggestion.origin is AssignmentOrigin.AUTOMATION
        assert suggestion.review_state is AssignmentReviewState.USER_CONFIRMED

        target.setCurrentIndex(target.findData("supporting-evidence"))
        assert move.isEnabled()
        QTest.keyClick(move, Qt.Key.Key_Space)
        app.processEvents()
        moved = next(
            item for item in dialog.state.assignments if item.assignment_id == ASSIGNMENT_IDS[1]
        )
        assert moved.requirement_id == "supporting-evidence"
        assert moved.origin is AssignmentOrigin.USER
        assert moved.review_state is AssignmentReviewState.SUGGESTED

        assert remove.isEnabled()
        QTest.keyClick(remove, Qt.Key.Key_Space)
        app.processEvents()
        assert all(item.assignment_id != ASSIGNMENT_IDS[1] for item in dialog.state.assignments)
        page_model = cast(
            ChecklistPageListModel,
            _list(dialog, "checklistAvailablePageList").model(),
        )
        page_row = page_model.row_for_id(PAGE_IDS[1])
        assert page_row >= 0
        assert page_model.data(page_model.index(page_row, 0)) == "Page 2 — Unassigned"

    with _dialog(_mixed_state()) as reject_dialog:
        requirements = _list(reject_dialog, "checklistRequirementList")
        assignments = _list(reject_dialog, "checklistAssignmentList")
        _select_row(requirements, 1)
        _select_row(assignments, _assignment_row(reject_dialog, ASSIGNMENT_IDS[1]))
        reject = _button(reject_dialog, "checklistRejectSuggestionButton")
        QTest.keyClick(reject, Qt.Key.Key_Space)
        app.processEvents()
        assert all(
            item.assignment_id != ASSIGNMENT_IDS[1] for item in reject_dialog.state.assignments
        )
        status = reject_dialog.findChild(QLabel, "checklistEditorStatus")
        assert status is not None
        assert "rejected" in status.text().casefold()


def test_document_page_order_and_history_are_complete_without_dragging() -> None:
    app = _application()
    with _dialog(_ordered_state()) as dialog:
        requirements = _list(dialog, "checklistRequirementList")
        assignments = _list(dialog, "checklistAssignmentList")
        assigned_pages = _list(dialog, "checklistAssignedPageList")
        _select_row(requirements, 1)
        _select_row(assignments, _assignment_row(dialog, ASSIGNMENT_IDS[1]))

        earlier_document = _button(dialog, "checklistDocumentEarlierButton")
        assert earlier_document.isEnabled()
        QTest.keyClick(earlier_document, Qt.Key.Key_Space)
        app.processEvents()
        identity = next(
            requirement
            for requirement in dialog.state.requirements
            if requirement.requirement_id == "identity-evidence"
        )
        assert identity.assignment_ids == (ASSIGNMENT_IDS[1], ASSIGNMENT_IDS[0])
        assert dialog.focusWidget() is assignments

        _select_row(assignments, _assignment_row(dialog, ASSIGNMENT_IDS[0]))
        _select_row(assigned_pages, 1)
        earlier_page = _button(dialog, "checklistPageEarlierButton")
        assert earlier_page.isEnabled()
        QTest.keyClick(earlier_page, Qt.Key.Key_Space)
        app.processEvents()
        multi_page = next(
            item for item in dialog.state.assignments if item.assignment_id == ASSIGNMENT_IDS[0]
        )
        assert multi_page.page_ids == (PAGE_IDS[1], PAGE_IDS[0])
        assert dialog.focusWidget() is assigned_pages

        undo = _button(dialog, "checklistUndoButton")
        redo = _button(dialog, "checklistRedoButton")
        assert undo.isEnabled()
        QTest.keyClick(undo, Qt.Key.Key_Space)
        app.processEvents()
        multi_page = next(
            item for item in dialog.state.assignments if item.assignment_id == ASSIGNMENT_IDS[0]
        )
        assert multi_page.page_ids == PAGE_IDS[:2]
        assert redo.isEnabled()
        QTest.keyClick(redo, Qt.Key.Key_Space)
        app.processEvents()
        multi_page = next(
            item for item in dialog.state.assignments if item.assignment_id == ASSIGNMENT_IDS[0]
        )
        assert multi_page.page_ids == (PAGE_IDS[1], PAGE_IDS[0])


def test_inactive_assignment_page_stays_needs_review_without_crashing_or_reordering() -> None:
    app = _application()
    state = _inactive_assignment_state()
    assert state.requirements[0].status is ChecklistStatus.NEEDS_REVIEW

    with _dialog(state) as dialog:
        app.processEvents()
        assignments = _list(dialog, "checklistAssignmentList")
        assigned_pages = _list(dialog, "checklistAssignedPageList")
        assert assignments.model() is not None
        assert "Needs review" in str(assignments.model().data(assignments.model().index(0, 0)))
        assert assigned_pages.model() is not None
        assert assigned_pages.model().rowCount() == 0
        assert "1 inactive assignment page is omitted" in assigned_pages.accessibleDescription()
        assert not _button(dialog, "checklistPageEarlierButton").isEnabled()
        assert not _button(dialog, "checklistPageLaterButton").isEnabled()


def test_apply_waits_for_save_and_sanitized_failure_retains_state_for_retry() -> None:
    app = _application()
    with _dialog(_mixed_state()) as dialog:
        apply = _button(dialog, "checklistApplyButton")
        cancel = _button(dialog, "checklistCancelButton")
        status = dialog.findChild(QLabel, "checklistEditorStatus")
        assert status is not None
        requests: list[ChecklistEditorState] = []
        dialog.apply_requested.connect(requests.append)

        assert apply.isEnabled()
        QTest.keyClick(apply, Qt.Key.Key_Space)
        app.processEvents()
        assert requests == [dialog.state]
        apply_pending_before_result = dialog.apply_pending
        assert apply_pending_before_result
        assert dialog.commit_started
        assert dialog.result() == int(QDialog.DialogCode.Rejected)
        assert dialog.isVisible()
        pending_shutdown_state = dialog.latest_state_for_shutdown()
        assert pending_shutdown_state is None
        assert not apply.isEnabled()
        assert not cancel.isEnabled()

        assert dialog.mark_apply_failed("PRIVATE_CODE", PRIVATE_CANARY)
        app.processEvents()
        apply_pending_after_failure = dialog.apply_pending
        assert not apply_pending_after_failure
        retry_shutdown_state = dialog.latest_state_for_shutdown()
        assert retry_shutdown_state is dialog.state
        assert PRIVATE_CANARY not in status.text()
        assert "PRIVATE_CODE" not in status.text()
        assert "retained" in status.text().casefold()
        assert dialog.focusWidget() is status
        assert apply.isEnabled()
        assert not cancel.isEnabled()

        dialog.reject()
        assert dialog.isVisible()
        assert "cannot be discarded" in status.text().casefold()

        undo = _button(dialog, "checklistUndoButton")
        while dialog.state.can_undo:
            QTest.keyClick(undo, Qt.Key.Key_Space)
            app.processEvents()
        assert not dialog.state.changed
        assert apply.isEnabled()
        assert not cancel.isEnabled()

        QTest.keyClick(apply, Qt.Key.Key_Space)
        app.processEvents()
        assert requests[-1] is dialog.state
        assert not requests[-1].changed
        assert dialog.mark_applied()
        app.processEvents()
        assert dialog.result() == int(QDialog.DialogCode.Accepted)


def test_cancel_discards_before_apply_and_unchanged_draft_cannot_apply() -> None:
    app = _application()
    with _dialog(_empty_state()) as dialog:
        requests: list[ChecklistEditorState] = []
        dialog.apply_requested.connect(requests.append)
        apply = _button(dialog, "checklistApplyButton")
        cancel = _button(dialog, "checklistCancelButton")
        assert not apply.isEnabled()
        assert dialog.latest_state_for_shutdown() is dialog.state
        QTest.keyClick(cancel, Qt.Key.Key_Space)
        app.processEvents()
        assert requests == []
        assert dialog.result() == int(QDialog.DialogCode.Rejected)
        assert not dialog.isVisible()


def test_drop_rejects_wrong_actions_columns_and_non_uuid_payloads() -> None:
    model = ChecklistRequirementListModel()
    state = _empty_state()
    model.replace(state.requirements, frozenset({PAGE_IDS[0]}))
    target = model.index(0, 0)
    mime = QMimeData()
    mime.setData(CHECKLIST_PAGE_MIME_TYPE, str(PAGE_IDS[0]).encode("ascii"))
    assert not model.dropMimeData(
        mime,
        Qt.DropAction.MoveAction,
        -1,
        -1,
        target,
    )
    assert not model.dropMimeData(
        mime,
        Qt.DropAction.CopyAction,
        -1,
        1,
        target,
    )
    wrong_id = QMimeData()
    wrong_id.setData(
        CHECKLIST_PAGE_MIME_TYPE,
        b"not-a-uuid\nC:\\private\\source.pdf",
    )
    assert not model.dropMimeData(
        wrong_id,
        Qt.DropAction.CopyAction,
        -1,
        -1,
        target,
    )
    assert not model.dropMimeData(
        mime,
        Qt.DropAction.CopyAction,
        -1,
        -1,
        QModelIndex(),
    )

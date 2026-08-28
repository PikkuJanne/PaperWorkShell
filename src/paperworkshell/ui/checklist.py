"""Accessible keyboard-first checklist assignment editor.

The editor receives only the detached, path-free checklist state prepared by
the application layer.  Page rows intentionally expose positions rather than
source names or document content.  Pointer drag-and-drop is an optional UUID-
only shortcut for the same provisional Add action that is available by
keyboard.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from itertools import pairwise
from typing import Final, cast
from uuid import UUID

from PySide6.QtCore import (
    QAbstractListModel,
    QMimeData,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QAccessible,
    QAccessibleAnnouncementEvent,
    QCloseEvent,
    QFont,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListView,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from paperworkshell.app.checklist import (
    ChecklistAssignmentView,
    ChecklistEditorState,
    ChecklistPageView,
    ChecklistRequirementView,
)
from paperworkshell.domain.checklist import (
    AssignmentOrigin,
    AssignmentReviewState,
    ChecklistError,
    ChecklistStatus,
    RequirementCountUnit,
)

CHECKLIST_PAGE_MIME_TYPE: Final = "application/x-paperworkshell-checklist-page-ids-v1"
_MAX_DRAG_PAGE_COUNT: Final = 4_096
_MAX_DRAG_PAYLOAD_BYTES: Final = (_MAX_DRAG_PAGE_COUNT * 36) + _MAX_DRAG_PAGE_COUNT - 1
_FOCUS_VISIBLE_TEXT_STYLE: Final = (
    "QLabel:focus { border: 2px solid palette(highlight); padding: 2px; }"
)
_STATUS_LABELS: Final[dict[ChecklistStatus, str]] = {
    ChecklistStatus.MISSING: "Missing",
    ChecklistStatus.ADDED: "Added — confirmation required",
    ChecklistStatus.NEEDS_REVIEW: "Needs review",
    ChecklistStatus.READY: "Ready",
}
_INVALID_MODEL_INDEX: Final = QModelIndex()


def _valid_uuid(value: object) -> bool:
    return type(value) is UUID and value.version == 4


def _encode_page_ids(page_ids: tuple[UUID, ...]) -> bytes:
    if (
        not page_ids
        or len(page_ids) > _MAX_DRAG_PAGE_COUNT
        or any(not _valid_uuid(page_id) for page_id in page_ids)
        or len(set(page_ids)) != len(page_ids)
    ):
        return b""
    return "\n".join(str(page_id) for page_id in page_ids).encode("ascii")


def _decode_page_ids(data: QMimeData) -> tuple[UUID, ...] | None:
    if not data.hasFormat(CHECKLIST_PAGE_MIME_TYPE):
        return None
    payload = cast(bytes, data.data(CHECKLIST_PAGE_MIME_TYPE).data())
    if not payload or len(payload) > _MAX_DRAG_PAYLOAD_BYTES:
        return None
    try:
        parts = payload.decode("ascii").split("\n")
        page_ids = tuple(UUID(part) for part in parts)
    except UnicodeDecodeError, ValueError:
        return None
    if (
        len(page_ids) > _MAX_DRAG_PAGE_COUNT
        or any(
            str(page_id) != part or page_id.version != 4
            for page_id, part in zip(page_ids, parts, strict=True)
        )
        or len(set(page_ids)) != len(page_ids)
    ):
        return None
    return page_ids


def _assignment_status_text(assignment: ChecklistAssignmentView) -> str:
    if assignment.page_review_required:
        return "Needs review — inactive or overlapping pages"
    if assignment.review_state is AssignmentReviewState.USER_CONFIRMED:
        return "Confirmed by the user"
    if assignment.origin is AssignmentOrigin.AUTOMATION:
        return "Needs review — automation suggestion"
    return "Added — confirmation required"


def _count_text(requirement: ChecklistRequirementView) -> str:
    unit = "document" if requirement.count_unit is RequirementCountUnit.DOCUMENTS else "page"
    plural = unit if requirement.confirmed_count == 1 else f"{unit}s"
    maximum = requirement.maximum_count
    required = (
        f"at least {requirement.minimum_count}"
        if maximum is None
        else f"{requirement.minimum_count} to {maximum}"
    )
    return f"{requirement.confirmed_count} confirmed {plural}; required {required}"


def _model_index_row(index: QModelIndex | QPersistentModelIndex) -> int:
    return index.row() if index.isValid() else -1


class ChecklistRequirementListModel(QAbstractListModel):
    """Plain-text requirements and UUID-only page drop targets."""

    pages_dropped = Signal(str, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: tuple[ChecklistRequirementView, ...] = ()
        self._allowed_page_ids: frozenset[UUID] = frozenset()

    def replace(
        self,
        rows: tuple[ChecklistRequirementView, ...],
        allowed_page_ids: frozenset[UUID],
    ) -> None:
        self.beginResetModel()
        self._rows = rows
        self._allowed_page_ids = allowed_page_ids
        self.endResetModel()

    def rowCount(  # noqa: N802 - Qt override name
        self,
        parent: QModelIndex | QPersistentModelIndex = _INVALID_MODEL_INDEX,
    ) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> object | None:
        row = _model_index_row(index)
        if not 0 <= row < len(self._rows):
            return None
        requirement = self._rows[row]
        status = _STATUS_LABELS[requirement.status]
        count = _count_text(requirement)
        if role in {
            int(Qt.ItemDataRole.DisplayRole),
            int(Qt.ItemDataRole.AccessibleTextRole),
        }:
            return f"{requirement.display_name} — {status}. {count}."
        if role == int(Qt.ItemDataRole.AccessibleDescriptionRole):
            return (
                f"{requirement.description} Status: {status}. Count: {count}. "
                "Select this category, then use Add selected pages or drop internal page rows. "
                "Adding never confirms the document."
            )
        if role == int(Qt.ItemDataRole.UserRole):
            return requirement.requirement_id
        return None

    def flags(
        self,
        index: QModelIndex | QPersistentModelIndex,
    ) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return (
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDropEnabled
        )

    def mimeTypes(self) -> list[str]:  # noqa: N802 - Qt override name
        return [CHECKLIST_PAGE_MIME_TYPE]

    def supportedDropActions(self) -> Qt.DropAction:  # noqa: N802 - Qt override name
        return Qt.DropAction.CopyAction

    def canDropMimeData(  # noqa: N802 - Qt override name
        self,
        data: QMimeData,
        action: Qt.DropAction,
        row: int,
        column: int,
        parent: QModelIndex | QPersistentModelIndex,
    ) -> bool:
        if action == Qt.DropAction.IgnoreAction:
            return True
        target = self._drop_target(row, column, parent)
        page_ids = _decode_page_ids(data)
        return (
            action == Qt.DropAction.CopyAction
            and target is not None
            and page_ids is not None
            and set(page_ids) <= self._allowed_page_ids
        )

    def dropMimeData(  # noqa: N802 - Qt override name
        self,
        data: QMimeData,
        action: Qt.DropAction,
        row: int,
        column: int,
        parent: QModelIndex | QPersistentModelIndex,
    ) -> bool:
        if action == Qt.DropAction.IgnoreAction:
            return True
        target = self._drop_target(row, column, parent)
        page_ids = _decode_page_ids(data)
        if (
            action != Qt.DropAction.CopyAction
            or target is None
            or page_ids is None
            or not set(page_ids) <= self._allowed_page_ids
        ):
            return False
        self.pages_dropped.emit(target.requirement_id, page_ids)
        return True

    def requirement_at(self, row: int) -> ChecklistRequirementView | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def row_for_id(self, requirement_id: str | None) -> int:
        if requirement_id is None:
            return -1
        return next(
            (
                index
                for index, requirement in enumerate(self._rows)
                if requirement.requirement_id == requirement_id
            ),
            -1,
        )

    def _drop_target(
        self,
        row: int,
        column: int,
        parent: QModelIndex | QPersistentModelIndex,
    ) -> ChecklistRequirementView | None:
        if column not in {-1, 0}:
            return None
        selected_row = parent.row() if parent.isValid() else row
        return self.requirement_at(selected_row)


class ChecklistPageListModel(QAbstractListModel):
    """Pages without confirmed assignment; only unassigned rows can drag."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: tuple[ChecklistPageView, ...] = ()
        self._assignments: dict[UUID, ChecklistAssignmentView] = {}

    def replace(
        self,
        pages: tuple[ChecklistPageView, ...],
        assignments: tuple[ChecklistAssignmentView, ...],
    ) -> None:
        assignment_by_id = {assignment.assignment_id: assignment for assignment in assignments}
        visible = tuple(
            page
            for page in pages
            if page.assignment_id is None
            or assignment_by_id[page.assignment_id].review_state
            is not AssignmentReviewState.USER_CONFIRMED
        )
        self.beginResetModel()
        self._rows = visible
        self._assignments = assignment_by_id
        self.endResetModel()

    def rowCount(  # noqa: N802 - Qt override name
        self,
        parent: QModelIndex | QPersistentModelIndex = _INVALID_MODEL_INDEX,
    ) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> object | None:
        row = _model_index_row(index)
        if not 0 <= row < len(self._rows):
            return None
        page = self._rows[row]
        state = self._page_state(page)
        if role in {
            int(Qt.ItemDataRole.DisplayRole),
            int(Qt.ItemDataRole.AccessibleTextRole),
        }:
            return f"Page {page.position} — {state}"
        if role == int(Qt.ItemDataRole.AccessibleDescriptionRole):
            return f"Case page position {page.position}. {state}. " + (
                "Select it and use Add selected pages, or drag this internal row."
                if page.assignment_id is None
                else "Review its document assignment below; it cannot be added again."
            )
        if role == int(Qt.ItemDataRole.UserRole):
            return page.page_id
        return None

    def flags(
        self,
        index: QModelIndex | QPersistentModelIndex,
    ) -> Qt.ItemFlag:
        row = _model_index_row(index)
        if not 0 <= row < len(self._rows):
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if self._rows[row].assignment_id is None:
            flags |= Qt.ItemFlag.ItemIsDragEnabled
        return flags

    def mimeTypes(self) -> list[str]:  # noqa: N802 - Qt override name
        return [CHECKLIST_PAGE_MIME_TYPE]

    def mimeData(  # noqa: N802 - Qt override name
        self,
        indexes: Sequence[QModelIndex],
    ) -> QMimeData:
        rows = sorted({index.row() for index in indexes if index.isValid()})
        page_ids = tuple(
            self._rows[row].page_id
            for row in rows
            if 0 <= row < len(self._rows) and self._rows[row].assignment_id is None
        )
        result = QMimeData()
        payload = _encode_page_ids(page_ids)
        if payload:
            result.setData(CHECKLIST_PAGE_MIME_TYPE, payload)
        return result

    def supportedDragActions(self) -> Qt.DropAction:  # noqa: N802 - Qt override name
        return Qt.DropAction.CopyAction

    def page_at(self, row: int) -> ChecklistPageView | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def row_for_id(self, page_id: UUID | None) -> int:
        if page_id is None:
            return -1
        return next(
            (index for index, page in enumerate(self._rows) if page.page_id == page_id),
            -1,
        )

    def unassigned_page_ids(self) -> frozenset[UUID]:
        return frozenset(page.page_id for page in self._rows if page.assignment_id is None)

    def _page_state(self, page: ChecklistPageView) -> str:
        if page.assignment_id is None:
            return "Unassigned"
        assignment = self._assignments[page.assignment_id]
        return _assignment_status_text(assignment)


class ChecklistAssignmentListModel(QAbstractListModel):
    """Ordered logical documents for the selected requirement."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: tuple[ChecklistAssignmentView, ...] = ()

    def replace(self, rows: tuple[ChecklistAssignmentView, ...]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rowCount(  # noqa: N802 - Qt override name
        self,
        parent: QModelIndex | QPersistentModelIndex = _INVALID_MODEL_INDEX,
    ) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> object | None:
        row = _model_index_row(index)
        if not 0 <= row < len(self._rows):
            return None
        assignment = self._rows[row]
        page_word = "page" if len(assignment.page_ids) == 1 else "pages"
        status = _assignment_status_text(assignment)
        text = f"Document {row + 1} — {status}; {len(assignment.page_ids)} {page_word}"
        if role in {
            int(Qt.ItemDataRole.DisplayRole),
            int(Qt.ItemDataRole.AccessibleTextRole),
        }:
            return text
        if role == int(Qt.ItemDataRole.AccessibleDescriptionRole):
            return (
                f"{text}. Use the named buttons to confirm, accept, reject, move, reorder, "
                "or remove this assignment."
            )
        if role == int(Qt.ItemDataRole.UserRole):
            return assignment.assignment_id
        return None

    def flags(
        self,
        index: QModelIndex | QPersistentModelIndex,
    ) -> Qt.ItemFlag:
        return (
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if index.isValid()
            else Qt.ItemFlag.NoItemFlags
        )

    def assignment_at(self, row: int) -> ChecklistAssignmentView | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def row_for_id(self, assignment_id: UUID | None) -> int:
        if assignment_id is None:
            return -1
        return next(
            (
                index
                for index, assignment in enumerate(self._rows)
                if assignment.assignment_id == assignment_id
            ),
            -1,
        )

    @property
    def assignments(self) -> tuple[ChecklistAssignmentView, ...]:
        return self._rows


class ChecklistAssignedPageListModel(QAbstractListModel):
    """Ordered page positions for one selected logical document."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: tuple[ChecklistPageView, ...] = ()

    def replace(self, rows: tuple[ChecklistPageView, ...]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rowCount(  # noqa: N802 - Qt override name
        self,
        parent: QModelIndex | QPersistentModelIndex = _INVALID_MODEL_INDEX,
    ) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> object | None:
        row = _model_index_row(index)
        if not 0 <= row < len(self._rows):
            return None
        page = self._rows[row]
        text = f"Page {page.position} — order {row + 1} of {len(self._rows)}"
        if role in {
            int(Qt.ItemDataRole.DisplayRole),
            int(Qt.ItemDataRole.AccessibleTextRole),
        }:
            return text
        if role == int(Qt.ItemDataRole.AccessibleDescriptionRole):
            return f"{text}. Use Move page earlier or Move page later to change document order."
        if role == int(Qt.ItemDataRole.UserRole):
            return page.page_id
        return None

    def flags(
        self,
        index: QModelIndex | QPersistentModelIndex,
    ) -> Qt.ItemFlag:
        return (
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if index.isValid()
            else Qt.ItemFlag.NoItemFlags
        )

    def page_at(self, row: int) -> ChecklistPageView | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def row_for_id(self, page_id: UUID | None) -> int:
        if page_id is None:
            return -1
        return next(
            (index for index, page in enumerate(self._rows) if page.page_id == page_id),
            -1,
        )


class ChecklistEditorDialog(QDialog):
    """Scrollable modal checklist editor with complete keyboard alternatives."""

    apply_requested = Signal(object)
    state_changed = Signal(object)

    def __init__(self, state: ChecklistEditorState, parent: QWidget | None = None) -> None:
        if type(state) is not ChecklistEditorState:
            raise ValueError("state must be a ChecklistEditorState")
        super().__init__(parent)
        self._state = state
        self._apply_pending = False
        self._commit_started = False

        self.setObjectName("checklistEditorDialog")
        self.setModal(True)
        self.setWindowTitle("Review checklist assignments")
        self.setAccessibleName("Review checklist assignments")
        self.setAccessibleDescription(
            "Assign case pages to checklist categories without exposing source names. Adding by "
            "keyboard or drag remains pending until Confirm. Automation suggestions require "
            "Accept or Reject. Cancel is the safe default."
        )

        content = QWidget()
        content.setObjectName("checklistEditorContent")
        content_layout = QVBoxLayout(content)

        heading = QLabel("Review checklist assignments")
        heading.setObjectName("checklistEditorHeading")
        heading.setAccessibleName("Review checklist assignments")
        heading.setTextFormat(Qt.TextFormat.PlainText)
        heading.setWordWrap(True)
        heading_font = QFont(heading.font())
        heading_font.setBold(True)
        heading_font.setPointSizeF(max(heading_font.pointSizeF(), 14.0))
        heading.setFont(heading_font)
        content_layout.addWidget(heading)

        guidance = QLabel(
            "Add groups selected pages as one document. Added documents are not complete: select "
            "the document and choose Confirm. Suggestions made by automation never count until "
            "you choose Accept suggestion. Every drag action has a named keyboard button."
        )
        guidance.setObjectName("checklistEditorGuidance")
        guidance.setAccessibleName("Checklist assignment instructions")
        guidance.setAccessibleDescription(guidance.text())
        guidance.setTextFormat(Qt.TextFormat.PlainText)
        guidance.setWordWrap(True)
        content_layout.addWidget(guidance)

        requirement_group = QGroupBox("Checklist categories")
        requirement_group.setObjectName("checklistRequirementGroup")
        requirement_group.setAccessibleName("Checklist categories and textual statuses")
        requirement_group.setAccessibleDescription(
            "Each category states Missing, Added, Needs review, or Ready in text. Categories are "
            "also the optional internal page-drop targets."
        )
        requirement_layout = QVBoxLayout(requirement_group)
        requirement_label = QLabel("Category")
        self._requirement_list = QListView()
        self._requirement_list.setObjectName("checklistRequirementList")
        self._requirement_list.setAccessibleName("Checklist categories")
        self._requirement_list.setAccessibleDescription(
            "Select one category. Status and confirmed count are included in every row. "
            "Internal UUID-only page rows may be dropped here, but dropping only adds pending work."
        )
        self._requirement_list.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self._requirement_list.setAcceptDrops(True)
        self._requirement_list.setDragDropMode(QListView.DragDropMode.DropOnly)
        self._requirement_list.setDropIndicatorShown(True)
        requirement_label.setBuddy(self._requirement_list)
        requirement_layout.addWidget(requirement_label)
        requirement_layout.addWidget(self._requirement_list)
        self._requirement_guidance = QLabel("Select a checklist category to read its guidance.")
        self._requirement_guidance.setObjectName("checklistRequirementGuidance")
        self._requirement_guidance.setAccessibleName("Selected checklist category guidance")
        self._requirement_guidance.setAccessibleDescription(self._requirement_guidance.text())
        self._requirement_guidance.setTextFormat(Qt.TextFormat.PlainText)
        self._requirement_guidance.setWordWrap(True)
        self._requirement_guidance.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._requirement_guidance.setStyleSheet(_FOCUS_VISIBLE_TEXT_STYLE)
        self._requirement_guidance.setFrameShape(QFrame.Shape.StyledPanel)
        requirement_layout.addWidget(self._requirement_guidance)
        content_layout.addWidget(requirement_group)

        page_group = QGroupBox("Pages without a confirmed assignment")
        page_group.setObjectName("checklistAvailablePageGroup")
        page_group.setAccessibleName("Pages without a confirmed assignment")
        page_group.setAccessibleDescription(
            "Unassigned, added, and suggested page rows are identified only by case page position. "
            "Only unassigned rows can be added or dragged."
        )
        page_layout = QVBoxLayout(page_group)
        page_label = QLabel("Case pages")
        self._page_list = QListView()
        self._page_list.setObjectName("checklistAvailablePageList")
        self._page_list.setAccessibleName("Pages without a confirmed assignment")
        self._page_list.setAccessibleDescription(
            "Select one or more unassigned page positions, then use Add selected pages. "
            "Drag is optional and carries internal page identifiers only."
        )
        self._page_list.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        self._page_list.setDragEnabled(True)
        self._page_list.setDragDropMode(QListView.DragDropMode.DragOnly)
        self._page_list.setDefaultDropAction(Qt.DropAction.CopyAction)
        page_label.setBuddy(self._page_list)
        page_layout.addWidget(page_label)
        page_layout.addWidget(self._page_list)
        self._add_button = self._button(
            "Add selected pages",
            "checklistAddPagesButton",
            "Add the selected unassigned page positions to the selected category as one pending "
            "user document. This does not confirm the assignment.",
        )
        self._add_button.clicked.connect(self._add_selected_pages)
        page_layout.addWidget(self._add_button)
        content_layout.addWidget(page_group)

        assignment_group = QGroupBox("Documents in selected category")
        assignment_group.setObjectName("checklistAssignmentGroup")
        assignment_group.setAccessibleName("Documents in selected checklist category")
        assignment_group.setAccessibleDescription(
            "Document rows state whether they are Added, an automation suggestion, or confirmed. "
            "All review, category, order, and removal actions have keyboard buttons."
        )
        assignment_layout = QVBoxLayout(assignment_group)
        assignment_label = QLabel("Documents")
        self._assignment_list = QListView()
        self._assignment_list.setObjectName("checklistAssignmentList")
        self._assignment_list.setAccessibleName("Documents in selected category")
        self._assignment_list.setAccessibleDescription(
            "Select one logical document to review, move, reorder, or remove."
        )
        self._assignment_list.setSelectionMode(QListView.SelectionMode.SingleSelection)
        assignment_label.setBuddy(self._assignment_list)
        assignment_layout.addWidget(assignment_label)
        assignment_layout.addWidget(self._assignment_list)

        review_actions = QHBoxLayout()
        self._confirm_button = self._button(
            "Confirm added document",
            "checklistConfirmButton",
            "Explicitly confirm the selected user-added document. Only confirmation can make it "
            "count toward Ready.",
        )
        self._accept_button = self._button(
            "Accept suggestion",
            "checklistAcceptSuggestionButton",
            "Explicitly accept and confirm the selected automation suggestion.",
        )
        self._reject_button = self._button(
            "Reject suggestion",
            "checklistRejectSuggestionButton",
            "Reject and remove the selected automation suggestion without confirming it.",
        )
        self._remove_button = self._button(
            "Remove assignment",
            "checklistRemoveAssignmentButton",
            "Unassign the selected user-added or confirmed document and return its pages to the "
            "unassigned list.",
        )
        self._confirm_button.clicked.connect(self._confirm_assignment)
        self._accept_button.clicked.connect(self._accept_suggestion)
        self._reject_button.clicked.connect(self._reject_suggestion)
        self._remove_button.clicked.connect(self._remove_assignment)
        for button in (
            self._confirm_button,
            self._accept_button,
            self._reject_button,
            self._remove_button,
        ):
            review_actions.addWidget(button)
        review_actions.addStretch(1)
        assignment_layout.addLayout(review_actions)

        category_actions = QHBoxLayout()
        target_label = QLabel("Move to category")
        self._target_requirement = QComboBox()
        self._target_requirement.setObjectName("checklistMoveTargetCombo")
        self._target_requirement.setAccessibleName("Destination checklist category")
        self._target_requirement.setAccessibleDescription(
            "Choose another checklist category for the selected document. Moving requires "
            "confirmation again."
        )
        target_label.setBuddy(self._target_requirement)
        self._move_category_button = self._button(
            "Move document to category",
            "checklistMoveCategoryButton",
            "Move the selected document to the chosen category. The moved document becomes Added "
            "and requires explicit confirmation again.",
        )
        self._move_category_button.clicked.connect(self._move_assignment)
        self._target_requirement.currentIndexChanged.connect(self._update_action_availability)
        category_actions.addWidget(target_label)
        category_actions.addWidget(self._target_requirement, 1)
        category_actions.addWidget(self._move_category_button)
        assignment_layout.addLayout(category_actions)

        document_order = QHBoxLayout()
        self._document_earlier_button = self._button(
            "Move document earlier",
            "checklistDocumentEarlierButton",
            "Move the selected document one position earlier in this category.",
        )
        self._document_later_button = self._button(
            "Move document later",
            "checklistDocumentLaterButton",
            "Move the selected document one position later in this category.",
        )
        self._document_earlier_button.clicked.connect(lambda: self._reorder_document(-1))
        self._document_later_button.clicked.connect(lambda: self._reorder_document(1))
        document_order.addWidget(self._document_earlier_button)
        document_order.addWidget(self._document_later_button)
        document_order.addStretch(1)
        assignment_layout.addLayout(document_order)

        assigned_page_label = QLabel("Pages in selected document")
        self._assigned_page_list = QListView()
        self._assigned_page_list.setObjectName("checklistAssignedPageList")
        self._assigned_page_list.setAccessibleName("Ordered pages in selected document")
        self._assigned_page_list.setAccessibleDescription(
            "Select one case page position and use the named earlier or later button to reorder it."
        )
        self._assigned_page_list.setSelectionMode(QListView.SelectionMode.SingleSelection)
        assigned_page_label.setBuddy(self._assigned_page_list)
        assignment_layout.addWidget(assigned_page_label)
        assignment_layout.addWidget(self._assigned_page_list)
        page_order = QHBoxLayout()
        self._page_earlier_button = self._button(
            "Move page earlier",
            "checklistPageEarlierButton",
            "Move the selected page one position earlier inside this document.",
        )
        self._page_later_button = self._button(
            "Move page later",
            "checklistPageLaterButton",
            "Move the selected page one position later inside this document.",
        )
        self._page_earlier_button.clicked.connect(lambda: self._reorder_page(-1))
        self._page_later_button.clicked.connect(lambda: self._reorder_page(1))
        page_order.addWidget(self._page_earlier_button)
        page_order.addWidget(self._page_later_button)
        page_order.addStretch(1)
        assignment_layout.addLayout(page_order)
        content_layout.addWidget(assignment_group)

        history_actions = QHBoxLayout()
        self._undo_button = self._button(
            "Undo checklist change",
            "checklistUndoButton",
            "Undo the most recent checklist change made in this editor. Ctrl+Z.",
        )
        self._redo_button = self._button(
            "Redo checklist change",
            "checklistRedoButton",
            "Redo the next checklist change in this editor. Ctrl+Y.",
        )
        self._undo_button.setShortcut("Ctrl+Z")
        self._redo_button.setShortcut("Ctrl+Y")
        self._undo_button.clicked.connect(self._undo)
        self._redo_button.clicked.connect(self._redo)
        history_actions.addWidget(self._undo_button)
        history_actions.addWidget(self._redo_button)
        history_actions.addStretch(1)
        content_layout.addLayout(history_actions)

        self._status = QLabel(state.announcement)
        self._status.setObjectName("checklistEditorStatus")
        self._status.setAccessibleName("Checklist editor status")
        self._status.setAccessibleDescription(state.announcement)
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setWordWrap(True)
        self._status.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._status.setStyleSheet(_FOCUS_VISIBLE_TEXT_STYLE)
        self._status.setFrameShape(QFrame.Shape.StyledPanel)
        content_layout.addWidget(self._status)
        content_layout.addStretch(1)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("checklistEditorScrollArea")
        self._scroll.setAccessibleName("Checklist assignment controls")
        self._scroll.setAccessibleDescription(
            "Scrollable category, page, document, review, ordering, and history controls."
        )
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._scroll.setWidget(content)
        self._scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        buttons = QDialogButtonBox()
        buttons.setObjectName("checklistEditorButtons")
        buttons.setAccessibleName("Apply or cancel checklist changes")
        buttons.setAccessibleDescription(
            "Cancel is the safe default. Apply requests saving the complete detached checklist "
            "state and waits for a confirmed result."
        )
        self._apply_button = self._button(
            "Apply checklist changes",
            "checklistApplyButton",
            "Request saving the complete local checklist draft. The dialog stays open until the "
            "save succeeds or fails safely.",
        )
        self._apply_button.setDefault(False)
        self._apply_button.setAutoDefault(False)
        buttons.addButton(self._apply_button, QDialogButtonBox.ButtonRole.AcceptRole)
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        if not isinstance(cancel, QPushButton):
            raise RuntimeError("Qt did not create the required Cancel button")
        self._cancel_button = cancel
        self._cancel_button.setObjectName("checklistCancelButton")
        self._cancel_button.setAccessibleName("Cancel checklist changes")
        self._cancel_button.setAccessibleDescription(
            "Discard this local checklist draft without requesting a case change. Escape."
        )
        self._cancel_button.setDefault(True)
        self._cancel_button.setAutoDefault(True)
        self._apply_button.clicked.connect(self._request_apply)
        self._cancel_button.clicked.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._scroll, 1)
        layout.addWidget(buttons)

        self._requirement_model = ChecklistRequirementListModel(self)
        self._page_model = ChecklistPageListModel(self)
        self._assignment_model = ChecklistAssignmentListModel(self)
        self._assigned_page_model = ChecklistAssignedPageListModel(self)
        self._requirement_list.setModel(self._requirement_model)
        self._page_list.setModel(self._page_model)
        self._assignment_list.setModel(self._assignment_model)
        self._assigned_page_list.setModel(self._assigned_page_model)
        self._requirement_model.pages_dropped.connect(self._add_pages)
        self._requirement_list.selectionModel().currentChanged.connect(
            self._requirement_selection_changed
        )
        self._page_list.selectionModel().selectionChanged.connect(self._page_selection_changed)
        self._assignment_list.selectionModel().currentChanged.connect(
            self._assignment_selection_changed
        )
        self._assigned_page_list.selectionModel().currentChanged.connect(
            self._assigned_page_selection_changed
        )

        self._editing_controls: tuple[QWidget, ...] = (
            self._requirement_list,
            self._page_list,
            self._add_button,
            self._assignment_list,
            self._confirm_button,
            self._accept_button,
            self._reject_button,
            self._remove_button,
            self._target_requirement,
            self._move_category_button,
            self._document_earlier_button,
            self._document_later_button,
            self._assigned_page_list,
            self._page_earlier_button,
            self._page_later_button,
            self._undo_button,
            self._redo_button,
        )
        tab_chain: tuple[QWidget, ...] = (
            self._cancel_button,
            self._scroll,
            self._requirement_list,
            self._requirement_guidance,
            self._page_list,
            self._add_button,
            self._assignment_list,
            self._confirm_button,
            self._accept_button,
            self._reject_button,
            self._remove_button,
            self._target_requirement,
            self._move_category_button,
            self._document_earlier_button,
            self._document_later_button,
            self._assigned_page_list,
            self._page_earlier_button,
            self._page_later_button,
            self._undo_button,
            self._redo_button,
            self._status,
            self._apply_button,
            self._cancel_button,
        )
        for current, following in pairwise(tab_chain):
            QWidget.setTabOrder(current, following)

        self._refresh()
        self.resize(900, 760)
        self._cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)

    @property
    def state(self) -> ChecklistEditorState:
        """Return the complete detached checklist draft, never a manifest."""

        return self._state

    @property
    def apply_pending(self) -> bool:
        return self._apply_pending

    @property
    def commit_started(self) -> bool:
        return self._commit_started

    def latest_state_for_shutdown(self) -> ChecklistEditorState | None:
        """Return a safe draft for shutdown only when no Apply is in flight."""

        return None if self._apply_pending else self._state

    def mark_applied(self) -> bool:
        """Accept only after the caller confirms the pending state was saved."""

        if not self._apply_pending:
            return False
        self._apply_pending = False
        self._set_status("Checklist changes applied.", announce=True)
        super().accept()
        return True

    def mark_apply_failed(self, _code: str, _message: str) -> bool:
        """Restore retained editing after one sanitized save failure."""

        if not self._apply_pending:
            return False
        self._apply_pending = False
        self._set_editor_enabled(True)
        self._apply_button.setAccessibleDescription("Retry saving the retained checklist draft.")
        self._set_status(
            "PWS_CHECKLIST_APPLY_FAILED: PaperWorkShell could not save checklist changes safely. "
            "Changes are retained; review them and try Apply again.",
            announce=True,
            assertive=True,
        )
        self._status.setFocus(Qt.FocusReason.OtherFocusReason)
        self._scroll.ensureWidgetVisible(self._status, 8, 8)
        return True

    def reject(self) -> None:
        """Discard only before the draft crosses the persistence boundary."""

        if self._commit_started:
            QApplication.beep()
            self._set_status(
                "These checklist changes are retained for retry and cannot be discarded from "
                "this dialog after Apply was requested.",
                announce=True,
                assertive=True,
            )
            self._status.setFocus(Qt.FocusReason.OtherFocusReason)
            self._scroll.ensureWidgetVisible(self._status, 8, 8)
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override name
        if self._commit_started:
            event.ignore()
            self.reject()
            return
        super().closeEvent(event)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt override name
        self._cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)
        super().showEvent(event)

    def _button(self, text: str, object_name: str, description: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(object_name)
        button.setAccessibleName(text)
        button.setAccessibleDescription(description)
        return button

    def _current_requirement(self) -> ChecklistRequirementView | None:
        return self._requirement_model.requirement_at(self._requirement_list.currentIndex().row())

    def _current_assignment(self) -> ChecklistAssignmentView | None:
        return self._assignment_model.assignment_at(self._assignment_list.currentIndex().row())

    def _current_assigned_page(self) -> ChecklistPageView | None:
        return self._assigned_page_model.page_at(self._assigned_page_list.currentIndex().row())

    def _refresh(
        self,
        *,
        requirement_id: str | None = None,
        assignment_id: UUID | None = None,
        page_id: UUID | None = None,
    ) -> None:
        current_requirement = self._current_requirement()
        current_assignment = self._current_assignment()
        current_page = self._current_assigned_page()
        previous_requirement = requirement_id or (
            current_requirement.requirement_id if current_requirement is not None else None
        )
        previous_assignment = assignment_id or (
            current_assignment.assignment_id if current_assignment is not None else None
        )
        previous_page = page_id or (current_page.page_id if current_page is not None else None)
        self._page_model.replace(self._state.pages, self._state.assignments)
        self._requirement_model.replace(
            self._state.requirements,
            self._page_model.unassigned_page_ids(),
        )
        requirement_row = self._requirement_model.row_for_id(previous_requirement)
        if requirement_row < 0 and self._requirement_model.rowCount() > 0:
            requirement_row = 0
        if requirement_row >= 0:
            self._requirement_list.setCurrentIndex(
                self._requirement_model.index(requirement_row, 0)
            )
        else:
            self._assignment_model.replace(())
            self._assigned_page_model.replace(())
        self._refresh_assignments(previous_assignment, previous_page)
        self._refresh_requirement_guidance()
        self._populate_target_requirements()
        self._set_status(self._state.announcement, announce=False)
        self._update_action_availability()

    def _refresh_assignments(
        self,
        assignment_id: UUID | None = None,
        page_id: UUID | None = None,
    ) -> None:
        requirement = self._current_requirement()
        rows = (
            tuple(
                assignment
                for assignment in self._state.assignments
                if assignment.requirement_id == requirement.requirement_id
            )
            if requirement is not None
            else ()
        )
        self._assignment_model.replace(rows)
        assignment_row = self._assignment_model.row_for_id(assignment_id)
        if assignment_row < 0 and rows:
            assignment_row = 0
        if assignment_row >= 0:
            self._assignment_list.setCurrentIndex(self._assignment_model.index(assignment_row, 0))
        else:
            self._assignment_list.setCurrentIndex(QModelIndex())
        self._refresh_assigned_pages(page_id)

    def _refresh_assigned_pages(self, page_id: UUID | None = None) -> None:
        assignment = self._current_assignment()
        page_by_id = {page.page_id: page for page in self._state.pages}
        rows = (
            tuple(
                page_by_id[selected] for selected in assignment.page_ids if selected in page_by_id
            )
            if assignment is not None
            else ()
        )
        inactive_count = 0 if assignment is None else len(assignment.page_ids) - len(rows)
        description = (
            "Select one active case page position and use the named earlier or later button "
            "to reorder it."
        )
        if inactive_count:
            description += (
                f" {inactive_count} inactive assignment page"
                f"{'s are' if inactive_count != 1 else ' is'} omitted; the document remains "
                "Needs review."
            )
        self._assigned_page_list.setAccessibleDescription(description)
        self._assigned_page_model.replace(rows)
        page_row = self._assigned_page_model.row_for_id(page_id)
        if page_row < 0 and rows:
            page_row = 0
        if page_row >= 0:
            self._assigned_page_list.setCurrentIndex(self._assigned_page_model.index(page_row, 0))
        else:
            self._assigned_page_list.setCurrentIndex(QModelIndex())

    def _populate_target_requirements(self) -> None:
        selected = self._target_requirement.currentData()
        self._target_requirement.blockSignals(True)
        self._target_requirement.clear()
        for requirement in self._state.requirements:
            self._target_requirement.addItem(requirement.display_name, requirement.requirement_id)
        target_index = self._target_requirement.findData(selected)
        if target_index < 0:
            current = self._current_requirement()
            target_index = (
                self._target_requirement.findData(current.requirement_id)
                if current is not None
                else 0
            )
        if target_index >= 0:
            self._target_requirement.setCurrentIndex(target_index)
        self._target_requirement.blockSignals(False)

    def _refresh_requirement_guidance(self) -> None:
        requirement = self._current_requirement()
        message = (
            "Select a checklist category to read its guidance."
            if requirement is None
            else (
                f"{requirement.display_name}. {_STATUS_LABELS[requirement.status]}. "
                f"{_count_text(requirement)}. {requirement.description}"
            )
        )
        self._requirement_guidance.setText(message)
        self._requirement_guidance.setAccessibleDescription(message)

    def _selected_unassigned_page_ids(self) -> tuple[UUID, ...]:
        pages: list[ChecklistPageView] = []
        for index in self._page_list.selectionModel().selectedIndexes():
            page = self._page_model.page_at(index.row())
            if page is not None and page.assignment_id is None:
                pages.append(page)
        pages.sort(key=lambda item: item.position)
        return tuple(page.page_id for page in pages)

    def _add_selected_pages(self) -> None:
        requirement = self._current_requirement()
        if requirement is None:
            return
        self._add_pages(requirement.requirement_id, self._selected_unassigned_page_ids())

    def _add_pages(self, requirement_id: str, value: object) -> None:
        page_ids = value if type(value) is tuple else ()
        if not page_ids:
            self._show_action_error("Select at least one unassigned page before adding a document.")
            return
        before_ids = {assignment.assignment_id for assignment in self._state.assignments}
        if not self._change_state(
            lambda state: state.add_pages(requirement_id, page_ids),
            "Document added. It is not confirmed; select it and choose Confirm added document.",
        ):
            return
        added = next(
            (
                assignment
                for assignment in self._state.assignments
                if assignment.assignment_id not in before_ids
            ),
            None,
        )
        self._refresh(
            requirement_id=requirement_id,
            assignment_id=None if added is None else added.assignment_id,
        )
        self._assignment_list.setFocus(Qt.FocusReason.OtherFocusReason)

    def _confirm_assignment(self) -> None:
        assignment = self._current_assignment()
        if (
            assignment is None
            or assignment.origin is not AssignmentOrigin.USER
            or assignment.review_state is AssignmentReviewState.USER_CONFIRMED
        ):
            return
        if self._change_state(
            lambda state: state.confirm_assignment(assignment.assignment_id),
            "Added document confirmed by the user.",
        ):
            self._refresh(
                requirement_id=assignment.requirement_id,
                assignment_id=assignment.assignment_id,
            )
            self._assignment_list.setFocus(Qt.FocusReason.OtherFocusReason)

    def _accept_suggestion(self) -> None:
        assignment = self._current_assignment()
        if (
            assignment is None
            or assignment.origin is not AssignmentOrigin.AUTOMATION
            or assignment.review_state is AssignmentReviewState.USER_CONFIRMED
        ):
            return
        if self._change_state(
            lambda state: state.accept_suggestion(assignment.assignment_id),
            "Automation suggestion accepted and confirmed by the user.",
        ):
            self._refresh(
                requirement_id=assignment.requirement_id,
                assignment_id=assignment.assignment_id,
            )
            self._assignment_list.setFocus(Qt.FocusReason.OtherFocusReason)

    def _reject_suggestion(self) -> None:
        assignment = self._current_assignment()
        if (
            assignment is None
            or assignment.origin is not AssignmentOrigin.AUTOMATION
            or assignment.review_state is AssignmentReviewState.USER_CONFIRMED
        ):
            return
        if self._change_state(
            lambda state: state.reject_suggestion(assignment.assignment_id),
            "Automation suggestion rejected and removed.",
        ):
            self._refresh(requirement_id=assignment.requirement_id)
            self._assignment_list.setFocus(Qt.FocusReason.OtherFocusReason)

    def _remove_assignment(self) -> None:
        assignment = self._current_assignment()
        if (
            assignment is None
            or assignment.origin is AssignmentOrigin.AUTOMATION
            and (assignment.review_state is AssignmentReviewState.SUGGESTED)
        ):
            return
        first_page = assignment.page_ids[0]
        if self._change_state(
            lambda state: state.remove_assignment(assignment.assignment_id),
            "Document assignment removed. Its pages are unassigned.",
        ):
            self._refresh(requirement_id=assignment.requirement_id)
            row = self._page_model.row_for_id(first_page)
            if row >= 0:
                self._page_list.setCurrentIndex(self._page_model.index(row, 0))
            self._page_list.setFocus(Qt.FocusReason.OtherFocusReason)

    def _move_assignment(self) -> None:
        assignment = self._current_assignment()
        target = self._target_requirement.currentData()
        if (
            assignment is None
            or (
                assignment.origin is AssignmentOrigin.AUTOMATION
                and assignment.review_state is AssignmentReviewState.SUGGESTED
            )
            or type(target) is not str
            or target == assignment.requirement_id
        ):
            return
        if self._change_state(
            lambda state: state.move_assignment(assignment.assignment_id, target),
            "Document moved. It is Added in the new category and requires confirmation again.",
        ):
            self._refresh(requirement_id=target, assignment_id=assignment.assignment_id)
            self._assignment_list.setFocus(Qt.FocusReason.OtherFocusReason)

    def _reorder_document(self, offset: int) -> None:
        assignment = self._current_assignment()
        requirement = self._current_requirement()
        current_row = self._assignment_list.currentIndex().row()
        target_row = current_row + offset
        rows = list(self._assignment_model.assignments)
        if (
            assignment is None
            or requirement is None
            or (
                assignment.origin is AssignmentOrigin.AUTOMATION
                and assignment.review_state is AssignmentReviewState.SUGGESTED
            )
            or not 0 <= target_row < len(rows)
        ):
            return
        rows[current_row], rows[target_row] = rows[target_row], rows[current_row]
        order = tuple(item.assignment_id for item in rows)
        if self._change_state(
            lambda state: state.reorder_requirement_assignments(
                requirement.requirement_id,
                order,
            ),
            "Document order changed.",
        ):
            self._refresh(
                requirement_id=requirement.requirement_id,
                assignment_id=assignment.assignment_id,
            )
            self._assignment_list.setFocus(Qt.FocusReason.OtherFocusReason)

    def _reorder_page(self, offset: int) -> None:
        assignment = self._current_assignment()
        page = self._current_assigned_page()
        current_row = self._assigned_page_list.currentIndex().row()
        target_row = current_row + offset
        if (
            assignment is None
            or page is None
            or (
                assignment.origin is AssignmentOrigin.AUTOMATION
                and assignment.review_state is AssignmentReviewState.SUGGESTED
            )
            or not 0 <= target_row < len(assignment.page_ids)
        ):
            return
        order = list(assignment.page_ids)
        order[current_row], order[target_row] = order[target_row], order[current_row]
        if self._change_state(
            lambda state: state.reorder_assignment_pages(assignment.assignment_id, tuple(order)),
            "Page order changed inside the selected document.",
        ):
            self._refresh(
                requirement_id=assignment.requirement_id,
                assignment_id=assignment.assignment_id,
                page_id=page.page_id,
            )
            self._assigned_page_list.setFocus(Qt.FocusReason.OtherFocusReason)

    def _undo(self) -> None:
        if self._state.can_undo and self._change_state(
            lambda state: state.undo(),
            "Checklist change undone.",
        ):
            self._refresh()
            self._undo_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _redo(self) -> None:
        if self._state.can_redo and self._change_state(
            lambda state: state.redo(),
            "Checklist change redone.",
        ):
            self._refresh()
            self._redo_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _change_state(
        self,
        action: Callable[[ChecklistEditorState], ChecklistEditorState],
        announcement: str,
    ) -> bool:
        if self._apply_pending:
            return False
        try:
            next_state = action(self._state)
        except ChecklistError as error:
            self._show_action_error(f"{error.code.value}: {error.safe_message}")
            return False
        if type(next_state) is not ChecklistEditorState:
            self._show_action_error(
                "PWS_CHECKLIST_ACTION_FAILED: PaperWorkShell could not apply that checklist "
                "action safely."
            )
            return False
        self._state = next_state
        self._set_status(announcement, announce=True)
        self.state_changed.emit(next_state)
        return True

    def _show_action_error(self, message: str) -> None:
        self._set_status(message, announce=True, assertive=True)
        self._status.setFocus(Qt.FocusReason.OtherFocusReason)
        self._scroll.ensureWidgetVisible(self._status, 8, 8)

    def _request_apply(self) -> None:
        if self._apply_pending or not (self._state.changed or self._commit_started):
            return
        self._apply_pending = True
        self._commit_started = True
        self._set_editor_enabled(False)
        self._set_status(
            "Applying checklist changes. Keep this dialog open.",
            announce=True,
        )
        self.apply_requested.emit(self._state)

    def _set_editor_enabled(self, enabled: bool) -> None:
        for control in self._editing_controls:
            control.setEnabled(enabled)
        self._apply_button.setEnabled(enabled and (self._state.changed or self._commit_started))
        self._cancel_button.setEnabled(enabled and not self._commit_started)
        if self._commit_started:
            self._cancel_button.setAccessibleDescription(
                "Unavailable after the first Apply request because changes are retained for retry."
            )
        if enabled:
            self._update_action_availability()

    def _set_status(
        self,
        message: str,
        *,
        announce: bool,
        assertive: bool = False,
    ) -> None:
        self._status.setText(message)
        self._status.setAccessibleDescription(message)
        if not announce:
            return
        event = QAccessibleAnnouncementEvent(self._status, message)
        event.setPoliteness(
            QAccessible.AnnouncementPoliteness.Assertive
            if assertive
            else QAccessible.AnnouncementPoliteness.Polite
        )
        QAccessible.updateAccessibility(event)

    def _requirement_selection_changed(
        self,
        _current: QModelIndex,
        _previous: QModelIndex,
    ) -> None:
        self._refresh_assignments()
        self._refresh_requirement_guidance()
        self._populate_target_requirements()
        self._update_action_availability()

    def _page_selection_changed(self, _selected: object, _deselected: object) -> None:
        self._update_action_availability()

    def _assignment_selection_changed(
        self,
        _current: QModelIndex,
        _previous: QModelIndex,
    ) -> None:
        self._refresh_assigned_pages()
        assignment = self._current_assignment()
        if assignment is not None:
            target_index = self._target_requirement.findData(assignment.requirement_id)
            if target_index >= 0:
                self._target_requirement.setCurrentIndex(target_index)
        self._update_action_availability()

    def _assigned_page_selection_changed(
        self,
        _current: QModelIndex,
        _previous: QModelIndex,
    ) -> None:
        self._update_action_availability()

    def _update_action_availability(self, *_ignored: object) -> None:
        if self._apply_pending:
            return
        requirement = self._current_requirement()
        assignment = self._current_assignment()
        page_ids = self._selected_unassigned_page_ids()
        self._add_button.setEnabled(requirement is not None and bool(page_ids))
        pending = (
            assignment is not None and assignment.review_state is AssignmentReviewState.SUGGESTED
        )
        self._confirm_button.setEnabled(
            pending and assignment is not None and assignment.origin is AssignmentOrigin.USER
        )
        self._accept_button.setEnabled(
            pending and assignment is not None and assignment.origin is AssignmentOrigin.AUTOMATION
        )
        self._reject_button.setEnabled(
            pending and assignment is not None and assignment.origin is AssignmentOrigin.AUTOMATION
        )
        self._remove_button.setEnabled(
            assignment is not None
            and not (
                pending
                and assignment is not None
                and assignment.origin is AssignmentOrigin.AUTOMATION
            )
        )
        automation_pending = (
            pending and assignment is not None and assignment.origin is AssignmentOrigin.AUTOMATION
        )
        target = self._target_requirement.currentData()
        self._move_category_button.setEnabled(
            assignment is not None
            and not automation_pending
            and type(target) is str
            and target != assignment.requirement_id
        )
        assignment_row = self._assignment_list.currentIndex().row()
        self._document_earlier_button.setEnabled(
            assignment is not None and not automation_pending and assignment_row > 0
        )
        self._document_later_button.setEnabled(
            assignment is not None
            and not automation_pending
            and assignment_row + 1 < self._assignment_model.rowCount()
        )
        page = self._current_assigned_page()
        page_row = self._assigned_page_list.currentIndex().row()
        all_assignment_pages_visible = assignment is not None and (
            self._assigned_page_model.rowCount() == len(assignment.page_ids)
        )
        self._page_earlier_button.setEnabled(
            page is not None
            and all_assignment_pages_visible
            and not automation_pending
            and page_row > 0
        )
        self._page_later_button.setEnabled(
            page is not None
            and all_assignment_pages_visible
            and not automation_pending
            and page_row + 1 < self._assigned_page_model.rowCount()
        )
        self._undo_button.setEnabled(self._state.can_undo)
        self._redo_button.setEnabled(self._state.can_redo)
        self._apply_button.setEnabled(self._state.changed or self._commit_started)


__all__ = (
    "CHECKLIST_PAGE_MIME_TYPE",
    "ChecklistAssignedPageListModel",
    "ChecklistAssignmentListModel",
    "ChecklistEditorDialog",
    "ChecklistPageListModel",
    "ChecklistRequirementListModel",
)

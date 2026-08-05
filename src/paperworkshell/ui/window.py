"""Keyboard-first PaperWorkShell case and page organizer window."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from pathlib import Path
from typing import Final
from uuid import UUID

from PySide6.QtCore import (
    QEvent,
    QItemSelection,
    QItemSelectionModel,
    QObject,
    Qt,
    QTimer,
    Slot,
)
from PySide6.QtGui import (
    QAccessible,
    QAccessibleAnnouncementEvent,
    QCloseEvent,
    QImage,
    QKeySequence,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from paperworkshell.app.organizer import (
    MAX_ORGANIZER_PREVIEW_BATCH_PAGES,
    OrganizerViewState,
)
from paperworkshell.domain.case_lifecycle import CaseIndex
from paperworkshell.domain.pdf_thumbnails import PdfThumbnailResult
from paperworkshell.ui.backend import (
    OperationCancellation,
    OrganizerBackendCommands,
    OrganizerBackendWorker,
    UiFailure,
    UiProgress,
)
from paperworkshell.ui.models import OrganizerPageListModel, PreviewState

_AUTOSAVE_DEBOUNCE_MS: Final = 400
_THUMBNAIL_WIDTH: Final = 240
_THUMBNAIL_HEIGHT: Final = 320
_PREVIEW_OVERSCAN_ROWS: Final = 2
_PREVIEW_REQUEST_CAP: Final = MAX_ORGANIZER_PREVIEW_BATCH_PAGES
_PREVIEW_PIXMAP_BUDGET: Final = 12
_PREVIEW_REFRESH_DEBOUNCE_MS: Final = 75
_MAX_OPERATION_ID: Final = 2_147_483_647


class _LongOperationKind(StrEnum):
    IMPORT = "Import"
    PREVIEW = "Preview"


@dataclass(frozen=True, slots=True)
class _LongOperation:
    operation_id: int
    kind: _LongOperationKind
    cancellation: OperationCancellation


def _bounded_preview_rows(
    row_count: int,
    visible_rows: tuple[int, ...],
    selected_row: int,
    *,
    overscan_rows: int = _PREVIEW_OVERSCAN_ROWS,
    maximum_count: int = _PREVIEW_PIXMAP_BUDGET,
) -> tuple[int, ...]:
    """Choose a deterministic visible/selected window without exceeding memory policy."""

    if row_count <= 0 or maximum_count <= 0:
        return ()
    visible = tuple(sorted({row for row in visible_rows if 0 <= row < row_count}))
    selection = selected_row if 0 <= selected_row < row_count else None
    anchors = set(visible)
    if selection is not None:
        anchors.add(selection)
    if not anchors:
        anchors.add(0)
    candidates: set[int] = set()
    for anchor in anchors:
        start = max(0, anchor - max(0, overscan_rows))
        stop = min(row_count, anchor + max(0, overscan_rows) + 1)
        candidates.update(range(start, stop))
    visible_center = (
        (visible[0] + visible[-1]) / 2
        if visible
        else float(selection if selection is not None else 0)
    )

    def priority(row: int) -> tuple[int, float, int]:
        if selection is not None and row == selection:
            return (0, 0.0, row)
        if row in visible:
            return (1, abs(row - visible_center), row)
        distance = min(abs(row - anchor) for anchor in anchors)
        return (2, float(distance), row)

    chosen = sorted(candidates, key=priority)[:maximum_count]
    return tuple(sorted(chosen))


class PageOrderDialog(QDialog):
    """Explicit non-drag page ordering workflow with keyboard controls."""

    def __init__(
        self,
        pages: tuple[tuple[UUID, int], ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Change page order")
        self.setAccessibleName("Change page order dialog")
        self.setAccessibleDescription(
            "Select a page and use Move earlier or Move later. Dragging is not required."
        )
        self._page_list = QListWidget()
        self._page_list.setObjectName("pageOrderList")
        self._page_list.setAccessibleName("Proposed page order")
        self._page_list.setAccessibleDescription(
            "Pages in proposed order. Select one, then use the visible move buttons."
        )
        self._page_list.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        for page_id, position in pages:
            item = QListWidgetItem(f"Page {position}")
            item.setData(Qt.ItemDataRole.UserRole, page_id)
            self._page_list.addItem(item)
        if self._page_list.count():
            self._page_list.setCurrentRow(0)

        self._move_earlier = QPushButton("Move earlier")
        self._move_earlier.setObjectName("orderMoveEarlierButton")
        self._move_earlier.setAccessibleDescription("Move the selected page one position earlier.")
        self._move_later = QPushButton("Move later")
        self._move_later.setObjectName("orderMoveLaterButton")
        self._move_later.setAccessibleDescription("Move the selected page one position later.")
        self._move_earlier.clicked.connect(lambda: self._move(-1))
        self._move_later.clicked.connect(lambda: self._move(1))
        self._page_list.currentRowChanged.connect(self._update_move_actions)
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.setAccessibleName("Confirm or cancel page order")
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        move_layout = QHBoxLayout()
        move_layout.addWidget(self._move_earlier)
        move_layout.addWidget(self._move_later)
        layout = QVBoxLayout(self)
        instructions = QLabel(
            "Select a page, then choose Move earlier or Move later. "
            "Use Alt+Up and Alt+Down as keyboard shortcuts."
        )
        instructions.setWordWrap(True)
        instructions.setAccessibleName("Page order instructions")
        layout.addWidget(instructions)
        layout.addWidget(self._page_list)
        layout.addLayout(move_layout)
        self._move_status = QLabel()
        self._move_status.setObjectName("pageOrderStatus")
        self._move_status.setAccessibleName("Page order change status")
        self._move_status.setWordWrap(True)
        layout.addWidget(self._move_status)
        layout.addWidget(button_box)

        self._earlier_shortcut = QShortcut(QKeySequence("Alt+Up"), self)
        self._earlier_shortcut.activated.connect(lambda: self._move(-1))
        self._later_shortcut = QShortcut(QKeySequence("Alt+Down"), self)
        self._later_shortcut.activated.connect(lambda: self._move(1))
        QWidget.setTabOrder(self._page_list, self._move_earlier)
        QWidget.setTabOrder(self._move_earlier, self._move_later)
        self._update_move_actions()
        self._page_list.setFocus()

    @property
    def page_ids(self) -> tuple[UUID, ...]:
        """Return the explicit proposed order."""

        values: list[UUID] = []
        for row in range(self._page_list.count()):
            value = self._page_list.item(row).data(Qt.ItemDataRole.UserRole)
            if type(value) is UUID:
                values.append(value)
        return tuple(values)

    def _move(self, offset: int) -> None:
        row = self._page_list.currentRow()
        target = row + offset
        if row < 0 or not 0 <= target < self._page_list.count():
            QApplication.beep()
            return
        item = self._page_list.takeItem(row)
        self._page_list.insertItem(target, item)
        self._page_list.setCurrentRow(target)
        self._update_move_actions()
        message = f"{item.text()} moved to position {target + 1} of {self._page_list.count()}."
        self._move_status.setText(message)
        announcement = QAccessibleAnnouncementEvent(self._move_status, message)
        announcement.setPoliteness(QAccessible.AnnouncementPoliteness.Polite)
        QAccessible.updateAccessibility(announcement)

    def _update_move_actions(self, _row: int | None = None) -> None:
        row = self._page_list.currentRow()
        count = self._page_list.count()
        self._move_earlier.setEnabled(row > 0)
        self._move_later.setEnabled(0 <= row < count - 1)


class PaperWorkShellWindow(QMainWindow):
    """Accessible case chooser and page organizer presentation shell."""

    def __init__(
        self,
        commands: OrganizerBackendCommands,
        worker: OrganizerBackendWorker,
    ) -> None:
        super().__init__()
        self.setObjectName("paperWorkShellWindow")
        self.setWindowTitle("PaperWorkShell")
        self.setAccessibleName("PaperWorkShell guided packet builder")
        self._commands = commands
        self._worker = worker
        self._state: OrganizerViewState | None = None
        self._model = OrganizerPageListModel()
        self._thumbnail_generation = 0
        self._next_operation_id = 1
        self._active_operation: _LongOperation | None = None
        self._active_preview_page_ids: tuple[UUID, ...] = ()
        self._preview_batch_complete = False
        self._deferred_import: tuple[Path, str] | None = None
        self._deferred_preview_state: OrganizerViewState | None = None
        self._case_reopen_required = False
        self._controls_enabled = False
        self._closing_requested = False
        self._allow_close = False
        self._last_announced_progress: tuple[str, str, int] | None = None

        self._preview_refresh_timer = QTimer(self)
        self._preview_refresh_timer.setSingleShot(True)
        self._preview_refresh_timer.setInterval(_PREVIEW_REFRESH_DEBOUNCE_MS)
        self._preview_refresh_timer.timeout.connect(self._refresh_visible_previews)

        self._stack = QStackedWidget()
        self._stack.setObjectName("workflowStack")
        self._status_surface = self._build_status_surface()
        self._welcome_page = self._build_welcome_page()
        self._organizer_page = self._build_organizer_page()
        self._stack.addWidget(self._welcome_page)
        self._stack.addWidget(self._organizer_page)
        central = QWidget()
        central.setObjectName("applicationSurface")
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        central_layout.addWidget(self._stack, 1)
        central_layout.addWidget(self._status_surface)
        self._application_scroll = QScrollArea()
        self._application_scroll.setObjectName("applicationScrollArea")
        self._application_scroll.setAccessibleName("PaperWorkShell workflow and operation status")
        self._application_scroll.setWidgetResizable(True)
        self._application_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._application_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._application_scroll.setWidget(central)
        self.setCentralWidget(self._application_scroll)
        self._build_shortcuts()
        self._connect_backend()

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(_AUTOSAVE_DEBOUNCE_MS)
        self._autosave_timer.timeout.connect(self._request_autosave_flush)
        self._set_controls_enabled(False)
        self._stack.setCurrentWidget(self._welcome_page)

    def _build_status_surface(self) -> QGroupBox:
        status_group = QGroupBox("Operation status")
        status_group.setObjectName("operationStatusGroup")
        status_group.setAccessibleName("Operation status")
        status_group.setAccessibleDescription(
            "Current application status, progress, and any action error."
        )
        status_layout = QVBoxLayout(status_group)
        self._status_label = QLabel("PaperWorkShell is starting.")
        self._status_label.setObjectName("statusLabel")
        self._status_label.setAccessibleName("Current operation status")
        self._status_label.setAccessibleDescription(
            "Current PaperWorkShell status shown on every workflow page."
        )
        self._status_label.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._status_label.setWordWrap(True)
        self._error_label = QLabel()
        self._error_label.setObjectName("errorLabel")
        self._error_label.setAccessibleName("Action error")
        self._error_label.setAccessibleDescription(
            "The latest safe error code and recovery message."
        )
        self._error_label.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._error_label.setWordWrap(True)
        self._error_label.setVisible(False)
        self._progress = QProgressBar()
        self._progress.setObjectName("operationProgress")
        self._progress.setAccessibleName("Document operation progress")
        self._progress.setTextVisible(True)
        self._progress.setVisible(False)
        status_actions = QHBoxLayout()
        self._cancel_button = QPushButton("Cancel current operation")
        self._cancel_button.setObjectName("cancelButton")
        self._cancel_button.setAccessibleDescription(
            "Request cancellation of the running import or preview operation. Escape."
        )
        self._cancel_button.clicked.connect(self._cancel_active_operation)
        self._cancel_button.setVisible(False)
        self._retry_save_button = QPushButton("Retry saving changes")
        self._retry_save_button.setObjectName("retrySaveButton")
        self._retry_save_button.setAccessibleDescription(
            "Retry the retained organizer autosave after a recoverable failure."
        )
        self._retry_save_button.clicked.connect(self._request_retry_save)
        self._retry_save_button.setVisible(False)
        status_actions.addWidget(self._cancel_button)
        status_actions.addWidget(self._retry_save_button)
        status_actions.addStretch(1)
        status_layout.addWidget(self._status_label)
        status_layout.addWidget(self._error_label)
        status_layout.addWidget(self._progress)
        status_layout.addLayout(status_actions)
        return status_group

    def _build_welcome_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("caseChooserPage")
        layout = QVBoxLayout(page)
        heading = QLabel("Choose a case")
        heading.setObjectName("caseChooserHeading")
        heading.setAccessibleName("Choose a case")
        heading.setStyleSheet("font-size: 20pt; font-weight: 600;")
        guidance = QLabel(
            "Create a new local case or open an existing one. "
            "Cases are identified by an internal identifier; source paths are not displayed here."
        )
        guidance.setWordWrap(True)
        guidance.setAccessibleName("Case chooser guidance")
        self._case_list = QListWidget()
        self._case_list.setObjectName("caseList")
        self._case_list.setAccessibleName("Available cases")
        self._case_list.setAccessibleDescription("Select one case, then choose Open selected case.")
        self._case_list.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self._case_list.itemDoubleClicked.connect(lambda _item: self._open_selected_case())

        self._create_case_button = QPushButton("Create new case")
        self._create_case_button.setObjectName("createCaseButton")
        self._create_case_button.setAccessibleDescription(
            "Create an empty local case and open its page organizer. Shortcut Ctrl+N."
        )
        self._create_case_button.clicked.connect(self._request_create_case)
        self._open_case_button = QPushButton("Open selected case")
        self._open_case_button.setObjectName("openCaseButton")
        self._open_case_button.setAccessibleDescription("Open the selected case. Shortcut Ctrl+O.")
        self._open_case_button.clicked.connect(self._open_selected_case)
        self._refresh_cases_button = QPushButton("Refresh case list")
        self._refresh_cases_button.setObjectName("refreshCasesButton")
        self._refresh_cases_button.clicked.connect(self._commands.list_cases_requested.emit)
        chooser_actions = QHBoxLayout()
        chooser_actions.addWidget(self._create_case_button)
        chooser_actions.addWidget(self._open_case_button)
        chooser_actions.addWidget(self._refresh_cases_button)
        chooser_actions.addStretch(1)

        layout.addWidget(heading)
        layout.addWidget(guidance)
        layout.addWidget(self._case_list, 1)
        layout.addLayout(chooser_actions)
        QWidget.setTabOrder(self._case_list, self._create_case_button)
        QWidget.setTabOrder(self._create_case_button, self._open_case_button)
        QWidget.setTabOrder(self._open_case_button, self._refresh_cases_button)
        return page

    def _build_organizer_page(self) -> QWidget:
        content = QWidget()
        content.setObjectName("organizerPage")
        layout = QVBoxLayout(content)

        header = QHBoxLayout()
        self._case_heading = QLabel("Page organizer")
        self._case_heading.setObjectName("organizerHeading")
        self._case_heading.setAccessibleName("Page organizer")
        self._case_heading.setStyleSheet("font-size: 20pt; font-weight: 600;")
        self._import_button = QPushButton("Import document")
        self._import_button.setObjectName("importButton")
        self._import_button.setAccessibleDescription(
            "Choose a PDF, JPEG, or PNG to copy into this case. Shortcut Ctrl+I."
        )
        self._import_button.clicked.connect(self._choose_import)
        self._close_case_button = QPushButton("Close case")
        self._close_case_button.setObjectName("closeCaseButton")
        self._close_case_button.setAccessibleDescription(
            "Save queued organizer changes and return to the case chooser."
        )
        self._close_case_button.clicked.connect(self._request_close_case)
        header.addWidget(self._case_heading)
        header.addStretch(1)
        header.addWidget(self._import_button)
        header.addWidget(self._close_case_button)
        layout.addLayout(header)

        organizer_group = QGroupBox("Active pages")
        organizer_group.setObjectName("activePagesGroup")
        organizer_group.setAccessibleDescription(
            "Keyboard-selectable active pages. Reordering never requires drag-and-drop."
        )
        organizer_layout = QVBoxLayout(organizer_group)
        self._page_view = QListView()
        self._page_view.setObjectName("pageList")
        self._page_view.setAccessibleName("Active pages")
        self._page_view.setAccessibleDescription(
            "Select a page, then use the visible organizer actions or their shortcuts."
        )
        self._page_view.setModel(self._model)
        self._page_view.setViewMode(QListView.ViewMode.IconMode)
        self._page_view.setResizeMode(QListView.ResizeMode.Adjust)
        self._page_view.setMovement(QListView.Movement.Static)
        self._page_view.setDragEnabled(False)
        self._page_view.setAcceptDrops(False)
        self._page_view.setDropIndicatorShown(False)
        self._page_view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._page_view.setWordWrap(True)
        self._page_view.setSpacing(12)
        self._page_view.setIconSize(
            QImage(_THUMBNAIL_WIDTH, _THUMBNAIL_HEIGHT, QImage.Format.Format_RGBA8888).size()
        )
        self._page_view.selectionModel().selectionChanged.connect(self._selection_changed)
        self._page_view.verticalScrollBar().valueChanged.connect(self._schedule_preview_refresh)
        self._page_view.horizontalScrollBar().valueChanged.connect(self._schedule_preview_refresh)
        self._page_view.viewport().installEventFilter(self)
        organizer_layout.addWidget(self._page_view, 1)

        action_layout = QGridLayout()
        self._move_earlier_button = self._action_button(
            "Move earlier",
            "moveEarlierButton",
            "Move the selected page one position earlier. Alt+Up.",
        )
        self._move_later_button = self._action_button(
            "Move later", "moveLaterButton", "Move the selected page one position later. Alt+Down."
        )
        self._rotate_button = self._action_button(
            "Rotate clockwise",
            "rotateButton",
            "Rotate the selected page clockwise by 90 degrees. Ctrl+R.",
        )
        self._remove_button = self._action_button(
            "Remove from packet",
            "removeButton",
            "Remove the selected page from the active packet. Delete.",
        )
        self._change_order_button = self._action_button(
            "Change full order",
            "changeOrderButton",
            "Open the explicit keyboard page-order dialog. Ctrl+Shift+O.",
        )
        self._undo_button = self._action_button(
            "Undo", "undoButton", "Undo the last organizer action from this session. Ctrl+Z."
        )
        self._redo_button = self._action_button(
            "Redo", "redoButton", "Redo the next undone organizer action. Ctrl+Y."
        )
        self._move_earlier_button.clicked.connect(lambda: self._move_selected(-1))
        self._move_later_button.clicked.connect(lambda: self._move_selected(1))
        self._rotate_button.clicked.connect(self._rotate_selected)
        self._remove_button.clicked.connect(self._remove_selected)
        self._change_order_button.clicked.connect(self._change_full_order)
        self._undo_button.clicked.connect(self._request_undo)
        self._redo_button.clicked.connect(self._request_redo)
        action_layout.addWidget(self._move_earlier_button, 0, 0)
        action_layout.addWidget(self._move_later_button, 0, 1)
        action_layout.addWidget(self._rotate_button, 0, 2)
        action_layout.addWidget(self._remove_button, 1, 0)
        action_layout.addWidget(self._change_order_button, 1, 1)
        action_layout.addWidget(self._undo_button, 1, 2)
        action_layout.addWidget(self._redo_button, 1, 3)
        organizer_layout.addLayout(action_layout)
        layout.addWidget(organizer_group, 1)

        tab_chain = (
            self._import_button,
            self._close_case_button,
            self._page_view,
            self._move_earlier_button,
            self._move_later_button,
            self._rotate_button,
            self._remove_button,
            self._change_order_button,
            self._undo_button,
            self._redo_button,
            self._cancel_button,
            self._retry_save_button,
        )
        for current, following in pairwise(tab_chain):
            QWidget.setTabOrder(current, following)
        return content

    def _action_button(self, text: str, object_name: str, description: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(object_name)
        button.setAccessibleName(text)
        button.setAccessibleDescription(description)
        button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        return button

    def _build_shortcuts(self) -> None:
        shortcuts = (
            ("Ctrl+N", self._request_create_case),
            ("Ctrl+O", self._open_selected_case),
            ("Ctrl+I", self._choose_import),
            ("Ctrl+Z", self._request_undo),
            ("Ctrl+Y", self._request_redo),
            ("Alt+Up", lambda: self._move_selected(-1)),
            ("Alt+Down", lambda: self._move_selected(1)),
            ("Ctrl+R", self._rotate_selected),
            ("Delete", self._remove_selected),
            ("Ctrl+Shift+O", self._change_full_order),
            ("Escape", self._cancel_active_operation),
        )
        self._shortcuts: list[QShortcut] = []
        for key, callback in shortcuts:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)

    def _connect_backend(self) -> None:
        self._worker.initialized.connect(self._backend_initialized)
        self._worker.cases_changed.connect(self._cases_changed)
        self._worker.state_changed.connect(self._state_changed)
        self._worker.thumbnails_ready.connect(self._thumbnails_ready)
        self._worker.progress_changed.connect(self._progress_changed)
        self._worker.status_changed.connect(self._status_changed)
        self._worker.operation_status_changed.connect(self._operation_status_changed)
        self._worker.case_reopen_required.connect(self._reopen_required)
        self._worker.review_required.connect(self._review_required)
        self._worker.operation_started.connect(self._operation_started)
        self._worker.operation_cancelled.connect(self._operation_cancelled)
        self._worker.operation_finished.connect(self._operation_finished)
        self._worker.failed.connect(self._failed)
        self._worker.shutdown_complete.connect(self._shutdown_complete)
        self._worker.shutdown_refused.connect(self._shutdown_refused)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        """Refresh the on-demand preview window after viewport layout changes."""

        if watched is self._page_view.viewport() and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
        }:
            self._schedule_preview_refresh()
        return super().eventFilter(watched, event)

    @Slot()
    def _backend_initialized(self) -> None:
        self._set_controls_enabled(True)
        self._case_list.setFocus()

    @Slot(object)
    def _cases_changed(self, case_index: object) -> None:
        if type(case_index) is not CaseIndex:
            return
        selected_id = self._selected_case_id()
        self._case_list.clear()
        for summary in case_index.cases:
            item = QListWidgetItem(
                f"Case {summary.case_id}\nRevision {summary.revision}; updated {summary.updated_at.isoformat()}"
            )
            item.setData(Qt.ItemDataRole.UserRole, summary.case_id)
            item.setToolTip("Internal case identifier and last saved revision.")
            self._case_list.addItem(item)
            if summary.case_id == selected_id:
                self._case_list.setCurrentItem(item)
        if self._case_list.currentRow() < 0 and self._case_list.count():
            self._case_list.setCurrentRow(0)
        if case_index.unavailable:
            self._announce(
                f"{len(case_index.unavailable)} case entries need recovery review and are unavailable.",
                assertive=True,
            )

    @Slot(object)
    def _state_changed(self, state: object) -> None:
        if type(state) is not OrganizerViewState:
            return
        prior_state = self._state
        focused_widget = QApplication.focusWidget()
        preserve_focus = (
            prior_state is not None
            and prior_state.case_id is not None
            and prior_state.case_id == state.case_id
            and focused_widget is not None
            and (focused_widget is self or self.isAncestorOf(focused_widget))
        )
        selected_page = self._selected_page_id()
        if state.case_id is None or prior_state is None or prior_state.case_id != state.case_id:
            self._case_reopen_required = False
        self._state = state
        self._model.set_organizer_state(state)
        self._update_import_availability()
        if state.case_id is None:
            self._preview_refresh_timer.stop()
            self._autosave_timer.stop()
            self._deferred_import = None
            self._deferred_preview_state = None
            self._cancel_active_operation_silently()
            self._hide_retry_save()
            self._stack.setCurrentWidget(self._welcome_page)
            self._case_list.setFocus(Qt.FocusReason.OtherFocusReason)
            self._ensure_widget_visible(self._case_list)
            return
        self._stack.setCurrentWidget(self._organizer_page)
        heading = f"Page organizer — revision {state.revision}"
        if state.recovery_review_required:
            heading = f"{heading} — recovered; review needed"
        self._case_heading.setText(heading)
        if state.autosave_pending:
            self._deferred_preview_state = None
            self._cancel_active_preview()
            self._autosave_timer.start()
        else:
            self._autosave_timer.stop()
            self._hide_retry_save()
        self._restore_page_selection(selected_page)
        self._update_page_actions()
        if not state.autosave_pending:
            self._schedule_preview_refresh()
        if preserve_focus:
            if focused_widget is not None and self._focus_widget_is_available(focused_widget):
                focused_widget.setFocus(Qt.FocusReason.OtherFocusReason)
            else:
                self._focus_safe_control()
        elif prior_state is None or prior_state.case_id != state.case_id:
            self._focus_safe_control()

    @Slot(int, int, object)
    def _thumbnails_ready(self, operation_id: int, generation: int, results: object) -> None:
        active = self._active_operation
        if (
            active is None
            or active.operation_id != operation_id
            or active.kind is not _LongOperationKind.PREVIEW
            or generation != self._thumbnail_generation
            or type(results) is not tuple
        ):
            return
        expected = self._active_preview_page_ids
        received = tuple(
            result.snapshot.page_id for result in results if type(result) is PdfThumbnailResult
        )
        if len(received) != len(results) or received != expected:
            for page_id in expected:
                self._model.set_preview_failed(page_id)
            self._preview_batch_complete = False
            return
        images_valid = True
        for result in results:
            assert type(result) is PdfThumbnailResult
            snapshot = result.snapshot
            image = QImage(
                snapshot.pixels,
                snapshot.width_pixels,
                snapshot.height_pixels,
                snapshot.row_stride_bytes,
                QImage.Format.Format_RGBA8888,
            ).copy()
            if image.isNull():
                self._model.set_preview_failed(snapshot.page_id)
                images_valid = False
                continue
            self._model.set_preview(snapshot.page_id, QPixmap.fromImage(image))
        self._preview_batch_complete = images_valid
        retained = self._current_preview_page_ids()
        self._model.touch_previews(retained)
        self._model.evict_previews(
            _PREVIEW_PIXMAP_BUDGET,
            protected_page_ids=retained,
        )

    @Slot(object)
    def _progress_changed(self, progress: object) -> None:
        if type(progress) is not UiProgress:
            return
        active = self._active_operation
        if active is None or active.operation_id != progress.operation_id:
            return
        self._progress.setVisible(True)
        self._progress.setRange(0, progress.total)
        self._progress.setValue(progress.completed)
        self._progress.setFormat(f"{progress.operation}: {progress.phase} — %v of %m")
        self._progress.setAccessibleDescription(
            f"{progress.operation} {progress.phase}, {progress.completed} of {progress.total}."
        )
        cancelable = progress.cancelable and not active.cancellation.cancelled()
        self._cancel_button.setVisible(progress.cancelable)
        self._cancel_button.setEnabled(cancelable)
        prior = self._last_announced_progress
        phase_changed = prior is None or prior[:2] != (progress.operation, progress.phase)
        announcement_step = max(1, progress.total // 4)
        completed_enough = prior is None or progress.completed - prior[2] >= announcement_step
        if phase_changed or completed_enough or progress.completed == progress.total:
            self._last_announced_progress = (
                progress.operation,
                progress.phase,
                progress.completed,
            )
            self._announce(
                f"{progress.operation} {progress.phase}: {progress.completed} of {progress.total}."
            )

    @Slot(str)
    def _status_changed(self, message: str) -> None:
        self._hide_error()
        self._announce(message)

    @Slot(int, str)
    def _operation_status_changed(self, operation_id: int, message: str) -> None:
        active = self._active_operation
        if active is None or active.operation_id != operation_id or active.cancellation.cancelled():
            return
        if active.kind is _LongOperationKind.IMPORT:
            self._hide_error()
        self._announce(message)

    @Slot(int, str)
    def _reopen_required(self, operation_id: int, message: str) -> None:
        active = self._active_operation
        if (
            active is None
            or active.operation_id != operation_id
            or active.kind is not _LongOperationKind.IMPORT
        ):
            return
        self._case_reopen_required = True
        self._preview_refresh_timer.stop()
        self._autosave_timer.stop()
        self._deferred_import = None
        self._deferred_preview_state = None
        self._hide_error()
        self._hide_retry_save()
        self._update_import_availability()
        self._update_page_actions()
        self._announce(message, assertive=True)
        self._close_case_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._ensure_widget_visible(self._close_case_button)

    @Slot(str)
    def _review_required(self, message: str) -> None:
        self._announce(message, assertive=True)

    @Slot(int, str)
    def _operation_started(self, operation_id: int, operation_name: str) -> None:
        active = self._active_operation
        if active is None or active.operation_id != operation_id:
            return
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        self._progress.setFormat(f"{operation_name} starting")

    @Slot(int, str)
    def _operation_cancelled(self, operation_id: int, message: str) -> None:
        active = self._active_operation
        if active is None or active.operation_id != operation_id:
            return
        self._announce(message, assertive=True)

    @Slot(int)
    def _operation_finished(self, operation_id: int) -> None:
        active = self._active_operation
        if active is None or active.operation_id != operation_id:
            return
        finished_preview = active.kind is _LongOperationKind.PREVIEW
        preview_batch_complete = self._preview_batch_complete
        if finished_preview:
            self._model.finish_preview_loading(self._active_preview_page_ids)
        self._finish_active_operation()
        if self._closing_requested:
            return
        deferred_import = self._deferred_import
        if deferred_import is not None:
            self._deferred_import = None
            self._start_import(*deferred_import)
            return
        deferred_preview = self._deferred_preview_state
        self._deferred_preview_state = None
        if deferred_preview is not None and not deferred_preview.autosave_pending:
            self._schedule_preview_refresh()
        elif finished_preview and preview_batch_complete:
            self._schedule_preview_refresh()

    @Slot(object)
    def _failed(self, failure: object) -> None:
        if type(failure) is not UiFailure:
            return
        active = self._active_operation
        if failure.operation_id is not None and (
            active is None or active.operation_id != failure.operation_id
        ):
            return
        if active is not None and active.kind is _LongOperationKind.PREVIEW:
            for page_id in self._active_preview_page_ids:
                self._model.set_preview_failed(page_id)
            self._preview_batch_complete = False
        self._error_label.setText(f"{failure.code}: {failure.message}")
        self._error_label.setVisible(True)
        pending_save = self._state is not None and self._state.autosave_pending
        if failure.retryable and pending_save:
            self._retry_save_button.setVisible(True)
        else:
            self._hide_retry_save()
        self._announce(f"Action failed. {failure.message}", assertive=True)
        if self._retry_save_button.isVisible():
            self._retry_save_button.setFocus()
            self._ensure_widget_visible(self._retry_save_button)
        else:
            self._focus_safe_control()

    @Slot()
    def _shutdown_complete(self) -> None:
        self._allow_close = True
        self.close()

    @Slot()
    def _shutdown_refused(self) -> None:
        self._closing_requested = False
        self._set_controls_enabled(True)
        self._announce(
            "The application remains open because queued changes could not be saved. Retry saving changes.",
            assertive=True,
        )
        self._retry_save_button.setVisible(True)
        self._retry_save_button.setFocus()
        self._ensure_widget_visible(self._retry_save_button)

    @Slot()
    def shutdown_wait_timed_out(self) -> None:
        """Keep the closing surface alive while a bounded backend call winds down."""

        self._announce(
            "PaperWorkShell is still finishing the current operation before it can close.",
            assertive=True,
        )

    def _request_previews(self, state: OrganizerViewState) -> None:
        if self._case_reopen_required:
            return
        unsupported = tuple(page.page_id for page in state.pages if not page.preview_supported)
        for page_id in unsupported:
            self._model.set_preview_unavailable(page_id)
        retained = self._current_preview_page_ids()
        self._model.touch_previews(retained)
        self._model.evict_previews(
            _PREVIEW_PIXMAP_BUDGET,
            protected_page_ids=retained,
        )
        missing = tuple(
            page_id
            for page_id in retained
            if self._model.preview_state(page_id) is PreviewState.NOT_LOADED
        )
        selected = self._selected_page_id()
        prioritized = (
            ((selected,) if selected in missing else ())
            + tuple(page_id for page_id in missing if page_id != selected)
        )[:_PREVIEW_REQUEST_CAP]
        requested_set = frozenset(prioritized)
        requested = tuple(page_id for page_id in retained if page_id in requested_set)
        if not requested:
            self._deferred_preview_state = None
            return
        if state.autosave_pending or self._closing_requested:
            return
        if self._active_operation is not None:
            self._deferred_preview_state = state
            return
        self._start_preview(requested)

    def _start_preview(self, page_ids: tuple[UUID, ...]) -> None:
        operation = self._begin_long_operation(_LongOperationKind.PREVIEW)
        self._thumbnail_generation += 1
        generation = self._thumbnail_generation
        self._active_preview_page_ids = page_ids
        self._preview_batch_complete = False
        self._model.set_preview_loading(page_ids)
        self._commands.thumbnails_requested.emit(
            operation.operation_id,
            generation,
            page_ids,
            _THUMBNAIL_WIDTH,
            _THUMBNAIL_HEIGHT,
            operation.cancellation,
        )

    def _schedule_preview_refresh(self, _value: int | None = None) -> None:
        state = self._state
        if (
            state is None
            or state.case_id is None
            or state.autosave_pending
            or self._closing_requested
            or self._case_reopen_required
        ):
            return
        self._preview_refresh_timer.start()

    @Slot()
    def _refresh_visible_previews(self) -> None:
        state = self._state
        if (
            state is None
            or state.case_id is None
            or state.autosave_pending
            or self._closing_requested
            or self._case_reopen_required
        ):
            return
        self._request_previews(state)

    def _current_preview_page_ids(self) -> tuple[UUID, ...]:
        state = self._state
        if state is None or state.case_id is None or not state.pages:
            return ()
        viewport = self._page_view.viewport().rect()
        visible_rows = tuple(
            row
            for row in range(self._model.rowCount())
            if self._page_view.visualRect(self._model.index(row, 0)).intersects(viewport)
        )
        rows = _bounded_preview_rows(
            self._model.rowCount(),
            visible_rows,
            self._page_view.currentIndex().row(),
        )
        return tuple(state.pages[row].page_id for row in rows if state.pages[row].preview_supported)

    def _choose_import(self) -> None:
        if (
            not self._controls_enabled
            or self._closing_requested
            or not self._import_button.isEnabled()
            or self._state is None
            or self._state.case_id is None
        ):
            return
        if self._import_is_pending_or_running():
            QApplication.beep()
            self._announce(
                "An import is already queued or running. Cancel it or wait for it to finish.",
                assertive=True,
            )
            return
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "Import document",
            "",
            "Supported documents (*.pdf *.png *.jpg *.jpeg);;All files (*)",
        )
        if not selected:
            self._announce("Import selection cancelled.")
            return
        source_path = Path(selected)
        self._queue_import(source_path, source_path.name)

    def _queue_import(self, source_path: Path, display_name: str) -> None:
        if self._case_reopen_required:
            return
        state = self._state
        self._deferred_preview_state = (
            state if state is not None and not state.autosave_pending else None
        )
        active = self._active_operation
        if active is not None:
            if active.kind is not _LongOperationKind.PREVIEW:
                QApplication.beep()
                self._announce(
                    "An import is already queued or running. Cancel it or wait for it to finish.",
                    assertive=True,
                )
                return
            self._deferred_import = (source_path, display_name)
            self._update_import_availability()
            self._cancel_active_preview()
            self._announce("Stopping the current preview before import starts.")
            return
        self._start_import(source_path, display_name)

    def _start_import(self, source_path: Path, display_name: str) -> None:
        operation = self._begin_long_operation(_LongOperationKind.IMPORT)
        self._announce("Import started. You can cancel this operation.")
        self._commands.import_requested.emit(
            operation.operation_id,
            source_path,
            display_name,
            operation.cancellation,
        )

    def _open_selected_case(self) -> None:
        if (
            not self._controls_enabled
            or self._closing_requested
            or (self._state is not None and self._state.case_id is not None)
        ):
            return
        case_id = self._selected_case_id()
        if case_id is None:
            QApplication.beep()
            self._announce("Select a case before choosing Open selected case.", assertive=True)
            self._case_list.setFocus()
            return
        self._commands.open_case_requested.emit(case_id)

    def _request_create_case(self) -> None:
        if (
            not self._controls_enabled
            or self._closing_requested
            or (self._state is not None and self._state.case_id is not None)
        ):
            return
        self._commands.create_case_requested.emit()

    def _request_close_case(self) -> None:
        if (
            not self._controls_enabled
            or self._closing_requested
            or self._state is None
            or self._state.case_id is None
        ):
            return
        self._autosave_timer.stop()
        self._deferred_import = None
        self._deferred_preview_state = None
        self._cancel_active_operation_silently()
        self._commands.close_case_requested.emit()

    def _request_autosave_flush(self) -> None:
        state = self._state
        if (
            self._case_reopen_required
            or state is None
            or state.case_id is None
            or not state.autosave_pending
        ):
            return
        self._deferred_preview_state = None
        self._cancel_active_preview()
        self._commands.flush_requested.emit()

    def _request_retry_save(self) -> None:
        state = self._state
        if (
            self._case_reopen_required
            or state is None
            or state.case_id is None
            or not state.autosave_pending
        ):
            return
        self._deferred_preview_state = None
        self._cancel_active_preview()
        self._commands.retry_save_requested.emit()

    def _selected_case_id(self) -> UUID | None:
        row = self._case_list.currentRow()
        if row < 0:
            return None
        item = self._case_list.item(row)
        value = item.data(Qt.ItemDataRole.UserRole)
        return value if type(value) is UUID else None

    def _selected_page_id(self) -> UUID | None:
        return self._model.page_id_at(self._page_view.currentIndex().row())

    def _restore_page_selection(self, page_id: UUID | None) -> None:
        row = None if page_id is None else self._model.row_for_page(page_id)
        if row is None and self._model.rowCount() > 0:
            row = 0
        if row is None:
            return
        index = self._model.index(row, 0)
        selection = QItemSelection(index, index)
        self._page_view.selectionModel().select(
            selection,
            QItemSelectionModel.SelectionFlag.ClearAndSelect,
        )
        self._page_view.setCurrentIndex(index)

    def _selection_changed(
        self,
        _selected: QItemSelection,
        _deselected: QItemSelection,
    ) -> None:
        self._update_page_actions()
        self._schedule_preview_refresh()

    def _update_page_actions(self) -> None:
        row = self._page_view.currentIndex().row()
        page_count = self._model.rowCount()
        editable = (
            self._controls_enabled
            and not self._case_reopen_required
            and not self._import_is_pending_or_running()
        )
        has_selection = editable and 0 <= row < page_count
        self._move_earlier_button.setEnabled(has_selection and row > 0)
        self._move_later_button.setEnabled(has_selection and row < page_count - 1)
        self._rotate_button.setEnabled(has_selection)
        self._remove_button.setEnabled(has_selection)
        self._change_order_button.setEnabled(editable and page_count > 1)
        state = self._state
        self._undo_button.setEnabled(editable and state is not None and state.can_undo)
        self._redo_button.setEnabled(editable and state is not None and state.can_redo)

    def _move_selected(self, offset: int) -> None:
        action = self._move_earlier_button if offset < 0 else self._move_later_button
        if offset not in {-1, 1} or not action.isEnabled():
            return
        page_id = self._selected_page_id()
        if page_id is None or not self._prepare_for_mutation():
            return
        self._commands.move_requested.emit(page_id, offset)

    def _rotate_selected(self) -> None:
        if not self._rotate_button.isEnabled():
            return
        page_id = self._selected_page_id()
        if page_id is not None and self._prepare_for_mutation():
            self._commands.rotate_requested.emit(page_id)

    def _remove_selected(self) -> None:
        if not self._remove_button.isEnabled():
            return
        page_id = self._selected_page_id()
        if page_id is not None and self._prepare_for_mutation():
            self._commands.remove_requested.emit(page_id)

    def _change_full_order(self) -> None:
        if not self._change_order_button.isEnabled() or len(self._model.pages) < 2:
            return
        dialog = PageOrderDialog(
            tuple((page.page_id, page.position) for page in self._model.pages),
            self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if self._prepare_for_mutation():
                self._commands.reorder_requested.emit(dialog.page_ids)
        else:
            self._page_view.setFocus()

    def _request_undo(self) -> None:
        if self._undo_button.isEnabled() and self._prepare_for_mutation():
            self._commands.undo_requested.emit()

    def _request_redo(self) -> None:
        if self._redo_button.isEnabled() and self._prepare_for_mutation():
            self._commands.redo_requested.emit()

    def _prepare_for_mutation(self) -> bool:
        if (
            not self._controls_enabled
            or self._closing_requested
            or self._case_reopen_required
            or self._state is None
            or self._state.case_id is None
        ):
            return False
        if self._import_is_pending_or_running():
            QApplication.beep()
            self._announce(
                "Wait for the current import to finish, or cancel it, before changing pages.",
                assertive=True,
            )
            return False
        self._deferred_preview_state = None
        self._cancel_active_preview()
        return True

    def _cancel_active_operation(self) -> None:
        active = self._active_operation
        if (
            active is None
            or not self._cancel_button.isVisible()
            or not self._cancel_button.isEnabled()
        ):
            return
        active.cancellation.cancel()
        self._cancel_button.setEnabled(False)
        self._announce("Cancellation requested. Waiting for the operation to stop.", assertive=True)

    def _begin_long_operation(self, kind: _LongOperationKind) -> _LongOperation:
        if self._active_operation is not None:
            raise RuntimeError("long operation overlap")
        operation_id = self._next_operation_id
        self._next_operation_id = 1 if operation_id >= _MAX_OPERATION_ID else operation_id + 1
        operation = _LongOperation(operation_id, kind, OperationCancellation())
        self._active_operation = operation
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        self._progress.setFormat(f"{kind.value} queued")
        self._cancel_button.setVisible(True)
        self._cancel_button.setEnabled(True)
        self._update_import_availability()
        self._update_page_actions()
        return operation

    def _cancel_active_preview(self) -> None:
        active = self._active_operation
        if active is None or active.kind is not _LongOperationKind.PREVIEW:
            return
        if not active.cancellation.cancelled():
            active.cancellation.cancel()
            self._thumbnail_generation += 1
        self._model.finish_preview_loading(self._active_preview_page_ids)
        self._preview_batch_complete = False
        self._cancel_button.setEnabled(False)

    def _cancel_active_operation_silently(self) -> None:
        active = self._active_operation
        if active is None:
            return
        active.cancellation.cancel()
        if active.kind is _LongOperationKind.PREVIEW:
            self._thumbnail_generation += 1
            self._model.finish_preview_loading(self._active_preview_page_ids)
            self._preview_batch_complete = False
        self._cancel_button.setEnabled(False)

    def _finish_active_operation(self) -> None:
        if self._cancel_button.hasFocus():
            self._focus_safe_control()
        self._progress.setVisible(False)
        self._cancel_button.setVisible(False)
        self._active_operation = None
        self._active_preview_page_ids = ()
        self._preview_batch_complete = False
        self._last_announced_progress = None
        self._update_import_availability()
        self._update_page_actions()

    def _import_is_pending_or_running(self) -> bool:
        active = self._active_operation
        return self._deferred_import is not None or (
            active is not None and active.kind is _LongOperationKind.IMPORT
        )

    def _announce(self, message: str, *, assertive: bool = False) -> None:
        self._status_label.setText(message)
        announcement = QAccessibleAnnouncementEvent(self._status_label, message)
        announcement.setPoliteness(
            QAccessible.AnnouncementPoliteness.Assertive
            if assertive
            else QAccessible.AnnouncementPoliteness.Polite
        )
        QAccessible.updateAccessibility(announcement)

    def _focus_widget_is_available(self, widget: QWidget | None) -> bool:
        return (
            widget is not None
            and widget.isVisible()
            and widget.isEnabled()
            and widget.focusPolicy() is not Qt.FocusPolicy.NoFocus
        )

    def _focus_safe_control(self) -> None:
        state = self._state
        candidates: tuple[QWidget, ...]
        if state is not None and state.case_id is not None:
            if self._model.rowCount() > 0:
                candidates = (
                    self._page_view,
                    self._import_button,
                    self._close_case_button,
                )
            else:
                candidates = (
                    self._import_button,
                    self._close_case_button,
                    self._page_view,
                )
        else:
            candidates = (
                self._case_list,
                self._create_case_button,
                self._open_case_button,
            )
        for candidate in (*candidates, self._error_label, self._status_label):
            if self._focus_widget_is_available(candidate):
                candidate.setFocus(Qt.FocusReason.OtherFocusReason)
                self._ensure_widget_visible(candidate)
                return

    def _ensure_widget_visible(self, widget: QWidget) -> None:
        self._application_scroll.ensureWidgetVisible(widget, 8, 8)

    def _hide_error(self) -> None:
        had_focus = self._error_label.hasFocus()
        self._error_label.setVisible(False)
        if had_focus:
            self._focus_safe_control()

    def _hide_retry_save(self) -> None:
        if self._retry_save_button.hasFocus():
            self._focus_safe_control()
        self._retry_save_button.setVisible(False)

    def _update_import_availability(self) -> None:
        state = self._state
        self._import_button.setEnabled(
            self._controls_enabled
            and state is not None
            and state.case_id is not None
            and not self._case_reopen_required
            and not self._import_is_pending_or_running()
        )

    def _set_controls_enabled(self, enabled: bool) -> None:
        self._controls_enabled = enabled
        for widget in (
            self._case_list,
            self._create_case_button,
            self._open_case_button,
            self._refresh_cases_button,
            self._close_case_button,
            self._page_view,
            self._move_earlier_button,
            self._move_later_button,
            self._rotate_button,
            self._remove_button,
            self._change_order_button,
            self._undo_button,
            self._redo_button,
        ):
            widget.setEnabled(enabled)
        self._update_import_availability()
        self._update_page_actions()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        """Keep the window open until autosave and worker cleanup complete."""

        if self._allow_close:
            event.accept()
            return
        event.ignore()
        self.request_shutdown()

    def request_shutdown(self) -> None:
        """Cancel active work and queue a clean backend shutdown exactly once."""

        if self._closing_requested:
            return
        self._closing_requested = True
        self._autosave_timer.stop()
        self._deferred_import = None
        self._deferred_preview_state = None
        if self._cancel_button.hasFocus():
            self._focus_safe_control()
        self._cancel_active_operation_silently()
        self._set_controls_enabled(False)
        self._announce("Closing PaperWorkShell after saving queued changes.")
        self._commands.shutdown_requested.emit()

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
    QModelIndex,
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

from paperworkshell.app.ocr_languages import OcrLanguageSnapshot
from paperworkshell.app.organizer import (
    MAX_ORGANIZER_PREVIEW_BATCH_PAGES,
    OrganizerViewState,
)
from paperworkshell.app.quality_analysis import QualityReviewSnapshot
from paperworkshell.app.scan_review import (
    ScanReviewDraft,
    ScanReviewPreviewResult,
    ScanReviewPreviewVariant,
)
from paperworkshell.domain.case_lifecycle import CaseIndex
from paperworkshell.domain.image_pages import (
    ImagePagePreviewResult,
    ImagePagePreviewSnapshot,
)
from paperworkshell.domain.image_transforms import (
    ImagePageTransformResult,
    ImagePageTransformSnapshot,
)
from paperworkshell.domain.pdf_thumbnails import PdfThumbnailResult, PdfThumbnailSnapshot
from paperworkshell.domain.recovery import (
    CaseRecoveryStatus,
    StartupRecoveryResult,
    StartupRecoveryStatus,
)
from paperworkshell.domain.resource_policy import (
    NORMAL_RESOURCE_POLICY_V1,
    ResourcePolicy,
    select_resource_policy,
)
from paperworkshell.ui.backend import (
    OperationCancellation,
    OrganizerBackendCommands,
    OrganizerBackendWorker,
    ShutdownRefusalKind,
    UiFailure,
    UiProgress,
)
from paperworkshell.ui.models import (
    OrganizerPageListModel,
    PreviewState,
    QualityFindingListModel,
)
from paperworkshell.ui.ocr_languages import OcrLanguageDialog
from paperworkshell.ui.scan_review import ScanReviewDialog

_AUTOSAVE_DEBOUNCE_MS: Final = 400
_THUMBNAIL_WIDTH: Final = 240
_THUMBNAIL_HEIGHT: Final = 320
_PREVIEW_REFRESH_DEBOUNCE_MS: Final = 75
_SCAN_REVIEW_PREVIEW_WIDTH: Final = 600
_SCAN_REVIEW_PREVIEW_HEIGHT: Final = 800
_MAX_OPERATION_ID: Final = 2_147_483_647
_FOCUS_VISIBLE_TEXT_STYLE: Final = """
QLabel {
    border: 2px solid transparent;
    padding: 2px;
}
QLabel:focus {
    border-color: palette(highlight);
}
"""


class _LongOperationKind(StrEnum):
    IMPORT = "Import"
    PREVIEW = "Preview"
    QUALITY = "Page quality check"


class _ImportPresentationState(StrEnum):
    IDLE = "idle"
    QUEUED = "queued"
    ACTIVE = "active"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REOPEN_REQUIRED = "reopen_required"


class _QualityPresentationState(StrEnum):
    NOT_CHECKED = "not_checked"
    QUEUED = "queued"
    ACTIVE = "active"
    READY = "ready"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class _LongOperation:
    operation_id: int
    kind: _LongOperationKind
    cancellation: OperationCancellation


@dataclass(frozen=True, slots=True)
class _ScanReviewPreviewOperation:
    """One exact provisional preview request owned by the GUI thread."""

    operation_id: int
    generation: int
    draft: ScanReviewDraft
    variant: ScanReviewPreviewVariant
    cancellation: OperationCancellation


def _bounded_preview_rows(
    row_count: int,
    visible_rows: tuple[int, ...],
    selected_row: int,
    *,
    overscan_rows: int = NORMAL_RESOURCE_POLICY_V1.preview_prefetch_radius_pages,
    maximum_count: int = NORMAL_RESOURCE_POLICY_V1.maximum_preview_request_pages,
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


def _startup_recovery_notice(
    result: StartupRecoveryResult,
    *,
    additional_needs_review: int = 0,
) -> tuple[str, bool]:
    """Render bounded recovery counts without exposing case paths or content."""

    checked = result.scanned_cases + additional_needs_review
    recovered = result.recovered_cases
    needs_review = (
        sum(
            case.status is CaseRecoveryStatus.UNAVAILABLE or bool(case.issues)
            for case in result.cases
        )
        + additional_needs_review
    )
    removed_files = sum(
        case.removed_source_orphans + case.removed_thumbnail_stages + case.removed_temp_files
        for case in result.cases
    )
    removed_directories = sum(case.removed_temp_directories for case in result.cases)
    checked_noun = "case" if checked == 1 else "cases"
    review_verb = "needs" if needs_review == 1 else "need"
    file_noun = "file" if removed_files == 1 else "files"
    directory_noun = "directory" if removed_directories == 1 else "directories"
    if result.status is StartupRecoveryStatus.ATTENTION_REQUIRED or additional_needs_review:
        return (
            "Startup recovery needs attention. "
            f"Checked {checked} local {checked_noun}; {needs_review} {review_verb} review. "
            "Uncertain leftover work data was preserved.",
            True,
        )
    if recovered or removed_files or removed_directories:
        return (
            "Startup recovery complete. "
            f"Checked {checked} local {checked_noun}; recovered {recovered}, removed "
            f"{removed_files} leftover work {file_noun} and "
            f"{removed_directories} {directory_noun}. "
            "Ready. Create a case or open an existing case.",
            False,
        )
    return (
        "Startup recovery complete. "
        f"Checked {checked} local {checked_noun}; no recovery action was needed. "
        "Ready. Create a case or open an existing case.",
        False,
    )


def _quality_state_invalidates_review(
    prior: OrganizerViewState | None,
    current: OrganizerViewState,
) -> bool:
    """Invalidate ephemeral findings whenever their exact saved page facts can differ."""

    return (
        prior is None
        or prior.autosave_pending
        or current.autosave_pending
        or prior.case_id != current.case_id
        or prior.revision != current.revision
        or prior.pages != current.pages
    )


def _next_generation(current: int) -> int:
    return 1 if current >= _MAX_OPERATION_ID else current + 1


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
        *,
        resource_policy: ResourcePolicy | None = None,
    ) -> None:
        super().__init__()
        selected_policy = select_resource_policy() if resource_policy is None else resource_policy
        if type(selected_policy) is not ResourcePolicy:
            raise ValueError("resource_policy must be a ResourcePolicy")
        if selected_policy.maximum_preview_request_pages > MAX_ORGANIZER_PREVIEW_BATCH_PAGES:
            raise ValueError("resource policy exceeds the organizer preview batch limit")
        if _THUMBNAIL_WIDTH * _THUMBNAIL_HEIGHT > (selected_policy.maximum_preview_pixels_per_page):
            raise ValueError("resource policy cannot accommodate the bounded preview dimensions")
        if (
            _SCAN_REVIEW_PREVIEW_WIDTH * _SCAN_REVIEW_PREVIEW_HEIGHT
            > selected_policy.maximum_preview_pixels_per_page
        ):
            raise ValueError("resource policy cannot accommodate scan-review preview dimensions")
        if (
            2 * _SCAN_REVIEW_PREVIEW_WIDTH * _SCAN_REVIEW_PREVIEW_HEIGHT * 4
            > selected_policy.thumbnail_preview_cache_bytes
        ):
            raise ValueError("resource policy cannot retain both scan-review previews")
        self.setObjectName("paperWorkShellWindow")
        self.setWindowTitle("PaperWorkShell")
        self.setAccessibleName("PaperWorkShell guided packet builder")
        self._commands = commands
        self._worker = worker
        self._resource_policy = selected_policy
        self._state: OrganizerViewState | None = None
        self._model = OrganizerPageListModel(
            maximum_preview_cache_bytes=selected_policy.thumbnail_preview_cache_bytes
        )
        self._quality_findings_model = QualityFindingListModel()
        self._thumbnail_generation = 0
        self._next_operation_id = 1
        self._active_operation: _LongOperation | None = None
        self._active_preview_page_ids: tuple[UUID, ...] = ()
        self._preview_batch_complete = False
        self._deferred_import: tuple[Path, str] | None = None
        self._deferred_preview_state: OrganizerViewState | None = None
        self._quality_generation = 0
        self._pending_quality_request: tuple[UUID, int] | None = None
        self._active_quality_binding: tuple[UUID, int, int] | None = None
        self._quality_result_focus_requested = False
        self._quality_presentation = _QualityPresentationState.NOT_CHECKED
        self._case_reopen_required = False
        self._controls_enabled = False
        self._closing_requested = False
        self._closing_import_cleanup = False
        self._allow_close = False
        self._last_announced_progress: tuple[str, str, int] | None = None
        self._latest_import_progress: UiProgress | None = None
        self._import_presentation = _ImportPresentationState.IDLE
        self._pending_failure_focus: str | None = None
        self._startup_recovery_announced = False
        self._pending_startup_unavailable_case_ids: frozenset[UUID] = frozenset()
        self._pending_scan_review_page_id: UUID | None = None
        self._scan_review_start_operation_id: int | None = None
        self._scan_review_start_page_id: UUID | None = None
        self._scan_review_dialog: ScanReviewDialog | None = None
        self._scan_review_preview_generation = 0
        self._active_scan_review_preview: _ScanReviewPreviewOperation | None = None
        self._pending_scan_review_preview: (
            tuple[ScanReviewDraft, ScanReviewPreviewVariant] | None
        ) = None
        self._scan_review_preview_succeeded = False
        self._pending_scan_review_apply_draft: ScanReviewDraft | None = None
        self._scan_review_apply_operation_id: int | None = None
        self._scan_review_apply_draft: ScanReviewDraft | None = None
        self._scan_review_apply_completed = False
        self._scan_review_original_bytes = 0
        self._scan_review_adjusted_bytes = 0
        self._ocr_language_dialog: OcrLanguageDialog | None = None
        self._ocr_language_refresh_request_id: int | None = None
        self._ocr_language_saved_message: str | None = None

        self._preview_refresh_timer = QTimer(self)
        self._preview_refresh_timer.setSingleShot(True)
        self._preview_refresh_timer.setInterval(_PREVIEW_REFRESH_DEBOUNCE_MS)
        self._preview_refresh_timer.timeout.connect(self._refresh_visible_previews)

        self._stack = QStackedWidget()
        self._stack.setObjectName("workflowStack")
        self._status_surface = self._build_status_surface()
        self._ocr_language_surface = self._build_ocr_language_surface()
        self._welcome_page = self._build_welcome_page()
        self._organizer_page = self._build_organizer_page()
        self._stack.addWidget(self._welcome_page)
        self._stack.addWidget(self._organizer_page)
        central = QWidget()
        central.setObjectName("applicationSurface")
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        central_layout.addWidget(self._ocr_language_surface)
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

    @property
    def resource_policy(self) -> ResourcePolicy:
        """Return the immutable policy actually enforcing this window's preview work."""

        return self._resource_policy

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
        self._status_label.setStyleSheet(_FOCUS_VISIBLE_TEXT_STYLE)
        self._status_label.setWordWrap(True)
        self._error_label = QLabel()
        self._error_label.setObjectName("errorLabel")
        self._error_label.setAccessibleName("Action error")
        self._error_label.setAccessibleDescription(
            "The latest safe error code and recovery message."
        )
        self._error_label.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._error_label.setStyleSheet(_FOCUS_VISIBLE_TEXT_STYLE)
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
            "Request cancellation of the running document operation, including import, preview, "
            "or page-quality review. Escape."
        )
        self._cancel_button.clicked.connect(self._cancel_active_operation)
        self._cancel_button.setVisible(False)
        self._retry_preview_button = QPushButton("Retry selected preview")
        self._retry_preview_button.setObjectName("retryPreviewButton")
        self._retry_preview_button.setAccessibleDescription(
            "Retry thumbnail rendering for the selected failed page. Ctrl+Shift+R."
        )
        self._retry_preview_button.clicked.connect(self._retry_failed_preview)
        self._retry_preview_button.setVisible(False)
        self._retry_save_button = QPushButton("Retry saving changes")
        self._retry_save_button.setObjectName("retrySaveButton")
        self._retry_save_button.setAccessibleDescription(
            "Retry the retained organizer autosave after a recoverable failure."
        )
        self._retry_save_button.clicked.connect(self._request_retry_save)
        self._retry_save_button.setVisible(False)
        self._retry_close_button = QPushButton("Retry closing PaperWorkShell")
        self._retry_close_button.setObjectName("retryCloseButton")
        self._retry_close_button.setAccessibleDescription(
            "Retry secure shutdown cleanup after PaperWorkShell remains open."
        )
        self._retry_close_button.clicked.connect(self.request_shutdown)
        self._retry_close_button.setVisible(False)
        status_actions.addWidget(self._cancel_button)
        status_actions.addWidget(self._retry_preview_button)
        status_actions.addWidget(self._retry_save_button)
        status_actions.addWidget(self._retry_close_button)
        status_actions.addStretch(1)
        status_layout.addWidget(self._status_label)
        status_layout.addWidget(self._error_label)
        status_layout.addWidget(self._progress)
        status_layout.addLayout(status_actions)
        return status_group

    def _build_ocr_language_surface(self) -> QGroupBox:
        surface = QGroupBox("OCR language settings")
        surface.setObjectName("ocrLanguageSettingsGroup")
        surface.setAccessibleName("OCR language settings")
        surface.setAccessibleDescription(
            "Manage verified local OCR language packs from the case chooser or organizer."
        )
        layout = QVBoxLayout(surface)
        guidance = QLabel(
            "Choose the verified local languages PaperWorkShell may use for OCR. "
            "No language data is downloaded."
        )
        guidance.setObjectName("ocrLanguageSettingsGuidance")
        guidance.setAccessibleName("OCR language settings guidance")
        guidance.setTextFormat(Qt.TextFormat.PlainText)
        guidance.setWordWrap(True)
        self._ocr_language_button = QPushButton("Manage OCR languages…")
        self._ocr_language_button.setObjectName("ocrLanguageSettingsButton")
        self._ocr_language_button.setAccessibleName("Manage OCR languages")
        self._ocr_language_button.setAccessibleDescription(
            "Review installed and verified offline OCR languages. "
            "Shortcut Ctrl+Shift+L. No language data is downloaded."
        )
        self._ocr_language_button.clicked.connect(self._open_ocr_languages)
        layout.addWidget(guidance)
        layout.addWidget(self._ocr_language_button)
        return surface

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
        self._organizer_guidance = QLabel()
        self._organizer_guidance.setObjectName("organizerGuidance")
        self._organizer_guidance.setAccessibleName("Page organizer guidance")
        self._organizer_guidance.setAccessibleDescription(
            "Current page organizer state and the next available action."
        )
        self._organizer_guidance.setTextFormat(Qt.TextFormat.PlainText)
        self._organizer_guidance.setWordWrap(True)
        self._organizer_guidance.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._organizer_guidance.setFrameShape(QFrame.Shape.StyledPanel)
        self._organizer_guidance.setMargin(8)
        self._organizer_guidance.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self._organizer_guidance.setVisible(False)
        layout.addWidget(self._organizer_guidance)

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
        self._scan_review_button = self._action_button(
            "Review scan adjustments",
            "scanReviewButton",
            "Compare the unchanged original with provisional scan adjustments. "
            "Nothing changes until Apply adjustments is chosen. Ctrl+Shift+A.",
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
        self._scan_review_button.clicked.connect(self._request_scan_review)
        self._change_order_button.clicked.connect(self._change_full_order)
        self._undo_button.clicked.connect(self._request_undo)
        self._redo_button.clicked.connect(self._request_redo)
        action_layout.addWidget(self._move_earlier_button, 0, 0)
        action_layout.addWidget(self._move_later_button, 0, 1)
        action_layout.addWidget(self._rotate_button, 0, 2)
        action_layout.addWidget(self._remove_button, 1, 0)
        action_layout.addWidget(self._scan_review_button, 1, 1)
        action_layout.addWidget(self._change_order_button, 1, 2)
        action_layout.addWidget(self._undo_button, 2, 0)
        action_layout.addWidget(self._redo_button, 2, 1)
        organizer_layout.addLayout(action_layout)
        layout.addWidget(organizer_group, 1)

        quality_group = QGroupBox("Page quality review")
        quality_group.setObjectName("qualityReviewGroup")
        quality_group.setAccessibleName("Page quality review")
        quality_group.setAccessibleDescription(
            "Limited automatic review signals for the exact saved case revision. "
            "These suggestions never complete a requirement."
        )
        quality_layout = QVBoxLayout(quality_group)
        self._quality_summary = QLabel(
            "Not checked for this saved revision. Automatic quality signals are suggestions; "
            "review every page yourself."
        )
        self._quality_summary.setObjectName("qualityReviewSummary")
        self._quality_summary.setAccessibleName("Page quality review status")
        self._quality_summary.setAccessibleDescription(self._quality_summary.text())
        self._quality_summary.setTextFormat(Qt.TextFormat.PlainText)
        self._quality_summary.setWordWrap(True)
        self._quality_summary.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._quality_summary.setStyleSheet(_FOCUS_VISIBLE_TEXT_STYLE)
        self._quality_findings_view = QListView()
        self._quality_findings_view.setObjectName("qualityFindingsList")
        self._quality_findings_view.setAccessibleName("Page quality findings")
        self._quality_findings_view.setAccessibleDescription(
            "Use the arrow keys to select a review signal. Press Enter or choose Go to selected "
            "page to navigate without confirming or completing anything."
        )
        self._quality_findings_view.setModel(self._quality_findings_model)
        self._quality_findings_view.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._quality_findings_view.setDragEnabled(False)
        self._quality_findings_view.setAcceptDrops(False)
        self._quality_findings_view.setDropIndicatorShown(False)
        self._quality_findings_view.setWordWrap(True)
        self._quality_findings_view.setResizeMode(QListView.ResizeMode.Adjust)
        self._quality_findings_view.setUniformItemSizes(False)
        self._quality_findings_view.activated.connect(self._go_to_quality_finding)
        self._quality_findings_view.selectionModel().selectionChanged.connect(
            self._quality_finding_selection_changed
        )
        self._quality_check_button = self._action_button(
            "Check page quality",
            "qualityCheckButton",
            "Run limited automatic checks for the exact saved revision. "
            "Results need human review and never complete a requirement. Ctrl+Shift+Q.",
        )
        self._quality_check_button.clicked.connect(self._request_quality_review)
        self._quality_go_to_page_button = self._action_button(
            "Go to selected page",
            "qualityGoToPageButton",
            "Select and focus the organizer page referenced by the current review signal.",
        )
        self._quality_go_to_page_button.clicked.connect(self._go_to_selected_quality_finding)
        quality_actions = QHBoxLayout()
        quality_actions.addWidget(self._quality_check_button)
        quality_actions.addWidget(self._quality_go_to_page_button)
        quality_actions.addStretch(1)
        quality_layout.addWidget(self._quality_summary)
        quality_layout.addWidget(self._quality_findings_view)
        quality_layout.addLayout(quality_actions)
        layout.addWidget(quality_group)

        tab_chain = (
            self._import_button,
            self._close_case_button,
            self._page_view,
            self._move_earlier_button,
            self._move_later_button,
            self._rotate_button,
            self._remove_button,
            self._scan_review_button,
            self._change_order_button,
            self._undo_button,
            self._redo_button,
            self._quality_summary,
            self._quality_check_button,
            self._quality_findings_view,
            self._quality_go_to_page_button,
            self._cancel_button,
            self._retry_preview_button,
            self._retry_save_button,
            self._retry_close_button,
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
            ("Ctrl+Shift+A", self._request_scan_review),
            ("Ctrl+Shift+Q", self._request_quality_review),
            ("Ctrl+Shift+L", self._open_ocr_languages),
            ("Ctrl+Shift+O", self._change_full_order),
            ("Ctrl+Shift+R", self._retry_failed_preview),
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
        self._worker.startup_recovery_changed.connect(self._startup_recovery_changed)
        self._worker.cases_changed.connect(self._cases_changed)
        self._worker.ocr_languages_changed.connect(self._ocr_languages_changed)
        self._worker.ocr_languages_failed.connect(self._ocr_languages_failed)
        self._worker.state_changed.connect(self._state_changed)
        self._worker.thumbnails_ready.connect(self._thumbnails_ready)
        self._worker.quality_review_ready.connect(self._quality_review_ready)
        self._worker.scan_review_started.connect(self._scan_review_started)
        self._worker.scan_review_start_failed.connect(self._scan_review_start_failed)
        self._worker.scan_review_preview_ready.connect(self._scan_review_preview_ready)
        self._worker.scan_review_progress_changed.connect(self._scan_review_progress_changed)
        self._worker.scan_review_preview_cancelled.connect(self._scan_review_preview_cancelled)
        self._worker.scan_review_preview_failed.connect(self._scan_review_preview_failed)
        self._worker.scan_review_preview_finished.connect(self._scan_review_preview_finished)
        self._worker.scan_review_applied.connect(self._scan_review_applied)
        self._worker.scan_review_apply_failed.connect(self._scan_review_apply_failed)
        self._worker.scan_review_discard_failed.connect(self._scan_review_discard_failed)
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

    @Slot()
    def _open_ocr_languages(self) -> None:
        if (
            self._ocr_language_dialog is not None
            or self._ocr_language_refresh_request_id is not None
            or not self._ocr_language_button.isEnabled()
        ):
            QApplication.beep()
            return
        dialog = OcrLanguageDialog(self._allocate_operation_id, self)
        dialog.refresh_requested.connect(self._request_ocr_language_refresh)
        dialog.selection_requested.connect(self._commands.ocr_languages_select_requested.emit)
        dialog.selection_saved.connect(self._ocr_language_selection_saved)
        dialog.finished.connect(self._ocr_language_dialog_finished)
        self._ocr_language_dialog = dialog
        self._ocr_language_saved_message = None
        self._update_ocr_language_availability()
        dialog.show()
        dialog.request_refresh()

    @Slot(int)
    def _request_ocr_language_refresh(self, request_id: int) -> None:
        """Retain one app-level queue gate until its exact terminal signal arrives."""

        if (
            type(request_id) is not int
            or request_id <= 0
            or self._ocr_language_dialog is None
            or self._ocr_language_refresh_request_id is not None
        ):
            return
        self._ocr_language_refresh_request_id = request_id
        self._update_ocr_language_availability()
        self._commands.ocr_languages_refresh_requested.emit(request_id)

    @Slot(int, object)
    def _ocr_languages_changed(self, request_id: int, snapshot: object) -> None:
        if type(snapshot) is not OcrLanguageSnapshot:
            return
        dialog = self._ocr_language_dialog
        if dialog is not None:
            dialog.set_snapshot(request_id, snapshot)
        self._complete_ocr_language_refresh(request_id)

    @Slot(int, object)
    def _ocr_languages_failed(self, request_id: int, failure: object) -> None:
        if type(failure) is not UiFailure or failure.operation_id != request_id:
            return
        dialog = self._ocr_language_dialog
        if dialog is not None:
            dialog.set_failure(request_id, failure.code, failure.message)
        self._complete_ocr_language_refresh(request_id)

    def _complete_ocr_language_refresh(self, request_id: int) -> bool:
        if type(request_id) is not int or self._ocr_language_refresh_request_id != request_id:
            return False
        self._ocr_language_refresh_request_id = None
        self._update_ocr_language_availability()
        return True

    @Slot(object)
    def _ocr_language_selection_saved(self, snapshot: object) -> None:
        if type(snapshot) is not OcrLanguageSnapshot:
            return
        # OCR findings are configuration-bound as well as revision-bound. A
        # new process-session language set makes any accepted review stale,
        # even though it does not mutate the case manifest.
        self._invalidate_quality_review()
        count = len(snapshot.selected_language_codes)
        noun = "language" if count == 1 else "languages"
        self._ocr_language_saved_message = (
            f"OCR language selection saved for this application session: {count} {noun}."
        )

    @Slot(int)
    def _ocr_language_dialog_finished(self, _result: int) -> None:
        dialog = self._ocr_language_dialog
        if dialog is None:
            return
        saved_message = self._ocr_language_saved_message
        self._ocr_language_dialog = None
        self._ocr_language_saved_message = None
        dialog.deleteLater()
        self._update_ocr_language_availability()
        if not self._closing_requested and self._ocr_language_button.isEnabled():
            QTimer.singleShot(0, self._focus_ocr_language_button_after_dialog)
        if saved_message is not None:
            self._announce(saved_message)

    def _focus_ocr_language_button_after_dialog(self) -> None:
        if self._closing_requested or not self._ocr_language_button.isEnabled():
            return
        self.activateWindow()
        self._ocr_language_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._ensure_widget_visible(self._ocr_language_button)

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
        if case_index.unavailable and not self._startup_recovery_announced:
            self._pending_startup_unavailable_case_ids = (
                self._pending_startup_unavailable_case_ids
                | frozenset(issue.case_id for issue in case_index.unavailable)
            )
        elif case_index.unavailable:
            self._announce(
                f"{len(case_index.unavailable)} case entries need recovery review and are unavailable.",
                assertive=True,
            )

    @Slot(object)
    def _state_changed(self, state: object) -> None:
        if type(state) is not OrganizerViewState:
            return
        dialog = self._scan_review_dialog
        if dialog is not None and (
            state.case_id != dialog.draft.case_id
            or state.revision != dialog.draft.manifest_revision
        ):
            self._dismiss_scan_review_after_state_change()
        prior_state = self._state
        if _quality_state_invalidates_review(prior_state, state):
            self._invalidate_quality_review()
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
            self._latest_import_progress = None
            self._import_presentation = _ImportPresentationState.IDLE
        self._state = state
        self._model.set_organizer_state(state)
        self._update_organizer_guidance()
        self._update_import_availability()
        self._update_preview_retry_action()
        self._update_quality_actions()
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
        self._update_preview_retry_action()
        self._update_quality_actions()
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
        ):
            return
        if type(results) is not tuple:
            self._reject_preview_result(operation_id)
            return
        expected = self._active_preview_page_ids
        snapshots: list[
            tuple[
                PdfThumbnailSnapshot | ImagePagePreviewSnapshot | ImagePageTransformSnapshot,
                bytes,
            ]
        ] = []
        for result in results:
            if type(result) is PdfThumbnailResult:
                snapshots.append((result.snapshot, result.snapshot.pixels))
            elif type(result) is ImagePagePreviewResult:
                snapshots.append((result.snapshot, result.snapshot.pixels))
            elif type(result) is ImagePageTransformResult:
                snapshots.append((result.snapshot, result.pixels))
            else:
                self._reject_preview_result(operation_id)
                return
        received = tuple(snapshot.page_id for snapshot, _pixels in snapshots)
        if received != expected:
            self._reject_preview_result(operation_id)
            return
        images_valid = True
        for snapshot, pixels in snapshots:
            image = QImage(
                pixels,
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
        if not images_valid:
            self._reject_preview_result(operation_id)
            return
        self._preview_batch_complete = images_valid
        retained = self._current_preview_page_ids()
        self._model.touch_previews(retained)
        self._model.evict_previews(
            protected_page_ids=retained,
        )

    @Slot(int, int, object)
    def _quality_review_ready(
        self,
        operation_id: int,
        generation: int,
        snapshot: object,
    ) -> None:
        active = self._active_operation
        binding = self._active_quality_binding
        if (
            active is None
            or active.operation_id != operation_id
            or active.kind is not _LongOperationKind.QUALITY
            or binding is None
            or binding[2] != generation
            or generation != self._quality_generation
        ):
            return
        if active.cancellation.cancelled():
            if self._quality_presentation is _QualityPresentationState.ACTIVE:
                self._model.clear_quality_review()
                self._quality_findings_model.set_snapshot(None)
                self._quality_presentation = _QualityPresentationState.CANCELLED
                self._cancel_button.setEnabled(False)
                self._update_quality_summary()
                self._update_quality_actions()
                self._pending_failure_focus = "quality"
                self._announce(
                    "Page quality check cancelled. No review results were applied.",
                    assertive=True,
                )
            return
        if self._quality_presentation is not _QualityPresentationState.ACTIVE:
            return
        state = self._state
        if (
            type(snapshot) is not QualityReviewSnapshot
            or state is None
            or state.case_id is None
            or state.autosave_pending
            or binding[:2] != (state.case_id, state.revision)
            or snapshot.case_id != state.case_id
            or snapshot.manifest_revision != state.revision
            or snapshot.page_ids != tuple(page.page_id for page in state.pages)
            or not self._model.set_quality_review(snapshot)
        ):
            self._reject_quality_result(operation_id)
            return
        self._quality_findings_model.set_snapshot(snapshot)
        self._cancel_button.setEnabled(False)
        if self._quality_findings_model.rowCount() > 0:
            index = self._quality_findings_model.index(0, 0)
            self._quality_findings_view.setCurrentIndex(index)
            self._quality_findings_view.selectionModel().select(
                index,
                QItemSelectionModel.SelectionFlag.ClearAndSelect,
            )
        self._quality_presentation = _QualityPresentationState.READY
        self._update_quality_summary()
        self._update_quality_actions()

    def _reject_quality_result(self, operation_id: int) -> None:
        self._failed(
            UiFailure(
                code="PWS_UI_QUALITY_RESULT_INVALID",
                message=(
                    "PaperWorkShell could not display page-quality review results safely. "
                    "Run the check again."
                ),
                retryable=True,
                operation_id=operation_id,
            )
        )

    @Slot(int, object)
    def _scan_review_started(self, operation_id: int, draft: object) -> None:
        """Open one exact revision-bound review after the backend accepts it."""

        if operation_id != self._scan_review_start_operation_id:
            return
        expected_page_id = self._scan_review_start_page_id
        self._scan_review_start_operation_id = None
        self._scan_review_start_page_id = None
        self._pending_scan_review_page_id = None
        state = self._state
        expected_pages = (
            tuple(page for page in state.pages if page.page_id == expected_page_id)
            if state is not None
            else ()
        )
        expected_page = expected_pages[0] if len(expected_pages) == 1 else None
        if (
            type(draft) is not ScanReviewDraft
            or state is None
            or state.autosave_pending
            or state.case_id != draft.case_id
            or state.revision != draft.manifest_revision
            or draft.page_id != expected_page_id
            or self._selected_page_id() != expected_page_id
            or expected_page is None
            or expected_page.scan_review_intent is None
            or expected_page.scan_review_intent != draft.base_intent
            or expected_page.rotation_degrees != int(draft.base_intent.additional_rotation)
            or draft.candidate_intent != draft.base_intent
            or draft.suggestion_present
            or self._scan_review_dialog is not None
        ):
            if type(draft) is ScanReviewDraft:
                self._commands.scan_review_cancel_requested.emit(draft)
            self._show_scan_review_failure(
                UiFailure(
                    code="PWS_UI_SCAN_REVIEW_RESULT_INVALID",
                    message="PaperWorkShell could not open scan review safely. Start the review again.",
                    retryable=True,
                )
            )
            self._update_import_availability()
            self._update_page_actions()
            self._update_preview_retry_action()
            return
        dialog = ScanReviewDialog(draft, self)
        dialog.preview_requested.connect(self._queue_scan_review_preview)
        dialog.apply_requested.connect(self._request_scan_review_apply)
        dialog.cancel_preview_requested.connect(self._cancel_scan_review_preview)
        dialog.finished.connect(self._scan_review_dialog_finished)
        self._scan_review_dialog = dialog
        self._scan_review_apply_completed = False
        self._scan_review_original_bytes = 0
        self._scan_review_adjusted_bytes = 0
        self._preview_refresh_timer.stop()
        self._deferred_preview_state = None
        self._hide_error()
        self._update_page_actions()
        self._update_preview_retry_action()
        dialog.open()
        if dialog.set_preview_loading(draft):
            self._start_scan_review_preview_cycle(draft)

    @Slot(int, object)
    def _scan_review_start_failed(self, operation_id: int, failure: object) -> None:
        if operation_id != self._scan_review_start_operation_id:
            return
        self._scan_review_start_operation_id = None
        self._scan_review_start_page_id = None
        self._pending_scan_review_page_id = None
        self._update_import_availability()
        if type(failure) is UiFailure:
            self._show_scan_review_failure(failure)
        else:
            self._show_scan_review_failure(
                UiFailure(
                    code="PWS_UI_UNEXPECTED",
                    message="PaperWorkShell could not open scan review. Try again.",
                    retryable=True,
                )
            )
        self._update_page_actions()
        self._update_preview_retry_action()

    @Slot(int, int, object)
    def _scan_review_preview_ready(
        self,
        operation_id: int,
        generation: int,
        result: object,
    ) -> None:
        active = self._active_scan_review_preview
        dialog = self._scan_review_dialog
        if (
            active is None
            or dialog is None
            or operation_id != active.operation_id
            or generation != active.generation
            or type(result) is not ScanReviewPreviewResult
            or result.draft != active.draft
            or result.variant is not active.variant
            or active.cancellation.cancelled()
            or not self._scan_review_result_matches_window_request(result)
        ):
            return
        image = self._scan_review_image(result)
        if image is None:
            self._fail_active_scan_review_preview(
                "PWS_UI_SCAN_REVIEW_RESULT_INVALID",
                "PaperWorkShell could not display that preview safely. Update the preview again.",
            )
            return
        byte_cost = image.width() * image.height() * 4
        if not self._reserve_scan_review_preview_bytes(active.variant, byte_cost):
            self._fail_active_scan_review_preview(
                "PWS_UI_SCAN_REVIEW_LIMIT_EXCEEDED",
                "The preview exceeds the configured memory limit. Close scan review and try again.",
            )
            return
        if active.variant is ScanReviewPreviewVariant.ORIGINAL:
            accepted = dialog.set_original_image(result.draft, image)
        else:
            accepted = dialog.set_adjusted_image(result.draft, image)
        if not accepted:
            self._fail_active_scan_review_preview(
                "PWS_UI_SCAN_REVIEW_RESULT_INVALID",
                "PaperWorkShell rejected a stale scan-review preview. Update the preview again.",
            )
            return
        self._scan_review_preview_succeeded = True

    @Slot(object)
    def _scan_review_progress_changed(self, progress: object) -> None:
        active = self._active_scan_review_preview
        dialog = self._scan_review_dialog
        if (
            active is None
            or dialog is None
            or type(progress) is not UiProgress
            or progress.operation_id != active.operation_id
            or active.cancellation.cancelled()
        ):
            return
        dialog.set_progress(
            active.draft,
            progress.phase,
            progress.completed,
            progress.total,
        )

    @Slot(int, int, str)
    def _scan_review_preview_cancelled(
        self,
        operation_id: int,
        generation: int,
        _message: str,
    ) -> None:
        active = self._active_scan_review_preview
        dialog = self._scan_review_dialog
        if (
            active is None
            or dialog is None
            or operation_id != active.operation_id
            or generation != active.generation
        ):
            return
        if active.draft == dialog.draft:
            cancelled_variant = (
                ScanReviewPreviewVariant.ADJUSTED
                if active.variant is ScanReviewPreviewVariant.ORIGINAL
                and self._scan_review_preview_succeeded
                else active.variant
            )
            self._pending_scan_review_preview = None
            if cancelled_variant is ScanReviewPreviewVariant.ORIGINAL:
                self._scan_review_original_bytes = 0
            else:
                self._scan_review_adjusted_bytes = 0
            self._rebalance_preview_cache()
            dialog.set_preview_cancelled(active.draft, cancelled_variant)

    @Slot(int, int, object)
    def _scan_review_preview_failed(
        self,
        operation_id: int,
        generation: int,
        failure: object,
    ) -> None:
        active = self._active_scan_review_preview
        dialog = self._scan_review_dialog
        if (
            active is None
            or dialog is None
            or operation_id != active.operation_id
            or generation != active.generation
        ):
            return
        if active.draft != dialog.draft:
            return
        self._pending_scan_review_preview = None
        if active.variant is ScanReviewPreviewVariant.ORIGINAL:
            self._scan_review_original_bytes = 0
            self._rebalance_preview_cache()
        else:
            self._scan_review_adjusted_bytes = 0
            self._rebalance_preview_cache()
        if type(failure) is UiFailure:
            dialog.set_failure(
                active.draft,
                failure.code,
                failure.message,
                variant=active.variant,
            )
        else:
            dialog.set_failure(
                active.draft,
                "PWS_UI_UNEXPECTED",
                "PaperWorkShell could not complete scan review. Try again.",
                variant=active.variant,
            )

    @Slot(int, int)
    def _scan_review_preview_finished(self, operation_id: int, generation: int) -> None:
        active = self._active_scan_review_preview
        if active is None or operation_id != active.operation_id or generation != active.generation:
            return
        succeeded = self._scan_review_preview_succeeded
        self._active_scan_review_preview = None
        self._scan_review_preview_succeeded = False
        pending = self._pending_scan_review_preview
        self._pending_scan_review_preview = None
        dialog = self._scan_review_dialog
        pending_apply = self._pending_scan_review_apply_draft
        self._pending_scan_review_apply_draft = None
        if pending_apply is not None:
            if (
                dialog is not None
                and succeeded
                and active.variant is ScanReviewPreviewVariant.ADJUSTED
                and not active.cancellation.cancelled()
                and active.draft == pending_apply
                and dialog.draft == pending_apply
                and pending is None
            ):
                self._start_scan_review_apply(pending_apply)
            elif dialog is not None and active.draft == dialog.draft:
                dialog.set_failure(
                    active.draft,
                    "PWS_UI_SCAN_REVIEW_RESULT_INVALID",
                    "The preview did not finish safely. Update the preview again.",
                    variant=active.variant,
                )
            return
        if (
            dialog is not None
            and pending is not None
            and not succeeded
            and active.variant is ScanReviewPreviewVariant.ORIGINAL
            and pending[1] is ScanReviewPreviewVariant.ADJUSTED
            and active.draft == dialog.draft
        ):
            dialog.set_failure(
                active.draft,
                "PWS_UI_SCAN_REVIEW_RESULT_INVALID",
                "The original preview did not finish safely. Update the preview again.",
                variant=active.variant,
            )
            return
        if dialog is None or pending is None:
            if (
                dialog is not None
                and active.draft == dialog.draft
                and (
                    active.cancellation.cancelled()
                    or (succeeded and active.variant is ScanReviewPreviewVariant.ORIGINAL)
                )
            ):
                cancelled_variant = (
                    ScanReviewPreviewVariant.ADJUSTED
                    if succeeded and active.variant is ScanReviewPreviewVariant.ORIGINAL
                    else active.variant
                )
                dialog.set_preview_cancelled(active.draft, cancelled_variant)
            elif dialog is not None and active.draft == dialog.draft and not succeeded:
                dialog.set_failure(
                    active.draft,
                    "PWS_UI_SCAN_REVIEW_RESULT_INVALID",
                    "The preview did not finish safely. Update the preview again.",
                    variant=active.variant,
                )
            return
        draft, variant = pending
        if draft != dialog.draft:
            return
        self._start_scan_review_preview(draft, variant)

    @Slot(int, object)
    def _scan_review_applied(self, operation_id: int, state: object) -> None:
        if operation_id != self._scan_review_apply_operation_id:
            return
        dialog = self._scan_review_dialog
        draft = self._scan_review_apply_draft
        self._scan_review_apply_operation_id = None
        self._scan_review_apply_draft = None
        if dialog is None or draft is None:
            return
        applied_page = (
            next(
                (page for page in state.pages if page.page_id == draft.page_id),
                None,
            )
            if type(state) is OrganizerViewState
            else None
        )
        if (
            type(state) is not OrganizerViewState
            or self._state != state
            or state.case_id != draft.case_id
            or state.revision != draft.manifest_revision
            or applied_page is None
            or applied_page.scan_review_intent != draft.candidate_intent
        ):
            dialog.set_failure(
                draft,
                "PWS_UI_SCAN_REVIEW_RESULT_INVALID",
                "PaperWorkShell could not verify the applied scan-review state safely.",
            )
            return
        self._scan_review_apply_completed = True
        if not dialog.set_apply_complete(
            draft,
            changes_applied=state.autosave_pending,
        ):
            self._scan_review_apply_completed = False
            self._show_scan_review_failure(
                UiFailure(
                    code="PWS_UI_SCAN_REVIEW_RESULT_INVALID",
                    message="PaperWorkShell applied the case change, but could not close scan review safely.",
                    retryable=False,
                )
            )

    @Slot(int, object)
    def _scan_review_apply_failed(self, operation_id: int, failure: object) -> None:
        if operation_id != self._scan_review_apply_operation_id:
            return
        dialog = self._scan_review_dialog
        draft = self._scan_review_apply_draft
        self._scan_review_apply_operation_id = None
        self._scan_review_apply_draft = None
        if dialog is None or draft is None:
            return
        if type(failure) is UiFailure:
            dialog.set_failure(draft, failure.code, failure.message)
        else:
            dialog.set_failure(
                draft,
                "PWS_UI_UNEXPECTED",
                "PaperWorkShell could not apply those adjustments. Try again.",
            )

    @Slot(object)
    def _scan_review_discard_failed(self, failure: object) -> None:
        if self._closing_requested:
            return
        if type(failure) is UiFailure:
            self._show_scan_review_failure(failure)

    @Slot(object)
    def _progress_changed(self, progress: object) -> None:
        if type(progress) is not UiProgress:
            return
        active = self._active_operation
        if active is None or active.operation_id != progress.operation_id:
            return
        if active.cancellation.cancelled():
            return
        if active.kind is _LongOperationKind.QUALITY and (
            self._quality_presentation
            in {
                _QualityPresentationState.READY,
                _QualityPresentationState.FAILED,
                _QualityPresentationState.CANCELLED,
            }
        ):
            return
        prior = self._last_announced_progress
        phase_changed = prior is None or prior[:2] != (progress.operation, progress.phase)
        announcement_step = max(1, progress.total // 4)
        completed_enough = prior is None or progress.completed - prior[2] >= announcement_step
        announce_checkpoint = (
            phase_changed or completed_enough or progress.completed == progress.total
        )
        if active.kind is _LongOperationKind.IMPORT and announce_checkpoint:
            self._latest_import_progress = progress
            self._import_presentation = _ImportPresentationState.ACTIVE
            self._update_organizer_guidance()
        elif active.kind is _LongOperationKind.QUALITY:
            quality_message = (
                f"Checking this saved revision — {progress.phase}: "
                f"{progress.completed} of {progress.total}. Results remain automatic suggestions."
            )
            self._quality_summary.setText(quality_message)
            self._quality_summary.setAccessibleDescription(quality_message)
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
        if announce_checkpoint:
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

    @Slot(object)
    def _startup_recovery_changed(self, result: object) -> None:
        if type(result) is not StartupRecoveryResult:
            return
        represented_attention = frozenset(
            case.case_id
            for case in result.cases
            if case.status is CaseRecoveryStatus.UNAVAILABLE or bool(case.issues)
        )
        unmatched_unavailable = self._pending_startup_unavailable_case_ids - represented_attention
        self._pending_startup_unavailable_case_ids = frozenset()
        message, assertive = _startup_recovery_notice(
            result,
            additional_needs_review=len(unmatched_unavailable),
        )
        self._startup_recovery_announced = True
        self._hide_error()
        self._announce(message, assertive=assertive)

    @Slot(int, str)
    def _operation_status_changed(self, operation_id: int, message: str) -> None:
        active = self._active_operation
        if active is None or active.operation_id != operation_id or active.cancellation.cancelled():
            return
        if active.kind is _LongOperationKind.PREVIEW and self._pending_failure_focus == "preview":
            return
        if active.kind is _LongOperationKind.QUALITY and self._pending_failure_focus == "quality":
            return
        if active.kind is _LongOperationKind.IMPORT:
            self._hide_error()
            self._import_presentation = _ImportPresentationState.IDLE
            self._latest_import_progress = None
            self._update_organizer_guidance()
        self._announce(message)

    def _reject_preview_result(self, operation_id: int) -> None:
        self._failed(
            UiFailure(
                code="PWS_UI_PREVIEW_RESULT_INVALID",
                message="PaperWorkShell could not display the preview safely. Retry the selected preview.",
                retryable=True,
                operation_id=operation_id,
            )
        )

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
        self._latest_import_progress = None
        self._import_presentation = _ImportPresentationState.REOPEN_REQUIRED
        self._update_organizer_guidance()
        self._preview_refresh_timer.stop()
        self._autosave_timer.stop()
        self._deferred_import = None
        self._deferred_preview_state = None
        self._hide_error()
        self._hide_retry_save()
        self._update_import_availability()
        self._update_page_actions()
        self._update_preview_retry_action()
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
        if active.kind is _LongOperationKind.IMPORT:
            self._latest_import_progress = None
            self._import_presentation = _ImportPresentationState.CANCELLED
            self._update_organizer_guidance()
        elif (
            active.kind is _LongOperationKind.PREVIEW and self._pending_quality_request is not None
        ):
            return
        elif (
            active.kind is _LongOperationKind.PREVIEW
            and self._quality_presentation is _QualityPresentationState.CANCELLED
        ):
            return
        elif active.kind is _LongOperationKind.QUALITY and self._active_quality_binding is not None:
            self._model.clear_quality_review()
            self._quality_findings_model.set_snapshot(None)
            self._quality_presentation = _QualityPresentationState.CANCELLED
            self._update_quality_summary()
            self._pending_failure_focus = "quality"
        self._announce(message, assertive=True)

    @Slot(int)
    def _operation_finished(self, operation_id: int) -> None:
        active = self._active_operation
        if active is None or active.operation_id != operation_id:
            return
        finished_preview = active.kind is _LongOperationKind.PREVIEW
        finished_quality = active.kind is _LongOperationKind.QUALITY
        quality_result_focus_requested = finished_quality and self._quality_result_focus_requested
        preview_batch_complete = self._preview_batch_complete
        if finished_preview:
            self._model.finish_preview_loading(self._active_preview_page_ids)
        self._finish_active_operation()
        if finished_quality:
            self._active_quality_binding = None
            self._quality_result_focus_requested = False
        if self._closing_requested:
            self._pending_failure_focus = None
            return
        pending_scan_review = self._pending_scan_review_page_id
        pending_quality = self._pending_quality_request
        if pending_quality is not None:
            self._pending_failure_focus = None
            self._pending_quality_request = None
            state = self._state
            if (
                state is None
                or state.case_id != pending_quality[0]
                or state.revision != pending_quality[1]
                or state.autosave_pending
                or not state.pages
            ):
                self._quality_presentation = _QualityPresentationState.NOT_CHECKED
                self._update_quality_summary()
                self._update_quality_actions()
                return
            self._start_quality_review(*pending_quality)
            return
        if pending_scan_review is not None:
            self._pending_failure_focus = None
            self._pending_scan_review_page_id = None
            if self._selected_page_id() != pending_scan_review:
                self._show_scan_review_failure(
                    UiFailure(
                        code="PWS_UI_SCAN_REVIEW_SELECTION_CHANGED",
                        message=(
                            "The selected page changed before scan review opened. "
                            "Select a page and try again."
                        ),
                        retryable=True,
                    )
                )
                self._update_import_availability()
                self._update_page_actions()
                self._update_preview_retry_action()
                return
            self._start_scan_review(pending_scan_review)
            return
        deferred_import = self._deferred_import
        if deferred_import is not None:
            self._pending_failure_focus = None
            self._deferred_import = None
            self._start_import(*deferred_import)
            return
        deferred_preview = self._deferred_preview_state
        self._deferred_preview_state = None
        if deferred_preview is not None and not deferred_preview.autosave_pending:
            self._schedule_preview_refresh()
        elif finished_preview and preview_batch_complete:
            self._schedule_preview_refresh()
        elif finished_quality:
            self._schedule_preview_refresh()
        self._restore_failure_recovery_focus()
        if (
            quality_result_focus_requested
            and self._quality_presentation is _QualityPresentationState.READY
        ):
            self._focus_quality_result_or_action()

    @Slot(object)
    def _failed(self, failure: object) -> None:
        if type(failure) is not UiFailure:
            return
        active = self._active_operation
        if failure.operation_id is not None and (
            active is None or active.operation_id != failure.operation_id
        ):
            return
        correlated_kind = (
            active.kind
            if active is not None
            and failure.operation_id is not None
            and active.operation_id == failure.operation_id
            else None
        )
        if correlated_kind is _LongOperationKind.QUALITY and self._active_quality_binding is None:
            return
        if correlated_kind is _LongOperationKind.PREVIEW:
            for page_id in self._active_preview_page_ids:
                self._model.set_preview_failed(page_id)
            self._preview_batch_complete = False
        elif correlated_kind is _LongOperationKind.IMPORT:
            self._latest_import_progress = None
            self._import_presentation = _ImportPresentationState.FAILED
        elif correlated_kind is _LongOperationKind.QUALITY:
            self._cancel_button.setEnabled(False)
            self._model.clear_quality_review()
            self._quality_findings_model.set_snapshot(None)
            self._quality_presentation = _QualityPresentationState.FAILED
            self._update_quality_summary()
        self._error_label.setText(f"{failure.code}: {failure.message}")
        self._error_label.setVisible(True)
        pending_save = self._state is not None and self._state.autosave_pending
        if failure.retryable and pending_save:
            self._retry_save_button.setVisible(True)
        else:
            self._hide_retry_save()
        if correlated_kind is _LongOperationKind.PREVIEW and failure.retryable:
            self._pending_failure_focus = "preview"
        elif correlated_kind is _LongOperationKind.IMPORT and not self._case_reopen_required:
            self._pending_failure_focus = "import"
        elif correlated_kind is _LongOperationKind.QUALITY:
            self._pending_failure_focus = "quality"
        else:
            self._pending_failure_focus = None
        self._update_preview_retry_action()
        self._update_quality_actions()
        self._update_organizer_guidance()
        self._announce(f"Action failed. {failure.message}", assertive=True)
        if self._retry_save_button.isVisible():
            self._retry_save_button.setFocus()
            self._ensure_widget_visible(self._retry_save_button)
        elif self._pending_failure_focus is not None:
            self._error_label.setFocus()
            self._ensure_widget_visible(self._error_label)
        else:
            self._focus_safe_control()

    @Slot()
    def _shutdown_complete(self) -> None:
        self._allow_close = True
        self.close()

    @Slot(object)
    def _shutdown_refused(self, refusal: object) -> None:
        if type(refusal) is not ShutdownRefusalKind:
            return
        self._closing_requested = False
        self._closing_import_cleanup = False
        self._update_organizer_guidance()
        if refusal is ShutdownRefusalKind.CLEANUP_RETRY:
            self._hide_retry_save()
            self._retry_close_button.setVisible(True)
            self._retry_close_button.setEnabled(True)
            self._announce(
                "The application remains open because secure cleanup could not finish. "
                "Retry closing PaperWorkShell.",
                assertive=True,
            )
            self._retry_close_button.setFocus(Qt.FocusReason.OtherFocusReason)
            self._ensure_widget_visible(self._retry_close_button)
            return
        self._set_controls_enabled(True)
        self._hide_retry_close()
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
        if (
            self._case_reopen_required
            or self._scan_review_is_opening_or_active()
            or self._quality_is_pending_or_running()
        ):
            return
        unsupported = tuple(page.page_id for page in state.pages if not page.preview_supported)
        for page_id in unsupported:
            self._model.set_preview_unavailable(page_id)
        retained = self._current_preview_page_ids()
        self._model.touch_previews(retained)
        self._model.evict_previews(
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
        )[: self._resource_policy.maximum_preview_request_pages]
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
            or self._scan_review_is_opening_or_active()
            or self._quality_is_pending_or_running()
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
            or self._scan_review_is_opening_or_active()
            or self._quality_is_pending_or_running()
        ):
            return
        self._request_previews(state)

    def _request_quality_review(self) -> None:
        """Queue one explicit, revision-bound advisory review."""

        if not self._quality_check_button.isEnabled():
            return
        state = self._state
        if (
            state is None
            or state.case_id is None
            or state.revision is None
            or state.autosave_pending
            or not state.pages
        ):
            return
        quality_action_had_focus = self._quality_check_button.hasFocus()
        self._preview_refresh_timer.stop()
        self._deferred_preview_state = None
        self._hide_error()
        self._model.clear_quality_review()
        self._quality_findings_model.set_snapshot(None)
        active = self._active_operation
        if active is not None:
            if active.kind is not _LongOperationKind.PREVIEW:
                return
            self._pending_quality_request = (state.case_id, state.revision)
            self._quality_result_focus_requested = quality_action_had_focus
            self._quality_generation = _next_generation(self._quality_generation)
            self._quality_presentation = _QualityPresentationState.QUEUED
            self._cancel_active_preview()
            self._cancel_button.setEnabled(True)
            self._update_quality_summary()
            self._update_import_availability()
            self._update_page_actions()
            self._update_quality_actions()
            self._announce(
                "Stopping the current page preview before the page quality check starts."
            )
            return
        self._quality_result_focus_requested = quality_action_had_focus
        self._start_quality_review(state.case_id, state.revision)

    def _start_quality_review(self, case_id: UUID, revision: int) -> None:
        state = self._state
        if (
            self._closing_requested
            or self._active_operation is not None
            or self._pending_quality_request is not None
            or state is None
            or state.case_id != case_id
            or state.revision != revision
            or state.autosave_pending
            or not state.pages
        ):
            return
        check_had_focus = self._quality_check_button.hasFocus()
        self._quality_generation = _next_generation(self._quality_generation)
        generation = self._quality_generation
        operation = self._begin_long_operation(_LongOperationKind.QUALITY)
        self._active_quality_binding = (case_id, revision, generation)
        self._quality_presentation = _QualityPresentationState.ACTIVE
        self._model.clear_quality_review()
        self._quality_findings_model.set_snapshot(None)
        self._update_quality_summary()
        self._update_quality_actions()
        self._commands.quality_review_requested.emit(
            operation.operation_id,
            generation,
            case_id,
            revision,
            operation.cancellation,
        )
        if check_had_focus:
            self._cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)
            self._ensure_widget_visible(self._cancel_button)

    @Slot(object)
    def _go_to_quality_finding(self, index: object) -> None:
        if not isinstance(index, QModelIndex) or not index.isValid():
            return
        finding = self._quality_findings_model.finding_at(index.row())
        state = self._state
        snapshot = self._model.quality_review
        if finding is None or state is None or snapshot is None:
            return
        if (
            state.case_id != snapshot.case_id
            or state.revision != snapshot.manifest_revision
            or state.autosave_pending
        ):
            self._invalidate_quality_review()
            return
        row = self._model.row_for_page(finding.page_id)
        if row is None:
            self._invalidate_quality_review()
            self._announce(
                "The page quality results no longer match this organizer. Run the check again.",
                assertive=True,
            )
            return
        page_index = self._model.index(row, 0)
        self._page_view.selectionModel().select(
            page_index,
            QItemSelectionModel.SelectionFlag.ClearAndSelect,
        )
        self._page_view.setCurrentIndex(page_index)
        self._page_view.scrollTo(page_index, QAbstractItemView.ScrollHint.PositionAtCenter)
        self._page_view.setFocus(Qt.FocusReason.OtherFocusReason)
        self._ensure_widget_visible(self._page_view)
        self._announce(f"Showing page {finding.page_position}. Needs review: {finding.title}.")

    def _go_to_selected_quality_finding(self) -> None:
        self._go_to_quality_finding(self._quality_findings_view.currentIndex())

    def _quality_finding_selection_changed(
        self,
        _selected: QItemSelection,
        _deselected: QItemSelection,
    ) -> None:
        self._update_quality_actions()

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
            overscan_rows=self._resource_policy.preview_prefetch_radius_pages,
            maximum_count=self._resource_policy.maximum_preview_request_pages,
        )
        return tuple(state.pages[row].page_id for row in rows if state.pages[row].preview_supported)

    def _choose_import(self) -> None:
        if (
            not self._controls_enabled
            or self._closing_requested
            or not self._import_button.isEnabled()
            or self._scan_review_is_opening_or_active()
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
        if self._case_reopen_required or self._scan_review_is_opening_or_active():
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
            self._import_presentation = _ImportPresentationState.QUEUED
            self._latest_import_progress = None
            self._update_organizer_guidance()
            self._update_import_availability()
            self._cancel_active_preview()
            self._announce("Stopping the current preview before import starts.")
            return
        self._start_import(source_path, display_name)

    def _start_import(self, source_path: Path, display_name: str) -> None:
        self._hide_error()
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
            or self._scan_review_is_opening_or_active()
            or self._quality_is_pending_or_running()
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
        self._update_preview_retry_action()
        self._schedule_preview_refresh()

    def _update_page_actions(self) -> None:
        row = self._page_view.currentIndex().row()
        page_count = self._model.rowCount()
        state = self._state
        scan_review_busy = self._scan_review_is_opening_or_active()
        quality_busy = self._quality_is_pending_or_running()
        self._close_case_button.setEnabled(
            self._controls_enabled
            and state is not None
            and state.case_id is not None
            and not scan_review_busy
            and not quality_busy
        )
        editable = (
            self._controls_enabled
            and not self._case_reopen_required
            and not self._import_is_pending_or_running()
            and not scan_review_busy
            and not quality_busy
        )
        has_selection = editable and 0 <= row < page_count
        self._move_earlier_button.setEnabled(has_selection and row > 0)
        self._move_later_button.setEnabled(has_selection and row < page_count - 1)
        self._rotate_button.setEnabled(has_selection)
        self._remove_button.setEnabled(has_selection)
        selected_page = self._model.pages[row] if 0 <= row < page_count else None
        scan_review_idle = (
            self._scan_review_start_operation_id is None
            and self._scan_review_dialog is None
            and self._pending_scan_review_page_id is None
        )
        self._scan_review_button.setEnabled(
            has_selection
            and scan_review_idle
            and state is not None
            and not state.autosave_pending
            and selected_page is not None
            and selected_page.scan_review_intent is not None
        )
        self._change_order_button.setEnabled(editable and page_count > 1)
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

    def _request_scan_review(self) -> None:
        """Start a provisional image-page review without changing case intent."""

        if not self._scan_review_button.isEnabled():
            return
        page_id = self._selected_page_id()
        if page_id is None:
            return
        self._preview_refresh_timer.stop()
        self._deferred_preview_state = None
        active = self._active_operation
        if active is not None:
            if active.kind is not _LongOperationKind.PREVIEW:
                return
            self._pending_scan_review_page_id = page_id
            self._cancel_active_preview()
            self._update_import_availability()
            self._update_page_actions()
            self._update_preview_retry_action()
            self._announce("Stopping the current page preview before scan review opens.")
            return
        self._start_scan_review(page_id)

    def _start_scan_review(self, page_id: UUID) -> None:
        if (
            self._scan_review_start_operation_id is not None
            or self._scan_review_dialog is not None
            or self._closing_requested
        ):
            return
        operation_id = self._allocate_operation_id()
        self._scan_review_start_operation_id = operation_id
        self._scan_review_start_page_id = page_id
        self._pending_scan_review_page_id = None
        self._hide_error()
        self._update_import_availability()
        self._update_page_actions()
        self._update_preview_retry_action()
        self._announce("Opening scan review. Nothing has been applied.")
        self._commands.scan_review_begin_requested.emit(operation_id, page_id, None)

    @Slot(object)
    def _queue_scan_review_preview(self, draft: object) -> None:
        dialog = self._scan_review_dialog
        if dialog is None or type(draft) is not ScanReviewDraft or draft != dialog.draft:
            return
        self._scan_review_adjusted_bytes = 0
        self._rebalance_preview_cache()
        self._start_scan_review_preview_cycle(draft)

    def _start_scan_review_preview_cycle(self, draft: ScanReviewDraft) -> None:
        variant = (
            ScanReviewPreviewVariant.ORIGINAL
            if self._scan_review_original_bytes == 0
            else ScanReviewPreviewVariant.ADJUSTED
        )
        if self._active_scan_review_preview is not None:
            self._pending_scan_review_preview = (draft, variant)
            self._active_scan_review_preview.cancellation.cancel()
            return
        if variant is ScanReviewPreviewVariant.ORIGINAL:
            self._pending_scan_review_preview = (
                draft,
                ScanReviewPreviewVariant.ADJUSTED,
            )
        self._start_scan_review_preview(draft, variant)

    def _start_scan_review_preview(
        self,
        draft: ScanReviewDraft,
        variant: ScanReviewPreviewVariant,
    ) -> None:
        if self._active_scan_review_preview is not None:
            raise RuntimeError("scan-review preview overlap")
        self._scan_review_preview_generation += 1
        operation = _ScanReviewPreviewOperation(
            operation_id=self._allocate_operation_id(),
            generation=self._scan_review_preview_generation,
            draft=draft,
            variant=variant,
            cancellation=OperationCancellation(),
        )
        self._active_scan_review_preview = operation
        self._scan_review_preview_succeeded = False
        self._commands.scan_review_preview_requested.emit(
            operation.operation_id,
            operation.generation,
            operation.draft,
            operation.variant,
            _SCAN_REVIEW_PREVIEW_WIDTH,
            _SCAN_REVIEW_PREVIEW_HEIGHT,
            operation.cancellation,
        )

    @Slot()
    def _cancel_scan_review_preview(self) -> None:
        active = self._active_scan_review_preview
        pending = self._pending_scan_review_preview
        self._pending_scan_review_preview = None
        if active is not None:
            active.cancellation.cancel()
        dialog = self._scan_review_dialog
        if pending is None or active is None or dialog is None:
            return
        draft, variant = pending
        is_automatic_adjusted = (
            active.variant is ScanReviewPreviewVariant.ORIGINAL
            and variant is ScanReviewPreviewVariant.ADJUSTED
            and active.draft == draft
        )
        if is_automatic_adjusted or draft != dialog.draft:
            return
        if variant is ScanReviewPreviewVariant.ORIGINAL:
            self._scan_review_original_bytes = 0
        else:
            self._scan_review_adjusted_bytes = 0
        self._rebalance_preview_cache()
        dialog.set_preview_cancelled(draft, variant)

    @Slot(object)
    def _request_scan_review_apply(self, draft: object) -> None:
        dialog = self._scan_review_dialog
        if (
            dialog is None
            or type(draft) is not ScanReviewDraft
            or draft != dialog.draft
            or not dialog.preview_ready_for_apply
            or self._scan_review_apply_operation_id is not None
            or self._pending_scan_review_apply_draft is not None
        ):
            return
        active = self._active_scan_review_preview
        if active is not None:
            if (
                active.variant is ScanReviewPreviewVariant.ADJUSTED
                and active.draft == draft
                and self._scan_review_preview_succeeded
                and not active.cancellation.cancelled()
            ):
                self._pending_scan_review_apply_draft = draft
            return
        self._start_scan_review_apply(draft)

    def _start_scan_review_apply(self, draft: ScanReviewDraft) -> None:
        """Emit Apply only after the exact adjusted preview operation has drained."""

        operation_id = self._allocate_operation_id()
        self._scan_review_apply_operation_id = operation_id
        self._scan_review_apply_draft = draft
        self._commands.scan_review_apply_requested.emit(operation_id, draft)

    @Slot(int)
    def _scan_review_dialog_finished(self, _result: int) -> None:
        dialog = self._scan_review_dialog
        if dialog is None:
            return
        draft = dialog.draft
        applied = self._scan_review_apply_completed
        if self._active_scan_review_preview is not None:
            self._active_scan_review_preview.cancellation.cancel()
        if not applied:
            self._commands.scan_review_cancel_requested.emit(draft)
        dialog.deleteLater()
        self._clear_scan_review_presentation()
        if not self._closing_requested:
            self._page_view.setFocus(Qt.FocusReason.OtherFocusReason)
            self._schedule_preview_refresh()

    def _clear_scan_review_presentation(self) -> None:
        self._scan_review_dialog = None
        self._scan_review_start_operation_id = None
        self._scan_review_start_page_id = None
        self._pending_scan_review_page_id = None
        self._active_scan_review_preview = None
        self._pending_scan_review_preview = None
        self._scan_review_preview_succeeded = False
        self._pending_scan_review_apply_draft = None
        self._scan_review_apply_operation_id = None
        self._scan_review_apply_draft = None
        self._scan_review_apply_completed = False
        self._scan_review_original_bytes = 0
        self._scan_review_adjusted_bytes = 0
        self._update_import_availability()
        self._update_page_actions()
        self._update_preview_retry_action()
        self._rebalance_preview_cache()

    def _dismiss_scan_review_after_state_change(self) -> None:
        dialog = self._scan_review_dialog
        if dialog is None:
            return
        if self._active_scan_review_preview is not None:
            self._active_scan_review_preview.cancellation.cancel()
        try:
            dialog.finished.disconnect(self._scan_review_dialog_finished)
        except RuntimeError:
            pass
        dialog.done(QDialog.DialogCode.Rejected)
        dialog.deleteLater()
        self._clear_scan_review_presentation()

    def _fail_active_scan_review_preview(self, code: str, message: str) -> None:
        active = self._active_scan_review_preview
        dialog = self._scan_review_dialog
        self._pending_scan_review_preview = None
        if active is not None and dialog is not None and active.draft == dialog.draft:
            if active.variant is ScanReviewPreviewVariant.ORIGINAL:
                self._scan_review_original_bytes = 0
            else:
                self._scan_review_adjusted_bytes = 0
            self._rebalance_preview_cache()
            dialog.set_failure(active.draft, code, message, variant=active.variant)

    @staticmethod
    def _scan_review_image(result: ScanReviewPreviewResult) -> QImage | None:
        snapshot: ImagePagePreviewSnapshot | ImagePageTransformSnapshot
        if result.variant is ScanReviewPreviewVariant.ORIGINAL:
            value = result.result
            if type(value) is not ImagePagePreviewResult:
                return None
            snapshot = value.snapshot
            pixels = snapshot.pixels
        else:
            value = result.result
            if type(value) is not ImagePageTransformResult:
                return None
            snapshot = value.snapshot
            pixels = value.pixels
        image = QImage(
            pixels,
            snapshot.width_pixels,
            snapshot.height_pixels,
            snapshot.row_stride_bytes,
            QImage.Format.Format_RGBA8888,
        ).copy()
        return None if image.isNull() else image

    @staticmethod
    def _scan_review_result_matches_window_request(
        result: ScanReviewPreviewResult,
    ) -> bool:
        request = result.result.request
        snapshot = result.result.snapshot
        return (
            request.maximum_width_pixels == _SCAN_REVIEW_PREVIEW_WIDTH
            and request.maximum_height_pixels == _SCAN_REVIEW_PREVIEW_HEIGHT
            and 1 <= snapshot.width_pixels <= _SCAN_REVIEW_PREVIEW_WIDTH
            and 1 <= snapshot.height_pixels <= _SCAN_REVIEW_PREVIEW_HEIGHT
        )

    def _reserve_scan_review_preview_bytes(
        self,
        variant: ScanReviewPreviewVariant,
        byte_cost: int,
    ) -> bool:
        if type(byte_cost) is not int or byte_cost <= 0:
            return False
        original = (
            byte_cost
            if variant is ScanReviewPreviewVariant.ORIGINAL
            else self._scan_review_original_bytes
        )
        adjusted = (
            byte_cost
            if variant is ScanReviewPreviewVariant.ADJUSTED
            else self._scan_review_adjusted_bytes
        )
        if original + adjusted > self._resource_policy.thumbnail_preview_cache_bytes:
            return False
        if variant is ScanReviewPreviewVariant.ORIGINAL:
            self._scan_review_original_bytes = byte_cost
        else:
            self._scan_review_adjusted_bytes = byte_cost
        self._rebalance_preview_cache()
        return (
            self._model.retained_preview_bytes + original + adjusted
            <= self._resource_policy.thumbnail_preview_cache_bytes
        )

    def _rebalance_preview_cache(self) -> None:
        review_bytes = self._scan_review_original_bytes + self._scan_review_adjusted_bytes
        remaining = max(
            0,
            self._resource_policy.thumbnail_preview_cache_bytes - review_bytes,
        )
        self._model.evict_previews(maximum_bytes=remaining)

    def _show_scan_review_failure(self, failure: UiFailure) -> None:
        self._error_label.setText(f"{failure.code}: {failure.message}")
        self._error_label.setVisible(True)
        self._announce(f"Scan review failed. {failure.message}", assertive=True)
        self._error_label.setFocus(Qt.FocusReason.OtherFocusReason)
        self._ensure_widget_visible(self._error_label)

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
        if active.kind is _LongOperationKind.QUALITY and self._quality_presentation in {
            _QualityPresentationState.READY,
            _QualityPresentationState.FAILED,
        }:
            self._cancel_button.setEnabled(False)
            return
        if self._pending_quality_request is not None and active.kind is _LongOperationKind.PREVIEW:
            self._pending_quality_request = None
            self._quality_generation = _next_generation(self._quality_generation)
            self._quality_presentation = _QualityPresentationState.CANCELLED
            self._cancel_button.setEnabled(False)
            self._update_quality_summary()
            self._update_quality_actions()
            self._announce(
                "Queued page quality check cancelled. No review results were applied.",
                assertive=True,
            )
            return
        active.cancellation.cancel()
        self._cancel_button.setEnabled(False)
        if active.kind is _LongOperationKind.QUALITY:
            self._quality_summary.setText(
                "Cancellation requested. Waiting for the page quality check to stop safely."
            )
            self._quality_summary.setAccessibleDescription(self._quality_summary.text())
        self._announce("Cancellation requested. Waiting for the operation to stop.", assertive=True)

    def _retry_failed_preview(self) -> None:
        if (
            self._scan_review_is_opening_or_active()
            or not self._retry_preview_button.isVisible()
            or not self._retry_preview_button.isEnabled()
        ):
            return
        page_id = self._selected_page_id()
        if page_id is None or not self._model.reset_failed_preview(page_id):
            self._update_preview_retry_action()
            return
        self._hide_error()
        self._page_view.setFocus(Qt.FocusReason.OtherFocusReason)
        self._ensure_widget_visible(self._page_view)
        self._update_preview_retry_action()
        self._announce("Retrying the selected page preview.")
        self._start_preview((page_id,))

    def _begin_long_operation(self, kind: _LongOperationKind) -> _LongOperation:
        if self._active_operation is not None:
            raise RuntimeError("long operation overlap")
        operation_id = self._allocate_operation_id()
        operation = _LongOperation(operation_id, kind, OperationCancellation())
        self._active_operation = operation
        if kind is _LongOperationKind.IMPORT:
            self._latest_import_progress = None
            self._import_presentation = _ImportPresentationState.ACTIVE
            self._update_organizer_guidance()
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        self._progress.setFormat(f"{kind.value} queued")
        self._cancel_button.setVisible(True)
        self._cancel_button.setEnabled(True)
        self._update_import_availability()
        self._update_page_actions()
        self._update_preview_retry_action()
        self._update_quality_actions()
        return operation

    def _allocate_operation_id(self) -> int:
        operation_id = self._next_operation_id
        self._next_operation_id = 1 if operation_id >= _MAX_OPERATION_ID else operation_id + 1
        return operation_id

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
        finished_kind = None if self._active_operation is None else self._active_operation.kind
        if self._cancel_button.hasFocus():
            self._focus_safe_control()
        self._progress.setVisible(False)
        self._cancel_button.setVisible(False)
        self._active_operation = None
        if finished_kind is _LongOperationKind.IMPORT:
            self._latest_import_progress = None
            if self._import_presentation is _ImportPresentationState.ACTIVE:
                self._import_presentation = _ImportPresentationState.IDLE
        self._active_preview_page_ids = ()
        self._preview_batch_complete = False
        self._last_announced_progress = None
        self._update_import_availability()
        self._update_page_actions()
        self._update_preview_retry_action()
        self._update_quality_actions()
        self._update_organizer_guidance()

    def _import_is_pending_or_running(self) -> bool:
        active = self._active_operation
        return self._deferred_import is not None or (
            active is not None and active.kind is _LongOperationKind.IMPORT
        )

    def _scan_review_is_opening_or_active(self) -> bool:
        return (
            self._pending_scan_review_page_id is not None
            or self._scan_review_start_operation_id is not None
            or self._scan_review_dialog is not None
        )

    def _quality_is_pending_or_running(self) -> bool:
        active = self._active_operation
        return self._pending_quality_request is not None or (
            active is not None and active.kind is _LongOperationKind.QUALITY
        )

    def _invalidate_quality_review(self) -> None:
        """Cancel and hide every result that is not exact for the next organizer state."""

        quality_had_focus = (
            self._quality_findings_view.hasFocus()
            or self._quality_go_to_page_button.hasFocus()
            or self._quality_summary.hasFocus()
        )
        self._quality_generation = _next_generation(self._quality_generation)
        self._pending_quality_request = None
        self._active_quality_binding = None
        self._quality_result_focus_requested = False
        active = self._active_operation
        if active is not None and active.kind is _LongOperationKind.QUALITY:
            active.cancellation.cancel()
            self._cancel_button.setEnabled(False)
        self._model.clear_quality_review()
        self._quality_findings_model.set_snapshot(None)
        self._quality_presentation = _QualityPresentationState.NOT_CHECKED
        self._update_quality_summary()
        self._update_quality_actions()
        if quality_had_focus:
            if self._quality_check_button.isVisible() and self._quality_check_button.isEnabled():
                self._quality_check_button.setFocus(Qt.FocusReason.OtherFocusReason)
                self._ensure_widget_visible(self._quality_check_button)
            else:
                self._focus_safe_control()

    def _update_quality_summary(self) -> None:
        presentation = self._quality_presentation
        snapshot = self._model.quality_review
        if presentation is _QualityPresentationState.QUEUED:
            message = (
                "Page quality check queued. PaperWorkShell is stopping the current preview first. "
                "No review results are shown yet."
            )
        elif presentation is _QualityPresentationState.ACTIVE:
            message = (
                "Checking this saved revision. Results are limited automatic suggestions and do "
                "not complete a requirement."
            )
        elif presentation is _QualityPresentationState.READY and snapshot is not None:
            finding_count = len(snapshot.findings)
            if finding_count:
                affected_pages = len({finding.page_id for finding in snapshot.findings})
                finding_noun = "signal" if finding_count == 1 else "signals"
                page_noun = "page" if affected_pages == 1 else "pages"
                message = (
                    f"Needs review: {finding_count} automatic review {finding_noun} on "
                    f"{affected_pages} {page_noun}. Select each signal and review the page; these "
                    "suggestions do not complete a requirement."
                )
            else:
                message = (
                    "No review signals were found by these limited checks. Review every page "
                    "yourself before export."
                )
        elif presentation is _QualityPresentationState.CANCELLED:
            message = (
                "Page quality check cancelled. No review results were applied. Run the check "
                "again when ready."
            )
        elif presentation is _QualityPresentationState.FAILED:
            message = (
                "Page quality check did not finish. No review results are shown. Review the Action "
                "error and run the check again."
            )
        else:
            message = (
                "Not checked for this saved revision. Automatic quality signals are suggestions; "
                "review every page yourself."
            )
        self._quality_summary.setText(message)
        self._quality_summary.setAccessibleDescription(message)

    def _update_quality_actions(self) -> None:
        state = self._state
        active = self._active_operation
        check_enabled = (
            self._controls_enabled
            and not self._closing_requested
            and not self._case_reopen_required
            and state is not None
            and state.case_id is not None
            and state.revision is not None
            and bool(state.pages)
            and not state.autosave_pending
            and self._pending_quality_request is None
            and (active is None or active.kind is _LongOperationKind.PREVIEW)
            and not self._scan_review_is_opening_or_active()
        )
        self._quality_check_button.setEnabled(check_enabled)
        findings_enabled = (
            self._controls_enabled
            and not self._closing_requested
            and self._model.quality_review is not None
            and self._quality_findings_model.rowCount() > 0
        )
        self._quality_findings_view.setEnabled(findings_enabled)
        self._quality_go_to_page_button.setEnabled(
            findings_enabled and self._quality_findings_view.currentIndex().isValid()
        )

    def _focus_quality_result_or_action(self) -> None:
        if self._quality_findings_view.isVisible() and self._quality_findings_view.isEnabled():
            self._quality_findings_view.setFocus(Qt.FocusReason.OtherFocusReason)
            self._ensure_widget_visible(self._quality_findings_view)
        elif self._quality_check_button.isVisible() and self._quality_check_button.isEnabled():
            self._quality_check_button.setFocus(Qt.FocusReason.OtherFocusReason)
            self._ensure_widget_visible(self._quality_check_button)
        else:
            self._focus_safe_control()

    def _update_organizer_guidance(self) -> None:
        state = self._state
        if state is None or state.case_id is None:
            self._organizer_guidance.setVisible(False)
            return
        if self._closing_requested and self._closing_import_cleanup:
            message = (
                "Closing PaperWorkShell safely. Waiting for the current import to stop and secure "
                "cleanup to finish."
            )
        elif self._case_reopen_required:
            message = (
                "The import was saved, but this organizer must be reopened before the new pages "
                "can be used. Choose Close case, then reopen this case."
            )
        elif self._import_presentation is _ImportPresentationState.FAILED:
            recovery = (
                "Review Action error below, then choose"
                if self._error_label.isVisible()
                else "Choose"
            )
            message = f"Import did not finish. {recovery} Import document (Ctrl+I) to try again."
        elif self._import_presentation is _ImportPresentationState.CANCELLED:
            message = (
                "Import cancelled. No source was committed. Choose Import document (Ctrl+I) "
                "to try again."
            )
        elif self._import_presentation is _ImportPresentationState.QUEUED:
            message = (
                "Import queued. PaperWorkShell is stopping the current preview before import "
                "starts. Keep PaperWorkShell open."
            )
        elif self._import_is_pending_or_running():
            progress = self._latest_import_progress
            phase = (
                ""
                if progress is None
                else f" — {progress.phase}: {progress.completed} of {progress.total}"
            )
            message = (
                f"Importing document{phase}. PaperWorkShell copies and checks the file before new "
                "pages appear. Keep PaperWorkShell open; press Escape to cancel safely."
            )
        elif not state.pages:
            message = "No pages yet. Choose Import document (Ctrl+I) to add a PDF, JPEG, or PNG."
        else:
            self._organizer_guidance.setVisible(False)
            return
        self._organizer_guidance.setText(message)
        self._organizer_guidance.setAccessibleDescription(message)
        self._organizer_guidance.setVisible(True)

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
        self._update_organizer_guidance()
        if had_focus:
            self._focus_safe_control()

    def _hide_retry_save(self) -> None:
        if self._retry_save_button.hasFocus():
            self._focus_safe_control()
        self._retry_save_button.setVisible(False)

    def _hide_retry_close(self) -> None:
        if self._retry_close_button.hasFocus():
            self._focus_safe_control()
        self._retry_close_button.setVisible(False)

    def _update_preview_retry_action(self) -> None:
        state = self._state
        page_id = self._selected_page_id()
        failed = page_id is not None and self._model.preview_state(page_id) is PreviewState.FAILED
        visible = (
            self._controls_enabled
            and state is not None
            and state.case_id is not None
            and not self._case_reopen_required
            and failed
        )
        enabled = (
            visible
            and self._active_operation is None
            and not self._closing_requested
            and not self._scan_review_is_opening_or_active()
        )
        if self._retry_preview_button.hasFocus() and not enabled:
            self._focus_safe_control()
        self._retry_preview_button.setVisible(visible)
        self._retry_preview_button.setEnabled(enabled)

    def _restore_failure_recovery_focus(self) -> None:
        target = self._pending_failure_focus
        self._pending_failure_focus = None
        if self._closing_requested:
            return
        if (
            self._case_reopen_required
            and self._close_case_button.isVisible()
            and self._close_case_button.isEnabled()
        ):
            self._close_case_button.setFocus(Qt.FocusReason.OtherFocusReason)
            self._ensure_widget_visible(self._close_case_button)
            return
        if target == "preview":
            self._update_preview_retry_action()
            if self._retry_preview_button.isVisible() and self._retry_preview_button.isEnabled():
                self._retry_preview_button.setFocus(Qt.FocusReason.OtherFocusReason)
                self._ensure_widget_visible(self._retry_preview_button)
                return
        elif (
            target == "import"
            and self._import_button.isVisible()
            and self._import_button.isEnabled()
        ):
            self._import_button.setFocus(Qt.FocusReason.OtherFocusReason)
            self._ensure_widget_visible(self._import_button)
            return
        elif target == "quality":
            self._update_quality_actions()
            if self._quality_check_button.isVisible() and self._quality_check_button.isEnabled():
                self._quality_check_button.setFocus(Qt.FocusReason.OtherFocusReason)
                self._ensure_widget_visible(self._quality_check_button)
                return
        if self._error_label.isVisible():
            self._error_label.setFocus(Qt.FocusReason.OtherFocusReason)
            self._ensure_widget_visible(self._error_label)
        else:
            self._focus_safe_control()

    def _update_import_availability(self) -> None:
        state = self._state
        self._import_button.setEnabled(
            self._controls_enabled
            and state is not None
            and state.case_id is not None
            and not self._case_reopen_required
            and not self._import_is_pending_or_running()
            and not self._scan_review_is_opening_or_active()
            and not self._quality_is_pending_or_running()
        )
        self._update_ocr_language_availability()

    def _update_ocr_language_availability(self) -> None:
        self._ocr_language_button.setEnabled(
            self._controls_enabled
            and not self._closing_requested
            and self._active_operation is None
            and not self._scan_review_is_opening_or_active()
            and not self._quality_is_pending_or_running()
            and self._ocr_language_dialog is None
            and self._ocr_language_refresh_request_id is None
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
            self._scan_review_button,
            self._change_order_button,
            self._undo_button,
            self._redo_button,
        ):
            widget.setEnabled(enabled)
        self._update_import_availability()
        self._update_page_actions()
        self._update_preview_retry_action()
        self._update_quality_actions()

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
        retrying_cleanup = self._retry_close_button.isVisible()
        queued_import = self._deferred_import is not None
        active = self._active_operation
        active_import = active is not None and active.kind is _LongOperationKind.IMPORT
        self._closing_requested = True
        self._closing_import_cleanup = queued_import or active_import
        self._hide_retry_close()
        self._autosave_timer.stop()
        self._deferred_import = None
        if queued_import:
            self._import_presentation = _ImportPresentationState.CANCELLED
        self._deferred_preview_state = None
        self._invalidate_quality_review()
        self._discard_ocr_language_dialog_for_shutdown()
        self._discard_scan_review_for_shutdown()
        if self._cancel_button.hasFocus():
            self._focus_safe_control()
        self._cancel_active_operation_silently()
        self._update_organizer_guidance()
        self._set_controls_enabled(False)
        self._announce(
            "Retrying secure cleanup before closing PaperWorkShell."
            if retrying_cleanup
            else "Closing PaperWorkShell after saving queued changes."
        )
        self._commands.shutdown_requested.emit()

    def _discard_ocr_language_dialog_for_shutdown(self) -> None:
        dialog = self._ocr_language_dialog
        if dialog is not None:
            dialog.discard_for_shutdown()

    def _discard_scan_review_for_shutdown(self) -> None:
        dialog = self._scan_review_dialog
        if self._active_scan_review_preview is not None:
            self._active_scan_review_preview.cancellation.cancel()
        if dialog is not None:
            draft = dialog.draft
            try:
                dialog.finished.disconnect(self._scan_review_dialog_finished)
            except RuntimeError:
                pass
            dialog.done(QDialog.DialogCode.Rejected)
            dialog.deleteLater()
            self._commands.scan_review_cancel_requested.emit(draft)
        self._clear_scan_review_presentation()

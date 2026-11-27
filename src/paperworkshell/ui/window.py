"""Keyboard-first PaperWorkShell case and page organizer window."""

from __future__ import annotations

import stat
from dataclasses import dataclass, replace
from enum import StrEnum
from math import ceil, floor
from pathlib import Path
from typing import Final
from uuid import UUID

from PySide6.QtCore import (
    QEvent,
    QItemSelection,
    QItemSelectionModel,
    QModelIndex,
    QObject,
    QRect,
    Qt,
    QTimer,
    QUrl,
    Slot,
)
from PySide6.QtGui import (
    QAccessible,
    QAccessibleAnnouncementEvent,
    QAction,
    QCloseEvent,
    QDesktopServices,
    QImage,
    QKeySequence,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLayout,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from paperworkshell.app.checklist import (
    ChecklistEditorState,
    is_checklist_announcement_safe,
)
from paperworkshell.app.core_overlay_tools import (
    CoreOverlayToolsState,
    is_core_overlay_announcement_safe,
)
from paperworkshell.app.ocr_languages import OCR_LANGUAGE_DISPLAY_NAMES, OcrLanguageSnapshot
from paperworkshell.app.organizer import (
    MAX_ORGANIZER_PREVIEW_BATCH_PAGES,
    OrganizerPageView,
    OrganizerSignatureImageDeleteOutcome,
    OrganizerSignatureImagePlacementOutcome,
    OrganizerViewState,
    TracePlannedPreview,
)
from paperworkshell.app.package_workflow import PackageReviewState
from paperworkshell.app.portal_constraints import (
    ConstraintEditorState,
    is_constraint_announcement_safe,
)
from paperworkshell.app.quality_analysis import QualityReviewSnapshot
from paperworkshell.app.redaction_tools import (
    RedactionToolsState,
    is_redaction_announcement_safe,
)
from paperworkshell.app.removal_export_review import RemovalExportReview
from paperworkshell.app.scan_review import (
    ScanReviewDraft,
    ScanReviewPreviewResult,
    ScanReviewPreviewVariant,
)
from paperworkshell.app.signature_images import (
    SIGNATURE_IMAGE_DISCLAIMER,
    SignatureImagePlacementChoice,
    SignatureImagePlacementDraft,
    SignatureImagePlacementRequest,
)
from paperworkshell.app.trace_automation import TraceAutomationError
from paperworkshell.app.trace_results import TraceExportEvidence, TraceSavedExportSelection
from paperworkshell.diagnostics import phase_observer
from paperworkshell.domain.assisted_use import AssistedUseError, AssistedUseErrorCode
from paperworkshell.domain.case_lifecycle import CaseDeletionResult, CaseDeletionStatus, CaseIndex
from paperworkshell.domain.compression_review import CompressionPagePreview, CompressionReviewState
from paperworkshell.domain.image_pages import (
    ORGANIZER_PREVIEW_HEIGHT_PIXELS,
    ORGANIZER_PREVIEW_WIDTH_PIXELS,
    ImagePagePreviewResult,
    ImagePagePreviewSnapshot,
    ImageQuarterTurn,
)
from paperworkshell.domain.image_transforms import (
    ImagePageTransformResult,
    ImagePageTransformSnapshot,
    ImageTransformIntent,
)
from paperworkshell.domain.package_summary import PackageSummary, PackageSummaryStatus
from paperworkshell.domain.page_coordinates import (
    ClockwiseRotation,
    NormalizedPageBox,
    PageCoordinateTransform,
    RenderedImageSize,
)
from paperworkshell.domain.pdf_thumbnails import PdfThumbnailResult, PdfThumbnailSnapshot
from paperworkshell.domain.post_export_deletion import (
    PostExportDeletionError,
    PostExportDeletionErrorCode,
    PostExportDeletionInventory,
)
from paperworkshell.domain.recovery import (
    CaseRecoveryStatus,
    StartupRecoveryResult,
    StartupRecoveryStatus,
)
from paperworkshell.domain.redaction_export import (
    REDACTION_EXPORT_REPORT_VERSION,
    RUNTIME_EXPORT_REPORT_VERSION,
    RedactionExportCode,
    RedactionExportReport,
    RedactionExportStatus,
)
from paperworkshell.domain.redaction_verification import runtime_release_qualified
from paperworkshell.domain.resource_policy import (
    NORMAL_RESOURCE_POLICY_V1,
    ResourcePolicy,
    select_resource_policy,
)
from paperworkshell.domain.signature_images import SignatureImageErrorCode
from paperworkshell.domain.target_size_search import TargetSizeColorChoice
from paperworkshell.domain.text_companion import TextCompanionError, TextCompanionErrorCode
from paperworkshell.domain.trace_recipe import TraceOutputStatus, TraceRecipeError
from paperworkshell.localization.catalog import mark, tr
from paperworkshell.overlays.models import OverlayRecord, TextualOverlayPayload
from paperworkshell.preflight.portal_constraints import PortalOutputMode
from paperworkshell.ui.announcements import ScreenReaderAnnouncer
from paperworkshell.ui.assisted_session import AssistedSessionController
from paperworkshell.ui.backend import (
    OperationCancellation,
    OrganizerBackendCommands,
    OrganizerBackendWorker,
    ShutdownRefusalKind,
    UiFailure,
    UiProgress,
    is_package_review_state_valid,
)
from paperworkshell.ui.checklist import ChecklistEditorDialog
from paperworkshell.ui.compression_review import CompressionReviewDialog
from paperworkshell.ui.constraints import ConstraintEditorDialog
from paperworkshell.ui.diagnostic_bundle import DiagnosticBundleDialog, session_bundle_service
from paperworkshell.ui.keyboard_help import KeyboardHelpDialog
from paperworkshell.ui.localization_layout import adapt_localized_content
from paperworkshell.ui.models import (
    OrganizerPageListModel,
    PreviewState,
    QualityFindingListModel,
)
from paperworkshell.ui.ocr_languages import OcrLanguageDialog
from paperworkshell.ui.overlay_canvas import paint_core_overlay_preview
from paperworkshell.ui.overlay_tools import (
    CoreOverlayToolsDialog,
    safe_core_overlay_failure,
)
from paperworkshell.ui.package_export import PackageExportDialog
from paperworkshell.ui.page_delegate import OrganizerPageDelegate
from paperworkshell.ui.post_export import PostExportDeletionDialog
from paperworkshell.ui.presentation import (
    ElidingTextLabel,
    FocusVisibleTabBar,
    WrappingActionLayout,
    adapt_dialog_button_box,
    install_presentation_policy,
    set_static_busy,
)
from paperworkshell.ui.redaction_export import RedactionExportDialog
from paperworkshell.ui.redaction_tools import RedactionToolsDialog, safe_redaction_failure
from paperworkshell.ui.removal_export_review import RemovalExportReviewDialog
from paperworkshell.ui.scan_review import ScanReviewDialog
from paperworkshell.ui.signature_images import (
    SignatureImageDeleteDialog,
    SignatureImagePlacementDialog,
)
from paperworkshell.ui.trace_presentation import install_trace_presentation
from paperworkshell.ui.trace_recipe import TraceRecipePane
from paperworkshell.ui.trace_script import export_trace_powershell
from paperworkshell.ui.trace_workbench import (
    TraceCommand,
    TraceCommandPalette,
    TraceDocumentCanvas,
    TracePageRailDelegate,
    TraceWorkbenchSplit,
)

_AUTOSAVE_DEBOUNCE_MS: Final = 400
_THUMBNAIL_WIDTH: Final = ORGANIZER_PREVIEW_WIDTH_PIXELS
_THUMBNAIL_HEIGHT: Final = ORGANIZER_PREVIEW_HEIGHT_PIXELS
_PREVIEW_REFRESH_DEBOUNCE_MS: Final = 75
_SCAN_REVIEW_PREVIEW_WIDTH: Final = 600
_SCAN_REVIEW_PREVIEW_HEIGHT: Final = 800
_MAX_OPERATION_ID: Final = 2_147_483_647
_POST_EXPORT_FAILURE_TEXT: Final = {
    **{
        code.value: PostExportDeletionError(code, "post_export.ui").safe_message
        for code in PostExportDeletionErrorCode
    },
    **{code.value: TextCompanionError(code).safe_message for code in TextCompanionErrorCode},
    **{
        code.value: AssistedUseError(code, "post_export.ui").safe_message
        for code in AssistedUseErrorCode
    },
}
_FOCUS_VISIBLE_TEXT_STYLE: Final = """
QLabel {
    border: 2px solid transparent;
    padding: 2px;
}
QLabel:focus {
    border-color: palette(highlight);
}
"""
_FOCUS_VISIBLE_WORKFLOW_STYLE: Final = """
QPushButton {
    min-width: 32px;
    min-height: 32px;
}
QAbstractItemView {
    border: 2px solid palette(mid);
}
QScrollArea {
    border: 2px solid transparent;
}
QAbstractItemView:focus, QScrollArea[pwsKeyboardFocus="true"], QPushButton:focus {
    border: 2px dashed palette(highlight);
}
"""


class _LongOperationKind(StrEnum):
    IMPORT = mark("Import")
    PREVIEW = mark("Preview")
    QUALITY = mark("Page quality check")
    SIGNATURE_IMAGE = mark("Signature image")
    PDF_EXPORT = mark("PDF export")
    REMOVAL_REVIEW = mark("Removal review")
    REMOVAL_EXPORT = mark("Removal export")
    PACKAGE_REVIEW = mark("Package inspection")
    TRACE_RECIPE = mark("Recipe")


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
class _TraceCanvasTarget:
    case_id: UUID
    page_id: UUID
    plan: str
    source: bool


@dataclass(frozen=True, slots=True)
class _TraceCanvasPresentation:
    target: _TraceCanvasTarget
    image: QPixmap
    rendered_marks: int = 0
    approximate_text: bool = False


@dataclass(frozen=True, slots=True)
class _FirstPngPreviewTarget:
    case_id: UUID
    revision: int
    page_id: UUID


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
    checked_noun = tr("case") if checked == 1 else tr("cases")
    review_verb = tr("needs") if needs_review == 1 else tr("need")
    file_noun = tr("file") if removed_files == 1 else tr("files")
    directory_noun = tr("directory") if removed_directories == 1 else tr("directories")
    if result.status is StartupRecoveryStatus.ATTENTION_REQUIRED or additional_needs_review:
        return (
            tr(
                "Startup recovery needs attention. Checked {value1} local {value2}; {value3} {value4} review. Uncertain leftover work data was preserved.",
                value1=checked,
                value2=checked_noun,
                value3=needs_review,
                value4=review_verb,
            ),
            True,
        )
    if recovered or removed_files or removed_directories:
        return (
            tr(
                "Startup recovery complete. Checked {value1} local {value2}; recovered {value3}, removed {value4} leftover work {value5} and {value6} {value7}. Ready. Create a case or open an existing case.",
                value1=checked,
                value2=checked_noun,
                value3=recovered,
                value4=removed_files,
                value5=file_noun,
                value6=removed_directories,
                value7=directory_noun,
            ),
            False,
        )
    return (
        tr(
            "Startup recovery complete. Checked {value1} local {value2}; no recovery action was needed. Ready. Create a case or open an existing case.",
            value1=checked,
            value2=checked_noun,
        ),
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


def _page_rotation_changed(
    prior: OrganizerViewState, current: OrganizerViewState, page_id: UUID | None
) -> bool:
    prior_angle = next(
        (page.rotation_degrees for page in prior.pages if page.page_id == page_id), None
    )
    return prior_angle is not None and any(
        page.page_id == page_id and page.rotation_degrees != prior_angle for page in current.pages
    )


def _core_overlay_apply_result_matches(
    dialog: CoreOverlayToolsDialog,
    result: OrganizerViewState,
    current: OrganizerViewState | None,
) -> bool:
    """Correlate a save result to the exact open draft without trusting its strings."""

    draft = dialog.state
    allowed_revisions = {
        draft.manifest_revision,
        draft.manifest_revision + 1,
    }
    return (
        type(current) is OrganizerViewState
        and current == result
        and result.case_id == draft.case_id
        and result.revision in allowed_revisions
        and (not draft.changed or result.revision == draft.manifest_revision + 1)
        and not result.autosave_pending
        and any(page.page_id == draft.page_id for page in result.pages)
    )


def _redaction_save_result_matches(
    dialog: RedactionToolsDialog,
    result: OrganizerViewState,
    current: OrganizerViewState | None,
) -> bool:
    """Correlate a save result to the exact open draft without trusting its strings."""

    draft = dialog.state
    allowed_revisions = {
        draft.manifest_revision,
        draft.manifest_revision + 1,
    }
    return (
        type(current) is OrganizerViewState
        and current == result
        and result.case_id == draft.case_id
        and result.revision in allowed_revisions
        and (not draft.changed or result.revision == draft.manifest_revision + 1)
        and not result.autosave_pending
        and any(page.page_id == draft.page_id for page in result.pages)
    )


def _checklist_apply_result_matches(
    dialog: ChecklistEditorDialog,
    result: OrganizerViewState,
    current: OrganizerViewState | None,
) -> bool:
    """Correlate one checklist save to the exact detached editor draft."""

    draft = dialog.state
    allowed_revisions = {
        draft.manifest_revision,
        draft.manifest_revision + 1,
    }
    return (
        type(current) is OrganizerViewState
        and current == result
        and result.case_id == draft.case_id
        and result.revision in allowed_revisions
        and (not draft.changed or result.revision == draft.manifest_revision + 1)
        and not result.autosave_pending
    )


def _portal_constraints_apply_result_matches(
    dialog: ConstraintEditorDialog,
    result: OrganizerViewState,
    current: OrganizerViewState | None,
) -> bool:
    """Correlate one constraint save to the exact detached editor draft."""

    draft = dialog.state
    allowed_revisions = {
        draft.manifest_revision,
        draft.manifest_revision + 1,
    }
    return (
        type(current) is OrganizerViewState
        and current == result
        and result.case_id == draft.case_id
        and result.revision in allowed_revisions
        and (not draft.changed or result.revision == draft.manifest_revision + 1)
        and not result.autosave_pending
    )


def _overlay_editor_display_facts(
    page: OrganizerPageView,
) -> tuple[PageCoordinateTransform, bool]:
    """Build exact crop/quarter-turn facts and disclose whether pointer mapping is safe."""

    if type(page) is not OrganizerPageView:
        raise ValueError("page must be an OrganizerPageView")
    crop = page.crop or NormalizedPageBox.full_page()
    pointer_mapping_enabled = True
    if page.scan_review_intent is not None:
        source_crop = page.scan_review_intent.crop
        crop = NormalizedPageBox(
            source_crop.x,
            source_crop.y,
            source_crop.width,
            source_crop.height,
        )
        pointer_mapping_enabled = page.scan_review_intent.deskew_angle_millidegrees_clockwise == 0
    return (
        PageCoordinateTransform(
            manifest_crop=crop,
            manifest_rotation=ClockwiseRotation(page.rotation_degrees),
        ),
        pointer_mapping_enabled,
    )


def _next_generation(current: int) -> int:
    return 1 if current >= _MAX_OPERATION_ID else current + 1


def _organizer_preview_image(
    snapshot: PdfThumbnailSnapshot | ImagePagePreviewSnapshot | ImagePageTransformSnapshot,
    pixels: bytes,
    page: OrganizerPageView | None,
) -> QImage:
    image = QImage(
        pixels,
        snapshot.width_pixels,
        snapshot.height_pixels,
        snapshot.row_stride_bytes,
        QImage.Format.Format_RGBA8888,
    )
    if image.isNull():
        return image
    if isinstance(snapshot, PdfThumbnailSnapshot) and page is not None and page.crop is not None:
        if snapshot.additional_rotation.value != page.rotation_degrees:
            return QImage()
        crop = PageCoordinateTransform(
            manifest_rotation=ClockwiseRotation(page.rotation_degrees)
        ).normalized_box_to_rendered(page.crop, RenderedImageSize(image.width(), image.height()))
        left, top = floor(crop.left), floor(crop.top)
        return image.copy(QRect(left, top, ceil(crop.right) - left, ceil(crop.bottom) - top))
    return image.copy()


class PageOrderDialog(QDialog):
    """Explicit non-drag page ordering workflow with keyboard controls."""

    def __init__(
        self,
        pages: tuple[tuple[UUID, int], ...],
        parent: QWidget | None = None,
        *,
        page_labels: dict[UUID, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Reorder pages"))
        self.setAccessibleName(tr("Change page order dialog"))
        self.setAccessibleDescription(
            tr("Select a page and use Move up or Move down. Dragging is not required.")
        )
        self._page_list = QListWidget()
        self._page_list.setObjectName("pageOrderList")
        self._page_list.setAccessibleName(tr("Proposed page order"))
        self._page_list.setAccessibleDescription(
            tr("Pages in proposed order. Select one, then use the visible move buttons.")
        )
        self._page_list.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        for page_id, position in pages:
            item = QListWidgetItem(
                (page_labels or {}).get(page_id) or tr("Page {value1}", value1=position)
            )
            item.setData(Qt.ItemDataRole.UserRole, page_id)
            self._page_list.addItem(item)
        if self._page_list.count():
            self._page_list.setCurrentRow(0)

        self._move_earlier = QPushButton(tr("Move up"))
        self._move_earlier.setObjectName("orderMoveEarlierButton")
        self._move_earlier.setAccessibleDescription(
            tr("Move the selected page one position earlier.")
        )
        self._move_later = QPushButton(tr("Move down"))
        self._move_later.setObjectName("orderMoveLaterButton")
        self._move_later.setAccessibleDescription(tr("Move the selected page one position later."))
        self._move_earlier.clicked.connect(lambda: self._move(-1))
        self._move_later.clicked.connect(lambda: self._move(1))
        self._page_list.currentRowChanged.connect(self._update_move_actions)
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        adapt_dialog_button_box(button_box)
        button_box.setAccessibleName(tr("Confirm or cancel page order"))
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        move_layout = QHBoxLayout()
        move_layout.addWidget(self._move_earlier)
        move_layout.addWidget(self._move_later)
        layout = QVBoxLayout(self)
        instructions = QLabel(
            tr(
                "Select a page, then choose Move up or Move down. Use Alt+Up and Alt+Down as keyboard shortcuts."
            )
        )
        instructions.setWordWrap(True)
        instructions.setAccessibleName(instructions.text())
        layout.addWidget(instructions)
        layout.addWidget(self._page_list)
        layout.addLayout(move_layout)
        self._move_status = QLabel()
        self._move_status.setObjectName("pageOrderStatus")
        self._move_status.setAccessibleName(tr("Page order change status"))
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
        focused_move = self._move_earlier.hasFocus() or self._move_later.hasFocus()
        row = self._page_list.currentRow()
        target = row + offset
        if row < 0 or not 0 <= target < self._page_list.count():
            QApplication.beep()
            return
        item = self._page_list.takeItem(row)
        self._page_list.insertItem(target, item)
        self._page_list.setCurrentRow(target)
        self._update_move_actions()
        if focused_move:
            self._page_list.setFocus(Qt.FocusReason.OtherFocusReason)
        message = tr(
            "{value1} moved to position {value2} of {value3}.",
            value1=item.text(),
            value2=target + 1,
            value3=self._page_list.count(),
        )
        self._move_status.setText(message)
        self._move_status.setAccessibleName(
            tr("Page order change status. {value1}", value1=message)
        )
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
        phase_observer_enabled: bool = False,
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
        self.setWindowTitle(tr("PaperWorkShell"))
        application = QApplication.instance()
        if isinstance(application, QApplication):
            install_presentation_policy(application)
            current_style = application.styleSheet()
            if _FOCUS_VISIBLE_WORKFLOW_STYLE not in current_style:
                application.setStyleSheet(current_style + "\n" + _FOCUS_VISIBLE_WORKFLOW_STYLE)
            install_trace_presentation(application)
        self.setAccessibleName(tr("PaperWorkShell guided packet builder"))
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
        self._first_png_import_origin: tuple[UUID, int] | None = None
        self._first_png_import_preview: _FirstPngPreviewTarget | None = None
        self._queued_import_preview: _FirstPngPreviewTarget | None = None
        self._rotated_preview_after_save: UUID | None = None
        self._quality_generation = 0
        self._pending_quality_request: tuple[UUID, int] | None = None
        self._active_quality_binding: tuple[UUID, int, int] | None = None
        self._quality_result_focus_requested = False
        self._quality_presentation = _QualityPresentationState.NOT_CHECKED
        self._case_reopen_required = False
        self._case_close_pending = False
        self._controls_enabled = False
        self._closing_requested = False
        self._closing_import_cleanup = False
        self._allow_close = False
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
        self._selected_ocr_language_codes: tuple[str, ...] = ("eng",)
        self._ocr_language_refresh_request_id: int | None = None
        self._ocr_language_saved_message: str | None = None
        self._core_overlay_tools_dialog: CoreOverlayToolsDialog | None = None
        self._core_overlay_tools_start_operation_id: int | None = None
        self._core_overlay_tools_apply_operation_id: int | None = None
        self._core_overlay_tools_cancel_operation_id: int | None = None
        self._redaction_tools_dialog: RedactionToolsDialog | None = None
        self._redaction_tools_start_operation_id: int | None = None
        self._redaction_tools_save_operation_id: int | None = None
        self._redaction_tools_cancel_operation_id: int | None = None
        self._redaction_export_dialog: RedactionExportDialog | None = None
        self._pending_redaction_export_report: RedactionExportReport | None = None
        self._pending_redaction_review: tuple[UUID, int, UUID] | None = None
        self._pending_removal_review: RemovalExportReview | None = None
        self._removal_review_dialog: RemovalExportReviewDialog | None = None
        self._pending_removal_retry = False
        self._checklist_dialog: ChecklistEditorDialog | None = None
        self._checklist_start_operation_id: int | None = None
        self._checklist_apply_operation_id: int | None = None
        self._checklist_cancel_operation_id: int | None = None
        self._portal_constraints_dialog: ConstraintEditorDialog | None = None
        self._portal_constraints_start_operation_id: int | None = None
        self._portal_constraints_apply_operation_id: int | None = None
        self._portal_constraints_cancel_operation_id: int | None = None
        self._package_export_dialog: PackageExportDialog | None = None
        self._package_review_state: PackageReviewState | None = None
        self._package_review_operation_id: int | None = None
        self._pending_package_review = False
        self._package_export_operation_id: int | None = None
        self._package_terminal_result_published = False
        self._package_cancel_operation_id: int | None = None
        self._package_summary_save_operation_id: int | None = None
        self._post_export_review_operation_id: int | None = None
        self._post_export_delete_operation_id: int | None = None
        self._post_export_delete_case_id: UUID | None = None
        self._text_companion_operation_id: int | None = None
        self._post_export_binding: tuple[UUID, int, int] | None = None
        self._post_export_deletion_dialog: PostExportDeletionDialog | None = None
        self._post_export_pending_feedback: tuple[str, bool] | None = None
        self._post_export_closed = False
        self._post_export_private_summary_discarded = False
        self._package_cancellation: OperationCancellation | None = None
        self._package_release_failed = False
        self._compression_dialog: CompressionReviewDialog | None = None
        self._compression_destination: Path | None = None
        self._compression_case_id: UUID | None = None
        self._compression_signed = False
        self._compression_human = False
        self._compression_phase: str | None = None
        self._compression_review_state: CompressionReviewState | None = None
        self._compression_preview_request: tuple[UUID, str, int] | None = None
        self._compression_pair_review_id: UUID | None = None
        self._signature_image_source_path: Path | None = None
        self._signature_image_page_id: UUID | None = None
        self._signature_image_prepare_operation_id: int | None = None
        self._signature_image_place_operation_id: int | None = None
        self._signature_image_cancel_operation_id: int | None = None
        self._signature_image_list_request_id: int | None = None
        self._signature_image_delete_operation_id: int | None = None
        self._signature_image_dialog: SignatureImagePlacementDialog | None = None
        self._signature_image_delete_dialog: SignatureImageDeleteDialog | None = None
        self._signature_image_retry_after_cancel = False
        self._signature_image_committed = False
        self._keyboard_help_dialog: KeyboardHelpDialog | None = None
        self._keyboard_help_return_focus: QWidget | None = None
        self._diagnostic_bundle_dialog: DiagnosticBundleDialog | None = None
        self._diagnostic_bundle_return_focus: QWidget | None = None
        self._diagnostic_bundle_service = session_bundle_service(selected_policy)
        self._diagnostic_bundle_shutdown_requested = False

        self._trace_last_selection: tuple[UUID, UUID] | None = None
        self._trace_canvas_timer = QTimer(self)
        self._trace_canvas_timer.setSingleShot(True)
        self._trace_canvas_timer.setInterval(160)
        self._trace_canvas_timer.timeout.connect(self._request_trace_canvas)
        self._trace_canvas_request: tuple[int, _TraceCanvasTarget, OperationCancellation] | None = (
            None
        )
        self._trace_canvas_image: _TraceCanvasPresentation | None = None
        self._trace_canvas_failed: _TraceCanvasTarget | None = None
        self._trace_ocr_language_set_id: str | None = None
        self._trace_evidence: TraceExportEvidence | None = None
        self._trace_saved_selection: TraceSavedExportSelection | None = None
        self._trace_output_status: TraceOutputStatus | None = None
        self._trace_output_request: (
            tuple[int, OperationCancellation, bool, TraceExportEvidence | TraceSavedExportSelection]
            | None
        ) = None
        self._preview_refresh_timer = QTimer(self)
        self._preview_refresh_timer.setSingleShot(True)
        self._preview_refresh_timer.timeout.connect(self._refresh_visible_previews)

        self._focus_reveal_timer = QTimer(self)
        self._focus_reveal_timer.setSingleShot(True)
        self._focus_reveal_timer.setInterval(0)
        self._focus_reveal_timer.timeout.connect(self._reveal_focused_control)

        self._stack = QStackedWidget()
        self._stack.setObjectName("workflowStack")
        self._status_surface = self._build_status_surface()
        adapt_localized_content(self._status_surface)
        self._announcer = ScreenReaderAnnouncer(self._status_label)
        self._trace_app_bar = self._build_trace_app_bar()
        self._welcome_page = self._build_welcome_page()
        self._organizer_page = self._build_organizer_page()
        self._stack.addWidget(self._welcome_page)
        self._stack.addWidget(self._organizer_page)
        central = QWidget()
        central.setObjectName("applicationSurface")
        central.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        central_layout.addWidget(self._trace_app_bar)
        central_layout.addWidget(self._stack, 1)
        self._application_scroll = QScrollArea()
        self._application_scroll.setObjectName("applicationScrollArea")
        self._application_scroll.setAccessibleName(
            tr("PaperWorkShell workflow and operation status")
        )
        self._application_scroll.setWidgetResizable(True)
        self._application_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._application_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._application_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._application_scroll.setWidget(central)
        adapt_localized_content(self._application_scroll)
        shell = QWidget()
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.addWidget(self._application_scroll, 1)
        shell_layout.addWidget(self._status_surface)
        self.setCentralWidget(shell)
        file_menu = self.menuBar().addMenu(tr("&File"))
        self._package_export_action = QAction(tr("Export &checklist package…"), self)
        self._package_export_action.setObjectName("packageExportMenuAction")
        self._package_export_action.setShortcut(QKeySequence("Ctrl+E"))
        self._package_export_action.setStatusTip(
            tr("Review and export the current package. Ctrl+E.")
        )
        self._package_export_action.triggered.connect(self._package_export_button.click)
        file_menu.addAction(self._package_export_action)
        self._pages_pdf_export_action = QAction(tr("Export &PDF…"), self)
        self._pages_pdf_export_action.setObjectName("pagesPdfExportMenuAction")
        self._pages_pdf_export_action.setShortcut(QKeySequence("Ctrl+Shift+E"))
        self._pages_pdf_export_action.setStatusTip(
            tr("Export the saved pages as PDF. Ctrl+Shift+E.")
        )
        self._pages_pdf_export_action.triggered.connect(self._pages_pdf_export_button.click)
        file_menu.addAction(self._pages_pdf_export_action)
        self._removal_export_action = QAction(tr("Export removal &derivative…"), self)
        self._removal_export_action.setObjectName("removalExportMenuAction")
        self._removal_export_action.setShortcut(QKeySequence("Ctrl+Alt+E"))
        self._removal_export_action.setStatusTip(
            tr("Review saved marks and export a verified removal derivative. Ctrl+Alt+E.")
        )
        self._removal_export_action.triggered.connect(self._removal_export_button.click)
        file_menu.addAction(self._removal_export_action)
        help_menu = self.menuBar().addMenu(tr("&Help"))
        self._keyboard_help_action = QAction(tr("&Keyboard guide"), self)
        self._keyboard_help_action.setObjectName("keyboardHelpAction")
        self._keyboard_help_action.setShortcut(QKeySequence("F1"))
        self._keyboard_help_action.setStatusTip(tr("Keyboard shortcuts and navigation. F1."))
        self._keyboard_help_action.triggered.connect(self._show_keyboard_help)
        help_menu.addAction(self._keyboard_help_action)
        self._diagnostic_bundle_action = QAction(tr("Create &support bundle…"), self)
        self._diagnostic_bundle_action.setObjectName("diagnosticBundleAction")
        self._diagnostic_bundle_action.setStatusTip(
            tr("Review exact sanitized support files before choosing a local save location.")
        )
        self._diagnostic_bundle_action.triggered.connect(self._show_diagnostic_bundle)
        help_menu.addAction(self._diagnostic_bundle_action)
        if phase_observer_enabled:
            arm_phase = QAction("Arm synthetic PNG phase observation", self)
            arm_phase.setObjectName("armSyntheticPngPhaseObservation")
            arm_phase.triggered.connect(self._toggle_phase_observation)
            help_menu.addAction(arm_phase)
            save_phase = QAction("Save synthetic PNG phase receipt…", self)
            save_phase.setObjectName("saveSyntheticPngPhaseReceipt")
            save_phase.triggered.connect(self._save_phase_observation)
            help_menu.addAction(save_phase)
        self._build_trace_commands()
        self._build_shortcuts()
        self._connect_backend()

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(self._request_autosave_flush)
        self._set_controls_enabled(False)
        self._show_workflow_page(self._welcome_page)
        application = QApplication.instance()
        if isinstance(application, QApplication):
            application.focusChanged.connect(self._queue_focus_reveal)
        self._assisted_session = AssistedSessionController(self)
        available = self.screen().availableGeometry()
        self.resize(
            min(1270, max(1, available.width() - 48)),
            min(820, max(1, available.height() - 48)),
        )

    def _show_workflow_page(self, page: QWidget) -> None:
        """Keep the inactive workflow out of width-dependent layout sizing.

        Removing a page from the stack layout hides it while preserving its
        QObject parent, controls and tab links for the next case transition.
        """
        for inactive in (self._welcome_page, self._organizer_page):
            if inactive is not page and self._stack.indexOf(inactive) >= 0:
                self._stack.removeWidget(inactive)
        if self._stack.indexOf(page) < 0:
            self._stack.addWidget(page)
        self._stack.setCurrentWidget(page)

    @Slot(QWidget, QWidget)
    def _queue_focus_reveal(self, previous: QWidget | None, current: QWidget | None) -> None:
        """Coalesce focus changes until the current widget layout has settled."""
        # QScrollArea's native frame style omits State_HasFocus, so its :focus
        # selector cannot provide a cue for the deliberately focusable container.
        for widget, focused in ((previous, False), (current, True)):
            if isinstance(widget, QScrollArea) and self._owns_ui_widget(widget):
                widget.setProperty("pwsKeyboardFocus", focused)
                widget.style().unpolish(widget)
                widget.style().polish(widget)
                widget.update()
        if current is not None and self._owns_ui_widget(current):
            self._focus_reveal_timer.start()
        else:
            self._focus_reveal_timer.stop()

    @Slot()
    def _reveal_focused_control(self) -> None:
        """Keep keyboard and recovery focus visible inside owned scroll areas."""
        if self._closing_requested:
            return
        focused = QApplication.focusWidget()
        if not self._focus_widget_is_available(focused) or focused is None:
            return
        if not self._owns_ui_widget(focused):
            return
        owner = focused.window()
        ancestor = focused.parentWidget()
        # Do not scroll the underlying editor when a combo popup owns focus.
        while ancestor is not None and ancestor.window() is owner:
            if isinstance(ancestor, QScrollArea):
                content = ancestor.widget()
                if content is not None and (focused is content or content.isAncestorOf(focused)):
                    self._reveal_control_in_scroll_area(ancestor, focused)
            ancestor = ancestor.parentWidget()

    @Slot(QModelIndex, QModelIndex)
    def _queue_current_row_reveal(self, _current: QModelIndex, _previous: QModelIndex) -> None:
        """Follow keyboard selection and a newly populated focused item view."""
        focused = QApplication.focusWidget()
        if isinstance(focused, QAbstractItemView) and self._owns_ui_widget(focused):
            self._focus_reveal_timer.start()

    def _reveal_control_in_scroll_area(self, area: QScrollArea, widget: QWidget) -> None:
        """Reveal the current row instead of centering an oversized item view."""
        if isinstance(widget, QAbstractItemView):
            current = widget.currentIndex()
            content = area.widget()
            if current.isValid() and content is not None and content.isAncestorOf(widget):
                widget.scrollTo(current, QAbstractItemView.ScrollHint.EnsureVisible)
                row = widget.visualRect(current)
                if not row.isEmpty():
                    center = widget.viewport().mapTo(content, row.center())
                    area.ensureVisible(
                        center.x(),
                        center.y(),
                        (row.width() + 1) // 2 + 8,
                        (row.height() + 1) // 2 + 8,
                    )
                    return
        area.ensureWidgetVisible(widget, 8, 8)

    def _owns_ui_widget(self, widget: QWidget) -> bool:
        """Include owned top-level dialogs without treating unrelated windows as ours."""
        candidate: QWidget | None = widget
        while candidate is not None:
            if candidate is self:
                return True
            candidate = candidate.parentWidget()
        return False

    @property
    def resource_policy(self) -> ResourcePolicy:
        """Return the immutable policy actually enforcing this window's preview work."""

        return self._resource_policy

    def _build_status_surface(self) -> QGroupBox:
        status_group = QGroupBox(tr("Operation status"))
        status_group.setObjectName("operationStatusGroup")
        status_group.setAccessibleName(tr("Operation status"))
        status_group.setAccessibleDescription(
            tr("Current application status, progress, and any action error.")
        )
        status_layout = QVBoxLayout(status_group)
        self._status_label = QLabel(tr("PaperWorkShell is starting."))
        self._status_label.setObjectName("statusLabel")
        self._status_label.setAccessibleName(tr("Current operation status"))
        self._status_label.setAccessibleDescription(
            tr("Current PaperWorkShell status shown on every workflow page.")
        )
        self._status_label.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._status_label.setStyleSheet(_FOCUS_VISIBLE_TEXT_STYLE)
        self._status_label.setWordWrap(True)
        self._status_label.setTextFormat(Qt.TextFormat.PlainText)
        self._import_file_label = ElidingTextLabel()
        self._import_file_label.setObjectName("importFileTarget")
        self._import_file_label.setTextFormat(Qt.TextFormat.PlainText)
        self._import_file_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._import_file_label.hide()
        self._error_label = QLabel()
        self._error_label.setObjectName("errorLabel")
        self._error_label.setProperty("traceRole", "error")
        self._error_label.setAccessibleName(tr("Action error"))
        self._error_label.setAccessibleDescription(
            tr("The latest safe error code and recovery message.")
        )
        self._error_label.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._error_label.setStyleSheet(_FOCUS_VISIBLE_TEXT_STYLE)
        self._error_label.setWordWrap(True)
        self._error_label.setVisible(False)
        self._progress = QProgressBar()
        self._progress.setObjectName("operationProgress")
        self._progress.setAccessibleName(tr("Document operation progress"))
        self._progress.setTextVisible(True)
        self._progress.setVisible(False)
        status_actions = WrappingActionLayout()
        self._cancel_button = QPushButton(tr("Cancel current operation"))
        self._cancel_button.setObjectName("cancelButton")
        self._cancel_button.setAccessibleDescription(
            tr(
                "Request cancellation of the running document operation, including import, preview, or page-quality review. Escape."
            )
        )
        self._cancel_button.clicked.connect(self._cancel_active_operation)
        self._cancel_button.setVisible(False)
        self._retry_preview_button = QPushButton(tr("Retry selected preview"))
        self._retry_preview_button.setObjectName("retryPreviewButton")
        self._retry_preview_button.setAccessibleDescription(
            tr("Retry thumbnail rendering for the selected failed page. Ctrl+Shift+R.")
        )
        self._retry_preview_button.clicked.connect(self._retry_failed_preview)
        self._retry_preview_button.setVisible(False)
        self._retry_save_button = QPushButton(tr("Retry saving changes"))
        self._retry_save_button.setObjectName("retrySaveButton")
        self._retry_save_button.setAccessibleDescription(
            tr("Retry the retained organizer autosave after a recoverable failure.")
        )
        self._retry_save_button.clicked.connect(self._request_retry_save)
        self._retry_save_button.setVisible(False)
        self._retry_close_button = QPushButton(tr("Retry closing PaperWorkShell"))
        self._retry_close_button.setObjectName("retryCloseButton")
        self._retry_close_button.setAccessibleDescription(
            tr("Retry secure shutdown cleanup after PaperWorkShell remains open.")
        )
        self._retry_close_button.clicked.connect(self.request_shutdown)
        self._retry_close_button.setVisible(False)
        status_actions.addWidget(self._cancel_button)
        status_actions.addWidget(self._retry_preview_button)
        status_actions.addWidget(self._retry_save_button)
        status_actions.addWidget(self._retry_close_button)
        self._saved_label = QLabel(tr("No case open."))
        self._saved_label.setObjectName("caseSavedState")
        self._saved_label.setProperty("traceRole", "muted")
        self._saved_label.setTextFormat(Qt.TextFormat.PlainText)
        self._saved_label.setWordWrap(True)
        self._last_result_label = QLabel()
        self._last_result_label.setObjectName("lastResultLabel")
        self._last_result_label.setTextFormat(Qt.TextFormat.PlainText)
        self._last_result_label.setWordWrap(True)
        self._last_result_label.hide()
        self._trace_destination_label = QLabel()
        self._trace_destination_label.setObjectName("traceExportDestination")
        self._trace_destination_label.setTextFormat(Qt.TextFormat.PlainText)
        self._trace_destination_label.setWordWrap(True)
        self._trace_destination_label.hide()
        status_layout.addWidget(self._import_file_label)
        status_layout.addWidget(self._status_label)
        status_layout.addWidget(self._trace_destination_label)
        status_layout.addWidget(self._last_result_label)
        self._last_export_path: Path | None = None
        self._pdf_export_destination: Path | None = None
        self._pdf_export_saved_id: int | None = None
        self._last_export_summary = ""
        self._open_pdf_button = QPushButton(tr("Open PDF"))
        self._open_pdf_button.setObjectName("openExportedPdfButton")
        self._open_pdf_button.setAccessibleDescription(
            tr("Open the last verified PDF in your normal PDF viewer.")
        )
        self._open_pdf_button.clicked.connect(lambda: self._open_last_export(False))
        self._open_export_folder_button = QPushButton(tr("Open folder"))
        self._open_export_folder_button.setObjectName("openExportFolderButton")
        self._open_export_folder_button.setAccessibleDescription(
            tr("Open the folder containing the last verified PDF.")
        )
        self._open_export_folder_button.clicked.connect(lambda: self._open_last_export(True))
        self._open_pdf_button.hide()
        self._open_export_folder_button.hide()
        status_actions.addWidget(self._open_pdf_button)
        status_actions.addWidget(self._open_export_folder_button)
        status_layout.addWidget(self._error_label)
        status_layout.addWidget(self._progress)
        status_layout.addLayout(status_actions)
        return status_group

    def _build_trace_app_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("traceApplicationBar")
        bar.setProperty("tracePanel", True)
        frame_layout = QVBoxLayout(bar)
        frame_layout.setContentsMargins(12, 6, 12, 6)
        frame_layout.setSpacing(4)
        layout = WrappingActionLayout()
        brand = QLabel(tr("PaperWorkShell"))
        brand.setProperty("traceRole", "brand")
        font = brand.font()
        font.setPointSizeF(font.pointSizeF() * 1.35)
        brand.setFont(font)
        frame_layout.addWidget(brand)
        frame_layout.addLayout(layout)
        self._command_button = self._action_button(
            "Commands",
            "traceCommandsButton",
            "Find an action for the current case or page. Ctrl+K.",
        )
        self._command_button.clicked.connect(self._show_command_palette)
        layout.addWidget(self._command_button)
        self._ocr_language_button = self._action_button(
            "OCR languages…",
            "ocrLanguageSettingsButton",
            "Review installed and verified offline OCR languages. Shortcut Ctrl+Shift+L. No language data is downloaded.",
        )
        self._ocr_language_button.setAccessibleName(tr("OCR languages"))
        self._ocr_language_button.clicked.connect(self._open_ocr_languages)
        layout.addWidget(self._ocr_language_button)
        self._settings_button = QPushButton(tr("Settings"))
        self._settings_button.setObjectName("traceSettingsButton")
        layout.addWidget(self._settings_button)
        return bar

    def _build_welcome_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("caseChooserPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        heading = QLabel(tr("Choose a case"))
        heading.setObjectName("caseChooserHeading")
        heading.setAccessibleName(tr("Choose a case"))
        heading_font = heading.font()
        heading_font.setPointSizeF(heading_font.pointSizeF() * 1.3)
        heading_font.setBold(True)
        heading.setFont(heading_font)
        heading.setWordWrap(True)
        guidance = QLabel(
            tr(
                "A case keeps your documents and edits together. Give each case a name so you can recognize it later, or open an existing case."
            )
        )
        guidance.setWordWrap(True)
        guidance.setAccessibleName(tr("Case chooser guidance"))
        self._case_list = QListWidget()
        self._case_list.setObjectName("caseList")
        self._case_list.setAccessibleName(tr("Available cases"))
        self._case_list.setAccessibleDescription(
            tr("Select a case by name, then choose Open case.")
        )
        self._case_list.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self._case_list.selectionModel().currentChanged.connect(self._queue_current_row_reveal)
        self._case_list.itemDoubleClicked.connect(lambda _item: self._open_selected_case())

        self._create_case_button = QPushButton(tr("New case…"))
        self._create_case_button.setObjectName("createCaseButton")
        self._create_case_button.setProperty("tracePrimary", True)
        self._create_case_button.setAccessibleDescription(
            tr(
                "Enter a case name, then create the case. Cancel leaves your cases unchanged. Shortcut Ctrl+N."
            )
        )
        self._create_case_button.clicked.connect(self._request_create_case)
        self._open_case_button = QPushButton(tr("Open case"))
        self._open_case_button.setObjectName("openCaseButton")
        self._case_list.setAlternatingRowColors(True)
        self._open_case_button.setAccessibleDescription(
            tr("Open the selected case. Shortcut Ctrl+O.")
        )
        self._open_case_button.clicked.connect(self._open_selected_case)
        self._refresh_cases_button = QPushButton(tr("Refresh case list"))
        self._refresh_cases_button.setObjectName("refreshCasesButton")
        self._refresh_cases_button.clicked.connect(self._commands.list_cases_requested.emit)
        chooser_actions = WrappingActionLayout()
        chooser_actions.addWidget(self._create_case_button)
        chooser_actions.addWidget(self._open_case_button)
        chooser_actions.addWidget(self._refresh_cases_button)
        self._assisted_create_button = QPushButton(tr("Start assisted case"))
        self._assisted_create_button.setObjectName("assistedCreateButton")
        self._assisted_create_button.setAccessibleDescription(
            tr("Review consent, expiry, and a local passphrase before importing documents.")
        )
        self._assisted_create_button.clicked.connect(lambda: self._assisted_session.start())
        chooser_actions.addWidget(self._assisted_create_button)

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
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        header = WrappingActionLayout()
        self._case_heading = QLabel(tr("Page organizer"))
        self._case_heading.setObjectName("organizerHeading")
        self._case_heading.setAccessibleName(tr("Page organizer"))
        heading_font = self._case_heading.font()
        heading_font.setPointSizeF(heading_font.pointSizeF() * 1.3)
        heading_font.setBold(True)
        self._case_heading.setFont(heading_font)
        self._case_heading.setWordWrap(True)
        self._case_heading.setTextFormat(Qt.TextFormat.PlainText)
        self._import_button = QPushButton(tr("Add file…"))
        self._import_button.setObjectName("importButton")
        self._import_button.setAccessibleDescription(
            tr("Choose a PDF, JPEG, or PNG to copy into this case. Shortcut Ctrl+I.")
        )
        self._import_button.clicked.connect(self._choose_import)
        self._close_case_button = QPushButton(tr("Close case"))
        self._close_case_button.setObjectName("closeCaseButton")
        self._close_case_button.setAccessibleDescription(
            tr("Save queued organizer changes and return to the case chooser.")
        )
        self._close_case_button.clicked.connect(self._request_close_case)
        self._assisted_lock_button = QPushButton(tr("Lock assisted case"))
        self._assisted_lock_button.setObjectName("assistedLockButton")
        self._assisted_lock_button.setAccessibleDescription(
            tr("Hide the assisted case and require its local passphrase to continue.")
        )
        self._assisted_lock_button.hide()
        self._assisted_lock_button.clicked.connect(lambda: self._assisted_session.cover())
        self._rename_case_button = QPushButton(tr("Rename case…"))
        self._rename_case_button.setObjectName("renameCaseButton")
        self._rename_case_button.setAccessibleDescription(
            tr(
                "Change this case's display name. Its documents and saved edits remain attached to the same case."
            )
        )
        self._rename_case_button.clicked.connect(self._request_rename_case)
        self._case_heading.setMinimumWidth(0)
        layout.addWidget(self._case_heading)
        layout.addWidget(self._saved_label)
        layout.addLayout(header)

        organizer_group = QFrame()
        organizer_group.setProperty("tracePanel", True)
        organizer_group.setAccessibleName(tr("Pages"))
        organizer_group.setObjectName("activePagesGroup")
        organizer_group.setAccessibleDescription(
            tr("Keyboard-selectable active pages. Reordering never requires drag-and-drop.")
        )
        self._organizer_guidance = QLabel()
        self._organizer_guidance.setObjectName("organizerGuidance")
        self._organizer_guidance.setAccessibleName(tr("Page organizer guidance"))
        self._organizer_guidance.setAccessibleDescription(
            tr("Current page organizer state and the next available action.")
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
        self._page_view.setAccessibleName(tr("Pages"))
        self._page_view.setAccessibleDescription(
            tr("Select a page, then use the visible organizer actions or their shortcuts.")
        )
        self._page_view.setModel(self._model)
        self._page_view.setViewMode(QListView.ViewMode.ListMode)
        self._trace_rail_delegate = TracePageRailDelegate(self._page_view)
        self._page_view.setItemDelegate(self._trace_rail_delegate)
        self._page_view.setUniformItemSizes(True)
        self._page_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._page_view.setResizeMode(QListView.ResizeMode.Adjust)
        self._page_view.setMovement(QListView.Movement.Static)
        self._page_view.setDragEnabled(False)
        self._page_view.setAcceptDrops(False)
        self._page_view.setDropIndicatorShown(False)
        self._page_view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._page_view.setWordWrap(True)
        self._page_view.setSpacing(4)
        self._page_view.setIconSize(
            QImage(_THUMBNAIL_WIDTH, _THUMBNAIL_HEIGHT, QImage.Format.Format_RGBA8888).size()
        )
        self._page_view.selectionModel().selectionChanged.connect(self._selection_changed)
        self._page_view.selectionModel().currentChanged.connect(self._queue_current_row_reveal)
        self._page_view.selectionModel().currentChanged.connect(
            lambda _current, _previous: self._update_page_actions()
        )
        self._page_view.verticalScrollBar().valueChanged.connect(self._schedule_preview_refresh)
        self._page_view.horizontalScrollBar().valueChanged.connect(self._schedule_preview_refresh)
        self._page_view.viewport().installEventFilter(self)
        self._trace_source_tree = QTreeWidget()
        self._trace_source_tree.setObjectName("traceSourceGroups")
        self._trace_source_tree.setAccessibleName(tr("Source documents"))
        self._trace_source_tree.setHeaderHidden(True)
        self._trace_source_tree.setColumnCount(1)
        self._trace_source_tree.setUniformRowHeights(True)
        self._trace_source_tree.itemActivated.connect(self._select_source_tree_page)
        self._trace_sources_identity: object = None
        self._trace_left_tabs = QTabWidget()
        FocusVisibleTabBar.install_on(self._trace_left_tabs)
        self._trace_left_tabs.setAccessibleName(tr("Pages and source documents"))
        self._trace_left_tabs.setUsesScrollButtons(False)
        self._trace_left_tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self._trace_left_tabs.addTab(self._page_view, tr("Pages"))
        self._trace_left_tabs.addTab(self._trace_source_tree, tr("Sources"))
        self._trace_left_tabs.currentChanged.connect(self._schedule_preview_refresh)
        organizer_layout.addWidget(self._trace_left_tabs, 1)
        self._selection_summary = QLabel(tr("Select a page to arrange or edit it."))
        self._selection_summary.setObjectName("selectedPageSummary")
        self._selection_summary.setAccessibleName(tr("Selected page"))
        self._selection_summary.setTextFormat(Qt.TextFormat.PlainText)
        self._selection_summary.setWordWrap(True)
        self._selection_summary.setProperty("traceRole", "muted")

        self._trace_workflow_actions = WrappingActionLayout()
        layout.addLayout(self._trace_workflow_actions)
        self._move_earlier_button = self._action_button(
            tr("Move up"),
            "moveEarlierButton",
            tr("Move the selected page one position earlier. Alt+Up."),
        )
        self._move_later_button = self._action_button(
            tr("Move down"),
            "moveLaterButton",
            tr("Move the selected page one position later. Alt+Down."),
        )
        self._rotate_button = self._action_button(
            tr("Rotate right 90°"),
            "rotateButton",
            tr("Rotate the selected page clockwise by 90 degrees. Ctrl+R."),
        )
        self._remove_button = self._action_button(
            tr("Remove page"),
            "removeButton",
            tr("Remove the selected page from the active packet. Delete."),
        )
        self._scan_review_button = self._action_button(
            tr("Improve scan…"),
            "scanReviewButton",
            tr(
                "Compare the unchanged original with provisional scan adjustments. Nothing changes until Apply adjustments is chosen. Ctrl+Shift+A."
            ),
        )
        self._change_order_button = self._action_button(
            tr("Reorder pages…"),
            "changeOrderButton",
            tr("Open the explicit keyboard page-order dialog. Ctrl+Shift+O."),
        )
        self._core_overlay_tools_button = self._action_button(
            tr("Add text or marks…"),
            "editFormMarksButton",
            tr(
                "Add text, a checkmark, or other visual marks to the selected page. Select, move, or resize marks with the mouse or keyboard. Ctrl+Shift+F."
            ),
        )
        self._core_overlay_tools_button.setAccessibleName(tr("Add text or marks"))
        self._redaction_tools_button = self._action_button(
            tr("Permanently remove information…"),
            "redactionToolsButton",
            (
                tr(
                    "Mark areas on the selected page. Save marks only; use Export removal derivative for a separately checked output. Ctrl+Shift+P."
                )
                if runtime_release_qualified()
                else tr(
                    "Mark areas on the selected page for future permanent removal. Save marks only; removal and verification remain disabled. Ctrl+Shift+P."
                )
            ),
        )
        self._redaction_tools_button.setAccessibleName(tr("Permanently remove information"))
        self._place_signature_image_button = self._action_button(
            tr("Place signature image…"),
            "placeSignatureImageButton",
            tr(
                "Choose, preview, and place a local JPEG or PNG as a visual image. {value1} Ctrl+Shift+S.",
                value1=tr(SIGNATURE_IMAGE_DISCLAIMER),
            ),
        )
        self._place_signature_image_button.setAccessibleName(tr("Place signature image"))
        self._remove_signature_image_button = self._action_button(
            tr("Remove signature image…"),
            "removeSignatureImageButton",
            tr(
                "Select a visual signature-image placement and explicitly choose whether to keep its case copy or remove it only when unused. Ctrl+Shift+D."
            ),
        )
        self._remove_signature_image_button.setAccessibleName(tr("Remove signature image"))
        self._checklist_button = self._action_button(
            tr("Document checklist…"),
            "packetChecklistButton",
            tr(
                "Open the keyboard-complete packet checklist. Adding or dragging pages creates an unconfirmed document; only a separate Confirm or Accept action can make a category Ready. Ctrl+Shift+C."
            ),
        )
        self._checklist_button.setAccessibleName(tr("Document checklist"))
        self._portal_constraints_button = self._action_button(
            tr("Review package constraints…"),
            "portalConstraintsButton",
            tr(
                "Review allowed package formats, encryption policy, optional limits, and safe requested names. These local checks do not guarantee portal acceptance."
            ),
        )
        self._portal_constraints_button.setAccessibleName(tr("Review package constraints"))
        self._package_export_button: QPushButton = self._action_button(
            tr("Export checklist package…"),
            "packageExportButton",
            tr(
                "Review planned files, concrete checks, warnings, and human confirmations, then export the package to a selected destination. Ctrl+E."
            ),
        )
        self._package_export_button.clicked.connect(self._request_package_export)
        self._pages_pdf_export_button: QPushButton = self._action_button(
            tr("Export PDF…"),
            "pagesPdfExportButton",
            tr(
                "Save the current PDF, JPEG, and PNG pages in order as one verified PDF. Choose a new filename. Ctrl+Shift+E."
            ),
        )
        self._pages_pdf_export_button.clicked.connect(self._choose_pages_pdf_export)
        self._removal_export_button: QPushButton = self._action_button(
            tr("Export removal derivative…"),
            "removalExportButton",
            tr(
                "Review saved removal marks and choose a new PDF file. The supported profile has one active PDF page without overlays or scan cleanup. Required checks and independent release approval cannot be skipped. Ctrl+Alt+E."
            ),
        )
        self._removal_export_button.clicked.connect(self._request_removal_export)
        self._undo_button = self._action_button(
            tr("Undo"),
            "undoButton",
            tr("Undo the last organizer action from this session. Ctrl+Z."),
        )
        self._redo_button = self._action_button(
            tr("Redo"), "redoButton", tr("Redo the next undone organizer action. Ctrl+Y.")
        )
        self._move_earlier_button.clicked.connect(lambda: self._move_selected(-1))
        self._move_later_button.clicked.connect(lambda: self._move_selected(1))
        self._rotate_button.clicked.connect(self._rotate_selected)
        self._remove_button.clicked.connect(self._remove_selected)
        self._scan_review_button.clicked.connect(self._request_scan_review)
        self._change_order_button.clicked.connect(self._change_full_order)
        self._core_overlay_tools_button.clicked.connect(self._request_core_overlay_tools)
        self._redaction_tools_button.clicked.connect(self._request_redaction_tools)
        self._place_signature_image_button.clicked.connect(self._choose_signature_image)
        self._remove_signature_image_button.clicked.connect(
            self._request_signature_image_placements
        )
        self._checklist_button.clicked.connect(self._request_checklist)
        self._portal_constraints_button.clicked.connect(self._request_portal_constraints)
        self._undo_button.clicked.connect(self._request_undo)
        self._redo_button.clicked.connect(self._request_redo)
        header.addWidget(self._import_button)
        self._pages_pdf_export_button.setProperty("tracePrimary", True)
        header.addWidget(self._pages_pdf_export_button)
        header.addWidget(self._assisted_lock_button)
        header.addWidget(self._close_case_button)

        quality_group = QGroupBox(tr("Page quality review"))
        quality_group.setObjectName("qualityReviewGroup")
        quality_group.setAccessibleName(tr("Page quality review"))
        quality_group.setAccessibleDescription(
            tr(
                "Limited automatic review signals for the exact saved case revision. These suggestions never complete a requirement."
            )
        )
        quality_layout = QVBoxLayout(quality_group)
        self._quality_summary = QLabel(
            tr(
                "Not checked for this saved revision. Automatic quality signals are suggestions; review every page yourself."
            )
        )
        self._quality_summary.setObjectName("qualityReviewSummary")
        self._quality_summary.setAccessibleName(
            tr("Page quality review status. {value1}", value1=self._quality_summary.text())
        )
        self._quality_summary.setAccessibleDescription(self._quality_summary.text())
        self._quality_summary.setTextFormat(Qt.TextFormat.PlainText)
        self._quality_summary.setWordWrap(True)
        self._quality_summary.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._quality_summary.setStyleSheet(_FOCUS_VISIBLE_TEXT_STYLE)
        self._quality_findings_view = QListView()
        self._quality_findings_view.setObjectName("qualityFindingsList")
        self._quality_findings_view.setAccessibleName(tr("Page quality findings"))
        self._quality_findings_view.setAccessibleDescription(
            tr(
                "Use the arrow keys to select a review signal. Press Enter or choose Go to selected page to navigate without confirming or completing anything."
            )
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
            tr("Check page quality"),
            "qualityCheckButton",
            tr(
                "Run limited automatic checks for the exact saved revision. Results need human review and never complete a requirement. Ctrl+Shift+Q."
            ),
        )
        self._quality_check_button.clicked.connect(self._request_quality_review)
        self._quality_go_to_page_button = self._action_button(
            tr("Go to selected page"),
            "qualityGoToPageButton",
            tr("Select and focus the organizer page referenced by the current review signal."),
        )
        self._quality_go_to_page_button.clicked.connect(self._go_to_selected_quality_finding)
        quality_actions = WrappingActionLayout()
        quality_actions.addWidget(self._quality_check_button)
        quality_actions.addWidget(self._quality_go_to_page_button)
        quality_layout.addWidget(self._quality_summary)
        quality_layout.addWidget(self._quality_findings_view)
        quality_layout.addLayout(quality_actions)
        self._trace_lens = "planned"
        center = self._build_trace_document()
        self._trace_inspector_tabs = QTabWidget()
        FocusVisibleTabBar.install_on(self._trace_inspector_tabs)
        self._trace_inspector_tabs.setAccessibleName(tr("Recipe and inspection"))
        self._trace_inspector_tabs.setUsesScrollButtons(False)
        self._trace_inspector_tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self._trace_inspector_tabs.addTab(self._build_trace_recipe_pane(), tr("Recipe"))
        self._trace_inspector_tabs.addTab(quality_group, tr("Checks"))
        self._trace_split = TraceWorkbenchSplit(organizer_group, center, self._trace_inspector_tabs)
        layout.addWidget(self._trace_split, 1)
        self._model.dataChanged.connect(self._refresh_trace_document)
        self._page_view.selectionModel().currentChanged.connect(self._refresh_trace_document)
        return content

    def _build_trace_document(self) -> QWidget:
        content = QWidget()
        content.setObjectName("traceDocumentPanel")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        page_navigation = WrappingActionLayout()
        self._trace_previous_button = self._action_button(
            "Previous page",
            "tracePreviousPage",
            "Select the preceding page in the assembled order.",
        )
        self._trace_next_button = self._action_button(
            "Next page", "traceNextPage", "Select the next page in the assembled order."
        )
        self._trace_page_position = QLabel()
        self._trace_page_position.setObjectName("tracePagePosition")
        self._trace_page_position.setTextFormat(Qt.TextFormat.PlainText)
        self._trace_page_position.hide()
        layout.addWidget(self._trace_page_position)
        self._trace_previous_button.clicked.connect(lambda: self._select_trace_adjacent(-1))
        self._trace_next_button.clicked.connect(lambda: self._select_trace_adjacent(1))
        page_navigation.addWidget(self._trace_previous_button)
        page_navigation.addWidget(self._trace_next_button)
        layout.addLayout(page_navigation)

        lens_row = WrappingActionLayout()
        self._trace_lens_buttons: dict[str, QPushButton] = {}
        for key, title in (
            ("source", tr("Source")),
            ("planned", tr("Planned")),
            ("exported", tr("Exported")),
            ("arrange", tr("Arrange")),
        ):
            button = QPushButton(title)
            button.setObjectName(f"traceView-{key}")
            button.setCheckable(True)
            button.setChecked(key == self._trace_lens)
            button.clicked.connect(lambda _checked=False, lens=key: self._set_trace_lens(lens))
            self._trace_lens_buttons[key] = button
            lens_row.addWidget(button)
        layout.addLayout(lens_row)
        self._trace_document = TraceDocumentCanvas()
        self._trace_document_stack = QStackedWidget()
        self._trace_document_stack.addWidget(self._trace_document)
        self._trace_arrangement = QListView()
        self._trace_arrangement.setObjectName("traceArrangement")
        self._trace_arrangement.setAccessibleName(tr("Assembled page order"))
        self._trace_arrangement.setModel(self._model)
        self._trace_arrangement.setSelectionModel(self._page_view.selectionModel())
        self._trace_arrangement.setItemDelegate(OrganizerPageDelegate(self._trace_arrangement))
        self._trace_arrangement.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._trace_arrangement.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._trace_arrangement.setUniformItemSizes(True)
        self._trace_arrangement.setResizeMode(QListView.ResizeMode.Adjust)
        self._trace_arrangement.verticalScrollBar().valueChanged.connect(
            self._schedule_preview_refresh
        )
        self._trace_document_stack.addWidget(self._trace_arrangement)
        layout.addWidget(self._trace_document_stack, 1)
        layout.addWidget(self._selection_summary)

        navigation = WrappingActionLayout()
        self._trace_zoom = QSpinBox()
        self._trace_zoom.setObjectName("traceZoom")
        self._trace_zoom.setAccessibleName(tr("Zoom relative to fit"))
        self._trace_zoom.setRange(100, 300)
        self._trace_zoom.setSingleStep(25)
        self._trace_zoom.setSuffix("%")
        self._trace_zoom.setSpecialValueText(tr("Fit page"))
        self._trace_zoom.valueChanged.connect(self._trace_document.set_zoom)
        navigation.addWidget(self._trace_zoom)
        navigation.addWidget(self._rotate_button)
        self._trace_arrangement_actions = (
            self._move_earlier_button,
            self._move_later_button,
            self._change_order_button,
            self._remove_button,
        )
        for button in self._trace_arrangement_actions:
            navigation.addWidget(button)
            button.hide()
        self._trace_open_pdf_button = self._action_button(
            "Open exported PDF",
            "traceOpenExport",
            "Verify the file identity and open it in your PDF viewer.",
        )
        self._trace_open_pdf_button.clicked.connect(
            lambda: self._request_trace_output_check(open_when_verified=True)
        )
        self._trace_open_pdf_button.hide()
        self._trace_choose_pdf_button = self._action_button(
            tr("Select exported PDF…"),
            "traceChooseExport",
            tr("Choose the saved PDF to verify its exact size and SHA-256."),
        )
        self._trace_choose_pdf_button.clicked.connect(self._choose_trace_export)
        self._trace_choose_pdf_button.hide()
        navigation.addWidget(self._trace_choose_pdf_button)
        navigation.addWidget(self._trace_open_pdf_button)
        self._trace_cancel_check_button = self._action_button(
            tr("Cancel file check"),
            "traceCancelExportCheck",
            tr("Stop verifying the PDF file."),
        )
        self._trace_cancel_check_button.clicked.connect(self._cancel_trace_file_check)
        self._trace_cancel_check_button.hide()
        navigation.addWidget(self._trace_cancel_check_button)
        layout.addLayout(navigation)
        return content

    def _build_trace_recipe_pane(self) -> QWidget:
        self._trace_recipe_pane = TraceRecipePane()
        self._trace_recipe_pane.page_requested.connect(self._select_recipe_page)
        return self._trace_recipe_pane

    def _sync_trace_plan(
        self, state: OrganizerViewState, prior_state: OrganizerViewState | None
    ) -> None:
        if self._trace_evidence is not None and self._trace_evidence.case_id != state.case_id:
            self._trace_evidence = None
        if (
            prior_state is None
            or prior_state.case_id != state.case_id
            or (
                self._trace_evidence is None
                and prior_state.trace_saved_export != state.trace_saved_export
            )
        ):
            self._cancel_trace_output_check()
            self._trace_saved_selection = None
            self._trace_output_status = None
        self._trace_recipe_pane.set_export_evidence(
            self._trace_evidence or state.trace_saved_export
        )
        self._trace_recipe_pane.set_plan(state.trace_plan)
        groups: dict[int, list[OrganizerPageView]] = {}
        for page in state.pages:
            groups.setdefault(page.document_number or 0, []).append(page)
        identity = (
            state.case_id,
            state.trace_plan.sources if state.trace_plan is not None else (),
            tuple(
                (
                    number,
                    tuple(
                        (page.page_id, page.position, page.source_display_name) for page in pages
                    ),
                )
                for number, pages in groups.items()
            ),
        )
        if identity == self._trace_sources_identity:
            return
        self._trace_sources_identity = identity
        self._trace_source_tree.clear()
        sources = state.trace_plan.sources if state.trace_plan is not None else ()
        for source in sources:
            groups.setdefault(source.binding.binding, [])
        names = {source.binding.binding: source.display_name for source in sources}
        counts = {source.binding.binding: source.binding.page_count for source in sources}
        for number, pages in sorted(groups.items()):
            name = names.get(number) or (pages[0].source_display_name if pages else None)
            name = name or tr("Document {value1}", value1=number)
            group = QTreeWidgetItem(
                self._trace_source_tree,
                [
                    tr(
                        "{value1} · {value2} of {value3} in packet",
                        value1=name,
                        value2=len(pages),
                        value3=counts.get(number, len(pages)),
                    )
                ],
            )
            group.setToolTip(0, group.text(0))
            for page in pages:
                item = QTreeWidgetItem(
                    group,
                    [
                        tr(
                            "Source {value1} → page {value2}",
                            value1=page.source_page_number or 1,
                            value2=page.position,
                        )
                    ],
                )
                item.setData(0, Qt.ItemDataRole.UserRole, page.page_id)
        self._trace_source_tree.expandAll()

    def _select_recipe_page(self, page_id: UUID) -> None:
        row = self._model.row_for_page(page_id)
        if row is not None:
            self._restore_page_selection(page_id)
            self._page_view.scrollTo(self._model.index(row, 0))
            self._trace_arrangement.scrollTo(self._model.index(row, 0))

    def _select_source_tree_page(self, item: QTreeWidgetItem, _column: int) -> None:
        page_id = item.data(0, Qt.ItemDataRole.UserRole)
        if type(page_id) is UUID:
            self._restore_page_selection(page_id)
            self._trace_split.select_pane("document")
            self._trace_document.setFocus(Qt.FocusReason.OtherFocusReason)

    def _select_trace_adjacent(self, offset: int) -> None:
        row = self._page_view.currentIndex().row() + offset
        page_id = self._model.page_id_at(row)
        if page_id is not None:
            previous, following = self._trace_previous_button, self._trace_next_button
            activated, reverse = (previous, following) if offset < 0 else (following, previous)
            had_focus = activated.hasFocus()
            self._restore_page_selection(page_id)
            self._page_view.scrollTo(self._model.index(row, 0))
            self._trace_arrangement.scrollTo(self._model.index(row, 0))
            if had_focus and not activated.isEnabled() and reverse.isEnabled():
                reverse.setFocus(Qt.FocusReason.OtherFocusReason)
            if self._trace_page_position.isVisible():
                self._announcer.announce(self._trace_page_position.text())

    def _set_trace_lens(self, lens: str) -> None:
        self._trace_lens = lens
        self._trace_canvas_failed = None
        if lens == "exported":
            self._request_trace_output_check(open_when_verified=False)
        else:
            self._cancel_trace_output_check()
        for key, button in self._trace_lens_buttons.items():
            button.setChecked(key == lens)
        self._trace_document_stack.setCurrentWidget(
            self._trace_arrangement if lens == "arrange" else self._trace_document
        )
        self._selection_summary.setVisible(lens != "exported")
        self._trace_zoom.setVisible(lens not in {"arrange", "exported"})
        self._rotate_button.setVisible(lens in {"planned", "arrange"})
        for button in self._trace_arrangement_actions:
            button.setVisible(lens == "arrange")
        if lens != "exported":
            self._trace_choose_pdf_button.hide()
            self._trace_open_pdf_button.hide()
            self._trace_cancel_check_button.hide()
        self._refresh_trace_document()
        self._schedule_preview_refresh()

    def _refresh_trace_document(self, *_args: object) -> None:
        row = self._page_view.currentIndex().row()
        page_id = self._selected_page_id()
        pages = self._model.pages
        self._trace_recipe_pane.select_page(page_id)
        has_page = 0 <= row < len(pages)
        show_packet_position = has_page and self._trace_lens != "exported"
        self._trace_page_position.setText(
            tr("Page {value1} of {value2}", value1=row + 1, value2=len(pages))
            if show_packet_position
            else ""
        )
        self._trace_page_position.setVisible(show_packet_position)
        show_page_navigation = self._trace_lens != "exported" or self._trace_evidence is not None
        self._trace_previous_button.setVisible(show_page_navigation)
        self._trace_next_button.setVisible(show_page_navigation)
        self._trace_previous_button.setEnabled(0 < row < len(pages))
        self._trace_next_button.setEnabled(0 <= row < len(pages) - 1)
        target = self._trace_canvas_target()
        if self._trace_canvas_request is not None and self._trace_canvas_request[1] != target:
            self._cancel_trace_canvas()
        if self._trace_canvas_image is not None and self._trace_canvas_image.target != target:
            self._trace_canvas_image = None
        if self._trace_lens == "arrange":
            return
        if self._trace_lens == "exported":
            self._refresh_trace_exported(page_id)
            return
        if page_id is None or not 0 <= row < len(pages):
            self._trace_document.display(None, tr("Add a file to start the packet."))
            return
        page = pages[row]
        description = (
            tr(
                "Source page {value1} · Assembled page {value2}",
                value1=page.source_page_number or 1,
                value2=page.position,
            )
            if self._trace_lens == "source"
            else self._page_label(page)
        )
        canvas = self._trace_canvas_image
        pixmap = canvas.image if canvas is not None else None
        if self._trace_lens == "planned" and pixmap is None:
            pixmap = self._model.preview(page_id)
        if pixmap is None:
            status = (
                PreviewState.LOADING
                if self._trace_lens == "source" and page.preview_supported
                else self._model.preview_state(page_id)
            )
            if target is not None and self._trace_canvas_failed == target:
                status = PreviewState.FAILED
            if status is not None:
                description = tr(
                    "{value1} · Preview {value2}", value1=description, value2=tr(status.value)
                )
        if self._trace_lens == "planned":
            rendered_marks = canvas.rendered_marks if canvas is not None else 0
            if (
                page.removal_mark_count
                or sum(mark.count for mark in page.overlay_summaries) > rendered_marks
            ):
                description += "\n" + tr("Review saved marks in the page editor.")
            if canvas is not None and canvas.approximate_text:
                description += "\n" + tr("Text size is approximate in this preview.")
        self._trace_document.display(pixmap, description)
        self._selection_summary.setText(description)
        if (
            target is not None
            and self._trace_canvas_image is None
            and self._trace_canvas_request is None
            and self._trace_canvas_failed != target
            and not self._trace_canvas_timer.isActive()
        ):
            self._trace_canvas_timer.start()

    def _trace_canvas_target(self) -> _TraceCanvasTarget | None:
        state = self._state
        page_id = self._selected_page_id()
        row = self._model.row_for_page(page_id) if page_id is not None else None
        if (
            state is None
            or state.case_id is None
            or state.trace_plan is None
            or state.autosave_pending
            or page_id is None
            or row is None
            or not self._model.pages[row].preview_supported
            or self._trace_lens not in {"source", "planned"}
            or self._closing_requested
            or self._assisted_session.locked
        ):
            return None
        return _TraceCanvasTarget(
            state.case_id, page_id, state.trace_plan.fingerprint, self._trace_lens == "source"
        )

    def _request_trace_canvas(self) -> None:
        target = self._trace_canvas_target()
        if (
            target is None
            or self._active_operation is not None
            or self._case_close_pending
            or not self._controls_enabled
            or not self._trace_document.isVisible()
        ):
            return
        operation_id = self._allocate_operation_id()
        token = OperationCancellation()
        self._trace_canvas_request = operation_id, target, token
        self._commands.source_preview_requested.emit(
            operation_id,
            target.case_id,
            target.page_id,
            _SCAN_REVIEW_PREVIEW_WIDTH,
            _SCAN_REVIEW_PREVIEW_HEIGHT,
            token,
            target.source,
        )

    def _cancel_trace_canvas(self) -> None:
        self._trace_canvas_timer.stop()
        if self._trace_canvas_request is not None:
            self._trace_canvas_request[2].cancel()
            self._trace_canvas_request = None

    def _accept_trace_canvas(
        self, operation_id: int, case_id: object, page_id: object, source: bool
    ) -> _TraceCanvasTarget | None:
        pending = self._trace_canvas_request
        if pending is None or pending[0] != operation_id:
            return None
        self._trace_canvas_request = None
        target = pending[1]
        if (
            case_id != target.case_id
            or page_id != target.page_id
            or source != target.source
            or pending[2].cancelled()
            or target != self._trace_canvas_target()
        ):
            return None
        return target

    @Slot(int, object, object, object, bool)
    def _trace_canvas_ready(
        self, operation_id: int, case_id: object, page_id: object, result: object, source: bool
    ) -> None:
        target = self._accept_trace_canvas(operation_id, case_id, page_id, source)
        if target is None:
            return
        records: tuple[OverlayRecord, ...] = ()
        if not source and isinstance(result, TracePlannedPreview):
            records = result.overlays
            result = result.raster
        if isinstance(result, (PdfThumbnailResult, ImagePagePreviewResult)):
            pixels = result.snapshot.pixels
        elif isinstance(result, ImagePageTransformResult):
            pixels = result.pixels
        else:
            self._trace_canvas_failed = target
            self._refresh_trace_document()
            return
        snapshot = result.snapshot
        if snapshot.case_id != target.case_id or snapshot.page_id != target.page_id:
            self._trace_canvas_failed = target
            self._refresh_trace_document()
            return
        row = self._model.row_for_page(target.page_id)
        page = self._model.pages[row] if row is not None and not source else None
        image = _organizer_preview_image(snapshot, pixels, page)
        if image.isNull():
            self._trace_canvas_failed = target
        else:
            rendered_marks = 0
            if page is not None and records:
                transform, can_map_marks = _overlay_editor_display_facts(page)
                if can_map_marks and paint_core_overlay_preview(
                    image,
                    transform,
                    records,
                    self.font(),
                    self.logicalDpiY(),
                    maximum_paint_pixels=self._resource_policy.maximum_preview_pixels_per_page,
                ):
                    rendered_marks = len(records)
            self._trace_canvas_image = _TraceCanvasPresentation(
                target,
                QPixmap.fromImage(image),
                rendered_marks,
                rendered_marks > 0
                and any(isinstance(record.payload, TextualOverlayPayload) for record in records),
            )
        self._refresh_trace_document()

    @Slot(int, object, object, object, bool)
    def _trace_canvas_failure(
        self, operation_id: int, case_id: object, page_id: object, failure: object, source: bool
    ) -> None:
        target = self._accept_trace_canvas(operation_id, case_id, page_id, source)
        if target is not None:
            self._trace_canvas_failed = target
            self._refresh_trace_document()
            if source and isinstance(failure, UiFailure):
                self._announce(failure.message)

    def _refresh_trace_exported(self, page_id: UUID | None) -> None:
        evidence = self._trace_evidence
        state = self._state
        saved = state.trace_saved_export if state is not None else None
        checking = self._trace_output_request is not None
        self._trace_choose_pdf_button.setVisible(evidence is None and saved is not None)
        self._trace_cancel_check_button.setVisible(checking)
        self._trace_open_pdf_button.setVisible(
            evidence is not None or self._trace_saved_selection is not None
        )
        self._trace_open_pdf_button.setEnabled(evidence is not None or not checking)
        if evidence is None:
            if saved is None:
                message = tr("No verified PDF export record is available for this case.")
            else:
                selection = self._trace_saved_selection
                message = "\n".join(
                    part
                    for part in (
                        tr("Saved PDF export · {value1} pages", value1=saved.page_count),
                        selection.destination.name if selection is not None else "",
                        tr("Checking the exported file…")
                        if checking
                        else self._trace_saved_output_message(),
                    )
                    if part
                )
            self._trace_document.display(None, message)
            return
        plan = state.trace_plan if state is not None else None
        current = (
            state is not None
            and plan is not None
            and evidence.applies_to(
                state.case_id, plan.fingerprint, ocr_language_set_id=self._trace_ocr_language_set_id
            )
        )
        if checking:
            status = tr("Checking the exported file…")
        elif self._trace_output_status is TraceOutputStatus.PRESENT:
            status = tr("Exported file matches the verified output.")
        elif self._trace_output_status is TraceOutputStatus.MISSING:
            status = tr("Exported file could not be found.")
        elif self._trace_output_status is TraceOutputStatus.CHANGED:
            status = tr("Exported file has changed since verification.")
        else:
            status = tr("Exported file not checked in this view.")
        if not current:
            status = tr("Out of date for the current recipe.") + "\n" + status
        mapping = ""
        if page_id in evidence.page_ids:
            mapping = tr(
                "Selected page was output page {value1} of {value2}.",
                value1=evidence.page_ids.index(page_id) + 1,
                value2=evidence.page_count,
            )
        elif page_id is not None:
            mapping = tr("The selected page was not in this PDF.")
        self._trace_document.display(
            None,
            "\n".join(
                part
                for part in (
                    evidence.destination.name,
                    status,
                    mapping,
                    tr("Use Open PDF to view the exported file."),
                )
                if part
            ),
        )

    def _trace_saved_output_message(self) -> str:
        status = self._trace_output_status
        if status is TraceOutputStatus.PRESENT:
            return tr("Selected PDF matches the saved export by size and SHA-256.")
        if status is TraceOutputStatus.MISSING:
            return tr("The selected PDF could not be found. Select another copy.")
        if status is TraceOutputStatus.CHANGED:
            return tr("The selected PDF does not match the saved export. Select another copy.")
        if status is TraceOutputStatus.UNAVAILABLE:
            return tr("PaperWorkShell could not verify the selected PDF. Select another copy.")
        return tr("Select the exported PDF to verify its size and SHA-256.")

    def _choose_trace_export(self) -> None:
        state = self._state
        saved = state.trace_saved_export if state is not None else None
        if (
            saved is None
            or self._trace_evidence is not None
            or self._assisted_session.locked
            or self._closing_requested
            or self._case_close_pending
        ):
            return
        selected, _ = QFileDialog.getOpenFileName(
            self,
            tr("Select exported PDF…"),
            "",
            tr("PDF files (*.pdf)"),
            options=self._file_dialog_options(),
        )
        if (
            selected
            and self._state is not None
            and self._state.trace_saved_export == saved
            and self._trace_lens == "exported"
        ):
            self._request_trace_output_check(
                open_when_verified=False, selection=TraceSavedExportSelection(saved, Path(selected))
            )

    @Slot(int, object)
    def _trace_pdf_export_ready(self, operation_id: int, evidence: object) -> None:
        active = self._active_operation
        if (
            not isinstance(evidence, TraceExportEvidence)
            or self._state is None
            or self._state.case_id != evidence.case_id
            or active is None
            or active.operation_id != operation_id
            or active.kind is not _LongOperationKind.PDF_EXPORT
        ):
            return
        self._cancel_trace_output_check()
        self._trace_evidence = evidence
        self._trace_saved_selection = None
        self._trace_output_status = TraceOutputStatus.PRESENT
        self._trace_recipe_pane.set_export_evidence(evidence)
        self._refresh_trace_document()

    def _cancel_trace_output_check(self) -> None:
        if self._trace_output_request is not None:
            self._trace_output_request[1].cancel()
            self._trace_output_request = None

    def _cancel_trace_file_check(self) -> None:
        self._cancel_trace_output_check()
        self._refresh_trace_document()
        recovery = (
            self._trace_choose_pdf_button
            if self._trace_evidence is None
            else self._trace_open_pdf_button
        )
        recovery.setFocus(Qt.FocusReason.OtherFocusReason)
        self._announce(tr("PDF verification cancelled."))

    def _request_trace_output_check(
        self, *, open_when_verified: bool, selection: TraceSavedExportSelection | None = None
    ) -> None:
        target = self._trace_evidence or selection or self._trace_saved_selection
        if (
            target is None
            or self._assisted_session.locked
            or self._closing_requested
            or self._case_close_pending
        ):
            return
        self._cancel_trace_output_check()
        if selection is not None:
            self._trace_saved_selection = None
            self._trace_output_status = None
        token = OperationCancellation()
        operation_id = self._allocate_operation_id()
        self._trace_output_request = operation_id, token, open_when_verified, target
        if self._trace_lens == "exported":
            self._refresh_trace_document()
        self._commands.trace_export_inspect_requested.emit(operation_id, target, token)

    @Slot(int, object, object)
    def _trace_export_inspected(self, operation_id: int, evidence: object, status: object) -> None:
        request = self._trace_output_request
        if request is None or request[0] != operation_id or request[3] != evidence:
            return
        self._trace_output_request = None
        if self._assisted_session.locked or self._closing_requested or self._case_close_pending:
            return
        if not isinstance(status, TraceOutputStatus):
            return
        target = request[3]
        self._trace_output_status = status
        self._trace_recipe_pane.set_output_status(status)
        focus_was_on_cancel = self._trace_cancel_check_button.hasFocus()
        if isinstance(target, TraceSavedExportSelection):
            self._trace_saved_selection = target if status is TraceOutputStatus.PRESENT else None
            self._announce(self._trace_saved_output_message())
        self._refresh_trace_document()
        if focus_was_on_cancel:
            recovery = (
                self._trace_choose_pdf_button
                if isinstance(target, TraceSavedExportSelection)
                else self._trace_open_pdf_button
            )
            recovery.setFocus(Qt.FocusReason.OtherFocusReason)
        if request[2]:
            if status is TraceOutputStatus.PRESENT:
                self._open_export_path(target.destination)
            elif isinstance(target, TraceExportEvidence):
                message = tr("The exported PDF could not be verified. Export again to a new file.")
                self._set_error_text(message)
                self._error_label.show()
                self._announce(message, assertive=True)

    def _choose_trace_recipe(self, mode: str) -> None:
        state = self._state
        if (
            state is None
            or state.case_id is None
            or state.trace_plan is None
            or state.autosave_pending
            or not state.pages
            or self._active_operation is not None
        ):
            return
        recipe = state.trace_plan.recipe
        try:
            if mode != "apply":
                recipe.require_automation_supported()
        except TraceRecipeError as error:
            self._set_error_text(error.safe_message)
            self._error_label.show()
            self._focus_error_recovery(self._error_label)
            return
        options = self._file_dialog_options()
        if mode == "apply":
            selected, _ = QFileDialog.getOpenFileName(
                self, tr("Apply recipe"), "", tr("TRACE recipes (*.json)"), options=options
            )
        else:
            script = mode == "script"
            selected, _ = QFileDialog.getSaveFileName(
                self,
                tr("Export PowerShell script") if script else tr("Save recipe"),
                "PaperWorkShell-recipe.ps1" if script else "PaperWorkShell-recipe.json",
                tr("PowerShell script (*.ps1)") if script else tr("TRACE recipes (*.json)"),
                options=options,
            )
        if not selected:
            return
        destination = Path(selected)
        self._error_label.hide()
        if mode == "script":
            try:
                export_trace_powershell(recipe, destination)
            except (TraceRecipeError, TraceAutomationError) as error:
                self._set_error_text(error.safe_message)
                self._error_label.show()
                self._focus_error_recovery(self._error_label)
            else:
                self._announce(tr("PowerShell script saved."))
            return
        operation = self._begin_long_operation(_LongOperationKind.TRACE_RECIPE)
        self._cancel_button.hide()
        signal = (
            self._commands.trace_recipe_apply_requested
            if mode == "apply"
            else self._commands.trace_recipe_save_requested
        )
        signal.emit(
            operation.operation_id, state.case_id, state.trace_plan.fingerprint, destination
        )

    def _build_trace_commands(self) -> None:
        case_required = tr("Open a saved case to use this command.")
        page_required = tr("Select an applicable page and finish any operation in progress.")
        chooser_required = tr("Close the current case to return to the case chooser.")
        add = tr("Add")
        organize = tr("Organize")
        improve = tr("Improve")
        fill = tr("Fill")
        protect = tr("Protect")
        check = tr("Check")
        export = tr("Export")
        settings = tr("Settings")
        self._trace_chooser_commands = (
            TraceCommand(
                "case.new", tr("New case…"), add, self._create_case_button, chooser_required
            ),
            TraceCommand(
                "case.open",
                tr("Open selected case"),
                add,
                self._open_case_button,
                tr("Select a case first."),
            ),
            TraceCommand(
                "case.refresh",
                tr("Refresh case list"),
                add,
                self._refresh_cases_button,
                chooser_required,
            ),
            TraceCommand(
                "case.assisted",
                tr("Start assisted case"),
                protect,
                self._assisted_create_button,
                chooser_required,
            ),
        )
        self._trace_save_recipe_button = self._action_button(
            "Save recipe…",
            "traceSaveRecipe",
            "Save source slots, assembled order and rotation as a local recipe.",
        )
        self._trace_apply_recipe_button = self._action_button(
            "Apply recipe…",
            "traceApplyRecipe",
            "Use a saved recipe with the currently imported sources.",
        )
        self._trace_script_button = self._action_button(
            "Export PowerShell script…",
            "traceExportPowerShell",
            "Create a local script for page selection, order, rotation and ordinary PDF export.",
        )
        for mode, action in (
            ("save", self._trace_save_recipe_button),
            ("apply", self._trace_apply_recipe_button),
            ("script", self._trace_script_button),
        ):
            action.clicked.connect(
                lambda _checked=False, selected=mode: self._choose_trace_recipe(selected)
            )
        self._trace_case_commands = (
            TraceCommand(
                "source.import",
                tr("Add file…"),
                add,
                self._import_button,
                case_required,
                ("import", "PDF JPEG PNG"),
            ),
            TraceCommand(
                "case.rename", tr("Rename case…"), add, self._rename_case_button, case_required
            ),
            TraceCommand(
                "case.close",
                tr("Close case"),
                add,
                self._close_case_button,
                case_required,
                ("return",),
            ),
            TraceCommand(
                "case.lock",
                tr("Lock assisted case"),
                protect,
                self._assisted_lock_button,
                tr("This case is not an active assisted case."),
            ),
            TraceCommand(
                "page.move-earlier",
                tr("Move up"),
                organize,
                self._move_earlier_button,
                tr("Select a page below the first one."),
                ("earlier",),
            ),
            TraceCommand(
                "page.move-later",
                tr("Move down"),
                organize,
                self._move_later_button,
                tr("Select a page above the last one."),
                ("later",),
            ),
            TraceCommand(
                "page.order",
                tr("Reorder pages…"),
                organize,
                self._change_order_button,
                tr("Add at least two pages to reorder them."),
                ("arrange", "sort"),
            ),
            TraceCommand(
                "page.rotate",
                tr("Rotate selected page 90°"),
                organize,
                self._rotate_button,
                page_required,
                ("turn", "clockwise"),
            ),
            TraceCommand(
                "page.remove",
                tr("Remove page"),
                organize,
                self._remove_button,
                page_required,
                ("delete",),
            ),
            TraceCommand(
                "history.undo",
                tr("Undo"),
                organize,
                self._undo_button,
                tr("There are no available edits to undo."),
            ),
            TraceCommand(
                "history.redo",
                tr("Redo"),
                organize,
                self._redo_button,
                tr("There are no available edits to redo."),
            ),
            TraceCommand(
                "scan.improve",
                tr("Improve selected scan…"),
                improve,
                self._scan_review_button,
                tr("Select a supported scan and let current changes save."),
                ("crop", "deskew", "contrast"),
            ),
            TraceCommand(
                "overlay.edit",
                tr("Add text or marks…"),
                fill,
                self._core_overlay_tools_button,
                page_required,
                ("form", "checkmark", "overlay"),
            ),
            TraceCommand(
                "signature.place",
                tr("Place signature image…"),
                fill,
                self._place_signature_image_button,
                page_required,
            ),
            TraceCommand(
                "signature.remove",
                tr("Remove signature image…"),
                fill,
                self._remove_signature_image_button,
                page_required,
            ),
            TraceCommand(
                "checklist.review",
                tr("Document checklist…"),
                fill,
                self._checklist_button,
                case_required,
                ("template", "package"),
            ),
            TraceCommand(
                "removal.mark",
                tr("Permanently remove information…"),
                protect,
                self._redaction_tools_button,
                page_required,
                ("redaction",),
            ),
            TraceCommand(
                "quality.check",
                tr("Check page quality"),
                check,
                self._quality_check_button,
                tr("Add supported pages and let current changes save."),
                ("OCR", "review"),
            ),
            TraceCommand(
                "package.constraints",
                tr("Review package constraints…"),
                check,
                self._portal_constraints_button,
                case_required,
            ),
            TraceCommand(
                "recipe.save",
                tr("Save recipe…"),
                export,
                self._trace_save_recipe_button,
                case_required,
                ("reuse",),
            ),
            TraceCommand(
                "recipe.apply",
                tr("Apply recipe…"),
                organize,
                self._trace_apply_recipe_button,
                case_required,
                ("reuse",),
            ),
            TraceCommand(
                "recipe.script",
                tr("Export PowerShell script…"),
                export,
                self._trace_script_button,
                case_required,
                ("automation", "headless"),
            ),
            TraceCommand(
                "pdf.export",
                tr("Export PDF…"),
                export,
                self._pages_pdf_export_button,
                tr("Add pages and let current changes save."),
                ("OCR", "searchable", "save"),
            ),
            TraceCommand(
                "package.export",
                tr("Export checklist package…"),
                export,
                self._package_export_button,
                case_required,
                ("summary", "ZIP"),
            ),
            TraceCommand(
                "removal.export",
                tr("Export removal derivative…"),
                export,
                self._removal_export_button,
                tr("Review saved removal marks on a supported single-page case."),
            ),
        )
        density = self._action_button(
            "Compact page list", "traceDensity", "Use less space between page rows."
        )
        density.setCheckable(True)
        density.toggled.connect(self._set_trace_compact)
        self._trace_global_commands = (
            TraceCommand(
                "ocr.languages",
                tr("OCR languages…"),
                settings,
                self._ocr_language_button,
                tr("Wait for the application to be ready."),
                ("Tesseract",),
            ),
            TraceCommand(
                "help.keyboard",
                tr("Keyboard guide"),
                settings,
                self._action_button(
                    "Keyboard guide", "traceGuideCommand", "Keyboard shortcuts and navigation. F1."
                ),
                tr("Wait for the application to be ready."),
            ),
            TraceCommand(
                "help.support",
                tr("Create support bundle…"),
                settings,
                self._action_button(
                    "Create support bundle…",
                    "traceSupportCommand",
                    "Review sanitized support files before saving.",
                ),
                tr("Wait for the application to be ready."),
            ),
            TraceCommand(
                "view.compact",
                tr("Compact page list"),
                settings,
                density,
                case_required,
                ("density", "comfortable"),
            ),
        )
        self._trace_global_commands[1].button.clicked.connect(self._show_keyboard_help)
        self._trace_global_commands[2].button.clicked.connect(self._show_diagnostic_bundle)
        self._keyboard_help_action.triggered.disconnect(self._show_keyboard_help)
        self._diagnostic_bundle_action.triggered.disconnect(self._show_diagnostic_bundle)
        self._keyboard_help_action.triggered.connect(self._trace_global_commands[1].button.click)
        self._diagnostic_bundle_action.triggered.connect(
            self._trace_global_commands[2].button.click
        )
        for command in self._trace_global_commands[1:]:
            command.button.setParent(self._trace_app_bar)
            command.button.hide()
        self._set_trace_menu(self._settings_button, self._trace_global_commands)
        self._trace_button_proxy: dict[QPushButton, QPushButton] = {
            command.button: self._settings_button for command in self._trace_global_commands
        }
        for key, group in (
            ("add", add),
            ("organize", organize),
            ("improve", improve),
            ("fill", fill),
            ("protect", protect),
            ("check", check),
            ("export", export),
        ):
            group_button = QPushButton(group)
            group_button.setObjectName(f"traceWorkflow-{key}")
            commands = tuple(row for row in self._trace_case_commands if row.group == group)
            for row in commands:
                if row.button.parentWidget() is None:
                    row.button.setParent(self._organizer_page)
                    row.button.hide()
                self._trace_button_proxy[row.button] = group_button
            self._set_trace_menu(group_button, commands)
            self._trace_workflow_actions.addWidget(group_button)
        for pane, label in (
            ("pages", tr("Pages")),
            ("document", tr("Document")),
            ("inspector", tr("Inspection")),
        ):
            button = QPushButton(label)
            button.setObjectName(f"tracePane-{pane}")
            button.clicked.connect(
                lambda _checked=False, selected=pane: self._trace_select_pane(selected)
            )
            self._trace_workflow_actions.addWidget(button)

    def _set_trace_menu(self, invoker: QPushButton, commands: tuple[TraceCommand, ...]) -> None:
        menu = QMenu(invoker)
        invoker.setMenu(menu)

        def populate() -> None:
            menu.clear()
            for command in commands:
                if command.key == "case.lock" and command.button.isHidden():
                    continue
                action = menu.addAction(self._trace_command_label(command))
                action.setEnabled(command.button.isEnabled())
                action.setCheckable(command.button.isCheckable())
                action.setChecked(command.button.isChecked())
                action.setToolTip(command.button.accessibleDescription())
                action.triggered.connect(command.button.click)

        menu.aboutToShow.connect(populate)

    def _trace_command_label(self, command: TraceCommand) -> str:
        if command.key == "pdf.export":
            return command.button.accessibleName()
        return command.label

    def _show_command_palette(self) -> None:
        if self._closing_requested:
            return
        focus = QApplication.focusWidget()
        state = self._state
        commands = (
            self._trace_chooser_commands
            if state is None or state.case_id is None
            else tuple(
                row
                for row in self._trace_case_commands
                if row.key != "case.lock" or not row.button.isHidden()
            )
        )
        dialog = TraceCommandPalette(
            self,
            tuple(
                replace(command, label=self._trace_command_label(command))
                for command in commands + self._trace_global_commands
            ),
        )
        dialog.exec()
        if focus is not None and self._focus_widget_is_available(focus):
            focus.setFocus(Qt.FocusReason.OtherFocusReason)
        if dialog.command is not None:
            dialog.command.button.click()
        dialog.deleteLater()

    def _focus_trace_invoker(self, action: QPushButton) -> None:
        proxy = self._trace_button_proxy.get(action)
        if action.isHidden() and action.isEnabled() and proxy is not None and proxy.isVisible():
            proxy.setFocus(Qt.FocusReason.OtherFocusReason)

    def _set_trace_compact(self, compact: bool) -> None:
        self._trace_rail_delegate.compact = compact
        self._page_view.doItemsLayout()
        self._page_view.viewport().update()
        self._schedule_preview_refresh()

    def _trace_select_pane(self, selected: str) -> None:
        if selected == "inspector":
            if self._trace_split.toggle_inspection():
                self._trace_inspector_tabs.tabBar().setFocus(Qt.FocusReason.OtherFocusReason)
            return
        self._trace_split.select_pane(selected)
        if selected == "document":
            self._refresh_trace_document()
            self._trace_document_stack.currentWidget().setFocus(Qt.FocusReason.OtherFocusReason)
        elif selected == "pages":
            self._trace_left_tabs.currentWidget().setFocus(Qt.FocusReason.OtherFocusReason)

    def _action_button(self, text: str, object_name: str, description: str) -> QPushButton:
        button = QPushButton(tr(text))
        button.setObjectName(object_name)
        button.setAccessibleName(tr(text))
        button.setAccessibleDescription(tr(description))
        button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        return button

    def _build_shortcuts(self) -> None:
        shortcuts = (
            ("Ctrl+K", self._show_command_palette),
            ("Ctrl+N", self._create_case_button.click),
            ("Ctrl+O", self._open_case_button.click),
            ("Ctrl+I", self._import_button.click),
            ("Ctrl+Z", self._undo_button.click),
            ("Ctrl+Y", self._redo_button.click),
            ("Alt+Up", self._move_earlier_button.click),
            ("Alt+Down", self._move_later_button.click),
            ("Ctrl+R", self._rotate_button.click),
            ("Delete", self._remove_button.click),
            ("Ctrl+Shift+A", self._scan_review_button.click),
            ("Ctrl+Shift+Q", self._quality_check_button.click),
            ("Ctrl+Shift+L", self._ocr_language_button.click),
            ("Ctrl+Shift+O", self._change_order_button.click),
            ("Ctrl+Shift+F", self._core_overlay_tools_button.click),
            ("Ctrl+Shift+P", self._redaction_tools_button.click),
            ("Ctrl+Shift+C", self._checklist_button.click),
            ("Ctrl+Shift+S", self._place_signature_image_button.click),
            ("Ctrl+Shift+D", self._remove_signature_image_button.click),
            ("Ctrl+Shift+R", self._retry_preview_button.click),
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
        self._worker.first_png_import_saved.connect(self._first_png_import_saved)
        self._worker.case_close_finished.connect(self._case_close_finished)
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
        self._worker.core_overlay_tools_started.connect(self._core_overlay_tools_started)
        self._worker.core_overlay_tools_start_failed.connect(self._core_overlay_tools_start_failed)
        self._worker.core_overlay_tools_applied.connect(self._core_overlay_tools_applied)
        self._worker.core_overlay_tools_apply_failed.connect(self._core_overlay_tools_apply_failed)
        self._worker.core_overlay_tools_cancelled.connect(self._core_overlay_tools_cancelled)
        self._worker.core_overlay_tools_cancel_failed.connect(
            self._core_overlay_tools_cancel_failed
        )
        self._worker.redaction_tools_started.connect(self._redaction_tools_started)
        self._worker.redaction_tools_start_failed.connect(self._redaction_tools_start_failed)
        self._worker.redaction_tools_saved.connect(self._redaction_tools_saved)
        self._worker.redaction_tools_save_failed.connect(self._redaction_tools_save_failed)
        self._worker.redaction_tools_cancelled.connect(self._redaction_tools_cancelled)
        self._worker.redaction_tools_cancel_failed.connect(self._redaction_tools_cancel_failed)
        self._worker.checklist_started.connect(self._checklist_started)
        self._worker.checklist_start_failed.connect(self._checklist_start_failed)
        self._worker.checklist_applied.connect(self._checklist_applied)
        self._worker.checklist_apply_failed.connect(self._checklist_apply_failed)
        self._worker.checklist_cancelled.connect(self._checklist_cancelled)
        self._worker.checklist_cancel_failed.connect(self._checklist_cancel_failed)
        self._worker.portal_constraints_started.connect(self._portal_constraints_started)
        self._worker.portal_constraints_start_failed.connect(self._portal_constraints_start_failed)
        self._worker.portal_constraints_applied.connect(self._portal_constraints_applied)
        self._worker.portal_constraints_apply_failed.connect(self._portal_constraints_apply_failed)
        self._worker.portal_constraints_cancelled.connect(self._portal_constraints_cancelled)
        self._worker.portal_constraints_cancel_failed.connect(
            self._portal_constraints_cancel_failed
        )
        self._worker.package_review_ready.connect(self._package_review_ready)
        self._worker.package_export_ready.connect(self._package_export_ready)
        self._worker.package_failed.connect(self._package_failed)
        self._worker.package_progress.connect(self._package_progress)
        self._worker.package_review_cancelled.connect(self._package_review_cancelled)
        self._worker.package_summary_saved.connect(self._package_summary_saved)
        self._worker.post_export_deletion_review_ready.connect(self._post_export_deletion_ready)
        self._worker.post_export_deletion_finished.connect(self._post_export_deletion_finished)
        self._worker.text_companion_saved.connect(self._text_companion_saved)
        self._worker.post_export_failed.connect(self._post_export_failed)
        self._worker.compression_ready.connect(self._compression_ready)
        self._worker.compression_preview_ready.connect(self._compression_preview_ready)
        self._worker.signature_image_prepared.connect(self._signature_image_prepared)
        self._worker.signature_image_prepare_failed.connect(self._signature_image_prepare_failed)
        self._worker.signature_image_placed.connect(self._signature_image_placed)
        self._worker.signature_image_place_failed.connect(self._signature_image_place_failed)
        self._worker.signature_image_cancelled.connect(self._signature_image_cancelled)
        self._worker.signature_image_cancel_failed.connect(self._signature_image_cancel_failed)
        self._worker.signature_image_placements_ready.connect(
            self._signature_image_placements_ready
        )
        self._worker.signature_image_placements_failed.connect(
            self._signature_image_placements_failed
        )
        self._worker.signature_image_deleted.connect(self._signature_image_deleted)
        self._worker.signature_image_delete_failed.connect(self._signature_image_delete_failed)
        self._worker.progress_changed.connect(self._progress_changed)
        self._worker.removal_export_review_ready.connect(self._removal_export_review_ready)
        self._worker.removal_export_ready.connect(self._removal_export_ready)
        self._worker.status_changed.connect(self._status_changed)
        self._worker.operation_status_changed.connect(self._operation_status_changed)
        self._worker.pages_pdf_export_saved.connect(self._pages_pdf_export_saved)
        self._worker.source_preview_ready.connect(self._trace_canvas_ready)
        self._worker.source_preview_failed.connect(self._trace_canvas_failure)
        self._worker.trace_pdf_export_ready.connect(self._trace_pdf_export_ready)
        self._worker.trace_export_inspected.connect(self._trace_export_inspected)
        self._worker.case_reopen_required.connect(self._reopen_required)
        self._worker.review_required.connect(self._review_required)
        self._worker.operation_started.connect(self._operation_started)
        self._worker.operation_cancelled.connect(self._operation_cancelled)
        self._worker.operation_finished.connect(self._operation_finished)
        self._worker.failed.connect(self._failed)
        self._worker.shutdown_complete.connect(self._shutdown_complete)
        self._worker.shutdown_refused.connect(self._shutdown_refused)

    @Slot()
    def _show_diagnostic_bundle(self) -> None:
        if self._diagnostic_bundle_dialog is not None or self._closing_requested:
            return
        self._diagnostic_bundle_return_focus = QApplication.focusWidget()
        dialog = DiagnosticBundleDialog(self._diagnostic_bundle_service, self)
        self._diagnostic_bundle_dialog = dialog
        dialog.finished.connect(self._diagnostic_bundle_finished)
        dialog.open()

    @Slot(int)
    def _diagnostic_bundle_finished(self, _result: int) -> None:
        dialog = self._diagnostic_bundle_dialog
        self._diagnostic_bundle_dialog = None
        if dialog is not None:
            dialog.deleteLater()
        previous = self._diagnostic_bundle_return_focus
        self._diagnostic_bundle_return_focus = None
        if self._diagnostic_bundle_shutdown_requested:
            self._diagnostic_bundle_shutdown_requested = False
            QTimer.singleShot(0, self.request_shutdown)
            return
        if not self._closing_requested:
            self.activateWindow()
            if self._focus_widget_is_available(previous) and previous is not None:
                previous.setFocus(Qt.FocusReason.OtherFocusReason)
                self._ensure_widget_visible(previous)
            else:
                self._focus_safe_control()

    @Slot()
    def _show_keyboard_help(self) -> None:
        if self._keyboard_help_dialog is not None or self._closing_requested:
            return
        self._keyboard_help_return_focus = QApplication.focusWidget()
        dialog = KeyboardHelpDialog(self)
        self._keyboard_help_dialog = dialog
        dialog.finished.connect(self._keyboard_help_finished)
        dialog.open()

    @Slot(int)
    def _keyboard_help_finished(self, _result: int) -> None:
        dialog = self._keyboard_help_dialog
        self._keyboard_help_dialog = None
        if dialog is not None:
            dialog.deleteLater()
        QTimer.singleShot(0, self._restore_keyboard_help_focus)

    def _restore_keyboard_help_focus(self) -> None:
        previous = self._keyboard_help_return_focus
        self._keyboard_help_return_focus = None
        if self._closing_requested:
            return
        self.activateWindow()
        if self._focus_widget_is_available(previous) and previous is not None:
            previous.setFocus(Qt.FocusReason.OtherFocusReason)
            self._ensure_widget_visible(previous)
        else:
            self._focus_safe_control()

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
        self._ocr_language_cached_open = True
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
        if getattr(self, "_ocr_language_cached_open", False):
            self._ocr_language_cached_open = False
            self._commands.ocr_languages_show_requested.emit(request_id)
        else:
            self._commands.ocr_languages_refresh_requested.emit(request_id)

    @Slot(int, object)
    def _ocr_languages_changed(self, request_id: int, snapshot: object) -> None:
        if type(snapshot) is not OcrLanguageSnapshot:
            return
        dialog = self._ocr_language_dialog
        if dialog is not None:
            dialog.set_snapshot(request_id, snapshot)
        if self._complete_ocr_language_refresh(request_id):
            self._update_trace_ocr_identity(snapshot)

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
        self._selected_ocr_language_codes = snapshot.selected_language_codes
        self._update_trace_ocr_identity(snapshot)
        # OCR findings are configuration-bound as well as revision-bound. A
        # new process-session language set makes any accepted review stale,
        # even though it does not mutate the case manifest.
        self._invalidate_quality_review()
        count = len(snapshot.selected_language_codes)
        noun = tr("language") if count == 1 else tr("languages")
        self._ocr_language_saved_message = tr(
            "OCR language selection saved for this application session: {value1} {value2}.",
            value1=count,
            value2=noun,
        )

    def _update_trace_ocr_identity(self, snapshot: OcrLanguageSnapshot) -> None:
        self._trace_ocr_language_set_id = snapshot.selected_language_set_id
        self._trace_recipe_pane.set_ocr_language_set(snapshot.selected_language_set_id)
        if self._trace_lens == "exported":
            self._refresh_trace_document()

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
        self._focus_trace_invoker(self._ocr_language_button)
        if self._closing_requested or not self._ocr_language_button.isEnabled():
            return
        self.activateWindow()
        self._ocr_language_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._ensure_widget_visible(self._ocr_language_button)

    @Slot(object)
    def _cases_changed(self, case_index: object) -> None:
        if self._assisted_session.active:
            self._case_list.clear()
            return
        if type(case_index) is not CaseIndex:
            return
        selected_id = self._selected_case_id()
        self._case_list.clear()
        for summary in case_index.cases:
            item = QListWidgetItem(
                tr(
                    "{value1}\nRevision {value2}; updated {value3}",
                    value1=summary.case_name
                    or tr("Unnamed case — {value1}", value1=summary.updated_at.date().isoformat()),
                    value2=summary.revision,
                    value3=summary.updated_at.isoformat(),
                )
            )
            item.setData(Qt.ItemDataRole.UserRole, summary.case_id)
            item.setToolTip(tr("Case name and last saved revision."))
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
                tr(
                    "{value1} case entries need recovery review and are unavailable.",
                    value1=len(case_index.unavailable),
                ),
                assertive=True,
            )
        if not case_index.cases and not case_index.unavailable and self._case_list.hasFocus():
            self._create_case_button.setFocus(Qt.FocusReason.OtherFocusReason)

    @Slot(object)
    def _state_changed(self, state: object) -> None:
        if self._assisted_session.locked:
            return
        if type(state) is not OrganizerViewState:
            return
        observation = phase_observer.active
        if (
            observation is not None
            and not observation.terminal
            and (
                state.case_id != observation.case_id
                or state.autosave_pending
                or state.revision not in (observation.original_revision, observation.revision)
            )
        ):
            observation.terminal = True
        compression = self._compression_review_state
        if compression is not None and (
            state.case_id != compression.case_id
            or state.revision != compression.manifest_revision
            or state.autosave_pending
        ):
            self._compression_review_state = None
            self._compression_pair_review_id = None
            self._cancel_package_operation()
            if self._compression_dialog is not None and self._package_export_operation_id is None:
                self._compression_dialog.show_failure()
        dialog = self._scan_review_dialog
        if dialog is not None and (
            state.case_id != dialog.draft.case_id
            or state.revision != dialog.draft.manifest_revision
        ):
            self._dismiss_scan_review_after_state_change()
        prior_state = self._state
        rotated_preview_after_save = self._rotated_preview_after_save
        self._rotated_preview_after_save = None
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
            if (
                prior_state is not None
                and prior_state.case_id is not None
                and selected_page is not None
            ):
                self._trace_last_selection = prior_state.case_id, selected_page
            remembered = self._trace_last_selection
            selected_page = (
                remembered[1] if remembered is not None and remembered[0] == state.case_id else None
            )
            if self._trace_lens == "exported":
                self._set_trace_lens("planned")
            self._first_png_import_origin = None
            self._first_png_import_preview = None
            self._import_file_label.clear()
            self._import_file_label.setAccessibleName("")
            self._import_file_label.setAccessibleDescription("")
            self._import_file_label.hide()
            self._last_result_label.clear()
            self._last_result_label.hide()
            self._last_export_path = None
            self._open_pdf_button.hide()
            self._open_export_folder_button.hide()
            self._case_reopen_required = False
            self._latest_import_progress = None
            self._import_presentation = _ImportPresentationState.IDLE
        self._state = state
        self._sync_trace_plan(state, prior_state)
        self._saved_label.setText(
            tr("No case open.")
            if state.case_id is None
            else tr("Saving changes…")
            if state.autosave_pending
            else tr("All changes saved.")
        )
        self._model.set_organizer_state(state)
        self._update_organizer_guidance()
        self._update_import_availability()
        self._update_preview_retry_action()
        self._update_quality_actions()
        if state.case_id is None:
            self.setAccessibleName(tr("PaperWorkShell guided packet builder"))
            self._preview_refresh_timer.stop()
            self._autosave_timer.stop()
            self._deferred_import = None
            self._deferred_preview_state = None
            self._cancel_active_operation_silently()
            self._hide_retry_save()
            self._show_workflow_page(self._welcome_page)
            self._case_list.setFocus(Qt.FocusReason.OtherFocusReason)
            self._ensure_widget_visible(self._case_list)
            return
        self._show_workflow_page(self._organizer_page)
        heading = state.case_name or tr("Unnamed case")
        if state.recovery_review_required:
            heading = tr("{value1} — recovered; review needed", value1=heading)
        self._case_heading.setText(heading)
        case_description = tr(
            "Case {value1}. {value2}. {value3} active pages.",
            value1=heading,
            value2=tr("All changes saved.")
            if not state.autosave_pending
            else tr("Saving changes…"),
            value3=len(state.pages),
        )
        self._case_heading.setAccessibleName(case_description)
        self.setAccessibleName(tr("PaperWorkShell. {value1}", value1=case_description))
        selected_preview_missing = (
            selected_page is not None
            and self._model.preview_state(selected_page) is PreviewState.NOT_LOADED
        )
        previous_case_state = (
            prior_state
            if prior_state is not None and prior_state.case_id == state.case_id
            else None
        )
        if state.autosave_pending:
            self._deferred_preview_state = None
            self._cancel_active_preview()
            # Saving is on the critical path when a new angle has no live pixels.
            rotation_needs_save = (
                selected_preview_missing
                and previous_case_state is not None
                and _page_rotation_changed(previous_case_state, state, selected_page)
            )
            if rotation_needs_save:
                self._rotated_preview_after_save = selected_page
            self._autosave_timer.start(0 if rotation_needs_save else _AUTOSAVE_DEBOUNCE_MS)
        else:
            self._autosave_timer.stop()
            self._hide_retry_save()
        self._restore_page_selection(selected_page)
        self._refresh_trace_document()
        self._update_page_actions()
        self._update_preview_retry_action()
        self._update_quality_actions()
        export_only_refresh = (
            self._active_operation is not None
            and self._active_operation.kind is _LongOperationKind.PDF_EXPORT
            and prior_state is not None
            and prior_state.case_id == state.case_id
            and prior_state.pages == state.pages
        )
        if not state.autosave_pending and not export_only_refresh:
            self._schedule_preview_refresh(
                immediate=(
                    selected_preview_missing
                    and selected_page == rotated_preview_after_save
                    and previous_case_state is not None
                    and previous_case_state.autosave_pending
                    and previous_case_state.pages == state.pages
                )
            )
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
            row = self._model.row_for_page(snapshot.page_id)
            page = self._model.pages[row] if row is not None else None
            image = _organizer_preview_image(snapshot, pixels, page)
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
        observation = phase_observer.active
        if (
            observation is not None
            and observation.preview_id == (operation_id, generation)
            and not active.cancellation.cancelled()
            and self._state is not None
            and len(snapshots) == 1
        ):
            snapshot = snapshots[0][0]
            if (
                type(snapshot) is ImagePageTransformSnapshot
                and snapshot.intent == ImageTransformIntent()
                and self._model.preview_state(snapshot.page_id) is PreviewState.READY
            ):
                observation.preview_accepted(
                    operation_id,
                    generation,
                    snapshot.case_id,
                    snapshot.source_id,
                    snapshot.page_id,
                    self._state.revision,
                    self._selected_page_id(),
                )
            elif (
                type(snapshot) is ImagePagePreviewSnapshot
                and snapshot.additional_rotation is ImageQuarterTurn.DEGREES_0
                and self._state.case_id == snapshot.case_id
                and len(self._state.pages) == 1
                and self._state.pages[0].page_id == snapshot.page_id
                and self._state.pages[0].source_page_number == 1
                and self._state.pages[0].scan_review_intent == ImageTransformIntent()
                and self._model.preview_state(snapshot.page_id) is PreviewState.READY
            ):
                observation.initial_preview_accepted(
                    operation_id,
                    generation,
                    snapshot.case_id,
                    snapshot.page_id,
                    self._state.revision,
                    self._selected_page_id(),
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
                    tr("Page quality check cancelled. No review results were applied."),
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
            or state.revision is None
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
                    tr(
                        "PaperWorkShell could not display page-quality review results safely. Run the check again."
                    )
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
                    message=tr(
                        "PaperWorkShell could not open scan review safely. Start the review again."
                    ),
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
                    message=tr("PaperWorkShell could not open scan review. Try again."),
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
                tr(
                    "PaperWorkShell could not display that preview safely. Update the preview again."
                ),
            )
            return
        byte_cost = image.width() * image.height() * 4
        if not self._reserve_scan_review_preview_bytes(active.variant, byte_cost):
            self._fail_active_scan_review_preview(
                "PWS_UI_SCAN_REVIEW_LIMIT_EXCEEDED",
                tr(
                    "The preview exceeds the configured memory limit. Close scan review and try again."
                ),
            )
            return
        if active.variant is ScanReviewPreviewVariant.ORIGINAL:
            accepted = dialog.set_original_image(result.draft, image)
        else:
            accepted = dialog.set_adjusted_image(result.draft, image)
        if not accepted:
            self._fail_active_scan_review_preview(
                "PWS_UI_SCAN_REVIEW_RESULT_INVALID",
                tr(
                    "PaperWorkShell rejected a stale scan-review preview. Update the preview again."
                ),
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
                tr("PaperWorkShell could not complete scan review. Try again."),
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
                    tr("The preview did not finish safely. Update the preview again."),
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
                tr("The original preview did not finish safely. Update the preview again."),
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
                    tr("The preview did not finish safely. Update the preview again."),
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
                tr("PaperWorkShell could not verify the applied scan-review state safely."),
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
                    message=tr(
                        "PaperWorkShell applied the case change, but could not close scan review safely."
                    ),
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
                tr("PaperWorkShell could not apply those adjustments. Try again."),
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
        if active.kind is _LongOperationKind.IMPORT:
            self._latest_import_progress = progress
            self._import_presentation = _ImportPresentationState.ACTIVE
            self._update_organizer_guidance()
        elif active.kind is _LongOperationKind.QUALITY:
            quality_message = tr(
                "Checking this saved revision — {value1}: {value2} of {value3}. Results remain automatic suggestions.",
                value1=tr(progress.phase),
                value2=progress.completed,
                value3=progress.total,
            )
            self._quality_summary.setText(quality_message)
            self._quality_summary.setAccessibleName(
                tr("Page quality review status. {value1}", value1=quality_message)
            )
            self._quality_summary.setAccessibleDescription(quality_message)
        message = tr(
            "{value1} {value2}: {value3} of {value4}.",
            value1=tr(progress.operation),
            value2=tr(progress.phase),
            value3=progress.completed,
            value4=progress.total,
        )
        if progress.total == 1 and progress.completed == 0:
            message = tr(
                "{value1}: {value2}…", value1=tr(progress.operation), value2=tr(progress.phase)
            )
            self._progress.setAccessibleDescription(message)
            set_static_busy(self._progress)
        else:
            self._progress.setAccessibleDescription(
                tr(
                    "{value1} {value2}, {value3} of {value4}.",
                    value1=tr(progress.operation),
                    value2=tr(progress.phase),
                    value3=progress.completed,
                    value4=progress.total,
                )
            )
            self._progress.setFormat(
                tr(
                    "{value1}: {value2} — %v of %m",
                    value1=tr(progress.operation),
                    value2=tr(progress.phase),
                )
            )
            self._progress.setRange(0, progress.total)
            self._progress.setValue(progress.completed)
        self._progress.setVisible(True)
        cancelable = progress.cancelable and not active.cancellation.cancelled()
        self._cancel_button.setVisible(progress.cancelable)
        self._cancel_button.setEnabled(cancelable)
        self._set_status_text(message)
        self._announcer.progress(
            message,
            key=f"{progress.operation}:{progress.phase}",
            completed=progress.completed,
            total=progress.total,
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
        if (
            active is None
            or active.operation_id != operation_id
            or (
                active.cancellation.cancelled() and active.kind is not _LongOperationKind.PDF_EXPORT
            )
        ):
            return
        if active.kind is _LongOperationKind.PREVIEW and not self._preview_batch_complete:
            return
        if active.kind is _LongOperationKind.QUALITY and self._pending_failure_focus == "quality":
            return
        if active.kind is _LongOperationKind.IMPORT:
            self._hide_error()
            self._import_presentation = _ImportPresentationState.IDLE
            self._latest_import_progress = None
            self._update_organizer_guidance()
        if active.kind is not _LongOperationKind.PREVIEW:
            result = (
                self._last_export_summary + " " + message
                if self._pdf_export_saved_id == operation_id
                else message
            )
            self._last_result_label.setText(result)
            self._last_result_label.show()
        self._announce(message)

    def _reject_preview_result(self, operation_id: int) -> None:
        self._failed(
            UiFailure(
                code="PWS_UI_PREVIEW_RESULT_INVALID",
                message=tr(
                    "PaperWorkShell could not display the preview safely. Retry the selected preview."
                ),
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
            or active.kind
            not in {
                _LongOperationKind.IMPORT,
                _LongOperationKind.SIGNATURE_IMAGE,
                _LongOperationKind.PDF_EXPORT,
            }
        ):
            return
        self._case_reopen_required = True
        if active.kind is _LongOperationKind.IMPORT:
            self._first_png_import_preview = None
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
        message = tr("{value1} starting", value1=tr(operation_name))
        self._progress.setAccessibleDescription(message)
        set_static_busy(self._progress)
        self._progress.setFormat(message)
        self._progress.setVisible(True)

    @Slot(int, str)
    def _operation_cancelled(self, operation_id: int, message: str) -> None:
        active = self._active_operation
        if active is None or active.operation_id != operation_id:
            return
        if active.kind is _LongOperationKind.IMPORT:
            self._first_png_import_preview = None
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

    @Slot(int, object, object, object)
    def _first_png_import_saved(
        self, operation_id: int, case_id: UUID, revision: int, page_id: UUID
    ) -> None:
        active = self._active_operation
        if (
            active is None
            or active.kind is not _LongOperationKind.IMPORT
            or active.operation_id != operation_id
            or active.cancellation.cancelled()
            or self._first_png_import_origin != (case_id, revision - 1)
        ):
            return
        target = _FirstPngPreviewTarget(case_id, revision, page_id)
        if self._first_png_preview_is_current(target):
            self._first_png_import_preview = target

    @Slot(int)
    def _operation_finished(self, operation_id: int) -> None:
        active = self._active_operation
        if active is None or active.operation_id != operation_id:
            return
        observation = phase_observer.active
        if (
            observation is not None
            and not observation.terminal
            and (
                (observation.import_id == operation_id and observation.page_id is None)
                or observation.preview_id == (operation_id, self._thumbnail_generation)
            )
        ):
            observation.terminal = True
        finished_preview = active.kind is _LongOperationKind.PREVIEW
        finished_export = active.kind in {
            _LongOperationKind.PDF_EXPORT,
            _LongOperationKind.REMOVAL_REVIEW,
            _LongOperationKind.REMOVAL_EXPORT,
        }
        finished_quality = active.kind is _LongOperationKind.QUALITY
        quality_result_focus_requested = finished_quality and self._quality_result_focus_requested
        preview_batch_complete = self._preview_batch_complete
        first_png_preview = None
        if active.kind is _LongOperationKind.IMPORT:
            if not active.cancellation.cancelled():
                first_png_preview = self._first_png_import_preview
            self._first_png_import_origin = None
            self._first_png_import_preview = None
        if finished_preview:
            self._model.finish_preview_loading(self._active_preview_page_ids)
        self._finish_active_operation()
        self._refresh_trace_document()
        if finished_quality:
            self._active_quality_binding = None
            self._quality_result_focus_requested = False
        if self._closing_requested:
            self._pending_failure_focus = None
            return
        if self._pending_package_review:
            self._pending_failure_focus = None
            self._start_package_review()
            return
        if finished_export:
            self._set_controls_enabled(True)
            self._pending_failure_focus = "pdf_export"
        if active.kind is _LongOperationKind.REMOVAL_REVIEW and active.cancellation.cancelled():
            self._pending_removal_review = None
            self._announce(tr("Removal export review cancelled. No derivative was created."))
        if self._show_pending_removal_review():
            self._pending_failure_focus = None
            return
        if self._show_pending_redaction_export_report():
            self._pending_failure_focus = None
            return
        pending_scan_review = self._pending_scan_review_page_id
        pending_quality = self._pending_quality_request
        pending_signature_image = (
            finished_preview
            and self._signature_image_source_path is not None
            and self._signature_image_page_id is not None
            and self._signature_image_prepare_operation_id is None
            and self._signature_image_dialog is None
        )
        if pending_signature_image:
            self._pending_failure_focus = None
            if self._selected_page_id() != self._signature_image_page_id:
                self._clear_signature_image_source()
                self._announce(
                    tr(
                        "The selected page changed before the signature image could be prepared. Select a page and try again."
                    ),
                    assertive=True,
                )
                return
            self._start_signature_image_prepare()
            return
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
                            tr(
                                "The selected page changed before scan review opened. Select a page and try again."
                            )
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
        if first_png_preview is not None and self._first_png_preview_is_current(first_png_preview):
            self._schedule_preview_refresh(immediate=True, import_preview=first_png_preview)
        elif deferred_preview is not None and not deferred_preview.autosave_pending:
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
        if failure.code == "REDACTION_PIPELINE_DISABLED" and (
            correlated_kind is _LongOperationKind.PDF_EXPORT or active is None
        ):
            self._queue_redaction_export_report()
            if self._show_pending_redaction_export_report():
                return
        if correlated_kind is _LongOperationKind.PREVIEW:
            for page_id in self._active_preview_page_ids:
                self._model.set_preview_failed(page_id)
            self._preview_batch_complete = False
        elif correlated_kind is _LongOperationKind.IMPORT:
            self._first_png_import_preview = None
            self._latest_import_progress = None
            self._import_presentation = _ImportPresentationState.FAILED
        elif correlated_kind is _LongOperationKind.QUALITY:
            self._cancel_button.setEnabled(False)
            self._model.clear_quality_review()
            self._quality_findings_model.set_snapshot(None)
            self._quality_presentation = _QualityPresentationState.FAILED
            self._update_quality_summary()
        failure_message = tr(failure.message)
        number = failure.affected_page_number
        if (
            correlated_kind is _LongOperationKind.PDF_EXPORT
            and self._state is not None
            and type(number) is int
            and 1 <= number <= len(self._state.pages)
        ):
            failure_message = tr(
                "{value1}. {value2}",
                value1=self._page_label(self._state.pages[number - 1]),
                value2=failure_message,
            )
        self._set_error_text(f"{failure.code}: {failure_message}")
        self._error_label.setVisible(True)
        pending_save = self._state is not None and self._state.autosave_pending
        if failure.retryable and pending_save:
            self._retry_save_button.setVisible(True)
            self._saved_label.setText(tr("Changes are unsaved. Retry saving."))
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
        self._announce(tr("Action failed. {value1}", value1=failure_message), assertive=True)
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
        self._dismiss_core_overlay_tools_after_shutdown_refusal()
        self._dismiss_redaction_tools_after_shutdown_refusal()
        self._dismiss_checklist_after_shutdown_refusal()
        self._dismiss_portal_constraints_after_shutdown_refusal()
        self._update_organizer_guidance()
        if refusal is ShutdownRefusalKind.CLEANUP_RETRY:
            self._hide_retry_save()
            self._retry_close_button.setVisible(True)
            self._retry_close_button.setEnabled(True)
            self._announce(
                tr(
                    "The application remains open because secure cleanup could not finish. Retry closing PaperWorkShell."
                ),
                assertive=True,
            )
            QTimer.singleShot(0, self._focus_retry_close_after_shutdown_refusal)
            return
        self._set_controls_enabled(True)
        self._hide_retry_close()
        self._announce(
            tr(
                "The application remains open because queued changes could not be saved. Retry saving changes."
            ),
            assertive=True,
        )
        self._retry_save_button.setVisible(True)
        self._saved_label.setText(tr("Changes are unsaved. Retry saving."))
        QTimer.singleShot(0, self._focus_retry_save_after_shutdown_refusal)

    def _focus_retry_save_after_shutdown_refusal(self) -> None:
        if self._closing_requested or not self._retry_save_button.isVisible():
            return
        self.activateWindow()
        self._retry_save_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._ensure_widget_visible(self._retry_save_button)

    def _focus_retry_close_after_shutdown_refusal(self) -> None:
        if self._closing_requested or not self._retry_close_button.isVisible():
            return
        self.activateWindow()
        self._retry_close_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._ensure_widget_visible(self._retry_close_button)

    @Slot()
    def shutdown_wait_timed_out(self) -> None:
        """Keep the closing surface alive while a bounded backend call winds down."""

        self._announce(
            tr("PaperWorkShell is still finishing the current operation before it can close."),
            assertive=True,
        )

    def _request_previews(self, state: OrganizerViewState) -> None:
        if (
            self._case_close_pending
            or self._case_reopen_required
            or self._scan_review_is_opening_or_active()
            or self._core_overlay_tools_is_opening_or_active()
            or self._redaction_tools_is_opening_or_active()
            or self._checklist_is_opening_or_active()
            or self._portal_constraints_is_opening_or_active()
            or self._package_export_is_opening_or_active()
            or self._quality_is_pending_or_running()
            or self._signature_image_is_opening_or_active()
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
        observation = phase_observer.active
        if observation is not None and self._state is not None:
            observation.preview_dispatch(
                operation.operation_id,
                generation,
                self._state.case_id,
                self._state.revision,
                page_ids,
                self._selected_page_id(),
            )
        self._commands.thumbnails_requested.emit(
            operation.operation_id,
            generation,
            page_ids,
            _THUMBNAIL_WIDTH,
            _THUMBNAIL_HEIGHT,
            operation.cancellation,
        )

    def _first_png_preview_is_current(self, target: _FirstPngPreviewTarget) -> bool:
        state = self._state
        return (
            state is not None
            and state.case_id == target.case_id
            and state.revision == target.revision
            and not state.autosave_pending
            and len(state.pages) == 1
            and state.pages[0].page_id == target.page_id
            and state.pages[0].preview_supported
            and self._selected_page_id() == target.page_id
            and self._model.preview_state(target.page_id) is PreviewState.NOT_LOADED
        )

    def _schedule_preview_refresh(
        self,
        _value: int | None = None,
        *,
        immediate: bool = False,
        import_preview: _FirstPngPreviewTarget | None = None,
    ) -> None:
        if self._case_close_pending:
            return
        if (
            self._active_operation is not None
            and self._active_operation.kind is _LongOperationKind.PDF_EXPORT
        ):
            return
        state = self._state
        if (
            state is None
            or state.case_id is None
            or state.autosave_pending
            or self._closing_requested
            or self._case_reopen_required
            or self._scan_review_is_opening_or_active()
            or self._core_overlay_tools_is_opening_or_active()
            or self._redaction_tools_is_opening_or_active()
            or self._checklist_is_opening_or_active()
            or self._portal_constraints_is_opening_or_active()
            or self._package_export_is_opening_or_active()
            or self._quality_is_pending_or_running()
        ):
            return
        queued_import = self._queued_import_preview
        if (
            import_preview is None
            and queued_import is not None
            and self._preview_refresh_timer.isActive()
            and self._first_png_preview_is_current(queued_import)
        ):
            # Focus and layout can emit scroll events before the queued timeout.
            return
        self._queued_import_preview = import_preview
        self._preview_refresh_timer.start(0 if immediate else _PREVIEW_REFRESH_DEBOUNCE_MS)

    @Slot()
    def _refresh_visible_previews(self) -> None:
        import_preview = self._queued_import_preview
        self._queued_import_preview = None
        state = self._state
        if (
            self._case_close_pending
            or state is None
            or state.case_id is None
            or state.autosave_pending
            or self._closing_requested
            or self._case_reopen_required
            or self._scan_review_is_opening_or_active()
            or self._core_overlay_tools_is_opening_or_active()
            or self._redaction_tools_is_opening_or_active()
            or self._checklist_is_opening_or_active()
            or self._portal_constraints_is_opening_or_active()
            or self._package_export_is_opening_or_active()
            or self._quality_is_pending_or_running()
        ):
            return
        if import_preview is not None and (
            self._active_operation is not None
            or not self._first_png_preview_is_current(import_preview)
        ):
            self._schedule_preview_refresh()
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
        self._trace_split.select_pane("inspector")
        self._trace_inspector_tabs.setCurrentIndex(1)
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
                tr("Stopping the current page preview before the page quality check starts.")
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
                tr("The page quality results no longer match this organizer. Run the check again."),
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
        self._announce(
            tr(
                "Showing page {value1}. Needs review: {value2}.",
                value1=finding.page_position,
                value2=tr(finding.title),
            )
        )

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
        visible_page_view = (
            self._trace_arrangement if self._trace_lens == "arrange" else self._page_view
        )
        viewport = visible_page_view.viewport().rect()
        visible_rows = tuple(
            row
            for row in range(self._model.rowCount())
            if visible_page_view.visualRect(self._model.index(row, 0)).intersects(viewport)
        )
        rows = _bounded_preview_rows(
            self._model.rowCount(),
            visible_rows,
            self._page_view.currentIndex().row(),
            overscan_rows=self._resource_policy.preview_prefetch_radius_pages,
            maximum_count=self._resource_policy.maximum_preview_request_pages,
        )
        return tuple(state.pages[row].page_id for row in rows if state.pages[row].preview_supported)

    def _file_dialog_options(self, options: QFileDialog.Option | None = None) -> QFileDialog.Option:
        """Keep assisted pickers inside Qt's foreground and privacy ownership."""

        selected = QFileDialog.Option(0) if options is None else options
        if self._assisted_session.active:
            return selected | QFileDialog.Option.DontUseNativeDialog
        return selected

    def _choose_import(self) -> None:
        if (
            not self._controls_enabled
            or self._closing_requested
            or not self._import_button.isEnabled()
            or self._scan_review_is_opening_or_active()
            or self._signature_image_is_opening_or_active()
            or self._state is None
            or self._state.case_id is None
        ):
            return
        if self._import_is_pending_or_running():
            QApplication.beep()
            self._announce(
                tr("An import is already queued or running. Cancel it or wait for it to finish."),
                assertive=True,
            )
            return
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            tr("Add file…"),
            "",
            tr("Supported documents (*.pdf *.png *.jpg *.jpeg);;All files (*)"),
            options=self._file_dialog_options(),
        )
        if not selected:
            observation = phase_observer.active
            if observation is not None and observation.import_id is None:
                phase_observer.active = None
            self._announce(tr("Import selection cancelled."))
            return
        source_path = Path(selected)
        self._queue_import(source_path, source_path.name)

    def _page_label(self, page: OrganizerPageView, *, case_order: bool = False) -> str:
        name = getattr(page, "source_display_name", "") or tr(
            "Document {value1}", value1=page.document_number
        )
        message = (
            mark("{value1} — source page {value2}, case page {value3}")
            if case_order
            else mark("{value1} — source page {value2}, output page {value3}")
        )
        return tr(
            message,
            value1=name,
            value2=page.source_page_number or 1,
            value3=page.position,
        )

    def _ask_pdf_export_options(self) -> bool | None:
        options = QDialog(self)
        options.setObjectName("ordinaryPdfExportOptions")
        options.setWindowTitle(tr("Export PDF"))
        outer = QVBoxLayout(options)
        body = QWidget()
        body.setObjectName("ordinaryPdfExportContent")
        content = QVBoxLayout(body)
        scroll = QScrollArea()
        scroll.setObjectName("ordinaryPdfExportScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(body)
        adapt_localized_content(scroll)
        outer.addWidget(scroll, 1)
        info = QLabel(
            tr("Save the pages in their current order as one PDF. Originals are preserved.")
        )
        info.setWordWrap(True)
        content.addWidget(info)
        sources = QListWidget()
        sources.setObjectName("ordinaryExportPages")
        sources.setAccessibleName(tr("Pages to export in order"))
        if self._state is not None:
            for page in self._state.pages:
                sources.addItem(self._page_label(page))
        sources.setMaximumHeight(sources.fontMetrics().height() * 4 + 24)
        content.addWidget(sources)
        language = QLabel(
            tr(
                "Recognition language: {value1}",
                value1=", ".join(
                    tr(OCR_LANGUAGE_DISPLAY_NAMES[code])
                    for code in self._selected_ocr_language_codes
                ),
            )
        )
        language.setWordWrap(True)
        content.addWidget(language)
        searchable = QCheckBox(tr("Make scanned text searchable"))
        searchable.setObjectName("makeScannedTextSearchable")
        content.addWidget(searchable)
        explanation = QLabel(
            tr(
                "Adds searchable text using local OCR and your selected language (English by default). Processing stays on this computer. Existing digital text is retained. Recognition can make mistakes."
            )
        )
        explanation.setWordWrap(True)
        content.addWidget(explanation)
        limits = QLabel(
            tr(
                "Text and marks on PDF pages are included. OCR with marks, and marks on pictures, cannot yet be combined in one export. Scan improvements require searchable export. Unsupported combinations are refused before writing a file."
            )
        )
        limits.setWordWrap(True)
        content.addWidget(limits)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(tr("Choose PDF file…"))
        buttons.accepted.connect(options.accept)
        buttons.rejected.connect(options.reject)
        adapt_dialog_button_box(buttons)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(tr("Choose PDF file…"))
        outer.addWidget(buttons)
        screen = options.screen().availableGeometry()
        options.resize(
            min(620, max(1, screen.width() - 48)), min(580, max(1, screen.height() - 48))
        )
        if options.exec() != QDialog.DialogCode.Accepted:
            return None
        return searchable.isChecked()

    def _choose_pages_pdf_export(self) -> None:
        if not self._pages_pdf_export_button.isEnabled() or self._active_operation is not None:
            return
        make_searchable = self._ask_pdf_export_options()
        if make_searchable is None:
            return
        selected, _filter = QFileDialog.getSaveFileName(
            self,
            tr("Export PDF…"),
            "",
            tr("PDF document (*.pdf)"),
            options=self._file_dialog_options(QFileDialog.Option.DontConfirmOverwrite),
        )
        if not selected:
            self._announce(tr("PDF export selection cancelled."))
            return
        self._preview_refresh_timer.stop()
        self._autosave_timer.stop()
        self._hide_error()
        operation = self._begin_long_operation(_LongOperationKind.PDF_EXPORT)
        self._pdf_export_destination = Path(selected)
        self._trace_destination_label.setText(
            tr("Export destination · {value1}", value1=str(self._pdf_export_destination))
        )
        self._trace_destination_label.show()
        self._pdf_export_saved_id = None
        self._set_controls_enabled(False)
        self._cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._ensure_widget_visible(self._cancel_button)
        self._announce(tr("PDF export started. You can cancel this operation."))
        signal = (
            self._commands.searchable_pdf_export_requested
            if make_searchable
            else self._commands.pages_pdf_export_requested
        )
        signal.emit(
            operation.operation_id,
            Path(selected),
            operation.cancellation,
        )

    @Slot(int, int)
    def _pages_pdf_export_saved(self, operation_id: int, page_count: int) -> None:
        active = self._active_operation
        destination = self._pdf_export_destination
        if (
            active is None
            or active.operation_id != operation_id
            or active.kind is not _LongOperationKind.PDF_EXPORT
            or destination is None
            or page_count < 1
        ):
            return
        self._pdf_export_saved_id = operation_id
        self._last_export_path = destination
        self._last_export_summary = tr(
            "Saved {value1} — {value2} pages.", value1=destination.name, value2=page_count
        )
        self._last_result_label.setText(self._last_export_summary)
        self._last_result_label.setToolTip(str(destination))
        self._last_result_label.show()
        self._open_pdf_button.show()
        self._open_export_folder_button.show()

    def _open_last_export(self, folder: bool) -> None:
        path = self._last_export_path
        if path is None:
            return
        if (
            not folder
            and self._trace_evidence is not None
            and self._trace_evidence.destination == path
        ):
            self._request_trace_output_check(open_when_verified=True)
            return
        self._open_export_path(path.parent if folder else path)

    def _open_export_path(self, path: Path) -> None:
        if self._assisted_session.locked:
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.absolute()))):
            self._announce(
                tr(
                    "The saved output could not be opened. Use the displayed destination in File Explorer."
                ),
                assertive=True,
            )

    def _queue_import(self, source_path: Path, display_name: str) -> None:
        if (
            self._case_reopen_required
            or self._scan_review_is_opening_or_active()
            or self._signature_image_is_opening_or_active()
        ):
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
                    tr(
                        "An import is already queued or running. Cancel it or wait for it to finish."
                    ),
                    assertive=True,
                )
                return
            self._deferred_import = (source_path, display_name)
            self._set_import_file_target(display_name)
            self._import_presentation = _ImportPresentationState.QUEUED
            self._latest_import_progress = None
            self._update_organizer_guidance()
            self._update_import_availability()
            self._cancel_active_preview()
            self._announce(tr("Stopping the current preview before import starts."))
            return
        self._start_import(source_path, display_name)

    def _start_import(self, source_path: Path, display_name: str) -> None:
        self._hide_error()
        self._set_import_file_target(display_name)
        state = self._state
        self._first_png_import_origin = (
            (state.case_id, state.revision)
            if state is not None
            and state.case_id is not None
            and state.revision is not None
            and not state.autosave_pending
            and not state.pages
            else None
        )
        self._first_png_import_preview = None
        operation = self._begin_long_operation(_LongOperationKind.IMPORT)
        self._announce(tr("Import started. You can cancel this operation."))
        observation = phase_observer.active
        if observation is not None and self._state is not None:
            observation.dispatch(operation.operation_id, self._state.case_id, self._state.revision)
        self._commands.import_requested.emit(
            operation.operation_id,
            source_path,
            display_name,
            operation.cancellation,
        )

    def _toggle_phase_observation(self) -> None:
        observation = phase_observer.active
        if observation is not None and not observation.terminal:
            phase_observer.active = None
            self._announce("Synthetic PNG phase observation disarmed.")
            return
        state = self._state
        if (
            state is None
            or state.case_id is None
            or state.revision != 0
            or state.pages
            or state.autosave_pending
            or self._active_operation is not None
            or self._closing_requested
            or self._assisted_session.active
        ):
            self._announce("Open a new saved empty case to arm phase observation.", assertive=True)
            return
        try:
            clock, frequency = phase_observer.windows_clock()
            phase_observer.active = phase_observer.Observation(state.case_id, 0, clock, frequency)
        except Exception:
            self._announce("The engineering phase clock is unavailable.", assertive=True)
            return
        self._announce("Synthetic PNG phase observation armed for the next import.")

    def _save_phase_observation(self) -> None:
        observation = phase_observer.active
        if observation is None or self._active_operation is not None:
            self._announce("No synthetic phase receipt is ready.", assertive=True)
            return
        try:
            receipt = observation.receipt()
        except Exception:
            receipt = None
        if receipt is None:
            self._announce("No synthetic phase receipt is ready.", assertive=True)
            return
        review = QMessageBox(self)
        review.setWindowTitle(tr("Review synthetic PNG phase receipt"))
        review.setText(tr("Review the timing fields before saving this local engineering receipt."))
        review.setDetailedText(receipt.decode("ascii"))
        review.setStandardButtons(
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel
        )
        review.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if review.exec() != int(QMessageBox.StandardButton.Save):
            return
        directory = Path.home() / "PWS0811PhaseEvidence"
        try:
            directory.mkdir(exist_ok=True)
            info = directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or (
                getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise OSError
            selected, _filter = QFileDialog.getSaveFileName(
                self,
                "Save synthetic PNG phase receipt",
                str(directory / "phase-receipt.json"),
                "Engineering receipt (*.json)",
                options=QFileDialog.Option.DontConfirmOverwrite,
            )
            if not selected:
                return
            destination = Path(selected)
            if (
                destination.parent != directory
                or destination.suffix != ".json"
                or not destination.stem.replace("-", "").replace("_", "").isalnum()
            ):
                raise OSError
            with destination.open("xb") as output:
                output.write(receipt)
        except OSError:
            self._announce(
                "Choose a new JSON file in the engineering evidence folder.", assertive=True
            )
            return
        phase_observer.active = None
        self._announce("Synthetic PNG phase receipt saved.")

    def _set_import_file_target(self, display_name: str) -> None:
        """Expose only the user-chosen basename, outside scrolling workflow content."""

        name = Path(display_name).name
        message = tr("Import file: {value1}", value1=name)
        self._import_file_label.setText(message)
        self._import_file_label.setAccessibleName(message)
        self._import_file_label.setAccessibleDescription(message)

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
            self._announce(tr("Select a case before choosing Open case."), assertive=True)
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
        name = self._ask_case_name(tr("New case"), "")
        if name is not None:
            self._commands.create_named_case_requested.emit(name)

    def _ask_case_name(self, title: str, initial: str) -> str | None:
        dialog = QInputDialog(self)
        dialog.setWindowTitle(title)
        dialog.setObjectName("caseNameDialog")
        dialog.setLabelText(tr("A case keeps your documents and edits together.\nCase name"))
        dialog.setTextValue(initial)
        dialog.setOkButtonText(tr("Create case") if title == tr("New case") else tr("Save name"))
        field = dialog.findChild(QLineEdit)
        if field is not None:
            field.setObjectName("caseNameField")
            field.setAccessibleName(tr("Case name"))
            field.setPlaceholderText(tr("Housing application"))
            field.setMaxLength(200)
        for label in dialog.findChildren(QLabel):
            label.setWordWrap(True)
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        screen = dialog.screen().availableGeometry()
        width = min(520, max(1, screen.width() - 48))
        available_height = max(1, screen.height() - 48)
        dialog.resize(width, min(340, available_height))
        layout = dialog.layout()
        if layout is not None:
            # Give the footer its actual available width before it measures
            # whether the complete button labels need a row or a stack.
            layout.activate()
        button = dialog.findChild(QDialogButtonBox)
        if button is not None:
            adapt_dialog_button_box(button)
            dialog.setOkButtonText(
                tr("Create case") if title == tr("New case") else tr("Save name")
            )
            ok = button.button(QDialogButtonBox.StandardButton.Ok)
            ok.setEnabled(bool(initial.strip()))
            dialog.textValueChanged.connect(lambda value: ok.setEnabled(bool(value.strip())))
        dialog.resize(width, min(max(160, dialog.sizeHint().height()), available_height))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.textValue().strip() or None

    def _request_rename_case(self) -> None:
        if (
            self._state is None
            or self._state.case_id is None
            or not self._rename_case_button.isEnabled()
        ):
            return
        name = self._ask_case_name(tr("Rename case"), self._state.case_name)
        if name is not None:
            self._commands.rename_case_requested.emit(name)

    def _request_close_case(self) -> None:
        if (
            not self._controls_enabled
            or self._closing_requested
            or self._scan_review_is_opening_or_active()
            or self._quality_is_pending_or_running()
            or self._signature_image_is_opening_or_active()
            or self._state is None
            or self._state.case_id is None
        ):
            return
        self._case_close_pending = True
        self._cancel_trace_output_check()
        self._preview_refresh_timer.stop()
        self._autosave_timer.stop()
        self._deferred_import = None
        self._deferred_preview_state = None
        self._cancel_active_operation_silently()
        self._set_controls_enabled(False)
        if self._assisted_session.active:
            self._assisted_lock_button.setEnabled(True)
            self._assisted_lock_button.setFocus(Qt.FocusReason.OtherFocusReason)
            self._ensure_widget_visible(self._assisted_lock_button)
        else:
            self._status_label.setFocus(Qt.FocusReason.OtherFocusReason)
            self._ensure_widget_visible(self._status_label)
        self._announce(tr("Closing the case. Saving changes and finishing cleanup."))
        self._commands.close_case_requested.emit()

    @Slot()
    def _case_close_finished(self) -> None:
        """Restore interaction only after the queued close has actually finished."""

        if not self._case_close_pending:
            return
        self._case_close_pending = False
        if not self._closing_requested:
            self._set_controls_enabled(True)
            if self._state is not None and self._state.autosave_pending:
                self._autosave_timer.start(_AUTOSAVE_DEBOUNCE_MS)
            modal = QApplication.activeModalWidget()
            if modal is None or not modal.isVisible():
                if (
                    self._error_label.isVisible()
                    and self._state is not None
                    and (self._state.case_id is not None)
                ):
                    self._focus_error_recovery(
                        self._retry_save_button
                        if self._retry_save_button.isVisible()
                        else self._close_case_button
                    )
                else:
                    self._focus_safe_control()

    def _request_autosave_flush(self) -> None:
        state = self._state
        if (
            self._case_close_pending
            or self._case_reopen_required
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
            self._case_close_pending
            or self._case_reopen_required
            or state is None
            or state.case_id is None
            or not state.autosave_pending
        ):
            return
        self._deferred_preview_state = None
        self._cancel_active_preview()
        self._saved_label.setText(tr("Saving changes…"))
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
        if state is not None and 0 <= row < len(state.pages):
            self._selection_summary.setText(
                tr("Selected: {value1}", value1=self._page_label(state.pages[row]))
            )
        else:
            self._selection_summary.setText(tr("Select a page to arrange or edit it."))
        scan_review_busy = self._scan_review_is_opening_or_active()
        quality_busy = self._quality_is_pending_or_running()
        overlay_tools_busy = (
            self._core_overlay_tools_is_opening_or_active()
            or self._redaction_tools_is_opening_or_active()
        )
        checklist_busy = self._checklist_is_opening_or_active()
        portal_constraints_busy = self._portal_constraints_is_opening_or_active()
        package_export_busy = self._package_export_is_opening_or_active()
        signature_image_busy = self._signature_image_is_opening_or_active()
        self._rename_case_button.setEnabled(
            self._controls_enabled
            and state is not None
            and state.case_id is not None
            and self._active_operation is None
        )
        self._close_case_button.setEnabled(
            self._controls_enabled
            and state is not None
            and state.case_id is not None
            and not scan_review_busy
            and not quality_busy
            and not overlay_tools_busy
            and not checklist_busy
            and not portal_constraints_busy
            and not package_export_busy
            and not signature_image_busy
        )
        editable = (
            self._controls_enabled
            and not self._case_reopen_required
            and not self._import_is_pending_or_running()
            and not scan_review_busy
            and not quality_busy
            and not overlay_tools_busy
            and not checklist_busy
            and not portal_constraints_busy
            and not package_export_busy
            and not signature_image_busy
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
        self._core_overlay_tools_button.setEnabled(has_selection)
        self._redaction_tools_button.setEnabled(has_selection)
        self._place_signature_image_button.setEnabled(has_selection)
        self._remove_signature_image_button.setEnabled(has_selection)
        self._checklist_button.setEnabled(
            editable and state is not None and state.case_id is not None
        )
        self._portal_constraints_button.setEnabled(
            editable and state is not None and state.case_id is not None
        )
        package_enabled = (
            editable
            and state is not None
            and state.case_id is not None
            and not state.autosave_pending
        ) or (
            self._controls_enabled
            and self._package_release_failed
            and self._package_cancel_operation_id is None
            and not self._closing_requested
        )
        self._package_export_button.setEnabled(package_enabled)
        self._package_export_button.setText(
            tr("Retry closing package review")
            if self._package_release_failed
            else tr("Export checklist package…")
        )
        self._package_export_button.setAccessibleName(self._package_export_button.text())
        self._package_export_action.setEnabled(package_enabled)
        pages_pdf_enabled = (
            editable
            and state is not None
            and state.case_id is not None
            and bool(state.pages)
            and not state.autosave_pending
            and self._active_operation is None
        )
        self._pages_pdf_export_button.setEnabled(pages_pdf_enabled)
        recipe_enabled = pages_pdf_enabled and state is not None and state.trace_plan is not None
        self._trace_save_recipe_button.setEnabled(recipe_enabled)
        self._trace_apply_recipe_button.setEnabled(recipe_enabled)
        self._trace_script_button.setEnabled(recipe_enabled)
        export_label = (
            tr("Export {count}-page PDF…", count=page_count) if page_count else tr("Export PDF…")
        )
        self._pages_pdf_export_button.setText(export_label)
        self._pages_pdf_export_button.setAccessibleName(export_label)
        self._pages_pdf_export_action.setEnabled(pages_pdf_enabled)
        removal_enabled = (
            pages_pdf_enabled
            and state is not None
            and len(state.pages) == 1
            and bool(state.removal_page_ids)
            and not self._redaction_tools_is_opening_or_active()
        )
        self._removal_export_button.setEnabled(removal_enabled)
        self._removal_export_action.setEnabled(removal_enabled)
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
            self._announce(tr("Stopping the current page preview before scan review opens."))
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
        self._announce(tr("Opening scan review. Nothing has been applied."))
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
        if self._compression_dialog is not None:
            self._model.evict_previews(maximum_bytes=0)
            return
        review_bytes = self._scan_review_original_bytes + self._scan_review_adjusted_bytes
        remaining = max(
            0,
            self._resource_policy.thumbnail_preview_cache_bytes - review_bytes,
        )
        self._model.evict_previews(maximum_bytes=remaining)

    def _request_core_overlay_tools(self) -> None:
        """Open the keyboard-complete core form-mark editor for the selected page."""

        if not self._core_overlay_tools_button.isEnabled():
            return
        page_id = self._selected_page_id()
        if page_id is None or not self._prepare_for_mutation():
            return
        if (
            self._core_overlay_tools_is_opening_or_active()
            or self._checklist_is_opening_or_active()
            or self._portal_constraints_is_opening_or_active()
        ):
            return
        operation_id = self._allocate_operation_id()
        self._core_overlay_tools_start_operation_id = operation_id
        self._autosave_timer.stop()
        self._preview_refresh_timer.stop()
        self._deferred_preview_state = None
        self._hide_error()
        self._update_import_availability()
        self._update_page_actions()
        self._update_quality_actions()
        self._announce(tr("Opening Add text or marks. No changes have been applied."))
        self._commands.core_overlay_tools_begin_requested.emit(operation_id, page_id)

    @Slot(int, object)
    def _core_overlay_tools_started(self, operation_id: int, editor_state: object) -> None:
        expected = self._core_overlay_tools_start_operation_id
        if operation_id != expected:
            return
        self._core_overlay_tools_start_operation_id = None
        if (
            self._closing_requested
            or type(editor_state) is not CoreOverlayToolsState
            or self._state is None
            or editor_state.case_id != self._state.case_id
            or editor_state.manifest_revision != self._state.revision
            or editor_state.page_id != self._selected_page_id()
            or not is_core_overlay_announcement_safe(editor_state.announcement)
            or self._core_overlay_tools_dialog is not None
        ):
            if not self._closing_requested and self._core_overlay_tools_dialog is None:
                cancel_operation_id = self._allocate_operation_id()
                self._core_overlay_tools_cancel_operation_id = cancel_operation_id
                self._commands.core_overlay_tools_abort_requested.emit(cancel_operation_id)
            self._update_import_availability()
            self._update_page_actions()
            self._update_quality_actions()
            return
        matching_pages = tuple(
            page for page in self._state.pages if page.page_id == editor_state.page_id
        )
        if len(matching_pages) != 1:
            cancel_operation_id = self._allocate_operation_id()
            self._core_overlay_tools_cancel_operation_id = cancel_operation_id
            self._commands.core_overlay_tools_abort_requested.emit(cancel_operation_id)
            self._update_import_availability()
            self._update_page_actions()
            self._update_quality_actions()
            return
        transform, pointer_mapping_enabled = _overlay_editor_display_facts(matching_pages[0])
        preview = self._model.preview(editor_state.page_id)
        display_size = (
            RenderedImageSize(preview.width(), preview.height())
            if preview is not None and not preview.isNull()
            else None
        )
        dialog = CoreOverlayToolsDialog(
            editor_state,
            self,
            page_transform=transform,
            page_preview=preview if pointer_mapping_enabled else None,
            page_display_size=display_size,
            pointer_mapping_enabled=pointer_mapping_enabled,
            page_label=self._page_label(matching_pages[0]),
        )
        dialog.apply_requested.connect(self._apply_core_overlay_tools)
        dialog.finished.connect(self._core_overlay_tools_finished)
        self._core_overlay_tools_dialog = dialog
        self._update_import_availability()
        self._update_page_actions()
        self._update_quality_actions()
        dialog.open()

    @Slot(int, object)
    def _core_overlay_tools_start_failed(self, operation_id: int, failure: object) -> None:
        if operation_id != self._core_overlay_tools_start_operation_id:
            return
        self._core_overlay_tools_start_operation_id = None
        if self._state is not None and self._state.autosave_pending and not self._closing_requested:
            self._autosave_timer.start(_AUTOSAVE_DEBOUNCE_MS)
        self._update_import_availability()
        self._update_page_actions()
        self._update_quality_actions()
        self._show_core_overlay_tools_failure(failure)

    @Slot(object)
    def _apply_core_overlay_tools(self, editor_state: object) -> None:
        dialog = self._core_overlay_tools_dialog
        if (
            dialog is None
            or type(editor_state) is not CoreOverlayToolsState
            or editor_state is not dialog.state
            or self._core_overlay_tools_apply_operation_id is not None
        ):
            return
        operation_id = self._allocate_operation_id()
        self._core_overlay_tools_apply_operation_id = operation_id
        self._commands.core_overlay_tools_apply_requested.emit(operation_id, editor_state)

    @Slot(int, object)
    def _core_overlay_tools_applied(self, operation_id: int, state: object) -> None:
        if operation_id != self._core_overlay_tools_apply_operation_id:
            return
        self._core_overlay_tools_apply_operation_id = None
        dialog = self._core_overlay_tools_dialog
        if dialog is None:
            return
        if type(state) is not OrganizerViewState or not _core_overlay_apply_result_matches(
            dialog,
            state,
            self._state,
        ):
            dialog.set_apply_failed(
                "PWS_UI_RESPONSE_INVALID",
                tr("PaperWorkShell received an invalid save confirmation. Try saving again."),
            )
            return
        if dialog.set_apply_succeeded():
            self._announce(tr("Visual overlay changes applied and saved."))

    @Slot(int, object)
    def _core_overlay_tools_apply_failed(self, operation_id: int, failure: object) -> None:
        if operation_id != self._core_overlay_tools_apply_operation_id:
            return
        self._core_overlay_tools_apply_operation_id = None
        dialog = self._core_overlay_tools_dialog
        if dialog is None:
            return
        if type(failure) is UiFailure:
            dialog.set_apply_failed(failure.code, failure.message)
            return
        dialog.set_apply_failed(
            "PWS_UI_RESPONSE_INVALID",
            tr("PaperWorkShell received an invalid save failure. Try saving again."),
        )

    @Slot(int)
    def _core_overlay_tools_finished(self, _result: int) -> None:
        dialog = self._core_overlay_tools_dialog
        if dialog is None:
            return
        state = dialog.state
        cancelled = (
            _result != int(QDialog.DialogCode.Accepted)
            and not dialog.commit_started
            and self._core_overlay_tools_apply_operation_id is None
        )
        dialog.deleteLater()
        self._core_overlay_tools_dialog = None
        self._core_overlay_tools_apply_operation_id = None
        if cancelled:
            operation_id = self._allocate_operation_id()
            self._core_overlay_tools_cancel_operation_id = operation_id
            self._commands.core_overlay_tools_cancel_requested.emit(operation_id, state)
        self._update_import_availability()
        self._update_page_actions()
        self._update_quality_actions()
        if (
            not cancelled
            and not self._closing_requested
            and self._core_overlay_tools_button.isEnabled()
        ):
            QTimer.singleShot(0, self._focus_core_overlay_tools_button_after_dialog)

    @Slot(int)
    def _core_overlay_tools_cancelled(self, operation_id: int) -> None:
        if operation_id != self._core_overlay_tools_cancel_operation_id:
            return
        self._core_overlay_tools_cancel_operation_id = None
        self._update_import_availability()
        self._update_page_actions()
        self._update_quality_actions()
        self._announce(tr("Visual overlay changes cancelled. Nothing was applied."))
        if not self._closing_requested and self._core_overlay_tools_button.isEnabled():
            QTimer.singleShot(0, self._focus_core_overlay_tools_button_after_dialog)

    def _focus_core_overlay_tools_button_after_dialog(self) -> None:
        self._focus_trace_invoker(self._core_overlay_tools_button)
        if self._closing_requested or not self._core_overlay_tools_button.isEnabled():
            return
        self.activateWindow()
        self._core_overlay_tools_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._ensure_widget_visible(self._core_overlay_tools_button)

    @Slot(int, object)
    def _core_overlay_tools_cancel_failed(self, operation_id: int, failure: object) -> None:
        if operation_id != self._core_overlay_tools_cancel_operation_id:
            return
        self._core_overlay_tools_cancel_operation_id = None
        self._update_import_availability()
        self._update_page_actions()
        self._update_quality_actions()
        self._show_core_overlay_tools_failure(failure)

    def _show_core_overlay_tools_failure(self, failure: object) -> None:
        code = failure.code if type(failure) is UiFailure else None
        message = failure.message if type(failure) is UiFailure else None
        safe_code, safe_message = safe_core_overlay_failure(code, message)
        self._set_error_text(f"{safe_code}: {safe_message}")
        self._error_label.setVisible(True)
        self._announce(
            tr("Add text or marks could not finish. {value1}", value1=safe_message),
            assertive=True,
        )
        self._focus_error_recovery(self._core_overlay_tools_button)

    def _request_redaction_tools(self) -> None:
        """Open the separate redaction mark intent editor for the selected page."""

        if not self._redaction_tools_button.isEnabled():
            return
        page_id = self._selected_page_id()
        if page_id is None or not self._prepare_for_mutation():
            return
        if (
            self._redaction_tools_is_opening_or_active()
            or self._core_overlay_tools_is_opening_or_active()
            or self._checklist_is_opening_or_active()
            or self._portal_constraints_is_opening_or_active()
        ):
            return
        operation_id = self._allocate_operation_id()
        self._redaction_tools_start_operation_id = operation_id
        self._autosave_timer.stop()
        self._preview_refresh_timer.stop()
        self._deferred_preview_state = None
        self._hide_error()
        self._update_import_availability()
        self._update_page_actions()
        self._update_quality_actions()
        self._announce(tr("Opening redaction mark editor. No information has been removed."))
        self._commands.redaction_tools_begin_requested.emit(operation_id, page_id)

    def _request_removal_export(self) -> None:
        if not self._removal_export_button.isEnabled() or self._active_operation is not None:
            return
        self._autosave_timer.stop()
        self._preview_refresh_timer.stop()
        self._hide_error()
        operation = self._begin_long_operation(_LongOperationKind.REMOVAL_REVIEW)
        self._set_controls_enabled(False)
        self._announce(
            tr("Preparing review of the saved removal marks. No file has been exported.")
        )
        self._commands.removal_export_review_requested.emit(operation.operation_id)

    @Slot(int, object)
    def _removal_export_review_ready(self, operation_id: int, review: object) -> None:
        active = self._active_operation
        if (
            self._closing_requested
            or active is None
            or active.operation_id != operation_id
            or active.kind is not _LongOperationKind.REMOVAL_REVIEW
            or type(review) is not RemovalExportReview
            or active.cancellation.cancelled()
        ):
            return
        try:
            review.__post_init__()
        except BaseException:
            self._failed(
                UiFailure(
                    "PWS_UI_RESPONSE_INVALID",
                    tr("Removal review could not be authenticated. Start a new review."),
                    True,
                    operation_id,
                )
            )
            return
        self._pending_removal_review = review

    def _show_pending_removal_review(self) -> bool:
        review = self._pending_removal_review
        if review is None or self._active_operation is not None:
            return False
        self._pending_removal_review = None
        state = self._state
        if (
            self._closing_requested
            or state is None
            or state.case_id != review.case_id
            or state.revision != review.manifest_revision
            or state.autosave_pending
            or state.removal_page_ids != review.affected_page_ids
            or self._removal_review_dialog is not None
        ):
            return False
        dialog = RemovalExportReviewDialog(review, self)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.destination_requested.connect(self._choose_removal_destination)
        dialog.finished.connect(self._removal_review_finished)
        self._removal_review_dialog = dialog
        self._update_package_availability()
        dialog.open()
        return True

    @Slot(bool)
    def _choose_removal_destination(self, signed_source_confirmed: bool) -> None:
        dialog = self._removal_review_dialog
        if (
            dialog is None
            or not dialog.review_confirmed
            or signed_source_confirmed is not dialog.signed_source_confirmed
            or self._active_operation is not None
            or self._closing_requested
        ):
            return
        selected, _filter = QFileDialog.getSaveFileName(
            self,
            tr("Export removal derivative to a new PDF file"),
            "",
            tr("PDF document (*.pdf)"),
            options=self._file_dialog_options(QFileDialog.Option.DontConfirmOverwrite),
        )
        if not selected:
            self._announce(
                tr("Removal export destination selection cancelled. No file was exported.")
            )
            return
        review = dialog.review
        dialog.accept()
        self._preview_refresh_timer.stop()
        self._autosave_timer.stop()
        self._hide_error()
        operation = self._begin_long_operation(_LongOperationKind.REMOVAL_EXPORT)
        self._set_controls_enabled(False)
        self._cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._ensure_widget_visible(self._cancel_button)
        self._announce(tr("Removal export started. Required checks are running; you can cancel."))
        self._commands.removal_pdf_export_requested.emit(
            operation.operation_id,
            review,
            Path(selected),
            signed_source_confirmed,
            operation.cancellation,
        )

    def _removal_review_finished(self, _result: int) -> None:
        dialog = self._removal_review_dialog
        self._removal_review_dialog = None
        if dialog is not None:
            dialog.deleteLater()
        self._update_package_availability()
        if not self._closing_requested and self._active_operation is None:
            self._removal_export_button.setFocus(Qt.FocusReason.OtherFocusReason)
            self._ensure_widget_visible(self._removal_export_button)

    @Slot(int, object)
    def _removal_export_ready(self, operation_id: int, report: object) -> None:
        active = self._active_operation
        if (
            self._closing_requested
            or active is None
            or active.operation_id != operation_id
            or active.kind is not _LongOperationKind.REMOVAL_EXPORT
            or type(report) is not RedactionExportReport
        ):
            return
        try:
            report.__post_init__()
        except BaseException:
            self._failed(
                UiFailure(
                    "PWS_UI_RESPONSE_INVALID",
                    tr(
                        "Removal export result could not be authenticated. Review the destination before retrying."
                    ),
                    False,
                    operation_id,
                )
            )
            return
        self._pending_redaction_export_report = report

    def _queue_redaction_export_report(self) -> bool:
        """Route unapplied marks to the separately verified removal workflow."""

        state = self._state
        if (
            self._closing_requested
            or state is None
            or state.case_id is None
            or state.revision is None
            or not state.removal_page_ids
        ):
            return False
        qualified = runtime_release_qualified()
        self._pending_redaction_export_report = RedactionExportReport(
            state.case_id,
            state.revision,
            state.removal_page_ids,
            None,
            RedactionExportStatus.BLOCKED,
            RedactionExportCode.WORKFLOW_REQUIRED
            if qualified
            else RedactionExportCode.RELEASE_PENDING,
            report_version=(
                RUNTIME_EXPORT_REPORT_VERSION if qualified else REDACTION_EXPORT_REPORT_VERSION
            ),
        )
        return True

    def _show_pending_redaction_export_report(self) -> bool:
        report = self._pending_redaction_export_report
        state = self._state
        if (
            report is None
            or self._closing_requested
            or self._active_operation is not None
            or self._package_export_is_opening_or_active()
            or self._redaction_export_dialog is not None
            or self._redaction_tools_dialog is not None
            or self._core_overlay_tools_is_opening_or_active()
            or self._scan_review_is_opening_or_active()
            or self._checklist_is_opening_or_active()
            or self._portal_constraints_is_opening_or_active()
            or self._signature_image_is_opening_or_active()
        ):
            return False
        self._pending_redaction_export_report = None
        if (
            state is None
            or state.case_id != report.case_id
            or state.revision != report.manifest_revision
            or state.removal_page_ids != report.affected_page_ids
        ):
            return False
        dialog = RedactionExportDialog(report, self)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.review_marks_requested.connect(self._review_redaction_export_marks)
        dialog.retry_requested.connect(self._retry_removal_export)
        dialog.finished.connect(self._redaction_export_report_finished)
        self._redaction_export_dialog = dialog
        self._update_package_availability()
        dialog.open()
        return True

    def _review_redaction_export_marks(self) -> None:
        dialog = self._redaction_export_dialog
        state = self._state
        if (
            dialog is None
            or state is None
            or state.case_id != dialog.report.case_id
            or state.revision != dialog.report.manifest_revision
        ):
            return
        page_id = next(
            (value for value in dialog.report.affected_page_ids if value in state.removal_page_ids),
            None,
        )
        self._pending_redaction_review = (
            None
            if page_id is None
            else (dialog.report.case_id, dialog.report.manifest_revision, page_id)
        )
        dialog.accept()

    def _redaction_export_report_finished(self, _result: int) -> None:
        dialog = self._redaction_export_dialog
        if dialog is None:
            return
        self._redaction_export_dialog = None
        dialog.deleteLater()
        self._update_package_availability()
        if self._closing_requested:
            self._pending_redaction_review = None
            self._pending_removal_retry = False
            return
        if self._pending_removal_retry:
            self._pending_removal_retry = False
            QTimer.singleShot(0, self._request_removal_export)
            return
        if self._pending_redaction_review is not None:
            QTimer.singleShot(0, self._open_redaction_report_marks)
        else:
            QTimer.singleShot(0, self._focus_redaction_tools_button_after_dialog)

    def _retry_removal_export(self) -> None:
        dialog = self._redaction_export_dialog
        if dialog is not None and dialog.report.retry_allowed:
            self._pending_removal_retry = True
            dialog.accept()

    def _open_redaction_report_marks(self) -> None:
        pending = self._pending_redaction_review
        self._pending_redaction_review = None
        state = self._state
        if (
            self._closing_requested
            or pending is None
            or state is None
            or state.case_id != pending[0]
            or state.revision != pending[1]
            or pending[2] not in state.removal_page_ids
            or self._active_operation is not None
            or self._package_export_is_opening_or_active()
        ):
            return
        page_id = pending[2]
        row = self._model.row_for_page(page_id)
        if row is None:
            return
        self._page_view.setCurrentIndex(self._model.index(row, 0))
        self._request_redaction_tools()

    @Slot(int, object)
    def _redaction_tools_started(self, operation_id: int, editor_state: object) -> None:
        expected = self._redaction_tools_start_operation_id
        if operation_id != expected:
            return
        self._redaction_tools_start_operation_id = None
        if (
            self._closing_requested
            or type(editor_state) is not RedactionToolsState
            or type(editor_state.editor_session_id) is not UUID
            or self._state is None
            or editor_state.case_id != self._state.case_id
            or editor_state.manifest_revision != self._state.revision
            or editor_state.page_id != self._selected_page_id()
            or not is_redaction_announcement_safe(editor_state.announcement)
            or self._redaction_tools_dialog is not None
        ):
            if not self._closing_requested and self._redaction_tools_dialog is None:
                cancel_operation_id = self._allocate_operation_id()
                self._redaction_tools_cancel_operation_id = cancel_operation_id
                self._commands.redaction_tools_abort_requested.emit(cancel_operation_id)
            self._update_import_availability()
            self._update_page_actions()
            self._update_quality_actions()
            return
        matching_pages = tuple(
            page for page in self._state.pages if page.page_id == editor_state.page_id
        )
        if len(matching_pages) != 1:
            cancel_operation_id = self._allocate_operation_id()
            self._redaction_tools_cancel_operation_id = cancel_operation_id
            self._commands.redaction_tools_abort_requested.emit(cancel_operation_id)
            self._update_import_availability()
            self._update_page_actions()
            self._update_quality_actions()
            return
        transform, pointer_mapping_enabled = _overlay_editor_display_facts(matching_pages[0])
        preview = self._model.preview(editor_state.page_id)
        display_size = (
            RenderedImageSize(preview.width(), preview.height())
            if preview is not None and not preview.isNull()
            else None
        )
        dialog = RedactionToolsDialog(
            editor_state,
            self,
            page_transform=transform,
            page_preview=preview if pointer_mapping_enabled else None,
            page_display_size=display_size,
            pointer_mapping_enabled=pointer_mapping_enabled,
        )
        dialog.save_requested.connect(self._save_redaction_tools)
        dialog.finished.connect(self._redaction_tools_finished)
        self._redaction_tools_dialog = dialog
        self._update_import_availability()
        self._update_page_actions()
        self._update_quality_actions()
        dialog.open()

    @Slot(int, object)
    def _redaction_tools_start_failed(self, operation_id: int, failure: object) -> None:
        if operation_id != self._redaction_tools_start_operation_id:
            return
        self._redaction_tools_start_operation_id = None
        if self._state is not None and self._state.autosave_pending and not self._closing_requested:
            self._autosave_timer.start(_AUTOSAVE_DEBOUNCE_MS)
        self._update_import_availability()
        self._update_page_actions()
        self._update_quality_actions()
        self._show_redaction_tools_failure(failure)

    @Slot(object)
    def _save_redaction_tools(self, editor_state: object) -> None:
        dialog = self._redaction_tools_dialog
        if (
            dialog is None
            or type(editor_state) is not RedactionToolsState
            or editor_state is not dialog.state
            or self._redaction_tools_save_operation_id is not None
        ):
            return
        operation_id = self._allocate_operation_id()
        self._redaction_tools_save_operation_id = operation_id
        dialog.set_save_pending()
        self._commands.redaction_tools_save_requested.emit(operation_id, editor_state)

    @Slot(int, object)
    def _redaction_tools_saved(self, operation_id: int, state: object) -> None:
        if operation_id != self._redaction_tools_save_operation_id:
            return
        self._redaction_tools_save_operation_id = None
        dialog = self._redaction_tools_dialog
        if dialog is None:
            return
        if type(state) is not OrganizerViewState or not _redaction_save_result_matches(
            dialog,
            state,
            self._state,
        ):
            dialog.save_failed(
                "PWS_UI_RESPONSE_INVALID",
                tr("PaperWorkShell received an invalid save confirmation. Try saving again."),
            )
            return
        dialog.finish_saved()
        self._announce(tr("Redaction marks saved. No information has been removed."))

    @Slot(int, object)
    def _redaction_tools_save_failed(self, operation_id: int, failure: object) -> None:
        if operation_id != self._redaction_tools_save_operation_id:
            return
        self._redaction_tools_save_operation_id = None
        dialog = self._redaction_tools_dialog
        if dialog is None:
            return
        if type(failure) is UiFailure:
            dialog.save_failed(failure.code, failure.message)
            return
        dialog.save_failed(
            "PWS_UI_RESPONSE_INVALID",
            tr("PaperWorkShell received an invalid save failure. Try saving again."),
        )

    @Slot(int)
    def _redaction_tools_finished(self, _result: int) -> None:
        dialog = self._redaction_tools_dialog
        if dialog is None:
            return
        state = dialog.state
        cancelled = (
            _result != int(QDialog.DialogCode.Accepted)
            and not dialog.commit_started
            and self._redaction_tools_save_operation_id is None
        )
        dialog.deleteLater()
        self._redaction_tools_dialog = None
        self._redaction_tools_save_operation_id = None
        if cancelled:
            operation_id = self._allocate_operation_id()
            self._redaction_tools_cancel_operation_id = operation_id
            self._commands.redaction_tools_cancel_requested.emit(operation_id, state)
        self._update_import_availability()
        self._update_page_actions()
        self._update_quality_actions()
        if (
            not cancelled
            and not self._closing_requested
            and self._redaction_tools_button.isEnabled()
        ):
            QTimer.singleShot(0, self._focus_redaction_tools_button_after_dialog)

    @Slot(int)
    def _redaction_tools_cancelled(self, operation_id: int) -> None:
        if operation_id != self._redaction_tools_cancel_operation_id:
            return
        self._redaction_tools_cancel_operation_id = None
        self._update_import_availability()
        self._update_page_actions()
        self._update_quality_actions()
        self._announce(tr("Redaction mark changes cancelled. No information has been removed."))
        if not self._closing_requested and self._redaction_tools_button.isEnabled():
            QTimer.singleShot(0, self._focus_redaction_tools_button_after_dialog)

    def _focus_redaction_tools_button_after_dialog(self) -> None:
        self._focus_trace_invoker(self._redaction_tools_button)
        if self._closing_requested or not self._redaction_tools_button.isEnabled():
            return
        self.activateWindow()
        self._redaction_tools_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._ensure_widget_visible(self._redaction_tools_button)

    @Slot(int, object)
    def _redaction_tools_cancel_failed(self, operation_id: int, failure: object) -> None:
        if operation_id != self._redaction_tools_cancel_operation_id:
            return
        self._redaction_tools_cancel_operation_id = None
        self._update_import_availability()
        self._update_page_actions()
        self._update_quality_actions()
        self._show_redaction_tools_failure(failure)

    def _show_redaction_tools_failure(self, failure: object) -> None:
        code = failure.code if type(failure) is UiFailure else None
        message = failure.message if type(failure) is UiFailure else None
        safe_code, _ = safe_redaction_failure(code, message)
        safe_message = tr(
            "PaperWorkShell could not finish the removal-mark editor. Try again. No information has been removed."
        )
        self._set_error_text(f"{safe_code}: {safe_message}")
        self._error_label.setVisible(True)
        self._announce(
            tr("Redaction mark editor could not finish. {value1}", value1=safe_message),
            assertive=True,
        )
        self._focus_error_recovery(self._redaction_tools_button)

    def _request_checklist(self) -> None:
        """Open the exclusive, keyboard-complete packet checklist editor."""

        if not self._checklist_button.isEnabled() or not self._prepare_for_mutation():
            return
        if self._checklist_is_opening_or_active():
            return
        operation_id = self._allocate_operation_id()
        self._checklist_start_operation_id = operation_id
        self._autosave_timer.stop()
        self._preview_refresh_timer.stop()
        self._deferred_preview_state = None
        self._hide_error()
        self._update_import_availability()
        self._update_page_actions()
        self._update_quality_actions()
        self._announce(
            tr("Opening packet checklist. Adding pages will still require separate confirmation.")
        )
        self._commands.checklist_begin_requested.emit(operation_id)

    @Slot(int, object)
    def _checklist_started(self, operation_id: int, editor_state: object) -> None:
        if operation_id != self._checklist_start_operation_id:
            return
        self._checklist_start_operation_id = None
        current = self._state
        valid_pages = (
            type(editor_state) is ChecklistEditorState
            and current is not None
            and tuple((page.page_id, page.position) for page in editor_state.pages)
            == tuple((page.page_id, page.position) for page in current.pages)
        )
        if (
            self._closing_requested
            or type(editor_state) is not ChecklistEditorState
            or current is None
            or editor_state.case_id != current.case_id
            or editor_state.manifest_revision != current.revision
            or not valid_pages
            or not is_checklist_announcement_safe(editor_state.announcement)
            or self._checklist_dialog is not None
        ):
            if not self._closing_requested and self._checklist_dialog is None:
                cancel_operation_id = self._allocate_operation_id()
                self._checklist_cancel_operation_id = cancel_operation_id
                self._commands.checklist_abort_requested.emit(cancel_operation_id)
            self._update_import_availability()
            self._update_page_actions()
            self._update_quality_actions()
            return
        dialog = ChecklistEditorDialog(
            editor_state,
            self,
            page_labels={page.page_id: self._page_label(page) for page in current.pages},
        )
        dialog.apply_requested.connect(self._apply_checklist)
        dialog.finished.connect(self._checklist_finished)
        self._checklist_dialog = dialog
        self._update_import_availability()
        self._update_page_actions()
        self._update_quality_actions()
        dialog.open()

    @Slot(int, object)
    def _checklist_start_failed(self, operation_id: int, failure: object) -> None:
        if operation_id != self._checklist_start_operation_id:
            return
        self._checklist_start_operation_id = None
        if self._state is not None and self._state.autosave_pending and not self._closing_requested:
            self._autosave_timer.start(_AUTOSAVE_DEBOUNCE_MS)
        self._update_import_availability()
        self._update_page_actions()
        self._update_quality_actions()
        if (
            self._assisted_session.active
            and type(failure) is UiFailure
            and failure.code == AssistedUseErrorCode.LOCKED.value
        ):
            # Revocation can reject an already queued editor request. The lock
            # surface explains that interruption; a delayed reply must not tell
            # the newly unlocked case that it still needs to be unlocked.
            return
        self._show_checklist_failure(failure)

    @Slot(object)
    def _apply_checklist(self, editor_state: object) -> None:
        dialog = self._checklist_dialog
        if (
            dialog is None
            or type(editor_state) is not ChecklistEditorState
            or editor_state is not dialog.state
            or self._checklist_apply_operation_id is not None
        ):
            return
        operation_id = self._allocate_operation_id()
        self._checklist_apply_operation_id = operation_id
        self._commands.checklist_apply_requested.emit(operation_id, editor_state)

    @Slot(int, object)
    def _checklist_applied(self, operation_id: int, state: object) -> None:
        if operation_id != self._checklist_apply_operation_id:
            return
        self._checklist_apply_operation_id = None
        dialog = self._checklist_dialog
        if dialog is None:
            return
        if type(state) is not OrganizerViewState or not _checklist_apply_result_matches(
            dialog,
            state,
            self._state,
        ):
            dialog.mark_apply_failed(
                "PWS_UI_RESPONSE_INVALID",
                tr("PaperWorkShell received an invalid checklist save confirmation."),
            )
            return
        if dialog.mark_applied():
            self._announce(tr("Checklist assignments applied and saved."))

    @Slot(int, object)
    def _checklist_apply_failed(self, operation_id: int, failure: object) -> None:
        if operation_id != self._checklist_apply_operation_id:
            return
        self._checklist_apply_operation_id = None
        dialog = self._checklist_dialog
        if dialog is None:
            return
        if type(failure) is UiFailure:
            dialog.mark_apply_failed(failure.code, failure.message)
            return
        dialog.mark_apply_failed(
            "PWS_UI_RESPONSE_INVALID",
            tr("PaperWorkShell received an invalid checklist save failure."),
        )

    @Slot(int)
    def _checklist_finished(self, result: int) -> None:
        dialog = self._checklist_dialog
        if dialog is None:
            return
        state = dialog.state
        cancelled = (
            result != int(QDialog.DialogCode.Accepted)
            and not dialog.commit_started
            and self._checklist_apply_operation_id is None
        )
        dialog.deleteLater()
        self._checklist_dialog = None
        self._checklist_apply_operation_id = None
        if cancelled:
            operation_id = self._allocate_operation_id()
            self._checklist_cancel_operation_id = operation_id
            self._commands.checklist_cancel_requested.emit(operation_id, state)
        self._update_import_availability()
        self._update_page_actions()
        self._update_quality_actions()
        if not cancelled and not self._closing_requested and self._checklist_button.isEnabled():
            QTimer.singleShot(0, self._focus_checklist_button_after_dialog)

    @Slot(int)
    def _checklist_cancelled(self, operation_id: int) -> None:
        if operation_id != self._checklist_cancel_operation_id:
            return
        self._checklist_cancel_operation_id = None
        self._update_import_availability()
        self._update_page_actions()
        self._update_quality_actions()
        self._announce(tr("Checklist changes cancelled. Nothing was applied."))
        if not self._closing_requested and self._checklist_button.isEnabled():
            QTimer.singleShot(0, self._focus_checklist_button_after_dialog)

    def _focus_checklist_button_after_dialog(self) -> None:
        self._focus_trace_invoker(self._checklist_button)
        if self._closing_requested or not self._checklist_button.isEnabled():
            return
        self.activateWindow()
        self._checklist_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._ensure_widget_visible(self._checklist_button)

    @Slot(int, object)
    def _checklist_cancel_failed(self, operation_id: int, failure: object) -> None:
        if operation_id != self._checklist_cancel_operation_id:
            return
        self._checklist_cancel_operation_id = None
        self._update_import_availability()
        self._update_page_actions()
        self._update_quality_actions()
        self._show_checklist_failure(failure)

    def _show_checklist_failure(self, failure: object) -> None:
        safe_code = (
            failure.code if type(failure) is UiFailure else "PWS_UI_CHECKLIST_RESPONSE_INVALID"
        )
        safe_message = (
            tr(failure.message)
            if type(failure) is UiFailure
            else tr("PaperWorkShell could not finish the checklist action safely. Try again.")
        )
        self._set_error_text(f"{safe_code}: {safe_message}")
        self._error_label.setVisible(True)
        self._announce(
            tr("Checklist editor could not finish. {value1}", value1=safe_message),
            assertive=True,
        )
        self._focus_error_recovery(self._checklist_button)

    def _request_package_export(self) -> None:
        """Open the saved-case package review through an ordinary keyboard action."""

        if self._package_release_failed and self._package_export_button.isEnabled():
            self._release_package_review()
            return
        if not self._package_export_button.isEnabled() or not self._prepare_for_mutation():
            return
        self._pending_package_review = True
        self._autosave_timer.stop()
        self._preview_refresh_timer.stop()
        self._deferred_preview_state = None
        self._hide_error()
        self._update_package_availability()
        if self._active_operation is not None:
            self._cancel_button.setEnabled(True)
            self._cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)
            self._announce(
                tr("Package inspection queued. Stopping the current preview first. You can cancel.")
            )
            return
        self._start_package_review()

    def _start_package_review(self) -> None:
        self._pending_package_review = False
        self._package_terminal_result_published = False
        operation = self._begin_long_operation(_LongOperationKind.PACKAGE_REVIEW)
        self._package_review_operation_id = operation.operation_id
        self._package_cancellation = operation.cancellation
        self._cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._ensure_widget_visible(self._cancel_button)
        self._announce(
            tr(
                "Inspecting PDFs for package review. You can cancel. No package files are being saved."
            )
        )
        self._commands.package_review_begin_requested.emit(
            operation.operation_id, None, operation.cancellation
        )

    @Slot(int, object)
    def _package_review_ready(self, operation_id: int, review: object) -> None:
        if operation_id != self._package_review_operation_id:
            return
        if self._package_cancellation is not None and self._package_cancellation.cancelled():
            self._package_failed(
                operation_id,
                "review",
                UiFailure(
                    code="PACKAGE_REVIEW_CANCELLED",
                    message=tr("Package inspection was cancelled."),
                    retryable=True,
                    operation_id=operation_id,
                ),
            )
            return
        self._package_review_operation_id = None
        self._package_cancellation = None
        if (
            self._active_operation is not None
            and self._active_operation.operation_id == operation_id
        ):
            self._finish_active_operation()
        if self._closing_requested or not is_package_review_state_valid(review):
            self._package_review_state = None
            if self._package_export_dialog is not None:
                self._package_export_dialog.show_failure()
            self._publish_package_failure("review", None)
            self._release_package_review()
            return
        if type(review) is not PackageReviewState:
            return
        dialog = self._package_export_dialog
        if dialog is None:
            dialog = PackageExportDialog(
                review.allowed_modes,
                self,
                initial_mode=review.selected_mode,
                input_labels=tuple(
                    self._page_label(page, case_order=True) for page in self._state.pages
                )
                if self._state is not None
                else (),
            )
            self._post_export_closed = False
            self._post_export_private_summary_discarded = False
            dialog.mode_requested.connect(self._refresh_package_mode)
            dialog.refresh_requested.connect(self._refresh_package_review)
            dialog.export_requested.connect(self._choose_package_destination)
            dialog.compression_requested.connect(self._choose_compression_destination)
            dialog.cancel_requested.connect(self._cancel_package_operation)
            dialog.summary_save_requested.connect(self._choose_package_summary_destination)
            dialog.text_companion_requested.connect(self._choose_text_companion_destination)
            dialog.deletion_requested.connect(self._request_post_export_deletion)
            dialog.finished.connect(self._package_dialog_finished)
            self._package_export_dialog = dialog
        elif dialog.selected_mode is not review.selected_mode:
            dialog.show_failure()
            self._package_review_state = None
            self._publish_package_failure("review", None)
            return
        self._package_review_state = review
        dialog.show_review(
            review.summary,
            export_allowed=review.export_allowed,
            signed_source_confirmation_required=review.signed_source_confirmation_required,
            human_review_required=review.human_review_required,
            compression_allowed=review.compression_allowed,
        )
        self._set_status_text(review.summary.status_message)
        self._update_package_availability()
        if not dialog.isVisible():
            dialog.open()

    @Slot(object)
    def _refresh_package_mode(self, mode: object) -> None:
        dialog = self._package_export_dialog
        if (
            dialog is None
            or type(mode) is not PortalOutputMode
            or mode is not dialog.selected_mode
            or self._package_review_operation_id is not None
            or self._package_export_operation_id is not None
            or self._closing_requested
        ):
            return
        self._package_review_state = None
        self._package_terminal_result_published = False
        self._package_review_operation_id = self._allocate_operation_id()
        self._package_cancellation = OperationCancellation()
        dialog.set_busy("inspecting_pdfs", cancelable=True)
        self._commands.package_review_refresh_requested.emit(
            self._package_review_operation_id, mode, self._package_cancellation
        )

    @Slot()
    def _refresh_package_review(self) -> None:
        dialog = self._package_export_dialog
        if dialog is not None:
            self._refresh_package_mode(dialog.selected_mode)

    @Slot(bool, bool)
    def _choose_package_destination(self, signed: bool, human: bool) -> None:
        dialog = self._package_export_dialog
        review = self._package_review_state
        if (
            dialog is None
            or review is None
            or not review.export_allowed
            or review.selected_mode is not dialog.selected_mode
            or self._package_export_operation_id is not None
            or self._package_review_operation_id is not None
            or self._closing_requested
            or type(signed) is not bool
            or type(human) is not bool
            or (review.signed_source_confirmation_required and not signed)
            or (review.human_review_required and not human)
        ):
            if dialog is not None:
                dialog.show_failure()
            return
        selected = QFileDialog.getExistingDirectory(
            dialog,
            tr("Choose a folder for the package"),
            "",
            self._file_dialog_options(QFileDialog.Option.ShowDirsOnly),
        )
        if not selected:
            dialog.show_review(
                review.summary,
                export_allowed=review.export_allowed,
                signed_source_confirmation_required=review.signed_source_confirmation_required,
                human_review_required=review.human_review_required,
                compression_allowed=review.compression_allowed,
            )
            return
        self._package_export_operation_id = self._allocate_operation_id()
        self._package_cancellation = OperationCancellation()
        dialog.set_busy("generating_pdfs")
        self._commands.package_export_requested.emit(
            self._package_export_operation_id,
            review.review_id,
            Path(selected),
            signed,
            human,
            self._package_cancellation,
        )

    @Slot(int, object)
    def _package_export_ready(self, operation_id: int, summary: object) -> None:
        if operation_id != self._package_export_operation_id:
            return
        if self._compression_phase is not None and self._compression_phase != "export":
            return
        self._package_export_operation_id = None
        self._package_cancellation = None
        self._package_review_state = None
        self._dispose_compression_dialog()
        dialog = self._package_export_dialog
        if dialog is None:
            return
        try:
            if type(summary) is not PackageSummary:
                raise TypeError
            dialog.show_result(summary, save_available=True)
        except BaseException:
            dialog.show_failure(phase="export")
            self._publish_package_failure("export", None)
        else:
            self._publish_package_result(
                summary.status_message
                + " "
                + tr(
                    "Files planned: {value_0}. Files saved and reopened: {value_1}.",
                    value_0=summary.file_count,
                    value_1=summary.saved_file_count,
                ),
                assertive=summary.status is not PackageSummaryStatus.COMMITTED,
            )

    def _publish_package_result(self, message: str, *, assertive: bool = False) -> None:
        """Retain an actual package outcome without reusing an ordinary PDF target."""

        self._package_terminal_result_published = True
        # A package summary is display-only. Its filenames do not authorize a
        # filesystem target for the ordinary single-PDF Open actions.
        self._last_export_path = None
        self._pdf_export_saved_id = None
        self._last_export_summary = ""
        self._last_result_label.setToolTip("")
        self._open_pdf_button.hide()
        self._open_export_folder_button.hide()
        self._announce(message, assertive=assertive)

    def _publish_package_failure(self, phase: str, failure: object) -> None:
        code = failure.code if type(failure) is UiFailure else ""
        if phase == "export":
            message = tr(
                "Package export did not finish safely. The destination state is not established by this message. Inspect the destination before retrying in a new folder. Close and reopen package review to start a new attempt."
            )
            if code == "PACKAGE_WORKFLOW_CANCELLED":
                message = tr("Package export was cancelled before writing files.")
            elif code in {"PACKAGE_EXPORT_CANCELLED", "PACKAGE_EXPORT_TIMED_OUT"}:
                message = tr(
                    "{prefix} {message}",
                    prefix=tr("Package export was cancelled before completion.")
                    if code == "PACKAGE_EXPORT_CANCELLED"
                    else tr("Package export exceeded its configured time limit."),
                    message=message,
                )
        else:
            message = tr(
                "Package review could not be prepared. Check the checklist assignments and saved constraints, then try again."
            )
            if code == "PACKAGE_REVIEW_CANCELLED":
                message = tr(
                    "Package inspection was cancelled. Choose Export checklist package to try again."
                )
            elif code == "PACKAGE_REVIEW_TIMED_OUT":
                message = tr(
                    "Package inspection exceeded its time limit. Choose Export checklist package to try again."
                )
        self._publish_package_result(message, assertive=True)

    @Slot(bool, bool)
    def _choose_compression_destination(self, signed: bool, human: bool) -> None:
        parent = self._package_export_dialog
        review = self._package_review_state
        state = self._state
        if (
            parent is None
            or review is None
            or state is None
            or state.case_id is None
            or not review.compression_allowed
            or review.selected_mode is not parent.selected_mode
            or self._compression_dialog is not None
            or self._package_export_operation_id is not None
            or self._package_review_operation_id is not None
            or self._closing_requested
            or type(signed) is not bool
            or type(human) is not bool
            or (review.signed_source_confirmation_required and not signed)
            or (review.human_review_required and not human)
        ):
            if parent is not None:
                parent.show_failure()
            return
        selected = QFileDialog.getExistingDirectory(
            parent,
            tr("Choose a folder for the reviewed package"),
            "",
            self._file_dialog_options(QFileDialog.Option.ShowDirsOnly),
        )
        if not selected:
            parent.show_review(
                review.summary,
                export_allowed=review.export_allowed,
                signed_source_confirmation_required=review.signed_source_confirmation_required,
                human_review_required=review.human_review_required,
                compression_allowed=review.compression_allowed,
            )
            return
        self._compression_destination = Path(selected)
        self._compression_case_id = state.case_id
        self._compression_signed = signed
        self._compression_human = human
        dialog = CompressionReviewDialog(self._resource_policy, parent)
        dialog.reprepare_requested.connect(self._prepare_compression)
        dialog.preview_requested.connect(self._preview_compression_page)
        dialog.export_requested.connect(self._export_compression)
        dialog.cancel_requested.connect(self._cancel_package_operation)
        dialog.finished.connect(self._compression_dialog_finished)
        self._compression_dialog = dialog
        self._rebalance_preview_cache()
        dialog.open()
        self._prepare_compression(TargetSizeColorChoice.PRESERVE)

    @Slot(object)
    def _prepare_compression(self, color_choice: object) -> None:
        dialog = self._compression_dialog
        parent = self._package_export_dialog
        review = self._package_review_state
        destination = self._compression_destination
        if (
            dialog is None
            or parent is None
            or review is None
            or destination is None
            or type(color_choice) is not TargetSizeColorChoice
            or color_choice is not dialog.color_choice
            or self._closing_requested
            or self._package_export_operation_id is not None
            or self._package_review_operation_id is not None
        ):
            return
        self._compression_review_state = None
        self._compression_preview_request = None
        self._compression_pair_review_id = None
        self._compression_phase = "preparing"
        self._package_export_operation_id = self._allocate_operation_id()
        self._package_cancellation = OperationCancellation()
        parent.set_busy("generating_pdfs")
        dialog.set_busy("preparing")
        self._commands.compression_prepare_requested.emit(
            self._package_export_operation_id,
            review.review_id,
            destination,
            color_choice,
            self._compression_signed,
            self._compression_human,
            self._package_cancellation,
        )

    @Slot(int, object)
    def _compression_ready(self, operation_id: int, review: object) -> None:
        if (
            operation_id != self._package_export_operation_id
            or self._compression_phase != "preparing"
        ):
            return
        dialog = self._compression_dialog
        state = self._state
        if self._package_cancellation is not None and self._package_cancellation.cancelled():
            self._package_export_operation_id = None
            self._package_cancellation = None
            self._dispose_compression_dialog()
            self._discard_compression_review()
            return
        if (
            dialog is None
            or state is None
            or type(review) is not CompressionReviewState
            or self._closing_requested
            or state.autosave_pending
            or review.case_id != state.case_id
            or review.case_id != self._compression_case_id
            or review.manifest_revision != state.revision
            or review.color_choice is not dialog.color_choice
            or any(
                page.page_id not in {value.page_id for value in state.pages}
                for page in review.affected_pages
            )
        ):
            self._package_failed(operation_id, "compression_prepare", None)
            return
        self._package_export_operation_id = None
        self._package_cancellation = None
        self._compression_phase = None
        self._compression_review_state = review
        dialog.show_review(review)

    @Slot(object, str, int)
    def _preview_compression_page(
        self, review_id: object, output_key: str, page_index: int
    ) -> None:
        dialog = self._compression_dialog
        review = self._compression_review_state
        if (
            dialog is None
            or review is None
            or dialog.review is not review
            or type(review_id) is not UUID
            or review_id != review.review_id
            or type(output_key) is not str
            or type(page_index) is not int
            or not any(
                page.output_key == output_key and page.page_index == page_index
                for page in review.affected_pages
            )
            or self._package_export_operation_id is not None
            or self._closing_requested
        ):
            return
        self._compression_preview_request = (review_id, output_key, page_index)
        self._compression_phase = "preview"
        self._package_export_operation_id = self._allocate_operation_id()
        self._package_cancellation = OperationCancellation()
        self._rebalance_preview_cache()
        dialog.set_busy("preview")
        self._commands.compression_preview_requested.emit(
            self._package_export_operation_id,
            review_id,
            output_key,
            page_index,
            self._package_cancellation,
        )

    @Slot(int, object)
    def _compression_preview_ready(self, operation_id: int, result: object) -> None:
        if (
            operation_id != self._package_export_operation_id
            or self._compression_phase != "preview"
        ):
            return
        dialog = self._compression_dialog
        review = self._compression_review_state
        expected = self._compression_preview_request
        valid = (
            dialog is not None
            and review is not None
            and dialog.review is review
            and type(result) is CompressionPagePreview
            and expected is not None
            and (result.review_id, result.output_key, result.page_index) == expected
            and self._package_cancellation is not None
            and not self._package_cancellation.cancelled()
            and not self._closing_requested
        )
        if (
            not valid
            or type(result) is not CompressionPagePreview
            or dialog is None
            or review is None
        ):
            self._package_failed(operation_id, "compression_preview", None)
            return
        page = next(
            (
                page
                for page in review.affected_pages
                if (page.output_key, page.page_index) == (result.output_key, result.page_index)
            ),
            None,
        )
        snapshots = (result.before, result.proposed)
        if (
            page is None
            or any(
                snapshot.case_id != review.case_id
                or snapshot.page_id != page.page_id
                or snapshot.width_pixels * snapshot.height_pixels
                > self._resource_policy.maximum_preview_pixels_per_page
                for snapshot in snapshots
            )
            or 2 * sum(len(snapshot.pixels) for snapshot in snapshots)
            > self._resource_policy.thumbnail_preview_cache_bytes
        ):
            self._package_failed(operation_id, "compression_preview", None)
            return
        self._rebalance_preview_cache()
        # These views borrow the validated snapshot bytes. The dialog makes the
        # only detached pair; incoming bytes plus copies fit the central budget.
        images = tuple(
            QImage(
                snapshot.pixels,
                snapshot.width_pixels,
                snapshot.height_pixels,
                snapshot.row_stride_bytes,
                QImage.Format.Format_RGBA8888,
            )
            for snapshot in snapshots
        )
        if not dialog.set_preview(result.review_id, result.output_key, result.page_index, *images):
            self._package_failed(operation_id, "compression_preview", None)
            return
        self._compression_pair_review_id = review.review_id
        self._compression_preview_request = None
        self._compression_phase = None
        self._package_export_operation_id = None
        self._package_cancellation = None

    @Slot(object)
    def _export_compression(self, review_id: object) -> None:
        dialog = self._compression_dialog
        review = self._compression_review_state
        if (
            dialog is None
            or review is None
            or dialog.review is not review
            or not dialog.review_confirmed
            or type(review_id) is not UUID
            or review_id != review.review_id
            or not review.export_allowed
            or (review.affected_pages and self._compression_pair_review_id != review.review_id)
            or self._package_export_operation_id is not None
            or self._closing_requested
        ):
            return
        self._compression_phase = "export"
        self._package_export_operation_id = self._allocate_operation_id()
        self._package_cancellation = OperationCancellation()
        dialog.set_busy("export")
        self._commands.compression_export_requested.emit(
            self._package_export_operation_id,
            review_id,
            True,
            self._package_cancellation,
        )

    @Slot(int)
    def _compression_dialog_finished(self, _result: int) -> None:
        self._dispose_compression_dialog()
        self._discard_compression_review()

    def _discard_compression_review(self) -> None:
        if self._package_review_operation_id is not None or self._package_export_dialog is None:
            return
        self._package_review_operation_id = self._allocate_operation_id()
        self._package_cancellation = None
        self._package_export_dialog.set_busy("preparing", cancelable=False)
        self._commands.compression_discard_requested.emit(self._package_review_operation_id)

    def _dispose_compression_dialog(self) -> None:
        dialog = self._compression_dialog
        self._compression_dialog = None
        if dialog is not None:
            dialog.finished.disconnect(self._compression_dialog_finished)
            dialog.finish_operation()
            dialog.done(QDialog.DialogCode.Rejected)
            dialog.deleteLater()
        self._compression_destination = None
        self._compression_case_id = None
        self._compression_signed = False
        self._compression_human = False
        self._compression_phase = None
        self._compression_review_state = None
        self._compression_preview_request = None
        self._compression_pair_review_id = None
        self._rebalance_preview_cache()

    @Slot(int, int, int)
    def _package_progress(self, operation_id: int, completed: int, total: int) -> None:
        if (
            type(completed) is not int
            or type(total) is not int
            or not 1 <= total <= 1_000_000
            or not 0 <= completed <= total
        ):
            return
        if operation_id == self._package_review_operation_id:
            if self._package_export_dialog is not None:
                self._package_export_dialog.set_progress("inspecting_pdfs", completed, total)
            else:
                self._progress_changed(
                    UiProgress(
                        tr("Package inspection"),
                        tr("PDFs processed"),
                        completed,
                        total,
                        True,
                        operation_id,
                    )
                )
            return
        if (
            operation_id == self._package_export_operation_id
            and self._package_export_dialog is not None
        ):
            if self._compression_dialog is not None:
                self._compression_dialog.set_progress(
                    self._compression_phase or "preparing", completed, total
                )
                return
            self._package_export_dialog.set_progress("generating_pdfs", completed, total)

    @Slot()
    def _cancel_package_operation(self) -> None:
        cancellation = self._package_cancellation
        if cancellation is not None:
            cancellation.cancel()

    def _post_export_context(self) -> tuple[UUID, int, int] | None:
        state = self._state
        dialog = self._package_export_dialog
        controller = self._assisted_session
        if (
            self._closing_requested
            or self._case_close_pending
            or self._post_export_closed
            or controller.locked
            or state is None
            or state.case_id is None
            or state.revision is None
            or dialog is None
            or dialog.summary is None
            or dialog.summary.status is not PackageSummaryStatus.COMMITTED
        ):
            return None
        return state.case_id, state.revision, controller._privacy_epoch

    def _post_export_operation_pending(self) -> bool:
        return (
            any(
                operation is not None
                for operation in (
                    self._post_export_review_operation_id,
                    self._post_export_delete_operation_id,
                    self._text_companion_operation_id,
                    self._package_summary_save_operation_id,
                    self._package_export_operation_id,
                    self._package_review_operation_id,
                )
            )
            or self._post_export_deletion_dialog is not None
        )

    @Slot()
    def _request_post_export_deletion(self) -> None:
        if self._post_export_operation_pending():
            return
        binding = self._post_export_context()
        if binding is None:
            self._post_export_feedback(
                tr("Case deletion was not requested. Review the current case again."), failed=True
            )
            return
        self._post_export_binding = binding
        self._post_export_review_operation_id = self._allocate_operation_id()
        self._package_cancellation = OperationCancellation()
        self._commands.post_export_deletion_review_requested.emit(
            self._post_export_review_operation_id, self._package_cancellation
        )

    @Slot(int, object)
    def _post_export_deletion_ready(self, operation_id: int, review: object) -> None:
        if operation_id != self._post_export_review_operation_id:
            return
        cancelled = (
            self._package_cancellation is not None and self._package_cancellation.cancelled()
        )
        self._post_export_review_operation_id = None
        self._package_cancellation = None
        binding = self._post_export_binding
        if (
            cancelled
            or binding is None
            or binding != self._post_export_context()
            or type(review) is not PostExportDeletionInventory
            or (review.case_id, review.revision) != binding[:2]
        ):
            self._post_export_binding = None
            self._commands.post_export_deletion_cancel_requested.emit()
            self._post_export_feedback(
                tr("Case deletion review cancelled. No deletion was requested.")
            )
            return
        parent = self._package_export_dialog
        if parent is None:
            return
        parent.complete_post_export(
            tr("Review the case inventory before deciding whether to delete it.")
        )
        dialog = PostExportDeletionDialog(review, parent)
        self._post_export_deletion_dialog = dialog
        dialog.finished.connect(self._post_export_deletion_dialog_finished)
        dialog.open()

    @Slot(int)
    def _post_export_deletion_dialog_finished(self, result: int) -> None:
        dialog = self._post_export_deletion_dialog
        if dialog is None:
            return
        self._post_export_deletion_dialog = None
        binding = self._post_export_binding
        self._post_export_binding = None
        confirmed = (
            result == QDialog.DialogCode.Accepted
            and dialog.confirmed
            and binding is not None
            and binding == self._post_export_context()
        )
        review_id = dialog.review_id
        dialog.deleteLater()
        parent = self._package_export_dialog
        if not confirmed or parent is None or binding is None:
            self._commands.post_export_deletion_cancel_requested.emit()
            self._post_export_feedback(tr("Case kept. No deletion was requested."))
            return
        self._post_export_delete_operation_id = self._allocate_operation_id()
        self._post_export_delete_case_id = binding[0]
        parent.set_busy("preparing", cancelable=False)
        self._commands.post_export_deletion_confirm_requested.emit(
            self._post_export_delete_operation_id, review_id
        )

    @Slot()
    def _choose_text_companion_destination(self) -> None:
        if self._post_export_operation_pending():
            return
        parent = self._package_export_dialog
        binding = self._post_export_context()
        if parent is None or binding is None or not parent.text_privacy_confirmed:
            self._post_export_feedback(
                tr("Confirm the text companion privacy warning before saving."), failed=True
            )
            return
        selected, _filter = QFileDialog.getSaveFileName(
            parent,
            tr("Save plain-text companion"),
            "package-text.txt",
            tr("Plain text (*.txt)"),
            options=self._file_dialog_options(),
        )
        if binding != self._post_export_context() or not parent.text_privacy_confirmed:
            self._post_export_feedback(
                tr("Text companion request cancelled. Review its privacy warning again.")
            )
            return
        if not selected:
            self._post_export_feedback(
                tr("Text companion was not saved. The verified exports remain unchanged.")
            )
            return
        self._post_export_binding = binding
        self._text_companion_operation_id = self._allocate_operation_id()
        self._package_cancellation = OperationCancellation()
        self._commands.text_companion_save_requested.emit(
            self._text_companion_operation_id,
            Path(selected),
            True,
            self._package_cancellation,
        )

    @Slot(int)
    def _text_companion_saved(self, operation_id: int) -> None:
        if operation_id != self._text_companion_operation_id:
            return
        self._text_companion_operation_id = None
        self._package_cancellation = None
        self._post_export_binding = None
        self._post_export_feedback(
            tr(
                "Plain-text companion saved. Review its text for OCR errors and reading order before sharing. This separate file remains after case deletion."
            )
        )

    @Slot(int, object)
    def _post_export_deletion_finished(self, operation_id: int, result: object) -> None:
        if operation_id != self._post_export_delete_operation_id:
            return
        self._post_export_delete_operation_id = None
        expected_case, self._post_export_delete_case_id = self._post_export_delete_case_id, None
        self._post_export_binding = None
        self._post_export_closed = True
        parent = self._package_export_dialog
        if parent is not None:
            parent.disable_post_export()
        if type(result) is not CaseDeletionResult or result.case_id != expected_case:
            self._post_export_deletion_feedback(
                tr(
                    "The case deletion result is unavailable. Check the case list before continuing. The saved exports remain separate."
                ),
                failed=True,
            )
            return
        if result.status is CaseDeletionStatus.COMPLETE:
            message = tr(
                "Case removed from PaperWorkShell storage. The verified exports, separately saved summaries and text companions remain. External source files remain unchanged. Forensic erasure is not guaranteed."
            )
        else:
            message = tr(
                "Case removal is incomplete. Case reference: {case}. Some case files remain and the case may no longer reopen. Remaining application files may need support or later explicit cleanup. PaperWorkShell will not retry deletion automatically. The verified exports and separately saved companions remain. Forensic erasure is not guaranteed.",
                case=str(result.case_id),
            )
        self._post_export_deletion_feedback(
            message, failed=result.status is CaseDeletionStatus.PARTIAL
        )
        if (
            parent is not None
            and not self._closing_requested
            and not self._assisted_session.locked
            and not self._post_export_private_summary_discarded
        ):
            parent.show()

    @Slot(int, str, object)
    def _post_export_failed(self, operation_id: int, phase: str, failure: object) -> None:
        if phase == "inventory" and operation_id == self._post_export_review_operation_id:
            self._post_export_review_operation_id = None
            self._commands.post_export_deletion_cancel_requested.emit()
        elif phase == "delete" and operation_id == self._post_export_delete_operation_id:
            self._post_export_delete_operation_id = None
            self._post_export_delete_case_id = None
            if self._state is None or self._state.case_id is None:
                self._post_export_closed = True
                if self._package_export_dialog is not None:
                    self._package_export_dialog.disable_post_export()
        elif phase == "text" and operation_id == self._text_companion_operation_id:
            self._text_companion_operation_id = None
        else:
            return
        self._package_cancellation = None
        self._post_export_binding = None
        message = tr(
            "The optional post-export operation did not complete. The saved package remains separate; check the current case and destination before retrying."
        )
        if type(failure) is UiFailure:
            message = tr(_POST_EXPORT_FAILURE_TEXT.get(failure.code, message))
        if phase == "delete":
            self._post_export_deletion_feedback(message, failed=True)
        else:
            self._post_export_feedback(message, failed=True)

    def _post_export_deletion_feedback(self, message: str, *, failed: bool = False) -> None:
        if self._post_export_private_summary_discarded:
            # Closing a locked case does not authorize revealing its output names,
            # categories, or old review. Only the content-free terminal fact remains.
            self._post_export_pending_feedback = None
            if not self._closing_requested:
                self._announce(message, assertive=failed)
                self._focus_safe_control()
            return
        self._post_export_feedback(message, failed=failed)

    def _post_export_feedback(self, message: str, *, failed: bool = False) -> None:
        if self._closing_requested or self._post_export_private_summary_discarded:
            return
        if self._assisted_session.locked:
            self._post_export_pending_feedback = message, failed
            return
        parent = self._package_export_dialog
        if parent is not None:
            parent.complete_post_export(message, failed=failed)

    def _resume_post_export_feedback(self) -> None:
        pending, self._post_export_pending_feedback = self._post_export_pending_feedback, None
        if pending is not None:
            self._post_export_feedback(pending[0], failed=pending[1])

    def _discard_post_export_content_for_closed_privacy(self) -> None:
        """Case closure cannot substitute for unlocking a private export summary."""
        self._post_export_private_summary_discarded = True
        self._post_export_pending_feedback = None
        self._post_export_binding = None
        parent = self._package_export_dialog
        if parent is not None:
            self._assisted_session.discard_hidden_window(parent)
            # Bypass reject(), which deliberately keeps a busy operation visible.
            # The correlated removal result is retained only as a generic status.
            parent.done(QDialog.DialogCode.Rejected)

    def _discard_post_export_deletion_for_privacy(self) -> None:
        """A restored window must never restore destructive consent from before a lock."""
        parent = self._package_export_dialog
        if parent is not None:
            parent.clear_post_export_consent()
        had_review = self._post_export_review_operation_id is not None
        self._post_export_review_operation_id = None
        dialog = self._post_export_deletion_dialog
        if had_review or dialog is not None:
            self._post_export_binding = None
            if dialog is not None:
                self._assisted_session.discard_hidden_window(dialog)
                dialog.reject()
            else:
                self._commands.post_export_deletion_cancel_requested.emit()
            if had_review:
                self._package_cancellation = None
            self._post_export_feedback(
                tr(
                    "Case deletion review cancelled when the case locked. Review it again after unlocking."
                )
            )

    @Slot()
    def _choose_package_summary_destination(self) -> None:
        dialog = self._package_export_dialog
        if dialog is None or self._package_summary_save_operation_id is not None:
            return
        selected, _filter = QFileDialog.getSaveFileName(
            dialog,
            tr("Save displayed package summary"),
            "package-summary.txt",
            tr("Plain text (*.txt)"),
            options=self._file_dialog_options(),
        )
        if not selected:
            dialog.complete_summary_save(succeeded=False)
            return
        self._package_summary_save_operation_id = self._allocate_operation_id()
        self._package_cancellation = OperationCancellation()
        self._commands.package_summary_save_requested.emit(
            self._package_summary_save_operation_id, Path(selected), self._package_cancellation
        )

    @Slot(int)
    def _package_summary_saved(self, operation_id: int) -> None:
        if operation_id != self._package_summary_save_operation_id:
            return
        self._package_summary_save_operation_id = None
        self._package_cancellation = None
        if self._package_export_dialog is not None:
            self._package_export_dialog.complete_summary_save(succeeded=True)

    @Slot(int, str, object)
    def _package_failed(self, operation_id: int, phase: str, failure: object) -> None:
        dialog = self._package_export_dialog
        compression_phases = {
            "compression_prepare": "preparing",
            "compression_preview": "preview",
            "compression_export": "export",
        }
        if phase in compression_phases:
            expected_phase = compression_phases[phase]
            if (
                operation_id != self._package_export_operation_id
                or self._compression_phase != expected_phase
            ):
                return
            self._package_export_operation_id = None
            self._package_cancellation = None
            self._compression_phase = None
            self._compression_preview_request = None
            if expected_phase != "preview":
                self._compression_review_state = None
                self._compression_pair_review_id = None
            if self._finish_package_redaction_failure(failure):
                return
            if expected_phase == "export":
                self._publish_package_failure("export", failure)
            if self._compression_dialog is not None:
                self._compression_dialog.show_failure(
                    failure.code if type(failure) is UiFailure else "",
                    phase=expected_phase
                    if expected_phase != "preview" or self._compression_review_state is not None
                    else "preparing",
                )
            return
        if phase == "save" and operation_id == self._package_summary_save_operation_id:
            self._package_summary_save_operation_id = None
            self._package_cancellation = None
            if dialog is not None:
                dialog.complete_summary_save(succeeded=False)
            return
        if phase == "cancel" and operation_id == self._package_cancel_operation_id:
            self._package_cancel_operation_id = None
            self._package_release_failed = True
            self._update_package_availability()
            self._announce(
                tr(
                    "Package review could not be released safely. Choose Retry closing package review before continuing."
                ),
                assertive=True,
            )
            self._focus_package_export_button()
            return
        if phase == "review" and operation_id == self._package_review_operation_id:
            self._package_review_operation_id = None
            self._package_review_state = None
            self._package_cancellation = None
            if (
                self._active_operation is not None
                and self._active_operation.operation_id == operation_id
            ):
                self._finish_active_operation()
        elif phase == "export" and operation_id == self._package_export_operation_id:
            self._package_export_operation_id = None
            self._package_cancellation = None
            self._package_review_state = None
        else:
            return
        if self._finish_package_redaction_failure(failure):
            return
        self._publish_package_failure(phase, failure)
        if dialog is not None:
            dialog.show_failure(
                phase=phase, code=failure.code if type(failure) is UiFailure else None
            )
        else:
            self._release_package_review()
        self._update_package_availability()

    def _finish_package_redaction_failure(self, failure: object) -> bool:
        if (
            type(failure) is not UiFailure
            or failure.code != "REDACTION_PIPELINE_DISABLED"
            or not self._queue_redaction_export_report()
        ):
            return False
        dialog = self._package_export_dialog
        if dialog is not None:
            dialog.reject()
        else:
            self._release_package_review()
        # The result is shown only after the backend confirms that the package
        # lease was released, so Review marks cannot overlap its authority.
        self._show_pending_redaction_export_report()
        return True

    @Slot(int)
    def _package_dialog_finished(self, _result: int) -> None:
        dialog = self._package_export_dialog
        if dialog is None:
            return
        if not self._package_terminal_result_published:
            summary = dialog.summary
            self._publish_package_result(
                (summary or PackageSummary(PackageSummaryStatus.PLANNED)).status_message
            )
        inventory = self._post_export_deletion_dialog
        if inventory is not None:
            inventory.reject()
        self._post_export_binding = None
        self._post_export_pending_feedback = None
        self._commands.post_export_deletion_cancel_requested.emit()
        self._dispose_compression_dialog()
        dialog.deleteLater()
        self._package_export_dialog = None
        self._package_review_state = None
        self._release_package_review()

    def _release_package_review(self) -> None:
        if self._package_cancel_operation_id is not None:
            return
        self._package_cancel_operation_id = self._allocate_operation_id()
        self._update_package_availability()
        self._commands.package_review_cancel_requested.emit(self._package_cancel_operation_id)

    @Slot(int)
    def _package_review_cancelled(self, operation_id: int) -> None:
        if operation_id != self._package_cancel_operation_id:
            return
        self._package_cancel_operation_id = None
        self._package_release_failed = False
        self._update_package_availability()
        if self._show_pending_redaction_export_report():
            return
        if not self._closing_requested and self._package_export_button.isEnabled():
            QTimer.singleShot(0, self._focus_package_export_button)

    def _focus_package_export_button(self) -> None:
        self._focus_trace_invoker(self._package_export_button)
        if not self._closing_requested and self._package_export_button.isEnabled():
            self.activateWindow()
            self._package_export_button.setFocus(Qt.FocusReason.OtherFocusReason)
            self._ensure_widget_visible(self._package_export_button)

    def _update_package_availability(self) -> None:
        self._update_import_availability()
        self._update_page_actions()
        self._update_quality_actions()
        self._update_preview_retry_action()

    def _package_export_is_opening_or_active(self) -> bool:
        return (
            self._pending_package_review
            or self._package_export_dialog is not None
            or self._package_review_operation_id is not None
            or self._package_export_operation_id is not None
            or self._package_cancel_operation_id is not None
            or self._package_summary_save_operation_id is not None
            or self._post_export_review_operation_id is not None
            or self._post_export_delete_operation_id is not None
            or self._text_companion_operation_id is not None
            or self._package_release_failed
        )

    def _request_portal_constraints(self) -> None:
        """Open the exclusive, keyboard-complete packaging-constraint editor."""

        if not self._portal_constraints_button.isEnabled() or not self._prepare_for_mutation():
            return
        if self._portal_constraints_is_opening_or_active():
            return
        operation_id = self._allocate_operation_id()
        self._portal_constraints_start_operation_id = operation_id
        self._autosave_timer.stop()
        self._preview_refresh_timer.stop()
        self._deferred_preview_state = None
        self._hide_error()
        self._update_import_availability()
        self._update_page_actions()
        self._update_quality_actions()
        self._announce(
            tr(
                "Opening package constraints. These local checks do not guarantee portal acceptance."
            )
        )
        self._commands.portal_constraints_begin_requested.emit(operation_id)

    @Slot(int, object)
    def _portal_constraints_started(self, operation_id: int, editor_state: object) -> None:
        if operation_id != self._portal_constraints_start_operation_id:
            return
        self._portal_constraints_start_operation_id = None
        current = self._state
        if (
            self._closing_requested
            or type(editor_state) is not ConstraintEditorState
            or current is None
            or editor_state.case_id != current.case_id
            or editor_state.manifest_revision != current.revision
            or not is_constraint_announcement_safe(editor_state.announcement)
            or self._portal_constraints_dialog is not None
        ):
            if not self._closing_requested and self._portal_constraints_dialog is None:
                cancel_operation_id = self._allocate_operation_id()
                self._portal_constraints_cancel_operation_id = cancel_operation_id
                self._commands.portal_constraints_abort_requested.emit(cancel_operation_id)
            self._update_import_availability()
            self._update_page_actions()
            self._update_quality_actions()
            return
        dialog = ConstraintEditorDialog(editor_state, self)
        dialog.apply_requested.connect(self._apply_portal_constraints)
        dialog.finished.connect(self._portal_constraints_finished)
        self._portal_constraints_dialog = dialog
        self._update_import_availability()
        self._update_page_actions()
        self._update_quality_actions()
        dialog.open()

    @Slot(int, object)
    def _portal_constraints_start_failed(self, operation_id: int, failure: object) -> None:
        if operation_id != self._portal_constraints_start_operation_id:
            return
        self._portal_constraints_start_operation_id = None
        if self._state is not None and self._state.autosave_pending and not self._closing_requested:
            self._autosave_timer.start(_AUTOSAVE_DEBOUNCE_MS)
        self._update_import_availability()
        self._update_page_actions()
        self._update_quality_actions()
        self._show_portal_constraints_failure(failure)

    @Slot(object)
    def _apply_portal_constraints(self, editor_state: object) -> None:
        dialog = self._portal_constraints_dialog
        if (
            dialog is None
            or type(editor_state) is not ConstraintEditorState
            or editor_state is not dialog.state
            or self._portal_constraints_apply_operation_id is not None
        ):
            return
        operation_id = self._allocate_operation_id()
        self._portal_constraints_apply_operation_id = operation_id
        self._commands.portal_constraints_apply_requested.emit(operation_id, editor_state)

    @Slot(int, object)
    def _portal_constraints_applied(self, operation_id: int, state: object) -> None:
        if operation_id != self._portal_constraints_apply_operation_id:
            return
        self._portal_constraints_apply_operation_id = None
        dialog = self._portal_constraints_dialog
        if dialog is None:
            return
        if type(state) is not OrganizerViewState or not _portal_constraints_apply_result_matches(
            dialog,
            state,
            self._state,
        ):
            dialog.mark_apply_failed(
                "PWS_UI_RESPONSE_INVALID",
                tr("PaperWorkShell received an invalid constraint save confirmation."),
            )
            return
        if dialog.mark_applied():
            self._announce(tr("Package constraints applied and saved."))

    @Slot(int, object)
    def _portal_constraints_apply_failed(self, operation_id: int, failure: object) -> None:
        if operation_id != self._portal_constraints_apply_operation_id:
            return
        self._portal_constraints_apply_operation_id = None
        dialog = self._portal_constraints_dialog
        if dialog is None:
            return
        if type(failure) is UiFailure:
            dialog.mark_apply_failed(failure.code, failure.message)
            return
        dialog.mark_apply_failed(
            "PWS_UI_RESPONSE_INVALID",
            tr("PaperWorkShell received an invalid constraint save failure."),
        )

    @Slot(int)
    def _portal_constraints_finished(self, result: int) -> None:
        dialog = self._portal_constraints_dialog
        if dialog is None:
            return
        state = dialog.state
        cancelled = (
            result != int(QDialog.DialogCode.Accepted)
            and not dialog.commit_started
            and self._portal_constraints_apply_operation_id is None
        )
        dialog.deleteLater()
        self._portal_constraints_dialog = None
        self._portal_constraints_apply_operation_id = None
        if cancelled:
            operation_id = self._allocate_operation_id()
            self._portal_constraints_cancel_operation_id = operation_id
            self._commands.portal_constraints_cancel_requested.emit(operation_id, state)
        self._update_import_availability()
        self._update_page_actions()
        self._update_quality_actions()
        if (
            not cancelled
            and not self._closing_requested
            and self._portal_constraints_button.isEnabled()
        ):
            QTimer.singleShot(0, self._focus_portal_constraints_button_after_dialog)

    @Slot(int)
    def _portal_constraints_cancelled(self, operation_id: int) -> None:
        if operation_id != self._portal_constraints_cancel_operation_id:
            return
        self._portal_constraints_cancel_operation_id = None
        self._update_import_availability()
        self._update_page_actions()
        self._update_quality_actions()
        self._announce(tr("Package constraint changes cancelled. Nothing was applied."))
        if not self._closing_requested and self._portal_constraints_button.isEnabled():
            QTimer.singleShot(0, self._focus_portal_constraints_button_after_dialog)

    def _focus_portal_constraints_button_after_dialog(self) -> None:
        self._focus_trace_invoker(self._portal_constraints_button)
        if self._closing_requested or not self._portal_constraints_button.isEnabled():
            return
        self.activateWindow()
        self._portal_constraints_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._ensure_widget_visible(self._portal_constraints_button)

    @Slot(int, object)
    def _portal_constraints_cancel_failed(self, operation_id: int, failure: object) -> None:
        if operation_id != self._portal_constraints_cancel_operation_id:
            return
        self._portal_constraints_cancel_operation_id = None
        self._update_import_availability()
        self._update_page_actions()
        self._update_quality_actions()
        self._show_portal_constraints_failure(failure)

    def _show_portal_constraints_failure(self, failure: object) -> None:
        safe_code = (
            failure.code if type(failure) is UiFailure else "PWS_UI_CONSTRAINT_RESPONSE_INVALID"
        )
        safe_message = (
            tr(failure.message)
            if type(failure) is UiFailure
            else tr("PaperWorkShell could not finish the constraint action safely. Try again.")
        )
        self._set_error_text(f"{safe_code}: {safe_message}")
        self._error_label.setVisible(True)
        self._announce(
            tr("Package constraint editor could not finish. {value1}", value1=safe_message),
            assertive=True,
        )
        self._focus_error_recovery(self._portal_constraints_button)

    def _choose_signature_image(self) -> None:
        """Choose one local JPEG/PNG without retaining or displaying its name."""

        if not self._place_signature_image_button.isEnabled():
            return
        page_id = self._selected_page_id()
        if page_id is None:
            return
        selected, _selected_filter = QFileDialog.getOpenFileName(
            self,
            tr("Place signature image"),
            "",
            tr("Signature images (*.png *.jpg *.jpeg);;All files (*)"),
            options=self._file_dialog_options(),
        )
        if not selected:
            self._announce(tr("Signature-image selection cancelled."))
            return
        self._signature_image_source_path = Path(selected)
        self._signature_image_page_id = page_id
        self._preview_refresh_timer.stop()
        self._deferred_preview_state = None
        active = self._active_operation
        if active is not None:
            if active.kind is not _LongOperationKind.PREVIEW:
                self._clear_signature_image_source()
                return
            self._cancel_active_preview()
            self._update_page_actions()
            self._announce(
                tr("Stopping the current page preview before preparing the visual image.")
            )
            return
        self._start_signature_image_prepare()

    def _start_signature_image_prepare(self) -> None:
        source_path = self._signature_image_source_path
        page_id = self._signature_image_page_id
        if (
            source_path is None
            or page_id is None
            or self._active_operation is not None
            or self._closing_requested
            or self._signature_image_dialog is not None
        ):
            return
        operation = self._begin_long_operation(_LongOperationKind.SIGNATURE_IMAGE)
        self._signature_image_prepare_operation_id = operation.operation_id
        self._hide_error()
        self._announce(
            tr("Preparing a private signature-image preview. You can cancel this operation.")
        )
        self._commands.signature_image_prepare_requested.emit(
            operation.operation_id,
            page_id,
            source_path,
            operation.cancellation,
        )

    @Slot(int, object)
    def _signature_image_prepared(self, operation_id: int, draft: object) -> None:
        if operation_id != self._signature_image_prepare_operation_id:
            return
        self._signature_image_prepare_operation_id = None
        if (
            self._closing_requested
            or type(draft) is not SignatureImagePlacementDraft
            or draft.page_id != self._signature_image_page_id
            or draft.page_id != self._selected_page_id()
            or self._state is None
            or draft.case_id != self._state.case_id
            or draft.manifest_revision != self._state.revision
            or self._signature_image_dialog is not None
        ):
            if type(draft) is SignatureImagePlacementDraft:
                self._request_signature_image_cancel(draft.draft_id)
            else:
                self._clear_signature_image_source()
            return
        dialog = SignatureImagePlacementDialog(draft, self)
        dialog.place_requested.connect(self._place_signature_image)
        dialog.retry_requested.connect(self._retry_signature_image)
        dialog.cancel_operation_requested.connect(self._cancel_active_operation)
        dialog.finished.connect(self._signature_image_dialog_finished)
        self._signature_image_dialog = dialog
        self._signature_image_committed = False
        self._update_page_actions()
        self._update_quality_actions()
        dialog.open()

    @Slot(int, object)
    def _signature_image_prepare_failed(self, operation_id: int, failure: object) -> None:
        if operation_id != self._signature_image_prepare_operation_id:
            return
        self._signature_image_prepare_operation_id = None
        self._clear_signature_image_source()
        self._update_page_actions()
        if type(failure) is UiFailure:
            self._failed(failure)

    @Slot(object)
    def _place_signature_image(self, request: object) -> None:
        dialog = self._signature_image_dialog
        if (
            dialog is None
            or type(request) is not SignatureImagePlacementRequest
            or request.draft_id != dialog.draft.draft_id
            or self._signature_image_place_operation_id is not None
            or self._active_operation is not None
        ):
            return
        operation = self._begin_long_operation(_LongOperationKind.SIGNATURE_IMAGE)
        self._signature_image_place_operation_id = operation.operation_id
        self._commands.signature_image_place_requested.emit(
            operation.operation_id,
            request,
            operation.cancellation,
        )

    @Slot(int, object)
    def _signature_image_placed(self, operation_id: int, outcome: object) -> None:
        if operation_id != self._signature_image_place_operation_id:
            return
        self._signature_image_place_operation_id = None
        dialog = self._signature_image_dialog
        if (
            dialog is None
            or type(outcome) is not OrganizerSignatureImagePlacementOutcome
            or outcome.committed_placement.case_id != dialog.draft.case_id
        ):
            if dialog is not None:
                dialog.show_failure("PWS_UI_RESPONSE_INVALID", None)
            return
        self._signature_image_committed = True
        dialog.mark_placed()
        self._clear_signature_image_source()
        self._announce(tr(outcome.safe_message), assertive=outcome.refresh_required)

    @Slot(int, object)
    def _signature_image_place_failed(self, operation_id: int, failure: object) -> None:
        if operation_id != self._signature_image_place_operation_id:
            return
        self._signature_image_place_operation_id = None
        dialog = self._signature_image_dialog
        if dialog is None:
            return
        code = failure.code if type(failure) is UiFailure else None
        message = failure.message if type(failure) is UiFailure else None
        if code == SignatureImageErrorCode.CANCELLED.value:
            try:
                dialog.finished.disconnect(self._signature_image_dialog_finished)
            except RuntimeError:
                pass
            dialog.done(QDialog.DialogCode.Rejected)
            dialog.deleteLater()
            self._signature_image_dialog = None
            self._signature_image_committed = False
            self._signature_image_retry_after_cancel = False
            self._clear_signature_image_source()
            self._pending_failure_focus = "signature_image"
            self._update_page_actions()
            self._update_quality_actions()
            self._announce(
                tr("Signature-image placement cancelled. No placement was committed."),
                assertive=True,
            )
            return
        dialog.show_failure(code, message)

    @Slot()
    def _retry_signature_image(self) -> None:
        dialog = self._signature_image_dialog
        if dialog is None or self._signature_image_cancel_operation_id is not None:
            return
        self._signature_image_retry_after_cancel = True
        self._request_signature_image_cancel(dialog.draft.draft_id)

    @Slot(int)
    def _signature_image_dialog_finished(self, result: int) -> None:
        dialog = self._signature_image_dialog
        if dialog is None:
            return
        draft_id = dialog.draft.draft_id
        dialog.deleteLater()
        self._signature_image_dialog = None
        cancelled = (
            result != int(QDialog.DialogCode.Accepted)
            and not self._signature_image_committed
            and self._signature_image_cancel_operation_id is None
        )
        if cancelled:
            self._request_signature_image_cancel(draft_id)
        self._signature_image_committed = False
        self._update_page_actions()
        self._update_quality_actions()

    def _request_signature_image_cancel(self, draft_id: UUID) -> None:
        if self._signature_image_cancel_operation_id is not None:
            return
        operation_id = self._allocate_operation_id()
        self._signature_image_cancel_operation_id = operation_id
        self._commands.signature_image_cancel_requested.emit(operation_id, draft_id)

    @Slot(int)
    def _signature_image_cancelled(self, operation_id: int) -> None:
        if operation_id != self._signature_image_cancel_operation_id:
            return
        self._signature_image_cancel_operation_id = None
        retry = self._signature_image_retry_after_cancel
        self._signature_image_retry_after_cancel = False
        dialog = self._signature_image_dialog
        if dialog is not None:
            try:
                dialog.finished.disconnect(self._signature_image_dialog_finished)
            except RuntimeError:
                pass
            dialog.done(QDialog.DialogCode.Rejected)
            dialog.deleteLater()
            self._signature_image_dialog = None
        if retry:
            self._start_signature_image_prepare()
        else:
            self._clear_signature_image_source()
            self._announce(tr("Signature-image placement cancelled. Nothing was applied."))
        self._update_page_actions()
        self._update_quality_actions()
        if (
            not retry
            and not self._closing_requested
            and self._place_signature_image_button.isEnabled()
        ):
            QTimer.singleShot(0, self._focus_signature_image_button_after_dialog)

    def _focus_signature_image_button_after_dialog(self) -> None:
        self._focus_trace_invoker(self._place_signature_image_button)
        if self._closing_requested or not self._place_signature_image_button.isEnabled():
            return
        self.activateWindow()
        self._place_signature_image_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._ensure_widget_visible(self._place_signature_image_button)

    def _focus_remove_signature_image_button_after_dialog(self) -> None:
        self._focus_trace_invoker(self._remove_signature_image_button)
        if self._closing_requested or not self._remove_signature_image_button.isEnabled():
            return
        self.activateWindow()
        self._remove_signature_image_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._ensure_widget_visible(self._remove_signature_image_button)

    @Slot(int, object)
    def _signature_image_cancel_failed(self, operation_id: int, failure: object) -> None:
        if operation_id != self._signature_image_cancel_operation_id:
            return
        self._signature_image_cancel_operation_id = None
        self._signature_image_retry_after_cancel = False
        dialog = self._signature_image_dialog
        if dialog is not None:
            code = failure.code if type(failure) is UiFailure else None
            message = failure.message if type(failure) is UiFailure else None
            dialog.show_failure(code, message)
        self._clear_signature_image_source()
        self._update_page_actions()

    def _request_signature_image_placements(self) -> None:
        if not self._remove_signature_image_button.isEnabled():
            return
        page_id = self._selected_page_id()
        if page_id is None or not self._prepare_for_mutation():
            return
        if self._active_operation is not None:
            return
        request_id = self._allocate_operation_id()
        self._signature_image_list_request_id = request_id
        self._signature_image_page_id = page_id
        self._update_page_actions()
        self._commands.signature_image_placements_requested.emit(request_id, page_id)

    @Slot(int, object)
    def _signature_image_placements_ready(self, request_id: int, choices: object) -> None:
        if request_id != self._signature_image_list_request_id:
            return
        self._signature_image_list_request_id = None
        expected_page_id = self._signature_image_page_id
        if (
            expected_page_id is None
            or expected_page_id != self._selected_page_id()
            or type(choices) is not tuple
            or any(
                type(choice) is not SignatureImagePlacementChoice
                or choice.page_id != expected_page_id
                or choice.position != index
                or choice.total != len(choices)
                for index, choice in enumerate(choices, start=1)
            )
        ):
            self._announce(
                tr("PaperWorkShell could not verify the signature-image placement list safely."),
                assertive=True,
            )
            self._clear_signature_image_source()
            return
        if not choices:
            self._announce(tr("There are no signature-image placements on the selected page."))
            self._clear_signature_image_source()
            self._update_page_actions()
            self._place_signature_image_button.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        dialog = SignatureImageDeleteDialog(choices, self)
        dialog.delete_requested.connect(self._delete_signature_image)
        dialog.cancel_operation_requested.connect(self._cancel_active_operation)
        dialog.finished.connect(self._signature_image_delete_dialog_finished)
        self._signature_image_delete_dialog = dialog
        self._update_page_actions()
        dialog.open()

    @Slot(int, object)
    def _signature_image_placements_failed(self, request_id: int, failure: object) -> None:
        if request_id != self._signature_image_list_request_id:
            return
        self._signature_image_list_request_id = None
        self._clear_signature_image_source()
        self._update_page_actions()
        if type(failure) is UiFailure:
            self._failed(failure)

    @Slot(object, object)
    def _delete_signature_image(self, overlay_id: object, policy: object) -> None:
        if (
            self._signature_image_delete_dialog is None
            or type(overlay_id) is not UUID
            or self._signature_image_page_id is None
            or self._signature_image_delete_operation_id is not None
            or self._active_operation is not None
        ):
            return
        operation = self._begin_long_operation(_LongOperationKind.SIGNATURE_IMAGE)
        self._signature_image_delete_operation_id = operation.operation_id
        self._commands.signature_image_delete_requested.emit(
            operation.operation_id,
            self._signature_image_page_id,
            overlay_id,
            policy,
            operation.cancellation,
        )

    @Slot(int, object)
    def _signature_image_deleted(self, operation_id: int, outcome: object) -> None:
        if operation_id != self._signature_image_delete_operation_id:
            return
        self._signature_image_delete_operation_id = None
        dialog = self._signature_image_delete_dialog
        if dialog is None or type(outcome) is not OrganizerSignatureImageDeleteOutcome:
            if dialog is not None:
                dialog.show_failure("PWS_UI_RESPONSE_INVALID", None)
            return
        dialog.mark_deleted()
        self._announce(tr(outcome.safe_message), assertive=outcome.cleanup_warning is not None)

    @Slot(int, object)
    def _signature_image_delete_failed(self, operation_id: int, failure: object) -> None:
        if operation_id != self._signature_image_delete_operation_id:
            return
        self._signature_image_delete_operation_id = None
        dialog = self._signature_image_delete_dialog
        if dialog is None:
            return
        code = failure.code if type(failure) is UiFailure else None
        message = failure.message if type(failure) is UiFailure else None
        if code == SignatureImageErrorCode.CANCELLED.value:
            try:
                dialog.finished.disconnect(self._signature_image_delete_dialog_finished)
            except RuntimeError:
                pass
            dialog.done(QDialog.DialogCode.Rejected)
            dialog.deleteLater()
            self._signature_image_delete_dialog = None
            self._clear_signature_image_source()
            self._pending_failure_focus = "signature_image_delete"
            self._update_page_actions()
            self._announce(
                tr("Signature-image removal cancelled. The placement was not removed."),
                assertive=True,
            )
            return
        dialog.show_failure(code, message)

    @Slot(int)
    def _signature_image_delete_dialog_finished(self, _result: int) -> None:
        dialog = self._signature_image_delete_dialog
        if dialog is None:
            return
        dialog.deleteLater()
        self._signature_image_delete_dialog = None
        self._signature_image_delete_operation_id = None
        self._clear_signature_image_source()
        self._update_page_actions()
        if not self._closing_requested and self._remove_signature_image_button.isEnabled():
            self._remove_signature_image_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _clear_signature_image_source(self) -> None:
        self._signature_image_source_path = None
        self._signature_image_page_id = None

    def _show_scan_review_failure(self, failure: UiFailure) -> None:
        self._set_error_text(f"{failure.code}: {tr(failure.message)}")
        self._error_label.setVisible(True)
        self._announce(
            tr("Scan review failed. {value1}", value1=tr(failure.message)), assertive=True
        )
        self._focus_error_recovery(self._scan_review_button)

    def _change_full_order(self) -> None:
        if not self._change_order_button.isEnabled() or len(self._model.pages) < 2:
            return
        dialog = PageOrderDialog(
            tuple((page.page_id, page.position) for page in self._model.pages),
            self,
            page_labels={
                page.page_id: tr(
                    "{value1} — source page {value2}",
                    value1=page.source_display_name,
                    value2=page.source_page_number or 1,
                )
                for page in self._model.pages
            },
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
            or self._signature_image_is_opening_or_active()
            or self._package_export_is_opening_or_active()
            or self._state is None
            or self._state.case_id is None
        ):
            return False
        if self._import_is_pending_or_running():
            QApplication.beep()
            self._announce(
                tr("Wait for the current import to finish, or cancel it, before changing pages."),
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
        if self._pending_package_review:
            self._pending_package_review = False
            self._pending_failure_focus = "package_review"
            self._cancel_button.setEnabled(False)
            self._update_package_availability()
            self._announce(
                tr("Queued package inspection cancelled. No review results were applied."),
                assertive=True,
            )
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
                tr("Queued page quality check cancelled. No review results were applied."),
                assertive=True,
            )
            return
        active.cancellation.cancel()
        self._cancel_button.setEnabled(False)
        if active.kind is _LongOperationKind.QUALITY:
            self._quality_summary.setText(
                tr("Cancellation requested. Waiting for the page quality check to stop safely.")
            )
            self._quality_summary.setAccessibleDescription(self._quality_summary.text())
            self._quality_summary.setAccessibleName(
                tr("Page quality review status. {value1}", value1=self._quality_summary.text())
            )
        self._announce(
            tr("Cancellation requested. Waiting for the operation to stop."), assertive=True
        )

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
        self._announce(tr("Retrying the selected page preview."))
        self._start_preview((page_id,))

    def _begin_long_operation(self, kind: _LongOperationKind) -> _LongOperation:
        if self._active_operation is not None:
            raise RuntimeError("long operation overlap")
        operation_id = self._allocate_operation_id()
        operation = _LongOperation(operation_id, kind, OperationCancellation())
        self._cancel_trace_canvas()
        if kind is not _LongOperationKind.PREVIEW:
            self._cancel_trace_output_check()
        self._active_operation = operation
        self._announcer.reset_progress()
        if kind is _LongOperationKind.IMPORT:
            self._latest_import_progress = None
            self._import_presentation = _ImportPresentationState.ACTIVE
            self._update_organizer_guidance()
        message = tr("{value1} queued", value1=kind.value)
        self._progress.setAccessibleDescription(message)
        set_static_busy(self._progress)
        self._progress.setFormat(message)
        self._progress.setVisible(True)
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
        self._trace_destination_label.hide()
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

    def _core_overlay_tools_is_opening_or_active(self) -> bool:
        return (
            self._core_overlay_tools_start_operation_id is not None
            or self._core_overlay_tools_dialog is not None
            or self._core_overlay_tools_apply_operation_id is not None
            or self._core_overlay_tools_cancel_operation_id is not None
        )

    def _redaction_tools_is_opening_or_active(self) -> bool:
        return (
            self._redaction_tools_start_operation_id is not None
            or self._redaction_tools_dialog is not None
            or self._redaction_tools_save_operation_id is not None
            or self._redaction_tools_cancel_operation_id is not None
            or self._redaction_export_dialog is not None
            or self._removal_review_dialog is not None
        )

    def _checklist_is_opening_or_active(self) -> bool:
        return (
            self._checklist_start_operation_id is not None
            or self._checklist_dialog is not None
            or self._checklist_apply_operation_id is not None
            or self._checklist_cancel_operation_id is not None
        )

    def _portal_constraints_is_opening_or_active(self) -> bool:
        return (
            self._portal_constraints_start_operation_id is not None
            or self._portal_constraints_dialog is not None
            or self._portal_constraints_apply_operation_id is not None
            or self._portal_constraints_cancel_operation_id is not None
        )

    def _signature_image_is_opening_or_active(self) -> bool:
        active = self._active_operation
        return (
            self._signature_image_prepare_operation_id is not None
            or self._signature_image_place_operation_id is not None
            or self._signature_image_cancel_operation_id is not None
            or self._signature_image_list_request_id is not None
            or self._signature_image_delete_operation_id is not None
            or self._signature_image_dialog is not None
            or self._signature_image_delete_dialog is not None
            or (active is not None and active.kind is _LongOperationKind.SIGNATURE_IMAGE)
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
            message = tr(
                "Page quality check queued. PaperWorkShell is stopping the current preview first. No review results are shown yet."
            )
        elif presentation is _QualityPresentationState.ACTIVE:
            message = tr(
                "Checking this saved revision. Results are limited automatic suggestions and do not complete a requirement."
            )
        elif presentation is _QualityPresentationState.READY and snapshot is not None:
            finding_count = len(snapshot.findings)
            if finding_count:
                affected_pages = len({finding.page_id for finding in snapshot.findings})
                finding_noun = tr("signal") if finding_count == 1 else tr("signals")
                page_noun = tr("page") if affected_pages == 1 else tr("pages")
                message = tr(
                    "Needs review: {value1} automatic review {value2} on {value3} {value4}. Select each signal and review the page; these suggestions do not complete a requirement.",
                    value1=finding_count,
                    value2=finding_noun,
                    value3=affected_pages,
                    value4=page_noun,
                )
            else:
                message = tr(
                    "No review signals were found by these limited checks. Review every page yourself before export."
                )
        elif presentation is _QualityPresentationState.CANCELLED:
            message = tr(
                "Page quality check cancelled. No review results were applied. Run the check again when ready."
            )
        elif presentation is _QualityPresentationState.FAILED:
            message = tr(
                "Page quality check did not finish. No review results are shown. Review the Action error and run the check again."
            )
        else:
            message = tr(
                "Not checked for this saved revision. Automatic quality signals are suggestions; review every page yourself."
            )
        self._quality_summary.setText(message)
        self._quality_summary.setAccessibleName(
            tr("Page quality review status. {value1}", value1=message)
        )
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
            and not self._core_overlay_tools_is_opening_or_active()
            and not self._redaction_tools_is_opening_or_active()
            and not self._checklist_is_opening_or_active()
            and not self._portal_constraints_is_opening_or_active()
            and not self._package_export_is_opening_or_active()
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
        self._trace_split.select_pane("inspector")
        self._trace_inspector_tabs.setCurrentIndex(1)
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
            message = tr(
                "Closing PaperWorkShell safely. Waiting for the current import to stop and secure cleanup to finish."
            )
        elif self._case_reopen_required:
            message = tr(
                "The saved case must be reopened before continuing. Choose Close case, then reopen this case."
            )
        elif self._import_presentation is _ImportPresentationState.FAILED:
            recovery = (
                tr("Review Action error below, then choose")
                if self._error_label.isVisible()
                else tr("Choose")
            )
            message = tr(
                "Import did not finish. {value1} Add file (Ctrl+I) to try again.",
                value1=recovery,
            )
        elif self._import_presentation is _ImportPresentationState.CANCELLED:
            message = tr(
                "Import cancelled. No source was committed. Choose Add file (Ctrl+I) to try again."
            )
        elif self._import_presentation is _ImportPresentationState.QUEUED:
            message = tr(
                "Import queued. PaperWorkShell is stopping the current preview before import starts. Keep PaperWorkShell open."
            )
        elif self._import_is_pending_or_running():
            progress = self._latest_import_progress
            phase = (
                ""
                if progress is None
                else tr(" — {value1}…", value1=tr(progress.phase))
                if progress.total == 1 and progress.completed == 0
                else tr(
                    " — {value1}: {value2} of {value3}",
                    value1=tr(progress.phase),
                    value2=progress.completed,
                    value3=progress.total,
                )
            )
            message = tr(
                "Importing document{value1}. PaperWorkShell copies and checks the file before new pages appear. Keep PaperWorkShell open; press Escape to cancel safely.",
                value1=phase,
            )
        elif not state.pages:
            message = tr("No pages yet. Choose Add file (Ctrl+I) to add a PDF, JPEG, or PNG.")
        else:
            self._organizer_guidance.setVisible(False)
            return
        self._organizer_guidance.setText(message)
        self._organizer_guidance.setAccessibleName(
            tr("Page organizer guidance. {value1}", value1=message)
        )
        self._organizer_guidance.setAccessibleDescription(message)
        self._organizer_guidance.setVisible(True)

    def _set_error_text(self, message: str) -> None:
        message = tr(message)
        self._error_label.setText(message)
        self._error_label.setAccessibleName(tr("Action error. {value1}", value1=message))

    def _focus_error_recovery(self, action: QWidget) -> None:
        if isinstance(action, QPushButton) and not action.isVisible() and action.isEnabled():
            action = self._trace_button_proxy.get(action, action)
        if self._focus_widget_is_available(action):
            action.setFocus(Qt.FocusReason.OtherFocusReason)
            self._ensure_widget_visible(action)
        else:
            self._focus_safe_control()

    def _set_status_text(self, message: str) -> None:
        message = tr(message)
        self._status_label.setText(message)
        self._status_label.setAccessibleName(
            tr("Current operation status. {value1}", value1=message)
        )

    def _announce(self, message: str, *, assertive: bool = False) -> None:
        message = tr(message)
        if self._assisted_session.locked:
            return
        self._set_status_text(message)
        if (
            self._active_operation is None
            and self._state is not None
            and self._state.case_id is not None
        ):
            self._last_result_label.setText(message)
            self._last_result_label.show()
        self._announcer.announce(message, assertive=assertive)

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
                    self._trace_document_stack.currentWidget(),
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
        self._reveal_control_in_scroll_area(self._application_scroll, widget)

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
            and not self._core_overlay_tools_is_opening_or_active()
            and not self._redaction_tools_is_opening_or_active()
            and not self._checklist_is_opening_or_active()
            and not self._portal_constraints_is_opening_or_active()
            and not self._package_export_is_opening_or_active()
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
            self._trace_split.select_pane("inspector")
            self._trace_inspector_tabs.setCurrentIndex(1)
            self._update_quality_actions()
            if self._quality_check_button.isVisible() and self._quality_check_button.isEnabled():
                self._quality_check_button.setFocus(Qt.FocusReason.OtherFocusReason)
                self._ensure_widget_visible(self._quality_check_button)
                return
        elif target == "pdf_export" and self._pages_pdf_export_button.isEnabled():
            self._pages_pdf_export_button.setFocus(Qt.FocusReason.OtherFocusReason)
            self._ensure_widget_visible(self._pages_pdf_export_button)
            return
        elif target == "package_review" and self._package_export_button.isEnabled():
            self._focus_package_export_button()
            return
        elif target == "signature_image":
            self._update_page_actions()
            if self._place_signature_image_button.isEnabled():
                QTimer.singleShot(0, self._focus_signature_image_button_after_dialog)
                return
        elif target == "signature_image_delete":
            self._update_page_actions()
            if self._remove_signature_image_button.isEnabled():
                QTimer.singleShot(0, self._focus_remove_signature_image_button_after_dialog)
                return
        if self._error_label.isVisible():
            self._error_label.setFocus(Qt.FocusReason.OtherFocusReason)
            self._ensure_widget_visible(self._error_label)
        else:
            self._focus_safe_control()

    def _update_import_availability(self) -> None:
        state = self._state
        self._import_file_label.setVisible(
            bool(self._import_file_label.text())
            and state is not None
            and state.case_id is not None
            and (
                self._import_is_pending_or_running()
                or self._import_presentation
                in {_ImportPresentationState.FAILED, _ImportPresentationState.CANCELLED}
            )
        )
        self._import_button.setEnabled(
            self._controls_enabled
            and state is not None
            and state.case_id is not None
            and not self._case_reopen_required
            and not self._import_is_pending_or_running()
            and not self._scan_review_is_opening_or_active()
            and not self._quality_is_pending_or_running()
            and not self._core_overlay_tools_is_opening_or_active()
            and not self._redaction_tools_is_opening_or_active()
            and not self._checklist_is_opening_or_active()
            and not self._portal_constraints_is_opening_or_active()
            and not self._package_export_is_opening_or_active()
            and not self._signature_image_is_opening_or_active()
        )
        self._update_ocr_language_availability()

    def _update_ocr_language_availability(self) -> None:
        self._ocr_language_button.setEnabled(
            self._controls_enabled
            and not self._closing_requested
            and self._active_operation is None
            and not self._scan_review_is_opening_or_active()
            and not self._quality_is_pending_or_running()
            and not self._core_overlay_tools_is_opening_or_active()
            and not self._redaction_tools_is_opening_or_active()
            and not self._checklist_is_opening_or_active()
            and not self._portal_constraints_is_opening_or_active()
            and not self._package_export_is_opening_or_active()
            and not self._signature_image_is_opening_or_active()
            and self._ocr_language_dialog is None
            and self._ocr_language_refresh_request_id is None
        )

    def _set_controls_enabled(self, enabled: bool) -> None:
        self._controls_enabled = enabled
        for widget in (
            self._case_list,
            self._create_case_button,
            self._assisted_create_button,
            self._open_case_button,
            self._refresh_cases_button,
            self._close_case_button,
            self._assisted_lock_button,
            self._page_view,
            self._move_earlier_button,
            self._move_later_button,
            self._rotate_button,
            self._remove_button,
            self._scan_review_button,
            self._change_order_button,
            self._core_overlay_tools_button,
            self._redaction_tools_button,
            self._removal_export_button,
            self._place_signature_image_button,
            self._remove_signature_image_button,
            self._checklist_button,
            self._portal_constraints_button,
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
        self._cancel_trace_canvas()
        self._cancel_trace_output_check()
        diagnostic_dialog = self._diagnostic_bundle_dialog
        if diagnostic_dialog is not None:
            pending = diagnostic_dialog.busy
            self._diagnostic_bundle_shutdown_requested = pending
            diagnostic_dialog.reject()
            if pending:
                self._announce(
                    tr(
                        "Waiting for support bundle publication or safe cancellation before closing."
                    ),
                    assertive=True,
                )
                return
        self._pending_package_review = False
        package_dialog = self._package_export_dialog
        if (
            self._package_review_operation_id is not None
            or self._package_export_operation_id is not None
            or self._package_summary_save_operation_id is not None
            or self._post_export_review_operation_id is not None
            or self._post_export_delete_operation_id is not None
            or self._text_companion_operation_id is not None
        ):
            self._cancel_package_operation()
            self._announce(
                tr(
                    "Wait for the package operation to finish and review its final result before closing PaperWorkShell."
                ),
                assertive=True,
            )
            return
        if package_dialog is not None:
            if self._compression_dialog is not None:
                self._dispose_compression_dialog()
                package_dialog.show_failure()
            package_dialog.reject()
        overlay_dialog = self._core_overlay_tools_dialog
        if (
            overlay_dialog is not None
            and not overlay_dialog.apply_pending
            and overlay_dialog.latest_state_for_shutdown() is None
        ):
            self._announce(
                tr("Resolve the visual overlay editor fields before closing PaperWorkShell."),
                assertive=True,
            )
            return
        redaction_dialog = self._redaction_tools_dialog
        if (
            redaction_dialog is not None
            and not redaction_dialog.save_pending
            and redaction_dialog.latest_state_for_shutdown() is None
        ):
            self._announce(
                tr("Resolve the redaction mark editor fields before closing PaperWorkShell."),
                assertive=True,
            )
            return
        constraint_dialog = self._portal_constraints_dialog
        if (
            constraint_dialog is not None
            and not constraint_dialog.apply_pending
            and constraint_dialog.latest_state_for_shutdown() is None
        ):
            self._announce(
                tr("Resolve the package constraint editor fields before closing PaperWorkShell."),
                assertive=True,
            )
            return
        retrying_cleanup = self._retry_close_button.isVisible()
        queued_import = self._deferred_import is not None
        active = self._active_operation
        active_import = active is not None and active.kind is _LongOperationKind.IMPORT
        self._closing_requested = True
        self._pending_redaction_export_report = None
        self._pending_redaction_review = None
        self._pending_removal_review = None
        self._pending_removal_retry = False
        if self._removal_review_dialog is not None:
            self._removal_review_dialog.reject()
        if self._redaction_export_dialog is not None:
            self._redaction_export_dialog.reject()
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
        self._discard_core_overlay_tools_for_shutdown()
        self._discard_redaction_tools_for_shutdown()
        self._discard_checklist_for_shutdown()
        self._discard_portal_constraints_for_shutdown()
        self._discard_signature_images_for_shutdown()
        if self._cancel_button.hasFocus():
            self._focus_safe_control()
        self._cancel_active_operation_silently()
        self._update_organizer_guidance()
        self._set_controls_enabled(False)
        self._announce(
            tr("Retrying secure cleanup before closing PaperWorkShell.")
            if retrying_cleanup
            else tr("Closing PaperWorkShell after saving queued changes.")
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

    def _discard_core_overlay_tools_for_shutdown(self) -> None:
        dialog = self._core_overlay_tools_dialog
        if dialog is None or dialog.apply_pending:
            return
        state = dialog.latest_state_for_shutdown()
        if state is None:
            return
        try:
            dialog.finished.disconnect(self._core_overlay_tools_finished)
        except RuntimeError:
            pass
        dialog.done(QDialog.DialogCode.Rejected)
        dialog.deleteLater()
        self._core_overlay_tools_dialog = None
        if dialog.commit_started:
            # A retained draft can be edited after a failed Apply. Queue its
            # latest exact state before shutdown so an older pending candidate
            # cannot be saved while newer local changes are silently dropped.
            operation_id = self._allocate_operation_id()
            self._commands.core_overlay_tools_apply_requested.emit(operation_id, state)
        else:
            operation_id = self._allocate_operation_id()
            self._core_overlay_tools_cancel_operation_id = operation_id
            self._commands.core_overlay_tools_cancel_requested.emit(operation_id, state)

    def _discard_redaction_tools_for_shutdown(self) -> None:
        dialog = self._redaction_tools_dialog
        if dialog is None or dialog.save_pending:
            return
        state = dialog.latest_state_for_shutdown()
        if state is None:
            return
        try:
            dialog.finished.disconnect(self._redaction_tools_finished)
        except RuntimeError:
            pass
        dialog.done(QDialog.DialogCode.Rejected)
        dialog.deleteLater()
        self._redaction_tools_dialog = None
        if dialog.commit_started:
            # A retained draft can be edited after a failed Save. Queue its
            # latest exact state before shutdown so an older pending candidate
            # cannot be saved while newer local changes are silently dropped.
            operation_id = self._allocate_operation_id()
            self._commands.redaction_tools_save_requested.emit(operation_id, state)
        else:
            operation_id = self._allocate_operation_id()
            self._redaction_tools_cancel_operation_id = operation_id
            self._commands.redaction_tools_cancel_requested.emit(operation_id, state)

    def _discard_checklist_for_shutdown(self) -> None:
        dialog = self._checklist_dialog
        if dialog is None or dialog.apply_pending:
            return
        state = dialog.latest_state_for_shutdown()
        if state is None:
            return
        try:
            dialog.finished.disconnect(self._checklist_finished)
        except RuntimeError:
            pass
        dialog.done(QDialog.DialogCode.Rejected)
        dialog.deleteLater()
        self._checklist_dialog = None
        if dialog.commit_started:
            # A failed Apply leaves an exact pending candidate/authority for
            # retry. Queue the latest retained state before backend shutdown.
            operation_id = self._allocate_operation_id()
            self._commands.checklist_apply_requested.emit(operation_id, state)
        else:
            operation_id = self._allocate_operation_id()
            self._checklist_cancel_operation_id = operation_id
            self._commands.checklist_cancel_requested.emit(operation_id, state)

    def _discard_portal_constraints_for_shutdown(self) -> None:
        dialog = self._portal_constraints_dialog
        if dialog is None or dialog.apply_pending:
            return
        state = dialog.latest_state_for_shutdown()
        if state is None:
            return
        try:
            dialog.finished.disconnect(self._portal_constraints_finished)
        except RuntimeError:
            pass
        dialog.done(QDialog.DialogCode.Rejected)
        dialog.deleteLater()
        self._portal_constraints_dialog = None
        if dialog.commit_started:
            # Retry the latest complete state collected from the live form before shutdown.
            operation_id = self._allocate_operation_id()
            self._commands.portal_constraints_apply_requested.emit(operation_id, state)
        else:
            operation_id = self._allocate_operation_id()
            self._portal_constraints_cancel_operation_id = operation_id
            self._commands.portal_constraints_cancel_requested.emit(operation_id, state)

    def _discard_signature_images_for_shutdown(self) -> None:
        """Dismiss detached previews; backend shutdown owns retained-asset cleanup."""

        placement = self._signature_image_dialog
        if placement is not None:
            try:
                placement.finished.disconnect(self._signature_image_dialog_finished)
            except RuntimeError:
                pass
            placement.done(QDialog.DialogCode.Rejected)
            placement.deleteLater()
        deletion = self._signature_image_delete_dialog
        if deletion is not None:
            try:
                deletion.finished.disconnect(self._signature_image_delete_dialog_finished)
            except RuntimeError:
                pass
            deletion.done(QDialog.DialogCode.Rejected)
            deletion.deleteLater()
        self._signature_image_dialog = None
        self._signature_image_delete_dialog = None
        self._signature_image_retry_after_cancel = False
        self._signature_image_committed = False
        self._clear_signature_image_source()

    def _dismiss_core_overlay_tools_after_shutdown_refusal(self) -> None:
        """Expose the main retry surface after shutdown clears editor authority."""

        dialog = self._core_overlay_tools_dialog
        if dialog is None:
            return
        try:
            dialog.finished.disconnect(self._core_overlay_tools_finished)
        except RuntimeError:
            pass
        dialog.done(QDialog.DialogCode.Rejected)
        dialog.deleteLater()
        self._core_overlay_tools_dialog = None
        self._core_overlay_tools_apply_operation_id = None

    def _dismiss_redaction_tools_after_shutdown_refusal(self) -> None:
        """Expose the main retry surface after shutdown clears editor authority."""

        dialog = self._redaction_tools_dialog
        if dialog is None:
            return
        try:
            dialog.finished.disconnect(self._redaction_tools_finished)
        except RuntimeError:
            pass
        dialog.done(QDialog.DialogCode.Rejected)
        dialog.deleteLater()
        self._redaction_tools_dialog = None
        self._redaction_tools_save_operation_id = None

    def _dismiss_checklist_after_shutdown_refusal(self) -> None:
        """Expose the main retry surface after shutdown clears checklist authority."""

        dialog = self._checklist_dialog
        if dialog is None:
            return
        try:
            dialog.finished.disconnect(self._checklist_finished)
        except RuntimeError:
            pass
        dialog.done(QDialog.DialogCode.Rejected)
        dialog.deleteLater()
        self._checklist_dialog = None
        self._checklist_apply_operation_id = None

    def _dismiss_portal_constraints_after_shutdown_refusal(self) -> None:
        """Expose the main retry surface after shutdown clears constraint authority."""

        dialog = self._portal_constraints_dialog
        if dialog is None:
            return
        try:
            dialog.finished.disconnect(self._portal_constraints_finished)
        except RuntimeError:
            pass
        dialog.done(QDialog.DialogCode.Rejected)
        dialog.deleteLater()
        self._portal_constraints_dialog = None
        self._portal_constraints_apply_operation_id = None

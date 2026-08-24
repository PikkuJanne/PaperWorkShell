"""Keyboard-complete full-page editor for released visual-overlay intent.

The dialog edits an in-memory :class:`CoreOverlayToolsState`.  It performs no
document parsing, rendering, filesystem access, persistence, or logging.  Its
list and status surfaces deliberately describe only overlay type and geometry;
user-entered field values remain confined to the explicit value editor. Signature,
highlight, and shape payloads are geometry-only here; signature removal retains its
dedicated asset-cleanup workflow.
"""

from __future__ import annotations

from collections.abc import Callable
from itertools import pairwise
from typing import Final
from uuid import UUID

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAccessible,
    QAccessibleAnnouncementEvent,
    QCloseEvent,
    QKeySequence,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from paperworkshell.app.core_overlay_tools import (
    CORE_OVERLAY_TOOL_DESCRIPTORS,
    GUIDE_STEPS_MILLIONTHS,
    CoreOverlayToolsState,
    KeyboardDirection,
    core_overlay_tool_descriptor,
    is_core_overlay_announcement_safe,
    overlay_editor_kind_accessible_name,
    overlay_editor_record_accessible_name,
    overlay_editor_record_summary,
)
from paperworkshell.domain.case_lifecycle import CaseLifecycleErrorCode
from paperworkshell.domain.manifest_repository import ManifestRepositoryErrorCode
from paperworkshell.domain.page_coordinates import (
    NORMALIZED_PAGE_MILLIONTHS,
    NormalizedPageBox,
    PageCoordinateError,
    PageCoordinateTransform,
    RenderedImageSize,
)
from paperworkshell.domain.page_operations import PageOperationErrorCode
from paperworkshell.overlays.models import (
    MAX_FONT_SIZE_POINTS,
    MAX_OVERLAY_TEXT_CODE_POINTS,
    MIN_FONT_SIZE_POINTS,
    MarkOverlayPayload,
    OverlayError,
    OverlayErrorCode,
    OverlayKind,
    OverlayRecord,
    OverlayVisibility,
    TextualOverlayPayload,
)
from paperworkshell.ui.overlay_canvas import (
    DEFAULT_EDITOR_ZOOM_PERCENT,
    MAX_EDITOR_ZOOM_PERCENT,
    MIN_EDITOR_ZOOM_PERCENT,
    OverlayEditorCanvas,
)

_FOCUS_VISIBLE_TEXT_STYLE: Final = """
QLabel {
    border: 2px solid transparent;
    padding: 2px;
}
QLabel:focus {
    border-color: palette(highlight);
}
"""
_SAFE_CORE_FAILURE_CODES: Final = frozenset(
    {
        "PWS_UI_NOT_READY",
        "PWS_UI_REQUEST_INVALID",
        "PWS_UI_RESPONSE_INVALID",
        "PWS_UI_UNEXPECTED",
    }
    | {code.value for code in CaseLifecycleErrorCode}
    | {code.value for code in ManifestRepositoryErrorCode}
    | {code.value for code in OverlayErrorCode}
    | {code.value for code in PageOperationErrorCode}
)
_TEXT_INPUT_MAX_UTF16_UNITS: Final = (MAX_OVERLAY_TEXT_CODE_POINTS + 1) * 2
_UNREPRESENTABLE_SAVED_VALUE: Final = (
    "The selected saved value cannot be represented by this editor. Replace the value "
    "explicitly before changing its other properties."
)
_LIST_DESCRIPTION: Final = (
    "Select a visual mark with the arrow keys, then use the property fields or keyboard "
    "shortcuts. Dragging is not required. Each row identifies the available property path."
)
_COLOR_CHOICES: Final = (
    ("Black", "#000000ff"),
    ("Dark blue", "#203080ff"),
    ("Green", "#008040ff"),
    ("Dark red", "#a02020ff"),
)
_TEXTUAL_KINDS: Final = frozenset({OverlayKind.TEXT, OverlayKind.DATE, OverlayKind.INITIALS})
_ZOOM_CHOICES: Final = (25, 50, 75, 100, 125, 150, 200, 300, 400)
_GUIDE_LABELS: Final = {
    10_000: "1 percent",
    25_000: "2.5 percent",
    50_000: "5 percent",
    100_000: "10 percent",
}
_SAFE_CANVAS_MESSAGES: Final = frozenset(
    {
        "Pointer editing is unavailable for a deskewed preview. Use the exact fields or keyboard controls.",
        "No visual mark is at that point. Select a mark from the list to edit it exactly.",
    }
)


class CoreOverlayToolsDialog(QDialog):
    """A full-page projection plus authoritative native list and exact controls."""

    apply_requested = Signal(object)
    draft_changed = Signal(object)

    def __init__(
        self,
        state: CoreOverlayToolsState,
        parent: QWidget | None = None,
        *,
        page_transform: PageCoordinateTransform | None = None,
        page_preview: QPixmap | None = None,
        page_display_size: RenderedImageSize | None = None,
        pointer_mapping_enabled: bool = True,
    ) -> None:
        if type(state) is not CoreOverlayToolsState or not is_core_overlay_announcement_safe(
            state.announcement
        ):
            raise ValueError("state must be a CoreOverlayToolsState")
        super().__init__(parent)
        self._state = state
        self._apply_pending = False
        self._commit_started = False
        self._loading = False
        self._properties_dirty = False
        self._loaded_value_preserved = True
        self._value_edited = False
        self._shortcuts: list[QShortcut] = []

        if page_transform is not None and type(page_transform) is not PageCoordinateTransform:
            raise ValueError("page_transform must be a PageCoordinateTransform or None")
        if page_preview is not None and type(page_preview) is not QPixmap:
            raise ValueError("page_preview must be a QPixmap or None")
        if page_display_size is not None and type(page_display_size) is not RenderedImageSize:
            raise ValueError("page_display_size must be a RenderedImageSize or None")
        if type(pointer_mapping_enabled) is not bool:
            raise ValueError("pointer_mapping_enabled must be a bool")

        self.setObjectName("coreOverlayToolsDialog")
        self.setWindowTitle("Edit visual overlays")
        self.setAccessibleName("Edit visual overlays for selected page")
        self.setAccessibleDescription(
            "Create core visual marks and select, move, or resize every released overlay type "
            "using a full-page preview plus a complete list, exact fields, buttons, and keyboard "
            "shortcuts. No pointer is required."
        )
        self.setModal(True)
        self.resize(1180, 780)

        self._content = QWidget()
        self._content.setObjectName("coreOverlayToolsContent")
        content_layout = QVBoxLayout(self._content)

        heading = QLabel("Edit visual overlays")
        heading.setObjectName("coreOverlayToolsHeading")
        heading.setAccessibleName("Edit visual overlays")
        heading.setStyleSheet("font-weight: 600;")
        content_layout.addWidget(heading)

        guidance = QLabel(
            "Select any overlay in the native list, then use exact properties or named movement "
            "controls. Add creates the five core mark types; signature-image removal stays in "
            "its explicit organizer workflow. Positions and sizes use the full page before crop "
            "or rotation. Alt+Arrow moves; Alt+Shift+Arrow resizes."
        )
        guidance.setObjectName("coreOverlayToolsGuidance")
        guidance.setAccessibleName("Visual overlay editor instructions")
        guidance.setAccessibleDescription(guidance.text())
        guidance.setTextFormat(Qt.TextFormat.PlainText)
        guidance.setWordWrap(True)
        content_layout.addWidget(guidance)

        content_layout.addWidget(
            self._build_canvas_group(
                page_transform or PageCoordinateTransform(),
                page_preview,
                page_display_size,
                pointer_mapping_enabled,
            )
        )
        content_layout.addWidget(self._build_add_group())
        content_layout.addWidget(self._build_list_group())
        content_layout.addWidget(self._build_properties_group())
        content_layout.addLayout(self._build_edit_actions())

        self._status = QLabel(state.announcement)
        self._status.setObjectName("coreOverlayToolsStatus")
        self._status.setAccessibleName("Visual overlay editor status")
        self._status.setAccessibleDescription(state.announcement)
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setWordWrap(True)
        self._status.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._status.setStyleSheet(_FOCUS_VISIBLE_TEXT_STYLE)
        self._status.setFrameShape(QFrame.Shape.StyledPanel)
        content_layout.addWidget(self._status)
        content_layout.addStretch(1)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("coreOverlayToolsScrollArea")
        self._scroll.setAccessibleName("Visual overlay controls")
        self._scroll.setAccessibleDescription(
            "Scrollable visual overlay list, property fields, actions, and status."
        )
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scroll.setWidget(self._content)

        self._buttons = QDialogButtonBox()
        self._buttons.setObjectName("coreOverlayToolsDialogButtons")
        self._buttons.setAccessibleName("Apply or cancel visual overlay changes")
        self._apply_button = self._buttons.addButton(
            "Apply overlay changes",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self._apply_button.setObjectName("applyFormMarksButton")
        self._apply_button.setAccessibleName("Apply overlay changes")
        self._apply_button.setAccessibleDescription(
            "Request saving the complete local visual overlay draft."
        )
        self._apply_button.setDefault(True)
        self._cancel_button = self._buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._cancel_button.setObjectName("cancelFormMarksButton")
        self._cancel_button.setAccessibleName("Cancel visual overlay changes")
        self._cancel_button.setAccessibleDescription(
            "Discard this local draft without requesting a case change. Escape."
        )
        self._apply_button.clicked.connect(self._request_apply)
        self._cancel_button.clicked.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._scroll, 1)
        layout.addWidget(self._buttons)

        self._connect_property_tracking()
        self._build_shortcuts()
        self._set_tab_order()
        self._refresh_from_state()
        QTimer.singleShot(0, self._set_initial_focus)

    @property
    def state(self) -> CoreOverlayToolsState:
        """Return the exact current in-memory draft."""

        return self._state

    @property
    def apply_pending(self) -> bool:
        """Return whether an emitted apply request still needs a terminal result."""

        return self._apply_pending

    @property
    def commit_started(self) -> bool:
        """Return whether this draft has ever crossed the persistence boundary."""

        return self._commit_started

    def _build_canvas_group(
        self,
        transform: PageCoordinateTransform,
        preview: QPixmap | None,
        display_size: RenderedImageSize | None,
        pointer_mapping_enabled: bool,
    ) -> QGroupBox:
        group = QGroupBox("Full-page overlay view")
        group.setObjectName("overlayEditorCanvasGroup")
        group.setAccessibleName("Full-page overlay view")
        group.setAccessibleDescription(
            "A visual page projection with patterned guides and selection outlines. The native "
            "visual-marks list and exact controls provide every editing action without a pointer. "
            f"The active crop starts at left {_percent_text(transform.manifest_crop.x)} and top "
            f"{_percent_text(transform.manifest_crop.y)}, with width "
            f"{_percent_text(transform.manifest_crop.width)} and height "
            f"{_percent_text(transform.manifest_crop.height)}."
        )
        layout = QVBoxLayout(group)

        if not pointer_mapping_enabled:
            notice = QLabel(
                "Deskewed page: the pixel preview is replaced by a schematic. Pointer editing "
                "is off; the native list, exact fields, and keyboard controls remain complete."
            )
            notice.setObjectName("overlayEditorDeskewNotice")
            notice.setAccessibleName("Deskewed overlay preview notice")
            notice.setAccessibleDescription(notice.text())
            notice.setTextFormat(Qt.TextFormat.PlainText)
            notice.setWordWrap(True)
            notice.setFrameShape(QFrame.Shape.StyledPanel)
            layout.addWidget(notice)

        self._canvas = OverlayEditorCanvas(
            transform=transform,
            preview=preview,
            display_size=display_size,
            pointer_mapping_enabled=pointer_mapping_enabled,
        )
        self._canvas.selection_requested.connect(self._canvas_selection_requested)
        self._canvas.box_change_requested.connect(self._canvas_box_change_requested)
        self._canvas.interaction_message.connect(self._canvas_interaction_message)

        self._canvas_scroll = QScrollArea()
        self._canvas_scroll.setObjectName("overlayEditorCanvasScroll")
        self._canvas_scroll.setAccessibleName("Scrollable full-page overlay preview")
        self._canvas_scroll.setAccessibleDescription(
            "Use the named pan buttons or scroll bars to inspect the page. Use the list and "
            "property controls for a complete keyboard editing path."
        )
        self._canvas_scroll.setWidgetResizable(False)
        self._canvas_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._canvas_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._canvas_scroll.setMinimumHeight(340)
        self._canvas_scroll.setWidget(self._canvas)
        layout.addWidget(self._canvas_scroll, 1)

        display_controls = QGridLayout()
        zoom_label = QLabel("Zoom")
        self._zoom_combo = QComboBox()
        self._zoom_combo.setObjectName("overlayEditorZoomCombo")
        self._zoom_combo.setAccessibleName("Page preview zoom percentage")
        self._zoom_combo.setAccessibleDescription(
            "Change only the visual preview scale. Saved overlay coordinates never change."
        )
        for percent in _ZOOM_CHOICES:
            self._zoom_combo.addItem(f"{percent} percent", percent)
        self._zoom_combo.setCurrentIndex(self._zoom_combo.findData(DEFAULT_EDITOR_ZOOM_PERCENT))
        self._zoom_combo.currentIndexChanged.connect(self._zoom_changed)
        zoom_label.setBuddy(self._zoom_combo)
        self._zoom_out_button = self._display_button(
            "Zoom out", "overlayEditorZoomOutButton", "Reduce preview zoom. Ctrl+Minus."
        )
        self._zoom_in_button = self._display_button(
            "Zoom in", "overlayEditorZoomInButton", "Increase preview zoom. Ctrl+Plus."
        )
        self._zoom_100_button = self._display_button(
            "100%", "overlayEditorZoom100Button", "Set preview zoom to 100 percent. Ctrl+0."
        )
        self._fit_page_button = self._display_button(
            "Fit page", "overlayEditorFitPageButton", "Fit the complete page in the preview."
        )
        self._zoom_out_button.clicked.connect(self._zoom_out)
        self._zoom_in_button.clicked.connect(self._zoom_in)
        self._zoom_100_button.clicked.connect(lambda: self._set_zoom(100))
        self._fit_page_button.clicked.connect(self._fit_page)
        display_controls.addWidget(zoom_label, 0, 0)
        display_controls.addWidget(self._zoom_combo, 0, 1)
        display_controls.addWidget(self._zoom_out_button, 0, 2)
        display_controls.addWidget(self._zoom_in_button, 0, 3)
        display_controls.addWidget(self._zoom_100_button, 0, 4)
        display_controls.addWidget(self._fit_page_button, 0, 5)

        self._show_guides = QCheckBox("Show dashed guides")
        self._show_guides.setObjectName("overlayEditorShowGuidesCheck")
        self._show_guides.setAccessibleName("Show dashed alignment guides")
        self._show_guides.setAccessibleDescription(
            "Show a patterned grid and text label; guide state is never conveyed by color alone."
        )
        self._show_guides.setChecked(True)
        self._snap_to_guides = QCheckBox("Snap pointer moves to guides")
        self._snap_to_guides.setObjectName("overlayEditorSnapCheck")
        self._snap_to_guides.setAccessibleName("Snap pointer moves to guides")
        self._snap_to_guides.setAccessibleDescription(
            "When checked, pointer movement aligns the selected mark to the chosen normalized grid."
        )
        guide_label = QLabel("Guide spacing")
        self._guide_spacing = QComboBox()
        self._guide_spacing.setObjectName("overlayEditorGuideSpacingCombo")
        self._guide_spacing.setAccessibleName("Normalized guide spacing")
        self._guide_spacing.setAccessibleDescription(
            "Choose guide spacing in full-page percentages, independent of preview zoom."
        )
        for step in GUIDE_STEPS_MILLIONTHS:
            self._guide_spacing.addItem(_GUIDE_LABELS[step], step)
        self._guide_spacing.setCurrentIndex(self._guide_spacing.findData(50_000))
        guide_label.setBuddy(self._guide_spacing)
        self._snap_selected_button = self._display_button(
            "Snap selected now",
            "overlayEditorSnapSelectedButton",
            "Move the selected mark origin to the nearest normalized guides.",
        )
        self._snap_selected_button.clicked.connect(self._snap_selected)
        self._show_guides.toggled.connect(self._guide_options_changed)
        self._snap_to_guides.toggled.connect(self._guide_options_changed)
        self._guide_spacing.currentIndexChanged.connect(self._guide_options_changed)
        display_controls.addWidget(self._show_guides, 1, 0, 1, 2)
        display_controls.addWidget(self._snap_to_guides, 1, 2, 1, 2)
        display_controls.addWidget(guide_label, 1, 4)
        display_controls.addWidget(self._guide_spacing, 1, 5)
        display_controls.addWidget(self._snap_selected_button, 1, 6)

        self._pan_left_button = self._display_button(
            "Pan left", "overlayEditorPanLeftButton", "Move the preview viewport left."
        )
        self._pan_right_button = self._display_button(
            "Pan right", "overlayEditorPanRightButton", "Move the preview viewport right."
        )
        self._pan_up_button = self._display_button(
            "Pan up", "overlayEditorPanUpButton", "Move the preview viewport up."
        )
        self._pan_down_button = self._display_button(
            "Pan down", "overlayEditorPanDownButton", "Move the preview viewport down."
        )
        self._pan_center_button = self._display_button(
            "Center", "overlayEditorPanCenterButton", "Center the preview viewport."
        )
        self._pan_left_button.clicked.connect(lambda: self._pan(horizontal=-1, vertical=0))
        self._pan_right_button.clicked.connect(lambda: self._pan(horizontal=1, vertical=0))
        self._pan_up_button.clicked.connect(lambda: self._pan(horizontal=0, vertical=-1))
        self._pan_down_button.clicked.connect(lambda: self._pan(horizontal=0, vertical=1))
        self._pan_center_button.clicked.connect(self._center_preview)
        for bar in (
            self._canvas_scroll.horizontalScrollBar(),
            self._canvas_scroll.verticalScrollBar(),
        ):
            bar.valueChanged.connect(self._update_pan_actions)
            bar.rangeChanged.connect(self._update_pan_actions)
        for column, button in enumerate(
            (
                self._pan_left_button,
                self._pan_right_button,
                self._pan_up_button,
                self._pan_down_button,
                self._pan_center_button,
            )
        ):
            display_controls.addWidget(button, 2, column)

        self._snap_feedback = QLabel("Snap feedback: no snap action yet.")
        self._snap_feedback.setObjectName("overlayEditorSnapFeedback")
        self._snap_feedback.setAccessibleName("Overlay snap feedback")
        self._snap_feedback.setAccessibleDescription(self._snap_feedback.text())
        self._snap_feedback.setTextFormat(Qt.TextFormat.PlainText)
        self._snap_feedback.setWordWrap(True)
        display_controls.addWidget(self._snap_feedback, 2, 5, 1, 2)
        layout.addLayout(display_controls)
        self._guide_options_changed()
        self._update_pan_actions()
        return group

    def _display_button(self, text: str, object_name: str, description: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(object_name)
        button.setAccessibleName(text)
        button.setAccessibleDescription(description)
        return button

    def set_apply_succeeded(self) -> bool:
        """Accept the dialog after the caller confirms the pending draft was applied."""

        if not self._apply_pending:
            return False
        self._apply_pending = False
        self._set_status("Visual overlay changes applied.", announce=False)
        super().accept()
        return True

    def latest_state_for_shutdown(self) -> CoreOverlayToolsState | None:
        """Commit representable pending fields before a shutdown handoff."""

        if self._apply_pending or not self._commit_properties_if_needed():
            return None
        return self._state

    def set_apply_failed(self, code: str, message: str) -> bool:
        """Restore editing after one sanitized pending apply failure."""

        if not self._apply_pending:
            return False
        selected_code, selected_message = safe_core_overlay_failure(code, message)
        self._apply_pending = False
        self._set_editor_enabled(True)
        self._apply_button.setAccessibleDescription(
            "Retry saving the retained visual overlay draft."
        )
        self._set_status(
            f"{selected_code}: {selected_message} Changes are retained for retry.",
            announce=True,
            assertive=True,
        )
        self._status.setFocus(Qt.FocusReason.OtherFocusReason)
        self._scroll.ensureWidgetVisible(self._status, 8, 8)
        return True

    def reject(self) -> None:
        """Keep the dialog open after its draft crosses the persistence boundary."""

        if self._commit_started:
            QApplication.beep()
            self._set_status(
                "These visual overlay changes are retained for retry and cannot be "
                "discarded from this dialog.",
                announce=True,
                assertive=True,
            )
            self._status.setFocus(Qt.FocusReason.OtherFocusReason)
            self._scroll.ensureWidgetVisible(self._status, 8, 8)
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._commit_started:
            event.ignore()
            self.reject()
            return
        super().closeEvent(event)

    def _build_add_group(self) -> QGroupBox:
        group = QGroupBox("Add a visual mark")
        group.setObjectName("coreOverlayAddGroup")
        group.setAccessibleName("Add a visual mark")
        group.setAccessibleDescription(
            "Choose one of the five core form-mark types, then add it to the selected page."
        )
        layout = QHBoxLayout(group)
        label = QLabel("New mark type")
        label.setObjectName("newOverlayTypeLabel")
        self._tool_combo = QComboBox()
        self._tool_combo.setObjectName("newOverlayTypeCombo")
        self._tool_combo.setAccessibleName("New visual form-mark type")
        self._tool_combo.setAccessibleDescription(
            "Choose Text, Date, Checkmark, Cross, or Initials with the keyboard."
        )
        for descriptor in CORE_OVERLAY_TOOL_DESCRIPTORS:
            self._tool_combo.addItem(descriptor.label, descriptor.kind)
        label.setBuddy(self._tool_combo)
        self._add_button = QPushButton("Add selected type")
        self._add_button.setObjectName("addCoreOverlayButton")
        self._add_button.setAccessibleName("Add selected visual form-mark type")
        self._add_button.setAccessibleDescription(
            "Add the selected type at a deterministic default position and select it."
        )
        self._add_button.clicked.connect(self._add_selected_type)
        layout.addWidget(label)
        layout.addWidget(self._tool_combo, 1)
        layout.addWidget(self._add_button)
        return group

    def _build_list_group(self) -> QGroupBox:
        group = QGroupBox("Visual marks on this page")
        group.setObjectName("coreOverlayListGroup")
        group.setAccessibleName("Visual marks on this page")
        group.setAccessibleDescription(_LIST_DESCRIPTION)
        layout = QVBoxLayout(group)
        self._overlay_list = QListWidget()
        self._overlay_list.setObjectName("coreOverlayList")
        self._overlay_list.setAccessibleName("Visual overlays on selected page")
        self._overlay_list.setAccessibleDescription(_LIST_DESCRIPTION)
        self._overlay_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._overlay_list.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self._overlay_list.setDragEnabled(False)
        self._overlay_list.setAcceptDrops(False)
        self._overlay_list.setDropIndicatorShown(False)
        self._overlay_list.setWordWrap(True)
        self._overlay_list.currentRowChanged.connect(self._selected_row_changed)
        layout.addWidget(self._overlay_list)
        return group

    def _build_properties_group(self) -> QGroupBox:
        self._properties_group = QGroupBox("Selected visual-mark properties")
        self._properties_group.setObjectName("coreOverlayPropertiesGroup")
        self._properties_group.setAccessibleName("Selected visual-mark properties")
        self._properties_group.setAccessibleDescription(
            "Edit the selected mark's value, formatting, and exact full-page normalized box."
        )
        layout = QGridLayout(self._properties_group)

        self._selection_capability = QLabel("Select a visual mark to edit its exact geometry.")
        self._selection_capability.setObjectName("overlayEditorSelectionCapability")
        self._selection_capability.setAccessibleName("Selected overlay editing capabilities")
        self._selection_capability.setAccessibleDescription(self._selection_capability.text())
        self._selection_capability.setTextFormat(Qt.TextFormat.PlainText)
        self._selection_capability.setWordWrap(True)
        layout.addWidget(self._selection_capability, 0, 0, 1, 4)

        self._value_label = QLabel("Value")
        self._value_label.setObjectName("coreOverlayValueLabel")
        self._value_edit = QLineEdit()
        self._value_edit.setObjectName("coreOverlayValueEdit")
        # QLineEdit counts UTF-16 code units. One extra full Python code point
        # lets the domain reject 4,097-code-point input instead of Qt silently
        # truncating it to a valid-looking 4,096-code-point value.
        self._value_edit.setMaxLength(_TEXT_INPUT_MAX_UTF16_UNITS)
        self._value_label.setBuddy(self._value_edit)
        layout.addWidget(self._value_label, 1, 0)
        layout.addWidget(self._value_edit, 1, 1, 1, 3)

        self._font_label = QLabel("Font")
        self._font_label.setObjectName("coreOverlayFontLabel")
        self._font_value = QLabel("PaperWorkShell default")
        self._font_value.setObjectName("coreOverlayFontValue")
        self._font_value.setAccessibleName("Visual form-mark font")
        self._font_value.setAccessibleDescription(
            "The approved PaperWorkShell default overlay font."
        )
        layout.addWidget(self._font_label, 2, 0)
        layout.addWidget(self._font_value, 2, 1)

        self._font_size_label = QLabel("Font size")
        self._font_size_label.setObjectName("coreOverlayFontSizeLabel")
        self._font_size = QSpinBox()
        self._font_size.setObjectName("coreOverlayFontSizeSpin")
        self._font_size.setAccessibleName("Visual form-mark font size in points")
        self._font_size.setAccessibleDescription(
            "Choose the selected text, date, or initials size in typographic points."
        )
        self._font_size.setRange(MIN_FONT_SIZE_POINTS, MAX_FONT_SIZE_POINTS)
        self._font_size.setSuffix(" points")
        self._font_size_label.setBuddy(self._font_size)
        layout.addWidget(self._font_size_label, 2, 2)
        layout.addWidget(self._font_size, 2, 3)

        self._color_label = QLabel("Color")
        self._color_label.setObjectName("coreOverlayColorLabel")
        self._color_combo = QComboBox()
        self._color_combo.setObjectName("coreOverlayColorCombo")
        self._color_combo.setAccessibleName("Visual form-mark color")
        self._color_combo.setAccessibleDescription(
            "Choose a named visual color; color is never the only status indicator."
        )
        for name, value in _COLOR_CHOICES:
            self._color_combo.addItem(name, value)
        self._color_label.setBuddy(self._color_combo)
        layout.addWidget(self._color_label, 3, 0)
        layout.addWidget(self._color_combo, 3, 1, 1, 3)

        self._left, self._left_percent = self._coordinate_control(
            "coreOverlayLeftSpin",
            "Left position in normalized page millionths",
            "Distance from the full page's left edge, from 0 through 1,000,000.",
        )
        self._top, self._top_percent = self._coordinate_control(
            "coreOverlayTopSpin",
            "Top position in normalized page millionths",
            "Distance from the full page's top edge, from 0 through 1,000,000.",
        )
        self._width, self._width_percent = self._coordinate_control(
            "coreOverlayWidthSpin",
            "Width in normalized page millionths",
            "Visual-mark width on the full page, from 1 through 1,000,000.",
            positive=True,
        )
        self._height, self._height_percent = self._coordinate_control(
            "coreOverlayHeightSpin",
            "Height in normalized page millionths",
            "Visual-mark height on the full page, from 1 through 1,000,000.",
            positive=True,
        )
        for row, (label_text, control, percent) in enumerate(
            (
                ("Left", self._left, self._left_percent),
                ("Top", self._top, self._top_percent),
                ("Width", self._width, self._width_percent),
                ("Height", self._height, self._height_percent),
            ),
            start=4,
        ):
            label = QLabel(label_text)
            label.setObjectName(f"coreOverlay{label_text}Label")
            label.setBuddy(control)
            layout.addWidget(label, row, 0)
            layout.addWidget(control, row, 1, 1, 2)
            layout.addWidget(percent, row, 3)

        self._apply_properties_button = QPushButton("Apply property changes")
        self._apply_properties_button.setObjectName("applyCoreOverlayPropertiesButton")
        self._apply_properties_button.setAccessibleName("Apply selected visual-mark properties")
        self._apply_properties_button.setAccessibleDescription(
            "Apply the value, formatting, position, and size fields to the selected mark. "
            "Ctrl+Enter."
        )
        self._apply_properties_button.clicked.connect(self._apply_properties)
        layout.addWidget(self._apply_properties_button, 8, 0, 1, 4)
        return self._properties_group

    def _coordinate_control(
        self,
        object_name: str,
        accessible_name: str,
        accessible_description: str,
        *,
        positive: bool = False,
    ) -> tuple[QSpinBox, QLabel]:
        control = QSpinBox()
        control.setObjectName(object_name)
        control.setAccessibleName(accessible_name)
        control.setAccessibleDescription(accessible_description)
        control.setRange(1 if positive else 0, NORMALIZED_PAGE_MILLIONTHS)
        control.setSingleStep(1_000)
        control.setGroupSeparatorShown(True)
        percent = QLabel("0.0000 percent")
        percent.setObjectName(f"{object_name}Percent")
        percent.setAccessibleName(f"{accessible_name} percentage")
        percent.setTextFormat(Qt.TextFormat.PlainText)
        percent.setWordWrap(True)
        control.valueChanged.connect(
            lambda value, target=percent, source=control, base=accessible_description: (
                self._update_coordinate_accessibility(source, target, base, value)
            )
        )
        return control, percent

    def _update_coordinate_accessibility(
        self,
        control: QSpinBox,
        percent: QLabel,
        base_description: str,
        value: int,
    ) -> None:
        value_text = _percent_text(value)
        percent.setText(value_text)
        control.setAccessibleDescription(f"{base_description} Current value: {value_text}.")

    def _build_edit_actions(self) -> QVBoxLayout:
        layout = QVBoxLayout()
        geometry = QHBoxLayout()
        self._fine_step = QCheckBox("Fine step (0.1%)")
        self._fine_step.setObjectName("overlayEditorFineStepCheck")
        self._fine_step.setAccessibleName("Use fine movement and resize step")
        self._fine_step.setAccessibleDescription(
            "When checked, named movement and resize buttons use 0.1 percent instead of 1 percent."
        )
        geometry.addWidget(self._fine_step)
        self._move_left_button = self._geometry_button(
            "Move left", "overlayEditorMoveLeftButton", KeyboardDirection.LEFT, resize=False
        )
        self._move_right_button = self._geometry_button(
            "Move right", "overlayEditorMoveRightButton", KeyboardDirection.RIGHT, resize=False
        )
        self._move_up_button = self._geometry_button(
            "Move up", "overlayEditorMoveUpButton", KeyboardDirection.UP, resize=False
        )
        self._move_down_button = self._geometry_button(
            "Move down", "overlayEditorMoveDownButton", KeyboardDirection.DOWN, resize=False
        )
        self._shrink_width_button = self._geometry_button(
            "Shrink width",
            "overlayEditorShrinkWidthButton",
            KeyboardDirection.LEFT,
            resize=True,
        )
        self._grow_width_button = self._geometry_button(
            "Grow width",
            "overlayEditorGrowWidthButton",
            KeyboardDirection.RIGHT,
            resize=True,
        )
        self._shrink_height_button = self._geometry_button(
            "Shrink height",
            "overlayEditorShrinkHeightButton",
            KeyboardDirection.UP,
            resize=True,
        )
        self._grow_height_button = self._geometry_button(
            "Grow height",
            "overlayEditorGrowHeightButton",
            KeyboardDirection.DOWN,
            resize=True,
        )
        for button in self._geometry_buttons():
            geometry.addWidget(button)
        geometry.addStretch(1)
        layout.addLayout(geometry)

        history = QHBoxLayout()
        self._remove_button = QPushButton("Remove selected")
        self._remove_button.setObjectName("removeCoreOverlayButton")
        self._remove_button.setAccessibleName("Remove selected core visual mark")
        self._remove_button.setAccessibleDescription("Remove the selected mark. Delete.")
        self._undo_button = QPushButton("Undo")
        self._undo_button.setObjectName("undoCoreOverlayButton")
        self._undo_button.setAccessibleName("Undo visual overlay change")
        self._undo_button.setAccessibleDescription(
            "Undo the last local visual overlay change. Ctrl+Z."
        )
        self._redo_button = QPushButton("Redo")
        self._redo_button.setObjectName("redoCoreOverlayButton")
        self._redo_button.setAccessibleName("Redo visual overlay change")
        self._redo_button.setAccessibleDescription(
            "Redo the next local visual overlay change. Ctrl+Y."
        )
        self._remove_button.clicked.connect(self._remove_selected)
        self._undo_button.clicked.connect(self._undo)
        self._redo_button.clicked.connect(self._redo)
        history.addWidget(self._remove_button)
        history.addWidget(self._undo_button)
        history.addWidget(self._redo_button)
        history.addStretch(1)
        layout.addLayout(history)
        return layout

    def _geometry_button(
        self,
        text: str,
        object_name: str,
        direction: KeyboardDirection,
        *,
        resize: bool,
    ) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(object_name)
        button.setAccessibleName(text + " selected visual mark")
        button.setAccessibleDescription(
            "Resize the selected mark using the current named step."
            if resize
            else "Move the selected mark using the current named step."
        )
        if resize:
            button.clicked.connect(lambda: self._resize(direction))
        else:
            button.clicked.connect(lambda: self._move(direction))
        return button

    def _geometry_buttons(self) -> tuple[QPushButton, ...]:
        return (
            self._move_left_button,
            self._move_right_button,
            self._move_up_button,
            self._move_down_button,
            self._shrink_width_button,
            self._grow_width_button,
            self._shrink_height_button,
            self._grow_height_button,
        )

    def _connect_property_tracking(self) -> None:
        self._value_edit.textEdited.connect(self._mark_value_edited)
        for control in (
            self._font_size,
            self._color_combo,
            self._left,
            self._top,
            self._width,
            self._height,
        ):
            control.currentIndexChanged.connect(self._mark_properties_dirty) if isinstance(
                control, QComboBox
            ) else control.valueChanged.connect(self._mark_properties_dirty)

    def _build_shortcuts(self) -> None:
        bindings: tuple[tuple[str, Callable[[], None]], ...] = (
            ("Delete", self._remove_selected),
            ("Ctrl+Z", self._undo),
            ("Ctrl+Y", self._redo),
            ("Ctrl+Enter", self._apply_properties),
            ("Ctrl+Return", self._apply_properties),
            ("Alt+Left", lambda: self._move(KeyboardDirection.LEFT)),
            ("Alt+Right", lambda: self._move(KeyboardDirection.RIGHT)),
            ("Alt+Up", lambda: self._move(KeyboardDirection.UP)),
            ("Alt+Down", lambda: self._move(KeyboardDirection.DOWN)),
            ("Alt+Shift+Left", lambda: self._resize(KeyboardDirection.LEFT)),
            ("Alt+Shift+Right", lambda: self._resize(KeyboardDirection.RIGHT)),
            ("Alt+Shift+Up", lambda: self._resize(KeyboardDirection.UP)),
            ("Alt+Shift+Down", lambda: self._resize(KeyboardDirection.DOWN)),
            ("Ctrl+Alt+Left", lambda: self._move(KeyboardDirection.LEFT, fine=True)),
            ("Ctrl+Alt+Right", lambda: self._move(KeyboardDirection.RIGHT, fine=True)),
            ("Ctrl+Alt+Up", lambda: self._move(KeyboardDirection.UP, fine=True)),
            ("Ctrl+Alt+Down", lambda: self._move(KeyboardDirection.DOWN, fine=True)),
            (
                "Ctrl+Alt+Shift+Left",
                lambda: self._resize(KeyboardDirection.LEFT, fine=True),
            ),
            (
                "Ctrl+Alt+Shift+Right",
                lambda: self._resize(KeyboardDirection.RIGHT, fine=True),
            ),
            ("Ctrl+Alt+Shift+Up", lambda: self._resize(KeyboardDirection.UP, fine=True)),
            ("Ctrl+Alt+Shift+Down", lambda: self._resize(KeyboardDirection.DOWN, fine=True)),
            ("Ctrl+-", self._zoom_out),
            ("Ctrl+=", self._zoom_in),
            ("Ctrl+0", lambda: self._set_zoom(100)),
            ("Ctrl+G", self._snap_selected),
        )
        for sequence, callback in bindings:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)

    def _set_tab_order(self) -> None:
        chain: tuple[QWidget, ...] = (
            self._zoom_combo,
            self._zoom_out_button,
            self._zoom_in_button,
            self._zoom_100_button,
            self._fit_page_button,
            self._show_guides,
            self._snap_to_guides,
            self._guide_spacing,
            self._snap_selected_button,
            self._pan_left_button,
            self._pan_right_button,
            self._pan_up_button,
            self._pan_down_button,
            self._pan_center_button,
            self._tool_combo,
            self._add_button,
            self._overlay_list,
            self._value_edit,
            self._font_size,
            self._color_combo,
            self._left,
            self._top,
            self._width,
            self._height,
            self._apply_properties_button,
            self._fine_step,
            *self._geometry_buttons(),
            self._remove_button,
            self._undo_button,
            self._redo_button,
            self._apply_button,
            self._cancel_button,
            self._status,
        )
        self._tab_chain = chain
        for current, following in pairwise(chain):
            QWidget.setTabOrder(current, following)
        QWidget.setTabOrder(self._status, self._zoom_combo)

    def focusNextPrevChild(self, forward: bool) -> bool:  # noqa: FBT001, N802
        """Keep one closed logical Tab cycle across the nested scroll areas."""

        chain = tuple(
            widget
            for widget in getattr(self, "_tab_chain", ())
            if widget.isVisible()
            and widget.isEnabled()
            and widget.focusPolicy() is not Qt.FocusPolicy.NoFocus
        )
        if not chain:
            return super().focusNextPrevChild(forward)
        focused = QApplication.focusWidget()
        current_index = next(
            (
                index
                for index, widget in enumerate(chain)
                if focused is widget or (focused is not None and widget.isAncestorOf(focused))
            ),
            None,
        )
        if current_index is None:
            return super().focusNextPrevChild(forward)
        offset = 1 if forward else -1
        target = chain[(current_index + offset) % len(chain)]
        target.setFocus(
            Qt.FocusReason.TabFocusReason if forward else Qt.FocusReason.BacktabFocusReason
        )
        if self._content.isAncestorOf(target):
            self._scroll.ensureWidgetVisible(target, 8, 8)
        return True

    def _mark_properties_dirty(self, *_args: object) -> None:
        if not self._loading and self._state.selected_record is not None:
            self._properties_dirty = True

    def _mark_value_edited(self, *_args: object) -> None:
        self._value_edited = True
        self._mark_properties_dirty()

    def _selected_row_changed(self, row: int) -> None:
        if self._loading or self._apply_pending or row < 0:
            return
        item = self._overlay_list.item(row)
        overlay_id = item.data(Qt.ItemDataRole.UserRole)
        if type(overlay_id) is not UUID or overlay_id == self._state.selected_overlay_id:
            return
        if self._properties_dirty and not self._commit_properties(announce=False):
            self._restore_list_selection()
            return
        self._publish_state(self._state.select(overlay_id))

    def _add_selected_type(self) -> None:
        if self._apply_pending or not self._commit_properties_if_needed():
            return
        raw_kind = self._tool_combo.currentData()
        try:
            kind = OverlayKind(raw_kind) if type(raw_kind) is str else None
        except ValueError:
            kind = None
        if kind is None or kind not in _TEXTUAL_KINDS | {
            OverlayKind.CHECKMARK,
            OverlayKind.CROSS,
        }:
            self._show_unexpected_failure()
            return
        self._transition(lambda state: state.create(kind))
        record = self._state.selected_record
        if record is not None and record.kind in _TEXTUAL_KINDS:
            self._value_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            self._value_edit.selectAll()
        else:
            self._overlay_list.setFocus(Qt.FocusReason.OtherFocusReason)

    def _apply_properties(self) -> None:
        if self._apply_pending:
            return
        self._commit_properties(announce=True)

    def _commit_properties_if_needed(self) -> bool:
        return not self._properties_dirty or self._commit_properties(announce=False)

    def _commit_properties(self, *, announce: bool) -> bool:
        record = self._state.selected_record
        if record is None:
            self._properties_dirty = False
            return True
        if (
            type(record.payload) is TextualOverlayPayload
            and not self._loaded_value_preserved
            and not self._value_edited
        ):
            self._set_status(
                _UNREPRESENTABLE_SAVED_VALUE,
                announce=True,
                assertive=True,
            )
            self._status.setFocus(Qt.FocusReason.OtherFocusReason)
            return False
        try:
            box = NormalizedPageBox(
                self._left.value(),
                self._top.value(),
                self._width.value(),
                self._height.value(),
            )
            state = self._state.replace_selected_box(box)
            if type(record.payload) is TextualOverlayPayload:
                color = self._color_combo.currentData()
                if type(color) is not str:
                    raise ValueError
                state = state.edit_selected(
                    value=self._value_edit.text(),
                    font_size_points=self._font_size.value(),
                    color_value=color,
                )
            elif type(record.payload) is MarkOverlayPayload:
                color = self._color_combo.currentData()
                if type(color) is not str:
                    raise ValueError
                state = state.edit_selected(color_value=color)
        except (OverlayError, PageCoordinateError) as error:
            self._set_status(error.safe_message, announce=True, assertive=True)
            self._status.setFocus(Qt.FocusReason.OtherFocusReason)
            return False
        except BaseException:
            self._show_unexpected_failure()
            return False
        self._properties_dirty = False
        message = (
            state.announcement
            if state is not self._state
            else f"{overlay_editor_kind_accessible_name(record.kind)} properties unchanged."
        )
        self._state = state
        self._refresh_from_state()
        self.draft_changed.emit(state)
        self._set_status(message, announce=announce)
        return True

    def _remove_selected(self) -> None:
        if self._apply_pending:
            return
        record = self._state.selected_record
        if record is not None and record.kind is OverlayKind.SIGNATURE_IMAGE:
            self._set_status(
                "Close this editor and use Remove signature image so case-asset cleanup stays explicit.",
                announce=True,
                assertive=True,
            )
            return
        if record is not None and record.kind in {OverlayKind.HIGHLIGHT, OverlayKind.SHAPE}:
            self._set_status(
                "This ordinary visual-mark type is geometry-only in this editor and cannot be removed here.",
                announce=True,
                assertive=True,
            )
            return
        self._properties_dirty = False
        prior_row = self._overlay_list.currentRow()
        self._transition(lambda state: state.delete_selected())
        remaining = self._state.editor_records
        if remaining:
            target = remaining[min(max(0, prior_row), len(remaining) - 1)]
            self._publish_state(self._state.select(target.overlay_id))
            self._overlay_list.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        self._tool_combo.setFocus(Qt.FocusReason.OtherFocusReason)

    def _undo(self) -> None:
        if self._apply_pending:
            return
        focus = QApplication.focusWidget()
        if focus is self._value_edit and self._value_edit.isUndoAvailable():
            self._value_edit.undo()
            return
        if not self._commit_properties_if_needed():
            return
        self._transition(lambda state: state.undo())

    def _redo(self) -> None:
        if self._apply_pending:
            return
        focus = QApplication.focusWidget()
        if focus is self._value_edit and self._value_edit.isRedoAvailable():
            self._value_edit.redo()
            return
        if not self._commit_properties_if_needed():
            return
        self._transition(lambda state: state.redo())

    def _move(self, direction: KeyboardDirection, *, fine: bool | None = None) -> None:
        if self._apply_pending or not self._commit_properties_if_needed():
            return
        selected_fine = self._fine_step.isChecked() if fine is None else fine
        self._transition(lambda state: state.move_selected(direction, fine=selected_fine))

    def _resize(self, direction: KeyboardDirection, *, fine: bool | None = None) -> None:
        if self._apply_pending or not self._commit_properties_if_needed():
            return
        selected_fine = self._fine_step.isChecked() if fine is None else fine
        self._transition(lambda state: state.resize_selected(direction, fine=selected_fine))

    def _canvas_selection_requested(self, overlay_id: object) -> None:
        if (
            self._apply_pending
            or type(overlay_id) is not UUID
            or not self._commit_properties_if_needed()
        ):
            return
        self._transition(lambda state: state.select(overlay_id))

    def _canvas_box_change_requested(
        self,
        overlay_id: object,
        box: object,
        snapped: bool,
    ) -> None:
        if (
            self._apply_pending
            or type(overlay_id) is not UUID
            or type(box) is not NormalizedPageBox
            or type(snapped) is not bool
            or self._state.selected_overlay_id != overlay_id
            or not self._commit_properties_if_needed()
        ):
            return
        prior = self._state
        self._transition(lambda state: state.replace_selected_box(box))
        if self._state is prior:
            return
        if snapped:
            spacing = self._current_guide_step()
            message = (
                f"Snap feedback: pointer move aligned the selected mark to the nearest "
                f"{_GUIDE_LABELS[spacing]} dashed guides."
            )
            self._snap_feedback.setText(message)
            self._snap_feedback.setAccessibleDescription(message)
            self._set_status(message, announce=True)

    def _canvas_interaction_message(self, message: str, assertive: bool) -> None:
        if (
            type(message) is not str
            or message not in _SAFE_CANVAS_MESSAGES
            or type(assertive) is not bool
        ):
            self._show_unexpected_failure()
            return
        self._set_status(message, announce=True, assertive=assertive)

    def _snap_selected(self) -> None:
        if self._apply_pending or not self._commit_properties_if_needed():
            return
        step = self._current_guide_step()
        prior = self._state
        self._transition(lambda state: state.snap_selected(step), announce=False)
        result = "already aligned with" if self._state is prior else "aligned to"
        message = (
            f"Snap feedback: selected mark {result} the nearest {_GUIDE_LABELS[step]} "
            "dashed guides."
        )
        self._snap_feedback.setText(message)
        self._snap_feedback.setAccessibleDescription(message)
        self._set_status(message, announce=True)

    def _guide_options_changed(self, *_args: object) -> None:
        step = self._current_guide_step()
        self._canvas.set_guide_options(
            visible=self._show_guides.isChecked(),
            snap=self._snap_to_guides.isChecked(),
            step_millionths=step,
        )
        visible = "shown as dashed lines" if self._show_guides.isChecked() else "hidden"
        snapping = "on" if self._snap_to_guides.isChecked() else "off"
        description = (
            f"Guides are {visible}; pointer snap is {snapping}; spacing is {_GUIDE_LABELS[step]}."
        )
        self._show_guides.setAccessibleDescription(description)
        self._snap_to_guides.setAccessibleDescription(description)

    def _current_guide_step(self) -> int:
        value = self._guide_spacing.currentData()
        return value if type(value) is int and value in GUIDE_STEPS_MILLIONTHS else 50_000

    def _zoom_changed(self, _index: int) -> None:
        if self._apply_pending:
            return
        value = self._zoom_combo.currentData()
        if type(value) is int and value in _ZOOM_CHOICES:
            self._apply_zoom(value, announce=True)

    def _set_zoom(self, percent: int, *, announce: bool = True) -> None:
        if self._apply_pending or percent not in _ZOOM_CHOICES:
            return
        index = self._zoom_combo.findData(percent)
        if index < 0:
            return
        if self._zoom_combo.currentIndex() == index:
            self._apply_zoom(percent, announce=announce)
            return
        with QSignalBlocker(self._zoom_combo):
            self._zoom_combo.setCurrentIndex(index)
        self._apply_zoom(percent, announce=announce)

    def _apply_zoom(self, percent: int, *, announce: bool) -> None:
        if (
            self._apply_pending
            or type(percent) is not int
            or percent not in _ZOOM_CHOICES
            or not MIN_EDITOR_ZOOM_PERCENT <= percent <= MAX_EDITOR_ZOOM_PERCENT
        ):
            return
        self._canvas.set_zoom_percent(percent)
        description = f"Page preview zoom is {percent} percent. Saved overlay positions and sizes are unchanged."
        self._zoom_combo.setAccessibleDescription(description)
        self._zoom_out_button.setEnabled(percent > min(_ZOOM_CHOICES))
        self._zoom_in_button.setEnabled(percent < max(_ZOOM_CHOICES))
        if announce:
            self._set_status(description, announce=True)

    def _zoom_out(self) -> None:
        current = self._canvas.zoom_percent
        target = max((value for value in _ZOOM_CHOICES if value < current), default=current)
        self._set_zoom(target)

    def _zoom_in(self) -> None:
        current = self._canvas.zoom_percent
        target = min((value for value in _ZOOM_CHOICES if value > current), default=current)
        self._set_zoom(target)

    def _fit_page(self) -> None:
        if self._apply_pending:
            return
        viewport = self._canvas_scroll.viewport().size()
        base = self._canvas.base_canvas_size
        if viewport.width() <= 0 or viewport.height() <= 0:
            return
        target = int(
            min(
                viewport.width() * 100 / max(1, base.width()),
                viewport.height() * 100 / max(1, base.height()),
            )
        )
        choice = max((value for value in _ZOOM_CHOICES if value <= target), default=25)
        self._set_zoom(choice, announce=False)
        for bar in (
            self._canvas_scroll.horizontalScrollBar(),
            self._canvas_scroll.verticalScrollBar(),
        ):
            bar.setValue((bar.minimum() + bar.maximum()) // 2)
        self._set_status(
            f"Page preview fit at {choice} percent and centered. Saved overlay positions and "
            "sizes were not changed.",
            announce=True,
        )

    def _pan(self, *, horizontal: int, vertical: int) -> None:
        if self._apply_pending or horizontal not in {-1, 0, 1} or vertical not in {-1, 0, 1}:
            return
        horizontal_bar = self._canvas_scroll.horizontalScrollBar()
        vertical_bar = self._canvas_scroll.verticalScrollBar()
        if horizontal:
            horizontal_bar.setValue(
                horizontal_bar.value() + horizontal * max(1, horizontal_bar.pageStep() // 2)
            )
        if vertical:
            vertical_bar.setValue(
                vertical_bar.value() + vertical * max(1, vertical_bar.pageStep() // 2)
            )
        direction = (
            "left"
            if horizontal < 0
            else "right"
            if horizontal > 0
            else "up"
            if vertical < 0
            else "down"
        )
        self._set_status(
            f"Page preview panned {direction}. Saved overlay positions were not changed.",
            announce=True,
        )

    def _center_preview(self) -> None:
        if self._apply_pending:
            return
        for bar in (
            self._canvas_scroll.horizontalScrollBar(),
            self._canvas_scroll.verticalScrollBar(),
        ):
            bar.setValue((bar.minimum() + bar.maximum()) // 2)
        self._set_status(
            "Page preview centered. Saved overlay positions were not changed.",
            announce=True,
        )

    def _update_pan_actions(self, *_args: object) -> None:
        if self._apply_pending:
            for button in (
                self._pan_left_button,
                self._pan_right_button,
                self._pan_up_button,
                self._pan_down_button,
                self._pan_center_button,
            ):
                button.setEnabled(False)
            return
        horizontal = self._canvas_scroll.horizontalScrollBar()
        vertical = self._canvas_scroll.verticalScrollBar()
        self._pan_left_button.setEnabled(horizontal.value() > horizontal.minimum())
        self._pan_right_button.setEnabled(horizontal.value() < horizontal.maximum())
        self._pan_up_button.setEnabled(vertical.value() > vertical.minimum())
        self._pan_down_button.setEnabled(vertical.value() < vertical.maximum())
        self._pan_center_button.setEnabled(horizontal.maximum() > 0 or vertical.maximum() > 0)

    def _set_initial_focus(self) -> None:
        if self._state.selected_record is not None:
            self._overlay_list.setFocus(Qt.FocusReason.OtherFocusReason)
            self._scroll.ensureWidgetVisible(self._overlay_list, 8, 8)
            return
        self._tool_combo.setFocus(Qt.FocusReason.OtherFocusReason)
        self._scroll.ensureWidgetVisible(self._tool_combo, 8, 8)

    def _transition(
        self,
        operation: Callable[[CoreOverlayToolsState], CoreOverlayToolsState],
        *,
        announce: bool = True,
    ) -> None:
        try:
            state = operation(self._state)
        except OverlayError as error:
            QApplication.beep()
            self._set_status(error.safe_message, announce=True, assertive=True)
            return
        except BaseException:
            self._show_unexpected_failure()
            return
        self._publish_state(state, announce=announce)

    def _publish_state(self, state: CoreOverlayToolsState, *, announce: bool = True) -> None:
        self._state = state
        self._properties_dirty = False
        self._refresh_from_state()
        self.draft_changed.emit(state)
        if announce and self._loaded_value_preserved:
            self._set_status(state.announcement, announce=True)

    def _refresh_from_state(self) -> None:
        self._loading = True
        try:
            with QSignalBlocker(self._overlay_list):
                records = self._state.editor_records
                existing_ids = tuple(
                    self._overlay_list.item(row).data(Qt.ItemDataRole.UserRole)
                    for row in range(self._overlay_list.count())
                )
                target_ids = tuple(record.overlay_id for record in records)
                if existing_ids != target_ids:
                    self._overlay_list.clear()
                    for record in records:
                        item = QListWidgetItem()
                        item.setData(Qt.ItemDataRole.UserRole, record.overlay_id)
                        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                        self._overlay_list.addItem(item)
                for position, record in enumerate(records, start=1):
                    summary = (
                        f"{overlay_editor_record_summary(record, position, len(records))} "
                        f"{self._visibility_description(record)}"
                    )
                    item = self._overlay_list.item(position - 1)
                    item.setText(summary)
                    item.setData(Qt.ItemDataRole.AccessibleTextRole, summary)
                    item.setData(
                        Qt.ItemDataRole.AccessibleDescriptionRole,
                        self._record_action_description(record),
                    )
            self._restore_list_selection()
            self._load_selected_properties()
            self._canvas.set_records(
                self._state.editor_records,
                self._state.selected_overlay_id,
            )
            selected = self._state.selected_record is not None
            selected_core = (
                self._state.selected_record is not None
                and self._state.selected_record.kind
                in {
                    OverlayKind.TEXT,
                    OverlayKind.DATE,
                    OverlayKind.CHECKMARK,
                    OverlayKind.CROSS,
                    OverlayKind.INITIALS,
                }
            )
            self._properties_group.setEnabled(selected)
            self._remove_button.setEnabled(selected_core)
            self._apply_properties_button.setEnabled(selected)
            self._snap_selected_button.setEnabled(selected)
            for button in self._geometry_buttons():
                button.setEnabled(selected)
            self._undo_button.setEnabled(self._state.can_undo)
            self._redo_button.setEnabled(self._state.can_redo)
            self._apply_button.setEnabled(not self._apply_pending)
            self._cancel_button.setEnabled(not self._apply_pending and not self._commit_started)
            self._properties_dirty = False
        finally:
            self._loading = False
        if not self._loaded_value_preserved:
            self._set_status(
                _UNREPRESENTABLE_SAVED_VALUE,
                announce=True,
                assertive=True,
            )

    def _restore_list_selection(self) -> None:
        selected_id = self._state.selected_overlay_id
        selected_row = -1
        for row in range(self._overlay_list.count()):
            if self._overlay_list.item(row).data(Qt.ItemDataRole.UserRole) == selected_id:
                selected_row = row
                break
        with QSignalBlocker(self._overlay_list):
            self._overlay_list.setCurrentRow(selected_row)

    def _load_selected_properties(self) -> None:
        record = self._state.selected_record
        controls: tuple[QWidget, ...] = (
            self._value_edit,
            self._font_size,
            self._color_combo,
            self._left,
            self._top,
            self._width,
            self._height,
        )
        blockers = tuple(QSignalBlocker(control) for control in controls)
        try:
            if record is None:
                self._loaded_value_preserved = True
                self._value_edited = False
                self._value_edit.clear()
                self._font_size.setValue(MIN_FONT_SIZE_POINTS)
                self._set_color("#000000ff")
                for control in (self._left, self._top, self._width, self._height):
                    control.setValue(control.minimum())
                for control, percent in (
                    (self._left, self._left_percent),
                    (self._top, self._top_percent),
                    (self._width, self._width_percent),
                    (self._height, self._height_percent),
                ):
                    base = control.accessibleDescription().split(" Current value:", maxsplit=1)[0]
                    self._update_coordinate_accessibility(
                        control,
                        percent,
                        base,
                        control.value(),
                    )
                self._value_label.setText("Value")
                self._value_edit.setAccessibleName("Visual form-mark value")
                self._value_edit.setAccessibleDescription(
                    "Select a text, date, or initials visual mark to edit its value."
                )
                self._set_text_controls_visible(False)
                self._set_color_controls_visible(False)
                self._selection_capability.setText(
                    "Select a visual mark to edit its exact position and size."
                )
                self._selection_capability.setAccessibleDescription(
                    self._selection_capability.text()
                )
                self._properties_group.setAccessibleDescription(
                    "Select a visual mark to inspect and edit its available exact properties."
                )
                self._apply_properties_button.setText("Apply property changes")
                self._apply_properties_button.setAccessibleName(
                    "Apply selected visual-mark properties"
                )
                self._apply_properties_button.setAccessibleDescription(
                    "Select a visual mark before applying properties. Ctrl+Enter."
                )
                self._remove_button.setText("Remove selected")
                self._remove_button.setAccessibleName("Remove selected core visual mark")
                self._remove_button.setAccessibleDescription(
                    "Select a removable core visual mark. Delete."
                )
                return
            payload = record.payload
            self._set_text_controls_visible(type(payload) is TextualOverlayPayload)
            self._set_color_controls_visible(
                type(payload) in {TextualOverlayPayload, MarkOverlayPayload}
            )
            if type(payload) is TextualOverlayPayload:
                descriptor = core_overlay_tool_descriptor(record.kind)
                if descriptor.value_label is None or descriptor.value_description is None:
                    raise ValueError("textual descriptor is incomplete")
                self._value_label.setText(descriptor.value_label)
                self._value_edit.setAccessibleName(descriptor.value_label)
                self._value_edit.setAccessibleDescription(descriptor.value_description)
                self._value_edit.setText(payload.value)
                self._loaded_value_preserved = self._value_edit.text() == payload.value
                self._value_edited = False
                self._font_size.setValue(payload.font_size_points)
                color = payload.color.value
            elif type(payload) is MarkOverlayPayload:
                self._loaded_value_preserved = True
                self._value_edited = False
                self._value_edit.clear()
                self._font_size.setValue(MIN_FONT_SIZE_POINTS)
                color = payload.color.value
            else:
                self._loaded_value_preserved = True
                self._value_edited = False
                self._value_edit.clear()
                self._font_size.setValue(MIN_FONT_SIZE_POINTS)
                color = None
            if color is not None:
                self._set_color(color)
            self._left.setValue(record.box.x)
            self._top.setValue(record.box.y)
            self._width.setValue(record.box.width)
            self._height.setValue(record.box.height)
            for control, percent in (
                (self._left, self._left_percent),
                (self._top, self._top_percent),
                (self._width, self._width_percent),
                (self._height, self._height_percent),
            ):
                base = control.accessibleDescription().split(" Current value:", maxsplit=1)[0]
                self._update_coordinate_accessibility(
                    control,
                    percent,
                    base,
                    control.value(),
                )
            name = overlay_editor_record_accessible_name(record)
            visibility = self._visibility_description(record)
            if record.kind is OverlayKind.SIGNATURE_IMAGE:
                capability = (
                    f"{name} selected. Position and size are editable here. To remove it, close "
                    "this editor and use Remove signature image so case-asset cleanup stays explicit. "
                    "This remains a visual image, not a digital or qualified electronic signature."
                )
            elif record.kind in {OverlayKind.HIGHLIGHT, OverlayKind.SHAPE}:
                capability = (
                    f"{name} selected. Position and size are editable here; its visual content "
                    "properties are read-only. This is an ordinary visual mark, not permanent removal."
                )
            elif type(payload) is TextualOverlayPayload:
                capability = f"{name} selected. Value, formatting, position, and size are editable."
            else:
                capability = f"{name} selected. Color, position, and size are editable."
            capability = f"{capability} {visibility}"
            self._selection_capability.setText(capability)
            self._selection_capability.setAccessibleDescription(capability)
            if type(payload) is TextualOverlayPayload:
                self._properties_group.setAccessibleDescription(
                    "Edit the selected mark's value, formatting, and exact full-page normalized box."
                )
                self._apply_properties_button.setText("Apply property changes")
                self._apply_properties_button.setAccessibleName(
                    "Apply selected visual-mark properties"
                )
                self._apply_properties_button.setAccessibleDescription(
                    "Apply the value, formatting, position, and size fields to the selected "
                    "visual mark. Ctrl+Enter."
                )
            elif type(payload) is MarkOverlayPayload:
                self._properties_group.setAccessibleDescription(
                    "Edit the selected mark's color and exact full-page normalized box."
                )
                self._apply_properties_button.setText("Apply property changes")
                self._apply_properties_button.setAccessibleName(
                    "Apply selected visual-mark properties"
                )
                self._apply_properties_button.setAccessibleDescription(
                    "Apply the color, position, and size fields to the selected visual mark. "
                    "Ctrl+Enter."
                )
            else:
                self._properties_group.setAccessibleDescription(
                    "Edit only the selected mark's exact full-page normalized position and size; "
                    "its visual content properties are read-only here."
                )
                self._apply_properties_button.setText("Apply position and size")
                self._apply_properties_button.setAccessibleName(
                    "Apply selected visual-mark position and size"
                )
                self._apply_properties_button.setAccessibleDescription(
                    "Apply only the exact position and size fields; visual content properties "
                    "are read-only here. Ctrl+Enter."
                )
            if record.kind is OverlayKind.SIGNATURE_IMAGE:
                self._remove_button.setText("Use organizer to remove signature image")
                self._remove_button.setAccessibleName(
                    "Signature image removal unavailable in this editor"
                )
                self._remove_button.setAccessibleDescription(
                    "Close this editor and use Remove signature image so case-asset cleanup "
                    "stays explicit."
                )
            elif record.kind in {OverlayKind.HIGHLIGHT, OverlayKind.SHAPE}:
                self._remove_button.setText("Removal unavailable here")
                self._remove_button.setAccessibleName(f"{name} removal unavailable in this editor")
                self._remove_button.setAccessibleDescription(
                    "This ordinary visual-mark type is geometry-only in this editor and cannot "
                    "be removed here."
                )
            else:
                self._remove_button.setText("Remove selected")
                self._remove_button.setAccessibleName("Remove selected core visual mark")
                self._remove_button.setAccessibleDescription(
                    "Remove the selected core visual mark. Delete."
                )
        finally:
            del blockers

    def _visibility_description(self, record: OverlayRecord) -> str:
        visibility = self._canvas.visibility_for(record)
        if visibility is OverlayVisibility.VISIBLE:
            return "Fully shown inside the active crop."
        if visibility is OverlayVisibility.PARTIALLY_VISIBLE:
            return (
                "Partially shown in the preview; saved geometry remains relative to the full page."
            )
        return (
            "Outside the active crop and not shown in the preview; exact full-page geometry remains "
            "available here."
        )

    @staticmethod
    def _record_action_description(record: OverlayRecord) -> str:
        if record.kind is OverlayKind.SIGNATURE_IMAGE:
            return (
                "Position and size are editable here. Use the organizer removal action for "
                "explicit asset cleanup; this is a visual image, not a digital signature."
            )
        if record.kind in {OverlayKind.HIGHLIGHT, OverlayKind.SHAPE}:
            return (
                "Position and size are editable here; visual content is read-only. This ordinary "
                "visual mark is not permanent removal."
            )
        if type(record.payload) is TextualOverlayPayload:
            return (
                "Value, formatting, position, and size are editable with the native property "
                "controls; dragging is not required."
            )
        return (
            "Color, position, and size are editable with the native property controls; dragging "
            "is not required."
        )

    def _set_text_controls_visible(self, visible: bool) -> None:
        for widget in (
            self._value_label,
            self._value_edit,
            self._font_label,
            self._font_value,
            self._font_size_label,
            self._font_size,
        ):
            widget.setVisible(visible)

    def _set_color_controls_visible(self, visible: bool) -> None:
        self._color_label.setVisible(visible)
        self._color_combo.setVisible(visible)

    def _set_color(self, color: str) -> None:
        index = self._color_combo.findData(color)
        if index < 0:
            self._color_combo.insertItem(0, "Current custom color", color)
            index = 0
        self._color_combo.setCurrentIndex(index)

    def _request_apply(self) -> None:
        if self._apply_pending or not self._commit_properties_if_needed():
            return
        self._apply_pending = True
        self._commit_started = True
        self._set_editor_enabled(False)
        self._set_status(
            "Applying visual overlay changes. Keep this dialog open.",
            announce=True,
        )
        self.apply_requested.emit(self._state)

    def _set_editor_enabled(self, enabled: bool) -> None:
        for control in (
            self._zoom_combo,
            self._zoom_out_button,
            self._zoom_in_button,
            self._zoom_100_button,
            self._fit_page_button,
            self._show_guides,
            self._snap_to_guides,
            self._guide_spacing,
            self._pan_left_button,
            self._pan_right_button,
            self._pan_up_button,
            self._pan_down_button,
            self._pan_center_button,
        ):
            control.setEnabled(enabled)
        self._tool_combo.setEnabled(enabled)
        self._add_button.setEnabled(enabled)
        self._overlay_list.setEnabled(enabled)
        self._canvas.setEnabled(enabled)
        self._fine_step.setEnabled(enabled)
        for button in self._geometry_buttons():
            button.setEnabled(enabled and self._state.selected_record is not None)
        self._snap_selected_button.setEnabled(enabled and self._state.selected_record is not None)
        if enabled:
            self._refresh_from_state()
            self._apply_zoom(self._canvas.zoom_percent, announce=False)
            self._update_pan_actions()
        else:
            self._properties_group.setEnabled(False)
            self._remove_button.setEnabled(False)
            self._undo_button.setEnabled(False)
            self._redo_button.setEnabled(False)
        self._apply_button.setEnabled(enabled)
        self._cancel_button.setEnabled(enabled and not self._commit_started)
        if self._commit_started:
            self._cancel_button.setAccessibleDescription(
                "Unavailable after the first apply request because changes are retained for retry."
            )

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

    def _show_unexpected_failure(self) -> None:
        QApplication.beep()
        self._set_status(
            "PaperWorkShell could not update that visual overlay. Try the action again.",
            announce=True,
            assertive=True,
        )


def _percent_text(value: int) -> str:
    whole, fraction = divmod(value, 10_000)
    return f"{whole}.{fraction:04d} percent"


def safe_core_overlay_failure(code: object, message: object) -> tuple[str, str]:
    """Return a code plus fixed non-disclosing form-mark recovery message."""

    del message
    if type(code) is str and code in _SAFE_CORE_FAILURE_CODES:
        return (
            code,
            "PaperWorkShell could not save the visual overlays safely. Try saving again.",
        )
    return (
        "PWS_UI_REQUEST_INVALID",
        "PaperWorkShell could not confirm that visual overlay change safely.",
    )

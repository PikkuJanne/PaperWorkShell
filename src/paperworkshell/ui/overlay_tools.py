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
from decimal import Decimal
from itertools import pairwise
from typing import Final
from uuid import UUID

from PySide6.QtCore import QEvent, QObject, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAccessible,
    QAccessibleAnnouncementEvent,
    QCloseEvent,
    QKeyEvent,
    QKeySequence,
    QPixmap,
    QResizeEvent,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
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
)
from paperworkshell.domain.case_lifecycle import CaseLifecycleErrorCode
from paperworkshell.domain.manifest_repository import ManifestRepositoryErrorCode
from paperworkshell.domain.page_coordinates import (
    NormalizedPageBox,
    PageCoordinateError,
    PageCoordinateTransform,
    RenderedImageSize,
)
from paperworkshell.domain.page_operations import PageOperationErrorCode
from paperworkshell.localization.catalog import mark, tr
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
from paperworkshell.ui.localization_layout import adapt_localized_content
from paperworkshell.ui.localized_messages import (
    localized_overlay_announcement,
    localized_overlay_record_name,
    localized_overlay_record_summary,
)
from paperworkshell.ui.overlay_canvas import (
    DEFAULT_EDITOR_ZOOM_PERCENT,
    MAX_EDITOR_ZOOM_PERCENT,
    MIN_EDITOR_ZOOM_PERCENT,
    OverlayEditorCanvas,
)
from paperworkshell.ui.presentation import ElidingTextLabel as _PageIdentityLabel
from paperworkshell.ui.presentation import WrappingActionLayout, adapt_dialog_button_box
from paperworkshell.ui.wheel_safe import (
    WheelSafeComboBox as QComboBox,
)
from paperworkshell.ui.wheel_safe import (
    WheelSafeDoubleSpinBox as QDoubleSpinBox,
)
from paperworkshell.ui.wheel_safe import (
    WheelSafeSpinBox as QSpinBox,
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
_UNREPRESENTABLE_SAVED_VALUE: Final = mark(
    "The selected saved value cannot be represented by this editor. Replace the value explicitly before changing its other properties."
)
_LIST_DESCRIPTION: Final = mark(
    "Select a visual mark with the arrow keys, then use the property fields or keyboard shortcuts. Dragging is not required. Each row identifies the available property path."
)
_COLOR_CHOICES: Final = (
    (mark("Black"), "#000000ff"),
    (mark("Dark blue"), "#203080ff"),
    (mark("Green"), "#008040ff"),
    (mark("Dark red"), "#a02020ff"),
)
_TEXTUAL_KINDS: Final = frozenset({OverlayKind.TEXT, OverlayKind.DATE, OverlayKind.INITIALS})
_ZOOM_CHOICES: Final = (25, 50, 75, 100, 125, 150, 200, 300, 400)
_GUIDE_LABELS: Final = {
    10_000: mark("1 percent"),
    25_000: mark("2.5 percent"),
    50_000: mark("5 percent"),
    100_000: mark("10 percent"),
}
_SAFE_CANVAS_MESSAGES: Final = frozenset(
    {
        mark(
            "Pointer editing is unavailable for a deskewed preview. Use the exact fields or keyboard controls."
        ),
        mark("No visual mark is at that point. Select a mark from the list to edit it exactly."),
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
        page_label: str = "",
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
        self.setWindowTitle(tr("Add text or marks"))
        self.setAccessibleName(tr("Edit visual overlays for selected page"))
        self.setAccessibleDescription(
            tr(
                "Create core visual marks and select, move, or resize every released overlay type using a full-page preview plus a complete list, exact fields, buttons, and keyboard shortcuts. No pointer is required."
            )
        )
        self.setModal(True)
        available = self.screen().availableGeometry()
        self.resize(min(1180, available.width() - 32), min(780, available.height() - 32))

        self._content = QWidget()
        self._content.setObjectName("coreOverlayToolsContent")
        content_layout = QVBoxLayout(self._content)

        heading = QLabel(tr("Add text or marks"))
        heading.setObjectName("coreOverlayToolsHeading")
        heading.setAccessibleName(tr("Edit visual overlays"))
        heading.setStyleSheet("font-weight: 600;")
        content_layout.addWidget(heading)

        guidance = QLabel(
            tr(
                "Add text or a checkmark, then edit its settings. Drag the selected mark or use page percentages and keyboard controls. Save changes keeps this draft; Cancel discards it. Alt+Arrow moves; Alt+Shift+Arrow resizes."
            )
        )
        guidance.setObjectName("coreOverlayToolsGuidance")
        guidance.setAccessibleName(tr("Visual overlay editor instructions"))
        guidance.setAccessibleDescription(guidance.text())
        guidance.setTextFormat(Qt.TextFormat.PlainText)
        guidance.setWordWrap(True)
        content_layout.addWidget(guidance)

        self._canvas_group = self._build_canvas_group(
            page_transform or PageCoordinateTransform(),
            page_preview,
            page_display_size,
            pointer_mapping_enabled,
        )
        self._settings_panel = QWidget()
        self._settings_panel.setObjectName("overlayEditorSettingsPanel")
        settings = QVBoxLayout(self._settings_panel)
        settings.setContentsMargins(0, 0, 0, 0)
        settings.addWidget(self._build_add_group())
        settings.addWidget(self._build_properties_group())
        settings.addWidget(self._build_list_group())
        settings.addLayout(self._build_edit_actions())
        self._editor_layout = QGridLayout()
        self._editor_layout.addWidget(self._settings_panel, 0, 0)
        self._editor_layout.addWidget(self._canvas_group, 0, 1)
        self._editor_layout.setColumnStretch(0, 1)
        self._editor_layout.setColumnStretch(1, 1)
        self._editor_stacked = False
        content_layout.addLayout(self._editor_layout)

        self._status = QLabel(localized_overlay_announcement(state.announcement))
        self._status.setObjectName("coreOverlayToolsStatus")
        self._status.setAccessibleName(tr("Visual overlay editor status"))
        self._status.setAccessibleDescription(localized_overlay_announcement(state.announcement))
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setWordWrap(True)
        self._status.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._status.setStyleSheet(_FOCUS_VISIBLE_TEXT_STYLE)
        self._status.setFrameShape(QFrame.Shape.StyledPanel)
        content_layout.addWidget(self._status)
        content_layout.addStretch(1)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("coreOverlayToolsScrollArea")
        self._scroll.setAccessibleName(tr("Visual overlay controls"))
        self._scroll.setAccessibleDescription(
            tr("Scrollable visual overlay list, property fields, actions, and status.")
        )
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scroll.setWidget(self._content)
        adapt_localized_content(self._scroll)

        self._buttons = QDialogButtonBox()
        self._buttons.setObjectName("coreOverlayToolsDialogButtons")
        self._buttons.setAccessibleName(tr("Apply or cancel visual overlay changes"))
        self._apply_button = self._buttons.addButton(
            tr("Save changes"),
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self._apply_button.setObjectName("applyFormMarksButton")
        self._apply_button.setAccessibleName(tr("Save changes"))
        self._apply_button.setAccessibleDescription(
            tr("Request saving the complete local visual overlay draft.")
        )
        self._apply_button.setDefault(True)
        self._cancel_button = self._buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._cancel_button.setObjectName("cancelFormMarksButton")
        self._cancel_button.setAccessibleName(tr("Cancel visual overlay changes"))
        self._cancel_button.setAccessibleDescription(
            tr("Discard this local draft without requesting a case change. Escape.")
        )
        self._apply_button.clicked.connect(self._request_apply)
        self._cancel_button.clicked.connect(self.reject)
        adapt_dialog_button_box(self._buttons)

        layout = QVBoxLayout(self)
        self._page_label = _PageIdentityLabel(page_label)
        self._page_label.setObjectName("overlayEditorPageLabel")
        self._page_label.setTextFormat(Qt.TextFormat.PlainText)
        self._page_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._page_label.setToolTip(page_label)
        self._page_label.setAccessibleName(page_label or tr("Selected page"))
        self._page_label.setVisible(bool(page_label))
        layout.addWidget(self._page_label)
        layout.addWidget(self._value_row)
        layout.addWidget(self._scroll, 1)
        layout.addWidget(self._buttons)

        self._connect_property_tracking()
        self._focus_reveal_control: QWidget | None = None
        self._focus_reveal_timer = QTimer(self)
        self._focus_reveal_timer.setSingleShot(True)
        self._focus_reveal_timer.setInterval(0)
        self._focus_reveal_timer.timeout.connect(self._reveal_focused_control)
        self._content.installEventFilter(self)
        self._scroll.viewport().installEventFilter(self)
        self._build_shortcuts()
        self._set_tab_order()
        self._refresh_from_state()
        QTimer.singleShot(0, self._set_initial_focus)
        QTimer.singleShot(0, lambda: self._fit_page(announce=False))

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
        group = QGroupBox(tr("Full-page overlay view"))
        group.setObjectName("overlayEditorCanvasGroup")
        group.setAccessibleName(tr("Full-page overlay view"))
        group.setAccessibleDescription(
            tr(
                "A visual page projection with patterned guides and selection outlines. The native visual-marks list and exact controls provide every editing action without a pointer. The active crop starts at left {value1} and top {value2}, with width {value3} and height {value4}.",
                value1=_percent_text(transform.manifest_crop.x),
                value2=_percent_text(transform.manifest_crop.y),
                value3=_percent_text(transform.manifest_crop.width),
                value4=_percent_text(transform.manifest_crop.height),
            )
        )
        layout = QVBoxLayout(group)

        if not pointer_mapping_enabled:
            notice = QLabel(
                tr(
                    "Deskewed page: the pixel preview is replaced by a schematic. Pointer editing is off; the native list, exact fields, and keyboard controls remain complete."
                )
            )
            notice.setObjectName("overlayEditorDeskewNotice")
            notice.setAccessibleName(tr("Deskewed overlay preview notice"))
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
        self._canvas_scroll.setAccessibleName(tr("Scrollable full-page overlay preview"))
        self._canvas_scroll.setAccessibleDescription(
            tr(
                "Use the named pan buttons or scroll bars to inspect the page. Use the list and property controls for a complete keyboard editing path."
            )
        )
        self._canvas_scroll.setWidgetResizable(False)
        self._canvas_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._canvas_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._canvas_scroll.setMinimumHeight(260)
        self._canvas_scroll.setWidget(self._canvas)
        layout.addWidget(self._canvas_scroll, 1)

        display_controls = WrappingActionLayout()
        zoom_label = QLabel(tr("Zoom"))
        self._zoom_combo = QComboBox()
        self._zoom_combo.setObjectName("overlayEditorZoomCombo")
        self._zoom_combo.setAccessibleName(tr("Page preview zoom percentage"))
        self._zoom_combo.setAccessibleDescription(
            tr(
                "Change only the visual preview scale. Saved overlay coordinates never change. Alt+Down opens the choices."
            )
        )
        for percent in _ZOOM_CHOICES:
            self._zoom_combo.addItem(tr("{value1} percent", value1=percent), percent)
        self._zoom_combo.setCurrentIndex(self._zoom_combo.findData(DEFAULT_EDITOR_ZOOM_PERCENT))
        self._zoom_combo.currentIndexChanged.connect(self._zoom_changed)
        zoom_label.setBuddy(self._zoom_combo)
        self._zoom_out_button = self._display_button(
            tr("Zoom out"), "overlayEditorZoomOutButton", tr("Reduce preview zoom. Ctrl+Minus.")
        )
        self._zoom_in_button = self._display_button(
            tr("Zoom in"), "overlayEditorZoomInButton", tr("Increase preview zoom. Ctrl+Plus.")
        )
        self._zoom_100_button = self._display_button(
            "100%", "overlayEditorZoom100Button", tr("Set preview zoom to 100 percent. Ctrl+0.")
        )
        self._fit_page_button = self._display_button(
            tr("Fit page"),
            "overlayEditorFitPageButton",
            tr("Fit the complete page in the preview."),
        )
        self._zoom_out_button.clicked.connect(self._zoom_out)
        self._zoom_in_button.clicked.connect(self._zoom_in)
        self._zoom_100_button.clicked.connect(lambda: self._set_zoom(100))
        self._fit_page_button.clicked.connect(self._fit_page)
        display_controls.addWidget(zoom_label)
        display_controls.addWidget(self._zoom_combo)
        display_controls.addWidget(self._zoom_out_button)
        display_controls.addWidget(self._zoom_in_button)
        display_controls.addWidget(self._zoom_100_button)
        display_controls.addWidget(self._fit_page_button)

        self._show_guides = QCheckBox(tr("Show dashed guides"))
        self._show_guides.setObjectName("overlayEditorShowGuidesCheck")
        self._show_guides.setAccessibleName(tr("Show dashed alignment guides"))
        self._show_guides.setAccessibleDescription(
            tr(
                "Show a patterned grid and text label; guide state is never conveyed by color alone."
            )
        )
        self._show_guides.setChecked(True)
        self._snap_to_guides = QCheckBox(tr("Snap pointer moves to guides"))
        self._snap_to_guides.setObjectName("overlayEditorSnapCheck")
        self._snap_to_guides.setAccessibleName(tr("Snap pointer moves to guides"))
        self._snap_to_guides.setAccessibleDescription(
            tr(
                "When checked, pointer movement aligns the selected mark to the chosen normalized grid."
            )
        )
        guide_label = QLabel(tr("Guide spacing"))
        self._guide_spacing = QComboBox()
        self._guide_spacing.setObjectName("overlayEditorGuideSpacingCombo")
        self._guide_spacing.setAccessibleName(tr("Normalized guide spacing"))
        self._guide_spacing.setAccessibleDescription(
            tr(
                "Choose guide spacing in full-page percentages, independent of preview zoom. Alt+Down opens the choices."
            )
        )
        for step in GUIDE_STEPS_MILLIONTHS:
            self._guide_spacing.addItem(tr(_GUIDE_LABELS[step]), step)
        self._guide_spacing.setCurrentIndex(self._guide_spacing.findData(50_000))
        guide_label.setBuddy(self._guide_spacing)
        self._snap_selected_button = self._display_button(
            tr("Snap selected now"),
            "overlayEditorSnapSelectedButton",
            tr("Move the selected mark origin to the nearest normalized guides."),
        )
        self._snap_selected_button.clicked.connect(self._snap_selected)
        self._show_guides.toggled.connect(self._guide_options_changed)
        self._snap_to_guides.toggled.connect(self._guide_options_changed)
        self._guide_spacing.currentIndexChanged.connect(self._guide_options_changed)
        display_controls.addWidget(self._show_guides)
        display_controls.addWidget(self._snap_to_guides)
        display_controls.addWidget(guide_label)
        display_controls.addWidget(self._guide_spacing)
        display_controls.addWidget(self._snap_selected_button)

        self._pan_left_button = self._display_button(
            tr("Pan left"), "overlayEditorPanLeftButton", tr("Move the preview viewport left.")
        )
        self._pan_right_button = self._display_button(
            tr("Pan right"), "overlayEditorPanRightButton", tr("Move the preview viewport right.")
        )
        self._pan_up_button = self._display_button(
            tr("Pan up"), "overlayEditorPanUpButton", tr("Move the preview viewport up.")
        )
        self._pan_down_button = self._display_button(
            tr("Pan down"), "overlayEditorPanDownButton", tr("Move the preview viewport down.")
        )
        self._pan_center_button = self._display_button(
            tr("Center"), "overlayEditorPanCenterButton", tr("Center the preview viewport.")
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
        for button in (
            self._pan_left_button,
            self._pan_right_button,
            self._pan_up_button,
            self._pan_down_button,
            self._pan_center_button,
        ):
            display_controls.addWidget(button)

        self._snap_feedback = QLabel(tr("Snap feedback: no snap action yet."))
        self._snap_feedback.setObjectName("overlayEditorSnapFeedback")
        self._snap_feedback.setAccessibleName(tr("Overlay snap feedback"))
        self._snap_feedback.setAccessibleDescription(self._snap_feedback.text())
        self._snap_feedback.setTextFormat(Qt.TextFormat.PlainText)
        self._snap_feedback.setWordWrap(True)
        display_controls.addWidget(self._snap_feedback)
        layout.addLayout(display_controls)
        self._guide_options_changed()
        self._update_pan_actions()
        return group

    def _display_button(self, text: str, object_name: str, description: str) -> QPushButton:
        button = QPushButton(tr(text))
        button.setObjectName(object_name)
        button.setAccessibleName(tr(text))
        button.setAccessibleDescription(tr(description))
        return button

    def set_apply_succeeded(self) -> bool:
        """Accept the dialog after the caller confirms the pending draft was applied."""

        if not self._apply_pending:
            return False
        self._apply_pending = False
        self._set_status(tr("Visual overlay changes applied."), announce=False)
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
            tr("Retry saving the retained visual overlay draft.")
        )
        self._set_status(
            tr(
                "{value1}: {value2} Changes are retained for retry.",
                value1=selected_code,
                value2=selected_message,
            ),
            announce=True,
            assertive=True,
        )
        self._apply_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._scroll.ensureWidgetVisible(self._apply_button, 8, 8)
        return True

    def reject(self) -> None:
        """Keep the dialog open after its draft crosses the persistence boundary."""

        if self._commit_started:
            QApplication.beep()
            self._set_status(
                tr(
                    "These visual overlay changes are retained for retry and cannot be discarded from this dialog."
                ),
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

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Apply local properties after a field has processed its ordinary Return."""

        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter} and event.modifiers() in {
            Qt.KeyboardModifier.NoModifier,
            Qt.KeyboardModifier.KeypadModifier,
        }:
            focused = QApplication.focusWidget()
            fields = (
                self._value_edit,
                self._font_size,
                self._color_combo,
                self._left,
                self._top,
                self._width,
                self._height,
            )
            if (
                focused is not None
                and not self._color_combo.view().isVisible()
                and any(field is focused or field.isAncestorOf(focused) for field in fields)
            ):
                self._apply_properties()
                event.accept()
                return
        super().keyPressEvent(event)

    def _build_add_group(self) -> QGroupBox:
        group = QGroupBox(tr("Add a visual mark"))
        group.setObjectName("coreOverlayAddGroup")
        group.setAccessibleName(tr("Add a visual mark"))
        group.setAccessibleDescription(
            tr("Choose one of the five core form-mark types, then add it to the selected page.")
        )
        layout = QVBoxLayout(group)
        quick = WrappingActionLayout()
        self._add_text_button = QPushButton(tr("Add text"))
        self._add_text_button.setObjectName("addTextButton")
        self._add_text_button.setAccessibleName(tr("Add text"))
        self._add_text_button.clicked.connect(lambda: self._add_kind(OverlayKind.TEXT))
        self._add_checkmark_button = QPushButton(tr("Add checkmark"))
        self._add_checkmark_button.setObjectName("addCheckmarkButton")
        self._add_checkmark_button.setAccessibleName(tr("Add checkmark"))
        self._add_checkmark_button.clicked.connect(lambda: self._add_kind(OverlayKind.CHECKMARK))
        quick.addWidget(self._add_text_button)
        quick.addWidget(self._add_checkmark_button)
        layout.addLayout(quick)
        label = QLabel(tr("Other mark type"))
        label.setObjectName("newOverlayTypeLabel")
        self._tool_combo = QComboBox()
        self._tool_combo.setObjectName("newOverlayTypeCombo")
        self._tool_combo.setAccessibleName(tr("New visual form-mark type"))
        self._tool_combo.setAccessibleDescription(
            tr(
                "Choose Text, Date, Checkmark, Cross, or Initials with the keyboard. Alt+Down opens the choices."
            )
        )
        for descriptor in CORE_OVERLAY_TOOL_DESCRIPTORS:
            self._tool_combo.addItem(tr(descriptor.label), descriptor.kind)
        label.setBuddy(self._tool_combo)
        self._add_button = QPushButton(tr("Add selected type"))
        self._add_button.setObjectName("addCoreOverlayButton")
        self._add_button.setAccessibleName(tr("Add selected visual form-mark type"))
        self._add_button.setAccessibleDescription(
            tr("Add the selected type at a deterministic default position and select it.")
        )
        self._add_button.clicked.connect(self._add_selected_type)
        layout.addWidget(label)
        layout.addWidget(self._tool_combo)
        layout.addWidget(self._add_button)
        return group

    def _build_list_group(self) -> QGroupBox:
        group = QGroupBox(tr("Visual marks on this page"))
        group.setObjectName("coreOverlayListGroup")
        group.setAccessibleName(tr("Visual marks on this page"))
        group.setAccessibleDescription(tr(_LIST_DESCRIPTION))
        layout = QVBoxLayout(group)
        self._overlay_list = QListWidget()
        self._overlay_list.setObjectName("coreOverlayList")
        self._overlay_list.setAccessibleName(tr("Visual overlays on selected page"))
        self._overlay_list.setAccessibleDescription(tr(_LIST_DESCRIPTION))
        self._overlay_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._overlay_list.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self._overlay_list.setDragEnabled(False)
        self._overlay_list.setAcceptDrops(False)
        self._overlay_list.setDropIndicatorShown(False)
        self._overlay_list.setWordWrap(True)
        self._overlay_list.setMaximumHeight(120)
        self._overlay_list.currentRowChanged.connect(self._selected_row_changed)
        layout.addWidget(self._overlay_list)
        return group

    def _build_properties_group(self) -> QGroupBox:
        self._properties_group = QGroupBox(tr("Mark settings"))
        self._properties_group.setObjectName("coreOverlayPropertiesGroup")
        self._properties_group.setAccessibleName(tr("Mark settings"))
        self._properties_group.setAccessibleDescription(
            tr("Edit the selected mark's value, formatting, and exact full-page normalized box.")
        )
        layout = QGridLayout(self._properties_group)

        self._selection_capability = QLabel(tr("Select a visual mark to edit its exact geometry."))
        self._selection_capability.setObjectName("overlayEditorSelectionCapability")
        self._selection_capability.setAccessibleName(tr("Selected overlay editing capabilities"))
        self._selection_capability.setAccessibleDescription(self._selection_capability.text())
        self._selection_capability.setTextFormat(Qt.TextFormat.PlainText)
        self._selection_capability.setWordWrap(True)
        layout.addWidget(self._selection_capability, 0, 0, 1, 3)

        self._value_label = QLabel(tr("Value"))
        self._value_label.setObjectName("coreOverlayValueLabel")
        self._value_edit = QLineEdit()
        self._value_edit.setObjectName("coreOverlayValueEdit")
        # QLineEdit counts UTF-16 code units. One extra full Python code point
        # lets the domain reject 4,097-code-point input instead of Qt silently
        # truncating it to a valid-looking 4,096-code-point value.
        self._value_edit.setMaxLength(_TEXT_INPUT_MAX_UTF16_UNITS)
        self._value_label.setBuddy(self._value_edit)
        self._value_row = QWidget()
        self._value_row.setObjectName("overlayEditorValueRow")
        value_layout = QGridLayout(self._value_row)
        value_layout.setContentsMargins(0, 0, 0, 0)
        value_layout.addWidget(self._value_label, 0, 0)
        value_layout.addWidget(self._value_edit, 0, 1)
        value_layout.setColumnStretch(1, 1)

        self._font_label = QLabel(tr("Font"))
        self._font_label.setObjectName("coreOverlayFontLabel")
        self._font_value = QLabel(tr("PaperWorkShell default"))
        self._font_value.setObjectName("coreOverlayFontValue")
        self._font_value.setAccessibleName(tr("Visual form-mark font"))
        self._font_value.setAccessibleDescription(
            tr("The approved PaperWorkShell default overlay font.")
        )
        self._font_value.setWordWrap(True)
        layout.addWidget(self._font_label, 2, 0)
        layout.addWidget(self._font_value, 2, 1, 1, 2)

        self._font_size_label = QLabel(tr("Font size"))
        self._font_size_label.setObjectName("coreOverlayFontSizeLabel")
        self._font_size = QSpinBox()
        self._font_size.setObjectName("coreOverlayFontSizeSpin")
        self._font_size.setAccessibleName(tr("Visual form-mark font size in points"))
        self._font_size.setAccessibleDescription(
            tr(
                "Choose the selected text, date, or initials size in typographic points. Enter applies the selected mark's local properties."
            )
        )
        self._font_size.setRange(MIN_FONT_SIZE_POINTS, MAX_FONT_SIZE_POINTS)
        self._font_size.setSuffix(tr(" points"))
        self._font_size_label.setBuddy(self._font_size)
        layout.addWidget(self._font_size_label, 3, 0)
        layout.addWidget(self._font_size, 3, 1, 1, 2)

        self._color_label = QLabel(tr("Color"))
        self._color_label.setObjectName("coreOverlayColorLabel")
        self._color_combo = QComboBox()
        self._color_combo.setObjectName("coreOverlayColorCombo")
        self._color_combo.setAccessibleName(tr("Visual form-mark color"))
        self._color_combo.setAccessibleDescription(
            tr(
                "Choose a named visual color; color is never the only status indicator. Alt+Down opens the choices. Enter in the closed field applies the selected mark's local properties."
            )
        )
        for name, value in _COLOR_CHOICES:
            self._color_combo.addItem(tr(name), value)
        self._color_label.setBuddy(self._color_combo)
        layout.addWidget(self._color_label, 4, 0)
        layout.addWidget(self._color_combo, 4, 1, 1, 2)

        self._left, self._left_percent = self._coordinate_control(
            "coreOverlayLeftSpin",
            tr("Left position in percent"),
            tr("Distance from the full page's left edge, from 0 through 100 percent."),
        )
        self._top, self._top_percent = self._coordinate_control(
            "coreOverlayTopSpin",
            tr("Top position in percent"),
            tr("Distance from the full page's top edge, from 0 through 100 percent."),
        )
        self._width, self._width_percent = self._coordinate_control(
            "coreOverlayWidthSpin",
            tr("Width in percent"),
            tr("Visual-mark width as a percentage of the full page."),
            positive=True,
        )
        self._height, self._height_percent = self._coordinate_control(
            "coreOverlayHeightSpin",
            tr("Height in percent"),
            tr("Visual-mark height as a percentage of the full page."),
            positive=True,
        )
        for row, (label_text, control, percent) in enumerate(
            (
                (tr("Left"), self._left, self._left_percent),
                (tr("Top"), self._top, self._top_percent),
                (tr("Width"), self._width, self._width_percent),
                (tr("Height"), self._height, self._height_percent),
            ),
            start=5,
        ):
            label = QLabel(tr(label_text))
            label.setObjectName(control.objectName().removesuffix("Spin") + "Label")
            label.setBuddy(control)
            layout.addWidget(label, row, 0)
            layout.addWidget(control, row, 1)
            layout.addWidget(percent, row, 2)

        self._apply_properties_button = QPushButton(
            tr("Apply property changes"), self._properties_group
        )
        self._apply_properties_button.setObjectName("applyCoreOverlayPropertiesButton")
        self._apply_properties_button.setAccessibleName(tr("Apply selected visual-mark properties"))
        self._apply_properties_button.setAccessibleDescription(
            tr(
                "Apply the value, formatting, position, and size fields to the selected mark. Enter in a property field, or Ctrl+Enter."
            )
        )
        self._apply_properties_button.clicked.connect(self._apply_properties)
        self._apply_properties_button.setVisible(False)
        return self._properties_group

    def _coordinate_control(
        self,
        object_name: str,
        accessible_name: str,
        accessible_description: str,
        *,
        positive: bool = False,
    ) -> tuple[QDoubleSpinBox, QLabel]:
        control = QDoubleSpinBox()
        control.setObjectName(object_name)
        control.setAccessibleName(tr(accessible_name))
        accessible_description += tr(" Enter applies the selected mark's local properties.")
        control.setAccessibleDescription(tr(accessible_description))
        control.setProperty("pwsCoordinateBaseDescription", accessible_description)
        control.setDecimals(4)
        control.setRange(0.0001 if positive else 0.0, 100.0)
        control.setSingleStep(0.1)
        control.setSuffix(" %")
        percent = QLabel(tr("0.0000 percent"))
        percent.setObjectName(f"{object_name}Percent")
        percent.setAccessibleName(tr("{value1} percentage", value1=accessible_name))
        percent.setTextFormat(Qt.TextFormat.PlainText)
        percent.setWordWrap(True)
        percent.setVisible(False)
        control.valueChanged.connect(
            lambda value, target=percent, source=control, base=accessible_description: (
                self._update_coordinate_accessibility(source, target, base, value)
            )
        )
        return control, percent

    def _update_coordinate_accessibility(
        self,
        control: QDoubleSpinBox,
        percent: QLabel,
        base_description: str,
        value: float,
    ) -> None:
        value_text = _percent_text(_normalized_percent(value))
        percent.setText(value_text)
        control.setAccessibleDescription(
            tr("{value1} Current value: {value2}.", value1=base_description, value2=value_text)
        )

    def _build_edit_actions(self) -> QVBoxLayout:
        layout = QVBoxLayout()
        geometry = WrappingActionLayout()
        self._fine_step = QCheckBox(tr("Fine step (0.1%)"))
        self._fine_step.setObjectName("overlayEditorFineStepCheck")
        self._fine_step.setAccessibleName(tr("Use fine movement and resize step"))
        self._fine_step.setAccessibleDescription(
            tr(
                "When checked, named movement and resize buttons use 0.1 percent instead of 1 percent."
            )
        )
        geometry.addWidget(self._fine_step)
        self._move_left_button = self._geometry_button(
            tr("Move left"), "overlayEditorMoveLeftButton", KeyboardDirection.LEFT, resize=False
        )
        self._move_right_button = self._geometry_button(
            tr("Move right"), "overlayEditorMoveRightButton", KeyboardDirection.RIGHT, resize=False
        )
        self._move_up_button = self._geometry_button(
            tr("Move up"), "overlayEditorMoveUpButton", KeyboardDirection.UP, resize=False
        )
        self._move_down_button = self._geometry_button(
            tr("Move down"), "overlayEditorMoveDownButton", KeyboardDirection.DOWN, resize=False
        )
        self._shrink_width_button = self._geometry_button(
            tr("Shrink width"),
            "overlayEditorShrinkWidthButton",
            KeyboardDirection.LEFT,
            resize=True,
        )
        self._grow_width_button = self._geometry_button(
            tr("Grow width"),
            "overlayEditorGrowWidthButton",
            KeyboardDirection.RIGHT,
            resize=True,
        )
        self._shrink_height_button = self._geometry_button(
            tr("Shrink height"),
            "overlayEditorShrinkHeightButton",
            KeyboardDirection.UP,
            resize=True,
        )
        self._grow_height_button = self._geometry_button(
            tr("Grow height"),
            "overlayEditorGrowHeightButton",
            KeyboardDirection.DOWN,
            resize=True,
        )
        for button in self._geometry_buttons():
            geometry.addWidget(button)
        layout.addLayout(geometry)

        history = WrappingActionLayout()
        self._remove_button = QPushButton(tr("Remove selected"))
        self._remove_button.setObjectName("removeCoreOverlayButton")
        self._remove_button.setAccessibleName(tr("Remove selected core visual mark"))
        self._remove_button.setAccessibleDescription(tr("Remove the selected mark. Delete."))
        self._undo_button = QPushButton(tr("Undo"))
        self._undo_button.setObjectName("undoCoreOverlayButton")
        self._undo_button.setAccessibleName(tr("Undo visual overlay change"))
        self._undo_button.setAccessibleDescription(
            tr("Undo the last local visual overlay change. Ctrl+Z.")
        )
        self._redo_button = QPushButton(tr("Redo"))
        self._redo_button.setObjectName("redoCoreOverlayButton")
        self._redo_button.setAccessibleName(tr("Redo visual overlay change"))
        self._redo_button.setAccessibleDescription(
            tr("Redo the next local visual overlay change. Ctrl+Y.")
        )
        self._remove_button.clicked.connect(self._remove_selected)
        self._undo_button.clicked.connect(self._undo)
        self._redo_button.clicked.connect(self._redo)
        history.addWidget(self._remove_button)
        history.addWidget(self._undo_button)
        history.addWidget(self._redo_button)
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
        button = QPushButton(tr(text))
        button.setObjectName(object_name)
        button.setAccessibleName(text + tr(" selected visual mark"))
        button.setAccessibleDescription(
            tr("Resize the selected mark using the current named step.")
            if resize
            else tr("Move the selected mark using the current named step.")
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
            ("Alt+Down", self._move_down_or_open_combo),
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
            ("Ctrl++", self._zoom_in),
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
            self._add_text_button,
            self._add_checkmark_button,
            self._value_edit,
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
        QWidget.setTabOrder(self._status, self._add_text_button)

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
            self._preview_properties()

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
            self._focus_visible_control(self._value_edit)
            self._value_edit.selectAll()
        else:
            self._focus_visible_control(self._overlay_list)

    def _add_kind(self, kind: OverlayKind) -> None:
        index = self._tool_combo.findData(kind.value)
        self._tool_combo.setCurrentIndex(index)
        self._add_selected_type()

    def _property_box(self) -> NormalizedPageBox:
        return NormalizedPageBox(
            _normalized_percent(self._left.value()),
            _normalized_percent(self._top.value()),
            _normalized_percent(self._width.value()),
            _normalized_percent(self._height.value()),
        )

    def _preview_properties(self) -> None:
        """Paint the in-progress fields without committing character-by-character history."""
        record = self._state.selected_record
        if record is None or not self._loaded_value_preserved:
            return
        try:
            preview = self._state.replace_selected_box(self._property_box())
            if type(record.payload) is TextualOverlayPayload:
                color = self._color_combo.currentData()
                if type(color) is not str:
                    return
                preview = preview.edit_selected(
                    value=self._value_edit.text(),
                    font_size_points=self._font_size.value(),
                    color_value=color,
                )
            elif type(record.payload) is MarkOverlayPayload:
                color = self._color_combo.currentData()
                if type(color) is not str:
                    return
                preview = preview.edit_selected(color_value=color)
        except OverlayError, PageCoordinateError:
            return
        self._canvas.set_records(preview.editor_records, preview.selected_overlay_id)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not hasattr(self, "_editor_layout"):
            return
        stacked = self.width() < 850
        if stacked != self._editor_stacked:
            self._editor_layout.removeWidget(self._canvas_group)
            self._editor_layout.addWidget(
                self._canvas_group, 1 if stacked else 0, 0 if stacked else 1
            )
            self._editor_layout.setColumnStretch(1, 0 if stacked else 1)
            self._editor_stacked = stacked

    def _focus_visible_control(self, control: QWidget) -> None:
        control.setFocus(Qt.FocusReason.OtherFocusReason)
        self._focus_reveal_control = control
        self._reveal_focused_control()
        self._focus_reveal_control = control
        self._focus_reveal_timer.start()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if (
            event.type() in (QEvent.Type.LayoutRequest, QEvent.Type.Resize)
            and self.isVisible()
            and watched in (self._content, self._scroll.viewport())
        ):
            focused = QApplication.focusWidget()
            if focused is not None and self._content.isAncestorOf(focused):
                self._focus_reveal_control = focused
                self._focus_reveal_timer.start()
        return super().eventFilter(watched, event)

    def _reveal_focused_control(self) -> None:
        """Reveal newly shown fields after their wrapping layout has settled."""

        control = self._focus_reveal_control
        self._focus_reveal_control = None
        focused = QApplication.focusWidget()
        if (
            control is None
            or not self.isVisible()
            or not control.isVisible()
            or not self._content.isAncestorOf(control)
            or focused is None
            or (focused is not control and not control.isAncestorOf(focused))
        ):
            return
        content_layout = self._content.layout()
        if content_layout is not None:
            content_layout.activate()
        viewport = self._scroll.viewport()
        horizontal_margin = min(8, max(0, (viewport.width() - control.width()) // 2))
        vertical_margin = min(8, max(0, (viewport.height() - control.height()) // 2))
        self._scroll.ensureWidgetVisible(control, horizontal_margin, vertical_margin)
        # QScrollArea may reveal only a line edit's input-method cursor rect.
        # Keep the complete field/frame visible when it fits the viewport.
        top_left = control.mapTo(viewport, control.rect().topLeft())
        bottom_right = control.mapTo(viewport, control.rect().bottomRight())
        for start, end, length, margin, bar in (
            (
                top_left.x(),
                bottom_right.x(),
                viewport.width(),
                horizontal_margin,
                self._scroll.horizontalScrollBar(),
            ),
            (
                top_left.y(),
                bottom_right.y(),
                viewport.height(),
                vertical_margin,
                self._scroll.verticalScrollBar(),
            ),
        ):
            delta = start - margin if start < margin else max(0, end - (length - 1 - margin))
            if delta:
                bar.setValue(bar.value() + delta)

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
                tr(_UNREPRESENTABLE_SAVED_VALUE),
                announce=True,
                assertive=True,
            )
            self._focus_visible_control(self._status)
            return False
        try:
            box = self._property_box()
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
            self._set_status(tr(error.safe_message), announce=True, assertive=True)
            self._focus_visible_control(self._status)
            return False
        except BaseException:
            self._show_unexpected_failure()
            return False
        self._properties_dirty = False
        message = (
            localized_overlay_announcement(state.announcement)
            if state is not self._state
            else tr(
                "{value1} properties unchanged.",
                value1=overlay_editor_kind_accessible_name(record.kind),
            )
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
                tr(
                    "Close this editor and use Remove signature image so case-asset cleanup stays explicit."
                ),
                announce=True,
                assertive=True,
            )
            return
        if record is not None and record.kind in {OverlayKind.HIGHLIGHT, OverlayKind.SHAPE}:
            self._set_status(
                tr(
                    "This ordinary visual-mark type is geometry-only in this editor and cannot be removed here."
                ),
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
            self._focus_visible_control(self._overlay_list)
            return
        self._focus_visible_control(self._tool_combo)

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

    def _move_down_or_open_combo(self) -> None:
        focused = QApplication.focusWidget()
        while focused is not None and focused is not self:
            if isinstance(focused, QComboBox):
                if focused.isEnabled():
                    focused.showPopup()
                return
            focused = focused.parentWidget()
        self._move(KeyboardDirection.DOWN)

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
            message = tr(
                "Snap feedback: pointer move aligned the selected mark to the nearest {value1} dashed guides.",
                value1=tr(_GUIDE_LABELS[spacing]),
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
        result = tr("already aligned with") if self._state is prior else tr("aligned to")
        message = tr(
            "Snap feedback: selected mark {value1} the nearest {value2} dashed guides.",
            value1=result,
            value2=tr(_GUIDE_LABELS[step]),
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
        visible = tr("shown as dashed lines") if self._show_guides.isChecked() else tr("hidden")
        snapping = tr("on") if self._snap_to_guides.isChecked() else tr("off")
        description = tr(
            "Guides are {value1}; pointer snap is {value2}; spacing is {value3}.",
            value1=visible,
            value2=snapping,
            value3=tr(_GUIDE_LABELS[step]),
        )
        self._show_guides.setAccessibleDescription(tr(description))
        self._snap_to_guides.setAccessibleDescription(tr(description))

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
        description = tr(
            "Page preview zoom is {value1} percent. Saved overlay positions and sizes are unchanged.",
            value1=percent,
        )
        self._zoom_combo.setAccessibleDescription(tr(description))
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

    def _fit_page(self, *, announce: bool = True) -> None:
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
        if announce:
            self._set_status(
                tr(
                    "Page preview fit at {value1} percent and centered. Saved overlay positions and sizes were not changed.",
                    value1=choice,
                ),
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
            tr(
                "Page preview panned {value1}. Saved overlay positions were not changed.",
                value1=direction,
            ),
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
            tr("Page preview centered. Saved overlay positions were not changed."),
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
        self._add_text_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._scroll.ensureWidgetVisible(self._add_text_button, 8, 8)

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
            self._set_status(tr(error.safe_message), announce=True, assertive=True)
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
            self._set_status(localized_overlay_announcement(state.announcement), announce=True)

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
                        f"{localized_overlay_record_summary(record, position, len(records))} "
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
            self._value_row.setEnabled(selected)
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
                tr(_UNREPRESENTABLE_SAVED_VALUE),
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
                    base = control.property("pwsCoordinateBaseDescription")
                    if type(base) is not str:
                        raise ValueError("coordinate base description is unavailable")
                    self._update_coordinate_accessibility(
                        control,
                        percent,
                        base,
                        control.value(),
                    )
                self._value_label.setText(tr("Value"))
                self._value_edit.setAccessibleName(tr("Visual form-mark value"))
                self._value_edit.setAccessibleDescription(
                    tr("Select a text, date, or initials visual mark to edit its value.")
                )
                self._set_text_controls_visible(False)
                self._set_color_controls_visible(False)
                self._selection_capability.setText(
                    tr("Select a visual mark to edit its exact position and size.")
                )
                self._selection_capability.setAccessibleDescription(
                    self._selection_capability.text()
                )
                self._properties_group.setAccessibleDescription(
                    tr("Select a visual mark to inspect and edit its available exact properties.")
                )
                self._apply_properties_button.setText(tr("Apply property changes"))
                self._apply_properties_button.setAccessibleName(
                    tr("Apply selected visual-mark properties")
                )
                self._apply_properties_button.setAccessibleDescription(
                    tr("Select a visual mark before applying properties. Ctrl+Enter.")
                )
                self._remove_button.setText(tr("Remove selected"))
                self._remove_button.setAccessibleName(tr("Remove selected core visual mark"))
                self._remove_button.setAccessibleDescription(
                    tr("Select a removable core visual mark. Delete.")
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
                value_label = (
                    tr("Text") if record.kind is OverlayKind.TEXT else tr(descriptor.value_label)
                )
                self._value_label.setText(value_label)
                self._value_edit.setAccessibleName(value_label)
                self._value_edit.setAccessibleDescription(
                    tr(descriptor.value_description)
                    + tr(" Enter applies the selected mark's local properties.")
                )
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
            self._left.setValue(record.box.x / 10_000)
            self._top.setValue(record.box.y / 10_000)
            self._width.setValue(record.box.width / 10_000)
            self._height.setValue(record.box.height / 10_000)
            for control, percent in (
                (self._left, self._left_percent),
                (self._top, self._top_percent),
                (self._width, self._width_percent),
                (self._height, self._height_percent),
            ):
                base = control.property("pwsCoordinateBaseDescription")
                if type(base) is not str:
                    raise ValueError("coordinate base description is unavailable")
                self._update_coordinate_accessibility(
                    control,
                    percent,
                    base,
                    control.value(),
                )
            name = localized_overlay_record_name(record)
            visibility = self._visibility_description(record)
            if record.kind is OverlayKind.SIGNATURE_IMAGE:
                capability = tr(
                    "{value1} selected. Position and size are editable here. To remove it, close this editor and use Remove signature image so case-asset cleanup stays explicit. This remains a visual image, not a digital or qualified electronic signature.",
                    value1=name,
                )
            elif record.kind in {OverlayKind.HIGHLIGHT, OverlayKind.SHAPE}:
                capability = tr(
                    "{value1} selected. Position and size are editable here; its visual content properties are read-only. This is an ordinary visual mark, not permanent removal.",
                    value1=name,
                )
            elif type(payload) is TextualOverlayPayload:
                capability = tr(
                    "{value1} selected. Value, formatting, position, and size are editable.",
                    value1=name,
                )
            else:
                capability = tr(
                    "{value1} selected. Color, position, and size are editable.", value1=name
                )
            capability = f"{capability} {visibility}"
            self._selection_capability.setText(capability)
            self._selection_capability.setAccessibleDescription(capability)
            if type(payload) is TextualOverlayPayload:
                self._properties_group.setAccessibleDescription(
                    tr(
                        "Edit the selected mark's value, formatting, and exact full-page normalized box."
                    )
                )
                self._apply_properties_button.setText(tr("Apply property changes"))
                self._apply_properties_button.setAccessibleName(
                    tr("Apply selected visual-mark properties")
                )
                self._apply_properties_button.setAccessibleDescription(
                    tr(
                        "Apply the value, formatting, position, and size fields to the selected visual mark. Ctrl+Enter."
                    )
                )
            elif type(payload) is MarkOverlayPayload:
                self._properties_group.setAccessibleDescription(
                    tr("Edit the selected mark's color and exact full-page normalized box.")
                )
                self._apply_properties_button.setText(tr("Apply property changes"))
                self._apply_properties_button.setAccessibleName(
                    tr("Apply selected visual-mark properties")
                )
                self._apply_properties_button.setAccessibleDescription(
                    tr(
                        "Apply the color, position, and size fields to the selected visual mark. Ctrl+Enter."
                    )
                )
            else:
                self._properties_group.setAccessibleDescription(
                    tr(
                        "Edit only the selected mark's exact full-page normalized position and size; its visual content properties are read-only here."
                    )
                )
                self._apply_properties_button.setText(tr("Apply position and size"))
                self._apply_properties_button.setAccessibleName(
                    tr("Apply selected visual-mark position and size")
                )
                self._apply_properties_button.setAccessibleDescription(
                    tr(
                        "Apply only the exact position and size fields; visual content properties are read-only here. Ctrl+Enter."
                    )
                )
            if record.kind is OverlayKind.SIGNATURE_IMAGE:
                self._remove_button.setText(tr("Use organizer to remove signature image"))
                self._remove_button.setAccessibleName(
                    tr("Signature image removal unavailable in this editor")
                )
                self._remove_button.setAccessibleDescription(
                    tr(
                        "Close this editor and use Remove signature image so case-asset cleanup stays explicit."
                    )
                )
            elif record.kind in {OverlayKind.HIGHLIGHT, OverlayKind.SHAPE}:
                self._remove_button.setText(tr("Removal unavailable here"))
                self._remove_button.setAccessibleName(
                    tr("{value1} removal unavailable in this editor", value1=name)
                )
                self._remove_button.setAccessibleDescription(
                    tr(
                        "This ordinary visual-mark type is geometry-only in this editor and cannot be removed here."
                    )
                )
            else:
                self._remove_button.setText(tr("Remove selected"))
                self._remove_button.setAccessibleName(tr("Remove selected core visual mark"))
                self._remove_button.setAccessibleDescription(
                    tr("Remove the selected core visual mark. Delete.")
                )
        finally:
            del blockers

    def _visibility_description(self, record: OverlayRecord) -> str:
        visibility = self._canvas.visibility_for(record)
        if visibility is OverlayVisibility.VISIBLE:
            return tr("Fully shown inside the active crop.")
        if visibility is OverlayVisibility.PARTIALLY_VISIBLE:
            return tr(
                "Partially shown in the preview; saved geometry remains relative to the full page."
            )
        return tr(
            "Outside the active crop and not shown in the preview; exact full-page geometry remains available here."
        )

    @staticmethod
    def _record_action_description(record: OverlayRecord) -> str:
        if record.kind is OverlayKind.SIGNATURE_IMAGE:
            return tr(
                "Position and size are editable here. Use the organizer removal action for explicit asset cleanup; this is a visual image, not a digital signature."
            )
        if record.kind in {OverlayKind.HIGHLIGHT, OverlayKind.SHAPE}:
            return tr(
                "Position and size are editable here; visual content is read-only. This ordinary visual mark is not permanent removal."
            )
        if type(record.payload) is TextualOverlayPayload:
            return tr(
                "Value, formatting, position, and size are editable with the native property controls; dragging is not required."
            )
        return tr(
            "Color, position, and size are editable with the native property controls; dragging is not required."
        )

    def _set_text_controls_visible(self, visible: bool) -> None:
        for widget in (
            self._value_row,
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
            self._color_combo.insertItem(0, tr("Current custom color"), color)
            index = 0
        self._color_combo.setCurrentIndex(index)

    def _request_apply(self) -> None:
        if self._apply_pending or not self._commit_properties_if_needed():
            return
        self._apply_pending = True
        self._commit_started = True
        self._set_editor_enabled(False)
        self._set_status(
            tr("Applying visual overlay changes. Keep this dialog open."),
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
        self._add_text_button.setEnabled(enabled)
        self._add_checkmark_button.setEnabled(enabled)
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
            self._value_row.setEnabled(False)
            self._remove_button.setEnabled(False)
            self._undo_button.setEnabled(False)
            self._redo_button.setEnabled(False)
        self._apply_button.setEnabled(enabled)
        self._cancel_button.setEnabled(enabled and not self._commit_started)
        if self._commit_started:
            self._cancel_button.setAccessibleDescription(
                tr(
                    "Unavailable after the first apply request because changes are retained for retry."
                )
            )

    def _set_status(
        self,
        message: str,
        *,
        announce: bool,
        assertive: bool = False,
    ) -> None:
        message = tr(message)
        self._status.setText(message)
        self._status.setAccessibleName(tr("Visual overlay editor status. {value1}", value1=message))
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
            tr("PaperWorkShell could not update that visual overlay. Try the action again."),
            announce=True,
            assertive=True,
        )


def _normalized_percent(value: float) -> int:
    return int(Decimal(str(value)) * Decimal(10_000))


def _percent_text(value: int) -> str:
    return tr("{value:.4f} percent", value=Decimal(value) / 10_000)


def safe_core_overlay_failure(code: object, message: object) -> tuple[str, str]:
    """Return a code plus fixed non-disclosing form-mark recovery message."""

    del message
    if type(code) is str and code in _SAFE_CORE_FAILURE_CODES:
        return (
            code,
            tr("PaperWorkShell could not save the visual overlays safely. Try saving again."),
        )
    return (
        "PWS_UI_REQUEST_INVALID",
        tr("PaperWorkShell could not confirm that visual overlay change safely."),
    )

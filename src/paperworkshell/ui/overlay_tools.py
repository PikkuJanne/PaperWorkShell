"""Keyboard-complete Qt presentation for the five core visual-overlay tools.

The dialog edits an in-memory :class:`CoreOverlayToolsState`.  It performs no
document parsing, rendering, filesystem access, persistence, or logging.  Its
list and status surfaces deliberately describe only overlay type and geometry;
user-entered field values remain confined to the explicit value editor.
"""

from __future__ import annotations

from collections.abc import Callable
from itertools import pairwise
from typing import Final
from uuid import UUID

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import (
    QAccessible,
    QAccessibleAnnouncementEvent,
    QCloseEvent,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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
    CoreOverlayToolsState,
    KeyboardDirection,
    core_overlay_record_summary,
    core_overlay_tool_descriptor,
    is_core_overlay_announcement_safe,
)
from paperworkshell.domain.case_lifecycle import CaseLifecycleErrorCode
from paperworkshell.domain.manifest_repository import ManifestRepositoryErrorCode
from paperworkshell.domain.page_coordinates import (
    NORMALIZED_PAGE_MILLIONTHS,
    NormalizedPageBox,
    PageCoordinateError,
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
    TextualOverlayPayload,
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
    "shortcuts. Dragging is not required. Field values are available only in the value control."
)
_COLOR_CHOICES: Final = (
    ("Black", "#000000ff"),
    ("Dark blue", "#203080ff"),
    ("Green", "#008040ff"),
    ("Dark red", "#a02020ff"),
)
_TEXTUAL_KINDS: Final = frozenset({OverlayKind.TEXT, OverlayKind.DATE, OverlayKind.INITIALS})


class CoreOverlayToolsDialog(QDialog):
    """A narrow list-and-fields editor with no canvas or document I/O."""

    apply_requested = Signal(object)
    draft_changed = Signal(object)

    def __init__(
        self,
        state: CoreOverlayToolsState,
        parent: QWidget | None = None,
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

        self.setObjectName("coreOverlayToolsDialog")
        self.setWindowTitle("Edit visual form marks")
        self.setAccessibleName("Edit visual form marks for selected page")
        self.setAccessibleDescription(
            "Create and adjust text, date, checkmark, cross, and initials visual marks "
            "using a list, exact property fields, buttons, and keyboard shortcuts. "
            "No pointer is required."
        )
        self.setModal(True)
        self.resize(760, 680)

        self._content = QWidget()
        self._content.setObjectName("coreOverlayToolsContent")
        content_layout = QVBoxLayout(self._content)

        heading = QLabel("Edit visual form marks")
        heading.setObjectName("coreOverlayToolsHeading")
        heading.setAccessibleName("Edit visual form marks")
        heading.setStyleSheet("font-weight: 600;")
        content_layout.addWidget(heading)

        guidance = QLabel(
            "Add a visual mark, select it in the list, then edit its properties. Positions and "
            "sizes use the full page before crop or rotation. Alt+Arrow moves; "
            "Alt+Shift+Arrow resizes."
        )
        guidance.setObjectName("coreOverlayToolsGuidance")
        guidance.setAccessibleName("Visual form-mark editor instructions")
        guidance.setAccessibleDescription(guidance.text())
        guidance.setTextFormat(Qt.TextFormat.PlainText)
        guidance.setWordWrap(True)
        content_layout.addWidget(guidance)

        content_layout.addWidget(self._build_add_group())
        content_layout.addWidget(self._build_list_group())
        content_layout.addWidget(self._build_properties_group())
        content_layout.addLayout(self._build_edit_actions())

        self._status = QLabel(state.announcement)
        self._status.setObjectName("coreOverlayToolsStatus")
        self._status.setAccessibleName("Visual form-mark editor status")
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
        self._scroll.setAccessibleName("Visual form-mark controls")
        self._scroll.setAccessibleDescription(
            "Scrollable visual form-mark list, property fields, actions, and status."
        )
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scroll.setWidget(self._content)

        self._buttons = QDialogButtonBox()
        self._buttons.setObjectName("coreOverlayToolsDialogButtons")
        self._buttons.setAccessibleName("Apply or cancel visual form marks")
        self._apply_button = self._buttons.addButton(
            "Apply form marks",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self._apply_button.setObjectName("applyFormMarksButton")
        self._apply_button.setAccessibleName("Apply form marks")
        self._apply_button.setAccessibleDescription(
            "Request saving the complete local visual form-mark draft."
        )
        self._apply_button.setDefault(True)
        self._cancel_button = self._buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._cancel_button.setObjectName("cancelFormMarksButton")
        self._cancel_button.setAccessibleName("Cancel visual form-mark changes")
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

    def set_apply_succeeded(self) -> bool:
        """Accept the dialog after the caller confirms the pending draft was applied."""

        if not self._apply_pending:
            return False
        self._apply_pending = False
        self._set_status("Visual form marks applied.", announce=True)
        super().accept()
        return True

    def set_apply_failed(self, code: str, message: str) -> bool:
        """Restore editing after one sanitized pending apply failure."""

        if not self._apply_pending:
            return False
        selected_code, selected_message = safe_core_overlay_failure(code, message)
        self._apply_pending = False
        self._set_editor_enabled(True)
        self._apply_button.setAccessibleDescription(
            "Retry saving the retained visual form-mark draft."
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
                "These visual form-mark changes are retained for retry and cannot be "
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
        self._overlay_list.setAccessibleName("Visual form marks on selected page")
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

        self._value_label = QLabel("Value")
        self._value_label.setObjectName("coreOverlayValueLabel")
        self._value_edit = QLineEdit()
        self._value_edit.setObjectName("coreOverlayValueEdit")
        # QLineEdit counts UTF-16 code units. One extra full Python code point
        # lets the domain reject 4,097-code-point input instead of Qt silently
        # truncating it to a valid-looking 4,096-code-point value.
        self._value_edit.setMaxLength(_TEXT_INPUT_MAX_UTF16_UNITS)
        self._value_label.setBuddy(self._value_edit)
        layout.addWidget(self._value_label, 0, 0)
        layout.addWidget(self._value_edit, 0, 1, 1, 3)

        self._font_label = QLabel("Font")
        self._font_label.setObjectName("coreOverlayFontLabel")
        self._font_value = QLabel("PaperWorkShell default")
        self._font_value.setObjectName("coreOverlayFontValue")
        self._font_value.setAccessibleName("Visual form-mark font")
        self._font_value.setAccessibleDescription(
            "The approved PaperWorkShell default overlay font."
        )
        layout.addWidget(self._font_label, 1, 0)
        layout.addWidget(self._font_value, 1, 1)

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
        layout.addWidget(self._font_size_label, 1, 2)
        layout.addWidget(self._font_size, 1, 3)

        color_label = QLabel("Color")
        color_label.setObjectName("coreOverlayColorLabel")
        self._color_combo = QComboBox()
        self._color_combo.setObjectName("coreOverlayColorCombo")
        self._color_combo.setAccessibleName("Visual form-mark color")
        self._color_combo.setAccessibleDescription(
            "Choose a named visual color; color is never the only status indicator."
        )
        for name, value in _COLOR_CHOICES:
            self._color_combo.addItem(name, value)
        color_label.setBuddy(self._color_combo)
        layout.addWidget(color_label, 2, 0)
        layout.addWidget(self._color_combo, 2, 1, 1, 3)

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
            start=3,
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
        layout.addWidget(self._apply_properties_button, 7, 0, 1, 4)
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
            lambda value, target=percent: target.setText(_percent_text(value))
        )
        return control, percent

    def _build_edit_actions(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        self._remove_button = QPushButton("Remove selected")
        self._remove_button.setObjectName("removeCoreOverlayButton")
        self._remove_button.setAccessibleName("Remove selected visual form mark")
        self._remove_button.setAccessibleDescription("Remove the selected mark. Delete.")
        self._undo_button = QPushButton("Undo")
        self._undo_button.setObjectName("undoCoreOverlayButton")
        self._undo_button.setAccessibleName("Undo visual form-mark change")
        self._undo_button.setAccessibleDescription(
            "Undo the last local visual form-mark change. Ctrl+Z."
        )
        self._redo_button = QPushButton("Redo")
        self._redo_button.setObjectName("redoCoreOverlayButton")
        self._redo_button.setAccessibleName("Redo visual form-mark change")
        self._redo_button.setAccessibleDescription(
            "Redo the next local visual form-mark change. Ctrl+Y."
        )
        self._remove_button.clicked.connect(self._remove_selected)
        self._undo_button.clicked.connect(self._undo)
        self._redo_button.clicked.connect(self._redo)
        layout.addWidget(self._remove_button)
        layout.addWidget(self._undo_button)
        layout.addWidget(self._redo_button)
        layout.addStretch(1)
        return layout

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
        )
        for sequence, callback in bindings:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)

    def _set_tab_order(self) -> None:
        chain: tuple[QWidget, ...] = (
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
            self._remove_button,
            self._undo_button,
            self._redo_button,
            self._apply_button,
            self._cancel_button,
        )
        for current, following in pairwise(chain):
            QWidget.setTabOrder(current, following)

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
            color = self._color_combo.currentData()
            if type(color) is not str:
                raise ValueError
            if type(record.payload) is TextualOverlayPayload:
                state = state.edit_selected(
                    value=self._value_edit.text(),
                    font_size_points=self._font_size.value(),
                    color_value=color,
                )
            elif type(record.payload) is MarkOverlayPayload:
                state = state.edit_selected(color_value=color)
            else:
                raise ValueError
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
            else f"{core_overlay_tool_descriptor(record.kind).accessible_name} properties unchanged."
        )
        self._state = state
        self._refresh_from_state()
        self.draft_changed.emit(state)
        self._set_status(message, announce=announce)
        return True

    def _remove_selected(self) -> None:
        if self._apply_pending:
            return
        self._properties_dirty = False
        self._transition(lambda state: state.delete_selected())

    def _undo(self) -> None:
        if self._apply_pending:
            return
        focus = QApplication.focusWidget()
        if focus is self._value_edit and self._value_edit.isUndoAvailable():
            self._value_edit.undo()
            return
        self._properties_dirty = False
        self._transition(lambda state: state.undo())

    def _redo(self) -> None:
        if self._apply_pending:
            return
        focus = QApplication.focusWidget()
        if focus is self._value_edit and self._value_edit.isRedoAvailable():
            self._value_edit.redo()
            return
        self._properties_dirty = False
        self._transition(lambda state: state.redo())

    def _move(self, direction: KeyboardDirection) -> None:
        if self._apply_pending or not self._commit_properties_if_needed():
            return
        self._transition(lambda state: state.move_selected(direction))

    def _resize(self, direction: KeyboardDirection) -> None:
        if self._apply_pending or not self._commit_properties_if_needed():
            return
        self._transition(lambda state: state.resize_selected(direction))

    def _transition(
        self, operation: Callable[[CoreOverlayToolsState], CoreOverlayToolsState]
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
        self._publish_state(state)

    def _publish_state(self, state: CoreOverlayToolsState) -> None:
        self._state = state
        self._properties_dirty = False
        self._refresh_from_state()
        self.draft_changed.emit(state)
        if self._loaded_value_preserved:
            self._set_status(state.announcement, announce=True)

    def _refresh_from_state(self) -> None:
        self._loading = True
        try:
            with QSignalBlocker(self._overlay_list):
                self._overlay_list.clear()
                records = self._state.records
                for position, record in enumerate(records, start=1):
                    summary = core_overlay_record_summary(record, position, len(records))
                    item = QListWidgetItem(summary)
                    item.setData(Qt.ItemDataRole.UserRole, record.overlay_id)
                    item.setData(Qt.ItemDataRole.AccessibleTextRole, summary)
                    item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, _LIST_DESCRIPTION)
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    self._overlay_list.addItem(item)
            self._restore_list_selection()
            self._load_selected_properties()
            selected = self._state.selected_record is not None
            self._properties_group.setEnabled(selected)
            self._remove_button.setEnabled(selected)
            self._apply_properties_button.setEnabled(selected)
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
                self._value_label.setText("Value")
                self._value_edit.setAccessibleName("Visual form-mark value")
                self._value_edit.setAccessibleDescription(
                    "Select a text, date, or initials visual mark to edit its value."
                )
                self._set_text_controls_visible(False)
                return
            descriptor = core_overlay_tool_descriptor(record.kind)
            payload = record.payload
            self._set_text_controls_visible(type(payload) is TextualOverlayPayload)
            if type(payload) is TextualOverlayPayload:
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
                raise ValueError("unsupported selected payload")
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
                percent.setText(_percent_text(control.value()))
        finally:
            del blockers

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
            "Applying visual form marks. Keep this dialog open.",
            announce=True,
        )
        self.apply_requested.emit(self._state)

    def _set_editor_enabled(self, enabled: bool) -> None:
        self._tool_combo.setEnabled(enabled)
        self._add_button.setEnabled(enabled)
        self._overlay_list.setEnabled(enabled)
        if enabled:
            self._refresh_from_state()
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
            "PaperWorkShell could not update that visual form mark. Try the action again.",
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
            "PaperWorkShell could not save the visual form marks safely. Try saving again.",
        )
    return (
        "PWS_UI_REQUEST_INVALID",
        "PaperWorkShell could not confirm that visual form-mark change safely.",
    )

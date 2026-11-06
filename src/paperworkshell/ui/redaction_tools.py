"""Accessible editor for saved, unapplied removal regions; no processing authority."""

from __future__ import annotations

from collections.abc import Callable
from itertools import pairwise
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
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from paperworkshell.app.redaction_tools import (
    MAX_REDACTION_EDIT_STEPS,
    RedactionToolsState,
    is_redaction_announcement_safe,
)
from paperworkshell.domain.case_lifecycle import CaseLifecycleErrorCode
from paperworkshell.domain.manifest_repository import ManifestRepositoryErrorCode
from paperworkshell.domain.page_coordinates import (
    NormalizedPageBox,
    PageCoordinateError,
    PageCoordinateTransform,
    RenderedImageSize,
)
from paperworkshell.domain.redaction import RedactionError, RedactionErrorCode
from paperworkshell.domain.redaction_verification import runtime_release_qualified
from paperworkshell.localization.catalog import mark, tr
from paperworkshell.ui.localization_layout import adapt_localized_content
from paperworkshell.ui.presentation import adapt_dialog_button_box
from paperworkshell.ui.redaction_canvas import RedactionIntentCanvas

_NOT_APPLIED = mark("Unapplied — information is still present. No removal has been verified.")
_EDIT_LIMIT_STATUS = mark(
    "The edit limit has been reached. Save the current marks, Undo an edit to continue, or Cancel this draft. Information is still present."
)
_SAVE_FAILURE = mark(
    "Removal marks could not be saved. Information is still present. Changes are retained for retry."
)
_SAFE_FAILURE_CODES = frozenset(
    {code.value for code in RedactionErrorCode}
    | {code.value for code in ManifestRepositoryErrorCode}
    | {code.value for code in CaseLifecycleErrorCode}
    | {"PWS_UI_UNEXPECTED", "PWS_UI_RESPONSE_INVALID", "PWS_UI_REQUEST_INVALID", "PWS_UI_NOT_READY"}
)


def safe_redaction_failure(code: object, message: object) -> tuple[str, str]:
    """Do not reflect backend exception text, paths, or content into UI announcements."""
    del message
    selected = code if type(code) is str and code in _SAFE_FAILURE_CODES else "PWS_UI_UNEXPECTED"
    return selected, tr(_SAVE_FAILURE)


class RedactionToolsDialog(QDialog):
    """Native region list, exact full-page geometry, and explicitly unapplied preview."""

    save_requested = Signal(object)
    draft_changed = Signal(object)

    def __init__(
        self,
        state: RedactionToolsState,
        parent: QWidget | None = None,
        *,
        page_transform: PageCoordinateTransform | None = None,
        page_preview: QPixmap | None = None,
        page_display_size: RenderedImageSize | None = None,
        pointer_mapping_enabled: bool = True,
    ) -> None:
        if type(state) is not RedactionToolsState or not is_redaction_announcement_safe(
            state.announcement
        ):
            raise ValueError("invalid removal editor state")
        super().__init__(parent)
        self._state = state
        self._save_pending = False
        self._commit_started = False
        self._properties_dirty = False
        self._loading = False
        self._listed_records: tuple[object, ...] | None = None
        self._listed_rows: dict[UUID, int] = {}
        self._shortcuts: list[QShortcut] = []
        self._transform = page_transform or PageCoordinateTransform()
        qualified = runtime_release_qualified()
        self.setObjectName("redactionToolsDialog")
        self.setWindowTitle(tr("Permanently remove information — mark regions"))
        self.setAccessibleName(tr("Permanently remove information: unapplied region review"))
        self.setAccessibleDescription(
            tr(
                "Save removal intent using the region list and numeric fields. Information remains present. Use Export removal derivative for a separately verified output."
            )
            if qualified
            else tr(
                "Save removal intent using the region list and numeric fields. Information remains present. Permanent removal is disabled pending independent security approval."
            )
        )
        self.setModal(True)
        available = self.screen().availableGeometry()
        self.resize(
            min(900, max(320, available.width() - 40)), min(760, max(240, available.height() - 80))
        )
        self._content = QWidget()
        content = QVBoxLayout(self._content)
        for name, text in (
            (
                "redactionIntentWarning",
                tr(_NOT_APPLIED)
                + (
                    tr(" Save marks only; use Export removal derivative to create a checked copy.")
                    if qualified
                    else tr(" Save marks only; permanent removal is not enabled.")
                ),
            ),
            (
                "redactionDerivativeWarning",
                tr(
                    "Applying removal later creates a new derivative. Affected pages may be rasterized; interactive forms and accessibility structure may be lost. A modified digitally signed source produces an unsigned derivative. Originals stay unchanged."
                ),
            ),
            (
                "redactionInstructions",
                tr(
                    "Add a region, enter left, top, width and height in full-page millionths before crop or rotation, then update it. 1000000 equals 100 percent. Select any region with the arrow keys. Dashed crossed outlines mean unapplied intent. Ordinary shapes remain separate visual marks. Review every occurrence on every affected page."
                ),
            ),
            (
                "redactionStateLegend",
                tr(
                    "Status meanings: Unapplied: information is still present. Applied: candidate created, removal unverified. Verified: required checks passed for the exact derivative. Failed: checks failed; export blocked. Applied and verified states describe derivative results; saved marks always remain unapplied."
                )
                if qualified
                else tr(
                    "Status meanings: Unapplied: information is still present. Applied: candidate created, removal unverified. Verified: required checks passed for the exact derivative. Failed: checks failed; export blocked. Applied and verified states are unavailable while the security gate is closed."
                ),
            ),
        ):
            label = QLabel(tr(text))
            label.setObjectName(name)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setAccessibleName(tr(text))
            label.setAccessibleDescription(tr(text))
            content.addWidget(label)

        self._canvas = RedactionIntentCanvas(
            transform=self._transform,
            preview=page_preview,
            display_size=page_display_size,
            pointer_mapping_enabled=pointer_mapping_enabled,
        )
        content.addWidget(self._canvas)
        self._canvas.region_requested.connect(self._add_pointer_region)
        self._list = QListWidget()
        self._list.setObjectName("redactionRegionList")
        self._list.setAccessibleName(tr("Unapplied removal regions"))
        self._list.setAccessibleDescription(
            tr(
                "Select a region using Up and Down, then edit exact geometry. Every region is unapplied; information is still present."
            )
        )
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setMinimumHeight(100)
        self._list.currentRowChanged.connect(self._selection_changed)
        content.addWidget(self._list)

        self._properties = QGroupBox(tr("Exact full-page region geometry"))
        self._properties.setAccessibleName(tr("Exact full-page region geometry"))
        grid = QGridLayout(self._properties)
        self._fields: list[QSpinBox] = []
        for row, (label_text, key) in enumerate(
            ((tr("Left"), "x"), (tr("Top"), "y"), (tr("Width"), "width"), (tr("Height"), "height"))
        ):
            field = QSpinBox()
            field.setObjectName(
                {
                    "x": "redactionLeftSpin",
                    "y": "redactionTopSpin",
                    "width": "redactionWidthSpin",
                    "height": "redactionHeightSpin",
                }[key]
            )
            field.setRange(1 if key in {"width", "height"} else 0, 1_000_000)
            field.setSingleStep(1_000)
            field.setAccessibleName(
                tr("Removal region {value1} in millionths", value1=label_text.lower())
            )
            field.setAccessibleDescription(
                tr(
                    "Full-page units before crop and rotation. 1000000 equals 100 percent. Use Update region to save the geometry in this draft."
                )
            )
            field.setKeyboardTracking(False)
            field.valueChanged.connect(self._field_changed)
            label = QLabel(tr(label_text))
            label.setBuddy(field)
            grid.addWidget(label, row, 0)
            grid.addWidget(field, row, 1)
            self._fields.append(field)
        self._update_button = self._button(
            tr("Update region"),
            "updateRedactionRegionButton",
            tr("Update selected region from all four numeric fields."),
            self._update_region,
        )
        grid.addWidget(self._update_button, 4, 0, 1, 2)
        content.addWidget(self._properties)
        actions = QVBoxLayout()
        self._add_button = self._button(
            tr("Add region"),
            "addRedactionRegionButton",
            tr(
                "Add an unapplied region with editable position and size. No information is removed. Alt+A."
            ),
            lambda: self._transition(lambda state: state.create()),
        )
        self._remove_button = self._button(
            tr("Remove mark"),
            "removeRedactionRegionButton",
            tr("Remove only the selected removal intent. Source information stays present."),
            lambda: self._transition(lambda state: state.delete_selected()),
        )
        self._undo_button = self._button(
            tr("Undo"),
            "undoRedactionRegionButton",
            tr("Undo last region edit. Ctrl+Z."),
            lambda: self._transition(lambda state: state.undo()),
        )
        self._redo_button = self._button(
            tr("Redo"),
            "redoRedactionRegionButton",
            tr("Redo region edit. Ctrl+Y."),
            lambda: self._transition(lambda state: state.redo()),
        )
        for button in (self._add_button, self._remove_button, self._undo_button, self._redo_button):
            actions.addWidget(button)
        content.addLayout(actions)
        self._status = QLabel(tr(_NOT_APPLIED))
        self._status.setObjectName("redactionToolsStatus")
        self._status.setAccessibleName(
            tr("Removal review status. {value1}", value1=tr(_NOT_APPLIED))
        )
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setWordWrap(True)
        self._status.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._status.setStyleSheet(
            "QLabel {border: 2px solid transparent; padding: 4px;} "
            "QLabel:focus {border-color: palette(highlight);}"
        )
        content.addWidget(self._status)
        content.addStretch(1)
        self._scroll = QScrollArea()
        self._scroll.setObjectName("redactionToolsScroll")
        self._scroll.setAccessibleName(tr("Removal review controls"))
        self._scroll.setAccessibleDescription(
            tr("Scrollable instructions, preview, region list and exact controls.")
        )
        self._scroll.setWidgetResizable(True)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._scroll.setWidget(self._content)
        adapt_localized_content(self._scroll)
        buttons = QDialogButtonBox()
        self._save_button = buttons.addButton(
            tr("Save unapplied marks"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._save_button.setObjectName("saveRedactionMarksButton")
        self._save_button.setAccessibleName(tr("Save unapplied removal marks"))
        self._save_button.setAccessibleDescription(
            tr("Save intent only. Information is still present; no removal is applied or verified.")
        )
        self._save_button.setDefault(True)
        self._save_button.clicked.connect(self._request_save)
        self._cancel_button = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._cancel_button.setObjectName("cancelRedactionMarksButton")
        self._cancel_button.setAccessibleName(tr("Cancel removal mark changes"))
        self._cancel_button.setAccessibleDescription(
            tr("Discard this local draft and return to the organizer. Escape.")
        )
        self._cancel_button.clicked.connect(self.reject)
        adapt_dialog_button_box(buttons)
        layout = QVBoxLayout(self)
        layout.addWidget(self._scroll, 1)
        layout.addWidget(buttons)
        self._focus_order: list[QWidget] = [
            self._list,
            *self._fields,
            self._update_button,
            self._add_button,
            self._remove_button,
            self._undo_button,
            self._redo_button,
            self._status,
            self._save_button,
            self._cancel_button,
        ]
        for first, second in pairwise(self._focus_order):
            QWidget.setTabOrder(first, second)
        for key, callback in (
            ("Alt+A", self._add_button.click),
            ("Ctrl+Z", self._undo_button.click),
            ("Ctrl+Y", self._redo_button.click),
        ):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)
        self._refresh()
        if self._edit_limit_reached():
            self._set_status(tr(_EDIT_LIMIT_STATUS))
        QTimer.singleShot(0, self._initial_focus)

    @staticmethod
    def _button(
        text: str, name: str, description: str, callback: Callable[[], object]
    ) -> QPushButton:
        button = QPushButton(tr(text))
        button.setObjectName(name)
        button.setAccessibleName(tr(text))
        button.setAccessibleDescription(tr(description))
        button.clicked.connect(callback)
        return button

    @property
    def state(self) -> RedactionToolsState:
        return self._state

    @property
    def save_pending(self) -> bool:
        return self._save_pending

    @property
    def commit_started(self) -> bool:
        return self._commit_started

    def _initial_focus(self) -> None:
        target = (
            self._undo_button
            if self._edit_limit_reached()
            else self._list
            if self._state.selected_record is not None
            else self._add_button
        )
        target.setFocus(Qt.FocusReason.OtherFocusReason)
        self._scroll.ensureWidgetVisible(target)

    def _field_changed(self, _value: int) -> None:
        if not self._loading:
            self._properties_dirty = True

    def _edit_limit_reached(self) -> bool:
        return self._state.cursor >= MAX_REDACTION_EDIT_STEPS

    def _capacity_status(self) -> str:
        if self._edit_limit_reached():
            if self._commit_started:
                return tr(
                    "The edit limit has been reached. Retry saving the current marks, or Undo an edit to continue. Information is still present."
                )
            return tr(_EDIT_LIMIT_STATUS)
        if self._commit_started:
            return tr(
                "The case has reached the supported removal-mark limit. Remove a mark or retry saving the current marks. Information is still present."
            )
        return tr(
            "The case has reached the supported removal-mark limit. Remove a mark, save the current marks, or cancel. Information is still present."
        )

    def _update_region(self) -> bool:
        if self._save_pending:
            return False
        if self._state.selected_record is None:
            return True
        # Keyboard tracking is off: visible text may be newer than value(),
        # including when shutdown collects a draft without moving focus first.
        for field in self._fields:
            if not field.hasAcceptableInput():
                self._set_status(tr("Enter a complete numeric value for this region field."), True)
                field.setFocus(Qt.FocusReason.OtherFocusReason)
                self._scroll.ensureWidgetVisible(field)
                return False
            field.interpretText()
        if not self._properties_dirty:
            return True
        try:
            left, top, width, height = (field.value() for field in self._fields)
            box = NormalizedPageBox(left, top, width, height)
            state = self._state.replace_selected_box(box)
        except PageCoordinateError:
            self._set_status(
                tr(
                    "Region must have a positive width and height and fit inside the full page. Correct the numeric fields."
                ),
                True,
            )
            self._fields[0].setFocus(Qt.FocusReason.OtherFocusReason)
            self._scroll.ensureWidgetVisible(self._fields[0])
            return False
        except RedactionError as error:
            if error.code is RedactionErrorCode.CAPACITY_EXCEEDED:
                # Disabled fields normally prevent this path. Restore the last
                # accepted values if a queued/direct signal arrives at the limit.
                self._properties_dirty = False
                self._refresh()
                self._set_status(self._capacity_status(), True)
                self._undo_button.setFocus(Qt.FocusReason.OtherFocusReason)
                self._scroll.ensureWidgetVisible(self._undo_button)
            else:
                self._set_status(
                    tr(
                        "The removal mark edit was rejected. Information is still present. Review the selected region and try again."
                    ),
                    True,
                )
                self._status.setFocus(Qt.FocusReason.OtherFocusReason)
            return False
        self._publish(state)
        return True

    def _transition(self, operation: Callable[[RedactionToolsState], RedactionToolsState]) -> None:
        if self._save_pending or not self._update_region():
            return
        try:
            state = operation(self._state)
        except RedactionError as error:
            self._set_status(
                self._capacity_status()
                if error.code is RedactionErrorCode.CAPACITY_EXCEEDED
                else tr(
                    "The removal mark change was rejected. Information is still present. Review the selected region."
                ),
                True,
            )
            return
        except PageCoordinateError:
            self._set_status(
                tr(
                    "Region must have a positive width and height and fit inside the full page. Correct the numeric fields."
                ),
                True,
            )
            return
        self._publish(state)

    def _publish(self, state: RedactionToolsState) -> None:
        if type(state) is not RedactionToolsState or not is_redaction_announcement_safe(
            state.announcement
        ):
            self._set_status(
                tr("The removal editor returned an invalid state. Information is still present."),
                True,
            )
            return
        previously_limited = self._edit_limit_reached()
        self._state = state
        self._properties_dirty = False
        self._refresh()
        self.draft_changed.emit(state)
        limited = self._edit_limit_reached()
        self._set_status(
            self._capacity_status()
            if limited
            else tr(
                "{announcement} Information is still present.", announcement=tr(state.announcement)
            )
        )
        if limited and not previously_limited:
            self._undo_button.setFocus(Qt.FocusReason.OtherFocusReason)
            self._scroll.ensureWidgetVisible(self._undo_button)

    def _add_pointer_region(self, box: object) -> None:
        if type(box) is NormalizedPageBox and not self._edit_limit_reached():
            self._transition(lambda state: state.create(box))

    def _selection_changed(self, row: int) -> None:
        if self._loading or row < 0 or self._save_pending:
            return
        mark_id = self._list.item(row).data(Qt.ItemDataRole.UserRole)
        if type(mark_id) is UUID:
            if not self._update_region():
                self._restore_list_selection()
                return
            self._transition(lambda state: state.select(mark_id))

    def _restore_list_selection(self) -> None:
        selected_row = next(
            (
                row
                for row in range(self._list.count())
                if self._list.item(row).data(Qt.ItemDataRole.UserRole)
                == self._state.selected_mark_id
            ),
            -1,
        )
        with QSignalBlocker(self._list):
            self._list.setCurrentRow(selected_row)

    def _refresh(self) -> None:
        records = self._state.records
        selected = self._state.selected_record
        editing_enabled = not self._edit_limit_reached()
        self._loading = True
        try:
            with QSignalBlocker(self._list):
                if records != self._listed_records:
                    self._list.clear()
                    self._listed_rows.clear()
                    for position, record in enumerate(records, start=1):
                        box = record.box
                        visible = self._transform.visible_part(box)
                        visibility = (
                            tr("outside active crop")
                            if visible is None
                            else (
                                tr("fully shown in active crop")
                                if visible == box
                                else tr("partly shown in active crop")
                            )
                        )
                        summary = tr(
                            "Unapplied removal region {value1} of {value2}; left {value3:g} percent; top {value4:g} percent; width {value5:g} percent; height {value6:g} percent; {value7}. Information is still present.",
                            value1=position,
                            value2=len(records),
                            value3=box.x / 10000,
                            value4=box.y / 10000,
                            value5=box.width / 10000,
                            value6=box.height / 10000,
                            value7=visibility,
                        )
                        item = QListWidgetItem(summary)
                        item.setData(Qt.ItemDataRole.UserRole, record.mark_id)
                        item.setData(Qt.ItemDataRole.AccessibleTextRole, summary)
                        item.setData(
                            Qt.ItemDataRole.AccessibleDescriptionRole,
                            tr(
                                "Unapplied intent. Select and edit numeric fields; no removal verified."
                            ),
                        )
                        self._list.addItem(item)
                        self._listed_rows[record.mark_id] = position - 1
                    self._listed_records = records
                self._list.setCurrentRow(
                    -1 if selected is None else self._listed_rows[selected.mark_id]
                )
            self._properties.setEnabled(selected is not None and editing_enabled)
            if selected is not None:
                for field, value in zip(
                    self._fields,
                    (selected.box.x, selected.box.y, selected.box.width, selected.box.height),
                    strict=True,
                ):
                    field.setValue(value)
            self._add_button.setEnabled(editing_enabled)
            self._remove_button.setEnabled(selected is not None and editing_enabled)
            self._canvas.setEnabled(editing_enabled)
            self._undo_button.setEnabled(self._state.can_undo)
            self._redo_button.setEnabled(self._state.can_redo)
            self._canvas.set_records(records, None if selected is None else selected.mark_id)
            self._status.setAccessibleDescription(self._status.text())
        finally:
            self._loading = False

    def _set_status(self, text: str, assertive: bool = False) -> None:
        text = tr(text)
        self._status.setText(text)
        self._status.setAccessibleName(tr("Removal mark editor status. {value1}", value1=text))
        self._status.setAccessibleDescription(tr(text))
        self._scroll.ensureWidgetVisible(self._status)
        # The native dialog is a stable, visible UIA source even when scrolling
        # has just exposed the child status. Keep the user's keyboard focus.
        event = QAccessibleAnnouncementEvent(self, text)
        if assertive:
            event.setPoliteness(QAccessible.AnnouncementPoliteness.Assertive)
        QAccessible.updateAccessibility(event)

    def _request_save(self) -> None:
        if self._save_pending or not self._update_region():
            return
        self.set_save_pending()
        self.save_requested.emit(self._state)

    def set_save_pending(self) -> None:
        self._save_pending = self._commit_started = True
        for control in (
            self._canvas,
            self._list,
            self._properties,
            self._add_button,
            self._remove_button,
            self._undo_button,
            self._redo_button,
        ):
            control.setEnabled(False)
        self._cancel_button.setEnabled(False)
        self._save_button.setEnabled(False)
        self._set_status(tr("Saving unapplied removal marks. Information is still present."))
        self._status.setFocus(Qt.FocusReason.OtherFocusReason)

    def save_failed(self, code: object, message: object) -> bool:
        if not self._save_pending:
            return False
        self._save_pending = False
        self._list.setEnabled(True)
        self._refresh()
        self._cancel_button.setEnabled(not self._commit_started)
        self._save_button.setEnabled(True)
        safe_code, safe_message = safe_redaction_failure(code, message)
        message_text = f"{safe_code}: {safe_message}"
        if self._edit_limit_reached():
            message_text += f" {self._capacity_status()}"
        self._set_status(message_text, True)
        self._save_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._scroll.ensureWidgetVisible(self._save_button, 8, 8)
        return True

    def finish_saved(self) -> bool:
        if not self._save_pending:
            return False
        self._save_pending = False
        self.accept()
        return True

    def latest_state_for_shutdown(self) -> RedactionToolsState | None:
        return self._state if self._update_region() else None

    def reject(self) -> None:
        if self._commit_started:
            self._set_status(
                tr(
                    "Removal mark changes are retained for retry and cannot be discarded after saving has started. Retry saving. Information is still present."
                ),
                True,
            )
            if not self._save_pending:
                self._status.setFocus()
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._commit_started:
            event.ignore()
            self.reject()
        else:
            super().closeEvent(event)

    def focusNextPrevChild(self, forward: bool) -> bool:  # noqa: FBT001, N802
        order = [
            widget for widget in self._focus_order if widget.isEnabled() and widget.isVisible()
        ]
        current = QApplication.focusWidget()
        if current in order:
            target = order[(order.index(current) + (1 if forward else -1)) % len(order)]
            target.setFocus(
                Qt.FocusReason.TabFocusReason if forward else Qt.FocusReason.BacktabFocusReason
            )
            self._scroll.ensureWidgetVisible(target)
            return True
        return super().focusNextPrevChild(forward)

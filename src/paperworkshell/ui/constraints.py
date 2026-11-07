"""Accessible modal editor for detached portal packaging constraints."""

from __future__ import annotations

from itertools import pairwise
from typing import Final

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QAccessible,
    QAccessibleAnnouncementEvent,
    QCloseEvent,
    QFont,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
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

from paperworkshell.app.portal_constraints import (
    ConstraintAnnouncement,
    ConstraintDraftInput,
    ConstraintEditorError,
    ConstraintEditorErrorCode,
    ConstraintEditorState,
    ConstraintValidationField,
    preview_output_names,
    reject_constraint_editor,
)
from paperworkshell.domain.manifest_validation import MAX_OUTPUT_NAME_UTF16_CODE_UNITS
from paperworkshell.localization.catalog import mark, tr
from paperworkshell.preflight.portal_constraints import (
    PortalEncryptionPolicy,
    PortalOutputMode,
)
from paperworkshell.ui.localization_layout import adapt_localized_content
from paperworkshell.ui.presentation import WrappingActionLayout, adapt_dialog_button_box
from paperworkshell.ui.wheel_safe import (
    WheelSafeComboBox as QComboBox,
)

_FOCUS_VISIBLE_TEXT_STYLE: Final = (
    "QLabel:focus { border: 2px solid palette(highlight); padding: 3px; }"
)
_MODE_TEXT: Final[dict[PortalOutputMode, str]] = {
    PortalOutputMode.COMBINED_PDF: mark("One combined PDF"),
    PortalOutputMode.SEPARATE_PDFS: mark("Several separate PDFs"),
    PortalOutputMode.ZIP: mark("ZIP package"),
}
_ENCRYPTION_TEXT: Final[dict[PortalEncryptionPolicy, str]] = {
    PortalEncryptionPolicy.MUST_BE_UNENCRYPTED: mark("Outputs must be unencrypted"),
    PortalEncryptionPolicy.ALLOWED: mark("Encrypted outputs are allowed"),
}


class ConstraintEditorDialog(QDialog):
    """Scrollable modal with a complete keyboard path and safe Apply lifecycle."""

    apply_requested = Signal(object)
    state_changed = Signal(object)

    def __init__(self, state: ConstraintEditorState, parent: QWidget | None = None) -> None:
        if type(state) is not ConstraintEditorState:
            raise ValueError("state must be a ConstraintEditorState")
        super().__init__(parent)
        self._state = state
        self._apply_pending = False
        self._commit_started = False
        self._refreshing = False
        self._name_stems: list[str] = []

        self.setObjectName("portalConstraintEditorDialog")
        self.setModal(True)
        self.setWindowTitle(tr("Review portal packaging constraints"))
        self.setAccessibleName(tr("Review portal packaging constraints"))
        self.setAccessibleDescription(
            tr(
                "Review allowed packaging formats, encryption policy, optional numeric limits, and safe requested names. These local checks do not guarantee portal acceptance. Cancel is the safe default."
            )
        )

        content = QWidget()
        content.setObjectName("constraintEditorContent")
        content_layout = QVBoxLayout(content)

        heading = QLabel(tr("Review portal packaging constraints"))
        heading.setObjectName("constraintEditorHeading")
        heading.setAccessibleName(tr("Review portal packaging constraints"))
        heading.setTextFormat(Qt.TextFormat.PlainText)
        heading.setWordWrap(True)
        heading_font = QFont(heading.font())
        heading_font.setBold(True)
        heading_font.setPointSizeF(max(heading_font.pointSizeF(), 14.0))
        heading.setFont(heading_font)
        content_layout.addWidget(heading)

        guidance = QLabel(
            tr(
                "These values control packaging checks performed by PaperWorkShell. Compare them with the current application instructions. Passing these checks does not confirm eligibility, legal sufficiency, submission, or acceptance by a portal."
            )
        )
        guidance.setObjectName("constraintEditorGuidance")
        guidance.setAccessibleName(tr("Constraint review guidance"))
        guidance.setAccessibleDescription(guidance.text())
        guidance.setTextFormat(Qt.TextFormat.PlainText)
        guidance.setWordWrap(True)
        content_layout.addWidget(guidance)

        source_group = QGroupBox(tr("Constraint source"))
        source_group.setObjectName("constraintSourceGroup")
        source_group.setAccessibleName(tr("Constraint source and provenance"))
        source_layout = QVBoxLayout(source_group)
        self._template_title = QLabel()
        self._template_title.setObjectName("constraintTemplateTitle")
        self._template_title.setAccessibleName(tr("Generic template title"))
        self._template_title.setTextFormat(Qt.TextFormat.PlainText)
        self._template_title.setWordWrap(True)
        source_layout.addWidget(self._template_title)
        self._source_status = QLabel()
        self._source_status.setObjectName("constraintSourceStatus")
        self._source_status.setAccessibleName(tr("Current constraint source"))
        self._source_status.setTextFormat(Qt.TextFormat.PlainText)
        self._source_status.setWordWrap(True)
        self._source_status.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._source_status.setFrameShape(QFrame.Shape.StyledPanel)
        self._source_status.setStyleSheet(_FOCUS_VISIBLE_TEXT_STYLE)
        source_layout.addWidget(self._source_status)
        self._restore_button = self._button(
            tr("Restore generic template defaults"),
            "constraintRestoreDefaultsButton",
            tr(
                "Copy the configured generic template's packaging defaults into this local draft. They still require review and are not saved until Apply succeeds."
            ),
        )
        self._restore_button.clicked.connect(self._restore_defaults)
        source_layout.addWidget(self._restore_button)
        content_layout.addWidget(source_group)

        mode_group = QGroupBox(tr("Allowed output formats"))
        mode_group.setObjectName("constraintOutputModesGroup")
        mode_group.setAccessibleName(tr("Allowed output formats"))
        mode_group.setAccessibleDescription(
            tr(
                "Select one or more formats that a later export plan may use. This editor does not select the actual export organization."
            )
        )
        mode_layout = QVBoxLayout(mode_group)
        self._mode_checks: dict[PortalOutputMode, QCheckBox] = {}
        for mode, label in _MODE_TEXT.items():
            check = QCheckBox(tr(label))
            check.setObjectName(
                {
                    PortalOutputMode.COMBINED_PDF: "constraintCombinedPdfCheck",
                    PortalOutputMode.SEPARATE_PDFS: "constraintSeparatePdfsCheck",
                    PortalOutputMode.ZIP: "constraintZipCheck",
                }[mode]
            )
            check.setAccessibleName(tr(label))
            check.setAccessibleDescription(
                tr("Allow {value1} as a future export organization.", value1=tr(label).casefold())
            )
            check.toggled.connect(self._allowed_modes_changed)
            self._mode_checks[mode] = check
            mode_layout.addWidget(check)
        content_layout.addWidget(mode_group)

        encryption_group = QGroupBox(tr("Encryption policy"))
        encryption_group.setObjectName("constraintEncryptionGroup")
        encryption_layout = QVBoxLayout(encryption_group)
        encryption_label = QLabel(tr("Output &encryption policy"))
        self._encryption = QComboBox()
        self._encryption.setObjectName("constraintEncryptionCombo")
        self._encryption.setAccessibleName(tr("Output encryption policy"))
        self._encryption.setAccessibleDescription(
            tr("Choose whether planned outputs must be unencrypted or may be encrypted.")
        )
        for policy, label in _ENCRYPTION_TEXT.items():
            self._encryption.addItem(tr(label), policy.value)
        encryption_label.setBuddy(self._encryption)
        encryption_layout.addWidget(encryption_label)
        encryption_layout.addWidget(self._encryption)
        content_layout.addWidget(encryption_group)

        limit_group = QGroupBox(tr("Optional limits"))
        limit_group.setObjectName("constraintLimitsGroup")
        limit_group.setAccessibleName(tr("Optional packaging limits"))
        limit_group.setAccessibleDescription(
            tr(
                "Each limit has an explicit Set checkbox. Unchecked means no configured limit; zero is never used as a substitute for no limit. Byte values are exact integers."
            )
        )
        limit_layout = QVBoxLayout(limit_group)
        self._limit_controls: dict[ConstraintValidationField, tuple[QCheckBox, QLineEdit]] = {}
        for validation_field, label, object_token, description in (
            (
                ConstraintValidationField.MAXIMUM_FILE_COUNT,
                tr("Maximum file count"),
                "FileCount",
                tr("Maximum number of files. Leave Set unchecked for no configured limit."),
            ),
            (
                ConstraintValidationField.MAXIMUM_TOTAL_BYTES,
                tr("Maximum total bytes"),
                "TotalBytes",
                tr(
                    "Maximum total output size in exact bytes. Leave Set unchecked for no configured limit."
                ),
            ),
            (
                ConstraintValidationField.MAXIMUM_FILE_BYTES,
                tr("Maximum bytes per file"),
                "FileBytes",
                tr(
                    "Maximum size of each output file in exact bytes. Leave Set unchecked for no configured limit."
                ),
            ),
            (
                ConstraintValidationField.MAXIMUM_PAGES_PER_FILE,
                tr("Maximum pages per file"),
                "PagesPerFile",
                tr(
                    "Maximum pages in each output file. Leave Set unchecked for no configured limit."
                ),
            ),
        ):
            row = QVBoxLayout()
            enabled = QCheckBox(tr("Set {value1}", value1=tr(label).casefold()))
            enabled.setObjectName(f"constraint{object_token}Enabled")
            enabled.setAccessibleName(tr("Set {value1}", value1=tr(label).casefold()))
            enabled.setAccessibleDescription(tr(description))
            value_label = QLabel(f"&{label}")
            value = QLineEdit()
            value.setObjectName(f"constraint{object_token}Input")
            value.setAccessibleName(tr(label))
            value.setAccessibleDescription(tr(description))
            value.setInputMethodHints(Qt.InputMethodHint.ImhDigitsOnly)
            value.setMaxLength(16)
            value_label.setBuddy(value)
            enabled.toggled.connect(value.setEnabled)
            row.addWidget(enabled)
            row.addWidget(value_label)
            row.addWidget(value, 1)
            limit_layout.addLayout(row)
            self._limit_controls[validation_field] = (enabled, value)
        content_layout.addWidget(limit_group)

        name_group = QGroupBox(tr("Requested output names"))
        name_group.setObjectName("constraintNamesGroup")
        name_group.setAccessibleName(tr("Requested output names"))
        name_group.setAccessibleDescription(
            tr(
                "Names are safe final filename components only, never paths. Enter a stem; the PDF or ZIP extension is derived and read-only. Actual output organization is selected later in the export workflow."
            )
        )
        name_layout = QVBoxLayout(name_group)
        name_guidance = QLabel(
            tr(
                "Enter filename stems only. PaperWorkShell derives the displayed extension and adds understandable counters for collisions. Folder paths, archive paths, device names, and unsafe characters are rejected."
            )
        )
        name_guidance.setObjectName("constraintNameGuidance")
        name_guidance.setAccessibleName(tr("Requested filename instructions"))
        name_guidance.setAccessibleDescription(name_guidance.text())
        name_guidance.setTextFormat(Qt.TextFormat.PlainText)
        name_guidance.setWordWrap(True)
        name_layout.addWidget(name_guidance)
        list_label = QLabel(tr("Requested &names"))
        self._name_list = QListWidget()
        self._name_list.setObjectName("constraintNameList")
        self._name_list.setAccessibleName(tr("Ordered requested output names"))
        self._name_list.setAccessibleDescription(
            tr("Select one safe resolved filename to update, remove, or move using named buttons.")
        )
        self._name_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        list_label.setBuddy(self._name_list)
        name_layout.addWidget(list_label)
        name_layout.addWidget(self._name_list)
        editor_row = QVBoxLayout()
        stem_label = QLabel(tr("Filename &stem"))
        self._name_stem = QLineEdit()
        self._name_stem.setObjectName("constraintNameStemInput")
        self._name_stem.setAccessibleName(tr("Requested filename stem"))
        self._name_stem.setAccessibleDescription(
            tr("Enter one filename stem without a path or extension.")
        )
        self._name_stem.setMaxLength(MAX_OUTPUT_NAME_UTF16_CODE_UNITS)
        stem_label.setBuddy(self._name_stem)
        extension_label = QLabel(tr("Derived &extension"))
        self._name_extension = QLineEdit()
        self._name_extension.setObjectName("constraintNameExtension")
        self._name_extension.setAccessibleName(tr("Derived read-only filename extension"))
        self._name_extension.setAccessibleDescription(
            tr("Read-only extension preview. It does not select the future export organization.")
        )
        self._name_extension.setReadOnly(True)
        extension_label.setBuddy(self._name_extension)
        editor_row.addWidget(stem_label)
        editor_row.addWidget(self._name_stem, 1)
        editor_row.addWidget(extension_label)
        editor_row.addWidget(self._name_extension)
        name_layout.addLayout(editor_row)
        name_actions = WrappingActionLayout()
        self._name_add = self._button(
            tr("Add requested name"),
            "constraintNameAddButton",
            tr("Validate and add the entered stem to the local ordered name form."),
        )
        self._name_update = self._button(
            tr("Update selected name"),
            "constraintNameUpdateButton",
            tr("Validate the entered stem and replace the selected local name."),
        )
        self._name_remove = self._button(
            tr("Remove selected name"),
            "constraintNameRemoveButton",
            tr("Remove the selected requested name from the local form."),
        )
        self._name_earlier = self._button(
            tr("Move name earlier"),
            "constraintNameEarlierButton",
            tr("Move the selected requested name one position earlier."),
        )
        self._name_later = self._button(
            tr("Move name later"),
            "constraintNameLaterButton",
            tr("Move the selected requested name one position later."),
        )
        self._name_add.clicked.connect(self._add_name)
        self._name_update.clicked.connect(self._update_name)
        self._name_remove.clicked.connect(self._remove_name)
        self._name_earlier.clicked.connect(lambda: self._move_name(-1))
        self._name_later.clicked.connect(lambda: self._move_name(1))
        for button in (
            self._name_add,
            self._name_update,
            self._name_remove,
            self._name_earlier,
            self._name_later,
        ):
            name_actions.addWidget(button)
        name_layout.addLayout(name_actions)
        content_layout.addWidget(name_group)

        self._status = QLabel(tr(state.announcement))
        self._status.setObjectName("constraintEditorStatus")
        self._status.setAccessibleName(tr("Constraint editor status"))
        self._status.setAccessibleDescription(tr(state.announcement))
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setWordWrap(True)
        self._status.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._status.setFrameShape(QFrame.Shape.StyledPanel)
        self._status.setStyleSheet(_FOCUS_VISIBLE_TEXT_STYLE)
        content_layout.addWidget(self._status)
        content_layout.addStretch(1)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("constraintEditorScrollArea")
        self._scroll.setAccessibleName(tr("Portal constraint controls"))
        self._scroll.setAccessibleDescription(
            tr(
                "Scrollable source, format, encryption, optional limit, and requested-name controls."
            )
        )
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._scroll.setWidget(content)
        adapt_localized_content(self._scroll)
        self._scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        buttons = QDialogButtonBox()
        buttons.setObjectName("constraintEditorButtons")
        buttons.setAccessibleName(tr("Apply or cancel constraint changes"))
        buttons.setAccessibleDescription(
            tr(
                "Cancel is the safe default. Apply validates the complete detached draft and requests one revision-bound save."
            )
        )
        self._apply_button = self._button(
            tr("Apply constraint changes"),
            "constraintApplyButton",
            tr(
                "Validate every field and request saving the complete detached draft. The dialog stays open until saving succeeds or fails safely."
            ),
        )
        self._apply_button.setDefault(False)
        self._apply_button.setAutoDefault(False)
        buttons.addButton(self._apply_button, QDialogButtonBox.ButtonRole.AcceptRole)
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        if not isinstance(cancel, QPushButton):
            raise RuntimeError("Qt did not create the required Cancel button")
        self._cancel_button = cancel
        self._cancel_button.setObjectName("constraintCancelButton")
        self._cancel_button.setAccessibleName(tr("Cancel constraint changes"))
        self._cancel_button.setAccessibleDescription(
            tr("Discard this local draft without changing the case. Escape.")
        )
        self._cancel_button.setDefault(True)
        self._cancel_button.setAutoDefault(True)
        self._apply_button.clicked.connect(self._request_apply)
        self._cancel_button.clicked.connect(self.reject)
        adapt_dialog_button_box(buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(self._scroll, 1)
        layout.addWidget(buttons)

        self._name_list.currentRowChanged.connect(self._name_selection_changed)
        self._name_stem.textChanged.connect(self._update_name_actions)
        self._editing_controls: tuple[QWidget, ...] = (
            self._restore_button,
            *self._mode_checks.values(),
            self._encryption,
            *(widget for pair in self._limit_controls.values() for widget in pair),
            self._name_list,
            self._name_stem,
            self._name_extension,
            self._name_add,
            self._name_update,
            self._name_remove,
            self._name_earlier,
            self._name_later,
        )
        tab_chain: tuple[QWidget, ...] = (
            self._cancel_button,
            self._scroll,
            self._source_status,
            self._restore_button,
            *self._mode_checks.values(),
            self._encryption,
            *(widget for pair in self._limit_controls.values() for widget in pair),
            self._name_list,
            self._name_stem,
            self._name_extension,
            self._name_add,
            self._name_update,
            self._name_remove,
            self._name_earlier,
            self._name_later,
            self._status,
            self._apply_button,
            self._cancel_button,
        )
        for current, following in pairwise(tab_chain):
            QWidget.setTabOrder(current, following)

        self._populate_from_state()
        self.resize(860, 760)
        self._cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)

    @property
    def state(self) -> ConstraintEditorState:
        """Return the latest complete validated detached state, never a manifest."""

        return self._state

    @property
    def apply_pending(self) -> bool:
        return self._apply_pending

    @property
    def commit_started(self) -> bool:
        return self._commit_started

    def latest_state_for_shutdown(self) -> ConstraintEditorState | None:
        """Return a safe draft for shutdown only when no Apply request is in flight."""

        if self._apply_pending:
            return None
        try:
            next_state = self._state.replace(self._collect_draft())
        except ConstraintEditorError as error:
            self._show_validation_error(error)
            return None
        self._state = next_state
        self.state_changed.emit(next_state)
        return next_state

    def mark_applied(self) -> bool:
        """Close only after the coordinator confirms durable revision publication."""

        if not self._apply_pending:
            return False
        self._apply_pending = False
        self._set_status(ConstraintAnnouncement.APPLIED, announce=True)
        super().accept()
        return True

    def mark_apply_failed(self, _code: str, _message: str) -> bool:
        """Retain the validated draft and restore a keyboard retry path."""

        if not self._apply_pending:
            return False
        self._apply_pending = False
        self._set_editor_enabled(True)
        self._apply_button.setAccessibleDescription(
            tr("Retry saving the complete retained constraint draft.")
        )
        self._set_status(
            ConstraintAnnouncement.APPLY_FAILED,
            announce=True,
            assertive=True,
        )
        self._apply_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._scroll.ensureWidgetVisible(self._apply_button, 8, 8)
        return True

    def reject(self) -> None:
        """Discard only before the draft crosses the persistence boundary."""

        if self._commit_started:
            QApplication.beep()
            self._set_status(
                tr(
                    "These validated constraint changes are retained for retry and cannot be discarded from this dialog after Apply was requested."
                ),
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
        button = QPushButton(tr(text))
        button.setObjectName(object_name)
        button.setAccessibleName(tr(text))
        button.setAccessibleDescription(tr(description))
        return button

    def _populate_from_state(self) -> None:
        self._refreshing = True
        try:
            source = self._state.source
            self._template_title.setText(
                tr("Configured generic template: {value1}", value1=tr(source.template_title))
            )
            self._template_title.setAccessibleDescription(self._template_title.text())
            source_text = tr(
                "{heading}. {detail}", heading=tr(source.heading), detail=tr(source.detail)
            )
            self._source_status.setText(source_text)
            self._source_status.setAccessibleDescription(source_text)
            self._restore_button.setEnabled(source.can_restore_generic_defaults)
            for mode, check in self._mode_checks.items():
                check.setChecked(mode in self._state.constraints.allowed_modes)
            encryption_index = self._encryption.findData(
                self._state.constraints.encryption_policy.value
            )
            if encryption_index >= 0:
                self._encryption.setCurrentIndex(encryption_index)
            for selected_field, value in (
                (
                    ConstraintValidationField.MAXIMUM_FILE_COUNT,
                    self._state.constraints.maximum_file_count,
                ),
                (
                    ConstraintValidationField.MAXIMUM_TOTAL_BYTES,
                    self._state.constraints.maximum_total_bytes,
                ),
                (
                    ConstraintValidationField.MAXIMUM_FILE_BYTES,
                    self._state.constraints.maximum_file_bytes,
                ),
                (
                    ConstraintValidationField.MAXIMUM_PAGES_PER_FILE,
                    self._state.constraints.maximum_pages_per_file,
                ),
            ):
                enabled, editor = self._limit_controls[selected_field]
                enabled.setChecked(value is not None)
                editor.setEnabled(value is not None)
                editor.setText("" if value is None else str(value))
            self._name_stems = [value.stem for value in self._state.output_names]
            self._refresh_name_list()
            self._set_status(self._state.announcement, announce=False)
        finally:
            self._refreshing = False
        self._update_name_actions()

    def _selected_modes(self) -> tuple[PortalOutputMode, ...]:
        return tuple(mode for mode, check in self._mode_checks.items() if check.isChecked())

    def _preview_mode(self) -> PortalOutputMode:
        selected = self._selected_modes()
        if self._state.filename_mode in selected:
            return self._state.filename_mode
        for mode in selected:
            return mode
        return self._state.filename_mode

    def _refresh_name_list(self, *, selected_row: int | None = None) -> bool:
        try:
            rows = preview_output_names(tuple(self._name_stems), self._preview_mode())
        except ConstraintEditorError as error:
            self._show_validation_error(error)
            return False
        previous = self._name_list.currentRow() if selected_row is None else selected_row
        self._name_list.clear()
        for row in rows:
            item = QListWidgetItem(row.filename)
            item.setToolTip(tr("Safe resolved filename component"))
            self._name_list.addItem(item)
        extension = ".zip" if self._preview_mode() is PortalOutputMode.ZIP else ".pdf"
        self._name_extension.setText(extension)
        self._name_extension.setAccessibleDescription(
            tr(
                "Read-only {value1} preview. This does not select the future export organization.",
                value1=extension,
            )
        )
        if self._name_list.count() > 0:
            self._name_list.setCurrentRow(min(max(previous, 0), self._name_list.count() - 1))
        self._update_name_actions()
        return True

    def _allowed_modes_changed(self, _checked: bool) -> None:
        if self._refreshing:
            return
        self._refresh_name_list()

    def _name_selection_changed(self, row: int) -> None:
        if 0 <= row < len(self._name_stems):
            self._name_stem.setText(self._name_stems[row])
        self._update_name_actions()

    def _add_name(self) -> None:
        stem = self._name_stem.text()
        candidate = [*self._name_stems, stem]
        try:
            preview_output_names(tuple(candidate), self._preview_mode())
        except ConstraintEditorError as error:
            self._show_validation_error(error)
            return
        self._name_stems = candidate
        self._refresh_name_list(selected_row=len(candidate) - 1)
        self._name_stem.clear()
        self._set_status(
            tr("Requested name added to the local form. Apply validates the complete draft."),
            announce=True,
        )
        self._name_stem.setFocus(Qt.FocusReason.OtherFocusReason)

    def _update_name(self) -> None:
        row = self._name_list.currentRow()
        if not 0 <= row < len(self._name_stems):
            return
        candidate = list(self._name_stems)
        candidate[row] = self._name_stem.text()
        try:
            preview_output_names(tuple(candidate), self._preview_mode())
        except ConstraintEditorError as error:
            self._show_validation_error(error)
            return
        self._name_stems = candidate
        self._refresh_name_list(selected_row=row)
        self._set_status(
            tr("Selected requested name updated in the local form."),
            announce=True,
        )
        self._name_list.setFocus(Qt.FocusReason.OtherFocusReason)

    def _remove_name(self) -> None:
        row = self._name_list.currentRow()
        if not 0 <= row < len(self._name_stems):
            return
        del self._name_stems[row]
        self._refresh_name_list(selected_row=min(row, len(self._name_stems) - 1))
        self._name_stem.clear()
        self._set_status(
            tr("Selected requested name removed from the local form."),
            announce=True,
        )
        target = self._name_list if self._name_stems else self._name_stem
        target.setFocus(Qt.FocusReason.OtherFocusReason)

    def _move_name(self, direction: int) -> None:
        row = self._name_list.currentRow()
        target = row + direction
        if not 0 <= row < len(self._name_stems) or not 0 <= target < len(self._name_stems):
            return
        self._name_stems[row], self._name_stems[target] = (
            self._name_stems[target],
            self._name_stems[row],
        )
        self._refresh_name_list(selected_row=target)
        self._set_status(tr("Requested name order updated."), announce=True)
        self._name_list.setFocus(Qt.FocusReason.OtherFocusReason)

    def _update_name_actions(self, *_ignored: object) -> None:
        if self._apply_pending:
            return
        row = self._name_list.currentRow()
        selected = 0 <= row < len(self._name_stems)
        has_text = bool(self._name_stem.text())
        self._name_add.setEnabled(has_text)
        self._name_update.setEnabled(selected and has_text)
        self._name_remove.setEnabled(selected)
        self._name_earlier.setEnabled(selected and row > 0)
        self._name_later.setEnabled(selected and row + 1 < len(self._name_stems))

    def _restore_defaults(self) -> None:
        if self._apply_pending:
            return
        try:
            next_state = self._state.restore_generic_defaults()
        except ConstraintEditorError as error:
            self._show_validation_error(error)
            return
        self._state = next_state
        self._populate_from_state()
        self.state_changed.emit(next_state)
        self._set_status(next_state.announcement, announce=True)
        self._source_status.setFocus(Qt.FocusReason.OtherFocusReason)
        self._scroll.ensureWidgetVisible(self._source_status, 8, 8)

    def _collect_draft(self) -> ConstraintDraftInput:
        modes = self._selected_modes()
        if not modes:
            reject_constraint_editor(
                ConstraintEditorErrorCode.DRAFT_INVALID,
                "constraints.ui_modes",
                ConstraintValidationField.ALLOWED_MODES,
            )
        policy_value = self._encryption.currentData()
        if type(policy_value) is not str:
            reject_constraint_editor(
                ConstraintEditorErrorCode.DRAFT_INVALID,
                "constraints.ui_encryption",
                ConstraintValidationField.ENCRYPTION_POLICY,
            )
        try:
            policy = PortalEncryptionPolicy(policy_value)
        except ValueError:
            reject_constraint_editor(
                ConstraintEditorErrorCode.DRAFT_INVALID,
                "constraints.ui_encryption",
                ConstraintValidationField.ENCRYPTION_POLICY,
            )
        limits: dict[ConstraintValidationField, int | None] = {}
        for selected_field, (enabled, editor) in self._limit_controls.items():
            if not enabled.isChecked():
                limits[selected_field] = None
                continue
            text = editor.text()
            if not text or not text.isascii() or not text.isdecimal():
                reject_constraint_editor(
                    ConstraintEditorErrorCode.DRAFT_INVALID,
                    "constraints.ui_limit",
                    selected_field,
                )
            limits[selected_field] = int(text)
        return ConstraintDraftInput(
            modes,
            policy,
            limits[ConstraintValidationField.MAXIMUM_FILE_COUNT],
            limits[ConstraintValidationField.MAXIMUM_TOTAL_BYTES],
            limits[ConstraintValidationField.MAXIMUM_FILE_BYTES],
            limits[ConstraintValidationField.MAXIMUM_PAGES_PER_FILE],
            tuple(self._name_stems),
        )

    def _request_apply(self) -> None:
        if self._apply_pending:
            return
        try:
            draft = self._collect_draft()
            next_state = self._state.replace(draft)
        except ConstraintEditorError as error:
            self._show_validation_error(error)
            return
        self._state = next_state
        self.state_changed.emit(next_state)
        self._apply_pending = True
        self._commit_started = True
        self._set_editor_enabled(False)
        self._set_status(ConstraintAnnouncement.APPLYING, announce=True)
        self.apply_requested.emit(next_state)

    def _show_validation_error(self, error: ConstraintEditorError) -> None:
        self._set_status(
            f"{error.code.value}: {tr(error.safe_message)}",
            announce=True,
            assertive=True,
        )
        target = self._field_control(error.field)
        target.setFocus(Qt.FocusReason.OtherFocusReason)
        self._scroll.ensureWidgetVisible(target, 8, 8)

    def _field_control(self, field: ConstraintValidationField | None) -> QWidget:
        if field is ConstraintValidationField.ALLOWED_MODES:
            return self._mode_checks[PortalOutputMode.COMBINED_PDF]
        if field is ConstraintValidationField.ENCRYPTION_POLICY:
            return self._encryption
        if field in self._limit_controls:
            return self._limit_controls[field][1]
        if field is ConstraintValidationField.OUTPUT_NAMES:
            return self._name_stem
        return self._status

    def _set_editor_enabled(self, enabled: bool) -> None:
        for control in self._editing_controls:
            control.setEnabled(enabled)
        self._apply_button.setEnabled(enabled)
        self._cancel_button.setEnabled(enabled and not self._commit_started)
        if self._commit_started:
            self._cancel_button.setAccessibleDescription(
                tr("Unavailable after Apply because the validated draft is retained for retry.")
            )
        if enabled:
            for checkbox, editor in self._limit_controls.values():
                editor.setEnabled(checkbox.isChecked())
            self._restore_button.setEnabled(self._state.source.can_restore_generic_defaults)
            self._update_name_actions()

    def _set_status(
        self,
        message: str,
        *,
        announce: bool,
        assertive: bool = False,
    ) -> None:
        message = tr(message)
        self._status.setText(message)
        self._status.setAccessibleName(
            tr("Package constraint editor status. {value1}", value1=message)
        )
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


__all__ = ("ConstraintEditorDialog",)

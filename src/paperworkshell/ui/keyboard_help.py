"""Visible keyboard reference for the guided packet workflow."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from paperworkshell.localization.catalog import mark, tr
from paperworkshell.ui.presentation import adapt_dialog_button_box

KEYBOARD_REFERENCE = mark(
    "PaperWorkShell keyboard guide\n\n"
    "Move around\n"
    "Tab / Shift+Tab: next / previous control. Disabled actions are skipped.\n"
    "Space: activate a focused button or checkbox.\n"
    "Arrow keys: select a page, list item, or option; adjust numeric controls.\n"
    "Enter: activate a focused button or the default dialog action. In text/mark fields, Enter updates the local draft; use Save changes to save it.\n"
    "Escape: close a dialog or request cancellation when allowed. In a draft editor, Cancel discards unsaved changes.\n"
    "Alt+F4: close the current window. Use Save changes first when editing a draft.\n"
    "Ctrl+K: search and run commands for the current case or page. Escape returns to your previous control.\n"
    "F1: open this keyboard guide from the main window (also Help > Keyboard guide).\n\n"
    "Cases and files\n"
    "Ctrl+N: open New case, enter a Case name, then choose Create case. Cancel creates no case.\n"
    "Ctrl+O: Open case from the case chooser. Select the named case first.\n"
    "Ctrl+I: Add file; choose one PDF, JPEG, or PNG for the open case.\n"
    "Rename case: change the visible name while keeping its documents and edits.\n"
    "Close case: return to the case chooser after queued changes are saved.\n\n"
    "Arrange pages\n"
    "Select a page by filename. The selection stays on that page when focus moves to a button.\n"
    "Alt+Up / Alt+Down: Move up / Move down in the output order.\n"
    "Ctrl+R: Rotate right 90 degrees.\n"
    "Delete: Remove page from this case. The original file is preserved.\n"
    "Ctrl+Z / Ctrl+Y: undo / redo an organizer action.\n"
    "Ctrl+Shift+O: Reorder pages using the list and Move up / Move down.\n\n"
    "Improve scans and choose languages\n"
    "Ctrl+Shift+A: Improve scan for a supported picture page.\n"
    "Ctrl+Shift+L: OCR languages; review the selected languages and their local availability.\n"
    "Ctrl+Shift+Q: Check page quality. Findings need human review.\n"
    "Ctrl+Shift+R: Retry selected preview after a preview failure.\n\n"
    "Add text or marks\n"
    "Ctrl+Shift+F: open Add text or marks for the selected page.\n"
    "Choose Add text, type in the focused Text field, and review the page preview.\n"
    "Choose Add checkmark for a selected mark you can position on the page.\n"
    "Use percentages for position and size; font size uses points. Save changes saves the complete draft. Cancel discards it before saving starts.\n"
    "Tab reaches the text field, mark list, settings, movement buttons, and Save changes. The visual canvas is skipped.\n"
    "Alt+arrows move the selected mark; Alt+Shift+arrows resize it.\n"
    "Ctrl+Alt+arrows / Ctrl+Alt+Shift+arrows make fine movement / size adjustments.\n"
    "Ctrl+Plus or Ctrl+= / Ctrl+Minus zoom in / out; Ctrl+0 resets zoom.\n\n"
    "Signature images and permanent-removal marks\n"
    "Ctrl+Shift+S: Place signature image, a visual mark rather than a digital signature.\n"
    "Ctrl+Shift+D: Remove signature image; review the saved placements and removal choices.\n"
    "Ctrl+Shift+P: open Permanently remove information to mark areas. Saving marks does not remove content.\n"
    "In that editor, Alt+A adds a region; use the numeric position and size fields to place it.\n"
    "Ctrl+Alt+E: review and export a supported removal derivative, when available.\n\n"
    "Export PDF\n"
    "Ctrl+Shift+E: open Export PDF for the current pages and saved edits.\n"
    "Optionally turn on Make scanned text searchable to add OCR during export. Processing stays on this computer. Recognition can make mistakes.\n"
    "The export dialog shows the recognition language. Choose OCR languages before exporting to change it.\n"
    "Choose PDF file, select a new filename, and wait for the checked result. Open PDF or Open folder from the retained result.\n\n"
    "Optional document checklist\n"
    "Ctrl+Shift+C: Document checklist; assign pages to the chosen checklist requirements and complete their substantive confirmations. Ordinary PDF export does not require these assignments.\n"
    "Review package constraints: edit the checklist package limits and requested names.\n"
    "Ctrl+E: Export checklist package; review its files, checks, confirmations, and output format.\n"
    "In package review, Tab reaches findings, confirmations, compression, and export actions.\n"
    "In read-only summaries, Ctrl+Home / Ctrl+End go to the start / end.\n"
    "Page Up / Page Down scroll summaries; Tab / Shift+Tab leave the text view.\n\n"
    "Progress and recovery\n"
    "Escape or Cancel current operation: request safe cancellation, then wait for completion.\n"
    "Brief saves already in progress must finish; a failed save keeps changes for retry.\n"
    "Saved state, current work, and the last exported result are shown separately.\n"
    "Errors show a recovery message and focus a relevant retry or review control.\n"
    "If a saved case must be reopened, use Close case and reopen it from the chooser.\n"
    "All drag operations have visible selection, assignment, or movement controls.\n"
)


class KeyboardHelpDialog(QDialog):
    """A bounded, selectable reference with an ordinary Tab and Escape exit."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("keyboardHelpDialog")
        self.setWindowTitle(tr("PaperWorkShell keyboard guide"))
        self.setAccessibleName(tr("PaperWorkShell keyboard guide"))
        self.setModal(True)
        self.resize(760, 560)
        self._reference = QPlainTextEdit(tr(KEYBOARD_REFERENCE))
        self._reference.setObjectName("keyboardReference")
        self._reference.setAccessibleName(tr("Keyboard guide"))
        self._reference.setAccessibleDescription(
            tr(
                "Read-only shortcuts and navigation instructions. Control+Home and Control+End move through the guide. Tab reaches Close."
            )
        )
        self._reference.setReadOnly(True)
        self._reference.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self._reference.setTabChangesFocus(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        adapt_dialog_button_box(buttons)
        self._close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        self._close_button.setObjectName("keyboardHelpCloseButton")
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self._reference)
        layout.addWidget(buttons)
        QWidget.setTabOrder(self._reference, self._close_button)
        self._reference.setFocus()

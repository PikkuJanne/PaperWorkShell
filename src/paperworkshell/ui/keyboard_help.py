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
    "PaperWorkShell keyboard guide\n\nMove around\nTab / Shift+Tab: next / previous control. Disabled actions are skipped.\nSpace: activate a focused button or checkbox.\nArrow keys: select a page, list item, or option; adjust numeric controls.\nEnter: activate the default dialog action. Read its label before confirming.\nEscape: close a review dialog or request cancellation when the operation allows it.\nAlt+F4: close the current window; pending work is saved or stopped safely.\nF1: open this keyboard guide from the main window (also Help > Keyboard guide).\n\nAdd and cases\nCtrl+N: create a case from the case chooser.\nCtrl+O: open the selected case from the case chooser.\nCtrl+I: import a PDF, JPEG, or PNG into the open case.\nClose case: Tab to the visible button to return to the case chooser.\n\nOrganize (main window)\nAlt+Up / Alt+Down: move the selected page earlier / later.\nCtrl+R: rotate the selected page clockwise.\nDelete: remove the selected page from the packet.\nCtrl+Z / Ctrl+Y: undo / redo an organizer action.\nCtrl+Shift+O: change page order using a list and Move earlier / Move later.\n\nImprove\nCtrl+Shift+A: review scan adjustments for a supported image page.\nCtrl+Shift+L: manage verified local OCR languages.\nCtrl+Shift+Q: run page quality checks. Findings need human review.\nCtrl+Shift+R: retry the selected failed preview.\n\nFill and Protect\nCtrl+Shift+F: edit text, dates, initials, checkmarks, crosses, and ordinary shapes.\nCtrl+Shift+S: place a signature image (a visual mark).\nCtrl+Shift+D: review signature-image placements and removal choices.\nCtrl+Shift+P: edit permanent-removal marks. Saving marks does not remove content.\nIn an editor, Tab reaches the mark list, geometry fields, history and save buttons.\nThe visual canvas is skipped; use the list, numeric fields and visible movement buttons.\nIn Fill, Alt+arrows move a mark; Alt+Shift+arrows resize it.\nCtrl+Alt+arrows / Ctrl+Alt+Shift+arrows make fine movement / size adjustments.\nCtrl+Plus or Ctrl+= / Ctrl+Minus zoom in / out; Ctrl+0 resets zoom.\nIn Protect, Alt+A adds a region; use numeric position and size fields to place it.\nEditor controls describe their local shortcuts; text fields keep normal text editing.\n\nCheck and Export\nCtrl+Shift+C: review packet checklist assignments and confirmations.\nReview package constraints: Tab to the visible button to edit limits and names.\nCtrl+E: review and export a package; choose its output format in the review.\nCtrl+Shift+E: export current pages as one verified PDF.\nCtrl+Alt+E: review and export a supported removal derivative, when available.\nIn package review, Tab reaches findings, confirmations, compression and export actions.\nIn read-only summaries, Ctrl+Home / Ctrl+End go to the start / end.\nPage Up / Page Down scroll summaries; Tab / Shift+Tab leave the text view.\n\nProgress and recovery\nEscape or Cancel current operation: request safe cancellation, then wait for completion.\nBrief saves already in progress must finish; a failed save keeps the draft for Retry.\nErrors show a recovery message and focus a relevant retry or review control.\nIf a saved case must be reopened, use Close case and reopen it from the chooser.\nAll drag operations have visible selection, assignment, or movement controls.\n"
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

"""Accessible, explicit review and terminal summary for package exports."""

from __future__ import annotations

from itertools import pairwise
from typing import Final

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent, QCloseEvent, QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from paperworkshell.app.package_workflow import PackageWorkflowError, PackageWorkflowErrorCode
from paperworkshell.domain.package_summary import PackageSummary, PackageSummaryStatus
from paperworkshell.preflight.portal_constraints import PortalOutputMode

_MODE_TEXT: Final[dict[PortalOutputMode, str]] = {
    PortalOutputMode.COMBINED_PDF: "Combined PDF",
    PortalOutputMode.SEPARATE_PDFS: "Separate PDFs",
    PortalOutputMode.ZIP: "ZIP package",
}
_PROGRESS_TEXT: Final[dict[str, str]] = {
    "preparing": "Preparing package review",
    "inspecting_pdfs": "Inspecting PDFs for package review",
    "generating_pdfs": "Creating and verifying PDFs",
    "packaging_zip": "Creating and verifying the ZIP package",
    "checking_constraints": "Checking actual output constraints",
    "committing": "Publishing verified outputs",
    "complete": "Finishing package export",
    "saving_summary": "Saving the displayed summary",
}
_RESULT_TEXT: Final[dict[PackageSummaryStatus, str]] = {
    PackageSummaryStatus.COMMITTED: (
        "Package export complete. Review the saved files and remaining human checks in the summary."
    ),
    PackageSummaryStatus.CONSTRAINTS_BLOCKED: (
        "Package export blocked by constraints. Review the summary, then close this dialog to "
        "adjust the case or constraints before trying again."
    ),
    PackageSummaryStatus.NOT_COMMITTED: (
        "Package export did not publish the planned outputs. Review the summary and choose a "
        "new attempt when the cause is resolved."
    ),
    PackageSummaryStatus.PARTIAL: (
        "Package export is incomplete: some outputs were saved. Check the exact saved-file "
        "inventory before retrying in a new destination folder."
    ),
    PackageSummaryStatus.UNCERTAIN: (
        "Package export could not confirm the complete destination state. Inspect the destination "
        "and the summary before retrying in a new folder."
    ),
}
_FOCUS_STYLE: Final = "QLabel:focus { border: 2px solid palette(highlight); padding: 3px; }"


class PackageExportDialog(QDialog):
    """Display authoritative summaries; the coordinator retains the export authority.

    Choosing a mode invalidates the previous review immediately. A coordinator
    must provide a fresh review before export can be requested. Local checkboxes
    are explicit user acknowledgements, never stored preflight confirmations.
    """

    mode_requested = Signal(object)
    refresh_requested = Signal()
    export_requested = Signal(bool, bool)
    compression_requested = Signal(bool, bool)
    cancel_requested = Signal()
    summary_save_requested = Signal()

    def __init__(
        self,
        allowed_modes: tuple[PortalOutputMode, ...],
        parent: QWidget | None = None,
        *,
        initial_mode: PortalOutputMode | None = None,
    ) -> None:
        if (
            type(allowed_modes) is not tuple
            or not allowed_modes
            or any(type(mode) is not PortalOutputMode for mode in allowed_modes)
            or len(set(allowed_modes)) != len(allowed_modes)
            or (initial_mode is not None and initial_mode not in allowed_modes)
        ):
            raise ValueError("Package export requires distinct allowed output modes.")
        super().__init__(parent)
        self._summary: PackageSummary | None = None
        self._busy = False
        self._cancelable = False
        self._cancellation_requested = False
        self._terminal = False
        self._review_ready = False
        self._export_allowed = False
        self._compression_allowed = False
        self._signed_required = False
        self._human_required = False
        self._save_available = False
        self._saving = False

        self.setObjectName("packageExportDialog")
        self.setWindowTitle("Review and export package")
        self.setAccessibleName("Review and export package")
        self.setAccessibleDescription(
            "Choose an allowed output format, review planned files and human checks, then "
            "explicitly export. The summary is selectable plain text. Close is the safe default."
        )
        self.setModal(True)

        content = QWidget()
        content.setObjectName("packageExportContent")
        content_layout = QVBoxLayout(content)
        guidance = self._label(
            "Review file names, page counts, categories, machine checks, and required human "
            "review. Actual sizes are known after the files are created and verified. These "
            "checks do not guarantee portal acceptance or legal sufficiency.",
            "packageExportGuidance",
            "Package review guidance",
        )
        content_layout.addWidget(guidance)

        mode_label = QLabel("Output &format")
        self._mode = QComboBox()
        self._mode.setObjectName("packageExportModeCombo")
        self._mode.setAccessibleName("Package output format")
        self._mode.setAccessibleDescription(
            "Choose a format allowed by the saved case constraints. Changing the format "
            "requires a fresh review and clears local acknowledgements."
        )
        for mode in allowed_modes:
            self._mode.addItem(_MODE_TEXT[mode], mode.value)
        if initial_mode is not None:
            self._mode.setCurrentIndex(allowed_modes.index(initial_mode))
        mode_label.setBuddy(self._mode)
        content_layout.addWidget(mode_label)
        content_layout.addWidget(self._mode)
        self._refresh = self._button(
            "Refresh review",
            "packageExportRefreshButton",
            "Request a fresh review of the current case and selected output format.",
        )
        self._refresh.clicked.connect(self._request_refresh)
        content_layout.addWidget(self._refresh)

        summary_label = QLabel("Package &summary")
        summary_label.setWordWrap(True)
        self._text = QPlainTextEdit()
        self._text.setObjectName("packageExportSummaryText")
        self._text.setAccessibleName("Package summary plain text")
        self._text.setAccessibleDescription(
            "Read-only, selectable plain text. Use arrow keys to read, Control+A to select all, "
            "and Control+C to copy. Tab moves to the next control."
        )
        self._text.setReadOnly(True)
        self._text.setTabChangesFocus(True)
        self._text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._text.setMinimumHeight(220)
        self._text.setPlainText("A package review has not been prepared yet.")
        summary_label.setBuddy(self._text)
        content_layout.addWidget(summary_label)
        content_layout.addWidget(self._text, 1)

        self._human_ack = QCheckBox()
        self._human_ack.setObjectName("packageExportHumanReviewCheck")
        self._human_ack.setAccessibleName("Confirm human review of this package")
        self._human_ack.setAccessibleDescription(
            "Confirm only after reviewing the listed warnings and required human checks. "
            "This records your acknowledgement for this attempt; it does not establish "
            "eligibility, legal sufficiency, OCR correctness, or portal acceptance."
        )
        self._human_ack.toggled.connect(self._update_actions)
        self._human_label = self._label(
            "I reviewed the listed warnings and human checks",
            "packageExportHumanReviewLabel",
            "Human review confirmation",
        )
        self._human_label.setBuddy(self._human_ack)
        human_row = QHBoxLayout()
        human_row.addWidget(self._human_ack, 0, Qt.AlignmentFlag.AlignTop)
        human_row.addWidget(self._human_label, 1)
        content_layout.addLayout(human_row)
        self._human_guidance = self._label(
            "Confirm only after reviewing the listed warnings and required human checks for "
            "this package. This confirmation applies to this attempt.",
            "packageExportHumanReviewGuidance",
            "Human review acknowledgement instructions",
        )
        content_layout.addWidget(self._human_guidance)
        self._signed_ack = QCheckBox()
        self._signed_ack.setObjectName("packageExportUnsignedDerivativeCheck")
        self._signed_ack.setAccessibleName("Confirm creation of unsigned derivatives")
        self._signed_ack.setAccessibleDescription(
            "The exported modified files will be unsigned derivatives. Original digital "
            "signatures do not remain valid on the exported modified files. This action does "
            "not validate a signature or create a digital or qualified electronic signature."
        )
        self._signed_ack.toggled.connect(self._update_actions)
        self._signed_label = self._label(
            "Create unsigned derivatives from digitally signed sources",
            "packageExportUnsignedDerivativeLabel",
            "Unsigned derivative confirmation",
        )
        self._signed_label.setBuddy(self._signed_ack)
        signed_row = QHBoxLayout()
        signed_row.addWidget(self._signed_ack, 0, Qt.AlignmentFlag.AlignTop)
        signed_row.addWidget(self._signed_label, 1)
        content_layout.addLayout(signed_row)
        self._signed_guidance = self._label(
            "Digitally signed sources will produce unsigned derivatives. The original "
            "signatures do not remain valid on the modified exported files. Original source "
            "files remain unchanged.",
            "packageExportUnsignedDerivativeGuidance",
            "Unsigned derivative consequence",
        )
        content_layout.addWidget(self._signed_guidance)

        self._status = self._label(
            "Prepare a package review before exporting.",
            "packageExportStatus",
            "Package export status and recovery",
        )
        self._status.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._status.setFrameShape(QFrame.Shape.StyledPanel)
        self._status.setStyleSheet(_FOCUS_STYLE)
        content_layout.addWidget(self._status)
        self._progress = QProgressBar()
        self._progress.setObjectName("packageExportProgress")
        self._progress.setAccessibleName("Package operation progress")
        self._progress.setAccessibleDescription("Package operation has not started.")
        self._progress.hide()
        content_layout.addWidget(self._progress)
        self._export = self._button(
            "Export package",
            "packageExportStartButton",
            "Choose the destination and request the reviewed package. Every required PDF is "
            "verified before publication. Existing files are not replaced.",
        )
        self._export.clicked.connect(self._request_export)
        content_layout.addWidget(self._export)
        self._compression = self._button(
            "Preview size\nreduction",
            "packageExportCompressionButton",
            "Choose a destination and prepare a private size-reduction preview. Review actual "
            "sizes and affected pages before explicitly publishing the selected candidate.",
        )
        self._compression.setAccessibleName("Preview size reduction")
        self._compression.clicked.connect(self._request_compression)
        content_layout.addWidget(self._compression)
        self._save = self._button(
            "Save summary",
            "packageExportSaveSummaryButton",
            "Choose where to save exactly the displayed summary as plain text. The summary "
            "contains output names and category labels; review it before sharing.",
        )
        self._save.clicked.connect(self._request_summary_save)
        content_layout.addWidget(self._save)
        self._cancel = self._button(
            "Cancel",
            "packageExportCancelButton",
            "Request cancellation and wait for the final result. Files already saved may remain; "
            "the final summary states the known destination inventory.",
        )
        self._cancel.clicked.connect(self._request_cancel)
        content_layout.addWidget(self._cancel)
        self._scroll = QScrollArea()
        self._scroll.setObjectName("packageExportScrollArea")
        self._scroll.setAccessibleName("Package review and export controls")
        self._scroll.setAccessibleDescription(
            "Scrollable format, summary, acknowledgement, export, and cancellation controls."
        )
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._scroll.setWidget(content)
        self._close = self._button(
            "Close",
            "packageExportCloseButton",
            "Close this review. During an operation Escape requests cancellation and keeps the "
            "dialog open until the result is known.",
        )
        self._close.setDefault(True)
        self._close.setAutoDefault(True)
        self._close.clicked.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self._scroll, 1)
        layout.addWidget(self._close)
        tab_chain: tuple[QWidget, ...] = (
            self._close,
            self._scroll,
            self._mode,
            self._refresh,
            self._text,
            self._human_ack,
            self._signed_ack,
            self._status,
            self._export,
            self._compression,
            self._save,
            self._cancel,
            self._close,
        )
        for current, following in pairwise(tab_chain):
            QWidget.setTabOrder(current, following)
        self._mode.currentIndexChanged.connect(self._mode_changed)
        self._update_actions()
        self.resize(800, 740)

    @property
    def selected_mode(self) -> PortalOutputMode:
        return PortalOutputMode(self._mode.currentData())

    @property
    def summary(self) -> PackageSummary | None:
        return self._summary

    @property
    def busy(self) -> bool:
        return self._busy

    def show_review(
        self,
        summary: PackageSummary,
        *,
        export_allowed: bool,
        signed_source_confirmation_required: bool = False,
        human_review_required: bool = False,
        compression_allowed: bool = False,
    ) -> None:
        """Replace the review and clear acknowledgements for the previous snapshot."""

        if (
            type(summary) is not PackageSummary
            or summary.status
            not in {PackageSummaryStatus.PLANNED, PackageSummaryStatus.NEEDS_REVIEW}
            or type(export_allowed) is not bool
            or type(signed_source_confirmation_required) is not bool
            or type(human_review_required) is not bool
            or type(compression_allowed) is not bool
        ):
            raise ValueError("Package review requires a review summary and explicit gates.")
        self._summary = summary
        self._text.setPlainText(summary.to_plain_text())
        self._busy = False
        self._saving = False
        self._terminal = False
        self._review_ready = True
        self._export_allowed = export_allowed
        self._compression_allowed = compression_allowed
        self._signed_required = signed_source_confirmation_required
        self._human_required = human_review_required
        self._signed_ack.setChecked(False)
        self._human_ack.setChecked(False)
        self._progress.hide()
        self._update_actions()
        self._set_status(
            "Package review ready. Read the summary and complete any required review steps "
            "before choosing Export package."
            if export_allowed
            else "The package has a size constraint to resolve. Read the summary and complete "
            "any required review steps before choosing Preview size reduction."
            if compression_allowed
            else "Package needs attention before export. Read the summary, then close this dialog "
            "to resolve blocking items and refresh the review.",
            assertive=not export_allowed,
        )
        self._focus(self._text)

    def show_result(self, summary: PackageSummary, *, save_available: bool = False) -> None:
        """Show the actual terminal inventory, including partial and uncertain results."""

        if (
            type(summary) is not PackageSummary
            or summary.status not in _RESULT_TEXT
            or type(save_available) is not bool
        ):
            raise ValueError("Package result requires a terminal summary.")
        self._summary = summary
        self._text.setPlainText(summary.to_plain_text())
        self._busy = False
        self._saving = False
        self._terminal = True
        self._review_ready = False
        self._export_allowed = False
        self._save_available = save_available
        self._progress.hide()
        self._update_actions()
        self._set_status(
            _RESULT_TEXT[summary.status],
            assertive=summary.status is not PackageSummaryStatus.COMMITTED,
        )
        self._focus(self._text)

    def set_busy(self, phase: str = "preparing", *, cancelable: bool = True) -> None:
        """Display only known content-free progress text, never a caller's message."""

        if type(phase) is not str or type(cancelable) is not bool:
            raise ValueError("Package progress requires a phase and cancellation state.")
        self._busy = True
        self._cancelable = cancelable
        self._cancellation_requested = False
        self._progress.setRange(0, 0)
        self._progress.show()
        label = _PROGRESS_TEXT.get(phase, "Working on the package")
        self._progress.setAccessibleDescription(label)
        self._update_actions()
        self._set_status(f"{label}. Keep this dialog open until the result is known.")
        self._focus(self._cancel if cancelable else self._status)

    def set_progress(self, phase: str, completed: int, total: int) -> bool:
        """Update an active operation from bounded numeric progress only."""

        if (
            not self._busy
            or self._cancellation_requested
            or type(phase) is not str
            or type(completed) is not int
            or type(total) is not int
            or not 1 <= total <= 1_000_000
            or not 0 <= completed <= total
        ):
            return False
        label = _PROGRESS_TEXT.get(phase, "Working on the package")
        self._progress.setRange(0, total)
        self._progress.setValue(completed)
        self._progress.setFormat("%v of %m")
        self._progress.setAccessibleDescription(f"{label}: {completed} of {total}.")
        self._set_status(f"{label}: {completed} of {total}.")
        return True

    def show_failure(
        self, _message: str = "", *, phase: str = "review", code: str | None = None
    ) -> None:
        """Present a fixed recovery message; arbitrary exception text is discarded."""

        self._busy = False
        self._saving = False
        self._review_ready = False
        self._export_allowed = False
        self._progress.hide()
        self._update_actions()
        message = (
            "Package export did not finish safely. The destination state is not established by "
            "this message. Inspect the destination before retrying in a new folder. Close and "
            "reopen package review to start a new attempt."
            if phase == "export"
            else "The package review could not be prepared. Close this dialog to check the case "
            "and saved constraints, or choose Refresh review to try again."
        )
        if type(code) is str:
            try:
                known = PackageWorkflowError(PackageWorkflowErrorCode(code))
            except ValueError:
                pass
            else:
                message = f"{known.safe_message} {message}"
        self._set_status(message, assertive=True)
        self._focus(
            self._refresh
            if phase == "review"
            and code in {"PACKAGE_REVIEW_CANCELLED", "PACKAGE_REVIEW_TIMED_OUT"}
            else self._status
        )

    def complete_summary_save(self, *, succeeded: bool) -> None:
        """Keep the final inventory available whether optional summary saving succeeds or fails."""

        if type(succeeded) is not bool or not self._saving:
            return
        cancelled = self._cancellation_requested
        self._busy = False
        self._saving = False
        self._progress.hide()
        self._update_actions()
        if succeeded:
            message = "The displayed summary was saved. Review it before sharing."
        else:
            prefix = (
                "Summary saving stopped after a cancellation request."
                if cancelled
                else "Summary saving was not confirmed."
            )
            message = (
                f"{prefix} The package result above is unchanged. Check the selected destination "
                "before trying a new filename, or select and copy the displayed text."
            )
        self._set_status(message, assertive=not succeeded)
        self._focus(self._save)

    def reject(self) -> None:
        """Keep the result visible while active work finishes or cancels."""

        if self._busy:
            self._request_cancel()
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override name
        if self._busy:
            event.ignore()
            self._request_cancel()
            return
        super().closeEvent(event)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt override name
        super().showEvent(event)
        self._close.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def _mode_changed(self, _index: int) -> None:
        if self._busy or self._terminal:
            return
        self._invalidate_review()
        self.set_busy("preparing")
        self.mode_requested.emit(self.selected_mode)

    def _request_refresh(self) -> None:
        if self._busy or self._terminal:
            return
        self._invalidate_review()
        self.set_busy("preparing")
        self.refresh_requested.emit()

    def _invalidate_review(self) -> None:
        self._summary = None
        self._review_ready = False
        self._export_allowed = False
        self._signed_ack.setChecked(False)
        self._human_ack.setChecked(False)
        self._text.setPlainText("Preparing a fresh review for the selected output format.")

    def _request_export(self) -> None:
        if not self._export.isEnabled():
            return
        signed = self._signed_required and self._signed_ack.isChecked()
        human = self._human_required and self._human_ack.isChecked()
        self.set_busy("preparing")
        self.export_requested.emit(signed, human)

    def _request_summary_save(self) -> None:
        if not self._save.isEnabled():
            return
        self._saving = True
        self.set_busy("saving_summary", cancelable=True)
        self.summary_save_requested.emit()

    def _request_compression(self) -> None:
        if not self._compression.isEnabled():
            return
        signed = self._signed_required and self._signed_ack.isChecked()
        human = self._human_required and self._human_ack.isChecked()
        self.set_busy("preparing")
        self.compression_requested.emit(signed, human)

    def _request_cancel(self) -> None:
        if not self._busy:
            return
        if not self._cancelable or self._cancellation_requested:
            self._set_status("Waiting for the active operation to finish safely.", assertive=True)
            return
        self._cancellation_requested = True
        self._update_actions()
        self._set_status(
            "Cancellation requested. Waiting for the final result and known destination inventory.",
            assertive=True,
        )
        self.cancel_requested.emit()

    def _update_actions(self, *_ignored: object) -> None:
        editable = not self._busy and not self._terminal
        self._mode.setEnabled(editable)
        self._refresh.setEnabled(editable)
        self._human_ack.setVisible(self._human_required and not self._terminal)
        self._human_label.setVisible(self._human_required and not self._terminal)
        self._human_guidance.setVisible(self._human_required and not self._terminal)
        self._signed_ack.setVisible(self._signed_required and not self._terminal)
        self._signed_label.setVisible(self._signed_required and not self._terminal)
        self._signed_guidance.setVisible(self._signed_required and not self._terminal)
        self._human_ack.setEnabled(editable and self._review_ready)
        self._signed_ack.setEnabled(editable and self._review_ready)
        self._export.setEnabled(
            editable
            and self._review_ready
            and self._export_allowed
            and (not self._human_required or self._human_ack.isChecked())
            and (not self._signed_required or self._signed_ack.isChecked())
        )
        self._export.setVisible(not self._terminal)
        self._compression.setEnabled(
            editable
            and self._review_ready
            and self._compression_allowed
            and (not self._human_required or self._human_ack.isChecked())
            and (not self._signed_required or self._signed_ack.isChecked())
        )
        self._compression.setVisible(not self._terminal)
        self._refresh.setVisible(not self._terminal)
        self._save.setVisible(self._terminal and self._save_available)
        self._save.setEnabled(self._terminal and self._save_available and not self._busy)
        self._cancel.setVisible(self._busy)
        self._cancel.setEnabled(
            self._busy and self._cancelable and not self._cancellation_requested
        )
        self._close.setEnabled(not self._busy)

    def _set_status(self, message: str, *, assertive: bool = False) -> None:
        self._status.setText(message)
        self._status.setAccessibleDescription(message)
        event = QAccessibleAnnouncementEvent(self._status, message)
        event.setPoliteness(
            QAccessible.AnnouncementPoliteness.Assertive
            if assertive
            else QAccessible.AnnouncementPoliteness.Polite
        )
        QAccessible.updateAccessibility(event)

    def _focus(self, widget: QWidget) -> None:
        widget.setFocus(Qt.FocusReason.OtherFocusReason)
        self._scroll.ensureWidgetVisible(widget, 8, 8)

    @staticmethod
    def _label(text: str, name: str, accessible_name: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName(name)
        label.setAccessibleName(accessible_name)
        label.setAccessibleDescription(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        return label

    @staticmethod
    def _button(text: str, name: str, description: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(name)
        button.setAccessibleName(text)
        button.setAccessibleDescription(description)
        button.setAutoDefault(False)
        button.setDefault(False)
        return button


__all__ = ("PackageExportDialog",)

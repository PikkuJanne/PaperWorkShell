"""Accessible, explicit review and terminal summary for package exports."""

from __future__ import annotations

from itertools import pairwise
from typing import Final

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
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

from paperworkshell.app.package_pdf_export import (
    PackagePdfCandidateError,
    PackagePdfCandidateErrorCode,
)
from paperworkshell.app.package_workflow import PackageWorkflowError, PackageWorkflowErrorCode
from paperworkshell.domain.package_export_errors import PackageExportError, PackageExportErrorCode
from paperworkshell.domain.package_summary import PackageSummary, PackageSummaryStatus
from paperworkshell.domain.resource_policy import NORMAL_RESOURCE_POLICY_V1
from paperworkshell.localization.catalog import mark, tr
from paperworkshell.preflight.portal_constraints import PortalOutputMode
from paperworkshell.ui.announcements import ScreenReaderAnnouncer
from paperworkshell.ui.localization_layout import adapt_localized_content
from paperworkshell.ui.presentation import set_static_busy
from paperworkshell.ui.wheel_safe import WheelSafeComboBox as QComboBox

_MODE_TEXT: Final[dict[PortalOutputMode, str]] = {
    PortalOutputMode.COMBINED_PDF: mark("Combined PDF"),
    PortalOutputMode.SEPARATE_PDFS: mark("Separate PDFs"),
    PortalOutputMode.ZIP: mark("ZIP package"),
}
_PROGRESS_TEXT: Final[dict[str, str]] = {
    "preparing": mark("Preparing package review"),
    "inspecting_pdfs": mark("Inspecting PDFs for package review"),
    "generating_pdfs": mark("Creating and verifying PDFs"),
    "packaging_zip": mark("Creating and verifying the ZIP package"),
    "checking_constraints": mark("Checking actual output constraints"),
    "committing": mark("Publishing verified outputs"),
    "complete": mark("Finishing package export"),
    "saving_summary": mark("Saving the displayed summary"),
}
_RESULT_TEXT: Final[dict[PackageSummaryStatus, str]] = {
    PackageSummaryStatus.COMMITTED: (
        mark(
            "Package export complete. Review the saved files and remaining human checks in the summary."
        )
    ),
    PackageSummaryStatus.CONSTRAINTS_BLOCKED: (
        mark(
            "Package export blocked by constraints. Review the summary, then close this dialog to adjust the case or constraints before trying again."
        )
    ),
    PackageSummaryStatus.NOT_COMMITTED: (
        mark(
            "Package export did not publish the planned outputs. Review the summary and choose a new attempt when the cause is resolved."
        )
    ),
    PackageSummaryStatus.PARTIAL: (
        mark(
            "Package export is incomplete: some outputs were saved. Check the exact saved-file inventory before retrying in a new destination folder."
        )
    ),
    PackageSummaryStatus.UNCERTAIN: (
        mark(
            "Package export could not confirm the complete destination state. Inspect the destination and the summary before retrying in a new folder."
        )
    ),
}
_FOCUS_STYLE: Final = "QLabel:focus { border: 2px solid palette(highlight); padding: 3px; }"
_FAILURE_TEXT: Final[dict[str, str]] = {
    **{code.value: PackageWorkflowError(code).safe_message for code in PackageWorkflowErrorCode},
    **{
        code.value: PackageExportError(code, "package.ui").safe_message
        for code in PackageExportErrorCode
    },
    **{
        code.value: PackagePdfCandidateError(code, "package.ui").safe_message
        for code in PackagePdfCandidateErrorCode
    },
}
_FORMAT_RECOVERY: Final = mark(
    "Close this dialog to review the output format and page processing. Check requested filenames in Constraints when changing the format."
)
_PLAN_RECOVERY: Final = mark(
    "Close this dialog to review the current case, output format, and requested filenames in Constraints before reopening package review."
)
_LIMIT_RECOVERY: Final = mark(
    "Review package size, page counts, and constraints before a new attempt within the processing limits."
)
_FAILURE_RECOVERY: Final[dict[str, str]] = {
    PackageExportErrorCode.PDF_FAILED.value: _FORMAT_RECOVERY,
    PackagePdfCandidateErrorCode.UNSUPPORTED_OVERLAY_TRANSFORM.value: _FORMAT_RECOVERY,
    PackageExportErrorCode.PLAN_INVALID.value: _PLAN_RECOVERY,
    PackageExportErrorCode.PLAN_STALE.value: _PLAN_RECOVERY,
    PackageExportErrorCode.LIMIT_EXCEEDED.value: _LIMIT_RECOVERY,
    PackagePdfCandidateErrorCode.LIMIT_EXCEEDED.value: _LIMIT_RECOVERY,
}


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
    text_companion_requested = Signal()
    deletion_requested = Signal()

    def __init__(
        self,
        allowed_modes: tuple[PortalOutputMode, ...],
        parent: QWidget | None = None,
        *,
        initial_mode: PortalOutputMode | None = None,
        input_labels: tuple[str, ...] = (),
    ) -> None:
        if (
            type(allowed_modes) is not tuple
            or not allowed_modes
            or any(type(mode) is not PortalOutputMode for mode in allowed_modes)
            or len(set(allowed_modes)) != len(allowed_modes)
            or (initial_mode is not None and initial_mode not in allowed_modes)
            or type(input_labels) is not tuple
            or len(input_labels) > NORMAL_RESOURCE_POLICY_V1.maximum_page_count
            or any(type(label) is not str or not label.strip() for label in input_labels)
        ):
            raise ValueError("Package export requires distinct allowed output modes.")
        super().__init__(parent)
        self._announcer = ScreenReaderAnnouncer(self)
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
        self._post_export_available = True

        self.setObjectName("packageExportDialog")
        self.setWindowTitle(tr("Review and export package"))
        self.setAccessibleName(tr("Review and export package"))
        self.setAccessibleDescription(
            tr(
                "Choose an allowed output format, review planned files and human checks, then explicitly export. The summary is selectable plain text. Close is the safe default."
            )
        )
        self.setModal(True)

        content = QWidget()
        content.setObjectName("packageExportContent")
        content_layout = QVBoxLayout(content)
        guidance = self._label(
            tr(
                "Review file names, page counts, categories, machine checks, and required human review. Actual sizes are known after the files are created and verified. These checks do not guarantee portal acceptance or legal sufficiency."
            ),
            "packageExportGuidance",
            tr("Package review guidance"),
        )
        content_layout.addWidget(guidance)

        input_title = tr("Files and pages in case order")
        input_heading = QLabel(input_title)
        input_heading.setWordWrap(True)
        input_heading.setVisible(bool(input_labels))
        self._input_names = QPlainTextEdit()
        self._input_names.setObjectName("packageExportInputNames")
        self._input_names.setAccessibleName(input_title)
        self._input_names.setAccessibleDescription(
            tr(
                "Saved case page order. See the package summary below for the planned files and assigned categories."
            )
        )
        self._input_names.setReadOnly(True)
        self._input_names.setTabChangesFocus(True)
        self._input_names.setMinimumHeight(80)
        self._input_names.setMaximumHeight(140)
        self._input_names.setPlainText("\n".join(input_labels))
        self._input_names.setVisible(bool(input_labels))
        input_heading.setBuddy(self._input_names)
        content_layout.addWidget(input_heading)
        content_layout.addWidget(self._input_names)

        mode_label = QLabel(tr("Output &format"))
        self._mode = QComboBox()
        self._mode.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._mode.setMinimumContentsLength(8)
        self._mode.setObjectName("packageExportModeCombo")
        self._mode.setAccessibleName(tr("Package output format"))
        self._mode.setAccessibleDescription(
            tr(
                "Choose a format allowed by the saved case constraints. Changing the format requires a fresh review and clears local acknowledgements."
            )
        )
        for mode in allowed_modes:
            self._mode.addItem(tr(_MODE_TEXT[mode]), mode.value)
        if initial_mode is not None:
            self._mode.setCurrentIndex(allowed_modes.index(initial_mode))
        mode_label.setBuddy(self._mode)
        content_layout.addWidget(mode_label)
        content_layout.addWidget(self._mode)
        self._refresh = self._button(
            tr("Refresh\nreview"),
            "packageExportRefreshButton",
            tr("Request a fresh review of the current case and selected output format."),
        )
        self._refresh.setAccessibleName(tr("Refresh review"))
        self._refresh.clicked.connect(self._request_refresh)
        content_layout.addWidget(self._refresh)

        summary_label = QLabel(tr("Package &summary"))
        summary_label.setWordWrap(True)
        self._text = QPlainTextEdit()
        self._text.setObjectName("packageExportSummaryText")
        self._text.setAccessibleName(tr("Package summary plain text"))
        self._text.setAccessibleDescription(
            tr(
                "Read-only, selectable plain text. Use arrow keys to read, Control+A to select all, and Control+C to copy. Control+Home and Control+End move to the start and end. Tab moves to the next control."
            )
        )
        self._text.setReadOnly(True)
        self._text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self._text.setTabChangesFocus(True)
        self._text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._text.setMinimumHeight(220)
        self._text.setPlainText(tr("A package review has not been prepared yet."))
        summary_label.setBuddy(self._text)
        content_layout.addWidget(summary_label)
        content_layout.addWidget(self._text, 1)

        self._human_ack = QCheckBox()
        self._human_ack.setObjectName("packageExportHumanReviewCheck")
        self._human_ack.setAccessibleName(tr("Confirm human review of this package"))
        self._human_ack.setAccessibleDescription(
            tr(
                "Confirm only after reviewing the listed warnings and required human checks. This records your acknowledgement for this attempt; it does not establish eligibility, legal sufficiency, OCR correctness, or portal acceptance."
            )
        )
        self._human_ack.toggled.connect(self._update_actions)
        self._human_label = self._label(
            tr("I reviewed the listed warnings and human checks"),
            "packageExportHumanReviewLabel",
            tr("Human review confirmation"),
        )
        self._human_label.setBuddy(self._human_ack)
        human_row = QHBoxLayout()
        human_row.addWidget(self._human_ack, 0, Qt.AlignmentFlag.AlignTop)
        human_row.addWidget(self._human_label, 1)
        content_layout.addLayout(human_row)
        self._human_guidance = self._label(
            tr(
                "Confirm only after reviewing the listed warnings and required human checks for this package. This confirmation applies to this attempt."
            ),
            "packageExportHumanReviewGuidance",
            tr("Human review acknowledgement instructions"),
        )
        content_layout.addWidget(self._human_guidance)
        self._signed_ack = QCheckBox()
        self._signed_ack.setObjectName("packageExportUnsignedDerivativeCheck")
        self._signed_ack.setAccessibleName(tr("Confirm creation of unsigned derivatives"))
        self._signed_ack.setAccessibleDescription(
            tr(
                "The exported modified files will be unsigned derivatives. Original digital signatures do not remain valid on the exported modified files. This action does not validate a signature or create a digital or qualified electronic signature."
            )
        )
        self._signed_ack.toggled.connect(self._update_actions)
        self._signed_label = self._label(
            tr("Create unsigned derivatives from digitally signed sources"),
            "packageExportUnsignedDerivativeLabel",
            tr("Unsigned derivative confirmation"),
        )
        self._signed_label.setBuddy(self._signed_ack)
        signed_row = QHBoxLayout()
        signed_row.addWidget(self._signed_ack, 0, Qt.AlignmentFlag.AlignTop)
        signed_row.addWidget(self._signed_label, 1)
        content_layout.addLayout(signed_row)
        self._signed_guidance = self._label(
            tr(
                "Digitally signed sources will produce unsigned derivatives. The original signatures do not remain valid on the modified exported files. Original source files remain unchanged."
            ),
            "packageExportUnsignedDerivativeGuidance",
            tr("Unsigned derivative consequence"),
        )
        content_layout.addWidget(self._signed_guidance)

        self._status = self._label(
            tr("Prepare a package review before exporting."),
            "packageExportStatus",
            tr("Package export status and recovery"),
        )
        self._status.setAccessibleName(
            tr("Package export status: {value1}", value1=self._status.text())
        )
        self._status.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._status.setFrameShape(QFrame.Shape.StyledPanel)
        self._status.setStyleSheet(_FOCUS_STYLE)
        self._progress = QProgressBar()
        self._progress.setObjectName("packageExportProgress")
        self._progress.setAccessibleName(tr("Package operation progress"))
        self._progress.setAccessibleDescription(tr("Package operation has not started."))
        self._progress.hide()
        self._export = self._button(
            tr("Export\npackage"),
            "packageExportStartButton",
            tr(
                "Choose the destination and request the reviewed package. Every required PDF is verified before publication. Existing files are not replaced."
            ),
        )
        self._export.setAccessibleName(tr("Export package"))
        self._export.clicked.connect(self._request_export)
        content_layout.addWidget(self._export)
        self._compression = self._button(
            tr("Preview size\nreduction"),
            "packageExportCompressionButton",
            tr(
                "Choose a destination and prepare a private size-reduction preview. Review actual sizes and affected pages before explicitly publishing the selected candidate."
            ),
        )
        self._compression.setAccessibleName(tr("Preview size reduction"))
        self._compression.clicked.connect(self._request_compression)
        content_layout.addWidget(self._compression)
        self._save = self._button(
            tr("Save summary"),
            "packageExportSaveSummaryButton",
            tr(
                "Choose where to save exactly the displayed summary as plain text. The summary contains output names and category labels; review it before sharing."
            ),
        )
        self._save.clicked.connect(self._request_summary_save)
        content_layout.addWidget(self._save)
        self._text_privacy = QCheckBox()
        self._text_privacy.setObjectName("packageTextPrivacyCheck")
        self._text_privacy.setAccessibleName(tr("Allow a separate plain-text companion"))
        self._text_privacy.setAccessibleDescription(
            tr(
                "The text may contain personal information. It remains outside the case after case deletion. Review OCR errors and protect or delete the companion separately."
            )
        )
        self._text_privacy.toggled.connect(self._update_actions)
        self._text_privacy_label = self._label(
            tr(
                "I understand that the optional text companion may contain personal information and remains after case deletion. I will review OCR errors and protect or delete this separate file."
            ),
            "packageTextPrivacyLabel",
            tr("Text companion privacy warning"),
        )
        self._text_privacy_label.setBuddy(self._text_privacy)
        privacy_row = QHBoxLayout()
        privacy_row.addWidget(self._text_privacy, 0, Qt.AlignmentFlag.AlignTop)
        privacy_row.addWidget(self._text_privacy_label, 1)
        content_layout.addLayout(privacy_row)
        self._companion = self._button(
            tr("Save text companion"),
            "packageTextCompanionButton",
            tr(
                "Save available text from the verified exported pages in package order. Image-only pages may have no text. This does not run OCR. Check the privacy acknowledgement first."
            ),
        )
        self._companion.clicked.connect(self._request_companion)
        content_layout.addWidget(self._companion)
        self._delete_case = self._button(
            tr("Review case deletion"),
            "packageDeleteCaseButton",
            tr(
                "Check the saved export and review the case storage inventory. A separate confirmation is required before any deletion. Saved exports and external source files remain."
            ),
        )
        self._delete_case.clicked.connect(self._request_deletion)
        content_layout.addWidget(self._delete_case)
        self._cancel = self._button(
            tr("Cancel"),
            "packageExportCancelButton",
            tr(
                "Request cancellation and wait for the final result. Files already saved may remain; the final summary states the known destination inventory."
            ),
        )
        self._cancel.clicked.connect(self._request_cancel)
        content_layout.addWidget(self._cancel)
        self._scroll = QScrollArea()
        self._scroll.setObjectName("packageExportScrollArea")
        self._scroll.setAccessibleName(tr("Package review and export controls"))
        self._scroll.setAccessibleDescription(
            tr("Scrollable format, summary, acknowledgement, export, and cancellation controls.")
        )
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._scroll.setWidget(content)
        adapt_localized_content(self._scroll)
        self._close = self._button(
            tr("Close"),
            "packageExportCloseButton",
            tr(
                "Close this review. During an operation Escape requests cancellation and keeps the dialog open until the result is known."
            ),
        )
        self._close.setDefault(True)
        self._close.setAutoDefault(True)
        self._close.clicked.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self._scroll, 1)
        layout.addWidget(self._status)
        layout.addWidget(self._progress)
        layout.addWidget(self._close)
        tab_chain: tuple[QWidget, ...] = (
            self._close,
            self._scroll,
            self._input_names,
            self._mode,
            self._refresh,
            self._text,
            self._human_ack,
            self._signed_ack,
            self._status,
            self._export,
            self._compression,
            self._save,
            self._text_privacy,
            self._companion,
            self._delete_case,
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
            tr(
                "Package review ready. Read the summary and complete any required review steps before choosing Export package."
            )
            if export_allowed
            else tr(
                "The package has a size constraint to resolve. Read the summary and complete any required review steps before choosing Preview size reduction."
            )
            if compression_allowed
            else tr(
                "Package needs attention before export. Read the summary, then close this dialog to resolve blocking items and refresh the review."
            ),
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
            tr(_RESULT_TEXT[summary.status]),
            assertive=summary.status is not PackageSummaryStatus.COMMITTED,
        )
        self._focus(self._text)

    def set_busy(self, phase: str = "preparing", *, cancelable: bool = True) -> None:
        """Display only known content-free progress text, never a caller's message."""

        if type(phase) is not str or type(cancelable) is not bool:
            raise ValueError("Package progress requires a phase and cancellation state.")
        self._busy = True
        self._announcer.reset_progress()
        self._cancelable = cancelable
        self._cancellation_requested = False
        set_static_busy(self._progress)
        self._progress.show()
        label = _PROGRESS_TEXT.get(phase, mark("Working on the package"))
        self._progress.setAccessibleDescription(tr(label))
        self._update_actions()
        self._set_status(
            tr("{value1}. Keep this dialog open until the result is known.", value1=tr(label))
        )
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
        label = _PROGRESS_TEXT.get(phase, mark("Working on the package"))
        self._progress.setRange(0, total)
        self._progress.setValue(completed)
        self._progress.setFormat(tr("%v of %m"))
        self._progress.setAccessibleDescription(
            tr("{value1}: {value2} of {value3}.", value1=tr(label), value2=completed, value3=total)
        )
        message = tr(
            "{value1}: {value2} of {value3}.", value1=tr(label), value2=completed, value3=total
        )
        self._set_status(message, announce=False)
        self._announcer.progress(message, key=label, completed=completed, total=total)
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
            tr(
                "Package export did not finish safely. The destination state is not established by this message. Inspect the destination before retrying in a new folder. Close and reopen package review to start a new attempt."
            )
            if phase == "export"
            else tr(
                "The package review could not be prepared. Close this dialog to check the case and saved constraints, or choose Refresh review to try again."
            )
        )
        if type(code) is str:
            known = _FAILURE_TEXT.get(code)
            if known is not None:
                recovery = _FAILURE_RECOVERY.get(code)
                prefix = (
                    tr(known)
                    if recovery is None
                    else tr("{message} {recovery}", message=tr(known), recovery=tr(recovery))
                )
                message = tr("{prefix} {message}", prefix=prefix, message=message)
        self._set_status(message, assertive=True)
        self._focus(self._refresh if phase == "review" else self._close)

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
            message = tr("The displayed summary was saved. Review it before sharing.")
        else:
            prefix = (
                tr("Summary saving stopped after a cancellation request.")
                if cancelled
                else tr("Summary saving was not confirmed.")
            )
            message = tr(
                "{value1} The package result above is unchanged. Check the selected destination before trying a new filename, or select and copy the displayed text.",
                value1=prefix,
            )
        self._set_status(message, assertive=not succeeded)
        self._focus(self._save)

    @property
    def text_privacy_confirmed(self) -> bool:
        return self._text_privacy.isChecked()

    def complete_post_export(self, message: str, *, failed: bool = False) -> None:
        """Retain the verified package summary after an optional operation."""
        self._busy = False
        self._saving = False
        self._progress.hide()
        self._update_actions()
        self._set_status(message, assertive=failed)
        self._focus(self._status)

    def disable_post_export(self) -> None:
        """Keep the terminal summary readable after case closure or deletion."""
        self._post_export_available = False
        self._save_available = False
        self._text_privacy.setChecked(False)
        self._update_actions()

    def clear_post_export_consent(self) -> None:
        self._text_privacy.setChecked(False)

    def _request_companion(self) -> None:
        if self._companion.isEnabled():
            self.set_busy("preparing", cancelable=True)
            self.text_companion_requested.emit()

    def _request_deletion(self) -> None:
        if self._delete_case.isEnabled():
            self.set_busy("preparing", cancelable=True)
            self.deletion_requested.emit()

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
        self._text.setPlainText(tr("Preparing a fresh review for the selected output format."))

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
        if self._cancellation_requested:
            return
        if not self._cancelable:
            self._set_status(
                tr("Waiting for the active operation to finish safely."), assertive=True
            )
            return
        self._cancellation_requested = True
        self._update_actions()
        self._set_status(
            tr(
                "Cancellation requested. Waiting for the final result and known destination inventory."
            ),
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
        committed = (
            self._terminal
            and self._post_export_available
            and self._summary is not None
            and self._summary.status is PackageSummaryStatus.COMMITTED
        )
        self._text_privacy.setVisible(committed)
        self._text_privacy_label.setVisible(committed)
        self._text_privacy.setEnabled(committed and not self._busy)
        self._companion.setVisible(committed)
        self._companion.setEnabled(committed and not self._busy and self._text_privacy.isChecked())
        self._delete_case.setVisible(committed)
        self._delete_case.setEnabled(committed and not self._busy)
        self._cancel.setVisible(self._busy)
        self._cancel.setEnabled(
            self._busy and self._cancelable and not self._cancellation_requested
        )
        self._close.setEnabled(not self._busy)

    def _set_status(self, message: str, *, assertive: bool = False, announce: bool = True) -> None:
        message = tr(message)
        self._status.setText(message)
        self._status.setAccessibleName(tr("Package export status: {value1}", value1=message))
        self._status.setAccessibleDescription(message)
        if announce:
            self._announcer.announce(message, assertive=assertive)

    def _focus(self, widget: QWidget) -> None:
        widget.setFocus(Qt.FocusReason.OtherFocusReason)
        self._scroll.ensureWidgetVisible(widget, 8, 8)

    @staticmethod
    def _label(text: str, name: str, accessible_name: str) -> QLabel:
        label = QLabel(tr(text))
        label.setObjectName(name)
        label.setAccessibleName(f"{accessible_name}: {text}")
        label.setAccessibleDescription(tr(text))
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        return label

    @staticmethod
    def _button(text: str, name: str, description: str) -> QPushButton:
        button = QPushButton(tr(text))
        button.setObjectName(name)
        button.setAccessibleName(tr(text))
        button.setAccessibleDescription(tr(description))
        button.setAutoDefault(False)
        button.setDefault(False)
        return button


__all__ = ("PackageExportDialog",)

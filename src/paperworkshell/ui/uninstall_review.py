"""Interactive current-user case choices before an installer removes the app.

Entry point: bundled Python ``-I -B -m paperworkshell.ui.uninstall_review``.
No command-line paths or destructive switches are accepted. Exit 0 permits
application removal after an explicit user action; 1 cancels it; 2 reports an
initialization/argument failure. Silent uninstallation skips this helper and
preserves data. This helper never removes installation files itself.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence
from itertools import pairwise
from threading import Event

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from paperworkshell.app.uninstall_review import UninstallReviewService
from paperworkshell.domain.uninstall_review import (
    UninstallDeletionConfirmation,
    UninstallRemovalResult,
    UninstallReview,
    UninstallReviewError,
    UninstallReviewErrorCode,
)
from paperworkshell.localization.catalog import tr
from paperworkshell.storage.uninstall_review import (
    current_user_uninstall_store,
    uninstall_application_guard,
)
from paperworkshell.ui.announcements import ScreenReaderAnnouncer
from paperworkshell.ui.application import configure_ui_locale
from paperworkshell.ui.localization_layout import adapt_localized_content
from paperworkshell.ui.trace_presentation import install_trace_presentation


class _OperationThread(QThread):
    """One bounded operation; no executor, request queue or document worker."""

    progress = Signal(int, int)

    def __init__(
        self,
        operation: Callable[[], UninstallReview | UninstallRemovalResult],
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self._operation = operation
        self.outcome: UninstallReview | UninstallRemovalResult | None = None
        self.error: UninstallReviewErrorCode | None = None

    def run(self) -> None:
        try:
            self.outcome = self._operation()
        except UninstallReviewError as error:
            self.error = error.code
        except Exception:
            self.error = UninstallReviewErrorCode.INSPECTION_FAILED


def _checkbox(text: str, name: str, layout: QVBoxLayout) -> QCheckBox:
    check = QCheckBox()
    check.setObjectName(name)
    check.setAccessibleName(text)
    check.setAccessibleDescription(text)
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setBuddy(check)
    row = QHBoxLayout()
    row.addWidget(check, 0, Qt.AlignmentFlag.AlignTop)
    row.addWidget(label, 1)
    layout.addLayout(row)
    return check


class UninstallReviewDialog(QDialog):
    """Preserve by default; preview and a separate acknowledgement gate deletion."""

    def __init__(self, service: UninstallReviewService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._review: UninstallReview | None = None
        self._thread: _OperationThread | None = None
        self._cancellation = Event()
        self._deletion_attempted = False
        self._announcer = ScreenReaderAnnouncer(self)
        self.setObjectName("uninstallReviewDialog")
        self.setWindowTitle(tr("Remove PaperWorkShell"))
        self.setAccessibleName(tr("Review application removal and local case choices"))
        self.setAccessibleDescription(
            tr(
                "Keeping cases is the default. Case deletion requires review and separate confirmation."
            )
        )
        self.setModal(True)
        content = QWidget()
        rows = QVBoxLayout(content)
        guidance = QLabel(
            tr(
                "Application removal keeps your cases unless you explicitly choose deletion below. External source documents, saved exports and support bundles remain. Unknown or unsafe data remains. Case deletion cannot be undone and does not guarantee forensic erasure."
            )
        )
        guidance.setTextFormat(Qt.TextFormat.PlainText)
        guidance.setWordWrap(True)
        rows.addWidget(guidance)
        self._choose = _checkbox(
            tr("Review deletion of my PaperWorkShell-managed cases on this computer."),
            "uninstallDeleteChoice",
            rows,
        )
        self._review_button = QPushButton(tr("Review case deletion"))
        self._review_button.setObjectName("uninstallInspectButton")
        self._review_button.setAccessibleName(tr("Inspect cases before deciding about deletion"))
        self._review_button.setAutoDefault(False)
        self._review_button.setEnabled(False)
        self._review_button.clicked.connect(self._prepare)
        rows.addWidget(self._review_button)
        self._inventory = QPlainTextEdit()
        self._inventory.setObjectName("uninstallCaseInventory")
        self._inventory.setAccessibleName(tr("Case deletion inventory and current result"))
        self._inventory.setReadOnly(True)
        self._inventory.setTabChangesFocus(True)
        self._inventory.setMinimumHeight(140)
        self._inventory.setPlainText(tr("No case deletion has been requested."))
        rows.addWidget(self._inventory, 1)
        self._confirm = _checkbox(
            tr(
                "I confirm deleting the reviewed managed cases and understand this cannot be undone."
            ),
            "uninstallDeletionConfirmation",
            rows,
        )
        self._confirm.setEnabled(False)
        self._remove = QPushButton(tr("Delete reviewed cases"))
        self._remove.setObjectName("uninstallDeleteButton")
        self._remove.setAccessibleName(tr("Delete only the reviewed application-managed cases"))
        self._remove.setAutoDefault(False)
        self._remove.setEnabled(False)
        self._remove.clicked.connect(self._delete)
        rows.addWidget(self._remove)
        area = QScrollArea()
        area.setObjectName("uninstallReviewScrollArea")
        area.setAccessibleName(tr("Application removal review controls"))
        area.setWidgetResizable(True)
        area.setWidget(content)
        adapt_localized_content(area)
        self._keep = QPushButton(tr("Keep cases and continue application removal"))
        self._keep.setObjectName("uninstallContinueButton")
        self._keep.setAccessibleName(tr("Continue application removal with case data preserved"))
        self._keep.setDefault(True)
        self._keep.clicked.connect(self._continue)
        self._cancel = QPushButton(tr("Cancel application removal"))
        self._cancel.setObjectName("uninstallCancelButton")
        self._cancel.setAccessibleName(
            tr("Cancel application removal or stop after the current case")
        )
        self._cancel.setAutoDefault(False)
        self._cancel.clicked.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(area, 1)
        layout.addWidget(self._keep)
        layout.addWidget(self._cancel)
        self._choose.toggled.connect(self._choice_changed)
        self._confirm.toggled.connect(self._confirmation_changed)
        controls = (
            self._keep,
            self._choose,
            self._review_button,
            self._inventory,
            self._confirm,
            self._remove,
            self._cancel,
        )
        for first, second in pairwise(controls):
            QWidget.setTabOrder(first, second)
        self.resize(680, 600)
        self._keep.setFocus()

    def _status(self, text: str, *, error: bool = False) -> None:
        self._inventory.setPlainText(text)
        self._announcer.announce(text, assertive=error)

    def _choice_changed(self, checked: bool) -> None:
        self._service.discard()
        self._review = None
        self._confirm.setChecked(False)
        self._confirm.setEnabled(False)
        self._remove.setEnabled(False)
        self._review_button.setEnabled(checked and not self._deletion_attempted)

    def _confirmation_changed(self, checked: bool) -> None:
        self._remove.setEnabled(checked and self._review is not None and self._thread is None)

    def _start(self, operation: Callable[[], UninstallReview | UninstallRemovalResult]) -> None:
        if self._thread is not None:
            return
        self._cancellation.clear()
        for control in (self._choose, self._review_button, self._confirm, self._remove, self._keep):
            control.setEnabled(False)
        self._thread = _OperationThread(operation, self)
        self._thread.progress.connect(self._progress)
        self._thread.finished.connect(self._finished)
        self._thread.start()
        self._cancel.setFocus()

    def _prepare(self) -> None:
        if not self._choose.isChecked() or self._deletion_attempted:
            return
        self._status(tr("Inspecting local cases. You can cancel without deleting anything."))
        self._start(lambda: self._service.prepare(cancellation=self._cancellation.is_set))

    def _delete(self) -> None:
        review = self._review
        if review is None or not self._confirm.isChecked() or not self._remove.isEnabled():
            return
        self._review = None
        self._deletion_attempted = True

        def remove() -> UninstallRemovalResult:
            return self._service.confirm(
                review.review_id,
                UninstallDeletionConfirmation.DELETE_REVIEWED_MANAGED_CASES,
                cancellation=self._cancellation.is_set,
                progress=self._emit_progress,
            )

        self._status(tr("Deleting reviewed cases. Cancellation stops before the next case."))
        self._start(remove)

    def _emit_progress(self, completed: int, total: int) -> None:
        thread = self._thread
        if thread is not None:
            thread.progress.emit(completed, total)

    def _progress(self, completed: int, total: int) -> None:
        text = tr(
            "Case deletion attempts finished: {completed} of {total}.",
            completed=completed,
            total=total,
        )
        self._inventory.setPlainText(text)
        self._announcer.progress(text, key="uninstall", completed=completed, total=total)

    def _finished(self) -> None:
        thread = self._thread
        if thread is None:
            return
        self._thread = None
        outcome = thread.outcome
        error = thread.error
        thread.deleteLater()
        self._confirm.setChecked(False)
        self._keep.setEnabled(True)
        self._choose.setEnabled(not self._deletion_attempted)
        self._review_button.setEnabled(self._choose.isChecked() and not self._deletion_attempted)
        if type(outcome) is UninstallReview:
            if self._cancellation.is_set():
                self._service.discard()
                self._status(tr("Case review cancelled. No cases were deleted."))
            else:
                self._review = outcome
                self._confirm.setEnabled(bool(outcome.inventory.cases))
                self._status(
                    tr(
                        "Cases available for deletion: {available}. Unavailable cases to keep: {unavailable}. Deletion includes imported copies, case records, visual marks, OCR derivatives, caches and temporary files. External originals and saved exports remain. Other unrecognized data stays in place.",
                        available=len(outcome.inventory.cases),
                        unavailable=outcome.inventory.unavailable_count,
                    )
                )
        elif type(outcome) is UninstallRemovalResult:
            self._keep.setText(tr("Continue application removal; keep remaining data"))
            self._keep.setAccessibleName(
                tr("Acknowledge the result and continue application removal")
            )
            self._status(
                tr(
                    "Cases fully deleted: {deleted}. Recognized cases with data remaining: {remaining}. Failed or partial attempts: {failed}. Cancellation requested: {cancelled}. Some files may remain after a partial attempt. External originals, saved exports and unrecognized data remain. Continue only after reviewing this result.",
                    deleted=outcome.deleted_count,
                    remaining=outcome.remaining_count,
                    failed=outcome.failed_count,
                    cancelled=tr("Yes") if outcome.cancelled else tr("No"),
                ),
                error=bool(outcome.failed_count),
            )
        elif error is UninstallReviewErrorCode.CANCELLED:
            self._status(tr("Case review cancelled. No cases were deleted."))
        else:
            self._status(
                tr(
                    "Case review could not finish safely. Keep the remaining data and retry from PaperWorkShell before removing the application."
                ),
                error=True,
            )
        self._keep.setFocus()

    def _continue(self) -> None:
        if self._thread is None:
            self._service.discard()
            self.accept()

    def reject(self) -> None:
        if self._thread is not None:
            self._cancellation.set()
            self._status(
                tr("Stopping after the current case. Any completed deletions cannot be undone.")
            )
            return
        self._service.discard()
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._thread is not None:
            self.reject()
            event.ignore()
            return
        super().closeEvent(event)


def main(argv: Sequence[str] | None = None) -> int:
    """A visible current-user review only; no paths or unattended deletion options."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments or os.name != "nt":
        print(
            "ERROR [UNINSTALL_ARGUMENT_INVALID] Interactive Windows review accepts no options.",
            file=sys.stderr,
        )
        return 2
    try:
        with uninstall_application_guard():
            application = QApplication(["PaperWorkShell"])
            install_trace_presentation(application)
            configure_ui_locale(application)
            application.setApplicationName("PaperWorkShell")
            service = UninstallReviewService(current_user_uninstall_store())
            dialog = UninstallReviewDialog(service)
            dialog.finished.connect(application.quit)
            dialog.show()
            application.exec()
            return 0 if dialog.result() == QDialog.DialogCode.Accepted else 1
    except UninstallReviewError as error:
        print(
            f"ERROR [{error.code.value}] Close PaperWorkShell and retry removal.", file=sys.stderr
        )
        return 2
    except Exception:
        print(
            "ERROR [UNINSTALL_INITIALIZATION_FAILED] Keep application data and retry removal.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

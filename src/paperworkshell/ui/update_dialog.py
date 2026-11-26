"""Offline update review launched by the installed native application."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from enum import Enum, auto
from itertools import pairwise
from pathlib import Path
from threading import Event
from typing import Protocol

from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from paperworkshell.domain.update_verification import UpdateErrorCode
from paperworkshell.localization.catalog import tr
from paperworkshell.storage.update_installation import (
    InstallationStatus,
    UpdateInstallation,
    UpdateInstallationError,
    UpdateOffer,
)
from paperworkshell.ui.announcements import ScreenReaderAnnouncer
from paperworkshell.ui.application import configure_ui_locale
from paperworkshell.ui.localization_layout import adapt_localized_content
from paperworkshell.ui.trace_presentation import install_trace_presentation


class _UpdateService(Protocol):
    def status(self) -> InstallationStatus: ...

    def inspect_offer(self, directory: Path) -> UpdateOffer: ...

    def apply_offer(self, offer: UpdateOffer, *, cancelled: Callable[[], bool]) -> None: ...

    def restore_previous(self) -> None: ...

    def remove_installed_updates(self) -> None: ...


class _Action(Enum):
    STATUS = auto()
    INSPECT = auto()
    INSTALL = auto()
    RESTORE = auto()
    REMOVE = auto()


class _OperationThread(QThread):
    """Run one update operation and refresh status without blocking the dialog."""

    def __init__(
        self,
        action: _Action,
        work: Callable[[], UpdateOffer | None] | None,
        service: _UpdateService,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.action = action
        self._work = work
        self._service = service
        self.offer: UpdateOffer | None = None
        self.status: InstallationStatus | None = None
        self.error_code: str | None = None

    def run(self) -> None:
        try:
            if self._work is not None:
                self.offer = self._work()
        except UpdateInstallationError as error:
            self.error_code = error.code
        except Exception:
            self.error_code = "UPDATE_OPERATION_FAILED"
        try:
            self.status = self._service.status()
        except Exception:
            return


class UpdateDialog(QDialog):
    """Review one local offer before activating it, with separate recovery actions."""

    def __init__(self, service: _UpdateService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._thread: _OperationThread | None = None
        self._status: InstallationStatus | None = None
        self._offer: UpdateOffer | None = None
        self._cancellation = Event()
        self._announcer = ScreenReaderAnnouncer(self)
        self.setObjectName("updateDialog")
        self.setWindowTitle(tr("PaperWorkShell updates"))
        self.setAccessibleName(tr("PaperWorkShell updates"))
        self.setAccessibleDescription(
            tr("Verify, install, or restore a local PaperWorkShell update.")
        )

        content = QWidget()
        rows = QVBoxLayout(content)
        guidance = QLabel(tr("Choose the folder containing the downloaded PaperWorkShell update."))
        guidance.setTextFormat(Qt.TextFormat.PlainText)
        guidance.setWordWrap(True)
        rows.addWidget(guidance)
        self._choose = self._button(tr("Choose update &folder…"), "updateChooseFolder")
        self._choose.clicked.connect(self._choose_offer)
        rows.addWidget(self._choose)
        self._review = QPlainTextEdit()
        self._review.setObjectName("updateReview")
        self._review.setAccessibleName(tr("Current installation and signed update details"))
        self._review.setReadOnly(True)
        self._review.setTabChangesFocus(True)
        self._review.setMinimumHeight(112)
        rows.addWidget(self._review, 1)
        self._confirm = QCheckBox(tr("I &reviewed this update"))
        self._confirm.setObjectName("updateReviewConfirmation")
        self._confirm.setAccessibleName(tr("Confirm review of the signed update information"))
        self._confirm.toggled.connect(self._refresh_actions)
        rows.addWidget(self._confirm)
        self._install = self._button(tr("&Install update"), "updateInstall")
        self._install.clicked.connect(self._install_offer)
        rows.addWidget(self._install)
        self._restore = self._button(tr("&Restore previous version"), "updateRestore")
        self._restore.clicked.connect(self._restore_previous)
        rows.addWidget(self._restore)
        self._remove = self._button(tr("Re&move installed updates"), "updateRemove")
        self._remove.clicked.connect(self._remove_updates)
        rows.addWidget(self._remove)
        area = QScrollArea()
        area.setAccessibleName(tr("Update review controls"))
        area.setWidgetResizable(True)
        area.setWidget(content)
        adapt_localized_content(area)

        self._activity = QLabel()
        self._activity.setObjectName("updateActivity")
        self._activity.setTextFormat(Qt.TextFormat.PlainText)
        self._activity.setWordWrap(True)
        self._close = self._button(tr("&Close"), "updateClose")
        self._close.setDefault(True)
        self._close.clicked.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(area, 1)
        layout.addWidget(self._activity)
        layout.addWidget(self._close)
        controls = (
            self._choose,
            self._review,
            self._confirm,
            self._install,
            self._restore,
            self._remove,
            self._close,
        )
        for first, second in pairwise(controls):
            QWidget.setTabOrder(first, second)
        available = self.screen().availableGeometry()
        self.resize(min(640, available.width()), min(560, available.height()))
        self._close.setFocus()
        self._start(_Action.STATUS, tr("Reading the current installation."))

    @staticmethod
    def _button(text: str, name: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(name)
        button.setAccessibleName(text.replace("&", ""))
        button.setAutoDefault(False)
        return button

    def _report(self, message: str, *, error: bool = False) -> None:
        self._activity.setText(message)
        self._announcer.announce(message, assertive=error)

    def _refresh_actions(self) -> None:
        idle = self._thread is None
        status = self._status
        verified = (
            idle and status is not None and status.trust_available and self._offer is not None
        )
        self._choose.setEnabled(idle and status is not None and status.trust_available)
        self._confirm.setEnabled(verified)
        self._install.setEnabled(verified and self._confirm.isChecked())
        self._restore.setEnabled(idle and status is not None and status.can_restore)
        self._remove.setEnabled(idle and status is not None and status.has_staged_updates)
        action = None if self._thread is None else self._thread.action
        cancellable = action in (_Action.INSPECT, _Action.INSTALL)
        close_text = tr("&Cancel operation") if cancellable else tr("&Close")
        self._close.setText(close_text)
        self._close.setAccessibleName(close_text.replace("&", ""))
        self._close.setEnabled(idle or (cancellable and not self._cancellation.is_set()))

    def _refresh_review(self) -> None:
        status = self._status
        if status is None:
            self._review.setPlainText(tr("Current installation details are unavailable."))
            return
        current = tr(
            "Current version: {version}\nCurrent build: {candidate}",
            version=status.active_version,
            candidate=status.active_candidate,
        )
        if self._offer is not None:
            metadata = self._offer.metadata
            current += "\n\n" + tr(
                "Offered version: {version}\nUpdate channel: {channel}\nUpdate build: {candidate}",
                version=metadata.version,
                channel=metadata.channel,
                candidate=metadata.candidate_id,
            )
        self._review.setPlainText(current)

    def _start(
        self,
        action: _Action,
        message: str,
        work: Callable[[], UpdateOffer | None] | None = None,
    ) -> None:
        if self._thread is not None:
            return
        self._offer = None
        self._confirm.setChecked(False)
        self._cancellation.clear()
        self._thread = _OperationThread(action, work, self._service, self)
        self._refresh_actions()
        self._refresh_review()
        self._report(message)
        self._thread.finished.connect(self._finished)
        self._thread.start()
        if self._close.isEnabled():
            self._close.setFocus()

    def _choose_offer(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, tr("Choose the update folder"))
        if directory:
            self._start(
                _Action.INSPECT,
                tr("Verifying the local update. You can cancel before installation."),
                lambda: self._service.inspect_offer(Path(directory)),
            )

    def _install_offer(self) -> None:
        offer = self._offer
        if offer is None or not self._install.isEnabled():
            return
        self._start(
            _Action.INSTALL,
            tr("Checking the update package and preparing installation."),
            lambda: self._service.apply_offer(offer, cancelled=self._cancellation.is_set),
        )

    def _confirm_recovery(self, title: str, question: str) -> bool:
        return (
            QMessageBox.question(
                self,
                title,
                question,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )

    def _restore_previous(self) -> None:
        if self._restore.isEnabled() and self._confirm_recovery(
            tr("Restore previous version"),
            tr("Switch back to the previous PaperWorkShell version? Your saved cases are kept."),
        ):
            self._start(
                _Action.RESTORE,
                tr("Restoring the previous version. Wait for the result."),
                self._service.restore_previous,
            )

    def _remove_updates(self) -> None:
        if self._remove.isEnabled() and self._confirm_recovery(
            tr("Remove installed updates"),
            tr(
                "Return to the original PaperWorkShell installation and remove its local updates? Your saved cases are kept."
            ),
        ):
            self._start(
                _Action.REMOVE,
                tr("Removing local updates. Wait for the result."),
                self._service.remove_installed_updates,
            )

    def _finished(self) -> None:
        thread = self._thread
        if thread is None:
            return
        self._thread = None
        self._status = thread.status
        inspection_cancelled = thread.action is _Action.INSPECT and self._cancellation.is_set()
        cancelled = inspection_cancelled or thread.error_code == "UPDATE_CANCELLED"
        if not inspection_cancelled:
            self._offer = thread.offer
        self._refresh_actions()
        self._refresh_review()

        if cancelled:
            self._report(tr("The update operation was cancelled."))
        elif thread.error_code == UpdateErrorCode.ROLLBACK_FORBIDDEN:
            self._report(
                tr(
                    "This PaperWorkShell update has already been installed or superseded. Choose a newer update."
                ),
                error=True,
            )
        elif thread.error_code is not None:
            self._report(
                tr(
                    "The update operation could not finish safely. Review the current installation before trying again."
                ),
                error=True,
            )
        elif thread.status is None:
            self._report(
                tr(
                    "Current installation details could not be refreshed. Close this window and check again before taking another action."
                ),
                error=True,
            )
        elif thread.action is _Action.STATUS:
            self._report(
                tr("Choose a local update folder to begin.")
                if thread.status.trust_available
                else tr(
                    "This installation has no update verification keys. Existing recovery options remain available."
                )
            )
        elif thread.action is _Action.INSPECT:
            self._report(
                tr("Update information verified. Review the version and channel before installing.")
            )
        elif thread.action is _Action.INSTALL:
            self._report(
                tr(
                    "Update installed. Close this window, then open PaperWorkShell to use the new version."
                )
            )
        elif thread.action is _Action.RESTORE:
            self._report(tr("The previous PaperWorkShell version is active."))
        else:
            self._report(
                tr("Local updates removed. The original PaperWorkShell installation is active.")
            )
        thread.deleteLater()
        if self._offer is not None:
            self._review.setFocus()
        elif thread.error_code is not None and not cancelled and self._choose.isEnabled():
            self._choose.setFocus()
        else:
            self._close.setFocus()

    def reject(self) -> None:
        if self._thread is None:
            super().reject()
        elif self._thread.action in (_Action.INSPECT, _Action.INSTALL):
            if not self._cancellation.is_set():
                self._cancellation.set()
                self._report(tr("Cancellation requested. Wait for the operation to finish safely."))
                self._refresh_actions()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._thread is not None:
            self.reject()
            event.ignore()
            return
        super().closeEvent(event)


def main() -> int:
    """Start the installed, interactive update dialog without path arguments."""
    if len(sys.argv) != 1 or os.name != "nt":
        return 2
    application = QApplication(["PaperWorkShell"])
    install_trace_presentation(application)
    configure_ui_locale(application)
    application.setApplicationName("PaperWorkShell")
    try:
        service = UpdateInstallation.for_installed_runtime()
    except UpdateInstallationError:
        QMessageBox.critical(
            None,
            tr("PaperWorkShell updates"),
            tr(
                "Update controls are unavailable. Close and reopen PaperWorkShell before trying again."
            ),
        )
        return 2
    dialog = UpdateDialog(service)
    dialog.finished.connect(application.quit)
    dialog.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())

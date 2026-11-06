"""Accessible consent, local unlocking, and handover for one assisted case."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import pairwise
from uuid import UUID

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from paperworkshell.domain.assisted_use import (
    ASSISTED_USE_RETENTION_DAYS,
    MAX_ASSISTED_PASSPHRASE_LENGTH,
    MIN_ASSISTED_PASSPHRASE_LENGTH,
    AssistedUseError,
    AssistedUseErrorCode,
)
from paperworkshell.localization.catalog import mark, tr
from paperworkshell.ui.announcements import ScreenReaderAnnouncer
from paperworkshell.ui.localization_layout import adapt_localized_content
from paperworkshell.ui.presentation import adapt_dialog_button_box

_FOCUS_STYLE = """
QLabel:focus, QScrollArea:focus {
    border: 2px solid palette(highlight);
}
"""


def _plain_label(text: str, name: str) -> QLabel:
    label = QLabel(tr(text))
    label.setObjectName(name)
    label.setAccessibleName(tr(text))
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def _password_edit(name: str, accessible_name: str) -> QLineEdit:
    edit = QLineEdit()
    edit.setObjectName(name)
    edit.setAccessibleName(tr(accessible_name))
    edit.setAccessibleDescription(
        tr(
            "Use {value1} to {value2} characters. Keep this local passphrase for reopening this assisted case.",
            value1=MIN_ASSISTED_PASSPHRASE_LENGTH,
            value2=MAX_ASSISTED_PASSPHRASE_LENGTH,
        )
    )
    edit.setEchoMode(QLineEdit.EchoMode.Password)
    edit.setMaxLength(MAX_ASSISTED_PASSPHRASE_LENGTH)
    edit.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
    return edit


class AssistedConsentDialog(QDialog):
    """Require a deliberate bounded consent choice before case creation/import."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("assistedConsentDialog")
        self.setWindowTitle(tr("Start assisted case"))
        self.setAccessibleName(tr("Consent and retention for an assisted case"))
        self.setAccessibleDescription(
            tr(
                "Review local storage, expiry, and locking before adding any documents. Nothing is imported by this dialog."
            )
        )
        self.setModal(True)
        self.resize(620, 560)
        self._announcer = ScreenReaderAnnouncer(self)
        self._initial_focus_pending = True

        guidance = _plain_label(
            tr(
                "Before adding documents, ask the person receiving help to agree to this local case. PaperWorkShell keeps copies on this computer and does not change the originals. No account or upload is needed. Exports are separate files and remain after the case is closed or removed."
            ),
            "assistedConsentGuidance",
        )
        retention_guidance = _plain_label(
            tr(
                "Choose when this case expires. At expiry it locks and cannot be used again; its stored files remain until the case is explicitly removed. Expiry does not automatically delete files. Removal does not guarantee forensic erasure."
            ),
            "assistedRetentionGuidance",
        )
        lock_guidance = _plain_label(
            tr(
                "The case locks after five minutes without activity and must be unlocked after reopening. Choose a local passphrase and keep it for handover. This screen lock hides the case in PaperWorkShell; it does not encrypt files or replace signing out of Windows on a shared computer. A forgotten passphrase cannot be recovered."
            ),
            "assistedLockGuidance",
        )
        self._retention = QComboBox()
        self._retention.setObjectName("assistedRetentionDays")
        self._retention.setAccessibleName(tr("Case expires after"))
        self._retention.setAccessibleDescription(
            tr("Choose one day, seven days, or thirty days after consent. The deadline is fixed.")
        )
        for days in ASSISTED_USE_RETENTION_DAYS:
            self._retention.addItem(
                tr("{value1} day", value1=days) if days == 1 else tr("{value1} days", value1=days),
                days,
            )
        self._retention.setCurrentIndex(ASSISTED_USE_RETENTION_DAYS.index(7))
        self._passphrase = _password_edit("assistedPassphrase", tr("Local case passphrase"))
        self._confirmation = _password_edit(
            "assistedPassphraseConfirmation", tr("Repeat local case passphrase")
        )
        self._confirmation.setAccessibleDescription(
            tr("Enter the same passphrase again. It must match before assistance can start.")
        )
        form = QVBoxLayout()
        for text, name, field in (
            (mark("Case expires after"), "assistedRetentionLabel", self._retention),
            (mark("Local case passphrase"), "assistedPassphraseLabel", self._passphrase),
            (mark("Repeat passphrase"), "assistedPassphraseConfirmationLabel", self._confirmation),
        ):
            label = _plain_label(text, name)
            label.setBuddy(field)
            # Full-width rows let translated labels wrap without collapsing the
            # label column when the scroll area's content policy allows shrinkage.
            form.addWidget(label)
            form.addWidget(field)
        self._consent = QCheckBox(
            tr("The person receiving help agrees to this local storage, expiry, and locking.")
        )
        self._consent.setObjectName("assistedConsentConfirmation")
        self._consent.setAccessibleName(tr("Confirm assisted-use consent"))
        self._consent.setAccessibleDescription(
            tr(
                "Unchecked initially. Select only after explaining the choices and receiving agreement. Only this confirmation and the chosen policy are recorded; no consent notes are stored."
            )
        )
        consent_label = _plain_label(self._consent.text(), "assistedConsentConfirmationLabel")
        consent_label.setBuddy(self._consent)
        self._consent.setText("")
        consent_row = QHBoxLayout()
        consent_row.addWidget(self._consent, 0, Qt.AlignmentFlag.AlignTop)
        consent_row.addWidget(consent_label, 1)
        self._error = _plain_label("", "assistedConsentError")
        self._error.setAccessibleName(tr("Assisted-use consent needs attention"))
        self._error.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._error.setStyleSheet(_FOCUS_STYLE)
        self._error.hide()
        content = QWidget()
        content_layout = QVBoxLayout(content)
        for label in (guidance, retention_guidance, lock_guidance):
            content_layout.addWidget(label)
        content_layout.addLayout(form)
        content_layout.addLayout(consent_row)
        content_layout.addWidget(self._error)
        content_layout.addStretch(1)
        self._scroll = QScrollArea()
        self._scroll.setObjectName("assistedConsentScroll")
        self._scroll.setAccessibleName(tr("Assisted-use policy and consent choices"))
        self._scroll.setWidgetResizable(True)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._scroll.setStyleSheet(_FOCUS_STYLE)
        self._scroll.setWidget(content)

        adapt_localized_content(self._scroll)
        buttons = QDialogButtonBox()
        adapt_dialog_button_box(buttons)
        self._start = buttons.addButton(
            tr("Start assisted case"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._start.setObjectName("assistedConsentStart")
        self._start.setAccessibleName(tr("Start assisted case"))
        self._start.setAccessibleDescription(
            tr("Create a case only after consent and a matching local passphrase.")
        )
        self._cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._cancel.setObjectName("assistedConsentCancel")
        self._cancel.setAccessibleName(tr("Cancel assisted case"))
        self._cancel.setAccessibleDescription(
            tr("Close without creating a case or importing documents.")
        )
        self._cancel.setDefault(True)
        self._start.setAutoDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self._scroll, 1)
        layout.addWidget(buttons)
        for before, after in pairwise(
            (
                self._retention,
                self._passphrase,
                self._confirmation,
                self._consent,
                self._error,
                self._scroll,
                self._start,
                self._cancel,
            )
        ):
            QWidget.setTabOrder(before, after)

    @property
    def retention_days(self) -> int:
        """Return a closed numeric policy choice, never editable free text."""
        return ASSISTED_USE_RETENTION_DAYS[self._retention.currentIndex()]

    @property
    def consent_confirmed(self) -> bool:
        """Return the person's explicit agreement recorded by the application."""
        return self._consent.isChecked()

    def take_passphrase(self) -> str:
        """Transfer accepted input once and clear both password controls."""
        value = self._passphrase.text() if self.result() == QDialog.DialogCode.Accepted else ""
        self._clear_secrets()
        return value

    def accept(self) -> None:
        """Validate again even when activation comes from Enter or an API call."""
        if not self._consent.isChecked():
            self._validation_failed(
                tr("Confirm agreement before starting assistance."), self._consent
            )
            return
        value = self._passphrase.text()
        if not MIN_ASSISTED_PASSPHRASE_LENGTH <= len(value) <= MAX_ASSISTED_PASSPHRASE_LENGTH:
            self._validation_failed(
                tr(
                    "Choose a passphrase with at least {value1} characters.",
                    value1=MIN_ASSISTED_PASSPHRASE_LENGTH,
                ),
                self._passphrase,
            )
            return
        if value != self._confirmation.text():
            self._validation_failed(
                tr("The passphrases must match. Enter them again."), self._confirmation
            )
            return
        super().accept()

    def reject(self) -> None:
        """Discard secrets on Cancel, Escape, and window close."""
        self._clear_secrets()
        super().reject()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        if self._initial_focus_pending:
            self._initial_focus_pending = False
            self._cancel.setFocus(Qt.FocusReason.OtherFocusReason)

    def _clear_secrets(self) -> None:
        self._passphrase.clear()
        self._confirmation.clear()

    def _validation_failed(self, message: str, recovery: QWidget) -> None:
        message = tr(message)
        self._error.setText(message)
        self._error.setAccessibleName(
            tr("Assisted-use consent needs attention. {value1}", value1=message)
        )
        self._error.show()
        recovery.setFocus(Qt.FocusReason.OtherFocusReason)
        self._scroll.ensureWidgetVisible(recovery)
        self._announcer.announce(message, assertive=True)


class AssistedUnlockDialog(QDialog):
    """Collect a local passphrase without exposing or selecting any case content."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("assistedUnlockDialog")
        self.setWindowTitle(tr("Unlock assisted case"))
        self.setAccessibleName(tr("Unlock assisted case"))
        self.setAccessibleDescription(
            tr("Enter the local case passphrase. Cancel leaves the case locked.")
        )
        self.setModal(True)
        self._announcer = ScreenReaderAnnouncer(self)
        self._passphrase = _password_edit("assistedUnlockPassphrase", tr("Local case passphrase"))
        self._pending = False
        self._error = _plain_label("", "assistedUnlockError")
        self._error.hide()
        buttons = QDialogButtonBox()
        adapt_dialog_button_box(buttons)
        unlock = buttons.addButton(tr("Unlock"), QDialogButtonBox.ButtonRole.AcceptRole)
        unlock.setObjectName("assistedUnlockSubmit")
        unlock.setAccessibleName(tr("Unlock assisted case"))
        unlock.setAccessibleDescription(
            tr("Check the entered local passphrase before showing the case.")
        )
        self._unlock_button = unlock
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        cancel.setObjectName("assistedUnlockCancel")
        cancel.setAccessibleName(tr("Keep assisted case locked"))
        cancel.setAccessibleDescription(tr("Close this prompt and keep all case content hidden."))
        self._cancel_button = cancel
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(
            _plain_label(tr("Enter the local case passphrase."), "assistedUnlockGuidance")
        )
        layout.addWidget(self._passphrase)
        layout.addWidget(self._error)
        layout.addWidget(buttons)
        QWidget.setTabOrder(self._passphrase, unlock)
        QWidget.setTabOrder(unlock, cancel)

    def take_passphrase(self) -> str:
        """Transfer accepted input once; remove it from the password widget."""
        value = self._passphrase.text() if self.result() == QDialog.DialogCode.Accepted else ""
        self._passphrase.clear()
        return value

    def reject(self) -> None:
        self._passphrase.clear()
        super().reject()

    def accept(self) -> None:
        """Keep a single unlock request pending until its exact result arrives."""
        if not self._pending:
            super().accept()

    def set_pending(self, pending: bool) -> None:
        """Keep Cancel available during bounded verification or blocked storage."""
        self._pending = pending
        self._passphrase.setEnabled(not pending)
        self._unlock_button.setEnabled(not pending)
        if pending:
            self._passphrase.clear()
            self._cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def show_failure(self, code: AssistedUseErrorCode | None = None) -> None:
        """Use only a closed safe recovery message, without library exception text."""
        message = (
            tr(AssistedUseError(code, "assisted.unlock_ui").safe_message)
            if type(code) is AssistedUseErrorCode
            and code
            in (
                AssistedUseErrorCode.CLOCK_REVIEW,
                AssistedUseErrorCode.UNLOCK_WAIT,
                AssistedUseErrorCode.STORAGE_FAILED,
                AssistedUseErrorCode.EXPIRED,
            )
            else tr("The case could not be unlocked. Check the passphrase and try again.")
        )
        self._error.setText(message)
        self._error.setAccessibleName(message)
        self._error.show()
        self.set_pending(False)
        self._passphrase.clear()
        self._passphrase.setFocus(Qt.FocusReason.OtherFocusReason)
        self._announcer.announce(message, assertive=True)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self._passphrase.setFocus(Qt.FocusReason.OtherFocusReason)


class AssistedLockScreen(QWidget):
    """A content-free surface; the window owns hiding other windows and dialogs."""

    unlock_requested = Signal()
    end_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("assistedLockScreen")
        self.setAccessibleName(tr("Assisted case locked"))
        self.setAccessibleDescription(tr("Case content is hidden. Unlock or end assistance."))
        self._announcer = ScreenReaderAnnouncer(self)
        self._expired = False
        self._clock_review = False
        self._unlock_pending = False
        self._status = _plain_label(tr("Assisted case locked."), "assistedLockStatus")
        self._guidance = _plain_label(
            tr(
                "Enter the local case passphrase to continue. End assistance closes PaperWorkShell without showing other cases. Stored files and separate exports remain."
            ),
            "assistedLockGuidance",
        )
        self._unlock = QPushButton(tr("Unlock assisted case"))
        self._unlock.setObjectName("assistedLockUnlock")
        self._unlock.setAccessibleName(tr("Unlock assisted case"))
        self._unlock.setAccessibleDescription(
            tr("Open a local passphrase prompt. Case content stays hidden.")
        )
        self._end = QPushButton(tr("End assistance"))
        self._end.setObjectName("assistedLockEnd")
        self._end.setAccessibleName(tr("End assistance"))
        self._end.setAccessibleDescription(
            tr(
                "Save queued changes and close PaperWorkShell without deleting files or opening others."
            )
        )
        self._unlock.clicked.connect(self.unlock_requested)
        self._end.clicked.connect(self.end_requested)
        layout = QVBoxLayout(self)
        layout.addWidget(self._status)
        layout.addWidget(self._guidance)
        layout.addWidget(self._unlock)
        layout.addWidget(self._end)
        layout.addStretch(1)
        QWidget.setTabOrder(self._unlock, self._end)

    def set_expired(self, expired: bool) -> None:
        """An expired case remains hidden and cannot be unlocked for document work."""
        self._expired = expired
        self._render_policy_state()

    def set_clock_review(self, clock_review: bool) -> None:
        """Explain local clock recovery without revealing any document content."""
        self._clock_review = clock_review
        self._render_policy_state()

    def set_unlock_pending(self, pending: bool) -> None:
        """Retain one outstanding unlock while leaving End assistance available."""
        self._unlock_pending = pending
        self._render_policy_state()

    def _render_policy_state(self) -> None:
        expired, clock_review = self._expired, self._clock_review
        status = (
            tr("Assisted case expired.")
            if expired
            else tr("Assisted case needs clock review.")
            if clock_review
            else tr("Assisted case locked.")
        )
        self._status.setText(status)
        self._status.setAccessibleName(self._status.text())
        self._unlock.setEnabled(not expired and not self._unlock_pending)
        self._guidance.setText(
            tr(
                "This case has expired and cannot be used again. Its stored files remain until explicit case removal. Separate exports remain. End assistance closes PaperWorkShell."
            )
            if expired
            else tr(
                "The computer clock moved backwards. Correct the clock, then unlock the case. Case content stays hidden. End assistance closes PaperWorkShell."
            )
            if clock_review
            else tr(
                "An unlock is finishing. End assistance is available. Case content stays hidden."
            )
            if self._unlock_pending
            else tr(
                "Enter the local case passphrase to continue. End assistance closes PaperWorkShell without showing other cases. Stored files and separate exports remain."
            )
        )
        self._guidance.setAccessibleName(self._guidance.text())
        self.setAccessibleName(status.removesuffix("."))
        self.setAccessibleDescription(
            tr(
                "This case has expired. Its content stays hidden. End assistance closes PaperWorkShell."
            )
            if expired
            else tr("Correct the computer clock, then unlock. Case content stays hidden.")
            if clock_review
            else tr(
                "An unlock is finishing. End assistance is available. Case content stays hidden."
            )
            if self._unlock_pending
            else tr("Case content is hidden. Unlock or end assistance.")
        )
        self._announcer.announce(f"{status} {self._guidance.text()}", assertive=True)

    def focus_recovery(self) -> None:
        """Focus the available recovery action after the privacy curtain is visible."""
        (self._unlock if self._unlock.isEnabled() else self._end).setFocus(
            Qt.FocusReason.OtherFocusReason
        )


class AssistedHandoverDialog(QDialog):
    """Show only the internal case reference and recorded local expiry policy."""

    def __init__(self, case_id: UUID, expires_at: datetime, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if type(case_id) is not UUID or not isinstance(expires_at, datetime):
            raise ValueError("assisted handover identity is invalid")
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("assisted handover expiry is invalid")
        self.setObjectName("assistedHandoverDialog")
        self.setWindowTitle(tr("Assisted-case handover"))
        self.setAccessibleName(tr("Assisted-case handover"))
        self.setAccessibleDescription(
            tr("Review a case reference and the recorded local expiry policy.")
        )
        self.setModal(True)
        self.resize(620, 420)
        self._summary = QPlainTextEdit()
        self._summary.setObjectName("assistedHandoverSummary")
        self._summary.setAccessibleName(tr("Assisted-case handover summary"))
        self._summary.setAccessibleDescription(
            tr(
                "Read-only local handover instructions. Select and copy only if you want to retain them."
            )
        )
        self._summary.setReadOnly(True)
        timestamp = expires_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
        self._summary.setPlainText(
            tr(
                "PaperWorkShell assisted case\nCase reference: {value1}\nExpires: {value2}\n\nTo reopen on this computer, select this case reference and enter its local case passphrase. Keep the passphrase separately; it is not included in this summary and cannot be recovered.\n\nThe case locks after five minutes without activity and whenever it is reopened. At expiry it becomes unavailable for document work. Files remain until explicit case removal. Exports are separate files and remain independently.\n\nThis screen lock does not encrypt case files. Sign out of Windows before leaving a shared computer. Case removal does not guarantee forensic erasure.",
                value1=case_id,
                value2=timestamp,
            )
        )
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        adapt_dialog_button_box(buttons)
        close = buttons.button(QDialogButtonBox.StandardButton.Close)
        close.setAccessibleName(tr("Close handover summary"))
        close.setAccessibleDescription(tr("Return to the active assisted case."))
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self._summary, 1)
        layout.addWidget(buttons)

    @property
    def summary_text(self) -> str:
        """Return path-free policy guidance, without names, document values, or secrets."""
        return self._summary.toPlainText()

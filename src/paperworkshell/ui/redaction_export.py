"""Accessible, content-free removal-export result and recovery presentation."""

from __future__ import annotations

from itertools import pairwise

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent, QShowEvent
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from paperworkshell.domain.redaction_export import (
    RedactionExportCode,
    RedactionExportReport,
    RedactionExportStatus,
)
from paperworkshell.localization.catalog import tr
from paperworkshell.ui.localization_layout import adapt_localized_content


class RedactionExportDialog(QDialog):
    """Display result facts without authorizing export or changing saved marks."""

    retry_requested = Signal()
    review_marks_requested = Signal()

    def __init__(self, report: RedactionExportReport, parent: QWidget | None = None) -> None:
        if type(report) is not RedactionExportReport:
            raise ValueError("Removal export requires a validated result report.")
        report.__post_init__()
        super().__init__(parent)
        self._report = report
        self.setObjectName("redactionExportDialog")
        self.setWindowTitle(tr("Removal export result"))
        self.setAccessibleName(tr("Removal export result and recovery"))
        self.setAccessibleDescription(
            tr(report.status_label)
            + ". "
            + tr(report.safe_message)
            + tr(
                " Review affected internal page IDs and every required method result. Retry starts a new attempt and cannot override a failed check. Review marks returns to unapplied intent."
            )
        )
        self.setModal(True)
        content = QWidget()
        content.setObjectName("redactionExportContent")
        body = QVBoxLayout(content)
        self._status = QLabel(tr(report.status_label) + ". " + tr(report.safe_message))
        self._status.setObjectName("redactionExportStatus")
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setWordWrap(True)
        self._status.setAccessibleName(tr("Removal export status and recovery"))
        self._status.setAccessibleDescription(self._status.text())
        self._status.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._status.setFrameShape(QFrame.Shape.StyledPanel)
        self._status.setStyleSheet(
            "QLabel:focus { border: 2px solid palette(highlight); padding: 3px; }"
        )
        body.addWidget(self._status)
        report_label = QLabel(tr("&Removal checks and affected pages"))
        report_label.setWordWrap(True)
        report_label.setTextFormat(Qt.TextFormat.PlainText)
        self._details = QPlainTextEdit()
        self._details.setObjectName("redactionExportReportText")
        self._details.setAccessibleName(tr("Removal export methods and affected page IDs"))
        self._details.setAccessibleDescription(
            tr(
                "Selectable read-only plain text lists internal affected page IDs, every required method, pass, fail or unknown results, result codes and unsigned derivative status. Control+Home and Control+End move to the start and end. Tab moves to the next control."
            )
        )
        self._details.setReadOnly(True)
        self._details.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self._details.setTabChangesFocus(True)
        self._details.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._details.setMinimumHeight(180)
        self._details.setPlainText(report.user_text)
        report_label.setBuddy(self._details)
        body.addWidget(report_label)
        body.addWidget(self._details)
        self._scroll = QScrollArea()
        self._scroll.setObjectName("redactionExportScrollArea")
        self._scroll.setAccessibleName(tr("Removal export result contents"))
        self._scroll.setAccessibleDescription(
            tr("Scrollable result status, method results and internal affected page IDs.")
        )
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidget(content)
        adapt_localized_content(self._scroll)
        self._retry = self._button(
            tr("&Retry"),
            "redactionExportRetryButton",
            tr("Retry removal export"),
            tr(
                "Start a new authenticated attempt. Required checks cannot be skipped or overridden."
            ),
        )
        self._retry.setEnabled(report.retry_allowed)
        if report.code is RedactionExportCode.WORKFLOW_REQUIRED:
            self._retry.setText(tr("&Export removal derivative…"))
            self._retry.setAccessibleName(tr("Review and export removal derivative"))
            self._retry.setAccessibleDescription(
                tr(
                    "Open a fresh review of the saved marks, then choose a new file. Required checks and release qualification cannot be skipped."
                )
            )
        self._retry.clicked.connect(self._request_retry)
        self._review = self._button(
            tr("Review &marks"),
            "redactionExportReviewButton",
            tr("Review unapplied removal marks"),
            tr("Return to review the saved intent. This does not apply or verify removal."),
        )
        self._review.setEnabled(report.review_allowed)
        self._review.clicked.connect(self._request_review)
        self._close = self._button(
            tr("&Close"),
            "redactionExportCloseButton",
            tr("Close removal export result"),
            tr("Close this report without starting another attempt or changing removal marks."),
        )
        self._close.setDefault(True)
        self._close.clicked.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self._scroll, 1)
        layout.addWidget(self._retry)
        layout.addWidget(self._review)
        layout.addWidget(self._close)
        for current, following in pairwise(
            (self._status, self._details, self._retry, self._review, self._close, self._status)
        ):
            QWidget.setTabOrder(current, following)
        available = self.screen().availableGeometry()
        self.resize(
            min(780, max(320, available.width() - 40)),
            min(680, max(240, available.height() - 80)),
        )

    @property
    def report(self) -> RedactionExportReport:
        return self._report

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt override name
        super().showEvent(event)
        recovery = self._retry if self._retry.isEnabled() else self._review
        if not recovery.isEnabled():
            recovery = self._close
        recovery.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        announcement = QAccessibleAnnouncementEvent(self._status, self._status.text())
        announcement.setPoliteness(
            QAccessible.AnnouncementPoliteness.Polite
            if self._report.status is RedactionExportStatus.COMMITTED
            else QAccessible.AnnouncementPoliteness.Assertive
        )
        QAccessible.updateAccessibility(announcement)

    @staticmethod
    def _button(text: str, name: str, accessible_name: str, description: str) -> QPushButton:
        button = QPushButton(tr(text))
        button.setObjectName(name)
        button.setAccessibleName(tr(accessible_name))
        button.setAccessibleDescription(tr(description))
        button.setAutoDefault(False)
        return button

    def _request_retry(self) -> None:
        if self._report.retry_allowed and self._retry.isEnabled():
            self.retry_requested.emit()

    def _request_review(self) -> None:
        if self._report.review_allowed and self._review.isEnabled():
            self.review_marks_requested.emit()

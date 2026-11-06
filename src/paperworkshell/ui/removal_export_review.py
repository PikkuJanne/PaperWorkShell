"""Accessible explicit review before creating a removal derivative."""

from __future__ import annotations

from itertools import pairwise

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from paperworkshell.app.removal_export_review import RemovalExportReview
from paperworkshell.localization.catalog import tr
from paperworkshell.ui.localization_layout import adapt_localized_content


class RemovalExportReviewDialog(QDialog):
    """Collect user review and signature consent, never verification approval."""

    destination_requested = Signal(bool)

    def __init__(self, review: RemovalExportReview, parent: QWidget | None = None) -> None:
        if type(review) is not RemovalExportReview:
            raise ValueError("Removal export requires a bound review.")
        review.__post_init__()
        super().__init__(parent)
        self.review = review
        self.setObjectName("removalExportReviewDialog")
        self.setWindowTitle(tr("Review removal export"))
        self.setAccessibleName(tr("Review permanent removal derivative"))
        self.setAccessibleDescription(
            tr(
                "Review the saved removal marks and derivative limitations, then choose a new PDF file. Required verification and independent release approval cannot be overridden."
            )
        )
        self.setModal(True)
        content = QWidget()
        body = QVBoxLayout(content)
        details = QLabel(
            tr(
                "Create a new unsigned derivative from the saved removal marks. Original files and saved marks stay unchanged. Affected pages are reconstructed from masked pixels; forms, vector detail, searchable text and accessibility structure may be lost. Review every occurrence before continuing. The current profile supports one active PDF page, with crop and quarter-turn rotation, without overlays or scan cleanup. Required checks must pass, and permanent removal must have independent release approval before any file is saved."
            )
        )
        details.setObjectName("removalExportReviewWarning")
        details.setAccessibleName(tr("Removal derivative warnings and supported scope"))
        details.setAccessibleDescription(details.text())
        details.setTextFormat(Qt.TextFormat.PlainText)
        details.setWordWrap(True)
        details.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        details.setStyleSheet(
            "QLabel:focus { border: 2px solid palette(highlight); padding: 3px; }"
        )
        body.addWidget(details)
        pages = QLabel(tr("Affected pages: {value1}", value1=len(review.affected_page_ids)))
        pages.setAccessibleName(tr("Affected removal page count"))
        body.addWidget(pages)
        self._reviewed = QCheckBox(tr("Marks &reviewed"))
        self._reviewed.setObjectName("removalExportReviewedCheckBox")
        self._reviewed.setAccessibleName(tr("Confirm review of saved removal marks and warnings"))
        self._reviewed.setAccessibleDescription(
            tr("Select only after reviewing every saved removal mark and the derivative warnings.")
        )
        body.addWidget(self._reviewed)
        self._signed = QCheckBox(tr("&Unsigned derivative"))
        self._signed.setObjectName("removalExportUnsignedCheckBox")
        self._signed.setAccessibleName(tr("Confirm creation of an unsigned derivative"))
        self._signed.setAccessibleDescription(
            tr(
                "Explicitly accept creating an unsigned derivative from a source with digital-signature markers."
            )
        )
        self._signed.setVisible(review.signed_source_confirmation_required)
        if review.signed_source_confirmation_required:
            warning = QLabel(tr(review.signature_review.warning_message))
            warning.setTextFormat(Qt.TextFormat.PlainText)
            warning.setWordWrap(True)
            warning.setAccessibleName(tr("Digital signature warning"))
            warning.setAccessibleDescription(warning.text())
            body.addWidget(warning)
        body.addWidget(self._signed)
        scroll = QScrollArea()
        scroll.setObjectName("removalExportReviewScrollArea")
        scroll.setAccessibleName(tr("Removal export review contents"))
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        adapt_localized_content(scroll)
        self._choose = QPushButton(tr("Choose &new file…"))
        self._choose.setObjectName("removalExportChooseFileButton")
        self._choose.setAccessibleName(tr("Choose new PDF destination for removal derivative"))
        self._choose.setAccessibleDescription(
            tr(
                "Continue only after review. Existing files cannot be overwritten; checks cannot be skipped."
            )
        )
        self._choose.setAutoDefault(False)
        self._choose.clicked.connect(self._request_destination)
        self._cancel = QPushButton(tr("&Cancel"))
        self._cancel.setObjectName("removalExportCancelButton")
        self._cancel.setAccessibleName(tr("Cancel removal export review"))
        self._cancel.setDefault(True)
        self._cancel.clicked.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(scroll, 1)
        layout.addWidget(self._choose)
        layout.addWidget(self._cancel)
        controls = (details, self._reviewed, self._signed, self._choose, self._cancel, details)
        for current, following in pairwise(controls):
            QWidget.setTabOrder(current, following)
        self._reviewed.toggled.connect(self._update_actions)
        self._signed.toggled.connect(self._update_actions)
        self._update_actions()
        available = self.screen().availableGeometry()
        self.resize(
            min(720, max(320, available.width() - 40)), min(560, max(240, available.height() - 80))
        )
        self._cancel.setFocus(Qt.FocusReason.OtherFocusReason)

    def _update_actions(self) -> None:
        self._choose.setEnabled(
            self._reviewed.isChecked()
            and (not self.review.signed_source_confirmation_required or self._signed.isChecked())
        )

    @property
    def review_confirmed(self) -> bool:
        return self._reviewed.isChecked() and (
            not self.review.signed_source_confirmation_required or self._signed.isChecked()
        )

    @property
    def signed_source_confirmed(self) -> bool:
        return self.review.signed_source_confirmation_required and self._signed.isChecked()

    def _request_destination(self) -> None:
        if self._choose.isEnabled():
            self.destination_requested.emit(self.signed_source_confirmed)

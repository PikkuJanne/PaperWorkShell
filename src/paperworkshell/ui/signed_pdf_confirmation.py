"""Accessible presentation for confirming an unsigned PDF derivative."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QShowEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from paperworkshell.domain.signed_source_policy import (
    SignedSourceModificationReview,
    SignedSourceReviewStatus,
)


@dataclass(frozen=True, slots=True)
class UnsignedDerivativeConfirmationPresentation:
    """UI projection of one manifest-bound signed-source review."""

    review: SignedSourceModificationReview

    def __post_init__(self) -> None:
        if (
            type(self.review) is not SignedSourceModificationReview
            or self.review.status is not SignedSourceReviewStatus.CONFIRMATION_REQUIRED
        ):
            raise ValueError("signed-source review does not require confirmation")

    @property
    def reason_explanation(self) -> str:
        """Return the domain-owned, reason-aware warning text."""

        return self.review.warning_message

    @property
    def question(self) -> str:
        """Return the domain-owned confirmation question."""

        return self.review.warning_title

    @property
    def confirm_action(self) -> str:
        """Return the exact explicit action text."""

        return self.review.confirm_action

    @property
    def cancel_action(self) -> str:
        """Return the exact safe action text."""

        return self.review.cancel_action

    @property
    def result_label(self) -> str:
        """Return the visible label for the permitted modified output."""

        return self.review.result_label


class UnsignedDerivativeConfirmationDialog(QDialog):
    """Keyboard-first confirmation with Cancel as the safe default."""

    def __init__(
        self,
        presentation: UnsignedDerivativeConfirmationPresentation,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._presentation = presentation
        self.setModal(True)
        self.setWindowTitle(presentation.confirm_action)
        self.setAccessibleName("Create unsigned derivative confirmation")
        self.setAccessibleDescription(
            "Review why the result will be unsigned, then create the derivative or cancel. "
            "Cancel is the safe default."
        )

        content = QWidget()
        content.setObjectName("unsignedDerivativeConfirmationContent")
        content_layout = QVBoxLayout(content)

        title = QLabel(presentation.question)
        title.setObjectName("unsignedDerivativeConfirmationHeading")
        title.setAccessibleName("Create unsigned derivative question")
        title.setWordWrap(True)
        title_font = QFont(title.font())
        title_font.setBold(True)
        title_font.setPointSizeF(max(title_font.pointSizeF(), 14.0))
        title.setFont(title_font)
        content_layout.addWidget(title)

        reason_heading = QLabel("Why this needs confirmation")
        reason_heading.setObjectName("unsignedDerivativeReasonHeading")
        reason_heading.setAccessibleName("Reason for unsigned derivative warning")
        reason_heading.setAccessibleDescription(
            "The following text explains the detected or unknown digital-signature-marker state."
        )
        reason_heading.setWordWrap(True)
        reason_font = QFont(reason_heading.font())
        reason_font.setBold(True)
        reason_heading.setFont(reason_font)
        content_layout.addWidget(reason_heading)

        reason = QLabel(presentation.reason_explanation)
        reason.setObjectName("unsignedDerivativeReason")
        reason.setAccessibleName("Unsigned derivative warning reason")
        reason.setAccessibleDescription(presentation.reason_explanation)
        reason.setWordWrap(True)
        reason.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByKeyboard)
        content_layout.addWidget(reason)

        unsigned_label = QLabel(presentation.result_label)
        unsigned_label.setObjectName("unsignedDerivativeLabel")
        unsigned_label.setAccessibleName("Output label: Unsigned derivative")
        unsigned_label.setAccessibleDescription(
            "The created output is a modified file and is not reported as digitally signed."
        )
        unsigned_label.setWordWrap(True)
        unsigned_font = QFont(unsigned_label.font())
        unsigned_font.setBold(True)
        unsigned_label.setFont(unsigned_font)
        content_layout.addWidget(unsigned_label)

        content_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setObjectName("unsignedDerivativeConfirmationScrollArea")
        scroll.setAccessibleName("Unsigned derivative warning details")
        scroll.setAccessibleDescription(
            "Scrollable warning details. Use arrow or Page Down keys, then use the buttons after "
            "reviewing the reason and consequence."
        )
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        scroll.setWidget(content)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        button_box = QDialogButtonBox()
        button_box.setObjectName("unsignedDerivativeConfirmationButtons")
        button_box.setAccessibleName("Create unsigned derivative or cancel")
        button_box.setAccessibleDescription(
            "Cancel is selected by default. Create unsigned derivative continues explicitly."
        )
        self._create_button = QPushButton(presentation.confirm_action)
        self._create_button.setObjectName("createUnsignedDerivativeButton")
        self._create_button.setAccessibleName("Create unsigned derivative")
        self._create_button.setAccessibleDescription(
            "Continue with creation of a modified PDF labeled as an unsigned derivative."
        )
        self._create_button.setDefault(False)
        self._create_button.setAutoDefault(False)
        button_box.addButton(self._create_button, QDialogButtonBox.ButtonRole.AcceptRole)
        cancel = button_box.addButton(QDialogButtonBox.StandardButton.Cancel)
        if not isinstance(cancel, QPushButton):
            raise RuntimeError("Qt did not create the required Cancel button")
        self._cancel_button = cancel
        if self._cancel_button.text() != presentation.cancel_action:
            self._cancel_button.setText(presentation.cancel_action)
        self._cancel_button.setObjectName("cancelUnsignedDerivativeButton")
        self._cancel_button.setAccessibleName("Cancel unsigned derivative creation")
        self._cancel_button.setAccessibleDescription(
            "Close this confirmation without creating an unsigned derivative."
        )
        self._cancel_button.setDefault(True)
        self._cancel_button.setAutoDefault(True)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll, 1)
        layout.addWidget(button_box)
        QWidget.setTabOrder(self._cancel_button, scroll)
        QWidget.setTabOrder(scroll, self._create_button)
        QWidget.setTabOrder(self._create_button, self._cancel_button)
        self.resize(600, 420)
        self._cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)

    @property
    def presentation(self) -> UnsignedDerivativeConfirmationPresentation:
        """Return the immutable content used to construct the dialog."""

        return self._presentation

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt override name
        """Restore the safe focus target each time the dialog is shown."""

        super().showEvent(event)
        self._cancel_button.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

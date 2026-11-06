"""Accessible Qt review surfaces for visual signature-image placement and deletion."""

from __future__ import annotations

from itertools import pairwise
from typing import Final
from uuid import UUID

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QAccessible,
    QAccessibleAnnouncementEvent,
    QColor,
    QImage,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from paperworkshell.app.signature_images import (
    SIGNATURE_IMAGE_DELETE_KEEP_WORDING,
    SIGNATURE_IMAGE_DELETE_UNUSED_WORDING,
    SIGNATURE_IMAGE_DISCLAIMER,
    SIGNATURE_IMAGE_WHOLE_IMAGE_CONFIRMATION,
    SignatureImagePlacementChoice,
    SignatureImagePlacementDraft,
    SignatureImagePlacementRequest,
)
from paperworkshell.domain.manifest_repository import ManifestRepositoryErrorCode
from paperworkshell.domain.page_coordinates import (
    NORMALIZED_PAGE_MILLIONTHS,
    NormalizedPageBox,
    PageCoordinateError,
)
from paperworkshell.domain.signature_images import SignatureImageErrorCode
from paperworkshell.localization.catalog import mark, tr
from paperworkshell.overlays.models import OverlayErrorCode
from paperworkshell.overlays.signature_commands import SignatureAssetDeletePolicy
from paperworkshell.ui.localization_layout import adapt_localized_content
from paperworkshell.ui.presentation import adapt_dialog_button_box

_SAFE_FAILURE_CODES: Final = frozenset(
    code.value
    for enum_type in (
        ManifestRepositoryErrorCode,
        OverlayErrorCode,
        SignatureImageErrorCode,
    )
    for code in enum_type
)
_PLACEMENT_FAILURE = mark(
    "PaperWorkShell could not place that visual signature image safely. Choose Retry to prepare it again, or Cancel."
)
_DELETE_FAILURE = mark(
    "PaperWorkShell could not remove that signature-image placement safely. Review the selection and try again."
)
_CHECKER_SIZE = 16
_FOCUS_VISIBLE_TEXT_STYLE = (
    "QLabel {border: 2px solid transparent; padding: 4px;} "
    "QLabel:focus {border-color: palette(highlight);}"
)


def _wrapped_choice(control: QCheckBox | QRadioButton) -> QHBoxLayout:
    """Keep full choice wording readable without widening the scroll content."""
    label = QLabel(control.text())
    label.setObjectName(f"{control.objectName()}Label")
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setBuddy(control)
    control.setText("")
    row = QHBoxLayout()
    row.addWidget(control, 0, Qt.AlignmentFlag.AlignTop)
    row.addWidget(label, 1)
    return row


def default_signature_image_box() -> NormalizedPageBox:
    """Return a conservative keyboard-editable full-page normalized placement."""

    return NormalizedPageBox(300_000, 425_000, 400_000, 150_000)


class SignatureImagePlacementDialog(QDialog):
    """Review a bounded preview and explicitly confirm one visual-only placement."""

    place_requested = Signal(object)
    retry_requested = Signal()
    cancel_operation_requested = Signal()

    def __init__(
        self,
        draft: SignatureImagePlacementDraft,
        parent: QWidget | None = None,
    ) -> None:
        if type(draft) is not SignatureImagePlacementDraft:
            raise ValueError("draft must be a SignatureImagePlacementDraft")
        super().__init__(parent)
        self._draft = draft
        self._pending = False
        self.setObjectName("signatureImagePlacementDialog")
        self.setWindowTitle(tr("Place signature image"))
        self.setAccessibleName(tr("Place signature image dialog"))
        self.setAccessibleDescription(
            tr(
                "Review the image, its background, and normalized placement. Confirm both statements before placing the visual image."
            )
        )

        outer_layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setObjectName("signatureImagePlacementScrollArea")
        scroll.setAccessibleName(tr("Signature image review content"))
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("signatureImagePlacementContent")
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        adapt_localized_content(scroll)
        outer_layout.addWidget(scroll)
        heading = QLabel(tr("Place signature image"))
        heading.setObjectName("signatureImageHeading")
        heading.setAccessibleName(tr("Place signature image"))
        heading_font = heading.font()
        heading_font.setPointSizeF(heading_font.pointSizeF() * 1.3)
        heading_font.setBold(True)
        heading.setFont(heading_font)
        heading.setWordWrap(True)
        layout.addWidget(heading)

        disclaimer = QLabel(tr(SIGNATURE_IMAGE_DISCLAIMER))
        disclaimer.setObjectName("signatureImageDisclaimer")
        disclaimer.setAccessibleName(
            tr("Visual image disclaimer. {value1}", value1=tr(SIGNATURE_IMAGE_DISCLAIMER))
        )
        disclaimer.setAccessibleDescription(tr(SIGNATURE_IMAGE_DISCLAIMER))
        disclaimer.setTextFormat(Qt.TextFormat.PlainText)
        disclaimer.setWordWrap(True)
        disclaimer.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        disclaimer.setStyleSheet(_FOCUS_VISIBLE_TEXT_STYLE)
        layout.addWidget(disclaimer)

        self._preview = QLabel()
        self._preview.setObjectName("signatureImagePreview")
        self._preview.setAccessibleName(tr("Signature image preview"))
        self._preview.setAccessibleDescription(
            tr(
                "Bounded visual preview over a checkerboard. The image itself is not described or read aloud."
            )
        )
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setPixmap(_checkerboard_preview(draft))
        layout.addWidget(self._preview)

        review = QLabel(
            tr(
                "{crop} {background}",
                crop=tr(draft.crop_review),
                background=tr(draft.background_review),
            )
        )
        review.setObjectName("signatureImageCropBackgroundReview")
        review.setAccessibleName(
            tr("Image area and background review. {value1}", value1=review.text())
        )
        review.setAccessibleDescription(review.text())
        review.setTextFormat(Qt.TextFormat.PlainText)
        review.setWordWrap(True)
        review.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        review.setStyleSheet(_FOCUS_VISIBLE_TEXT_STYLE)
        layout.addWidget(review)

        placement_group = QGroupBox(tr("Page placement"))
        placement_group.setObjectName("signatureImagePlacementGroup")
        placement_group.setAccessibleDescription(
            tr(
                "Coordinates use millionths of the full original page. They continue to follow the page when it is rotated or cropped."
            )
        )
        placement_layout = QGridLayout(placement_group)
        initial = default_signature_image_box()
        self._left = self._coordinate(
            "signatureImageLeft",
            tr("Left position"),
            tr("Distance from the full page left edge in millionths."),
            0,
            initial.x,
        )
        self._top = self._coordinate(
            "signatureImageTop",
            tr("Top position"),
            tr("Distance from the full page top edge in millionths."),
            0,
            initial.y,
        )
        self._width = self._coordinate(
            "signatureImageWidth",
            tr("Placement width"),
            tr("Visual image width in full-page millionths."),
            1,
            initial.width,
        )
        self._height = self._coordinate(
            "signatureImageHeight",
            tr("Placement height"),
            tr("Visual image height in full-page millionths."),
            1,
            initial.height,
        )
        for row, (label_text, control) in enumerate(
            (
                (tr("Left"), self._left),
                (tr("Top"), self._top),
                (tr("Width"), self._width),
                (tr("Height"), self._height),
            )
        ):
            label = QLabel(tr(label_text))
            label.setBuddy(control)
            placement_layout.addWidget(label, row, 0)
            placement_layout.addWidget(control, row, 1)
        layout.addWidget(placement_group)

        self._disclaimer_confirmation = QCheckBox(
            tr(
                "I understand this places a visual image only and creates no digital or qualified electronic signature."
            )
        )
        self._disclaimer_confirmation.setObjectName("signatureImageDisclaimerConfirmation")
        self._disclaimer_confirmation.setAccessibleName(self._disclaimer_confirmation.text())
        self._disclaimer_confirmation.setAccessibleDescription(tr(SIGNATURE_IMAGE_DISCLAIMER))
        self._whole_image_confirmation = QCheckBox(tr(SIGNATURE_IMAGE_WHOLE_IMAGE_CONFIRMATION))
        self._whole_image_confirmation.setObjectName("signatureImageBackgroundConfirmation")
        self._whole_image_confirmation.setAccessibleName(
            tr(SIGNATURE_IMAGE_WHOLE_IMAGE_CONFIRMATION)
        )
        self._whole_image_confirmation.setAccessibleDescription(
            tr(SIGNATURE_IMAGE_WHOLE_IMAGE_CONFIRMATION)
        )
        self._disclaimer_confirmation.toggled.connect(self._update_place_enabled)
        self._whole_image_confirmation.toggled.connect(self._update_place_enabled)
        layout.addLayout(_wrapped_choice(self._disclaimer_confirmation))
        layout.addLayout(_wrapped_choice(self._whole_image_confirmation))

        self._status = QLabel(tr("Review the preview and both confirmations before placing."))
        self._status.setObjectName("signatureImagePlacementStatus")
        self._status.setAccessibleName(
            tr("Signature image placement status. {value1}", value1=self._status.text())
        )
        self._status.setAccessibleDescription(self._status.text())
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setWordWrap(True)
        self._status.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._status.setStyleSheet(_FOCUS_VISIBLE_TEXT_STYLE)
        layout.addWidget(self._status)

        self._buttons = QDialogButtonBox()
        adapt_dialog_button_box(self._buttons)
        self._place_button = self._buttons.addButton(
            tr("Place visual image"),
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self._place_button.setObjectName("placeSignatureImageButton")
        self._place_button.setAccessibleName(tr("Place signature image as a visual image"))
        self._place_button.setAccessibleDescription(
            tr("Place the confirmed visual image at the normalized coordinates.")
        )
        self._retry_button = self._buttons.addButton(
            tr("Retry"),
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        self._retry_button.setObjectName("retrySignatureImageButton")
        self._retry_button.setAccessibleDescription(
            tr("Prepare the selected local image again after a failed placement.")
        )
        self._retry_button.setVisible(False)
        self._cancel_button = self._buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._cancel_button.setObjectName("cancelSignatureImageButton")
        self._cancel_button.setAccessibleDescription(
            tr("Cancel and discard the private prepared case copy.")
        )
        self._place_button.clicked.connect(self._request_place)
        self._retry_button.clicked.connect(self.retry_requested.emit)
        self._cancel_button.clicked.connect(self._cancel_or_reject)
        layout.addWidget(self._buttons)

        for control in (self._left, self._top, self._width, self._height):
            control.valueChanged.connect(self._update_place_enabled)
        chain: tuple[QWidget, ...] = (
            disclaimer,
            review,
            self._left,
            self._top,
            self._width,
            self._height,
            self._disclaimer_confirmation,
            self._whole_image_confirmation,
            self._status,
            self._place_button,
            self._retry_button,
            self._cancel_button,
        )
        for current, following in pairwise(chain):
            QWidget.setTabOrder(current, following)
        self._update_place_enabled()
        self._disclaimer_confirmation.setFocus(Qt.FocusReason.OtherFocusReason)

    @property
    def draft(self) -> SignatureImagePlacementDraft:
        return self._draft

    def mark_placed(self) -> None:
        self._pending = False
        self.accept()

    def reject(self) -> None:
        """Keep a commit review visible until cooperative cancellation finishes."""

        if self._pending:
            self._request_pending_cancellation()
            return
        super().reject()

    def show_failure(self, code: object, message: object) -> None:
        del message
        self._pending = False
        self._set_editor_enabled(False)
        self._retry_button.setVisible(True)
        self._retry_button.setEnabled(True)
        self._cancel_button.setEnabled(True)
        self._set_status(_safe_failure(code, tr(_PLACEMENT_FAILURE)), assertive=True)
        self._retry_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _coordinate(
        self,
        object_name: str,
        accessible_name: str,
        description: str,
        minimum: int,
        value: int,
    ) -> QSpinBox:
        control = QSpinBox()
        control.setObjectName(object_name)
        control.setAccessibleName(tr(accessible_name))
        control.setAccessibleDescription(tr(description))
        control.setRange(minimum, NORMALIZED_PAGE_MILLIONTHS)
        control.setSingleStep(1_000)
        control.setGroupSeparatorShown(True)
        control.setValue(value)
        return control

    def _box(self) -> NormalizedPageBox | None:
        try:
            return NormalizedPageBox(
                self._left.value(),
                self._top.value(),
                self._width.value(),
                self._height.value(),
            )
        except PageCoordinateError:
            return None

    def _update_place_enabled(self, *_args: object) -> None:
        valid = (
            not self._pending
            and self._disclaimer_confirmation.isChecked()
            and self._whole_image_confirmation.isChecked()
            and self._box() is not None
        )
        self._place_button.setEnabled(valid)
        if not self._pending and self._box() is None:
            self._set_status(
                tr("The placement must remain within the full normalized page."),
                assertive=False,
            )

    def _request_place(self) -> None:
        box = self._box()
        if box is None or not self._place_button.isEnabled():
            QApplication.beep()
            self._set_status(
                tr("Review both confirmations and choose a placement inside the full page."),
                assertive=True,
            )
            return
        request = SignatureImagePlacementRequest(
            self._draft.draft_id,
            box,
            self._disclaimer_confirmation.isChecked(),
            self._whole_image_confirmation.isChecked(),
        )
        self._pending = True
        self._set_editor_enabled(False)
        self._cancel_button.setEnabled(True)
        self._set_status(tr("Placing the visual image. Keep this dialog open."), assertive=False)
        self.place_requested.emit(request)

    def _cancel_or_reject(self) -> None:
        if self._pending:
            self._request_pending_cancellation()
            return
        self.reject()

    def _request_pending_cancellation(self) -> None:
        if not self._cancel_button.isEnabled():
            return
        self._cancel_button.setEnabled(False)
        self._set_status(tr("Cancelling the signature-image operation."), assertive=False)
        self.cancel_operation_requested.emit()

    def _set_editor_enabled(self, enabled: bool) -> None:
        for widget in (
            self._left,
            self._top,
            self._width,
            self._height,
            self._disclaimer_confirmation,
            self._whole_image_confirmation,
        ):
            widget.setEnabled(enabled)
        self._place_button.setEnabled(enabled and self._box() is not None)
        self._cancel_button.setEnabled(enabled)

    def _set_status(self, text: str, *, assertive: bool) -> None:
        text = tr(text)
        self._status.setText(text)
        self._status.setAccessibleName(tr("Signature image status. {value1}", value1=text))
        self._status.setAccessibleDescription(tr(text))
        event = QAccessibleAnnouncementEvent(self._status, text)
        event.setPoliteness(
            QAccessible.AnnouncementPoliteness.Assertive
            if assertive
            else QAccessible.AnnouncementPoliteness.Polite
        )
        QAccessible.updateAccessibility(event)


class SignatureImageDeleteDialog(QDialog):
    """Select one placement and one explicit case-asset cleanup policy."""

    delete_requested = Signal(object, object)
    cancel_operation_requested = Signal()

    def __init__(
        self,
        choices: tuple[SignatureImagePlacementChoice, ...],
        parent: QWidget | None = None,
    ) -> None:
        if not choices or any(
            type(choice) is not SignatureImagePlacementChoice for choice in choices
        ):
            raise ValueError("choices must contain signature-image placements")
        super().__init__(parent)
        self._pending = False
        self.setObjectName("signatureImageDeleteDialog")
        self.setWindowTitle(tr("Remove signature image"))
        self.setAccessibleName(tr("Remove signature image dialog"))
        self.setAccessibleDescription(
            tr(
                "Select one visual-image placement and explicitly choose whether its unused case copy may be removed."
            )
        )
        outer_layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setObjectName("signatureImageDeleteScrollArea")
        scroll.setAccessibleName(tr("Signature image removal content"))
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("signatureImageDeleteContent")
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        adapt_localized_content(scroll)
        outer_layout.addWidget(scroll)
        guidance = QLabel(
            tr(
                "Choose a placement to remove. This removes a visual image only; it does not change or validate any digital signature."
            )
        )
        guidance.setObjectName("signatureImageDeleteGuidance")
        guidance.setAccessibleName(
            tr("Signature image removal guidance. {value1}", value1=guidance.text())
        )
        guidance.setTextFormat(Qt.TextFormat.PlainText)
        guidance.setWordWrap(True)
        layout.addWidget(guidance)

        self._list = QListWidget()
        self._list.setObjectName("signatureImagePlacementList")
        self._list.setAccessibleName(tr("Signature-image placements on selected page"))
        self._list.setAccessibleDescription(
            tr("Select one placement by its position and normalized geometry.")
        )
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self._list.setWordWrap(True)
        for choice in choices:
            item = QListWidgetItem(choice.accessible_summary)
            item.setData(Qt.ItemDataRole.UserRole, choice.overlay_id)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, choice.accessible_summary)
            item.setData(
                Qt.ItemDataRole.AccessibleDescriptionRole,
                tr("Visual-image placement; select it, then choose an asset policy."),
            )
            self._list.addItem(item)
        self._list.setCurrentRow(0)
        layout.addWidget(self._list)

        policy_group = QGroupBox(tr("Case copy"))
        policy_group.setObjectName("signatureImageDeletePolicyGroup")
        policy_layout = QVBoxLayout(policy_group)
        self._keep = QRadioButton(tr(SIGNATURE_IMAGE_DELETE_KEEP_WORDING))
        self._keep.setObjectName("keepSignatureImageAssetRadio")
        self._keep.setAccessibleName(tr(SIGNATURE_IMAGE_DELETE_KEEP_WORDING))
        self._keep.setAccessibleDescription(tr(SIGNATURE_IMAGE_DELETE_KEEP_WORDING))
        self._remove_unused = QRadioButton(tr(SIGNATURE_IMAGE_DELETE_UNUSED_WORDING))
        self._remove_unused.setObjectName("removeUnusedSignatureImageAssetRadio")
        self._remove_unused.setAccessibleName(tr(SIGNATURE_IMAGE_DELETE_UNUSED_WORDING))
        self._remove_unused.setAccessibleDescription(tr(SIGNATURE_IMAGE_DELETE_UNUSED_WORDING))
        self._keep.setChecked(True)
        policy_layout.addLayout(_wrapped_choice(self._keep))
        policy_layout.addLayout(_wrapped_choice(self._remove_unused))
        layout.addWidget(policy_group)

        self._status = QLabel(tr("Review the placement and case-copy policy before removing."))
        self._status.setObjectName("signatureImageDeleteStatus")
        self._status.setAccessibleName(
            tr("Signature image removal status. {value1}", value1=self._status.text())
        )
        self._status.setAccessibleDescription(self._status.text())
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setWordWrap(True)
        self._status.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._status.setStyleSheet(_FOCUS_VISIBLE_TEXT_STYLE)
        layout.addWidget(self._status)

        buttons = QDialogButtonBox()
        adapt_dialog_button_box(buttons)
        self._remove = buttons.addButton(
            tr("Remove placement"),
            QDialogButtonBox.ButtonRole.DestructiveRole,
        )
        self._remove.setObjectName("confirmSignatureImageDeleteButton")
        self._remove.setAccessibleDescription(
            tr("Remove the selected placement using the explicit case-copy policy.")
        )
        self._cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._cancel.setObjectName("cancelSignatureImageDeleteButton")
        self._cancel.setAccessibleDescription(
            tr("Close before removal starts, or request cancellation while removal is pending.")
        )
        self._remove.clicked.connect(self._request_delete)
        self._cancel.clicked.connect(self._cancel_or_reject)
        layout.addWidget(buttons)

        chain: tuple[QWidget, ...] = (
            self._list,
            self._keep,
            self._remove_unused,
            self._status,
            self._remove,
            self._cancel,
        )
        for current, following in pairwise(chain):
            QWidget.setTabOrder(current, following)
        self._list.setFocus(Qt.FocusReason.OtherFocusReason)

    def mark_deleted(self) -> None:
        self._pending = False
        self.accept()

    def reject(self) -> None:
        """Request cooperative cancellation while the removal outcome is unknown."""

        if self._pending:
            self._request_pending_cancellation()
            return
        super().reject()

    def show_failure(self, code: object, message: object) -> None:
        del message
        self._pending = False
        self._list.setEnabled(True)
        self._keep.setEnabled(True)
        self._remove_unused.setEnabled(True)
        self._remove.setEnabled(True)
        self._remove.setText(tr("Retry removal"))
        self._cancel.setEnabled(True)
        self._set_status(_safe_failure(code, tr(_DELETE_FAILURE)), assertive=True)
        self._remove.setFocus(Qt.FocusReason.OtherFocusReason)

    def _request_delete(self) -> None:
        item = self._list.currentItem()
        overlay_id = None if item is None else item.data(Qt.ItemDataRole.UserRole)
        if type(overlay_id) is not UUID or self._pending:
            QApplication.beep()
            self._set_status(tr("Select one signature-image placement first."), assertive=True)
            return
        policy = (
            SignatureAssetDeletePolicy.REMOVE_IF_UNUSED
            if self._remove_unused.isChecked()
            else SignatureAssetDeletePolicy.KEEP_ASSET
        )
        self._pending = True
        self._list.setEnabled(False)
        self._keep.setEnabled(False)
        self._remove_unused.setEnabled(False)
        self._remove.setEnabled(False)
        self._cancel.setEnabled(True)
        self._set_status(tr("Removing the selected placement safely."), assertive=False)
        self.delete_requested.emit(overlay_id, policy)

    def _cancel_or_reject(self) -> None:
        if self._pending:
            self._request_pending_cancellation()
            return
        self.reject()

    def _request_pending_cancellation(self) -> None:
        if not self._cancel.isEnabled():
            return
        self._cancel.setEnabled(False)
        self._set_status(tr("Cancelling signature-image removal."), assertive=False)
        self.cancel_operation_requested.emit()

    def _set_status(self, text: str, *, assertive: bool) -> None:
        text = tr(text)
        self._status.setText(text)
        self._status.setAccessibleName(tr("Signature image removal status. {value1}", value1=text))
        self._status.setAccessibleDescription(tr(text))
        event = QAccessibleAnnouncementEvent(self._status, text)
        event.setPoliteness(
            QAccessible.AnnouncementPoliteness.Assertive
            if assertive
            else QAccessible.AnnouncementPoliteness.Polite
        )
        QAccessible.updateAccessibility(event)


def _checkerboard_preview(draft: SignatureImagePlacementDraft) -> QPixmap:
    preview = draft.preview
    source = QImage(
        preview.pixels,
        preview.width_pixels,
        preview.height_pixels,
        preview.row_stride_bytes,
        QImage.Format.Format_RGBA8888,
    ).copy()
    if source.isNull():
        raise ValueError("signature preview could not be detached")
    canvas = QImage(source.size(), QImage.Format.Format_RGBA8888)
    painter = QPainter(canvas)
    light = QColor("#f2f2f2")
    dark = QColor("#c9c9c9")
    for top in range(0, source.height(), _CHECKER_SIZE):
        for left in range(0, source.width(), _CHECKER_SIZE):
            color = light if (left // _CHECKER_SIZE + top // _CHECKER_SIZE) % 2 == 0 else dark
            painter.fillRect(left, top, _CHECKER_SIZE, _CHECKER_SIZE, color)
    painter.drawImage(0, 0, source)
    painter.end()
    return QPixmap.fromImage(canvas)


def _safe_failure(code: object, fallback: str) -> str:
    if type(code) is str and code in _SAFE_FAILURE_CODES:
        return fallback
    return tr("PaperWorkShell could not confirm that signature-image action safely.")

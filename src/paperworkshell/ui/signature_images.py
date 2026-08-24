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
from paperworkshell.overlays.models import OverlayErrorCode
from paperworkshell.overlays.signature_commands import SignatureAssetDeletePolicy

_SAFE_FAILURE_CODES: Final = frozenset(
    code.value
    for enum_type in (
        ManifestRepositoryErrorCode,
        OverlayErrorCode,
        SignatureImageErrorCode,
    )
    for code in enum_type
)
_PLACEMENT_FAILURE = (
    "PaperWorkShell could not place that visual signature image safely. "
    "Choose Retry to prepare it again, or Cancel."
)
_DELETE_FAILURE = (
    "PaperWorkShell could not remove that signature-image placement safely. "
    "Review the selection and try again."
)
_CHECKER_SIZE = 16


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
        self.setWindowTitle("Place signature image")
        self.setAccessibleName("Place signature image dialog")
        self.setAccessibleDescription(
            "Review the image, its background, and normalized placement. Confirm both statements "
            "before placing the visual image."
        )

        outer_layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setObjectName("signatureImagePlacementScrollArea")
        scroll.setAccessibleName("Signature image review content")
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("signatureImagePlacementContent")
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        outer_layout.addWidget(scroll)
        heading = QLabel("Place signature image")
        heading.setObjectName("signatureImageHeading")
        heading.setAccessibleName("Place signature image")
        heading.setStyleSheet("font-size: 18pt; font-weight: 600;")
        layout.addWidget(heading)

        disclaimer = QLabel(SIGNATURE_IMAGE_DISCLAIMER)
        disclaimer.setObjectName("signatureImageDisclaimer")
        disclaimer.setAccessibleName("Visual image disclaimer")
        disclaimer.setAccessibleDescription(SIGNATURE_IMAGE_DISCLAIMER)
        disclaimer.setTextFormat(Qt.TextFormat.PlainText)
        disclaimer.setWordWrap(True)
        disclaimer.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout.addWidget(disclaimer)

        self._preview = QLabel()
        self._preview.setObjectName("signatureImagePreview")
        self._preview.setAccessibleName("Signature image preview")
        self._preview.setAccessibleDescription(
            "Bounded visual preview over a checkerboard. The image itself is not described or "
            "read aloud."
        )
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setPixmap(_checkerboard_preview(draft))
        layout.addWidget(self._preview)

        review = QLabel(f"{draft.crop_review} {draft.background_review}")
        review.setObjectName("signatureImageCropBackgroundReview")
        review.setAccessibleName("Image area and background review")
        review.setAccessibleDescription(review.text())
        review.setTextFormat(Qt.TextFormat.PlainText)
        review.setWordWrap(True)
        review.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout.addWidget(review)

        placement_group = QGroupBox("Placement on selected page")
        placement_group.setObjectName("signatureImagePlacementGroup")
        placement_group.setAccessibleDescription(
            "Coordinates use millionths of the full original page. They continue to follow the "
            "page when it is rotated or cropped."
        )
        placement_layout = QGridLayout(placement_group)
        initial = default_signature_image_box()
        self._left = self._coordinate(
            "signatureImageLeft",
            "Left position",
            "Distance from the full page left edge in millionths.",
            0,
            initial.x,
        )
        self._top = self._coordinate(
            "signatureImageTop",
            "Top position",
            "Distance from the full page top edge in millionths.",
            0,
            initial.y,
        )
        self._width = self._coordinate(
            "signatureImageWidth",
            "Placement width",
            "Visual image width in full-page millionths.",
            1,
            initial.width,
        )
        self._height = self._coordinate(
            "signatureImageHeight",
            "Placement height",
            "Visual image height in full-page millionths.",
            1,
            initial.height,
        )
        for row, (label_text, control) in enumerate(
            (
                ("Left", self._left),
                ("Top", self._top),
                ("Width", self._width),
                ("Height", self._height),
            )
        ):
            label = QLabel(label_text)
            label.setBuddy(control)
            placement_layout.addWidget(label, row, 0)
            placement_layout.addWidget(control, row, 1)
        layout.addWidget(placement_group)

        self._disclaimer_confirmation = QCheckBox(
            "I understand this places a visual image only and creates no digital or qualified "
            "electronic signature."
        )
        self._disclaimer_confirmation.setObjectName("signatureImageDisclaimerConfirmation")
        self._disclaimer_confirmation.setAccessibleName("Confirm visual image only")
        self._disclaimer_confirmation.setAccessibleDescription(SIGNATURE_IMAGE_DISCLAIMER)
        self._whole_image_confirmation = QCheckBox(SIGNATURE_IMAGE_WHOLE_IMAGE_CONFIRMATION)
        self._whole_image_confirmation.setObjectName("signatureImageBackgroundConfirmation")
        self._whole_image_confirmation.setAccessibleName(
            "Confirm entire image and background review"
        )
        self._whole_image_confirmation.setAccessibleDescription(
            SIGNATURE_IMAGE_WHOLE_IMAGE_CONFIRMATION
        )
        self._disclaimer_confirmation.toggled.connect(self._update_place_enabled)
        self._whole_image_confirmation.toggled.connect(self._update_place_enabled)
        layout.addWidget(self._disclaimer_confirmation)
        layout.addWidget(self._whole_image_confirmation)

        self._status = QLabel("Review the preview and both confirmations before placing.")
        self._status.setObjectName("signatureImagePlacementStatus")
        self._status.setAccessibleName("Signature image placement status")
        self._status.setAccessibleDescription(self._status.text())
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setWordWrap(True)
        self._status.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout.addWidget(self._status)

        self._buttons = QDialogButtonBox()
        self._place_button = self._buttons.addButton(
            "Place visual image",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self._place_button.setObjectName("placeSignatureImageButton")
        self._place_button.setAccessibleName("Place signature image as a visual image")
        self._place_button.setAccessibleDescription(
            "Place the confirmed visual image at the normalized coordinates."
        )
        self._retry_button = self._buttons.addButton(
            "Retry",
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        self._retry_button.setObjectName("retrySignatureImageButton")
        self._retry_button.setAccessibleDescription(
            "Prepare the selected local image again after a failed placement."
        )
        self._retry_button.setVisible(False)
        self._cancel_button = self._buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._cancel_button.setObjectName("cancelSignatureImageButton")
        self._cancel_button.setAccessibleDescription(
            "Cancel and discard the private prepared case copy."
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
        self._set_status(_safe_failure(code, _PLACEMENT_FAILURE), assertive=True)
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
        control.setAccessibleName(accessible_name)
        control.setAccessibleDescription(description)
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
                "The placement must remain within the full normalized page.",
                assertive=False,
            )

    def _request_place(self) -> None:
        box = self._box()
        if box is None or not self._place_button.isEnabled():
            QApplication.beep()
            self._set_status(
                "Review both confirmations and choose a placement inside the full page.",
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
        self._set_status("Placing the visual image. Keep this dialog open.", assertive=False)
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
        self._set_status("Cancelling the signature-image operation.", assertive=False)
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
        self._status.setText(text)
        self._status.setAccessibleDescription(text)
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
        self.setWindowTitle("Remove signature image")
        self.setAccessibleName("Remove signature image dialog")
        self.setAccessibleDescription(
            "Select one visual-image placement and explicitly choose whether its unused case "
            "copy may be removed."
        )
        outer_layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setObjectName("signatureImageDeleteScrollArea")
        scroll.setAccessibleName("Signature image removal content")
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("signatureImageDeleteContent")
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        outer_layout.addWidget(scroll)
        guidance = QLabel(
            "Choose a placement to remove. This removes a visual image only; it does not change "
            "or validate any digital signature."
        )
        guidance.setObjectName("signatureImageDeleteGuidance")
        guidance.setAccessibleName("Signature image removal guidance")
        guidance.setTextFormat(Qt.TextFormat.PlainText)
        guidance.setWordWrap(True)
        layout.addWidget(guidance)

        self._list = QListWidget()
        self._list.setObjectName("signatureImagePlacementList")
        self._list.setAccessibleName("Signature-image placements on selected page")
        self._list.setAccessibleDescription(
            "Select one placement by its position and normalized geometry."
        )
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        for choice in choices:
            item = QListWidgetItem(choice.accessible_summary)
            item.setData(Qt.ItemDataRole.UserRole, choice.overlay_id)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, choice.accessible_summary)
            item.setData(
                Qt.ItemDataRole.AccessibleDescriptionRole,
                "Visual-image placement; select it, then choose an asset policy.",
            )
            self._list.addItem(item)
        self._list.setCurrentRow(0)
        layout.addWidget(self._list)

        policy_group = QGroupBox("Case copy after removal")
        policy_group.setObjectName("signatureImageDeletePolicyGroup")
        policy_layout = QVBoxLayout(policy_group)
        self._keep = QRadioButton(SIGNATURE_IMAGE_DELETE_KEEP_WORDING)
        self._keep.setObjectName("keepSignatureImageAssetRadio")
        self._keep.setAccessibleName("Keep the case copy")
        self._keep.setAccessibleDescription(SIGNATURE_IMAGE_DELETE_KEEP_WORDING)
        self._remove_unused = QRadioButton(SIGNATURE_IMAGE_DELETE_UNUSED_WORDING)
        self._remove_unused.setObjectName("removeUnusedSignatureImageAssetRadio")
        self._remove_unused.setAccessibleName("Remove the unused case copy")
        self._remove_unused.setAccessibleDescription(SIGNATURE_IMAGE_DELETE_UNUSED_WORDING)
        self._keep.setChecked(True)
        policy_layout.addWidget(self._keep)
        policy_layout.addWidget(self._remove_unused)
        layout.addWidget(policy_group)

        self._status = QLabel("Review the placement and case-copy policy before removing.")
        self._status.setObjectName("signatureImageDeleteStatus")
        self._status.setAccessibleName("Signature image removal status")
        self._status.setAccessibleDescription(self._status.text())
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setWordWrap(True)
        self._status.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout.addWidget(self._status)

        buttons = QDialogButtonBox()
        self._remove = buttons.addButton(
            "Remove placement",
            QDialogButtonBox.ButtonRole.DestructiveRole,
        )
        self._remove.setObjectName("confirmSignatureImageDeleteButton")
        self._remove.setAccessibleDescription(
            "Remove the selected placement using the explicit case-copy policy."
        )
        self._cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._cancel.setObjectName("cancelSignatureImageDeleteButton")
        self._cancel.setAccessibleDescription(
            "Close before removal starts, or request cancellation while removal is pending."
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
        self._remove.setText("Retry removal")
        self._cancel.setEnabled(True)
        self._set_status(_safe_failure(code, _DELETE_FAILURE), assertive=True)
        self._remove.setFocus(Qt.FocusReason.OtherFocusReason)

    def _request_delete(self) -> None:
        item = self._list.currentItem()
        overlay_id = None if item is None else item.data(Qt.ItemDataRole.UserRole)
        if type(overlay_id) is not UUID or self._pending:
            QApplication.beep()
            self._set_status("Select one signature-image placement first.", assertive=True)
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
        self._set_status("Removing the selected placement safely.", assertive=False)
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
        self._set_status("Cancelling signature-image removal.", assertive=False)
        self.cancel_operation_requested.emit()

    def _set_status(self, text: str, *, assertive: bool) -> None:
        self._status.setText(text)
        self._status.setAccessibleDescription(text)
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
    return "PaperWorkShell could not confirm that signature-image action safely."

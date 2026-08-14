"""Accessible, provisional before-and-after scan review presentation."""

from __future__ import annotations

from decimal import Decimal
from itertools import pairwise
from typing import Final

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QAccessible,
    QAccessibleAnnouncementEvent,
    QFont,
    QImage,
    QPixmap,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from paperworkshell.app.scan_review import ScanReviewDraft, ScanReviewPreviewVariant
from paperworkshell.domain.image_pages import ImageQuarterTurn
from paperworkshell.domain.image_transforms import (
    MAX_BACKGROUND_CLEANUP_STRENGTH_MILLIONTHS,
    MAX_CONTRAST_FACTOR_MILLIONTHS,
    MAX_DESKEW_ANGLE_MILLIDEGREES,
    NORMALIZED_PAGE_MILLIONTHS,
    ImageNormalizedCrop,
    ImageTransformIntent,
)

_FOCUS_VISIBLE_TEXT_STYLE: Final = (
    "QLabel:focus { border: 2px solid palette(highlight); padding: 2px; }"
)
_PERCENT_SCALE: Final = Decimal("10000")
_DEGREE_SCALE: Final = Decimal("1000")
_KNOWN_PROGRESS_PHASES: Final[dict[str, str]] = {
    "lookup": "preparing",
    "generating": "building preview",
    "validating": "checking preview",
    "complete": "finishing",
}


class ScanReviewDialog(QDialog):
    """Keyboard-first scan adjustment review with explicit confirmation."""

    preview_requested = Signal(object)
    apply_requested = Signal(object)
    cancel_preview_requested = Signal()

    def __init__(
        self,
        draft: ScanReviewDraft,
        parent: QWidget | None = None,
    ) -> None:
        if type(draft) is not ScanReviewDraft:
            raise ValueError("draft must be a ScanReviewDraft")
        super().__init__(parent)
        self._draft = draft
        self._loading_draft: ScanReviewDraft | None = None
        self._adjusted_preview_draft: ScanReviewDraft | None = None
        self._original_pixmap: QPixmap | None = None
        self._adjusted_pixmap: QPixmap | None = None
        self._preview_active = False
        self._apply_active = False
        self._controls_valid = True
        self._updating_controls = False
        self._control_error_visible = False

        self.setObjectName("scanReviewDialog")
        self.setModal(True)
        self.setWindowTitle("Review scan adjustments")
        self.setAccessibleName("Review scan adjustments")
        self.setAccessibleDescription(
            "Compare the unchanged original with provisional scan adjustments. "
            "Apply is available only after the current adjustments are previewed. "
            "Cancel is the safe default."
        )

        content = QWidget()
        content.setObjectName("scanReviewContent")
        content_layout = QVBoxLayout(content)

        heading = QLabel("Review scan adjustments")
        heading.setObjectName("scanReviewHeading")
        heading.setAccessibleName("Review scan adjustments")
        heading.setWordWrap(True)
        heading_font = QFont(heading.font())
        heading_font.setBold(True)
        heading_font.setPointSizeF(max(heading_font.pointSizeF(), 14.0))
        heading.setFont(heading_font)
        content_layout.addWidget(heading)

        self._guidance = QLabel(
            "Preview changes before applying them. The original is never changed; once its "
            "preview is ready, it stays available for comparison."
        )
        self._guidance.setObjectName("scanReviewGuidance")
        self._guidance.setAccessibleName("Scan review guidance")
        self._guidance.setAccessibleDescription(self._guidance.text())
        self._guidance.setWordWrap(True)
        content_layout.addWidget(self._guidance)

        self._suggestion_notice = QLabel(
            "Suggested adjustments — needs review. Nothing has been applied."
        )
        self._suggestion_notice.setObjectName("scanReviewSuggestionNotice")
        self._suggestion_notice.setAccessibleName("Suggested adjustments need review")
        self._suggestion_notice.setAccessibleDescription(self._suggestion_notice.text())
        self._suggestion_notice.setWordWrap(True)
        self._suggestion_notice.setFrameShape(QFrame.Shape.StyledPanel)
        self._suggestion_notice.setVisible(draft.suggestion_present)
        content_layout.addWidget(self._suggestion_notice)

        compare_group = QGroupBox("Compare previews")
        compare_group.setObjectName("scanReviewCompareGroup")
        compare_group.setAccessibleName("Compare original and adjusted previews")
        compare_group.setAccessibleDescription(
            "Choose whether the original, adjusted, or both preview panels are shown."
        )
        compare_layout = QVBoxLayout(compare_group)
        compare_choices = QHBoxLayout()
        self._show_original = self._compare_choice(
            "Original",
            "scanReviewShowOriginalRadio",
            "Show only the unchanged original preview.",
        )
        self._show_adjusted = self._compare_choice(
            "Adjusted",
            "scanReviewShowAdjustedRadio",
            "Show only the provisional adjusted preview.",
        )
        self._show_side_by_side = self._compare_choice(
            "Side by side",
            "scanReviewShowSideBySideRadio",
            "Show the unchanged original and provisional adjusted previews together.",
        )
        self._show_side_by_side.setChecked(True)
        for choice in (
            self._show_original,
            self._show_adjusted,
            self._show_side_by_side,
        ):
            choice.toggled.connect(self._update_compare_visibility)
            compare_choices.addWidget(choice)
        compare_choices.addStretch(1)
        compare_layout.addLayout(compare_choices)

        preview_layout = QHBoxLayout()
        self._original_group, self._original_preview, self._original_status = self._preview_panel(
            title="Original",
            group_name="scanReviewOriginalGroup",
            preview_name="scanReviewOriginalPreview",
            status_name="scanReviewOriginalPreviewStatus",
            initial_status="Original preview not loaded.",
        )
        self._adjusted_group, self._adjusted_preview, self._adjusted_status = self._preview_panel(
            title="Adjusted",
            group_name="scanReviewAdjustedGroup",
            preview_name="scanReviewAdjustedPreview",
            status_name="scanReviewAdjustedPreviewStatus",
            initial_status="Adjusted preview not loaded.",
        )
        preview_layout.addWidget(self._original_group, 1)
        preview_layout.addWidget(self._adjusted_group, 1)
        compare_layout.addLayout(preview_layout)
        content_layout.addWidget(compare_group)

        adjustment_group = QGroupBox("Provisional adjustments")
        adjustment_group.setObjectName("scanReviewAdjustmentGroup")
        adjustment_group.setAccessibleName("Provisional scan adjustments")
        adjustment_group.setAccessibleDescription(
            "These values remain provisional until Apply adjustments is activated."
        )
        adjustment_layout = QGridLayout(adjustment_group)

        self._crop_left = self._percent_spin(
            "scanReviewCropLeftSpin",
            "Crop left edge",
            "Position of the retained page's left edge as a percentage. Use arrow keys to adjust.",
        )
        self._crop_top = self._percent_spin(
            "scanReviewCropTopSpin",
            "Crop top edge",
            "Position of the retained page's top edge as a percentage. Use arrow keys to adjust.",
        )
        self._crop_right = self._percent_spin(
            "scanReviewCropRightSpin",
            "Crop right edge",
            "Position of the retained page's right edge as a percentage. Use arrow keys to adjust.",
        )
        self._crop_bottom = self._percent_spin(
            "scanReviewCropBottomSpin",
            "Crop bottom edge",
            "Position of the retained page's bottom edge as a percentage. Use arrow keys to adjust.",
        )
        self._add_labeled_control(
            adjustment_layout,
            0,
            0,
            "Left edge",
            "scanReviewCropLeftLabel",
            self._crop_left,
        )
        self._add_labeled_control(
            adjustment_layout,
            0,
            2,
            "Top edge",
            "scanReviewCropTopLabel",
            self._crop_top,
        )
        self._add_labeled_control(
            adjustment_layout,
            1,
            0,
            "Right edge",
            "scanReviewCropRightLabel",
            self._crop_right,
        )
        self._add_labeled_control(
            adjustment_layout,
            1,
            2,
            "Bottom edge",
            "scanReviewCropBottomLabel",
            self._crop_bottom,
        )

        self._rotation = QComboBox()
        self._rotation.setObjectName("scanReviewRotationCombo")
        self._rotation.setAccessibleName("Clockwise page rotation")
        self._rotation.setAccessibleDescription(
            "Choose no rotation or a clockwise quarter-turn using the keyboard."
        )
        for turn in ImageQuarterTurn:
            label = "No rotation" if int(turn) == 0 else f"{int(turn)} degrees clockwise"
            self._rotation.addItem(label, int(turn))
        self._add_labeled_control(
            adjustment_layout,
            2,
            0,
            "Rotation",
            "scanReviewRotationLabel",
            self._rotation,
        )

        self._deskew = QDoubleSpinBox()
        self._deskew.setObjectName("scanReviewDeskewSpin")
        self._deskew.setAccessibleName("Fine deskew angle")
        self._deskew.setAccessibleDescription(
            "Fine clockwise deskew angle in degrees. Negative values turn counterclockwise. "
            "Use arrow keys to adjust."
        )
        self._deskew.setDecimals(3)
        self._deskew.setRange(
            -MAX_DESKEW_ANGLE_MILLIDEGREES / 1000,
            MAX_DESKEW_ANGLE_MILLIDEGREES / 1000,
        )
        self._deskew.setSingleStep(0.1)
        self._deskew.setSuffix(" degrees")
        self._add_labeled_control(
            adjustment_layout,
            2,
            2,
            "Deskew",
            "scanReviewDeskewLabel",
            self._deskew,
        )

        self._contrast = self._factor_spin(
            "scanReviewContrastSpin",
            "Contrast",
            "Contrast percentage. One hundred percent is unchanged. Use arrow keys to adjust.",
            MAX_CONTRAST_FACTOR_MILLIONTHS,
        )
        self._cleanup = self._factor_spin(
            "scanReviewCleanupSpin",
            "Background cleanup",
            "Background cleanup strength. Zero percent is unchanged. Use arrow keys to adjust.",
            MAX_BACKGROUND_CLEANUP_STRENGTH_MILLIONTHS,
        )
        self._add_labeled_control(
            adjustment_layout,
            3,
            0,
            "Contrast",
            "scanReviewContrastLabel",
            self._contrast,
        )
        self._add_labeled_control(
            adjustment_layout,
            3,
            2,
            "Background cleanup",
            "scanReviewCleanupLabel",
            self._cleanup,
        )
        content_layout.addWidget(adjustment_group)

        action_layout = QHBoxLayout()
        self._update_preview_button = QPushButton("Update preview")
        self._update_preview_button.setObjectName("scanReviewUpdatePreviewButton")
        self._update_preview_button.setAccessibleName("Update adjusted preview")
        self._update_preview_button.setAccessibleDescription(
            "Generate the current provisional adjusted preview. The unchanged original is "
            "generated first only when it is not already available."
        )
        self._update_preview_button.clicked.connect(self._request_preview)
        self._reset_button = QPushButton("Reset to original")
        self._reset_button.setObjectName("scanReviewResetOriginalButton")
        self._reset_button.setAccessibleName("Reset adjustments to original")
        self._reset_button.setAccessibleDescription(
            "Reset the provisional crop, rotation, deskew, contrast, and cleanup to the "
            "unchanged imported page. This does not change the case until Apply adjustments."
        )
        self._reset_button.clicked.connect(self._reset_to_original)
        action_layout.addWidget(self._update_preview_button)
        action_layout.addWidget(self._reset_button)
        action_layout.addStretch(1)
        content_layout.addLayout(action_layout)

        self._status = QLabel("Choose Update preview to compare the original and adjusted page.")
        self._status.setObjectName("scanReviewStatus")
        self._status.setAccessibleName("Scan review status")
        self._status.setAccessibleDescription(self._status.text())
        self._status.setWordWrap(True)
        self._status.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._status.setStyleSheet(_FOCUS_VISIBLE_TEXT_STYLE)
        content_layout.addWidget(self._status)

        self._error = QLabel()
        self._error.setObjectName("scanReviewError")
        self._error.setAccessibleName("Scan review error")
        self._error.setAccessibleDescription(
            "A safe error code and recovery message for the current scan review."
        )
        self._error.setWordWrap(True)
        self._error.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._error.setStyleSheet(_FOCUS_VISIBLE_TEXT_STYLE)
        self._error.setVisible(False)
        content_layout.addWidget(self._error)

        self._progress = QProgressBar()
        self._progress.setObjectName("scanReviewProgress")
        self._progress.setAccessibleName("Scan review preview progress")
        self._progress.setAccessibleDescription(
            "Progress while PaperWorkShell builds and checks the provisional preview."
        )
        self._progress.setTextVisible(True)
        self._progress.setVisible(False)
        content_layout.addWidget(self._progress)

        self._cancel_preview_button = QPushButton("Cancel preview")
        self._cancel_preview_button.setObjectName("scanReviewCancelPreviewButton")
        self._cancel_preview_button.setAccessibleName("Cancel scan review preview")
        self._cancel_preview_button.setAccessibleDescription(
            "Cancel the running preview without applying adjustments. Escape."
        )
        self._cancel_preview_button.clicked.connect(self._request_preview_cancel)
        self._cancel_preview_button.setVisible(False)
        content_layout.addWidget(self._cancel_preview_button)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("scanReviewScrollArea")
        self._scroll.setAccessibleName("Scan review content")
        self._scroll.setAccessibleDescription(
            "Scrollable before-and-after previews and keyboard-adjustable scan controls."
        )
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._scroll.setWidget(content)
        self._scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        button_box = QDialogButtonBox()
        button_box.setObjectName("scanReviewButtons")
        button_box.setAccessibleName("Apply scan adjustments or cancel")
        button_box.setAccessibleDescription(
            "Cancel is selected by default. Apply remains unavailable until the exact current "
            "adjustments have a successful original and adjusted preview."
        )
        self._apply_button = QPushButton("Apply adjustments")
        self._apply_button.setObjectName("scanReviewApplyAdjustmentsButton")
        self._apply_button.setAccessibleName("Apply scan adjustments")
        self._apply_button.setAccessibleDescription(
            "Confirm the successfully previewed adjustments for this case without changing the "
            "original."
        )
        self._apply_button.setDefault(False)
        self._apply_button.setAutoDefault(False)
        self._apply_button.setEnabled(False)
        self._apply_button.clicked.connect(self._request_apply)
        button_box.addButton(self._apply_button, QDialogButtonBox.ButtonRole.AcceptRole)
        cancel = button_box.addButton(QDialogButtonBox.StandardButton.Cancel)
        if not isinstance(cancel, QPushButton):
            raise RuntimeError("Qt did not create the required Cancel button")
        self._cancel_button = cancel
        self._cancel_button.setObjectName("scanReviewCancelButton")
        self._cancel_button.setAccessibleName("Cancel scan review")
        self._cancel_button.setAccessibleDescription(
            "Stop an active preview first. When preview work is idle, close scan review without "
            "applying provisional adjustments."
        )
        self._cancel_button.setDefault(True)
        self._cancel_button.setAutoDefault(True)
        self._cancel_button.clicked.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._scroll, 1)
        layout.addWidget(button_box)

        self._adjustment_controls: tuple[QWidget, ...] = (
            self._crop_left,
            self._crop_top,
            self._crop_right,
            self._crop_bottom,
            self._rotation,
            self._deskew,
            self._contrast,
            self._cleanup,
        )
        for control in self._adjustment_controls:
            if isinstance(control, QComboBox):
                control.currentIndexChanged.connect(self._controls_changed)
            elif isinstance(control, QDoubleSpinBox):
                control.valueChanged.connect(self._controls_changed)

        tab_chain: tuple[QWidget, ...] = (
            self._cancel_button,
            self._scroll,
            self._show_original,
            self._show_adjusted,
            self._show_side_by_side,
            self._original_preview,
            self._adjusted_preview,
            *self._adjustment_controls,
            self._update_preview_button,
            self._reset_button,
            self._status,
            self._error,
            self._cancel_preview_button,
            self._apply_button,
            self._cancel_button,
        )
        for current, following in pairwise(tab_chain):
            QWidget.setTabOrder(current, following)

        self._load_controls(draft.candidate_intent)
        self._update_compare_visibility()
        self.resize(820, 720)
        self._cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)

    @property
    def draft(self) -> ScanReviewDraft:
        """Return the current detached provisional draft."""

        return self._draft

    @property
    def preview_ready_for_apply(self) -> bool:
        """Return whether both previews prove the exact current candidate."""

        return self._has_current_preview_proof()

    def set_preview_loading(self, draft: ScanReviewDraft) -> bool:
        """Mark one matching preview request active without clearing the original."""

        if not self._matches_current_candidate(draft) or self._preview_active or self._apply_active:
            return False
        self._loading_draft = draft
        self._preview_active = True
        self._adjusted_preview_draft = None
        self._adjusted_pixmap = None
        self._adjusted_preview.clear()
        self._adjusted_preview.setText("Adjusted preview loading.")
        self._set_preview_status(self._adjusted_status, "Adjusted preview loading.")
        original_required = self._original_pixmap is None
        if original_required:
            self._original_preview.clear()
            self._original_preview.setText("Original preview loading.")
            self._set_preview_status(self._original_status, "Original preview loading.")
        self._progress.setRange(0, 0)
        self._progress.setFormat("Preview starting")
        self._progress.setAccessibleName("Scan review preview progress")
        self._progress.setAccessibleDescription(
            "Progress while PaperWorkShell builds and checks the provisional preview."
        )
        self._progress.setVisible(True)
        self._cancel_preview_button.setVisible(True)
        self._cancel_preview_button.setEnabled(True)
        self._set_busy_controls()
        self._hide_error()
        self._set_status(
            "Building original and adjusted previews. Adjustments are not applied."
            if original_required
            else "Building adjusted preview. The original remains available. Adjustments are not applied."
        )
        self._update_apply_availability()
        return True

    def set_original_image(self, draft: ScanReviewDraft, image: QImage) -> bool:
        """Retain one detached original image for this exact review session."""

        if not self._matches_current_session(draft):
            return False
        pixmap = self._detached_pixmap(image)
        if pixmap is None:
            return False
        self._original_pixmap = pixmap
        self._original_preview.setText("")
        self._original_preview.setPixmap(pixmap)
        self._set_preview_status(self._original_status, "Original preview ready.")
        self._update_apply_availability()
        return True

    def set_adjusted_image(self, draft: ScanReviewDraft, image: QImage) -> bool:
        """Accept adjusted pixels only for the exact current candidate."""

        if not self._preview_active or not self._matches_loading_draft(draft):
            return False
        pixmap = self._detached_pixmap(image)
        if pixmap is None:
            return False
        self._adjusted_pixmap = pixmap
        self._adjusted_preview_draft = draft
        self._adjusted_preview.setText("")
        self._adjusted_preview.setPixmap(pixmap)
        self._set_preview_status(self._adjusted_status, "Adjusted preview ready.")
        self._finish_preview_state()
        if self._original_pixmap is None:
            self._set_status("Adjusted preview ready. The original preview is still required.")
        else:
            self._set_status(
                "Original and adjusted previews are ready. Review them before applying adjustments."
            )
            self._announce("Original and adjusted previews are ready.")
        self._update_apply_availability()
        return True

    def set_progress(
        self,
        draft: ScanReviewDraft,
        phase: str,
        completed: int,
        total: int,
    ) -> bool:
        """Show one content-free progress checkpoint for a matching request."""

        if (
            not self._preview_active
            or not self._matches_loading_draft(draft)
            or type(phase) is not str
            or type(completed) is not int
            or type(total) is not int
            or not 1 <= total
            or not 0 <= completed <= total
        ):
            return False
        label = _KNOWN_PROGRESS_PHASES.get(phase, "working")
        self._progress.setRange(0, total)
        self._progress.setValue(completed)
        self._progress.setFormat(f"Preview: {label} — %v of %m")
        self._progress.setAccessibleDescription(
            f"Scan review preview {label}, {completed} of {total}."
        )
        self._set_status(f"Preview {label}: {completed} of {total}. Adjustments are not applied.")
        self._announce(f"Preview {label}: {completed} of {total}.")
        return True

    def set_failure(
        self,
        draft: ScanReviewDraft,
        code: str,
        message: str,
        *,
        variant: ScanReviewPreviewVariant | None = None,
    ) -> bool:
        """Display one sanitized failure bound to the active preview variant or apply."""

        if not self._matches_current_candidate(draft):
            return False
        safe_code, safe_message = self._safe_failure_text(code, message)
        if self._apply_active:
            self._apply_active = False
            self._progress.setVisible(False)
            self._set_busy_controls()
            self._set_status("Applying adjustments did not finish. Review the error and try again.")
        elif self._preview_active:
            if type(variant) is not ScanReviewPreviewVariant:
                return False
            self._set_preview_terminal_state(variant, "failed")
            self._finish_preview_state()
            self._set_status("Preview did not finish. Adjustments were not applied.")
        else:
            return False
        self._error.setText(f"{safe_code}: {safe_message}")
        self._error.setAccessibleDescription(self._error.text())
        self._error.setVisible(True)
        self._control_error_visible = False
        self._update_apply_availability()
        self._announce("Scan review action failed. Review the error and try again.", assertive=True)
        self._error.setFocus(Qt.FocusReason.OtherFocusReason)
        self._scroll.ensureWidgetVisible(self._error, 8, 8)
        return True

    def set_preview_cancelled(
        self,
        draft: ScanReviewDraft,
        variant: ScanReviewPreviewVariant,
    ) -> bool:
        """Complete cancellation for the exact active preview variant."""

        if (
            not self._preview_active
            or not self._matches_loading_draft(draft)
            or type(variant) is not ScanReviewPreviewVariant
        ):
            return False
        self._set_preview_terminal_state(variant, "cancelled")
        self._finish_preview_state()
        self._set_status("Preview cancelled. Adjustments were not applied.")
        self._announce("Preview cancelled. Adjustments were not applied.", assertive=True)
        self._update_preview_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._scroll.ensureWidgetVisible(self._update_preview_button, 8, 8)
        return True

    def set_apply_complete(
        self,
        draft: ScanReviewDraft,
        *,
        changes_applied: bool,
    ) -> bool:
        """Accept after an exact Apply result with truthful changed/no-change wording."""

        if (
            not self._apply_active
            or not self._matches_current_candidate(draft)
            or type(changes_applied) is not bool
        ):
            return False
        self._apply_active = False
        if changes_applied:
            self._set_status(
                "Scan adjustments applied to this case. The original remains unchanged."
            )
            self._announce("Scan adjustments applied.")
        else:
            self._set_status(
                "Scan review complete. No changes were applied. The original remains unchanged."
            )
            self._announce("Scan review complete. No changes were applied.")
        super().accept()
        return True

    def reject(self) -> None:
        """Cancel active preview first; otherwise close without applying."""

        if self._apply_active:
            self._announce("Wait for the apply action to finish.", assertive=True)
            return
        if self._preview_active:
            self._request_preview_cancel()
            return
        super().reject()

    def done(self, result: int) -> None:
        """Release both disposable preview pixmaps as soon as review closes."""

        self._original_pixmap = None
        self._adjusted_pixmap = None
        self._original_preview.clear()
        self._adjusted_preview.clear()
        super().done(result)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt override name
        """Restore the safe default focus target whenever the dialog is shown."""

        super().showEvent(event)
        self._cancel_button.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def _compare_choice(self, text: str, object_name: str, description: str) -> QRadioButton:
        choice = QRadioButton(text)
        choice.setObjectName(object_name)
        choice.setAccessibleName(text)
        choice.setAccessibleDescription(description)
        return choice

    def _preview_panel(
        self,
        *,
        title: str,
        group_name: str,
        preview_name: str,
        status_name: str,
        initial_status: str,
    ) -> tuple[QGroupBox, QLabel, QLabel]:
        group = QGroupBox(title)
        group.setObjectName(group_name)
        group.setAccessibleName(f"{title} scan preview panel")
        group.setAccessibleDescription(
            f"{title} preview pixels and a truthful textual availability state."
        )
        layout = QVBoxLayout(group)
        preview = QLabel(initial_status)
        preview.setObjectName(preview_name)
        preview.setAccessibleName(f"{title} scan preview")
        preview.setAccessibleDescription(initial_status)
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview.setFrameShape(QFrame.Shape.StyledPanel)
        preview.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        preview.setStyleSheet(_FOCUS_VISIBLE_TEXT_STYLE)
        preview.setWordWrap(True)
        preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        status = QLabel(initial_status)
        status.setObjectName(status_name)
        status.setAccessibleName(f"{title} preview status")
        status.setAccessibleDescription(initial_status)
        status.setWordWrap(True)
        layout.addWidget(preview, 1)
        layout.addWidget(status)
        return group, preview, status

    def _percent_spin(
        self,
        object_name: str,
        accessible_name: str,
        description: str,
    ) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setObjectName(object_name)
        spin.setAccessibleName(accessible_name)
        spin.setAccessibleDescription(description)
        spin.setDecimals(4)
        spin.setRange(0.0, 100.0)
        spin.setSingleStep(0.1)
        spin.setSuffix(" %")
        return spin

    def _factor_spin(
        self,
        object_name: str,
        accessible_name: str,
        description: str,
        maximum_millionths: int,
    ) -> QDoubleSpinBox:
        spin = self._percent_spin(object_name, accessible_name, description)
        spin.setRange(0.0, maximum_millionths / 10_000)
        spin.setSingleStep(1.0)
        return spin

    def _add_labeled_control(
        self,
        layout: QGridLayout,
        row: int,
        column: int,
        text: str,
        object_name: str,
        control: QWidget,
    ) -> None:
        label = QLabel(text)
        label.setObjectName(object_name)
        label.setAccessibleName(text)
        label.setBuddy(control)
        layout.addWidget(label, row, column)
        layout.addWidget(control, row, column + 1)

    def _load_controls(self, intent: ImageTransformIntent) -> None:
        self._updating_controls = True
        try:
            crop = intent.crop
            self._crop_left.setValue(crop.x / 10_000)
            self._crop_top.setValue(crop.y / 10_000)
            self._crop_right.setValue((crop.x + crop.width) / 10_000)
            self._crop_bottom.setValue((crop.y + crop.height) / 10_000)
            rotation_index = self._rotation.findData(int(intent.additional_rotation))
            if rotation_index < 0:
                raise ValueError("unsupported scan-review rotation")
            self._rotation.setCurrentIndex(rotation_index)
            self._deskew.setValue(intent.deskew_angle_millidegrees_clockwise / 1000)
            self._contrast.setValue(intent.contrast_factor_millionths / 10_000)
            self._cleanup.setValue(intent.background_cleanup_strength_millionths / 10_000)
        finally:
            self._updating_controls = False

    def _controls_changed(self, _value: object = None) -> None:
        if self._updating_controls or self._preview_active or self._apply_active:
            return
        candidate = self._candidate_from_controls()
        if candidate is None:
            self._controls_valid = False
            self._adjusted_preview_draft = None
            self._clear_adjusted_preview("Adjusted preview unavailable until crop edges are valid.")
            self._show_control_error(
                "Crop edges must retain a positive page area: left before right and top before bottom."
            )
            self._set_busy_controls()
            self._update_apply_availability()
            return
        self._controls_valid = True
        self._hide_control_error()
        self._draft = self._draft.with_candidate(candidate, suggestion_present=False)
        self._suggestion_notice.setVisible(False)
        self._adjusted_preview_draft = None
        self._clear_adjusted_preview("Adjusted preview out of date; choose Update preview.")
        self._set_status("Adjustments changed. Update preview before applying them.")
        self._set_busy_controls()
        self._update_apply_availability()

    def _candidate_from_controls(self) -> ImageTransformIntent | None:
        left = self._scaled_integer(self._crop_left.value(), _PERCENT_SCALE)
        top = self._scaled_integer(self._crop_top.value(), _PERCENT_SCALE)
        right = self._scaled_integer(self._crop_right.value(), _PERCENT_SCALE)
        bottom = self._scaled_integer(self._crop_bottom.value(), _PERCENT_SCALE)
        if not 0 <= left < right <= NORMALIZED_PAGE_MILLIONTHS:
            return None
        if not 0 <= top < bottom <= NORMALIZED_PAGE_MILLIONTHS:
            return None
        rotation_data = self._rotation.currentData()
        if type(rotation_data) is not int:
            return None
        return ImageTransformIntent(
            crop=ImageNormalizedCrop(left, top, right - left, bottom - top),
            additional_rotation=ImageQuarterTurn(rotation_data),
            deskew_angle_millidegrees_clockwise=self._scaled_integer(
                self._deskew.value(),
                _DEGREE_SCALE,
            ),
            contrast_factor_millionths=self._scaled_integer(
                self._contrast.value(),
                _PERCENT_SCALE,
            ),
            background_cleanup_strength_millionths=self._scaled_integer(
                self._cleanup.value(),
                _PERCENT_SCALE,
            ),
        )

    @staticmethod
    def _scaled_integer(value: float, scale: Decimal) -> int:
        return int((Decimal(str(value)) * scale).to_integral_value())

    def _request_preview(self) -> None:
        if (
            self._preview_active
            or self._apply_active
            or not self._controls_valid
            or not self.set_preview_loading(self._draft)
        ):
            return
        self.preview_requested.emit(self._draft)

    def _request_preview_cancel(self) -> None:
        if not self._preview_active or not self._cancel_preview_button.isEnabled():
            return
        self._cancel_preview_button.setEnabled(False)
        self._set_status("Cancellation requested. Adjustments have not been applied.")
        self._announce("Cancellation requested. Waiting for preview work to stop.", assertive=True)
        self.cancel_preview_requested.emit()

    def _request_apply(self) -> None:
        if not self._can_apply():
            return
        self._apply_active = True
        self._set_busy_controls()
        self._apply_button.setEnabled(False)
        self._cancel_button.setEnabled(False)
        self._hide_error()
        self._progress.setRange(0, 0)
        self._progress.setFormat("Applying adjustments")
        self._progress.setAccessibleName("Scan review apply progress")
        self._progress.setAccessibleDescription(
            "Progress while PaperWorkShell applies reviewed adjustments as case intent."
        )
        self._progress.setVisible(True)
        self._set_status("Applying the reviewed adjustments as case intent.")
        self._announce("Applying reviewed scan adjustments.")
        self.apply_requested.emit(self._draft)

    def _reset_to_original(self) -> None:
        if self._preview_active or self._apply_active:
            return
        self._draft = self._draft.reset_to_original()
        self._load_controls(self._draft.candidate_intent)
        self._controls_valid = True
        self._hide_control_error()
        self._suggestion_notice.setVisible(False)
        self._adjusted_preview_draft = None
        self._clear_adjusted_preview("Adjusted preview reset; choose Update preview.")
        self._set_status("Adjustments reset to the original page. Update preview before applying.")
        self._announce("Provisional adjustments reset to the original page.")
        self._update_apply_availability()

    def _update_compare_visibility(self, _checked: bool = False) -> None:
        show_original = self._show_original.isChecked() or self._show_side_by_side.isChecked()
        show_adjusted = self._show_adjusted.isChecked() or self._show_side_by_side.isChecked()
        self._original_group.setVisible(show_original)
        self._adjusted_group.setVisible(show_adjusted)

    def _can_apply(self) -> bool:
        return (
            not self._preview_active
            and not self._apply_active
            and self._has_current_preview_proof()
        )

    def _has_current_preview_proof(self) -> bool:
        return (
            self._controls_valid
            and self._original_pixmap is not None
            and self._adjusted_pixmap is not None
            and self._adjusted_preview_draft is not None
            and self._matches_current_candidate(self._adjusted_preview_draft)
        )

    def _update_apply_availability(self) -> None:
        self._apply_button.setEnabled(self._can_apply())

    def _set_busy_controls(self) -> None:
        enabled = not self._preview_active and not self._apply_active
        for control in self._adjustment_controls:
            control.setEnabled(enabled)
        self._update_preview_button.setEnabled(enabled and self._controls_valid)
        self._reset_button.setEnabled(enabled)
        self._cancel_button.setEnabled(not self._apply_active)

    def _finish_preview_state(self) -> None:
        self._preview_active = False
        self._loading_draft = None
        self._progress.setVisible(False)
        if self._cancel_preview_button.hasFocus():
            self._update_preview_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._cancel_preview_button.setVisible(False)
        self._cancel_preview_button.setEnabled(False)
        self._set_busy_controls()

    def _clear_adjusted_preview(self, message: str) -> None:
        self._adjusted_pixmap = None
        self._adjusted_preview.clear()
        self._adjusted_preview.setText(message)
        self._set_preview_status(self._adjusted_status, message)

    def _set_preview_status(self, label: QLabel, message: str) -> None:
        label.setText(message)
        label.setAccessibleDescription(message)
        preview = (
            self._original_preview if label is self._original_status else self._adjusted_preview
        )
        preview.setAccessibleDescription(message)

    def _set_preview_terminal_state(
        self,
        variant: ScanReviewPreviewVariant,
        outcome: str,
    ) -> None:
        if variant is ScanReviewPreviewVariant.ORIGINAL:
            self._original_pixmap = None
            self._original_preview.clear()
            original_message = f"Original preview {outcome}."
            self._original_preview.setText(original_message)
            self._set_preview_status(self._original_status, original_message)
            self._adjusted_preview_draft = None
            self._adjusted_pixmap = None
            self._adjusted_preview.clear()
            adjusted_message = "Adjusted preview not produced."
            self._adjusted_preview.setText(adjusted_message)
            self._set_preview_status(self._adjusted_status, adjusted_message)
            return
        self._adjusted_preview_draft = None
        self._adjusted_pixmap = None
        self._adjusted_preview.clear()
        adjusted_message = f"Adjusted preview {outcome}."
        self._adjusted_preview.setText(adjusted_message)
        self._set_preview_status(self._adjusted_status, adjusted_message)

    def _set_status(self, message: str) -> None:
        self._status.setText(message)
        self._status.setAccessibleDescription(message)

    def _show_control_error(self, message: str) -> None:
        announce = not self._control_error_visible
        self._control_error_visible = True
        self._error.setText(message)
        self._error.setAccessibleDescription(message)
        self._error.setVisible(True)
        self._set_status("Adjust the crop edges before updating the preview.")
        if announce:
            self._announce("Crop edges are invalid. Adjust the crop controls.", assertive=True)

    def _hide_control_error(self) -> None:
        if self._control_error_visible:
            self._error.setVisible(False)
            self._control_error_visible = False

    def _hide_error(self) -> None:
        self._error.setVisible(False)
        self._control_error_visible = False

    def _matches_current_session(self, draft: object) -> bool:
        return (
            type(draft) is ScanReviewDraft
            and draft.case_id == self._draft.case_id
            and draft.page_id == self._draft.page_id
            and draft.manifest_revision == self._draft.manifest_revision
            and draft.base_intent == self._draft.base_intent
        )

    def _matches_current_candidate(self, draft: object) -> bool:
        return (
            self._matches_current_session(draft)
            and isinstance(draft, ScanReviewDraft)
            and draft.candidate_intent == self._draft.candidate_intent
        )

    def _matches_loading_draft(self, draft: object) -> bool:
        loading = self._loading_draft
        return (
            loading is not None
            and self._matches_current_candidate(draft)
            and isinstance(draft, ScanReviewDraft)
            and draft.candidate_intent == loading.candidate_intent
        )

    @staticmethod
    def _detached_pixmap(image: object) -> QPixmap | None:
        if not isinstance(image, QImage) or image.isNull():
            return None
        detached = image.copy()
        if detached.isNull():
            return None
        pixmap = QPixmap.fromImage(detached)
        return None if pixmap.isNull() else pixmap

    @staticmethod
    def _safe_failure_text(code: object, message: object) -> tuple[str, str]:
        if (
            type(code) is not str
            or not code
            or len(code) > 64
            or not code.isascii()
            or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for character in code)
        ):
            return (
                "PWS_UI_UNEXPECTED",
                "PaperWorkShell could not complete scan review. Try again.",
            )
        if (
            type(message) is not str
            or not message
            or len(message) > 500
            or any(ord(character) < 32 and character not in "\t\n" for character in message)
        ):
            return (
                "PWS_UI_UNEXPECTED",
                "PaperWorkShell could not complete scan review. Try again.",
            )
        return code, message

    def _announce(self, message: str, *, assertive: bool = False) -> None:
        announcement = QAccessibleAnnouncementEvent(self._status, message)
        announcement.setPoliteness(
            QAccessible.AnnouncementPoliteness.Assertive
            if assertive
            else QAccessible.AnnouncementPoliteness.Polite
        )
        QAccessible.updateAccessibility(announcement)

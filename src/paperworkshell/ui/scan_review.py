"""Accessible, provisional before-and-after scan review presentation."""

from __future__ import annotations

from decimal import Decimal
from itertools import pairwise
from typing import Final

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QFocusEvent,
    QFont,
    QImage,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QResizeEvent,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QBoxLayout,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QGroupBox,
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
from paperworkshell.localization.catalog import mark, tr
from paperworkshell.ui.announcements import ScreenReaderAnnouncer
from paperworkshell.ui.localization_layout import adapt_localized_content
from paperworkshell.ui.presentation import (
    WrappingActionLayout,
    adapt_dialog_button_box,
    set_static_busy,
)
from paperworkshell.ui.wheel_safe import (
    WheelSafeComboBox as QComboBox,
)
from paperworkshell.ui.wheel_safe import (
    WheelSafeDoubleSpinBox as QDoubleSpinBox,
)

_FOCUS_VISIBLE_TEXT_STYLE: Final = (
    "QLabel:focus { border: 2px solid palette(highlight); padding: 2px; }"
)
_PERCENT_SCALE: Final = Decimal("10000")
_DEGREE_SCALE: Final = Decimal("1000")
_KNOWN_PROGRESS_PHASES: Final[dict[str, str]] = {
    "lookup": mark("preparing"),
    "generating": mark("building preview"),
    "validating": mark("checking preview"),
    "complete": mark("finishing"),
}


class _FitPreviewLabel(QLabel):
    """Fit the existing bounded raster without retaining another scaled bitmap."""

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(300, 340)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(120, 120)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt override
        pixmap = self.pixmap()
        if pixmap.isNull():
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().base())
        bounds = self.rect().adjusted(4, 4, -4, -4)
        target_size = pixmap.size().scaled(bounds.size(), Qt.AspectRatioMode.KeepAspectRatio)
        target = QRect(0, 0, target_size.width(), target_size.height())
        target.moveCenter(bounds.center())
        painter.drawPixmap(target, pixmap)
        pen = QPen(
            self.palette().highlight().color() if self.hasFocus() else self.palette().mid().color()
        )
        pen.setWidth(2 if self.hasFocus() else 1)
        if self.hasFocus():
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRect(self.rect().adjusted(1, 1, -2, -2))

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802 - Qt override
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802 - Qt override
        super().focusOutEvent(event)
        self.update()


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
        self._announcer = ScreenReaderAnnouncer(self)
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
        self.setWindowTitle(tr("Review scan adjustments"))
        self.setAccessibleName(tr("Review scan adjustments"))
        self.setAccessibleDescription(
            tr(
                "Compare the unchanged original with provisional scan adjustments. Apply is available only after the current adjustments are previewed. Cancel is the safe default."
            )
        )

        content = QWidget()
        content.setObjectName("scanReviewContent")
        content_layout = QVBoxLayout(content)

        heading = QLabel(tr("Review scan adjustments"))
        heading.setObjectName("scanReviewHeading")
        heading.setAccessibleName(tr("Review scan adjustments"))
        heading.setWordWrap(True)
        heading_font = QFont(heading.font())
        heading_font.setBold(True)
        heading_font.setPointSizeF(max(heading_font.pointSizeF(), 14.0))
        heading.setFont(heading_font)
        content_layout.addWidget(heading)

        self._guidance = QLabel(
            tr(
                "Preview changes before applying them. The original is never changed; once its preview is ready, it stays available for comparison."
            )
        )
        self._guidance.setObjectName("scanReviewGuidance")
        self._guidance.setAccessibleName(
            tr("Scan review guidance: {value1}", value1=self._guidance.text())
        )
        self._guidance.setAccessibleDescription(self._guidance.text())
        self._guidance.setWordWrap(True)
        content_layout.addWidget(self._guidance)

        self._suggestion_notice = QLabel(
            tr("Suggested adjustments — needs review. Nothing has been applied.")
        )
        self._suggestion_notice.setObjectName("scanReviewSuggestionNotice")
        self._suggestion_notice.setAccessibleName(self._suggestion_notice.text())
        self._suggestion_notice.setAccessibleDescription(self._suggestion_notice.text())
        self._suggestion_notice.setWordWrap(True)
        self._suggestion_notice.setFrameShape(QFrame.Shape.StyledPanel)
        self._suggestion_notice.setVisible(draft.suggestion_present)
        content_layout.addWidget(self._suggestion_notice)

        compare_group = QGroupBox(tr("Compare previews"))
        compare_group.setObjectName("scanReviewCompareGroup")
        compare_group.setAccessibleName(tr("Compare original and adjusted previews"))
        compare_group.setAccessibleDescription(
            tr("Choose whether the original, adjusted, or both preview panels are shown.")
        )
        compare_layout = QVBoxLayout(compare_group)
        compare_choices = WrappingActionLayout()
        self._show_original = self._compare_choice(
            tr("Original"),
            "scanReviewShowOriginalRadio",
            tr("Show only the unchanged original preview."),
        )
        self._show_adjusted = self._compare_choice(
            tr("Adjusted"),
            "scanReviewShowAdjustedRadio",
            tr("Show only the provisional adjusted preview."),
        )
        self._show_side_by_side = self._compare_choice(
            tr("Side by side"),
            "scanReviewShowSideBySideRadio",
            tr("Show the unchanged original and provisional adjusted previews together."),
        )
        self._show_side_by_side.setChecked(True)
        for choice in (
            self._show_original,
            self._show_adjusted,
            self._show_side_by_side,
        ):
            choice.toggled.connect(self._update_compare_visibility)
            compare_choices.addWidget(choice)
        compare_layout.addLayout(compare_choices)

        self._preview_layout = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self._original_group, self._original_preview, self._original_status = self._preview_panel(
            title=tr("Original"),
            group_name="scanReviewOriginalGroup",
            preview_name="scanReviewOriginalPreview",
            status_name="scanReviewOriginalPreviewStatus",
            initial_status=tr("Original preview not loaded."),
        )
        self._adjusted_group, self._adjusted_preview, self._adjusted_status = self._preview_panel(
            title=tr("Adjusted"),
            group_name="scanReviewAdjustedGroup",
            preview_name="scanReviewAdjustedPreview",
            status_name="scanReviewAdjustedPreviewStatus",
            initial_status=tr("Adjusted preview not loaded."),
        )
        self._preview_layout.addWidget(self._original_group, 1)
        self._preview_layout.addWidget(self._adjusted_group, 1)
        compare_layout.addLayout(self._preview_layout)
        content_layout.addWidget(compare_group)

        adjustment_group = QGroupBox(tr("Adjustments"))
        adjustment_group.setObjectName("scanReviewAdjustmentGroup")
        adjustment_group.setAccessibleName(tr("Provisional scan adjustments"))
        adjustment_group.setAccessibleDescription(
            tr("These values remain provisional until Apply adjustments is activated.")
        )
        adjustment_layout = QGridLayout(adjustment_group)

        self._crop_left = self._percent_spin(
            "scanReviewCropLeftSpin",
            tr("Crop left edge"),
            tr(
                "Position of the retained page's left edge as a percentage. Use arrow keys to adjust."
            ),
        )
        self._crop_top = self._percent_spin(
            "scanReviewCropTopSpin",
            tr("Crop top edge"),
            tr(
                "Position of the retained page's top edge as a percentage. Use arrow keys to adjust."
            ),
        )
        self._crop_right = self._percent_spin(
            "scanReviewCropRightSpin",
            tr("Crop right edge"),
            tr(
                "Position of the retained page's right edge as a percentage. Use arrow keys to adjust."
            ),
        )
        self._crop_bottom = self._percent_spin(
            "scanReviewCropBottomSpin",
            tr("Crop bottom edge"),
            tr(
                "Position of the retained page's bottom edge as a percentage. Use arrow keys to adjust."
            ),
        )
        self._add_labeled_control(
            adjustment_layout,
            0,
            0,
            tr("Left edge"),
            "scanReviewCropLeftLabel",
            self._crop_left,
        )
        self._add_labeled_control(
            adjustment_layout,
            0,
            2,
            tr("Top edge"),
            "scanReviewCropTopLabel",
            self._crop_top,
        )
        self._add_labeled_control(
            adjustment_layout,
            1,
            0,
            tr("Right edge"),
            "scanReviewCropRightLabel",
            self._crop_right,
        )
        self._add_labeled_control(
            adjustment_layout,
            1,
            2,
            tr("Bottom edge"),
            "scanReviewCropBottomLabel",
            self._crop_bottom,
        )

        self._rotation = QComboBox()
        self._rotation.setObjectName("scanReviewRotationCombo")
        self._rotation.setAccessibleName(tr("Clockwise page rotation"))
        self._rotation.setAccessibleDescription(
            tr("Choose no rotation or a clockwise quarter-turn using the keyboard.")
        )
        for turn in ImageQuarterTurn:
            label = (
                tr("No rotation")
                if int(turn) == 0
                else tr("{value1} degrees clockwise", value1=int(turn))
            )
            self._rotation.addItem(label, int(turn))
        self._add_labeled_control(
            adjustment_layout,
            2,
            0,
            tr("Rotation"),
            "scanReviewRotationLabel",
            self._rotation,
        )

        self._deskew = QDoubleSpinBox()
        self._deskew.setObjectName("scanReviewDeskewSpin")
        self._deskew.setAccessibleName(tr("Fine deskew angle"))
        self._deskew.setAccessibleDescription(
            tr(
                "Fine clockwise deskew angle in degrees. Negative values turn counterclockwise. Use arrow keys to adjust."
            )
        )
        self._deskew.setDecimals(3)
        self._deskew.setRange(
            -MAX_DESKEW_ANGLE_MILLIDEGREES / 1000,
            MAX_DESKEW_ANGLE_MILLIDEGREES / 1000,
        )
        self._deskew.setSingleStep(0.1)
        self._deskew.setSuffix(tr(" degrees"))
        self._add_labeled_control(
            adjustment_layout,
            2,
            2,
            tr("Deskew"),
            "scanReviewDeskewLabel",
            self._deskew,
        )

        self._contrast = self._factor_spin(
            "scanReviewContrastSpin",
            tr("Contrast"),
            tr("Contrast percentage. One hundred percent is unchanged. Use arrow keys to adjust."),
            MAX_CONTRAST_FACTOR_MILLIONTHS,
        )
        self._cleanup = self._factor_spin(
            "scanReviewCleanupSpin",
            tr("Background cleanup"),
            tr("Background cleanup strength. Zero percent is unchanged. Use arrow keys to adjust."),
            MAX_BACKGROUND_CLEANUP_STRENGTH_MILLIONTHS,
        )
        self._add_labeled_control(
            adjustment_layout,
            3,
            0,
            tr("Contrast"),
            "scanReviewContrastLabel",
            self._contrast,
        )
        self._add_labeled_control(
            adjustment_layout,
            3,
            2,
            tr("Background cleanup"),
            "scanReviewCleanupLabel",
            self._cleanup,
        )
        content_layout.addWidget(adjustment_group)

        action_layout = WrappingActionLayout()
        self._update_preview_button = QPushButton(tr("Update preview"))
        self._update_preview_button.setObjectName("scanReviewUpdatePreviewButton")
        self._update_preview_button.setAccessibleName(tr("Update adjusted preview"))
        self._update_preview_button.setAccessibleDescription(
            tr(
                "Generate the current provisional adjusted preview. The unchanged original is generated first only when it is not already available."
            )
        )
        self._update_preview_button.clicked.connect(self._request_preview)
        self._reset_button = QPushButton(tr("Reset to original"))
        self._reset_button.setObjectName("scanReviewResetOriginalButton")
        self._reset_button.setAccessibleName(tr("Reset adjustments to original"))
        self._reset_button.setAccessibleDescription(
            tr(
                "Reset the provisional crop, rotation, deskew, contrast, and cleanup to the unchanged imported page. This does not change the case until Apply adjustments."
            )
        )
        self._reset_button.clicked.connect(self._reset_to_original)
        action_layout.addWidget(self._update_preview_button)
        action_layout.addWidget(self._reset_button)
        content_layout.addLayout(action_layout)

        self._status = QLabel(
            tr("Choose Update preview to compare the original and adjusted page.")
        )
        self._status.setObjectName("scanReviewStatus")
        self._status.setAccessibleName(
            tr("Scan review status: {value1}", value1=self._status.text())
        )
        self._status.setAccessibleDescription(self._status.text())
        self._status.setWordWrap(True)
        self._status.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._status.setStyleSheet(_FOCUS_VISIBLE_TEXT_STYLE)
        content_layout.addWidget(self._status)

        self._error = QLabel()
        self._error.setObjectName("scanReviewError")
        self._error.setAccessibleName(tr("Scan review error"))
        self._error.setAccessibleDescription(
            tr("A safe error code and recovery message for the current scan review.")
        )
        self._error.setWordWrap(True)
        self._error.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._error.setStyleSheet(_FOCUS_VISIBLE_TEXT_STYLE)
        self._error.setVisible(False)
        content_layout.addWidget(self._error)

        self._progress = QProgressBar()
        self._progress.setObjectName("scanReviewProgress")
        self._progress.setAccessibleName(tr("Scan review preview progress"))
        self._progress.setAccessibleDescription(
            tr("Progress while PaperWorkShell builds and checks the provisional preview.")
        )
        self._progress.setTextVisible(True)
        self._progress.setVisible(False)
        content_layout.addWidget(self._progress)

        self._cancel_preview_button = QPushButton(tr("Cancel preview"))
        self._cancel_preview_button.setObjectName("scanReviewCancelPreviewButton")
        self._cancel_preview_button.setAccessibleName(tr("Cancel scan review preview"))
        self._cancel_preview_button.setAccessibleDescription(
            tr("Cancel the running preview without applying adjustments. Escape.")
        )
        self._cancel_preview_button.clicked.connect(self._request_preview_cancel)
        self._cancel_preview_button.setVisible(False)
        content_layout.addWidget(self._cancel_preview_button)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("scanReviewScrollArea")
        self._scroll.setAccessibleName(tr("Scan review content"))
        self._scroll.setAccessibleDescription(
            tr("Scrollable before-and-after previews and keyboard-adjustable scan controls.")
        )
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._scroll.setWidget(content)
        adapt_localized_content(self._scroll)
        self._scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        button_box = QDialogButtonBox()
        adapt_dialog_button_box(button_box)
        button_box.setObjectName("scanReviewButtons")
        button_box.setAccessibleName(tr("Apply scan adjustments or cancel"))
        button_box.setAccessibleDescription(
            tr(
                "Cancel is selected by default. Apply remains unavailable until the exact current adjustments have a successful original and adjusted preview."
            )
        )
        self._apply_button = QPushButton(tr("Apply adjustments"))
        self._apply_button.setObjectName("scanReviewApplyAdjustmentsButton")
        self._apply_button.setAccessibleName(tr("Apply scan adjustments"))
        self._apply_button.setAccessibleDescription(
            tr(
                "Confirm the successfully previewed adjustments for this case without changing the original."
            )
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
        self._cancel_button.setAccessibleName(tr("Cancel scan review"))
        self._cancel_button.setAccessibleDescription(
            tr(
                "Stop an active preview first. When preview work is idle, close scan review without applying provisional adjustments."
            )
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
        self._announcer.reset_progress()
        self._preview_active = True
        self._adjusted_preview_draft = None
        self._adjusted_pixmap = None
        self._adjusted_preview.clear()
        self._adjusted_preview.setText(tr("Adjusted preview loading."))
        self._set_preview_status(self._adjusted_status, tr("Adjusted preview loading."))
        original_required = self._original_pixmap is None
        if original_required:
            self._original_preview.clear()
            self._original_preview.setText(tr("Original preview loading."))
            self._set_preview_status(self._original_status, tr("Original preview loading."))
        set_static_busy(self._progress)
        self._progress.setFormat(tr("Preview starting"))
        self._progress.setAccessibleName(tr("Scan review preview progress"))
        self._progress.setAccessibleDescription(
            tr("Progress while PaperWorkShell builds and checks the provisional preview.")
        )
        self._progress.setVisible(True)
        self._cancel_preview_button.setVisible(True)
        self._cancel_preview_button.setEnabled(True)
        self._set_busy_controls()
        self._hide_error()
        self._set_status(
            tr("Building original and adjusted previews. Adjustments are not applied.")
            if original_required
            else tr(
                "Building adjusted preview. The original remains available. Adjustments are not applied."
            )
        )
        self._announce(self._status.text())
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
        self._set_preview_status(self._original_status, tr("Original preview ready."))
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
        self._set_preview_status(self._adjusted_status, tr("Adjusted preview ready."))
        self._finish_preview_state()
        if self._original_pixmap is None:
            self._set_status(tr("Adjusted preview ready. The original preview is still required."))
        else:
            self._set_status(
                tr(
                    "Original and adjusted previews are ready. Review them before applying adjustments."
                )
            )
            self._announce(tr("Original and adjusted previews are ready."))
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
            or not self._cancel_preview_button.isEnabled()
            or not self._matches_loading_draft(draft)
            or type(phase) is not str
            or type(completed) is not int
            or type(total) is not int
            or not 1 <= total
            or not 0 <= completed <= total
        ):
            return False
        label = tr(_KNOWN_PROGRESS_PHASES.get(phase, mark("working")))
        self._progress.setRange(0, total)
        self._progress.setValue(completed)
        self._progress.setFormat(tr("Preview: {value1} — %v of %m", value1=label))
        self._progress.setAccessibleDescription(
            tr(
                "Scan review preview {value1}, {value2} of {value3}.",
                value1=label,
                value2=completed,
                value3=total,
            )
        )
        self._set_status(
            tr(
                "Preview {value1}: {value2} of {value3}. Adjustments are not applied.",
                value1=label,
                value2=completed,
                value3=total,
            )
        )
        self._announcer.progress(
            tr(
                "Preview {value1}: {value2} of {value3}.",
                value1=label,
                value2=completed,
                value3=total,
            ),
            key=label,
            completed=completed,
            total=total,
        )
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
        failed_apply = self._apply_active
        if self._apply_active:
            self._apply_active = False
            self._progress.setVisible(False)
            self._set_busy_controls()
            self._set_status(
                tr("Applying adjustments did not finish. Review the error and try again.")
            )
        elif self._preview_active:
            if type(variant) is not ScanReviewPreviewVariant:
                return False
            self._set_preview_terminal_state(variant, "failed")
            self._finish_preview_state()
            self._set_status(tr("Preview did not finish. Adjustments were not applied."))
        else:
            return False
        self._error.setText(f"{safe_code}: {safe_message}")
        self._error.setAccessibleName(tr("Scan review error: {value1}", value1=self._error.text()))
        recovery = (
            tr(
                "Choose Apply adjustments to retry saving the reviewed adjustments, or Cancel scan review to leave."
            )
            if failed_apply
            else tr(
                "Choose Update preview to rebuild the current comparison, or Cancel scan review to leave."
            )
        )
        self._error.setAccessibleDescription(f"{self._error.text()} {recovery}")
        self._error.setVisible(True)
        self._control_error_visible = False
        self._update_apply_availability()
        self._announce(
            tr(
                "Scan review action failed. {value1} {value2}", value1=safe_message, value2=recovery
            ),
            assertive=True,
        )
        target = self._apply_button if failed_apply else self._update_preview_button
        target.setFocus(Qt.FocusReason.OtherFocusReason)
        self._scroll.ensureWidgetVisible(target, 8, 8)
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
        self._set_status(tr("Preview cancelled. Adjustments were not applied."))
        self._announce(tr("Preview cancelled. Adjustments were not applied."), assertive=True)
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
                tr("Scan adjustments applied to this case. The original remains unchanged.")
            )
            self._announce(tr("Scan adjustments applied."))
        else:
            self._set_status(
                tr("Scan review complete. No changes were applied. The original remains unchanged.")
            )
            self._announce(tr("Scan review complete. No changes were applied."))
        super().accept()
        return True

    def reject(self) -> None:
        """Cancel active preview first; otherwise close without applying."""

        if self._apply_active:
            self._announce(tr("Wait for the apply action to finish."), assertive=True)
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

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override name
        super().resizeEvent(event)
        self._preview_layout.setDirection(
            QBoxLayout.Direction.TopToBottom
            if self.width() < 1000
            else QBoxLayout.Direction.LeftToRight
        )

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt override name
        """Restore the safe default focus target whenever the dialog is shown."""

        super().showEvent(event)
        self._cancel_button.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def _compare_choice(self, text: str, object_name: str, description: str) -> QRadioButton:
        choice = QRadioButton(tr(text))
        choice.setObjectName(object_name)
        choice.setAccessibleName(tr(text))
        choice.setAccessibleDescription(tr(description))
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
        group.setAccessibleName(tr("{value1} scan preview panel", value1=title))
        group.setAccessibleDescription(
            tr("{value1} preview pixels and a truthful textual availability state.", value1=title)
        )
        layout = QVBoxLayout(group)
        preview = _FitPreviewLabel(initial_status)
        preview.setObjectName(preview_name)
        preview.setAccessibleName(
            tr("{value1} scan preview: {value2}", value1=title, value2=initial_status)
        )
        preview.setAccessibleDescription(tr(initial_status))
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview.setFrameShape(QFrame.Shape.StyledPanel)
        preview.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        preview.setStyleSheet(_FOCUS_VISIBLE_TEXT_STYLE)
        preview.setWordWrap(True)
        preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        status = QLabel(tr(initial_status))
        status.setObjectName(status_name)
        status.setAccessibleName(
            tr("{value1} preview status: {value2}", value1=title, value2=initial_status)
        )
        status.setAccessibleDescription(tr(initial_status))
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
        spin.setAccessibleName(tr(accessible_name))
        spin.setAccessibleDescription(tr(description))
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
        label = QLabel(tr(text))
        label.setObjectName(object_name)
        label.setAccessibleName(tr(text))
        label.setBuddy(control)
        label.setWordWrap(True)
        # Keep one labelled control per row so the long cleanup/rotation
        # choices cannot force a four-column form beyond the viewport.
        position = (row * 2 + column // 2) * 2
        layout.addWidget(label, position, 0)
        layout.addWidget(control, position + 1, 0)

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
            self._clear_adjusted_preview(
                tr("Adjusted preview unavailable until crop edges are valid.")
            )
            self._show_control_error(
                tr(
                    "Crop edges must retain a positive page area: left before right and top before bottom."
                )
            )
            self._set_busy_controls()
            self._update_apply_availability()
            return
        self._controls_valid = True
        self._hide_control_error()
        self._draft = self._draft.with_candidate(candidate, suggestion_present=False)
        self._suggestion_notice.setVisible(False)
        self._adjusted_preview_draft = None
        self._clear_adjusted_preview(tr("Adjusted preview out of date; choose Update preview."))
        self._set_status(tr("Adjustments changed. Update preview before applying them."))
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
        self._set_status(tr("Cancellation requested. Adjustments have not been applied."))
        self._announce(
            tr("Cancellation requested. Waiting for preview work to stop."), assertive=True
        )
        self.cancel_preview_requested.emit()

    def _request_apply(self) -> None:
        if not self._can_apply():
            return
        self._apply_active = True
        self._set_busy_controls()
        self._apply_button.setEnabled(False)
        self._cancel_button.setEnabled(False)
        self._hide_error()
        set_static_busy(self._progress)
        self._progress.setFormat(tr("Applying adjustments"))
        self._progress.setAccessibleName(tr("Scan review apply progress"))
        self._progress.setAccessibleDescription(
            tr("Progress while PaperWorkShell applies reviewed adjustments as case intent.")
        )
        self._progress.setVisible(True)
        self._set_status(tr("Applying the reviewed adjustments as case intent."))
        self._announce(tr("Applying reviewed scan adjustments."))
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
        self._clear_adjusted_preview(tr("Adjusted preview reset; choose Update preview."))
        self._set_status(
            tr("Adjustments reset to the original page. Update preview before applying.")
        )
        self._announce(tr("Provisional adjustments reset to the original page."))
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
        self._set_busy_controls()
        if self._cancel_preview_button.hasFocus():
            self._update_preview_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._cancel_preview_button.setVisible(False)
        self._cancel_preview_button.setEnabled(False)

    def _clear_adjusted_preview(self, message: str) -> None:
        self._adjusted_pixmap = None
        self._adjusted_preview.clear()
        self._adjusted_preview.setText(message)
        self._set_preview_status(self._adjusted_status, message)

    def _set_preview_status(self, label: QLabel, message: str) -> None:
        label.setText(message)
        title = tr("Original") if label is self._original_status else tr("Adjusted")
        label.setAccessibleName(
            tr("{value1} preview status: {value2}", value1=title, value2=message)
        )
        label.setAccessibleDescription(message)
        preview = (
            self._original_preview if label is self._original_status else self._adjusted_preview
        )
        preview.setAccessibleName(
            tr("{value1} scan preview: {value2}", value1=title, value2=message)
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
            original_message = tr("Original preview {value1}.", value1=outcome)
            self._original_preview.setText(original_message)
            self._set_preview_status(self._original_status, original_message)
            self._adjusted_preview_draft = None
            self._adjusted_pixmap = None
            self._adjusted_preview.clear()
            adjusted_message = tr("Adjusted preview not produced.")
            self._adjusted_preview.setText(adjusted_message)
            self._set_preview_status(self._adjusted_status, adjusted_message)
            return
        self._adjusted_preview_draft = None
        self._adjusted_pixmap = None
        self._adjusted_preview.clear()
        adjusted_message = tr("Adjusted preview {value1}.", value1=outcome)
        self._adjusted_preview.setText(adjusted_message)
        self._set_preview_status(self._adjusted_status, adjusted_message)

    def _set_status(self, message: str) -> None:
        message = tr(message)
        self._status.setText(message)
        self._status.setAccessibleName(tr("Scan review status: {value1}", value1=message))
        self._status.setAccessibleDescription(message)

    def _show_control_error(self, message: str) -> None:
        announce = not self._control_error_visible
        self._control_error_visible = True
        self._error.setText(message)
        self._error.setAccessibleName(tr("Scan review error: {value1}", value1=message))
        self._error.setAccessibleDescription(message)
        self._error.setVisible(True)
        self._set_status(tr("Adjust the crop edges before updating the preview."))
        if announce:
            self._announce(tr("Crop edges are invalid. Adjust the crop controls."), assertive=True)

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
                tr("PaperWorkShell could not complete scan review. Try again."),
            )
        if (
            type(message) is not str
            or not message
            or len(message) > 500
            or any(ord(character) < 32 and character not in "\t\n" for character in message)
        ):
            return (
                "PWS_UI_UNEXPECTED",
                tr("PaperWorkShell could not complete scan review. Try again."),
            )
        return code, tr(message)

    def _announce(self, message: str, *, assertive: bool = False) -> None:
        message = tr(message)
        self._announcer.announce(message, assertive=assertive)

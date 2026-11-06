from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QFont, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QWidget,
)

from paperworkshell.app.scan_review import ScanReviewDraft, ScanReviewPreviewVariant
from paperworkshell.domain.image_transforms import ImageTransformIntent
from paperworkshell.ui.scan_review import ScanReviewDialog

CASE_ID = UUID("23000000-0000-4000-8000-000000000011")
PAGE_ID = UUID("23000000-0000-4000-8000-000000000012")


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def _draft(*, suggestion_present: bool = False) -> ScanReviewDraft:
    intent = ImageTransformIntent()
    return ScanReviewDraft(
        case_id=CASE_ID,
        page_id=PAGE_ID,
        manifest_revision=9,
        base_intent=intent,
        candidate_intent=intent,
        suggestion_present=suggestion_present,
    )


@contextmanager
def _dialog(*, suggestion_present: bool = False) -> Iterator[ScanReviewDialog]:
    app = _application()
    dialog = ScanReviewDialog(_draft(suggestion_present=suggestion_present))
    dialog.show()
    app.processEvents()
    try:
        yield dialog
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def _button(dialog: ScanReviewDialog, name: str) -> QPushButton:
    button = dialog.findChild(QPushButton, name)
    assert button is not None
    return button


def _preview_image() -> QImage:
    image = QImage(32, 24, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.white)
    return image


def test_dialog_uses_neutral_wording_and_every_interactive_control_has_context() -> None:
    with _dialog(suggestion_present=True) as dialog:
        assert dialog.isModal()
        assert dialog.windowTitle() == "Review scan adjustments"
        assert dialog.accessibleName() == "Review scan adjustments"
        assert "improvement" not in dialog.accessibleDescription().casefold()
        notice = dialog.findChild(QLabel, "scanReviewSuggestionNotice")
        assert notice is not None
        assert notice.isVisible()
        assert "needs review" in notice.text().casefold()
        assert "nothing has been applied" in notice.text().casefold()

        interactive_names = (
            "scanReviewShowOriginalRadio",
            "scanReviewShowAdjustedRadio",
            "scanReviewShowSideBySideRadio",
            "scanReviewCropLeftSpin",
            "scanReviewCropTopSpin",
            "scanReviewCropRightSpin",
            "scanReviewCropBottomSpin",
            "scanReviewRotationCombo",
            "scanReviewDeskewSpin",
            "scanReviewContrastSpin",
            "scanReviewCleanupSpin",
            "scanReviewUpdatePreviewButton",
            "scanReviewResetOriginalButton",
            "scanReviewCancelPreviewButton",
            "scanReviewApplyAdjustmentsButton",
            "scanReviewCancelButton",
        )
        for object_name in interactive_names:
            widget = dialog.findChild(QWidget, object_name)
            assert widget is not None
            assert widget.accessibleName()
            assert widget.accessibleDescription()
        cancel = _button(dialog, "scanReviewCancelButton")
        assert "Stop an active preview first" in cancel.accessibleDescription()
        assert "close scan review without applying" in cancel.accessibleDescription()

        for label_name, control_name in (
            ("scanReviewCropLeftLabel", "scanReviewCropLeftSpin"),
            ("scanReviewCropTopLabel", "scanReviewCropTopSpin"),
            ("scanReviewCropRightLabel", "scanReviewCropRightSpin"),
            ("scanReviewCropBottomLabel", "scanReviewCropBottomSpin"),
            ("scanReviewRotationLabel", "scanReviewRotationCombo"),
            ("scanReviewDeskewLabel", "scanReviewDeskewSpin"),
            ("scanReviewContrastLabel", "scanReviewContrastSpin"),
            ("scanReviewCleanupLabel", "scanReviewCleanupSpin"),
        ):
            label = dialog.findChild(QLabel, label_name)
            control = dialog.findChild(QWidget, control_name)
            assert label is not None
            assert control is not None
            assert label.buddy() is control


def test_safe_default_focus_compare_choices_and_keyboard_adjustments() -> None:
    app = _application()
    with _dialog() as dialog:
        cancel = _button(dialog, "scanReviewCancelButton")
        scroll = dialog.findChild(QScrollArea, "scanReviewScrollArea")
        original_choice = dialog.findChild(QRadioButton, "scanReviewShowOriginalRadio")
        left = dialog.findChild(QDoubleSpinBox, "scanReviewCropLeftSpin")
        rotation = dialog.findChild(QComboBox, "scanReviewRotationCombo")
        deskew = dialog.findChild(QDoubleSpinBox, "scanReviewDeskewSpin")
        contrast = dialog.findChild(QDoubleSpinBox, "scanReviewContrastSpin")
        cleanup = dialog.findChild(QDoubleSpinBox, "scanReviewCleanupSpin")
        assert scroll is not None
        assert original_choice is not None
        assert left is not None
        assert rotation is not None
        assert deskew is not None
        assert contrast is not None
        assert cleanup is not None
        assert cancel.isDefault()
        assert dialog.focusWidget() is cancel

        QTest.keyClick(cancel, Qt.Key.Key_Tab)
        app.processEvents()
        assert dialog.focusWidget() is scroll

        original_choice.setFocus()
        QTest.keyClick(original_choice, Qt.Key.Key_Space)
        app.processEvents()
        adjusted_preview = dialog.findChild(QLabel, "scanReviewAdjustedPreview")
        original_preview = dialog.findChild(QLabel, "scanReviewOriginalPreview")
        assert adjusted_preview is not None
        assert original_preview is not None
        assert original_preview.isVisible()
        assert not adjusted_preview.isVisible()

        left.setFocus()
        QTest.keyClick(left, Qt.Key.Key_Up)
        rotation.setFocus()
        QTest.keyClick(rotation, Qt.Key.Key_Down)
        deskew.setFocus()
        QTest.keyClick(deskew, Qt.Key.Key_Up)
        contrast.setFocus()
        QTest.keyClick(contrast, Qt.Key.Key_Up)
        cleanup.setFocus()
        QTest.keyClick(cleanup, Qt.Key.Key_Up)
        app.processEvents()

        intent = dialog.draft.candidate_intent
        assert intent.crop.x == 1_000
        assert int(intent.additional_rotation) == 90
        assert intent.deskew_angle_millidegrees_clockwise == 100
        assert intent.contrast_factor_millionths == 1_010_000
        assert intent.background_cleanup_strength_millionths == 10_000
        assert dialog.focusWidget() is cleanup


def test_progress_cancel_failure_and_recovery_are_visible_focusable_and_textual() -> None:
    app = _application()
    with _dialog() as dialog:
        requests: list[ScanReviewDraft] = []
        cancellations: list[bool] = []
        dialog.preview_requested.connect(requests.append)
        dialog.cancel_preview_requested.connect(lambda: cancellations.append(True))
        update = _button(dialog, "scanReviewUpdatePreviewButton")
        cancel_preview = _button(dialog, "scanReviewCancelPreviewButton")
        progress = dialog.findChild(QWidget, "scanReviewProgress")
        status = dialog.findChild(QLabel, "scanReviewStatus")
        assert progress is not None
        assert status is not None

        update.setFocus()
        QTest.keyClick(update, Qt.Key.Key_Space)
        app.processEvents()
        request = requests[0]
        assert progress.isVisible()
        assert cancel_preview.isVisible()
        assert cancel_preview.isEnabled()
        assert cancel_preview.focusPolicy() is not Qt.FocusPolicy.NoFocus
        assert dialog.set_progress(request, "generating", 1, 2)
        assert "1 of 2" in status.text()
        assert "Adjustments are not applied" in status.text()

        cancel_preview.setFocus()
        QTest.keyClick(cancel_preview, Qt.Key.Key_Space)
        app.processEvents()
        assert cancellations == [True]
        assert "Cancellation requested" in status.text()
        assert dialog.set_preview_cancelled(request, ScanReviewPreviewVariant.ORIGINAL)
        assert dialog.focusWidget() is update
        assert "Adjustments were not applied" in status.text()

        update.click()
        app.processEvents()
        failure_request = requests[-1]
        assert dialog.set_failure(
            failure_request,
            "SCAN_REVIEW_PREVIEW_RESULT_INVALID",
            "PaperWorkShell could not display this preview safely. Try again.",
            variant=ScanReviewPreviewVariant.ORIGINAL,
        )
        error = dialog.findChild(QLabel, "scanReviewError")
        assert error is not None
        assert error.isVisible()
        assert error.focusPolicy() is Qt.FocusPolicy.StrongFocus
        assert dialog.focusWidget() is update
        assert "SCAN_REVIEW_PREVIEW_RESULT_INVALID" in error.text()
        assert "Try again" in error.text()


def test_enlarged_text_keeps_actions_visible_and_every_review_control_scroll_reachable() -> None:
    app = _application()
    original_font = QFont(app.font())
    enlarged_font = QFont(original_font)
    enlarged_font.setPointSizeF(max(24.0, original_font.pointSizeF() * 2.0))
    app.setFont(enlarged_font)
    try:
        with _dialog() as dialog:
            dialog.resize(520, 360)
            app.processEvents()
            scroll = dialog.findChild(QScrollArea, "scanReviewScrollArea")
            apply = _button(dialog, "scanReviewApplyAdjustmentsButton")
            cancel = _button(dialog, "scanReviewCancelButton")
            assert scroll is not None
            assert scroll.widgetResizable()
            assert scroll.verticalScrollBar().maximum() > 0
            assert apply.isVisible()
            assert cancel.isVisible()
            assert dialog.rect().contains(apply.mapTo(dialog, apply.rect().center()))
            assert dialog.rect().contains(cancel.mapTo(dialog, cancel.rect().center()))

            reachable_names = (
                "scanReviewShowOriginalRadio",
                "scanReviewShowAdjustedRadio",
                "scanReviewShowSideBySideRadio",
                "scanReviewOriginalPreview",
                "scanReviewAdjustedPreview",
                "scanReviewCropLeftSpin",
                "scanReviewCropTopSpin",
                "scanReviewCropRightSpin",
                "scanReviewCropBottomSpin",
                "scanReviewRotationCombo",
                "scanReviewDeskewSpin",
                "scanReviewContrastSpin",
                "scanReviewCleanupSpin",
                "scanReviewUpdatePreviewButton",
                "scanReviewResetOriginalButton",
                "scanReviewStatus",
            )
            for object_name in reachable_names:
                widget = dialog.findChild(QWidget, object_name)
                assert widget is not None
                assert widget.isVisible()
                scroll.ensureWidgetVisible(widget, 8, 8)
                app.processEvents()
                origin = widget.mapTo(scroll.viewport(), QPoint(0, 0))
                assert QRect(origin, widget.size()).intersects(scroll.viewport().rect())

            cancel.setFocus()
            app.processEvents()
            assert dialog.focusWidget() is cancel
    finally:
        app.setFont(original_font)


def test_successful_exact_preview_enables_keyboard_apply_only_after_both_images() -> None:
    app = _application()
    with _dialog() as dialog:
        requests: list[ScanReviewDraft] = []
        applied: list[ScanReviewDraft] = []
        dialog.preview_requested.connect(requests.append)
        dialog.apply_requested.connect(applied.append)
        update = _button(dialog, "scanReviewUpdatePreviewButton")
        apply = _button(dialog, "scanReviewApplyAdjustmentsButton")
        progress = dialog.findChild(QWidget, "scanReviewProgress")
        assert progress is not None
        assert not apply.isEnabled()

        QTest.keyClick(update, Qt.Key.Key_Space)
        app.processEvents()
        assert progress.accessibleName() == "Scan review preview progress"
        assert "provisional preview" in progress.accessibleDescription()
        request = requests[0]
        assert dialog.set_adjusted_image(request, _preview_image())
        assert not apply.isEnabled()
        assert dialog.set_original_image(request, _preview_image())
        assert apply.isEnabled()

        apply.setFocus()
        QTest.keyClick(apply, Qt.Key.Key_Space)
        app.processEvents()
        assert applied == [request]
        assert progress.accessibleName() == "Scan review apply progress"
        assert "applies reviewed adjustments" in progress.accessibleDescription()
        assert dialog.isVisible()
        assert not apply.isEnabled()

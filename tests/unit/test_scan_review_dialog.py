from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QLabel,
    QPushButton,
    QRadioButton,
)

from paperworkshell.app.scan_review import ScanReviewDraft, ScanReviewPreviewVariant
from paperworkshell.domain.image_pages import ImageQuarterTurn
from paperworkshell.domain.image_transforms import (
    ImageNormalizedCrop,
    ImageTransformIntent,
)
from paperworkshell.ui.scan_review import ScanReviewDialog

CASE_ID = UUID("23000000-0000-4000-8000-000000000001")
PAGE_ID = UUID("23000000-0000-4000-8000-000000000002")


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def _draft(
    *,
    candidate: ImageTransformIntent | None = None,
    suggestion_present: bool = False,
) -> ScanReviewDraft:
    base = ImageTransformIntent()
    return ScanReviewDraft(
        case_id=CASE_ID,
        page_id=PAGE_ID,
        manifest_revision=7,
        base_intent=base,
        candidate_intent=base if candidate is None else candidate,
        suggestion_present=suggestion_present,
    )


@contextmanager
def _dialog(draft: ScanReviewDraft | None = None) -> Iterator[ScanReviewDialog]:
    app = _application()
    dialog = ScanReviewDialog(_draft() if draft is None else draft)
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


def _label(dialog: ScanReviewDialog, name: str) -> QLabel:
    label = dialog.findChild(QLabel, name)
    assert label is not None
    return label


def _image(color: Qt.GlobalColor = Qt.GlobalColor.white) -> QImage:
    image = QImage(24, 16, QImage.Format.Format_RGBA8888)
    image.fill(color)
    return image


def _complete_preview(dialog: ScanReviewDialog) -> ScanReviewDraft:
    requested: list[ScanReviewDraft] = []
    dialog.preview_requested.connect(requested.append)
    _button(dialog, "scanReviewUpdatePreviewButton").click()
    _application().processEvents()
    assert len(requested) == 1
    request = requested[0]
    assert dialog.set_original_image(request, _image())
    assert dialog.set_adjusted_image(request, _image(Qt.GlobalColor.lightGray))
    _application().processEvents()
    return request


def test_update_with_retained_original_announces_only_adjusted_work() -> None:
    with _dialog() as dialog:
        request = _complete_preview(dialog)

        assert dialog.set_preview_loading(request)

        assert dialog._status.text() == (
            "Building adjusted preview. The original remains available. "
            "Adjustments are not applied."
        )
        description = _button(
            dialog,
            "scanReviewUpdatePreviewButton",
        ).accessibleDescription()
        assert "current provisional adjusted preview" in description
        assert "only when it is not already available" in description


def test_current_candidate_preview_is_required_and_original_remains_retained() -> None:
    with _dialog() as dialog:
        first_request = _complete_preview(dialog)
        apply = _button(dialog, "scanReviewApplyAdjustmentsButton")
        original = dialog.findChild(QLabel, "scanReviewOriginalPreview")
        contrast = dialog.findChild(QDoubleSpinBox, "scanReviewContrastSpin")
        assert original is not None
        assert contrast is not None
        assert apply.isEnabled()
        assert dialog.preview_ready_for_apply
        assert not original.pixmap().isNull()

        contrast.setValue(125.0)
        _application().processEvents()

        assert dialog.draft.candidate_intent.contrast_factor_millionths == 1_250_000
        assert not apply.isEnabled()
        assert original.accessibleDescription() == "Original preview ready."
        assert not dialog.set_adjusted_image(first_request, _image())

        requested: list[ScanReviewDraft] = []
        dialog.preview_requested.connect(requested.append)
        _button(dialog, "scanReviewUpdatePreviewButton").click()
        _application().processEvents()
        assert requested == [dialog.draft]
        assert not dialog.set_adjusted_image(first_request, _image())
        assert dialog.set_original_image(requested[0], _image())
        assert dialog.set_adjusted_image(requested[0], _image())
        assert apply.isEnabled()


def test_compare_choices_and_reset_keep_original_available_without_applying() -> None:
    candidate = ImageTransformIntent(
        crop=ImageNormalizedCrop(100_000, 100_000, 800_000, 800_000),
        additional_rotation=ImageQuarterTurn.DEGREES_90,
        deskew_angle_millidegrees_clockwise=1_250,
        contrast_factor_millionths=1_200_000,
        background_cleanup_strength_millionths=300_000,
    )
    with _dialog(_draft(candidate=candidate, suggestion_present=True)) as dialog:
        _complete_preview(dialog)
        notice = dialog.findChild(QLabel, "scanReviewSuggestionNotice")
        original_group = dialog.findChild(QLabel, "scanReviewOriginalPreview")
        adjusted_group = dialog.findChild(QLabel, "scanReviewAdjustedPreview")
        show_original = dialog.findChild(QRadioButton, "scanReviewShowOriginalRadio")
        show_adjusted = dialog.findChild(QRadioButton, "scanReviewShowAdjustedRadio")
        show_both = dialog.findChild(QRadioButton, "scanReviewShowSideBySideRadio")
        assert notice is not None
        assert original_group is not None
        assert adjusted_group is not None
        assert show_original is not None
        assert show_adjusted is not None
        assert show_both is not None
        assert notice.isVisible()

        show_original.click()
        assert original_group.isVisible()
        assert not adjusted_group.isVisible()
        show_adjusted.click()
        assert not original_group.isVisible()
        assert adjusted_group.isVisible()
        show_both.click()
        assert original_group.isVisible()
        assert adjusted_group.isVisible()

        _button(dialog, "scanReviewResetOriginalButton").click()
        _application().processEvents()
        assert dialog.draft.candidate_intent == ImageTransformIntent()
        assert not dialog.draft.suggestion_present
        assert not notice.isVisible()
        assert not original_group.pixmap().isNull()
        assert not dialog.preview_ready_for_apply


def test_apply_waits_for_correlated_completion_and_idle_cancel_is_safe_default() -> None:
    with _dialog() as dialog:
        app = _application()
        request = _complete_preview(dialog)
        applied: list[ScanReviewDraft] = []
        proof_during_apply: list[bool] = []
        dialog.apply_requested.connect(applied.append)
        dialog.apply_requested.connect(
            lambda _draft: proof_during_apply.append(dialog.preview_ready_for_apply)
        )
        apply = _button(dialog, "scanReviewApplyAdjustmentsButton")
        cancel = _button(dialog, "scanReviewCancelButton")
        original = dialog.findChild(QLabel, "scanReviewOriginalPreview")
        assert original is not None
        assert cancel.isDefault()
        assert dialog.focusWidget() is cancel

        apply.click()
        app.processEvents()

        assert applied == [request]
        assert proof_during_apply == [True]
        assert dialog.result() == ScanReviewDialog.DialogCode.Rejected
        assert dialog.isVisible()
        stale = request.with_candidate(
            ImageTransformIntent(additional_rotation=ImageQuarterTurn.DEGREES_90)
        )
        assert not dialog.set_apply_complete(stale, changes_applied=True)
        assert dialog.set_apply_complete(request, changes_applied=True)
        app.processEvents()
        assert dialog.result() == ScanReviewDialog.DialogCode.Accepted
        assert original.pixmap().isNull()

    with _dialog() as dialog:
        request = _complete_preview(dialog)
        dialog._request_apply()
        assert dialog.set_apply_complete(request, changes_applied=False)
        assert "No changes were applied" in dialog._status.text()
        assert "adjustments applied" not in dialog._status.text().casefold()

    with _dialog() as dialog:
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        _application().processEvents()
        assert dialog.result() == ScanReviewDialog.DialogCode.Rejected
        assert not dialog.isVisible()


def test_escape_cancels_active_preview_then_rejects_only_when_idle() -> None:
    with _dialog() as dialog:
        app = _application()
        requested: list[ScanReviewDraft] = []
        cancellations: list[bool] = []
        dialog.preview_requested.connect(requested.append)
        dialog.cancel_preview_requested.connect(lambda: cancellations.append(True))
        _button(dialog, "scanReviewUpdatePreviewButton").click()
        app.processEvents()
        assert len(requested) == 1

        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        app.processEvents()

        assert cancellations == [True]
        assert dialog.isVisible()
        assert dialog.set_preview_cancelled(
            requested[0],
            ScanReviewPreviewVariant.ORIGINAL,
        )
        assert dialog.focusWidget() is _button(dialog, "scanReviewUpdatePreviewButton")

        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        app.processEvents()
        assert not dialog.isVisible()
        assert dialog.result() == ScanReviewDialog.DialogCode.Rejected


def test_controls_map_exact_values_and_invalid_crop_blocks_preview() -> None:
    with _dialog() as dialog:
        left = dialog.findChild(QDoubleSpinBox, "scanReviewCropLeftSpin")
        right = dialog.findChild(QDoubleSpinBox, "scanReviewCropRightSpin")
        deskew = dialog.findChild(QDoubleSpinBox, "scanReviewDeskewSpin")
        contrast = dialog.findChild(QDoubleSpinBox, "scanReviewContrastSpin")
        cleanup = dialog.findChild(QDoubleSpinBox, "scanReviewCleanupSpin")
        rotation = dialog.findChild(QComboBox, "scanReviewRotationCombo")
        error = dialog.findChild(QLabel, "scanReviewError")
        update = _button(dialog, "scanReviewUpdatePreviewButton")
        assert left is not None
        assert right is not None
        assert deskew is not None
        assert contrast is not None
        assert cleanup is not None
        assert rotation is not None
        assert error is not None

        left.setValue(25.0)
        right.setValue(20.0)
        assert error.isVisible()
        assert not update.isEnabled()

        right.setValue(80.0)
        rotation.setCurrentIndex(rotation.findData(270))
        deskew.setValue(-1.250)
        contrast.setValue(120.0)
        cleanup.setValue(30.0)
        _application().processEvents()

        intent = dialog.draft.candidate_intent
        assert intent.crop == ImageNormalizedCrop(250_000, 0, 550_000, 1_000_000)
        assert intent.additional_rotation is ImageQuarterTurn.DEGREES_270
        assert intent.deskew_angle_millidegrees_clockwise == -1_250
        assert intent.contrast_factor_millionths == 1_200_000
        assert intent.background_cleanup_strength_millionths == 300_000
        assert not error.isVisible()
        assert update.isEnabled()


def test_progress_unknown_phase_is_not_reflected_and_failure_focuses_safe_error() -> None:
    with _dialog() as dialog:
        requested: list[ScanReviewDraft] = []
        dialog.preview_requested.connect(requested.append)
        _button(dialog, "scanReviewUpdatePreviewButton").click()
        _application().processEvents()
        request = requested[0]
        secret_phase = "PRIVATE_SOURCE_PATH_CANARY"

        assert dialog.set_progress(request, secret_phase, 1, 2)
        progress = dialog.findChild(QLabel, "scanReviewStatus")
        assert progress is not None
        assert "working" in progress.text()
        assert secret_phase not in progress.text()

        assert dialog.set_failure(
            request,
            "invalid code",
            "PRIVATE_NATIVE_EXCEPTION_CANARY",
            variant=ScanReviewPreviewVariant.ORIGINAL,
        )
        error = dialog.findChild(QLabel, "scanReviewError")
        assert error is not None
        assert error.isVisible()
        assert error.text() == (
            "PWS_UI_UNEXPECTED: PaperWorkShell could not complete scan review. Try again."
        )
        assert "PRIVATE" not in error.text()
        assert dialog.focusWidget() is _button(dialog, "scanReviewUpdatePreviewButton")


def test_original_and_adjusted_failures_have_truthful_visible_and_accessible_states() -> None:
    with _dialog() as dialog:
        request = dialog.draft
        assert dialog.set_preview_loading(request)
        assert dialog.set_failure(
            request,
            "SCAN_REVIEW_PREVIEW_FAILED",
            "PaperWorkShell could not build this preview. Try again.",
            variant=ScanReviewPreviewVariant.ORIGINAL,
        )
        original = _label(dialog, "scanReviewOriginalPreview")
        adjusted = _label(dialog, "scanReviewAdjustedPreview")
        guidance = _label(dialog, "scanReviewGuidance")
        assert original.text() == "Original preview failed."
        assert original.accessibleDescription() == "Original preview failed."
        assert adjusted.text() == "Adjusted preview not produced."
        assert adjusted.accessibleDescription() == "Adjusted preview not produced."
        assert "once its preview is ready" in guidance.text()
        assert "remains unchanged and available" not in guidance.text()

    with _dialog() as dialog:
        request = _complete_preview(dialog)
        original = _label(dialog, "scanReviewOriginalPreview")
        assert dialog.set_preview_loading(request)
        assert dialog.set_failure(
            request,
            "SCAN_REVIEW_PREVIEW_FAILED",
            "PaperWorkShell could not build this preview. Try again.",
            variant=ScanReviewPreviewVariant.ADJUSTED,
        )
        adjusted = _label(dialog, "scanReviewAdjustedPreview")
        assert not original.pixmap().isNull()
        assert original.accessibleDescription() == "Original preview ready."
        assert adjusted.text() == "Adjusted preview failed."
        assert adjusted.accessibleDescription() == "Adjusted preview failed."


def test_original_and_adjusted_cancellation_states_are_variant_specific() -> None:
    with _dialog() as dialog:
        request = dialog.draft
        assert dialog.set_preview_loading(request)
        assert dialog.set_preview_cancelled(request, ScanReviewPreviewVariant.ORIGINAL)
        original = _label(dialog, "scanReviewOriginalPreview")
        adjusted = _label(dialog, "scanReviewAdjustedPreview")
        assert original.text() == "Original preview cancelled."
        assert original.accessibleDescription() == "Original preview cancelled."
        assert adjusted.text() == "Adjusted preview not produced."
        assert adjusted.accessibleDescription() == "Adjusted preview not produced."

    with _dialog() as dialog:
        request = _complete_preview(dialog)
        original = _label(dialog, "scanReviewOriginalPreview")
        assert dialog.set_preview_loading(request)
        assert dialog.set_preview_cancelled(request, ScanReviewPreviewVariant.ADJUSTED)
        adjusted = _label(dialog, "scanReviewAdjustedPreview")
        assert not original.pixmap().isNull()
        assert original.accessibleDescription() == "Original preview ready."
        assert adjusted.text() == "Adjusted preview cancelled."
        assert adjusted.accessibleDescription() == "Adjusted preview cancelled."

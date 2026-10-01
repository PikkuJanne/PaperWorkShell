from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import cast
from uuid import UUID

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QWidget,
)

from paperworkshell.domain.resource_policy import CONSTRAINED_RESOURCE_POLICY_V1
from paperworkshell.domain.target_size_search import TargetSizeColorChoice
from paperworkshell.ui.compression_review import CompressionReviewDialog
from paperworkshell.ui.preview_comparison import PreviewComparisonWidget, _ImagePanel
from tests.helpers.compression_review import compression_review


def application() -> QApplication:
    current = QApplication.instance()
    return QApplication([]) if current is None else cast(QApplication, current)


@contextmanager
def dialog_context() -> Iterator[CompressionReviewDialog]:
    app = application()
    dialog = CompressionReviewDialog(CONSTRAINED_RESOURCE_POLICY_V1)
    dialog.show()
    app.processEvents()
    try:
        yield dialog
    finally:
        dialog.finish_operation()
        dialog.reject()
        dialog.deleteLater()
        app.processEvents()


def widget[T: QWidget](dialog: QWidget, kind: type[T], name: str) -> T:
    result = dialog.findChild(kind, name)
    assert result is not None
    return result


def press(button: QPushButton) -> None:
    button.setFocus(Qt.FocusReason.OtherFocusReason)
    QTest.keyClick(button, Qt.Key.Key_Space)
    application().processEvents()


def image() -> QImage:
    result = QImage(80, 100, QImage.Format.Format_RGBA8888)
    result.fill(Qt.GlobalColor.white)
    return result


def load_pair(dialog: CompressionReviewDialog) -> None:
    review = dialog.review
    assert review is not None
    index = widget(dialog, QComboBox, "compressionReviewPageCombo").currentIndex()
    page = review.affected_pages[index]
    press(widget(dialog, QPushButton, "compressionReviewShowPageButton"))
    assert dialog.set_preview(review.review_id, page.output_key, page.page_index, image(), image())


def test_default_preserves_color_and_preparation_is_explicit() -> None:
    with dialog_context() as dialog:
        requests: list[object] = []
        dialog.reprepare_requested.connect(requests.append)
        assert dialog.color_choice is TargetSizeColorChoice.PRESERVE
        assert requests == []
        assert not widget(dialog, QPushButton, "compressionReviewExportButton").isEnabled()
        press(widget(dialog, QPushButton, "compressionReviewPrepareButton"))
        press(widget(dialog, QPushButton, "compressionReviewPrepareButton"))
        assert requests == [TargetSizeColorChoice.PRESERVE]
        assert dialog.busy


@pytest.mark.parametrize("floor", (False, True))
def test_achievable_and_floor_candidates_require_exact_pair_and_explicit_review(
    floor: bool,
) -> None:
    with dialog_context() as dialog:
        review = compression_review(floor=floor)
        dialog.show_review(review)
        exports: list[object] = []
        dialog.export_requested.connect(exports.append)
        ack = widget(dialog, QCheckBox, "compressionReviewAcknowledgementCheck")
        start = widget(dialog, QPushButton, "compressionReviewExportButton")
        assert not ack.isEnabled()
        assert not start.isEnabled()
        load_pair(dialog)
        assert ack.isEnabled()
        assert not start.isEnabled()
        ack.setChecked(True)
        assert start.isEnabled()
        press(start)
        press(start)
        assert exports == [review.review_id]
        assert dialog.busy
        text = widget(dialog, QPlainTextEdit, "compressionReviewSummaryText").toPlainText()
        assert "Current verified total: 20,000 bytes" in text
        assert "Required total limit: 18,000 bytes" in text
        assert "Output 1, page 1" in text and "Output 1, page 2" in text
        assert "DPI 400 × 400 → 200 × 200" in text if floor else "DPI 400 × 400 → 300 × 300" in text
        assert "JPEG quality 80" in text if floor else "JPEG quality 90" in text


def test_grayscale_change_discards_pixels_and_approval_until_explicit_reprepare() -> None:
    with dialog_context() as dialog:
        review = compression_review()
        dialog.show_review(review)
        load_pair(dialog)
        widget(dialog, QCheckBox, "compressionReviewAcknowledgementCheck").setChecked(True)
        requests: list[object] = []
        dialog.reprepare_requested.connect(requests.append)
        widget(dialog, QCheckBox, "compressionReviewGrayscaleCheck").setChecked(True)
        assert dialog.review is None
        assert dialog.preview_bytes == 0
        assert requests == []
        assert not widget(dialog, QCheckBox, "compressionReviewAcknowledgementCheck").isChecked()
        assert not widget(dialog, QPushButton, "compressionReviewExportButton").isEnabled()
        assert not dialog.set_preview(review.review_id, "output-one", 0, image(), image())
        press(widget(dialog, QPushButton, "compressionReviewPrepareButton"))
        assert requests == [TargetSizeColorChoice.ALLOW_GRAYSCALE]
        dialog.show_review(compression_review(grayscale=True))
        text = widget(dialog, QPlainTextEdit, "compressionReviewSummaryText").toPlainText()
        assert "color rgb → grayscale" in text


def test_unsafe_target_has_no_override_and_actionable_recovery() -> None:
    with dialog_context() as dialog:
        dialog.show_review(compression_review(blocked=True, floor=True))
        load_pair(dialog)
        widget(dialog, QCheckBox, "compressionReviewAcknowledgementCheck").setChecked(True)
        assert not widget(dialog, QPushButton, "compressionReviewExportButton").isEnabled()
        assert "200 dpi" in widget(dialog, QLabel, "compressionReviewFloor").text()
        assert "no override" in widget(dialog, QLabel, "compressionReviewFloor").text()
        status = widget(dialog, QLabel, "compressionReviewStatus").text()
        # Loading a page remains possible even for a blocked candidate.
        assert "comparison ready" in status
        assert (
            "could not be met"
            in widget(dialog, QPlainTextEdit, "compressionReviewSummaryText").toPlainText()
        )


def test_unchanged_candidate_requires_acknowledgement_without_unnecessary_image_visit() -> None:
    with dialog_context() as dialog:
        dialog.show_review(compression_review(unchanged=True))
        assert not widget(dialog, QPushButton, "compressionReviewShowPageButton").isEnabled()
        ack = widget(dialog, QCheckBox, "compressionReviewAcknowledgementCheck")
        assert ack.isEnabled()
        assert not widget(dialog, QPushButton, "compressionReviewExportButton").isEnabled()
        ack.setChecked(True)
        assert widget(dialog, QPushButton, "compressionReviewExportButton").isEnabled()


def test_zip_totals_and_archive_limit_are_distinguished_from_member_pdf_sizes() -> None:
    with dialog_context() as dialog:
        dialog.show_review(compression_review(archive=True))
        text = widget(dialog, QPlainTextEdit, "compressionReviewSummaryText").toPlainText()
        assert "Current verified total: 21,000 bytes" in text
        assert "Proposed verified total: 18,000 bytes" in text
        assert "PDF 1: 20,000 → 17,000 bytes; file limit: 18,000 bytes" in text
        assert "ZIP: 21,000 → 18,000 bytes; archive limit: 18,000 bytes" in text


def test_pair_results_require_current_identity_page_and_active_request() -> None:
    with dialog_context() as dialog:
        review = compression_review()
        dialog.show_review(review)
        assert not dialog.set_preview(review.review_id, "output-one", 0, image(), image())
        press(widget(dialog, QPushButton, "compressionReviewShowPageButton"))
        assert not dialog.set_preview(
            UUID("55555555-5555-4555-8555-555555555555"), "output-one", 0, image(), image()
        )
        assert not dialog.set_preview(review.review_id, "output-other", 0, image(), image())
        assert not dialog.set_preview(review.review_id, "output-one", 1, image(), image())
        assert not dialog.set_preview(review.review_id, "output-one", True, image(), image())
        assert dialog.preview_bytes == 0
        assert dialog.set_preview(review.review_id, "output-one", 0, image(), image())
        widget(dialog, QComboBox, "compressionReviewPageCombo").setCurrentIndex(1)
        assert dialog.preview_bytes == 0
        assert not dialog.set_preview(review.review_id, "output-one", 0, image(), image())


def test_cancellation_happens_once_and_suppresses_late_pixels_without_review_timeout() -> None:
    with dialog_context() as dialog:
        review = compression_review()
        dialog.show_review(review)
        cancelled: list[bool] = []
        dialog.cancel_requested.connect(lambda: cancelled.append(True))
        press(widget(dialog, QPushButton, "compressionReviewShowPageButton"))
        dialog.reject()
        dialog.reject()
        assert cancelled == [True]
        pending = dialog.busy
        assert dialog.isVisible() and pending
        assert not dialog.set_preview(review.review_id, "output-one", 0, image(), image())
        assert not dialog.set_progress("preview", 1, 2)
        dialog.show_failure("private source path", phase="preview")
        finished = dialog.busy
        assert not finished
        assert dialog.review is review
        assert widget(dialog, QPushButton, "compressionReviewShowPageButton").isEnabled()
        assert "private source path" not in widget(dialog, QLabel, "compressionReviewStatus").text()


def test_pair_budget_dimensions_and_copies_are_bounded_and_released() -> None:
    app = application()
    comparison = PreviewComparisonWidget(128_000)
    try:
        before, proposed = image(), image()
        assert comparison.set_images(before, proposed)
        assert comparison.retained_bytes == 64_000
        before.fill(Qt.GlobalColor.red)
        # Detached storage is independent of caller mutation.
        before_panel = comparison.findChild(QWidget, "previewComparisonBeforeImage")
        assert before_panel is not None
        stored = cast(_ImagePanel, before_panel).image
        assert isinstance(stored, QImage)
        assert stored.pixelColor(0, 0) != before.pixelColor(0, 0)
        assert not comparison.set_images(before, QImage(81, 100, QImage.Format.Format_RGBA8888))
        assert comparison.retained_bytes == 0
        assert not comparison.set_images(
            QImage(81, 100, QImage.Format.Format_RGBA8888),
            QImage(81, 100, QImage.Format.Format_RGBA8888),
        )
        assert not comparison.has_pair
    finally:
        comparison.deleteLater()
        app.processEvents()


def test_pixel_limit_is_checked_before_copy_and_summary_does_not_expand_each_image() -> None:
    app = application()
    policy = replace(CONSTRAINED_RESOURCE_POLICY_V1, maximum_preview_pixels_per_page=100)
    dialog = CompressionReviewDialog(policy)
    try:
        review = compression_review()
        page = review.affected_pages[0]
        review = replace(review, affected_pages=(replace(page, images=page.images * 10_000),))
        dialog.show_review(review)
        text = widget(dialog, QPlainTextEdit, "compressionReviewSummaryText").toPlainText()
        assert "10000 changed image(s)" in text
        assert len(text) < 1500
        press(widget(dialog, QPushButton, "compressionReviewShowPageButton"))
        assert not dialog.set_preview(
            review.review_id, page.output_key, page.page_index, image(), image()
        )
        assert dialog.preview_bytes == 0
        assert not dialog.busy
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_closing_releases_pair_and_raw_progress_or_failure_text_is_never_displayed() -> None:
    with dialog_context() as dialog:
        dialog.show_review(compression_review())
        load_pair(dialog)
        assert dialog.preview_bytes > 0
        dialog.set_busy("private source path")
        assert dialog.set_progress("private source path", 1, 2)
        dialog.show_failure("private source path")
        assert "private source path" not in " ".join(
            label.text() for label in dialog.findChildren(QLabel)
        )
        assert dialog.preview_bytes == 0
        dialog.show_review(compression_review())
        load_pair(dialog)
        dialog.reject()
        assert dialog.preview_bytes == 0
        assert dialog.review is None

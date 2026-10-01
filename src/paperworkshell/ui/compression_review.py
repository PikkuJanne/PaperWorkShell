"""Explicit review of privately prepared, verified size-reduction candidates."""

from __future__ import annotations

from itertools import pairwise
from typing import Final
from uuid import UUID

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent, QCloseEvent, QImage, QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from paperworkshell.domain.compression_review import CompressionPageChange, CompressionReviewState
from paperworkshell.domain.resource_policy import ResourcePolicy
from paperworkshell.domain.target_size_search import TargetSizeColorChoice
from paperworkshell.ui.preview_comparison import PreviewComparisonWidget

_PHASE_TEXT: Final = {
    "preparing": "Preparing and verifying a private candidate",
    "preview": "Loading the selected page comparison",
    "export": "Publishing the reviewed candidate",
}


class CompressionReviewDialog(QDialog):
    """Keep review consent separate from the coordinator's candidate authority."""

    reprepare_requested = Signal(object)
    preview_requested = Signal(object, str, int)
    export_requested = Signal(object)
    cancel_requested = Signal()

    def __init__(self, resource_policy: ResourcePolicy, parent: QWidget | None = None) -> None:
        if type(resource_policy) is not ResourcePolicy:
            raise ValueError("Compression review requires the central resource policy.")
        super().__init__(parent)
        self._review: CompressionReviewState | None = None
        self._busy = False
        self._cancelable = False
        self._cancellation_requested = False
        self._phase = "preparing"
        self._updating = False
        self._preview_request: tuple[UUID, str, int] | None = None
        self._has_preview_proof = False
        self._maximum_preview_pixels = resource_policy.maximum_preview_pixels_per_page
        self.setObjectName("compressionReviewDialog")
        self.setWindowTitle("Review size reduction")
        self.setAccessibleName("Review size reduction")
        self.setAccessibleDescription(
            "Review actual sizes and affected pages before exporting a privately prepared "
            "candidate. Color removal requires an explicit choice. Close is the safe default."
        )
        self.setModal(True)
        content = QWidget()
        content.setObjectName("compressionReviewContent")
        body = QVBoxLayout(content)
        body.addWidget(
            self._label(
                "Inspect the proposed changes before export. Review small text, stamps, handwriting, "
                "and meaning carried by color. A size limit and a preview do not prove readability "
                "or portal acceptance. Original source files remain unchanged.",
                "compressionReviewGuidance",
                "Compression review guidance",
            )
        )
        self._grayscale = QCheckBox()
        self._grayscale.setObjectName("compressionReviewGrayscaleCheck")
        self._grayscale.setAccessibleName("Allow grayscale for eligible images")
        self._grayscale.setAccessibleDescription(
            "Unchecked preserves color. Checking permits color removal from eligible images. "
            "Changing this choice discards the current preview and requires preparation again."
        )
        color_label = self._label(
            "Allow grayscale for eligible images; color information may be lost",
            "compressionReviewGrayscaleLabel",
            "Explicit grayscale choice",
        )
        color_label.setBuddy(self._grayscale)
        color_row = QHBoxLayout()
        color_row.addWidget(self._grayscale, 0, Qt.AlignmentFlag.AlignTop)
        color_row.addWidget(color_label, 1)
        body.addLayout(color_row)
        self._prepare = self._button(
            "Prepare",
            "compressionReviewPrepareButton",
            "Prepare a new private candidate with the selected color policy. This does not publish files.",
        )
        self._prepare.setAccessibleName("Prepare compression preview")
        self._prepare.clicked.connect(self._request_prepare)
        body.addWidget(self._prepare)
        self._floor = self._label(
            "Automatic reduction stops at 200 dpi and JPEG quality 80. Images already below the "
            "resolution floor remain unchanged. There is no override below this floor. Human "
            "review is still required.",
            "compressionReviewFloor",
            "Automatic legibility floor",
        )
        body.addWidget(self._floor)
        summary_label = QLabel("Sizes and &changes")
        summary_label.setWordWrap(True)
        self._summary = QPlainTextEdit()
        self._summary.setObjectName("compressionReviewSummaryText")
        self._summary.setAccessibleName("Actual sizes and proposed image changes")
        self._summary.setAccessibleDescription(
            "Selectable plain text lists measured current and proposed sizes, required limits, "
            "and every affected page. Tab moves to the next control."
        )
        self._summary.setReadOnly(True)
        self._summary.setTabChangesFocus(True)
        self._summary.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._summary.setMinimumHeight(220)
        summary_label.setBuddy(self._summary)
        body.addWidget(summary_label)
        body.addWidget(self._summary)
        page_label = QLabel("Affected &page")
        page_label.setWordWrap(True)
        self._pages = QComboBox()
        self._pages.setObjectName("compressionReviewPageCombo")
        self._pages.setAccessibleName("Affected page to inspect")
        self._pages.setAccessibleDescription(
            "Choose an affected page using the arrow keys, then choose Show page. Only one "
            "before and proposed pair is retained at a time."
        )
        self._pages.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._pages.setMinimumContentsLength(8)
        self._pages.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        page_label.setBuddy(self._pages)
        body.addWidget(page_label)
        body.addWidget(self._pages)
        self._load = self._button(
            "Show page",
            "compressionReviewShowPageButton",
            "Load before and proposed pixels for the selected affected page.",
        )
        self._load.clicked.connect(self._request_preview)
        body.addWidget(self._load)
        self._comparison = PreviewComparisonWidget(resource_policy.thumbnail_preview_cache_bytes)
        body.addWidget(self._comparison)
        self._ack = QCheckBox()
        self._ack.setObjectName("compressionReviewAcknowledgementCheck")
        self._ack.setAccessibleName("Confirm review of displayed changes and legibility risks")
        self._ack.setAccessibleDescription(
            "Confirm only after reviewing the displayed changes and any page previews you need. "
            "This does not claim every affected page was inspected or establish readability."
        )
        ack_label = self._label(
            "I reviewed the displayed changes and legibility risks",
            "compressionReviewAcknowledgementLabel",
            "Compression review confirmation",
        )
        ack_label.setBuddy(self._ack)
        ack_row = QHBoxLayout()
        ack_row.addWidget(self._ack, 0, Qt.AlignmentFlag.AlignTop)
        ack_row.addWidget(ack_label, 1)
        body.addLayout(ack_row)
        self._export = self._button(
            "Export",
            "compressionReviewExportButton",
            "Publish the exact verified candidate associated with this review and selected destination.",
        )
        self._export.setAccessibleName("Export reviewed candidate")
        self._export.clicked.connect(self._request_export)
        body.addWidget(self._export)
        self._status = self._label(
            "Choose the color policy, then prepare a preview.",
            "compressionReviewStatus",
            "Compression review status and recovery",
        )
        self._status.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._status.setFrameShape(QFrame.Shape.StyledPanel)
        self._status.setStyleSheet(
            "QLabel:focus { border: 2px solid palette(highlight); padding: 3px; }"
        )
        body.addWidget(self._status)
        self._progress = QProgressBar()
        self._progress.setObjectName("compressionReviewProgress")
        self._progress.setAccessibleName("Compression operation progress")
        self._progress.setAccessibleDescription("No compression operation is active.")
        self._progress.hide()
        body.addWidget(self._progress)
        self._scroll = QScrollArea()
        self._scroll.setObjectName("compressionReviewScrollArea")
        self._scroll.setAccessibleName("Compression review controls")
        self._scroll.setAccessibleDescription(
            "Scrollable sizes, page comparison, color choice, and review confirmation."
        )
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._scroll.setWidget(content)
        self._cancel = self._button(
            "Cancel",
            "compressionReviewCancelButton",
            "Request cancellation and keep the dialog open until the operation finishes safely.",
        )
        self._cancel.setAccessibleName("Cancel compression operation")
        self._cancel.clicked.connect(self._request_cancel)
        self._close = self._button(
            "Close",
            "compressionReviewCloseButton",
            "Discard this private review. During work, Escape requests cancellation and waits for the result.",
        )
        self._close.setDefault(True)
        self._close.setAutoDefault(True)
        self._close.clicked.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self._scroll, 1)
        layout.addWidget(self._cancel)
        layout.addWidget(self._close)
        chain = (
            self._close,
            self._scroll,
            self._grayscale,
            self._prepare,
            self._summary,
            self._pages,
            self._load,
            *self._comparison.focus_controls,
            self._ack,
            self._export,
            self._status,
            self._cancel,
            self._close,
        )
        for current, following in pairwise(chain):
            QWidget.setTabOrder(current, following)
        self._grayscale.toggled.connect(self._color_changed)
        self._pages.currentIndexChanged.connect(self._page_changed)
        self._ack.toggled.connect(self._update_actions)
        self._update_actions()
        self.resize(840, 760)

    @property
    def review(self) -> CompressionReviewState | None:
        return self._review

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def color_choice(self) -> TargetSizeColorChoice:
        return (
            TargetSizeColorChoice.ALLOW_GRAYSCALE
            if self._grayscale.isChecked()
            else TargetSizeColorChoice.PRESERVE
        )

    @property
    def preview_bytes(self) -> int:
        return self._comparison.retained_bytes

    @property
    def review_confirmed(self) -> bool:
        review = self._review
        return bool(
            review
            and review.export_allowed
            and self._ack.isChecked()
            and (not review.affected_pages or self._has_preview_proof)
        )

    def show_review(self, review: CompressionReviewState) -> None:
        if type(review) is not CompressionReviewState:
            raise ValueError("Compression review requires verified candidate facts.")
        self._invalidate()
        self._review = review
        self._updating = True
        try:
            self._grayscale.setChecked(review.color_choice is TargetSizeColorChoice.ALLOW_GRAYSCALE)
            for page in review.affected_pages:
                self._pages.addItem(f"Output {page.output_position}, page {page.page_index + 1}")
        finally:
            self._updating = False
        self._summary.setPlainText(self._review_text(review))
        self.finish_operation()
        message = (
            "Candidate prepared privately. Review the changes and load a page comparison before "
            "confirming and exporting."
            if review.export_allowed and review.affected_pages
            else "Candidate prepared privately. No image changes were selected. Review the sizes before confirming export."
            if review.export_allowed
            else f"{review.decision.message} Close to adjust the case or permitted size limits, "
            "or prepare again with an explicit color choice. Automatic reduction will not go below the floor."
        )
        self._set_status(message, assertive=not review.export_allowed)
        self._focus(self._summary)

    def set_preview(
        self, review_id: UUID, output_key: str, page_index: int, before: QImage, proposed: QImage
    ) -> bool:
        request = (review_id, output_key, page_index)
        if (
            type(review_id) is not UUID
            or type(output_key) is not str
            or type(page_index) is not int
            or self._review is None
            or self._review.review_id != review_id
            or self._preview_request != request
            or self._cancellation_requested
            or not self._busy
            or self._phase != "preview"
        ):
            return False
        if (
            not isinstance(before, QImage)
            or not isinstance(proposed, QImage)
            or before.width() * before.height() > self._maximum_preview_pixels
            or proposed.width() * proposed.height() > self._maximum_preview_pixels
            or not self._comparison.set_images(before, proposed)
        ):
            self.show_failure(phase="preview")
            return False
        self._has_preview_proof = True
        self.finish_operation()
        self._set_status(
            "Page comparison ready. Inspect the proposed result and confirm the displayed changes when ready."
            if self._review.export_allowed
            else "Page comparison ready. The target remains blocked. Close to adjust the case or "
            "permitted size limits, or prepare again with an explicit color choice."
        )
        self._focus(self._comparison.focus_controls[2])
        return True

    def set_busy(self, phase: str = "preparing", *, cancelable: bool = True) -> None:
        if type(phase) is not str or type(cancelable) is not bool:
            raise ValueError("Compression progress requires a phase and cancellation state.")
        self._busy = True
        self._phase = phase if phase in _PHASE_TEXT else "preparing"
        self._cancelable = cancelable
        self._cancellation_requested = False
        self._progress.setRange(0, 0)
        self._progress.show()
        self._progress.setAccessibleDescription(_PHASE_TEXT[self._phase])
        self._update_actions()
        self._set_status(
            f"{_PHASE_TEXT[self._phase]}. Keep this dialog open until the result is known."
        )
        self._focus(self._cancel if cancelable else self._status)

    def set_progress(self, phase: str, completed: int, total: int) -> bool:
        if (
            not self._busy
            or self._cancellation_requested
            or type(phase) is not str
            or type(completed) is not int
            or type(total) is not int
            or not 1 <= total <= 1_000_000
            or not 0 <= completed <= total
        ):
            return False
        text = _PHASE_TEXT.get(phase, "Working on the private candidate")
        self._progress.setRange(0, total)
        self._progress.setValue(completed)
        self._progress.setFormat("%v of %m")
        self._progress.setAccessibleDescription(f"{text}: {completed} of {total}.")
        self._set_status(f"{text}: {completed} of {total}.")
        return True

    def finish_operation(self) -> None:
        self._busy = False
        self._cancelable = False
        self._cancellation_requested = False
        self._preview_request = None
        self._progress.hide()
        self._update_actions()

    def show_failure(self, _code: str = "", *, phase: str = "preparing") -> None:
        if phase == "preview":
            self._comparison.clear()
            message = "The page comparison did not finish. Choose Show page to retry, or close this private review."
        else:
            self._invalidate()
            message = (
                "Publication did not finish. Review the package result and destination inventory before retrying."
                if phase == "export"
                else "The private candidate could not be prepared. Prepare again, or close to review the case and permitted size limits."
            )
        self.finish_operation()
        self._set_status(message, assertive=True)
        self._focus(self._load if phase == "preview" else self._status)

    def reject(self) -> None:
        if self._busy:
            self._request_cancel()
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override name
        if self._busy:
            event.ignore()
            self._request_cancel()
            return
        super().closeEvent(event)

    def done(self, result: int) -> None:
        self._invalidate()
        super().done(result)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt override name
        super().showEvent(event)
        self._close.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def _color_changed(self, _checked: bool) -> None:
        if self._updating or self._busy:
            return
        self._invalidate()
        self._update_actions()
        self._set_status(
            "Color choice changed. Prepare a new preview before reviewing and exporting."
        )

    def _page_changed(self, _index: int) -> None:
        if self._updating:
            return
        self._comparison.clear()
        self._preview_request = None
        self._ack.setChecked(False)
        self._update_actions()

    def _invalidate(self) -> None:
        self._review = None
        self._has_preview_proof = False
        self._preview_request = None
        self._comparison.clear()
        self._ack.setChecked(False)
        self._pages.clear()
        self._summary.setPlainText("Prepare a candidate to see actual sizes and proposed changes.")

    def _request_prepare(self) -> None:
        if not self._prepare.isEnabled():
            return
        self._invalidate()
        self.set_busy("preparing")
        self.reprepare_requested.emit(self.color_choice)

    def _request_preview(self) -> None:
        review = self._review
        if review is None or not self._load.isEnabled():
            return
        page = review.affected_pages[self._pages.currentIndex()]
        self._comparison.clear()
        self._ack.setChecked(False)
        self._preview_request = (review.review_id, page.output_key, page.page_index)
        self.set_busy("preview")
        self.preview_requested.emit(*self._preview_request)

    def _request_export(self) -> None:
        if self._review is None or not self._export.isEnabled():
            return
        review_id = self._review.review_id
        self.set_busy("export")
        self.export_requested.emit(review_id)

    def _request_cancel(self) -> None:
        if not self._busy or not self._cancelable or self._cancellation_requested:
            return
        self._cancellation_requested = True
        self._update_actions()
        self._set_status(
            "Cancellation requested. Waiting for the operation to finish safely.", assertive=True
        )
        self.cancel_requested.emit()

    def _update_actions(self, *_ignored: object) -> None:
        editable = not self._busy
        review = self._review
        self._grayscale.setEnabled(editable)
        self._prepare.setEnabled(editable)
        self._pages.setEnabled(editable and review is not None and bool(review.affected_pages))
        self._load.setEnabled(self._pages.isEnabled())
        reviewable = bool(
            review
            and review.export_allowed
            and (not review.affected_pages or self._has_preview_proof)
        )
        self._ack.setEnabled(editable and reviewable)
        self._export.setEnabled(editable and reviewable and self._ack.isChecked())
        self._close.setEnabled(editable)
        self._cancel.setVisible(self._busy)
        self._cancel.setEnabled(
            self._busy and self._cancelable and not self._cancellation_requested
        )

    @staticmethod
    def _review_text(review: CompressionReviewState) -> str:
        lines = [
            f"Current verified total: {review.current_total_bytes:,} bytes",
            f"Proposed verified total: {review.proposed_total_bytes:,} bytes",
            f"Required total limit: {_limit(review.required_total_bytes)}",
        ]
        for position, (before, proposed, limit) in enumerate(
            zip(
                review.current_pdf_byte_sizes,
                review.proposed_pdf_byte_sizes,
                review.required_file_bytes,
                strict=True,
            ),
            start=1,
        ):
            lines.append(
                f"PDF {position}: {before:,} → {proposed:,} bytes; file limit: {_limit(limit)}"
            )
        if review.current_archive_byte_size is not None:
            lines.append(
                f"ZIP: {review.current_archive_byte_size:,} → {review.proposed_archive_byte_size:,} bytes; archive limit: {_limit(review.required_archive_bytes)}"
            )
        lines.extend(("", review.decision.message, "", "Affected pages and actual image changes:"))
        for page in review.affected_pages:
            lines.append(f"Output {page.output_position}, page {page.page_index + 1}")
            lines.append(_page_changes(page))
        if not review.affected_pages:
            lines.append("No image changes selected.")
        return "\n".join(lines)

    def _set_status(self, message: str, *, assertive: bool = False) -> None:
        self._status.setText(message)
        self._status.setAccessibleDescription(message)
        announcement = QAccessibleAnnouncementEvent(self._status, message)
        announcement.setPoliteness(
            QAccessible.AnnouncementPoliteness.Assertive
            if assertive
            else QAccessible.AnnouncementPoliteness.Polite
        )
        QAccessible.updateAccessibility(announcement)

    def _focus(self, widget: QWidget) -> None:
        widget.setFocus(Qt.FocusReason.OtherFocusReason)
        if widget not in (self._close, self._cancel):
            self._scroll.ensureWidgetVisible(widget, 8, 8)

    @staticmethod
    def _label(text: str, name: str, accessible_name: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName(name)
        label.setAccessibleName(accessible_name)
        label.setAccessibleDescription(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        return label

    @staticmethod
    def _button(text: str, name: str, description: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(name)
        button.setAccessibleName(text)
        button.setAccessibleDescription(description)
        button.setAutoDefault(False)
        button.setDefault(False)
        return button


def _limit(value: int | None) -> str:
    return "not specified" if value is None else f"{value:,} bytes"


def _dpi(value: int) -> str:
    return f"{value / 1000:g}"


def _page_changes(page: CompressionPageChange) -> str:
    """Keep one bounded line per page even when it contains many image objects."""

    def limits(attribute: str, *, dpi: bool = False) -> str:
        lower = min(getattr(image, attribute) for image in page.images)
        upper = max(getattr(image, attribute) for image in page.images)
        text = _dpi(lower) if dpi else str(lower)
        if lower != upper:
            text += f"–{_dpi(upper) if dpi else upper}"
        return text

    colors = sorted(
        {
            f"{image.original_color_mode.value} → {image.proposed_color_mode.value}"
            for image in page.images
        }
    )
    return (
        f"  {len(page.images)} changed image(s); width × height ranges "
        f"{limits('original_width_pixels')} × {limits('original_height_pixels')} → "
        f"{limits('proposed_width_pixels')} × {limits('proposed_height_pixels')} pixels; "
        f"DPI {limits('original_effective_dpi_x_milli', dpi=True)} × "
        f"{limits('original_effective_dpi_y_milli', dpi=True)} → "
        f"{limits('proposed_effective_dpi_x_milli', dpi=True)} × "
        f"{limits('proposed_effective_dpi_y_milli', dpi=True)}; "
        f"color {', '.join(colors)}; JPEG quality {limits('jpeg_quality')}."
    )


__all__ = ("CompressionReviewDialog",)

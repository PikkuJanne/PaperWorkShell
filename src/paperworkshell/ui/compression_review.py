"""Explicit review of privately prepared, verified size-reduction candidates."""

from __future__ import annotations

from itertools import pairwise
from typing import Final
from uuid import UUID

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QImage, QShowEvent
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
from paperworkshell.localization.catalog import format_file_size, format_number, mark, tr
from paperworkshell.ui.announcements import ScreenReaderAnnouncer
from paperworkshell.ui.localization_layout import adapt_localized_content
from paperworkshell.ui.presentation import set_static_busy
from paperworkshell.ui.preview_comparison import PreviewComparisonWidget

_PHASE_TEXT: Final = {
    "preparing": mark("Preparing and verifying a private candidate"),
    "preview": mark("Loading the selected page comparison"),
    "export": mark("Publishing the reviewed candidate"),
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
        self._announcer = ScreenReaderAnnouncer(self)
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
        self.setWindowTitle(tr("Review size reduction"))
        self.setAccessibleName(tr("Review size reduction"))
        self.setAccessibleDescription(
            tr(
                "Review actual sizes and affected pages before exporting a privately prepared candidate. Color removal requires an explicit choice. Close is the safe default."
            )
        )
        self.setModal(True)
        content = QWidget()
        content.setObjectName("compressionReviewContent")
        body = QVBoxLayout(content)
        body.addWidget(
            self._label(
                tr(
                    "Inspect the proposed changes before export. Review small text, stamps, handwriting, and meaning carried by color. A size limit and a preview do not prove readability or portal acceptance. Original source files remain unchanged."
                ),
                "compressionReviewGuidance",
                tr("Compression review guidance"),
            )
        )
        self._grayscale = QCheckBox()
        self._grayscale.setObjectName("compressionReviewGrayscaleCheck")
        self._grayscale.setAccessibleName(tr("Allow grayscale for eligible images"))
        self._grayscale.setAccessibleDescription(
            tr(
                "Unchecked preserves color. Checking permits color removal from eligible images. Changing this choice discards the current preview and requires preparation again."
            )
        )
        color_label = self._label(
            tr("Allow grayscale for eligible images; color information may be lost"),
            "compressionReviewGrayscaleLabel",
            tr("Explicit grayscale choice"),
        )
        color_label.setBuddy(self._grayscale)
        color_row = QHBoxLayout()
        color_row.addWidget(self._grayscale, 0, Qt.AlignmentFlag.AlignTop)
        color_row.addWidget(color_label, 1)
        body.addLayout(color_row)
        self._prepare = self._button(
            tr("Prepare"),
            "compressionReviewPrepareButton",
            tr(
                "Prepare a new private candidate with the selected color policy. This does not publish files."
            ),
        )
        self._prepare.setAccessibleName(tr("Prepare compression preview"))
        self._prepare.clicked.connect(self._request_prepare)
        body.addWidget(self._prepare)
        self._floor = self._label(
            tr(
                "Automatic reduction stops at 200 dpi and JPEG quality 80. Images already below the resolution floor remain unchanged. There is no override below this floor. Human review is still required."
            ),
            "compressionReviewFloor",
            tr("Automatic legibility floor"),
        )
        body.addWidget(self._floor)
        summary_label = QLabel(tr("Sizes and &changes"))
        summary_label.setWordWrap(True)
        self._summary = QPlainTextEdit()
        self._summary.setObjectName("compressionReviewSummaryText")
        self._summary.setAccessibleName(tr("Actual sizes and proposed image changes"))
        self._summary.setAccessibleDescription(
            tr(
                "Selectable plain text lists measured current and proposed sizes, required limits, and every affected page. Control+Home and Control+End move to the start and end. Tab moves to the next control."
            )
        )
        self._summary.setReadOnly(True)
        self._summary.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self._summary.setTabChangesFocus(True)
        self._summary.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._summary.setMinimumHeight(220)
        summary_label.setBuddy(self._summary)
        body.addWidget(summary_label)
        body.addWidget(self._summary)
        page_label = QLabel(tr("Affected &page"))
        page_label.setWordWrap(True)
        self._pages = QComboBox()
        self._pages.setObjectName("compressionReviewPageCombo")
        self._pages.setAccessibleName(tr("Affected page to inspect"))
        self._pages.setAccessibleDescription(
            tr(
                "Choose an affected page using the arrow keys, then choose Show page. Only one before and proposed pair is retained at a time."
            )
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
            tr("Show page"),
            "compressionReviewShowPageButton",
            tr("Load before and proposed pixels for the selected affected page."),
        )
        self._load.clicked.connect(self._request_preview)
        body.addWidget(self._load)
        self._comparison = PreviewComparisonWidget(resource_policy.thumbnail_preview_cache_bytes)
        body.addWidget(self._comparison)
        self._ack = QCheckBox()
        self._ack.setObjectName("compressionReviewAcknowledgementCheck")
        self._ack.setAccessibleName(tr("Confirm review of displayed changes and legibility risks"))
        self._ack.setAccessibleDescription(
            tr(
                "Confirm only after reviewing the displayed changes and any page previews you need. This does not claim every affected page was inspected or establish readability."
            )
        )
        ack_label = self._label(
            tr("I reviewed the displayed changes and legibility risks"),
            "compressionReviewAcknowledgementLabel",
            tr("Compression review confirmation"),
        )
        ack_label.setBuddy(self._ack)
        ack_row = QHBoxLayout()
        ack_row.addWidget(self._ack, 0, Qt.AlignmentFlag.AlignTop)
        ack_row.addWidget(ack_label, 1)
        body.addLayout(ack_row)
        self._export = self._button(
            tr("Export"),
            "compressionReviewExportButton",
            tr(
                "Publish the exact verified candidate associated with this review and selected destination."
            ),
        )
        self._export.setAccessibleName(tr("Export reviewed candidate"))
        self._export.clicked.connect(self._request_export)
        body.addWidget(self._export)
        self._status = self._label(
            tr("Choose the color policy, then prepare a preview."),
            "compressionReviewStatus",
            tr("Compression review status and recovery"),
        )
        self._status.setAccessibleName(
            tr("Compression review status: {value1}", value1=self._status.text())
        )
        self._status.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._status.setFrameShape(QFrame.Shape.StyledPanel)
        self._status.setStyleSheet(
            "QLabel:focus { border: 2px solid palette(highlight); padding: 3px; }"
        )
        body.addWidget(self._status)
        self._progress = QProgressBar()
        self._progress.setObjectName("compressionReviewProgress")
        self._progress.setAccessibleName(tr("Compression operation progress"))
        self._progress.setAccessibleDescription(tr("No compression operation is active."))
        self._progress.hide()
        body.addWidget(self._progress)
        self._scroll = QScrollArea()
        self._scroll.setObjectName("compressionReviewScrollArea")
        self._scroll.setAccessibleName(tr("Compression review controls"))
        self._scroll.setAccessibleDescription(
            tr("Scrollable sizes, page comparison, color choice, and review confirmation.")
        )
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._scroll.setWidget(content)
        adapt_localized_content(self._scroll)
        self._cancel = self._button(
            tr("Cancel"),
            "compressionReviewCancelButton",
            tr(
                "Request cancellation and keep the dialog open until the operation finishes safely."
            ),
        )
        self._cancel.setAccessibleName(tr("Cancel compression operation"))
        self._cancel.clicked.connect(self._request_cancel)
        self._close = self._button(
            tr("Close"),
            "compressionReviewCloseButton",
            tr(
                "Discard this private review. During work, Escape requests cancellation and waits for the result."
            ),
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
                self._pages.addItem(
                    tr(
                        "Output {value1}, page {value2}",
                        value1=page.output_position,
                        value2=page.page_index + 1,
                    )
                )
        finally:
            self._updating = False
        self._summary.setPlainText(self._review_text(review))
        self.finish_operation()
        message = (
            tr(
                "Candidate prepared privately. Review the changes and load a page comparison before confirming and exporting."
            )
            if review.export_allowed and review.affected_pages
            else tr(
                "Candidate prepared privately. No image changes were selected. Review the sizes before confirming export."
            )
            if review.export_allowed
            else tr(
                "{value1} Close to adjust the case or permitted size limits, or prepare again with an explicit color choice. Automatic reduction will not go below the floor.",
                value1=tr(review.decision.message),
            )
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
            tr(
                "Page comparison ready. Inspect the proposed result and confirm the displayed changes when ready."
            )
            if self._review.export_allowed
            else tr(
                "Page comparison ready. The target remains blocked. Close to adjust the case or permitted size limits, or prepare again with an explicit color choice."
            )
        )
        self._focus(self._comparison.focus_controls[2])
        return True

    def set_busy(self, phase: str = "preparing", *, cancelable: bool = True) -> None:
        if type(phase) is not str or type(cancelable) is not bool:
            raise ValueError("Compression progress requires a phase and cancellation state.")
        self._busy = True
        self._announcer.reset_progress()
        self._phase = phase if phase in _PHASE_TEXT else "preparing"
        self._cancelable = cancelable
        self._cancellation_requested = False
        set_static_busy(self._progress)
        self._progress.show()
        self._progress.setAccessibleDescription(tr(_PHASE_TEXT[self._phase]))
        self._update_actions()
        self._set_status(
            tr(
                "{value1}. Keep this dialog open until the result is known.",
                value1=tr(_PHASE_TEXT[self._phase]),
            )
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
        text = _PHASE_TEXT.get(phase, mark("Working on the private candidate"))
        self._progress.setRange(0, total)
        self._progress.setValue(completed)
        self._progress.setFormat(tr("%v of %m"))
        self._progress.setAccessibleDescription(
            tr("{value1}: {value2} of {value3}.", value1=tr(text), value2=completed, value3=total)
        )
        message = tr(
            "{value1}: {value2} of {value3}.", value1=tr(text), value2=completed, value3=total
        )
        self._set_status(message, announce=False)
        self._announcer.progress(message, key=text, completed=completed, total=total)
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
            message = tr(
                "The page comparison did not finish. Choose Show page to retry, or close this private review."
            )
        else:
            self._invalidate()
            message = (
                tr(
                    "Publication did not finish. Review the package result and destination inventory before retrying."
                )
                if phase == "export"
                else tr(
                    "The private candidate could not be prepared. Prepare again, or close to review the case and permitted size limits."
                )
            )
        self.finish_operation()
        self._set_status(message, assertive=True)
        self._focus(
            self._load
            if phase == "preview"
            else self._close
            if phase == "export"
            else self._prepare
        )

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
            tr("Color choice changed. Prepare a new preview before reviewing and exporting.")
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
        self._summary.setPlainText(
            tr("Prepare a candidate to see actual sizes and proposed changes.")
        )

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
            tr("Cancellation requested. Waiting for the operation to finish safely."),
            assertive=True,
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
            tr(
                "Current verified total: {value1} bytes ({display_size})",
                value1=format_number(review.current_total_bytes),
                display_size=format_file_size(review.current_total_bytes),
            ),
            tr(
                "Proposed verified total: {value1} bytes ({display_size})",
                value1=format_number(review.proposed_total_bytes),
                display_size=format_file_size(review.proposed_total_bytes),
            ),
            tr("Required total limit: {value1}", value1=_limit(review.required_total_bytes)),
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
                tr(
                    "PDF {value1}: {value2} → {value3} bytes; file limit: {value4}",
                    value1=position,
                    value2=format_number(before),
                    value3=format_number(proposed),
                    value4=_limit(limit),
                )
            )
        if review.current_archive_byte_size is not None:
            lines.append(
                tr(
                    "ZIP: {value1} → {value2} bytes; archive limit: {value3}",
                    value1=format_number(review.current_archive_byte_size),
                    value2=format_number(review.proposed_archive_byte_size)
                    if review.proposed_archive_byte_size is not None
                    else tr("not specified"),
                    value3=_limit(review.required_archive_bytes),
                )
            )
        lines.extend(
            ("", tr(review.decision.message), "", tr("Affected pages and actual image changes:"))
        )
        for page in review.affected_pages:
            lines.append(
                tr(
                    "Output {value1}, page {value2}",
                    value1=page.output_position,
                    value2=page.page_index + 1,
                )
            )
            lines.append(_page_changes(page))
        if not review.affected_pages:
            lines.append(tr("No image changes selected."))
        return "\n".join(lines)

    def _set_status(self, message: str, *, assertive: bool = False, announce: bool = True) -> None:
        message = tr(message)
        self._status.setText(message)
        self._status.setAccessibleName(tr("Compression review status: {value1}", value1=message))
        self._status.setAccessibleDescription(message)
        if announce:
            self._announcer.announce(message, assertive=assertive)

    def _focus(self, widget: QWidget) -> None:
        widget.setFocus(Qt.FocusReason.OtherFocusReason)
        if widget not in (self._close, self._cancel):
            self._scroll.ensureWidgetVisible(widget, 8, 8)

    @staticmethod
    def _label(text: str, name: str, accessible_name: str) -> QLabel:
        label = QLabel(tr(text))
        label.setObjectName(name)
        label.setAccessibleName(f"{accessible_name}: {text}")
        label.setAccessibleDescription(tr(text))
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        return label

    @staticmethod
    def _button(text: str, name: str, description: str) -> QPushButton:
        button = QPushButton(tr(text))
        button.setObjectName(name)
        button.setAccessibleName(tr(text))
        button.setAccessibleDescription(tr(description))
        button.setAutoDefault(False)
        button.setDefault(False)
        return button


def _limit(value: int | None) -> str:
    return (
        tr("not specified") if value is None else tr("{value1} bytes", value1=format_number(value))
    )


def _dpi(value: int) -> str:
    return tr("{value:g}", value=value / 1000)


def _page_changes(page: CompressionPageChange) -> str:
    """Keep one bounded line per page even when it contains many image objects."""

    def limits(attribute: str, *, dpi: bool = False) -> str:
        lower = min(getattr(image, attribute) for image in page.images)
        upper = max(getattr(image, attribute) for image in page.images)
        if lower != upper:
            if dpi:
                return tr("{lower:g}–{upper:g}", lower=lower / 1000, upper=upper / 1000)
            return tr("{lower}–{upper}", lower=lower, upper=upper)
        return _dpi(lower) if dpi else tr("{value}", value=lower)

    colors = sorted(
        {
            f"{image.original_color_mode.value} → {image.proposed_color_mode.value}"
            for image in page.images
        }
    )
    return tr(
        "  {value1} changed image(s); width × height ranges {value2} × {value3} → {value4} × {value5} pixels; DPI {value6} × {value7} → {value8} × {value9}; color {value10}; JPEG quality {value11}.",
        value1=len(page.images),
        value2=limits("original_width_pixels"),
        value3=limits("original_height_pixels"),
        value4=limits("proposed_width_pixels"),
        value5=limits("proposed_height_pixels"),
        value6=limits("original_effective_dpi_x_milli", dpi=True),
        value7=limits("original_effective_dpi_y_milli", dpi=True),
        value8=limits("proposed_effective_dpi_x_milli", dpi=True),
        value9=limits("proposed_effective_dpi_y_milli", dpi=True),
        value10=", ".join(colors),
        value11=limits("jpeg_quality"),
    )


__all__ = ("CompressionReviewDialog",)

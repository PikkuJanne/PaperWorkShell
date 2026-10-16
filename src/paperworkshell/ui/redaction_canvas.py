"""Bounded, disposable preview of unapplied removal intent; never covers source pixels."""

from __future__ import annotations

from uuid import UUID

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QHideEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import QLabel, QWidget

from paperworkshell.domain.page_coordinates import (
    BoxQuantization,
    PageCoordinateError,
    PageCoordinateTransform,
    RenderedImageSize,
    ScreenBox,
    ScreenViewport,
)
from paperworkshell.domain.redaction import RedactionMark


class RedactionIntentCanvas(QLabel):
    """Patterned outline projection with an optional drag-to-add convenience."""

    region_requested = Signal(object)

    def __init__(
        self,
        *,
        transform: PageCoordinateTransform,
        preview: QPixmap | None = None,
        display_size: RenderedImageSize | None = None,
        pointer_mapping_enabled: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if (
            type(transform) is not PageCoordinateTransform
            or (preview is not None and type(preview) is not QPixmap)
            or (display_size is not None and type(display_size) is not RenderedImageSize)
            or type(pointer_mapping_enabled) is not bool
        ):
            raise ValueError("invalid removal preview facts")
        self._transform = transform
        # A deskew projection has no safe inverse in this coordinate model.
        self._preview = preview if pointer_mapping_enabled else None
        self._display_size = display_size
        self._pointer_mapping_enabled = pointer_mapping_enabled
        self._records: tuple[RedactionMark, ...] = ()
        self._selected: UUID | None = None
        self._start: QPointF | None = None
        self._end: QPointF | None = None
        self.setObjectName("redactionIntentCanvas")
        self.setAccessibleName("Unapplied removal regions preview")
        self.setAccessibleDescription(
            "Dashed crossed outlines show intent only. Information is still present. "
            "Use the region list and numeric fields for all actions without a pointer."
        )
        marker = QPixmap(1, 1)
        marker.fill(Qt.GlobalColor.transparent)
        self.setPixmap(marker)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMinimumSize(240, 200)
        self.setMouseTracking(True)

    def set_records(self, records: tuple[RedactionMark, ...], selected: UUID | None) -> None:
        if type(records) is not tuple or any(
            type(record) is not RedactionMark for record in records
        ):
            raise ValueError("invalid removal preview records")
        self._records = records
        self._selected = selected
        self._cancel_drag()
        self.update()

    def _page_rect(self) -> QRectF:
        if self._preview is not None and not self._preview.isNull():
            width, height = self._preview.width(), self._preview.height()
        elif self._display_size is not None:
            width, height = self._display_size.width_pixels, self._display_size.height_pixels
        else:
            crop = self._transform.manifest_crop
            width, height = 612 * crop.width, 792 * crop.height
            if int(self._transform.manifest_rotation) in {90, 270}:
                width, height = height, width
        scale = min(max(1, self.width() - 24) / width, max(1, self.height() - 24) / height)
        page_width, page_height = width * scale, height * scale
        return QRectF(
            (self.width() - page_width) / 2,
            (self.height() - page_height) / 2,
            page_width,
            page_height,
        )

    def _mapping(self) -> tuple[RenderedImageSize, ScreenViewport]:
        page = self._page_rect()
        return (
            RenderedImageSize(max(1, round(page.width())), max(1, round(page.height()))),
            ScreenViewport(page.x(), page.y(), page.width(), page.height()),
        )

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().window())
        page = self._page_rect()
        painter.fillRect(page, Qt.GlobalColor.white)
        if self._preview is not None and not self._preview.isNull():
            painter.drawPixmap(page, self._preview, QRectF(self._preview.rect()))
        else:
            painter.setPen(Qt.GlobalColor.black)
            painter.drawText(
                page.adjusted(8, 8, -8, -8),
                int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
                "Deskewed page schematic. Use exact fields; pointer selection is off."
                if not self._pointer_mapping_enabled
                else "Preview unavailable. Use exact full-page fields and review the source.",
            )
        size, viewport = self._mapping()
        for position, record in enumerate(self._records, start=1):
            visible = self._transform.visible_part(record.box)
            if visible is None:
                continue
            screen = self._transform.normalized_box_to_screen(visible, size, viewport)
            rect = QRectF(
                screen.left, screen.top, screen.right - screen.left, screen.bottom - screen.top
            )
            # Dual outlines retain contrast over dark or light page pixels. No fill hides content.
            painter.setPen(QPen(Qt.GlobalColor.white, 6, Qt.PenStyle.SolidLine))
            painter.drawRect(rect)
            painter.setPen(
                QPen(
                    Qt.GlobalColor.black,
                    3 if record.mark_id == self._selected else 2,
                    Qt.PenStyle.DashLine,
                )
            )
            painter.drawRect(rect)
            painter.drawLine(rect.topLeft(), rect.bottomRight())
            painter.drawLine(rect.topRight(), rect.bottomLeft())
            label = f"{position}: UNAPPLIED"
            label_rect = QRectF(
                rect.left(),
                rect.top(),
                painter.fontMetrics().horizontalAdvance(label) + 8,
                painter.fontMetrics().height() + 4,
            )
            painter.fillRect(label_rect, Qt.GlobalColor.white)
            painter.drawText(label_rect, int(Qt.AlignmentFlag.AlignCenter), label)
        if self._start is not None and self._end is not None:
            painter.setPen(QPen(Qt.GlobalColor.black, 2, Qt.PenStyle.DashLine))
            painter.drawRect(QRectF(self._start, self._end).normalized())
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            self.isEnabled()
            and self._pointer_mapping_enabled
            and self._preview is not None
            and not self._preview.isNull()
            and event.button() is Qt.MouseButton.LeftButton
            and self._page_rect().contains(event.position())
        ):
            self._start = event.position()
            self._end = event.position()
            self.grabMouse()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._start is not None:
            page = self._page_rect()
            self._end = QPointF(
                min(page.right(), max(page.left(), event.position().x())),
                min(page.bottom(), max(page.top(), event.position().y())),
            )
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() is not Qt.MouseButton.LeftButton or self._start is None:
            super().mouseReleaseEvent(event)
            return
        page = self._page_rect()
        start = self._start
        end = QPointF(
            min(page.right(), max(page.left(), event.position().x())),
            min(page.bottom(), max(page.top(), event.position().y())),
        )
        self._cancel_drag()
        if end is None or abs(start.x() - end.x()) < 4 or abs(start.y() - end.y()) < 4:
            return
        rect = QRectF(start, end).normalized()
        try:
            size, viewport = self._mapping()
            box = self._transform.screen_box_to_normalized(
                ScreenBox(rect.left(), rect.top(), rect.right(), rect.bottom()),
                size,
                viewport,
                quantization=BoxQuantization.OUTWARD,
            )
        except PageCoordinateError:
            return
        self.region_requested.emit(box)

    def _cancel_drag(self) -> None:
        self._start = self._end = None
        if QWidget.mouseGrabber() is self:
            self.releaseMouse()
        self.update()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        self._cancel_drag()
        super().hideEvent(event)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        if event.type() is QEvent.Type.EnabledChange:
            self._cancel_drag()
        super().changeEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        self._cancel_drag()
        super().resizeEvent(event)

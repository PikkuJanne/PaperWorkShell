"""Bounded preview of unapplied intent using outlines, without filled coverings."""

from __future__ import annotations

from uuid import UUID

from PySide6.QtCore import QEvent, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QAccessible,
    QAccessibleEvent,
    QHideEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QResizeEvent,
    QTransform,
)
from PySide6.QtWidgets import QLabel, QWidget

from paperworkshell.domain.page_coordinates import (
    BoxQuantization,
    NormalizedPagePoint,
    PageCoordinateError,
    PageCoordinateTransform,
    RenderedImageSize,
    ScreenBox,
    ScreenViewport,
)
from paperworkshell.domain.redaction import MAX_REDACTION_MARKS, RedactionMark
from paperworkshell.localization.catalog import mark, tr

_CONTRAST_OUTLINE_WIDTH = 6
_DENSE_DESCRIPTION = mark(
    "Dense preview: only the visible part of the selected region is shown; all regions remain in the list."
)
_SELECT_FROM_LIST = mark("Select a region in the list to show its outline.")
_PREVIEW_DESCRIPTION = mark(
    "Dashed crossed outlines show intent only. Information is still present. Use the region list and numeric fields for all actions without a pointer."
)


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
        self._selected_position: int | None = None
        self._start: QPointF | None = None
        self._end: QPointF | None = None
        self._projection_key: tuple[object, ...] | None = None
        self._outline_path = QPainterPath()
        self._projected_rects: dict[UUID, QRectF] = {}
        self._dense_preview = False
        self.setObjectName("redactionIntentCanvas")
        # Mirror surrounding controls, never the source-page coordinate system.
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.setAccessibleName(tr("Unapplied removal regions preview"))
        self.setAccessibleDescription(tr(_PREVIEW_DESCRIPTION))
        marker = QPixmap(1, 1)
        marker.fill(Qt.GlobalColor.transparent)
        self.setPixmap(marker)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMinimumSize(240, 320)
        self.setMouseTracking(True)

    def set_records(self, records: tuple[RedactionMark, ...], selected: UUID | None) -> None:
        if (
            type(records) is not tuple
            or len(records) > MAX_REDACTION_MARKS
            or any(type(record) is not RedactionMark for record in records)
        ):
            raise ValueError("invalid removal preview records")
        if records != self._records:
            self._invalidate_projection()
        self._records = records
        self._selected = selected
        self._selected_position = next(
            (
                position
                for position, mark in enumerate(records, start=1)
                if mark.mark_id == selected
            ),
            None,
        )
        self._cancel_drag()
        self.update()

    def _invalidate_projection(self) -> None:
        self._projection_key = None
        self._outline_path = QPainterPath()
        self._projected_rects.clear()

    def _legend_text(self) -> str:
        text = tr("UNAPPLIED — information remains.")
        if self._dense_preview:
            text += f"\n{tr(_DENSE_DESCRIPTION)}"
        if self._selected_position is not None:
            text += tr("\nSelected region {value1}: UNAPPLIED.", value1=self._selected_position)
        elif self._dense_preview:
            text += f"\n{tr(_SELECT_FROM_LIST)}"
        return text

    def _legend_height(self) -> int:
        # Reserve the largest legend before projection. Density/selection changes
        # cannot move the page or change the pointer mapping between frames.
        base = tr("UNAPPLIED — information remains.\n{value1}\n", value1=tr(_DENSE_DESCRIPTION))
        candidates = (
            base + tr("Selected region {value1}: UNAPPLIED.", value1=MAX_REDACTION_MARKS),
            base + tr(_SELECT_FROM_LIST),
        )
        metrics = self.fontMetrics()
        text_height = max(
            metrics.boundingRect(
                QRect(0, 0, max(1, self.width() - 8), 100_000),
                int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
                text,
            ).height()
            for text in candidates
        )
        return text_height + 8

    def _update_accessible_description(self) -> None:
        description = tr(_PREVIEW_DESCRIPTION)
        if self._dense_preview:
            description += f" {tr(_DENSE_DESCRIPTION)}"
            if self._selected_position is None:
                description += f" {tr(_SELECT_FROM_LIST)}"
        if description != self.accessibleDescription():
            self.setAccessibleDescription(tr(description))
            QAccessible.updateAccessibility(
                QAccessibleEvent(self, QAccessible.Event.DescriptionChanged)
            )

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
        legend_height = self._legend_height()
        available_height = max(1, self.height() - legend_height - 24)
        scale = min(max(1, self.width() - 24) / width, available_height / height)
        page_width, page_height = width * scale, height * scale
        return QRectF(
            (self.width() - page_width) / 2,
            legend_height + (self.height() - legend_height - page_height) / 2,
            page_width,
            page_height,
        )

    def _mapping(self) -> tuple[RenderedImageSize, ScreenViewport]:
        page = self._page_rect()
        return (
            RenderedImageSize(max(1, round(page.width())), max(1, round(page.height()))),
            ScreenViewport(page.x(), page.y(), page.width(), page.height()),
        )

    def _ensure_projection(self) -> None:
        # Keep one disposable path/rectangle projection, bounded by MAX_REDACTION_MARKS.
        # The shared page preview is referenced; no source pixel copy is retained.
        key = (
            self.width(),
            self.height(),
            self._legend_height(),
            self._transform,
            self._display_size,
            None if self._preview is None else self._preview.cacheKey(),
        )
        if key == self._projection_key:
            self._update_accessible_description()
            return
        path = QPainterPath()
        rectangles: dict[UUID, QRectF] = {}
        outline_footprint = 0.0
        size, viewport = self._mapping()
        crop = self._transform.manifest_crop
        # The domain model maps crop/quarter-turn geometry affinely. Derive the
        # Qt map from three validated corner projections, then project all trusted
        # mark rectangles in Qt without repeating Python coordinate validation.
        origin = self._transform.normalized_to_screen(
            NormalizedPagePoint(crop.x, crop.y), size, viewport
        )
        right = self._transform.normalized_to_screen(
            NormalizedPagePoint(crop.x + crop.width, crop.y), size, viewport
        )
        bottom = self._transform.normalized_to_screen(
            NormalizedPagePoint(crop.x, crop.y + crop.height), size, viewport
        )
        xx, xy = (right.x - origin.x) / crop.width, (right.y - origin.y) / crop.width
        yx, yy = (bottom.x - origin.x) / crop.height, (bottom.y - origin.y) / crop.height
        projection = QTransform(
            xx,
            xy,
            yx,
            yy,
            origin.x - crop.x * xx - crop.y * yx,
            origin.y - crop.x * xy - crop.y * yy,
        )
        crop_rect = QRectF(crop.x, crop.y, crop.width, crop.height)
        for record in self._records:
            box = record.box
            visible = QRectF(box.x, box.y, box.width, box.height).intersected(crop_rect)
            if visible.isEmpty():
                continue
            rect = projection.mapRect(visible)
            rectangles[record.mark_id] = rect
            outline_footprint += (rect.width() + rect.height()) * 2 * _CONTRAST_OUTLINE_WIDTH
            path.addRect(rect)
            path.moveTo(rect.topLeft())
            path.lineTo(rect.bottomRight())
            path.moveTo(rect.topRight())
            path.lineTo(rect.bottomLeft())
        self._outline_path = path
        self._projected_rects = rectangles
        self._projection_key = key
        self._dense_preview = outline_footprint > viewport.width * viewport.height
        self._update_accessible_description()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        self._ensure_projection()
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().window())
        painter.setPen(self.palette().windowText().color())
        painter.drawText(
            QRectF(4, 0, max(1, self.width() - 8), self._legend_height()),
            int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
            self._legend_text(),
        )
        page = self._page_rect()
        painter.fillRect(page, Qt.GlobalColor.white)
        if self._preview is not None and not self._preview.isNull():
            painter.drawPixmap(page, self._preview, QRectF(self._preview.rect()))
        else:
            painter.setPen(Qt.GlobalColor.black)
            painter.drawText(
                page.adjusted(8, 8, -8, -8),
                int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
                tr("Deskewed page schematic. Use exact fields; pointer selection is off.")
                if not self._pointer_mapping_enabled
                else tr("Preview unavailable. Use exact full-page fields and review the source."),
            )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        selected_rect = self._projected_rects.get(self._selected) if self._selected else None
        if self._dense_preview:
            # Dense crossed strokes can resemble an opaque cover. Keep source
            # pixels reviewable by showing only the explicitly selected region.
            if selected_rect is not None:
                selected_path = QPainterPath()
                selected_path.addRect(selected_rect)
                selected_path.moveTo(selected_rect.topLeft())
                selected_path.lineTo(selected_rect.bottomRight())
                selected_path.moveTo(selected_rect.topRight())
                selected_path.lineTo(selected_rect.bottomLeft())
                painter.setPen(
                    QPen(Qt.GlobalColor.white, _CONTRAST_OUTLINE_WIDTH, Qt.PenStyle.SolidLine)
                )
                painter.drawPath(selected_path)
                painter.setPen(QPen(Qt.GlobalColor.black, 3, Qt.PenStyle.DashLine))
                painter.drawPath(selected_path)
        else:
            painter.setPen(
                QPen(Qt.GlobalColor.white, _CONTRAST_OUTLINE_WIDTH, Qt.PenStyle.SolidLine)
            )
            painter.drawPath(self._outline_path)
            painter.setPen(QPen(Qt.GlobalColor.black, 2, Qt.PenStyle.DashLine))
            painter.drawPath(self._outline_path)
            if selected_rect is not None:
                painter.setPen(QPen(Qt.GlobalColor.black, 3, Qt.PenStyle.DashLine))
                painter.drawRect(selected_rect)
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
        if event.type() in {QEvent.Type.EnabledChange, QEvent.Type.FontChange}:
            self._cancel_drag()
        super().changeEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        self._invalidate_projection()
        self._cancel_drag()
        super().resizeEvent(event)

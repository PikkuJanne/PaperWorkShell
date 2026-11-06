"""Disposable full-page projection for the accessible overlay editor.

The widget is pointer convenience, never the authoritative editing surface.
It paints an already validated, bounded organizer preview and content-free
overlay geometry.  The native list, exact fields, and buttons remain the
complete keyboard and assistive-technology path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QHideEvent, QMouseEvent, QPainter, QPaintEvent, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QWidget

from paperworkshell.domain.page_coordinates import (
    NORMALIZED_PAGE_MILLIONTHS,
    BoxQuantization,
    NormalizedPageBox,
    NormalizedPagePoint,
    PageCoordinateError,
    PageCoordinateTransform,
    RenderedImageSize,
    ScreenBox,
    ScreenPoint,
    ScreenViewport,
)
from paperworkshell.localization.catalog import mark, tr
from paperworkshell.overlays.models import OverlayRecord, OverlayVisibility
from paperworkshell.ui.localized_messages import localized_overlay_record_name

MIN_EDITOR_ZOOM_PERCENT: Final = 25
MAX_EDITOR_ZOOM_PERCENT: Final = 400
DEFAULT_EDITOR_ZOOM_PERCENT: Final = 100
_CANVAS_MARGIN: Final = 24
_BASE_LONG_EDGE: Final = 680
_HANDLE_SIZE: Final = 12.0
_MIN_POINTER_BOX_SPAN: Final = 8.0
_POINTER_MOVE_THRESHOLD: Final = 2.0


@dataclass(frozen=True, slots=True)
class _DragState:
    overlay_id: UUID
    original_box: NormalizedPageBox
    start_normalized_x: int
    start_normalized_y: int
    start_screen_x: float
    start_screen_y: float
    resize: bool
    original_screen_box: ScreenBox | None


@dataclass(frozen=True, slots=True)
class _PointerProposal:
    box: NormalizedPageBox
    snapped: bool


class OverlayEditorCanvas(QLabel):
    """Paint one full page and emit normalized pointer edits on release."""

    selection_requested = Signal(object)
    box_change_requested = Signal(object, object, bool)
    interaction_message = Signal(str, bool)

    def __init__(
        self,
        *,
        transform: PageCoordinateTransform,
        preview: QPixmap | None = None,
        display_size: RenderedImageSize | None = None,
        pointer_mapping_enabled: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        if type(transform) is not PageCoordinateTransform:
            raise ValueError("transform must be a PageCoordinateTransform")
        if preview is not None and type(preview) is not QPixmap:
            raise ValueError("preview must be a QPixmap or None")
        if display_size is not None and type(display_size) is not RenderedImageSize:
            raise ValueError("display_size must be a RenderedImageSize or None")
        if type(pointer_mapping_enabled) is not bool:
            raise ValueError("pointer_mapping_enabled must be a bool")
        super().__init__(parent)
        self._transform = transform
        self._preview = preview
        self._display_size = display_size
        self._pointer_mapping_enabled = pointer_mapping_enabled
        self._records: tuple[OverlayRecord, ...] = ()
        self._selected_overlay_id: UUID | None = None
        self._zoom_percent = DEFAULT_EDITOR_ZOOM_PERCENT
        self._show_guides = True
        self._snap_enabled = False
        self._guide_step = 50_000
        self._drag: _DragState | None = None
        self._transient_box: NormalizedPageBox | None = None
        self._transient_snapped = False

        self.setObjectName("overlayEditorCanvas")
        # Document coordinates remain physical left/top in every interface locale.
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.setAccessibleName(tr("Full-page visual overlay preview"))
        description = tr(
            "Visual preview only. Use the visual-marks list, exact property fields, movement buttons, zoom controls, and pan controls for a complete keyboard path."
        )
        if not pointer_mapping_enabled:
            description += tr(
                " The page is deskewed, so this is a schematic and pointer editing is disabled."
            )
        self.setAccessibleDescription(tr(description))
        role_pixmap = QPixmap(1, 1)
        role_pixmap.fill(Qt.GlobalColor.transparent)
        self.setPixmap(role_pixmap)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setAutoFillBackground(False)
        self._apply_canvas_size()

    @property
    def zoom_percent(self) -> int:
        return self._zoom_percent

    @property
    def base_canvas_size(self) -> QSize:
        return self._base_canvas_size()

    @property
    def pointer_mapping_enabled(self) -> bool:
        return self._pointer_mapping_enabled

    def visibility_for(self, record: OverlayRecord) -> OverlayVisibility:
        """Return the content-free active-crop visibility shown by this canvas."""

        if type(record) is not OverlayRecord:
            raise ValueError("record must be an OverlayRecord")
        visible = self._transform.visible_part(record.box)
        if visible is None:
            return OverlayVisibility.OUTSIDE_ACTIVE_CROP
        if visible == record.box:
            return OverlayVisibility.VISIBLE
        return OverlayVisibility.PARTIALLY_VISIBLE

    def set_records(
        self,
        records: tuple[OverlayRecord, ...],
        selected_overlay_id: UUID | None,
    ) -> None:
        if type(records) is not tuple or any(
            type(record) is not OverlayRecord for record in records
        ):
            raise ValueError("records must contain exact OverlayRecord values")
        if selected_overlay_id is not None and type(selected_overlay_id) is not UUID:
            raise ValueError("selected_overlay_id must be a UUID or None")
        self._records = records
        self._selected_overlay_id = selected_overlay_id
        self._cancel_drag()
        self.update()

    def set_zoom_percent(self, percent: int) -> None:
        if (
            type(percent) is not int
            or not MIN_EDITOR_ZOOM_PERCENT <= percent <= MAX_EDITOR_ZOOM_PERCENT
        ):
            raise ValueError("zoom percent is outside the supported range")
        if percent == self._zoom_percent:
            return
        self._zoom_percent = percent
        self._cancel_drag()
        self._apply_canvas_size()
        self.update()

    def set_guide_options(self, *, visible: bool, snap: bool, step_millionths: int) -> None:
        if (
            type(visible) is not bool
            or type(snap) is not bool
            or type(step_millionths) is not int
            or not 1 <= step_millionths <= NORMALIZED_PAGE_MILLIONTHS
            or NORMALIZED_PAGE_MILLIONTHS % step_millionths != 0
        ):
            raise ValueError("invalid guide options")
        self._show_guides = visible
        self._snap_enabled = snap
        self._guide_step = step_millionths
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), self.palette().window())
        page = self._page_rect()
        painter.fillRect(page, QColor("white"))
        painter.setPen(QPen(self.palette().mid().color(), 2, Qt.PenStyle.SolidLine))
        painter.drawRect(page)
        if self._preview is not None and not self._preview.isNull():
            painter.drawPixmap(page, self._preview, QRectF(self._preview.rect()))
        else:
            # The document surface remains white in every application theme.
            painter.setPen(QColor("black"))
            placeholder = (
                tr(
                    "Deskewed page schematic. Exact overlay geometry is shown without the pixel preview because the saved deskew transform is not a safe pointer map."
                )
                if not self._pointer_mapping_enabled
                else tr("Page preview unavailable. Overlay geometry remains exact.")
            )
            painter.drawText(
                page.adjusted(20, 20, -20, -20),
                int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
                placeholder,
            )
        if self._show_guides:
            self._paint_guides(painter, page)
        for record in self._records:
            self._paint_record(painter, record)
        if not self._pointer_mapping_enabled:
            text = tr(
                "Deskewed preview: use the exact fields or keyboard controls; pointer editing is off."
            )
            metrics = painter.fontMetrics()
            width = max(1, round(page.width() - 32))
            measured = metrics.boundingRect(
                0,
                0,
                width,
                max(1, round(page.height() - 16)),
                int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
                text,
            )
            height = min(max(1.0, page.height() - 16), measured.height() + 12)
            banner = QRectF(
                page.left() + 8,
                page.top() + 8,
                max(1.0, page.width() - 16),
                height,
            )
            painter.fillRect(banner, QColor(255, 255, 255, 225))
            painter.setPen(QPen(QColor("black"), 2, Qt.PenStyle.DashLine))
            painter.drawRect(banner)
            painter.drawText(
                banner.adjusted(8, 4, -8, -4),
                int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
                text,
            )
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() is not Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        if not self._pointer_mapping_enabled:
            self.interaction_message.emit(
                mark(
                    "Pointer editing is unavailable for a deskewed preview. Use the exact fields or keyboard controls."
                ),
                True,
            )
            event.accept()
            return
        point = event.position()
        selected = self._record_for_id(self._selected_overlay_id)
        selected_screen = None if selected is None else self._screen_box_for(selected)
        resize_requested = (
            selected is not None
            and selected_screen is not None
            and self._resize_handle_rect(selected_screen).contains(point)
            and self._transform.manifest_crop.contains_box(selected.box)
        )
        record = selected if resize_requested else self._top_record_at(point)
        if record is None:
            self.interaction_message.emit(
                mark(
                    "No visual mark is at that point. Select a mark from the list to edit it exactly."
                ),
                False,
            )
            event.accept()
            return
        self.selection_requested.emit(record.overlay_id)
        record = self._record_for_id(record.overlay_id)
        if record is None:
            event.accept()
            return
        refreshed_screen = self._screen_box_for(record)
        resize = (
            resize_requested
            and refreshed_screen is not None
            and self._resize_handle_rect(refreshed_screen).contains(point)
            and self._transform.manifest_crop.contains_box(record.box)
        )
        if not resize and (
            refreshed_screen is None
            or not QRectF(
                refreshed_screen.left,
                refreshed_screen.top,
                refreshed_screen.right - refreshed_screen.left,
                refreshed_screen.bottom - refreshed_screen.top,
            ).contains(point)
        ):
            self.interaction_message.emit(
                mark(
                    "No visual mark is at that point. Select a mark from the list to edit it exactly."
                ),
                False,
            )
            event.accept()
            return
        try:
            normalized = self._normalized_point(point)
        except PageCoordinateError:
            event.accept()
            return
        original_screen = self._screen_box_for(record) if resize else None
        self._drag = _DragState(
            record.overlay_id,
            record.box,
            normalized.x,
            normalized.y,
            point.x(),
            point.y(),
            resize,
            original_screen,
        )
        self._transient_box = record.box
        self._transient_snapped = False
        self.grabMouse()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag is None:
            super().mouseMoveEvent(event)
            return
        proposal = self._proposed_drag_box(event.position())
        if proposal is not None:
            self._transient_box = proposal.box
            self._transient_snapped = proposal.snapped
            self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() is not Qt.MouseButton.LeftButton or self._drag is None:
            super().mouseReleaseEvent(event)
            return
        drag = self._drag
        proposal = self._proposed_drag_box(event.position())
        proposed = proposal.box if proposal is not None else self._transient_box
        snapped = proposal.snapped if proposal is not None else self._transient_snapped
        self._cancel_drag()
        self.update()
        if proposed is not None and proposed != drag.original_box:
            self.box_change_requested.emit(drag.overlay_id, proposed, snapped)
        event.accept()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        self._cancel_drag()
        super().hideEvent(event)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        if event.type() is QEvent.Type.EnabledChange and not self.isEnabled():
            self._cancel_drag()
        super().changeEvent(event)

    def event(self, event: QEvent) -> bool:
        if event.type() is QEvent.Type.UngrabMouse and self._drag is not None:
            self._clear_drag_state()
        return super().event(event)

    def _paint_guides(self, painter: QPainter, page: QRectF) -> None:
        pen = QPen(QColor(70, 70, 70, 150), 1, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        crop = self._transform.manifest_crop
        size = self._rendered_size()
        viewport = self._viewport()
        for value in range(self._guide_step, NORMALIZED_PAGE_MILLIONTHS, self._guide_step):
            if crop.x <= value <= crop.right:
                start = self._transform.normalized_to_screen(
                    NormalizedPagePoint(value, crop.y), size, viewport
                )
                end = self._transform.normalized_to_screen(
                    NormalizedPagePoint(value, crop.bottom), size, viewport
                )
                painter.drawLine(QPointF(start.x, start.y), QPointF(end.x, end.y))
            if crop.y <= value <= crop.bottom:
                start = self._transform.normalized_to_screen(
                    NormalizedPagePoint(crop.x, value), size, viewport
                )
                end = self._transform.normalized_to_screen(
                    NormalizedPagePoint(crop.right, value), size, viewport
                )
                painter.drawLine(QPointF(start.x, start.y), QPointF(end.x, end.y))
        text = tr("Dashed guides: {value1:g}% grid", value1=self._guide_step / 10000)
        metrics = painter.fontMetrics()
        available_width = max(1, round(page.width() - 16))
        label_height = min(page.height() - 16, metrics.height() + 8)
        label = QRectF(
            page.left() + 8,
            max(page.top() + 8, page.bottom() - 8 - label_height),
            available_width,
            max(1.0, label_height),
        )
        painter.fillRect(label, QColor(255, 255, 255, 220))
        painter.setPen(QColor("black"))
        visible_text = metrics.elidedText(
            text,
            Qt.TextElideMode.ElideRight,
            max(1, round(label.width() - 8)),
        )
        painter.drawText(
            label.adjusted(4, 2, -4, -2),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            visible_text,
        )

    def _paint_record(self, painter: QPainter, record: OverlayRecord) -> None:
        screen = self._screen_box_for(record)
        if screen is None:
            return
        rect = QRectF(
            screen.left, screen.top, screen.right - screen.left, screen.bottom - screen.top
        )
        selected = record.overlay_id == self._selected_overlay_id
        painter.setBrush(QColor(255, 245, 120, 45) if selected else QColor(90, 140, 220, 25))
        painter.setPen(
            QPen(
                QColor("black") if selected else QColor(40, 70, 120),
                3 if selected else 2,
                Qt.PenStyle.SolidLine if selected else Qt.PenStyle.DashLine,
            )
        )
        painter.drawRect(rect)
        name = localized_overlay_record_name(record).replace(" visual mark", "")
        metrics = painter.fontMetrics()
        inner = rect.adjusted(3, 3, -3, -3)
        if inner.width() >= 8 and inner.height() >= metrics.height() + 4:
            label = QRectF(
                inner.left(),
                inner.top(),
                inner.width(),
                min(inner.height(), metrics.height() + 6),
            )
            visible_name = metrics.elidedText(
                name,
                Qt.TextElideMode.ElideRight,
                max(1, round(label.width() - 6)),
            )
            painter.fillRect(label, QColor(255, 255, 255, 215))
            painter.setPen(QColor("black"))
            painter.drawText(
                label.adjusted(3, 1, -3, -1),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                visible_name,
            )
        if selected and self._transform.manifest_crop.contains_box(record.box):
            handle = self._resize_handle_rect(screen)
            painter.fillRect(handle, QColor("white"))
            painter.setPen(QPen(QColor("black"), 2, Qt.PenStyle.SolidLine))
            painter.drawRect(handle)

    def _screen_box_for(self, record: OverlayRecord) -> ScreenBox | None:
        box = (
            self._transient_box
            if self._drag is not None
            and self._drag.overlay_id == record.overlay_id
            and self._transient_box is not None
            else record.box
        )
        visible = self._transform.visible_part(box)
        if visible is None:
            return None
        try:
            return self._transform.normalized_box_to_screen(
                visible,
                self._rendered_size(),
                self._viewport(),
            )
        except PageCoordinateError:
            return None

    def _top_record_at(self, point: QPointF) -> OverlayRecord | None:
        for record in reversed(self._records):
            screen = self._screen_box_for(record)
            if screen is not None and QRectF(
                screen.left,
                screen.top,
                screen.right - screen.left,
                screen.bottom - screen.top,
            ).contains(point):
                return record
        return None

    def _proposed_drag_box(self, point: QPointF) -> _PointerProposal | None:
        drag = self._drag
        if drag is None:
            return None
        delta_x = point.x() - drag.start_screen_x
        delta_y = point.y() - drag.start_screen_y
        if max(abs(delta_x), abs(delta_y)) < _POINTER_MOVE_THRESHOLD:
            return _PointerProposal(drag.original_box, False)
        try:
            if drag.resize:
                original = drag.original_screen_box
                if original is None:
                    return None
                right = min(
                    self._viewport().right,
                    max(original.left + _MIN_POINTER_BOX_SPAN, original.right + delta_x),
                )
                bottom = min(
                    self._viewport().bottom,
                    max(original.top + _MIN_POINTER_BOX_SPAN, original.bottom + delta_y),
                )
                proposed = self._transform.screen_box_to_normalized(
                    ScreenBox(original.left, original.top, right, bottom),
                    self._rendered_size(),
                    self._viewport(),
                    quantization=BoxQuantization.OUTWARD,
                )
                snapped = False
            else:
                normalized = self._normalized_point(self._clamp_to_page(point))
                dx = normalized.x - drag.start_normalized_x
                dy = normalized.y - drag.start_normalized_y
                x = min(
                    NORMALIZED_PAGE_MILLIONTHS - drag.original_box.width,
                    max(0, drag.original_box.x + dx),
                )
                y = min(
                    NORMALIZED_PAGE_MILLIONTHS - drag.original_box.height,
                    max(0, drag.original_box.y + dy),
                )
                unsnapped_x, unsnapped_y = x, y
                if self._snap_enabled:
                    x = _nearest_guide_origin(
                        x,
                        self._guide_step,
                        NORMALIZED_PAGE_MILLIONTHS - drag.original_box.width,
                    )
                    y = _nearest_guide_origin(
                        y,
                        self._guide_step,
                        NORMALIZED_PAGE_MILLIONTHS - drag.original_box.height,
                    )
                snapped = x != unsnapped_x or y != unsnapped_y
                proposed = NormalizedPageBox(
                    x,
                    y,
                    drag.original_box.width,
                    drag.original_box.height,
                )
        except PageCoordinateError:
            return None
        return _PointerProposal(proposed, snapped)

    def _clear_drag_state(self) -> None:
        self._drag = None
        self._transient_box = None
        self._transient_snapped = False

    def _cancel_drag(self) -> None:
        held_mouse = QWidget.mouseGrabber() is self
        self._clear_drag_state()
        if held_mouse:
            self.releaseMouse()

    def _record_for_id(self, overlay_id: UUID | None) -> OverlayRecord | None:
        if overlay_id is None:
            return None
        return next((record for record in self._records if record.overlay_id == overlay_id), None)

    def _normalized_point(self, point: QPointF) -> NormalizedPagePoint:
        return self._transform.screen_to_normalized(
            ScreenPoint(point.x(), point.y()),
            self._rendered_size(),
            self._viewport(),
        )

    def _clamp_to_page(self, point: QPointF) -> QPointF:
        viewport = self._viewport()
        return QPointF(
            min(viewport.right, max(viewport.left, point.x())),
            min(viewport.bottom, max(viewport.top, point.y())),
        )

    def _resize_handle_rect(self, screen: ScreenBox) -> QRectF:
        return QRectF(
            screen.right - _HANDLE_SIZE / 2,
            screen.bottom - _HANDLE_SIZE / 2,
            _HANDLE_SIZE,
            _HANDLE_SIZE,
        )

    def _page_rect(self) -> QRectF:
        return QRectF(
            _CANVAS_MARGIN,
            _CANVAS_MARGIN,
            max(1, self.width() - 2 * _CANVAS_MARGIN),
            max(1, self.height() - 2 * _CANVAS_MARGIN),
        )

    def _rendered_size(self) -> RenderedImageSize:
        page = self._page_rect()
        return RenderedImageSize(max(1, round(page.width())), max(1, round(page.height())))

    def _viewport(self) -> ScreenViewport:
        page = self._page_rect()
        return ScreenViewport(page.left(), page.top(), page.width(), page.height())

    def _base_canvas_size(self) -> QSize:
        if self._preview is not None and not self._preview.isNull():
            width = self._preview.width()
            height = self._preview.height()
        elif self._display_size is not None:
            width = self._display_size.width_pixels
            height = self._display_size.height_pixels
        else:
            crop = self._transform.manifest_crop
            width = max(1, round(612 * crop.width / NORMALIZED_PAGE_MILLIONTHS))
            height = max(1, round(792 * crop.height / NORMALIZED_PAGE_MILLIONTHS))
            if int(self._transform.manifest_rotation) in {90, 270}:
                width, height = height, width
        if width >= height:
            base_width = _BASE_LONG_EDGE
            base_height = max(1, round(_BASE_LONG_EDGE * height / width))
        else:
            base_height = _BASE_LONG_EDGE
            base_width = max(1, round(_BASE_LONG_EDGE * width / height))
        return QSize(base_width + 2 * _CANVAS_MARGIN, base_height + 2 * _CANVAS_MARGIN)

    def _apply_canvas_size(self) -> None:
        base = self._base_canvas_size()
        page_width = max(1, base.width() - 2 * _CANVAS_MARGIN)
        page_height = max(1, base.height() - 2 * _CANVAS_MARGIN)
        width = max(1, round(page_width * self._zoom_percent / 100)) + 2 * _CANVAS_MARGIN
        height = max(1, round(page_height * self._zoom_percent / 100)) + 2 * _CANVAS_MARGIN
        self.setFixedSize(width, height)


def _round_to_guide(value: int, step: int) -> int:
    return ((value + step // 2) // step) * step


def _nearest_guide_origin(value: int, step: int, maximum_origin: int) -> int:
    return min(_round_to_guide(value, step), (maximum_origin // step) * step)

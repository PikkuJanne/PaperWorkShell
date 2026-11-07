"""Bounded organizer rows whose geometry never depends on arriving pixels."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem

from paperworkshell.localization.catalog import tr


class OrganizerPageDelegate(QStyledItemDelegate):
    """Reserve separate image and caption regions in every preview state.

    The view owns stable row rectangles. Painting is clipped to each rectangle;
    document dimensions and rotation can only change the fit inside its image
    region. Native palette roles preserve contrast and selection with lost focus.
    """

    def sizeHint(  # noqa: N802
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        return QSize(320, max(184, option.fontMetrics.height() * 8 + 24))

    @staticmethod
    def regions(rect: QRect) -> tuple[QRect, QRect]:
        """Return nonintersecting reserved image/caption bounds in logical pixels."""
        inner = rect.adjusted(12, 12, -12, -12)
        image_width = min(176, max(48, inner.width() * 2 // 5))
        return (
            QRect(inner.x(), inner.y(), image_width, inner.height()),
            QRect(
                inner.x() + image_width + 16,
                inner.y(),
                max(0, inner.width() - image_width - 16),
                inner.height(),
            ),
        )

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        from PySide6.QtWidgets import QStyle

        painter.save()
        try:
            painter.setClipRect(option.rect)
            selected = bool(option.state & QStyle.StateFlag.State_Selected)
            focused = bool(option.state & QStyle.StateFlag.State_HasFocus)
            background = QPalette.ColorRole.Highlight if selected else QPalette.ColorRole.Base
            foreground = QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text
            painter.fillRect(option.rect.adjusted(2, 2, -2, -2), option.palette.brush(background))
            border = QPen(option.palette.color(QPalette.ColorRole.Highlight), 3 if selected else 1)
            painter.setPen(border)
            painter.drawRect(option.rect.adjusted(3, 3, -4, -4))
            image_rect, caption_rect = self.regions(option.rect)
            preview = index.data(int(Qt.ItemDataRole.DecorationRole))
            if isinstance(preview, QPixmap) and not preview.isNull():
                size = preview.size().scaled(image_rect.size(), Qt.AspectRatioMode.KeepAspectRatio)
                target = QRect(0, 0, size.width(), size.height())
                target.moveCenter(image_rect.center())
                painter.drawPixmap(target, preview)
            else:
                painter.setPen(option.palette.color(foreground))
                painter.drawRect(image_rect.adjusted(1, 1, -1, -1))
                painter.drawText(
                    image_rect.adjusted(6, 6, -6, -6),
                    Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                    tr("Preview pending"),
                )
            painter.setPen(option.palette.color(foreground))
            font = option.font
            font.setBold(selected)
            painter.setFont(font)
            cue = tr("Selected page") if selected else tr("Page")
            cue_height = option.fontMetrics.height() + 8
            painter.drawText(
                QRect(caption_rect.x(), caption_rect.y(), caption_rect.width(), cue_height),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                cue,
            )
            font.setBold(False)
            painter.setFont(font)
            # Full labels remain in the model's accessible text and tooltip.
            # Clip long names to the reserved caption, never adjacent rows.
            text = str(index.data(int(Qt.ItemDataRole.DisplayRole)) or "")
            lines = text.split("\n")
            if lines:
                lines[0] = option.fontMetrics.elidedText(
                    lines[0], Qt.TextElideMode.ElideMiddle, caption_rect.width()
                )
            painter.drawText(
                caption_rect.adjusted(0, cue_height, 0, 0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                "\n".join(lines),
            )
            if focused:
                focus_pen = QPen(option.palette.color(foreground), 1, Qt.PenStyle.DashLine)
                painter.setPen(focus_pen)
                painter.drawRect(option.rect.adjusted(7, 7, -8, -8))
        finally:
            painter.restore()

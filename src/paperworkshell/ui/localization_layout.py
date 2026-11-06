"""Keep translated dialog content reflowable without changing document geometry."""

from __future__ import annotations

from typing import cast

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QAbstractButton,
    QBoxLayout,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLayoutItem,
    QScrollArea,
    QSizePolicy,
)


def _wrapped(source: str, metrics: QFontMetrics, width: int) -> str:
    lines: list[str] = []
    for paragraph in source.split("\n"):
        line = ""
        for word in paragraph.split(" "):
            candidate = word if not line else line + " " + word
            if line and metrics.horizontalAdvance(candidate.replace("&", "")) > width:
                lines.append(line)
                line = word
            else:
                line = candidate
        lines.append(line)
    return "\n".join(lines)


class _LocalizedContentPolicy(QObject):
    """One bounded widget-tree adapter per existing review scroll area."""

    def __init__(self, area: QScrollArea) -> None:
        super().__init__(area)
        self._area = area
        self._updating = False
        area.viewport().installEventFilter(self)
        area.installEventFilter(self)
        self._adapt()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.FontChange,
        ):
            self._adapt()
        return False

    def _adapt(self) -> None:
        content = self._area.widget()
        if self._updating or content is None:
            return
        self._updating = True
        try:
            available = max(160, self._area.viewport().width() - 48)
            for group in content.findChildren(QGroupBox):
                title = group.title()
                layout = group.layout()
                if (
                    not title
                    or layout is None
                    or group.fontMetrics().horizontalAdvance(title) < available
                ):
                    continue
                heading = QLabel(title, group)
                heading.setObjectName(group.objectName() + "TranslatedHeading")
                heading.setAccessibleName(title)
                heading.setWordWrap(True)
                heading.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                if not group.accessibleName():
                    group.setAccessibleName(title)
                if isinstance(layout, QGridLayout):
                    # Preserve all coordinates and spans while adding a visible
                    # wrapping group heading before the existing control rows.
                    items: list[tuple[tuple[int, int, int, int], QLayoutItem]] = []
                    while layout.count():
                        position = cast(tuple[int, int, int, int], layout.getItemPosition(0))
                        item = layout.takeAt(0)
                        if item is not None:
                            items.append((position, item))
                    for (row, column, row_span, column_span), item in items:
                        layout.addItem(item, row + 1, column, row_span, column_span)
                    layout.addWidget(heading, 0, 0, 1, max(1, layout.columnCount()))
                elif isinstance(layout, QBoxLayout):
                    layout.insertWidget(0, heading)
                else:
                    heading.deleteLater()
                    continue
                group.setTitle("")
            for label in content.findChildren(QLabel):
                if not label.pixmap().isNull():
                    continue
                if not label.wordWrap():
                    label.setWordWrap(True)
                if label.minimumWidth():
                    label.setMinimumWidth(0)
                policy = label.sizePolicy()
                if policy.horizontalPolicy() is not QSizePolicy.Policy.Ignored:
                    policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
                    label.setSizePolicy(policy)
            for combo in content.findChildren(QComboBox):
                if (
                    combo.sizeAdjustPolicy()
                    is not QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
                ):
                    combo.setSizeAdjustPolicy(
                        QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
                    )
                if combo.minimumContentsLength() != 6:
                    combo.setMinimumContentsLength(6)
                if combo.minimumWidth():
                    combo.setMinimumWidth(0)
                policy = combo.sizePolicy()
                if policy.horizontalPolicy() is not QSizePolicy.Policy.Expanding:
                    policy.setHorizontalPolicy(QSizePolicy.Policy.Expanding)
                    combo.setSizePolicy(policy)
            for button in content.findChildren(QAbstractButton):
                actual = button.text()
                prior = button.property("pwsLocalizedWrappedText")
                stored = button.property("pwsLocalizedSourceText")
                source = stored if type(stored) is str and actual == prior else actual
                button.setProperty("pwsLocalizedSourceText", source)
                # Keep each action's full accessible name; explicit line breaks
                # affect visual reflow only, never its shortcut or identity.
                parent = button.parentWidget()
                inner = available if parent is None else min(available, parent.width() - 32)
                rendered = _wrapped(source, button.fontMetrics(), max(120, inner - 44))
                button.setProperty("pwsLocalizedWrappedText", rendered)
                if actual != rendered:
                    button.setText(rendered)
                if button.minimumWidth() != 32:
                    button.setMinimumWidth(32)
                policy = button.sizePolicy()
                if policy.horizontalPolicy() is not QSizePolicy.Policy.Preferred:
                    policy.setHorizontalPolicy(QSizePolicy.Policy.Preferred)
                    button.setSizePolicy(policy)
        finally:
            self._updating = False


def adapt_localized_content(area: QScrollArea) -> None:
    """Install after setWidget; resizing keeps all translated controls visible."""
    if area.findChild(_LocalizedContentPolicy) is None:
        _LocalizedContentPolicy(area)

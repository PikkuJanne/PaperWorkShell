"""Native TRACE workbench controls shared by the case and document surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPersistentModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QKeyEvent, QPainter, QPaintEvent, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from paperworkshell.localization.catalog import tr


class TracePageRailDelegate(QStyledItemDelegate):
    """Compact assembled order with a thumbnail and native selected state."""

    compact = False

    def initStyleOption(  # noqa: N802
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        super().initStyleOption(option, index)
        option.text = "\n".join(option.text.splitlines()[:2])
        option.decorationSize = QSize(32, 42) if self.compact else QSize(42, 56)
        option.displayAlignment = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeading

    def sizeHint(  # noqa: N802
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        return QSize(168, max(52 if self.compact else 70, option.fontMetrics.height() * 3 + 10))


@dataclass(frozen=True, slots=True)
class TraceCommand:
    """Stable identity and contextual availability of one existing action."""

    key: str
    label: str
    group: str
    button: QPushButton
    unavailable: str
    terms: tuple[str, ...] = ()

    def matches(self, query: tuple[str, ...]) -> bool:
        searchable = " ".join((self.key, self.label, self.group, *self.terms)).casefold()
        return all(part in searchable for part in query)


class TraceCommandPalette(QDialog):
    def __init__(self, parent: QWidget, commands: tuple[TraceCommand, ...]) -> None:
        super().__init__(parent)
        self.setObjectName("traceCommandPalette")
        self.setWindowTitle(tr("Commands"))
        self.setAccessibleName(tr("Commands"))
        self._commands = {command.key: command for command in commands}
        self.command: TraceCommand | None = None

        layout = QVBoxLayout(self)
        self._search = QLineEdit()
        self._search.setAccessibleName(tr("Search commands"))
        self._search.setPlaceholderText(tr("Search commands"))
        self._search.textChanged.connect(self._filter)
        self._search.returnPressed.connect(self._choose)
        self._search.installEventFilter(self)
        layout.addWidget(self._search)
        self._results = QListWidget()
        self._results.setAccessibleName(tr("Matching commands"))
        self._results.itemActivated.connect(self._choose)
        self._results.currentItemChanged.connect(self._show_reason)
        layout.addWidget(self._results, 1)
        self._reason = QLabel()
        self._reason.setTextFormat(Qt.TextFormat.PlainText)
        self._reason.setWordWrap(True)
        layout.addWidget(self._reason)
        self._filter("")
        self._search.setFocus(Qt.FocusReason.OtherFocusReason)
        self.resize(min(520, parent.width()), min(470, max(240, parent.height())))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if (
            watched is self._search
            and isinstance(event, QKeyEvent)
            and event.type() == QEvent.Type.KeyPress
        ):
            row = self._results.currentRow()
            if event.key() == Qt.Key.Key_Up and row > 0:
                self._results.setCurrentRow(row - 1)
                return True
            if event.key() == Qt.Key.Key_Down and row < self._results.count() - 1:
                self._results.setCurrentRow(row + 1)
                return True
        return super().eventFilter(watched, event)

    def _filter(self, text: str) -> None:
        query = tuple(text.casefold().split())
        self._results.clear()
        for command in self._commands.values():
            if not command.matches(query):
                continue
            label = tr("{value1}  ·  {value2}", value1=command.label, value2=command.group)
            if not command.button.isEnabled():
                label = tr("{value1} (unavailable)", value1=label)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, command.key)
            self._results.addItem(item)
        self._results.setCurrentRow(0)
        if not self._results.count():
            self._reason.setText(tr("No matching commands."))

    def _show_reason(self, item: QListWidgetItem | None, _prior: QListWidgetItem | None) -> None:
        if item is None:
            self._reason.clear()
            return
        command = self._commands[item.data(Qt.ItemDataRole.UserRole)]
        self._reason.setText(
            command.button.accessibleDescription()
            if command.button.isEnabled()
            else command.unavailable
        )

    def _choose(self, _item: QListWidgetItem | None = None) -> None:
        item = cast(QListWidgetItem | None, self._results.currentItem())
        if item is None:
            return
        command = self._commands[item.data(Qt.ItemDataRole.UserRole)]
        if not command.button.isEnabled():
            self._reason.setText(command.unavailable)
            return
        self.command = command
        self.accept()


class _DocumentSheet(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.image = QPixmap()
        self.message = ""
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        inner = self.rect().adjusted(16, 16, -16, -16)
        if self.image.isNull():
            painter.setPen(self.palette().windowText().color())
            painter.drawText(
                inner, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message
            )
        elif inner.isValid():
            size = self.image.size().scaled(inner.size(), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRect(0, 0, size.width(), size.height())
            target.moveCenter(inner.center())
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(target, self.image)
        painter.end()


class TraceDocumentCanvas(QScrollArea):
    """Show a worker-rendered image without allocating a zoomed raster."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("traceDocumentStage")
        self.setAccessibleName(tr("Document preview"))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setMinimumSize(160, 215)
        self._sheet = _DocumentSheet()
        self.setWidget(self._sheet)
        self._zoom = 100

    def display(self, preview: QPixmap | None, description: str) -> None:
        self._sheet.image = QPixmap() if preview is None else preview
        self._sheet.message = description
        self.setAccessibleDescription(description)
        self._fit_sheet()
        self._sheet.update()

    def set_zoom(self, zoom: int) -> None:
        self._zoom = zoom
        self._fit_sheet()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_sheet()

    def _fit_sheet(self) -> None:
        if self._zoom == 100 or self._sheet.image.isNull():
            self._sheet.setMinimumSize(QSize())
            return
        width = self.maximumViewportSize().width()
        height = self.maximumViewportSize().height()
        target = self._sheet.image.size().scaled(
            width - 32, height - 32, Qt.AspectRatioMode.KeepAspectRatio
        )
        self._sheet.setMinimumSize(
            max(0, target.width() * self._zoom // 100 + 32),
            max(0, target.height() * self._zoom // 100 + 32),
        )


class TraceWorkbenchSplit(QSplitter):
    """Keep secondary panes one action away when the canvas needs the width."""

    def __init__(self, pages: QWidget, stage: QWidget, inspector: QWidget) -> None:
        super().__init__(Qt.Orientation.Horizontal)
        self.setObjectName("traceWorkbenchSplit")
        self.setChildrenCollapsible(False)
        self.setMinimumHeight(250)
        self._pages, self._stage, self._inspector = pages, stage, inspector
        self._compact: bool | None = None
        self._active = "document"
        self._inspector_collapsed = False
        for widget in (pages, stage, inspector):
            self.addWidget(widget)
        self.setStretchFactor(1, 1)

    def select_pane(self, pane: str) -> None:
        self._active = pane
        if pane == "inspector":
            self._inspector_collapsed = False
        self._apply_visibility()

    def toggle_inspection(self) -> bool:
        if self._compact:
            self.select_pane("document" if self._active == "inspector" else "inspector")
        else:
            self._inspector_collapsed = not self._inspector_collapsed
            self._apply_visibility()
        return self._inspector.isVisible()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        compact = event.size().width() < max(690, self.fontMetrics().averageCharWidth() * 84)
        if compact == self._compact:
            return
        self._compact = compact
        self._apply_visibility()
        if not compact:
            self.setSizes([204, max(230, self.width() - 474), 270])

    def _apply_visibility(self) -> None:
        self._pages.setVisible(not self._compact or self._active == "pages")
        self._stage.setVisible(not self._compact or self._active == "document")
        self._inspector.setVisible(
            self._active == "inspector" if self._compact else not self._inspector_collapsed
        )

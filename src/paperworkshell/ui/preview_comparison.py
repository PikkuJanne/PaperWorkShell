"""Bounded, keyboard-accessible comparison of one decoded image pair."""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QFocusEvent, QImage, QPainter, QPaintEvent, QPen, QResizeEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QButtonGroup,
    QGroupBox,
    QLabel,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class _ImagePanel(QWidget):
    """Draw at the available scale without retaining an extra scaled image."""

    def __init__(self, title: str, name: str) -> None:
        super().__init__()
        self.image: QImage | None = None
        self.setObjectName(name)
        self.setAccessibleName(f"{title} page preview")
        self.setAccessibleDescription(f"{title} page preview has not been loaded.")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(200)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override name
        return QSize(320, 360)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt override name
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().base())
        image = self.image
        if image is not None:
            bounds = self.rect().adjusted(4, 4, -4, -4)
            size = image.size().scaled(bounds.size(), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRect(0, 0, size.width(), size.height())
            target.moveCenter(bounds.center())
            painter.drawImage(target, image)
        else:
            painter.setPen(self.palette().text().color())
            painter.drawText(
                self.rect().adjusted(8, 8, -8, -8),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                "Page preview not loaded",
            )
        pen = QPen(
            self.palette().highlight().color() if self.hasFocus() else self.palette().mid().color()
        )
        pen.setWidth(3 if self.hasFocus() else 1)
        painter.setPen(pen)
        painter.drawRect(self.rect().adjusted(1, 1, -2, -2))

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802 - Qt override name
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802 - Qt override name
        super().focusOutEvent(event)
        self.update()


class PreviewComparisonWidget(QWidget):
    """Retain at most one pair; callers bind image authority to their review model.

    The budget includes incoming and detached images during replacement. The
    coordinator remains responsible for any other preview/cache allocations.
    Images must describe the same rendered page geometry for a fair comparison.
    """

    def __init__(self, maximum_pair_bytes: int, parent: QWidget | None = None) -> None:
        if type(maximum_pair_bytes) is not int or maximum_pair_bytes < 8:
            raise ValueError("Preview comparison requires a positive image-pair budget.")
        super().__init__(parent)
        self._maximum_pair_bytes = maximum_pair_bytes
        self.setObjectName("previewComparisonWidget")
        self.setAccessibleName("Before and proposed page comparison")
        self.setAccessibleDescription(
            "Choose one view or compare both views at the same scale. On narrow windows the "
            "two views are stacked. Preview pixels do not establish document readability."
        )
        layout = QVBoxLayout(self)
        self._choice_group = QButtonGroup(self)
        self._choices: list[QRadioButton] = []
        for text, name in (
            ("Before", "previewComparisonBeforeChoice"),
            ("Proposed", "previewComparisonProposedChoice"),
            ("Side by side", "previewComparisonBothChoice"),
        ):
            choice = QRadioButton(text)
            choice.setObjectName(name)
            choice.setAccessibleName(f"Show {text.lower()} preview")
            choice.setAccessibleDescription(
                "Use arrow keys to change the comparison view. Both views use matching page geometry."
            )
            self._choice_group.addButton(choice)
            choice.toggled.connect(self._update_visibility)
            self._choices.append(choice)
            layout.addWidget(choice)
        self._panels = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._groups: list[QGroupBox] = []
        self._images: list[_ImagePanel] = []
        for title, name in (("Before", "Before"), ("Proposed", "Proposed")):
            group = QGroupBox(title)
            group.setObjectName(f"previewComparison{name}Group")
            group.setAccessibleName(f"{title} page")
            group.setAccessibleDescription(f"{title} page pixels and availability.")
            group.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            panel = _ImagePanel(title, f"previewComparison{name}Image")
            panel_layout = QVBoxLayout(group)
            panel_layout.addWidget(panel)
            self._panels.addWidget(group)
            self._groups.append(group)
            self._images.append(panel)
        layout.addLayout(self._panels)
        self._status = QLabel("Choose an affected page and load its before and proposed previews.")
        self._status.setObjectName("previewComparisonStatus")
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setWordWrap(True)
        self._status.setAccessibleName("Page comparison availability")
        layout.addWidget(self._status)
        self._choices[2].setChecked(True)
        self.clear()

    @property
    def retained_bytes(self) -> int:
        return sum(panel.image.sizeInBytes() for panel in self._images if panel.image is not None)

    @property
    def has_pair(self) -> bool:
        return all(panel.image is not None for panel in self._images)

    @property
    def focus_controls(self) -> tuple[QWidget, ...]:
        return (*self._choices, *self._images)

    def set_images(self, before: QImage, proposed: QImage) -> bool:
        """Validate before copying; failed replacement removes any stale pixels."""
        self.clear()
        if (
            not isinstance(before, QImage)
            or not isinstance(proposed, QImage)
            or before.isNull()
            or proposed.isNull()
            or before.size() != proposed.size()
            or before.depth() != 32
            or proposed.depth() != 32
            or before.sizeInBytes() + proposed.sizeInBytes() > self._maximum_pair_bytes // 2
        ):
            return False
        copies = (before.copy(), proposed.copy())
        if any(image.isNull() for image in copies):
            return False
        for panel, image in zip(self._images, copies, strict=True):
            panel.image = image
            panel.setAccessibleDescription(
                f"{panel.accessibleName()} loaded: {image.width()} by {image.height()} pixels. "
                "The view scales to fit. Inspect text, stamps, handwriting, and color meaning."
            )
            panel.update()
        self._status.setText(
            "Before and proposed previews loaded at matching dimensions. Inspect small text, "
            "stamps, handwriting, and any meaning carried by color."
        )
        self._status.setAccessibleDescription(self._status.text())
        return True

    def clear(self) -> None:
        for panel in self._images:
            panel.image = None
            panel.setAccessibleDescription(f"{panel.accessibleName()} has not been loaded.")
            panel.update()
        self._status.setText("Choose an affected page and load its before and proposed previews.")
        self._status.setAccessibleDescription(self._status.text())

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override name
        self._panels.setDirection(
            QBoxLayout.Direction.TopToBottom
            if self.width() < 640
            else QBoxLayout.Direction.LeftToRight
        )
        super().resizeEvent(event)

    def _update_visibility(self, _checked: bool = False) -> None:
        for index, group in enumerate(self._groups):
            group.setVisible(self._choices[index].isChecked() or self._choices[2].isChecked())


__all__ = ("PreviewComparisonWidget",)

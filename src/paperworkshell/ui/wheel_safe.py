"""Editable choices that leave the mouse wheel to their surrounding scroll area.

Arrow keys, typing, spin buttons and opened choice lists retain their ordinary
behavior. A wheel gesture never changes a saved property by hovering over it.
"""

from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QSpinBox


class WheelSafeComboBox(QComboBox):
    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        event.ignore()


class WheelSafeSpinBox(QSpinBox):
    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        event.ignore()


class WheelSafeDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        event.ignore()

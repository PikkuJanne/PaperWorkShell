"""Native presentation policy, motion and wrapping geometry contracts."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import QApplication, QProgressBar, QPushButton, QStyle, QWidget

from paperworkshell.ui.presentation import (
    WrappingActionLayout,
    install_presentation_policy,
    set_static_busy,
)


def _app() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def test_policy_preserves_native_palette_and_application_font() -> None:
    application = _app()
    palette, font = QPalette(application.palette()), QFont(application.font())
    install_presentation_policy(application)
    assert application.palette() == palette
    assert application.font() == font


def test_policy_is_idempotent_with_the_existing_focus_stylesheet() -> None:
    application = _app()
    original = application.styleSheet()
    try:
        install_presentation_policy(application)
        application.setStyleSheet(original + "\nQPushButton:focus { border-style: dashed; }")
        style = application.style()
        install_presentation_policy(application)
        assert application.style() is style
        install_presentation_policy(application)
        assert application.style() is style
    finally:
        application.setStyleSheet(original)


def test_all_decorative_animation_effects_are_disabled() -> None:
    application = _app()
    install_presentation_policy(application)
    for effect in (
        Qt.UIEffect.UI_AnimateMenu,
        Qt.UIEffect.UI_FadeMenu,
        Qt.UIEffect.UI_AnimateCombo,
        Qt.UIEffect.UI_AnimateTooltip,
        Qt.UIEffect.UI_FadeTooltip,
        Qt.UIEffect.UI_AnimateToolBox,
    ):
        assert not application.isEffectEnabled(effect)
    for hint in (
        QStyle.StyleHint.SH_Widget_Animate,
        QStyle.StyleHint.SH_Widget_Animation_Duration,
        QStyle.StyleHint.SH_SpinBox_AnimateButton,
    ):
        assert application.style().styleHint(hint) == 0


def test_static_unknown_progress_replaces_previous_percentage_without_a_marquee() -> None:
    _app()
    progress = QProgressBar()
    progress.setRange(0, 100)
    progress.setValue(70)
    set_static_busy(progress)
    try:
        assert (progress.minimum(), progress.maximum()) != (0, 0)
        assert progress.text() == "Working…"
        set_static_busy(progress)
        assert progress.text() == "Working…"
        assert progress.value() == 0
    finally:
        progress.deleteLater()


@contextmanager
def _action_surface(
    scale: float,
) -> Iterator[tuple[QWidget, WrappingActionLayout, list[QPushButton]]]:
    application = _app()
    install_presentation_policy(application)
    surface = QWidget()
    font = QFont(application.font())
    font.setPointSizeF(font.pointSizeF() * scale)
    surface.setFont(font)
    layout = WrappingActionLayout()
    surface.setLayout(layout)
    buttons = [QPushButton(text) for text in ("Add", "Move earlier", "Remove", "Review", "Export")]
    for button in buttons:
        layout.addWidget(button)
    surface.show()
    application.processEvents()
    try:
        yield surface, layout, buttons
    finally:
        surface.hide()
        surface.deleteLater()
        application.processEvents()


@pytest.mark.parametrize("scale", (1.0, 1.5, 2.0))
def test_wrapping_actions_preserve_order_and_target_size_without_overlap(scale: float) -> None:
    with _action_surface(scale) as (_surface, layout, buttons):
        heights = []
        for width in (720, 360, 240):
            height = layout.heightForWidth(width)
            layout.setGeometry(QRect(0, 0, width, height))
            geometries = [button.geometry() for button in buttons]
            for index, rectangle in enumerate(geometries):
                assert rectangle.width() >= buttons[index].sizeHint().width()
                assert rectangle.height() >= buttons[index].sizeHint().height()
                assert rectangle.bottom() < height
                assert rectangle.x() >= 0 and rectangle.y() >= 0
                for other in geometries[index + 1 :]:
                    assert not rectangle.intersects(other)
            assert [(rect.y(), rect.x()) for rect in geometries] == sorted(
                (rect.y(), rect.x()) for rect in geometries
            )
            heights.append(height)
        assert heights == sorted(heights)
        assert heights[-1] > heights[0]


def test_hidden_actions_release_layout_space_and_reappear_in_the_same_order() -> None:
    with _action_surface(1.0) as (_surface, layout, buttons):
        width = 240
        full_height = layout.heightForWidth(width)
        for button in buttons[1:]:
            button.hide()
        collapsed_height = layout.heightForWidth(width)
        assert collapsed_height < full_height
        layout.setGeometry(QRect(0, 0, width, collapsed_height))
        assert buttons[0].geometry().topLeft().isNull()
        for button in buttons[1:]:
            button.show()
        assert layout.heightForWidth(width) == full_height
        assert layout.count() == len(buttons)
        for index, button in enumerate(buttons):
            item = layout.itemAt(index)
            assert item is not None and item.widget() is button


def test_pathologically_narrow_layout_keeps_every_action_and_full_text_width() -> None:
    with _action_surface(2.0) as (_surface, layout, buttons):
        height = layout.heightForWidth(1)
        layout.setGeometry(QRect(0, 0, 1, height))
        assert layout.count() == len(buttons)
        assert all(button.geometry().width() >= button.sizeHint().width() for button in buttons)
        assert all(button.geometry().x() == 0 for button in buttons)
        assert len({button.geometry().y() for button in buttons}) == len(buttons)
        assert buttons[-1].geometry().bottom() < height
        assert layout.itemAt(-1) is None
        assert layout.itemAt(layout.count()) is None
        assert layout.takeAt(layout.count()) is None

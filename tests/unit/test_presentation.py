"""Native presentation policy, motion and wrapping geometry contracts."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from typing import cast

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QFont, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from paperworkshell.ui.presentation import (
    WrappingActionLayout,
    adapt_dialog_button_box,
    install_presentation_policy,
    set_static_busy,
)
from tests.helpers.qt_layout import wait_for_geometry


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


def test_static_progress_reset_clears_old_completion_before_value_changes() -> None:
    _app()
    progress = QProgressBar()
    progress.setRange(0, 1)
    progress.setValue(1)
    progress.setFormat("Preview: complete — %v of %m")
    observed: list[str] = []
    progress.valueChanged.connect(lambda _value: observed.append(progress.text()))

    try:
        set_static_busy(progress)
        assert observed == ["Working…"]
    finally:
        progress.deleteLater()


def test_enlarged_footer_keeps_every_button_inside_its_allocated_space_after_resize() -> None:
    application = _app()
    dialog = QDialog()
    font = QFont(application.font())
    font.setPointSizeF(max(24, font.pointSizeF() * 2))
    dialog.setFont(font)
    layout = QVBoxLayout(dialog)
    heading = QLabel("Save the visible changes to the selected synthetic document.")
    heading.setWordWrap(True)
    layout.addWidget(heading)
    scroll = QScrollArea()
    scroll.setMinimumHeight(240)
    content = QLabel("Synthetic editable content\n" * 20)
    scroll.setWidget(content)
    scroll.setWidgetResizable(True)
    layout.addWidget(scroll, 1)
    footer = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
    )
    adapt_dialog_button_box(footer)
    layout.addWidget(footer)
    try:
        dialog.show()
        for width in (520, 360, 520):
            dialog.resize(width, 420)
            QTest.qWait(20)
            geometries = [button.geometry() for button in footer.buttons()]
            for index, rectangle in enumerate(geometries):
                assert footer.rect().contains(rectangle)
                assert dialog.rect().contains(footer.mapTo(dialog, rectangle.topLeft()))
                assert dialog.rect().contains(footer.mapTo(dialog, rectangle.bottomRight()))
                for other in geometries[index + 1 :]:
                    assert not rectangle.intersects(other)
    finally:
        dialog.close()
        dialog.deleteLater()
        application.processEvents()


def test_footer_fits_visible_labels_and_reflows_without_a_previous_row_width_lock() -> None:
    application = _app()
    previous_font = QFont(application.font())
    font = QFont(previous_font)
    font.setPointSize(24)
    dialog = QDialog()
    try:
        # A palette stylesheet can prevent a dialog-only font from propagating
        # to native child controls. Establish the intended application setting
        # and prove the actual footer/button fonts before assessing their fit.
        application.setFont(font)
        install_presentation_policy(application)
        dialog.setFont(font)
        layout = QVBoxLayout(dialog)
        footer = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        hidden = footer.addButton(
            "Synthetic hidden action with a deliberately very long label",
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        hidden.hide()
        adapt_dialog_button_box(footer)
        layout.addWidget(footer)

        def fits_requested_geometry(width: int, orientation: Qt.Orientation) -> bool:
            visible = [button for button in footer.buttons() if button.isVisible()]
            if (
                footer.font().pointSizeF() != 24
                or any(button.font().pointSizeF() != 24 for button in visible)
                or dialog.width() > width
                or footer.orientation() is not orientation
                or len(visible) != 2
            ):
                return False
            for index, button in enumerate(visible):
                if not footer.rect().contains(button.geometry()):
                    return False
                if (
                    button.width()
                    < max(
                        button.fontMetrics().horizontalAdvance(line.replace("&", ""))
                        for line in button.text().splitlines()
                    )
                    + 12
                ):
                    return False
                if any(
                    button.geometry().intersects(other.geometry()) for other in visible[index + 1 :]
                ):
                    return False
            return True

        dialog.show()
        dialog.resize(520, 220)
        wait_for_geometry(partial(fits_requested_geometry, 520, Qt.Orientation.Horizontal))
        visible_buttons = [button for button in footer.buttons() if button.isVisible()]
        footer_layout = footer.layout()
        assert footer_layout is not None
        native_button_widths = [
            max(
                button.minimumWidth(),
                button.minimumSizeHint().width(),
                button.sizeHint().width(),
                max(
                    button.fontMetrics().horizontalAdvance(line.replace("&", ""))
                    for line in button.text().splitlines()
                )
                + 12,
            )
            for button in visible_buttons
        ]
        parent_margins, footer_margins = layout.contentsMargins(), footer_layout.contentsMargins()
        layout_margin_width = (
            parent_margins.left()
            + parent_margins.right()
            + footer_margins.left()
            + footer_margins.right()
        )
        # Keep one intact native action feasible at the enlarged font. A 200px
        # dialog may be narrower than Cancel plus margins; stacking cannot make
        # that unbreakable label smaller. Still force a genuine row-to-stack
        # transition by staying below the complete horizontal row requirement.
        narrow_width = max(200, max(native_button_widths) + layout_margin_width)
        complete_horizontal_row_width = (
            sum(native_button_widths)
            + max(0, footer_layout.spacing()) * (len(native_button_widths) - 1)
            + layout_margin_width
        )
        assert narrow_width < complete_horizontal_row_width
        for width, orientation in (
            (520, Qt.Orientation.Horizontal),
            (narrow_width, Qt.Orientation.Vertical),
            (520, Qt.Orientation.Horizontal),
        ):
            dialog.resize(width, 220)
            wait_for_geometry(partial(fits_requested_geometry, width, orientation))
            assert footer.font().pointSizeF() == 24
            assert dialog.width() <= width
            assert footer.orientation() is orientation
            visible = [button for button in footer.buttons() if button.isVisible()]
            assert len(visible) == 2
            for index, button in enumerate(visible):
                assert button.font().pointSizeF() == 24
                assert footer.rect().contains(button.geometry())
                assert (
                    button.width()
                    >= max(
                        button.fontMetrics().horizontalAdvance(line.replace("&", ""))
                        for line in button.text().splitlines()
                    )
                    + 12
                )
                for other in visible[index + 1 :]:
                    assert not button.geometry().intersects(other.geometry())
    finally:
        dialog.close()
        dialog.deleteLater()
        application.setFont(previous_font)
        application.processEvents()


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

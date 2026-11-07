"""PWS-0703 rendered controls, enlarged-text reachability and contrast checks."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QWidget,
)

from paperworkshell.domain.page_coordinates import PageCoordinateTransform
from paperworkshell.ui.backend import UiProgress
from paperworkshell.ui.overlay_canvas import OverlayEditorCanvas
from paperworkshell.ui.presentation import AccessibleNativeStyle, install_presentation_policy
from paperworkshell.ui.redaction_canvas import RedactionIntentCanvas
from paperworkshell.ui.window import _LongOperationKind
from tests.accessibility.test_accessibility_baseline import _application, _state, _window


def _settle() -> None:
    # Focus reveal is coalesced with a zero-interval timer after layout settles.
    _application().processEvents()
    _application().processEvents()


@contextmanager
def _font_scale(scale: float) -> Iterator[None]:
    app = _application()
    original = QFont(app.font())
    enlarged = QFont(original)
    enlarged.setPointSizeF(original.pointSizeF() * scale)
    install_presentation_policy(app)
    app.setFont(enlarged)
    try:
        yield
    finally:
        app.setFont(original)
        _settle()


def _assert_revealed(control: QWidget) -> None:
    control.setFocus(Qt.FocusReason.OtherFocusReason)
    _settle()
    assert control.hasFocus(), control.objectName()
    ancestor = control.parentWidget()
    scroll_count = 0
    while ancestor is not None:
        if isinstance(ancestor, QScrollArea):
            viewport = ancestor.viewport()
            bounds = QRect(control.mapTo(viewport, control.rect().topLeft()), control.size())
            visible = viewport.rect().intersected(bounds)
            assert not visible.isEmpty(), control.objectName()
            # A normal target must fit completely. A wider text surface remains
            # scrollable, with at least a viewport's worth available for review.
            assert visible.width() >= min(bounds.width(), viewport.width()) - 16, (
                control.objectName(),
                bounds,
                viewport.rect(),
            )
            assert visible.height() >= min(bounds.height(), viewport.height()) - 16, (
                control.objectName(),
                bounds,
                viewport.rect(),
            )
            scroll_count += 1
        ancestor = ancestor.parentWidget()
    if scroll_count == 0:
        # Progress/recovery is pinned outside the scrolling workflow.
        # Require the entire fixed control to fit its actual window.
        owner = control.window()
        bounds = QRect(control.mapTo(owner, control.rect().topLeft()), control.size())
        assert owner.rect().contains(bounds), (control.objectName(), bounds, owner.rect())


@pytest.mark.parametrize("scale", (1.0, 1.5, 2.0))
@pytest.mark.parametrize("page", ("chooser", "organizer"))
def test_primary_actions_and_recovery_remain_reachable_with_enlarged_text(
    scale: float, page: str
) -> None:
    with _font_scale(scale), _window() as (window, _commands, worker):
        worker.initialized.emit()
        if page == "organizer":
            worker.state_changed.emit(_state())
        window.resize(800, 560)
        window.activateWindow()
        window._error_label.setText("Synthetic save failure. Retry saving changes.")
        window._error_label.setVisible(True)
        window._retry_save_button.setVisible(True)
        window._retry_save_button.setEnabled(True)
        _settle()
        controls = [
            button
            for button in window.findChildren(QPushButton)
            if button.isVisible() and button.isEnabled()
        ]
        assert len(controls) >= (3 if page == "chooser" else 8)
        for button in controls:
            assert button.width() >= button.sizeHint().width(), button.objectName()
            assert button.height() >= button.sizeHint().height(), button.objectName()
            _assert_revealed(button)
        _assert_revealed(window._error_label)
        assert "Retry saving changes" in window._error_label.text()


@pytest.mark.parametrize("scale", (1.0, 2.0))
def test_native_controls_use_large_targets_without_clipping_enlarged_text(scale: float) -> None:
    with _font_scale(scale):
        controls: tuple[QWidget, ...] = (
            QPushButton("Save reviewed changes"),
            QComboBox(),
            QLineEdit("Synthetic text"),
            QSpinBox(),
        )
        combo = controls[1]
        assert isinstance(combo, QComboBox)
        combo.addItem("Keep the reviewed page order")
        try:
            for control in controls:
                control.adjustSize()
                control.show()
            _settle()
            for control in controls:
                assert control.width() >= 32
                assert control.height() >= 32
                assert control.height() >= control.fontMetrics().height() + 4
        finally:
            for control in controls:
                control.hide()
                control.deleteLater()
            _settle()


def _contrast_palette(dark: bool) -> QPalette:
    palette = QPalette(_application().palette())
    background = QColor("black" if dark else "white")
    foreground = QColor("white" if dark else "black")
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        for role in (
            QPalette.ColorRole.Window,
            QPalette.ColorRole.Base,
            QPalette.ColorRole.Button,
        ):
            palette.setColor(group, role, background)
        for role in (
            QPalette.ColorRole.WindowText,
            QPalette.ColorRole.Text,
            QPalette.ColorRole.ButtonText,
        ):
            palette.setColor(group, role, foreground)
        palette.setColor(group, QPalette.ColorRole.Highlight, QColor("yellow" if dark else "blue"))
        palette.setColor(group, QPalette.ColorRole.HighlightedText, background)
    return palette


@pytest.mark.parametrize("dark", (True, False))
def test_status_and_keyboard_focus_follow_high_contrast_palette(dark: bool) -> None:
    app = _application()
    install_presentation_policy(app)
    original = QPalette(app.palette())
    requested = _contrast_palette(dark)
    app.setPalette(requested)
    try:
        with _window() as (window, _commands, worker):
            worker.initialized.emit()
            window.resize(800, 560)
            window.activateWindow()
            status = window._status_label
            status.setText("Synthetic operation complete. Review the result.")
            status.setFocus(Qt.FocusReason.OtherFocusReason)
            _settle()
            assert status.hasFocus()
            assert status.palette().color(QPalette.ColorRole.WindowText) == requested.color(
                QPalette.ColorRole.WindowText
            )
            focused = status.grab().toImage()
            highlight = requested.color(QPalette.ColorRole.Highlight).rgba()
            assert any(
                focused.pixel(x, y) == highlight
                for x in range(focused.width())
                for y in range(min(3, focused.height()))
            ), "The focused status needs a visible native-palette border."
            window._create_case_button.setFocus()
            _settle()
            unfocused = status.grab().toImage()
            assert focused != unfocused
            assert status.text().startswith("Synthetic operation complete.")
    finally:
        app.setPalette(original)
        _settle()


def test_existing_menus_and_buttons_follow_dark_then_light_palette_changes() -> None:
    app = _application()
    original_palette, original_font = QPalette(app.palette()), QFont(app.font())
    try:
        with _window() as (window, _commands, worker):
            worker.initialized.emit()
            menu_bar = window.menuBar()
            button = window._create_case_button
            original_style = app.style()
            original_button_font = QFont(button.font())
            original_menu_font = QFont(menu_bar.font())
            style_count = len(app.findChildren(AccessibleNativeStyle))
            assert style_count == 1
            assert menu_bar.isVisible() and button.isVisible()
            window.activateWindow()
            button.setFocus(Qt.FocusReason.OtherFocusReason)
            _settle()
            assert button.hasFocus()
            assert button.width() >= 32 and button.height() >= 32
            # Both surfaces exist before the change, as they do when Windows
            # switches contrast themes while PaperWorkShell remains open.
            for dark in (True, False):
                requested = _contrast_palette(dark)
                app.setPalette(requested)
                _settle()
                foreground = requested.color(QPalette.ColorRole.WindowText)
                background = requested.color(QPalette.ColorRole.Window)
                assert menu_bar.palette().color(QPalette.ColorRole.WindowText) == foreground
                assert menu_bar.palette().color(QPalette.ColorRole.Window) == background
                assert button.palette().color(QPalette.ColorRole.ButtonText) == foreground
                assert button.palette().color(QPalette.ColorRole.Button) == background
                assert button.hasFocus()
                assert button.width() >= 32 and button.height() >= 32
                # Inspect actual menu pixels as well as palette roles; a palette
                # change must not leave the old cached stylesheet painting.
                image = menu_bar.grab().toImage()
                pixels = (
                    image.pixelColor(x, y)
                    for y in range(image.height())
                    for x in range(image.width())
                )
                assert any(color == foreground for color in pixels)
                assert image.pixelColor(image.width() - 4, image.height() // 2) == background
                assert app.font() == original_font
                assert button.font() == original_button_font
                assert menu_bar.font() == original_menu_font
                assert app.style() is original_style
                assert len(app.findChildren(AccessibleNativeStyle)) == style_count
    finally:
        app.setPalette(original_palette)
        _settle()


def test_checkbox_indicator_has_an_enlarged_clickable_target() -> None:
    app = _application()
    install_presentation_policy(app)
    checkbox = QCheckBox("I reviewed this synthetic result")
    checkbox.adjustSize()
    checkbox.show()
    try:
        _settle()
        assert checkbox.height() >= 24
        # A hit near the bottom of the enlarged indicator must toggle the box;
        # merely painting a larger decoration would leave a small pointer target.
        QTest.mouseClick(checkbox, Qt.MouseButton.LeftButton, pos=QPoint(12, 22))
        assert checkbox.isChecked()
    finally:
        checkbox.hide()
        checkbox.deleteLater()
        _settle()


def test_progress_changes_from_static_starting_state_to_current_numeric_values() -> None:
    with _window() as (window, _commands, worker):
        worker.initialized.emit()
        worker.state_changed.emit(_state())
        operation = window._begin_long_operation(_LongOperationKind.IMPORT)
        worker.operation_started.emit(operation.operation_id, "Import")
        _settle()
        assert (window._progress.minimum(), window._progress.maximum()) != (0, 0)
        assert "starting" in window._progress.text()
        worker.progress_changed.emit(
            UiProgress("Import", "copying", 3, 8, True, operation.operation_id)
        )
        _settle()
        assert window._progress.maximum() == 8 and window._progress.value() == 3
        assert "3 of 8" in window._progress.text()
        assert "starting" not in window._progress.text()
        assert window._cancel_button.isVisible() and window._cancel_button.isEnabled()
        worker.operation_finished.emit(operation.operation_id)
        _settle()
        assert not window._progress.isVisible()


def test_missing_overlay_preview_guidance_is_readable_on_the_white_document_surface() -> None:
    _application()
    canvas = OverlayEditorCanvas(transform=PageCoordinateTransform())
    canvas.set_guide_options(visible=False, snap=False, step_millionths=50_000)
    canvas.setPalette(_contrast_palette(True))
    canvas.show()
    try:
        _settle()
        page = canvas._page_rect().adjusted(10, 10, -10, -10).toRect()
        image = canvas.grab().toImage().copy(page)
        assert any(
            image.pixelColor(x, y).lightness() < 64
            for y in range(image.height())
            for x in range(image.width())
        ), "Missing-preview guidance must remain visible in a dark contrast theme."
    finally:
        canvas.hide()
        canvas.deleteLater()
        _settle()


def test_removal_preview_legend_follows_the_dark_contrast_window_text_color() -> None:
    _application()
    canvas = RedactionIntentCanvas(transform=PageCoordinateTransform())
    canvas.setPalette(_contrast_palette(True))
    canvas.resize(520, 680)
    canvas.show()
    try:
        _settle()
        image = canvas.grab().toImage().copy(4, 0, canvas.width() - 8, canvas._legend_height())
        assert any(
            image.pixelColor(x, y).lightness() > 192
            for y in range(image.height())
            for x in range(image.width())
        ), "The legend must not become black text on a black contrast-theme background."
    finally:
        canvas.hide()
        canvas.deleteLater()
        _settle()

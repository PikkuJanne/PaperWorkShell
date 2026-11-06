"""PWS-0802 first-show geometry, active-page sizing and startup focus."""

from __future__ import annotations

from typing import cast

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QScreen
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QWidget

from paperworkshell.app.organizer import OrganizerViewState
from paperworkshell.domain.case_lifecycle import CaseIndex
from paperworkshell.ui.window import PaperWorkShellWindow
from tests.accessibility.test_accessibility_baseline import _application, _state, _window
from tests.accessibility.test_display_preferences import _assert_revealed
from tests.accessibility.test_localization_layout import _display_locale, _settle
from tests.accessibility.test_selected_row_focus_reveal import _populate_cases


def _bounds_in_viewport(window: PaperWorkShellWindow, widget: QWidget) -> QRect:
    viewport = window._application_scroll.viewport()
    return QRect(widget.mapTo(viewport, QPoint()), widget.size())


def test_first_show_exposes_heading_and_create_without_test_resize() -> None:
    with _display_locale("en", 9), _window() as (window, _commands, worker):
        window.activateWindow()
        worker.initialized.emit()
        worker.cases_changed.emit(CaseIndex())
        _settle()

        heading = window.findChild(QLabel, "caseChooserHeading")
        assert heading is not None
        viewport = window._application_scroll.viewport()
        assert viewport.rect().contains(_bounds_in_viewport(window, heading))
        assert viewport.rect().contains(_bounds_in_viewport(window, window._create_case_button))
        assert window._organizer_page.parentWidget() is window._stack
        assert not window._organizer_page.isVisible()


def test_populated_startup_and_refresh_preserve_list_and_action_focus() -> None:
    with _display_locale("en", 9), _window() as (window, _commands, worker):
        window.activateWindow()
        worker.initialized.emit()
        worker.cases_changed.emit(_populate_cases())
        _settle()
        assert window._case_list.hasFocus()
        assert window._case_list.currentRow() == 0

        QTest.keyClick(window._case_list, Qt.Key.Key_Tab)
        _settle()
        assert window._create_case_button.hasFocus()
        QTest.keyClick(window._create_case_button, Qt.Key.Key_Backtab)
        _settle()
        assert window._case_list.hasFocus()

        window._refresh_cases_button.setFocus(Qt.FocusReason.OtherFocusReason)
        worker.cases_changed.emit(_populate_cases())
        _settle()
        assert window._refresh_cases_button.hasFocus()
        worker.cases_changed.emit(CaseIndex())
        _settle()
        assert window._refresh_cases_button.hasFocus()


def test_page_round_trip_preserves_widgets_parentage_and_user_size() -> None:
    with _display_locale("en", 9), _window() as (window, _commands, worker):
        worker.initialized.emit()
        chooser, organizer = window._welcome_page, window._organizer_page
        original_size = window.size()
        for _ in range(2):
            worker.state_changed.emit(_state())
            _settle()
            assert window._stack.currentWidget() is organizer
            assert window._stack.count() == 1
            assert chooser.parentWidget() is window._stack
            assert not chooser.isVisible()
            worker.state_changed.emit(OrganizerViewState.empty())
            _settle()
            assert window._stack.currentWidget() is chooser
            assert window._stack.count() == 1
            assert organizer.parentWidget() is window._stack
            assert not organizer.isVisible()
            assert window.size() == original_size


class _SmallScreen:
    def availableGeometry(self) -> QRect:  # noqa: N802 - Qt API test double
        return QRect(0, 0, 640, 480)


@pytest.mark.parametrize("locale", ("en", "en-XA", "ar-XB"))
@pytest.mark.parametrize("point_size", (9, 18))
def test_small_screen_startup_keeps_empty_index_action_reachable(
    monkeypatch: pytest.MonkeyPatch, locale: str, point_size: int
) -> None:
    def screen(_window: PaperWorkShellWindow) -> QScreen:
        return cast(QScreen, _SmallScreen())

    monkeypatch.setattr(PaperWorkShellWindow, "screen", screen)
    with _display_locale(locale, point_size), _window() as (window, _commands, worker):
        window.activateWindow()
        worker.initialized.emit()
        worker.cases_changed.emit(CaseIndex())
        _settle()
        assert window.width() <= 640
        assert window.height() <= 480
        assert window._create_case_button.hasFocus()
        _assert_revealed(window._create_case_button)
        focus = _application().focusWidget()
        assert focus is window._create_case_button

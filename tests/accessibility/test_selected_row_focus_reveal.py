"""A selected row stays visible when its item view exceeds the outer viewport."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView

from paperworkshell.domain.case_lifecycle import CaseIndex, CaseSummary
from paperworkshell.ui.window import PaperWorkShellWindow
from tests.accessibility.test_accessibility_baseline import _application, _window
from tests.accessibility.test_display_preferences import _assert_revealed
from tests.accessibility.test_localization_layout import _display_locale, _settle


def _populate_cases() -> CaseIndex:
    now = datetime(2026, 11, 2, 12, tzinfo=UTC)
    return CaseIndex(
        tuple(
            CaseSummary(UUID(f"77000000-0000-4000-8000-{index + 1:012d}"), 3, 0, now, now)
            for index in range(40)
        )
    )


def _assert_selected_row_visible(window: PaperWorkShellWindow, view: QAbstractItemView) -> None:
    current = view.currentIndex()
    assert current.isValid()
    row = view.visualRect(current)
    assert not row.isEmpty()
    assert view.viewport().rect().contains(row), ("inner viewport", row, view.viewport().rect())
    outer = window._application_scroll.viewport()
    mapped = QRect(view.viewport().mapTo(outer, row.topLeft()), row.size())
    assert outer.rect().contains(mapped), ("outer viewport", mapped, outer.rect())


@pytest.mark.parametrize("locale", ("en-XA", "ar-XB"))
@pytest.mark.parametrize("point_size", (9, 18))
@pytest.mark.parametrize("route", ("focus", "recovery", "selection"))
def test_selected_row_is_revealed_in_oversized_view(
    locale: str, point_size: int, route: str
) -> None:
    with _display_locale(locale, point_size), _window() as (window, _commands, worker):
        worker.initialized.emit()
        view = window._case_list
        other = window._create_case_button
        # Reproduce the native 742-logical-pixel item view inside a roughly
        # 469-pixel outer viewport, independent of the offscreen style's hint.
        view.setFixedHeight(742)
        window.resize(900, 506)
        window.activateWindow()
        _settle()
        assert view.height() > window._application_scroll.viewport().height()
        other.setFocus(Qt.FocusReason.OtherFocusReason)
        _settle()
        if route != "focus":
            worker.cases_changed.emit(_populate_cases())
            _settle()

        if route == "recovery":
            # Exercise the explicit recovery reveal independently of the
            # focus-change timer; a queued focus reveal must not mask it.
            window._application_scroll.verticalScrollBar().setValue(
                window._application_scroll.verticalScrollBar().maximum()
            )
            window._ensure_widget_visible(view)
            _settle()
            _assert_selected_row_visible(window, view)
        else:
            view.setFocus(Qt.FocusReason.OtherFocusReason)
            _settle()
            assert view.hasFocus()
            if route == "focus":
                # Native startup focuses the empty list before the asynchronous
                # index arrives. Populating it must reveal the new current row
                # even though the focus itself has not changed.
                worker.cases_changed.emit(_populate_cases())
                _settle()
                _assert_selected_row_visible(window, view)
                QTest.keyClick(view, Qt.Key.Key_Tab)
                _settle()
                focused = _application().focusWidget()
                assert focused is not None
                QTest.keyClick(focused, Qt.Key.Key_Backtab)
                _settle()
                assert view.hasFocus()
                _assert_selected_row_visible(window, view)
            else:
                # Navigation changes the current row without changing focus.
                # Both the view's own scroll and its outer scroll must follow.
                for key, expected in (
                    (Qt.Key.Key_End, view.model().rowCount() - 1),
                    (Qt.Key.Key_Home, 0),
                    (Qt.Key.Key_Down, 1),
                ):
                    QTest.keyClick(view, key)
                    _settle()
                    assert view.hasFocus()
                    assert view.currentIndex().row() == expected
                    _assert_selected_row_visible(window, view)


@pytest.mark.parametrize("locale", ("en-XA", "ar-XB"))
@pytest.mark.parametrize("state", ("empty", "invalid_current"))
def test_item_view_without_a_current_row_keeps_widget_focus_visible(
    locale: str, state: str
) -> None:
    with _display_locale(locale, 18), _window() as (window, _commands, worker):
        worker.initialized.emit()
        view = window._case_list
        view.setFixedHeight(742)
        window.resize(900, 506)
        window.activateWindow()
        if state == "invalid_current":
            worker.cases_changed.emit(_populate_cases())
        view.setFocus(Qt.FocusReason.OtherFocusReason)
        _settle()
        view.selectionModel().clearCurrentIndex()
        _settle()
        assert not view.currentIndex().isValid()
        window._ensure_widget_visible(view)
        _settle()
        _assert_revealed(view)
        assert not view.currentIndex().isValid()

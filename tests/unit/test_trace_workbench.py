"""Native TRACE regression checks at the command and presentation boundaries."""

from __future__ import annotations

from typing import cast
from uuid import UUID, uuid4

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget

from paperworkshell.app.organizer import OrganizerPageView, OrganizerViewState
from paperworkshell.ui.backend import OrganizerBackendCommands, OrganizerBackendWorker
from paperworkshell.ui.trace_workbench import TraceCommandPalette
from paperworkshell.ui.window import PaperWorkShellWindow


def test_palette_and_visible_rotation_share_the_selected_page_command() -> None:
    application = cast(QApplication, QApplication.instance() or QApplication([]))
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(lambda: (_ for _ in ()).throw(AssertionError("no backend")))
    window = PaperWorkShellWindow(commands, worker)
    rotated: list[UUID] = []
    commands.rotate_requested.connect(rotated.append)
    first, second = uuid4(), uuid4()
    pages = (OrganizerPageView(first, 1, 0, True), OrganizerPageView(second, 2, 0, True))
    window._state_changed(OrganizerViewState(uuid4(), 1, pages, False, False, False))
    window._set_controls_enabled(True)
    window.show()
    application.processEvents()
    try:
        window._restore_page_selection(second)
        window._rotate_button.click()

        def select_rotation() -> None:
            palette = window.findChild(TraceCommandPalette)
            assert palette is not None
            search = palette.findChild(QLineEdit)
            assert search is not None
            search.setText("rotate clockwise")
            QTest.keyClick(search, Qt.Key.Key_Return)

        QTimer.singleShot(0, select_rotation)
        window._show_command_palette()
        assert rotated == [second, second]
    finally:
        window._allow_close = True
        window.close()
        window.deleteLater()
        application.processEvents()


def test_narrow_workbench_keeps_document_pages_and_recipe_reachable() -> None:
    application = cast(QApplication, QApplication.instance() or QApplication([]))
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(lambda: (_ for _ in ()).throw(AssertionError("no backend")))
    window = PaperWorkShellWindow(commands, worker)
    page = OrganizerPageView(uuid4(), 1, 0, True)
    window._state_changed(OrganizerViewState(uuid4(), 1, (page,), False, False, False))
    window.resize(620, 560)
    window.show()
    application.processEvents()
    try:
        assert window._trace_document.isVisible()
        assert not window._trace_inspector_tabs.isVisible()
        pages_control = window.findChild(QWidget, "tracePane-pages")
        assert pages_control is not None and pages_control.isVisible()
        window._trace_select_pane("pages")
        assert window._page_view.isVisible()
        window._trace_select_pane("inspector")
        assert window._trace_recipe_pane.isVisible()
        window._trace_select_pane("document")
        assert window._trace_document.isVisible()
        assert window._selected_page_id() == page.page_id
    finally:
        window._allow_close = True
        window.close()
        window.deleteLater()
        application.processEvents()

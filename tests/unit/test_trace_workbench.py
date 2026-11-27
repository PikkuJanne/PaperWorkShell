"""Native TRACE regression checks at the command and presentation boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QLineEdit, QWidget

from paperworkshell.app.organizer import OrganizerPageView, OrganizerViewState
from paperworkshell.app.trace_results import (
    TraceCheck,
    TraceCheckStatus,
    TraceSavedExportEvidence,
    TraceSavedExportSelection,
)
from paperworkshell.domain.trace_recipe import TraceOutputStatus
from paperworkshell.ui.backend import (
    OperationCancellation,
    OrganizerBackendCommands,
    OrganizerBackendWorker,
)
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


def test_compact_document_shows_page_count_and_navigation_before_scrolling() -> None:
    application = cast(QApplication, QApplication.instance() or QApplication([]))
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(lambda: (_ for _ in ()).throw(AssertionError("no backend")))
    window = PaperWorkShellWindow(commands, worker)
    case_id = uuid4()
    pages = tuple(
        OrganizerPageView(
            uuid4(), position, 0, True, document_number=position, source_page_number=2
        )
        for position in range(1, 7)
    )
    window.resize(620, 560)
    window._state_changed(OrganizerViewState(case_id, 1, pages, False, False, False))
    window._set_controls_enabled(True)
    window.show()
    window.activateWindow()
    application.processEvents()
    try:
        viewport = window._application_scroll.viewport()
        previous = window._trace_previous_button
        following = window._trace_next_button
        for control in (previous, following):
            visible_bounds = QRect(control.mapTo(viewport, QPoint()), control.size())
            assert viewport.rect().contains(visible_bounds)

        position = window._trace_page_position
        assert position.text() == "Page 1 of 6"
        assert viewport.rect().contains(QRect(position.mapTo(viewport, QPoint()), position.size()))
        assert not previous.isEnabled() and following.isEnabled()

        following.setFocus()
        QTest.keyClick(following, Qt.Key.Key_Space)
        assert window._selected_page_id() == pages[1].page_id
        assert position.text() == "Page 2 of 6"
        assert previous.isEnabled() and following.isEnabled()

        QTest.keyClick(window._trace_lens_buttons["source"], Qt.Key.Key_Space)
        assert position.text() == "Page 2 of 6"
        assert "Source page 2 · Assembled page 2" in window._trace_document.accessibleDescription()
        window._restore_page_selection(pages[-2].page_id)
        following.setFocus()
        QTest.keyClick(following, Qt.Key.Key_Space)
        assert window._selected_page_id() == pages[-1].page_id
        assert position.text() == "Page 6 of 6"
        assert not following.isEnabled() and previous.hasFocus()
        QTest.keyClick(previous, Qt.Key.Key_Space)
        assert window._selected_page_id() == pages[-2].page_id

        QTest.keyClick(window._trace_lens_buttons["exported"], Qt.Key.Key_Space)
        assert not position.isVisible()
        assert not previous.isVisible() and not following.isVisible()
        QTest.keyClick(window._trace_lens_buttons["planned"], Qt.Key.Key_Space)
        assert position.isVisible() and position.text() == "Page 5 of 6"

        window._state_changed(OrganizerViewState(case_id, 2, (), False, False, False))
        assert not position.isVisible()
        assert not previous.isEnabled() and not following.isEnabled()
    finally:
        window._allow_close = True
        window.close()
        window.deleteLater()
        application.processEvents()


def test_saved_export_requires_explicit_reselection_and_rechecks_before_opening(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    application = cast(QApplication, QApplication.instance() or QApplication([]))
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(lambda: (_ for _ in ()).throw(AssertionError("no backend")))
    window = PaperWorkShellWindow(commands, worker)
    case_id = uuid4()
    page = OrganizerPageView(uuid4(), 1, 0, False)
    saved = TraceSavedExportEvidence(
        case_id,
        uuid4(),
        4,
        "a" * 64,
        100,
        1,
        (TraceCheck("PDF_REOPENED", TraceCheckStatus.PASSED, "The PDF was reopened."),),
    )
    reopened = OrganizerViewState(
        case_id, 5, (page,), False, False, False, trace_saved_export=saved
    )
    checks: list[tuple[int, TraceSavedExportSelection, OperationCancellation]] = []
    commands.trace_export_inspect_requested.connect(lambda *args: checks.append(args))
    chosen = tmp_path / "Relocated synthetic PDF.pdf"
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_a, **_kw: (str(chosen), ""))
    opened: list[Path] = []
    monkeypatch.setattr(window, "_open_export_path", opened.append)
    window._state_changed(reopened)
    window.show()
    application.processEvents()
    try:
        exported = window._trace_lens_buttons["exported"]
        choose = window._trace_choose_pdf_button
        open_pdf = window._trace_open_pdf_button
        assert window._selection_summary.isVisible()
        QTest.keyClick(exported, Qt.Key.Key_Space)
        initial = window._trace_document.accessibleDescription()
        assert "Saved PDF export" in initial
        assert "SHA-256" in initial
        assert not window._selection_summary.isVisible()
        assert choose.isVisible() and not open_pdf.isVisible() and not checks
        pane = window._trace_recipe_pane
        assert "Saved export checks" in pane._result_status.text()
        assert [pane._checks.item(i).text() for i in range(pane._checks.count())] == [
            "Passed · The PDF was reopened."
        ]

        for status, expected in (
            (TraceOutputStatus.CHANGED, "does not match"),
            (TraceOutputStatus.MISSING, "could not be found"),
        ):
            QTest.keyClick(choose, Qt.Key.Key_Space)
            operation_id, target, _token = checks[-1]
            assert target == TraceSavedExportSelection(saved, chosen)
            worker.trace_export_inspected.emit(operation_id, target, status)
            assert expected in window._trace_document.accessibleDescription()
            assert not open_pdf.isVisible() and not opened

        QTest.keyClick(choose, Qt.Key.Key_Space)
        operation_id, target, token = checks[-1]
        assert window._trace_cancel_check_button.isVisible()
        QTest.keyClick(window._trace_cancel_check_button, Qt.Key.Key_Space)
        assert token.cancelled()
        worker.trace_export_inspected.emit(operation_id, target, TraceOutputStatus.PRESENT)
        assert not open_pdf.isVisible()

        QTest.keyClick(choose, Qt.Key.Key_Space)
        operation_id, target, _token = checks[-1]
        worker.trace_export_inspected.emit(operation_id, target, TraceOutputStatus.PRESENT)
        verified = window._trace_document.accessibleDescription()
        assert "matches the saved export by size and SHA-256" in verified
        assert "output page" not in verified and "Out of date" not in verified
        assert open_pdf.isVisible() and not opened

        QTest.keyClick(open_pdf, Qt.Key.Key_Space)
        open_operation_id, open_target, _token = checks[-1]
        assert open_operation_id != operation_id and open_target == target
        assert not opened
        worker.trace_export_inspected.emit(
            open_operation_id, open_target, TraceOutputStatus.PRESENT
        )
        assert opened == [chosen]

        QTest.keyClick(open_pdf, Qt.Key.Key_Space)
        late_id, late_target, late_token = checks[-1]
        window._state_changed(OrganizerViewState(None, None, (), False, False, False))
        assert late_token.cancelled()
        window._state_changed(reopened)
        QTest.keyClick(exported, Qt.Key.Key_Space)
        worker.trace_export_inspected.emit(late_id, late_target, TraceOutputStatus.PRESENT)
        assert opened == [chosen]
        assert window._trace_document.accessibleDescription() == initial
        assert choose.isVisible() and not open_pdf.isVisible()
        QTest.keyClick(window._trace_lens_buttons["planned"], Qt.Key.Key_Space)
        assert window._selection_summary.isVisible()
    finally:
        window._allow_close = True
        window.close()
        window.deleteLater()
        application.processEvents()

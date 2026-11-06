"""Keyboard, semantics, focus, and scaling checks for page-quality review."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea

from paperworkshell.app.organizer import OrganizerBackend, OrganizerPageView, OrganizerViewState
from paperworkshell.app.quality_analysis import QualityReviewSnapshot
from paperworkshell.domain.quality_analysis import (
    QualityEvidenceKind,
    QualityFinding,
    QualityFindingCode,
)
from paperworkshell.ui.backend import (
    OrganizerBackendCommands,
    OrganizerBackendWorker,
    UiProgress,
)
from paperworkshell.ui.window import PaperWorkShellWindow

CASE_ID = UUID("8f300000-0000-4000-8000-000000000001")
PAGE_ONE = UUID("8f300000-0000-4000-8000-000000000002")
PAGE_TWO = UUID("8f300000-0000-4000-8000-000000000003")


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def _unused_backend_factory() -> OrganizerBackend:
    raise AssertionError("accessibility tests do not initialize the backend")


@contextmanager
def _window() -> Iterator[
    tuple[PaperWorkShellWindow, OrganizerBackendCommands, OrganizerBackendWorker]
]:
    app = _application()
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(_unused_backend_factory)
    commands.connect_worker(worker)
    commands.quality_review_requested.disconnect(worker.analyze_quality)
    window = PaperWorkShellWindow(commands, worker)
    window.show()
    worker.initialized.emit()
    worker.state_changed.emit(_state())
    app.processEvents()
    try:
        yield window, commands, worker
    finally:
        window._allow_close = True
        window.close()
        window.deleteLater()
        app.processEvents()


def _state() -> OrganizerViewState:
    return OrganizerViewState(
        CASE_ID,
        11,
        (
            OrganizerPageView(PAGE_ONE, 1, 0, False),
            OrganizerPageView(PAGE_TWO, 2, 0, False),
        ),
        False,
        False,
        False,
    )


def _snapshot() -> QualityReviewSnapshot:
    finding = QualityFinding(
        PAGE_TWO,
        2,
        QualityFindingCode.LIKELY_BLUR,
        QualityEvidenceKind.LAPLACIAN_MEAN_SQUARE,
        10,
        20,
    )
    return QualityReviewSnapshot(
        CASE_ID,
        11,
        (PAGE_ONE, PAGE_TWO),
        (finding,),
        "scan-quality-v1",
        "a" * 64,
    )


def test_quality_review_semantics_are_textual_virtual_and_non_completing() -> None:
    with _window() as (window, _commands, _worker):
        summary = window._quality_summary
        findings = window._quality_findings_view
        check = window._quality_check_button
        navigate = window._quality_go_to_page_button

        assert summary.text() in summary.accessibleName()
        assert summary.wordWrap()
        assert summary.focusPolicy() is Qt.FocusPolicy.StrongFocus
        assert "Not checked for this saved revision" in summary.text()
        assert "suggestions" in summary.text()
        assert findings.accessibleName() == "Page quality findings"
        assert "page-quality review" in window._cancel_button.accessibleDescription()
        assert "navigate without confirming or completing" in findings.accessibleDescription()
        assert not findings.dragEnabled()
        assert not findings.acceptDrops()
        assert findings.indexWidget(findings.model().index(0, 0)) is None
        assert "never complete a requirement" in check.accessibleDescription()
        assert navigate.accessibleDescription()


def test_shortcut_progress_result_and_keyboard_navigation_have_truthful_text_and_focus() -> None:
    app = _application()
    with _window() as (window, commands, worker):
        requests: list[tuple[object, ...]] = []
        commands.quality_review_requested.connect(lambda *values: requests.append(values))
        window._quality_check_button.setFocus()
        app.processEvents()

        QTest.keyClick(
            window,
            Qt.Key.Key_Q,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
        )
        app.processEvents()

        assert len(requests) == 1
        operation_id = cast(int, requests[0][0])
        generation = cast(int, requests[0][1])
        worker.progress_changed.emit(
            UiProgress("Page quality check", "analyzing", 1, 2, True, operation_id)
        )
        app.processEvents()
        assert "analyzing: 1 of 2" in window._quality_summary.text()
        assert "suggestions" in window._quality_summary.text()
        assert "Page quality check analyzing: 1 of 2" in window._status_label.text()

        worker.quality_review_ready.emit(operation_id, generation, _snapshot())
        worker.operation_status_changed.emit(
            operation_id,
            "Page quality check finished. Review every page yourself before export.",
        )
        worker.operation_finished.emit(operation_id)
        app.processEvents()

        assert "Needs review" in window._quality_summary.text()
        assert "do not complete a requirement" in window._quality_summary.text()
        assert app.focusWidget() is window._quality_findings_view
        QTest.keyClick(window._quality_findings_view, Qt.Key.Key_Return)
        app.processEvents()
        assert window._selected_page_id() == PAGE_TWO
        assert app.focusWidget() is window._page_view
        assert window._status_label.text() == "Showing page 2. Needs review: Likely blur."


def test_doubled_text_keeps_every_quality_review_control_scroll_reachable() -> None:
    app = _application()
    original_font = app.font()
    enlarged = app.font()
    enlarged.setPointSizeF(max(18.0, original_font.pointSizeF() * 2.0))
    app.setFont(enlarged)
    try:
        with _window() as (window, _commands, _worker):
            window.resize(960, 540)
            app.processEvents()
            scroll = window.findChild(QScrollArea, "applicationScrollArea")
            assert scroll is not None and scroll.widgetResizable()
            controls = (
                window._quality_summary,
                window._quality_check_button,
                window._quality_findings_view,
                window._quality_go_to_page_button,
            )
            assert scroll.verticalScrollBar().maximum() > 0
            for control in controls:
                assert control.isVisible()
                scroll.ensureWidgetVisible(control, 8, 8)
                app.processEvents()
                origin = control.mapTo(scroll.viewport(), QPoint(0, 0))
                assert QRect(origin, control.size()).intersects(scroll.viewport().rect())
            assert window._quality_summary.height() > window._quality_summary.fontMetrics().height()
            assert window._quality_check_button.maximumHeight() >= 16_777_215
    finally:
        app.setFont(original_font)

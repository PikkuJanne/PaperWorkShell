"""Threaded package inspection keeps keyboard cancellation and recovery responsive."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event
from typing import cast
from uuid import UUID

import pytest
from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtGui import QAccessible, QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QPushButton

from paperworkshell.app.organizer import OrganizerBackend, OrganizerPageView, OrganizerViewState
from paperworkshell.app.package_workflow import PackageReviewState
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.pdf_inspection import (
    PdfInspectionError,
    PdfInspectionErrorCode,
    PdfInspectionResult,
)
from paperworkshell.preflight.portal_constraints import PortalOutputMode
from paperworkshell.ui.backend import OrganizerBackendCommands, OrganizerBackendWorker
from paperworkshell.ui.window import PaperWorkShellWindow
from tests.helpers.package_pilot import PAGE_IDS
from tests.unit.test_package_export_backend_bridge import _Backend
from tests.unit.test_package_export_window import _application
from tests.unit.test_package_review_inspection import Inspector, _service


def _wait_for(predicate: Callable[[], bool]) -> None:
    for _ in range(500):
        _application().processEvents()
        if predicate():
            return
        QTest.qWait(10)
    raise AssertionError("The bounded package-review UI wait did not complete.")


class SlowInspector(Inspector):
    def __init__(self) -> None:
        super().__init__()
        self.block = Event()
        self.entered = Event()
        self.release = Event()
        self.on_gui_thread: bool | None = None

    def inspect_committed(
        self,
        snapshot: ManifestSnapshot,
        source_id: UUID,
        *,
        deadline: Callable[[], bool],
        cancellation: Callable[[], bool],
    ) -> PdfInspectionResult:
        result = super().inspect_committed(
            snapshot, source_id, deadline=deadline, cancellation=cancellation
        )
        if not self.block.is_set():
            return result
        self.on_gui_thread = QThread.currentThread() == _application().thread()
        self.entered.set()
        while not self.release.wait(0.01):
            if cancellation():
                raise PdfInspectionError(PdfInspectionErrorCode.CANCELLED, "inspection.cancel")
            if deadline():
                raise PdfInspectionError(PdfInspectionErrorCode.TIMED_OUT, "inspection.deadline")
        return result


@pytest.mark.parametrize("refresh", [False, True])
def test_begin_and_refresh_remain_responsive_and_keyboard_cancel_reaches_actionable_retry(
    refresh: bool,
) -> None:
    app = _application()
    original_font = QFont(app.font())
    enlarged = QFont(original_font)
    enlarged.setPointSizeF(max(24.0, original_font.pointSizeF() * 2))
    app.setFont(enlarged)
    inspector = SlowInspector()
    workflow, repository, exporter = _service(inspector)

    class Backend(_Backend):
        def begin_package_review(
            self,
            mode: PortalOutputMode | None = None,
            *,
            cancellation: Callable[[], bool],
            progress: Callable[[int, int], None],
        ) -> PackageReviewState:
            return workflow.begin(
                repository.snapshot, mode, cancellation=cancellation, progress=progress
            )

        def refresh_package_review(
            self,
            mode: PortalOutputMode,
            *,
            cancellation: Callable[[], bool],
            progress: Callable[[int, int], None],
        ) -> PackageReviewState:
            return workflow.refresh(mode, cancellation=cancellation, progress=progress)

        def cancel_package_review(self) -> None:
            workflow.cancel()

    backend = Backend()
    backend.state = OrganizerViewState(
        repository.snapshot.case_id,
        repository.snapshot.revision,
        tuple(OrganizerPageView(page, index, 0, False) for index, page in enumerate(PAGE_IDS, 1)),
        False,
        False,
        False,
    )
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(lambda: cast(OrganizerBackend, backend))
    commands.connect_worker(worker)
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.initialize)
    thread.finished.connect(worker.deleteLater)
    window = PaperWorkShellWindow(commands, worker)
    window.resize(960, 540)
    window.show()
    thread.start()
    timer = QTimer()
    ticks: list[bool] = []
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start(5)
    try:
        _wait_for(lambda: window._controls_enabled)
        worker.state_changed.emit(backend.state)
        _wait_for(lambda: window._package_export_button.isEnabled())
        if not refresh:
            inspector.block.set()
        window.activateWindow()
        window._package_export_button.setFocus()
        app.processEvents()
        QTest.keyClick(window, Qt.Key.Key_E, Qt.KeyboardModifier.ControlModifier)
        if refresh:
            _wait_for(lambda: window._package_export_dialog is not None)
            dialog = window._package_export_dialog
            assert dialog is not None
            inspector.block.set()
            refresh_button = dialog.findChild(QPushButton, "packageExportRefreshButton")
            assert refresh_button is not None
            refresh_button.setFocus()
            QTest.keyClick(refresh_button, Qt.Key.Key_Space)
        _wait_for(inspector.entered.is_set)
        before = len(ticks)
        QTest.qWait(35)
        assert len(ticks) > before
        assert inspector.on_gui_thread is False
        dialog = window._package_export_dialog
        cancel = (
            window._cancel_button
            if dialog is None
            else dialog.findChild(QPushButton, "packageExportCancelButton")
        )
        assert (
            cancel is not None and cancel.isVisible() and cancel.isEnabled() and cancel.hasFocus()
        )
        interface = QAccessible.queryAccessibleInterface(cancel)
        assert interface is not None and interface.role() is QAccessible.Role.Button
        assert interface.text(QAccessible.Text.Name) and interface.state().focused
        assert cancel.height() >= cancel.fontMetrics().height()
        QTest.keyClick(cancel, Qt.Key.Key_Space)
        _wait_for(lambda: window._package_review_operation_id is None)
        assert workflow._active is None
        assert repository.saves == exporter.calls == 0
        if refresh:
            assert dialog is not None and dialog.summary is None
            retry = dialog.findChild(QPushButton, "packageExportRefreshButton")
            assert retry is not None and retry.isEnabled() and retry.hasFocus()
            status = dialog.findChild(QLabel, "packageExportStatus")
            assert status is not None and "cancelled" in status.text().lower()
        else:
            _wait_for(lambda: window._package_cancel_operation_id is None)
            export_route = window.findChild(QPushButton, "traceWorkflow-export")
            assert export_route is not None and export_route.isVisible()
            _wait_for(export_route.hasFocus)
            assert window._package_export_dialog is None
    finally:
        inspector.release.set()
        timer.stop()
        thread.quit()
        assert thread.wait(5000)
        window._allow_close = True
        window.close()
        window.deleteLater()
        app.processEvents()
        app.setFont(original_font)

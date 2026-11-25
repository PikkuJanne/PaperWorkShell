"""Exercise obsolete organizer pixels through the real thumbnail result signal."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton, QStyle, QStyleOptionViewItem

from paperworkshell.app.organizer import OrganizerBackend, OrganizerPageView, OrganizerViewState
from paperworkshell.domain.case_lifecycle import CaseIndex, CaseSummary
from paperworkshell.domain.pdf_rendering import PdfRenderRotation
from paperworkshell.domain.pdf_thumbnails import PdfThumbnailResult
from paperworkshell.domain.recovery import StartupRecoveryResult
from paperworkshell.ui.backend import (
    OperationCancellation,
    OrganizerBackendCommands,
    OrganizerBackendWorker,
)
from paperworkshell.ui.models import PreviewState
from paperworkshell.ui.trace_workbench import TracePageRailDelegate
from paperworkshell.ui.window import PaperWorkShellWindow, _LongOperationKind
from tests.unit.test_mixed_preview_window import CASE_ID, PDF_PAGE_ID, _pdf_result
from tests.unit.test_package_export_window import _application

_OTHER_PAGE = UUID("83000000-0000-4000-8000-000000000005")
_OLD_PIXEL = (201, 17, 41, 255)
_CURRENT_PIXEL = (13, 173, 107, 255)


class _SyntheticCase:
    startup_recovery_result = StartupRecoveryResult()
    assisted_use: None = None

    def __init__(self) -> None:
        self.saved = OrganizerViewState(
            CASE_ID,
            1,
            (
                OrganizerPageView(PDF_PAGE_ID, 1, 0, True),
                OrganizerPageView(_OTHER_PAGE, 2, 0, False),
            ),
            False,
            False,
            False,
        )
        self.state = OrganizerViewState.empty()
        self.previous_pages = self.saved.pages

    def list_cases(self) -> CaseIndex:
        date = datetime(2026, 11, 8, tzinfo=UTC)
        assert self.saved.revision is not None
        return CaseIndex((CaseSummary(CASE_ID, 1, self.saved.revision, date, date),))

    def open_case(self, case_id: UUID) -> OrganizerViewState:
        assert case_id == CASE_ID
        self.state = self.saved
        return self.state

    def close_case(self) -> OrganizerViewState:
        assert not self.state.autosave_pending
        self.state = OrganizerViewState.empty()
        return self.state

    def rotate_clockwise(self, page_id: UUID) -> OrganizerViewState:
        assert page_id == PDF_PAGE_ID
        target, other = self.state.pages
        return self._edit((replace(target, rotation_degrees=90), other))

    def remove_page(self, page_id: UUID) -> OrganizerViewState:
        assert page_id == PDF_PAGE_ID
        return self._edit((replace(self.state.pages[1], position=1),))

    def undo(self) -> OrganizerViewState:
        return self._edit(self.previous_pages)

    def _edit(self, pages: tuple[OrganizerPageView, ...]) -> OrganizerViewState:
        self.previous_pages = self.state.pages
        self.state = replace(self.state, pages=pages, can_undo=True, autosave_pending=True)
        return self.state

    def flush_autosave(self) -> OrganizerViewState:
        assert self.state.autosave_pending and self.state.revision is not None
        self.saved = replace(self.state, revision=self.state.revision + 1, autosave_pending=False)
        self.state = self.saved
        return self.state


@dataclass(frozen=True)
class _PreviewRequest:
    operation_id: int
    generation: int
    page_ids: tuple[UUID, ...]
    width: int
    height: int
    cancellation: OperationCancellation

    def result(
        self, pixel: tuple[int, int, int, int], rotation: PdfRenderRotation
    ) -> PdfThumbnailResult:
        result = _pdf_result()
        return replace(
            result,
            request=replace(
                result.request,
                maximum_width_pixels=self.width,
                maximum_height_pixels=self.height,
                additional_rotation=rotation,
            ),
            cache_key=replace(
                result.cache_key,
                maximum_width_pixels=self.width,
                maximum_height_pixels=self.height,
                additional_rotation=rotation,
            ),
            snapshot=replace(
                result.snapshot,
                width_pixels=24,
                height_pixels=32,
                row_stride_bytes=24 * 4,
                pixels=bytes(pixel) * (24 * 32),
                additional_rotation=rotation,
            ),
        )


def _click(button: QPushButton) -> None:
    assert button.isEnabled()
    button.click()


def _flush_autosave(window: PaperWorkShellWindow) -> None:
    assert window._autosave_timer.isActive()
    window._autosave_timer.stop()
    window._autosave_timer.timeout.emit()


def _start_preview(
    window: PaperWorkShellWindow, requests: list[_PreviewRequest], *, prior_requests: int
) -> _PreviewRequest:
    if len(requests) == prior_requests:
        assert window._preview_refresh_timer.isActive()
        window._preview_refresh_timer.stop()
        window._preview_refresh_timer.timeout.emit()
    assert len(requests) == prior_requests + 1
    request = requests[-1]
    active = window._active_operation
    assert active is not None and active.kind is _LongOperationKind.PREVIEW
    assert active.operation_id == request.operation_id
    assert request.page_ids == (PDF_PAGE_ID,)
    assert window._model.preview_state(PDF_PAGE_ID) is PreviewState.LOADING
    return request


def _presentation(window: PaperWorkShellWindow) -> tuple[object, ...]:
    return (
        window._selected_page_id(),
        window._status_label.text(),
        window._status_label.accessibleName(),
        window._error_label.text(),
        window._error_label.isVisible(),
        window._progress.isVisible(),
        window._cancel_button.isEnabled(),
    )


def _painted_pixel(window: PaperWorkShellWindow) -> tuple[int, int, int, int]:
    row = window._model.row_for_page(PDF_PAGE_ID)
    assert row is not None
    view = window._page_view
    index = window._model.index(row, 0)
    view.scrollTo(index)
    delegate = view.itemDelegateForIndex(index)
    assert isinstance(delegate, TracePageRailDelegate)
    option = QStyleOptionViewItem()
    view.initViewItemOption(option)
    option.rect = view.visualRect(index)
    delegate.initStyleOption(option, index)
    decoration = view.style().subElementRect(
        QStyle.SubElement.SE_ItemViewItemDecoration, option, view
    )
    assert decoration.isValid()
    center = decoration.center()
    viewport = view.viewport()
    assert viewport.rect().contains(center)
    painted = QImage(viewport.size(), QImage.Format.Format_RGBA8888)
    painted.fill(Qt.GlobalColor.transparent)
    with QPainter(painted) as painter:
        delegate.paint(painter, option, index)
    color = painted.pixelColor(center)
    return color.red(), color.green(), color.blue(), color.alpha()


@pytest.mark.parametrize("action", ["rotate", "remove_undo", "close_reopen"])
def test_obsolete_organizer_preview_cannot_paint(
    action: str, record_property: Callable[[str, object], None]
) -> None:
    app = _application()
    backend = _SyntheticCase()
    worker = OrganizerBackendWorker(lambda: cast(OrganizerBackend, backend))
    commands = OrganizerBackendCommands()
    commands.connect_worker(worker)
    commands.thumbnails_requested.disconnect(worker.get_thumbnails)
    requests: list[_PreviewRequest] = []
    commands.thumbnails_requested.connect(lambda *args: requests.append(_PreviewRequest(*args)))
    # Deliver UI commands through the real bridge only after the outstanding
    # preview callback, as with a busy backend event loop. No sleeps or threads.
    for signal, slot in (
        (commands.rotate_requested, worker.rotate_clockwise),
        (commands.remove_requested, worker.remove_page),
        (commands.close_case_requested, worker.close_case),
    ):
        signal.disconnect(slot)
        signal.connect(slot, Qt.ConnectionType.QueuedConnection)

    window = PaperWorkShellWindow(commands, worker)
    window.resize(1000, 760)
    window.show()
    try:
        commands.initialize_requested.emit()
        _click(window._open_case_button)
        app.processEvents()
        assert window._selected_page_id() == PDF_PAGE_ID
        old = _start_preview(window, requests, prior_requests=0)
        stale = (old.result(_OLD_PIXEL, PdfRenderRotation.DEGREES_0),)

        _click(
            {
                "rotate": window._rotate_button,
                "remove_undo": window._remove_button,
                "close_reopen": window._close_case_button,
            }[action]
        )
        active = window._active_operation
        assert active is not None and active.kind is _LongOperationKind.PREVIEW
        assert active.operation_id == old.operation_id
        assert old.cancellation.cancelled()
        invalidated_generation = window._thumbnail_generation
        assert invalidated_generation > old.generation
        assert window._active_preview_page_ids == old.page_ids
        assert window._model.row_for_page(PDF_PAGE_ID) == 0
        assert window._model.preview_state(PDF_PAGE_ID) is PreviewState.NOT_LOADED
        before = _presentation(window)

        worker.thumbnails_ready.emit(old.operation_id, old.generation, stale)

        assert window._model.preview_state(PDF_PAGE_ID) is PreviewState.NOT_LOADED
        assert window._model.preview(PDF_PAGE_ID) is None
        assert _presentation(window) == before
        worker.operation_finished.emit(old.operation_id)
        QCoreApplication.sendPostedEvents(worker, QEvent.Type.MetaCall)

        if action == "close_reopen":
            assert window._state == OrganizerViewState.empty()
            assert window._model.rowCount() == 0
            _click(window._open_case_button)
            assert window._state == backend.saved
        else:
            _flush_autosave(window)
            assert window._state == backend.saved
            if action == "remove_undo":
                assert window._selected_page_id() == _OTHER_PAGE
                before = _presentation(window)
                worker.thumbnails_ready.emit(old.operation_id, old.generation, stale)
                assert window._model.rowCount() == 1
                assert window._model.page_id_at(0) == _OTHER_PAGE
                assert window._model.preview_state(PDF_PAGE_ID) is None
                assert _presentation(window) == before
                _click(window._undo_button)
                _flush_autosave(window)
                QTest.keyClick(window._page_view, Qt.Key.Key_Home)

        assert window._selected_page_id() == PDF_PAGE_ID
        assert window._model.rowCount() == 2
        assert window._model.page_id_at(0) == PDF_PAGE_ID
        assert window._model.page_id_at(1) == _OTHER_PAGE
        assert window._state is not None and window._state.case_id == CASE_ID
        rotation = (
            PdfRenderRotation.DEGREES_90 if action == "rotate" else PdfRenderRotation.DEGREES_0
        )
        assert window._state.pages[0].rotation_degrees == rotation.value
        current = _start_preview(window, requests, prior_requests=1)
        assert current.operation_id != old.operation_id and current.generation > old.generation
        worker.thumbnails_ready.emit(
            current.operation_id, current.generation, (current.result(_CURRENT_PIXEL, rotation),)
        )
        assert window._model.preview_state(PDF_PAGE_ID) is PreviewState.READY
        painted_before = _painted_pixel(window)
        assert painted_before == _CURRENT_PIXEL
        status = "Preview update complete for 1 pages."
        worker.operation_status_changed.emit(current.operation_id, status)
        assert window._status_label.text() == status
        before = _presentation(window)

        worker.thumbnails_ready.emit(old.operation_id, old.generation, stale)

        assert _presentation(window) == before
        assert window._model.preview_state(PDF_PAGE_ID) is PreviewState.READY
        painted_after = _painted_pixel(window)
        assert painted_after == painted_before
        worker.operation_finished.emit(current.operation_id)

        record_property("old_operation_id", old.operation_id)
        record_property("old_generation", old.generation)
        record_property("generation_at_matching_operation_delivery", invalidated_generation)
        record_property("current_operation_id", current.operation_id)
        record_property("current_generation", current.generation)
        record_property("painted_rgba_before_replay", painted_before)
        record_property("painted_rgba_after_replay", painted_after)
    finally:
        window._preview_refresh_timer.stop()
        window._autosave_timer.stop()
        window._allow_close = True
        window.close()
        window.deleteLater()
        app.processEvents()

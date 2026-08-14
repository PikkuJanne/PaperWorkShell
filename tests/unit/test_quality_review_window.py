"""Focused organizer-window lifecycle tests for advisory page-quality review."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from paperworkshell.app.ocr_languages import (
    OCR_LANGUAGE_DISPLAY_NAMES,
    OcrLanguageOption,
    OcrLanguageRuntimeStatus,
    OcrLanguageSnapshot,
)
from paperworkshell.app.organizer import OrganizerBackend, OrganizerPageView, OrganizerViewState
from paperworkshell.app.quality_analysis import QualityReviewSnapshot
from paperworkshell.domain.ocr_languages import ENGLISH_GERMAN_OCR_LANGUAGE_SET_ID
from paperworkshell.domain.quality_analysis import (
    QualityEvidenceKind,
    QualityFinding,
    QualityFindingCode,
)
from paperworkshell.ui.backend import (
    OrganizerBackendCommands,
    OrganizerBackendWorker,
    UiFailure,
    UiProgress,
)
from paperworkshell.ui.window import PaperWorkShellWindow, _LongOperationKind

CASE_ID = UUID("8e300000-0000-4000-8000-000000000001")
PAGE_ONE = UUID("8e300000-0000-4000-8000-000000000002")
PAGE_TWO = UUID("8e300000-0000-4000-8000-000000000003")


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def _unused_backend_factory() -> OrganizerBackend:
    raise AssertionError("focused presentation tests do not initialize the backend")


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
    app.processEvents()
    try:
        yield window, commands, worker
    finally:
        window._allow_close = True
        window.close()
        window.deleteLater()
        app.processEvents()


def _state(*, revision: int = 7, autosave_pending: bool = False) -> OrganizerViewState:
    return OrganizerViewState(
        CASE_ID,
        revision,
        (
            OrganizerPageView(PAGE_ONE, 1, 0, False),
            OrganizerPageView(PAGE_TWO, 2, 0, False),
        ),
        False,
        False,
        autosave_pending,
    )


def _snapshot(*, revision: int = 7) -> QualityReviewSnapshot:
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
        revision,
        (PAGE_ONE, PAGE_TWO),
        (finding,),
        "scan-quality-v1",
        "a" * 64,
    )


def _multilingual_ocr_snapshot() -> OcrLanguageSnapshot:
    return OcrLanguageSnapshot(
        OcrLanguageRuntimeStatus.AVAILABLE,
        (
            OcrLanguageOption("eng", OCR_LANGUAGE_DISPLAY_NAMES["eng"], True),
            OcrLanguageOption("deu", OCR_LANGUAGE_DISPLAY_NAMES["deu"], True),
        ),
        ENGLISH_GERMAN_OCR_LANGUAGE_SET_ID,
        ("eng", "deu"),
        True,
    )


def _start_review(
    window: PaperWorkShellWindow,
    commands: OrganizerBackendCommands,
    worker: OrganizerBackendWorker,
) -> tuple[int, int, list[tuple[object, ...]]]:
    requests: list[tuple[object, ...]] = []
    commands.quality_review_requested.connect(lambda *values: requests.append(values))
    worker.state_changed.emit(_state())
    _application().processEvents()
    window._quality_check_button.setFocus()
    window._request_quality_review()
    active = window._active_operation
    assert active is not None and active.kind is _LongOperationKind.QUALITY
    assert len(requests) == 1
    return active.operation_id, window._quality_generation, requests


def test_shortcut_starts_one_exact_request_and_ready_is_terminal_before_finished() -> None:
    app = _application()
    with _window() as (window, commands, worker):
        requests: list[tuple[object, ...]] = []
        commands.quality_review_requested.connect(lambda *values: requests.append(values))
        worker.state_changed.emit(_state())
        app.processEvents()
        window._quality_check_button.setFocus()
        app.processEvents()

        QTest.keyClick(
            window,
            Qt.Key.Key_Q,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
        )
        app.processEvents()

        active = window._active_operation
        assert active is not None and active.kind is _LongOperationKind.QUALITY
        assert len(requests) == 1
        operation_id, generation, case_id, revision, cancellation = requests[0]
        assert (operation_id, generation, case_id, revision) == (
            active.operation_id,
            window._quality_generation,
            CASE_ID,
            7,
        )
        assert cancellation is active.cancellation
        assert not window._quality_check_button.isEnabled()
        assert window._cancel_button.isEnabled()

        worker.quality_review_ready.emit(operation_id, generation, _snapshot())
        app.processEvents()
        ready_summary = window._quality_summary.text()
        assert "Needs review: 1 automatic review signal" in ready_summary
        assert not window._cancel_button.isEnabled()
        QTest.keyClick(window, Qt.Key.Key_Escape)
        app.processEvents()
        assert window._quality_summary.text() == ready_summary
        assert not active.cancellation.cancelled()

        worker.operation_finished.emit(operation_id)
        app.processEvents()
        assert window._quality_summary.text() == ready_summary
        assert window._quality_findings_model.rowCount() == 1
        assert not window._cancel_button.isVisible()
        assert app.focusWidget() is window._quality_findings_view


def test_preview_is_cancelled_then_exactly_one_pending_quality_request_drains() -> None:
    app = _application()
    with _window() as (window, commands, worker):
        requests: list[tuple[object, ...]] = []
        commands.quality_review_requested.connect(lambda *values: requests.append(values))
        worker.state_changed.emit(_state())
        app.processEvents()
        preview = window._begin_long_operation(_LongOperationKind.PREVIEW)

        window._request_quality_review()
        assert preview.cancellation.cancelled()
        assert window._pending_quality_request == (CASE_ID, 7)
        assert requests == []
        assert "queued" in window._quality_summary.text().casefold()

        worker.operation_finished.emit(preview.operation_id)
        app.processEvents()

        assert len(requests) == 1
        active = window._active_operation
        assert active is not None and active.kind is _LongOperationKind.QUALITY
        assert requests[0][0] == active.operation_id
        assert window._pending_quality_request is None


def test_late_cancelled_preview_progress_cannot_disable_queued_quality_cancellation() -> None:
    app = _application()
    with _window() as (window, commands, worker):
        requests: list[tuple[object, ...]] = []
        commands.quality_review_requested.connect(lambda *values: requests.append(values))
        worker.state_changed.emit(_state())
        app.processEvents()
        preview = window._begin_long_operation(_LongOperationKind.PREVIEW)

        window._request_quality_review()
        assert preview.cancellation.cancelled()
        assert window._cancel_button.isEnabled()
        worker.progress_changed.emit(
            UiProgress("Preview", "rendering", 1, 2, True, preview.operation_id)
        )
        app.processEvents()
        assert window._cancel_button.isEnabled()

        QTest.keyClick(window, Qt.Key.Key_Escape)
        app.processEvents()
        assert window._pending_quality_request is None
        assert "cancelled" in window._quality_summary.text().casefold()
        assert requests == []


def test_state_change_cancels_and_rejects_late_quality_result() -> None:
    app = _application()
    with _window() as (window, commands, worker):
        operation_id, generation, _requests = _start_review(window, commands, worker)
        active = window._active_operation
        assert active is not None

        worker.state_changed.emit(_state(revision=8))
        app.processEvents()
        assert active.cancellation.cancelled()
        assert window._model.quality_review is None
        assert "Not checked for this saved revision" in window._quality_summary.text()

        worker.failed.emit(
            UiFailure(
                "QUALITY_ANALYSIS_PREVIEW_FAILED",
                "PaperWorkShell could not prepare a page for quality review safely.",
                True,
                operation_id,
            )
        )
        worker.quality_review_ready.emit(operation_id, generation, _snapshot())
        worker.operation_finished.emit(operation_id)
        app.processEvents()
        assert window._model.quality_review is None
        assert window._quality_findings_model.rowCount() == 0
        assert "Not checked for this saved revision" in window._quality_summary.text()
        assert not window._error_label.isVisible()


def test_saved_ocr_language_change_invalidates_accepted_quality_review() -> None:
    app = _application()
    with _window() as (window, commands, worker):
        operation_id, generation, _requests = _start_review(window, commands, worker)
        worker.quality_review_ready.emit(operation_id, generation, _snapshot())
        worker.operation_finished.emit(operation_id)
        app.processEvents()
        assert window._quality_findings_model.rowCount() == 1

        window._ocr_language_selection_saved(_multilingual_ocr_snapshot())
        app.processEvents()

        assert window._model.quality_review is None
        assert window._quality_findings_model.rowCount() == 0
        assert "Not checked for this saved revision" in window._quality_summary.text()


def test_cancel_requested_ignores_late_progress_until_cancelled_terminal() -> None:
    app = _application()
    with _window() as (window, commands, worker):
        operation_id, _generation, _requests = _start_review(window, commands, worker)
        active = window._active_operation
        assert active is not None

        window._cancel_active_operation()
        requested_summary = window._quality_summary.text()
        assert active.cancellation.cancelled()
        assert "Cancellation requested" in requested_summary
        worker.progress_changed.emit(
            UiProgress("Page quality check", "analyzing", 1, 2, True, operation_id)
        )
        app.processEvents()
        assert window._quality_summary.text() == requested_summary
        assert not window._cancel_button.isEnabled()

        worker.operation_cancelled.emit(
            operation_id,
            "Page quality check cancelled. No review results were applied.",
        )
        worker.operation_finished.emit(operation_id)
        app.processEvents()
        assert "cancelled" in window._quality_summary.text().casefold()


def test_late_success_after_cancel_is_discarded_and_recovers_focus() -> None:
    app = _application()
    with _window() as (window, commands, worker):
        operation_id, generation, _requests = _start_review(window, commands, worker)

        window._cancel_active_operation()
        worker.quality_review_ready.emit(operation_id, generation, _snapshot())
        app.processEvents()

        assert window._model.quality_review is None
        assert window._quality_findings_model.rowCount() == 0
        assert "cancelled" in window._quality_summary.text().casefold()
        assert "cancelled" in window._status_label.text().casefold()
        worker.operation_finished.emit(operation_id)
        app.processEvents()
        assert app.focusWidget() is window._quality_check_button


def test_failure_is_terminal_before_finished_and_focus_recovers_to_retry() -> None:
    app = _application()
    with _window() as (window, commands, worker):
        operation_id, _generation, _requests = _start_review(window, commands, worker)
        active = window._active_operation
        assert active is not None
        worker.failed.emit(
            UiFailure(
                "QUALITY_ANALYSIS_PREVIEW_FAILED",
                "PaperWorkShell could not prepare a page for quality review safely.",
                True,
                operation_id,
            )
        )
        app.processEvents()
        failed_summary = window._quality_summary.text()
        assert "did not finish" in failed_summary
        assert not window._cancel_button.isEnabled()

        QTest.keyClick(window, Qt.Key.Key_Escape)
        worker.progress_changed.emit(
            UiProgress("Page quality check", "analyzing", 1, 2, True, operation_id)
        )
        app.processEvents()
        assert window._quality_summary.text() == failed_summary
        assert not active.cancellation.cancelled()

        worker.operation_finished.emit(operation_id)
        app.processEvents()
        assert app.focusWidget() is window._quality_check_button
        assert window._quality_check_button.isEnabled()


def test_finding_activation_navigates_by_page_id_without_emitting_mutations() -> None:
    app = _application()
    with _window() as (window, commands, worker):
        operation_id, generation, _requests = _start_review(window, commands, worker)
        mutations: list[str] = []
        commands.move_requested.connect(lambda *_values: mutations.append("move"))
        commands.rotate_requested.connect(lambda *_values: mutations.append("rotate"))
        commands.remove_requested.connect(lambda *_values: mutations.append("remove"))
        commands.undo_requested.connect(lambda: mutations.append("undo"))
        worker.quality_review_ready.emit(operation_id, generation, _snapshot())
        worker.operation_finished.emit(operation_id)
        app.processEvents()

        finding_index = window._quality_findings_model.index(0, 0)
        window._go_to_quality_finding(finding_index)
        app.processEvents()

        assert window._selected_page_id() == PAGE_TWO
        assert app.focusWidget() is window._page_view
        assert "Showing page 2. Needs review: Likely blur." == window._status_label.text()
        assert mutations == []

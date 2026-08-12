from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import cast
from uuid import UUID

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from paperworkshell.app.organizer import (
    OrganizerBackend,
    OrganizerImportOutcome,
    OrganizerImportRefreshStatus,
    OrganizerPageView,
    OrganizerViewState,
)
from paperworkshell.domain.case_lifecycle import (
    CaseIndex,
    CaseLifecycleError,
    CaseLifecycleErrorCode,
)
from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.pdf_thumbnails import (
    PdfThumbnailError,
    PdfThumbnailErrorCode,
    PdfThumbnailResult,
)
from paperworkshell.domain.recovery import (
    CaseRecoveryResult,
    CaseRecoveryStatus,
    StartupRecoveryResult,
)
from paperworkshell.domain.source_ingest import (
    SourceImportProgress,
    SourceImportResult,
    SourceIngestError,
    SourceIngestErrorCode,
    SourceProgressPhase,
)
from paperworkshell.ui.application import _ShutdownWaitOutcome, _wait_for_backend_shutdown
from paperworkshell.ui.backend import (
    OperationCancellation,
    OrganizerBackendCommands,
    OrganizerBackendWorker,
)
from paperworkshell.ui.window import PaperWorkShellWindow
from tests.helpers.storage_delay import DeterministicStorageDelay, StorageDelayOperation

CASE_ID = UUID("81000000-0000-4000-8000-000000000001")
PAGE_ID = UUID("82000000-0000-4000-8000-000000000001")


def _application() -> QApplication:
    existing = QApplication.instance()
    return QApplication([]) if existing is None else cast(QApplication, existing)


def _wait_until(predicate: Callable[[], bool], *, timeout_ms: int = 2_000) -> None:
    deadline = time.monotonic() + (timeout_ms / 1_000)
    application = _application()
    while not predicate() and time.monotonic() < deadline:
        application.processEvents()
        QTest.qWait(5)
    application.processEvents()
    assert predicate()


def _initial_state() -> OrganizerViewState:
    return OrganizerViewState(
        CASE_ID,
        0,
        (OrganizerPageView(PAGE_ID, 1, 0, True),),
        False,
        False,
        False,
    )


def _many_page_state(page_count: int) -> OrganizerViewState:
    return OrganizerViewState(
        CASE_ID,
        0,
        tuple(
            OrganizerPageView(
                UUID(f"82000000-0000-4000-8000-{number:012d}"),
                number,
                0,
                True,
            )
            for number in range(1, page_count + 1)
        ),
        False,
        False,
        False,
    )


class _BlockingOrganizerBackend:
    startup_recovery_result = StartupRecoveryResult()

    def __init__(
        self,
        *,
        state: OrganizerViewState | None = None,
        shutdown_delay_seconds: float = 0.0,
    ) -> None:
        self.state = _initial_state() if state is None else state
        self.timeline: list[str] = []
        self.preview_started = Event()
        self.preview_cancelled = Event()
        self.preview_resumed = Event()
        self.preview_timed_out = Event()
        self.import_started = Event()
        self.import_cancelled = Event()
        self.import_timed_out = Event()
        self.shutdown_started = Event()
        self.shutdown_finished = Event()
        self.preview_calls = 0
        self.preview_page_ids: list[tuple[UUID, ...]] = []
        self.import_calls = 0
        self.shutdown_delay_seconds = shutdown_delay_seconds

    def list_cases(self) -> CaseIndex:
        return CaseIndex()

    def create_case(self) -> OrganizerViewState:
        return self.state

    def get_thumbnails(
        self,
        maximum_width_pixels: int,
        maximum_height_pixels: int,
        *,
        page_ids: tuple[UUID, ...],
        timeout_seconds: float = 30.0,
        cancellation: Callable[[], bool] | None = None,
        progress: object = None,
    ) -> tuple[PdfThumbnailResult, ...]:
        del maximum_width_pixels, maximum_height_pixels, timeout_seconds, progress
        assert page_ids
        assert len(page_ids) <= 8
        self.preview_page_ids.append(page_ids)
        self.preview_calls += 1
        if self.preview_calls > 1:
            self.timeline.append("preview_resumed")
            self.preview_resumed.set()
            return ()
        self.timeline.append("preview_started")
        self.preview_started.set()
        deadline = time.monotonic() + 3.0
        while cancellation is None or not cancellation():
            if time.monotonic() >= deadline:
                self.preview_timed_out.set()
                raise PdfThumbnailError(PdfThumbnailErrorCode.TIMED_OUT, "test.preview")
            time.sleep(0.005)
        self.timeline.append("preview_cancelled")
        self.preview_cancelled.set()
        raise PdfThumbnailError(PdfThumbnailErrorCode.CANCELLED, "test.preview")

    def import_source(
        self,
        source_path: Path,
        display_name: str,
        *,
        timeout_seconds: float = 30.0,
        cancellation: Callable[[], bool] | None = None,
        progress: object = None,
    ) -> SourceImportResult:
        del source_path, display_name, timeout_seconds, progress
        self.import_calls += 1
        self.timeline.append("import_started")
        self.import_started.set()
        deadline = time.monotonic() + 3.0
        while cancellation is None or not cancellation():
            if time.monotonic() >= deadline:
                self.import_timed_out.set()
                raise SourceIngestError(SourceIngestErrorCode.TIMED_OUT, "test.import")
            time.sleep(0.005)
        self.timeline.append("import_cancelled")
        self.import_cancelled.set()
        raise SourceIngestError(SourceIngestErrorCode.CANCELLED, "test.import")

    def rotate_clockwise(self, page_id: UUID) -> OrganizerViewState:
        assert page_id == PAGE_ID
        self.timeline.append("rotate")
        page = replace(self.state.pages[0], rotation_degrees=90)
        self.state = replace(
            self.state,
            pages=(page,),
            can_undo=True,
            autosave_pending=True,
        )
        return self.state

    def flush_autosave(self) -> OrganizerViewState:
        self.timeline.append("autosave")
        assert self.state.autosave_pending
        assert self.state.revision is not None
        self.state = replace(
            self.state,
            revision=self.state.revision + 1,
            autosave_pending=False,
        )
        return self.state

    def shutdown(self) -> None:
        self.timeline.append("shutdown_started")
        self.shutdown_started.set()
        if self.shutdown_delay_seconds:
            time.sleep(self.shutdown_delay_seconds)
        self.timeline.append("shutdown_finished")
        self.shutdown_finished.set()


class _ReopenRequiredOrganizerBackend:
    startup_recovery_result = StartupRecoveryResult()

    def __init__(self) -> None:
        self.state = _initial_state()

    def list_cases(self) -> CaseIndex:
        return CaseIndex()

    def import_source(
        self,
        source_path: Path,
        display_name: str,
        *,
        timeout_seconds: float = 30.0,
        cancellation: Callable[[], bool] | None = None,
        progress: object = None,
    ) -> OrganizerImportOutcome:
        del source_path, display_name, timeout_seconds, cancellation, progress
        return OrganizerImportOutcome(
            SourceImportResult(
                case_id=CASE_ID,
                source_id=UUID("83000000-0000-4000-8000-000000000001"),
                page_ids=(UUID("84000000-0000-4000-8000-000000000001"),),
                media_type=DetectedMediaType.PDF,
                byte_size=128,
                sha256="a" * 64,
                manifest_revision=1,
            ),
            self.state,
            OrganizerImportRefreshStatus.REOPEN_REQUIRED,
        )


class _CleanupRefusalBackend(_BlockingOrganizerBackend):
    def __init__(self, failure_kind: str) -> None:
        super().__init__(
            state=OrganizerViewState(
                CASE_ID,
                0,
                (),
                False,
                False,
                False,
            )
        )
        self.failure_kind = failure_kind
        self.shutdown_attempts = 0

    def shutdown(self) -> None:
        self.shutdown_attempts += 1
        self.shutdown_started.set()
        if self.shutdown_attempts == 1:
            self.state = OrganizerViewState.empty()
            if self.failure_kind == "lifecycle":
                raise CaseLifecycleError(CaseLifecycleErrorCode.CLOSE_FAILED, "case.test")
            raise RuntimeError("PRIVATE_SHUTDOWN_CLEANUP_CANARY")
        self.shutdown_finished.set()


class _SlowAutosaveBackend(_BlockingOrganizerBackend):
    def __init__(self, delay: DeterministicStorageDelay) -> None:
        super().__init__(
            state=replace(
                _initial_state(),
                pages=(replace(_initial_state().pages[0], preview_supported=False),),
            )
        )
        self._delay = delay

    def flush_autosave(self) -> OrganizerViewState:
        self._delay.checkpoint(StorageDelayOperation.FLUSH)
        return super().flush_autosave()


class _SlowImportBackend(_BlockingOrganizerBackend):
    def __init__(self, delay: DeterministicStorageDelay) -> None:
        super().__init__(
            state=replace(
                _initial_state(),
                pages=(replace(_initial_state().pages[0], preview_supported=False),),
            )
        )
        self._delay = delay

    def import_source(
        self,
        source_path: Path,
        display_name: str,
        *,
        timeout_seconds: float = 30.0,
        cancellation: Callable[[], bool] | None = None,
        progress: object = None,
    ) -> SourceImportResult:
        del source_path, display_name, timeout_seconds
        assert cancellation is not None
        assert callable(progress)
        self.import_calls += 1
        self.timeline.append("import_started")
        self.import_started.set()
        progress(
            SourceImportProgress(
                UUID("83000000-0000-4000-8000-000000000002"),
                SourceProgressPhase.COPYING,
                1,
                2,
            )
        )
        if not self._delay.checkpoint(
            StorageDelayOperation.WRITE,
            cancellation=cancellation,
        ):
            self.timeline.append("import_cancelled")
            self.import_cancelled.set()
            raise SourceIngestError(SourceIngestErrorCode.CANCELLED, "test.slow_import")
        raise AssertionError("slow import test unexpectedly released its write boundary")


class _SlowReadBackend(_BlockingOrganizerBackend):
    def __init__(self, delay: DeterministicStorageDelay) -> None:
        super().__init__(
            state=replace(
                _initial_state(),
                pages=(replace(_initial_state().pages[0], preview_supported=False),),
            )
        )
        self._delay = delay
        self.read_finished = Event()

    def open_case(self, case_id: UUID) -> OrganizerViewState:
        assert case_id == CASE_ID
        self._delay.checkpoint(StorageDelayOperation.READ)
        self.read_finished.set()
        return self.state


class _SlowDirectoryBackend(_BlockingOrganizerBackend):
    def __init__(self, delay: DeterministicStorageDelay) -> None:
        super().__init__()
        self._delay = delay
        self.scan_finished = Event()

    def list_cases(self) -> CaseIndex:
        self._delay.checkpoint(StorageDelayOperation.DIRECTORY)
        self.scan_finished.set()
        return super().list_cases()


@contextmanager
def _threaded_window(
    backend: _BlockingOrganizerBackend,
) -> Iterator[tuple[PaperWorkShellWindow, OrganizerBackendCommands, OrganizerBackendWorker]]:
    application = _application()
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(lambda: cast(OrganizerBackend, backend))
    backend_thread = QThread()
    worker.moveToThread(backend_thread)
    commands.connect_worker(worker)
    backend_thread.started.connect(worker.initialize)
    worker.shutdown_complete.connect(worker.deleteLater)
    worker.shutdown_complete.connect(
        backend_thread.quit,
        Qt.ConnectionType.DirectConnection,
    )
    initialized = Event()
    worker.initialized.connect(initialized.set)
    window = PaperWorkShellWindow(commands, worker)
    window.show()
    backend_thread.start()
    _wait_until(initialized.is_set)
    try:
        yield window, commands, worker
    finally:
        if backend_thread.isRunning():
            window.request_shutdown()
            _wait_until(lambda: not backend_thread.isRunning(), timeout_ms=4_000)
        window._allow_close = True
        window.close()
        window.deleteLater()
        backend_thread.deleteLater()
        application.processEvents()


def test_worker_reports_committed_reopen_outcome_without_retryable_failure() -> None:
    application = _application()
    backend = _ReopenRequiredOrganizerBackend()
    worker = OrganizerBackendWorker(lambda: cast(OrganizerBackend, backend))
    states: list[OrganizerViewState] = []
    reopen_required: list[tuple[int, str]] = []
    operation_statuses: list[tuple[int, str]] = []
    failures: list[object] = []
    finished: list[int] = []
    worker.state_changed.connect(states.append)
    worker.case_reopen_required.connect(
        lambda operation_id, message: reopen_required.append((operation_id, message))
    )
    worker.operation_status_changed.connect(
        lambda operation_id, message: operation_statuses.append((operation_id, message))
    )
    worker.failed.connect(failures.append)
    worker.operation_finished.connect(finished.append)
    worker.initialize()

    worker.import_source(
        41,
        Path("synthetic.pdf"),
        "Synthetic.pdf",
        OperationCancellation(),
    )

    assert states == [backend.state]
    assert reopen_required == [
        (
            41,
            "Import committed. Close and reopen the case before continuing so the organizer "
            "can refresh.",
        )
    ]
    assert operation_statuses == []
    assert failures == []
    assert finished == [41]
    worker.deleteLater()
    application.processEvents()


def test_worker_publishes_exact_path_free_startup_recovery_result() -> None:
    application = _application()
    result = StartupRecoveryResult(
        (
            CaseRecoveryResult(
                case_id=UUID("85000000-0000-4000-8000-000000000001"),
                status=CaseRecoveryStatus.CURRENT,
                manifest_revision=0,
                removed_temp_files=1,
                released_bytes=17,
            ),
        )
    )
    backend = _BlockingOrganizerBackend()
    backend.startup_recovery_result = result
    worker = OrganizerBackendWorker(lambda: cast(OrganizerBackend, backend))
    published: list[object] = []
    signal_order: list[str] = []
    statuses: list[str] = []
    worker.initialized.connect(lambda: signal_order.append("initialized"))
    worker.cases_changed.connect(lambda _cases: signal_order.append("cases"))
    worker.status_changed.connect(statuses.append)

    def record_recovery(value: object) -> None:
        signal_order.append("recovery")
        published.append(value)

    worker.startup_recovery_changed.connect(record_recovery)

    worker.initialize()

    assert published == [result]
    assert signal_order == ["initialized", "cases", "recovery"]
    assert statuses == []
    worker.deleteLater()
    application.processEvents()


@pytest.mark.parametrize("failure_kind", ("lifecycle", "resource"))
def test_cleanup_shutdown_refusal_republishes_empty_state_and_keyboard_retry_closes(
    failure_kind: str,
) -> None:
    backend = _CleanupRefusalBackend(failure_kind)
    with _threaded_window(backend) as (window, commands, _worker):
        commands.create_case_requested.emit()
        _wait_until(lambda: window._state is not None and window._state.case_id == CASE_ID)

        window.request_shutdown()
        _wait_until(lambda: window._retry_close_button.isVisible())

        assert backend.shutdown_attempts == 1
        assert window._state == OrganizerViewState.empty()
        assert window._stack.currentWidget() is window._welcome_page
        assert not window._retry_save_button.isVisible()
        assert window._retry_close_button.isEnabled()
        assert _application().focusWidget() is window._retry_close_button
        assert window._status_label.text() == (
            "The application remains open because secure cleanup could not finish. "
            "Retry closing PaperWorkShell."
        )
        assert "PRIVATE_SHUTDOWN_CLEANUP_CANARY" not in window._error_label.text()
        assert "PRIVATE_SHUTDOWN_CLEANUP_CANARY" not in window._status_label.text()

        QTest.keyClick(window._retry_close_button, Qt.Key.Key_Space)

        _wait_until(lambda: backend.shutdown_attempts == 2)
        _wait_until(lambda: window._allow_close)
        assert backend.shutdown_finished.is_set()


def test_real_thread_correlates_preview_and_import_cancellation_and_resumes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backend = _BlockingOrganizerBackend()
    dialog_calls: list[bool] = []
    selected = tmp_path / "synthetic.pdf"

    def select_source(*_args: object, **_kwargs: object) -> tuple[str, str]:
        dialog_calls.append(True)
        return str(selected), ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", select_source)
    with _threaded_window(backend) as (window, commands, worker):
        commands.create_case_requested.emit()
        _wait_until(backend.preview_started.is_set)
        first_operation = window._active_operation
        assert first_operation is not None

        window._choose_import()
        window._choose_import()
        _wait_until(backend.import_started.is_set)

        assert dialog_calls == [True]
        assert backend.preview_cancelled.is_set()
        assert backend.import_calls == 1
        import_operation = window._active_operation
        assert import_operation is not None
        assert import_operation.operation_id != first_operation.operation_id

        prior_status = window._status_label.text()
        worker.operation_status_changed.emit(
            first_operation.operation_id,
            "Stale preview completion.",
        )
        _application().processEvents()
        assert window._status_label.text() == prior_status

        window._cancel_active_operation()
        _wait_until(backend.import_cancelled.is_set)
        _wait_until(backend.preview_resumed.is_set)

        assert not backend.preview_timed_out.is_set()
        assert not backend.import_timed_out.is_set()
        assert backend.timeline[:4] == [
            "preview_started",
            "preview_cancelled",
            "import_started",
            "import_cancelled",
        ]
        assert backend.timeline[-1] == "preview_resumed"


def test_window_close_during_import_waits_for_cancellation_and_backend_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backend = _BlockingOrganizerBackend(
        state=OrganizerViewState(CASE_ID, 0, (), False, False, False),
        shutdown_delay_seconds=0.25,
    )
    selected = tmp_path / "synthetic.pdf"
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(selected), ""),
    )

    with _threaded_window(backend) as (window, commands, _worker):
        commands.create_case_requested.emit()
        _wait_until(lambda: window._state is not None and window._state.case_id == CASE_ID)

        window._choose_import()
        _wait_until(backend.import_started.is_set)
        assert "Keep PaperWorkShell open" in window._organizer_guidance.text()

        window.request_shutdown()
        assert window._closing_requested
        assert not window._allow_close
        assert window._organizer_guidance.text() == (
            "Closing PaperWorkShell safely. Waiting for the current import to stop and secure "
            "cleanup to finish."
        )
        _wait_until(backend.import_cancelled.is_set)
        _wait_until(backend.shutdown_started.is_set)
        _wait_until(lambda: window._active_operation is None)
        assert not backend.shutdown_finished.is_set()
        assert window._organizer_guidance.text() == (
            "Closing PaperWorkShell safely. Waiting for the current import to stop and secure "
            "cleanup to finish."
        )
        _wait_until(backend.shutdown_finished.is_set)
        _wait_until(lambda: window._allow_close)

        assert backend.timeline == [
            "import_started",
            "import_cancelled",
            "shutdown_started",
            "shutdown_finished",
        ]


def test_real_thread_cancels_preview_and_saves_before_renderer_timeout() -> None:
    backend = _BlockingOrganizerBackend()
    with _threaded_window(backend) as (window, commands, _worker):
        commands.create_case_requested.emit()
        _wait_until(backend.preview_started.is_set)
        started = time.monotonic()

        window._rotate_selected()
        _wait_until(lambda: "autosave" in backend.timeline, timeout_ms=2_000)
        _wait_until(backend.preview_resumed.is_set)

        assert time.monotonic() - started < 2.0
        assert not backend.preview_timed_out.is_set()
        assert backend.timeline[:4] == [
            "preview_started",
            "preview_cancelled",
            "rotate",
            "autosave",
        ]
        assert backend.timeline[-1] == "preview_resumed"


def test_artificially_slow_storage_flush_does_not_block_the_gui_event_loop() -> None:
    delay = DeterministicStorageDelay(frozenset({StorageDelayOperation.FLUSH}))
    backend = _SlowAutosaveBackend(delay)
    heartbeat = Event()

    with _threaded_window(backend) as (window, commands, _worker):
        commands.create_case_requested.emit()
        _wait_until(lambda: window._state is not None and window._state.case_id == CASE_ID)
        window._rotate_selected()
        _wait_until(delay.entered.is_set)

        QTimer.singleShot(0, heartbeat.set)
        _wait_until(heartbeat.is_set)
        assert window.isVisible()

        delay.release.set()
        _wait_until(lambda: "autosave" in backend.timeline)
        assert delay.observed == (StorageDelayOperation.FLUSH,)


def test_slow_storage_import_keeps_progress_visible_and_cancellation_available(
    tmp_path: Path,
) -> None:
    delay = DeterministicStorageDelay(frozenset({StorageDelayOperation.WRITE}))
    backend = _SlowImportBackend(delay)
    heartbeat = Event()

    with _threaded_window(backend) as (window, commands, _worker):
        commands.create_case_requested.emit()
        _wait_until(lambda: window._state is not None and window._state.case_id == CASE_ID)
        window._queue_import(tmp_path / "synthetic.pdf", "Synthetic.pdf")
        _wait_until(delay.entered.is_set)

        QTimer.singleShot(0, heartbeat.set)
        _wait_until(heartbeat.is_set)
        assert window._progress.isVisible()
        assert window._progress.value() == 1
        assert window._progress.maximum() == 2
        assert window._cancel_button.isVisible()
        assert window._cancel_button.isEnabled()

        window._cancel_active_operation()
        _wait_until(backend.import_cancelled.is_set)
        _wait_until(lambda: window._active_operation is None)

        assert not delay.release.is_set()
        assert delay.observed == (StorageDelayOperation.WRITE,)
        assert backend.timeline == ["import_started", "import_cancelled"]


def test_artificially_slow_storage_read_does_not_block_the_gui_event_loop() -> None:
    delay = DeterministicStorageDelay(frozenset({StorageDelayOperation.READ}))
    backend = _SlowReadBackend(delay)
    heartbeat = Event()

    with _threaded_window(backend) as (window, commands, _worker):
        commands.open_case_requested.emit(CASE_ID)
        _wait_until(delay.entered.is_set)

        try:
            QTimer.singleShot(0, heartbeat.set)
            _wait_until(heartbeat.is_set)
            assert window.isVisible()
            assert not backend.read_finished.is_set()
        finally:
            delay.release.set()

        _wait_until(backend.read_finished.is_set)
        _wait_until(lambda: window._state is not None and window._state.case_id == CASE_ID)
        assert delay.observed == (StorageDelayOperation.READ,)


def test_artificially_slow_directory_scan_does_not_block_the_gui_event_loop() -> None:
    delay = DeterministicStorageDelay(frozenset({StorageDelayOperation.DIRECTORY}))
    backend = _SlowDirectoryBackend(delay)
    heartbeat = Event()

    with _threaded_window(backend) as (window, _commands, _worker):
        _wait_until(delay.entered.is_set)

        try:
            QTimer.singleShot(0, heartbeat.set)
            _wait_until(heartbeat.is_set)
            assert window.isVisible()
            assert not backend.scan_finished.is_set()
        finally:
            delay.release.set()

        _wait_until(backend.scan_finished.is_set)
        assert delay.observed == (StorageDelayOperation.DIRECTORY,)


def test_visible_preview_batches_remain_bounded_and_follow_keyboard_selection() -> None:
    state = _many_page_state(1_000)
    backend = _BlockingOrganizerBackend(state=state)
    with _threaded_window(backend) as (window, commands, _worker):
        commands.create_case_requested.emit()
        _wait_until(backend.preview_started.is_set)

        first_batch = backend.preview_page_ids[0]
        assert 1 <= len(first_batch) <= 8
        assert first_batch == tuple(
            page.page_id for page in state.pages if page.page_id in frozenset(first_batch)
        )
        assert state.pages[0].page_id in first_batch

        final_index = window._model.index(999, 0)
        window._page_view.setCurrentIndex(final_index)
        window._page_view.scrollTo(final_index)
        QTest.qWait(150)
        _application().processEvents()
        window._cancel_active_operation()
        _wait_until(backend.preview_resumed.is_set)

        second_batch = backend.preview_page_ids[1]
        assert 1 <= len(second_batch) <= 8
        assert state.pages[-1].page_id in second_batch
        assert second_batch == tuple(
            page.page_id for page in state.pages if page.page_id in frozenset(second_batch)
        )


def test_shutdown_wait_timeout_does_not_quit_backend_before_cleanup() -> None:
    _application()
    backend = _BlockingOrganizerBackend(shutdown_delay_seconds=0.15)
    commands = OrganizerBackendCommands()
    worker = OrganizerBackendWorker(lambda: cast(OrganizerBackend, backend))
    backend_thread = QThread()
    worker.moveToThread(backend_thread)
    commands.connect_worker(worker)
    backend_thread.started.connect(worker.initialize)
    worker.shutdown_complete.connect(worker.deleteLater)
    worker.shutdown_complete.connect(
        backend_thread.quit,
        Qt.ConnectionType.DirectConnection,
    )
    backend_thread.start()
    _wait_until(lambda: worker._backend is not None)

    commands.shutdown_requested.emit()
    outcome = _wait_for_backend_shutdown(backend_thread, worker, timeout_ms=10)

    assert outcome is _ShutdownWaitOutcome.TIMEOUT
    assert backend.shutdown_started.is_set()
    assert not backend.shutdown_finished.is_set()
    assert backend_thread.isRunning()
    assert backend.shutdown_finished.wait(1.0)
    assert backend_thread.wait(1_000)
    backend_thread.deleteLater()
    _application().processEvents()

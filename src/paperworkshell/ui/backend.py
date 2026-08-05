"""Thread-confined Qt bridge for organizer application services."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from typing import Final, Protocol
from uuid import UUID

from PySide6.QtCore import QObject, Signal, Slot

from paperworkshell.app.organizer import OrganizerBackend, OrganizerViewState
from paperworkshell.domain.case_lifecycle import CaseIndex, CaseLifecycleError
from paperworkshell.domain.manifest_repository import ManifestRepositoryError
from paperworkshell.domain.page_operations import PageOperationError
from paperworkshell.domain.pdf_thumbnails import (
    PdfThumbnailError,
    PdfThumbnailErrorCode,
    PdfThumbnailProgress,
)
from paperworkshell.domain.source_ingest import (
    SourceImportProgress,
    SourceIngestError,
    SourceIngestErrorCode,
)


class OrganizerBackendFactory(Protocol):
    """Construct the complete backend on its permanent owner thread."""

    def __call__(self) -> OrganizerBackend: ...


@dataclass(frozen=True, slots=True)
class UiFailure:
    """Stable path-free failure allowed to cross into presentation code."""

    code: str
    message: str
    retryable: bool
    operation_id: int | None = None


@dataclass(frozen=True, slots=True)
class UiProgress:
    """Visible, non-color-only progress announcement state."""

    operation: str
    phase: str
    completed: int
    total: int
    cancelable: bool
    operation_id: int


class OperationCancellation:
    """Thread-safe cooperative cancellation token for one long operation."""

    __slots__ = ("_event", "_lock")

    def __init__(self) -> None:
        self._event = Event()
        self._lock = Lock()

    def cancel(self) -> None:
        """Request cancellation from the GUI thread without waiting for a Qt slot."""

        with self._lock:
            self._event.set()

    def cancelled(self) -> bool:
        """Return the current cancellation state as a service probe."""

        return self._event.is_set()


_KNOWN_FAILURES: Final = (
    CaseLifecycleError,
    ManifestRepositoryError,
    PageOperationError,
    PdfThumbnailError,
    SourceIngestError,
)


def sanitize_ui_failure(
    error: BaseException,
    *,
    operation_id: int | None = None,
) -> UiFailure:
    """Map only known typed failures; never stringify an unknown exception."""

    if isinstance(error, _KNOWN_FAILURES):
        return UiFailure(
            code=error.code.value,
            message=error.safe_message,
            retryable=isinstance(error, (ManifestRepositoryError, PdfThumbnailError)),
            operation_id=operation_id,
        )
    return UiFailure(
        code="PWS_UI_UNEXPECTED",
        message="PaperWorkShell could not complete that action. Try again or restart the application.",
        retryable=True,
        operation_id=operation_id,
    )


class OrganizerBackendWorker(QObject):
    """Serialize backend and worker mutations on one long-lived Qt thread."""

    initialized = Signal()
    cases_changed = Signal(object)
    state_changed = Signal(object)
    thumbnails_ready = Signal(int, int, object)
    progress_changed = Signal(object)
    status_changed = Signal(str)
    operation_status_changed = Signal(int, str)
    case_reopen_required = Signal(int, str)
    review_required = Signal(str)
    operation_started = Signal(int, str)
    operation_cancelled = Signal(int, str)
    operation_finished = Signal(int)
    failed = Signal(object)
    shutdown_complete = Signal()
    shutdown_refused = Signal()

    def __init__(self, factory: OrganizerBackendFactory) -> None:
        super().__init__()
        self._factory = factory
        self._backend: OrganizerBackend | None = None

    @Slot()
    def initialize(self) -> None:
        """Construct all thread-affine services and publish the case index."""

        try:
            self._backend = self._factory()
            self.initialized.emit()
            self._emit_cases()
            self.status_changed.emit("Ready. Create a case or open an existing case.")
        except BaseException as error:
            self.failed.emit(sanitize_ui_failure(error))

    @Slot()
    def list_cases(self) -> None:
        """Publish a deterministic path-free case index."""

        self._run(self._emit_cases)

    @Slot()
    def create_case(self) -> None:
        """Create and open a case, then publish its empty organizer state."""

        def action(backend: OrganizerBackend) -> None:
            state = backend.create_case()
            self.state_changed.emit(state)
            self.status_changed.emit(
                "New case created. Import a document to begin organizing pages."
            )

        self._with_backend(action)

    @Slot(object)
    def open_case(self, case_id: object) -> None:
        """Open one selected internal case identifier."""

        def action(backend: OrganizerBackend) -> None:
            if type(case_id) is not UUID:
                raise TypeError
            state = backend.open_case(case_id)
            self.state_changed.emit(state)
            if state.recovery_review_required:
                self.review_required.emit(
                    "Case recovered from the validated save journal. "
                    "Review the page organizer before continuing."
                )
            else:
                self.status_changed.emit(f"Case opened with {len(state.pages)} active pages.")

        self._with_backend(action)

    @Slot()
    def close_case(self) -> None:
        """Flush and close the active case before returning to case selection."""

        def action(backend: OrganizerBackend) -> None:
            state = backend.close_case()
            self.state_changed.emit(state)
            self._emit_cases()
            self.status_changed.emit("Case closed. All queued changes were saved.")

        self._with_backend(action)

    @Slot(int, object, str, object)
    def import_source(
        self,
        operation_id: int,
        source_path: object,
        display_name: str,
        cancellation: object,
    ) -> None:
        """Import one user-selected source with progress and direct cancellation."""

        def action(backend: OrganizerBackend) -> None:
            if not isinstance(source_path, Path) or type(cancellation) is not OperationCancellation:
                raise TypeError
            outcome = backend.import_source(
                source_path,
                display_name,
                cancellation=cancellation.cancelled,
                progress=lambda progress: self._source_progress(operation_id, progress),
            )
            state = outcome.state
            self.state_changed.emit(state)
            if outcome.refresh_required:
                self.case_reopen_required.emit(operation_id, outcome.safe_message)
            else:
                self.operation_status_changed.emit(
                    operation_id,
                    f"Import complete. The case now has {len(state.pages)} active pages.",
                )

        self._run_long(operation_id, "Import", action)

    @Slot(int, int, object, int, int, object)
    def get_thumbnails(
        self,
        operation_id: int,
        generation: int,
        page_ids: object,
        maximum_width: int,
        maximum_height: int,
        cancellation: object,
    ) -> None:
        """Generate one bounded ordered preview subset for a UI generation."""

        def action(backend: OrganizerBackend) -> None:
            if (
                type(page_ids) is not tuple
                or not all(type(page_id) is UUID for page_id in page_ids)
                or type(cancellation) is not OperationCancellation
            ):
                raise TypeError
            results = backend.get_thumbnails(
                maximum_width_pixels=maximum_width,
                maximum_height_pixels=maximum_height,
                page_ids=page_ids,
                cancellation=cancellation.cancelled,
                progress=lambda progress: self._thumbnail_progress(operation_id, progress),
            )
            self.thumbnails_ready.emit(operation_id, generation, results)
            self.operation_status_changed.emit(
                operation_id,
                f"Preview update complete for {len(results)} PDF pages.",
            )

        self._run_long(operation_id, "Preview", action)

    @Slot(object)
    def rotate_clockwise(self, page_id: object) -> None:
        """Queue one clockwise quarter-turn for the selected page."""

        self._page_action(page_id, lambda backend, selected: backend.rotate_clockwise(selected))

    @Slot(object)
    def remove_page(self, page_id: object) -> None:
        """Queue removal of one selected page from the active sequence."""

        self._page_action(page_id, lambda backend, selected: backend.remove_page(selected))

    @Slot(object, int)
    def move_page(self, page_id: object, offset: int) -> None:
        """Queue a keyboard-accessible relative reorder operation."""

        def move(backend: OrganizerBackend, selected: UUID) -> OrganizerViewState:
            page = next(item for item in backend.state.pages if item.page_id == selected)
            return backend.move_page(selected, page.position + offset)

        self._page_action(page_id, move)

    @Slot(object)
    def reorder_pages(self, page_ids: object) -> None:
        """Queue an exact page order selected without drag-and-drop."""

        def action(backend: OrganizerBackend) -> None:
            if type(page_ids) is not tuple or not all(type(item) is UUID for item in page_ids):
                raise TypeError
            state = backend.reorder_pages(page_ids)
            self.state_changed.emit(state)
            self.status_changed.emit("Page order changed. Saving changes.")

        self._with_backend(action)

    @Slot()
    def undo(self) -> None:
        """Undo exactly one session organizer operation."""

        self._state_action(lambda backend: backend.undo(), "Last organizer action undone.")

    @Slot()
    def redo(self) -> None:
        """Redo exactly one previously undone session operation."""

        self._state_action(lambda backend: backend.redo(), "Organizer action redone.")

    @Slot()
    def flush_autosave(self) -> None:
        """Flush the latest queued organizer state without closing the case."""

        self._state_action(
            lambda backend: backend.flush_autosave(),
            "All organizer changes saved.",
        )

    @Slot()
    def retry_autosave(self) -> None:
        """Retry a retained autosave candidate after a recoverable save failure."""

        self._state_action(
            lambda backend: backend.retry_autosave(),
            "All organizer changes saved after retry.",
        )

    @Slot()
    def shutdown(self) -> None:
        """Flush, close owned resources, and acknowledge only a complete shutdown."""

        backend = self._backend
        if backend is None:
            self.shutdown_complete.emit()
            return
        try:
            backend.shutdown()
        except BaseException as error:
            self.failed.emit(sanitize_ui_failure(error))
            self.shutdown_refused.emit()
            return
        self._backend = None
        self.shutdown_complete.emit()

    def _page_action(
        self,
        page_id: object,
        callback: Callable[[OrganizerBackend, UUID], OrganizerViewState],
    ) -> None:
        def action(backend: OrganizerBackend) -> None:
            if type(page_id) is not UUID:
                raise TypeError
            state = callback(backend, page_id)
            self.state_changed.emit(state)
            self.status_changed.emit("Organizer change queued. Saving changes.")

        self._with_backend(action)

    def _state_action(
        self,
        callback: Callable[[OrganizerBackend], OrganizerViewState],
        message: str,
    ) -> None:
        def action(backend: OrganizerBackend) -> None:
            state = callback(backend)
            self.state_changed.emit(state)
            self.status_changed.emit(message)

        self._with_backend(action)

    def _emit_cases(self, backend: OrganizerBackend | None = None) -> None:
        selected = self._backend if backend is None else backend
        if selected is None:
            raise RuntimeError
        cases: CaseIndex = selected.list_cases()
        self.cases_changed.emit(cases)

    def _source_progress(self, operation_id: int, progress: SourceImportProgress) -> None:
        phase = progress.phase.value.replace("_", " ")
        self.progress_changed.emit(
            UiProgress(
                operation="Import",
                phase=phase,
                completed=progress.completed_units,
                total=progress.total_units,
                cancelable=True,
                operation_id=operation_id,
            )
        )

    def _thumbnail_progress(self, operation_id: int, progress: PdfThumbnailProgress) -> None:
        self.progress_changed.emit(
            UiProgress(
                operation="Preview",
                phase=progress.phase.value,
                completed=progress.completed_pages,
                total=progress.total_pages,
                cancelable=True,
                operation_id=operation_id,
            )
        )

    def _run_long(
        self,
        operation_id: int,
        operation_name: str,
        callback: Callable[[OrganizerBackend], None],
    ) -> None:
        if type(operation_id) is not int or operation_id <= 0:
            self.failed.emit(
                UiFailure(
                    code="PWS_UI_REQUEST_INVALID",
                    message="PaperWorkShell rejected an invalid operation request.",
                    retryable=True,
                )
            )
            return
        self.operation_started.emit(operation_id, operation_name)
        self._with_backend(callback, operation_id=operation_id)
        self.operation_finished.emit(operation_id)

    def _with_backend(
        self,
        callback: Callable[[OrganizerBackend], None],
        *,
        operation_id: int | None = None,
    ) -> None:
        backend = self._backend
        if backend is None:
            self.failed.emit(
                UiFailure(
                    code="PWS_UI_NOT_READY",
                    message="PaperWorkShell is still starting. Try the action again.",
                    retryable=True,
                    operation_id=operation_id,
                )
            )
            return
        self._run(lambda: callback(backend), operation_id=operation_id)

    def _run(
        self,
        callback: Callable[[], None],
        *,
        operation_id: int | None = None,
    ) -> None:
        try:
            callback()
        except SourceIngestError as error:
            if error.code is SourceIngestErrorCode.CANCELLED:
                if operation_id is not None:
                    self.operation_cancelled.emit(
                        operation_id,
                        "Import cancelled. No source was committed.",
                    )
                else:
                    self.failed.emit(sanitize_ui_failure(error))
                return
            self.failed.emit(sanitize_ui_failure(error, operation_id=operation_id))
        except PdfThumbnailError as error:
            if error.code is PdfThumbnailErrorCode.CANCELLED:
                if operation_id is not None:
                    self.operation_cancelled.emit(operation_id, "Preview update cancelled.")
                else:
                    self.failed.emit(sanitize_ui_failure(error))
                return
            self.failed.emit(sanitize_ui_failure(error, operation_id=operation_id))
        except BaseException as error:
            self.failed.emit(sanitize_ui_failure(error, operation_id=operation_id))


class OrganizerBackendCommands(QObject):
    """Queued command surface owned by the GUI thread."""

    initialize_requested = Signal()
    list_cases_requested = Signal()
    create_case_requested = Signal()
    open_case_requested = Signal(object)
    close_case_requested = Signal()
    import_requested = Signal(int, object, str, object)
    thumbnails_requested = Signal(int, int, object, int, int, object)
    rotate_requested = Signal(object)
    remove_requested = Signal(object)
    move_requested = Signal(object, int)
    reorder_requested = Signal(object)
    undo_requested = Signal()
    redo_requested = Signal()
    flush_requested = Signal()
    retry_save_requested = Signal()
    shutdown_requested = Signal()

    def connect_worker(self, worker: OrganizerBackendWorker) -> None:
        """Connect every request to the backend thread using automatic queued delivery."""

        self.initialize_requested.connect(worker.initialize)
        self.list_cases_requested.connect(worker.list_cases)
        self.create_case_requested.connect(worker.create_case)
        self.open_case_requested.connect(worker.open_case)
        self.close_case_requested.connect(worker.close_case)
        self.import_requested.connect(worker.import_source)
        self.thumbnails_requested.connect(worker.get_thumbnails)
        self.rotate_requested.connect(worker.rotate_clockwise)
        self.remove_requested.connect(worker.remove_page)
        self.move_requested.connect(worker.move_page)
        self.reorder_requested.connect(worker.reorder_pages)
        self.undo_requested.connect(worker.undo)
        self.redo_requested.connect(worker.redo)
        self.flush_requested.connect(worker.flush_autosave)
        self.retry_save_requested.connect(worker.retry_autosave)
        self.shutdown_requested.connect(worker.shutdown)

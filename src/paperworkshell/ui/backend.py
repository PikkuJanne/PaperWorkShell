"""Thread-confined Qt bridge for organizer application services."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from threading import Event, Lock
from typing import Final, Protocol
from uuid import UUID

from PySide6.QtCore import QObject, Signal, Slot

from paperworkshell.app.ocr_languages import OcrLanguageSnapshot
from paperworkshell.app.organizer import OrganizerBackend, OrganizerViewState
from paperworkshell.app.page_previews import (
    PagePreviewError,
    PagePreviewErrorCode,
    PagePreviewProgress,
)
from paperworkshell.app.scan_review import (
    ScanReviewDraft,
    ScanReviewError,
    ScanReviewPreviewVariant,
)
from paperworkshell.domain.case_lifecycle import CaseIndex, CaseLifecycleError
from paperworkshell.domain.image_pages import ImagePageError, ImagePageErrorCode
from paperworkshell.domain.image_transforms import ImageTransformIntent
from paperworkshell.domain.manifest_repository import ManifestRepositoryError
from paperworkshell.domain.ocr import OcrError
from paperworkshell.domain.ocr_languages import (
    resolve_ocr_language_set,
    select_ocr_language_set,
)
from paperworkshell.domain.page_operations import PageOperationError
from paperworkshell.domain.pdf_thumbnails import (
    PdfThumbnailError,
    PdfThumbnailErrorCode,
)
from paperworkshell.domain.recovery import RecoveryError
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


class ShutdownRefusalKind(StrEnum):
    """Closed recovery actions after a backend declines desktop shutdown."""

    SAVE_PENDING = "save_pending"
    CLEANUP_RETRY = "cleanup_retry"


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
    ImagePageError,
    ManifestRepositoryError,
    OcrError,
    PagePreviewError,
    PageOperationError,
    PdfThumbnailError,
    RecoveryError,
    ScanReviewError,
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
            retryable=isinstance(
                error,
                (
                    ImagePageError,
                    ManifestRepositoryError,
                    PagePreviewError,
                    PdfThumbnailError,
                ),
            ),
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
    startup_recovery_changed = Signal(object)
    cases_changed = Signal(object)
    ocr_languages_changed = Signal(int, object)
    ocr_languages_failed = Signal(int, object)
    state_changed = Signal(object)
    thumbnails_ready = Signal(int, int, object)
    scan_review_preview_ready = Signal(int, int, object)
    scan_review_started = Signal(int, object)
    scan_review_start_failed = Signal(int, object)
    scan_review_progress_changed = Signal(object)
    scan_review_preview_cancelled = Signal(int, int, str)
    scan_review_preview_failed = Signal(int, int, object)
    scan_review_preview_finished = Signal(int, int)
    scan_review_applied = Signal(int, object)
    scan_review_apply_failed = Signal(int, object)
    scan_review_discard_failed = Signal(object)
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
    shutdown_refused = Signal(object)

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
            self.startup_recovery_changed.emit(self._backend.startup_recovery_result)
        except BaseException as error:
            self.failed.emit(sanitize_ui_failure(error))

    @Slot()
    def list_cases(self) -> None:
        """Publish a deterministic path-free case index."""

        self._run(self._emit_cases)

    @Slot(int)
    def refresh_ocr_languages(self, request_id: int) -> None:
        """Publish one correlated local-only OCR language snapshot."""

        self._run_ocr_language_action(
            request_id,
            lambda backend: backend.refresh_ocr_languages(),
        )

    @Slot(int, object)
    def select_ocr_language_codes(self, request_id: int, language_codes: object) -> None:
        """Retain one correlated canonical selection without mutating a case."""

        def action(backend: OrganizerBackend) -> OcrLanguageSnapshot:
            if type(language_codes) is not tuple:
                raise TypeError
            language_set_id = select_ocr_language_set(language_codes)
            canonical_codes = resolve_ocr_language_set(language_set_id)
            snapshot = backend.select_ocr_language_codes(language_codes)
            if (
                type(snapshot) is not OcrLanguageSnapshot
                or snapshot.selected_language_set_id != language_set_id
                or snapshot.selected_language_codes != canonical_codes
                or not snapshot.selected_available
            ):
                raise TypeError
            return snapshot

        self._run_ocr_language_action(request_id, action)

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
                f"Preview update complete for {len(results)} pages.",
            )

        self._run_long(operation_id, "Preview", action)

    @Slot(int, object, object)
    def begin_scan_review(
        self,
        operation_id: int,
        page_id: object,
        suggested_intent: object,
    ) -> None:
        """Create one revision-bound provisional review on the backend owner thread."""

        backend = self._backend
        if (
            backend is None
            or type(operation_id) is not int
            or operation_id <= 0
            or type(page_id) is not UUID
            or (suggested_intent is not None and type(suggested_intent) is not ImageTransformIntent)
        ):
            self.scan_review_start_failed.emit(
                operation_id if type(operation_id) is int else 0,
                UiFailure(
                    code="PWS_UI_REQUEST_INVALID",
                    message="PaperWorkShell rejected an invalid scan-review request.",
                    retryable=True,
                    operation_id=operation_id if type(operation_id) is int else None,
                ),
            )
            return
        try:
            draft = backend.begin_scan_review(page_id, suggested_intent=suggested_intent)
            expected_candidate = (
                draft.base_intent
                if suggested_intent is None and type(draft) is ScanReviewDraft
                else suggested_intent
            )
            if (
                type(draft) is not ScanReviewDraft
                or draft.page_id != page_id
                or draft.candidate_intent != expected_candidate
                or draft.suggestion_present != (suggested_intent is not None)
            ):
                if type(draft) is ScanReviewDraft:
                    try:
                        backend.cancel_scan_review(draft)
                    except BaseException:
                        pass
                self.scan_review_start_failed.emit(
                    operation_id,
                    UiFailure(
                        code="PWS_UI_SCAN_REVIEW_RESULT_INVALID",
                        message="PaperWorkShell could not verify the scan-review request safely.",
                        retryable=True,
                        operation_id=operation_id,
                    ),
                )
                return
        except BaseException as error:
            self.scan_review_start_failed.emit(
                operation_id,
                sanitize_ui_failure(error, operation_id=operation_id),
            )
            return
        self.scan_review_started.emit(operation_id, draft)

    @Slot(int, int, object, object, int, int, object)
    def get_scan_review_preview(
        self,
        operation_id: int,
        generation: int,
        draft: object,
        variant: object,
        maximum_width: int,
        maximum_height: int,
        cancellation: object,
    ) -> None:
        """Render one correlated provisional review preview without manifest mutation."""

        backend = self._backend
        if (
            backend is None
            or type(operation_id) is not int
            or operation_id <= 0
            or type(generation) is not int
            or generation <= 0
            or type(draft) is not ScanReviewDraft
            or type(variant) is not ScanReviewPreviewVariant
            or type(maximum_width) is not int
            or type(maximum_height) is not int
            or type(cancellation) is not OperationCancellation
        ):
            self.scan_review_preview_failed.emit(
                operation_id if type(operation_id) is int else 0,
                generation if type(generation) is int else 0,
                UiFailure(
                    code="PWS_UI_REQUEST_INVALID",
                    message="PaperWorkShell rejected an invalid scan-review preview request.",
                    retryable=True,
                    operation_id=operation_id if type(operation_id) is int else None,
                ),
            )
            self.scan_review_preview_finished.emit(
                operation_id if type(operation_id) is int else 0,
                generation if type(generation) is int else 0,
            )
            return
        try:
            result = backend.get_scan_review_preview(
                draft,
                variant,
                maximum_width,
                maximum_height,
                cancellation=cancellation.cancelled,
                progress=lambda progress: self._scan_review_progress(operation_id, progress),
            )
            self.scan_review_preview_ready.emit(operation_id, generation, result)
        except (ImagePageError, PagePreviewError) as error:
            cancelled = (
                isinstance(error, ImagePageError) and error.code is ImagePageErrorCode.CANCELLED
            ) or (
                isinstance(error, PagePreviewError) and error.code is PagePreviewErrorCode.CANCELLED
            )
            if cancelled:
                self.scan_review_preview_cancelled.emit(
                    operation_id,
                    generation,
                    "Preview cancelled. Adjustments were not applied.",
                )
            else:
                self.scan_review_preview_failed.emit(
                    operation_id,
                    generation,
                    sanitize_ui_failure(error, operation_id=operation_id),
                )
        except BaseException as error:
            self.scan_review_preview_failed.emit(
                operation_id,
                generation,
                sanitize_ui_failure(error, operation_id=operation_id),
            )
        finally:
            self.scan_review_preview_finished.emit(operation_id, generation)

    @Slot(int, object)
    def apply_scan_review(self, operation_id: int, draft: object) -> None:
        """Apply one exact reviewed draft and publish its queued organizer state."""

        backend = self._backend
        if (
            backend is None
            or type(operation_id) is not int
            or operation_id <= 0
            or type(draft) is not ScanReviewDraft
        ):
            self.scan_review_apply_failed.emit(
                operation_id if type(operation_id) is int else 0,
                UiFailure(
                    code="PWS_UI_REQUEST_INVALID",
                    message="PaperWorkShell rejected an invalid scan-review apply request.",
                    retryable=True,
                    operation_id=operation_id if type(operation_id) is int else None,
                ),
            )
            return
        try:
            before = backend.state
            state = backend.apply_scan_review(draft)
            if not _scan_review_apply_transition_is_valid(before, state, draft):
                self.scan_review_apply_failed.emit(
                    operation_id,
                    UiFailure(
                        code="PWS_UI_SCAN_REVIEW_RESULT_INVALID",
                        message="PaperWorkShell could not verify the scan-review result safely.",
                        retryable=True,
                        operation_id=operation_id,
                    ),
                )
                return
        except BaseException as error:
            self.scan_review_apply_failed.emit(
                operation_id,
                sanitize_ui_failure(error, operation_id=operation_id),
            )
            return
        self.state_changed.emit(state)
        self.status_changed.emit(
            "Scan adjustments applied. Saving changes."
            if state.autosave_pending
            else "Scan review complete. No changes were applied."
        )
        self.scan_review_applied.emit(operation_id, state)

    @Slot(object)
    def cancel_scan_review(self, draft: object) -> None:
        """Discard one provisional review after any active worker call settles."""

        backend = self._backend
        if backend is None or type(draft) is not ScanReviewDraft:
            self.scan_review_discard_failed.emit(
                UiFailure(
                    code="PWS_UI_REQUEST_INVALID",
                    message="PaperWorkShell could not discard the scan review safely.",
                    retryable=True,
                )
            )
            return
        try:
            backend.cancel_scan_review(draft)
        except BaseException as error:
            self.scan_review_discard_failed.emit(sanitize_ui_failure(error))

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
            state = backend.state
            refusal = (
                ShutdownRefusalKind.SAVE_PENDING
                if state.autosave_pending
                else ShutdownRefusalKind.CLEANUP_RETRY
            )
            self.failed.emit(sanitize_ui_failure(error))
            if refusal is ShutdownRefusalKind.CLEANUP_RETRY:
                self.state_changed.emit(state)
            self.shutdown_refused.emit(refusal)
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

    def _run_ocr_language_action(
        self,
        request_id: int,
        callback: Callable[[OrganizerBackend], OcrLanguageSnapshot],
    ) -> None:
        if type(request_id) is not int or request_id <= 0 or not callable(callback):
            self.ocr_languages_failed.emit(
                request_id,
                UiFailure(
                    code="PWS_UI_REQUEST_INVALID",
                    message="PaperWorkShell rejected an invalid OCR language request.",
                    retryable=True,
                    operation_id=(
                        request_id if type(request_id) is int and request_id > 0 else None
                    ),
                ),
            )
            return
        backend = self._backend
        if backend is None:
            self.ocr_languages_failed.emit(
                request_id,
                UiFailure(
                    code="PWS_UI_NOT_READY",
                    message="PaperWorkShell is still starting. Try the action again.",
                    retryable=True,
                    operation_id=request_id,
                ),
            )
            return
        try:
            snapshot = callback(backend)
            if type(snapshot) is not OcrLanguageSnapshot:
                raise TypeError
        except BaseException as error:
            self.ocr_languages_failed.emit(
                request_id,
                sanitize_ui_failure(error, operation_id=request_id),
            )
            return
        self.ocr_languages_changed.emit(request_id, snapshot)

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

    def _thumbnail_progress(self, operation_id: int, progress: PagePreviewProgress) -> None:
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

    def _scan_review_progress(self, operation_id: int, progress: PagePreviewProgress) -> None:
        self.scan_review_progress_changed.emit(
            UiProgress(
                operation="Scan review",
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
        except ImagePageError as error:
            if error.code is ImagePageErrorCode.CANCELLED:
                if operation_id is not None:
                    self.operation_cancelled.emit(operation_id, "Preview update cancelled.")
                else:
                    self.failed.emit(sanitize_ui_failure(error))
                return
            self.failed.emit(sanitize_ui_failure(error, operation_id=operation_id))
        except PagePreviewError as error:
            if error.code is PagePreviewErrorCode.CANCELLED:
                if operation_id is not None:
                    self.operation_cancelled.emit(operation_id, "Preview update cancelled.")
                else:
                    self.failed.emit(sanitize_ui_failure(error))
                return
            self.failed.emit(sanitize_ui_failure(error, operation_id=operation_id))
        except BaseException as error:
            self.failed.emit(sanitize_ui_failure(error, operation_id=operation_id))


def _scan_review_apply_transition_is_valid(
    before: object,
    after: object,
    draft: ScanReviewDraft,
) -> bool:
    """Accept only the complete organizer-state transition produced by one Apply."""

    if type(before) is not OrganizerViewState or type(after) is not OrganizerViewState:
        return False
    if (
        before.case_id != draft.case_id
        or before.revision != draft.manifest_revision
        or before.autosave_pending
    ):
        return False
    matching_before = tuple(page for page in before.pages if page.page_id == draft.page_id)
    if (
        len(matching_before) != 1
        or matching_before[0].scan_review_intent != draft.base_intent
        or matching_before[0].rotation_degrees != int(draft.base_intent.additional_rotation)
    ):
        return False
    changes_applied = draft.candidate_intent != draft.base_intent
    if not changes_applied:
        return after == before
    if (
        after.case_id != before.case_id
        or after.revision != before.revision
        or after.recovery_review_required is not before.recovery_review_required
        or not after.can_undo
        or after.can_redo
        or not after.autosave_pending
        or len(after.pages) != len(before.pages)
    ):
        return False
    for before_page, after_page in zip(before.pages, after.pages, strict=True):
        if before_page.page_id != draft.page_id:
            if after_page != before_page:
                return False
            continue
        if (
            after_page.page_id != before_page.page_id
            or after_page.position != before_page.position
            or after_page.preview_supported is not before_page.preview_supported
            or after_page.rotation_degrees != int(draft.candidate_intent.additional_rotation)
            or after_page.scan_review_intent != draft.candidate_intent
        ):
            return False
    return True


class OrganizerBackendCommands(QObject):
    """Queued command surface owned by the GUI thread."""

    initialize_requested = Signal()
    list_cases_requested = Signal()
    ocr_languages_refresh_requested = Signal(int)
    ocr_languages_select_requested = Signal(int, object)
    create_case_requested = Signal()
    open_case_requested = Signal(object)
    close_case_requested = Signal()
    import_requested = Signal(int, object, str, object)
    thumbnails_requested = Signal(int, int, object, int, int, object)
    scan_review_begin_requested = Signal(int, object, object)
    scan_review_preview_requested = Signal(int, int, object, object, int, int, object)
    scan_review_apply_requested = Signal(int, object)
    scan_review_cancel_requested = Signal(object)
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
        self.ocr_languages_refresh_requested.connect(worker.refresh_ocr_languages)
        self.ocr_languages_select_requested.connect(worker.select_ocr_language_codes)
        self.create_case_requested.connect(worker.create_case)
        self.open_case_requested.connect(worker.open_case)
        self.close_case_requested.connect(worker.close_case)
        self.import_requested.connect(worker.import_source)
        self.thumbnails_requested.connect(worker.get_thumbnails)
        self.scan_review_begin_requested.connect(worker.begin_scan_review)
        self.scan_review_preview_requested.connect(worker.get_scan_review_preview)
        self.scan_review_apply_requested.connect(worker.apply_scan_review)
        self.scan_review_cancel_requested.connect(worker.cancel_scan_review)
        self.rotate_requested.connect(worker.rotate_clockwise)
        self.remove_requested.connect(worker.remove_page)
        self.move_requested.connect(worker.move_page)
        self.reorder_requested.connect(worker.reorder_pages)
        self.undo_requested.connect(worker.undo)
        self.redo_requested.connect(worker.redo)
        self.flush_requested.connect(worker.flush_autosave)
        self.retry_save_requested.connect(worker.retry_autosave)
        self.shutdown_requested.connect(worker.shutdown)

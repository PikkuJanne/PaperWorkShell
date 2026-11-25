"""Thread-confined Qt bridge for organizer application services."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from threading import Event, Lock
from time import monotonic
from typing import Final, Protocol, cast
from uuid import UUID

from PySide6.QtCore import QObject, Signal, Slot

from paperworkshell.app.checklist import (
    ChecklistEditorState,
    is_checklist_announcement_safe,
)
from paperworkshell.app.core_overlay_tools import CoreOverlayToolsState
from paperworkshell.app.ocr_languages import OcrLanguageSnapshot
from paperworkshell.app.ordinary_pdf_export import OrdinaryPdfExportError
from paperworkshell.app.organizer import (
    OrganizerBackend,
    OrganizerSignatureImageDeleteOutcome,
    OrganizerSignatureImagePlacementOutcome,
    OrganizerViewState,
)
from paperworkshell.app.package_pdf_export import PackagePdfCandidateError
from paperworkshell.app.package_workflow import PackageReviewState, PackageWorkflowError
from paperworkshell.app.page_previews import (
    PagePreviewError,
    PagePreviewErrorCode,
    PagePreviewProgress,
    PagePreviewProgressPhase,
)
from paperworkshell.app.portal_constraints import (
    ConstraintEditorError,
    ConstraintEditorState,
    is_constraint_announcement_safe,
)
from paperworkshell.app.quality_analysis import (
    QualityAnalysisError,
    QualityAnalysisErrorCode,
    QualityAnalysisProgress,
    QualityReviewSnapshot,
)
from paperworkshell.app.redaction_tools import RedactionToolsState
from paperworkshell.app.removal_export_review import RemovalExportReview
from paperworkshell.app.scan_review import (
    ScanReviewDraft,
    ScanReviewError,
    ScanReviewPreviewVariant,
)
from paperworkshell.app.signature_images import (
    SIGNATURE_IMAGE_WORKFLOW_TIMEOUT_SECONDS,
    SignatureImagePlacementChoice,
    SignatureImagePlacementDraft,
    SignatureImagePlacementRequest,
)
from paperworkshell.app.trace_results import TraceExportEvidence, trace_export_evidence
from paperworkshell.diagnostics import phase_observer
from paperworkshell.domain.assisted_use import (
    AssistedUseError,
    AssistedUseErrorCode,
    AssistedUseSnapshot,
    AssistedUseStatus,
)
from paperworkshell.domain.case_lifecycle import CaseIndex, CaseLifecycleError
from paperworkshell.domain.checklist import ChecklistError
from paperworkshell.domain.compression_review import CompressionPagePreview, CompressionReviewState
from paperworkshell.domain.image_pages import ImagePageError, ImagePageErrorCode
from paperworkshell.domain.image_transforms import ImageTransformIntent
from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.manifest_repository import ManifestRepositoryError
from paperworkshell.domain.ocr import OcrError
from paperworkshell.domain.ocr_languages import (
    resolve_ocr_language_set,
    select_ocr_language_set,
)
from paperworkshell.domain.package_export_errors import PackageExportError
from paperworkshell.domain.package_summary import PackageSummary, PackageSummaryStatus
from paperworkshell.domain.page_operations import PageOperationError
from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportProgress,
    PdfExportProgressPhase,
)
from paperworkshell.domain.pdf_thumbnails import (
    PdfThumbnailError,
    PdfThumbnailErrorCode,
)
from paperworkshell.domain.post_export_deletion import (
    PostExportDeletionError,
    PostExportDeletionErrorCode,
    reject_post_export_deletion,
)
from paperworkshell.domain.recovery import RecoveryError
from paperworkshell.domain.redaction import RedactionError, RedactionErrorCode
from paperworkshell.domain.redaction_export import RedactionExportReport
from paperworkshell.domain.redaction_verification import runtime_release_qualified
from paperworkshell.domain.searchable_pdf_export import SearchablePdfExportError
from paperworkshell.domain.signature_images import (
    SignatureImageError,
    SignatureImageErrorCode,
    SignatureImageProgress,
)
from paperworkshell.domain.signed_source_policy import SignedSourcePolicyError
from paperworkshell.domain.source_ingest import (
    SourceImportProgress,
    SourceIngestError,
    SourceIngestErrorCode,
)
from paperworkshell.domain.target_size_search import TargetSizeColorChoice
from paperworkshell.domain.text_companion import TextCompanionError
from paperworkshell.domain.trace_recipe import (
    TraceOutputStatus,
    TraceRecipeError,
    TraceRecipeErrorCode,
)
from paperworkshell.localization.catalog import mark, tr
from paperworkshell.overlays.exporting import (
    FlattenedPdfExportError,
    FlattenedPdfExportErrorCode,
)
from paperworkshell.overlays.models import OverlayError, OverlayErrorCode
from paperworkshell.overlays.signature_commands import SignatureAssetDeletePolicy
from paperworkshell.preflight.models import PreflightError
from paperworkshell.preflight.portal_constraints import PortalOutputMode
from paperworkshell.storage.trace_files import (
    inspect_trace_output,
    load_trace_recipe,
    save_trace_recipe,
)
from paperworkshell.templates.models import TemplateLoadError


class OrganizerBackendFactory(Protocol):
    """Construct the complete backend on its permanent owner thread."""

    def __call__(self) -> OrganizerBackend: ...


class _SanitizableFailure(Protocol):
    code: StrEnum
    context: str
    safe_message: str


@dataclass(frozen=True, slots=True)
class UiFailure:
    """Stable path-free failure allowed to cross into presentation code."""

    code: str
    message: str
    retryable: bool
    operation_id: int | None = None
    affected_page_number: int | None = None


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
    PostExportDeletionError,
    AssistedUseError,
    CaseLifecycleError,
    ChecklistError,
    ConstraintEditorError,
    ImagePageError,
    ManifestRepositoryError,
    OcrError,
    PagePreviewError,
    PageOperationError,
    OverlayError,
    PackageExportError,
    PackagePdfCandidateError,
    PackageWorkflowError,
    PdfExportError,
    PdfThumbnailError,
    PreflightError,
    RecoveryError,
    RedactionError,
    SignedSourcePolicyError,
    ScanReviewError,
    SignatureImageError,
    SourceIngestError,
    TemplateLoadError,
)


def sanitize_ui_failure(
    error: BaseException,
    *,
    operation_id: int | None = None,
) -> UiFailure:
    """Map only known typed failures; never stringify an unknown exception."""

    if type(error) is TraceRecipeError:
        rebuilt_recipe = TraceRecipeError(error.code, error.unsupported_operations)
        return UiFailure(error.code.value, rebuilt_recipe.safe_message, True, operation_id)
    if type(error) is OrdinaryPdfExportError:
        rebuilt_ordinary = OrdinaryPdfExportError(error.code)
        return UiFailure(error.code.value, rebuilt_ordinary.safe_message, True, operation_id)
    if type(error) in {SearchablePdfExportError, FlattenedPdfExportError}:
        if isinstance(error, SearchablePdfExportError):
            rebuilt_searchable = SearchablePdfExportError(
                error.code, error.context, error.output_page_number
            )
            return UiFailure(
                error.code.value,
                rebuilt_searchable.safe_message,
                True,
                operation_id,
                rebuilt_searchable.output_page_number,
            )
        if isinstance(error, FlattenedPdfExportError):
            rebuilt_flattened = FlattenedPdfExportError(error.code, error.context)
            return UiFailure(error.code.value, rebuilt_flattened.safe_message, True, operation_id)

    if type(error) is TextCompanionError:
        rebuilt_text = TextCompanionError(error.code)
        return UiFailure(rebuilt_text.code.value, rebuilt_text.safe_message, True, operation_id)

    if (
        type(error) is RedactionError
        and error.code is RedactionErrorCode.PIPELINE_DISABLED
        and runtime_release_qualified()
    ):
        return UiFailure(
            code=error.code.value,
            message=(
                tr(
                    "Saved removal marks require Export removal derivative. Ordinary export cannot apply or verify these marks."
                )
            ),
            retryable=False,
            operation_id=operation_id,
        )

    if type(error) is PackageWorkflowError:
        rebuilt_package = PackageWorkflowError(error.code)
        return UiFailure(
            code=rebuilt_package.code.value,
            message=rebuilt_package.safe_message,
            retryable=True,
            operation_id=operation_id,
        )
    if type(error) is OverlayError:
        try:
            rebuilt_overlay = OverlayError(error.code, error.context)
        except BaseException:
            rebuilt_overlay = OverlayError(OverlayErrorCode.MODEL_INVALID, "overlay.operation")
        return UiFailure(
            code=rebuilt_overlay.code.value,
            message=rebuilt_overlay.safe_message,
            retryable=False,
            operation_id=operation_id,
        )
    if type(error) is QualityAnalysisError:
        try:
            rebuilt = QualityAnalysisError(error.code, error.context)
        except BaseException:
            rebuilt = QualityAnalysisError(
                QualityAnalysisErrorCode.INTERNAL_ERROR,
                "quality.operation",
            )
        return UiFailure(
            code=rebuilt.code.value,
            message=rebuilt.safe_message,
            retryable=True,
            operation_id=operation_id,
        )
    if type(error) in _KNOWN_FAILURES:
        selected = cast(_SanitizableFailure, error)
        try:
            rebuilt_failure = cast(
                _SanitizableFailure,
                type(error)(selected.code, selected.context),
            )
        except BaseException:
            return UiFailure(
                code="PWS_UI_UNEXPECTED",
                message=(
                    tr(
                        "PaperWorkShell could not complete that action. Try again or restart the application."
                    )
                ),
                retryable=True,
                operation_id=operation_id,
            )
        return UiFailure(
            code=rebuilt_failure.code.value,
            message=rebuilt_failure.safe_message,
            retryable=type(error)
            in {
                ImagePageError,
                ManifestRepositoryError,
                PagePreviewError,
                PackageExportError,
                PackagePdfCandidateError,
                PdfExportError,
                PdfThumbnailError,
                SignatureImageError,
            },
            operation_id=operation_id,
        )
    return UiFailure(
        code="PWS_UI_UNEXPECTED",
        message=tr(
            "PaperWorkShell could not complete that action. Try again or restart the application."
        ),
        retryable=True,
        operation_id=operation_id,
    )


def _core_overlay_apply_state_matches(
    editor_state: CoreOverlayToolsState,
    state: OrganizerViewState,
) -> bool:
    allowed_revisions = {
        editor_state.manifest_revision,
        editor_state.manifest_revision + 1,
    }
    return (
        state.case_id == editor_state.case_id
        and state.revision in allowed_revisions
        and (not editor_state.changed or state.revision == editor_state.manifest_revision + 1)
        and not state.autosave_pending
        and any(page.page_id == editor_state.page_id for page in state.pages)
    )


def _redaction_save_state_matches(
    editor_state: RedactionToolsState,
    state: OrganizerViewState,
) -> bool:
    allowed_revisions = {
        editor_state.manifest_revision,
        editor_state.manifest_revision + 1,
    }
    return (
        state.case_id == editor_state.case_id
        and state.revision in allowed_revisions
        and (not editor_state.changed or state.revision == editor_state.manifest_revision + 1)
        and not state.autosave_pending
        and any(page.page_id == editor_state.page_id for page in state.pages)
    )


def _checklist_apply_state_matches(
    editor_state: ChecklistEditorState,
    state: OrganizerViewState,
) -> bool:
    """Verify one checklist save result without trusting presentation strings."""

    allowed_revisions = {
        editor_state.manifest_revision,
        editor_state.manifest_revision + 1,
    }
    return (
        state.case_id == editor_state.case_id
        and state.revision in allowed_revisions
        and (not editor_state.changed or state.revision == editor_state.manifest_revision + 1)
        and not state.autosave_pending
    )


def _portal_constraints_apply_state_matches(
    editor_state: ConstraintEditorState,
    state: OrganizerViewState,
) -> bool:
    """Verify one constraint save without trusting filename presentation values."""

    allowed_revisions = {
        editor_state.manifest_revision,
        editor_state.manifest_revision + 1,
    }
    return (
        state.case_id == editor_state.case_id
        and state.revision in allowed_revisions
        and (not editor_state.changed or state.revision == editor_state.manifest_revision + 1)
        and not state.autosave_pending
    )


class OrganizerBackendWorker(QObject):
    """Serialize backend and worker mutations on one long-lived Qt thread."""

    initialized = Signal()
    startup_recovery_changed = Signal(object)
    cases_changed = Signal(object)
    ocr_languages_changed = Signal(int, object)
    ocr_languages_failed = Signal(int, object)
    state_changed = Signal(object)
    first_png_import_saved = Signal(int, object, object, object)
    thumbnails_ready = Signal(int, int, object)
    quality_review_ready = Signal(int, int, object)
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
    core_overlay_tools_started = Signal(int, object)
    core_overlay_tools_start_failed = Signal(int, object)
    core_overlay_tools_applied = Signal(int, object)
    core_overlay_tools_apply_failed = Signal(int, object)
    core_overlay_tools_cancelled = Signal(int)
    core_overlay_tools_cancel_failed = Signal(int, object)
    redaction_tools_started = Signal(int, object)
    redaction_tools_start_failed = Signal(int, object)
    redaction_tools_saved = Signal(int, object)
    redaction_tools_save_failed = Signal(int, object)
    redaction_tools_cancelled = Signal(int)
    redaction_tools_cancel_failed = Signal(int, object)
    removal_export_review_ready = Signal(int, object)
    removal_export_ready = Signal(int, object)
    checklist_started = Signal(int, object)
    checklist_start_failed = Signal(int, object)
    checklist_applied = Signal(int, object)
    checklist_apply_failed = Signal(int, object)
    checklist_cancelled = Signal(int)
    checklist_cancel_failed = Signal(int, object)
    portal_constraints_started = Signal(int, object)
    portal_constraints_start_failed = Signal(int, object)
    portal_constraints_applied = Signal(int, object)
    portal_constraints_apply_failed = Signal(int, object)
    portal_constraints_cancelled = Signal(int)
    portal_constraints_cancel_failed = Signal(int, object)
    package_review_ready = Signal(int, object)
    package_export_ready = Signal(int, object)
    package_review_cancelled = Signal(int)
    package_summary_saved = Signal(int)
    text_companion_saved = Signal(int)
    post_export_deletion_review_ready = Signal(int, object)
    post_export_deletion_finished = Signal(int, object)
    post_export_failed = Signal(int, str, object)
    package_failed = Signal(int, str, object)
    package_progress = Signal(int, int, int)
    compression_ready = Signal(int, object)
    compression_preview_ready = Signal(int, object)
    signature_image_prepared = Signal(int, object)
    signature_image_prepare_failed = Signal(int, object)
    signature_image_placed = Signal(int, object)
    signature_image_place_failed = Signal(int, object)
    signature_image_cancelled = Signal(int)
    signature_image_cancel_failed = Signal(int, object)
    signature_image_placements_ready = Signal(int, object)
    signature_image_placements_failed = Signal(int, object)
    signature_image_deleted = Signal(int, object)
    signature_image_delete_failed = Signal(int, object)
    progress_changed = Signal(object)
    status_changed = Signal(str)
    operation_status_changed = Signal(int, str)
    case_reopen_required = Signal(int, str)
    review_required = Signal(str)
    operation_started = Signal(int, str)
    operation_cancelled = Signal(int, str)
    operation_finished = Signal(int)
    pages_pdf_export_saved = Signal(int, int)
    trace_pdf_export_ready = Signal(int, object)
    trace_export_inspected = Signal(int, object, object)
    source_preview_ready = Signal(int, object, object, object, bool)
    source_preview_failed = Signal(int, object, object, object, bool)
    failed = Signal(object)
    shutdown_complete = Signal()
    shutdown_refused = Signal(object)
    assisted_use_changed = Signal(object)
    assisted_check_finished = Signal()
    case_close_finished = Signal()
    assisted_unlock_failed = Signal(int, object)
    assisted_unlock_finished = Signal(int)
    assisted_unlocked = Signal(int, object)
    assisted_handover = Signal(object)

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

    @Slot(int)
    def show_ocr_languages(self, request_id: int) -> None:
        """Open the last checked presentation without redundant closure hashing."""

        self._run_ocr_language_action(
            request_id,
            lambda backend: backend.ocr_language_manager.review_snapshot(),
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
    @Slot(str)
    def create_case(self, case_name: str = "") -> None:
        """Create and open a case, then publish its empty organizer state."""

        def action(backend: OrganizerBackend) -> None:
            state = backend.create_case(case_name) if case_name else backend.create_case()
            self.state_changed.emit(state)
            self.status_changed.emit(
                tr("New case created. Import a document to begin organizing pages.")
            )

        self._with_backend(action)

    @Slot(str)
    def rename_case(self, case_name: str) -> None:
        """Save a new display name using the active case's manifest transaction."""

        def action(backend: OrganizerBackend) -> None:
            try:
                state = backend.rename_case(case_name)
            except ManifestRepositoryError:
                # Publish retained unsaved intent before the actionable failure;
                # the desktop must not keep displaying an obsolete saved state.
                self.state_changed.emit(backend.state)
                raise
            self.state_changed.emit(state)
            self._emit_cases(backend)
            self.status_changed.emit(tr("Case name saved."))

        self._with_backend(action)

    @Slot(object)
    def open_case(self, case_id: object) -> None:
        """Open one selected internal case identifier."""

        def action(backend: OrganizerBackend) -> None:
            if type(case_id) is not UUID:
                raise TypeError
            service = backend.assisted_use
            if service is not None:
                snapshot = service.inspect(case_id)
                if snapshot.status is not AssistedUseStatus.ORDINARY:
                    self.assisted_use_changed.emit(snapshot)
                    if snapshot.status is not AssistedUseStatus.UNLOCKED:
                        return
            state = backend.open_case(case_id)
            self.state_changed.emit(state)
            if state.recovery_review_required:
                self.review_required.emit(
                    tr(
                        "Case recovered from the validated save journal. Review the page organizer before continuing."
                    )
                )
            else:
                self.status_changed.emit(
                    tr("Case opened with {value1} active pages.", value1=len(state.pages))
                )

        self._with_backend(action)

    @Slot(int, str, bool)
    @Slot(int, str, bool, str)
    def create_assisted_case(
        self, retention_days: int, passphrase: str, consent: bool, case_name: str = ""
    ) -> None:
        """Record consent before emitting an import-capable organizer."""

        def action(backend: OrganizerBackend) -> None:
            state = backend.create_assisted_case(
                retention_days, passphrase, consent=consent, case_name=case_name
            )
            assert backend.assisted_use is not None
            self.assisted_use_changed.emit(backend.assisted_use.snapshot)
            self.cases_changed.emit(CaseIndex())
            self.state_changed.emit(state)
            self.status_changed.emit(
                tr("Assisted case created. Consent and expiry recorded locally.")
            )

        self._with_backend(action)

    @Slot(int, object, str, object)
    def unlock_assisted_case(
        self, request_id: int, case_id: object, passphrase: str, cancellation: object
    ) -> None:
        """Honor revocation before queued authentication and every exposure boundary.

        Only this request's closed failure or completion crosses back to the
        controller. A stale authentication error must not become a generic error
        announcement in a later unlocked case.
        """

        service = None
        try:
            backend = self._backend
            if backend is None or backend.assisted_use is None:
                raise TypeError
            service = backend.assisted_use
            if (
                type(request_id) is not int
                or request_id <= 0
                or type(case_id) is not UUID
                or type(cancellation) is not OperationCancellation
            ):
                raise TypeError
            if cancellation.cancelled():
                return
            snapshot = service.unlock(case_id, passphrase)
            if cancellation.cancelled():
                service.lock()
                return
            state = backend.open_case(case_id)
            if cancellation.cancelled():
                service.lock()
                return
            self.assisted_unlocked.emit(request_id, snapshot)
            self.cases_changed.emit(CaseIndex())
            self.state_changed.emit(state)
        except BaseException as error:
            if service is not None:
                service.lock()
            self.assisted_unlock_failed.emit(
                request_id,
                error.code
                if type(error) is AssistedUseError and type(error.code) is AssistedUseErrorCode
                else None,
            )
        finally:
            self.assisted_unlock_finished.emit(request_id)

    def lock_assisted_immediately(self) -> None:
        """Close the thread-safe memory gate without waiting for the worker event loop."""

        backend = self._backend
        if backend is not None and backend.assisted_use is not None:
            backend.assisted_use.lock()

    def current_assisted_snapshot(self) -> AssistedUseSnapshot | None:
        """Read only the immediate memory gate, never storage or a thread-affine worker."""

        backend = self._backend
        return (
            None
            if backend is None or backend.assisted_use is None
            else backend.assisted_use.snapshot
        )

    def assisted_activity(self) -> None:
        """Record local user input without filesystem access or queued work."""

        backend = self._backend
        if backend is not None and backend.assisted_use is not None:
            try:
                backend.assisted_use.activity()
            except AssistedUseError:
                pass

    @Slot(object)
    def check_assisted_use(self, case_id: object = None) -> None:
        """Persist bounded clock checkpoints and enforce expiry away from the GUI."""

        try:
            backend = self._backend
            if backend is None or backend.assisted_use is None:
                self.assisted_use_changed.emit(None)
                return
            try:
                if case_id is not None and type(case_id) is not UUID:
                    raise TypeError
                self.assisted_use_changed.emit(backend.assisted_use.check(case_id))
            except BaseException as error:
                backend.assisted_use.lock()
                self.assisted_use_changed.emit(backend.assisted_use.snapshot)
                self.failed.emit(sanitize_ui_failure(error))
        finally:
            self.assisted_check_finished.emit()

    @Slot()
    def close_case(self) -> None:
        """Flush and close the active case before returning to case selection."""

        def action(backend: OrganizerBackend) -> None:
            handover = backend.assisted_use.snapshot if backend.assisted_use is not None else None
            state = backend.close_case()
            self.assisted_use_changed.emit(None)
            self.state_changed.emit(state)
            self._emit_cases()
            if handover is not None:
                self.assisted_handover.emit(handover)
            self.status_changed.emit(tr("Case closed. All queued changes were saved."))

        try:
            self._with_backend(action)
        finally:
            self.case_close_finished.emit()

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
            result = outcome.committed_import
            saved_single_page = (
                not outcome.refresh_required
                and not state.autosave_pending
                and state.case_id == result.case_id
                and state.revision == result.manifest_revision
                and len(state.pages) == 1
                and result.page_ids == (state.pages[0].page_id,)
                and state.pages[0].position == 1
                and state.pages[0].source_page_number == 1
                and state.pages[0].rotation_degrees == 0
            )
            scope = phase_observer.current_scope()
            if scope is not None:
                observation, _role = scope
                if saved_single_page:
                    observation.import_refreshed(
                        state.case_id, result.source_id, state.revision, state.pages[0].page_id
                    )
                else:
                    observation.terminal = True
            self.state_changed.emit(state)
            if outcome.refresh_required:
                self.case_reopen_required.emit(operation_id, outcome.safe_message)
            else:
                if saved_single_page and result.media_type is DetectedMediaType.PNG:
                    self.first_png_import_saved.emit(
                        operation_id, result.case_id, result.manifest_revision, result.page_ids[0]
                    )
                self.operation_status_changed.emit(
                    operation_id,
                    tr(
                        "Import complete. The case now has {value1} active pages.",
                        value1=len(state.pages),
                    ),
                )

        observation = phase_observer.active
        if observation is not None and observation.import_id == operation_id:
            with phase_observer.backend_scope(observation, phase_observer.Role.INSPECTION):
                self._run_long(operation_id, "Import", action)
        else:
            self._run_long(operation_id, "Import", action)

    @Slot(int, object, object)
    def export_pages_pdf(
        self,
        operation_id: int,
        destination: object,
        cancellation: object,
    ) -> None:
        """Save a verified ordinary PDF on the serialized backend thread."""

        self._export_pages_pdf(operation_id, destination, cancellation, make_searchable=False)

    @Slot(int, object, object)
    def export_searchable_pdf(
        self, operation_id: int, destination: object, cancellation: object
    ) -> None:
        """Honor an explicit visible OCR choice without checklist assignments."""

        self._export_pages_pdf(operation_id, destination, cancellation, make_searchable=True)

    def _export_pages_pdf(
        self, operation_id: int, destination: object, cancellation: object, *, make_searchable: bool
    ) -> None:

        def action(backend: OrganizerBackend) -> None:
            if not isinstance(destination, Path) or type(cancellation) is not OperationCancellation:
                raise TypeError
            outcome = backend.export_pages_pdf(
                destination,
                cancellation=cancellation.cancelled,
                progress=lambda value: self._pdf_export_progress(operation_id, value),
                **({"make_searchable": True} if make_searchable else {}),
            )
            evidence = trace_export_evidence(outcome, destination, make_searchable=make_searchable)
            if evidence is not None:
                self.trace_pdf_export_ready.emit(operation_id, evidence)
            self.pages_pdf_export_saved.emit(
                operation_id, outcome.committed_export.report.page_count
            )
            self.state_changed.emit(outcome.state)
            if outcome.refresh_required:
                self.case_reopen_required.emit(operation_id, outcome.safe_message)
            else:
                self.operation_status_changed.emit(operation_id, outcome.safe_message)

        self._run_long(operation_id, tr("PDF export"), action)

    @Slot(int, object, object)
    def inspect_trace_export(
        self, operation_id: int, evidence: object, cancellation: object
    ) -> None:
        """Independently authenticate the selected result before the desktop opens it."""

        backend = self._backend
        if (
            type(evidence) is not TraceExportEvidence
            or type(cancellation) is not OperationCancellation
        ):
            return
        if backend is None or backend.state.case_id != evidence.case_id:
            status = TraceOutputStatus.UNAVAILABLE
        else:
            status = inspect_trace_output(
                evidence.destination, evidence.byte_size, evidence.sha256, cancellation.cancelled
            )
        self.trace_export_inspected.emit(operation_id, evidence, status)

    @Slot(int, object, str, object)
    def save_trace_recipe_file(
        self, operation_id: int, case_id: object, fingerprint: str, path: object
    ) -> None:
        """Save an exact plan after verifying the queued action is still current."""

        def action(backend: OrganizerBackend) -> None:
            state = backend.state
            plan = state.trace_plan
            if not isinstance(path, Path):
                raise TypeError
            if plan is None or state.case_id != case_id or plan.fingerprint != fingerprint:
                raise TraceRecipeError(TraceRecipeErrorCode.TARGET_CHANGED)
            save_trace_recipe(path, plan.recipe)
            self.operation_status_changed.emit(operation_id, tr("Recipe saved."))

        self._run_long(operation_id, tr("Save recipe"), action)

    @Slot(int, object, str, object)
    def apply_trace_recipe_file(
        self, operation_id: int, case_id: object, fingerprint: str, path: object
    ) -> None:
        """Read and atomically apply supported metadata to the bound case."""

        def action(backend: OrganizerBackend) -> None:
            state = backend.state
            plan = state.trace_plan
            if not isinstance(path, Path):
                raise TypeError
            if plan is None or state.case_id != case_id or plan.fingerprint != fingerprint:
                raise TraceRecipeError(TraceRecipeErrorCode.TARGET_CHANGED)
            state = backend.apply_trace_recipe(load_trace_recipe(path))
            self.state_changed.emit(state)
            self.operation_status_changed.emit(operation_id, tr("Recipe applied."))

        self._run_long(operation_id, tr("Apply recipe"), action)

    @Slot(int)
    def begin_removal_export(self, operation_id: int) -> None:
        def action(backend: OrganizerBackend) -> None:
            review = backend.begin_removal_export()
            if type(review) is not RemovalExportReview:
                raise TypeError
            review.__post_init__()
            self.state_changed.emit(backend.state)
            self.removal_export_review_ready.emit(operation_id, review)

        self._run_long(operation_id, tr("Removal review"), action)

    @Slot(int, object, object, bool, object)
    def export_removal_pdf(
        self,
        operation_id: int,
        review: object,
        destination: object,
        signed_source_confirmed: bool,
        cancellation: object,
    ) -> None:
        def action(backend: OrganizerBackend) -> None:
            if (
                type(review) is not RemovalExportReview
                or not isinstance(destination, Path)
                or type(signed_source_confirmed) is not bool
                or type(cancellation) is not OperationCancellation
            ):
                raise TypeError
            report = backend.export_removal_pdf(
                review,
                destination,
                signed_source_confirmed=signed_source_confirmed,
                cancellation=cancellation.cancelled,
                progress=lambda value: self._pdf_export_progress(
                    operation_id, value, operation_name=tr("Removal export")
                ),
                state_changed=self.state_changed.emit,
            )
            if type(report) is not RedactionExportReport:
                raise TypeError
            report.__post_init__()
            expected_revision = review.manifest_revision + int(
                review.signed_source_confirmation_required
            )
            if (
                report.case_id != review.case_id
                or report.manifest_revision != expected_revision
                or report.affected_page_ids != review.affected_page_ids
            ):
                raise TypeError
            self.removal_export_ready.emit(operation_id, report)
            self.operation_status_changed.emit(operation_id, report.safe_message)

        self._run_long(operation_id, tr("Removal export"), action)

    def _pdf_export_progress(
        self,
        operation_id: int,
        progress: PdfExportProgress,
        *,
        operation_name: str = mark("PDF export"),
    ) -> None:
        self.progress_changed.emit(
            UiProgress(
                operation=operation_name,
                phase=progress.phase.value.replace("_", " "),
                completed=progress.completed_pages,
                total=progress.total_pages,
                cancelable=progress.phase
                not in {
                    PdfExportProgressPhase.RECORDING,
                    PdfExportProgressPhase.COMPLETE,
                },
                operation_id=operation_id,
            )
        )

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
                tr("Preview update complete for {value1} pages.", value1=len(results)),
            )

        observation = phase_observer.active
        if observation is not None and observation.preview_id == (operation_id, generation):
            with phase_observer.backend_scope(observation, phase_observer.Role.PREVIEW):
                self._run_long(operation_id, "Preview", action)
        else:
            self._run_long(operation_id, "Preview", action)

    @Slot(int, object, object, int, int, object, bool)
    def get_trace_canvas_preview(
        self,
        operation_id: int,
        case_id: object,
        page_id: object,
        width: int,
        height: int,
        cancellation: object,
        source: bool,
    ) -> None:
        """Return one lens preview tied to the exact case, page, and request."""

        backend = self._backend
        if backend is None or type(case_id) is not UUID or type(page_id) is not UUID:
            return
        if type(cancellation) is not OperationCancellation or cancellation.cancelled():
            return
        try:
            if backend.state.case_id != case_id:
                raise TraceRecipeError(TraceRecipeErrorCode.TARGET_CHANGED)
            if source:
                result = backend.get_trace_source_preview(
                    case_id, page_id, width, height, cancellation=cancellation.cancelled
                )
            else:
                (result,) = backend.get_thumbnails(
                    width,
                    height,
                    page_ids=(page_id,),
                    cancellation=cancellation.cancelled,
                )
        except Exception as error:
            self.source_preview_failed.emit(
                operation_id,
                case_id,
                page_id,
                sanitize_ui_failure(error, operation_id=operation_id),
                source,
            )
            return
        self.source_preview_ready.emit(operation_id, case_id, page_id, result, source)

    @Slot(int, int, object, int, object)
    def analyze_quality(
        self,
        operation_id: int,
        generation: int,
        expected_case_id: object,
        expected_revision: int,
        cancellation: object,
    ) -> None:
        """Analyze one exact saved organizer revision away from the GUI thread."""

        def action(backend: OrganizerBackend) -> None:
            if (
                type(generation) is not int
                or generation <= 0
                or type(expected_case_id) is not UUID
                or type(expected_revision) is not int
                or type(cancellation) is not OperationCancellation
            ):
                raise TypeError
            before = backend.state
            if (
                before.case_id != expected_case_id
                or before.revision != expected_revision
                or before.autosave_pending
            ):
                raise QualityAnalysisError(
                    QualityAnalysisErrorCode.STALE,
                    "quality.ui_bridge_state",
                )
            snapshot = backend.analyze_quality(
                expected_case_id=expected_case_id,
                expected_revision=expected_revision,
                cancellation=cancellation.cancelled,
                progress=lambda value: self._quality_progress(operation_id, value),
            )
            after = backend.state
            if (
                type(snapshot) is not QualityReviewSnapshot
                or snapshot.case_id != before.case_id
                or snapshot.manifest_revision != before.revision
                or snapshot.page_ids != tuple(page.page_id for page in before.pages)
                or after != before
            ):
                raise QualityAnalysisError(
                    QualityAnalysisErrorCode.RESULT_INVALID,
                    "quality.ui_bridge_result",
                )
            self.quality_review_ready.emit(operation_id, generation, snapshot)
            finding_count = len(snapshot.findings)
            affected_page_count = len({finding.page_id for finding in snapshot.findings})
            if finding_count:
                finding_noun = tr("signal") if finding_count == 1 else tr("signals")
                page_noun = tr("page") if affected_page_count == 1 else tr("pages")
                message = tr(
                    "Page quality check finished. {value1} review {value2} on {value3} {value4}. These automatic suggestions need your review.",
                    value1=finding_count,
                    value2=finding_noun,
                    value3=affected_page_count,
                    value4=page_noun,
                )
            else:
                message = tr(
                    "Page quality check finished. No review signals were found by these limited checks. Review the pages yourself before export."
                )
            self.operation_status_changed.emit(operation_id, message)

        self._run_long(operation_id, tr("Page quality check"), action)

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
                    message=tr("PaperWorkShell rejected an invalid scan-review request."),
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
                        message=tr(
                            "PaperWorkShell could not verify the scan-review request safely."
                        ),
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
                    message=tr("PaperWorkShell rejected an invalid scan-review preview request."),
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
                    tr("Preview cancelled. Adjustments were not applied."),
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
                    message=tr("PaperWorkShell rejected an invalid scan-review apply request."),
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
                        message=tr(
                            "PaperWorkShell could not verify the scan-review result safely."
                        ),
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
                    message=tr("PaperWorkShell could not discard the scan review safely."),
                    retryable=True,
                )
            )
            return
        try:
            backend.cancel_scan_review(draft)
        except BaseException as error:
            self.scan_review_discard_failed.emit(sanitize_ui_failure(error))

    @Slot(int, object)
    def begin_core_overlay_tools(self, operation_id: int, page_id: object) -> None:
        """Open one exact keyboard form-mark draft on the backend thread."""

        if type(operation_id) is not int or operation_id <= 0 or type(page_id) is not UUID:
            self.core_overlay_tools_start_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_REQUEST_INVALID",
                    message=tr("PaperWorkShell rejected an invalid visual form-mark request."),
                    retryable=True,
                    operation_id=(
                        operation_id if type(operation_id) is int and operation_id > 0 else None
                    ),
                ),
            )
            return
        backend = self._backend
        if backend is None:
            self.core_overlay_tools_start_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_NOT_READY",
                    message=tr("PaperWorkShell is still starting. Try the action again."),
                    retryable=True,
                    operation_id=operation_id,
                ),
            )
            return
        try:
            state = backend.begin_core_overlay_tools(page_id)
            organizer_state = backend.state
            if (
                type(state) is not CoreOverlayToolsState
                or type(organizer_state) is not OrganizerViewState
            ):
                raise TypeError
        except BaseException as error:
            self.core_overlay_tools_start_failed.emit(
                operation_id,
                sanitize_ui_failure(error, operation_id=operation_id),
            )
            return
        # Beginning an editor can flush prior page intent. Publish that durable
        # revision before the revision-bound editor reaches the window.
        self.state_changed.emit(organizer_state)
        self.core_overlay_tools_started.emit(operation_id, state)

    @Slot(int, object)
    def apply_core_overlay_tools(self, operation_id: int, editor_state: object) -> None:
        """Persist one validated local form-mark draft and publish its organizer state."""

        if (
            type(operation_id) is not int
            or operation_id <= 0
            or type(editor_state) is not CoreOverlayToolsState
        ):
            self.core_overlay_tools_apply_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_REQUEST_INVALID",
                    message=tr("PaperWorkShell rejected invalid visual form-mark changes."),
                    retryable=True,
                    operation_id=(
                        operation_id if type(operation_id) is int and operation_id > 0 else None
                    ),
                ),
            )
            return
        backend = self._backend
        if backend is None:
            self.core_overlay_tools_apply_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_NOT_READY",
                    message=tr("PaperWorkShell is still starting. Try the action again."),
                    retryable=True,
                    operation_id=operation_id,
                ),
            )
            return
        try:
            state = backend.apply_core_overlay_tools(editor_state)
            current = backend.state
            if (
                type(state) is not OrganizerViewState
                or type(current) is not OrganizerViewState
                or state != current
                or not _core_overlay_apply_state_matches(editor_state, state)
            ):
                raise TypeError
        except BaseException as error:
            self.core_overlay_tools_apply_failed.emit(
                operation_id,
                sanitize_ui_failure(error, operation_id=operation_id),
            )
            return
        self.state_changed.emit(state)
        self.core_overlay_tools_applied.emit(operation_id, state)

    @Slot(int, object)
    def cancel_core_overlay_tools(self, operation_id: int, editor_state: object) -> None:
        """Release one matching draft without mutating or saving the case."""

        if (
            type(operation_id) is not int
            or operation_id <= 0
            or type(editor_state) is not CoreOverlayToolsState
        ):
            self.core_overlay_tools_cancel_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_REQUEST_INVALID",
                    message=tr("PaperWorkShell rejected an invalid visual form-mark cancellation."),
                    retryable=True,
                    operation_id=(
                        operation_id if type(operation_id) is int and operation_id > 0 else None
                    ),
                ),
            )
            return
        backend = self._backend
        if backend is None:
            self.core_overlay_tools_cancel_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_NOT_READY",
                    message=tr("PaperWorkShell is still starting. Try the action again."),
                    retryable=True,
                    operation_id=operation_id,
                ),
            )
            return
        try:
            backend.cancel_core_overlay_tools(editor_state)
        except BaseException as error:
            self.core_overlay_tools_cancel_failed.emit(
                operation_id,
                sanitize_ui_failure(error, operation_id=operation_id),
            )
            return
        self.core_overlay_tools_cancelled.emit(operation_id)

    @Slot(int)
    def abort_core_overlay_tools(self, operation_id: int) -> None:
        """Release a backend draft whose returned presentation state was unusable."""

        if type(operation_id) is not int or operation_id <= 0:
            self.core_overlay_tools_cancel_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_REQUEST_INVALID",
                    message=tr("PaperWorkShell rejected an invalid visual overlay abort request."),
                    retryable=True,
                    operation_id=(
                        operation_id if type(operation_id) is int and operation_id > 0 else None
                    ),
                ),
            )
            return
        backend = self._backend
        if backend is None:
            self.core_overlay_tools_cancel_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_NOT_READY",
                    message=tr("PaperWorkShell is still starting. Try the action again."),
                    retryable=True,
                    operation_id=operation_id,
                ),
            )
            return
        try:
            backend.abort_core_overlay_tools()
        except BaseException as error:
            self.core_overlay_tools_cancel_failed.emit(
                operation_id,
                sanitize_ui_failure(error, operation_id=operation_id),
            )
            return
        self.core_overlay_tools_cancelled.emit(operation_id)

    @Slot(int, object)
    def begin_redaction_tools(self, operation_id: int, page_id: object) -> None:
        """Open one exact redaction mark intent draft on the backend thread."""

        if type(operation_id) is not int or operation_id <= 0 or type(page_id) is not UUID:
            self.redaction_tools_start_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_REQUEST_INVALID",
                    message=tr("PaperWorkShell rejected an invalid redaction mark request."),
                    retryable=True,
                    operation_id=(
                        operation_id if type(operation_id) is int and operation_id > 0 else None
                    ),
                ),
            )
            return
        backend = self._backend
        if backend is None:
            self.redaction_tools_start_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_NOT_READY",
                    message=tr("PaperWorkShell is still starting. Try the action again."),
                    retryable=True,
                    operation_id=operation_id,
                ),
            )
            return
        try:
            state = backend.begin_redaction_tools(page_id)
            organizer_state = backend.state
            if (
                type(state) is not RedactionToolsState
                or type(organizer_state) is not OrganizerViewState
                or type(state.editor_session_id) is not UUID
                or state.case_id != organizer_state.case_id
                or state.manifest_revision != organizer_state.revision
                or state.page_id != page_id
                or organizer_state.autosave_pending
                or not any(page.page_id == page_id for page in organizer_state.pages)
            ):
                raise TypeError
        except BaseException as error:
            # A malformed begin result must not strand an exclusive editor
            # session that presentation code cannot safely identify or cancel.
            try:
                backend.abort_redaction_tools()
            except BaseException:
                pass
            self.redaction_tools_start_failed.emit(
                operation_id,
                sanitize_ui_failure(error, operation_id=operation_id),
            )
            return
        # Beginning an editor can flush prior page intent. Publish that durable
        # revision before the revision-bound editor reaches the window.
        self.state_changed.emit(organizer_state)
        self.redaction_tools_started.emit(operation_id, state)

    @Slot(int, object)
    def save_redaction_tools(self, operation_id: int, editor_state: object) -> None:
        """Persist one validated local redaction mark draft and publish its organizer state."""

        if (
            type(operation_id) is not int
            or operation_id <= 0
            or type(editor_state) is not RedactionToolsState
        ):
            self.redaction_tools_save_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_REQUEST_INVALID",
                    message=tr("PaperWorkShell rejected invalid redaction mark changes."),
                    retryable=True,
                    operation_id=(
                        operation_id if type(operation_id) is int and operation_id > 0 else None
                    ),
                ),
            )
            return
        backend = self._backend
        if backend is None:
            self.redaction_tools_save_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_NOT_READY",
                    message=tr("PaperWorkShell is still starting. Try the action again."),
                    retryable=True,
                    operation_id=operation_id,
                ),
            )
            return
        try:
            state = backend.save_redaction_tools(editor_state)
            current = backend.state
            if (
                type(state) is not OrganizerViewState
                or type(current) is not OrganizerViewState
                or state != current
                or not _redaction_save_state_matches(editor_state, state)
            ):
                raise TypeError
        except BaseException as error:
            self.redaction_tools_save_failed.emit(
                operation_id,
                sanitize_ui_failure(error, operation_id=operation_id),
            )
            return
        self.state_changed.emit(state)
        self.redaction_tools_saved.emit(operation_id, state)

    @Slot(int, object)
    def cancel_redaction_tools(self, operation_id: int, editor_state: object) -> None:
        """Release one matching draft without mutating or saving the case."""

        if (
            type(operation_id) is not int
            or operation_id <= 0
            or type(editor_state) is not RedactionToolsState
        ):
            self.redaction_tools_cancel_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_REQUEST_INVALID",
                    message=tr("PaperWorkShell rejected an invalid redaction mark cancellation."),
                    retryable=True,
                    operation_id=(
                        operation_id if type(operation_id) is int and operation_id > 0 else None
                    ),
                ),
            )
            return
        backend = self._backend
        if backend is None:
            self.redaction_tools_cancel_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_NOT_READY",
                    message=tr("PaperWorkShell is still starting. Try the action again."),
                    retryable=True,
                    operation_id=operation_id,
                ),
            )
            return
        try:
            backend.cancel_redaction_tools(editor_state)
        except BaseException as error:
            self.redaction_tools_cancel_failed.emit(
                operation_id,
                sanitize_ui_failure(error, operation_id=operation_id),
            )
            return
        self.redaction_tools_cancelled.emit(operation_id)

    @Slot(int)
    def abort_redaction_tools(self, operation_id: int) -> None:
        """Release a backend draft whose returned presentation state was unusable."""

        if type(operation_id) is not int or operation_id <= 0:
            self.redaction_tools_cancel_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_REQUEST_INVALID",
                    message=tr("PaperWorkShell rejected an invalid visual overlay abort request."),
                    retryable=True,
                    operation_id=(
                        operation_id if type(operation_id) is int and operation_id > 0 else None
                    ),
                ),
            )
            return
        backend = self._backend
        if backend is None:
            self.redaction_tools_cancel_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_NOT_READY",
                    message=tr("PaperWorkShell is still starting. Try the action again."),
                    retryable=True,
                    operation_id=operation_id,
                ),
            )
            return
        try:
            backend.abort_redaction_tools()
        except BaseException as error:
            self.redaction_tools_cancel_failed.emit(
                operation_id,
                sanitize_ui_failure(error, operation_id=operation_id),
            )
            return
        self.redaction_tools_cancelled.emit(operation_id)

    @Slot(int)
    def begin_checklist(self, operation_id: int) -> None:
        """Open one exact, path-free checklist draft on the backend thread."""

        if type(operation_id) is not int or operation_id <= 0:
            self.checklist_start_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_REQUEST_INVALID",
                    message=tr("PaperWorkShell rejected an invalid checklist request."),
                    retryable=True,
                    operation_id=(
                        operation_id if type(operation_id) is int and operation_id > 0 else None
                    ),
                ),
            )
            return
        backend = self._backend
        if backend is None:
            self.checklist_start_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_NOT_READY",
                    message=tr("PaperWorkShell is still starting. Try the action again."),
                    retryable=True,
                    operation_id=operation_id,
                ),
            )
            return
        try:
            editor_state = backend.begin_checklist()
            organizer_state = backend.state
            if (
                type(editor_state) is not ChecklistEditorState
                or type(organizer_state) is not OrganizerViewState
                or editor_state.case_id != organizer_state.case_id
                or editor_state.manifest_revision != organizer_state.revision
                or not is_checklist_announcement_safe(editor_state.announcement)
            ):
                raise TypeError
        except BaseException as error:
            self.checklist_start_failed.emit(
                operation_id,
                sanitize_ui_failure(error, operation_id=operation_id),
            )
            return
        # Opening can flush earlier page intent. Publish that durable revision
        # before the revision-bound checklist reaches the GUI.
        self.state_changed.emit(organizer_state)
        self.checklist_started.emit(operation_id, editor_state)

    @Slot(int, object)
    def apply_checklist(self, operation_id: int, editor_state: object) -> None:
        """Persist one authenticated checklist draft and publish its organizer state."""

        if (
            type(operation_id) is not int
            or operation_id <= 0
            or type(editor_state) is not ChecklistEditorState
        ):
            self.checklist_apply_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_REQUEST_INVALID",
                    message=tr("PaperWorkShell rejected invalid checklist changes."),
                    retryable=True,
                    operation_id=(
                        operation_id if type(operation_id) is int and operation_id > 0 else None
                    ),
                ),
            )
            return
        backend = self._backend
        if backend is None:
            self.checklist_apply_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_NOT_READY",
                    message=tr("PaperWorkShell is still starting. Try the action again."),
                    retryable=True,
                    operation_id=operation_id,
                ),
            )
            return
        try:
            state = backend.apply_checklist(editor_state)
            current = backend.state
            if (
                type(state) is not OrganizerViewState
                or type(current) is not OrganizerViewState
                or state != current
                or not _checklist_apply_state_matches(editor_state, state)
            ):
                raise TypeError
        except BaseException as error:
            self.checklist_apply_failed.emit(
                operation_id,
                sanitize_ui_failure(error, operation_id=operation_id),
            )
            return
        self.state_changed.emit(state)
        self.checklist_applied.emit(operation_id, state)

    @Slot(int, object)
    def cancel_checklist(self, operation_id: int, editor_state: object) -> None:
        """Release one matching checklist draft without changing the case."""

        if (
            type(operation_id) is not int
            or operation_id <= 0
            or type(editor_state) is not ChecklistEditorState
        ):
            self.checklist_cancel_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_REQUEST_INVALID",
                    message=tr("PaperWorkShell rejected an invalid checklist cancellation."),
                    retryable=True,
                    operation_id=(
                        operation_id if type(operation_id) is int and operation_id > 0 else None
                    ),
                ),
            )
            return
        backend = self._backend
        if backend is None:
            self.checklist_cancel_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_NOT_READY",
                    message=tr("PaperWorkShell is still starting. Try the action again."),
                    retryable=True,
                    operation_id=operation_id,
                ),
            )
            return
        try:
            backend.cancel_checklist(editor_state)
        except BaseException as error:
            self.checklist_cancel_failed.emit(
                operation_id,
                sanitize_ui_failure(error, operation_id=operation_id),
            )
            return
        self.checklist_cancelled.emit(operation_id)

    @Slot(int)
    def abort_checklist(self, operation_id: int) -> None:
        """Release a checklist draft whose returned presentation state was unusable."""

        if type(operation_id) is not int or operation_id <= 0:
            self.checklist_cancel_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_REQUEST_INVALID",
                    message=tr("PaperWorkShell rejected an invalid checklist abort request."),
                    retryable=True,
                    operation_id=(
                        operation_id if type(operation_id) is int and operation_id > 0 else None
                    ),
                ),
            )
            return
        backend = self._backend
        if backend is None:
            self.checklist_cancel_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_NOT_READY",
                    message=tr("PaperWorkShell is still starting. Try the action again."),
                    retryable=True,
                    operation_id=operation_id,
                ),
            )
            return
        try:
            backend.abort_checklist()
        except BaseException as error:
            self.checklist_cancel_failed.emit(
                operation_id,
                sanitize_ui_failure(error, operation_id=operation_id),
            )
            return
        self.checklist_cancelled.emit(operation_id)

    @Slot(int, object, object)
    def begin_package_review(self, operation_id: int, mode: object, cancellation: object) -> None:
        """Prepare a presentation snapshot while retaining export authority in the backend."""

        self._prepare_package_review(operation_id, mode, cancellation, refresh=False)

    @Slot(int, object, object)
    def refresh_package_review(self, operation_id: int, mode: object, cancellation: object) -> None:
        self._prepare_package_review(operation_id, mode, cancellation, refresh=True)

    def _prepare_package_review(
        self, operation_id: int, mode: object, cancellation: object, *, refresh: bool
    ) -> None:
        def action(backend: OrganizerBackend) -> None:
            if (mode is not None and type(mode) is not PortalOutputMode) or type(
                cancellation
            ) is not OperationCancellation:
                raise TypeError

            def progress(completed: int, total: int) -> None:
                self._package_progress(operation_id, completed, total)

            if refresh:
                if type(mode) is not PortalOutputMode:
                    raise TypeError
                review = backend.refresh_package_review(
                    mode, cancellation=cancellation.cancelled, progress=progress
                )
            else:
                review = backend.begin_package_review(
                    mode, cancellation=cancellation.cancelled, progress=progress
                )
            if not is_package_review_state_valid(review):
                raise TypeError
            state = backend.state
            if type(state) is not OrganizerViewState or state.autosave_pending:
                raise TypeError
            self.state_changed.emit(state)
            self.package_review_ready.emit(operation_id, review)

        self._run_package_action(operation_id, "review", action)

    @Slot(int, object, object, bool, bool, object)
    def export_package(
        self,
        operation_id: int,
        review_id: object,
        destination_parent: object,
        signed_source_confirmed: bool,
        human_review_confirmed: bool,
        cancellation: object,
    ) -> None:
        """Run the exact reviewed export on the serialized backend thread."""

        def action(backend: OrganizerBackend) -> None:
            if (
                type(review_id) is not UUID
                or review_id.version != 4
                or not isinstance(destination_parent, Path)
                or type(signed_source_confirmed) is not bool
                or type(human_review_confirmed) is not bool
                or type(cancellation) is not OperationCancellation
            ):
                raise TypeError
            summary = backend.export_package(
                destination_parent,
                review_id=review_id,
                signed_source_confirmed=signed_source_confirmed,
                human_review_confirmed=human_review_confirmed,
                cancellation=cancellation.cancelled,
                progress=lambda completed, total: self._package_progress(
                    operation_id, completed, total
                ),
            )
            if type(summary) is not PackageSummary or summary.status in {
                PackageSummaryStatus.PLANNED,
                PackageSummaryStatus.NEEDS_REVIEW,
            }:
                raise TypeError
            summary.to_plain_text()
            self.state_changed.emit(backend.state)
            self.package_export_ready.emit(operation_id, summary)

        self._run_package_action(operation_id, "export", action)

    @Slot(int)
    def cancel_package_review(self, operation_id: int) -> None:
        def action(backend: OrganizerBackend) -> None:
            backend.cancel_package_review()
            self.package_review_cancelled.emit(operation_id)

        self._run_package_action(operation_id, "cancel", action)

    @Slot(int, object, object, object, bool, bool, object)
    def prepare_compression(
        self,
        operation_id: int,
        review_id: object,
        destination_parent: object,
        color_choice: object,
        signed: bool,
        human: bool,
        cancellation: object,
    ) -> None:
        def action(backend: OrganizerBackend) -> None:
            if (
                type(review_id) is not UUID
                or review_id.version != 4
                or not isinstance(destination_parent, Path)
                or type(color_choice) is not TargetSizeColorChoice
                or type(signed) is not bool
                or type(human) is not bool
                or type(cancellation) is not OperationCancellation
            ):
                raise TypeError
            state = backend.prepare_compression(
                destination_parent,
                review_id=review_id,
                color_choice=color_choice,
                signed_source_confirmed=signed,
                human_review_confirmed=human,
                cancellation=cancellation.cancelled,
                progress=lambda done, total: self._package_progress(operation_id, done, total),
            )
            if type(state) is not CompressionReviewState:
                raise TypeError
            self.state_changed.emit(backend.state)
            self.compression_ready.emit(operation_id, state)

        self._run_package_action(operation_id, "compression_prepare", action)

    @Slot(int, object, str, int, object)
    def preview_compression_page(
        self,
        operation_id: int,
        review_id: object,
        output_key: str,
        page_index: int,
        cancellation: object,
    ) -> None:
        def action(backend: OrganizerBackend) -> None:
            if (
                type(review_id) is not UUID
                or review_id.version != 4
                or type(output_key) is not str
                or not 1 <= len(output_key) <= 80
                or type(page_index) is not int
                or not 0 <= page_index < 1_000
                or type(cancellation) is not OperationCancellation
            ):
                raise TypeError
            result = backend.preview_compression_page(
                review_id,
                output_key,
                page_index,
                cancellation=cancellation.cancelled,
            )
            if type(result) is not CompressionPagePreview:
                raise TypeError
            self.compression_preview_ready.emit(operation_id, result)

        self._run_package_action(operation_id, "compression_preview", action)

    @Slot(int, object, bool, object)
    def commit_compression(
        self,
        operation_id: int,
        review_id: object,
        human: bool,
        cancellation: object,
    ) -> None:
        def action(backend: OrganizerBackend) -> None:
            if (
                type(review_id) is not UUID
                or review_id.version != 4
                or type(human) is not bool
                or type(cancellation) is not OperationCancellation
            ):
                raise TypeError
            summary = backend.commit_compression(
                review_id,
                human_review_confirmed=human,
                cancellation=cancellation.cancelled,
                progress=lambda done, total: self._package_progress(operation_id, done, total),
            )
            if type(summary) is not PackageSummary:
                raise TypeError
            summary.to_plain_text()
            self.state_changed.emit(backend.state)
            self.package_export_ready.emit(operation_id, summary)

        self._run_package_action(operation_id, "compression_export", action)

    @Slot(int)
    def discard_compression(self, operation_id: int) -> None:
        def action(backend: OrganizerBackend) -> None:
            review = backend.discard_compression()
            if not is_package_review_state_valid(review):
                raise TypeError
            self.package_review_ready.emit(operation_id, review)

        self._run_package_action(operation_id, "review", action)

    @Slot(int, object, object)
    def save_package_summary(
        self, operation_id: int, destination: object, cancellation: object
    ) -> None:
        def action(backend: OrganizerBackend) -> None:
            if not isinstance(destination, Path) or type(cancellation) is not OperationCancellation:
                raise TypeError
            backend.save_package_summary(destination, cancellation=cancellation.cancelled)
            self.package_summary_saved.emit(operation_id)

        self._run_package_action(operation_id, "save", action)

    @Slot(int, object, bool, object)
    def save_text_companion(
        self,
        operation_id: int,
        destination: object,
        privacy_confirmed: bool,
        cancellation: object,
    ) -> None:
        def action(backend: OrganizerBackend) -> None:
            if (
                not isinstance(destination, Path)
                or type(privacy_confirmed) is not bool
                or type(cancellation) is not OperationCancellation
            ):
                raise TypeError
            backend.save_package_text_companion(
                destination,
                privacy_confirmed=privacy_confirmed,
                cancellation=cancellation.cancelled,
            )
            self.text_companion_saved.emit(operation_id)

        self._run_post_export_action(operation_id, "text", action)

    @Slot(int, object)
    def begin_post_export_deletion(self, operation_id: int, cancellation: object) -> None:
        def action(backend: OrganizerBackend) -> None:
            if type(cancellation) is not OperationCancellation:
                raise TypeError
            if cancellation.cancelled():
                reject_post_export_deletion(
                    PostExportDeletionErrorCode.CANCELLED, "post_export.cancelled"
                )
            review = backend.begin_post_export_deletion(cancellation=cancellation.cancelled)
            if cancellation.cancelled():
                backend.cancel_post_export_deletion()
                reject_post_export_deletion(
                    PostExportDeletionErrorCode.CANCELLED, "post_export.cancelled"
                )
            self.post_export_deletion_review_ready.emit(operation_id, review)

        self._run_post_export_action(operation_id, "inventory", action)

    @Slot(int, object)
    def confirm_post_export_deletion(self, operation_id: int, review_id: object) -> None:
        def action(backend: OrganizerBackend) -> None:
            if type(review_id) is not UUID or review_id.version != 4:
                raise TypeError
            try:
                result = backend.confirm_post_export_deletion(review_id)
            finally:
                # Close can succeed before removal fails. Publish the actual session
                # state so the UI never presents a closed/partial case as editable.
                self.state_changed.emit(backend.state)
                if backend.state.case_id is None:
                    self.assisted_use_changed.emit(None)
                self._emit_cases()
            self.post_export_deletion_finished.emit(operation_id, result)

        self._run_post_export_action(operation_id, "delete", action)

    @Slot()
    def cancel_post_export_deletion(self) -> None:
        if self._backend is not None:
            self._backend.cancel_post_export_deletion()

    def _run_post_export_action(
        self,
        operation_id: int,
        phase: str,
        action: Callable[[OrganizerBackend], None],
    ) -> None:
        try:
            if type(operation_id) is not int or operation_id <= 0 or self._backend is None:
                raise TypeError
            action(self._backend)
        except BaseException as error:
            self.post_export_failed.emit(
                operation_id, phase, sanitize_ui_failure(error, operation_id=operation_id)
            )

    def _package_progress(self, operation_id: int, completed: int, total: int) -> None:
        if (
            type(completed) is not int
            or type(total) is not int
            or not 1 <= total <= 1_000_000
            or not 0 <= completed <= total
        ):
            raise TypeError
        self.package_progress.emit(operation_id, completed, total)

    def _run_package_action(
        self,
        operation_id: int,
        phase: str,
        action: Callable[[OrganizerBackend], None],
    ) -> None:
        try:
            if type(operation_id) is not int or operation_id <= 0:
                raise TypeError
            backend = self._backend
            if backend is None:
                raise TypeError
            action(backend)
        except BaseException as error:
            self.package_failed.emit(
                operation_id, phase, sanitize_ui_failure(error, operation_id=operation_id)
            )

    @Slot(int)
    def begin_portal_constraints(self, operation_id: int) -> None:
        """Open one exact path-free constraint draft on the backend thread."""

        if type(operation_id) is not int or operation_id <= 0:
            self.portal_constraints_start_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_REQUEST_INVALID",
                    message=tr("PaperWorkShell rejected an invalid constraint editor request."),
                    retryable=True,
                    operation_id=(
                        operation_id if type(operation_id) is int and operation_id > 0 else None
                    ),
                ),
            )
            return
        backend = self._backend
        if backend is None:
            self.portal_constraints_start_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_NOT_READY",
                    message=tr("PaperWorkShell is still starting. Try the action again."),
                    retryable=True,
                    operation_id=operation_id,
                ),
            )
            return
        try:
            editor_state = backend.begin_portal_constraints()
            organizer_state = backend.state
            if (
                type(editor_state) is not ConstraintEditorState
                or type(organizer_state) is not OrganizerViewState
                or editor_state.case_id != organizer_state.case_id
                or editor_state.manifest_revision != organizer_state.revision
                or not is_constraint_announcement_safe(editor_state.announcement)
            ):
                raise TypeError
        except BaseException as error:
            self.portal_constraints_start_failed.emit(
                operation_id,
                sanitize_ui_failure(error, operation_id=operation_id),
            )
            return
        self.state_changed.emit(organizer_state)
        self.portal_constraints_started.emit(operation_id, editor_state)

    @Slot(int, object)
    def apply_portal_constraints(self, operation_id: int, editor_state: object) -> None:
        """Persist one authenticated complete constraint draft and publish state."""

        if (
            type(operation_id) is not int
            or operation_id <= 0
            or type(editor_state) is not ConstraintEditorState
        ):
            self.portal_constraints_apply_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_REQUEST_INVALID",
                    message=tr("PaperWorkShell rejected invalid constraint changes."),
                    retryable=True,
                    operation_id=(
                        operation_id if type(operation_id) is int and operation_id > 0 else None
                    ),
                ),
            )
            return
        backend = self._backend
        if backend is None:
            self.portal_constraints_apply_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_NOT_READY",
                    message=tr("PaperWorkShell is still starting. Try the action again."),
                    retryable=True,
                    operation_id=operation_id,
                ),
            )
            return
        try:
            state = backend.apply_portal_constraints(editor_state)
            current = backend.state
            if (
                type(state) is not OrganizerViewState
                or type(current) is not OrganizerViewState
                or state != current
                or not _portal_constraints_apply_state_matches(editor_state, state)
            ):
                raise TypeError
        except BaseException as error:
            self.portal_constraints_apply_failed.emit(
                operation_id,
                sanitize_ui_failure(error, operation_id=operation_id),
            )
            return
        self.state_changed.emit(state)
        self.portal_constraints_applied.emit(operation_id, state)

    @Slot(int, object)
    def cancel_portal_constraints(self, operation_id: int, editor_state: object) -> None:
        """Release one matching constraint draft without changing the case."""

        if (
            type(operation_id) is not int
            or operation_id <= 0
            or type(editor_state) is not ConstraintEditorState
        ):
            self.portal_constraints_cancel_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_REQUEST_INVALID",
                    message=tr("PaperWorkShell rejected an invalid constraint cancellation."),
                    retryable=True,
                    operation_id=(
                        operation_id if type(operation_id) is int and operation_id > 0 else None
                    ),
                ),
            )
            return
        backend = self._backend
        if backend is None:
            self.portal_constraints_cancel_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_NOT_READY",
                    message=tr("PaperWorkShell is still starting. Try the action again."),
                    retryable=True,
                    operation_id=operation_id,
                ),
            )
            return
        try:
            backend.cancel_portal_constraints(editor_state)
        except BaseException as error:
            self.portal_constraints_cancel_failed.emit(
                operation_id,
                sanitize_ui_failure(error, operation_id=operation_id),
            )
            return
        self.portal_constraints_cancelled.emit(operation_id)

    @Slot(int)
    def abort_portal_constraints(self, operation_id: int) -> None:
        """Release a constraint draft whose presentation response was unusable."""

        if type(operation_id) is not int or operation_id <= 0:
            self.portal_constraints_cancel_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_REQUEST_INVALID",
                    message=tr("PaperWorkShell rejected an invalid constraint abort request."),
                    retryable=True,
                    operation_id=(
                        operation_id if type(operation_id) is int and operation_id > 0 else None
                    ),
                ),
            )
            return
        backend = self._backend
        if backend is None:
            self.portal_constraints_cancel_failed.emit(
                operation_id,
                UiFailure(
                    code="PWS_UI_NOT_READY",
                    message=tr("PaperWorkShell is still starting. Try the action again."),
                    retryable=True,
                    operation_id=operation_id,
                ),
            )
            return
        try:
            backend.abort_portal_constraints()
        except BaseException as error:
            self.portal_constraints_cancel_failed.emit(
                operation_id,
                sanitize_ui_failure(error, operation_id=operation_id),
            )
            return
        self.portal_constraints_cancelled.emit(operation_id)

    @Slot(int, object, object, object)
    def prepare_signature_image(
        self,
        operation_id: int,
        page_id: object,
        source_path: object,
        cancellation: object,
    ) -> None:
        """Prepare a bounded worker preview without exposing the source name."""

        def action(backend: OrganizerBackend) -> None:
            if (
                type(page_id) is not UUID
                or not isinstance(source_path, Path)
                or type(cancellation) is not OperationCancellation
            ):
                raise TypeError
            deadline_at = monotonic() + SIGNATURE_IMAGE_WORKFLOW_TIMEOUT_SECONDS
            try:
                draft = backend.begin_signature_image(
                    page_id,
                    source_path,
                    cancellation=cancellation.cancelled,
                    deadline=lambda: monotonic() >= deadline_at,
                    progress=lambda value: self._signature_image_progress(operation_id, value),
                )
                state = backend.state
                if (
                    type(draft) is not SignatureImagePlacementDraft
                    or type(state) is not OrganizerViewState
                    or draft.case_id != state.case_id
                    or draft.manifest_revision != state.revision
                    or draft.page_id != page_id
                ):
                    raise TypeError
            except BaseException as error:
                self.signature_image_prepare_failed.emit(
                    operation_id,
                    sanitize_ui_failure(error, operation_id=operation_id),
                )
                return
            self.state_changed.emit(state)
            self.signature_image_prepared.emit(operation_id, draft)

        self._run_long(operation_id, tr("Signature image preview"), action)

    @Slot(int, object, object)
    def place_signature_image(
        self,
        operation_id: int,
        request: object,
        cancellation: object,
    ) -> None:
        """Commit one explicitly reviewed visual image placement."""

        def action(backend: OrganizerBackend) -> None:
            if (
                type(request) is not SignatureImagePlacementRequest
                or type(cancellation) is not OperationCancellation
            ):
                raise TypeError
            deadline_at = monotonic() + SIGNATURE_IMAGE_WORKFLOW_TIMEOUT_SECONDS
            try:
                outcome = backend.commit_signature_image(
                    request,
                    cancellation=cancellation.cancelled,
                    deadline=lambda: monotonic() >= deadline_at,
                    progress=lambda value: self._signature_image_progress(operation_id, value),
                )
                if type(outcome) is not OrganizerSignatureImagePlacementOutcome:
                    raise TypeError
            except BaseException as error:
                self.signature_image_place_failed.emit(
                    operation_id,
                    sanitize_ui_failure(error, operation_id=operation_id),
                )
                return
            self.state_changed.emit(outcome.state)
            if outcome.refresh_required:
                self.case_reopen_required.emit(operation_id, outcome.safe_message)
            else:
                self.operation_status_changed.emit(operation_id, outcome.safe_message)
            self.signature_image_placed.emit(operation_id, outcome)

        self._run_long(operation_id, tr("Place signature image"), action)

    @Slot(int, object)
    def cancel_signature_image(self, operation_id: int, draft_id: object) -> None:
        """Abort one retained image after dialog cancellation or failed apply."""

        backend = self._backend
        if (
            backend is None
            or type(operation_id) is not int
            or operation_id <= 0
            or type(draft_id) is not UUID
        ):
            self.signature_image_cancel_failed.emit(
                operation_id if type(operation_id) is int else 0,
                UiFailure(
                    code="PWS_UI_REQUEST_INVALID",
                    message=tr("PaperWorkShell could not discard the signature image safely."),
                    retryable=True,
                    operation_id=(operation_id if type(operation_id) is int else None),
                ),
            )
            return
        try:
            backend.cancel_signature_image(draft_id)
        except BaseException as error:
            self.signature_image_cancel_failed.emit(
                operation_id,
                sanitize_ui_failure(error, operation_id=operation_id),
            )
            return
        self.signature_image_cancelled.emit(operation_id)

    @Slot(int, object)
    def list_signature_image_placements(self, request_id: int, page_id: object) -> None:
        """Publish content-free placement choices for one active page."""

        backend = self._backend
        if (
            backend is None
            or type(request_id) is not int
            or request_id <= 0
            or type(page_id) is not UUID
        ):
            self.signature_image_placements_failed.emit(
                request_id if type(request_id) is int else 0,
                UiFailure(
                    code="PWS_UI_REQUEST_INVALID",
                    message=tr("PaperWorkShell could not list signature-image placements safely."),
                    retryable=True,
                    operation_id=(request_id if type(request_id) is int else None),
                ),
            )
            return
        try:
            choices = backend.signature_image_placements(page_id)
            state = backend.state
            if (
                type(choices) is not tuple
                or any(type(choice) is not SignatureImagePlacementChoice for choice in choices)
                or type(state) is not OrganizerViewState
            ):
                raise TypeError
        except BaseException as error:
            self.signature_image_placements_failed.emit(
                request_id,
                sanitize_ui_failure(error, operation_id=request_id),
            )
            return
        self.state_changed.emit(state)
        self.signature_image_placements_ready.emit(request_id, choices)

    @Slot(int, object, object, object, object)
    def delete_signature_image(
        self,
        operation_id: int,
        page_id: object,
        overlay_id: object,
        policy: object,
        cancellation: object,
    ) -> None:
        """Persist an exact deletion and report its explicit cleanup outcome."""

        def action(backend: OrganizerBackend) -> None:
            try:
                if (
                    type(page_id) is not UUID
                    or type(overlay_id) is not UUID
                    or type(policy) is not SignatureAssetDeletePolicy
                    or type(cancellation) is not OperationCancellation
                ):
                    raise TypeError
                deadline_at = monotonic() + SIGNATURE_IMAGE_WORKFLOW_TIMEOUT_SECONDS
                outcome = backend.delete_signature_image(
                    page_id,
                    overlay_id,
                    policy,
                    cancellation=cancellation.cancelled,
                    deadline=lambda: monotonic() >= deadline_at,
                    progress=lambda value: self._signature_image_progress(operation_id, value),
                )
                if type(outcome) is not OrganizerSignatureImageDeleteOutcome:
                    raise TypeError
            except BaseException as error:
                self.signature_image_delete_failed.emit(
                    operation_id,
                    sanitize_ui_failure(error, operation_id=operation_id),
                )
                return
            self.state_changed.emit(outcome.state)
            self.operation_status_changed.emit(operation_id, outcome.safe_message)
            self.signature_image_deleted.emit(operation_id, outcome)

        self._run_long(operation_id, tr("Remove signature image"), action)

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
            self.status_changed.emit(tr("Page order changed. Saving changes."))

        self._with_backend(action)

    @Slot()
    def undo(self) -> None:
        """Undo exactly one session organizer operation."""

        self._state_action(lambda backend: backend.undo(), tr("Last organizer action undone."))

    @Slot()
    def redo(self) -> None:
        """Redo exactly one previously undone session operation."""

        self._state_action(lambda backend: backend.redo(), tr("Organizer action redone."))

    @Slot()
    def flush_autosave(self) -> None:
        """Flush the latest queued organizer state without closing the case."""

        self._state_action(
            lambda backend: backend.flush_autosave(),
            tr("All organizer changes saved."),
        )

    @Slot()
    def retry_autosave(self) -> None:
        """Retry a retained autosave candidate after a recoverable save failure."""

        self._state_action(
            lambda backend: backend.retry_autosave(),
            tr("All organizer changes saved after retry."),
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
            self.status_changed.emit(tr("Organizer change queued. Saving changes."))

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
                    message=tr("PaperWorkShell rejected an invalid OCR language request."),
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
                    message=tr("PaperWorkShell is still starting. Try the action again."),
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
        # The organizer announces completion after it accepts the current pixels.
        if progress.phase is PagePreviewProgressPhase.COMPLETE:
            return
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

    def _quality_progress(self, operation_id: int, progress: QualityAnalysisProgress) -> None:
        self.progress_changed.emit(
            UiProgress(
                operation=tr("Page quality check"),
                phase=progress.phase.value.replace("_", " "),
                completed=progress.completed_pages,
                total=progress.total_pages,
                cancelable=True,
                operation_id=operation_id,
            )
        )

    def _scan_review_progress(self, operation_id: int, progress: PagePreviewProgress) -> None:
        self.scan_review_progress_changed.emit(
            UiProgress(
                operation=tr("Scan review"),
                phase=progress.phase.value,
                completed=progress.completed_pages,
                total=progress.total_pages,
                cancelable=True,
                operation_id=operation_id,
            )
        )

    def _signature_image_progress(
        self,
        operation_id: int,
        progress: SignatureImageProgress,
    ) -> None:
        if type(progress) is not SignatureImageProgress:
            raise TypeError
        self.progress_changed.emit(
            UiProgress(
                operation=tr("Signature image"),
                phase=progress.phase.value.replace("_", " "),
                completed=progress.completed_units,
                total=progress.total_units,
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
                    message=tr("PaperWorkShell rejected an invalid operation request."),
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
                    message=tr("PaperWorkShell is still starting. Try the action again."),
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
        except AssistedUseError as error:
            backend = self._backend
            if backend is not None and backend.assisted_use is not None:
                snapshot = backend.assisted_use.snapshot
                if snapshot is not None and snapshot.status is not AssistedUseStatus.UNLOCKED:
                    self.assisted_use_changed.emit(snapshot)
            self.failed.emit(sanitize_ui_failure(error, operation_id=operation_id))
        except PdfExportError as error:
            if error.code is PdfExportErrorCode.CANCELLED and operation_id is not None:
                self.operation_cancelled.emit(
                    operation_id, tr("PDF export cancelled before completion.")
                )
                return
            self.failed.emit(sanitize_ui_failure(error, operation_id=operation_id))
        except FlattenedPdfExportError as error:
            if error.code is FlattenedPdfExportErrorCode.CANCELLED and operation_id is not None:
                self.operation_cancelled.emit(
                    operation_id, tr("PDF export cancelled before completion.")
                )
                return
            self.failed.emit(sanitize_ui_failure(error, operation_id=operation_id))
        except SourceIngestError as error:
            if error.code is SourceIngestErrorCode.CANCELLED:
                if operation_id is not None:
                    self.operation_cancelled.emit(
                        operation_id,
                        tr("Import cancelled. No source was committed."),
                    )
                else:
                    self.failed.emit(sanitize_ui_failure(error))
                return
            self.failed.emit(sanitize_ui_failure(error, operation_id=operation_id))
        except PdfThumbnailError as error:
            if error.code is PdfThumbnailErrorCode.CANCELLED:
                if operation_id is not None:
                    self.operation_cancelled.emit(operation_id, tr("Preview update cancelled."))
                else:
                    self.failed.emit(sanitize_ui_failure(error))
                return
            self.failed.emit(sanitize_ui_failure(error, operation_id=operation_id))
        except ImagePageError as error:
            if error.code is ImagePageErrorCode.CANCELLED:
                if operation_id is not None:
                    self.operation_cancelled.emit(operation_id, tr("Preview update cancelled."))
                else:
                    self.failed.emit(sanitize_ui_failure(error))
                return
            self.failed.emit(sanitize_ui_failure(error, operation_id=operation_id))
        except PagePreviewError as error:
            if error.code is PagePreviewErrorCode.CANCELLED:
                if operation_id is not None:
                    self.operation_cancelled.emit(operation_id, tr("Preview update cancelled."))
                else:
                    self.failed.emit(sanitize_ui_failure(error))
                return
            self.failed.emit(sanitize_ui_failure(error, operation_id=operation_id))
        except QualityAnalysisError as error:
            if error.code is QualityAnalysisErrorCode.CANCELLED:
                if operation_id is not None:
                    self.operation_cancelled.emit(
                        operation_id,
                        tr("Page quality check cancelled. No review results were applied."),
                    )
                else:
                    self.failed.emit(sanitize_ui_failure(error))
                return
            self.failed.emit(sanitize_ui_failure(error, operation_id=operation_id))
        except SignatureImageError as error:
            if error.code is SignatureImageErrorCode.CANCELLED:
                if operation_id is not None:
                    self.operation_cancelled.emit(
                        operation_id,
                        tr("Signature-image operation cancelled. No new placement was committed."),
                    )
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


def is_package_review_state_valid(value: object) -> bool:
    """Reject malformed display state without touching document content or authority."""

    if type(value) is not PackageReviewState:
        return False
    try:
        if (
            type(value.review_id) is not UUID
            or value.review_id.version != 4
            or type(value.summary) is not PackageSummary
            or value.summary.status
            not in {PackageSummaryStatus.PLANNED, PackageSummaryStatus.NEEDS_REVIEW}
            or type(value.allowed_modes) is not tuple
            or not value.allowed_modes
            or any(type(mode) is not PortalOutputMode for mode in value.allowed_modes)
            or len(set(value.allowed_modes)) != len(value.allowed_modes)
            or type(value.selected_mode) is not PortalOutputMode
            or value.selected_mode not in value.allowed_modes
            or type(value.export_allowed) is not bool
            or type(value.signed_source_confirmation_required) is not bool
            or type(value.human_review_required) is not bool
            or type(value.compression_allowed) is not bool
        ):
            return False
        value.summary.to_plain_text()
    except BaseException:
        return False
    return True


class OrganizerBackendCommands(QObject):
    """Queued command surface owned by the GUI thread."""

    initialize_requested = Signal()
    list_cases_requested = Signal()
    ocr_languages_refresh_requested = Signal(int)
    ocr_languages_show_requested = Signal(int)
    ocr_languages_select_requested = Signal(int, object)
    create_case_requested = Signal()
    create_named_case_requested = Signal(str)
    rename_case_requested = Signal(str)
    create_assisted_case_requested = Signal(int, str, bool)
    create_named_assisted_case_requested = Signal(int, str, bool, str)
    unlock_assisted_case_requested = Signal(int, object, str, object)
    assisted_check_requested = Signal(object)
    open_case_requested = Signal(object)
    close_case_requested = Signal()
    import_requested = Signal(int, object, str, object)
    thumbnails_requested = Signal(int, int, object, int, int, object)
    source_preview_requested = Signal(int, object, object, int, int, object, bool)
    trace_export_inspect_requested = Signal(int, object, object)
    trace_recipe_save_requested = Signal(int, object, str, object)
    trace_recipe_apply_requested = Signal(int, object, str, object)
    quality_review_requested = Signal(int, int, object, int, object)
    scan_review_begin_requested = Signal(int, object, object)
    scan_review_preview_requested = Signal(int, int, object, object, int, int, object)
    scan_review_apply_requested = Signal(int, object)
    scan_review_cancel_requested = Signal(object)
    core_overlay_tools_begin_requested = Signal(int, object)
    core_overlay_tools_apply_requested = Signal(int, object)
    core_overlay_tools_cancel_requested = Signal(int, object)
    core_overlay_tools_abort_requested = Signal(int)
    redaction_tools_begin_requested = Signal(int, object)
    redaction_tools_save_requested = Signal(int, object)
    redaction_tools_cancel_requested = Signal(int, object)
    redaction_tools_abort_requested = Signal(int)
    checklist_begin_requested = Signal(int)
    checklist_apply_requested = Signal(int, object)
    checklist_cancel_requested = Signal(int, object)
    checklist_abort_requested = Signal(int)
    portal_constraints_begin_requested = Signal(int)
    portal_constraints_apply_requested = Signal(int, object)
    portal_constraints_cancel_requested = Signal(int, object)
    portal_constraints_abort_requested = Signal(int)
    package_review_begin_requested = Signal(int, object, object)
    package_review_refresh_requested = Signal(int, object, object)
    package_export_requested = Signal(int, object, object, bool, bool, object)
    compression_prepare_requested = Signal(int, object, object, object, bool, bool, object)
    compression_preview_requested = Signal(int, object, str, int, object)
    compression_export_requested = Signal(int, object, bool, object)
    compression_discard_requested = Signal(int)
    pages_pdf_export_requested = Signal(int, object, object)
    searchable_pdf_export_requested = Signal(int, object, object)
    removal_export_review_requested = Signal(int)
    removal_pdf_export_requested = Signal(int, object, object, bool, object)
    package_review_cancel_requested = Signal(int)
    package_summary_save_requested = Signal(int, object, object)
    text_companion_save_requested = Signal(int, object, bool, object)
    post_export_deletion_review_requested = Signal(int, object)
    post_export_deletion_confirm_requested = Signal(int, object)
    post_export_deletion_cancel_requested = Signal()
    signature_image_prepare_requested = Signal(int, object, object, object)
    signature_image_place_requested = Signal(int, object, object)
    signature_image_cancel_requested = Signal(int, object)
    signature_image_placements_requested = Signal(int, object)
    signature_image_delete_requested = Signal(int, object, object, object, object)
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
        self.ocr_languages_show_requested.connect(worker.show_ocr_languages)
        self.ocr_languages_select_requested.connect(worker.select_ocr_language_codes)
        self.create_case_requested.connect(worker.create_case)
        self.create_named_case_requested.connect(worker.create_case)
        self.rename_case_requested.connect(worker.rename_case)
        self.create_assisted_case_requested.connect(worker.create_assisted_case)
        self.create_named_assisted_case_requested.connect(worker.create_assisted_case)
        self.unlock_assisted_case_requested.connect(worker.unlock_assisted_case)
        self.assisted_check_requested.connect(worker.check_assisted_use)
        self.open_case_requested.connect(worker.open_case)
        self.close_case_requested.connect(worker.close_case)
        self.import_requested.connect(worker.import_source)
        self.pages_pdf_export_requested.connect(worker.export_pages_pdf)
        self.searchable_pdf_export_requested.connect(worker.export_searchable_pdf)
        self.removal_export_review_requested.connect(worker.begin_removal_export)
        self.removal_pdf_export_requested.connect(worker.export_removal_pdf)
        self.thumbnails_requested.connect(worker.get_thumbnails)
        self.source_preview_requested.connect(worker.get_trace_canvas_preview)
        self.trace_export_inspect_requested.connect(worker.inspect_trace_export)
        self.trace_recipe_save_requested.connect(worker.save_trace_recipe_file)
        self.trace_recipe_apply_requested.connect(worker.apply_trace_recipe_file)
        self.quality_review_requested.connect(worker.analyze_quality)
        self.scan_review_begin_requested.connect(worker.begin_scan_review)
        self.scan_review_preview_requested.connect(worker.get_scan_review_preview)
        self.scan_review_apply_requested.connect(worker.apply_scan_review)
        self.scan_review_cancel_requested.connect(worker.cancel_scan_review)
        self.core_overlay_tools_begin_requested.connect(worker.begin_core_overlay_tools)
        self.core_overlay_tools_apply_requested.connect(worker.apply_core_overlay_tools)
        self.core_overlay_tools_cancel_requested.connect(worker.cancel_core_overlay_tools)
        self.core_overlay_tools_abort_requested.connect(worker.abort_core_overlay_tools)
        self.redaction_tools_begin_requested.connect(worker.begin_redaction_tools)
        self.redaction_tools_save_requested.connect(worker.save_redaction_tools)
        self.redaction_tools_cancel_requested.connect(worker.cancel_redaction_tools)
        self.redaction_tools_abort_requested.connect(worker.abort_redaction_tools)
        self.checklist_begin_requested.connect(worker.begin_checklist)
        self.checklist_apply_requested.connect(worker.apply_checklist)
        self.checklist_cancel_requested.connect(worker.cancel_checklist)
        self.checklist_abort_requested.connect(worker.abort_checklist)
        self.portal_constraints_begin_requested.connect(worker.begin_portal_constraints)
        self.portal_constraints_apply_requested.connect(worker.apply_portal_constraints)
        self.portal_constraints_cancel_requested.connect(worker.cancel_portal_constraints)
        self.portal_constraints_abort_requested.connect(worker.abort_portal_constraints)
        self.package_review_begin_requested.connect(worker.begin_package_review)
        self.package_review_refresh_requested.connect(worker.refresh_package_review)
        self.package_export_requested.connect(worker.export_package)
        self.compression_prepare_requested.connect(worker.prepare_compression)
        self.compression_preview_requested.connect(worker.preview_compression_page)
        self.compression_export_requested.connect(worker.commit_compression)
        self.compression_discard_requested.connect(worker.discard_compression)
        self.package_review_cancel_requested.connect(worker.cancel_package_review)
        self.package_summary_save_requested.connect(worker.save_package_summary)
        self.text_companion_save_requested.connect(worker.save_text_companion)
        self.post_export_deletion_review_requested.connect(worker.begin_post_export_deletion)
        self.post_export_deletion_confirm_requested.connect(worker.confirm_post_export_deletion)
        self.post_export_deletion_cancel_requested.connect(worker.cancel_post_export_deletion)
        self.signature_image_prepare_requested.connect(worker.prepare_signature_image)
        self.signature_image_place_requested.connect(worker.place_signature_image)
        self.signature_image_cancel_requested.connect(worker.cancel_signature_image)
        self.signature_image_placements_requested.connect(worker.list_signature_image_placements)
        self.signature_image_delete_requested.connect(worker.delete_signature_image)
        self.rotate_requested.connect(worker.rotate_clockwise)
        self.remove_requested.connect(worker.remove_page)
        self.move_requested.connect(worker.move_page)
        self.reorder_requested.connect(worker.reorder_pages)
        self.undo_requested.connect(worker.undo)
        self.redo_requested.connect(worker.redo)
        self.flush_requested.connect(worker.flush_autosave)
        self.retry_save_requested.connect(worker.retry_autosave)
        self.shutdown_requested.connect(worker.shutdown)

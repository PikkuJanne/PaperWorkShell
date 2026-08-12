"""Toolkit-free organizer session orchestration for the desktop vertical slice.

The backend is deliberately synchronous and thread-confined.  A desktop host
must construct and call it on one coordinator thread so the injected document
worker client retains its single-thread ownership.  The module owns no Qt,
filesystem, storage-adapter, worker-host, renderer, or PDF-library dependency.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol
from uuid import UUID

from paperworkshell.app.manifest_repository import ManifestAutosaveService
from paperworkshell.app.pdf_thumbnails import (
    DEFAULT_PDF_THUMBNAIL_TIMEOUT_SECONDS,
    MAX_PDF_THUMBNAIL_TIMEOUT_SECONDS,
    ThumbnailProgressSink,
)
from paperworkshell.app.pdf_thumbnails import (
    CancellationProbe as ThumbnailCancellationProbe,
)
from paperworkshell.app.source_ingest import (
    DEFAULT_SOURCE_IMPORT_TIMEOUT_SECONDS,
)
from paperworkshell.app.source_ingest import (
    CancellationProbe as SourceCancellationProbe,
)
from paperworkshell.app.source_ingest import (
    ProgressSink as SourceProgressSink,
)
from paperworkshell.domain.case_lifecycle import (
    CaseIndex,
    CaseLifecycleErrorCode,
    CaseSummary,
    reject_case_lifecycle,
)
from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.manifest_repository import (
    MAX_SAFE_MANIFEST_INTEGER,
    ManifestLoadResult,
    ManifestLoadStatus,
    ManifestRepository,
    ManifestRepositoryErrorCode,
    ManifestSnapshot,
    reject_manifest_repository,
)
from paperworkshell.domain.page_operations import (
    MAX_PAGE_COUNT,
    OrganizerProjection,
    PageCommandHistory,
    PageOperationErrorCode,
    RotatePageCommand,
    SequenceChangeCommand,
    SequenceChangeReason,
    reject_page_operation,
)
from paperworkshell.domain.pdf_rendering import (
    MAX_PDF_RENDER_DIMENSION_PIXELS,
    PdfRenderRotation,
)
from paperworkshell.domain.pdf_thumbnails import (
    PdfThumbnailErrorCode,
    PdfThumbnailRequest,
    PdfThumbnailResult,
    reject_pdf_thumbnail,
)
from paperworkshell.domain.recovery import (
    RecoveryErrorCode,
    StartupRecoveryResult,
    reject_recovery,
)
from paperworkshell.domain.source_ingest import SourceImportResult

_DEFAULT_ORGANIZER_PREVIEW_PAGE_TIMEOUT_SECONDS: Final = DEFAULT_PDF_THUMBNAIL_TIMEOUT_SECONDS
MAX_ORGANIZER_PREVIEW_BATCH_PAGES: Final = 8


class _CaseLifecyclePort(Protocol):
    @property
    def active_case(self) -> CaseSummary | None: ...

    def create_case(self) -> CaseSummary: ...

    def list_cases(self) -> CaseIndex: ...

    def open_case(self, case_id: UUID) -> CaseSummary: ...

    def close_case(self, case_id: UUID | None = None) -> CaseSummary | None: ...


class _SourceIngestPort(Protocol):
    def import_source(
        self,
        case_id: UUID,
        source_path: Path,
        display_name: str,
        *,
        timeout_seconds: float = DEFAULT_SOURCE_IMPORT_TIMEOUT_SECONDS,
        cancellation: SourceCancellationProbe | None = None,
        progress: SourceProgressSink | None = None,
    ) -> SourceImportResult: ...


class _ThumbnailServicePort(Protocol):
    def get_thumbnails(
        self,
        requests: tuple[PdfThumbnailRequest, ...],
        *,
        timeout_seconds: float = DEFAULT_PDF_THUMBNAIL_TIMEOUT_SECONDS,
        cancellation: ThumbnailCancellationProbe | None = None,
        progress: ThumbnailProgressSink | None = None,
    ) -> tuple[PdfThumbnailResult, ...]: ...


def _close_nothing() -> None:
    return None


@dataclass(frozen=True, slots=True)
class OrganizerPageView:
    """Path-free presentation facts for one active organizer page."""

    page_id: UUID
    position: int
    rotation_degrees: int
    preview_supported: bool

    def __post_init__(self) -> None:
        if type(self.page_id) is not UUID or self.page_id.version != 4:
            reject_page_operation(PageOperationErrorCode.IDENTIFIER_INVALID, "organizer.page_id")
        if type(self.position) is not int or not 1 <= self.position <= MAX_PAGE_COUNT:
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.position")
        if type(self.rotation_degrees) is not int or self.rotation_degrees not in {
            0,
            90,
            180,
            270,
        }:
            reject_page_operation(PageOperationErrorCode.ROTATION_INVALID, "organizer.rotation")
        if type(self.preview_supported) is not bool:
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.preview")


@dataclass(frozen=True, slots=True)
class OrganizerViewState:
    """Complete UI-facing organizer state with no manifest containers or content."""

    case_id: UUID | None
    revision: int | None
    pages: tuple[OrganizerPageView, ...]
    can_undo: bool
    can_redo: bool
    autosave_pending: bool
    recovery_review_required: bool = False

    def __post_init__(self) -> None:
        flags = (
            self.can_undo,
            self.can_redo,
            self.autosave_pending,
            self.recovery_review_required,
        )
        if any(type(value) is not bool for value in flags) or type(self.pages) is not tuple:
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.state")
        if any(type(page) is not OrganizerPageView for page in self.pages):
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.pages")
        if self.case_id is None:
            if self.revision is not None or self.pages or any(flags):
                reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.empty_state")
            return
        if type(self.case_id) is not UUID or self.case_id.version != 4:
            reject_page_operation(PageOperationErrorCode.IDENTIFIER_INVALID, "organizer.case_id")
        if (
            type(self.revision) is not int
            or self.revision < 0
            or self.revision > MAX_SAFE_MANIFEST_INTEGER
        ):
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.revision")
        page_ids = tuple(page.page_id for page in self.pages)
        if (
            len(self.pages) > MAX_PAGE_COUNT
            or len(set(page_ids)) != len(page_ids)
            or self.case_id in page_ids
            or tuple(page.position for page in self.pages) != tuple(range(1, len(self.pages) + 1))
        ):
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.pages")

    @classmethod
    def empty(cls) -> OrganizerViewState:
        """Return the one valid no-active-case state."""

        return cls(None, None, (), False, False, False, False)


class OrganizerImportRefreshStatus(StrEnum):
    """Closed organizer refresh state after immutable ingest has committed."""

    REFRESHED = "refreshed"
    REOPEN_REQUIRED = "reopen_required"


@dataclass(frozen=True, slots=True)
class OrganizerImportOutcome:
    """Path-free result that never disguises a committed import as a failure."""

    committed_import: SourceImportResult
    state: OrganizerViewState
    refresh_status: OrganizerImportRefreshStatus

    def __post_init__(self) -> None:
        if (
            type(self.committed_import) is not SourceImportResult
            or type(self.state) is not OrganizerViewState
            or type(self.refresh_status) is not OrganizerImportRefreshStatus
            or self.state.case_id != self.committed_import.case_id
        ):
            reject_manifest_repository(
                ManifestRepositoryErrorCode.MODEL_INVALID,
                "organizer.import_outcome",
            )
        if self.refresh_status is OrganizerImportRefreshStatus.REFRESHED and (
            self.state.revision is None
            or self.state.revision < self.committed_import.manifest_revision
        ):
            reject_manifest_repository(
                ManifestRepositoryErrorCode.REVISION_CONFLICT,
                "organizer.import_outcome_revision",
            )

    @property
    def refresh_required(self) -> bool:
        """Return whether the active view must be reconstructed before more work."""

        return self.refresh_status is OrganizerImportRefreshStatus.REOPEN_REQUIRED

    @property
    def safe_message(self) -> str:
        """Describe the durable outcome without paths, content, or adapter details."""

        if self.refresh_required:
            return (
                "Import committed. Close and reopen the case before continuing "
                "so the organizer can refresh."
            )
        return "Import committed and the organizer was refreshed."


@dataclass(slots=True)
class _OrganizerSession:
    snapshot: ManifestSnapshot
    history: PageCommandHistory
    autosave: ManifestAutosaveService
    load_status: ManifestLoadStatus


class OrganizerBackend:
    """Coordinate one active organizer without importing a desktop toolkit.

    All methods are synchronous.  Long import and thumbnail calls expose the
    existing cooperative cancellation and progress callbacks; the desktop host
    is responsible for invoking them away from the GUI event thread.
    """

    __slots__ = (
        "_close_resources",
        "_lifecycle",
        "_pending_committed_import",
        "_repository",
        "_session",
        "_shutdown",
        "_source_ingest",
        "_startup_recovery",
        "_thumbnails",
    )

    def __init__(
        self,
        lifecycle: _CaseLifecyclePort,
        repository: ManifestRepository,
        source_ingest: _SourceIngestPort,
        thumbnails: _ThumbnailServicePort,
        *,
        close_resources: Callable[[], None] = _close_nothing,
        startup_recovery: StartupRecoveryResult | None = None,
    ) -> None:
        if not callable(close_resources):
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.resources")
        if startup_recovery is None:
            startup_recovery = StartupRecoveryResult()
        elif type(startup_recovery) is not StartupRecoveryResult:
            reject_recovery(RecoveryErrorCode.MODEL_INVALID, "recovery.organizer_result")
        self._lifecycle = lifecycle
        self._repository = repository
        self._source_ingest = source_ingest
        self._thumbnails = thumbnails
        self._close_resources = close_resources
        self._startup_recovery = startup_recovery
        self._pending_committed_import: SourceImportResult | None = None
        self._session: _OrganizerSession | None = None
        self._shutdown = False

    @property
    def startup_recovery_result(self) -> StartupRecoveryResult:
        """Return the immutable path-free result from desktop startup recovery."""

        return self._startup_recovery

    @property
    def state(self) -> OrganizerViewState:
        """Return the current path-free projection without performing I/O."""

        session = self._session
        return OrganizerViewState.empty() if session is None else _view_state(session)

    @property
    def load_status(self) -> ManifestLoadStatus | None:
        """Expose whether the active snapshot was current or recovered."""

        session = self._session
        return None if session is None else session.load_status

    def list_cases(self) -> CaseIndex:
        """Return the lifecycle service's deterministic path-free case index."""

        return self._lifecycle.list_cases()

    def create_case(self) -> OrganizerViewState:
        """Create, load, and activate one empty case."""

        self._require_not_shutdown()
        if self._session is not None:
            reject_case_lifecycle(
                CaseLifecycleErrorCode.ACTIVE_CASE_CONFLICT,
                "organizer.create_active",
            )
        summary = self._lifecycle.create_case()
        try:
            self._session = self._load_session(summary.case_id)
        except BaseException:
            _close_rejected_activation(self._lifecycle, summary.case_id)
            raise
        return self.state

    def open_case(self, case_id: UUID) -> OrganizerViewState:
        """Open and reconstruct one organizer from its authoritative manifest."""

        self._require_not_shutdown()
        current = self._session
        if current is not None:
            if current.snapshot.case_id == case_id:
                return self.state
            reject_case_lifecycle(
                CaseLifecycleErrorCode.ACTIVE_CASE_CONFLICT,
                "organizer.open_active",
            )
        summary = self._lifecycle.open_case(case_id)
        try:
            self._session = self._load_session(summary.case_id)
        except BaseException:
            _close_rejected_activation(self._lifecycle, summary.case_id)
            raise
        return self.state

    def reload_case(self) -> OrganizerViewState:
        """Flush pending edits and rebuild state from the latest durable snapshot."""

        session = self._require_session()
        pending = self._pending_committed_import
        if pending is not None:
            return self._reconcile_committed_import(session, pending).state
        self.flush_autosave()
        loaded = _require_load_result(
            self._repository.load(session.snapshot.case_id),
            session.snapshot.case_id,
        )
        replacement = _session_from_load(self._repository, loaded)
        session.autosave.close()
        self._session = replacement
        return self.state

    def close_case(self) -> OrganizerViewState:
        """Drain autosave before releasing the active case lease."""

        session = self._session
        if session is None:
            self._lifecycle.close_case()
            self._pending_committed_import = None
            return OrganizerViewState.empty()
        result = session.autosave.close()
        if result.snapshot is not None:
            session.snapshot = result.snapshot
        try:
            self._lifecycle.close_case(session.snapshot.case_id)
        except BaseException:
            # Autosave is sealed after a successful close.  Retain the active
            # session and replace only that coordinator so a lifecycle-close
            # failure does not strand an otherwise editable case.
            session.autosave = ManifestAutosaveService(
                self._repository,
                session.snapshot.case_id,
            )
            raise
        self._session = None
        self._pending_committed_import = None
        return OrganizerViewState.empty()

    def import_source(
        self,
        source_path: Path,
        display_name: str,
        *,
        timeout_seconds: float = DEFAULT_SOURCE_IMPORT_TIMEOUT_SECONDS,
        cancellation: SourceCancellationProbe | None = None,
        progress: SourceProgressSink | None = None,
    ) -> OrganizerImportOutcome:
        """Import once, then report a closed post-commit organizer refresh outcome."""

        session = self._require_session()
        pending = self._pending_committed_import
        if pending is not None:
            # A repeated UI import after a post-commit refresh problem is a
            # reconciliation attempt for the already committed source.  It
            # must never invoke immutable ingest a second time.
            return self._reconcile_committed_import(session, pending)
        self.flush_autosave()
        result = self._source_ingest.import_source(
            session.snapshot.case_id,
            source_path,
            display_name,
            timeout_seconds=timeout_seconds,
            cancellation=cancellation,
            progress=progress,
        )
        if type(result) is not SourceImportResult:
            reject_manifest_repository(
                ManifestRepositoryErrorCode.MODEL_INVALID,
                "organizer.import_result",
            )
        if result.case_id != session.snapshot.case_id:
            reject_manifest_repository(
                ManifestRepositoryErrorCode.CASE_MISMATCH,
                "organizer.import_result",
            )
        # Returning SourceImportResult is the immutable-ingest commit point.
        # Record it before any fallible reload/session construction so no
        # later exception can make the caller retry the ingest itself.
        self._pending_committed_import = result
        return self._reconcile_committed_import(session, result)

    def rotate_clockwise(self, page_id: UUID) -> OrganizerViewState:
        """Record one additive clockwise quarter turn for an active page."""

        session = self._require_session()
        _require_active_page(session.history.projection, page_id)
        history = session.history.execute(RotatePageCommand(page_id, 1))
        return self._queue_history(session, history)

    def remove_page(self, page_id: UUID) -> OrganizerViewState:
        """Remove one active page from the packet sequence without deleting its source."""

        session = self._require_session()
        _require_active_page(session.history.projection, page_id)
        sequence = tuple(
            candidate
            for candidate in session.history.projection.page_sequence
            if candidate != page_id
        )
        history = session.history.execute(
            SequenceChangeCommand(SequenceChangeReason.REMOVE, sequence)
        )
        return self._queue_history(session, history)

    def move_page(self, page_id: UUID, target_position: int) -> OrganizerViewState:
        """Move one active page to a one-based position using reorder intent."""

        session = self._require_session()
        _require_active_page(session.history.projection, page_id)
        sequence = list(session.history.projection.page_sequence)
        if type(target_position) is not int or not 1 <= target_position <= len(sequence):
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.move_position")
        current_position = sequence.index(page_id) + 1
        if current_position == target_position:
            return self.state
        sequence.pop(current_position - 1)
        sequence.insert(target_position - 1, page_id)
        return self.reorder_pages(tuple(sequence))

    def reorder_pages(self, page_ids: tuple[UUID, ...]) -> OrganizerViewState:
        """Record one complete keyboard- or dialog-provided active page order."""

        session = self._require_session()
        if type(page_ids) is not tuple:
            reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.reorder")
        if page_ids == session.history.projection.page_sequence:
            return self.state
        history = session.history.execute(
            SequenceChangeCommand(SequenceChangeReason.REORDER, page_ids)
        )
        return self._queue_history(session, history)

    def undo(self) -> OrganizerViewState:
        """Undo exactly one session page operation and queue the resulting state."""

        session = self._require_session()
        return self._queue_history(session, session.history.undo())

    def redo(self) -> OrganizerViewState:
        """Redo exactly one session page operation and queue the resulting state."""

        session = self._require_session()
        return self._queue_history(session, session.history.redo())

    def flush_autosave(self) -> OrganizerViewState:
        """Persist every queued edit, retaining pending state after a failure."""

        session = self._require_session()
        result = session.autosave.flush()
        if result.snapshot is not None:
            session.snapshot = result.snapshot
        return self.state

    def retry_autosave(self) -> OrganizerViewState:
        """Retry the same retained latest edit after a reported save failure."""

        return self.flush_autosave()

    def get_thumbnails(
        self,
        maximum_width_pixels: int,
        maximum_height_pixels: int,
        *,
        page_ids: tuple[UUID, ...],
        timeout_seconds: float | None = None,
        cancellation: ThumbnailCancellationProbe | None = None,
        progress: ThumbnailProgressSink | None = None,
    ) -> tuple[PdfThumbnailResult, ...]:
        """Render one bounded, ordered active-page subset.

        The thumbnail service uses one shared batch deadline while its secure broker launches a
        fresh, one-shot worker for every cache miss.  The organizer therefore derives its default
        batch budget from the requested PDF page count, without changing the independently bounded
        worker-operation limit.  An explicit caller timeout remains exact.  Non-PDF page IDs are
        valid members of the subset but remain explicit placeholders and produce no result.
        """

        session = self._require_session()
        _require_thumbnail_dimension(maximum_width_pixels, "organizer.thumbnail_width")
        _require_thumbnail_dimension(maximum_height_pixels, "organizer.thumbnail_height")
        preview_by_source = _preview_support_by_source(session.snapshot)
        requests: list[PdfThumbnailRequest] = []
        projection = session.history.projection
        selected_page_ids = _require_preview_page_subset(projection, page_ids)
        for page_id in selected_page_ids:
            page = projection.state_for(page_id)
            if not preview_by_source.get(page.page.source_id, False):
                continue
            requests.append(
                PdfThumbnailRequest(
                    case_id=session.snapshot.case_id,
                    page_id=page_id,
                    maximum_width_pixels=maximum_width_pixels,
                    maximum_height_pixels=maximum_height_pixels,
                    additional_rotation=PdfRenderRotation(page.quarter_turns_clockwise * 90),
                )
            )
        if not requests:
            return ()
        selected_timeout = (
            _default_organizer_preview_timeout(len(requests))
            if timeout_seconds is None
            else timeout_seconds
        )
        return self._thumbnails.get_thumbnails(
            tuple(requests),
            timeout_seconds=selected_timeout,
            cancellation=cancellation,
            progress=progress,
        )

    def shutdown(self) -> None:
        """Drain the case, then close injected worker/resources exactly once."""

        if self._shutdown:
            return
        self.close_case()
        self._close_resources()
        self._shutdown = True

    def _reconcile_committed_import(
        self,
        session: _OrganizerSession,
        result: SourceImportResult,
    ) -> OrganizerImportOutcome:
        """Best-effort refresh after the immutable-ingest commit point."""

        try:
            loaded = _require_load_result(
                self._repository.load(session.snapshot.case_id),
                session.snapshot.case_id,
            )
            _require_committed_import(loaded.snapshot, result)
            replacement = _session_from_load(self._repository, loaded)
            refreshed_state = _view_state(replacement)
        except BaseException:
            return OrganizerImportOutcome(
                result,
                _view_state(session),
                OrganizerImportRefreshStatus.REOPEN_REQUIRED,
            )

        # Pre-import edits were flushed before ingest, so the superseded
        # coordinator has no pending mutation.  Its cleanup cannot invalidate
        # the authoritative replacement or turn a committed import into an
        # apparent retryable failure.
        try:
            session.autosave.close()
        except BaseException:
            pass
        self._session = replacement
        self._pending_committed_import = None
        return OrganizerImportOutcome(
            result,
            refreshed_state,
            OrganizerImportRefreshStatus.REFRESHED,
        )

    def _load_session(self, case_id: UUID) -> _OrganizerSession:
        loaded = _require_load_result(self._repository.load(case_id), case_id)
        return _session_from_load(self._repository, loaded)

    def _require_session(self) -> _OrganizerSession:
        self._require_not_shutdown()
        session = self._session
        if session is None:
            reject_case_lifecycle(
                CaseLifecycleErrorCode.NO_ACTIVE_CASE,
                "organizer.active_case",
            )
        active = self._lifecycle.active_case
        if active is None or active.case_id != session.snapshot.case_id:
            reject_case_lifecycle(
                CaseLifecycleErrorCode.ACTIVE_CASE_MISMATCH,
                "organizer.active_case",
            )
        return session

    def _require_not_shutdown(self) -> None:
        if self._shutdown:
            reject_case_lifecycle(
                CaseLifecycleErrorCode.NO_ACTIVE_CASE,
                "organizer.shutdown",
            )

    def _queue_history(
        self,
        session: _OrganizerSession,
        history: PageCommandHistory,
    ) -> OrganizerViewState:
        candidate = history.to_candidate(session.snapshot)
        session.autosave.queue(candidate)
        session.history = history
        return self.state


def _session_from_load(
    repository: ManifestRepository,
    loaded: ManifestLoadResult,
) -> _OrganizerSession:
    snapshot = loaded.snapshot
    return _OrganizerSession(
        snapshot=snapshot,
        history=PageCommandHistory.from_snapshot(snapshot),
        autosave=ManifestAutosaveService(repository, snapshot.case_id),
        load_status=loaded.status,
    )


def _require_load_result(result: object, case_id: UUID) -> ManifestLoadResult:
    if type(result) is not ManifestLoadResult or result.snapshot.case_id != case_id:
        reject_manifest_repository(
            ManifestRepositoryErrorCode.STORAGE_FAILED,
            "organizer.load_result",
        )
    return result


def _require_committed_import(
    snapshot: ManifestSnapshot,
    result: SourceImportResult,
) -> None:
    """Prove that an authoritative snapshot contains the committed import."""

    document = snapshot.document
    sources = document.get("sources")
    pages = document.get("pages")
    expected_source_id = str(result.source_id)
    expected_page_ids = {str(page_id) for page_id in result.page_ids}
    source_found = False
    found_page_ids: set[str] = set()
    if snapshot.revision >= result.manifest_revision and type(sources) is list:
        for value in sources:
            if not isinstance(value, Mapping) or value.get("source_id") != expected_source_id:
                continue
            source_found = (
                value.get("detected_media_type") == result.media_type.value
                and value.get("byte_size") == result.byte_size
                and value.get("sha256") == result.sha256
                and value.get("page_count") == len(result.page_ids)
            )
            break
    if source_found and type(pages) is list:
        for value in pages:
            if (
                isinstance(value, Mapping)
                and value.get("source_id") == expected_source_id
                and value.get("page_id") in expected_page_ids
            ):
                page_id = value.get("page_id")
                if type(page_id) is str:
                    found_page_ids.add(page_id)
    if not source_found or found_page_ids != expected_page_ids:
        reject_manifest_repository(
            ManifestRepositoryErrorCode.REVISION_CONFLICT,
            "organizer.import_reconciliation",
        )


def _view_state(session: _OrganizerSession) -> OrganizerViewState:
    projection = session.history.projection
    preview_by_source = _preview_support_by_source(session.snapshot)
    pages: list[OrganizerPageView] = []
    for position, page_id in enumerate(projection.page_sequence, start=1):
        page = projection.state_for(page_id)
        try:
            preview_supported = preview_by_source[page.page.source_id]
        except KeyError:
            reject_page_operation(
                PageOperationErrorCode.MANIFEST_INVALID,
                "organizer.page_source",
            )
        pages.append(
            OrganizerPageView(
                page_id=page_id,
                position=position,
                rotation_degrees=page.quarter_turns_clockwise * 90,
                preview_supported=preview_supported,
            )
        )
    return OrganizerViewState(
        case_id=session.snapshot.case_id,
        revision=session.snapshot.revision,
        pages=tuple(pages),
        can_undo=session.history.can_undo,
        can_redo=session.history.can_redo,
        autosave_pending=session.autosave.pending,
        recovery_review_required=session.load_status is ManifestLoadStatus.RECOVERED,
    )


def _preview_support_by_source(snapshot: ManifestSnapshot) -> dict[UUID, bool]:
    document = snapshot.document
    sources = document.get("sources")
    if type(sources) is not list:
        reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "organizer.sources")
    result: dict[UUID, bool] = {}
    for value in sources:
        if not isinstance(value, Mapping):
            reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "organizer.source")
        source_id_value = value.get("source_id")
        media_type = value.get("detected_media_type")
        if type(source_id_value) is not str or type(media_type) is not str:
            reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "organizer.source")
        try:
            source_id = UUID(source_id_value)
        except ValueError:
            reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "organizer.source_id")
        if source_id.version != 4 or source_id in result:
            reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "organizer.source_id")
        try:
            detected_media_type = DetectedMediaType(media_type)
        except ValueError:
            reject_page_operation(PageOperationErrorCode.MANIFEST_INVALID, "organizer.media_type")
        result[source_id] = detected_media_type is DetectedMediaType.PDF
    return result


def _require_active_page(projection: OrganizerProjection, page_id: UUID) -> None:
    page = projection.state_for(page_id)
    if not page.active:
        reject_page_operation(PageOperationErrorCode.PAGE_UNKNOWN, "organizer.active_page")


def _require_thumbnail_dimension(value: object, context: str) -> None:
    if type(value) is not int or not 1 <= value <= MAX_PDF_RENDER_DIMENSION_PIXELS:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, context)


def _require_preview_page_subset(
    projection: OrganizerProjection,
    page_ids: object,
) -> tuple[UUID, ...]:
    """Validate one small organizer-ordered subset before any worker request."""

    if type(page_ids) is not tuple or len(page_ids) > MAX_ORGANIZER_PREVIEW_BATCH_PAGES:
        reject_page_operation(PageOperationErrorCode.MODEL_INVALID, "organizer.thumbnail_pages")
    selected: set[UUID] = set()
    for page_id in page_ids:
        if type(page_id) is not UUID or page_id.version != 4:
            reject_page_operation(
                PageOperationErrorCode.IDENTIFIER_INVALID,
                "organizer.thumbnail_page_id",
            )
        if page_id in selected:
            reject_page_operation(
                PageOperationErrorCode.PAGE_DUPLICATE,
                "organizer.thumbnail_pages",
            )
        selected.add(page_id)
        _require_active_page(projection, page_id)
    ordered = tuple(page_id for page_id in projection.page_sequence if page_id in selected)
    if ordered != page_ids:
        reject_page_operation(
            PageOperationErrorCode.SEQUENCE_MISMATCH,
            "organizer.thumbnail_pages",
        )
    return ordered


def _default_organizer_preview_timeout(request_count: int) -> float:
    """Bound the serial batch without weakening any per-render worker limit."""

    return min(
        MAX_PDF_THUMBNAIL_TIMEOUT_SECONDS,
        _DEFAULT_ORGANIZER_PREVIEW_PAGE_TIMEOUT_SECONDS * request_count,
    )


def _close_rejected_activation(lifecycle: _CaseLifecyclePort, case_id: UUID) -> None:
    try:
        lifecycle.close_case(case_id)
    except Exception:
        return

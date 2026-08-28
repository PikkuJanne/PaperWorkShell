"""Path-free application boundary for checklist assignment editing.

The GUI receives only reviewed template requirement text, internal UUIDs,
counts, statuses, and an immutable domain history.  The opening manifest and
its compare-and-swap authority remain private to :class:`ChecklistWorkflowService`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from threading import Lock
from typing import Final, Protocol, Self, cast, runtime_checkable
from uuid import UUID, uuid4

from paperworkshell.domain.checklist import (
    AddDocumentAssignmentCommand,
    AssignmentOrigin,
    AssignmentReviewState,
    ChecklistCommand,
    ChecklistCommandHistory,
    ChecklistDefinition,
    ChecklistError,
    ChecklistErrorCode,
    ChecklistRequirement,
    ChecklistStatus,
    ReorderAssignmentPagesCommand,
    ReorderRequirementAssignmentsCommand,
    ReplaceAssignmentPagesCommand,
    RequirementCountUnit,
    SuggestDocumentAssignmentCommand,
    TemplateBinding,
    reject_checklist,
)
from paperworkshell.domain.checklist import (
    ConfirmAssignmentCommand as DomainConfirmAssignmentCommand,
)
from paperworkshell.domain.checklist import (
    MoveAssignmentCommand as DomainMoveAssignmentCommand,
)
from paperworkshell.domain.checklist import (
    RemoveAssignmentCommand as DomainRemoveAssignmentCommand,
)
from paperworkshell.domain.manifest_repository import (
    MAX_SAFE_MANIFEST_INTEGER,
    ManifestSnapshot,
)
from paperworkshell.templates.loader import SafeTemplateLoader
from paperworkshell.templates.models import TemplateKey, TemplateSnapshot

MAX_CHECKLIST_SUGGESTIONS: Final = 128
MAX_EDITOR_IDENTIFIER_ATTEMPTS: Final = 32


class _MonotonicUtcClock:
    """Thread-safe UTC sequencer shared by GUI edits and manifest autosave."""

    __slots__ = ("_last", "_lock", "_source")

    def __init__(self, source: Callable[[], datetime]) -> None:
        if not callable(source):
            reject_checklist(ChecklistErrorCode.TIMESTAMP_INVALID, "checklist.workflow_clock")
        self._source = source
        self._lock = Lock()
        self._last: datetime | None = None

    def __call__(self) -> datetime:
        return self._next_after(None)

    def next_after(self, floor: datetime) -> datetime:
        """Advance the shared sequence beyond an existing manifest or edit time."""

        if not _is_utc_datetime(floor):
            reject_checklist(ChecklistErrorCode.TIMESTAMP_INVALID, "checklist.workflow_clock")
        return self._next_after(floor)

    def include(self, observed: datetime) -> None:
        """Include an accepted explicit event timestamp in the shared sequence."""

        if (
            type(observed) is not datetime
            or observed.tzinfo is None
            or observed.utcoffset() is None
        ):
            reject_checklist(ChecklistErrorCode.TIMESTAMP_INVALID, "checklist.workflow_clock")
        selected = observed.astimezone(UTC)
        with self._lock:
            if self._last is None or selected > self._last:
                self._last = selected

    def _next_after(self, floor: datetime | None) -> datetime:
        with self._lock:
            try:
                observed = self._source()
            except BaseException:
                reject_checklist(
                    ChecklistErrorCode.TIMESTAMP_INVALID,
                    "checklist.workflow_clock",
                )
            if (
                type(observed) is not datetime
                or observed.tzinfo is None
                or observed.utcoffset() is None
            ):
                reject_checklist(
                    ChecklistErrorCode.TIMESTAMP_INVALID,
                    "checklist.workflow_clock",
                )
            selected = observed.astimezone(UTC)
            sequence_floor = floor
            if self._last is not None and (sequence_floor is None or self._last > sequence_floor):
                sequence_floor = self._last
            if sequence_floor is not None and selected <= sequence_floor:
                try:
                    selected = sequence_floor + timedelta(microseconds=1)
                except OverflowError:
                    reject_checklist(
                        ChecklistErrorCode.TIMESTAMP_INVALID,
                        "checklist.workflow_clock",
                    )
            self._last = selected
        return selected


class ChecklistAnnouncement(StrEnum):
    """Closed, content-free messages suitable for an accessible live region."""

    READY = "Checklist editor ready."
    SUGGESTIONS_ADDED = "Checklist suggestions added. Review each suggestion before accepting it."
    PAGES_ADDED = "Pages added as a document. Review and confirm the assignment."
    SUGGESTION_ADDED = "Document suggestion added. Review it before accepting it."
    ASSIGNMENT_CONFIRMED = "Document assignment confirmed."
    SUGGESTION_ACCEPTED = "Suggested document assignment accepted and confirmed."
    SUGGESTION_REJECTED = "Suggested document assignment rejected."
    ASSIGNMENT_MOVED = "Document assignment moved. Review and confirm it again."
    ASSIGNMENT_REMOVED = "Document assignment removed."
    ASSIGNMENT_PAGES_REPLACED = "Document pages changed. Review and confirm the assignment again."
    DOCUMENT_ORDER_CHANGED = "Document order changed."
    PAGE_ORDER_CHANGED = "Document page order changed."
    UNDONE = "Last checklist change undone."
    REDONE = "Checklist change redone."


class ChecklistOrderDirection(StrEnum):
    """One keyboard-accessible adjacent ordering action."""

    EARLIER = "earlier"
    LATER = "later"


@dataclass(frozen=True, slots=True, repr=False)
class ChecklistRequirementView:
    """Reviewed template text and deterministic state for one requirement."""

    requirement_id: str
    display_name: str = field(repr=False)
    description: str = field(repr=False)
    count_unit: RequirementCountUnit
    status: ChecklistStatus
    confirmed_count: int
    minimum_count: int
    maximum_count: int | None
    assignment_ids: tuple[UUID, ...] = field(repr=False)

    def __post_init__(self) -> None:
        maximum = self.maximum_count
        if (
            type(self.requirement_id) is not str
            or not self.requirement_id
            or type(self.display_name) is not str
            or not self.display_name
            or type(self.description) is not str
            or not self.description
            or type(self.count_unit) is not RequirementCountUnit
            or type(self.status) is not ChecklistStatus
            or type(self.confirmed_count) is not int
            or type(self.minimum_count) is not int
            or not 0 <= self.confirmed_count <= MAX_SAFE_MANIFEST_INTEGER
            or not 0 <= self.minimum_count <= MAX_SAFE_MANIFEST_INTEGER
            or (
                maximum is not None
                and (type(maximum) is not int or not self.minimum_count <= maximum)
            )
            or not _is_uuid_tuple(self.assignment_ids, allow_empty=True)
        ):
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.requirement_view")

    def __repr__(self) -> str:
        return (
            "ChecklistRequirementView("
            f"count_unit={self.count_unit.value!r}, status={self.status.value!r}, "
            f"confirmed_count={self.confirmed_count}, assignment_count={len(self.assignment_ids)})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class ChecklistPageView:
    """One active page and its current deterministic assignment owner."""

    page_id: UUID
    position: int
    assignment_id: UUID | None

    def __post_init__(self) -> None:
        if (
            not _is_uuid(self.page_id)
            or type(self.position) is not int
            or not 1 <= self.position <= MAX_SAFE_MANIFEST_INTEGER
            or (self.assignment_id is not None and not _is_uuid(self.assignment_id))
        ):
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.page_view")

    def __repr__(self) -> str:
        return (
            "ChecklistPageView("
            f"position={self.position}, assigned={self.assignment_id is not None})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class ChecklistAssignmentView:
    """One logical document without source names, paths, or document content."""

    assignment_id: UUID
    requirement_id: str
    page_ids: tuple[UUID, ...] = field(repr=False)
    origin: AssignmentOrigin
    review_state: AssignmentReviewState
    page_review_required: bool

    def __post_init__(self) -> None:
        if (
            not _is_uuid(self.assignment_id)
            or type(self.requirement_id) is not str
            or not self.requirement_id
            or not _is_uuid_tuple(self.page_ids, allow_empty=False)
            or type(self.origin) is not AssignmentOrigin
            or type(self.review_state) is not AssignmentReviewState
            or type(self.page_review_required) is not bool
        ):
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.assignment_view")

    def __repr__(self) -> str:
        return (
            "ChecklistAssignmentView("
            f"page_count={len(self.page_ids)}, origin={self.origin.value!r}, "
            f"review_state={self.review_state.value!r}, "
            f"page_review_required={self.page_review_required})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class ChecklistSuggestionContext:
    """Bounded, path-free facts made available to an optional suggestion provider."""

    case_id: UUID
    manifest_revision: int
    requirement_ids: tuple[str, ...]
    active_page_ids: tuple[UUID, ...] = field(repr=False)
    unassigned_page_ids: tuple[UUID, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not _is_uuid(self.case_id)
            or type(self.manifest_revision) is not int
            or not 0 <= self.manifest_revision <= MAX_SAFE_MANIFEST_INTEGER
            or type(self.requirement_ids) is not tuple
            or not self.requirement_ids
            or any(type(value) is not str or not value for value in self.requirement_ids)
            or len(set(self.requirement_ids)) != len(self.requirement_ids)
            or not _is_uuid_tuple(self.active_page_ids, allow_empty=True)
            or not _is_uuid_tuple(self.unassigned_page_ids, allow_empty=True)
            or not set(self.unassigned_page_ids) <= set(self.active_page_ids)
        ):
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.suggestion_context")

    def __repr__(self) -> str:
        return (
            "ChecklistSuggestionContext("
            f"manifest_revision={self.manifest_revision}, "
            f"requirement_count={len(self.requirement_ids)}, "
            f"active_page_count={len(self.active_page_ids)}, "
            f"unassigned_page_count={len(self.unassigned_page_ids)})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class ChecklistSuggestion:
    """One provider proposal; origin and review state are deliberately not configurable."""

    requirement_id: str
    page_ids: tuple[UUID, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.requirement_id) is not str
            or not self.requirement_id
            or not _is_uuid_tuple(self.page_ids, allow_empty=False)
        ):
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.suggestion")

    def __repr__(self) -> str:
        return f"ChecklistSuggestion(page_count={len(self.page_ids)})"


@runtime_checkable
class ChecklistSuggestionProvider(Protocol):
    """Optional inert proposal hook with no filesystem, content, or completion authority."""

    def suggest(self, context: ChecklistSuggestionContext) -> tuple[ChecklistSuggestion, ...]: ...


@dataclass(frozen=True, slots=True, repr=False)
class AddPages:
    editor_id: UUID
    requirement_id: str
    page_ids: tuple[UUID, ...] = field(repr=False)

    def __post_init__(self) -> None:
        _validate_editor_page_command(self.editor_id, self.requirement_id, self.page_ids)


@dataclass(frozen=True, slots=True, repr=False)
class ConfirmAssignment:
    editor_id: UUID
    assignment_id: UUID

    def __post_init__(self) -> None:
        _validate_editor_assignment_command(self.editor_id, self.assignment_id)


@dataclass(frozen=True, slots=True, repr=False)
class AcceptSuggestion:
    editor_id: UUID
    assignment_id: UUID

    def __post_init__(self) -> None:
        _validate_editor_assignment_command(self.editor_id, self.assignment_id)


@dataclass(frozen=True, slots=True, repr=False)
class RejectSuggestion:
    editor_id: UUID
    assignment_id: UUID

    def __post_init__(self) -> None:
        _validate_editor_assignment_command(self.editor_id, self.assignment_id)


@dataclass(frozen=True, slots=True, repr=False)
class MoveAssignment:
    editor_id: UUID
    assignment_id: UUID
    target_requirement_id: str

    def __post_init__(self) -> None:
        _validate_editor_assignment_command(self.editor_id, self.assignment_id)
        if type(self.target_requirement_id) is not str or not self.target_requirement_id:
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.editor_requirement")


@dataclass(frozen=True, slots=True, repr=False)
class ReorderAssignment:
    editor_id: UUID
    assignment_id: UUID
    direction: ChecklistOrderDirection

    def __post_init__(self) -> None:
        _validate_editor_assignment_command(self.editor_id, self.assignment_id)
        if type(self.direction) is not ChecklistOrderDirection:
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.editor_direction")


@dataclass(frozen=True, slots=True, repr=False)
class ReorderAssignmentPage:
    editor_id: UUID
    assignment_id: UUID
    page_id: UUID
    direction: ChecklistOrderDirection

    def __post_init__(self) -> None:
        _validate_editor_assignment_command(self.editor_id, self.assignment_id)
        if not _is_uuid(self.page_id) or type(self.direction) is not ChecklistOrderDirection:
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.editor_page_order")


@dataclass(frozen=True, slots=True, repr=False)
class RemoveAssignment:
    editor_id: UUID
    assignment_id: UUID

    def __post_init__(self) -> None:
        _validate_editor_assignment_command(self.editor_id, self.assignment_id)


@dataclass(frozen=True, slots=True, repr=False)
class Undo:
    editor_id: UUID

    def __post_init__(self) -> None:
        if not _is_uuid(self.editor_id):
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.editor_id")


@dataclass(frozen=True, slots=True, repr=False)
class Redo:
    editor_id: UUID

    def __post_init__(self) -> None:
        if not _is_uuid(self.editor_id):
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.editor_id")


ChecklistEditorCommand = (
    AddPages
    | ConfirmAssignment
    | AcceptSuggestion
    | RejectSuggestion
    | MoveAssignment
    | ReorderAssignment
    | ReorderAssignmentPage
    | RemoveAssignment
    | Undo
    | Redo
)


@dataclass(frozen=True, slots=True, repr=False)
class _RequirementPresentation:
    requirement_id: str
    display_name: str = field(repr=False)
    description: str = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class ChecklistEditorState:
    """Detached local draft containing no manifest, source metadata, paths, or content."""

    case_id: UUID
    manifest_revision: int
    editor_session_id: UUID
    requirements: tuple[ChecklistRequirementView, ...] = field(repr=False)
    pages: tuple[ChecklistPageView, ...] = field(repr=False)
    assignments: tuple[ChecklistAssignmentView, ...] = field(repr=False)
    can_undo: bool
    can_redo: bool
    changed: bool
    announcement: ChecklistAnnouncement
    _history: ChecklistCommandHistory = field(repr=False, compare=False)
    _opening_history: ChecklistCommandHistory = field(repr=False, compare=False)
    _presentations: tuple[_RequirementPresentation, ...] = field(repr=False, compare=False)
    _last_event_at: datetime = field(repr=False, compare=False)
    _clock: _MonotonicUtcClock = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            not _is_uuid(self.case_id)
            or type(self.manifest_revision) is not int
            or not 0 <= self.manifest_revision <= MAX_SAFE_MANIFEST_INTEGER
            or not _is_uuid(self.editor_session_id)
            or type(self.requirements) is not tuple
            or any(type(value) is not ChecklistRequirementView for value in self.requirements)
            or type(self.pages) is not tuple
            or any(type(value) is not ChecklistPageView for value in self.pages)
            or type(self.assignments) is not tuple
            or any(type(value) is not ChecklistAssignmentView for value in self.assignments)
            or type(self.can_undo) is not bool
            or type(self.can_redo) is not bool
            or type(self.changed) is not bool
            or type(self.announcement) is not ChecklistAnnouncement
            or type(self._history) is not ChecklistCommandHistory
            or type(self._opening_history) is not ChecklistCommandHistory
            or type(self._presentations) is not tuple
            or any(type(value) is not _RequirementPresentation for value in self._presentations)
            or not _is_utc_datetime(self._last_event_at)
            or type(self._clock) is not _MonotonicUtcClock
        ):
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.editor_state")
        expected = _editor_views(self._history, self._presentations)
        if (
            (self.requirements, self.pages, self.assignments) != expected
            or self.can_undo != self._history.can_undo
            or self.can_redo != self._history.can_redo
            or self.changed != _history_changed(self._history)
        ):
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.editor_projection")

    def __repr__(self) -> str:
        return (
            "ChecklistEditorState("
            f"manifest_revision={self.manifest_revision}, "
            f"requirement_count={len(self.requirements)}, page_count={len(self.pages)}, "
            f"assignment_count={len(self.assignments)}, can_undo={self.can_undo}, "
            f"can_redo={self.can_redo}, changed={self.changed}, "
            f"announcement={self.announcement.name})"
        )

    @property
    def editor_id(self) -> UUID:
        """Compatibility alias for GUI command DTOs."""

        return self.editor_session_id

    @property
    def requirement_rows(self) -> tuple[ChecklistRequirementView, ...]:
        return self.requirements

    @property
    def page_rows(self) -> tuple[ChecklistPageView, ...]:
        return self.pages

    @property
    def assignment_rows(self) -> tuple[ChecklistAssignmentView, ...]:
        return self.assignments

    def execute(
        self,
        command: ChecklistEditorCommand,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> Self:
        """Apply one closed GUI command locally without crossing a worker boundary."""

        if type(command) not in {
            AddPages,
            ConfirmAssignment,
            AcceptSuggestion,
            RejectSuggestion,
            MoveAssignment,
            ReorderAssignment,
            ReorderAssignmentPage,
            RemoveAssignment,
            Undo,
            Redo,
        }:
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.editor_command")
        if command.editor_id != self.editor_session_id:
            reject_checklist(ChecklistErrorCode.CONCURRENT_CHANGE, "checklist.editor_command_id")
        if type(command) is AddPages:
            return self.add_pages(
                command.requirement_id,
                command.page_ids,
                clock=clock,
                id_factory=id_factory,
            )
        if type(command) is ConfirmAssignment:
            return self.confirm_assignment(
                command.assignment_id,
                clock=clock,
                id_factory=id_factory,
            )
        if type(command) is AcceptSuggestion:
            return self.accept_suggestion(
                command.assignment_id,
                clock=clock,
                id_factory=id_factory,
            )
        if type(command) is RejectSuggestion:
            return self.reject_suggestion(command.assignment_id, id_factory=id_factory)
        if type(command) is MoveAssignment:
            return self.move_assignment(
                command.assignment_id,
                command.target_requirement_id,
                clock=clock,
                id_factory=id_factory,
            )
        if type(command) is ReorderAssignment:
            return self._reorder_assignment_adjacent(
                command.assignment_id,
                command.direction,
                id_factory=id_factory,
            )
        if type(command) is ReorderAssignmentPage:
            return self._reorder_page_adjacent(
                command.assignment_id,
                command.page_id,
                command.direction,
                id_factory=id_factory,
            )
        if type(command) is RemoveAssignment:
            return self.remove_assignment(command.assignment_id, id_factory=id_factory)
        if type(command) is Undo:
            return self.undo()
        if type(command) is Redo:
            return self.redo()
        reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.editor_command")

    def add_pages(
        self,
        requirement_id: str,
        page_ids: tuple[UUID, ...],
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> Self:
        assigned_at = self._event_at(clock)
        return self._execute_domain(
            AddDocumentAssignmentCommand(requirement_id, page_ids, assigned_at),
            announcement=ChecklistAnnouncement.PAGES_ADDED,
            last_event_at=assigned_at,
            id_factory=id_factory,
        )

    def suggest_pages(
        self,
        requirement_id: str,
        page_ids: tuple[UUID, ...],
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> Self:
        assigned_at = self._event_at(clock)
        return self._execute_domain(
            SuggestDocumentAssignmentCommand(requirement_id, page_ids, assigned_at),
            announcement=ChecklistAnnouncement.SUGGESTION_ADDED,
            last_event_at=assigned_at,
            id_factory=id_factory,
        )

    def confirm_assignment(
        self,
        assignment_id: UUID,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> Self:
        confirmed_at = self._event_at(clock)
        return self._execute_domain(
            DomainConfirmAssignmentCommand(assignment_id, confirmed_at),
            announcement=ChecklistAnnouncement.ASSIGNMENT_CONFIRMED,
            last_event_at=confirmed_at,
            id_factory=id_factory,
        )

    def accept_suggestion(
        self,
        assignment_id: UUID,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> Self:
        selected = self._history.assignment(assignment_id)
        if (
            selected.origin is not AssignmentOrigin.AUTOMATION
            or selected.review_state is not AssignmentReviewState.SUGGESTED
        ):
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.accept_suggestion")
        confirmed_at = self._event_at(clock)
        return self._execute_domain(
            DomainConfirmAssignmentCommand(assignment_id, confirmed_at),
            announcement=ChecklistAnnouncement.SUGGESTION_ACCEPTED,
            last_event_at=confirmed_at,
            id_factory=id_factory,
        )

    def reject_suggestion(
        self,
        assignment_id: UUID,
        *,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> Self:
        selected = self._history.assignment(assignment_id)
        if (
            selected.origin is not AssignmentOrigin.AUTOMATION
            or selected.review_state is not AssignmentReviewState.SUGGESTED
        ):
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.reject_suggestion")
        return self._execute_domain(
            DomainRemoveAssignmentCommand(assignment_id),
            announcement=ChecklistAnnouncement.SUGGESTION_REJECTED,
            id_factory=id_factory,
        )

    def move_assignment(
        self,
        assignment_id: UUID,
        requirement_id: str,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> Self:
        moved_at = self._event_at(clock)
        return self._execute_domain(
            DomainMoveAssignmentCommand(assignment_id, requirement_id, moved_at),
            announcement=ChecklistAnnouncement.ASSIGNMENT_MOVED,
            last_event_at=moved_at,
            id_factory=id_factory,
        )

    def remove_assignment(
        self,
        assignment_id: UUID,
        *,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> Self:
        return self._execute_domain(
            DomainRemoveAssignmentCommand(assignment_id),
            announcement=ChecklistAnnouncement.ASSIGNMENT_REMOVED,
            id_factory=id_factory,
        )

    def replace_pages(
        self,
        assignment_id: UUID,
        page_ids: tuple[UUID, ...],
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> Self:
        changed_at = self._event_at(clock)
        return self._execute_domain(
            ReplaceAssignmentPagesCommand(assignment_id, page_ids, changed_at),
            announcement=ChecklistAnnouncement.ASSIGNMENT_PAGES_REPLACED,
            last_event_at=changed_at,
            id_factory=id_factory,
        )

    def reorder_documents(
        self,
        requirement_id: str,
        assignment_ids: tuple[UUID, ...],
        *,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> Self:
        return self._execute_domain(
            ReorderRequirementAssignmentsCommand(requirement_id, assignment_ids),
            announcement=ChecklistAnnouncement.DOCUMENT_ORDER_CHANGED,
            id_factory=id_factory,
        )

    def reorder_requirement_assignments(
        self,
        requirement_id: str,
        assignment_ids: tuple[UUID, ...],
        *,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> Self:
        return self.reorder_documents(
            requirement_id,
            assignment_ids,
            id_factory=id_factory,
        )

    def reorder_pages(
        self,
        assignment_id: UUID,
        page_ids: tuple[UUID, ...],
        *,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> Self:
        return self._execute_domain(
            ReorderAssignmentPagesCommand(assignment_id, page_ids),
            announcement=ChecklistAnnouncement.PAGE_ORDER_CHANGED,
            id_factory=id_factory,
        )

    def reorder_assignment_pages(
        self,
        assignment_id: UUID,
        page_ids: tuple[UUID, ...],
        *,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> Self:
        return self.reorder_pages(assignment_id, page_ids, id_factory=id_factory)

    def undo(self) -> Self:
        return self._transition(self._history.undo(), ChecklistAnnouncement.UNDONE)

    def redo(self) -> Self:
        return self._transition(self._history.redo(), ChecklistAnnouncement.REDONE)

    def matches_opening(self, opening: ChecklistEditorState) -> bool:
        """Return whether this draft descends from one exact safe opening state."""

        if (
            type(opening) is not ChecklistEditorState
            or self.case_id != opening.case_id
            or self.manifest_revision != opening.manifest_revision
            or self.editor_session_id != opening.editor_session_id
            or self._presentations != opening._presentations
            or self._opening_history is not opening._opening_history
            or self._clock is not opening._clock
        ):
            return False
        try:
            return opening._history.is_session_extension_of(
                opening._opening_history
            ) and self._history.is_session_extension_of(opening._opening_history)
        except ChecklistError:
            return False

    def _reorder_assignment_adjacent(
        self,
        assignment_id: UUID,
        direction: ChecklistOrderDirection,
        *,
        id_factory: Callable[[], UUID],
    ) -> Self:
        selected = self._history.assignment(assignment_id)
        row = self._history.projection.requirement(selected.requirement_id)
        ordered = tuple(value.assignment_id for value in row.assignments)
        replacement = _move_adjacent(ordered, assignment_id, direction)
        if replacement == ordered:
            return self
        return self.reorder_documents(
            selected.requirement_id,
            replacement,
            id_factory=id_factory,
        )

    def _reorder_page_adjacent(
        self,
        assignment_id: UUID,
        page_id: UUID,
        direction: ChecklistOrderDirection,
        *,
        id_factory: Callable[[], UUID],
    ) -> Self:
        selected = self._history.assignment(assignment_id)
        replacement = _move_adjacent(selected.page_ids, page_id, direction)
        if replacement == selected.page_ids:
            return self
        return self.reorder_pages(assignment_id, replacement, id_factory=id_factory)

    def _event_at(self, override: Callable[[], datetime] | None) -> datetime:
        if override is None:
            return self._clock.next_after(self._last_event_at)
        selected = _next_event_at(self._last_event_at, override)
        self._clock.include(selected)
        return selected

    def _execute_domain(
        self,
        command: ChecklistCommand,
        *,
        announcement: ChecklistAnnouncement,
        id_factory: Callable[[], UUID],
        last_event_at: datetime | None = None,
    ) -> Self:
        history = self._history.execute(command, id_factory=id_factory)
        if history is self._history:
            return self
        return self._transition(
            history,
            announcement,
            last_event_at=self._last_event_at if last_event_at is None else last_event_at,
        )

    def _transition(
        self,
        history: ChecklistCommandHistory,
        announcement: ChecklistAnnouncement,
        *,
        last_event_at: datetime | None = None,
    ) -> Self:
        return cast(
            Self,
            _state_from_history(
                case_id=self.case_id,
                manifest_revision=self.manifest_revision,
                editor_session_id=self.editor_session_id,
                history=history,
                opening_history=self._opening_history,
                presentations=self._presentations,
                last_event_at=self._last_event_at if last_event_at is None else last_event_at,
                clock=self._clock,
                announcement=announcement,
            ),
        )


@dataclass(frozen=True, slots=True, repr=False)
class _OpeningAuthority:
    editor_session_id: UUID
    case_id: UUID
    manifest_revision: int
    manifest_sha256: str = field(repr=False)
    manifest_cas_token: str = field(repr=False)
    opening_history: ChecklistCommandHistory = field(repr=False)


class ChecklistWorkflowService:
    """Load one exact template and hold one private, exclusive opening authority."""

    __slots__ = (
        "_active",
        "_as_of",
        "_clock",
        "_id_factory",
        "_loader",
        "_provider",
        "_template_key",
    )

    def __init__(
        self,
        loader: SafeTemplateLoader,
        template_key: TemplateKey,
        *,
        as_of: date,
        suggestion_provider: ChecklistSuggestionProvider | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if (
            not isinstance(loader, SafeTemplateLoader)
            or type(template_key) is not TemplateKey
            or type(as_of) is not date
            or (
                suggestion_provider is not None
                and not isinstance(suggestion_provider, ChecklistSuggestionProvider)
            )
            or not callable(clock)
            or not callable(id_factory)
        ):
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.workflow")
        self._loader = loader
        self._template_key = template_key
        self._as_of = as_of
        self._provider = suggestion_provider
        self._clock = _MonotonicUtcClock(clock)
        self._id_factory = id_factory
        self._active: _OpeningAuthority | None = None

    def begin(self, snapshot: ManifestSnapshot) -> ChecklistEditorState:
        """Open one revision-bound draft after validating the configured exact template."""

        if type(snapshot) is not ManifestSnapshot:
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.workflow_begin")
        if self._active is not None:
            reject_checklist(ChecklistErrorCode.CONCURRENT_CHANGE, "checklist.workflow_active")
        template = self._loader.load(self._template_key, as_of=self._as_of)
        definition, presentations = _template_contract(template)
        history = ChecklistCommandHistory.from_snapshot(snapshot, definition)
        editor_session_id = _new_editor_id(
            self._id_factory,
            {snapshot.case_id, *history.projection.active_page_ids},
        )
        state = _state_from_history(
            case_id=snapshot.case_id,
            manifest_revision=snapshot.revision,
            editor_session_id=editor_session_id,
            history=history,
            opening_history=history,
            presentations=presentations,
            last_event_at=_snapshot_updated_at(snapshot),
            clock=self._clock,
            announcement=ChecklistAnnouncement.READY,
        )
        state = self._add_provider_suggestions(state)
        self._active = _OpeningAuthority(
            editor_session_id,
            snapshot.case_id,
            snapshot.revision,
            snapshot.sha256,
            snapshot.cas_token,
            history,
        )
        return state

    def utc_now(self) -> datetime:
        """Return the next workflow timestamp for its paired autosave coordinator."""

        return self._clock()

    def to_candidate(
        self,
        opening_state: ChecklistEditorState,
        editor_state: ChecklistEditorState,
        current_snapshot: ManifestSnapshot,
    ) -> dict[str, object]:
        """Validate exact opening authority and privately reconstruct one manifest candidate."""

        authority = self._require_active(opening_state)
        if (
            type(editor_state) is not ChecklistEditorState
            or type(current_snapshot) is not ManifestSnapshot
            or not editor_state.matches_opening(opening_state)
            or current_snapshot.case_id != authority.case_id
            or current_snapshot.revision != authority.manifest_revision
            or current_snapshot.sha256 != authority.manifest_sha256
            or current_snapshot.cas_token != authority.manifest_cas_token
        ):
            reject_checklist(ChecklistErrorCode.CONCURRENT_CHANGE, "checklist.workflow_apply")
        return editor_state._history.to_candidate(current_snapshot)

    def complete(
        self,
        opening_state: ChecklistEditorState,
        editor_state: ChecklistEditorState,
    ) -> None:
        """Release authority only after the caller has durably saved or accepted no change."""

        self._require_active(opening_state)
        if type(editor_state) is not ChecklistEditorState or not editor_state.matches_opening(
            opening_state
        ):
            reject_checklist(ChecklistErrorCode.CONCURRENT_CHANGE, "checklist.workflow_complete")
        self._active = None

    def cancel(
        self,
        opening_state: ChecklistEditorState,
        editor_state: ChecklistEditorState,
    ) -> None:
        """Discard one matching draft without creating a manifest candidate."""

        self._require_active(opening_state)
        if type(editor_state) is not ChecklistEditorState or not editor_state.matches_opening(
            opening_state
        ):
            reject_checklist(ChecklistErrorCode.CONCURRENT_CHANGE, "checklist.workflow_cancel")
        self._active = None

    def abort(self, editor_session_id: UUID) -> None:
        """Release a backend draft whose presentation result could not be used."""

        active = self._active
        if (
            active is None
            or not _is_uuid(editor_session_id)
            or active.editor_session_id != editor_session_id
        ):
            reject_checklist(ChecklistErrorCode.CONCURRENT_CHANGE, "checklist.workflow_abort")
        self._active = None

    def _add_provider_suggestions(self, state: ChecklistEditorState) -> ChecklistEditorState:
        provider = self._provider
        if provider is None:
            return state
        projection = state._history.projection
        context = ChecklistSuggestionContext(
            state.case_id,
            state.manifest_revision,
            tuple(value.requirement_id for value in state.requirements),
            projection.active_page_ids,
            projection.unassigned_page_ids,
        )
        try:
            suggestions = provider.suggest(context)
        except BaseException:
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.suggestion_provider")
        if (
            type(suggestions) is not tuple
            or len(suggestions) > MAX_CHECKLIST_SUGGESTIONS
            or any(type(value) is not ChecklistSuggestion for value in suggestions)
        ):
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.suggestion_result")
        changed = state
        for suggestion in suggestions:
            changed = changed.suggest_pages(
                suggestion.requirement_id,
                suggestion.page_ids,
                id_factory=self._id_factory,
            )
        if suggestions:
            changed = changed._transition(
                changed._history,
                ChecklistAnnouncement.SUGGESTIONS_ADDED,
            )
        return changed

    def _require_active(self, opening_state: ChecklistEditorState) -> _OpeningAuthority:
        active = self._active
        if (
            type(opening_state) is not ChecklistEditorState
            or active is None
            or active.editor_session_id != opening_state.editor_session_id
            or active.case_id != opening_state.case_id
            or active.manifest_revision != opening_state.manifest_revision
            or active.opening_history is not opening_state._opening_history
            or not opening_state._history.is_session_extension_of(opening_state._opening_history)
        ):
            reject_checklist(ChecklistErrorCode.CONCURRENT_CHANGE, "checklist.workflow_opening")
        return active


def checklist_definition_from_template(template: TemplateSnapshot) -> ChecklistDefinition:
    """Convert one validated exact template snapshot to the pure domain definition."""

    return _template_contract(template)[0]


def is_checklist_announcement_safe(value: object) -> bool:
    """Return whether a value is one member of the closed announcement registry."""

    return type(value) is ChecklistAnnouncement


def _template_contract(
    template: TemplateSnapshot,
) -> tuple[ChecklistDefinition, tuple[_RequirementPresentation, ...]]:
    if type(template) is not TemplateSnapshot:
        reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.template_snapshot")
    document = template.document
    requirements = document.get("requirements")
    order = document.get("requirement_order")
    if type(requirements) is not list or type(order) is not list:
        reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.template_requirements")
    by_id: dict[str, Mapping[str, object]] = {}
    for value in requirements:
        if not isinstance(value, Mapping):
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.template_requirement")
        requirement_id = value.get("requirement_id")
        if type(requirement_id) is not str or requirement_id in by_id:
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.template_requirement")
        by_id[requirement_id] = cast(Mapping[str, object], value)
    if (
        not order
        or any(type(value) is not str for value in order)
        or len(set(cast(list[str], order))) != len(order)
        or set(cast(list[str], order)) != set(by_id)
    ):
        reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.template_order")
    domain_requirements: list[ChecklistRequirement] = []
    presentations: list[_RequirementPresentation] = []
    for requirement_id in cast(list[str], order):
        value = by_id[requirement_id]
        title = value.get("title")
        guidance = value.get("guidance")
        count = value.get("count")
        if (
            type(title) is not str
            or not title
            or type(guidance) is not str
            or not guidance
            or not isinstance(count, Mapping)
        ):
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.template_requirement")
        unit = count.get("unit")
        if type(unit) is not str:
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.template_count")
        try:
            count_unit = RequirementCountUnit(unit)
        except TypeError, ValueError:
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.template_count")
        minimum = count.get("minimum")
        maximum = count.get("maximum")
        if type(minimum) is not int or type(maximum) is not int:
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.template_count")
        domain_requirements.append(
            ChecklistRequirement(requirement_id, count_unit, minimum, maximum)
        )
        presentations.append(_RequirementPresentation(requirement_id, title, guidance))
    return (
        ChecklistDefinition(
            TemplateBinding(
                template.key.template_id,
                template.key.template_version,
                template.sha256,
            ),
            tuple(domain_requirements),
        ),
        tuple(presentations),
    )


def _state_from_history(
    *,
    case_id: UUID,
    manifest_revision: int,
    editor_session_id: UUID,
    history: ChecklistCommandHistory,
    opening_history: ChecklistCommandHistory,
    presentations: tuple[_RequirementPresentation, ...],
    last_event_at: datetime,
    clock: _MonotonicUtcClock,
    announcement: ChecklistAnnouncement,
) -> ChecklistEditorState:
    requirement_rows, page_rows, assignment_rows = _editor_views(history, presentations)
    return ChecklistEditorState(
        case_id,
        manifest_revision,
        editor_session_id,
        requirement_rows,
        page_rows,
        assignment_rows,
        history.can_undo,
        history.can_redo,
        _history_changed(history),
        announcement,
        history,
        opening_history,
        presentations,
        last_event_at,
        clock,
    )


def _editor_views(
    history: ChecklistCommandHistory,
    presentations: tuple[_RequirementPresentation, ...],
) -> tuple[
    tuple[ChecklistRequirementView, ...],
    tuple[ChecklistPageView, ...],
    tuple[ChecklistAssignmentView, ...],
]:
    projection = history.projection
    if len(presentations) != len(projection.requirements):
        reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.editor_presentations")
    requirement_rows: list[ChecklistRequirementView] = []
    for presentation, projected in zip(presentations, projection.requirements, strict=True):
        requirement = projected.requirement
        if presentation.requirement_id != requirement.requirement_id:
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.editor_presentations")
        requirement_rows.append(
            ChecklistRequirementView(
                requirement.requirement_id,
                presentation.display_name,
                presentation.description,
                requirement.count_unit,
                projected.status,
                projected.confirmed_count,
                requirement.minimum_count,
                requirement.maximum_count,
                tuple(value.assignment_id for value in projected.assignments),
            )
        )
    owners: dict[UUID, UUID] = {}
    owner_counts: dict[UUID, int] = {}
    for assignment in history.assignments:
        for page_id in assignment.page_ids:
            owners.setdefault(page_id, assignment.assignment_id)
            owner_counts[page_id] = owner_counts.get(page_id, 0) + 1
    active_page_ids = set(projection.active_page_ids)
    page_rows = tuple(
        ChecklistPageView(page_id, position, owners.get(page_id))
        for position, page_id in enumerate(projection.active_page_ids, start=1)
    )
    assignment_rows = tuple(
        ChecklistAssignmentView(
            value.assignment_id,
            value.requirement_id,
            value.page_ids,
            value.origin,
            value.review_state,
            any(
                page_id not in active_page_ids or owner_counts[page_id] > 1
                for page_id in value.page_ids
            ),
        )
        for value in history.assignments
    )
    return tuple(requirement_rows), page_rows, assignment_rows


def _history_changed(history: ChecklistCommandHistory) -> bool:
    value = getattr(history, "changed", history.can_undo)
    if type(value) is not bool:
        reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.history_changed")
    return value


def _snapshot_updated_at(snapshot: ManifestSnapshot) -> datetime:
    value = snapshot.document.get("updated_at")
    if type(value) is not str or not value.endswith("Z"):
        reject_checklist(ChecklistErrorCode.MANIFEST_INVALID, "checklist.updated_at")
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        reject_checklist(ChecklistErrorCode.MANIFEST_INVALID, "checklist.updated_at")
    if not _is_utc_datetime(parsed):
        reject_checklist(ChecklistErrorCode.MANIFEST_INVALID, "checklist.updated_at")
    return parsed


def _next_event_at(previous: datetime, clock: Callable[[], datetime]) -> datetime:
    if not callable(clock):
        reject_checklist(ChecklistErrorCode.TIMESTAMP_INVALID, "checklist.editor_clock")
    try:
        observed = clock()
    except BaseException:
        reject_checklist(ChecklistErrorCode.TIMESTAMP_INVALID, "checklist.editor_clock")
    if type(observed) is not datetime or observed.tzinfo is None or observed.utcoffset() is None:
        reject_checklist(ChecklistErrorCode.TIMESTAMP_INVALID, "checklist.editor_clock")
    selected = observed.astimezone(UTC)
    if selected <= previous:
        try:
            selected = previous + timedelta(microseconds=1)
        except OverflowError:
            reject_checklist(ChecklistErrorCode.TIMESTAMP_INVALID, "checklist.editor_clock")
    return selected


def _new_editor_id(factory: Callable[[], UUID], reserved: set[UUID]) -> UUID:
    for _ in range(MAX_EDITOR_IDENTIFIER_ATTEMPTS):
        try:
            value = factory()
        except BaseException:
            reject_checklist(ChecklistErrorCode.ID_GENERATION_FAILED, "checklist.editor_id")
        if _is_uuid(value) and value not in reserved:
            return value
    reject_checklist(ChecklistErrorCode.ID_GENERATION_FAILED, "checklist.editor_id")


def _move_adjacent[T](
    values: tuple[T, ...],
    selected: T,
    direction: ChecklistOrderDirection,
) -> tuple[T, ...]:
    if type(direction) is not ChecklistOrderDirection or selected not in values:
        reject_checklist(ChecklistErrorCode.ORDER_INVALID, "checklist.editor_order")
    index = values.index(selected)
    target = index - 1 if direction is ChecklistOrderDirection.EARLIER else index + 1
    if not 0 <= target < len(values):
        return values
    changed = list(values)
    changed[index], changed[target] = changed[target], changed[index]
    return tuple(changed)


def _validate_editor_page_command(
    editor_id: UUID,
    requirement_id: str,
    page_ids: tuple[UUID, ...],
) -> None:
    if (
        not _is_uuid(editor_id)
        or type(requirement_id) is not str
        or not requirement_id
        or not _is_uuid_tuple(page_ids, allow_empty=False)
    ):
        reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.editor_page_command")


def _validate_editor_assignment_command(editor_id: UUID, assignment_id: UUID) -> None:
    if not _is_uuid(editor_id) or not _is_uuid(assignment_id):
        reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.editor_assignment_command")


def _is_uuid(value: object) -> bool:
    return type(value) is UUID and value.version == 4


def _is_uuid_tuple(value: object, *, allow_empty: bool) -> bool:
    return (
        type(value) is tuple
        and (allow_empty or bool(value))
        and all(_is_uuid(item) for item in value)
        and len(set(value)) == len(value)
    )


def _is_utc_datetime(value: object) -> bool:
    return (
        type(value) is datetime and value.tzinfo is not None and value.utcoffset() == timedelta(0)
    )


__all__ = (
    "AcceptSuggestion",
    "AddPages",
    "ChecklistAnnouncement",
    "ChecklistAssignmentView",
    "ChecklistEditorCommand",
    "ChecklistEditorState",
    "ChecklistOrderDirection",
    "ChecklistPageView",
    "ChecklistRequirementView",
    "ChecklistSuggestion",
    "ChecklistSuggestionContext",
    "ChecklistSuggestionProvider",
    "ChecklistWorkflowService",
    "ConfirmAssignment",
    "MoveAssignment",
    "Redo",
    "RejectSuggestion",
    "RemoveAssignment",
    "ReorderAssignment",
    "ReorderAssignmentPage",
    "Undo",
    "checklist_definition_from_template",
    "is_checklist_announcement_safe",
)

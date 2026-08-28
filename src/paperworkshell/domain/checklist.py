"""Pure checklist assignment projection, commands, and manifest integration.

The released case-manifest schemas already define assignment and confirmation
records.  This module binds those inert records to the generic checklist
workflow without changing their schema meaning.  It performs no I/O, imports no
template or UI package, and treats ``ManifestSnapshot`` as an untrusted domain
boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Never, Self, cast
from uuid import UUID, uuid4

from paperworkshell.domain.manifest_repository import (
    MAX_SAFE_MANIFEST_INTEGER,
    ManifestSnapshot,
    copy_manifest_document,
)

MAX_CHECKLIST_REQUIREMENTS: Final = 128
MAX_CHECKLIST_ASSIGNMENTS: Final = 4_096
MAX_CHECKLIST_CONFIRMATIONS: Final = 4_096
MAX_CHECKLIST_COMMANDS: Final = 4_096
MAX_CHECKLIST_PAGES: Final = 4_096
MAX_ID_GENERATION_ATTEMPTS: Final = 32
ASSIGNMENT_CONFIRMATION_CODE: Final = "assignment-reviewed"
ASSIGNMENT_CONFIRMATION_VERSION: Final = 1
ASSIGNMENT_STATEMENT_VERSION: Final = 1

_TOKEN: Final = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*\Z")
_SEMVER: Final = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")
_CONTEXT: Final = re.compile(r"[a-z][a-z0-9_.]{0,79}\Z")
_UUID_V4: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
_UTC_TIMESTAMP: Final = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?Z\Z"
)

_ASSIGNMENT_MEMBERS: Final = frozenset(
    {
        "assignment_id",
        "requirement_id",
        "page_ids",
        "origin",
        "status",
        "confirmation_id",
        "assigned_at",
    }
)
_CONFIRMATION_MEMBERS: Final = frozenset(
    {
        "confirmation_id",
        "confirmation_version",
        "confirmation_code",
        "statement_version",
        "state",
        "actor",
        "scope",
        "recorded_at",
    }
)
_ASSIGNMENT_SCOPE_MEMBERS: Final = frozenset({"kind", "assignment_id"})
_TEMPLATE_REF_MEMBERS: Final = frozenset({"template_id", "template_version", "sha256"})


class ChecklistErrorCode(StrEnum):
    """Closed registry of content-free checklist failures."""

    MODEL_INVALID = "CHECKLIST_MODEL_INVALID"
    MANIFEST_INVALID = "CHECKLIST_MANIFEST_INVALID"
    TEMPLATE_REQUIRED = "CHECKLIST_TEMPLATE_REQUIRED"
    TEMPLATE_MISMATCH = "CHECKLIST_TEMPLATE_MISMATCH"
    REQUIREMENT_UNKNOWN = "CHECKLIST_REQUIREMENT_UNKNOWN"
    ASSIGNMENT_UNKNOWN = "CHECKLIST_ASSIGNMENT_UNKNOWN"
    PAGE_UNKNOWN = "CHECKLIST_PAGE_UNKNOWN"
    PAGE_INACTIVE = "CHECKLIST_PAGE_INACTIVE"
    PAGE_ALREADY_ASSIGNED = "CHECKLIST_PAGE_ALREADY_ASSIGNED"
    SELECTION_EMPTY = "CHECKLIST_SELECTION_EMPTY"
    ORDER_INVALID = "CHECKLIST_ORDER_INVALID"
    CAPACITY_EXCEEDED = "CHECKLIST_CAPACITY_EXCEEDED"
    IDENTIFIER_COLLISION = "CHECKLIST_IDENTIFIER_COLLISION"
    ID_GENERATION_FAILED = "CHECKLIST_ID_GENERATION_FAILED"
    TIMESTAMP_INVALID = "CHECKLIST_TIMESTAMP_INVALID"
    UNDO_UNAVAILABLE = "CHECKLIST_UNDO_UNAVAILABLE"
    REDO_UNAVAILABLE = "CHECKLIST_REDO_UNAVAILABLE"
    CONCURRENT_CHANGE = "CHECKLIST_CONCURRENT_CHANGE"


_SAFE_MESSAGES: Final[dict[ChecklistErrorCode, str]] = {
    ChecklistErrorCode.MODEL_INVALID: "PaperWorkShell received invalid checklist data.",
    ChecklistErrorCode.MANIFEST_INVALID: (
        "The saved checklist record is invalid and was not changed."
    ),
    ChecklistErrorCode.TEMPLATE_REQUIRED: (
        "Choose the checklist template that owns these saved assignments."
    ),
    ChecklistErrorCode.TEMPLATE_MISMATCH: (
        "The saved checklist belongs to a different template and was not changed."
    ),
    ChecklistErrorCode.REQUIREMENT_UNKNOWN: "Choose a checklist category that is available.",
    ChecklistErrorCode.ASSIGNMENT_UNKNOWN: "Choose a document assignment that is available.",
    ChecklistErrorCode.PAGE_UNKNOWN: "A selected page is not part of this case.",
    ChecklistErrorCode.PAGE_INACTIVE: "Restore the selected page before assigning it.",
    ChecklistErrorCode.PAGE_ALREADY_ASSIGNED: (
        "A selected page already has a pending or confirmed document assignment."
    ),
    ChecklistErrorCode.SELECTION_EMPTY: "Select at least one page for the document.",
    ChecklistErrorCode.ORDER_INVALID: "Provide one complete supported checklist order.",
    ChecklistErrorCode.CAPACITY_EXCEEDED: (
        "The case has reached the supported checklist assignment limit."
    ),
    ChecklistErrorCode.IDENTIFIER_COLLISION: (
        "A checklist record conflicts with another internal case identifier."
    ),
    ChecklistErrorCode.ID_GENERATION_FAILED: (
        "PaperWorkShell could not allocate a unique checklist identifier."
    ),
    ChecklistErrorCode.TIMESTAMP_INVALID: "PaperWorkShell received an invalid checklist time.",
    ChecklistErrorCode.UNDO_UNAVAILABLE: "There is no session checklist change to undo.",
    ChecklistErrorCode.REDO_UNAVAILABLE: "There is no session checklist change to redo.",
    ChecklistErrorCode.CONCURRENT_CHANGE: (
        "The checklist changed before this edit could be saved. Try the edit again."
    ),
}


@dataclass(slots=True, repr=False)
class ChecklistError(ValueError):
    """Stable failure that never retains template text, paths, or identifiers."""

    code: ChecklistErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not ChecklistErrorCode:
            self.code = ChecklistErrorCode.MODEL_INVALID
        if type(self.context) is not str or _CONTEXT.fullmatch(self.context) is None:
            self.context = "checklist.domain"
        self.safe_message = _SAFE_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"ChecklistError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_checklist(code: ChecklistErrorCode, context: str) -> Never:
    """Raise one sanitized checklist-domain failure."""

    raise ChecklistError(code, context) from None


class RequirementCountUnit(StrEnum):
    """Unit declared by one template requirement."""

    DOCUMENTS = "documents"
    PAGES = "pages"


class AssignmentOrigin(StrEnum):
    """Origin of the current category placement."""

    USER = "user"
    AUTOMATION = "automation"


class AssignmentReviewState(StrEnum):
    """Whether the user explicitly confirmed the assignment."""

    SUGGESTED = "suggested"
    USER_CONFIRMED = "user_confirmed"


class ChecklistStatus(StrEnum):
    """Textual, non-color-only checklist status."""

    MISSING = "missing"
    ADDED = "added"
    NEEDS_REVIEW = "needs_review"
    READY = "ready"


@dataclass(frozen=True, slots=True, repr=False)
class TemplateBinding:
    """Exact template identity recorded by a case manifest."""

    template_id: str
    template_version: str
    sha256: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.template_id) is not str
            or not 1 <= len(self.template_id) <= 128
            or _TOKEN.fullmatch(self.template_id) is None
            or type(self.template_version) is not str
            or not 1 <= len(self.template_version) <= 32
            or _SEMVER.fullmatch(self.template_version) is None
            or type(self.sha256) is not str
            or _SHA256.fullmatch(self.sha256) is None
        ):
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.template_binding")

    def __repr__(self) -> str:
        return (
            "TemplateBinding("
            f"template_id={self.template_id!r}, template_version={self.template_version!r})"
        )

    def to_manifest(self) -> dict[str, object]:
        return {
            "template_id": self.template_id,
            "template_version": self.template_version,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class ChecklistRequirement:
    """One ordered, bounded requirement rule without presentation text."""

    requirement_id: str
    count_unit: RequirementCountUnit
    minimum_count: int
    maximum_count: int

    def __post_init__(self) -> None:
        if (
            type(self.requirement_id) is not str
            or not 1 <= len(self.requirement_id) <= 128
            or _TOKEN.fullmatch(self.requirement_id) is None
            or type(self.count_unit) is not RequirementCountUnit
            or type(self.minimum_count) is not int
            or type(self.maximum_count) is not int
            or not 0 <= self.minimum_count <= self.maximum_count
            or not 1 <= self.maximum_count <= MAX_SAFE_MANIFEST_INTEGER
        ):
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.requirement")


@dataclass(frozen=True, slots=True, repr=False)
class ChecklistDefinition:
    """Exact template binding plus complete ordered requirement rules."""

    binding: TemplateBinding
    requirements: tuple[ChecklistRequirement, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.binding) is not TemplateBinding
            or type(self.requirements) is not tuple
            or not 1 <= len(self.requirements) <= MAX_CHECKLIST_REQUIREMENTS
            or any(type(item) is not ChecklistRequirement for item in self.requirements)
        ):
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.definition")
        identifiers = tuple(item.requirement_id for item in self.requirements)
        if len(set(identifiers)) != len(identifiers):
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.definition_ids")

    def __repr__(self) -> str:
        return (
            "ChecklistDefinition("
            f"binding={self.binding!r}, requirement_count={len(self.requirements)})"
        )

    def requirement(self, requirement_id: str) -> ChecklistRequirement:
        selected = _require_token(requirement_id, "checklist.requirement_id")
        for requirement in self.requirements:
            if requirement.requirement_id == selected:
                return requirement
        reject_checklist(ChecklistErrorCode.REQUIREMENT_UNKNOWN, "checklist.requirement_id")


@dataclass(frozen=True, slots=True, repr=False)
class ChecklistAssignment:
    """One logical document composed of ordered case pages."""

    assignment_id: UUID
    requirement_id: str
    page_ids: tuple[UUID, ...] = field(repr=False)
    origin: AssignmentOrigin
    review_state: AssignmentReviewState
    confirmation_id: UUID | None = field(repr=False)
    assigned_at: datetime = field(repr=False)

    def __post_init__(self) -> None:
        _require_uuid(self.assignment_id, "checklist.assignment_id")
        _require_token(self.requirement_id, "checklist.assignment_requirement")
        _require_page_tuple(self.page_ids, context="checklist.assignment_pages")
        if (
            type(self.origin) is not AssignmentOrigin
            or type(self.review_state) is not AssignmentReviewState
        ):
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.assignment_state")
        if self.review_state is AssignmentReviewState.SUGGESTED:
            if self.confirmation_id is not None:
                reject_checklist(
                    ChecklistErrorCode.MODEL_INVALID,
                    "checklist.assignment_confirmation",
                )
        elif self.confirmation_id is None:
            reject_checklist(
                ChecklistErrorCode.MODEL_INVALID,
                "checklist.assignment_confirmation",
            )
        else:
            _require_uuid(self.confirmation_id, "checklist.assignment_confirmation")
        _require_utc_datetime(self.assigned_at, "checklist.assignment_time")

    def __repr__(self) -> str:
        return (
            "ChecklistAssignment("
            f"page_count={len(self.page_ids)}, origin={self.origin.value!r}, "
            f"review_state={self.review_state.value!r})"
        )

    def to_manifest(self) -> dict[str, object]:
        return {
            "assignment_id": str(self.assignment_id),
            "requirement_id": self.requirement_id,
            "page_ids": [str(page_id) for page_id in self.page_ids],
            "origin": self.origin.value,
            "status": self.review_state.value,
            "confirmation_id": (
                None if self.confirmation_id is None else str(self.confirmation_id)
            ),
            "assigned_at": _render_utc_timestamp(self.assigned_at),
        }


@dataclass(frozen=True, slots=True, repr=False)
class AssignmentScopedConfirmation:
    """One released confirmation record scoped to an assignment."""

    confirmation_id: UUID
    assignment_id: UUID
    confirmation_code: str
    statement_version: int
    state: str
    recorded_at: datetime = field(repr=False)

    def __post_init__(self) -> None:
        _require_uuid(self.confirmation_id, "checklist.confirmation_id")
        _require_uuid(self.assignment_id, "checklist.confirmation_assignment")
        _require_token(self.confirmation_code, "checklist.confirmation_code")
        if (
            type(self.statement_version) is not int
            or not 1 <= self.statement_version <= MAX_SAFE_MANIFEST_INTEGER
            or self.state not in {"confirmed", "withdrawn"}
        ):
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.confirmation")
        _require_utc_datetime(self.recorded_at, "checklist.confirmation_time")

    def __repr__(self) -> str:
        return (
            "AssignmentScopedConfirmation("
            f"confirmation_code={self.confirmation_code!r}, state={self.state!r})"
        )

    def to_manifest(self) -> dict[str, object]:
        return {
            "confirmation_id": str(self.confirmation_id),
            "confirmation_version": ASSIGNMENT_CONFIRMATION_VERSION,
            "confirmation_code": self.confirmation_code,
            "statement_version": self.statement_version,
            "state": self.state,
            "actor": "user",
            "scope": {
                "kind": "assignment",
                "assignment_id": str(self.assignment_id),
            },
            "recorded_at": _render_utc_timestamp(self.recorded_at),
        }


@dataclass(frozen=True, slots=True, repr=False)
class ChecklistRequirementProjection:
    """One requirement's deterministic status and document order."""

    requirement: ChecklistRequirement
    assignments: tuple[ChecklistAssignment, ...] = field(repr=False)
    confirmed_count: int
    status: ChecklistStatus

    def __post_init__(self) -> None:
        if (
            type(self.requirement) is not ChecklistRequirement
            or type(self.assignments) is not tuple
            or any(type(item) is not ChecklistAssignment for item in self.assignments)
            or any(
                item.requirement_id != self.requirement.requirement_id for item in self.assignments
            )
            or type(self.confirmed_count) is not int
            or not 0 <= self.confirmed_count <= MAX_CHECKLIST_PAGES
            or type(self.status) is not ChecklistStatus
        ):
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.projection_requirement")

    def __repr__(self) -> str:
        return (
            "ChecklistRequirementProjection("
            f"assignment_count={len(self.assignments)}, confirmed_count={self.confirmed_count}, "
            f"status={self.status.value!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class ChecklistProjection:
    """Complete path-free checklist state in stable template/page order."""

    requirements: tuple[ChecklistRequirementProjection, ...] = field(repr=False)
    active_page_ids: tuple[UUID, ...] = field(repr=False)
    unassigned_page_ids: tuple[UUID, ...] = field(repr=False)
    unconfirmed_page_ids: tuple[UUID, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.requirements) is not tuple or any(
            type(item) is not ChecklistRequirementProjection for item in self.requirements
        ):
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.projection")
        for value in (
            self.active_page_ids,
            self.unassigned_page_ids,
            self.unconfirmed_page_ids,
        ):
            if (
                type(value) is not tuple
                or len(value) > MAX_CHECKLIST_PAGES
                or any(type(item) is not UUID or item.version != 4 for item in value)
                or len(set(value)) != len(value)
            ):
                reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.projection_pages")
        active = set(self.active_page_ids)
        if (
            not set(self.unassigned_page_ids) <= active
            or not set(self.unconfirmed_page_ids) <= active
        ):
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.projection_pages")

    def __repr__(self) -> str:
        return (
            "ChecklistProjection("
            f"requirement_count={len(self.requirements)}, active_page_count={len(self.active_page_ids)}, "
            f"unassigned_page_count={len(self.unassigned_page_ids)}, "
            f"unconfirmed_page_count={len(self.unconfirmed_page_ids)})"
        )

    def requirement(self, requirement_id: str) -> ChecklistRequirementProjection:
        selected = _require_token(requirement_id, "checklist.projection_requirement")
        for item in self.requirements:
            if item.requirement.requirement_id == selected:
                return item
        reject_checklist(ChecklistErrorCode.REQUIREMENT_UNKNOWN, "checklist.projection_requirement")


@dataclass(frozen=True, slots=True, repr=False)
class AddDocumentAssignmentCommand:
    """Place one user-selected logical document pending explicit confirmation."""

    requirement_id: str
    page_ids: tuple[UUID, ...] = field(repr=False)
    assigned_at: datetime = field(repr=False)

    def __post_init__(self) -> None:
        _require_token(self.requirement_id, "checklist.command_requirement")
        _require_page_tuple(self.page_ids, context="checklist.command_pages")
        _require_utc_datetime(self.assigned_at, "checklist.command_time")


@dataclass(frozen=True, slots=True, repr=False)
class SuggestDocumentAssignmentCommand:
    """Record one automation suggestion that cannot satisfy a requirement."""

    requirement_id: str
    page_ids: tuple[UUID, ...] = field(repr=False)
    assigned_at: datetime = field(repr=False)

    def __post_init__(self) -> None:
        _require_token(self.requirement_id, "checklist.command_requirement")
        _require_page_tuple(self.page_ids, context="checklist.command_pages")
        _require_utc_datetime(self.assigned_at, "checklist.command_time")


@dataclass(frozen=True, slots=True, repr=False)
class ConfirmAssignmentCommand:
    """Explicitly confirm one pending user placement or automation suggestion."""

    assignment_id: UUID
    confirmed_at: datetime = field(repr=False)

    def __post_init__(self) -> None:
        _require_uuid(self.assignment_id, "checklist.command_assignment")
        _require_utc_datetime(self.confirmed_at, "checklist.command_time")


@dataclass(frozen=True, slots=True, repr=False)
class MoveAssignmentCommand:
    """Move a document to another requirement and require confirmation again."""

    assignment_id: UUID
    requirement_id: str
    moved_at: datetime = field(repr=False)

    def __post_init__(self) -> None:
        _require_uuid(self.assignment_id, "checklist.command_assignment")
        _require_token(self.requirement_id, "checklist.command_requirement")
        _require_utc_datetime(self.moved_at, "checklist.command_time")


@dataclass(frozen=True, slots=True, repr=False)
class RemoveAssignmentCommand:
    """Remove a pending or confirmed logical document assignment."""

    assignment_id: UUID

    def __post_init__(self) -> None:
        _require_uuid(self.assignment_id, "checklist.command_assignment")


@dataclass(frozen=True, slots=True, repr=False)
class ReplaceAssignmentPagesCommand:
    """Replace document membership and require explicit confirmation again."""

    assignment_id: UUID
    page_ids: tuple[UUID, ...] = field(repr=False)
    changed_at: datetime = field(repr=False)

    def __post_init__(self) -> None:
        _require_uuid(self.assignment_id, "checklist.command_assignment")
        _require_page_tuple(self.page_ids, context="checklist.command_pages")
        _require_utc_datetime(self.changed_at, "checklist.command_time")


@dataclass(frozen=True, slots=True, repr=False)
class ReorderRequirementAssignmentsCommand:
    """Provide one complete document order for a requirement."""

    requirement_id: str
    assignment_ids: tuple[UUID, ...] = field(repr=False)

    def __post_init__(self) -> None:
        _require_token(self.requirement_id, "checklist.command_requirement")
        _require_uuid_tuple(
            self.assignment_ids,
            allow_empty=True,
            context="checklist.command_assignment_order",
        )


@dataclass(frozen=True, slots=True, repr=False)
class ReorderAssignmentPagesCommand:
    """Provide one complete page order without changing document membership."""

    assignment_id: UUID
    page_ids: tuple[UUID, ...] = field(repr=False)

    def __post_init__(self) -> None:
        _require_uuid(self.assignment_id, "checklist.command_assignment")
        _require_page_tuple(self.page_ids, context="checklist.command_page_order")


type ChecklistCommand = (
    AddDocumentAssignmentCommand
    | SuggestDocumentAssignmentCommand
    | ConfirmAssignmentCommand
    | MoveAssignmentCommand
    | RemoveAssignmentCommand
    | ReplaceAssignmentPagesCommand
    | ReorderRequirementAssignmentsCommand
    | ReorderAssignmentPagesCommand
)


@dataclass(frozen=True, slots=True)
class _AssignmentEdit:
    index: int
    before: ChecklistAssignment | None
    after: ChecklistAssignment | None


@dataclass(frozen=True, slots=True)
class _ConfirmationEdit:
    index: int
    before: AssignmentScopedConfirmation | None
    after: AssignmentScopedConfirmation | None


@dataclass(frozen=True, slots=True, repr=False)
class _ChecklistDelta:
    assignment_edits: tuple[_AssignmentEdit, ...]
    confirmation_edits: tuple[_ConfirmationEdit, ...]
    template_before: TemplateBinding | None
    template_after: TemplateBinding | None


@dataclass(frozen=True, slots=True, repr=False)
class _ChecklistState:
    template_binding: TemplateBinding | None
    assignments: tuple[ChecklistAssignment, ...]
    confirmations: tuple[AssignmentScopedConfirmation, ...]


@dataclass(frozen=True, slots=True, repr=False)
class _ParsedSnapshot:
    document: dict[str, object] = field(repr=False)
    case_id: UUID
    revision: int
    state: _ChecklistState = field(repr=False)
    known_page_ids: tuple[UUID, ...] = field(repr=False)
    active_page_ids: tuple[UUID, ...] = field(repr=False)
    complete_namespace: frozenset[UUID] = field(repr=False)
    non_checklist_namespace: frozenset[UUID] = field(repr=False)
    non_assignment_confirmation_count: int


@dataclass(frozen=True, slots=True, init=False, repr=False)
class ChecklistCommandHistory:
    """Immutable bounded session history over durable checklist intent."""

    _case_id: UUID
    _origin_revision: int
    _definition: ChecklistDefinition
    _known_page_ids: tuple[UUID, ...]
    _active_page_ids: tuple[UUID, ...]
    _origin_namespace: frozenset[UUID]
    _origin_state: _ChecklistState
    _non_assignment_confirmation_count: int
    _state: _ChecklistState
    _applied_deltas: tuple[_ChecklistDelta, ...]
    _redo_deltas: tuple[_ChecklistDelta, ...]
    _owned_deltas: tuple[_ChecklistDelta, ...]
    _known_state_keys: frozenset[bytes]
    _reserved_ids: frozenset[UUID]

    @classmethod
    def from_snapshot(
        cls,
        snapshot: ManifestSnapshot,
        definition: ChecklistDefinition,
    ) -> Self:
        """Start a clean session over one exact manifest/template binding."""

        if type(snapshot) is not ManifestSnapshot or type(definition) is not ChecklistDefinition:
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.history_open")
        parsed = _parse_snapshot(snapshot, definition)
        return cls._create(
            case_id=parsed.case_id,
            origin_revision=parsed.revision,
            definition=definition,
            known_page_ids=parsed.known_page_ids,
            active_page_ids=parsed.active_page_ids,
            origin_namespace=parsed.complete_namespace,
            origin_state=parsed.state,
            non_assignment_confirmation_count=(parsed.non_assignment_confirmation_count),
            state=parsed.state,
            applied_deltas=(),
            redo_deltas=(),
            owned_deltas=(),
            known_state_keys=frozenset({_state_key(parsed.state)}),
            reserved_ids=parsed.complete_namespace,
        )

    @classmethod
    def _create(
        cls,
        *,
        case_id: UUID,
        origin_revision: int,
        definition: ChecklistDefinition,
        known_page_ids: tuple[UUID, ...],
        active_page_ids: tuple[UUID, ...],
        origin_namespace: frozenset[UUID],
        origin_state: _ChecklistState,
        non_assignment_confirmation_count: int,
        state: _ChecklistState,
        applied_deltas: tuple[_ChecklistDelta, ...],
        redo_deltas: tuple[_ChecklistDelta, ...],
        owned_deltas: tuple[_ChecklistDelta, ...],
        known_state_keys: frozenset[bytes],
        reserved_ids: frozenset[UUID],
    ) -> Self:
        instance = object.__new__(cls)
        object.__setattr__(instance, "_case_id", case_id)
        object.__setattr__(instance, "_origin_revision", origin_revision)
        object.__setattr__(instance, "_definition", definition)
        object.__setattr__(instance, "_known_page_ids", known_page_ids)
        object.__setattr__(instance, "_active_page_ids", active_page_ids)
        object.__setattr__(instance, "_origin_namespace", origin_namespace)
        object.__setattr__(instance, "_origin_state", origin_state)
        object.__setattr__(
            instance,
            "_non_assignment_confirmation_count",
            non_assignment_confirmation_count,
        )
        object.__setattr__(instance, "_state", state)
        object.__setattr__(instance, "_applied_deltas", applied_deltas)
        object.__setattr__(instance, "_redo_deltas", redo_deltas)
        object.__setattr__(instance, "_owned_deltas", owned_deltas)
        object.__setattr__(instance, "_known_state_keys", known_state_keys)
        object.__setattr__(instance, "_reserved_ids", reserved_ids)
        return instance

    def __repr__(self) -> str:
        return (
            "ChecklistCommandHistory("
            f"assignment_count={len(self._state.assignments)}, "
            f"undo_count={len(self._applied_deltas)}, redo_count={len(self._redo_deltas)})"
        )

    @property
    def assignments(self) -> tuple[ChecklistAssignment, ...]:
        return self._state.assignments

    @property
    def projection(self) -> ChecklistProjection:
        return _project_checklist(
            self._definition,
            self._active_page_ids,
            self._state.assignments,
        )

    @property
    def can_undo(self) -> bool:
        return bool(self._applied_deltas)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_deltas)

    @property
    def changed(self) -> bool:
        """Return whether durable checklist intent differs from the opening state."""

        return self._state != self._origin_state

    def is_session_extension_of(self, opening: ChecklistCommandHistory) -> bool:
        """Authenticate this value as a replayable extension of a clean opening."""

        if type(opening) is not ChecklistCommandHistory:
            return False
        origin_key = _state_key(opening._origin_state)
        if (
            opening._applied_deltas
            or opening._redo_deltas
            or opening._owned_deltas
            or opening._state != opening._origin_state
            or opening._known_state_keys != frozenset({origin_key})
            or opening._reserved_ids != opening._origin_namespace
            or self._case_id != opening._case_id
            or self._origin_revision != opening._origin_revision
            or self._definition != opening._definition
            or self._known_page_ids != opening._known_page_ids
            or self._active_page_ids != opening._active_page_ids
            or self._origin_namespace != opening._origin_namespace
            or self._origin_state != opening._origin_state
            or self._non_assignment_confirmation_count != opening._non_assignment_confirmation_count
            or len(self._owned_deltas) > MAX_CHECKLIST_COMMANDS
            or any(type(key) is not bytes or len(key) != 32 for key in self._known_state_keys)
            or origin_key not in self._known_state_keys
            or _state_key(self._state) not in self._known_state_keys
        ):
            return False
        owned_counts = Counter(self._owned_deltas)
        used_counts = Counter(self._applied_deltas + self._redo_deltas)
        if any(count > owned_counts[delta] for delta, count in used_counts.items()):
            return False
        generated_ids = _generated_ids(self._owned_deltas)
        if self._reserved_ids != self._origin_namespace | generated_ids:
            return False

        replayed = self._origin_state
        try:
            for delta in self._applied_deltas:
                replayed = _apply_delta(replayed, delta, forward=True)
                _validate_runtime_state(
                    replayed,
                    self._definition,
                    self._known_page_ids,
                    self._non_assignment_confirmation_count,
                )
            if replayed != self._state:
                return False
            redo_state = replayed
            for delta in self._redo_deltas:
                redo_state = _apply_delta(redo_state, delta, forward=True)
                _validate_runtime_state(
                    redo_state,
                    self._definition,
                    self._known_page_ids,
                    self._non_assignment_confirmation_count,
                )
        except ChecklistError:
            return False
        return True

    def assignment(self, assignment_id: UUID) -> ChecklistAssignment:
        selected = _require_uuid(assignment_id, "checklist.history_assignment")
        for assignment in self._state.assignments:
            if assignment.assignment_id == selected:
                return assignment
        reject_checklist(ChecklistErrorCode.ASSIGNMENT_UNKNOWN, "checklist.history_assignment")

    def execute(
        self,
        command: ChecklistCommand,
        *,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> Self:
        """Apply one exact command, invalidating any previous redo branch."""

        if type(command) not in {
            AddDocumentAssignmentCommand,
            SuggestDocumentAssignmentCommand,
            ConfirmAssignmentCommand,
            MoveAssignmentCommand,
            RemoveAssignmentCommand,
            ReplaceAssignmentPagesCommand,
            ReorderRequirementAssignmentsCommand,
            ReorderAssignmentPagesCommand,
        }:
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.history_command")
        if not callable(id_factory):
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.history_id_factory")
        if len(self._owned_deltas) >= MAX_CHECKLIST_COMMANDS:
            reject_checklist(ChecklistErrorCode.CAPACITY_EXCEEDED, "checklist.history_commands")

        generated_ids: set[UUID] = set()
        delta = self._delta_for(command, id_factory=id_factory, generated_ids=generated_ids)
        if delta is None:
            return self
        state = _apply_delta(self._state, delta, forward=True)
        _validate_runtime_state(
            state,
            self._definition,
            self._known_page_ids,
            self._non_assignment_confirmation_count,
        )
        return self._transition(
            state=state,
            applied_deltas=self._applied_deltas + (delta,),
            redo_deltas=(),
            owned_deltas=self._owned_deltas + (delta,),
            known_state_keys=self._known_state_keys | {_state_key(state)},
            reserved_ids=self._reserved_ids | generated_ids,
        )

    def undo(self) -> Self:
        """Reverse exactly one session command; loaded intent is not undoable."""

        if not self._applied_deltas:
            reject_checklist(ChecklistErrorCode.UNDO_UNAVAILABLE, "checklist.history_undo")
        delta = self._applied_deltas[-1]
        state = _apply_delta(self._state, delta, forward=False)
        _validate_runtime_state(
            state,
            self._definition,
            self._known_page_ids,
            self._non_assignment_confirmation_count,
        )
        return self._transition(
            state=state,
            applied_deltas=self._applied_deltas[:-1],
            redo_deltas=(delta,) + self._redo_deltas,
            owned_deltas=self._owned_deltas,
            known_state_keys=self._known_state_keys | {_state_key(state)},
            reserved_ids=self._reserved_ids,
        )

    def redo(self) -> Self:
        """Reapply the exact next command, including original IDs and times."""

        if not self._redo_deltas:
            reject_checklist(ChecklistErrorCode.REDO_UNAVAILABLE, "checklist.history_redo")
        delta = self._redo_deltas[0]
        state = _apply_delta(self._state, delta, forward=True)
        _validate_runtime_state(
            state,
            self._definition,
            self._known_page_ids,
            self._non_assignment_confirmation_count,
        )
        return self._transition(
            state=state,
            applied_deltas=self._applied_deltas + (delta,),
            redo_deltas=self._redo_deltas[1:],
            owned_deltas=self._owned_deltas,
            known_state_keys=self._known_state_keys | {_state_key(state)},
            reserved_ids=self._reserved_ids,
        )

    def to_candidate(self, current_snapshot: ManifestSnapshot) -> dict[str, object]:
        """Rebase only a history-authenticated checklist state onto current unrelated data."""

        if type(current_snapshot) is not ManifestSnapshot:
            reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.history_candidate")
        current = _parse_snapshot(current_snapshot, self._definition)
        if (
            current.case_id != self._case_id
            or current.revision < self._origin_revision
            or current.known_page_ids != self._known_page_ids
            or current.active_page_ids != self._active_page_ids
            or _state_key(current.state) not in self._known_state_keys
            or not self._state_is_history_owned(current.state)
        ):
            reject_checklist(ChecklistErrorCode.CONCURRENT_CHANGE, "checklist.history_rebase")

        target_ids = {assignment.assignment_id for assignment in self._state.assignments} | {
            confirmation.confirmation_id for confirmation in self._state.confirmations
        }
        if target_ids & set(current.non_checklist_namespace):
            reject_checklist(ChecklistErrorCode.CONCURRENT_CHANGE, "checklist.history_namespace")

        candidate = current.document
        candidate["template_ref"] = (
            None
            if self._state.template_binding is None
            else self._state.template_binding.to_manifest()
        )
        candidate["assignments"] = [
            assignment.to_manifest() for assignment in self._state.assignments
        ]
        raw_confirmations = _require_list(candidate.get("confirmations"), "checklist.confirmations")
        if (
            current.non_assignment_confirmation_count + len(self._state.confirmations)
            > MAX_CHECKLIST_CONFIRMATIONS
        ):
            reject_checklist(
                ChecklistErrorCode.CAPACITY_EXCEEDED,
                "checklist.confirmations",
            )
        candidate["confirmations"] = _merge_assignment_confirmations(
            raw_confirmations,
            self._state.confirmations,
        )
        return candidate

    def _delta_for(
        self,
        command: ChecklistCommand,
        *,
        id_factory: Callable[[], UUID],
        generated_ids: set[UUID],
    ) -> _ChecklistDelta | None:
        binding_before = self._state.template_binding
        binding_after = binding_before
        if type(command) in {AddDocumentAssignmentCommand, SuggestDocumentAssignmentCommand}:
            add_command = cast(
                AddDocumentAssignmentCommand | SuggestDocumentAssignmentCommand,
                command,
            )
            self._definition.requirement(add_command.requirement_id)
            self._require_new_pages(add_command.page_ids)
            if len(self._state.assignments) >= MAX_CHECKLIST_ASSIGNMENTS:
                reject_checklist(
                    ChecklistErrorCode.CAPACITY_EXCEEDED,
                    "checklist.assignments",
                )
            assignment_id = _generate_id(id_factory, self._reserved_ids | generated_ids)
            generated_ids.add(assignment_id)
            origin = (
                AssignmentOrigin.USER
                if type(command) is AddDocumentAssignmentCommand
                else AssignmentOrigin.AUTOMATION
            )
            assignment = ChecklistAssignment(
                assignment_id,
                add_command.requirement_id,
                add_command.page_ids,
                origin,
                AssignmentReviewState.SUGGESTED,
                None,
                add_command.assigned_at,
            )
            if binding_after is None:
                binding_after = self._definition.binding
            return _ChecklistDelta(
                (_AssignmentEdit(len(self._state.assignments), None, assignment),),
                (),
                binding_before,
                binding_after,
            )

        if type(command) is ConfirmAssignmentCommand:
            index, before = self._assignment_with_index(command.assignment_id)
            if before.review_state is AssignmentReviewState.USER_CONFIRMED:
                return None
            self._require_confirmable_pages(before)
            if command.confirmed_at < before.assigned_at:
                reject_checklist(ChecklistErrorCode.TIMESTAMP_INVALID, "checklist.confirm_time")
            if (
                self._non_assignment_confirmation_count + len(self._state.confirmations)
                >= MAX_CHECKLIST_CONFIRMATIONS
            ):
                reject_checklist(
                    ChecklistErrorCode.CAPACITY_EXCEEDED,
                    "checklist.confirmations",
                )
            confirmation_id = _generate_id(id_factory, self._reserved_ids | generated_ids)
            generated_ids.add(confirmation_id)
            confirmation = AssignmentScopedConfirmation(
                confirmation_id,
                before.assignment_id,
                ASSIGNMENT_CONFIRMATION_CODE,
                ASSIGNMENT_STATEMENT_VERSION,
                "confirmed",
                command.confirmed_at,
            )
            after = ChecklistAssignment(
                before.assignment_id,
                before.requirement_id,
                before.page_ids,
                before.origin,
                AssignmentReviewState.USER_CONFIRMED,
                confirmation_id,
                before.assigned_at,
            )
            return _ChecklistDelta(
                (_AssignmentEdit(index, before, after),),
                (
                    _ConfirmationEdit(
                        len(self._state.confirmations),
                        None,
                        confirmation,
                    ),
                ),
                binding_before,
                binding_after,
            )

        if type(command) is MoveAssignmentCommand:
            self._definition.requirement(command.requirement_id)
            index, before = self._assignment_with_index(command.assignment_id)
            if before.requirement_id == command.requirement_id:
                return None
            after = ChecklistAssignment(
                before.assignment_id,
                command.requirement_id,
                before.page_ids,
                AssignmentOrigin.USER,
                AssignmentReviewState.SUGGESTED,
                None,
                command.moved_at,
            )
            return self._replacement_delta(index, before, after)

        if type(command) is RemoveAssignmentCommand:
            index, before = self._assignment_with_index(command.assignment_id)
            return _ChecklistDelta(
                (_AssignmentEdit(index, before, None),),
                self._remove_confirmation_edits(before.assignment_id),
                binding_before,
                binding_after,
            )

        if type(command) is ReplaceAssignmentPagesCommand:
            index, before = self._assignment_with_index(command.assignment_id)
            if before.page_ids == command.page_ids:
                return None
            self._require_new_pages(command.page_ids, excluding=before.assignment_id)
            after = ChecklistAssignment(
                before.assignment_id,
                before.requirement_id,
                command.page_ids,
                AssignmentOrigin.USER,
                AssignmentReviewState.SUGGESTED,
                None,
                command.changed_at,
            )
            return self._replacement_delta(index, before, after)

        if type(command) is ReorderRequirementAssignmentsCommand:
            self._definition.requirement(command.requirement_id)
            indexed = tuple(
                (index, assignment)
                for index, assignment in enumerate(self._state.assignments)
                if assignment.requirement_id == command.requirement_id
            )
            before_ids = tuple(assignment.assignment_id for _, assignment in indexed)
            if len(command.assignment_ids) != len(before_ids) or set(command.assignment_ids) != set(
                before_ids
            ):
                reject_checklist(ChecklistErrorCode.ORDER_INVALID, "checklist.assignment_order")
            if command.assignment_ids == before_ids:
                return None
            by_id = {assignment.assignment_id: assignment for _, assignment in indexed}
            edits = tuple(
                _AssignmentEdit(index, before, by_id[after_id])
                for (index, before), after_id in zip(indexed, command.assignment_ids, strict=True)
            )
            return _ChecklistDelta(edits, (), binding_before, binding_after)

        if type(command) is ReorderAssignmentPagesCommand:
            index, before = self._assignment_with_index(command.assignment_id)
            if len(command.page_ids) != len(before.page_ids) or set(command.page_ids) != set(
                before.page_ids
            ):
                reject_checklist(ChecklistErrorCode.ORDER_INVALID, "checklist.page_order")
            if command.page_ids == before.page_ids:
                return None
            after = ChecklistAssignment(
                before.assignment_id,
                before.requirement_id,
                command.page_ids,
                before.origin,
                before.review_state,
                before.confirmation_id,
                before.assigned_at,
            )
            return _ChecklistDelta(
                (_AssignmentEdit(index, before, after),),
                (),
                binding_before,
                binding_after,
            )

        reject_checklist(ChecklistErrorCode.MODEL_INVALID, "checklist.history_command")

    def _replacement_delta(
        self,
        index: int,
        before: ChecklistAssignment,
        after: ChecklistAssignment,
    ) -> _ChecklistDelta:
        return _ChecklistDelta(
            (_AssignmentEdit(index, before, after),),
            self._remove_confirmation_edits(before.assignment_id),
            self._state.template_binding,
            self._state.template_binding,
        )

    def _remove_confirmation_edits(
        self,
        assignment_id: UUID,
    ) -> tuple[_ConfirmationEdit, ...]:
        return tuple(
            _ConfirmationEdit(index, confirmation, None)
            for index, confirmation in reversed(tuple(enumerate(self._state.confirmations)))
            if confirmation.assignment_id == assignment_id
        )

    def _state_is_history_owned(self, state: _ChecklistState) -> bool:
        allowed_assignments: dict[UUID, set[ChecklistAssignment]] = {}
        allowed_confirmations: dict[UUID, set[AssignmentScopedConfirmation]] = {}
        for assignment in self._origin_state.assignments:
            allowed_assignments.setdefault(assignment.assignment_id, set()).add(assignment)
        for confirmation in self._origin_state.confirmations:
            allowed_confirmations.setdefault(confirmation.confirmation_id, set()).add(confirmation)
        for delta in self._owned_deltas:
            for assignment_edit in delta.assignment_edits:
                if assignment_edit.before is not None:
                    allowed_assignments.setdefault(assignment_edit.before.assignment_id, set()).add(
                        assignment_edit.before
                    )
                if assignment_edit.after is not None:
                    allowed_assignments.setdefault(assignment_edit.after.assignment_id, set()).add(
                        assignment_edit.after
                    )
            for confirmation_edit in delta.confirmation_edits:
                if confirmation_edit.before is not None:
                    allowed_confirmations.setdefault(
                        confirmation_edit.before.confirmation_id, set()
                    ).add(confirmation_edit.before)
                if confirmation_edit.after is not None:
                    allowed_confirmations.setdefault(
                        confirmation_edit.after.confirmation_id, set()
                    ).add(confirmation_edit.after)
        return (
            state.template_binding
            in {self._origin_state.template_binding, self._definition.binding}
            and all(
                assignment in allowed_assignments.get(assignment.assignment_id, set())
                for assignment in state.assignments
            )
            and all(
                confirmation in allowed_confirmations.get(confirmation.confirmation_id, set())
                for confirmation in state.confirmations
            )
        )

    def _assignment_with_index(self, assignment_id: UUID) -> tuple[int, ChecklistAssignment]:
        selected = _require_uuid(assignment_id, "checklist.history_assignment")
        for index, assignment in enumerate(self._state.assignments):
            if assignment.assignment_id == selected:
                return index, assignment
        reject_checklist(ChecklistErrorCode.ASSIGNMENT_UNKNOWN, "checklist.history_assignment")

    def _require_new_pages(
        self,
        page_ids: tuple[UUID, ...],
        *,
        excluding: UUID | None = None,
    ) -> None:
        active = set(self._active_page_ids)
        for page_id in page_ids:
            if page_id not in set(self._known_page_ids):
                reject_checklist(ChecklistErrorCode.PAGE_UNKNOWN, "checklist.command_page")
            if page_id not in active:
                reject_checklist(ChecklistErrorCode.PAGE_INACTIVE, "checklist.command_page")
        occupied = {
            page_id
            for assignment in self._state.assignments
            if assignment.assignment_id != excluding
            for page_id in assignment.page_ids
        }
        if occupied & set(page_ids):
            reject_checklist(
                ChecklistErrorCode.PAGE_ALREADY_ASSIGNED,
                "checklist.command_page",
            )

    def _require_confirmable_pages(self, selected: ChecklistAssignment) -> None:
        active = set(self._active_page_ids)
        if any(page_id not in active for page_id in selected.page_ids):
            reject_checklist(ChecklistErrorCode.PAGE_INACTIVE, "checklist.confirm_page")
        other_pages = {
            page_id
            for assignment in self._state.assignments
            if assignment.assignment_id != selected.assignment_id
            for page_id in assignment.page_ids
        }
        if other_pages & set(selected.page_ids):
            reject_checklist(
                ChecklistErrorCode.PAGE_ALREADY_ASSIGNED,
                "checklist.confirm_page",
            )

    def _transition(
        self,
        *,
        state: _ChecklistState,
        applied_deltas: tuple[_ChecklistDelta, ...],
        redo_deltas: tuple[_ChecklistDelta, ...],
        owned_deltas: tuple[_ChecklistDelta, ...],
        known_state_keys: frozenset[bytes],
        reserved_ids: frozenset[UUID],
    ) -> Self:
        return self._create(
            case_id=self._case_id,
            origin_revision=self._origin_revision,
            definition=self._definition,
            known_page_ids=self._known_page_ids,
            active_page_ids=self._active_page_ids,
            origin_namespace=self._origin_namespace,
            origin_state=self._origin_state,
            non_assignment_confirmation_count=(self._non_assignment_confirmation_count),
            state=state,
            applied_deltas=applied_deltas,
            redo_deltas=redo_deltas,
            owned_deltas=owned_deltas,
            known_state_keys=known_state_keys,
            reserved_ids=reserved_ids,
        )


def _project_checklist(
    definition: ChecklistDefinition,
    active_page_ids: tuple[UUID, ...],
    assignments: tuple[ChecklistAssignment, ...],
) -> ChecklistProjection:
    active = set(active_page_ids)
    assignment_owners: dict[UUID, list[UUID]] = {}
    confirmed_pages: set[UUID] = set()
    suggested_pages: set[UUID] = set()
    for assignment in assignments:
        target = (
            confirmed_pages
            if assignment.review_state is AssignmentReviewState.USER_CONFIRMED
            else suggested_pages
        )
        target.update(page_id for page_id in assignment.page_ids if page_id in active)
        for page_id in assignment.page_ids:
            assignment_owners.setdefault(page_id, []).append(assignment.assignment_id)
    overlapping = {page_id for page_id, owners in assignment_owners.items() if len(owners) > 1}

    requirement_projections: list[ChecklistRequirementProjection] = []
    for requirement in definition.requirements:
        selected = tuple(
            assignment
            for assignment in assignments
            if assignment.requirement_id == requirement.requirement_id
        )
        confirmed = tuple(
            assignment
            for assignment in selected
            if assignment.review_state is AssignmentReviewState.USER_CONFIRMED
        )
        if requirement.count_unit is RequirementCountUnit.DOCUMENTS:
            confirmed_count = sum(
                1
                for assignment in confirmed
                if any(page_id in active for page_id in assignment.page_ids)
            )
        else:
            confirmed_count = len(
                {
                    page_id
                    for assignment in confirmed
                    for page_id in assignment.page_ids
                    if page_id in active
                }
            )
        has_inactive = any(
            page_id not in active for assignment in selected for page_id in assignment.page_ids
        )
        has_overlap = any(
            page_id in overlapping for assignment in selected for page_id in assignment.page_ids
        )
        has_automation_suggestion = any(
            assignment.origin is AssignmentOrigin.AUTOMATION
            and assignment.review_state is AssignmentReviewState.SUGGESTED
            for assignment in selected
        )
        has_user_pending = any(
            assignment.review_state is AssignmentReviewState.SUGGESTED for assignment in selected
        )
        if (
            has_inactive
            or has_overlap
            or has_automation_suggestion
            or confirmed_count > requirement.maximum_count
        ):
            status = ChecklistStatus.NEEDS_REVIEW
        elif not selected and confirmed_count == 0:
            status = (
                ChecklistStatus.READY if requirement.minimum_count == 0 else ChecklistStatus.MISSING
            )
        elif has_user_pending or confirmed_count < requirement.minimum_count:
            status = ChecklistStatus.ADDED
        else:
            status = ChecklistStatus.READY
        requirement_projections.append(
            ChecklistRequirementProjection(
                requirement,
                selected,
                confirmed_count,
                status,
            )
        )
    return ChecklistProjection(
        tuple(requirement_projections),
        active_page_ids,
        tuple(
            page_id
            for page_id in active_page_ids
            if page_id not in confirmed_pages and page_id not in suggested_pages
        ),
        tuple(page_id for page_id in active_page_ids if page_id in suggested_pages),
    )


def _apply_delta(
    state: _ChecklistState,
    delta: _ChecklistDelta,
    *,
    forward: bool,
) -> _ChecklistState:
    expected_template = delta.template_before if forward else delta.template_after
    target_template = delta.template_after if forward else delta.template_before
    if state.template_binding != expected_template:
        reject_checklist(ChecklistErrorCode.CONCURRENT_CHANGE, "checklist.delta_template")
    assignment_edits = delta.assignment_edits
    confirmation_edits = delta.confirmation_edits
    assignments = _apply_record_edits(
        state.assignments,
        assignment_edits,
        forward=forward,
        context="checklist.delta_assignments",
    )
    confirmations = _apply_record_edits(
        state.confirmations,
        confirmation_edits,
        forward=forward,
        context="checklist.delta_confirmations",
    )
    return _ChecklistState(target_template, assignments, confirmations)


def _apply_record_edits[T, E: (_AssignmentEdit, _ConfirmationEdit)](
    records: tuple[T, ...],
    edits: tuple[E, ...],
    *,
    forward: bool,
    context: str,
) -> tuple[T, ...]:
    if not edits:
        return records
    selected_edits = edits if forward else tuple(reversed(edits))
    result = list(records)
    for edit in selected_edits:
        expected = edit.before if forward else edit.after
        replacement = edit.after if forward else edit.before
        if expected is None:
            if replacement is None or not 0 <= edit.index <= len(result):
                reject_checklist(ChecklistErrorCode.CONCURRENT_CHANGE, context)
            result.insert(edit.index, cast(T, replacement))
        elif replacement is None:
            if not 0 <= edit.index < len(result) or result[edit.index] != expected:
                reject_checklist(ChecklistErrorCode.CONCURRENT_CHANGE, context)
            result.pop(edit.index)
        else:
            if not 0 <= edit.index < len(result) or result[edit.index] != expected:
                reject_checklist(ChecklistErrorCode.CONCURRENT_CHANGE, context)
            result[edit.index] = cast(T, replacement)
    return tuple(result)


def _validate_runtime_state(
    state: _ChecklistState,
    definition: ChecklistDefinition,
    known_page_ids: tuple[UUID, ...],
    non_assignment_confirmation_count: int,
) -> None:
    if (
        type(non_assignment_confirmation_count) is not int
        or non_assignment_confirmation_count < 0
        or len(state.assignments) > MAX_CHECKLIST_ASSIGNMENTS
        or non_assignment_confirmation_count + len(state.confirmations)
        > MAX_CHECKLIST_CONFIRMATIONS
    ):
        reject_checklist(ChecklistErrorCode.CAPACITY_EXCEEDED, "checklist.state_capacity")
    if state.assignments and state.template_binding is None:
        reject_checklist(ChecklistErrorCode.TEMPLATE_REQUIRED, "checklist.state_template")
    if state.template_binding is not None and state.template_binding != definition.binding:
        reject_checklist(ChecklistErrorCode.TEMPLATE_MISMATCH, "checklist.state_template")
    known = set(known_page_ids)
    requirements = {item.requirement_id for item in definition.requirements}
    assignment_ids: set[UUID] = set()
    confirmation_ids: set[UUID] = set()
    confirmation_by_id: dict[UUID, AssignmentScopedConfirmation] = {}
    for confirmation in state.confirmations:
        if confirmation.confirmation_id in confirmation_ids:
            reject_checklist(ChecklistErrorCode.MANIFEST_INVALID, "checklist.confirmation_ids")
        confirmation_ids.add(confirmation.confirmation_id)
        confirmation_by_id[confirmation.confirmation_id] = confirmation
    for assignment in state.assignments:
        if assignment.assignment_id in assignment_ids:
            reject_checklist(ChecklistErrorCode.MANIFEST_INVALID, "checklist.assignment_ids")
        assignment_ids.add(assignment.assignment_id)
        if assignment.requirement_id not in requirements:
            reject_checklist(
                ChecklistErrorCode.MANIFEST_INVALID, "checklist.assignment_requirement"
            )
        if any(page_id not in known for page_id in assignment.page_ids):
            reject_checklist(ChecklistErrorCode.MANIFEST_INVALID, "checklist.assignment_page")
        if assignment.review_state is AssignmentReviewState.USER_CONFIRMED:
            coupled = confirmation_by_id.get(cast(UUID, assignment.confirmation_id))
            if (
                coupled is None
                or coupled.assignment_id != assignment.assignment_id
                or coupled.confirmation_code != ASSIGNMENT_CONFIRMATION_CODE
                or coupled.state != "confirmed"
                or coupled.recorded_at < assignment.assigned_at
            ):
                reject_checklist(
                    ChecklistErrorCode.MANIFEST_INVALID,
                    "checklist.assignment_confirmation",
                )
    if assignment_ids & confirmation_ids:
        reject_checklist(ChecklistErrorCode.IDENTIFIER_COLLISION, "checklist.state_namespace")
    if any(
        confirmation.assignment_id not in assignment_ids for confirmation in state.confirmations
    ):
        reject_checklist(ChecklistErrorCode.MANIFEST_INVALID, "checklist.confirmation_scope")


def _parse_snapshot(
    snapshot: ManifestSnapshot,
    definition: ChecklistDefinition,
) -> _ParsedSnapshot:
    try:
        document = snapshot.document
    except Exception:
        reject_checklist(ChecklistErrorCode.MANIFEST_INVALID, "checklist.snapshot_document")
    if (
        document.get("case_id") != str(snapshot.case_id)
        or document.get("revision") != snapshot.revision
    ):
        reject_checklist(ChecklistErrorCode.MANIFEST_INVALID, "checklist.snapshot_identity")
    pages = _require_list(document.get("pages"), "checklist.pages")
    page_sequence = _require_list(document.get("page_sequence"), "checklist.page_sequence")
    assignments_raw = _require_list(document.get("assignments"), "checklist.assignments")
    confirmations_raw = _require_list(document.get("confirmations"), "checklist.confirmations")
    if (
        len(pages) > MAX_CHECKLIST_PAGES
        or len(page_sequence) > MAX_CHECKLIST_PAGES
        or len(assignments_raw) > MAX_CHECKLIST_ASSIGNMENTS
        or len(confirmations_raw) > MAX_CHECKLIST_CONFIRMATIONS
    ):
        reject_checklist(ChecklistErrorCode.CAPACITY_EXCEEDED, "checklist.snapshot_capacity")

    known_page_ids = tuple(_parse_record_uuid(page, "page_id", "checklist.page") for page in pages)
    if len(set(known_page_ids)) != len(known_page_ids):
        reject_checklist(ChecklistErrorCode.MANIFEST_INVALID, "checklist.page_ids")
    active_page_ids = tuple(
        _parse_uuid_text(value, "checklist.page_sequence") for value in page_sequence
    )
    if len(set(active_page_ids)) != len(active_page_ids) or not set(active_page_ids) <= set(
        known_page_ids
    ):
        reject_checklist(ChecklistErrorCode.MANIFEST_INVALID, "checklist.page_sequence")

    template_binding = _parse_template_binding(document.get("template_ref"), definition)
    assignments = tuple(_parse_assignment(item) for item in assignments_raw)
    assignment_ids = {item.assignment_id for item in assignments}
    confirmations = tuple(
        _parse_assignment_confirmation(item)
        for item in confirmations_raw
        if _is_assignment_scoped(item)
    )
    non_assignment_confirmation_count = len(confirmations_raw) - len(confirmations)
    state = _ChecklistState(template_binding, assignments, confirmations)
    if assignments and template_binding is None:
        reject_checklist(ChecklistErrorCode.TEMPLATE_REQUIRED, "checklist.snapshot_template")
    _validate_runtime_state(
        state,
        definition,
        known_page_ids,
        non_assignment_confirmation_count,
    )

    complete_namespace = _manifest_namespace(document)
    checklist_namespace = assignment_ids | {
        confirmation.confirmation_id for confirmation in confirmations
    }
    return _ParsedSnapshot(
        document,
        snapshot.case_id,
        snapshot.revision,
        state,
        known_page_ids,
        active_page_ids,
        complete_namespace,
        complete_namespace - checklist_namespace,
        non_assignment_confirmation_count,
    )


def _parse_template_binding(
    value: object,
    definition: ChecklistDefinition,
) -> TemplateBinding | None:
    if value is None:
        return None
    if type(value) is not dict or frozenset(value) != _TEMPLATE_REF_MEMBERS:
        reject_checklist(ChecklistErrorCode.MANIFEST_INVALID, "checklist.template_ref")
    mapping = cast(dict[str, object], value)
    try:
        binding = TemplateBinding(
            cast(str, mapping["template_id"]),
            cast(str, mapping["template_version"]),
            cast(str, mapping["sha256"]),
        )
    except ChecklistError:
        reject_checklist(ChecklistErrorCode.MANIFEST_INVALID, "checklist.template_ref")
    if binding != definition.binding:
        reject_checklist(ChecklistErrorCode.TEMPLATE_MISMATCH, "checklist.template_ref")
    return binding


def _parse_assignment(value: object) -> ChecklistAssignment:
    if type(value) is not dict or frozenset(value) != _ASSIGNMENT_MEMBERS:
        reject_checklist(ChecklistErrorCode.MANIFEST_INVALID, "checklist.assignment")
    record = cast(dict[str, object], value)
    raw_pages = _require_list(record["page_ids"], "checklist.assignment_pages")
    try:
        confirmation_id = (
            None
            if record["confirmation_id"] is None
            else _parse_uuid_text(record["confirmation_id"], "checklist.assignment_confirmation")
        )
        return ChecklistAssignment(
            _parse_uuid_text(record["assignment_id"], "checklist.assignment_id"),
            cast(str, record["requirement_id"]),
            tuple(_parse_uuid_text(page_id, "checklist.assignment_page") for page_id in raw_pages),
            AssignmentOrigin(cast(str, record["origin"])),
            AssignmentReviewState(cast(str, record["status"])),
            confirmation_id,
            _parse_utc_timestamp(record["assigned_at"], "checklist.assignment_time"),
        )
    except ChecklistError, ValueError, TypeError:
        reject_checklist(ChecklistErrorCode.MANIFEST_INVALID, "checklist.assignment")


def _parse_assignment_confirmation(value: object) -> AssignmentScopedConfirmation:
    if type(value) is not dict or frozenset(value) != _CONFIRMATION_MEMBERS:
        reject_checklist(ChecklistErrorCode.MANIFEST_INVALID, "checklist.confirmation")
    record = cast(dict[str, object], value)
    scope = record.get("scope")
    if type(scope) is not dict or frozenset(scope) != _ASSIGNMENT_SCOPE_MEMBERS:
        reject_checklist(ChecklistErrorCode.MANIFEST_INVALID, "checklist.confirmation_scope")
    if (
        record.get("confirmation_version") != ASSIGNMENT_CONFIRMATION_VERSION
        or record.get("actor") != "user"
        or scope.get("kind") != "assignment"
    ):
        reject_checklist(ChecklistErrorCode.MANIFEST_INVALID, "checklist.confirmation")
    try:
        return AssignmentScopedConfirmation(
            _parse_uuid_text(record["confirmation_id"], "checklist.confirmation_id"),
            _parse_uuid_text(scope["assignment_id"], "checklist.confirmation_assignment"),
            cast(str, record["confirmation_code"]),
            cast(int, record["statement_version"]),
            cast(str, record["state"]),
            _parse_utc_timestamp(record["recorded_at"], "checklist.confirmation_time"),
        )
    except ChecklistError, TypeError, ValueError:
        reject_checklist(ChecklistErrorCode.MANIFEST_INVALID, "checklist.confirmation")


def _is_assignment_scoped(value: object) -> bool:
    if type(value) is not dict:
        return False
    scope = value.get("scope")
    return type(scope) is dict and scope.get("kind") == "assignment"


def _merge_assignment_confirmations(
    current: list[object],
    target: tuple[AssignmentScopedConfirmation, ...],
) -> list[object]:
    rendered = [confirmation.to_manifest() for confirmation in target]
    result: list[object] = []
    target_index = 0
    for record in current:
        if _is_assignment_scoped(record):
            if target_index < len(rendered):
                result.append(rendered[target_index])
                target_index += 1
            continue
        result.append(
            copy_manifest_document({"value": record}, context="checklist.confirmation_copy")[
                "value"
            ]
        )
    result.extend(rendered[target_index:])
    return result


def _manifest_namespace(document: Mapping[str, object]) -> frozenset[UUID]:
    identifiers: list[UUID] = [
        _parse_uuid_text(document.get("case_id"), "checklist.namespace_case")
    ]
    for collection_name, id_field in (
        ("sources", "source_id"),
        ("pages", "page_id"),
        ("page_operations", "operation_id"),
        ("assets", "asset_id"),
        ("overlays", "overlay_id"),
        ("assignments", "assignment_id"),
        ("confirmations", "confirmation_id"),
    ):
        collection = _require_list(document.get(collection_name), "checklist.namespace")
        for record in collection:
            identifiers.append(_parse_record_uuid(record, id_field, "checklist.namespace"))
            if collection_name == "page_operations" and type(record) is dict:
                payload = record.get("payload")
                if type(payload) is dict and payload.get("kind") == "set_group":
                    identifiers.append(
                        _parse_uuid_text(payload.get("group_id"), "checklist.namespace_group")
                    )
    export_history = document.get("export_history")
    if type(export_history) is not dict:
        reject_checklist(ChecklistErrorCode.MANIFEST_INVALID, "checklist.namespace_exports")
    for record in _require_list(export_history.get("entries"), "checklist.namespace_exports"):
        identifiers.append(_parse_record_uuid(record, "export_id", "checklist.namespace"))
    if len(set(identifiers)) != len(identifiers):
        reject_checklist(ChecklistErrorCode.IDENTIFIER_COLLISION, "checklist.namespace")
    return frozenset(identifiers)


def _state_key(state: _ChecklistState) -> bytes:
    material = {
        "template_ref": (
            None if state.template_binding is None else state.template_binding.to_manifest()
        ),
        "assignments": [assignment.to_manifest() for assignment in state.assignments],
        "confirmations": [confirmation.to_manifest() for confirmation in state.confirmations],
    }
    return hashlib.sha256(
        json.dumps(
            material,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).digest()


def _generated_ids(deltas: Sequence[_ChecklistDelta]) -> frozenset[UUID]:
    generated: set[UUID] = set()
    for delta in deltas:
        for assignment_edit in delta.assignment_edits:
            if assignment_edit.before is None and assignment_edit.after is not None:
                generated.add(assignment_edit.after.assignment_id)
        for confirmation_edit in delta.confirmation_edits:
            if confirmation_edit.before is None and confirmation_edit.after is not None:
                generated.add(confirmation_edit.after.confirmation_id)
    return frozenset(generated)


def _generate_id(factory: Callable[[], UUID], reserved: set[UUID] | frozenset[UUID]) -> UUID:
    for _ in range(MAX_ID_GENERATION_ATTEMPTS):
        try:
            candidate = factory()
        except BaseException:
            reject_checklist(ChecklistErrorCode.ID_GENERATION_FAILED, "checklist.identifier")
        if type(candidate) is UUID and candidate.version == 4 and candidate not in reserved:
            return candidate
    reject_checklist(ChecklistErrorCode.ID_GENERATION_FAILED, "checklist.identifier")


def _require_token(value: object, context: str) -> str:
    if type(value) is not str or not 1 <= len(value) <= 128 or _TOKEN.fullmatch(value) is None:
        reject_checklist(ChecklistErrorCode.MODEL_INVALID, context)
    return value


def _require_uuid(value: object, context: str) -> UUID:
    if type(value) is not UUID or value.version != 4:
        reject_checklist(ChecklistErrorCode.MODEL_INVALID, context)
    return value


def _require_uuid_tuple(
    value: object,
    *,
    allow_empty: bool,
    context: str,
) -> tuple[UUID, ...]:
    if (
        type(value) is not tuple
        or (not allow_empty and not value)
        or len(value) > MAX_CHECKLIST_PAGES
        or any(type(item) is not UUID or item.version != 4 for item in value)
        or len(set(value)) != len(value)
    ):
        reject_checklist(
            ChecklistErrorCode.SELECTION_EMPTY
            if type(value) is tuple and not value
            else ChecklistErrorCode.MODEL_INVALID,
            context,
        )
    return value


def _require_page_tuple(value: object, *, context: str) -> tuple[UUID, ...]:
    return _require_uuid_tuple(value, allow_empty=False, context=context)


def _require_utc_datetime(value: object, context: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        reject_checklist(ChecklistErrorCode.TIMESTAMP_INVALID, context)
    return value


def _render_utc_timestamp(value: datetime) -> str:
    selected = _require_utc_datetime(value, "checklist.timestamp")
    timespec = "seconds" if selected.microsecond == 0 else "microseconds"
    return selected.astimezone(UTC).isoformat(timespec=timespec).replace("+00:00", "Z")


def _parse_utc_timestamp(value: object, context: str) -> datetime:
    if type(value) is not str or _UTC_TIMESTAMP.fullmatch(value) is None:
        reject_checklist(ChecklistErrorCode.MANIFEST_INVALID, context)
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        reject_checklist(ChecklistErrorCode.MANIFEST_INVALID, context)
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        reject_checklist(ChecklistErrorCode.MANIFEST_INVALID, context)
    return parsed.astimezone(UTC)


def _parse_uuid_text(value: object, context: str) -> UUID:
    if type(value) is not str or _UUID_V4.fullmatch(value) is None:
        reject_checklist(ChecklistErrorCode.MANIFEST_INVALID, context)
    return UUID(value)


def _parse_record_uuid(value: object, field_name: str, context: str) -> UUID:
    if type(value) is not dict:
        reject_checklist(ChecklistErrorCode.MANIFEST_INVALID, context)
    return _parse_uuid_text(value.get(field_name), context)


def _require_list(value: object, context: str) -> list[object]:
    if type(value) is not list:
        reject_checklist(ChecklistErrorCode.MANIFEST_INVALID, context)
    return value


__all__ = (
    "ASSIGNMENT_CONFIRMATION_CODE",
    "AddDocumentAssignmentCommand",
    "AssignmentOrigin",
    "AssignmentReviewState",
    "ChecklistAssignment",
    "ChecklistCommand",
    "ChecklistCommandHistory",
    "ChecklistDefinition",
    "ChecklistError",
    "ChecklistErrorCode",
    "ChecklistProjection",
    "ChecklistRequirement",
    "ChecklistRequirementProjection",
    "ChecklistStatus",
    "ConfirmAssignmentCommand",
    "MoveAssignmentCommand",
    "RemoveAssignmentCommand",
    "ReorderAssignmentPagesCommand",
    "ReorderRequirementAssignmentsCommand",
    "ReplaceAssignmentPagesCommand",
    "RequirementCountUnit",
    "SuggestDocumentAssignmentCommand",
    "TemplateBinding",
    "reject_checklist",
)

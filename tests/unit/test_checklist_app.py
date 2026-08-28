from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, date, datetime
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.app.checklist import (
    AcceptSuggestion,
    AddPages,
    ChecklistAnnouncement,
    ChecklistEditorState,
    ChecklistOrderDirection,
    ChecklistSuggestion,
    ChecklistSuggestionContext,
    ChecklistWorkflowService,
    ConfirmAssignment,
    ReorderAssignment,
    ReorderAssignmentPage,
    checklist_definition_from_template,
    is_checklist_announcement_safe,
)
from paperworkshell.domain.checklist import (
    AssignmentOrigin,
    AssignmentReviewState,
    ChecklistError,
    ChecklistErrorCode,
    ChecklistStatus,
    RequirementCountUnit,
)
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.templates.catalog import (
    GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
    built_in_template_repository,
)
from paperworkshell.templates.loader import SafeTemplateLoader

CASE_ID = UUID("10000000-0000-4000-8000-000000000001")
SOURCE_ID = UUID("20000000-0000-4000-8000-000000000001")
PAGE_IDS = tuple(UUID(f"30000000-0000-4000-8000-{value:012d}") for value in range(1, 5))
EDITOR_IDS = tuple(UUID(f"40000000-0000-4000-8000-{value:012d}") for value in range(1, 5))
ASSIGNMENT_IDS = tuple(UUID(f"50000000-0000-4000-8000-{value:012d}") for value in range(1, 9))
CONFIRMATION_IDS = tuple(UUID(f"60000000-0000-4000-8000-{value:012d}") for value in range(1, 9))
TEMPLATE_DIGEST = "f06ca149a4ff049de49a8a671352e43527900287dd898c7a49e24871d1b6d9bd"
BASE_TIME = datetime(2026, 8, 28, 10, 10, tzinfo=UTC)


def _ids(*values: UUID) -> Callable[[], UUID]:
    iterator = iter(values)

    def next_id() -> UUID:
        return next(iterator)

    return next_id


def _clock(value: datetime = BASE_TIME) -> Callable[[], datetime]:
    return lambda: value


def _assignment(
    assignment_id: UUID,
    requirement_id: str,
    page_ids: tuple[UUID, ...],
    *,
    origin: str = "user",
) -> dict[str, object]:
    return {
        "assignment_id": str(assignment_id),
        "requirement_id": requirement_id,
        "page_ids": [str(page_id) for page_id in page_ids],
        "origin": origin,
        "status": "suggested",
        "confirmation_id": None,
        "assigned_at": "2026-08-28T10:05:00Z",
    }


def _document(
    *,
    template_ref: dict[str, object] | None = None,
    assignments: list[dict[str, object]] | None = None,
    revision: int = 0,
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "case_id": str(CASE_ID),
        "revision": revision,
        "created_at": "2026-08-28T10:00:00Z",
        "updated_at": "2026-08-28T10:09:00Z",
        "template_ref": template_ref,
        "sources": [
            {
                "source_id": str(SOURCE_ID),
                "display_name": "PRIVATE-SOURCE-NAME.pdf",
                "storage_name": "PRIVATE-STORAGE-NAME.bin",
            }
        ],
        "pages": [
            {
                "page_id": str(page_id),
                "source_id": str(SOURCE_ID),
                "source_page_index": index,
            }
            for index, page_id in enumerate(PAGE_IDS)
        ],
        "page_sequence_base": [str(page_id) for page_id in PAGE_IDS],
        "page_sequence": [str(page_id) for page_id in PAGE_IDS],
        "page_operations": [],
        "assets": [],
        "overlays": [],
        "assignments": [] if assignments is None else assignments,
        "constraints": {},
        "confirmations": [],
        "export_history": {"storage_name": "export-history.json", "entries": []},
    }


def _snapshot(
    document: dict[str, object] | None = None,
    *,
    sha256: str = "a" * 64,
    cas_token: str = "b" * 64,
) -> ManifestSnapshot:
    selected = _document() if document is None else document
    return ManifestSnapshot(
        CASE_ID,
        2,
        cast(int, selected["revision"]),
        selected,
        sha256,
        cas_token,
    )


def _service(
    *ids: UUID,
    provider: _SuggestionProvider | None = None,
) -> ChecklistWorkflowService:
    return ChecklistWorkflowService(
        SafeTemplateLoader(built_in_template_repository()),
        GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY,
        as_of=date(2026, 8, 28),
        suggestion_provider=provider,
        clock=_clock(),
        id_factory=_ids(*ids),
    )


class _SuggestionProvider:
    def __init__(self, suggestions: tuple[ChecklistSuggestion, ...]) -> None:
        self.suggestions = suggestions
        self.context: ChecklistSuggestionContext | None = None

    def suggest(self, context: ChecklistSuggestionContext) -> tuple[ChecklistSuggestion, ...]:
        self.context = context
        return self.suggestions


def test_exact_template_conversion_and_empty_editor_projection_are_path_free() -> None:
    loader = SafeTemplateLoader(built_in_template_repository())
    template = loader.load(GENERIC_PORTAL_APPLICATION_TEMPLATE_KEY, as_of=date(2026, 8, 28))
    definition = checklist_definition_from_template(template)

    assert definition.binding.template_id == "generic-portal-application"
    assert definition.binding.template_version == "1.1.0"
    assert definition.binding.sha256 == TEMPLATE_DIGEST
    assert tuple(value.requirement_id for value in definition.requirements) == (
        "application-form",
        "identity-evidence",
        "address-evidence",
        "supporting-evidence",
    )
    assert definition.requirements[0].count_unit is RequirementCountUnit.DOCUMENTS
    assert (definition.requirements[0].minimum_count, definition.requirements[0].maximum_count) == (
        1,
        1,
    )

    service = _service(EDITOR_IDS[0])
    state = service.begin(_snapshot())

    assert isinstance(state, ChecklistEditorState)
    assert state.editor_id == state.editor_session_id == EDITOR_IDS[0]
    assert tuple(value.display_name for value in state.requirements) == (
        "Application form",
        "Identity evidence",
        "Proof of address",
        "Supporting evidence",
    )
    assert all(value.status is ChecklistStatus.MISSING for value in state.requirements)
    assert all(value.assignment_id is None for value in state.pages)
    assert tuple(value.page_id for value in state.pages) == PAGE_IDS
    assert state.changed is False
    assert state.announcement is ChecklistAnnouncement.READY
    assert is_checklist_announcement_safe(state.announcement)
    assert not hasattr(state, "snapshot")
    assert not hasattr(state, "document")
    rendered = repr(state)
    assert "PRIVATE-SOURCE-NAME" not in rendered
    assert "PRIVATE-STORAGE-NAME" not in rendered
    assert TEMPLATE_DIGEST not in rendered

    service.cancel(state, state)


def test_manual_add_stays_suggested_until_explicit_confirmation_and_round_trips() -> None:
    snapshot = _snapshot()
    service = _service(EDITOR_IDS[0])
    opening = service.begin(snapshot)
    added = opening.execute(
        AddPages(opening.editor_id, "application-form", PAGE_IDS[:2]),
        clock=_clock(),
        id_factory=_ids(ASSIGNMENT_IDS[0]),
    )

    assert added.changed is True
    assert added.can_undo is True
    assert added.assignments[0].origin is AssignmentOrigin.USER
    assert added.assignments[0].review_state is AssignmentReviewState.SUGGESTED
    assert added.requirements[0].status is ChecklistStatus.ADDED
    assert added.requirements[0].confirmed_count == 0
    pending = service.to_candidate(opening, added, snapshot)
    assert pending["template_ref"] == {
        "template_id": "generic-portal-application",
        "template_version": "1.1.0",
        "sha256": TEMPLATE_DIGEST,
    }
    pending_assignments = cast(list[dict[str, object]], pending["assignments"])
    assert pending_assignments[0]["status"] == "suggested"
    assert pending["confirmations"] == []

    confirmed = added.execute(
        # Manual Add and explicit Confirm remain two separate user actions.
        ConfirmAssignment(added.editor_id, ASSIGNMENT_IDS[0]),
        clock=_clock(),
        id_factory=_ids(CONFIRMATION_IDS[0]),
    )
    assert confirmed.assignments[0].review_state is AssignmentReviewState.USER_CONFIRMED
    assert confirmed.requirements[0].status is ChecklistStatus.READY
    assert confirmed.requirements[0].confirmed_count == 1
    undone = confirmed.undo()
    assert undone.assignments[0].review_state is AssignmentReviewState.SUGGESTED
    redone = undone.redo()
    assert redone.assignments[0].review_state is AssignmentReviewState.USER_CONFIRMED

    candidate = service.to_candidate(opening, redone, snapshot)
    records = cast(list[dict[str, object]], candidate["assignments"])
    confirmations = cast(list[dict[str, object]], candidate["confirmations"])
    assert records[0]["confirmation_id"] == str(CONFIRMATION_IDS[0])
    assert confirmations[0]["confirmation_id"] == str(CONFIRMATION_IDS[0])
    assert cast(dict[str, object], confirmations[0]["scope"])["assignment_id"] == str(
        ASSIGNMENT_IDS[0]
    )
    service.complete(opening, redone)


def test_provider_is_bounded_path_free_and_suggestions_need_explicit_acceptance() -> None:
    provider = _SuggestionProvider((ChecklistSuggestion("identity-evidence", (PAGE_IDS[0],)),))
    snapshot = _snapshot()
    service = _service(EDITOR_IDS[0], ASSIGNMENT_IDS[0], provider=provider)
    opening = service.begin(snapshot)

    assert provider.context is not None
    assert provider.context.active_page_ids == PAGE_IDS
    assert provider.context.unassigned_page_ids == PAGE_IDS
    assert not hasattr(provider.context, "source_names")
    assert not hasattr(provider.context, "paths")
    assert opening.assignments[0].origin is AssignmentOrigin.AUTOMATION
    assert opening.assignments[0].review_state is AssignmentReviewState.SUGGESTED
    identity = opening.requirements[1]
    assert identity.status is ChecklistStatus.NEEDS_REVIEW
    assert identity.confirmed_count == 0

    accepted = opening.execute(
        AcceptSuggestion(opening.editor_id, ASSIGNMENT_IDS[0]),
        clock=_clock(),
        id_factory=_ids(CONFIRMATION_IDS[0]),
    )
    assert accepted.assignments[0].review_state is AssignmentReviewState.USER_CONFIRMED
    assert accepted.requirements[1].status is ChecklistStatus.READY
    candidate = service.to_candidate(opening, accepted, snapshot)
    assert cast(list[dict[str, object]], candidate["assignments"])[0]["origin"] == "automation"
    service.complete(opening, accepted)


def test_provider_opening_supports_candidate_retry_cancel_and_complete_lifecycle() -> None:
    provider = _SuggestionProvider((ChecklistSuggestion("supporting-evidence", (PAGE_IDS[0],)),))
    snapshot = _snapshot()
    service = _service(EDITOR_IDS[0], ASSIGNMENT_IDS[0], provider=provider)
    opening = service.begin(snapshot)

    first = service.to_candidate(opening, opening, snapshot)
    # A caller-side persistence failure leaves the private opening authority intact.
    retried = service.to_candidate(opening, opening, snapshot)
    assert retried == first
    service.cancel(opening, opening)
    with pytest.raises(ChecklistError) as cancelled:
        service.to_candidate(opening, opening, snapshot)
    assert cancelled.value.code is ChecklistErrorCode.CONCURRENT_CHANGE

    replacement_provider = _SuggestionProvider(
        (ChecklistSuggestion("supporting-evidence", (PAGE_IDS[1],)),)
    )
    replacement = _service(
        EDITOR_IDS[1],
        ASSIGNMENT_IDS[1],
        provider=replacement_provider,
    )
    replacement_opening = replacement.begin(snapshot)
    replacement.to_candidate(replacement_opening, replacement_opening, snapshot)
    replacement.complete(replacement_opening, replacement_opening)
    with pytest.raises(ChecklistError) as completed:
        replacement.to_candidate(replacement_opening, replacement_opening, snapshot)
    assert completed.value.code is ChecklistErrorCode.CONCURRENT_CHANGE


def test_keyboard_adjacent_orders_move_remove_and_undo_redo_stay_local() -> None:
    service = _service(EDITOR_IDS[0])
    opening = service.begin(_snapshot())
    first = opening.add_pages(
        "supporting-evidence",
        (PAGE_IDS[0], PAGE_IDS[1]),
        clock=_clock(),
        id_factory=_ids(ASSIGNMENT_IDS[0]),
    )
    second = first.add_pages(
        "supporting-evidence",
        (PAGE_IDS[2],),
        clock=_clock(),
        id_factory=_ids(ASSIGNMENT_IDS[1]),
    )
    reordered = second.execute(
        ReorderAssignment(
            second.editor_id,
            ASSIGNMENT_IDS[1],
            ChecklistOrderDirection.EARLIER,
        )
    )
    assert reordered.requirements[3].assignment_ids == (
        ASSIGNMENT_IDS[1],
        ASSIGNMENT_IDS[0],
    )
    pages_reordered = reordered.execute(
        ReorderAssignmentPage(
            reordered.editor_id,
            ASSIGNMENT_IDS[0],
            PAGE_IDS[1],
            ChecklistOrderDirection.EARLIER,
        )
    )
    first_assignment = next(
        value for value in pages_reordered.assignments if value.assignment_id == ASSIGNMENT_IDS[0]
    )
    assert first_assignment.page_ids == (PAGE_IDS[1], PAGE_IDS[0])
    moved = pages_reordered.move_assignment(
        ASSIGNMENT_IDS[1],
        "address-evidence",
        clock=_clock(),
    )
    moved_assignment = next(
        value for value in moved.assignments if value.assignment_id == ASSIGNMENT_IDS[1]
    )
    assert moved_assignment.requirement_id == "address-evidence"
    removed = moved.remove_assignment(ASSIGNMENT_IDS[0])
    assert tuple(value.assignment_id for value in removed.assignments) == (ASSIGNMENT_IDS[1],)
    assert removed.undo().redo().assignments == removed.assignments
    service.cancel(opening, removed)


def test_exact_template_binding_and_opening_manifest_authority_are_enforced() -> None:
    mismatched = _document(
        template_ref={
            "template_id": "generic-portal-application",
            "template_version": "1.1.0",
            "sha256": "d" * 64,
        }
    )
    with pytest.raises(ChecklistError) as mismatch:
        _service(EDITOR_IDS[0]).begin(_snapshot(mismatched))
    assert mismatch.value.code is ChecklistErrorCode.TEMPLATE_MISMATCH

    populated_unbound = _document(
        assignments=[_assignment(ASSIGNMENT_IDS[0], "application-form", (PAGE_IDS[0],))]
    )
    with pytest.raises(ChecklistError) as unbound:
        _service(EDITOR_IDS[0]).begin(_snapshot(populated_unbound))
    assert unbound.value.code is ChecklistErrorCode.TEMPLATE_REQUIRED

    snapshot = _snapshot()
    service = _service(EDITOR_IDS[0])
    opening = service.begin(snapshot)
    added = opening.add_pages(
        "application-form",
        (PAGE_IDS[0],),
        clock=_clock(),
        id_factory=_ids(ASSIGNMENT_IDS[0]),
    )
    with pytest.raises(ChecklistError) as changed_authority:
        service.to_candidate(
            opening,
            added,
            _snapshot(deepcopy(snapshot.document), sha256="e" * 64),
        )
    assert changed_authority.value.code is ChecklistErrorCode.CONCURRENT_CHANGE
    service.abort(opening.editor_session_id)


def test_clock_identifier_provider_failures_and_representations_are_content_free() -> None:
    with pytest.raises(ChecklistError) as invalid_editor_id:
        _service(UUID(int=0)).begin(_snapshot())
    assert invalid_editor_id.value.code is ChecklistErrorCode.ID_GENERATION_FAILED

    service = _service(EDITOR_IDS[0])
    opening = service.begin(_snapshot())
    with pytest.raises(ChecklistError) as invalid_clock:
        opening.add_pages(
            "application-form",
            (PAGE_IDS[0],),
            clock=lambda: cast(datetime, "PRIVATE-CLOCK-VALUE"),
            id_factory=_ids(ASSIGNMENT_IDS[0]),
        )
    assert invalid_clock.value.code is ChecklistErrorCode.TIMESTAMP_INVALID
    assert "PRIVATE-CLOCK-VALUE" not in repr(invalid_clock.value)
    assert str(CASE_ID) not in repr(opening)
    assert str(PAGE_IDS[0]) not in repr(opening.pages[0])
    service.cancel(opening, opening)

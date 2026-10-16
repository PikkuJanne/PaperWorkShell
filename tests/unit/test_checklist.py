from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.domain.checklist import (
    ASSIGNMENT_CONFIRMATION_CODE,
    AddDocumentAssignmentCommand,
    AssignmentOrigin,
    AssignmentReviewState,
    ChecklistCommandHistory,
    ChecklistDefinition,
    ChecklistError,
    ChecklistErrorCode,
    ChecklistRequirement,
    ChecklistStatus,
    ConfirmAssignmentCommand,
    MoveAssignmentCommand,
    RemoveAssignmentCommand,
    ReorderAssignmentPagesCommand,
    ReorderRequirementAssignmentsCommand,
    ReplaceAssignmentPagesCommand,
    RequirementCountUnit,
    SuggestDocumentAssignmentCommand,
    TemplateBinding,
)
from paperworkshell.domain.manifest_repository import ManifestSnapshot

CASE_ID = UUID("10000000-0000-4000-8000-000000000001")
PAGE_IDS = tuple(UUID(f"30000000-0000-4000-8000-{number:012d}") for number in range(1, 7))
SOURCE_ID = UUID("20000000-0000-4000-8000-000000000001")
ASSIGNMENT_IDS = tuple(UUID(f"70000000-0000-4000-8000-{number:012d}") for number in range(1, 9))
CONFIRMATION_IDS = tuple(UUID(f"a0000000-0000-4000-8000-{number:012d}") for number in range(1, 9))
TIMES = tuple(datetime(2026, 8, 28, 10, minute, tzinfo=UTC) for minute in range(10))
BINDING = TemplateBinding(
    "generic-portal-application",
    "1.1.0",
    "f06ca149a4ff049de49a8a671352e43527900287dd898c7a49e24871d1b6d9bd",
)


def test_v3_removal_marks_reserve_identifiers_for_checklist_edits() -> None:
    document = _document()
    document["schema_version"] = 3
    mark_id = ASSIGNMENT_IDS[0]
    document["redaction_marks"] = [
        {
            "mark_id": str(mark_id),
            "mark_version": 1,
            "page_id": str(PAGE_IDS[0]),
            "box": {
                "coordinate_space": "normalized_page_millionths_v1",
                "x": 0,
                "y": 0,
                "width": 100000,
                "height": 100000,
            },
            "state": "unapplied",
        }
    ]
    snapshot = ManifestSnapshot(CASE_ID, 3, 0, document, "a" * 64, "b" * 24)
    history = ChecklistCommandHistory.from_snapshot(snapshot, _definition())
    generated = iter((mark_id, ASSIGNMENT_IDS[1]))
    edited = history.execute(
        AddDocumentAssignmentCommand("application-form", (PAGE_IDS[0],), TIMES[1]),
        id_factory=lambda: next(generated),
    )
    assert edited.assignments[0].assignment_id == ASSIGNMENT_IDS[1]


def _definition(*, application_minimum: int = 1) -> ChecklistDefinition:
    return ChecklistDefinition(
        BINDING,
        (
            ChecklistRequirement(
                "application-form",
                RequirementCountUnit.DOCUMENTS,
                application_minimum,
                2,
            ),
            ChecklistRequirement(
                "identity-evidence",
                RequirementCountUnit.DOCUMENTS,
                1,
                2,
            ),
            ChecklistRequirement(
                "address-evidence",
                RequirementCountUnit.PAGES,
                1,
                2,
            ),
            ChecklistRequirement(
                "supporting-evidence",
                RequirementCountUnit.DOCUMENTS,
                0,
                2,
            ),
        ),
    )


def _assignment(
    assignment_id: UUID,
    requirement_id: str,
    page_ids: tuple[UUID, ...],
    *,
    origin: str = "user",
    status: str = "suggested",
    confirmation_id: UUID | None = None,
    assigned_at: datetime = TIMES[1],
) -> dict[str, object]:
    return {
        "assignment_id": str(assignment_id),
        "requirement_id": requirement_id,
        "page_ids": [str(page_id) for page_id in page_ids],
        "origin": origin,
        "status": status,
        "confirmation_id": None if confirmation_id is None else str(confirmation_id),
        "assigned_at": assigned_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


def _assignment_confirmation(
    confirmation_id: UUID,
    assignment_id: UUID,
    *,
    recorded_at: datetime = TIMES[2],
) -> dict[str, object]:
    return {
        "confirmation_id": str(confirmation_id),
        "confirmation_version": 1,
        "confirmation_code": ASSIGNMENT_CONFIRMATION_CODE,
        "statement_version": 1,
        "state": "confirmed",
        "actor": "user",
        "scope": {"kind": "assignment", "assignment_id": str(assignment_id)},
        "recorded_at": recorded_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


def _case_confirmation() -> dict[str, object]:
    return {
        "confirmation_id": str(CONFIRMATION_IDS[7]),
        "confirmation_version": 1,
        "confirmation_code": "current-instructions-reviewed",
        "statement_version": 1,
        "state": "confirmed",
        "actor": "user",
        "scope": {"kind": "case"},
        "recorded_at": "2026-08-28T10:02:00Z",
    }


def _case_confirmations(count: int) -> list[dict[str, object]]:
    return [
        {
            "confirmation_id": str(UUID(f"b0000000-0000-4000-8000-{number:012d}")),
            "confirmation_version": 1,
            "confirmation_code": "current-instructions-reviewed",
            "statement_version": 1,
            "state": "confirmed",
            "actor": "user",
            "scope": {"kind": "case"},
            "recorded_at": "2026-08-28T10:02:00Z",
        }
        for number in range(1, count + 1)
    ]


def _document(
    *,
    active_page_ids: tuple[UUID, ...] = PAGE_IDS[:4],
    template_bound: bool = False,
    assignments: list[dict[str, object]] | None = None,
    confirmations: list[dict[str, object]] | None = None,
    revision: int = 0,
) -> dict[str, object]:
    all_pages = PAGE_IDS[:4]
    return {
        "schema_version": 2,
        "case_id": str(CASE_ID),
        "revision": revision,
        "created_at": "2026-08-28T10:00:00Z",
        "updated_at": "2026-08-28T10:09:00Z",
        "template_ref": BINDING.to_manifest() if template_bound else None,
        "sources": [
            {
                "source_id": str(SOURCE_ID),
                "display_name": "Synthetic packet.pdf",
                "storage_name": f"{SOURCE_ID}.bin",
                "detected_media_type": "application/pdf",
                "byte_size": 4096,
                "page_count": len(all_pages),
                "sha256": "a" * 64,
                "signature_state": "none_detected",
                "encryption_state": "unencrypted",
                "import_state": "complete",
                "imported_at": "2026-08-28T10:01:00Z",
            }
        ],
        "pages": [
            {
                "page_id": str(page_id),
                "source_id": str(SOURCE_ID),
                "source_page_index": index,
            }
            for index, page_id in enumerate(all_pages)
        ],
        "page_sequence_base": [str(page_id) for page_id in all_pages],
        "page_sequence": [str(page_id) for page_id in active_page_ids],
        "page_operations": [],
        "assets": [],
        "overlays": [],
        "assignments": [] if assignments is None else assignments,
        "constraints": {
            "constraints_version": 1,
            "allowed_output_modes": ["combined_pdf"],
            "encryption_policy": "must_be_unencrypted",
        },
        "confirmations": [] if confirmations is None else confirmations,
        "export_history": {"storage_name": "export-history.json", "entries": []},
    }


def _snapshot(document: dict[str, object]) -> ManifestSnapshot:
    return ManifestSnapshot(
        CASE_ID,
        2,
        cast(int, document["revision"]),
        document,
        "b" * 64,
        "c" * 64,
    )


def _ids(*values: UUID) -> Callable[[], UUID]:
    iterator = iter(values)
    return lambda: next(iterator)


def test_empty_projection_is_template_ordered_missing_and_visibly_unassigned() -> None:
    history = ChecklistCommandHistory.from_snapshot(_snapshot(_document()), _definition())

    projection = history.projection

    assert tuple(item.requirement.requirement_id for item in projection.requirements) == (
        "application-form",
        "identity-evidence",
        "address-evidence",
        "supporting-evidence",
    )
    assert tuple(item.status for item in projection.requirements) == (
        ChecklistStatus.MISSING,
        ChecklistStatus.MISSING,
        ChecklistStatus.MISSING,
        ChecklistStatus.READY,
    )
    assert projection.active_page_ids == PAGE_IDS[:4]
    assert projection.unassigned_page_ids == PAGE_IDS[:4]
    assert projection.unconfirmed_page_ids == ()
    assert not history.can_undo
    assert not history.can_redo
    assert not history.changed


def test_user_add_is_pending_added_and_binds_exact_template_without_confirmation() -> None:
    history = ChecklistCommandHistory.from_snapshot(_snapshot(_document()), _definition())

    changed = history.execute(
        AddDocumentAssignmentCommand("application-form", PAGE_IDS[:2], TIMES[1]),
        id_factory=_ids(ASSIGNMENT_IDS[0]),
    )

    assignment = changed.assignments[0]
    assert assignment.origin is AssignmentOrigin.USER
    assert assignment.review_state is AssignmentReviewState.SUGGESTED
    assert assignment.confirmation_id is None
    assert changed.projection.requirement("application-form").status is ChecklistStatus.ADDED
    assert changed.projection.unassigned_page_ids == PAGE_IDS[2:4]
    assert changed.projection.unconfirmed_page_ids == PAGE_IDS[:2]
    candidate = changed.to_candidate(_snapshot(_document()))
    assert candidate["template_ref"] == BINDING.to_manifest()
    assert candidate["confirmations"] == []
    assert cast(list[dict[str, object]], candidate["assignments"])[0]["status"] == "suggested"


def test_automation_suggestion_needs_review_and_cannot_satisfy_count() -> None:
    history = ChecklistCommandHistory.from_snapshot(_snapshot(_document()), _definition())

    changed = history.execute(
        SuggestDocumentAssignmentCommand("identity-evidence", (PAGE_IDS[0],), TIMES[1]),
        id_factory=_ids(ASSIGNMENT_IDS[0]),
    )

    item = changed.projection.requirement("identity-evidence")
    assert item.status is ChecklistStatus.NEEDS_REVIEW
    assert item.confirmed_count == 0
    assert changed.assignments[0].origin is AssignmentOrigin.AUTOMATION
    assert changed.projection.unassigned_page_ids == PAGE_IDS[1:4]
    assert changed.projection.unconfirmed_page_ids == (PAGE_IDS[0],)


def test_explicit_confirm_adds_exact_coupling_and_redo_replays_ids_and_times() -> None:
    opened = ChecklistCommandHistory.from_snapshot(_snapshot(_document()), _definition())
    added = opened.execute(
        SuggestDocumentAssignmentCommand("identity-evidence", PAGE_IDS[:2], TIMES[1]),
        id_factory=_ids(ASSIGNMENT_IDS[0]),
    )
    confirmed = added.execute(
        ConfirmAssignmentCommand(ASSIGNMENT_IDS[0], TIMES[2]),
        id_factory=_ids(CONFIRMATION_IDS[0]),
    )

    assignment = confirmed.assignment(ASSIGNMENT_IDS[0])
    assert assignment.origin is AssignmentOrigin.AUTOMATION
    assert assignment.review_state is AssignmentReviewState.USER_CONFIRMED
    assert assignment.confirmation_id == CONFIRMATION_IDS[0]
    assert confirmed.projection.requirement("identity-evidence").status is ChecklistStatus.READY
    assert confirmed.projection.unassigned_page_ids == PAGE_IDS[2:4]
    assert confirmed.projection.unconfirmed_page_ids == ()
    candidate = confirmed.to_candidate(_snapshot(_document()))
    confirmation = cast(list[dict[str, object]], candidate["confirmations"])[0]
    assert confirmation == _assignment_confirmation(
        CONFIRMATION_IDS[0],
        ASSIGNMENT_IDS[0],
        recorded_at=TIMES[2],
    )

    pending_again = confirmed.undo()
    replayed = pending_again.redo()

    assert pending_again.assignment(ASSIGNMENT_IDS[0]).confirmation_id is None
    assert replayed.assignment(ASSIGNMENT_IDS[0]) == assignment
    assert replayed.to_candidate(_snapshot(_document())) == candidate


def test_four_textual_statuses_are_distinct_in_one_projection() -> None:
    definition = _definition(application_minimum=2)
    history = ChecklistCommandHistory.from_snapshot(_snapshot(_document()), definition)
    history = history.execute(
        AddDocumentAssignmentCommand("application-form", (PAGE_IDS[0],), TIMES[1]),
        id_factory=_ids(ASSIGNMENT_IDS[0]),
    )
    history = history.execute(
        SuggestDocumentAssignmentCommand("identity-evidence", (PAGE_IDS[1],), TIMES[1]),
        id_factory=_ids(ASSIGNMENT_IDS[1]),
    )
    history = history.execute(
        AddDocumentAssignmentCommand("address-evidence", (PAGE_IDS[2],), TIMES[1]),
        id_factory=_ids(ASSIGNMENT_IDS[2]),
    ).execute(
        ConfirmAssignmentCommand(ASSIGNMENT_IDS[2], TIMES[2]),
        id_factory=_ids(CONFIRMATION_IDS[2]),
    )

    statuses = {
        item.requirement.requirement_id: item.status for item in history.projection.requirements
    }

    assert statuses == {
        "application-form": ChecklistStatus.ADDED,
        "identity-evidence": ChecklistStatus.NEEDS_REVIEW,
        "address-evidence": ChecklistStatus.READY,
        "supporting-evidence": ChecklistStatus.READY,
    }
    assert {status.value for status in ChecklistStatus} == {
        "missing",
        "added",
        "needs_review",
        "ready",
    }


def test_confirmed_document_count_is_by_assignment_not_page_side() -> None:
    history = ChecklistCommandHistory.from_snapshot(_snapshot(_document()), _definition())
    history = history.execute(
        AddDocumentAssignmentCommand("identity-evidence", PAGE_IDS[:2], TIMES[1]),
        id_factory=_ids(ASSIGNMENT_IDS[0]),
    ).execute(
        ConfirmAssignmentCommand(ASSIGNMENT_IDS[0], TIMES[2]),
        id_factory=_ids(CONFIRMATION_IDS[0]),
    )

    item = history.projection.requirement("identity-evidence")

    assert len(item.assignments) == 1
    assert item.confirmed_count == 1
    assert item.status is ChecklistStatus.READY


def test_page_unit_counts_unique_active_confirmed_pages() -> None:
    history = ChecklistCommandHistory.from_snapshot(_snapshot(_document()), _definition())
    history = history.execute(
        AddDocumentAssignmentCommand("address-evidence", PAGE_IDS[:2], TIMES[1]),
        id_factory=_ids(ASSIGNMENT_IDS[0]),
    ).execute(
        ConfirmAssignmentCommand(ASSIGNMENT_IDS[0], TIMES[2]),
        id_factory=_ids(CONFIRMATION_IDS[0]),
    )

    assert history.projection.requirement("address-evidence").confirmed_count == 2


def test_loaded_inactive_and_overlapping_assignments_are_review_findings_not_rejected() -> None:
    assignments = [
        _assignment(
            ASSIGNMENT_IDS[0],
            "application-form",
            (PAGE_IDS[0], PAGE_IDS[3]),
            status="user_confirmed",
            confirmation_id=CONFIRMATION_IDS[0],
        ),
        _assignment(
            ASSIGNMENT_IDS[1],
            "identity-evidence",
            (PAGE_IDS[0],),
            origin="automation",
        ),
    ]
    confirmations = [_assignment_confirmation(CONFIRMATION_IDS[0], ASSIGNMENT_IDS[0])]
    history = ChecklistCommandHistory.from_snapshot(
        _snapshot(
            _document(
                active_page_ids=PAGE_IDS[:3],
                template_bound=True,
                assignments=assignments,
                confirmations=confirmations,
            )
        ),
        _definition(),
    )

    assert history.projection.requirement("application-form").status is ChecklistStatus.NEEDS_REVIEW
    assert (
        history.projection.requirement("identity-evidence").status is ChecklistStatus.NEEDS_REVIEW
    )
    assert history.projection.unassigned_page_ids == (PAGE_IDS[1], PAGE_IDS[2])
    assert history.projection.unconfirmed_page_ids == (PAGE_IDS[0],)


def test_move_invalidates_confirmation_and_undo_restores_exact_record() -> None:
    opened = ChecklistCommandHistory.from_snapshot(_snapshot(_document()), _definition())
    confirmed = opened.execute(
        AddDocumentAssignmentCommand("application-form", (PAGE_IDS[0],), TIMES[1]),
        id_factory=_ids(ASSIGNMENT_IDS[0]),
    ).execute(
        ConfirmAssignmentCommand(ASSIGNMENT_IDS[0], TIMES[2]),
        id_factory=_ids(CONFIRMATION_IDS[0]),
    )
    moved = confirmed.execute(
        MoveAssignmentCommand(ASSIGNMENT_IDS[0], "identity-evidence", TIMES[3])
    )

    assignment = moved.assignment(ASSIGNMENT_IDS[0])
    assert assignment.requirement_id == "identity-evidence"
    assert assignment.origin is AssignmentOrigin.USER
    assert assignment.review_state is AssignmentReviewState.SUGGESTED
    assert assignment.confirmation_id is None
    assert moved.projection.requirement("identity-evidence").status is ChecklistStatus.ADDED
    assert moved.to_candidate(_snapshot(_document()))["confirmations"] == []

    restored = moved.undo()

    assert restored.assignment(ASSIGNMENT_IDS[0]) == confirmed.assignment(ASSIGNMENT_IDS[0])
    assert (
        len(cast(list[object], restored.to_candidate(_snapshot(_document()))["confirmations"])) == 1
    )


def test_page_membership_change_invalidates_confirmation_but_page_reorder_does_not() -> None:
    history = ChecklistCommandHistory.from_snapshot(_snapshot(_document()), _definition())
    history = history.execute(
        AddDocumentAssignmentCommand("identity-evidence", PAGE_IDS[:2], TIMES[1]),
        id_factory=_ids(ASSIGNMENT_IDS[0]),
    ).execute(
        ConfirmAssignmentCommand(ASSIGNMENT_IDS[0], TIMES[2]),
        id_factory=_ids(CONFIRMATION_IDS[0]),
    )
    reordered = history.execute(
        ReorderAssignmentPagesCommand(ASSIGNMENT_IDS[0], (PAGE_IDS[1], PAGE_IDS[0]))
    )

    assert reordered.assignment(ASSIGNMENT_IDS[0]).confirmation_id == CONFIRMATION_IDS[0]
    assert reordered.assignment(ASSIGNMENT_IDS[0]).page_ids == (PAGE_IDS[1], PAGE_IDS[0])

    replaced = reordered.execute(
        ReplaceAssignmentPagesCommand(ASSIGNMENT_IDS[0], (PAGE_IDS[2],), TIMES[3])
    )

    assert replaced.assignment(ASSIGNMENT_IDS[0]).review_state is AssignmentReviewState.SUGGESTED
    assert replaced.assignment(ASSIGNMENT_IDS[0]).confirmation_id is None
    assert replaced.to_candidate(_snapshot(_document()))["confirmations"] == []


def test_remove_deletes_scoped_confirmation_and_is_exactly_undoable() -> None:
    history = ChecklistCommandHistory.from_snapshot(_snapshot(_document()), _definition())
    history = history.execute(
        AddDocumentAssignmentCommand("application-form", (PAGE_IDS[0],), TIMES[1]),
        id_factory=_ids(ASSIGNMENT_IDS[0]),
    ).execute(
        ConfirmAssignmentCommand(ASSIGNMENT_IDS[0], TIMES[2]),
        id_factory=_ids(CONFIRMATION_IDS[0]),
    )
    removed = history.execute(RemoveAssignmentCommand(ASSIGNMENT_IDS[0]))

    assert removed.assignments == ()
    assert removed.projection.unassigned_page_ids == PAGE_IDS[:4]
    assert removed.to_candidate(_snapshot(_document()))["confirmations"] == []
    assert removed.undo().assignment(ASSIGNMENT_IDS[0]) == history.assignment(ASSIGNMENT_IDS[0])


def test_document_and_page_reorder_are_complete_deterministic_orders() -> None:
    history = ChecklistCommandHistory.from_snapshot(_snapshot(_document()), _definition())
    history = history.execute(
        AddDocumentAssignmentCommand("identity-evidence", (PAGE_IDS[0], PAGE_IDS[1]), TIMES[1]),
        id_factory=_ids(ASSIGNMENT_IDS[0]),
    ).execute(
        AddDocumentAssignmentCommand("identity-evidence", (PAGE_IDS[2],), TIMES[1]),
        id_factory=_ids(ASSIGNMENT_IDS[1]),
    )
    history = history.execute(
        ReorderRequirementAssignmentsCommand(
            "identity-evidence", (ASSIGNMENT_IDS[1], ASSIGNMENT_IDS[0])
        )
    ).execute(ReorderAssignmentPagesCommand(ASSIGNMENT_IDS[0], (PAGE_IDS[1], PAGE_IDS[0])))

    item = history.projection.requirement("identity-evidence")

    assert tuple(record.assignment_id for record in item.assignments) == (
        ASSIGNMENT_IDS[1],
        ASSIGNMENT_IDS[0],
    )
    assert history.assignment(ASSIGNMENT_IDS[0]).page_ids == (PAGE_IDS[1], PAGE_IDS[0])

    with pytest.raises(ChecklistError) as incomplete:
        history.execute(
            ReorderRequirementAssignmentsCommand("identity-evidence", (ASSIGNMENT_IDS[0],))
        )
    assert incomplete.value.code is ChecklistErrorCode.ORDER_INVALID


def test_new_commands_reject_inactive_unknown_and_already_placed_pages() -> None:
    history = ChecklistCommandHistory.from_snapshot(
        _snapshot(_document(active_page_ids=PAGE_IDS[:2])),
        _definition(),
    )
    placed = history.execute(
        AddDocumentAssignmentCommand("application-form", (PAGE_IDS[0],), TIMES[1]),
        id_factory=_ids(ASSIGNMENT_IDS[0]),
    )

    with pytest.raises(ChecklistError) as duplicate:
        placed.execute(
            SuggestDocumentAssignmentCommand("identity-evidence", (PAGE_IDS[0],), TIMES[1]),
            id_factory=_ids(ASSIGNMENT_IDS[1]),
        )
    with pytest.raises(ChecklistError) as inactive:
        history.execute(
            AddDocumentAssignmentCommand("application-form", (PAGE_IDS[2],), TIMES[1]),
            id_factory=_ids(ASSIGNMENT_IDS[1]),
        )
    with pytest.raises(ChecklistError) as unknown:
        history.execute(
            AddDocumentAssignmentCommand("application-form", (PAGE_IDS[5],), TIMES[1]),
            id_factory=_ids(ASSIGNMENT_IDS[1]),
        )

    assert duplicate.value.code is ChecklistErrorCode.PAGE_ALREADY_ASSIGNED
    assert inactive.value.code is ChecklistErrorCode.PAGE_INACTIVE
    assert unknown.value.code is ChecklistErrorCode.PAGE_UNKNOWN


def test_undo_redo_branching_keeps_generated_identifiers_reserved() -> None:
    history = ChecklistCommandHistory.from_snapshot(_snapshot(_document()), _definition())
    first = history.execute(
        AddDocumentAssignmentCommand("application-form", (PAGE_IDS[0],), TIMES[1]),
        id_factory=_ids(ASSIGNMENT_IDS[0]),
    )
    undone = first.undo()
    branched = undone.execute(
        AddDocumentAssignmentCommand("identity-evidence", (PAGE_IDS[1],), TIMES[1]),
        id_factory=_ids(ASSIGNMENT_IDS[0], ASSIGNMENT_IDS[1]),
    )

    assert branched.assignments[0].assignment_id == ASSIGNMENT_IDS[1]
    assert not branched.can_redo
    with pytest.raises(ChecklistError) as raised:
        branched.redo()
    assert raised.value.code is ChecklistErrorCode.REDO_UNAVAILABLE


def test_changed_tracks_exact_opening_state_not_history_stack_shape() -> None:
    assignments = [
        _assignment(ASSIGNMENT_IDS[0], "identity-evidence", (PAGE_IDS[0],)),
        _assignment(ASSIGNMENT_IDS[1], "identity-evidence", (PAGE_IDS[1],)),
    ]
    opened = ChecklistCommandHistory.from_snapshot(
        _snapshot(_document(template_bound=True, assignments=assignments)),
        _definition(),
    )
    swapped = opened.execute(
        ReorderRequirementAssignmentsCommand(
            "identity-evidence", (ASSIGNMENT_IDS[1], ASSIGNMENT_IDS[0])
        )
    )
    restored_manually = swapped.execute(
        ReorderRequirementAssignmentsCommand(
            "identity-evidence", (ASSIGNMENT_IDS[0], ASSIGNMENT_IDS[1])
        )
    )

    assert swapped.changed
    assert restored_manually.can_undo
    assert not restored_manually.changed

    added = opened.execute(
        AddDocumentAssignmentCommand("application-form", (PAGE_IDS[2],), TIMES[1]),
        id_factory=_ids(ASSIGNMENT_IDS[2]),
    )
    undone = added.undo()
    assert added.changed
    assert undone.can_redo
    assert not undone.changed


def test_session_extension_authenticates_origin_and_replayable_history() -> None:
    opened = ChecklistCommandHistory.from_snapshot(_snapshot(_document()), _definition())
    changed = opened.execute(
        AddDocumentAssignmentCommand("application-form", (PAGE_IDS[0],), TIMES[1]),
        id_factory=_ids(ASSIGNMENT_IDS[0]),
    )
    undone = changed.undo()

    assert opened.is_session_extension_of(opened)
    assert changed.is_session_extension_of(opened)
    assert undone.is_session_extension_of(opened)

    later_document = _document(revision=1)
    later = ChecklistCommandHistory.from_snapshot(_snapshot(later_document), _definition())
    different_pages = ChecklistCommandHistory.from_snapshot(
        _snapshot(_document(active_page_ids=PAGE_IDS[:3])),
        _definition(),
    )
    assert not changed.is_session_extension_of(later)
    assert not changed.is_session_extension_of(different_pages)


def test_confirmation_capacity_counts_every_scope_and_rebases_fail_closed() -> None:
    unrelated = _case_confirmations(4_095)
    opened_document = _document(confirmations=unrelated)
    opened = ChecklistCommandHistory.from_snapshot(
        _snapshot(opened_document),
        _definition(),
    )
    pending = opened.execute(
        AddDocumentAssignmentCommand("application-form", (PAGE_IDS[0],), TIMES[1]),
        id_factory=_ids(ASSIGNMENT_IDS[0]),
    )
    confirmed = pending.execute(
        ConfirmAssignmentCommand(ASSIGNMENT_IDS[0], TIMES[2]),
        id_factory=_ids(CONFIRMATION_IDS[0]),
    )
    assert (
        len(cast(list[object], confirmed.to_candidate(_snapshot(opened_document))["confirmations"]))
        == 4_096
    )

    full_current = _document(
        confirmations=unrelated + [_case_confirmations(4_096)[-1]],
        revision=1,
    )
    with pytest.raises(ChecklistError) as rebased:
        confirmed.to_candidate(_snapshot(full_current))
    assert rebased.value.code is ChecklistErrorCode.CAPACITY_EXCEEDED

    full_opening = ChecklistCommandHistory.from_snapshot(
        _snapshot(_document(confirmations=_case_confirmations(4_096))),
        _definition(),
    )
    full_pending = full_opening.execute(
        AddDocumentAssignmentCommand("application-form", (PAGE_IDS[0],), TIMES[1]),
        id_factory=_ids(ASSIGNMENT_IDS[0]),
    )
    with pytest.raises(ChecklistError) as full:
        full_pending.execute(
            ConfirmAssignmentCommand(ASSIGNMENT_IDS[0], TIMES[2]),
            id_factory=_ids(CONFIRMATION_IDS[0]),
        )
    assert full.value.code is ChecklistErrorCode.CAPACITY_EXCEEDED


def test_removing_a_confirmed_assignment_frees_capacity_and_undo_restores_it() -> None:
    assignments = [
        _assignment(
            ASSIGNMENT_IDS[0],
            "application-form",
            (PAGE_IDS[0],),
            status="user_confirmed",
            confirmation_id=CONFIRMATION_IDS[0],
        )
    ]
    confirmations = _case_confirmations(4_095) + [
        _assignment_confirmation(CONFIRMATION_IDS[0], ASSIGNMENT_IDS[0])
    ]
    opened_document = _document(
        template_bound=True,
        assignments=assignments,
        confirmations=confirmations,
    )
    opened = ChecklistCommandHistory.from_snapshot(
        _snapshot(opened_document),
        _definition(),
    )
    removed = opened.execute(RemoveAssignmentCommand(ASSIGNMENT_IDS[0]))

    assert not removed.undo().changed
    replacement = removed.execute(
        AddDocumentAssignmentCommand("application-form", (PAGE_IDS[0],), TIMES[3]),
        id_factory=_ids(ASSIGNMENT_IDS[1]),
    ).execute(
        ConfirmAssignmentCommand(ASSIGNMENT_IDS[1], TIMES[4]),
        id_factory=_ids(CONFIRMATION_IDS[1]),
    )
    assert (
        len(
            cast(
                list[object],
                replacement.to_candidate(_snapshot(opened_document))["confirmations"],
            )
        )
        == 4_096
    )


def test_to_candidate_preserves_unrelated_state_and_confirmation_order() -> None:
    original = _document(confirmations=[_case_confirmation()])
    history = ChecklistCommandHistory.from_snapshot(_snapshot(original), _definition())
    changed = history.execute(
        AddDocumentAssignmentCommand("application-form", (PAGE_IDS[0],), TIMES[1]),
        id_factory=_ids(ASSIGNMENT_IDS[0]),
    ).execute(
        ConfirmAssignmentCommand(ASSIGNMENT_IDS[0], TIMES[2]),
        id_factory=_ids(CONFIRMATION_IDS[0]),
    )

    candidate = changed.to_candidate(_snapshot(original))

    assert candidate["sources"] == original["sources"]
    assert candidate["page_sequence"] == original["page_sequence"]
    assert candidate["constraints"] == original["constraints"]
    confirmations = cast(list[dict[str, object]], candidate["confirmations"])
    assert confirmations[0] == _case_confirmation()
    assert confirmations[1]["confirmation_id"] == str(CONFIRMATION_IDS[0])

    saved = deepcopy(candidate)
    saved["revision"] = 1
    saved["updated_at"] = "2026-08-28T10:09:01Z"
    rebound = changed.to_candidate(_snapshot(saved))
    assert rebound["assignments"] == candidate["assignments"]
    assert rebound["confirmations"] == candidate["confirmations"]


def test_to_candidate_rejects_foreign_checklist_state_but_preserves_unrelated_revision() -> None:
    original = _document()
    history = ChecklistCommandHistory.from_snapshot(_snapshot(original), _definition())
    changed = history.execute(
        AddDocumentAssignmentCommand("application-form", (PAGE_IDS[0],), TIMES[1]),
        id_factory=_ids(ASSIGNMENT_IDS[0]),
    )
    foreign = _document(
        template_bound=True,
        assignments=[
            _assignment(
                ASSIGNMENT_IDS[1],
                "identity-evidence",
                (PAGE_IDS[1],),
            )
        ],
        revision=1,
    )

    with pytest.raises(ChecklistError) as raised:
        changed.to_candidate(_snapshot(foreign))

    assert raised.value.code is ChecklistErrorCode.CONCURRENT_CHANGE


def test_exact_template_binding_is_required_for_populated_or_different_cases() -> None:
    populated_unbound = _document(
        assignments=[_assignment(ASSIGNMENT_IDS[0], "application-form", (PAGE_IDS[0],))]
    )
    mismatched = _document(template_bound=True)
    cast(dict[str, object], mismatched["template_ref"])["sha256"] = "d" * 64

    with pytest.raises(ChecklistError) as unbound:
        ChecklistCommandHistory.from_snapshot(_snapshot(populated_unbound), _definition())
    with pytest.raises(ChecklistError) as different:
        ChecklistCommandHistory.from_snapshot(_snapshot(mismatched), _definition())

    assert unbound.value.code is ChecklistErrorCode.TEMPLATE_REQUIRED
    assert different.value.code is ChecklistErrorCode.TEMPLATE_MISMATCH


def test_errors_and_representations_hide_identifiers_digests_and_times() -> None:
    history = ChecklistCommandHistory.from_snapshot(_snapshot(_document()), _definition())
    changed = history.execute(
        AddDocumentAssignmentCommand("application-form", (PAGE_IDS[0],), TIMES[1]),
        id_factory=_ids(ASSIGNMENT_IDS[0]),
    )

    with pytest.raises(ChecklistError) as raised:
        changed.assignment(ASSIGNMENT_IDS[7])

    rendered = "\n".join(
        (
            repr(changed),
            repr(changed.assignments[0]),
            repr(changed.projection),
            repr(_definition()),
            repr(raised.value),
            str(raised.value),
        )
    )
    for prohibited in (
        str(CASE_ID),
        str(PAGE_IDS[0]),
        str(ASSIGNMENT_IDS[0]),
        BINDING.sha256,
        TIMES[1].isoformat(),
        "Synthetic packet.pdf",
    ):
        assert prohibited not in rendered
    assert raised.value.code is ChecklistErrorCode.ASSIGNMENT_UNKNOWN
    assert raised.value.__cause__ is None


def test_confirm_rejects_time_before_assignment_without_mutation() -> None:
    history = ChecklistCommandHistory.from_snapshot(_snapshot(_document()), _definition())
    added = history.execute(
        AddDocumentAssignmentCommand("application-form", (PAGE_IDS[0],), TIMES[2]),
        id_factory=_ids(ASSIGNMENT_IDS[0]),
    )

    with pytest.raises(ChecklistError) as raised:
        added.execute(
            ConfirmAssignmentCommand(ASSIGNMENT_IDS[0], TIMES[1]),
            id_factory=_ids(CONFIRMATION_IDS[0]),
        )

    assert raised.value.code is ChecklistErrorCode.TIMESTAMP_INVALID
    assert added.assignment(ASSIGNMENT_IDS[0]).review_state is AssignmentReviewState.SUGGESTED


def test_loaded_records_are_a_non_undoable_baseline() -> None:
    assignments = [_assignment(ASSIGNMENT_IDS[0], "application-form", (PAGE_IDS[0],))]
    history = ChecklistCommandHistory.from_snapshot(
        _snapshot(_document(template_bound=True, assignments=assignments)),
        _definition(),
    )

    assert not history.can_undo
    with pytest.raises(ChecklistError) as raised:
        history.undo()
    assert raised.value.code is ChecklistErrorCode.UNDO_UNAVAILABLE

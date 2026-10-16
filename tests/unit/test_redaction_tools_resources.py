"""Deterministic supported-limit work bounds and adversarial delta-journal checks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from paperworkshell.app.redaction_tools import (
    MAX_REDACTION_EDIT_STEPS,
    RedactionEdit,
    RedactionToolsState,
    _mark_facts,
)
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.redaction import (
    MAX_REDACTION_MARKS,
    RedactionError,
    RedactionErrorCode,
    RedactionMark,
)
from tests.unit.test_organizer_backend import CASE_ID, PAGE_IDS, _backend, _snapshot
from tests.unit.test_redaction_tools import _current_document


def _maximum_state(*, other_page_marks: bool, edit_count: int) -> RedactionToolsState:
    box = NormalizedPageBox(100_000, 100_000, 100_000, 100_000)
    opening = tuple(
        RedactionMark(
            UUID(f"8f060200-0000-4000-8000-{index:012x}"),
            PAGE_IDS[1] if other_page_marks and index > 1 else PAGE_IDS[0],
            box,
        )
        for index in range(1, MAX_REDACTION_MARKS + 1)
    )
    before = opening[0]
    edits: list[RedactionEdit] = []
    for index in range(1, edit_count + 1):
        after = RedactionMark(
            before.mark_id,
            before.page_id,
            NormalizedPageBox(100_000 + index * 1_000, 100_000, 100_000, 100_000),
        )
        edits.append(RedactionEdit(before, after))
        before = after
    return RedactionToolsState(
        CASE_ID,
        0,
        PAGE_IDS[0],
        uuid4(),
        opening,
        tuple(edits),
        edit_count,
        opening[0].mark_id,
    )


@pytest.mark.parametrize("other_page_marks", [False, True])
def test_supported_limit_transition_validation_is_linear_in_marks_plus_edits(
    monkeypatch: pytest.MonkeyPatch,
    other_page_marks: bool,
) -> None:
    state = _maximum_state(
        other_page_marks=other_page_marks, edit_count=MAX_REDACTION_EDIT_STEPS - 1
    )
    original = RedactionMark.__post_init__
    validations = 0

    def counted(mark: RedactionMark) -> None:
        nonlocal validations
        validations += 1
        original(mark)

    monkeypatch.setattr(RedactionMark, "__post_init__", counted)
    changed = state.replace_selected_box(NormalizedPageBox(500_000, 100_000, 100_000, 100_000))
    # One opening pass plus at most two records per retained delta and one new edit.
    # The previous full-case snapshots visited >1,000,000 marks at this input size.
    work_bound = MAX_REDACTION_MARKS + 2 * MAX_REDACTION_EDIT_STEPS + 4
    assert MAX_REDACTION_MARKS <= validations <= work_bound
    transitions: tuple[Callable[[], RedactionToolsState], ...] = (
        lambda: changed.select(changed.opening_marks[0].mark_id),
        changed.undo,
    )
    for transition in transitions:
        validations = 0
        transition()
        assert MAX_REDACTION_MARKS <= validations <= work_bound
    assert len(changed.steps) == MAX_REDACTION_EDIT_STEPS
    assert all(type(edit) is RedactionEdit for edit in changed.steps)
    # The journal retains at most two mark references per edit, not 4096 per edit.
    assert sum(edit.before is not None for edit in changed.steps) <= MAX_REDACTION_EDIT_STEPS
    assert sum(edit.after is not None for edit in changed.steps) <= MAX_REDACTION_EDIT_STEPS
    validations = 0
    assert changed.selected_record is not None
    assert changed.replace_selected_box(changed.selected_record.box) is changed
    assert validations == 0


@pytest.mark.parametrize("tamper", ["before", "foreign_redo", "cache", "malformed"])
def test_untrusted_journal_and_derived_cache_are_revalidated_before_candidate(tamper: str) -> None:
    document = _current_document()
    opening = RedactionToolsState.from_snapshot(_snapshot(document), PAGE_IDS[0])
    created = opening.create()
    assert created.selected_record is not None
    changed = created.replace_selected_box(NormalizedPageBox(300_000, 300_000, 100_000, 100_000))
    forged = replace(changed)
    if tamper == "before":
        assert changed.steps[-1].before is not None
        wrong = RedactionMark(
            changed.steps[-1].before.mark_id,
            PAGE_IDS[0],
            NormalizedPageBox(0, 0, 100_000, 100_000),
        )
        object.__setattr__(
            forged, "steps", (*changed.steps[:-1], RedactionEdit(wrong, changed.steps[-1].after))
        )
    elif tamper == "foreign_redo":
        foreign = RedactionMark(uuid4(), PAGE_IDS[1], NormalizedPageBox(0, 0, 100_000, 100_000))
        object.__setattr__(forged, "steps", (*changed.steps, RedactionEdit(None, foreign)))
        # The foreign delta is only in redo history, but still must not be accepted.
    elif tamper == "malformed":
        object.__setattr__(forged, "steps", ((),))
        object.__setattr__(forged, "cursor", 1)
    else:
        foreign = RedactionMark(uuid4(), PAGE_IDS[1], NormalizedPageBox(0, 0, 100_000, 100_000))
        object.__setattr__(forged, "_current_marks", (foreign,))
        candidate = forged.to_candidate(_snapshot(document))
        assert (
            candidate["redaction_marks"]
            == changed.to_candidate(_snapshot(document))["redaction_marks"]
        )
        return
    with pytest.raises(RedactionError):
        forged.to_candidate(_snapshot(document))


def test_mutated_opening_is_rejected_by_independent_backend_authority() -> None:
    backend, repository, _, _, _ = _backend(_current_document())
    backend.open_case(CASE_ID)
    opening = backend.begin_redaction_tools(PAGE_IDS[0])
    forged_opening = RedactionMark(uuid4(), PAGE_IDS[0], NormalizedPageBox(0, 0, 100_000, 100_000))
    forged = replace(opening)
    object.__setattr__(forged, "opening_marks", (forged_opening,))
    assert not forged.matches_opening_facts(_mark_facts(opening.opening_marks))
    with pytest.raises(RedactionError) as failure:
        backend.save_redaction_tools(forged)
    assert failure.value.code is RedactionErrorCode.CONCURRENT_CHANGE
    assert repository.save_calls == 0
    backend.cancel_redaction_tools(opening)

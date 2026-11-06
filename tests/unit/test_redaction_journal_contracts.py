"""Independent order/history oracle for the bounded PWS-0602 delta journal."""

from __future__ import annotations

from collections.abc import Callable
from random import Random
from uuid import UUID

import pytest

from paperworkshell.app.redaction_tools import MAX_REDACTION_EDIT_STEPS, RedactionToolsState
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.redaction import RedactionError, RedactionMark, RedactionMarkCollection
from tests.unit.test_organizer_backend import PAGE_IDS, _snapshot
from tests.unit.test_redaction_tools import _current_document


def _mark(value: int, page: UUID, left: int = 100_000) -> RedactionMark:
    return RedactionMark(
        UUID(int=value, version=4), page, NormalizedPageBox(left, 100_000, 100_000, 100_000)
    )


def _fixed_id(value: UUID) -> Callable[[], UUID]:
    return lambda: value


def test_deterministic_edit_undo_redo_sequences_match_independent_full_order_oracle() -> None:
    initial: tuple[RedactionMark, ...] = (
        _mark(1, PAGE_IDS[0]),
        _mark(2, PAGE_IDS[1]),
        _mark(3, PAGE_IDS[0]),
    )
    document = RedactionMarkCollection(initial).to_manifest_document(_current_document())
    snapshot = _snapshot(document)
    state = RedactionToolsState.from_snapshot(snapshot, PAGE_IDS[0])
    history: list[tuple[RedactionMark, ...]] = [initial]
    cursor = 0
    next_id = 100
    random = Random(602)
    for _ in range(220):
        current = history[cursor]
        editable = tuple(mark for mark in current if mark.page_id == PAGE_IDS[0])
        operation = random.choice(("create", "replace", "delete", "undo", "redo"))
        if not editable and operation in {"replace", "delete"}:
            operation = "create"
        if operation == "undo":
            state = state.undo()
            cursor = max(0, cursor - 1)
        elif operation == "redo":
            state = state.redo()
            cursor = min(len(history) - 1, cursor + 1)
        else:
            if operation == "create":
                mark = _mark(next_id, PAGE_IDS[0], random.randrange(0, 700) * 1_000)
                next_id += 1
                state = state.create(mark.box, id_factory=_fixed_id(mark.mark_id))
                changed: tuple[RedactionMark, ...] = (*current, mark)
            else:
                selected = random.choice(editable)
                state = state.select(selected.mark_id)
                if operation == "delete":
                    state = state.delete_selected()
                    changed = tuple(mark for mark in current if mark.mark_id != selected.mark_id)
                else:
                    replacement = _mark(next_id, PAGE_IDS[0], random.randrange(0, 700) * 1_000)
                    replacement = RedactionMark(selected.mark_id, selected.page_id, replacement.box)
                    state = state.replace_selected_box(replacement.box)
                    changed = tuple(
                        replacement if mark.mark_id == selected.mark_id else mark
                        for mark in current
                    )
            if changed != current:
                history = [*history[: cursor + 1], changed]
                cursor += 1
        expected = history[cursor]
        actual = RedactionMarkCollection.from_manifest_document(state.to_candidate(snapshot)).marks
        assert actual == expected
        assert state.records == tuple(mark for mark in expected if mark.page_id == PAGE_IDS[0])
        assert state.cursor == cursor
        assert state.changed is (expected != initial)
        assert state.can_undo is (cursor > 0)
        assert state.can_redo is (cursor < len(history) - 1)


def test_delete_undo_restores_original_global_position_and_branch_discards_redo() -> None:
    initial: tuple[RedactionMark, ...] = (
        _mark(1, PAGE_IDS[0]),
        _mark(2, PAGE_IDS[1]),
        _mark(3, PAGE_IDS[0]),
    )
    document = RedactionMarkCollection(initial).to_manifest_document(_current_document())
    snapshot = _snapshot(document)
    opening = RedactionToolsState.from_snapshot(snapshot, PAGE_IDS[0])
    deleted = opening.select(initial[0].mark_id).delete_selected()
    assert (
        RedactionMarkCollection.from_manifest_document(deleted.to_candidate(snapshot)).marks
        == initial[1:]
    )
    restored = deleted.undo()
    assert (
        RedactionMarkCollection.from_manifest_document(restored.to_candidate(snapshot)).marks
        == initial
    )
    branch = restored.create(id_factory=lambda: UUID(int=4, version=4))
    assert not branch.can_redo
    assert branch.redo() is branch
    assert (
        RedactionMarkCollection.from_manifest_document(branch.to_candidate(snapshot)).marks[:3]
        == initial
    )


def test_undo_at_history_limit_releases_redo_capacity_for_new_branch() -> None:
    snapshot = _snapshot(_current_document())
    state = RedactionToolsState.from_snapshot(snapshot, PAGE_IDS[0]).create(
        id_factory=lambda: UUID(int=100, version=4)
    )
    for position in range(1, MAX_REDACTION_EDIT_STEPS):
        state = state.replace_selected_box(
            NormalizedPageBox(100_000 + position, 100_000, 100_000, 100_000)
        )
    assert state.cursor == MAX_REDACTION_EDIT_STEPS
    with pytest.raises(RedactionError):
        state.replace_selected_box(NormalizedPageBox(200_000, 100_000, 100_000, 100_000))
    branch = state.undo().replace_selected_box(
        NormalizedPageBox(300_000, 100_000, 100_000, 100_000)
    )
    assert branch.cursor == MAX_REDACTION_EDIT_STEPS and not branch.can_redo
    assert branch.selected_record is not None and branch.selected_record.box.x == 300_000

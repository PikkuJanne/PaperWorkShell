"""Revision-bound local editing of unapplied removal intent, without document parsing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final, Self
from uuid import UUID, uuid4

from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.domain.redaction import (
    MAX_REDACTION_MARKS,
    RedactionErrorCode,
    RedactionMark,
    RedactionMarkCollection,
    reject_redaction,
)

MAX_REDACTION_EDIT_STEPS: Final = 256
_ANNOUNCEMENTS: Final = frozenset(
    {
        "Removal-mark editor ready. Marks are unapplied; source information remains.",
        "Unapplied removal mark selected.",
        "Unapplied removal mark added and selected.",
        "Unapplied removal mark position and size updated.",
        "Unapplied removal mark removed.",
        "Last removal-mark edit undone.",
        "Removal-mark edit redone.",
    }
)


def is_redaction_announcement_safe(value: object) -> bool:
    """Recognize only fixed, content-free draft announcements."""

    return type(value) is str and value in _ANNOUNCEMENTS


def redaction_record_summary(record: RedactionMark, position: int, total: int) -> str:
    """Give a non-color status and full-page geometry without document content."""

    if (
        type(record) is not RedactionMark
        or type(position) is not int
        or type(total) is not int
        or not 1 <= position <= total
    ):
        reject_redaction(RedactionErrorCode.MODEL_INVALID, "redaction.summary")
    box = record.box
    return (
        f"Unapplied removal mark, item {position} of {total}; "
        f"left {box.x / 10000:.4f} percent; top {box.y / 10000:.4f} percent; "
        f"width {box.width / 10000:.4f} percent; height {box.height / 10000:.4f} percent. "
        "Source information remains."
    )


def _mark_facts(marks: tuple[RedactionMark, ...]) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (mark.mark_id, mark.page_id, mark.box.x, mark.box.y, mark.box.width, mark.box.height)
        for mark in marks
    )


@dataclass(frozen=True, slots=True, repr=False)
class RedactionEdit:
    """One bounded before/after change; it never retains a whole-case snapshot."""

    before: RedactionMark | None
    after: RedactionMark | None

    def __post_init__(self) -> None:
        if self.before is None and self.after is None:
            reject_redaction(RedactionErrorCode.MODEL_INVALID, "redaction.edit")
        for mark in (self.before, self.after):
            if mark is not None:
                if type(mark) is not RedactionMark:
                    reject_redaction(RedactionErrorCode.MODEL_INVALID, "redaction.edit")
                mark.__post_init__()
        if (
            self.before is not None
            and self.after is not None
            and (
                self.before.mark_id != self.after.mark_id
                or self.before.page_id != self.after.page_id
            )
        ):
            reject_redaction(RedactionErrorCode.CONCURRENT_CHANGE, "redaction.edit_identity")

    def __repr__(self) -> str:
        return "RedactionEdit(<intent-hidden>)"


def _replay_edits(
    opening_marks: tuple[RedactionMark, ...],
    edits: tuple[RedactionEdit, ...],
    cursor: int,
    page_id: UUID,
) -> tuple[RedactionMark, ...]:
    """Validate all deltas in one pass, materializing only the requested revision.

    Ordered dictionaries retain replacement positions. Replaying from the
    opening order restores a deleted mark at its exact position on undo, while
    creations append. Both the applied prefix and redo suffix are authenticated.
    """

    records = {mark.mark_id: mark for mark in opening_marks}
    current = opening_marks if cursor == 0 else None
    for position, edit in enumerate(edits, start=1):
        if type(edit) is not RedactionEdit:
            reject_redaction(RedactionErrorCode.MODEL_INVALID, "redaction.edit")
        edit.__post_init__()
        before, after = edit.before, edit.after
        if any(mark is not None and mark.page_id != page_id for mark in (before, after)):
            reject_redaction(RedactionErrorCode.CONCURRENT_CHANGE, "redaction.page_scope")
        identity = (
            before.mark_id if before is not None else after.mark_id if after is not None else None
        )
        if identity is None:
            reject_redaction(RedactionErrorCode.MODEL_INVALID, "redaction.edit")
        existing = records.get(identity)
        if before is None:
            if existing is not None:
                reject_redaction(RedactionErrorCode.IDENTIFIER_COLLISION, "redaction.edit")
        elif existing != before:
            reject_redaction(RedactionErrorCode.CONCURRENT_CHANGE, "redaction.edit_before")
        if after is None:
            del records[identity]
        else:
            records[identity] = after
        if len(records) > MAX_REDACTION_MARKS:
            reject_redaction(RedactionErrorCode.CAPACITY_EXCEEDED, "redaction.collection")
        if position == cursor:
            current = tuple(records.values())
    if current is None:
        reject_redaction(RedactionErrorCode.MODEL_INVALID, "redaction.cursor")
    return current


@dataclass(frozen=True, slots=True, repr=False)
class RedactionToolsState:
    """Bounded in-memory intent journal; saving never applies permanent removal."""

    case_id: UUID
    manifest_revision: int
    page_id: UUID
    editor_session_id: UUID
    opening_marks: tuple[RedactionMark, ...] = field(repr=False)
    steps: tuple[RedactionEdit, ...] = field(default=(), repr=False)
    cursor: int = 0
    selected_mark_id: UUID | None = None
    announcement: str = (
        "Removal-mark editor ready. Marks are unapplied; source information remains."
    )
    _current_marks: tuple[RedactionMark, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            any(
                type(value) is not UUID or value.version != 4
                for value in (self.case_id, self.page_id, self.editor_session_id)
            )
            or type(self.manifest_revision) is not int
            or self.manifest_revision < 0
            or type(self.opening_marks) is not tuple
            or type(self.steps) is not tuple
            or len(self.steps) > MAX_REDACTION_EDIT_STEPS
            or type(self.cursor) is not int
            or not 0 <= self.cursor <= len(self.steps)
            or not is_redaction_announcement_safe(self.announcement)
        ):
            reject_redaction(RedactionErrorCode.MODEL_INVALID, "redaction.editor")
        RedactionMarkCollection(self.opening_marks)
        current = _replay_edits(self.opening_marks, self.steps, self.cursor, self.page_id)
        object.__setattr__(self, "_current_marks", current)
        if self.selected_mark_id is not None:
            if type(self.selected_mark_id) is not UUID or self.selected_mark_id.version != 4:
                reject_redaction(RedactionErrorCode.IDENTIFIER_INVALID, "redaction.selection")
            if not any(mark.mark_id == self.selected_mark_id for mark in self.records):
                reject_redaction(RedactionErrorCode.RECORD_UNKNOWN, "redaction.selection")

    def __repr__(self) -> str:
        return f"RedactionToolsState(mark_count={len(self.records)}, changed={self.changed})"

    @classmethod
    def from_snapshot(cls, snapshot: ManifestSnapshot, page_id: UUID) -> Self:
        if type(snapshot) is not ManifestSnapshot or type(page_id) is not UUID:
            reject_redaction(RedactionErrorCode.MODEL_INVALID, "redaction.open")
        document = snapshot.document
        sequence = document.get("page_sequence")
        if type(sequence) is not list or str(page_id) not in sequence:
            reject_redaction(RedactionErrorCode.PAGE_UNKNOWN, "redaction.open_page")
        marks = RedactionMarkCollection.from_manifest_document(document).marks
        return cls(snapshot.case_id, snapshot.revision, page_id, uuid4(), marks)

    @property
    def records(self) -> tuple[RedactionMark, ...]:
        return tuple(mark for mark in self._current_marks if mark.page_id == self.page_id)

    @property
    def marks(self) -> tuple[RedactionMark, ...]:
        return self.records

    @property
    def selected_record(self) -> RedactionMark | None:
        return next((mark for mark in self.records if mark.mark_id == self.selected_mark_id), None)

    @property
    def selected_mark(self) -> RedactionMark | None:
        return self.selected_record

    @property
    def changed(self) -> bool:
        return _mark_facts(self._current_marks) != _mark_facts(self.opening_marks)

    @property
    def can_undo(self) -> bool:
        return self.cursor > 0

    @property
    def can_redo(self) -> bool:
        return self.cursor < len(self.steps)

    def select(self, mark_id: UUID) -> Self:
        if type(mark_id) is not UUID or not any(mark.mark_id == mark_id for mark in self.records):
            reject_redaction(RedactionErrorCode.RECORD_UNKNOWN, "redaction.selection")
        return self._transition(selection=mark_id, announcement="Unapplied removal mark selected.")

    def create(
        self,
        box: NormalizedPageBox | None = None,
        *,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> Self:
        chosen_box = NormalizedPageBox(100_000, 100_000, 300_000, 100_000) if box is None else box
        if type(chosen_box) is not NormalizedPageBox or not callable(id_factory):
            reject_redaction(RedactionErrorCode.BOX_INVALID, "redaction.create")
        mark = RedactionMark(id_factory(), self.page_id, chosen_box)
        return self._edit(
            RedactionEdit(None, mark),
            mark.mark_id,
            "Unapplied removal mark added and selected.",
        )

    def replace_selected_box(self, box: NormalizedPageBox) -> Self:
        selected = self._require_selected()
        if type(box) is not NormalizedPageBox:
            reject_redaction(RedactionErrorCode.BOX_INVALID, "redaction.box")
        if box == selected.box:
            return self
        replacement = RedactionMark(selected.mark_id, selected.page_id, box)
        return self._edit(
            RedactionEdit(selected, replacement),
            selected.mark_id,
            "Unapplied removal mark position and size updated.",
        )

    def delete_selected(self) -> Self:
        selected = self._require_selected()
        return self._edit(
            RedactionEdit(selected, None),
            None,
            "Unapplied removal mark removed.",
        )

    def remove_selected(self) -> Self:
        return self.delete_selected()

    def undo(self) -> Self:
        if not self.can_undo:
            return self
        return self._transition(
            cursor=self.cursor - 1, announcement="Last removal-mark edit undone."
        )

    def redo(self) -> Self:
        if not self.can_redo:
            return self
        return self._transition(cursor=self.cursor + 1, announcement="Removal-mark edit redone.")

    def to_candidate(self, snapshot: ManifestSnapshot) -> dict[str, object]:
        # Recompute the cache at the persistence boundary, including the redo suffix.
        self.__post_init__()
        if (
            type(snapshot) is not ManifestSnapshot
            or snapshot.case_id != self.case_id
            or snapshot.revision != self.manifest_revision
            or _mark_facts(RedactionMarkCollection.from_manifest_document(snapshot.document).marks)
            != _mark_facts(self.opening_marks)
        ):
            reject_redaction(RedactionErrorCode.CONCURRENT_CHANGE, "redaction.binding")
        candidate = RedactionMarkCollection(self._current_marks).to_manifest_document(
            snapshot.document
        )
        # Revalidate wire data even when presentation objects were changed through reflection.
        RedactionMarkCollection.from_manifest_document(candidate)
        return candidate

    def matches_opening_facts(self, facts: tuple[tuple[object, ...], ...]) -> bool:
        """Revalidate every draft entry against backend-private primitive opening facts."""

        self.__post_init__()
        return _mark_facts(self.opening_marks) == facts

    def _require_selected(self) -> RedactionMark:
        record = self.selected_record
        if record is None:
            reject_redaction(RedactionErrorCode.RECORD_UNKNOWN, "redaction.selection")
        return record

    def _edit(self, edit: RedactionEdit, selection: UUID | None, announcement: str) -> Self:
        if self.cursor >= MAX_REDACTION_EDIT_STEPS:
            reject_redaction(RedactionErrorCode.CAPACITY_EXCEEDED, "redaction.edit_steps")
        steps = (*self.steps[: self.cursor], edit)
        return self._transition(
            steps=steps, cursor=len(steps), selection=selection, announcement=announcement
        )

    def _transition(
        self,
        *,
        announcement: str,
        steps: tuple[RedactionEdit, ...] | None = None,
        cursor: int | None = None,
        selection: UUID | None | object = ...,
    ) -> Self:
        chosen_steps = self.steps if steps is None else steps
        chosen_cursor = self.cursor if cursor is None else cursor
        chosen_selection = self.selected_mark_id if selection is ... else selection
        if chosen_selection is not None and type(chosen_selection) is not UUID:
            reject_redaction(RedactionErrorCode.MODEL_INVALID, "redaction.selection")
        result = type(self)(
            self.case_id,
            self.manifest_revision,
            self.page_id,
            self.editor_session_id,
            self.opening_marks,
            chosen_steps,
            chosen_cursor,
            None,
            announcement,
        )
        # Replaying once establishes both the current records and valid selection.
        # The cache is always recomputed again before a backend save can consume it.
        if any(mark.mark_id == chosen_selection for mark in result.records):
            object.__setattr__(result, "selected_mark_id", chosen_selection)
        return result

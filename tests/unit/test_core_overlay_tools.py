"""Unit evidence for the toolkit-free PWS-0304 keyboard editing facade."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.app.core_overlay_tools import (
    CORE_OVERLAY_TOOL_DESCRIPTORS,
    CoreOverlayToolsState,
    KeyboardDirection,
    core_overlay_record_summary,
    core_overlay_tool_descriptor,
)
from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.overlays.models import (
    MarkOverlayPayload,
    OverlayError,
    OverlayErrorCode,
    OverlayKind,
    TextualOverlayPayload,
)

ROOT = Path(__file__).resolve().parents[2]
REPRESENTATIVE = ROOT / "schemas" / "examples" / "case-manifest-v2-representative.json"
CASE_ID = UUID("10000000-0000-4000-8000-000000000002")
PAGE_ONE = UUID("30000000-0000-4000-8000-000000000001")
PAGE_THREE = UUID("30000000-0000-4000-8000-000000000003")


def _document() -> dict[str, object]:
    loaded = json.loads(REPRESENTATIVE.read_bytes())
    assert type(loaded) is dict
    return cast(dict[str, object], loaded)


def _snapshot(document: dict[str, object] | None = None) -> ManifestSnapshot:
    selected = _document() if document is None else document
    revision = cast(int, selected["revision"])
    return ManifestSnapshot(
        CASE_ID,
        cast(int, selected["schema_version"]),
        revision,
        selected,
        "b" * 64,
        f"core_overlay_tools_{revision}",
    )


def test_descriptors_cover_five_tools_with_content_free_accessibility_metadata() -> None:
    assert tuple(descriptor.kind for descriptor in CORE_OVERLAY_TOOL_DESCRIPTORS) == (
        OverlayKind.TEXT,
        OverlayKind.DATE,
        OverlayKind.CHECKMARK,
        OverlayKind.CROSS,
        OverlayKind.INITIALS,
    )
    assert len({descriptor.accessible_name for descriptor in CORE_OVERLAY_TOOL_DESCRIPTORS}) == 5
    for descriptor in CORE_OVERLAY_TOOL_DESCRIPTORS:
        assert core_overlay_tool_descriptor(descriptor.kind) is descriptor
        assert descriptor.label
        assert descriptor.accessible_name.endswith("visual mark")
        assert descriptor.default_width > 0
        assert descriptor.default_height > 0
    assert "does not reformat" in cast(
        str,
        core_overlay_tool_descriptor(OverlayKind.DATE).value_description,
    )


def test_keyboard_facade_creates_moves_resizes_edits_deletes_and_replays_exact_date() -> None:
    canary = " 31.VIII.2032 -- $(PRIVATE_DATE_CANARY) \n"
    overlay_id = UUID("6f100000-0000-4000-8000-000000000001")
    state = CoreOverlayToolsState.from_snapshot(_snapshot(), PAGE_ONE)
    baseline = state.history.overlays

    created = state.create(OverlayKind.DATE, value=canary, id_factory=lambda: overlay_id)
    assert created.selected_overlay_id == overlay_id
    record = created.selected_record
    assert record is not None and type(record.payload) is TextualOverlayPayload
    assert record.payload.value == canary
    assert canary not in repr(created)
    summary = core_overlay_record_summary(
        record,
        created.records.index(record) + 1,
        len(created.records),
    )
    assert "Date visual mark" in summary
    assert canary not in summary

    moved = created.move_selected(KeyboardDirection.RIGHT)
    assert moved.selected_record is not None
    assert moved.selected_record.box.x == record.box.x + 10_000
    resized = moved.resize_selected(KeyboardDirection.DOWN, fine=True)
    assert resized.selected_record is not None
    assert resized.selected_record.box.height == record.box.height + 1_000
    edited_value = "exact display date / 08-31-2032"
    edited = resized.edit_selected(
        value=edited_value,
        font_size_points=18,
        color_value="#123456ff",
    )
    edited_record = edited.selected_record
    assert edited_record is not None and type(edited_record.payload) is TextualOverlayPayload
    assert edited_record.payload.value == edited_value
    assert edited_record.payload.font_size_points == 18
    assert edited_record.payload.color.to_manifest() == "#123456ff"

    exact_box = NormalizedPageBox(1, 2, 300_000, 40_000)
    positioned = edited.replace_selected_box(exact_box)
    assert positioned.selected_record is not None
    assert positioned.selected_record.box == exact_box
    deleted = positioned.delete_selected()
    assert deleted.selected_record is None
    assert overlay_id not in {item.overlay_id for item in deleted.history.overlays}

    replay = deleted
    while replay.can_undo:
        replay = replay.undo()
    assert replay.history.overlays == baseline
    while replay.can_redo:
        replay = replay.redo()
    assert replay.history.overlays == deleted.history.overlays


@pytest.mark.parametrize("kind", tuple(OverlayKind)[0:5])
def test_every_core_tool_has_a_keyboard_create_edit_and_remove_path(kind: OverlayKind) -> None:
    identifier = UUID(f"6f200000-0000-4000-8000-{list(OverlayKind).index(kind) + 1:012x}")
    initial = CoreOverlayToolsState.from_snapshot(_snapshot(), PAGE_THREE)
    value = (
        "opaque field value"
        if kind
        in {
            OverlayKind.TEXT,
            OverlayKind.DATE,
            OverlayKind.INITIALS,
        }
        else None
    )
    created = initial.create(kind, value=value, id_factory=lambda: identifier)
    moved = created.move_selected(KeyboardDirection.DOWN)
    resized = moved.resize_selected(KeyboardDirection.RIGHT)
    if kind in {OverlayKind.CHECKMARK, OverlayKind.CROSS}:
        edited = resized.edit_selected(color_value="#abcdef80")
        assert edited.selected_record is not None
        assert type(edited.selected_record.payload) is MarkOverlayPayload
    else:
        edited = resized.edit_selected(value="replacement", font_size_points=14)
        assert edited.selected_record is not None
        assert type(edited.selected_record.payload) is TextualOverlayPayload
    assert edited.delete_selected().selected_overlay_id is None


def test_selection_and_page_edges_fail_closed_without_changing_state() -> None:
    state = CoreOverlayToolsState.from_snapshot(_snapshot(), PAGE_ONE)
    with pytest.raises(OverlayError) as no_selection:
        state.move_selected(KeyboardDirection.LEFT)
    assert no_selection.value.code is OverlayErrorCode.SELECTION_REQUIRED

    existing = state.records[0]
    selected = state.select(existing.overlay_id)
    edge = selected.replace_selected_box(
        NormalizedPageBox(0, 0, existing.box.width, existing.box.height)
    )
    with pytest.raises(OverlayError) as outside:
        edge.move_selected(KeyboardDirection.LEFT)
    assert outside.value.code is OverlayErrorCode.BOX_INVALID
    assert edge.selected_record is not None
    assert edge.selected_record.box.x == 0


def test_candidate_is_exact_revision_bound_and_preserves_every_unrelated_member() -> None:
    origin = _snapshot()
    editor = CoreOverlayToolsState.from_snapshot(origin, PAGE_ONE).create(
        OverlayKind.INITIALS,
        value="XY",
        id_factory=lambda: UUID("6f300000-0000-4000-8000-000000000001"),
    )
    candidate = editor.to_candidate(origin)
    original = origin.document
    assert candidate["overlays"] != original["overlays"]
    assert {key: value for key, value in candidate.items() if key != "overlays"} == {
        key: value for key, value in original.items() if key != "overlays"
    }

    stale_document = copy.deepcopy(original)
    stale_document["revision"] = origin.revision + 1
    with pytest.raises(OverlayError) as stale:
        editor.to_candidate(_snapshot(stale_document))
    assert stale.value.code is OverlayErrorCode.CONCURRENT_CHANGE


def test_inactive_page_and_non_core_selection_are_rejected_by_editor_boundary() -> None:
    document = _document()
    sequence = cast(list[str], document["page_sequence"])
    sequence.remove(str(PAGE_THREE))
    with pytest.raises(OverlayError) as inactive:
        CoreOverlayToolsState.from_snapshot(_snapshot(document), PAGE_THREE)
    assert inactive.value.code is OverlayErrorCode.PAGE_UNKNOWN

    state = CoreOverlayToolsState.from_snapshot(_snapshot(), PAGE_ONE)
    shape = next(record for record in state.history.overlays if record.kind is OverlayKind.SHAPE)
    with pytest.raises(OverlayError) as unsupported:
        state.select(shape.overlay_id)
    assert unsupported.value.code is OverlayErrorCode.RECORD_UNKNOWN


def test_editor_rejects_noncanonical_presentation_announcement() -> None:
    state = CoreOverlayToolsState.from_snapshot(_snapshot(), PAGE_ONE)

    with pytest.raises(OverlayError) as rejected:
        replace(state, announcement="PRIVATE_PATH_OR_MARKUP_<script>C:\\Applicant")

    assert rejected.value.code is OverlayErrorCode.MODEL_INVALID

"""Unit and generated-history evidence for the pure PWS-0304 commands."""

from __future__ import annotations

import copy
import json
import random
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.domain.manifest_repository import (
    MAX_SAFE_MANIFEST_INTEGER,
    ManifestSnapshot,
)
from paperworkshell.domain.page_coordinates import NormalizedPageBox
from paperworkshell.overlays.commands import (
    CORE_OVERLAY_KINDS,
    DEFAULT_CORE_COLOR_VALUE,
    DEFAULT_CORE_FONT_SIZE_POINTS,
    CreateCoreOverlayCommand,
    DeleteOverlayCommand,
    OverlayCommandHistory,
    ReplaceCoreOverlayPayloadCommand,
    ReplaceOverlayBoxCommand,
    default_core_overlay_payload,
)
from paperworkshell.overlays.models import (
    DEFAULT_FONT_TOKEN,
    MarkOverlayPayload,
    OverlayError,
    OverlayErrorCode,
    OverlayKind,
    RgbaColor,
    TextualOverlayPayload,
)

ROOT = Path(__file__).resolve().parents[2]
REPRESENTATIVE = ROOT / "schemas" / "examples" / "case-manifest-v2-representative.json"
PAGE_ONE = UUID("30000000-0000-4000-8000-000000000001")
PAGE_THREE = UUID("30000000-0000-4000-8000-000000000003")
BOX = NormalizedPageBox(50_000, 60_000, 200_000, 80_000)


def _document() -> dict[str, object]:
    loaded = json.loads(REPRESENTATIVE.read_bytes())
    assert type(loaded) is dict
    return cast(dict[str, object], loaded)


def _snapshot(document: dict[str, object] | None = None) -> ManifestSnapshot:
    selected = _document() if document is None else document
    case_id = selected["case_id"]
    revision = selected["revision"]
    schema_version = selected["schema_version"]
    assert type(case_id) is str
    assert type(revision) is int
    assert type(schema_version) is int
    return ManifestSnapshot(
        UUID(case_id),
        schema_version,
        revision,
        selected,
        "a" * 64,
        "synthetic-overlay-command-token",
    )


def _published(candidate: dict[str, object], revision: int) -> ManifestSnapshot:
    document = copy.deepcopy(candidate)
    document["revision"] = revision
    return _snapshot(document)


def _overlay_id(index: int) -> UUID:
    return UUID(f"6f000000-0000-4000-8000-{index:012x}")


def _assert_error(code: OverlayErrorCode, action: object) -> OverlayError:
    assert callable(action)
    with pytest.raises(OverlayError) as raised:
        action()
    assert raised.value.code is code
    return raised.value


def test_default_factory_covers_exactly_five_core_kinds_and_preserves_raw_dates() -> None:
    assert CORE_OVERLAY_KINDS == {
        OverlayKind.TEXT,
        OverlayKind.DATE,
        OverlayKind.CHECKMARK,
        OverlayKind.CROSS,
        OverlayKind.INITIALS,
    }
    raw_date = " 24. VIII. 2031 / user display \n"
    values = {
        OverlayKind.TEXT: "Exact text",
        OverlayKind.DATE: raw_date,
        OverlayKind.INITIALS: "QZ",
    }
    for kind in CORE_OVERLAY_KINDS:
        payload = default_core_overlay_payload(kind, value=values.get(kind))
        assert payload.kind is kind
        assert payload.color.to_manifest() == DEFAULT_CORE_COLOR_VALUE
        if type(payload) is TextualOverlayPayload:
            assert payload.value == values[kind]
            assert payload.font_token == DEFAULT_FONT_TOKEN
            assert payload.font_size_points == DEFAULT_CORE_FONT_SIZE_POINTS
        else:
            assert type(payload) is MarkOverlayPayload

    assert (
        cast(
            TextualOverlayPayload,
            default_core_overlay_payload(OverlayKind.TEXT),
        ).value
        == ""
    )
    _assert_error(
        OverlayErrorCode.EDIT_KIND_MISMATCH,
        lambda: default_core_overlay_payload(OverlayKind.CROSS, value="not-mark-text"),
    )
    _assert_error(
        OverlayErrorCode.CORE_TOOL_UNSUPPORTED,
        lambda: default_core_overlay_payload(OverlayKind.SIGNATURE_IMAGE),
    )


def test_each_core_kind_creates_with_exact_defaults_and_monotonic_top_z() -> None:
    history = OverlayCommandHistory.from_snapshot(_snapshot())
    baseline_count = len(history.overlays)
    ids = iter(_overlay_id(index) for index in range(1, 6))
    for kind in (
        OverlayKind.TEXT,
        OverlayKind.DATE,
        OverlayKind.CHECKMARK,
        OverlayKind.CROSS,
        OverlayKind.INITIALS,
    ):
        value = (
            "raw-value"
            if kind in {OverlayKind.TEXT, OverlayKind.DATE, OverlayKind.INITIALS}
            else None
        )
        history = history.execute(
            CreateCoreOverlayCommand(kind, PAGE_ONE, BOX, value),
            id_factory=ids.__next__,
        )

    created = history.overlays[baseline_count:]
    assert tuple(record.kind for record in created) == (
        OverlayKind.TEXT,
        OverlayKind.DATE,
        OverlayKind.CHECKMARK,
        OverlayKind.CROSS,
        OverlayKind.INITIALS,
    )
    # Representative page one begins at z=0..4; new records remain on top.
    assert tuple(record.z_index for record in created) == (5, 6, 7, 8, 9)
    assert all(record.box == BOX and record.page_id == PAGE_ONE for record in created)


def test_create_replace_box_replace_payload_delete_undo_and_redo_are_exact() -> None:
    initial = OverlayCommandHistory.from_snapshot(_snapshot())
    overlay_id = _overlay_id(20)
    created = initial.execute(
        CreateCoreOverlayCommand(OverlayKind.DATE, PAGE_THREE, BOX, "31/02/never-normalized"),
        id_factory=lambda: overlay_id,
    )
    moved_box = NormalizedPageBox(100_000, 110_000, 250_000, 90_000)
    moved = created.execute(ReplaceOverlayBoxCommand(overlay_id, moved_box))
    replacement = TextualOverlayPayload(
        OverlayKind.DATE,
        "display exactly: someday",
        DEFAULT_FONT_TOKEN,
        17,
        RgbaColor("#123456ff"),
    )
    edited = moved.execute(ReplaceCoreOverlayPayloadCommand(overlay_id, replacement))
    deleted = edited.execute(DeleteOverlayCommand(overlay_id))

    states = (initial.overlays, created.overlays, moved.overlays, edited.overlays, deleted.overlays)
    history = deleted
    for expected in reversed(states[:-1]):
        history = history.undo()
        assert history.overlays == expected
    assert not history.can_undo and history.can_redo
    for expected in states[1:]:
        history = history.redo()
        assert history.overlays == expected
    assert not history.can_redo
    assert history.overlays == deleted.overlays


def test_no_op_replacements_do_not_consume_history() -> None:
    history = OverlayCommandHistory.from_snapshot(_snapshot())
    existing = history.record_for(UUID("60000000-0000-4000-8000-000000000001"))

    same_box = history.execute(ReplaceOverlayBoxCommand(existing.overlay_id, existing.box))
    same_payload = history.execute(
        ReplaceCoreOverlayPayloadCommand(
            existing.overlay_id,
            cast(TextualOverlayPayload, existing.payload),
        )
    )

    assert same_box is history
    assert same_payload is history
    assert not history.can_undo


def test_loaded_records_allow_geometry_only_edits_but_reject_non_core_deletion() -> None:
    history = OverlayCommandHistory.from_snapshot(_snapshot())
    _assert_error(OverlayErrorCode.UNDO_UNAVAILABLE, history.undo)
    _assert_error(OverlayErrorCode.REDO_UNAVAILABLE, history.redo)
    shape_id = UUID("60000000-0000-4000-8000-000000000008")
    before = history.record_for(shape_id)
    moved = history.execute(ReplaceOverlayBoxCommand(shape_id, BOX))
    after = moved.record_for(shape_id)
    assert after.box == BOX
    assert after.payload == before.payload
    assert after.z_index == before.z_index
    assert moved.undo().overlays == history.overlays
    _assert_error(
        OverlayErrorCode.CORE_TOOL_UNSUPPORTED,
        lambda: moved.execute(DeleteOverlayCommand(shape_id)),
    )
    signature_id = UUID("60000000-0000-4000-8000-000000000002")
    _assert_error(
        OverlayErrorCode.CORE_TOOL_UNSUPPORTED,
        lambda: history.execute(DeleteOverlayCommand(signature_id)),
    )


def test_branch_after_undo_clears_redo_and_never_reuses_discarded_id() -> None:
    first_id = _overlay_id(30)
    second_id = _overlay_id(31)
    history = OverlayCommandHistory.from_snapshot(_snapshot()).execute(
        CreateCoreOverlayCommand(OverlayKind.CHECKMARK, PAGE_ONE, BOX),
        id_factory=lambda: first_id,
    )
    undone = history.undo()
    candidates = iter((first_id, second_id))
    branched = undone.execute(
        CreateCoreOverlayCommand(OverlayKind.CROSS, PAGE_ONE, BOX),
        id_factory=candidates.__next__,
    )

    assert branched.overlays[-1].overlay_id == second_id
    assert not branched.can_redo
    _assert_error(OverlayErrorCode.REDO_UNAVAILABLE, branched.redo)


def test_create_fails_closed_when_top_z_is_not_representable() -> None:
    document = _document()
    overlays = cast(list[dict[str, object]], document["overlays"])
    overlays[0]["z_index"] = MAX_SAFE_MANIFEST_INTEGER
    history = OverlayCommandHistory.from_snapshot(_snapshot(document))

    _assert_error(
        OverlayErrorCode.Z_INDEX_INVALID,
        lambda: history.execute(
            CreateCoreOverlayCommand(OverlayKind.TEXT, PAGE_ONE, BOX),
            id_factory=lambda: _overlay_id(40),
        ),
    )


def test_candidate_replaces_only_overlays_and_accepts_saved_undo_redo_states() -> None:
    origin = _snapshot()
    history = OverlayCommandHistory.from_snapshot(origin).execute(
        CreateCoreOverlayCommand(OverlayKind.INITIALS, PAGE_ONE, BOX, "AB"),
        id_factory=lambda: _overlay_id(50),
    )
    origin_document = origin.document
    candidate = history.to_candidate(origin)

    assert candidate["overlays"] != origin_document["overlays"]
    assert {key: value for key, value in candidate.items() if key != "overlays"} == {
        key: value for key, value in origin_document.items() if key != "overlays"
    }
    published = _published(candidate, origin.revision + 1)
    undone = history.undo()
    undo_candidate = undone.to_candidate(published)
    assert undo_candidate["overlays"] == origin_document["overlays"]
    assert undo_candidate["revision"] == origin.revision + 1
    redone = undone.redo()
    assert redone.to_candidate(published)["overlays"] == candidate["overlays"]

    reloaded = OverlayCommandHistory.from_snapshot(_snapshot(candidate))
    assert reloaded.overlays == history.overlays
    assert not reloaded.can_undo and not reloaded.can_redo


def test_candidate_rejects_unknown_overlay_state_and_latest_namespace_collision() -> None:
    origin = _snapshot()
    generated_id = _overlay_id(60)
    history = OverlayCommandHistory.from_snapshot(origin).execute(
        CreateCoreOverlayCommand(OverlayKind.TEXT, PAGE_ONE, BOX, "private"),
        id_factory=lambda: generated_id,
    )
    concurrent_document = origin.document
    overlays = cast(list[dict[str, object]], concurrent_document["overlays"])
    payload = cast(dict[str, object], overlays[0]["payload"])
    payload["value"] = "other exact valid state"
    concurrent = _published(concurrent_document, origin.revision + 1)
    _assert_error(
        OverlayErrorCode.CONCURRENT_CHANGE,
        lambda: history.to_candidate(concurrent),
    )

    namespace_document = origin.document
    assignments = cast(list[dict[str, object]], namespace_document["assignments"])
    assignments[0]["assignment_id"] = str(generated_id)
    namespace_current = _published(namespace_document, origin.revision + 1)
    _assert_error(
        OverlayErrorCode.CONCURRENT_CHANGE,
        lambda: history.to_candidate(namespace_current),
    )


def test_complete_manifest_namespace_is_reserved_for_generated_ids() -> None:
    document = _document()
    reserved = (
        UUID(cast(str, document["case_id"])),
        UUID("20000000-0000-4000-8000-000000000001"),
        PAGE_ONE,
        UUID("50000000-0000-4000-8000-000000000001"),
        UUID("90000000-0000-4000-8000-000000000001"),
        UUID("40000000-0000-4000-8000-000000000001"),
        UUID("60000000-0000-4000-8000-000000000001"),
        UUID("70000000-0000-4000-8000-000000000001"),
        UUID("a0000000-0000-4000-8000-000000000001"),
        UUID("80000000-0000-4000-8000-000000000001"),
    )

    def assert_reserved(identifier: UUID) -> None:
        calls = 0

        def collide() -> UUID:
            nonlocal calls
            calls += 1
            return identifier

        history = OverlayCommandHistory.from_snapshot(_snapshot(document))
        _assert_error(
            OverlayErrorCode.ID_GENERATION_FAILED,
            lambda: history.execute(
                CreateCoreOverlayCommand(OverlayKind.TEXT, PAGE_ONE, BOX),
                id_factory=collide,
            ),
        )
        assert calls == 32

    for identifier in reserved:
        assert_reserved(identifier)


def test_fixed_seed_box_edit_histories_are_deterministic_and_fully_reversible() -> None:
    seed = 30_404
    generated = random.Random(seed)
    commands: list[ReplaceOverlayBoxCommand] = []
    overlay_id = UUID("60000000-0000-4000-8000-000000000001")
    for _ in range(64):
        x = generated.randrange(0, 700_000)
        y = generated.randrange(0, 850_000)
        commands.append(
            ReplaceOverlayBoxCommand(
                overlay_id,
                NormalizedPageBox(x, y, min(250_000, 1_000_000 - x), 50_000),
            )
        )

    first = OverlayCommandHistory.from_snapshot(_snapshot())
    second = OverlayCommandHistory.from_snapshot(_snapshot())
    baseline = first.overlays
    for command in commands:
        first = first.execute(command)
        second = second.execute(command)
    assert first.overlays == second.overlays

    final = first.overlays
    undo_count = 0
    while first.can_undo:
        first = first.undo()
        undo_count += 1
    assert first.overlays == baseline
    for _ in range(undo_count):
        first = first.redo()
    assert first.overlays == final

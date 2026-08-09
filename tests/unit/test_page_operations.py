from __future__ import annotations

import copy
import json
import random
from collections.abc import Callable
from itertools import combinations, permutations
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.page_operations import (
    MAX_ID_GENERATION_ATTEMPTS,
    MAX_PAGE_COUNT,
    MAX_PAGE_OPERATION_COUNT,
    ChangeSequencePayload,
    CropPayload,
    NormalizedPageBox,
    PageCommandHistory,
    PageOperation,
    PageOperationError,
    PageOperationErrorCode,
    PageReference,
    RotatePageCommand,
    RotatePayload,
    SequenceChangeCommand,
    SequenceChangeReason,
    SetGroupCommand,
    SetGroupPayload,
    replay_page_operations,
)

CASE_ID = UUID("10000000-0000-4000-8000-000000000010")
SOURCE_ID = UUID("20000000-0000-4000-8000-000000000010")
PAGE_IDS = (
    UUID("30000000-0000-4000-8000-000000000010"),
    UUID("30000000-0000-4000-8000-000000000011"),
    UUID("30000000-0000-4000-8000-000000000012"),
)
GROUP_ID = UUID("90000000-0000-4000-8000-000000000010")
REPRESENTATIVE_MANIFEST = (
    Path(__file__).parents[2] / "schemas" / "examples" / "case-manifest-v1-representative.json"
)


def _operation_id(index: int) -> UUID:
    return UUID(f"5{index:07x}-0000-4000-8000-000000000010")


def _constant_id_factory(identifier: UUID) -> Callable[[], UUID]:
    def create() -> UUID:
        return identifier

    return create


def _page_id(index: int) -> UUID:
    return UUID(f"3{index:07x}-0000-4000-8000-000000000010")


def _references(count: int = 3) -> tuple[PageReference, ...]:
    return tuple(
        PageReference(page_id, SOURCE_ID, index) for index, page_id in enumerate(PAGE_IDS[:count])
    )


def _operation(
    sequence: int,
    payload: RotatePayload | CropPayload | ChangeSequencePayload | SetGroupPayload,
    *,
    operation_id: UUID | None = None,
) -> PageOperation:
    return PageOperation(
        operation_id=operation_id or _operation_id(sequence),
        sequence=sequence,
        payload=payload,
    )


def _clean_document(*, page_count: int = 3, revision: int = 0) -> dict[str, object]:
    selected_pages = PAGE_IDS[:page_count]
    return {
        "schema_version": 1,
        "case_id": str(CASE_ID),
        "revision": revision,
        "created_at": "2026-08-03T10:00:00Z",
        "updated_at": "2026-08-03T10:00:00Z",
        "template_ref": None,
        "sources": [
            {
                "source_id": str(SOURCE_ID),
                "display_name": "Synthetic pages.pdf",
                "storage_name": f"{SOURCE_ID}.bin",
                "detected_media_type": "application/pdf",
                "byte_size": 1024,
                "page_count": page_count,
                "sha256": "a" * 64,
                "signature_state": "none_detected",
                "encryption_state": "unencrypted",
                "import_state": "complete",
                "imported_at": "2026-08-03T10:00:00Z",
            }
        ],
        "pages": [
            {
                "page_id": str(page_id),
                "source_id": str(SOURCE_ID),
                "source_page_index": index,
            }
            for index, page_id in enumerate(selected_pages)
        ],
        "page_sequence_base": [str(page_id) for page_id in selected_pages],
        "page_sequence": [str(page_id) for page_id in selected_pages],
        "page_operations": [],
        "assets": [],
        "overlays": [],
        "assignments": [],
        "constraints": {
            "constraints_version": 1,
            "allowed_output_modes": ["combined_pdf"],
            "encryption_policy": "must_be_unencrypted",
        },
        "confirmations": [],
        "export_history": {"storage_name": "export-history.json", "entries": []},
    }


def _snapshot(document: dict[str, object] | None = None) -> ManifestSnapshot:
    selected = _clean_document() if document is None else document
    revision = cast(int, selected["revision"])
    return ManifestSnapshot(
        CASE_ID,
        1,
        revision,
        selected,
        f"{revision + 1:064x}",
        f"page_operation_cas_{revision:016d}",
    )


def _published_snapshot(
    candidate: dict[str, object],
    *,
    revision: int,
) -> ManifestSnapshot:
    published = copy.deepcopy(candidate)
    published["revision"] = revision
    published["updated_at"] = f"2026-08-03T10:{revision:02d}:00Z"
    return _snapshot(published)


def _assert_error_code(
    code: PageOperationErrorCode,
    action: Callable[[], object],
) -> PageOperationError:
    with pytest.raises(PageOperationError) as raised:
        action()
    assert raised.value.code is code
    return raised.value


def test_representative_manifest_replays_rotation_crop_removal_reorder_and_group() -> None:
    document = cast(dict[str, object], json.loads(REPRESENTATIVE_MANIFEST.read_bytes()))
    representative_case_id = UUID(cast(str, document["case_id"]))
    snapshot = ManifestSnapshot(
        representative_case_id,
        1,
        cast(int, document["revision"]),
        document,
        "f" * 64,
        "representative_page_operations",
    )

    history = PageCommandHistory.from_snapshot(snapshot)
    page_one = UUID("30000000-0000-4000-8000-000000000001")
    page_two = UUID("30000000-0000-4000-8000-000000000002")
    page_three = UUID("30000000-0000-4000-8000-000000000003")
    group_id = UUID("90000000-0000-4000-8000-000000000001")

    assert history.projection.page_sequence == (page_three, page_one)
    assert history.projection.state_for(page_two).active is False
    assert history.projection.state_for(page_three).quarter_turns_clockwise == 1
    assert history.projection.state_for(page_three).crop == NormalizedPageBox(
        x=50_000,
        y=50_000,
        width=900_000,
        height=900_000,
    )
    assert history.projection.group_for(group_id).page_ids == (page_three, page_one)
    assert history.projection.state_for(page_three).group_ids == (group_id,)
    assert len(history.operations) == 5
    assert not history.can_undo
    assert not history.can_redo


def test_replay_accepts_every_v1_sequence_reason_in_one_deterministic_history() -> None:
    page_one, page_two, page_three = PAGE_IDS
    operations = (
        _operation(
            1,
            ChangeSequencePayload(
                SequenceChangeReason.IMPORT,
                (page_one, page_two),
                (page_one, page_three, page_two),
            ),
        ),
        _operation(
            2,
            ChangeSequencePayload(
                SequenceChangeReason.REORDER,
                (page_one, page_three, page_two),
                (page_three, page_one, page_two),
            ),
        ),
        _operation(
            3,
            ChangeSequencePayload(
                SequenceChangeReason.SPLIT,
                (page_three, page_one, page_two),
                (page_three, page_two, page_one),
            ),
        ),
        _operation(
            4,
            ChangeSequencePayload(
                SequenceChangeReason.COMBINE,
                (page_three, page_two, page_one),
                (page_one, page_two, page_three),
            ),
        ),
        _operation(
            5,
            ChangeSequencePayload(
                SequenceChangeReason.REMOVE,
                (page_one, page_two, page_three),
                (page_one, page_three),
            ),
        ),
        _operation(
            6,
            ChangeSequencePayload(
                SequenceChangeReason.RESTORE,
                (page_one, page_three),
                (page_two, page_one, page_three),
            ),
        ),
    )

    first = replay_page_operations(
        _references(),
        (page_one, page_two),
        operations,
        final_sequence=(page_two, page_one, page_three),
    )
    second = replay_page_operations(
        _references(),
        (page_one, page_two),
        operations,
        final_sequence=(page_two, page_one, page_three),
    )

    assert first == second
    assert first.page_sequence == (page_two, page_one, page_three)
    _assert_error_code(
        PageOperationErrorCode.SEQUENCE_REASON_INVALID,
        lambda: SequenceChangeCommand(SequenceChangeReason.IMPORT, PAGE_IDS),
    )


@pytest.mark.parametrize(
    ("code", "action"),
    (
        (
            PageOperationErrorCode.PAGE_UNKNOWN,
            lambda: replay_page_operations(
                _references(2),
                (PAGE_IDS[0], UUID("30000000-0000-4000-8000-000000000099")),
                (),
            ),
        ),
        (
            PageOperationErrorCode.PAGE_DUPLICATE,
            lambda: replay_page_operations(
                (_references(2)[0], _references(2)[0]),
                (PAGE_IDS[0],),
                (),
            ),
        ),
        (
            PageOperationErrorCode.PAGE_DUPLICATE,
            lambda: replay_page_operations(
                _references(2),
                (PAGE_IDS[0], PAGE_IDS[0]),
                (),
            ),
        ),
        (
            PageOperationErrorCode.PAGE_UNKNOWN,
            lambda: replay_page_operations(
                _references(2),
                PAGE_IDS[:2],
                (
                    _operation(
                        1,
                        RotatePayload(
                            UUID("30000000-0000-4000-8000-000000000099"),
                            1,
                        ),
                    ),
                ),
            ),
        ),
        (
            PageOperationErrorCode.PAGE_NOT_INTRODUCED,
            lambda: replay_page_operations(
                _references(),
                PAGE_IDS[:2],
                (
                    _operation(1, RotatePayload(PAGE_IDS[2], 1)),
                    _operation(
                        2,
                        ChangeSequencePayload(
                            SequenceChangeReason.IMPORT,
                            PAGE_IDS[:2],
                            PAGE_IDS,
                        ),
                    ),
                ),
            ),
        ),
        (
            PageOperationErrorCode.PAGE_INTRODUCTION_INCOMPLETE,
            lambda: replay_page_operations(_references(), PAGE_IDS[:2], ()),
        ),
    ),
)
def test_replay_rejects_unknown_duplicate_not_introduced_and_missing_pages(
    code: PageOperationErrorCode,
    action: Callable[[], object],
) -> None:
    _assert_error_code(code, action)


def test_replay_rejects_operation_baseline_and_final_sequence_mismatches() -> None:
    operation = _operation(
        1,
        ChangeSequencePayload(
            SequenceChangeReason.REORDER,
            (PAGE_IDS[1], PAGE_IDS[0], PAGE_IDS[2]),
            PAGE_IDS,
        ),
    )
    _assert_error_code(
        PageOperationErrorCode.SEQUENCE_MISMATCH,
        lambda: replay_page_operations(_references(), PAGE_IDS, (operation,)),
    )

    valid = _operation(
        1,
        ChangeSequencePayload(
            SequenceChangeReason.REORDER,
            PAGE_IDS,
            (PAGE_IDS[2], PAGE_IDS[1], PAGE_IDS[0]),
        ),
    )
    _assert_error_code(
        PageOperationErrorCode.SEQUENCE_MISMATCH,
        lambda: replay_page_operations(
            _references(),
            PAGE_IDS,
            (valid,),
            final_sequence=PAGE_IDS,
        ),
    )


@pytest.mark.parametrize("turns", (-1, 4, True, False, 1.0, "1"))
def test_rotation_models_and_commands_reject_noncanonical_quarter_turns(turns: object) -> None:
    _assert_error_code(
        PageOperationErrorCode.ROTATION_INVALID,
        lambda: RotatePayload(PAGE_IDS[0], turns),  # type: ignore[arg-type]
    )
    _assert_error_code(
        PageOperationErrorCode.ROTATION_INVALID,
        lambda: RotatePageCommand(PAGE_IDS[0], turns),  # type: ignore[arg-type]
    )


def test_groups_reject_empty_duplicate_unknown_and_not_introduced_members() -> None:
    for action in (
        lambda: SetGroupPayload(GROUP_ID, ()),
        lambda: SetGroupCommand((), GROUP_ID),
    ):
        error = _assert_error_code(PageOperationErrorCode.GROUP_EMPTY, action)
        assert error.safe_message == "Select at least one page before creating a page group."
    _assert_error_code(
        PageOperationErrorCode.PAGE_DUPLICATE,
        lambda: SetGroupPayload(GROUP_ID, (PAGE_IDS[0], PAGE_IDS[0])),
    )
    unknown = UUID("30000000-0000-4000-8000-000000000099")
    _assert_error_code(
        PageOperationErrorCode.PAGE_UNKNOWN,
        lambda: replay_page_operations(
            _references(),
            PAGE_IDS,
            (_operation(1, SetGroupPayload(GROUP_ID, (unknown,))),),
        ),
    )
    _assert_error_code(
        PageOperationErrorCode.PAGE_NOT_INTRODUCED,
        lambda: replay_page_operations(
            _references(),
            PAGE_IDS[:2],
            (
                _operation(1, SetGroupPayload(GROUP_ID, (PAGE_IDS[2],))),
                _operation(
                    2,
                    ChangeSequencePayload(
                        SequenceChangeReason.IMPORT,
                        PAGE_IDS[:2],
                        PAGE_IDS,
                    ),
                ),
            ),
        ),
    )


def test_replay_rejects_operation_gaps_and_domain_namespace_collisions() -> None:
    gap = _operation(2, RotatePayload(PAGE_IDS[0], 1))
    _assert_error_code(
        PageOperationErrorCode.OPERATION_SEQUENCE_INVALID,
        lambda: replay_page_operations(_references(), PAGE_IDS, (gap,)),
    )

    duplicate_id = _operation_id(70)
    duplicate_operations = (
        _operation(1, RotatePayload(PAGE_IDS[0], 1), operation_id=duplicate_id),
        _operation(2, RotatePayload(PAGE_IDS[1], 1), operation_id=duplicate_id),
    )
    _assert_error_code(
        PageOperationErrorCode.IDENTIFIER_COLLISION,
        lambda: replay_page_operations(_references(), PAGE_IDS, duplicate_operations),
    )

    _assert_error_code(
        PageOperationErrorCode.IDENTIFIER_COLLISION,
        lambda: replay_page_operations(
            _references(),
            PAGE_IDS,
            (_operation(1, RotatePayload(PAGE_IDS[0], 1), operation_id=PAGE_IDS[1]),),
        ),
    )
    _assert_error_code(
        PageOperationErrorCode.IDENTIFIER_COLLISION,
        lambda: replay_page_operations(
            _references(),
            PAGE_IDS,
            (_operation(1, SetGroupPayload(SOURCE_ID, (PAGE_IDS[0],))),),
        ),
    )


def test_future_operation_versions_fail_closed_with_a_distinct_error() -> None:
    serialized = _operation(1, RotatePayload(PAGE_IDS[0], 1)).to_manifest()
    serialized["operation_version"] = 2

    _assert_error_code(
        PageOperationErrorCode.VERSION_UNSUPPORTED,
        lambda: PageOperation.from_manifest(serialized),
    )
    _assert_error_code(
        PageOperationErrorCode.VERSION_UNSUPPORTED,
        lambda: PageOperation(
            operation_id=_operation_id(71),
            operation_version=2,
            sequence=1,
            payload=RotatePayload(PAGE_IDS[0], 1),
        ),
    )


def test_rotations_accumulate_and_inactive_pages_retain_transform_and_group_intent() -> None:
    crop = NormalizedPageBox(x=10_000, y=20_000, width=700_000, height=600_000)
    operations = (
        _operation(1, RotatePayload(PAGE_IDS[1], 1)),
        _operation(
            2,
            ChangeSequencePayload(
                SequenceChangeReason.REMOVE,
                PAGE_IDS,
                (PAGE_IDS[0], PAGE_IDS[2]),
            ),
        ),
        _operation(3, RotatePayload(PAGE_IDS[1], 3)),
        _operation(4, CropPayload(PAGE_IDS[1], crop)),
        _operation(5, SetGroupPayload(GROUP_ID, (PAGE_IDS[1], PAGE_IDS[2]))),
    )

    projection = replay_page_operations(_references(), PAGE_IDS, operations)
    inactive = projection.state_for(PAGE_IDS[1])

    assert not inactive.active
    assert inactive.quarter_turns_clockwise == 0
    assert inactive.crop == crop
    assert inactive.group_ids == (GROUP_ID,)
    assert projection.group_for(GROUP_ID).page_ids == (PAGE_IDS[1], PAGE_IDS[2])


def test_history_undo_and_redo_are_exact_multi_step_lifo_transitions() -> None:
    initial = PageCommandHistory.from_snapshot(_snapshot())
    ids = iter((_operation_id(101), _operation_id(102), _operation_id(103)))
    commands = (
        SequenceChangeCommand(
            SequenceChangeReason.REORDER,
            (PAGE_IDS[2], PAGE_IDS[1], PAGE_IDS[0]),
        ),
        RotatePageCommand(PAGE_IDS[1], 2),
        SetGroupCommand((PAGE_IDS[2], PAGE_IDS[0]), GROUP_ID),
    )
    states = [initial]
    history = initial
    for command in commands:
        history = history.execute(command, id_factory=ids.__next__)
        states.append(history)

    final_operations = history.operations
    final_projection = history.projection
    for expected in reversed(states[:-1]):
        history = history.undo()
        assert history.operations == expected.operations
        assert history.projection == expected.projection
    assert not history.can_undo
    assert history.can_redo

    for expected in states[1:]:
        history = history.redo()
        assert history.operations == expected.operations
        assert history.projection == expected.projection
    assert history.operations == final_operations
    assert history.projection == final_projection
    assert not history.can_redo


def test_loaded_durable_operations_are_not_undoable_or_redoable() -> None:
    document = cast(dict[str, object], json.loads(REPRESENTATIVE_MANIFEST.read_bytes()))
    case_id = UUID(cast(str, document["case_id"]))
    snapshot = ManifestSnapshot(
        case_id,
        1,
        cast(int, document["revision"]),
        document,
        "f" * 64,
        "loaded_page_operation_baseline",
    )
    history = PageCommandHistory.from_snapshot(snapshot)

    assert len(history.operations) == 5
    assert not history.can_undo
    assert not history.can_redo
    _assert_error_code(PageOperationErrorCode.UNDO_UNAVAILABLE, history.undo)
    _assert_error_code(PageOperationErrorCode.REDO_UNAVAILABLE, history.redo)


def test_execute_after_undo_clears_redo_and_never_reuses_discarded_identifiers() -> None:
    first_id = _operation_id(111)
    replacement_id = _operation_id(112)
    history = PageCommandHistory.from_snapshot(_snapshot()).execute(
        RotatePageCommand(PAGE_IDS[0], 1),
        id_factory=lambda: first_id,
    )
    undone = history.undo()
    candidates = iter((first_id, replacement_id))

    branched = undone.execute(
        RotatePageCommand(PAGE_IDS[1], 2),
        id_factory=candidates.__next__,
    )

    assert branched.operations[-1].operation_id == replacement_id
    assert not branched.can_redo
    _assert_error_code(PageOperationErrorCode.REDO_UNAVAILABLE, branched.redo)


def test_candidate_preserves_unrelated_state_metadata_and_caller_owned_containers() -> None:
    caller_document = _clean_document(revision=9)
    snapshot = _snapshot(caller_document)
    caller_sequence = [PAGE_IDS[2], PAGE_IDS[0], PAGE_IDS[1]]
    command = SequenceChangeCommand(
        SequenceChangeReason.REORDER,
        caller_sequence,  # type: ignore[arg-type]
    )
    caller_sequence.reverse()
    history = PageCommandHistory.from_snapshot(snapshot).execute(
        command,
        id_factory=lambda: _operation_id(121),
    )
    before = snapshot.document

    candidate = history.to_candidate(snapshot)

    assert candidate["revision"] == 9
    assert candidate["created_at"] == before["created_at"]
    assert candidate["updated_at"] == before["updated_at"]
    assert candidate["page_sequence"] == [str(PAGE_IDS[2]), str(PAGE_IDS[0]), str(PAGE_IDS[1])]
    assert {
        key: value
        for key, value in candidate.items()
        if key not in {"page_operations", "page_sequence"}
    } == {
        key: value
        for key, value in before.items()
        if key not in {"page_operations", "page_sequence"}
    }

    cast(dict[str, object], candidate["constraints"])["constraints_version"] = 99
    cast(list[object], caller_document["pages"]).clear()
    assert cast(dict[str, object], snapshot.document["constraints"])["constraints_version"] == 1
    assert len(cast(list[object], snapshot.document["pages"])) == 3
    assert history.projection.page_sequence == (PAGE_IDS[2], PAGE_IDS[0], PAGE_IDS[1])


def test_owned_autosaved_suffix_accepts_followup_undo_redo_and_new_branch() -> None:
    origin = _snapshot()
    operation_one = _operation_id(131)
    operation_two = _operation_id(132)
    edited = PageCommandHistory.from_snapshot(origin).execute(
        SequenceChangeCommand(
            SequenceChangeReason.REORDER,
            (PAGE_IDS[2], PAGE_IDS[0], PAGE_IDS[1]),
        ),
        id_factory=lambda: operation_one,
    )
    published = _published_snapshot(edited.to_candidate(origin), revision=1)

    undone = edited.undo()
    undo_candidate = undone.to_candidate(published)
    assert undo_candidate["page_operations"] == []
    assert undo_candidate["page_sequence"] == [str(page_id) for page_id in PAGE_IDS]
    assert undo_candidate["revision"] == 1

    redone = undone.redo()
    redo_candidate = redone.to_candidate(published)
    assert redo_candidate["page_operations"] == edited.to_candidate(origin)["page_operations"]

    branched = undone.execute(
        RotatePageCommand(PAGE_IDS[0], 3),
        id_factory=lambda: operation_two,
    )
    branch_candidate = branched.to_candidate(published)
    branch_operations = cast(list[dict[str, object]], branch_candidate["page_operations"])
    assert len(branch_operations) == 1
    assert branch_operations[0]["operation_id"] == str(operation_two)
    assert not branched.can_redo


def test_unknown_concurrent_operation_suffix_is_rejected() -> None:
    origin = _snapshot()
    history = PageCommandHistory.from_snapshot(origin).execute(
        RotatePageCommand(PAGE_IDS[0], 1),
        id_factory=lambda: _operation_id(141),
    )
    concurrent_document = origin.document
    concurrent_operation = _operation(
        1, RotatePayload(PAGE_IDS[1], 2), operation_id=_operation_id(142)
    )
    concurrent_document["page_operations"] = [concurrent_operation.to_manifest()]
    concurrent = _published_snapshot(concurrent_document, revision=1)

    _assert_error_code(
        PageOperationErrorCode.CONCURRENT_CHANGE,
        lambda: history.to_candidate(concurrent),
    )


def test_concurrent_page_catalogue_and_import_change_is_rejected() -> None:
    origin_document = _clean_document(page_count=2)
    origin = _snapshot(origin_document)
    history = PageCommandHistory.from_snapshot(origin).execute(
        RotatePageCommand(PAGE_IDS[0], 1),
        id_factory=lambda: _operation_id(151),
    )
    concurrent_document = origin.document
    source = cast(dict[str, object], cast(list[object], concurrent_document["sources"])[0])
    source["page_count"] = 3
    cast(list[object], concurrent_document["pages"]).append(
        {
            "page_id": str(PAGE_IDS[2]),
            "source_id": str(SOURCE_ID),
            "source_page_index": 2,
        }
    )
    imported = _operation(
        1,
        ChangeSequencePayload(
            SequenceChangeReason.IMPORT,
            PAGE_IDS[:2],
            PAGE_IDS,
        ),
        operation_id=_operation_id(152),
    )
    concurrent_document["page_operations"] = [imported.to_manifest()]
    concurrent_document["page_sequence"] = [str(page_id) for page_id in PAGE_IDS]
    concurrent = _published_snapshot(concurrent_document, revision=1)

    _assert_error_code(
        PageOperationErrorCode.CONCURRENT_CHANGE,
        lambda: history.to_candidate(concurrent),
    )


@pytest.mark.parametrize("collision_target", ("operation", "group"))
def test_latest_unrelated_namespace_cannot_collide_with_session_domain_ids(
    collision_target: str,
) -> None:
    origin = _snapshot()
    operation_id = _operation_id(161)
    group_id = GROUP_ID
    if collision_target == "operation":
        history = PageCommandHistory.from_snapshot(origin).execute(
            RotatePageCommand(PAGE_IDS[0], 1),
            id_factory=lambda: operation_id,
        )
        collision_id = operation_id
    else:
        history = PageCommandHistory.from_snapshot(origin).execute(
            SetGroupCommand((PAGE_IDS[0], PAGE_IDS[1]), group_id),
            id_factory=lambda: operation_id,
        )
        collision_id = group_id
    concurrent_document = origin.document
    concurrent_document["overlays"] = [
        {
            "overlay_id": str(collision_id),
            "overlay_version": 1,
            "page_id": str(PAGE_IDS[0]),
            "box": {
                "coordinate_space": "normalized_page_millionths_v1",
                "x": 0,
                "y": 0,
                "width": 10_000,
                "height": 10_000,
            },
            "z_index": 0,
            "payload": {
                "kind": "highlight",
                "shape": "rectangle",
                "fill_color": "#00000000",
                "stroke_color": "#000000ff",
                "intent": "visual_overlay_only_not_permanent_removal",
            },
        }
    ]
    concurrent = _published_snapshot(concurrent_document, revision=1)

    _assert_error_code(
        PageOperationErrorCode.CONCURRENT_CHANGE,
        lambda: history.to_candidate(concurrent),
    )


def test_errors_hide_manifest_values_identifiers_paths_and_raw_factory_failures() -> None:
    canary = "private-applicant-name-C:/private/case.pdf"
    private_id = UUID("30000000-0000-4000-8000-000000000099")
    document = _clean_document()
    cast(dict[str, object], cast(list[object], document["sources"])[0])["display_name"] = canary
    document["page_sequence"] = [str(private_id)]

    error = _assert_error_code(
        PageOperationErrorCode.PAGE_UNKNOWN,
        lambda: PageCommandHistory.from_snapshot(_snapshot(document)),
    )
    for rendered in (str(error), repr(error), repr(error.args)):
        assert canary not in rendered
        assert str(private_id) not in rendered
        assert "case.pdf" not in rendered

    closed = PageOperationError(
        code=canary,  # type: ignore[arg-type]
        context=canary,
    )
    assert closed.code is PageOperationErrorCode.MODEL_INVALID
    assert closed.context == "page.operation"
    assert canary not in str(closed)
    assert canary not in repr(closed)


def test_uuid_factory_stops_after_bounded_collision_and_exception_attempts() -> None:
    history = PageCommandHistory.from_snapshot(_snapshot())
    attempts = 0

    def exhausted_factory() -> UUID:
        nonlocal attempts
        attempts += 1
        if attempts % 2:
            raise RuntimeError("private-applicant-factory-value")
        return PAGE_IDS[0]

    error = _assert_error_code(
        PageOperationErrorCode.ID_GENERATION_FAILED,
        lambda: history.execute(
            RotatePageCommand(PAGE_IDS[0], 1),
            id_factory=exhausted_factory,
        ),
    )

    assert attempts == MAX_ID_GENERATION_ATTEMPTS
    assert "private-applicant-factory-value" not in str(error)
    assert "private-applicant-factory-value" not in repr(error)


def test_page_and_operation_capacity_boundaries_are_exact() -> None:
    pages = tuple(
        PageReference(_page_id(index), SOURCE_ID, index) for index in range(1, MAX_PAGE_COUNT + 1)
    )
    page_ids = tuple(page.page_id for page in pages)
    projection = replay_page_operations(pages, page_ids, ())
    assert len(projection.pages) == MAX_PAGE_COUNT

    over_capacity_pages = pages + (
        PageReference(_page_id(MAX_PAGE_COUNT + 1), SOURCE_ID, MAX_PAGE_COUNT),
    )
    _assert_error_code(
        PageOperationErrorCode.CAPACITY_EXCEEDED,
        lambda: replay_page_operations(over_capacity_pages, page_ids, ()),
    )

    single_page = (PageReference(PAGE_IDS[0], SOURCE_ID, 0),)
    operations = tuple(
        PageOperation(
            operation_id=_operation_id(index + 10_000),
            sequence=index,
            payload=RotatePayload(PAGE_IDS[0], 0),
        )
        for index in range(1, MAX_PAGE_OPERATION_COUNT + 1)
    )
    replayed = replay_page_operations(single_page, (PAGE_IDS[0],), operations)
    assert replayed.page_sequence == (PAGE_IDS[0],)
    over_capacity_operations = operations + (
        PageOperation(
            operation_id=_operation_id(MAX_PAGE_OPERATION_COUNT + 10_001),
            sequence=MAX_PAGE_OPERATION_COUNT + 1,
            payload=RotatePayload(PAGE_IDS[0], 0),
        ),
    )
    _assert_error_code(
        PageOperationErrorCode.CAPACITY_EXCEEDED,
        lambda: replay_page_operations(
            single_page,
            (PAGE_IDS[0],),
            over_capacity_operations,
        ),
    )


def test_all_three_page_permutations_replay_undo_redo_and_reload_equivalently() -> None:
    snapshot = _snapshot()
    for index, permutation in enumerate(permutations(PAGE_IDS), start=1):
        history = PageCommandHistory.from_snapshot(snapshot).execute(
            SequenceChangeCommand(SequenceChangeReason.REORDER, permutation),
            id_factory=_constant_id_factory(_operation_id(index + 20_000)),
        )
        replayed = replay_page_operations(
            _references(),
            PAGE_IDS,
            history.operations,
            final_sequence=permutation,
        )
        undone = history.undo()
        redone = undone.redo()
        reloaded = PageCommandHistory.from_snapshot(_snapshot(history.to_candidate(snapshot)))

        assert history.projection == replayed
        assert undone.projection.page_sequence == PAGE_IDS
        assert redone.operations == history.operations
        assert redone.projection == history.projection
        assert reloaded.operations == history.operations
        assert reloaded.projection == history.projection
        assert not reloaded.can_undo
        assert not reloaded.can_redo


def test_remove_restore_subset_and_superset_grid_obeys_order_invariants() -> None:
    snapshot = _snapshot()
    case_index = 0
    for survivor_count in range(len(PAGE_IDS)):
        for survivors in combinations(PAGE_IDS, survivor_count):
            for restored in permutations(PAGE_IDS):
                if tuple(page for page in restored if page in survivors) != survivors:
                    continue
                case_index += 1
                ids = iter(
                    (
                        _operation_id(30_000 + case_index * 2),
                        _operation_id(30_001 + case_index * 2),
                    )
                )
                removed = PageCommandHistory.from_snapshot(snapshot).execute(
                    SequenceChangeCommand(SequenceChangeReason.REMOVE, survivors),
                    id_factory=ids.__next__,
                )
                restored_history = removed.execute(
                    SequenceChangeCommand(SequenceChangeReason.RESTORE, restored),
                    id_factory=ids.__next__,
                )

                assert removed.projection.page_sequence == survivors
                assert restored_history.projection.page_sequence == restored
                assert restored_history.undo().projection == removed.projection
    assert case_index == 33


def _generated_commands(seed: int, count: int) -> tuple[object, ...]:
    generator = random.Random(seed)
    sequence = list(PAGE_IDS)
    group_id = UUID(f"9{seed:07x}-0000-4000-8000-000000000010")
    commands: list[object] = []
    for _ in range(count):
        action = generator.choice(
            ("rotate", "reorder", "remove", "restore", "split", "combine", "group")
        )
        inactive = [page_id for page_id in PAGE_IDS if page_id not in sequence]
        if action == "remove" and sequence:
            removed = generator.choice(sequence)
            sequence = [page_id for page_id in sequence if page_id != removed]
            commands.append(SequenceChangeCommand(SequenceChangeReason.REMOVE, tuple(sequence)))
        elif action == "restore" and inactive:
            restored = generator.choice(inactive)
            sequence.insert(generator.randrange(len(sequence) + 1), restored)
            commands.append(SequenceChangeCommand(SequenceChangeReason.RESTORE, tuple(sequence)))
        elif action in {"reorder", "split", "combine"}:
            generator.shuffle(sequence)
            reason = SequenceChangeReason(action)
            commands.append(SequenceChangeCommand(reason, tuple(sequence)))
        elif action == "group":
            size = generator.randint(1, len(PAGE_IDS))
            members = tuple(generator.sample(PAGE_IDS, size))
            commands.append(SetGroupCommand(members, group_id))
        else:
            commands.append(RotatePageCommand(generator.choice(PAGE_IDS), generator.randrange(4)))
    return tuple(commands)


@pytest.mark.parametrize("seed", (7, 19, 43, 71, 101, 211))
def test_fixed_seed_command_sequences_are_deterministic_reversible_and_serializable(
    seed: int,
) -> None:
    snapshot = _snapshot()
    commands = _generated_commands(seed, 32)
    identifiers = tuple(
        _operation_id(50_000 + seed * 100 + index) for index in range(len(commands))
    )
    first = PageCommandHistory.from_snapshot(snapshot)
    second = PageCommandHistory.from_snapshot(snapshot)
    for command, identifier in zip(commands, identifiers, strict=True):
        first = first.execute(
            cast(SequenceChangeCommand | RotatePageCommand | SetGroupCommand, command),
            id_factory=_constant_id_factory(identifier),
        )
        second = second.execute(
            cast(SequenceChangeCommand | RotatePageCommand | SetGroupCommand, command),
            id_factory=_constant_id_factory(identifier),
        )

    assert first.operations == second.operations
    assert first.projection == second.projection
    final_operations = first.operations
    final_projection = first.projection

    for _ in commands:
        first = first.undo()
    assert first.projection == PageCommandHistory.from_snapshot(snapshot).projection
    assert not first.can_undo
    for _ in commands:
        first = first.redo()
    assert first.operations == final_operations
    assert first.projection == final_projection
    assert not first.can_redo

    candidate = first.to_candidate(snapshot)
    reloaded = PageCommandHistory.from_snapshot(_snapshot(candidate))
    assert reloaded.operations == final_operations
    assert reloaded.projection == final_projection
    assert not reloaded.can_undo
    assert not reloaded.can_redo

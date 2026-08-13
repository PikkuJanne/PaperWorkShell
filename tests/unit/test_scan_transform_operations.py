"""Pure page-operation evidence for PWS-0202 scan-transform intent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.page_operations import (
    MAX_BACKGROUND_CLEANUP_STRENGTH_MILLIONTHS,
    MAX_CONTRAST_FACTOR_MILLIONTHS,
    MAX_DESKEW_ANGLE_MILLIDEGREES,
    SCAN_TRANSFORM_OPERATION_VERSION,
    BackgroundCleanupPageCommand,
    BackgroundCleanupPayload,
    ContrastPageCommand,
    ContrastPayload,
    CropPageCommand,
    DeskewPageCommand,
    DeskewPayload,
    NormalizedPageBox,
    PageCommandHistory,
    PageOperationError,
    PageOperationErrorCode,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "schemas" / "examples" / "case-manifest-v2-representative.json"
PAGE_ID = UUID("30000000-0000-4000-8000-000000000003")


def _snapshot() -> ManifestSnapshot:
    loaded = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert type(loaded) is dict
    document = cast(dict[str, object], loaded)
    return ManifestSnapshot(
        case_id=UUID(cast(str, document["case_id"])),
        schema_version=2,
        revision=cast(int, document["revision"]),
        document=document,
        sha256="a" * 64,
        cas_token="migration-test-token",
    )


def test_v2_scan_transforms_replay_latest_wins_and_round_trip() -> None:
    snapshot = _snapshot()
    history = PageCommandHistory.from_snapshot(snapshot)
    initial = history.projection.state_for(PAGE_ID)
    assert initial.deskew_angle_millidegrees_clockwise == -1250
    assert initial.contrast_factor_millionths == 1_150_000
    assert initial.background_cleanup_strength_millionths == 250_000

    edited = (
        history.execute(DeskewPageCommand(PAGE_ID, 2_500))
        .execute(ContrastPageCommand(PAGE_ID, 900_000))
        .execute(BackgroundCleanupPageCommand(PAGE_ID, 0))
        .execute(
            CropPageCommand(
                PAGE_ID,
                NormalizedPageBox(x=100_000, y=120_000, width=700_000, height=650_000),
            )
        )
    )
    state = edited.projection.state_for(PAGE_ID)
    assert state.deskew_angle_millidegrees_clockwise == 2_500
    assert state.contrast_factor_millionths == 900_000
    assert state.background_cleanup_strength_millionths == 0
    assert state.crop == NormalizedPageBox(100_000, 120_000, 700_000, 650_000)

    operations = edited.operations[-4:]
    assert [operation.operation_version for operation in operations] == [2, 2, 2, 1]
    assert isinstance(operations[0].payload, DeskewPayload)
    assert isinstance(operations[1].payload, ContrastPayload)
    assert isinstance(operations[2].payload, BackgroundCleanupPayload)
    assert operations[0].operation_version == SCAN_TRANSFORM_OPERATION_VERSION

    reloaded = PageCommandHistory.from_snapshot(
        ManifestSnapshot(
            case_id=snapshot.case_id,
            schema_version=2,
            revision=snapshot.revision,
            document=edited.to_candidate(snapshot),
            sha256="b" * 64,
            cas_token="migration-test-reload-token",
        )
    )
    assert reloaded.projection == edited.projection
    assert edited.undo().redo().projection == edited.projection


@pytest.mark.parametrize(
    "command",
    [
        DeskewPageCommand(PAGE_ID, -MAX_DESKEW_ANGLE_MILLIDEGREES),
        DeskewPageCommand(PAGE_ID, MAX_DESKEW_ANGLE_MILLIDEGREES),
        ContrastPageCommand(PAGE_ID, 0),
        ContrastPageCommand(PAGE_ID, MAX_CONTRAST_FACTOR_MILLIONTHS),
        BackgroundCleanupPageCommand(PAGE_ID, 0),
        BackgroundCleanupPageCommand(
            PAGE_ID,
            MAX_BACKGROUND_CLEANUP_STRENGTH_MILLIONTHS,
        ),
    ],
)
def test_scan_transform_inclusive_boundaries_are_accepted(command: object) -> None:
    history = PageCommandHistory.from_snapshot(_snapshot())
    assert history.execute(command).operations[-1].operation_version == 2  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("factory", "code"),
    [
        (
            lambda: DeskewPageCommand(PAGE_ID, MAX_DESKEW_ANGLE_MILLIDEGREES + 1),
            PageOperationErrorCode.DESKEW_INVALID,
        ),
        (
            lambda: ContrastPageCommand(PAGE_ID, MAX_CONTRAST_FACTOR_MILLIONTHS + 1),
            PageOperationErrorCode.CONTRAST_INVALID,
        ),
        (
            lambda: BackgroundCleanupPageCommand(
                PAGE_ID,
                MAX_BACKGROUND_CLEANUP_STRENGTH_MILLIONTHS + 1,
            ),
            PageOperationErrorCode.BACKGROUND_CLEANUP_INVALID,
        ),
    ],
)
def test_scan_transform_maximum_plus_one_is_rejected(factory: object, code: object) -> None:
    assert callable(factory)
    with pytest.raises(PageOperationError) as raised:
        factory()
    assert raised.value.code is code

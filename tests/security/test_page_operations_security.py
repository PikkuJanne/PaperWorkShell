"""Privacy and bounded-work evidence for the pure PWS-0110 page domain."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest

from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.domain.page_operations import (
    PageCommandHistory,
    PageOperation,
    PageOperationError,
    PageOperationErrorCode,
    PageReference,
    RotatePageCommand,
    SetGroupPayload,
    replay_page_operations,
)

ROOT = Path(__file__).resolve().parents[2]
REPRESENTATIVE_MANIFEST = ROOT / "schemas" / "examples" / "case-manifest-v1-representative.json"


def _snapshot(document: dict[str, object]) -> ManifestSnapshot:
    case_id = document["case_id"]
    revision = document["revision"]
    assert type(case_id) is str
    assert type(revision) is int
    return ManifestSnapshot(
        UUID(case_id),
        1,
        revision,
        document,
        "a" * 64,
        "synthetic-token-1",
    )


def _representative_document() -> dict[str, object]:
    loaded = json.loads(REPRESENTATIVE_MANIFEST.read_bytes())
    assert type(loaded) is dict
    return loaded


def test_page_history_failure_never_discloses_manifest_canaries() -> None:
    document = _representative_document()
    sources = document["sources"]
    pages = document["pages"]
    assert type(sources) is list and type(sources[0]) is dict
    assert type(pages) is list and type(pages[0]) is dict
    display_canary = "private display canary.pdf"
    identifier_canary = "private-page-identifier-canary"
    sources[0]["display_name"] = display_canary
    pages[0]["page_id"] = identifier_canary

    with pytest.raises(PageOperationError) as raised:
        PageCommandHistory.from_snapshot(_snapshot(document))

    rendered = f"{raised.value!r} {raised.value} {raised.value.args!r}"
    assert raised.value.code is PageOperationErrorCode.MANIFEST_INVALID
    assert display_canary not in rendered
    assert identifier_canary not in rendered


def test_identifier_factory_is_bounded_and_discloses_no_identifier() -> None:
    history = PageCommandHistory.from_snapshot(_snapshot(_representative_document()))
    reserved = UUID("10000000-0000-4000-8000-000000000002")
    page_id = UUID("30000000-0000-4000-8000-000000000003")
    calls = 0

    def collide() -> UUID:
        nonlocal calls
        calls += 1
        return reserved

    with pytest.raises(PageOperationError) as raised:
        history.execute(RotatePageCommand(page_id, 1), id_factory=collide)

    assert calls == 32
    assert raised.value.code is PageOperationErrorCode.ID_GENERATION_FAILED
    assert str(reserved) not in f"{raised.value!r} {raised.value}"


def test_replay_rejects_excessive_aggregate_group_work() -> None:
    source_id = UUID(int=1_000_000, version=4)
    pages = tuple(
        PageReference(UUID(int=index, version=4), source_id, index - 1) for index in range(1, 1_001)
    )
    page_ids = tuple(page.page_id for page in pages)
    operations = tuple(
        PageOperation(
            UUID(int=2_000_000 + index, version=4),
            index,
            SetGroupPayload(
                UUID(int=3_000_000 + index, version=4),
                page_ids,
            ),
        )
        for index in range(1, 101)
    )

    with pytest.raises(PageOperationError) as raised:
        replay_page_operations(pages, page_ids, operations)

    assert raised.value.code is PageOperationErrorCode.CAPACITY_EXCEEDED

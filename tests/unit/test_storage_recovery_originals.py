"""Focused pure evidence for manifest-authorized originals recovery."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from uuid import UUID

import pytest

from paperworkshell.domain.manifest_repository import ManifestSnapshot
from paperworkshell.storage import recovery as recovery_module
from paperworkshell.storage.errors import WorkspaceError, WorkspaceErrorCode
from paperworkshell.storage.manifest_codec import MAX_MANIFEST_COLLECTION_ITEMS

CASE_ID = UUID("51000000-0000-4000-8000-000000000001")
SOURCE_ID = UUID("52000000-0000-4000-8000-000000000002")
ASSET_ID = UUID("53000000-0000-4000-8000-000000000003")


def _snapshot() -> ManifestSnapshot:
    return ManifestSnapshot(
        CASE_ID,
        2,
        0,
        {
            "schema_version": 2,
            "case_id": str(CASE_ID),
            "revision": 0,
            "sources": [{"storage_name": f"{SOURCE_ID}.bin"}],
            "assets": [{"storage_name": f"{ASSET_ID}.bin"}],
        },
        "a" * 64,
        "storage_recovery_originals_0001",
    )


@dataclass(frozen=True, slots=True)
class _DirectoryEntry:
    name: str


class _Scandir:
    def __init__(self, count: int) -> None:
        self._count = count

    def __enter__(self) -> Iterator[_DirectoryEntry]:
        return (_DirectoryEntry(f"entry-{index:04d}") for index in range(self._count))

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback


def test_manifest_original_names_include_sources_and_assets() -> None:
    assert recovery_module._manifest_original_names(_snapshot()) == frozenset(
        {f"{SOURCE_ID}.bin", f"{ASSET_ID}.bin"}
    )


def test_original_inventory_accepts_schema_valid_combined_maximum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    combined_maximum = MAX_MANIFEST_COLLECTION_ITEMS * 2
    monkeypatch.setattr(
        os,
        "scandir",
        lambda _path: _Scandir(combined_maximum),
    )

    names = recovery_module._bounded_original_names(Path("C:/synthetic-originals"))

    assert len(names) == combined_maximum


def test_original_inventory_rejects_more_than_combined_maximum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        os,
        "scandir",
        lambda _path: _Scandir((MAX_MANIFEST_COLLECTION_ITEMS * 2) + 1),
    )

    with pytest.raises(WorkspaceError) as raised:
        recovery_module._bounded_original_names(Path("C:/synthetic-originals"))

    assert raised.value.code is WorkspaceErrorCode.SCAN_LIMIT_EXCEEDED
    assert raised.value.context == "recovery.original_entries"

"""Focused lossless case-manifest v1-to-v2 migration evidence."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import cast

import pytest

from paperworkshell.domain.manifest_migrations import migrate_manifest_v1_to_v2
from paperworkshell.domain.manifest_repository import (
    MAX_SAFE_MANIFEST_INTEGER,
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
)

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_ROOT = ROOT / "tests" / "fixtures" / "manifests" / "migrations"


def _fixture(name: str) -> dict[str, object]:
    loaded = json.loads((MIGRATION_ROOT / name).read_text(encoding="utf-8"))
    assert type(loaded) is dict
    return cast(dict[str, object], loaded)


def test_v1_to_v2_migration_matches_exact_fixture_and_does_not_mutate_source() -> None:
    source = _fixture("v1-to-v2-input.json")
    before = copy.deepcopy(source)

    migrated = migrate_manifest_v1_to_v2(source)

    assert migrated == _fixture("v1-to-v2-expected.json")
    assert source == before
    assert migrated is not source
    assert migrated["sources"] is not source["sources"]
    assert migrated["page_operations"] is not source["page_operations"]


@pytest.mark.parametrize(
    ("version", "code"),
    [
        ("1", ManifestRepositoryErrorCode.MANIFEST_INVALID),
        (0, ManifestRepositoryErrorCode.VERSION_UNSUPPORTED),
        (2, ManifestRepositoryErrorCode.VERSION_UNSUPPORTED),
        (3, ManifestRepositoryErrorCode.VERSION_UNSUPPORTED),
    ],
)
def test_migration_rejects_non_v1_dispatch_without_mutation(
    version: object,
    code: ManifestRepositoryErrorCode,
) -> None:
    source = _fixture("v1-to-v2-input.json")
    source["schema_version"] = version
    before = copy.deepcopy(source)

    with pytest.raises(ManifestRepositoryError) as raised:
        migrate_manifest_v1_to_v2(source)

    assert raised.value.code is code
    assert source == before


def test_migration_rejects_revision_exhaustion() -> None:
    source = _fixture("v1-to-v2-input.json")
    source["revision"] = MAX_SAFE_MANIFEST_INTEGER

    with pytest.raises(ManifestRepositoryError) as raised:
        migrate_manifest_v1_to_v2(source)

    assert raised.value.code is ManifestRepositoryErrorCode.MANIFEST_INVALID

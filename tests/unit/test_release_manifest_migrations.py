"""Closed release-migration routes preserve complete frozen case intent."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import cast
from unittest.mock import Mock
from uuid import UUID

import pytest

from paperworkshell.app.case_lifecycle import CaseLifecycleService, CaseLifecycleStore
from paperworkshell.domain.case_lifecycle import CaseLifecycleError, CaseLifecycleErrorCode
from paperworkshell.domain.manifest_migrations import (
    MANIFEST_MIGRATION_REGISTRY,
    manifest_migration_path,
    migrate_manifest_to_current,
)
from paperworkshell.domain.manifest_repository import (
    MAX_SAFE_MANIFEST_INTEGER,
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
)
from paperworkshell.storage.manifest_migration_backup import MIGRATION_BACKUP_NAMES
from paperworkshell.storage.manifest_repository import (
    canonical_manifest_bytes,
    validate_manifest_payload,
)

ROOT = Path(__file__).resolve().parents[2]


def test_versioned_compatibility_index_binds_accepted_synthetic_inputs() -> None:
    index = json.loads(
        (ROOT / "tests/fixtures/manifests/release-compatibility-v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert index["fixture_index_version"] == 1
    assert len(index["cases"]) == 6
    for item in index["cases"]:
        expected = f"schemas/examples/case-manifest-v{item['schema_version']}-{item['kind']}.json"
        assert item["input"] == expected
        assert hashlib.sha256((ROOT / expected).read_bytes()).hexdigest() == item["sha256"]


@pytest.mark.parametrize("version", [1, 2, 3])
@pytest.mark.parametrize("kind", ["minimal", "representative"])
def test_frozen_versions_preserve_every_value_outside_reviewed_dispatch(
    version: int, kind: str
) -> None:
    source = cast(
        dict[str, object],
        json.loads(
            (ROOT / "schemas" / "examples" / f"case-manifest-v{version}-{kind}.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    before = copy.deepcopy(source)
    migrated = migrate_manifest_to_current(source)
    expected = copy.deepcopy(source)
    if version < 3:
        expected.update(schema_version=3, revision=cast(int, source["revision"]) + 1)
        expected["redaction_marks"] = []
    assert migrated == expected
    assert source == before
    assert migrated is not source
    assert migrated["sources"] is not source["sources"]
    validate_manifest_payload(
        canonical_manifest_bytes(migrated), UUID(cast(str, source["case_id"]))
    )
    assert migrate_manifest_to_current(migrated) == migrated


def test_registry_is_closed_contiguous_and_current_has_no_work() -> None:
    assert [(step.source_version, step.target_version) for step in MANIFEST_MIGRATION_REGISTRY] == [
        (1, 2),
        (2, 3),
    ]
    assert manifest_migration_path(1) == MANIFEST_MIGRATION_REGISTRY
    assert manifest_migration_path(2) == MANIFEST_MIGRATION_REGISTRY[1:]
    assert manifest_migration_path(3) == ()
    assert tuple(MIGRATION_BACKUP_NAMES.values()) == (
        "migration-source-v1.json",
        "migration-source-v2.json",
    )
    with pytest.raises(TypeError):
        cast(dict[int, str], MIGRATION_BACKUP_NAMES)[1] = "../outside.json"


@pytest.mark.parametrize("version", [0, 4, 999_999])
def test_unknown_versions_fail_before_transforming_caller_state(version: int) -> None:
    source: dict[str, object] = {"schema_version": version, "revision": 0}
    before = copy.deepcopy(source)
    with pytest.raises(ManifestRepositoryError) as raised:
        migrate_manifest_to_current(source)
    assert raised.value.code is ManifestRepositoryErrorCode.VERSION_UNSUPPORTED
    assert source == before


@pytest.mark.parametrize("version", [True, "1", None])
def test_malformed_dispatch_is_not_an_alias_for_a_supported_version(version: object) -> None:
    with pytest.raises(ManifestRepositoryError) as raised:
        migrate_manifest_to_current({"schema_version": version, "revision": 0})
    assert raised.value.code is ManifestRepositoryErrorCode.MANIFEST_INVALID


def test_complete_route_consumes_one_remaining_revision() -> None:
    source: dict[str, object] = {
        "schema_version": 1,
        "revision": MAX_SAFE_MANIFEST_INTEGER - 1,
    }
    assert migrate_manifest_to_current(source) == {
        "schema_version": 3,
        "revision": MAX_SAFE_MANIFEST_INTEGER,
        "redaction_marks": [],
    }


@pytest.mark.parametrize("external", [Path("external-project.pws"), "external-project.pws"])
def test_case_open_port_rejects_external_project_paths_before_storage(external: object) -> None:
    store = Mock(spec=CaseLifecycleStore)
    service = CaseLifecycleService(cast(CaseLifecycleStore, store))
    with pytest.raises(CaseLifecycleError) as raised:
        service.open_case(cast(UUID, external))
    assert raised.value.code is CaseLifecycleErrorCode.CASE_ID_INVALID
    assert store.mock_calls == []

"""Windows transactional evidence for automatic case-manifest v1-to-v2 migration."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.domain.manifest_migrations import migrate_manifest_v1_to_v2
from paperworkshell.domain.manifest_repository import (
    ManifestLoadStatus,
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
)
from paperworkshell.storage import manifest_repository as repository_storage
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.manifest_repository import (
    WindowsManifestRepository,
    canonical_manifest_bytes,
)
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.workspace import CaseWorkspaceManager

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="Manifest publication uses the supported Windows storage boundary",
)

ROOT = Path(__file__).resolve().parents[2]
V1_FIXTURE = ROOT / "tests" / "fixtures" / "manifests" / "migrations" / "v1-to-v2-input.json"
CASE_ID = UUID("10000000-0000-4000-8000-000000000202")


def _quota() -> WorkspaceQuota:
    return WorkspaceQuota(
        maximum_case_bytes=32_000_000,
        maximum_cache_bytes=1_000_000,
        maximum_temp_bytes=20_000_000,
        maximum_entries=256,
        maximum_depth=16,
    )


def _stack(tmp_path: Path) -> tuple[CaseWorkspaceManager, Path]:
    profile = tmp_path / "profile"
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=_quota(),
        id_factory=lambda: CASE_ID,
    )
    lease = WindowsCaseLifecycleStore(manager).create_case()
    lease.close()
    return manager, manager.layout(CASE_ID).manifest


def _install_v1(manifest_path: Path) -> dict[str, object]:
    loaded = json.loads(V1_FIXTURE.read_text(encoding="utf-8"))
    assert type(loaded) is dict
    document = cast(dict[str, object], loaded)
    document["case_id"] = str(CASE_ID)
    manifest_path.write_bytes(canonical_manifest_bytes(document))
    return document


def test_load_migrates_v1_losslessly_through_journal_and_is_idempotent(tmp_path: Path) -> None:
    manager, manifest_path = _stack(tmp_path)
    source = _install_v1(manifest_path)
    source_hashes = tuple(
        cast(dict[str, object], record)["sha256"]
        for record in cast(list[object], source["sources"])
    )

    migrated = WindowsManifestRepository(manager).load(CASE_ID)

    assert migrated.status is ManifestLoadStatus.MIGRATED
    assert migrated.snapshot.document == migrate_manifest_v1_to_v2(source)
    assert (
        tuple(
            cast(dict[str, object], record)["sha256"]
            for record in cast(list[object], migrated.snapshot.document["sources"])
        )
        == source_hashes
    )
    layout = manager.layout(CASE_ID)
    assert manifest_path.read_bytes() == (layout.journal / "latest.json").read_bytes()

    second = WindowsManifestRepository(manager).load(CASE_ID)
    assert second.status is ManifestLoadStatus.CURRENT
    assert second.snapshot.document == migrated.snapshot.document


def test_interrupted_migration_checkpoint_recovers_exact_v2_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, manifest_path = _stack(tmp_path)
    source = _install_v1(manifest_path)
    previous = manifest_path.read_bytes()

    def interrupt_replace(*_args: object, **_kwargs: object) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(
        repository_storage,
        "replace_manifest_atomically",
        interrupt_replace,
    )
    with pytest.raises(KeyboardInterrupt):
        WindowsManifestRepository(manager).load(CASE_ID)

    layout = manager.layout(CASE_ID)
    assert manifest_path.read_bytes() == previous
    assert json.loads((layout.journal / "latest.json").read_bytes()) == (
        migrate_manifest_v1_to_v2(source)
    )

    monkeypatch.undo()
    reopened_manager = CaseWorkspaceManager(profile_root=manager.profile_root, quota=_quota())
    recovered = WindowsManifestRepository(reopened_manager).load(CASE_ID)
    assert recovered.status is ManifestLoadStatus.RECOVERED_MIGRATED
    assert recovered.snapshot.document == migrate_manifest_v1_to_v2(source)
    assert manifest_path.read_bytes() == (layout.journal / "latest.json").read_bytes()


def test_invalid_v1_is_rejected_before_migration_or_checkpoint_mutation(tmp_path: Path) -> None:
    manager, manifest_path = _stack(tmp_path)
    source = _install_v1(manifest_path)
    source["page_sequence"] = ["ffffffff-ffff-4fff-8fff-ffffffffffff"]
    previous = canonical_manifest_bytes(source)
    manifest_path.write_bytes(previous)

    with pytest.raises(ManifestRepositoryError) as raised:
        WindowsManifestRepository(manager).load(CASE_ID)

    assert raised.value.code is ManifestRepositoryErrorCode.MANIFEST_INVALID
    assert manifest_path.read_bytes() == previous
    assert not (manager.layout(CASE_ID).journal / "latest.json").exists()

"""Native release-to-release case migration, bounded backups and failure recovery."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest

from paperworkshell.__main__ import build_windows_startup_recovery_service
from paperworkshell.domain.manifest_migrations import migrate_manifest_to_current
from paperworkshell.domain.manifest_repository import (
    ManifestLoadStatus,
    ManifestRepositoryError,
    ManifestRepositoryErrorCode,
)
from paperworkshell.domain.recovery import ExportHistoryRecoveryResult
from paperworkshell.domain.resource_policy import (
    CONSTRAINED_RESOURCE_POLICY_V1,
    NORMAL_RESOURCE_POLICY_V1,
    ResourcePolicy,
)
from paperworkshell.storage import manifest_migration_backup as backup_storage
from paperworkshell.storage import manifest_repository as repository_storage
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.recovery import WindowsStartupRecoveryStore
from paperworkshell.storage.workspace import CaseWorkspaceManager
from tests.helpers.release_migration import CASE_ID, file_bytes, release_case, release_quota

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Authenticated Windows storage required")


@pytest.mark.parametrize("version", [1, 2, 3])
def test_listing_and_startup_do_not_initiate_migration_of_closed_cases(
    tmp_path: Path, version: int
) -> None:
    manager, layout, _source, old_bytes = release_case(tmp_path, version)
    originals = file_bytes(layout.originals)
    index = WindowsCaseLifecycleStore(manager).list_cases()
    assert [case.case_id for case in index.cases] == [CASE_ID]
    assert index.cases[0].manifest_version == version
    assert layout.manifest.read_bytes() == old_bytes
    assert not list(layout.journal.glob("migration-source-v*.json"))
    recovered = WindowsManifestRepository(manager).load(CASE_ID, migrate=False)
    assert recovered.snapshot.schema_version == version
    assert layout.manifest.read_bytes() == old_bytes
    WindowsStartupRecoveryStore(
        manager, settle_exports=lambda _case_id: ExportHistoryRecoveryResult()
    ).scan_and_recover()
    build_windows_startup_recovery_service(manager).recover_startup()
    assert layout.manifest.read_bytes() == old_bytes
    assert not list(layout.journal.glob("migration-source-v*.json"))
    assert file_bytes(layout.originals) == originals
    opened = WindowsCaseLifecycleStore(manager).open_case(CASE_ID)
    try:
        assert opened.summary.manifest_version == 4
        assert (layout.journal / f"migration-source-v{version}.json").read_bytes() == old_bytes
    finally:
        opened.close()


@pytest.mark.parametrize("version", [1, 2, 3, 4])
@pytest.mark.parametrize("policy", [NORMAL_RESOURCE_POLICY_V1, CONSTRAINED_RESOURCE_POLICY_V1])
def test_versioned_case_reopen_preserves_intent_sources_and_bounded_backup(
    tmp_path: Path, version: int, policy: ResourcePolicy
) -> None:
    manager, layout, source, old_bytes = release_case(tmp_path, version, policy=policy)
    originals = file_bytes(layout.originals)
    with manager.observe_capacity_claims(CASE_ID) as observation:
        first = WindowsManifestRepository(manager).load(CASE_ID)
    assert first.snapshot.document == migrate_manifest_to_current(source)
    assert first.status is (
        ManifestLoadStatus.CURRENT if version == 4 else ManifestLoadStatus.MIGRATED
    )
    backups = list(layout.journal.glob("migration-source-v*.json"))
    assert len(backups) == (0 if version == 4 else 1)
    if backups:
        assert backups[0].name == f"migration-source-v{version}.json"
        assert backups[0].read_bytes() == old_bytes
    before_reopen = file_bytes(layout.case_root)
    second = WindowsManifestRepository(manager).load(CASE_ID)
    assert second.status is ManifestLoadStatus.CURRENT
    assert second.snapshot.document == first.snapshot.document
    assert file_bytes(layout.case_root) == before_reopen
    assert file_bytes(layout.originals) == originals
    assert not list(layout.temp.iterdir())
    assert observation.snapshot.current.is_empty
    assert observation.snapshot.high_water.temp_bytes <= policy.maximum_temporary_storage_bytes


@pytest.mark.parametrize("version", [1, 2, 3])
def test_interruption_after_checkpoint_preserves_backup_and_recovers_exact_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: int
) -> None:
    manager, layout, source, old_bytes = release_case(tmp_path, version)
    originals = file_bytes(layout.originals)

    def interrupt(*_args: object, **_kwargs: object) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(repository_storage, "replace_manifest_atomically", interrupt)
    with pytest.raises(KeyboardInterrupt):
        WindowsManifestRepository(manager).load(CASE_ID)
    assert layout.manifest.read_bytes() == old_bytes
    assert (layout.journal / f"migration-source-v{version}.json").read_bytes() == old_bytes
    assert json.loads((layout.journal / "latest.json").read_bytes()) == migrate_manifest_to_current(
        source
    )
    monkeypatch.undo()
    reopened = CaseWorkspaceManager(profile_root=manager.profile_root, quota=release_quota())
    result = WindowsManifestRepository(reopened).load(CASE_ID)
    assert result.status is ManifestLoadStatus.RECOVERED_MIGRATED
    assert result.snapshot.document == migrate_manifest_to_current(source)
    assert file_bytes(layout.originals) == originals
    assert not list(layout.temp.iterdir())


def test_disk_full_while_retaining_backup_never_changes_authoritative_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, layout, _source, _old_bytes = release_case(tmp_path)
    before = file_bytes(layout.case_root)

    def disk_full(*_args: object, **_kwargs: object) -> object:
        raise OSError(errno.ENOSPC, "synthetic private filename and content")

    monkeypatch.setattr(backup_storage, "write_new_file", disk_full)
    with pytest.raises(ManifestRepositoryError) as raised:
        WindowsManifestRepository(manager).load(CASE_ID)
    assert raised.value.code is ManifestRepositoryErrorCode.SAVE_FAILED
    assert "private" not in str(raised.value)
    assert file_bytes(layout.case_root) == before
    with manager.observe_capacity_claims(CASE_ID) as observation:
        assert observation.snapshot.current.is_empty


def test_post_backup_stage_failure_retains_original_and_retries_without_new_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, layout, source, old_bytes = release_case(tmp_path)

    def disk_full(*_args: object, **_kwargs: object) -> object:
        raise OSError(errno.ENOSPC, "synthetic failure")

    monkeypatch.setattr(repository_storage, "write_new_file", disk_full)
    with pytest.raises(ManifestRepositoryError):
        WindowsManifestRepository(manager).load(CASE_ID)
    backup = layout.journal / "migration-source-v1.json"
    backup_identity = backup.stat().st_ino
    assert backup.read_bytes() == old_bytes
    assert layout.manifest.read_bytes() == old_bytes
    assert not (layout.journal / "latest.json").exists()
    monkeypatch.undo()
    assert WindowsManifestRepository(manager).load(CASE_ID).snapshot.document == (
        migrate_manifest_to_current(source)
    )
    assert backup.stat().st_ino == backup_identity
    assert not list(layout.temp.iterdir())


def test_low_disk_refuses_backup_before_any_case_mutation(tmp_path: Path) -> None:
    manager, layout, _source, _old_bytes = release_case(tmp_path)
    before = file_bytes(layout.case_root)
    constrained = CONSTRAINED_RESOURCE_POLICY_V1
    low_disk = CaseWorkspaceManager(
        profile_root=manager.profile_root,
        quota=release_quota(constrained),
        minimum_free_space_reserve_bytes=constrained.minimum_export_free_space_reserve_bytes,
        available_space_probe=lambda _path: 0,
    )
    with pytest.raises(ManifestRepositoryError) as raised:
        WindowsManifestRepository(low_disk).load(CASE_ID)
    assert raised.value.code is ManifestRepositoryErrorCode.CAPACITY_EXCEEDED
    assert file_bytes(layout.case_root) == before


def test_cancelled_open_never_begins_migration_or_changes_case(tmp_path: Path) -> None:
    manager, layout, _source, _old_bytes = release_case(tmp_path)
    before = file_bytes(layout.case_root)

    def cancel() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        WindowsManifestRepository(manager).load(CASE_ID, coordination_checkpoint=cancel)
    assert file_bytes(layout.case_root) == before

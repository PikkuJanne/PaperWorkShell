from __future__ import annotations

import copy
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from paperworkshell.domain.recovery import (
    CaseRecoveryStatus,
    ExportHistoryRecoveryResult,
    RecoveryIssueCode,
    StartupRecoveryStatus,
)
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.errors import WorkspaceErrorCode, reject
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.recovery import WindowsStartupRecoveryStore
from paperworkshell.storage.workspace import CaseWorkspaceManager, CaseWorkspacePaths

pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="PWS-0113 startup storage recovery targets supported Windows volumes",
)

CASE_ID = UUID("41000000-0000-4000-8000-000000000001")
ORPHAN_ID = UUID("42000000-0000-4000-8000-000000000002")
THUMBNAIL_STAGE_ID = UUID("43000000-0000-4000-8000-000000000003")
TEMP_STAGE_ID = UUID("44000000-0000-4000-8000-000000000004")
CREATED_AT = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)


def _quota() -> WorkspaceQuota:
    return WorkspaceQuota(
        maximum_case_bytes=32_000_000,
        maximum_cache_bytes=8_000_000,
        maximum_temp_bytes=8_000_000,
        maximum_entries=512,
        maximum_depth=16,
    )


def _stack(tmp_path: Path) -> tuple[CaseWorkspaceManager, CaseWorkspacePaths]:
    profile = (tmp_path / "profile").resolve()
    profile.mkdir()
    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=_quota(),
        id_factory=lambda: CASE_ID,
    )
    lease = WindowsCaseLifecycleStore(manager, clock=lambda: CREATED_AT).create_case()
    lease.close()
    return manager, manager.layout(CASE_ID)


def _next_manifest(document: dict[str, object]) -> dict[str, object]:
    candidate = copy.deepcopy(document)
    candidate["revision"] = 1
    candidate["updated_at"] = "2026-08-09T10:01:00Z"
    return candidate


def _publish_recoverable_checkpoint(
    manager: CaseWorkspaceManager,
    layout: CaseWorkspacePaths,
) -> bytes:
    repository = WindowsManifestRepository(manager)
    opened = repository.load(CASE_ID).snapshot
    candidate = _next_manifest(opened.document)
    repository.save(
        CASE_ID,
        candidate,
        expected_revision=opened.revision,
        expected_sha256=opened.sha256,
        expected_cas_token=opened.cas_token,
    )
    checkpoint = (layout.journal / "latest.json").read_bytes()
    layout.manifest.write_bytes(b'{"schema_version":1')
    return checkpoint


def _tree_snapshot(root: Path) -> dict[str, tuple[str, bytes]]:
    snapshot: dict[str, tuple[str, bytes]] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            snapshot[relative] = ("directory", b"")
        else:
            snapshot[relative] = ("file", path.read_bytes())
    return snapshot


def test_startup_recovers_manifest_then_cleans_orphans_thumbnail_stages_and_temp(
    tmp_path: Path,
) -> None:
    manager, layout = _stack(tmp_path)
    checkpoint = _publish_recoverable_checkpoint(manager, layout)
    orphan = layout.originals / f"{ORPHAN_ID}.bin"
    thumbnail_stage = layout.thumbnails / f"{THUMBNAIL_STAGE_ID}.tmp"
    temp_stage = layout.temp / f"{TEMP_STAGE_ID}.tmp"
    orphan.write_bytes(b"synthetic-unreferenced-source")
    thumbnail_stage.write_bytes(b"synthetic-stale-thumbnail-stage")
    temp_stage.write_bytes(b"synthetic-stale-case-temp")

    result = WindowsStartupRecoveryStore(manager).scan_and_recover()

    assert result.status is StartupRecoveryStatus.COMPLETE
    assert result.scanned_cases == result.recovered_cases == 1
    case = result.cases[0]
    assert case.case_id == CASE_ID
    assert case.status is CaseRecoveryStatus.RECOVERED
    assert case.manifest_revision == 1
    assert case.issues == ()
    assert case.removed_source_orphans == 1
    assert case.retained_source_artifacts == 0
    assert case.removed_thumbnail_stages == 1
    assert case.removed_temp_files == 1
    assert case.removed_temp_directories == 0
    assert case.released_bytes == (
        len(b"synthetic-unreferenced-source")
        + len(b"synthetic-stale-thumbnail-stage")
        + len(b"synthetic-stale-case-temp")
    )
    assert layout.manifest.read_bytes() == checkpoint
    assert not orphan.exists()
    assert not thumbnail_stage.exists()
    assert list(layout.temp.iterdir()) == []


def test_invalid_case_is_reported_unavailable_without_cleaning_any_artifact(
    tmp_path: Path,
) -> None:
    manager, layout = _stack(tmp_path)
    layout.manifest.write_bytes(b'{"schema_version":1')
    orphan = layout.originals / f"{ORPHAN_ID}.bin"
    thumbnail_stage = layout.thumbnails / f"{THUMBNAIL_STAGE_ID}.tmp"
    temp_stage = layout.temp / f"{TEMP_STAGE_ID}.tmp"
    orphan.write_bytes(b"invalid-case-source-residue")
    thumbnail_stage.write_bytes(b"invalid-case-thumbnail-residue")
    temp_stage.write_bytes(b"invalid-case-temp-residue")
    before = _tree_snapshot(layout.case_root)

    result = WindowsStartupRecoveryStore(manager).scan_and_recover()

    assert result.status is StartupRecoveryStatus.ATTENTION_REQUIRED
    assert result.unavailable_cases == 1
    case = result.cases[0]
    assert case.status is CaseRecoveryStatus.UNAVAILABLE
    assert case.manifest_revision is None
    assert case.issues == (RecoveryIssueCode.MANIFEST_INVALID,)
    assert case.removed_source_orphans == 0
    assert case.removed_thumbnail_stages == 0
    assert case.removed_temp_files == 0
    assert _tree_snapshot(layout.case_root) == before


def test_export_receipts_settle_before_generic_temp_cleanup(tmp_path: Path) -> None:
    manager, layout = _stack(tmp_path)
    temp_stage = layout.temp / f"{TEMP_STAGE_ID}.export-retry-committed.tmp"
    temp_stage.write_bytes(b"synthetic-owner-stage")
    observed: list[UUID] = []

    def settle(case_id: UUID) -> ExportHistoryRecoveryResult:
        assert temp_stage.is_file()
        observed.append(case_id)
        return ExportHistoryRecoveryResult(
            repaired_entries=1,
            ambiguous_entries=1,
        )

    result = WindowsStartupRecoveryStore(
        manager,
        settle_exports=settle,
    ).scan_and_recover()

    assert observed == [CASE_ID]
    assert not temp_stage.exists()
    case = result.cases[0]
    assert case.status is CaseRecoveryStatus.RECOVERED
    assert case.repaired_export_history == 1
    assert case.ambiguous_export_commits == 1
    assert case.issues == (RecoveryIssueCode.EXPORT_COMMIT_AMBIGUOUS,)
    assert result.status is StartupRecoveryStatus.ATTENTION_REQUIRED


def test_capacity_failure_during_manifest_restore_preserves_all_recovery_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, layout = _stack(tmp_path)
    _publish_recoverable_checkpoint(manager, layout)
    orphan = layout.originals / f"{ORPHAN_ID}.bin"
    orphan.write_bytes(b"capacity-failure-source-residue")
    before = _tree_snapshot(layout.case_root)

    def reject_capacity(*_args: object, **_kwargs: object) -> None:
        reject(
            WorkspaceErrorCode.QUOTA_EXCEEDED,
            "quota.capacity",
            "Synthetic configured-capacity failure.",
        )

    monkeypatch.setattr(manager, "reserve_capacity", reject_capacity)

    result = WindowsStartupRecoveryStore(manager).scan_and_recover()

    assert result.unavailable_cases == 1
    assert result.cases[0].issues == (RecoveryIssueCode.MANIFEST_RECOVERY_FAILED,)
    assert _tree_snapshot(layout.case_root) == before

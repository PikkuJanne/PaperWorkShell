"""Windows integration evidence for secure case-workspace provisioning and cleanup."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from paperworkshell.storage.errors import WorkspaceError, WorkspaceErrorCode
from paperworkshell.storage.permissions import (
    PermissionVerification,
    WindowsPrivateDirectoryPermissions,
)
from paperworkshell.storage.quotas import QuotaArea, WorkspaceQuota
from paperworkshell.storage.workspace import (
    DEFAULT_CASES_ROOT_PARTS,
    CaseWorkspaceManager,
    CleanupStatus,
    CleanupTarget,
)

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0101 targets the supported Windows platform"
)


def _quota(
    *,
    case_bytes: int = 1_000_000,
    cache_bytes: int = 100_000,
    temp_bytes: int = 100_000,
    entries: int = 100,
    depth: int = 10,
) -> WorkspaceQuota:
    return WorkspaceQuota(case_bytes, cache_bytes, temp_bytes, entries, depth)


def test_provision_creates_exact_private_skeleton_without_authoritative_records(
    tmp_path: Path,
) -> None:
    manager = CaseWorkspaceManager(profile_root=tmp_path, quota=_quota())
    layout = manager.provision()
    verifier = WindowsPrivateDirectoryPermissions()

    assert layout.case_id.version == 4
    assert layout.case_root.parent == tmp_path.joinpath(*DEFAULT_CASES_ROOT_PARTS)
    assert all(path.is_dir() for path in layout.directories)
    assert not layout.manifest.exists()
    assert not layout.export_history.exists()
    assert {path.name for path in layout.case_root.iterdir()} == {
        "journal",
        "originals",
        "cache",
        "temp",
    }
    assert {path.name for path in layout.cache.iterdir()} == {"thumbnails", "previews"}
    for directory in layout.directories:
        result = verifier.verify_private_directory(directory, context="test.permissions")
        assert result.mechanism == "windows_protected_acl_v1"


def test_every_app_created_root_component_has_verified_private_permissions(tmp_path: Path) -> None:
    manager = CaseWorkspaceManager(profile_root=tmp_path, quota=_quota())
    manager.provision()
    verifier = WindowsPrivateDirectoryPermissions()

    current = tmp_path
    for component in DEFAULT_CASES_ROOT_PARTS:
        current = current / component
        verifier.verify_private_directory(current, context="test.permissions")


def test_usage_reservations_and_cleanup_use_logical_bytes_and_preserve_fixed_areas(
    tmp_path: Path,
) -> None:
    manager = CaseWorkspaceManager(
        profile_root=tmp_path,
        quota=_quota(case_bytes=10_000, cache_bytes=4_096, temp_bytes=4_096),
    )
    layout = manager.provision()
    cache_file = layout.thumbnails / f"{uuid4()}.bin"
    temp_file = layout.temp / f"{uuid4()}.tmp"
    cache_file.write_bytes(b"cache")
    with temp_file.open("wb") as stream:
        stream.seek(4_095)
        stream.write(b"\0")

    usage = manager.measure_usage(layout.case_id)
    assert usage.case_bytes == 4_101
    assert usage.cache_bytes == 5
    assert usage.temp_bytes == 4_096
    with pytest.raises(WorkspaceError) as raised:
        manager.reserve_capacity(
            layout.case_id,
            QuotaArea.TEMP,
            relative_path=f"temp/{uuid4()}.tmp",
            is_directory=False,
            byte_count=1,
        )
    assert raised.value.code is WorkspaceErrorCode.QUOTA_EXCEEDED

    cache_report = manager.cleanup_disposable(layout.case_id, CleanupTarget.CACHE)
    temp_report = manager.cleanup_disposable(layout.case_id, CleanupTarget.TEMP)
    assert cache_report.status is CleanupStatus.COMPLETE
    assert cache_report.removed_files == 1
    assert cache_report.released_bytes == 5
    assert temp_report.status is CleanupStatus.COMPLETE
    assert temp_report.removed_files == 1
    assert temp_report.released_bytes == 4_096
    assert layout.cache.is_dir()
    assert layout.thumbnails.is_dir()
    assert layout.previews.is_dir()
    assert layout.temp.is_dir()
    assert layout.originals.is_dir()
    assert layout.journal.is_dir()


def test_failed_mid_layout_permission_application_rolls_back_only_new_empty_case(
    tmp_path: Path,
) -> None:
    delegate = WindowsPrivateDirectoryPermissions()

    class FailingPermissions:
        def __init__(self) -> None:
            self.created = 0

        def create_private_directory(
            self,
            path: Path,
            *,
            context: str,
        ) -> PermissionVerification:
            self.created += 1
            # Four root components are provisioned before the case skeleton starts.
            if self.created == 8:
                raise WorkspaceError(
                    WorkspaceErrorCode.PERMISSIONS_UNSUPPORTED,
                    "test.permissions",
                    "Synthetic permission verification failure.",
                )
            return delegate.create_private_directory(path, context=context)

        def verify_private_directory(
            self,
            path: Path,
            *,
            context: str,
        ) -> PermissionVerification:
            return delegate.verify_private_directory(path, context=context)

    case_id = UUID("20000000-0000-4000-8000-000000000001")
    manager = CaseWorkspaceManager(
        profile_root=tmp_path,
        quota=_quota(),
        permissions=FailingPermissions(),
    )

    with pytest.raises(WorkspaceError) as raised:
        manager.provision(case_id)

    assert raised.value.code is WorkspaceErrorCode.PERMISSIONS_UNSUPPORTED
    assert not manager.layout(case_id).case_root.exists()
    assert manager.cases_root.is_dir()


def test_generated_case_id_collisions_are_bounded_and_never_adopt_existing_state(
    tmp_path: Path,
) -> None:
    case_id = UUID("20000000-0000-4000-8000-000000000002")
    creator = CaseWorkspaceManager(profile_root=tmp_path, quota=_quota())
    existing = creator.provision(case_id)
    marker = existing.temp / f"{uuid4()}.tmp"
    marker.write_bytes(b"existing")
    collider = CaseWorkspaceManager(
        profile_root=tmp_path,
        quota=_quota(),
        id_factory=lambda: case_id,
    )

    with pytest.raises(WorkspaceError) as raised:
        collider.provision()

    assert raised.value.code is WorkspaceErrorCode.ALREADY_EXISTS
    assert marker.read_bytes() == b"existing"


def test_empty_workspace_is_measurable_at_exact_structural_quota_floors(
    tmp_path: Path,
) -> None:
    manager = CaseWorkspaceManager(
        profile_root=tmp_path,
        quota=_quota(entries=6, depth=2),
    )
    layout = manager.provision()

    usage = manager.measure_usage(layout.case_id)

    assert usage.entry_count == 6
    assert usage.maximum_depth_seen == 2


def test_reservations_are_shared_by_case_identity_across_managers(tmp_path: Path) -> None:
    quota = _quota(case_bytes=10, cache_bytes=10, temp_bytes=10)
    first_manager = CaseWorkspaceManager(profile_root=tmp_path, quota=quota)
    second_manager = CaseWorkspaceManager(profile_root=tmp_path, quota=quota)
    layout = first_manager.provision()
    first = first_manager.reserve_capacity(
        layout.case_id,
        QuotaArea.CACHE,
        relative_path=f"cache/thumbnails/{uuid4()}.bin",
        is_directory=False,
        byte_count=6,
    )

    with pytest.raises(WorkspaceError) as raised:
        second_manager.reserve_capacity(
            layout.case_id,
            QuotaArea.CACHE,
            relative_path=f"cache/previews/{uuid4()}.bin",
            is_directory=False,
            byte_count=5,
        )
    first.release()

    assert raised.value.code is WorkspaceErrorCode.QUOTA_EXCEEDED


def test_capacity_reservation_rejects_intended_depth_before_mutation(tmp_path: Path) -> None:
    manager = CaseWorkspaceManager(profile_root=tmp_path, quota=_quota(depth=2))
    layout = manager.provision()
    accepted = manager.reserve_capacity(
        layout.case_id,
        QuotaArea.TEMP,
        relative_path="temp/a/item.bin",
        is_directory=False,
        byte_count=1,
    )
    assert accepted.relative_parts == ("temp", "a", "item.bin")
    assert not accepted.is_directory
    accepted.release()

    with pytest.raises(WorkspaceError) as file_over:
        manager.reserve_capacity(
            layout.case_id,
            QuotaArea.TEMP,
            relative_path="temp/a/b/item.bin",
            is_directory=False,
            byte_count=1,
        )
    with pytest.raises(WorkspaceError) as directory_over:
        manager.reserve_capacity(
            layout.case_id,
            QuotaArea.TEMP,
            relative_path="temp/a/b",
            is_directory=True,
            byte_count=0,
        )

    assert file_over.value.code is WorkspaceErrorCode.QUOTA_EXCEEDED
    assert directory_over.value.code is WorkspaceErrorCode.QUOTA_EXCEEDED
    assert not (layout.temp / "a").exists()

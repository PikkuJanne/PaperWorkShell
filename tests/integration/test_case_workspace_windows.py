"""Windows integration evidence for secure case-workspace provisioning and cleanup."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from uuid import UUID, uuid4

import pytest

from paperworkshell.storage._windows import FileIdentity
from paperworkshell.storage.errors import WorkspaceError, WorkspaceErrorCode
from paperworkshell.storage.permissions import (
    PermissionVerification,
    WindowsPrivateDirectoryPermissions,
)
from paperworkshell.storage.quotas import QuotaArea, WorkspaceQuota, WorkspaceUsage
from paperworkshell.storage.workspace import (
    DEFAULT_CASES_ROOT_PARTS,
    CaseWorkspaceManager,
    CaseWorkspacePaths,
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


def test_generated_case_id_collision_during_create_retries_without_adoption(
    tmp_path: Path,
) -> None:
    first_id = UUID("20000000-0000-4000-8000-000000000003")
    second_id = UUID("20000000-0000-4000-8000-000000000004")
    identifiers = iter((first_id, second_id))
    delegate = WindowsPrivateDirectoryPermissions()
    injected = False

    class RacingPermissions:
        def create_private_directory(
            self,
            path: Path,
            *,
            context: str,
        ) -> PermissionVerification:
            nonlocal injected
            if context == "case.root" and not injected:
                injected = True
                path.mkdir(mode=0o700)
                (path / "competitor.bin").write_bytes(b"competitor")
            return delegate.create_private_directory(path, context=context)

        def verify_private_directory(
            self,
            path: Path,
            *,
            context: str,
        ) -> PermissionVerification:
            return delegate.verify_private_directory(path, context=context)

    manager = CaseWorkspaceManager(
        profile_root=tmp_path,
        quota=_quota(),
        permissions=RacingPermissions(),
        id_factory=lambda: next(identifiers),
    )

    layout = manager.provision()

    assert layout.case_id == second_id
    assert (manager.layout(first_id).case_root / "competitor.bin").read_bytes() == b"competitor"


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


def test_authenticate_checkpoint_exits_while_case_coordination_is_contended(
    tmp_path: Path,
) -> None:
    manager = CaseWorkspaceManager(profile_root=tmp_path, quota=_quota())
    layout = manager.provision()
    reservation = manager.reserve_capacity(
        layout.case_id,
        QuotaArea.TEMP,
        relative_path=f"temp/{uuid4()}.tmp",
        is_directory=False,
        byte_count=1,
    )
    lock_held = Event()
    release_lock = Event()
    checkpoint_calls = 0

    def hold_case_coordination() -> None:
        with reservation.publication_guard(
            case_identity=reservation.case_identity,
            relative_parts=reservation.relative_parts,
            is_directory=False,
            byte_count=1,
            entry_count=1,
        ):
            lock_held.set()
            assert release_lock.wait(timeout=5)

    def expire_after_one_poll() -> None:
        nonlocal checkpoint_calls
        checkpoint_calls += 1
        if checkpoint_calls >= 2:
            raise TimeoutError("synthetic case-coordination deadline")

    with ThreadPoolExecutor(max_workers=1) as pool:
        holder = pool.submit(hold_case_coordination)
        assert lock_held.wait(timeout=2)
        try:
            with pytest.raises(TimeoutError, match="case-coordination deadline"):
                manager.authenticate_case(
                    layout.case_id,
                    retain_lease=True,
                    coordination_checkpoint=expire_after_one_poll,
                )
        finally:
            release_lock.set()
        holder.result(timeout=2)

    assert checkpoint_calls == 2
    with manager.authenticate_case(layout.case_id, retain_lease=True) as authority:
        assert authority.case_id == layout.case_id
    reservation.release()


def test_reserve_checkpoint_failure_after_acquire_releases_before_measurement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = CaseWorkspaceManager(profile_root=tmp_path, quota=_quota())
    layout = manager.provision()
    existing = manager.reserve_capacity(
        layout.case_id,
        QuotaArea.TEMP,
        relative_path=f"temp/{uuid4()}.tmp",
        is_directory=False,
        byte_count=1,
    )
    measurement_entered = Event()
    original_measure = manager._measure_usage_and_identity

    def observe_measurement(
        measured_layout: CaseWorkspacePaths,
    ) -> tuple[WorkspaceUsage, FileIdentity]:
        measurement_entered.set()
        return original_measure(measured_layout)

    monkeypatch.setattr(manager, "_measure_usage_and_identity", observe_measurement)
    checkpoint_calls = 0

    def fail_immediately_after_acquire() -> None:
        nonlocal checkpoint_calls
        checkpoint_calls += 1
        if checkpoint_calls == 2:
            raise RuntimeError("synthetic coordination cancellation")

    with pytest.raises(RuntimeError, match="coordination cancellation"):
        manager.reserve_capacity(
            layout.case_id,
            QuotaArea.TEMP,
            relative_path=f"temp/{uuid4()}.tmp",
            is_directory=False,
            byte_count=1,
            coordination_checkpoint=fail_immediately_after_acquire,
        )

    assert checkpoint_calls == 2
    assert not measurement_entered.is_set()

    def prove_lock_was_released() -> int:
        probe_calls = 0

        def fail_if_coordination_remains_locked() -> None:
            nonlocal probe_calls
            probe_calls += 1
            if probe_calls >= 4:
                raise TimeoutError("case coordination remained locked")

        with existing.publication_guard(
            case_identity=existing.case_identity,
            relative_parts=existing.relative_parts,
            is_directory=False,
            byte_count=1,
            entry_count=1,
            coordination_checkpoint=fail_if_coordination_remains_locked,
        ):
            return probe_calls

    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(prove_lock_was_released).result(timeout=2) == 2
    existing.release()


def test_publication_checkpoint_preserves_reentrancy_and_never_yields_after_failure(
    tmp_path: Path,
) -> None:
    manager = CaseWorkspaceManager(profile_root=tmp_path, quota=_quota())
    layout = manager.provision()
    reservation = manager.reserve_capacity(
        layout.case_id,
        QuotaArea.TEMP,
        relative_path=f"temp/{uuid4()}.tmp",
        is_directory=False,
        byte_count=1,
    )
    reentrant_checks = 0

    def record_reentrant_checkpoint() -> None:
        nonlocal reentrant_checks
        reentrant_checks += 1

    with reservation.publication_guard(
        case_identity=reservation.case_identity,
        relative_parts=reservation.relative_parts,
        is_directory=False,
        byte_count=1,
        entry_count=1,
        coordination_checkpoint=record_reentrant_checkpoint,
    ):
        with reservation.publication_guard(
            case_identity=reservation.case_identity,
            relative_parts=reservation.relative_parts,
            is_directory=False,
            byte_count=1,
            entry_count=1,
            coordination_checkpoint=record_reentrant_checkpoint,
        ):
            assert reservation.is_active
    assert reentrant_checks == 4

    failure_checks = 0
    yielded = False

    def fail_immediately_after_acquire() -> None:
        nonlocal failure_checks
        failure_checks += 1
        if failure_checks == 2:
            raise RuntimeError("synthetic post-acquire failure")

    with pytest.raises(RuntimeError, match="post-acquire failure"):
        with reservation.publication_guard(
            case_identity=reservation.case_identity,
            relative_parts=reservation.relative_parts,
            is_directory=False,
            byte_count=1,
            entry_count=1,
            coordination_checkpoint=fail_immediately_after_acquire,
        ):
            yielded = True

    assert failure_checks == 2
    assert not yielded
    reservation.release()


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
    assert accepted.byte_count == 1
    assert accepted.entry_count == 1
    with accepted.publication_guard(
        case_identity=accepted.case_identity,
        relative_parts=accepted.relative_parts,
        is_directory=False,
        byte_count=1,
        entry_count=1,
    ):
        assert accepted.is_active
    with pytest.raises(WorkspaceError) as amount_over:
        with accepted.publication_guard(
            case_identity=accepted.case_identity,
            relative_parts=accepted.relative_parts,
            is_directory=False,
            byte_count=2,
            entry_count=1,
        ):
            pytest.fail("an excessive publication claim was accepted")
    with pytest.raises(WorkspaceError) as scope_mismatch:
        with accepted.publication_guard(
            case_identity=accepted.case_identity,
            relative_parts=("temp", "different.bin"),
            is_directory=False,
            byte_count=1,
            entry_count=1,
        ):
            pytest.fail("a mismatched publication scope was accepted")
    accepted.release()
    with pytest.raises(WorkspaceError) as released:
        with accepted.publication_guard(
            case_identity=accepted.case_identity,
            relative_parts=accepted.relative_parts,
            is_directory=False,
            byte_count=1,
            entry_count=1,
        ):
            pytest.fail("a released publication claim was accepted")

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
    assert amount_over.value.code is WorkspaceErrorCode.QUOTA_EXCEEDED
    assert scope_mismatch.value.code is WorkspaceErrorCode.QUOTA_INVALID
    assert released.value.code is WorkspaceErrorCode.QUOTA_INVALID
    assert not (layout.temp / "a").exists()


def test_publication_and_release_cannot_make_a_concurrent_measurement_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    quota = _quota(case_bytes=10, cache_bytes=10, temp_bytes=10)
    first_manager = CaseWorkspaceManager(profile_root=tmp_path, quota=quota)
    second_manager = CaseWorkspaceManager(profile_root=tmp_path, quota=quota)
    layout = first_manager.provision()
    published_path = layout.temp / f"{uuid4()}.tmp"
    first = first_manager.reserve_capacity(
        layout.case_id,
        QuotaArea.TEMP,
        relative_path=f"temp/{published_path.name}",
        is_directory=False,
        byte_count=6,
    )
    measured = Event()
    published = Event()
    original_measure = second_manager._measure_usage_and_identity

    def pause_after_measurement(
        measured_layout: CaseWorkspacePaths,
    ) -> tuple[WorkspaceUsage, FileIdentity]:
        snapshot = original_measure(measured_layout)
        measured.set()
        assert published.wait(timeout=5)
        return snapshot

    monkeypatch.setattr(
        second_manager,
        "_measure_usage_and_identity",
        pause_after_measurement,
    )

    def reserve_from_paused_snapshot() -> WorkspaceErrorCode | None:
        try:
            second_manager.reserve_capacity(
                layout.case_id,
                QuotaArea.TEMP,
                relative_path=f"temp/{uuid4()}.tmp",
                is_directory=False,
                byte_count=5,
            )
        except WorkspaceError as error:
            return error.code
        return None

    def publish_then_release() -> None:
        assert measured.wait(timeout=5)
        published_path.write_bytes(b"123456")
        published.set()
        first.release()

    with ThreadPoolExecutor(max_workers=2) as pool:
        reserve_future = pool.submit(reserve_from_paused_snapshot)
        publish_future = pool.submit(publish_then_release)
        outcome = reserve_future.result(timeout=10)
        publish_future.result(timeout=10)

    assert outcome is WorkspaceErrorCode.QUOTA_EXCEEDED
    assert published_path.read_bytes() == b"123456"
    assert not first.is_active


def test_failed_measurement_cannot_poison_a_future_case_quota_policy(tmp_path: Path) -> None:
    case_id = UUID("20000000-0000-4000-8000-000000000005")
    first_manager = CaseWorkspaceManager(
        profile_root=tmp_path,
        quota=_quota(case_bytes=10, cache_bytes=10, temp_bytes=10),
    )

    with pytest.raises(WorkspaceError):
        first_manager.reserve_capacity(
            case_id,
            QuotaArea.TEMP,
            relative_path=f"temp/{uuid4()}.tmp",
            is_directory=False,
            byte_count=1,
        )

    second_manager = CaseWorkspaceManager(
        profile_root=tmp_path,
        quota=_quota(case_bytes=20, cache_bytes=20, temp_bytes=20),
    )
    layout = second_manager.provision(case_id)
    reservation = second_manager.reserve_capacity(
        case_id,
        QuotaArea.TEMP,
        relative_path=f"temp/{uuid4()}.tmp",
        is_directory=False,
        byte_count=1,
    )
    reservation.release()

    assert layout.case_id == case_id


def test_cleanup_rejects_an_active_reservation_in_the_selected_area(tmp_path: Path) -> None:
    manager = CaseWorkspaceManager(profile_root=tmp_path, quota=_quota())
    layout = manager.provision()
    marker = layout.temp / f"{uuid4()}.tmp"
    marker.write_bytes(b"preserved-while-active")
    reservation = manager.reserve_capacity(
        layout.case_id,
        QuotaArea.TEMP,
        relative_path=f"temp/{uuid4()}.tmp",
        is_directory=False,
        byte_count=1,
    )

    with pytest.raises(WorkspaceError) as raised:
        manager.cleanup_disposable(layout.case_id, CleanupTarget.TEMP)

    assert raised.value.code is WorkspaceErrorCode.CLEANUP_FAILED
    assert marker.read_bytes() == b"preserved-while-active"
    reservation.release()
    report = manager.cleanup_disposable(layout.case_id, CleanupTarget.TEMP)
    assert report.status is CleanupStatus.COMPLETE
    assert not marker.exists()

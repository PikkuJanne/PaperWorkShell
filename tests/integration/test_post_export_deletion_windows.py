"""Real retained-export handles and authenticated post-export case removal."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import UUID

import pytest

from paperworkshell.app.case_lifecycle import CaseLifecycleService
from paperworkshell.app.post_export_deletion import PostExportDeletionService
from paperworkshell.domain.case_lifecycle import (
    CaseDeletionStatus,
    CaseLifecycleErrorCode,
)
from paperworkshell.domain.post_export_deletion import (
    PostExportDeletionConfirmation,
    PostExportDeletionError,
    PostExportDeletionErrorCode,
)
from paperworkshell.storage._windows import WindowsHandle
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.errors import WorkspaceError, WorkspaceErrorCode
from paperworkshell.storage.manifest_repository import WindowsManifestRepository
from paperworkshell.storage.post_export_deletion import (
    PreservedExportArtifact,
    WindowsPostExportDeletionStore,
    WindowsRetainedExportGuard,
)
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.workspace import CaseWorkspaceManager

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows authenticated storage boundary")
_CONFIRM = PostExportDeletionConfirmation.REMOVE_AFTER_VERIFIED_EXPORT


def _manager(tmp_path: Path) -> CaseWorkspaceManager:
    profile = tmp_path / "profile"
    profile.mkdir()
    return CaseWorkspaceManager(
        profile_root=profile, quota=WorkspaceQuota(4_000_000, 1_000_000, 1_000_000, 256, 16)
    )


def _artifact(
    path: Path, payload: bytes = b"synthetic intended output\n"
) -> PreservedExportArtifact:
    path.write_bytes(payload)
    return PreservedExportArtifact(path, len(payload), hashlib.sha256(payload).hexdigest())


def test_export_guard_pins_file_and_ancestor_then_releases(tmp_path: Path) -> None:
    parent = tmp_path / "exports"
    parent.mkdir()
    artifact = _artifact(parent / "packet.txt")
    guard = WindowsRetainedExportGuard((artifact,), forbidden_root=tmp_path / "cases")
    with guard.guard():
        with pytest.raises(OSError):
            artifact.path.write_bytes(b"changed")
        with pytest.raises(OSError):
            artifact.path.unlink()
        with pytest.raises(OSError):
            parent.rename(tmp_path / "moved")
    artifact.path.unlink()
    parent.rmdir()


def test_guard_preserves_caller_exception_and_releases_handles(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path / "packet.txt")
    guard = WindowsRetainedExportGuard((artifact,), forbidden_root=tmp_path / "cases")
    with pytest.raises(PermissionError, match="access revoked"):
        with guard.guard():
            raise PermissionError("access revoked")
    artifact.path.unlink()


@pytest.mark.parametrize("probe", ["cancellation", "deadline"])
def test_guard_operation_probes_release_handles_before_any_removal(
    tmp_path: Path, probe: str
) -> None:
    artifact = _artifact(tmp_path / "packet.txt")
    guard = WindowsRetainedExportGuard((artifact,), forbidden_root=tmp_path / "cases")
    with pytest.raises(PostExportDeletionError) as raised:
        if probe == "cancellation":
            with guard.guard(cancellation=lambda: True):
                pytest.fail("cancelled guard granted access")
        else:
            with guard.guard(deadline=lambda: True):
                pytest.fail("expired guard granted access")
    assert raised.value.code is (
        PostExportDeletionErrorCode.CANCELLED
        if probe == "cancellation"
        else PostExportDeletionErrorCode.TIMED_OUT
    )
    artifact.path.unlink()


@pytest.mark.parametrize("condition", ["changed", "missing", "inside_case", "hardlink"])
def test_guard_rejects_unavailable_or_unsafe_output(tmp_path: Path, condition: str) -> None:
    forbidden = tmp_path / "cases"
    forbidden.mkdir()
    path = (forbidden if condition == "inside_case" else tmp_path) / "packet.txt"
    artifact = _artifact(path)
    if condition == "changed":
        path.write_bytes(b"different same-size text!\n")
    elif condition == "missing":
        path.unlink()
    elif condition == "hardlink":
        os.link(path, tmp_path / "alias.txt")
    with pytest.raises(PostExportDeletionError) as raised:
        with WindowsRetainedExportGuard((artifact,), forbidden_root=forbidden).guard():
            pytest.fail("unsafe output guard granted access")
    assert raised.value.code is PostExportDeletionErrorCode.EXPORT_UNAVAILABLE
    assert str(path) not in str(raised.value)


def test_real_removal_preserves_external_exports_and_original_input(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    lifecycle = CaseLifecycleService(WindowsCaseLifecycleStore(manager))
    selected = lifecycle.create_case().case_id
    snapshot = WindowsManifestRepository(manager).load(selected).snapshot
    original_input = tmp_path / "source.txt"
    original_input.write_bytes(b"synthetic external input")
    artifact = _artifact(tmp_path / "packet.txt")
    guard = WindowsRetainedExportGuard((artifact,), forbidden_root=manager.cases_root)

    def close(case_id: UUID) -> None:
        lifecycle.close_case(case_id)

    service = PostExportDeletionService(
        WindowsPostExportDeletionStore(manager),
        require_access=lambda _case_id: None,
        close_case=close,
    )
    service.remember_verified_export(
        snapshot,
        export_count=1,
        export_names=(artifact.path.name,),
        guard_exports=guard.guard,
    )
    review = service.prepare(snapshot)
    result = service.confirm(review.review_id, snapshot, _CONFIRM)
    assert result.status is CaseDeletionStatus.COMPLETE
    assert not manager.layout(selected).case_root.exists()
    assert artifact.path.read_bytes() == b"synthetic intended output\n"
    assert original_input.read_bytes() == b"synthetic external input"
    assert lifecycle.active_case is None


def test_actual_manifest_change_prevents_removal_even_with_old_snapshot(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    lifecycle = CaseLifecycleService(WindowsCaseLifecycleStore(manager))
    selected = lifecycle.create_case().case_id
    repository = WindowsManifestRepository(manager)
    snapshot = repository.load(selected).snapshot
    store = WindowsPostExportDeletionStore(manager)
    store.inspect(snapshot)
    # A same-version authoritative change is rejected by the storage boundary.
    payload = manager.layout(selected).manifest.read_bytes()
    changed = payload.replace(b'"revision":0', b'"revision":1')
    manager.layout(selected).manifest.write_bytes(changed)
    lifecycle.close_case(selected)
    with pytest.raises(PostExportDeletionError):
        store.delete(snapshot)
    assert manager.layout(selected).manifest.read_bytes() == changed


def test_partial_removal_restart_is_unavailable_and_never_auto_resumes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    lifecycle = CaseLifecycleService(WindowsCaseLifecycleStore(manager))
    selected = lifecycle.create_case().case_id
    snapshot = WindowsManifestRepository(manager).load(selected).snapshot
    artifact = _artifact(tmp_path / "packet.txt")
    guard = WindowsRetainedExportGuard((artifact,), forbidden_root=manager.cases_root)

    def close(case_id: UUID) -> None:
        lifecycle.close_case(case_id)

    service = PostExportDeletionService(
        WindowsPostExportDeletionStore(manager),
        require_access=lambda _case_id: None,
        close_case=close,
    )
    service.remember_verified_export(
        snapshot,
        export_count=1,
        export_names=(artifact.path.name,),
        guard_exports=guard.guard,
    )
    review = service.prepare(snapshot)
    original = WindowsHandle.mark_for_deletion
    calls = 0

    def fail_second(handle: WindowsHandle) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise WorkspaceError(
                WorkspaceErrorCode.CLEANUP_FAILED, "test.remove", "Synthetic deletion interruption."
            )
        original(handle)

    monkeypatch.setattr(WindowsHandle, "mark_for_deletion", fail_second)
    result = service.confirm(review.review_id, snapshot, _CONFIRM)
    assert result.status is CaseDeletionStatus.PARTIAL
    assert result.removed_files == 1
    assert not manager.layout(selected).manifest.exists()
    fresh = CaseLifecycleService(WindowsCaseLifecycleStore(manager))
    index = fresh.list_cases()
    assert index.cases == ()
    assert index.unavailable[0].code is CaseLifecycleErrorCode.CASE_INCOMPLETE
    restarted = PostExportDeletionService(
        WindowsPostExportDeletionStore(manager),
        require_access=lambda _case_id: None,
        close_case=close,
    )
    with pytest.raises(PostExportDeletionError) as raised:
        restarted.confirm(review.review_id, snapshot, _CONFIRM)
    assert raised.value.code is PostExportDeletionErrorCode.VERIFIED_EXPORT_REQUIRED
    assert calls == 2
    assert artifact.path.read_bytes() == b"synthetic intended output\n"

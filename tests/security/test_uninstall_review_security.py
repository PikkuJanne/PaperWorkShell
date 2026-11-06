"""Synthetic Windows deletion boundaries and fail-closed uninstall regressions."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from paperworkshell.app.uninstall_review import UninstallReviewService
from paperworkshell.domain.uninstall_review import (
    UninstallDeletionConfirmation,
    UninstallReviewError,
    UninstallReviewErrorCode,
)
from paperworkshell.storage._windows import WindowsHandle
from paperworkshell.storage.case_lifecycle import WindowsCaseLifecycleStore
from paperworkshell.storage.errors import WorkspaceError, WorkspaceErrorCode
from paperworkshell.storage.quotas import WorkspaceQuota
from paperworkshell.storage.uninstall_review import (
    WindowsUninstallCaseStore,
    uninstall_application_guard,
)
from paperworkshell.storage.workspace import CaseWorkspaceManager

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows authenticated uninstall storage")
CONFIRM = UninstallDeletionConfirmation.DELETE_REVIEWED_MANAGED_CASES


def _manager(tmp_path: Path) -> CaseWorkspaceManager:
    profile = tmp_path / "profile"
    profile.mkdir()
    return CaseWorkspaceManager(
        profile_root=profile,
        quota=WorkspaceQuota(4_000_000, 1_000_000, 1_000_000, 256, 16),
    )


def test_explicit_removal_preserves_external_original_export_and_unknown_entry(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    lifecycle = WindowsCaseLifecycleStore(manager)
    lease = lifecycle.create_case()
    selected = lease.summary.case_id
    lease.close()
    outside = tmp_path / "synthetic-source.bin"
    exported = tmp_path / "synthetic-export.bin"
    unknown = manager.cases_root / "unknown-entry.bin"
    for sentinel in (outside, exported, unknown):
        sentinel.write_bytes(b"synthetic preservation sentinel")
    service = UninstallReviewService(WindowsUninstallCaseStore(manager))
    review = service.prepare()
    assert manager.layout(selected).manifest.exists()
    result = service.confirm(review.review_id, CONFIRM, cancellation=lambda: False)
    assert result.deleted_count == 1 and result.failed_count == 0
    assert not manager.layout(selected).case_root.exists()
    for sentinel in (outside, exported, unknown):
        assert sentinel.read_bytes() == b"synthetic preservation sentinel"


@pytest.mark.parametrize("variant", ["future", "old", "corrupt"])
def test_unsupported_or_corrupt_manifest_is_not_migrated_recovered_or_deleted(
    tmp_path: Path, variant: str
) -> None:
    manager = _manager(tmp_path)
    lease = WindowsCaseLifecycleStore(manager).create_case()
    selected = lease.summary.case_id
    lease.close()
    path = manager.layout(selected).manifest
    payload = json.loads(path.read_bytes())
    payload["schema_version"] = 4 if variant == "future" else 2
    changed = b"{" if variant == "corrupt" else json.dumps(payload).encode()
    path.write_bytes(changed)
    service = UninstallReviewService(WindowsUninstallCaseStore(manager))
    review = service.prepare()
    assert review.inventory.cases == () and review.inventory.unavailable_count == 1
    result = service.confirm(review.review_id, CONFIRM, cancellation=lambda: False)
    assert result.deleted_count == 0 and result.remaining_count == 1
    assert path.read_bytes() == changed


def test_changed_reviewed_manifest_refuses_deletion_without_mutation(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    lease = WindowsCaseLifecycleStore(manager).create_case()
    selected = lease.summary.case_id
    lease.close()
    service = UninstallReviewService(WindowsUninstallCaseStore(manager))
    review = service.prepare()
    path = manager.layout(selected).manifest
    document = json.loads(path.read_bytes())
    document["revision"] = 1
    changed = json.dumps(document).encode()
    path.write_bytes(changed)
    result = service.confirm(review.review_id, CONFIRM, cancellation=lambda: False)
    assert result.deleted_count == 0 and result.failed_count == 1
    assert path.read_bytes() == changed


def test_active_case_is_retained_and_requires_new_review_after_failure(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    lease = WindowsCaseLifecycleStore(manager).create_case()
    try:
        service = UninstallReviewService(WindowsUninstallCaseStore(manager))
        review = service.prepare()
        result = service.confirm(review.review_id, CONFIRM, cancellation=lambda: False)
        assert result.deleted_count == 0 and result.failed_count == 1
        assert manager.layout(lease.summary.case_id).manifest.exists()
        with pytest.raises(UninstallReviewError) as raised:
            service.confirm(review.review_id, CONFIRM, cancellation=lambda: False)
        assert raised.value.code is UninstallReviewErrorCode.REVIEW_STALE
    finally:
        lease.close()


def test_hardlinked_case_leaf_cannot_delete_external_target(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    lease = WindowsCaseLifecycleStore(manager).create_case()
    selected = lease.summary.case_id
    lease.close()
    outside = tmp_path / "synthetic-external.bin"
    outside.write_bytes(b"synthetic external preservation target")
    linked = manager.layout(selected).temp / "aliased.bin"
    try:
        os.link(outside, linked)
    except OSError:
        pytest.skip("Test volume does not provide hard links")
    service = UninstallReviewService(WindowsUninstallCaseStore(manager))
    review = service.prepare()
    before = hashlib.sha256(manager.layout(selected).manifest.read_bytes()).hexdigest()
    result = service.confirm(review.review_id, CONFIRM, cancellation=lambda: False)
    assert result.failed_count == 1 and result.deleted_count == 0
    assert hashlib.sha256(manager.layout(selected).manifest.read_bytes()).hexdigest() == before
    assert outside.read_bytes() == b"synthetic external preservation target"


def test_replaced_case_with_identical_manifest_does_not_consume_old_consent(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    lease = WindowsCaseLifecycleStore(manager).create_case()
    selected = lease.summary.case_id
    lease.close()
    service = UninstallReviewService(WindowsUninstallCaseStore(manager))
    review = service.prepare()
    layout = manager.layout(selected)
    before = layout.manifest.read_bytes()
    original = layout.case_root.with_name(str(selected) + ".retained")
    layout.case_root.rename(original)
    manager.provision(selected)
    layout.manifest.write_bytes(before)
    result = service.confirm(review.review_id, CONFIRM, cancellation=lambda: False)
    assert result.deleted_count == 0 and result.failed_count == 1
    assert layout.manifest.read_bytes() == before
    assert (original / "manifest.json").read_bytes() == before


def test_application_mutex_refuses_a_separate_review_process_before_storage(tmp_path: Path) -> None:
    probe = (
        "import paperworkshell.ui.uninstall_review as ui\n"
        "def unexpected():\n"
        "    raise RuntimeError('INSPECTION_REACHED')\n"
        "ui.current_user_uninstall_store = unexpected\n"
        "raise SystemExit(ui.main([]))\n"
    )
    with uninstall_application_guard():
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", probe],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    assert result.returncode == 2
    assert "UNINSTALL_APPLICATION_RUNNING" in result.stderr
    assert "INSPECTION_REACHED" not in result.stderr and result.stdout == ""


def test_application_mutex_releases_after_exception_and_rejects_nested_review() -> None:
    with pytest.raises(RuntimeError):
        with uninstall_application_guard():
            with pytest.raises(UninstallReviewError) as raised:
                with uninstall_application_guard():
                    pytest.fail("Nested destructive review acquired the application mutex")
            assert raised.value.code is UninstallReviewErrorCode.APPLICATION_RUNNING
            raise RuntimeError("Synthetic caller interruption")
    with uninstall_application_guard():
        pass


def test_partial_removal_is_counted_and_cannot_resume_automatically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path)
    lease = WindowsCaseLifecycleStore(manager).create_case()
    selected = lease.summary.case_id
    lease.close()
    service = UninstallReviewService(WindowsUninstallCaseStore(manager))
    review = service.prepare()
    original = WindowsHandle.mark_for_deletion
    calls = 0

    def fail_after_manifest(handle: WindowsHandle) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise WorkspaceError(
                WorkspaceErrorCode.CLEANUP_FAILED, "test.partial", "Synthetic failure"
            )
        original(handle)

    monkeypatch.setattr(WindowsHandle, "mark_for_deletion", fail_after_manifest)
    result = service.confirm(review.review_id, CONFIRM, cancellation=lambda: False)
    assert result.deleted_count == 0 and result.remaining_count == 1 and result.failed_count == 1
    assert not manager.layout(selected).manifest.exists()
    fresh = UninstallReviewService(WindowsUninstallCaseStore(manager)).prepare()
    assert fresh.inventory.cases == () and fresh.inventory.unavailable_count == 1
    assert calls == 2


def test_review_cancellation_reads_no_manifest_and_changes_nothing(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    lease = WindowsCaseLifecycleStore(manager).create_case()
    selected = lease.summary.case_id
    lease.close()
    before = manager.layout(selected).manifest.read_bytes()
    with pytest.raises(UninstallReviewError) as raised:
        WindowsUninstallCaseStore(manager).inspect(cancellation=lambda: True)
    assert raised.value.code is UninstallReviewErrorCode.CANCELLED
    assert manager.layout(selected).manifest.read_bytes() == before

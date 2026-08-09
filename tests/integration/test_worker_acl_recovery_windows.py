from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
import traceback
from ctypes import wintypes
from dataclasses import replace
from pathlib import Path
from secrets import token_hex

import pytest

from paperworkshell.worker.client import _windows as windows_module
from paperworkshell.worker.client._acl_recovery import (
    AclRecoveryJournal,
    AclRecoveryJournalError,
    AclRecoveryObject,
    AclRecoveryPhase,
)
from paperworkshell.worker.client._windows import (
    WindowsLaunchError,
    WindowsLaunchErrorCode,
    _AclMutationMutex,
    _AppContainerProfile,
    _PartialAppContainerProfile,
    _sid_access_mask,
    _sid_from_string,
    recover_worker_acl_access,
)
from tests.integration.test_worker_windows_containment import _dacl_sddl


def _recovery_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "worker-acl-recovery"
    monkeypatch.setattr(windows_module, "_acl_recovery_directory_path", lambda: root)
    return root


def _derive_profile_sid(profile_name: str) -> ctypes.c_void_p:
    derive = windows_module._userenv().DeriveAppContainerSidFromAppContainerName
    derive.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p))
    derive.restype = ctypes.c_long
    sid = ctypes.c_void_p()
    assert derive(profile_name, ctypes.byref(sid)) == 0
    assert sid.value
    return sid


def test_cold_restart_repairs_durable_acl_inventory_before_new_grant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery_root = _recovery_root(tmp_path, monkeypatch)
    approved = tmp_path / "approved-runtime"
    approved.mkdir()
    child = approved / "module.py"
    child.write_text("VALUE = 1\n", encoding="utf-8")
    before = (_dacl_sddl(approved), _dacl_sddl(child))
    profile = _AppContainerProfile.create((approved,))
    try:
        profile.grant_read_execute((approved,))
        assert _sid_access_mask(approved, profile.sid)
        assert len(AclRecoveryJournal(recovery_root).inventory()) == 1

        monkeypatch.setattr(windows_module, "_ledger_owner_is_active", lambda *_args: False)
        result = recover_worker_acl_access((approved,))

        assert result.journal_entries == 1
        assert result.repaired_leases == 1
        assert result.active_leases == 0
        assert not _sid_access_mask(approved, profile.sid)
        assert AclRecoveryJournal(recovery_root).inventory() == ()
        assert (
            _dacl_sddl(approved),
            _dacl_sddl(child),
        ) == before
    finally:
        assert profile.close_no_raise()


def test_prepared_recovery_record_is_durable_before_first_acl_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery_root = _recovery_root(tmp_path, monkeypatch)
    approved = tmp_path / "approved-runtime"
    approved.mkdir()
    profile = _AppContainerProfile.create((approved,))
    original_change = windows_module._change_sid_access
    observed_phases: list[AclRecoveryPhase] = []

    def observe_journal_before_mutation(
        path: Path,
        sid: ctypes.c_void_p,
        mode: int,
    ) -> None:
        entries = AclRecoveryJournal(recovery_root).inventory()
        assert len(entries) == 1
        observed_phases.append(entries[0].phase)
        original_change(path, sid, mode)

    monkeypatch.setattr(windows_module, "_change_sid_access", observe_journal_before_mutation)
    try:
        profile.grant_read_execute((approved,))
        assert observed_phases[0] is AclRecoveryPhase.PREPARED
    finally:
        monkeypatch.setattr(windows_module, "_change_sid_access", original_change)
        assert profile.close_no_raise()


def test_preexisting_insecure_recovery_directory_blocks_before_inventory_use(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery_root = _recovery_root(tmp_path, monkeypatch)
    recovery_root.mkdir()
    approved = tmp_path / "approved-runtime"
    approved.mkdir()

    with pytest.raises(WindowsLaunchError) as raised:
        recover_worker_acl_access((approved,))

    assert raised.value.code is WindowsLaunchErrorCode.ACCESS_GRANT_FAILED
    assert raised.value.context == "access.recovery_policy"
    assert list(recovery_root.iterdir()) == []


def test_recovery_identity_mismatch_fails_closed_without_acl_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery_root = _recovery_root(tmp_path, monkeypatch)
    approved = tmp_path / "approved-runtime"
    approved.mkdir()
    child = approved / "module.py"
    child.write_text("VALUE = 1\n", encoding="utf-8")
    profile = _AppContainerProfile.create((approved,))
    journal = AclRecoveryJournal(recovery_root)
    try:
        profile.grant_read_execute((approved,))
        original = journal.inventory()[0]
        first = original.objects[0]
        forged = replace(
            original,
            objects=(
                AclRecoveryObject(
                    path=first.path,
                    device=first.device,
                    inode=first.inode + 1,
                    is_directory=first.is_directory,
                    auto_inherited=first.auto_inherited,
                ),
                *original.objects[1:],
            ),
        )
        journal.write(forged)
        monkeypatch.setattr(windows_module, "_ledger_owner_is_active", lambda *_args: False)

        with pytest.raises(WindowsLaunchError) as raised:
            recover_worker_acl_access((approved,))

        assert raised.value.code is WindowsLaunchErrorCode.ACCESS_GRANT_FAILED
        assert raised.value.context == "access.recovery_identity"
        assert _sid_access_mask(approved, profile.sid)
        assert journal.inventory() == (forged,)

        journal.write(original)
        assert recover_worker_acl_access((approved,)).repaired_leases == 1
    finally:
        assert profile.close_no_raise()


def test_profile_name_and_sid_mismatch_is_retained_without_acl_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery_root = _recovery_root(tmp_path, monkeypatch)
    approved = tmp_path / "approved-runtime"
    approved.mkdir()
    granted = _AppContainerProfile.create((approved,))
    different = _AppContainerProfile.create((approved,))
    journal = AclRecoveryJournal(recovery_root)
    try:
        granted.grant_read_execute((approved,))
        original = journal.inventory()[0]
        forged = replace(original, sid=windows_module._sid_string(different.sid))
        journal.write(forged)
        before = _dacl_sddl(approved)
        monkeypatch.setattr(windows_module, "_ledger_owner_is_active", lambda *_args: False)

        with pytest.raises(WindowsLaunchError) as raised:
            recover_worker_acl_access((approved,))

        assert raised.value.code is WindowsLaunchErrorCode.ACCESS_GRANT_FAILED
        assert raised.value.context == "access.recovery_identity"
        assert _dacl_sddl(approved) == before
        assert _sid_access_mask(approved, granted.sid)
        assert _sid_access_mask(approved, different.sid) == 0
        assert journal.inventory() == (forged,)

        journal.write(original)
        assert recover_worker_acl_access((approved,)).repaired_leases == 1
    finally:
        assert different.close_no_raise()
        assert granted.close_no_raise()


def test_live_owner_profile_name_and_sid_mismatch_blocks_before_phase_skip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery_root = _recovery_root(tmp_path, monkeypatch)
    approved = tmp_path / "approved-runtime"
    approved.mkdir()
    granted = _AppContainerProfile.create((approved,))
    different = _AppContainerProfile.create((approved,))
    journal = AclRecoveryJournal(recovery_root)
    try:
        granted.grant_read_execute((approved,))
        original = journal.inventory()[0]
        assert original.phase is AclRecoveryPhase.ACTIVE
        forged = replace(original, sid=windows_module._sid_string(different.sid))
        journal.write(forged)
        before = _dacl_sddl(approved)

        with pytest.raises(WindowsLaunchError) as raised:
            recover_worker_acl_access((approved,))

        assert raised.value.code is WindowsLaunchErrorCode.ACCESS_GRANT_FAILED
        assert raised.value.context == "access.recovery_identity"
        assert _dacl_sddl(approved) == before
        assert _sid_access_mask(approved, granted.sid)
        assert _sid_access_mask(approved, different.sid) == 0
        assert journal.inventory() == (forged,)

        journal.write(original)
    finally:
        assert different.close_no_raise()
        assert granted.close_no_raise()


def test_live_entry_with_different_current_allowlist_blocks_new_acl_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _recovery_root(tmp_path, monkeypatch)
    approved = tmp_path / "approved-runtime"
    approved.mkdir()
    different = tmp_path / "different-runtime"
    different.mkdir()
    profile = _AppContainerProfile.create((approved,))
    try:
        profile.grant_read_execute((approved,))

        with pytest.raises(WindowsLaunchError) as raised:
            recover_worker_acl_access((different,))

        assert raised.value.context == "access.recovery_allowlist"
        assert _sid_access_mask(approved, profile.sid)
    finally:
        assert profile.close_no_raise()


def test_corrupt_recovery_record_is_isolated_and_blocks_new_acl_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery_root = _recovery_root(tmp_path, monkeypatch)
    approved = tmp_path / "approved-runtime"
    approved.mkdir()
    profile = _AppContainerProfile.create((approved,))
    journal = AclRecoveryJournal(recovery_root)
    try:
        profile.grant_read_execute((approved,))
        entry = journal.inventory()[0]
        record = recovery_root / f"worker-acl-{entry.profile_id}.json"
        original_payload = record.read_bytes()
        record.write_bytes(b'{"journal_version":1')
        monkeypatch.setattr(windows_module, "_ledger_owner_is_active", lambda *_args: False)

        with pytest.raises(WindowsLaunchError) as raised:
            recover_worker_acl_access((approved,))

        assert raised.value.context == "access.recovery_journal"
        assert _sid_access_mask(approved, profile.sid)
        assert record.read_bytes() == b'{"journal_version":1'

        record.write_bytes(original_payload)
        assert recover_worker_acl_access((approved,)).repaired_leases == 1
    finally:
        assert profile.close_no_raise()


def test_recovery_journal_delete_failure_has_no_path_bearing_exception_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _recovery_root(tmp_path, monkeypatch)
    approved = tmp_path / "approved-runtime"
    approved.mkdir()
    profile = _AppContainerProfile.create((approved,))
    original_delete = AclRecoveryJournal.delete
    canary = os.fspath(tmp_path / "PRIVATE_RECOVERY_PATH_CANARY")

    def fail_delete(_journal: AclRecoveryJournal, _profile_id: str) -> None:
        try:
            raise OSError(5, "synthetic private journal failure", canary)
        except OSError as error:
            raise AclRecoveryJournalError("delete.filesystem") from error

    try:
        profile.grant_read_execute((approved,))
        monkeypatch.setattr(windows_module, "_ledger_owner_is_active", lambda *_args: False)
        monkeypatch.setattr(AclRecoveryJournal, "delete", fail_delete)

        with pytest.raises(WindowsLaunchError) as raised:
            recover_worker_acl_access((approved,))

        rendered = "".join(traceback.format_exception(raised.value))
        assert raised.value.code is WindowsLaunchErrorCode.ACCESS_GRANT_FAILED
        assert raised.value.context == "access.recovery_journal"
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None
        assert canary not in str(raised.value)
        assert canary not in repr(raised.value)
        assert canary not in rendered
    finally:
        monkeypatch.setattr(AclRecoveryJournal, "delete", original_delete)
        recover_worker_acl_access((approved,))
        assert profile.close_no_raise()


def test_hardlinked_runtime_file_is_rejected_before_any_sid_grant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _recovery_root(tmp_path, monkeypatch)
    approved = tmp_path / "approved-runtime"
    approved.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "outside.py"
    outside_file.write_text("VALUE = 1\n", encoding="utf-8")
    alias = approved / "module.py"
    os.link(outside_file, alias)
    profile = _AppContainerProfile.create((approved,))
    try:
        with pytest.raises(WindowsLaunchError) as raised:
            profile.grant_read_execute((approved,))

        assert raised.value.code is WindowsLaunchErrorCode.CONFIGURATION_INVALID
        assert raised.value.context == "config.resource_identity"
        assert _sid_access_mask(approved, profile.sid) == 0
        assert _sid_access_mask(outside_file, profile.sid) == 0
    finally:
        assert profile.close_no_raise()


def test_replaced_runtime_file_is_revalidated_after_prepared_cutpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _recovery_root(tmp_path, monkeypatch)
    approved = tmp_path / "approved-runtime"
    approved.mkdir()
    child = approved / "module.py"
    child.write_text("VALUE = 1\n", encoding="utf-8")
    retained = tmp_path / "retained-module.py"
    outside = tmp_path / "outside.py"
    outside.write_text("OUTSIDE = 1\n", encoding="utf-8")
    original_capture = windows_module._capture_acl_recovery_object
    replaced = False

    def replace_after_capture(path: Path) -> AclRecoveryObject:
        nonlocal replaced
        captured = original_capture(path)
        if path == child:
            child.replace(retained)
            os.link(outside, child)
            replaced = True
        return captured

    monkeypatch.setattr(
        windows_module,
        "_capture_acl_recovery_object",
        replace_after_capture,
    )
    profile = _AppContainerProfile.create((approved,))
    try:
        with pytest.raises(WindowsLaunchError) as raised:
            profile.grant_read_execute((approved,))

        assert replaced
        assert raised.value.code is WindowsLaunchErrorCode.ACCESS_GRANT_FAILED
        assert raised.value.context == "access.recovery_identity"
        assert _sid_access_mask(outside, profile.sid) == 0
    finally:
        if child.exists():
            child.unlink()
        retained.replace(child)
        assert profile.close_no_raise()


@pytest.mark.parametrize(
    ("mode", "expected_phase"),
    (
        ("prepared", AclRecoveryPhase.PREPARED),
        ("partial_grant", AclRecoveryPhase.PREPARED),
        ("revoking", AclRecoveryPhase.REVOKING),
    ),
)
def test_forced_owner_exit_repairs_interrupted_acl_cutpoint(
    mode: str,
    expected_phase: AclRecoveryPhase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery_root = _recovery_root(tmp_path, monkeypatch)
    roots = (tmp_path / "approved-a", tmp_path / "approved-b")
    for index, root in enumerate(roots):
        root.mkdir()
        (root / f"module-{index}.py").write_text("VALUE = 1\n", encoding="utf-8")
    observed_paths = tuple(path for root in roots for path in (root, *root.iterdir()))
    before = tuple(_dacl_sddl(path) for path in observed_paths)
    ready = tmp_path / "owner-ready"
    helper = Path(__file__).parents[1] / "helpers" / "worker_acl_abandoned_owner.py"
    mutex_name = f"Global\\PaperWorkShell.Test.WorkerAcl.{token_hex(12)}"
    monkeypatch.setattr(windows_module, "_ACL_MUTATION_MUTEX_NAME", mutex_name)
    retained_mutex = _AclMutationMutex(recover_abandoned=True)
    process = subprocess.Popen(  # noqa: S603 - interpreter and helper are fixed
        (
            sys.executable,
            os.fspath(helper),
            mode,
            os.fspath(recovery_root),
            os.fspath(ready),
            mutex_name,
            *(os.fspath(root) for root in roots),
        ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    entry = None
    orphan_cleaned = False
    try:
        deadline = time.monotonic() + 20.0
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.is_file()
        assert process.poll() is None
        entries = AclRecoveryJournal(recovery_root).inventory()
        assert len(entries) == 1
        entry = entries[0]
        assert entry.phase is expected_phase
        assert ready.read_text(encoding="ascii") == entry.profile_name

        interrupted_sid = _sid_from_string(entry.sid)
        try:
            root_masks = tuple(_sid_access_mask(root, interrupted_sid) for root in roots)
        finally:
            windows_module._local_free(interrupted_sid)
        if mode == "prepared":
            assert root_masks == (0, 0)
        else:
            assert root_masks[0] != 0 and root_masks[1] == 0

        process.kill()
        assert process.wait(timeout=10.0) != 0
        observed_abandonment: list[bool] = []
        original_recover = windows_module._recover_acl_entries_locked

        def capture_abandonment(
            allowed_roots: tuple[Path, ...],
            *,
            mutex_was_abandoned: bool,
        ) -> windows_module.WindowsWorkerAclRecoveryResult:
            observed_abandonment.append(mutex_was_abandoned)
            return original_recover(
                allowed_roots,
                mutex_was_abandoned=mutex_was_abandoned,
            )

        monkeypatch.setattr(
            windows_module,
            "_recover_acl_entries_locked",
            capture_abandonment,
        )
        result = recover_worker_acl_access(roots)
        monkeypatch.setattr(
            windows_module,
            "_recover_acl_entries_locked",
            original_recover,
        )

        assert observed_abandonment == [True]
        assert result.repaired_leases == 1
        repaired_sid = _sid_from_string(entry.sid)
        try:
            assert all(_sid_access_mask(item.path, repaired_sid) == 0 for item in entry.objects)
        finally:
            windows_module._local_free(repaired_sid)
        assert tuple(_dacl_sddl(path) for path in observed_paths) == before
        assert AclRecoveryJournal(recovery_root).inventory() == ()

        orphan_sid = _derive_profile_sid(entry.profile_name)
        assert windows_module._sid_string(orphan_sid) == entry.sid
        orphan = _PartialAppContainerProfile(name=entry.profile_name, sid=orphan_sid)
        assert orphan.close_no_raise()
        orphan_cleaned = True
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10.0)
        if entry is not None and not orphan_cleaned:
            cleanup_sid = _derive_profile_sid(entry.profile_name)
            assert windows_module._sid_string(cleanup_sid) == entry.sid
            _PartialAppContainerProfile(
                name=entry.profile_name,
                sid=cleanup_sid,
            ).close_no_raise()
        assert retained_mutex._handle.close_no_raise()


def test_forced_owner_exit_repairs_abandoned_mutex_from_a_separate_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery_root = _recovery_root(tmp_path, monkeypatch)
    approved = tmp_path / "approved-runtime"
    approved.mkdir()
    child = approved / "module.py"
    child.write_text("VALUE = 1\n", encoding="utf-8")
    ready = tmp_path / "owner-ready"
    helper = Path(__file__).parents[1] / "helpers" / "worker_acl_abandoned_owner.py"
    mutex_name = f"Global\\PaperWorkShell.Test.WorkerAcl.{token_hex(12)}"
    monkeypatch.setattr(windows_module, "_ACL_MUTATION_MUTEX_NAME", mutex_name)
    before = (_dacl_sddl(approved), _dacl_sddl(child))
    retained_mutex = _AclMutationMutex(recover_abandoned=True)
    process = subprocess.Popen(  # noqa: S603 - interpreter and helper are fixed
        (
            sys.executable,
            os.fspath(helper),
            "active",
            os.fspath(recovery_root),
            os.fspath(ready),
            mutex_name,
            os.fspath(approved),
        ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    entry = None
    orphan_cleaned = False
    try:
        deadline = time.monotonic() + 20.0
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.is_file()
        assert process.poll() is None
        entries = AclRecoveryJournal(recovery_root).inventory()
        assert len(entries) == 1
        entry = entries[0]
        assert entry.phase is AclRecoveryPhase.ACTIVE
        assert ready.read_text(encoding="ascii") == entry.profile_name
        active_sid = _sid_from_string(entry.sid)
        try:
            assert _sid_access_mask(approved, active_sid)
        finally:
            windows_module._local_free(active_sid)

        process.kill()
        assert process.wait(timeout=10.0) != 0
        observed_abandonment: list[bool] = []
        original_recover = windows_module._recover_acl_entries_locked

        def capture_abandonment(
            allowed_roots: tuple[Path, ...],
            *,
            mutex_was_abandoned: bool,
        ) -> windows_module.WindowsWorkerAclRecoveryResult:
            observed_abandonment.append(mutex_was_abandoned)
            return original_recover(
                allowed_roots,
                mutex_was_abandoned=mutex_was_abandoned,
            )

        monkeypatch.setattr(
            windows_module,
            "_recover_acl_entries_locked",
            capture_abandonment,
        )
        result = recover_worker_acl_access((approved,))
        monkeypatch.setattr(
            windows_module,
            "_recover_acl_entries_locked",
            original_recover,
        )

        assert observed_abandonment == [True]
        assert result.repaired_leases == 1
        sid = _sid_from_string(entry.sid)
        try:
            assert _sid_access_mask(approved, sid) == 0
            assert _sid_access_mask(child, sid) == 0
        finally:
            windows_module._local_free(sid)
        assert (_dacl_sddl(approved), _dacl_sddl(child)) == before
        assert AclRecoveryJournal(recovery_root).inventory() == ()

        orphan_sid = _derive_profile_sid(entry.profile_name)
        assert windows_module._sid_string(orphan_sid) == entry.sid
        orphan = _PartialAppContainerProfile(name=entry.profile_name, sid=orphan_sid)
        assert orphan.close_no_raise()
        orphan_cleaned = True

        replacement = _AppContainerProfile.create((approved,))
        try:
            replacement.grant_read_execute((approved,))
            assert _sid_access_mask(approved, replacement.sid)
        finally:
            assert replacement.close_no_raise()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10.0)
        if entry is not None and not orphan_cleaned:
            cleanup_sid = _derive_profile_sid(entry.profile_name)
            assert windows_module._sid_string(cleanup_sid) == entry.sid
            _PartialAppContainerProfile(
                name=entry.profile_name,
                sid=cleanup_sid,
            ).close_no_raise()
        assert retained_mutex._handle.close_no_raise()

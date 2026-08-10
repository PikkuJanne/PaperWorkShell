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
    ACL_RECOVERY_LEGACY_VERSION,
    AclRecoveryEntry,
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
from tests.integration.test_worker_windows_containment import (
    _assert_profile_removed,
    _dacl_sddl,
)


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


def test_dead_overlapping_lease_recovery_keeps_one_guard_for_live_sibling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery_root = _recovery_root(tmp_path, monkeypatch)
    approved = tmp_path / "approved-runtime"
    approved.mkdir()
    child = approved / "module.py"
    child.write_text("VALUE = 1\n", encoding="utf-8")
    before = (_dacl_sddl(approved), _dacl_sddl(child))
    first = _AppContainerProfile.create((approved,))
    second = _AppContainerProfile.create((approved,))
    journal = AclRecoveryJournal(recovery_root)
    dead_owner_pid = 2_000_000_000
    try:
        first.grant_read_execute((approved,))
        second.grant_read_execute((approved,))
        entries = {entry.profile_id: entry for entry in journal.inventory()}
        first_entry = entries[first.access.profile_id]
        journal.write(replace(first_entry, owner_pid=dead_owner_pid))
        monkeypatch.setattr(
            windows_module,
            "_ledger_owner_is_active",
            lambda owner_pid, _created: owner_pid != dead_owner_pid,
        )

        result = recover_worker_acl_access((approved,))

        assert result.repaired_leases == 1
        assert result.active_leases == 1
        assert {entry.profile_id for entry in journal.inventory()} == {second.access.profile_id}
        assert _sid_access_mask(child, first.sid) == 0
        assert _sid_access_mask(child, second.sid)
        inventory = second.access.inventory
        assert inventory is not None
        resource = inventory.resources_by_path[windows_module._normalized_path(child)]
        peer_sid = windows_module._convert_string_sid(
            windows_module._current_process_user_sid_string()
        )
        try:
            assert (
                windows_module._exact_deny_ace_count_handle(
                    resource.handle.value,
                    peer_sid,
                    windows_module._FILE_WRITE_ATTRIBUTES,
                )
                == 1
            )
        finally:
            windows_module._local_free(peer_sid)

        assert first.close_no_raise()
        assert second.close_no_raise()
        assert (_dacl_sddl(approved), _dacl_sddl(child)) == before
    finally:
        assert first.close_no_raise()
        assert second.close_no_raise()


def test_prepared_recovery_record_is_durable_before_first_acl_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery_root = _recovery_root(tmp_path, monkeypatch)
    approved = tmp_path / "approved-runtime"
    approved.mkdir()
    profile = _AppContainerProfile.create((approved,))
    original_change = windows_module._change_sid_access_handle
    observed_phases: list[AclRecoveryPhase] = []

    def observe_journal_before_mutation(
        handle: int,
        sid: ctypes.c_void_p,
        mode: int,
    ) -> None:
        entries = AclRecoveryJournal(recovery_root).inventory()
        assert len(entries) == 1
        observed_phases.append(entries[0].phase)
        original_change(handle, sid, mode)

    monkeypatch.setattr(
        windows_module,
        "_change_sid_access_handle",
        observe_journal_before_mutation,
    )
    try:
        profile.grant_read_execute((approved,))
        assert observed_phases[0] is AclRecoveryPhase.PREPARED
    finally:
        monkeypatch.setattr(windows_module, "_change_sid_access_handle", original_change)
        assert profile.close_no_raise()


@pytest.mark.parametrize("failure_point", ("peer_sid", "second_capture"))
def test_pre_prepared_failure_discards_only_unmutated_retained_state(
    failure_point: str,
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
    profile_folder = profile.local_app_data
    original_capture = windows_module._capture_exact_acl_recovery_object
    capture_calls = 0

    def fail_peer_sid() -> str:
        raise WindowsLaunchError(
            WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
            "access.identity",
            "Synthetic sanitized failure.",
        )

    def fail_second_capture(
        resource: windows_module._RetainedResource,
        *,
        guard_baseline: int | None,
        auto_inherited: bool,
    ) -> AclRecoveryObject:
        nonlocal capture_calls
        capture_calls += 1
        if capture_calls == 2:
            raise WindowsLaunchError(
                WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
                "access.recovery_object",
                "Synthetic sanitized failure.",
            )
        return original_capture(
            resource,
            guard_baseline=guard_baseline,
            auto_inherited=auto_inherited,
        )

    if failure_point == "peer_sid":
        monkeypatch.setattr(
            windows_module,
            "_current_process_user_sid_string",
            fail_peer_sid,
        )
    else:
        monkeypatch.setattr(
            windows_module,
            "_capture_exact_acl_recovery_object",
            fail_second_capture,
        )

    with pytest.raises(WindowsLaunchError):
        profile.grant_read_execute((approved,))

    assert not profile.access.prepared_publication_attempted
    assert profile.access.inventory is not None
    assert _sid_access_mask(approved, profile.sid) == 0
    assert _sid_access_mask(child, profile.sid) == 0
    assert not recovery_root.exists() or AclRecoveryJournal(recovery_root).inventory() == ()
    assert profile.close_no_raise()
    assert (_dacl_sddl(approved), _dacl_sddl(child)) == before
    _assert_profile_removed(profile_folder)


def test_inheritance_capture_disagreement_fails_before_prepared_publication(
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
    profile_folder = profile.local_app_data
    original_capture = windows_module._capture_exact_acl_recovery_object
    capture_calls = 0

    def flip_second_capture(
        resource: windows_module._RetainedResource,
        *,
        guard_baseline: int | None,
        auto_inherited: bool,
    ) -> AclRecoveryObject:
        nonlocal capture_calls
        capture_calls += 1
        captured = original_capture(
            resource,
            guard_baseline=guard_baseline,
            auto_inherited=auto_inherited,
        )
        if capture_calls == 2:
            return replace(captured, auto_inherited=not auto_inherited)
        return captured

    monkeypatch.setattr(
        windows_module,
        "_capture_exact_acl_recovery_object",
        flip_second_capture,
    )
    with pytest.raises(WindowsLaunchError) as raised:
        profile.grant_read_execute((approved,))

    assert raised.value.context == "access.recovery_inventory"
    assert not profile.access.prepared_publication_attempted
    assert _sid_access_mask(approved, profile.sid) == 0
    assert _sid_access_mask(child, profile.sid) == 0
    assert AclRecoveryJournal(recovery_root).inventory() == ()
    assert profile.close_no_raise()
    assert (_dacl_sddl(approved), _dacl_sddl(child)) == before
    _assert_profile_removed(profile_folder)


def test_published_prepared_write_failure_replays_without_private_exception_chain(
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
    profile_folder = profile.local_app_data
    original_write = AclRecoveryJournal.write
    canary = os.fspath(tmp_path / "PRIVATE_PREPARED_WRITE_CANARY")
    failed = False

    def publish_then_fail(
        journal: AclRecoveryJournal,
        entry: AclRecoveryEntry,
    ) -> None:
        nonlocal failed
        original_write(journal, entry)
        if entry.phase is AclRecoveryPhase.PREPARED and not failed:
            failed = True
            try:
                raise OSError(5, "synthetic private write failure", canary)
            except OSError as error:
                raise AclRecoveryJournalError("write.filesystem") from error

    monkeypatch.setattr(AclRecoveryJournal, "write", publish_then_fail)
    with pytest.raises(WindowsLaunchError) as raised:
        profile.grant_read_execute((approved,))

    rendered = "".join(traceback.format_exception(raised.value))
    assert profile.access.prepared_publication_attempted
    assert profile.access.inventory is not None
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert canary not in str(raised.value)
    assert canary not in repr(raised.value)
    assert canary not in rendered
    assert AclRecoveryJournal(recovery_root).inventory()[0].phase is AclRecoveryPhase.PREPARED

    monkeypatch.setattr(AclRecoveryJournal, "write", original_write)
    assert profile.close_no_raise()
    assert AclRecoveryJournal(recovery_root).inventory() == ()
    assert (_dacl_sddl(approved), _dacl_sddl(child)) == before
    _assert_profile_removed(profile_folder)


def test_active_inventory_failure_has_no_private_exception_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _recovery_root(tmp_path, monkeypatch)
    approved = tmp_path / "approved-runtime"
    approved.mkdir()
    profile = _AppContainerProfile.create((approved,))
    profile_folder = profile.local_app_data
    original_inventory = AclRecoveryJournal.inventory
    canary = os.fspath(tmp_path / "PRIVATE_INVENTORY_CANARY")
    inventory_calls = 0

    def fail_second_inventory(
        journal: AclRecoveryJournal,
    ) -> tuple[AclRecoveryEntry, ...]:
        nonlocal inventory_calls
        inventory_calls += 1
        if inventory_calls == 2:
            try:
                raise OSError(5, "synthetic private inventory failure", canary)
            except OSError as error:
                raise AclRecoveryJournalError("inventory.filesystem") from error
        return original_inventory(journal)

    monkeypatch.setattr(AclRecoveryJournal, "inventory", fail_second_inventory)
    with pytest.raises(WindowsLaunchError) as raised:
        profile.grant_read_execute((approved,))

    rendered = "".join(traceback.format_exception(raised.value))
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert canary not in str(raised.value)
    assert canary not in repr(raised.value)
    assert canary not in rendered
    monkeypatch.setattr(AclRecoveryJournal, "inventory", original_inventory)
    assert profile.close_no_raise()
    _assert_profile_removed(profile_folder)


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
                replace(
                    first,
                    file_id=("00" * 16 if first.file_id != "00" * 16 else "ff" * 16),
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


def test_old_v2_python_cache_identity_mismatch_remains_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery_root = _recovery_root(tmp_path, monkeypatch)
    approved = tmp_path / "approved-runtime"
    package = approved / "package"
    cache = package / "__pycache__"
    cache.mkdir(parents=True)
    source = package / "module.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    cached = cache / "module.cpython-314.pyc"
    cached.write_bytes(b"old synthetic bytecode")
    profile = _AppContainerProfile.create((approved,))
    journal = AclRecoveryJournal(recovery_root)
    cache_handle, cache_info = windows_module._open_retained_resource(
        cached,
        acl_authority=True,
    )
    cache_resource = windows_module._RetainedResource(cached, cache_handle, cache_info)
    peer_sid = windows_module._convert_string_sid(windows_module._current_process_user_sid_string())
    try:
        cache_object = windows_module._capture_exact_acl_recovery_object(
            cache_resource,
            guard_baseline=windows_module._exact_deny_ace_count_handle(
                cache_handle.value,
                peer_sid,
                windows_module._FILE_WRITE_ATTRIBUTES,
            ),
            auto_inherited=windows_module._dacl_auto_inherited_handle(cache_handle.value),
        )
    finally:
        windows_module._local_free(peer_sid)
        cache_handle.close()
    try:
        profile.grant_read_execute((approved,))
        original = journal.inventory()[0]
        old_v2 = replace(original, objects=(*original.objects, cache_object))
        journal.write(old_v2)
        replacement = cache / "replacement.tmp"
        replacement.write_bytes(b"new synthetic bytecode")
        replacement.replace(cached)
        replacement_dacl = _dacl_sddl(cached)
        granted_dacl = _dacl_sddl(approved)
        monkeypatch.setattr(windows_module, "_ledger_owner_is_active", lambda *_args: False)

        with pytest.raises(WindowsLaunchError) as raised:
            recover_worker_acl_access((approved,))

        assert raised.value.code is WindowsLaunchErrorCode.ACCESS_GRANT_FAILED
        assert raised.value.context == "access.recovery_identity"
        assert journal.inventory() == (old_v2,)
        assert _dacl_sddl(approved) == granted_dacl
        assert _dacl_sddl(cached) == replacement_dacl
        assert _sid_access_mask(approved, profile.sid)

        journal.write(original)
        assert recover_worker_acl_access((approved,)).repaired_leases == 1
    finally:
        assert profile.close_no_raise()


def test_legacy_recovery_pins_root_before_rechecking_swap_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _recovery_root(tmp_path, monkeypatch)
    approved = tmp_path / "legacy-runtime"
    approved.mkdir()
    child = approved / "module.py"
    child.write_text("VALUE = 1\n", encoding="utf-8")
    retained = tmp_path / "retained-legacy-runtime"
    replacement = tmp_path / "replacement-runtime"
    replacement.mkdir()
    (replacement / "replacement.py").write_text("VALUE = 2\n", encoding="utf-8")
    original_dacls = (_dacl_sddl(approved), _dacl_sddl(child))
    replacement_dacl = _dacl_sddl(replacement)
    profile = _AppContainerProfile.create((approved,))
    profile_folder = profile.local_app_data
    objects = tuple(windows_module._capture_acl_recovery_object(path) for path in (approved, child))
    root_handle, _root_info = windows_module._open_retained_resource(
        approved,
        acl_authority=True,
    )
    try:
        windows_module._change_sid_access_handle(
            root_handle.value,
            profile.sid,
            windows_module._GRANT_ACCESS,
            inheritance=windows_module._SUB_CONTAINERS_AND_OBJECTS_INHERIT,
        )
        windows_module._restore_dacl_auto_inherited_handle(
            root_handle.value,
            objects[0].auto_inherited,
        )
    finally:
        root_handle.close()
    assert _sid_access_mask(approved, profile.sid)
    assert _sid_access_mask(child, profile.sid)
    granted_dacls = (_dacl_sddl(approved), _dacl_sddl(child))
    owner_pid, owner_created_100ns = windows_module._current_process_identity()
    entry = AclRecoveryEntry(
        profile_id=profile.access.profile_id,
        profile_name=profile.name,
        sid=windows_module._sid_string(profile.sid),
        owner_pid=owner_pid,
        owner_created_100ns=owner_created_100ns,
        phase=AclRecoveryPhase.ACTIVE,
        grant_roots=(approved,),
        objects=objects,
        journal_version=ACL_RECOVERY_LEGACY_VERSION,
    )
    journal = AclRecoveryJournal(windows_module._ensure_acl_recovery_directory())
    journal.write(entry)
    monkeypatch.setattr(windows_module, "_ledger_owner_is_active", lambda *_args: False)
    alias_object = replace(
        objects[0],
        path=approved / ".." / approved.name,
    )
    alias_entry = replace(
        entry,
        objects=(objects[0], alias_object, objects[1]),
    )
    journal.write(alias_entry)
    with pytest.raises(WindowsLaunchError) as alias_error:
        recover_worker_acl_access((approved,))
    assert alias_error.value.context == "access.recovery_inventory"
    assert journal.inventory() == (replace(alias_entry, phase=AclRecoveryPhase.REVOKING),)
    assert (_dacl_sddl(approved), _dacl_sddl(child)) == granted_dacls
    assert _sid_access_mask(approved, profile.sid)
    assert _sid_access_mask(child, profile.sid)
    journal.write(entry)
    original_retain_guards = windows_module._retain_ancestor_guards
    swapped = False

    def swap_before_open(
        parent: Path,
        inventory: windows_module._ApprovedResourceInventory,
    ) -> None:
        nonlocal swapped
        if parent == approved.parent and not swapped:
            approved.rename(retained)
            replacement.rename(approved)
            swapped = True
        original_retain_guards(parent, inventory)

    monkeypatch.setattr(windows_module, "_retain_ancestor_guards", swap_before_open)
    try:
        with pytest.raises(WindowsLaunchError) as raised:
            recover_worker_acl_access((approved,))

        assert swapped
        assert raised.value.context == "access.recovery_identity"
        assert _dacl_sddl(approved) == replacement_dacl
        assert journal.inventory() == (replace(entry, phase=AclRecoveryPhase.REVOKING),)
        assert _sid_access_mask(retained, profile.sid)

        approved.rename(replacement)
        retained.rename(approved)
        monkeypatch.setattr(
            windows_module,
            "_retain_ancestor_guards",
            original_retain_guards,
        )
        result = recover_worker_acl_access((approved,))
        assert result.repaired_leases == 1
        assert _sid_access_mask(approved, profile.sid) == 0
        assert _sid_access_mask(child, profile.sid) == 0
        assert (_dacl_sddl(approved), _dacl_sddl(child)) == original_dacls
        assert journal.inventory() == ()
    finally:
        monkeypatch.setattr(
            windows_module,
            "_retain_ancestor_guards",
            original_retain_guards,
        )
        if retained.exists() and approved.exists():
            approved.rename(replacement)
            retained.rename(approved)
        if journal.exists(entry.profile_id):
            recover_worker_acl_access((approved,))
        assert profile.close_no_raise()
    _assert_profile_removed(profile_folder)


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
        assert raised.value.context == "config.resource_links"
        assert _sid_access_mask(approved, profile.sid) == 0
        assert _sid_access_mask(outside_file, profile.sid) == 0
    finally:
        assert profile.close_no_raise()


def test_retained_handle_blocks_runtime_replacement_after_prepared_cutpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _recovery_root(tmp_path, monkeypatch)
    approved = tmp_path / "approved-runtime"
    approved.mkdir()
    child = approved / "module.py"
    child.write_text("VALUE = 1\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("OUTSIDE = 1\n", encoding="utf-8")
    original_append = windows_module._append_exact_deny_ace_handle
    replacement_blocked = False

    def attempt_replace_before_first_acl_mutation(
        handle: int,
        sid: ctypes.c_void_p,
        mask: int,
    ) -> None:
        nonlocal replacement_blocked
        try:
            child.replace(tmp_path / "retained-module.py")
        except OSError:
            replacement_blocked = True
        original_append(handle, sid, mask)

    profile = _AppContainerProfile.create((approved,))
    monkeypatch.setattr(
        windows_module,
        "_append_exact_deny_ace_handle",
        attempt_replace_before_first_acl_mutation,
    )
    try:
        profile.grant_read_execute((approved,))

        assert replacement_blocked
        assert child.read_text(encoding="utf-8") == "VALUE = 1\n"
        assert _sid_access_mask(outside, profile.sid) == 0
    finally:
        assert profile.close_no_raise()


@pytest.mark.parametrize(
    ("mode", "expected_phase"),
    (
        ("prepared", AclRecoveryPhase.PREPARED),
        ("partial_guard", AclRecoveryPhase.PREPARED),
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
        if mode in {"prepared", "partial_guard"}:
            assert root_masks == (0, 0)
        elif mode == "partial_grant":
            assert root_masks[0] != 0 and root_masks[1] == 0
        else:
            assert root_masks == (0, 0)

        peer_sid_string = entry.peer_sid
        assert peer_sid_string is not None
        peer_sid = windows_module._convert_string_sid(peer_sid_string)
        guard_counts: list[int] = []
        try:
            for item in entry.objects:
                if item.is_directory:
                    continue
                handle, _info = windows_module._open_retained_resource(
                    item.path,
                    acl_authority=False,
                )
                try:
                    guard_counts.append(
                        windows_module._exact_deny_ace_count_handle(
                            handle.value,
                            peer_sid,
                            windows_module._FILE_WRITE_ATTRIBUTES,
                        )
                    )
                finally:
                    handle.close()
        finally:
            windows_module._local_free(peer_sid)
        if mode == "prepared":
            assert guard_counts == [0, 0]
        elif mode in {"partial_guard", "revoking"}:
            assert guard_counts == [1, 0]
        else:
            assert guard_counts == [1, 1]

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

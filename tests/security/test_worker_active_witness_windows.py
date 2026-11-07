"""The own-live-ACTIVE shortcut replaces path reopening, never live ACL validation."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from paperworkshell.worker.client import _windows as windows
from paperworkshell.worker.client._acl_recovery import AclRecoveryJournal, AclRecoveryPhase
from tests.security.test_worker_acl_control_windows import (
    _AI,
    _AR,
    _DACL_INFORMATION,
    _DEAD_OWNER,
    _PROTECTED,
    _OwnedTargets,
    _set_fixture_controls,
    _state,
    _tree,
)
from tests.security.test_worker_acl_control_windows import targets as targets

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native own-live-ACTIVE witness contract")


@contextmanager
def _lease(targets: _OwnedTargets) -> Iterator[tuple[Path, windows._AppContainerProfile]]:
    root, _child, paths = _tree(targets)
    before = tuple(_state(path) for path in paths)
    profile = windows._AppContainerProfile.create((root,))
    try:
        profile.grant_read_execute((root,))
        assert profile.access._active_recovery_witness() is not None
        yield root, profile
    finally:
        assert windows._close_profile_bounded(profile)
    assert tuple(_state(path) for path in paths) == before
    assert AclRecoveryJournal(targets.journal).inventory() == ()


def test_repeated_own_recovery_skips_path_reopen_but_revalidates_actual_handles_and_dacl(
    targets: _OwnedTargets, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _lease(targets) as (root, profile):
        witness = profile.access._active_recovery_witness()
        assert witness is not None
        paths: list[Path] = []
        validations: list[bool] = []
        identity = windows._require_acl_recovery_object_identity
        validate = windows._require_active_lease_recovery_witness

        def observe_identity(item: Any) -> None:
            paths.append(item.path)
            identity(item)

        def observe_validation(*args: Any, **kwargs: Any) -> Any:
            validations.append(True)
            return validate(*args, **kwargs)

        monkeypatch.setattr(windows, "_require_acl_recovery_object_identity", observe_identity)
        monkeypatch.setattr(windows, "_require_active_lease_recovery_witness", observe_validation)
        for _borrow in range(2):
            result = windows.recover_worker_acl_access((root,), _active_lease_witness=witness)
            assert result.active_leases == 1 and result.repaired_leases == 0
        assert paths == []
        assert len(validations) >= 2


def test_default_recovery_still_reopens_each_exact_object_without_a_witness(
    targets: _OwnedTargets, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _lease(targets) as (root, _profile):
        original = windows._require_acl_recovery_object_identity
        observed: list[Path] = []

        def observe(item: Any) -> None:
            observed.append(item.path)
            original(item)

        monkeypatch.setattr(windows, "_require_acl_recovery_object_identity", observe)
        result = windows.recover_worker_acl_access((root,))
        entry = AclRecoveryJournal(targets.journal).inventory()[0]
        assert result.active_leases == 1 and set(observed) == {item.path for item in entry.objects}


@pytest.mark.parametrize(
    "change", ("missing", "owner_start", "phase", "sid", "object_control", "path_spelling")
)
def test_missing_or_changed_own_record_is_rejected_and_original_can_still_clean_up(
    targets: _OwnedTargets, change: str
) -> None:
    with _lease(targets) as (root, profile):
        witness = profile.access._active_recovery_witness()
        assert witness is not None
        journal = AclRecoveryJournal(targets.journal)
        original = journal.inventory()[0]
        if change == "missing":
            journal.delete(original.profile_id)
        else:
            if change == "owner_start":
                changed = replace(original, owner_created_100ns=original.owner_created_100ns + 1)
            elif change == "phase":
                changed = replace(original, phase=AclRecoveryPhase.PREPARED)
            elif change == "sid":
                head, _, number = original.sid.rpartition("-")
                changed = replace(original, sid=f"{head}-{int(number) + 1}")
            elif change == "path_spelling":
                root = original.grant_roots[0]
                spelling = Path(str(root).swapcase())
                changed = replace(
                    original,
                    grant_roots=(spelling, *original.grant_roots[1:]),
                    objects=tuple(
                        replace(item, path=spelling) if item.path == root else item
                        for item in original.objects
                    ),
                )
            else:
                first = original.objects[0]
                changed = replace(
                    original,
                    objects=(
                        replace(first, auto_inherited=not first.auto_inherited),
                        *original.objects[1:],
                    ),
                )
            journal.write(changed)
        try:
            with pytest.raises(windows.WindowsLaunchError) as caught:
                windows.recover_worker_acl_access((root,), _active_lease_witness=witness)
            assert caught.value.context == "access.recovery_witness"
        finally:
            # Exact captured synthetic record restored through the supported API.
            journal.write(original)


@pytest.mark.parametrize("change", ("thread", "inventory"))
def test_stale_witness_owner_or_inventory_reference_never_authorizes_skip(
    targets: _OwnedTargets, change: str
) -> None:
    with _lease(targets) as (root, profile):
        witness = profile.access._active_recovery_witness()
        assert witness is not None
        changed = (
            replace(witness, owner_thread=witness.owner_thread + 1)
            if change == "thread"
            else replace(witness, inventory=windows._ApprovedResourceInventory())
        )
        with pytest.raises(windows.WindowsLaunchError):
            windows.recover_worker_acl_access((root,), _active_lease_witness=changed)


def _restore_active_descriptor(handle: int, descriptor: bytes) -> None:
    payload = bytearray(descriptor)
    control = int.from_bytes(payload[2:4], "little")
    control = control | _AR if control & _AI else control & ~_AR
    payload[2:4] = control.to_bytes(2, "little")
    storage = ctypes.create_string_buffer(bytes(payload))
    setter = windows._advapi32().SetFileSecurityW
    setter.argtypes = (ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_void_p)
    setter.restype = ctypes.c_int
    name = ctypes.create_unicode_buffer(32768)
    query = windows._kernel32().GetFinalPathNameByHandleW
    query.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong)
    query.restype = ctypes.c_ulong
    assert query(handle, name, len(name), 0)
    assert setter(name.value, _DACL_INFORMATION, ctypes.cast(storage, ctypes.c_void_p))


def _mutate_own_ace(descriptor: bytes, sid: ctypes.c_void_p, change: str) -> bytes:
    length = windows._advapi32().GetLengthSid
    length.argtypes = (ctypes.c_void_p,)
    length.restype = ctypes.c_ulong
    sid_bytes = ctypes.string_at(sid, length(sid))
    payload = bytearray(descriptor)
    dacl = int.from_bytes(payload[16:20], "little")
    count = int.from_bytes(payload[dacl + 4 : dacl + 6], "little")
    offset = dacl + 8
    for _index in range(count):
        size = int.from_bytes(payload[offset + 2 : offset + 4], "little")
        if payload[offset] == 0 and bytes(payload[offset + 8 : offset + size]) == sid_bytes:
            if change == "extra_rights":
                mask = int.from_bytes(payload[offset + 4 : offset + 8], "little")
                payload[offset + 4 : offset + 8] = (mask | 0x100).to_bytes(4, "little")
            elif change == "deny":
                payload[offset] = 1
            elif change == "inherit_only":
                payload[offset + 1] |= 8
            else:
                payload[offset + 1] |= 1
            return bytes(payload)
        offset += size
    raise AssertionError("Synthetic own profile ACE was not found.")


@pytest.mark.parametrize("change", ("extra_rights", "deny", "inherit_only", "inheritable", "ai"))
def test_each_borrow_rejects_actual_current_acl_tamper_without_a_permission_cache(
    targets: _OwnedTargets, change: str
) -> None:
    with _lease(targets) as (root, profile):
        witness = profile.access._active_recovery_witness()
        inventory = profile.access.inventory
        assert witness is not None and inventory is not None
        resource = inventory.resources_by_path[windows._normalized_path(root)]
        active = windows._capture_security_descriptor_handle(resource.handle.value)
        before = _state(root)
        windows.recover_worker_acl_access((root,), _active_lease_witness=witness)
        try:
            if change == "ai":
                _set_fixture_controls(
                    root, auto=not bool(before[0] & _AI), protected=bool(before[0] & _PROTECTED)
                )
            else:
                _restore_active_descriptor(
                    resource.handle.value, _mutate_own_ace(active, profile.sid, change)
                )
            assert _state(root) != before
            with pytest.raises(windows.WindowsLaunchError):
                windows.recover_worker_acl_access((root,), _active_lease_witness=witness)
        finally:
            _restore_active_descriptor(resource.handle.value, active)
        assert _state(root) == before


def test_each_borrow_rejects_removed_real_file_hardlink_guard(targets: _OwnedTargets) -> None:
    with _lease(targets) as (root, profile):
        witness = profile.access._active_recovery_witness()
        inventory = profile.access.inventory
        peer_name = profile.access.peer_sid_string
        assert witness is not None and inventory is not None and peer_name is not None
        resource = next(item for item in inventory.resources if not item.info.is_directory)
        active = windows._capture_security_descriptor_handle(resource.handle.value)
        peer = windows._convert_string_sid(peer_name)
        try:
            windows._remove_one_exact_deny_ace_handle(
                resource.handle.value, peer, windows._FILE_WRITE_ATTRIBUTES
            )
            with pytest.raises(windows.WindowsLaunchError):
                windows.recover_worker_acl_access((root,), _active_lease_witness=witness)
        finally:
            _restore_active_descriptor(resource.handle.value, active)
            windows._local_free(peer)


def test_other_live_profile_even_same_pid_keeps_full_path_validation(
    targets: _OwnedTargets, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _lease(targets) as (root, profile):
        other = windows._AppContainerProfile.create((root,))
        try:
            other.grant_read_execute((root,))
            witness = profile.access._active_recovery_witness()
            assert witness is not None
            seen: list[Path] = []
            original = windows._require_acl_recovery_object_identity

            def observe(item: Any) -> None:
                seen.append(item.path)
                original(item)

            monkeypatch.setattr(windows, "_require_acl_recovery_object_identity", observe)
            result = windows.recover_worker_acl_access((root,), _active_lease_witness=witness)
            assert result.active_leases == 2 and result.repaired_leases == 0
            assert other.access.inventory is not None
            assert set(seen) == {item.path for item in other.access.inventory.resources}
        finally:
            assert windows._close_profile_bounded(other)


def test_abandoned_condition_with_only_own_active_record_still_fails_closed(
    targets: _OwnedTargets,
) -> None:
    with _lease(targets) as (root, profile):
        witness = profile.access._active_recovery_witness()
        assert witness is not None
        # Inject only the abandoned-status condition under the real private
        # mutation mutex; this does not claim an actual OS-abandonment event.
        with windows._AclMutationMutex(recover_abandoned=True):
            with pytest.raises(windows.WindowsLaunchError) as caught:
                windows._recover_acl_entries_locked(
                    (root,), mutex_was_abandoned=True, _active_lease_witness=witness
                )
        assert caught.value.code is windows.WindowsLaunchErrorCode.CLEANUP_FAILED
        assert caught.value.context == "access.recovery_abandoned"


def test_dead_revoking_foreign_lease_keeps_full_repair_and_rechecks_own_live_dacl(
    targets: _OwnedTargets, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _lease(targets) as (root, profile):
        witness = profile.access._active_recovery_witness()
        assert witness is not None
        script = targets.root / "fixed-foreign-owner.py"
        script.write_text(_DEAD_OWNER, encoding="utf-8")
        source = Path(__file__).resolve().parents[2] / "src"
        result = subprocess.run(
            (
                sys.executable,
                "-I",
                "-S",
                "-B",
                str(script),
                str(source),
                str(root),
                str(targets.journal),
                windows._ACL_MUTATION_MUTEX_NAME,
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
        assert result.returncode == 23
        journal = AclRecoveryJournal(targets.journal)
        entries = journal.inventory()
        assert len(entries) == 2
        foreign = next(entry for entry in entries if entry.profile_id != witness.entry.profile_id)
        assert not windows._ledger_owner_is_active(foreign.owner_pid, foreign.owner_created_100ns)
        journal.write(replace(foreign, phase=AclRecoveryPhase.REVOKING))
        reopened: list[Path] = []
        revalidated: list[bool] = []
        original_identity = windows._require_acl_recovery_object_identity
        original_witness = windows._require_active_lease_recovery_witness

        def identity(item: Any) -> None:
            reopened.append(item.path)
            original_identity(item)

        def validate(*args: Any, **kwargs: Any) -> Any:
            revalidated.append(True)
            return original_witness(*args, **kwargs)

        monkeypatch.setattr(windows, "_require_acl_recovery_object_identity", identity)
        monkeypatch.setattr(windows, "_require_active_lease_recovery_witness", validate)
        recovered = windows.recover_worker_acl_access((root,), _active_lease_witness=witness)
        assert recovered.repaired_leases == 1 and recovered.active_leases == 1
        assert set(reopened) == {item.path for item in foreign.objects}
        assert len(revalidated) >= 2  # Before and after the foreign mutations.
        assert journal.inventory() == (witness.entry,)
        profile.require_read_execute(tuple(item.path for item in witness.entry.objects))

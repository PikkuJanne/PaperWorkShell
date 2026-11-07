"""Fresh ACL decisions and uncertain publication cannot reuse an ACTIVE proof."""

from __future__ import annotations

import ctypes
from dataclasses import replace
from pathlib import Path
from typing import Self, cast

import pytest

from paperworkshell.worker.client import _windows as windows
from paperworkshell.worker.client._acl_recovery import (
    AclRecoveryEntry,
    AclRecoveryJournal,
    AclRecoveryJournalError,
    AclRecoveryObject,
    AclRecoveryPhase,
)

_OWN = bytes.fromhex("010100000000000501000000")
_PEER = bytes.fromhex("010100000000000502000000")


def _entry() -> AclRecoveryEntry:
    root = Path("C:/synthetic-runtime")
    return AclRecoveryEntry(
        profile_id="1" * 24,
        profile_name="PaperWorkShell.Worker." + "1" * 24,
        sid="S-1-15-2-1-2-3-4-5-6-7",
        owner_pid=123,
        owner_created_100ns=456,
        phase=AclRecoveryPhase.ACTIVE,
        grant_roots=(root,),
        objects=(AclRecoveryObject(root, 1, 2, True, False, volume_serial=1, file_id="1" * 32),),
        peer_sid="S-1-5-21-1-2-3-1001",
    )


def test_committed_then_failed_publication_discards_active_witness_before_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = _entry()
    lease = windows._AccessGrantLease(ctypes.c_void_p(123))
    lease.inventory = windows._ApprovedResourceInventory()
    lease._last_published_entry = active
    assert lease._active_recovery_witness() is not None
    committed: list[AclRecoveryEntry] = []

    class Journal:
        def write(self, entry: AclRecoveryEntry) -> None:
            assert lease._active_recovery_witness() is None
            committed.append(entry)
            raise AclRecoveryJournalError("write.readback")

    monkeypatch.setattr(
        windows._AccessGrantLease,
        "_entry",
        lambda _self, phase: replace(active, phase=phase),
    )
    with pytest.raises(windows.WindowsLaunchError, match="persisted"):
        lease._write_entry(cast(AclRecoveryJournal, Journal()), AclRecoveryPhase.REVOKING)
    assert committed[0].phase is AclRecoveryPhase.REVOKING
    assert lease._active_recovery_witness() is None

    class RetryJournal:
        def write(self, entry: AclRecoveryEntry) -> None:
            committed.append(entry)

    lease._write_entry(cast(AclRecoveryJournal, RetryJournal()), AclRecoveryPhase.REVOKING)
    assert lease._last_published_entry == committed[-1]
    assert lease._active_recovery_witness() is None


def test_canonical_record_path_spelling_is_exact_even_when_windows_paths_compare_equal() -> None:
    original = _entry()
    changed = replace(
        original,
        grant_roots=tuple(Path(str(path).swapcase()) for path in original.grant_roots),
        objects=tuple(
            replace(item, path=Path(str(item.path).swapcase())) for item in original.objects
        ),
    )
    lease = windows._AccessGrantLease(ctypes.c_void_p(123))
    lease.inventory = windows._ApprovedResourceInventory()
    lease._last_published_entry = original
    witness = lease._active_recovery_witness()
    assert witness is not None
    with pytest.raises(windows.WindowsLaunchError) as caught:
        windows._require_active_lease_recovery_witness(witness, changed)
    assert caught.value.context == "access.recovery_witness"


def test_default_recovery_keeps_original_internal_call_without_optional_witness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Mutex:
        was_abandoned = False

        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            pass

    roots = (Path("C:/synthetic-runtime"),)
    observed: list[tuple[Path, ...]] = []

    def recover(
        allowed_roots: tuple[Path, ...], *, mutex_was_abandoned: bool
    ) -> windows.WindowsWorkerAclRecoveryResult:
        assert not mutex_was_abandoned
        observed.append(allowed_roots)
        return windows.WindowsWorkerAclRecoveryResult(0, 0, 0)

    monkeypatch.setattr(windows, "_AclMutationMutex", Mutex)
    monkeypatch.setattr(windows, "_normalized_unique_roots", lambda paths: paths)
    monkeypatch.setattr(windows, "_recover_acl_entries_locked", recover)
    assert windows.recover_worker_acl_access(roots).journal_entries == 0
    assert observed == [roots]


def _ace(ace_type: int, flags: int, mask: int, sid: bytes) -> bytes:
    payload = mask.to_bytes(4, "little") + sid
    return bytes((ace_type, flags)) + (4 + len(payload)).to_bytes(2, "little") + payload


def _descriptor(aces: tuple[bytes, ...], *, auto_inherited: bool = False) -> bytes:
    payload = b"".join(aces)
    acl = b"\x02\0" + (8 + len(payload)).to_bytes(2, "little")
    acl += len(aces).to_bytes(2, "little") + b"\0\0" + payload
    control = 0x8004 | (0x0400 if auto_inherited else 0)
    header = b"\x01\0" + control.to_bytes(2, "little") + bytes(12)
    return header + (20).to_bytes(4, "little") + acl


@pytest.mark.parametrize(
    "fault",
    ["deny", "surplus", "inherited", "inherit_only", "missing", "duplicate", "guard", "control"],
)
def test_current_own_acl_or_guard_tamper_cannot_use_prior_permission(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    resource = windows._RetainedResource(
        Path("C:/synthetic-runtime/module.py"),
        windows._OwnedHandle(123),
        windows._RetainedResourceInfo((1, bytes(16)), False, False, False, 1),
    )
    item = AclRecoveryObject(
        resource.path, 1, 2, False, False, volume_serial=1, file_id="1" * 32, guard_baseline=0
    )
    good = _ace(0, 0, windows._FILE_GENERIC_READ_EXECUTE, _OWN)
    guard = _ace(1, 0, windows._FILE_WRITE_ATTRIBUTES, _PEER)
    aces = [good, guard]
    if fault == "deny":
        aces.append(_ace(1, 0, 1, _OWN))
    elif fault == "surplus":
        aces[0] = _ace(0, 0, windows._FILE_GENERIC_READ_EXECUTE | 2, _OWN)
    elif fault == "inherited":
        aces[0] = _ace(0, 0x10, windows._FILE_GENERIC_READ_EXECUTE, _OWN)
    elif fault == "inherit_only":
        aces[0] = _ace(0, 8, windows._FILE_GENERIC_READ_EXECUTE, _OWN)
    elif fault == "missing":
        aces.remove(good)
    elif fault == "duplicate":
        aces.append(good)
    elif fault == "guard":
        aces.remove(guard)
    monkeypatch.setattr(
        windows,
        "_capture_security_descriptor_handle",
        lambda _handle: _descriptor(tuple(aces), auto_inherited=fault == "control"),
    )
    with pytest.raises(windows.WindowsLaunchError) as failure:
        windows._require_active_resource_acl(resource, item, _OWN, _PEER)
    assert failure.value.context == "access.recovery_witness"


def test_unrelated_principal_deny_is_preserved_without_becoming_own_sid_deny(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _entry().objects[0]
    resource = windows._RetainedResource(
        item.path,
        windows._OwnedHandle(123),
        windows._RetainedResourceInfo((1, bytes(16)), True, False, False, 1),
    )
    monkeypatch.setattr(
        windows,
        "_capture_security_descriptor_handle",
        lambda _handle: _descriptor(
            (_ace(0, 0, windows._FILE_GENERIC_READ_EXECUTE, _OWN), _ace(1, 0, 2, _PEER))
        ),
    )
    windows._require_active_resource_acl(resource, item, _OWN, _PEER)


@pytest.mark.parametrize("flags", [0, 1, 2, 3])
def test_object_callback_ace_sid_uses_optional_guid_layout(flags: int) -> None:
    payload = bytes(4) + flags.to_bytes(4, "little")
    payload += bytes(16 * bool(flags & 1) + 16 * bool(flags & 2)) + _OWN + b"condition"
    ace = bytes((11, 0)) + (4 + len(payload)).to_bytes(2, "little") + payload
    assert windows._access_ace_sid_bytes(ace) == _OWN

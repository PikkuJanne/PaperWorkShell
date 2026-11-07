"""Native filesystem ACL controls survive exact grants and dead-owner recovery."""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from secrets import token_hex

import pytest

from paperworkshell.worker.client import _windows as windows
from paperworkshell.worker.client._acl_recovery import AclRecoveryJournal

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native Windows filesystem ACL controls")

_AI = 0x0400
_AR = 0x0100
_PROTECTED = 0x1000
_DACL_INFORMATION = 4
_SID = "S-1-15-2-101-102-103-104-105-106-107-108"
type _DaclState = tuple[int, tuple[bytes, ...]]


@dataclass(frozen=True)
class _OwnedTargets:
    root: Path
    journal: Path


@pytest.fixture
def targets(monkeypatch: pytest.MonkeyPatch) -> Iterator[_OwnedTargets]:
    parent = Path(__file__).resolve().parents[2] / ".paperworkshell-local"
    parent.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="acl-control-contract-", dir=parent))
    journal = stage / "private-recovery"
    monkeypatch.setattr(windows, "_acl_recovery_directory_path", lambda: journal)
    monkeypatch.setattr(
        windows,
        "_ACL_MUTATION_MUTEX_NAME",
        f"Global\\PaperWorkShell.Test.AclControls.{token_hex(12)}",
    )
    yield _OwnedTargets(stage, journal)
    # Never remove identities needed by supported recovery after a failed test.
    if journal.exists() and AclRecoveryJournal(journal).inventory():
        raise RuntimeError("Unresolved test ACL recovery retained its exact owned targets.")
    if stage.resolve().parent != parent.resolve() or stage.is_symlink():
        raise RuntimeError("ACL control test cleanup escaped its owned staging directory.")
    shutil.rmtree(stage)


def _descriptor(path: Path) -> bytes:
    handle, _info = windows._open_retained_resource(path, acl_authority=False)
    try:
        return windows._capture_security_descriptor_handle(handle.value)
    finally:
        assert windows._close_owned_handle_bounded(handle)


def _state(path: Path) -> _DaclState:
    descriptor = _descriptor(path)
    control = int.from_bytes(descriptor[2:4], "little")
    start = int.from_bytes(descriptor[16:20], "little")
    assert 20 <= start < len(descriptor)
    size = int.from_bytes(descriptor[start + 2 : start + 4], "little")
    count = int.from_bytes(descriptor[start + 4 : start + 6], "little")
    end = start + size
    assert start + 8 <= end <= len(descriptor)
    cursor = start + 8
    aces: list[bytes] = []
    for _index in range(count):
        assert cursor + 4 <= end
        ace_size = int.from_bytes(descriptor[cursor + 2 : cursor + 4], "little")
        assert ace_size >= 4 and cursor + ace_size <= end
        aces.append(descriptor[cursor : cursor + ace_size])
        cursor += ace_size
    # Compare every ACE byte (order, inheritance flags, permissions and SIDs),
    # excluding only allocator padding after the last ACE.
    return control, tuple(aces)


def _set_fixture_controls(path: Path, *, auto: bool, protected: bool) -> None:
    """Independently establish fixture flags; never call the production setter."""

    descriptor = bytearray(_descriptor(path))
    control = int.from_bytes(descriptor[2:4], "little")
    control &= ~(_AI | _AR | _PROTECTED)
    if auto:
        control |= _AI | _AR
    if protected:
        control |= _PROTECTED
    descriptor[2:4] = control.to_bytes(2, "little")
    storage = ctypes.create_string_buffer(bytes(descriptor))
    setter = ctypes.WinDLL("advapi32", use_last_error=True).SetFileSecurityW
    setter.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p)
    setter.restype = wintypes.BOOL
    assert setter(str(path), _DACL_INFORMATION, ctypes.cast(storage, ctypes.c_void_p))
    observed, _aces = _state(path)
    assert bool(observed & _AI) is auto
    assert bool(observed & _PROTECTED) is protected


def _tree(targets: _OwnedTargets) -> tuple[Path, Path, tuple[Path, ...]]:
    root = targets.root / "approved"
    child = root / "admitted-child"
    child.mkdir(parents=True)
    code = child / "fixed.py"
    code.write_bytes(b"VALUE = 1\n")
    excluded = root / "__pycache__"
    excluded.mkdir()
    bytecode = excluded / "unadmitted.pyc"
    bytecode.write_bytes(b"synthetic excluded bytecode canary")
    return root, child, (root, child, code, excluded, bytecode)


@pytest.mark.parametrize("parent_auto,child_auto", ((True, False), (False, True)))
@pytest.mark.parametrize("protected", (False, True))
def test_single_directory_update_preserves_mixed_and_uninventoried_full_dacls(
    targets: _OwnedTargets, parent_auto: bool, child_auto: bool, protected: bool
) -> None:
    root, child, paths = _tree(targets)
    _set_fixture_controls(root, auto=parent_auto, protected=protected)
    _set_fixture_controls(child, auto=child_auto, protected=not protected)
    _set_fixture_controls(paths[3], auto=child_auto, protected=protected)
    before = tuple(_state(path) for path in paths)
    handle, _info = windows._open_retained_resource(root, acl_authority=True)
    sid = windows._convert_string_sid(_SID)
    try:
        windows._change_sid_access_handle(handle.value, sid, windows._GRANT_ACCESS)
        assert _state(root)[0] == before[0][0]
        assert tuple(_state(path) for path in paths[1:]) == before[1:]
        assert windows._sid_access_mask_handle(handle.value, sid) != 0
    finally:
        windows._change_sid_access_handle(handle.value, sid, windows._REVOKE_ACCESS)
        windows._local_free(sid)
        assert windows._close_owned_handle_bounded(handle)
    assert tuple(_state(path) for path in paths) == before


@pytest.mark.parametrize("parent_auto,child_auto", ((True, False), (False, True)))
@pytest.mark.parametrize("protected", (False, True))
def test_tiny_exact_lease_preserves_protected_mixed_and_excluded_acl_bytes(
    targets: _OwnedTargets, parent_auto: bool, child_auto: bool, protected: bool
) -> None:
    root, child, paths = _tree(targets)
    _set_fixture_controls(root, auto=parent_auto, protected=protected)
    _set_fixture_controls(child, auto=child_auto, protected=not protected)
    _set_fixture_controls(paths[3], auto=child_auto, protected=protected)
    before = tuple(_state(path) for path in paths)
    profile = windows._AppContainerProfile.create((root,))
    try:
        profile.grant_read_execute((root,))
        profile.require_read_execute((root, child, paths[2]))
        assert _state(root)[0] == before[0][0]
        assert _state(child)[0] == before[1][0]
        assert tuple(_state(path) for path in paths[3:]) == before[3:]
        assert profile.access.inventory is not None
        assert not any(
            resource.path in paths[3:] for resource in profile.access.inventory.resources
        )
    finally:
        assert windows._close_profile_bounded(profile)
    assert tuple(_state(path) for path in paths) == before
    assert AclRecoveryJournal(targets.journal).inventory() == ()


_DEAD_OWNER = r"""
import os
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from paperworkshell.worker.client import _windows as w
approved, journal = map(Path, sys.argv[2:4])
w._acl_recovery_directory_path = lambda: journal
w._ACL_MUTATION_MUTEX_NAME = sys.argv[4]
profile = w._AppContainerProfile.create((approved,))
profile.grant_read_execute((approved,))
w._reset_profile_storage(profile)
assert profile._deleted
os._exit(23)
"""


@pytest.mark.parametrize("protected", (False, True))
def test_supported_dead_owner_recovery_restores_original_full_acl_and_damaged_ai_flag(
    targets: _OwnedTargets, protected: bool
) -> None:
    root, child, paths = _tree(targets)
    _set_fixture_controls(root, auto=True, protected=protected)
    _set_fixture_controls(child, auto=False, protected=not protected)
    _set_fixture_controls(paths[3], auto=False, protected=protected)
    before = tuple(_state(path) for path in paths)
    script = targets.root / "fixed-owned-crash.py"
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
    entries = AclRecoveryJournal(targets.journal).inventory()
    assert len(entries) == 1
    assert not windows._ledger_owner_is_active(entries[0].owner_pid, entries[0].owner_created_100ns)
    assert tuple(_state(path) for path in paths[3:]) == before[3:]
    # Recreate only the known historical AI-control damage on this disposable
    # exact owned object; the journal's original True state remains authority.
    _set_fixture_controls(root, auto=False, protected=protected)
    assert _state(root)[0] != before[0][0]
    recovery = windows.recover_worker_acl_access((root,))
    assert recovery.repaired_leases == 1
    assert AclRecoveryJournal(targets.journal).inventory() == ()
    assert tuple(_state(path) for path in paths) == before

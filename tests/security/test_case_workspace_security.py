"""Adversarial PWS-0101 filesystem, permission, alias, and cleanup tests."""

from __future__ import annotations

import ctypes
import os
import struct
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from paperworkshell.storage._windows import WindowsHandle
from paperworkshell.storage.errors import WorkspaceError, WorkspaceErrorCode
from paperworkshell.storage.permissions import (
    PermissionVerification,
    WindowsPrivateDirectoryPermissions,
)
from paperworkshell.storage.quotas import MAXIMUM_WORKSPACE_DEPTH, WorkspaceQuota
from paperworkshell.storage.workspace import (
    CaseWorkspaceManager,
    CleanupReport,
    CleanupStatus,
    CleanupTarget,
)

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="PWS-0101 targets the supported Windows platform"
)

_GENERIC_WRITE = 0x4000_0000
_FILE_SHARE_READ = 0x1
_FILE_SHARE_WRITE = 0x2
_FILE_SHARE_DELETE = 0x4
_OPEN_EXISTING = 3
_FILE_FLAG_OPEN_REPARSE_POINT = 0x0020_0000
_FILE_FLAG_BACKUP_SEMANTICS = 0x0200_0000
_FSCTL_SET_REPARSE_POINT = 0x0009_00A4
_IO_REPARSE_TAG_MOUNT_POINT = 0xA000_0003
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def _quota() -> WorkspaceQuota:
    return WorkspaceQuota(1_000_000, 100_000, 100_000, 100, 10)


def _manager(profile: Path) -> CaseWorkspaceManager:
    return CaseWorkspaceManager(profile_root=profile, quota=_quota())


def _create_junction(link: Path, target: Path) -> None:
    """Create an NTFS directory junction without invoking a shell or interpolating a command."""

    link.mkdir()
    substitute = f"\\??\\{target.resolve()}"
    printable = str(target.resolve())
    substitute_bytes = substitute.encode("utf-16-le")
    printable_bytes = printable.encode("utf-16-le")
    path_buffer = substitute_bytes + b"\0\0" + printable_bytes + b"\0\0"
    payload = (
        struct.pack(
            "<IHHHHHH",
            _IO_REPARSE_TAG_MOUNT_POINT,
            8 + len(path_buffer),
            0,
            0,
            len(substitute_bytes),
            len(substitute_bytes) + 2,
            len(printable_bytes),
        )
        + path_buffer
    )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        ctypes.c_wchar_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
    )
    create_file.restype = ctypes.c_void_p
    device_io_control = kernel32.DeviceIoControl
    device_io_control.argtypes = (
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.c_void_p,
    )
    device_io_control.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (ctypes.c_void_p,)
    close_handle.restype = ctypes.c_int

    raw_handle = create_file(
        str(link),
        _GENERIC_WRITE,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    handle = int(raw_handle) if raw_handle else 0
    if not handle or handle == _INVALID_HANDLE_VALUE:
        pytest.fail("required Windows junction fixture could not be opened")
    try:
        returned = ctypes.c_ulong()
        buffer = ctypes.create_string_buffer(payload)
        if not device_io_control(
            ctypes.c_void_p(handle),
            _FSCTL_SET_REPARSE_POINT,
            buffer,
            len(payload),
            None,
            0,
            ctypes.byref(returned),
            None,
        ):
            pytest.fail("required Windows junction fixture could not be created")
    finally:
        close_handle(ctypes.c_void_p(handle))
    assert os.path.isjunction(link)


def _remove_junction(path: Path) -> None:
    if os.path.lexists(path):
        os.rmdir(path)


@pytest.mark.parametrize(
    "configured",
    [
        Path(r"\\server\share\cases"),
        Path(r"\\?\C:\cases"),
        Path(r"\\?\UNC\server\share\cases"),
        Path(r"\\.\PhysicalDrive0"),
        Path(r"\??\C:\cases"),
        Path(r"C:drive-relative"),
    ],
)
def test_nonlocal_device_and_drive_relative_roots_fail_before_filesystem_access(
    tmp_path: Path,
    configured: Path,
) -> None:
    with pytest.raises(WorkspaceError) as raised:
        CaseWorkspaceManager(profile_root=tmp_path, cases_root=configured, quota=_quota())

    assert raised.value.code in {
        WorkspaceErrorCode.PATH_INVALID,
        WorkspaceErrorCode.LOCATION_UNSUPPORTED,
    }
    assert str(configured) not in str(raised.value)


def test_cases_root_outside_profile_is_rejected_without_mutation(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    outside = tmp_path / "outside"
    profile.mkdir()
    outside.mkdir()

    with pytest.raises(WorkspaceError) as raised:
        CaseWorkspaceManager(profile_root=profile, cases_root=outside / "cases", quota=_quota())

    assert raised.value.code is WorkspaceErrorCode.LOCATION_UNSUPPORTED
    assert not (outside / "cases").exists()
    assert str(outside) not in str(raised.value)


def test_profile_root_junction_is_rejected_and_target_remains_unchanged(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    sentinel = target / "outside-sentinel.bin"
    sentinel.write_bytes(b"outside")
    link = tmp_path / "profile-link"
    _create_junction(link, target)
    try:
        manager = _manager(link)
        with pytest.raises(WorkspaceError) as raised:
            manager.provision()
        assert raised.value.code is WorkspaceErrorCode.REPARSE_POINT
        assert sentinel.read_bytes() == b"outside"
        assert not (target / "AppData").exists()
    finally:
        _remove_junction(link)


def test_cases_root_junction_is_rejected_without_touching_outside_target(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    target = tmp_path / "target"
    profile.mkdir()
    target.mkdir()
    sentinel = target / "outside-sentinel.bin"
    sentinel.write_bytes(b"outside")
    cases_link = profile / "cases"
    _create_junction(cases_link, target)
    try:
        manager = CaseWorkspaceManager(
            profile_root=profile,
            cases_root=cases_link,
            quota=_quota(),
        )
        with pytest.raises(WorkspaceError) as raised:
            manager.provision()
        assert raised.value.code is WorkspaceErrorCode.REPARSE_POINT
        assert sentinel.read_bytes() == b"outside"
    finally:
        _remove_junction(cases_link)


def test_nested_junction_blocks_cleanup_before_any_deletion(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    outside = tmp_path / "outside"
    profile.mkdir()
    outside.mkdir()
    sentinel = outside / "outside-sentinel.bin"
    sentinel.write_bytes(b"outside")
    manager = _manager(profile)
    layout = manager.provision()
    ordinary = layout.temp / f"{uuid4()}.tmp"
    ordinary.write_bytes(b"keep-when-inventory-fails")
    junction = layout.temp / f"{uuid4()}.bin"
    _create_junction(junction, outside)
    try:
        with pytest.raises(WorkspaceError) as raised:
            manager.cleanup_disposable(layout.case_id, CleanupTarget.TEMP)
        assert raised.value.code is WorkspaceErrorCode.REPARSE_POINT
        assert ordinary.read_bytes() == b"keep-when-inventory-fails"
        assert sentinel.read_bytes() == b"outside"
    finally:
        _remove_junction(junction)


def test_real_directory_symlink_is_rejected_when_account_has_symlink_capability(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "profile"
    outside = tmp_path / "outside"
    profile.mkdir()
    outside.mkdir()
    sentinel = outside / "outside-sentinel.bin"
    sentinel.write_bytes(b"outside")
    manager = _manager(profile)
    layout = manager.provision()
    link = layout.temp / f"{uuid4()}.bin"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError as error:
        if error.winerror == 1314:
            pytest.skip("current Windows account lacks symbolic-link privilege; junction test ran")
        raise
    try:
        with pytest.raises(WorkspaceError) as raised:
            manager.cleanup_disposable(layout.case_id, CleanupTarget.TEMP)
        assert raised.value.code is WorkspaceErrorCode.REPARSE_POINT
        assert sentinel.read_bytes() == b"outside"
    finally:
        link.unlink(missing_ok=True)


def test_hardlinked_disposable_file_is_rejected_without_unlinking_either_name(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "profile"
    outside = tmp_path / "outside"
    profile.mkdir()
    outside.mkdir()
    outside_file = outside / "outside-sentinel.bin"
    outside_file.write_bytes(b"outside-hardlink")
    manager = _manager(profile)
    layout = manager.provision()
    linked = layout.temp / f"{uuid4()}.bin"
    os.link(outside_file, linked)

    with pytest.raises(WorkspaceError) as raised:
        manager.cleanup_disposable(layout.case_id, CleanupTarget.TEMP)

    assert raised.value.code is WorkspaceErrorCode.ALIAS_DETECTED
    assert linked.read_bytes() == b"outside-hardlink"
    assert outside_file.read_bytes() == b"outside-hardlink"


def test_named_alternate_data_stream_is_rejected_from_quota_inventory(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    layout = manager.provision()
    ordinary = layout.temp / f"{uuid4()}.bin"
    ordinary.write_bytes(b"visible")
    with open(f"{ordinary}:synthetic-private", "wb") as stream:
        stream.write(b"hidden")

    with pytest.raises(WorkspaceError) as raised:
        manager.measure_usage(layout.case_id)

    assert raised.value.code is WorkspaceErrorCode.ALIAS_DETECTED
    assert "synthetic-private" not in str(raised.value)


@pytest.mark.parametrize("location", ["case", "temp", "nested"])
def test_directory_alternate_stream_is_rejected_from_inventory(
    tmp_path: Path,
    location: str,
) -> None:
    manager = _manager(tmp_path)
    layout = manager.provision()
    if location == "case":
        directory = layout.case_root
    elif location == "temp":
        directory = layout.temp
    else:
        directory = layout.temp / "nested"
        directory.mkdir(mode=0o700)
    with open(f"{directory}:synthetic-private", "wb") as stream:
        stream.write(b"hidden-directory-bytes")

    with pytest.raises(WorkspaceError) as raised:
        manager.measure_usage(layout.case_id)

    assert raised.value.code is WorkspaceErrorCode.ALIAS_DETECTED
    assert "synthetic-private" not in str(raised.value)


def test_directory_alternate_stream_blocks_cleanup_without_false_complete(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    layout = manager.provision()
    disposable = layout.temp / f"{uuid4()}.tmp"
    disposable.write_bytes(b"preserved")
    stream_path = f"{layout.temp}:synthetic-private"
    with open(stream_path, "wb") as stream:
        stream.write(b"hidden-directory-bytes")

    with pytest.raises(WorkspaceError) as raised:
        manager.cleanup_disposable(layout.case_id, CleanupTarget.TEMP)

    assert raised.value.code is WorkspaceErrorCode.ALIAS_DETECTED
    assert disposable.read_bytes() == b"preserved"
    with open(stream_path, "rb") as stream:
        assert stream.read() == b"hidden-directory-bytes"


@pytest.mark.parametrize("fixed_name", ["thumbnails", "previews"])
def test_cache_cleanup_rejects_fixed_directory_replaced_by_regular_file(
    tmp_path: Path,
    fixed_name: str,
) -> None:
    manager = _manager(tmp_path)
    layout = manager.provision()
    fixed_path = layout.cache / fixed_name
    os.rmdir(fixed_path)
    fixed_path.write_bytes(b"must-not-be-preserved-as-a-cache-root")
    ordinary = layout.cache / f"{uuid4()}.bin"
    ordinary.write_bytes(b"keep-when-layout-invalid")

    with pytest.raises(WorkspaceError) as raised:
        manager.cleanup_disposable(layout.case_id, CleanupTarget.CACHE)

    assert raised.value.code is WorkspaceErrorCode.PATH_INVALID
    assert fixed_path.read_bytes() == b"must-not-be-preserved-as-a-cache-root"
    assert ordinary.read_bytes() == b"keep-when-layout-invalid"


def test_inherited_permission_replacement_is_detected_before_cleanup(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    layout = manager.provision()
    os.rmdir(layout.temp)
    os.mkdir(layout.temp)
    marker = layout.temp / f"{uuid4()}.tmp"
    marker.write_bytes(b"not-private")

    with pytest.raises(WorkspaceError) as raised:
        manager.cleanup_disposable(layout.case_id, CleanupTarget.TEMP)

    assert raised.value.code is WorkspaceErrorCode.PERMISSIONS_INSECURE
    assert marker.read_bytes() == b"not-private"


def test_terminal_file_identity_swap_returns_partial_without_deleting_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(tmp_path)
    layout = manager.provision()
    candidate = layout.temp / f"{uuid4()}.tmp"
    candidate.write_bytes(b"first-identity")
    original_delete = manager._delete_inventory

    def swap_then_delete(
        target: CleanupTarget,
        target_path: Path,
        target_handle: WindowsHandle,
        entries: list[Any],
    ) -> CleanupReport:
        candidate.unlink()
        candidate.write_bytes(b"replacement-identity")
        return original_delete(target, target_path, target_handle, entries)

    monkeypatch.setattr(manager, "_delete_inventory", swap_then_delete)
    report = manager.cleanup_disposable(layout.case_id, CleanupTarget.TEMP)

    assert report.status is CleanupStatus.PARTIAL
    assert report.failure_code is WorkspaceErrorCode.IDENTITY_CHANGED
    assert candidate.read_bytes() == b"replacement-identity"


def test_terminal_directory_swap_to_junction_never_traverses_outside(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = tmp_path / "profile"
    outside = tmp_path / "outside"
    profile.mkdir()
    outside.mkdir()
    sentinel = outside / "outside-sentinel.bin"
    sentinel.write_bytes(b"outside")
    manager = _manager(profile)
    layout = manager.provision()
    candidate = layout.temp / str(uuid4())
    candidate.mkdir(mode=0o700)
    original_delete = manager._delete_inventory

    def swap_then_delete(
        target: CleanupTarget,
        target_path: Path,
        target_handle: WindowsHandle,
        entries: list[Any],
    ) -> CleanupReport:
        os.rmdir(candidate)
        _create_junction(candidate, outside)
        return original_delete(target, target_path, target_handle, entries)

    monkeypatch.setattr(manager, "_delete_inventory", swap_then_delete)
    try:
        report = manager.cleanup_disposable(layout.case_id, CleanupTarget.TEMP)
        assert report.status is CleanupStatus.PARTIAL
        assert report.failure_code is WorkspaceErrorCode.REPARSE_POINT
        assert sentinel.read_bytes() == b"outside"
    finally:
        _remove_junction(candidate)


def test_cleanup_preserves_originals_records_sibling_case_and_outside_data(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    first = manager.provision(UUID("40000000-0000-4000-8000-000000000001"))
    second = manager.provision(UUID("40000000-0000-4000-8000-000000000002"))
    original = first.originals / f"{uuid4()}.bin"
    journal = first.journal / f"{uuid4()}.json"
    original.write_bytes(b"immutable-original")
    journal.write_bytes(b"journal-record")
    first.manifest.write_bytes(b"manifest-record")
    first.export_history.write_bytes(b"history-record")
    sibling = second.temp / f"{uuid4()}.tmp"
    sibling.write_bytes(b"sibling-case")
    disposable = first.temp / f"{uuid4()}.tmp"
    disposable.write_bytes(b"disposable")
    outside = tmp_path / "outside-sentinel.bin"
    outside.write_bytes(b"outside")

    report = manager.cleanup_disposable(first.case_id, CleanupTarget.TEMP)

    assert report.status is CleanupStatus.COMPLETE
    assert not disposable.exists()
    assert original.read_bytes() == b"immutable-original"
    assert journal.read_bytes() == b"journal-record"
    assert first.manifest.read_bytes() == b"manifest-record"
    assert first.export_history.read_bytes() == b"history-record"
    assert sibling.read_bytes() == b"sibling-case"
    assert outside.read_bytes() == b"outside"


@pytest.mark.parametrize("swapped_name", ["case-root", "cache"])
def test_mid_provision_parent_swap_is_rejected_before_outside_child_mutation(
    tmp_path: Path,
    swapped_name: str,
) -> None:
    profile = tmp_path / "profile"
    outside = tmp_path / "outside"
    profile.mkdir()
    outside.mkdir()
    sentinel = outside / "outside-sentinel.bin"
    sentinel.write_bytes(b"outside")
    case_id = UUID("40000000-0000-4000-8000-000000000010")
    delegate = WindowsPrivateDirectoryPermissions()
    swapped_path: Path | None = None

    class SwappingPermissions:
        def create_private_directory(
            self,
            path: Path,
            *,
            context: str,
        ) -> PermissionVerification:
            nonlocal swapped_path
            verification = delegate.create_private_directory(path, context=context)
            selected = (swapped_name == "case-root" and path.name == str(case_id)) or (
                swapped_name == "cache" and path.name == "cache"
            )
            if selected:
                os.rmdir(path)
                _create_junction(path, outside)
                swapped_path = path
            return verification

        def verify_private_directory(
            self,
            path: Path,
            *,
            context: str,
        ) -> PermissionVerification:
            return delegate.verify_private_directory(path, context=context)

    manager = CaseWorkspaceManager(
        profile_root=profile,
        quota=_quota(),
        permissions=SwappingPermissions(),
    )
    try:
        with pytest.raises(WorkspaceError) as raised:
            manager.provision(case_id)

        assert raised.value.code in {
            WorkspaceErrorCode.IDENTITY_CHANGED,
            WorkspaceErrorCode.REPARSE_POINT,
        }
        assert sentinel.read_bytes() == b"outside"
        assert {path.name for path in outside.iterdir()} == {"outside-sentinel.bin"}
    finally:
        if swapped_path is not None:
            _remove_junction(swapped_path)


@pytest.mark.parametrize("reserved_name", ["manifest.json", "export-history.json"])
def test_dangling_reserved_record_junction_blocks_provision(
    tmp_path: Path,
    reserved_name: str,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    delegate = WindowsPrivateDirectoryPermissions()
    reserved_path: Path | None = None

    class InjectingPermissions:
        def create_private_directory(
            self,
            path: Path,
            *,
            context: str,
        ) -> PermissionVerification:
            nonlocal reserved_path
            verification = delegate.create_private_directory(path, context=context)
            if path.name == "temp":
                reserved_path = path.parent / reserved_name
                _create_junction(reserved_path, outside / "missing-target")
            return verification

        def verify_private_directory(
            self,
            path: Path,
            *,
            context: str,
        ) -> PermissionVerification:
            return delegate.verify_private_directory(path, context=context)

    manager = CaseWorkspaceManager(
        profile_root=tmp_path / "profile",
        quota=_quota(),
        permissions=InjectingPermissions(),
    )
    (tmp_path / "profile").mkdir()
    try:
        with pytest.raises(WorkspaceError) as raised:
            manager.provision()

        assert raised.value.code is WorkspaceErrorCode.CREATE_FAILED
        assert reserved_path is not None
        assert os.path.lexists(reserved_path)
        assert not reserved_path.exists()
    finally:
        if reserved_path is not None:
            _remove_junction(reserved_path)


def test_inventory_consumes_only_remaining_entry_budget_plus_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = CaseWorkspaceManager(
        profile_root=tmp_path,
        quota=WorkspaceQuota(1_000_000, 100_000, 100_000, 6, 2),
    )
    layout = manager.provision()
    real_scandir = os.scandir
    consumed = 0

    class UnboundedSyntheticDirectory:
        def __enter__(self) -> UnboundedSyntheticDirectory:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def __iter__(self) -> UnboundedSyntheticDirectory:
            return self

        def __next__(self) -> SimpleNamespace:
            nonlocal consumed
            consumed += 1
            return SimpleNamespace(name=f"item-{consumed}")

    def bounded_scandir(path: Path) -> Any:
        if Path(path) == layout.temp:
            return UnboundedSyntheticDirectory()
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", bounded_scandir)

    with pytest.raises(WorkspaceError) as raised:
        manager.measure_usage(layout.case_id)

    assert raised.value.code is WorkspaceErrorCode.SCAN_LIMIT_EXCEEDED
    assert consumed == 1


def test_inventory_depth_cap_returns_stable_error_before_python_recursion_limit(
    tmp_path: Path,
) -> None:
    manager = CaseWorkspaceManager(
        profile_root=tmp_path,
        quota=WorkspaceQuota(1_000_000, 100_000, 100_000, 100, MAXIMUM_WORKSPACE_DEPTH),
    )
    layout = manager.provision()
    deepest = layout.temp
    for _ in range(MAXIMUM_WORKSPACE_DEPTH):
        deepest = deepest / "a"
        deepest.mkdir(mode=0o700)

    with pytest.raises(WorkspaceError) as raised:
        manager.measure_usage(layout.case_id)

    assert raised.value.code is WorkspaceErrorCode.SCAN_LIMIT_EXCEEDED
    assert raised.value.context == "inventory.depth"

"""Retained Win32 authority for one PWS-0106 benchmark stage.

This module is parent-only.  The staged worker bootstrap does not import it.  It builds on the
already-reviewed storage Win32 handles so every ancestor, directory, and file remains pinned while
the stage is populated, consumed, audited, and removed.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Final, NoReturn

from paperworkshell.storage._windows import (
    _TOKEN_USER_CLASS,
    FileIdentity,
    WindowsHandle,
    _query_opened_info,
    _token_sid,
    open_handle,
    require_expected_handle,
)

_FILE_DISPOSITION_INFO_EX_CLASS: Final = 21
_FILE_DISPOSITION_FLAG_DELETE: Final = 0x0000_0001
_FILE_DISPOSITION_FLAG_POSIX_SEMANTICS: Final = 0x0000_0002
_FILE_DISPOSITION_FLAG_IGNORE_READONLY_ATTRIBUTE: Final = 0x0000_0010
_INVALID_HANDLE_VALUE: Final = ctypes.c_void_p(-1).value
_READ_CONTROL: Final = 0x0002_0000
_WRITE_DAC: Final = 0x0004_0000
_DELETE: Final = 0x0001_0000
_GENERIC_READ: Final = 0x8000_0000
_GENERIC_WRITE: Final = 0x4000_0000
_FILE_READ_ATTRIBUTES: Final = 0x0000_0080
_FILE_SHARE_READ: Final = 0x0000_0001
_FILE_SHARE_WRITE: Final = 0x0000_0002
_FILE_SHARE_DELETE: Final = 0x0000_0004
_CREATE_NEW: Final = 1
_OPEN_EXISTING: Final = 3
_FILE_ATTRIBUTE_READONLY: Final = 0x0000_0001
_FILE_FLAG_OPEN_REPARSE_POINT: Final = 0x0020_0000
_FILE_FLAG_BACKUP_SEMANTICS: Final = 0x0200_0000
_FILE_FLAG_SEQUENTIAL_SCAN: Final = 0x0800_0000
_FILE_ATTRIBUTE_SPARSE_FILE: Final = 0x0000_0200
_UNSUPPORTED_SOURCE_ATTRIBUTES: Final = 0x0000_1000 | 0x0004_0000 | 0x0040_0000
_READ_CHUNK_BYTES: Final = 1024 * 1024

_SE_FILE_OBJECT: Final = 1
_DACL_SECURITY_INFORMATION: Final = 0x0000_0004
_DENY_ACCESS: Final = 3
_NO_INHERITANCE: Final = 0
_TRUSTEE_IS_SID: Final = 0
_TRUSTEE_IS_UNKNOWN: Final = 0
_ACCESS_DENIED_ACE_TYPE: Final = 1
_INHERIT_ONLY_ACE: Final = 0x08
_ACL_SIZE_INFORMATION_CLASS: Final = 2
_SE_DACL_PRESENT: Final = 0x0004
_SE_DACL_AUTO_INHERITED: Final = 0x0400
_DACL_CONTROL_MASK: Final = 0x150C
_FILE_ADD_FILE: Final = 0x0000_0002
_FILE_ADD_SUBDIRECTORY: Final = 0x0000_0004
_FILE_DELETE_CHILD: Final = 0x0000_0040
_NAMESPACE_MUTATION_ACCESS: Final = (
    _FILE_ADD_FILE | _FILE_ADD_SUBDIRECTORY | _FILE_DELETE_CHILD | _DELETE
)
_ERROR_SUCCESS: Final = 0


class _FileDispositionInfoEx(ctypes.Structure):
    _fields_ = [("flags", wintypes.DWORD)]


class _TrusteeW(ctypes.Structure):
    _fields_ = [
        ("multiple_trustee", ctypes.c_void_p),
        ("multiple_trustee_operation", ctypes.c_int),
        ("trustee_form", ctypes.c_int),
        ("trustee_type", ctypes.c_int),
        ("name", wintypes.LPWSTR),
    ]


class _ExplicitAccessW(ctypes.Structure):
    _fields_ = [
        ("access_permissions", wintypes.DWORD),
        ("access_mode", ctypes.c_int),
        ("inheritance", wintypes.DWORD),
        ("trustee", _TrusteeW),
    ]


class _AclSizeInformation(ctypes.Structure):
    _fields_ = [
        ("ace_count", wintypes.DWORD),
        ("acl_bytes_in_use", wintypes.DWORD),
        ("acl_bytes_free", wintypes.DWORD),
    ]


class _AceHeader(ctypes.Structure):
    _fields_ = [
        ("ace_type", wintypes.BYTE),
        ("ace_flags", wintypes.BYTE),
        ("ace_size", wintypes.WORD),
    ]


class _AccessDeniedAce(ctypes.Structure):
    _fields_ = [
        ("header", _AceHeader),
        ("mask", wintypes.DWORD),
        ("sid_start", wintypes.DWORD),
    ]


@dataclass(frozen=True, slots=True, repr=False)
class _DirectoryPolicy:
    handle: WindowsHandle
    identity: FileIdentity
    original_dacl: bytes = field(repr=False)
    original_control: int


class StageWindowsFailure(RuntimeError):
    """Sanitized failure from retained benchmark-stage authority."""

    def __init__(
        self,
        message: str,
        *,
        root_cleanup_completed: bool = False,
    ) -> None:
        super().__init__(message)
        self.root_cleanup_completed = root_cleanup_completed


def _fail() -> NoReturn:
    raise StageWindowsFailure("benchmark stage authority failed") from None


def _normalized(path: Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _relative(value: PurePosixPath) -> PurePosixPath:
    if (
        type(value) is not PurePosixPath
        or value.is_absolute()
        or not value.parts
        or any(part in {"", ".", ".."} for part in value.parts)
    ):
        _fail()
    return value


def _path_chain(path: Path) -> tuple[Path, ...]:
    if not path.is_absolute() or not path.anchor:
        _fail()
    current = Path(path.anchor)
    output = [current]
    for component in path.parts[1:]:
        current /= component
        output.append(current)
    return tuple(output)


def _same_handle(
    handle: WindowsHandle,
    expected_path: Path,
    expected_identity: FileIdentity,
    *,
    directory: bool,
) -> None:
    try:
        current = handle.refresh_info()
        require_expected_handle(
            handle,
            expected_path,
            directory=directory,
            expected_identity=expected_identity,
        )
    except Exception:
        _fail()
    if (
        current.is_reparse_point
        or current.is_directory is not directory
        or (not directory and (current.is_sparse or current.link_count != 1))
    ):
        _fail()


def _open_directory_policy_handle(path: Path) -> WindowsHandle:
    """Open one exact directory for retained DACL control without delete authority."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    raw = create_file(
        str(path),
        _READ_CONTROL | _WRITE_DAC | _FILE_READ_ATTRIBUTES,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    value = int(raw) if raw else 0
    if not value or value == _INVALID_HANDLE_VALUE:
        _fail()
    try:
        handle = WindowsHandle(value, _query_opened_info(value))
        require_expected_handle(handle, path, directory=True)
        if handle.info.is_reparse_point:
            _fail()
        return handle
    except BaseException:
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        close_handle(wintypes.HANDLE(value))
        raise


def _local_free(pointer: ctypes.c_void_p) -> None:
    if not pointer.value:
        return
    local_free = ctypes.WinDLL("kernel32", use_last_error=True).LocalFree
    local_free.argtypes = (ctypes.c_void_p,)
    local_free.restype = ctypes.c_void_p
    local_free(pointer)


def _capture_handle_dacl(handle: WindowsHandle) -> tuple[bytes, int]:
    if handle.value == 0:
        _fail()
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    get_security = advapi32.GetSecurityInfo
    get_security.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    )
    get_security.restype = wintypes.DWORD
    get_control = advapi32.GetSecurityDescriptorControl
    get_control.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    )
    get_control.restype = wintypes.BOOL
    get_acl_information = advapi32.GetAclInformation
    get_acl_information.argtypes = (
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_int,
    )
    get_acl_information.restype = wintypes.BOOL

    descriptor = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    status = get_security(
        wintypes.HANDLE(handle.value),
        _SE_FILE_OBJECT,
        _DACL_SECURITY_INFORMATION,
        None,
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if status != _ERROR_SUCCESS or not descriptor.value or not dacl.value:
        _local_free(descriptor)
        _fail()
    try:
        control = wintypes.WORD()
        revision = wintypes.DWORD()
        information = _AclSizeInformation()
        if not get_control(
            descriptor,
            ctypes.byref(control),
            ctypes.byref(revision),
        ) or not get_acl_information(
            dacl,
            ctypes.byref(information),
            ctypes.sizeof(information),
            _ACL_SIZE_INFORMATION_CLASS,
        ):
            _fail()
        size = int(information.acl_bytes_in_use)
        if not int(control.value) & _SE_DACL_PRESENT or not 8 <= size <= 65_535:
            _fail()
        return ctypes.string_at(dacl, size), int(control.value)
    finally:
        _local_free(descriptor)


def _capture_handle_security_descriptor(handle: WindowsHandle) -> bytes:
    if handle.value == 0:
        _fail()
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    get_security = advapi32.GetSecurityInfo
    get_security.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    )
    get_security.restype = wintypes.DWORD
    get_length = advapi32.GetSecurityDescriptorLength
    get_length.argtypes = (ctypes.c_void_p,)
    get_length.restype = wintypes.DWORD
    descriptor = ctypes.c_void_p()
    status = get_security(
        wintypes.HANDLE(handle.value),
        _SE_FILE_OBJECT,
        _DACL_SECURITY_INFORMATION,
        None,
        None,
        None,
        None,
        ctypes.byref(descriptor),
    )
    if status != _ERROR_SUCCESS or not descriptor.value:
        _local_free(descriptor)
        _fail()
    try:
        size = int(get_length(descriptor))
        if not 20 <= size <= 65_535:
            _fail()
        return ctypes.string_at(descriptor, size)
    finally:
        _local_free(descriptor)


def _restore_auto_inherited_control(
    handle: WindowsHandle,
    path: Path,
    expected_control: int,
) -> None:
    """Restore the one DACL control bit SetSecurityInfo may normalize."""

    descriptor = bytearray(_capture_handle_security_descriptor(handle))
    control = int.from_bytes(descriptor[2:4], "little")
    desired = (
        control | _SE_DACL_AUTO_INHERITED
        if expected_control & _SE_DACL_AUTO_INHERITED
        else control & ~_SE_DACL_AUTO_INHERITED
    )
    if desired == control:
        return
    descriptor[2:4] = desired.to_bytes(2, "little")
    _same_handle(handle, path, handle.info.identity, directory=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    set_security = advapi32.SetFileSecurityW
    set_security.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p)
    set_security.restype = wintypes.BOOL
    storage = ctypes.create_string_buffer(bytes(descriptor))
    if not set_security(
        str(path),
        _DACL_SECURITY_INFORMATION,
        ctypes.cast(storage, ctypes.c_void_p),
    ):
        _fail()
    _same_handle(handle, path, handle.info.identity, directory=True)
    _dacl, restored_control = _capture_handle_dacl(handle)
    if bool(restored_control & _SE_DACL_AUTO_INHERITED) is not bool(
        expected_control & _SE_DACL_AUTO_INHERITED
    ):
        _fail()


def _set_handle_dacl(handle: WindowsHandle, dacl: bytes) -> None:
    if handle.value == 0 or not 8 <= len(dacl) <= 65_535:
        _fail()
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    set_security = advapi32.SetSecurityInfo
    set_security.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )
    set_security.restype = wintypes.DWORD
    storage = ctypes.create_string_buffer(dacl)
    status = set_security(
        wintypes.HANDLE(handle.value),
        _SE_FILE_OBJECT,
        _DACL_SECURITY_INFORMATION,
        None,
        None,
        ctypes.cast(storage, ctypes.c_void_p),
        None,
    )
    if status != _ERROR_SUCCESS:
        _fail()


def _current_user_sid() -> bytes:
    try:
        token_storage, sid_value = _token_sid(_TOKEN_USER_CLASS)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        is_valid_sid = advapi32.IsValidSid
        is_valid_sid.argtypes = (ctypes.c_void_p,)
        is_valid_sid.restype = wintypes.BOOL
        get_length_sid = advapi32.GetLengthSid
        get_length_sid.argtypes = (ctypes.c_void_p,)
        get_length_sid.restype = wintypes.DWORD
        sid = ctypes.c_void_p(sid_value)
        if not is_valid_sid(sid):
            _fail()
        size = int(get_length_sid(sid))
        if not 8 <= size <= len(token_storage):
            _fail()
        return ctypes.string_at(sid, size)
    except StageWindowsFailure:
        raise
    except Exception:
        _fail()


def _deny_namespace_mutation(
    handle: WindowsHandle,
    path: Path,
    sid: bytes,
    expected_control: int,
) -> None:
    old_dacl, _control = _capture_handle_dacl(handle)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    set_entries = advapi32.SetEntriesInAclW
    set_entries.argtypes = (
        wintypes.ULONG,
        ctypes.POINTER(_ExplicitAccessW),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    )
    set_entries.restype = wintypes.DWORD
    sid_storage = ctypes.create_string_buffer(sid)
    old_dacl_storage = ctypes.create_string_buffer(old_dacl)
    new_dacl = ctypes.c_void_p()
    entry = _ExplicitAccessW(
        access_permissions=_NAMESPACE_MUTATION_ACCESS,
        access_mode=_DENY_ACCESS,
        inheritance=_NO_INHERITANCE,
        trustee=_TrusteeW(
            multiple_trustee=None,
            multiple_trustee_operation=0,
            trustee_form=_TRUSTEE_IS_SID,
            trustee_type=_TRUSTEE_IS_UNKNOWN,
            name=ctypes.cast(sid_storage, wintypes.LPWSTR),
        ),
    )
    status = set_entries(
        1,
        ctypes.byref(entry),
        ctypes.cast(old_dacl_storage, ctypes.c_void_p),
        ctypes.byref(new_dacl),
    )
    if status != _ERROR_SUCCESS or not new_dacl.value:
        _local_free(new_dacl)
        _fail()
    try:
        information = _AclSizeInformation()
        get_acl_information = advapi32.GetAclInformation
        get_acl_information.argtypes = (
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_int,
        )
        get_acl_information.restype = wintypes.BOOL
        if not get_acl_information(
            new_dacl,
            ctypes.byref(information),
            ctypes.sizeof(information),
            _ACL_SIZE_INFORMATION_CLASS,
        ):
            _fail()
        size = int(information.acl_bytes_in_use)
        if not 8 <= size <= 65_535:
            _fail()
        _set_handle_dacl(handle, ctypes.string_at(new_dacl, size))
        _restore_auto_inherited_control(handle, path, expected_control)
    finally:
        _local_free(new_dacl)


def _sid_denied_mask(dacl: bytes, sid: bytes) -> int:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    get_acl_information = advapi32.GetAclInformation
    get_acl_information.argtypes = (
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_int,
    )
    get_acl_information.restype = wintypes.BOOL
    get_ace = advapi32.GetAce
    get_ace.argtypes = (
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    )
    get_ace.restype = wintypes.BOOL
    equal_sid = advapi32.EqualSid
    equal_sid.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    equal_sid.restype = wintypes.BOOL

    dacl_storage = ctypes.create_string_buffer(dacl)
    dacl_pointer = ctypes.cast(dacl_storage, ctypes.c_void_p)
    sid_storage = ctypes.create_string_buffer(sid)
    sid_pointer = ctypes.cast(sid_storage, ctypes.c_void_p)
    information = _AclSizeInformation()
    if not get_acl_information(
        dacl_pointer,
        ctypes.byref(information),
        ctypes.sizeof(information),
        _ACL_SIZE_INFORMATION_CLASS,
    ):
        _fail()
    denied = 0
    for index in range(int(information.ace_count)):
        ace_pointer = ctypes.c_void_p()
        if not get_ace(dacl_pointer, index, ctypes.byref(ace_pointer)) or not ace_pointer.value:
            _fail()
        address = ace_pointer.value
        if address is None:
            _fail()
        ace = ctypes.cast(ace_pointer, ctypes.POINTER(_AccessDeniedAce)).contents
        if (
            ace.header.ace_type != _ACCESS_DENIED_ACE_TYPE
            or ace.header.ace_flags & _INHERIT_ONLY_ACE
            or ace.header.ace_size < ctypes.sizeof(_AccessDeniedAce)
        ):
            continue
        ace_sid = ctypes.c_void_p(address + _AccessDeniedAce.sid_start.offset)
        if equal_sid(ace_sid, sid_pointer):
            denied |= int(ace.mask)
    return denied


def _require_policy_sealed(policy: _DirectoryPolicy, path: Path, sid: bytes) -> None:
    _same_handle(policy.handle, path, policy.identity, directory=True)
    dacl, control = _capture_handle_dacl(policy.handle)
    if (
        control & _DACL_CONTROL_MASK != policy.original_control & _DACL_CONTROL_MASK
        or _sid_denied_mask(dacl, sid) & _NAMESPACE_MUTATION_ACCESS != _NAMESPACE_MUTATION_ACCESS
    ):
        _fail()


def _restore_policy(policy: _DirectoryPolicy, path: Path) -> None:
    _same_handle(policy.handle, path, policy.identity, directory=True)
    _set_handle_dacl(policy.handle, policy.original_dacl)
    _restore_auto_inherited_control(policy.handle, path, policy.original_control)
    restored_dacl, restored_control = _capture_handle_dacl(policy.handle)
    if (
        restored_dacl != policy.original_dacl
        or restored_control & _DACL_CONTROL_MASK != policy.original_control & _DACL_CONTROL_MASK
    ):
        _fail()


def _hash_handle(handle: WindowsHandle, maximum_bytes: int) -> tuple[int, str]:
    try:
        initial = handle.refresh_info()
        if (
            initial.is_directory
            or initial.is_reparse_point
            or initial.is_sparse
            or initial.link_count != 1
            or not 0 <= initial.logical_size <= maximum_bytes
        ):
            _fail()
        handle.rewind()
        digest = hashlib.sha256()
        remaining = initial.logical_size
        while remaining:
            chunk = handle.read_exact(min(_READ_CHUNK_BYTES, remaining))
            digest.update(chunk)
            remaining -= len(chunk)
        current = handle.require_eof()
    except StageWindowsFailure:
        raise
    except Exception:
        _fail()
    if current.identity != initial.identity or current.logical_size != initial.logical_size:
        _fail()
    return initial.logical_size, digest.hexdigest()


def _mark_delete(handle: WindowsHandle) -> None:
    if handle.value == 0:
        _fail()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    set_information = kernel32.SetFileInformationByHandle
    set_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    set_information.restype = wintypes.BOOL
    disposition = _FileDispositionInfoEx(
        _FILE_DISPOSITION_FLAG_DELETE
        | _FILE_DISPOSITION_FLAG_POSIX_SEMANTICS
        | _FILE_DISPOSITION_FLAG_IGNORE_READONLY_ATTRIBUTE
    )
    if not set_information(
        wintypes.HANDLE(handle.value),
        _FILE_DISPOSITION_INFO_EX_CLASS,
        ctypes.byref(disposition),
        ctypes.sizeof(disposition),
    ):
        _fail()


def _create_retained_file(path: Path) -> WindowsHandle:
    """CREATE_NEW one file and return the same non-share-delete handle."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    raw = create_file(
        str(path),
        _READ_CONTROL | _GENERIC_READ | _GENERIC_WRITE | _FILE_READ_ATTRIBUTES,
        _FILE_SHARE_READ,
        None,
        _CREATE_NEW,
        _FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_SEQUENTIAL_SCAN,
        None,
    )
    value = int(raw) if raw else 0
    if not value or value == _INVALID_HANDLE_VALUE:
        _fail()
    try:
        handle = WindowsHandle(value, _query_opened_info(value))
        require_expected_handle(handle, path, directory=False)
        if handle.info.is_sparse or handle.info.link_count != 1:
            _fail()
        return handle
    except BaseException:
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        close_handle(wintypes.HANDLE(value))
        raise


def _write_all(handle: WindowsHandle, data: bytes) -> None:
    if handle.value == 0 or not data or len(data) > _READ_CHUNK_BYTES:
        _fail()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    write_file = kernel32.WriteFile
    write_file.argtypes = (
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    )
    write_file.restype = wintypes.BOOL
    buffer = ctypes.create_string_buffer(data)
    completed = 0
    while completed < len(data):
        written = wintypes.DWORD()
        remaining = len(data) - completed
        if not write_file(
            wintypes.HANDLE(handle.value),
            ctypes.byref(buffer, completed),
            remaining,
            ctypes.byref(written),
            None,
        ):
            _fail()
        amount = int(written.value)
        if amount <= 0 or amount > remaining:
            _fail()
        completed += amount


def _seal_readonly(handle: WindowsHandle, path: Path, expected_size: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    flush = kernel32.FlushFileBuffers
    flush.argtypes = (wintypes.HANDLE,)
    flush.restype = wintypes.BOOL
    set_attributes = kernel32.SetFileAttributesW
    set_attributes.argtypes = (wintypes.LPCWSTR, wintypes.DWORD)
    set_attributes.restype = wintypes.BOOL
    if not flush(wintypes.HANDLE(handle.value)) or not set_attributes(
        str(path), _FILE_ATTRIBUTE_READONLY
    ):
        _fail()
    current = handle.refresh_info()
    if (
        current.is_directory
        or current.is_reparse_point
        or current.is_sparse
        or current.link_count != 1
        or current.logical_size != expected_size
    ):
        _fail()


def _downgrade_written_file(
    handle: WindowsHandle,
    path: Path,
    identity: FileIdentity,
    expected_size: int,
    expected_digest: str,
    maximum_bytes: int,
) -> WindowsHandle:
    """Replace a writer with a read-only retained handle and reauthenticate the leaf."""

    handle.close()
    retained = open_handle(path, read=True)
    try:
        _same_handle(retained, path, identity, directory=False)
        if _hash_handle(retained, maximum_bytes) != (expected_size, expected_digest):
            _fail()
    except BaseException:
        try:
            retained.close()
        except Exception:
            pass
        raise
    return retained


@dataclass(slots=True, repr=False)
class PinnedStageTree:
    """Exact retained authority for one fixed-drive stage tree."""

    root: Path
    _ancestor_handles: list[tuple[Path, WindowsHandle, FileIdentity]] = field(default_factory=list)
    _root_handle: WindowsHandle | None = None
    _root_identity: FileIdentity | None = None
    _directories: dict[PurePosixPath, tuple[WindowsHandle, FileIdentity]] = field(
        default_factory=dict
    )
    _files: dict[
        PurePosixPath,
        tuple[WindowsHandle, FileIdentity, int | None, str | None],
    ] = field(default_factory=dict)
    _namespace_policies: dict[PurePosixPath | None, _DirectoryPolicy] = field(default_factory=dict)
    _namespace_sid: bytes | None = field(default=None, repr=False)

    @classmethod
    def open(cls, root: Path) -> PinnedStageTree:
        """Pin every ancestor and the exact newly created stage root."""

        authority = cls(root)
        try:
            chain = _path_chain(root)
            root_handle = open_handle(root)
            try:
                require_expected_handle(root_handle, root, directory=True)
                if root_handle.info.is_reparse_point:
                    _fail()
            except BaseException:
                try:
                    root_handle.close()
                except Exception:
                    pass
                raise
            authority._root_handle = root_handle
            authority._root_identity = root_handle.info.identity
            for path in chain[:-1]:
                handle = open_handle(path)
                try:
                    require_expected_handle(handle, path, directory=True)
                    if handle.info.is_reparse_point:
                        _fail()
                except BaseException:
                    try:
                        handle.close()
                    except Exception:
                        pass
                    raise
                authority._ancestor_handles.append((path, handle, handle.info.identity))
            return authority
        except BaseException as cause:
            root_cleanup_completed = False
            if authority._root_handle is not None:
                try:
                    authority.delete(None, 0)
                except BaseException:
                    try:
                        authority.close()
                    except StageWindowsFailure:
                        pass
                else:
                    root_cleanup_completed = True
            else:
                try:
                    authority.close()
                except StageWindowsFailure:
                    pass
            if isinstance(cause, Exception):
                raise StageWindowsFailure(
                    "benchmark stage authority failed",
                    root_cleanup_completed=root_cleanup_completed,
                ) from None
            raise

    @property
    def root_identity(self) -> FileIdentity:
        if self._root_identity is None:
            _fail()
        return self._root_identity

    @property
    def file_paths(self) -> frozenset[PurePosixPath]:
        return frozenset(self._files)

    @property
    def sealed_hashes(self) -> dict[PurePosixPath, str]:
        """Return a copy of every completely sealed file hash."""

        return {
            relative: digest
            for relative, (_handle, _identity, _size, digest) in self._files.items()
            if digest is not None
        }

    @property
    def sealed_files(self) -> dict[PurePosixPath, tuple[int, str]]:
        """Return immutable size/hash facts for every completely sealed file."""

        return {
            relative: (size, digest)
            for relative, (_handle, _identity, size, digest) in self._files.items()
            if size is not None and digest is not None
        }

    @property
    def namespace_sealed(self) -> bool:
        return bool(self._namespace_policies) and self._namespace_sid is not None

    def _policy_path(self, relative: PurePosixPath | None) -> Path:
        return self.root if relative is None else self.root.joinpath(*relative.parts)

    def _expected_policy_keys(self) -> set[PurePosixPath | None]:
        return {None, *self._directories}

    def _restore_all_namespace_policies(self) -> None:
        failed = False
        ordered = sorted(
            self._namespace_policies,
            key=lambda item: -1 if item is None else len(item.parts),
            reverse=True,
        )
        for relative in ordered:
            policy = self._namespace_policies[relative]
            try:
                _restore_policy(policy, self._policy_path(relative))
            except StageWindowsFailure:
                failed = True
        if failed:
            _fail()
        for relative in ordered:
            try:
                self._namespace_policies[relative].handle.close()
            except Exception:
                failed = True
        if failed:
            _fail()
        self._namespace_policies.clear()
        self._namespace_sid = None

    def require_namespace_sealed(self) -> None:
        """Require every retained stage directory to deny ordinary namespace mutation."""

        self.require_live()
        sid = self._namespace_sid
        if sid is None or set(self._namespace_policies) != self._expected_policy_keys():
            _fail()
        for relative, policy in self._namespace_policies.items():
            _require_policy_sealed(policy, self._policy_path(relative), sid)

    def seal_namespace(
        self,
        expected: dict[PurePosixPath, str],
        maximum_bytes: int,
    ) -> None:
        """Deny current-user create/delete operations in every exact stage directory."""

        if self._namespace_policies or self._namespace_sid is not None:
            self.require_namespace_sealed()
            self.audit(expected, maximum_bytes)
            return
        self.audit(expected, maximum_bytes)
        policies: dict[PurePosixPath | None, _DirectoryPolicy] = {}
        try:
            directory_entries: list[
                tuple[PurePosixPath | None, Path, WindowsHandle, FileIdentity]
            ] = []
            if self._root_handle is None or self._root_identity is None:
                _fail()
            directory_entries.append((None, self.root, self._root_handle, self._root_identity))
            directory_entries.extend(
                (
                    relative,
                    self.root.joinpath(*relative.parts),
                    retained,
                    identity,
                )
                for relative, (retained, identity) in self._directories.items()
            )
            for relative, path, retained, identity in directory_entries:
                _same_handle(retained, path, identity, directory=True)
                policy_handle = _open_directory_policy_handle(path)
                try:
                    _same_handle(policy_handle, path, identity, directory=True)
                    original_dacl, original_control = _capture_handle_dacl(policy_handle)
                except BaseException:
                    try:
                        policy_handle.close()
                    except Exception:
                        pass
                    raise
                policies[relative] = _DirectoryPolicy(
                    handle=policy_handle,
                    identity=identity,
                    original_dacl=original_dacl,
                    original_control=original_control,
                )
            self._namespace_policies = policies
            self._namespace_sid = _current_user_sid()
            sid = self._namespace_sid
            if sid is None:
                _fail()
            for relative, policy in self._namespace_policies.items():
                _deny_namespace_mutation(
                    policy.handle,
                    self._policy_path(relative),
                    sid,
                    policy.original_control,
                )
            self.require_namespace_sealed()
            self.audit(expected, maximum_bytes)
        except BaseException:
            if self._namespace_policies:
                try:
                    self._restore_all_namespace_policies()
                except StageWindowsFailure:
                    pass
            else:
                for policy in policies.values():
                    try:
                        policy.handle.close()
                    except Exception:
                        pass
            raise

    def require_live(self) -> None:
        if self._root_handle is None or self._root_identity is None:
            _fail()
        _same_handle(
            self._root_handle,
            self.root,
            self._root_identity,
            directory=True,
        )
        for path, handle, identity in self._ancestor_handles:
            _same_handle(handle, path, identity, directory=True)

    def ensure_directory(self, relative: PurePosixPath) -> Path:
        """Create and immediately pin each missing directory component."""

        relative = _relative(relative)
        if self._namespace_policies or self._namespace_sid is not None:
            _fail()
        self.require_live()
        parts: list[str] = []
        for component in relative.parts:
            parts.append(component)
            current_relative = PurePosixPath(*parts)
            existing = self._directories.get(current_relative)
            current_path = self.root.joinpath(*parts)
            if existing is not None:
                _same_handle(existing[0], current_path, existing[1], directory=True)
                continue
            parent_relative = PurePosixPath(*parts[:-1]) if len(parts) > 1 else None
            if parent_relative is not None and parent_relative not in self._directories:
                _fail()
            try:
                current_path.mkdir()
                handle = open_handle(current_path)
                require_expected_handle(handle, current_path, directory=True)
            except Exception:
                _fail()
            if handle.info.is_reparse_point:
                try:
                    handle.close()
                except Exception:
                    pass
                _fail()
            self._directories[current_relative] = (handle, handle.info.identity)
        return self.root.joinpath(*relative.parts)

    def pin_file(
        self,
        relative: PurePosixPath,
        *,
        expected_size: int,
        expected_sha256: str,
        maximum_bytes: int,
    ) -> None:
        """Pin one exact regular file and verify its bytes through the retained handle."""

        relative = _relative(relative)
        if self._namespace_policies or self._namespace_sid is not None:
            _fail()
        if relative in self._files:
            existing = self._files[relative]
            if existing[2:] != (expected_size, expected_sha256):
                _fail()
            return
        parent = PurePosixPath(*relative.parts[:-1])
        if not parent.parts or parent not in self._directories:
            _fail()
        self.require_live()
        path = self.root.joinpath(*relative.parts)
        try:
            handle = open_handle(path, read=True)
            require_expected_handle(handle, path, directory=False)
        except Exception:
            _fail()
        try:
            size, digest = _hash_handle(handle, maximum_bytes)
            if size != expected_size or digest != expected_sha256:
                _fail()
        except BaseException:
            try:
                handle.close()
            except Exception:
                pass
            raise
        self._files[relative] = (handle, handle.info.identity, size, digest)

    def copy_file(
        self,
        source: Path,
        relative: PurePosixPath,
        *,
        maximum_bytes: int,
    ) -> tuple[int, str]:
        """Copy through one pinned source and one CREATE_NEW retained destination handle."""

        from paperworkshell.storage._source_windows import (
            SourceFileInfo,
            is_fixed_local_drive,
            open_authenticated_source,
        )

        relative = _relative(relative)
        if self._namespace_policies or self._namespace_sid is not None:
            _fail()
        parent = PurePosixPath(*relative.parts[:-1])
        if not parent.parts or parent not in self._directories:
            _fail()
        if not source.is_absolute() or not is_fixed_local_drive(source):
            _fail()
        self.require_live()

        source_handle = None
        destination_handle: WindowsHandle | None = None
        primary: BaseException | None = None
        try:
            source_handle = open_authenticated_source(source)
            initial: SourceFileInfo = source_handle.initial_info
            if (
                _normalized(initial.final_path) != _normalized(source)
                or initial.is_directory
                or initial.is_reparse_point
                or initial.delete_pending
                or initial.link_count < 1
                or initial.file_attributes
                & (_FILE_ATTRIBUTE_SPARSE_FILE | _UNSUPPORTED_SOURCE_ATTRIBUTES)
                or not 0 <= initial.logical_size <= maximum_bytes
            ):
                _fail()

            existing = self._files.get(relative)
            destination = self.root.joinpath(*relative.parts)
            if existing is None:
                destination_handle = _create_retained_file(destination)
                self._files[relative] = (
                    destination_handle,
                    destination_handle.info.identity,
                    None,
                    None,
                )

            digest = hashlib.sha256()
            copied = 0
            while copied < initial.logical_size:
                count = min(_READ_CHUNK_BYTES, initial.logical_size - copied)
                chunk = source_handle.read_at(copied, count)
                if len(chunk) != count:
                    _fail()
                digest.update(chunk)
                if destination_handle is not None:
                    _write_all(destination_handle, chunk)
                copied += count
            current = source_handle.current_info()
            if current != initial or copied != initial.logical_size:
                _fail()
            source_digest = digest.hexdigest()

            if existing is not None:
                if existing[2:] != (initial.logical_size, source_digest):
                    _fail()
                return initial.logical_size, source_digest

            if destination_handle is None:
                _fail()
            _seal_readonly(destination_handle, destination, copied)
            size, destination_digest = _hash_handle(destination_handle, maximum_bytes)
            if size != copied or destination_digest != source_digest:
                _fail()
            destination_identity = destination_handle.info.identity
            destination_handle = _downgrade_written_file(
                destination_handle,
                destination,
                destination_identity,
                size,
                destination_digest,
                maximum_bytes,
            )
            self._files[relative] = (
                destination_handle,
                destination_identity,
                size,
                destination_digest,
            )
            return size, destination_digest
        except BaseException as error:
            primary = error
            if destination_handle is not None:
                try:
                    partial_size, partial_digest = _hash_handle(
                        destination_handle,
                        maximum_bytes,
                    )
                except BaseException:
                    pass
                else:
                    self._files[relative] = (
                        destination_handle,
                        destination_handle.info.identity,
                        partial_size,
                        partial_digest,
                    )
            raise
        finally:
            if source_handle is not None:
                try:
                    source_handle.close()
                except BaseException:
                    if primary is None:
                        _fail()

    def write_bytes(
        self,
        relative: PurePosixPath,
        payload: bytes,
        *,
        maximum_bytes: int,
    ) -> str:
        """CREATE_NEW, seal, and retain one parent-generated bounded file."""

        relative = _relative(relative)
        if self._namespace_policies or self._namespace_sid is not None:
            _fail()
        parent = PurePosixPath(*relative.parts[:-1])
        if (
            type(payload) is not bytes
            or not 0 <= len(payload) <= maximum_bytes
            or not parent.parts
            or parent not in self._directories
            or relative in self._files
        ):
            _fail()
        self.require_live()
        path = self.root.joinpath(*relative.parts)
        handle = _create_retained_file(path)
        self._files[relative] = (handle, handle.info.identity, None, None)
        try:
            offset = 0
            while offset < len(payload):
                chunk = payload[offset : offset + _READ_CHUNK_BYTES]
                _write_all(handle, chunk)
                offset += len(chunk)
            _seal_readonly(handle, path, len(payload))
            size, digest = _hash_handle(handle, maximum_bytes)
            if size != len(payload) or digest != hashlib.sha256(payload).hexdigest():
                _fail()
            identity = handle.info.identity
            handle = _downgrade_written_file(
                handle,
                path,
                identity,
                size,
                digest,
                maximum_bytes,
            )
            self._files[relative] = (handle, identity, size, digest)
        except BaseException:
            try:
                size, digest = _hash_handle(handle, maximum_bytes)
            except BaseException:
                pass
            else:
                self._files[relative] = (handle, handle.info.identity, size, digest)
            raise
        self._files[relative] = (handle, handle.info.identity, size, digest)
        return digest

    def read_bytes(self, relative: PurePosixPath, *, maximum_bytes: int) -> bytes:
        """Read one sealed retained file for parent-side manifest validation."""

        relative = _relative(relative)
        entry = self._files.get(relative)
        if entry is None or entry[2] is None or entry[3] is None:
            _fail()
        handle, identity, size_value, digest_value = entry
        if size_value is None or digest_value is None:
            _fail()
        expected_size = size_value
        expected_digest = digest_value
        if expected_size > maximum_bytes:
            _fail()
        path = self.root.joinpath(*relative.parts)
        _same_handle(handle, path, identity, directory=False)
        try:
            handle.rewind()
            chunks: list[bytes] = []
            remaining = expected_size
            while remaining:
                chunk = handle.read_exact(min(_READ_CHUNK_BYTES, remaining))
                chunks.append(chunk)
                remaining -= len(chunk)
            current = handle.require_eof()
        except Exception:
            _fail()
        payload = b"".join(chunks)
        if (
            current.identity != identity
            or len(payload) != expected_size
            or hashlib.sha256(payload).hexdigest() != expected_digest
        ):
            _fail()
        return payload

    def audit(self, expected: dict[PurePosixPath, str], maximum_bytes: int) -> None:
        """Require exact names, identities, and bytes without following unknown entries."""

        self.require_live()
        if self._namespace_policies or self._namespace_sid is not None:
            self.require_namespace_sealed()
        if set(expected) != set(self._files):
            _fail()
        expected_children: dict[PurePosixPath | None, set[str]] = {None: set()}
        for relative in self._directories:
            parent = PurePosixPath(*relative.parts[:-1]) if len(relative.parts) > 1 else None
            expected_children.setdefault(parent, set()).add(relative.name)
            expected_children.setdefault(relative, set())
        for relative in self._files:
            parent = PurePosixPath(*relative.parts[:-1])
            expected_children.setdefault(parent, set()).add(relative.name)

        roots: list[tuple[PurePosixPath | None, Path]] = [(None, self.root)]
        roots.extend(
            (relative, self.root.joinpath(*relative.parts)) for relative in self._directories
        )
        for directory_relative, path in roots:
            try:
                observed = {child.name for child in path.iterdir()}
            except OSError:
                _fail()
            if observed != expected_children.get(directory_relative, set()):
                _fail()

        for relative, (handle, identity, expected_size, expected_hash) in self._files.items():
            path = self.root.joinpath(*relative.parts)
            _same_handle(handle, path, identity, directory=False)
            if expected_size is None or expected_hash is None:
                _fail()
            size, digest = _hash_handle(handle, maximum_bytes)
            if (
                size != expected_size
                or digest != expected_hash
                or expected.get(relative) != expected_hash
            ):
                _fail()
        for relative, (handle, identity) in self._directories.items():
            _same_handle(
                handle,
                self.root.joinpath(*relative.parts),
                identity,
                directory=True,
            )

    def delete(
        self,
        expected: dict[PurePosixPath, str] | None,
        maximum_bytes: int,
    ) -> None:
        """Delete only the exact audited retained objects; unknown entries fail closed."""

        if expected is not None:
            self.audit(expected, maximum_bytes)
        else:
            self._audit_partial(maximum_bytes)
        try:
            for relative in sorted(self._files, key=lambda item: item.as_posix()):
                retained, identity, _size, _digest = self._files[relative]
                path = self.root.joinpath(*relative.parts)
                _same_handle(retained, path, identity, directory=False)
                retained.close()
                deletion_handle = open_handle(path, delete=True)
                try:
                    _same_handle(deletion_handle, path, identity, directory=False)
                    _mark_delete(deletion_handle)
                finally:
                    deletion_handle.close()
                del self._files[relative]
            for relative in sorted(
                tuple(self._directories),
                key=lambda item: len(item.parts),
                reverse=True,
            ):
                retained, identity = self._directories[relative]
                path = self.root.joinpath(*relative.parts)
                _same_handle(retained, path, identity, directory=True)
                policy = self._namespace_policies.get(relative)
                if self._namespace_sid is not None:
                    if policy is None:
                        _fail()
                    _require_policy_sealed(policy, path, self._namespace_sid)
                    _restore_policy(policy, path)
                retained.close()
                deletion_handle = open_handle(path, delete=True)
                try:
                    _same_handle(deletion_handle, path, identity, directory=True)
                    _mark_delete(deletion_handle)
                finally:
                    deletion_handle.close()
                if policy is not None:
                    policy.handle.close()
                    del self._namespace_policies[relative]
                del self._directories[relative]
            if self._root_handle is None or self._root_identity is None:
                _fail()
            root_identity = self._root_identity
            _same_handle(self._root_handle, self.root, root_identity, directory=True)
            root_policy = self._namespace_policies.get(None)
            if self._namespace_sid is not None:
                if root_policy is None:
                    _fail()
                _require_policy_sealed(root_policy, self.root, self._namespace_sid)
                _restore_policy(root_policy, self.root)
            self._root_handle.close()
            deletion_handle = open_handle(self.root, delete=True)
            try:
                _same_handle(deletion_handle, self.root, root_identity, directory=True)
                _mark_delete(deletion_handle)
            finally:
                deletion_handle.close()
            if root_policy is not None:
                root_policy.handle.close()
                del self._namespace_policies[None]
            if self._namespace_policies:
                _fail()
            self._namespace_sid = None
            self._root_handle = None
            self._root_identity = None
            for _path, handle, _identity in reversed(self._ancestor_handles):
                handle.close()
            self._ancestor_handles.clear()
        except StageWindowsFailure:
            raise
        except Exception:
            _fail()

    def _audit_partial(self, maximum_bytes: int) -> None:
        """Audit exact retained identities/names before cleaning an incomplete stage."""

        self.require_live()
        if self._namespace_policies or self._namespace_sid is not None:
            self.require_namespace_sealed()
        expected_children: dict[PurePosixPath | None, set[str]] = {None: set()}
        for relative in self._directories:
            parent = PurePosixPath(*relative.parts[:-1]) if len(relative.parts) > 1 else None
            expected_children.setdefault(parent, set()).add(relative.name)
            expected_children.setdefault(relative, set())
        for relative in self._files:
            parent = PurePosixPath(*relative.parts[:-1])
            expected_children.setdefault(parent, set()).add(relative.name)
        roots: list[tuple[PurePosixPath | None, Path]] = [(None, self.root)]
        roots.extend(
            (relative, self.root.joinpath(*relative.parts)) for relative in self._directories
        )
        for directory_relative, path in roots:
            try:
                observed = {child.name for child in path.iterdir()}
            except OSError:
                _fail()
            if observed != expected_children.get(directory_relative, set()):
                _fail()
        for relative, (handle, identity, expected_size, expected_hash) in self._files.items():
            _same_handle(
                handle,
                self.root.joinpath(*relative.parts),
                identity,
                directory=False,
            )
            if expected_size is not None and expected_hash is not None:
                if _hash_handle(handle, maximum_bytes) != (expected_size, expected_hash):
                    _fail()
        for relative, (handle, identity) in self._directories.items():
            _same_handle(
                handle,
                self.root.joinpath(*relative.parts),
                identity,
                directory=True,
            )

    def close(self) -> None:
        """Release retained authority without deleting unverified objects."""

        if self._namespace_policies or self._namespace_sid is not None:
            self._restore_all_namespace_policies()
        handles: list[WindowsHandle] = [entry[0] for entry in self._files.values()]
        handles.extend(entry[0] for entry in self._directories.values())
        if self._root_handle is not None:
            handles.append(self._root_handle)
        handles.extend(entry[1] for entry in reversed(self._ancestor_handles))
        self._files.clear()
        self._directories.clear()
        self._root_handle = None
        self._root_identity = None
        self._ancestor_handles.clear()
        failed = False
        for handle in handles:
            try:
                handle.close()
            except Exception:
                failed = True
        if failed:
            _fail()

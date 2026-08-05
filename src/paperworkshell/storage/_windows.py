"""Narrow Win32 filesystem primitives for authenticated case storage."""

from __future__ import annotations

import ctypes
import os
import re
from contextlib import AbstractContextManager
from ctypes import wintypes
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Never, Self
from uuid import UUID

from paperworkshell.storage.errors import WorkspaceErrorCode, reject

_INVALID_HANDLE_VALUE: Final = ctypes.c_void_p(-1).value

_READ_CONTROL: Final = 0x0002_0000
_DELETE: Final = 0x0001_0000
_GENERIC_READ: Final = 0x8000_0000
_GENERIC_WRITE: Final = 0x4000_0000
_SYNCHRONIZE: Final = 0x0010_0000
_FILE_READ_ATTRIBUTES: Final = 0x0000_0080
_FILE_SHARE_READ: Final = 0x0000_0001
_FILE_SHARE_WRITE: Final = 0x0000_0002
_FILE_SHARE_DELETE: Final = 0x0000_0004
_CREATE_NEW: Final = 1
_OPEN_EXISTING: Final = 3
_FILE_FLAG_OPEN_REPARSE_POINT: Final = 0x0020_0000
_FILE_FLAG_BACKUP_SEMANTICS: Final = 0x0200_0000
_FILE_FLAG_SEQUENTIAL_SCAN: Final = 0x0800_0000
_FILE_ATTRIBUTE_NORMAL: Final = 0x0000_0080
_FILE_ATTRIBUTE_DIRECTORY: Final = 0x0000_0010
_FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x0000_0400
_FILE_ATTRIBUTE_SPARSE_FILE: Final = 0x0000_0200

_FILE_STANDARD_INFO_CLASS: Final = 1
_FILE_RENAME_INFO_CLASS: Final = 3
_FILE_RENAME_INFO_EX_CLASS: Final = 22
_FILE_ATTRIBUTE_TAG_INFO_CLASS: Final = 9
_FILE_ID_INFO_CLASS: Final = 18
_FILE_DISPOSITION_INFO_CLASS: Final = 4
_FILE_RENAME_FLAG_REPLACE_IF_EXISTS: Final = 0x0000_0001
_FILE_RENAME_FLAG_POSIX_SEMANTICS: Final = 0x0000_0002

_SE_FILE_OBJECT: Final = 1
_OWNER_SECURITY_INFORMATION: Final = 0x0000_0001
_DACL_SECURITY_INFORMATION: Final = 0x0000_0004
_SE_DACL_PROTECTED: Final = 0x1000
_ACCESS_ALLOWED_ACE_TYPE: Final = 0x00
_OBJECT_INHERIT_ACE: Final = 0x01
_CONTAINER_INHERIT_ACE: Final = 0x02
_FILE_ALL_ACCESS: Final = 0x001F_01FF
_ACL_SIZE_INFORMATION_CLASS: Final = 2

_TOKEN_QUERY: Final = 0x0008
_TOKEN_USER_CLASS: Final = 1
_TOKEN_OWNER_CLASS: Final = 4
_ERROR_INSUFFICIENT_BUFFER: Final = 122
_ERROR_FILE_EXISTS: Final = 80
_ERROR_ALREADY_EXISTS: Final = 183

_DRIVE_FIXED: Final = 3
_FILE_PERSISTENT_ACLS: Final = 0x0000_0008
_ERROR_HANDLE_EOF: Final = 38
_FILE_BEGIN: Final = 0

MAXIMUM_STREAM_CHUNK_BYTES: Final = 1024 * 1024
_MAXIMUM_SAFE_FILE_BYTES: Final = 9_007_199_254_740_991
_PENDING_SOURCE_NAME: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.tmp\Z"
)
_ORIGINAL_STORAGE_NAME: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.bin\Z"
)
_PENDING_THUMBNAIL_NAME: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.tmp\Z"
)
_THUMBNAIL_CACHE_NAME: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
    r"\.[0-9a-f]{64}\.pwsthumb\Z"
)

_EXPECTED_PRIVATE_TRUSTEES: Final = frozenset(
    {
        "S-1-5-18",  # Local System
        "S-1-5-32-544",  # Builtin Administrators
        "S-1-3-4",  # Owner Rights
    }
)


class _FileId128(ctypes.Structure):
    _fields_ = [("identifier", wintypes.BYTE * 16)]


class _FileIdInfo(ctypes.Structure):
    _fields_ = [
        ("volume_serial_number", ctypes.c_ulonglong),
        ("file_id", _FileId128),
    ]


class _FileAttributeTagInfo(ctypes.Structure):
    _fields_ = [
        ("file_attributes", wintypes.DWORD),
        ("reparse_tag", wintypes.DWORD),
    ]


class _FileStandardInfo(ctypes.Structure):
    _fields_ = [
        ("allocation_size", ctypes.c_longlong),
        ("end_of_file", ctypes.c_longlong),
        ("number_of_links", wintypes.DWORD),
        ("delete_pending", wintypes.BOOLEAN),
        ("directory", wintypes.BOOLEAN),
    ]


class _FileDispositionInfo(ctypes.Structure):
    _fields_ = [("delete_file", wintypes.BOOLEAN)]


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


class _AccessAllowedAce(ctypes.Structure):
    _fields_ = [
        ("header", _AceHeader),
        ("mask", wintypes.DWORD),
        ("sid_start", wintypes.DWORD),
    ]


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [
        ("sid", ctypes.c_void_p),
        ("attributes", wintypes.DWORD),
    ]


class _TokenUser(ctypes.Structure):
    _fields_ = [("user", _SidAndAttributes)]


class _TokenOwner(ctypes.Structure):
    _fields_ = [("owner", ctypes.c_void_p)]


class _Guid(ctypes.Structure):
    _fields_ = [
        ("data1", wintypes.DWORD),
        ("data2", wintypes.WORD),
        ("data3", wintypes.WORD),
        ("data4", wintypes.BYTE * 8),
    ]

    @classmethod
    def from_uuid(cls, value: UUID) -> Self:
        raw = value.bytes_le
        return cls(
            int.from_bytes(raw[0:4], "little"),
            int.from_bytes(raw[4:6], "little"),
            int.from_bytes(raw[6:8], "little"),
            (wintypes.BYTE * 8).from_buffer_copy(raw[8:]),
        )


class _Win32FindStreamData(ctypes.Structure):
    _fields_ = [
        ("stream_size", ctypes.c_longlong),
        ("stream_name", wintypes.WCHAR * (260 + 36)),
    ]


@dataclass(frozen=True, slots=True)
class FileIdentity:
    """Stable identity obtained from an already-open Windows handle."""

    volume_serial_number: int
    file_id: bytes


@dataclass(frozen=True, slots=True)
class OpenedFileInfo:
    """Security-relevant metadata from an already-open Windows handle."""

    identity: FileIdentity
    final_path: Path
    is_directory: bool
    is_reparse_point: bool
    is_sparse: bool
    link_count: int
    logical_size: int


class ManifestReplaceState(StrEnum):
    """Identity-resolved outcome of one authoritative manifest replacement."""

    COMMITTED = "committed"
    NOT_COMMITTED = "not_committed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ManifestReplaceResult:
    """Sanitized commit state; callers preserve source bytes whenever state is unknown."""

    state: ManifestReplaceState
    delayed_exception: BaseException | None = field(
        default=None,
        repr=False,
        compare=False,
    )


class CachePublishState(StrEnum):
    """Identity-resolved outcome of one no-replace thumbnail publication."""

    PUBLISHED = "published"
    DESTINATION_EXISTS = "destination_exists"
    NOT_PUBLISHED = "not_published"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CachePublishResult:
    """Sanitized cache publication state with a delayed interruption kept private."""

    state: CachePublishState
    delayed_exception: BaseException | None = field(
        default=None,
        repr=False,
        compare=False,
    )


def _libraries() -> tuple[Any, Any, Any]:
    if os.name != "nt":
        reject(
            WorkspaceErrorCode.LOCATION_UNSUPPORTED,
            "platform",
            "Private case storage requires the supported Windows filesystem adapter.",
        )
    return (
        ctypes.WinDLL("kernel32", use_last_error=True),
        ctypes.WinDLL("advapi32", use_last_error=True),
        ctypes.WinDLL("shell32", use_last_error=True),
    )


def _raise_api_failure(context: str, *, permissions: bool = False) -> Never:
    code = (
        WorkspaceErrorCode.PERMISSIONS_UNSUPPORTED
        if permissions
        else WorkspaceErrorCode.LOCATION_UNSUPPORTED
    )
    reject(
        code,
        context,
        "Windows could not verify the required private local storage capability.",
    )


def _co_task_mem_free(value: wintypes.LPWSTR) -> None:
    """Release one COM task-allocation result, including a null result."""

    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    co_task_mem_free = ole32.CoTaskMemFree
    co_task_mem_free.argtypes = (ctypes.c_void_p,)
    co_task_mem_free.restype = None
    co_task_mem_free(ctypes.cast(value, ctypes.c_void_p))


def _strip_extended_prefix(value: str) -> str:
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[8:]
    if value.startswith("\\\\?\\"):
        return value[4:]
    return value


def _normalized_path(value: Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(value)))


class WindowsHandle(AbstractContextManager["WindowsHandle"]):
    """An authenticated handle that prevents name replacement while held."""

    __slots__ = ("_handle", "_info")

    def __init__(self, handle: int, info: OpenedFileInfo) -> None:
        self._handle = handle
        self._info = info

    @property
    def value(self) -> int:
        return self._handle

    @property
    def info(self) -> OpenedFileInfo:
        return self._info

    def refresh_info(self) -> OpenedFileInfo:
        """Refresh security facts from this exact handle and retain the new snapshot."""

        if self._handle == 0:
            reject(
                WorkspaceErrorCode.IDENTITY_CHANGED,
                "filesystem.refresh",
                "The authenticated storage object is no longer available.",
            )
        self._info = _query_opened_info(self._handle)
        return self._info

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._handle == 0:
            return
        kernel32, _, _ = _libraries()
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        handle = self._handle
        self._handle = 0
        if not close_handle(wintypes.HANDLE(handle)):
            _raise_api_failure("handle.close")

    def mark_for_deletion(self) -> None:
        """Mark this exact opened object for deletion when its handle closes."""

        if self._handle == 0:
            reject(
                WorkspaceErrorCode.IDENTITY_CHANGED,
                "cleanup.handle",
                "The cleanup object is no longer available.",
            )
        kernel32, _, _ = _libraries()
        set_information = kernel32.SetFileInformationByHandle
        set_information.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        set_information.restype = wintypes.BOOL
        disposition = _FileDispositionInfo(wintypes.BOOLEAN(1))
        if not set_information(
            wintypes.HANDLE(self._handle),
            _FILE_DISPOSITION_INFO_CLASS,
            ctypes.byref(disposition),
            ctypes.sizeof(disposition),
        ):
            reject(
                WorkspaceErrorCode.CLEANUP_FAILED,
                "cleanup.delete",
                "PaperWorkShell could not remove a verified disposable item.",
            )

    def rewind(self) -> None:
        """Move one retained regular-file handle to byte zero."""

        if self._handle == 0 or self._info.is_directory:
            reject(
                WorkspaceErrorCode.IDENTITY_CHANGED,
                "filesystem.rewind",
                "The authenticated file is no longer available.",
            )
        kernel32, _, _ = _libraries()
        set_pointer = kernel32.SetFilePointerEx
        set_pointer.argtypes = (
            wintypes.HANDLE,
            ctypes.c_longlong,
            ctypes.POINTER(ctypes.c_longlong),
            wintypes.DWORD,
        )
        set_pointer.restype = wintypes.BOOL
        if not set_pointer(
            wintypes.HANDLE(self._handle),
            ctypes.c_longlong(0),
            None,
            _FILE_BEGIN,
        ):
            reject(
                WorkspaceErrorCode.IDENTITY_CHANGED,
                "filesystem.rewind",
                "PaperWorkShell could not read the authenticated file safely.",
            )

    def read_exact(self, count: int) -> bytes:
        """Read one exact, bounded sequential chunk from this retained handle."""

        if type(count) is not int or not 0 <= count <= MAXIMUM_STREAM_CHUNK_BYTES:
            reject(
                WorkspaceErrorCode.SCAN_LIMIT_EXCEEDED,
                "filesystem.read_chunk",
                "The authenticated file-read request exceeds its fixed chunk limit.",
            )
        if self._handle == 0 or self._info.is_directory:
            reject(
                WorkspaceErrorCode.IDENTITY_CHANGED,
                "filesystem.read",
                "The authenticated file is no longer available.",
            )
        if count == 0:
            return b""

        kernel32, _, _ = _libraries()
        read_file = kernel32.ReadFile
        read_file.argtypes = (
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        )
        read_file.restype = wintypes.BOOL
        buffer = ctypes.create_string_buffer(count)
        completed = 0
        while completed < count:
            received = wintypes.DWORD()
            remaining = count - completed
            if not read_file(
                wintypes.HANDLE(self._handle),
                ctypes.byref(buffer, completed),
                remaining,
                ctypes.byref(received),
                None,
            ):
                reject(
                    WorkspaceErrorCode.IDENTITY_CHANGED,
                    "filesystem.read",
                    "The authenticated file changed while it was being read.",
                )
            amount = int(received.value)
            if amount <= 0 or amount > remaining:
                reject(
                    WorkspaceErrorCode.IDENTITY_CHANGED,
                    "filesystem.read",
                    "The authenticated file changed while it was being read.",
                )
            completed += amount
        return buffer.raw

    def require_eof(self) -> OpenedFileInfo:
        """Require EOF and unchanged identity/size after a sequential read."""

        if self._handle == 0 or self._info.is_directory:
            reject(
                WorkspaceErrorCode.IDENTITY_CHANGED,
                "filesystem.read",
                "The authenticated file is no longer available.",
            )
        expected = self._info
        kernel32, _, _ = _libraries()
        read_file = kernel32.ReadFile
        read_file.argtypes = (
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        )
        read_file.restype = wintypes.BOOL
        probe = ctypes.create_string_buffer(1)
        received = wintypes.DWORD()
        if (
            not read_file(
                wintypes.HANDLE(self._handle),
                probe,
                1,
                ctypes.byref(received),
                None,
            )
            or received.value != 0
        ):
            reject(
                WorkspaceErrorCode.IDENTITY_CHANGED,
                "filesystem.read",
                "The authenticated file changed while it was being read.",
            )
        current = _query_opened_info(self._handle)
        if (
            current.identity != expected.identity
            or current.logical_size != expected.logical_size
            or current.is_directory
            or current.is_reparse_point
            or current.is_sparse != expected.is_sparse
            or current.link_count != expected.link_count
        ):
            reject(
                WorkspaceErrorCode.IDENTITY_CHANGED,
                "filesystem.read",
                "The authenticated file changed while it was being read.",
            )
        self._info = current
        return current

    def read_bytes(self, *, maximum_bytes: int) -> bytes:
        """Read one already-authenticated regular file through its retained handle."""

        if type(maximum_bytes) is not int or maximum_bytes < 1:
            reject(
                WorkspaceErrorCode.SCAN_LIMIT_EXCEEDED,
                "filesystem.read_limit",
                "A positive bounded file-read limit is required.",
            )
        if self._handle == 0 or self._info.is_directory:
            reject(
                WorkspaceErrorCode.IDENTITY_CHANGED,
                "filesystem.read",
                "The authenticated file is no longer available.",
            )
        expected_size = self._info.logical_size
        if expected_size > maximum_bytes:
            reject(
                WorkspaceErrorCode.SCAN_LIMIT_EXCEEDED,
                "filesystem.read_limit",
                "The authenticated file exceeds its configured read limit.",
            )

        kernel32, _, _ = _libraries()
        read_file = kernel32.ReadFile
        read_file.argtypes = (
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        )
        read_file.restype = wintypes.BOOL
        chunks: list[bytes] = []
        remaining = expected_size
        while remaining:
            requested = min(remaining, 1024 * 1024)
            buffer = ctypes.create_string_buffer(requested)
            received = wintypes.DWORD()
            if not read_file(
                wintypes.HANDLE(self._handle),
                buffer,
                requested,
                ctypes.byref(received),
                None,
            ):
                reject(
                    WorkspaceErrorCode.IDENTITY_CHANGED,
                    "filesystem.read",
                    "The authenticated file changed while it was being read.",
                )
            count = int(received.value)
            if count <= 0 or count > requested:
                reject(
                    WorkspaceErrorCode.IDENTITY_CHANGED,
                    "filesystem.read",
                    "The authenticated file changed while it was being read.",
                )
            chunks.append(buffer.raw[:count])
            remaining -= count

        probe = ctypes.create_string_buffer(1)
        received = wintypes.DWORD()
        if (
            not read_file(
                wintypes.HANDLE(self._handle),
                probe,
                1,
                ctypes.byref(received),
                None,
            )
            or received.value != 0
        ):
            reject(
                WorkspaceErrorCode.IDENTITY_CHANGED,
                "filesystem.read",
                "The authenticated file changed while it was being read.",
            )
        current = _query_opened_info(self._handle)
        if current.identity != self._info.identity or current.logical_size != expected_size:
            reject(
                WorkspaceErrorCode.IDENTITY_CHANGED,
                "filesystem.read",
                "The authenticated file changed while it was being read.",
            )
        return b"".join(chunks)


def open_handle(
    path: Path,
    *,
    delete: bool = False,
    read: bool = False,
    share_delete: bool = False,
) -> WindowsHandle:
    """Open without following the final reparse point; deletion sharing is explicit."""

    if any(type(value) is not bool for value in (delete, read, share_delete)):
        reject(
            WorkspaceErrorCode.PATH_INVALID,
            "filesystem.open_mode",
            "The authenticated storage open mode is invalid.",
        )

    kernel32, _, _ = _libraries()
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
    access = _READ_CONTROL | _FILE_READ_ATTRIBUTES
    if delete:
        access |= _DELETE
    if read:
        access |= _GENERIC_READ
    share_mode = _FILE_SHARE_READ if read else _FILE_SHARE_READ | _FILE_SHARE_WRITE
    if share_delete:
        share_mode |= _FILE_SHARE_DELETE
    raw_handle = create_file(
        str(path),
        access,
        share_mode,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    handle_value = int(raw_handle) if raw_handle else 0
    if not handle_value or handle_value == _INVALID_HANDLE_VALUE:
        reject(
            WorkspaceErrorCode.IDENTITY_CHANGED,
            "filesystem.open",
            "PaperWorkShell could not authenticate the requested storage object.",
        )
    try:
        info = _query_opened_info(handle_value)
    except BaseException:
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        close_handle(wintypes.HANDLE(handle_value))
        raise
    return WindowsHandle(handle_value, info)


def reopen_read_only_file(handle: WindowsHandle) -> WindowsHandle:
    """Create an independent, non-inheritable read handle to one authenticated file.

    ``ReOpenFile`` binds the new file object to the already authenticated kernel
    object and gives it an independent file pointer.  No path lookup is performed.
    The returned handle deliberately omits write and delete rights and shares
    deletion so the owning pending-source lease can still clean up after a borrow.
    """

    if type(handle) is not WindowsHandle or handle.value == 0:
        reject(
            WorkspaceErrorCode.IDENTITY_CHANGED,
            "filesystem.reopen",
            "The authenticated storage object is no longer available.",
        )
    original = handle.refresh_info()
    if (
        original.is_directory
        or original.is_reparse_point
        or original.is_sparse
        or original.link_count != 1
    ):
        reject(
            WorkspaceErrorCode.ALIAS_DETECTED,
            "filesystem.reopen",
            "The authenticated storage object is not a supported regular file.",
        )

    kernel32, _, _ = _libraries()
    reopen_file = kernel32.ReOpenFile
    reopen_file.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    reopen_file.restype = wintypes.HANDLE
    raw_handle = reopen_file(
        wintypes.HANDLE(handle.value),
        _GENERIC_READ | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE,
        # Sharing write/delete does not grant either right to this handle.  It
        # keeps the existing pending handle's retained write/delete access
        # compatible while the borrower itself remains read-only.
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        _FILE_FLAG_SEQUENTIAL_SCAN,
    )
    handle_value = int(raw_handle) if raw_handle else 0
    if not handle_value or handle_value == _INVALID_HANDLE_VALUE:
        reject(
            WorkspaceErrorCode.IDENTITY_CHANGED,
            "filesystem.reopen",
            "PaperWorkShell could not borrow the authenticated storage object safely.",
        )

    reopened: WindowsHandle | None = None
    try:
        reopened = WindowsHandle(handle_value, _query_opened_info(handle_value))
        current = reopened.info
        if (
            current.identity != original.identity
            or current.logical_size != original.logical_size
            or current.is_directory
            or current.is_reparse_point
            or current.is_sparse
            or current.link_count != 1
        ):
            reject(
                WorkspaceErrorCode.IDENTITY_CHANGED,
                "filesystem.reopen_identity",
                "The authenticated storage object changed before it could be borrowed.",
            )
        require_no_alternate_streams(current.final_path)
        return reopened
    except BaseException:
        if reopened is not None:
            try:
                reopened.close()
            except BaseException:
                pass
        else:
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = (wintypes.HANDLE,)
            close_handle.restype = wintypes.BOOL
            close_handle(wintypes.HANDLE(handle_value))
        raise


class WindowsPendingSourceFile(AbstractContextManager["WindowsPendingSourceFile"]):
    """One random CREATE_NEW source stage with sequential, bounded write/read access."""

    __slots__ = (
        "_bytes_written",
        "_handle",
        "_initial_identity",
        "_initial_path",
        "_sealed",
    )

    def __init__(self, handle: WindowsHandle) -> None:
        self._handle = handle
        self._initial_identity = handle.info.identity
        self._initial_path = handle.info.final_path
        self._bytes_written = 0
        self._sealed = False

    @property
    def handle(self) -> WindowsHandle:
        """Return the retained exact handle for authenticated publication."""

        return self._handle

    @property
    def info(self) -> OpenedFileInfo:
        return self._handle.info

    @property
    def bytes_written(self) -> int:
        return self._bytes_written

    @property
    def is_sealed(self) -> bool:
        return self._sealed

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def write_chunk(self, data: bytes) -> None:
        """Append one exact chunk; callers cannot seek or rewrite staged source bytes."""

        if (
            type(data) is not bytes
            or not data
            or len(data) > MAXIMUM_STREAM_CHUNK_BYTES
            or self._sealed
            or self._handle.value == 0
            or self._bytes_written > _MAXIMUM_SAFE_FILE_BYTES - len(data)
        ):
            reject(
                WorkspaceErrorCode.CREATE_FAILED,
                "source_stage.write",
                "PaperWorkShell could not write the pending source safely.",
            )
        kernel32, _, _ = _libraries()
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
                wintypes.HANDLE(self._handle.value),
                ctypes.byref(buffer, completed),
                remaining,
                ctypes.byref(written),
                None,
            ):
                reject(
                    WorkspaceErrorCode.CREATE_FAILED,
                    "source_stage.write",
                    "PaperWorkShell could not write the pending source safely.",
                )
            amount = int(written.value)
            if amount <= 0 or amount > remaining:
                reject(
                    WorkspaceErrorCode.CREATE_FAILED,
                    "source_stage.write",
                    "PaperWorkShell could not write the pending source safely.",
                )
            completed += amount
        self._bytes_written += len(data)

    def seal(self, *, expected_size: int) -> OpenedFileInfo:
        """Flush and authenticate the complete stage before independent readback."""

        if (
            type(expected_size) is not int
            or not 1 <= expected_size <= _MAXIMUM_SAFE_FILE_BYTES
            or expected_size != self._bytes_written
            or self._sealed
            or self._handle.value == 0
        ):
            reject(
                WorkspaceErrorCode.CREATE_FAILED,
                "source_stage.size",
                "The pending source does not have its expected complete size.",
            )
        kernel32, _, _ = _libraries()
        flush = kernel32.FlushFileBuffers
        flush.argtypes = (wintypes.HANDLE,)
        flush.restype = wintypes.BOOL
        if not flush(wintypes.HANDLE(self._handle.value)):
            reject(
                WorkspaceErrorCode.CREATE_FAILED,
                "source_stage.flush",
                "PaperWorkShell could not commit the pending source bytes.",
            )
        current = self._handle.refresh_info()
        if (
            current.identity != self._initial_identity
            or _normalized_path(current.final_path) != _normalized_path(self._initial_path)
            or current.is_directory
            or current.is_reparse_point
            or current.is_sparse
            or current.link_count != 1
            or current.logical_size != expected_size
        ):
            reject(
                WorkspaceErrorCode.IDENTITY_CHANGED,
                "source_stage.identity",
                "The pending source changed before it could be sealed.",
            )
        require_no_alternate_streams(current.final_path)
        self._sealed = True
        return current

    def rewind(self) -> None:
        if not self._sealed:
            reject(
                WorkspaceErrorCode.CREATE_FAILED,
                "source_stage.read_state",
                "The pending source is not ready for verification.",
            )
        self._handle.rewind()

    def read_exact(self, count: int) -> bytes:
        if not self._sealed:
            reject(
                WorkspaceErrorCode.CREATE_FAILED,
                "source_stage.read_state",
                "The pending source is not ready for verification.",
            )
        return self._handle.read_exact(count)

    def require_eof(self) -> OpenedFileInfo:
        if not self._sealed:
            reject(
                WorkspaceErrorCode.CREATE_FAILED,
                "source_stage.read_state",
                "The pending source is not ready for verification.",
            )
        current = self._handle.require_eof()
        if (
            current.identity != self._initial_identity
            or current.logical_size != self._bytes_written
            or current.is_sparse
            or current.link_count != 1
        ):
            reject(
                WorkspaceErrorCode.IDENTITY_CHANGED,
                "source_stage.readback",
                "The pending source changed during verification.",
            )
        require_no_alternate_streams(current.final_path)
        return current

    def mark_for_deletion(self) -> None:
        self._handle.mark_for_deletion()

    def close(self) -> None:
        self._handle.close()


class WindowsPendingThumbnailFile(AbstractContextManager["WindowsPendingThumbnailFile"]):
    """One random cache stage writable only by the trusted storage adapter."""

    __slots__ = (
        "_bytes_written",
        "_handle",
        "_initial_identity",
        "_initial_path",
        "_maximum_bytes",
        "_sealed",
    )

    def __init__(self, handle: WindowsHandle, *, maximum_bytes: int) -> None:
        self._handle = handle
        self._initial_identity = handle.info.identity
        self._initial_path = handle.info.final_path
        self._maximum_bytes = maximum_bytes
        self._bytes_written = 0
        self._sealed = False

    @property
    def handle(self) -> WindowsHandle:
        """Return the exact handle only to trusted storage publication helpers."""

        return self._handle

    @property
    def info(self) -> OpenedFileInfo:
        return self._handle.info

    @property
    def is_sealed(self) -> bool:
        return self._sealed

    @property
    def maximum_bytes(self) -> int:
        return self._maximum_bytes

    def write_artifact(self, payload: bytes) -> None:
        """Write one complete bounded artifact through the retained trusted handle."""

        if type(payload) is not bytes or not payload or len(payload) > self._maximum_bytes:
            reject(
                WorkspaceErrorCode.QUOTA_EXCEEDED,
                "thumbnail_stage.write_limit",
                "The thumbnail cache artifact exceeds its reserved storage bound.",
            )
        if self._bytes_written != 0 or self._sealed or self._handle.value == 0:
            reject(
                WorkspaceErrorCode.CREATE_FAILED,
                "thumbnail_stage.write_state",
                "The thumbnail cache stage is not writable.",
            )
        kernel32, _, _ = _libraries()
        write_file = kernel32.WriteFile
        write_file.argtypes = (
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        )
        write_file.restype = wintypes.BOOL
        buffer = ctypes.create_string_buffer(payload)
        completed = 0
        while completed < len(payload):
            remaining = len(payload) - completed
            chunk_size = min(remaining, MAXIMUM_STREAM_CHUNK_BYTES)
            if self._bytes_written > self._maximum_bytes - chunk_size:
                reject(
                    WorkspaceErrorCode.QUOTA_EXCEEDED,
                    "thumbnail_stage.write_limit",
                    "The thumbnail cache artifact exceeds its reserved storage bound.",
                )
            written = wintypes.DWORD()
            if not write_file(
                wintypes.HANDLE(self._handle.value),
                ctypes.byref(buffer, completed),
                chunk_size,
                ctypes.byref(written),
                None,
            ):
                reject(
                    WorkspaceErrorCode.CREATE_FAILED,
                    "thumbnail_stage.write",
                    "PaperWorkShell could not write the thumbnail cache artifact safely.",
                )
            amount = int(written.value)
            if amount <= 0 or amount > chunk_size:
                reject(
                    WorkspaceErrorCode.CREATE_FAILED,
                    "thumbnail_stage.write",
                    "PaperWorkShell could not write the thumbnail cache artifact safely.",
                )
            completed += amount
            self._bytes_written += amount

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def seal(self) -> OpenedFileInfo:
        """Flush and authenticate trusted staged bytes before bounded readback."""

        if self._sealed or self._handle.value == 0 or self._bytes_written == 0:
            reject(
                WorkspaceErrorCode.CREATE_FAILED,
                "thumbnail_stage.state",
                "The thumbnail cache stage is not writable.",
            )
        kernel32, _, _ = _libraries()
        flush = kernel32.FlushFileBuffers
        flush.argtypes = (wintypes.HANDLE,)
        flush.restype = wintypes.BOOL
        if not flush(wintypes.HANDLE(self._handle.value)):
            reject(
                WorkspaceErrorCode.CREATE_FAILED,
                "thumbnail_stage.flush",
                "PaperWorkShell could not commit the thumbnail cache stage.",
            )
        current = self._handle.refresh_info()
        if (
            current.identity != self._initial_identity
            or _normalized_path(current.final_path) != _normalized_path(self._initial_path)
            or current.is_directory
            or current.is_reparse_point
            or current.is_sparse
            or current.link_count != 1
            or current.logical_size != self._bytes_written
            or not 1 <= current.logical_size <= self._maximum_bytes
        ):
            reject(
                WorkspaceErrorCode.IDENTITY_CHANGED,
                "thumbnail_stage.identity",
                "The thumbnail cache stage changed before validation.",
            )
        require_no_alternate_streams(current.final_path)
        self._sealed = True
        return current

    def read_bytes(self) -> bytes:
        """Return the exact sealed stage through a bounded retained-handle read."""

        if not self._sealed:
            reject(
                WorkspaceErrorCode.CREATE_FAILED,
                "thumbnail_stage.read_state",
                "The thumbnail cache stage is not ready for validation.",
            )
        self._handle.rewind()
        payload = self._handle.read_bytes(maximum_bytes=self._maximum_bytes)
        current = self._handle.info
        if (
            current.identity != self._initial_identity
            or current.is_sparse
            or current.link_count != 1
            or len(payload) != self._bytes_written
            or current.logical_size != self._bytes_written
        ):
            reject(
                WorkspaceErrorCode.IDENTITY_CHANGED,
                "thumbnail_stage.readback",
                "The thumbnail cache stage changed during validation.",
            )
        require_no_alternate_streams(current.final_path)
        return payload

    def mark_for_deletion(self) -> None:
        self._handle.mark_for_deletion()

    def close(self) -> None:
        self._handle.close()


def create_pending_source_file(
    path: Path,
    *,
    temp_root: WindowsHandle,
) -> WindowsPendingSourceFile:
    """Create one opaque pending source beneath a case temp directory."""

    root_before = temp_root.info
    root_current = temp_root.refresh_info()
    if (
        root_current.identity != root_before.identity
        or not root_current.is_directory
        or root_current.is_reparse_point
        or root_current.final_path.name.casefold() != "temp"
        or _normalized_path(path.parent) != _normalized_path(root_current.final_path)
    ):
        reject(
            WorkspaceErrorCode.IDENTITY_CHANGED,
            "source_stage.root",
            "The authenticated temporary directory changed before source staging.",
        )
    verify_private_acl(temp_root)
    require_no_alternate_streams(root_current.final_path)
    if _PENDING_SOURCE_NAME.fullmatch(path.name) is None:
        reject(
            WorkspaceErrorCode.NAME_INVALID,
            "source_stage.name",
            "A pending source requires a random internal staging name.",
        )
    kernel32, _, _ = _libraries()
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
    raw_handle = create_file(
        str(path),
        _GENERIC_READ | _GENERIC_WRITE | _DELETE | _READ_CONTROL | _FILE_READ_ATTRIBUTES,
        _FILE_SHARE_READ,
        None,
        _CREATE_NEW,
        _FILE_ATTRIBUTE_NORMAL,
        None,
    )
    handle_value = int(raw_handle) if raw_handle else 0
    if not handle_value or handle_value == _INVALID_HANDLE_VALUE:
        code = (
            WorkspaceErrorCode.ALREADY_EXISTS
            if ctypes.get_last_error() in {_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS}
            else WorkspaceErrorCode.CREATE_FAILED
        )
        reject(
            code,
            "source_stage.create",
            "PaperWorkShell could not create the pending source safely.",
        )

    handle: WindowsHandle | None = None
    try:
        handle = WindowsHandle(handle_value, _query_opened_info(handle_value))
        require_expected_handle(handle, path, directory=False)
        if handle.info.is_sparse or handle.info.link_count != 1:
            reject(
                WorkspaceErrorCode.ALIAS_DETECTED,
                "source_stage.create",
                "The pending source unexpectedly aliases another storage object.",
            )
        require_no_alternate_streams(path)
        return WindowsPendingSourceFile(handle)
    except BaseException:
        if handle is not None:
            try:
                handle.mark_for_deletion()
            except BaseException:
                pass
            try:
                handle.close()
            except BaseException:
                pass
        else:
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = (wintypes.HANDLE,)
            close_handle.restype = wintypes.BOOL
            close_handle(wintypes.HANDLE(handle_value))
        raise


def create_pending_thumbnail_file(
    path: Path,
    *,
    thumbnails_root: WindowsHandle,
    maximum_bytes: int,
) -> WindowsPendingThumbnailFile:
    """Create one opaque, bounded stage beneath an authenticated thumbnail directory."""

    if type(maximum_bytes) is not int or not 1 <= maximum_bytes <= _MAXIMUM_SAFE_FILE_BYTES:
        reject(
            WorkspaceErrorCode.QUOTA_INVALID,
            "thumbnail_stage.limit",
            "A valid thumbnail cache-stage limit is required.",
        )
    root_before = thumbnails_root.info
    root_current = thumbnails_root.refresh_info()
    if (
        root_current.identity != root_before.identity
        or not root_current.is_directory
        or root_current.is_reparse_point
        or root_current.final_path.name.casefold() != "thumbnails"
        or root_current.final_path.parent.name.casefold() != "cache"
        or _normalized_path(path.parent) != _normalized_path(root_current.final_path)
    ):
        reject(
            WorkspaceErrorCode.IDENTITY_CHANGED,
            "thumbnail_stage.root",
            "The authenticated thumbnail directory changed before staging.",
        )
    verify_private_acl(thumbnails_root)
    require_no_alternate_streams(root_current.final_path)
    if _PENDING_THUMBNAIL_NAME.fullmatch(path.name) is None:
        reject(
            WorkspaceErrorCode.NAME_INVALID,
            "thumbnail_stage.name",
            "A thumbnail stage requires a random internal name.",
        )

    kernel32, _, _ = _libraries()
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
    raw_handle = create_file(
        str(path),
        _GENERIC_READ | _GENERIC_WRITE | _DELETE | _READ_CONTROL | _FILE_READ_ATTRIBUTES,
        _FILE_SHARE_READ,
        None,
        _CREATE_NEW,
        _FILE_ATTRIBUTE_NORMAL,
        None,
    )
    handle_value = int(raw_handle) if raw_handle else 0
    if not handle_value or handle_value == _INVALID_HANDLE_VALUE:
        code = (
            WorkspaceErrorCode.ALREADY_EXISTS
            if ctypes.get_last_error() in {_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS}
            else WorkspaceErrorCode.CREATE_FAILED
        )
        reject(
            code,
            "thumbnail_stage.create",
            "PaperWorkShell could not create the thumbnail cache stage safely.",
        )

    handle: WindowsHandle | None = None
    try:
        handle = WindowsHandle(handle_value, _query_opened_info(handle_value))
        require_expected_handle(handle, path, directory=False)
        if handle.info.is_sparse or handle.info.link_count != 1:
            reject(
                WorkspaceErrorCode.ALIAS_DETECTED,
                "thumbnail_stage.create",
                "The thumbnail cache stage unexpectedly aliases another object.",
            )
        require_no_alternate_streams(path)
        return WindowsPendingThumbnailFile(handle, maximum_bytes=maximum_bytes)
    except BaseException:
        if handle is not None:
            try:
                handle.mark_for_deletion()
            except BaseException:
                pass
            try:
                handle.close()
            except BaseException:
                pass
        else:
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = (wintypes.HANDLE,)
            close_handle.restype = wintypes.BOOL
            close_handle(wintypes.HANDLE(handle_value))
        raise


def write_new_file(path: Path, data: bytes) -> WindowsHandle:
    """Exclusively create, fully write, flush, and retain one authenticated file."""

    if type(data) is not bytes:
        reject(
            WorkspaceErrorCode.CREATE_FAILED,
            "filesystem.write",
            "PaperWorkShell could not create the authoritative case record.",
        )
    kernel32, _, _ = _libraries()
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
    raw_handle = create_file(
        str(path),
        _GENERIC_WRITE | _DELETE | _READ_CONTROL | _FILE_READ_ATTRIBUTES,
        _FILE_SHARE_READ | _FILE_SHARE_DELETE,
        None,
        _CREATE_NEW,
        _FILE_ATTRIBUTE_NORMAL,
        None,
    )
    handle_value = int(raw_handle) if raw_handle else 0
    if not handle_value or handle_value == _INVALID_HANDLE_VALUE:
        code = (
            WorkspaceErrorCode.ALREADY_EXISTS
            if ctypes.get_last_error() in {_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS}
            else WorkspaceErrorCode.CREATE_FAILED
        )
        reject(
            code,
            "filesystem.write",
            "PaperWorkShell could not create the authoritative case record.",
        )

    handle: WindowsHandle | None = None
    try:
        info = _query_opened_info(handle_value)
        handle = WindowsHandle(handle_value, info)
        require_expected_handle(handle, path, directory=False)
        if info.link_count != 1:
            reject(
                WorkspaceErrorCode.ALIAS_DETECTED,
                "filesystem.write",
                "The new case record unexpectedly aliases another filesystem object.",
            )

        write_file = kernel32.WriteFile
        write_file.argtypes = (
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        )
        write_file.restype = wintypes.BOOL
        offset = 0
        while offset < len(data):
            chunk = data[offset : offset + 1024 * 1024]
            buffer = ctypes.create_string_buffer(chunk)
            written = wintypes.DWORD()
            if not write_file(
                wintypes.HANDLE(handle_value),
                buffer,
                len(chunk),
                ctypes.byref(written),
                None,
            ) or int(written.value) != len(chunk):
                reject(
                    WorkspaceErrorCode.CREATE_FAILED,
                    "filesystem.write",
                    "PaperWorkShell could not create the authoritative case record.",
                )
            offset += len(chunk)
        flush = kernel32.FlushFileBuffers
        flush.argtypes = (wintypes.HANDLE,)
        flush.restype = wintypes.BOOL
        if not flush(wintypes.HANDLE(handle_value)):
            reject(
                WorkspaceErrorCode.CREATE_FAILED,
                "filesystem.flush",
                "PaperWorkShell could not commit the authoritative case record.",
            )
        final_info = _query_opened_info(handle_value)
        if final_info.identity != info.identity or final_info.logical_size != len(data):
            reject(
                WorkspaceErrorCode.IDENTITY_CHANGED,
                "filesystem.write",
                "The new case record changed before it could be committed.",
            )
        handle._info = final_info
        return handle
    except BaseException:
        if handle is not None:
            try:
                handle.mark_for_deletion()
            except BaseException:
                pass
            handle.close()
        else:
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = (wintypes.HANDLE,)
            close_handle.restype = wintypes.BOOL
            close_handle(wintypes.HANDLE(handle_value))
        raise


def publish_new_file(
    source_handle: WindowsHandle,
    destination_root: WindowsHandle,
    *,
    destination: Path,
) -> None:
    """Atomically rename an exact opened file beneath an authenticated directory handle."""

    if source_handle.info.is_directory or source_handle.info.link_count != 1:
        reject(
            WorkspaceErrorCode.ALIAS_DETECTED,
            "filesystem.publish",
            "The staged case record unexpectedly aliases another filesystem object.",
        )
    if not destination_root.info.is_directory or destination_root.info.is_reparse_point:
        reject(
            WorkspaceErrorCode.IDENTITY_CHANGED,
            "filesystem.publish",
            "The case storage location changed before publication.",
        )
    destination_name = destination.name
    if (
        destination.parent != destination_root.info.final_path
        or destination_name != "manifest.json"
    ):
        reject(
            WorkspaceErrorCode.PATH_INVALID,
            "filesystem.publish",
            "The authoritative case record has an unexpected destination.",
        )
    renamed, error_number = _rename_open_file(
        source_handle,
        destination=destination,
        replace=False,
    )
    if not renamed:
        code = (
            WorkspaceErrorCode.ALREADY_EXISTS
            if error_number in {_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS}
            else WorkspaceErrorCode.CREATE_FAILED
        )
        reject(
            code,
            "filesystem.publish",
            "PaperWorkShell could not publish the authoritative case record.",
        )
    previous = source_handle.info
    published = source_handle.refresh_info()
    if (
        published.identity != previous.identity
        or published.is_directory
        or published.is_reparse_point
        or published.link_count != 1
        or _normalized_path(published.final_path) != _normalized_path(destination)
    ):
        reject(
            WorkspaceErrorCode.IDENTITY_CHANGED,
            "filesystem.publish",
            "The authoritative case record changed during publication.",
        )
    require_no_alternate_streams(destination)


def publish_new_original(
    source: WindowsPendingSourceFile,
    originals_root: WindowsHandle,
    *,
    storage_name: str,
) -> OpenedFileInfo:
    """Publish one sealed pending source once under an opaque UUIDv4 storage name."""

    if (
        not isinstance(source, WindowsPendingSourceFile)
        or not source.is_sealed
        or type(storage_name) is not str
        or _ORIGINAL_STORAGE_NAME.fullmatch(storage_name) is None
    ):
        reject(
            WorkspaceErrorCode.NAME_INVALID,
            "source_publish.name",
            "A source copy requires a valid random internal storage name.",
        )
    root_before = originals_root.info
    root_current = originals_root.refresh_info()
    if (
        root_current.identity != root_before.identity
        or not root_current.is_directory
        or root_current.is_reparse_point
        or root_current.final_path.name.casefold() != "originals"
    ):
        reject(
            WorkspaceErrorCode.IDENTITY_CHANGED,
            "source_publish.root",
            "The authenticated originals directory changed before publication.",
        )
    verify_private_acl(originals_root)
    require_no_alternate_streams(root_current.final_path)

    destination = root_current.final_path / storage_name
    staged = source.info
    renamed, error_number = _rename_open_file(
        source.handle,
        destination=destination,
        replace=False,
    )
    if not renamed:
        code = (
            WorkspaceErrorCode.ALREADY_EXISTS
            if error_number in {_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS}
            else WorkspaceErrorCode.CREATE_FAILED
        )
        reject(
            code,
            "source_publish.rename",
            "PaperWorkShell could not publish the immutable source copy.",
        )
    published = source.handle.refresh_info()
    if (
        published.identity != staged.identity
        or published.logical_size != staged.logical_size
        or published.is_directory
        or published.is_reparse_point
        or published.is_sparse
        or published.link_count != 1
        or _normalized_path(published.final_path) != _normalized_path(destination)
    ):
        reject(
            WorkspaceErrorCode.IDENTITY_CHANGED,
            "source_publish.identity",
            "The immutable source copy changed during publication.",
        )
    require_no_alternate_streams(destination)
    return published


def publish_new_thumbnail_file(
    source: WindowsPendingThumbnailFile,
    thumbnails_root: WindowsHandle,
    *,
    storage_name: str,
) -> CachePublishResult:
    """Atomically publish one validated stage without replacing an existing key."""

    if (
        not isinstance(source, WindowsPendingThumbnailFile)
        or not source.is_sealed
        or type(storage_name) is not str
        or _THUMBNAIL_CACHE_NAME.fullmatch(storage_name) is None
    ):
        reject(
            WorkspaceErrorCode.NAME_INVALID,
            "thumbnail_publish.name",
            "A thumbnail cache entry requires a valid opaque internal name.",
        )
    root_before = thumbnails_root.info
    root_current = thumbnails_root.refresh_info()
    if (
        root_current.identity != root_before.identity
        or not root_current.is_directory
        or root_current.is_reparse_point
        or root_current.final_path.name.casefold() != "thumbnails"
        or root_current.final_path.parent.name.casefold() != "cache"
    ):
        reject(
            WorkspaceErrorCode.IDENTITY_CHANGED,
            "thumbnail_publish.root",
            "The authenticated thumbnail directory changed before publication.",
        )
    verify_private_acl(thumbnails_root)
    require_no_alternate_streams(root_current.final_path)

    staged_before = source.info
    staged = source.handle.refresh_info()
    if (
        staged.identity != staged_before.identity
        or staged.is_directory
        or staged.is_reparse_point
        or staged.is_sparse
        or staged.link_count != 1
        or staged.logical_size > source.maximum_bytes
        or _normalized_path(staged.final_path.parent) != _normalized_path(root_current.final_path)
        or _PENDING_THUMBNAIL_NAME.fullmatch(staged.final_path.name) is None
    ):
        reject(
            WorkspaceErrorCode.IDENTITY_CHANGED,
            "thumbnail_publish.stage",
            "The thumbnail cache stage changed before publication.",
        )
    require_no_alternate_streams(staged.final_path)
    destination = root_current.final_path / storage_name
    initial = _resolve_cache_publish_state(
        source.handle,
        initial_path=staged.final_path,
        initial_identity=staged.identity,
        destination=destination,
    )
    if initial is not CachePublishState.NOT_PUBLISHED:
        return CachePublishResult(initial)

    delayed_exception: BaseException | None = None
    try:
        renamed, error_number = _rename_open_file(
            source.handle,
            destination=destination,
            replace=False,
        )
    except BaseException as error:
        delayed_exception = error
        renamed = False
        error_number = 0

    resolved = _resolve_cache_publish_state(
        source.handle,
        initial_path=staged.final_path,
        initial_identity=staged.identity,
        destination=destination,
    )
    if (
        resolved is CachePublishState.NOT_PUBLISHED
        and error_number in {_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS}
        and os.path.lexists(destination)
    ):
        resolved = CachePublishState.DESTINATION_EXISTS
    if resolved is CachePublishState.PUBLISHED:
        published = source.handle.refresh_info()
        if (
            published.identity != staged.identity
            or published.logical_size != staged.logical_size
            or published.is_directory
            or published.is_reparse_point
            or published.is_sparse
            or published.link_count != 1
            or _normalized_path(published.final_path) != _normalized_path(destination)
        ):
            return CachePublishResult(
                CachePublishState.UNKNOWN,
                delayed_exception=delayed_exception,
            )
        require_no_alternate_streams(destination)
        return CachePublishResult(
            CachePublishState.PUBLISHED,
            delayed_exception=delayed_exception,
        )
    if delayed_exception is not None and resolved is CachePublishState.NOT_PUBLISHED:
        raise delayed_exception
    if delayed_exception is not None:
        return CachePublishResult(resolved, delayed_exception=delayed_exception)
    if not renamed:
        return CachePublishResult(resolved)
    return CachePublishResult(CachePublishState.UNKNOWN)


def _resolve_cache_publish_state(
    source_handle: WindowsHandle,
    *,
    initial_path: Path,
    initial_identity: FileIdentity,
    destination: Path,
) -> CachePublishState:
    """Resolve a no-replace rename solely through the retained source identity."""

    try:
        current = source_handle.refresh_info()
        if (
            current.identity != initial_identity
            or current.is_directory
            or current.is_reparse_point
            or current.is_sparse
            or current.link_count != 1
        ):
            return CachePublishState.UNKNOWN
        current_path = _normalized_path(current.final_path)
        if current_path == _normalized_path(destination):
            return CachePublishState.PUBLISHED
        if current_path != _normalized_path(initial_path):
            return CachePublishState.UNKNOWN
        if os.path.lexists(destination):
            return CachePublishState.DESTINATION_EXISTS
        return CachePublishState.NOT_PUBLISHED
    except BaseException:
        return CachePublishState.UNKNOWN


def replace_manifest_atomically(
    candidate_handle: WindowsHandle,
    case_root: WindowsHandle,
    expected_manifest_handle: WindowsHandle,
) -> ManifestReplaceResult:
    """Replace manifest.json last and resolve the commit by destination identity."""

    case_before = case_root.info
    case_current = case_root.refresh_info()
    if (
        case_current.identity != case_before.identity
        or not case_current.is_directory
        or case_current.is_reparse_point
    ):
        reject(
            WorkspaceErrorCode.IDENTITY_CHANGED,
            "manifest_replace.root",
            "The authenticated case directory changed before manifest publication.",
        )
    verify_private_acl(case_root)
    require_no_alternate_streams(case_current.final_path)
    destination = case_current.final_path / "manifest.json"

    candidate_before = candidate_handle.info
    candidate_current = candidate_handle.refresh_info()
    if (
        candidate_current.identity != candidate_before.identity
        or candidate_current.is_directory
        or candidate_current.is_reparse_point
        or candidate_current.is_sparse
        or candidate_current.link_count != 1
        or _normalized_path(candidate_current.final_path.parent)
        != _normalized_path(case_current.final_path / "temp")
        or _PENDING_SOURCE_NAME.fullmatch(candidate_current.final_path.name) is None
    ):
        reject(
            WorkspaceErrorCode.IDENTITY_CHANGED,
            "manifest_replace.candidate",
            "The staged case record changed before publication.",
        )
    require_no_alternate_streams(candidate_current.final_path)

    expected_before = expected_manifest_handle.info
    expected_current = expected_manifest_handle.refresh_info()
    if (
        expected_current.identity != expected_before.identity
        or expected_current.is_directory
        or expected_current.is_reparse_point
        or expected_current.link_count != 1
        or _normalized_path(expected_current.final_path) != _normalized_path(destination)
    ):
        reject(
            WorkspaceErrorCode.IDENTITY_CHANGED,
            "manifest_replace.expected",
            "The authoritative case record changed before replacement.",
        )
    require_no_alternate_streams(destination)

    initial = _resolve_manifest_replace_state(
        destination,
        candidate_identity=candidate_current.identity,
        expected_identity=expected_current.identity,
    )
    if initial is not ManifestReplaceState.NOT_COMMITTED:
        return ManifestReplaceResult(ManifestReplaceState.UNKNOWN)

    delayed_exception: BaseException | None = None
    try:
        renamed, _error_number = _rename_open_file(
            candidate_handle,
            destination=destination,
            replace=True,
        )
    except BaseException as error:
        delayed_exception = error
        renamed = False

    resolved = _resolve_manifest_replace_state(
        destination,
        candidate_identity=candidate_current.identity,
        expected_identity=expected_current.identity,
    )
    result = ManifestReplaceResult(
        resolved,
        delayed_exception=delayed_exception,
    )
    if resolved is ManifestReplaceState.COMMITTED:
        try:
            candidate_handle.refresh_info()
        except Exception:
            pass
        return result
    if delayed_exception is not None and resolved is ManifestReplaceState.NOT_COMMITTED:
        raise delayed_exception
    if delayed_exception is not None:
        return result
    if not renamed and resolved is ManifestReplaceState.NOT_COMMITTED:
        return result
    return ManifestReplaceResult(ManifestReplaceState.UNKNOWN)


def _resolve_manifest_replace_state(
    destination: Path,
    *,
    candidate_identity: FileIdentity,
    expected_identity: FileIdentity,
) -> ManifestReplaceState:
    """Resolve an attempted manifest commit without trusting a path-only observation."""

    try:
        with open_handle(destination, share_delete=True) as current:
            require_expected_handle(current, destination, directory=False)
            if current.info.is_reparse_point or current.info.link_count != 1:
                return ManifestReplaceState.UNKNOWN
            require_no_alternate_streams(destination)
            if current.info.identity == candidate_identity:
                return ManifestReplaceState.COMMITTED
            if current.info.identity == expected_identity:
                return ManifestReplaceState.NOT_COMMITTED
    except Exception:
        return ManifestReplaceState.UNKNOWN
    return ManifestReplaceState.UNKNOWN


def _rename_open_file(
    source_handle: WindowsHandle,
    *,
    destination: Path,
    replace: bool,
) -> tuple[bool, int]:
    """Rename one exact handle using the absolute-name Win32 form proven by PWS-0102."""

    destination_text = str(destination)
    destination_utf16 = destination_text.encode("utf-16-le")
    destination_code_units = len(destination_utf16) // 2

    if replace:

        class _FileRenameInfoEx(ctypes.Structure):
            _fields_ = [
                ("flags", wintypes.DWORD),
                ("root_directory", wintypes.HANDLE),
                ("file_name_length", wintypes.DWORD),
                ("file_name", wintypes.WCHAR * (destination_code_units + 1)),
            ]

        rename: ctypes.Structure = _FileRenameInfoEx(
            wintypes.DWORD(_FILE_RENAME_FLAG_REPLACE_IF_EXISTS | _FILE_RENAME_FLAG_POSIX_SEMANTICS),
            wintypes.HANDLE(),
            wintypes.DWORD(len(destination_utf16)),
            destination_text,
        )
        information_class = _FILE_RENAME_INFO_EX_CLASS
    else:

        class _FileRenameInfo(ctypes.Structure):
            _fields_ = [
                ("replace_if_exists", wintypes.BOOLEAN),
                ("root_directory", wintypes.HANDLE),
                ("file_name_length", wintypes.DWORD),
                ("file_name", wintypes.WCHAR * (destination_code_units + 1)),
            ]

        rename = _FileRenameInfo(
            wintypes.BOOLEAN(0),
            wintypes.HANDLE(),
            wintypes.DWORD(len(destination_utf16)),
            destination_text,
        )
        information_class = _FILE_RENAME_INFO_CLASS
    kernel32, _, _ = _libraries()
    set_information = kernel32.SetFileInformationByHandle
    set_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    set_information.restype = wintypes.BOOL
    renamed = bool(
        set_information(
            wintypes.HANDLE(source_handle.value),
            information_class,
            ctypes.byref(rename),
            ctypes.sizeof(rename),
        )
    )
    return renamed, 0 if renamed else int(ctypes.get_last_error())


def _query_opened_info(handle: int) -> OpenedFileInfo:
    kernel32, _, _ = _libraries()
    get_information = kernel32.GetFileInformationByHandleEx
    get_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    get_information.restype = wintypes.BOOL

    identity = _FileIdInfo()
    attributes = _FileAttributeTagInfo()
    standard = _FileStandardInfo()
    requests = (
        (_FILE_ID_INFO_CLASS, identity),
        (_FILE_ATTRIBUTE_TAG_INFO_CLASS, attributes),
        (_FILE_STANDARD_INFO_CLASS, standard),
    )
    for information_class, destination in requests:
        if not get_information(
            wintypes.HANDLE(handle),
            information_class,
            ctypes.byref(destination),
            ctypes.sizeof(destination),
        ):
            _raise_api_failure("filesystem.identity")

    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    get_final_path.restype = wintypes.DWORD
    required = get_final_path(wintypes.HANDLE(handle), None, 0, 0)
    if required == 0 or required > 32_767:
        _raise_api_failure("filesystem.final_path")
    buffer = ctypes.create_unicode_buffer(required + 1)
    written = get_final_path(wintypes.HANDLE(handle), buffer, len(buffer), 0)
    if written == 0 or written >= len(buffer):
        _raise_api_failure("filesystem.final_path")

    file_attributes = int(attributes.file_attributes)
    return OpenedFileInfo(
        identity=FileIdentity(
            volume_serial_number=int(identity.volume_serial_number),
            file_id=bytes(identity.file_id.identifier),
        ),
        final_path=Path(_strip_extended_prefix(buffer.value)),
        is_directory=bool(file_attributes & _FILE_ATTRIBUTE_DIRECTORY),
        is_reparse_point=bool(file_attributes & _FILE_ATTRIBUTE_REPARSE_POINT),
        is_sparse=bool(file_attributes & _FILE_ATTRIBUTE_SPARSE_FILE),
        link_count=int(standard.number_of_links),
        logical_size=int(standard.end_of_file),
    )


def require_expected_handle(
    handle: WindowsHandle,
    expected_path: Path,
    *,
    directory: bool,
    expected_identity: FileIdentity | None = None,
) -> None:
    """Validate type, identity, reparse state, and the authenticated final path."""

    info = handle.info
    if info.is_reparse_point:
        reject(
            WorkspaceErrorCode.REPARSE_POINT,
            "filesystem.reparse",
            "Linked or redirected storage locations are not supported.",
        )
    if info.is_directory is not directory:
        reject(
            WorkspaceErrorCode.PATH_INVALID,
            "filesystem.type",
            "The storage object has an unexpected type.",
        )
    if _normalized_path(info.final_path) != _normalized_path(expected_path):
        reject(
            WorkspaceErrorCode.IDENTITY_CHANGED,
            "filesystem.containment",
            "The authenticated storage object is outside its expected location.",
        )
    if expected_identity is not None and info.identity != expected_identity:
        reject(
            WorkspaceErrorCode.IDENTITY_CHANGED,
            "filesystem.identity",
            "The storage object changed during the operation.",
        )


def require_no_alternate_streams(path: Path) -> None:
    """Reject a regular file with any named NTFS alternate data stream."""

    kernel32, _, _ = _libraries()
    find_first_stream = kernel32.FindFirstStreamW
    find_first_stream.argtypes = (
        wintypes.LPCWSTR,
        ctypes.c_int,
        ctypes.POINTER(_Win32FindStreamData),
        wintypes.DWORD,
    )
    find_first_stream.restype = wintypes.HANDLE
    find_next_stream = kernel32.FindNextStreamW
    find_next_stream.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_Win32FindStreamData),
    )
    find_next_stream.restype = wintypes.BOOL
    find_close = kernel32.FindClose
    find_close.argtypes = (wintypes.HANDLE,)
    find_close.restype = wintypes.BOOL

    stream = _Win32FindStreamData()
    raw_handle = find_first_stream(str(path), 0, ctypes.byref(stream), 0)
    handle_value = int(raw_handle) if raw_handle else 0
    if not handle_value or handle_value == _INVALID_HANDLE_VALUE:
        if ctypes.get_last_error() == _ERROR_HANDLE_EOF:
            return
        _raise_api_failure("filesystem.streams")
    try:
        if stream.stream_name != "::$DATA":
            reject(
                WorkspaceErrorCode.ALIAS_DETECTED,
                "filesystem.streams",
                "Alternate data streams are not supported in case storage.",
            )
        if find_next_stream(wintypes.HANDLE(handle_value), ctypes.byref(stream)):
            reject(
                WorkspaceErrorCode.ALIAS_DETECTED,
                "filesystem.streams",
                "Alternate data streams are not supported in case storage.",
            )
        if ctypes.get_last_error() != _ERROR_HANDLE_EOF:
            _raise_api_failure("filesystem.streams")
    finally:
        find_close(wintypes.HANDLE(handle_value))


def _token_sid(information_class: int) -> tuple[ctypes.Array[ctypes.c_char], int]:
    kernel32, advapi32, _ = _libraries()
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.argtypes = ()
    get_current_process.restype = wintypes.HANDLE
    open_process_token = advapi32.OpenProcessToken
    open_process_token.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    )
    open_process_token.restype = wintypes.BOOL
    get_token_information = advapi32.GetTokenInformation
    get_token_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    get_token_information.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    if not open_process_token(get_current_process(), _TOKEN_QUERY, ctypes.byref(token)):
        _raise_api_failure("permissions.token", permissions=True)
    try:
        required = wintypes.DWORD()
        get_token_information(token, information_class, None, 0, ctypes.byref(required))
        if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER or required.value == 0:
            _raise_api_failure("permissions.token", permissions=True)
        buffer = ctypes.create_string_buffer(required.value)
        if not get_token_information(
            token,
            information_class,
            buffer,
            len(buffer),
            ctypes.byref(required),
        ):
            _raise_api_failure("permissions.token", permissions=True)
        if information_class == _TOKEN_USER_CLASS:
            sid = int(_TokenUser.from_buffer(buffer).user.sid)
        else:
            sid = int(_TokenOwner.from_buffer(buffer).owner)
        return buffer, sid
    finally:
        close_handle(token)


def _sid_string(sid: int) -> str:
    _, advapi32, _ = _libraries()
    is_valid_sid = advapi32.IsValidSid
    is_valid_sid.argtypes = (ctypes.c_void_p,)
    is_valid_sid.restype = wintypes.BOOL
    convert_sid = advapi32.ConvertSidToStringSidW
    convert_sid.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR))
    convert_sid.restype = wintypes.BOOL
    local_free = ctypes.WinDLL("kernel32", use_last_error=True).LocalFree
    local_free.argtypes = (ctypes.c_void_p,)
    local_free.restype = ctypes.c_void_p
    if not is_valid_sid(ctypes.c_void_p(sid)):
        _raise_api_failure("permissions.sid", permissions=True)
    rendered = wintypes.LPWSTR()
    if not convert_sid(ctypes.c_void_p(sid), ctypes.byref(rendered)):
        _raise_api_failure("permissions.sid", permissions=True)
    try:
        value = rendered.value
        if value is None:
            _raise_api_failure("permissions.sid", permissions=True)
        return value
    finally:
        local_free(rendered)


def verify_private_acl(handle: WindowsHandle) -> None:
    """Verify the owner and protected DACL of one authenticated directory handle."""

    if not handle.info.is_directory or handle.info.is_reparse_point:
        reject(
            WorkspaceErrorCode.PERMISSIONS_INSECURE,
            "permissions.object",
            "Private permissions could not be verified for this storage object.",
        )
    _, advapi32, _ = _libraries()
    get_security_info = advapi32.GetSecurityInfo
    get_security_info.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    )
    get_security_info.restype = wintypes.DWORD
    get_control = advapi32.GetSecurityDescriptorControl
    get_control.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    )
    get_control.restype = wintypes.BOOL
    get_dacl = advapi32.GetSecurityDescriptorDacl
    get_dacl.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    )
    get_dacl.restype = wintypes.BOOL
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
    local_free = ctypes.WinDLL("kernel32", use_last_error=True).LocalFree
    local_free.argtypes = (ctypes.c_void_p,)
    local_free.restype = ctypes.c_void_p

    owner = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    status = get_security_info(
        wintypes.HANDLE(handle.value),
        _SE_FILE_OBJECT,
        _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
        ctypes.byref(owner),
        None,
        None,
        None,
        ctypes.byref(descriptor),
    )
    if status != 0 or not descriptor.value or not owner.value:
        _raise_api_failure("permissions.read", permissions=True)
    try:
        token_sids = (
            _token_sid(_TOKEN_USER_CLASS),
            _token_sid(_TOKEN_OWNER_CLASS),
        )
        user_sid = token_sids[0][1]
        token_owner_sid = token_sids[1][1]
        owner_matches = bool(
            equal_sid(owner, ctypes.c_void_p(user_sid))
            or equal_sid(owner, ctypes.c_void_p(token_owner_sid))
        )
        if not owner_matches:
            reject(
                WorkspaceErrorCode.PERMISSIONS_INSECURE,
                "permissions.owner",
                "The case directory is not owned by the current Windows account.",
            )

        control = wintypes.WORD()
        revision = wintypes.DWORD()
        if not get_control(descriptor, ctypes.byref(control), ctypes.byref(revision)):
            _raise_api_failure("permissions.control", permissions=True)
        if not int(control.value) & _SE_DACL_PROTECTED:
            reject(
                WorkspaceErrorCode.PERMISSIONS_INSECURE,
                "permissions.inheritance",
                "The case directory inherits access from another location.",
            )

        dacl_present = wintypes.BOOL()
        dacl_defaulted = wintypes.BOOL()
        dacl = ctypes.c_void_p()
        if not get_dacl(
            descriptor,
            ctypes.byref(dacl_present),
            ctypes.byref(dacl),
            ctypes.byref(dacl_defaulted),
        ):
            _raise_api_failure("permissions.dacl", permissions=True)
        if not dacl_present.value or not dacl.value or dacl_defaulted.value:
            reject(
                WorkspaceErrorCode.PERMISSIONS_INSECURE,
                "permissions.dacl",
                "The case directory does not have an explicit private access policy.",
            )

        acl_info = _AclSizeInformation()
        if not get_acl_information(
            dacl,
            ctypes.byref(acl_info),
            ctypes.sizeof(acl_info),
            _ACL_SIZE_INFORMATION_CLASS,
        ):
            _raise_api_failure("permissions.dacl", permissions=True)
        if int(acl_info.ace_count) != len(_EXPECTED_PRIVATE_TRUSTEES):
            reject(
                WorkspaceErrorCode.PERMISSIONS_INSECURE,
                "permissions.trustees",
                "The case directory grants access outside the approved private policy.",
            )

        trustees: set[str] = set()
        for index in range(int(acl_info.ace_count)):
            ace_pointer = ctypes.c_void_p()
            if not get_ace(dacl, index, ctypes.byref(ace_pointer)) or not ace_pointer.value:
                _raise_api_failure("permissions.ace", permissions=True)
            ace_address = ace_pointer.value
            if ace_address is None:
                _raise_api_failure("permissions.ace", permissions=True)
            ace = _AccessAllowedAce.from_address(ace_address)
            if (
                int(ace.header.ace_type) != _ACCESS_ALLOWED_ACE_TYPE
                or int(ace.header.ace_flags) != (_OBJECT_INHERIT_ACE | _CONTAINER_INHERIT_ACE)
                or int(ace.mask) != _FILE_ALL_ACCESS
            ):
                reject(
                    WorkspaceErrorCode.PERMISSIONS_INSECURE,
                    "permissions.ace",
                    "The case directory access policy is not the supported private policy.",
                )
            sid_address = ace_address + _AccessAllowedAce.sid_start.offset
            trustees.add(_sid_string(sid_address))
        if trustees != _EXPECTED_PRIVATE_TRUSTEES:
            reject(
                WorkspaceErrorCode.PERMISSIONS_INSECURE,
                "permissions.trustees",
                "The case directory grants access outside the approved private policy.",
            )
    finally:
        local_free(descriptor)


def require_local_acl_volume(path: Path) -> None:
    """Require a fixed local volume that advertises persistent ACL support."""

    kernel32, _, _ = _libraries()
    get_volume_path = kernel32.GetVolumePathNameW
    get_volume_path.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD)
    get_volume_path.restype = wintypes.BOOL
    get_drive_type = kernel32.GetDriveTypeW
    get_drive_type.argtypes = (wintypes.LPCWSTR,)
    get_drive_type.restype = wintypes.UINT
    get_volume_information = kernel32.GetVolumeInformationW
    get_volume_information.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        wintypes.DWORD,
    )
    get_volume_information.restype = wintypes.BOOL

    volume_root = ctypes.create_unicode_buffer(32_768)
    if not get_volume_path(str(path), volume_root, len(volume_root)):
        _raise_api_failure("volume.root", permissions=True)
    if get_drive_type(volume_root.value) != _DRIVE_FIXED:
        reject(
            WorkspaceErrorCode.LOCATION_UNSUPPORTED,
            "volume.location",
            "Cases must be stored on a supported fixed local drive.",
        )
    flags = wintypes.DWORD()
    if not get_volume_information(
        volume_root.value,
        None,
        0,
        None,
        None,
        ctypes.byref(flags),
        None,
        0,
    ):
        _raise_api_failure("volume.capabilities", permissions=True)
    if not int(flags.value) & _FILE_PERSISTENT_ACLS:
        reject(
            WorkspaceErrorCode.PERMISSIONS_UNSUPPORTED,
            "volume.permissions",
            "This storage location cannot prove persistent private permissions.",
        )


def current_profile_root() -> Path:
    """Resolve the current user's Windows profile without trusting environment variables."""

    _, _, shell32 = _libraries()
    sh_get_known_folder = shell32.SHGetKnownFolderPath
    sh_get_known_folder.argtypes = (
        ctypes.POINTER(_Guid),
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.LPWSTR),
    )
    sh_get_known_folder.restype = ctypes.c_long
    folder_profile = _Guid.from_uuid(UUID("5e6c858f-0e22-4760-9afe-ea3317b67173"))
    value = wintypes.LPWSTR()
    try:
        if sh_get_known_folder(ctypes.byref(folder_profile), 0, None, ctypes.byref(value)) != 0:
            _raise_api_failure("profile.location")
        profile = value.value
        if profile is None:
            _raise_api_failure("profile.location")
        return Path(profile)
    finally:
        _co_task_mem_free(value)

"""Private Win32 primitives for one pinned read-only external source."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, Final, Self

from paperworkshell.domain.import_precheck import MAXIMUM_HEADER_BUDGET
from paperworkshell.storage._windows import FileIdentity

_INVALID_HANDLE_VALUE: Final = ctypes.c_void_p(-1).value
_GENERIC_READ: Final = 0x8000_0000
_FILE_READ_ATTRIBUTES: Final = 0x0000_0080
_FILE_SHARE_READ: Final = 0x0000_0001
_OPEN_EXISTING: Final = 3
_FILE_FLAG_OPEN_NO_RECALL: Final = 0x0010_0000
_FILE_FLAG_OPEN_REPARSE_POINT: Final = 0x0020_0000
_FILE_FLAG_BACKUP_SEMANTICS: Final = 0x0200_0000
_FILE_FLAG_SEQUENTIAL_SCAN: Final = 0x0800_0000
_FILE_BEGIN: Final = 0
_FILE_TYPE_DISK: Final = 1
_DRIVE_FIXED: Final = 3

_FILE_BASIC_INFO_CLASS: Final = 0
_FILE_STANDARD_INFO_CLASS: Final = 1
_FILE_ATTRIBUTE_TAG_INFO_CLASS: Final = 9
_FILE_ID_INFO_CLASS: Final = 18
_FILE_ATTRIBUTE_DIRECTORY: Final = 0x0000_0010
_FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x0000_0400
_UNSUPPORTED_SOURCE_ATTRIBUTES: Final = 0x0000_1000 | 0x0004_0000 | 0x0040_0000

MAXIMUM_SOURCE_READ_BYTES: Final = MAXIMUM_HEADER_BUDGET


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


class _FileBasicInfo(ctypes.Structure):
    _fields_ = [
        ("creation_time", ctypes.c_longlong),
        ("last_access_time", ctypes.c_longlong),
        ("last_write_time", ctypes.c_longlong),
        ("change_time", ctypes.c_longlong),
        ("file_attributes", wintypes.DWORD),
    ]


@dataclass(slots=True)
class SourceHandleFailure(OSError):
    """A private fixed-token failure that never contains source details."""

    operation: str

    def __str__(self) -> str:
        return f"Authenticated source operation failed at {self.operation}."


@dataclass(frozen=True, slots=True)
class SourceFileInfo:
    """Fresh security facts from the currently open source handle."""

    identity: FileIdentity
    final_path: Path = field(repr=False)
    logical_size: int
    link_count: int
    delete_pending: bool
    is_directory: bool
    is_reparse_point: bool
    creation_time: int
    last_write_time: int
    change_time: int
    file_attributes: int


def _kernel32() -> Any:
    if os.name != "nt":
        raise SourceHandleFailure("source.platform")
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _strip_extended_prefix(value: str) -> str:
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[8:]
    if value.startswith("\\\\?\\"):
        return value[4:]
    return value


def is_fixed_local_drive(path: Path) -> bool:
    """Classify a drive-qualified path before attempting a source open."""

    kernel32 = _kernel32()
    get_drive_type = kernel32.GetDriveTypeW
    get_drive_type.argtypes = (wintypes.LPCWSTR,)
    get_drive_type.restype = wintypes.UINT
    anchor = path.anchor
    return bool(anchor) and get_drive_type(anchor) == _DRIVE_FIXED


class AuthenticatedSourceHandle:
    """A source handle retained with its authenticated no-follow ancestor chain."""

    __slots__ = ("_ancestor_handles", "_ancestor_infos", "_handle", "_initial_info", "_lock")

    def __init__(
        self,
        handle: int,
        initial_info: SourceFileInfo,
        ancestor_handles: tuple[int, ...],
        ancestor_infos: tuple[SourceFileInfo, ...],
    ) -> None:
        self._handle = handle
        self._initial_info = initial_info
        self._ancestor_handles = ancestor_handles
        self._ancestor_infos = ancestor_infos
        self._lock = RLock()

    @property
    def initial_info(self) -> SourceFileInfo:
        return self._initial_info

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._handle != 0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def read_at(self, offset: int, count: int) -> bytes:
        """Read an exact bounded range using this handle's serialized file pointer."""

        if (
            type(offset) is not int
            or type(count) is not int
            or offset < 0
            or not 0 <= count <= MAXIMUM_SOURCE_READ_BYTES
            or offset > self._initial_info.logical_size - count
        ):
            raise SourceHandleFailure("source.read_range")
        if count == 0:
            return b""
        with self._lock:
            if self._handle == 0:
                raise SourceHandleFailure("source.closed")
            kernel32 = _kernel32()
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
                ctypes.c_longlong(offset),
                None,
                _FILE_BEGIN,
            ):
                raise SourceHandleFailure("source.seek")
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
                read_count = wintypes.DWORD()
                remaining = count - completed
                if not read_file(
                    wintypes.HANDLE(self._handle),
                    ctypes.byref(buffer, completed),
                    remaining,
                    ctypes.byref(read_count),
                    None,
                ):
                    raise SourceHandleFailure("source.read")
                if read_count.value == 0:
                    raise SourceHandleFailure("source.short_read")
                completed += int(read_count.value)
            return buffer.raw

    def current_info(self) -> SourceFileInfo:
        """Revalidate pinned ancestors, then query fresh terminal source facts."""

        with self._lock:
            if self._handle == 0:
                raise SourceHandleFailure("source.closed")
            for handle, expected in zip(
                self._ancestor_handles,
                self._ancestor_infos,
                strict=True,
            ):
                _require_same_ancestor(_query_source_info(handle), expected)
            return _query_source_info(self._handle)

    def close(self) -> None:
        """Close idempotently; the source was never opened for writing."""

        with self._lock:
            if self._handle == 0:
                return
            handles = (self._handle, *reversed(self._ancestor_handles))
            self._handle = 0
            self._ancestor_handles = ()
            failed = False
            for handle in handles:
                failed = not _close_raw_handle(handle) or failed
            if failed:
                raise SourceHandleFailure("source.close")


def open_authenticated_source(path: Path) -> AuthenticatedSourceHandle:
    """Pin every local ancestor before opening one read-only default stream."""

    ancestor_handles: list[int] = []
    ancestor_infos: list[SourceFileInfo] = []
    source_handle = 0
    try:
        expected_volume: int | None = None
        for ancestor_path in _ancestor_paths(path):
            ancestor_handle = _open_raw_handle(
                ancestor_path,
                _FILE_READ_ATTRIBUTES,
                _FILE_FLAG_OPEN_NO_RECALL
                | _FILE_FLAG_OPEN_REPARSE_POINT
                | _FILE_FLAG_BACKUP_SEMANTICS,
                "source.ancestor_open",
            )
            try:
                ancestor_info = _query_source_info(ancestor_handle)
                _require_ancestor(ancestor_info, ancestor_path, expected_volume)
            except BaseException:
                _close_raw_handle(ancestor_handle)
                raise
            if expected_volume is None:
                expected_volume = ancestor_info.identity.volume_serial_number
            ancestor_handles.append(ancestor_handle)
            ancestor_infos.append(ancestor_info)

        source_handle = _open_raw_handle(
            path,
            _GENERIC_READ,
            _FILE_FLAG_OPEN_NO_RECALL | _FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_SEQUENTIAL_SCAN,
            "source.open",
        )
        if _get_file_type(source_handle) != _FILE_TYPE_DISK:
            raise SourceHandleFailure("source.type")
        initial_info = _query_source_info(source_handle)
        if expected_volume is None or initial_info.identity.volume_serial_number != expected_volume:
            raise SourceHandleFailure("source.ancestor_alias")
        return AuthenticatedSourceHandle(
            source_handle,
            initial_info,
            tuple(ancestor_handles),
            tuple(ancestor_infos),
        )
    except BaseException:
        if source_handle:
            _close_raw_handle(source_handle)
        for ancestor_handle in reversed(ancestor_handles):
            _close_raw_handle(ancestor_handle)
        raise


def _ancestor_paths(path: Path) -> tuple[Path, ...]:
    parts = path.parts
    if not parts:
        raise SourceHandleFailure("source.ancestor_path")
    current = Path(parts[0])
    ancestors = [current]
    for component in parts[1:-1]:
        current /= component
        ancestors.append(current)
    return tuple(ancestors)


def _open_raw_handle(path: Path, access: int, flags: int, operation: str) -> int:
    kernel32 = _kernel32()
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
        access,
        _FILE_SHARE_READ,
        None,
        _OPEN_EXISTING,
        flags,
        None,
    )
    handle = int(raw_handle) if raw_handle else 0
    if not handle or handle == _INVALID_HANDLE_VALUE:
        raise SourceHandleFailure(operation)
    return handle


def _close_raw_handle(handle: int) -> bool:
    kernel32 = _kernel32()
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    return bool(close_handle(wintypes.HANDLE(handle)))


def _get_file_type(handle: int) -> int:
    kernel32 = _kernel32()
    get_file_type = kernel32.GetFileType
    get_file_type.argtypes = (wintypes.HANDLE,)
    get_file_type.restype = wintypes.DWORD
    return int(get_file_type(wintypes.HANDLE(handle)))


def _require_ancestor(
    info: SourceFileInfo,
    expected_path: Path,
    expected_volume: int | None,
) -> None:
    if not info.is_directory:
        raise SourceHandleFailure("source.ancestor_type")
    if info.is_reparse_point:
        raise SourceHandleFailure("source.ancestor_reparse")
    if info.file_attributes & _UNSUPPORTED_SOURCE_ATTRIBUTES:
        raise SourceHandleFailure("source.ancestor_location")
    if (
        info.delete_pending
        or _normalized_path(info.final_path) != _normalized_path(expected_path)
        or (expected_volume is not None and info.identity.volume_serial_number != expected_volume)
    ):
        raise SourceHandleFailure("source.ancestor_alias")


def _require_same_ancestor(current: SourceFileInfo, expected: SourceFileInfo) -> None:
    if (
        current.identity != expected.identity
        or _normalized_path(current.final_path) != _normalized_path(expected.final_path)
        or current.delete_pending
        or not current.is_directory
        or current.is_reparse_point
        or current.file_attributes & _UNSUPPORTED_SOURCE_ATTRIBUTES
    ):
        raise SourceHandleFailure("source.ancestor_changed")


def _normalized_path(path: Path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _query_source_info(handle: int) -> SourceFileInfo:
    kernel32 = _kernel32()
    get_information = kernel32.GetFileInformationByHandleEx
    get_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    get_information.restype = wintypes.BOOL
    basic = _FileBasicInfo()
    standard = _FileStandardInfo()
    attributes = _FileAttributeTagInfo()
    identity = _FileIdInfo()
    requests = (
        (_FILE_BASIC_INFO_CLASS, basic),
        (_FILE_STANDARD_INFO_CLASS, standard),
        (_FILE_ATTRIBUTE_TAG_INFO_CLASS, attributes),
        (_FILE_ID_INFO_CLASS, identity),
    )
    for information_class, destination in requests:
        if not get_information(
            wintypes.HANDLE(handle),
            information_class,
            ctypes.byref(destination),
            ctypes.sizeof(destination),
        ):
            raise SourceHandleFailure("source.identity")

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
        raise SourceHandleFailure("source.final_path")
    buffer = ctypes.create_unicode_buffer(required + 1)
    written = get_final_path(wintypes.HANDLE(handle), buffer, len(buffer), 0)
    if written == 0 or written >= len(buffer):
        raise SourceHandleFailure("source.final_path")

    file_attributes = int(attributes.file_attributes)
    return SourceFileInfo(
        identity=FileIdentity(
            volume_serial_number=int(identity.volume_serial_number),
            file_id=bytes(identity.file_id.identifier),
        ),
        final_path=Path(_strip_extended_prefix(buffer.value)),
        logical_size=int(standard.end_of_file),
        link_count=int(standard.number_of_links),
        delete_pending=bool(standard.delete_pending),
        is_directory=bool(file_attributes & _FILE_ATTRIBUTE_DIRECTORY),
        is_reparse_point=bool(file_attributes & _FILE_ATTRIBUTE_REPARSE_POINT),
        creation_time=int(basic.creation_time),
        last_write_time=int(basic.last_write_time),
        change_time=int(basic.change_time),
        file_attributes=int(basic.file_attributes),
    )

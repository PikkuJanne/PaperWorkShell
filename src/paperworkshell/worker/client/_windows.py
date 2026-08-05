"""Classic AppContainer process launch for the document worker.

This module is the only UI-side module that knows the Win32 process and
containment APIs.  It deliberately accepts no case path, command line,
environment mapping, or caller-supplied handle.  A launch is assembled from a
trusted, fixed configuration and a typed launch request.
"""

from __future__ import annotations

import ctypes
import io
import math
import os
import subprocess
import threading
import time
from contextlib import AbstractContextManager
from ctypes import wintypes
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from secrets import token_hex
from typing import TYPE_CHECKING, Any, Final, Never, Self

from paperworkshell.worker.protocol.bootstrap import (
    BOOTSTRAP_BYTES,
    BootstrapGrant,
    encode_bootstrap_grant,
)

if TYPE_CHECKING:
    from paperworkshell.worker.client.process import (
        BrokeredHandlePurpose,
        IsolationEvidence,
        WorkerLaunchRequest,
        WorkerResourcePolicy,
        WorkerTransport,
    )


_INVALID_HANDLE_VALUE: Final = ctypes.c_void_p(-1).value
_STILL_ACTIVE: Final = 259

_CREATE_SUSPENDED: Final = 0x0000_0004
_CREATE_UNICODE_ENVIRONMENT: Final = 0x0000_0400
_CREATE_NO_WINDOW: Final = 0x0800_0000
_EXTENDED_STARTUPINFO_PRESENT: Final = 0x0008_0000
_STARTF_USESTDHANDLES: Final = 0x0000_0100

_PROC_THREAD_ATTRIBUTE_HANDLE_LIST: Final = 0x0002_0002
_PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES: Final = 0x0002_0009

_HANDLE_FLAG_INHERIT: Final = 0x0000_0001
_WAIT_OBJECT_0: Final = 0
_WAIT_ABANDONED: Final = 0x0000_0080
_WAIT_TIMEOUT: Final = 258
_INFINITE: Final = 0xFFFF_FFFF

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS: Final = 9
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS: Final = 1
_JOB_OBJECT_LIMIT_PROCESS_TIME: Final = 0x0000_0002
_JOB_OBJECT_LIMIT_JOB_TIME: Final = 0x0000_0004
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS: Final = 0x0000_0008
_JOB_OBJECT_LIMIT_PROCESS_MEMORY: Final = 0x0000_0100
_JOB_OBJECT_LIMIT_JOB_MEMORY: Final = 0x0000_0200
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: Final = 0x0000_2000

_SE_FILE_OBJECT: Final = 1
_SE_KERNEL_OBJECT: Final = 6
_OWNER_SECURITY_INFORMATION: Final = 0x0000_0001
_DACL_SECURITY_INFORMATION: Final = 0x0000_0004
_GRANT_ACCESS: Final = 1
_REVOKE_ACCESS: Final = 4
_TRUSTEE_IS_SID: Final = 0
_TRUSTEE_IS_UNKNOWN: Final = 0
_SUB_CONTAINERS_AND_OBJECTS_INHERIT: Final = 0x3
_FILE_GENERIC_READ_EXECUTE: Final = 0x0012_00A9
_FILE_READ_DATA: Final = 0x0000_0001
_FILE_WRITE_DATA: Final = 0x0000_0002
_FILE_APPEND_DATA: Final = 0x0000_0004
_FILE_DELETE_CHILD: Final = 0x0000_0040
_FILE_READ_ATTRIBUTES: Final = 0x0000_0080
_FILE_WRITE_ATTRIBUTES: Final = 0x0000_0100
_DELETE: Final = 0x0001_0000
_READ_CONTROL: Final = 0x0002_0000
_SYNCHRONIZE: Final = 0x0010_0000
_PRIVATE_TEMP_ACCESS: Final = (
    _FILE_READ_DATA
    | _FILE_WRITE_DATA
    | _FILE_APPEND_DATA
    | _FILE_DELETE_CHILD
    | _FILE_READ_ATTRIBUTES
    | _FILE_WRITE_ATTRIBUTES
    | _DELETE
    | _READ_CONTROL
    | _SYNCHRONIZE
)
_ACCESS_ALLOWED_ACE_TYPE: Final = 0
_ACL_SIZE_INFORMATION_CLASS: Final = 2
_MUTEX_ALL_ACCESS: Final = 0x001F_0001
_SE_DACL_PRESENT: Final = 0x0004
_SE_DACL_AUTO_INHERITED: Final = 0x0400
_SE_SELF_RELATIVE: Final = 0x8000

_ERROR_INSUFFICIENT_BUFFER: Final = 122
_ERROR_ALREADY_EXISTS: Final = 183
_ERROR_BROKEN_PIPE: Final = 109
_ERROR_SUCCESS: Final = 0
_INVALID_FILE_ATTRIBUTES: Final = 0xFFFF_FFFF
_FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x0000_0400
_DRIVE_FIXED: Final = 3
_TOKEN_USER_INFORMATION_CLASS: Final = 1
_CURRENT_PROCESS_TOKEN: Final = -4

_PROFILE_PREFIX: Final = "PaperWorkShell.Worker."
_PROFILE_DISPLAY_NAME: Final = "PaperWorkShell document worker"
_PROFILE_DESCRIPTION: Final = "Ephemeral restricted document-processing worker"
_MAX_PROFILE_NAME_CODEPOINTS: Final = 64
_MAX_BOOTSTRAP_WRITE_ATTEMPTS: Final = 4
_MAX_APPROVED_RESOURCE_ENTRIES: Final = 20_000
_MAX_APPROVED_RESOURCE_DEPTH: Final = 64
_PROFILE_DELETE_ATTEMPTS: Final = 5
_PROFILE_DELETE_RETRY_SECONDS: Final = 0.125
_PROFILE_CLEANUP_ATTEMPTS: Final = 3
_HANDLE_CLOSE_ATTEMPTS: Final = 3
_ACL_MUTATION_MUTEX_NAME: Final = "Global\\PaperWorkShell.WorkerResourceAcl.v1"
_ACL_MUTATION_WAIT_MILLISECONDS: Final = 120_000
_PROCESS_CREATION_WAIT_SECONDS: Final = 10.0
_MAX_WINDOWS_CPU_TIME_MS: Final = ((1 << 63) - 1) // 10_000
_MAX_WINDOWS_SIZE_T: Final = (1 << (ctypes.sizeof(ctypes.c_size_t) * 8)) - 1
_MAX_WINDOWS_WAIT_SECONDS: Final = 86_400.0
_TREE_WAIT_QUANTUM_SECONDS: Final = 0.01
_TERMINATION_WAIT_SECONDS: Final = 10.0

_PROCESS_CREATION_LOCK: Final = threading.Lock()
_PROCESS_CREATION_QUARANTINED = False
_PROCESS_CREATION_CLEANUP_PENDING = False


def _acquire_process_creation_authority() -> None:
    if not _PROCESS_CREATION_LOCK.acquire(timeout=_PROCESS_CREATION_WAIT_SECONDS):
        _reject(
            WindowsLaunchErrorCode.CLEANUP_FAILED,
            "process_creation.lock",
            "Worker process creation is unavailable after a cleanup failure.",
        )
    if _PROCESS_CREATION_QUARANTINED or _PROCESS_CREATION_CLEANUP_PENDING:
        _PROCESS_CREATION_LOCK.release()
        _reject(
            WindowsLaunchErrorCode.CLEANUP_FAILED,
            "process_creation.quarantine",
            "Worker process creation remains disabled after a handle cleanup failure.",
        )
    if not _drain_launch_quarantines() or _PROCESS_CREATION_QUARANTINED:
        _PROCESS_CREATION_LOCK.release()
        _reject(
            WindowsLaunchErrorCode.CLEANUP_FAILED,
            "launch.quarantine",
            "A previous worker launch still requires deterministic cleanup.",
        )


class WindowsLaunchErrorCode(StrEnum):
    """Stable, non-disclosing Windows launch failure codes."""

    PLATFORM_UNSUPPORTED = "WORKER_WINDOWS_PLATFORM_UNSUPPORTED"
    CONFIGURATION_INVALID = "WORKER_WINDOWS_CONFIGURATION_INVALID"
    ISOLATION_UNAVAILABLE = "WORKER_WINDOWS_ISOLATION_UNAVAILABLE"
    PROFILE_FAILED = "WORKER_WINDOWS_PROFILE_FAILED"
    ACCESS_GRANT_FAILED = "WORKER_WINDOWS_ACCESS_GRANT_FAILED"
    PIPE_FAILED = "WORKER_WINDOWS_PIPE_FAILED"
    JOB_FAILED = "WORKER_WINDOWS_JOB_FAILED"
    PROCESS_FAILED = "WORKER_WINDOWS_PROCESS_FAILED"
    HANDLE_TRANSFER_FAILED = "WORKER_WINDOWS_HANDLE_TRANSFER_FAILED"
    BOOTSTRAP_FAILED = "WORKER_WINDOWS_BOOTSTRAP_FAILED"
    TERMINATION_FAILED = "WORKER_WINDOWS_TERMINATION_FAILED"
    CLEANUP_FAILED = "WORKER_WINDOWS_CLEANUP_FAILED"


@dataclass(slots=True)
class WindowsLaunchError(RuntimeError):
    """A stable failure that contains no path, command, environment, SID, or raw error."""

    code: WindowsLaunchErrorCode
    context: str
    safe_message: str

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


@dataclass(frozen=True, slots=True)
class WindowsWorkerResourceSnapshot:
    """Safe numeric resource accounting from one retained Windows Job."""

    process_memory_limit_bytes: int
    job_memory_limit_bytes: int
    peak_process_memory_used_bytes: int
    peak_job_memory_used_bytes: int


def _reject(
    code: WindowsLaunchErrorCode,
    context: str,
    safe_message: str,
) -> Never:
    raise WindowsLaunchError(code=code, context=context, safe_message=safe_message)


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.DWORD),
        ("security_descriptor", ctypes.c_void_p),
        ("inherit_handle", wintypes.BOOL),
    ]


class _StartupInfoW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("reserved", wintypes.LPWSTR),
        ("desktop", wintypes.LPWSTR),
        ("title", wintypes.LPWSTR),
        ("x", wintypes.DWORD),
        ("y", wintypes.DWORD),
        ("x_size", wintypes.DWORD),
        ("y_size", wintypes.DWORD),
        ("x_count_chars", wintypes.DWORD),
        ("y_count_chars", wintypes.DWORD),
        ("fill_attribute", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("show_window", wintypes.WORD),
        ("reserved2_bytes", wintypes.WORD),
        ("reserved2", ctypes.POINTER(wintypes.BYTE)),
        ("standard_input", wintypes.HANDLE),
        ("standard_output", wintypes.HANDLE),
        ("standard_error", wintypes.HANDLE),
    ]


class _StartupInfoExW(ctypes.Structure):
    _fields_ = [
        ("startup_info", _StartupInfoW),
        ("attribute_list", ctypes.c_void_p),
    ]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("process", wintypes.HANDLE),
        ("thread", wintypes.HANDLE),
        ("process_id", wintypes.DWORD),
        ("thread_id", wintypes.DWORD),
    ]


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [
        ("sid", ctypes.c_void_p),
        ("attributes", wintypes.DWORD),
    ]


class _TokenUser(ctypes.Structure):
    _fields_ = [("user", _SidAndAttributes)]


class _SecurityCapabilities(ctypes.Structure):
    _fields_ = [
        ("app_container_sid", ctypes.c_void_p),
        ("capabilities", ctypes.POINTER(_SidAndAttributes)),
        ("capability_count", wintypes.DWORD),
        ("reserved", wintypes.DWORD),
    ]


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("per_process_user_time_limit", ctypes.c_longlong),
        ("per_job_user_time_limit", ctypes.c_longlong),
        ("limit_flags", wintypes.DWORD),
        ("minimum_working_set_size", ctypes.c_size_t),
        ("maximum_working_set_size", ctypes.c_size_t),
        ("active_process_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority_class", wintypes.DWORD),
        ("scheduling_class", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("read_operation_count", ctypes.c_ulonglong),
        ("write_operation_count", ctypes.c_ulonglong),
        ("other_operation_count", ctypes.c_ulonglong),
        ("read_transfer_count", ctypes.c_ulonglong),
        ("write_transfer_count", ctypes.c_ulonglong),
        ("other_transfer_count", ctypes.c_ulonglong),
    ]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("basic_limit_information", _JobObjectBasicLimitInformation),
        ("io_info", _IoCounters),
        ("process_memory_limit", ctypes.c_size_t),
        ("job_memory_limit", ctypes.c_size_t),
        ("peak_process_memory_used", ctypes.c_size_t),
        ("peak_job_memory_used", ctypes.c_size_t),
    ]


class _JobObjectBasicAccountingInformation(ctypes.Structure):
    _fields_ = [
        ("total_user_time", ctypes.c_longlong),
        ("total_kernel_time", ctypes.c_longlong),
        ("this_period_total_user_time", ctypes.c_longlong),
        ("this_period_total_kernel_time", ctypes.c_longlong),
        ("total_page_fault_count", wintypes.DWORD),
        ("total_processes", wintypes.DWORD),
        ("active_processes", wintypes.DWORD),
        ("total_terminated_processes", wintypes.DWORD),
    ]


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


class _AccessAllowedAce(ctypes.Structure):
    _fields_ = [
        ("header", _AceHeader),
        ("mask", wintypes.DWORD),
        ("sid_start", wintypes.DWORD),
    ]


def _kernel32() -> Any:
    if os.name != "nt":
        _reject(
            WindowsLaunchErrorCode.PLATFORM_UNSUPPORTED,
            "platform",
            "The restricted document worker requires supported Windows isolation.",
        )
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _advapi32() -> Any:
    _kernel32()
    return ctypes.WinDLL("advapi32", use_last_error=True)


def _userenv() -> Any:
    _kernel32()
    return ctypes.WinDLL("userenv", use_last_error=True)


def _local_free(value: ctypes.c_void_p | wintypes.LPWSTR) -> None:
    if not value:
        return
    function = _kernel32().LocalFree
    function.argtypes = (ctypes.c_void_p,)
    function.restype = ctypes.c_void_p
    function(ctypes.cast(value, ctypes.c_void_p))


def _co_task_mem_free(value: wintypes.LPWSTR) -> None:
    if not value:
        return
    function = ctypes.WinDLL("ole32", use_last_error=True).CoTaskMemFree
    function.argtypes = (ctypes.c_void_p,)
    function.restype = None
    function(ctypes.cast(value, ctypes.c_void_p))


def _close_raw_handle(handle: int) -> bool:
    if not handle or handle == _INVALID_HANDLE_VALUE:
        return True
    close_handle = _kernel32().CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    return bool(close_handle(wintypes.HANDLE(handle)))


class _OwnedHandle(AbstractContextManager["_OwnedHandle"]):
    __slots__ = ("_value",)

    def __init__(self, value: int) -> None:
        if not value or value == _INVALID_HANDLE_VALUE:
            _reject(
                WindowsLaunchErrorCode.ISOLATION_UNAVAILABLE,
                "handle",
                "Windows did not provide a required containment handle.",
            )
        self._value = value

    @classmethod
    def empty(cls) -> Self:
        """Allocate cleanup authority before asking Windows to create a handle."""

        owner = cls.__new__(cls)
        owner._value = 0
        return owner

    @property
    def is_open(self) -> bool:
        return self._value not in {0, _INVALID_HANDLE_VALUE}

    def _adopt_valid(self, value: int) -> None:
        """Attach a prevalidated raw handle without allocation or failure."""

        self._value = value

    @property
    def value(self) -> int:
        if self._value == 0:
            _reject(
                WindowsLaunchErrorCode.CLEANUP_FAILED,
                "handle.closed",
                "A required worker handle is already closed.",
            )
        return self._value

    def detach(self) -> int:
        value = self.value
        self._value = 0
        return value

    def close(self) -> None:
        value = self._value
        if value and not _close_raw_handle(value):
            _reject(
                WindowsLaunchErrorCode.CLEANUP_FAILED,
                "handle.close",
                "Windows could not close a worker containment handle.",
            )
        self._value = 0

    def close_no_raise(self) -> bool:
        value = self._value
        if value and not _close_raw_handle(value):
            return False
        self._value = 0
        return True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


_PIPE_HANDLE_QUARANTINE_LOCK: Final = threading.Lock()
_PIPE_HANDLE_QUARANTINE: list[_OwnedHandle] = []
_RETAINED_HANDLE_QUARANTINE_LOCK: Final = threading.Lock()
_RETAINED_HANDLE_QUARANTINE: list[_OwnedHandle] = []
_ACL_MUTEX_QUARANTINE_LOCK: Final = threading.Lock()
_ACL_MUTEX_QUARANTINE: list[_AclMutationMutex] = []
_ABANDONED_ACL_MUTEX_QUARANTINE_LOCK: Final = threading.Lock()
_ABANDONED_ACL_MUTEX_QUARANTINE: list[_AclMutationMutex] = []


def _close_owned_handle_bounded(handle: _OwnedHandle) -> bool:
    for _attempt in range(_HANDLE_CLOSE_ATTEMPTS):
        if handle.close_no_raise():
            return True
    return False


def _quarantine_owned_handle(handle: _OwnedHandle) -> None:
    with _RETAINED_HANDLE_QUARANTINE_LOCK:
        if all(existing is not handle for existing in _RETAINED_HANDLE_QUARANTINE):
            _RETAINED_HANDLE_QUARANTINE.append(handle)


def _drain_retained_handle_quarantine() -> bool:
    with _RETAINED_HANDLE_QUARANTINE_LOCK:
        remaining = [
            handle
            for handle in _RETAINED_HANDLE_QUARANTINE
            if not _close_owned_handle_bounded(handle)
        ]
        _RETAINED_HANDLE_QUARANTINE[:] = remaining
        return not remaining


def _close_or_quarantine_pipe_handles(
    handles: tuple[_OwnedHandle, ...] | list[_OwnedHandle],
) -> tuple[bool, bool]:
    """Bounded-close pipe handles and de-inherit every retained failure."""

    global _PROCESS_CREATION_QUARANTINED

    secured = True
    closed = True
    unresolved: list[_OwnedHandle] = []
    for handle in handles:
        handle_closed = False
        for _attempt in range(_HANDLE_CLOSE_ATTEMPTS):
            if handle.close_no_raise():
                handle_closed = True
                break
        if handle_closed:
            continue
        closed = False
        try:
            _set_handle_inherit(handle.value, inherit=False)
        except WindowsLaunchError:
            secured = False
            _PROCESS_CREATION_QUARANTINED = True
        unresolved.append(handle)
    if unresolved:
        with _PIPE_HANDLE_QUARANTINE_LOCK:
            for handle in unresolved:
                if all(existing is not handle for existing in _PIPE_HANDLE_QUARANTINE):
                    _PIPE_HANDLE_QUARANTINE.append(handle)
    return secured, closed


def _drain_pipe_handle_quarantine() -> bool:
    with _PIPE_HANDLE_QUARANTINE_LOCK:
        remaining: list[_OwnedHandle] = []
        for handle in _PIPE_HANDLE_QUARANTINE:
            handle_closed = False
            for _attempt in range(_HANDLE_CLOSE_ATTEMPTS):
                if handle.close_no_raise():
                    handle_closed = True
                    break
            if not handle_closed:
                remaining.append(handle)
        _PIPE_HANDLE_QUARANTINE[:] = remaining
        return not remaining


def _current_process_user_sid_string() -> str:
    get_information = _advapi32().GetTokenInformation
    get_information.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    get_information.restype = wintypes.BOOL
    required = wintypes.DWORD()
    get_information(
        wintypes.HANDLE(_CURRENT_PROCESS_TOKEN),
        _TOKEN_USER_INFORMATION_CLASS,
        None,
        0,
        ctypes.byref(required),
    )
    if not required.value:
        _reject(
            WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
            "access.lock_identity",
            "Windows could not authenticate worker resource serialization.",
        )
    storage = ctypes.create_string_buffer(required.value)
    if not get_information(
        wintypes.HANDLE(_CURRENT_PROCESS_TOKEN),
        _TOKEN_USER_INFORMATION_CLASS,
        storage,
        required.value,
        ctypes.byref(required),
    ):
        _reject(
            WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
            "access.lock_identity",
            "Windows could not authenticate worker resource serialization.",
        )
    token_user = ctypes.cast(storage, ctypes.POINTER(_TokenUser)).contents
    convert = _advapi32().ConvertSidToStringSidW
    convert.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR))
    convert.restype = wintypes.BOOL
    rendered = wintypes.LPWSTR()
    if not convert(token_user.user.sid, ctypes.byref(rendered)) or rendered.value is None:
        _reject(
            WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
            "access.lock_identity",
            "Windows could not authenticate worker resource serialization.",
        )
    try:
        return rendered.value
    finally:
        _local_free(rendered)


def _acl_mutex_security_descriptor() -> ctypes.c_void_p:
    user_sid = _current_process_user_sid_string()
    convert = _advapi32().ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    )
    convert.restype = wintypes.BOOL
    descriptor = ctypes.c_void_p()
    access = f"0x{_MUTEX_ALL_ACCESS:x}"
    sddl = f"O:{user_sid}D:P(A;;{access};;;SY)(A;;{access};;;{user_sid})"
    if not convert(sddl, 1, ctypes.byref(descriptor), None) or not descriptor.value:
        _reject(
            WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
            "access.lock_policy",
            "Windows could not establish worker resource serialization policy.",
        )
    return descriptor


def _render_mutex_security_descriptor(descriptor: ctypes.c_void_p) -> str:
    convert = _advapi32().ConvertSecurityDescriptorToStringSecurityDescriptorW
    convert.argtypes = (
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(wintypes.DWORD),
    )
    convert.restype = wintypes.BOOL
    rendered = wintypes.LPWSTR()
    if (
        not convert(
            descriptor,
            1,
            _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
            ctypes.byref(rendered),
            None,
        )
        or rendered.value is None
    ):
        _reject(
            WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
            "access.lock_policy",
            "Windows could not verify worker resource serialization policy.",
        )
    try:
        return rendered.value
    finally:
        _local_free(rendered)


def _existing_mutex_has_expected_security(
    handle: int,
    expected_descriptor: ctypes.c_void_p,
) -> bool:
    get_security = _advapi32().GetSecurityInfo
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
    actual_descriptor = ctypes.c_void_p()
    status = get_security(
        wintypes.HANDLE(handle),
        _SE_KERNEL_OBJECT,
        _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
        None,
        None,
        None,
        None,
        ctypes.byref(actual_descriptor),
    )
    if status != _ERROR_SUCCESS or not actual_descriptor.value:
        return False
    try:
        return _render_mutex_security_descriptor(
            actual_descriptor
        ) == _render_mutex_security_descriptor(expected_descriptor)
    finally:
        _local_free(actual_descriptor)


def _mutex_handle_is_non_inheritable(handle: int) -> bool:
    get_information = _kernel32().GetHandleInformation
    get_information.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    get_information.restype = wintypes.BOOL
    flags = wintypes.DWORD()
    return bool(
        get_information(wintypes.HANDLE(handle), ctypes.byref(flags))
        and not flags.value & _HANDLE_FLAG_INHERIT
    )


def _quarantine_acl_mutex(mutex: _AclMutationMutex) -> None:
    with _ACL_MUTEX_QUARANTINE_LOCK:
        if all(existing is not mutex for existing in _ACL_MUTEX_QUARANTINE):
            _ACL_MUTEX_QUARANTINE.append(mutex)


def _quarantine_abandoned_acl_mutex(mutex: _AclMutationMutex) -> None:
    """Retain abandoned cross-process authority without ever releasing it."""

    with _ABANDONED_ACL_MUTEX_QUARANTINE_LOCK:
        if all(existing is not mutex for existing in _ABANDONED_ACL_MUTEX_QUARANTINE):
            _ABANDONED_ACL_MUTEX_QUARANTINE.append(mutex)


def _drain_acl_mutex_quarantine() -> bool:
    with _ACL_MUTEX_QUARANTINE_LOCK:
        remaining: list[_AclMutationMutex] = []
        clean = True
        for mutex in _ACL_MUTEX_QUARANTINE:
            if not mutex.release_no_raise():
                remaining.append(mutex)
                continue
            if not _close_owned_handle_bounded(mutex._handle):
                _quarantine_owned_handle(mutex._handle)
                clean = False
        _ACL_MUTEX_QUARANTINE[:] = remaining
        return not remaining and clean


class _AclMutationMutex(AbstractContextManager["_AclMutationMutex"]):
    """Serialize SID-specific ACL read/modify/write operations across app instances."""

    __slots__ = ("_handle", "_owned", "_owner_thread_id")

    def __init__(self) -> None:
        self._handle = _OwnedHandle.empty()
        descriptor = _acl_mutex_security_descriptor()
        attributes = _SecurityAttributes(
            length=ctypes.sizeof(_SecurityAttributes),
            security_descriptor=descriptor,
            inherit_handle=False,
        )
        create = _kernel32().CreateMutexW
        create.argtypes = (
            ctypes.POINTER(_SecurityAttributes),
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )
        create.restype = wintypes.HANDLE
        ctypes.set_last_error(_ERROR_SUCCESS)
        raw: int | None = 0
        value = 0
        already_exists = False
        try:
            try:
                try:
                    raw = create(ctypes.byref(attributes), False, _ACL_MUTATION_MUTEX_NAME)
                    already_exists = ctypes.get_last_error() == _ERROR_ALREADY_EXISTS
                finally:
                    value = int(raw or 0)
                    if value not in {0, _INVALID_HANDLE_VALUE}:
                        self._handle._adopt_valid(value)
            finally:
                _local_free(descriptor)
            if not self._handle.is_open:
                _reject(
                    WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
                    "access.lock",
                    "Windows could not serialize worker resource access changes.",
                )
            trusted = _mutex_handle_is_non_inheritable(value)
            if already_exists:
                expected = _acl_mutex_security_descriptor()
                try:
                    trusted = trusted and _existing_mutex_has_expected_security(
                        value,
                        expected,
                    )
                finally:
                    _local_free(expected)
        except BaseException:
            value = int(raw or 0)
            if value not in {0, _INVALID_HANDLE_VALUE} and not self._handle.is_open:
                self._handle._adopt_valid(value)
            if not _close_owned_handle_bounded(self._handle):
                _quarantine_owned_handle(self._handle)
                raise WindowsLaunchError(
                    WindowsLaunchErrorCode.CLEANUP_FAILED,
                    "access.lock_cleanup",
                    "Windows could not clean up worker resource access serialization.",
                ) from None
            raise
        if not trusted:
            if not _close_owned_handle_bounded(self._handle):
                _quarantine_owned_handle(self._handle)
                _reject(
                    WindowsLaunchErrorCode.CLEANUP_FAILED,
                    "access.lock_cleanup",
                    "Windows could not clean up worker resource access serialization.",
                )
            _reject(
                WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
                "access.lock_policy",
                "Windows rejected untrusted worker resource serialization.",
            )
        self._owned = False
        self._owner_thread_id: int | None = None

    def __enter__(self) -> Self:
        global _PROCESS_CREATION_QUARANTINED

        wait = _kernel32().WaitForSingleObject
        wait.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        wait.restype = wintypes.DWORD
        result = int(
            wait(
                wintypes.HANDLE(self._handle.value),
                _ACL_MUTATION_WAIT_MILLISECONDS,
            )
        )
        if result == _WAIT_ABANDONED:
            self._owned = True
            self._owner_thread_id = threading.get_ident()
            _PROCESS_CREATION_QUARANTINED = True
            _quarantine_abandoned_acl_mutex(self)
            _reject(
                WindowsLaunchErrorCode.CLEANUP_FAILED,
                "access.lock_abandoned",
                "Worker resource access serialization requires maintenance after an interrupted update.",
            )
        if result != _WAIT_OBJECT_0:
            if not _close_owned_handle_bounded(self._handle):
                _quarantine_owned_handle(self._handle)
                _reject(
                    WindowsLaunchErrorCode.CLEANUP_FAILED,
                    "access.lock_cleanup",
                    "Windows could not clean up worker resource access serialization.",
                )
            _reject(
                WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
                "access.lock",
                "Windows could not serialize worker resource access changes.",
            )
        self._owned = True
        self._owner_thread_id = threading.get_ident()
        return self

    def release_no_raise(self) -> bool:
        if not self._owned:
            return True
        if self._owner_thread_id != threading.get_ident():
            return False
        release = _kernel32().ReleaseMutex
        release.argtypes = (wintypes.HANDLE,)
        release.restype = wintypes.BOOL
        for _attempt in range(_HANDLE_CLOSE_ATTEMPTS):
            if release(wintypes.HANDLE(self._handle.value)):
                self._owned = False
                self._owner_thread_id = None
                return True
        return False

    def __exit__(self, *_exc: object) -> None:
        if not self.release_no_raise():
            _quarantine_acl_mutex(self)
            _reject(
                WindowsLaunchErrorCode.CLEANUP_FAILED,
                "access.lock_release",
                "Windows could not release worker resource access serialization.",
            )
        if not _close_owned_handle_bounded(self._handle):
            _quarantine_owned_handle(self._handle)
            _reject(
                WindowsLaunchErrorCode.CLEANUP_FAILED,
                "access.lock_cleanup",
                "Windows could not clean up worker resource access serialization.",
            )


@dataclass(frozen=True, slots=True, repr=False)
class WindowsLaunchConfig:
    """Trusted, fixed resources from which every worker command is assembled."""

    python_executable: Path
    bootstrap_script: Path
    runtime_roots: tuple[Path, ...]
    code_roots: tuple[Path, ...]
    working_directory: Path

    def __post_init__(self) -> None:
        paths = (
            self.python_executable,
            self.bootstrap_script,
            self.working_directory,
            *self.runtime_roots,
            *self.code_roots,
        )
        if (
            not self.runtime_roots
            or not self.code_roots
            or any(not isinstance(path, Path) or not path.is_absolute() for path in paths)
            or any("\x00" in os.fspath(path) for path in paths)
        ):
            _reject(
                WindowsLaunchErrorCode.CONFIGURATION_INVALID,
                "config.paths",
                "Worker resources must be fixed absolute local paths.",
            )
        if any(_is_remote_or_device_path(path) for path in paths):
            _reject(
                WindowsLaunchErrorCode.CONFIGURATION_INVALID,
                "config.location",
                "Worker resources must be on a supported local Windows path.",
            )
        try:
            valid_types = (
                self.python_executable.is_file()
                and self.bootstrap_script.is_file()
                and self.working_directory.is_dir()
                and all(path.is_dir() for path in (*self.runtime_roots, *self.code_roots))
            )
        except OSError:
            valid_types = False
        if not valid_types:
            _reject(
                WindowsLaunchErrorCode.CONFIGURATION_INVALID,
                "config.resources",
                "An approved worker runtime resource is unavailable.",
            )
        for path in paths:
            _require_no_reparse_local_path(path)
        if not any(_is_within(self.python_executable, root) for root in self.runtime_roots):
            _reject(
                WindowsLaunchErrorCode.CONFIGURATION_INVALID,
                "config.executable",
                "The worker executable is outside the approved runtime roots.",
            )
        if not any(_is_within(self.bootstrap_script, root) for root in self.code_roots):
            _reject(
                WindowsLaunchErrorCode.CONFIGURATION_INVALID,
                "config.bootstrap",
                "The worker bootstrap is outside the approved code roots.",
            )
        if not any(
            _is_within(self.working_directory, root)
            for root in (*self.runtime_roots, *self.code_roots)
        ):
            _reject(
                WindowsLaunchErrorCode.CONFIGURATION_INVALID,
                "config.working_directory",
                "The worker working directory is outside approved resources.",
            )

    @property
    def access_roots(self) -> tuple[Path, ...]:
        return self.runtime_roots + self.code_roots

    @property
    def fixed_argv(self) -> tuple[str, ...]:
        return (
            os.fspath(self.python_executable),
            "-I",
            "-S",
            "-B",
            "-X",
            "utf8",
            os.fspath(self.bootstrap_script),
        )

    def __repr__(self) -> str:
        return "WindowsLaunchConfig(<approved fixed resources>)"


def _is_remote_or_device_path(path: Path) -> bool:
    rendered = os.fspath(path)
    return (
        rendered.startswith("\\\\")
        or rendered.startswith("\\\\?\\")
        or rendered.startswith("\\\\.\\")
        or rendered.startswith("\\??\\")
    )


def _normalized_path(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _is_within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath(
            (_normalized_path(path), _normalized_path(root))
        ) == _normalized_path(root)
    except ValueError:
        return False


def _require_no_reparse_local_path(path: Path) -> None:
    rendered = _normalized_path(path)
    drive, _tail = os.path.splitdrive(rendered)
    if not drive:
        _reject(
            WindowsLaunchErrorCode.CONFIGURATION_INVALID,
            "config.location",
            "Worker resources must be on a supported fixed local drive.",
        )
    get_drive_type = _kernel32().GetDriveTypeW
    get_drive_type.argtypes = (wintypes.LPCWSTR,)
    get_drive_type.restype = wintypes.UINT
    if get_drive_type(f"{drive}\\") != _DRIVE_FIXED:
        _reject(
            WindowsLaunchErrorCode.CONFIGURATION_INVALID,
            "config.location",
            "Worker resources must be on a supported fixed local drive.",
        )
    get_attributes = _kernel32().GetFileAttributesW
    get_attributes.argtypes = (wintypes.LPCWSTR,)
    get_attributes.restype = wintypes.DWORD
    current = Path(f"{drive}\\")
    relative = Path(rendered).relative_to(current)
    for component in relative.parts:
        current /= component
        attributes = int(get_attributes(os.fspath(current)))
        if attributes == _INVALID_FILE_ATTRIBUTES:
            _reject(
                WindowsLaunchErrorCode.CONFIGURATION_INVALID,
                "config.resources",
                "An approved worker runtime resource is unavailable.",
            )
        if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            _reject(
                WindowsLaunchErrorCode.CONFIGURATION_INVALID,
                "config.reparse",
                "Linked or redirected worker runtime resources are not supported.",
            )


@dataclass(slots=True, repr=False)
class _AccessGrantLease:
    sid: ctypes.c_void_p = field(repr=False)
    paths: list[Path] = field(default_factory=list, repr=False)
    observed_objects: list[Path] = field(default_factory=list, repr=False)
    original_auto_inherited: dict[Path, bool] = field(default_factory=dict, repr=False)

    def grant_all(self, paths: tuple[Path, ...]) -> None:
        discovered: list[Path] = []
        for path in paths:
            for descendant in _approved_resource_tree(path):
                if descendant not in discovered:
                    discovered.append(descendant)
        with _AclMutationMutex():
            captured: dict[Path, bool] = {}
            for descendant in discovered:
                captured[descendant] = _dacl_auto_inherited(descendant)
            for descendant, expected in captured.items():
                self.original_auto_inherited.setdefault(descendant, expected)
                if descendant not in self.observed_objects:
                    self.observed_objects.append(descendant)
            for path in paths:
                if path in self.paths:
                    continue
                self.paths.append(path)
                _change_sid_access(path, self.sid, _GRANT_ACCESS)
            for descendant in discovered:
                _restore_dacl_auto_inherited(
                    descendant,
                    self.original_auto_inherited[descendant],
                )

    def require_read_execute(self, paths: tuple[Path, ...]) -> None:
        for path in paths:
            mask = _sid_access_mask(path, self.sid)
            if mask & _FILE_GENERIC_READ_EXECUTE != _FILE_GENERIC_READ_EXECUTE:
                _reject(
                    WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
                    "access.verify",
                    "Windows could not verify worker access to an approved resource.",
                )
            if path not in self.observed_objects:
                self.observed_objects.append(path)

    def close_no_raise(self) -> bool:
        success = True
        try:
            with _AclMutationMutex():
                for path in reversed(self.paths):
                    try:
                        _change_sid_access(path, self.sid, _REVOKE_ACCESS)
                    except WindowsLaunchError:
                        success = False
                for path in self.observed_objects:
                    try:
                        expected = self.original_auto_inherited[path]
                        _restore_dacl_auto_inherited(path, expected)
                        if _sid_access_mask(path, self.sid):
                            success = False
                    except KeyError, WindowsLaunchError:
                        success = False
        except WindowsLaunchError:
            success = False
        if success:
            self.paths.clear()
            self.observed_objects.clear()
            self.original_auto_inherited.clear()
        return success


def _capture_security_descriptor(path: Path) -> bytes:
    advapi32 = _advapi32()
    get_security_info = advapi32.GetNamedSecurityInfoW
    get_security_info.argtypes = (
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    )
    get_security_info.restype = wintypes.DWORD
    get_length = advapi32.GetSecurityDescriptorLength
    get_length.argtypes = (ctypes.c_void_p,)
    get_length.restype = wintypes.DWORD
    descriptor = ctypes.c_void_p()
    mutable_path = ctypes.create_unicode_buffer(os.fspath(path))
    status = get_security_info(
        mutable_path,
        _SE_FILE_OBJECT,
        _DACL_SECURITY_INFORMATION,
        None,
        None,
        None,
        None,
        ctypes.byref(descriptor),
    )
    if status != _ERROR_SUCCESS or not descriptor.value:
        _reject(
            WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
            "access.inspect",
            "Windows could not inspect an approved worker resource policy.",
        )
    try:
        size = int(get_length(descriptor))
        if size < 20 or size > 65_536:
            _reject(
                WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
                "access.inspect",
                "Windows could not inspect an approved worker resource policy.",
            )
        return ctypes.string_at(descriptor, size)
    finally:
        _local_free(descriptor)


def _descriptor_control(descriptor: bytes | bytearray) -> int:
    if len(descriptor) < 20:
        _reject(
            WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
            "access.inspect",
            "Windows could not inspect an approved worker resource policy.",
        )
    control = int.from_bytes(descriptor[2:4], "little")
    if not control & _SE_SELF_RELATIVE:
        _reject(
            WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
            "access.inspect",
            "Windows could not inspect an approved worker resource policy.",
        )
    return control


def _require_non_null_dacl(descriptor: bytes | bytearray) -> None:
    control = _descriptor_control(descriptor)
    if not control & _SE_DACL_PRESENT or int.from_bytes(descriptor[16:20], "little") == 0:
        _reject(
            WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
            "access.inspect",
            "An approved worker resource has an unsupported access policy.",
        )


def _dacl_auto_inherited(path: Path) -> bool:
    descriptor = _capture_security_descriptor(path)
    _require_non_null_dacl(descriptor)
    return bool(_descriptor_control(descriptor) & _SE_DACL_AUTO_INHERITED)


def _restore_dacl_auto_inherited(path: Path, expected: bool) -> None:
    descriptor = bytearray(_capture_security_descriptor(path))
    _require_non_null_dacl(descriptor)
    control = _descriptor_control(descriptor)
    desired = control | _SE_DACL_AUTO_INHERITED if expected else control & ~_SE_DACL_AUTO_INHERITED
    if desired == control:
        return
    descriptor[2:4] = desired.to_bytes(2, "little")
    set_security = _advapi32().SetFileSecurityW
    set_security.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p)
    set_security.restype = wintypes.BOOL
    storage = ctypes.create_string_buffer(bytes(descriptor))
    if (
        not set_security(
            os.fspath(path),
            _DACL_SECURITY_INFORMATION,
            ctypes.cast(storage, ctypes.c_void_p),
        )
        or _dacl_auto_inherited(path) is not expected
    ):
        _reject(
            WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
            "access.restore",
            "Windows could not restore an approved worker resource policy.",
        )


def _approved_resource_tree(root: Path) -> tuple[Path, ...]:
    discovered: list[Path] = []
    pending: list[tuple[Path, int]] = [(root, 0)]
    while pending:
        current, depth = pending.pop()
        if depth > _MAX_APPROVED_RESOURCE_DEPTH:
            _reject(
                WindowsLaunchErrorCode.CONFIGURATION_INVALID,
                "config.resource_depth",
                "The approved worker resource tree exceeds its safe depth.",
            )
        _require_no_reparse_local_path(current)
        discovered.append(current)
        if len(discovered) > _MAX_APPROVED_RESOURCE_ENTRIES:
            _reject(
                WindowsLaunchErrorCode.CONFIGURATION_INVALID,
                "config.resource_entries",
                "The approved worker resource tree exceeds its safe entry limit.",
            )
        is_directory: bool | None = None
        try:
            is_directory = current.is_dir()
        except OSError:
            pass
        if is_directory is None:
            _reject(
                WindowsLaunchErrorCode.CONFIGURATION_INVALID,
                "config.resources",
                "An approved worker runtime resource is unavailable.",
            )
        if not is_directory:
            continue
        children: list[Path] | None = None
        try:
            children = sorted(current.iterdir(), key=lambda child: child.name.casefold())
        except OSError:
            pass
        if children is None:
            _reject(
                WindowsLaunchErrorCode.CONFIGURATION_INVALID,
                "config.resources",
                "An approved worker runtime resource is unavailable.",
            )
        pending.extend((child, depth + 1) for child in reversed(children))
    return tuple(discovered)


def _change_sid_access(path: Path, sid: ctypes.c_void_p, mode: int) -> None:
    advapi32 = _advapi32()
    get_security_info = advapi32.GetNamedSecurityInfoW
    get_security_info.argtypes = (
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    )
    get_security_info.restype = wintypes.DWORD
    set_entries = advapi32.SetEntriesInAclW
    set_entries.argtypes = (
        wintypes.ULONG,
        ctypes.POINTER(_ExplicitAccessW),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    )
    set_entries.restype = wintypes.DWORD
    set_security_info = advapi32.SetNamedSecurityInfoW
    set_security_info.argtypes = (
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )
    set_security_info.restype = wintypes.DWORD

    descriptor = ctypes.c_void_p()
    old_dacl = ctypes.c_void_p()
    new_dacl = ctypes.c_void_p()
    mutable_path = ctypes.create_unicode_buffer(os.fspath(path))
    status = get_security_info(
        mutable_path,
        _SE_FILE_OBJECT,
        _DACL_SECURITY_INFORMATION,
        None,
        None,
        ctypes.byref(old_dacl),
        None,
        ctypes.byref(descriptor),
    )
    if status != _ERROR_SUCCESS or not descriptor.value or not old_dacl.value:
        _reject(
            WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
            "access.read",
            "Windows could not authenticate an approved worker resource policy.",
        )
    try:
        entry = _ExplicitAccessW(
            access_permissions=_FILE_GENERIC_READ_EXECUTE,
            access_mode=mode,
            inheritance=_SUB_CONTAINERS_AND_OBJECTS_INHERIT,
            trustee=_TrusteeW(
                multiple_trustee=None,
                multiple_trustee_operation=0,
                trustee_form=_TRUSTEE_IS_SID,
                trustee_type=_TRUSTEE_IS_UNKNOWN,
                name=ctypes.cast(sid, wintypes.LPWSTR),
            ),
        )
        status = set_entries(1, ctypes.byref(entry), old_dacl, ctypes.byref(new_dacl))
        if status != _ERROR_SUCCESS or not new_dacl.value:
            _reject(
                WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
                "access.build",
                "Windows could not construct the approved worker resource policy.",
            )
        status = set_security_info(
            mutable_path,
            _SE_FILE_OBJECT,
            _DACL_SECURITY_INFORMATION,
            None,
            None,
            new_dacl,
            None,
        )
        if status != _ERROR_SUCCESS:
            _reject(
                WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
                "access.apply",
                "Windows could not apply the approved worker resource policy.",
            )
    finally:
        _local_free(new_dacl)
        _local_free(descriptor)


def _sid_access_mask(path: Path, sid: ctypes.c_void_p) -> int:
    advapi32 = _advapi32()
    get_security_info = advapi32.GetNamedSecurityInfoW
    get_security_info.argtypes = (
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    )
    get_security_info.restype = wintypes.DWORD
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

    descriptor = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    mutable_path = ctypes.create_unicode_buffer(os.fspath(path))
    status = get_security_info(
        mutable_path,
        _SE_FILE_OBJECT,
        _DACL_SECURITY_INFORMATION,
        None,
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if status != _ERROR_SUCCESS or not descriptor.value or not dacl.value:
        _reject(
            WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
            "access.inspect",
            "Windows could not inspect an approved worker resource policy.",
        )
    try:
        information = _AclSizeInformation()
        if not get_acl_information(
            dacl,
            ctypes.byref(information),
            ctypes.sizeof(information),
            _ACL_SIZE_INFORMATION_CLASS,
        ):
            _reject(
                WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
                "access.inspect",
                "Windows could not inspect an approved worker resource policy.",
            )
        mask = 0
        for index in range(int(information.ace_count)):
            pointer = ctypes.c_void_p()
            if not get_ace(dacl, index, ctypes.byref(pointer)) or not pointer.value:
                _reject(
                    WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
                    "access.inspect",
                    "Windows could not inspect an approved worker resource policy.",
                )
            address = pointer.value
            if address is None:
                _reject(
                    WindowsLaunchErrorCode.ACCESS_GRANT_FAILED,
                    "access.inspect",
                    "Windows could not inspect an approved worker resource policy.",
                )
            ace = _AccessAllowedAce.from_address(address)
            if int(ace.header.ace_type) != _ACCESS_ALLOWED_ACE_TYPE:
                continue
            ace_sid = ctypes.c_void_p(address + _AccessAllowedAce.sid_start.offset)
            if equal_sid(ace_sid, sid):
                mask |= int(ace.mask)
        return mask
    finally:
        _local_free(descriptor)


@dataclass(slots=True, repr=False)
class _AppContainerProfile:
    name: str = field(repr=False)
    sid: ctypes.c_void_p = field(repr=False)
    local_app_data: Path = field(repr=False)
    access: _AccessGrantLease = field(repr=False)
    _deleted: bool = False
    _closed: bool = False

    @classmethod
    def create(cls) -> Self:
        name = f"{_PROFILE_PREFIX}{token_hex(12)}"
        if len(name) > _MAX_PROFILE_NAME_CODEPOINTS:
            _reject(
                WindowsLaunchErrorCode.PROFILE_FAILED,
                "profile.name",
                "The worker profile identifier is invalid.",
            )
        create_profile = _userenv().CreateAppContainerProfile
        create_profile.argtypes = (
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            ctypes.POINTER(_SidAndAttributes),
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
        )
        create_profile.restype = ctypes.c_long
        sid = ctypes.c_void_p()
        result = create_profile(
            name,
            _PROFILE_DISPLAY_NAME,
            _PROFILE_DESCRIPTION,
            None,
            0,
            ctypes.byref(sid),
        )
        if result != 0 or not sid.value:
            _reject(
                WindowsLaunchErrorCode.PROFILE_FAILED,
                "profile.create",
                "Windows could not create an isolated worker profile.",
            )
        try:
            local_app_data = _app_container_folder_path(sid)
            _prepare_app_container_temp(local_app_data, sid)
        except BaseException:
            partial = _PartialAppContainerProfile(name=name, sid=sid)
            if not _close_profile_bounded(partial):
                _quarantine_profile(partial, cleanup_allowed=True)
                raise WindowsLaunchError(
                    WindowsLaunchErrorCode.CLEANUP_FAILED,
                    "profile.partial_cleanup",
                    "PaperWorkShell could not clean up an incomplete worker profile.",
                ) from None
            raise
        return cls(
            name=name,
            sid=sid,
            local_app_data=local_app_data,
            access=_AccessGrantLease(sid=sid),
        )

    def grant_read_execute(self, paths: tuple[Path, ...]) -> None:
        self.access.grant_all(paths)

    def require_read_execute(self, paths: tuple[Path, ...]) -> None:
        self.access.require_read_execute(paths)

    def close_no_raise(self) -> bool:
        if self._closed:
            return True
        if not self.access.close_no_raise():
            return False
        if not self._deleted:
            delete_profile = _userenv().DeleteAppContainerProfile
            delete_profile.argtypes = (wintypes.LPCWSTR,)
            delete_profile.restype = ctypes.c_long
            for attempt in range(_PROFILE_DELETE_ATTEMPTS):
                if delete_profile(self.name) == 0:
                    self._deleted = True
                    break
                if attempt + 1 < _PROFILE_DELETE_ATTEMPTS:
                    time.sleep(_PROFILE_DELETE_RETRY_SECONDS)
            if not self._deleted:
                return False
        free_sid = _advapi32().FreeSid
        free_sid.argtypes = (ctypes.c_void_p,)
        free_sid.restype = ctypes.c_void_p
        if free_sid(self.sid):
            return False
        self.sid = ctypes.c_void_p()
        self._closed = True
        return True


@dataclass(slots=True, repr=False)
class _PartialAppContainerProfile:
    """Cleanup authority retained when profile-folder resolution fails."""

    name: str = field(repr=False)
    sid: ctypes.c_void_p = field(repr=False)
    _deleted: bool = False
    _closed: bool = False

    def close_no_raise(self) -> bool:
        if self._closed:
            return True
        if not self._deleted:
            delete_profile = _userenv().DeleteAppContainerProfile
            delete_profile.argtypes = (wintypes.LPCWSTR,)
            delete_profile.restype = ctypes.c_long
            for attempt in range(_PROFILE_DELETE_ATTEMPTS):
                if delete_profile(self.name) == 0:
                    self._deleted = True
                    break
                if attempt + 1 < _PROFILE_DELETE_ATTEMPTS:
                    time.sleep(_PROFILE_DELETE_RETRY_SECONDS)
            if not self._deleted:
                return False
        free_sid = _advapi32().FreeSid
        free_sid.argtypes = (ctypes.c_void_p,)
        free_sid.restype = ctypes.c_void_p
        if free_sid(self.sid):
            return False
        self.sid = ctypes.c_void_p()
        self._closed = True
        return True


type _ProfileCleanupAuthority = _AppContainerProfile | _PartialAppContainerProfile


@dataclass(slots=True, repr=False)
class _ProfileQuarantineEntry:
    profile: _ProfileCleanupAuthority = field(repr=False)
    cleanup_allowed: bool


_PROFILE_QUARANTINE_LOCK: Final = threading.Lock()
_PROFILE_QUARANTINE: list[_ProfileQuarantineEntry] = []


def _close_profile_bounded(profile: _ProfileCleanupAuthority) -> bool:
    for attempt in range(_PROFILE_CLEANUP_ATTEMPTS):
        if profile.close_no_raise():
            return True
        if attempt + 1 < _PROFILE_CLEANUP_ATTEMPTS:
            time.sleep(_PROFILE_DELETE_RETRY_SECONDS)
    return False


def _quarantine_profile(
    profile: _ProfileCleanupAuthority,
    *,
    cleanup_allowed: bool,
) -> None:
    with _PROFILE_QUARANTINE_LOCK:
        if any(entry.profile is profile for entry in _PROFILE_QUARANTINE):
            return
        _PROFILE_QUARANTINE.append(_ProfileQuarantineEntry(profile, cleanup_allowed))


def _drain_profile_quarantine() -> bool:
    with _PROFILE_QUARANTINE_LOCK:
        remaining: list[_ProfileQuarantineEntry] = []
        for entry in _PROFILE_QUARANTINE:
            if not entry.cleanup_allowed or not _close_profile_bounded(entry.profile):
                remaining.append(entry)
        _PROFILE_QUARANTINE[:] = remaining
        return not remaining


def _app_container_folder_path(sid: ctypes.c_void_p) -> Path:
    convert_sid = _advapi32().ConvertSidToStringSidW
    convert_sid.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR))
    convert_sid.restype = wintypes.BOOL
    rendered = wintypes.LPWSTR()
    if not convert_sid(sid, ctypes.byref(rendered)) or rendered.value is None:
        _reject(
            WindowsLaunchErrorCode.PROFILE_FAILED,
            "profile.identity",
            "Windows could not authenticate the isolated worker profile.",
        )
    folder = wintypes.LPWSTR()
    try:
        get_folder = _userenv().GetAppContainerFolderPath
        get_folder.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.LPWSTR))
        get_folder.restype = ctypes.c_long
        if get_folder(rendered.value, ctypes.byref(folder)) != 0 or folder.value is None:
            _reject(
                WindowsLaunchErrorCode.PROFILE_FAILED,
                "profile.folder",
                "Windows could not resolve the isolated worker profile.",
            )
        return Path(folder.value)
    finally:
        _co_task_mem_free(folder)
        _local_free(rendered)


def _prepare_app_container_temp(local_app_data: Path, sid: ctypes.c_void_p) -> None:
    """Provision and verify one reparse-free, profile-private writable temp root."""

    if (
        not local_app_data.is_absolute()
        or _is_remote_or_device_path(local_app_data)
        or not local_app_data.is_dir()
    ):
        _reject(
            WindowsLaunchErrorCode.PROFILE_FAILED,
            "profile.temp_root",
            "Windows did not create the isolated worker profile storage.",
        )
    _require_no_reparse_local_path(local_app_data)
    temp = local_app_data / "Temp"
    try:
        temp.mkdir(mode=0o700, parents=False, exist_ok=True)
    except OSError:
        _reject(
            WindowsLaunchErrorCode.PROFILE_FAILED,
            "profile.temp_create",
            "Windows could not provision private worker temporary storage.",
        )
    if not temp.is_dir() or not _is_within(temp, local_app_data):
        _reject(
            WindowsLaunchErrorCode.PROFILE_FAILED,
            "profile.temp_identity",
            "Windows could not authenticate private worker temporary storage.",
        )
    _require_no_reparse_local_path(temp)
    if _sid_access_mask(temp, sid) & _PRIVATE_TEMP_ACCESS != _PRIVATE_TEMP_ACCESS:
        _reject(
            WindowsLaunchErrorCode.PROFILE_FAILED,
            "profile.temp_access",
            "Windows could not verify private worker temporary-storage access.",
        )


@dataclass(slots=True, repr=False)
class _PipeSet:
    client_read: _OwnedHandle
    client_write: _OwnedHandle
    child_read: _OwnedHandle
    child_write: _OwnedHandle
    bootstrap_read: _OwnedHandle
    bootstrap_write: _OwnedHandle

    @classmethod
    def create(cls) -> Self:
        handles: list[_OwnedHandle] = []
        try:
            child_read, client_write = _create_pipe(child_reads=True)
            handles.extend((child_read, client_write))
            client_read, child_write = _create_pipe(child_reads=False)
            handles.extend((client_read, child_write))
            bootstrap_read, bootstrap_write = _create_pipe(child_reads=True)
            handles.extend((bootstrap_read, bootstrap_write))
            return cls(
                client_read=client_read,
                client_write=client_write,
                child_read=child_read,
                child_write=child_write,
                bootstrap_read=bootstrap_read,
                bootstrap_write=bootstrap_write,
            )
        except BaseException:
            secured, closed = _close_or_quarantine_pipe_handles(handles)
            if not secured or not closed:
                raise WindowsLaunchError(
                    WindowsLaunchErrorCode.CLEANUP_FAILED,
                    "pipe.partial_cleanup",
                    "Windows could not clean up an incomplete pipe set.",
                ) from None
            raise

    @property
    def inherited_handles(self) -> tuple[int, int, int]:
        return self.child_read.value, self.child_write.value, self.bootstrap_read.value

    def close_child_ends_no_raise(self) -> bool:
        secured, closed = self.secure_child_ends_no_raise()
        return secured and closed

    def secure_child_ends_no_raise(self) -> tuple[bool, bool]:
        """Close inherited ends, or at minimum clear inheritance before unlock."""

        return _close_or_quarantine_pipe_handles(
            (self.child_read, self.child_write, self.bootstrap_read)
        )

    def close_no_raise(self) -> bool:
        secured, child_handles_closed = self.secure_child_ends_no_raise()
        success = secured and child_handles_closed
        for handle in (
            self.client_read,
            self.client_write,
            self.bootstrap_write,
        ):
            if not _close_owned_handle_bounded(handle):
                _quarantine_owned_handle(handle)
                success = False
        return success


def _create_pipe(*, child_reads: bool) -> tuple[_OwnedHandle, _OwnedHandle]:
    create_pipe = _kernel32().CreatePipe
    create_pipe.argtypes = (
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(_SecurityAttributes),
        wintypes.DWORD,
    )
    create_pipe.restype = wintypes.BOOL
    attributes = _SecurityAttributes(
        length=ctypes.sizeof(_SecurityAttributes),
        security_descriptor=None,
        inherit_handle=True,
    )
    read_handle = wintypes.HANDLE()
    write_handle = wintypes.HANDLE()
    read_owned = _OwnedHandle.empty()
    write_owned = _OwnedHandle.empty()
    created = False
    try:
        try:
            created = bool(
                create_pipe(
                    ctypes.byref(read_handle),
                    ctypes.byref(write_handle),
                    ctypes.byref(attributes),
                    0,
                )
            )
        finally:
            read_value = int(read_handle.value or 0)
            write_value = int(write_handle.value or 0)
            if read_value not in {0, _INVALID_HANDLE_VALUE}:
                read_owned._adopt_valid(read_value)
            if write_value not in {0, _INVALID_HANDLE_VALUE}:
                write_owned._adopt_valid(write_value)
        if not created:
            _reject(
                WindowsLaunchErrorCode.PIPE_FAILED,
                "pipe.create",
                "Windows could not create a bounded worker transport.",
            )
        if not read_owned.is_open or not write_owned.is_open:
            _reject(
                WindowsLaunchErrorCode.PIPE_FAILED,
                "pipe.handles",
                "Windows returned an incomplete worker transport.",
            )
        parent_handle = write_owned if child_reads else read_owned
        _set_handle_inherit(parent_handle.value, inherit=False)
        return read_owned, write_owned
    except BaseException:
        read_value = int(read_handle.value or 0)
        write_value = int(write_handle.value or 0)
        if read_value not in {0, _INVALID_HANDLE_VALUE} and not read_owned.is_open:
            read_owned._adopt_valid(read_value)
        if write_value not in {0, _INVALID_HANDLE_VALUE} and not write_owned.is_open:
            write_owned._adopt_valid(write_value)
        secured, closed = _close_or_quarantine_pipe_handles((read_owned, write_owned))
        if not secured or not closed:
            raise WindowsLaunchError(
                WindowsLaunchErrorCode.CLEANUP_FAILED,
                "pipe.partial_cleanup",
                "Windows could not clean up an incomplete pipe pair.",
            ) from None
        raise


def _set_handle_inherit(handle: int, *, inherit: bool) -> None:
    set_information = _kernel32().SetHandleInformation
    set_information.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD)
    set_information.restype = wintypes.BOOL
    flags = _HANDLE_FLAG_INHERIT if inherit else 0
    if not set_information(wintypes.HANDLE(handle), _HANDLE_FLAG_INHERIT, flags):
        _reject(
            WindowsLaunchErrorCode.PIPE_FAILED,
            "pipe.inheritance",
            "Windows could not restrict worker handle inheritance.",
        )


class _AttributeList(AbstractContextManager["_AttributeList"]):
    __slots__ = ("_buffer", "_pointer", "_handle_array", "_security_capabilities")

    def __init__(self, sid: ctypes.c_void_p, inherited_handles: tuple[int, int, int]) -> None:
        initialize = _kernel32().InitializeProcThreadAttributeList
        initialize.argtypes = (
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_size_t),
        )
        initialize.restype = wintypes.BOOL
        size = ctypes.c_size_t()
        initialize(None, 2, 0, ctypes.byref(size))
        if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER or not size.value:
            _reject(
                WindowsLaunchErrorCode.ISOLATION_UNAVAILABLE,
                "attributes.size",
                "Windows process containment attributes are unavailable.",
            )
        self._buffer = ctypes.create_string_buffer(size.value)
        self._pointer = ctypes.cast(self._buffer, ctypes.c_void_p)
        if not initialize(self._pointer, 2, 0, ctypes.byref(size)):
            _reject(
                WindowsLaunchErrorCode.ISOLATION_UNAVAILABLE,
                "attributes.initialize",
                "Windows process containment attributes are unavailable.",
            )
        handle_array_type = wintypes.HANDLE * len(inherited_handles)
        self._handle_array = handle_array_type(
            *(wintypes.HANDLE(value) for value in inherited_handles)
        )
        self._security_capabilities = _SecurityCapabilities(
            app_container_sid=sid,
            capabilities=None,
            capability_count=0,
            reserved=0,
        )
        self._update(
            _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
            ctypes.cast(self._handle_array, ctypes.c_void_p),
            ctypes.sizeof(self._handle_array),
        )
        self._update(
            _PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
            ctypes.cast(ctypes.byref(self._security_capabilities), ctypes.c_void_p),
            ctypes.sizeof(self._security_capabilities),
        )

    @property
    def pointer(self) -> ctypes.c_void_p:
        return self._pointer

    def _update(self, attribute: int, value: ctypes.c_void_p, size: int) -> None:
        update = _kernel32().UpdateProcThreadAttribute
        update.argtypes = (
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t),
        )
        update.restype = wintypes.BOOL
        if not update(self._pointer, 0, attribute, value, size, None, None):
            self.close_no_raise()
            _reject(
                WindowsLaunchErrorCode.ISOLATION_UNAVAILABLE,
                "attributes.update",
                "Windows could not apply the required process containment attributes.",
            )

    def close_no_raise(self) -> None:
        if not getattr(self, "_pointer", None):
            return
        delete = _kernel32().DeleteProcThreadAttributeList
        delete.argtypes = (ctypes.c_void_p,)
        delete.restype = None
        delete(self._pointer)
        self._pointer = ctypes.c_void_p()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close_no_raise()


def _validate_windows_resource_policy(policy: WorkerResourcePolicy) -> None:
    if policy.cpu_time_ms > _MAX_WINDOWS_CPU_TIME_MS:
        _reject(
            WindowsLaunchErrorCode.CONFIGURATION_INVALID,
            "resource_policy.cpu_time",
            "The worker CPU limit cannot be represented safely on Windows.",
        )
    if (
        policy.process_memory_bytes > _MAX_WINDOWS_SIZE_T
        or policy.job_memory_bytes > _MAX_WINDOWS_SIZE_T
    ):
        _reject(
            WindowsLaunchErrorCode.CONFIGURATION_INVALID,
            "resource_policy.memory",
            "The worker memory limits cannot be represented safely on Windows.",
        )


class _Job(AbstractContextManager["_Job"]):
    __slots__ = ("handle",)

    def __init__(self, policy: WorkerResourcePolicy) -> None:
        _validate_windows_resource_policy(policy)
        self.handle = _OwnedHandle.empty()
        create_job = _kernel32().CreateJobObjectW
        create_job.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        create_job.restype = wintypes.HANDLE
        raw: int | None = 0
        try:
            try:
                raw = create_job(None, None)
            finally:
                value = int(raw or 0)
                if value not in {0, _INVALID_HANDLE_VALUE}:
                    self.handle._adopt_valid(value)
            if not self.handle.is_open:
                _reject(
                    WindowsLaunchErrorCode.JOB_FAILED,
                    "job.create",
                    "Windows could not create the worker containment job.",
                )
            limits = _JobObjectExtendedLimitInformation()
            limits.basic_limit_information.per_process_user_time_limit = policy.cpu_time_ms * 10_000
            limits.basic_limit_information.per_job_user_time_limit = policy.cpu_time_ms * 10_000
            limits.basic_limit_information.active_process_limit = policy.maximum_processes
            limits.basic_limit_information.limit_flags = (
                _JOB_OBJECT_LIMIT_PROCESS_TIME
                | _JOB_OBJECT_LIMIT_JOB_TIME
                | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
                | _JOB_OBJECT_LIMIT_PROCESS_MEMORY
                | _JOB_OBJECT_LIMIT_JOB_MEMORY
                | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            limits.process_memory_limit = policy.process_memory_bytes
            limits.job_memory_limit = policy.job_memory_bytes
            set_information = _kernel32().SetInformationJobObject
            set_information.argtypes = (
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
            )
            set_information.restype = wintypes.BOOL
            if not set_information(
                wintypes.HANDLE(self.handle.value),
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            ):
                _reject(
                    WindowsLaunchErrorCode.JOB_FAILED,
                    "job.limits",
                    "Windows could not apply the required worker resource limits.",
                )
        except BaseException:
            value = int(raw or 0)
            if value not in {0, _INVALID_HANDLE_VALUE} and not self.handle.is_open:
                self.handle._adopt_valid(value)
            if not _close_owned_handle_bounded(self.handle):
                _quarantine_owned_handle(self.handle)
                raise WindowsLaunchError(
                    WindowsLaunchErrorCode.CLEANUP_FAILED,
                    "job.cleanup",
                    "Windows could not clean up a worker job after setup failed.",
                ) from None
            raise

    def assign(self, process: int) -> None:
        assign = _kernel32().AssignProcessToJobObject
        assign.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        assign.restype = wintypes.BOOL
        if not assign(wintypes.HANDLE(self.handle.value), wintypes.HANDLE(process)):
            _reject(
                WindowsLaunchErrorCode.JOB_FAILED,
                "job.assign",
                "Windows could not atomically contain the suspended worker.",
            )

    def resource_snapshot(self) -> WindowsWorkerResourceSnapshot:
        """Query authoritative memory limits and peaks without exposing Job authority."""

        query = _kernel32().QueryInformationJobObject
        query.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        )
        query.restype = wintypes.BOOL
        information = _JobObjectExtendedLimitInformation()
        returned_length = wintypes.DWORD()
        if not query(
            wintypes.HANDLE(self.handle.value),
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(information),
            ctypes.sizeof(information),
            ctypes.byref(returned_length),
        ):
            _reject(
                WindowsLaunchErrorCode.JOB_FAILED,
                "job.resource_snapshot",
                "Windows could not query the worker resource state.",
            )
        required_flags = _JOB_OBJECT_LIMIT_PROCESS_MEMORY | _JOB_OBJECT_LIMIT_JOB_MEMORY
        limit_flags = int(information.basic_limit_information.limit_flags)
        process_memory_limit = int(information.process_memory_limit)
        job_memory_limit = int(information.job_memory_limit)
        if (
            returned_length.value != ctypes.sizeof(information)
            or limit_flags & required_flags != required_flags
            or process_memory_limit < 1
            or job_memory_limit < process_memory_limit
        ):
            _reject(
                WindowsLaunchErrorCode.JOB_FAILED,
                "job.resource_snapshot",
                "Windows returned an invalid worker resource state.",
            )
        return WindowsWorkerResourceSnapshot(
            process_memory_limit_bytes=process_memory_limit,
            job_memory_limit_bytes=job_memory_limit,
            peak_process_memory_used_bytes=int(information.peak_process_memory_used),
            peak_job_memory_used_bytes=int(information.peak_job_memory_used),
        )

    def active_processes(self) -> int:
        query = _kernel32().QueryInformationJobObject
        query.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        )
        query.restype = wintypes.BOOL
        accounting = _JobObjectBasicAccountingInformation()
        if not query(
            wintypes.HANDLE(self.handle.value),
            _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS,
            ctypes.byref(accounting),
            ctypes.sizeof(accounting),
            None,
        ):
            _reject(
                WindowsLaunchErrorCode.JOB_FAILED,
                "job.query",
                "Windows could not verify the worker process tree state.",
            )
        return int(accounting.active_processes)

    def terminate(self) -> None:
        terminate = _kernel32().TerminateJobObject
        terminate.argtypes = (wintypes.HANDLE, wintypes.UINT)
        terminate.restype = wintypes.BOOL
        if not terminate(wintypes.HANDLE(self.handle.value), 1):
            _reject(
                WindowsLaunchErrorCode.TERMINATION_FAILED,
                "job.terminate",
                "Windows could not terminate the restricted worker process tree.",
            )

    def close_no_raise(self) -> bool:
        return self.handle.close_no_raise()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.handle.close()


def _wait_for_empty_job_no_raise(job: _Job, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        active = _job_active_processes_no_raise(job)
        if active is None:
            return False
        if active == 0:
            return True
        time.sleep(_TREE_WAIT_QUANTUM_SECONDS)
    return False


def _job_active_processes_no_raise(job: _Job) -> int | None:
    query = _kernel32().QueryInformationJobObject
    query.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    query.restype = wintypes.BOOL
    accounting = _JobObjectBasicAccountingInformation()
    try:
        handle = job.handle.value
    except WindowsLaunchError:
        return None
    if not query(
        wintypes.HANDLE(handle),
        _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS,
        ctypes.byref(accounting),
        ctypes.sizeof(accounting),
        None,
    ):
        return None
    return int(accounting.active_processes)


def _terminate_job_no_raise(job: _Job) -> bool:
    terminate = _kernel32().TerminateJobObject
    terminate.argtypes = (wintypes.HANDLE, wintypes.UINT)
    terminate.restype = wintypes.BOOL
    try:
        handle = job.handle.value
    except WindowsLaunchError:
        return False
    return bool(terminate(wintypes.HANDLE(handle), 1))


def _stop_job_tree_no_raise(job: _Job, timeout_seconds: float) -> bool:
    active = _job_active_processes_no_raise(job)
    if active is None:
        return False
    if active:
        _terminate_job_no_raise(job)
    return _wait_for_empty_job_no_raise(job, timeout_seconds)


def _terminate_process_no_raise(process: _OwnedHandle, timeout_seconds: float) -> bool:
    wait = _kernel32().WaitForSingleObject
    wait.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    wait.restype = wintypes.DWORD
    if wait(wintypes.HANDLE(process.value), 0) == _WAIT_OBJECT_0:
        return True
    terminate = _kernel32().TerminateProcess
    terminate.argtypes = (wintypes.HANDLE, wintypes.UINT)
    terminate.restype = wintypes.BOOL
    if not terminate(wintypes.HANDLE(process.value), 1):
        return False
    milliseconds = min(int(timeout_seconds * 1000), _INFINITE - 1)
    return bool(wait(wintypes.HANDLE(process.value), milliseconds) == _WAIT_OBJECT_0)


@dataclass(slots=True, repr=False)
class _FailedLaunchCleanup:
    job: _Job | None = field(repr=False)
    process: _OwnedHandle | None = field(repr=False)
    profile: _AppContainerProfile = field(repr=False)
    job_assigned: bool
    process_authority_missing: bool = False


_FAILED_LAUNCH_QUARANTINE_LOCK: Final = threading.Lock()
_FAILED_LAUNCH_QUARANTINE: list[_FailedLaunchCleanup] = []


def _quarantine_failed_launch(
    job: _Job | None,
    process: _OwnedHandle | None,
    profile: _AppContainerProfile,
    *,
    job_assigned: bool,
    process_authority_missing: bool = False,
) -> None:
    with _FAILED_LAUNCH_QUARANTINE_LOCK:
        _FAILED_LAUNCH_QUARANTINE.append(
            _FailedLaunchCleanup(
                job,
                process,
                profile,
                job_assigned,
                process_authority_missing,
            )
        )


def _drain_failed_launch_quarantine() -> bool:
    with _FAILED_LAUNCH_QUARANTINE_LOCK:
        remaining: list[_FailedLaunchCleanup] = []
        for entry in _FAILED_LAUNCH_QUARANTINE:
            if entry.process_authority_missing:
                remaining.append(entry)
                continue
            tree_confirmed = True
            if entry.job_assigned and entry.job is not None:
                tree_confirmed = _stop_job_tree_no_raise(
                    entry.job,
                    _TERMINATION_WAIT_SECONDS,
                )
            elif entry.process is not None:
                tree_confirmed = _terminate_process_no_raise(
                    entry.process,
                    _TERMINATION_WAIT_SECONDS,
                )
            if entry.process is not None and not tree_confirmed:
                _terminate_process_no_raise(entry.process, _TERMINATION_WAIT_SECONDS)
            if not tree_confirmed:
                remaining.append(entry)
                continue
            for handle in (
                entry.process,
                None if entry.job is None else entry.job.handle,
            ):
                if handle is not None and not _close_owned_handle_bounded(handle):
                    _quarantine_owned_handle(handle)
            if not _close_profile_bounded(entry.profile):
                _quarantine_profile(entry.profile, cleanup_allowed=True)
        _FAILED_LAUNCH_QUARANTINE[:] = remaining
        return not remaining


def _drain_launch_quarantines() -> bool:
    clean = _drain_failed_launch_quarantine()
    clean = _drain_pipe_handle_quarantine() and clean
    clean = _drain_retained_handle_quarantine() and clean
    clean = _drain_acl_mutex_quarantine() and clean
    return _drain_profile_quarantine() and clean


def _clear_process_creation_cleanup_pending() -> bool:
    global _PROCESS_CREATION_CLEANUP_PENDING, _PROCESS_CREATION_QUARANTINED

    if not _PROCESS_CREATION_LOCK.acquire(timeout=_PROCESS_CREATION_WAIT_SECONDS):
        _PROCESS_CREATION_QUARANTINED = True
        return False
    try:
        _PROCESS_CREATION_CLEANUP_PENDING = False
        return True
    finally:
        _PROCESS_CREATION_LOCK.release()


class _WindowsPipeReader(io.RawIOBase):
    __slots__ = ("_close_failed", "_handle")

    def __init__(self, handle: _OwnedHandle) -> None:
        super().__init__()
        self._handle = handle
        self._close_failed = False

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        if self.closed:
            raise ValueError("read of closed worker transport")
        view = memoryview(buffer).cast("B")
        if not view:
            return 0
        storage = (ctypes.c_ubyte * len(view)).from_buffer(view)
        read = wintypes.DWORD()
        function = _kernel32().ReadFile
        function.argtypes = (
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        )
        function.restype = wintypes.BOOL
        if not function(
            wintypes.HANDLE(self._handle.value),
            storage,
            len(view),
            ctypes.byref(read),
            None,
        ):
            if ctypes.get_last_error() == _ERROR_BROKEN_PIPE:
                return 0
            _reject(
                WindowsLaunchErrorCode.PIPE_FAILED,
                "pipe.read",
                "Windows could not read from the bounded worker transport.",
            )
        return int(read.value)

    def close(self) -> None:
        if self.closed and not self._close_failed:
            return
        failed = not self._handle.close_no_raise()
        if not self.closed:
            try:
                super().close()
            except Exception:
                failed = True
        self._close_failed = failed
        if failed:
            _reject(
                WindowsLaunchErrorCode.PIPE_FAILED,
                "pipe.close",
                "Windows could not close a bounded worker transport handle.",
            )


class _WindowsPipeWriter(io.RawIOBase):
    __slots__ = ("_close_failed", "_handle")

    def __init__(self, handle: _OwnedHandle) -> None:
        super().__init__()
        self._handle = handle
        self._close_failed = False

    def writable(self) -> bool:
        return True

    def write(self, buffer: Any) -> int:
        if self.closed:
            raise ValueError("write to closed worker transport")
        data = bytes(buffer)
        if not data:
            return 0
        written = wintypes.DWORD()
        function = _kernel32().WriteFile
        function.argtypes = (
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        )
        function.restype = wintypes.BOOL
        storage = ctypes.create_string_buffer(data)
        if not function(
            wintypes.HANDLE(self._handle.value),
            storage,
            len(data),
            ctypes.byref(written),
            None,
        ):
            raise BrokenPipeError("worker transport is closed")
        return int(written.value)

    def close(self) -> None:
        if self.closed and not self._close_failed:
            return
        failed = not self._handle.close_no_raise()
        if not self.closed:
            try:
                super().close()
            except Exception:
                failed = True
        self._close_failed = failed
        if failed:
            _reject(
                WindowsLaunchErrorCode.PIPE_FAILED,
                "pipe.close",
                "Windows could not close a bounded worker transport handle.",
            )


class WindowsWorkerProcess(AbstractContextManager["WindowsWorkerProcess"]):
    """Retained AppContainer process/job/transport ownership for one launch."""

    __slots__ = (
        "_pid",
        "_process",
        "_job",
        "_profile",
        "_transport",
        "_isolation",
        "_closed",
        "_cleanup_failed",
        "_containment_closed",
        "_transport_closed",
        "_tree_confirmed",
        "_profile_closed",
        "_profile_cleanup_allowed",
        "_returncode",
    )

    def __init__(
        self,
        *,
        pid: int,
        process: _OwnedHandle,
        job: _Job,
        profile: _AppContainerProfile,
        transport: WorkerTransport,
        isolation: IsolationEvidence,
    ) -> None:
        self._pid = pid
        self._process = process
        self._job = job
        self._profile = profile
        self._transport = transport
        self._isolation = isolation
        self._closed = False
        self._cleanup_failed = False
        self._containment_closed = False
        self._transport_closed = False
        self._tree_confirmed = False
        self._profile_closed = False
        self._profile_cleanup_allowed = True
        self._returncode: int | None = None

    @property
    def pid(self) -> int:
        return self._pid

    @property
    def transport(self) -> WorkerTransport:
        return self._transport

    @property
    def isolation(self) -> IsolationEvidence:
        return self._isolation

    def resource_snapshot(self) -> WindowsWorkerResourceSnapshot:
        """Return safe Job accounting while process and containment authority remain open."""

        if (
            self._closed
            or self._containment_closed
            or not self._process.is_open
            or not self._job.handle.is_open
        ):
            _reject(
                WindowsLaunchErrorCode.PROCESS_FAILED,
                "resource_snapshot.closed",
                "Worker resource data is unavailable after containment authority closes.",
            )
        return self._job.resource_snapshot()

    def poll(self) -> int | None:
        if self._returncode is not None:
            return self._returncode
        if self._closed:
            _reject(
                WindowsLaunchErrorCode.PROCESS_FAILED,
                "process.closed",
                "The worker process authority is already closed.",
            )
        if self._job.active_processes() != 0:
            return None
        self._returncode = self._exit_code()
        return self._returncode

    def wait(self, timeout_seconds: float | None = None) -> int | None:
        if self._returncode is not None:
            return self._returncode
        if self._closed:
            _reject(
                WindowsLaunchErrorCode.PROCESS_FAILED,
                "process.closed",
                "The worker process authority is already closed.",
            )
        if timeout_seconds is not None:
            if type(timeout_seconds) is int:
                valid_timeout = 0 <= timeout_seconds <= _MAX_WINDOWS_WAIT_SECONDS
            elif type(timeout_seconds) is float:
                valid_timeout = (
                    math.isfinite(timeout_seconds)
                    and 0 <= timeout_seconds <= _MAX_WINDOWS_WAIT_SECONDS
                )
            else:
                valid_timeout = False
            if not valid_timeout:
                _reject(
                    WindowsLaunchErrorCode.CONFIGURATION_INVALID,
                    "wait.timeout",
                    "A worker wait timeout must be finite and within the supported range.",
                )
        deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
        while self._job.active_processes() != 0:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                time.sleep(min(_TREE_WAIT_QUANTUM_SECONDS, remaining))
            else:
                time.sleep(_TREE_WAIT_QUANTUM_SECONDS)
        self._returncode = self._exit_code()
        return self._returncode

    def duplicate_handle(
        self,
        source_handle: int,
        purpose: BrokeredHandlePurpose,
    ) -> int:
        """Duplicate one explicitly reduced, non-inheritable handle into the worker."""

        from paperworkshell.worker.client.process import BrokeredHandlePurpose

        if (
            type(source_handle) is not int
            or not 0 < source_handle <= _MAX_WINDOWS_SIZE_T
            or source_handle == _INVALID_HANDLE_VALUE
            or type(purpose) is not BrokeredHandlePurpose
        ):
            _reject(
                WindowsLaunchErrorCode.CONFIGURATION_INVALID,
                "handle_transfer.request",
                "A valid typed brokered-handle request is required.",
            )
        if self._closed or self._containment_closed or not self._process.is_open:
            _reject(
                WindowsLaunchErrorCode.HANDLE_TRANSFER_FAILED,
                "handle_transfer.process",
                "The document worker cannot receive another brokered handle.",
            )
        desired_access = {
            BrokeredHandlePurpose.READ_SOURCE: (
                _FILE_READ_DATA | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE
            ),
            BrokeredHandlePurpose.WRITE_RESULT_PIPE: _FILE_WRITE_DATA | _SYNCHRONIZE,
        }[purpose]
        kernel32 = _kernel32()
        current_process = kernel32.GetCurrentProcess
        current_process.argtypes = ()
        current_process.restype = wintypes.HANDLE
        duplicate = kernel32.DuplicateHandle
        duplicate.argtypes = (
            wintypes.HANDLE,
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        )
        duplicate.restype = wintypes.BOOL
        target_handle = wintypes.HANDLE()
        succeeded = duplicate(
            current_process(),
            wintypes.HANDLE(source_handle),
            wintypes.HANDLE(self._process.value),
            ctypes.byref(target_handle),
            desired_access,
            False,
            0,
        )
        target_value = int(target_handle.value or 0)
        if not succeeded or target_value in {0, _INVALID_HANDLE_VALUE}:
            _reject(
                WindowsLaunchErrorCode.HANDLE_TRANSFER_FAILED,
                "handle_transfer.duplicate",
                "Windows could not transfer the required reduced-right handle.",
            )
        return target_value

    def terminate_tree(self) -> None:
        if self._closed or self._containment_closed:
            return
        active = _job_active_processes_no_raise(self._job)
        if active == 0:
            if self._returncode is None:
                self._returncode = self._exit_code()
            return
        if not _stop_job_tree_no_raise(self._job, _TERMINATION_WAIT_SECONDS):
            _reject(
                WindowsLaunchErrorCode.TERMINATION_FAILED,
                "job.await_termination",
                "The restricted worker process tree did not terminate in time.",
            )
        self._returncode = self._exit_code()

    def close(self) -> None:
        if self._closed:
            if self._cleanup_failed:
                _reject(
                    WindowsLaunchErrorCode.CLEANUP_FAILED,
                    "process.close",
                    "PaperWorkShell could not completely clean up a worker launch.",
                )
            return
        failed = False
        if not self._containment_closed:
            if not self._tree_confirmed and self._job.handle._value:
                self._tree_confirmed = _stop_job_tree_no_raise(
                    self._job,
                    _TERMINATION_WAIT_SECONDS,
                )
            if self._returncode is None:
                try:
                    if self._tree_confirmed:
                        self._returncode = self._exit_code()
                except WindowsLaunchError:
                    failed = True
            if self._tree_confirmed:
                process_closed = _close_owned_handle_bounded(self._process)
                if not process_closed:
                    _quarantine_owned_handle(self._process)
                job_closed = _close_owned_handle_bounded(self._job.handle)
                if not job_closed:
                    _quarantine_owned_handle(self._job.handle)
                self._containment_closed = process_closed and job_closed
                failed = not self._containment_closed or failed
            else:
                # Closing the last retained job handle is the OS-enforced final tree-kill
                # fallback.  Without an ACTIVE_PROCESS_ZERO observation, runtime grants
                # and the profile deliberately remain in place rather than being revoked
                # from a possibly terminating process.
                if self._process._value:
                    try:
                        _terminate_process_no_raise(
                            self._process,
                            _TERMINATION_WAIT_SECONDS,
                        )
                    except WindowsLaunchError:
                        pass
                job_closed = _close_owned_handle_bounded(self._job.handle)
                if not job_closed:
                    _quarantine_owned_handle(self._job.handle)
                process_closed = _close_owned_handle_bounded(self._process)
                if not process_closed:
                    _quarantine_owned_handle(self._process)
                self._profile_cleanup_allowed = False
                _quarantine_profile(self._profile, cleanup_allowed=False)
                self._containment_closed = job_closed and process_closed
                failed = True
        # Stop the process tree before closing the anonymous-pipe transport. A
        # live worker can keep its pipe ends in a blocking ReadFile/WriteFile;
        # terminating the retained Job first makes endpoint closure bounded.
        if not self._transport_closed:
            try:
                self._transport.close()
            except Exception:
                failed = True
            else:
                self._transport_closed = True
        if self._tree_confirmed and self._profile_cleanup_allowed and not self._profile_closed:
            if _close_profile_bounded(self._profile):
                self._profile_closed = True
            else:
                _quarantine_profile(self._profile, cleanup_allowed=True)
                failed = True
        profile_resolved = self._profile_closed or not self._profile_cleanup_allowed
        self._closed = self._containment_closed and self._transport_closed and profile_resolved
        self._cleanup_failed = failed or self._cleanup_failed
        if self._cleanup_failed:
            _reject(
                WindowsLaunchErrorCode.CLEANUP_FAILED,
                "process.close",
                "PaperWorkShell could not completely clean up a worker launch.",
            )

    def _exit_code(self) -> int:
        wait = _kernel32().WaitForSingleObject
        wait.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        wait.restype = wintypes.DWORD
        if wait(wintypes.HANDLE(self._process.value), 0) == _WAIT_TIMEOUT:
            return _STILL_ACTIVE
        code = wintypes.DWORD()
        get_exit_code = _kernel32().GetExitCodeProcess
        get_exit_code.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        get_exit_code.restype = wintypes.BOOL
        if not get_exit_code(wintypes.HANDLE(self._process.value), ctypes.byref(code)):
            _reject(
                WindowsLaunchErrorCode.PROCESS_FAILED,
                "process.exit_code",
                "Windows could not query the worker exit state.",
            )
        return int(code.value)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"WindowsWorkerProcess(pid={self._pid}, isolated=True)"


def _adopt_process_information(
    information: _ProcessInformation,
    process: _OwnedHandle,
    thread: _OwnedHandle,
) -> bool:
    """Move every valid raw process authority into preallocated owners."""

    process_value = int(information.process or 0)
    thread_value = int(information.thread or 0)
    produced_authority = bool(
        process_value not in {0, _INVALID_HANDLE_VALUE}
        or thread_value not in {0, _INVALID_HANDLE_VALUE}
        or information.process_id
        or information.thread_id
    )
    if process_value not in {0, _INVALID_HANDLE_VALUE}:
        if not process.is_open:
            process._adopt_valid(process_value)
        information.process = wintypes.HANDLE()
    if thread_value not in {0, _INVALID_HANDLE_VALUE}:
        if not thread.is_open:
            thread._adopt_valid(thread_value)
        information.thread = wintypes.HANDLE()
    return produced_authority


def _process_information_produced_authority(information: _ProcessInformation) -> bool:
    process_value = int(information.process or 0)
    thread_value = int(information.thread or 0)
    return bool(
        process_value not in {0, _INVALID_HANDLE_VALUE}
        or thread_value not in {0, _INVALID_HANDLE_VALUE}
        or information.process_id
        or information.thread_id
    )


class WindowsProcessLauncher:
    """Launch the fixed Python worker in a zero-capability AppContainer."""

    __slots__ = ("_config",)

    def __init__(self, config: WindowsLaunchConfig) -> None:
        if type(config) is not WindowsLaunchConfig:
            _reject(
                WindowsLaunchErrorCode.CONFIGURATION_INVALID,
                "config",
                "A validated fixed Windows worker configuration is required.",
            )
        self._config = config

    def launch(self, request: WorkerLaunchRequest) -> WindowsWorkerProcess:
        global _PROCESS_CREATION_CLEANUP_PENDING, _PROCESS_CREATION_QUARANTINED

        from paperworkshell.worker.client.process import (
            IsolationEvidence,
            WorkerLaunchRequest,
            create_worker_transport,
        )

        if type(request) is not WorkerLaunchRequest:
            _reject(
                WindowsLaunchErrorCode.CONFIGURATION_INVALID,
                "request",
                "A validated worker launch request is required.",
            )
        _validate_windows_resource_policy(request.resource_policy)
        if _PROCESS_CREATION_QUARANTINED or _PROCESS_CREATION_CLEANUP_PENDING:
            _reject(
                WindowsLaunchErrorCode.CLEANUP_FAILED,
                "process_creation.quarantine",
                "Worker process creation remains disabled after a handle cleanup failure.",
            )
        if not _drain_launch_quarantines():
            _reject(
                WindowsLaunchErrorCode.CLEANUP_FAILED,
                "launch.quarantine",
                "A previous worker launch still requires deterministic cleanup.",
            )
        process = _OwnedHandle.empty()
        thread = _OwnedHandle.empty()
        information = _ProcessInformation()
        profile = _AppContainerProfile.create()
        job: _Job | None = None
        job_assigned = False
        process_created = False
        process_cleanup_pending = False
        pipes: _PipeSet | None = None
        pid = 0
        transport: WorkerTransport | None = None
        try:
            profile.grant_read_execute(self._config.access_roots)
            profile.require_read_execute(
                (
                    *self._config.access_roots,
                    self._config.python_executable,
                    self._config.bootstrap_script,
                    self._config.working_directory,
                )
            )
            job = _Job(request.resource_policy)
            bootstrap = encode_bootstrap_grant(
                BootstrapGrant(
                    session_id=request.session_id,
                    worker_instance_id=request.worker_instance_id,
                    case_id=request.case_id,
                    session_capability=request.session_capability,
                )
            )
            if len(bootstrap) != BOOTSTRAP_BYTES:
                _reject(
                    WindowsLaunchErrorCode.BOOTSTRAP_FAILED,
                    "bootstrap.size",
                    "The worker bootstrap grant has an invalid size.",
                )
            _acquire_process_creation_authority()
            child_handles_secured = True
            child_handles_closed = True
            try:
                pipes = _PipeSet.create()
                with _AttributeList(profile.sid, pipes.inherited_handles) as attributes:
                    try:
                        self._create_suspended_process(
                            pipes,
                            attributes,
                            profile.local_app_data,
                            information,
                        )
                        process_created = True
                    finally:
                        pid = int(information.process_id)
                        process_created = (
                            _process_information_produced_authority(information) or process_created
                        )
                        _adopt_process_information(information, process, thread)
                    if not process.is_open:
                        _PROCESS_CREATION_QUARANTINED = True
                    if not process.is_open or not thread.is_open or pid < 1:
                        _reject(
                            WindowsLaunchErrorCode.PROCESS_FAILED,
                            "process.handles",
                            "Windows returned incomplete worker process authority.",
                        )
                job.assign(process.value)
                job_assigned = True
            finally:
                if process_created and not job_assigned:
                    _PROCESS_CREATION_CLEANUP_PENDING = True
                    process_cleanup_pending = True
                if pipes is not None:
                    child_handles_secured, child_handles_closed = pipes.secure_child_ends_no_raise()
                if not child_handles_secured:
                    _PROCESS_CREATION_QUARANTINED = True
                if _PROCESS_CREATION_QUARANTINED:
                    child_handles_secured = False
                _PROCESS_CREATION_LOCK.release()
                if not child_handles_secured:
                    _reject(
                        WindowsLaunchErrorCode.CLEANUP_FAILED,
                        "pipe.child_inheritance",
                        "Windows could not close or de-inherit a worker-only handle.",
                    )
                if not child_handles_closed:
                    _reject(
                        WindowsLaunchErrorCode.CLEANUP_FAILED,
                        "pipe.child_close",
                        "Windows could not close broker copies of worker-only handles.",
                    )
            _write_exact(pipes.bootstrap_write.value, bootstrap)
            pipes.bootstrap_write.close()
            _resume_thread(thread.value)
            thread.close()
            reader = _WindowsPipeReader(pipes.client_read)
            writer = _WindowsPipeWriter(pipes.client_write)
            transport = create_worker_transport(reader, writer)
            isolation = IsolationEvidence(
                app_container=True,
                network_denied=True,
                job_object_assigned=True,
                kill_on_job_close=True,
                case_paths_absent=True,
                minimal_environment=True,
                explicit_handle_list=True,
                shell_disabled=True,
                inherited_handle_count=3,
            )
            result = WindowsWorkerProcess(
                pid=pid,
                process=process,
                job=job,
                profile=profile,
                transport=transport,
                isolation=isolation,
            )
            return result
        except BaseException:
            process_created = (
                _process_information_produced_authority(information) or process_created
            )
            _adopt_process_information(information, process, thread)
            cleanup_failed = False
            tree_confirmed = True
            process_authority_missing = process_created and not process.is_open
            if process_authority_missing:
                tree_confirmed = False
            elif job_assigned and job is not None:
                tree_confirmed = _stop_job_tree_no_raise(
                    job,
                    _TERMINATION_WAIT_SECONDS,
                )
            elif process.is_open:
                tree_confirmed = _terminate_process_no_raise(
                    process,
                    _TERMINATION_WAIT_SECONDS,
                )
            if process.is_open and not tree_confirmed:
                _terminate_process_no_raise(process, _TERMINATION_WAIT_SECONDS)
            if transport is not None:
                try:
                    transport.close()
                except Exception:
                    cleanup_failed = True
            if pipes is not None:
                cleanup_failed = not pipes.close_no_raise() or cleanup_failed
            if thread.is_open:
                if not _close_owned_handle_bounded(thread):
                    _quarantine_owned_handle(thread)
                    cleanup_failed = True
            if tree_confirmed:
                if process.is_open and not _close_owned_handle_bounded(process):
                    _quarantine_owned_handle(process)
                    cleanup_failed = True
                if job is not None and not _close_owned_handle_bounded(job.handle):
                    _quarantine_owned_handle(job.handle)
                    cleanup_failed = True
                if not _close_profile_bounded(profile):
                    _quarantine_profile(profile, cleanup_allowed=True)
                    cleanup_failed = True
            else:
                _quarantine_failed_launch(
                    job,
                    process if process.is_open else None,
                    profile,
                    job_assigned=job_assigned,
                    process_authority_missing=process_authority_missing,
                )
                cleanup_failed = True
            if process_cleanup_pending and not _clear_process_creation_cleanup_pending():
                cleanup_failed = True
            if cleanup_failed:
                raise WindowsLaunchError(
                    WindowsLaunchErrorCode.CLEANUP_FAILED,
                    "launch.cleanup",
                    "PaperWorkShell could not completely clean up a failed worker launch.",
                ) from None
            raise

    def _create_suspended_process(
        self,
        pipes: _PipeSet,
        attributes: _AttributeList,
        local_app_data: Path,
        information: _ProcessInformation,
    ) -> None:
        startup = _StartupInfoExW()
        startup.startup_info.cb = ctypes.sizeof(startup)
        startup.startup_info.flags = _STARTF_USESTDHANDLES
        startup.startup_info.standard_input = wintypes.HANDLE(pipes.child_read.value)
        startup.startup_info.standard_output = wintypes.HANDLE(pipes.child_write.value)
        startup.startup_info.standard_error = wintypes.HANDLE(pipes.bootstrap_read.value)
        startup.attribute_list = attributes.pointer
        command_line = ctypes.create_unicode_buffer(
            subprocess.list2cmdline(self._config.fixed_argv)
        )
        environment = _minimal_environment_block(local_app_data)
        create_process = _kernel32().CreateProcessW
        create_process.argtypes = (
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.POINTER(_StartupInfoExW),
            ctypes.POINTER(_ProcessInformation),
        )
        create_process.restype = wintypes.BOOL
        flags = (
            _CREATE_SUSPENDED
            | _CREATE_UNICODE_ENVIRONMENT
            | _CREATE_NO_WINDOW
            | _EXTENDED_STARTUPINFO_PRESENT
        )
        if not create_process(
            os.fspath(self._config.python_executable),
            command_line,
            None,
            None,
            True,
            flags,
            environment,
            os.fspath(self._config.working_directory),
            ctypes.byref(startup),
            ctypes.byref(information),
        ):
            raw_failure = ctypes.get_last_error()
            failure_context = {
                2: "process.create_resource",
                3: "process.create_resource",
                5: "process.create_access",
                50: "process.create_unsupported",
                87: "process.create_parameters",
                123: "process.create_resource",
                127: "process.create_runtime",
                193: "process.create_runtime",
                203: "process.create_environment",
                206: "process.create_parameters",
                575: "process.create_runtime",
                740: "process.create_access",
                1114: "process.create_runtime",
                1260: "process.create_policy",
                1307: "process.create_security",
                1308: "process.create_security",
                1312: "process.create_security",
                1314: "process.create_security",
                1338: "process.create_security",
                1349: "process.create_security",
                14001: "process.create_runtime",
            }.get(raw_failure, "process.create")
            _reject(
                WindowsLaunchErrorCode.PROCESS_FAILED,
                failure_context,
                "Windows could not start the restricted document worker.",
            )

    def __repr__(self) -> str:
        return "WindowsProcessLauncher(<fixed restricted configuration>)"


def _minimal_environment_block(local_app_data: Path) -> ctypes.Array[ctypes.c_wchar]:
    get_windows_directory = _kernel32().GetWindowsDirectoryW
    get_windows_directory.argtypes = (wintypes.LPWSTR, wintypes.UINT)
    get_windows_directory.restype = wintypes.UINT
    buffer = ctypes.create_unicode_buffer(32_768)
    written = get_windows_directory(buffer, len(buffer))
    if not written or written >= len(buffer):
        _reject(
            WindowsLaunchErrorCode.ISOLATION_UNAVAILABLE,
            "environment.system_root",
            "Windows could not construct the minimal worker environment.",
        )
    temp = local_app_data / "Temp"
    entries = {
        "LOCALAPPDATA": os.fspath(local_app_data),
        "SystemRoot": buffer.value,
        "TEMP": os.fspath(temp),
        "TMP": os.fspath(temp),
        "WINDIR": buffer.value,
    }
    if any("\x00" in entry for pair in entries.items() for entry in pair):
        _reject(
            WindowsLaunchErrorCode.ISOLATION_UNAVAILABLE,
            "environment.values",
            "Windows could not construct the minimal worker environment.",
        )
    value = "\0".join(
        f"{name}={entry}"
        for name, entry in sorted(entries.items(), key=lambda item: item[0].upper())
    )
    value += "\0\0"
    return ctypes.create_unicode_buffer(value)


def _write_exact(handle: int, payload: bytes) -> None:
    if len(payload) != BOOTSTRAP_BYTES:
        _reject(
            WindowsLaunchErrorCode.BOOTSTRAP_FAILED,
            "bootstrap.size",
            "The worker bootstrap grant has an invalid size.",
        )
    write_file = _kernel32().WriteFile
    write_file.argtypes = (
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    )
    write_file.restype = wintypes.BOOL
    offset = 0
    attempts = 0
    while offset < len(payload) and attempts < _MAX_BOOTSTRAP_WRITE_ATTEMPTS:
        remaining = payload[offset:]
        storage = ctypes.create_string_buffer(remaining)
        written = wintypes.DWORD()
        if not write_file(
            wintypes.HANDLE(handle),
            storage,
            len(remaining),
            ctypes.byref(written),
            None,
        ):
            _reject(
                WindowsLaunchErrorCode.BOOTSTRAP_FAILED,
                "bootstrap.write",
                "Windows could not provision the worker launch grant.",
            )
        if not written.value:
            _reject(
                WindowsLaunchErrorCode.BOOTSTRAP_FAILED,
                "bootstrap.write",
                "Windows could not provision the worker launch grant.",
            )
        offset += int(written.value)
        attempts += 1
    if offset != len(payload):
        _reject(
            WindowsLaunchErrorCode.BOOTSTRAP_FAILED,
            "bootstrap.write",
            "Windows could not provision the complete worker launch grant.",
        )


def _resume_thread(handle: int) -> None:
    resume = _kernel32().ResumeThread
    resume.argtypes = (wintypes.HANDLE,)
    resume.restype = wintypes.DWORD
    if resume(wintypes.HANDLE(handle)) == 0xFFFF_FFFF:
        _reject(
            WindowsLaunchErrorCode.PROCESS_FAILED,
            "process.resume",
            "Windows could not resume the contained document worker.",
        )

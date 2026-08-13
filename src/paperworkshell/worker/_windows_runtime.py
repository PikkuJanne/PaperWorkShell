"""Worker-side Windows isolation audit and one-shot bootstrap reader."""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from paperworkshell.worker.protocol.bootstrap import BOOTSTRAP_BYTES

_TOKEN_QUERY: Final = 0x0008
_TOKEN_IS_APP_CONTAINER: Final = 29
_TOKEN_CAPABILITIES: Final = 30
_STD_INPUT_HANDLE: Final = -10
_STD_OUTPUT_HANDLE: Final = -11
_STD_ERROR_HANDLE: Final = -12
_STANDARD_HANDLE_IDS: Final = (_STD_INPUT_HANDLE, _STD_OUTPUT_HANDLE, _STD_ERROR_HANDLE)
_HANDLE_FLAG_INHERIT: Final = 0x0000_0001
_INVALID_HANDLE_VALUE: Final = ctypes.c_void_p(-1).value
_ERROR_BROKEN_PIPE: Final = 109
_PRIVATE_PYCACHE_DIRECTORY_NAME: Final = "PyCache"
_ALLOWED_ENVIRONMENT: Final = frozenset(
    {
        "SYSTEMROOT",
        "WINDIR",
        "LOCALAPPDATA",
        "PWS_PROFILE_ROOT",
        "TEMP",
        "TMP",
        "PWS_QPDF_EXECUTABLE",
        "PWS_TESSERACT_EXECUTABLE",
        "PWS_TESSDATA_ROOT",
        "PYTHONIOENCODING",
        "PYTHONUTF8",
    }
)


class IsolationAuditCode(StrEnum):
    """Stable worker-side isolation failures."""

    UNSUPPORTED_PLATFORM = "WORKER_ISOLATION_UNSUPPORTED_PLATFORM"
    TOKEN_UNAVAILABLE = "WORKER_ISOLATION_TOKEN_UNAVAILABLE"
    APP_CONTAINER_REQUIRED = "WORKER_ISOLATION_APP_CONTAINER_REQUIRED"
    NETWORK_CAPABILITY_PRESENT = "WORKER_ISOLATION_NETWORK_CAPABILITY_PRESENT"
    JOB_REQUIRED = "WORKER_ISOLATION_JOB_REQUIRED"
    ENVIRONMENT_INVALID = "WORKER_ISOLATION_ENVIRONMENT_INVALID"
    BYTECODE_POLICY_INVALID = "WORKER_ISOLATION_BYTECODE_POLICY_INVALID"
    STANDARD_HANDLE_POLICY_INVALID = "WORKER_ISOLATION_STANDARD_HANDLE_POLICY_INVALID"
    BOOTSTRAP_UNAVAILABLE = "WORKER_BOOTSTRAP_UNAVAILABLE"


class IsolationAuditError(Exception):
    """Sanitized isolation failure with no retained Windows error or path."""

    __slots__ = ("code",)

    def __init__(self, code: IsolationAuditCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True, slots=True)
class RuntimeIsolationEvidence:
    """Facts the worker proves about its own process before negotiation."""

    app_container: bool
    zero_capabilities: bool
    in_job: bool
    minimal_environment: bool
    private_bytecode_cache: bool
    standard_handles_non_inheritable: bool


def _fail(code: IsolationAuditCode) -> None:
    raise IsolationAuditError(code) from None


def _private_bytecode_configuration_is_valid(
    profile_root: object,
    local_app_data: object,
    configured_prefix: object,
    active_prefix: object,
) -> bool:
    """Bind the raw fixed option to the profile despite Windows path aliases."""

    if (
        type(profile_root) is not str
        or not profile_root
        or type(local_app_data) is not str
        or not local_app_data
        or type(configured_prefix) is not str
        or not configured_prefix
        or type(active_prefix) is not str
        or not active_prefix
        or "\x00" in configured_prefix
        or "\x00" in active_prefix
        or "\x00" in profile_root
        or not os.path.isabs(profile_root)
        or not os.path.isabs(configured_prefix)
        or not os.path.isabs(active_prefix)
    ):
        return False
    normalized_profile = os.path.normcase(os.path.abspath(profile_root))
    normalized_local = os.path.normcase(os.path.abspath(local_app_data))
    normalized_expected = os.path.normcase(
        os.path.abspath(os.path.join(profile_root, _PRIVATE_PYCACHE_DIRECTORY_NAME))
    )
    normalized_configured = os.path.normcase(os.path.abspath(configured_prefix))
    normalized_active = os.path.normcase(os.path.abspath(active_prefix))
    try:
        local_is_scoped = (
            os.path.commonpath((normalized_profile, normalized_local)) == normalized_profile
        )
    except ValueError:
        return False
    return (
        local_is_scoped
        and normalized_configured == normalized_expected
        and normalized_active == normalized_expected
    )


def _clear_and_verify_standard_handle_inheritance(kernel32: Any) -> None:
    """Prevent the three launch-only handles from reaching worker descendants."""

    get_std_handle = kernel32.GetStdHandle
    get_std_handle.argtypes = (wintypes.DWORD,)
    get_std_handle.restype = wintypes.HANDLE
    set_handle_information = kernel32.SetHandleInformation
    set_handle_information.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD)
    set_handle_information.restype = wintypes.BOOL
    get_handle_information = kernel32.GetHandleInformation
    get_handle_information.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    get_handle_information.restype = wintypes.BOOL

    handles: list[wintypes.HANDLE] = []
    for identifier in _STANDARD_HANDLE_IDS:
        handle = get_std_handle(wintypes.DWORD(identifier & 0xFFFF_FFFF))
        if not handle or handle == _INVALID_HANDLE_VALUE:
            _fail(IsolationAuditCode.STANDARD_HANDLE_POLICY_INVALID)
        handles.append(wintypes.HANDLE(handle))
    for handle in handles:
        if not set_handle_information(handle, _HANDLE_FLAG_INHERIT, 0):
            _fail(IsolationAuditCode.STANDARD_HANDLE_POLICY_INVALID)
    for handle in handles:
        flags = wintypes.DWORD()
        if (
            not get_handle_information(handle, ctypes.byref(flags))
            or flags.value & _HANDLE_FLAG_INHERIT
        ):
            _fail(IsolationAuditCode.STANDARD_HANDLE_POLICY_INVALID)


def read_bootstrap_from_standard_error() -> bytes:
    """Read the exact launch grant from the inherited one-shot bootstrap pipe."""

    if os.name != "nt":
        _fail(IsolationAuditCode.UNSUPPORTED_PLATFORM)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_std_handle = kernel32.GetStdHandle
    get_std_handle.argtypes = (wintypes.DWORD,)
    get_std_handle.restype = wintypes.HANDLE
    read_file = kernel32.ReadFile
    read_file.argtypes = (
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    )
    read_file.restype = wintypes.BOOL
    handle = get_std_handle(wintypes.DWORD(_STD_ERROR_HANDLE & 0xFFFFFFFF))
    if not handle or handle == wintypes.HANDLE(-1).value:
        _fail(IsolationAuditCode.BOOTSTRAP_UNAVAILABLE)
    output = bytearray(BOOTSTRAP_BYTES)
    offset = 0
    while offset < BOOTSTRAP_BYTES:
        read_count = wintypes.DWORD()
        buffer = (ctypes.c_ubyte * (BOOTSTRAP_BYTES - offset)).from_buffer(output, offset)
        if not read_file(
            handle,
            buffer,
            BOOTSTRAP_BYTES - offset,
            ctypes.byref(read_count),
            None,
        ):
            if ctypes.get_last_error() == _ERROR_BROKEN_PIPE:
                _fail(IsolationAuditCode.BOOTSTRAP_UNAVAILABLE)
            _fail(IsolationAuditCode.BOOTSTRAP_UNAVAILABLE)
        if read_count.value == 0:
            _fail(IsolationAuditCode.BOOTSTRAP_UNAVAILABLE)
        offset += read_count.value
    return bytes(output)


def verify_runtime_isolation() -> RuntimeIsolationEvidence:
    """Fail before session.ready unless OS-enforced isolation is observable."""

    if os.name != "nt":
        _fail(IsolationAuditCode.UNSUPPORTED_PLATFORM)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _clear_and_verify_standard_handle_inheritance(kernel32)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    current_process = kernel32.GetCurrentProcess()
    token = wintypes.HANDLE()
    open_token = advapi32.OpenProcessToken
    open_token.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    open_token.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    if not open_token(current_process, _TOKEN_QUERY, ctypes.byref(token)):
        _fail(IsolationAuditCode.TOKEN_UNAVAILABLE)
    try:
        is_app_container = wintypes.DWORD()
        returned = wintypes.DWORD()
        get_token_information = advapi32.GetTokenInformation
        get_token_information.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        )
        get_token_information.restype = wintypes.BOOL
        if not get_token_information(
            token,
            _TOKEN_IS_APP_CONTAINER,
            ctypes.byref(is_app_container),
            ctypes.sizeof(is_app_container),
            ctypes.byref(returned),
        ):
            _fail(IsolationAuditCode.TOKEN_UNAVAILABLE)
        if is_app_container.value != 1:
            _fail(IsolationAuditCode.APP_CONTAINER_REQUIRED)

        required = wintypes.DWORD()
        get_token_information(token, _TOKEN_CAPABILITIES, None, 0, ctypes.byref(required))
        capability_buffer = ctypes.create_string_buffer(required.value)
        if not get_token_information(
            token,
            _TOKEN_CAPABILITIES,
            capability_buffer,
            required,
            ctypes.byref(required),
        ):
            _fail(IsolationAuditCode.TOKEN_UNAVAILABLE)
        capability_count = ctypes.cast(
            capability_buffer,
            ctypes.POINTER(wintypes.DWORD),
        ).contents.value
        if capability_count != 0:
            _fail(IsolationAuditCode.NETWORK_CAPABILITY_PRESENT)
    finally:
        close_handle(token)

    in_job = wintypes.BOOL()
    is_process_in_job = kernel32.IsProcessInJob
    is_process_in_job.argtypes = (
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.BOOL),
    )
    is_process_in_job.restype = wintypes.BOOL
    if not is_process_in_job(current_process, None, ctypes.byref(in_job)) or not in_job.value:
        _fail(IsolationAuditCode.JOB_REQUIRED)
    if any(name.upper() not in _ALLOWED_ENVIRONMENT for name in os.environ):
        _fail(IsolationAuditCode.ENVIRONMENT_INVALID)
    profile_root = os.environ.get("PWS_PROFILE_ROOT")
    local_app_data = os.environ.get("LOCALAPPDATA")
    configured_prefix = sys._xoptions.get("pycache_prefix")
    pycache_prefix = sys.pycache_prefix
    if (
        not _private_bytecode_configuration_is_valid(
            profile_root,
            local_app_data,
            configured_prefix,
            pycache_prefix,
        )
        or not sys.dont_write_bytecode
    ):
        _fail(IsolationAuditCode.BYTECODE_POLICY_INVALID)
    assert type(pycache_prefix) is str
    try:
        if not os.path.isdir(pycache_prefix):
            _fail(IsolationAuditCode.BYTECODE_POLICY_INVALID)
        with os.scandir(pycache_prefix) as entries:
            if next(entries, None) is not None:
                _fail(IsolationAuditCode.BYTECODE_POLICY_INVALID)
    except OSError:
        _fail(IsolationAuditCode.BYTECODE_POLICY_INVALID)
    return RuntimeIsolationEvidence(True, True, True, True, True, True)

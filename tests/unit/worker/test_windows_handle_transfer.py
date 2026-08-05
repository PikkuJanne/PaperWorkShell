"""Windows primitive tests for reduced-right brokered handle duplication."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path
from typing import Any, cast

import pytest

from paperworkshell.worker.client import _windows as windows_module
from paperworkshell.worker.client._windows import (
    WindowsLaunchError,
    WindowsLaunchErrorCode,
    WindowsWorkerProcess,
    _OwnedHandle,
)
from paperworkshell.worker.client.process import BrokeredHandlePurpose


class _Function:
    def __init__(self, callback: Any) -> None:
        self._callback = callback
        self.argtypes: object = None
        self.restype: object = None

    def __call__(self, *args: object) -> object:
        return self._callback(*args)


class _FakeKernel32:
    def __init__(self, *, succeed: bool = True, target_handle: int = 997) -> None:
        self.succeed = succeed
        self.target_handle = target_handle
        self.calls: list[tuple[object, ...]] = []
        self.GetCurrentProcess = _Function(lambda: 101)
        self.DuplicateHandle = _Function(self._duplicate_handle)

    def _duplicate_handle(self, *args: object) -> bool:
        self.calls.append(args)
        target_pointer = cast(Any, args[3])
        target_pointer._obj.value = self.target_handle
        return self.succeed


def _worker_process() -> WindowsWorkerProcess:
    process = cast(Any, WindowsWorkerProcess.__new__(WindowsWorkerProcess))
    process._process = _OwnedHandle(313)
    process._closed = False
    process._containment_closed = False
    return cast(WindowsWorkerProcess, process)


@pytest.mark.parametrize(
    ("purpose", "expected_access"),
    (
        (
            BrokeredHandlePurpose.READ_SOURCE,
            windows_module._FILE_READ_DATA
            | windows_module._FILE_READ_ATTRIBUTES
            | windows_module._SYNCHRONIZE,
        ),
        (
            BrokeredHandlePurpose.WRITE_RESULT_PIPE,
            windows_module._FILE_WRITE_DATA | windows_module._SYNCHRONIZE,
        ),
        (
            BrokeredHandlePurpose.WRITE_OPERATION_CACHE,
            windows_module._FILE_WRITE_DATA | windows_module._SYNCHRONIZE,
        ),
    ),
)
def test_duplicate_handle_uses_reduced_access_without_inheritance_or_same_access(
    monkeypatch: pytest.MonkeyPatch,
    purpose: BrokeredHandlePurpose,
    expected_access: int,
) -> None:
    kernel32 = _FakeKernel32()
    monkeypatch.setattr(windows_module, "_kernel32", lambda: kernel32)

    target_handle = _worker_process().duplicate_handle(317, purpose)

    assert target_handle == 997
    assert len(kernel32.calls) == 1
    call = kernel32.calls[0]
    assert cast(Any, call[1]).value == 317
    assert cast(Any, call[2]).value == 313
    assert call[4] == expected_access
    assert call[5] is False
    assert call[6] == 0


def test_duplicate_handle_failure_is_sanitized_even_with_ambiguous_target_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel32 = _FakeKernel32(succeed=False, target_handle=991)
    monkeypatch.setattr(windows_module, "_kernel32", lambda: kernel32)

    with pytest.raises(WindowsLaunchError) as raised:
        _worker_process().duplicate_handle(331, BrokeredHandlePurpose.READ_SOURCE)

    assert raised.value.code is WindowsLaunchErrorCode.HANDLE_TRANSFER_FAILED
    assert "331" not in str(raised.value)
    assert "991" not in str(raised.value)


@pytest.mark.skipif(os.name != "nt", reason="requires Windows kernel handles")
def test_real_read_source_duplicate_cannot_write_and_is_not_inheritable(tmp_path: Path) -> None:
    import msvcrt

    source_path = tmp_path / "synthetic.pdf"
    source_path.write_bytes(b"%PDF-1.7\n%%EOF\n")
    source_fd = os.open(source_path, os.O_RDWR | os.O_BINARY)
    source_handle = msvcrt.get_osfhandle(source_fd)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    process_handle = int(open_process(0x0040, False, os.getpid()) or 0)
    assert process_handle > 0
    worker = cast(Any, WindowsWorkerProcess.__new__(WindowsWorkerProcess))
    worker._process = _OwnedHandle(process_handle)
    worker._closed = False
    worker._containment_closed = False
    target_handle = 0
    try:
        target_handle = worker.duplicate_handle(
            source_handle,
            BrokeredHandlePurpose.READ_SOURCE,
        )
        flags = wintypes.DWORD()
        get_handle_information = kernel32.GetHandleInformation
        get_handle_information.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        get_handle_information.restype = wintypes.BOOL
        assert get_handle_information(wintypes.HANDLE(target_handle), ctypes.byref(flags))
        assert flags.value & windows_module._HANDLE_FLAG_INHERIT == 0

        written = wintypes.DWORD()
        byte = ctypes.create_string_buffer(b"x")
        write_file = kernel32.WriteFile
        write_file.argtypes = (
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        )
        write_file.restype = wintypes.BOOL
        assert not write_file(
            wintypes.HANDLE(target_handle),
            byte,
            1,
            ctypes.byref(written),
            None,
        )
    finally:
        if target_handle:
            assert windows_module._close_raw_handle(target_handle)
        worker._process.close()
        os.close(source_fd)

    assert source_path.read_bytes() == b"%PDF-1.7\n%%EOF\n"


@pytest.mark.skipif(os.name != "nt", reason="requires Windows kernel handles")
def test_real_write_cache_duplicate_cannot_read_and_is_not_inheritable(tmp_path: Path) -> None:
    import msvcrt

    cache_path = tmp_path / "thumbnail.rgba"
    source_fd = os.open(cache_path, os.O_RDWR | os.O_CREAT | os.O_BINARY)
    source_handle = msvcrt.get_osfhandle(source_fd)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    process_handle = int(open_process(0x0040, False, os.getpid()) or 0)
    assert process_handle > 0
    worker = cast(Any, WindowsWorkerProcess.__new__(WindowsWorkerProcess))
    worker._process = _OwnedHandle(process_handle)
    worker._closed = False
    worker._containment_closed = False
    target_handle = 0
    try:
        target_handle = worker.duplicate_handle(
            source_handle,
            BrokeredHandlePurpose.WRITE_OPERATION_CACHE,
        )
        flags = wintypes.DWORD()
        get_handle_information = kernel32.GetHandleInformation
        get_handle_information.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        get_handle_information.restype = wintypes.BOOL
        assert get_handle_information(wintypes.HANDLE(target_handle), ctypes.byref(flags))
        assert flags.value & windows_module._HANDLE_FLAG_INHERIT == 0

        written = wintypes.DWORD()
        byte = ctypes.create_string_buffer(b"x")
        write_file = kernel32.WriteFile
        write_file.argtypes = (
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        )
        write_file.restype = wintypes.BOOL
        assert write_file(
            wintypes.HANDLE(target_handle),
            byte,
            1,
            ctypes.byref(written),
            None,
        )
        assert written.value == 1

        read = wintypes.DWORD()
        read_file = kernel32.ReadFile
        read_file.argtypes = (
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        )
        read_file.restype = wintypes.BOOL
        assert not read_file(
            wintypes.HANDLE(target_handle),
            byte,
            1,
            ctypes.byref(read),
            None,
        )
    finally:
        if target_handle:
            assert windows_module._close_raw_handle(target_handle)
        worker._process.close()
        os.close(source_fd)

    assert cache_path.read_bytes() == b"x"

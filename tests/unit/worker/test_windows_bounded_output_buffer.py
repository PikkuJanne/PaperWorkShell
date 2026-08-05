"""Windows tests for the fixed-size pagefile-backed worker output section."""

from __future__ import annotations

import ctypes
import mmap
import os
from ctypes import wintypes

import pytest

from paperworkshell.domain.pdf_thumbnails import (
    PdfThumbnailError,
    PdfThumbnailErrorCode,
)
from paperworkshell.worker import _windows_broker as broker_module
from paperworkshell.worker._windows_broker import WindowsBoundedOutputBuffer

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires Windows section objects")


def _kernel32() -> ctypes.WinDLL:
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _handle_is_open(handle: int) -> bool:
    kernel32 = _kernel32()
    get_handle_information = kernel32.GetHandleInformation
    get_handle_information.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    )
    get_handle_information.restype = wintypes.BOOL
    flags = wintypes.DWORD()
    return bool(get_handle_information(wintypes.HANDLE(handle), ctypes.byref(flags)))


def _map_write(handle: int, byte_count: int) -> int:
    kernel32 = _kernel32()
    map_view = kernel32.MapViewOfFile
    map_view.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_size_t,
    )
    map_view.restype = ctypes.c_void_p
    return int(
        map_view(
            wintypes.HANDLE(handle),
            0x0000_0002,
            0,
            0,
            byte_count,
        )
        or 0
    )


def _unmap(view: int) -> bool:
    kernel32 = _kernel32()
    unmap_view = kernel32.UnmapViewOfFile
    unmap_view.argtypes = (ctypes.c_void_p,)
    unmap_view.restype = wintypes.BOOL
    return bool(unmap_view(ctypes.c_void_p(view)))


def test_output_buffer_is_non_inheritable_zeroed_and_exactly_readable() -> None:
    payload = b"synthetic-rgba"
    with WindowsBoundedOutputBuffer.create(4_096) as output:
        assert output.capacity == 4_096
        assert "4096" not in repr(output)
        assert output.read_exact(len(payload)) == bytes(len(payload))

        kernel32 = _kernel32()
        flags = wintypes.DWORD()
        get_handle_information = kernel32.GetHandleInformation
        get_handle_information.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        )
        get_handle_information.restype = wintypes.BOOL
        assert get_handle_information(
            wintypes.HANDLE(output.write_handle_value),
            ctypes.byref(flags),
        )
        assert flags.value & broker_module._HANDLE_FLAG_INHERIT == 0

        view = _map_write(output.write_handle_value, output.capacity)
        assert view > 0
        try:
            ctypes.memmove(view, payload, len(payload))
            assert output.read_exact(len(payload)) == payload
        finally:
            assert _unmap(view)


@pytest.mark.parametrize("capacity", (False, 0, -1, 1.5, "4096"))
def test_output_buffer_rejects_invalid_capacity_without_leaking_values(
    capacity: object,
) -> None:
    with pytest.raises(PdfThumbnailError) as caught:
        WindowsBoundedOutputBuffer.create(capacity)  # type: ignore[arg-type]

    assert caught.value.code is PdfThumbnailErrorCode.MODEL_INVALID
    assert str(capacity) not in str(caught.value)


def test_output_buffer_rejects_read_past_capacity() -> None:
    with WindowsBoundedOutputBuffer.create(4_096) as output:
        with pytest.raises(PdfThumbnailError) as caught:
            output.read_exact(output.capacity + 1)

    assert caught.value.code is PdfThumbnailErrorCode.RESULT_INVALID
    assert "4097" not in str(caught.value)


def test_output_buffer_close_unmaps_view_and_closes_handle() -> None:
    output = WindowsBoundedOutputBuffer.create(4_096)
    handle = output.write_handle_value
    view = output._read_view
    assert _handle_is_open(handle)

    output.close()
    output.close()

    assert not _handle_is_open(handle)
    assert not _unmap(view)
    with pytest.raises(PdfThumbnailError) as caught:
        output.read_exact(0)
    assert caught.value.code is PdfThumbnailErrorCode.INTERNAL_ERROR
    assert str(handle) not in str(caught.value)


def test_output_buffer_cleanup_failure_is_sanitized_and_retriable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = WindowsBoundedOutputBuffer.create(mmap.PAGESIZE)
    calls: list[str] = []

    def fail_unmap(_view: int) -> bool:
        calls.append("unmap")
        return False

    def fail_close(_handle: int) -> bool:
        calls.append("close")
        return False

    with monkeypatch.context() as context:
        context.setattr(broker_module, "_unmap_view", fail_unmap)
        context.setattr(broker_module, "_close_handle", fail_close)
        with pytest.raises(PdfThumbnailError) as caught:
            output.close()

    assert caught.value.code is PdfThumbnailErrorCode.DESTINATION_FAILED
    assert calls == ["unmap", "close"]
    assert "handle" not in str(caught.value).casefold()
    assert output in broker_module._OUTPUT_CLEANUP_QUARANTINE
    output.close()
    assert output not in broker_module._OUTPUT_CLEANUP_QUARANTINE


def test_output_buffer_cleanup_failure_wins_and_retains_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = WindowsBoundedOutputBuffer.create(mmap.PAGESIZE)
    with monkeypatch.context() as context:
        context.setattr(broker_module, "_unmap_view", lambda _view: False)
        context.setattr(broker_module, "_close_handle", lambda _handle: False)
        with pytest.raises(PdfThumbnailError) as caught:
            with output:
                raise RuntimeError("synthetic primary")

    assert caught.value.code is PdfThumbnailErrorCode.DESTINATION_FAILED
    assert output in broker_module._OUTPUT_CLEANUP_QUARANTINE
    output.close()


def test_pending_cleanup_blocks_new_output_until_authority_is_released(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = WindowsBoundedOutputBuffer.create(mmap.PAGESIZE)
    with monkeypatch.context() as context:
        context.setattr(broker_module, "_unmap_view", lambda _view: False)
        context.setattr(broker_module, "_close_handle", lambda _handle: False)
        with pytest.raises(PdfThumbnailError):
            output.close()
        with pytest.raises(PdfThumbnailError) as caught:
            WindowsBoundedOutputBuffer.create(mmap.PAGESIZE)

    assert caught.value.code is PdfThumbnailErrorCode.DESTINATION_FAILED
    output.close()
    with WindowsBoundedOutputBuffer.create(mmap.PAGESIZE):
        pass


def test_creation_failure_retains_authority_when_cleanup_cannot_be_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_kernel32 = _kernel32()

    class _FailCall:
        argtypes: object = None
        restype: object = None

        def __call__(self, *_args: object) -> bool:
            return False

    class _CreateThenFailKernel:
        CreateFileMappingW = real_kernel32.CreateFileMappingW
        SetHandleInformation = _FailCall()

    with monkeypatch.context() as context:
        context.setattr(broker_module, "_kernel32", lambda: _CreateThenFailKernel())
        context.setattr(broker_module, "_close_handle", lambda _handle: False)
        with pytest.raises(PdfThumbnailError) as caught:
            WindowsBoundedOutputBuffer.create(mmap.PAGESIZE)

    assert caught.value.code is PdfThumbnailErrorCode.DESTINATION_FAILED
    assert len(broker_module._OUTPUT_CLEANUP_QUARANTINE) == 1
    partial = broker_module._OUTPUT_CLEANUP_QUARANTINE[0]
    partial.close()
    assert not broker_module._OUTPUT_CLEANUP_QUARANTINE


def test_mapping_beyond_page_rounded_backing_is_rejected_by_windows() -> None:
    with WindowsBoundedOutputBuffer.create(mmap.PAGESIZE) as output:
        assert _map_write(output.write_handle_value, output.capacity + 1) == 0


def test_non_page_aligned_backing_has_less_than_one_page_of_bounded_slack() -> None:
    logical_capacity = mmap.PAGESIZE + 1
    rounded_capacity = mmap.PAGESIZE * 2
    with WindowsBoundedOutputBuffer.create(logical_capacity) as output:
        view = _map_write(output.write_handle_value, rounded_capacity)
        assert view > 0
        assert _unmap(view)
        assert _map_write(output.write_handle_value, rounded_capacity + 1) == 0
        with pytest.raises(PdfThumbnailError) as caught:
            output.read_exact(logical_capacity + 1)
    assert caught.value.code is PdfThumbnailErrorCode.RESULT_INVALID

"""Windows primitive tests for the bounded out-of-band inspection channel."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from types import SimpleNamespace

import pytest

from paperworkshell.domain.pdf_inspection import (
    PdfEncryptionState,
    PdfFeatureState,
    PdfFeatureSummary,
    PdfInspectionEnvelope,
    PdfInspectionError,
    PdfInspectionErrorCode,
    PdfInspectionResult,
    PdfOpenState,
    PdfStructuralState,
    decode_pdf_inspection_frame,
    encode_pdf_inspection_frame,
)
from paperworkshell.worker import _windows_broker as broker_module
from paperworkshell.worker._windows_broker import WindowsResultPipe

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Win32 pipe contract")


def _handle_is_open(handle: int) -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_handle_information = kernel32.GetHandleInformation
    get_handle_information.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    )
    get_handle_information.restype = wintypes.BOOL
    flags = wintypes.DWORD()
    return bool(get_handle_information(wintypes.HANDLE(handle), ctypes.byref(flags)))


def _frame() -> bytes:
    from uuid import UUID

    return encode_pdf_inspection_frame(
        PdfInspectionEnvelope(
            UUID("11000000-0000-4000-8000-000000000105"),
            UUID("12000000-0000-4000-8000-000000000105"),
            PdfInspectionResult(
                PdfOpenState.OPENED,
                1,
                6,
                PdfEncryptionState.UNENCRYPTED,
                PdfStructuralState.CLEAN,
                PdfFeatureSummary(*(PdfFeatureState.NOT_DETECTED for _ in range(6))),
            ),
        )
    )


def test_result_pipe_is_non_inheritable_and_reads_one_complete_frame() -> None:
    frame = _frame()
    with WindowsResultPipe.create() as channel:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_handle_information = kernel32.GetHandleInformation
        get_handle_information.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        )
        get_handle_information.restype = wintypes.BOOL
        flags = wintypes.DWORD()
        assert get_handle_information(
            wintypes.HANDLE(channel.write_handle_value),
            ctypes.byref(flags),
        )
        assert flags.value & 1 == 0

        write_file = kernel32.WriteFile
        write_file.argtypes = (
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        )
        write_file.restype = wintypes.BOOL
        storage = ctypes.create_string_buffer(frame)
        written = wintypes.DWORD()
        assert write_file(
            wintypes.HANDLE(channel.write_handle_value),
            storage,
            len(frame),
            ctypes.byref(written),
            None,
        )
        assert written.value == len(frame)
        channel.close_local_writer()

        assert decode_pdf_inspection_frame(channel.read_complete_frame()).result.page_count == 1


def test_empty_result_channel_fails_with_sanitized_error() -> None:
    with WindowsResultPipe.create() as channel:
        channel.close_local_writer()
        with pytest.raises(PdfInspectionError) as caught:
            channel.read_complete_frame()

    assert "handle" not in str(caught.value).lower()


def test_failed_local_writer_close_retains_authority_for_context_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingClose:
        argtypes: object = None
        restype: object = None

        def __call__(self, _handle: object) -> bool:
            return False

    channel = WindowsResultPipe.create()
    writer = channel.write_handle_value
    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(
                broker_module,
                "_kernel32",
                lambda: SimpleNamespace(CloseHandle=_FailingClose()),
            )
            with pytest.raises(PdfInspectionError) as caught:
                channel.close_local_writer()
            assert caught.value.code is PdfInspectionErrorCode.INTERNAL_ERROR
            assert caught.value.context == "broker.pipe_close"
            assert str(writer) not in str(caught.value)
            assert channel.write_handle_value == writer
            assert _handle_is_open(writer)

        channel.close()
        assert not _handle_is_open(writer)
    finally:
        channel.close()

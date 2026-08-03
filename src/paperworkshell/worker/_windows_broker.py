"""Private Win32 result-channel primitives for exact worker-handle brokerage."""

from __future__ import annotations

import ctypes
import os
from contextlib import AbstractContextManager
from ctypes import wintypes
from typing import Any, Final, Self

from paperworkshell.domain.pdf_inspection import (
    MAX_PDF_INSPECTION_FRAME_BYTES,
    PdfInspectionErrorCode,
    reject_pdf_inspection,
)

_ERROR_BROKEN_PIPE: Final = 109
_HANDLE_FLAG_INHERIT: Final = 0x0000_0001
_PIPE_BUFFER_BYTES: Final = 8_192
_READ_CHUNK_BYTES: Final = 1_024


class _SecurityAttributes(ctypes.Structure):
    _fields_ = (
        ("length", wintypes.DWORD),
        ("security_descriptor", ctypes.c_void_p),
        ("inherit_handle", wintypes.BOOL),
    )


def _kernel32() -> Any:
    if os.name != "nt":
        reject_pdf_inspection(PdfInspectionErrorCode.DEPENDENCY_UNAVAILABLE, "broker.platform")
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _close_no_raise(handle: int) -> None:
    if handle == 0:
        return
    try:
        kernel32 = _kernel32()
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        close_handle(wintypes.HANDLE(handle))
    except BaseException:
        pass


class WindowsResultPipe(AbstractContextManager["WindowsResultPipe"]):
    """One bounded local reader and broker-side writer for a worker result."""

    __slots__ = ("_closed", "_read_handle", "_write_handle")

    def __init__(self, read_handle: int, write_handle: int) -> None:
        self._read_handle = read_handle
        self._write_handle = write_handle
        self._closed = False

    def __repr__(self) -> str:
        return "WindowsResultPipe(<redacted>)"

    @classmethod
    def create(cls) -> WindowsResultPipe:
        """Create a non-inheritable anonymous channel with a bounded kernel buffer."""

        kernel32 = _kernel32()
        create_pipe = kernel32.CreatePipe
        create_pipe.argtypes = (
            ctypes.POINTER(wintypes.HANDLE),
            ctypes.POINTER(wintypes.HANDLE),
            ctypes.POINTER(_SecurityAttributes),
            wintypes.DWORD,
        )
        create_pipe.restype = wintypes.BOOL
        read_handle = wintypes.HANDLE()
        write_handle = wintypes.HANDLE()
        security = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), None, False)
        if not create_pipe(
            ctypes.byref(read_handle),
            ctypes.byref(write_handle),
            ctypes.byref(security),
            _PIPE_BUFFER_BYTES,
        ):
            reject_pdf_inspection(PdfInspectionErrorCode.INTERNAL_ERROR, "broker.pipe_create")
        reader = int(read_handle.value) if read_handle.value else 0
        writer = int(write_handle.value) if write_handle.value else 0
        try:
            if reader == 0 or writer == 0:
                reject_pdf_inspection(PdfInspectionErrorCode.INTERNAL_ERROR, "broker.pipe_create")
            set_handle_information = kernel32.SetHandleInformation
            set_handle_information.argtypes = (
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.DWORD,
            )
            set_handle_information.restype = wintypes.BOOL
            for value in (reader, writer):
                if not set_handle_information(
                    wintypes.HANDLE(value),
                    _HANDLE_FLAG_INHERIT,
                    0,
                ):
                    reject_pdf_inspection(
                        PdfInspectionErrorCode.INTERNAL_ERROR,
                        "broker.pipe_inheritance",
                    )
            return cls(reader, writer)
        except BaseException:
            _close_no_raise(writer)
            _close_no_raise(reader)
            raise

    @property
    def write_handle_value(self) -> int:
        """Return the local writer only for reduced-right worker duplication."""

        if self._closed or self._write_handle == 0:
            reject_pdf_inspection(PdfInspectionErrorCode.HANDLE_INVALID, "broker.pipe_writer")
        return self._write_handle

    def close_local_writer(self) -> None:
        """Drop broker write authority after the worker copy is established."""

        if self._write_handle == 0:
            return
        handle = self._write_handle
        kernel32 = _kernel32()
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        if not close_handle(wintypes.HANDLE(handle)):
            reject_pdf_inspection(PdfInspectionErrorCode.INTERNAL_ERROR, "broker.pipe_close")
        self._write_handle = 0

    def read_complete_frame(self) -> bytes:
        """Read through EOF after worker-tree termination and enforce the frame cap."""

        if self._closed or self._read_handle == 0 or self._write_handle != 0:
            reject_pdf_inspection(PdfInspectionErrorCode.HANDLE_INVALID, "broker.pipe_reader")
        kernel32 = _kernel32()
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
        total = 0
        while True:
            buffer = ctypes.create_string_buffer(_READ_CHUNK_BYTES)
            received = wintypes.DWORD()
            ctypes.set_last_error(0)
            succeeded = read_file(
                wintypes.HANDLE(self._read_handle),
                buffer,
                len(buffer),
                ctypes.byref(received),
                None,
            )
            count = int(received.value)
            if not succeeded:
                if ctypes.get_last_error() == _ERROR_BROKEN_PIPE and count == 0:
                    break
                reject_pdf_inspection(PdfInspectionErrorCode.REPORT_INVALID, "broker.pipe_read")
            if count == 0:
                break
            total += count
            if total > MAX_PDF_INSPECTION_FRAME_BYTES:
                reject_pdf_inspection(PdfInspectionErrorCode.REPORT_INVALID, "broker.pipe_limit")
            chunks.append(buffer.raw[:count])
        payload = b"".join(chunks)
        if not payload:
            reject_pdf_inspection(PdfInspectionErrorCode.REPORT_INVALID, "broker.pipe_empty")
        return payload

    def __enter__(self) -> Self:
        if self._closed:
            reject_pdf_inspection(PdfInspectionErrorCode.HANDLE_INVALID, "broker.pipe_closed")
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        writer = self._write_handle
        reader = self._read_handle
        self._write_handle = 0
        self._read_handle = 0
        _close_no_raise(writer)
        _close_no_raise(reader)

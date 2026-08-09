"""Private Win32 result-channel primitives for exact worker-handle brokerage."""

from __future__ import annotations

import ctypes
import os
from collections.abc import Iterator
from contextlib import AbstractContextManager
from ctypes import wintypes
from typing import Any, Final, Self

from paperworkshell.domain.pdf_inspection import (
    MAX_PDF_INSPECTION_FRAME_BYTES,
    PdfInspectionErrorCode,
    reject_pdf_inspection,
)
from paperworkshell.domain.pdf_thumbnails import (
    PdfThumbnailErrorCode,
    reject_pdf_thumbnail,
)

_ERROR_BROKEN_PIPE: Final = 109
_FILE_MAP_READ: Final = 0x0000_0004
_HANDLE_FLAG_INHERIT: Final = 0x0000_0001
_INVALID_HANDLE_VALUE: Final = ctypes.c_void_p(-1).value
_MAX_WINDOWS_SIZE_T: Final = (1 << (ctypes.sizeof(ctypes.c_size_t) * 8)) - 1
_PAGE_READWRITE: Final = 0x0000_0004
_PIPE_BUFFER_BYTES: Final = 8_192
_READ_CHUNK_BYTES: Final = 1_024
_OUTPUT_CLEANUP_QUARANTINE: list[Any] = []


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


def _close_handle(handle: int) -> bool:
    if handle == 0:
        return True
    try:
        kernel32 = _kernel32()
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        return bool(close_handle(wintypes.HANDLE(handle)))
    except BaseException:
        return False


def _close_no_raise(handle: int) -> None:
    _close_handle(handle)


def _unmap_view(view: int) -> bool:
    if view == 0:
        return True
    try:
        kernel32 = _kernel32()
        unmap_view = kernel32.UnmapViewOfFile
        unmap_view.argtypes = (ctypes.c_void_p,)
        unmap_view.restype = wintypes.BOOL
        return bool(unmap_view(ctypes.c_void_p(view)))
    except BaseException:
        return False


def _retain_output_cleanup(value: Any) -> None:
    if all(candidate is not value for candidate in _OUTPUT_CLEANUP_QUARANTINE):
        _OUTPUT_CLEANUP_QUARANTINE.append(value)


def _release_output_cleanup(value: Any) -> None:
    _OUTPUT_CLEANUP_QUARANTINE[:] = [
        candidate for candidate in _OUTPUT_CLEANUP_QUARANTINE if candidate is not value
    ]


def _drain_output_cleanup() -> None:
    for candidate in tuple(_OUTPUT_CLEANUP_QUARANTINE):
        try:
            candidate.close()
        except BaseException:
            pass


class WindowsBoundedOutputBuffer(AbstractContextManager["WindowsBoundedOutputBuffer"]):
    """One fixed-size pagefile section with an exact logical capacity."""

    __slots__ = ("_capacity", "_closed", "_mapping_handle", "_read_view")

    def __init__(self, mapping_handle: int, read_view: int, capacity: int) -> None:
        self._mapping_handle = mapping_handle
        self._read_view = read_view
        self._capacity = capacity
        self._closed = False

    def __repr__(self) -> str:
        return "WindowsBoundedOutputBuffer(<redacted>)"

    @classmethod
    def create(cls, capacity: int) -> WindowsBoundedOutputBuffer:
        """Create an unnamed section from an exact logical, page-rounded size."""

        if type(capacity) is not int or capacity <= 0:
            reject_pdf_thumbnail(
                PdfThumbnailErrorCode.MODEL_INVALID,
                "broker.output_capacity",
            )
        if capacity > _MAX_WINDOWS_SIZE_T:
            reject_pdf_thumbnail(
                PdfThumbnailErrorCode.LIMIT_EXCEEDED,
                "broker.output_capacity",
            )
        if os.name != "nt":
            reject_pdf_thumbnail(
                PdfThumbnailErrorCode.WORKER_FAILED,
                "broker.platform",
            )
        _drain_output_cleanup()
        if _OUTPUT_CLEANUP_QUARANTINE:
            reject_pdf_thumbnail(
                PdfThumbnailErrorCode.DESTINATION_FAILED,
                "broker.output_cleanup_pending",
            )

        kernel32 = _kernel32()
        create_mapping = kernel32.CreateFileMappingW
        create_mapping.argtypes = (
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPCWSTR,
        )
        create_mapping.restype = wintypes.HANDLE
        maximum_high = capacity >> 32
        maximum_low = capacity & 0xFFFF_FFFF
        mapping = create_mapping(
            wintypes.HANDLE(_INVALID_HANDLE_VALUE),
            None,
            _PAGE_READWRITE,
            maximum_high,
            maximum_low,
            None,
        )
        mapping_handle = int(mapping or 0)
        read_view = 0
        try:
            if mapping_handle == 0 or mapping_handle == _INVALID_HANDLE_VALUE:
                reject_pdf_thumbnail(
                    PdfThumbnailErrorCode.DESTINATION_FAILED,
                    "broker.output_create",
                )

            set_handle_information = kernel32.SetHandleInformation
            set_handle_information.argtypes = (
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.DWORD,
            )
            set_handle_information.restype = wintypes.BOOL
            if not set_handle_information(
                wintypes.HANDLE(mapping_handle),
                _HANDLE_FLAG_INHERIT,
                0,
            ):
                reject_pdf_thumbnail(
                    PdfThumbnailErrorCode.INTERNAL_ERROR,
                    "broker.output_inheritance",
                )

            map_view = kernel32.MapViewOfFile
            map_view.argtypes = (
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.c_size_t,
            )
            map_view.restype = ctypes.c_void_p
            mapped = map_view(
                wintypes.HANDLE(mapping_handle),
                _FILE_MAP_READ,
                0,
                0,
                capacity,
            )
            read_view = int(mapped or 0)
            if read_view == 0:
                reject_pdf_thumbnail(
                    PdfThumbnailErrorCode.DESTINATION_FAILED,
                    "broker.output_map",
                )
            return cls(mapping_handle, read_view, capacity)
        except BaseException:
            cleanup_handle = (
                mapping_handle if mapping_handle not in {0, _INVALID_HANDLE_VALUE} else 0
            )
            if cleanup_handle != 0 or read_view != 0:
                partial = cls(cleanup_handle, read_view, capacity)
                partial.close()
            raise

    @property
    def capacity(self) -> int:
        """Return the exact logical cap enforced by the broker and worker adapter."""

        return self._capacity

    @property
    def write_handle_value(self) -> int:
        """Return the local section handle for reduced-right worker duplication."""

        if self._closed or self._mapping_handle == 0:
            reject_pdf_thumbnail(
                PdfThumbnailErrorCode.INTERNAL_ERROR,
                "broker.output_handle",
            )
        return self._mapping_handle

    def read_exact(self, byte_count: int) -> bytes:
        """Copy exactly the trusted post-termination byte count from the broker view."""

        if self._closed or self._read_view == 0:
            reject_pdf_thumbnail(
                PdfThumbnailErrorCode.INTERNAL_ERROR,
                "broker.output_reader",
            )
        if type(byte_count) is not int or not 0 <= byte_count <= self._capacity:
            reject_pdf_thumbnail(
                PdfThumbnailErrorCode.RESULT_INVALID,
                "broker.output_byte_count",
            )
        try:
            return ctypes.string_at(self._read_view, byte_count)
        except MemoryError:
            reject_pdf_thumbnail(
                PdfThumbnailErrorCode.LIMIT_EXCEEDED,
                "broker.output_read",
            )
        except OSError, OverflowError, ValueError:
            reject_pdf_thumbnail(
                PdfThumbnailErrorCode.RESULT_INVALID,
                "broker.output_read",
            )

    def iter_chunks(
        self,
        byte_count: int,
        *,
        chunk_bytes: int = 1024 * 1024,
    ) -> Iterator[bytes]:
        """Yield an exact trusted prefix without copying the complete output at once."""

        if self._closed or self._read_view == 0:
            reject_pdf_thumbnail(
                PdfThumbnailErrorCode.INTERNAL_ERROR,
                "broker.output_reader",
            )
        if (
            type(byte_count) is not int
            or not 0 <= byte_count <= self._capacity
            or type(chunk_bytes) is not int
            or not 1 <= chunk_bytes <= 16 * 1024 * 1024
        ):
            reject_pdf_thumbnail(
                PdfThumbnailErrorCode.RESULT_INVALID,
                "broker.output_byte_count",
            )
        offset = 0
        try:
            while offset < byte_count:
                size = min(chunk_bytes, byte_count - offset)
                yield ctypes.string_at(self._read_view + offset, size)
                offset += size
        except MemoryError:
            reject_pdf_thumbnail(
                PdfThumbnailErrorCode.LIMIT_EXCEEDED,
                "broker.output_read",
            )
        except OSError, OverflowError, ValueError:
            reject_pdf_thumbnail(
                PdfThumbnailErrorCode.RESULT_INVALID,
                "broker.output_read",
            )

    def __enter__(self) -> Self:
        if self._closed:
            reject_pdf_thumbnail(
                PdfThumbnailErrorCode.INTERNAL_ERROR,
                "broker.output_closed",
            )
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception, traceback
        del exception_type
        self.close()

    def close(self) -> None:
        """Unmap the broker view before releasing the section handle."""

        if self._closed:
            _release_output_cleanup(self)
            return
        read_view = self._read_view
        mapping_handle = self._mapping_handle
        unmapped = _unmap_view(read_view)
        handle_closed = _close_handle(mapping_handle)
        if unmapped:
            self._read_view = 0
        if handle_closed:
            self._mapping_handle = 0
        self._closed = unmapped and handle_closed
        if not self._closed:
            _retain_output_cleanup(self)
            reject_pdf_thumbnail(
                PdfThumbnailErrorCode.DESTINATION_FAILED,
                "broker.output_cleanup",
            )
        _release_output_cleanup(self)


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

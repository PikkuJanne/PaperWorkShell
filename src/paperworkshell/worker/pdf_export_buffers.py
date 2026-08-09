"""Least-privilege bounded memory sections for one PDF-export worker launch."""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Final, Never, Self

from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    reject_pdf_export,
)
from paperworkshell.domain.pdf_thumbnails import PdfThumbnailError
from paperworkshell.worker._windows_broker import WindowsBoundedOutputBuffer
from paperworkshell.worker.protocol.models import MAX_PDF_EXPORT_PLAN_BYTES

_FILE_MAP_WRITE: Final = 0x0000_0002
_FILE_MAP_READ: Final = 0x0000_0004
_HANDLE_FLAG_INHERIT: Final = 0x0000_0001
_INVALID_HANDLE_VALUE: Final = ctypes.c_void_p(-1).value
_MAX_WINDOWS_SIZE_T: Final = (1 << (ctypes.sizeof(ctypes.c_size_t) * 8)) - 1
_PAGE_READWRITE: Final = 0x0000_0004
_COPY_CHUNK_BYTES: Final = 1024 * 1024
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")


def _kernel32() -> Any:
    if os.name != "nt":
        reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.buffer_platform")
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _close_handle_no_raise(handle: int) -> bool:
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


def _unmap_no_raise(view: int) -> bool:
    if view == 0:
        return True
    try:
        kernel32 = _kernel32()
        unmap = kernel32.UnmapViewOfFile
        unmap.argtypes = (ctypes.c_void_p,)
        unmap.restype = wintypes.BOOL
        return bool(unmap(ctypes.c_void_p(view)))
    except BaseException:
        return False


def _require_handle(value: object, *, context: str) -> int:
    if (
        type(value) is not int
        or not 0 < value <= _MAX_WINDOWS_SIZE_T
        or value == _INVALID_HANDLE_VALUE
    ):
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, context)
    return value


def _require_non_inheritable(handle: int, *, context: str) -> None:
    kernel32 = _kernel32()
    get_information = kernel32.GetHandleInformation
    get_information.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    get_information.restype = wintypes.BOOL
    flags = wintypes.DWORD()
    if not get_information(wintypes.HANDLE(handle), ctypes.byref(flags)) or (
        int(flags.value) & _HANDLE_FLAG_INHERIT
    ):
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, context)


def _require_digest(value: object, *, context: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, context)
    return value


def _checkpoint(callback: Callable[[], object]) -> None:
    try:
        result = callback()
    except PdfExportError:
        raise
    except BaseException:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.buffer_checkpoint")
    if result is not None:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.buffer_checkpoint")


class WindowsPdfExportPlanBuffer(AbstractContextManager["WindowsPdfExportPlanBuffer"]):
    """Broker-owned exact-capacity plan section transferred read-only to the worker."""

    __slots__ = ("_closed", "_mapping_handle", "_payload_size")

    def __init__(self, mapping_handle: int, payload_size: int) -> None:
        self._mapping_handle = mapping_handle
        self._payload_size = payload_size
        self._closed = False

    def __repr__(self) -> str:
        return "WindowsPdfExportPlanBuffer(<redacted>)"

    @classmethod
    def create(cls, payload: bytes) -> WindowsPdfExportPlanBuffer:
        """Copy one bounded plan into a fresh unnamed, non-inheritable section."""

        if type(payload) is not bytes or not 1 <= len(payload) <= MAX_PDF_EXPORT_PLAN_BYTES:
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.plan_buffer")
        if len(payload) > _MAX_WINDOWS_SIZE_T:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.plan_buffer")
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
        size = len(payload)
        mapping = create_mapping(
            wintypes.HANDLE(_INVALID_HANDLE_VALUE),
            None,
            _PAGE_READWRITE,
            size >> 32,
            size & 0xFFFF_FFFF,
            None,
        )
        mapping_handle = int(mapping or 0)
        view = 0
        try:
            if mapping_handle in {0, _INVALID_HANDLE_VALUE}:
                reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.plan_create")
            set_information = kernel32.SetHandleInformation
            set_information.argtypes = (
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.DWORD,
            )
            set_information.restype = wintypes.BOOL
            if not set_information(
                wintypes.HANDLE(mapping_handle),
                _HANDLE_FLAG_INHERIT,
                0,
            ):
                reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.plan_inheritance")
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
                _FILE_MAP_WRITE,
                0,
                0,
                size,
            )
            view = int(mapped or 0)
            if view == 0:
                reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.plan_map")
            source = ctypes.create_string_buffer(payload)
            ctypes.memmove(view, source, size)
            if not _flush_view(view, size):
                reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.plan_flush")
            if not _unmap_no_raise(view):
                view = 0
                reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.plan_unmap")
            view = 0
            return cls(mapping_handle, size)
        except BaseException:
            _unmap_no_raise(view)
            _close_handle_no_raise(
                mapping_handle if mapping_handle not in {0, _INVALID_HANDLE_VALUE} else 0
            )
            raise

    @property
    def payload_size(self) -> int:
        return self._payload_size

    @property
    def read_handle_value(self) -> int:
        if self._closed:
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.plan_closed")
        return self._mapping_handle

    def __enter__(self) -> Self:
        if self._closed:
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.plan_closed")
        return self

    def __exit__(self, *args: object) -> None:
        del args
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        if not _close_handle_no_raise(self._mapping_handle):
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.plan_cleanup")
        self._mapping_handle = 0
        self._closed = True


class WindowsPdfExportOutputBuffer(AbstractContextManager["WindowsPdfExportOutputBuffer"]):
    """Export-typed wrapper around the existing exact-capacity output section."""

    __slots__ = ("_buffer", "_closed")

    def __init__(self, buffer: WindowsBoundedOutputBuffer) -> None:
        self._buffer = buffer
        self._closed = False

    def __repr__(self) -> str:
        return "WindowsPdfExportOutputBuffer(<redacted>)"

    @classmethod
    def create(cls, capacity: int) -> WindowsPdfExportOutputBuffer:
        try:
            return cls(WindowsBoundedOutputBuffer.create(capacity))
        except PdfThumbnailError as error:
            _map_thumbnail_buffer_error(error, "export.output_create")

    @property
    def capacity(self) -> int:
        return self._buffer.capacity

    @property
    def write_handle_value(self) -> int:
        try:
            return self._buffer.write_handle_value
        except PdfThumbnailError as error:
            _map_thumbnail_buffer_error(error, "export.output_handle")

    def iter_chunks(
        self,
        byte_count: int,
        *,
        chunk_bytes: int = _COPY_CHUNK_BYTES,
    ) -> Iterator[bytes]:
        try:
            yield from self._buffer.iter_chunks(byte_count, chunk_bytes=chunk_bytes)
        except PdfThumbnailError as error:
            _map_thumbnail_buffer_error(error, "export.output_read")

    def __enter__(self) -> Self:
        if self._closed:
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.output_closed")
        return self

    def __exit__(self, *args: object) -> None:
        del args
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._buffer.close()
        except PdfThumbnailError as error:
            _map_thumbnail_buffer_error(error, "export.output_cleanup")
        self._closed = True


@dataclass(frozen=True, slots=True)
class PdfExportSectionWriteResult:
    """Exact worker output facts bound to bytes in the broker-owned section."""

    byte_size: int
    sha256: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.byte_size) is not int
            or self.byte_size < 1
            or type(self.sha256) is not str
            or _SHA256.fullmatch(self.sha256) is None
        ):
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.section_result")


def read_pdf_export_plan_section(
    handle: int,
    *,
    byte_count: int,
    expected_sha256: str,
) -> bytes:
    """Worker-side exact plan read that always consumes its reduced-right handle."""

    selected_handle = _require_handle(handle, context="export.plan_handle")
    expected_digest = _require_digest(expected_sha256, context="export.plan_digest")
    if type(byte_count) is not int or not 1 <= byte_count <= MAX_PDF_EXPORT_PLAN_BYTES:
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.plan_size")
    view = 0
    cleanup_ok = True
    try:
        _require_non_inheritable(selected_handle, context="export.plan_inheritance")
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
        mapped = map_view(
            wintypes.HANDLE(selected_handle),
            _FILE_MAP_READ,
            0,
            0,
            byte_count,
        )
        view = int(mapped or 0)
        if view == 0:
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.plan_map")
        try:
            payload = ctypes.string_at(view, byte_count)
        except MemoryError:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.plan_read")
        except OSError, OverflowError, ValueError:
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.plan_read")
        if not hashlib.sha256(payload).hexdigest() == expected_digest:
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.plan_digest")
        return payload
    finally:
        cleanup_ok = _unmap_no_raise(view) and cleanup_ok
        cleanup_ok = _close_handle_no_raise(selected_handle) and cleanup_ok
        if not cleanup_ok:
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.plan_cleanup")


def write_pdf_export_output_section(
    handle: int,
    source: BinaryIO,
    *,
    byte_count: int,
    expected_sha256: str,
    capacity: int,
    checkpoint: Callable[[], object] = lambda: None,
) -> PdfExportSectionWriteResult:
    """Worker-side exact verified-output copy that always consumes its handle."""

    selected_handle = _require_handle(handle, context="export.output_handle")
    expected_digest = _require_digest(expected_sha256, context="export.output_digest")
    if (
        not hasattr(source, "read")
        or type(byte_count) is not int
        or type(capacity) is not int
        or not 1 <= byte_count <= capacity <= _MAX_WINDOWS_SIZE_T
        or not callable(checkpoint)
    ):
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.output_copy")
    view = 0
    cleanup_ok = True
    result: PdfExportSectionWriteResult | None = None
    try:
        _require_non_inheritable(selected_handle, context="export.output_inheritance")
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
        mapped = map_view(
            wintypes.HANDLE(selected_handle),
            _FILE_MAP_WRITE,
            0,
            0,
            capacity,
        )
        view = int(mapped or 0)
        if view == 0:
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.output_map")
        digest = hashlib.sha256()
        offset = 0
        while offset < byte_count:
            _checkpoint(checkpoint)
            requested = min(_COPY_CHUNK_BYTES, byte_count - offset)
            try:
                block = source.read(requested)
            except Exception:
                reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.output_read")
            if type(block) is not bytes or not block or len(block) > requested:
                reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.output_read")
            buffer = ctypes.create_string_buffer(block)
            ctypes.memmove(view + offset, buffer, len(block))
            digest.update(block)
            offset += len(block)
        _checkpoint(checkpoint)
        try:
            surplus = source.read(1)
        except Exception:
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.output_eof")
        if type(surplus) is not bytes or surplus:
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.output_eof")
        actual_digest = digest.hexdigest()
        if actual_digest != expected_digest:
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.output_digest")
        if not _flush_view(view, byte_count):
            reject_pdf_export(PdfExportErrorCode.DESTINATION_FAILED, "export.output_flush")
        _checkpoint(checkpoint)
        result = PdfExportSectionWriteResult(byte_count, actual_digest)
    finally:
        cleanup_ok = _unmap_no_raise(view) and cleanup_ok
        cleanup_ok = _close_handle_no_raise(selected_handle) and cleanup_ok
        if not cleanup_ok:
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.output_cleanup")
    if result is None:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.output_result")
    return result


def _flush_view(view: int, byte_count: int) -> bool:
    kernel32 = _kernel32()
    flush = kernel32.FlushViewOfFile
    flush.argtypes = (ctypes.c_void_p, ctypes.c_size_t)
    flush.restype = wintypes.BOOL
    try:
        return bool(flush(ctypes.c_void_p(view), byte_count))
    except BaseException:
        return False


def _map_thumbnail_buffer_error(error: PdfThumbnailError, context: str) -> Never:
    code = (
        PdfExportErrorCode.LIMIT_EXCEEDED
        if error.code.value.endswith("LIMIT_EXCEEDED")
        else PdfExportErrorCode.RESULT_INVALID
    )
    reject_pdf_export(code, context)

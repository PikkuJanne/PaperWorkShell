"""Least-privilege immutable input section for one OCR quality sample."""

from __future__ import annotations

import ctypes
import os
from contextlib import AbstractContextManager
from ctypes import wintypes
from typing import Any, Final, Self

from paperworkshell.domain.quality_ocr_review import (
    QualityOcrReviewErrorCode,
    reject_quality_ocr_review,
)

_FILE_MAP_WRITE: Final = 0x0000_0002
_HANDLE_FLAG_INHERIT: Final = 0x0000_0001
_INVALID_HANDLE_VALUE: Final = ctypes.c_void_p(-1).value
_MAX_WINDOWS_SIZE_T: Final = (1 << (ctypes.sizeof(ctypes.c_size_t) * 8)) - 1
_PAGE_READWRITE: Final = 0x0000_0004
_INPUT_CLEANUP_QUARANTINE: list[Any] = []


class WindowsQualityOcrInputBuffer(AbstractContextManager["WindowsQualityOcrInputBuffer"]):
    """One exact unnamed section copied once and transferred read-only."""

    __slots__ = ("_closed", "_mapping_handle", "_payload_size", "_write_view")

    def __init__(self, mapping_handle: int, payload_size: int, write_view: int = 0) -> None:
        self._mapping_handle = mapping_handle
        self._payload_size = payload_size
        self._write_view = write_view
        self._closed = False

    def __repr__(self) -> str:
        return "WindowsQualityOcrInputBuffer(<redacted>)"

    @classmethod
    def create(cls, payload: bytes) -> WindowsQualityOcrInputBuffer:
        """Copy one bounded raster into a fresh non-inheritable section."""

        if type(payload) is not bytes or not payload or len(payload) > _MAX_WINDOWS_SIZE_T:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.MODEL_INVALID,
                "quality_ocr.input_buffer",
            )
        drain_quality_ocr_input_cleanup()
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
                reject_quality_ocr_review(
                    QualityOcrReviewErrorCode.INTERNAL_ERROR,
                    "quality_ocr.input_create",
                )
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
                reject_quality_ocr_review(
                    QualityOcrReviewErrorCode.INTERNAL_ERROR,
                    "quality_ocr.input_inheritance",
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
                _FILE_MAP_WRITE,
                0,
                0,
                size,
            )
            view = int(mapped or 0)
            if view == 0:
                reject_quality_ocr_review(
                    QualityOcrReviewErrorCode.INTERNAL_ERROR,
                    "quality_ocr.input_map",
                )
            source = ctypes.create_string_buffer(payload)
            ctypes.memmove(view, source, size)
            if not _flush_view(view, size):
                reject_quality_ocr_review(
                    QualityOcrReviewErrorCode.INTERNAL_ERROR,
                    "quality_ocr.input_flush",
                )
            if not _unmap_view(view):
                reject_quality_ocr_review(
                    QualityOcrReviewErrorCode.INTERNAL_ERROR,
                    "quality_ocr.input_unmap",
                )
            view = 0
            return cls(mapping_handle, size)
        except BaseException:
            cleanup_handle = (
                mapping_handle if mapping_handle not in {0, _INVALID_HANDLE_VALUE} else 0
            )
            if cleanup_handle != 0 or view != 0:
                cls(cleanup_handle, size, view).close()
            raise

    @property
    def payload_size(self) -> int:
        return self._payload_size

    @property
    def read_handle_value(self) -> int:
        if self._closed or self._mapping_handle <= 0:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.INTERNAL_ERROR,
                "quality_ocr.input_handle",
            )
        return self._mapping_handle

    def __enter__(self) -> Self:
        if self._closed:
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.INTERNAL_ERROR,
                "quality_ocr.input_closed",
            )
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            _release_input_cleanup(self)
            return
        handle = self._mapping_handle
        view = self._write_view
        unmapped = _unmap_view(view)
        handle_closed = _close_handle(handle)
        if unmapped:
            self._write_view = 0
        if handle_closed:
            self._mapping_handle = 0
        self._closed = unmapped and handle_closed
        if not self._closed:
            _retain_input_cleanup(self)
            reject_quality_ocr_review(
                QualityOcrReviewErrorCode.INTERNAL_ERROR,
                "quality_ocr.input_cleanup",
            )
        _release_input_cleanup(self)


def _retain_input_cleanup(value: Any) -> None:
    if all(candidate is not value for candidate in _INPUT_CLEANUP_QUARANTINE):
        _INPUT_CLEANUP_QUARANTINE.append(value)


def _release_input_cleanup(value: Any) -> None:
    _INPUT_CLEANUP_QUARANTINE[:] = [
        candidate for candidate in _INPUT_CLEANUP_QUARANTINE if candidate is not value
    ]


def _drain_input_cleanup() -> None:
    for candidate in tuple(_INPUT_CLEANUP_QUARANTINE):
        try:
            candidate.close()
        except BaseException:
            pass


def drain_quality_ocr_input_cleanup() -> None:
    """Make one bounded retry pass and refuse while any raster authority remains."""

    _drain_input_cleanup()
    if _INPUT_CLEANUP_QUARANTINE:
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.INTERNAL_ERROR,
            "quality_ocr.input_cleanup_pending",
        )


def _kernel32() -> Any:
    if os.name != "nt":
        reject_quality_ocr_review(
            QualityOcrReviewErrorCode.ENGINE_UNAVAILABLE,
            "quality_ocr.platform",
        )
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _flush_view(view: int, size: int) -> bool:
    kernel32 = _kernel32()
    flush = kernel32.FlushViewOfFile
    flush.argtypes = (ctypes.c_void_p, ctypes.c_size_t)
    flush.restype = wintypes.BOOL
    return bool(flush(ctypes.c_void_p(view), size))


def _unmap_view(view: int) -> bool:
    if view <= 0:
        return True
    try:
        kernel32 = _kernel32()
        unmap = kernel32.UnmapViewOfFile
        unmap.argtypes = (ctypes.c_void_p,)
        unmap.restype = wintypes.BOOL
        return bool(unmap(ctypes.c_void_p(view)))
    except BaseException:
        return False


def _close_handle(handle: int) -> bool:
    if handle <= 0 or os.name != "nt":
        return True
    try:
        kernel32 = _kernel32()
        close = kernel32.CloseHandle
        close.argtypes = (wintypes.HANDLE,)
        close.restype = wintypes.BOOL
        return bool(close(wintypes.HANDLE(handle)))
    except BaseException:
        return False

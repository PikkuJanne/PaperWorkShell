"""Exact read-only RGBA section mapping for flattened-PDF worker inputs."""

from __future__ import annotations

import ctypes
import hashlib
import hmac
import os
from collections.abc import Callable
from contextlib import AbstractContextManager
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Any, Final, Self

from paperworkshell.overlays.exporting import (
    FlattenedPdfExportError,
    FlattenedPdfExportErrorCode,
    reject_flattened_pdf_export,
)
from paperworkshell.overlays.rendering import MAX_OVERLAY_RENDER_BYTES, OverlayRasterAsset

_FILE_MAP_WRITE: Final = 0x0002
_FILE_MAP_READ: Final = 0x0004
_HANDLE_FLAG_INHERIT: Final = 0x0001
_INVALID_HANDLE_VALUE: Final = ctypes.c_void_p(-1).value
_PAGE_READWRITE: Final = 0x0004
_HASH_CHUNK_BYTES: Final = 1024 * 1024
_MAX_WINDOWS_SIZE_T: Final = (1 << (ctypes.sizeof(ctypes.c_size_t) * 8)) - 1
_RASTER_CLEANUP_QUARANTINE: list[Any] = []


class WindowsFlattenedPdfRasterBuffer(AbstractContextManager["WindowsFlattenedPdfRasterBuffer"]):
    """Broker-owned exact RGBA section transferred to the worker read-only."""

    __slots__ = (
        "_closed",
        "_mapping_handle",
        "_payload_sha256",
        "_payload_size",
        "_write_view",
    )

    def __init__(
        self,
        mapping_handle: int,
        payload_size: int,
        payload_sha256: str,
        write_view: int = 0,
    ) -> None:
        self._mapping_handle = mapping_handle
        self._payload_size = payload_size
        self._payload_sha256 = payload_sha256
        self._write_view = write_view
        self._closed = False

    def __repr__(self) -> str:
        return "WindowsFlattenedPdfRasterBuffer(<redacted>)"

    @classmethod
    def create(cls, payload: bytes) -> WindowsFlattenedPdfRasterBuffer:
        """Copy exact bounded RGBA8 bytes into a fresh unnamed section."""

        if (
            type(payload) is not bytes
            or not 4 <= len(payload) <= min(_MAX_WINDOWS_SIZE_T, MAX_OVERLAY_RENDER_BYTES)
            or len(payload) % 4 != 0
        ):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.MODEL_INVALID,
                "flattened.raster_buffer",
            )
        drain_flattened_pdf_raster_cleanup()
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
        digest = hashlib.sha256(payload).hexdigest()
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
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                    "flattened.raster_create",
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
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                    "flattened.raster_inheritance",
                )
            view = _map_write_section(mapping_handle, size)
            if view <= 0:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                    "flattened.raster_write_map",
                )
            source = ctypes.create_string_buffer(payload)
            ctypes.memmove(view, source, size)
            if not _flush_view(view, size):
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                    "flattened.raster_flush",
                )
            if not _unmap_view_no_raise(view):
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                    "flattened.raster_unmap",
                )
            view = 0
            return cls(mapping_handle, size, digest)
        except BaseException:
            cleanup_handle = (
                mapping_handle if mapping_handle not in {0, _INVALID_HANDLE_VALUE} else 0
            )
            if cleanup_handle != 0 or view != 0:
                cls(cleanup_handle, size, digest, view).close()
            raise

    @property
    def payload_size(self) -> int:
        return self._payload_size

    @property
    def payload_sha256(self) -> str:
        return self._payload_sha256

    @property
    def read_handle_value(self) -> int:
        if self._closed or self._mapping_handle <= 0:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                "flattened.raster_handle",
            )
        return self._mapping_handle

    def __enter__(self) -> Self:
        if self._closed:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                "flattened.raster_closed",
            )
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            _release_raster_cleanup(self)
            return
        unmapped = _unmap_view_no_raise(self._write_view)
        handle_closed = _close_native_handle_no_raise(self._mapping_handle)
        if unmapped:
            self._write_view = 0
        if handle_closed:
            self._mapping_handle = 0
        self._closed = unmapped and handle_closed
        if not self._closed:
            _retain_raster_cleanup(self)
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                "flattened.raster_cleanup",
            )
        _release_raster_cleanup(self)


def _retain_raster_cleanup(value: Any) -> None:
    if all(candidate is not value for candidate in _RASTER_CLEANUP_QUARANTINE):
        _RASTER_CLEANUP_QUARANTINE.append(value)


def _release_raster_cleanup(value: Any) -> None:
    _RASTER_CLEANUP_QUARANTINE[:] = [
        candidate for candidate in _RASTER_CLEANUP_QUARANTINE if candidate is not value
    ]


def drain_flattened_pdf_raster_cleanup() -> None:
    """Retry each retained raster authority once and refuse on residue."""

    for candidate in tuple(_RASTER_CLEANUP_QUARANTINE):
        try:
            candidate.close()
        except BaseException:
            pass
    if _RASTER_CLEANUP_QUARANTINE:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.INTERNAL_ERROR,
            "flattened.raster_cleanup_pending",
        )


@dataclass(slots=True, repr=False)
class FlattenedPdfRasterSection:
    """Own one authenticated, seekable, read-only worker section view."""

    _handle: int
    _view: int
    metadata: OverlayRasterAsset
    _position: int = 0
    _closed: bool = field(default=False, init=False)

    @classmethod
    def adopt(
        cls,
        handle: int,
        metadata: OverlayRasterAsset,
        *,
        expected_byte_size: int,
        expected_sha256: str,
        checkpoint: Callable[[], object] = lambda: None,
    ) -> FlattenedPdfRasterSection:
        """Map, authenticate, and own one reduced-right section handle."""

        if (
            type(handle) is not int
            or handle <= 0
            or type(metadata) is not OverlayRasterAsset
            or type(expected_byte_size) is not int
            or expected_byte_size != metadata.byte_count
            or not 1 <= expected_byte_size <= _MAX_WINDOWS_SIZE_T
            or type(expected_sha256) is not str
            or not hmac.compare_digest(expected_sha256, metadata.pixel_sha256)
            or not callable(checkpoint)
        ):
            _close_native_handle_no_raise(handle)
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.RASTER_METADATA_INVALID,
                "flattened.raster_section",
            )
        view = 0
        try:
            _require_non_inheritable(handle)
            view = _map_read_section(handle, expected_byte_size)
            if view <= 0:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.RESULT_INVALID,
                    "flattened.raster_map",
                )
            section = cls(handle, view, metadata)
            section._authenticate(checkpoint)
            return section
        except FlattenedPdfExportError:
            cleanup_ok = _unmap_view_no_raise(view)
            cleanup_ok = _close_native_handle_no_raise(handle) and cleanup_ok
            if not cleanup_ok:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                    "flattened.raster_cleanup",
                )
            raise
        except MemoryError, RecursionError:
            _unmap_view_no_raise(view)
            _close_native_handle_no_raise(handle)
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.raster_authentication",
            )
        except Exception:
            cleanup_ok = _unmap_view_no_raise(view)
            cleanup_ok = _close_native_handle_no_raise(handle) and cleanup_ok
            if not cleanup_ok:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                    "flattened.raster_cleanup",
                )
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.RESULT_INVALID,
                "flattened.raster_map",
            )

    @property
    def closed(self) -> bool:
        return self._closed

    def readable(self) -> bool:
        return not self._closed

    def writable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return not self._closed

    def read(self, size: int = -1, /) -> bytes:
        self._require_open()
        if type(size) is not int or size < -1:
            raise OSError from None
        remaining = self.metadata.byte_count - self._position
        wanted = remaining if size < 0 else min(size, remaining)
        if wanted == 0:
            return b""
        try:
            payload = ctypes.string_at(self._view + self._position, wanted)
        except MemoryError:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.raster_read",
            )
        except Exception:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.RESULT_INVALID,
                "flattened.raster_read",
            )
        self._position += len(payload)
        return payload

    def seek(self, offset: int, whence: int = os.SEEK_SET, /) -> int:
        self._require_open()
        if type(offset) is not int or type(whence) is not int:
            raise OSError from None
        if whence == os.SEEK_SET:
            target = offset
        elif whence == os.SEEK_CUR:
            target = self._position + offset
        elif whence == os.SEEK_END:
            target = self.metadata.byte_count + offset
        else:
            raise OSError from None
        if not 0 <= target <= self.metadata.byte_count:
            raise OSError from None
        self._position = target
        return target

    def tell(self) -> int:
        self._require_open()
        return self._position

    def flush(self) -> None:
        self._require_open()

    def close(self) -> None:
        if self._closed:
            return
        view = self._view
        handle = self._handle
        self._view = 0
        self._handle = 0
        self._position = 0
        self._closed = True
        cleanup_ok = _unmap_view_no_raise(view)
        cleanup_ok = _close_native_handle_no_raise(handle) and cleanup_ok
        if not cleanup_ok:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                "flattened.raster_cleanup",
            )

    def __enter__(self) -> FlattenedPdfRasterSection:
        self._require_open()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _authenticate(self, checkpoint: Callable[[], object]) -> None:
        digest = hashlib.sha256()
        offset = 0
        while offset < self.metadata.byte_count:
            _checkpoint(checkpoint)
            count = min(_HASH_CHUNK_BYTES, self.metadata.byte_count - offset)
            try:
                block = ctypes.string_at(self._view + offset, count)
            except MemoryError:
                raise
            except Exception as error:
                raise OSError from error
            if type(block) is not bytes or len(block) != count:
                raise OSError
            digest.update(block)
            offset += count
        _checkpoint(checkpoint)
        if not hmac.compare_digest(digest.hexdigest(), self.metadata.pixel_sha256):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.ASSET_CHANGED,
                "flattened.raster_digest",
            )

    def _require_open(self) -> None:
        if self._closed or self._view <= 0:
            raise OSError from None


def _checkpoint(callback: Callable[[], object]) -> None:
    try:
        callback()
    except FlattenedPdfExportError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.INTERNAL_ERROR,
            "flattened.raster_checkpoint",
        )


def _require_non_inheritable(handle: int) -> None:
    try:
        inherited = os.get_handle_inheritable(handle)
    except OSError, OverflowError, ValueError:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RESULT_INVALID,
            "flattened.raster_handle",
        )
    if inherited:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RESULT_INVALID,
            "flattened.raster_handle",
        )


def _kernel32() -> Any:
    if os.name != "nt":
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.INTERNAL_ERROR,
            "flattened.platform",
        )
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _map_read_section(handle: int, byte_count: int) -> int:
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
    mapped = map_view(wintypes.HANDLE(handle), _FILE_MAP_READ, 0, 0, byte_count)
    return int(mapped or 0)


def _map_write_section(handle: int, byte_count: int) -> int:
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
    mapped = map_view(wintypes.HANDLE(handle), _FILE_MAP_WRITE, 0, 0, byte_count)
    return int(mapped or 0)


def _flush_view(view: int, byte_count: int) -> bool:
    kernel32 = _kernel32()
    flush = kernel32.FlushViewOfFile
    flush.argtypes = (ctypes.c_void_p, ctypes.c_size_t)
    flush.restype = wintypes.BOOL
    return bool(flush(ctypes.c_void_p(view), byte_count))


def _unmap_view_no_raise(view: int) -> bool:
    if type(view) is not int or view <= 0:
        return True
    try:
        kernel32 = _kernel32()
        unmap = kernel32.UnmapViewOfFile
        unmap.argtypes = (ctypes.c_void_p,)
        unmap.restype = wintypes.BOOL
        return bool(unmap(ctypes.c_void_p(view)))
    except BaseException:
        return False


def _close_native_handle_no_raise(handle: int) -> bool:
    if type(handle) is not int or handle <= 0:
        return True
    if os.name != "nt":
        return False
    try:
        kernel32 = _kernel32()
        close = kernel32.CloseHandle
        close.argtypes = (wintypes.HANDLE,)
        close.restype = wintypes.BOOL
        return bool(close(wintypes.HANDLE(handle)))
    except BaseException:
        return False

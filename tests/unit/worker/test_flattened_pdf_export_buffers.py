"""Windows tests for immutable flattened-export RGBA input sections."""

from __future__ import annotations

import ctypes
import hashlib
import os
from ctypes import wintypes
from typing import Any

import pytest

import paperworkshell.worker.flattened_pdf_export_buffers as buffers
from paperworkshell.overlays.exporting import (
    FlattenedPdfExportError,
    FlattenedPdfExportErrorCode,
)
from paperworkshell.overlays.rendering import OverlayRasterAsset
from paperworkshell.worker.flattened_pdf_export_buffers import (
    FlattenedPdfRasterSection,
    WindowsFlattenedPdfRasterBuffer,
    drain_flattened_pdf_raster_cleanup,
)
from tests.unit.worker.flattened_pdf_export_fixtures import uuid4

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows sections are required")

_FILE_MAP_READ = 0x0004
_FILE_MAP_WRITE = 0x0002


def _kernel32() -> Any:
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _duplicate_read_handle(handle: int) -> int:
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
    target = wintypes.HANDLE()
    assert duplicate(
        current_process(),
        wintypes.HANDLE(handle),
        current_process(),
        ctypes.byref(target),
        _FILE_MAP_READ,
        False,
        0,
    )
    assert target.value is not None
    return int(target.value)


def _metadata(payload: bytes, *, digest: str | None = None) -> OverlayRasterAsset:
    return OverlayRasterAsset(
        asset_id=uuid4(5),
        width_pixels=2,
        height_pixels=2,
        row_stride_bytes=8,
        byte_count=len(payload),
        pixel_sha256=hashlib.sha256(payload).hexdigest() if digest is None else digest,
    )


def test_raster_buffer_and_worker_view_round_trip_exact_read_only_bytes() -> None:
    payload = bytes(range(16))

    with WindowsFlattenedPdfRasterBuffer.create(payload) as owner:
        assert owner.payload_size == len(payload)
        assert owner.payload_sha256 == hashlib.sha256(payload).hexdigest()
        worker_handle = _duplicate_read_handle(owner.read_handle_value)
        with FlattenedPdfRasterSection.adopt(
            worker_handle,
            _metadata(payload),
            expected_byte_size=len(payload),
            expected_sha256=hashlib.sha256(payload).hexdigest(),
        ) as section:
            assert section.read(5) == payload[:5]
            assert section.tell() == 5
            assert section.seek(-2, os.SEEK_END) == 14
            assert section.read() == payload[14:]
            assert section.seek(0) == 0
            assert section.read() == payload
            assert buffers._map_write_section(worker_handle, len(payload)) == 0

    drain_flattened_pdf_raster_cleanup()


def test_worker_view_rejects_digest_mismatch_and_consumes_its_handle() -> None:
    payload = bytes(range(16))

    with WindowsFlattenedPdfRasterBuffer.create(payload) as owner:
        worker_handle = _duplicate_read_handle(owner.read_handle_value)
        with pytest.raises(FlattenedPdfExportError) as captured:
            FlattenedPdfRasterSection.adopt(
                worker_handle,
                _metadata(payload, digest="f" * 64),
                expected_byte_size=len(payload),
                expected_sha256="f" * 64,
            )

    assert captured.value.code is FlattenedPdfExportErrorCode.ASSET_CHANGED


@pytest.mark.parametrize("payload", (b"", b"abc", b"12345"))
def test_raster_buffer_rejects_non_rgba_payload_lengths(payload: bytes) -> None:
    with pytest.raises(FlattenedPdfExportError) as captured:
        WindowsFlattenedPdfRasterBuffer.create(payload)

    assert captured.value.code is FlattenedPdfExportErrorCode.MODEL_INVALID


def test_raster_cleanup_quarantine_retries_and_drains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buffer = WindowsFlattenedPdfRasterBuffer(101, 4, "0" * 64)
    results = iter((False, True))
    monkeypatch.setattr(buffers, "_close_native_handle_no_raise", lambda _handle: next(results))

    with pytest.raises(FlattenedPdfExportError):
        buffer.close()
    drain_flattened_pdf_raster_cleanup()

    assert buffer._closed

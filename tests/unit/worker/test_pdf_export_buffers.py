"""Windows tests for bounded out-of-band PDF-export plan and output sections."""

from __future__ import annotations

import ctypes
import hashlib
import io
import os
from ctypes import wintypes

import pytest

from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    reject_pdf_export,
)
from paperworkshell.worker.pdf_export_buffers import (
    WindowsPdfExportOutputBuffer,
    WindowsPdfExportPlanBuffer,
    read_pdf_export_plan_section,
    write_pdf_export_output_section,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows memory sections are required")

_SECTION_MAP_WRITE = 0x0000_0002
_SECTION_MAP_READ = 0x0000_0004


def _duplicate_local_handle(handle: int, *, access: int) -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
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
        access,
        False,
        0,
    )
    assert target.value is not None
    return int(target.value)


def test_plan_section_round_trip_is_exact_redacted_and_parent_owned() -> None:
    payload = b'{"plan_version":1,"synthetic":"section-canary"}'
    digest = hashlib.sha256(payload).hexdigest()

    with WindowsPdfExportPlanBuffer.create(payload) as plan:
        worker_handle = _duplicate_local_handle(
            plan.read_handle_value,
            access=_SECTION_MAP_READ,
        )
        received = read_pdf_export_plan_section(
            worker_handle,
            byte_count=len(payload),
            expected_sha256=digest,
        )

        assert received == payload
        assert plan.payload_size == len(payload)
        assert "section-canary" not in repr(plan)
        assert plan.read_handle_value > 0


@pytest.mark.parametrize(
    "payload",
    (b"", b"x" * (2 * 1024 * 1024 + 1)),
    ids=("empty", "too_large"),
)
def test_plan_section_rejects_invalid_capacity(payload: bytes) -> None:
    with pytest.raises(PdfExportError) as raised:
        WindowsPdfExportPlanBuffer.create(payload)

    assert raised.value.code in {
        PdfExportErrorCode.MODEL_INVALID,
        PdfExportErrorCode.LIMIT_EXCEEDED,
    }


def test_plan_section_digest_mismatch_fails_closed() -> None:
    payload = b"synthetic-plan"
    with WindowsPdfExportPlanBuffer.create(payload) as plan:
        worker_handle = _duplicate_local_handle(
            plan.read_handle_value,
            access=_SECTION_MAP_READ,
        )

        with pytest.raises(PdfExportError) as raised:
            read_pdf_export_plan_section(
                worker_handle,
                byte_count=len(payload),
                expected_sha256="0" * 64,
            )

    assert raised.value.code is PdfExportErrorCode.RESULT_INVALID


def test_output_section_streams_exact_verified_prefix_in_bounded_chunks() -> None:
    payload = bytes(range(251)) * 23
    digest = hashlib.sha256(payload).hexdigest()
    capacity = len(payload) + 4096

    with WindowsPdfExportOutputBuffer.create(capacity) as output:
        worker_handle = _duplicate_local_handle(
            output.write_handle_value,
            access=_SECTION_MAP_WRITE,
        )
        result = write_pdf_export_output_section(
            worker_handle,
            io.BytesIO(payload),
            byte_count=len(payload),
            expected_sha256=digest,
            capacity=capacity,
        )

        chunks = tuple(output.iter_chunks(len(payload), chunk_bytes=127))

    assert result.byte_size == len(payload)
    assert result.sha256 == digest
    assert len(chunks) > 1
    assert b"".join(chunks) == payload


@pytest.mark.parametrize(
    ("source", "byte_count", "digest"),
    (
        (io.BytesIO(b"short"), 10, hashlib.sha256(b"short").hexdigest()),
        (io.BytesIO(b"surplus"), 6, hashlib.sha256(b"surplu").hexdigest()),
        (io.BytesIO(b"digest"), 6, "0" * 64),
    ),
)
def test_output_section_rejects_short_surplus_or_digest_mismatch(
    source: io.BytesIO,
    byte_count: int,
    digest: str,
) -> None:
    with WindowsPdfExportOutputBuffer.create(64) as output:
        worker_handle = _duplicate_local_handle(
            output.write_handle_value,
            access=_SECTION_MAP_WRITE,
        )

        with pytest.raises(PdfExportError) as raised:
            write_pdf_export_output_section(
                worker_handle,
                source,
                byte_count=byte_count,
                expected_sha256=digest,
                capacity=64,
            )

    assert raised.value.code is PdfExportErrorCode.RESULT_INVALID


def test_output_section_honours_a_typed_cancellation_checkpoint() -> None:
    payload = b"x" * (1024 * 1024 + 1)
    calls = 0

    def cancel_after_first_chunk() -> None:
        nonlocal calls
        calls += 1
        if calls > 1:
            reject_pdf_export(PdfExportErrorCode.CANCELLED, "export.buffer_test")

    with WindowsPdfExportOutputBuffer.create(len(payload)) as output:
        worker_handle = _duplicate_local_handle(
            output.write_handle_value,
            access=_SECTION_MAP_WRITE,
        )

        with pytest.raises(PdfExportError) as raised:
            write_pdf_export_output_section(
                worker_handle,
                io.BytesIO(payload),
                byte_count=len(payload),
                expected_sha256=hashlib.sha256(payload).hexdigest(),
                capacity=len(payload),
                checkpoint=cancel_after_first_chunk,
            )

    assert raised.value.code is PdfExportErrorCode.CANCELLED
    assert calls == 2

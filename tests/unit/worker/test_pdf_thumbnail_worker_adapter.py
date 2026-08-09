"""Focused Windows tests for the production PDF-thumbnail worker adapter."""

from __future__ import annotations

import ctypes
import hashlib
import os
import tempfile
from collections.abc import Callable, Iterator
from ctypes import wintypes
from pathlib import Path
from threading import Event
from typing import Any, BinaryIO, cast
from uuid import UUID

import pytest

import paperworkshell.worker.pdf_thumbnail_operation as thumbnail_operation
from paperworkshell.domain.pdf_rendering import (
    PdfAnnotationMode,
    PdfPageBox,
    PdfPageRenderLimits,
    PdfPageRenderRequest,
    PdfPageRenderResult,
    PdfRasterBackground,
    PdfRasterFormat,
    PdfRenderError,
    PdfRenderErrorCode,
    PdfRenderRotation,
)
from paperworkshell.domain.pdf_thumbnails import (
    PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES,
    PdfThumbnailError,
    PdfThumbnailErrorCode,
    decode_pdf_thumbnail_artifact,
    decode_pdf_thumbnail_artifact_header,
)
from paperworkshell.pdf.pdfium_renderer import (
    EXPECTED_PDFIUM_BUILD,
    EXPECTED_PDFIUM_VERSION,
    EXPECTED_PYPDFIUM2_VERSION,
    PDFIUM_RENDERER_VERSION,
)
from paperworkshell.pdf.renderer import PdfPageRenderer, PdfRenderControl
from paperworkshell.worker._windows_broker import WindowsBoundedOutputBuffer
from paperworkshell.worker.operations import OperationContext, PdfThumbnailRenderRequest
from paperworkshell.worker.pdf_thumbnail_operation import WindowsPdfThumbnailAdapter
from paperworkshell.worker.pdf_thumbnail_profile import (
    PDF_THUMBNAIL_RENDERER_PROFILE_SHA256,
    PDF_THUMBNAIL_RENDERER_PROFILE_VERSION,
)
from paperworkshell.worker.protocol.models import (
    PDF_THUMBNAIL_CONTENT_KIND,
    CaseObjectReference,
    ReferenceArea,
    ResourceLimits,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Win32 handle contract")

_GENERIC_READ = 0x8000_0000
_GENERIC_WRITE = 0x4000_0000
_FILE_SHARE_READ = 0x0000_0001
_FILE_SHARE_WRITE = 0x0000_0002
_FILE_SHARE_DELETE = 0x0000_0004
_CREATE_ALWAYS = 2
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x0000_0080
_DUPLICATE_SAME_ACCESS = 0x0000_0002
_SECTION_MAP_WRITE = 0x0000_0002
_DEFAULT_OUTPUT_CAPACITY = 4 * 1024 * 1024

_SOURCE_PAYLOAD = b"%PDF-1.4\nsynthetic worker-adapter bytes\n%%EOF\n"
_OPAQUE_PIXELS = bytes((0x10, 0x20, 0x30, 0xFF, 0x40, 0x50, 0x60, 0xFF))
_ACTIVE_OUTPUTS: list[WindowsBoundedOutputBuffer] = []


def _uuid(value: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{value:012x}")


@pytest.fixture(autouse=True)
def _worker_private_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    local_root = tmp_path / "worker-profile"
    temp_root = local_root / "Temp"
    monkeypatch.setenv("LOCALAPPDATA", os.fspath(local_root))
    monkeypatch.setenv("TEMP", os.fspath(temp_root))
    monkeypatch.setenv("TMP", os.fspath(temp_root))
    yield
    for output in reversed(_ACTIVE_OUTPUTS):
        output.close()
    _ACTIVE_OUTPUTS.clear()


def _kernel32() -> Any:
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _open_file_handle(path: Path, *, access: int, creation: int) -> int:
    kernel32 = _kernel32()
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    raw = create_file(
        os.fspath(path),
        access,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        None,
        creation,
        _FILE_ATTRIBUTE_NORMAL,
        None,
    )
    value = int(raw) if raw else 0
    assert value > 0
    assert not os.get_handle_inheritable(value)
    return value


def _duplicate_local_handle(handle: int, *, access: int | None = None) -> int:
    kernel32 = _kernel32()
    current_process = kernel32.GetCurrentProcess
    current_process.argtypes = ()
    current_process.restype = wintypes.HANDLE
    duplicate_handle = kernel32.DuplicateHandle
    duplicate_handle.argtypes = (
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    )
    duplicate_handle.restype = wintypes.BOOL
    process = current_process()
    duplicate = wintypes.HANDLE()
    assert duplicate_handle(
        process,
        wintypes.HANDLE(handle),
        process,
        ctypes.byref(duplicate),
        0 if access is None else access,
        False,
        _DUPLICATE_SAME_ACCESS if access is None else 0,
    )
    value = int(duplicate.value) if duplicate.value else 0
    assert value > 0
    assert not os.get_handle_inheritable(value)
    return value


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


def _close_if_open(handle: int) -> None:
    if not _handle_is_open(handle):
        return
    kernel32 = _kernel32()
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    assert close_handle(wintypes.HANDLE(handle))


def _brokered_handles(
    tmp_path: Path,
    payload: bytes = _SOURCE_PAYLOAD,
    *,
    output_capacity: int = _DEFAULT_OUTPUT_CAPACITY,
) -> tuple[WindowsBoundedOutputBuffer, int, int]:
    source_path = tmp_path / "source.pws"
    source_path.write_bytes(payload)
    source_authority = _open_file_handle(
        source_path,
        access=_GENERIC_READ,
        creation=_OPEN_EXISTING,
    )
    output = WindowsBoundedOutputBuffer.create(output_capacity)
    source_handle = 0
    output_handle = 0
    try:
        source_handle = _duplicate_local_handle(source_authority)
        output_handle = _duplicate_local_handle(
            output.write_handle_value,
            access=_SECTION_MAP_WRITE,
        )
        _ACTIVE_OUTPUTS.append(output)
        return output, source_handle, output_handle
    except BaseException:
        _close_if_open(source_handle)
        _close_if_open(output_handle)
        output.close()
        raise
    finally:
        _close_if_open(source_authority)


def _source_reference(
    payload: bytes = _SOURCE_PAYLOAD,
    *,
    expected_size: int | None = None,
    expected_digest: str | None = None,
) -> CaseObjectReference:
    return CaseObjectReference(
        area=ReferenceArea.SOURCE_READ,
        object_id=_uuid(2),
        operation_id=None,
        content_kind="pdf",
        expected_byte_size=len(payload) if expected_size is None else expected_size,
        expected_sha256=(
            hashlib.sha256(payload).hexdigest() if expected_digest is None else expected_digest
        ),
    )


def _context(
    source: CaseObjectReference,
    *,
    cancellation: Event | None = None,
    deadline_expired: bool = False,
    memory_bytes: int = 64 * 1024 * 1024,
    temporary_storage_bytes: int = 16 * 1024 * 1024,
    output_bytes: int = 4 * 1024 * 1024,
    page_count: int = 100,
    pixel_count: int = 1_000_000,
) -> OperationContext:
    return OperationContext(
        operation_id=_uuid(3),
        case_id=_uuid(1),
        limits=ResourceLimits(
            wall_time_ms=10_000,
            cpu_time_ms=5_000,
            memory_bytes=memory_bytes,
            temporary_storage_bytes=temporary_storage_bytes,
            output_bytes=output_bytes,
            page_count=page_count,
            pixel_count=pixel_count,
        ),
        cancel_requested=Event() if cancellation is None else cancellation,
        report_progress=lambda _progress: None,
        deadline_expired=lambda: deadline_expired,
        inputs=(source,),
    )


def _request(source_handle: int, cache_handle: int) -> PdfThumbnailRenderRequest:
    return PdfThumbnailRenderRequest(
        source_handle=source_handle,
        cache_handle=cache_handle,
        cache_object_id=_uuid(4),
        page_id=_uuid(5),
        source_page_index=7,
        maximum_width_pixels=32,
        maximum_height_pixels=24,
        page_box=PdfPageBox.CROP,
        additional_rotation=PdfRenderRotation.DEGREES_270,
    )


class _SyntheticRenderer:
    def __init__(
        self,
        *,
        pixels: bytes = _OPAQUE_PIXELS,
        width_pixels: int = 2,
        height_pixels: int = 1,
        result_page_index: int | None = None,
        result_digest: str | None = None,
        bytes_to_write: bytes | None = None,
        failure: PdfRenderError | None = None,
        nonresult: object | None = None,
        render_probe: Callable[[], None] | None = None,
    ) -> None:
        self.pixels = pixels
        self.width_pixels = width_pixels
        self.height_pixels = height_pixels
        self.result_page_index = result_page_index
        self.result_digest = result_digest
        self.bytes_to_write = pixels if bytes_to_write is None else bytes_to_write
        self.failure = failure
        self.nonresult = nonresult
        self.render_probe = render_probe
        self.calls = 0
        self.source_payloads: list[bytes] = []
        self.requests: list[PdfPageRenderRequest] = []
        self.limits: list[PdfPageRenderLimits] = []
        self.controls: list[PdfRenderControl] = []

    def render(
        self,
        source: BinaryIO,
        destination: BinaryIO,
        request: PdfPageRenderRequest,
        limits: PdfPageRenderLimits,
        control: PdfRenderControl,
    ) -> PdfPageRenderResult:
        self.calls += 1
        if self.render_probe is not None:
            self.render_probe()
        self.source_payloads.append(source.read())
        self.requests.append(request)
        self.limits.append(limits)
        self.controls.append(control)
        if self.failure is not None:
            raise self.failure
        if self.nonresult is not None:
            return cast(PdfPageRenderResult, self.nonresult)
        destination.write(self.bytes_to_write)
        destination.flush()
        return PdfPageRenderResult(
            page_index=(
                request.page_index if self.result_page_index is None else self.result_page_index
            ),
            width_pixels=self.width_pixels,
            height_pixels=self.height_pixels,
            row_stride_bytes=self.width_pixels * 4,
            bytes_written=len(self.pixels),
            pixel_sha256=(
                hashlib.sha256(self.pixels).hexdigest()
                if self.result_digest is None
                else self.result_digest
            ),
            page_box=request.page_box,
            intrinsic_rotation=PdfRenderRotation.DEGREES_90,
            additional_rotation=request.additional_rotation,
        )


def _adapter(renderer: _SyntheticRenderer) -> WindowsPdfThumbnailAdapter:
    return WindowsPdfThumbnailAdapter(renderer=cast(PdfPageRenderer, renderer))


def _assert_consumed(source_handle: int, cache_handle: int) -> None:
    assert not _handle_is_open(source_handle)
    assert not _handle_is_open(cache_handle)


def test_adapter_discard_consumes_both_brokered_handles(tmp_path: Path) -> None:
    _cache_path, source_handle, cache_handle = _brokered_handles(tmp_path)

    try:
        WindowsPdfThumbnailAdapter().discard_handles(source_handle, cache_handle)
        _assert_consumed(source_handle, cache_handle)
    finally:
        _close_if_open(source_handle)
        _close_if_open(cache_handle)


def test_adapter_writes_one_fixed_authenticated_artifact_and_reference(tmp_path: Path) -> None:
    cache_path, source_handle, cache_handle = _brokered_handles(tmp_path)
    native_authority_state: list[tuple[bool, bool]] = []
    renderer = _SyntheticRenderer(
        render_probe=lambda: native_authority_state.append(
            (_handle_is_open(source_handle), _handle_is_open(cache_handle))
        )
    )
    context = _context(_source_reference())
    request = _request(source_handle, cache_handle)

    try:
        reference = _adapter(renderer).render_to_cache(context, request)
        _assert_consumed(source_handle, cache_handle)
        assert reference.expected_byte_size is not None
        encoded = cache_path.read_exact(reference.expected_byte_size)
    finally:
        _close_if_open(source_handle)
        _close_if_open(cache_handle)

    assert reference == CaseObjectReference(
        area=ReferenceArea.OPERATION_STAGING,
        object_id=request.cache_object_id,
        operation_id=context.operation_id,
        content_kind=PDF_THUMBNAIL_CONTENT_KIND,
        expected_byte_size=len(encoded),
        expected_sha256=hashlib.sha256(encoded).hexdigest(),
    )
    artifact = decode_pdf_thumbnail_artifact(
        encoded,
        expected_operation_id=context.operation_id,
        expected_cache_object_id=request.cache_object_id,
    )
    assert artifact.payload == _OPAQUE_PIXELS
    assert artifact.header.cache_key.case_id == context.case_id
    assert artifact.header.cache_key.source_id == context.inputs[0].object_id
    assert artifact.header.cache_key.page_id == request.page_id
    assert artifact.header.cache_key.renderer_profile_sha256 == (
        PDF_THUMBNAIL_RENDERER_PROFILE_SHA256
    )
    assert artifact.header.width_pixels == 2
    assert artifact.header.height_pixels == 1
    assert artifact.header.intrinsic_rotation is PdfRenderRotation.DEGREES_90
    assert native_authority_state == [(False, True)]
    assert renderer.source_payloads == [_SOURCE_PAYLOAD]
    assert renderer.requests == [
        PdfPageRenderRequest(
            page_index=request.source_page_index,
            maximum_width_pixels=request.maximum_width_pixels,
            maximum_height_pixels=request.maximum_height_pixels,
            page_box=PdfPageBox.CROP,
            additional_rotation=request.additional_rotation,
        )
    ]
    limits = renderer.limits[0]
    assert limits.maximum_pixels == context.limits.pixel_count
    assert limits.maximum_pages == context.limits.page_count
    assert limits.maximum_destination_bytes == (
        context.limits.output_bytes - PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES
    )


def test_adapter_maps_exact_declared_output_capacity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_capacity = PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES + 64
    output, source_handle, cache_handle = _brokered_handles(
        tmp_path,
        output_capacity=output_capacity,
    )
    original_map = thumbnail_operation._map_output_section
    mapped_capacities: list[int] = []

    def map_probe(handle: int, capacity: int) -> int:
        mapped_capacities.append(capacity)
        return original_map(handle, capacity)

    monkeypatch.setattr(thumbnail_operation, "_map_output_section", map_probe)
    context = _context(_source_reference(), output_bytes=output_capacity)
    request = _request(source_handle, cache_handle)
    try:
        reference = _adapter(_SyntheticRenderer()).render_to_cache(context, request)
        _assert_consumed(source_handle, cache_handle)
    finally:
        _close_if_open(source_handle)
        _close_if_open(cache_handle)

    assert mapped_capacities == [output_capacity]
    assert reference.expected_byte_size is not None
    decode_pdf_thumbnail_artifact(output.read_exact(reference.expected_byte_size))


def test_adapter_cannot_map_beyond_os_enforced_section_capacity(tmp_path: Path) -> None:
    section_capacity = 4_096
    _output, source_handle, cache_handle = _brokered_handles(
        tmp_path,
        output_capacity=section_capacity,
    )
    renderer = _SyntheticRenderer()

    try:
        with pytest.raises(PdfThumbnailError) as raised:
            _adapter(renderer).render_to_cache(
                _context(_source_reference(), output_bytes=section_capacity + 1),
                _request(source_handle, cache_handle),
            )
        _assert_consumed(source_handle, cache_handle)
    finally:
        _close_if_open(source_handle)
        _close_if_open(cache_handle)

    assert raised.value.code is PdfThumbnailErrorCode.DESTINATION_FAILED
    assert raised.value.context == "thumbnail.output_map"
    assert renderer.calls == 0


def test_adapter_rejects_file_handle_instead_of_output_section(tmp_path: Path) -> None:
    output, source_handle, section_handle = _brokered_handles(tmp_path)
    _close_if_open(section_handle)
    file_path = tmp_path / "not-a-section.pws"
    cache_handle = _open_file_handle(
        file_path,
        access=_GENERIC_WRITE,
        creation=_CREATE_ALWAYS,
    )

    try:
        with pytest.raises(PdfThumbnailError) as raised:
            _adapter(_SyntheticRenderer()).render_to_cache(
                _context(_source_reference()),
                _request(source_handle, cache_handle),
            )
        _assert_consumed(source_handle, cache_handle)
    finally:
        _close_if_open(source_handle)
        _close_if_open(cache_handle)

    assert raised.value.code is PdfThumbnailErrorCode.DESTINATION_FAILED
    assert raised.value.context == "thumbnail.output_map"
    assert file_path.read_bytes() == b""
    assert _output_is_empty(output)


def test_adapter_sanitizes_output_mapping_failure_and_consumes_handles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output, source_handle, cache_handle = _brokered_handles(tmp_path)
    monkeypatch.setattr(thumbnail_operation, "_map_output_section", lambda *_args: 0)

    try:
        with pytest.raises(PdfThumbnailError) as raised:
            _adapter(_SyntheticRenderer()).render_to_cache(
                _context(_source_reference()),
                _request(source_handle, cache_handle),
            )
        _assert_consumed(source_handle, cache_handle)
    finally:
        _close_if_open(source_handle)
        _close_if_open(cache_handle)

    assert raised.value.code is PdfThumbnailErrorCode.DESTINATION_FAILED
    assert raised.value.context == "thumbnail.output_map"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert _output_is_empty(output)


@pytest.mark.parametrize(
    ("fault", "expected_context"),
    (
        ("write", "thumbnail.output_write"),
        ("flush", "thumbnail.output_flush"),
        ("unmap", "thumbnail.output_cleanup"),
        ("close", "thumbnail.output_cleanup"),
    ),
)
def test_adapter_fails_closed_on_sanitized_output_faults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
    expected_context: str,
) -> None:
    _output, source_handle, cache_handle = _brokered_handles(tmp_path)
    if fault == "write":

        def fail_write(_view: int, _offset: int, _payload: bytes) -> None:
            raise OSError("sensitive-output-write-detail")

        monkeypatch.setattr(thumbnail_operation, "_copy_to_output_view", fail_write)
    elif fault == "flush":
        monkeypatch.setattr(thumbnail_operation, "_flush_output_view", lambda *_args: False)
    elif fault == "unmap":
        original_unmap = thumbnail_operation._unmap_output_view

        def fail_after_unmap(view: int) -> bool:
            original_unmap(view)
            return False

        monkeypatch.setattr(thumbnail_operation, "_unmap_output_view", fail_after_unmap)
    else:
        original_close = thumbnail_operation._close_native_handle

        def fail_after_close(handle: int) -> bool:
            original_close(handle)
            return False

        monkeypatch.setattr(thumbnail_operation, "_close_native_handle", fail_after_close)

    try:
        with pytest.raises(PdfThumbnailError) as raised:
            _adapter(_SyntheticRenderer()).render_to_cache(
                _context(_source_reference()),
                _request(source_handle, cache_handle),
            )
        _assert_consumed(source_handle, cache_handle)
    finally:
        _close_if_open(source_handle)
        _close_if_open(cache_handle)

    assert raised.value.code is PdfThumbnailErrorCode.DESTINATION_FAILED
    assert raised.value.context == expected_context
    assert "sensitive-output-write-detail" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.parametrize(
    ("expected_size", "expected_digest"),
    ((len(_SOURCE_PAYLOAD) + 1, None), (None, "0" * 64)),
)
def test_adapter_rejects_source_size_or_digest_misbinding_and_consumes_both_handles(
    tmp_path: Path,
    expected_size: int | None,
    expected_digest: str | None,
) -> None:
    cache_path, source_handle, cache_handle = _brokered_handles(tmp_path)
    renderer = _SyntheticRenderer()
    source = _source_reference(
        expected_size=len(_SOURCE_PAYLOAD) if expected_size is None else expected_size,
        expected_digest=(
            hashlib.sha256(_SOURCE_PAYLOAD).hexdigest()
            if expected_digest is None
            else expected_digest
        ),
    )

    try:
        with pytest.raises(PdfThumbnailError) as raised:
            _adapter(renderer).render_to_cache(
                _context(source),
                _request(source_handle, cache_handle),
            )
        _assert_consumed(source_handle, cache_handle)
    finally:
        _close_if_open(source_handle)
        _close_if_open(cache_handle)

    assert raised.value.code is PdfThumbnailErrorCode.SOURCE_INVALID
    assert renderer.calls == 0
    assert _output_is_empty(cache_path)


@pytest.mark.parametrize(
    ("cancelled", "deadline_expired", "expected_code"),
    (
        (True, False, PdfThumbnailErrorCode.CANCELLED),
        (False, True, PdfThumbnailErrorCode.TIMED_OUT),
    ),
)
def test_adapter_checks_terminal_state_before_render_and_consumes_both_handles(
    tmp_path: Path,
    cancelled: bool,
    deadline_expired: bool,
    expected_code: PdfThumbnailErrorCode,
) -> None:
    cache_path, source_handle, cache_handle = _brokered_handles(tmp_path)
    renderer = _SyntheticRenderer()
    cancellation = Event()
    if cancelled:
        cancellation.set()

    try:
        with pytest.raises(PdfThumbnailError) as raised:
            _adapter(renderer).render_to_cache(
                _context(
                    _source_reference(),
                    cancellation=cancellation,
                    deadline_expired=deadline_expired,
                ),
                _request(source_handle, cache_handle),
            )
        _assert_consumed(source_handle, cache_handle)
    finally:
        _close_if_open(source_handle)
        _close_if_open(cache_handle)

    assert raised.value.code is expected_code
    assert renderer.calls == 0
    assert _output_is_empty(cache_path)


@pytest.mark.parametrize(
    ("output_bytes", "temporary_storage_bytes"),
    (
        (PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES, 16 * 1024 * 1024),
        (4 * 1024 * 1024, len(_SOURCE_PAYLOAD)),
    ),
)
def test_adapter_rejects_exhausted_output_or_temporary_capacity_before_render(
    tmp_path: Path,
    output_bytes: int,
    temporary_storage_bytes: int,
) -> None:
    cache_path, source_handle, cache_handle = _brokered_handles(
        tmp_path,
        output_capacity=output_bytes,
    )
    renderer = _SyntheticRenderer()

    try:
        with pytest.raises(PdfThumbnailError) as raised:
            _adapter(renderer).render_to_cache(
                _context(
                    _source_reference(),
                    output_bytes=output_bytes,
                    temporary_storage_bytes=temporary_storage_bytes,
                ),
                _request(source_handle, cache_handle),
            )
        _assert_consumed(source_handle, cache_handle)
    finally:
        _close_if_open(source_handle)
        _close_if_open(cache_handle)

    assert raised.value.code is PdfThumbnailErrorCode.LIMIT_EXCEEDED
    assert renderer.calls == 0
    assert _output_is_empty(cache_path)


def test_second_temporary_file_failure_closes_the_first_and_both_handles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_path, source_handle, cache_handle = _brokered_handles(tmp_path)
    renderer = _SyntheticRenderer()
    original = cast(Callable[..., BinaryIO], tempfile.TemporaryFile)
    opened: list[BinaryIO] = []

    def fail_second(*args: object, **kwargs: object) -> BinaryIO:
        if opened:
            raise OSError("private temporary-file failure")
        temporary = original(*args, **kwargs)
        opened.append(temporary)
        return temporary

    monkeypatch.setattr(
        "paperworkshell.worker.pdf_thumbnail_operation.tempfile.TemporaryFile",
        fail_second,
    )
    try:
        with pytest.raises(PdfThumbnailError) as raised:
            WindowsPdfThumbnailAdapter(cast(PdfPageRenderer, renderer)).render_to_cache(
                _context(_source_reference()),
                _request(source_handle, cache_handle),
            )
        assert raised.value.code is PdfThumbnailErrorCode.LIMIT_EXCEEDED
        assert "private" not in str(raised.value)
        assert len(opened) == 1 and opened[0].closed
        assert not _handle_is_open(source_handle)
        assert not _handle_is_open(cache_handle)
        assert _output_is_empty(cache_path)
        assert renderer.calls == 0
    finally:
        _close_if_open(source_handle)
        _close_if_open(cache_handle)
    assert _output_is_empty(cache_path)


@pytest.mark.parametrize(
    ("output_bytes", "temporary_storage_bytes", "pixel_count", "renderer"),
    (
        (
            PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES + len(_OPAQUE_PIXELS) - 1,
            16 * 1024 * 1024,
            1_000_000,
            _SyntheticRenderer(),
        ),
        (
            4 * 1024 * 1024,
            len(_SOURCE_PAYLOAD) + len(_OPAQUE_PIXELS) - 1,
            1_000_000,
            _SyntheticRenderer(),
        ),
        (
            4 * 1024 * 1024,
            16 * 1024 * 1024,
            1,
            _SyntheticRenderer(),
        ),
    ),
)
def test_adapter_revalidates_renderer_output_against_operation_limits(
    tmp_path: Path,
    output_bytes: int,
    temporary_storage_bytes: int,
    pixel_count: int,
    renderer: _SyntheticRenderer,
) -> None:
    cache_path, source_handle, cache_handle = _brokered_handles(
        tmp_path,
        output_capacity=output_bytes,
    )

    try:
        with pytest.raises(PdfThumbnailError) as raised:
            _adapter(renderer).render_to_cache(
                _context(
                    _source_reference(),
                    output_bytes=output_bytes,
                    temporary_storage_bytes=temporary_storage_bytes,
                    pixel_count=pixel_count,
                ),
                _request(source_handle, cache_handle),
            )
        _assert_consumed(source_handle, cache_handle)
    finally:
        _close_if_open(source_handle)
        _close_if_open(cache_handle)

    assert raised.value.code is PdfThumbnailErrorCode.LIMIT_EXCEEDED
    assert renderer.calls == 1
    assert _reference_was_not_published(cache_path)


def _output_is_empty(output: WindowsBoundedOutputBuffer) -> bool:
    return output.read_exact(PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES) == bytes(
        PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES
    )


def _reference_was_not_published(output: WindowsBoundedOutputBuffer) -> bool:
    """The section may contain untrusted bytes, but no valid artifact may escape."""

    header_bytes = output.read_exact(PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES)
    if header_bytes == bytes(PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES):
        return True
    try:
        header = decode_pdf_thumbnail_artifact_header(header_bytes)
        payload = output.read_exact(PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES + header.payload_bytes)
        decode_pdf_thumbnail_artifact(payload)
    except PdfThumbnailError:
        return True
    return False


@pytest.mark.parametrize(
    ("render_code", "thumbnail_code"),
    (
        (PdfRenderErrorCode.MODEL_INVALID, PdfThumbnailErrorCode.WORKER_FAILED),
        (PdfRenderErrorCode.CANCELLED, PdfThumbnailErrorCode.CANCELLED),
        (PdfRenderErrorCode.TIMED_OUT, PdfThumbnailErrorCode.TIMED_OUT),
        (PdfRenderErrorCode.LIMIT_EXCEEDED, PdfThumbnailErrorCode.LIMIT_EXCEEDED),
        (PdfRenderErrorCode.PAGE_OUT_OF_RANGE, PdfThumbnailErrorCode.PAGE_UNAVAILABLE),
        (PdfRenderErrorCode.SOURCE_INVALID, PdfThumbnailErrorCode.SOURCE_INVALID),
        (PdfRenderErrorCode.GEOMETRY_INVALID, PdfThumbnailErrorCode.SOURCE_INVALID),
        (PdfRenderErrorCode.DESTINATION_FAILED, PdfThumbnailErrorCode.DESTINATION_FAILED),
        (PdfRenderErrorCode.RESULT_INVALID, PdfThumbnailErrorCode.RESULT_INVALID),
        (PdfRenderErrorCode.DEPENDENCY_UNAVAILABLE, PdfThumbnailErrorCode.WORKER_FAILED),
        (PdfRenderErrorCode.DEPENDENCY_UNSAFE, PdfThumbnailErrorCode.WORKER_FAILED),
        (PdfRenderErrorCode.DEPENDENCY_FAILED, PdfThumbnailErrorCode.WORKER_FAILED),
        (PdfRenderErrorCode.INTERNAL_ERROR, PdfThumbnailErrorCode.WORKER_FAILED),
    ),
)
def test_adapter_maps_renderer_failures_and_consumes_both_handles(
    tmp_path: Path,
    render_code: PdfRenderErrorCode,
    thumbnail_code: PdfThumbnailErrorCode,
) -> None:
    cache_path, source_handle, cache_handle = _brokered_handles(tmp_path)
    renderer = _SyntheticRenderer(failure=PdfRenderError(render_code, "render.synthetic"))

    try:
        with pytest.raises(PdfThumbnailError) as raised:
            _adapter(renderer).render_to_cache(
                _context(_source_reference()),
                _request(source_handle, cache_handle),
            )
        _assert_consumed(source_handle, cache_handle)
    finally:
        _close_if_open(source_handle)
        _close_if_open(cache_handle)

    assert raised.value.code is thumbnail_code
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert _output_is_empty(cache_path)


@pytest.mark.parametrize(
    "renderer",
    (
        _SyntheticRenderer(nonresult=object()),
        _SyntheticRenderer(result_page_index=8),
        _SyntheticRenderer(bytes_to_write=_OPAQUE_PIXELS[:-4]),
        _SyntheticRenderer(result_digest="0" * 64),
        _SyntheticRenderer(
            pixels=bytes((0x10, 0x20, 0x30, 0x00)),
            width_pixels=1,
            height_pixels=1,
        ),
    ),
)
def test_adapter_rejects_invalid_result_or_raster_and_consumes_both_handles(
    tmp_path: Path,
    renderer: _SyntheticRenderer,
) -> None:
    _cache_path, source_handle, cache_handle = _brokered_handles(tmp_path)

    try:
        with pytest.raises(PdfThumbnailError) as raised:
            _adapter(renderer).render_to_cache(
                _context(_source_reference()),
                _request(source_handle, cache_handle),
            )
        _assert_consumed(source_handle, cache_handle)
    finally:
        _close_if_open(source_handle)
        _close_if_open(cache_handle)

    assert raised.value.code is PdfThumbnailErrorCode.RESULT_INVALID


def test_renderer_profile_fingerprint_tracks_the_pinned_pdfium_adapter_profile() -> None:
    assert PDF_THUMBNAIL_RENDERER_PROFILE_VERSION == PDFIUM_RENDERER_VERSION == 1
    assert EXPECTED_PYPDFIUM2_VERSION == "5.12.1"
    assert EXPECTED_PDFIUM_VERSION == "152.0.7947.0"
    assert EXPECTED_PDFIUM_BUILD == 7_947
    expected_profile = (
        b"paperworkshell.pdf-thumbnail-profile/v1\x00"
        + f"pypdfium2/{EXPECTED_PYPDFIUM2_VERSION}\x00".encode()
        + f"pdfium/{EXPECTED_PDFIUM_VERSION}\x00".encode()
        + b"crop-box\x00rgba8\x00opaque-white\x00stored-annotation-appearances\x00"
        + b"forms-disabled\x00xfa-disabled\x00"
    )

    assert PDF_THUMBNAIL_RENDERER_PROFILE_SHA256 == hashlib.sha256(expected_profile).hexdigest()
    assert PdfPageBox.CROP.value == "crop"
    assert PdfRasterFormat.RGBA8.value == "rgba8"
    assert PdfRasterBackground.OPAQUE_WHITE.value == "opaque_white"
    assert PdfAnnotationMode.STORED_APPEARANCES.value == "stored_appearances"

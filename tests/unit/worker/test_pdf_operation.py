"""Focused Windows tests for the production ``pdf.inspect`` worker seams."""

from __future__ import annotations

import ctypes
import hashlib
import os
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from threading import Event
from typing import Any, BinaryIO, Never, cast
from uuid import UUID

import pytest

from paperworkshell.domain.pdf_inspection import (
    MAX_PDF_INSPECTION_FRAME_BYTES,
    PdfEncryptionState,
    PdfFeatureState,
    PdfFeatureSummary,
    PdfInspectionError,
    PdfInspectionErrorCode,
    PdfInspectionLimits,
    PdfInspectionResult,
    PdfOpenState,
    PdfStructuralState,
    decode_pdf_inspection_frame,
)
from paperworkshell.pdf.pikepdf_adapter import PikePdfAdapter
from paperworkshell.worker._windows_broker import WindowsResultPipe
from paperworkshell.worker.operations import (
    NoopOperationHandler,
    OperationContext,
    PdfInspectionOperationHandler,
)
from paperworkshell.worker.pdf_operation import (
    WindowsPdfInspectionAdapter,
    WindowsPdfInspectionResultWriter,
    _materialize_worker_private_temp,
    build_pdf_runtime_registry,
)
from paperworkshell.worker.protocol.models import (
    NOOP_CAPABILITY,
    PDF_INSPECT_CAPABILITY,
    PDF_INSPECT_RESULT_HANDLE_PARAMETER,
    PDF_INSPECT_SOURCE_HANDLE_PARAMETER,
    CaseObjectReference,
    OperationOutcome,
    ReferenceArea,
    ResourceLimits,
    ScalarValueType,
    TaggedScalar,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Win32 handle contract")

_GENERIC_READ = 0x8000_0000
_FILE_SHARE_READ = 0x0000_0001
_FILE_SHARE_WRITE = 0x0000_0002
_FILE_SHARE_DELETE = 0x0000_0004
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x0000_0080
_DUPLICATE_SAME_ACCESS = 0x0000_0002


def _uuid(value: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{value:012x}")


def _ordinary_result() -> PdfInspectionResult:
    return PdfInspectionResult(
        open_state=PdfOpenState.OPENED,
        page_count=1,
        object_count=6,
        encryption_state=PdfEncryptionState.UNENCRYPTED,
        structural_state=PdfStructuralState.CLEAN,
        features=PdfFeatureSummary(*(PdfFeatureState.NOT_DETECTED for _ in range(6))),
    )


def _source_reference(
    payload: bytes,
    *,
    expected_size: int | None = None,
    expected_digest: str | None = None,
) -> CaseObjectReference:
    return CaseObjectReference(
        area=ReferenceArea.SOURCE_READ,
        object_id=_uuid(105),
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
    cancel_requested: Event | None = None,
    wall_time_ms: int = 10_000,
    temporary_storage_bytes: int = 16 * 1024 * 1024,
    output_bytes: int = 4_096,
    parameters: tuple[TaggedScalar, ...] = (),
) -> OperationContext:
    return OperationContext(
        operation_id=_uuid(106),
        limits=ResourceLimits(
            wall_time_ms=wall_time_ms,
            cpu_time_ms=5_000,
            memory_bytes=128 * 1024 * 1024,
            temporary_storage_bytes=temporary_storage_bytes,
            output_bytes=output_bytes,
            page_count=100,
            pixel_count=1,
        ),
        cancel_requested=Event() if cancel_requested is None else cancel_requested,
        report_progress=lambda _progress: None,
        inputs=(source,),
        parameters=parameters,
    )


def _handler_context(*, cancelled: bool = False) -> OperationContext:
    cancellation = Event()
    if cancelled:
        cancellation.set()
    return _context(
        _source_reference(b"synthetic-source"),
        cancel_requested=cancellation,
        parameters=(
            TaggedScalar(
                PDF_INSPECT_SOURCE_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                501,
            ),
            TaggedScalar(
                PDF_INSPECT_RESULT_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                503,
            ),
        ),
    )


def _kernel32() -> Any:
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _open_read_handle(path: Path) -> int:
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
        _GENERIC_READ,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        None,
        _OPEN_EXISTING,
        _FILE_ATTRIBUTE_NORMAL,
        None,
    )
    value = int(raw) if raw else 0
    assert value > 0
    assert not os.get_handle_inheritable(value)
    return value


def _duplicate_local_handle(handle: int) -> int:
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
        0,
        False,
        _DUPLICATE_SAME_ACCESS,
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


class _RecordingPikeAdapter:
    def __init__(self, payload: bytes, source_handle: int) -> None:
        self.payload = payload
        self.source_handle = source_handle
        self.calls = 0
        self.limits: PdfInspectionLimits | None = None

    def inspect(
        self,
        stream: BinaryIO,
        *,
        source_bytes: int,
        limits: PdfInspectionLimits,
        deadline: float,
        cancellation_probe: Callable[[], bool],
    ) -> PdfInspectionResult:
        self.calls += 1
        self.limits = limits
        assert not _handle_is_open(self.source_handle)
        assert source_bytes == len(self.payload)
        assert deadline > 0
        assert cancellation_probe() is False
        assert stream.read() == self.payload
        return _ordinary_result()


class _RecordingOperationAdapter:
    def __init__(
        self,
        *,
        failure: BaseException | None = None,
        cancel_after_inspection: bool = False,
    ) -> None:
        self.failure = failure
        self.cancel_after_inspection = cancel_after_inspection
        self.inspection = object()
        self.discarded_sources: list[int] = []
        self.inspected_sources: list[int] = []

    def discard_source(self, source_handle: int) -> None:
        self.discarded_sources.append(source_handle)

    def inspect(self, context: OperationContext, source_handle: int) -> object:
        self.inspected_sources.append(source_handle)
        if self.cancel_after_inspection:
            context.cancel_requested.set()
        if self.failure is not None:
            raise self.failure
        return self.inspection


class _RecordingOperationResultWriter:
    def __init__(self) -> None:
        self.discarded_results: list[int] = []
        self.write_calls: list[tuple[OperationContext, int, object]] = []

    def discard_result(self, result_handle: int) -> None:
        self.discarded_results.append(result_handle)

    def write(
        self,
        context: OperationContext,
        result_handle: int,
        inspection: object,
    ) -> None:
        self.write_calls.append((context, result_handle, inspection))


def _worker_adapter(recording: _RecordingPikeAdapter) -> WindowsPdfInspectionAdapter:
    return WindowsPdfInspectionAdapter(adapter=cast(PikePdfAdapter, recording))


def test_handler_pre_cancel_discards_both_handles_without_inspection_or_write() -> None:
    adapter = _RecordingOperationAdapter()
    writer = _RecordingOperationResultWriter()
    handler = PdfInspectionOperationHandler(adapter, writer)

    result = handler.execute(_handler_context(cancelled=True))

    assert result.outcome is OperationOutcome.CANCELLED
    assert result.error is None
    assert adapter.discarded_sources == [501]
    assert adapter.inspected_sources == []
    assert writer.discarded_results == [503]
    assert writer.write_calls == []


def test_handler_pdf_error_preserves_adapter_source_ownership_and_discards_result() -> None:
    failure = PdfInspectionError(
        PdfInspectionErrorCode.SOURCE_CHANGED,
        "worker.source_verify",
    )
    adapter = _RecordingOperationAdapter(failure=failure)
    writer = _RecordingOperationResultWriter()
    handler = PdfInspectionOperationHandler(adapter, writer)

    with pytest.raises(PdfInspectionError) as raised:
        handler.execute(_handler_context())

    assert raised.value is failure
    assert raised.value.code is PdfInspectionErrorCode.SOURCE_CHANGED
    assert raised.value.context == "worker.source_verify"
    assert adapter.inspected_sources == [501]
    assert adapter.discarded_sources == []
    assert writer.discarded_results == [503]
    assert writer.write_calls == []


def test_handler_unexpected_adapter_failure_discards_result_and_propagates() -> None:
    failure = RuntimeError("synthetic adapter failure")
    adapter = _RecordingOperationAdapter(failure=failure)
    writer = _RecordingOperationResultWriter()
    handler = PdfInspectionOperationHandler(adapter, writer)

    with pytest.raises(RuntimeError) as raised:
        handler.execute(_handler_context())

    assert raised.value is failure
    assert adapter.inspected_sources == [501]
    assert adapter.discarded_sources == []
    assert writer.discarded_results == [503]
    assert writer.write_calls == []


def test_handler_cancellation_after_adapter_success_discards_result_without_write() -> None:
    adapter = _RecordingOperationAdapter(cancel_after_inspection=True)
    writer = _RecordingOperationResultWriter()
    handler = PdfInspectionOperationHandler(adapter, writer)

    result = handler.execute(_handler_context())

    assert result.outcome is OperationOutcome.CANCELLED
    assert result.error is None
    assert adapter.inspected_sources == [501]
    assert adapter.discarded_sources == []
    assert writer.discarded_results == [503]
    assert writer.write_calls == []


def test_adapter_authenticates_source_and_closes_case_handle_before_native_parser(
    tmp_path: Path,
) -> None:
    payload = b"%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF\n"
    source_path = tmp_path / "synthetic.pdf"
    source_path.write_bytes(payload)
    handle = _open_read_handle(source_path)
    recording = _RecordingPikeAdapter(payload, handle)

    try:
        result = _worker_adapter(recording).inspect(
            _context(_source_reference(payload)),
            handle,
        )
    finally:
        _close_if_open(handle)

    assert result == _ordinary_result()
    assert recording.calls == 1
    assert recording.limits is not None
    assert recording.limits.maximum_input_bytes == 16 * 1024 * 1024
    assert recording.limits.maximum_temporary_bytes == 16 * 1024 * 1024
    assert recording.limits.maximum_output_bytes == 4_096
    assert recording.limits.maximum_pages == 100


@pytest.mark.parametrize(
    ("expected_size_delta", "expected_digest"),
    ((1, None), (0, "0" * 64)),
)
def test_adapter_rejects_source_size_or_hash_misbinding_before_parser(
    tmp_path: Path,
    expected_size_delta: int,
    expected_digest: str | None,
) -> None:
    payload = b"%PDF-1.4\n%%EOF\n"
    source_path = tmp_path / "synthetic.pdf"
    source_path.write_bytes(payload)
    handle = _open_read_handle(source_path)
    recording = _RecordingPikeAdapter(payload, handle)
    source = _source_reference(
        payload,
        expected_size=len(payload) + expected_size_delta,
        expected_digest=expected_digest,
    )

    try:
        with pytest.raises(PdfInspectionError) as raised:
            _worker_adapter(recording).inspect(_context(source), handle)
    finally:
        _close_if_open(handle)

    assert raised.value.code is PdfInspectionErrorCode.SOURCE_CHANGED
    assert recording.calls == 0
    assert not _handle_is_open(handle)


def test_adapter_cooperatively_cancels_during_authenticated_copy(tmp_path: Path) -> None:
    payload = b"%PDF-1.4\n%%EOF\n"
    source_path = tmp_path / "synthetic.pdf"
    source_path.write_bytes(payload)
    handle = _open_read_handle(source_path)
    recording = _RecordingPikeAdapter(payload, handle)
    cancelled = Event()
    cancelled.set()

    try:
        with pytest.raises(PdfInspectionError) as raised:
            _worker_adapter(recording).inspect(
                _context(_source_reference(payload), cancel_requested=cancelled),
                handle,
            )
    finally:
        _close_if_open(handle)

    assert raised.value.code is PdfInspectionErrorCode.CANCELLED
    assert recording.calls == 0
    assert not _handle_is_open(handle)


def test_adapter_enforces_one_worker_wall_deadline_during_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"%PDF-1.4\n%%EOF\n"
    source_path = tmp_path / "synthetic.pdf"
    source_path.write_bytes(payload)
    handle = _open_read_handle(source_path)
    recording = _RecordingPikeAdapter(payload, handle)
    calls = 0

    def clock() -> float:
        nonlocal calls
        calls += 1
        return 10.0 if calls == 1 else 11.0

    monkeypatch.setattr("paperworkshell.worker.pdf_operation.time.monotonic", clock)
    try:
        with pytest.raises(PdfInspectionError) as raised:
            _worker_adapter(recording).inspect(
                _context(_source_reference(payload), wall_time_ms=1),
                handle,
            )
    finally:
        _close_if_open(handle)

    assert raised.value.code is PdfInspectionErrorCode.TIMED_OUT
    assert recording.calls == 0
    assert not _handle_is_open(handle)


def test_adapter_rejects_source_larger_than_worker_temporary_limit_and_consumes_handle(
    tmp_path: Path,
) -> None:
    payload = b"%PDF-1.4\n%%EOF\n"
    source_path = tmp_path / "synthetic.pdf"
    source_path.write_bytes(payload)
    handle = _open_read_handle(source_path)
    recording = _RecordingPikeAdapter(payload, handle)

    try:
        with pytest.raises(PdfInspectionError) as raised:
            _worker_adapter(recording).inspect(
                _context(
                    _source_reference(payload),
                    temporary_storage_bytes=len(payload) - 1,
                ),
                handle,
            )
        assert not _handle_is_open(handle)
    finally:
        _close_if_open(handle)

    assert raised.value.code is PdfInspectionErrorCode.LIMIT_EXCEEDED
    assert recording.calls == 0


def test_adapter_closes_adopted_source_and_sanitizes_private_temp_creation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"%PDF-1.4\n%%EOF\n"
    source_path = tmp_path / "synthetic.pdf"
    source_path.write_bytes(payload)
    handle = _open_read_handle(source_path)
    recording = _RecordingPikeAdapter(payload, handle)

    def fail_temporary_file(*_args: object, **_kwargs: object) -> Never:
        raise FileNotFoundError("synthetic private temp failure")

    monkeypatch.setattr(
        "paperworkshell.worker.pdf_operation._materialize_worker_private_temp",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        "paperworkshell.worker.pdf_operation.tempfile.TemporaryFile",
        fail_temporary_file,
    )
    try:
        with pytest.raises(PdfInspectionError) as raised:
            _worker_adapter(recording).inspect(
                _context(_source_reference(payload)),
                handle,
            )
    finally:
        _close_if_open(handle)

    assert raised.value.code is PdfInspectionErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "worker.temp_create"
    assert recording.calls == 0
    assert not _handle_is_open(handle)


def test_private_temp_materialization_accepts_only_the_scoped_non_redirected_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_root = tmp_path / "worker-profile"
    local_root.mkdir()
    temp_root = local_root / "Temp"
    monkeypatch.setenv("LOCALAPPDATA", os.fspath(local_root))
    monkeypatch.setenv("TEMP", os.fspath(temp_root))
    monkeypatch.setenv("TMP", os.fspath(temp_root))

    assert _materialize_worker_private_temp() == temp_root
    assert temp_root.is_dir()

    outside = tmp_path / "outside-temp"
    monkeypatch.setenv("TEMP", os.fspath(outside))
    monkeypatch.setenv("TMP", os.fspath(outside))
    with pytest.raises(PdfInspectionError) as raised:
        _materialize_worker_private_temp()

    assert raised.value.code is PdfInspectionErrorCode.INTERNAL_ERROR
    assert raised.value.context == "worker.temp_scope"
    assert not outside.exists()


def test_result_writer_emits_one_bounded_frame_correlated_to_operation_and_source() -> None:
    result = _ordinary_result()
    source = _source_reference(b"synthetic-source")
    context = _context(source)

    with WindowsResultPipe.create() as channel:
        worker_writer = _duplicate_local_handle(channel.write_handle_value)
        WindowsPdfInspectionResultWriter().write(context, worker_writer, result)
        assert not _handle_is_open(worker_writer)
        channel.close_local_writer()
        frame = channel.read_complete_frame()

    assert len(frame) <= MAX_PDF_INSPECTION_FRAME_BYTES
    envelope = decode_pdf_inspection_frame(frame)
    assert envelope.operation_id == context.operation_id
    assert envelope.source_object_id == source.object_id
    assert envelope.result == result


def test_result_writer_closes_handle_and_rejects_frame_over_operation_output_limit() -> None:
    source = _source_reference(b"synthetic-source")
    context = _context(source, output_bytes=1)

    with WindowsResultPipe.create() as channel:
        worker_writer = _duplicate_local_handle(channel.write_handle_value)
        with pytest.raises(PdfInspectionError) as raised:
            WindowsPdfInspectionResultWriter().write(
                context,
                worker_writer,
                _ordinary_result(),
            )
        assert not _handle_is_open(worker_writer)
        channel.close_local_writer()

    assert raised.value.code is PdfInspectionErrorCode.LIMIT_EXCEEDED


def test_production_registry_wires_real_pdf_adapter_and_result_writer() -> None:
    registry = build_pdf_runtime_registry()

    assert registry.capabilities == (NOOP_CAPABILITY, PDF_INSPECT_CAPABILITY)
    assert type(registry.handler_for(NOOP_CAPABILITY)) is NoopOperationHandler
    handler = registry.handler_for(PDF_INSPECT_CAPABILITY)
    assert type(handler) is PdfInspectionOperationHandler
    assert type(handler.adapter) is WindowsPdfInspectionAdapter
    assert type(handler.result_writer) is WindowsPdfInspectionResultWriter

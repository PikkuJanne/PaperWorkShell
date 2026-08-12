"""Windows composition tests for the production PDF-export worker adapter."""

from __future__ import annotations

import ctypes
import hashlib
import os
import shutil
from collections.abc import Mapping
from ctypes import wintypes
from pathlib import Path
from threading import Event
from typing import Any, BinaryIO, cast
from uuid import UUID

import pytest

from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportPage,
    PdfExportPlan,
    PdfExportRotation,
    PdfExportSource,
    PdfExportSourceSignatureState,
    PdfExportVerificationReport,
)
from paperworkshell.pdf.export_adapter import (
    PdfExportAdapterControl,
    PdfExportAdapterLimits,
)
from paperworkshell.worker._windows_broker import WindowsResultPipe
from paperworkshell.worker.operations import (
    OperationContext,
    PdfExportOperationHandler,
    PdfExportOperationRequest,
)
from paperworkshell.worker.pdf_export_buffers import (
    WindowsPdfExportOutputBuffer,
    WindowsPdfExportPlanBuffer,
)
from paperworkshell.worker.pdf_export_operation import (
    WindowsPdfExportOperationAdapter,
    _materialize_worker_private_temp,
    _worker_private_operation_temp,
    build_pdf_export_operation_handler,
)
from paperworkshell.worker.pdf_export_plan import encode_pdf_export_worker_plan
from paperworkshell.worker.pdf_export_report import decode_pdf_export_worker_report
from paperworkshell.worker.private_temp import (
    WORKER_PRIVATE_TEMP_MINIMUM_FREE_SPACE_RESERVE_BYTES,
    OsWorkerPrivateTempStorage,
)
from paperworkshell.worker.protocol.models import (
    PDF_EXPORT_OUTPUT_CONTENT_KIND,
    PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
    PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
    PDF_EXPORT_PLAN_CONTENT_KIND,
    PDF_EXPORT_PLAN_HANDLE_PARAMETER,
    PDF_EXPORT_RESULT_HANDLE_PARAMETER,
    CaseObjectReference,
    ReferenceArea,
    ResourceLimits,
    ScalarValueType,
    TaggedScalar,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows handle composition is required")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PDF_FIXTURE = PROJECT_ROOT / "tests/fixtures/synthetic/v1/pdf/born-digital.pdf"

_DUPLICATE_SAME_ACCESS = 0x0000_0002
_FILE_MAP_READ = 0x0000_0004
_FILE_MAP_WRITE = 0x0000_0002
_FILE_SHARE_READ = 0x0000_0001
_FILE_SHARE_WRITE = 0x0000_0002
_FILE_SHARE_DELETE = 0x0000_0004
_GENERIC_READ = 0x8000_0000
_GENERIC_WRITE = 0x4000_0000
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x0000_0080
_FILE_FLAG_BACKUP_SEMANTICS = 0x0200_0000
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def _uuid(value: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{value:012x}")


def _kernel32() -> object:
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _duplicate_local_handle(handle: int, *, access: int | None = None) -> int:
    kernel32 = _kernel32()
    current_process = kernel32.GetCurrentProcess  # type: ignore[attr-defined]
    current_process.argtypes = ()
    current_process.restype = wintypes.HANDLE
    duplicate = kernel32.DuplicateHandle  # type: ignore[attr-defined]
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
    flags = _DUPLICATE_SAME_ACCESS if access is None else 0
    assert duplicate(
        current_process(),
        wintypes.HANDLE(handle),
        current_process(),
        ctypes.byref(target),
        0 if access is None else access,
        False,
        flags,
    )
    assert target.value is not None
    value = int(target.value)
    assert value > 0
    assert not os.get_handle_inheritable(value)
    return value


def _open_read_authority(path: Path) -> int:
    kernel32 = _kernel32()
    create_file = kernel32.CreateFileW  # type: ignore[attr-defined]
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
        _FILE_SHARE_READ,
        None,
        _OPEN_EXISTING,
        _FILE_ATTRIBUTE_NORMAL,
        None,
    )
    value = int(raw) if raw else 0
    assert value > 0
    assert not os.get_handle_inheritable(value)
    return value


def _try_open_directory_for_write(path: Path) -> tuple[int, int]:
    kernel32 = _kernel32()
    create_file = kernel32.CreateFileW  # type: ignore[attr-defined]
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
    ctypes.set_last_error(0)
    raw = create_file(
        os.fspath(path),
        _GENERIC_WRITE,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    value = int(raw) if raw else 0
    return value, ctypes.get_last_error()


def _handle_is_open(handle: int) -> bool:
    kernel32 = _kernel32()
    get_information = kernel32.GetHandleInformation  # type: ignore[attr-defined]
    get_information.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    )
    get_information.restype = wintypes.BOOL
    flags = wintypes.DWORD()
    return bool(get_information(wintypes.HANDLE(handle), ctypes.byref(flags)))


def _close_if_open(handle: int) -> None:
    if not _handle_is_open(handle):
        return
    kernel32 = _kernel32()
    close_handle = kernel32.CloseHandle  # type: ignore[attr-defined]
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    assert close_handle(wintypes.HANDLE(handle))


def _plan(source_path: Path) -> PdfExportPlan:
    payload = source_path.read_bytes()
    source_id = _uuid(10)
    source = PdfExportSource(
        source_id=source_id,
        byte_size=len(payload),
        page_count=1,
        signature_state=PdfExportSourceSignatureState.NONE_DETECTED,
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    return PdfExportPlan(
        case_id=_uuid(1),
        manifest_revision=7,
        manifest_sha256="1" * 64,
        manifest_cas_token="worker-export-cas",
        sources=(source,),
        pages=(
            PdfExportPage(
                output_page_index=0,
                page_id=_uuid(11),
                source_id=source_id,
                source_page_index=0,
                additional_rotation=PdfExportRotation.DEGREES_0,
            ),
        ),
    )


def _inputs(
    plan: PdfExportPlan,
    plan_payload: bytes,
    *,
    operation_id: UUID | None = None,
) -> tuple[CaseObjectReference, ...]:
    source = plan.sources[0]
    selected_operation_id = _uuid(3) if operation_id is None else operation_id
    return (
        CaseObjectReference(
            area=ReferenceArea.OPERATION_TEMP,
            object_id=_uuid(4),
            operation_id=selected_operation_id,
            content_kind=PDF_EXPORT_PLAN_CONTENT_KIND,
            expected_byte_size=len(plan_payload),
            expected_sha256=hashlib.sha256(plan_payload).hexdigest(),
        ),
        CaseObjectReference(
            area=ReferenceArea.SOURCE_READ,
            object_id=source.source_id,
            operation_id=None,
            content_kind="pdf",
            expected_byte_size=source.byte_size,
            expected_sha256=source.sha256,
        ),
    )


def _context(
    plan: PdfExportPlan,
    plan_payload: bytes,
    request: PdfExportOperationRequest,
    *,
    cancel: Event | None = None,
    deadline: Event | None = None,
) -> OperationContext:
    return OperationContext(
        operation_id=_uuid(3),
        case_id=plan.case_id,
        limits=ResourceLimits(
            wall_time_ms=30_000,
            cpu_time_ms=20_000,
            memory_bytes=16 * 1024 * 1024,
            temporary_storage_bytes=2 * 1024 * 1024,
            output_bytes=1024 * 1024,
            page_count=10,
            pixel_count=1024 * 1024,
        ),
        cancel_requested=Event() if cancel is None else cancel,
        report_progress=lambda _progress: None,
        deadline_expired=(Event() if deadline is None else deadline).is_set,
        inputs=_inputs(plan, plan_payload),
        parameters=(
            TaggedScalar(
                PDF_EXPORT_PLAN_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                request.plan_handle,
            ),
            TaggedScalar(
                PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                request.output_handle,
            ),
            TaggedScalar(
                PDF_EXPORT_RESULT_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                request.result_handle,
            ),
            TaggedScalar(
                PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
                ScalarValueType.UUID,
                request.output_object_id,
            ),
        ),
    )


class _RecordingCandidateAdapter:
    def __init__(self, original_source: Path, expected_payload: bytes) -> None:
        self.original_source = original_source
        self.expected_payload = expected_payload
        self.calls = 0
        self.operation_temp: Path | None = None

    def assemble_and_verify(
        self,
        plan: PdfExportPlan,
        source_paths: Mapping[UUID, Path],
        output_path: Path,
        limits: PdfExportAdapterLimits,
        control: PdfExportAdapterControl,
    ) -> PdfExportVerificationReport:
        self.calls += 1
        assert set(source_paths) == {plan.sources[0].source_id}
        staged_source = source_paths[plan.sources[0].source_id]
        self.operation_temp = staged_source.parent
        assert staged_source.is_absolute()
        assert staged_source != self.original_source
        assert staged_source.read_bytes() == self.expected_payload
        assert not output_path.exists()
        assert output_path.parent == staged_source.parent

        # The source authority was opened without delete sharing. A successful
        # rename here proves every case handle closed before native dispatch.
        moved = self.original_source.with_suffix(".closed-handle-check")
        os.replace(self.original_source, moved)
        os.replace(moved, self.original_source)

        assert limits.verification_width_pixels == 1024
        assert limits.verification_height_pixels == 1024
        assert limits.rendering.maximum_width_pixels == 1024
        assert limits.rendering.maximum_height_pixels == 1024
        assert limits.rendering.maximum_pixels == 1024 * 1024
        assert limits.rendering.maximum_memory_bytes <= 16 * 1024 * 1024
        assert limits.inspection.maximum_pages == 10
        assert control.cancellation_requested() is False
        assert control.deadline_expired() is False

        with output_path.open("xb") as candidate:
            candidate.write(self.expected_payload)
        return PdfExportVerificationReport.for_verified_plan(
            plan,
            output_sha256=hashlib.sha256(self.expected_payload).hexdigest(),
            output_byte_size=len(self.expected_payload),
        )


class _DiskFullNamedStream:
    """Persist a partial source stage, then simulate one native ENOSPC."""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream

    def __enter__(self) -> _DiskFullNamedStream:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def write(self, payload: bytes, /) -> int:
        self._stream.write(payload[: max(1, len(payload) // 2)])
        raise OSError(28, "sensitive worker-private export disk-full detail")

    def close(self) -> None:
        self._stream.close()

    def __getattr__(self, member: str) -> object:
        return getattr(self._stream, member)


class _DiskFullOnceNamedTempStorage:
    def __init__(self, *, available_bytes: int = 2 * 1024 * 1024) -> None:
        self._storage = OsWorkerPrivateTempStorage(
            available_bytes_probe=lambda _root: available_bytes,
            minimum_free_space_reserve_bytes=0,
        )
        self._fail_next_named = True

    def require_capacity(self, root: Path, byte_count: int) -> None:
        self._storage.require_capacity(root, byte_count)

    def open_anonymous(self, root: Path) -> BinaryIO:
        return self._storage.open_anonymous(root)

    def open_named(self, root: Path, *, suffix: str) -> tuple[BinaryIO, Path]:
        stream, path = self._storage.open_named(root, suffix=suffix)
        if self._fail_next_named:
            self._fail_next_named = False
            return cast(BinaryIO, _DiskFullNamedStream(stream)), path
        return stream, path


def _execute_worker_export_once(
    source_path: Path,
    candidate: _RecordingCandidateAdapter,
    private_temp_storage: object,
) -> CaseObjectReference:
    source_authority = _open_read_authority(source_path)
    source_worker = _duplicate_local_handle(source_authority)
    _close_if_open(source_authority)
    plan = _plan(source_path)
    with (
        WindowsPdfExportOutputBuffer.create(1024 * 1024) as output,
        WindowsResultPipe.create() as result_pipe,
    ):
        output_worker = _duplicate_local_handle(
            output.write_handle_value,
            access=_FILE_MAP_WRITE,
        )
        result_worker = _duplicate_local_handle(result_pipe.write_handle_value)
        result_pipe.close_local_writer()
        plan_payload = encode_pdf_export_worker_plan(
            plan,
            {plan.sources[0].source_id: source_worker},
        )
        with WindowsPdfExportPlanBuffer.create(plan_payload) as plan_buffer:
            plan_worker = _duplicate_local_handle(
                plan_buffer.read_handle_value,
                access=_FILE_MAP_READ,
            )
            request = PdfExportOperationRequest(
                plan_worker,
                output_worker,
                result_worker,
                _uuid(20),
            )
            context = _context(plan, plan_payload, request)
            try:
                return WindowsPdfExportOperationAdapter(
                    candidate_adapter=candidate,
                    private_temp_storage=cast(Any, private_temp_storage),
                ).export(context, request)
            finally:
                for handle in (source_worker, plan_worker, output_worker, result_worker):
                    _close_if_open(handle)


def test_private_temp_preflight_rejects_before_source_copy_or_operation_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "source.pdf"
    payload = PDF_FIXTURE.read_bytes()
    source_path.write_bytes(payload)
    local_root = tmp_path / "worker-profile"
    local_root.mkdir()
    temp_root = local_root / "Temp"
    monkeypatch.setenv("LOCALAPPDATA", os.fspath(local_root))
    monkeypatch.setenv("TEMP", os.fspath(temp_root))
    monkeypatch.setenv("TMP", os.fspath(temp_root))
    candidate = _RecordingCandidateAdapter(source_path, payload)
    storage = OsWorkerPrivateTempStorage(
        available_bytes_probe=lambda _root: (
            (2 * 1024 * 1024) + WORKER_PRIVATE_TEMP_MINIMUM_FREE_SPACE_RESERVE_BYTES - 1
        )
    )

    with pytest.raises(PdfExportError) as caught:
        _execute_worker_export_once(source_path, candidate, storage)

    assert caught.value.code is PdfExportErrorCode.LIMIT_EXCEEDED
    assert caught.value.context == "export.temp_capacity"
    assert candidate.calls == 0
    assert temp_root.is_dir()
    assert tuple(temp_root.iterdir()) == ()


def test_mid_export_private_disk_full_removes_partial_and_fresh_retry_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "source.pdf"
    payload = PDF_FIXTURE.read_bytes()
    source_path.write_bytes(payload)
    local_root = tmp_path / "worker-profile"
    local_root.mkdir()
    temp_root = local_root / "Temp"
    monkeypatch.setenv("LOCALAPPDATA", os.fspath(local_root))
    monkeypatch.setenv("TEMP", os.fspath(temp_root))
    monkeypatch.setenv("TMP", os.fspath(temp_root))
    storage = _DiskFullOnceNamedTempStorage()
    failed_candidate = _RecordingCandidateAdapter(source_path, payload)

    with pytest.raises(PdfExportError) as caught:
        _execute_worker_export_once(source_path, failed_candidate, storage)

    assert caught.value.code is PdfExportErrorCode.LIMIT_EXCEEDED
    assert caught.value.context == "export.source_temp_write"
    assert "sensitive" not in str(caught.value)
    assert failed_candidate.calls == 0
    assert temp_root.is_dir()
    assert tuple(temp_root.iterdir()) == ()

    recovered_candidate = _RecordingCandidateAdapter(source_path, payload)
    reference = _execute_worker_export_once(source_path, recovered_candidate, storage)

    assert reference.expected_byte_size == len(payload)
    assert recovered_candidate.calls == 1
    assert tuple(temp_root.iterdir()) == ()


def test_production_adapter_consumes_handles_stages_sources_and_correlates_outputs(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.pdf"
    source_payload = PDF_FIXTURE.read_bytes()
    source_path.write_bytes(source_payload)
    source_authority = _open_read_authority(source_path)
    source_worker = _duplicate_local_handle(source_authority)
    _close_if_open(source_authority)
    plan = _plan(source_path)
    candidate = _RecordingCandidateAdapter(source_path, source_payload)

    with (
        WindowsPdfExportOutputBuffer.create(1024 * 1024) as output,
        WindowsResultPipe.create() as result_pipe,
    ):
        output_worker = _duplicate_local_handle(
            output.write_handle_value,
            access=_FILE_MAP_WRITE,
        )
        result_worker = _duplicate_local_handle(result_pipe.write_handle_value)
        result_pipe.close_local_writer()
        plan_payload = encode_pdf_export_worker_plan(
            plan,
            {plan.sources[0].source_id: source_worker},
        )
        with WindowsPdfExportPlanBuffer.create(plan_payload) as plan_buffer:
            plan_worker = _duplicate_local_handle(
                plan_buffer.read_handle_value,
                access=_FILE_MAP_READ,
            )
            request = PdfExportOperationRequest(
                plan_worker,
                output_worker,
                result_worker,
                _uuid(20),
            )
            context = _context(plan, plan_payload, request)
            try:
                reference = WindowsPdfExportOperationAdapter(candidate).export(context, request)
            finally:
                for handle in (source_worker, plan_worker, output_worker, result_worker):
                    _close_if_open(handle)

        assert reference.area is ReferenceArea.OPERATION_STAGING
        assert reference.object_id == request.output_object_id
        assert reference.operation_id == context.operation_id
        assert reference.content_kind == PDF_EXPORT_OUTPUT_CONTENT_KIND
        assert reference.expected_byte_size == len(source_payload)
        assert reference.expected_sha256 == hashlib.sha256(source_payload).hexdigest()
        assert b"".join(output.iter_chunks(len(source_payload))) == source_payload
        worker_report = decode_pdf_export_worker_report(result_pipe.read_complete_frame())

    assert candidate.calls == 1
    assert worker_report.operation_id == context.operation_id
    assert worker_report.output_object_id == request.output_object_id
    assert worker_report.report.output_sha256 == reference.expected_sha256
    assert candidate.operation_temp is not None
    assert not candidate.operation_temp.exists()


def test_plan_decode_failure_consumes_every_discoverable_transfer_handle(tmp_path: Path) -> None:
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(PDF_FIXTURE.read_bytes())
    plan = _plan(source_path)
    invalid_plan = b"{not-canonical-json"
    candidate = _RecordingCandidateAdapter(source_path, source_path.read_bytes())

    with (
        WindowsPdfExportOutputBuffer.create(1024 * 1024) as output,
        WindowsResultPipe.create() as result_pipe,
        WindowsPdfExportPlanBuffer.create(invalid_plan) as plan_buffer,
    ):
        plan_worker = _duplicate_local_handle(plan_buffer.read_handle_value, access=_FILE_MAP_READ)
        output_worker = _duplicate_local_handle(
            output.write_handle_value,
            access=_FILE_MAP_WRITE,
        )
        result_worker = _duplicate_local_handle(result_pipe.write_handle_value)
        result_pipe.close_local_writer()
        request = PdfExportOperationRequest(
            plan_worker,
            output_worker,
            result_worker,
            _uuid(20),
        )
        context = _context(plan, invalid_plan, request)

        with pytest.raises(PdfExportError) as caught:
            WindowsPdfExportOperationAdapter(candidate).export(context, request)

        assert caught.value.code is PdfExportErrorCode.RESULT_INVALID
        assert candidate.calls == 0
        assert not _handle_is_open(plan_worker)
        assert not _handle_is_open(output_worker)
        assert not _handle_is_open(result_worker)


def test_source_handle_collision_with_output_is_rejected_and_closed_once(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(PDF_FIXTURE.read_bytes())
    candidate = _RecordingCandidateAdapter(source_path, source_path.read_bytes())

    with (
        WindowsPdfExportOutputBuffer.create(1024 * 1024) as output,
        WindowsResultPipe.create() as result_pipe,
    ):
        output_worker = _duplicate_local_handle(
            output.write_handle_value,
            access=_FILE_MAP_WRITE,
        )
        result_worker = _duplicate_local_handle(result_pipe.write_handle_value)
        result_pipe.close_local_writer()
        plan = _plan(source_path)
        plan_payload = encode_pdf_export_worker_plan(
            plan,
            {plan.sources[0].source_id: output_worker},
        )
        with WindowsPdfExportPlanBuffer.create(plan_payload) as plan_buffer:
            plan_worker = _duplicate_local_handle(
                plan_buffer.read_handle_value,
                access=_FILE_MAP_READ,
            )
            request = PdfExportOperationRequest(
                plan_worker,
                output_worker,
                result_worker,
                _uuid(20),
            )
            context = _context(plan, plan_payload, request)

            with pytest.raises(PdfExportError) as caught:
                WindowsPdfExportOperationAdapter(candidate).export(context, request)

            assert caught.value.code is PdfExportErrorCode.RESULT_INVALID
            assert caught.value.context == "export.handle_collision"
            assert candidate.calls == 0
            assert not _handle_is_open(plan_worker)
            assert not _handle_is_open(output_worker)
            assert not _handle_is_open(result_worker)


def test_changed_source_is_rejected_and_every_discovered_handle_is_consumed(
    tmp_path: Path,
) -> None:
    declared = tmp_path / "declared.pdf"
    payload = PDF_FIXTURE.read_bytes()
    declared.write_bytes(payload)
    changed = tmp_path / "changed.pdf"
    changed.write_bytes(bytes((byte ^ 0x01) for byte in payload))
    changed_authority = _open_read_authority(changed)
    source_worker = _duplicate_local_handle(changed_authority)
    _close_if_open(changed_authority)
    plan = _plan(declared)
    candidate = _RecordingCandidateAdapter(declared, payload)

    with (
        WindowsPdfExportOutputBuffer.create(1024 * 1024) as output,
        WindowsResultPipe.create() as result_pipe,
    ):
        output_worker = _duplicate_local_handle(
            output.write_handle_value,
            access=_FILE_MAP_WRITE,
        )
        result_worker = _duplicate_local_handle(result_pipe.write_handle_value)
        result_pipe.close_local_writer()
        plan_payload = encode_pdf_export_worker_plan(
            plan,
            {plan.sources[0].source_id: source_worker},
        )
        with WindowsPdfExportPlanBuffer.create(plan_payload) as plan_buffer:
            plan_worker = _duplicate_local_handle(
                plan_buffer.read_handle_value,
                access=_FILE_MAP_READ,
            )
            request = PdfExportOperationRequest(
                plan_worker,
                output_worker,
                result_worker,
                _uuid(20),
            )
            context = _context(plan, plan_payload, request)

            with pytest.raises(PdfExportError) as caught:
                WindowsPdfExportOperationAdapter(candidate).export(context, request)

            assert caught.value.code is PdfExportErrorCode.SOURCE_CHANGED
            assert candidate.calls == 0
            moved = changed.with_suffix(".closed")
            os.replace(changed, moved)
            os.replace(moved, changed)
            for handle in (source_worker, plan_worker, output_worker, result_worker):
                assert not _handle_is_open(handle)


def test_build_function_composes_the_fixed_export_handler() -> None:
    handler = build_pdf_export_operation_handler()

    assert type(handler) is PdfExportOperationHandler
    assert type(handler.adapter) is WindowsPdfExportOperationAdapter


def test_worker_private_temp_accepts_an_identity_equivalent_local_path_spelling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_root = tmp_path / "Local"
    alias_root = tmp_path / "LOCAL~1"
    local_root.mkdir()
    alias_root.mkdir()
    temp_root = alias_root / "Temp"
    local_information = os.lstat(local_root)
    alias_information = os.lstat(alias_root)
    equivalent_parent_identities = {
        (local_information.st_dev, local_information.st_ino),
        (alias_information.st_dev, alias_information.st_ino),
    }
    real_samestat = os.path.samestat

    def samestat(first: os.stat_result, second: os.stat_result) -> bool:
        identities = {
            (first.st_dev, first.st_ino),
            (second.st_dev, second.st_ino),
        }
        if identities == equivalent_parent_identities:
            return True
        return real_samestat(first, second)

    monkeypatch.setenv("LOCALAPPDATA", os.fspath(local_root))
    monkeypatch.setenv("TEMP", os.fspath(temp_root))
    monkeypatch.setenv("TMP", os.fspath(temp_root))
    monkeypatch.setattr(os.path, "samestat", samestat)

    assert _materialize_worker_private_temp() == temp_root
    assert temp_root.is_dir()


def test_worker_private_temp_avoids_identity_queries_for_exact_launch_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_root = tmp_path / "Local"
    local_root.mkdir()
    temp_root = local_root / "Temp"
    monkeypatch.setenv("LOCALAPPDATA", os.fspath(local_root))
    monkeypatch.setenv("TEMP", os.fspath(temp_root))
    monkeypatch.setenv("TMP", os.fspath(temp_root))

    def denied_samestat(_first: object, _second: object) -> bool:
        raise PermissionError

    monkeypatch.setattr(os.path, "samestat", denied_samestat)

    assert _materialize_worker_private_temp() == temp_root
    assert temp_root.is_dir()


def test_worker_private_temp_rejects_a_different_parent_before_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_root = tmp_path / "Local"
    unrelated_root = tmp_path / "unrelated"
    local_root.mkdir()
    unrelated_root.mkdir()
    temp_root = unrelated_root / "Temp"
    monkeypatch.setenv("LOCALAPPDATA", os.fspath(local_root))
    monkeypatch.setenv("TEMP", os.fspath(temp_root))
    monkeypatch.setenv("TMP", os.fspath(temp_root))

    with pytest.raises(PdfExportError) as caught:
        _materialize_worker_private_temp()

    assert caught.value.code is PdfExportErrorCode.INTERNAL_ERROR
    assert caught.value.context == "export.temp_scope"
    assert not temp_root.exists()


def test_worker_private_temp_rejects_an_alias_parent_identity_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_root = tmp_path / "Local"
    alias_root = tmp_path / "LOCAL~1"
    local_root.mkdir()
    alias_root.mkdir()
    temp_root = alias_root / "Temp"
    local_information = os.lstat(local_root)
    alias_information = os.lstat(alias_root)
    equivalent_parent_identities = {
        (local_information.st_dev, local_information.st_ino),
        (alias_information.st_dev, alias_information.st_ino),
    }
    real_lstat = os.lstat
    real_samestat = os.path.samestat
    alias_checks = 0

    def changing_lstat(candidate: os.PathLike[str] | str) -> os.stat_result:
        nonlocal alias_checks
        information = real_lstat(candidate)
        if Path(candidate) != alias_root:
            return information
        alias_checks += 1
        if alias_checks <= 2:
            return information
        changed = list(information)
        changed[1] = information.st_ino + 1
        return os.stat_result(
            changed,
            {"st_file_attributes": information.st_file_attributes},
        )

    def samestat(first: os.stat_result, second: os.stat_result) -> bool:
        identities = {
            (first.st_dev, first.st_ino),
            (second.st_dev, second.st_ino),
        }
        if identities == equivalent_parent_identities:
            return True
        return real_samestat(first, second)

    monkeypatch.setenv("LOCALAPPDATA", os.fspath(local_root))
    monkeypatch.setenv("TEMP", os.fspath(temp_root))
    monkeypatch.setenv("TMP", os.fspath(temp_root))
    monkeypatch.setattr(os, "lstat", changing_lstat)
    monkeypatch.setattr(os.path, "samestat", samestat)

    with pytest.raises(PdfExportError) as caught:
        _materialize_worker_private_temp()

    assert caught.value.code is PdfExportErrorCode.INTERNAL_ERROR
    assert caught.value.context == "export.temp_identity"


def test_worker_private_operation_temp_removes_nested_content(tmp_path: Path) -> None:
    temp_root = tmp_path / "Temp"
    temp_root.mkdir()

    with _worker_private_operation_temp(temp_root) as operation_temp:
        assert operation_temp.parent == temp_root
        nested = operation_temp / "candidate.pdf"
        nested.write_bytes(b"synthetic-export-candidate")
        assert nested.read_bytes() == b"synthetic-export-candidate"

    assert not operation_temp.exists()


def test_worker_private_operation_temp_pin_blocks_root_replacement(tmp_path: Path) -> None:
    temp_root = tmp_path / "Temp"
    temp_root.mkdir()

    with _worker_private_operation_temp(temp_root) as operation_temp:
        replacement = temp_root / "replacement"
        with pytest.raises(OSError):
            operation_temp.rename(replacement)
        write_handle, error = _try_open_directory_for_write(operation_temp)
        if write_handle not in {0, _INVALID_HANDLE_VALUE}:
            _close_if_open(write_handle)
        assert write_handle in {0, _INVALID_HANDLE_VALUE}
        assert error == 32
        (operation_temp / "candidate.pdf").write_bytes(b"flat child remains writable")
        assert operation_temp.exists()
        assert not replacement.exists()

    assert not operation_temp.exists()


def test_worker_private_operation_temp_blocks_success_when_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temp_root = tmp_path / "Temp"
    temp_root.mkdir()
    created: Path | None = None
    real_rmtree = shutil.rmtree

    def fail_cleanup(_path: Path) -> bool:
        return False

    monkeypatch.setattr(
        "paperworkshell.worker.pdf_export_operation._remove_operation_temp_contents",
        fail_cleanup,
    )
    try:
        with pytest.raises(PdfExportError) as caught:
            with _worker_private_operation_temp(temp_root) as operation_temp:
                created = operation_temp
                (operation_temp / "residue.bin").write_bytes(b"synthetic residue")

        assert caught.value.code is PdfExportErrorCode.WORKER_FAILED
        assert caught.value.context == "export.temp_cleanup"
        assert created is not None
        assert created.exists()
    finally:
        if created is not None:
            real_rmtree(created, ignore_errors=True)


def test_worker_private_operation_temp_preserves_body_failure_when_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temp_root = tmp_path / "Temp"
    temp_root.mkdir()
    created: Path | None = None
    real_rmtree = shutil.rmtree

    class SyntheticBodyFailure(Exception):
        pass

    def fail_cleanup(_path: Path) -> bool:
        return False

    monkeypatch.setattr(
        "paperworkshell.worker.pdf_export_operation._remove_operation_temp_contents",
        fail_cleanup,
    )
    try:
        with pytest.raises(SyntheticBodyFailure):
            with _worker_private_operation_temp(temp_root) as operation_temp:
                created = operation_temp
                (operation_temp / "residue.bin").write_bytes(b"synthetic residue")
                raise SyntheticBodyFailure

        assert created is not None
        assert created.exists()
    finally:
        if created is not None:
            real_rmtree(created, ignore_errors=True)


def test_worker_private_operation_temp_never_recurses_into_nested_directory(
    tmp_path: Path,
) -> None:
    temp_root = tmp_path / "Temp"
    temp_root.mkdir()
    created: Path | None = None
    marker: Path | None = None
    try:
        with pytest.raises(PdfExportError) as caught:
            with _worker_private_operation_temp(temp_root) as operation_temp:
                created = operation_temp
                marker = operation_temp / "unexpected" / "preserve.bin"
                marker.parent.mkdir()
                marker.write_bytes(b"must not be traversed")

        assert caught.value.code is PdfExportErrorCode.WORKER_FAILED
        assert marker is not None
        assert marker.read_bytes() == b"must not be traversed"
    finally:
        if created is not None:
            shutil.rmtree(created, ignore_errors=True)

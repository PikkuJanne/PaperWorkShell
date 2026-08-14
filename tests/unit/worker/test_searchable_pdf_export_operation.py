"""Focused production-boundary tests for searchable PDF worker export."""

from __future__ import annotations

import ctypes
import hashlib
import io
import os
import time
from collections.abc import Mapping
from ctypes import wintypes
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import Any, cast
from uuid import UUID

import pytest

import paperworkshell.worker.searchable_pdf_export_operation as operation
from paperworkshell.domain.image_pages import ImagePageError, ImagePageErrorCode
from paperworkshell.domain.ocr import OcrError, OcrErrorCode
from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.domain.pdf_inspection import PdfInspectionError, PdfInspectionErrorCode
from paperworkshell.domain.searchable_pdf_export import (
    SearchablePdfExportPlan,
    SearchablePdfPage,
    SearchablePdfPageEligibility,
    SearchablePdfPageEligibilityCode,
    SearchablePdfPageOutcome,
    SearchablePdfPageOutcomeCode,
    SearchablePdfPageSearchability,
    SearchablePdfSource,
    SearchablePdfSourceEncryptionState,
    SearchablePdfSourceMediaType,
    SearchablePdfSourceSignatureState,
    SearchablePdfVerificationReport,
)
from paperworkshell.ocr.ocrmypdf_adapter import OcrMyPdfAdapter
from paperworkshell.pdf.qpdf_checker import QpdfCommandChecker
from paperworkshell.pdf.searchable_export_adapter import (
    SearchablePdfAdapterProgressPhase,
    SearchablePdfAdapterProgressSink,
    SearchablePdfExportAdapter,
    SearchablePdfExportAdapterControl,
    SearchablePdfExportAdapterLimits,
)
from paperworkshell.worker._windows_broker import WindowsResultPipe
from paperworkshell.worker.operations import (
    OperationContext,
    SearchablePdfExportOperationHandler,
    SearchablePdfExportOperationRequest,
)
from paperworkshell.worker.pdf_export_buffers import (
    WindowsPdfExportOutputBuffer,
    WindowsPdfExportPlanBuffer,
)
from paperworkshell.worker.private_temp import OsWorkerPrivateTempStorage
from paperworkshell.worker.protocol.models import (
    SEARCHABLE_PDF_EXPORT_OUTPUT_CONTENT_KIND,
    SEARCHABLE_PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
    SEARCHABLE_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
    SEARCHABLE_PDF_EXPORT_PLAN_CONTENT_KIND,
    SEARCHABLE_PDF_EXPORT_PLAN_HANDLE_PARAMETER,
    SEARCHABLE_PDF_EXPORT_RESULT_HANDLE_PARAMETER,
    SEARCHABLE_PDF_EXPORT_STATUS_CAPACITY_PARAMETER,
    SEARCHABLE_PDF_EXPORT_STATUS_CONTENT_KIND,
    SEARCHABLE_PDF_EXPORT_STATUS_HANDLE_PARAMETER,
    SEARCHABLE_PDF_EXPORT_STATUS_OBJECT_ID_PARAMETER,
    CaseObjectReference,
    OperationOutcome,
    OperationProgressPayload,
    ReferenceArea,
    ResourceLimits,
    ScalarValueType,
    TaggedScalar,
)
from paperworkshell.worker.searchable_pdf_export_operation import (
    WindowsSearchablePdfExportOperationAdapter,
    build_searchable_pdf_export_operation_handler,
)
from paperworkshell.worker.searchable_pdf_export_plan import (
    encode_searchable_pdf_worker_plan,
)
from paperworkshell.worker.searchable_pdf_export_report import (
    decode_searchable_pdf_worker_report,
)
from paperworkshell.worker.searchable_pdf_export_status import (
    decode_searchable_pdf_page_status,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows handle composition is required")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PDF_FIXTURE = PROJECT_ROOT / "tests/fixtures/synthetic/v1/pdf/born-digital.pdf"

_DUPLICATE_SAME_ACCESS = 0x0000_0002
_FILE_MAP_READ = 0x0000_0004
_FILE_MAP_WRITE = 0x0000_0002


def _uuid(value: int) -> UUID:
    return UUID(f"a6000000-0000-4000-8000-{value:012x}")


def _kernel32() -> Any:
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _duplicate_local_handle(handle: int, *, access: int | None = None) -> int:
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
        0 if access is None else access,
        False,
        _DUPLICATE_SAME_ACCESS if access is None else 0,
    )
    assert target.value is not None
    result = int(target.value)
    assert result > 0
    assert not os.get_handle_inheritable(result)
    return result


def _open_source_authority(path: Path) -> int:
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
        0x8000_0000,
        0x0000_0001,
        None,
        3,
        0x0000_0080,
        None,
    )
    result = int(raw) if raw else 0
    assert result > 0
    assert not os.get_handle_inheritable(result)
    return result


def _handle_is_open(handle: int) -> bool:
    kernel32 = _kernel32()
    get_information = kernel32.GetHandleInformation
    get_information.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    get_information.restype = wintypes.BOOL
    flags = wintypes.DWORD()
    return bool(get_information(wintypes.HANDLE(handle), ctypes.byref(flags)))


def _close_if_open(handle: int) -> None:
    if not _handle_is_open(handle):
        return
    kernel32 = _kernel32()
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    assert close_handle(wintypes.HANDLE(handle))


def _plan(source_path: Path) -> SearchablePdfExportPlan:
    payload = source_path.read_bytes()
    source = SearchablePdfSource(
        source_id=_uuid(10),
        media_type=SearchablePdfSourceMediaType.PDF,
        byte_size=len(payload),
        page_count=1,
        sha256=hashlib.sha256(payload).hexdigest(),
        signature_state=SearchablePdfSourceSignatureState.NONE_DETECTED,
        encryption_state=SearchablePdfSourceEncryptionState.UNENCRYPTED,
    )
    return SearchablePdfExportPlan(
        case_id=_uuid(1),
        manifest_revision=7,
        manifest_sha256="1" * 64,
        manifest_cas_token="searchable-worker-operation-cas-v2",
        sources=(source,),
        pages=(
            SearchablePdfPage(
                0,
                _uuid(11),
                source.source_id,
                0,
                0,
                None,
                0,
                1_000_000,
                0,
            ),
        ),
    )


def _outcome(plan: SearchablePdfExportPlan) -> SearchablePdfPageOutcome:
    page = plan.pages[0]
    return SearchablePdfPageOutcome(
        SearchablePdfPageEligibility(
            page.output_page_index,
            page.page_id,
            SearchablePdfPageEligibilityCode.PRESERVE_EXTRACTABLE_TEXT,
        ),
        SearchablePdfPageOutcomeCode.EXISTING_TEXT_PRESERVED,
        SearchablePdfPageSearchability.EXTRACTABLE_TEXT_PRESENT_EXISTING,
    )


def _limits(plan: SearchablePdfExportPlan, *, temporary_bytes: int = 16_000_000) -> ResourceLimits:
    assert plan.sources[0].byte_size < temporary_bytes
    return ResourceLimits(
        wall_time_ms=30_000,
        cpu_time_ms=20_000,
        memory_bytes=32 * 1024 * 1024,
        temporary_storage_bytes=temporary_bytes,
        output_bytes=2 * 1024 * 1024,
        page_count=10,
        pixel_count=1024 * 1024,
    )


def _context(
    plan: SearchablePdfExportPlan,
    plan_payload: bytes,
    *,
    limits: ResourceLimits | None = None,
    cancel: Event | None = None,
    progress: list[OperationProgressPayload] | None = None,
) -> OperationContext:
    source = plan.sources[0]
    return OperationContext(
        operation_id=_uuid(3),
        case_id=plan.case_id,
        limits=_limits(plan) if limits is None else limits,
        cancel_requested=Event() if cancel is None else cancel,
        report_progress=(lambda fact: None if progress is None else progress.append(fact)),
        deadline_expired=lambda: False,
        inputs=(
            CaseObjectReference(
                ReferenceArea.SOURCE_READ,
                _uuid(4),
                None,
                SEARCHABLE_PDF_EXPORT_PLAN_CONTENT_KIND,
                len(plan_payload),
                hashlib.sha256(plan_payload).hexdigest(),
            ),
            CaseObjectReference(
                ReferenceArea.SOURCE_READ,
                source.source_id,
                None,
                "pdf",
                source.byte_size,
                source.sha256,
            ),
        ),
    )


def _operation_parameters() -> tuple[TaggedScalar, ...]:
    return (
        TaggedScalar(SEARCHABLE_PDF_EXPORT_PLAN_HANDLE_PARAMETER, ScalarValueType.INTEGER, 1),
        TaggedScalar(
            SEARCHABLE_PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
            ScalarValueType.INTEGER,
            2,
        ),
        TaggedScalar(
            SEARCHABLE_PDF_EXPORT_STATUS_HANDLE_PARAMETER,
            ScalarValueType.INTEGER,
            3,
        ),
        TaggedScalar(
            SEARCHABLE_PDF_EXPORT_RESULT_HANDLE_PARAMETER,
            ScalarValueType.INTEGER,
            4,
        ),
        TaggedScalar(
            SEARCHABLE_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
            ScalarValueType.UUID,
            _uuid(20),
        ),
        TaggedScalar(
            SEARCHABLE_PDF_EXPORT_STATUS_OBJECT_ID_PARAMETER,
            ScalarValueType.UUID,
            _uuid(21),
        ),
        TaggedScalar(
            SEARCHABLE_PDF_EXPORT_STATUS_CAPACITY_PARAMETER,
            ScalarValueType.INTEGER,
            1024,
        ),
    )


class _NoopOcr:
    def process(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("native OCR must not run in this boundary test")


def _candidate_adapter() -> SearchablePdfExportAdapter:
    return SearchablePdfExportAdapter(ocr_processor=cast(Any, _NoopOcr()))


def test_worker_adapter_publishes_exact_correlated_pdf_status_and_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "source.pdf"
    output_payload = PDF_FIXTURE.read_bytes()
    source_path.write_bytes(output_payload)
    plan = _plan(source_path)
    source_authority = _open_source_authority(source_path)
    source_worker = _duplicate_local_handle(source_authority)
    _close_if_open(source_authority)
    progress: list[OperationProgressPayload] = []
    observed: dict[str, object] = {}

    def assemble_and_verify(
        _self: SearchablePdfExportAdapter,
        selected_plan: SearchablePdfExportPlan,
        source_paths: Mapping[UUID, Path],
        output_path: Path,
        temporary_directory: Path,
        *,
        languages: tuple[str, ...],
        limits: SearchablePdfExportAdapterLimits,
        control: SearchablePdfExportAdapterControl,
        progress: SearchablePdfAdapterProgressSink,
    ) -> SearchablePdfVerificationReport:
        observed.update(
            source_paths=source_paths,
            temporary_directory=temporary_directory,
            languages=languages,
            limits=limits,
            control=control,
        )
        assert selected_plan == plan
        assert source_paths[plan.sources[0].source_id].read_bytes() == output_payload
        output_path.write_bytes(output_payload)
        # The worker boundary must bind progress to the decoded plan rather
        # than trusting a nested adapter's redundant total argument.
        progress(SearchablePdfAdapterProgressPhase.COMPLETE, 1, 17)
        return SearchablePdfVerificationReport.for_verified_plan(
            plan,
            output_sha256=hashlib.sha256(output_payload).hexdigest(),
            output_byte_size=len(output_payload),
            page_outcomes=(_outcome(plan),),
        )

    monkeypatch.setattr(SearchablePdfExportAdapter, "assemble_and_verify", assemble_and_verify)
    private_root = tmp_path / "private-temp"
    private_root.mkdir()
    monkeypatch.setattr(operation, "_materialize_worker_private_temp", lambda: private_root)
    storage = OsWorkerPrivateTempStorage(
        available_bytes_probe=lambda _root: 64 * 1024 * 1024,
        minimum_free_space_reserve_bytes=0,
    )

    with (
        WindowsPdfExportOutputBuffer.create(2 * 1024 * 1024) as output,
        WindowsPdfExportOutputBuffer.create(256 * 1024) as status,
        WindowsResultPipe.create() as result_pipe,
    ):
        output_worker = _duplicate_local_handle(output.write_handle_value, access=_FILE_MAP_WRITE)
        status_worker = _duplicate_local_handle(status.write_handle_value, access=_FILE_MAP_WRITE)
        result_worker = _duplicate_local_handle(result_pipe.write_handle_value)
        result_pipe.close_local_writer()
        plan_payload = encode_searchable_pdf_worker_plan(
            plan,
            {plan.sources[0].source_id: source_worker},
        )
        with WindowsPdfExportPlanBuffer.create(plan_payload) as plan_buffer:
            plan_worker = _duplicate_local_handle(
                plan_buffer.read_handle_value,
                access=_FILE_MAP_READ,
            )
            request = SearchablePdfExportOperationRequest(
                plan_worker,
                output_worker,
                status_worker,
                result_worker,
                _uuid(20),
                _uuid(21),
                status.capacity,
            )
            context = _context(plan, plan_payload, progress=progress)
            try:
                references = WindowsSearchablePdfExportOperationAdapter(
                    _candidate_adapter(),
                    cast(Any, storage),
                ).export(context, request)
            finally:
                for handle in (
                    source_worker,
                    plan_worker,
                    output_worker,
                    status_worker,
                    result_worker,
                ):
                    _close_if_open(handle)

        output_reference, status_reference = references
        output_bytes = b"".join(output.iter_chunks(output_reference.expected_byte_size or 0))
        status_bytes = b"".join(status.iter_chunks(status_reference.expected_byte_size or 0))
        status_document = decode_searchable_pdf_page_status(status_bytes)
        worker_report = decode_searchable_pdf_worker_report(result_pipe.read_complete_frame())

    assert output_reference.content_kind == SEARCHABLE_PDF_EXPORT_OUTPUT_CONTENT_KIND
    assert status_reference.content_kind == SEARCHABLE_PDF_EXPORT_STATUS_CONTENT_KIND
    assert (output_reference.object_id, status_reference.object_id) == (
        request.output_object_id,
        request.status_object_id,
    )
    assert output_bytes == output_payload
    assert status_document.operation_id == context.operation_id
    assert status_document.status_object_id == status_reference.object_id
    assert status_document.semantic_fingerprint == plan.semantic_fingerprint
    assert status_document.page_outcomes == (_outcome(plan),)
    assert worker_report.operation_id == context.operation_id
    assert worker_report.output_object_id == output_reference.object_id
    assert worker_report.status_object_id == status_reference.object_id
    assert worker_report.status_byte_size == status_reference.expected_byte_size
    assert worker_report.status_sha256 == status_reference.expected_sha256
    assert worker_report.base_report.report.output_sha256 == output_reference.expected_sha256
    assert progress[-1].completed_units == plan.page_count
    assert {fact.total_units for fact in progress} == {plan.page_count}
    assert tuple(private_root.iterdir()) == ()
    assert observed["languages"] == ("eng",)


def test_candidate_reopen_failure_keeps_output_authority_owned_until_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[int] = []
    authorities = operation._OwnedAuthorities({101})
    monkeypatch.setattr(operation, "_close_native_handle_no_raise", closed.append)

    with pytest.raises(PdfExportError) as raised:
        operation._copy_file_section(
            authorities,
            101,
            tmp_path / "missing-candidate.pdf",
            byte_count=1,
            expected_sha256="0" * 64,
            capacity=1,
            checkpoint=lambda: None,
        )

    assert raised.value.code is PdfExportErrorCode.RESULT_INVALID
    assert raised.value.context == "export.output_read"
    assert authorities.handles == {101}
    authorities.close_all()
    assert closed == [101]


def test_status_source_allocation_failure_keeps_authority_owned_until_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[int] = []
    authorities = operation._OwnedAuthorities({102})
    monkeypatch.setattr(operation, "_close_native_handle_no_raise", closed.append)

    def fail_allocation(_payload: bytes) -> object:
        raise MemoryError

    monkeypatch.setattr(io, "BytesIO", fail_allocation)

    with pytest.raises(MemoryError):
        operation._copy_status_section(
            authorities,
            102,
            b"status",
            byte_count=6,
            capacity=6,
            checkpoint=lambda: None,
        )

    assert authorities.handles == {102}
    authorities.close_all()
    assert closed == [102]


def test_report_encoding_failure_keeps_authority_owned_until_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[int] = []
    authorities = operation._OwnedAuthorities({103})
    monkeypatch.setattr(operation, "_close_native_handle_no_raise", closed.append)

    def fail_encoding(_report: object) -> bytes:
        raise MemoryError

    monkeypatch.setattr(operation, "encode_searchable_pdf_worker_report", fail_encoding)

    with pytest.raises(MemoryError):
        operation._write_worker_report(authorities, 103, cast(Any, object()))

    assert authorities.handles == {103}
    authorities.close_all()
    assert closed == [103]


class _CancelAfterPreflightStorage:
    def __init__(self, cancel: Event) -> None:
        self.cancel = cancel

    def require_capacity(self, _root: Path, _byte_count: int) -> None:
        self.cancel.set()

    def open_named(self, _root: Path, *, suffix: str) -> object:
        del suffix
        raise AssertionError("cancellation must precede source staging")


def test_cancellation_after_plan_decode_closes_every_discovered_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(PDF_FIXTURE.read_bytes())
    plan = _plan(source_path)
    source_authority = _open_source_authority(source_path)
    source_worker = _duplicate_local_handle(source_authority)
    _close_if_open(source_authority)
    cancel = Event()
    private_root = tmp_path / "private-temp"
    private_root.mkdir()
    monkeypatch.setattr(operation, "_materialize_worker_private_temp", lambda: private_root)

    with (
        WindowsPdfExportOutputBuffer.create(2 * 1024 * 1024) as output,
        WindowsPdfExportOutputBuffer.create(256 * 1024) as status,
        WindowsResultPipe.create() as result_pipe,
    ):
        output_worker = _duplicate_local_handle(output.write_handle_value, access=_FILE_MAP_WRITE)
        status_worker = _duplicate_local_handle(status.write_handle_value, access=_FILE_MAP_WRITE)
        result_worker = _duplicate_local_handle(result_pipe.write_handle_value)
        result_pipe.close_local_writer()
        plan_payload = encode_searchable_pdf_worker_plan(
            plan,
            {plan.sources[0].source_id: source_worker},
        )
        with WindowsPdfExportPlanBuffer.create(plan_payload) as plan_buffer:
            plan_worker = _duplicate_local_handle(
                plan_buffer.read_handle_value,
                access=_FILE_MAP_READ,
            )
            request = SearchablePdfExportOperationRequest(
                plan_worker,
                output_worker,
                status_worker,
                result_worker,
                _uuid(20),
                _uuid(21),
                status.capacity,
            )
            context = _context(plan, plan_payload, cancel=cancel)

            with pytest.raises(PdfExportError) as raised:
                WindowsSearchablePdfExportOperationAdapter(
                    _candidate_adapter(),
                    cast(Any, _CancelAfterPreflightStorage(cancel)),
                ).export(context, request)

            assert raised.value.code is PdfExportErrorCode.CANCELLED
            for handle in (
                source_worker,
                plan_worker,
                output_worker,
                status_worker,
                result_worker,
            ):
                assert not _handle_is_open(handle)


def test_resource_preflight_reserves_sources_two_outputs_status_and_scratch(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(PDF_FIXTURE.read_bytes())
    plan = _plan(source_path)
    status_capacity = 256 * 1024
    output_capacity = 2 * 1024 * 1024
    fixed = plan.sources[0].byte_size + (2 * output_capacity) + status_capacity
    context = _context(
        plan,
        b"x",
        limits=ResourceLimits(
            wall_time_ms=30_000,
            cpu_time_ms=20_000,
            memory_bytes=32 * 1024 * 1024,
            temporary_storage_bytes=fixed,
            output_bytes=output_capacity,
            page_count=10,
            pixel_count=1024 * 1024,
        ),
    )

    with pytest.raises(PdfExportError) as raised:
        operation._adapter_limits(context, plan, status_capacity)

    assert raised.value.code is PdfExportErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "export.worker_capacity"


def test_resource_preflight_classifies_subreport_output_capacity_as_limit(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(PDF_FIXTURE.read_bytes())
    plan = _plan(source_path)
    context = _context(
        plan,
        b"x",
        limits=ResourceLimits(
            wall_time_ms=30_000,
            cpu_time_ms=20_000,
            memory_bytes=32 * 1024 * 1024,
            temporary_storage_bytes=32 * 1024 * 1024,
            output_bytes=1,
            page_count=10,
            pixel_count=1024 * 1024,
        ),
    )

    with pytest.raises(PdfExportError) as raised:
        operation._adapter_limits(context, plan, 256 * 1024)

    assert raised.value.code is PdfExportErrorCode.LIMIT_EXCEEDED
    assert raised.value.context == "export.worker_capacity"


def test_derived_limits_admit_transform_ceiling_and_smaller_verification_request(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(PDF_FIXTURE.read_bytes())
    original_plan = _plan(source_path)
    large_source = replace(original_plan.sources[0], byte_size=40 * 1024 * 1024)
    plan = SearchablePdfExportPlan(
        case_id=original_plan.case_id,
        manifest_revision=original_plan.manifest_revision,
        manifest_sha256=original_plan.manifest_sha256,
        manifest_cas_token=original_plan.manifest_cas_token,
        sources=(large_source,),
        pages=original_plan.pages,
        profile=original_plan.profile,
    )
    temporary_bytes = 256 * 1024 * 1024
    output_bytes = 32 * 1024 * 1024
    status_bytes = 1024 * 1024
    transform_pixels = 20_000_000
    context = _context(
        plan,
        b"x",
        limits=ResourceLimits(
            wall_time_ms=120_000,
            cpu_time_ms=120_000,
            memory_bytes=512 * 1024 * 1024,
            temporary_storage_bytes=temporary_bytes,
            output_bytes=output_bytes,
            page_count=10,
            pixel_count=transform_pixels,
        ),
    )

    limits = operation._adapter_limits(context, plan, status_bytes)

    assert limits.rendering.maximum_width_pixels == limits.pdf_transform_width_pixels
    assert limits.rendering.maximum_height_pixels == limits.pdf_transform_height_pixels
    assert limits.image_normalization.maximum_source_pixels == transform_pixels
    assert 1024 * 1024 < limits.image_transform.maximum_output_pixels < transform_pixels
    assert limits.rendering.maximum_pixels == limits.image_transform.maximum_output_pixels
    assert limits.verification_width_pixels == 1024
    assert limits.verification_height_pixels == 1024
    assert limits.text_presence.maximum_source_bytes == large_source.byte_size
    assert limits.maximum_page_scratch_bytes == (
        temporary_bytes - large_source.byte_size - (2 * output_bytes) - status_bytes
    )
    assert (
        max(
            max(
                limits.image_normalization.maximum_output_bytes,
                limits.image_transform.maximum_source_pixels * 4,
            )
            + limits.image_transform.maximum_output_bytes,
            limits.image_transform.maximum_output_bytes + limits.raster_pdf.maximum_output_bytes,
        )
        <= limits.maximum_page_scratch_bytes
    )
    assert (
        limits.maximum_ocr_temporary_storage_bytes
        == limits.inspection.maximum_input_bytes
        == temporary_bytes
    )


def test_handler_progress_uses_exact_plan_total_not_resource_ceiling(tmp_path: Path) -> None:
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(PDF_FIXTURE.read_bytes())
    plan = _plan(source_path)
    progress: list[OperationProgressPayload] = []
    context = replace(
        _context(plan, b"x", progress=progress),
        parameters=_operation_parameters(),
    )

    class _ExactProgressAdapter:
        def discard_handles(self, *_args: object) -> None:
            return None

        def export(
            self,
            selected_context: OperationContext,
            request: SearchablePdfExportOperationRequest,
        ) -> tuple[CaseObjectReference, CaseObjectReference]:
            selected_context.report_progress(operation._processing_progress(0, plan.page_count))
            selected_context.report_progress(
                operation._processing_progress(plan.page_count, plan.page_count)
            )
            return (
                CaseObjectReference(
                    ReferenceArea.OPERATION_STAGING,
                    request.output_object_id,
                    selected_context.operation_id,
                    SEARCHABLE_PDF_EXPORT_OUTPUT_CONTENT_KIND,
                    1,
                    "a" * 64,
                ),
                CaseObjectReference(
                    ReferenceArea.OPERATION_STAGING,
                    request.status_object_id,
                    selected_context.operation_id,
                    SEARCHABLE_PDF_EXPORT_STATUS_CONTENT_KIND,
                    1,
                    "b" * 64,
                ),
            )

    result = SearchablePdfExportOperationHandler(cast(Any, _ExactProgressAdapter())).execute(
        context
    )

    assert result.outcome is OperationOutcome.SUCCEEDED
    assert {fact.total_units for fact in progress} == {plan.page_count}


def test_nested_ocr_error_mapping_is_exhaustive_stable_and_sanitized() -> None:
    expected = {
        OcrErrorCode.MODEL_INVALID: PdfExportErrorCode.RESULT_INVALID,
        OcrErrorCode.SOURCE_INVALID: PdfExportErrorCode.ASSEMBLY_FAILED,
        OcrErrorCode.SOURCE_ENCRYPTED: PdfExportErrorCode.ASSEMBLY_FAILED,
        OcrErrorCode.SOURCE_ENCRYPTION_UNKNOWN: PdfExportErrorCode.ASSEMBLY_FAILED,
        OcrErrorCode.SIGNED_SOURCE_REQUIRES_CONFIRMATION: PdfExportErrorCode.ASSEMBLY_FAILED,
        OcrErrorCode.LANGUAGE_UNAVAILABLE: PdfExportErrorCode.WORKER_FAILED,
        OcrErrorCode.LIMIT_EXCEEDED: PdfExportErrorCode.LIMIT_EXCEEDED,
        OcrErrorCode.CANCELLED: PdfExportErrorCode.CANCELLED,
        OcrErrorCode.TIMED_OUT: PdfExportErrorCode.TIMED_OUT,
        OcrErrorCode.ENGINE_UNAVAILABLE: PdfExportErrorCode.WORKER_FAILED,
        OcrErrorCode.ENGINE_FAILED: PdfExportErrorCode.WORKER_FAILED,
        OcrErrorCode.OUTPUT_INVALID: PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
        OcrErrorCode.RESULT_INVALID: PdfExportErrorCode.RESULT_INVALID,
        OcrErrorCode.INTERNAL_ERROR: PdfExportErrorCode.INTERNAL_ERROR,
    }
    assert set(expected) == set(OcrErrorCode)

    for source_code, export_code in expected.items():
        with pytest.raises(PdfExportError) as raised:
            operation._map_nested_processing_error(
                OcrError(source_code, "ocr.private_nested_context")
            )

        assert raised.value.code is export_code
        assert raised.value.context == "export.searchable_ocr"
        assert "private_nested_context" not in str(raised.value)
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None


def test_nested_image_error_mapping_is_exhaustive_stable_and_sanitized() -> None:
    expected = {
        ImagePageErrorCode.MODEL_INVALID: PdfExportErrorCode.RESULT_INVALID,
        ImagePageErrorCode.SOURCE_INVALID: PdfExportErrorCode.SOURCE_INCONSISTENT,
        ImagePageErrorCode.FORMAT_MISMATCH: PdfExportErrorCode.SOURCE_INCONSISTENT,
        ImagePageErrorCode.METADATA_INVALID: PdfExportErrorCode.SOURCE_INCONSISTENT,
        ImagePageErrorCode.GEOMETRY_INVALID: PdfExportErrorCode.TRANSFORM_UNSUPPORTED,
        ImagePageErrorCode.LIMIT_EXCEEDED: PdfExportErrorCode.LIMIT_EXCEEDED,
        ImagePageErrorCode.MALFORMED: PdfExportErrorCode.SOURCE_INCONSISTENT,
        ImagePageErrorCode.UNSUPPORTED: PdfExportErrorCode.SOURCE_UNSUPPORTED,
        ImagePageErrorCode.CANCELLED: PdfExportErrorCode.CANCELLED,
        ImagePageErrorCode.TIMED_OUT: PdfExportErrorCode.TIMED_OUT,
        ImagePageErrorCode.DEPENDENCY_UNAVAILABLE: PdfExportErrorCode.WORKER_FAILED,
        ImagePageErrorCode.DEPENDENCY_FAILED: PdfExportErrorCode.WORKER_FAILED,
        ImagePageErrorCode.DESTINATION_FAILED: PdfExportErrorCode.ASSEMBLY_FAILED,
        ImagePageErrorCode.RESULT_INVALID: PdfExportErrorCode.RESULT_INVALID,
        ImagePageErrorCode.CACHE_INVALID: PdfExportErrorCode.RESULT_INVALID,
        ImagePageErrorCode.INTERNAL_ERROR: PdfExportErrorCode.INTERNAL_ERROR,
    }
    assert set(expected) == set(ImagePageErrorCode)

    for source_code, export_code in expected.items():
        with pytest.raises(PdfExportError) as raised:
            operation._map_nested_processing_error(
                ImagePageError(source_code, "image.private_nested_context")
            )

        assert raised.value.code is export_code
        assert raised.value.context == "export.searchable_image"
        assert "private_nested_context" not in str(raised.value)
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None


@pytest.mark.parametrize(
    ("nested_error", "expected_outcome"),
    (
        (OcrError(OcrErrorCode.CANCELLED, "ocr.private"), OperationOutcome.CANCELLED),
        (OcrError(OcrErrorCode.TIMED_OUT, "ocr.private"), OperationOutcome.TIMED_OUT),
        (
            ImagePageError(ImagePageErrorCode.CANCELLED, "image.private"),
            OperationOutcome.CANCELLED,
        ),
        (
            ImagePageError(ImagePageErrorCode.TIMED_OUT, "image.private"),
            OperationOutcome.TIMED_OUT,
        ),
    ),
)
def test_nested_terminal_failures_drive_exact_handler_terminal_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    nested_error: OcrError | ImagePageError,
    expected_outcome: OperationOutcome,
) -> None:
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(PDF_FIXTURE.read_bytes())
    plan = _plan(source_path)
    plan_payload = encode_searchable_pdf_worker_plan(
        plan,
        {plan.sources[0].source_id: 5},
    )
    context = replace(
        _context(plan, plan_payload),
        parameters=_operation_parameters(),
    )
    closed: list[int] = []

    def read_plan(
        handle: int,
        *,
        byte_count: int,
        expected_sha256: str,
    ) -> bytes:
        assert byte_count == len(plan_payload)
        assert expected_sha256 == hashlib.sha256(plan_payload).hexdigest()
        closed.append(handle)
        return plan_payload

    def fail_processing(*_args: object, **_kwargs: object) -> object:
        raise nested_error

    monkeypatch.setattr(operation, "read_pdf_export_plan_section", read_plan)
    monkeypatch.setattr(operation, "_close_native_handle_no_raise", closed.append)
    monkeypatch.setattr(
        operation,
        "_stage_sources",
        lambda *_args, **_kwargs: {plan.sources[0].source_id: source_path},
    )
    monkeypatch.setattr(
        SearchablePdfExportAdapter,
        "assemble_and_verify",
        fail_processing,
    )
    monkeypatch.setattr(operation, "_materialize_worker_private_temp", lambda: tmp_path)
    storage = OsWorkerPrivateTempStorage(
        available_bytes_probe=lambda _root: 64 * 1024 * 1024,
        minimum_free_space_reserve_bytes=0,
    )

    result = SearchablePdfExportOperationHandler(
        WindowsSearchablePdfExportOperationAdapter(
            _candidate_adapter(),
            cast(Any, storage),
        )
    ).execute(context)

    assert result.outcome is expected_outcome
    assert result.outputs == ()
    assert set(closed) == {1, 2, 3, 4, 5}


def test_exact_available_qpdf_and_tesseract_authorities_are_admitted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qpdf = (tmp_path / "qpdf.exe").resolve()
    tesseract = (tmp_path / "tesseract.exe").resolve()
    tessdata = (tmp_path / "tessdata").resolve()
    private_root = (tmp_path / "private").resolve()
    private_temp = private_root / "Temp"
    qpdf.write_bytes(b"qpdf")
    tesseract.write_bytes(b"tesseract")
    tessdata.mkdir()
    private_root.mkdir()
    monkeypatch.setenv("PWS_QPDF_EXECUTABLE", os.fspath(qpdf))
    monkeypatch.setenv("PWS_TESSERACT_EXECUTABLE", os.fspath(tesseract))
    monkeypatch.setenv("PWS_TESSDATA_ROOT", os.fspath(tessdata))
    monkeypatch.setenv("LOCALAPPDATA", os.fspath(private_root))
    monkeypatch.setenv("TEMP", os.fspath(private_temp))
    monkeypatch.setenv("TMP", os.fspath(private_temp))
    monkeypatch.setattr(operation, "distribution_version", lambda _name: "17.10.0")
    monkeypatch.setattr(operation, "loaded_ocrmypdf_version", lambda: "17.10.0")
    admitted_qpdf: list[tuple[Path, float]] = []

    def require_trusted_qpdf_version(
        checker: QpdfCommandChecker,
        *,
        deadline: float,
    ) -> None:
        admitted_qpdf.append((checker.executable, deadline))

    monkeypatch.setattr(
        QpdfCommandChecker,
        "require_trusted_version",
        require_trusted_qpdf_version,
    )

    class _Runner:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def version(self) -> str:
            return "5.5.3"

        def languages(self) -> frozenset[str]:
            return frozenset({"eng"})

    monkeypatch.setattr(operation, "TesseractCommandRunner", _Runner)

    handler = build_searchable_pdf_export_operation_handler()

    assert type(handler) is SearchablePdfExportOperationHandler
    adapter = cast(WindowsSearchablePdfExportOperationAdapter, handler.adapter)
    candidate = adapter.candidate_adapter
    ocr = cast(OcrMyPdfAdapter, candidate.ocr_processor)
    checker = cast(QpdfCommandChecker, candidate.qpdf_checker)
    assert ocr.tesseract_executable == tesseract
    assert ocr.tessdata_directory == tessdata
    assert checker.executable == qpdf
    assert len(admitted_qpdf) == 1
    assert admitted_qpdf[0][0] == qpdf
    assert admitted_qpdf[0][1] > time.monotonic()
    assert all(
        os.fspath(path) not in f"{handler!r} {adapter!r} {candidate!r}"
        for path in (qpdf, tesseract, tessdata)
    )


def test_qpdf_identity_failure_omits_searchable_export_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qpdf = (tmp_path / "qpdf.exe").resolve()
    tesseract = (tmp_path / "tesseract.exe").resolve()
    tessdata = (tmp_path / "tessdata").resolve()
    qpdf.write_bytes(b"qpdf")
    tesseract.write_bytes(b"tesseract")
    tessdata.mkdir()
    monkeypatch.setenv("PWS_QPDF_EXECUTABLE", os.fspath(qpdf))
    monkeypatch.setenv("PWS_TESSERACT_EXECUTABLE", os.fspath(tesseract))
    monkeypatch.setenv("PWS_TESSDATA_ROOT", os.fspath(tessdata))
    monkeypatch.setattr(operation, "distribution_version", lambda _name: "17.10.0")
    monkeypatch.setattr(operation, "loaded_ocrmypdf_version", lambda: "17.10.0")

    def reject_untrusted_qpdf(
        _checker: QpdfCommandChecker,
        *,
        deadline: float,
    ) -> None:
        assert deadline > time.monotonic()
        raise PdfInspectionError(
            PdfInspectionErrorCode.DEPENDENCY_UNAVAILABLE,
            "qpdf.version",
        )

    monkeypatch.setattr(
        QpdfCommandChecker,
        "require_trusted_version",
        reject_untrusted_qpdf,
    )
    monkeypatch.setattr(
        operation,
        "_materialize_worker_private_temp",
        lambda: pytest.fail("qpdf identity must be admitted before OCR setup"),
    )

    assert build_searchable_pdf_export_operation_handler() is None


@pytest.mark.parametrize(
    ("attribute", "untrusted_version"),
    (("__version__", "10.10.1"), ("__libqpdf_version__", "12.3.3")),
)
def test_pikepdf_engine_identity_mismatch_omits_searchable_export_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attribute: str,
    untrusted_version: str,
) -> None:
    qpdf = (tmp_path / "qpdf.exe").resolve()
    tesseract = (tmp_path / "tesseract.exe").resolve()
    tessdata = (tmp_path / "tessdata").resolve()
    qpdf.write_bytes(b"qpdf")
    tesseract.write_bytes(b"tesseract")
    tessdata.mkdir()
    monkeypatch.setenv("PWS_QPDF_EXECUTABLE", os.fspath(qpdf))
    monkeypatch.setenv("PWS_TESSERACT_EXECUTABLE", os.fspath(tesseract))
    monkeypatch.setenv("PWS_TESSDATA_ROOT", os.fspath(tessdata))
    monkeypatch.setattr(operation, "distribution_version", lambda _name: "17.10.0")
    monkeypatch.setattr(operation, "loaded_ocrmypdf_version", lambda: "17.10.0")
    trusted = {
        "__version__": "10.10.0",
        "__libqpdf_version__": "12.3.2",
    }
    trusted[attribute] = untrusted_version
    monkeypatch.setattr(
        operation,
        "loaded_pikepdf_engine_versions",
        lambda: (trusted["__version__"], trusted["__libqpdf_version__"]),
    )
    monkeypatch.setattr(
        QpdfCommandChecker,
        "require_trusted_version",
        lambda *_args, **_kwargs: pytest.fail(
            "pikepdf identity must be admitted before subprocess probes"
        ),
    )

    assert build_searchable_pdf_export_operation_handler() is None


@pytest.mark.parametrize(
    "missing",
    ("PWS_QPDF_EXECUTABLE", "PWS_TESSERACT_EXECUTABLE", "PWS_TESSDATA_ROOT"),
)
def test_missing_combined_engine_authority_omits_handler(
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    monkeypatch.setenv("PWS_QPDF_EXECUTABLE", r"C:\tools\qpdf.exe")
    monkeypatch.setenv("PWS_TESSERACT_EXECUTABLE", r"C:\tools\tesseract.exe")
    monkeypatch.setenv("PWS_TESSDATA_ROOT", r"C:\tools\tessdata")
    monkeypatch.delenv(missing)

    assert build_searchable_pdf_export_operation_handler() is None

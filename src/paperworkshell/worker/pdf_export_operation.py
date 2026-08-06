"""Production Windows worker composition for deterministic verified PDF export."""

from __future__ import annotations

import ctypes
import hashlib
import hmac
import math
import os
import shutil
import stat
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Final, Protocol, cast
from uuid import UUID, uuid4

from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportPlan,
    PdfExportVerificationReport,
    reject_pdf_export,
)
from paperworkshell.domain.pdf_inspection import (
    MAX_PDF_INSPECTION_OBJECTS,
    PdfInspectionLimits,
)
from paperworkshell.domain.pdf_rendering import (
    MAX_PDF_RENDER_BYTES,
    MAX_PDF_RENDER_PAGES,
    MAX_PDF_RENDER_PIXELS,
    MAX_PDF_RENDER_WALL_TIME_MS,
    PdfPageRenderLimits,
)
from paperworkshell.pdf.export_adapter import (
    PdfExportAdapter as PdfCandidateAdapter,
)
from paperworkshell.pdf.export_adapter import (
    PdfExportAdapterControl,
    PdfExportAdapterLimits,
)
from paperworkshell.pdf.qpdf_checker import QpdfCommandChecker
from paperworkshell.worker.operations import (
    OperationContext,
    PdfExportOperationHandler,
    PdfExportOperationRequest,
)
from paperworkshell.worker.pdf_export_buffers import (
    PdfExportSectionWriteResult,
    read_pdf_export_plan_section,
    write_pdf_export_output_section,
)
from paperworkshell.worker.pdf_export_plan import (
    PdfExportWorkerPlan,
    PdfExportWorkerSource,
    decode_pdf_export_worker_plan,
)
from paperworkshell.worker.pdf_export_report import (
    MAX_PDF_EXPORT_WORKER_REPORT_BYTES,
    PdfExportWorkerReport,
    encode_pdf_export_worker_report,
)
from paperworkshell.worker.protocol.models import (
    PDF_EXPORT_OUTPUT_CONTENT_KIND,
    PDF_EXPORT_PLAN_CONTENT_KIND,
    CaseObjectReference,
    ReferenceArea,
)

if os.name == "nt":
    import msvcrt

_COPY_CHUNK_BYTES: Final = 1024 * 1024
_FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x0000_0400
_MAXIMUM_QPDF_OUTPUT_BYTES: Final = 1024 * 1024
_MINIMUM_OBJECT_LIMIT: Final = 4_096
_OBJECTS_PER_PAGE_LIMIT: Final = 1_024
_VERIFICATION_DIMENSION_PIXELS: Final = 1_024
_VERIFICATION_PIXELS: Final = _VERIFICATION_DIMENSION_PIXELS * _VERIFICATION_DIMENSION_PIXELS
_RGBA_BYTES_PER_PIXEL: Final = 4
_QPDF_EXECUTABLE_ENVIRONMENT: Final = "PWS_QPDF_EXECUTABLE"
_OPERATION_TEMP_CREATE_ATTEMPTS: Final = 4


class PdfExportCandidateAssembler(Protocol):
    """Reviewed private-candidate adapter composed by this worker boundary."""

    def assemble_and_verify(
        self,
        plan: PdfExportPlan,
        source_paths: Mapping[UUID, Path],
        output_path: Path,
        limits: PdfExportAdapterLimits,
        control: PdfExportAdapterControl,
    ) -> PdfExportVerificationReport: ...


@dataclass(slots=True, repr=False)
class _OwnedAuthorities:
    """Deduplicate transferred values so aliases can never be closed twice."""

    _handles: set[int]

    @classmethod
    def initial(cls, request: PdfExportOperationRequest) -> _OwnedAuthorities:
        return cls({request.plan_handle, request.output_handle, request.result_handle})

    def add_discovered(self, handles: tuple[int, ...], *, already_consumed: set[int]) -> None:
        for handle in handles:
            if handle not in already_consumed:
                self._handles.add(handle)

    def transfer(self, handle: int) -> int:
        if handle not in self._handles:
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.handle_ownership")
        self._handles.remove(handle)
        return handle

    def close_all(self) -> None:
        handles = tuple(self._handles)
        self._handles.clear()
        for handle in handles:
            _close_native_handle_no_raise(handle)


@dataclass(frozen=True, slots=True, repr=False)
class _ContextMonotonic:
    context: OperationContext = field(repr=False)
    absolute_deadline: float
    monotonic: Callable[[], float] = field(default=time.monotonic, repr=False)

    def __call__(self) -> float:
        if _deadline_expired(self.context):
            return self.absolute_deadline
        try:
            value = self.monotonic()
        except Exception:
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.clock")
        if type(value) not in (int, float) or not math.isfinite(value):
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.clock")
        return float(value)


@dataclass(frozen=True, slots=True, repr=False)
class WindowsPdfExportOperationAdapter:
    """Consume all authorities, stage immutable sources, and publish verified bytes."""

    candidate_adapter: PdfExportCandidateAssembler = field(
        default_factory=PdfCandidateAdapter,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not callable(getattr(self.candidate_adapter, "assemble_and_verify", None)):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.worker_adapter")

    def discard_handles(
        self,
        context: OperationContext,
        request: PdfExportOperationRequest,
    ) -> None:
        """Consume the three discoverable transfer handles before plan decoding."""

        del context
        _OwnedAuthorities.initial(request).close_all()

    def export(
        self,
        context: OperationContext,
        request: PdfExportOperationRequest,
    ) -> CaseObjectReference:
        """Run one closed export while owning every transferred authority."""

        authorities = _OwnedAuthorities.initial(request)
        consumed_values: set[int] = set()
        try:
            if os.name != "nt":
                reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.platform")
            _validate_outer_request(context, request)
            absolute_deadline = _absolute_deadline(context)
            _require_active(context, absolute_deadline=absolute_deadline)

            plan_reference = context.inputs[0]
            plan_handle = authorities.transfer(request.plan_handle)
            consumed_values.add(plan_handle)
            payload = read_pdf_export_plan_section(
                plan_handle,
                byte_count=plan_reference.expected_byte_size or 0,
                expected_sha256=plan_reference.expected_sha256 or "",
            )
            worker_plan = decode_pdf_export_worker_plan(payload)
            source_handles = tuple(source.worker_handle for source in worker_plan.sources)
            authorities.add_discovered(
                source_handles,
                already_consumed=consumed_values,
            )
            if set(source_handles) & {
                request.plan_handle,
                request.output_handle,
                request.result_handle,
            }:
                reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.handle_collision")

            source_references = _bind_worker_plan(context, worker_plan)
            source_total = sum(source.source.byte_size for source in worker_plan.sources)
            adapter_limits = _adapter_limits(
                context,
                worker_plan.plan,
                source_total=source_total,
            )
            control = PdfExportAdapterControl(
                deadline=absolute_deadline,
                cancellation_probe=context.cancel_requested.is_set,
                monotonic=_ContextMonotonic(context, absolute_deadline),
            )

            temp_root = _materialize_worker_private_temp()
            with _worker_private_operation_temp(temp_root) as operation_temp:
                staged_sources = _stage_sources(
                    context,
                    worker_plan,
                    source_references,
                    authorities,
                    operation_temp=operation_temp,
                    absolute_deadline=absolute_deadline,
                )
                # Every case-source handle and descriptor is closed before any
                # pikepdf, PDFium, or qpdf entry point can observe the sources.
                candidate_path = operation_temp / f"{uuid4().hex}.pdf"
                if candidate_path.exists():
                    reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.candidate_path")
                report = self.candidate_adapter.assemble_and_verify(
                    worker_plan.plan,
                    staged_sources,
                    candidate_path,
                    adapter_limits,
                    control,
                )
                _require_active(context, absolute_deadline=absolute_deadline)
                section_result = _copy_verified_candidate(
                    context,
                    request,
                    report,
                    candidate_path,
                    authorities,
                    absolute_deadline=absolute_deadline,
                )
                _write_correlated_report(
                    context,
                    request,
                    report,
                    authorities,
                    absolute_deadline=absolute_deadline,
                )
                _require_active(context, absolute_deadline=absolute_deadline)
                return _staging_reference(context, request, report, section_result)
        except PdfExportError:
            raise
        except MemoryError, RecursionError:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.worker_resources")
        except OSError:
            reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.worker_io")
        except Exception:
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.worker_adapter")
        finally:
            authorities.close_all()


def _validate_outer_request(
    context: OperationContext,
    request: PdfExportOperationRequest,
) -> None:
    if (
        type(context) is not OperationContext
        or type(request) is not PdfExportOperationRequest
        or len(context.inputs) < 2
    ):
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.worker_request")
    plan_reference = context.inputs[0]
    if (
        plan_reference.area is not ReferenceArea.OPERATION_TEMP
        or plan_reference.operation_id != context.operation_id
        or plan_reference.content_kind != PDF_EXPORT_PLAN_CONTENT_KIND
        or plan_reference.expected_byte_size is None
        or plan_reference.expected_sha256 is None
    ):
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.plan_reference")


def _bind_worker_plan(
    context: OperationContext,
    worker_plan: PdfExportWorkerPlan,
) -> dict[UUID, CaseObjectReference]:
    plan = worker_plan.plan
    if plan.case_id != context.case_id:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.plan_case")
    if plan.page_count > context.limits.page_count:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.plan_pages")
    references: dict[UUID, CaseObjectReference] = {}
    for reference in context.inputs[1:]:
        if (
            reference.area is not ReferenceArea.SOURCE_READ
            or reference.operation_id is not None
            or reference.content_kind != "pdf"
            or reference.expected_byte_size is None
            or reference.expected_sha256 is None
            or reference.object_id in references
        ):
            reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.source_reference")
        references[reference.object_id] = reference
    expected_ids = {source.source.source_id for source in worker_plan.sources}
    if set(references) != expected_ids:
        reject_pdf_export(PdfExportErrorCode.SOURCE_INCONSISTENT, "export.source_references")
    for worker_source in worker_plan.sources:
        source = worker_source.source
        reference = references[source.source_id]
        if (
            reference.expected_byte_size != source.byte_size
            or reference.expected_sha256 != source.sha256
        ):
            reject_pdf_export(PdfExportErrorCode.SOURCE_INCONSISTENT, "export.source_facts")
    return references


def _adapter_limits(
    context: OperationContext,
    plan: PdfExportPlan,
    *,
    source_total: int,
) -> PdfExportAdapterLimits:
    limits = context.limits
    if (
        source_total < 1
        or source_total > limits.temporary_storage_bytes
        or limits.output_bytes > limits.temporary_storage_bytes - source_total
        or plan.page_count > limits.page_count
    ):
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.worker_capacity")
    candidate_capacity = min(
        limits.output_bytes,
        limits.temporary_storage_bytes - source_total,
    )
    maximum_pages = min(limits.page_count, MAX_PDF_RENDER_PAGES)
    maximum_pixels = min(limits.pixel_count, _VERIFICATION_PIXELS, MAX_PDF_RENDER_PIXELS)
    maximum_bitmap_bytes = min(
        limits.memory_bytes,
        maximum_pixels * _RGBA_BYTES_PER_PIXEL,
        MAX_PDF_RENDER_BYTES,
    )
    if candidate_capacity < 1 or maximum_bitmap_bytes < 1:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.worker_capacity")
    object_limit = min(
        MAX_PDF_INSPECTION_OBJECTS,
        max(_MINIMUM_OBJECT_LIMIT, maximum_pages * _OBJECTS_PER_PAGE_LIMIT),
    )
    return PdfExportAdapterLimits(
        inspection=PdfInspectionLimits(
            maximum_pages=maximum_pages,
            maximum_objects=object_limit,
            maximum_input_bytes=limits.temporary_storage_bytes,
            maximum_temporary_bytes=candidate_capacity,
            maximum_output_bytes=max(4_096, limits.output_bytes),
            maximum_tool_output_bytes=min(
                limits.output_bytes,
                _MAXIMUM_QPDF_OUTPUT_BYTES,
            ),
        ),
        rendering=PdfPageRenderLimits(
            maximum_pages=maximum_pages,
            maximum_width_pixels=_VERIFICATION_DIMENSION_PIXELS,
            maximum_height_pixels=_VERIFICATION_DIMENSION_PIXELS,
            maximum_pixels=maximum_pixels,
            maximum_bitmap_bytes=maximum_bitmap_bytes,
            maximum_destination_bytes=maximum_bitmap_bytes,
            maximum_memory_bytes=maximum_bitmap_bytes,
            wall_time_ms=min(limits.wall_time_ms, MAX_PDF_RENDER_WALL_TIME_MS),
        ),
        maximum_total_source_bytes=limits.temporary_storage_bytes,
        verification_width_pixels=_VERIFICATION_DIMENSION_PIXELS,
        verification_height_pixels=_VERIFICATION_DIMENSION_PIXELS,
    )


def _stage_sources(
    context: OperationContext,
    worker_plan: PdfExportWorkerPlan,
    references: Mapping[UUID, CaseObjectReference],
    authorities: _OwnedAuthorities,
    *,
    operation_temp: Path,
    absolute_deadline: float,
) -> dict[UUID, Path]:
    staged: dict[UUID, Path] = {}
    for worker_source in worker_plan.sources:
        _require_active(context, absolute_deadline=absolute_deadline)
        handle = authorities.transfer(worker_source.worker_handle)
        staged[worker_source.source.source_id] = _stage_source_handle(
            context,
            worker_source,
            references[worker_source.source.source_id],
            handle,
            operation_temp=operation_temp,
            absolute_deadline=absolute_deadline,
        )
    return staged


def _stage_source_handle(
    context: OperationContext,
    worker_source: PdfExportWorkerSource,
    reference: CaseObjectReference,
    handle: int,
    *,
    operation_temp: Path,
    absolute_deadline: float,
) -> Path:
    owned_handle = handle
    source_descriptor: int | None = None
    staged_descriptor: int | None = None
    staged_path: Path | None = None
    try:
        _require_non_inheritable(handle, context="export.source_handle")
        try:
            source_descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            owned_handle = 0
        except OSError, OverflowError, ValueError:
            reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.source_handle")
        try:
            staged_descriptor, staged_name = tempfile.mkstemp(
                suffix=".pdf",
                dir=operation_temp,
            )
            staged_path = Path(staged_name)
        except OSError:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.source_temp")

        with ExitStack() as stack:
            source_stream = os.fdopen(source_descriptor, "rb", buffering=0, closefd=True)
            source_descriptor = None
            source = stack.enter_context(source_stream)
            staged_stream = os.fdopen(staged_descriptor, "w+b", buffering=0, closefd=True)
            staged_descriptor = None
            destination = stack.enter_context(staged_stream)
            _copy_and_verify_source(
                context,
                cast(BinaryIO, source),
                cast(BinaryIO, destination),
                expected_size=worker_source.source.byte_size,
                expected_digest=worker_source.source.sha256,
                reference=reference,
                absolute_deadline=absolute_deadline,
            )
        if staged_path is None:
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.source_temp")
        return staged_path
    finally:
        if source_descriptor is not None:
            _close_descriptor_no_raise(source_descriptor)
        elif owned_handle > 0:
            _close_native_handle_no_raise(owned_handle)
        if staged_descriptor is not None:
            _close_descriptor_no_raise(staged_descriptor)


def _copy_and_verify_source(
    context: OperationContext,
    source: BinaryIO,
    destination: BinaryIO,
    *,
    expected_size: int,
    expected_digest: str,
    reference: CaseObjectReference,
    absolute_deadline: float,
) -> None:
    try:
        before = os.fstat(source.fileno())
    except OSError, OverflowError, ValueError:
        reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.source_stat")
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_size != expected_size
        or reference.expected_byte_size != expected_size
        or reference.expected_sha256 != expected_digest
    ):
        reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.source_stat")

    digest = hashlib.sha256()
    copied = 0
    while copied < expected_size:
        _require_active(context, absolute_deadline=absolute_deadline)
        wanted = min(_COPY_CHUNK_BYTES, expected_size - copied)
        try:
            block = source.read(wanted)
        except OSError:
            reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.source_read")
        if type(block) is not bytes or not block or len(block) > wanted:
            reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.source_read")
        _write_exact(destination, block, context="export.source_temp_write")
        digest.update(block)
        copied += len(block)
    try:
        surplus = source.read(1)
        after = os.fstat(source.fileno())
    except OSError, OverflowError, ValueError:
        reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.source_eof")
    if (
        surplus != b""
        or not hmac.compare_digest(digest.hexdigest(), expected_digest)
        or _stat_identity(before) != _stat_identity(after)
    ):
        reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.source_verify")

    try:
        destination.flush()
        os.fsync(destination.fileno())
        staged_stat = os.fstat(destination.fileno())
        destination.seek(0)
    except OSError, OverflowError, ValueError:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.source_temp_flush")
    if not stat.S_ISREG(staged_stat.st_mode) or staged_stat.st_size != expected_size:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.source_temp_stat")
    staged_digest = _hash_exact_stream(
        context,
        destination,
        expected_size=expected_size,
        absolute_deadline=absolute_deadline,
    )
    if not hmac.compare_digest(staged_digest, expected_digest):
        reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.source_temp_verify")


def _hash_exact_stream(
    context: OperationContext,
    stream: BinaryIO,
    *,
    expected_size: int,
    absolute_deadline: float,
) -> str:
    digest = hashlib.sha256()
    consumed = 0
    while consumed < expected_size:
        _require_active(context, absolute_deadline=absolute_deadline)
        wanted = min(_COPY_CHUNK_BYTES, expected_size - consumed)
        try:
            block = stream.read(wanted)
        except OSError:
            reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.source_temp_read")
        if type(block) is not bytes or not block or len(block) > wanted:
            reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.source_temp_read")
        digest.update(block)
        consumed += len(block)
    try:
        surplus = stream.read(1)
    except OSError:
        reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.source_temp_eof")
    if surplus != b"":
        reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.source_temp_eof")
    return digest.hexdigest()


def _copy_verified_candidate(
    context: OperationContext,
    request: PdfExportOperationRequest,
    report: PdfExportVerificationReport,
    candidate_path: Path,
    authorities: _OwnedAuthorities,
    *,
    absolute_deadline: float,
) -> PdfExportSectionWriteResult:
    output_handle = authorities.transfer(request.output_handle)
    try:
        source = candidate_path.open("rb")
    except OSError:
        _close_native_handle_no_raise(output_handle)
        reject_pdf_export(PdfExportErrorCode.VERIFICATION_REOPEN_FAILED, "export.candidate_read")
    with source:
        return write_pdf_export_output_section(
            output_handle,
            cast(BinaryIO, source),
            byte_count=report.output_byte_size,
            expected_sha256=report.output_sha256,
            capacity=context.limits.output_bytes,
            checkpoint=lambda: _require_active(
                context,
                absolute_deadline=absolute_deadline,
            ),
        )


def _write_correlated_report(
    context: OperationContext,
    request: PdfExportOperationRequest,
    report: PdfExportVerificationReport,
    authorities: _OwnedAuthorities,
    *,
    absolute_deadline: float,
) -> None:
    _require_active(context, absolute_deadline=absolute_deadline)
    payload = encode_pdf_export_worker_report(
        PdfExportWorkerReport(
            operation_id=context.operation_id,
            output_object_id=request.output_object_id,
            report=report,
        )
    )
    if len(payload) > min(
        MAX_PDF_EXPORT_WORKER_REPORT_BYTES,
        context.limits.output_bytes,
    ):
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.result_size")
    result_handle = authorities.transfer(request.result_handle)
    _write_result_pipe(result_handle, payload)


def _write_result_pipe(handle: int, payload: bytes) -> None:
    try:
        _require_non_inheritable(handle, context="export.result_handle")
        kernel32 = _kernel32()
        write_file = kernel32.WriteFile
        write_file.argtypes = (
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        )
        write_file.restype = wintypes.BOOL
        storage = ctypes.create_string_buffer(payload)
        cursor = 0
        while cursor < len(payload):
            written = wintypes.DWORD()
            remaining = len(payload) - cursor
            if not write_file(
                wintypes.HANDLE(handle),
                ctypes.byref(storage, cursor),
                remaining,
                ctypes.byref(written),
                None,
            ):
                reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "export.result_write")
            count = int(written.value)
            if not 1 <= count <= remaining:
                reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "export.result_write")
            cursor += count
    except PdfExportError:
        raise
    except Exception:
        reject_pdf_export(PdfExportErrorCode.REPORT_INVALID, "export.result_write")
    finally:
        _close_native_handle_no_raise(handle)


def _staging_reference(
    context: OperationContext,
    request: PdfExportOperationRequest,
    report: PdfExportVerificationReport,
    section: PdfExportSectionWriteResult,
) -> CaseObjectReference:
    if (
        type(report) is not PdfExportVerificationReport
        or type(section) is not PdfExportSectionWriteResult
        or report.output_byte_size != section.byte_size
        or report.output_sha256 != section.sha256
    ):
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.staging_result")
    return CaseObjectReference(
        area=ReferenceArea.OPERATION_STAGING,
        object_id=request.output_object_id,
        operation_id=context.operation_id,
        content_kind=PDF_EXPORT_OUTPUT_CONTENT_KIND,
        expected_byte_size=section.byte_size,
        expected_sha256=section.sha256,
    )


def _write_exact(destination: BinaryIO, payload: bytes, *, context: str) -> None:
    offset = 0
    try:
        while offset < len(payload):
            written = destination.write(payload[offset:])
            if type(written) is not int or not 1 <= written <= len(payload) - offset:
                reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, context)
            offset += written
    except PdfExportError:
        raise
    except OSError:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, context)


def _absolute_deadline(context: OperationContext) -> float:
    try:
        current = time.monotonic()
    except Exception:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.clock")
    if type(current) not in (int, float) or not math.isfinite(current):
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.clock")
    return float(current) + (context.limits.wall_time_ms / 1_000.0)


def _require_active(context: OperationContext, *, absolute_deadline: float) -> None:
    if context.cancel_requested.is_set():
        reject_pdf_export(PdfExportErrorCode.CANCELLED, "export.worker_operation")
    if _deadline_expired(context):
        reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "export.worker_operation")
    try:
        current = time.monotonic()
    except Exception:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.clock")
    if type(current) not in (int, float) or not math.isfinite(current):
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.clock")
    if current >= absolute_deadline:
        reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "export.worker_operation")


def _deadline_expired(context: OperationContext) -> bool:
    try:
        value = context.deadline_expired()
    except Exception:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.deadline_probe")
    if type(value) is not bool:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.deadline_probe")
    return value


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
    )


def _require_non_inheritable(handle: int, *, context: str) -> None:
    try:
        inheritable = os.get_handle_inheritable(handle)
    except OSError, OverflowError, ValueError:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, context)
    if inheritable:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, context)


def _kernel32() -> Any:
    if os.name != "nt":
        reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.platform")
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _close_native_handle_no_raise(handle: int) -> None:
    if type(handle) is not int or handle <= 0 or os.name != "nt":
        return
    try:
        kernel32 = _kernel32()
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        close_handle(wintypes.HANDLE(handle))
    except BaseException:
        pass


def _close_descriptor_no_raise(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass


def _materialize_worker_private_temp() -> Path:
    """Return the isolated launch temp root after exact local identity checks."""

    try:
        local_value = os.environ.get("LOCALAPPDATA")
        temp_value = os.environ.get("TEMP")
        tmp_value = os.environ.get("TMP")
        if (
            type(local_value) is not str
            or type(temp_value) is not str
            or type(tmp_value) is not str
            or not local_value
            or not temp_value
            or not tmp_value
            or "\x00" in local_value
            or "\x00" in temp_value
            or "\x00" in tmp_value
        ):
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.temp_environment")
        local_root = Path(local_value)
        temp_root = Path(temp_value)
        tmp_root = Path(tmp_value)
        if (
            not local_root.is_absolute()
            or not temp_root.is_absolute()
            or not tmp_root.is_absolute()
            or os.path.normcase(os.path.abspath(temp_root))
            != os.path.normcase(os.path.abspath(tmp_root))
            or os.path.normcase(os.path.abspath(temp_root.parent))
            != os.path.normcase(os.path.abspath(local_root))
            or temp_root.name.casefold() != "temp"
            or os.fspath(local_root).startswith(("\\\\", "\\\\?\\", "\\\\.\\", "\\??\\"))
        ):
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.temp_scope")
        temp_root.mkdir(parents=True, exist_ok=True)
        for candidate in (local_root, temp_root):
            information = os.lstat(candidate)
            attributes = int(getattr(information, "st_file_attributes", 0))
            if not stat.S_ISDIR(information.st_mode) or attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.temp_identity")
        return temp_root
    except PdfExportError:
        raise
    except OSError, TypeError, ValueError:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.temp_unavailable")


@contextmanager
def _worker_private_operation_temp(temp_root: Path) -> Iterator[Path]:
    """Create one inherited-ACL AppContainer directory and remove it exactly once."""

    operation_temp = _create_worker_private_operation_temp(temp_root)
    body_failed = True
    try:
        yield operation_temp
        body_failed = False
    finally:
        cleanup_failed = False
        try:
            shutil.rmtree(operation_temp)
        except BaseException:
            cleanup_failed = True
        if not cleanup_failed:
            try:
                os.lstat(operation_temp)
            except FileNotFoundError:
                pass
            except BaseException:
                cleanup_failed = True
            else:
                cleanup_failed = True
        if cleanup_failed and not body_failed:
            reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.temp_cleanup")


def _create_worker_private_operation_temp(temp_root: Path) -> Path:
    """Create a random child without CPython's package-inaccessible Windows ACL."""

    for _attempt in range(_OPERATION_TEMP_CREATE_ATTEMPTS):
        candidate = temp_root / f"pws-export-{uuid4().hex}"
        try:
            # On Windows, mode 0o700 creates a new restrictive ACL that excludes
            # the AppContainer package SID. The default mode inherits the already
            # authenticated profile-private TEMP policy instead.
            candidate.mkdir()
        except FileExistsError:
            continue
        except OSError:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.temp_create")
        try:
            information = os.lstat(candidate)
        except OSError:
            _remove_untrusted_operation_temp_no_raise(candidate)
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.temp_identity")
        attributes = int(getattr(information, "st_file_attributes", 0))
        if (
            candidate.parent != temp_root
            or not stat.S_ISDIR(information.st_mode)
            or attributes & _FILE_ATTRIBUTE_REPARSE_POINT
        ):
            _remove_untrusted_operation_temp_no_raise(candidate)
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.temp_identity")
        return candidate
    reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.temp_create")


def _remove_untrusted_operation_temp_no_raise(candidate: Path) -> None:
    try:
        information = os.lstat(candidate)
    except OSError:
        return
    attributes = int(getattr(information, "st_file_attributes", 0))
    try:
        if stat.S_ISDIR(information.st_mode) and not (attributes & _FILE_ATTRIBUTE_REPARSE_POINT):
            shutil.rmtree(candidate)
        else:
            candidate.unlink(missing_ok=True)
    except BaseException:
        pass


def build_pdf_export_operation_handler() -> PdfExportOperationHandler:
    """Compose the production pikepdf/PDFium/qpdf adapter behind the handler."""

    configured_qpdf = os.environ.get(_QPDF_EXECUTABLE_ENVIRONMENT)
    if configured_qpdf is None:
        return PdfExportOperationHandler(WindowsPdfExportOperationAdapter())
    candidate_adapter = PdfCandidateAdapter(
        qpdf_checker=QpdfCommandChecker(executable=Path(configured_qpdf)),
    )
    return PdfExportOperationHandler(
        WindowsPdfExportOperationAdapter(candidate_adapter=candidate_adapter)
    )

"""Production one-shot worker adapter for flattened visual-overlay PDF export."""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import BinaryIO, Final, Never, Protocol, cast
from uuid import UUID, uuid4

from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.domain.pdf_inspection import PdfInspectionError
from paperworkshell.domain.pdf_rendering import PdfRenderError
from paperworkshell.overlays.exporting import (
    FlattenedPdfExportError,
    FlattenedPdfExportErrorCode,
    FlattenedPdfExportPlan,
    FlattenedPdfExportVerificationReport,
    reject_flattened_pdf_export,
)
from paperworkshell.overlays.models import MAX_OVERLAYS, TextualOverlayPayload
from paperworkshell.overlays.rendering import (
    MAX_OVERLAY_RENDER_BYTES,
    MAX_OVERLAY_RENDER_OBJECTS,
    MAX_OVERLAY_RENDER_WALL_TIME_MS,
    OVERLAY_ENGINE_MEMORY_RESERVE_DIVISOR,
    OVERLAY_OUTPUT_PEAK_MULTIPLIER,
    OVERLAY_RASTER_PEAK_MULTIPLIER,
    OverlayRasterSource,
    OverlayRenderError,
    OverlayRenderLimits,
)
from paperworkshell.pdf.export_adapter import (
    PdfExportAdapterControl,
    PdfExportAdapterLimits,
    loaded_pikepdf_engine_versions,
)
from paperworkshell.pdf.flattened_export_adapter import (
    FlattenedPdfExportAdapter as FlattenedCandidateAdapter,
)
from paperworkshell.pdf.overlay_renderer import (
    FpdfOverlayRenderer,
)
from paperworkshell.pdf.overlay_renderer import (
    _require_approved_runtime as require_approved_overlay_runtime,
)
from paperworkshell.pdf.pdfium_renderer import (
    PdfiumPageRenderer,
)
from paperworkshell.pdf.pdfium_renderer import (
    _require_approved_runtime as require_approved_pdfium_runtime,
)
from paperworkshell.pdf.qpdf_checker import QpdfCommandChecker
from paperworkshell.worker.flattened_pdf_export_buffers import FlattenedPdfRasterSection
from paperworkshell.worker.flattened_pdf_export_plan import (
    FlattenedPdfWorkerPlan,
    decode_flattened_pdf_worker_plan,
)
from paperworkshell.worker.flattened_pdf_export_report import (
    MAX_FLATTENED_PDF_WORKER_REPORT_BYTES,
    FlattenedPdfExportWorkerReport,
    encode_flattened_pdf_worker_report,
)
from paperworkshell.worker.operations import (
    FlattenedPdfExportOperationHandler,
    FlattenedPdfExportOperationRequest,
    OperationContext,
)
from paperworkshell.worker.pdf_export_buffers import (
    PdfExportSectionWriteResult,
    read_pdf_export_plan_section,
    write_pdf_export_output_section,
)
from paperworkshell.worker.pdf_export_operation import (
    _adapter_limits as _base_adapter_limits,
)
from paperworkshell.worker.pdf_export_operation import (
    _close_native_handle_no_raise,
    _materialize_worker_private_temp,
    _stage_source_handle,
    _worker_private_operation_temp,
    _write_result_pipe,
)
from paperworkshell.worker.pdf_export_report import (
    PIKEPDF_EXPORT_ENGINE_VERSION,
    QPDF_EXPORT_ENGINE_VERSION,
)
from paperworkshell.worker.pdf_optimization import optimize_verified_pdf
from paperworkshell.worker.private_temp import (
    OsWorkerPrivateTempStorage,
    WorkerPrivateTempStorage,
    WorkerPrivateTempStorageError,
)
from paperworkshell.worker.protocol.models import (
    FLATTENED_PDF_EXPORT_OUTPUT_CONTENT_KIND,
    FLATTENED_PDF_EXPORT_PLAN_CONTENT_KIND,
    FLATTENED_PDF_EXPORT_RASTER_CONTENT_KIND,
    CaseObjectReference,
    ReferenceArea,
)

PWS_QPDF_EXECUTABLE_ENVIRONMENT: Final = "PWS_QPDF_EXECUTABLE"
_COPY_CHUNK_BYTES: Final = 1024 * 1024
_MINIMUM_OBJECT_LIMIT: Final = 4_096
_ENGINE_ADMISSION_SECONDS: Final = 5.0


class FlattenedPdfCandidateAssembler(Protocol):
    """Reviewed native adapter seam behind the worker authority boundary."""

    def assemble_and_verify(
        self,
        plan: FlattenedPdfExportPlan,
        source_streams: Mapping[UUID, BinaryIO],
        raster_sources: Mapping[UUID, OverlayRasterSource],
        output_path: Path,
        limits: PdfExportAdapterLimits,
        overlay_limits: OverlayRenderLimits,
        control: PdfExportAdapterControl,
    ) -> FlattenedPdfExportVerificationReport: ...


@dataclass(slots=True, repr=False)
class _OwnedAuthorities:
    """Track each transferred numeric authority exactly once, including aliases."""

    _handles: set[int]

    @classmethod
    def initial(cls, request: FlattenedPdfExportOperationRequest) -> _OwnedAuthorities:
        return cls({request.plan_handle, request.output_handle, request.result_handle})

    def add_discovered(self, handles: tuple[int, ...], *, consumed: set[int]) -> None:
        for handle in handles:
            if handle not in consumed:
                self._handles.add(handle)

    def transfer(self, handle: int) -> int:
        if handle not in self._handles:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.RESULT_INVALID,
                "flattened.handle_ownership",
            )
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
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                "flattened.clock",
            )
        if type(value) not in (int, float) or not math.isfinite(value):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                "flattened.clock",
            )
        return float(value)


@dataclass(frozen=True, slots=True, repr=False)
class WindowsFlattenedPdfExportOperationAdapter:
    """Stage PDFs, map exact RGBA sections, and publish one verified output."""

    candidate_adapter: FlattenedPdfCandidateAssembler = field(
        default_factory=FlattenedCandidateAdapter,
        repr=False,
    )
    private_temp_storage: WorkerPrivateTempStorage = field(
        default_factory=OsWorkerPrivateTempStorage,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not callable(getattr(self.candidate_adapter, "assemble_and_verify", None)) or any(
            not callable(getattr(self.private_temp_storage, member, None))
            for member in ("require_capacity", "open_named")
        ):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.MODEL_INVALID,
                "flattened.worker_adapter",
            )

    def discard_handles(
        self,
        context: OperationContext,
        request: FlattenedPdfExportOperationRequest,
    ) -> None:
        """Consume the three discoverable handles before plan decoding."""

        del context
        _OwnedAuthorities.initial(request).close_all()

    def export(
        self,
        context: OperationContext,
        request: FlattenedPdfExportOperationRequest,
    ) -> CaseObjectReference:
        """Consume all transferred authority and return one verified reference."""

        authorities = _OwnedAuthorities.initial(request)
        consumed: set[int] = set()
        try:
            if os.name != "nt":
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                    "flattened.platform",
                )
            _validate_outer_request(context, request)
            absolute_deadline = _absolute_deadline(context)
            _require_active(context, absolute_deadline=absolute_deadline)

            plan_reference = context.inputs[0]
            plan_handle = authorities.transfer(request.plan_handle)
            consumed.add(plan_handle)
            plan_payload = read_pdf_export_plan_section(
                plan_handle,
                byte_count=plan_reference.expected_byte_size or 0,
                expected_sha256=plan_reference.expected_sha256 or "",
            )
            worker_plan = decode_flattened_pdf_worker_plan(plan_payload)
            del plan_payload

            source_handles = tuple(source.worker_handle for source in worker_plan.sources)
            raster_handles = tuple(asset.worker_handle for asset in worker_plan.assets)
            discovered = (*source_handles, *raster_handles)
            authorities.add_discovered(discovered, consumed=consumed)
            if set(discovered) & {
                request.plan_handle,
                request.output_handle,
                request.result_handle,
            }:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.RESULT_INVALID,
                    "flattened.handle_collision",
                )

            source_references, raster_references = _bind_worker_plan(context, worker_plan)
            source_total = sum(source.source.byte_size for source in worker_plan.sources)
            raster_total = sum(asset.asset.raster_asset.byte_count for asset in worker_plan.assets)
            adapter_limits = replace(
                _base_adapter_limits(
                    context,
                    worker_plan.plan.base_plan,
                    source_total=source_total,
                ),
                maximum_total_source_bytes=source_total,
            )
            overlay_limits = _overlay_limits(
                context,
                worker_plan.plan,
                source_total=source_total,
                raster_total=raster_total,
            )
            control = PdfExportAdapterControl(
                deadline=absolute_deadline,
                cancellation_probe=context.cancel_requested.is_set,
                monotonic=_ContextMonotonic(context, absolute_deadline),
            )

            temp_root = _materialize_worker_private_temp()
            try:
                self.private_temp_storage.require_capacity(
                    temp_root,
                    context.limits.temporary_storage_bytes,
                )
            except WorkerPrivateTempStorageError:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                    "flattened.temp_capacity",
                )
            with _worker_private_operation_temp(temp_root) as operation_temp:
                staged_sources = _stage_sources(
                    context,
                    worker_plan,
                    source_references,
                    authorities,
                    private_temp_storage=self.private_temp_storage,
                    operation_temp=operation_temp,
                    absolute_deadline=absolute_deadline,
                )
                with ExitStack() as stack:
                    source_streams = {
                        source.source.source_id: cast(
                            BinaryIO,
                            stack.enter_context(
                                staged_sources[source.source.source_id].open(
                                    "rb",
                                    buffering=0,
                                )
                            ),
                        )
                        for source in worker_plan.sources
                    }
                    raster_sources = _map_rasters(
                        context,
                        worker_plan,
                        raster_references,
                        authorities,
                        stack,
                        absolute_deadline=absolute_deadline,
                    )
                    candidate_path = operation_temp / f"{uuid4().hex}.pdf"
                    if candidate_path.exists():
                        reject_flattened_pdf_export(
                            FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                            "flattened.candidate_path",
                        )
                    report = self.candidate_adapter.assemble_and_verify(
                        worker_plan.plan,
                        source_streams,
                        raster_sources,
                        candidate_path,
                        adapter_limits,
                        overlay_limits,
                        control,
                    )
                    _require_active(context, absolute_deadline=absolute_deadline)
                    selection = optimize_verified_pdf(
                        candidate_path,
                        report.base_report,
                        operation_temp=operation_temp,
                        resource_limits=context.limits,
                        adapter_limits=adapter_limits,
                        control=control,
                        private_temp_storage=self.private_temp_storage,
                        reserved_temporary_bytes=raster_total,
                    )
                    candidate_path = selection.path
                    report = replace(report, base_report=selection.report)
                    _require_active(context, absolute_deadline=absolute_deadline)
                    section = _copy_verified_candidate(
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
                    return _staging_reference(context, request, report, section)
        except FlattenedPdfExportError:
            raise
        except PdfExportError as error:
            _translate_pdf_export_error(error)
        except MemoryError, RecursionError:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.worker_resources",
            )
        except OSError:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                "flattened.worker_io",
            )
        except Exception:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                "flattened.worker_adapter",
            )
        finally:
            authorities.close_all()


def _validate_outer_request(
    context: OperationContext,
    request: FlattenedPdfExportOperationRequest,
) -> None:
    if (
        type(context) is not OperationContext
        or type(request) is not FlattenedPdfExportOperationRequest
        or len(context.inputs) < 2
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.MODEL_INVALID,
            "flattened.worker_request",
        )
    plan = context.inputs[0]
    if (
        plan.area is not ReferenceArea.OPERATION_TEMP
        or plan.operation_id != context.operation_id
        or plan.content_kind != FLATTENED_PDF_EXPORT_PLAN_CONTENT_KIND
        or plan.expected_byte_size is None
        or plan.expected_sha256 is None
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RESULT_INVALID,
            "flattened.plan_reference",
        )


def _bind_worker_plan(
    context: OperationContext,
    worker_plan: FlattenedPdfWorkerPlan,
) -> tuple[dict[UUID, CaseObjectReference], dict[UUID, CaseObjectReference]]:
    plan = worker_plan.plan
    if plan.base_plan.case_id != context.case_id:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RESULT_INVALID,
            "flattened.plan_case",
        )
    if plan.page_count > context.limits.page_count:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.plan_pages",
        )
    source_count = len(worker_plan.sources)
    raster_count = len(worker_plan.assets)
    if len(context.inputs) != 1 + source_count + raster_count:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.SOURCE_INCONSISTENT,
            "flattened.input_count",
        )
    source_references = {
        reference.object_id: reference for reference in context.inputs[1 : 1 + source_count]
    }
    raster_references = {
        reference.object_id: reference for reference in context.inputs[1 + source_count :]
    }
    if len(source_references) != source_count or set(source_references) != {
        source.source.source_id for source in worker_plan.sources
    }:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.SOURCE_INCONSISTENT,
            "flattened.source_references",
        )
    if len(raster_references) != raster_count or set(raster_references) != {
        asset.asset.asset_id for asset in worker_plan.assets
    }:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RASTER_METADATA_INVALID,
            "flattened.raster_references",
        )
    for source in worker_plan.sources:
        expected = source.source
        reference = source_references[expected.source_id]
        if (
            reference.area is not ReferenceArea.SOURCE_READ
            or reference.operation_id is not None
            or reference.content_kind != "pdf"
            or reference.expected_byte_size != expected.byte_size
            or reference.expected_sha256 != expected.sha256
        ):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.SOURCE_INCONSISTENT,
                "flattened.source_facts",
            )
    for asset in worker_plan.assets:
        metadata = asset.asset.raster_asset
        reference = raster_references[asset.asset.asset_id]
        if (
            reference.area is not ReferenceArea.OPERATION_TEMP
            or reference.operation_id != context.operation_id
            or reference.content_kind != FLATTENED_PDF_EXPORT_RASTER_CONTENT_KIND
            or reference.expected_byte_size != metadata.byte_count
            or reference.expected_sha256 != metadata.pixel_sha256
        ):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.RASTER_METADATA_INVALID,
                "flattened.raster_facts",
            )
    return source_references, raster_references


def _stage_sources(
    context: OperationContext,
    worker_plan: FlattenedPdfWorkerPlan,
    references: Mapping[UUID, CaseObjectReference],
    authorities: _OwnedAuthorities,
    *,
    private_temp_storage: WorkerPrivateTempStorage,
    operation_temp: Path,
    absolute_deadline: float,
) -> dict[UUID, Path]:
    staged: dict[UUID, Path] = {}
    for worker_source in worker_plan.sources:
        _require_active(context, absolute_deadline=absolute_deadline)
        staged[worker_source.source.source_id] = _stage_source_handle(
            context,
            worker_source,
            references[worker_source.source.source_id],
            authorities.transfer(worker_source.worker_handle),
            private_temp_storage=private_temp_storage,
            operation_temp=operation_temp,
            absolute_deadline=absolute_deadline,
        )
    return staged


def _map_rasters(
    context: OperationContext,
    worker_plan: FlattenedPdfWorkerPlan,
    references: Mapping[UUID, CaseObjectReference],
    authorities: _OwnedAuthorities,
    stack: ExitStack,
    *,
    absolute_deadline: float,
) -> dict[UUID, OverlayRasterSource]:
    rasters: dict[UUID, OverlayRasterSource] = {}
    for worker_asset in worker_plan.assets:
        _require_active(context, absolute_deadline=absolute_deadline)
        asset = worker_asset.asset
        reference = references[asset.asset_id]
        section = FlattenedPdfRasterSection.adopt(
            authorities.transfer(worker_asset.worker_handle),
            asset.raster_asset,
            expected_byte_size=reference.expected_byte_size or 0,
            expected_sha256=reference.expected_sha256 or "",
            checkpoint=lambda: _require_active(
                context,
                absolute_deadline=absolute_deadline,
            ),
        )
        rasters[asset.asset_id] = cast(OverlayRasterSource, stack.enter_context(section))
    return rasters


def _overlay_limits(
    context: OperationContext,
    plan: FlattenedPdfExportPlan,
    *,
    source_total: int,
    raster_total: int,
) -> OverlayRenderLimits:
    values = context.limits
    memory = min(values.memory_bytes, MAX_OVERLAY_RENDER_BYTES)
    raster_metadata = tuple(asset.raster_asset for asset in plan.assets)
    maximum_raster_bytes = max((item.byte_count for item in raster_metadata), default=1)
    maximum_raster_pixels = max(
        (item.width_pixels * item.height_pixels for item in raster_metadata),
        default=1,
    )
    total_raster_allowance = max(1, raster_total)
    overlay_count = max(1, plan.visible_overlay_count)
    text_count = max(
        1,
        sum(
            len(record.payload.value)
            for page in plan.pages
            for record in page.overlays
            if type(record.payload) is TextualOverlayPayload
        ),
    )
    overlay_budget = min(MAX_OVERLAYS, max(1, memory // 4_096))
    text_budget = min(MAX_OVERLAYS * 4_096, max(1, memory // 1_024))
    object_budget = min(MAX_OVERLAY_RENDER_OBJECTS, max(1, memory // 4_096))
    available_peak = (
        memory
        * (OVERLAY_ENGINE_MEMORY_RESERVE_DIVISOR - 1)
        // OVERLAY_ENGINE_MEMORY_RESERVE_DIVISOR
    )
    raster_peak = total_raster_allowance * OVERLAY_RASTER_PEAK_MULTIPLIER
    output_peak = available_peak - raster_peak
    remaining_temp = values.temporary_storage_bytes - source_total - raster_total
    if (
        source_total < 1
        or raster_total < 0
        or raster_total > values.memory_bytes
        or maximum_raster_pixels > values.pixel_count
        or overlay_count > overlay_budget
        or text_count > text_budget
        or object_budget < _MINIMUM_OBJECT_LIMIT
        or output_peak < OVERLAY_OUTPUT_PEAK_MULTIPLIER
        or remaining_temp < 1
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.overlay_limits",
        )
    maximum_output = min(
        values.output_bytes,
        remaining_temp,
        output_peak // OVERLAY_OUTPUT_PEAK_MULTIPLIER,
        MAX_OVERLAY_RENDER_BYTES,
    )
    if maximum_output < 1:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.overlay_limits",
        )
    try:
        return OverlayRenderLimits(
            maximum_overlays=overlay_count,
            maximum_total_text_code_points=text_count,
            maximum_raster_width_pixels=max(
                (item.width_pixels for item in raster_metadata),
                default=1,
            ),
            maximum_raster_height_pixels=max(
                (item.height_pixels for item in raster_metadata),
                default=1,
            ),
            maximum_raster_pixels=maximum_raster_pixels,
            maximum_raster_bytes=maximum_raster_bytes,
            maximum_total_raster_bytes=total_raster_allowance,
            maximum_output_bytes=maximum_output,
            maximum_memory_bytes=memory,
            maximum_pdf_objects=object_budget,
            io_chunk_bytes=min(_COPY_CHUNK_BYTES, memory),
            wall_time_ms=min(values.wall_time_ms, MAX_OVERLAY_RENDER_WALL_TIME_MS),
        )
    except OverlayRenderError as error:
        if error.code.value.endswith("LIMIT_EXCEEDED"):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.overlay_limits",
            )
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.MODEL_INVALID,
            "flattened.overlay_limits",
        )


def _copy_verified_candidate(
    context: OperationContext,
    request: FlattenedPdfExportOperationRequest,
    report: FlattenedPdfExportVerificationReport,
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
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.VERIFICATION_REOPEN_FAILED,
            "flattened.candidate_read",
        )
    with source:
        return write_pdf_export_output_section(
            output_handle,
            cast(BinaryIO, source),
            byte_count=report.base_report.output_byte_size,
            expected_sha256=report.base_report.output_sha256,
            capacity=context.limits.output_bytes,
            checkpoint=lambda: _require_active(
                context,
                absolute_deadline=absolute_deadline,
            ),
        )


def _write_correlated_report(
    context: OperationContext,
    request: FlattenedPdfExportOperationRequest,
    report: FlattenedPdfExportVerificationReport,
    authorities: _OwnedAuthorities,
    *,
    absolute_deadline: float,
) -> None:
    _require_active(context, absolute_deadline=absolute_deadline)
    payload = encode_flattened_pdf_worker_report(
        FlattenedPdfExportWorkerReport(
            operation_id=context.operation_id,
            output_object_id=request.output_object_id,
            report=report,
        )
    )
    if len(payload) > min(
        MAX_FLATTENED_PDF_WORKER_REPORT_BYTES,
        context.limits.output_bytes,
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.result_size",
        )
    _write_result_pipe(authorities.transfer(request.result_handle), payload)


def _staging_reference(
    context: OperationContext,
    request: FlattenedPdfExportOperationRequest,
    report: FlattenedPdfExportVerificationReport,
    section: PdfExportSectionWriteResult,
) -> CaseObjectReference:
    if (
        type(report) is not FlattenedPdfExportVerificationReport
        or type(section) is not PdfExportSectionWriteResult
        or report.base_report.output_byte_size != section.byte_size
        or report.base_report.output_sha256 != section.sha256
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RESULT_INVALID,
            "flattened.staging_result",
        )
    return CaseObjectReference(
        area=ReferenceArea.OPERATION_STAGING,
        object_id=request.output_object_id,
        operation_id=context.operation_id,
        content_kind=FLATTENED_PDF_EXPORT_OUTPUT_CONTENT_KIND,
        expected_byte_size=section.byte_size,
        expected_sha256=section.sha256,
    )


def _absolute_deadline(context: OperationContext) -> float:
    try:
        current = time.monotonic()
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.INTERNAL_ERROR,
            "flattened.clock",
        )
    if type(current) not in (int, float) or not math.isfinite(current):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.INTERNAL_ERROR,
            "flattened.clock",
        )
    return float(current) + (context.limits.wall_time_ms / 1_000.0)


def _require_active(context: OperationContext, *, absolute_deadline: float) -> None:
    if context.cancel_requested.is_set():
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.CANCELLED,
            "flattened.worker_operation",
        )
    if _deadline_expired(context):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.TIMED_OUT,
            "flattened.worker_operation",
        )
    try:
        current = time.monotonic()
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.INTERNAL_ERROR,
            "flattened.clock",
        )
    if type(current) not in (int, float) or not math.isfinite(current):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.INTERNAL_ERROR,
            "flattened.clock",
        )
    if current >= absolute_deadline:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.TIMED_OUT,
            "flattened.worker_operation",
        )


def _deadline_expired(context: OperationContext) -> bool:
    try:
        value = context.deadline_expired()
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.INTERNAL_ERROR,
            "flattened.deadline_probe",
        )
    if type(value) is not bool:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.INTERNAL_ERROR,
            "flattened.deadline_probe",
        )
    return value


def _translate_pdf_export_error(error: PdfExportError) -> Never:
    direct = {
        PdfExportErrorCode.MODEL_INVALID: FlattenedPdfExportErrorCode.MODEL_INVALID,
        PdfExportErrorCode.PLAN_EMPTY: FlattenedPdfExportErrorCode.PLAN_EMPTY,
        PdfExportErrorCode.SOURCE_UNSUPPORTED: FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
        PdfExportErrorCode.SOURCE_ENCRYPTED: FlattenedPdfExportErrorCode.SOURCE_ENCRYPTED,
        PdfExportErrorCode.SOURCE_ENCRYPTION_UNKNOWN: (
            FlattenedPdfExportErrorCode.SOURCE_ENCRYPTION_UNKNOWN
        ),
        PdfExportErrorCode.SOURCE_SIGNATURE_UNKNOWN: (
            FlattenedPdfExportErrorCode.SOURCE_SIGNATURE_UNKNOWN
        ),
        PdfExportErrorCode.SOURCE_INCONSISTENT: (FlattenedPdfExportErrorCode.SOURCE_INCONSISTENT),
        PdfExportErrorCode.SOURCE_CHANGED: FlattenedPdfExportErrorCode.SOURCE_CHANGED,
        PdfExportErrorCode.TRANSFORM_UNSUPPORTED: (
            FlattenedPdfExportErrorCode.TRANSFORM_UNSUPPORTED
        ),
        PdfExportErrorCode.LIMIT_EXCEEDED: FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
        PdfExportErrorCode.CANCELLED: FlattenedPdfExportErrorCode.CANCELLED,
        PdfExportErrorCode.TIMED_OUT: FlattenedPdfExportErrorCode.TIMED_OUT,
        PdfExportErrorCode.ASSEMBLY_FAILED: FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
        PdfExportErrorCode.VERIFICATION_REOPEN_FAILED: (
            FlattenedPdfExportErrorCode.VERIFICATION_REOPEN_FAILED
        ),
        PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED: (
            FlattenedPdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED
        ),
        PdfExportErrorCode.VERIFICATION_PAGE_COUNT_MISMATCH: (
            FlattenedPdfExportErrorCode.VERIFICATION_PAGE_COUNT_MISMATCH
        ),
        PdfExportErrorCode.VERIFICATION_ENCRYPTION_FAILED: (
            FlattenedPdfExportErrorCode.VERIFICATION_ENCRYPTION_FAILED
        ),
        PdfExportErrorCode.VERIFICATION_RENDER_FAILED: (
            FlattenedPdfExportErrorCode.VERIFICATION_RENDER_FAILED
        ),
        PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH: (
            FlattenedPdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH
        ),
        PdfExportErrorCode.REPORT_INVALID: FlattenedPdfExportErrorCode.REPORT_INVALID,
        PdfExportErrorCode.RESULT_INVALID: FlattenedPdfExportErrorCode.RESULT_INVALID,
        PdfExportErrorCode.INTERNAL_ERROR: FlattenedPdfExportErrorCode.INTERNAL_ERROR,
    }
    reject_flattened_pdf_export(
        direct.get(error.code, FlattenedPdfExportErrorCode.INTERNAL_ERROR),
        "flattened.worker_dependency",
    )


def build_flattened_pdf_export_operation_handler() -> FlattenedPdfExportOperationHandler | None:
    """Advertise only after the exact local PDF and overlay engines are admitted."""

    configured_qpdf = os.environ.get(PWS_QPDF_EXECUTABLE_ENVIRONMENT)
    if (
        os.name != "nt"
        or type(configured_qpdf) is not str
        or not configured_qpdf
        or "\x00" in configured_qpdf
    ):
        return None
    qpdf_path = Path(configured_qpdf)
    try:
        if (
            not qpdf_path.is_absolute()
            or not qpdf_path.is_file()
            or loaded_pikepdf_engine_versions()
            != (PIKEPDF_EXPORT_ENGINE_VERSION, QPDF_EXPORT_ENGINE_VERSION)
        ):
            return None
        require_approved_pdfium_runtime()
        require_approved_overlay_runtime()
        deadline = time.monotonic() + _ENGINE_ADMISSION_SECONDS
        qpdf_checker = QpdfCommandChecker(executable=qpdf_path)
        qpdf_checker.require_trusted_version(deadline=deadline)
        candidate = FlattenedCandidateAdapter(
            qpdf_checker=qpdf_checker,
            renderer=PdfiumPageRenderer(),
            overlay_renderer=FpdfOverlayRenderer(),
        )
    except (
        FlattenedPdfExportError,
        PdfExportError,
        PdfInspectionError,
        PdfRenderError,
        OverlayRenderError,
        ImportError,
        OSError,
        ValueError,
    ):
        return None
    return FlattenedPdfExportOperationHandler(
        WindowsFlattenedPdfExportOperationAdapter(candidate_adapter=candidate)
    )

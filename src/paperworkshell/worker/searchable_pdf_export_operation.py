"""Production one-shot worker adapter for searchable mixed-PDF export."""

from __future__ import annotations

import hashlib
import io
import math
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from pathlib import Path
from typing import Final, Never, cast
from uuid import UUID, uuid4

from paperworkshell.domain.image_pages import (
    IMAGE_RGBA8_BYTES_PER_PIXEL,
    MAX_IMAGE_DECOMPRESSED_BYTES,
    MAX_IMAGE_DIMENSION_PIXELS,
    MAX_IMAGE_MEMORY_BYTES,
    MAX_IMAGE_METADATA_BYTES,
    MAX_IMAGE_METADATA_RECORDS,
    MAX_IMAGE_OUTPUT_BYTES,
    MAX_IMAGE_OUTPUT_PIXELS,
    MAX_IMAGE_SOURCE_BYTES,
    MAX_IMAGE_SOURCE_PIXELS,
    MAX_IMAGE_WALL_TIME_MS,
    ImageNormalizationLimits,
    ImagePageError,
    ImagePageErrorCode,
)
from paperworkshell.domain.image_transforms import (
    MAX_IMAGE_TRANSFORM_TEMPORARY_BYTES,
    ImageTransformLimits,
)
from paperworkshell.domain.ocr import (
    TRUSTED_OCRMYPDF_VERSION,
    TRUSTED_TESSERACT_VERSION,
    OcrError,
    OcrErrorCode,
)
from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode, reject_pdf_export
from paperworkshell.domain.pdf_inspection import (
    MAX_PDF_INSPECTION_OBJECTS,
    MAX_PDF_INSPECTION_REPORT_BYTES,
    PdfInspectionError,
    PdfInspectionLimits,
)
from paperworkshell.domain.pdf_rendering import (
    MAX_PDF_RENDER_BYTES,
    MAX_PDF_RENDER_DIMENSION_PIXELS,
    MAX_PDF_RENDER_PAGES,
    MAX_PDF_RENDER_PIXELS,
    MAX_PDF_RENDER_WALL_TIME_MS,
    PDF_RENDER_RGBA8_BYTES_PER_PIXEL,
    PdfPageRenderLimits,
)
from paperworkshell.domain.searchable_pdf_export import (
    SearchablePdfExportError,
    SearchablePdfExportErrorCode,
    SearchablePdfExportPlan,
    SearchablePdfSourceMediaType,
    reject_searchable_pdf_export,
)
from paperworkshell.ocr.ocrmypdf_adapter import OcrMyPdfAdapter, loaded_ocrmypdf_version
from paperworkshell.ocr.tesseract_runner import TesseractCommandRunner, TesseractRunnerError
from paperworkshell.pdf.export_adapter import loaded_pikepdf_engine_versions
from paperworkshell.pdf.qpdf_checker import QpdfCommandChecker
from paperworkshell.pdf.raster_pdf_page import RasterPdfPageLimits
from paperworkshell.pdf.searchable_export_adapter import (
    SearchablePdfAdapterProgressPhase,
    SearchablePdfAdapterProgressSink,
    SearchablePdfExportAdapter,
    SearchablePdfExportAdapterControl,
    SearchablePdfExportAdapterLimits,
)
from paperworkshell.pdf.text_presence import PdfTextPresenceLimits
from paperworkshell.worker.operations import (
    OperationContext,
    SearchablePdfExportOperationHandler,
    SearchablePdfExportOperationRequest,
)
from paperworkshell.worker.pdf_export_buffers import (
    PdfExportSectionWriteResult,
    read_pdf_export_plan_section,
    write_pdf_export_output_section,
)
from paperworkshell.worker.pdf_export_operation import (
    _absolute_deadline,
    _close_native_handle_no_raise,
    _ContextMonotonic,
    _materialize_worker_private_temp,
    _require_active,
    _stage_source_handle,
    _worker_private_operation_temp,
    _write_result_pipe,
)
from paperworkshell.worker.pdf_export_plan import PdfExportWorkerSource
from paperworkshell.worker.pdf_export_report import (
    PIKEPDF_EXPORT_ENGINE_VERSION,
    QPDF_EXPORT_ENGINE_VERSION,
    PdfExportWorkerReport,
)
from paperworkshell.worker.private_temp import (
    OsWorkerPrivateTempStorage,
    WorkerPrivateTempStorage,
    WorkerPrivateTempStorageError,
)
from paperworkshell.worker.protocol.models import (
    SEARCHABLE_PDF_EXPORT_OUTPUT_CONTENT_KIND,
    SEARCHABLE_PDF_EXPORT_PLAN_CONTENT_KIND,
    SEARCHABLE_PDF_EXPORT_STATUS_CONTENT_KIND,
    CaseObjectReference,
    OperationProgressPayload,
    ProgressStage,
    ProgressUnit,
    ReferenceArea,
)
from paperworkshell.worker.searchable_pdf_export_plan import (
    SearchablePdfWorkerPlan,
    decode_searchable_pdf_worker_plan,
)
from paperworkshell.worker.searchable_pdf_export_report import (
    SearchablePdfWorkerReport,
    encode_searchable_pdf_worker_report,
)
from paperworkshell.worker.searchable_pdf_export_status import (
    SearchablePdfPageStatusDocument,
    encode_searchable_pdf_page_status,
)

PWS_TESSERACT_EXECUTABLE_ENVIRONMENT: Final = "PWS_TESSERACT_EXECUTABLE"
PWS_TESSDATA_ROOT_ENVIRONMENT: Final = "PWS_TESSDATA_ROOT"
PWS_QPDF_EXECUTABLE_ENVIRONMENT: Final = "PWS_QPDF_EXECUTABLE"
_COPY_CHUNK_BYTES: Final = 1024 * 1024
_VERIFICATION_DIMENSION_PIXELS: Final = 1024
_VERIFICATION_PIXELS: Final = _VERIFICATION_DIMENSION_PIXELS**2
_MINIMUM_OBJECT_LIMIT: Final = 4_096
_OBJECTS_PER_PAGE_LIMIT: Final = 1_024
_MAX_TEXT_CHARACTERS_PER_PAGE: Final = 10_000_000
_ENGINE_ADMISSION_SECONDS: Final = 5.0

_OCR_EXPORT_ERROR_CODES: Final[dict[OcrErrorCode, PdfExportErrorCode]] = {
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

_IMAGE_EXPORT_ERROR_CODES: Final[dict[ImagePageErrorCode, PdfExportErrorCode]] = {
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


@dataclass(slots=True, repr=False)
class _OwnedAuthorities:
    handles: set[int]

    @classmethod
    def initial(cls, request: SearchablePdfExportOperationRequest) -> _OwnedAuthorities:
        return cls(
            {
                request.plan_handle,
                request.output_handle,
                request.status_handle,
                request.result_handle,
            }
        )

    def add(self, values: tuple[int, ...]) -> None:
        self.handles.update(values)

    def transfer(self, value: int) -> int:
        if value not in self.handles:
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.INTERNAL_ERROR,
                "searchable.handle_ownership",
            )
        self.handles.remove(value)
        return value

    def close_all(self) -> None:
        selected = tuple(self.handles)
        self.handles.clear()
        for value in selected:
            _close_native_handle_no_raise(value)


@dataclass(frozen=True, slots=True, repr=False)
class WindowsSearchablePdfExportOperationAdapter:
    """Stage immutable sources and publish correlated PDF/status sections."""

    candidate_adapter: SearchablePdfExportAdapter = field(repr=False)
    private_temp_storage: WorkerPrivateTempStorage = field(
        default_factory=OsWorkerPrivateTempStorage,
        repr=False,
    )

    def __post_init__(self) -> None:
        if (
            type(self.candidate_adapter) is not SearchablePdfExportAdapter
            or not callable(getattr(self.private_temp_storage, "require_capacity", None))
            or not callable(getattr(self.private_temp_storage, "open_named", None))
        ):
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.MODEL_INVALID,
                "searchable.worker_adapter",
            )

    def discard_handles(
        self,
        context: OperationContext,
        request: SearchablePdfExportOperationRequest,
    ) -> None:
        del context
        _OwnedAuthorities.initial(request).close_all()

    def export(
        self,
        context: OperationContext,
        request: SearchablePdfExportOperationRequest,
    ) -> tuple[CaseObjectReference, CaseObjectReference]:
        authorities = _OwnedAuthorities.initial(request)
        try:
            if os.name != "nt":
                reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.platform")
            plan_reference = _plan_reference(context)
            absolute_deadline = _absolute_deadline(context)
            _require_active(context, absolute_deadline=absolute_deadline)
            plan_payload = read_pdf_export_plan_section(
                authorities.transfer(request.plan_handle),
                byte_count=plan_reference.expected_byte_size or 0,
                expected_sha256=plan_reference.expected_sha256 or "",
            )
            worker_plan = decode_searchable_pdf_worker_plan(plan_payload)
            context.report_progress(_processing_progress(0, worker_plan.plan.page_count))
            source_handles = tuple(source.worker_handle for source in worker_plan.sources)
            if set(source_handles) & {
                request.plan_handle,
                request.output_handle,
                request.status_handle,
                request.result_handle,
            }:
                reject_searchable_pdf_export(
                    SearchablePdfExportErrorCode.RESULT_INVALID,
                    "searchable.handle_collision",
                )
            authorities.add(source_handles)
            references = _bind_plan(context, worker_plan)
            limits = _adapter_limits(context, worker_plan.plan, request.status_capacity_bytes)
            control = SearchablePdfExportAdapterControl(
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
                reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.temp_capacity")
            with _worker_private_operation_temp(temp_root) as operation_temp:
                staged = _stage_sources(
                    context,
                    worker_plan,
                    references,
                    authorities,
                    operation_temp=operation_temp,
                    absolute_deadline=absolute_deadline,
                    private_temp_storage=self.private_temp_storage,
                )
                candidate_path = operation_temp / f"{uuid4().hex}.pdf"
                reported_pages = 0

                def report_adapter_progress(
                    _phase: SearchablePdfAdapterProgressPhase,
                    completed: int,
                    _total: int,
                ) -> None:
                    nonlocal reported_pages
                    if not 0 <= completed <= worker_plan.plan.page_count:
                        reject_searchable_pdf_export(
                            SearchablePdfExportErrorCode.RESULT_INVALID,
                            "searchable.progress",
                        )
                    if completed > reported_pages:
                        context.report_progress(
                            _processing_progress(completed, worker_plan.plan.page_count)
                        )
                        reported_pages = completed

                report = self.candidate_adapter.assemble_and_verify(
                    worker_plan.plan,
                    staged,
                    candidate_path,
                    operation_temp,
                    languages=("eng",),
                    limits=limits,
                    control=control,
                    progress=cast(SearchablePdfAdapterProgressSink, report_adapter_progress),
                )
                if reported_pages < worker_plan.plan.page_count:
                    context.report_progress(
                        _processing_progress(
                            worker_plan.plan.page_count,
                            worker_plan.plan.page_count,
                        )
                    )
                _require_active(context, absolute_deadline=absolute_deadline)
                output_result = _copy_file_section(
                    authorities,
                    request.output_handle,
                    candidate_path,
                    byte_count=report.base_report.output_byte_size,
                    expected_sha256=report.base_report.output_sha256,
                    capacity=context.limits.output_bytes,
                    checkpoint=lambda: _require_active(
                        context,
                        absolute_deadline=absolute_deadline,
                    ),
                )
                status_document = SearchablePdfPageStatusDocument(
                    operation_id=context.operation_id,
                    status_object_id=request.status_object_id,
                    semantic_fingerprint=worker_plan.plan.semantic_fingerprint,
                    page_outcomes=report.page_outcomes,
                )
                status_payload = encode_searchable_pdf_page_status(status_document)
                if len(status_payload) > request.status_capacity_bytes:
                    reject_searchable_pdf_export(
                        SearchablePdfExportErrorCode.LIMIT_EXCEEDED,
                        "searchable.status_capacity",
                    )
                status_result = _copy_status_section(
                    authorities,
                    request.status_handle,
                    status_payload,
                    byte_count=len(status_payload),
                    capacity=request.status_capacity_bytes,
                    checkpoint=lambda: _require_active(
                        context,
                        absolute_deadline=absolute_deadline,
                    ),
                )
                worker_report = SearchablePdfWorkerReport(
                    operation_id=context.operation_id,
                    output_object_id=request.output_object_id,
                    status_object_id=request.status_object_id,
                    status_byte_size=status_result.byte_size,
                    status_sha256=status_result.sha256,
                    base_report=PdfExportWorkerReport(
                        operation_id=context.operation_id,
                        output_object_id=request.output_object_id,
                        report=report.base_report,
                    ),
                )
                _write_worker_report(
                    authorities,
                    request.result_handle,
                    worker_report,
                )
                _require_active(context, absolute_deadline=absolute_deadline)
                return (
                    _reference(
                        context,
                        request.output_object_id,
                        SEARCHABLE_PDF_EXPORT_OUTPUT_CONTENT_KIND,
                        output_result,
                    ),
                    _reference(
                        context,
                        request.status_object_id,
                        SEARCHABLE_PDF_EXPORT_STATUS_CONTENT_KIND,
                        status_result,
                    ),
                )
        except PdfExportError, SearchablePdfExportError:
            raise
        except OcrError as error:
            _map_nested_processing_error(error)
        except ImagePageError as error:
            _map_nested_processing_error(error)
        except MemoryError, RecursionError:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.worker_resources")
        except OSError:
            reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.worker_io")
        except Exception:
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.INTERNAL_ERROR,
                "searchable.worker_adapter",
            )
        finally:
            authorities.close_all()


def _map_nested_processing_error(error: OcrError | ImagePageError) -> Never:
    """Map nested domain failures without retaining their context or cause."""

    if isinstance(error, OcrError):
        code = _OCR_EXPORT_ERROR_CODES.get(error.code, PdfExportErrorCode.INTERNAL_ERROR)
        context = "export.searchable_ocr"
    else:
        code = _IMAGE_EXPORT_ERROR_CODES.get(error.code, PdfExportErrorCode.INTERNAL_ERROR)
        context = "export.searchable_image"
    reject_pdf_export(code, context)


def _processing_progress(completed: int, total: int) -> OperationProgressPayload:
    return OperationProgressPayload(
        ProgressStage.PROCESSING,
        completed,
        total,
        ProgressUnit.PAGES,
    )


def _plan_reference(context: OperationContext) -> CaseObjectReference:
    if len(context.inputs) < 2:
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.RESULT_INVALID,
            "searchable.plan_reference",
        )
    reference = context.inputs[0]
    if (
        reference.area is not ReferenceArea.SOURCE_READ
        or reference.operation_id is not None
        or reference.content_kind != SEARCHABLE_PDF_EXPORT_PLAN_CONTENT_KIND
        or reference.expected_byte_size is None
        or reference.expected_sha256 is None
    ):
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.RESULT_INVALID,
            "searchable.plan_reference",
        )
    return reference


def _bind_plan(
    context: OperationContext,
    worker_plan: SearchablePdfWorkerPlan,
) -> dict[UUID, CaseObjectReference]:
    plan = worker_plan.plan
    if plan.case_id != context.case_id or plan.page_count > context.limits.page_count:
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.RESULT_INVALID,
            "searchable.plan_binding",
        )
    references: dict[UUID, CaseObjectReference] = {}
    content_by_media = {
        SearchablePdfSourceMediaType.PDF: "pdf",
        SearchablePdfSourceMediaType.JPEG: "jpeg",
        SearchablePdfSourceMediaType.PNG: "png",
    }
    for reference in context.inputs[1:]:
        if reference.object_id in references:
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.RESULT_INVALID,
                "searchable.source_reference",
            )
        references[reference.object_id] = reference
    if set(references) != {source.source.source_id for source in worker_plan.sources}:
        reject_searchable_pdf_export(
            SearchablePdfExportErrorCode.SOURCE_INCONSISTENT,
            "searchable.source_references",
        )
    for worker_source in worker_plan.sources:
        source = worker_source.source
        reference = references[source.source_id]
        if (
            reference.area is not ReferenceArea.SOURCE_READ
            or reference.operation_id is not None
            or reference.content_kind != content_by_media[source.media_type]
            or reference.expected_byte_size != source.byte_size
            or reference.expected_sha256 != source.sha256
        ):
            reject_searchable_pdf_export(
                SearchablePdfExportErrorCode.SOURCE_INCONSISTENT,
                "searchable.source_facts",
            )
    return references


def _stage_sources(
    context: OperationContext,
    worker_plan: SearchablePdfWorkerPlan,
    references: Mapping[UUID, CaseObjectReference],
    authorities: _OwnedAuthorities,
    *,
    operation_temp: Path,
    absolute_deadline: float,
    private_temp_storage: WorkerPrivateTempStorage,
) -> dict[UUID, Path]:
    result: dict[UUID, Path] = {}
    for source in worker_plan.sources:
        _require_active(context, absolute_deadline=absolute_deadline)
        # The established staging helper authenticates bytes and native identity;
        # its temporary suffix is non-authoritative and adapters classify by plan.
        result[source.source.source_id] = _stage_source_handle(
            context,
            cast(PdfExportWorkerSource, source),
            references[source.source.source_id],
            authorities.transfer(source.worker_handle),
            private_temp_storage=private_temp_storage,
            operation_temp=operation_temp,
            absolute_deadline=absolute_deadline,
        )
    return result


def _copy_file_section(
    authorities: _OwnedAuthorities,
    handle: int,
    path: Path,
    *,
    byte_count: int,
    expected_sha256: str,
    capacity: int,
    checkpoint: Callable[[], object],
) -> PdfExportSectionWriteResult:
    try:
        with path.open("rb", buffering=0) as source:
            return write_pdf_export_output_section(
                authorities.transfer(handle),
                source,
                byte_count=byte_count,
                expected_sha256=expected_sha256,
                capacity=capacity,
                checkpoint=checkpoint,
            )
    except PdfExportError:
        raise
    except OSError:
        reject_pdf_export(PdfExportErrorCode.RESULT_INVALID, "export.output_read")


def _copy_status_section(
    authorities: _OwnedAuthorities,
    handle: int,
    payload: bytes,
    *,
    byte_count: int,
    capacity: int,
    checkpoint: Callable[[], object],
) -> PdfExportSectionWriteResult:
    source = io.BytesIO(payload)
    expected_sha256 = hashlib.sha256(payload).hexdigest()
    return write_pdf_export_output_section(
        authorities.transfer(handle),
        source,
        byte_count=byte_count,
        expected_sha256=expected_sha256,
        capacity=capacity,
        checkpoint=checkpoint,
    )


def _write_worker_report(
    authorities: _OwnedAuthorities,
    handle: int,
    report: SearchablePdfWorkerReport,
) -> None:
    payload = encode_searchable_pdf_worker_report(report)
    _write_result_pipe(authorities.transfer(handle), payload)


def _reference(
    context: OperationContext,
    object_id: UUID,
    content_kind: str,
    result: PdfExportSectionWriteResult,
) -> CaseObjectReference:
    return CaseObjectReference(
        area=ReferenceArea.OPERATION_STAGING,
        object_id=object_id,
        operation_id=context.operation_id,
        content_kind=content_kind,
        expected_byte_size=result.byte_size,
        expected_sha256=result.sha256,
    )


def _adapter_limits(
    context: OperationContext,
    plan: SearchablePdfExportPlan,
    status_capacity: int,
) -> SearchablePdfExportAdapterLimits:
    values = context.limits
    if values.output_bytes < MAX_PDF_INSPECTION_REPORT_BYTES:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.worker_capacity")
    source_total = sum(source.byte_size for source in plan.sources)
    # Staged sources + candidate + mapped candidate + status stay live while the
    # remaining budget is the sole sequential OCR/page scratch allowance.
    fixed = source_total + (2 * values.output_bytes) + status_capacity
    if fixed >= values.temporary_storage_bytes:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.worker_capacity")
    scratch = values.temporary_storage_bytes - fixed
    pages = min(values.page_count, MAX_PDF_RENDER_PAGES)
    memory = min(values.memory_bytes, MAX_PDF_RENDER_BYTES, MAX_IMAGE_MEMORY_BYTES)
    source_decode_bytes = min(memory, MAX_IMAGE_DECOMPRESSED_BYTES)
    source_decode_pixels = min(
        values.pixel_count,
        MAX_IMAGE_SOURCE_PIXELS,
        source_decode_bytes // IMAGE_RGBA8_BYTES_PER_PIXEL,
    )
    verification_bytes = _VERIFICATION_PIXELS * PDF_RENDER_RGBA8_BYTES_PER_PIXEL
    # Eager authenticated deletion limits pre-OCR page scratch to two pairwise
    # peaks: source + transformed RGBA, then transformed RGBA + one-page PDF.
    # Reserve a useful PDF share before deriving the common RGBA ceiling.
    page_pdf_seed = min(values.output_bytes, scratch // 2)
    page_rgba_bytes = min(
        MAX_IMAGE_OUTPUT_BYTES,
        MAX_PDF_RENDER_BYTES,
        memory,
        source_decode_pixels * IMAGE_RGBA8_BYTES_PER_PIXEL,
        scratch // 2,
        scratch - page_pdf_seed,
    )
    page_rgba_pixels = min(
        values.pixel_count,
        MAX_PDF_RENDER_PIXELS,
        MAX_IMAGE_OUTPUT_PIXELS,
        page_rgba_bytes // IMAGE_RGBA8_BYTES_PER_PIXEL,
    )
    page_rgba_bytes = page_rgba_pixels * IMAGE_RGBA8_BYTES_PER_PIXEL
    page_pdf_bytes = min(values.output_bytes, scratch - page_rgba_bytes)
    if (
        pages < plan.page_count
        or source_decode_pixels < 1
        or page_rgba_pixels < 1
        or page_pdf_bytes < 1
        or memory < verification_bytes
    ):
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.worker_capacity")
    objects = min(
        MAX_PDF_INSPECTION_OBJECTS,
        max(_MINIMUM_OBJECT_LIMIT, pages * _OBJECTS_PER_PAGE_LIMIT),
    )
    transform_dimension = min(
        MAX_IMAGE_DIMENSION_PIXELS,
        MAX_PDF_RENDER_DIMENSION_PIXELS,
        int(math.isqrt(page_rgba_pixels)),
    )
    render_dimension = max(transform_dimension, _VERIFICATION_DIMENSION_PIXELS)
    render_pixels = max(page_rgba_pixels, _VERIFICATION_PIXELS)
    render_bytes = max(page_rgba_bytes, verification_bytes)
    wall_time = min(values.wall_time_ms, MAX_IMAGE_WALL_TIME_MS)
    maximum_image_source_bytes = max(
        (
            source.byte_size
            for source in plan.sources
            if source.media_type
            in {SearchablePdfSourceMediaType.JPEG, SearchablePdfSourceMediaType.PNG}
        ),
        default=1,
    )
    return SearchablePdfExportAdapterLimits(
        inspection=PdfInspectionLimits(
            maximum_pages=pages,
            maximum_objects=objects,
            maximum_input_bytes=values.temporary_storage_bytes,
            maximum_temporary_bytes=scratch,
            maximum_output_bytes=values.output_bytes,
            maximum_tool_output_bytes=min(values.output_bytes, 1024 * 1024),
        ),
        rendering=PdfPageRenderLimits(
            maximum_pages=pages,
            maximum_width_pixels=render_dimension,
            maximum_height_pixels=render_dimension,
            maximum_pixels=render_pixels,
            maximum_bitmap_bytes=render_bytes,
            maximum_destination_bytes=render_bytes,
            maximum_memory_bytes=memory,
            wall_time_ms=min(values.wall_time_ms, MAX_PDF_RENDER_WALL_TIME_MS),
        ),
        text_presence=PdfTextPresenceLimits(
            maximum_pages=pages,
            maximum_source_bytes=max(source_total, values.output_bytes),
            maximum_characters_per_page=_MAX_TEXT_CHARACTERS_PER_PAGE,
        ),
        image_normalization=ImageNormalizationLimits(
            maximum_source_bytes=min(MAX_IMAGE_SOURCE_BYTES, maximum_image_source_bytes),
            maximum_encoded_width_pixels=MAX_IMAGE_DIMENSION_PIXELS,
            maximum_encoded_height_pixels=MAX_IMAGE_DIMENSION_PIXELS,
            maximum_source_pixels=source_decode_pixels,
            maximum_decompressed_bytes=source_decode_bytes,
            maximum_metadata_records=MAX_IMAGE_METADATA_RECORDS,
            maximum_metadata_bytes=min(MAX_IMAGE_METADATA_BYTES, memory),
            maximum_output_width_pixels=transform_dimension,
            maximum_output_height_pixels=transform_dimension,
            maximum_output_pixels=page_rgba_pixels,
            maximum_output_bytes=page_rgba_bytes,
            maximum_memory_bytes=memory,
            wall_time_ms=wall_time,
        ),
        image_transform=ImageTransformLimits(
            maximum_source_width_pixels=transform_dimension,
            maximum_source_height_pixels=transform_dimension,
            maximum_source_pixels=page_rgba_pixels,
            maximum_intermediate_pixels=page_rgba_pixels,
            maximum_output_width_pixels=transform_dimension,
            maximum_output_height_pixels=transform_dimension,
            maximum_output_pixels=page_rgba_pixels,
            maximum_output_bytes=page_rgba_bytes,
            maximum_memory_bytes=memory,
            maximum_temporary_storage_bytes=min(
                MAX_IMAGE_TRANSFORM_TEMPORARY_BYTES,
                2 * page_rgba_bytes,
            ),
            wall_time_ms=wall_time,
        ),
        raster_pdf=RasterPdfPageLimits(
            maximum_width_pixels=transform_dimension,
            maximum_height_pixels=transform_dimension,
            maximum_pixels=page_rgba_pixels,
            maximum_input_bytes=page_rgba_bytes,
            maximum_output_bytes=page_pdf_bytes,
            maximum_memory_bytes=memory,
            maximum_pdf_objects=objects,
        ),
        maximum_total_source_bytes=source_total,
        maximum_page_scratch_bytes=scratch,
        maximum_ocr_output_bytes=min(values.output_bytes, scratch),
        maximum_ocr_image_pixels=page_rgba_pixels,
        maximum_ocr_temporary_storage_bytes=values.temporary_storage_bytes,
        pdf_transform_width_pixels=transform_dimension,
        pdf_transform_height_pixels=transform_dimension,
        verification_width_pixels=_VERIFICATION_DIMENSION_PIXELS,
        verification_height_pixels=_VERIFICATION_DIMENSION_PIXELS,
        maximum_active_full_resolution_page_buffers=1,
    )


@dataclass(frozen=True, slots=True)
class _AdmissionControl:
    deadline: float

    def cancellation_requested(self) -> bool:
        return False

    def deadline_expired(self) -> bool:
        return time.monotonic() >= self.deadline


def build_searchable_pdf_export_operation_handler() -> SearchablePdfExportOperationHandler | None:
    """Advertise only when exact qpdf/OCR resources and engines are admitted."""

    executable = os.environ.get(PWS_TESSERACT_EXECUTABLE_ENVIRONMENT)
    tessdata = os.environ.get(PWS_TESSDATA_ROOT_ENVIRONMENT)
    qpdf = os.environ.get(PWS_QPDF_EXECUTABLE_ENVIRONMENT)
    if (
        not executable
        or not tessdata
        or not qpdf
        or "\x00" in executable
        or "\x00" in tessdata
        or "\x00" in qpdf
    ):
        return None
    executable_path = Path(executable)
    tessdata_path = Path(tessdata)
    qpdf_path = Path(qpdf)
    try:
        if (
            os.name != "nt"
            or not executable_path.is_absolute()
            or not tessdata_path.is_absolute()
            or not executable_path.is_file()
            or not tessdata_path.is_dir()
            or not qpdf_path.is_absolute()
            or not qpdf_path.is_file()
            or distribution_version("ocrmypdf") != TRUSTED_OCRMYPDF_VERSION
            or loaded_ocrmypdf_version() != TRUSTED_OCRMYPDF_VERSION
            or loaded_pikepdf_engine_versions()
            != (PIKEPDF_EXPORT_ENGINE_VERSION, QPDF_EXPORT_ENGINE_VERSION)
        ):
            return None
        admission_deadline = time.monotonic() + _ENGINE_ADMISSION_SECONDS
        qpdf_checker = QpdfCommandChecker(executable=qpdf_path)
        qpdf_checker.require_trusted_version(deadline=admission_deadline)
        temporary = _materialize_worker_private_temp()
        runner = TesseractCommandRunner(
            executable=executable_path,
            tessdata_directory=tessdata_path,
            temporary_directory=temporary,
            control=_AdmissionControl(admission_deadline),
        )
        if runner.version() != TRUSTED_TESSERACT_VERSION or "eng" not in runner.languages():
            return None
    except (
        OcrError,
        PdfInspectionError,
        TesseractRunnerError,
        ImportError,
        PackageNotFoundError,
        OSError,
        ValueError,
    ):
        return None
    adapter = SearchablePdfExportAdapter(
        ocr_processor=OcrMyPdfAdapter(
            tesseract_executable=executable_path,
            tessdata_directory=tessdata_path,
        ),
        qpdf_checker=qpdf_checker,
    )
    return SearchablePdfExportOperationHandler(
        WindowsSearchablePdfExportOperationAdapter(candidate_adapter=adapter)
    )

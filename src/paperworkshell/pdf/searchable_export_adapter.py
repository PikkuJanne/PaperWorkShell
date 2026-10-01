"""Sequential mixed-source searchable-PDF assembly and closed verification."""

from __future__ import annotations

import hashlib
import math
import os
import re
import shutil
import stat
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO, Final, Protocol, cast
from uuid import UUID, uuid4

import pikepdf

from paperworkshell.domain.image_pages import (
    IMAGE_RGBA8_BYTES_PER_PIXEL,
    ImageNormalizationLimits,
    ImageNormalizationRequest,
    ImageNormalizationResult,
    ImagePageError,
    ImageQuarterTurn,
    ImageRgba8Layout,
    ImageSourceFormat,
)
from paperworkshell.domain.image_transforms import (
    ImageNormalizedCrop,
    ImageTransformIntent,
    ImageTransformLimits,
    ImageTransformResult,
)
from paperworkshell.domain.incremental_processing import (
    IncrementalPageScheduler,
    IncrementalProcessingError,
    IncrementalProcessingErrorCode,
    IncrementalProcessingLimits,
    IncrementalProcessingObserver,
    IncrementalProcessingSnapshot,
)
from paperworkshell.domain.ocr import (
    OcrAdapterProgressSink,
    OcrAdapterResult,
    OcrError,
    OcrErrorCode,
)
from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    reject_pdf_export,
)
from paperworkshell.domain.pdf_geometry import (
    MAX_PDF_PAGE_COORDINATE_MAGNITUDE_UNITS,
    MAX_PDF_PAGE_SPAN_UNITS,
    PdfPageGeometryLimits,
    PdfResolvedPageGeometry,
)
from paperworkshell.domain.pdf_inspection import (
    PdfInspectionError,
    PdfInspectionErrorCode,
    PdfInspectionLimits,
    PdfStructuralState,
)
from paperworkshell.domain.pdf_rendering import (
    PdfPageBox,
    PdfPageRenderLimits,
    PdfPageRenderRequest,
    PdfPageRenderResult,
    PdfRenderError,
    PdfRenderErrorCode,
    PdfRenderRotation,
)
from paperworkshell.domain.searchable_pdf_export import (
    SearchablePdfExportPlan,
    SearchablePdfPage,
    SearchablePdfPageEligibility,
    SearchablePdfPageEligibilityCode,
    SearchablePdfPageFailureCode,
    SearchablePdfPageOutcome,
    SearchablePdfPageOutcomeCode,
    SearchablePdfPageSearchability,
    SearchablePdfSource,
    SearchablePdfSourceMediaType,
    SearchablePdfSourceSignatureState,
    SearchablePdfVerificationReport,
)
from paperworkshell.imaging.pillow_decoder import PillowImageDecoder
from paperworkshell.imaging.pillow_transformer import PillowImageTransformer
from paperworkshell.pdf.page_geometry import PikePdfPageGeometryResolver
from paperworkshell.pdf.pdfium_renderer import PdfiumPageRenderer
from paperworkshell.pdf.portal_policy import (
    PORTAL_MINIMUM_OUTPUT_VERSION,
    require_portal_output,
    require_portal_source_version,
)
from paperworkshell.pdf.qpdf_checker import QpdfCommandChecker
from paperworkshell.pdf.raster_pdf_page import (
    RasterPdfPageAdapter,
    RasterPdfPageGeometry,
    RasterPdfPageLimits,
    RasterPdfPageResult,
    _BoundedOutput,
)
from paperworkshell.pdf.renderer import (
    PdfPageRenderer,
    PdfRasterDestination,
)
from paperworkshell.pdf.text_presence import (
    PdfiumTextPresenceInspector,
    PdfTextPresenceFact,
    PdfTextPresenceLimits,
)

_HASH_CHUNK_BYTES: Final = 1024 * 1024
_MAXIMUM_INHERITANCE_DEPTH: Final = 64
_FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x0000_0400
_LANGUAGE_TOKEN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}\Z")

_NAME_CROP_BOX: Final = pikepdf.Name("/CropBox")
_NAME_LAST_MODIFIED: Final = pikepdf.Name("/LastModified")
_NAME_MEDIA_BOX: Final = pikepdf.Name("/MediaBox")
_NAME_METADATA: Final = pikepdf.Name("/Metadata")
_NAME_PARENT: Final = pikepdf.Name("/Parent")
_NAME_PIECE_INFO: Final = pikepdf.Name("/PieceInfo")
_NAME_RESOURCES: Final = pikepdf.Name("/Resources")
_NAME_ROTATE: Final = pikepdf.Name("/Rotate")
_PHYSICAL_GEOMETRY_TOLERANCE_POINTS: Final = 0.001

CancellationProbe = Callable[[], bool]
MonotonicClock = Callable[[], float]
PageScratchObserver = Callable[[int], None]


def _never_cancel() -> bool:
    return False


def _discard_page_scratch(_bytes_used: int) -> None:
    return


def _discard_resource_observation(_snapshot: IncrementalProcessingSnapshot) -> None:
    return


class SearchablePdfAdapterProgressPhase(StrEnum):
    """Content-free phases emitted by the worker-neutral adapter."""

    ASSEMBLING = "assembling"
    VERIFYING = "verifying"
    COMPLETE = "complete"


class SearchablePdfAdapterProgressSink(Protocol):
    def __call__(
        self,
        phase: SearchablePdfAdapterProgressPhase,
        completed_pages: int,
        total_pages: int,
    ) -> None: ...


def _discard_progress(
    _phase: SearchablePdfAdapterProgressPhase,
    _completed_pages: int,
    _total_pages: int,
) -> None:
    return None


_DISCARD_PROGRESS: Final = cast(SearchablePdfAdapterProgressSink, _discard_progress)


class SearchablePdfStructureChecker(Protocol):
    def check(
        self,
        source_path: Path,
        *,
        limits: PdfInspectionLimits,
        deadline: float,
        cancellation_probe: CancellationProbe,
        monotonic: MonotonicClock,
    ) -> PdfStructuralState: ...


class SearchablePdfTextInspector(Protocol):
    def inspect(
        self,
        source_path: Path,
        page_indices: tuple[int, ...],
        limits: PdfTextPresenceLimits,
        control: SearchablePdfExportAdapterControl,
    ) -> tuple[PdfTextPresenceFact, ...]: ...


class SearchablePdfOcrProcessor(Protocol):
    """Exact subset implemented by ``OcrMyPdfAdapter``."""

    def process(
        self,
        source_path: Path,
        output_path: Path,
        *,
        temporary_directory: Path,
        languages: tuple[str, ...],
        profile_id: str,
        expected_page_count: int,
        maximum_output_bytes: int,
        maximum_image_pixels: int,
        maximum_temporary_storage_bytes: int,
        control: SearchablePdfExportAdapterControl,
        progress: OcrAdapterProgressSink,
    ) -> OcrAdapterResult: ...


class SearchablePdfImageDecoder(Protocol):
    def normalize(
        self,
        source: BinaryIO,
        destination: BinaryIO,
        *,
        expected_format: ImageSourceFormat,
        request: ImageNormalizationRequest,
        limits: ImageNormalizationLimits,
        control: SearchablePdfExportAdapterControl,
    ) -> ImageNormalizationResult: ...


class SearchablePdfImageTransformer(Protocol):
    def transform(
        self,
        source_rgba: BinaryIO,
        destination: BinaryIO,
        *,
        source_layout: ImageRgba8Layout,
        intent: ImageTransformIntent,
        maximum_width_pixels: int,
        maximum_height_pixels: int,
        limits: ImageTransformLimits,
        control: SearchablePdfExportAdapterControl,
    ) -> ImageTransformResult: ...


class SearchablePdfRasterPageBuilder(Protocol):
    def build(
        self,
        rgba_path: Path,
        output_path: Path,
        layout: ImageRgba8Layout,
        limits: RasterPdfPageLimits,
        control: SearchablePdfExportAdapterControl,
        *,
        geometry: RasterPdfPageGeometry | None = None,
    ) -> RasterPdfPageResult: ...


@dataclass(frozen=True, slots=True)
class SearchablePdfExportAdapterLimits:
    """Central-policy-derived limits for the complete one-shot operation."""

    inspection: PdfInspectionLimits
    rendering: PdfPageRenderLimits
    text_presence: PdfTextPresenceLimits
    image_normalization: ImageNormalizationLimits
    image_transform: ImageTransformLimits
    raster_pdf: RasterPdfPageLimits
    maximum_total_source_bytes: int
    maximum_page_scratch_bytes: int
    maximum_ocr_output_bytes: int
    maximum_ocr_image_pixels: int
    maximum_ocr_temporary_storage_bytes: int
    pdf_transform_width_pixels: int
    pdf_transform_height_pixels: int
    verification_width_pixels: int = 1024
    verification_height_pixels: int = 1024
    maximum_active_full_resolution_page_buffers: int = 1

    def __post_init__(self) -> None:
        if (
            type(self.inspection) is not PdfInspectionLimits
            or type(self.rendering) is not PdfPageRenderLimits
            or type(self.text_presence) is not PdfTextPresenceLimits
            or type(self.image_normalization) is not ImageNormalizationLimits
            or type(self.image_transform) is not ImageTransformLimits
            or type(self.raster_pdf) is not RasterPdfPageLimits
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.searchable_limits")
        maximum_source_raster_bytes = max(
            self.image_normalization.maximum_output_bytes,
            self.image_transform.maximum_source_pixels * IMAGE_RGBA8_BYTES_PER_PIXEL,
        )
        maximum_page_scratch_bytes = max(
            maximum_source_raster_bytes + self.image_transform.maximum_output_bytes,
            self.image_transform.maximum_output_bytes + self.raster_pdf.maximum_output_bytes,
        )
        if (
            any(
                type(value) is not int or value < 1
                for value in (
                    self.maximum_total_source_bytes,
                    self.maximum_page_scratch_bytes,
                    self.maximum_ocr_output_bytes,
                    self.maximum_ocr_image_pixels,
                    self.maximum_ocr_temporary_storage_bytes,
                    self.pdf_transform_width_pixels,
                    self.pdf_transform_height_pixels,
                    self.verification_width_pixels,
                    self.verification_height_pixels,
                    self.maximum_active_full_resolution_page_buffers,
                )
            )
            or self.maximum_active_full_resolution_page_buffers != 1
            or self.inspection.maximum_pages > self.rendering.maximum_pages
            or self.inspection.maximum_pages > self.text_presence.maximum_pages
            or self.maximum_total_source_bytes > self.text_presence.maximum_source_bytes
            or self.text_presence.maximum_source_bytes > self.inspection.maximum_input_bytes
            or self.maximum_page_scratch_bytes > self.inspection.maximum_temporary_bytes
            or maximum_page_scratch_bytes > self.maximum_page_scratch_bytes
            or self.maximum_ocr_temporary_storage_bytes > self.inspection.maximum_input_bytes
            or self.maximum_ocr_output_bytes > self.inspection.maximum_output_bytes
            or self.image_normalization.maximum_output_pixels
            > self.image_transform.maximum_source_pixels
            or self.image_normalization.maximum_output_bytes
            > self.image_transform.maximum_source_pixels * IMAGE_RGBA8_BYTES_PER_PIXEL
            or self.image_transform.maximum_output_bytes > self.raster_pdf.maximum_input_bytes
            or self.image_transform.maximum_source_pixels > self.rendering.maximum_pixels
            or self.image_transform.maximum_source_pixels * IMAGE_RGBA8_BYTES_PER_PIXEL
            > self.rendering.maximum_destination_bytes
            or self.pdf_transform_width_pixels > self.rendering.maximum_width_pixels
            or self.pdf_transform_height_pixels > self.rendering.maximum_height_pixels
            or self.pdf_transform_width_pixels > self.image_transform.maximum_source_width_pixels
            or self.pdf_transform_height_pixels > self.image_transform.maximum_source_height_pixels
            or self.verification_width_pixels > self.rendering.maximum_width_pixels
            or self.verification_height_pixels > self.rendering.maximum_height_pixels
            or self.verification_width_pixels * self.verification_height_pixels
            > self.rendering.maximum_pixels
            or self.verification_width_pixels
            * self.verification_height_pixels
            * IMAGE_RGBA8_BYTES_PER_PIXEL
            > self.rendering.maximum_destination_bytes
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.searchable_limits")


@dataclass(frozen=True, slots=True, repr=False)
class SearchablePdfExportAdapterControl:
    """Cooperative worker probes plus the absolute qpdf deadline."""

    deadline: float
    cancellation_probe: CancellationProbe = field(default=_never_cancel, repr=False)
    monotonic: MonotonicClock = field(default=time.monotonic, repr=False)
    page_scratch_observer: PageScratchObserver = field(
        default=_discard_page_scratch,
        repr=False,
    )

    def __post_init__(self) -> None:
        if (
            type(self.deadline) not in (int, float)
            or not math.isfinite(self.deadline)
            or self.deadline <= 0
            or not callable(self.cancellation_probe)
            or not callable(self.monotonic)
            or not callable(self.page_scratch_observer)
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.searchable_control")

    def cancellation_requested(self) -> bool:
        try:
            value = self.cancellation_probe()
        except Exception:
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.cancel_probe")
        if type(value) is not bool:
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.cancel_probe")
        return value

    def deadline_expired(self) -> bool:
        try:
            value = self.monotonic()
        except Exception:
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.clock")
        if type(value) not in (int, float) or not math.isfinite(value):
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.clock")
        return value >= self.deadline


@dataclass(frozen=True, slots=True, repr=False)
class _SourceBinding:
    source: SearchablePdfSource
    path: Path = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class _EffectivePageAttributes:
    media_box: object = field(repr=False)
    crop_box: object = field(repr=False)
    resources: object | None = field(repr=False)
    rotation: int


@dataclass(frozen=True, slots=True)
class _SelectedPage:
    outcome: SearchablePdfPageOutcome
    expected_render: PdfPageRenderResult
    expected_geometry: RasterPdfPageGeometry


@dataclass(slots=True, repr=False)
class _DiscardRaster(PdfRasterDestination):
    maximum_bytes: int
    bytes_written: int = 0

    def write(self, data: object, /) -> int:
        try:
            size = memoryview(data).nbytes  # type: ignore[arg-type]
        except Exception:
            raise OSError from None
        if size < 1 or self.bytes_written + size > self.maximum_bytes:
            raise OSError from None
        self.bytes_written += size
        return size

    def flush(self) -> None:
        return None


@dataclass(frozen=True, slots=True, repr=False)
class SearchablePdfExportAdapter:
    """Create one searchable mixed derivative and verify every limited claim."""

    ocr_processor: SearchablePdfOcrProcessor = field(repr=False)
    qpdf_checker: SearchablePdfStructureChecker = field(
        default_factory=QpdfCommandChecker,
        repr=False,
    )
    renderer: PdfPageRenderer = field(default_factory=PdfiumPageRenderer, repr=False)
    text_inspector: SearchablePdfTextInspector = field(
        default_factory=PdfiumTextPresenceInspector,
        repr=False,
    )
    image_decoder: SearchablePdfImageDecoder = field(
        default_factory=PillowImageDecoder,
        repr=False,
    )
    image_transformer: SearchablePdfImageTransformer = field(
        default_factory=PillowImageTransformer,
        repr=False,
    )
    raster_page_builder: SearchablePdfRasterPageBuilder = field(
        default_factory=RasterPdfPageAdapter,
        repr=False,
    )

    def __post_init__(self) -> None:
        methods = (
            (self.ocr_processor, "process"),
            (self.qpdf_checker, "check"),
            (self.renderer, "render"),
            (self.text_inspector, "inspect"),
            (self.image_decoder, "normalize"),
            (self.image_transformer, "transform"),
            (self.raster_page_builder, "build"),
        )
        if any(not callable(getattr(owner, method, None)) for owner, method in methods):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.searchable_adapter")

    def assemble_and_verify(
        self,
        plan: SearchablePdfExportPlan,
        source_paths: Mapping[UUID, Path],
        output_path: Path,
        temporary_directory: Path,
        *,
        languages: tuple[str, ...],
        limits: SearchablePdfExportAdapterLimits,
        control: SearchablePdfExportAdapterControl,
        progress: SearchablePdfAdapterProgressSink = _DISCARD_PROGRESS,
        resource_observer: IncrementalProcessingObserver = _discard_resource_observation,
    ) -> SearchablePdfVerificationReport:
        """Process pages in plan order and publish only after closed verification."""

        bindings = _validate_call(
            plan,
            source_paths,
            output_path,
            temporary_directory,
            languages,
            limits,
            control,
            progress,
            resource_observer,
        )
        _checkpoint(control)
        _validate_sources(bindings, limits, control, portal=plan.profile.pdf_profile.is_portal)
        binding_by_id = {binding.source.source_id: binding for binding in bindings}
        selected_pages: list[_SelectedPage] = []
        output_saved = False

        try:
            scheduler = IncrementalPageScheduler(
                plan.page_count,
                IncrementalProcessingLimits(
                    maximum_pages=limits.inspection.maximum_pages,
                    maximum_full_resolution_page_buffers=(
                        limits.maximum_active_full_resolution_page_buffers
                    ),
                    maximum_page_results=1,
                    maximum_temporary_storage_bytes=limits.maximum_page_scratch_bytes,
                ),
                observer=resource_observer,
            )
            with pikepdf.Pdf.new() as output:
                for page_index, planned_page in enumerate(plan.pages):
                    _checkpoint(control)
                    with scheduler.begin_page(page_index) as page_lease:
                        page_lease.observe(
                            full_resolution_page_buffers=1,
                            page_results=0,
                            temporary_storage_bytes=0,
                        )

                        def observe_scratch(
                            consumed: int,
                        ) -> None:
                            page_lease.observe(
                                full_resolution_page_buffers=1,
                                page_results=0,
                                temporary_storage_bytes=consumed,
                            )

                        page_control = replace(
                            control,
                            page_scratch_observer=observe_scratch,
                        )
                        binding = binding_by_id[planned_page.source_id]
                        selected = _process_page(
                            self,
                            output,
                            plan,
                            planned_page,
                            binding,
                            temporary_directory,
                            languages,
                            limits,
                            page_control,
                        )
                        page_lease.observe(
                            full_resolution_page_buffers=0,
                            page_results=1,
                            temporary_storage_bytes=0,
                        )
                        selected_pages.append(selected)
                        _emit_progress(
                            progress,
                            SearchablePdfAdapterProgressPhase.ASSEMBLING,
                            len(selected_pages),
                            plan.page_count,
                        )
                        page_lease.observe(
                            full_resolution_page_buffers=0,
                            page_results=0,
                            temporary_storage_bytes=0,
                        )
                scheduler.require_complete()
                if _NAME_METADATA in output.Root:
                    del output.Root[_NAME_METADATA]
                _save_output(
                    output, output_path, limits, control, portal=plan.profile.pdf_profile.is_portal
                )
                output_saved = True

            _emit_progress(
                progress,
                SearchablePdfAdapterProgressPhase.VERIFYING,
                0,
                plan.page_count,
            )
            first_output = _measure_output(output_path, limits, control)
            _verify_structure(output_path, plan, limits, control)
            _verify_qpdf(self.qpdf_checker, output_path, limits, control)
            _verify_final_renders(
                self.renderer,
                output_path,
                tuple((page.expected_render, page.expected_geometry) for page in selected_pages),
                limits,
                control,
                progress,
            )
            outcomes = tuple(page.outcome for page in selected_pages)
            _verify_final_text(
                self.text_inspector,
                output_path,
                outcomes,
                limits,
                control,
            )
            _validate_sources(bindings, limits, control, portal=plan.profile.pdf_profile.is_portal)
            final_output = _measure_output(output_path, limits, control)
            if final_output != first_output:
                reject_pdf_export(
                    PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
                    "export.searchable_changed",
                )
            report = SearchablePdfVerificationReport.for_verified_plan(
                plan,
                output_sha256=final_output[1],
                output_byte_size=final_output[0],
                page_outcomes=outcomes,
            )
            _emit_progress(
                progress,
                SearchablePdfAdapterProgressPhase.COMPLETE,
                plan.page_count,
                plan.page_count,
            )
            return report
        except PdfExportError, OcrError, ImagePageError:
            if output_saved:
                _remove_failed_candidate(output_path)
            raise
        except IncrementalProcessingError as error:
            if output_saved:
                _remove_failed_candidate(output_path)
            code = (
                PdfExportErrorCode.LIMIT_EXCEEDED
                if error.code is IncrementalProcessingErrorCode.RESOURCE_LIMIT
                else PdfExportErrorCode.INTERNAL_ERROR
            )
            reject_pdf_export(code, "export.incremental_processing")
        except MemoryError, RecursionError:
            if output_saved:
                _remove_failed_candidate(output_path)
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.searchable_resources")
        except Exception:
            if output_saved:
                _remove_failed_candidate(output_path)
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.searchable_adapter")


def _validate_call(
    plan: SearchablePdfExportPlan,
    source_paths: Mapping[UUID, Path],
    output_path: Path,
    temporary_directory: Path,
    languages: tuple[str, ...],
    limits: SearchablePdfExportAdapterLimits,
    control: SearchablePdfExportAdapterControl,
    progress: SearchablePdfAdapterProgressSink,
    resource_observer: IncrementalProcessingObserver,
) -> tuple[_SourceBinding, ...]:
    if (
        type(plan) is not SearchablePdfExportPlan
        or not isinstance(source_paths, Mapping)
        or not isinstance(output_path, Path)
        or not output_path.is_absolute()
        or not isinstance(temporary_directory, Path)
        or not temporary_directory.is_absolute()
        or type(languages) is not tuple
        or not languages
        or len(set(languages)) != len(languages)
        or any(
            type(language) is not str or _LANGUAGE_TOKEN.fullmatch(language) is None
            for language in languages
        )
        or type(limits) is not SearchablePdfExportAdapterLimits
        or type(control) is not SearchablePdfExportAdapterControl
        or not callable(progress)
        or not callable(resource_observer)
        or plan.page_count > limits.inspection.maximum_pages
    ):
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.searchable_request")
    source_ids = tuple(source.source_id for source in plan.sources)
    try:
        path_ids = tuple(source_paths.keys())
    except Exception:
        reject_pdf_export(PdfExportErrorCode.SOURCE_INCONSISTENT, "export.searchable_sources")
    if len(path_ids) != len(source_ids) or set(path_ids) != set(source_ids):
        reject_pdf_export(PdfExportErrorCode.SOURCE_INCONSISTENT, "export.searchable_sources")
    try:
        temp_info = os.lstat(temporary_directory)
        output_parent = os.lstat(output_path.parent)
    except OSError:
        reject_pdf_export(PdfExportErrorCode.DESTINATION_INVALID, "export.searchable_paths")
    if (
        not stat.S_ISDIR(temp_info.st_mode)
        or not stat.S_ISDIR(output_parent.st_mode)
        or _is_reparse(temp_info)
        or _is_reparse(output_parent)
        or output_path.exists()
    ):
        code = (
            PdfExportErrorCode.DESTINATION_EXISTS
            if output_path.exists()
            else PdfExportErrorCode.DESTINATION_INVALID
        )
        reject_pdf_export(code, "export.searchable_paths")
    bindings: list[_SourceBinding] = []
    for source in plan.sources:
        try:
            path = source_paths[source.source_id]
        except Exception:
            reject_pdf_export(
                PdfExportErrorCode.SOURCE_INCONSISTENT,
                "export.searchable_sources",
            )
        if (
            not isinstance(path, Path)
            or not path.is_absolute()
            or path == output_path
            or path == temporary_directory
        ):
            reject_pdf_export(
                PdfExportErrorCode.SOURCE_INCONSISTENT,
                "export.searchable_source_path",
            )
        bindings.append(_SourceBinding(source, path))
    return tuple(bindings)


def _validate_sources(
    bindings: tuple[_SourceBinding, ...],
    limits: SearchablePdfExportAdapterLimits,
    control: SearchablePdfExportAdapterControl,
    *,
    portal: bool = False,
) -> None:
    total = 0
    for binding in bindings:
        _checkpoint(control)
        size, digest = _measure_file(
            binding.path,
            maximum_bytes=min(binding.source.byte_size, limits.maximum_total_source_bytes),
            control=control,
            source=True,
        )
        if size != binding.source.byte_size or digest != binding.source.sha256:
            reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.searchable_source")
        total += size
        if total > limits.maximum_total_source_bytes:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.searchable_source_bytes")
        if binding.source.media_type is SearchablePdfSourceMediaType.PDF:
            _strict_validate_pdf_source(binding, limits, control, portal=portal)


def _strict_validate_pdf_source(
    binding: _SourceBinding,
    limits: SearchablePdfExportAdapterLimits,
    control: SearchablePdfExportAdapterControl,
    *,
    portal: bool = False,
) -> None:
    try:
        with binding.path.open("rb") as stream:
            with _strict_open(stream) as document:
                _checkpoint(control)
                if portal:
                    require_portal_source_version(document)
                if document.is_encrypted:
                    reject_pdf_export(PdfExportErrorCode.SOURCE_ENCRYPTED, "export.source")
                if len(document.pages) != binding.source.page_count:
                    reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.source_pages")
                if len(document.objects) > limits.inspection.maximum_objects:
                    reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.source_objects")
    except PdfExportError:
        raise
    except pikepdf.PasswordError:
        reject_pdf_export(PdfExportErrorCode.SOURCE_ENCRYPTED, "export.source")
    except pikepdf.PdfError, RuntimeError, ValueError, OSError:
        reject_pdf_export(PdfExportErrorCode.SOURCE_UNSUPPORTED, "export.source_open")


def _process_page(
    adapter: SearchablePdfExportAdapter,
    output: pikepdf.Pdf,
    plan: SearchablePdfExportPlan,
    page: SearchablePdfPage,
    binding: _SourceBinding,
    temporary_directory: Path,
    languages: tuple[str, ...],
    limits: SearchablePdfExportAdapterLimits,
    control: SearchablePdfExportAdapterControl,
) -> _SelectedPage:
    page_directory = _create_page_directory(temporary_directory)
    result: _SelectedPage | None = None
    operation_error: BaseException | None = None
    try:
        if binding.source.media_type is SearchablePdfSourceMediaType.PDF:
            result = _process_pdf_page(
                adapter,
                output,
                plan,
                page,
                binding,
                page_directory,
                temporary_directory,
                languages,
                limits,
                control,
            )
        else:
            result = _process_image_page(
                adapter,
                output,
                plan,
                page,
                binding,
                page_directory,
                temporary_directory,
                languages,
                limits,
                control,
            )
    except BaseException as error:
        operation_error = error
    cleanup_error = _remove_page_directory(page_directory)
    if cleanup_error:
        reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.page_cleanup")
    if operation_error is not None:
        raise operation_error
    if result is None:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.page_result")
    return result


def _process_pdf_page(
    adapter: SearchablePdfExportAdapter,
    output: pikepdf.Pdf,
    plan: SearchablePdfExportPlan,
    page: SearchablePdfPage,
    binding: _SourceBinding,
    page_directory: Path,
    operation_directory: Path,
    languages: tuple[str, ...],
    limits: SearchablePdfExportAdapterLimits,
    control: SearchablePdfExportAdapterControl,
) -> _SelectedPage:
    signature_blocked = binding.source.signature_state in {
        SearchablePdfSourceSignatureState.MARKERS_DETECTED,
        SearchablePdfSourceSignatureState.UNKNOWN,
    }
    # Repeat the pure-plan invariant immediately before native work. A decoded
    # or future plan can never make a signed/unknown page enter raster/OCR.
    if signature_blocked and page.has_pixel_transform_intent:
        reject_pdf_export(
            PdfExportErrorCode.TRANSFORM_UNSUPPORTED,
            "export.signed_pixel_transform",
        )
    if page.has_pixel_transform_intent:
        base_path = page_directory / "base.pdf"
        _rasterize_transformed_pdf_page(
            adapter,
            page,
            binding,
            page_directory,
            base_path,
            limits,
            control,
        )
        eligibility = SearchablePdfPageEligibility(
            page.output_page_index,
            page.page_id,
            SearchablePdfPageEligibilityCode.OCR_REQUIRED_PIXEL_TRANSFORM,
        )
        return _ocr_select_and_append(
            adapter,
            output,
            plan,
            page,
            eligibility,
            base_path,
            page_directory,
            operation_directory,
            languages,
            limits,
            control,
        )

    source_fact = _inspect_one(
        adapter.text_inspector,
        binding.path,
        page.source_page_index,
        limits,
        control,
    )
    if source_fact.has_extractable_text:
        eligibility = SearchablePdfPageEligibility(
            page.output_page_index,
            page.page_id,
            SearchablePdfPageEligibilityCode.PRESERVE_EXTRACTABLE_TEXT,
        )
        expected = _render_one(
            adapter.renderer,
            binding.path,
            page.source_page_index,
            _page_rotation(page),
            limits,
            control,
        )
        _append_pdf_page(
            output,
            binding.path,
            page.source_page_index,
            page.quarter_turns_clockwise * 90,
            control,
        )
        return _SelectedPage(
            SearchablePdfPageOutcome(
                eligibility,
                SearchablePdfPageOutcomeCode.EXISTING_TEXT_PRESERVED,
                SearchablePdfPageSearchability.EXTRACTABLE_TEXT_PRESENT_EXISTING,
            ),
            expected,
            _read_physical_geometry(
                binding.path,
                page.source_page_index,
                _page_rotation(page),
                limits,
                control,
            ),
        )

    base_path = page_directory / "base.pdf"
    _write_one_pdf_page(
        binding.path,
        page.source_page_index,
        page.quarter_turns_clockwise * 90,
        base_path,
        limits,
        control,
    )
    base_byte_size = _measure_file(
        base_path,
        maximum_bytes=limits.maximum_ocr_output_bytes,
        control=control,
        source=False,
    )[0]
    _require_page_scratch(
        page_directory,
        ((base_path, base_byte_size),),
        limits,
        control,
    )
    if signature_blocked:
        eligibility = SearchablePdfPageEligibility(
            page.output_page_index,
            page.page_id,
            SearchablePdfPageEligibilityCode.OCR_BLOCKED_SIGNATURE_STATE,
        )
        expected = _render_one(
            adapter.renderer,
            base_path,
            0,
            PdfRenderRotation.DEGREES_0,
            limits,
            control,
        )
        _append_pdf_page(output, base_path, 0, 0, control)
        return _SelectedPage(
            SearchablePdfPageOutcome(
                eligibility,
                SearchablePdfPageOutcomeCode.OCR_BLOCKED_SIGNED_SOURCE_RETAINED,
                SearchablePdfPageSearchability.NOT_VERIFIED,
                SearchablePdfPageFailureCode.SIGNATURE_STATE_BLOCKED,
            ),
            expected,
            _read_physical_geometry(
                base_path,
                0,
                PdfRenderRotation.DEGREES_0,
                limits,
                control,
            ),
        )

    eligibility = SearchablePdfPageEligibility(
        page.output_page_index,
        page.page_id,
        SearchablePdfPageEligibilityCode.OCR_REQUIRED_IMAGE_ONLY_PDF,
    )
    return _ocr_select_and_append(
        adapter,
        output,
        plan,
        page,
        eligibility,
        base_path,
        page_directory,
        operation_directory,
        languages,
        limits,
        control,
    )


def _process_image_page(
    adapter: SearchablePdfExportAdapter,
    output: pikepdf.Pdf,
    plan: SearchablePdfExportPlan,
    page: SearchablePdfPage,
    binding: _SourceBinding,
    page_directory: Path,
    operation_directory: Path,
    languages: tuple[str, ...],
    limits: SearchablePdfExportAdapterLimits,
    control: SearchablePdfExportAdapterControl,
) -> _SelectedPage:
    normalized_path = page_directory / "normalized.rgba"
    transformed_path = page_directory / "transformed.rgba"
    base_path = page_directory / "base.pdf"
    source_format = (
        ImageSourceFormat.JPEG
        if binding.source.media_type is SearchablePdfSourceMediaType.JPEG
        else ImageSourceFormat.PNG
    )
    request = ImageNormalizationRequest(
        expected_source_format=source_format,
        expected_source_bytes=binding.source.byte_size,
        expected_source_sha256=binding.source.sha256,
        maximum_width_pixels=min(
            limits.image_normalization.maximum_output_width_pixels,
            limits.image_transform.maximum_source_width_pixels,
        ),
        maximum_height_pixels=min(
            limits.image_normalization.maximum_output_height_pixels,
            limits.image_transform.maximum_source_height_pixels,
        ),
        additional_rotation=ImageQuarterTurn.DEGREES_0,
    )
    try:
        with (
            binding.path.open("rb") as source,
            normalized_path.open("x+b") as normalized,
        ):
            normalized_result = adapter.image_decoder.normalize(
                source,
                normalized,
                expected_format=source_format,
                request=request,
                limits=limits.image_normalization,
                control=control,
            )
    except ImagePageError, PdfExportError:
        raise
    except OSError:
        reject_pdf_export(PdfExportErrorCode.ASSEMBLY_FAILED, "export.image_scratch")
    source_layout = ImageRgba8Layout(
        normalized_result.width_pixels,
        normalized_result.height_pixels,
        normalized_result.row_stride_bytes,
        normalized_result.width_pixels * normalized_result.height_pixels,
        normalized_result.bytes_written,
    )
    _require_raw_digest(
        normalized_path,
        source_layout,
        normalized_result.pixel_sha256,
        control,
    )
    transformed_result = _transform_rgba_page(
        adapter,
        normalized_path,
        transformed_path,
        source_layout,
        page,
        limits,
        control,
    )
    transformed_layout = _transform_result_layout(transformed_result)
    _require_raw_digest(
        transformed_path,
        transformed_layout,
        transformed_result.pixel_sha256,
        control,
    )
    _require_page_scratch(
        page_directory,
        (
            (normalized_path, normalized_result.bytes_written),
            (transformed_path, transformed_result.bytes_written),
        ),
        limits,
        control,
    )
    _remove_authenticated_page_artifact(
        normalized_path,
        normalized_result.bytes_written,
        page_directory,
        control,
    )
    _require_page_scratch(
        page_directory,
        ((transformed_path, transformed_result.bytes_written),),
        limits,
        control,
    )
    base_result = adapter.raster_page_builder.build(
        transformed_path,
        base_path,
        transformed_layout,
        limits.raster_pdf,
        control,
        geometry=None,
    )
    base_byte_size = _require_raster_result(base_path, base_result, limits, control)
    _require_page_scratch(
        page_directory,
        (
            (transformed_path, transformed_result.bytes_written),
            (base_path, base_byte_size),
        ),
        limits,
        control,
    )
    _remove_authenticated_page_artifact(
        transformed_path,
        transformed_result.bytes_written,
        page_directory,
        control,
    )
    _require_page_scratch(
        page_directory,
        ((base_path, base_byte_size),),
        limits,
        control,
    )
    eligibility = SearchablePdfPageEligibility(
        page.output_page_index,
        page.page_id,
        SearchablePdfPageEligibilityCode.OCR_REQUIRED_IMAGE_SOURCE,
    )
    return _ocr_select_and_append(
        adapter,
        output,
        plan,
        page,
        eligibility,
        base_path,
        page_directory,
        operation_directory,
        languages,
        limits,
        control,
    )


def _rasterize_transformed_pdf_page(
    adapter: SearchablePdfExportAdapter,
    page: SearchablePdfPage,
    binding: _SourceBinding,
    page_directory: Path,
    base_path: Path,
    limits: SearchablePdfExportAdapterLimits,
    control: SearchablePdfExportAdapterControl,
) -> None:
    source_rgba = page_directory / "pdf-source.rgba"
    transformed_rgba = page_directory / "pdf-transformed.rgba"
    request = PdfPageRenderRequest(
        page_index=page.source_page_index,
        maximum_width_pixels=limits.pdf_transform_width_pixels,
        maximum_height_pixels=limits.pdf_transform_height_pixels,
        page_box=PdfPageBox.CROP,
        additional_rotation=PdfRenderRotation.DEGREES_0,
    )
    source_geometry = _read_physical_geometry(
        binding.path,
        page.source_page_index,
        PdfRenderRotation.DEGREES_0,
        limits,
        control,
    )
    try:
        with binding.path.open("rb") as source, source_rgba.open("x+b") as destination:
            rendered = adapter.renderer.render(
                source,
                destination,
                request,
                limits.rendering,
                control,
            )
    except PdfRenderError as error:
        _map_render_error(error)
    except PdfExportError:
        raise
    except OSError:
        reject_pdf_export(PdfExportErrorCode.ASSEMBLY_FAILED, "export.pdf_transform")
    source_layout = ImageRgba8Layout(
        rendered.width_pixels,
        rendered.height_pixels,
        rendered.row_stride_bytes,
        rendered.width_pixels * rendered.height_pixels,
        rendered.bytes_written,
    )
    _require_raw_digest(source_rgba, source_layout, rendered.pixel_sha256, control)
    transformed_result = _transform_rgba_page(
        adapter,
        source_rgba,
        transformed_rgba,
        source_layout,
        page,
        limits,
        control,
    )
    transformed_layout = _transform_result_layout(transformed_result)
    _require_raw_digest(
        transformed_rgba,
        transformed_layout,
        transformed_result.pixel_sha256,
        control,
    )
    _require_page_scratch(
        page_directory,
        (
            (source_rgba, rendered.bytes_written),
            (transformed_rgba, transformed_result.bytes_written),
        ),
        limits,
        control,
    )
    _remove_authenticated_page_artifact(
        source_rgba,
        rendered.bytes_written,
        page_directory,
        control,
    )
    _require_page_scratch(
        page_directory,
        ((transformed_rgba, transformed_result.bytes_written),),
        limits,
        control,
    )
    base_result = adapter.raster_page_builder.build(
        transformed_rgba,
        base_path,
        transformed_layout,
        limits.raster_pdf,
        control,
        geometry=_transformed_physical_geometry(source_geometry, page),
    )
    base_byte_size = _require_raster_result(base_path, base_result, limits, control)
    _require_page_scratch(
        page_directory,
        (
            (transformed_rgba, transformed_result.bytes_written),
            (base_path, base_byte_size),
        ),
        limits,
        control,
    )
    _remove_authenticated_page_artifact(
        transformed_rgba,
        transformed_result.bytes_written,
        page_directory,
        control,
    )
    _require_page_scratch(
        page_directory,
        ((base_path, base_byte_size),),
        limits,
        control,
    )


def _transform_rgba_page(
    adapter: SearchablePdfExportAdapter,
    source_path: Path,
    destination_path: Path,
    source_layout: ImageRgba8Layout,
    page: SearchablePdfPage,
    limits: SearchablePdfExportAdapterLimits,
    control: SearchablePdfExportAdapterControl,
) -> ImageTransformResult:
    intent = _image_transform_intent(page)
    try:
        with source_path.open("rb") as source, destination_path.open("x+b") as destination:
            return adapter.image_transformer.transform(
                source,
                destination,
                source_layout=source_layout,
                intent=intent,
                maximum_width_pixels=limits.image_transform.maximum_output_width_pixels,
                maximum_height_pixels=limits.image_transform.maximum_output_height_pixels,
                limits=limits.image_transform,
                control=control,
            )
    except ImagePageError, PdfExportError:
        raise
    except OSError:
        reject_pdf_export(PdfExportErrorCode.ASSEMBLY_FAILED, "export.transform_scratch")


def _image_transform_intent(page: SearchablePdfPage) -> ImageTransformIntent:
    crop = (
        ImageNormalizedCrop()
        if page.crop is None
        else ImageNormalizedCrop(page.crop.x, page.crop.y, page.crop.width, page.crop.height)
    )
    return ImageTransformIntent(
        crop=crop,
        additional_rotation=ImageQuarterTurn(page.quarter_turns_clockwise * 90),
        deskew_angle_millidegrees_clockwise=page.deskew_angle_millidegrees_clockwise,
        contrast_factor_millionths=page.contrast_factor_millionths,
        background_cleanup_strength_millionths=page.background_cleanup_strength_millionths,
    )


def _transformed_physical_geometry(
    source: RasterPdfPageGeometry,
    page: SearchablePdfPage,
) -> RasterPdfPageGeometry:
    width = source.width_points
    height = source.height_points
    if page.crop is not None:
        width *= page.crop.width / 1_000_000
        height *= page.crop.height / 1_000_000
    if page.quarter_turns_clockwise % 2:
        width, height = height, width
    return RasterPdfPageGeometry(float(width), float(height))


def _transform_result_layout(result: ImageTransformResult) -> ImageRgba8Layout:
    if type(result) is not ImageTransformResult:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.transform_result")
    return ImageRgba8Layout(
        result.width_pixels,
        result.height_pixels,
        result.row_stride_bytes,
        result.width_pixels * result.height_pixels,
        result.bytes_written,
    )


def _ocr_select_and_append(
    adapter: SearchablePdfExportAdapter,
    output: pikepdf.Pdf,
    plan: SearchablePdfExportPlan,
    page: SearchablePdfPage,
    eligibility: SearchablePdfPageEligibility,
    base_path: Path,
    page_directory: Path,
    operation_directory: Path,
    languages: tuple[str, ...],
    limits: SearchablePdfExportAdapterLimits,
    control: SearchablePdfExportAdapterControl,
) -> _SelectedPage:
    if page_directory.parent != operation_directory:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.ocr_temp_scope")
    base_fact = _inspect_one(adapter.text_inspector, base_path, 0, limits, control)
    if base_fact.has_extractable_text:
        reject_pdf_export(
            PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH,
            "export.ocr_base_text",
        )
    base_render = _render_one(
        adapter.renderer,
        base_path,
        0,
        PdfRenderRotation.DEGREES_0,
        limits,
        control,
    )
    base_geometry = _read_physical_geometry(
        base_path,
        0,
        PdfRenderRotation.DEGREES_0,
        limits,
        control,
    )
    candidate_path = page_directory / "ocr.pdf"
    try:
        result = adapter.ocr_processor.process(
            base_path,
            candidate_path,
            temporary_directory=operation_directory,
            languages=languages,
            profile_id=plan.profile.ocr_profile_id,
            expected_page_count=1,
            maximum_output_bytes=limits.maximum_ocr_output_bytes,
            maximum_image_pixels=limits.maximum_ocr_image_pixels,
            maximum_temporary_storage_bytes=limits.maximum_ocr_temporary_storage_bytes,
            control=control,
            progress=lambda *_values: _checkpoint(control),
        )
    except OcrError as error:
        if not _is_local_recognition_failure(error):
            raise
        _append_pdf_page(output, base_path, 0, 0, control)
        return _SelectedPage(
            SearchablePdfPageOutcome(
                eligibility,
                SearchablePdfPageOutcomeCode.OCR_FAILED_RETAINED,
                SearchablePdfPageSearchability.NOT_VERIFIED,
                SearchablePdfPageFailureCode.OCR_ENGINE_FAILED,
            ),
            base_render,
            base_geometry,
        )
    if type(result) is not OcrAdapterResult or result.page_count != 1:
        reject_pdf_export(PdfExportErrorCode.VERIFICATION_REOPEN_FAILED, "export.ocr_result")
    candidate_measurement = _measure_file(
        candidate_path,
        maximum_bytes=limits.maximum_ocr_output_bytes,
        control=control,
        source=False,
    )
    if candidate_measurement != (result.byte_size, result.sha256):
        reject_pdf_export(
            PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
            "export.ocr_changed",
        )
    candidate_render = _render_one(
        adapter.renderer,
        candidate_path,
        0,
        PdfRenderRotation.DEGREES_0,
        limits,
        control,
    )
    candidate_geometry = _read_physical_geometry(
        candidate_path,
        0,
        PdfRenderRotation.DEGREES_0,
        limits,
        control,
    )
    if not _same_physical_geometry(base_geometry, candidate_geometry):
        reject_pdf_export(
            PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH,
            "export.ocr_geometry_mismatch",
        )
    if not _same_rendered_page(base_render, candidate_render):
        reject_pdf_export(
            PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH,
            "export.ocr_render_mismatch",
        )
    candidate_fact = _inspect_one(
        adapter.text_inspector,
        candidate_path,
        0,
        limits,
        control,
    )
    if (
        _measure_file(
            candidate_path,
            maximum_bytes=limits.maximum_ocr_output_bytes,
            control=control,
            source=False,
        )
        != candidate_measurement
    ):
        reject_pdf_export(
            PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
            "export.ocr_changed",
        )
    if not candidate_fact.has_extractable_text:
        _append_pdf_page(output, base_path, 0, 0, control)
        return _SelectedPage(
            SearchablePdfPageOutcome(
                eligibility,
                SearchablePdfPageOutcomeCode.OCR_NO_TEXT_RETAINED,
                SearchablePdfPageSearchability.NOT_VERIFIED,
                SearchablePdfPageFailureCode.NO_EXTRACTABLE_TEXT,
            ),
            base_render,
            base_geometry,
        )
    _append_pdf_page(output, candidate_path, 0, 0, control)
    return _SelectedPage(
        SearchablePdfPageOutcome(
            eligibility,
            SearchablePdfPageOutcomeCode.OCR_TEXT_VERIFIED,
            SearchablePdfPageSearchability.EXTRACTABLE_TEXT_PRESENT_OCR,
        ),
        candidate_render,
        candidate_geometry,
    )


def _is_local_recognition_failure(error: OcrError) -> bool:
    return error.code is OcrErrorCode.ENGINE_FAILED and error.context == "ocr.tesseract_exit"


def _inspect_one(
    inspector: SearchablePdfTextInspector,
    path: Path,
    page_index: int,
    limits: SearchablePdfExportAdapterLimits,
    control: SearchablePdfExportAdapterControl,
) -> PdfTextPresenceFact:
    facts = inspector.inspect(path, (page_index,), limits.text_presence, control)
    if (
        type(facts) is not tuple
        or len(facts) != 1
        or type(facts[0]) is not PdfTextPresenceFact
        or facts[0].page_index != page_index
    ):
        reject_pdf_export(PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "export.text_result")
    return facts[0]


def _append_pdf_page(
    output: pikepdf.Pdf,
    source_path: Path,
    page_index: int,
    additional_rotation_degrees: int,
    control: SearchablePdfExportAdapterControl,
) -> None:
    _checkpoint(control)
    try:
        with source_path.open("rb") as stream:
            with _strict_open(stream) as source:
                if source.is_encrypted or not 0 <= page_index < len(source.pages):
                    reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.page_source")
                source_page = source.pages[page_index].obj
                attributes = _effective_page_attributes(source_page)
                combined = (attributes.rotation + additional_rotation_degrees) % 360
                _materialize_page(source_page, attributes, combined)
                output.pages.append(pikepdf.Page(source_page))
                copied = output.pages[-1].obj
                copied[_NAME_ROTATE] = combined
                for key in (_NAME_LAST_MODIFIED, _NAME_METADATA, _NAME_PIECE_INFO):
                    if key in copied:
                        del copied[key]
    except PdfExportError:
        raise
    except pikepdf.PasswordError:
        reject_pdf_export(PdfExportErrorCode.SOURCE_ENCRYPTED, "export.page_source")
    except MemoryError, RecursionError:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.page_resources")
    except Exception:
        reject_pdf_export(PdfExportErrorCode.ASSEMBLY_FAILED, "export.page_copy")
    _checkpoint(control)


def _write_one_pdf_page(
    source_path: Path,
    page_index: int,
    additional_rotation_degrees: int,
    output_path: Path,
    limits: SearchablePdfExportAdapterLimits,
    control: SearchablePdfExportAdapterControl,
) -> None:
    try:
        stream = output_path.open("x+b")
    except OSError:
        reject_pdf_export(PdfExportErrorCode.ASSEMBLY_FAILED, "export.page_scratch")
    bounded = _BoundedOutput(stream, limits.maximum_ocr_output_bytes)
    try:
        with stream, pikepdf.Pdf.new() as output:
            _append_pdf_page(
                output,
                source_path,
                page_index,
                additional_rotation_degrees,
                control,
            )
            output.save(
                cast(BinaryIO, bounded),
                deterministic_id=True,
                encryption=False,
                compress_streams=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
                normalize_content=False,
                linearize=False,
            )
            bounded.flush()
            os.fsync(bounded.fileno())
    except PdfExportError:
        output_path.unlink(missing_ok=True)
        raise
    except Exception:
        output_path.unlink(missing_ok=True)
        reject_pdf_export(
            PdfExportErrorCode.LIMIT_EXCEEDED
            if bounded.limit_exceeded
            else PdfExportErrorCode.ASSEMBLY_FAILED,
            "export.page_scratch",
        )


def _effective_page_attributes(page: pikepdf.Dictionary) -> _EffectivePageAttributes:
    media_box: object | None = None
    crop_box: object | None = None
    resources: object | None = None
    rotate: object | None = None
    current = page
    seen: set[tuple[int, int]] = set()
    for _depth in range(_MAXIMUM_INHERITANCE_DEPTH):
        try:
            identity = tuple(current.objgen)
        except Exception:
            reject_pdf_export(PdfExportErrorCode.SOURCE_UNSUPPORTED, "export.page_geometry")
        if len(identity) != 2 or identity in seen:
            reject_pdf_export(PdfExportErrorCode.SOURCE_UNSUPPORTED, "export.page_geometry")
        seen.add(identity)
        if media_box is None:
            media_box = current.get(_NAME_MEDIA_BOX)
        if crop_box is None:
            crop_box = current.get(_NAME_CROP_BOX)
        if resources is None:
            resources = current.get(_NAME_RESOURCES)
        if rotate is None:
            rotate = current.get(_NAME_ROTATE)
        parent = current.get(_NAME_PARENT)
        if parent is None:
            break
        if not isinstance(parent, pikepdf.Dictionary):
            reject_pdf_export(PdfExportErrorCode.SOURCE_UNSUPPORTED, "export.page_geometry")
        current = parent
    else:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.page_inheritance")
    if media_box is None:
        reject_pdf_export(PdfExportErrorCode.SOURCE_UNSUPPORTED, "export.page_geometry")
    if rotate is None:
        rotation = 0
    elif type(rotate) is int and rotate % 90 == 0:
        rotation = rotate % 360
    else:
        reject_pdf_export(PdfExportErrorCode.SOURCE_UNSUPPORTED, "export.page_rotation")
    return _EffectivePageAttributes(
        media_box,
        media_box if crop_box is None else crop_box,
        resources,
        rotation,
    )


def _materialize_page(
    page: pikepdf.Dictionary,
    attributes: _EffectivePageAttributes,
    rotation: int,
) -> None:
    page[_NAME_MEDIA_BOX] = attributes.media_box
    page[_NAME_CROP_BOX] = attributes.crop_box
    page[_NAME_ROTATE] = rotation
    page[_NAME_RESOURCES] = (
        pikepdf.Dictionary() if attributes.resources is None else attributes.resources
    )


def _save_output(
    document: pikepdf.Pdf,
    output_path: Path,
    limits: SearchablePdfExportAdapterLimits,
    control: SearchablePdfExportAdapterControl,
    *,
    portal: bool = False,
) -> None:
    try:
        stream = output_path.open("x+b")
    except FileExistsError:
        reject_pdf_export(PdfExportErrorCode.DESTINATION_EXISTS, "export.output_exists")
    except OSError:
        reject_pdf_export(PdfExportErrorCode.DESTINATION_INVALID, "export.output_path")
    bounded = _BoundedOutput(stream, limits.inspection.maximum_output_bytes)
    try:
        with stream:
            _checkpoint(control)
            document.save(
                cast(BinaryIO, bounded),
                deterministic_id=True,
                encryption=False,
                compress_streams=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
                normalize_content=False,
                linearize=False,
                min_version=PORTAL_MINIMUM_OUTPUT_VERSION if portal else "",
            )
            bounded.flush()
            os.fsync(bounded.fileno())
            _checkpoint(control)
    except PdfExportError:
        _remove_failed_candidate(output_path)
        raise
    except Exception:
        _remove_failed_candidate(output_path)
        reject_pdf_export(
            PdfExportErrorCode.LIMIT_EXCEEDED
            if bounded.limit_exceeded
            else PdfExportErrorCode.ASSEMBLY_FAILED,
            "export.output_save",
        )


def _verify_structure(
    output_path: Path,
    plan: SearchablePdfExportPlan,
    limits: SearchablePdfExportAdapterLimits,
    control: SearchablePdfExportAdapterControl,
) -> None:
    try:
        with output_path.open("rb") as stream:
            with _strict_open(stream) as document:
                _checkpoint(control)
                if document.is_encrypted:
                    reject_pdf_export(
                        PdfExportErrorCode.VERIFICATION_ENCRYPTION_FAILED,
                        "export.output_encrypted",
                    )
                if len(document.pages) != plan.page_count:
                    reject_pdf_export(
                        PdfExportErrorCode.VERIFICATION_PAGE_COUNT_MISMATCH,
                        "export.output_pages",
                    )
                if len(document.objects) > limits.inspection.maximum_objects:
                    reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.output_objects")
                if plan.profile.pdf_profile.is_portal:
                    require_portal_output(
                        document,
                        maximum_objects=limits.inspection.maximum_objects,
                        checkpoint=lambda: _checkpoint(control),
                    )
                warnings = document.check_pdf_syntax(progress=lambda _value: _checkpoint(control))
                if type(warnings) is not list or warnings:
                    reject_pdf_export(
                        PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
                        "export.output_syntax",
                    )
    except PdfExportError:
        raise
    except pikepdf.PasswordError:
        reject_pdf_export(
            PdfExportErrorCode.VERIFICATION_ENCRYPTION_FAILED,
            "export.output_encrypted",
        )
    except pikepdf.PdfError, RuntimeError, ValueError, OSError:
        reject_pdf_export(
            PdfExportErrorCode.VERIFICATION_REOPEN_FAILED,
            "export.output_reopen",
        )


def _verify_qpdf(
    checker: SearchablePdfStructureChecker,
    output_path: Path,
    limits: SearchablePdfExportAdapterLimits,
    control: SearchablePdfExportAdapterControl,
) -> None:
    _checkpoint(control)
    try:
        state = checker.check(
            output_path,
            limits=limits.inspection,
            deadline=float(control.deadline),
            cancellation_probe=control.cancellation_requested,
            monotonic=control.monotonic,
        )
    except PdfInspectionError as error:
        if error.code is PdfInspectionErrorCode.CANCELLED:
            reject_pdf_export(PdfExportErrorCode.CANCELLED, "export.qpdf")
        if error.code is PdfInspectionErrorCode.TIMED_OUT:
            reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "export.qpdf")
        if error.code is PdfInspectionErrorCode.LIMIT_EXCEEDED:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.qpdf")
        reject_pdf_export(PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "export.qpdf")
    except PdfExportError:
        raise
    except Exception:
        reject_pdf_export(PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "export.qpdf")
    if state is not PdfStructuralState.CLEAN:
        reject_pdf_export(PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "export.qpdf")


def _verify_final_renders(
    renderer: PdfPageRenderer,
    output_path: Path,
    expected: tuple[tuple[PdfPageRenderResult, RasterPdfPageGeometry], ...],
    limits: SearchablePdfExportAdapterLimits,
    control: SearchablePdfExportAdapterControl,
    progress: SearchablePdfAdapterProgressSink,
) -> None:
    for page_index, (expected_render, expected_geometry) in enumerate(expected):
        actual_geometry = _read_physical_geometry(
            output_path,
            page_index,
            PdfRenderRotation.DEGREES_0,
            limits,
            control,
        )
        if not _same_physical_geometry(expected_geometry, actual_geometry):
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH,
                "export.geometry_mismatch",
            )
        actual = _render_one(
            renderer,
            output_path,
            page_index,
            PdfRenderRotation.DEGREES_0,
            limits,
            control,
        )
        if not _same_rendered_page(expected_render, actual):
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH,
                "export.render_mismatch",
            )
        _emit_progress(
            progress,
            SearchablePdfAdapterProgressPhase.VERIFYING,
            page_index + 1,
            len(expected),
        )


def _read_physical_geometry(
    path: Path,
    page_index: int,
    additional_rotation: PdfRenderRotation,
    limits: SearchablePdfExportAdapterLimits,
    control: SearchablePdfExportAdapterControl,
) -> RasterPdfPageGeometry:
    _checkpoint(control)
    request = PdfPageRenderRequest(
        page_index=page_index,
        maximum_width_pixels=limits.verification_width_pixels,
        maximum_height_pixels=limits.verification_height_pixels,
        page_box=PdfPageBox.CROP,
        additional_rotation=additional_rotation,
    )
    resolver = PikePdfPageGeometryResolver()
    geometry_limits = PdfPageGeometryLimits(
        MAX_PDF_PAGE_COORDINATE_MAGNITUDE_UNITS,
        MAX_PDF_PAGE_SPAN_UNITS,
    )
    try:
        with path.open("rb") as source:
            resolved = resolver.resolve(source, request, limits.rendering, geometry_limits)
    except PdfRenderError as error:
        _map_render_error(error)
    except PdfExportError:
        raise
    except Exception:
        reject_pdf_export(PdfExportErrorCode.VERIFICATION_RENDER_FAILED, "export.geometry")
    return _physical_geometry(resolved, additional_rotation)


def _physical_geometry(
    geometry: PdfResolvedPageGeometry,
    additional_rotation: PdfRenderRotation,
) -> RasterPdfPageGeometry:
    combined = (int(geometry.intrinsic_rotation) + int(additional_rotation)) % 360
    width = geometry.effective_box.width * geometry.user_unit
    height = geometry.effective_box.height * geometry.user_unit
    if combined in (90, 270):
        width, height = height, width
    return RasterPdfPageGeometry(float(width), float(height))


def _same_physical_geometry(
    source: RasterPdfPageGeometry,
    output: RasterPdfPageGeometry,
) -> bool:
    return math.isclose(
        source.width_points,
        output.width_points,
        rel_tol=0.0,
        abs_tol=_PHYSICAL_GEOMETRY_TOLERANCE_POINTS,
    ) and math.isclose(
        source.height_points,
        output.height_points,
        rel_tol=0.0,
        abs_tol=_PHYSICAL_GEOMETRY_TOLERANCE_POINTS,
    )


def _verify_final_text(
    inspector: SearchablePdfTextInspector,
    output_path: Path,
    outcomes: tuple[SearchablePdfPageOutcome, ...],
    limits: SearchablePdfExportAdapterLimits,
    control: SearchablePdfExportAdapterControl,
) -> None:
    facts = inspector.inspect(
        output_path,
        tuple(range(len(outcomes))),
        limits.text_presence,
        control,
    )
    if (
        type(facts) is not tuple
        or len(facts) != len(outcomes)
        or any(type(fact) is not PdfTextPresenceFact for fact in facts)
    ):
        reject_pdf_export(PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "export.text_result")
    for page_index, (outcome, fact) in enumerate(zip(outcomes, facts, strict=True)):
        expected_text = outcome.outcome in {
            SearchablePdfPageOutcomeCode.EXISTING_TEXT_PRESERVED,
            SearchablePdfPageOutcomeCode.OCR_TEXT_VERIFIED,
        }
        if fact.page_index != page_index or fact.has_extractable_text is not expected_text:
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH,
                "export.text_mismatch",
            )


def _render_one(
    renderer: PdfPageRenderer,
    path: Path,
    page_index: int,
    rotation: PdfRenderRotation,
    limits: SearchablePdfExportAdapterLimits,
    control: SearchablePdfExportAdapterControl,
) -> PdfPageRenderResult:
    request = PdfPageRenderRequest(
        page_index=page_index,
        maximum_width_pixels=limits.verification_width_pixels,
        maximum_height_pixels=limits.verification_height_pixels,
        page_box=PdfPageBox.CROP,
        additional_rotation=rotation,
    )
    destination = _DiscardRaster(limits.rendering.maximum_destination_bytes)
    try:
        with path.open("rb") as source:
            result = renderer.render(source, destination, request, limits.rendering, control)
    except PdfRenderError as error:
        _map_render_error(error)
    except PdfExportError:
        raise
    except Exception:
        reject_pdf_export(PdfExportErrorCode.VERIFICATION_RENDER_FAILED, "export.render")
    if type(result) is not PdfPageRenderResult or destination.bytes_written != result.bytes_written:
        reject_pdf_export(PdfExportErrorCode.VERIFICATION_RENDER_FAILED, "export.render_result")
    return result


def _map_render_error(error: PdfRenderError) -> None:
    if error.code is PdfRenderErrorCode.CANCELLED:
        reject_pdf_export(PdfExportErrorCode.CANCELLED, "export.render")
    if error.code is PdfRenderErrorCode.TIMED_OUT:
        reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "export.render")
    if error.code is PdfRenderErrorCode.LIMIT_EXCEEDED:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.render")
    reject_pdf_export(PdfExportErrorCode.VERIFICATION_RENDER_FAILED, "export.render")


def _same_rendered_page(source: PdfPageRenderResult, output: PdfPageRenderResult) -> bool:
    return (
        source.width_pixels == output.width_pixels
        and source.height_pixels == output.height_pixels
        and source.row_stride_bytes == output.row_stride_bytes
        and source.bytes_written == output.bytes_written
        and source.pixel_sha256 == output.pixel_sha256
        and source.page_box is output.page_box
        and source.pixel_format is output.pixel_format
        and source.background is output.background
        and source.annotations is output.annotations
        and (int(source.intrinsic_rotation) + int(source.additional_rotation)) % 360
        == (int(output.intrinsic_rotation) + int(output.additional_rotation)) % 360
        and output.additional_rotation is PdfRenderRotation.DEGREES_0
    )


def _page_rotation(page: SearchablePdfPage) -> PdfRenderRotation:
    return PdfRenderRotation(page.quarter_turns_clockwise * 90)


def _require_raw_digest(
    path: Path,
    layout: ImageRgba8Layout,
    expected_sha256: str,
    control: SearchablePdfExportAdapterControl,
) -> None:
    size, digest = _measure_file(
        path,
        maximum_bytes=layout.byte_count,
        control=control,
        source=True,
    )
    if size != layout.byte_count or digest != expected_sha256:
        reject_pdf_export(PdfExportErrorCode.VERIFICATION_RENDER_FAILED, "export.render_raster")


def _require_raster_result(
    path: Path,
    result: RasterPdfPageResult,
    limits: SearchablePdfExportAdapterLimits,
    control: SearchablePdfExportAdapterControl,
) -> int:
    if type(result) is not RasterPdfPageResult:
        reject_pdf_export(PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "export.raster_result")
    measurement = _measure_file(
        path,
        maximum_bytes=limits.raster_pdf.maximum_output_bytes,
        control=control,
        source=False,
    )
    if measurement != (result.byte_size, result.sha256):
        reject_pdf_export(PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "export.raster_result")
    return result.byte_size


def _require_page_scratch(
    page_directory: Path,
    artifacts: tuple[tuple[Path, int], ...],
    limits: SearchablePdfExportAdapterLimits,
    control: SearchablePdfExportAdapterControl,
) -> None:
    """Authenticate the complete pre-OCR tree and its aggregate disk charge."""

    _checkpoint(control)
    if (
        not artifacts
        or any(
            not isinstance(path, Path)
            or path.parent != page_directory
            or type(byte_size) is not int
            or byte_size < 1
            for path, byte_size in artifacts
        )
        or len({path.name for path, _byte_size in artifacts}) != len(artifacts)
    ):
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.page_scratch_model")
    expected = {path.name: byte_size for path, byte_size in artifacts}
    try:
        with os.scandir(page_directory) as iterator:
            entries = tuple(iterator)
        if {entry.name for entry in entries} != set(expected):
            raise OSError
        consumed = 0
        for entry in entries:
            info = os.lstat(entry.path)
            if (
                not stat.S_ISREG(info.st_mode)
                or _is_reparse(info)
                or info.st_size != expected[entry.name]
            ):
                raise OSError
            consumed += info.st_size
    except OSError:
        reject_pdf_export(
            PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
            "export.page_scratch_artifacts",
        )
    if consumed > limits.maximum_page_scratch_bytes:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.page_scratch_bytes")
    try:
        control.page_scratch_observer(consumed)
    except IncrementalProcessingError:
        raise
    except BaseException:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.page_scratch_observer")
    _checkpoint(control)


def _remove_authenticated_page_artifact(
    path: Path,
    expected_byte_size: int,
    page_directory: Path,
    control: SearchablePdfExportAdapterControl,
) -> None:
    """Delete one authenticated scratch file and prove that it is gone."""

    _checkpoint(control)
    if (
        not isinstance(path, Path)
        or not isinstance(page_directory, Path)
        or path.parent != page_directory
        or type(expected_byte_size) is not int
        or expected_byte_size < 1
    ):
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.page_scratch_model")
    try:
        info = os.lstat(path)
    except OSError:
        reject_pdf_export(
            PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
            "export.page_scratch_artifacts",
        )
    if not stat.S_ISREG(info.st_mode) or _is_reparse(info) or info.st_size != expected_byte_size:
        reject_pdf_export(
            PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
            "export.page_scratch_artifacts",
        )
    try:
        path.unlink()
    except OSError:
        reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.page_scratch_cleanup")
    try:
        os.lstat(path)
    except FileNotFoundError:
        _checkpoint(control)
        return
    except OSError:
        reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.page_scratch_cleanup")
    reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.page_scratch_cleanup")


def _measure_output(
    path: Path,
    limits: SearchablePdfExportAdapterLimits,
    control: SearchablePdfExportAdapterControl,
) -> tuple[int, str]:
    return _measure_file(
        path,
        maximum_bytes=limits.inspection.maximum_output_bytes,
        control=control,
        source=False,
    )


def _measure_file(
    path: Path,
    *,
    maximum_bytes: int,
    control: SearchablePdfExportAdapterControl,
    source: bool,
) -> tuple[int, str]:
    digest = hashlib.sha256()
    consumed = 0
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or _is_reparse(info):
            raise OSError
        with path.open("rb") as stream:
            while True:
                _checkpoint(control)
                block = stream.read(_HASH_CHUNK_BYTES)
                if type(block) is not bytes:
                    raise OSError
                if not block:
                    break
                consumed += len(block)
                if consumed > maximum_bytes:
                    reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.file_bytes")
                digest.update(block)
        if consumed != info.st_size or consumed < 1:
            raise OSError
    except PdfExportError:
        raise
    except OSError:
        reject_pdf_export(
            PdfExportErrorCode.SOURCE_CHANGED
            if source
            else PdfExportErrorCode.VERIFICATION_REOPEN_FAILED,
            "export.file_read",
        )
    return consumed, digest.hexdigest()


def _strict_open(stream: BinaryIO) -> pikepdf.Pdf:
    return pikepdf.Pdf.open(
        stream,
        password="",
        attempt_recovery=False,
        suppress_warnings=True,
        inherit_page_attributes=False,
    )


def _create_page_directory(root: Path) -> Path:
    for _attempt in range(32):
        candidate = root / f"pws-searchable-page-{uuid4().hex}"
        try:
            candidate.mkdir()
            info = os.lstat(candidate)
        except FileExistsError:
            continue
        except OSError:
            reject_pdf_export(PdfExportErrorCode.ASSEMBLY_FAILED, "export.page_temp")
        if not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
            reject_pdf_export(PdfExportErrorCode.ASSEMBLY_FAILED, "export.page_temp")
        return candidate
    reject_pdf_export(PdfExportErrorCode.ASSEMBLY_FAILED, "export.page_temp")


def _remove_page_directory(path: Path) -> bool:
    try:
        shutil.rmtree(path)
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)


def _checkpoint(control: SearchablePdfExportAdapterControl) -> None:
    if control.cancellation_requested():
        reject_pdf_export(PdfExportErrorCode.CANCELLED, "export.operation")
    if control.deadline_expired():
        reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "export.operation")


def _emit_progress(
    sink: SearchablePdfAdapterProgressSink,
    phase: SearchablePdfAdapterProgressPhase,
    completed: int,
    total: int,
) -> None:
    try:
        sink(phase, completed, total)
    except Exception:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.progress")


def _remove_failed_candidate(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.output_cleanup")

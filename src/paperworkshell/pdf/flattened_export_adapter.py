"""Worker-side composition and verification for flattened visual overlays.

The adapter extends, but does not alter, the frozen ``pdf.export`` version-1
adapter.  It accepts only authenticated, seekable source streams and raster
sources, writes one exclusively new worker-private candidate, places each
verified overlay page as an explicit Form XObject, and returns a report only
after strict reopen, qpdf, structural overlay-presence, and PDFium checks.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import warnings
import zlib
from array import array
from collections.abc import Buffer, Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO, Final, Never, Protocol, cast
from uuid import UUID

import pikepdf

from paperworkshell.domain.page_coordinates import (
    NormalizedPageBox,
    NormalizedPagePoint,
    PageCoordinateTransform,
    PdfPageCoordinateGeometry,
)
from paperworkshell.domain.pdf_geometry import (
    MAX_PDF_PAGE_COORDINATE_MAGNITUDE_UNITS,
    MAX_PDF_PAGE_SPAN_UNITS,
    PdfPageGeometryLimits,
    PdfPageRectangle,
    PdfPageRenderGeometry,
    PdfResolvedPageGeometry,
    fit_pdf_page_geometry,
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
from paperworkshell.overlays.exporting import (
    FlattenedPdfExportError,
    FlattenedPdfExportErrorCode,
    FlattenedPdfExportPage,
    FlattenedPdfExportPlan,
    FlattenedPdfExportVerificationReport,
    reject_flattened_pdf_export,
)
from paperworkshell.overlays.models import (
    MarkOverlayPayload,
    OverlayAssetRecord,
    OverlayCollection,
    ShapeOverlayPayload,
    ShapePrimitive,
    SignatureImageOverlayPayload,
    TextualOverlayPayload,
)
from paperworkshell.overlays.rendering import (
    OverlayCanvasBox,
    OverlayPageGeometry,
    OverlayPageRenderer,
    OverlayPageRenderRequest,
    OverlayPageRenderResult,
    OverlayPdfPlacementMatrix,
    OverlayRasterAsset,
    OverlayRasterSource,
    OverlayRenderError,
    OverlayRenderErrorCode,
    OverlayRenderLimits,
)
from paperworkshell.pdf.export_adapter import (
    PdfExportAdapterControl,
    PdfExportAdapterLimits,
)
from paperworkshell.pdf.overlay_renderer import FpdfOverlayRenderer
from paperworkshell.pdf.page_geometry import (
    PikePdfPageGeometryResolver,
    to_page_coordinate_geometry,
)
from paperworkshell.pdf.pdfium_renderer import PdfiumPageRenderer
from paperworkshell.pdf.qpdf_checker import QpdfCommandChecker
from paperworkshell.pdf.renderer import PdfPageRenderer, PdfRasterDestination

_HASH_CHUNK_BYTES: Final = 1_048_576
_MAXIMUM_RESOURCE_NAME_ATTEMPTS: Final = 4_096
_SEMANTIC_AA_MARGIN_PIXELS: Final = 2

_NAME_ACRO_FORM: Final = pikepdf.Name("/AcroForm")
_NAME_ANNOTS: Final = pikepdf.Name("/Annots")
_NAME_CHAR_PROCS: Final = pikepdf.Name("/CharProcs")
_NAME_CONTENTS: Final = pikepdf.Name("/Contents")
_NAME_CROP_BOX: Final = pikepdf.Name("/CropBox")
_NAME_FILTER: Final = pikepdf.Name("/Filter")
_NAME_FONT: Final = pikepdf.Name("/Font")
_NAME_LAST_MODIFIED: Final = pikepdf.Name("/LastModified")
_NAME_LENGTH: Final = pikepdf.Name("/Length")
_NAME_MATRIX: Final = pikepdf.Name("/Matrix")
_NAME_MEDIA_BOX: Final = pikepdf.Name("/MediaBox")
_NAME_METADATA: Final = pikepdf.Name("/Metadata")
_NAME_OPEN_ACTION: Final = pikepdf.Name("/OpenAction")
_NAME_OC: Final = pikepdf.Name("/OC")
_NAME_PARENT: Final = pikepdf.Name("/Parent")
_NAME_PATTERN_TYPE: Final = pikepdf.Name("/PatternType")
_NAME_PIECE_INFO: Final = pikepdf.Name("/PieceInfo")
_NAME_RESOURCES: Final = pikepdf.Name("/Resources")
_NAME_ROTATE: Final = pikepdf.Name("/Rotate")
_NAME_SUBTYPE: Final = pikepdf.Name("/Subtype")
_NAME_TYPE: Final = pikepdf.Name("/Type")
_NAME_USER_UNIT: Final = pikepdf.Name("/UserUnit")
_NAME_XOBJECT: Final = pikepdf.Name("/XObject")

_PROHIBITED_FORM_KEYS: Final = frozenset(
    {
        "/A",
        "/AA",
        "/AcroForm",
        "/Annots",
        "/EmbeddedFiles",
        "/Filespec",
        "/JavaScript",
        "/JS",
        "/Launch",
        "/OpenAction",
        "/RichMedia",
        "/SubmitForm",
        "/XFA",
    }
)

_SOURCE_PAGE_AUX_OMITTED_KEYS: Final = frozenset(
    {
        "/Contents",
        "/CropBox",
        "/MediaBox",
        "/Parent",
        "/Resources",
        "/Rotate",
        "/UserUnit",
    }
)


class FlattenedPdfStructureChecker(Protocol):
    """Fixed qpdf seam used after the candidate strictly reopens."""

    def check(
        self,
        source_path: Path,
        *,
        limits: PdfInspectionLimits,
        deadline: float,
        cancellation_probe: Callable[[], bool],
        monotonic: Callable[[], float],
    ) -> PdfStructuralState: ...


class _FingerprintDigest(Protocol):
    def update(self, data: Buffer, /) -> None: ...

    def hexdigest(self) -> str: ...


@dataclass(frozen=True, slots=True, repr=False)
class _SourceBinding:
    source_id: UUID
    stream: BinaryIO = field(repr=False)
    byte_size: int
    page_count: int
    sha256: str = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class _ExpectedOverlayForm:
    output_page_index: int
    resource_name: pikepdf.Name
    invocation: bytes = field(repr=False)
    canvas_width_points: float
    canvas_height_points: float
    placement: OverlayPdfPlacementMatrix = field(repr=False)
    embedded_font_count: int
    image_placement_count: int
    source_content_fingerprint: str = field(repr=False)
    semantic_fingerprint: str = field(repr=False)
    maximum_fingerprint_bytes: int = field(repr=False)
    fingerprint_chunk_bytes: int = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class _ExpectedPage:
    output_page_index: int
    media_box: tuple[float, float, float, float] = field(repr=False)
    crop_box: tuple[float, float, float, float] = field(repr=False)
    rotation: int
    user_unit: float
    source_resource_fingerprint: str = field(repr=False)
    source_page_aux_fingerprint: str = field(repr=False)
    source_content_fingerprint: str = field(repr=False)
    source_had_xobject_dictionary: bool
    maximum_resource_fingerprint_bytes: int = field(repr=False)
    resource_fingerprint_chunk_bytes: int = field(repr=False)
    overlay_form: _ExpectedOverlayForm | None = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class _RasterAlphaFacts:
    metadata: OverlayRasterAsset
    nonzero_alpha_integral: array[int] = field(repr=False)
    resolvable_alpha_integral: array[int] = field(repr=False)

    @property
    def memory_bytes(self) -> int:
        return (
            len(self.nonzero_alpha_integral) * self.nonzero_alpha_integral.itemsize
            + len(self.resolvable_alpha_integral) * self.resolvable_alpha_integral.itemsize
        )


@dataclass(frozen=True, slots=True)
class _MeasuredOutput:
    byte_size: int
    sha256: str = field(repr=False)


class _CandidateLimitExceeded(OSError):
    pass


class _SemanticPdfLimitExceeded(OSError):
    pass


class _BoundedCandidateStream:
    """Bound every pikepdf write without retaining candidate bytes in memory."""

    __slots__ = ("_extent", "_limit_exceeded", "_maximum", "_stream")

    def __init__(self, stream: BinaryIO, maximum: int) -> None:
        self._stream = stream
        self._maximum = maximum
        self._limit_exceeded = False
        try:
            self._extent = os.fstat(stream.fileno()).st_size
            position = stream.tell()
        except OSError:
            raise OSError from None
        if not 0 <= self._extent <= maximum or not 0 <= position <= maximum:
            raise OSError

    @property
    def limit_exceeded(self) -> bool:
        return self._limit_exceeded

    def write(self, data: Buffer, /) -> int:
        try:
            view = memoryview(data).cast("B")
            current = self.tell()
            self._require_extent(current + view.nbytes)
            written = self._stream.write(view)
        except _CandidateLimitExceeded:
            raise
        except Exception as error:
            raise OSError from error
        if type(written) is not int or not 0 <= written <= view.nbytes:
            raise OSError
        self._extent = max(self._extent, current + written)
        return written

    def seek(self, offset: int, whence: int = os.SEEK_SET, /) -> int:
        if type(offset) is not int or type(whence) is not int:
            raise OSError
        if whence == os.SEEK_SET:
            target = offset
        elif whence == os.SEEK_CUR:
            target = self.tell() + offset
        elif whence == os.SEEK_END:
            target = self._extent + offset
        else:
            raise OSError
        if target < 0:
            raise OSError
        self._require_extent(target)
        position = self._stream.seek(offset, whence)
        if type(position) is not int or position != target:
            raise OSError
        return position

    def tell(self) -> int:
        position = self._stream.tell()
        if type(position) is not int or not 0 <= position <= self._maximum:
            raise OSError
        return position

    def truncate(self, size: int | None = None, /) -> int:
        target = self.tell() if size is None else size
        if type(target) is not int or target < 0:
            raise OSError
        self._require_extent(target)
        result = self._stream.truncate(target)
        if type(result) is not int or result != target:
            raise OSError
        self._extent = target
        return result

    def flush(self) -> None:
        self._stream.flush()

    def fileno(self) -> int:
        return self._stream.fileno()

    def readable(self) -> bool:
        return False

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def _require_extent(self, extent: int) -> None:
        if extent <= self._maximum:
            return
        self._limit_exceeded = True
        raise _CandidateLimitExceeded from None


class _BoundedMemoryPdfSink:
    """Seekable in-memory PDF sink with an enforced maximum extent."""

    __slots__ = ("_extent", "_limit_exceeded", "_maximum", "_stream")

    def __init__(self, maximum: int) -> None:
        if type(maximum) is not int or maximum < 1:
            raise ValueError
        self._stream = BytesIO()
        self._maximum = maximum
        self._extent = 0
        self._limit_exceeded = False

    @property
    def limit_exceeded(self) -> bool:
        return self._limit_exceeded

    def write(self, data: Buffer, /) -> int:
        try:
            count = memoryview(data).nbytes
            position = self.tell()
        except Exception:
            raise OSError from None
        self._require_extent(position + count)
        written = self._stream.write(data)
        if type(written) is not int or not 0 <= written <= count:
            raise OSError from None
        self._extent = max(self._extent, position + written)
        return written

    def read(self, size: int = -1, /) -> bytes:
        if type(size) is not int:
            raise OSError
        result = self._stream.read(size)
        if type(result) is not bytes:
            raise OSError
        return result

    def seek(self, offset: int, whence: int = os.SEEK_SET, /) -> int:
        if type(offset) is not int or type(whence) is not int:
            raise OSError
        if whence == os.SEEK_SET:
            target = offset
        elif whence == os.SEEK_CUR:
            target = self.tell() + offset
        elif whence == os.SEEK_END:
            target = self._extent + offset
        else:
            raise OSError
        if target < 0:
            raise OSError
        self._require_extent(target)
        result = self._stream.seek(offset, whence)
        if type(result) is not int or result != target:
            raise OSError
        return result

    def tell(self) -> int:
        result = self._stream.tell()
        if type(result) is not int or not 0 <= result <= self._maximum:
            raise OSError
        return result

    def truncate(self, size: int | None = None, /) -> int:
        target = self.tell() if size is None else size
        if type(target) is not int or target < 0:
            raise OSError
        self._require_extent(target)
        result = self._stream.truncate(target)
        if type(result) is not int or result != target:
            raise OSError
        self._extent = target
        return result

    def flush(self) -> None:
        self._stream.flush()

    def close(self) -> None:
        self._stream.close()
        self._extent = 0

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def getvalue(self) -> bytes:
        return self._stream.getvalue()

    def _require_extent(self, extent: int) -> None:
        if extent <= self._maximum:
            return
        self._limit_exceeded = True
        raise _SemanticPdfLimitExceeded from None


@dataclass(slots=True, repr=False)
class _DiscardRaster(PdfRasterDestination):
    maximum_bytes: int
    bytes_written: int = 0

    def write(self, data: Buffer, /) -> int:
        try:
            size = memoryview(data).nbytes
        except Exception:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.VERIFICATION_RENDER_FAILED,
                "flattened.render_destination",
            )
        if size < 1 or self.bytes_written > self.maximum_bytes - size:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.render_bytes",
            )
        self.bytes_written += size
        return size

    def flush(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class _SemanticPixelRegion:
    left: int
    top: int
    right: int
    bottom: int

    def __post_init__(self) -> None:
        if (
            type(self.left) is not int
            or type(self.top) is not int
            or type(self.right) is not int
            or type(self.bottom) is not int
            or self.left < 0
            or self.top < 0
            or self.left >= self.right
            or self.top >= self.bottom
        ):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.MODEL_INVALID,
                "flattened.overlay_semantic_region",
            )

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom


@dataclass(frozen=True, slots=True)
class _OverlaySemanticRegion:
    box: OverlayCanvasBox
    paint_required: bool

    def __post_init__(self) -> None:
        if type(self.box) is not OverlayCanvasBox or type(self.paint_required) is not bool:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.MODEL_INVALID,
                "flattened.overlay_semantic_region",
            )


@dataclass(slots=True, repr=False)
class _UniformBackgroundProbe(PdfRasterDestination):
    """Stream RGBA8 pixels and mark request-derived regions that changed."""

    maximum_bytes: int
    expected_pixel: bytes = field(repr=False)
    width_pixels: int
    height_pixels: int
    regions: tuple[_SemanticPixelRegion, ...] = field(repr=False)
    maximum_region_comparisons: int
    control: PdfExportAdapterControl = field(repr=False)
    bytes_written: int = 0
    flushed: bool = False
    region_comparisons: int = 0
    _row_index: int = 0
    _row_buffer: bytearray = field(default_factory=bytearray, repr=False)
    _region_hits: list[bool] = field(init=False, repr=False)
    _pixel_digest: _FingerprintDigest = field(
        default_factory=hashlib.sha256,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        expected_bytes = self.width_pixels * self.height_pixels * 4
        if (
            type(self.maximum_bytes) is not int
            or self.maximum_bytes < 1
            or type(self.expected_pixel) is not bytes
            or len(self.expected_pixel) != 4
            or type(self.width_pixels) is not int
            or self.width_pixels < 1
            or type(self.height_pixels) is not int
            or self.height_pixels < 1
            or expected_bytes > self.maximum_bytes
            or type(self.regions) is not tuple
            or not self.regions
            or any(type(region) is not _SemanticPixelRegion for region in self.regions)
            or any(
                region.right > self.width_pixels or region.bottom > self.height_pixels
                for region in self.regions
            )
            or type(self.maximum_region_comparisons) is not int
            or self.maximum_region_comparisons < 1
            or type(self.control) is not PdfExportAdapterControl
        ):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.MODEL_INVALID,
                "flattened.overlay_semantic_probe",
            )
        self._region_hits = [False] * len(self.regions)

    @property
    def region_hits(self) -> tuple[bool, ...]:
        return tuple(self._region_hits)

    @property
    def pixel_sha256(self) -> str:
        return self._pixel_digest.hexdigest()

    def write(self, data: Buffer, /) -> int:
        try:
            view = memoryview(data).cast("B")
        except Exception:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                "flattened.overlay_semantic_probe",
            )
        size = view.nbytes
        expected_bytes = self.width_pixels * self.height_pixels * 4
        if (
            size < 1
            or self.flushed
            or self.bytes_written > self.maximum_bytes - size
            or self.bytes_written > expected_bytes - size
        ):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.overlay_semantic_bytes",
            )
        row_bytes = self.width_pixels * 4
        self._pixel_digest.update(view)
        offset = 0
        while offset < size:
            take = min(row_bytes - len(self._row_buffer), size - offset)
            self._row_buffer.extend(view[offset : offset + take])
            offset += take
            if len(self._row_buffer) == row_bytes:
                self._process_row()
        self.bytes_written += size
        return size

    def flush(self) -> None:
        if (
            self.flushed
            or self._row_buffer
            or self.bytes_written != self.width_pixels * self.height_pixels * 4
            or self._row_index != self.height_pixels
        ):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                "flattened.overlay_semantic_probe",
            )
        self.flushed = True

    def _process_row(self) -> None:
        _checkpoint(self.control)
        if self._row_index >= self.height_pixels:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.overlay_semantic_bytes",
            )
        expected = self.expected_pixel
        changed_prefix = array("I", [0])
        changed_count = 0
        for offset in range(0, len(self._row_buffer), 4):
            if any(self._row_buffer[offset + index] != expected[index] for index in range(4)):
                changed_count += 1
            changed_prefix.append(changed_count)
        if changed_count:
            for region_index, region in enumerate(self.regions):
                if (
                    self._region_hits[region_index]
                    or not region.top <= self._row_index < region.bottom
                ):
                    continue
                self.region_comparisons += 1
                if self.region_comparisons > self.maximum_region_comparisons:
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                        "flattened.overlay_semantic_comparisons",
                    )
                if changed_prefix[region.right] > changed_prefix[region.left]:
                    self._region_hits[region_index] = True
        self._row_buffer.clear()
        self._row_index += 1


@dataclass(frozen=True, slots=True, repr=False)
class FlattenedPdfExportAdapter:
    """Compose verified overlay Forms and verify one flattened derivative."""

    qpdf_checker: FlattenedPdfStructureChecker = field(
        default_factory=QpdfCommandChecker,
        repr=False,
    )
    renderer: PdfPageRenderer = field(default_factory=PdfiumPageRenderer, repr=False)
    overlay_renderer: OverlayPageRenderer = field(default_factory=FpdfOverlayRenderer, repr=False)
    geometry_resolver: PikePdfPageGeometryResolver = field(
        default_factory=PikePdfPageGeometryResolver,
        repr=False,
    )
    geometry_limits: PdfPageGeometryLimits = field(
        default_factory=lambda: PdfPageGeometryLimits(
            MAX_PDF_PAGE_COORDINATE_MAGNITUDE_UNITS,
            MAX_PDF_PAGE_SPAN_UNITS,
        ),
        repr=False,
    )

    def __post_init__(self) -> None:
        if (
            not callable(getattr(self.qpdf_checker, "check", None))
            or not callable(getattr(self.renderer, "render", None))
            or not callable(getattr(self.overlay_renderer, "render", None))
            or type(self.geometry_resolver) is not PikePdfPageGeometryResolver
            or type(self.geometry_limits) is not PdfPageGeometryLimits
        ):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.MODEL_INVALID,
                "flattened.adapter",
            )

    def assemble_and_verify(
        self,
        plan: FlattenedPdfExportPlan,
        source_streams: Mapping[UUID, BinaryIO],
        raster_sources: Mapping[UUID, OverlayRasterSource],
        output_path: Path,
        limits: PdfExportAdapterLimits,
        overlay_limits: OverlayRenderLimits,
        control: PdfExportAdapterControl,
    ) -> FlattenedPdfExportVerificationReport:
        """Write, reopen, inspect, render, and report one exact candidate."""

        bindings, rasters = _validate_call(
            plan,
            source_streams,
            raster_sources,
            output_path,
            limits,
            overlay_limits,
            control,
        )
        _checkpoint(control)
        _validate_sources(bindings, limits, control)
        maximum_output = _candidate_maximum_bytes(limits)

        try:
            output_stream = output_path.open("x+b")
        except FileExistsError:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.DESTINATION_FAILED,
                "flattened.output_exists",
            )
        except OSError:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.DESTINATION_FAILED,
                "flattened.output_path",
            )

        try:
            with output_stream:
                bounded = _BoundedCandidateStream(output_stream, maximum_output)
                expected_pages = _assemble_candidate(
                    plan,
                    bindings,
                    rasters,
                    bounded,
                    limits.rendering,
                    limits.inspection.maximum_objects,
                    overlay_limits,
                    control,
                    self.overlay_renderer,
                    self.renderer,
                    self.geometry_resolver,
                    self.geometry_limits,
                )
                _flush_candidate(bounded)
                written = _measure_open_candidate(output_stream, maximum_output, control)

            first = _measure_output(output_path, maximum_output, control)
            if first != written:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
                    "flattened.output_changed",
                )
            _verify_candidate_structure(output_path, plan, expected_pages, limits, control)
            _verify_with_qpdf(self.qpdf_checker, output_path, limits, control)
            _verify_rendered_pages(self.renderer, output_path, expected_pages, limits, control)
            _validate_sources(bindings, limits, control)
            final = _measure_output(output_path, maximum_output, control)
            if final != first:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
                    "flattened.output_changed",
                )
            return FlattenedPdfExportVerificationReport.for_verified_plan(
                plan,
                output_sha256=final.sha256,
                output_byte_size=final.byte_size,
            )
        except FlattenedPdfExportError:
            _remove_failed_candidate(output_path)
            raise
        except MemoryError, RecursionError:
            _remove_failed_candidate(output_path)
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.resources",
            )
        except BaseException as error:
            _remove_failed_candidate(output_path)
            if isinstance(error, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                raise
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.INTERNAL_ERROR,
                "flattened.adapter",
            )


def _validate_call(
    plan: FlattenedPdfExportPlan,
    source_streams: Mapping[UUID, BinaryIO],
    raster_sources: Mapping[UUID, OverlayRasterSource],
    output_path: Path,
    limits: PdfExportAdapterLimits,
    overlay_limits: OverlayRenderLimits,
    control: PdfExportAdapterControl,
) -> tuple[tuple[_SourceBinding, ...], dict[UUID, OverlayRasterSource]]:
    if (
        type(plan) is not FlattenedPdfExportPlan
        or not isinstance(source_streams, Mapping)
        or not isinstance(raster_sources, Mapping)
        or type(limits) is not PdfExportAdapterLimits
        or type(overlay_limits) is not OverlayRenderLimits
        or type(control) is not PdfExportAdapterControl
        or not isinstance(output_path, Path)
        or not output_path.is_absolute()
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.MODEL_INVALID,
            "flattened.adapter_input",
        )
    if plan.page_count > limits.inspection.maximum_pages or plan.page_count > (
        limits.rendering.maximum_pages
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.page_count",
        )
    try:
        supplied_source_ids = frozenset(source_streams.keys())
        supplied_raster_ids = frozenset(raster_sources.keys())
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.MODEL_INVALID,
            "flattened.inputs",
        )
    expected_source_ids = frozenset(source.source_id for source in plan.base_plan.sources)
    expected_raster_ids = frozenset(asset.asset_id for asset in plan.assets)
    if supplied_source_ids != expected_source_ids:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.SOURCE_INCONSISTENT,
            "flattened.source_streams",
        )
    if supplied_raster_ids != expected_raster_ids:
        code = (
            FlattenedPdfExportErrorCode.RASTER_METADATA_MISSING
            if supplied_raster_ids < expected_raster_ids
            else FlattenedPdfExportErrorCode.RASTER_METADATA_EXTRA
        )
        reject_flattened_pdf_export(code, "flattened.raster_sources")

    bindings: list[_SourceBinding] = []
    for source in plan.base_plan.sources:
        try:
            stream = source_streams[source.source_id]
            read = stream.read
            seek = stream.seek
            tell = stream.tell
        except Exception:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.SOURCE_INCONSISTENT,
                "flattened.source_stream",
            )
        if not all(callable(item) for item in (read, seek, tell)):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.SOURCE_INCONSISTENT,
                "flattened.source_stream",
            )
        bindings.append(
            _SourceBinding(
                source.source_id,
                stream,
                source.byte_size,
                source.page_count,
                source.sha256,
            )
        )

    captured_rasters: dict[UUID, OverlayRasterSource] = {}
    asset_by_id = {asset.asset_id: asset for asset in plan.assets}
    for asset_id in sorted(expected_raster_ids, key=str):
        try:
            raster = raster_sources[asset_id]
            metadata = raster.metadata
        except Exception:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.RASTER_METADATA_MISSING,
                "flattened.raster_source",
            )
        if metadata != asset_by_id[asset_id].raster_asset:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.RASTER_METADATA_INVALID,
                "flattened.raster_metadata",
            )
        captured_rasters[asset_id] = raster
    return tuple(bindings), captured_rasters


def _checkpoint(control: PdfExportAdapterControl) -> None:
    try:
        if control.cancellation_requested():
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.CANCELLED,
                "flattened.cancelled",
            )
        if control.deadline_expired():
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.TIMED_OUT,
                "flattened.deadline",
            )
    except FlattenedPdfExportError:
        raise
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.INTERNAL_ERROR,
            "flattened.control",
        )


def _validate_sources(
    bindings: tuple[_SourceBinding, ...],
    limits: PdfExportAdapterLimits,
    control: PdfExportAdapterControl,
) -> None:
    total = 0
    for binding in bindings:
        _checkpoint(control)
        measured = _measure_source(binding.stream, limits.inspection.maximum_input_bytes, control)
        if measured.byte_size != binding.byte_size or measured.sha256 != binding.sha256:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.SOURCE_CHANGED,
                "flattened.source_digest",
            )
        total += measured.byte_size
        if total > limits.maximum_total_source_bytes:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.source_total",
            )
        _validate_source_structure(binding, limits, control)


def _measure_source(
    stream: BinaryIO,
    maximum_bytes: int,
    control: PdfExportAdapterControl,
) -> _MeasuredOutput:
    original = _stream_position(stream, "flattened.source_state")
    digest = hashlib.sha256()
    consumed = 0
    operation_error: FlattenedPdfExportError | None = None
    try:
        if stream.seek(0) != 0:
            raise OSError
        while True:
            _checkpoint(control)
            block = stream.read(_HASH_CHUNK_BYTES)
            if type(block) is not bytes or len(block) > _HASH_CHUNK_BYTES:
                raise OSError
            if not block:
                break
            consumed += len(block)
            if consumed > maximum_bytes:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                    "flattened.source_bytes",
                )
            digest.update(block)
    except FlattenedPdfExportError as error:
        operation_error = error
    except Exception:
        operation_error = FlattenedPdfExportError(
            FlattenedPdfExportErrorCode.SOURCE_CHANGED,
            "flattened.source_read",
        )
    if not _restore_stream(stream, original):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.SOURCE_CHANGED,
            "flattened.source_state",
        )
    if operation_error is not None:
        raise operation_error from None
    if consumed < 1:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.SOURCE_CHANGED,
            "flattened.source_bytes",
        )
    return _MeasuredOutput(consumed, digest.hexdigest())


def _validate_source_structure(
    binding: _SourceBinding,
    limits: PdfExportAdapterLimits,
    control: PdfExportAdapterControl,
) -> None:
    original = _stream_position(binding.stream, "flattened.source_state")
    operation_error: FlattenedPdfExportError | None = None
    try:
        binding.stream.seek(0)
        with _strict_open(binding.stream) as document:
            _checkpoint(control)
            if document.is_encrypted:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.SOURCE_ENCRYPTED,
                    "flattened.source_encrypted",
                )
            if len(document.pages) != binding.page_count:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.SOURCE_CHANGED,
                    "flattened.source_pages",
                )
            if len(document.objects) > limits.inspection.maximum_objects:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                    "flattened.source_objects",
                )
    except FlattenedPdfExportError as error:
        operation_error = error
    except pikepdf.PasswordError:
        operation_error = FlattenedPdfExportError(
            FlattenedPdfExportErrorCode.SOURCE_ENCRYPTED,
            "flattened.source_encrypted",
        )
    except Exception:
        operation_error = FlattenedPdfExportError(
            FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
            "flattened.source_open",
        )
    if not _restore_stream(binding.stream, original):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.SOURCE_CHANGED,
            "flattened.source_state",
        )
    if operation_error is not None:
        raise operation_error from None


def _assemble_candidate(
    plan: FlattenedPdfExportPlan,
    bindings: tuple[_SourceBinding, ...],
    rasters: Mapping[UUID, OverlayRasterSource],
    output_stream: _BoundedCandidateStream,
    render_limits: PdfPageRenderLimits,
    maximum_inspection_objects: int,
    overlay_limits: OverlayRenderLimits,
    control: PdfExportAdapterControl,
    overlay_renderer: OverlayPageRenderer,
    page_renderer: PdfPageRenderer,
    geometry_resolver: PikePdfPageGeometryResolver,
    geometry_limits: PdfPageGeometryLimits,
) -> tuple[_ExpectedPage, ...]:
    binding_by_id = {binding.source_id: binding for binding in bindings}
    encoded_assets = tuple(asset.encoded_asset for asset in plan.assets)
    expected: list[_ExpectedPage] = []
    try:
        with pikepdf.Pdf.new() as output:
            for planned_page in plan.pages:
                _checkpoint(control)
                base_page = planned_page.base_page
                binding = binding_by_id[base_page.source_id]
                geometry = _resolve_geometry(
                    binding.stream,
                    base_page.source_page_index,
                    geometry_resolver,
                    geometry_limits,
                    render_limits,
                )
                coordinate_geometry = to_page_coordinate_geometry(geometry)
                overlay_geometry = OverlayPageGeometry.from_pdf_geometry(coordinate_geometry)
                raw_crop = _raw_crop_box(coordinate_geometry, planned_page.active_crop)
                rotation = (
                    int(geometry.intrinsic_rotation) + int(base_page.additional_rotation)
                ) % 360
                copied_page = _copy_source_page(
                    output,
                    binding,
                    base_page.source_page_index,
                    geometry,
                    raw_crop,
                    rotation,
                    maximum_inspection_objects,
                    control,
                )
                reserved_invoked_names: frozenset[pikepdf.Name] = frozenset()
                if planned_page.overlays:
                    _validate_direct_page_content_state(
                        copied_page,
                        maximum_inspection_objects,
                        control,
                    )
                    reserved_invoked_names = _existing_xobject_invocation_names(
                        copied_page,
                        maximum_inspection_objects,
                        overlay_limits.maximum_memory_bytes,
                        overlay_limits.io_chunk_bytes,
                        control,
                    )
                _reject_source_optional_content(
                    copied_page,
                    maximum_objects=maximum_inspection_objects,
                    maximum_decoded_bytes=overlay_limits.maximum_memory_bytes,
                    chunk_bytes=overlay_limits.io_chunk_bytes,
                    control=control,
                )
                copied_resources = copied_page.obj.get(_NAME_RESOURCES)
                if not isinstance(copied_resources, pikepdf.Dictionary):
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                        "flattened.source_resources",
                    )
                source_xobjects = copied_resources.get(_NAME_XOBJECT)
                source_had_xobject_dictionary = isinstance(
                    source_xobjects,
                    pikepdf.Dictionary,
                )
                source_resource_fingerprint = _source_resource_fingerprint(
                    copied_resources,
                    excluded_xobject_name=None,
                    preserve_empty_page_xobjects=source_had_xobject_dictionary,
                    maximum_objects=maximum_inspection_objects,
                    maximum_bytes=overlay_limits.maximum_memory_bytes,
                    chunk_bytes=overlay_limits.io_chunk_bytes,
                    control=control,
                    failure_code=FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                    context="flattened.source_resource_fingerprint",
                )
                source_content_fingerprint = _content_stream_fingerprint(
                    _direct_page_content_streams(
                        copied_page,
                        failure_code=FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
                        context="flattened.source_content",
                    ),
                    overlay_limits.maximum_memory_bytes,
                    overlay_limits.io_chunk_bytes,
                    control,
                    failure_code=FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
                    context="flattened.source_content_fingerprint",
                )
                source_page_aux_fingerprint = _source_page_aux_fingerprint(
                    copied_page.obj,
                    maximum_objects=maximum_inspection_objects,
                    maximum_bytes=overlay_limits.maximum_memory_bytes,
                    chunk_bytes=overlay_limits.io_chunk_bytes,
                    control=control,
                    failure_code=FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                    context="flattened.source_page_fingerprint",
                )

                expected_form: _ExpectedOverlayForm | None = None
                if planned_page.overlays:
                    request = _overlay_request(
                        planned_page,
                        encoded_assets,
                        overlay_geometry,
                    )
                    page_rasters = _page_raster_sources(request, rasters)
                    overlay_bytes, result = _render_overlay_page(
                        overlay_renderer,
                        request,
                        page_rasters,
                        overlay_limits,
                        control,
                    )
                    _validate_overlay_result(request, overlay_bytes, result)
                    _require_overlay_wrapper_determinism(
                        overlay_renderer,
                        request,
                        page_rasters,
                        overlay_limits,
                        control,
                        overlay_bytes,
                        result,
                    )
                    expected_form = _place_overlay_form(
                        output,
                        copied_page,
                        overlay_bytes,
                        result,
                        request,
                        page_rasters,
                        page_renderer,
                        render_limits,
                        overlay_limits,
                        base_page.output_page_index,
                        maximum_inspection_objects,
                        overlay_limits.maximum_memory_bytes,
                        overlay_limits.io_chunk_bytes,
                        reserved_invoked_names,
                        control,
                    )
                expected.append(
                    _ExpectedPage(
                        output_page_index=base_page.output_page_index,
                        media_box=_rectangle(geometry.media_box),
                        crop_box=raw_crop,
                        rotation=rotation,
                        user_unit=geometry.user_unit,
                        source_resource_fingerprint=source_resource_fingerprint,
                        source_page_aux_fingerprint=source_page_aux_fingerprint,
                        source_content_fingerprint=source_content_fingerprint,
                        source_had_xobject_dictionary=source_had_xobject_dictionary,
                        maximum_resource_fingerprint_bytes=overlay_limits.maximum_memory_bytes,
                        resource_fingerprint_chunk_bytes=overlay_limits.io_chunk_bytes,
                        overlay_form=expected_form,
                    )
                )
            if _NAME_METADATA in output.Root:
                del output.Root[_NAME_METADATA]
            _checkpoint(control)
            output.save(
                cast(BinaryIO, output_stream),
                deterministic_id=True,
                encryption=False,
                compress_streams=False,
                stream_decode_level=pikepdf.StreamDecodeLevel.none,
                recompress_flate=False,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
                normalize_content=False,
                linearize=False,
            )
            _checkpoint(control)
    except FlattenedPdfExportError:
        raise
    except _CandidateLimitExceeded:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.output_bytes",
        )
    except OverlayRenderError as error:
        _map_overlay_error(error)
    except PdfRenderError as error:
        _map_pdf_render_error(error, "flattened.geometry")
    except pikepdf.PasswordError:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.SOURCE_ENCRYPTED,
            "flattened.source_encrypted",
        )
    except MemoryError, RecursionError:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.assembly_resources",
        )
    except Exception:
        if output_stream.limit_exceeded:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.output_bytes",
            )
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
            "flattened.assembly",
        )
    return tuple(expected)


def _resolve_geometry(
    stream: BinaryIO,
    page_index: int,
    resolver: PikePdfPageGeometryResolver,
    geometry_limits: PdfPageGeometryLimits,
    render_limits: PdfPageRenderLimits,
) -> PdfResolvedPageGeometry:
    request = PdfPageRenderRequest(
        page_index=page_index,
        maximum_width_pixels=1,
        maximum_height_pixels=1,
        page_box=PdfPageBox.CROP,
        additional_rotation=PdfRenderRotation.DEGREES_0,
    )
    try:
        return resolver.resolve(stream, request, render_limits, geometry_limits)
    except PdfRenderError as error:
        _map_pdf_render_error(error, "flattened.geometry")


def _raw_crop_box(
    geometry: PdfPageCoordinateGeometry,
    crop: NormalizedPageBox,
) -> tuple[float, float, float, float]:
    transform = PageCoordinateTransform(pdf_geometry=geometry)
    points = (
        NormalizedPagePoint(crop.x, crop.y),
        NormalizedPagePoint(crop.right, crop.y),
        NormalizedPagePoint(crop.x, crop.bottom),
        NormalizedPagePoint(crop.right, crop.bottom),
    )
    mapped = [transform.normalized_to_pdf(point) for point in points]
    xs = tuple(point.x for point in mapped)
    ys = tuple(point.y for point in mapped)
    result = (min(xs), min(ys), max(xs), max(ys))
    if not all(math.isfinite(value) for value in result) or not (
        result[0] < result[2] and result[1] < result[3]
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.TRANSFORM_UNSUPPORTED,
            "flattened.crop_geometry",
        )
    return result


def _copy_source_page(
    output: pikepdf.Pdf,
    binding: _SourceBinding,
    source_page_index: int,
    geometry: PdfResolvedPageGeometry,
    crop_box: tuple[float, float, float, float],
    rotation: int,
    maximum_objects: int,
    control: PdfExportAdapterControl,
) -> pikepdf.Page:
    original = _stream_position(binding.stream, "flattened.source_state")
    operation_error: FlattenedPdfExportError | None = None
    copied_page: pikepdf.Page | None = None
    try:
        binding.stream.seek(0)
        with _strict_open(binding.stream) as source:
            if source.is_encrypted or len(source.pages) != binding.page_count:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.SOURCE_CHANGED,
                    "flattened.source_pages",
                )
            page = source.pages[source_page_index]
            resources = _resolve_inherited_page_resources(
                page.obj,
                maximum_objects,
                control,
            )
            page.obj[_NAME_MEDIA_BOX] = pikepdf.Array(_rectangle(geometry.media_box))
            page.obj[_NAME_CROP_BOX] = pikepdf.Array(crop_box)
            page.obj[_NAME_ROTATE] = rotation
            page.obj[_NAME_USER_UNIT] = geometry.user_unit
            page.obj[_NAME_RESOURCES] = _clone_dictionary(
                resources,
                "flattened.source_resources",
            )
            output.pages.append(page)
            copied_page = output.pages[-1]
            copied_page.obj[_NAME_MEDIA_BOX] = pikepdf.Array(_rectangle(geometry.media_box))
            copied_page.obj[_NAME_CROP_BOX] = pikepdf.Array(crop_box)
            copied_page.obj[_NAME_ROTATE] = rotation
            copied_page.obj[_NAME_USER_UNIT] = geometry.user_unit
            for key in (_NAME_LAST_MODIFIED, _NAME_METADATA, _NAME_PIECE_INFO):
                if key in copied_page.obj:
                    del copied_page.obj[key]
    except FlattenedPdfExportError as error:
        operation_error = error
    except Exception:
        operation_error = FlattenedPdfExportError(
            FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
            "flattened.page_copy",
        )
    if not _restore_stream(binding.stream, original):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.SOURCE_CHANGED,
            "flattened.source_state",
        )
    if operation_error is not None:
        raise operation_error from None
    if copied_page is None:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.INTERNAL_ERROR,
            "flattened.page_copy",
        )
    return copied_page


def _resolve_inherited_page_resources(
    page: pikepdf.Dictionary,
    maximum_objects: int,
    control: PdfExportAdapterControl,
) -> pikepdf.Dictionary:
    """Resolve the nearest inheritable Resources without mutating a Pages node."""

    if (
        not isinstance(page, pikepdf.Dictionary)
        or type(maximum_objects) is not int
        or maximum_objects < 1
        or type(control) is not PdfExportAdapterControl
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
            "flattened.source_resources",
        )
    current = page
    seen: set[tuple[int, int]] = set()
    visited = 0
    while True:
        _checkpoint(control)
        visited += 1
        if visited > maximum_objects:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.source_resources",
            )
        try:
            expected_type = "/Page" if visited == 1 else "/Pages"
            if str(current.get(_NAME_TYPE, "")) != expected_type:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
                    "flattened.source_resources",
                )
            if current.is_indirect:
                identity = cast(tuple[int, int], tuple(current.objgen))
                if (
                    len(identity) != 2
                    or any(type(part) is not int for part in identity)
                    or identity in seen
                ):
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
                        "flattened.source_resources",
                    )
                seen.add(identity)
            resources = current.get(_NAME_RESOURCES)
            if resources is not None:
                if not isinstance(resources, pikepdf.Dictionary):
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
                        "flattened.source_resources",
                    )
                return resources
            parent = current.get(_NAME_PARENT)
            if parent is None and expected_type == "/Pages":
                return pikepdf.Dictionary()
            if not isinstance(parent, pikepdf.Dictionary):
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
                    "flattened.source_resources",
                )
            current = parent
        except FlattenedPdfExportError:
            raise
        except MemoryError, RecursionError:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.source_resources",
            )
        except Exception:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
                "flattened.source_resources",
            )


def _rectangle(rectangle: PdfPageRectangle) -> tuple[float, float, float, float]:
    return (
        rectangle.left,
        rectangle.bottom,
        rectangle.right,
        rectangle.top,
    )


def _overlay_request(
    page: FlattenedPdfExportPage,
    assets: tuple[OverlayAssetRecord, ...],
    geometry: OverlayPageGeometry,
) -> OverlayPageRenderRequest:
    collection = OverlayCollection(
        assets,
        page.overlays,
        known_page_ids=(page.page_id,),
        active_page_ids=(page.page_id,),
    )
    return OverlayPageRenderRequest.from_collection(
        collection,
        page.page_id,
        geometry=geometry,
        active_crop=page.active_crop,
    )


def _page_raster_sources(
    request: OverlayPageRenderRequest,
    rasters: Mapping[UUID, OverlayRasterSource],
) -> dict[UUID, OverlayRasterSource]:
    required = {
        payload.asset_id
        for payload in (record.payload for record in request.records)
        if type(payload) is SignatureImageOverlayPayload
    }
    return {asset_id: rasters[asset_id] for asset_id in sorted(required, key=str)}


def _render_overlay_page(
    renderer: OverlayPageRenderer,
    request: OverlayPageRenderRequest,
    raster_sources: Mapping[UUID, OverlayRasterSource],
    limits: OverlayRenderLimits,
    control: PdfExportAdapterControl,
) -> tuple[bytes, OverlayPageRenderResult]:
    destination = BytesIO()
    result: OverlayPageRenderResult | None = None
    try:
        result = renderer.render(request, raster_sources, destination, limits, control)
    except OverlayRenderError as error:
        _map_overlay_error(error)
    except MemoryError, RecursionError:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.overlay_resources",
        )
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
            "flattened.overlay_render",
        )
    if result is None:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.INTERNAL_ERROR,
            "flattened.overlay_render",
        )
    return destination.getvalue(), result


def _validate_overlay_result(
    request: OverlayPageRenderRequest,
    data: bytes,
    result: OverlayPageRenderResult,
) -> None:
    expected_fonts = int(
        any(
            type(record.payload) is TextualOverlayPayload and bool(record.payload.value)
            for record in request.records
        )
    )
    expected_images = sum(
        type(record.payload) is SignatureImageOverlayPayload for record in request.records
    )
    if (
        type(result) is not OverlayPageRenderResult
        or not data
        or result.page_id != request.page_id
        or result.canvas_width_points != request.geometry.canvas_width_points
        or result.canvas_height_points != request.geometry.canvas_height_points
        or result.pdf_placement != request.geometry.pdf_placement
        or result.bytes_written != len(data)
        or result.pdf_sha256 != hashlib.sha256(data).hexdigest()
        or result.embedded_font_count != expected_fonts
        or result.image_count != expected_images
        or len(result.placements) != len(request.records)
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RESULT_INVALID,
            "flattened.overlay_result",
        )
    for record, projection, placement in zip(
        request.records,
        request.projections,
        result.placements,
        strict=True,
    ):
        if (
            placement.overlay_id != record.overlay_id
            or placement.kind is not record.kind
            or placement.z_index != record.z_index
            or placement.requested_box != request.geometry.box_for(record.box)
            or projection.visible_box is None
            or placement.clip_box
            != request.geometry.contained_box_for(
                projection.visible_box,
                within=record.box,
            )
        ):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.RESULT_INVALID,
                "flattened.overlay_placement",
            )


def _require_overlay_wrapper_determinism(
    renderer: OverlayPageRenderer,
    request: OverlayPageRenderRequest,
    raster_sources: Mapping[UUID, OverlayRasterSource],
    limits: OverlayRenderLimits,
    control: PdfExportAdapterControl,
    expected_data: bytes,
    expected_result: OverlayPageRenderResult,
) -> None:
    """Bind the first wrapper result to one independent exact renderer call."""

    destination = BytesIO()
    try:
        reference_result = renderer.render(
            request,
            raster_sources,
            destination,
            limits,
            control,
        )
    except OverlayRenderError as error:
        _map_overlay_error(error)
    except MemoryError, RecursionError:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.overlay_resources",
        )
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
            "flattened.overlay_render",
        )
    reference_data = destination.getvalue()
    _validate_overlay_result(request, reference_data, reference_result)
    if (
        not hmac.compare_digest(reference_data, expected_data)
        or reference_result != expected_result
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RESULT_INVALID,
            "flattened.overlay_determinism",
        )


def _verify_overlay_rendered_semantics(
    renderer: PdfPageRenderer,
    request: OverlayPageRenderRequest,
    result: OverlayPageRenderResult,
    raster_sources: Mapping[UUID, OverlayRasterSource],
    reference_overlay_bytes: bytes,
    imported_form_probe_bytes: bytes,
    render_limits: PdfPageRenderLimits,
    overlay_limits: OverlayRenderLimits,
    control: PdfExportAdapterControl,
) -> None:
    """Prove Form conversion equivalence and required visible paint."""

    effective_regions = _effective_overlay_regions(
        request,
        result,
        raster_sources,
        overlay_limits,
        control,
    )

    request_for_render = PdfPageRenderRequest(
        page_index=0,
        maximum_width_pixels=render_limits.maximum_width_pixels,
        maximum_height_pixels=render_limits.maximum_height_pixels,
        page_box=PdfPageBox.CROP,
        additional_rotation=PdfRenderRotation.DEGREES_0,
    )
    render_geometry = _overlay_semantic_render_geometry(
        result,
        request_for_render,
        render_limits,
    )
    semantic_regions = effective_regions
    if not semantic_regions:
        semantic_regions = (
            _OverlaySemanticRegion(
                OverlayCanvasBox(
                    0.0,
                    0.0,
                    result.canvas_width_points,
                    result.canvas_height_points,
                ),
                False,
            ),
        )
    regions = _semantic_pixel_regions(
        semantic_regions,
        request.geometry.canvas_height_points,
        render_geometry,
    )
    if len(regions) > overlay_limits.maximum_overlays:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.overlay_semantic_regions",
        )
    comparison_limit = render_geometry.layout.height_pixels * len(regions)
    if comparison_limit < 1:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.overlay_semantic_comparisons",
        )
    reference_white_result, reference_white_hits = _render_uniform_background_probe(
        renderer,
        BytesIO(reference_overlay_bytes),
        b"\xff\xff\xff\xff",
        request_for_render,
        render_geometry,
        regions,
        comparison_limit,
        render_limits,
        control,
    )
    form_white_result, form_white_hits = _render_uniform_background_probe(
        renderer,
        BytesIO(imported_form_probe_bytes),
        b"\xff\xff\xff\xff",
        request_for_render,
        render_geometry,
        regions,
        comparison_limit,
        render_limits,
        control,
    )
    reference_black_pdf = _black_background_overlay_pdf(
        reference_overlay_bytes,
        result,
        overlay_limits,
        control,
    )
    reference_black_result, reference_black_hits = _render_uniform_background_probe(
        renderer,
        reference_black_pdf,
        b"\x00\x00\x00\xff",
        request_for_render,
        render_geometry,
        regions,
        comparison_limit,
        render_limits,
        control,
    )
    reference_black_pdf.close()
    del reference_black_pdf
    form_black_pdf = _black_background_overlay_pdf(
        imported_form_probe_bytes,
        result,
        overlay_limits,
        control,
    )
    form_black_result, form_black_hits = _render_uniform_background_probe(
        renderer,
        form_black_pdf,
        b"\x00\x00\x00\xff",
        request_for_render,
        render_geometry,
        regions,
        comparison_limit,
        render_limits,
        control,
    )
    form_black_pdf.close()
    del form_black_pdf
    if (
        reference_white_result != form_white_result
        or reference_black_result != form_black_result
        or reference_white_hits != form_white_hits
        or reference_black_hits != form_black_hits
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
            "flattened.form_render_equivalence",
        )
    if not effective_regions:
        if any(reference_white_hits) or any(reference_black_hits):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                "flattened.overlay_semantics",
            )
        return
    for index, (white_hit, black_hit) in enumerate(
        zip(form_white_hits, form_black_hits, strict=True)
    ):
        semantic_region = effective_regions[index]
        if (
            not white_hit
            and not black_hit
            and semantic_region.paint_required
            and _semantic_region_is_full_raster_resolvable(
                semantic_region.box,
                request.geometry.canvas_height_points,
                render_geometry,
            )
        ):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                "flattened.overlay_semantics",
            )


def _semantic_region_is_full_raster_resolvable(
    region: OverlayCanvasBox,
    canvas_height: float,
    geometry: PdfPageRenderGeometry,
) -> bool:
    try:
        first = geometry.transform.map_point(region.left, canvas_height - region.top)
        second = geometry.transform.map_point(region.right, canvas_height - region.bottom)
        return abs(second[0] - first[0]) >= 1.0 and abs(second[1] - first[1]) >= 1.0
    except PdfRenderError as error:
        _map_pdf_render_error(error, "flattened.overlay_semantics")
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
            "flattened.overlay_semantics",
        )


def _effective_overlay_regions(
    request: OverlayPageRenderRequest,
    result: OverlayPageRenderResult,
    raster_sources: Mapping[UUID, OverlayRasterSource],
    limits: OverlayRenderLimits,
    control: PdfExportAdapterControl,
) -> tuple[_OverlaySemanticRegion, ...]:
    signature_facts: dict[UUID, _RasterAlphaFacts] = {}
    alpha_fact_bytes = 0
    alpha_fact_limit = min(
        limits.maximum_memory_bytes,
        limits.maximum_total_raster_bytes * 8,
    )
    effective_regions: list[_OverlaySemanticRegion] = []
    for record, placement in zip(request.records, result.placements, strict=True):
        _checkpoint(control)
        payload = record.payload
        requested = placement.requested_box
        painted = placement.painted_box
        resolvable_paint = False
        if type(payload) is TextualOverlayPayload:
            no_ink_text = not payload.value or payload.value.isspace()
            if no_ink_text and painted is not None:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.RESULT_INVALID,
                    "flattened.overlay_placement",
                )
            color_alpha = _color_alpha_byte(payload.color.value)
            effective = not no_ink_text and color_alpha > 0
            resolvable_paint = color_alpha == 255
            if painted is not None:
                metrics = placement.text_metrics
                if (
                    not _box_contains(requested, painted)
                    or metrics is None
                    or metrics.glyph_count < 1
                    or metrics.ascent_points + metrics.descent_points <= 0
                    or not requested.top <= metrics.baseline_y_points <= requested.bottom
                ):
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.RESULT_INVALID,
                        "flattened.overlay_placement",
                    )
        elif type(payload) is MarkOverlayPayload:
            color_alpha = _color_alpha_byte(payload.color.value)
            effective = color_alpha > 0
            resolvable_paint = color_alpha == 255
            if effective and painted != requested:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.RESULT_INVALID,
                    "flattened.overlay_placement",
                )
        elif type(payload) is ShapeOverlayPayload:
            fill_alpha = _color_alpha_byte(payload.fill_color.value)
            stroke_alpha = _color_alpha_byte(payload.stroke_color.value)
            relevant_alpha = (
                (stroke_alpha,)
                if payload.shape is ShapePrimitive.LINE
                else (fill_alpha, stroke_alpha)
            )
            effective = any(alpha > 0 for alpha in relevant_alpha)
            resolvable_paint = any(alpha == 255 for alpha in relevant_alpha) or (
                payload.shape is ShapePrimitive.RECTANGLE and fill_alpha >= 2
            )
            if effective and painted != requested:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.RESULT_INVALID,
                    "flattened.overlay_placement",
                )
            if not effective and painted is not None:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.RESULT_INVALID,
                    "flattened.overlay_placement",
                )
        elif type(payload) is SignatureImageOverlayPayload:
            alpha_facts = signature_facts.get(payload.asset_id)
            if alpha_facts is None:
                try:
                    source = raster_sources[payload.asset_id]
                    metadata = source.metadata
                except Exception:
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.RASTER_METADATA_MISSING,
                        "flattened.raster_source",
                    )
                if type(metadata) is not OverlayRasterAsset:
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.RASTER_METADATA_INVALID,
                        "flattened.raster_metadata",
                    )
                alpha_facts = _authenticated_raster_alpha_facts(
                    source,
                    limits,
                    control,
                )
                alpha_fact_bytes += alpha_facts.memory_bytes
                if alpha_fact_bytes > alpha_fact_limit:
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                        "flattened.raster_alpha_facts",
                    )
                signature_facts[payload.asset_id] = alpha_facts
            metadata = alpha_facts.metadata
            if painted != _expected_signature_painted_box(requested, metadata):
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.RESULT_INVALID,
                    "flattened.overlay_placement",
                )
            effective, resolvable_paint = _visible_raster_alpha_state(
                alpha_facts,
                painted,
                placement.clip_box,
                control,
            )
        else:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.RESULT_INVALID,
                "flattened.overlay_placement",
            )
        if effective:
            if painted is None:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.RESULT_INVALID,
                    "flattened.overlay_placement",
                )
            visible_paint = _intersect_canvas_boxes(painted, placement.clip_box)
            if visible_paint is not None:
                effective_regions.append(
                    _OverlaySemanticRegion(
                        visible_paint,
                        resolvable_paint
                        and (
                            type(payload) is SignatureImageOverlayPayload
                            or _box_contains(placement.clip_box, painted)
                        ),
                    )
                )
    return tuple(effective_regions)


def _box_contains(outer: OverlayCanvasBox, inner: OverlayCanvasBox) -> bool:
    return (
        outer.left <= inner.left
        and outer.top <= inner.top
        and inner.right <= outer.right
        and inner.bottom <= outer.bottom
    )


def _intersect_canvas_boxes(
    first: OverlayCanvasBox,
    second: OverlayCanvasBox,
) -> OverlayCanvasBox | None:
    left = max(first.left, second.left)
    top = max(first.top, second.top)
    right = min(first.right, second.right)
    bottom = min(first.bottom, second.bottom)
    if left >= right or top >= bottom:
        return None
    return OverlayCanvasBox(left, top, right, bottom)


def _expected_signature_painted_box(
    requested: OverlayCanvasBox,
    metadata: OverlayRasterAsset,
) -> OverlayCanvasBox:
    scale = min(
        requested.width / metadata.width_pixels,
        requested.height / metadata.height_pixels,
    )
    width = _quantize_overlay_point(metadata.width_pixels * scale)
    height = _quantize_overlay_point(metadata.height_pixels * scale)
    if width <= 0 or height <= 0:
        width = min(requested.width, max(0.01, width))
        height = min(requested.height, max(0.01, height))
        left = _contained_centered_overlay_origin(requested.left, requested.right, width)
        top = _contained_centered_overlay_origin(requested.top, requested.bottom, height)
    else:
        left = _quantize_overlay_point(requested.left + (requested.width - width) / 2)
        top = _quantize_overlay_point(requested.top + (requested.height - height) / 2)
    try:
        return OverlayCanvasBox(left, top, left + width, top + height)
    except OverlayRenderError:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RESULT_INVALID,
            "flattened.overlay_placement",
        )


def _quantize_overlay_point(value: float) -> float:
    try:
        result = round(float(value) + 1e-12, 2)
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RESULT_INVALID,
            "flattened.overlay_placement",
        )
    if not math.isfinite(result):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RESULT_INVALID,
            "flattened.overlay_placement",
        )
    return result


def _contained_centered_overlay_origin(start: float, end: float, extent: float) -> float:
    centered = _quantize_overlay_point(start + ((end - start) - extent) / 2)
    return min(max(centered, start), end - extent)


def _overlay_semantic_render_geometry(
    result: OverlayPageRenderResult,
    request: PdfPageRenderRequest,
    limits: PdfPageRenderLimits,
) -> PdfPageRenderGeometry:
    try:
        page_box = PdfPageRectangle(
            0.0,
            0.0,
            result.canvas_width_points,
            result.canvas_height_points,
        )
        resolved = PdfResolvedPageGeometry(
            media_box=page_box,
            crop_box=page_box,
            effective_box=page_box,
            page_box=PdfPageBox.CROP,
            intrinsic_rotation=PdfRenderRotation.DEGREES_0,
            user_unit=1.0,
        )
        return fit_pdf_page_geometry(resolved, request, limits)
    except PdfRenderError as error:
        _map_pdf_render_error(error, "flattened.overlay_semantics")
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
            "flattened.overlay_semantics",
        )


def _semantic_pixel_regions(
    effective_regions: tuple[_OverlaySemanticRegion, ...],
    canvas_height: float,
    geometry: PdfPageRenderGeometry,
) -> tuple[_SemanticPixelRegion, ...]:
    regions: list[_SemanticPixelRegion] = []
    width = geometry.layout.width_pixels
    height = geometry.layout.height_pixels
    try:
        for semantic_region in effective_regions:
            region = semantic_region.box
            left = region.left
            top = region.top
            right = region.right
            bottom = region.bottom
            first = geometry.transform.map_point(left, canvas_height - top)
            second = geometry.transform.map_point(right, canvas_height - bottom)
            mapped_left = min(first[0], second[0])
            mapped_top = min(first[1], second[1])
            mapped_right = max(first[0], second[0])
            mapped_bottom = max(first[1], second[1])
            pixel_left = max(
                0,
                math.floor(mapped_left) - _SEMANTIC_AA_MARGIN_PIXELS,
            )
            pixel_top = max(
                0,
                math.floor(mapped_top) - _SEMANTIC_AA_MARGIN_PIXELS,
            )
            pixel_right = min(
                width,
                math.ceil(mapped_right) + _SEMANTIC_AA_MARGIN_PIXELS,
            )
            pixel_bottom = min(
                height,
                math.ceil(mapped_bottom) + _SEMANTIC_AA_MARGIN_PIXELS,
            )
            if pixel_left >= pixel_right or pixel_top >= pixel_bottom:
                raise ValueError
            regions.append(
                _SemanticPixelRegion(
                    pixel_left,
                    pixel_top,
                    pixel_right,
                    pixel_bottom,
                )
            )
    except FlattenedPdfExportError:
        raise
    except MemoryError, RecursionError:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.overlay_semantic_regions",
        )
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RESULT_INVALID,
            "flattened.overlay_placement",
        )
    return tuple(regions)


def _color_alpha_byte(value: str) -> int:
    try:
        return int(value[7:9], 16)
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RESULT_INVALID,
            "flattened.overlay_color",
        )


def _authenticated_raster_alpha_facts(
    source: OverlayRasterSource,
    limits: OverlayRenderLimits,
    control: PdfExportAdapterControl,
) -> _RasterAlphaFacts:
    try:
        metadata = source.metadata
        original = source.tell()
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.ASSET_CHANGED,
            "flattened.raster_position",
        )
    if (
        type(original) is not int
        or original < 0
        or metadata.byte_count > limits.maximum_total_raster_bytes
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.raster_bytes",
        )

    consumed = 0
    try:
        pixel_count = metadata.width_pixels * metadata.height_pixels
        if pixel_count > 0xFFFFFFFF:
            raise OverflowError
        integral_stride = metadata.width_pixels + 1
        integral_count = (metadata.height_pixels + 1) * integral_stride
        alpha_fact_limit = min(
            limits.maximum_memory_bytes,
            limits.maximum_total_raster_bytes * 8,
        )
        integral_itemsize = array("I").itemsize
        if integral_count > alpha_fact_limit // (integral_itemsize * 2):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.raster_alpha_facts",
            )
        alpha_integral = (
            array(
                "I",
                [0],
            )
            * integral_count
        )
        resolvable_alpha_integral = (
            array(
                "I",
                [0],
            )
            * integral_count
        )
    except MemoryError, RecursionError:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.raster_alpha_facts",
        )
    digest = hashlib.sha256()
    next_pixel = 0
    operation_error: BaseException | None = None
    try:
        if source.seek(0) != 0:
            raise OSError
        while consumed < metadata.byte_count:
            _checkpoint(control)
            requested = min(limits.io_chunk_bytes, metadata.byte_count - consumed)
            block = source.read(requested)
            if type(block) is not bytes or not 1 <= len(block) <= requested:
                raise OSError
            alpha_start = (3 - consumed) % 4
            for offset in range(alpha_start, len(block), 4):
                pixel_index = (consumed + offset) // 4
                if pixel_index != next_pixel:
                    raise OSError
                row, column = divmod(pixel_index, metadata.width_pixels)
                integral_index = (row + 1) * integral_stride + column + 1
                alpha = block[offset]
                alpha_integral[integral_index] = (
                    int(alpha != 0)
                    + alpha_integral[integral_index - 1]
                    + alpha_integral[integral_index - integral_stride]
                    - alpha_integral[integral_index - integral_stride - 1]
                )
                resolvable_alpha_integral[integral_index] = (
                    int(alpha >= 2)
                    + resolvable_alpha_integral[integral_index - 1]
                    + resolvable_alpha_integral[integral_index - integral_stride]
                    - resolvable_alpha_integral[integral_index - integral_stride - 1]
                )
                next_pixel += 1
            digest.update(block)
            consumed += len(block)
        extra = source.read(1)
        if type(extra) is not bytes or extra:
            raise OSError
    except BaseException as error:
        operation_error = error

    try:
        restored = source.seek(original)
    except Exception:
        restored = None
    if type(restored) is not int or restored != original:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.ASSET_CHANGED,
            "flattened.raster_restore",
        )
    if operation_error is not None:
        if isinstance(operation_error, FlattenedPdfExportError):
            raise operation_error
        if isinstance(operation_error, (MemoryError, RecursionError)):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.raster_bytes",
            )
        if isinstance(operation_error, (KeyboardInterrupt, SystemExit, GeneratorExit)):
            raise operation_error
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.ASSET_CHANGED,
            "flattened.raster_read",
        )
    if (
        consumed != metadata.byte_count
        or next_pixel != pixel_count
        or not hmac.compare_digest(digest.hexdigest(), metadata.pixel_sha256)
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.ASSET_CHANGED,
            "flattened.raster_identity",
        )
    return _RasterAlphaFacts(metadata, alpha_integral, resolvable_alpha_integral)


def _visible_raster_alpha_state(
    facts: _RasterAlphaFacts,
    painted: OverlayCanvasBox,
    clip: OverlayCanvasBox,
    control: PdfExportAdapterControl,
) -> tuple[bool, bool]:
    visible = _intersect_canvas_boxes(painted, clip)
    if visible is None:
        return False, False
    metadata = facts.metadata
    try:
        left = max(
            0,
            math.floor((visible.left - painted.left) * metadata.width_pixels / painted.width),
        )
        top = max(
            0,
            math.floor((visible.top - painted.top) * metadata.height_pixels / painted.height),
        )
        right = min(
            metadata.width_pixels,
            math.ceil((visible.right - painted.left) * metadata.width_pixels / painted.width),
        )
        bottom = min(
            metadata.height_pixels,
            math.ceil((visible.bottom - painted.top) * metadata.height_pixels / painted.height),
        )
        if left >= right or top >= bottom:
            return False, False
        _checkpoint(control)
        stride = metadata.width_pixels + 1
        counts: list[int] = []
        for integral in (
            facts.nonzero_alpha_integral,
            facts.resolvable_alpha_integral,
        ):
            top_left = integral[top * stride + left]
            top_right = integral[top * stride + right]
            bottom_left = integral[bottom * stride + left]
            bottom_right = integral[bottom * stride + right]
            counts.append(bottom_right - top_right - bottom_left + top_left)
        visible_pixels = (right - left) * (bottom - top)
        return counts[0] > 0, counts[1] == visible_pixels
    except FlattenedPdfExportError:
        raise
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.RESULT_INVALID,
            "flattened.raster_alpha_facts",
        )


def _render_uniform_background_probe(
    renderer: PdfPageRenderer,
    source: BytesIO | _BoundedMemoryPdfSink,
    expected_pixel: bytes,
    request: PdfPageRenderRequest,
    geometry: PdfPageRenderGeometry,
    regions: tuple[_SemanticPixelRegion, ...],
    maximum_region_comparisons: int,
    limits: PdfPageRenderLimits,
    control: PdfExportAdapterControl,
) -> tuple[PdfPageRenderResult, tuple[bool, ...]]:
    destination = _UniformBackgroundProbe(
        limits.maximum_destination_bytes,
        expected_pixel,
        geometry.layout.width_pixels,
        geometry.layout.height_pixels,
        regions,
        maximum_region_comparisons,
        control,
    )
    try:
        if source.seek(0) != 0:
            raise OSError
        result = renderer.render(source, destination, request, limits, control)
    except PdfRenderError as error:
        _checkpoint(control)
        _map_pdf_render_error(error, "flattened.overlay_semantics")
    except FlattenedPdfExportError:
        raise
    except MemoryError, RecursionError:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.overlay_semantic_bytes",
        )
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
            "flattened.overlay_semantics",
        )
    if (
        type(result) is not PdfPageRenderResult
        or result.page_index != 0
        or result.page_box is not PdfPageBox.CROP
        or result.additional_rotation is not PdfRenderRotation.DEGREES_0
        or result.intrinsic_rotation is not PdfRenderRotation.DEGREES_0
        or result.width_pixels != geometry.layout.width_pixels
        or result.height_pixels != geometry.layout.height_pixels
        or result.row_stride_bytes != geometry.layout.row_stride_bytes
        or result.bytes_written != destination.bytes_written
        or not hmac.compare_digest(result.pixel_sha256, destination.pixel_sha256)
        or not destination.flushed
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
            "flattened.overlay_semantics",
        )
    return result, destination.region_hits


def _black_background_overlay_pdf(
    overlay_bytes: bytes,
    result: OverlayPageRenderResult,
    limits: OverlayRenderLimits,
    control: PdfExportAdapterControl,
) -> _BoundedMemoryPdfSink:
    """Build a private black-underlay copy within reserved two-copy headroom."""

    probe_maximum = min(
        limits.maximum_output_bytes * 2,
        limits.maximum_memory_bytes,
    )
    sink = _BoundedMemoryPdfSink(probe_maximum)
    try:
        with pikepdf.Pdf.open(
            BytesIO(overlay_bytes),
            password="",
            attempt_recovery=False,
            suppress_warnings=True,
            inherit_page_attributes=False,
        ) as document:
            _checkpoint(control)
            if (
                document.is_encrypted
                or len(document.pages) != 1
                or len(document.objects) > limits.maximum_pdf_objects
            ):
                raise ValueError
            width = result.canvas_width_points
            height = result.canvas_height_points
            background = b" ".join(
                (
                    b"q\n0 0 0 rg\n-1 -1",
                    _canonical_pdf_number(width + 2.0),
                    _canonical_pdf_number(height + 2.0),
                    b"re\nf\nQ\n",
                )
            )
            document.pages[0].contents_add(background, prepend=True)
            document.save(
                cast(Any, sink),
                deterministic_id=True,
                encryption=False,
                compress_streams=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
                normalize_content=False,
                linearize=False,
            )
            _checkpoint(control)
        if sink.seek(0) != 0:
            raise OSError
        return sink
    except FlattenedPdfExportError:
        raise
    except _SemanticPdfLimitExceeded:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.overlay_semantic_pdf_bytes",
        )
    except MemoryError, RecursionError:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.overlay_semantic_pdf_memory",
        )
    except Exception:
        if sink.limit_exceeded:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.overlay_semantic_pdf_bytes",
            )
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
            "flattened.overlay_semantics",
        )


def _imported_form_probe_pdf(
    imported: pikepdf.Stream,
    result: OverlayPageRenderResult,
    expected_fingerprint: str,
    maximum_objects: int,
    maximum_fingerprint_bytes: int,
    fingerprint_chunk_bytes: int,
    limits: OverlayRenderLimits,
    control: PdfExportAdapterControl,
) -> bytes:
    """Invoke the actual imported Form in a bounded verification-only page."""

    probe_maximum = min(limits.maximum_output_bytes * 2, limits.maximum_memory_bytes)
    sink = _BoundedMemoryPdfSink(probe_maximum)
    resource_name = pikepdf.Name("/PWSImportedForm")
    invocation = b"q\n" + str(resource_name).encode("ascii") + b" Do\nQ\n"
    try:
        _require_probe_form_structure(imported, result)
        with pikepdf.Pdf.new() as document:
            page = document.add_blank_page(
                page_size=(
                    result.canvas_width_points,
                    result.canvas_height_points,
                )
            )
            page.obj[_NAME_CROP_BOX] = pikepdf.Array(
                (
                    0.0,
                    0.0,
                    result.canvas_width_points,
                    result.canvas_height_points,
                )
            )
            copied = document.copy_foreign(imported)
            if not isinstance(copied, pikepdf.Stream):
                raise ValueError
            _require_probe_form_structure(copied, result)
            copied_fingerprint = _form_semantic_fingerprint(
                copied,
                maximum_objects,
                maximum_fingerprint_bytes,
                fingerprint_chunk_bytes,
                control,
                failure_code=FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                context="flattened.form_probe_fingerprint",
            )
            if copied_fingerprint != expected_fingerprint:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                    "flattened.form_probe_fingerprint",
                )
            resources = pikepdf.Dictionary()
            xobjects = pikepdf.Dictionary()
            xobjects[resource_name] = copied
            resources[_NAME_XOBJECT] = xobjects
            page.obj[_NAME_RESOURCES] = resources
            page.obj[_NAME_CONTENTS] = document.make_stream(invocation)
            if len(document.objects) > maximum_objects:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                    "flattened.form_probe_objects",
                )
            document.save(
                cast(Any, sink),
                deterministic_id=True,
                encryption=False,
                compress_streams=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
                normalize_content=False,
                linearize=False,
            )
            _checkpoint(control)
        probe_bytes = sink.getvalue()
        sink.close()
        with pikepdf.Pdf.open(
            BytesIO(probe_bytes),
            password="",
            attempt_recovery=False,
            suppress_warnings=True,
            inherit_page_attributes=False,
        ) as reopened:
            if (
                reopened.is_encrypted
                or len(reopened.pages) != 1
                or len(reopened.objects) > maximum_objects
            ):
                raise ValueError
            reopened_page = reopened.pages[0]
            if not _pdf_box_matches(
                reopened_page.obj.get(_NAME_MEDIA_BOX),
                (
                    0.0,
                    0.0,
                    result.canvas_width_points,
                    result.canvas_height_points,
                ),
            ) or not _pdf_box_matches(
                reopened_page.obj.get(_NAME_CROP_BOX),
                (
                    0.0,
                    0.0,
                    result.canvas_width_points,
                    result.canvas_height_points,
                ),
            ):
                raise ValueError
            reopened_form = reopened_page.obj.Resources.XObject[resource_name]
            if not isinstance(reopened_form, pikepdf.Stream):
                raise ValueError
            _require_probe_form_structure(reopened_form, result)
            if (
                _form_semantic_fingerprint(
                    reopened_form,
                    maximum_objects,
                    maximum_fingerprint_bytes,
                    fingerprint_chunk_bytes,
                    control,
                    failure_code=FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                    context="flattened.form_probe_fingerprint",
                )
                != expected_fingerprint
            ):
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                    "flattened.form_probe_fingerprint",
                )
            content_streams = _direct_page_content_streams(
                reopened_page,
                failure_code=FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                context="flattened.form_probe_invocation",
            )
            if len(content_streams) != 1 or content_streams[0].read_bytes() != invocation:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                    "flattened.form_probe_invocation",
                )
            if _content_xobject_invocations(
                reopened_page,
                maximum_objects,
                control,
                failure_code=FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                context="flattened.form_probe_invocation",
            ) != (resource_name,):
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                    "flattened.form_probe_invocation",
                )
        return probe_bytes
    except FlattenedPdfExportError:
        raise
    except _SemanticPdfLimitExceeded:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.form_probe_bytes",
        )
    except MemoryError, RecursionError:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.form_probe_memory",
        )
    except Exception:
        if sink.limit_exceeded:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.form_probe_bytes",
            )
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
            "flattened.form_probe",
        )


def _require_probe_form_structure(
    form: pikepdf.Stream,
    result: OverlayPageRenderResult,
) -> None:
    if (
        str(form.get(_NAME_TYPE, "")) != "/XObject"
        or str(form.get(_NAME_SUBTYPE, "")) != "/Form"
        or _NAME_MATRIX in form
        or not _pdf_box_matches(
            form.get(pikepdf.Name("/BBox")),
            (0.0, 0.0, result.canvas_width_points, result.canvas_height_points),
        )
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
            "flattened.form_probe_structure",
        )


def _pdf_box_matches(
    value: object,
    expected: tuple[float, float, float, float],
) -> bool:
    if not isinstance(value, pikepdf.Array) or len(value) != 4:
        return False
    try:
        actual = tuple(float(item) for item in value)
    except Exception:
        return False
    return all(math.isfinite(item) for item in actual) and actual == expected


def _place_overlay_form(
    output: pikepdf.Pdf,
    page: pikepdf.Page,
    overlay_bytes: bytes,
    result: OverlayPageRenderResult,
    request: OverlayPageRenderRequest,
    raster_sources: Mapping[UUID, OverlayRasterSource],
    page_renderer: PdfPageRenderer,
    render_limits: PdfPageRenderLimits,
    overlay_limits: OverlayRenderLimits,
    output_page_index: int,
    maximum_inspection_objects: int,
    maximum_fingerprint_bytes: int,
    fingerprint_chunk_bytes: int,
    invoked_names: frozenset[pikepdf.Name],
    control: PdfExportAdapterControl,
) -> _ExpectedOverlayForm:
    try:
        source_content_streams = _direct_page_content_streams(
            page,
            failure_code=FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
            context="flattened.source_content",
        )
        source_content_fingerprint = _content_stream_fingerprint(
            source_content_streams,
            maximum_fingerprint_bytes,
            fingerprint_chunk_bytes,
            control,
            failure_code=FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
            context="flattened.source_content_fingerprint",
        )
        with pikepdf.Pdf.open(
            BytesIO(overlay_bytes),
            password="",
            attempt_recovery=False,
            suppress_warnings=True,
            inherit_page_attributes=False,
        ) as overlay_document:
            if overlay_document.is_encrypted or len(overlay_document.pages) != 1:
                raise ValueError
            foreign = overlay_document.pages[0].as_form_xobject(handle_transformations=False)
            if not isinstance(foreign, pikepdf.Stream):
                raise ValueError
            _normalize_imported_form_stream(
                foreign,
                maximum_fingerprint_bytes,
            )
            source_form_fingerprint = _form_semantic_fingerprint(
                foreign,
                maximum_inspection_objects,
                maximum_fingerprint_bytes,
                fingerprint_chunk_bytes,
                control,
                failure_code=FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                context="flattened.form_source_fingerprint",
            )
            imported = output.copy_foreign(foreign)
        if not isinstance(imported, pikepdf.Stream):
            raise ValueError
        _normalize_imported_form_stream(
            imported,
            maximum_fingerprint_bytes,
        )
        semantic_fingerprint = _form_semantic_fingerprint(
            imported,
            maximum_inspection_objects,
            maximum_fingerprint_bytes,
            fingerprint_chunk_bytes,
            control,
            failure_code=FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
            context="flattened.form_fingerprint",
        )
        if semantic_fingerprint != source_form_fingerprint:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                "flattened.form_copy_fingerprint",
            )
        probe_bytes = _imported_form_probe_pdf(
            imported,
            result,
            semantic_fingerprint,
            maximum_inspection_objects,
            maximum_fingerprint_bytes,
            fingerprint_chunk_bytes,
            overlay_limits,
            control,
        )
        _verify_overlay_rendered_semantics(
            page_renderer,
            request,
            result,
            raster_sources,
            overlay_bytes,
            probe_bytes,
            render_limits,
            overlay_limits,
            control,
        )
        resources = _clone_dictionary(page.obj.get(_NAME_RESOURCES), "flattened.resources")
        existing_xobjects = resources.get(_NAME_XOBJECT)
        xobjects = (
            pikepdf.Dictionary()
            if existing_xobjects is None
            else _clone_dictionary(existing_xobjects, "flattened.xobjects")
        )
        resource_name = _available_resource_name(xobjects, invoked_names)
        xobjects[resource_name] = imported
        resources[_NAME_XOBJECT] = xobjects
        page.obj[_NAME_RESOURCES] = resources

        matrix_bytes = pikepdf.Matrix(*_matrix_values(result.pdf_placement)).encode()
        invocation = (
            b"Q\nq\n" + matrix_bytes + b" cm\n" + str(resource_name).encode("ascii") + b" Do\nQ\n"
        )
        page.contents_add(b"q\n", prepend=True)
        page.contents_add(invocation, prepend=False)
    except FlattenedPdfExportError:
        raise
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
            "flattened.form_import",
        )
    return _ExpectedOverlayForm(
        output_page_index=output_page_index,
        resource_name=resource_name,
        invocation=invocation,
        canvas_width_points=result.canvas_width_points,
        canvas_height_points=result.canvas_height_points,
        placement=result.pdf_placement,
        embedded_font_count=result.embedded_font_count,
        image_placement_count=result.image_count,
        source_content_fingerprint=source_content_fingerprint,
        semantic_fingerprint=semantic_fingerprint,
        maximum_fingerprint_bytes=maximum_fingerprint_bytes,
        fingerprint_chunk_bytes=fingerprint_chunk_bytes,
    )


def _clone_dictionary(value: object, context: str) -> pikepdf.Dictionary:
    if not isinstance(value, pikepdf.Dictionary):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
            context,
        )
    clone = pikepdf.Dictionary()
    for key in value.keys():
        clone[key] = value[key]
    return clone


def _available_resource_name(
    resources: pikepdf.Dictionary,
    invoked_names: frozenset[pikepdf.Name],
) -> pikepdf.Name:
    for index in range(_MAXIMUM_RESOURCE_NAME_ATTEMPTS):
        candidate = pikepdf.Name(f"/PWSOverlay{index}")
        if candidate not in resources and candidate not in invoked_names:
            return candidate
    reject_flattened_pdf_export(
        FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
        "flattened.resource_names",
    )


def _validate_direct_page_content_state(
    page: pikepdf.Page,
    maximum_instructions: int,
    control: PdfExportAdapterControl,
) -> None:
    """Require direct page content to end every state scope it opens."""

    _validate_content_stream_state(
        page,
        maximum_instructions,
        control,
        failure_code=FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
        context="flattened.source_content_state",
    )


def _reject_source_optional_content(
    page: pikepdf.Page,
    *,
    maximum_objects: int,
    maximum_decoded_bytes: int,
    chunk_bytes: int,
    control: PdfExportAdapterControl,
) -> None:
    """Reject page-reachable optional content whose Catalog policy cannot be copied."""

    if (
        not isinstance(page, pikepdf.Page)
        or type(maximum_objects) is not int
        or maximum_objects < 1
        or type(maximum_decoded_bytes) is not int
        or maximum_decoded_bytes < 1
        or type(chunk_bytes) is not int
        or chunk_bytes < 1
        or type(control) is not PdfExportAdapterControl
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
            "flattened.source_optional_content",
        )

    decoded_total = 0

    def account_decoded_stream(stream: pikepdf.Stream) -> None:
        nonlocal decoded_total
        try:
            decoded = stream.get_stream_buffer()
            view = memoryview(decoded).cast("B")
        except MemoryError, RecursionError:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.source_optional_content_bytes",
            )
        except Exception:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
                "flattened.source_optional_content",
            )
        decoded_total += view.nbytes
        if decoded_total > maximum_decoded_bytes:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.source_optional_content_bytes",
            )
        for offset in range(0, view.nbytes, chunk_bytes):
            _checkpoint(control)
            memoryview(view[offset : offset + chunk_bytes])

    direct_streams = _direct_page_content_streams(
        page,
        failure_code=FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
        context="flattened.source_optional_content",
    )
    for stream in direct_streams:
        account_decoded_stream(stream)
    _reject_optional_content_operators(page, maximum_objects, control)

    pending: list[tuple[object, bool, bool]] = [(page.obj, False, True)]
    seen_indirect: set[tuple[int, int]] = set()
    parsed_streams: set[tuple[int, int]] = set()
    visited = 0
    while pending:
        _checkpoint(control)
        value, required_content, is_page_root = pending.pop()
        visited += 1
        if visited > maximum_objects:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.source_optional_content_objects",
            )
        try:
            indirect_identity: tuple[int, int] | None = None
            if getattr(value, "is_indirect", False) is True:
                indirect_identity = cast(tuple[int, int], tuple(cast(Any, value).objgen))
                if len(indirect_identity) != 2 or any(
                    type(part) is not int for part in indirect_identity
                ):
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
                        "flattened.source_optional_content",
                    )
                graph_identity: tuple[int, int] | None = indirect_identity
            else:
                graph_identity = None

            if isinstance(value, (pikepdf.Dictionary, pikepdf.Stream)):
                object_type = str(value.get(_NAME_TYPE, ""))
                if object_type in {"/OCG", "/OCMD"} or _NAME_OC in value:
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
                        "flattened.source_optional_content",
                    )

            if isinstance(value, pikepdf.Stream):
                subtype = str(value.get(_NAME_SUBTYPE, ""))
                content_stream = (
                    required_content or subtype == "/Form" or value.get(_NAME_PATTERN_TYPE) == 1
                )
                if content_stream and (
                    graph_identity is None or graph_identity not in parsed_streams
                ):
                    account_decoded_stream(value)
                    _reject_optional_content_operators(value, maximum_objects, control)
                    if graph_identity is not None:
                        parsed_streams.add(graph_identity)

            if indirect_identity is not None:
                if indirect_identity in seen_indirect:
                    continue
                seen_indirect.add(indirect_identity)

            if isinstance(value, pikepdf.Array):
                pending.extend((child, required_content, False) for child in reversed(tuple(value)))
            elif isinstance(value, (pikepdf.Dictionary, pikepdf.Stream)):
                is_type3 = str(value.get(_NAME_SUBTYPE, "")) == "/Type3"
                propagate = required_content and not isinstance(value, pikepdf.Stream)
                keys = tuple(value.keys())
                for key in reversed(keys):
                    if type(key) is not str:
                        reject_flattened_pdf_export(
                            FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
                            "flattened.source_optional_content",
                        )
                    if is_page_root and str(key) in {
                        str(_NAME_CONTENTS),
                        str(_NAME_PARENT),
                    }:
                        continue
                    child_required = (
                        propagate
                        or str(key) == str(_NAME_CONTENTS)
                        or (is_type3 and str(key) == str(_NAME_CHAR_PROCS))
                    )
                    pending.append((value[key], child_required, False))
        except FlattenedPdfExportError:
            raise
        except MemoryError, RecursionError:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.source_optional_content_objects",
            )
        except Exception:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
                "flattened.source_optional_content",
            )


def _reject_optional_content_operators(
    owner: pikepdf.Page | pikepdf.Stream,
    maximum_instructions: int,
    control: PdfExportAdapterControl,
) -> None:
    try:
        with warnings.catch_warnings(record=True) as parse_warnings:
            warnings.simplefilter("always")
            instructions = pikepdf.parse_content_stream(owner, operators="BMC BDC")
        if parse_warnings:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
                "flattened.source_optional_content",
            )
        if len(instructions) > maximum_instructions:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.source_optional_content_instructions",
            )
        for instruction in instructions:
            _checkpoint(control)
            if not isinstance(instruction, pikepdf.ContentStreamInstruction):
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
                    "flattened.source_optional_content",
                )
            operator = str(instruction.operator)
            operands = instruction.operands
            expected_count = 1 if operator == "BMC" else 2
            _require_content_operands(
                operands,
                expected_count,
                FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
                "flattened.source_optional_content",
            )
            if not isinstance(operands[0], pikepdf.Name):
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
                    "flattened.source_optional_content",
                )
            if operands[0] == _NAME_OC:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
                    "flattened.source_optional_content",
                )
    except FlattenedPdfExportError:
        raise
    except MemoryError, RecursionError:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.source_optional_content_instructions",
        )
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
            "flattened.source_optional_content",
        )


def _validate_content_stream_state(
    owner: pikepdf.Page,
    maximum_instructions: int,
    control: PdfExportAdapterControl,
    *,
    failure_code: FlattenedPdfExportErrorCode,
    context: str,
) -> None:
    """Require one concatenated page-content sequence to balance state scopes."""

    graphics_depth = 0
    text_active = False
    marked_content_depth = 0
    compatibility_depth = 0
    try:
        with warnings.catch_warnings(record=True) as parse_warnings:
            warnings.simplefilter("always")
            instructions = pikepdf.parse_content_stream(owner)
        if parse_warnings:
            reject_flattened_pdf_export(
                failure_code,
                context,
            )
        if len(instructions) > maximum_instructions:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.source_content_instructions",
            )
        for instruction in instructions:
            _checkpoint(control)
            if isinstance(instruction, pikepdf.ContentStreamInlineImage):
                continue
            if not isinstance(instruction, pikepdf.ContentStreamInstruction):
                reject_flattened_pdf_export(
                    failure_code,
                    context,
                )
            operator = str(instruction.operator)
            operands = instruction.operands
            if operator == "q":
                _require_content_operands(operands, 0, failure_code, context)
                graphics_depth += 1
            elif operator == "Q":
                _require_content_operands(operands, 0, failure_code, context)
                if graphics_depth == 0:
                    reject_flattened_pdf_export(
                        failure_code,
                        context,
                    )
                graphics_depth -= 1
            elif operator == "BT":
                _require_content_operands(operands, 0, failure_code, context)
                if text_active:
                    reject_flattened_pdf_export(
                        failure_code,
                        context,
                    )
                text_active = True
            elif operator == "ET":
                _require_content_operands(operands, 0, failure_code, context)
                if not text_active:
                    reject_flattened_pdf_export(
                        failure_code,
                        context,
                    )
                text_active = False
            elif operator == "BMC":
                _require_content_operands(operands, 1, failure_code, context)
                if not isinstance(operands[0], pikepdf.Name):
                    reject_flattened_pdf_export(
                        failure_code,
                        context,
                    )
                marked_content_depth += 1
            elif operator == "BDC":
                _require_content_operands(operands, 2, failure_code, context)
                if not isinstance(operands[0], pikepdf.Name) or not isinstance(
                    operands[1], (pikepdf.Name, pikepdf.Dictionary)
                ):
                    reject_flattened_pdf_export(
                        failure_code,
                        context,
                    )
                marked_content_depth += 1
            elif operator == "EMC":
                _require_content_operands(operands, 0, failure_code, context)
                if marked_content_depth == 0:
                    reject_flattened_pdf_export(
                        failure_code,
                        context,
                    )
                marked_content_depth -= 1
            elif operator == "BX":
                _require_content_operands(operands, 0, failure_code, context)
                compatibility_depth += 1
            elif operator == "EX":
                _require_content_operands(operands, 0, failure_code, context)
                if compatibility_depth == 0:
                    reject_flattened_pdf_export(
                        failure_code,
                        context,
                    )
                compatibility_depth -= 1
    except FlattenedPdfExportError:
        raise
    except MemoryError, RecursionError:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.source_content_instructions",
        )
    except Exception:
        reject_flattened_pdf_export(
            failure_code,
            context,
        )
    if graphics_depth or text_active or marked_content_depth or compatibility_depth:
        reject_flattened_pdf_export(
            failure_code,
            context,
        )


def _require_content_operands(
    operands: Any,
    expected_count: int,
    failure_code: FlattenedPdfExportErrorCode,
    context: str,
) -> None:
    try:
        matches = len(operands) == expected_count
    except Exception:
        matches = False
    if not matches:
        reject_flattened_pdf_export(failure_code, context)


def _direct_page_content_streams(
    page: pikepdf.Page,
    *,
    failure_code: FlattenedPdfExportErrorCode,
    context: str,
) -> tuple[pikepdf.Stream, ...]:
    try:
        contents = page.obj.get(_NAME_CONTENTS)
        if contents is None:
            return ()
        if isinstance(contents, pikepdf.Stream):
            return (contents,)
        if isinstance(contents, pikepdf.Array):
            streams = tuple(contents)
            if all(isinstance(value, pikepdf.Stream) for value in streams):
                return cast(tuple[pikepdf.Stream, ...], streams)
    except MemoryError, RecursionError:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.source_content_objects",
        )
    except Exception:
        pass
    reject_flattened_pdf_export(failure_code, context)


def _content_stream_fingerprint(
    streams: tuple[pikepdf.Stream, ...],
    maximum_decoded_bytes: int,
    chunk_bytes: int,
    control: PdfExportAdapterControl,
    *,
    failure_code: FlattenedPdfExportErrorCode,
    context: str,
) -> str:
    """Bind the ordered decoded bytes of a page's direct content streams."""

    if (
        type(maximum_decoded_bytes) is not int
        or maximum_decoded_bytes < 1
        or type(chunk_bytes) is not int
        or chunk_bytes < 1
        or not all(isinstance(stream, pikepdf.Stream) for stream in streams)
    ):
        reject_flattened_pdf_export(failure_code, context)
    digest: _FingerprintDigest = hashlib.sha256()
    digest.update(b"PaperWorkShell direct page content v1\0")
    digest.update(len(streams).to_bytes(8, "big"))
    decoded_total = 0
    try:
        for stream in streams:
            _checkpoint(control)
            decoded = stream.read_bytes()
            if type(decoded) is not bytes:
                reject_flattened_pdf_export(failure_code, context)
            decoded_total += len(decoded)
            if decoded_total > maximum_decoded_bytes:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                    "flattened.source_content_bytes",
                )
            digest.update(len(decoded).to_bytes(8, "big"))
            for offset in range(0, len(decoded), chunk_bytes):
                _checkpoint(control)
                digest.update(decoded[offset : offset + chunk_bytes])
        return digest.hexdigest()
    except FlattenedPdfExportError:
        raise
    except MemoryError, RecursionError:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.source_content_bytes",
        )
    except Exception:
        reject_flattened_pdf_export(failure_code, context)


def _content_stream_page(streams: tuple[pikepdf.Stream, ...]) -> pikepdf.Page:
    try:
        return pikepdf.Page(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Page"),
                Contents=pikepdf.Array(streams),
            )
        )
    except MemoryError, RecursionError:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.source_content_objects",
        )
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.VERIFICATION_OVERLAY_PRESENCE_FAILED,
            "flattened.source_content_state",
        )


def _existing_xobject_invocation_names(
    page: pikepdf.Page,
    maximum_objects: int,
    maximum_decoded_bytes: int,
    chunk_bytes: int,
    control: PdfExportAdapterControl,
) -> frozenset[pikepdf.Name]:
    """Reserve names invoked by every page-reachable content-bearing stream."""

    if (
        type(maximum_objects) is not int
        or maximum_objects < 1
        or type(maximum_decoded_bytes) is not int
        or maximum_decoded_bytes < 1
        or type(chunk_bytes) is not int
        or chunk_bytes < 1
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.MODEL_INVALID,
            "flattened.source_content_limits",
        )

    page_names = _content_xobject_invocations(
        page,
        maximum_objects,
        control,
        failure_code=FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
        context="flattened.source_content",
    )
    invoked: set[pikepdf.Name] = set(page_names)
    visited = 1 + len(page_names)
    if visited > maximum_objects:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.source_content_objects",
        )

    if not isinstance(page.obj.get(_NAME_RESOURCES), pikepdf.Dictionary):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
            "flattened.source_resources",
        )
    pending: list[tuple[object, bool]] = [(page.obj, False)]
    seen_indirect: set[tuple[int, int]] = set()
    decoded_streams: set[tuple[int, int]] = set()
    stream_parse_state: dict[tuple[int, int], bool] = {}
    decoded_total = 0

    while pending:
        _checkpoint(control)
        value, required_content = pending.pop()
        is_stream = isinstance(value, pikepdf.Stream)
        indirect_identity: tuple[int, int] | None = None
        if getattr(value, "is_indirect", False) is True:
            try:
                indirect_identity = cast(tuple[int, int], tuple(cast(Any, value).objgen))
            except Exception:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
                    "flattened.source_content_graph",
                )
            if (
                len(indirect_identity) != 2
                or type(indirect_identity[0]) is not int
                or type(indirect_identity[1]) is not int
            ):
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
                    "flattened.source_content_graph",
                )
            graph_identity: tuple[int, int] | None = indirect_identity
        else:
            graph_identity = None

        if is_stream:
            stream = cast(pikepdf.Stream, value)
            subtype = str(stream.get(_NAME_SUBTYPE, ""))
            pattern_type = stream.get(_NAME_PATTERN_TYPE)
            stream_requires_parse = required_content or subtype == "/Form" or pattern_type == 1
            if stream_requires_parse and (
                graph_identity is None or graph_identity not in decoded_streams
            ):
                try:
                    decoded = stream.read_bytes()
                except MemoryError, RecursionError:
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                        "flattened.source_content_bytes",
                    )
                except Exception:
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
                        "flattened.source_content_decode",
                    )
                if type(decoded) is not bytes:
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
                        "flattened.source_content_decode",
                    )
                decoded_total += len(decoded)
                if decoded_total > maximum_decoded_bytes:
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                        "flattened.source_content_bytes",
                    )
                for _offset in range(0, len(decoded), chunk_bytes):
                    _checkpoint(control)
                if graph_identity is not None:
                    decoded_streams.add(graph_identity)
                parsed, names = _try_content_xobject_invocations(
                    stream,
                    maximum_objects - visited + 1,
                    control,
                    required=stream_requires_parse,
                )
                if graph_identity is not None:
                    stream_parse_state[graph_identity] = parsed
                if parsed:
                    invoked.update(names)
                    visited += len(names)
                    if visited > maximum_objects:
                        reject_flattened_pdf_export(
                            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                            "flattened.source_content_objects",
                        )
            elif (
                stream_requires_parse
                and graph_identity is not None
                and stream_parse_state.get(graph_identity) is not True
            ):
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
                    "flattened.source_content",
                )

        if indirect_identity is not None:
            if indirect_identity in seen_indirect:
                continue
            seen_indirect.add(indirect_identity)

        visited += 1
        if visited > maximum_objects:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.source_content_objects",
            )

        if isinstance(value, pikepdf.Array):
            try:
                pending.extend((child, required_content) for child in reversed(tuple(value)))
            except MemoryError, RecursionError:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                    "flattened.source_content_objects",
                )
            except Exception:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
                    "flattened.source_content_graph",
                )
        elif isinstance(value, (pikepdf.Dictionary, pikepdf.Stream)):
            dictionary = cast(pikepdf.Dictionary, value)
            is_type3_font = str(dictionary.get(_NAME_SUBTYPE, "")) == "/Type3"
            propagate_required_content = required_content and not isinstance(value, pikepdf.Stream)
            try:
                keys = tuple(
                    sorted(
                        (key for key in dictionary.keys() if str(key) != str(_NAME_PARENT)),
                        key=str,
                    )
                )
                for key in reversed(keys):
                    child = dictionary[key]
                    child_is_content = (
                        (is_type3_font and str(key) == str(_NAME_CHAR_PROCS))
                        or str(key) == str(_NAME_CONTENTS)
                        or propagate_required_content
                    )
                    pending.append((child, child_is_content))
            except MemoryError, RecursionError:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                    "flattened.source_content_objects",
                )
            except Exception:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
                    "flattened.source_content_graph",
                )
    return frozenset(invoked)


def _try_content_xobject_invocations(
    owner: pikepdf.Stream,
    maximum_instructions: int,
    control: PdfExportAdapterControl,
    *,
    required: bool,
) -> tuple[bool, tuple[pikepdf.Name, ...]]:
    try:
        with warnings.catch_warnings(record=True) as parse_warnings:
            warnings.simplefilter("always")
            instructions = pikepdf.parse_content_stream(owner, operators="Do")
        if parse_warnings:
            if required:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
                    "flattened.source_content",
                )
            return False, ()
        if len(instructions) > maximum_instructions:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.content_instructions",
            )
        names: list[pikepdf.Name] = []
        for instruction in instructions:
            _checkpoint(control)
            if (
                str(instruction.operator) != "Do"
                or len(instruction.operands) != 1
                or not isinstance(instruction.operands[0], pikepdf.Name)
            ):
                if required:
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
                        "flattened.source_content",
                    )
                return False, ()
            names.append(instruction.operands[0])
        return True, tuple(names)
    except FlattenedPdfExportError:
        raise
    except MemoryError, RecursionError:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.content_instructions",
        )
    except Exception:
        if required:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.SOURCE_UNSUPPORTED,
                "flattened.source_content",
            )
        return False, ()


def _content_xobject_invocations(
    owner: pikepdf.Page | pikepdf.Stream,
    maximum_instructions: int,
    control: PdfExportAdapterControl,
    *,
    failure_code: FlattenedPdfExportErrorCode,
    context: str,
) -> tuple[pikepdf.Name, ...]:
    try:
        with warnings.catch_warnings(record=True) as parse_warnings:
            warnings.simplefilter("always")
            instructions = pikepdf.parse_content_stream(owner, operators="Do")
        if parse_warnings:
            reject_flattened_pdf_export(failure_code, context)
        if len(instructions) > maximum_instructions:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.content_instructions",
            )
        names: list[pikepdf.Name] = []
        for instruction in instructions:
            _checkpoint(control)
            if (
                str(instruction.operator) != "Do"
                or len(instruction.operands) != 1
                or not isinstance(instruction.operands[0], pikepdf.Name)
            ):
                reject_flattened_pdf_export(failure_code, context)
            names.append(instruction.operands[0])
        return tuple(names)
    except FlattenedPdfExportError:
        raise
    except MemoryError, RecursionError:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.content_instructions",
        )
    except Exception:
        reject_flattened_pdf_export(failure_code, context)


def _normalize_imported_form_stream(root: pikepdf.Stream, maximum_bytes: int) -> None:
    """Make the generated root stream's save representation stable before binding it."""

    try:
        if _NAME_FILTER in root:
            return
        decoded = root.read_bytes()
        if type(decoded) is not bytes or not 0 <= len(decoded) <= maximum_bytes:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.form_fingerprint_bytes",
            )
        compressed = zlib.compress(decoded)
        if len(compressed) > maximum_bytes:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.form_fingerprint_bytes",
            )
        root.write(compressed, filter=pikepdf.Name("/FlateDecode"))
        if root.read_bytes() != decoded:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
                "flattened.form_fingerprint",
            )
    except FlattenedPdfExportError:
        raise
    except MemoryError, RecursionError:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.form_fingerprint_bytes",
        )
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.ASSEMBLY_FAILED,
            "flattened.form_fingerprint",
        )


def _form_semantic_fingerprint(
    root: pikepdf.Stream,
    maximum_objects: int,
    maximum_bytes: int,
    chunk_bytes: int,
    control: PdfExportAdapterControl,
    *,
    failure_code: FlattenedPdfExportErrorCode,
    context: str,
) -> str:
    """Hash one complete Form graph without depending on indirect object numbers."""

    if (
        not isinstance(root, pikepdf.Stream)
        or type(maximum_objects) is not int
        or maximum_objects < 1
        or type(maximum_bytes) is not int
        or maximum_bytes < 1
        or type(chunk_bytes) is not int
        or chunk_bytes < 1
    ):
        reject_flattened_pdf_export(failure_code, context)

    digest: _FingerprintDigest = hashlib.sha256()
    pending: list[tuple[str, object]] = [("value", root)]
    seen_indirect: dict[tuple[int, int], int] = {}
    visited = 0
    fingerprinted_bytes = 0

    def emit(tag: bytes, payload: Buffer = b"") -> None:
        nonlocal fingerprinted_bytes
        try:
            view = memoryview(payload).cast("B")
        except Exception:
            reject_flattened_pdf_export(failure_code, context)
        fingerprinted_bytes += view.nbytes
        if fingerprinted_bytes > maximum_bytes:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.form_fingerprint_bytes",
            )
        digest.update(tag)
        digest.update(view.nbytes.to_bytes(8, "big"))
        for offset in range(0, view.nbytes, chunk_bytes):
            _checkpoint(control)
            digest.update(view[offset : offset + chunk_bytes])

    while pending:
        _checkpoint(control)
        operation, payload = pending.pop()
        if operation == "emit":
            tag, emitted_payload = cast(tuple[bytes, bytes], payload)
            emit(tag, emitted_payload)
            continue
        if operation == "stream":
            try:
                stream_bytes = cast(pikepdf.Stream, payload).read_bytes()
            except MemoryError, RecursionError:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                    "flattened.form_fingerprint_bytes",
                )
            except Exception:
                reject_flattened_pdf_export(failure_code, context)
            if type(stream_bytes) is not bytes:
                reject_flattened_pdf_export(failure_code, context)
            emit(b"C", stream_bytes)
            continue
        if operation != "value":
            reject_flattened_pdf_export(failure_code, context)

        graph_value = payload
        visited += 1
        if visited > maximum_objects:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.form_fingerprint_objects",
            )

        try:
            is_indirect = getattr(graph_value, "is_indirect", False) is True
        except Exception:
            reject_flattened_pdf_export(failure_code, context)
        if is_indirect:
            try:
                identity = cast(tuple[int, int], tuple(cast(Any, graph_value).objgen))
            except Exception:
                reject_flattened_pdf_export(failure_code, context)
            if len(identity) != 2 or type(identity[0]) is not int or type(identity[1]) is not int:
                reject_flattened_pdf_export(failure_code, context)
            ordinal = seen_indirect.get(identity)
            if ordinal is not None:
                emit(b"R", ordinal.to_bytes(8, "big"))
                continue
            ordinal = len(seen_indirect)
            seen_indirect[identity] = ordinal
            emit(b"O", ordinal.to_bytes(8, "big"))

        if isinstance(graph_value, pikepdf.Stream):
            items = _fingerprint_dictionary_items(graph_value, failure_code, context)
            pending.append(("emit", (b"s", b"")))
            pending.append(("stream", graph_value))
            for key, child in reversed(items):
                pending.append(("value", child))
                pending.append(("emit", (b"K", key)))
            emit(b"S", len(items).to_bytes(8, "big"))
        elif isinstance(graph_value, pikepdf.Dictionary):
            items = _fingerprint_dictionary_items(graph_value, failure_code, context)
            pending.append(("emit", (b"d", b"")))
            for key, child in reversed(items):
                pending.append(("value", child))
                pending.append(("emit", (b"K", key)))
            emit(b"D", len(items).to_bytes(8, "big"))
        elif isinstance(graph_value, pikepdf.Array):
            try:
                children = tuple(graph_value)
            except Exception:
                reject_flattened_pdf_export(failure_code, context)
            pending.append(("emit", (b"a", b"")))
            pending.extend(("value", child) for child in reversed(children))
            emit(b"A", len(children).to_bytes(8, "big"))
        elif isinstance(graph_value, pikepdf.Name):
            try:
                emit(b"N", bytes(graph_value))
            except Exception:
                reject_flattened_pdf_export(failure_code, context)
        elif isinstance(graph_value, pikepdf.String):
            try:
                emit(b"T", bytes(graph_value))
            except Exception:
                reject_flattened_pdf_export(failure_code, context)
        elif graph_value is None:
            emit(b"Z")
        elif type(graph_value) is bool:
            emit(b"B", b"1" if graph_value else b"0")
        elif type(graph_value) in (int, float, Decimal):
            emit(
                b"#",
                _canonical_pdf_number(cast(int | float | Decimal, graph_value)),
            )
        else:
            reject_flattened_pdf_export(failure_code, context)
    return digest.hexdigest()


def _source_resource_fingerprint(
    root: object,
    *,
    excluded_xobject_name: pikepdf.Name | None,
    preserve_empty_page_xobjects: bool,
    maximum_objects: int,
    maximum_bytes: int,
    chunk_bytes: int,
    control: PdfExportAdapterControl,
    failure_code: FlattenedPdfExportErrorCode,
    context: str,
) -> str:
    if not isinstance(root, pikepdf.Dictionary):
        reject_flattened_pdf_export(failure_code, context)
    return _source_graph_fingerprint(
        root,
        root_role="resource_root",
        root_omitted_keys=frozenset(),
        excluded_xobject_name=excluded_xobject_name,
        preserve_empty_page_xobjects=preserve_empty_page_xobjects,
        maximum_objects=maximum_objects,
        maximum_bytes=maximum_bytes,
        chunk_bytes=chunk_bytes,
        control=control,
        failure_code=failure_code,
        context=context,
    )


def _source_page_aux_fingerprint(
    root: object,
    *,
    maximum_objects: int,
    maximum_bytes: int,
    chunk_bytes: int,
    control: PdfExportAdapterControl,
    failure_code: FlattenedPdfExportErrorCode,
    context: str,
) -> str:
    if not isinstance(root, pikepdf.Dictionary):
        reject_flattened_pdf_export(failure_code, context)
    return _source_graph_fingerprint(
        root,
        root_role="page_root",
        root_omitted_keys=_SOURCE_PAGE_AUX_OMITTED_KEYS,
        excluded_xobject_name=None,
        preserve_empty_page_xobjects=False,
        maximum_objects=maximum_objects,
        maximum_bytes=maximum_bytes,
        chunk_bytes=chunk_bytes,
        control=control,
        failure_code=failure_code,
        context=context,
    )


def _source_graph_fingerprint(
    root: pikepdf.Dictionary,
    *,
    root_role: str,
    root_omitted_keys: frozenset[str],
    excluded_xobject_name: pikepdf.Name | None,
    preserve_empty_page_xobjects: bool,
    maximum_objects: int,
    maximum_bytes: int,
    chunk_bytes: int,
    control: PdfExportAdapterControl,
    failure_code: FlattenedPdfExportErrorCode,
    context: str,
) -> str:
    """Hash copied page state with decoded content and raw opaque stream bytes."""

    if (
        root_role not in {"resource_root", "page_root"}
        or type(root_omitted_keys) is not frozenset
        or type(preserve_empty_page_xobjects) is not bool
        or type(maximum_objects) is not int
        or maximum_objects < 1
        or type(maximum_bytes) is not int
        or maximum_bytes < 1
        or type(chunk_bytes) is not int
        or chunk_bytes < 1
    ):
        reject_flattened_pdf_export(failure_code, context)
    digest: _FingerprintDigest = hashlib.sha256()
    pending: list[tuple[str, object]] = [("value", (root, False, root_role))]
    seen_indirect: dict[tuple[int, int], int] = {}
    root_identity: tuple[int, int] | None = None
    try:
        if root.is_indirect:
            root_identity = cast(tuple[int, int], tuple(root.objgen))
            if len(root_identity) != 2 or any(type(part) is not int for part in root_identity):
                reject_flattened_pdf_export(failure_code, context)
    except FlattenedPdfExportError:
        raise
    except Exception:
        reject_flattened_pdf_export(failure_code, context)
    visited = 0
    fingerprinted_bytes = 0

    def emit(tag: bytes, payload: Buffer = b"") -> None:
        nonlocal fingerprinted_bytes
        try:
            view = memoryview(payload).cast("B")
        except Exception:
            reject_flattened_pdf_export(failure_code, context)
        fingerprinted_bytes += view.nbytes
        if fingerprinted_bytes > maximum_bytes:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                context,
            )
        digest.update(tag)
        digest.update(view.nbytes.to_bytes(8, "big"))
        for offset in range(0, view.nbytes, chunk_bytes):
            _checkpoint(control)
            digest.update(view[offset : offset + chunk_bytes])

    def dictionary_items(
        value: pikepdf.Dictionary | pikepdf.Stream,
        role: str,
        *,
        decoded_stream: bool,
    ) -> tuple[tuple[bytes, object, str], ...]:
        try:
            omitted = {str(_NAME_LENGTH)}
            if role == root_role:
                omitted.update(root_omitted_keys)
            if decoded_stream:
                omitted.update({str(_NAME_FILTER), "/DecodeParms"})
            excluded_name = None if excluded_xobject_name is None else bytes(excluded_xobject_name)
            items: list[tuple[bytes, object, str]] = []
            for key in value.keys():
                if type(key) is not str or str(key) in omitted:
                    if type(key) is not str:
                        reject_flattened_pdf_export(failure_code, context)
                    continue
                encoded_key = bytes(pikepdf.Name(key))
                if role == "page_xobjects" and encoded_key == excluded_name:
                    continue
                if role == "resource_root" and str(key) == str(_NAME_XOBJECT):
                    xobjects = value[key]
                    if not isinstance(xobjects, pikepdf.Dictionary):
                        reject_flattened_pdf_export(failure_code, context)
                    remaining = tuple(
                        xobject_key
                        for xobject_key in xobjects.keys()
                        if excluded_xobject_name is None
                        or bytes(pikepdf.Name(xobject_key)) != excluded_name
                    )
                    if not remaining and not preserve_empty_page_xobjects:
                        continue
                child_role = (
                    "page_xobjects"
                    if role == "resource_root" and str(key) == str(_NAME_XOBJECT)
                    else "nested"
                )
                items.append((encoded_key, value[key], child_role))
            return tuple(sorted(items, key=lambda item: item[0]))
        except FlattenedPdfExportError:
            raise
        except MemoryError, RecursionError:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                context,
            )
        except Exception:
            reject_flattened_pdf_export(failure_code, context)

    while pending:
        _checkpoint(control)
        operation, payload = pending.pop()
        if operation == "emit":
            tag, emitted = cast(tuple[bytes, Buffer], payload)
            emit(tag, emitted)
            continue
        if operation in {"decoded_stream", "raw_stream"}:
            stream = cast(pikepdf.Stream, payload)
            try:
                stream_buffer = (
                    stream.get_stream_buffer()
                    if operation == "decoded_stream"
                    else stream.get_raw_stream_buffer()
                )
            except MemoryError, RecursionError:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                    context,
                )
            except Exception:
                reject_flattened_pdf_export(failure_code, context)
            emit(b"C" if operation == "decoded_stream" else b"R", stream_buffer)
            continue
        if operation != "value":
            reject_flattened_pdf_export(failure_code, context)
        graph_value, required_content, role = cast(tuple[object, bool, str], payload)
        visited += 1
        if visited > maximum_objects:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                context,
            )
        try:
            is_indirect = getattr(graph_value, "is_indirect", False) is True
        except Exception:
            reject_flattened_pdf_export(failure_code, context)
        if is_indirect and role not in {"resource_root", "page_xobjects", "page_root"}:
            try:
                identity = cast(tuple[int, int], tuple(cast(Any, graph_value).objgen))
            except Exception:
                reject_flattened_pdf_export(failure_code, context)
            if len(identity) != 2 or any(type(part) is not int for part in identity):
                reject_flattened_pdf_export(failure_code, context)
            if identity == root_identity:
                emit(b"R")
                continue
            ordinal = seen_indirect.get(identity)
            if ordinal is not None:
                emit(b"r", ordinal.to_bytes(8, "big"))
                continue
            ordinal = len(seen_indirect)
            seen_indirect[identity] = ordinal
            emit(b"o", ordinal.to_bytes(8, "big"))

        if isinstance(graph_value, pikepdf.Stream):
            subtype = str(graph_value.get(_NAME_SUBTYPE, ""))
            decoded_stream = (
                required_content or subtype == "/Form" or graph_value.get(_NAME_PATTERN_TYPE) == 1
            )
            items = dictionary_items(
                graph_value,
                role,
                decoded_stream=decoded_stream,
            )
            pending.append(("emit", (b"s", b"")))
            pending.append(("decoded_stream" if decoded_stream else "raw_stream", graph_value))
            is_type3 = subtype == "/Type3"
            for key, child, child_role in reversed(items):
                child_required = is_type3 and key == bytes(_NAME_CHAR_PROCS)
                pending.append(("value", (child, child_required, child_role)))
                pending.append(("emit", (b"K", key)))
            emit(b"S", len(items).to_bytes(8, "big"))
        elif isinstance(graph_value, pikepdf.Dictionary):
            items = dictionary_items(graph_value, role, decoded_stream=False)
            pending.append(("emit", (b"d", b"")))
            is_type3 = str(graph_value.get(_NAME_SUBTYPE, "")) == "/Type3"
            for key, child, child_role in reversed(items):
                child_required = required_content or (is_type3 and key == bytes(_NAME_CHAR_PROCS))
                pending.append(("value", (child, child_required, child_role)))
                pending.append(("emit", (b"K", key)))
            emit(b"D", len(items).to_bytes(8, "big"))
        elif isinstance(graph_value, pikepdf.Array):
            try:
                children = tuple(graph_value)
            except MemoryError, RecursionError:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                    context,
                )
            except Exception:
                reject_flattened_pdf_export(failure_code, context)
            pending.append(("emit", (b"a", b"")))
            pending.extend(
                ("value", (child, required_content, "nested")) for child in reversed(children)
            )
            emit(b"A", len(children).to_bytes(8, "big"))
        elif isinstance(graph_value, pikepdf.Name):
            emit(b"N", bytes(graph_value))
        elif isinstance(graph_value, pikepdf.String):
            emit(b"T", bytes(graph_value))
        elif graph_value is None:
            emit(b"Z")
        elif type(graph_value) is bool:
            emit(b"B", b"1" if graph_value else b"0")
        elif type(graph_value) in (int, float, Decimal):
            emit(b"#", _canonical_pdf_number(cast(int | float | Decimal, graph_value)))
        else:
            reject_flattened_pdf_export(failure_code, context)
    return digest.hexdigest()


def _fingerprint_dictionary_items(
    value: pikepdf.Dictionary | pikepdf.Stream,
    failure_code: FlattenedPdfExportErrorCode,
    context: str,
) -> tuple[tuple[bytes, object], ...]:
    try:
        keys = tuple(key for key in value.keys() if str(key) != str(_NAME_LENGTH))
        if not all(type(key) is str for key in keys):
            reject_flattened_pdf_export(failure_code, context)
        return tuple(
            sorted(
                (
                    (
                        bytes(pikepdf.Name(key)),
                        value[key],
                    )
                    for key in keys
                ),
                key=lambda item: item[0],
            )
        )
    except FlattenedPdfExportError:
        raise
    except MemoryError, RecursionError:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            "flattened.form_fingerprint_objects",
        )
    except Exception:
        reject_flattened_pdf_export(failure_code, context)


def _canonical_pdf_number(value: int | float | Decimal) -> bytes:
    try:
        number = value if type(value) is Decimal else Decimal(str(value))
        if not number.is_finite():
            raise ValueError
        if number == 0:
            return b"0"
        return format(number.normalize(), "f").encode("ascii")
    except ArithmeticError, UnicodeError, ValueError:
        raise ValueError from None


def _matrix_values(matrix: OverlayPdfPlacementMatrix) -> tuple[float, ...]:
    return (matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f)


def _verify_candidate_structure(
    output_path: Path,
    plan: FlattenedPdfExportPlan,
    expected_pages: tuple[_ExpectedPage, ...],
    limits: PdfExportAdapterLimits,
    control: PdfExportAdapterControl,
) -> None:
    try:
        with output_path.open("rb") as stream:
            with _strict_open(stream) as document:
                _checkpoint(control)
                if document.is_encrypted:
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.VERIFICATION_ENCRYPTION_FAILED,
                        "flattened.output_encrypted",
                    )
                if len(document.pages) != plan.page_count:
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.VERIFICATION_PAGE_COUNT_MISMATCH,
                        "flattened.output_pages",
                    )
                if len(document.objects) > limits.inspection.maximum_objects:
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                        "flattened.output_objects",
                    )
                if _NAME_ACRO_FORM in document.Root or _NAME_OPEN_ACTION in document.Root:
                    _overlay_presence_failure("flattened.output_interactive")
                for expected in expected_pages:
                    _checkpoint(control)
                    page = document.pages[expected.output_page_index]
                    _require_exact_box(page.obj.get(_NAME_MEDIA_BOX), expected.media_box)
                    _require_exact_box(page.obj.get(_NAME_CROP_BOX), expected.crop_box)
                    _require_exact_number(page.obj.get(_NAME_ROTATE), expected.rotation)
                    _require_exact_number(page.obj.get(_NAME_USER_UNIT), expected.user_unit)
                    excluded_name = (
                        None
                        if expected.overlay_form is None
                        else expected.overlay_form.resource_name
                    )
                    if (
                        _source_resource_fingerprint(
                            page.obj.get(_NAME_RESOURCES),
                            excluded_xobject_name=excluded_name,
                            preserve_empty_page_xobjects=(expected.source_had_xobject_dictionary),
                            maximum_objects=limits.inspection.maximum_objects,
                            maximum_bytes=expected.maximum_resource_fingerprint_bytes,
                            chunk_bytes=expected.resource_fingerprint_chunk_bytes,
                            control=control,
                            failure_code=(
                                FlattenedPdfExportErrorCode.VERIFICATION_OVERLAY_PRESENCE_FAILED
                            ),
                            context="flattened.source_resource_fingerprint",
                        )
                        != expected.source_resource_fingerprint
                    ):
                        _overlay_presence_failure("flattened.source_resource_fingerprint")
                    if (
                        _source_page_aux_fingerprint(
                            page.obj,
                            maximum_objects=limits.inspection.maximum_objects,
                            maximum_bytes=expected.maximum_resource_fingerprint_bytes,
                            chunk_bytes=expected.resource_fingerprint_chunk_bytes,
                            control=control,
                            failure_code=(
                                FlattenedPdfExportErrorCode.VERIFICATION_OVERLAY_PRESENCE_FAILED
                            ),
                            context="flattened.source_page_fingerprint",
                        )
                        != expected.source_page_aux_fingerprint
                    ):
                        _overlay_presence_failure("flattened.source_page_fingerprint")
                    if expected.overlay_form is None and (
                        _content_stream_fingerprint(
                            _direct_page_content_streams(
                                page,
                                failure_code=(
                                    FlattenedPdfExportErrorCode.VERIFICATION_OVERLAY_PRESENCE_FAILED
                                ),
                                context="flattened.source_content",
                            ),
                            expected.maximum_resource_fingerprint_bytes,
                            expected.resource_fingerprint_chunk_bytes,
                            control,
                            failure_code=(
                                FlattenedPdfExportErrorCode.VERIFICATION_OVERLAY_PRESENCE_FAILED
                            ),
                            context="flattened.source_content_fingerprint",
                        )
                        != expected.source_content_fingerprint
                    ):
                        _overlay_presence_failure("flattened.source_content_fingerprint")
                    if expected.overlay_form is not None:
                        _verify_overlay_form(page, expected.overlay_form, limits, control)

                def syntax_progress(_percent: int) -> None:
                    _checkpoint(control)

                warnings = document.check_pdf_syntax(progress=syntax_progress)
                if type(warnings) is not list or warnings:
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
                        "flattened.output_syntax",
                    )
    except FlattenedPdfExportError:
        raise
    except pikepdf.PasswordError:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.VERIFICATION_ENCRYPTION_FAILED,
            "flattened.output_encrypted",
        )
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.VERIFICATION_REOPEN_FAILED,
            "flattened.output_reopen",
        )


def _verify_overlay_form(
    page: pikepdf.Page,
    expected: _ExpectedOverlayForm,
    limits: PdfExportAdapterLimits,
    control: PdfExportAdapterControl,
) -> None:
    resources = page.obj.get(_NAME_RESOURCES)
    if not isinstance(resources, pikepdf.Dictionary):
        _overlay_presence_failure("flattened.form_resources")
    xobjects = resources.get(_NAME_XOBJECT)
    if not isinstance(xobjects, pikepdf.Dictionary) or expected.resource_name not in xobjects:
        _overlay_presence_failure("flattened.form_missing")
    form = xobjects[expected.resource_name]
    if not (
        isinstance(form, pikepdf.Stream)
        and str(form.get(_NAME_TYPE, "")) == "/XObject"
        and str(form.get(_NAME_SUBTYPE, "")) == "/Form"
        and _NAME_MATRIX not in form
    ):
        _overlay_presence_failure("flattened.form_structure")
    if (
        _form_semantic_fingerprint(
            form,
            limits.inspection.maximum_objects,
            expected.maximum_fingerprint_bytes,
            expected.fingerprint_chunk_bytes,
            control,
            failure_code=FlattenedPdfExportErrorCode.VERIFICATION_OVERLAY_PRESENCE_FAILED,
            context="flattened.form_fingerprint",
        )
        != expected.semantic_fingerprint
    ):
        _overlay_presence_failure("flattened.form_fingerprint")
    _require_exact_box(
        form.get(pikepdf.Name("/BBox")),
        (0.0, 0.0, expected.canvas_width_points, expected.canvas_height_points),
    )
    streams = _direct_page_content_streams(
        page,
        failure_code=FlattenedPdfExportErrorCode.VERIFICATION_OVERLAY_PRESENCE_FAILED,
        context="flattened.form_invocation",
    )
    if len(streams) < 2:
        _overlay_presence_failure("flattened.form_invocation")
    if streams[0].read_bytes() != b"q\n" or streams[-1].read_bytes() != expected.invocation:
        _overlay_presence_failure("flattened.form_invocation")
    source_streams = streams[1:-1]
    if (
        _content_stream_fingerprint(
            source_streams,
            expected.maximum_fingerprint_bytes,
            expected.fingerprint_chunk_bytes,
            control,
            failure_code=FlattenedPdfExportErrorCode.VERIFICATION_OVERLAY_PRESENCE_FAILED,
            context="flattened.source_content_fingerprint",
        )
        != expected.source_content_fingerprint
    ):
        _overlay_presence_failure("flattened.source_content_fingerprint")
    _validate_content_stream_state(
        _content_stream_page(source_streams),
        limits.inspection.maximum_objects,
        control,
        failure_code=FlattenedPdfExportErrorCode.VERIFICATION_OVERLAY_PRESENCE_FAILED,
        context="flattened.source_content_state",
    )
    invocation_names = _content_xobject_invocations(
        page,
        limits.inspection.maximum_objects,
        control,
        failure_code=FlattenedPdfExportErrorCode.VERIFICATION_OVERLAY_PRESENCE_FAILED,
        context="flattened.form_invocation",
    )
    if invocation_names.count(expected.resource_name) != 1:
        _overlay_presence_failure("flattened.form_invocation")
    form_resources = form.get(_NAME_RESOURCES)
    if not isinstance(form_resources, pikepdf.Dictionary):
        _overlay_presence_failure("flattened.form_resources")
    fonts = form_resources.get(_NAME_FONT, pikepdf.Dictionary())
    if not isinstance(fonts, pikepdf.Dictionary) or len(fonts) != (expected.embedded_font_count):
        _overlay_presence_failure("flattened.form_fonts")
    for key in fonts.keys():
        _verify_embedded_font(fonts[key])
    images = form_resources.get(_NAME_XOBJECT, pikepdf.Dictionary())
    if not isinstance(images, pikepdf.Dictionary):
        _overlay_presence_failure("flattened.form_images")
    if (expected.image_placement_count == 0) != (len(images) == 0):
        _overlay_presence_failure("flattened.form_images")
    for key in images.keys():
        image = images[key]
        if not (
            isinstance(image, pikepdf.Stream)
            and str(image.get(_NAME_TYPE, "")) == "/XObject"
            and str(image.get(_NAME_SUBTYPE, "")) == "/Image"
        ):
            _overlay_presence_failure("flattened.form_images")
    _inspect_form_graph(form, limits.inspection.maximum_objects, control)


def _inspect_form_graph(
    root: object,
    maximum_objects: int,
    control: PdfExportAdapterControl,
) -> None:
    pending = [root]
    seen_indirect: set[tuple[int, int]] = set()
    visited = 0
    while pending:
        _checkpoint(control)
        value = pending.pop()
        if getattr(value, "is_indirect", False) is True:
            identity = tuple(cast(Any, value).objgen)
            if identity in seen_indirect:
                continue
            seen_indirect.add(cast(tuple[int, int], identity))
        visited += 1
        if visited > maximum_objects:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.form_objects",
            )
        if isinstance(value, pikepdf.Array):
            pending.extend(value)
        elif isinstance(value, (pikepdf.Dictionary, pikepdf.Stream)):
            dictionary = cast(pikepdf.Dictionary, value)
            if any(str(key) in _PROHIBITED_FORM_KEYS for key in dictionary.keys()):
                _overlay_presence_failure("flattened.form_interactive")
            pending.extend(dictionary[key] for key in dictionary.keys())


def _verify_embedded_font(value: object) -> None:
    if not isinstance(value, pikepdf.Dictionary):
        _overlay_presence_failure("flattened.form_fonts")
    if str(value.get(_NAME_SUBTYPE, "")) != "/Type0" or pikepdf.Name("/ToUnicode") not in value:
        _overlay_presence_failure("flattened.form_fonts")
    descendants = value.get(pikepdf.Name("/DescendantFonts"))
    if not isinstance(descendants, pikepdf.Array) or len(descendants) != 1:
        _overlay_presence_failure("flattened.form_fonts")
    descendant = descendants[0]
    if (
        not isinstance(descendant, pikepdf.Dictionary)
        or str(descendant.get(_NAME_SUBTYPE, "")) != "/CIDFontType2"
    ):
        _overlay_presence_failure("flattened.form_fonts")
    descriptor = descendant.get(pikepdf.Name("/FontDescriptor"))
    if (
        not isinstance(descriptor, pikepdf.Dictionary)
        or pikepdf.Name("/FontFile2") not in descriptor
    ):
        _overlay_presence_failure("flattened.form_fonts")


def _overlay_presence_failure(context: str) -> Never:
    reject_flattened_pdf_export(
        FlattenedPdfExportErrorCode.VERIFICATION_OVERLAY_PRESENCE_FAILED,
        context,
    )


def _require_exact_box(value: object, expected: tuple[float, float, float, float]) -> None:
    if not isinstance(value, pikepdf.Array) or len(value) != 4:
        _overlay_presence_failure("flattened.page_geometry")
    try:
        actual = tuple(float(item) for item in value)
    except Exception:
        _overlay_presence_failure("flattened.page_geometry")
    if actual != expected:
        _overlay_presence_failure("flattened.page_geometry")


def _require_exact_number(value: object, expected: int | float) -> None:
    if type(value) not in (int, float, Decimal):
        _overlay_presence_failure("flattened.page_geometry")
    try:
        numeric = float(cast(int | float | Decimal, value))
    except TypeError, ValueError, OverflowError:
        _overlay_presence_failure("flattened.page_geometry")
    if not math.isfinite(numeric) or numeric != float(expected):
        _overlay_presence_failure("flattened.page_geometry")


def _verify_with_qpdf(
    checker: FlattenedPdfStructureChecker,
    output_path: Path,
    limits: PdfExportAdapterLimits,
    control: PdfExportAdapterControl,
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
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.CANCELLED,
                "flattened.qpdf",
            )
        if error.code is PdfInspectionErrorCode.TIMED_OUT:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.TIMED_OUT,
                "flattened.qpdf",
            )
        if error.code is PdfInspectionErrorCode.LIMIT_EXCEEDED:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.qpdf",
            )
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
            "flattened.qpdf",
        )
    except FlattenedPdfExportError:
        raise
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
            "flattened.qpdf",
        )
    if state is not PdfStructuralState.CLEAN:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
            "flattened.qpdf",
        )


def _verify_rendered_pages(
    renderer: PdfPageRenderer,
    output_path: Path,
    expected_pages: tuple[_ExpectedPage, ...],
    limits: PdfExportAdapterLimits,
    control: PdfExportAdapterControl,
) -> None:
    for expected in expected_pages:
        _checkpoint(control)
        request = PdfPageRenderRequest(
            page_index=expected.output_page_index,
            maximum_width_pixels=limits.verification_width_pixels,
            maximum_height_pixels=limits.verification_height_pixels,
            page_box=PdfPageBox.CROP,
            additional_rotation=PdfRenderRotation.DEGREES_0,
        )
        destination = _DiscardRaster(limits.rendering.maximum_destination_bytes)
        try:
            with output_path.open("rb") as source:
                result = renderer.render(source, destination, request, limits.rendering, control)
        except PdfRenderError as error:
            _map_pdf_render_error(error, "flattened.render")
        except FlattenedPdfExportError:
            raise
        except Exception:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.VERIFICATION_RENDER_FAILED,
                "flattened.render",
            )
        if (
            type(result) is not PdfPageRenderResult
            or result.page_index != expected.output_page_index
            or result.page_box is not PdfPageBox.CROP
            or result.additional_rotation is not PdfRenderRotation.DEGREES_0
            or int(result.intrinsic_rotation) != expected.rotation
            or destination.bytes_written != result.bytes_written
        ):
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.VERIFICATION_RENDER_FAILED,
                "flattened.render_result",
            )


def _map_overlay_error(error: OverlayRenderError) -> Never:
    mapping = {
        OverlayRenderErrorCode.CANCELLED: FlattenedPdfExportErrorCode.CANCELLED,
        OverlayRenderErrorCode.TIMED_OUT: FlattenedPdfExportErrorCode.TIMED_OUT,
        OverlayRenderErrorCode.LIMIT_EXCEEDED: FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
        OverlayRenderErrorCode.ASSET_CHANGED: FlattenedPdfExportErrorCode.ASSET_CHANGED,
        OverlayRenderErrorCode.ASSET_MISSING: FlattenedPdfExportErrorCode.RASTER_METADATA_MISSING,
    }
    reject_flattened_pdf_export(
        mapping.get(error.code, FlattenedPdfExportErrorCode.ASSEMBLY_FAILED),
        "flattened.overlay_render",
    )


def _map_pdf_render_error(error: PdfRenderError, context: str) -> Never:
    mapping = {
        PdfRenderErrorCode.CANCELLED: FlattenedPdfExportErrorCode.CANCELLED,
        PdfRenderErrorCode.TIMED_OUT: FlattenedPdfExportErrorCode.TIMED_OUT,
        PdfRenderErrorCode.LIMIT_EXCEEDED: FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
    }
    reject_flattened_pdf_export(
        mapping.get(error.code, FlattenedPdfExportErrorCode.VERIFICATION_RENDER_FAILED),
        context,
    )


def _candidate_maximum_bytes(limits: PdfExportAdapterLimits) -> int:
    return min(
        limits.inspection.maximum_output_bytes,
        limits.inspection.maximum_temporary_bytes,
        limits.inspection.maximum_input_bytes,
    )


def _flush_candidate(stream: _BoundedCandidateStream) -> None:
    try:
        stream.flush()
        os.fsync(stream.fileno())
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.DESTINATION_FAILED,
            "flattened.output_flush",
        )


def _measure_output(
    path: Path,
    maximum_bytes: int,
    control: PdfExportAdapterControl,
) -> _MeasuredOutput:
    try:
        before = path.stat()
        if not path.is_file() or not 1 <= before.st_size <= maximum_bytes:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.output_bytes",
            )
        digest = hashlib.sha256()
        consumed = 0
        with path.open("rb") as stream:
            while True:
                _checkpoint(control)
                block = stream.read(_HASH_CHUNK_BYTES)
                if type(block) is not bytes or len(block) > _HASH_CHUNK_BYTES:
                    raise OSError
                if not block:
                    break
                consumed += len(block)
                if consumed > maximum_bytes:
                    reject_flattened_pdf_export(
                        FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                        "flattened.output_bytes",
                    )
                digest.update(block)
            descriptor = os.fstat(stream.fileno())
        after = path.stat()
    except FlattenedPdfExportError:
        raise
    except OSError:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.VERIFICATION_REOPEN_FAILED,
            "flattened.output_read",
        )
    if (
        consumed != before.st_size
        or descriptor.st_size != before.st_size
        or after.st_size != before.st_size
        or descriptor.st_mtime_ns != before.st_mtime_ns
        or after.st_mtime_ns != before.st_mtime_ns
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
            "flattened.output_changed",
        )
    return _MeasuredOutput(consumed, digest.hexdigest())


def _measure_open_candidate(
    stream: BinaryIO,
    maximum_bytes: int,
    control: PdfExportAdapterControl,
) -> _MeasuredOutput:
    """Bind bytes through the still-exclusive descriptor immediately after fsync."""

    try:
        position = stream.tell()
        descriptor_before = os.fstat(stream.fileno())
        if not 1 <= descriptor_before.st_size <= maximum_bytes:
            reject_flattened_pdf_export(
                FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                "flattened.output_bytes",
            )
        if stream.seek(0) != 0:
            raise OSError
        digest = hashlib.sha256()
        consumed = 0
        while True:
            _checkpoint(control)
            block = stream.read(_HASH_CHUNK_BYTES)
            if type(block) is not bytes or len(block) > _HASH_CHUNK_BYTES:
                raise OSError
            if not block:
                break
            consumed += len(block)
            if consumed > maximum_bytes:
                reject_flattened_pdf_export(
                    FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
                    "flattened.output_bytes",
                )
            digest.update(block)
        descriptor_after = os.fstat(stream.fileno())
        if stream.seek(position) != position:
            raise OSError
    except FlattenedPdfExportError:
        raise
    except OSError:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.VERIFICATION_REOPEN_FAILED,
            "flattened.output_read",
        )
    if (
        consumed != descriptor_before.st_size
        or descriptor_after.st_size != descriptor_before.st_size
        or descriptor_after.st_mtime_ns != descriptor_before.st_mtime_ns
    ):
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
            "flattened.output_changed",
        )
    return _MeasuredOutput(consumed, digest.hexdigest())


def _strict_open(stream: BinaryIO) -> pikepdf.Pdf:
    return pikepdf.Pdf.open(
        stream,
        password="",
        attempt_recovery=False,
        suppress_warnings=True,
        inherit_page_attributes=False,
    )


def _stream_position(stream: BinaryIO, context: str) -> int:
    try:
        position = stream.tell()
    except Exception:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.SOURCE_INCONSISTENT,
            context,
        )
    if type(position) is not int or position < 0:
        reject_flattened_pdf_export(
            FlattenedPdfExportErrorCode.SOURCE_INCONSISTENT,
            context,
        )
    return position


def _restore_stream(stream: BinaryIO, position: int) -> bool:
    try:
        return stream.seek(position) == position
    except Exception:
        return False


def _remove_failed_candidate(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return

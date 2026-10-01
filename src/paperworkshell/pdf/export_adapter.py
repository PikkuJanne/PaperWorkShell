"""Worker-neutral pikepdf assembly and PDFium verification for PDF export."""

from __future__ import annotations

import hashlib
import math
import os
import stat
import time
from collections.abc import Buffer, Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Final, Protocol, cast
from uuid import UUID, uuid4

import pikepdf

from paperworkshell.domain.image_pages import (
    ImageNormalizationLimits,
    ImageNormalizationRequest,
    ImagePageError,
    ImagePageErrorCode,
    ImageRgba8Layout,
    ImageSourceFormat,
)
from paperworkshell.domain.pdf_export import (
    PdfExportError,
    PdfExportErrorCode,
    PdfExportPlan,
    PdfExportRotation,
    PdfExportSource,
    PdfExportSourceMediaType,
    PdfExportVerificationReport,
    reject_pdf_export,
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
from paperworkshell.imaging.pillow_decoder import PillowImageDecoder
from paperworkshell.pdf.pdfium_renderer import PdfiumPageRenderer
from paperworkshell.pdf.portal_policy import (
    PORTAL_MINIMUM_OUTPUT_VERSION,
    require_portal_output,
    require_portal_source_version,
)
from paperworkshell.pdf.qpdf_checker import QpdfCommandChecker
from paperworkshell.pdf.raster_pdf_page import RasterPdfPageAdapter, RasterPdfPageLimits
from paperworkshell.pdf.renderer import PdfPageRenderer, PdfRasterDestination

_HASH_CHUNK_BYTES: Final = 1_048_576
_MAXIMUM_INHERITANCE_DEPTH: Final = 64

_NAME_CROP_BOX: Final = pikepdf.Name("/CropBox")
_NAME_MEDIA_BOX: Final = pikepdf.Name("/MediaBox")
_NAME_LAST_MODIFIED: Final = pikepdf.Name("/LastModified")
_NAME_METADATA: Final = pikepdf.Name("/Metadata")
_NAME_PARENT: Final = pikepdf.Name("/Parent")
_NAME_PIECE_INFO: Final = pikepdf.Name("/PieceInfo")
_NAME_RESOURCES: Final = pikepdf.Name("/Resources")
_NAME_ROTATE: Final = pikepdf.Name("/Rotate")

CancellationProbe = Callable[[], bool]
MonotonicClock = Callable[[], float]


def loaded_pikepdf_engine_versions() -> tuple[object, object]:
    """Return only loaded engine identity facts across the PDF adapter seam."""

    return pikepdf.__version__, pikepdf.__libqpdf_version__


def _never_cancel() -> bool:
    return False


class PdfStructureChecker(Protocol):
    """Structural checker used after the candidate has strictly reopened."""

    def check(
        self,
        source_path: Path,
        *,
        limits: PdfInspectionLimits,
        deadline: float,
        cancellation_probe: CancellationProbe,
        monotonic: MonotonicClock,
    ) -> PdfStructuralState: ...


@dataclass(frozen=True, slots=True)
class PdfExportAdapterLimits:
    """Trusted native-operation, candidate, and raster comparison budgets."""

    inspection: PdfInspectionLimits
    rendering: PdfPageRenderLimits
    maximum_total_source_bytes: int
    verification_width_pixels: int = 1_024
    verification_height_pixels: int = 1_024
    image_normalization: ImageNormalizationLimits | None = None
    maximum_image_scratch_bytes: int = 0

    def __post_init__(self) -> None:
        if (
            type(self.inspection) is not PdfInspectionLimits
            or type(self.rendering) is not PdfPageRenderLimits
            or type(self.maximum_total_source_bytes) is not int
            or self.maximum_total_source_bytes < 1
            or type(self.verification_width_pixels) is not int
            or not 1 <= self.verification_width_pixels <= self.rendering.maximum_width_pixels
            or type(self.verification_height_pixels) is not int
            or not 1 <= self.verification_height_pixels <= self.rendering.maximum_height_pixels
            or self.inspection.maximum_pages > self.rendering.maximum_pages
            or type(self.maximum_image_scratch_bytes) is not int
            or self.maximum_image_scratch_bytes < 0
            or (
                self.image_normalization is not None
                and (
                    type(self.image_normalization) is not ImageNormalizationLimits
                    or self.maximum_image_scratch_bytes
                    < self.image_normalization.maximum_output_bytes * 2
                )
            )
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.adapter_limits")


@dataclass(frozen=True, slots=True, repr=False)
class PdfExportAdapterControl:
    """Cooperative worker probes plus the absolute deadline required by qpdf."""

    deadline: float
    cancellation_probe: CancellationProbe = field(default=_never_cancel, repr=False)
    monotonic: MonotonicClock = field(default=time.monotonic, repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.deadline) not in (int, float)
            or not math.isfinite(self.deadline)
            or self.deadline <= 0
            or not callable(self.cancellation_probe)
            or not callable(self.monotonic)
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.adapter_control")

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
    source: PdfExportSource
    path: Path = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class _EffectivePageAttributes:
    media_box: object = field(repr=False)
    crop_box: object = field(repr=False)
    resources: object | None = field(repr=False)
    rotation: int


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


class _CandidateLimitExceeded(OSError):
    """Content-free marker raised by the bounded candidate stream."""

    def __init__(self) -> None:
        super().__init__()


class _BoundedCandidateStream:
    """Seekable output facade that never permits an extent beyond its budget."""

    __slots__ = ("_extent", "_limit_exceeded", "_maximum_bytes", "_stream")

    def __init__(self, stream: BinaryIO, maximum_bytes: int) -> None:
        if type(maximum_bytes) is not int or maximum_bytes < 1:
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.output_limit")
        self._stream = stream
        self._maximum_bytes = maximum_bytes
        self._limit_exceeded = False
        try:
            self._extent = os.fstat(stream.fileno()).st_size
            position = stream.tell()
        except OSError:
            raise OSError from None
        if (
            type(self._extent) is not int
            or not 0 <= self._extent <= maximum_bytes
            or type(position) is not int
            or not 0 <= position <= maximum_bytes
        ):
            raise OSError from None

    @property
    def limit_exceeded(self) -> bool:
        return self._limit_exceeded

    def write(self, data: Buffer, /) -> int:
        try:
            byte_count = memoryview(data).nbytes
            position = self._stream.tell()
        except BufferError, OSError, TypeError, ValueError:
            raise OSError from None
        if (
            type(position) is not int
            or position < 0
            or type(byte_count) is not int
            or byte_count < 0
        ):
            raise OSError from None
        self._require_extent(position + byte_count)
        written = self._stream.write(data)
        if type(written) is not int or not 0 <= written <= byte_count:
            raise OSError from None
        self._extent = max(self._extent, position + written)
        return written

    def seek(self, offset: int, whence: int = os.SEEK_SET, /) -> int:
        if type(offset) is not int or type(whence) is not int:
            raise OSError from None
        try:
            if whence == os.SEEK_SET:
                target = offset
            elif whence == os.SEEK_CUR:
                target = self._stream.tell() + offset
            elif whence == os.SEEK_END:
                target = self._extent + offset
            else:
                raise OSError from None
        except OSError:
            raise OSError from None
        if type(target) is not int or target < 0:
            raise OSError from None
        self._require_extent(target)
        position = self._stream.seek(offset, whence)
        if type(position) is not int or position != target:
            raise OSError from None
        return position

    def tell(self) -> int:
        position = self._stream.tell()
        if type(position) is not int or not 0 <= position <= self._maximum_bytes:
            raise OSError from None
        return position

    def truncate(self, size: int | None = None, /) -> int:
        target = self.tell() if size is None else size
        if type(target) is not int or target < 0:
            raise OSError from None
        self._require_extent(target)
        result = self._stream.truncate(target)
        if type(result) is not int or result != target:
            raise OSError from None
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
        if extent <= self._maximum_bytes:
            return
        self._limit_exceeded = True
        raise _CandidateLimitExceeded from None


@dataclass(frozen=True, slots=True)
class _MeasuredFile:
    byte_size: int
    sha256: str = field(repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class PdfExportAdapter:
    """Create one private candidate and return facts only after closed verification."""

    qpdf_checker: PdfStructureChecker = field(default_factory=QpdfCommandChecker, repr=False)
    renderer: PdfPageRenderer = field(default_factory=PdfiumPageRenderer, repr=False)

    def __post_init__(self) -> None:
        if not callable(getattr(self.qpdf_checker, "check", None)) or not callable(
            getattr(self.renderer, "render", None)
        ):
            reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.adapter")

    def assemble_and_verify(
        self,
        plan: PdfExportPlan,
        source_paths: Mapping[UUID, Path],
        output_path: Path,
        limits: PdfExportAdapterLimits,
        control: PdfExportAdapterControl,
    ) -> PdfExportVerificationReport:
        """Assemble into an exclusively new path, then reopen, inspect, and render it."""

        bindings = _validate_call(plan, source_paths, output_path, limits, control)
        _checkpoint(control)
        _validate_sources(bindings, limits=limits, control=control, portal=plan.profile.is_portal)

        try:
            output_stream = output_path.open("x+b")
        except FileExistsError:
            reject_pdf_export(PdfExportErrorCode.DESTINATION_EXISTS, "export.output_exists")
        except OSError:
            reject_pdf_export(PdfExportErrorCode.DESTINATION_INVALID, "export.output_path")

        try:
            with output_stream:
                bounded_output = _BoundedCandidateStream(
                    output_stream,
                    _candidate_maximum_bytes(limits),
                )
                expected_rotations = _assemble_candidate(
                    plan,
                    bindings,
                    bounded_output,
                    temporary_directory=output_path.parent,
                    limits=limits,
                    control=control,
                )
                _flush_candidate(bounded_output)

            first_output = _measure_output(output_path, limits=limits, control=control)
            _verify_candidate_structure(
                output_path,
                plan=plan,
                expected_rotations=expected_rotations,
                limits=limits,
                control=control,
            )
            _verify_candidate_with_qpdf(
                self.qpdf_checker,
                output_path,
                limits=limits,
                control=control,
            )
            _verify_rendered_pages(
                self.renderer,
                plan,
                bindings,
                output_path,
                limits=limits,
                control=control,
            )
            _validate_sources(
                bindings, limits=limits, control=control, portal=plan.profile.is_portal
            )
            final_output = _measure_output(output_path, limits=limits, control=control)
            if final_output != first_output:
                reject_pdf_export(
                    PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED,
                    "export.output_changed",
                )
            return PdfExportVerificationReport.for_verified_plan(
                plan,
                output_sha256=final_output.sha256,
                output_byte_size=final_output.byte_size,
            )
        except PdfExportError:
            _remove_failed_candidate(output_path)
            raise
        except MemoryError, RecursionError:
            _remove_failed_candidate(output_path)
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.resources")
        except Exception:
            _remove_failed_candidate(output_path)
            reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.adapter")


def _validate_call(
    plan: PdfExportPlan,
    source_paths: Mapping[UUID, Path],
    output_path: Path,
    limits: PdfExportAdapterLimits,
    control: PdfExportAdapterControl,
) -> tuple[_SourceBinding, ...]:
    if (
        type(plan) is not PdfExportPlan
        or not isinstance(source_paths, Mapping)
        or type(limits) is not PdfExportAdapterLimits
        or type(control) is not PdfExportAdapterControl
    ):
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.adapter_input")
    if not isinstance(output_path, Path) or not output_path.is_absolute():
        reject_pdf_export(PdfExportErrorCode.DESTINATION_INVALID, "export.output_path")
    if (
        plan.page_count > limits.inspection.maximum_pages
        or plan.page_count > limits.rendering.maximum_pages
    ):
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.output_pages")
    try:
        supplied_ids = frozenset(source_paths.keys())
    except Exception:
        reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, "export.source_paths")
    expected_ids = frozenset(source.source_id for source in plan.sources)
    if supplied_ids != expected_ids:
        reject_pdf_export(PdfExportErrorCode.SOURCE_INCONSISTENT, "export.source_paths")

    bindings: list[_SourceBinding] = []
    output_key = os.path.normcase(os.path.abspath(os.fspath(output_path)))
    for source in plan.sources:
        try:
            path = source_paths[source.source_id]
        except Exception:
            reject_pdf_export(PdfExportErrorCode.SOURCE_INCONSISTENT, "export.source_paths")
        if not isinstance(path, Path) or not path.is_absolute():
            reject_pdf_export(PdfExportErrorCode.SOURCE_INCONSISTENT, "export.source_path")
        if os.path.normcase(os.path.abspath(os.fspath(path))) == output_key:
            reject_pdf_export(PdfExportErrorCode.DESTINATION_INVALID, "export.path_alias")
        bindings.append(_SourceBinding(source=source, path=path))
    return tuple(bindings)


def _checkpoint(control: PdfExportAdapterControl) -> None:
    if control.cancellation_requested():
        reject_pdf_export(PdfExportErrorCode.CANCELLED, "export.cancelled")
    if control.deadline_expired():
        reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "export.deadline")


def _validate_sources(
    bindings: tuple[_SourceBinding, ...],
    *,
    limits: PdfExportAdapterLimits,
    control: PdfExportAdapterControl,
    portal: bool = False,
) -> None:
    total_bytes = 0
    for binding in bindings:
        _checkpoint(control)
        measured = _measure_source(binding.path, limits=limits, control=control)
        if (
            measured.byte_size != binding.source.byte_size
            or measured.sha256 != binding.source.sha256
        ):
            reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.source_digest")
        total_bytes += measured.byte_size
        if total_bytes > limits.maximum_total_source_bytes:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.source_total")
        _strict_validate_source(binding, limits=limits, control=control, portal=portal)


def _measure_source(
    path: Path,
    *,
    limits: PdfExportAdapterLimits,
    control: PdfExportAdapterControl,
) -> _MeasuredFile:
    try:
        before = path.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_size < 1:
            reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.source_file")
        if before.st_size > limits.inspection.maximum_input_bytes:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.source_bytes")
        digest = hashlib.sha256()
        consumed = 0
        with path.open("rb") as stream:
            while True:
                _checkpoint(control)
                block = stream.read(_HASH_CHUNK_BYTES)
                if type(block) is not bytes:
                    reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.source_read")
                if not block:
                    break
                consumed += len(block)
                if consumed > limits.inspection.maximum_input_bytes:
                    reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.source_bytes")
                digest.update(block)
            descriptor = os.fstat(stream.fileno())
        after = path.stat()
    except PdfExportError:
        raise
    except OSError:
        reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.source_read")
    if (
        consumed != before.st_size
        or descriptor.st_size != before.st_size
        or after.st_size != before.st_size
        or descriptor.st_mtime_ns != before.st_mtime_ns
        or after.st_mtime_ns != before.st_mtime_ns
    ):
        reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.source_changed")
    return _MeasuredFile(byte_size=consumed, sha256=digest.hexdigest())


def _strict_validate_source(
    binding: _SourceBinding,
    *,
    limits: PdfExportAdapterLimits,
    control: PdfExportAdapterControl,
    portal: bool = False,
) -> None:
    if binding.source.media_type is not PdfExportSourceMediaType.PDF:
        if limits.image_normalization is None:
            reject_pdf_export(PdfExportErrorCode.SOURCE_UNSUPPORTED, "export.image_profile")
        return
    try:
        with binding.path.open("rb") as stream:
            with _strict_open(stream) as document:
                _checkpoint(control)
                if portal:
                    require_portal_source_version(document)
                if document.is_encrypted:
                    reject_pdf_export(
                        PdfExportErrorCode.SOURCE_ENCRYPTED, "export.source_encrypted"
                    )
                page_count = len(document.pages)
                object_count = len(document.objects)
                if page_count != binding.source.page_count:
                    reject_pdf_export(PdfExportErrorCode.SOURCE_CHANGED, "export.source_pages")
                if (
                    page_count > limits.inspection.maximum_pages
                    or object_count > limits.inspection.maximum_objects
                ):
                    reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.source_structure")
    except PdfExportError:
        raise
    except pikepdf.PasswordError:
        reject_pdf_export(PdfExportErrorCode.SOURCE_ENCRYPTED, "export.source_encrypted")
    except pikepdf.PdfError, RuntimeError, ValueError, OSError:
        reject_pdf_export(PdfExportErrorCode.SOURCE_UNSUPPORTED, "export.source_open")


def _strict_open(stream: BinaryIO) -> pikepdf.Pdf:
    return pikepdf.Pdf.open(
        stream,
        password="",
        attempt_recovery=False,
        suppress_warnings=True,
        inherit_page_attributes=False,
    )


def _assemble_candidate(
    plan: PdfExportPlan,
    bindings: tuple[_SourceBinding, ...],
    output_stream: _BoundedCandidateStream,
    *,
    temporary_directory: Path,
    limits: PdfExportAdapterLimits,
    control: PdfExportAdapterControl,
) -> tuple[int, ...]:
    binding_by_id = {binding.source.source_id: binding for binding in bindings}
    rotations: list[int] = []
    generated_image_bytes = 0
    try:
        with pikepdf.Pdf.new() as output:
            for planned_page in plan.pages:
                _checkpoint(control)
                binding = binding_by_id[planned_page.source_id]
                with (
                    _page_pdf_path(binding, temporary_directory, limits, control) as page_path,
                    page_path.open("rb") as source_stream,
                ):
                    if binding.source.media_type is not PdfExportSourceMediaType.PDF:
                        # Copying a foreign page retains compressed output objects
                        # until save. Bound their generated PDF representation before
                        # another image is appended, in addition to the one-page
                        # raster/scratch and final candidate stream limits.
                        generated_image_bytes += os.fstat(source_stream.fileno()).st_size
                        if generated_image_bytes > _candidate_maximum_bytes(limits):
                            reject_pdf_export(
                                PdfExportErrorCode.LIMIT_EXCEEDED,
                                "export.image_output_bytes",
                            )
                    with _strict_open(source_stream) as source:
                        if source.is_encrypted:
                            reject_pdf_export(
                                PdfExportErrorCode.SOURCE_ENCRYPTED,
                                "export.source_encrypted",
                            )
                        if len(source.pages) != binding.source.page_count:
                            reject_pdf_export(
                                PdfExportErrorCode.SOURCE_CHANGED, "export.source_pages"
                            )
                        source_page = source.pages[planned_page.source_page_index].obj
                        attributes = _effective_page_attributes(
                            source_page,
                            error_code=PdfExportErrorCode.SOURCE_UNSUPPORTED,
                            context="export.source_geometry",
                        )
                        combined = (
                            attributes.rotation + int(planned_page.additional_rotation)
                        ) % 360
                        _materialize_page(source_page, attributes, combined)
                        output.pages.append(pikepdf.Page(source_page))
                        copied_page = output.pages[-1].obj
                        copied_page[_NAME_ROTATE] = combined
                        for metadata_key in (
                            _NAME_LAST_MODIFIED,
                            _NAME_METADATA,
                            _NAME_PIECE_INFO,
                        ):
                            if metadata_key in copied_page:
                                del copied_page[metadata_key]
                        rotations.append(combined)
            if _NAME_METADATA in output.Root:
                del output.Root[_NAME_METADATA]
            _checkpoint(control)
            output.save(
                cast(BinaryIO, output_stream),
                deterministic_id=True,
                encryption=False,
                compress_streams=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
                normalize_content=False,
                linearize=False,
                min_version=PORTAL_MINIMUM_OUTPUT_VERSION if plan.profile.is_portal else "",
            )
            _checkpoint(control)
    except PdfExportError:
        raise
    except pikepdf.PasswordError:
        reject_pdf_export(PdfExportErrorCode.SOURCE_ENCRYPTED, "export.source_encrypted")
    except MemoryError, RecursionError:
        if output_stream.limit_exceeded:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.output_bytes")
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.resources")
    except Exception:
        # A Python file callback may be wrapped by pikepdf/QPDF.  The stream's
        # content-free sticky flag remains authoritative across that boundary.
        if output_stream.limit_exceeded:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.output_bytes")
        reject_pdf_export(PdfExportErrorCode.ASSEMBLY_FAILED, "export.assembly")
    return tuple(rotations)


def _effective_page_attributes(
    page: pikepdf.Dictionary,
    *,
    error_code: PdfExportErrorCode,
    context: str,
) -> _EffectivePageAttributes:
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
            reject_pdf_export(error_code, context)
        if len(identity) != 2 or identity in seen:
            reject_pdf_export(error_code, context)
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
            reject_pdf_export(error_code, context)
        current = parent
    else:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.inheritance_depth")
    if media_box is None:
        reject_pdf_export(error_code, context)
    rotation = _normalized_rotation(rotate, error_code=error_code, context=context)
    return _EffectivePageAttributes(
        media_box=media_box,
        crop_box=media_box if crop_box is None else crop_box,
        resources=resources,
        rotation=rotation,
    )


def _normalized_rotation(
    value: object | None,
    *,
    error_code: PdfExportErrorCode,
    context: str,
) -> int:
    if value is None:
        return 0
    if type(value) is not int or value % 90 != 0:
        reject_pdf_export(error_code, context)
    return value % 360


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


def _flush_candidate(stream: _BoundedCandidateStream) -> None:
    try:
        stream.flush()
        os.fsync(stream.fileno())
    except OSError:
        reject_pdf_export(PdfExportErrorCode.ASSEMBLY_FAILED, "export.output_flush")


def _measure_output(
    path: Path,
    *,
    limits: PdfExportAdapterLimits,
    control: PdfExportAdapterControl,
) -> _MeasuredFile:
    maximum = _candidate_maximum_bytes(limits)
    try:
        file_size = path.stat().st_size
        if file_size < 1:
            reject_pdf_export(PdfExportErrorCode.ASSEMBLY_FAILED, "export.output_empty")
        if file_size > maximum:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.output_bytes")
        digest = hashlib.sha256()
        consumed = 0
        with path.open("rb") as stream:
            while True:
                _checkpoint(control)
                block = stream.read(_HASH_CHUNK_BYTES)
                if type(block) is not bytes:
                    reject_pdf_export(
                        PdfExportErrorCode.VERIFICATION_REOPEN_FAILED, "export.output_read"
                    )
                if not block:
                    break
                consumed += len(block)
                if consumed > maximum:
                    reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.output_bytes")
                digest.update(block)
    except PdfExportError:
        raise
    except OSError:
        reject_pdf_export(PdfExportErrorCode.VERIFICATION_REOPEN_FAILED, "export.output_read")
    if consumed != file_size:
        reject_pdf_export(PdfExportErrorCode.VERIFICATION_STRUCTURE_FAILED, "export.output_changed")
    return _MeasuredFile(byte_size=consumed, sha256=digest.hexdigest())


def _candidate_maximum_bytes(limits: PdfExportAdapterLimits) -> int:
    return min(
        limits.inspection.maximum_output_bytes,
        limits.inspection.maximum_temporary_bytes,
        limits.inspection.maximum_input_bytes,
    )


def _verify_candidate_structure(
    output_path: Path,
    *,
    plan: PdfExportPlan,
    expected_rotations: tuple[int, ...],
    limits: PdfExportAdapterLimits,
    control: PdfExportAdapterControl,
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
                page_count = len(document.pages)
                if page_count != plan.page_count:
                    reject_pdf_export(
                        PdfExportErrorCode.VERIFICATION_PAGE_COUNT_MISMATCH,
                        "export.output_pages",
                    )
                if (
                    page_count > limits.inspection.maximum_pages
                    or len(document.objects) > limits.inspection.maximum_objects
                ):
                    reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.output_structure")
                if plan.profile.is_portal:
                    require_portal_output(
                        document,
                        maximum_objects=limits.inspection.maximum_objects,
                        checkpoint=lambda: _checkpoint(control),
                    )
                for page_index, expected in enumerate(expected_rotations):
                    _checkpoint(control)
                    page = document.pages[page_index].obj
                    attributes = _effective_page_attributes(
                        page,
                        error_code=PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH,
                        context="export.output_rotation",
                    )
                    direct_rotation = page.get(_NAME_ROTATE)
                    if (
                        attributes.rotation != expected
                        or _normalized_rotation(
                            direct_rotation,
                            error_code=PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH,
                            context="export.output_rotation",
                        )
                        != expected
                    ):
                        reject_pdf_export(
                            PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH,
                            "export.output_rotation",
                        )

                def syntax_progress(_percent: int) -> None:
                    _checkpoint(control)

                warnings = document.check_pdf_syntax(progress=syntax_progress)
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


def _verify_candidate_with_qpdf(
    checker: PdfStructureChecker,
    output_path: Path,
    *,
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


def _verify_rendered_pages(
    renderer: PdfPageRenderer,
    plan: PdfExportPlan,
    bindings: tuple[_SourceBinding, ...],
    output_path: Path,
    *,
    limits: PdfExportAdapterLimits,
    control: PdfExportAdapterControl,
) -> None:
    binding_by_id = {binding.source.source_id: binding for binding in bindings}
    for planned_page in plan.pages:
        _checkpoint(control)
        binding = binding_by_id[planned_page.source_id]
        with _page_pdf_path(binding, output_path.parent, limits, control) as page_path:
            source_result = _render_one(
                renderer,
                page_path,
                page_index=planned_page.source_page_index,
                rotation=_render_rotation(planned_page.additional_rotation),
                limits=limits,
                control=control,
            )
        output_result = _render_one(
            renderer,
            output_path,
            page_index=planned_page.output_page_index,
            rotation=PdfRenderRotation.DEGREES_0,
            limits=limits,
            control=control,
        )
        if not _same_rendered_page(source_result, output_result):
            reject_pdf_export(
                PdfExportErrorCode.VERIFICATION_SEMANTIC_MISMATCH,
                "export.render_mismatch",
            )


@contextmanager
def _page_pdf_path(
    binding: _SourceBinding,
    temporary_directory: Path,
    limits: PdfExportAdapterLimits,
    control: PdfExportAdapterControl,
) -> Iterator[Path]:
    """Build and discard one worker-private image page, never invoking OCR.

    A fresh second normalization supplies the independent source-side render for
    final verification. No full-resolution packet or image cache is retained.
    """

    if binding.source.media_type is PdfExportSourceMediaType.PDF:
        yield binding.path
        return
    image_limits = limits.image_normalization
    if image_limits is None:
        reject_pdf_export(PdfExportErrorCode.SOURCE_UNSUPPORTED, "export.image_profile")
    raw_path = temporary_directory / f"{uuid4().hex}.rgba"
    page_path = temporary_directory / f"{uuid4().hex}.pdf"
    raw_owned = False
    page_owned = False
    source_format = (
        ImageSourceFormat.JPEG
        if binding.source.media_type is PdfExportSourceMediaType.JPEG
        else ImageSourceFormat.PNG
    )
    request = ImageNormalizationRequest(
        expected_source_format=source_format,
        expected_source_bytes=binding.source.byte_size,
        expected_source_sha256=binding.source.sha256,
        maximum_width_pixels=image_limits.maximum_output_width_pixels,
        maximum_height_pixels=image_limits.maximum_output_height_pixels,
    )
    try:
        _checkpoint(control)
        with binding.path.open("rb") as source, raw_path.open("x+b") as destination:
            raw_owned = True
            result = PillowImageDecoder().normalize(
                source,
                destination,
                expected_format=source_format,
                request=request,
                limits=image_limits,
                control=control,
            )
            destination.flush()
        layout = ImageRgba8Layout(
            result.width_pixels,
            result.height_pixels,
            result.row_stride_bytes,
            result.width_pixels * result.height_pixels,
            result.bytes_written,
        )
        # Capacity was reserved against raw bytes plus the maximum page-PDF bytes
        # before either is produced. RasterPdfPageAdapter independently reopens
        # the one-page derivative and verifies its exact RGB sample digest.
        RasterPdfPageAdapter().build(
            raw_path,
            page_path,
            layout,
            RasterPdfPageLimits(
                maximum_width_pixels=image_limits.maximum_output_width_pixels,
                maximum_height_pixels=image_limits.maximum_output_height_pixels,
                maximum_pixels=image_limits.maximum_output_pixels,
                maximum_input_bytes=image_limits.maximum_output_bytes,
                maximum_output_bytes=limits.maximum_image_scratch_bytes
                - image_limits.maximum_output_bytes,
                maximum_memory_bytes=image_limits.maximum_memory_bytes,
                maximum_pdf_objects=limits.inspection.maximum_objects,
            ),
            control,
        )
        page_owned = True
        raw_path.unlink()
        raw_owned = False
        _checkpoint(control)
        yield page_path
    except ImagePageError as error:
        mapped = {
            ImagePageErrorCode.CANCELLED: PdfExportErrorCode.CANCELLED,
            ImagePageErrorCode.TIMED_OUT: PdfExportErrorCode.TIMED_OUT,
            ImagePageErrorCode.LIMIT_EXCEEDED: PdfExportErrorCode.LIMIT_EXCEEDED,
        }.get(error.code, PdfExportErrorCode.SOURCE_UNSUPPORTED)
        reject_pdf_export(mapped, "export.image_decode")
    except OSError:
        reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.image_scratch")
    finally:
        if raw_owned:
            raw_path.unlink(missing_ok=True)
        if page_owned:
            page_path.unlink(missing_ok=True)


def _render_one(
    renderer: PdfPageRenderer,
    path: Path,
    *,
    page_index: int,
    rotation: PdfRenderRotation,
    limits: PdfExportAdapterLimits,
    control: PdfExportAdapterControl,
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
        if error.code is PdfRenderErrorCode.CANCELLED:
            reject_pdf_export(PdfExportErrorCode.CANCELLED, "export.render")
        if error.code is PdfRenderErrorCode.TIMED_OUT:
            reject_pdf_export(PdfExportErrorCode.TIMED_OUT, "export.render")
        if error.code is PdfRenderErrorCode.LIMIT_EXCEEDED:
            reject_pdf_export(PdfExportErrorCode.LIMIT_EXCEEDED, "export.render")
        reject_pdf_export(PdfExportErrorCode.VERIFICATION_RENDER_FAILED, "export.render")
    except PdfExportError:
        raise
    except OSError, MemoryError, RecursionError:
        reject_pdf_export(PdfExportErrorCode.VERIFICATION_RENDER_FAILED, "export.render")
    except Exception:
        reject_pdf_export(PdfExportErrorCode.VERIFICATION_RENDER_FAILED, "export.render")
    if type(result) is not PdfPageRenderResult or destination.bytes_written != result.bytes_written:
        reject_pdf_export(PdfExportErrorCode.VERIFICATION_RENDER_FAILED, "export.render_result")
    if (
        result.page_index != page_index
        or result.page_box is not PdfPageBox.CROP
        or result.additional_rotation is not rotation
    ):
        reject_pdf_export(PdfExportErrorCode.VERIFICATION_RENDER_FAILED, "export.render_result")
    return result


def _render_rotation(rotation: PdfExportRotation) -> PdfRenderRotation:
    try:
        return PdfRenderRotation(int(rotation))
    except ValueError:
        reject_pdf_export(PdfExportErrorCode.INTERNAL_ERROR, "export.rotation")


def _same_rendered_page(
    source: PdfPageRenderResult,
    output: PdfPageRenderResult,
) -> bool:
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


def _remove_failed_candidate(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        reject_pdf_export(PdfExportErrorCode.WORKER_FAILED, "export.output_cleanup")

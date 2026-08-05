"""Bounded pypdfium2 implementation of the worker-only PDF renderer contract."""

from __future__ import annotations

import math
import os
import threading
from collections.abc import Buffer, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Final, cast

import pypdfium2 as pdfium  # type: ignore[import-untyped]
import pypdfium2.raw as pdfium_c  # type: ignore[import-untyped]

from paperworkshell.domain.pdf_geometry import (
    MAX_PDF_PAGE_COORDINATE_MAGNITUDE_UNITS,
    MAX_PDF_PAGE_SPAN_UNITS,
    PdfPageGeometryLimits,
    PdfPageRenderGeometry,
    PdfResolvedPageGeometry,
    fit_pdf_page_geometry,
)
from paperworkshell.domain.pdf_rendering import (
    PdfPageRenderLimits,
    PdfPageRenderRequest,
    PdfPageRenderResult,
    PdfRasterLayout,
    PdfRenderError,
    PdfRenderErrorCode,
    PdfRenderRotation,
    reject_pdf_rendering,
    validate_pdf_render_request,
)
from paperworkshell.pdf.page_geometry import PikePdfPageGeometryResolver
from paperworkshell.pdf.renderer import (
    PdfRasterDestination,
    PdfRenderControl,
    PdfRenderSource,
    require_pdf_render_active,
    write_exact_pdf_raster,
)

PDFIUM_RENDERER_VERSION: Final = 1
EXPECTED_PYPDFIUM2_VERSION: Final = "5.12.1"
EXPECTED_PDFIUM_VERSION: Final = "152.0.7947.0"
EXPECTED_PDFIUM_BUILD: Final = 7_947

_PDFIUM_LOCK: Final = threading.Lock()
_LOCK_POLL_SECONDS: Final = 0.05
_GEOMETRY_RELATIVE_TOLERANCE: Final = 1e-6
_GEOMETRY_ABSOLUTE_TOLERANCE: Final = 1e-5


class _SourceProtocolFault(ValueError):
    """Content-free marker used inside pypdfium2's read callback."""


class _ExactReadSource:
    """Adapt the neutral source to pypdfium2's unchecked ``readinto`` callback."""

    __slots__ = ("_fault", "_length", "_source")

    def __init__(self, source: PdfRenderSource) -> None:
        self._source = source
        self._fault: BaseException | None = None
        self._length = 0
        try:
            position = source.tell()
            length = source.seek(0, os.SEEK_END)
            restored = source.seek(position)
            if (
                type(position) is not int
                or position < 0
                or type(length) is not int
                or length < 0
                or type(restored) is not int
                or restored != position
            ):
                raise _SourceProtocolFault
            self._length = length
        except BaseException as error:
            self._record_fault(error)

    def read(self, size: int = -1, /) -> bytes:
        """Satisfy pypdfium2's stream predicate; native reads use ``readinto``."""

        if self._fault is not None:
            return b""
        try:
            position = self._source.tell()
            if type(position) is not int or not 0 <= position <= self._length:
                raise _SourceProtocolFault
            remaining = self._length - position
            wanted = remaining if size < 0 else min(size, remaining)
            chunks: list[bytes] = []
            received = 0
            while received < wanted:
                chunk = self._source.read(wanted - received)
                if type(chunk) is not bytes or not chunk or len(chunk) > wanted - received:
                    raise _SourceProtocolFault
                chunks.append(chunk)
                received += len(chunk)
            return b"".join(chunks)
        except BaseException as error:
            self._record_fault(error)
            return b""

    def readinto(self, target: Buffer, /) -> int:
        """Fill every requested byte or record a closed fault for post-call handling."""

        try:
            view = memoryview(target).cast("B")
        except Exception as error:
            self._record_fault(error)
            return 0

        offset = 0
        if self._fault is None:
            try:
                while offset < view.nbytes:
                    chunk = self._source.read(view.nbytes - offset)
                    if type(chunk) is not bytes or not chunk or len(chunk) > view.nbytes - offset:
                        raise _SourceProtocolFault
                    end = offset + len(chunk)
                    view[offset:end] = chunk
                    offset = end
            except BaseException as error:
                self._record_fault(error)

        if offset < view.nbytes:
            _zero_buffer(view[offset:])
        # pypdfium2 5.12.1 ignores this count and always reports success to PDFium.
        # Returning the requested count keeps that callback deterministic while the
        # recorded fault makes the enclosing adapter fail after native control returns.
        return view.nbytes

    def seek(self, offset: int, whence: int = os.SEEK_SET, /) -> int:
        try:
            position = self._source.seek(offset, whence)
            if type(position) is not int or position < 0:
                raise _SourceProtocolFault
            if whence == os.SEEK_SET and position != offset:
                raise _SourceProtocolFault
            return position
        except BaseException as error:
            self._record_fault(error)
            return 0

    def tell(self) -> int:
        try:
            position = self._source.tell()
            if type(position) is not int or position < 0:
                raise _SourceProtocolFault
            return position
        except BaseException as error:
            self._record_fault(error)
            return 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def raise_if_faulted(self) -> None:
        error = self._fault
        if error is None:
            return
        if not isinstance(error, Exception):
            raise error
        if isinstance(error, (MemoryError, RecursionError)):
            reject_pdf_rendering(PdfRenderErrorCode.LIMIT_EXCEEDED, "render.pdfium.source")
        reject_pdf_rendering(PdfRenderErrorCode.SOURCE_INVALID, "render.pdfium.source")

    def _record_fault(self, error: BaseException) -> None:
        if self._fault is None:
            self._fault = error


class _PikeReadSource(_ExactReadSource):
    """Expose ordinary EOF-aware ``readinto`` semantics to pikepdf."""

    __slots__ = ()

    def readinto(self, target: Buffer, /) -> int:
        try:
            view = memoryview(target).cast("B")
        except Exception as error:
            self._record_fault(error)
            return 0
        if self._fault is not None:
            return 0
        try:
            position = self._source.tell()
            if type(position) is not int or not 0 <= position <= self._length:
                raise _SourceProtocolFault
            wanted = min(view.nbytes, self._length - position)
            offset = 0
            while offset < wanted:
                chunk = self._source.read(wanted - offset)
                if type(chunk) is not bytes or not chunk or len(chunk) > wanted - offset:
                    raise _SourceProtocolFault
                end = offset + len(chunk)
                view[offset:end] = chunk
                offset = end
            return wanted
        except BaseException as error:
            self._record_fault(error)
            return 0


@dataclass(slots=True)
class _CheckedBitmapMaker:
    """Allocate exactly the prevalidated packed BGRA/RGBA bitmap once."""

    layout: PdfRasterLayout
    bitmap: Any | None = field(default=None, init=False, repr=False)

    def __call__(
        self,
        width: int,
        height: int,
        format: int,
        rev_byteorder: bool = False,
    ) -> Any:
        if (
            type(width) is not int
            or width != self.layout.width_pixels
            or type(height) is not int
            or height != self.layout.height_pixels
            or type(format) is not int
            or format != pdfium_c.FPDFBitmap_BGRA
            or type(rev_byteorder) is not bool
            or not rev_byteorder
            or self.bitmap is not None
        ):
            reject_pdf_rendering(PdfRenderErrorCode.RESULT_INVALID, "render.pdfium.allocation")

        try:
            bitmap = pdfium.PdfBitmap.new_foreign(
                width,
                height,
                format,
                rev_byteorder=rev_byteorder,
                force_packed=True,
            )
        except MemoryError:
            reject_pdf_rendering(PdfRenderErrorCode.LIMIT_EXCEEDED, "render.pdfium.allocation")
        except Exception:
            reject_pdf_rendering(PdfRenderErrorCode.DEPENDENCY_FAILED, "render.pdfium.allocation")

        if not _bitmap_matches_layout(bitmap, self.layout):
            try:
                bitmap.close()
            except Exception:
                pass
            reject_pdf_rendering(PdfRenderErrorCode.RESULT_INVALID, "render.pdfium.bitmap")
        self.bitmap = bitmap
        return bitmap

    def close(self) -> None:
        bitmap = self.bitmap
        self.bitmap = None
        if bitmap is not None:
            bitmap.close()


@dataclass(frozen=True, slots=True)
class PdfiumPageRenderer:
    """Render one bounded page with the approved pypdfium2/PDFium profile."""

    geometry_limits: PdfPageGeometryLimits = field(
        default_factory=lambda: PdfPageGeometryLimits(
            MAX_PDF_PAGE_COORDINATE_MAGNITUDE_UNITS,
            MAX_PDF_PAGE_SPAN_UNITS,
        )
    )
    geometry_resolver: PikePdfPageGeometryResolver = field(
        default_factory=PikePdfPageGeometryResolver
    )

    def __post_init__(self) -> None:
        if (
            type(self.geometry_limits) is not PdfPageGeometryLimits
            or type(self.geometry_resolver) is not PikePdfPageGeometryResolver
        ):
            reject_pdf_rendering(PdfRenderErrorCode.MODEL_INVALID, "render.pdfium.adapter")

    def render(
        self,
        source: PdfRenderSource,
        destination: PdfRasterDestination,
        request: PdfPageRenderRequest,
        limits: PdfPageRenderLimits,
        control: PdfRenderControl,
    ) -> PdfPageRenderResult:
        """Render and flush one exact opaque RGBA8 page without owning caller streams."""

        validate_pdf_render_request(request, limits)
        require_pdf_render_active(control)
        _require_approved_runtime()
        original_position = _source_position(source)

        result: PdfPageRenderResult | None = None
        operation_error: BaseException | None = None
        restore_error: BaseException | None = None
        with _serialized_pdfium(control):
            geometry_source = _PikeReadSource(source)
            try:
                try:
                    geometry = self.geometry_resolver.resolve(
                        cast(BinaryIO, geometry_source),
                        request,
                        limits,
                        self.geometry_limits,
                    )
                except BaseException:
                    geometry_source.raise_if_faulted()
                    raise
                geometry_source.raise_if_faulted()
                plan = fit_pdf_page_geometry(geometry, request, limits)
                require_pdf_render_active(control)
                wrapped_source = _ExactReadSource(source)
                wrapped_source.raise_if_faulted()
                result = _render_document(
                    wrapped_source,
                    destination,
                    request,
                    limits,
                    control,
                    geometry,
                    plan,
                )
            except BaseException as error:
                operation_error = error
            restore_error = _restore_source(source, original_position)

        if restore_error is not None:
            raise restore_error
        if operation_error is not None:
            raise operation_error
        if result is None:
            reject_pdf_rendering(PdfRenderErrorCode.INTERNAL_ERROR, "render.pdfium.adapter")
        return result


def _render_document(
    wrapped_source: _ExactReadSource,
    destination: PdfRasterDestination,
    request: PdfPageRenderRequest,
    limits: PdfPageRenderLimits,
    control: PdfRenderControl,
    geometry: PdfResolvedPageGeometry,
    plan: PdfPageRenderGeometry,
) -> PdfPageRenderResult:
    document = _open_document(wrapped_source)
    page: Any | None = None
    digest: str | None = None
    operation_error: BaseException | None = None
    try:
        page_count = _page_count(document, wrapped_source)
        if page_count > limits.maximum_pages:
            reject_pdf_rendering(PdfRenderErrorCode.LIMIT_EXCEEDED, "render.pages")
        if request.page_index >= page_count:
            reject_pdf_rendering(PdfRenderErrorCode.PAGE_OUT_OF_RANGE, "render.page_index")

        page = _load_page(document, request.page_index, wrapped_source)
        _set_and_verify_geometry(page, geometry, plan)
        require_pdf_render_active(control)
        digest = _render_bitmap(
            page,
            wrapped_source,
            destination,
            request,
            limits,
            control,
            plan,
        )
        wrapped_source.raise_if_faulted()
    except BaseException as error:
        operation_error = error

    cleanup_errors: list[BaseException] = []
    if page is not None:
        page_close_error = _capture_pdfium_close(page, "render.pdfium.page_close")
        if page_close_error is not None:
            cleanup_errors.append(page_close_error)
    document_close_error = _capture_pdfium_close(document, "render.pdfium.document_close")
    if document_close_error is not None:
        cleanup_errors.append(document_close_error)
    _settle_pdfium_lifecycle(operation_error, cleanup_errors)
    if digest is None:
        reject_pdf_rendering(PdfRenderErrorCode.INTERNAL_ERROR, "render.pdfium.result")

    return PdfPageRenderResult(
        page_index=request.page_index,
        width_pixels=plan.layout.width_pixels,
        height_pixels=plan.layout.height_pixels,
        row_stride_bytes=plan.layout.row_stride_bytes,
        bytes_written=plan.layout.byte_count,
        pixel_sha256=digest,
        page_box=request.page_box,
        intrinsic_rotation=geometry.intrinsic_rotation,
        additional_rotation=request.additional_rotation,
    )


def _render_bitmap(
    page: Any,
    source: _ExactReadSource,
    destination: PdfRasterDestination,
    request: PdfPageRenderRequest,
    limits: PdfPageRenderLimits,
    control: PdfRenderControl,
    plan: PdfPageRenderGeometry,
) -> str:
    maker = _CheckedBitmapMaker(plan.layout)
    digest: str | None = None
    operation_error: BaseException | None = None
    try:
        try:
            bitmap = page.render(
                scale=plan.scale_pixels_per_unit,
                rotation=int(request.additional_rotation),
                crop=(0, 0, 0, 0),
                may_draw_forms=False,
                bitmap_maker=maker,
                fill_color=(255, 255, 255, 255),
                grayscale=False,
                optimize_mode=None,
                draw_annots=True,
                no_smoothtext=False,
                no_smoothimage=False,
                no_smoothpath=False,
                force_halftone=False,
                limit_image_cache=False,
                rev_byteorder=True,
                prefer_bgrx=False,
                maybe_alpha=False,
                force_bitmap_format=pdfium_c.FPDFBitmap_BGRA,
                extra_flags=0,
            )
        except PdfRenderError:
            raise
        except MemoryError:
            source.raise_if_faulted()
            reject_pdf_rendering(PdfRenderErrorCode.LIMIT_EXCEEDED, "render.pdfium.resources")
        except Exception:
            source.raise_if_faulted()
            reject_pdf_rendering(PdfRenderErrorCode.DEPENDENCY_FAILED, "render.pdfium.render")

        source.raise_if_faulted()
        require_pdf_render_active(control)
        if (
            bitmap is None
            or bitmap is not maker.bitmap
            or not _bitmap_matches_layout(bitmap, plan.layout)
        ):
            reject_pdf_rendering(PdfRenderErrorCode.RESULT_INVALID, "render.pdfium.bitmap")
        try:
            buffer = cast(Any, bitmap).buffer
        except Exception:
            reject_pdf_rendering(PdfRenderErrorCode.RESULT_INVALID, "render.pdfium.bitmap")
        digest = write_exact_pdf_raster(destination, buffer, plan.layout, limits, control)
    except BaseException as error:
        operation_error = error

    bitmap_close_error = _capture_pdfium_close(maker, "render.pdfium.bitmap_close")
    cleanup_errors = [] if bitmap_close_error is None else [bitmap_close_error]
    _settle_pdfium_lifecycle(operation_error, cleanup_errors)
    if digest is None:
        reject_pdf_rendering(PdfRenderErrorCode.INTERNAL_ERROR, "render.pdfium.result")
    return digest


def _open_document(source: _ExactReadSource) -> Any:
    document: Any | None = None
    operation_error: BaseException | None = None
    try:
        try:
            document = pdfium.PdfDocument(source, password="", autoclose=False)
        except MemoryError:
            source.raise_if_faulted()
            reject_pdf_rendering(PdfRenderErrorCode.LIMIT_EXCEEDED, "render.pdfium.resources")
        except Exception:
            source.raise_if_faulted()
            reject_pdf_rendering(PdfRenderErrorCode.SOURCE_INVALID, "render.pdfium.source")
        source.raise_if_faulted()
    except BaseException as error:
        operation_error = error

    cleanup_errors: list[BaseException] = []
    if operation_error is not None and document is not None:
        close_error = _capture_pdfium_close(document, "render.pdfium.document_close")
        if close_error is not None:
            cleanup_errors.append(close_error)
    _settle_pdfium_lifecycle(operation_error, cleanup_errors)
    if document is None:
        reject_pdf_rendering(PdfRenderErrorCode.INTERNAL_ERROR, "render.pdfium.document")
    return document


def _page_count(document: Any, source: _ExactReadSource) -> int:
    try:
        count = len(document)
    except MemoryError:
        source.raise_if_faulted()
        reject_pdf_rendering(PdfRenderErrorCode.LIMIT_EXCEEDED, "render.pdfium.resources")
    except Exception:
        source.raise_if_faulted()
        reject_pdf_rendering(PdfRenderErrorCode.SOURCE_INVALID, "render.pdfium.page_count")
    source.raise_if_faulted()
    if type(count) is not int or count < 1:
        reject_pdf_rendering(PdfRenderErrorCode.SOURCE_INVALID, "render.pdfium.page_count")
    return count


def _load_page(document: Any, page_index: int, source: _ExactReadSource) -> Any:
    page: Any | None = None
    operation_error: BaseException | None = None
    try:
        try:
            page = document[page_index]
        except MemoryError:
            source.raise_if_faulted()
            reject_pdf_rendering(PdfRenderErrorCode.LIMIT_EXCEEDED, "render.pdfium.resources")
        except Exception:
            source.raise_if_faulted()
            reject_pdf_rendering(PdfRenderErrorCode.SOURCE_INVALID, "render.pdfium.page")
        source.raise_if_faulted()
    except BaseException as error:
        operation_error = error

    cleanup_errors: list[BaseException] = []
    if operation_error is not None and page is not None:
        close_error = _capture_pdfium_close(page, "render.pdfium.page_close")
        if close_error is not None:
            cleanup_errors.append(close_error)
    _settle_pdfium_lifecycle(operation_error, cleanup_errors)
    if page is None:
        reject_pdf_rendering(PdfRenderErrorCode.INTERNAL_ERROR, "render.pdfium.page")
    return page


def _set_and_verify_geometry(
    page: Any,
    geometry: PdfResolvedPageGeometry,
    plan: PdfPageRenderGeometry,
) -> None:
    box = geometry.effective_box
    media = geometry.media_box
    try:
        # PDFium's high-level render already uses the page CropBox. Make the
        # pikepdf-resolved boxes direct in this disposable native document; passing
        # PDF-space insets to render(crop=...) would crop a second time.
        page.set_mediabox(media.left, media.bottom, media.right, media.top)
        page.set_cropbox(box.left, box.bottom, box.right, box.top)
        rotation = page.get_rotation()
        bbox = page.get_bbox()
        page_size = page.get_size()
    except MemoryError:
        reject_pdf_rendering(PdfRenderErrorCode.LIMIT_EXCEEDED, "render.pdfium.resources")
    except Exception:
        reject_pdf_rendering(PdfRenderErrorCode.DEPENDENCY_FAILED, "render.pdfium.geometry")

    if type(rotation) is not int or rotation != int(geometry.intrinsic_rotation):
        reject_pdf_rendering(PdfRenderErrorCode.DEPENDENCY_FAILED, "render.pdfium.rotation")
    if (
        type(bbox) is not tuple
        or len(bbox) != 4
        or not all(
            _geometry_equal(actual, expected)
            for actual, expected in zip(
                bbox,
                (box.left, box.bottom, box.right, box.top),
                strict=True,
            )
        )
    ):
        reject_pdf_rendering(PdfRenderErrorCode.DEPENDENCY_FAILED, "render.pdfium.geometry")

    oriented_width, oriented_height = box.width, box.height
    if geometry.intrinsic_rotation in (
        PdfRenderRotation.DEGREES_90,
        PdfRenderRotation.DEGREES_270,
    ):
        oriented_width, oriented_height = oriented_height, oriented_width
    if (
        type(page_size) is not tuple
        or len(page_size) != 2
        or not _geometry_equal(page_size[0], oriented_width)
        or not _geometry_equal(page_size[1], oriented_height)
    ):
        reject_pdf_rendering(PdfRenderErrorCode.DEPENDENCY_FAILED, "render.pdfium.geometry")

    try:
        native_width = math.ceil(float(page_size[0]) * plan.scale_pixels_per_unit)
        native_height = math.ceil(float(page_size[1]) * plan.scale_pixels_per_unit)
    except OverflowError, TypeError, ValueError:
        reject_pdf_rendering(PdfRenderErrorCode.DEPENDENCY_FAILED, "render.pdfium.geometry")
    if plan.resolved.intrinsic_rotation is not geometry.intrinsic_rotation:
        reject_pdf_rendering(PdfRenderErrorCode.INTERNAL_ERROR, "render.pdfium.geometry")
    if (
        plan.combined_rotation in (PdfRenderRotation.DEGREES_90, PdfRenderRotation.DEGREES_270)
    ) != (
        geometry.intrinsic_rotation in (PdfRenderRotation.DEGREES_90, PdfRenderRotation.DEGREES_270)
    ):
        native_width, native_height = native_height, native_width
    if (native_width, native_height) != (
        plan.layout.width_pixels,
        plan.layout.height_pixels,
    ):
        reject_pdf_rendering(PdfRenderErrorCode.DEPENDENCY_FAILED, "render.pdfium.geometry")


def _bitmap_matches_layout(bitmap: Any, layout: PdfRasterLayout) -> bool:
    try:
        return (
            type(bitmap.width) is int
            and bitmap.width == layout.width_pixels
            and type(bitmap.height) is int
            and bitmap.height == layout.height_pixels
            and type(bitmap.stride) is int
            and bitmap.stride == layout.row_stride_bytes
            and type(bitmap.format) is int
            and bitmap.format == pdfium_c.FPDFBitmap_BGRA
            and type(bitmap.rev_byteorder) is bool
            and bitmap.rev_byteorder
            and bitmap.mode == "RGBA"
            and len(bitmap.buffer) == layout.byte_count
        )
    except Exception:
        return False


def _require_approved_runtime() -> None:
    try:
        wrapper_version = pdfium.PYPDFIUM_INFO.version
        engine_version = pdfium.PDFIUM_INFO.version
        engine_build = pdfium.PDFIUM_INFO.build
        feature_flags = pdfium.PDFIUM_INFO.flags
    except Exception:
        reject_pdf_rendering(PdfRenderErrorCode.DEPENDENCY_UNAVAILABLE, "render.pdfium.runtime")
    if (
        wrapper_version != EXPECTED_PYPDFIUM2_VERSION
        or engine_version != EXPECTED_PDFIUM_VERSION
        or engine_build != EXPECTED_PDFIUM_BUILD
        or feature_flags != ()
    ):
        reject_pdf_rendering(PdfRenderErrorCode.DEPENDENCY_UNSAFE, "render.pdfium.runtime")


@contextmanager
def _serialized_pdfium(control: PdfRenderControl) -> Iterator[None]:
    acquired = False
    try:
        while not acquired:
            require_pdf_render_active(control)
            acquired = _PDFIUM_LOCK.acquire(timeout=_LOCK_POLL_SECONDS)
        require_pdf_render_active(control)
        yield
    finally:
        if acquired:
            _PDFIUM_LOCK.release()


def _source_position(source: PdfRenderSource) -> int:
    try:
        position = source.tell()
    except MemoryError:
        reject_pdf_rendering(PdfRenderErrorCode.LIMIT_EXCEEDED, "render.pdfium.source")
    except Exception:
        reject_pdf_rendering(PdfRenderErrorCode.SOURCE_INVALID, "render.pdfium.source")
    if type(position) is not int or position < 0:
        reject_pdf_rendering(PdfRenderErrorCode.SOURCE_INVALID, "render.pdfium.source")
    return position


def _restore_source(source: PdfRenderSource, position: int) -> BaseException | None:
    try:
        restored = source.seek(position)
        if type(restored) is not int or restored != position:
            raise _SourceProtocolFault
    except BaseException as error:
        if not isinstance(error, Exception):
            return error
        if isinstance(error, (MemoryError, RecursionError)):
            return PdfRenderError(
                PdfRenderErrorCode.LIMIT_EXCEEDED,
                "render.pdfium.source_restore",
            )
        return PdfRenderError(
            PdfRenderErrorCode.SOURCE_INVALID,
            "render.pdfium.source_restore",
        )
    return None


def _capture_pdfium_close(value: Any, context: str) -> BaseException | None:
    """Attempt one close and convert only recoverable failures to safe errors."""

    try:
        value.close()
    except BaseException as error:
        if not isinstance(error, Exception) or isinstance(error, PdfRenderError):
            return error
        if isinstance(error, (MemoryError, RecursionError)):
            return PdfRenderError(PdfRenderErrorCode.LIMIT_EXCEEDED, context)
        return PdfRenderError(PdfRenderErrorCode.DEPENDENCY_FAILED, context)
    return None


def _settle_pdfium_lifecycle(
    operation_error: BaseException | None,
    cleanup_errors: list[BaseException],
) -> None:
    """Apply fail-closed cleanup precedence after every close has been attempted."""

    # Process-control exceptions already in flight always retain precedence over
    # recoverable cleanup faults. This is deliberately checked before cleanup
    # errors so close() cannot swallow SystemExit, KeyboardInterrupt, or GeneratorExit.
    if operation_error is not None and not isinstance(operation_error, Exception):
        raise operation_error
    for cleanup_error in cleanup_errors:
        if not isinstance(cleanup_error, Exception):
            raise cleanup_error
    if cleanup_errors:
        raise cleanup_errors[0]
    if operation_error is not None:
        raise operation_error


def _geometry_equal(actual: object, expected: float) -> bool:
    return (
        type(actual) is float
        and math.isfinite(actual)
        and math.isclose(
            actual,
            expected,
            rel_tol=_GEOMETRY_RELATIVE_TOLERANCE,
            abs_tol=_GEOMETRY_ABSOLUTE_TOLERANCE,
        )
    )


def _zero_buffer(view: memoryview) -> None:
    chunk = bytes(min(view.nbytes, 65_536))
    offset = 0
    while offset < view.nbytes:
        end = min(offset + len(chunk), view.nbytes)
        view[offset:end] = chunk[: end - offset]
        offset = end

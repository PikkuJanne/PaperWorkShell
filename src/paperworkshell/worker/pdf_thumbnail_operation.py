"""Production worker adapter for one bounded, handle-brokered PDF thumbnail."""

from __future__ import annotations

import hashlib
import hmac
import os
import stat
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Final, Never, cast

from paperworkshell.domain.pdf_rendering import (
    MAX_PDF_RENDER_BYTES,
    MAX_PDF_RENDER_DIMENSION_PIXELS,
    MAX_PDF_RENDER_PAGES,
    MAX_PDF_RENDER_PIXELS,
    MAX_PDF_RENDER_WALL_TIME_MS,
    PdfPageBox,
    PdfPageRenderLimits,
    PdfPageRenderRequest,
    PdfPageRenderResult,
    PdfRenderError,
    PdfRenderErrorCode,
    checked_pdf_raster_layout,
)
from paperworkshell.domain.pdf_thumbnails import (
    PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES,
    PdfThumbnailCacheKey,
    PdfThumbnailError,
    PdfThumbnailErrorCode,
    encode_pdf_thumbnail_artifact_header,
    pdf_thumbnail_header_from_render_result,
    reject_pdf_thumbnail,
)
from paperworkshell.pdf.pdfium_renderer import PdfiumPageRenderer
from paperworkshell.pdf.renderer import PdfPageRenderer
from paperworkshell.worker.operations import (
    OperationContext,
    PdfThumbnailOperationHandler,
    PdfThumbnailRenderRequest,
)
from paperworkshell.worker.pdf_thumbnail_profile import (
    PDF_THUMBNAIL_RENDERER_PROFILE_SHA256,
)
from paperworkshell.worker.protocol.models import (
    PDF_THUMBNAIL_CONTENT_KIND,
    CaseObjectReference,
    ReferenceArea,
)

if os.name == "nt":
    import msvcrt

_COPY_CHUNK_BYTES: Final = 1024 * 1024
_FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x0000_0400


@dataclass(frozen=True, slots=True)
class _RenderControl:
    context: OperationContext

    def cancellation_requested(self) -> bool:
        return self.context.cancel_requested.is_set()

    def deadline_expired(self) -> bool:
        try:
            value = self.context.deadline_expired()
        except Exception:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.deadline_probe")
        if type(value) is not bool:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.deadline_probe")
        return value


@dataclass(frozen=True, slots=True)
class WindowsPdfThumbnailAdapter:
    """Verify the source, render privately, then write one fixed cache artifact."""

    renderer: PdfPageRenderer = field(default_factory=PdfiumPageRenderer)

    def __post_init__(self) -> None:
        if not callable(getattr(self.renderer, "render", None)):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "thumbnail.adapter")

    def discard_handles(self, source_handle: int, cache_handle: int) -> None:
        """Consume both transferred authorities without exposing close diagnostics."""

        try:
            _close_native_handle_no_raise(source_handle)
        finally:
            _close_native_handle_no_raise(cache_handle)

    def render_to_cache(
        self,
        context: OperationContext,
        request: PdfThumbnailRenderRequest,
    ) -> CaseObjectReference:
        """Consume both handles and return only after exact bytes are durable and closed."""

        source_owned = request.source_handle
        cache_owned = request.cache_handle
        source_descriptor: int | None = None
        cache_descriptor: int | None = None
        try:
            if os.name != "nt":
                reject_pdf_thumbnail(
                    PdfThumbnailErrorCode.WORKER_FAILED,
                    "thumbnail.platform",
                )
            _require_non_inheritable(source_owned, context="thumbnail.source_handle")
            _require_non_inheritable(cache_owned, context="thumbnail.cache_handle")
            try:
                source_descriptor = msvcrt.open_osfhandle(
                    source_owned,
                    os.O_RDONLY | os.O_BINARY,
                )
                source_owned = 0
                cache_descriptor = msvcrt.open_osfhandle(
                    cache_owned,
                    os.O_WRONLY | os.O_BINARY,
                )
                cache_owned = 0
            except OSError, OverflowError, ValueError:
                reject_pdf_thumbnail(
                    PdfThumbnailErrorCode.DESTINATION_FAILED,
                    "thumbnail.handle_open",
                )

            with ExitStack() as stack:
                source = stack.enter_context(
                    os.fdopen(source_descriptor, "rb", buffering=0, closefd=True)
                )
                source_descriptor = None
                destination = stack.enter_context(
                    os.fdopen(cache_descriptor, "wb", buffering=0, closefd=True)
                )
                cache_descriptor = None
                return self._render_open_handles(
                    context,
                    request,
                    cast(BinaryIO, source),
                    cast(BinaryIO, destination),
                )
        except PdfThumbnailError:
            raise
        except PdfRenderError as error:
            _raise_thumbnail_render_error(error)
        except MemoryError, RecursionError:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.LIMIT_EXCEEDED, "thumbnail.resources")
        except Exception:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.adapter")
        finally:
            if source_descriptor is not None:
                _close_descriptor_no_raise(source_descriptor)
            elif source_owned > 0:
                _close_native_handle_no_raise(source_owned)
            if cache_descriptor is not None:
                _close_descriptor_no_raise(cache_descriptor)
            elif cache_owned > 0:
                _close_native_handle_no_raise(cache_owned)

    def _render_open_handles(
        self,
        context: OperationContext,
        request: PdfThumbnailRenderRequest,
        source: BinaryIO,
        destination: BinaryIO,
    ) -> CaseObjectReference:
        reference = context.inputs[0]
        expected_size = reference.expected_byte_size
        expected_digest = reference.expected_sha256
        if expected_size is None or expected_digest is None:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.SOURCE_INVALID, "thumbnail.source")
        if request.page_box is not PdfPageBox.CROP:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.MODEL_INVALID, "thumbnail.page_box")

        try:
            source_info = os.fstat(source.fileno())
            cache_position = destination.tell()
        except OSError, OverflowError, ValueError:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.SOURCE_INVALID, "thumbnail.handle_identity")
        if (
            not stat.S_ISREG(source_info.st_mode)
            or source_info.st_size != expected_size
            or type(cache_position) is not int
            or cache_position != 0
        ):
            reject_pdf_thumbnail(PdfThumbnailErrorCode.SOURCE_INVALID, "thumbnail.handle_identity")

        limits = _render_limits(context, request, expected_size)
        cache_key = PdfThumbnailCacheKey(
            case_id=context.case_id,
            source_id=reference.object_id,
            page_id=request.page_id,
            source_page_index=request.source_page_index,
            source_byte_size=expected_size,
            source_sha256=expected_digest,
            maximum_width_pixels=request.maximum_width_pixels,
            maximum_height_pixels=request.maximum_height_pixels,
            additional_rotation=request.additional_rotation,
            renderer_profile_sha256=PDF_THUMBNAIL_RENDERER_PROFILE_SHA256,
        )
        control = _RenderControl(context)
        _require_active(control)
        temp_root = _materialize_worker_private_temp()
        try:
            staged_source_context = tempfile.TemporaryFile(mode="w+b", dir=temp_root)
            raster_context = tempfile.TemporaryFile(mode="w+b", dir=temp_root)
        except OSError:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.LIMIT_EXCEEDED, "thumbnail.temp_create")

        with staged_source_context as staged_source, raster_context as raster:
            source_copy = cast(BinaryIO, staged_source)
            raster_copy = cast(BinaryIO, raster)
            _copy_verified_source(
                source,
                source_copy,
                expected_size=expected_size,
                expected_digest=expected_digest,
                control=control,
            )
            source_copy.flush()
            source_copy.seek(0)
            result = self.renderer.render(
                source_copy,
                raster_copy,
                PdfPageRenderRequest(
                    page_index=request.source_page_index,
                    maximum_width_pixels=request.maximum_width_pixels,
                    maximum_height_pixels=request.maximum_height_pixels,
                    page_box=request.page_box,
                    additional_rotation=request.additional_rotation,
                ),
                limits,
                control,
            )
            _require_render_result(result, request, limits)
            header = pdf_thumbnail_header_from_render_result(
                context.operation_id,
                request.cache_object_id,
                cache_key,
                result,
            )
            header_bytes = encode_pdf_thumbnail_artifact_header(header)
            artifact_size, artifact_digest = _write_artifact(
                destination,
                raster_copy,
                result,
                header_bytes=header_bytes,
                maximum_output_bytes=context.limits.output_bytes,
                control=control,
            )

        return CaseObjectReference(
            area=ReferenceArea.OPERATION_STAGING,
            object_id=request.cache_object_id,
            operation_id=context.operation_id,
            content_kind=PDF_THUMBNAIL_CONTENT_KIND,
            expected_byte_size=artifact_size,
            expected_sha256=artifact_digest,
        )


def _render_limits(
    context: OperationContext,
    request: PdfThumbnailRenderRequest,
    source_byte_size: int,
) -> PdfPageRenderLimits:
    output_capacity = context.limits.output_bytes - PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES
    temporary_capacity = context.limits.temporary_storage_bytes - source_byte_size
    destination_capacity = min(output_capacity, temporary_capacity, MAX_PDF_RENDER_BYTES)
    memory_capacity = min(context.limits.memory_bytes, destination_capacity, MAX_PDF_RENDER_BYTES)
    if destination_capacity < 1 or memory_capacity < 1:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.LIMIT_EXCEEDED, "thumbnail.capacity")
    return PdfPageRenderLimits(
        maximum_pages=min(context.limits.page_count, MAX_PDF_RENDER_PAGES),
        maximum_width_pixels=min(
            request.maximum_width_pixels,
            MAX_PDF_RENDER_DIMENSION_PIXELS,
        ),
        maximum_height_pixels=min(
            request.maximum_height_pixels,
            MAX_PDF_RENDER_DIMENSION_PIXELS,
        ),
        maximum_pixels=min(context.limits.pixel_count, MAX_PDF_RENDER_PIXELS),
        maximum_bitmap_bytes=destination_capacity,
        maximum_destination_bytes=destination_capacity,
        maximum_memory_bytes=memory_capacity,
        wall_time_ms=min(context.limits.wall_time_ms, MAX_PDF_RENDER_WALL_TIME_MS),
    )


def _copy_verified_source(
    source: BinaryIO,
    destination: BinaryIO,
    *,
    expected_size: int,
    expected_digest: str,
    control: _RenderControl,
) -> None:
    digest = hashlib.sha256()
    copied = 0
    while copied < expected_size:
        _require_active(control)
        wanted = min(_COPY_CHUNK_BYTES, expected_size - copied)
        try:
            block = source.read(wanted)
        except OSError:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.SOURCE_INVALID, "thumbnail.source_read")
        if type(block) is not bytes or not block or len(block) > wanted:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.SOURCE_INVALID, "thumbnail.source_read")
        _write_exact(destination, block, context="thumbnail.temp_write")
        digest.update(block)
        copied += len(block)
    try:
        surplus = source.read(1)
    except OSError:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.SOURCE_INVALID, "thumbnail.source_eof")
    if surplus != b"" or not hmac.compare_digest(digest.hexdigest(), expected_digest):
        reject_pdf_thumbnail(PdfThumbnailErrorCode.SOURCE_INVALID, "thumbnail.source_verify")
    _require_active(control)


def _require_render_result(
    result: PdfPageRenderResult,
    request: PdfThumbnailRenderRequest,
    limits: PdfPageRenderLimits,
) -> None:
    if (
        type(result) is not PdfPageRenderResult
        or result.page_index != request.source_page_index
        or result.page_box is not request.page_box
        or result.additional_rotation is not request.additional_rotation
        or result.width_pixels > request.maximum_width_pixels
        or result.height_pixels > request.maximum_height_pixels
    ):
        reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.render_result")
    try:
        layout = checked_pdf_raster_layout(
            result.width_pixels,
            result.height_pixels,
            limits,
        )
    except PdfRenderError as error:
        if error.code is PdfRenderErrorCode.LIMIT_EXCEEDED:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.LIMIT_EXCEEDED, "thumbnail.render_result")
        reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.render_result")
    if (
        result.row_stride_bytes != layout.row_stride_bytes
        or result.bytes_written != layout.byte_count
    ):
        reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.render_result")


def _write_artifact(
    destination: BinaryIO,
    raster: BinaryIO,
    result: PdfPageRenderResult,
    *,
    header_bytes: bytes,
    maximum_output_bytes: int,
    control: _RenderControl,
) -> tuple[int, str]:
    total_bytes = len(header_bytes) + result.bytes_written
    if total_bytes > maximum_output_bytes:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.LIMIT_EXCEEDED, "thumbnail.output_size")
    try:
        raster.flush()
        raster_size = raster.seek(0, os.SEEK_END)
        raster.seek(0)
    except OSError:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.raster_state")
    if type(raster_size) is not int or raster_size != result.bytes_written:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.raster_size")

    artifact_digest = hashlib.sha256()
    pixel_digest = hashlib.sha256()
    _write_exact(destination, header_bytes, context="thumbnail.cache_write")
    artifact_digest.update(header_bytes)
    copied = 0
    while copied < result.bytes_written:
        _require_active(control)
        wanted = min(_COPY_CHUNK_BYTES, result.bytes_written - copied)
        try:
            block = raster.read(wanted)
        except OSError:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.raster_read")
        if type(block) is not bytes or not block or len(block) > wanted:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.raster_read")
        if len(block) % 4 != 0 or block[3::4].count(255) != len(block) // 4:
            reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.raster_alpha")
        _write_exact(destination, block, context="thumbnail.cache_write")
        artifact_digest.update(block)
        pixel_digest.update(block)
        copied += len(block)
    try:
        surplus = raster.read(1)
    except OSError:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.raster_eof")
    if surplus != b"" or not hmac.compare_digest(
        pixel_digest.hexdigest(),
        result.pixel_sha256,
    ):
        reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.raster_digest")
    _require_active(control)
    try:
        destination.flush()
        os.fsync(destination.fileno())
        final_position = destination.tell()
    except OSError:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.DESTINATION_FAILED, "thumbnail.cache_flush")
    if type(final_position) is not int or final_position != total_bytes:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.DESTINATION_FAILED, "thumbnail.cache_size")
    _require_active(control)
    return total_bytes, artifact_digest.hexdigest()


def _write_exact(destination: BinaryIO, payload: bytes, *, context: str) -> None:
    offset = 0
    try:
        while offset < len(payload):
            written = destination.write(payload[offset:])
            if type(written) is not int or not 1 <= written <= len(payload) - offset:
                reject_pdf_thumbnail(PdfThumbnailErrorCode.DESTINATION_FAILED, context)
            offset += written
    except PdfThumbnailError:
        raise
    except Exception:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.DESTINATION_FAILED, context)


def _require_active(control: _RenderControl) -> None:
    if control.cancellation_requested():
        reject_pdf_thumbnail(PdfThumbnailErrorCode.CANCELLED, "thumbnail.operation")
    if control.deadline_expired():
        reject_pdf_thumbnail(PdfThumbnailErrorCode.TIMED_OUT, "thumbnail.operation")


def _raise_thumbnail_render_error(error: PdfRenderError) -> Never:
    if error.code is PdfRenderErrorCode.CANCELLED:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.CANCELLED, "thumbnail.render")
    if error.code is PdfRenderErrorCode.TIMED_OUT:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.TIMED_OUT, "thumbnail.render")
    if error.code is PdfRenderErrorCode.LIMIT_EXCEEDED:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.LIMIT_EXCEEDED, "thumbnail.render")
    if error.code is PdfRenderErrorCode.PAGE_OUT_OF_RANGE:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.PAGE_UNAVAILABLE, "thumbnail.render")
    if error.code in {
        PdfRenderErrorCode.SOURCE_INVALID,
        PdfRenderErrorCode.GEOMETRY_INVALID,
    }:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.SOURCE_INVALID, "thumbnail.render")
    if error.code is PdfRenderErrorCode.DESTINATION_FAILED:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.DESTINATION_FAILED, "thumbnail.render")
    if error.code is PdfRenderErrorCode.RESULT_INVALID:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.RESULT_INVALID, "thumbnail.render")
    reject_pdf_thumbnail(PdfThumbnailErrorCode.WORKER_FAILED, "thumbnail.render")


def _require_non_inheritable(handle: int, *, context: str) -> None:
    try:
        inheritable = os.get_handle_inheritable(handle)
    except OSError, OverflowError, ValueError:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.DESTINATION_FAILED, context)
    if inheritable:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.DESTINATION_FAILED, context)


def _close_native_handle_no_raise(handle: int) -> None:
    if handle <= 0 or os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
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
    """Return the launch-profile temp root after in-container identity checks."""

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
            reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.temp_environment")
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
            reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.temp_scope")
        temp_root.mkdir(parents=True, exist_ok=True)
        for candidate in (local_root, temp_root):
            information = os.lstat(candidate)
            attributes = int(getattr(information, "st_file_attributes", 0))
            if not stat.S_ISDIR(information.st_mode) or attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                reject_pdf_thumbnail(
                    PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.temp_identity"
                )
        return temp_root
    except PdfThumbnailError:
        raise
    except OSError, TypeError, ValueError:
        reject_pdf_thumbnail(PdfThumbnailErrorCode.INTERNAL_ERROR, "thumbnail.temp_unavailable")


def build_pdf_thumbnail_operation_handler() -> PdfThumbnailOperationHandler:
    """Compose the production PDFium adapter behind the fixed handler seam."""

    return PdfThumbnailOperationHandler(WindowsPdfThumbnailAdapter())

"""Production worker adapter for bounded, handle-brokered image normalization."""

from __future__ import annotations

import ctypes
import hashlib
import hmac
import os
import stat
from contextlib import ExitStack
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO, Final, Literal, Protocol, Self, cast

from paperworkshell.domain.image_pages import (
    IMAGE_PAGE_ARTIFACT_HEADER_BYTES,
    MAX_IMAGE_DECOMPRESSED_BYTES,
    MAX_IMAGE_DIMENSION_PIXELS,
    MAX_IMAGE_MEMORY_BYTES,
    MAX_IMAGE_METADATA_BYTES,
    MAX_IMAGE_METADATA_RECORDS,
    MAX_IMAGE_SOURCE_BYTES,
    MAX_IMAGE_SOURCE_PIXELS,
    MAX_IMAGE_WALL_TIME_MS,
    ImageNormalizationLimits,
    ImageNormalizationRequest,
    ImageNormalizationResult,
    ImagePageArtifactHeader,
    ImagePageError,
    ImagePageErrorCode,
    ImageQuarterTurn,
    ImageSourceFormat,
    encode_image_page_artifact_header,
    reject_image_page,
    validate_image_normalization_result,
)
from paperworkshell.imaging.pillow_decoder import (
    ImageNormalizationControl,
    PillowImageDecoder,
)
from paperworkshell.worker.operations import (
    ImageNormalizeOperationHandler,
    ImageNormalizeOperationRequest,
    OperationContext,
)
from paperworkshell.worker.private_temp import (
    OsWorkerPrivateTempStorage,
    WorkerPrivateTempStorage,
    WorkerPrivateTempStorageError,
)
from paperworkshell.worker.protocol.models import (
    IMAGE_NORMALIZE_OUTPUT_CONTENT_KIND,
    CaseObjectReference,
    ReferenceArea,
)

if os.name == "nt":
    import msvcrt

_COPY_CHUNK_BYTES: Final = 1024 * 1024
_FILE_ATTRIBUTE_REPARSE_POINT: Final = 0x0000_0400
_FILE_MAP_WRITE: Final = 0x0000_0002
_MAX_WINDOWS_SIZE_T: Final = (1 << (ctypes.sizeof(ctypes.c_size_t) * 8)) - 1


class ImageDecoder(Protocol):
    """Decoder seam implemented by the Pillow adapter."""

    def normalize(
        self,
        source: BinaryIO,
        destination: BinaryIO,
        *,
        expected_format: ImageSourceFormat,
        request: ImageNormalizationRequest,
        limits: ImageNormalizationLimits,
        control: ImageNormalizationControl,
    ) -> ImageNormalizationResult: ...


@dataclass(slots=True, repr=False)
class _MappedOutputSection:
    """Own one fixed-capacity writable view of a broker-created pagefile section."""

    _handle: int
    capacity: int
    _view: int
    _position: int = 0
    _closed: bool = False

    @classmethod
    def adopt(cls, handle: int, capacity: int) -> Self:
        """Consume ``handle`` and map exactly ``capacity`` writable bytes."""

        if type(handle) is not int or handle <= 0:
            reject_image_page(ImagePageErrorCode.DESTINATION_FAILED, "image.output_handle")
        if type(capacity) is not int or capacity <= 0 or capacity > _MAX_WINDOWS_SIZE_T:
            _close_native_handle_no_raise(handle)
            reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.output_capacity")
        try:
            view = _map_output_section(handle, capacity)
        except Exception:
            _close_native_handle_no_raise(handle)
            reject_image_page(ImagePageErrorCode.DESTINATION_FAILED, "image.output_map")
        if view <= 0:
            _close_native_handle_no_raise(handle)
            reject_image_page(ImagePageErrorCode.DESTINATION_FAILED, "image.output_map")
        return cls(handle, capacity, view)

    def __enter__(self) -> Self:
        if self._closed:
            reject_image_page(ImagePageErrorCode.DESTINATION_FAILED, "image.output_closed")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> Literal[False]:
        try:
            self.close()
        except ImagePageError:
            if exc_type is None:
                raise
        return False

    def write(self, payload: bytes, /) -> int:
        if self._closed or type(payload) is not bytes:
            reject_image_page(ImagePageErrorCode.DESTINATION_FAILED, "image.output_write")
        remaining = self.capacity - self._position
        if len(payload) > remaining:
            reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.output_capacity")
        if payload:
            try:
                _copy_to_output_view(self._view, self._position, payload)
            except Exception:
                reject_image_page(ImagePageErrorCode.DESTINATION_FAILED, "image.output_write")
            self._position += len(payload)
        return len(payload)

    def tell(self) -> int:
        if self._closed:
            reject_image_page(ImagePageErrorCode.DESTINATION_FAILED, "image.output_state")
        return self._position

    def flush(self) -> None:
        if self._closed:
            reject_image_page(ImagePageErrorCode.DESTINATION_FAILED, "image.output_flush")
        if self._position == 0:
            return
        try:
            flushed = _flush_output_view(self._view, self._position)
        except Exception:
            flushed = False
        if not flushed:
            reject_image_page(ImagePageErrorCode.DESTINATION_FAILED, "image.output_flush")

    def close(self) -> None:
        if self._closed:
            return
        view = self._view
        handle = self._handle
        self._view = 0
        self._handle = 0
        self._closed = True
        try:
            unmapped = _unmap_output_view(view)
        except Exception:
            unmapped = False
        try:
            handle_closed = _close_native_handle(handle)
        except Exception:
            handle_closed = False
        if not unmapped or not handle_closed:
            reject_image_page(ImagePageErrorCode.DESTINATION_FAILED, "image.output_cleanup")


@dataclass(frozen=True, slots=True)
class _OperationControl:
    context: OperationContext

    def cancellation_requested(self) -> bool:
        return self.context.cancel_requested.is_set()

    def deadline_expired(self) -> bool:
        try:
            value = self.context.deadline_expired()
        except Exception:
            reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.deadline_probe")
        if type(value) is not bool:
            reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.deadline_probe")
        return value


@dataclass(frozen=True, slots=True)
class WindowsImageNormalizeAdapter:
    """Authenticate one immutable image and emit a fixed RGBA8 artifact."""

    decoder: ImageDecoder = field(default_factory=PillowImageDecoder)
    private_temp_storage: WorkerPrivateTempStorage = field(
        default_factory=OsWorkerPrivateTempStorage,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not callable(getattr(self.decoder, "normalize", None)) or any(
            not callable(getattr(self.private_temp_storage, member, None))
            for member in ("require_capacity", "open_anonymous")
        ):
            reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.adapter")

    def discard_handles(
        self,
        context: OperationContext,
        request: ImageNormalizeOperationRequest,
    ) -> None:
        """Consume both transferred authorities without exposing close diagnostics."""

        del context
        try:
            _close_native_handle_no_raise(request.source_handle)
        finally:
            _close_native_handle_no_raise(request.output_handle)

    def normalize(
        self,
        context: OperationContext,
        request: ImageNormalizeOperationRequest,
    ) -> CaseObjectReference:
        """Consume both handles and return after the bounded section is closed."""

        source_owned = request.source_handle
        output_owned = request.output_handle
        source_descriptor: int | None = None
        try:
            if os.name != "nt":
                reject_image_page(ImagePageErrorCode.DEPENDENCY_UNAVAILABLE, "image.platform")
            _require_non_inheritable(
                source_owned,
                context="image.source_handle",
                code=ImagePageErrorCode.SOURCE_INVALID,
            )
            _require_non_inheritable(output_owned, context="image.output_handle")
            try:
                source_descriptor = msvcrt.open_osfhandle(
                    source_owned,
                    os.O_RDONLY | os.O_BINARY,
                )
                source_owned = 0
            except OSError, OverflowError, ValueError:
                reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "image.source_handle_open")

            with ExitStack() as stack:
                source = stack.enter_context(
                    os.fdopen(source_descriptor, "rb", buffering=0, closefd=True)
                )
                source_descriptor = None
                destination_handle = output_owned
                output_owned = 0
                destination = stack.enter_context(
                    _MappedOutputSection.adopt(
                        destination_handle,
                        context.limits.output_bytes,
                    )
                )
                return self._normalize_open_streams(
                    context,
                    request,
                    cast(BinaryIO, source),
                    destination,
                )
        except ImagePageError:
            raise
        except MemoryError, RecursionError:
            reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.resources")
        except Exception:
            reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.adapter")
        finally:
            if source_descriptor is not None:
                _close_descriptor_no_raise(source_descriptor)
            elif source_owned > 0:
                _close_native_handle_no_raise(source_owned)
            if output_owned > 0:
                _close_native_handle_no_raise(output_owned)

    def _normalize_open_streams(
        self,
        context: OperationContext,
        request: ImageNormalizeOperationRequest,
        source: BinaryIO,
        destination: _MappedOutputSection,
    ) -> CaseObjectReference:
        reference = _source_reference(context)
        expected_size = reference.expected_byte_size
        expected_digest = reference.expected_sha256
        if expected_size is None or expected_digest is None:
            reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "image.source")
        expected_format = _source_format(reference.content_kind)
        try:
            source_info = os.fstat(source.fileno())
        except OSError, OverflowError, ValueError:
            reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "image.handle_identity")
        if not stat.S_ISREG(source_info.st_mode) or source_info.st_size != expected_size:
            reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "image.handle_identity")
        if destination.capacity != context.limits.output_bytes or destination.tell() != 0:
            reject_image_page(ImagePageErrorCode.DESTINATION_FAILED, "image.output_identity")

        operation_request = ImageNormalizationRequest(
            expected_source_format=expected_format,
            expected_source_bytes=expected_size,
            expected_source_sha256=expected_digest,
            maximum_width_pixels=request.maximum_width_pixels,
            maximum_height_pixels=request.maximum_height_pixels,
            additional_rotation=_quarter_turn(request.additional_rotation),
        )
        limits = _normalization_limits(context, request, expected_size)
        control = _OperationControl(context)
        _require_active(control)
        temp_root = _materialize_worker_private_temp()
        try:
            self.private_temp_storage.require_capacity(
                temp_root,
                limits.maximum_output_bytes,
            )
        except WorkerPrivateTempStorageError:
            reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.temp_capacity")
        try:
            raster_context = self.private_temp_storage.open_anonymous(temp_root)
        except WorkerPrivateTempStorageError, OSError:
            reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.temp_create")
        with raster_context as raster:
            result = self.decoder.normalize(
                source,
                raster,
                expected_format=expected_format,
                request=operation_request,
                limits=limits,
                control=control,
            )
            validate_image_normalization_result(operation_request, limits, result)
            header = ImagePageArtifactHeader(
                operation_id=context.operation_id,
                output_id=request.output_object_id,
                result=result,
            )
            artifact_size, artifact_digest = _write_artifact(
                destination,
                raster,
                result,
                header_bytes=encode_image_page_artifact_header(header),
                maximum_output_bytes=context.limits.output_bytes,
                control=control,
            )

        return CaseObjectReference(
            area=ReferenceArea.OPERATION_STAGING,
            object_id=request.output_object_id,
            operation_id=context.operation_id,
            content_kind=IMAGE_NORMALIZE_OUTPUT_CONTENT_KIND,
            expected_byte_size=artifact_size,
            expected_sha256=artifact_digest,
        )


def _source_reference(context: OperationContext) -> CaseObjectReference:
    if len(context.inputs) != 1 or type(context.inputs[0]) is not CaseObjectReference:
        reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "image.source_reference")
    reference = context.inputs[0]
    if (
        reference.area is not ReferenceArea.SOURCE_READ
        or reference.operation_id is not None
        or reference.content_kind not in {"jpeg", "png"}
        or reference.expected_byte_size is None
        or reference.expected_sha256 is None
    ):
        reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "image.source_reference")
    return reference


def _source_format(content_kind: str) -> ImageSourceFormat:
    if content_kind == "jpeg":
        return ImageSourceFormat.JPEG
    if content_kind == "png":
        return ImageSourceFormat.PNG
    reject_image_page(ImagePageErrorCode.FORMAT_MISMATCH, "image.source_format")


def _quarter_turn(value: int) -> ImageQuarterTurn:
    try:
        return ImageQuarterTurn(value)
    except ValueError:
        reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.rotation")


def _normalization_limits(
    context: OperationContext,
    request: ImageNormalizeOperationRequest,
    source_bytes: int,
) -> ImageNormalizationLimits:
    pixel_capacity = context.limits.output_bytes - IMAGE_PAGE_ARTIFACT_HEADER_BYTES
    temporary_capacity = context.limits.temporary_storage_bytes
    maximum_output_bytes = min(pixel_capacity, temporary_capacity)
    maximum_output_pixels = min(
        context.limits.pixel_count,
        request.maximum_width_pixels * request.maximum_height_pixels,
        maximum_output_bytes // 4,
    )
    if (
        not 1 <= source_bytes <= MAX_IMAGE_SOURCE_BYTES
        or maximum_output_bytes < 4
        or maximum_output_pixels < 1
    ):
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.capacity")
    return ImageNormalizationLimits(
        maximum_source_bytes=source_bytes,
        maximum_encoded_width_pixels=MAX_IMAGE_DIMENSION_PIXELS,
        maximum_encoded_height_pixels=MAX_IMAGE_DIMENSION_PIXELS,
        maximum_source_pixels=min(context.limits.pixel_count, MAX_IMAGE_SOURCE_PIXELS),
        maximum_decompressed_bytes=min(
            context.limits.memory_bytes,
            MAX_IMAGE_DECOMPRESSED_BYTES,
        ),
        maximum_metadata_records=MAX_IMAGE_METADATA_RECORDS,
        maximum_metadata_bytes=min(MAX_IMAGE_METADATA_BYTES, context.limits.memory_bytes),
        maximum_output_width_pixels=request.maximum_width_pixels,
        maximum_output_height_pixels=request.maximum_height_pixels,
        maximum_output_pixels=maximum_output_pixels,
        maximum_output_bytes=maximum_output_bytes,
        maximum_memory_bytes=min(context.limits.memory_bytes, MAX_IMAGE_MEMORY_BYTES),
        wall_time_ms=min(context.limits.wall_time_ms, MAX_IMAGE_WALL_TIME_MS),
    )


def _write_artifact(
    destination: _MappedOutputSection,
    raster: BinaryIO,
    result: ImageNormalizationResult,
    *,
    header_bytes: bytes,
    maximum_output_bytes: int,
    control: _OperationControl,
) -> tuple[int, str]:
    total_bytes = len(header_bytes) + result.bytes_written
    if total_bytes > maximum_output_bytes:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.output_size")
    try:
        raster.flush()
        raster_size = raster.seek(0, os.SEEK_END)
        raster.seek(0)
    except OSError:
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.raster_state")
    if type(raster_size) is not int or raster_size != result.bytes_written:
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.raster_size")

    artifact_digest = hashlib.sha256()
    pixel_digest = hashlib.sha256()
    _write_exact(destination, header_bytes, context="image.output_write")
    artifact_digest.update(header_bytes)
    copied = 0
    while copied < result.bytes_written:
        _require_active(control)
        wanted = min(_COPY_CHUNK_BYTES, result.bytes_written - copied)
        try:
            block = raster.read(wanted)
        except OSError:
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.raster_read")
        if type(block) is not bytes or not block or len(block) > wanted:
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.raster_read")
        if len(block) % 4 != 0 or block[3::4].count(255) != len(block) // 4:
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.raster_alpha")
        _write_exact(destination, block, context="image.output_write")
        artifact_digest.update(block)
        pixel_digest.update(block)
        copied += len(block)
    try:
        surplus = raster.read(1)
    except OSError:
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.raster_eof")
    if surplus != b"" or not hmac.compare_digest(
        pixel_digest.hexdigest(),
        result.pixel_sha256,
    ):
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.raster_digest")
    _require_active(control)
    destination.flush()
    if destination.tell() != total_bytes:
        reject_image_page(ImagePageErrorCode.DESTINATION_FAILED, "image.output_size")
    _require_active(control)
    return total_bytes, artifact_digest.hexdigest()


def _write_exact(destination: _MappedOutputSection, payload: bytes, *, context: str) -> None:
    offset = 0
    try:
        while offset < len(payload):
            written = destination.write(payload[offset:])
            if type(written) is not int or not 1 <= written <= len(payload) - offset:
                reject_image_page(ImagePageErrorCode.DESTINATION_FAILED, context)
            offset += written
    except ImagePageError:
        raise
    except Exception:
        reject_image_page(ImagePageErrorCode.DESTINATION_FAILED, context)


def _require_active(control: ImageNormalizationControl) -> None:
    if control.cancellation_requested():
        reject_image_page(ImagePageErrorCode.CANCELLED, "image.operation")
    if control.deadline_expired():
        reject_image_page(ImagePageErrorCode.TIMED_OUT, "image.operation")


def _require_non_inheritable(
    handle: int,
    *,
    context: str,
    code: ImagePageErrorCode = ImagePageErrorCode.DESTINATION_FAILED,
) -> None:
    try:
        inheritable = os.get_handle_inheritable(handle)
    except OSError, OverflowError, ValueError:
        reject_image_page(code, context)
    if inheritable:
        reject_image_page(code, context)


def _kernel32() -> Any:
    if os.name != "nt":
        reject_image_page(ImagePageErrorCode.DEPENDENCY_UNAVAILABLE, "image.platform")
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _map_output_section(handle: int, capacity: int) -> int:
    kernel32 = _kernel32()
    map_view = kernel32.MapViewOfFile
    map_view.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_size_t,
    )
    map_view.restype = ctypes.c_void_p
    mapped = map_view(wintypes.HANDLE(handle), _FILE_MAP_WRITE, 0, 0, capacity)
    return int(mapped or 0)


def _copy_to_output_view(view: int, offset: int, payload: bytes) -> None:
    ctypes.memmove(ctypes.c_void_p(view + offset), payload, len(payload))


def _flush_output_view(view: int, byte_count: int) -> bool:
    kernel32 = _kernel32()
    flush_view = kernel32.FlushViewOfFile
    flush_view.argtypes = (ctypes.c_void_p, ctypes.c_size_t)
    flush_view.restype = wintypes.BOOL
    return bool(flush_view(ctypes.c_void_p(view), byte_count))


def _unmap_output_view(view: int) -> bool:
    kernel32 = _kernel32()
    unmap_view = kernel32.UnmapViewOfFile
    unmap_view.argtypes = (ctypes.c_void_p,)
    unmap_view.restype = wintypes.BOOL
    return bool(unmap_view(ctypes.c_void_p(view)))


def _close_native_handle(handle: int) -> bool:
    kernel32 = _kernel32()
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    return bool(close_handle(wintypes.HANDLE(handle)))


def _close_native_handle_no_raise(handle: int) -> None:
    if handle <= 0 or os.name != "nt":
        return
    try:
        _close_native_handle(handle)
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
            reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.temp_environment")
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
            reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.temp_scope")
        temp_root.mkdir(parents=True, exist_ok=True)
        for candidate in (local_root, temp_root):
            information = os.lstat(candidate)
            attributes = int(getattr(information, "st_file_attributes", 0))
            if not stat.S_ISDIR(information.st_mode) or attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.temp_identity")
        return temp_root
    except ImagePageError:
        raise
    except OSError, TypeError, ValueError:
        reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.temp_unavailable")


def build_image_normalize_operation_handler() -> ImageNormalizeOperationHandler:
    """Compose the production Pillow adapter behind the fixed protocol handler."""

    return ImageNormalizeOperationHandler(WindowsImageNormalizeAdapter())

"""Restricted-worker adapter for metadata-free straight-alpha signature rasters."""

from __future__ import annotations

import hashlib
import hmac
import os
import stat
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import BinaryIO, Final, Protocol, cast

from paperworkshell.domain.image_pages import (
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
    ImagePageError,
    ImagePageErrorCode,
    ImageSourceFormat,
    reject_image_page,
    validate_image_normalization_result,
)
from paperworkshell.domain.signature_images import (
    SIGNATURE_IMAGE_RASTER_ARTIFACT_HEADER_BYTES,
    SignatureImageMediaType,
    SignatureImageRasterArtifactHeader,
    SignatureImageRasterRequest,
    SignatureImageRasterResult,
    encode_signature_image_raster_artifact_header,
)
from paperworkshell.imaging.pillow_decoder import ImageNormalizationControl, PillowImageDecoder
from paperworkshell.worker.image_normalize_operation import (
    _close_descriptor_no_raise,
    _close_native_handle_no_raise,
    _MappedOutputSection,
    _materialize_worker_private_temp,
    _OperationControl,
    _require_active,
    _require_non_inheritable,
)
from paperworkshell.worker.operations import (
    OperationContext,
    SignatureImageRasterOperationHandler,
    SignatureImageRasterOperationRequest,
)
from paperworkshell.worker.private_temp import (
    OsWorkerPrivateTempStorage,
    WorkerPrivateTempStorage,
    WorkerPrivateTempStorageError,
)
from paperworkshell.worker.protocol.models import (
    SIGNATURE_IMAGE_RASTER_OUTPUT_CONTENT_KIND,
    CaseObjectReference,
    ReferenceArea,
)

if os.name == "nt":
    import msvcrt

_COPY_CHUNK_BYTES: Final = 64 * 1024


class SignatureImageDecoder(Protocol):
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


@dataclass(frozen=True, slots=True)
class WindowsSignatureImageRasterAdapter:
    """Authenticate one asset and emit a separate straight-alpha artifact profile."""

    decoder: SignatureImageDecoder = field(default_factory=PillowImageDecoder)
    private_temp_storage: WorkerPrivateTempStorage = field(
        default_factory=OsWorkerPrivateTempStorage,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not callable(getattr(self.decoder, "normalize", None)) or any(
            not callable(getattr(self.private_temp_storage, member, None))
            for member in ("require_capacity", "open_anonymous")
        ):
            reject_image_page(ImagePageErrorCode.MODEL_INVALID, "signature.adapter")

    def discard_handles(
        self,
        context: OperationContext,
        request: SignatureImageRasterOperationRequest,
    ) -> None:
        del context
        try:
            _close_native_handle_no_raise(request.source_handle)
        finally:
            _close_native_handle_no_raise(request.output_handle)

    def rasterize(
        self,
        context: OperationContext,
        request: SignatureImageRasterOperationRequest,
    ) -> CaseObjectReference:
        """Consume both transferred handles and return a correlated staging reference."""

        source_owned = request.source_handle
        output_owned = request.output_handle
        source_descriptor: int | None = None
        try:
            if os.name != "nt":
                reject_image_page(ImagePageErrorCode.DEPENDENCY_UNAVAILABLE, "signature.platform")
            _require_non_inheritable(
                source_owned,
                context="signature.source_handle",
                code=ImagePageErrorCode.SOURCE_INVALID,
            )
            _require_non_inheritable(output_owned, context="signature.output_handle")
            try:
                source_descriptor = msvcrt.open_osfhandle(
                    source_owned,
                    os.O_RDONLY | os.O_BINARY,
                )
                source_owned = 0
            except OSError, OverflowError, ValueError:
                reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "signature.source_open")
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
                return self._rasterize_open_streams(
                    context,
                    request,
                    cast(BinaryIO, source),
                    destination,
                )
        except ImagePageError:
            raise
        except MemoryError, RecursionError:
            reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "signature.resources")
        except Exception:
            reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "signature.adapter")
        finally:
            if source_descriptor is not None:
                _close_descriptor_no_raise(source_descriptor)
            elif source_owned > 0:
                _close_native_handle_no_raise(source_owned)
            if output_owned > 0:
                _close_native_handle_no_raise(output_owned)

    def _rasterize_open_streams(
        self,
        context: OperationContext,
        request: SignatureImageRasterOperationRequest,
        source: BinaryIO,
        destination: _MappedOutputSection,
    ) -> CaseObjectReference:
        reference = _asset_reference(context)
        expected_size = reference.expected_byte_size
        expected_digest = reference.expected_sha256
        if expected_size is None or expected_digest is None:
            reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "signature.source")
        try:
            source_info = os.fstat(source.fileno())
        except OSError, OverflowError, ValueError:
            reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "signature.handle_identity")
        if not stat.S_ISREG(source_info.st_mode) or source_info.st_size != expected_size:
            reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "signature.handle_identity")
        if destination.capacity != context.limits.output_bytes or destination.tell() != 0:
            reject_image_page(ImagePageErrorCode.DESTINATION_FAILED, "signature.output_identity")

        raster_request = SignatureImageRasterRequest(
            case_id=context.case_id,
            asset_id=reference.object_id,
            media_type=_media_type(reference.content_kind),
            source_byte_count=expected_size,
            source_sha256=expected_digest,
            maximum_width_pixels=request.maximum_width_pixels,
            maximum_height_pixels=request.maximum_height_pixels,
        )
        image_request = raster_request.image_request()
        limits = _signature_limits(context, request, expected_size)
        control = _OperationControl(context)
        _require_active(control)
        temp_root = _materialize_worker_private_temp()
        try:
            self.private_temp_storage.require_capacity(temp_root, limits.maximum_output_bytes)
        except WorkerPrivateTempStorageError:
            reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "signature.temp_capacity")
        try:
            raster_context = self.private_temp_storage.open_anonymous(temp_root)
        except WorkerPrivateTempStorageError, OSError:
            reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "signature.temp_create")
        with raster_context as raster:
            normalized = self.decoder.normalize(
                source,
                raster,
                expected_format=raster_request.source_format,
                request=image_request,
                limits=limits,
                control=control,
            )
            validate_image_normalization_result(image_request, limits, normalized)
            result = SignatureImageRasterResult.from_normalization(
                raster_request,
                normalized,
            )
            result.validate_for(raster_request)
            header = SignatureImageRasterArtifactHeader(
                context.operation_id,
                request.output_object_id,
                result,
            )
            artifact_size, artifact_digest = _write_signature_artifact(
                destination,
                raster,
                normalized,
                header_bytes=encode_signature_image_raster_artifact_header(header),
                maximum_output_bytes=context.limits.output_bytes,
                control=control,
            )
        return CaseObjectReference(
            area=ReferenceArea.OPERATION_STAGING,
            object_id=request.output_object_id,
            operation_id=context.operation_id,
            content_kind=SIGNATURE_IMAGE_RASTER_OUTPUT_CONTENT_KIND,
            expected_byte_size=artifact_size,
            expected_sha256=artifact_digest,
        )


def _asset_reference(context: OperationContext) -> CaseObjectReference:
    if len(context.inputs) != 1 or type(context.inputs[0]) is not CaseObjectReference:
        reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "signature.asset_reference")
    reference = context.inputs[0]
    if (
        reference.area is not ReferenceArea.ASSET_READ
        or reference.operation_id is not None
        or reference.content_kind not in {"jpeg", "png"}
        or reference.expected_byte_size is None
        or reference.expected_sha256 is None
    ):
        reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "signature.asset_reference")
    return reference


def _media_type(content_kind: str) -> SignatureImageMediaType:
    if content_kind == "jpeg":
        return SignatureImageMediaType.JPEG
    if content_kind == "png":
        return SignatureImageMediaType.PNG
    reject_image_page(ImagePageErrorCode.FORMAT_MISMATCH, "signature.source_format")


def _signature_limits(
    context: OperationContext,
    request: SignatureImageRasterOperationRequest,
    source_bytes: int,
) -> ImageNormalizationLimits:
    pixel_capacity = context.limits.output_bytes - SIGNATURE_IMAGE_RASTER_ARTIFACT_HEADER_BYTES
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
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "signature.capacity")
    return ImageNormalizationLimits(
        maximum_source_bytes=source_bytes,
        maximum_encoded_width_pixels=MAX_IMAGE_DIMENSION_PIXELS,
        maximum_encoded_height_pixels=MAX_IMAGE_DIMENSION_PIXELS,
        maximum_source_pixels=min(context.limits.pixel_count, MAX_IMAGE_SOURCE_PIXELS),
        maximum_decompressed_bytes=min(context.limits.memory_bytes, MAX_IMAGE_DECOMPRESSED_BYTES),
        maximum_metadata_records=MAX_IMAGE_METADATA_RECORDS,
        maximum_metadata_bytes=min(MAX_IMAGE_METADATA_BYTES, context.limits.memory_bytes),
        maximum_output_width_pixels=request.maximum_width_pixels,
        maximum_output_height_pixels=request.maximum_height_pixels,
        maximum_output_pixels=maximum_output_pixels,
        maximum_output_bytes=maximum_output_bytes,
        maximum_memory_bytes=min(context.limits.memory_bytes, MAX_IMAGE_MEMORY_BYTES),
        wall_time_ms=min(context.limits.wall_time_ms, MAX_IMAGE_WALL_TIME_MS),
    )


def _write_signature_artifact(
    destination: _MappedOutputSection,
    raster: BinaryIO,
    result: ImageNormalizationResult,
    *,
    header_bytes: bytes,
    maximum_output_bytes: int,
    control: _OperationControl,
) -> tuple[int, str]:
    """Copy one exact straight-alpha raster while rechecking its security invariants."""

    total_bytes = len(header_bytes) + result.bytes_written
    if total_bytes > maximum_output_bytes:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "signature.output_size")
    try:
        raster.flush()
        raster_size = raster.seek(0, os.SEEK_END)
        restored = raster.seek(0)
    except OSError:
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "signature.raster_state")
    if (
        type(raster_size) is not int
        or raster_size != result.bytes_written
        or type(restored) is not int
        or restored != 0
    ):
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "signature.raster_size")

    artifact_digest = hashlib.sha256()
    pixel_digest = hashlib.sha256()
    _write_output_exact(destination, header_bytes, control=control)
    artifact_digest.update(header_bytes)
    copied = 0
    while copied < result.bytes_written:
        _require_active(control)
        wanted = min(_COPY_CHUNK_BYTES, result.bytes_written - copied)
        block = _read_raster_exact(raster, wanted, control=control)
        if len(block) % 4:
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "signature.raster_layout")
        pixels = memoryview(block)
        if any(
            pixels[offset + 3] == 0 and any(pixels[offset : offset + 3])
            for offset in range(0, len(pixels), 4)
        ):
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "signature.raster_hidden_rgb")
        _write_output_exact(destination, block, control=control)
        artifact_digest.update(block)
        pixel_digest.update(block)
        copied += len(block)
    try:
        surplus = raster.read(1)
    except OSError:
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "signature.raster_eof")
    if surplus != b"" or not hmac.compare_digest(
        pixel_digest.hexdigest(),
        result.pixel_sha256,
    ):
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "signature.raster_digest")
    _require_active(control)
    try:
        destination.flush()
    except Exception:
        reject_image_page(ImagePageErrorCode.DESTINATION_FAILED, "signature.output_write")
    if destination.tell() != total_bytes:
        reject_image_page(ImagePageErrorCode.DESTINATION_FAILED, "signature.output_size")
    _require_active(control)
    return total_bytes, artifact_digest.hexdigest()


def _read_raster_exact(
    raster: BinaryIO,
    byte_count: int,
    *,
    control: _OperationControl,
) -> bytes:
    chunks: list[bytes] = []
    remaining = byte_count
    while remaining:
        _require_active(control)
        try:
            block = raster.read(remaining)
        except OSError:
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "signature.raster_read")
        if type(block) is not bytes or not block or len(block) > remaining:
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "signature.raster_read")
        chunks.append(block)
        remaining -= len(block)
    return b"".join(chunks)


def _write_output_exact(
    destination: _MappedOutputSection,
    payload: bytes,
    *,
    control: _OperationControl,
) -> None:
    offset = 0
    try:
        while offset < len(payload):
            _require_active(control)
            written = destination.write(payload[offset:])
            if type(written) is not int or not 1 <= written <= len(payload) - offset:
                reject_image_page(ImagePageErrorCode.DESTINATION_FAILED, "signature.output_write")
            offset += written
    except ImagePageError:
        raise
    except Exception:
        reject_image_page(ImagePageErrorCode.DESTINATION_FAILED, "signature.output_write")


def build_signature_image_raster_operation_handler() -> SignatureImageRasterOperationHandler:
    """Build the exact production signature-raster handler."""

    return SignatureImageRasterOperationHandler(WindowsSignatureImageRasterAdapter())

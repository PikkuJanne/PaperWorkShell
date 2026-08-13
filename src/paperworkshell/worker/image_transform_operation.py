"""Production worker adapter for bounded, handle-brokered scan-image transforms."""

from __future__ import annotations

import hashlib
import hmac
import os
import stat
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import BinaryIO, Final, Protocol, cast

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
    ImageNormalizationRequest,
    ImageNormalizationResult,
    ImagePageError,
    ImagePageErrorCode,
    ImageQuarterTurn,
    ImageRgba8Layout,
    ImageSourceFormat,
    reject_image_page,
    validate_image_normalization_result,
)
from paperworkshell.domain.image_transforms import (
    IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES,
    MAX_IMAGE_TRANSFORM_TEMPORARY_BYTES,
    ImageNormalizedCrop,
    ImageTransformArtifactHeader,
    ImageTransformCacheKey,
    ImageTransformIntent,
    ImageTransformLimits,
    ImageTransformResult,
    encode_image_transform_artifact_header,
    plan_image_transform,
    validate_image_transform_result,
)
from paperworkshell.imaging.pillow_decoder import (
    ImageNormalizationControl,
    PillowImageDecoder,
)
from paperworkshell.imaging.pillow_transformer import (
    ImageTransformControl,
    PillowImageTransformer,
)
from paperworkshell.worker.image_normalize_operation import (
    _close_descriptor_no_raise,
    _close_native_handle_no_raise,
    _MappedOutputSection,
    _materialize_worker_private_temp,
    _require_non_inheritable,
)
from paperworkshell.worker.operations import (
    ImageTransformOperationHandler,
    ImageTransformOperationRequest,
    OperationContext,
)
from paperworkshell.worker.private_temp import (
    OsWorkerPrivateTempStorage,
    WorkerPrivateTempStorage,
    WorkerPrivateTempStorageError,
)
from paperworkshell.worker.protocol.models import (
    IMAGE_TRANSFORM_OUTPUT_CONTENT_KIND,
    CaseObjectReference,
    ReferenceArea,
)

if os.name == "nt":
    import msvcrt

_COPY_CHUNK_BYTES: Final = 1024 * 1024


class ImageDecoder(Protocol):
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


class ImageTransformer(Protocol):
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
        control: ImageTransformControl,
    ) -> ImageTransformResult: ...


@dataclass(frozen=True, slots=True)
class _OperationControl:
    context: OperationContext

    def cancellation_requested(self) -> bool:
        return self.context.cancel_requested.is_set()

    def deadline_expired(self) -> bool:
        try:
            value = self.context.deadline_expired()
        except Exception:
            reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.transform_deadline_probe")
        if type(value) is not bool:
            reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.transform_deadline_probe")
        return value


@dataclass(frozen=True, slots=True)
class WindowsImageTransformAdapter:
    """Decode privately, transform privately, and emit one fixed authenticated artifact."""

    decoder: ImageDecoder = field(default_factory=PillowImageDecoder)
    transformer: ImageTransformer = field(default_factory=PillowImageTransformer)
    private_temp_storage: WorkerPrivateTempStorage = field(
        default_factory=OsWorkerPrivateTempStorage,
        repr=False,
    )

    def __post_init__(self) -> None:
        if (
            not callable(getattr(self.decoder, "normalize", None))
            or not callable(getattr(self.transformer, "transform", None))
            or any(
                not callable(getattr(self.private_temp_storage, member, None))
                for member in ("require_capacity", "open_anonymous")
            )
        ):
            reject_image_page(ImagePageErrorCode.MODEL_INVALID, "image.transform_adapter")

    def discard_handles(
        self,
        context: OperationContext,
        request: ImageTransformOperationRequest,
    ) -> None:
        del context
        try:
            _close_native_handle_no_raise(request.source_handle)
        finally:
            _close_native_handle_no_raise(request.output_handle)

    def transform(
        self,
        context: OperationContext,
        request: ImageTransformOperationRequest,
    ) -> CaseObjectReference:
        source_owned = request.source_handle
        output_owned = request.output_handle
        source_descriptor: int | None = None
        try:
            if os.name != "nt":
                reject_image_page(ImagePageErrorCode.DEPENDENCY_UNAVAILABLE, "image.platform")
            _require_non_inheritable(
                source_owned,
                context="image.transform_source_handle",
                code=ImagePageErrorCode.SOURCE_INVALID,
            )
            _require_non_inheritable(
                output_owned,
                context="image.transform_output_handle",
            )
            try:
                source_descriptor = msvcrt.open_osfhandle(
                    source_owned,
                    os.O_RDONLY | os.O_BINARY,
                )
                source_owned = 0
            except OSError, OverflowError, ValueError:
                reject_image_page(
                    ImagePageErrorCode.SOURCE_INVALID,
                    "image.transform_source_open",
                )
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
                return self._transform_open_streams(
                    context,
                    request,
                    cast(BinaryIO, source),
                    destination,
                )
        except ImagePageError:
            raise
        except MemoryError, RecursionError:
            reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.transform_resources")
        except Exception:
            reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.transform_adapter")
        finally:
            if source_descriptor is not None:
                _close_descriptor_no_raise(source_descriptor)
            elif source_owned > 0:
                _close_native_handle_no_raise(source_owned)
            if output_owned > 0:
                _close_native_handle_no_raise(output_owned)

    def _transform_open_streams(
        self,
        context: OperationContext,
        request: ImageTransformOperationRequest,
        source: BinaryIO,
        destination: _MappedOutputSection,
    ) -> CaseObjectReference:
        reference = _source_reference(context)
        expected_size = reference.expected_byte_size
        expected_digest = reference.expected_sha256
        if expected_size is None or expected_digest is None:
            reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "image.transform_source")
        try:
            source_info = os.fstat(source.fileno())
        except OSError, OverflowError, ValueError:
            reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "image.transform_identity")
        if not stat.S_ISREG(source_info.st_mode) or source_info.st_size != expected_size:
            reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "image.transform_identity")
        if destination.capacity != context.limits.output_bytes or destination.tell() != 0:
            reject_image_page(ImagePageErrorCode.DESTINATION_FAILED, "image.transform_output")

        source_format = _source_format(reference.content_kind)
        intent = _transform_intent(request)
        decode_request = ImageNormalizationRequest(
            expected_source_format=source_format,
            expected_source_bytes=expected_size,
            expected_source_sha256=expected_digest,
            maximum_width_pixels=MAX_IMAGE_DIMENSION_PIXELS,
            maximum_height_pixels=MAX_IMAGE_DIMENSION_PIXELS,
            additional_rotation=ImageQuarterTurn.DEGREES_0,
        )
        transform_limits = _transform_limits(context, request)
        decode_limits = _decode_limits(
            context,
            expected_size,
            maximum_normalized_bytes=transform_limits.maximum_temporary_storage_bytes,
        )
        control = _OperationControl(context)
        _require_active(control)
        temp_root = _materialize_worker_private_temp()
        required_temp = transform_limits.maximum_temporary_storage_bytes
        try:
            self.private_temp_storage.require_capacity(temp_root, required_temp)
        except WorkerPrivateTempStorageError:
            reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.transform_temp_capacity")
        with ExitStack() as temporary_files:
            try:
                normalized_raster = temporary_files.enter_context(
                    self.private_temp_storage.open_anonymous(temp_root)
                )
                transformed_raster = temporary_files.enter_context(
                    self.private_temp_storage.open_anonymous(temp_root)
                )
            except WorkerPrivateTempStorageError, OSError:
                reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.transform_temp_create")

            normalized = self.decoder.normalize(
                source,
                normalized_raster,
                expected_format=source_format,
                request=decode_request,
                limits=decode_limits,
                control=control,
            )
            validate_image_normalization_result(decode_request, decode_limits, normalized)
            source_layout = ImageRgba8Layout(
                normalized.width_pixels,
                normalized.height_pixels,
                normalized.row_stride_bytes,
                normalized.width_pixels * normalized.height_pixels,
                normalized.bytes_written,
            )
            plan = plan_image_transform(
                source_layout,
                intent,
                request.maximum_width_pixels,
                request.maximum_height_pixels,
                transform_limits,
            )
            _require_stream_size(normalized_raster, normalized.bytes_written, "normalized")
            normalized_raster.seek(0)
            transformed = self.transformer.transform(
                normalized_raster,
                transformed_raster,
                source_layout=source_layout,
                intent=intent,
                maximum_width_pixels=request.maximum_width_pixels,
                maximum_height_pixels=request.maximum_height_pixels,
                limits=transform_limits,
                control=control,
            )
            validate_image_transform_result(plan, transformed)
            cache_key = ImageTransformCacheKey(
                case_id=context.case_id,
                source_id=reference.object_id,
                page_id=request.page_id,
                source_byte_size=expected_size,
                source_sha256=expected_digest,
                source_format=source_format,
                source_layout=source_layout,
                source_pixel_sha256=normalized.pixel_sha256,
                exif_orientation=normalized.metadata.exif_orientation,
                intent=intent,
                maximum_width_pixels=request.maximum_width_pixels,
                maximum_height_pixels=request.maximum_height_pixels,
            )
            header = ImageTransformArtifactHeader(
                operation_id=context.operation_id,
                output_id=request.output_object_id,
                cache_key=cache_key,
                result=transformed,
            )
            artifact_size, artifact_sha = _write_artifact(
                destination,
                transformed_raster,
                transformed,
                header_bytes=encode_image_transform_artifact_header(header),
                maximum_output_bytes=context.limits.output_bytes,
                control=control,
            )
        return CaseObjectReference(
            area=ReferenceArea.OPERATION_STAGING,
            object_id=request.output_object_id,
            operation_id=context.operation_id,
            content_kind=IMAGE_TRANSFORM_OUTPUT_CONTENT_KIND,
            expected_byte_size=artifact_size,
            expected_sha256=artifact_sha,
        )


def _source_reference(context: OperationContext) -> CaseObjectReference:
    if len(context.inputs) != 1 or type(context.inputs[0]) is not CaseObjectReference:
        reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "image.transform_reference")
    reference = context.inputs[0]
    if (
        reference.area is not ReferenceArea.SOURCE_READ
        or reference.operation_id is not None
        or reference.content_kind not in {"jpeg", "png"}
        or reference.expected_byte_size is None
        or reference.expected_sha256 is None
    ):
        reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "image.transform_reference")
    return reference


def _source_format(content_kind: str) -> ImageSourceFormat:
    if content_kind == "jpeg":
        return ImageSourceFormat.JPEG
    if content_kind == "png":
        return ImageSourceFormat.PNG
    reject_image_page(ImagePageErrorCode.FORMAT_MISMATCH, "image.transform_format")


def _transform_intent(request: ImageTransformOperationRequest) -> ImageTransformIntent:
    return ImageTransformIntent(
        crop=ImageNormalizedCrop(
            request.crop_x_millionths,
            request.crop_y_millionths,
            request.crop_width_millionths,
            request.crop_height_millionths,
        ),
        additional_rotation=ImageQuarterTurn(request.quarter_turns_clockwise * 90),
        deskew_angle_millidegrees_clockwise=request.deskew_millidegrees_clockwise,
        contrast_factor_millionths=request.contrast_factor_millionths,
        background_cleanup_strength_millionths=(request.background_cleanup_strength_millionths),
    )


def _decode_limits(
    context: OperationContext,
    source_bytes: int,
    *,
    maximum_normalized_bytes: int,
) -> ImageNormalizationLimits:
    memory = min(context.limits.memory_bytes, MAX_IMAGE_MEMORY_BYTES)
    normalized_bytes = min(maximum_normalized_bytes, memory, MAX_IMAGE_OUTPUT_BYTES)
    normalized_pixels = normalized_bytes // IMAGE_RGBA8_BYTES_PER_PIXEL
    if normalized_pixels < 1:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.transform_temp")
    return ImageNormalizationLimits(
        maximum_source_bytes=min(source_bytes, MAX_IMAGE_SOURCE_BYTES),
        maximum_encoded_width_pixels=MAX_IMAGE_DIMENSION_PIXELS,
        maximum_encoded_height_pixels=MAX_IMAGE_DIMENSION_PIXELS,
        maximum_source_pixels=min(
            context.limits.pixel_count,
            MAX_IMAGE_SOURCE_PIXELS,
            normalized_pixels,
        ),
        maximum_decompressed_bytes=min(memory, MAX_IMAGE_DECOMPRESSED_BYTES),
        maximum_metadata_records=MAX_IMAGE_METADATA_RECORDS,
        maximum_metadata_bytes=min(memory, MAX_IMAGE_METADATA_BYTES),
        maximum_output_width_pixels=MAX_IMAGE_DIMENSION_PIXELS,
        maximum_output_height_pixels=MAX_IMAGE_DIMENSION_PIXELS,
        maximum_output_pixels=min(
            context.limits.pixel_count,
            MAX_IMAGE_OUTPUT_PIXELS,
            normalized_pixels,
        ),
        maximum_output_bytes=normalized_bytes,
        maximum_memory_bytes=memory,
        wall_time_ms=min(context.limits.wall_time_ms, MAX_IMAGE_WALL_TIME_MS),
    )


def _transform_limits(
    context: OperationContext,
    request: ImageTransformOperationRequest,
) -> ImageTransformLimits:
    output_capacity = context.limits.output_bytes - IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES
    if output_capacity < IMAGE_RGBA8_BYTES_PER_PIXEL:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.transform_output_capacity")
    return ImageTransformLimits(
        maximum_source_width_pixels=MAX_IMAGE_DIMENSION_PIXELS,
        maximum_source_height_pixels=MAX_IMAGE_DIMENSION_PIXELS,
        maximum_source_pixels=min(context.limits.pixel_count, MAX_IMAGE_OUTPUT_PIXELS),
        maximum_intermediate_pixels=min(context.limits.pixel_count, MAX_IMAGE_OUTPUT_PIXELS),
        maximum_output_width_pixels=request.maximum_width_pixels,
        maximum_output_height_pixels=request.maximum_height_pixels,
        maximum_output_pixels=min(
            context.limits.pixel_count,
            request.maximum_width_pixels * request.maximum_height_pixels,
            output_capacity // IMAGE_RGBA8_BYTES_PER_PIXEL,
        ),
        maximum_output_bytes=min(output_capacity, MAX_IMAGE_OUTPUT_BYTES),
        maximum_memory_bytes=min(context.limits.memory_bytes, MAX_IMAGE_MEMORY_BYTES),
        maximum_temporary_storage_bytes=min(
            context.limits.temporary_storage_bytes,
            MAX_IMAGE_TRANSFORM_TEMPORARY_BYTES,
        ),
        wall_time_ms=min(context.limits.wall_time_ms, MAX_IMAGE_WALL_TIME_MS),
    )


def _require_stream_size(stream: BinaryIO, expected: int, suffix: str) -> None:
    try:
        size = stream.seek(0, os.SEEK_END)
    except OSError:
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, f"image.transform_{suffix}_state")
    if type(size) is not int or size != expected:
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, f"image.transform_{suffix}_size")


def _write_artifact(
    destination: _MappedOutputSection,
    raster: BinaryIO,
    result: ImageTransformResult,
    *,
    header_bytes: bytes,
    maximum_output_bytes: int,
    control: _OperationControl,
) -> tuple[int, str]:
    total = len(header_bytes) + result.bytes_written
    if total > maximum_output_bytes:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.transform_artifact_size")
    _require_stream_size(raster, result.bytes_written, "raster")
    raster.seek(0)
    artifact_digest = hashlib.sha256()
    pixel_digest = hashlib.sha256()
    _require_active(control)
    _write_exact(destination, header_bytes)
    artifact_digest.update(header_bytes)
    copied = 0
    while copied < result.bytes_written:
        _require_active(control)
        wanted = min(_COPY_CHUNK_BYTES, result.bytes_written - copied)
        try:
            block = raster.read(wanted)
        except OSError:
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_raster_read")
        if type(block) is not bytes or not block or len(block) > wanted:
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_raster_read")
        if len(block) % 4 != 0 or block[3::4].count(255) != len(block) // 4:
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_raster_alpha")
        _write_exact(destination, block)
        artifact_digest.update(block)
        pixel_digest.update(block)
        copied += len(block)
    try:
        surplus = raster.read(1)
    except OSError:
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_raster_eof")
    if surplus != b"" or not hmac.compare_digest(
        pixel_digest.hexdigest(),
        result.pixel_sha256,
    ):
        reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_raster_digest")
    _require_active(control)
    destination.flush()
    if destination.tell() != total:
        reject_image_page(ImagePageErrorCode.DESTINATION_FAILED, "image.transform_output_size")
    _require_active(control)
    return total, artifact_digest.hexdigest()


def _write_exact(destination: _MappedOutputSection, payload: bytes) -> None:
    offset = 0
    try:
        while offset < len(payload):
            count = destination.write(payload[offset:])
            if type(count) is not int or not 1 <= count <= len(payload) - offset:
                reject_image_page(ImagePageErrorCode.DESTINATION_FAILED, "image.transform_output")
            offset += count
    except ImagePageError:
        raise
    except Exception:
        reject_image_page(ImagePageErrorCode.DESTINATION_FAILED, "image.transform_output")


def _require_active(control: ImageTransformControl) -> None:
    if control.cancellation_requested():
        reject_image_page(ImagePageErrorCode.CANCELLED, "image.transform_operation")
    if control.deadline_expired():
        reject_image_page(ImagePageErrorCode.TIMED_OUT, "image.transform_operation")


def build_image_transform_operation_handler() -> ImageTransformOperationHandler:
    return ImageTransformOperationHandler(WindowsImageTransformAdapter())

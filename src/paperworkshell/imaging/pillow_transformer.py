"""Pinned Pillow adapter for one deterministic raw-RGBA8 transform pipeline."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import BinaryIO, Protocol

import PIL
from PIL import Image

from paperworkshell.domain.image_pages import (
    ImagePageError,
    ImagePageErrorCode,
    ImageQuarterTurn,
    ImageRgba8Layout,
    reject_image_page,
)
from paperworkshell.domain.image_transforms import (
    IDENTITY_CONTRAST_FACTOR_MILLIONTHS,
    IMAGE_TRANSFORM_BACKGROUND_THRESHOLD,
    IMAGE_TRANSFORM_ENGINE_PROFILE_SHA256,
    IMAGE_TRANSFORM_PILLOW_VERSION,
    MAX_BACKGROUND_CLEANUP_STRENGTH_MILLIONTHS,
    ImageTransformIntent,
    ImageTransformLimits,
    ImageTransformPlan,
    ImageTransformResult,
    plan_image_transform,
    validate_image_transform_result,
)

_MILLION: int = 1_000_000
_RGBA_BYTES_PER_PIXEL: int = 4
_COPY_CHUNK_BYTES: int = 64 * 1024


class ImageTransformControl(Protocol):
    """Cooperative probes checked around bounded Python transform phases."""

    def cancellation_requested(self) -> bool: ...

    def deadline_expired(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class PillowImageTransformer:
    """Transform one authenticated, EXIF-oriented, opaque raw RGBA8 raster."""

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
    ) -> ImageTransformResult:
        """Apply the closed profile-v1 pipeline without owning either stream."""

        plan = plan_image_transform(
            source_layout,
            intent,
            maximum_width_pixels,
            maximum_height_pixels,
            limits,
        )
        _require_approved_runtime()
        _require_active(control)
        original_position = _stream_position(source_rgba, "image.transform_source_position")
        result: ImageTransformResult | None = None
        operation_error: BaseException | None = None
        try:
            _seek_exact(source_rgba, 0, "image.transform_source_seek")
            source_pixels = _read_source_raster(source_rgba, source_layout, control)
            _require_active(control)
            result = _transform_pixels(
                source_pixels,
                destination,
                plan=plan,
                control=control,
            )
        except BaseException as error:
            operation_error = error

        restore_error = _restore_source(source_rgba, original_position)
        if restore_error is not None:
            raise restore_error
        if operation_error is not None:
            if isinstance(operation_error, ImagePageError):
                raise operation_error
            if isinstance(operation_error, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                raise operation_error
            _map_failure(operation_error, "image.transform_dependency")
        if result is None:
            reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.transform_result")
        return result


def _transform_pixels(
    source_pixels: bytes,
    destination: BinaryIO,
    *,
    plan: ImageTransformPlan,
    control: ImageTransformControl,
) -> ImageTransformResult:
    image: Image.Image | None = None
    working: Image.Image | None = None
    serialized: bytes | None = None
    try:
        try:
            image = Image.frombytes(
                "RGBA",
                (plan.source_layout.width_pixels, plan.source_layout.height_pixels),
                source_pixels,
                "raw",
                "RGBA",
            )
        except MemoryError:
            reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.transform_memory")
        except Exception:
            reject_image_page(ImagePageErrorCode.DEPENDENCY_FAILED, "image.transform_source")
        del source_pixels
        working = image
        image = None
        _require_active(control)

        if plan.crop_box_pixels != (
            0,
            0,
            plan.source_layout.width_pixels,
            plan.source_layout.height_pixels,
        ):
            cropped = _pillow_crop(working, plan.crop_box_pixels)
            working = _replace_image(working, cropped)
        _require_active(control)

        rotated = _apply_quarter_turn(working, plan.intent.additional_rotation)
        working = _replace_image(working, rotated)
        _require_active(control)

        if plan.intent.deskew_angle_millidegrees_clockwise != 0:
            deskewed = _deskew(
                working,
                plan.intent.deskew_angle_millidegrees_clockwise,
            )
            working = _replace_image(working, deskewed)
        _require_active(control)

        if plan.intent.contrast_factor_millionths != IDENTITY_CONTRAST_FACTOR_MILLIONTHS:
            contrasted = _apply_lut(
                working,
                _contrast_lut(plan.intent.contrast_factor_millionths),
                "image.transform_contrast",
            )
            working = _replace_image(working, contrasted)
        _require_active(control)

        if plan.intent.background_cleanup_strength_millionths != 0:
            cleaned = _apply_lut(
                working,
                _background_cleanup_lut(plan.intent.background_cleanup_strength_millionths),
                "image.transform_cleanup",
            )
            working = _replace_image(working, cleaned)
        _require_active(control)

        expected_size = (
            plan.output_layout.width_pixels,
            plan.output_layout.height_pixels,
        )
        if working.size != expected_size:
            try:
                fitted = working.resize(
                    expected_size,
                    resample=Image.Resampling.LANCZOS,
                    reducing_gap=3.0,
                )
            except MemoryError:
                reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.transform_memory")
            except Exception:
                reject_image_page(ImagePageErrorCode.DEPENDENCY_FAILED, "image.transform_fit")
            working = _replace_image(working, fitted)
        _require_active(control)

        try:
            serialized = working.tobytes("raw", "RGBA")
        except MemoryError:
            reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.transform_memory")
        except Exception:
            reject_image_page(ImagePageErrorCode.DEPENDENCY_FAILED, "image.transform_serialize")
        _close_image_no_raise(working)
        working = None
        if len(serialized) != plan.output_layout.byte_count or any(
            alpha != 255 for alpha in memoryview(serialized)[3::4]
        ):
            reject_image_page(ImagePageErrorCode.RESULT_INVALID, "image.transform_pixels")
        pixel_sha256 = _write_exact(
            destination,
            serialized,
            plan.output_layout.row_stride_bytes,
            control,
        )
        del serialized
        serialized = None
        result = ImageTransformResult(
            source_width_pixels=plan.source_layout.width_pixels,
            source_height_pixels=plan.source_layout.height_pixels,
            crop_left_pixels=plan.crop_left_pixels,
            crop_top_pixels=plan.crop_top_pixels,
            crop_right_pixels=plan.crop_right_pixels,
            crop_bottom_pixels=plan.crop_bottom_pixels,
            rotated_width_pixels=plan.rotated_width_pixels,
            rotated_height_pixels=plan.rotated_height_pixels,
            width_pixels=plan.output_layout.width_pixels,
            height_pixels=plan.output_layout.height_pixels,
            row_stride_bytes=plan.output_layout.row_stride_bytes,
            bytes_written=plan.output_layout.byte_count,
            pixel_sha256=pixel_sha256,
            intent_sha256=plan.intent.intent_sha256,
            engine_profile_sha256=IMAGE_TRANSFORM_ENGINE_PROFILE_SHA256,
        )
        validate_image_transform_result(plan, result)
        return result
    except ImagePageError:
        raise
    except MemoryError:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.transform_memory")
    except Exception:
        reject_image_page(ImagePageErrorCode.DEPENDENCY_FAILED, "image.transform_dependency")
    finally:
        del serialized
        _close_image_no_raise(working)
        _close_image_no_raise(image)


def _read_source_raster(
    source: BinaryIO,
    layout: ImageRgba8Layout,
    control: ImageTransformControl,
) -> bytes:
    chunks: list[bytes] = []
    received = 0
    while received < layout.byte_count:
        _require_active(control)
        wanted = min(_COPY_CHUNK_BYTES, layout.byte_count - received)
        try:
            chunk = source.read(wanted)
        except Exception:
            reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "image.transform_source_read")
        if type(chunk) is not bytes or not chunk or len(chunk) > wanted:
            reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "image.transform_source_length")
        chunks.append(chunk)
        received += len(chunk)
    try:
        surplus = source.read(1)
    except Exception:
        reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "image.transform_source_read")
    if surplus != b"":
        reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "image.transform_source_length")
    try:
        pixels = b"".join(chunks)
    except MemoryError:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.transform_memory")
    if any(alpha != 255 for alpha in memoryview(pixels)[3::4]):
        reject_image_page(ImagePageErrorCode.SOURCE_INVALID, "image.transform_source_alpha")
    return pixels


def _pillow_crop(image: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    try:
        return image.crop(box)
    except MemoryError:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.transform_memory")
    except Exception:
        reject_image_page(ImagePageErrorCode.DEPENDENCY_FAILED, "image.transform_crop")


def _apply_quarter_turn(image: Image.Image, rotation: ImageQuarterTurn) -> Image.Image:
    transform = {
        ImageQuarterTurn.DEGREES_0: None,
        ImageQuarterTurn.DEGREES_90: Image.Transpose.ROTATE_270,
        ImageQuarterTurn.DEGREES_180: Image.Transpose.ROTATE_180,
        ImageQuarterTurn.DEGREES_270: Image.Transpose.ROTATE_90,
    }[rotation]
    if transform is None:
        return image
    try:
        return image.transpose(transform)
    except MemoryError:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.transform_memory")
    except Exception:
        reject_image_page(ImagePageErrorCode.DEPENDENCY_FAILED, "image.transform_rotation")


def _deskew(image: Image.Image, angle_millidegrees_clockwise: int) -> Image.Image:
    try:
        return image.rotate(
            -(angle_millidegrees_clockwise / 1_000.0),
            resample=Image.Resampling.BICUBIC,
            expand=False,
            fillcolor=(255, 255, 255, 255),
        )
    except MemoryError:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.transform_memory")
    except Exception:
        reject_image_page(ImagePageErrorCode.DEPENDENCY_FAILED, "image.transform_deskew")


def _contrast_lut(factor_millionths: int) -> tuple[int, ...]:
    values: list[int] = []
    for value in range(256):
        numerator = (value - 128) * factor_millionths
        adjusted = (
            (numerator + _MILLION // 2) // _MILLION
            if numerator >= 0
            else -((-numerator + _MILLION // 2) // _MILLION)
        )
        values.append(min(255, max(0, 128 + adjusted)))
    return tuple(values + values + values + list(range(256)))


def _background_cleanup_lut(strength_millionths: int) -> tuple[int, ...]:
    values: list[int] = []
    threshold_range = 255 - IMAGE_TRANSFORM_BACKGROUND_THRESHOLD
    denominator = threshold_range * MAX_BACKGROUND_CLEANUP_STRENGTH_MILLIONTHS
    for value in range(256):
        if value <= IMAGE_TRANSFORM_BACKGROUND_THRESHOLD:
            values.append(value)
            continue
        numerator = (
            (255 - value) * (value - IMAGE_TRANSFORM_BACKGROUND_THRESHOLD) * strength_millionths
        )
        values.append(min(255, value + (numerator + denominator // 2) // denominator))
    return tuple(values + values + values + list(range(256)))


def _apply_lut(image: Image.Image, lut: tuple[int, ...], context: str) -> Image.Image:
    try:
        return image.point(lut)
    except MemoryError:
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.transform_memory")
    except Exception:
        reject_image_page(ImagePageErrorCode.DEPENDENCY_FAILED, context)


def _replace_image(current: Image.Image, replacement: Image.Image) -> Image.Image:
    if replacement is not current:
        _close_image_no_raise(current)
    return replacement


def _close_image_no_raise(image: Image.Image | None) -> None:
    if image is None:
        return
    try:
        image.close()
    except Exception:
        pass


def _write_exact(
    destination: BinaryIO,
    pixels: bytes,
    row_stride_bytes: int,
    control: ImageTransformControl,
) -> str:
    digest = hashlib.sha256()
    view = memoryview(pixels)
    offset = 0
    try:
        while offset < len(pixels):
            _require_active(control)
            end = min(offset + row_stride_bytes, len(pixels))
            count = destination.write(bytes(view[offset:end]))
            if type(count) is not int or not 1 <= count <= end - offset:
                reject_image_page(
                    ImagePageErrorCode.DESTINATION_FAILED,
                    "image.transform_destination_write",
                )
            digest.update(view[offset : offset + count])
            offset += count
        _require_active(control)
        destination.flush()
        _require_active(control)
    except ImagePageError:
        raise
    except Exception:
        reject_image_page(
            ImagePageErrorCode.DESTINATION_FAILED,
            "image.transform_destination_write",
        )
    return digest.hexdigest()


def _require_approved_runtime() -> None:
    try:
        version = PIL.__version__
    except Exception:
        reject_image_page(ImagePageErrorCode.DEPENDENCY_UNAVAILABLE, "image.transform_runtime")
    if type(version) is not str or version != IMAGE_TRANSFORM_PILLOW_VERSION:
        reject_image_page(ImagePageErrorCode.DEPENDENCY_UNAVAILABLE, "image.transform_runtime")


def _require_active(control: ImageTransformControl) -> None:
    try:
        cancelled = control.cancellation_requested()
    except ImagePageError:
        raise
    except Exception:
        reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.transform_cancel_probe")
    if type(cancelled) is not bool:
        reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.transform_cancel_probe")
    if cancelled:
        reject_image_page(ImagePageErrorCode.CANCELLED, "image.transform_cancelled")
    try:
        expired = control.deadline_expired()
    except ImagePageError:
        raise
    except Exception:
        reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.transform_deadline_probe")
    if type(expired) is not bool:
        reject_image_page(ImagePageErrorCode.INTERNAL_ERROR, "image.transform_deadline_probe")
    if expired:
        reject_image_page(ImagePageErrorCode.TIMED_OUT, "image.transform_deadline")


def _stream_position(source: BinaryIO, context: str) -> int:
    try:
        position = source.tell()
    except Exception:
        reject_image_page(ImagePageErrorCode.SOURCE_INVALID, context)
    if type(position) is not int or position < 0:
        reject_image_page(ImagePageErrorCode.SOURCE_INVALID, context)
    return position


def _seek_exact(source: BinaryIO, offset: int, context: str) -> None:
    try:
        position = source.seek(offset, os.SEEK_SET)
    except Exception:
        reject_image_page(ImagePageErrorCode.SOURCE_INVALID, context)
    if type(position) is not int or position != offset:
        reject_image_page(ImagePageErrorCode.SOURCE_INVALID, context)


def _restore_source(source: BinaryIO, position: int) -> ImagePageError | None:
    try:
        restored = source.seek(position, os.SEEK_SET)
        if type(restored) is int and restored == position:
            return None
    except Exception:
        pass
    return ImagePageError(ImagePageErrorCode.SOURCE_INVALID, "image.transform_source_restore")


def _map_failure(error: BaseException, context: str) -> None:
    if isinstance(error, (MemoryError, RecursionError)):
        reject_image_page(ImagePageErrorCode.LIMIT_EXCEEDED, "image.transform_memory")
    reject_image_page(ImagePageErrorCode.DEPENDENCY_FAILED, context)

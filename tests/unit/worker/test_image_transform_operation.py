"""Focused transform-adapter tests without native Windows handle calls."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event
from typing import BinaryIO, cast
from uuid import UUID

import pytest
from PIL import Image

from paperworkshell.domain.image_pages import (
    ImageAlphaNormalizationPolicy,
    ImageExifOrientation,
    ImageNormalizationLimits,
    ImageNormalizationRequest,
    ImageNormalizationResult,
    ImagePageError,
    ImagePageErrorCode,
    ImagePageMetadata,
    ImageQuarterTurn,
    ImageRgba8Layout,
    ImageSourceColorModel,
    ImageSourceFormat,
)
from paperworkshell.domain.image_transforms import (
    IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES,
    ImageNormalizedCrop,
    ImageTransformIntent,
    ImageTransformLimits,
    ImageTransformResult,
    decode_image_transform_artifact,
    plan_image_transform,
)
from paperworkshell.imaging.pillow_decoder import ImageNormalizationControl
from paperworkshell.imaging.pillow_transformer import (
    ImageTransformControl,
    PillowImageTransformer,
)
from paperworkshell.worker.image_transform_operation import WindowsImageTransformAdapter
from paperworkshell.worker.operations import ImageTransformOperationRequest, OperationContext
from paperworkshell.worker.private_temp import (
    OsWorkerPrivateTempStorage,
    WorkerPrivateTempStorageError,
)
from paperworkshell.worker.protocol.models import (
    IMAGE_TRANSFORM_OUTPUT_CONTENT_KIND,
    CaseObjectReference,
    ReferenceArea,
    ResourceLimits,
)

_NORMALIZED_PIXELS = bytes((1, 2, 3, 255)) * 4


def _uuid(number: int) -> UUID:
    return UUID(f"8c000000-0000-4000-8000-{number:012d}")


@dataclass(slots=True)
class _Decoder:
    calls: int = 0
    limits: ImageNormalizationLimits | None = None

    def normalize(
        self,
        source: BinaryIO,
        destination: BinaryIO,
        *,
        expected_format: ImageSourceFormat,
        request: ImageNormalizationRequest,
        limits: ImageNormalizationLimits,
        control: ImageNormalizationControl,
    ) -> ImageNormalizationResult:
        del source, control
        self.calls += 1
        self.limits = limits
        assert expected_format is ImageSourceFormat.JPEG
        assert request.additional_rotation is ImageQuarterTurn.DEGREES_0
        destination.write(_NORMALIZED_PIXELS)
        metadata = ImagePageMetadata(
            source_format=ImageSourceFormat.JPEG,
            source_color_model=ImageSourceColorModel.RGB,
            alpha_policy=ImageAlphaNormalizationPolicy.COMPOSITE_ON_OPAQUE_WHITE,
            exif_orientation=ImageExifOrientation.NORMAL,
            encoded_width_pixels=2,
            encoded_height_pixels=2,
            oriented_width_pixels=2,
            oriented_height_pixels=2,
            component_count=3,
            bits_per_component=8,
            frame_count=1,
            metadata_record_count=0,
            metadata_byte_count=0,
            has_alpha=False,
            has_embedded_color_profile=False,
            has_exif=False,
        )
        return ImageNormalizationResult(
            metadata=metadata,
            additional_rotation=ImageQuarterTurn.DEGREES_0,
            width_pixels=2,
            height_pixels=2,
            row_stride_bytes=8,
            bytes_written=len(_NORMALIZED_PIXELS),
            pixel_sha256=hashlib.sha256(_NORMALIZED_PIXELS).hexdigest(),
        )


@dataclass(slots=True)
class _Transformer:
    cancel_event: Event | None = None
    invalidate_result: bool = False
    calls: int = 0
    intent: ImageTransformIntent | None = None

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
        del control
        self.calls += 1
        self.intent = intent
        assert source_rgba.read() == _NORMALIZED_PIXELS
        plan = plan_image_transform(
            source_layout,
            intent,
            maximum_width_pixels,
            maximum_height_pixels,
            limits,
        )
        assert plan.output_layout.byte_count == len(_NORMALIZED_PIXELS)
        destination.write(_NORMALIZED_PIXELS)
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
            pixel_sha256=hashlib.sha256(_NORMALIZED_PIXELS).hexdigest(),
            intent_sha256=plan.intent.intent_sha256,
        )
        if self.cancel_event is not None:
            self.cancel_event.set()
        if self.invalidate_result:
            return replace(result, intent_sha256="0" * 64)
        return result


class _TrackedBytesIO(io.BytesIO):
    pass


class _CancelAtProbe:
    def __init__(self, cancel_after: int) -> None:
        self.cancel_after = cancel_after
        self.cancel_checks = 0

    def cancellation_requested(self) -> bool:
        self.cancel_checks += 1
        return self.cancel_checks >= self.cancel_after

    def deadline_expired(self) -> bool:
        return False


class _TempStorage:
    def __init__(
        self, *, refuse_capacity: bool = False, fail_open_number: int | None = None
    ) -> None:
        self.refuse_capacity = refuse_capacity
        self.fail_open_number = fail_open_number
        self.required: list[int] = []
        self.streams: list[_TrackedBytesIO] = []

    def require_capacity(self, _root: Path, byte_count: int) -> None:
        self.required.append(byte_count)
        if self.refuse_capacity:
            raise WorkerPrivateTempStorageError

    def open_anonymous(self, _root: Path) -> BinaryIO:
        open_number = len(self.streams) + 1
        if open_number == self.fail_open_number:
            raise WorkerPrivateTempStorageError
        stream = _TrackedBytesIO()
        self.streams.append(stream)
        return stream

    def open_named(self, root: Path, *, suffix: str) -> tuple[BinaryIO, Path]:
        return _TrackedBytesIO(), root / f"unused{suffix}"


class _DiskFullStream:
    """Write one real partial raster, then raise a content-bearing ENOSPC."""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream

    @property
    def closed(self) -> bool:
        return self._stream.closed

    def __enter__(self) -> _DiskFullStream:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def write(self, payload: bytes, /) -> int:
        self._stream.write(payload[: max(1, len(payload) // 2)])
        raise OSError(28, "sensitive transform raster disk-full detail")

    def close(self) -> None:
        self._stream.close()

    def __getattr__(self, member: str) -> object:
        return getattr(self._stream, member)


class _DiskFullOnceTempStorage:
    def __init__(self) -> None:
        self._storage = OsWorkerPrivateTempStorage(
            available_bytes_probe=lambda _root: 1024 * 1024,
            minimum_free_space_reserve_bytes=0,
        )
        self._fail_next_anonymous = True
        self.failed_stream: _DiskFullStream | None = None

    def require_capacity(self, root: Path, byte_count: int) -> None:
        self._storage.require_capacity(root, byte_count)

    def open_anonymous(self, root: Path) -> BinaryIO:
        stream = self._storage.open_anonymous(root)
        if self._fail_next_anonymous:
            self._fail_next_anonymous = False
            failed = _DiskFullStream(stream)
            self.failed_stream = failed
            return cast(BinaryIO, failed)
        return stream

    def open_named(self, root: Path, *, suffix: str) -> tuple[BinaryIO, Path]:
        return self._storage.open_named(root, suffix=suffix)


class _Destination:
    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self.payload = bytearray()

    def write(self, payload: bytes, /) -> int:
        assert len(self.payload) + len(payload) <= self.capacity
        self.payload.extend(payload)
        return len(payload)

    def tell(self) -> int:
        return len(self.payload)

    def flush(self) -> None:
        return None


def _context(
    payload: bytes,
    *,
    temporary_storage_bytes: int = 32,
) -> OperationContext:
    return OperationContext(
        operation_id=_uuid(10),
        case_id=_uuid(1),
        limits=ResourceLimits(
            wall_time_ms=1_000,
            cpu_time_ms=500,
            memory_bytes=1_024 * 1_024,
            temporary_storage_bytes=temporary_storage_bytes,
            output_bytes=IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES + 16,
            page_count=1,
            pixel_count=16,
        ),
        cancel_requested=Event(),
        report_progress=lambda _value: None,
        deadline_expired=lambda: False,
        inputs=(
            CaseObjectReference(
                ReferenceArea.SOURCE_READ,
                _uuid(2),
                None,
                "jpeg",
                len(payload),
                hashlib.sha256(payload).hexdigest(),
            ),
        ),
    )


def _request() -> ImageTransformOperationRequest:
    return ImageTransformOperationRequest(
        source_handle=101,
        output_handle=202,
        output_object_id=_uuid(20),
        page_id=_uuid(3),
        maximum_width_pixels=2,
        maximum_height_pixels=2,
        quarter_turns_clockwise=0,
        crop_x_millionths=0,
        crop_y_millionths=0,
        crop_width_millionths=1_000_000,
        crop_height_millionths=1_000_000,
        deskew_millidegrees_clockwise=0,
        contrast_factor_millionths=1_000_000,
        background_cleanup_strength_millionths=0,
    )


def _source_stream(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> io.BytesIO:
    source = io.BytesIO(payload)
    source.fileno = lambda: 99  # type: ignore[method-assign]
    monkeypatch.setattr(
        "paperworkshell.worker.image_transform_operation.os.fstat",
        lambda _descriptor: type(
            "Info",
            (),
            {"st_mode": 0o100000, "st_size": len(payload)},
        )(),
    )
    return source


def _jpeg() -> bytes:
    payload = io.BytesIO()
    Image.new("RGB", (2, 2), (19, 47, 83)).save(
        payload,
        format="JPEG",
        quality=90,
        optimize=False,
        progressive=False,
    )
    return payload.getvalue()


def _run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    context: OperationContext,
    adapter: WindowsImageTransformAdapter,
    payload: bytes,
) -> tuple[CaseObjectReference, _Destination]:
    destination = _Destination(context.limits.output_bytes)
    monkeypatch.setattr(
        "paperworkshell.worker.image_transform_operation._materialize_worker_private_temp",
        lambda: tmp_path.resolve(),
    )
    reference = adapter._transform_open_streams(
        context,
        _request(),
        _source_stream(monkeypatch, payload),
        destination,  # type: ignore[arg-type]
    )
    return reference, destination


def test_open_stream_adapter_emits_page_bound_authenticated_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = b"encoded-source"
    context = _context(payload)
    decoder = _Decoder()
    transformer = _Transformer()
    storage = _TempStorage()
    adapter = WindowsImageTransformAdapter(decoder, transformer, storage)

    reference, destination = _run(
        monkeypatch,
        tmp_path,
        context=context,
        adapter=adapter,
        payload=payload,
    )

    assert reference.content_kind == IMAGE_TRANSFORM_OUTPUT_CONTENT_KIND
    assert reference.expected_byte_size == len(destination.payload)
    assert reference.expected_sha256 == hashlib.sha256(destination.payload).hexdigest()
    assert storage.required == [32]
    assert len(storage.streams) == 2
    assert all(stream.closed for stream in storage.streams)
    assert decoder.calls == transformer.calls == 1
    assert decoder.limits is not None and decoder.limits.maximum_output_bytes == 32
    artifact = decode_image_transform_artifact(
        bytes(destination.payload),
        expected_operation_id=context.operation_id,
        expected_output_id=_request().output_object_id,
    )
    assert artifact.payload == _NORMALIZED_PIXELS
    assert artifact.header.cache_key.case_id == context.case_id
    assert artifact.header.cache_key.source_id == _uuid(2)
    assert artifact.header.cache_key.page_id == _request().page_id
    assert artifact.header.cache_key.source_sha256 == hashlib.sha256(payload).hexdigest()


def test_capacity_refusal_precedes_temp_creation_decode_and_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = b"encoded-source"
    context = _context(payload)
    decoder = _Decoder()
    transformer = _Transformer()
    storage = _TempStorage(refuse_capacity=True)
    adapter = WindowsImageTransformAdapter(decoder, transformer, storage)
    destination = _Destination(context.limits.output_bytes)
    monkeypatch.setattr(
        "paperworkshell.worker.image_transform_operation._materialize_worker_private_temp",
        lambda: tmp_path.resolve(),
    )

    with pytest.raises(ImagePageError) as caught:
        adapter._transform_open_streams(
            context,
            _request(),
            _source_stream(monkeypatch, payload),
            destination,  # type: ignore[arg-type]
        )

    assert caught.value.code is ImagePageErrorCode.LIMIT_EXCEEDED
    assert caught.value.context == "image.transform_temp_capacity"
    assert storage.required == [32]
    assert storage.streams == []
    assert decoder.calls == transformer.calls == 0
    assert destination.payload == b""


def test_second_private_temp_failure_closes_first_and_does_not_decode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = b"encoded-source"
    context = _context(payload)
    decoder = _Decoder()
    transformer = _Transformer()
    storage = _TempStorage(fail_open_number=2)
    adapter = WindowsImageTransformAdapter(decoder, transformer, storage)
    destination = _Destination(context.limits.output_bytes)
    monkeypatch.setattr(
        "paperworkshell.worker.image_transform_operation._materialize_worker_private_temp",
        lambda: tmp_path.resolve(),
    )

    with pytest.raises(ImagePageError) as caught:
        adapter._transform_open_streams(
            context,
            _request(),
            _source_stream(monkeypatch, payload),
            destination,  # type: ignore[arg-type]
        )

    assert caught.value.code is ImagePageErrorCode.LIMIT_EXCEEDED
    assert caught.value.context == "image.transform_temp_create"
    assert len(storage.streams) == 1 and storage.streams[0].closed
    assert decoder.calls == transformer.calls == 0
    assert destination.payload == b""


def test_exact_combined_temp_limit_is_enforced_before_transform(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = b"encoded-source"
    context = _context(payload, temporary_storage_bytes=31)
    decoder = _Decoder()
    transformer = _Transformer()
    storage = _TempStorage()
    adapter = WindowsImageTransformAdapter(decoder, transformer, storage)
    destination = _Destination(context.limits.output_bytes)
    monkeypatch.setattr(
        "paperworkshell.worker.image_transform_operation._materialize_worker_private_temp",
        lambda: tmp_path.resolve(),
    )

    with pytest.raises(ImagePageError) as caught:
        adapter._transform_open_streams(
            context,
            _request(),
            _source_stream(monkeypatch, payload),
            destination,  # type: ignore[arg-type]
        )

    assert caught.value.code is ImagePageErrorCode.LIMIT_EXCEEDED
    assert caught.value.context == "image.transform_temp"
    assert decoder.calls == 1
    assert transformer.calls == 0
    assert len(storage.streams) == 2 and all(stream.closed for stream in storage.streams)
    assert destination.payload == b""


@pytest.mark.parametrize("late_failure", ("cancel", "invalid_result"))
def test_late_transform_failure_closes_temps_without_emitting_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    late_failure: str,
) -> None:
    payload = b"encoded-source"
    context = _context(payload)
    transformer = _Transformer(
        cancel_event=context.cancel_requested if late_failure == "cancel" else None,
        invalidate_result=late_failure == "invalid_result",
    )
    storage = _TempStorage()
    adapter = WindowsImageTransformAdapter(_Decoder(), transformer, storage)
    destination = _Destination(context.limits.output_bytes)
    monkeypatch.setattr(
        "paperworkshell.worker.image_transform_operation._materialize_worker_private_temp",
        lambda: tmp_path.resolve(),
    )

    with pytest.raises(ImagePageError) as caught:
        adapter._transform_open_streams(
            context,
            _request(),
            _source_stream(monkeypatch, payload),
            destination,  # type: ignore[arg-type]
        )

    expected_code = (
        ImagePageErrorCode.CANCELLED
        if late_failure == "cancel"
        else ImagePageErrorCode.RESULT_INVALID
    )
    assert caught.value.code is expected_code
    assert len(storage.streams) == 2 and all(stream.closed for stream in storage.streams)
    assert destination.payload == b""


@pytest.mark.parametrize(
    ("checkpoint", "cancel_after"),
    (
        ("before_pipeline", 1),
        ("source_read", 2),
        ("after_source_read", 3),
        ("after_image_materialization", 4),
        ("after_crop", 5),
        ("after_quarter_turn", 6),
        ("after_deskew", 7),
        ("after_contrast", 8),
        ("after_background_cleanup", 9),
        ("after_fit", 10),
        ("serialize_row_1", 11),
        ("serialize_row_2", 12),
        ("serialize_row_3", 13),
        ("before_flush", 14),
        ("after_flush", 15),
    ),
)
def test_real_transformer_checks_cancellation_at_every_pipeline_boundary(
    checkpoint: str,
    cancel_after: int,
) -> None:
    del checkpoint
    source_pixels = bytes((19, 47, 83, 255)) * 64
    source = io.BytesIO(source_pixels)
    source.seek(7)
    control = _CancelAtProbe(cancel_after)
    limits = ImageTransformLimits(
        maximum_source_width_pixels=512,
        maximum_source_height_pixels=512,
        maximum_source_pixels=262_144,
        maximum_intermediate_pixels=262_144,
        maximum_output_width_pixels=512,
        maximum_output_height_pixels=512,
        maximum_output_pixels=262_144,
        maximum_output_bytes=1_048_576,
        maximum_memory_bytes=4_194_304,
        maximum_temporary_storage_bytes=2_097_152,
        wall_time_ms=10_000,
    )
    intent = ImageTransformIntent(
        crop=ImageNormalizedCrop(125_000, 125_000, 750_000, 750_000),
        additional_rotation=ImageQuarterTurn.DEGREES_90,
        deskew_angle_millidegrees_clockwise=5_000,
        contrast_factor_millionths=1_250_000,
        background_cleanup_strength_millionths=500_000,
    )

    with pytest.raises(ImagePageError) as caught:
        PillowImageTransformer().transform(
            source,
            io.BytesIO(),
            source_layout=ImageRgba8Layout(8, 8, 32, 64, 256),
            intent=intent,
            maximum_width_pixels=3,
            maximum_height_pixels=3,
            limits=limits,
            control=control,
        )

    assert caught.value.code is ImagePageErrorCode.CANCELLED
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert control.cancel_checks == cancel_after
    assert source.tell() == 7


def test_mid_raster_disk_full_cleans_private_temps_and_fresh_retry_recovers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = _jpeg()
    context = _context(payload)
    storage = _DiskFullOnceTempStorage()
    adapter = WindowsImageTransformAdapter(private_temp_storage=storage)
    temp_root = (tmp_path / "worker-profile" / "Temp").resolve()
    temp_root.mkdir(parents=True)
    monkeypatch.setattr(
        "paperworkshell.worker.image_transform_operation._materialize_worker_private_temp",
        lambda: temp_root,
    )
    failed_destination = _Destination(context.limits.output_bytes)

    with pytest.raises(ImagePageError) as caught:
        adapter._transform_open_streams(
            context,
            _request(),
            _source_stream(monkeypatch, payload),
            failed_destination,  # type: ignore[arg-type]
        )

    assert caught.value.code is ImagePageErrorCode.DESTINATION_FAILED
    assert caught.value.context == "image.destination_write"
    assert "sensitive" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert storage.failed_stream is not None and storage.failed_stream.closed
    assert failed_destination.payload == b""
    assert tuple(temp_root.iterdir()) == ()

    recovered_destination = _Destination(context.limits.output_bytes)
    reference = adapter._transform_open_streams(
        context,
        _request(),
        _source_stream(monkeypatch, payload),
        recovered_destination,  # type: ignore[arg-type]
    )

    assert reference.expected_byte_size == len(recovered_destination.payload)
    assert reference.expected_sha256 == hashlib.sha256(recovered_destination.payload).hexdigest()
    artifact = decode_image_transform_artifact(
        bytes(recovered_destination.payload),
        expected_operation_id=context.operation_id,
        expected_output_id=_request().output_object_id,
    )
    assert artifact.header.result.width_pixels == 2
    assert artifact.header.result.height_pixels == 2
    assert tuple(temp_root.iterdir()) == ()

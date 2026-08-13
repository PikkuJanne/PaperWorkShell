"""Focused production-adapter tests without native Windows calls."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import BinaryIO, cast
from uuid import UUID

import pytest
from PIL import Image

from paperworkshell.domain.image_pages import (
    IMAGE_PAGE_ARTIFACT_HEADER_BYTES,
    ImageAlphaNormalizationPolicy,
    ImageExifOrientation,
    ImageNormalizationLimits,
    ImageNormalizationRequest,
    ImageNormalizationResult,
    ImagePageError,
    ImagePageErrorCode,
    ImagePageMetadata,
    ImageQuarterTurn,
    ImageSourceColorModel,
    ImageSourceFormat,
    decode_image_page_artifact,
)
from paperworkshell.imaging.pillow_decoder import ImageNormalizationControl
from paperworkshell.worker.image_normalize_operation import (
    WindowsImageNormalizeAdapter,
    _MappedOutputSection,
)
from paperworkshell.worker.operations import (
    ImageNormalizeOperationRequest,
    OperationContext,
)
from paperworkshell.worker.private_temp import OsWorkerPrivateTempStorage
from paperworkshell.worker.protocol.models import (
    IMAGE_NORMALIZE_OUTPUT_CONTENT_KIND,
    CaseObjectReference,
    ReferenceArea,
    ResourceLimits,
)


def _uuid(number: int) -> UUID:
    return UUID(f"8b000000-0000-4000-8000-{number:012d}")


@dataclass(slots=True)
class _Decoder:
    pixels: bytes = bytes((1, 2, 3, 255)) * 4
    calls: int = 0

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
        del source, limits, control
        self.calls += 1
        assert expected_format is ImageSourceFormat.JPEG
        assert request.maximum_width_pixels == 2
        destination.write(self.pixels)
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
            bytes_written=len(self.pixels),
            pixel_sha256=hashlib.sha256(self.pixels).hexdigest(),
        )


class _TempStorage:
    def require_capacity(self, _root: Path, _required: int) -> None:
        return None

    def open_anonymous(self, _root: Path) -> io.BytesIO:
        return io.BytesIO()

    def open_named(self, _root: Path, *, suffix: str) -> tuple[io.BytesIO, Path]:
        del suffix
        return io.BytesIO(), _root / "unused-stage"


class _CapacityRefusalTempStorage:
    def __init__(self, *, available_bytes: int) -> None:
        self._storage = OsWorkerPrivateTempStorage(
            available_bytes_probe=lambda _root: available_bytes,
            minimum_free_space_reserve_bytes=0,
        )
        self.anonymous_opens = 0

    def require_capacity(self, root: Path, byte_count: int) -> None:
        self._storage.require_capacity(root, byte_count)

    def open_anonymous(self, root: Path) -> BinaryIO:
        self.anonymous_opens += 1
        return self._storage.open_anonymous(root)

    def open_named(self, root: Path, *, suffix: str) -> tuple[BinaryIO, Path]:
        return self._storage.open_named(root, suffix=suffix)


class _DiskFullStream:
    """Write a real partial anonymous raster, then raise one content-bearing ENOSPC."""

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
        raise OSError(28, "sensitive image raster disk-full detail")

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


def _context(*, source_payload: bytes | None = None) -> OperationContext:
    expected_size = 100 if source_payload is None else len(source_payload)
    expected_digest = (
        "a" * 64 if source_payload is None else hashlib.sha256(source_payload).hexdigest()
    )
    limits = ResourceLimits(
        wall_time_ms=1_000,
        cpu_time_ms=500,
        memory_bytes=1_024 * 1_024,
        temporary_storage_bytes=16,
        output_bytes=IMAGE_PAGE_ARTIFACT_HEADER_BYTES + 16,
        page_count=1,
        pixel_count=16,
    )
    return OperationContext(
        operation_id=_uuid(10),
        case_id=_uuid(1),
        limits=limits,
        cancel_requested=Event(),
        report_progress=lambda _value: None,
        deadline_expired=lambda: False,
        inputs=(
            CaseObjectReference(
                ReferenceArea.SOURCE_READ,
                _uuid(2),
                None,
                "jpeg",
                expected_size,
                expected_digest,
            ),
        ),
    )


def _request() -> ImageNormalizeOperationRequest:
    return ImageNormalizeOperationRequest(
        source_handle=101,
        output_handle=202,
        output_object_id=_uuid(20),
        maximum_width_pixels=2,
        maximum_height_pixels=2,
        additional_rotation=0,
    )


def _source_stream(
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
) -> io.BytesIO:
    source = io.BytesIO(payload)
    source.fileno = lambda: 99  # type: ignore[method-assign]
    monkeypatch.setattr(
        "paperworkshell.worker.image_normalize_operation.os.fstat",
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


def test_open_stream_adapter_emits_authenticated_exact_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decoder = _Decoder()
    adapter = WindowsImageNormalizeAdapter(
        decoder=decoder,
        private_temp_storage=_TempStorage(),
    )
    context = _context()
    destination = _Destination(context.limits.output_bytes)
    monkeypatch.setattr(
        "paperworkshell.worker.image_normalize_operation._materialize_worker_private_temp",
        lambda: object(),
    )
    source = _source_stream(monkeypatch, b"x" * 100)

    reference = adapter._normalize_open_streams(
        context,
        _request(),
        source,
        destination,  # type: ignore[arg-type]
    )

    assert reference.content_kind == IMAGE_NORMALIZE_OUTPUT_CONTENT_KIND
    assert reference.expected_byte_size == len(destination.payload)
    assert reference.expected_sha256 == hashlib.sha256(destination.payload).hexdigest()
    assert decoder.calls == 1
    artifact = decode_image_page_artifact(
        bytes(destination.payload),
        expected_operation_id=_uuid(10),
        expected_output_id=_uuid(20),
    )
    assert artifact.payload == _Decoder().pixels


@pytest.mark.parametrize(
    ("terminal_state", "code"),
    (
        ("cancelled", ImagePageErrorCode.CANCELLED),
        ("timed_out", ImagePageErrorCode.TIMED_OUT),
    ),
)
def test_open_stream_adapter_checks_terminal_state_before_decoder(
    monkeypatch: pytest.MonkeyPatch,
    terminal_state: str,
    code: ImagePageErrorCode,
) -> None:
    context = _context()
    if terminal_state == "cancelled":
        context.cancel_requested.set()
    else:
        context = OperationContext(
            operation_id=context.operation_id,
            case_id=context.case_id,
            limits=context.limits,
            cancel_requested=context.cancel_requested,
            report_progress=context.report_progress,
            deadline_expired=lambda: True,
            inputs=context.inputs,
        )
    decoder = _Decoder()
    adapter = WindowsImageNormalizeAdapter(
        decoder=decoder,
        private_temp_storage=_TempStorage(),
    )
    source = _source_stream(monkeypatch, b"x" * 100)
    destination = _Destination(context.limits.output_bytes)

    with pytest.raises(ImagePageError) as caught:
        adapter._normalize_open_streams(
            context,
            _request(),
            source,
            destination,  # type: ignore[arg-type]
        )

    assert caught.value.code is code
    assert caught.value.context == "image.operation"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert decoder.calls == 0
    assert destination.payload == b""


def test_private_temp_capacity_refusal_precedes_decoder_and_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context()
    decoder = _Decoder()
    storage = _CapacityRefusalTempStorage(
        available_bytes=context.limits.temporary_storage_bytes - 1,
    )
    adapter = WindowsImageNormalizeAdapter(
        decoder=decoder,
        private_temp_storage=storage,
    )
    destination = _Destination(context.limits.output_bytes)
    temp_root = tmp_path.resolve()
    monkeypatch.setattr(
        "paperworkshell.worker.image_normalize_operation._materialize_worker_private_temp",
        lambda: temp_root,
    )

    with pytest.raises(ImagePageError) as caught:
        adapter._normalize_open_streams(
            context,
            _request(),
            _source_stream(monkeypatch, b"x" * 100),
            destination,  # type: ignore[arg-type]
        )

    assert caught.value.code is ImagePageErrorCode.LIMIT_EXCEEDED
    assert caught.value.context == "image.temp_capacity"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert decoder.calls == 0
    assert storage.anonymous_opens == 0
    assert destination.payload == b""
    assert tuple(temp_root.iterdir()) == ()


def test_mid_raster_disk_full_cleans_partial_and_fresh_retry_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _jpeg()
    context = _context(source_payload=payload)
    storage = _DiskFullOnceTempStorage()
    adapter = WindowsImageNormalizeAdapter(private_temp_storage=storage)
    temp_root = (tmp_path / "worker-profile" / "Temp").resolve()
    temp_root.mkdir(parents=True)
    monkeypatch.setattr(
        "paperworkshell.worker.image_normalize_operation._materialize_worker_private_temp",
        lambda: temp_root,
    )
    failed_destination = _Destination(context.limits.output_bytes)

    with pytest.raises(ImagePageError) as caught:
        adapter._normalize_open_streams(
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
    reference = adapter._normalize_open_streams(
        context,
        _request(),
        _source_stream(monkeypatch, payload),
        recovered_destination,  # type: ignore[arg-type]
    )

    assert reference.expected_byte_size == len(recovered_destination.payload)
    assert reference.expected_sha256 == hashlib.sha256(recovered_destination.payload).hexdigest()
    artifact = decode_image_page_artifact(
        bytes(recovered_destination.payload),
        expected_operation_id=context.operation_id,
        expected_output_id=_request().output_object_id,
    )
    assert artifact.header.result.width_pixels == 2
    assert artifact.header.result.height_pixels == 2
    assert tuple(temp_root.iterdir()) == ()


def test_mapped_output_rejects_capacity_overrun_without_writing() -> None:
    section = _MappedOutputSection(101, 4, 202)

    with pytest.raises(ImagePageError) as caught:
        section.write(b"12345")

    assert caught.value.code is ImagePageErrorCode.LIMIT_EXCEEDED
    assert section.tell() == 0

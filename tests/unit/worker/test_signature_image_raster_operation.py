"""Focused production-adapter tests for straight-alpha signature rasters."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import BinaryIO
from uuid import UUID

import pytest

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
    ImageRasterBackground,
    ImageSourceColorModel,
    ImageSourceFormat,
)
from paperworkshell.domain.signature_images import (
    SIGNATURE_IMAGE_RASTER_ARTIFACT_HEADER_BYTES,
    decode_signature_image_raster_artifact,
)
from paperworkshell.imaging.pillow_decoder import ImageNormalizationControl
from paperworkshell.worker.operations import (
    OperationContext,
    SignatureImageRasterOperationRequest,
)
from paperworkshell.worker.protocol.models import (
    SIGNATURE_IMAGE_RASTER_OUTPUT_CONTENT_KIND,
    CaseObjectReference,
    ReferenceArea,
    ResourceLimits,
)
from paperworkshell.worker.signature_image_raster_operation import (
    WindowsSignatureImageRasterAdapter,
)


def _uuid(number: int) -> UUID:
    return UUID(f"7f000000-0000-4000-8000-{number:012d}")


@dataclass(slots=True)
class _Decoder:
    pixels: bytes = bytes((0, 0, 0, 0, 11, 22, 33, 128))
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
        assert expected_format is ImageSourceFormat.PNG
        assert request.alpha_policy is ImageAlphaNormalizationPolicy.PRESERVE_STRAIGHT_ALPHA
        destination.write(self.pixels)
        metadata = ImagePageMetadata(
            source_format=ImageSourceFormat.PNG,
            source_color_model=ImageSourceColorModel.RGBA,
            alpha_policy=ImageAlphaNormalizationPolicy.PRESERVE_STRAIGHT_ALPHA,
            exif_orientation=ImageExifOrientation.NORMAL,
            encoded_width_pixels=2,
            encoded_height_pixels=1,
            oriented_width_pixels=2,
            oriented_height_pixels=1,
            component_count=4,
            bits_per_component=8,
            frame_count=1,
            metadata_record_count=0,
            metadata_byte_count=0,
            has_alpha=True,
            has_embedded_color_profile=False,
            has_exif=False,
        )
        return ImageNormalizationResult(
            metadata=metadata,
            additional_rotation=ImageQuarterTurn.DEGREES_0,
            width_pixels=2,
            height_pixels=1,
            row_stride_bytes=8,
            bytes_written=8,
            pixel_sha256=hashlib.sha256(self.pixels).hexdigest(),
            alpha_policy=ImageAlphaNormalizationPolicy.PRESERVE_STRAIGHT_ALPHA,
            background=ImageRasterBackground.PRESERVED_ALPHA,
        )


class _TempStorage:
    def require_capacity(self, _root: Path, _required: int) -> None:
        return None

    def open_anonymous(self, _root: Path) -> io.BytesIO:
        return io.BytesIO()

    def open_named(self, _root: Path, *, suffix: str) -> tuple[io.BytesIO, Path]:
        del suffix
        return io.BytesIO(), _root / "unused"


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


def _context(*, cancelled: bool = False, expired: bool = False) -> OperationContext:
    source = b"x" * 100
    event = Event()
    if cancelled:
        event.set()
    return OperationContext(
        operation_id=_uuid(10),
        case_id=_uuid(1),
        limits=ResourceLimits(
            wall_time_ms=1_000,
            cpu_time_ms=750,
            memory_bytes=1_024 * 1_024,
            temporary_storage_bytes=8,
            output_bytes=SIGNATURE_IMAGE_RASTER_ARTIFACT_HEADER_BYTES + 8,
            page_count=1,
            pixel_count=2,
        ),
        cancel_requested=event,
        report_progress=lambda _value: None,
        deadline_expired=lambda: expired,
        inputs=(
            CaseObjectReference(
                ReferenceArea.ASSET_READ,
                _uuid(2),
                None,
                "png",
                len(source),
                hashlib.sha256(source).hexdigest(),
            ),
        ),
    )


def _request() -> SignatureImageRasterOperationRequest:
    return SignatureImageRasterOperationRequest(101, 202, _uuid(20), 2, 1)


def _source_stream(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> io.BytesIO:
    source = io.BytesIO(payload)
    source.fileno = lambda: 99  # type: ignore[method-assign]
    monkeypatch.setattr(
        "paperworkshell.worker.signature_image_raster_operation.os.fstat",
        lambda _descriptor: type("Info", (), {"st_mode": 0o100000, "st_size": len(payload)})(),
    )
    return source


def test_open_stream_adapter_emits_authenticated_straight_alpha_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decoder = _Decoder()
    adapter = WindowsSignatureImageRasterAdapter(
        decoder=decoder,
        private_temp_storage=_TempStorage(),
    )
    context = _context()
    destination = _Destination(context.limits.output_bytes)
    monkeypatch.setattr(
        "paperworkshell.worker.signature_image_raster_operation._materialize_worker_private_temp",
        lambda: object(),
    )

    reference = adapter._rasterize_open_streams(
        context,
        _request(),
        _source_stream(monkeypatch, b"x" * 100),
        destination,  # type: ignore[arg-type]
    )

    assert reference.content_kind == SIGNATURE_IMAGE_RASTER_OUTPUT_CONTENT_KIND
    assert reference.expected_sha256 == hashlib.sha256(destination.payload).hexdigest()
    artifact = decode_signature_image_raster_artifact(
        bytes(destination.payload),
        expected_operation_id=_uuid(10),
        expected_output_id=_uuid(20),
    )
    assert artifact.payload == decoder.pixels
    assert artifact.payload[3::4] == bytes((0, 128))
    assert decoder.calls == 1


def test_hostile_decoder_hidden_rgb_is_rejected_before_output_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decoder = _Decoder(pixels=bytes((91, 82, 73, 0, 11, 22, 33, 128)))
    adapter = WindowsSignatureImageRasterAdapter(
        decoder=decoder,
        private_temp_storage=_TempStorage(),
    )
    context = _context()
    destination = _Destination(context.limits.output_bytes)
    monkeypatch.setattr(
        "paperworkshell.worker.signature_image_raster_operation._materialize_worker_private_temp",
        lambda: object(),
    )

    with pytest.raises(ImagePageError) as caught:
        adapter._rasterize_open_streams(
            context,
            _request(),
            _source_stream(monkeypatch, b"x" * 100),
            destination,  # type: ignore[arg-type]
        )

    assert caught.value.code is ImagePageErrorCode.RESULT_INVALID
    assert caught.value.context == "signature.raster_hidden_rgb"
    assert "91" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert destination.payload.startswith(b"PWS-SIG1")
    assert len(destination.payload) == SIGNATURE_IMAGE_RASTER_ARTIFACT_HEADER_BYTES


def test_cancelled_context_stops_before_decoder_or_artifact_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decoder = _Decoder()
    adapter = WindowsSignatureImageRasterAdapter(
        decoder=decoder,
        private_temp_storage=_TempStorage(),
    )
    context = _context(cancelled=True)
    destination = _Destination(context.limits.output_bytes)

    with pytest.raises(ImagePageError) as caught:
        adapter._rasterize_open_streams(
            context,
            _request(),
            _source_stream(monkeypatch, b"x" * 100),
            destination,  # type: ignore[arg-type]
        )

    assert caught.value.code is ImagePageErrorCode.CANCELLED
    assert decoder.calls == 0
    assert destination.payload == b""


def test_expired_context_stops_before_decoder_or_artifact_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decoder = _Decoder()
    adapter = WindowsSignatureImageRasterAdapter(
        decoder=decoder,
        private_temp_storage=_TempStorage(),
    )
    context = _context(expired=True)
    destination = _Destination(context.limits.output_bytes)

    with pytest.raises(ImagePageError) as caught:
        adapter._rasterize_open_streams(
            context,
            _request(),
            _source_stream(monkeypatch, b"x" * 100),
            destination,  # type: ignore[arg-type]
        )

    assert caught.value.code is ImagePageErrorCode.TIMED_OUT
    assert decoder.calls == 0
    assert destination.payload == b""

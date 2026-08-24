"""Focused owner-thread tests for brokered signature-image rasterization."""

from __future__ import annotations

import hashlib
from math import isqrt
from types import TracebackType
from typing import Self
from uuid import UUID

import pytest

from paperworkshell.domain.image_pages import (
    IMAGE_PAGE_ARTIFACT_HEADER_BYTES,
    ImageExifOrientation,
    ImageSourceFormat,
)
from paperworkshell.domain.signature_images import (
    SIGNATURE_IMAGE_RASTER_ARTIFACT_HEADER_BYTES,
    SignatureImageError,
    SignatureImageErrorCode,
    SignatureImageMediaType,
    SignatureImageProgress,
    SignatureImageProgressPhase,
    SignatureImageRasterArtifactHeader,
    SignatureImageRasterRequest,
    SignatureImageRasterResult,
    encode_signature_image_raster_artifact_header,
)
from paperworkshell.worker.client.models import (
    WorkerClientSnapshot,
    WorkerLifecyclePhase,
    WorkerOperationResultSnapshot,
)
from paperworkshell.worker.client.process import BrokeredHandlePurpose
from paperworkshell.worker.desktop_composition import _signature_image_raster_limits
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.protocol.models import (
    SIGNATURE_IMAGE_RASTER_CAPABILITY,
    SIGNATURE_IMAGE_RASTER_OUTPUT_CONTENT_KIND,
    CancelReason,
    CaseObjectReference,
    ErrorCategory,
    ErrorCode,
    OperationOutcome,
    OperationStartPayload,
    ReferenceArea,
    ResourceLimits,
)
from paperworkshell.worker.signature_image_raster_broker import (
    BrokeredSignatureImageRasterizer,
    SignatureImageRasterBrokerPolicy,
    _operation_limits,
)


def _uuid(number: int) -> UUID:
    return UUID(f"81000000-0000-4000-8000-{number:012d}")


def _request() -> SignatureImageRasterRequest:
    return SignatureImageRasterRequest(
        _uuid(1),
        _uuid(2),
        SignatureImageMediaType.PNG,
        100,
        "a" * 64,
        2,
        1,
    )


def _policy() -> SignatureImageRasterBrokerPolicy:
    return SignatureImageRasterBrokerPolicy(
        ResourceLimits(
            wall_time_ms=5_000,
            cpu_time_ms=4_000,
            memory_bytes=1_024 * 1_024,
            temporary_storage_bytes=8,
            output_bytes=SIGNATURE_IMAGE_RASTER_ARTIFACT_HEADER_BYTES + 8,
            page_count=1,
            pixel_count=16,
        ),
        preview_edge_pixels=2,
        pump_interval_seconds=0.05,
    )


def test_signature_limits_admit_the_exact_configured_raw_pixel_boundary() -> None:
    pixel_count = 4_000_000
    edge_pixels = isqrt(pixel_count)
    assert edge_pixels * edge_pixels == pixel_count
    raw_pixel_bytes = pixel_count * 4
    image_limits = ResourceLimits(
        wall_time_ms=5_000,
        cpu_time_ms=4_000,
        memory_bytes=raw_pixel_bytes,
        temporary_storage_bytes=raw_pixel_bytes,
        output_bytes=IMAGE_PAGE_ARTIFACT_HEADER_BYTES + raw_pixel_bytes,
        page_count=1,
        pixel_count=pixel_count,
    )
    request = SignatureImageRasterRequest(
        _uuid(1),
        _uuid(2),
        SignatureImageMediaType.PNG,
        100,
        "a" * 64,
        edge_pixels,
        edge_pixels,
    )

    signature_limits = _signature_image_raster_limits(image_limits)
    operation_limits = _operation_limits(signature_limits, request)

    expected_artifact_bytes = SIGNATURE_IMAGE_RASTER_ARTIFACT_HEADER_BYTES + raw_pixel_bytes
    assert signature_limits.output_bytes == expected_artifact_bytes
    assert operation_limits.output_bytes == expected_artifact_bytes


def _artifact(operation_id: UUID, output_id: UUID, *, hidden_rgb: bool = False) -> bytes:
    pixels = (
        bytes((91, 82, 73, 0, 10, 20, 30, 128))
        if hidden_rgb
        else bytes((0, 0, 0, 0, 10, 20, 30, 128))
    )
    result = SignatureImageRasterResult(
        source_format=ImageSourceFormat.PNG,
        source_byte_count=100,
        source_sha256="a" * 64,
        exif_orientation=ImageExifOrientation.NORMAL,
        encoded_width_pixels=2,
        encoded_height_pixels=1,
        oriented_width_pixels=2,
        oriented_height_pixels=1,
        width_pixels=2,
        height_pixels=1,
        row_stride_bytes=8,
        pixel_byte_count=8,
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
        source_has_alpha=True,
        source_had_embedded_color_profile=False,
        source_had_exif=False,
        source_metadata_byte_count=0,
    )
    return (
        encode_signature_image_raster_artifact_header(
            SignatureImageRasterArtifactHeader(operation_id, output_id, result)
        )
        + pixels
    )


class _Lease:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.case_id = _uuid(1)
        self.asset_id = _uuid(2)
        self.media_type = SignatureImageMediaType.PNG
        self.byte_size = 100
        self.sha256 = "a" * 64
        self.handle_value = 101

    def __enter__(self) -> Self:
        self.events.append("asset.enter")
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.events.append("asset.exit")


class _Store:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.lease = _Lease(events)

    def borrow_committed_signature_image(
        self,
        case_id: UUID,
        asset_id: UUID,
        *,
        cancellation: object,
        deadline: object,
    ) -> _Lease:
        del cancellation, deadline
        assert (case_id, asset_id) == (_uuid(1), _uuid(2))
        self.events.append("asset.borrow")
        return self.lease


def _snapshot(
    phase: WorkerLifecyclePhase,
    result: WorkerOperationResultSnapshot | None = None,
) -> WorkerClientSnapshot:
    return WorkerClientSnapshot(
        generation=1,
        phase=phase,
        case_id=_uuid(1),
        session_id=_uuid(30),
        worker_instance_id=_uuid(31),
        pid=123,
        capabilities=(SIGNATURE_IMAGE_RASTER_CAPABILITY,),
        active_operation=None,
        last_result=result,
        last_failure=None,
    )


class _Worker:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.payload: OperationStartPayload | None = None
        self.operation_id: UUID | None = None
        self.result: WorkerOperationResultSnapshot | None = None
        self.encoded: bytes | None = None
        self.hidden_rgb = False
        self.withhold_result = False
        self.wrong_output_id = False
        self.profile_unsupported = False
        self.terminated = False

    def start(self, case_id: UUID) -> int:
        assert case_id == _uuid(1)
        self.events.append("worker.start")
        return 1

    def query(self) -> WorkerClientSnapshot:
        self.events.append("worker.query")
        phase = WorkerLifecyclePhase.READY if self.payload is None else WorkerLifecyclePhase.BUSY
        return _snapshot(phase, self.result)

    def pump(self, timeout_seconds: float = 0.0) -> WorkerClientSnapshot:
        assert timeout_seconds == 0.05
        self.events.append("worker.pump")
        return self.query()

    def duplicate_handle(self, source_handle: int, purpose: BrokeredHandlePurpose) -> int:
        self.events.append(f"worker.duplicate.{purpose.value}")
        if purpose is BrokeredHandlePurpose.READ_SOURCE:
            assert source_handle == 101
            return 1_001
        assert purpose is BrokeredHandlePurpose.WRITE_BOUNDED_OUTPUT_SECTION
        assert source_handle == 202
        return 1_002

    def start_operation(
        self,
        payload: OperationStartPayload,
        *,
        operation_id: UUID | None = None,
    ) -> UUID:
        assert operation_id is not None
        self.events.append("worker.operation")
        self.payload = payload
        self.operation_id = operation_id
        output_id = next(
            parameter.value
            for parameter in payload.parameters
            if parameter.name == "output_object_id"
        )
        assert type(output_id) is UUID
        self.encoded = _artifact(operation_id, output_id, hidden_rgb=self.hidden_rgb)
        output = CaseObjectReference(
            ReferenceArea.OPERATION_STAGING,
            _uuid(99) if self.wrong_output_id else output_id,
            operation_id,
            SIGNATURE_IMAGE_RASTER_OUTPUT_CONTENT_KIND,
            len(self.encoded),
            hashlib.sha256(self.encoded).hexdigest(),
        )
        if self.profile_unsupported:
            self.result = WorkerOperationResultSnapshot(
                1,
                operation_id,
                OperationOutcome.FAILED,
                (),
                ErrorCode.WORKER_OPERATION_FAILED,
                ErrorCategory.UNSUPPORTED,
            )
        elif not self.withhold_result:
            self.result = WorkerOperationResultSnapshot(
                1,
                operation_id,
                OperationOutcome.SUCCEEDED,
                (output,),
                None,
            )
        return operation_id

    def cancel(self, reason: CancelReason = CancelReason.USER_REQUESTED) -> UUID:
        self.events.append(f"worker.cancel.{reason.value}")
        assert self.operation_id is not None
        self.result = WorkerOperationResultSnapshot(
            1,
            self.operation_id,
            OperationOutcome.CANCELLED,
            (),
            None,
        )
        return _uuid(40)

    def terminate(self) -> None:
        self.events.append("worker.terminate")
        self.terminated = True


class _OutputBuffer:
    def __init__(self, capacity: int, worker: _Worker, events: list[str]) -> None:
        self.capacity = capacity
        self.write_handle_value = 202
        self.worker = worker
        self.events = events
        self.private_failure = False

    def __enter__(self) -> Self:
        self.events.append("buffer.enter")
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.events.append("buffer.exit")

    def read_exact(self, byte_count: int) -> bytes:
        self.events.append("buffer.read")
        if self.private_failure:
            raise RuntimeError("PRIVATE-PIXEL-BUFFER-CANARY")
        assert self.worker.encoded is not None
        assert len(self.worker.encoded) == byte_count
        return self.worker.encoded


def _system() -> tuple[
    BrokeredSignatureImageRasterizer,
    _Store,
    _Worker,
    _OutputBuffer,
    list[str],
]:
    events: list[str] = []
    store = _Store(events)
    worker = _Worker(events)
    ids = iter((_uuid(20), _uuid(21)))
    selected_buffer: _OutputBuffer | None = None

    def output_buffer_factory(capacity: int) -> _OutputBuffer:
        nonlocal selected_buffer
        selected_buffer = _OutputBuffer(capacity, worker, events)
        return selected_buffer

    rasterizer = BrokeredSignatureImageRasterizer(
        worker,
        store,
        _policy(),
        heavy_operations=HeavyOperationCoordinator(1),
        id_factory=lambda: next(ids),
        output_buffer_factory=output_buffer_factory,
    )
    # Materialize the buffer deterministically for tests that configure it before use.
    buffer = output_buffer_factory(_policy().limits.output_bytes)
    original_factory = rasterizer._output_buffer_factory

    def selected_factory(capacity: int) -> _OutputBuffer:
        assert capacity == buffer.capacity
        return buffer

    assert original_factory is output_buffer_factory
    rasterizer._output_buffer_factory = selected_factory
    return rasterizer, store, worker, buffer, events


def test_committed_asset_runs_one_shot_asset_read_and_returns_renderer_source() -> None:
    rasterizer, _store, worker, _buffer, events = _system()
    progress: list[SignatureImageProgress] = []

    source = rasterizer.rasterize_committed(
        _request(),
        deadline=lambda: False,
        cancellation=lambda: False,
        progress=progress.append,
    )

    assert source.metadata.asset_id == _uuid(2)
    assert source.read() == bytes((0, 0, 0, 0, 10, 20, 30, 128))
    assert worker.payload is not None
    assert worker.payload.inputs[0].area is ReferenceArea.ASSET_READ
    assert worker.payload.inputs[0].object_id == _uuid(2)
    assert worker.terminated
    assert events.index("worker.terminate") < events.index("buffer.read")
    assert events[-1] == "asset.exit"
    assert [item.phase for item in progress] == [
        SignatureImageProgressPhase.DECODING,
        SignatureImageProgressPhase.PREVIEW_READY,
    ]
    assert {item.operation_id for item in progress} == {_uuid(21)}
    assert all(item.operation_id != _uuid(2) for item in progress)


def test_hidden_rgb_from_worker_fails_after_all_authorities_are_released() -> None:
    rasterizer, _store, worker, _buffer, events = _system()
    worker.hidden_rgb = True

    with pytest.raises(SignatureImageError) as caught:
        rasterizer.rasterize_committed(
            _request(),
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert caught.value.code is SignatureImageErrorCode.RESULT_INVALID
    assert caught.value.context == "signature.hidden_rgb"
    assert worker.terminated
    assert events[-1] == "asset.exit"


def test_late_cancellation_requests_cancel_and_terminates_worker() -> None:
    rasterizer, _store, worker, _buffer, events = _system()
    worker.withhold_result = True
    probes = 0

    def cancellation() -> bool:
        nonlocal probes
        probes += 1
        return probes >= 5

    with pytest.raises(SignatureImageError) as caught:
        rasterizer.rasterize_committed(
            _request(),
            deadline=lambda: False,
            cancellation=cancellation,
        )

    assert caught.value.code is SignatureImageErrorCode.CANCELLED
    assert "worker.cancel.user_requested" in events
    assert worker.terminated
    assert events[-1] == "asset.exit"


def test_deadline_expiry_requests_shutdown_cancel_and_terminates_worker() -> None:
    rasterizer, _store, worker, _buffer, events = _system()
    worker.withhold_result = True
    probes = 0

    def deadline() -> bool:
        nonlocal probes
        probes += 1
        return probes >= 3

    with pytest.raises(SignatureImageError) as caught:
        rasterizer.rasterize_committed(
            _request(),
            deadline=deadline,
            cancellation=lambda: False,
        )

    assert caught.value.code is SignatureImageErrorCode.TIMED_OUT
    assert "worker.cancel.shutdown" in events
    assert worker.terminated
    assert events[-1] == "asset.exit"


def test_malformed_worker_output_binding_fails_before_buffer_read() -> None:
    rasterizer, _store, worker, _buffer, events = _system()
    worker.wrong_output_id = True

    with pytest.raises(SignatureImageError) as caught:
        rasterizer.rasterize_committed(
            _request(),
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert caught.value.code is SignatureImageErrorCode.RESULT_INVALID
    assert caught.value.context == "signature.output"
    assert "buffer.read" not in events
    assert worker.terminated
    assert events[-1] == "asset.exit"


def test_sanitized_unsupported_worker_category_maps_to_stable_color_profile_error() -> None:
    rasterizer, _store, worker, _buffer, events = _system()
    worker.profile_unsupported = True

    with pytest.raises(SignatureImageError) as caught:
        rasterizer.rasterize_committed(
            _request(),
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert caught.value.code is SignatureImageErrorCode.COLOR_PROFILE_UNSUPPORTED
    assert caught.value.context == "signature.color_profile"
    assert "buffer.read" not in events
    assert worker.terminated
    assert events[-1] == "asset.exit"


def test_private_output_failure_is_rebuilt_as_content_free_error() -> None:
    rasterizer, _store, worker, buffer, events = _system()
    buffer.private_failure = True

    with pytest.raises(SignatureImageError) as caught:
        rasterizer.rasterize_committed(
            _request(),
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert caught.value.code is SignatureImageErrorCode.INTERNAL_ERROR
    assert "PRIVATE-PIXEL-BUFFER-CANARY" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert worker.terminated
    assert events[-1] == "asset.exit"


def test_mismatched_asset_lease_never_starts_worker() -> None:
    rasterizer, store, worker, _buffer, _events = _system()
    store.lease.sha256 = "b" * 64

    with pytest.raises(SignatureImageError) as caught:
        rasterizer.rasterize_committed(
            _request(),
            deadline=lambda: False,
            cancellation=lambda: False,
        )

    assert caught.value.code is SignatureImageErrorCode.ASSET_CHANGED
    assert worker.payload is None
    assert not worker.terminated

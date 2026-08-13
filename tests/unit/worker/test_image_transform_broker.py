"""Focused owner-thread tests for the scan-transform broker."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from types import TracebackType
from typing import Self
from uuid import UUID

import pytest

from paperworkshell.domain.image_pages import (
    ImageExifOrientation,
    ImagePageError,
    ImagePageErrorCode,
    ImagePagePreviewProgressPhase,
    ImageQuarterTurn,
    ImageRgba8Layout,
    ImageSourceFormat,
)
from paperworkshell.domain.image_transforms import (
    IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES,
    ImageNormalizedCrop,
    ImagePageTransformRequest,
    ImageTransformArtifactHeader,
    ImageTransformCacheKey,
    ImageTransformIntent,
    ImageTransformResult,
    encode_image_transform_artifact,
)
from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.source_ingest import SourceIngestError, SourceIngestErrorCode
from paperworkshell.worker.client.models import (
    WorkerClientSnapshot,
    WorkerLifecyclePhase,
    WorkerOperationResultSnapshot,
)
from paperworkshell.worker.client.process import BrokeredHandlePurpose
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.image_transform_broker import (
    BrokeredImagePageTransformer,
    ImageTransformBrokerPolicy,
)
from paperworkshell.worker.protocol.models import (
    IMAGE_TRANSFORM_CAPABILITY,
    IMAGE_TRANSFORM_OUTPUT_CONTENT_KIND,
    CancelReason,
    CaseObjectReference,
    OperationOutcome,
    OperationStartPayload,
    ReferenceArea,
    ResourceLimits,
)


def _uuid(number: int) -> UUID:
    return UUID(f"8b000000-0000-4000-8000-{number:012d}")


def _intent() -> ImageTransformIntent:
    return ImageTransformIntent(
        crop=ImageNormalizedCrop(100_000, 0, 900_000, 1_000_000),
        additional_rotation=ImageQuarterTurn.DEGREES_90,
        deskew_angle_millidegrees_clockwise=2_000,
        contrast_factor_millionths=1_250_000,
        background_cleanup_strength_millionths=250_000,
    )


def _request() -> ImagePageTransformRequest:
    return ImagePageTransformRequest(
        case_id=_uuid(1),
        page_id=_uuid(3),
        intent=_intent(),
        maximum_width_pixels=2,
        maximum_height_pixels=2,
    )


def _policy() -> ImageTransformBrokerPolicy:
    return ImageTransformBrokerPolicy(
        ResourceLimits(
            wall_time_ms=5_000,
            cpu_time_ms=4_000,
            memory_bytes=4 * 1024 * 1024,
            temporary_storage_bytes=1024,
            output_bytes=IMAGE_TRANSFORM_ARTIFACT_HEADER_BYTES + 64,
            page_count=1,
            pixel_count=64,
        )
    )


@dataclass(frozen=True, slots=True)
class _Handle:
    value: int


class _SourceLease:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.handle = _Handle(101)
        self.case_id = _uuid(1)
        self.source_id = _uuid(2)
        self.source_media_type = DetectedMediaType.JPEG
        self.page_id = _uuid(3)
        self.source_page_index = 0
        self.source_page_count = 1
        self.source_byte_size = 100
        self.source_sha256 = "a" * 64

    def __enter__(self) -> Self:
        self.events.append("source.enter")
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.events.append("source.exit")


class _SourceStore:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def borrow_committed_image_page(
        self,
        case_id: UUID,
        page_id: UUID,
        *,
        cancellation: object,
        deadline: object,
    ) -> _SourceLease:
        del cancellation, deadline
        assert (case_id, page_id) == (_uuid(1), _uuid(3))
        self.events.append("source.borrow")
        return _SourceLease(self.events)


class _FailingSourceStore:
    def __init__(self, code: SourceIngestErrorCode) -> None:
        self.code = code

    def borrow_committed_image_page(self, *args: object, **kwargs: object) -> _SourceLease:
        del args, kwargs
        raise SourceIngestError(self.code, "test.private_source_failure")


def _artifact(operation_id: UUID, output_id: UUID) -> bytes:
    pixels = bytes((10, 20, 30, 255)) * 4
    source_pixels = bytes((1, 2, 3, 255)) * 4
    layout = ImageRgba8Layout(2, 2, 8, 4, 16)
    key = ImageTransformCacheKey(
        case_id=_uuid(1),
        source_id=_uuid(2),
        page_id=_uuid(3),
        source_byte_size=100,
        source_sha256="a" * 64,
        source_format=ImageSourceFormat.JPEG,
        source_layout=layout,
        source_pixel_sha256=hashlib.sha256(source_pixels).hexdigest(),
        exif_orientation=ImageExifOrientation.NORMAL,
        intent=_intent(),
        maximum_width_pixels=2,
        maximum_height_pixels=2,
    )
    result = ImageTransformResult(
        source_width_pixels=2,
        source_height_pixels=2,
        crop_left_pixels=0,
        crop_top_pixels=0,
        crop_right_pixels=2,
        crop_bottom_pixels=2,
        rotated_width_pixels=2,
        rotated_height_pixels=2,
        width_pixels=2,
        height_pixels=2,
        row_stride_bytes=8,
        bytes_written=16,
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
        intent_sha256=key.intent.intent_sha256,
    )
    return encode_image_transform_artifact(
        ImageTransformArtifactHeader(operation_id, output_id, key, result),
        pixels,
    )


class _OutputBuffer:
    def __init__(self, capacity: int, worker: _Worker, events: list[str]) -> None:
        self.capacity = capacity
        self.write_handle_value = 202
        self.worker = worker
        self.events = events

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
        assert self.worker.encoded is not None
        assert len(self.worker.encoded) == byte_count
        return self.worker.encoded


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
        capabilities=(IMAGE_TRANSFORM_CAPABILITY,),
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
        self.bad_digest = False
        self.withhold_result = False
        self.terminated = False

    def start(self, case_id: UUID) -> int:
        assert case_id == _uuid(1)
        self.events.append("worker.start")
        return 1

    def query(self) -> WorkerClientSnapshot:
        return _snapshot(
            WorkerLifecyclePhase.READY if self.payload is None else WorkerLifecyclePhase.BUSY,
            self.result,
        )

    def pump(self, timeout_seconds: float = 0.0) -> WorkerClientSnapshot:
        assert timeout_seconds == 0.05
        self.events.append("worker.pump")
        return self.query()

    def duplicate_handle(
        self,
        source_handle: int,
        purpose: BrokeredHandlePurpose,
    ) -> int:
        self.events.append(f"worker.duplicate.{purpose.value}")
        if purpose is BrokeredHandlePurpose.READ_SOURCE:
            assert source_handle == 101
            return 1001
        assert source_handle == 202
        return 1002

    def start_operation(
        self,
        payload: OperationStartPayload,
        *,
        operation_id: UUID | None = None,
    ) -> UUID:
        assert operation_id is not None
        self.payload = payload
        self.operation_id = operation_id
        output_id = next(
            parameter.value
            for parameter in payload.parameters
            if parameter.name == "output_object_id"
        )
        assert type(output_id) is UUID
        self.encoded = _artifact(operation_id, output_id)
        digest = hashlib.sha256(self.encoded).hexdigest()
        output = CaseObjectReference(
            area=ReferenceArea.OPERATION_STAGING,
            object_id=output_id,
            operation_id=operation_id,
            content_kind=IMAGE_TRANSFORM_OUTPUT_CONTENT_KIND,
            expected_byte_size=len(self.encoded),
            expected_sha256="0" * 64 if self.bad_digest else digest,
        )
        if not self.withhold_result:
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


def _system() -> tuple[BrokeredImagePageTransformer, _Worker, list[str]]:
    events: list[str] = []
    worker = _Worker(events)
    identifiers = iter((_uuid(20), _uuid(21)))

    def output_buffer_factory(capacity: int) -> _OutputBuffer:
        return _OutputBuffer(capacity, worker, events)

    transformer = BrokeredImagePageTransformer(
        worker,
        _SourceStore(events),
        _policy(),
        heavy_operations=HeavyOperationCoordinator(1),
        id_factory=lambda: next(identifiers),
        output_buffer_factory=output_buffer_factory,
    )
    return transformer, worker, events


@pytest.mark.parametrize(
    ("source_code", "image_code"),
    (
        (SourceIngestErrorCode.CANCELLED, ImagePageErrorCode.CANCELLED),
        (SourceIngestErrorCode.TIMED_OUT, ImagePageErrorCode.TIMED_OUT),
        (SourceIngestErrorCode.CAPACITY_EXCEEDED, ImagePageErrorCode.LIMIT_EXCEEDED),
        (SourceIngestErrorCode.SOURCE_UNAVAILABLE, ImagePageErrorCode.SOURCE_INVALID),
    ),
)
def test_source_failure_preserves_cancel_timeout_and_capacity_semantics(
    source_code: SourceIngestErrorCode,
    image_code: ImagePageErrorCode,
) -> None:
    events: list[str] = []
    worker = _Worker(events)
    transformer = BrokeredImagePageTransformer(
        worker,
        _FailingSourceStore(source_code),
        _policy(),
        heavy_operations=HeavyOperationCoordinator(1),
    )

    with pytest.raises(ImagePageError) as caught:
        transformer.transform(
            _request(),
            deadline=lambda: False,
            cancellation=lambda: False,
            progress=lambda _phase: None,
        )

    assert caught.value.code is image_code
    assert caught.value.context == "image.transform_source"
    assert "worker.start" not in events


def test_transform_runs_one_shot_worker_and_authenticates_exact_intent() -> None:
    transformer, worker, events = _system()
    phases: list[ImagePagePreviewProgressPhase] = []

    result = transformer.transform(
        _request(),
        deadline=lambda: False,
        cancellation=lambda: False,
        progress=phases.append,
    )

    assert result.snapshot.intent == _intent()
    assert result.pixels == bytes((10, 20, 30, 255)) * 4
    assert phases == [
        ImagePagePreviewProgressPhase.LOOKUP,
        ImagePagePreviewProgressPhase.GENERATING,
        ImagePagePreviewProgressPhase.VALIDATING,
    ]
    assert worker.payload is not None
    assert worker.payload.operation_kind == "image.transform"
    parameters = {parameter.name: parameter.value for parameter in worker.payload.parameters}
    assert parameters["quarter_turns_clockwise"] == 1
    assert parameters["deskew_millidegrees_clockwise"] == 2_000
    assert parameters["contrast_factor_millionths"] == 1_250_000
    assert parameters["background_cleanup_strength_millionths"] == 250_000
    assert worker.terminated
    assert events.index("worker.terminate") < events.index("buffer.read")
    assert events.index("buffer.exit") < events.index("source.exit")


def test_malformed_worker_digest_fails_after_one_shot_cleanup() -> None:
    transformer, worker, events = _system()
    worker.bad_digest = True

    with pytest.raises(ImagePageError) as caught:
        transformer.transform(
            _request(),
            deadline=lambda: False,
            cancellation=lambda: False,
            progress=lambda _phase: None,
        )

    assert caught.value.code is ImagePageErrorCode.RESULT_INVALID
    assert worker.terminated
    assert events[-1] == "source.exit"


def test_late_cancellation_suppresses_worker_output_and_closes_authorities() -> None:
    transformer, worker, events = _system()
    worker.withhold_result = True
    calls = 0

    def cancellation() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 4

    with pytest.raises(ImagePageError) as caught:
        transformer.transform(
            _request(),
            deadline=lambda: False,
            cancellation=cancellation,
            progress=lambda _phase: None,
        )

    assert caught.value.code is ImagePageErrorCode.CANCELLED
    assert "worker.cancel.user_requested" in events
    assert worker.terminated
    assert events[-1] == "source.exit"

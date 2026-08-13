"""Focused owner-thread tests for brokered JPEG/PNG normalization."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from types import TracebackType
from typing import Self
from uuid import UUID

import pytest

from paperworkshell.domain.image_pages import (
    IMAGE_PAGE_ARTIFACT_HEADER_BYTES,
    ImageAlphaNormalizationPolicy,
    ImageExifOrientation,
    ImageNormalizationResult,
    ImagePageArtifactHeader,
    ImagePageError,
    ImagePageErrorCode,
    ImagePageMetadata,
    ImagePagePreviewProgressPhase,
    ImagePagePreviewRequest,
    ImageQuarterTurn,
    ImageSourceColorModel,
    ImageSourceFormat,
    encode_image_page_artifact,
)
from paperworkshell.domain.import_precheck import DetectedMediaType
from paperworkshell.domain.pdf_thumbnails import PdfThumbnailError, PdfThumbnailErrorCode
from paperworkshell.domain.source_ingest import (
    PreparedSourceSummary,
    SourceImportProgress,
    SourceIngestError,
    SourceIngestErrorCode,
)
from paperworkshell.worker.client.models import (
    WorkerClientSnapshot,
    WorkerLifecyclePhase,
    WorkerOperationResultSnapshot,
)
from paperworkshell.worker.client.process import BrokeredHandlePurpose
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.image_page_broker import (
    BrokeredImagePageGenerator,
    BrokeredImageSourceInspector,
    ImageNormalizeBrokerPolicy,
    _ImageNormalizeBrokerCore,
)
from paperworkshell.worker.protocol.models import (
    IMAGE_NORMALIZE_CAPABILITY,
    IMAGE_NORMALIZE_OUTPUT_CONTENT_KIND,
    CancelReason,
    CaseObjectReference,
    ErrorCode,
    OperationOutcome,
    OperationStartPayload,
    ReferenceArea,
    ResourceLimits,
)


def _uuid(number: int) -> UUID:
    return UUID(f"8a000000-0000-4000-8000-{number:012d}")


def _request() -> ImagePagePreviewRequest:
    return ImagePagePreviewRequest(
        case_id=_uuid(1),
        page_id=_uuid(3),
        maximum_width_pixels=2,
        maximum_height_pixels=2,
    )


def _policy() -> ImageNormalizeBrokerPolicy:
    return ImageNormalizeBrokerPolicy(
        ResourceLimits(
            wall_time_ms=5_000,
            cpu_time_ms=4_000,
            memory_bytes=1_024 * 1_024,
            temporary_storage_bytes=64,
            output_bytes=IMAGE_PAGE_ARTIFACT_HEADER_BYTES + 64,
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
        self.lease = _SourceLease(events)

    def borrow_committed_image_page(
        self,
        case_id: UUID,
        page_id: UUID,
        *,
        cancellation: object,
        deadline: object,
    ) -> _SourceLease:
        del cancellation, deadline
        assert case_id == _uuid(1)
        assert page_id == _uuid(3)
        self.events.append("source.borrow")
        return self.lease


def _artifact(operation_id: UUID, output_id: UUID, width: int, height: int) -> bytes:
    pixels = bytes((10, 20, 30, 255)) * (width * height)
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
    result = ImageNormalizationResult(
        metadata=metadata,
        additional_rotation=ImageQuarterTurn.DEGREES_0,
        width_pixels=width,
        height_pixels=height,
        row_stride_bytes=width * 4,
        bytes_written=len(pixels),
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
    )
    return encode_image_page_artifact(
        ImagePageArtifactHeader(operation_id, output_id, result),
        pixels,
    )


class _OutputBuffer:
    def __init__(self, capacity: int, worker: _Worker, events: list[str]) -> None:
        self.capacity = capacity
        self.write_handle_value = 202
        self.worker = worker
        self.events = events
        self.closed = False

    def __enter__(self) -> Self:
        self.events.append("buffer.enter")
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.closed = True
        self.events.append("buffer.exit")

    def read_exact(self, byte_count: int) -> bytes:
        self.events.append("buffer.read")
        if self.worker.bad_buffer:
            raise PdfThumbnailError(
                PdfThumbnailErrorCode.RESULT_INVALID,
                "thumbnail.synthetic",
            )
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
        capabilities=(IMAGE_NORMALIZE_CAPABILITY,),
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
        self.bad_buffer = False
        self.withhold_result = False
        self.fail = False
        self.terminated = False

    def start(self, case_id: UUID) -> int:
        assert case_id == _uuid(1)
        self.events.append("worker.start")
        return 1

    def query(self) -> WorkerClientSnapshot:
        self.events.append("worker.query")
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
        maximum_width = next(
            parameter.value
            for parameter in payload.parameters
            if parameter.name == "maximum_width_pixels"
        )
        maximum_height = next(
            parameter.value
            for parameter in payload.parameters
            if parameter.name == "maximum_height_pixels"
        )
        assert type(maximum_width) is int and type(maximum_height) is int
        self.encoded = _artifact(
            operation_id,
            output_id,
            min(2, maximum_width),
            min(2, maximum_height),
        )
        digest = hashlib.sha256(self.encoded).hexdigest()
        output = CaseObjectReference(
            area=ReferenceArea.OPERATION_STAGING,
            object_id=output_id,
            operation_id=operation_id,
            content_kind=IMAGE_NORMALIZE_OUTPUT_CONTENT_KIND,
            expected_byte_size=len(self.encoded),
            expected_sha256="0" * 64 if self.bad_digest else digest,
        )
        completed = (
            WorkerOperationResultSnapshot(
                1,
                operation_id,
                OperationOutcome.FAILED,
                (),
                ErrorCode.WORKER_OPERATION_FAILED,
            )
            if self.fail
            else WorkerOperationResultSnapshot(
                1,
                operation_id,
                OperationOutcome.SUCCEEDED,
                (output,),
                None,
            )
        )
        if not self.withhold_result:
            self.result = completed
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


def _system() -> tuple[
    BrokeredImagePageGenerator,
    _SourceStore,
    _Worker,
    list[str],
]:
    events: list[str] = []
    sources = _SourceStore(events)
    worker = _Worker(events)
    ids = iter((_uuid(20), _uuid(21)))

    def output_buffer_factory(capacity: int) -> _OutputBuffer:
        return _OutputBuffer(capacity, worker, events)

    core = _ImageNormalizeBrokerCore(
        worker,
        _policy(),
        heavy_operations=HeavyOperationCoordinator(1),
        id_factory=lambda: next(ids),
        output_buffer_factory=output_buffer_factory,
    )
    return BrokeredImagePageGenerator(core, sources), sources, worker, events


def test_preview_runs_one_shot_worker_then_authenticates_exact_artifact() -> None:
    generator, _, worker, events = _system()
    phases: list[ImagePagePreviewProgressPhase] = []

    result = generator.get_or_generate(
        _request(),
        deadline=lambda: False,
        cancellation=lambda: False,
        progress=phases.append,
    )

    assert result.snapshot.pixels == bytes((10, 20, 30, 255)) * 4
    assert phases == [
        ImagePagePreviewProgressPhase.LOOKUP,
        ImagePagePreviewProgressPhase.GENERATING,
        ImagePagePreviewProgressPhase.VALIDATING,
    ]
    assert worker.payload is not None
    assert worker.payload.inputs[0].object_id == _uuid(2)
    assert worker.payload.inputs[0].content_kind == "jpeg"
    assert worker.payload.inputs[0].expected_sha256 == "a" * 64
    assert worker.terminated
    assert events.index("worker.terminate") < events.index("buffer.read")
    assert events.index("buffer.exit") < events.index("source.exit")


def test_malformed_worker_digest_fails_after_one_shot_cleanup() -> None:
    generator, _, worker, events = _system()
    worker.bad_digest = True

    with pytest.raises(ImagePageError) as caught:
        generator.get_or_generate(
            _request(),
            deadline=lambda: False,
            cancellation=lambda: False,
            progress=lambda _phase: None,
        )

    assert caught.value.code is ImagePageErrorCode.RESULT_INVALID
    assert worker.terminated
    assert "buffer.exit" in events
    assert events[-1] == "source.exit"


def test_closed_worker_failure_is_rebuilt_without_context() -> None:
    generator, _, worker, events = _system()
    worker.fail = True

    with pytest.raises(ImagePageError) as caught:
        generator.get_or_generate(
            _request(),
            deadline=lambda: False,
            cancellation=lambda: False,
            progress=lambda _phase: None,
        )

    assert caught.value.code is ImagePageErrorCode.DEPENDENCY_FAILED
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert worker.terminated
    assert events[-1] == "source.exit"


def test_pdf_specific_buffer_failure_is_sanitized_to_image_error() -> None:
    generator, _, worker, _events = _system()
    worker.bad_buffer = True

    with pytest.raises(ImagePageError) as caught:
        generator.get_or_generate(
            _request(),
            deadline=lambda: False,
            cancellation=lambda: False,
            progress=lambda _phase: None,
        )

    assert caught.value.code is ImagePageErrorCode.DESTINATION_FAILED
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_late_cancellation_requests_worker_cancel_and_releases_authorities() -> None:
    generator, _, worker, events = _system()
    worker.withhold_result = True
    probes = 0

    def cancellation() -> bool:
        nonlocal probes
        probes += 1
        return probes >= 5

    with pytest.raises(ImagePageError) as caught:
        generator.get_or_generate(
            _request(),
            deadline=lambda: False,
            cancellation=cancellation,
            progress=lambda _phase: None,
        )

    assert caught.value.code is ImagePageErrorCode.CANCELLED
    assert "worker.cancel.user_requested" in events
    assert worker.terminated
    assert events[-1] == "source.exit"


def test_deadline_before_worker_start_never_acquires_worker_authority() -> None:
    generator, _, worker, events = _system()

    with pytest.raises(ImagePageError) as caught:
        generator.get_or_generate(
            _request(),
            deadline=lambda: True,
            cancellation=lambda: False,
            progress=lambda _phase: None,
        )

    assert caught.value.code is ImagePageErrorCode.TIMED_OUT
    assert worker.payload is None
    assert "worker.start" not in events


class _PreparedLease:
    def __init__(self, summary: PreparedSourceSummary, events: list[str]) -> None:
        self.summary = summary
        self.handle_value = 101
        self.events = events

    def __enter__(self) -> Self:
        self.events.append("prepared.enter")
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.events.append("prepared.exit")


class _PreparedStore:
    def __init__(self, summary: PreparedSourceSummary, events: list[str]) -> None:
        self.lease = _PreparedLease(summary, events)

    def borrow_prepared_source(self, summary: PreparedSourceSummary) -> _PreparedLease:
        assert summary == self.lease.summary
        return self.lease


def test_prepared_image_inspection_performs_real_worker_normalization() -> None:
    generator, _, worker, events = _system()
    summary = PreparedSourceSummary(
        pending_id=_uuid(4),
        case_id=_uuid(1),
        source_id=_uuid(2),
        media_type=DetectedMediaType.JPEG,
        byte_size=100,
        sha256="a" * 64,
    )
    inspector = BrokeredImageSourceInspector(
        generator._core,
        _PreparedStore(summary, events),
    )
    progress: list[SourceImportProgress] = []

    completed = inspector.inspect(
        summary,
        deadline=lambda: False,
        cancellation=lambda: False,
        progress=progress.append,
    )

    assert completed.pending_id == summary.pending_id
    assert completed.page_count == 1
    assert [checkpoint.completed_units for checkpoint in progress] == [0, 1]
    assert worker.terminated
    assert events[-1] == "prepared.exit"


def test_prepared_image_inspection_maps_malformed_worker_output() -> None:
    generator, _, worker, events = _system()
    worker.bad_digest = True
    summary = PreparedSourceSummary(
        pending_id=_uuid(4),
        case_id=_uuid(1),
        source_id=_uuid(2),
        media_type=DetectedMediaType.PNG,
        byte_size=100,
        sha256="a" * 64,
    )
    inspector = BrokeredImageSourceInspector(
        generator._core,
        _PreparedStore(summary, events),
    )

    with pytest.raises(SourceIngestError) as caught:
        inspector.inspect(
            summary,
            deadline=lambda: False,
            cancellation=lambda: False,
            progress=lambda _progress: None,
        )

    assert caught.value.code is SourceIngestErrorCode.INSPECT_FAILED
    assert worker.terminated
    assert events[-1] == "prepared.exit"

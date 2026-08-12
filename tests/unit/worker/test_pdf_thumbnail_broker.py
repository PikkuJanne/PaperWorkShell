from __future__ import annotations

import hashlib
from dataclasses import dataclass
from types import TracebackType
from typing import Self, cast
from uuid import UUID

import pytest

from paperworkshell.domain.pdf_rendering import (
    PdfPageBox,
    PdfPageRenderResult,
    PdfRenderRotation,
)
from paperworkshell.domain.pdf_thumbnails import (
    PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES,
    PdfThumbnailArtifact,
    PdfThumbnailCacheKey,
    PdfThumbnailCacheStatus,
    PdfThumbnailError,
    PdfThumbnailErrorCode,
    PdfThumbnailProgressPhase,
    PdfThumbnailRequest,
    encode_pdf_thumbnail_artifact,
    pdf_thumbnail_header_from_render_result,
)
from paperworkshell.domain.source_ingest import SourceIngestError, SourceIngestErrorCode
from paperworkshell.worker.client.errors import (
    WorkerClientContext,
    WorkerClientError,
    WorkerClientErrorCode,
)
from paperworkshell.worker.client.models import (
    WorkerClientSnapshot,
    WorkerLifecyclePhase,
    WorkerOperationResultSnapshot,
)
from paperworkshell.worker.client.process import BrokeredHandlePurpose
from paperworkshell.worker.heavy_operations import HeavyOperationCoordinator
from paperworkshell.worker.pdf_thumbnail_broker import (
    BrokeredPdfThumbnailGenerator,
    CancellationProbe,
    DeadlineProbe,
    PdfThumbnailBrokerPolicy,
)
from paperworkshell.worker.pdf_thumbnail_profile import (
    PDF_THUMBNAIL_RENDERER_PROFILE_SHA256,
)
from paperworkshell.worker.protocol.models import (
    PDF_THUMBNAIL_CAPABILITY,
    PDF_THUMBNAIL_CONTENT_KIND,
    CancelReason,
    CaseObjectReference,
    OperationOutcome,
    OperationStartPayload,
    ReferenceArea,
    ResourceLimits,
)


def _uuid(number: int) -> UUID:
    return UUID(f"82000000-0000-4000-8000-{number:012d}")


def _request() -> PdfThumbnailRequest:
    return PdfThumbnailRequest(
        case_id=_uuid(1),
        page_id=_uuid(3),
        maximum_width_pixels=2,
        maximum_height_pixels=2,
    )


def _policy(*, pixel_count: int = 16, output_bytes: int | None = None) -> PdfThumbnailBrokerPolicy:
    return PdfThumbnailBrokerPolicy(
        ResourceLimits(
            wall_time_ms=5_000,
            cpu_time_ms=4_000,
            memory_bytes=1_024,
            temporary_storage_bytes=2_048,
            output_bytes=output_bytes or PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES + 64,
            page_count=10,
            pixel_count=pixel_count,
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
        self.page_id = _uuid(3)
        self.source_page_index = 0
        self.source_page_count = 2
        self.source_byte_size = 100
        self.source_sha256 = "a" * 64

    def __enter__(self) -> Self:
        self.events.append("source.enter")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback
        self.events.append("source.exit")


class _SourceStore:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.lease = _SourceLease(events)
        self.error: SourceIngestError | None = None

    def borrow_committed_pdf_page(
        self,
        case_id: UUID,
        page_id: UUID,
        *,
        cancellation: object,
        deadline: object,
    ) -> _SourceLease:
        del cancellation, deadline
        self.events.append("source.borrow")
        assert case_id == _uuid(1)
        assert page_id == _uuid(3)
        if self.error is not None:
            raise self.error
        return self.lease


@dataclass(frozen=True, slots=True)
class _Lookup:
    is_hit: bool
    corrupt_removed: bool
    artifact: PdfThumbnailArtifact | None


@dataclass(frozen=True, slots=True)
class _Publication:
    artifact: PdfThumbnailArtifact
    published: bool = True


def _artifact(
    key: PdfThumbnailCacheKey,
    operation_id: UUID,
    cache_object_id: UUID,
) -> PdfThumbnailArtifact:
    pixels = b"\x10\x20\x30\xff"
    result = PdfPageRenderResult(
        page_index=key.source_page_index,
        width_pixels=1,
        height_pixels=1,
        row_stride_bytes=4,
        bytes_written=4,
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
        page_box=PdfPageBox.CROP,
        intrinsic_rotation=PdfRenderRotation.DEGREES_0,
        additional_rotation=key.additional_rotation,
    )
    return PdfThumbnailArtifact(
        pdf_thumbnail_header_from_render_result(
            operation_id,
            cache_object_id,
            key,
            result,
        ),
        pixels,
    )


class _Stage:
    def __init__(self, key: PdfThumbnailCacheKey, events: list[str]) -> None:
        self.key = key
        self.events = events
        self.cache_object_id = _uuid(20)
        self.maximum_bytes = (
            PDF_THUMBNAIL_ARTIFACT_HEADER_BYTES
            + key.maximum_width_pixels * key.maximum_height_pixels * 4
        )
        self.operation_id: UUID | None = None
        self.written: bytes | None = None
        self.validated = False
        self.published = False
        self.closed = False

    def __enter__(self) -> Self:
        self.events.append("stage.enter")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback
        self.closed = True
        self.events.append("stage.exit")

    def current_artifact(self) -> PdfThumbnailArtifact:
        assert self.operation_id is not None
        return _artifact(self.key, self.operation_id, self.cache_object_id)

    def validate(self, *, expected_operation_id: UUID) -> PdfThumbnailArtifact:
        self.events.append("stage.validate")
        assert self.operation_id == expected_operation_id
        assert self.written == encode_pdf_thumbnail_artifact(
            self.current_artifact().header,
            self.current_artifact().payload,
        )
        self.validated = True
        return self.current_artifact()

    def write_artifact(self, payload: bytes) -> None:
        self.events.append("stage.write")
        assert self.written is None
        assert 0 < len(payload) <= self.maximum_bytes
        self.written = payload

    def publish(self) -> _Publication:
        self.events.append("stage.publish")
        assert self.validated
        self.published = True
        return _Publication(self.current_artifact())


class _Cache:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.lookup_result = _Lookup(False, False, None)
        self.stage: _Stage | None = None
        self.output_buffer: _OutputBuffer | None = None

    def lookup(self, key: PdfThumbnailCacheKey) -> _Lookup:
        self.events.append("cache.lookup")
        return self.lookup_result

    def create_stage(self, key: PdfThumbnailCacheKey) -> _Stage:
        self.events.append("cache.stage")
        self.stage = _Stage(key, self.events)
        return self.stage


class _OutputBuffer:
    def __init__(self, capacity: int, cache: _Cache, events: list[str]) -> None:
        self.capacity = capacity
        self.write_handle_value = 202
        self.cache = cache
        self.events = events
        self.closed = False

    def __enter__(self) -> Self:
        self.events.append("buffer.enter")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback
        self.closed = True
        self.events.append("buffer.exit")

    def read_exact(self, byte_count: int) -> bytes:
        self.events.append("buffer.read")
        stage = self.cache.stage
        assert stage is not None
        artifact = stage.current_artifact()
        encoded = encode_pdf_thumbnail_artifact(artifact.header, artifact.payload)
        assert len(encoded) == byte_count
        return encoded


def _snapshot(
    *,
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
        capabilities=(PDF_THUMBNAIL_CAPABILITY,),
        active_operation=None,
        last_result=result,
        last_failure=None,
    )


class _Worker:
    def __init__(self, events: list[str], cache: _Cache) -> None:
        self.events = events
        self.cache = cache
        self.payload: OperationStartPayload | None = None
        self.operation_id: UUID | None = None
        self.result: WorkerOperationResultSnapshot | None = None
        self.cancelled = False
        self.termination_error: WorkerClientError | None = None
        self.bad_digest = False
        self.outcome = OperationOutcome.SUCCEEDED
        self.withhold_result_until_cancel = False
        self.fault_after_start = False

    def start(self, case_id: UUID) -> int:
        assert case_id == _uuid(1)
        self.events.append("worker.start")
        return 1

    def query(self) -> WorkerClientSnapshot:
        self.events.append("worker.query")
        return _snapshot(
            phase=(
                WorkerLifecyclePhase.READY
                if self.payload is None
                else (
                    WorkerLifecyclePhase.FAULTED
                    if self.fault_after_start
                    else WorkerLifecyclePhase.BUSY
                )
            ),
            result=self.result,
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
        stage = self.cache.stage
        assert stage is not None
        stage.operation_id = operation_id
        outputs: tuple[CaseObjectReference, ...]
        if self.outcome is OperationOutcome.SUCCEEDED:
            encoded = encode_pdf_thumbnail_artifact(
                stage.current_artifact().header,
                stage.current_artifact().payload,
            )
            output = CaseObjectReference(
                area=ReferenceArea.OPERATION_STAGING,
                object_id=stage.cache_object_id,
                operation_id=operation_id,
                content_kind=PDF_THUMBNAIL_CONTENT_KIND,
                expected_byte_size=len(encoded),
                expected_sha256=(
                    "0" * 64 if self.bad_digest else hashlib.sha256(encoded).hexdigest()
                ),
            )
            outputs = (output,)
        else:
            outputs = ()
        completed = WorkerOperationResultSnapshot(
            1,
            operation_id,
            self.outcome,
            outputs,
            None,
        )
        if not self.withhold_result_until_cancel:
            self.result = completed
        return operation_id

    def cancel(self, reason: CancelReason = CancelReason.USER_REQUESTED) -> UUID:
        self.events.append(f"worker.cancel.{reason.value}")
        self.cancelled = True
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
        if self.termination_error is not None:
            raise self.termination_error


def _system() -> tuple[
    BrokeredPdfThumbnailGenerator,
    _SourceStore,
    _Cache,
    _Worker,
    list[str],
]:
    events: list[str] = []
    sources = _SourceStore(events)
    cache = _Cache(events)
    worker = _Worker(events, cache)

    def output_buffer_factory(capacity: int) -> _OutputBuffer:
        buffer = _OutputBuffer(capacity, cache, events)
        cache.output_buffer = buffer
        return buffer

    generator = BrokeredPdfThumbnailGenerator(
        worker,
        sources,
        cache,
        _policy(),
        heavy_operations=HeavyOperationCoordinator(1),
        id_factory=lambda: _uuid(10),
        output_buffer_factory=output_buffer_factory,
    )
    return generator, sources, cache, worker, events


def test_cache_miss_runs_one_exact_worker_then_validates_and_publishes() -> None:
    generator, _, cache, worker, events = _system()
    phases: list[PdfThumbnailProgressPhase] = []

    result = generator.get_or_generate(
        _request(),
        deadline=lambda: False,
        cancellation=lambda: False,
        progress=phases.append,
    )

    assert result.cache_status is PdfThumbnailCacheStatus.GENERATED
    assert result.snapshot.pixels == b"\x10\x20\x30\xff"
    assert phases == [
        PdfThumbnailProgressPhase.RENDERING,
        PdfThumbnailProgressPhase.VALIDATING,
    ]
    assert worker.payload is not None
    assert worker.payload.inputs[0].object_id == _uuid(2)
    assert worker.payload.inputs[0].expected_sha256 == "a" * 64
    assert worker.payload.requested_limits.page_count == 2
    assert cache.stage is not None and cache.stage.published and cache.stage.closed
    assert events.index("worker.terminate") < events.index("stage.validate")
    assert events.index("buffer.read") < events.index("stage.write")
    assert events.index("stage.write") < events.index("stage.validate")
    assert events.index("stage.validate") < events.index("stage.publish")


def test_authenticated_cache_hit_avoids_stage_and_worker() -> None:
    generator, sources, cache, worker, events = _system()
    request = _request()
    key = PdfThumbnailCacheKey(
        case_id=request.case_id,
        source_id=sources.lease.source_id,
        page_id=request.page_id,
        source_page_index=0,
        source_byte_size=100,
        source_sha256="a" * 64,
        maximum_width_pixels=2,
        maximum_height_pixels=2,
        additional_rotation=PdfRenderRotation.DEGREES_0,
        renderer_profile_sha256=PDF_THUMBNAIL_RENDERER_PROFILE_SHA256,
    )
    cache.lookup_result = _Lookup(True, False, _artifact(key, _uuid(50), _uuid(51)))
    phases: list[PdfThumbnailProgressPhase] = []

    result = generator.get_or_generate(
        request,
        deadline=lambda: False,
        cancellation=lambda: False,
        progress=phases.append,
    )

    assert result.cache_status is PdfThumbnailCacheStatus.HIT
    assert phases == [PdfThumbnailProgressPhase.VALIDATING]
    assert cache.stage is None
    assert worker.payload is None
    assert "worker.start" not in events


def test_removed_corrupt_entry_is_reported_as_rebuilt() -> None:
    generator, _, cache, _, _ = _system()
    cache.lookup_result = _Lookup(False, True, None)

    result = generator.get_or_generate(
        _request(),
        deadline=lambda: False,
        cancellation=lambda: False,
        progress=lambda _phase: None,
    )

    assert result.cache_status is PdfThumbnailCacheStatus.REBUILT


class _CancellationSequence:
    def __init__(self, *values: bool) -> None:
        self.values = list(values)

    def __call__(self) -> bool:
        return self.values.pop(0) if self.values else True


def test_cancellation_sends_worker_cancel_and_never_validates_stage() -> None:
    generator, _, cache, worker, events = _system()
    worker.withhold_result_until_cancel = True
    cancellation = _CancellationSequence(False, False, False, True)

    with pytest.raises(PdfThumbnailError) as raised:
        generator.get_or_generate(
            _request(),
            deadline=lambda: False,
            cancellation=cast(CancellationProbe, cancellation),
            progress=lambda _phase: None,
        )

    assert raised.value.code is PdfThumbnailErrorCode.CANCELLED
    assert worker.cancelled
    assert "worker.terminate" in events
    assert cache.stage is not None and not cache.stage.validated and not cache.stage.published


def test_terminal_result_is_observed_before_late_cancellation_is_sent() -> None:
    generator, _, cache, worker, events = _system()
    cancellation = _CancellationSequence(False, False, False, True)

    with pytest.raises(PdfThumbnailError) as raised:
        generator.get_or_generate(
            _request(),
            deadline=lambda: False,
            cancellation=cast(CancellationProbe, cancellation),
            progress=lambda _phase: None,
        )

    assert raised.value.code is PdfThumbnailErrorCode.CANCELLED
    assert not worker.cancelled
    assert not any(event.startswith("worker.cancel") for event in events)
    assert cache.stage is not None and not cache.stage.validated and not cache.stage.published


def test_operation_deadline_requests_shutdown_cancel_and_persists_nothing() -> None:
    generator, _, cache, worker, events = _system()
    worker.withhold_result_until_cancel = True
    deadline = _CancellationSequence(False, False, False, True)

    with pytest.raises(PdfThumbnailError) as raised:
        generator.get_or_generate(
            _request(),
            deadline=cast(DeadlineProbe, deadline),
            cancellation=lambda: False,
            progress=lambda _phase: None,
        )

    assert raised.value.code is PdfThumbnailErrorCode.TIMED_OUT
    assert "worker.cancel.shutdown" in events
    assert "worker.terminate" in events
    assert cache.stage is not None and cache.stage.written is None
    assert not cache.stage.validated and not cache.stage.published


def test_worker_fault_discards_volatile_output_and_persists_nothing() -> None:
    generator, _, cache, worker, events = _system()
    worker.withhold_result_until_cancel = True
    worker.fault_after_start = True

    with pytest.raises(PdfThumbnailError) as raised:
        generator.get_or_generate(
            _request(),
            deadline=lambda: False,
            cancellation=lambda: False,
            progress=lambda _phase: None,
        )

    assert raised.value.code is PdfThumbnailErrorCode.WORKER_FAILED
    assert "worker.terminate" in events
    assert cache.stage is not None and cache.stage.written is None
    assert not cache.stage.validated and not cache.stage.published


def test_unconfirmed_termination_discards_empty_stage_and_volatile_buffer() -> None:
    generator, _, cache, worker, events = _system()
    worker.termination_error = WorkerClientError(
        WorkerClientErrorCode.TERMINATION_FAILED,
        WorkerClientContext.PROCESS,
    )

    with pytest.raises(PdfThumbnailError) as raised:
        generator.get_or_generate(
            _request(),
            deadline=lambda: False,
            cancellation=lambda: False,
            progress=lambda _phase: None,
        )

    assert raised.value.code is PdfThumbnailErrorCode.WORKER_FAILED
    assert cache.stage is not None and cache.stage.closed and cache.stage.written is None
    assert not cache.stage.validated and not cache.stage.published
    assert cache.output_buffer is not None and cache.output_buffer.closed
    assert "buffer.read" not in events


def test_worker_reference_must_match_before_persistent_stage_write() -> None:
    generator, _, cache, worker, _ = _system()
    worker.bad_digest = True

    with pytest.raises(PdfThumbnailError) as raised:
        generator.get_or_generate(
            _request(),
            deadline=lambda: False,
            cancellation=lambda: False,
            progress=lambda _phase: None,
        )

    assert raised.value.code is PdfThumbnailErrorCode.RESULT_INVALID
    assert cache.stage is not None and cache.stage.written is None
    assert not cache.stage.validated and not cache.stage.published


def test_request_bounds_fail_before_stage_or_worker() -> None:
    events: list[str] = []
    sources = _SourceStore(events)
    cache = _Cache(events)
    worker = _Worker(events, cache)
    generator = BrokeredPdfThumbnailGenerator(
        worker,
        sources,
        cache,
        _policy(pixel_count=1),
        heavy_operations=HeavyOperationCoordinator(1),
        id_factory=lambda: _uuid(10),
    )

    with pytest.raises(PdfThumbnailError) as raised:
        generator.get_or_generate(
            _request(),
            deadline=lambda: False,
            cancellation=lambda: False,
            progress=lambda _phase: None,
        )

    assert raised.value.code is PdfThumbnailErrorCode.LIMIT_EXCEEDED
    assert cache.stage is None
    assert worker.payload is None


def test_source_integrity_failure_maps_to_closed_thumbnail_error() -> None:
    generator, sources, _, _, _ = _system()
    sources.error = SourceIngestError(
        SourceIngestErrorCode.SOURCE_CHANGED,
        "source.private_path",
    )

    with pytest.raises(PdfThumbnailError) as raised:
        generator.get_or_generate(
            _request(),
            deadline=lambda: False,
            cancellation=lambda: False,
            progress=lambda _phase: None,
        )

    assert raised.value.code is PdfThumbnailErrorCode.SOURCE_INVALID
    assert "private" not in str(raised.value)


def test_progress_and_probe_failures_are_sanitized() -> None:
    generator, _, _, _, _ = _system()

    def bad_progress(_phase: PdfThumbnailProgressPhase) -> None:
        raise KeyboardInterrupt("private pixels")

    with pytest.raises(PdfThumbnailError) as progress:
        generator.get_or_generate(
            _request(),
            deadline=lambda: False,
            cancellation=lambda: False,
            progress=bad_progress,
        )
    assert progress.value.code is PdfThumbnailErrorCode.INTERNAL_ERROR
    assert "private" not in str(progress.value)

    with pytest.raises(PdfThumbnailError) as probe:
        generator.get_or_generate(
            _request(),
            deadline=cast(object, lambda: "private path"),  # type: ignore[arg-type]
            cancellation=lambda: False,
            progress=lambda _phase: None,
        )
    assert probe.value.code is PdfThumbnailErrorCode.INTERNAL_ERROR

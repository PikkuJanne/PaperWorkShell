from __future__ import annotations

from collections.abc import Callable
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.app.pdf_thumbnails import (
    CancellationProbe,
    DeadlineProbe,
    PdfThumbnailService,
    ThumbnailPhaseSink,
)
from paperworkshell.domain.pdf_rendering import PdfRenderRotation
from paperworkshell.domain.pdf_thumbnails import (
    PdfThumbnailCacheKey,
    PdfThumbnailCacheStatus,
    PdfThumbnailError,
    PdfThumbnailErrorCode,
    PdfThumbnailProgress,
    PdfThumbnailProgressPhase,
    PdfThumbnailRequest,
    PdfThumbnailResult,
    PdfThumbnailSnapshot,
)


def _uuid(number: int) -> UUID:
    return UUID(f"81000000-0000-4000-8000-{number:012d}")


def _request(page: int, *, case: int = 1) -> PdfThumbnailRequest:
    return PdfThumbnailRequest(
        case_id=_uuid(case),
        page_id=_uuid(page),
        maximum_width_pixels=32,
        maximum_height_pixels=32,
    )


def _result(request: PdfThumbnailRequest) -> PdfThumbnailResult:
    key = PdfThumbnailCacheKey(
        case_id=request.case_id,
        source_id=_uuid(90),
        page_id=request.page_id,
        source_page_index=request.page_id.int % 4,
        source_byte_size=128,
        source_sha256="a" * 64,
        maximum_width_pixels=request.maximum_width_pixels,
        maximum_height_pixels=request.maximum_height_pixels,
        additional_rotation=request.additional_rotation,
        renderer_profile_sha256="b" * 64,
    )
    snapshot = PdfThumbnailSnapshot(
        case_id=request.case_id,
        page_id=request.page_id,
        width_pixels=1,
        height_pixels=1,
        row_stride_bytes=4,
        pixels=b"\x10\x20\x30\xff",
        intrinsic_rotation=PdfRenderRotation.DEGREES_0,
        additional_rotation=request.additional_rotation,
    )
    return PdfThumbnailResult(request, key, snapshot, PdfThumbnailCacheStatus.GENERATED)


class _Generator:
    def __init__(self) -> None:
        self.requests: list[PdfThumbnailRequest] = []
        self.deadlines: list[DeadlineProbe] = []
        self.cancellations: list[CancellationProbe] = []
        self.error: Exception | None = None
        self.result_factory: Callable[[PdfThumbnailRequest], object] = _result
        self.phases: tuple[PdfThumbnailProgressPhase, ...] = (
            PdfThumbnailProgressPhase.RENDERING,
            PdfThumbnailProgressPhase.VALIDATING,
        )

    def get_or_generate(
        self,
        request: PdfThumbnailRequest,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ThumbnailPhaseSink,
    ) -> PdfThumbnailResult:
        self.requests.append(request)
        self.deadlines.append(deadline)
        self.cancellations.append(cancellation)
        if self.error is not None:
            raise self.error
        for phase in self.phases:
            progress(phase)
        return cast(PdfThumbnailResult, self.result_factory(request))


class _Sequence:
    def __init__(self, *values: object) -> None:
        self.values = list(values)
        self.last = values[-1] if values else False

    def __call__(self) -> object:
        value = self.values.pop(0) if self.values else self.last
        self.last = value
        if isinstance(value, BaseException):
            raise value
        return value


def test_service_generates_pages_in_order_with_path_free_aggregate_progress() -> None:
    generator = _Generator()
    operation_id = _uuid(99)
    reported: list[PdfThumbnailProgress] = []
    requests = (_request(10), _request(11))
    service = PdfThumbnailService(generator, id_factory=lambda: operation_id)

    results = service.get_thumbnails(requests, progress=reported.append)

    assert tuple(result.request for result in results) == requests
    assert generator.requests == list(requests)
    assert len({id(deadline) for deadline in generator.deadlines}) == 1
    assert len({id(probe) for probe in generator.cancellations}) == 1
    assert [(item.phase, item.completed_pages) for item in reported] == [
        (PdfThumbnailProgressPhase.LOOKUP, 0),
        (PdfThumbnailProgressPhase.RENDERING, 0),
        (PdfThumbnailProgressPhase.VALIDATING, 0),
        (PdfThumbnailProgressPhase.LOOKUP, 1),
        (PdfThumbnailProgressPhase.RENDERING, 1),
        (PdfThumbnailProgressPhase.VALIDATING, 1),
        (PdfThumbnailProgressPhase.COMPLETE, 2),
    ]
    assert {item.operation_id for item in reported} == {operation_id}
    assert {item.total_pages for item in reported} == {2}


def test_cancellation_stops_before_the_next_page_without_returning_partial_results() -> None:
    generator = _Generator()
    cancellation = _Sequence(False, False, False, True)
    service = PdfThumbnailService(generator)

    with pytest.raises(PdfThumbnailError) as raised:
        service.get_thumbnails(
            (_request(10), _request(11)),
            cancellation=cast(CancellationProbe, cancellation),
        )

    assert raised.value.code is PdfThumbnailErrorCode.CANCELLED
    assert generator.requests == [_request(10)]


@pytest.mark.parametrize(
    "requests",
    (
        (),
        [_request(10)],
        (_request(10), _request(10)),
        (_request(10), _request(11, case=2)),
        (object(),),
    ),
)
def test_invalid_batch_fails_before_generator_or_clock(
    requests: object,
) -> None:
    generator = _Generator()
    clock = _Sequence(KeyboardInterrupt("private clock"))
    service = PdfThumbnailService(generator, monotonic=cast(Callable[[], float], clock))

    with pytest.raises(PdfThumbnailError) as raised:
        service.get_thumbnails(cast(tuple[PdfThumbnailRequest, ...], requests))

    assert raised.value.code is PdfThumbnailErrorCode.MODEL_INVALID
    assert generator.requests == []


@pytest.mark.parametrize("timeout", (True, 0, -1, float("nan"), float("inf"), 3600.1, "2"))
def test_invalid_timeout_fails_before_clock_and_generator(timeout: object) -> None:
    generator = _Generator()
    clock = _Sequence(KeyboardInterrupt("private clock"))
    service = PdfThumbnailService(generator, monotonic=cast(Callable[[], float], clock))

    with pytest.raises(PdfThumbnailError) as raised:
        service.get_thumbnails((_request(10),), timeout_seconds=cast(float, timeout))

    assert raised.value.code is PdfThumbnailErrorCode.MODEL_INVALID
    assert generator.requests == []


def test_deadline_expires_before_generator_work() -> None:
    generator = _Generator()
    clock = _Sequence(0.0, 1.0)
    service = PdfThumbnailService(generator, monotonic=cast(Callable[[], float], clock))

    with pytest.raises(PdfThumbnailError) as raised:
        service.get_thumbnails((_request(10),), timeout_seconds=1.0)

    assert raised.value.code is PdfThumbnailErrorCode.TIMED_OUT
    assert generator.requests == []


def test_generator_failure_is_sanitized_and_invalid_result_is_rejected() -> None:
    generator = _Generator()
    generator.error = RuntimeError("C:/private/source.pdf")
    service = PdfThumbnailService(generator)

    with pytest.raises(PdfThumbnailError) as failed:
        service.get_thumbnails((_request(10),))
    assert failed.value.code is PdfThumbnailErrorCode.WORKER_FAILED
    assert "private" not in str(failed.value)

    generator.error = None
    generator.result_factory = lambda _request: object()
    with pytest.raises(PdfThumbnailError) as invalid:
        service.get_thumbnails((_request(10),))
    assert invalid.value.code is PdfThumbnailErrorCode.RESULT_INVALID


def test_callbacks_cannot_inject_values_or_content_into_failures() -> None:
    generator = _Generator()
    service = PdfThumbnailService(generator)

    with pytest.raises(PdfThumbnailError) as cancelled:
        service.get_thumbnails(
            (_request(10),),
            cancellation=cast(CancellationProbe, lambda: "C:/private/source.pdf"),
        )
    assert cancelled.value.code is PdfThumbnailErrorCode.INTERNAL_ERROR
    assert "private" not in str(cancelled.value)
    assert generator.requests == []

    def fail_progress(_value: PdfThumbnailProgress) -> None:
        raise KeyboardInterrupt("private pixel content")

    with pytest.raises(PdfThumbnailError) as progress:
        service.get_thumbnails((_request(10),), progress=fail_progress)
    assert progress.value.code is PdfThumbnailErrorCode.INTERNAL_ERROR
    assert "private" not in str(progress.value)


def test_page_generator_cannot_emit_terminal_batch_progress() -> None:
    generator = _Generator()
    generator.phases = (PdfThumbnailProgressPhase.COMPLETE,)
    service = PdfThumbnailService(generator)

    with pytest.raises(PdfThumbnailError) as raised:
        service.get_thumbnails((_request(10),))

    assert raised.value.code is PdfThumbnailErrorCode.RESULT_INVALID

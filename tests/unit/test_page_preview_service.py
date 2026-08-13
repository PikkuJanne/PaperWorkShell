from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import replace
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.app.page_previews import (
    CancellationProbe,
    DeadlineProbe,
    ImagePreviewPhaseSink,
    PagePreviewError,
    PagePreviewErrorCode,
    PagePreviewProgress,
    PagePreviewProgressPhase,
    PagePreviewService,
    PdfPreviewPhaseSink,
)
from paperworkshell.domain.image_pages import (
    ImageAlphaNormalizationPolicy,
    ImageExifOrientation,
    ImagePageMetadata,
    ImagePagePreviewProgressPhase,
    ImagePagePreviewRequest,
    ImagePagePreviewResult,
    ImagePagePreviewSnapshot,
    ImageSourceColorModel,
    ImageSourceFormat,
)
from paperworkshell.domain.image_transforms import (
    ImagePageTransformRequest,
    ImagePageTransformResult,
    ImagePageTransformSnapshot,
    ImageTransformIntent,
)
from paperworkshell.domain.pdf_rendering import PdfRenderRotation
from paperworkshell.domain.pdf_thumbnails import (
    PdfThumbnailCacheKey,
    PdfThumbnailCacheStatus,
    PdfThumbnailProgressPhase,
    PdfThumbnailRequest,
    PdfThumbnailResult,
    PdfThumbnailSnapshot,
)


def _uuid(number: int) -> UUID:
    return UUID(f"82000000-0000-4000-8000-{number:012d}")


def _pdf_request(page: int) -> PdfThumbnailRequest:
    return PdfThumbnailRequest(_uuid(1), _uuid(page), 32, 32)


def _image_request(page: int) -> ImagePagePreviewRequest:
    return ImagePagePreviewRequest(_uuid(1), _uuid(page), 32, 32)


def _pdf_result(request: PdfThumbnailRequest) -> PdfThumbnailResult:
    key = PdfThumbnailCacheKey(
        case_id=request.case_id,
        source_id=_uuid(90),
        page_id=request.page_id,
        source_page_index=0,
        source_byte_size=128,
        source_sha256="a" * 64,
        maximum_width_pixels=request.maximum_width_pixels,
        maximum_height_pixels=request.maximum_height_pixels,
        additional_rotation=request.additional_rotation,
        renderer_profile_sha256="b" * 64,
    )
    snapshot = PdfThumbnailSnapshot(
        request.case_id,
        request.page_id,
        1,
        1,
        4,
        b"\x10\x20\x30\xff",
        PdfRenderRotation.DEGREES_0,
        request.additional_rotation,
    )
    return PdfThumbnailResult(request, key, snapshot, PdfThumbnailCacheStatus.GENERATED)


def _image_result(request: ImagePagePreviewRequest) -> ImagePagePreviewResult:
    metadata = ImagePageMetadata(
        source_format=ImageSourceFormat.PNG,
        source_color_model=ImageSourceColorModel.RGB,
        alpha_policy=ImageAlphaNormalizationPolicy.COMPOSITE_ON_OPAQUE_WHITE,
        exif_orientation=ImageExifOrientation.NORMAL,
        encoded_width_pixels=2,
        encoded_height_pixels=1,
        oriented_width_pixels=2,
        oriented_height_pixels=1,
        component_count=3,
        bits_per_component=8,
        frame_count=1,
        metadata_record_count=0,
        metadata_byte_count=0,
        has_alpha=False,
        has_embedded_color_profile=False,
        has_exif=False,
    )
    snapshot = ImagePagePreviewSnapshot(
        request.case_id,
        request.page_id,
        2,
        1,
        8,
        bytes((10, 20, 30, 255)) * 2,
        metadata,
        request.additional_rotation,
    )
    return ImagePagePreviewResult(request, snapshot)


def _transform_request(page: int) -> ImagePageTransformRequest:
    return ImagePageTransformRequest(_uuid(1), _uuid(page), ImageTransformIntent(), 32, 32)


def _transform_result(request: ImagePageTransformRequest) -> ImagePageTransformResult:
    pixels = bytes((10, 20, 30, 255))
    snapshot = ImagePageTransformSnapshot(
        case_id=request.case_id,
        source_id=_uuid(90),
        page_id=request.page_id,
        intent=request.intent,
        width_pixels=1,
        height_pixels=1,
        row_stride_bytes=4,
        bytes_written=4,
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
        cache_key_sha256="c" * 64,
    )
    return ImagePageTransformResult(request, snapshot, pixels)


class _PdfGenerator:
    def __init__(self) -> None:
        self.requests: list[PdfThumbnailRequest] = []
        self.deadlines: list[DeadlineProbe] = []
        self.cancellations: list[CancellationProbe] = []
        self.error: BaseException | None = None
        self.before_cancellation: Callable[[], None] | None = None
        self.before_deadline: Callable[[], None] | None = None
        self.before_progress: Callable[[], None] | None = None
        self.poll_cancellation = False
        self.poll_deadline = False
        self.cancellation_results: list[bool] = []
        self.deadline_results: list[bool] = []
        self.completed = False
        self.result_factory: Callable[[PdfThumbnailRequest], object] = _pdf_result
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
        progress: PdfPreviewPhaseSink,
    ) -> PdfThumbnailResult:
        self.requests.append(request)
        self.deadlines.append(deadline)
        self.cancellations.append(cancellation)
        if self.error is not None:
            raise self.error
        if self.poll_deadline:
            if self.before_deadline is not None:
                self.before_deadline()
            self.deadline_results.append(deadline())
        if self.poll_cancellation:
            if self.before_cancellation is not None:
                self.before_cancellation()
            self.cancellation_results.append(cancellation())
        for phase in self.phases:
            if self.before_progress is not None:
                self.before_progress()
            progress(phase)
        result = cast(PdfThumbnailResult, self.result_factory(request))
        self.completed = True
        return result


class _ImageGenerator:
    def __init__(self) -> None:
        self.requests: list[ImagePagePreviewRequest] = []
        self.deadlines: list[DeadlineProbe] = []
        self.cancellations: list[CancellationProbe] = []
        self.error: BaseException | None = None
        self.before_cancellation: Callable[[], None] | None = None
        self.before_deadline: Callable[[], None] | None = None
        self.before_progress: Callable[[], None] | None = None
        self.poll_cancellation = False
        self.poll_deadline = False
        self.cancellation_results: list[bool] = []
        self.deadline_results: list[bool] = []
        self.completed = False
        self.result_factory: Callable[[ImagePagePreviewRequest], object] = _image_result
        self.phases: tuple[ImagePagePreviewProgressPhase, ...] = (
            ImagePagePreviewProgressPhase.GENERATING,
            ImagePagePreviewProgressPhase.VALIDATING,
        )

    def get_or_generate(
        self,
        request: ImagePagePreviewRequest,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ImagePreviewPhaseSink,
    ) -> ImagePagePreviewResult:
        self.requests.append(request)
        self.deadlines.append(deadline)
        self.cancellations.append(cancellation)
        if self.error is not None:
            raise self.error
        if self.poll_deadline:
            if self.before_deadline is not None:
                self.before_deadline()
            self.deadline_results.append(deadline())
        if self.poll_cancellation:
            if self.before_cancellation is not None:
                self.before_cancellation()
            self.cancellation_results.append(cancellation())
        for phase in self.phases:
            if self.before_progress is not None:
                self.before_progress()
            progress(phase)
        result = cast(ImagePagePreviewResult, self.result_factory(request))
        self.completed = True
        return result


class _TransformGenerator:
    def __init__(self) -> None:
        self.requests: list[ImagePageTransformRequest] = []

    def transform(
        self,
        request: ImagePageTransformRequest,
        *,
        deadline: DeadlineProbe,
        cancellation: CancellationProbe,
        progress: ImagePreviewPhaseSink,
    ) -> ImagePageTransformResult:
        self.requests.append(request)
        assert not deadline()
        assert not cancellation()
        progress(ImagePagePreviewProgressPhase.GENERATING)
        progress(ImagePagePreviewProgressPhase.VALIDATING)
        return _transform_result(request)


class _Sequence:
    def __init__(self, *values: object) -> None:
        self._values = list(values)
        self._last = values[-1] if values else False

    def __call__(self) -> object:
        value = self._values.pop(0) if self._values else self._last
        self._last = value
        if isinstance(value, BaseException):
            raise value
        return value


class _MutableClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def _selected_generator(
    source: str,
) -> tuple[
    _PdfGenerator,
    _ImageGenerator,
    _PdfGenerator | _ImageGenerator,
    PdfThumbnailRequest | ImagePagePreviewRequest,
]:
    pdf = _PdfGenerator()
    image = _ImageGenerator()
    if source == "pdf":
        return pdf, image, pdf, _pdf_request(10)
    return pdf, image, image, _image_request(11)


def test_mixed_service_preserves_order_and_uses_one_deadline_cancel_and_progress_stream() -> None:
    pdf = _PdfGenerator()
    image = _ImageGenerator()
    operation_id = _uuid(99)
    reported: list[PagePreviewProgress] = []
    requests = (_pdf_request(10), _image_request(11), _pdf_request(12))
    service = PagePreviewService(pdf, image, id_factory=lambda: operation_id)

    results = service.get_previews(requests, progress=reported.append)

    assert tuple(result.request for result in results) == requests
    assert pdf.requests == [_pdf_request(10), _pdf_request(12)]
    assert image.requests == [_image_request(11)]
    assert len({id(item) for item in (*pdf.deadlines, *image.deadlines)}) == 1
    assert len({id(item) for item in (*pdf.cancellations, *image.cancellations)}) == 1
    assert [(item.phase, item.completed_pages) for item in reported] == [
        (PagePreviewProgressPhase.LOOKUP, 0),
        (PagePreviewProgressPhase.GENERATING, 0),
        (PagePreviewProgressPhase.VALIDATING, 0),
        (PagePreviewProgressPhase.LOOKUP, 1),
        (PagePreviewProgressPhase.GENERATING, 1),
        (PagePreviewProgressPhase.VALIDATING, 1),
        (PagePreviewProgressPhase.LOOKUP, 2),
        (PagePreviewProgressPhase.GENERATING, 2),
        (PagePreviewProgressPhase.VALIDATING, 2),
        (PagePreviewProgressPhase.COMPLETE, 3),
    ]
    assert {item.operation_id for item in reported} == {operation_id}
    assert {item.total_pages for item in reported} == {3}


def test_transform_request_uses_the_same_sanitized_batch_boundary() -> None:
    pdf = _PdfGenerator()
    image = _ImageGenerator()
    transform = _TransformGenerator()
    request = _transform_request(13)
    reported: list[PagePreviewProgress] = []

    results = PagePreviewService(pdf, image, transform).get_previews(
        (request,),
        progress=reported.append,
    )

    assert results == (_transform_result(request),)
    assert transform.requests == [request]
    assert [item.phase for item in reported] == [
        PagePreviewProgressPhase.LOOKUP,
        PagePreviewProgressPhase.GENERATING,
        PagePreviewProgressPhase.VALIDATING,
        PagePreviewProgressPhase.COMPLETE,
    ]
    assert len({item.operation_id for item in reported}) == 1
    assert {item.total_pages for item in reported} == {1}


@pytest.mark.parametrize(
    "requests",
    (
        (),
        [_pdf_request(10)],
        (_pdf_request(10), _pdf_request(10)),
        (_pdf_request(10), replace(_image_request(11), case_id=_uuid(2))),
        (object(),),
    ),
)
def test_invalid_mixed_batch_fails_before_generators_or_clock(requests: object) -> None:
    pdf = _PdfGenerator()
    image = _ImageGenerator()
    clock = _Sequence(KeyboardInterrupt("private clock"))
    service = PagePreviewService(pdf, image, monotonic=cast(Callable[[], float], clock))

    with pytest.raises(PagePreviewError) as raised:
        service.get_previews(cast(tuple[PdfThumbnailRequest, ...], requests))

    assert raised.value.code is PagePreviewErrorCode.MODEL_INVALID
    assert pdf.requests == []
    assert image.requests == []


def test_cancellation_after_a_page_returns_no_partial_mixed_batch() -> None:
    pdf = _PdfGenerator()
    image = _ImageGenerator()
    cancelled = False
    ordinary_factory = pdf.result_factory

    def result_then_cancel(request: PdfThumbnailRequest) -> object:
        nonlocal cancelled
        result = ordinary_factory(request)
        cancelled = True
        return result

    pdf.result_factory = result_then_cancel
    service = PagePreviewService(pdf, image)

    with pytest.raises(PagePreviewError) as raised:
        service.get_previews(
            (_pdf_request(10), _image_request(11)),
            cancellation=lambda: cancelled,
        )

    assert raised.value.code is PagePreviewErrorCode.CANCELLED
    assert pdf.requests == [_pdf_request(10)]
    assert image.requests == []


def test_shared_deadline_expires_before_any_generator_work() -> None:
    pdf = _PdfGenerator()
    image = _ImageGenerator()
    clock = _Sequence(0.0, 1.0)
    service = PagePreviewService(pdf, image, monotonic=cast(Callable[[], float], clock))

    with pytest.raises(PagePreviewError) as raised:
        service.get_previews((_image_request(11),), timeout_seconds=1.0)

    assert raised.value.code is PagePreviewErrorCode.TIMED_OUT
    assert image.requests == []


@pytest.mark.parametrize("source", ("pdf", "image"))
def test_generator_cancellation_deadline_is_deferred_to_app_boundary(source: str) -> None:
    pdf, image, selected, request = _selected_generator(source)
    clock = _MutableClock()
    selected.poll_cancellation = True
    selected.before_cancellation = lambda: setattr(clock, "value", 2.0)
    service = PagePreviewService(pdf, image, monotonic=clock)

    with pytest.raises(PagePreviewError) as raised:
        service.get_previews((request,), timeout_seconds=1.0)

    assert raised.value.code is PagePreviewErrorCode.TIMED_OUT
    assert selected.cancellation_results == [True]
    assert selected.completed


@pytest.mark.parametrize("source", ("pdf", "image"))
def test_generator_deadline_clock_failure_is_deferred_to_app_boundary(source: str) -> None:
    pdf, image, selected, request = _selected_generator(source)
    armed = False

    def arm_failure() -> None:
        nonlocal armed
        armed = True

    def clock() -> float:
        if armed:
            raise RuntimeError("C:/private/clock.txt")
        return 0.0

    selected.poll_deadline = True
    selected.before_deadline = arm_failure
    service = PagePreviewService(pdf, image, monotonic=clock)

    with pytest.raises(PagePreviewError) as raised:
        service.get_previews((request,))

    assert raised.value.code is PagePreviewErrorCode.INTERNAL_ERROR
    assert "private" not in str(raised.value)
    assert selected.deadline_results == [True]
    assert selected.completed


@pytest.mark.parametrize("source", ("pdf", "image"))
def test_generator_cancellation_failure_is_deferred_to_app_boundary(source: str) -> None:
    pdf, image, selected, request = _selected_generator(source)
    armed = False

    def arm_failure() -> None:
        nonlocal armed
        armed = True

    def cancellation() -> bool:
        if armed:
            raise RuntimeError("C:/private/cancellation.txt")
        return False

    selected.poll_cancellation = True
    selected.before_cancellation = arm_failure
    service = PagePreviewService(pdf, image)

    with pytest.raises(PagePreviewError) as raised:
        service.get_previews((request,), cancellation=cancellation)

    assert raised.value.code is PagePreviewErrorCode.INTERNAL_ERROR
    assert "private" not in str(raised.value)
    assert selected.cancellation_results == [True]
    assert selected.completed


@pytest.mark.parametrize("source", ("pdf", "image"))
def test_generator_progress_deadline_is_deferred_to_app_boundary(source: str) -> None:
    pdf, image, selected, request = _selected_generator(source)
    clock = _MutableClock()
    selected.before_progress = lambda: setattr(clock, "value", 2.0)
    service = PagePreviewService(pdf, image, monotonic=clock)

    with pytest.raises(PagePreviewError) as raised:
        service.get_previews((request,), timeout_seconds=1.0)

    assert raised.value.code is PagePreviewErrorCode.TIMED_OUT
    assert selected.completed


@pytest.mark.parametrize("source", ("pdf", "image"))
def test_generator_progress_failure_is_deferred_to_app_boundary(source: str) -> None:
    pdf, image, selected, request = _selected_generator(source)
    progress_calls = 0

    def progress(_value: PagePreviewProgress) -> None:
        nonlocal progress_calls
        progress_calls += 1
        if progress_calls > 1:
            raise RuntimeError("C:/private/progress.txt")

    service = PagePreviewService(pdf, image)

    with pytest.raises(PagePreviewError) as raised:
        service.get_previews((request,), progress=progress)

    assert raised.value.code is PagePreviewErrorCode.INTERNAL_ERROR
    assert "private" not in str(raised.value)
    assert selected.completed


def test_unexpected_generator_details_are_sanitized_and_result_binding_is_exact() -> None:
    pdf = _PdfGenerator()
    image = _ImageGenerator()
    image.error = RuntimeError("C:/private/source.png")
    service = PagePreviewService(pdf, image)

    with pytest.raises(PagePreviewError) as failed:
        service.get_previews((_image_request(11),))
    assert failed.value.code is PagePreviewErrorCode.GENERATOR_FAILED
    assert "private" not in str(failed.value)

    image.error = None
    image.result_factory = lambda request: _image_result(replace(request, page_id=_uuid(12)))
    with pytest.raises(PagePreviewError) as invalid:
        service.get_previews((_image_request(11),))
    assert invalid.value.code is PagePreviewErrorCode.RESULT_INVALID


@pytest.mark.parametrize("source", ("pdf", "image"))
def test_generators_cannot_emit_terminal_batch_progress(source: str) -> None:
    pdf = _PdfGenerator()
    image = _ImageGenerator()
    request = _pdf_request(10) if source == "pdf" else _image_request(11)
    if source == "pdf":
        pdf.phases = (PdfThumbnailProgressPhase.COMPLETE,)
    else:
        image.phases = (ImagePagePreviewProgressPhase.COMPLETE,)
    service = PagePreviewService(pdf, image)

    with pytest.raises(PagePreviewError) as raised:
        service.get_previews((request,))

    assert raised.value.code is PagePreviewErrorCode.RESULT_INVALID

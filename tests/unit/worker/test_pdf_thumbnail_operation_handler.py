"""Focused tests for the generic ``pdf.thumbnail`` operation handler seam."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.domain.pdf_rendering import PdfPageBox, PdfRenderRotation
from paperworkshell.domain.pdf_thumbnails import PdfThumbnailError, PdfThumbnailErrorCode
from paperworkshell.worker.operations import (
    OperationContext,
    PdfThumbnailOperationHandler,
    PdfThumbnailRenderRequest,
)
from paperworkshell.worker.protocol.models import (
    PDF_THUMBNAIL_ADDITIONAL_ROTATION_PARAMETER,
    PDF_THUMBNAIL_CACHE_HANDLE_PARAMETER,
    PDF_THUMBNAIL_CACHE_OBJECT_ID_PARAMETER,
    PDF_THUMBNAIL_CAPABILITY,
    PDF_THUMBNAIL_CONTENT_KIND,
    PDF_THUMBNAIL_MAXIMUM_HEIGHT_PARAMETER,
    PDF_THUMBNAIL_MAXIMUM_WIDTH_PARAMETER,
    PDF_THUMBNAIL_PAGE_BOX_PARAMETER,
    PDF_THUMBNAIL_PAGE_ID_PARAMETER,
    PDF_THUMBNAIL_SOURCE_HANDLE_PARAMETER,
    PDF_THUMBNAIL_SOURCE_PAGE_INDEX_PARAMETER,
    CaseObjectReference,
    OperationOutcome,
    OperationProgressPayload,
    ProgressStage,
    ProgressUnit,
    ReferenceArea,
    ResourceLimits,
    ScalarValueType,
    TaggedScalar,
)


def _uuid(value: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{value:012x}")


def _parameters() -> tuple[TaggedScalar, ...]:
    return (
        TaggedScalar(
            PDF_THUMBNAIL_MAXIMUM_HEIGHT_PARAMETER,
            ScalarValueType.INTEGER,
            240,
        ),
        TaggedScalar(PDF_THUMBNAIL_PAGE_ID_PARAMETER, ScalarValueType.UUID, _uuid(5)),
        TaggedScalar(
            PDF_THUMBNAIL_SOURCE_HANDLE_PARAMETER,
            ScalarValueType.INTEGER,
            501,
        ),
        TaggedScalar(
            PDF_THUMBNAIL_ADDITIONAL_ROTATION_PARAMETER,
            ScalarValueType.INTEGER,
            270,
        ),
        TaggedScalar(
            PDF_THUMBNAIL_CACHE_OBJECT_ID_PARAMETER,
            ScalarValueType.UUID,
            _uuid(4),
        ),
        TaggedScalar(
            PDF_THUMBNAIL_SOURCE_PAGE_INDEX_PARAMETER,
            ScalarValueType.INTEGER,
            7,
        ),
        TaggedScalar(
            PDF_THUMBNAIL_PAGE_BOX_PARAMETER,
            ScalarValueType.TOKEN,
            "media",
        ),
        TaggedScalar(
            PDF_THUMBNAIL_CACHE_HANDLE_PARAMETER,
            ScalarValueType.INTEGER,
            503,
        ),
        TaggedScalar(
            PDF_THUMBNAIL_MAXIMUM_WIDTH_PARAMETER,
            ScalarValueType.INTEGER,
            320,
        ),
    )


def _source_reference() -> CaseObjectReference:
    return CaseObjectReference(
        area=ReferenceArea.SOURCE_READ,
        object_id=_uuid(2),
        operation_id=None,
        content_kind="pdf",
        expected_byte_size=128,
        expected_sha256="a" * 64,
    )


def _context(
    *,
    cancellation: Event | None = None,
    deadline: Event | None = None,
    progress: list[OperationProgressPayload] | None = None,
    parameters: tuple[TaggedScalar, ...] | None = None,
    output_bytes: int = 1_024,
) -> OperationContext:
    cancellation_event = Event() if cancellation is None else cancellation
    deadline_event = Event() if deadline is None else deadline
    progress_events = [] if progress is None else progress
    return OperationContext(
        operation_id=_uuid(3),
        case_id=_uuid(1),
        limits=ResourceLimits(
            wall_time_ms=10_000,
            cpu_time_ms=5_000,
            memory_bytes=64 * 1024 * 1024,
            temporary_storage_bytes=16 * 1024 * 1024,
            output_bytes=output_bytes,
            page_count=100,
            pixel_count=1_000_000,
        ),
        cancel_requested=cancellation_event,
        report_progress=progress_events.append,
        deadline_expired=deadline_event.is_set,
        inputs=(_source_reference(),),
        parameters=_parameters() if parameters is None else parameters,
    )


def _staging_reference(
    context: OperationContext,
    request: PdfThumbnailRenderRequest,
    *,
    area: ReferenceArea = ReferenceArea.OPERATION_STAGING,
    object_id: UUID | None = None,
    operation_id: UUID | None = None,
    content_kind: str = PDF_THUMBNAIL_CONTENT_KIND,
    expected_byte_size: int | None = 512,
    expected_sha256: str | None = "b" * 64,
) -> CaseObjectReference:
    return CaseObjectReference(
        area=area,
        object_id=request.cache_object_id if object_id is None else object_id,
        operation_id=context.operation_id if operation_id is None else operation_id,
        content_kind=content_kind,
        expected_byte_size=expected_byte_size,
        expected_sha256=expected_sha256,
    )


class _RecordingAdapter:
    def __init__(
        self,
        *,
        output_factory: (
            Callable[[OperationContext, PdfThumbnailRenderRequest], CaseObjectReference] | None
        ) = None,
        failure: PdfThumbnailError | RuntimeError | None = None,
        after_render: Callable[[OperationContext], None] | None = None,
    ) -> None:
        self.output_factory = _staging_reference if output_factory is None else output_factory
        self.failure = failure
        self.after_render = after_render
        self.discard_calls: list[tuple[int, int]] = []
        self.render_calls: list[tuple[OperationContext, PdfThumbnailRenderRequest]] = []

    def discard_handles(self, source_handle: int, cache_handle: int) -> None:
        self.discard_calls.append((source_handle, cache_handle))

    def render_to_cache(
        self,
        context: OperationContext,
        request: PdfThumbnailRenderRequest,
    ) -> CaseObjectReference:
        self.render_calls.append((context, request))
        if self.after_render is not None:
            self.after_render(context)
        if self.failure is not None:
            raise self.failure
        return self.output_factory(context, request)


def test_handler_parses_typed_request_and_returns_exact_staging_reference() -> None:
    progress: list[OperationProgressPayload] = []
    context = _context(progress=progress)
    adapter = _RecordingAdapter()
    handler = PdfThumbnailOperationHandler(adapter)

    result = handler.execute(context)

    assert handler.descriptor == PDF_THUMBNAIL_CAPABILITY
    assert result.outcome is OperationOutcome.SUCCEEDED
    assert result.error is None
    assert len(result.outputs) == 1
    assert adapter.discard_calls == []
    assert len(adapter.render_calls) == 1
    render_context, request = adapter.render_calls[0]
    assert render_context is context
    assert request == PdfThumbnailRenderRequest(
        source_handle=501,
        cache_handle=503,
        cache_object_id=_uuid(4),
        page_id=_uuid(5),
        source_page_index=7,
        maximum_width_pixels=320,
        maximum_height_pixels=240,
        page_box=PdfPageBox.MEDIA,
        additional_rotation=PdfRenderRotation.DEGREES_270,
    )
    assert result.outputs[0] == _staging_reference(context, request)
    assert progress == [
        OperationProgressPayload(ProgressStage.PROCESSING, 0, 1, ProgressUnit.PAGES),
        OperationProgressPayload(ProgressStage.PROCESSING, 1, 1, ProgressUnit.PAGES),
    ]


@pytest.mark.parametrize(
    ("cancelled", "deadline_expired", "expected_outcome"),
    (
        (True, False, OperationOutcome.CANCELLED),
        (False, True, OperationOutcome.TIMED_OUT),
        (True, True, OperationOutcome.CANCELLED),
    ),
)
def test_handler_pre_dispatch_terminal_state_discards_both_handles(
    *,
    cancelled: bool,
    deadline_expired: bool,
    expected_outcome: OperationOutcome,
) -> None:
    cancellation = Event()
    deadline = Event()
    progress: list[OperationProgressPayload] = []
    if cancelled:
        cancellation.set()
    if deadline_expired:
        deadline.set()
    adapter = _RecordingAdapter()

    result = PdfThumbnailOperationHandler(adapter).execute(
        _context(cancellation=cancellation, deadline=deadline, progress=progress)
    )

    assert result.outcome is expected_outcome
    assert result.outputs == ()
    assert result.error is None or expected_outcome is OperationOutcome.TIMED_OUT
    assert adapter.discard_calls == [(501, 503)]
    assert adapter.render_calls == []
    assert progress == [
        OperationProgressPayload(ProgressStage.PROCESSING, 0, 1, ProgressUnit.PAGES)
    ]


@pytest.mark.parametrize(
    ("error_code", "expected_outcome"),
    (
        (PdfThumbnailErrorCode.CANCELLED, OperationOutcome.CANCELLED),
        (PdfThumbnailErrorCode.TIMED_OUT, OperationOutcome.TIMED_OUT),
    ),
)
def test_handler_maps_closed_terminal_adapter_failures_without_outputs(
    error_code: PdfThumbnailErrorCode,
    expected_outcome: OperationOutcome,
) -> None:
    failure = PdfThumbnailError(error_code, "thumbnail.synthetic")
    adapter = _RecordingAdapter(failure=failure)

    result = PdfThumbnailOperationHandler(adapter).execute(_context())

    assert result.outcome is expected_outcome
    assert result.outputs == ()
    assert len(adapter.render_calls) == 1
    assert adapter.discard_calls == []


def test_handler_propagates_nonterminal_adapter_failure_for_runtime_sanitization() -> None:
    failure = PdfThumbnailError(PdfThumbnailErrorCode.SOURCE_INVALID, "thumbnail.synthetic")
    adapter = _RecordingAdapter(failure=failure)

    with pytest.raises(PdfThumbnailError) as raised:
        PdfThumbnailOperationHandler(adapter).execute(_context())

    assert raised.value is failure
    assert len(adapter.render_calls) == 1
    assert adapter.discard_calls == []


@pytest.mark.parametrize("late_state", ("cancelled", "timed_out"))
def test_handler_suppresses_output_after_late_terminal_state(late_state: str) -> None:
    cancellation = Event()
    deadline = Event()
    progress: list[OperationProgressPayload] = []

    def set_late_state(_context: OperationContext) -> None:
        if late_state == "cancelled":
            cancellation.set()
        else:
            deadline.set()

    adapter = _RecordingAdapter(after_render=set_late_state)

    result = PdfThumbnailOperationHandler(adapter).execute(
        _context(cancellation=cancellation, deadline=deadline, progress=progress)
    )

    assert result.outcome is (
        OperationOutcome.CANCELLED if late_state == "cancelled" else OperationOutcome.TIMED_OUT
    )
    assert result.outputs == ()
    assert len(adapter.render_calls) == 1
    assert adapter.discard_calls == []
    assert progress == [
        OperationProgressPayload(ProgressStage.PROCESSING, 0, 1, ProgressUnit.PAGES)
    ]


def _wrong_area(
    context: OperationContext,
    request: PdfThumbnailRenderRequest,
) -> CaseObjectReference:
    return _staging_reference(context, request, area=ReferenceArea.OPERATION_TEMP)


def _wrong_object(
    context: OperationContext,
    request: PdfThumbnailRenderRequest,
) -> CaseObjectReference:
    return _staging_reference(context, request, object_id=_uuid(20))


def _wrong_operation(
    context: OperationContext,
    request: PdfThumbnailRenderRequest,
) -> CaseObjectReference:
    return _staging_reference(context, request, operation_id=_uuid(21))


def _wrong_content(
    context: OperationContext,
    request: PdfThumbnailRenderRequest,
) -> CaseObjectReference:
    return _staging_reference(context, request, content_kind="pdf")


def _missing_size(
    context: OperationContext,
    request: PdfThumbnailRenderRequest,
) -> CaseObjectReference:
    return _staging_reference(context, request, expected_byte_size=None)


def _zero_size(
    context: OperationContext,
    request: PdfThumbnailRenderRequest,
) -> CaseObjectReference:
    return _staging_reference(context, request, expected_byte_size=0)


def _oversized(
    context: OperationContext,
    request: PdfThumbnailRenderRequest,
) -> CaseObjectReference:
    return _staging_reference(context, request, expected_byte_size=1_025)


def _missing_digest(
    context: OperationContext,
    request: PdfThumbnailRenderRequest,
) -> CaseObjectReference:
    return _staging_reference(context, request, expected_sha256=None)


@pytest.mark.parametrize(
    "output_factory",
    (
        _wrong_area,
        _wrong_object,
        _wrong_operation,
        _wrong_content,
        _missing_size,
        _zero_size,
        _oversized,
        _missing_digest,
    ),
)
def test_handler_rejects_unbound_or_incomplete_adapter_output(
    output_factory: Callable[[OperationContext, PdfThumbnailRenderRequest], CaseObjectReference],
) -> None:
    progress: list[OperationProgressPayload] = []
    adapter = _RecordingAdapter(output_factory=output_factory)

    with pytest.raises(ValueError, match="invalid staging reference"):
        PdfThumbnailOperationHandler(adapter).execute(_context(progress=progress))

    assert len(adapter.render_calls) == 1
    assert adapter.discard_calls == []
    assert progress == [
        OperationProgressPayload(ProgressStage.PROCESSING, 0, 1, ProgressUnit.PAGES)
    ]


def test_handler_rejects_nonreference_adapter_output() -> None:
    def nonreference(
        _context: OperationContext,
        _request: PdfThumbnailRenderRequest,
    ) -> CaseObjectReference:
        return cast(CaseObjectReference, object())

    with pytest.raises(ValueError, match="invalid staging reference"):
        PdfThumbnailOperationHandler(_RecordingAdapter(output_factory=nonreference)).execute(
            _context()
        )


@pytest.mark.parametrize(
    ("parameter_name", "value_type", "value"),
    (
        (PDF_THUMBNAIL_PAGE_BOX_PARAMETER, ScalarValueType.TOKEN, "bleed"),
        (PDF_THUMBNAIL_ADDITIONAL_ROTATION_PARAMETER, ScalarValueType.INTEGER, 45),
    ),
)
def test_handler_fails_closed_when_direct_context_bypasses_protocol_validation(
    parameter_name: str,
    value_type: ScalarValueType,
    value: int | str,
) -> None:
    parameters = tuple(
        TaggedScalar(parameter.name, value_type, value)
        if parameter.name == parameter_name
        else parameter
        for parameter in _parameters()
    )
    adapter = _RecordingAdapter()

    with pytest.raises(ValueError, match="validated thumbnail"):
        PdfThumbnailOperationHandler(adapter).execute(_context(parameters=parameters))

    assert adapter.render_calls == []
    assert adapter.discard_calls == []

"""Focused tests for the searchable-export operation-handler seam."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.worker.operations import (
    OperationContext,
    RuntimeOperationRegistry,
    SearchablePdfExportOperationHandler,
    SearchablePdfExportOperationRequest,
)
from paperworkshell.worker.protocol.models import (
    SEARCHABLE_PDF_EXPORT_CAPABILITY,
    SEARCHABLE_PDF_EXPORT_OUTPUT_CONTENT_KIND,
    SEARCHABLE_PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
    SEARCHABLE_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
    SEARCHABLE_PDF_EXPORT_PLAN_CONTENT_KIND,
    SEARCHABLE_PDF_EXPORT_PLAN_HANDLE_PARAMETER,
    SEARCHABLE_PDF_EXPORT_RESULT_HANDLE_PARAMETER,
    SEARCHABLE_PDF_EXPORT_STATUS_CAPACITY_PARAMETER,
    SEARCHABLE_PDF_EXPORT_STATUS_CONTENT_KIND,
    SEARCHABLE_PDF_EXPORT_STATUS_HANDLE_PARAMETER,
    SEARCHABLE_PDF_EXPORT_STATUS_OBJECT_ID_PARAMETER,
    CaseObjectReference,
    OperationOutcome,
    OperationProgressPayload,
    ReferenceArea,
    ResourceLimits,
    ScalarValueType,
    TaggedScalar,
)


def _uuid(value: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{value:012x}")


def _parameters() -> tuple[TaggedScalar, ...]:
    return (
        TaggedScalar(SEARCHABLE_PDF_EXPORT_RESULT_HANDLE_PARAMETER, ScalarValueType.INTEGER, 505),
        TaggedScalar(
            SEARCHABLE_PDF_EXPORT_STATUS_OBJECT_ID_PARAMETER,
            ScalarValueType.UUID,
            _uuid(9),
        ),
        TaggedScalar(SEARCHABLE_PDF_EXPORT_PLAN_HANDLE_PARAMETER, ScalarValueType.INTEGER, 501),
        TaggedScalar(
            SEARCHABLE_PDF_EXPORT_STATUS_CAPACITY_PARAMETER,
            ScalarValueType.INTEGER,
            1_024,
        ),
        TaggedScalar(
            SEARCHABLE_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
            ScalarValueType.UUID,
            _uuid(8),
        ),
        TaggedScalar(SEARCHABLE_PDF_EXPORT_OUTPUT_HANDLE_PARAMETER, ScalarValueType.INTEGER, 503),
        TaggedScalar(SEARCHABLE_PDF_EXPORT_STATUS_HANDLE_PARAMETER, ScalarValueType.INTEGER, 504),
    )


def _inputs(operation_id: UUID) -> tuple[CaseObjectReference, ...]:
    return (
        CaseObjectReference(
            area=ReferenceArea.SOURCE_READ,
            object_id=_uuid(4),
            operation_id=None,
            content_kind=SEARCHABLE_PDF_EXPORT_PLAN_CONTENT_KIND,
            expected_byte_size=512,
            expected_sha256="a" * 64,
        ),
        CaseObjectReference(
            area=ReferenceArea.SOURCE_READ,
            object_id=_uuid(5),
            operation_id=None,
            content_kind="pdf",
            expected_byte_size=1_024,
            expected_sha256="b" * 64,
        ),
        CaseObjectReference(
            area=ReferenceArea.SOURCE_READ,
            object_id=_uuid(6),
            operation_id=None,
            content_kind="png",
            expected_byte_size=256,
            expected_sha256="c" * 64,
        ),
    )


def _context(
    *,
    cancellation: Event | None = None,
    deadline: Event | None = None,
    progress: list[OperationProgressPayload] | None = None,
    parameters: tuple[TaggedScalar, ...] | None = None,
    output_bytes: int = 4_096,
) -> OperationContext:
    operation_id = _uuid(3)
    cancellation_event = Event() if cancellation is None else cancellation
    deadline_event = Event() if deadline is None else deadline
    progress_events = [] if progress is None else progress
    return OperationContext(
        operation_id=operation_id,
        case_id=_uuid(1),
        limits=ResourceLimits(
            wall_time_ms=10_000,
            cpu_time_ms=5_000,
            memory_bytes=64 * 1024 * 1024,
            temporary_storage_bytes=16 * 1024 * 1024,
            output_bytes=output_bytes,
            page_count=2,
            pixel_count=2_000_000,
        ),
        cancel_requested=cancellation_event,
        report_progress=progress_events.append,
        deadline_expired=deadline_event.is_set,
        inputs=_inputs(operation_id),
        parameters=_parameters() if parameters is None else parameters,
    )


def _pdf_reference(
    context: OperationContext,
    request: SearchablePdfExportOperationRequest,
    *,
    area: ReferenceArea = ReferenceArea.OPERATION_STAGING,
    object_id: UUID | None = None,
    operation_id: UUID | None = None,
    content_kind: str = SEARCHABLE_PDF_EXPORT_OUTPUT_CONTENT_KIND,
    expected_byte_size: int | None = 2_048,
    expected_sha256: str | None = "d" * 64,
) -> CaseObjectReference:
    return CaseObjectReference(
        area=area,
        object_id=request.output_object_id if object_id is None else object_id,
        operation_id=context.operation_id if operation_id is None else operation_id,
        content_kind=content_kind,
        expected_byte_size=expected_byte_size,
        expected_sha256=expected_sha256,
    )


def _status_reference(
    context: OperationContext,
    request: SearchablePdfExportOperationRequest,
    *,
    area: ReferenceArea = ReferenceArea.OPERATION_STAGING,
    object_id: UUID | None = None,
    operation_id: UUID | None = None,
    content_kind: str = SEARCHABLE_PDF_EXPORT_STATUS_CONTENT_KIND,
    expected_byte_size: int | None = 512,
    expected_sha256: str | None = "e" * 64,
) -> CaseObjectReference:
    return CaseObjectReference(
        area=area,
        object_id=request.status_object_id if object_id is None else object_id,
        operation_id=context.operation_id if operation_id is None else operation_id,
        content_kind=content_kind,
        expected_byte_size=expected_byte_size,
        expected_sha256=expected_sha256,
    )


def _staging_references(
    context: OperationContext,
    request: SearchablePdfExportOperationRequest,
) -> tuple[CaseObjectReference, CaseObjectReference]:
    return _pdf_reference(context, request), _status_reference(context, request)


class _RecordingAdapter:
    def __init__(
        self,
        *,
        output_factory: (
            Callable[
                [OperationContext, SearchablePdfExportOperationRequest],
                tuple[CaseObjectReference, CaseObjectReference],
            ]
            | None
        ) = None,
        failure: PdfExportError | RuntimeError | None = None,
        after_export: Callable[[OperationContext], None] | None = None,
    ) -> None:
        self.output_factory = _staging_references if output_factory is None else output_factory
        self.failure = failure
        self.after_export = after_export
        self.discard_calls: list[tuple[OperationContext, SearchablePdfExportOperationRequest]] = []
        self.export_calls: list[tuple[OperationContext, SearchablePdfExportOperationRequest]] = []

    def discard_handles(
        self,
        context: OperationContext,
        request: SearchablePdfExportOperationRequest,
    ) -> None:
        self.discard_calls.append((context, request))

    def export(
        self,
        context: OperationContext,
        request: SearchablePdfExportOperationRequest,
    ) -> tuple[CaseObjectReference, CaseObjectReference]:
        self.export_calls.append((context, request))
        if self.after_export is not None:
            self.after_export(context)
        if self.failure is not None:
            raise self.failure
        return self.output_factory(context, request)


def test_handler_parses_redacted_request_and_returns_both_staging_outputs() -> None:
    progress: list[OperationProgressPayload] = []
    context = _context(progress=progress)
    adapter = _RecordingAdapter()
    handler = SearchablePdfExportOperationHandler(adapter)

    result = handler.execute(context)

    request = SearchablePdfExportOperationRequest(
        501,
        503,
        504,
        505,
        _uuid(8),
        _uuid(9),
        1_024,
    )
    assert handler.descriptor == SEARCHABLE_PDF_EXPORT_CAPABILITY
    assert RuntimeOperationRegistry((handler,)).handler_for(handler.descriptor) is handler
    assert result.outcome is OperationOutcome.SUCCEEDED
    assert result.error is None
    assert result.outputs == _staging_references(context, request)
    assert adapter.discard_calls == []
    assert adapter.export_calls == [(context, request)]
    assert all(value not in repr(request) for value in ("501", "503", "504", "505"))
    # Only the production adapter can authenticate the opaque plan's exact
    # page count; the generic handler must not synthesize a resource-ceiling total.
    assert progress == []


@pytest.mark.parametrize(
    ("cancelled", "deadline_expired", "expected_outcome"),
    (
        (True, False, OperationOutcome.CANCELLED),
        (False, True, OperationOutcome.TIMED_OUT),
        (True, True, OperationOutcome.CANCELLED),
    ),
)
def test_handler_predispatch_terminal_state_discards_all_transfer_handles(
    *,
    cancelled: bool,
    deadline_expired: bool,
    expected_outcome: OperationOutcome,
) -> None:
    cancellation = Event()
    deadline = Event()
    if cancelled:
        cancellation.set()
    if deadline_expired:
        deadline.set()
    adapter = _RecordingAdapter()
    context = _context(cancellation=cancellation, deadline=deadline)

    result = SearchablePdfExportOperationHandler(adapter).execute(context)

    assert result.outcome is expected_outcome
    assert result.outputs == ()
    assert adapter.export_calls == []
    assert len(adapter.discard_calls) == 1


@pytest.mark.parametrize(
    ("error_code", "expected_outcome"),
    (
        (PdfExportErrorCode.CANCELLED, OperationOutcome.CANCELLED),
        (PdfExportErrorCode.TIMED_OUT, OperationOutcome.TIMED_OUT),
    ),
)
def test_handler_maps_terminal_adapter_failures_without_outputs(
    error_code: PdfExportErrorCode,
    expected_outcome: OperationOutcome,
) -> None:
    adapter = _RecordingAdapter(failure=PdfExportError(error_code, "export.synthetic"))

    result = SearchablePdfExportOperationHandler(adapter).execute(_context())

    assert result.outcome is expected_outcome
    assert result.outputs == ()
    assert len(adapter.export_calls) == 1


def test_handler_propagates_nonterminal_failure_for_runtime_sanitization() -> None:
    failure = PdfExportError(PdfExportErrorCode.VERIFICATION_RENDER_FAILED, "export.synthetic")
    adapter = _RecordingAdapter(failure=failure)

    with pytest.raises(PdfExportError) as raised:
        SearchablePdfExportOperationHandler(adapter).execute(_context())

    assert raised.value is failure


@pytest.mark.parametrize("late_state", ("cancelled", "timed_out"))
def test_handler_suppresses_both_outputs_after_late_terminal_state(late_state: str) -> None:
    cancellation = Event()
    deadline = Event()

    def set_late_state(_context: OperationContext) -> None:
        (cancellation if late_state == "cancelled" else deadline).set()

    adapter = _RecordingAdapter(after_export=set_late_state)
    result = SearchablePdfExportOperationHandler(adapter).execute(
        _context(cancellation=cancellation, deadline=deadline)
    )

    assert result.outcome is (
        OperationOutcome.CANCELLED if late_state == "cancelled" else OperationOutcome.TIMED_OUT
    )
    assert result.outputs == ()
    assert len(adapter.export_calls) == 1


def _reversed_outputs(
    context: OperationContext,
    request: SearchablePdfExportOperationRequest,
) -> tuple[CaseObjectReference, CaseObjectReference]:
    pdf, status = _staging_references(context, request)
    return status, pdf


def _wrong_pdf_object(
    context: OperationContext,
    request: SearchablePdfExportOperationRequest,
) -> tuple[CaseObjectReference, CaseObjectReference]:
    return _pdf_reference(context, request, object_id=_uuid(20)), _status_reference(
        context, request
    )


def _wrong_status_content(
    context: OperationContext,
    request: SearchablePdfExportOperationRequest,
) -> tuple[CaseObjectReference, CaseObjectReference]:
    return _pdf_reference(context, request), _status_reference(
        context,
        request,
        content_kind="status",
    )


def _oversized_pdf(
    context: OperationContext,
    request: SearchablePdfExportOperationRequest,
) -> tuple[CaseObjectReference, CaseObjectReference]:
    return _pdf_reference(context, request, expected_byte_size=4_097), _status_reference(
        context, request
    )


def _oversized_status(
    context: OperationContext,
    request: SearchablePdfExportOperationRequest,
) -> tuple[CaseObjectReference, CaseObjectReference]:
    return _pdf_reference(context, request), _status_reference(
        context,
        request,
        expected_byte_size=1_025,
    )


@pytest.mark.parametrize(
    "output_factory",
    (
        _reversed_outputs,
        _wrong_pdf_object,
        _wrong_status_content,
        _oversized_pdf,
        _oversized_status,
    ),
)
def test_handler_rejects_unbound_or_incomplete_adapter_outputs(
    output_factory: Callable[
        [OperationContext, SearchablePdfExportOperationRequest],
        tuple[CaseObjectReference, CaseObjectReference],
    ],
) -> None:
    adapter = _RecordingAdapter(output_factory=output_factory)

    with pytest.raises(ValueError, match="invalid staging references"):
        SearchablePdfExportOperationHandler(adapter).execute(_context())

    assert len(adapter.export_calls) == 1


def test_handler_rejects_non_tuple_adapter_output() -> None:
    def non_tuple(
        _context: OperationContext,
        _request: SearchablePdfExportOperationRequest,
    ) -> tuple[CaseObjectReference, CaseObjectReference]:
        return cast(tuple[CaseObjectReference, CaseObjectReference], object())

    with pytest.raises(ValueError, match="invalid staging references"):
        SearchablePdfExportOperationHandler(_RecordingAdapter(output_factory=non_tuple)).execute(
            _context()
        )


def test_handler_rejects_missing_or_mistyped_parameters_before_dispatch() -> None:
    adapter = _RecordingAdapter()
    bad_parameters = _parameters()[:-1] + (
        TaggedScalar(
            SEARCHABLE_PDF_EXPORT_STATUS_HANDLE_PARAMETER,
            ScalarValueType.TOKEN,
            "invalid",
        ),
    )

    with pytest.raises(ValueError, match="validated integer parameter"):
        SearchablePdfExportOperationHandler(adapter).execute(_context(parameters=bad_parameters))

    assert adapter.discard_calls == []
    assert adapter.export_calls == []

"""Focused tests for the generic ``pdf.export`` operation-handler seam."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.domain.pdf_export import PdfExportError, PdfExportErrorCode
from paperworkshell.worker.operations import (
    OperationContext,
    PdfExportOperationHandler,
    PdfExportOperationRequest,
)
from paperworkshell.worker.protocol.models import (
    PDF_EXPORT_CAPABILITY,
    PDF_EXPORT_OUTPUT_CONTENT_KIND,
    PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
    PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
    PDF_EXPORT_PLAN_CONTENT_KIND,
    PDF_EXPORT_PLAN_HANDLE_PARAMETER,
    PDF_EXPORT_RESULT_HANDLE_PARAMETER,
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
        TaggedScalar(PDF_EXPORT_RESULT_HANDLE_PARAMETER, ScalarValueType.INTEGER, 505),
        TaggedScalar(PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER, ScalarValueType.UUID, _uuid(8)),
        TaggedScalar(PDF_EXPORT_PLAN_HANDLE_PARAMETER, ScalarValueType.INTEGER, 501),
        TaggedScalar(PDF_EXPORT_OUTPUT_HANDLE_PARAMETER, ScalarValueType.INTEGER, 503),
    )


def _inputs(operation_id: UUID) -> tuple[CaseObjectReference, ...]:
    return (
        CaseObjectReference(
            area=ReferenceArea.OPERATION_TEMP,
            object_id=_uuid(4),
            operation_id=operation_id,
            content_kind=PDF_EXPORT_PLAN_CONTENT_KIND,
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


def _staging_reference(
    context: OperationContext,
    request: PdfExportOperationRequest,
    *,
    area: ReferenceArea = ReferenceArea.OPERATION_STAGING,
    object_id: UUID | None = None,
    operation_id: UUID | None = None,
    content_kind: str = PDF_EXPORT_OUTPUT_CONTENT_KIND,
    expected_byte_size: int | None = 2_048,
    expected_sha256: str | None = "c" * 64,
) -> CaseObjectReference:
    return CaseObjectReference(
        area=area,
        object_id=request.output_object_id if object_id is None else object_id,
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
            Callable[[OperationContext, PdfExportOperationRequest], CaseObjectReference] | None
        ) = None,
        failure: PdfExportError | RuntimeError | None = None,
        after_export: Callable[[OperationContext], None] | None = None,
    ) -> None:
        self.output_factory = _staging_reference if output_factory is None else output_factory
        self.failure = failure
        self.after_export = after_export
        self.discard_calls: list[tuple[OperationContext, PdfExportOperationRequest]] = []
        self.export_calls: list[tuple[OperationContext, PdfExportOperationRequest]] = []

    def discard_handles(
        self,
        context: OperationContext,
        request: PdfExportOperationRequest,
    ) -> None:
        self.discard_calls.append((context, request))

    def export(
        self,
        context: OperationContext,
        request: PdfExportOperationRequest,
    ) -> CaseObjectReference:
        self.export_calls.append((context, request))
        if self.after_export is not None:
            self.after_export(context)
        if self.failure is not None:
            raise self.failure
        return self.output_factory(context, request)


def test_handler_parses_redacted_request_and_returns_exact_staging_reference() -> None:
    progress: list[OperationProgressPayload] = []
    context = _context(progress=progress)
    adapter = _RecordingAdapter()
    handler = PdfExportOperationHandler(adapter)

    result = handler.execute(context)

    assert handler.descriptor == PDF_EXPORT_CAPABILITY
    assert result.outcome is OperationOutcome.SUCCEEDED
    assert result.error is None
    assert len(result.outputs) == 1
    assert adapter.discard_calls == []
    assert len(adapter.export_calls) == 1
    export_context, request = adapter.export_calls[0]
    assert export_context is context
    assert request == PdfExportOperationRequest(501, 503, 505, _uuid(8))
    assert all(value not in repr(request) for value in ("501", "503", "505"))
    assert result.outputs[0] == _staging_reference(context, request)
    assert progress == [
        OperationProgressPayload(ProgressStage.PROCESSING, 0, 2, ProgressUnit.PAGES),
        OperationProgressPayload(ProgressStage.PROCESSING, 2, 2, ProgressUnit.PAGES),
    ]


@pytest.mark.parametrize(
    ("cancelled", "deadline_expired", "expected_outcome"),
    (
        (True, False, OperationOutcome.CANCELLED),
        (False, True, OperationOutcome.TIMED_OUT),
        (True, True, OperationOutcome.CANCELLED),
    ),
)
def test_handler_pre_dispatch_terminal_state_discards_transfer_handles(
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
    context = _context(cancellation=cancellation, deadline=deadline, progress=progress)

    result = PdfExportOperationHandler(adapter).execute(context)

    assert result.outcome is expected_outcome
    assert result.outputs == ()
    assert adapter.export_calls == []
    assert len(adapter.discard_calls) == 1
    assert adapter.discard_calls[0][0] is context
    assert adapter.discard_calls[0][1] == PdfExportOperationRequest(501, 503, 505, _uuid(8))
    assert progress == [
        OperationProgressPayload(ProgressStage.PROCESSING, 0, 2, ProgressUnit.PAGES)
    ]


@pytest.mark.parametrize(
    ("error_code", "expected_outcome"),
    (
        (PdfExportErrorCode.CANCELLED, OperationOutcome.CANCELLED),
        (PdfExportErrorCode.TIMED_OUT, OperationOutcome.TIMED_OUT),
    ),
)
def test_handler_maps_closed_terminal_adapter_failures_without_outputs(
    error_code: PdfExportErrorCode,
    expected_outcome: OperationOutcome,
) -> None:
    adapter = _RecordingAdapter(failure=PdfExportError(error_code, "export.synthetic"))

    result = PdfExportOperationHandler(adapter).execute(_context())

    assert result.outcome is expected_outcome
    assert result.outputs == ()
    assert len(adapter.export_calls) == 1
    assert adapter.discard_calls == []


def test_handler_propagates_nonterminal_failure_for_runtime_sanitization() -> None:
    failure = PdfExportError(PdfExportErrorCode.VERIFICATION_RENDER_FAILED, "export.synthetic")
    adapter = _RecordingAdapter(failure=failure)

    with pytest.raises(PdfExportError) as raised:
        PdfExportOperationHandler(adapter).execute(_context())

    assert raised.value is failure
    assert len(adapter.export_calls) == 1
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

    adapter = _RecordingAdapter(after_export=set_late_state)
    result = PdfExportOperationHandler(adapter).execute(
        _context(cancellation=cancellation, deadline=deadline, progress=progress)
    )

    assert result.outcome is (
        OperationOutcome.CANCELLED if late_state == "cancelled" else OperationOutcome.TIMED_OUT
    )
    assert result.outputs == ()
    assert len(adapter.export_calls) == 1
    assert progress == [
        OperationProgressPayload(ProgressStage.PROCESSING, 0, 2, ProgressUnit.PAGES)
    ]


def _wrong_area(
    context: OperationContext,
    request: PdfExportOperationRequest,
) -> CaseObjectReference:
    return _staging_reference(context, request, area=ReferenceArea.OPERATION_TEMP)


def _wrong_object(
    context: OperationContext,
    request: PdfExportOperationRequest,
) -> CaseObjectReference:
    return _staging_reference(context, request, object_id=_uuid(20))


def _wrong_operation(
    context: OperationContext,
    request: PdfExportOperationRequest,
) -> CaseObjectReference:
    return _staging_reference(context, request, operation_id=_uuid(21))


def _wrong_content(
    context: OperationContext,
    request: PdfExportOperationRequest,
) -> CaseObjectReference:
    return _staging_reference(context, request, content_kind="pdf")


def _missing_size(
    context: OperationContext,
    request: PdfExportOperationRequest,
) -> CaseObjectReference:
    return _staging_reference(context, request, expected_byte_size=None)


def _zero_size(
    context: OperationContext,
    request: PdfExportOperationRequest,
) -> CaseObjectReference:
    return _staging_reference(context, request, expected_byte_size=0)


def _oversized(
    context: OperationContext,
    request: PdfExportOperationRequest,
) -> CaseObjectReference:
    return _staging_reference(context, request, expected_byte_size=4_097)


def _missing_digest(
    context: OperationContext,
    request: PdfExportOperationRequest,
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
    output_factory: Callable[[OperationContext, PdfExportOperationRequest], CaseObjectReference],
) -> None:
    progress: list[OperationProgressPayload] = []
    adapter = _RecordingAdapter(output_factory=output_factory)

    with pytest.raises(ValueError, match="invalid staging reference"):
        PdfExportOperationHandler(adapter).execute(_context(progress=progress))

    assert len(adapter.export_calls) == 1
    assert adapter.discard_calls == []
    assert progress == [
        OperationProgressPayload(ProgressStage.PROCESSING, 0, 2, ProgressUnit.PAGES)
    ]


def test_handler_rejects_nonreference_adapter_output() -> None:
    def nonreference(
        _context: OperationContext,
        _request: PdfExportOperationRequest,
    ) -> CaseObjectReference:
        return cast(CaseObjectReference, object())

    with pytest.raises(ValueError, match="invalid staging reference"):
        PdfExportOperationHandler(_RecordingAdapter(output_factory=nonreference)).execute(
            _context()
        )


def test_handler_rejects_missing_or_mistyped_parameters_before_dispatch() -> None:
    adapter = _RecordingAdapter()
    bad_parameters = _parameters()[:-1] + (
        TaggedScalar(PDF_EXPORT_OUTPUT_HANDLE_PARAMETER, ScalarValueType.TOKEN, "invalid"),
    )

    with pytest.raises(ValueError, match="validated integer parameter"):
        PdfExportOperationHandler(adapter).execute(_context(parameters=bad_parameters))

    assert adapter.discard_calls == []
    assert adapter.export_calls == []

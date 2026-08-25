"""Focused tests for the ``pdf.export.flattened`` operation-handler seam."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.overlays.exporting import (
    FlattenedPdfExportError,
    FlattenedPdfExportErrorCode,
)
from paperworkshell.worker.operations import (
    FlattenedPdfExportOperationHandler,
    FlattenedPdfExportOperationRequest,
    OperationContext,
)
from paperworkshell.worker.protocol.models import (
    FLATTENED_PDF_EXPORT_CAPABILITY,
    FLATTENED_PDF_EXPORT_OUTPUT_CONTENT_KIND,
    FLATTENED_PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
    FLATTENED_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
    FLATTENED_PDF_EXPORT_PLAN_CONTENT_KIND,
    FLATTENED_PDF_EXPORT_PLAN_HANDLE_PARAMETER,
    FLATTENED_PDF_EXPORT_RASTER_CONTENT_KIND,
    FLATTENED_PDF_EXPORT_RESULT_HANDLE_PARAMETER,
    CaseObjectReference,
    ErrorCode,
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
    return UUID(f"f3070000-0000-4000-8000-{value:012x}")


def _parameters() -> tuple[TaggedScalar, ...]:
    return (
        TaggedScalar(
            FLATTENED_PDF_EXPORT_RESULT_HANDLE_PARAMETER,
            ScalarValueType.INTEGER,
            505,
        ),
        TaggedScalar(
            FLATTENED_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
            ScalarValueType.UUID,
            _uuid(8),
        ),
        TaggedScalar(
            FLATTENED_PDF_EXPORT_PLAN_HANDLE_PARAMETER,
            ScalarValueType.INTEGER,
            501,
        ),
        TaggedScalar(
            FLATTENED_PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
            ScalarValueType.INTEGER,
            503,
        ),
    )


def _inputs(operation_id: UUID) -> tuple[CaseObjectReference, ...]:
    return (
        CaseObjectReference(
            ReferenceArea.OPERATION_TEMP,
            _uuid(4),
            operation_id,
            FLATTENED_PDF_EXPORT_PLAN_CONTENT_KIND,
            512,
            "a" * 64,
        ),
        CaseObjectReference(
            ReferenceArea.SOURCE_READ,
            _uuid(5),
            None,
            "pdf",
            1_024,
            "b" * 64,
        ),
        CaseObjectReference(
            ReferenceArea.OPERATION_TEMP,
            _uuid(6),
            operation_id,
            FLATTENED_PDF_EXPORT_RASTER_CONTENT_KIND,
            4,
            "c" * 64,
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
    request: FlattenedPdfExportOperationRequest,
    *,
    area: ReferenceArea = ReferenceArea.OPERATION_STAGING,
    object_id: UUID | None = None,
    operation_id: UUID | None = None,
    content_kind: str = FLATTENED_PDF_EXPORT_OUTPUT_CONTENT_KIND,
    expected_byte_size: int | None = 2_048,
    expected_sha256: str | None = "d" * 64,
) -> CaseObjectReference:
    return CaseObjectReference(
        area,
        request.output_object_id if object_id is None else object_id,
        context.operation_id if operation_id is None else operation_id,
        content_kind,
        expected_byte_size,
        expected_sha256,
    )


class _RecordingAdapter:
    def __init__(
        self,
        *,
        output_factory: (
            Callable[
                [OperationContext, FlattenedPdfExportOperationRequest],
                CaseObjectReference,
            ]
            | None
        ) = None,
        failure: FlattenedPdfExportError | None = None,
        after_export: Callable[[OperationContext], None] | None = None,
    ) -> None:
        self.output_factory = _staging_reference if output_factory is None else output_factory
        self.failure = failure
        self.after_export = after_export
        self.discard_calls: list[tuple[OperationContext, FlattenedPdfExportOperationRequest]] = []
        self.export_calls: list[tuple[OperationContext, FlattenedPdfExportOperationRequest]] = []

    def discard_handles(
        self,
        context: OperationContext,
        request: FlattenedPdfExportOperationRequest,
    ) -> None:
        self.discard_calls.append((context, request))

    def export(
        self,
        context: OperationContext,
        request: FlattenedPdfExportOperationRequest,
    ) -> CaseObjectReference:
        self.export_calls.append((context, request))
        if self.after_export is not None:
            self.after_export(context)
        if self.failure is not None:
            raise self.failure
        return self.output_factory(context, request)


def test_handler_returns_bound_output_and_reports_initial_and_terminal_progress() -> None:
    progress: list[OperationProgressPayload] = []
    context = _context(progress=progress)
    adapter = _RecordingAdapter()
    handler = FlattenedPdfExportOperationHandler(adapter)

    result = handler.execute(context)

    assert handler.descriptor == FLATTENED_PDF_EXPORT_CAPABILITY
    assert result.outcome is OperationOutcome.SUCCEEDED
    assert result.error is None
    assert len(result.outputs) == 1
    assert adapter.discard_calls == []
    assert len(adapter.export_calls) == 1
    export_context, request = adapter.export_calls[0]
    assert export_context is context
    assert request == FlattenedPdfExportOperationRequest(501, 503, 505, _uuid(8))
    assert all(value not in repr(request) for value in ("501", "503", "505"))
    assert result.outputs == (_staging_reference(context, request),)
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

    result = FlattenedPdfExportOperationHandler(adapter).execute(context)

    assert result.outcome is expected_outcome
    assert result.outputs == ()
    assert adapter.export_calls == []
    assert adapter.discard_calls == [
        (context, FlattenedPdfExportOperationRequest(501, 503, 505, _uuid(8)))
    ]
    assert progress == [
        OperationProgressPayload(ProgressStage.PROCESSING, 0, 2, ProgressUnit.PAGES)
    ]


@pytest.mark.parametrize(
    ("error_code", "expected_outcome", "expected_public_code"),
    (
        (FlattenedPdfExportErrorCode.CANCELLED, OperationOutcome.CANCELLED, None),
        (
            FlattenedPdfExportErrorCode.TIMED_OUT,
            OperationOutcome.TIMED_OUT,
            ErrorCode.WORKER_OPERATION_TIMED_OUT,
        ),
        (
            FlattenedPdfExportErrorCode.LIMIT_EXCEEDED,
            OperationOutcome.FAILED,
            ErrorCode.WORKER_OPERATION_FAILED,
        ),
    ),
)
def test_handler_maps_terminal_and_resource_adapter_failures(
    error_code: FlattenedPdfExportErrorCode,
    expected_outcome: OperationOutcome,
    expected_public_code: ErrorCode | None,
) -> None:
    progress: list[OperationProgressPayload] = []
    adapter = _RecordingAdapter(failure=FlattenedPdfExportError(error_code, "flattened.synthetic"))

    result = FlattenedPdfExportOperationHandler(adapter).execute(_context(progress=progress))

    assert result.outcome is expected_outcome
    assert result.outputs == ()
    assert (None if result.error is None else result.error.code) is expected_public_code
    assert len(adapter.export_calls) == 1
    assert adapter.discard_calls == []
    assert progress == [
        OperationProgressPayload(ProgressStage.PROCESSING, 0, 2, ProgressUnit.PAGES)
    ]


@pytest.mark.parametrize("late_state", ("cancelled", "timed_out"))
def test_handler_suppresses_output_and_terminal_progress_after_late_state(
    late_state: str,
) -> None:
    cancellation = Event()
    deadline = Event()
    progress: list[OperationProgressPayload] = []

    def set_late_state(_context: OperationContext) -> None:
        (cancellation if late_state == "cancelled" else deadline).set()

    adapter = _RecordingAdapter(after_export=set_late_state)
    result = FlattenedPdfExportOperationHandler(adapter).execute(
        _context(cancellation=cancellation, deadline=deadline, progress=progress)
    )

    assert result.outcome is (
        OperationOutcome.CANCELLED if late_state == "cancelled" else OperationOutcome.TIMED_OUT
    )
    assert result.outputs == ()
    assert len(adapter.export_calls) == 1
    assert adapter.discard_calls == []
    assert progress == [
        OperationProgressPayload(ProgressStage.PROCESSING, 0, 2, ProgressUnit.PAGES)
    ]


def _wrong_object(
    context: OperationContext,
    request: FlattenedPdfExportOperationRequest,
) -> CaseObjectReference:
    return _staging_reference(context, request, object_id=_uuid(20))


def _oversized(
    context: OperationContext,
    request: FlattenedPdfExportOperationRequest,
) -> CaseObjectReference:
    return _staging_reference(context, request, expected_byte_size=4_097)


def _missing_digest(
    context: OperationContext,
    request: FlattenedPdfExportOperationRequest,
) -> CaseObjectReference:
    return _staging_reference(context, request, expected_sha256=None)


@pytest.mark.parametrize("output_factory", (_wrong_object, _oversized, _missing_digest))
def test_handler_rejects_unbound_or_incomplete_adapter_output_without_terminal_progress(
    output_factory: Callable[
        [OperationContext, FlattenedPdfExportOperationRequest], CaseObjectReference
    ],
) -> None:
    progress: list[OperationProgressPayload] = []
    adapter = _RecordingAdapter(output_factory=output_factory)

    with pytest.raises(ValueError, match="invalid staging reference"):
        FlattenedPdfExportOperationHandler(adapter).execute(_context(progress=progress))

    assert len(adapter.export_calls) == 1
    assert adapter.discard_calls == []
    assert progress == [
        OperationProgressPayload(ProgressStage.PROCESSING, 0, 2, ProgressUnit.PAGES)
    ]


def test_handler_rejects_nonreference_output_and_mistyped_parameters_before_dispatch() -> None:
    def nonreference(
        _context: OperationContext,
        _request: FlattenedPdfExportOperationRequest,
    ) -> CaseObjectReference:
        return cast(CaseObjectReference, object())

    with pytest.raises(ValueError, match="invalid staging reference"):
        FlattenedPdfExportOperationHandler(_RecordingAdapter(output_factory=nonreference)).execute(
            _context()
        )

    adapter = _RecordingAdapter()
    bad_parameters = _parameters()[:-1] + (
        TaggedScalar(
            FLATTENED_PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
            ScalarValueType.TOKEN,
            "invalid",
        ),
    )
    with pytest.raises(ValueError, match="validated integer parameter"):
        FlattenedPdfExportOperationHandler(adapter).execute(_context(parameters=bad_parameters))
    assert adapter.discard_calls == []
    assert adapter.export_calls == []

"""Focused protocol and operation-handler tests for ``ocr.process`` version 1."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from threading import Event
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.domain.ocr import OcrError, OcrErrorCode
from paperworkshell.worker.operations import (
    OcrProcessOperationHandler,
    OcrProcessOperationRequest,
    OperationContext,
)
from paperworkshell.worker.protocol.codec import DEFAULT_OPERATION_REGISTRY
from paperworkshell.worker.protocol.errors import ProtocolError
from paperworkshell.worker.protocol.models import (
    MAX_MESSAGE_BYTES,
    OCR_PROCESS_CAPABILITY,
    OCR_PROCESS_LANGUAGE_SET_ID_PARAMETER,
    OCR_PROCESS_OUTPUT_CONTENT_KIND,
    OCR_PROCESS_OUTPUT_HANDLE_PARAMETER,
    OCR_PROCESS_OUTPUT_OBJECT_ID_PARAMETER,
    OCR_PROCESS_PAGE_COUNT_PARAMETER,
    OCR_PROCESS_PROFILE_ID_PARAMETER,
    OCR_PROCESS_RESULT_HANDLE_PARAMETER,
    OCR_PROCESS_SOURCE_HANDLE_PARAMETER,
    CaseObjectReference,
    Direction,
    MessageType,
    OperationAcceptedPayload,
    OperationOutcome,
    OperationProgressPayload,
    OperationResultPayload,
    OperationStartPayload,
    ProgressStage,
    ProgressUnit,
    ReferenceArea,
    ResourceLimits,
    ScalarValueType,
    SessionCapability,
    SessionHelloPayload,
    SessionReadyPayload,
    TaggedScalar,
    validate_ocr_process_result,
)
from paperworkshell.worker.protocol.peer import PeerBinding, ProtocolPeer


def _uuid(value: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{value:012x}")


def _limits(
    *,
    temporary_storage_bytes: int = 8_192,
    output_bytes: int = 4_096,
    page_count: int = 2,
) -> ResourceLimits:
    return ResourceLimits(
        wall_time_ms=10_000,
        cpu_time_ms=5_000,
        memory_bytes=64 * 1024 * 1024,
        temporary_storage_bytes=temporary_storage_bytes,
        output_bytes=output_bytes,
        page_count=page_count,
        pixel_count=2_000_000,
    )


def _source(
    *,
    object_id: UUID | None = None,
    content_kind: str = "pdf",
) -> CaseObjectReference:
    return CaseObjectReference(
        area=ReferenceArea.SOURCE_READ,
        object_id=_uuid(21) if object_id is None else object_id,
        operation_id=None,
        content_kind=content_kind,
        expected_byte_size=1_024,
        expected_sha256="a" * 64,
    )


def _parameters(*, page_count: int = 2) -> tuple[TaggedScalar, ...]:
    return (
        TaggedScalar(OCR_PROCESS_SOURCE_HANDLE_PARAMETER, ScalarValueType.INTEGER, 501),
        TaggedScalar(OCR_PROCESS_OUTPUT_HANDLE_PARAMETER, ScalarValueType.INTEGER, 503),
        TaggedScalar(OCR_PROCESS_RESULT_HANDLE_PARAMETER, ScalarValueType.INTEGER, 505),
        TaggedScalar(OCR_PROCESS_OUTPUT_OBJECT_ID_PARAMETER, ScalarValueType.UUID, _uuid(22)),
        TaggedScalar(OCR_PROCESS_PAGE_COUNT_PARAMETER, ScalarValueType.INTEGER, page_count),
        TaggedScalar(
            OCR_PROCESS_LANGUAGE_SET_ID_PARAMETER,
            ScalarValueType.TOKEN,
            "latin-default-v1",
        ),
        TaggedScalar(
            OCR_PROCESS_PROFILE_ID_PARAMETER,
            ScalarValueType.TOKEN,
            "searchable-pdf-v1",
        ),
    )


def _replace_parameter(replacement: TaggedScalar) -> tuple[TaggedScalar, ...]:
    return tuple(
        replacement if parameter.name == replacement.name else parameter
        for parameter in _parameters()
    )


def _payload(
    *,
    source: CaseObjectReference | None = None,
    parameters: tuple[TaggedScalar, ...] | None = None,
    limits: ResourceLimits | None = None,
) -> OperationStartPayload:
    return OperationStartPayload(
        operation_kind=OCR_PROCESS_CAPABILITY.capability_id,
        operation_version=OCR_PROCESS_CAPABILITY.capability_version,
        inputs=(_source() if source is None else source,),
        parameters=_parameters() if parameters is None else parameters,
        requested_limits=_limits() if limits is None else limits,
    )


def _output(
    *,
    object_id: UUID | None = None,
    operation_id: UUID | None = None,
    area: ReferenceArea = ReferenceArea.OPERATION_STAGING,
    content_kind: str = OCR_PROCESS_OUTPUT_CONTENT_KIND,
    byte_size: int | None = 2_048,
    digest: str | None = "b" * 64,
) -> CaseObjectReference:
    return CaseObjectReference(
        area=area,
        object_id=_uuid(22) if object_id is None else object_id,
        operation_id=_uuid(31) if operation_id is None else operation_id,
        content_kind=content_kind,
        expected_byte_size=byte_size,
        expected_sha256=digest,
    )


def _negotiated_peers() -> tuple[ProtocolPeer, ProtocolPeer]:
    binding = PeerBinding(_uuid(1), _uuid(2), _uuid(3), SessionCapability("d" * 64))
    client = ProtocolPeer(binding, Direction.CLIENT_TO_WORKER)
    worker = ProtocolPeer(binding, Direction.WORKER_TO_CLIENT)
    hello = client.prepare_outbound(
        MessageType.SESSION_HELLO,
        SessionHelloPayload((1,), (OCR_PROCESS_CAPABILITY,)),
        request_id=_uuid(10),
        operation_id=None,
    )
    accepted_hello = worker.accept_inbound(hello.encoded)
    ready = worker.prepare_outbound(
        MessageType.SESSION_READY,
        SessionReadyPayload(1, (OCR_PROCESS_CAPABILITY,), MAX_MESSAGE_BYTES),
        request_id=accepted_hello.request_id,
        operation_id=None,
    )
    client.accept_inbound(ready.encoded)
    return client, worker


def _started_peers() -> tuple[ProtocolPeer, ProtocolPeer, UUID, UUID]:
    client, worker = _negotiated_peers()
    request_id = _uuid(30)
    operation_id = _uuid(31)
    start = client.prepare_outbound(
        MessageType.OPERATION_START,
        _payload(),
        request_id=request_id,
        operation_id=operation_id,
    )
    worker.accept_inbound(start.encoded)
    accepted = worker.prepare_outbound(
        MessageType.OPERATION_ACCEPTED,
        OperationAcceptedPayload(
            OCR_PROCESS_CAPABILITY.capability_id,
            OCR_PROCESS_CAPABILITY.capability_version,
            _limits(),
        ),
        request_id=request_id,
        operation_id=operation_id,
    )
    client.accept_inbound(accepted.encoded)
    return client, worker, request_id, operation_id


def test_ocr_process_is_registered_and_round_trips_its_path_free_contract() -> None:
    client, worker = _negotiated_peers()
    payload = _payload()

    start = client.prepare_outbound(
        MessageType.OPERATION_START,
        payload,
        request_id=_uuid(30),
        operation_id=_uuid(31),
    )

    assert worker.accept_inbound(start.encoded).payload == payload
    assert OCR_PROCESS_CAPABILITY in DEFAULT_OPERATION_REGISTRY.capabilities
    representation = repr(payload)
    assert all(value not in representation for value in ("501", "503", "505"))


@pytest.mark.parametrize("content_kind", ("pdf", "jpeg", "png"))
def test_ocr_process_accepts_supported_source_content_kinds(content_kind: str) -> None:
    page_count = 2 if content_kind == "pdf" else 1
    DEFAULT_OPERATION_REGISTRY.validate_start(
        _payload(
            source=_source(content_kind=content_kind),
            parameters=_parameters(page_count=page_count),
            limits=_limits(page_count=page_count),
        )
    )


@pytest.mark.parametrize(
    "value",
    (
        "eng;run-command",
        "eng && run-command",
        "../../tessdata",
        "c:\\source.pdf",
        "line\nbreak",
    ),
)
def test_ocr_process_configuration_rejects_command_and_path_fragments(value: str) -> None:
    with pytest.raises(ProtocolError) as raised:
        TaggedScalar(OCR_PROCESS_LANGUAGE_SET_ID_PARAMETER, ScalarValueType.TOKEN, value)

    assert raised.value.code == "RPC_INVALID_TOKEN"
    assert value not in repr(raised.value)
    assert value not in str(raised.value)


@pytest.mark.parametrize(
    "payload",
    (
        replace(_payload(), inputs=()),
        _payload(source=replace(_source(), area=ReferenceArea.ASSET_READ)),
        _payload(source=replace(_source(), content_kind="tiff")),
        _payload(source=replace(_source(), expected_byte_size=0)),
        _payload(source=replace(_source(), expected_byte_size=None)),
        _payload(source=replace(_source(), expected_sha256=None)),
        _payload(parameters=_parameters()[:-1]),
        _payload(parameters=(*_parameters(), TaggedScalar("extra", ScalarValueType.INTEGER, 1))),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(OCR_PROCESS_SOURCE_HANDLE_PARAMETER, ScalarValueType.INTEGER, 0)
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(OCR_PROCESS_OUTPUT_HANDLE_PARAMETER, ScalarValueType.INTEGER, 501)
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(OCR_PROCESS_RESULT_HANDLE_PARAMETER, ScalarValueType.TOKEN, "pipe")
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(
                    OCR_PROCESS_OUTPUT_OBJECT_ID_PARAMETER, ScalarValueType.UUID, _uuid(21)
                )
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(OCR_PROCESS_PAGE_COUNT_PARAMETER, ScalarValueType.INTEGER, 1)
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(OCR_PROCESS_LANGUAGE_SET_ID_PARAMETER, ScalarValueType.INTEGER, 1)
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(OCR_PROCESS_PROFILE_ID_PARAMETER, ScalarValueType.INTEGER, 1)
            )
        ),
        _payload(source=_source(content_kind="jpeg")),
        _payload(limits=_limits(page_count=4_097)),
        _payload(limits=_limits(temporary_storage_bytes=5_119)),
    ),
)
def test_ocr_process_rejects_every_start_contract_deviation(
    payload: OperationStartPayload,
) -> None:
    with pytest.raises(ProtocolError) as raised:
        DEFAULT_OPERATION_REGISTRY.validate_start(payload)

    assert raised.value.code == "RPC_INVALID_OPERATION"


def test_ocr_process_binds_case_operation_source_and_output_ids() -> None:
    client, _worker = _negotiated_peers()
    for operation_id, payload in (
        (_uuid(3), _payload()),
        (_uuid(21), _payload()),
        (_uuid(22), _payload()),
        (_uuid(31), _payload(source=_source(object_id=_uuid(3)))),
    ):
        with pytest.raises(ProtocolError) as raised:
            client.prepare_outbound(
                MessageType.OPERATION_START,
                payload,
                request_id=_uuid(40 + operation_id.int % 10),
                operation_id=operation_id,
            )
        assert raised.value.code == "RPC_INVALID_OPERATION"


def test_ocr_process_requires_exact_numeric_page_progress() -> None:
    _client, worker, request_id, operation_id = _started_peers()

    for progress in (
        OperationProgressPayload(ProgressStage.PROCESSING, 1, 2, ProgressUnit.STEPS),
        OperationProgressPayload(ProgressStage.PROCESSING, 1, 1, ProgressUnit.PAGES),
    ):
        with pytest.raises(ProtocolError) as raised:
            worker.prepare_outbound(
                MessageType.OPERATION_PROGRESS,
                progress,
                request_id=request_id,
                operation_id=operation_id,
            )
        assert raised.value.code == "RPC_INVALID_PROGRESS"


def test_ocr_process_success_is_bound_to_one_opaque_pdf_candidate() -> None:
    client, worker, request_id, operation_id = _started_peers()
    progress = worker.prepare_outbound(
        MessageType.OPERATION_PROGRESS,
        OperationProgressPayload(ProgressStage.PROCESSING, 2, 2, ProgressUnit.PAGES),
        request_id=request_id,
        operation_id=operation_id,
    )
    client.accept_inbound(progress.encoded)
    terminal = worker.prepare_outbound(
        MessageType.OPERATION_RESULT,
        OperationResultPayload(OperationOutcome.SUCCEEDED, (_output(),), None),
        request_id=request_id,
        operation_id=operation_id,
    )

    accepted = client.accept_inbound(terminal.encoded)

    assert accepted.payload == OperationResultPayload(
        OperationOutcome.SUCCEEDED,
        (_output(),),
        None,
    )
    assert client.state.active_operation_id is None
    assert worker.state.active_operation_id is None


@pytest.mark.parametrize(
    "outputs",
    (
        (),
        (_output(), _output()),
        (_output(object_id=_uuid(99)),),
        (_output(operation_id=_uuid(99)),),
        (_output(area=ReferenceArea.OPERATION_TEMP),),
        (_output(content_kind="pdf"),),
        (_output(byte_size=0),),
        (_output(byte_size=4_097),),
        (_output(byte_size=None),),
        (_output(digest=None),),
    ),
)
def test_ocr_process_rejects_unbound_success_outputs(
    outputs: tuple[CaseObjectReference, ...],
) -> None:
    with pytest.raises(ProtocolError) as raised:
        validate_ocr_process_result(
            OperationResultPayload(OperationOutcome.SUCCEEDED, outputs, None),
            operation_id=_uuid(31),
            output_object_id=_uuid(22),
            maximum_output_bytes=4_096,
        )

    assert raised.value.code == "RPC_INVALID_RESULT"


def _context(
    *,
    cancellation: Event | None = None,
    deadline: Event | None = None,
    progress: list[OperationProgressPayload] | None = None,
    parameters: tuple[TaggedScalar, ...] | None = None,
    output_bytes: int = 4_096,
) -> OperationContext:
    cancellation_event = Event() if cancellation is None else cancellation
    deadline_event = Event() if deadline is None else deadline
    progress_events = [] if progress is None else progress
    return OperationContext(
        operation_id=_uuid(31),
        case_id=_uuid(3),
        limits=_limits(output_bytes=output_bytes),
        cancel_requested=cancellation_event,
        report_progress=progress_events.append,
        deadline_expired=deadline_event.is_set,
        inputs=(_source(),),
        parameters=_parameters() if parameters is None else parameters,
    )


def _staging_reference(
    context: OperationContext,
    request: OcrProcessOperationRequest,
    *,
    area: ReferenceArea = ReferenceArea.OPERATION_STAGING,
    object_id: UUID | None = None,
    operation_id: UUID | None = None,
    content_kind: str = OCR_PROCESS_OUTPUT_CONTENT_KIND,
    expected_byte_size: int | None = 2_048,
    expected_sha256: str | None = "b" * 64,
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
            Callable[[OperationContext, OcrProcessOperationRequest], CaseObjectReference] | None
        ) = None,
        failure: OcrError | RuntimeError | None = None,
        after_process: Callable[[OperationContext], None] | None = None,
    ) -> None:
        self.output_factory = _staging_reference if output_factory is None else output_factory
        self.failure = failure
        self.after_process = after_process
        self.discard_calls: list[tuple[OperationContext, OcrProcessOperationRequest]] = []
        self.process_calls: list[tuple[OperationContext, OcrProcessOperationRequest]] = []

    def discard_handles(
        self,
        context: OperationContext,
        request: OcrProcessOperationRequest,
    ) -> None:
        self.discard_calls.append((context, request))

    def process(
        self,
        context: OperationContext,
        request: OcrProcessOperationRequest,
    ) -> CaseObjectReference:
        self.process_calls.append((context, request))
        if self.after_process is not None:
            self.after_process(context)
        if self.failure is not None:
            raise self.failure
        return self.output_factory(context, request)


def test_handler_parses_redacted_request_and_returns_exact_staging_reference() -> None:
    progress: list[OperationProgressPayload] = []
    context = _context(progress=progress)
    adapter = _RecordingAdapter()
    handler = OcrProcessOperationHandler(adapter)

    result = handler.execute(context)

    assert handler.descriptor == OCR_PROCESS_CAPABILITY
    assert result.outcome is OperationOutcome.SUCCEEDED
    assert result.error is None
    assert len(result.outputs) == 1
    assert adapter.discard_calls == []
    assert len(adapter.process_calls) == 1
    process_context, request = adapter.process_calls[0]
    assert process_context is context
    assert request == OcrProcessOperationRequest(
        501,
        503,
        505,
        _uuid(22),
        "pdf",
        2,
        "latin-default-v1",
        "searchable-pdf-v1",
    )
    assert all(
        value not in repr(request)
        for value in ("501", "503", "505", "latin-default-v1", "searchable-pdf-v1")
    )
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
def test_handler_pre_dispatch_terminal_state_discards_all_handles(
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

    result = OcrProcessOperationHandler(adapter).execute(
        _context(cancellation=cancellation, deadline=deadline)
    )

    assert result.outcome is expected_outcome
    assert result.outputs == ()
    assert adapter.process_calls == []
    assert len(adapter.discard_calls) == 1


@pytest.mark.parametrize(
    ("error_code", "expected_outcome"),
    (
        (OcrErrorCode.CANCELLED, OperationOutcome.CANCELLED),
        (OcrErrorCode.TIMED_OUT, OperationOutcome.TIMED_OUT),
    ),
)
def test_handler_maps_closed_terminal_adapter_failures_without_outputs(
    error_code: OcrErrorCode,
    expected_outcome: OperationOutcome,
) -> None:
    adapter = _RecordingAdapter(failure=OcrError(error_code, "ocr.synthetic"))

    result = OcrProcessOperationHandler(adapter).execute(_context())

    assert result.outcome is expected_outcome
    assert result.outputs == ()
    assert len(adapter.process_calls) == 1


def test_handler_propagates_nonterminal_failure_for_runtime_sanitization() -> None:
    failure = OcrError(OcrErrorCode.ENGINE_FAILED, "ocr.synthetic")
    adapter = _RecordingAdapter(failure=failure)

    with pytest.raises(OcrError) as raised:
        OcrProcessOperationHandler(adapter).execute(_context())

    assert raised.value is failure


@pytest.mark.parametrize("late_state", ("cancelled", "timed_out"))
def test_handler_suppresses_output_after_late_terminal_state(late_state: str) -> None:
    cancellation = Event()
    deadline = Event()

    def set_late_state(_context: OperationContext) -> None:
        if late_state == "cancelled":
            cancellation.set()
        else:
            deadline.set()

    adapter = _RecordingAdapter(after_process=set_late_state)
    result = OcrProcessOperationHandler(adapter).execute(
        _context(cancellation=cancellation, deadline=deadline)
    )

    assert result.outcome is (
        OperationOutcome.CANCELLED if late_state == "cancelled" else OperationOutcome.TIMED_OUT
    )
    assert result.outputs == ()


@pytest.mark.parametrize(
    "output_factory",
    (
        lambda context, request: _staging_reference(
            context, request, area=ReferenceArea.OPERATION_TEMP
        ),
        lambda context, request: _staging_reference(context, request, object_id=_uuid(99)),
        lambda context, request: _staging_reference(context, request, operation_id=_uuid(99)),
        lambda context, request: _staging_reference(context, request, content_kind="pdf"),
        lambda context, request: _staging_reference(context, request, expected_byte_size=0),
        lambda context, request: _staging_reference(context, request, expected_byte_size=4_097),
        lambda context, request: _staging_reference(context, request, expected_byte_size=None),
        lambda context, request: _staging_reference(context, request, expected_sha256=None),
    ),
)
def test_handler_rejects_unbound_or_incomplete_adapter_output(
    output_factory: Callable[[OperationContext, OcrProcessOperationRequest], CaseObjectReference],
) -> None:
    adapter = _RecordingAdapter(output_factory=output_factory)

    with pytest.raises(ValueError, match="invalid staging reference"):
        OcrProcessOperationHandler(adapter).execute(_context())


def test_handler_rejects_nonreference_or_mistyped_parameters_before_success() -> None:
    def nonreference(
        _context: OperationContext,
        _request: OcrProcessOperationRequest,
    ) -> CaseObjectReference:
        return cast(CaseObjectReference, object())

    with pytest.raises(ValueError, match="invalid staging reference"):
        OcrProcessOperationHandler(_RecordingAdapter(output_factory=nonreference)).execute(
            _context()
        )

    adapter = _RecordingAdapter()
    bad_parameters = _replace_parameter(
        TaggedScalar(OCR_PROCESS_RESULT_HANDLE_PARAMETER, ScalarValueType.TOKEN, "pipe")
    )
    with pytest.raises(ValueError, match="validated integer parameter"):
        OcrProcessOperationHandler(adapter).execute(_context(parameters=bad_parameters))
    assert adapter.process_calls == []

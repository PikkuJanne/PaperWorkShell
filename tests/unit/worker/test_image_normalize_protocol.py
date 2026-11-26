"""Focused protocol and handler tests for ``image.normalize`` version 1."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from threading import Event
from uuid import UUID

import pytest

from paperworkshell.domain.image_pages import ImagePageError, ImagePageErrorCode
from paperworkshell.worker.operations import (
    ImageNormalizeOperationHandler,
    ImageNormalizeOperationRequest,
    OperationContext,
)
from paperworkshell.worker.protocol.codec import DEFAULT_OPERATION_REGISTRY, encode_message
from paperworkshell.worker.protocol.errors import ProtocolError
from paperworkshell.worker.protocol.models import (
    IMAGE_NORMALIZE_ADDITIONAL_ROTATION_PARAMETER,
    IMAGE_NORMALIZE_CAPABILITY,
    IMAGE_NORMALIZE_MAXIMUM_HEIGHT_PARAMETER,
    IMAGE_NORMALIZE_MAXIMUM_WIDTH_PARAMETER,
    IMAGE_NORMALIZE_OUTPUT_CONTENT_KIND,
    IMAGE_NORMALIZE_OUTPUT_HANDLE_PARAMETER,
    IMAGE_NORMALIZE_OUTPUT_OBJECT_ID_PARAMETER,
    IMAGE_NORMALIZE_SOURCE_HANDLE_PARAMETER,
    MAX_MESSAGE_BYTES,
    MAX_SAFE_INTEGER,
    CaseObjectReference,
    Direction,
    ErrorCategory,
    ErrorCode,
    ErrorPayload,
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
    RetryAction,
    ScalarValueType,
    SessionCapability,
    SessionHelloPayload,
    SessionReadyPayload,
    TaggedScalar,
    validate_image_normalize_result,
)
from paperworkshell.worker.protocol.peer import PeerBinding, ProtocolPeer


def _uuid(value: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{value:012x}")


def _limits(*, output_bytes: int = 8_192, pixel_count: int = 10_000) -> ResourceLimits:
    return ResourceLimits(
        wall_time_ms=1_000,
        cpu_time_ms=500,
        memory_bytes=64 * 1024 * 1024,
        temporary_storage_bytes=16 * 1024 * 1024,
        output_bytes=output_bytes,
        page_count=1,
        pixel_count=pixel_count,
    )


def _source(
    *,
    object_id: UUID | None = None,
    content_kind: str = "jpeg",
) -> CaseObjectReference:
    return CaseObjectReference(
        area=ReferenceArea.SOURCE_READ,
        object_id=_uuid(20) if object_id is None else object_id,
        operation_id=None,
        content_kind=content_kind,
        expected_byte_size=1_024,
        expected_sha256="a" * 64,
    )


def _parameters() -> tuple[TaggedScalar, ...]:
    return (
        TaggedScalar(IMAGE_NORMALIZE_SOURCE_HANDLE_PARAMETER, ScalarValueType.INTEGER, 501),
        TaggedScalar(IMAGE_NORMALIZE_OUTPUT_HANDLE_PARAMETER, ScalarValueType.INTEGER, 503),
        TaggedScalar(IMAGE_NORMALIZE_OUTPUT_OBJECT_ID_PARAMETER, ScalarValueType.UUID, _uuid(21)),
        TaggedScalar(IMAGE_NORMALIZE_MAXIMUM_WIDTH_PARAMETER, ScalarValueType.INTEGER, 100),
        TaggedScalar(IMAGE_NORMALIZE_MAXIMUM_HEIGHT_PARAMETER, ScalarValueType.INTEGER, 100),
        TaggedScalar(IMAGE_NORMALIZE_ADDITIONAL_ROTATION_PARAMETER, ScalarValueType.INTEGER, 90),
    )


def _replace_parameter(replacement: TaggedScalar) -> tuple[TaggedScalar, ...]:
    return tuple(
        replacement if parameter.name == replacement.name else parameter
        for parameter in _parameters()
    )


def _case_aliased_start(aliased_identity: str) -> tuple[OperationStartPayload, UUID]:
    case_id = _uuid(3)
    operation_id = case_id if aliased_identity == "operation" else _uuid(31)
    inputs = (_source(object_id=case_id),) if aliased_identity == "source" else (_source(),)
    parameters = _parameters()
    if aliased_identity == "output":
        parameters = _replace_parameter(
            TaggedScalar(
                IMAGE_NORMALIZE_OUTPUT_OBJECT_ID_PARAMETER,
                ScalarValueType.UUID,
                case_id,
            )
        )
    return _payload(inputs=inputs, parameters=parameters), operation_id


def _payload(
    *,
    inputs: tuple[CaseObjectReference, ...] | None = None,
    parameters: tuple[TaggedScalar, ...] | None = None,
    limits: ResourceLimits | None = None,
) -> OperationStartPayload:
    return OperationStartPayload(
        operation_kind=IMAGE_NORMALIZE_CAPABILITY.capability_id,
        operation_version=IMAGE_NORMALIZE_CAPABILITY.capability_version,
        inputs=(_source(),) if inputs is None else inputs,
        parameters=_parameters() if parameters is None else parameters,
        requested_limits=_limits() if limits is None else limits,
    )


def _output(
    *,
    object_id: UUID | None = None,
    operation_id: UUID | None = None,
    area: ReferenceArea = ReferenceArea.OPERATION_STAGING,
    content_kind: str = IMAGE_NORMALIZE_OUTPUT_CONTENT_KIND,
    byte_size: int | None = 4_096,
    digest: str | None = "b" * 64,
) -> CaseObjectReference:
    return CaseObjectReference(
        area=area,
        object_id=_uuid(21) if object_id is None else object_id,
        operation_id=_uuid(31) if operation_id is None else operation_id,
        content_kind=content_kind,
        expected_byte_size=byte_size,
        expected_sha256=digest,
    )


def _negotiated_peers() -> tuple[ProtocolPeer, ProtocolPeer]:
    binding = PeerBinding(_uuid(1), _uuid(2), _uuid(3), SessionCapability("c" * 64))
    client = ProtocolPeer(binding, Direction.CLIENT_TO_WORKER)
    worker = ProtocolPeer(binding, Direction.WORKER_TO_CLIENT)
    hello = client.prepare_outbound(
        MessageType.SESSION_HELLO,
        SessionHelloPayload((1,), (IMAGE_NORMALIZE_CAPABILITY,)),
        request_id=_uuid(10),
        operation_id=None,
    )
    accepted_hello = worker.accept_inbound(hello.encoded)
    ready = worker.prepare_outbound(
        MessageType.SESSION_READY,
        SessionReadyPayload(1, (IMAGE_NORMALIZE_CAPABILITY,), MAX_MESSAGE_BYTES),
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
            IMAGE_NORMALIZE_CAPABILITY.capability_id,
            IMAGE_NORMALIZE_CAPABILITY.capability_version,
            _limits(),
        ),
        request_id=request_id,
        operation_id=operation_id,
    )
    client.accept_inbound(accepted.encoded)
    return client, worker, request_id, operation_id


@pytest.mark.parametrize("content_kind", ("jpeg", "png"))
def test_image_normalize_is_registered_and_round_trips_valid_sources(
    content_kind: str,
) -> None:
    client, worker = _negotiated_peers()
    payload = _payload(inputs=(_source(content_kind=content_kind),))
    start = client.prepare_outbound(
        MessageType.OPERATION_START,
        payload,
        request_id=_uuid(30),
        operation_id=_uuid(31),
    )

    accepted = worker.accept_inbound(start.encoded)

    assert IMAGE_NORMALIZE_CAPABILITY in DEFAULT_OPERATION_REGISTRY.capabilities
    assert accepted.payload == payload


@pytest.mark.parametrize(
    "payload",
    (
        _payload(inputs=()),
        _payload(inputs=(_source(), _source(object_id=_uuid(22)))),
        _payload(inputs=(replace(_source(), area=ReferenceArea.ASSET_READ),)),
        _payload(inputs=(_source(content_kind="jpg"),)),
        _payload(inputs=(replace(_source(), expected_byte_size=None),)),
        _payload(inputs=(replace(_source(), expected_byte_size=0),)),
        _payload(inputs=(replace(_source(), expected_sha256=None),)),
        _payload(parameters=_parameters()[:-1]),
        _payload(
            parameters=(*_parameters(), TaggedScalar("unexpected", ScalarValueType.INTEGER, 1))
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(IMAGE_NORMALIZE_SOURCE_HANDLE_PARAMETER, ScalarValueType.INTEGER, 0)
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(IMAGE_NORMALIZE_SOURCE_HANDLE_PARAMETER, ScalarValueType.TOKEN, "one")
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(IMAGE_NORMALIZE_OUTPUT_HANDLE_PARAMETER, ScalarValueType.INTEGER, 501)
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(
                    IMAGE_NORMALIZE_OUTPUT_OBJECT_ID_PARAMETER,
                    ScalarValueType.TOKEN,
                    "output",
                )
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(
                    IMAGE_NORMALIZE_OUTPUT_OBJECT_ID_PARAMETER,
                    ScalarValueType.UUID,
                    _uuid(20),
                )
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(IMAGE_NORMALIZE_MAXIMUM_WIDTH_PARAMETER, ScalarValueType.INTEGER, 0)
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(IMAGE_NORMALIZE_MAXIMUM_HEIGHT_PARAMETER, ScalarValueType.TOKEN, "one")
            )
        ),
        _payload(limits=_limits(pixel_count=9_999)),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(
                    IMAGE_NORMALIZE_MAXIMUM_WIDTH_PARAMETER,
                    ScalarValueType.INTEGER,
                    MAX_SAFE_INTEGER,
                )
            ),
            limits=_limits(pixel_count=MAX_SAFE_INTEGER),
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(
                    IMAGE_NORMALIZE_ADDITIONAL_ROTATION_PARAMETER,
                    ScalarValueType.INTEGER,
                    45,
                )
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(
                    IMAGE_NORMALIZE_ADDITIONAL_ROTATION_PARAMETER,
                    ScalarValueType.TOKEN,
                    "zero",
                )
            )
        ),
    ),
)
def test_image_normalize_rejects_every_start_contract_deviation(
    payload: OperationStartPayload,
) -> None:
    with pytest.raises(ProtocolError) as raised:
        DEFAULT_OPERATION_REGISTRY.validate_start(payload)

    assert raised.value.code == "RPC_INVALID_OPERATION"


@pytest.mark.parametrize("aliased_object", ("source", "output"))
def test_image_normalize_rejects_operation_and_object_identity_aliasing(
    aliased_object: str,
) -> None:
    operation_id = _uuid(31)
    inputs = (_source(object_id=operation_id) if aliased_object == "source" else _source(),)
    parameters = _parameters()
    if aliased_object == "output":
        parameters = _replace_parameter(
            TaggedScalar(
                IMAGE_NORMALIZE_OUTPUT_OBJECT_ID_PARAMETER,
                ScalarValueType.UUID,
                operation_id,
            )
        )
    client, _worker = _negotiated_peers()

    with pytest.raises(ProtocolError) as raised:
        client.prepare_outbound(
            MessageType.OPERATION_START,
            _payload(inputs=inputs, parameters=parameters),
            request_id=_uuid(30),
            operation_id=operation_id,
        )

    assert raised.value.code == "RPC_INVALID_OPERATION"


@pytest.mark.parametrize("aliased_identity", ("operation", "source", "output"))
def test_image_normalize_client_rejects_case_identity_aliasing(
    aliased_identity: str,
) -> None:
    payload, operation_id = _case_aliased_start(aliased_identity)
    client, _worker = _negotiated_peers()

    with pytest.raises(ProtocolError) as raised:
        client.prepare_outbound(
            MessageType.OPERATION_START,
            payload,
            request_id=_uuid(30),
            operation_id=operation_id,
        )

    assert raised.value.code == "RPC_INVALID_OPERATION"
    assert client.state.active_operation_id is None


@pytest.mark.parametrize("aliased_identity", ("operation", "source", "output"))
def test_image_normalize_worker_rejects_case_identity_aliasing(
    aliased_identity: str,
) -> None:
    payload, operation_id = _case_aliased_start(aliased_identity)
    client, worker = _negotiated_peers()
    valid = client.prepare_outbound(
        MessageType.OPERATION_START,
        _payload(),
        request_id=_uuid(30),
        operation_id=_uuid(31),
    )
    hostile = replace(valid.envelope, payload=payload, operation_id=operation_id)

    with pytest.raises(ProtocolError) as raised:
        worker.accept_inbound(encode_message(hostile))

    assert raised.value.code == "RPC_INVALID_OPERATION"
    assert worker.state.active_operation_id is None


def test_image_normalize_success_is_bound_to_one_verified_output() -> None:
    client, worker, request_id, operation_id = _started_peers()
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
        (_output(area=ReferenceArea.OPERATION_CACHE),),
        (_output(content_kind="image-normalized-rgb-v1"),),
        (_output(byte_size=0),),
        (_output(byte_size=8_193),),
        (_output(byte_size=None),),
        (_output(digest=None),),
    ),
)
def test_image_normalize_rejects_every_success_output_deviation(
    outputs: tuple[CaseObjectReference, ...],
) -> None:
    with pytest.raises(ProtocolError) as raised:
        validate_image_normalize_result(
            OperationResultPayload(OperationOutcome.SUCCEEDED, outputs, None),
            operation_id=_uuid(31),
            output_object_id=_uuid(21),
            maximum_output_bytes=8_192,
        )

    assert raised.value.code == "RPC_INVALID_RESULT"


@pytest.mark.parametrize(
    "payload",
    (
        OperationResultPayload(OperationOutcome.CANCELLED, (), None),
        OperationResultPayload(
            OperationOutcome.FAILED,
            (),
            ErrorPayload(
                ErrorCode.WORKER_OPERATION_FAILED,
                ErrorCategory.OPERATION,
                RetryAction.RETRY,
                False,
                (),
            ),
        ),
        OperationResultPayload(
            OperationOutcome.TIMED_OUT,
            (),
            ErrorPayload(
                ErrorCode.WORKER_OPERATION_TIMED_OUT,
                ErrorCategory.RESOURCE_LIMIT,
                RetryAction.RESTART_WORKER,
                True,
                (),
            ),
        ),
    ),
)
def test_image_normalize_unsuccessful_results_are_output_free(
    payload: OperationResultPayload,
) -> None:
    validate_image_normalize_result(
        payload,
        operation_id=_uuid(31),
        output_object_id=_uuid(21),
        maximum_output_bytes=8_192,
    )


def _context(
    *,
    cancelled: bool = False,
    deadline: bool = False,
    progress: list[OperationProgressPayload] | None = None,
    parameters: tuple[TaggedScalar, ...] | None = None,
) -> OperationContext:
    cancel_event = Event()
    if cancelled:
        cancel_event.set()
    recorded = [] if progress is None else progress
    return OperationContext(
        operation_id=_uuid(31),
        case_id=_uuid(3),
        limits=_limits(),
        cancel_requested=cancel_event,
        report_progress=recorded.append,
        deadline_expired=lambda: deadline,
        inputs=(_source(),),
        parameters=_parameters() if parameters is None else parameters,
    )


class _RecordingAdapter:
    def __init__(
        self,
        *,
        output_factory: (
            Callable[[OperationContext, ImageNormalizeOperationRequest], CaseObjectReference] | None
        ) = None,
        after_normalize: Callable[[OperationContext], None] | None = None,
    ) -> None:
        self.output_factory = output_factory or (lambda _context, _request: _output())
        self.after_normalize = after_normalize
        self.discarded: list[tuple[OperationContext, ImageNormalizeOperationRequest]] = []
        self.normalize_calls: list[tuple[OperationContext, ImageNormalizeOperationRequest]] = []

    def discard_handles(
        self,
        context: OperationContext,
        request: ImageNormalizeOperationRequest,
    ) -> None:
        self.discarded.append((context, request))

    def normalize(
        self,
        context: OperationContext,
        request: ImageNormalizeOperationRequest,
    ) -> CaseObjectReference:
        self.normalize_calls.append((context, request))
        if self.after_normalize is not None:
            self.after_normalize(context)
        return self.output_factory(context, request)


def test_image_normalize_handler_passes_typed_authority_and_reports_progress() -> None:
    progress: list[OperationProgressPayload] = []
    context = _context(progress=progress)
    adapter = _RecordingAdapter()

    result = ImageNormalizeOperationHandler(adapter).execute(context)

    assert result == OperationResultPayload(OperationOutcome.SUCCEEDED, (_output(),), None)
    assert len(adapter.normalize_calls) == 1
    assert adapter.discarded == []
    assert adapter.normalize_calls[0][1] == ImageNormalizeOperationRequest(
        source_handle=501,
        output_handle=503,
        output_object_id=_uuid(21),
        maximum_width_pixels=100,
        maximum_height_pixels=100,
        additional_rotation=90,
    )
    assert progress == [
        OperationProgressPayload(ProgressStage.PROCESSING, 0, 1, ProgressUnit.PAGES),
        OperationProgressPayload(ProgressStage.PROCESSING, 1, 1, ProgressUnit.PAGES),
    ]


@pytest.mark.parametrize(
    ("cancelled", "deadline", "expected_outcome"),
    (
        (True, False, OperationOutcome.CANCELLED),
        (False, True, OperationOutcome.TIMED_OUT),
        (True, True, OperationOutcome.CANCELLED),
    ),
)
def test_image_normalize_handler_disposes_handles_before_skipped_dispatch(
    cancelled: bool,
    deadline: bool,
    expected_outcome: OperationOutcome,
) -> None:
    adapter = _RecordingAdapter()
    result = ImageNormalizeOperationHandler(adapter).execute(
        _context(cancelled=cancelled, deadline=deadline)
    )

    assert result.outcome is expected_outcome
    assert result.outputs == ()
    assert len(adapter.discarded) == 1
    assert adapter.normalize_calls == []


@pytest.mark.parametrize(
    ("code", "expected_outcome", "expected_error"),
    (
        (ImagePageErrorCode.CANCELLED, OperationOutcome.CANCELLED, None),
        (
            ImagePageErrorCode.TIMED_OUT,
            OperationOutcome.TIMED_OUT,
            ErrorPayload(
                ErrorCode.WORKER_OPERATION_TIMED_OUT,
                ErrorCategory.RESOURCE_LIMIT,
                RetryAction.RESTART_WORKER,
                True,
                (),
            ),
        ),
        (
            ImagePageErrorCode.PREVIEW_STORAGE_CAPACITY,
            OperationOutcome.FAILED,
            ErrorPayload(
                ErrorCode.WORKER_OPERATION_FAILED,
                ErrorCategory.RESOURCE_LIMIT,
                RetryAction.RETRY,
                False,
                (),
            ),
        ),
    ),
)
def test_image_normalize_handler_maps_terminal_domain_failures(
    code: ImagePageErrorCode,
    expected_outcome: OperationOutcome,
    expected_error: ErrorPayload | None,
) -> None:
    def fail(
        _context: OperationContext,
        _request: ImageNormalizeOperationRequest,
    ) -> CaseObjectReference:
        raise ImagePageError(code, "image.synthetic")

    result = ImageNormalizeOperationHandler(_RecordingAdapter(output_factory=fail)).execute(
        _context()
    )

    assert result == OperationResultPayload(expected_outcome, (), expected_error)


@pytest.mark.parametrize(
    "code",
    tuple(
        code
        for code in ImagePageErrorCode
        if code
        not in {
            ImagePageErrorCode.CANCELLED,
            ImagePageErrorCode.TIMED_OUT,
            ImagePageErrorCode.PREVIEW_STORAGE_CAPACITY,
        }
    ),
)
def test_image_normalize_handler_propagates_closed_sanitized_domain_failure(
    code: ImagePageErrorCode,
) -> None:
    failure = ImagePageError(code, "image.synthetic")

    def fail(
        _context: OperationContext,
        _request: ImageNormalizeOperationRequest,
    ) -> CaseObjectReference:
        raise failure

    with pytest.raises(ImagePageError) as caught:
        ImageNormalizeOperationHandler(_RecordingAdapter(output_factory=fail)).execute(_context())

    assert caught.value is failure
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize("late_state", ("cancelled", "deadline"))
def test_image_normalize_handler_suppresses_output_after_late_terminal_state(
    late_state: str,
) -> None:
    progress: list[OperationProgressPayload] = []
    deadline = False

    def set_late_state(context: OperationContext) -> None:
        nonlocal deadline
        if late_state == "cancelled":
            context.cancel_requested.set()
        else:
            deadline = True

    context = _context(progress=progress)
    context = replace(context, deadline_expired=lambda: deadline)
    result = ImageNormalizeOperationHandler(
        _RecordingAdapter(after_normalize=set_late_state)
    ).execute(context)

    assert result.outcome is (
        OperationOutcome.CANCELLED if late_state == "cancelled" else OperationOutcome.TIMED_OUT
    )
    assert result.outputs == ()
    assert progress == [
        OperationProgressPayload(ProgressStage.PROCESSING, 0, 1, ProgressUnit.PAGES)
    ]


@pytest.mark.parametrize(
    "output_factory",
    (
        lambda _context, _request: _output(object_id=_uuid(99)),
        lambda _context, _request: _output(operation_id=_uuid(99)),
        lambda _context, _request: _output(area=ReferenceArea.OPERATION_CACHE),
        lambda _context, _request: _output(content_kind="png"),
        lambda _context, _request: _output(byte_size=8_193),
        lambda _context, _request: _output(digest=None),
    ),
)
def test_image_normalize_handler_rejects_untrusted_adapter_outputs(
    output_factory: Callable[
        [OperationContext, ImageNormalizeOperationRequest], CaseObjectReference
    ],
) -> None:
    with pytest.raises(ValueError, match="invalid staging reference"):
        ImageNormalizeOperationHandler(_RecordingAdapter(output_factory=output_factory)).execute(
            _context()
        )


@pytest.mark.parametrize(
    ("parameter_name", "value_type", "value"),
    (
        (IMAGE_NORMALIZE_SOURCE_HANDLE_PARAMETER, ScalarValueType.INTEGER, 0),
        (IMAGE_NORMALIZE_OUTPUT_HANDLE_PARAMETER, ScalarValueType.INTEGER, 501),
        (IMAGE_NORMALIZE_OUTPUT_OBJECT_ID_PARAMETER, ScalarValueType.TOKEN, "output"),
        (IMAGE_NORMALIZE_MAXIMUM_WIDTH_PARAMETER, ScalarValueType.INTEGER, 0),
        (IMAGE_NORMALIZE_ADDITIONAL_ROTATION_PARAMETER, ScalarValueType.INTEGER, 45),
    ),
)
def test_image_normalize_handler_fails_closed_on_unvalidated_parameters(
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

    with pytest.raises(ValueError, match="validated"):
        ImageNormalizeOperationHandler(_RecordingAdapter()).execute(_context(parameters=parameters))

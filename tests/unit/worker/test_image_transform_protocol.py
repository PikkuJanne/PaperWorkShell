"""Focused protocol contract tests for ``image.transform`` version 1."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from threading import Event
from uuid import UUID

import pytest

from paperworkshell.worker.operations import (
    ImageTransformOperationHandler,
    ImageTransformOperationRequest,
    OperationContext,
)
from paperworkshell.worker.protocol.codec import DEFAULT_OPERATION_REGISTRY
from paperworkshell.worker.protocol.errors import ProtocolError
from paperworkshell.worker.protocol.models import (
    IMAGE_TRANSFORM_BACKGROUND_CLEANUP_PARAMETER,
    IMAGE_TRANSFORM_CAPABILITY,
    IMAGE_TRANSFORM_CONTRAST_PARAMETER,
    IMAGE_TRANSFORM_CROP_HEIGHT_PARAMETER,
    IMAGE_TRANSFORM_CROP_WIDTH_PARAMETER,
    IMAGE_TRANSFORM_CROP_X_PARAMETER,
    IMAGE_TRANSFORM_CROP_Y_PARAMETER,
    IMAGE_TRANSFORM_DESKEW_PARAMETER,
    IMAGE_TRANSFORM_MAXIMUM_HEIGHT_PARAMETER,
    IMAGE_TRANSFORM_MAXIMUM_WIDTH_PARAMETER,
    IMAGE_TRANSFORM_OUTPUT_CONTENT_KIND,
    IMAGE_TRANSFORM_OUTPUT_HANDLE_PARAMETER,
    IMAGE_TRANSFORM_OUTPUT_OBJECT_ID_PARAMETER,
    IMAGE_TRANSFORM_PAGE_ID_PARAMETER,
    IMAGE_TRANSFORM_QUARTER_TURNS_PARAMETER,
    IMAGE_TRANSFORM_SOURCE_HANDLE_PARAMETER,
    MAX_MESSAGE_BYTES,
    CaseObjectReference,
    Direction,
    MessageType,
    OperationAcceptedPayload,
    OperationOutcome,
    OperationResultPayload,
    OperationStartPayload,
    ReferenceArea,
    ResourceLimits,
    ScalarValueType,
    SessionCapability,
    SessionHelloPayload,
    SessionReadyPayload,
    TaggedScalar,
    validate_image_transform_result,
)
from paperworkshell.worker.protocol.peer import PeerBinding, ProtocolPeer


def _uuid(value: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{value:012x}")


def _limits() -> ResourceLimits:
    return ResourceLimits(
        wall_time_ms=1_000,
        cpu_time_ms=500,
        memory_bytes=64 * 1024 * 1024,
        temporary_storage_bytes=16 * 1024 * 1024,
        output_bytes=8_192,
        page_count=1,
        pixel_count=10_000,
    )


def _source() -> CaseObjectReference:
    return CaseObjectReference(
        area=ReferenceArea.SOURCE_READ,
        object_id=_uuid(20),
        operation_id=None,
        content_kind="png",
        expected_byte_size=1_024,
        expected_sha256="a" * 64,
    )


def _parameters() -> tuple[TaggedScalar, ...]:
    values = (
        (IMAGE_TRANSFORM_SOURCE_HANDLE_PARAMETER, 501),
        (IMAGE_TRANSFORM_OUTPUT_HANDLE_PARAMETER, 503),
        (IMAGE_TRANSFORM_MAXIMUM_WIDTH_PARAMETER, 100),
        (IMAGE_TRANSFORM_MAXIMUM_HEIGHT_PARAMETER, 100),
        (IMAGE_TRANSFORM_QUARTER_TURNS_PARAMETER, 1),
        (IMAGE_TRANSFORM_CROP_X_PARAMETER, 100_000),
        (IMAGE_TRANSFORM_CROP_Y_PARAMETER, 200_000),
        (IMAGE_TRANSFORM_CROP_WIDTH_PARAMETER, 700_000),
        (IMAGE_TRANSFORM_CROP_HEIGHT_PARAMETER, 600_000),
        (IMAGE_TRANSFORM_DESKEW_PARAMETER, -3_000),
        (IMAGE_TRANSFORM_CONTRAST_PARAMETER, 1_250_000),
        (IMAGE_TRANSFORM_BACKGROUND_CLEANUP_PARAMETER, 400_000),
    )
    return (
        TaggedScalar(IMAGE_TRANSFORM_PAGE_ID_PARAMETER, ScalarValueType.UUID, _uuid(22)),
        TaggedScalar(IMAGE_TRANSFORM_OUTPUT_OBJECT_ID_PARAMETER, ScalarValueType.UUID, _uuid(21)),
        *(TaggedScalar(name, ScalarValueType.INTEGER, value) for name, value in values),
    )


def _payload(*, parameters: tuple[TaggedScalar, ...] | None = None) -> OperationStartPayload:
    return OperationStartPayload(
        operation_kind=IMAGE_TRANSFORM_CAPABILITY.capability_id,
        operation_version=IMAGE_TRANSFORM_CAPABILITY.capability_version,
        inputs=(_source(),),
        parameters=_parameters() if parameters is None else parameters,
        requested_limits=_limits(),
    )


def _replace(
    name: str,
    value: bool | int | str | UUID,
    value_type: ScalarValueType,
) -> tuple[TaggedScalar, ...]:
    replacement = TaggedScalar(name, value_type, value)
    return tuple(replacement if item.name == name else item for item in _parameters())


def test_image_transform_is_registered_and_accepts_exact_integer_intent() -> None:
    payload = _payload()

    DEFAULT_OPERATION_REGISTRY.validate_start(payload)

    assert IMAGE_TRANSFORM_CAPABILITY in DEFAULT_OPERATION_REGISTRY.capabilities


def test_image_transform_round_trips_through_both_conversation_peers() -> None:
    binding = PeerBinding(_uuid(1), _uuid(2), _uuid(3), SessionCapability("c" * 64))
    client = ProtocolPeer(binding, Direction.CLIENT_TO_WORKER)
    worker = ProtocolPeer(binding, Direction.WORKER_TO_CLIENT)
    hello = client.prepare_outbound(
        MessageType.SESSION_HELLO,
        SessionHelloPayload((1,), (IMAGE_TRANSFORM_CAPABILITY,)),
        request_id=_uuid(10),
        operation_id=None,
    )
    accepted_hello = worker.accept_inbound(hello.encoded)
    ready = worker.prepare_outbound(
        MessageType.SESSION_READY,
        SessionReadyPayload(1, (IMAGE_TRANSFORM_CAPABILITY,), MAX_MESSAGE_BYTES),
        request_id=accepted_hello.request_id,
        operation_id=None,
    )
    client.accept_inbound(ready.encoded)
    start = client.prepare_outbound(
        MessageType.OPERATION_START,
        _payload(),
        request_id=_uuid(30),
        operation_id=_uuid(31),
    )
    worker.accept_inbound(start.encoded)
    accepted = worker.prepare_outbound(
        MessageType.OPERATION_ACCEPTED,
        OperationAcceptedPayload("image.transform", 1, _limits()),
        request_id=_uuid(30),
        operation_id=_uuid(31),
    )
    client.accept_inbound(accepted.encoded)
    output = CaseObjectReference(
        area=ReferenceArea.OPERATION_STAGING,
        object_id=_uuid(21),
        operation_id=_uuid(31),
        content_kind=IMAGE_TRANSFORM_OUTPUT_CONTENT_KIND,
        expected_byte_size=4_096,
        expected_sha256="b" * 64,
    )
    terminal = worker.prepare_outbound(
        MessageType.OPERATION_RESULT,
        OperationResultPayload(OperationOutcome.SUCCEEDED, (output,), None),
        request_id=_uuid(30),
        operation_id=_uuid(31),
    )

    result = client.accept_inbound(terminal.encoded)

    assert result.payload == OperationResultPayload(OperationOutcome.SUCCEEDED, (output,), None)
    assert client.state.active_operation_id is None
    assert worker.state.active_operation_id is None


@pytest.mark.parametrize(
    "parameters",
    (
        _parameters()[:-1],
        _replace(IMAGE_TRANSFORM_SOURCE_HANDLE_PARAMETER, 0, ScalarValueType.INTEGER),
        _replace(IMAGE_TRANSFORM_OUTPUT_HANDLE_PARAMETER, 501, ScalarValueType.INTEGER),
        _replace(IMAGE_TRANSFORM_MAXIMUM_WIDTH_PARAMETER, 0, ScalarValueType.INTEGER),
        _replace(IMAGE_TRANSFORM_QUARTER_TURNS_PARAMETER, 4, ScalarValueType.INTEGER),
        _replace(IMAGE_TRANSFORM_CROP_WIDTH_PARAMETER, 900_001, ScalarValueType.INTEGER),
        _replace(IMAGE_TRANSFORM_DESKEW_PARAMETER, 15_001, ScalarValueType.INTEGER),
        _replace(IMAGE_TRANSFORM_CONTRAST_PARAMETER, 4_000_001, ScalarValueType.INTEGER),
        _replace(
            IMAGE_TRANSFORM_BACKGROUND_CLEANUP_PARAMETER,
            1_000_001,
            ScalarValueType.INTEGER,
        ),
        _replace(IMAGE_TRANSFORM_DESKEW_PARAMETER, "three", ScalarValueType.TOKEN),
    ),
)
def test_image_transform_rejects_noncanonical_or_unbounded_intent(
    parameters: tuple[TaggedScalar, ...],
) -> None:
    with pytest.raises(ProtocolError) as raised:
        DEFAULT_OPERATION_REGISTRY.validate_start(_payload(parameters=parameters))

    assert raised.value.code == "RPC_INVALID_OPERATION"


def test_image_transform_success_is_bound_to_exact_artifact_identity() -> None:
    output = CaseObjectReference(
        area=ReferenceArea.OPERATION_STAGING,
        object_id=_uuid(21),
        operation_id=_uuid(31),
        content_kind=IMAGE_TRANSFORM_OUTPUT_CONTENT_KIND,
        expected_byte_size=4_096,
        expected_sha256="b" * 64,
    )

    validate_image_transform_result(
        OperationResultPayload(OperationOutcome.SUCCEEDED, (output,), None),
        operation_id=_uuid(31),
        output_object_id=_uuid(21),
        maximum_output_bytes=8_192,
    )

    with pytest.raises(ProtocolError):
        validate_image_transform_result(
            OperationResultPayload(
                OperationOutcome.SUCCEEDED,
                (replace(output, content_kind="image-normalized-rgba-v1"),),
                None,
            ),
            operation_id=_uuid(31),
            output_object_id=_uuid(21),
            maximum_output_bytes=8_192,
        )


class _Adapter:
    def __init__(
        self,
        output: CaseObjectReference | None = None,
    ) -> None:
        self.output = output
        self.discarded = 0
        self.requests: list[ImageTransformOperationRequest] = []

    def discard_handles(
        self,
        context: OperationContext,
        request: ImageTransformOperationRequest,
    ) -> None:
        del context
        self.requests.append(request)
        self.discarded += 1

    def transform(
        self,
        context: OperationContext,
        request: ImageTransformOperationRequest,
    ) -> CaseObjectReference:
        self.requests.append(request)
        if self.output is None:
            raise AssertionError("missing test output")
        return replace(self.output, operation_id=context.operation_id)


def _context(
    *,
    cancelled: bool = False,
    deadline: Callable[[], bool] = lambda: False,
) -> OperationContext:
    event = Event()
    if cancelled:
        event.set()
    return OperationContext(
        operation_id=_uuid(31),
        case_id=_uuid(3),
        limits=_limits(),
        cancel_requested=event,
        report_progress=lambda _value: None,
        deadline_expired=deadline,
        inputs=(_source(),),
        parameters=_parameters(),
    )


def test_image_transform_handler_passes_exact_typed_intent_and_output() -> None:
    output = CaseObjectReference(
        area=ReferenceArea.OPERATION_STAGING,
        object_id=_uuid(21),
        operation_id=_uuid(99),
        content_kind=IMAGE_TRANSFORM_OUTPUT_CONTENT_KIND,
        expected_byte_size=4_096,
        expected_sha256="b" * 64,
    )
    adapter = _Adapter(output)

    result = ImageTransformOperationHandler(adapter).execute(_context())

    assert result == OperationResultPayload(
        OperationOutcome.SUCCEEDED, (replace(output, operation_id=_uuid(31)),), None
    )
    assert adapter.requests == [
        ImageTransformOperationRequest(
            source_handle=501,
            output_handle=503,
            output_object_id=_uuid(21),
            page_id=_uuid(22),
            maximum_width_pixels=100,
            maximum_height_pixels=100,
            quarter_turns_clockwise=1,
            crop_x_millionths=100_000,
            crop_y_millionths=200_000,
            crop_width_millionths=700_000,
            crop_height_millionths=600_000,
            deskew_millidegrees_clockwise=-3_000,
            contrast_factor_millionths=1_250_000,
            background_cleanup_strength_millionths=400_000,
        )
    ]


@pytest.mark.parametrize("cancelled,expired", ((True, False), (False, True)))
def test_image_transform_handler_consumes_handles_before_skipped_dispatch(
    cancelled: bool,
    expired: bool,
) -> None:
    adapter = _Adapter()

    result = ImageTransformOperationHandler(adapter).execute(
        _context(cancelled=cancelled, deadline=lambda: expired)
    )

    assert result.outcome is (
        OperationOutcome.CANCELLED if cancelled else OperationOutcome.TIMED_OUT
    )
    assert result.outputs == ()
    assert adapter.discarded == 1

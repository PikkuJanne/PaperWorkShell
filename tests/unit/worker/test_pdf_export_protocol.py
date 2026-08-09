"""Focused tests for the closed ``pdf.export`` worker-RPC contract."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest

from paperworkshell.worker.protocol.codec import DEFAULT_OPERATION_REGISTRY
from paperworkshell.worker.protocol.errors import ProtocolError
from paperworkshell.worker.protocol.models import (
    MAX_MESSAGE_BYTES,
    PDF_EXPORT_CAPABILITY,
    PDF_EXPORT_OUTPUT_CONTENT_KIND,
    PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
    PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
    PDF_EXPORT_PLAN_CONTENT_KIND,
    PDF_EXPORT_PLAN_HANDLE_PARAMETER,
    PDF_EXPORT_RESULT_HANDLE_PARAMETER,
    CaseObjectReference,
    Direction,
    ErrorCategory,
    ErrorCode,
    ErrorPayload,
    MessageType,
    OperationAcceptedPayload,
    OperationOutcome,
    OperationResultPayload,
    OperationStartPayload,
    ReferenceArea,
    ResourceLimits,
    RetryAction,
    ScalarValueType,
    SessionCapability,
    SessionHelloPayload,
    SessionReadyPayload,
    TaggedScalar,
    validate_pdf_export_result,
)
from paperworkshell.worker.protocol.peer import PeerBinding, ProtocolPeer


def _uuid(value: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{value:012x}")


def _limits(
    *,
    temporary_storage_bytes: int = 8_192,
    output_bytes: int = 2_048,
    page_count: int = 2,
) -> ResourceLimits:
    return ResourceLimits(
        wall_time_ms=10_000,
        cpu_time_ms=5_000,
        memory_bytes=64 * 1024 * 1024,
        temporary_storage_bytes=temporary_storage_bytes,
        output_bytes=output_bytes,
        page_count=page_count,
        pixel_count=4_096,
    )


def _plan(*, operation_id: UUID | None = None) -> CaseObjectReference:
    return CaseObjectReference(
        area=ReferenceArea.OPERATION_TEMP,
        object_id=_uuid(20),
        operation_id=_uuid(31) if operation_id is None else operation_id,
        content_kind=PDF_EXPORT_PLAN_CONTENT_KIND,
        expected_byte_size=512,
        expected_sha256="a" * 64,
    )


def _source(object_id: UUID | None = None) -> CaseObjectReference:
    return CaseObjectReference(
        area=ReferenceArea.SOURCE_READ,
        object_id=_uuid(21) if object_id is None else object_id,
        operation_id=None,
        content_kind="pdf",
        expected_byte_size=1_024,
        expected_sha256="b" * 64,
    )


def _parameters() -> tuple[TaggedScalar, ...]:
    return (
        TaggedScalar(PDF_EXPORT_PLAN_HANDLE_PARAMETER, ScalarValueType.INTEGER, 501),
        TaggedScalar(PDF_EXPORT_OUTPUT_HANDLE_PARAMETER, ScalarValueType.INTEGER, 503),
        TaggedScalar(PDF_EXPORT_RESULT_HANDLE_PARAMETER, ScalarValueType.INTEGER, 505),
        TaggedScalar(PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER, ScalarValueType.UUID, _uuid(22)),
    )


def _replace_parameter(replacement: TaggedScalar) -> tuple[TaggedScalar, ...]:
    return tuple(
        replacement if parameter.name == replacement.name else parameter
        for parameter in _parameters()
    )


def _payload(
    *,
    inputs: tuple[CaseObjectReference, ...] | None = None,
    parameters: tuple[TaggedScalar, ...] | None = None,
    limits: ResourceLimits | None = None,
) -> OperationStartPayload:
    return OperationStartPayload(
        operation_kind=PDF_EXPORT_CAPABILITY.capability_id,
        operation_version=PDF_EXPORT_CAPABILITY.capability_version,
        inputs=(_plan(), _source()) if inputs is None else inputs,
        parameters=_parameters() if parameters is None else parameters,
        requested_limits=_limits() if limits is None else limits,
    )


def _output(
    *,
    object_id: UUID | None = None,
    operation_id: UUID | None = None,
    area: ReferenceArea = ReferenceArea.OPERATION_STAGING,
    content_kind: str = PDF_EXPORT_OUTPUT_CONTENT_KIND,
    byte_size: int | None = 1_500,
    digest: str | None = "c" * 64,
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
        SessionHelloPayload((1,), (PDF_EXPORT_CAPABILITY,)),
        request_id=_uuid(10),
        operation_id=None,
    )
    accepted_hello = worker.accept_inbound(hello.encoded)
    ready = worker.prepare_outbound(
        MessageType.SESSION_READY,
        SessionReadyPayload(1, (PDF_EXPORT_CAPABILITY,), MAX_MESSAGE_BYTES),
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
            PDF_EXPORT_CAPABILITY.capability_id,
            PDF_EXPORT_CAPABILITY.capability_version,
            _limits(),
        ),
        request_id=request_id,
        operation_id=operation_id,
    )
    client.accept_inbound(accepted.encoded)
    return client, worker, request_id, operation_id


def test_pdf_export_is_registered_and_round_trips_its_path_free_contract() -> None:
    client, worker = _negotiated_peers()
    payload = _payload()

    start = client.prepare_outbound(
        MessageType.OPERATION_START,
        payload,
        request_id=_uuid(30),
        operation_id=_uuid(31),
    )

    assert worker.accept_inbound(start.encoded).payload == payload
    assert PDF_EXPORT_CAPABILITY in DEFAULT_OPERATION_REGISTRY.capabilities
    assert "501" not in repr(payload)


@pytest.mark.parametrize(
    "payload",
    (
        _payload(inputs=()),
        _payload(inputs=(_plan(),)),
        _payload(inputs=(_source(), _plan())),
        _payload(inputs=(replace(_plan(), area=ReferenceArea.OPERATION_CACHE), _source())),
        _payload(inputs=(replace(_plan(), content_kind="plan"), _source())),
        _payload(inputs=(replace(_plan(), expected_byte_size=0), _source())),
        _payload(inputs=(replace(_plan(), expected_byte_size=2 * 1024 * 1024 + 1), _source())),
        _payload(inputs=(replace(_plan(), expected_sha256=None), _source())),
        _payload(inputs=(_plan(), replace(_source(), area=ReferenceArea.ASSET_READ))),
        _payload(inputs=(_plan(), replace(_source(), content_kind="application.pdf"))),
        _payload(inputs=(_plan(), replace(_source(), expected_byte_size=None))),
        _payload(inputs=(_plan(), replace(_source(), expected_sha256=None))),
        _payload(inputs=(_plan(), _source(_uuid(20)))),
        _payload(parameters=_parameters()[:-1]),
        _payload(parameters=(*_parameters(), TaggedScalar("extra", ScalarValueType.INTEGER, 1))),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(PDF_EXPORT_PLAN_HANDLE_PARAMETER, ScalarValueType.INTEGER, 0)
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(PDF_EXPORT_OUTPUT_HANDLE_PARAMETER, ScalarValueType.INTEGER, 501)
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(PDF_EXPORT_RESULT_HANDLE_PARAMETER, ScalarValueType.TOKEN, "pipe")
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER, ScalarValueType.TOKEN, "out")
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER, ScalarValueType.UUID, _uuid(21))
            )
        ),
        _payload(limits=_limits(page_count=4_097)),
        _payload(limits=_limits(temporary_storage_bytes=3_071)),
    ),
)
def test_pdf_export_rejects_every_start_contract_deviation(
    payload: OperationStartPayload,
) -> None:
    with pytest.raises(ProtocolError) as raised:
        DEFAULT_OPERATION_REGISTRY.validate_start(payload)

    assert raised.value.code == "RPC_INVALID_OPERATION"


def test_pdf_export_binds_plan_scope_and_all_object_ids_to_outer_operation() -> None:
    client, _worker = _negotiated_peers()

    with pytest.raises(ProtocolError) as scope_error:
        client.prepare_outbound(
            MessageType.OPERATION_START,
            _payload(inputs=(_plan(operation_id=_uuid(99)), _source())),
            request_id=_uuid(30),
            operation_id=_uuid(31),
        )
    assert scope_error.value.code == "RPC_REFERENCE_SCOPE"

    for aliased_id in (_uuid(20), _uuid(21), _uuid(22)):
        with pytest.raises(ProtocolError) as alias_error:
            client.prepare_outbound(
                MessageType.OPERATION_START,
                _payload(inputs=(_plan(operation_id=aliased_id), _source())),
                request_id=_uuid(40 + aliased_id.int % 10),
                operation_id=aliased_id,
            )
        assert alias_error.value.code == "RPC_INVALID_OPERATION"


def test_pdf_export_success_is_bound_to_one_verified_staging_output() -> None:
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
        (_output(content_kind="pdf"),),
        (_output(byte_size=0),),
        (_output(byte_size=2_049),),
        (_output(byte_size=None),),
        (_output(digest=None),),
    ),
)
def test_pdf_export_rejects_every_success_output_deviation(
    outputs: tuple[CaseObjectReference, ...],
) -> None:
    with pytest.raises(ProtocolError) as raised:
        validate_pdf_export_result(
            OperationResultPayload(OperationOutcome.SUCCEEDED, outputs, None),
            operation_id=_uuid(31),
            output_object_id=_uuid(22),
            maximum_output_bytes=2_048,
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
def test_pdf_export_unsuccessful_results_are_output_free(
    payload: OperationResultPayload,
) -> None:
    validate_pdf_export_result(
        payload,
        operation_id=_uuid(31),
        output_object_id=_uuid(22),
        maximum_output_bytes=2_048,
    )

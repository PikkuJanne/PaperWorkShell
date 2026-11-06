"""PWS-0706 closed text-sidecar RPC and result bindings."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from paperworkshell.worker.client.client import WorkerClient
from paperworkshell.worker.client.errors import WorkerClientError, WorkerClientErrorCode
from paperworkshell.worker.client.models import WorkerLifecyclePhase
from paperworkshell.worker.protocol.codec import DEFAULT_OPERATION_REGISTRY
from paperworkshell.worker.protocol.errors import ProtocolError
from paperworkshell.worker.protocol.models import (
    MAX_MESSAGE_BYTES,
    NOOP_CAPABILITY,
    PDF_INSPECT_CAPABILITY,
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
    SessionHelloPayload,
    SessionReadyPayload,
    TaggedScalar,
)
from paperworkshell.worker.protocol.peer import ProtocolPeer
from paperworkshell.worker.protocol.text_companion import (
    TEXT_COMPANION_CAPABILITY,
    TEXT_COMPANION_CONTENT_KIND,
    TEXT_COMPANION_SOURCE_KIND,
    text_companion_binding,
    validate_text_companion_result,
)


def _payload() -> OperationStartPayload:
    return OperationStartPayload(
        "pdf.text_companion",
        1,
        (
            CaseObjectReference(
                ReferenceArea.SOURCE_READ, uuid4(), None, TEXT_COMPANION_SOURCE_KIND, 500, "a" * 64
            ),
        ),
        (
            TaggedScalar("source_handle", ScalarValueType.INTEGER, 50),
            TaggedScalar("output_handle", ScalarValueType.INTEGER, 52),
            TaggedScalar("output_object_id", ScalarValueType.UUID, uuid4()),
            TaggedScalar("page_count", ScalarValueType.INTEGER, 1),
        ),
        ResourceLimits(1000, 1000, 1000000, 10000, 1000, 10, 1),
    )


def test_text_operation_has_closed_registered_version_and_derivative_bound_source() -> None:
    payload = _payload()
    DEFAULT_OPERATION_REGISTRY.validate_start(payload)
    assert TEXT_COMPANION_CAPABILITY not in DEFAULT_OPERATION_REGISTRY.capabilities
    DEFAULT_OPERATION_REGISTRY.validate_capabilities((TEXT_COMPANION_CAPABILITY,))
    assert text_companion_binding(payload) == payload.parameters[2].value
    with pytest.raises(ProtocolError):
        DEFAULT_OPERATION_REGISTRY.validate_start(replace(payload, operation_version=2))


def test_optional_text_capability_negotiates_and_starts_only_when_explicitly_requested() -> None:
    from tests.unit.worker.test_client_lifecycle import (
        CASE_ID,
        FakeClock,
        FakeLauncher,
        _consume,
        _queue,
        _resource_policy,
        _timeout_policy,
    )

    launcher = FakeLauncher()
    client = WorkerClient(
        launcher,
        _resource_policy(),
        _timeout_policy(),
        clock=FakeClock(),
        required_capabilities=(TEXT_COMPANION_CAPABILITY,),
    )
    try:
        client.start(CASE_ID)
        record = launcher.records[-1]
        record.worker_peer = ProtocolPeer(
            record.worker_peer.binding,
            Direction.WORKER_TO_CLIENT,
            registered_capabilities=(TEXT_COMPANION_CAPABILITY,),
        )
        hello = _consume(record)
        assert type(hello.payload) is SessionHelloPayload
        assert hello.payload.required_capabilities == (TEXT_COMPANION_CAPABILITY,)
        ready = record.worker_peer.prepare_outbound(
            MessageType.SESSION_READY,
            SessionReadyPayload(1, (TEXT_COMPANION_CAPABILITY,), MAX_MESSAGE_BYTES),
            request_id=hello.request_id,
            operation_id=None,
        )
        _queue(record, ready)
        client.pump()
        assert client.query().phase is WorkerLifecyclePhase.READY
        payload = _payload()
        operation_id = client.start_operation(payload)
        start = _consume(record)
        assert start.operation_id == operation_id
        active = record.worker_peer.state.active_operation
        assert (
            active is not None
            and active.text_companion_output_object_id == payload.parameters[2].value
        )
        accepted = record.worker_peer.prepare_outbound(
            MessageType.OPERATION_ACCEPTED,
            OperationAcceptedPayload("pdf.text_companion", 1, payload.requested_limits),
            request_id=start.request_id,
            operation_id=operation_id,
        )
        _queue(record, accepted)
        client.pump()
        output_id = text_companion_binding(payload)
        result = record.worker_peer.prepare_outbound(
            MessageType.OPERATION_RESULT,
            OperationResultPayload(
                OperationOutcome.SUCCEEDED,
                (
                    CaseObjectReference(
                        ReferenceArea.OPERATION_STAGING,
                        output_id,
                        operation_id,
                        TEXT_COMPANION_CONTENT_KIND,
                        5,
                        "b" * 64,
                    ),
                ),
                None,
            ),
            request_id=start.request_id,
            operation_id=operation_id,
        )
        _queue(record, result)
        client.pump()
        terminal = client.query().last_result
        assert terminal is not None
        assert terminal.outputs[0].object_id == output_id
    finally:
        client.terminate()


@pytest.mark.parametrize("unrequested_optional", (False, True))
def test_ready_keeps_extra_known_v1_capabilities_but_refuses_unrequested_optional_text(
    unrequested_optional: bool,
) -> None:
    from tests.unit.worker.test_client_lifecycle import (
        CASE_ID,
        FakeClock,
        FakeLauncher,
        _consume,
        _queue,
        _resource_policy,
        _timeout_policy,
    )

    launcher = FakeLauncher()
    client = WorkerClient(
        launcher,
        _resource_policy(),
        _timeout_policy(),
        clock=FakeClock(),
        required_capabilities=(NOOP_CAPABILITY,),
    )
    try:
        client.start(CASE_ID)
        record = launcher.records[-1]
        # The worker knows this optional operation; the current client has not
        # requested it. Existing v1 capabilities remain locally registered.
        record.worker_peer = ProtocolPeer(
            record.worker_peer.binding,
            Direction.WORKER_TO_CLIENT,
            registered_capabilities=(
                *DEFAULT_OPERATION_REGISTRY.capabilities,
                TEXT_COMPANION_CAPABILITY,
            ),
        )
        hello = _consume(record)
        capabilities = (
            NOOP_CAPABILITY,
            TEXT_COMPANION_CAPABILITY if unrequested_optional else PDF_INSPECT_CAPABILITY,
        )
        ready = record.worker_peer.prepare_outbound(
            MessageType.SESSION_READY,
            SessionReadyPayload(1, capabilities, MAX_MESSAGE_BYTES),
            request_id=hello.request_id,
            operation_id=None,
        )
        _queue(record, ready)
        if unrequested_optional:
            with pytest.raises(WorkerClientError) as caught:
                client.pump()
            assert caught.value.code is WorkerClientErrorCode.PROTOCOL_FAILED
            observed = client.query()
            assert observed.phase is WorkerLifecyclePhase.FAULTED
            assert observed.last_failure is not None
            assert observed.last_failure.code is WorkerClientErrorCode.PROTOCOL_FAILED
        else:
            observed = client.pump()
            assert observed.phase is WorkerLifecyclePhase.READY
            assert observed.capabilities == (NOOP_CAPABILITY, PDF_INSPECT_CAPABILITY)
            with pytest.raises(WorkerClientError):
                client.start_operation(_payload())
    finally:
        client.terminate()


@pytest.mark.parametrize(
    "which",
    ("missing", "extra", "wrong_source", "no_hash", "source_output_alias", "handle_alias", "pages"),
)
def test_malformed_or_unbound_request_is_rejected(which: str) -> None:
    payload = _payload()
    if which == "missing":
        payload = replace(payload, parameters=payload.parameters[:-1])
    elif which == "extra":
        payload = replace(
            payload,
            parameters=(
                *payload.parameters,
                TaggedScalar("arbitrary", ScalarValueType.TOKEN, "anything"),
            ),
        )
    elif which == "wrong_source":
        payload = replace(payload, inputs=(replace(payload.inputs[0], content_kind="pdf"),))
    elif which == "no_hash":
        payload = replace(payload, inputs=(replace(payload.inputs[0], expected_sha256=None),))
    elif which == "source_output_alias":
        payload = replace(
            payload,
            parameters=(
                *payload.parameters[:2],
                TaggedScalar("output_object_id", ScalarValueType.UUID, payload.inputs[0].object_id),
                payload.parameters[3],
            ),
        )
    elif which == "handle_alias":
        payload = replace(
            payload,
            parameters=(
                payload.parameters[0],
                TaggedScalar("output_handle", ScalarValueType.INTEGER, 50),
                *payload.parameters[2:],
            ),
        )
    else:
        payload = replace(
            payload,
            parameters=(
                *payload.parameters[:3],
                TaggedScalar("page_count", ScalarValueType.INTEGER, 11),
            ),
        )
    with pytest.raises(ProtocolError):
        DEFAULT_OPERATION_REGISTRY.validate_start(payload)


@pytest.mark.parametrize("which", ("valid", "operation", "output", "kind", "size", "missing"))
def test_result_requires_exact_output_identity_kind_and_budget(which: str) -> None:
    operation, output_id = uuid4(), uuid4()
    output = CaseObjectReference(
        ReferenceArea.OPERATION_STAGING,
        output_id,
        operation,
        TEXT_COMPANION_CONTENT_KIND,
        40,
        "b" * 64,
    )
    if which == "operation":
        output = replace(output, operation_id=uuid4())
    elif which == "output":
        output = replace(output, object_id=uuid4())
    elif which == "kind":
        output = replace(output, content_kind="pdf")
    elif which == "size":
        output = replace(output, expected_byte_size=41)
    elif which == "missing":
        output = replace(output, expected_sha256=None)
    result = OperationResultPayload(OperationOutcome.SUCCEEDED, (output,), None)
    if which == "valid":
        validate_text_companion_result(
            result, operation_id=operation, output_object_id=output_id, maximum_output_bytes=40
        )
    else:
        with pytest.raises(ProtocolError):
            validate_text_companion_result(
                result, operation_id=operation, output_object_id=output_id, maximum_output_bytes=40
            )

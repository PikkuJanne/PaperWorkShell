"""Hostile authority/result mutations for the explicit PDF image-stage boundary."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, cast
from uuid import UUID

import pytest

from paperworkshell.worker.protocol.codec import decode_message
from paperworkshell.worker.protocol.errors import ProtocolError
from paperworkshell.worker.protocol.image_stage import (
    validate_pdf_image_stage_result,
    validate_pdf_image_stage_start,
)
from paperworkshell.worker.protocol.models import (
    MAX_MESSAGE_BYTES,
    MAX_PDF_IMAGE_STAGE_EVIDENCE_BYTES,
    PDF_IMAGE_STAGE_CAPABILITY,
    PDF_IMAGE_STAGE_EVIDENCE_CONTENT_KIND,
    PDF_IMAGE_STAGE_OUTPUT_CONTENT_KIND,
    PDF_IMAGE_STAGE_PLAN_CONTENT_KIND,
    CaseObjectReference,
    Direction,
    MessageType,
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
)
from paperworkshell.worker.protocol.peer import PeerBinding, ProtocolPeer


def _uuid(value: int) -> UUID:
    return UUID(f"40000000-0000-4000-8000-{value:012x}")


def _start() -> OperationStartPayload:
    return OperationStartPayload(
        operation_kind=PDF_IMAGE_STAGE_CAPABILITY.capability_id,
        operation_version=1,
        inputs=(
            CaseObjectReference(
                ReferenceArea.SOURCE_READ,
                _uuid(10),
                None,
                PDF_IMAGE_STAGE_PLAN_CONTENT_KIND,
                256,
                "a" * 64,
            ),
            CaseObjectReference(ReferenceArea.SOURCE_READ, _uuid(11), None, "pdf", 1024, "b" * 64),
        ),
        parameters=(
            TaggedScalar("plan_handle", ScalarValueType.INTEGER, 401),
            TaggedScalar("output_handle", ScalarValueType.INTEGER, 403),
            TaggedScalar("evidence_handle", ScalarValueType.INTEGER, 405),
            TaggedScalar("result_handle", ScalarValueType.INTEGER, 407),
            TaggedScalar("output_object_id", ScalarValueType.UUID, _uuid(12)),
            TaggedScalar("evidence_object_id", ScalarValueType.UUID, _uuid(13)),
            TaggedScalar("evidence_capacity_bytes", ScalarValueType.INTEGER, 1024),
        ),
        requested_limits=ResourceLimits(
            wall_time_ms=10_000,
            cpu_time_ms=5_000,
            memory_bytes=64 * 1024 * 1024,
            temporary_storage_bytes=6144,
            output_bytes=2048,
            page_count=2,
            pixel_count=4096,
        ),
    )


def _parameter(
    payload: OperationStartPayload, name: str, value: int | UUID
) -> OperationStartPayload:
    return replace(
        payload,
        parameters=tuple(
            replace(parameter, value=value) if parameter.name == name else parameter
            for parameter in payload.parameters
        ),
    )


def _start_wire() -> bytes:
    binding = PeerBinding(_uuid(1), _uuid(2), _uuid(3), SessionCapability("d" * 64))
    client = ProtocolPeer(binding, Direction.CLIENT_TO_WORKER)
    worker = ProtocolPeer(binding, Direction.WORKER_TO_CLIENT)
    hello = client.prepare_outbound(
        MessageType.SESSION_HELLO,
        SessionHelloPayload((1,), (PDF_IMAGE_STAGE_CAPABILITY,)),
        request_id=_uuid(20),
        operation_id=None,
    )
    received = worker.accept_inbound(hello.encoded)
    ready = worker.prepare_outbound(
        MessageType.SESSION_READY,
        SessionReadyPayload(1, (PDF_IMAGE_STAGE_CAPABILITY,), MAX_MESSAGE_BYTES),
        request_id=received.request_id,
        operation_id=None,
    )
    client.accept_inbound(ready.encoded)
    return client.prepare_outbound(
        MessageType.OPERATION_START,
        _start(),
        request_id=_uuid(21),
        operation_id=_uuid(30),
    ).encoded


@pytest.mark.parametrize("left", ("plan_handle", "output_handle", "evidence_handle"))
def test_aliasing_transfer_authorities_is_rejected(left: str) -> None:
    with pytest.raises(ProtocolError, match="RPC_INVALID_OPERATION"):
        validate_pdf_image_stage_start(_parameter(_start(), left, 407))


@pytest.mark.parametrize("name", ("output_object_id", "evidence_object_id"))
@pytest.mark.parametrize("identifier", (_uuid(10), _uuid(11)))
def test_output_cannot_alias_plan_or_immutable_source(name: str, identifier: UUID) -> None:
    with pytest.raises(ProtocolError, match="RPC_INVALID_OPERATION"):
        validate_pdf_image_stage_start(_parameter(_start(), name, identifier))


def test_output_and_evidence_identifiers_cannot_alias() -> None:
    with pytest.raises(ProtocolError, match="RPC_INVALID_OPERATION"):
        validate_pdf_image_stage_start(_parameter(_start(), "evidence_object_id", _uuid(12)))


def test_plan_section_cannot_claim_operation_temporary_input_authority() -> None:
    payload = _start()
    forged = replace(payload.inputs[0], area=ReferenceArea.OPERATION_TEMP, operation_id=_uuid(30))
    with pytest.raises(ProtocolError, match="RPC_INVALID_OPERATION"):
        validate_pdf_image_stage_start(replace(payload, inputs=(forged, payload.inputs[1])))


def test_zero_length_source_is_rejected_before_worker_dispatch() -> None:
    payload = _start()
    empty = replace(payload.inputs[1], expected_byte_size=0)
    with pytest.raises(ProtocolError, match="RPC_INVALID_OPERATION"):
        validate_pdf_image_stage_start(replace(payload, inputs=(payload.inputs[0], empty)))


def test_exact_temporary_reservation_passes_and_one_byte_short_fails() -> None:
    payload = _start()
    validate_pdf_image_stage_start(payload)
    with pytest.raises(ProtocolError, match="RPC_INVALID_OPERATION"):
        validate_pdf_image_stage_start(
            replace(
                payload,
                requested_limits=replace(payload.requested_limits, temporary_storage_bytes=6143),
            )
        )


@pytest.mark.parametrize(
    "memory_bytes,capacity",
    ((8191, 1024), (64 * 1024 * 1024, MAX_PDF_IMAGE_STAGE_EVIDENCE_BYTES + 1)),
)
def test_evidence_cannot_exceed_memory_share_or_wire_cap(memory_bytes: int, capacity: int) -> None:
    payload = _parameter(_start(), "evidence_capacity_bytes", capacity)
    payload = replace(
        payload,
        requested_limits=replace(
            payload.requested_limits,
            memory_bytes=memory_bytes,
            temporary_storage_bytes=16 * 1024 * 1024,
        ),
    )
    with pytest.raises(ProtocolError, match="RPC_INVALID_OPERATION"):
        validate_pdf_image_stage_start(payload)


@pytest.mark.parametrize("index", (0, 1))
@pytest.mark.parametrize("field", ("expected_sha256", "expected_byte_size"))
def test_unauthenticated_input_is_rejected(index: int, field: str) -> None:
    payload = _start()
    inputs = list(payload.inputs)
    changes: dict[str, Any] = {field: None}
    inputs[index] = replace(inputs[index], **changes)
    with pytest.raises(ProtocolError):
        validate_pdf_image_stage_start(replace(payload, inputs=tuple(inputs)))


@pytest.mark.parametrize("field", ("path", "command", "env", "filename", "selected_profile"))
def test_untrusted_wire_extensions_do_not_leak_private_values(field: str) -> None:
    document = cast(dict[str, Any], json.loads(_start_wire()))
    canary = "PWS0503_PRIVATE_TRANSPORT_VALUE"
    document["payload"][field] = canary
    with pytest.raises(ProtocolError) as raised:
        decode_message(json.dumps(document).encode())
    assert canary not in str(raised.value)
    assert canary not in repr(raised.value)


def _outputs() -> tuple[CaseObjectReference, ...]:
    return (
        CaseObjectReference(
            ReferenceArea.OPERATION_STAGING,
            _uuid(12),
            _uuid(30),
            PDF_IMAGE_STAGE_OUTPUT_CONTENT_KIND,
            1500,
            "c" * 64,
        ),
        CaseObjectReference(
            ReferenceArea.OPERATION_STAGING,
            _uuid(13),
            _uuid(30),
            PDF_IMAGE_STAGE_EVIDENCE_CONTENT_KIND,
            800,
            "e" * 64,
        ),
    )


def _validate_result(
    outputs: tuple[CaseObjectReference, ...], outcome: OperationOutcome = OperationOutcome.SUCCEEDED
) -> None:
    validate_pdf_image_stage_result(
        OperationResultPayload(outcome=outcome, outputs=outputs, error=None),
        operation_id=_uuid(30),
        output_object_id=_uuid(12),
        evidence_object_id=_uuid(13),
        maximum_output_bytes=2048,
        maximum_evidence_bytes=1024,
    )


def test_exact_two_output_bindings_are_required() -> None:
    outputs = _outputs()
    _validate_result(outputs)
    for forged in ((), outputs[:1], tuple(reversed(outputs)), (*outputs, outputs[0])):
        with pytest.raises(ProtocolError, match="RPC_INVALID_RESULT"):
            _validate_result(forged)


@pytest.mark.parametrize("index", (0, 1))
@pytest.mark.parametrize(
    "field,value",
    (
        ("object_id", _uuid(90)),
        ("operation_id", _uuid(91)),
        ("content_kind", "pdf"),
        ("expected_sha256", None),
        ("expected_byte_size", 2049),
    ),
)
def test_foreign_or_oversized_output_is_rejected(index: int, field: str, value: object) -> None:
    outputs = list(_outputs())
    changes: dict[str, Any] = {field: value}
    outputs[index] = replace(outputs[index], **changes)
    with pytest.raises(ProtocolError, match="RPC_INVALID_RESULT"):
        _validate_result(tuple(outputs))


@pytest.mark.parametrize("outcome", (OperationOutcome.CANCELLED, OperationOutcome.FAILED))
def test_failed_or_cancelled_stage_cannot_publish_a_candidate(outcome: OperationOutcome) -> None:
    with pytest.raises(ProtocolError, match="RPC_INVALID_RESULT"):
        _validate_result(_outputs(), outcome)

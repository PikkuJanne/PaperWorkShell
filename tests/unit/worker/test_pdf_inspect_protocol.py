"""Focused tests for the closed ``pdf.inspect`` worker-RPC contract."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest

from paperworkshell.worker.protocol.codec import DEFAULT_OPERATION_REGISTRY
from paperworkshell.worker.protocol.errors import ProtocolError
from paperworkshell.worker.protocol.models import (
    FLATTENED_PDF_EXPORT_CAPABILITY,
    IMAGE_NORMALIZE_CAPABILITY,
    IMAGE_PDF_EXPORT_CAPABILITY,
    IMAGE_TRANSFORM_CAPABILITY,
    MAX_MESSAGE_BYTES,
    NOOP_CAPABILITY,
    OCR_PROCESS_CAPABILITY,
    PDF_EXPORT_CAPABILITY,
    PDF_INSPECT_CAPABILITY,
    PDF_THUMBNAIL_CAPABILITY,
    QUALITY_OCR_REVIEW_CAPABILITY,
    SEARCHABLE_PDF_EXPORT_CAPABILITY,
    SIGNATURE_IMAGE_RASTER_CAPABILITY,
    CaseObjectReference,
    Direction,
    MessageType,
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
    return UUID(f"00000000-0000-4000-8000-{value:012x}")


def _limits() -> ResourceLimits:
    return ResourceLimits(
        wall_time_ms=1_000,
        cpu_time_ms=500,
        memory_bytes=64 * 1024 * 1024,
        temporary_storage_bytes=16 * 1024 * 1024,
        output_bytes=64 * 1024,
        page_count=100,
        pixel_count=1,
    )


def _source() -> CaseObjectReference:
    return CaseObjectReference(
        area=ReferenceArea.SOURCE_READ,
        object_id=_uuid(20),
        operation_id=None,
        content_kind="pdf",
        expected_byte_size=1_024,
        expected_sha256="a" * 64,
    )


def _parameters() -> tuple[TaggedScalar, TaggedScalar]:
    return (
        TaggedScalar("result_handle", ScalarValueType.INTEGER, 503),
        TaggedScalar("source_handle", ScalarValueType.INTEGER, 501),
    )


def _payload(
    *,
    inputs: tuple[CaseObjectReference, ...] | None = None,
    parameters: tuple[TaggedScalar, ...] | None = None,
) -> OperationStartPayload:
    return OperationStartPayload(
        operation_kind=PDF_INSPECT_CAPABILITY.capability_id,
        operation_version=PDF_INSPECT_CAPABILITY.capability_version,
        inputs=(_source(),) if inputs is None else inputs,
        parameters=_parameters() if parameters is None else parameters,
        requested_limits=_limits(),
    )


def test_pdf_inspect_is_registered_and_round_trips_only_its_exact_contract() -> None:
    binding = PeerBinding(_uuid(1), _uuid(2), _uuid(3), SessionCapability("b" * 64))
    client = ProtocolPeer(binding, Direction.CLIENT_TO_WORKER)
    worker = ProtocolPeer(binding, Direction.WORKER_TO_CLIENT)
    hello = client.prepare_outbound(
        MessageType.SESSION_HELLO,
        SessionHelloPayload((1,), (PDF_INSPECT_CAPABILITY,)),
        request_id=_uuid(10),
        operation_id=None,
    )
    accepted_hello = worker.accept_inbound(hello.encoded)
    ready = worker.prepare_outbound(
        MessageType.SESSION_READY,
        SessionReadyPayload(
            1,
            (PDF_INSPECT_CAPABILITY,),
            MAX_MESSAGE_BYTES,
        ),
        request_id=accepted_hello.request_id,
        operation_id=None,
    )
    client.accept_inbound(ready.encoded)
    payload = _payload()
    start = client.prepare_outbound(
        MessageType.OPERATION_START,
        payload,
        request_id=_uuid(11),
        operation_id=_uuid(12),
    )

    accepted_start = worker.accept_inbound(start.encoded)

    assert DEFAULT_OPERATION_REGISTRY.capabilities == (
        NOOP_CAPABILITY,
        IMAGE_NORMALIZE_CAPABILITY,
        SIGNATURE_IMAGE_RASTER_CAPABILITY,
        IMAGE_TRANSFORM_CAPABILITY,
        OCR_PROCESS_CAPABILITY,
        QUALITY_OCR_REVIEW_CAPABILITY,
        PDF_INSPECT_CAPABILITY,
        PDF_THUMBNAIL_CAPABILITY,
        PDF_EXPORT_CAPABILITY,
        IMAGE_PDF_EXPORT_CAPABILITY,
        FLATTENED_PDF_EXPORT_CAPABILITY,
        SEARCHABLE_PDF_EXPORT_CAPABILITY,
    )
    assert accepted_start.payload == payload


@pytest.mark.parametrize(
    "payload",
    (
        _payload(inputs=()),
        _payload(inputs=(_source(), _source())),
        _payload(inputs=(replace(_source(), area=ReferenceArea.ASSET_READ),)),
        _payload(inputs=(replace(_source(), content_kind="application.pdf"),)),
        _payload(inputs=(replace(_source(), expected_byte_size=None),)),
        _payload(inputs=(replace(_source(), expected_sha256=None),)),
        _payload(parameters=(_parameters()[0],)),
        _payload(
            parameters=(
                TaggedScalar("source_handle", ScalarValueType.INTEGER, 501),
                TaggedScalar("unexpected_handle", ScalarValueType.INTEGER, 503),
            )
        ),
        _payload(
            parameters=(
                TaggedScalar("source_handle", ScalarValueType.TOKEN, "handle"),
                _parameters()[0],
            )
        ),
        _payload(
            parameters=(
                TaggedScalar("source_handle", ScalarValueType.INTEGER, 0),
                _parameters()[0],
            )
        ),
        _payload(
            parameters=(
                TaggedScalar("source_handle", ScalarValueType.INTEGER, 503),
                _parameters()[0],
            )
        ),
    ),
)
def test_pdf_inspect_rejects_every_contract_deviation(payload: OperationStartPayload) -> None:
    with pytest.raises(ProtocolError) as raised:
        DEFAULT_OPERATION_REGISTRY.validate_start(payload)

    assert raised.value.code == "RPC_INVALID_OPERATION"

"""Focused tests for ``pdf.export.flattened`` worker-RPC version 1."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest

from paperworkshell.worker.protocol.codec import DEFAULT_OPERATION_REGISTRY
from paperworkshell.worker.protocol.errors import ProtocolError
from paperworkshell.worker.protocol.models import (
    FLATTENED_PDF_EXPORT_CAPABILITY,
    FLATTENED_PDF_EXPORT_OUTPUT_CONTENT_KIND,
    FLATTENED_PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
    FLATTENED_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
    FLATTENED_PDF_EXPORT_PLAN_CONTENT_KIND,
    FLATTENED_PDF_EXPORT_PLAN_HANDLE_PARAMETER,
    FLATTENED_PDF_EXPORT_RASTER_CONTENT_KIND,
    FLATTENED_PDF_EXPORT_RESULT_HANDLE_PARAMETER,
    MAX_MESSAGE_BYTES,
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
    flattened_pdf_export_bound_object_ids,
    validate_flattened_pdf_export_result,
    validate_flattened_pdf_export_start,
)
from paperworkshell.worker.protocol.peer import PeerBinding, ProtocolPeer


def _uuid(value: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{value:012x}")


def _limits(
    *,
    temporary_storage_bytes: int = 8_192,
    output_bytes: int = 2_048,
    pixel_count: int = 4,
) -> ResourceLimits:
    return ResourceLimits(
        wall_time_ms=10_000,
        cpu_time_ms=5_000,
        memory_bytes=64 * 1024 * 1024,
        temporary_storage_bytes=temporary_storage_bytes,
        output_bytes=output_bytes,
        page_count=1,
        pixel_count=pixel_count,
    )


def _plan(operation_id: UUID | None = None) -> CaseObjectReference:
    return CaseObjectReference(
        ReferenceArea.OPERATION_TEMP,
        _uuid(20),
        _uuid(31) if operation_id is None else operation_id,
        FLATTENED_PDF_EXPORT_PLAN_CONTENT_KIND,
        512,
        "a" * 64,
    )


def _source(object_id: UUID | None = None) -> CaseObjectReference:
    return CaseObjectReference(
        ReferenceArea.SOURCE_READ,
        _uuid(21) if object_id is None else object_id,
        None,
        "pdf",
        1_024,
        "b" * 64,
    )


def _raster(operation_id: UUID | None = None) -> CaseObjectReference:
    return CaseObjectReference(
        ReferenceArea.OPERATION_TEMP,
        _uuid(22),
        _uuid(31) if operation_id is None else operation_id,
        FLATTENED_PDF_EXPORT_RASTER_CONTENT_KIND,
        16,
        "c" * 64,
    )


def _parameters() -> tuple[TaggedScalar, ...]:
    return (
        TaggedScalar(FLATTENED_PDF_EXPORT_PLAN_HANDLE_PARAMETER, ScalarValueType.INTEGER, 501),
        TaggedScalar(FLATTENED_PDF_EXPORT_OUTPUT_HANDLE_PARAMETER, ScalarValueType.INTEGER, 503),
        TaggedScalar(FLATTENED_PDF_EXPORT_RESULT_HANDLE_PARAMETER, ScalarValueType.INTEGER, 505),
        TaggedScalar(
            FLATTENED_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
            ScalarValueType.UUID,
            _uuid(23),
        ),
    )


def _payload(
    *,
    inputs: tuple[CaseObjectReference, ...] | None = None,
    parameters: tuple[TaggedScalar, ...] | None = None,
    limits: ResourceLimits | None = None,
) -> OperationStartPayload:
    return OperationStartPayload(
        FLATTENED_PDF_EXPORT_CAPABILITY.capability_id,
        FLATTENED_PDF_EXPORT_CAPABILITY.capability_version,
        (_plan(), _source(), _raster()) if inputs is None else inputs,
        _parameters() if parameters is None else parameters,
        _limits() if limits is None else limits,
    )


def _output(operation_id: UUID | None = None) -> CaseObjectReference:
    return CaseObjectReference(
        ReferenceArea.OPERATION_STAGING,
        _uuid(23),
        _uuid(31) if operation_id is None else operation_id,
        FLATTENED_PDF_EXPORT_OUTPUT_CONTENT_KIND,
        1_500,
        "d" * 64,
    )


def test_flattened_pdf_export_is_registered_and_binds_every_object() -> None:
    payload = _payload()

    DEFAULT_OPERATION_REGISTRY.validate_start(payload)

    assert FLATTENED_PDF_EXPORT_CAPABILITY in DEFAULT_OPERATION_REGISTRY.capabilities
    assert flattened_pdf_export_bound_object_ids(payload) == (
        _uuid(20),
        _uuid(21),
        _uuid(22),
        _uuid(23),
    )


@pytest.mark.parametrize(
    "payload",
    (
        _payload(inputs=(_plan(),)),
        _payload(inputs=(_plan(), _raster(), _source())),
        _payload(inputs=(_plan(), replace(_source(), content_kind="application.pdf"))),
        _payload(inputs=(_plan(), _source(), replace(_raster(), expected_byte_size=15))),
        _payload(inputs=(_plan(), _source(), replace(_raster(), expected_sha256=None))),
        _payload(inputs=(_plan(), _source(_uuid(20)))),
        _payload(parameters=_parameters()[:-1]),
        _payload(
            parameters=(
                _parameters()[0],
                replace(_parameters()[1], value=501),
                *_parameters()[2:],
            )
        ),
        _payload(limits=_limits(pixel_count=3)),
        _payload(limits=_limits(temporary_storage_bytes=3_000)),
    ),
)
def test_flattened_pdf_export_rejects_malformed_authority(
    payload: OperationStartPayload,
) -> None:
    with pytest.raises(ProtocolError):
        validate_flattened_pdf_export_start(payload)


def test_flattened_pdf_export_round_trips_scope_progress_and_result() -> None:
    binding = PeerBinding(_uuid(1), _uuid(2), _uuid(3), SessionCapability("e" * 64))
    client = ProtocolPeer(binding, Direction.CLIENT_TO_WORKER)
    worker = ProtocolPeer(binding, Direction.WORKER_TO_CLIENT)
    hello = client.prepare_outbound(
        MessageType.SESSION_HELLO,
        SessionHelloPayload((1,), (FLATTENED_PDF_EXPORT_CAPABILITY,)),
        request_id=_uuid(10),
        operation_id=None,
    )
    accepted_hello = worker.accept_inbound(hello.encoded)
    ready = worker.prepare_outbound(
        MessageType.SESSION_READY,
        SessionReadyPayload(1, (FLATTENED_PDF_EXPORT_CAPABILITY,), MAX_MESSAGE_BYTES),
        request_id=accepted_hello.request_id,
        operation_id=None,
    )
    client.accept_inbound(ready.encoded)
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
            FLATTENED_PDF_EXPORT_CAPABILITY.capability_id,
            FLATTENED_PDF_EXPORT_CAPABILITY.capability_version,
            _limits(),
        ),
        request_id=request_id,
        operation_id=operation_id,
    )
    client.accept_inbound(accepted.encoded)
    progress = worker.prepare_outbound(
        MessageType.OPERATION_PROGRESS,
        OperationProgressPayload(ProgressStage.PROCESSING, 1, 1, ProgressUnit.PAGES),
        request_id=request_id,
        operation_id=operation_id,
    )
    client.accept_inbound(progress.encoded)
    result = worker.prepare_outbound(
        MessageType.OPERATION_RESULT,
        OperationResultPayload(OperationOutcome.SUCCEEDED, (_output(),), None),
        request_id=request_id,
        operation_id=operation_id,
    )

    accepted_result = client.accept_inbound(result.encoded)
    assert type(accepted_result.payload) is OperationResultPayload
    assert accepted_result.payload.outputs == (_output(),)


def test_flattened_pdf_export_envelope_rejects_mismatched_temporary_scope() -> None:
    binding = PeerBinding(_uuid(1), _uuid(2), _uuid(3), SessionCapability("f" * 64))
    client = ProtocolPeer(binding, Direction.CLIENT_TO_WORKER)

    with pytest.raises(ProtocolError):
        client.prepare_outbound(
            MessageType.OPERATION_START,
            _payload(inputs=(_plan(), _source(), _raster(_uuid(32)))),
            request_id=_uuid(30),
            operation_id=_uuid(31),
        )


def test_flattened_pdf_export_result_requires_one_exact_staging_pdf() -> None:
    validate_flattened_pdf_export_result(
        OperationResultPayload(OperationOutcome.SUCCEEDED, (_output(),), None),
        operation_id=_uuid(31),
        output_object_id=_uuid(23),
        maximum_output_bytes=2_048,
    )

    with pytest.raises(ProtocolError):
        validate_flattened_pdf_export_result(
            OperationResultPayload(
                OperationOutcome.SUCCEEDED,
                (replace(_output(), content_kind="pdf"),),
                None,
            ),
            operation_id=_uuid(31),
            output_object_id=_uuid(23),
            maximum_output_bytes=2_048,
        )
    with pytest.raises(ProtocolError):
        validate_flattened_pdf_export_result(
            OperationResultPayload(OperationOutcome.FAILED, (_output(),), None),
            operation_id=_uuid(31),
            output_object_id=_uuid(23),
            maximum_output_bytes=2_048,
        )

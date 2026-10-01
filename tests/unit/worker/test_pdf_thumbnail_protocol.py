"""Focused tests for the closed ``pdf.thumbnail`` worker-RPC contract."""

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
    MAX_SAFE_INTEGER,
    NOOP_CAPABILITY,
    OCR_PROCESS_CAPABILITY,
    PDF_EXPORT_CAPABILITY,
    PDF_IMAGE_STAGE_CAPABILITY,
    PDF_INSPECT_CAPABILITY,
    PDF_THUMBNAIL_ADDITIONAL_ROTATION_PARAMETER,
    PDF_THUMBNAIL_CACHE_HANDLE_PARAMETER,
    PDF_THUMBNAIL_CACHE_OBJECT_ID_PARAMETER,
    PDF_THUMBNAIL_CAPABILITY,
    PDF_THUMBNAIL_CONTENT_KIND,
    PDF_THUMBNAIL_MAXIMUM_HEIGHT_PARAMETER,
    PDF_THUMBNAIL_MAXIMUM_WIDTH_PARAMETER,
    PDF_THUMBNAIL_PAGE_BOX_PARAMETER,
    PDF_THUMBNAIL_PAGE_ID_PARAMETER,
    PDF_THUMBNAIL_SOURCE_HANDLE_PARAMETER,
    PDF_THUMBNAIL_SOURCE_PAGE_INDEX_PARAMETER,
    QUALITY_OCR_REVIEW_CAPABILITY,
    SEARCHABLE_PDF_EXPORT_CAPABILITY,
    SIGNATURE_IMAGE_RASTER_CAPABILITY,
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
    validate_pdf_thumbnail_result,
)
from paperworkshell.worker.protocol.peer import PeerBinding, ProtocolPeer


def _uuid(value: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{value:012x}")


def _limits(
    *,
    output_bytes: int = 2_048,
    pixel_count: int = 10_000,
) -> ResourceLimits:
    return ResourceLimits(
        wall_time_ms=1_000,
        cpu_time_ms=500,
        memory_bytes=64 * 1024 * 1024,
        temporary_storage_bytes=16 * 1024 * 1024,
        output_bytes=output_bytes,
        page_count=1,
        pixel_count=pixel_count,
    )


def _source(*, object_id: UUID | None = None) -> CaseObjectReference:
    return CaseObjectReference(
        area=ReferenceArea.SOURCE_READ,
        object_id=_uuid(20) if object_id is None else object_id,
        operation_id=None,
        content_kind="pdf",
        expected_byte_size=1_024,
        expected_sha256="a" * 64,
    )


def _parameters() -> tuple[TaggedScalar, ...]:
    return (
        TaggedScalar(PDF_THUMBNAIL_SOURCE_HANDLE_PARAMETER, ScalarValueType.INTEGER, 501),
        TaggedScalar(PDF_THUMBNAIL_CACHE_HANDLE_PARAMETER, ScalarValueType.INTEGER, 503),
        TaggedScalar(PDF_THUMBNAIL_CACHE_OBJECT_ID_PARAMETER, ScalarValueType.UUID, _uuid(22)),
        TaggedScalar(PDF_THUMBNAIL_PAGE_ID_PARAMETER, ScalarValueType.UUID, _uuid(21)),
        TaggedScalar(PDF_THUMBNAIL_SOURCE_PAGE_INDEX_PARAMETER, ScalarValueType.INTEGER, 0),
        TaggedScalar(PDF_THUMBNAIL_MAXIMUM_WIDTH_PARAMETER, ScalarValueType.INTEGER, 100),
        TaggedScalar(PDF_THUMBNAIL_MAXIMUM_HEIGHT_PARAMETER, ScalarValueType.INTEGER, 100),
        TaggedScalar(PDF_THUMBNAIL_PAGE_BOX_PARAMETER, ScalarValueType.TOKEN, "crop"),
        TaggedScalar(PDF_THUMBNAIL_ADDITIONAL_ROTATION_PARAMETER, ScalarValueType.INTEGER, 0),
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
        operation_kind=PDF_THUMBNAIL_CAPABILITY.capability_id,
        operation_version=PDF_THUMBNAIL_CAPABILITY.capability_version,
        inputs=(_source(),) if inputs is None else inputs,
        parameters=_parameters() if parameters is None else parameters,
        requested_limits=_limits() if limits is None else limits,
    )


def _output(
    *,
    object_id: UUID | None = None,
    operation_id: UUID | None = None,
    area: ReferenceArea = ReferenceArea.OPERATION_STAGING,
    content_kind: str = PDF_THUMBNAIL_CONTENT_KIND,
    byte_size: int | None = 1_024,
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
    binding = PeerBinding(_uuid(1), _uuid(2), _uuid(3), SessionCapability("c" * 64))
    client = ProtocolPeer(binding, Direction.CLIENT_TO_WORKER)
    worker = ProtocolPeer(binding, Direction.WORKER_TO_CLIENT)
    hello = client.prepare_outbound(
        MessageType.SESSION_HELLO,
        SessionHelloPayload((1,), (PDF_THUMBNAIL_CAPABILITY,)),
        request_id=_uuid(10),
        operation_id=None,
    )
    accepted_hello = worker.accept_inbound(hello.encoded)
    ready = worker.prepare_outbound(
        MessageType.SESSION_READY,
        SessionReadyPayload(1, (PDF_THUMBNAIL_CAPABILITY,), MAX_MESSAGE_BYTES),
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
            PDF_THUMBNAIL_CAPABILITY.capability_id,
            PDF_THUMBNAIL_CAPABILITY.capability_version,
            _limits(),
        ),
        request_id=request_id,
        operation_id=operation_id,
    )
    client.accept_inbound(accepted.encoded)
    return client, worker, request_id, operation_id


def test_pdf_thumbnail_is_registered_and_round_trips_its_exact_contract() -> None:
    client, worker = _negotiated_peers()
    payload = _payload()
    start = client.prepare_outbound(
        MessageType.OPERATION_START,
        payload,
        request_id=_uuid(30),
        operation_id=_uuid(31),
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
        PDF_IMAGE_STAGE_CAPABILITY,
    )
    assert accepted_start.payload == payload


@pytest.mark.parametrize(
    "payload",
    (
        _payload(inputs=()),
        _payload(inputs=(_source(), _source(object_id=_uuid(25)))),
        _payload(inputs=(replace(_source(), area=ReferenceArea.ASSET_READ),)),
        _payload(inputs=(replace(_source(), content_kind="image"),)),
        _payload(inputs=(replace(_source(), expected_byte_size=None),)),
        _payload(inputs=(replace(_source(), expected_sha256=None),)),
        _payload(parameters=_parameters()[:-1]),
        _payload(
            parameters=(*_parameters(), TaggedScalar("unexpected", ScalarValueType.INTEGER, 1))
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(PDF_THUMBNAIL_SOURCE_HANDLE_PARAMETER, ScalarValueType.INTEGER, 0)
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(PDF_THUMBNAIL_SOURCE_HANDLE_PARAMETER, ScalarValueType.TOKEN, "one")
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(PDF_THUMBNAIL_CACHE_HANDLE_PARAMETER, ScalarValueType.INTEGER, 501)
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(
                    PDF_THUMBNAIL_CACHE_OBJECT_ID_PARAMETER,
                    ScalarValueType.TOKEN,
                    "cache",
                )
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(PDF_THUMBNAIL_PAGE_ID_PARAMETER, ScalarValueType.TOKEN, "page")
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(PDF_THUMBNAIL_PAGE_ID_PARAMETER, ScalarValueType.UUID, _uuid(20))
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(
                    PDF_THUMBNAIL_CACHE_OBJECT_ID_PARAMETER, ScalarValueType.UUID, _uuid(21)
                )
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(
                    PDF_THUMBNAIL_SOURCE_PAGE_INDEX_PARAMETER,
                    ScalarValueType.INTEGER,
                    -1,
                )
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(PDF_THUMBNAIL_MAXIMUM_WIDTH_PARAMETER, ScalarValueType.INTEGER, 0)
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(PDF_THUMBNAIL_MAXIMUM_HEIGHT_PARAMETER, ScalarValueType.INTEGER, 0)
            )
        ),
        _payload(limits=_limits(pixel_count=9_999)),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(
                    PDF_THUMBNAIL_MAXIMUM_WIDTH_PARAMETER,
                    ScalarValueType.INTEGER,
                    MAX_SAFE_INTEGER,
                )
            ),
            limits=_limits(pixel_count=MAX_SAFE_INTEGER),
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(PDF_THUMBNAIL_PAGE_BOX_PARAMETER, ScalarValueType.TOKEN, "bleed")
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(
                    PDF_THUMBNAIL_ADDITIONAL_ROTATION_PARAMETER,
                    ScalarValueType.INTEGER,
                    45,
                )
            )
        ),
    ),
)
def test_pdf_thumbnail_rejects_every_start_contract_deviation(
    payload: OperationStartPayload,
) -> None:
    with pytest.raises(ProtocolError) as raised:
        DEFAULT_OPERATION_REGISTRY.validate_start(payload)

    assert raised.value.code == "RPC_INVALID_OPERATION"


@pytest.mark.parametrize("aliased_object", ("source", "page", "cache"))
def test_pdf_thumbnail_rejects_an_operation_id_that_aliases_an_object(
    aliased_object: str,
) -> None:
    operation_id = _uuid(31)
    inputs = (_source(object_id=operation_id) if aliased_object == "source" else _source(),)
    parameters = _parameters()
    if aliased_object == "page":
        parameters = _replace_parameter(
            TaggedScalar(PDF_THUMBNAIL_PAGE_ID_PARAMETER, ScalarValueType.UUID, operation_id)
        )
    elif aliased_object == "cache":
        parameters = _replace_parameter(
            TaggedScalar(
                PDF_THUMBNAIL_CACHE_OBJECT_ID_PARAMETER,
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


def test_pdf_thumbnail_success_is_bound_to_one_verified_staging_output() -> None:
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
        (_output(content_kind="pdf-thumbnail-rgb"),),
        (_output(byte_size=0),),
        (_output(byte_size=2_049),),
        (_output(byte_size=None),),
        (_output(digest=None),),
    ),
)
def test_pdf_thumbnail_rejects_every_success_output_deviation(
    outputs: tuple[CaseObjectReference, ...],
) -> None:
    with pytest.raises(ProtocolError) as raised:
        validate_pdf_thumbnail_result(
            OperationResultPayload(OperationOutcome.SUCCEEDED, outputs, None),
            operation_id=_uuid(31),
            cache_object_id=_uuid(22),
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
def test_pdf_thumbnail_unsuccessful_results_are_output_free(
    payload: OperationResultPayload,
) -> None:
    validate_pdf_thumbnail_result(
        payload,
        operation_id=_uuid(31),
        cache_object_id=_uuid(22),
        maximum_output_bytes=2_048,
    )


def test_pdf_thumbnail_result_rejects_cache_and_operation_identity_aliasing() -> None:
    with pytest.raises(ProtocolError) as raised:
        validate_pdf_thumbnail_result(
            OperationResultPayload(OperationOutcome.SUCCEEDED, (_output(),), None),
            operation_id=_uuid(31),
            cache_object_id=_uuid(31),
            maximum_output_bytes=2_048,
        )

    assert raised.value.code == "RPC_INVALID_RESULT"

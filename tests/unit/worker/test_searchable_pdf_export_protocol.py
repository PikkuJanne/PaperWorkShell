"""Focused tests for ``pdf.export.searchable`` worker-RPC version 1."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest

from paperworkshell.worker.protocol.codec import DEFAULT_OPERATION_REGISTRY
from paperworkshell.worker.protocol.errors import ProtocolError
from paperworkshell.worker.protocol.models import (
    MAX_MESSAGE_BYTES,
    MAX_SEARCHABLE_PDF_EXPORT_STATUS_BYTES,
    SEARCHABLE_PDF_EXPORT_CAPABILITY,
    SEARCHABLE_PDF_EXPORT_OUTPUT_CONTENT_KIND,
    SEARCHABLE_PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
    SEARCHABLE_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
    SEARCHABLE_PDF_EXPORT_PLAN_CONTENT_KIND,
    SEARCHABLE_PDF_EXPORT_PLAN_HANDLE_PARAMETER,
    SEARCHABLE_PDF_EXPORT_RESULT_HANDLE_PARAMETER,
    SEARCHABLE_PDF_EXPORT_STATUS_CAPACITY_PARAMETER,
    SEARCHABLE_PDF_EXPORT_STATUS_CONTENT_KIND,
    SEARCHABLE_PDF_EXPORT_STATUS_HANDLE_PARAMETER,
    SEARCHABLE_PDF_EXPORT_STATUS_OBJECT_ID_PARAMETER,
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
    searchable_pdf_export_bound_object_ids,
    searchable_pdf_export_output_object_ids,
    searchable_pdf_export_status_capacity_bytes,
    validate_searchable_pdf_export_result,
)
from paperworkshell.worker.protocol.peer import PeerBinding, ProtocolPeer


def _uuid(value: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{value:012x}")


PLAN_ID = _uuid(20)
OUTPUT_ID = _uuid(22)
STATUS_ID = _uuid(24)
OPERATION_ID = _uuid(31)


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


def _plan(
    *,
    object_id: UUID = PLAN_ID,
    operation_id: UUID | None = None,
) -> CaseObjectReference:
    return CaseObjectReference(
        area=ReferenceArea.SOURCE_READ,
        object_id=object_id,
        operation_id=operation_id,
        content_kind=SEARCHABLE_PDF_EXPORT_PLAN_CONTENT_KIND,
        expected_byte_size=512,
        expected_sha256="a" * 64,
    )


def _source(
    object_id: UUID,
    *,
    content_kind: str,
    byte_size: int = 1_024,
) -> CaseObjectReference:
    return CaseObjectReference(
        area=ReferenceArea.SOURCE_READ,
        object_id=object_id,
        operation_id=None,
        content_kind=content_kind,
        expected_byte_size=byte_size,
        expected_sha256="b" * 64,
    )


def _parameters(
    *,
    output_object_id: UUID = OUTPUT_ID,
    status_object_id: UUID = STATUS_ID,
    status_capacity: int = 1_024,
) -> tuple[TaggedScalar, ...]:
    return (
        TaggedScalar(SEARCHABLE_PDF_EXPORT_PLAN_HANDLE_PARAMETER, ScalarValueType.INTEGER, 501),
        TaggedScalar(
            SEARCHABLE_PDF_EXPORT_OUTPUT_HANDLE_PARAMETER,
            ScalarValueType.INTEGER,
            503,
        ),
        TaggedScalar(
            SEARCHABLE_PDF_EXPORT_STATUS_HANDLE_PARAMETER,
            ScalarValueType.INTEGER,
            504,
        ),
        TaggedScalar(
            SEARCHABLE_PDF_EXPORT_RESULT_HANDLE_PARAMETER,
            ScalarValueType.INTEGER,
            505,
        ),
        TaggedScalar(
            SEARCHABLE_PDF_EXPORT_OUTPUT_OBJECT_ID_PARAMETER,
            ScalarValueType.UUID,
            output_object_id,
        ),
        TaggedScalar(
            SEARCHABLE_PDF_EXPORT_STATUS_OBJECT_ID_PARAMETER,
            ScalarValueType.UUID,
            status_object_id,
        ),
        TaggedScalar(
            SEARCHABLE_PDF_EXPORT_STATUS_CAPACITY_PARAMETER,
            ScalarValueType.INTEGER,
            status_capacity,
        ),
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
        SEARCHABLE_PDF_EXPORT_CAPABILITY.capability_id,
        SEARCHABLE_PDF_EXPORT_CAPABILITY.capability_version,
        (
            _plan(),
            _source(_uuid(21), content_kind="pdf"),
            _source(_uuid(23), content_kind="png", byte_size=256),
        )
        if inputs is None
        else inputs,
        _parameters() if parameters is None else parameters,
        _limits() if limits is None else limits,
    )


def _output(
    *,
    object_id: UUID = OUTPUT_ID,
    operation_id: UUID = OPERATION_ID,
    area: ReferenceArea = ReferenceArea.OPERATION_STAGING,
    content_kind: str = SEARCHABLE_PDF_EXPORT_OUTPUT_CONTENT_KIND,
    byte_size: int | None = 1_500,
    digest: str | None = "c" * 64,
) -> CaseObjectReference:
    return CaseObjectReference(
        area=area,
        object_id=object_id,
        operation_id=operation_id,
        content_kind=content_kind,
        expected_byte_size=byte_size,
        expected_sha256=digest,
    )


def _status(
    *,
    object_id: UUID = STATUS_ID,
    operation_id: UUID = OPERATION_ID,
    area: ReferenceArea = ReferenceArea.OPERATION_STAGING,
    content_kind: str = SEARCHABLE_PDF_EXPORT_STATUS_CONTENT_KIND,
    byte_size: int | None = 512,
    digest: str | None = "d" * 64,
) -> CaseObjectReference:
    return CaseObjectReference(
        area=area,
        object_id=object_id,
        operation_id=operation_id,
        content_kind=content_kind,
        expected_byte_size=byte_size,
        expected_sha256=digest,
    )


def _negotiated_peers() -> tuple[ProtocolPeer, ProtocolPeer]:
    binding = PeerBinding(_uuid(1), _uuid(2), _uuid(3), SessionCapability("e" * 64))
    client = ProtocolPeer(binding, Direction.CLIENT_TO_WORKER)
    worker = ProtocolPeer(binding, Direction.WORKER_TO_CLIENT)
    hello = client.prepare_outbound(
        MessageType.SESSION_HELLO,
        SessionHelloPayload((1,), (SEARCHABLE_PDF_EXPORT_CAPABILITY,)),
        request_id=_uuid(10),
        operation_id=None,
    )
    accepted_hello = worker.accept_inbound(hello.encoded)
    ready = worker.prepare_outbound(
        MessageType.SESSION_READY,
        SessionReadyPayload(1, (SEARCHABLE_PDF_EXPORT_CAPABILITY,), MAX_MESSAGE_BYTES),
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
            SEARCHABLE_PDF_EXPORT_CAPABILITY.capability_id,
            SEARCHABLE_PDF_EXPORT_CAPABILITY.capability_version,
            _limits(),
        ),
        request_id=request_id,
        operation_id=operation_id,
    )
    client.accept_inbound(accepted.encoded)
    return client, worker, request_id, operation_id


def test_searchable_export_is_registered_and_round_trips_exact_mixed_contract() -> None:
    client, worker = _negotiated_peers()
    payload = _payload()
    start = client.prepare_outbound(
        MessageType.OPERATION_START,
        payload,
        request_id=_uuid(30),
        operation_id=_uuid(31),
    )

    assert worker.accept_inbound(start.encoded).payload == payload
    assert SEARCHABLE_PDF_EXPORT_CAPABILITY in DEFAULT_OPERATION_REGISTRY.capabilities
    assert searchable_pdf_export_bound_object_ids(payload) == (
        _uuid(20),
        _uuid(21),
        _uuid(23),
        _uuid(22),
        _uuid(24),
    )
    assert searchable_pdf_export_output_object_ids(payload) == (_uuid(22), _uuid(24))
    assert searchable_pdf_export_status_capacity_bytes(payload) == 1_024
    assert all(value not in repr(payload) for value in ("501", "503", "504", "505"))


@pytest.mark.parametrize(
    "payload",
    (
        _payload(inputs=()),
        _payload(inputs=(_plan(),)),
        _payload(inputs=(_source(_uuid(21), content_kind="pdf"), _plan())),
        _payload(
            inputs=(
                replace(
                    _plan(),
                    area=ReferenceArea.OPERATION_TEMP,
                    operation_id=OPERATION_ID,
                ),
                _source(_uuid(21), content_kind="pdf"),
            )
        ),
        _payload(
            inputs=(replace(_plan(), content_kind="plan"), _source(_uuid(21), content_kind="pdf"))
        ),
        _payload(
            inputs=(replace(_plan(), expected_byte_size=0), _source(_uuid(21), content_kind="pdf"))
        ),
        _payload(
            inputs=(replace(_plan(), expected_sha256=None), _source(_uuid(21), content_kind="pdf"))
        ),
        _payload(
            inputs=(
                _plan(),
                replace(_source(_uuid(21), content_kind="pdf"), area=ReferenceArea.ASSET_READ),
            )
        ),
        _payload(inputs=(_plan(), _source(_uuid(21), content_kind="tiff"))),
        _payload(
            inputs=(
                _plan(),
                replace(_source(_uuid(21), content_kind="pdf"), expected_byte_size=None),
            )
        ),
        _payload(
            inputs=(_plan(), replace(_source(_uuid(21), content_kind="pdf"), expected_sha256=None))
        ),
        _payload(inputs=(_plan(), _source(_uuid(20), content_kind="pdf"))),
        _payload(parameters=_parameters()[:-1]),
        _payload(parameters=(*_parameters(), TaggedScalar("extra", ScalarValueType.INTEGER, 1))),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(
                    SEARCHABLE_PDF_EXPORT_PLAN_HANDLE_PARAMETER, ScalarValueType.INTEGER, 0
                )
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(
                    SEARCHABLE_PDF_EXPORT_STATUS_HANDLE_PARAMETER, ScalarValueType.INTEGER, 501
                )
            )
        ),
        _payload(
            parameters=_replace_parameter(
                TaggedScalar(
                    SEARCHABLE_PDF_EXPORT_RESULT_HANDLE_PARAMETER, ScalarValueType.TOKEN, "pipe"
                )
            )
        ),
        _payload(parameters=_parameters(output_object_id=_uuid(24))),
        _payload(parameters=_parameters(output_object_id=_uuid(21))),
        _payload(parameters=_parameters(status_capacity=0)),
        _payload(
            parameters=_parameters(status_capacity=MAX_SEARCHABLE_PDF_EXPORT_STATUS_BYTES + 1)
        ),
        _payload(limits=_limits(page_count=4_097)),
        _payload(limits=_limits(temporary_storage_bytes=3_327)),
    ),
)
def test_searchable_export_rejects_every_start_contract_deviation(
    payload: OperationStartPayload,
) -> None:
    with pytest.raises(ProtocolError) as raised:
        DEFAULT_OPERATION_REGISTRY.validate_start(payload)

    assert raised.value.code == "RPC_INVALID_OPERATION"


def test_searchable_export_correlates_plan_case_operation_and_all_object_ids() -> None:
    client, _worker = _negotiated_peers()
    with pytest.raises(ProtocolError) as scope_error:
        client.prepare_outbound(
            MessageType.OPERATION_START,
            _payload(
                inputs=(
                    replace(
                        _plan(),
                        area=ReferenceArea.OPERATION_TEMP,
                        operation_id=_uuid(99),
                    ),
                    _source(_uuid(21), content_kind="pdf"),
                )
            ),
            request_id=_uuid(30),
            operation_id=_uuid(31),
        )
    assert scope_error.value.code == "RPC_REFERENCE_SCOPE"

    for aliased_id in (_uuid(3), _uuid(20), _uuid(21), _uuid(22), _uuid(24)):
        client, _worker = _negotiated_peers()
        plan = _plan()
        parameters = _parameters()
        if aliased_id == _uuid(3):
            parameters = _parameters(output_object_id=aliased_id)
            operation_id = _uuid(31)
        else:
            operation_id = aliased_id
        with pytest.raises(ProtocolError) as alias_error:
            client.prepare_outbound(
                MessageType.OPERATION_START,
                _payload(
                    inputs=(plan, _source(_uuid(21), content_kind="pdf")),
                    parameters=parameters,
                ),
                request_id=_uuid(40 + aliased_id.int % 10),
                operation_id=operation_id,
            )
        assert alias_error.value.code == "RPC_INVALID_OPERATION"


@pytest.mark.parametrize(
    "progress",
    (
        OperationProgressPayload(ProgressStage.PROCESSING, 0, 2, ProgressUnit.STEPS),
        OperationProgressPayload(ProgressStage.PROCESSING, 0, None, ProgressUnit.PAGES),
        OperationProgressPayload(ProgressStage.PROCESSING, 0, 1, ProgressUnit.PAGES),
    ),
)
def test_searchable_export_progress_requires_exact_page_units(
    progress: OperationProgressPayload,
) -> None:
    client, worker, request_id, operation_id = _started_peers()
    with pytest.raises(ProtocolError) as raised:
        worker.prepare_outbound(
            MessageType.OPERATION_PROGRESS,
            progress,
            request_id=request_id,
            operation_id=operation_id,
        )

    assert raised.value.code == "RPC_INVALID_PROGRESS"
    assert client.state.active_operation_id == operation_id


def test_searchable_export_success_requires_pdf_then_status_staging_outputs() -> None:
    client, worker, request_id, operation_id = _started_peers()
    for completed in (0, 2):
        progress = worker.prepare_outbound(
            MessageType.OPERATION_PROGRESS,
            OperationProgressPayload(
                ProgressStage.PROCESSING,
                completed,
                2,
                ProgressUnit.PAGES,
            ),
            request_id=request_id,
            operation_id=operation_id,
        )
        client.accept_inbound(progress.encoded)
    terminal = worker.prepare_outbound(
        MessageType.OPERATION_RESULT,
        OperationResultPayload(OperationOutcome.SUCCEEDED, (_output(), _status()), None),
        request_id=request_id,
        operation_id=operation_id,
    )

    accepted = client.accept_inbound(terminal.encoded)

    assert accepted.payload == OperationResultPayload(
        OperationOutcome.SUCCEEDED,
        (_output(), _status()),
        None,
    )
    assert client.state.active_operation_id is None
    assert worker.state.active_operation_id is None


@pytest.mark.parametrize(
    "outputs",
    (
        (),
        (_output(),),
        (_status(), _output()),
        (_output(object_id=_uuid(99)), _status()),
        (_output(operation_id=_uuid(99)), _status()),
        (_output(area=ReferenceArea.OPERATION_CACHE), _status()),
        (_output(content_kind="pdf"), _status()),
        (_output(byte_size=0), _status()),
        (_output(byte_size=2_049), _status()),
        (_output(digest=None), _status()),
        (_output(), _status(object_id=_uuid(99))),
        (_output(), _status(operation_id=_uuid(99))),
        (_output(), _status(area=ReferenceArea.OPERATION_CACHE)),
        (_output(), _status(content_kind="status")),
        (_output(), _status(byte_size=0)),
        (_output(), _status(byte_size=1_025)),
        (_output(), _status(digest=None)),
    ),
)
def test_searchable_export_rejects_every_success_output_deviation(
    outputs: tuple[CaseObjectReference, ...],
) -> None:
    with pytest.raises(ProtocolError) as raised:
        validate_searchable_pdf_export_result(
            OperationResultPayload(OperationOutcome.SUCCEEDED, outputs, None),
            operation_id=_uuid(31),
            output_object_id=_uuid(22),
            status_object_id=_uuid(24),
            maximum_output_bytes=2_048,
            maximum_status_bytes=1_024,
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
def test_searchable_export_unsuccessful_results_are_output_free(
    payload: OperationResultPayload,
) -> None:
    validate_searchable_pdf_export_result(
        payload,
        operation_id=_uuid(31),
        output_object_id=_uuid(22),
        status_object_id=_uuid(24),
        maximum_output_bytes=2_048,
        maximum_status_bytes=1_024,
    )

from __future__ import annotations

import json
import unittest
from pathlib import Path
from uuid import UUID

from paperworkshell.worker.protocol.codec import decode_message, encode_message
from paperworkshell.worker.protocol.errors import ProtocolError
from paperworkshell.worker.protocol.models import (
    MAX_MESSAGE_BYTES,
    MESSAGE_VERSION,
    NOOP_CAPABILITY,
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    CancelDisposition,
    CancelReason,
    CapabilityDescriptor,
    CaseObjectReference,
    ClosedReason,
    Direction,
    ErrorCategory,
    ErrorCode,
    ErrorPayload,
    MessagePayload,
    MessageType,
    OperationAcceptedPayload,
    OperationCancelAckPayload,
    OperationCancelPayload,
    OperationOutcome,
    OperationProgressPayload,
    OperationResultPayload,
    OperationStartPayload,
    ProgressStage,
    ProgressUnit,
    ProtocolEnvelope,
    ProtocolErrorPayload,
    ReferenceArea,
    ResourceLimits,
    RetryAction,
    SessionCapability,
    SessionClosedPayload,
    SessionHelloPayload,
    SessionReadyPayload,
    SessionShutdownPayload,
    ShutdownReason,
    WorkerFaultPayload,
)


def _uuid(number: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{number:012x}")


CAPABILITY = SessionCapability("a" * 64)
SESSION_ID = _uuid(1)
WORKER_ID = _uuid(2)
CASE_ID = _uuid(3)
REQUEST_ID = _uuid(4)
OPERATION_ID = _uuid(5)


def _limits() -> ResourceLimits:
    return ResourceLimits(
        wall_time_ms=10_000,
        cpu_time_ms=5_000,
        memory_bytes=64 * 1024 * 1024,
        temporary_storage_bytes=32 * 1024 * 1024,
        output_bytes=16 * 1024 * 1024,
        page_count=100,
        pixel_count=100_000_000,
    )


def _error(
    *,
    category: ErrorCategory = ErrorCategory.OPERATION,
    retry_action: RetryAction = RetryAction.NEVER,
    code: ErrorCode = ErrorCode.WORKER_OPERATION_FAILED,
) -> ErrorPayload:
    return ErrorPayload(
        code=code,
        category=category,
        retry_action=retry_action,
        restart_required=retry_action is RetryAction.RESTART_WORKER,
        parameters=(),
    )


def _output_reference() -> CaseObjectReference:
    return CaseObjectReference(
        area=ReferenceArea.OPERATION_STAGING,
        object_id=_uuid(6),
        operation_id=OPERATION_ID,
        content_kind="application.pdf",
        expected_byte_size=128,
        expected_sha256="b" * 64,
    )


def _envelope(
    message_type: MessageType,
    direction: Direction,
    payload: MessagePayload,
    *,
    message_number: int,
    request_id: UUID | None = REQUEST_ID,
    operation_id: UUID | None = None,
) -> ProtocolEnvelope:
    return ProtocolEnvelope(
        protocol=PROTOCOL_NAME,
        protocol_version=PROTOCOL_VERSION,
        message_version=MESSAGE_VERSION,
        message_type=message_type,
        direction=direction,
        session_id=SESSION_ID,
        worker_instance_id=WORKER_ID,
        session_capability=CAPABILITY,
        case_id=CASE_ID,
        message_id=_uuid(100 + message_number),
        sender_sequence=message_number,
        request_id=request_id,
        operation_id=operation_id,
        payload=payload,
    )


def _messages() -> tuple[ProtocolEnvelope, ...]:
    failed = _error()
    timed_out = _error(
        category=ErrorCategory.RESOURCE_LIMIT,
        retry_action=RetryAction.RETRY,
        code=ErrorCode.WORKER_OPERATION_TIMED_OUT,
    )
    return (
        _envelope(
            MessageType.SESSION_HELLO,
            Direction.CLIENT_TO_WORKER,
            SessionHelloPayload((1,), (NOOP_CAPABILITY,)),
            message_number=0,
        ),
        _envelope(
            MessageType.SESSION_READY,
            Direction.WORKER_TO_CLIENT,
            SessionReadyPayload(1, (NOOP_CAPABILITY,), MAX_MESSAGE_BYTES),
            message_number=1,
        ),
        _envelope(
            MessageType.OPERATION_START,
            Direction.CLIENT_TO_WORKER,
            OperationStartPayload("protocol.noop", 1, (), (), _limits()),
            message_number=2,
            operation_id=OPERATION_ID,
        ),
        _envelope(
            MessageType.OPERATION_ACCEPTED,
            Direction.WORKER_TO_CLIENT,
            OperationAcceptedPayload("protocol.noop", 1, _limits()),
            message_number=3,
            operation_id=OPERATION_ID,
        ),
        _envelope(
            MessageType.OPERATION_PROGRESS,
            Direction.WORKER_TO_CLIENT,
            OperationProgressPayload(ProgressStage.PROCESSING, 1, 2, ProgressUnit.STEPS),
            message_number=4,
            operation_id=OPERATION_ID,
        ),
        _envelope(
            MessageType.OPERATION_CANCEL,
            Direction.CLIENT_TO_WORKER,
            OperationCancelPayload(CancelReason.USER_REQUESTED),
            message_number=5,
            operation_id=OPERATION_ID,
        ),
        _envelope(
            MessageType.OPERATION_CANCEL_ACK,
            Direction.WORKER_TO_CLIENT,
            OperationCancelAckPayload(CancelDisposition.REQUESTED),
            message_number=6,
            operation_id=OPERATION_ID,
        ),
        _envelope(
            MessageType.OPERATION_RESULT,
            Direction.WORKER_TO_CLIENT,
            OperationResultPayload(OperationOutcome.SUCCEEDED, (_output_reference(),), None),
            message_number=7,
            operation_id=OPERATION_ID,
        ),
        _envelope(
            MessageType.OPERATION_RESULT,
            Direction.WORKER_TO_CLIENT,
            OperationResultPayload(OperationOutcome.CANCELLED, (), None),
            message_number=8,
            operation_id=OPERATION_ID,
        ),
        _envelope(
            MessageType.OPERATION_RESULT,
            Direction.WORKER_TO_CLIENT,
            OperationResultPayload(OperationOutcome.FAILED, (), failed),
            message_number=9,
            operation_id=OPERATION_ID,
        ),
        _envelope(
            MessageType.OPERATION_RESULT,
            Direction.WORKER_TO_CLIENT,
            OperationResultPayload(OperationOutcome.TIMED_OUT, (), timed_out),
            message_number=10,
            operation_id=OPERATION_ID,
        ),
        _envelope(
            MessageType.SESSION_SHUTDOWN,
            Direction.CLIENT_TO_WORKER,
            SessionShutdownPayload(ShutdownReason.APPLICATION_EXIT),
            message_number=11,
        ),
        _envelope(
            MessageType.SESSION_CLOSED,
            Direction.WORKER_TO_CLIENT,
            SessionClosedPayload(ClosedReason.SHUTDOWN_COMPLETE),
            message_number=12,
        ),
        _envelope(
            MessageType.PROTOCOL_ERROR,
            Direction.CLIENT_TO_WORKER,
            ProtocolErrorPayload(
                _error(
                    category=ErrorCategory.PROTOCOL,
                    retry_action=RetryAction.RESTART_WORKER,
                    code=ErrorCode.RPC_MESSAGE_INVALID,
                )
            ),
            message_number=13,
            operation_id=OPERATION_ID,
        ),
        _envelope(
            MessageType.WORKER_FAULT,
            Direction.WORKER_TO_CLIENT,
            WorkerFaultPayload(
                _error(
                    category=ErrorCategory.INTERNAL,
                    retry_action=RetryAction.RESTART_WORKER,
                    code=ErrorCode.WORKER_INTERNAL_FAULT,
                ),
                (OPERATION_ID,),
            ),
            message_number=14,
            operation_id=None,
        ),
    )


class WorkerProtocolCodecTests(unittest.TestCase):
    def test_checked_in_schema_examples_decode_and_round_trip_without_drift(self) -> None:
        examples = Path("schemas/examples/worker-rpc/v1")
        paths = tuple(sorted(examples.glob("*.json")))
        observed_types: set[MessageType] = set()
        observed_outcomes: set[OperationOutcome] = set()

        self.assertEqual(15, len(paths))
        for path in paths:
            with self.subTest(path=path.name):
                decoded = decode_message(path.read_bytes())
                round_tripped = decode_message(encode_message(decoded))
                self.assertEqual(decoded, round_tripped)
                observed_types.add(decoded.message_type)
                if isinstance(decoded.payload, OperationResultPayload):
                    observed_outcomes.add(decoded.payload.outcome)

        self.assertEqual(set(MessageType), observed_types)
        self.assertEqual(set(OperationOutcome), observed_outcomes)

    def test_every_message_type_and_result_outcome_round_trips_canonically(self) -> None:
        observed_types: set[MessageType] = set()
        observed_outcomes: set[OperationOutcome] = set()
        for message in _messages():
            with self.subTest(message_type=message.message_type, payload=type(message.payload)):
                encoded = encode_message(message)
                decoded = decode_message(encoded, expected_capability=CAPABILITY)
                canonical = json.dumps(
                    json.loads(encoded),
                    ensure_ascii=True,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")

                self.assertEqual(message, decoded)
                self.assertEqual(canonical, encoded)
                observed_types.add(message.message_type)
                if isinstance(message.payload, OperationResultPayload):
                    observed_outcomes.add(message.payload.outcome)

        self.assertEqual(set(MessageType), observed_types)
        self.assertEqual(set(OperationOutcome), observed_outcomes)

    def test_unknown_operation_descriptors_fail_before_return(self) -> None:
        for message_type, payload, direction in (
            (
                MessageType.OPERATION_START,
                OperationStartPayload("unknown.operation", 1, (), (), _limits()),
                Direction.CLIENT_TO_WORKER,
            ),
            (
                MessageType.OPERATION_ACCEPTED,
                OperationAcceptedPayload("unknown.operation", 1, _limits()),
                Direction.WORKER_TO_CLIENT,
            ),
            (
                MessageType.SESSION_READY,
                SessionReadyPayload(
                    1,
                    (CapabilityDescriptor("unknown.operation", 1),),
                    MAX_MESSAGE_BYTES,
                ),
                Direction.WORKER_TO_CLIENT,
            ),
        ):
            with self.subTest(message_type=message_type):
                message = _envelope(
                    message_type,
                    direction,
                    payload,
                    message_number=20,
                    operation_id=(
                        None if message_type is MessageType.SESSION_READY else OPERATION_ID
                    ),
                )
                with self.assertRaises(ProtocolError) as raised:
                    encode_message(message)

                self.assertEqual("RPC_UNSUPPORTED_OPERATION", raised.exception.code)

    def test_old_and_future_envelope_versions_are_unsupported(self) -> None:
        encoded = encode_message(_messages()[0])
        document = json.loads(encoded)
        assert isinstance(document, dict)
        for field_name in ("protocol_version", "message_version"):
            for version in (0, 2):
                with self.subTest(field_name=field_name, version=version):
                    changed = dict(document)
                    changed[field_name] = version
                    raw = json.dumps(changed).encode("utf-8")
                    with self.assertRaises(ProtocolError) as raised:
                        decode_message(raw)

                    self.assertEqual("RPC_UNSUPPORTED_VERSION", raised.exception.code)


if __name__ == "__main__":
    unittest.main()

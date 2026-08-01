from __future__ import annotations

import unittest
from pathlib import Path
from uuid import UUID

from paperworkshell.worker.protocol.codec import decode_message
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
    ProgressUnit,
    ProtocolEnvelope,
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
from paperworkshell.worker.protocol.state import ConversationPhase, ConversationState


def _uuid(number: int) -> UUID:
    return UUID(f"20000000-0000-4000-8000-{number:012x}")


SESSION_ID = _uuid(1)
WORKER_ID = _uuid(2)
CASE_ID = _uuid(3)
CAPABILITY = SessionCapability("1" * 64)
HELLO_REQUEST = _uuid(10)
START_REQUEST = _uuid(11)
CANCEL_REQUEST = _uuid(12)
SHUTDOWN_REQUEST = _uuid(13)
OPERATION_ID = _uuid(20)


def _limits(value: int = 10) -> ResourceLimits:
    return ResourceLimits(
        wall_time_ms=value,
        cpu_time_ms=value,
        memory_bytes=value,
        temporary_storage_bytes=value,
        output_bytes=value,
        page_count=value,
        pixel_count=value,
    )


def _message(
    message_type: MessageType,
    direction: Direction,
    payload: MessagePayload,
    *,
    sender_sequence: int,
    message_number: int,
    request_id: UUID | None,
    operation_id: UUID | None,
    session_id: UUID = SESSION_ID,
    worker_instance_id: UUID = WORKER_ID,
    case_id: UUID = CASE_ID,
    capability: SessionCapability = CAPABILITY,
) -> ProtocolEnvelope:
    return ProtocolEnvelope(
        protocol=PROTOCOL_NAME,
        protocol_version=PROTOCOL_VERSION,
        message_version=MESSAGE_VERSION,
        message_type=message_type,
        direction=direction,
        session_id=session_id,
        worker_instance_id=worker_instance_id,
        session_capability=capability,
        case_id=case_id,
        message_id=_uuid(100 + message_number),
        sender_sequence=sender_sequence,
        request_id=request_id,
        operation_id=operation_id,
        payload=payload,
    )


def _new_state() -> ConversationState:
    return ConversationState(SESSION_ID, WORKER_ID, CAPABILITY, CASE_ID)


def _negotiate(
    *,
    capabilities: tuple[CapabilityDescriptor, ...] = (NOOP_CAPABILITY,),
) -> ConversationState:
    state = _new_state()
    state = state.accept(
        _message(
            MessageType.SESSION_HELLO,
            Direction.CLIENT_TO_WORKER,
            SessionHelloPayload((1,), ()),
            sender_sequence=0,
            message_number=1,
            request_id=HELLO_REQUEST,
            operation_id=None,
        )
    )
    return state.accept(
        _message(
            MessageType.SESSION_READY,
            Direction.WORKER_TO_CLIENT,
            SessionReadyPayload(1, capabilities, MAX_MESSAGE_BYTES),
            sender_sequence=0,
            message_number=2,
            request_id=HELLO_REQUEST,
            operation_id=None,
        )
    )


def _start(state: ConversationState, *, message_number: int = 3) -> ConversationState:
    return state.accept(
        _message(
            MessageType.OPERATION_START,
            Direction.CLIENT_TO_WORKER,
            OperationStartPayload("protocol.noop", 1, (), (), _limits()),
            sender_sequence=1,
            message_number=message_number,
            request_id=START_REQUEST,
            operation_id=OPERATION_ID,
        )
    )


def _accept_operation(state: ConversationState, *, message_number: int = 4) -> ConversationState:
    return state.accept(
        _message(
            MessageType.OPERATION_ACCEPTED,
            Direction.WORKER_TO_CLIENT,
            OperationAcceptedPayload("protocol.noop", 1, _limits()),
            sender_sequence=1,
            message_number=message_number,
            request_id=START_REQUEST,
            operation_id=OPERATION_ID,
        )
    )


class WorkerProtocolStateTests(unittest.TestCase):
    def test_all_canonical_results_replay_through_a_complete_conversation(self) -> None:
        examples = Path("schemas/examples/worker-rpc/v1")
        prefix = (
            "session-hello.json",
            "session-ready.json",
            "operation-start.json",
            "operation-accepted.json",
            "operation-progress.json",
            "operation-cancel.json",
            "operation-cancel-ack.json",
        )
        suffix = ("session-shutdown.json", "session-closed.json")
        result_names = (
            "operation-result-succeeded.json",
            "operation-result-cancelled.json",
            "operation-result-failed.json",
            "operation-result-timed-out.json",
        )

        for result_name in result_names:
            with self.subTest(result=result_name):
                names = (*prefix, result_name, *suffix)
                messages = tuple(decode_message((examples / name).read_bytes()) for name in names)
                first = messages[0]
                state = ConversationState(
                    first.session_id,
                    first.worker_instance_id,
                    first.session_capability,
                    first.case_id,
                )
                for message in messages:
                    state = state.accept(message)

                self.assertEqual(ConversationPhase.CLOSED, state.phase)

    def test_full_cancelled_operation_and_shutdown_conversation(self) -> None:
        state = _accept_operation(_start(_negotiate()))
        state = state.accept(
            _message(
                MessageType.OPERATION_PROGRESS,
                Direction.WORKER_TO_CLIENT,
                OperationProgressPayload("processing", 1, 2, ProgressUnit.STEPS),
                sender_sequence=2,
                message_number=5,
                request_id=START_REQUEST,
                operation_id=OPERATION_ID,
            )
        )
        state = state.accept(
            _message(
                MessageType.OPERATION_CANCEL,
                Direction.CLIENT_TO_WORKER,
                OperationCancelPayload(CancelReason.USER_REQUESTED),
                sender_sequence=2,
                message_number=6,
                request_id=CANCEL_REQUEST,
                operation_id=OPERATION_ID,
            )
        )
        state = state.accept(
            _message(
                MessageType.OPERATION_CANCEL_ACK,
                Direction.WORKER_TO_CLIENT,
                OperationCancelAckPayload(CancelDisposition.REQUESTED),
                sender_sequence=3,
                message_number=7,
                request_id=CANCEL_REQUEST,
                operation_id=OPERATION_ID,
            )
        )
        state = state.accept(
            _message(
                MessageType.OPERATION_RESULT,
                Direction.WORKER_TO_CLIENT,
                OperationResultPayload(OperationOutcome.CANCELLED, (), None),
                sender_sequence=4,
                message_number=8,
                request_id=START_REQUEST,
                operation_id=OPERATION_ID,
            )
        )
        state = state.accept(
            _message(
                MessageType.SESSION_SHUTDOWN,
                Direction.CLIENT_TO_WORKER,
                SessionShutdownPayload(ShutdownReason.APPLICATION_EXIT),
                sender_sequence=3,
                message_number=9,
                request_id=SHUTDOWN_REQUEST,
                operation_id=None,
            )
        )
        state = state.accept(
            _message(
                MessageType.SESSION_CLOSED,
                Direction.WORKER_TO_CLIENT,
                SessionClosedPayload(ClosedReason.SHUTDOWN_COMPLETE),
                sender_sequence=5,
                message_number=10,
                request_id=SHUTDOWN_REQUEST,
                operation_id=None,
            )
        )

        self.assertEqual(ConversationPhase.CLOSED, state.phase)
        self.assertIsNone(state.active_operation_id)

    def test_result_can_win_cancel_race_before_acknowledgement(self) -> None:
        state = _accept_operation(_start(_negotiate()))
        state = state.accept(
            _message(
                MessageType.OPERATION_CANCEL,
                Direction.CLIENT_TO_WORKER,
                OperationCancelPayload(CancelReason.USER_REQUESTED),
                sender_sequence=2,
                message_number=5,
                request_id=CANCEL_REQUEST,
                operation_id=OPERATION_ID,
            )
        )
        state = state.accept(
            _message(
                MessageType.OPERATION_RESULT,
                Direction.WORKER_TO_CLIENT,
                OperationResultPayload(OperationOutcome.SUCCEEDED, (), None),
                sender_sequence=2,
                message_number=6,
                request_id=START_REQUEST,
                operation_id=OPERATION_ID,
            )
        )
        state = state.accept(
            _message(
                MessageType.OPERATION_CANCEL_ACK,
                Direction.WORKER_TO_CLIENT,
                OperationCancelAckPayload(CancelDisposition.ALREADY_TERMINAL),
                sender_sequence=3,
                message_number=7,
                request_id=CANCEL_REQUEST,
                operation_id=OPERATION_ID,
            )
        )

        self.assertIsNone(state.active_operation_id)
        self.assertFalse(state.terminal_operations[0].pending_cancel_request_ids)

    def test_binding_replay_and_sequence_mismatches_are_rejected(self) -> None:
        hello = _message(
            MessageType.SESSION_HELLO,
            Direction.CLIENT_TO_WORKER,
            SessionHelloPayload((1,), ()),
            sender_sequence=0,
            message_number=1,
            request_id=HELLO_REQUEST,
            operation_id=None,
        )
        state = _new_state().accept(hello)
        cases = (
            (hello, "RPC_REPLAYED_MESSAGE"),
            (
                _message(
                    MessageType.SESSION_READY,
                    Direction.WORKER_TO_CLIENT,
                    SessionReadyPayload(1, (NOOP_CAPABILITY,), MAX_MESSAGE_BYTES),
                    sender_sequence=2,
                    message_number=2,
                    request_id=HELLO_REQUEST,
                    operation_id=None,
                ),
                "RPC_SEQUENCE_MISMATCH",
            ),
            (
                _message(
                    MessageType.SESSION_READY,
                    Direction.WORKER_TO_CLIENT,
                    SessionReadyPayload(1, (NOOP_CAPABILITY,), MAX_MESSAGE_BYTES),
                    sender_sequence=0,
                    message_number=3,
                    request_id=HELLO_REQUEST,
                    operation_id=None,
                    session_id=_uuid(99),
                ),
                "RPC_BINDING_MISMATCH",
            ),
        )
        for message, expected_code in cases:
            with (
                self.subTest(expected_code=expected_code),
                self.assertRaises(ProtocolError) as raised,
            ):
                state.accept(message)

            self.assertEqual(expected_code, raised.exception.code)

    def test_every_conversation_binding_dimension_is_checked(self) -> None:
        cases = (
            {"session_id": _uuid(90)},
            {"worker_instance_id": _uuid(91)},
            {"case_id": _uuid(92)},
            {"capability": SessionCapability("9" * 64)},
        )
        for overrides in cases:
            with self.subTest(overrides=tuple(overrides)):
                hello = _message(
                    MessageType.SESSION_HELLO,
                    Direction.CLIENT_TO_WORKER,
                    SessionHelloPayload((1,), ()),
                    sender_sequence=0,
                    message_number=30,
                    request_id=HELLO_REQUEST,
                    operation_id=None,
                    **overrides,
                )
                with self.assertRaises(ProtocolError) as raised:
                    _new_state().accept(hello)

                expected = (
                    "RPC_CAPABILITY_MISMATCH"
                    if "capability" in overrides
                    else "RPC_BINDING_MISMATCH"
                )
                self.assertEqual(expected, raised.exception.code)

    def test_additive_capability_discovery_is_allowed_but_dispatch_is_fail_closed(self) -> None:
        future = CapabilityDescriptor("future.operation", 1)
        state = _negotiate(capabilities=(NOOP_CAPABILITY, future))
        self.assertEqual((NOOP_CAPABILITY, future), state.negotiated_capabilities)
        self.assertEqual(OPERATION_ID, _start(state).active_operation_id)

        unadvertised = _negotiate(capabilities=(future,))
        with self.assertRaises(ProtocolError) as unavailable:
            _start(unadvertised, message_number=20)
        self.assertEqual("RPC_CAPABILITY_UNAVAILABLE", unavailable.exception.code)

        with self.assertRaises(ProtocolError) as unregistered:
            state.accept(
                _message(
                    MessageType.OPERATION_START,
                    Direction.CLIENT_TO_WORKER,
                    OperationStartPayload("future.operation", 1, (), (), _limits()),
                    sender_sequence=1,
                    message_number=21,
                    request_id=START_REQUEST,
                    operation_id=OPERATION_ID,
                )
            )
        self.assertEqual("RPC_UNSUPPORTED_OPERATION", unregistered.exception.code)

    def test_progress_regression_and_limit_escalation_are_rejected(self) -> None:
        started = _start(_negotiate())
        escalated = _message(
            MessageType.OPERATION_ACCEPTED,
            Direction.WORKER_TO_CLIENT,
            OperationAcceptedPayload("protocol.noop", 1, _limits(11)),
            sender_sequence=1,
            message_number=4,
            request_id=START_REQUEST,
            operation_id=OPERATION_ID,
        )
        with self.assertRaises(ProtocolError) as limits:
            started.accept(escalated)
        self.assertEqual("RPC_LIMIT_ESCALATION", limits.exception.code)

        state = _accept_operation(started)
        state = state.accept(
            _message(
                MessageType.OPERATION_PROGRESS,
                Direction.WORKER_TO_CLIENT,
                OperationProgressPayload("processing", 5, 10, ProgressUnit.ITEMS),
                sender_sequence=2,
                message_number=5,
                request_id=START_REQUEST,
                operation_id=OPERATION_ID,
            )
        )
        with self.assertRaises(ProtocolError) as progress:
            state.accept(
                _message(
                    MessageType.OPERATION_PROGRESS,
                    Direction.WORKER_TO_CLIENT,
                    OperationProgressPayload("processing", 4, 10, ProgressUnit.ITEMS),
                    sender_sequence=3,
                    message_number=6,
                    request_id=START_REQUEST,
                    operation_id=OPERATION_ID,
                )
            )
        self.assertEqual("RPC_PROGRESS_REGRESSION", progress.exception.code)

    def test_second_active_operation_and_result_before_acceptance_are_rejected(self) -> None:
        state = _start(_negotiate())
        second_start = _message(
            MessageType.OPERATION_START,
            Direction.CLIENT_TO_WORKER,
            OperationStartPayload("protocol.noop", 1, (), (), _limits()),
            sender_sequence=2,
            message_number=4,
            request_id=_uuid(30),
            operation_id=_uuid(31),
        )
        with self.assertRaises(ProtocolError) as in_flight:
            state.accept(second_start)
        self.assertEqual("RPC_OPERATION_IN_FLIGHT", in_flight.exception.code)

        premature_result = _message(
            MessageType.OPERATION_RESULT,
            Direction.WORKER_TO_CLIENT,
            OperationResultPayload(OperationOutcome.SUCCEEDED, (), None),
            sender_sequence=1,
            message_number=5,
            request_id=START_REQUEST,
            operation_id=OPERATION_ID,
        )
        with self.assertRaises(ProtocolError) as premature:
            state.accept(premature_result)
        self.assertEqual("RPC_INVALID_STATE", premature.exception.code)

    def test_worker_fault_must_invalidate_exactly_the_active_operation(self) -> None:
        state = _accept_operation(_start(_negotiate()))
        fault_error = ErrorPayload(
            code=ErrorCode.WORKER_INTERNAL_FAULT,
            category=ErrorCategory.INTERNAL,
            retry_action=RetryAction.RESTART_WORKER,
            restart_required=True,
            parameters=(),
        )
        invalid_fault = _message(
            MessageType.WORKER_FAULT,
            Direction.WORKER_TO_CLIENT,
            WorkerFaultPayload(fault_error, ()),
            sender_sequence=2,
            message_number=5,
            request_id=START_REQUEST,
            operation_id=None,
        )
        with self.assertRaises(ProtocolError) as invalidated:
            state.accept(invalid_fault)
        self.assertEqual("RPC_OPERATION_MISMATCH", invalidated.exception.code)

        valid_fault = _message(
            MessageType.WORKER_FAULT,
            Direction.WORKER_TO_CLIENT,
            WorkerFaultPayload(fault_error, (OPERATION_ID,)),
            sender_sequence=2,
            message_number=6,
            request_id=START_REQUEST,
            operation_id=None,
        )
        faulted = state.accept(valid_fault)
        self.assertEqual(ConversationPhase.FAULTED, faulted.phase)
        with self.assertRaises(ProtocolError) as terminal:
            faulted.accept(valid_fault)
        self.assertEqual("RPC_SESSION_TERMINAL", terminal.exception.code)

    def test_timed_out_result_is_terminal_and_later_events_are_rejected(self) -> None:
        state = _accept_operation(_start(_negotiate()))
        error = ErrorPayload(
            code=ErrorCode.WORKER_OPERATION_TIMED_OUT,
            category=ErrorCategory.RESOURCE_LIMIT,
            retry_action=RetryAction.RETRY,
            restart_required=False,
            parameters=(),
        )
        state = state.accept(
            _message(
                MessageType.OPERATION_RESULT,
                Direction.WORKER_TO_CLIENT,
                OperationResultPayload(OperationOutcome.TIMED_OUT, (), error),
                sender_sequence=2,
                message_number=40,
                request_id=START_REQUEST,
                operation_id=OPERATION_ID,
            )
        )
        self.assertIsNone(state.active_operation_id)

        later_messages = (
            _message(
                MessageType.OPERATION_RESULT,
                Direction.WORKER_TO_CLIENT,
                OperationResultPayload(OperationOutcome.TIMED_OUT, (), error),
                sender_sequence=3,
                message_number=41,
                request_id=START_REQUEST,
                operation_id=OPERATION_ID,
            ),
            _message(
                MessageType.OPERATION_PROGRESS,
                Direction.WORKER_TO_CLIENT,
                OperationProgressPayload("late", 1, 1, ProgressUnit.STEPS),
                sender_sequence=3,
                message_number=42,
                request_id=START_REQUEST,
                operation_id=OPERATION_ID,
            ),
        )
        for later in later_messages:
            with (
                self.subTest(message_type=later.message_type),
                self.assertRaises(ProtocolError) as raised,
            ):
                state.accept(later)
            self.assertEqual("RPC_OPERATION_TERMINAL", raised.exception.code)

    def test_restart_required_operation_result_faults_the_session(self) -> None:
        state = _accept_operation(_start(_negotiate()))
        error = ErrorPayload(
            code=ErrorCode.WORKER_OPERATION_FAILED,
            category=ErrorCategory.INTERNAL,
            retry_action=RetryAction.RESTART_WORKER,
            restart_required=True,
            parameters=(),
        )
        result = _message(
            MessageType.OPERATION_RESULT,
            Direction.WORKER_TO_CLIENT,
            OperationResultPayload(OperationOutcome.FAILED, (), error),
            sender_sequence=2,
            message_number=50,
            request_id=START_REQUEST,
            operation_id=OPERATION_ID,
        )

        faulted = state.accept(result)

        self.assertEqual(ConversationPhase.FAULTED, faulted.phase)
        self.assertIsNone(faulted.active_operation_id)
        with self.assertRaises(ProtocolError) as terminal:
            faulted.accept(result)
        self.assertEqual("RPC_SESSION_TERMINAL", terminal.exception.code)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from dataclasses import replace
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
    QUALITY_OCR_REVIEW_CAPABILITY,
    QUALITY_OCR_REVIEW_HEIGHT_PARAMETER,
    QUALITY_OCR_REVIEW_LANGUAGE_SET_ID_PARAMETER,
    QUALITY_OCR_REVIEW_MANIFEST_REVISION_PARAMETER,
    QUALITY_OCR_REVIEW_PAGE_ID_PARAMETER,
    QUALITY_OCR_REVIEW_PRESENTATION_SHA256_PARAMETER,
    QUALITY_OCR_REVIEW_PROFILE_ID_PARAMETER,
    QUALITY_OCR_REVIEW_RESULT_HANDLE_PARAMETER,
    QUALITY_OCR_REVIEW_ROW_STRIDE_PARAMETER,
    QUALITY_OCR_REVIEW_SAMPLE_BYTES_PARAMETER,
    QUALITY_OCR_REVIEW_SAMPLE_HANDLE_PARAMETER,
    QUALITY_OCR_REVIEW_SAMPLE_SHA256_PARAMETER,
    QUALITY_OCR_REVIEW_SHA256_TOKEN_PREFIX,
    QUALITY_OCR_REVIEW_WIDTH_PARAMETER,
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
    ReferenceArea,
    ResourceLimits,
    RetryAction,
    ScalarValueType,
    SessionCapability,
    SessionClosedPayload,
    SessionHelloPayload,
    SessionReadyPayload,
    SessionShutdownPayload,
    ShutdownReason,
    TaggedScalar,
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
QUALITY_PAGE_ID = _uuid(30)


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


def _quality_limits() -> ResourceLimits:
    return ResourceLimits(
        wall_time_ms=5_000,
        cpu_time_ms=4_000,
        memory_bytes=1_048_576,
        temporary_storage_bytes=8_192,
        output_bytes=4_096,
        page_count=1,
        pixel_count=16,
    )


def _quality_start_payload(*, page_id: UUID = QUALITY_PAGE_ID) -> OperationStartPayload:
    digest = f"{QUALITY_OCR_REVIEW_SHA256_TOKEN_PREFIX}{'a' * 64}"
    return OperationStartPayload(
        QUALITY_OCR_REVIEW_CAPABILITY.capability_id,
        QUALITY_OCR_REVIEW_CAPABILITY.capability_version,
        (),
        (
            TaggedScalar(
                QUALITY_OCR_REVIEW_SAMPLE_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                101,
            ),
            TaggedScalar(
                QUALITY_OCR_REVIEW_RESULT_HANDLE_PARAMETER,
                ScalarValueType.INTEGER,
                102,
            ),
            TaggedScalar(
                QUALITY_OCR_REVIEW_PAGE_ID_PARAMETER,
                ScalarValueType.UUID,
                page_id,
            ),
            TaggedScalar(
                QUALITY_OCR_REVIEW_MANIFEST_REVISION_PARAMETER,
                ScalarValueType.INTEGER,
                7,
            ),
            TaggedScalar(
                QUALITY_OCR_REVIEW_WIDTH_PARAMETER,
                ScalarValueType.INTEGER,
                4,
            ),
            TaggedScalar(
                QUALITY_OCR_REVIEW_HEIGHT_PARAMETER,
                ScalarValueType.INTEGER,
                4,
            ),
            TaggedScalar(
                QUALITY_OCR_REVIEW_ROW_STRIDE_PARAMETER,
                ScalarValueType.INTEGER,
                16,
            ),
            TaggedScalar(
                QUALITY_OCR_REVIEW_SAMPLE_BYTES_PARAMETER,
                ScalarValueType.INTEGER,
                64,
            ),
            TaggedScalar(
                QUALITY_OCR_REVIEW_SAMPLE_SHA256_PARAMETER,
                ScalarValueType.TOKEN,
                digest,
            ),
            TaggedScalar(
                QUALITY_OCR_REVIEW_PRESENTATION_SHA256_PARAMETER,
                ScalarValueType.TOKEN,
                digest,
            ),
            TaggedScalar(
                QUALITY_OCR_REVIEW_LANGUAGE_SET_ID_PARAMETER,
                ScalarValueType.TOKEN,
                "latin-default-v1",
            ),
            TaggedScalar(
                QUALITY_OCR_REVIEW_PROFILE_ID_PARAMETER,
                ScalarValueType.TOKEN,
                "quality-ocr-review-v1",
            ),
        ),
        _quality_limits(),
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
    initial_state: ConversationState | None = None,
) -> ConversationState:
    state = _new_state() if initial_state is None else initial_state
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


def _quality_state(
    *,
    page_id: UUID = QUALITY_PAGE_ID,
    accepted: bool = False,
) -> ConversationState:
    registered = ConversationState(
        SESSION_ID,
        WORKER_ID,
        CAPABILITY,
        CASE_ID,
        registered_capabilities=(QUALITY_OCR_REVIEW_CAPABILITY,),
    )
    state = _negotiate(
        capabilities=(QUALITY_OCR_REVIEW_CAPABILITY,),
        initial_state=registered,
    )
    state = state.accept(
        _message(
            MessageType.OPERATION_START,
            Direction.CLIENT_TO_WORKER,
            _quality_start_payload(page_id=page_id),
            sender_sequence=1,
            message_number=3,
            request_id=START_REQUEST,
            operation_id=OPERATION_ID,
        )
    )
    if not accepted:
        return state
    return state.accept(
        _message(
            MessageType.OPERATION_ACCEPTED,
            Direction.WORKER_TO_CLIENT,
            OperationAcceptedPayload(
                QUALITY_OCR_REVIEW_CAPABILITY.capability_id,
                QUALITY_OCR_REVIEW_CAPABILITY.capability_version,
                _quality_limits(),
            ),
            sender_sequence=1,
            message_number=4,
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
                OperationProgressPayload(ProgressStage.PROCESSING, 1, 2, ProgressUnit.STEPS),
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

    def test_worker_capabilities_must_be_explicitly_registered(self) -> None:
        future = CapabilityDescriptor("future.operation", 1)
        with self.assertRaises(ProtocolError) as unregistered:
            _negotiate(capabilities=(NOOP_CAPABILITY, future))
        self.assertEqual("RPC_CAPABILITY_UNAVAILABLE", unregistered.exception.code)

        registered_state = ConversationState(
            SESSION_ID,
            WORKER_ID,
            CAPABILITY,
            CASE_ID,
            registered_capabilities=(NOOP_CAPABILITY, future),
        )
        state = _negotiate(
            capabilities=(NOOP_CAPABILITY, future),
            initial_state=registered_state,
        )
        self.assertEqual((NOOP_CAPABILITY, future), state.negotiated_capabilities)
        self.assertEqual(OPERATION_ID, _start(state).active_operation_id)

    def test_quality_ocr_review_advertised_capability_accepts_exact_closed_start(self) -> None:
        state = _quality_state()

        self.assertEqual(OPERATION_ID, state.active_operation_id)
        self.assertEqual((QUALITY_OCR_REVIEW_CAPABILITY,), state.negotiated_capabilities)

    def test_quality_ocr_review_rejects_case_operation_and_page_identity_collisions(self) -> None:
        for page_id in (CASE_ID, OPERATION_ID):
            with self.subTest(page_id=page_id), self.assertRaises(ProtocolError) as raised:
                _quality_state(page_id=page_id)
            self.assertEqual("RPC_INVALID_OPERATION", raised.exception.code)

    def test_quality_ocr_review_rejects_open_profile_language_and_digest_tokens(self) -> None:
        mutations = (
            (QUALITY_OCR_REVIEW_SAMPLE_SHA256_PARAMETER, "sha256-bad"),
            (QUALITY_OCR_REVIEW_PRESENTATION_SHA256_PARAMETER, "sha256-bad"),
            (QUALITY_OCR_REVIEW_LANGUAGE_SET_ID_PARAMETER, "future-language-v9"),
            (QUALITY_OCR_REVIEW_PROFILE_ID_PARAMETER, "future-profile-v9"),
        )
        for name, value in mutations:
            with self.subTest(name=name):
                registered = ConversationState(
                    SESSION_ID,
                    WORKER_ID,
                    CAPABILITY,
                    CASE_ID,
                    registered_capabilities=(QUALITY_OCR_REVIEW_CAPABILITY,),
                )
                state = _negotiate(
                    capabilities=(QUALITY_OCR_REVIEW_CAPABILITY,),
                    initial_state=registered,
                )
                payload = _quality_start_payload()
                malformed = replace(
                    payload,
                    parameters=tuple(
                        replace(parameter, value=value) if parameter.name == name else parameter
                        for parameter in payload.parameters
                    ),
                )
                message = _message(
                    MessageType.OPERATION_START,
                    Direction.CLIENT_TO_WORKER,
                    malformed,
                    sender_sequence=1,
                    message_number=3,
                    request_id=START_REQUEST,
                    operation_id=OPERATION_ID,
                )
                with self.assertRaises(ProtocolError) as raised:
                    state.accept(message)
                self.assertEqual("RPC_INVALID_OPERATION", raised.exception.code)

    def test_quality_ocr_review_rejects_malformed_progress_total_and_unit(self) -> None:
        cases = (
            OperationProgressPayload(ProgressStage.PROCESSING, 0, 1, ProgressUnit.STEPS),
            OperationProgressPayload(ProgressStage.PROCESSING, 0, 2, ProgressUnit.PAGES),
        )
        for index, progress in enumerate(cases):
            with self.subTest(progress=progress), self.assertRaises(ProtocolError) as raised:
                _quality_state(accepted=True).accept(
                    _message(
                        MessageType.OPERATION_PROGRESS,
                        Direction.WORKER_TO_CLIENT,
                        progress,
                        sender_sequence=2,
                        message_number=40 + index,
                        request_id=START_REQUEST,
                        operation_id=OPERATION_ID,
                    )
                )
            self.assertEqual("RPC_INVALID_PROGRESS", raised.exception.code)

    def test_quality_ocr_review_rejects_nonempty_result_outputs(self) -> None:
        output = CaseObjectReference(
            area=ReferenceArea.OPERATION_STAGING,
            object_id=_uuid(50),
            operation_id=OPERATION_ID,
            content_kind="application.pdf",
            expected_byte_size=1,
            expected_sha256="e" * 64,
        )
        message = _message(
            MessageType.OPERATION_RESULT,
            Direction.WORKER_TO_CLIENT,
            OperationResultPayload(OperationOutcome.SUCCEEDED, (output,), None),
            sender_sequence=2,
            message_number=50,
            request_id=START_REQUEST,
            operation_id=OPERATION_ID,
        )

        with self.assertRaises(ProtocolError) as raised:
            _quality_state(accepted=True).accept(message)

        self.assertEqual("RPC_INVALID_RESULT", raised.exception.code)

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
                OperationProgressPayload(ProgressStage.PROCESSING, 5, 10, ProgressUnit.ITEMS),
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
                    OperationProgressPayload(ProgressStage.PROCESSING, 4, 10, ProgressUnit.ITEMS),
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
                OperationProgressPayload(ProgressStage.PROCESSING, 1, 1, ProgressUnit.STEPS),
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

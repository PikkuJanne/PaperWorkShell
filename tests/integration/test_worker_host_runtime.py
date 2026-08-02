"""Cross-component tests for the authenticated worker-host runtime."""

from __future__ import annotations

import io
import os
import threading
from dataclasses import dataclass
from types import TracebackType
from typing import Any, cast
from uuid import UUID

from paperworkshell.worker import runtime as runtime_module
from paperworkshell.worker.operations import (
    OperationContext,
    RuntimeOperationRegistry,
    timed_out_operation_result,
)
from paperworkshell.worker.protocol.bootstrap import BootstrapGrant
from paperworkshell.worker.protocol.framing import read_frame, write_frame
from paperworkshell.worker.protocol.models import (
    NOOP_CAPABILITY,
    CancelReason,
    CapabilityDescriptor,
    Direction,
    ErrorCode,
    MessagePayload,
    MessageType,
    OperationCancelPayload,
    OperationOutcome,
    OperationProgressPayload,
    OperationResultPayload,
    OperationStartPayload,
    ProgressStage,
    ProgressUnit,
    ProtocolEnvelope,
    ResourceLimits,
    SessionCapability,
    SessionHelloPayload,
    SessionShutdownPayload,
    ShutdownReason,
)
from paperworkshell.worker.protocol.peer import PeerBinding, ProtocolPeer
from paperworkshell.worker.protocol.state import ConversationPhase
from paperworkshell.worker.runtime import HostExitCode, WorkerHostRuntime


def _uuid(value: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{value:012x}")


def _limits(wall_time_ms: int = 1_000) -> ResourceLimits:
    return ResourceLimits(wall_time_ms, 1_000, 64 * 1024 * 1024, 1_024, 1_024, 1, 1)


class _Harness:
    def __init__(self, operations: RuntimeOperationRegistry | None = None) -> None:
        self.binding = PeerBinding(
            _uuid(1),
            _uuid(2),
            _uuid(3),
            SessionCapability("a" * 64),
        )
        to_host_read, to_host_write = os.pipe()
        from_host_read, from_host_write = os.pipe()
        self.host_reader = os.fdopen(to_host_read, "rb", buffering=0)
        self.client_writer = os.fdopen(to_host_write, "wb", buffering=0)
        self.client_reader = os.fdopen(from_host_read, "rb", buffering=0)
        self.host_writer = os.fdopen(from_host_write, "wb", buffering=0)
        grant = BootstrapGrant(
            self.binding.session_id,
            self.binding.worker_instance_id,
            self.binding.case_id,
            self.binding.session_capability,
        )
        runtime = (
            WorkerHostRuntime(grant, self.host_reader, self.host_writer)
            if operations is None
            else WorkerHostRuntime(
                grant,
                self.host_reader,
                self.host_writer,
                operations=operations,
            )
        )
        self.client = ProtocolPeer(self.binding, Direction.CLIENT_TO_WORKER)
        self.exit_codes: list[HostExitCode] = []
        self.thread = threading.Thread(
            target=lambda: self.exit_codes.append(runtime.run()),
            name="test-worker-host",
            daemon=True,
        )
        self.thread.start()

    def __enter__(self) -> _Harness:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        for stream in (
            self.client_writer,
            self.client_reader,
            self.host_reader,
            self.host_writer,
        ):
            try:
                stream.close()
            except OSError:
                pass
        self.thread.join(timeout=1)

    def send(
        self,
        message_type: MessageType,
        payload: MessagePayload,
        *,
        request_id: UUID,
        operation_id: UUID | None,
    ) -> None:
        prepared = self.client.prepare_outbound(
            message_type,
            payload,
            request_id=request_id,
            operation_id=operation_id,
        )
        write_frame(self.client_writer, prepared.encoded)

    def receive(self) -> ProtocolEnvelope:
        raw = read_frame(self.client_reader)
        assert raw is not None
        return self.client.accept_inbound(raw)

    def negotiate(self) -> None:
        request_id = _uuid(10)
        self.send(
            MessageType.SESSION_HELLO,
            SessionHelloPayload((1,), (NOOP_CAPABILITY,)),
            request_id=request_id,
            operation_id=None,
        )
        assert self.receive().message_type is MessageType.SESSION_READY

    def wait_for_exit(self) -> HostExitCode:
        self.thread.join(timeout=2)
        assert not self.thread.is_alive()
        assert len(self.exit_codes) == 1
        return self.exit_codes[0]


def _start(harness: _Harness, *, wall_time_ms: int = 1_000) -> tuple[UUID, UUID]:
    request_id = _uuid(20)
    operation_id = _uuid(21)
    harness.send(
        MessageType.OPERATION_START,
        OperationStartPayload("protocol.noop", 1, (), (), _limits(wall_time_ms)),
        request_id=request_id,
        operation_id=operation_id,
    )
    return request_id, operation_id


def _receive_result(harness: _Harness) -> OperationResultPayload:
    while True:
        message = harness.receive()
        if message.message_type is MessageType.OPERATION_RESULT:
            payload = message.payload
            assert type(payload) is OperationResultPayload
            return payload


def test_runtime_round_trip_progress_and_clean_shutdown() -> None:
    with _Harness() as harness:
        harness.negotiate()
        _start(harness)
        result = _receive_result(harness)
        assert result.outcome is OperationOutcome.SUCCEEDED

        shutdown_request = _uuid(30)
        harness.send(
            MessageType.SESSION_SHUTDOWN,
            SessionShutdownPayload(ShutdownReason.APPLICATION_EXIT),
            request_id=shutdown_request,
            operation_id=None,
        )
        assert harness.receive().message_type is MessageType.SESSION_CLOSED
        assert harness.wait_for_exit() is HostExitCode.CLEAN
        assert harness.client.state.phase is ConversationPhase.CLOSED


def test_runtime_cooperatively_cancels_an_active_operation() -> None:
    with _Harness() as harness:
        harness.negotiate()
        _request_id, operation_id = _start(harness)
        while harness.receive().message_type is not MessageType.OPERATION_PROGRESS:
            pass
        harness.send(
            MessageType.OPERATION_CANCEL,
            OperationCancelPayload(CancelReason.USER_REQUESTED),
            request_id=_uuid(31),
            operation_id=operation_id,
        )
        result = _receive_result(harness)
        assert result.outcome is OperationOutcome.CANCELLED


def test_runtime_timeout_is_restart_required_and_exits() -> None:
    with _Harness() as harness:
        harness.negotiate()
        _start(harness, wall_time_ms=1)
        result = _receive_result(harness)

        assert result.outcome is OperationOutcome.TIMED_OUT
        assert result.error is not None
        assert result.error.code is ErrorCode.WORKER_OPERATION_TIMED_OUT
        assert result.error.restart_required
        assert harness.wait_for_exit() is HostExitCode.OPERATION_TIMED_OUT
        assert harness.client.state.phase is ConversationPhase.FAULTED


@dataclass(frozen=True, slots=True)
class _EarlyRestartRequiredHandler:
    @property
    def descriptor(self) -> CapabilityDescriptor:
        return NOOP_CAPABILITY

    def execute(self, context: OperationContext) -> OperationResultPayload:
        del context
        return timed_out_operation_result()


def test_early_restart_required_result_terminates_the_host_generation() -> None:
    registry = RuntimeOperationRegistry((_EarlyRestartRequiredHandler(),))
    with _Harness(registry) as harness:
        harness.negotiate()
        _start(harness)
        result = _receive_result(harness)

        assert result.outcome is OperationOutcome.TIMED_OUT
        assert result.error is not None
        assert result.error.restart_required
        assert harness.wait_for_exit() is HostExitCode.OPERATION_TIMED_OUT
        assert harness.client.state.phase is ConversationPhase.FAULTED


class _FakeClock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _take_frame(writer: io.BytesIO) -> bytes:
    writer.seek(0)
    payload = read_frame(writer)
    assert payload is not None
    assert read_frame(writer) is None
    writer.seek(0)
    writer.truncate()
    return payload


def _take_frames(writer: io.BytesIO, count: int) -> tuple[bytes, ...]:
    writer.seek(0)
    payloads: list[bytes] = []
    for _index in range(count):
        payload = read_frame(writer)
        assert payload is not None
        payloads.append(payload)
    assert read_frame(writer) is None
    writer.seek(0)
    writer.truncate()
    return tuple(payloads)


class _BlockingSuccessHandler:
    def __init__(self) -> None:
        self.release = threading.Event()
        self.finished = threading.Event()

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return NOOP_CAPABILITY

    def execute(self, context: OperationContext) -> OperationResultPayload:
        del context
        self.release.wait()
        self.finished.set()
        return OperationResultPayload(OperationOutcome.SUCCEEDED, (), None)


class _WorkerPublicationGate:
    """Pause only the operation thread before terminal timestamp publication."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.worker_waiting = threading.Event()
        self.release_worker = threading.Event()
        self.worker_exited = threading.Event()

    def __enter__(self) -> _WorkerPublicationGate:
        is_worker = threading.current_thread().name == "pws-worker-operation"
        if is_worker:
            self.worker_waiting.set()
            self.release_worker.wait()
        self._lock.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._lock.release()
        if threading.current_thread().name == "pws-worker-operation":
            self.worker_exited.set()


def _direct_runtime(
    clock: _FakeClock,
    *,
    operations: RuntimeOperationRegistry | None = None,
) -> tuple[WorkerHostRuntime, ProtocolPeer, io.BytesIO]:
    binding = PeerBinding(_uuid(41), _uuid(42), _uuid(43), SessionCapability("b" * 64))
    grant = BootstrapGrant(
        binding.session_id,
        binding.worker_instance_id,
        binding.case_id,
        binding.session_capability,
    )
    writer = io.BytesIO()
    runtime = (
        WorkerHostRuntime(grant, io.BytesIO(), writer, monotonic=clock)
        if operations is None
        else WorkerHostRuntime(
            grant,
            io.BytesIO(),
            writer,
            operations=operations,
            monotonic=clock,
        )
    )
    client = ProtocolPeer(binding, Direction.CLIENT_TO_WORKER)
    hello = client.prepare_outbound(
        MessageType.SESSION_HELLO,
        SessionHelloPayload((1,), (NOOP_CAPABILITY,)),
        request_id=_uuid(44),
        operation_id=None,
    )
    assert runtime._dispatch(runtime_module._Inbound(hello.encoded)) is None
    client.accept_inbound(_take_frame(writer))
    return runtime, client, writer


def _start_direct_operation(
    runtime: WorkerHostRuntime,
    client: ProtocolPeer,
    writer: io.BytesIO,
) -> UUID:
    operation_id = _uuid(46)
    start = client.prepare_outbound(
        MessageType.OPERATION_START,
        OperationStartPayload("protocol.noop", 1, (), (), _limits(1_000)),
        request_id=_uuid(45),
        operation_id=operation_id,
    )
    assert runtime._dispatch(runtime_module._Inbound(start.encoded)) is None
    client.accept_inbound(_take_frame(writer))
    return operation_id


def _record_direct_completion(
    runtime: WorkerHostRuntime,
    *,
    occurred_at: float,
) -> None:
    active = runtime._active
    assert active is not None
    completion = runtime_module._Completed(
        active.token,
        OperationResultPayload(OperationOutcome.SUCCEEDED, (), None),
        occurred_at,
    )
    with runtime._completion_lock:
        runtime._recorded_completion = completion


def test_expired_host_deadline_preempts_queued_cancellation_traffic() -> None:
    clock = _FakeClock(10.0)
    runtime, client, writer = _direct_runtime(clock)
    operation_id = _start_direct_operation(runtime, client, writer)

    cancel = client.prepare_outbound(
        MessageType.OPERATION_CANCEL,
        OperationCancelPayload(CancelReason.USER_REQUESTED),
        request_id=_uuid(47),
        operation_id=operation_id,
    )
    clock.value = 11.0
    exit_code = runtime._dispatch(runtime_module._Inbound(cancel.encoded))
    result = client.accept_inbound(_take_frame(writer))

    assert exit_code is HostExitCode.OPERATION_TIMED_OUT
    assert result.message_type is MessageType.OPERATION_RESULT
    assert type(result.payload) is OperationResultPayload
    assert result.payload.outcome is OperationOutcome.TIMED_OUT
    assert result.payload.error is not None
    assert result.payload.error.restart_required


def test_predeadline_completion_wins_behind_queued_cancellation() -> None:
    clock = _FakeClock(10.0)
    handler = _BlockingSuccessHandler()
    runtime, client, writer = _direct_runtime(
        clock,
        operations=RuntimeOperationRegistry((handler,)),
    )
    operation_id = _start_direct_operation(runtime, client, writer)
    cancel = client.prepare_outbound(
        MessageType.OPERATION_CANCEL,
        OperationCancelPayload(CancelReason.USER_REQUESTED),
        request_id=_uuid(47),
        operation_id=operation_id,
    )
    _record_direct_completion(runtime, occurred_at=10.5)

    clock.value = 11.0
    assert runtime._dispatch(runtime_module._Inbound(cancel.encoded)) is None
    acknowledgement, result = (
        client.accept_inbound(payload) for payload in _take_frames(writer, 2)
    )

    assert acknowledgement.message_type is MessageType.OPERATION_CANCEL_ACK
    assert result.message_type is MessageType.OPERATION_RESULT
    assert type(result.payload) is OperationResultPayload
    assert result.payload.outcome is OperationOutcome.SUCCEEDED
    assert runtime._active is None
    handler.release.set()
    assert handler.finished.wait(1.0)


def test_predeadline_completion_wins_behind_queued_final_progress() -> None:
    clock = _FakeClock(10.0)
    handler = _BlockingSuccessHandler()
    runtime, client, writer = _direct_runtime(
        clock,
        operations=RuntimeOperationRegistry((handler,)),
    )
    _start_direct_operation(runtime, client, writer)
    active = runtime._active
    assert active is not None
    progress = runtime_module._Progress(
        active.token,
        OperationProgressPayload(ProgressStage.PROCESSING, 1, 1, ProgressUnit.STEPS),
    )
    _record_direct_completion(runtime, occurred_at=10.5)

    clock.value = 11.0
    assert runtime._dispatch(progress) is None
    progress_message, result = (
        client.accept_inbound(payload) for payload in _take_frames(writer, 2)
    )

    assert progress_message.message_type is MessageType.OPERATION_PROGRESS
    assert result.message_type is MessageType.OPERATION_RESULT
    assert type(result.payload) is OperationResultPayload
    assert result.payload.outcome is OperationOutcome.SUCCEEDED
    assert runtime._active is None
    handler.release.set()
    assert handler.finished.wait(1.0)


def test_postdeadline_recorded_completion_still_times_out() -> None:
    clock = _FakeClock(10.0)
    handler = _BlockingSuccessHandler()
    runtime, client, writer = _direct_runtime(
        clock,
        operations=RuntimeOperationRegistry((handler,)),
    )
    operation_id = _start_direct_operation(runtime, client, writer)
    cancel = client.prepare_outbound(
        MessageType.OPERATION_CANCEL,
        OperationCancelPayload(CancelReason.USER_REQUESTED),
        request_id=_uuid(47),
        operation_id=operation_id,
    )
    _record_direct_completion(runtime, occurred_at=11.1)

    clock.value = 11.1
    exit_code = runtime._dispatch(runtime_module._Inbound(cancel.encoded))
    result = client.accept_inbound(_take_frame(writer))

    assert exit_code is HostExitCode.OPERATION_TIMED_OUT
    assert result.message_type is MessageType.OPERATION_RESULT
    assert type(result.payload) is OperationResultPayload
    assert result.payload.outcome is OperationOutcome.TIMED_OUT
    handler.release.set()
    assert handler.finished.wait(1.0)


def test_deadline_arbitration_precedes_a_terminal_timestamp_not_yet_published() -> None:
    clock = _FakeClock(10.0)
    handler = _BlockingSuccessHandler()
    runtime, client, writer = _direct_runtime(
        clock,
        operations=RuntimeOperationRegistry((handler,)),
    )
    operation_id = _start_direct_operation(runtime, client, writer)
    cancel = client.prepare_outbound(
        MessageType.OPERATION_CANCEL,
        OperationCancelPayload(CancelReason.USER_REQUESTED),
        request_id=_uuid(47),
        operation_id=operation_id,
    )
    gate = _WorkerPublicationGate()
    runtime._completion_lock = cast(Any, gate)
    clock.value = 10.5
    handler.release.set()
    try:
        assert gate.worker_waiting.wait(1.0)
        clock.value = 11.0
        exit_code = runtime._dispatch(runtime_module._Inbound(cancel.encoded))
        result = client.accept_inbound(_take_frame(writer))
    finally:
        gate.release_worker.set()

    assert exit_code is HostExitCode.OPERATION_TIMED_OUT
    assert result.message_type is MessageType.OPERATION_RESULT
    assert type(result.payload) is OperationResultPayload
    assert result.payload.outcome is OperationOutcome.TIMED_OUT
    assert gate.worker_exited.wait(1.0)
    completion = runtime._recorded_completion
    assert completion is not None
    assert completion.occurred_at == 11.0


@dataclass(frozen=True, slots=True)
class _ExplodingHandler:
    @property
    def descriptor(self) -> CapabilityDescriptor:
        return NOOP_CAPABILITY

    def execute(self, context: OperationContext) -> OperationResultPayload:
        del context
        raise RuntimeError("C:/private/canary-document.pdf")


def test_handler_exception_becomes_sanitized_failure() -> None:
    registry = RuntimeOperationRegistry((_ExplodingHandler(),))
    with _Harness(registry) as harness:
        harness.negotiate()
        _start(harness)
        result = _receive_result(harness)

        assert result.outcome is OperationOutcome.FAILED
        assert result.error is not None
        assert result.error.code is ErrorCode.WORKER_OPERATION_FAILED
        assert "canary-document" not in repr(result)


def test_malformed_frame_fails_closed_with_one_protocol_error() -> None:
    with _Harness() as harness:
        write_frame(harness.client_writer, b"{}")
        message = harness.receive()

        assert message.message_type is MessageType.PROTOCOL_ERROR
        assert harness.wait_for_exit() is HostExitCode.PROTOCOL_FAULT
        assert harness.client.state.phase is ConversationPhase.FAULTED

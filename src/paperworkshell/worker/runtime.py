"""Coordinator-owned runtime for one document-worker process launch."""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import Final
from uuid import UUID, uuid4

from paperworkshell.worker.operations import (
    DEFAULT_RUNTIME_OPERATION_REGISTRY,
    OperationContext,
    RuntimeOperationRegistry,
    failed_operation_result,
    timed_out_operation_result,
)
from paperworkshell.worker.protocol.bootstrap import BootstrapGrant
from paperworkshell.worker.protocol.codec import DEFAULT_OPERATION_REGISTRY
from paperworkshell.worker.protocol.errors import ProtocolError
from paperworkshell.worker.protocol.framing import (
    FrameError,
    FrameReader,
    FrameWriter,
    read_frame,
    write_frame,
)
from paperworkshell.worker.protocol.models import (
    MAX_MESSAGE_BYTES,
    CancelDisposition,
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
    ProtocolErrorPayload,
    RetryAction,
    SessionClosedPayload,
    SessionHelloPayload,
    SessionReadyPayload,
    SessionShutdownPayload,
)
from paperworkshell.worker.protocol.peer import PeerBinding, ProtocolPeer

_EVENT_QUEUE_SIZE: Final = 64
_MAX_IDLE_POLL_SECONDS: Final = 0.050


class HostExitCode(IntEnum):
    """Stable process exit codes without diagnostic content."""

    CLEAN = 0
    BOOTSTRAP_FAILED = 64
    ISOLATION_FAILED = 65
    TRANSPORT_CLOSED = 66
    PROTOCOL_FAULT = 67
    INTERNAL_FAULT = 68
    OPERATION_TIMED_OUT = 69


@dataclass(frozen=True, slots=True)
class _Inbound:
    raw: bytes


@dataclass(frozen=True, slots=True)
class _TransportEnded:
    faulted: bool


@dataclass(frozen=True, slots=True)
class _Progress:
    token: UUID
    payload: OperationProgressPayload


@dataclass(frozen=True, slots=True)
class _Completed:
    token: UUID
    payload: OperationResultPayload
    occurred_at: float


type _RuntimeEvent = _Inbound | _TransportEnded | _Progress | _Completed


@dataclass(slots=True)
class _ActiveExecution:
    token: UUID
    operation_id: UUID
    request_id: UUID
    deadline: float
    cancel_requested: threading.Event


class WorkerHostRuntime:
    """Run one authenticated session; exactly this actor mutates protocol state."""

    def __init__(
        self,
        grant: BootstrapGrant,
        reader: FrameReader,
        writer: FrameWriter,
        *,
        operations: RuntimeOperationRegistry = DEFAULT_RUNTIME_OPERATION_REGISTRY,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._operations = operations
        if operations.capabilities != DEFAULT_OPERATION_REGISTRY.capabilities:
            raise ValueError("runtime and protocol operation registries differ")
        self._monotonic = monotonic
        self._events: queue.Queue[_RuntimeEvent] = queue.Queue(maxsize=_EVENT_QUEUE_SIZE)
        self._stopping = threading.Event()
        self._active: _ActiveExecution | None = None
        binding = PeerBinding(
            grant.session_id,
            grant.worker_instance_id,
            grant.case_id,
            grant.session_capability,
        )
        self._peer = ProtocolPeer(
            binding,
            Direction.WORKER_TO_CLIENT,
            registered_capabilities=operations.capabilities,
        )

    def run(self) -> HostExitCode:
        """Serve until clean shutdown or the first fail-closed fault."""

        reader_thread = threading.Thread(
            target=self._read_loop,
            name="pws-worker-rpc-reader",
            daemon=True,
        )
        reader_thread.start()
        try:
            while not self._stopping.is_set():
                event = self._next_event()
                if event is None:
                    if self._deadline_expired():
                        self._complete_active(timed_out_operation_result())
                        return HostExitCode.OPERATION_TIMED_OUT
                    continue
                outcome = self._dispatch(event)
                if outcome is not None:
                    return outcome
            return HostExitCode.INTERNAL_FAULT
        except FrameError, ProtocolError:
            self._send_protocol_error()
            return HostExitCode.PROTOCOL_FAULT
        except Exception:
            return HostExitCode.INTERNAL_FAULT
        finally:
            self._stopping.set()

    def _next_event(self) -> _RuntimeEvent | None:
        timeout = _MAX_IDLE_POLL_SECONDS
        if self._active is not None:
            timeout = max(0.0, min(timeout, self._active.deadline - self._monotonic()))
        try:
            return self._events.get(timeout=timeout)
        except queue.Empty:
            return None

    def _read_loop(self) -> None:
        try:
            while not self._stopping.is_set():
                raw = read_frame(self._reader)
                if raw is None:
                    self._events.put(_TransportEnded(False))
                    return
                self._events.put(_Inbound(raw))
        except FrameError:
            self._events.put(_TransportEnded(True))
        except Exception:
            self._events.put(_TransportEnded(True))

    def _dispatch(self, event: _RuntimeEvent) -> HostExitCode | None:
        if type(event) is _TransportEnded:
            if event.faulted:
                self._send_protocol_error()
                return HostExitCode.PROTOCOL_FAULT
            return HostExitCode.TRANSPORT_CLOSED
        if type(event) is _Progress:
            return self._accept_progress(event)
        if type(event) is _Completed:
            return self._accept_completed(event)
        if type(event) is not _Inbound:
            return HostExitCode.INTERNAL_FAULT
        message = self._peer.accept_inbound(event.raw)
        if message.message_type is MessageType.SESSION_HELLO:
            payload = message.payload
            if type(payload) is not SessionHelloPayload or message.request_id is None:
                raise ProtocolError("RPC_PAYLOAD_MISMATCH", "payload", "hello is invalid")
            self._send(
                MessageType.SESSION_READY,
                SessionReadyPayload(1, self._operations.capabilities, MAX_MESSAGE_BYTES),
                request_id=message.request_id,
                operation_id=None,
            )
            return None
        if message.message_type is MessageType.OPERATION_START:
            self._start_operation(message.payload, message.request_id, message.operation_id)
            return None
        if message.message_type is MessageType.OPERATION_CANCEL:
            self._cancel_operation(message.payload, message.request_id, message.operation_id)
            return None
        if message.message_type is MessageType.SESSION_SHUTDOWN:
            if type(message.payload) is not SessionShutdownPayload or message.request_id is None:
                raise ProtocolError("RPC_PAYLOAD_MISMATCH", "payload", "shutdown is invalid")
            self._send(
                MessageType.SESSION_CLOSED,
                SessionClosedPayload(ClosedReason.SHUTDOWN_COMPLETE),
                request_id=message.request_id,
                operation_id=None,
            )
            return HostExitCode.CLEAN
        raise ProtocolError("RPC_INVALID_STATE", "message_type", "message is not dispatchable")

    def _start_operation(
        self,
        payload: object,
        request_id: UUID | None,
        operation_id: UUID | None,
    ) -> None:
        if type(payload) is not OperationStartPayload or request_id is None or operation_id is None:
            raise ProtocolError("RPC_PAYLOAD_MISMATCH", "payload", "operation start is invalid")
        descriptor = CapabilityDescriptor(payload.operation_kind, payload.operation_version)
        handler = self._operations.handler_for(descriptor)
        self._send(
            MessageType.OPERATION_ACCEPTED,
            OperationAcceptedPayload(
                payload.operation_kind,
                payload.operation_version,
                payload.requested_limits,
            ),
            request_id=request_id,
            operation_id=operation_id,
        )
        active = _ActiveExecution(
            token=uuid4(),
            operation_id=operation_id,
            request_id=request_id,
            deadline=self._monotonic() + (payload.requested_limits.wall_time_ms / 1000),
            cancel_requested=threading.Event(),
        )
        self._active = active

        def report_progress(progress: OperationProgressPayload) -> None:
            self._events.put(_Progress(active.token, progress))

        context = OperationContext(
            operation_id,
            payload.requested_limits,
            active.cancel_requested,
            report_progress,
        )

        def execute() -> None:
            try:
                result = handler.execute(context)
            except Exception:
                result = failed_operation_result()
            self._events.put(_Completed(active.token, result, self._monotonic()))

        threading.Thread(
            target=execute,
            name="pws-worker-operation",
            daemon=True,
        ).start()

    def _cancel_operation(
        self,
        payload: object,
        request_id: UUID | None,
        operation_id: UUID | None,
    ) -> None:
        if (
            type(payload) is not OperationCancelPayload
            or request_id is None
            or operation_id is None
        ):
            raise ProtocolError("RPC_PAYLOAD_MISMATCH", "payload", "cancellation is invalid")
        active = self._active
        if active is None or active.operation_id != operation_id:
            disposition = CancelDisposition.ALREADY_TERMINAL
        elif active.cancel_requested.is_set():
            disposition = CancelDisposition.ALREADY_REQUESTED
        else:
            active.cancel_requested.set()
            disposition = CancelDisposition.REQUESTED
        self._send(
            MessageType.OPERATION_CANCEL_ACK,
            OperationCancelAckPayload(disposition),
            request_id=request_id,
            operation_id=operation_id,
        )

    def _accept_progress(self, event: _Progress) -> HostExitCode | None:
        active = self._active
        if active is None or event.token != active.token:
            return None
        if self._monotonic() >= active.deadline:
            self._complete_active(timed_out_operation_result())
            return HostExitCode.OPERATION_TIMED_OUT
        self._send(
            MessageType.OPERATION_PROGRESS,
            event.payload,
            request_id=active.request_id,
            operation_id=active.operation_id,
        )
        return None

    def _accept_completed(self, event: _Completed) -> HostExitCode | None:
        active = self._active
        if active is None or event.token != active.token:
            return None
        if event.occurred_at > active.deadline:
            self._complete_active(timed_out_operation_result())
            return HostExitCode.OPERATION_TIMED_OUT
        restart_required = event.payload.error is not None and event.payload.error.restart_required
        self._complete_active(event.payload)
        if not restart_required:
            return None
        if event.payload.outcome is OperationOutcome.TIMED_OUT:
            return HostExitCode.OPERATION_TIMED_OUT
        return HostExitCode.INTERNAL_FAULT

    def _deadline_expired(self) -> bool:
        return self._active is not None and self._monotonic() >= self._active.deadline

    def _complete_active(self, payload: OperationResultPayload) -> None:
        active = self._active
        if active is None:
            return
        if payload.error is not None and payload.error.restart_required:
            active.cancel_requested.set()
        self._send(
            MessageType.OPERATION_RESULT,
            payload,
            request_id=active.request_id,
            operation_id=active.operation_id,
        )
        self._active = None

    def _send(
        self,
        message_type: MessageType,
        payload: MessagePayload,
        *,
        request_id: UUID | None,
        operation_id: UUID | None,
    ) -> None:
        prepared = self._peer.prepare_outbound(
            message_type,
            payload,
            request_id=request_id,
            operation_id=operation_id,
        )
        write_frame(self._writer, prepared.encoded)

    def _send_protocol_error(self) -> None:
        try:
            self._send(
                MessageType.PROTOCOL_ERROR,
                ProtocolErrorPayload(
                    ErrorPayload(
                        ErrorCode.RPC_MESSAGE_INVALID,
                        ErrorCategory.PROTOCOL,
                        RetryAction.RESTART_WORKER,
                        True,
                        (),
                    )
                ),
                request_id=None,
                operation_id=None,
            )
        except Exception:
            pass

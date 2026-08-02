"""Single-owner GUI-process-side coordinator for one restricted worker generation."""

from __future__ import annotations

import math
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Never, cast
from uuid import UUID, uuid4

from paperworkshell.worker.client.errors import (
    WorkerClientContext,
    WorkerClientError,
    WorkerClientErrorCode,
    WorkerFailure,
    reject_client,
)
from paperworkshell.worker.client.models import (
    MAXIMUM_CLIENT_TIMEOUT_SECONDS,
    WorkerClientSnapshot,
    WorkerLifecyclePhase,
    WorkerOperationResultSnapshot,
    WorkerOperationSnapshot,
    WorkerProgressSnapshot,
    WorkerTimeoutPolicy,
)
from paperworkshell.worker.client.process import (
    IsolationEvidence,
    ProcessLauncher,
    TransportReceive,
    TransportReceiveKind,
    WorkerLaunchRequest,
    WorkerProcess,
    WorkerResourcePolicy,
    WorkerTransport,
)
from paperworkshell.worker.protocol.codec import (
    DEFAULT_OPERATION_REGISTRY,
    OperationRegistry,
)
from paperworkshell.worker.protocol.errors import ProtocolError
from paperworkshell.worker.protocol.models import (
    PROTOCOL_VERSION,
    CancelReason,
    CapabilityDescriptor,
    Direction,
    MessagePayload,
    MessageType,
    OperationAcceptedPayload,
    OperationCancelAckPayload,
    OperationCancelPayload,
    OperationProgressPayload,
    OperationResultPayload,
    OperationStartPayload,
    ProtocolEnvelope,
    ProtocolErrorPayload,
    SessionCapability,
    SessionClosedPayload,
    SessionHelloPayload,
    SessionReadyPayload,
    SessionShutdownPayload,
    ShutdownReason,
    WorkerFaultPayload,
)
from paperworkshell.worker.protocol.peer import PeerBinding, ProtocolPeer
from paperworkshell.worker.protocol.state import ConversationPhase


def _default_capability_factory() -> SessionCapability:
    return SessionCapability(secrets.token_hex(32))


@dataclass(slots=True)
class _ActiveOperation:
    operation_id: UUID
    request_id: UUID
    operation_kind: str
    operation_version: int
    accepted: bool = False
    cancellation_requested: bool = False
    progress: WorkerProgressSnapshot | None = None

    def snapshot(self) -> WorkerOperationSnapshot:
        return WorkerOperationSnapshot(
            operation_id=self.operation_id,
            request_id=self.request_id,
            operation_kind=self.operation_kind,
            operation_version=self.operation_version,
            accepted=self.accepted,
            cancellation_requested=self.cancellation_requested,
            progress=self.progress,
        )


class WorkerClient:
    """Coordinate launch, RPC, deadlines, cancellation, shutdown, and restart.

    Construct and use this object on a dedicated lifecycle-coordinator thread,
    never the GUI event thread: OS-confirmed termination and ACL cleanup are
    intentionally synchronous.  All mutating inputs, including decoded transport
    events, are serialized by that owner.  ``query`` returns only cached immutable
    state and never polls a process or reads a pipe.  UI integration must deliver
    those snapshots through the application message boundary.
    """

    def __init__(
        self,
        launcher: ProcessLauncher,
        resource_policy: WorkerResourcePolicy,
        timeout_policy: WorkerTimeoutPolicy,
        *,
        operation_registry: OperationRegistry = DEFAULT_OPERATION_REGISTRY,
        required_capabilities: tuple[CapabilityDescriptor, ...] | None = None,
        clock: Callable[[], float] = time.monotonic,
        id_factory: Callable[[], UUID] = uuid4,
        capability_factory: Callable[[], SessionCapability] = _default_capability_factory,
    ) -> None:
        if (
            type(resource_policy) is not WorkerResourcePolicy
            or type(timeout_policy) is not WorkerTimeoutPolicy
        ):
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.CONFIGURATION,
            )
        if not isinstance(operation_registry, OperationRegistry):
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.CONFIGURATION,
            )
        capabilities = (
            operation_registry.capabilities
            if required_capabilities is None
            else required_capabilities
        )
        if (
            type(capabilities) is not tuple
            or not capabilities
            or any(type(value) is not CapabilityDescriptor for value in capabilities)
            or len(set(capabilities)) != len(capabilities)
        ):
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.CONFIGURATION,
            )
        registry_valid = True
        try:
            operation_registry.validate_capabilities(capabilities)
        except ProtocolError:
            registry_valid = False
        if not registry_valid:
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.CONFIGURATION,
            )

        self._launcher = launcher
        self._resource_policy = resource_policy
        self._timeout_policy = timeout_policy
        self._operation_registry = operation_registry
        self._required_capabilities = capabilities
        self._clock = clock
        self._id_factory = id_factory
        self._capability_factory = capability_factory
        self._owner_thread_id = threading.get_ident()

        self._generation = 0
        self._phase = WorkerLifecyclePhase.STOPPED
        self._case_id: UUID | None = None
        self._session_id: UUID | None = None
        self._worker_instance_id: UUID | None = None
        self._pid: int | None = None
        self._capabilities: tuple[CapabilityDescriptor, ...] = ()
        self._process: WorkerProcess | None = None
        self._transport: WorkerTransport | None = None
        self._cleanup_unconfirmed = False
        self._peer: ProtocolPeer | None = None
        self._active_operation: _ActiveOperation | None = None
        self._pending_cancel_request_ids: set[UUID] = set()
        self._last_result: WorkerOperationResultSnapshot | None = None
        self._last_failure: WorkerFailure | None = None
        self._handshake_deadline: float | None = None
        self._operation_deadline: float | None = None
        self._cancellation_deadline: float | None = None
        self._shutdown_deadline: float | None = None
        self._shutdown_after_operation = False
        self._pending_shutdown_reason: ShutdownReason | None = None

    def __repr__(self) -> str:
        return f"WorkerClient(generation={self._generation}, phase={self._phase.value!r})"

    @property
    def generation(self) -> int:
        """Return the current launch generation without touching the worker."""

        return self._generation

    def start(self, case_id: UUID) -> int:
        """Launch a fresh case-bound worker and send protocol negotiation."""

        self._require_owner()
        self._require_uuid4(case_id)
        if self._phase not in {WorkerLifecyclePhase.STOPPED, WorkerLifecyclePhase.FAULTED}:
            reject_client(WorkerClientErrorCode.STATE_INVALID, WorkerClientContext.STATE)
        if self._cleanup_unconfirmed:
            self._set_failure(
                WorkerClientErrorCode.TERMINATION_FAILED,
                WorkerClientContext.PROCESS,
            )
            reject_client(
                WorkerClientErrorCode.TERMINATION_FAILED,
                WorkerClientContext.PROCESS,
            )
        if self._phase is WorkerLifecyclePhase.FAULTED and not self._dispose_process(
            terminate=True
        ):
            self._set_failure(
                WorkerClientErrorCode.TERMINATION_FAILED,
                WorkerClientContext.PROCESS,
            )
            reject_client(
                WorkerClientErrorCode.TERMINATION_FAILED,
                WorkerClientContext.PROCESS,
            )
        if self._generation >= 9_007_199_254_740_991:
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.CONFIGURATION,
            )

        generation = self._generation + 1
        session_id = self._new_id()
        worker_instance_id = self._new_id()
        capability = self._new_capability()
        binding = PeerBinding(session_id, worker_instance_id, case_id, capability)
        peer = ProtocolPeer(
            binding,
            Direction.CLIENT_TO_WORKER,
            operation_registry=self._operation_registry,
        )
        request = WorkerLaunchRequest(
            case_id=case_id,
            session_id=session_id,
            worker_instance_id=worker_instance_id,
            session_capability=capability,
            resource_policy=self._resource_policy,
        )

        self._generation = generation
        self._phase = WorkerLifecyclePhase.STARTING
        self._case_id = case_id
        self._session_id = session_id
        self._worker_instance_id = worker_instance_id
        self._pid = None
        self._capabilities = ()
        self._peer = peer
        self._active_operation = None
        self._pending_cancel_request_ids.clear()
        self._last_result = None
        self._last_failure = None
        self._clear_deadlines()
        self._shutdown_after_operation = False
        self._pending_shutdown_reason = None

        launched: WorkerProcess | None = None
        try:
            launched = self._launcher.launch(request)
        except Exception:
            pass
        if launched is None:
            failure = self._fault(
                WorkerClientErrorCode.LAUNCH_FAILED,
                WorkerClientContext.LAUNCH,
                terminate=False,
            )
            reject_client(failure.code, failure.context)

        self._process = launched
        try:
            pid: object = None
            isolation: object = None
            transport: object = None
            launch_properties_valid = True
            try:
                pid = launched.pid
                isolation = launched.isolation
                transport = launched.transport
            except Exception:
                launch_properties_valid = False
            if (
                not launch_properties_valid
                or type(pid) is not int
                or pid < 1
                or type(isolation) is not IsolationEvidence
                or transport is None
            ):
                reject_client(
                    WorkerClientErrorCode.LAUNCH_FAILED,
                    WorkerClientContext.LAUNCH,
                )
            self._pid = pid
            self._transport = cast(WorkerTransport, transport)
            if not isolation.satisfies_required_boundary:
                reject_client(
                    WorkerClientErrorCode.ISOLATION_INSUFFICIENT,
                    WorkerClientContext.ISOLATION,
                )

            # AppContainer/profile/ACL setup is a separate trusted launch phase and
            # can legitimately serialize with another application instance.  The
            # handshake budget starts only once the isolated process and transport
            # have passed local validation and immediately before session.hello.
            self._handshake_deadline = self._deadline_after(self._timeout_policy.handshake_seconds)
            hello_request_id = self._new_id()
            self._send_outbound(
                MessageType.SESSION_HELLO,
                SessionHelloPayload(
                    supported_protocol_versions=(PROTOCOL_VERSION,),
                    required_capabilities=self._required_capabilities,
                ),
                request_id=hello_request_id,
                operation_id=None,
            )
        except WorkerClientError as error:
            # `_send_outbound()` performs its own fault transition after a
            # transport failure.  Every other failure in this post-launch window
            # still owns a live process and must cross the same cleanup boundary.
            if self._phase is WorkerLifecyclePhase.FAULTED:
                raise
            failure = self._fault(error.code, error.context, terminate=True)
            reject_client(failure.code, failure.context)
        except Exception:
            failure = self._fault(
                WorkerClientErrorCode.LAUNCH_FAILED,
                WorkerClientContext.LAUNCH,
                terminate=True,
            )
            reject_client(failure.code, failure.context)
        return generation

    def query(self) -> WorkerClientSnapshot:
        """Return a safe immutable snapshot using cached coordinator state only."""

        self._require_owner()
        operation = None
        if self._active_operation is not None:
            operation = self._active_operation.snapshot()
        return WorkerClientSnapshot(
            generation=self._generation,
            phase=self._phase,
            case_id=self._case_id,
            session_id=self._session_id,
            worker_instance_id=self._worker_instance_id,
            pid=self._pid,
            capabilities=self._capabilities,
            active_operation=operation,
            last_result=self._last_result,
            last_failure=self._last_failure,
        )

    def pump(self, timeout_seconds: float = 0.0) -> WorkerClientSnapshot:
        """Accept at most one transport event, preserving actor-order race semantics."""

        self._require_owner()
        timeout = self._validated_wait(timeout_seconds)
        if self._process is None or self._transport is None:
            return self.query()
        timeout = min(timeout, self._remaining_to_next_deadline())

        receive: object = None
        receive_failed = False
        try:
            receive = self._transport.receive_payload(timeout)
        except Exception:
            receive_failed = True
        if receive_failed or type(receive) is not TransportReceive:
            failure = self._fault(
                WorkerClientErrorCode.TRANSPORT_FAILED,
                WorkerClientContext.TRANSPORT,
                terminate=True,
            )
            reject_client(failure.code, failure.context)

        if receive.kind is TransportReceiveKind.PAYLOAD:
            payload = receive.payload
            if type(payload) is not bytes:
                self._protocol_fault()
            self.accept_payload(self._generation, payload)
            self.check_deadlines()
            return self.query()
        if receive.kind is TransportReceiveKind.CLOSED:
            self.notify_process_exit(self._generation)
            return self.query()

        process_exit: object = None
        poll_failed = False
        try:
            process_exit = self._process.poll()
        except Exception:
            poll_failed = True
        if poll_failed or (process_exit is not None and (type(process_exit) is not int)):
            self._fault(
                WorkerClientErrorCode.PROCESS_EXITED,
                WorkerClientContext.PROCESS,
                terminate=True,
            )
        elif process_exit is not None:
            self.notify_process_exit(self._generation)
        else:
            self.check_deadlines()
        return self.query()

    def accept_payload(self, generation: int, payload: bytes) -> None:
        """Decode and apply one framed payload for the named live generation."""

        self._require_owner()
        self._require_generation(generation)
        if self._phase in {WorkerLifecyclePhase.STOPPED, WorkerLifecyclePhase.FAULTED}:
            reject_client(WorkerClientErrorCode.STATE_INVALID, WorkerClientContext.STATE)
        if type(payload) is not bytes or self._peer is None:
            self._protocol_fault()
        message: ProtocolEnvelope | None = None
        try:
            message = self._peer.accept_inbound(payload)
        except Exception:
            pass
        if message is None:
            self._protocol_fault()
        self._dispatch_inbound(message)

    def start_operation(self, payload: OperationStartPayload) -> UUID:
        """Start the one negotiated operation and arm its broker wall deadline."""

        self._require_owner()
        if (
            self._phase is not WorkerLifecyclePhase.READY
            or type(payload) is not OperationStartPayload
        ):
            reject_client(WorkerClientErrorCode.STATE_INVALID, WorkerClientContext.OPERATION)
        operation_id = self._new_id()
        request_id = self._new_id()
        self._send_outbound(
            MessageType.OPERATION_START,
            payload,
            request_id=request_id,
            operation_id=operation_id,
        )
        self._active_operation = _ActiveOperation(
            operation_id=operation_id,
            request_id=request_id,
            operation_kind=payload.operation_kind,
            operation_version=payload.operation_version,
        )
        self._phase = WorkerLifecyclePhase.BUSY
        self._operation_deadline = self._deadline_after(
            payload.requested_limits.wall_time_ms / 1_000.0
        )
        return operation_id

    def cancel(self, reason: CancelReason = CancelReason.USER_REQUESTED) -> UUID:
        """Request cooperative cancellation; acknowledgement is not terminal."""

        self._require_owner()
        if type(reason) is not CancelReason:
            reject_client(WorkerClientErrorCode.STATE_INVALID, WorkerClientContext.CANCELLATION)
        return self._request_cancel(reason)

    def shutdown(self, reason: ShutdownReason = ShutdownReason.USER_REQUESTED) -> None:
        """Begin graceful shutdown, cancelling active work first when required."""

        self._require_owner()
        if type(reason) is not ShutdownReason:
            reject_client(WorkerClientErrorCode.STATE_INVALID, WorkerClientContext.SHUTDOWN)
        if self._phase in {WorkerLifecyclePhase.STOPPED, WorkerLifecyclePhase.STOPPING}:
            return
        if self._phase is WorkerLifecyclePhase.FAULTED:
            self.terminate()
            return
        if self._phase is WorkerLifecyclePhase.STARTING:
            self.terminate()
            return
        if self._phase is WorkerLifecyclePhase.READY:
            self._begin_shutdown(reason)
            return
        if not self._shutdown_after_operation:
            self._pending_shutdown_reason = reason
        self._shutdown_after_operation = True
        if self._phase is WorkerLifecyclePhase.BUSY:
            self._request_cancel(CancelReason.SHUTDOWN)

    def terminate(self) -> None:
        """Force-stop the whole worker process tree without waiting for protocol close."""

        self._require_owner()
        if self._phase is WorkerLifecyclePhase.STOPPED:
            return
        disposed = self._dispose_process(terminate=True)
        self._peer = None
        self._pid = None
        self._capabilities = ()
        self._active_operation = None
        self._pending_cancel_request_ids.clear()
        self._clear_deadlines()
        self._shutdown_after_operation = False
        self._pending_shutdown_reason = None
        if disposed:
            self._phase = WorkerLifecyclePhase.STOPPED
        else:
            self._set_failure(
                WorkerClientErrorCode.TERMINATION_FAILED,
                WorkerClientContext.PROCESS,
            )
            reject_client(
                WorkerClientErrorCode.TERMINATION_FAILED,
                WorkerClientContext.PROCESS,
            )

    def restart(self) -> int:
        """Discard all launch authority and negotiate a fresh worker generation."""

        self._require_owner()
        case_id = self._case_id
        if case_id is None:
            reject_client(WorkerClientErrorCode.STATE_INVALID, WorkerClientContext.STATE)
        if not self._dispose_process(terminate=True):
            self._set_failure(
                WorkerClientErrorCode.TERMINATION_FAILED,
                WorkerClientContext.PROCESS,
            )
            reject_client(
                WorkerClientErrorCode.TERMINATION_FAILED,
                WorkerClientContext.PROCESS,
            )
        self._peer = None
        self._phase = WorkerLifecyclePhase.STOPPED
        self._pid = None
        self._capabilities = ()
        self._active_operation = None
        self._pending_cancel_request_ids.clear()
        self._clear_deadlines()
        self._shutdown_after_operation = False
        self._pending_shutdown_reason = None
        return self.start(case_id)

    def notify_process_exit(self, generation: int) -> None:
        """Apply an observed process-exit event without exposing its raw exit status."""

        self._require_owner()
        self._require_generation(generation)
        if self._phase in {WorkerLifecyclePhase.STOPPED, WorkerLifecyclePhase.FAULTED}:
            return
        self._fault(
            WorkerClientErrorCode.PROCESS_EXITED,
            WorkerClientContext.PROCESS,
            terminate=False,
        )

    def check_deadlines(self, observed_time: float | None = None) -> None:
        """Apply one ordered monotonic deadline event if its deadline has passed."""

        self._require_owner()
        now = self._now() if observed_time is None else self._validated_time(observed_time)
        deadline = self._next_deadline()
        if deadline is None or now < deadline:
            return
        if self._phase is WorkerLifecyclePhase.STARTING:
            code = WorkerClientErrorCode.HANDSHAKE_TIMED_OUT
            context = WorkerClientContext.HANDSHAKE
        elif self._phase is WorkerLifecyclePhase.STOPPING:
            code = WorkerClientErrorCode.SHUTDOWN_TIMED_OUT
            context = WorkerClientContext.SHUTDOWN
        elif (
            self._phase is WorkerLifecyclePhase.CANCELLING
            and self._cancellation_deadline is not None
            and self._cancellation_deadline <= deadline
        ):
            code = WorkerClientErrorCode.CANCELLATION_TIMED_OUT
            context = WorkerClientContext.CANCELLATION
        else:
            code = WorkerClientErrorCode.OPERATION_TIMED_OUT
            context = WorkerClientContext.OPERATION
        self._fault(code, context, terminate=True)

    def _dispatch_inbound(self, message: ProtocolEnvelope) -> None:
        message_type = message.message_type
        payload = message.payload
        if message_type is MessageType.SESSION_READY:
            if (
                self._phase is not WorkerLifecyclePhase.STARTING
                or type(payload) is not SessionReadyPayload
            ):
                self._protocol_fault()
            self._phase = WorkerLifecyclePhase.READY
            self._capabilities = payload.capabilities
            self._handshake_deadline = None
            return
        if message_type is MessageType.OPERATION_ACCEPTED:
            active = self._matching_active(message)
            if type(payload) is not OperationAcceptedPayload:
                self._protocol_fault()
            active.accepted = True
            return
        if message_type is MessageType.OPERATION_PROGRESS:
            active = self._matching_active(message)
            if type(payload) is not OperationProgressPayload:
                self._protocol_fault()
            active.progress = WorkerProgressSnapshot(
                stage=payload.stage_code,
                completed_units=payload.completed_units,
                total_units=payload.total_units,
                unit=payload.unit,
            )
            return
        if message_type is MessageType.OPERATION_CANCEL_ACK:
            if type(payload) is not OperationCancelAckPayload or message.request_id is None:
                self._protocol_fault()
            self._pending_cancel_request_ids.discard(message.request_id)
            if (
                self._shutdown_after_operation
                and self._active_operation is None
                and not self._pending_cancel_request_ids
            ):
                self._begin_shutdown(self._pending_shutdown_reason or ShutdownReason.USER_REQUESTED)
            elif (
                self._active_operation is None
                and not self._pending_cancel_request_ids
                and self._phase is WorkerLifecyclePhase.CANCELLING
            ):
                self._phase = WorkerLifecyclePhase.READY
                self._cancellation_deadline = None
            return
        if message_type is MessageType.OPERATION_RESULT:
            active = self._matching_active(message)
            if type(payload) is not OperationResultPayload:
                self._protocol_fault()
            self._last_result = WorkerOperationResultSnapshot(
                generation=self._generation,
                operation_id=active.operation_id,
                outcome=payload.outcome,
                outputs=payload.outputs,
                error_code=None if payload.error is None else payload.error.code,
            )
            self._active_operation = None
            self._operation_deadline = None
            if (
                self._peer is None
                or self._peer.state.phase is ConversationPhase.FAULTED
                or (payload.error is not None and payload.error.restart_required)
            ):
                self._fault(
                    WorkerClientErrorCode.RESTART_REQUIRED,
                    WorkerClientContext.OPERATION,
                    terminate=True,
                )
                return
            if self._shutdown_after_operation:
                if self._pending_cancel_request_ids:
                    self._phase = WorkerLifecyclePhase.CANCELLING
                else:
                    self._begin_shutdown(
                        self._pending_shutdown_reason or ShutdownReason.USER_REQUESTED
                    )
            else:
                if self._pending_cancel_request_ids:
                    self._phase = WorkerLifecyclePhase.CANCELLING
                else:
                    self._phase = WorkerLifecyclePhase.READY
                    self._cancellation_deadline = None
            return
        if message_type is MessageType.SESSION_CLOSED:
            if (
                self._phase is not WorkerLifecyclePhase.STOPPING
                or type(payload) is not SessionClosedPayload
            ):
                self._protocol_fault()
            disposed = self._dispose_process(terminate=False)
            self._pid = None
            self._capabilities = ()
            self._clear_deadlines()
            self._shutdown_after_operation = False
            self._pending_shutdown_reason = None
            if disposed:
                self._phase = WorkerLifecyclePhase.STOPPED
            else:
                self._set_failure(
                    WorkerClientErrorCode.TERMINATION_FAILED,
                    WorkerClientContext.PROCESS,
                )
            return
        if message_type is MessageType.PROTOCOL_ERROR:
            if type(payload) is not ProtocolErrorPayload:
                self._protocol_fault()
            self._fault(
                WorkerClientErrorCode.PROTOCOL_FAILED,
                WorkerClientContext.PROTOCOL,
                terminate=True,
            )
            return
        if message_type is MessageType.WORKER_FAULT:
            if type(payload) is not WorkerFaultPayload:
                self._protocol_fault()
            self._fault(
                WorkerClientErrorCode.RESTART_REQUIRED,
                WorkerClientContext.PROCESS,
                terminate=True,
            )
            return
        self._protocol_fault()

    def _matching_active(self, message: ProtocolEnvelope) -> _ActiveOperation:
        active = self._active_operation
        if (
            active is None
            or message.operation_id != active.operation_id
            or message.request_id != active.request_id
            or self._phase not in {WorkerLifecyclePhase.BUSY, WorkerLifecyclePhase.CANCELLING}
        ):
            self._protocol_fault()
        return active

    def _request_cancel(self, reason: CancelReason) -> UUID:
        active = self._active_operation
        if active is None or self._phase not in {
            WorkerLifecyclePhase.BUSY,
            WorkerLifecyclePhase.CANCELLING,
        }:
            reject_client(WorkerClientErrorCode.STATE_INVALID, WorkerClientContext.CANCELLATION)
        request_id = self._new_id()
        self._send_outbound(
            MessageType.OPERATION_CANCEL,
            OperationCancelPayload(reason),
            request_id=request_id,
            operation_id=active.operation_id,
        )
        self._pending_cancel_request_ids.add(request_id)
        active.cancellation_requested = True
        self._phase = WorkerLifecyclePhase.CANCELLING
        if self._cancellation_deadline is None:
            self._cancellation_deadline = self._deadline_after(
                self._timeout_policy.cancellation_grace_seconds
            )
        return request_id

    def _begin_shutdown(self, reason: ShutdownReason) -> None:
        if (
            self._phase
            not in {
                WorkerLifecyclePhase.READY,
                WorkerLifecyclePhase.CANCELLING,
            }
            or self._active_operation is not None
        ):
            reject_client(WorkerClientErrorCode.STATE_INVALID, WorkerClientContext.SHUTDOWN)
        request_id = self._new_id()
        self._send_outbound(
            MessageType.SESSION_SHUTDOWN,
            SessionShutdownPayload(reason),
            request_id=request_id,
            operation_id=None,
        )
        self._phase = WorkerLifecyclePhase.STOPPING
        self._cancellation_deadline = None
        self._shutdown_deadline = self._deadline_after(self._timeout_policy.shutdown_grace_seconds)

    def _send_outbound(
        self,
        message_type: MessageType,
        payload: MessagePayload,
        *,
        request_id: UUID | None,
        operation_id: UUID | None,
    ) -> None:
        peer = self._peer
        transport = self._transport
        if peer is None or transport is None:
            reject_client(WorkerClientErrorCode.STATE_INVALID, WorkerClientContext.STATE)
        prepared = None
        try:
            prepared = peer.prepare_outbound(
                message_type,
                payload,
                request_id=request_id,
                operation_id=operation_id,
            )
        except Exception:
            pass
        if prepared is None:
            reject_client(WorkerClientErrorCode.STATE_INVALID, WorkerClientContext.PROTOCOL)
        send_failed = False
        try:
            transport.send_payload(prepared.encoded)
        except Exception:
            send_failed = True
        if send_failed:
            failure = self._fault(
                WorkerClientErrorCode.TRANSPORT_FAILED,
                WorkerClientContext.TRANSPORT,
                terminate=True,
            )
            reject_client(failure.code, failure.context)

    def _protocol_fault(self) -> Never:
        failure = self._fault(
            WorkerClientErrorCode.PROTOCOL_FAILED,
            WorkerClientContext.PROTOCOL,
            terminate=True,
        )
        reject_client(failure.code, failure.context)

    def _fault(
        self,
        code: WorkerClientErrorCode,
        context: WorkerClientContext,
        *,
        terminate: bool,
    ) -> WorkerFailure:
        disposed = self._dispose_process(terminate=terminate)
        self._peer = None
        self._pid = None
        self._capabilities = ()
        self._active_operation = None
        self._pending_cancel_request_ids.clear()
        self._clear_deadlines()
        self._shutdown_after_operation = False
        self._pending_shutdown_reason = None
        if not disposed:
            code = WorkerClientErrorCode.TERMINATION_FAILED
            context = WorkerClientContext.PROCESS
        return self._set_failure(code, context)

    def _set_failure(
        self,
        code: WorkerClientErrorCode,
        context: WorkerClientContext,
    ) -> WorkerFailure:
        failure = WorkerFailure(code=code, context=context)
        self._last_failure = failure
        self._phase = WorkerLifecyclePhase.FAULTED
        return failure

    def _dispose_process(self, *, terminate: bool) -> bool:
        if self._cleanup_unconfirmed:
            return False
        process = self._process
        transport = self._transport
        process_closed = True
        transport_closed = True
        if process is not None and terminate:
            try:
                process.terminate_tree()
            except Exception:
                # A successful mandatory close remains authoritative: concrete
                # process adapters must confirm the tree and all launch authority
                # are gone before returning from close().
                pass
        if process is not None:
            try:
                process.close()
            except Exception:
                process_closed = False
        if transport is not None:
            try:
                transport.close()
            except Exception:
                transport_closed = False
        success = process_closed and transport_closed
        if success:
            self._process = None
            self._transport = None
        else:
            # Never launch another generation after any cleanup authority has
            # failed to prove closure.  Retain references for process-lifetime
            # kill-on-close protection, but make the uncertainty sticky rather
            # than treating a later idempotent no-op as fresh evidence.
            self._cleanup_unconfirmed = True
        return success

    def _next_deadline(self) -> float | None:
        candidates: list[float] = []
        if self._phase is WorkerLifecyclePhase.STARTING and self._handshake_deadline is not None:
            candidates.append(self._handshake_deadline)
        if (
            self._phase
            in {
                WorkerLifecyclePhase.BUSY,
                WorkerLifecyclePhase.CANCELLING,
            }
            and self._operation_deadline is not None
        ):
            candidates.append(self._operation_deadline)
        if (
            self._phase is WorkerLifecyclePhase.CANCELLING
            and self._cancellation_deadline is not None
        ):
            candidates.append(self._cancellation_deadline)
        if self._phase is WorkerLifecyclePhase.STOPPING and self._shutdown_deadline is not None:
            candidates.append(self._shutdown_deadline)
        return None if not candidates else min(candidates)

    def _remaining_to_next_deadline(self) -> float:
        deadline = self._next_deadline()
        if deadline is None:
            return MAXIMUM_CLIENT_TIMEOUT_SECONDS
        return max(0.0, deadline - self._now())

    def _clear_deadlines(self) -> None:
        self._handshake_deadline = None
        self._operation_deadline = None
        self._cancellation_deadline = None
        self._shutdown_deadline = None

    def _deadline_after(self, seconds: float) -> float:
        deadline = self._now() + seconds
        if not math.isfinite(deadline):
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.CONFIGURATION,
            )
        return deadline

    def _now(self) -> float:
        value: object = None
        try:
            value = self._clock()
        except Exception:
            pass
        return self._validated_time(value)

    @staticmethod
    def _validated_time(value: object) -> float:
        numeric: float | None = None
        if type(value) is int:
            try:
                numeric = float(value)
            except OverflowError:
                pass
        elif type(value) is float:
            numeric = value
        if numeric is None or not math.isfinite(numeric) or numeric < 0:
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.CONFIGURATION,
            )
        return numeric

    @staticmethod
    def _validated_wait(value: object) -> float:
        timeout = WorkerClient._validated_time(value)
        if timeout > MAXIMUM_CLIENT_TIMEOUT_SECONDS:
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.CONFIGURATION,
            )
        return timeout

    def _new_id(self) -> UUID:
        value: object = None
        try:
            value = self._id_factory()
        except Exception:
            pass
        return self._require_uuid4(value)

    def _new_capability(self) -> SessionCapability:
        value: object = None
        try:
            value = self._capability_factory()
        except Exception:
            pass
        if type(value) is not SessionCapability:
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.CONFIGURATION,
            )
        return value

    @staticmethod
    def _require_uuid4(value: object) -> UUID:
        if type(value) is not UUID or value.version != 4:
            reject_client(
                WorkerClientErrorCode.CONFIGURATION_INVALID,
                WorkerClientContext.CONFIGURATION,
            )
        return value

    def _require_generation(self, generation: object) -> None:
        if type(generation) is not int or generation != self._generation:
            reject_client(
                WorkerClientErrorCode.GENERATION_STALE,
                WorkerClientContext.GENERATION,
            )

    def _require_owner(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            reject_client(
                WorkerClientErrorCode.OWNER_THREAD_REQUIRED,
                WorkerClientContext.OWNER,
            )

"""Single-owner peer coordination for one worker RPC conversation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final
from uuid import UUID, uuid4

from paperworkshell.worker.protocol.codec import (
    DEFAULT_OPERATION_REGISTRY,
    OperationRegistry,
    decode_message,
    encode_message,
)
from paperworkshell.worker.protocol.errors import reject
from paperworkshell.worker.protocol.models import (
    MESSAGE_VERSION,
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    CapabilityDescriptor,
    Direction,
    MessagePayload,
    MessageType,
    ProtocolEnvelope,
    SessionCapability,
)
from paperworkshell.worker.protocol.state import ConversationState

_OPPOSITE_DIRECTION: Final = {
    Direction.CLIENT_TO_WORKER: Direction.WORKER_TO_CLIENT,
    Direction.WORKER_TO_CLIENT: Direction.CLIENT_TO_WORKER,
}


@dataclass(frozen=True, slots=True)
class PeerBinding:
    """Public identities and one redacted capability for a protocol peer."""

    session_id: UUID
    worker_instance_id: UUID
    case_id: UUID
    session_capability: SessionCapability = field(repr=False)


@dataclass(frozen=True, slots=True)
class PreparedMessage:
    """A state-validated outbound envelope and its canonical bytes."""

    envelope: ProtocolEnvelope
    encoded: bytes = field(repr=False)


class ProtocolPeer:
    """Advance inbound and outbound messages from one coordinator actor only."""

    def __init__(
        self,
        binding: PeerBinding,
        local_direction: Direction,
        *,
        operation_registry: OperationRegistry = DEFAULT_OPERATION_REGISTRY,
        registered_capabilities: tuple[CapabilityDescriptor, ...] | None = None,
    ) -> None:
        if type(binding) is not PeerBinding:
            reject("RPC_INVALID_TYPE", "peer", "a peer binding is required")
        if type(local_direction) is not Direction:
            reject("RPC_INVALID_ENUM", "peer", "a peer direction is required")
        capabilities = (
            operation_registry.capabilities
            if registered_capabilities is None
            else registered_capabilities
        )
        self._binding = binding
        self._local_direction = local_direction
        self._operation_registry = operation_registry
        self._state = ConversationState(
            binding.session_id,
            binding.worker_instance_id,
            binding.session_capability,
            binding.case_id,
            registered_capabilities=capabilities,
        )

    @property
    def state(self) -> ConversationState:
        """Return the current immutable conversation snapshot."""

        return self._state

    @property
    def binding(self) -> PeerBinding:
        """Return the launch binding without exposing its capability in representations."""

        return self._binding

    def prepare_outbound(
        self,
        message_type: MessageType,
        payload: MessagePayload,
        *,
        request_id: UUID | None,
        operation_id: UUID | None,
    ) -> PreparedMessage:
        """Validate, encode, and atomically advance one outbound message."""

        sequence = (
            self._state.next_client_sequence
            if self._local_direction is Direction.CLIENT_TO_WORKER
            else self._state.next_worker_sequence
        )
        envelope = ProtocolEnvelope(
            protocol=PROTOCOL_NAME,
            protocol_version=PROTOCOL_VERSION,
            message_version=MESSAGE_VERSION,
            message_type=message_type,
            direction=self._local_direction,
            session_id=self._binding.session_id,
            worker_instance_id=self._binding.worker_instance_id,
            session_capability=self._binding.session_capability,
            case_id=self._binding.case_id,
            message_id=uuid4(),
            sender_sequence=sequence,
            request_id=request_id,
            operation_id=operation_id,
            payload=payload,
        )
        advanced = self._state.accept(envelope)
        encoded = encode_message(envelope, operation_registry=self._operation_registry)
        self._state = advanced
        return PreparedMessage(envelope, encoded)

    def accept_inbound(self, raw: bytes) -> ProtocolEnvelope:
        """Decode, authenticate, direction-check, and advance one inbound message."""

        message = decode_message(
            raw,
            expected_capability=self._binding.session_capability,
            operation_registry=self._operation_registry,
        )
        expected = _OPPOSITE_DIRECTION[self._local_direction]
        if message.direction is not expected:
            reject(
                "RPC_DIRECTION_MISMATCH",
                "direction",
                "message direction is invalid for this peer",
            )
        advanced = self._state.accept(message)
        self._state = advanced
        return message

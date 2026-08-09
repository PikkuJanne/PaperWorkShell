"""Tests for the single-owner worker protocol peer."""

from __future__ import annotations

from uuid import UUID

import pytest

from paperworkshell.worker.protocol.errors import ProtocolError
from paperworkshell.worker.protocol.models import (
    MAX_MESSAGE_BYTES,
    NOOP_CAPABILITY,
    Direction,
    MessageType,
    SessionCapability,
    SessionHelloPayload,
    SessionReadyPayload,
)
from paperworkshell.worker.protocol.peer import PeerBinding, ProtocolPeer
from paperworkshell.worker.protocol.state import ConversationPhase


def _uuid(value: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{value:012x}")


def _peers() -> tuple[ProtocolPeer, ProtocolPeer]:
    binding = PeerBinding(_uuid(1), _uuid(2), _uuid(3), SessionCapability("a" * 64))
    return (
        ProtocolPeer(binding, Direction.CLIENT_TO_WORKER),
        ProtocolPeer(binding, Direction.WORKER_TO_CLIENT),
    )


def test_peers_validate_both_outbound_and_inbound_negotiation() -> None:
    client, worker = _peers()
    request_id = _uuid(10)
    hello = client.prepare_outbound(
        MessageType.SESSION_HELLO,
        SessionHelloPayload((1,), (NOOP_CAPABILITY,)),
        request_id=request_id,
        operation_id=None,
    )
    worker.accept_inbound(hello.encoded)
    ready = worker.prepare_outbound(
        MessageType.SESSION_READY,
        SessionReadyPayload(1, (NOOP_CAPABILITY,), MAX_MESSAGE_BYTES),
        request_id=request_id,
        operation_id=None,
    )
    client.accept_inbound(ready.encoded)

    assert client.state.phase is ConversationPhase.READY
    assert worker.state.phase is ConversationPhase.READY


def test_peer_rejects_a_same_direction_message_without_advancing() -> None:
    client, _worker = _peers()
    other_client, _ = _peers()
    prepared = other_client.prepare_outbound(
        MessageType.SESSION_HELLO,
        SessionHelloPayload((1,), ()),
        request_id=_uuid(11),
        operation_id=None,
    )

    with pytest.raises(ProtocolError) as raised:
        client.accept_inbound(prepared.encoded)

    assert raised.value.code == "RPC_DIRECTION_MISMATCH"
    assert client.state.phase is ConversationPhase.NEW

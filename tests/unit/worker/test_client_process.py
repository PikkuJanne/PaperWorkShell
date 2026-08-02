"""Unit tests for the GUI-safe framed worker transport and launch contracts."""

from __future__ import annotations

import io
import threading
from collections.abc import Buffer

import pytest

from paperworkshell.worker.client.errors import WorkerClientError, WorkerClientErrorCode
from paperworkshell.worker.client.process import (
    TransportReceiveKind,
    create_worker_transport,
)
from paperworkshell.worker.protocol.framing import encode_frame


class BlockingReader:
    def __init__(self) -> None:
        self._closed = threading.Event()

    def read(self, _size: int, /) -> bytes:
        self._closed.wait()
        return b""

    def close(self) -> None:
        self._closed.set()


class FailingWriter:
    def write(self, _data: Buffer, /) -> int | None:
        raise OSError("C:\\private\\applicant-name.pdf")

    def flush(self) -> None:
        raise OSError("C:\\private\\applicant-name.pdf")

    def close(self) -> None:
        return None


def test_default_transport_reads_one_frame_then_reports_clean_close() -> None:
    reader = io.BytesIO(encode_frame(b"bounded-payload"))
    writer = io.BytesIO()
    transport = create_worker_transport(reader, writer)

    received = transport.receive_payload(1.0)
    closed = transport.receive_payload(1.0)

    assert received.kind is TransportReceiveKind.PAYLOAD
    assert received.payload == b"bounded-payload"
    assert closed.kind is TransportReceiveKind.CLOSED
    assert received.payload not in repr(received).encode("utf-8")
    transport.close()


def test_default_transport_writes_canonical_frame_and_can_time_out() -> None:
    reader = BlockingReader()
    writer = io.BytesIO()
    transport = create_worker_transport(reader, writer)

    timed_out = transport.receive_payload(0.0)
    transport.send_payload(b"outbound")

    assert timed_out.kind is TransportReceiveKind.TIMED_OUT
    assert writer.getvalue() == encode_frame(b"outbound")
    transport.close()


def test_writer_exception_is_mapped_without_retaining_private_text() -> None:
    prohibited = "applicant-name.pdf"
    transport = create_worker_transport(BlockingReader(), FailingWriter())

    with pytest.raises(WorkerClientError) as raised:
        transport.send_payload(b"outbound")

    rendered = f"{raised.value!s} {raised.value!r}"
    assert raised.value.code is WorkerClientErrorCode.TRANSPORT_FAILED
    assert prohibited not in rendered
    assert raised.value.__context__ is None
    transport.close()


def test_malformed_frame_is_a_sanitized_terminal_transport_failure() -> None:
    transport = create_worker_transport(io.BytesIO(b"\x00\x00\x00\x00"), io.BytesIO())

    with pytest.raises(WorkerClientError) as raised:
        transport.receive_payload(1.0)

    assert raised.value.code is WorkerClientErrorCode.TRANSPORT_FAILED
    assert raised.value.__context__ is None
    transport.close()

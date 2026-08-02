"""Unit tests for the GUI-safe framed worker transport and launch contracts."""

from __future__ import annotations

import io
import threading
import time
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


class SignallingWriter:
    def __init__(self) -> None:
        self.buffer = io.BytesIO()
        self.written = threading.Event()

    def write(self, data: Buffer, /) -> int | None:
        result = self.buffer.write(data)
        self.written.set()
        return result

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


class BlockingWriter:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.released = threading.Event()

    def write(self, data: Buffer, /) -> int | None:
        self.entered.set()
        self.released.wait()
        return len(bytes(data))

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.released.set()


class FailOnceCloseBytesIO(io.BytesIO):
    def __init__(self, initial_bytes: bytes = b"") -> None:
        super().__init__(initial_bytes)
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        if self.close_calls == 1:
            raise OSError("C:\\private\\close-canary.pdf")
        super().close()


class BlockingRawReader(io.RawIOBase):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.released = threading.Event()

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Buffer, /) -> int | None:
        del buffer
        self.entered.set()
        self.released.wait()
        return 0


class BlockingRawWriter(io.RawIOBase):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.released = threading.Event()

    def writable(self) -> bool:
        return True

    def write(self, data: Buffer, /) -> int | None:
        size = len(bytes(data))
        self.entered.set()
        self.released.wait()
        return size


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
    writer = SignallingWriter()
    transport = create_worker_transport(reader, writer)

    timed_out = transport.receive_payload(0.0)
    transport.send_payload(b"outbound")

    assert timed_out.kind is TransportReceiveKind.TIMED_OUT
    assert writer.written.wait(1.0)
    assert writer.buffer.getvalue() == encode_frame(b"outbound")
    transport.close()


def test_writer_exception_is_mapped_without_retaining_private_text() -> None:
    prohibited = "applicant-name.pdf"
    transport = create_worker_transport(BlockingReader(), FailingWriter())

    transport.send_payload(b"outbound")
    with pytest.raises(WorkerClientError) as raised:
        transport.receive_payload(1.0)

    rendered = f"{raised.value!s} {raised.value!r}"
    assert raised.value.code is WorkerClientErrorCode.TRANSPORT_FAILED
    assert prohibited not in rendered
    assert raised.value.__context__ is None
    transport.close()


def test_blocked_worker_input_never_blocks_the_gui_sending_thread() -> None:
    writer = BlockingWriter()
    transport = create_worker_transport(BlockingReader(), writer)
    completed = threading.Event()

    def send() -> None:
        transport.send_payload(b"outbound")
        completed.set()

    thread = threading.Thread(target=send)
    thread.start()
    thread.join(timeout=0.5)

    assert completed.is_set()
    assert writer.entered.wait(1.0)
    transport.close()
    thread.join(timeout=1.0)


def test_endpoint_close_failure_is_sanitized_sticky_and_attempts_both_ends() -> None:
    reader = FailOnceCloseBytesIO()
    writer = FailOnceCloseBytesIO()
    transport = create_worker_transport(reader, writer)
    assert transport.receive_payload(1.0).kind is TransportReceiveKind.CLOSED

    with pytest.raises(WorkerClientError) as first:
        transport.close()
    with pytest.raises(WorkerClientError) as second:
        transport.close()

    assert first.value.code is WorkerClientErrorCode.TRANSPORT_FAILED
    assert second.value.code is WorkerClientErrorCode.TRANSPORT_FAILED
    assert "close-canary" not in f"{first.value!s} {first.value!r}"
    assert reader.close_calls == 2
    assert writer.close_calls == 2
    assert reader.closed
    assert writer.closed


def test_buffered_endpoint_locks_cannot_make_transport_close_unbounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "paperworkshell.worker.client.process.TRANSPORT_CLOSE_SECONDS",
        0.05,
    )
    raw_reader = BlockingRawReader()
    raw_writer = BlockingRawWriter()
    transport = create_worker_transport(
        io.BufferedReader(raw_reader),
        io.BufferedWriter(raw_writer),
    )
    transport.send_payload(b"outbound")
    assert raw_reader.entered.wait(1.0)
    assert raw_writer.entered.wait(1.0)

    started = time.monotonic()
    with pytest.raises(WorkerClientError) as raised:
        transport.close()
    elapsed = time.monotonic() - started

    assert raised.value.code is WorkerClientErrorCode.TRANSPORT_FAILED
    assert elapsed < 0.5
    raw_reader.released.set()
    raw_writer.released.set()


def test_malformed_frame_is_a_sanitized_terminal_transport_failure() -> None:
    transport = create_worker_transport(io.BytesIO(b"\x00\x00\x00\x00"), io.BytesIO())

    with pytest.raises(WorkerClientError) as raised:
        transport.receive_payload(1.0)

    assert raised.value.code is WorkerClientErrorCode.TRANSPORT_FAILED
    assert raised.value.__context__ is None
    transport.close()

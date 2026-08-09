"""Bounded length-prefix framing for worker RPC byte streams.

The framing layer deliberately knows nothing about JSON or protocol messages.  It
only transfers one non-empty, bounded byte payload at a time over blocking binary
streams.  Callers validate each returned payload with the protocol codec before
dispatch.
"""

from __future__ import annotations

from collections.abc import Buffer
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Never, Protocol

from paperworkshell.worker.protocol.models import MAX_MESSAGE_BYTES

FRAME_HEADER_BYTES: Final = 4
MAX_FRAME_PAYLOAD_BYTES: Final = MAX_MESSAGE_BYTES


class FrameErrorCode(StrEnum):
    """Stable local failure codes for the byte-stream transport boundary."""

    PAYLOAD_INVALID = "RPC_FRAME_PAYLOAD_INVALID"
    LENGTH_INVALID = "RPC_FRAME_LENGTH_INVALID"
    TRUNCATED = "RPC_FRAME_TRUNCATED"
    READ_FAILED = "RPC_FRAME_READ_FAILED"
    WRITE_FAILED = "RPC_FRAME_WRITE_FAILED"
    WRITE_STALLED = "RPC_FRAME_WRITE_STALLED"


_SAFE_MESSAGES: Final = MappingProxyType(
    {
        FrameErrorCode.PAYLOAD_INVALID: "frame payload must be immutable bytes",
        FrameErrorCode.LENGTH_INVALID: "frame payload length is outside the permitted range",
        FrameErrorCode.TRUNCATED: "frame input ended before the current frame was complete",
        FrameErrorCode.READ_FAILED: "frame input could not be read",
        FrameErrorCode.WRITE_FAILED: "frame output could not be written",
        FrameErrorCode.WRITE_STALLED: "frame output made no forward progress",
    }
)


class FrameError(Exception):
    """A sanitized framing failure which never retains stream or payload data."""

    __slots__ = ("code",)

    code: FrameErrorCode

    def __init__(self, code: FrameErrorCode) -> None:
        self.code = code
        super().__init__(code.value)

    def __str__(self) -> str:
        return f"{self.code.value}: {_SAFE_MESSAGES[self.code]}"

    def __repr__(self) -> str:
        return f"FrameError(code={self.code.value!r})"


class FrameReader(Protocol):
    """Blocking binary reader used by :func:`read_frame`."""

    def read(self, size: int, /) -> bytes:
        """Return at most ``size`` bytes, or empty bytes at end of stream."""


class FrameWriter(Protocol):
    """Blocking binary writer used by :func:`write_frame`."""

    def write(self, data: Buffer, /) -> int | None:
        """Write some bytes and return the count, or ``None`` if blocked."""


def _fail(code: FrameErrorCode) -> Never:
    raise FrameError(code) from None


def _validated_payload(payload: bytes) -> bytes:
    if type(payload) is not bytes:
        _fail(FrameErrorCode.PAYLOAD_INVALID)
    if not 1 <= len(payload) <= MAX_FRAME_PAYLOAD_BYTES:
        _fail(FrameErrorCode.LENGTH_INVALID)
    return payload


def _read_chunk(reader: FrameReader, byte_count: int) -> object:
    try:
        return reader.read(byte_count)
    except Exception:
        # Cross the exception boundary before constructing the sanitized error so
        # the private source exception is not retained as FrameError.__context__.
        return None


def _read_exact(
    reader: FrameReader,
    byte_count: int,
    *,
    clean_eof_allowed: bool,
) -> bytes | None:
    chunks: list[bytes] = []
    remaining = byte_count
    while remaining:
        chunk = _read_chunk(reader, remaining)
        if type(chunk) is not bytes or len(chunk) > remaining:
            _fail(FrameErrorCode.READ_FAILED)
        if not chunk:
            if clean_eof_allowed and remaining == byte_count:
                return None
            _fail(FrameErrorCode.TRUNCATED)
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def encode_frame(payload: bytes) -> bytes:
    """Return one canonical four-byte big-endian frame for ``payload``."""

    checked = _validated_payload(payload)
    header = len(checked).to_bytes(FRAME_HEADER_BYTES, byteorder="big", signed=False)
    return header + checked


def read_frame(reader: FrameReader) -> bytes | None:
    """Read one frame, returning ``None`` only for clean EOF between frames.

    A partial header or body is always truncation.  The declared length is
    validated before any body read or body-buffer construction takes place.
    """

    header = _read_exact(reader, FRAME_HEADER_BYTES, clean_eof_allowed=True)
    if header is None:
        return None
    payload_size = int.from_bytes(header, byteorder="big", signed=False)
    if not 1 <= payload_size <= MAX_FRAME_PAYLOAD_BYTES:
        _fail(FrameErrorCode.LENGTH_INVALID)
    payload = _read_exact(reader, payload_size, clean_eof_allowed=False)
    if payload is None:  # pragma: no cover - forbidden by clean_eof_allowed=False
        _fail(FrameErrorCode.TRUNCATED)
    return payload


_WRITE_EXCEPTION: Final = object()


def _write_chunk(writer: FrameWriter, data: Buffer) -> object:
    try:
        return writer.write(data)
    except Exception:
        # As for reads, do not chain a source exception that may contain a path or
        # another private value into the transport-facing failure.
        return _WRITE_EXCEPTION


def _write_all(writer: FrameWriter, data: bytes) -> None:
    view = memoryview(data)
    try:
        offset = 0
        while offset < len(view):
            remaining = len(view) - offset
            written = _write_chunk(writer, view[offset:])
            if written is _WRITE_EXCEPTION:
                _fail(FrameErrorCode.WRITE_FAILED)
            if written is None or written == 0:
                _fail(FrameErrorCode.WRITE_STALLED)
            if type(written) is not int or written < 0 or written > remaining:
                _fail(FrameErrorCode.WRITE_FAILED)
            offset += written
    finally:
        view.release()


def write_frame(writer: FrameWriter, payload: bytes) -> None:
    """Write exactly one canonical frame, retrying only partial forward progress.

    The passed writer must be a blocking, unbuffered transport or be flushed by
    its owner.  A zero/``None`` result is treated as a terminal stall rather than
    retried indefinitely.
    """

    framed = encode_frame(payload)
    _write_all(writer, framed)

from __future__ import annotations

import io
import unittest
from collections.abc import Buffer

from paperworkshell.worker.protocol.framing import (
    FRAME_HEADER_BYTES,
    MAX_FRAME_PAYLOAD_BYTES,
    FrameError,
    FrameErrorCode,
    encode_frame,
    read_frame,
    write_frame,
)


class _FragmentingReader:
    def __init__(self, content: bytes, maximum_chunk: int) -> None:
        self._content = content
        self._maximum_chunk = maximum_chunk
        self._offset = 0

    def read(self, size: int, /) -> bytes:
        count = min(size, self._maximum_chunk, len(self._content) - self._offset)
        result = self._content[self._offset : self._offset + count]
        self._offset += count
        return result


class _PartialWriter:
    def __init__(self, maximum_chunk: int) -> None:
        self.maximum_chunk = maximum_chunk
        self.content = bytearray()
        self.calls = 0

    def write(self, data: Buffer, /) -> int:
        chunk = bytes(data)[: self.maximum_chunk]
        self.content.extend(chunk)
        self.calls += 1
        return len(chunk)


class _FlushTrapWriter(_PartialWriter):
    def flush(self) -> None:
        raise AssertionError("write_frame must not own transport flushing")


class WorkerProtocolFramingTests(unittest.TestCase):
    def test_encoding_uses_unsigned_four_byte_big_endian_length(self) -> None:
        payload = b"bounded-json"

        framed = encode_frame(payload)

        self.assertEqual(FRAME_HEADER_BYTES + len(payload), len(framed))
        self.assertEqual(len(payload), int.from_bytes(framed[:4], byteorder="big"))
        self.assertEqual(b"\x00\x00\x00\x0c", framed[:4])
        self.assertEqual(payload, framed[4:])

    def test_read_frame_handles_every_fragment_size(self) -> None:
        payload = b'{"protocol":"synthetic"}'
        framed = encode_frame(payload)

        for maximum_chunk in range(1, len(framed) + 1):
            with self.subTest(maximum_chunk=maximum_chunk):
                reader = _FragmentingReader(framed, maximum_chunk)
                self.assertEqual(payload, read_frame(reader))
                self.assertIsNone(read_frame(reader))

    def test_read_frame_preserves_coalesced_frame_boundaries(self) -> None:
        first = b"first"
        second = b"second-frame"
        reader = io.BytesIO(encode_frame(first) + encode_frame(second))

        self.assertEqual(first, read_frame(reader))
        self.assertEqual(second, read_frame(reader))
        self.assertIsNone(read_frame(reader))

    def test_clean_boundary_eof_is_distinct_from_truncation(self) -> None:
        self.assertIsNone(read_frame(io.BytesIO()))
        cases = (
            b"\x00",
            b"\x00\x00\x00",
            (3).to_bytes(4, "big"),
            (3).to_bytes(4, "big") + b"ab",
        )
        for content in cases:
            with self.subTest(byte_count=len(content)):
                with self.assertRaises(FrameError) as raised:
                    read_frame(io.BytesIO(content))
                self.assertIs(FrameErrorCode.TRUNCATED, raised.exception.code)

    def test_exact_maximum_payload_round_trips(self) -> None:
        payload = b"x" * MAX_FRAME_PAYLOAD_BYTES
        reader = io.BytesIO(encode_frame(payload))

        self.assertEqual(payload, read_frame(reader))
        self.assertIsNone(read_frame(reader))

    def test_write_frame_completes_deterministic_partial_writes(self) -> None:
        payload = b"partial-write"
        expected = encode_frame(payload)

        for maximum_chunk in (1, 2, 3, 4, 7, len(expected)):
            with self.subTest(maximum_chunk=maximum_chunk):
                writer = _PartialWriter(maximum_chunk)
                write_frame(writer, payload)
                self.assertEqual(expected, bytes(writer.content))
                self.assertEqual(
                    (len(expected) + maximum_chunk - 1) // maximum_chunk,
                    writer.calls,
                )

    def test_write_frame_leaves_flush_lifecycle_to_transport_owner(self) -> None:
        payload = b"unbuffered-transport"
        writer = _FlushTrapWriter(MAX_FRAME_PAYLOAD_BYTES)

        write_frame(writer, payload)

        self.assertEqual(encode_frame(payload), bytes(writer.content))


if __name__ == "__main__":
    unittest.main()

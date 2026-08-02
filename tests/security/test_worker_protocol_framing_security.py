from __future__ import annotations

import io
import unittest
from collections.abc import Buffer

from paperworkshell.worker.protocol.framing import (
    MAX_FRAME_PAYLOAD_BYTES,
    FrameError,
    FrameErrorCode,
    encode_frame,
    read_frame,
    write_frame,
)


class _RecordingReader:
    def __init__(self, header: bytes) -> None:
        self.header = header
        self.read_sizes: list[int] = []

    def read(self, size: int, /) -> bytes:
        self.read_sizes.append(size)
        if len(self.read_sizes) == 1:
            return self.header
        raise AssertionError("body read must not occur")


class _ExplodingReader:
    def read(self, _size: int, /) -> bytes:
        raise OSError("SYNTHETIC_PRIVATE_READ_PATH")


class _ExplodingWriter:
    def write(self, _data: Buffer, /) -> int:
        raise OSError("SYNTHETIC_PRIVATE_WRITE_PATH")


class _StalledWriter:
    def __init__(self, result: int | None) -> None:
        self.result = result

    def write(self, _data: Buffer, /) -> int | None:
        return self.result


class _InvalidWriter:
    def write(self, data: Buffer, /) -> int:
        return len(memoryview(data)) + 1


class _OverdeliveringReader:
    def read(self, size: int, /) -> bytes:
        return b"x" * (size + 1)


class WorkerProtocolFramingSecurityTests(unittest.TestCase):
    def test_zero_and_oversized_lengths_are_rejected_before_body_read(self) -> None:
        for declared_size in (0, MAX_FRAME_PAYLOAD_BYTES + 1, (1 << 32) - 1):
            with self.subTest(declared_size=declared_size):
                reader = _RecordingReader(declared_size.to_bytes(4, "big"))
                with self.assertRaises(FrameError) as raised:
                    read_frame(reader)
                self.assertIs(FrameErrorCode.LENGTH_INVALID, raised.exception.code)
                self.assertEqual([4], reader.read_sizes)

    def test_invalid_outbound_payloads_fail_before_writer_use(self) -> None:
        canary = b"SYNTHETIC_PRIVATE_PAYLOAD"
        invalid_payloads: tuple[object, ...] = (
            b"",
            b"x" * (MAX_FRAME_PAYLOAD_BYTES + 1),
            bytearray(canary),
            memoryview(canary),
        )
        writer = _ExplodingWriter()
        for payload in invalid_payloads:
            with self.subTest(payload_type=type(payload).__name__):
                with self.assertRaises(FrameError) as raised:
                    write_frame(writer, payload)  # type: ignore[arg-type]
                expected = (
                    FrameErrorCode.PAYLOAD_INVALID
                    if type(payload) is not bytes
                    else FrameErrorCode.LENGTH_INVALID
                )
                self.assertIs(expected, raised.exception.code)
                self.assertNotIn(canary.decode("ascii"), str(raised.exception))

    def test_reader_and_writer_failures_are_sanitized(self) -> None:
        canaries = (
            "SYNTHETIC_PRIVATE_READ_PATH",
            "SYNTHETIC_PRIVATE_WRITE_PATH",
        )

        with self.assertRaises(FrameError) as read_raised:
            read_frame(_ExplodingReader())
        with self.assertRaises(FrameError) as write_raised:
            write_frame(_ExplodingWriter(), b"safe")

        self.assertIs(FrameErrorCode.READ_FAILED, read_raised.exception.code)
        self.assertIs(FrameErrorCode.WRITE_FAILED, write_raised.exception.code)
        self.assertIsNone(read_raised.exception.__context__)
        self.assertIsNone(write_raised.exception.__context__)
        rendered = (
            str(read_raised.exception)
            + repr(read_raised.exception)
            + str(write_raised.exception)
            + repr(write_raised.exception)
        )
        for canary in canaries:
            self.assertNotIn(canary, rendered)

    def test_stalled_and_invalid_writes_fail_in_bounded_time(self) -> None:
        for result, expected in (
            (None, FrameErrorCode.WRITE_STALLED),
            (0, FrameErrorCode.WRITE_STALLED),
            (-1, FrameErrorCode.WRITE_FAILED),
        ):
            with self.subTest(result=result):
                with self.assertRaises(FrameError) as raised:
                    write_frame(_StalledWriter(result), b"safe")
                self.assertIs(expected, raised.exception.code)

        with self.assertRaises(FrameError) as raised:
            write_frame(_InvalidWriter(), b"safe")
        self.assertIs(FrameErrorCode.WRITE_FAILED, raised.exception.code)

    def test_nonconforming_reader_cannot_overdeliver_into_another_frame(self) -> None:
        with self.assertRaises(FrameError) as raised:
            read_frame(_OverdeliveringReader())
        self.assertIs(FrameErrorCode.READ_FAILED, raised.exception.code)

    def test_frame_errors_expose_only_code_and_sanitized_text(self) -> None:
        canary = "SYNTHETIC_PRIVATE_FRAME_VALUE"
        invalid_frame = (len(canary) + 1).to_bytes(4, "big") + canary.encode("ascii")
        with self.assertRaises(FrameError) as raised:
            read_frame(io.BytesIO(invalid_frame))

        error = raised.exception
        self.assertEqual({}, error.__dict__)
        self.assertEqual((error.code.value,), error.args)
        self.assertNotIn(canary, str(error))
        self.assertNotIn(canary, repr(error))

    def test_encode_frame_error_is_code_only(self) -> None:
        canary = bytearray(b"SYNTHETIC_PRIVATE_ENCODE_VALUE")
        with self.assertRaises(FrameError) as raised:
            encode_frame(canary)  # type: ignore[arg-type]

        self.assertIs(FrameErrorCode.PAYLOAD_INVALID, raised.exception.code)
        self.assertNotIn(bytes(canary).decode("ascii"), str(raised.exception))


if __name__ == "__main__":
    unittest.main()

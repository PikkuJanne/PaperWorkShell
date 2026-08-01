from __future__ import annotations

import json
import random
import unittest
from dataclasses import fields
from typing import Any, cast
from uuid import UUID

from paperworkshell.worker.protocol.codec import decode_message, encode_message
from paperworkshell.worker.protocol.errors import ProtocolError
from paperworkshell.worker.protocol.models import (
    MAX_JSON_COLLECTION_ITEMS,
    MAX_JSON_DEPTH,
    MAX_JSON_NODES,
    MAX_JSON_STRING_CODEPOINTS,
    MAX_MESSAGE_BYTES,
    MESSAGE_VERSION,
    NOOP_CAPABILITY,
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    CaseObjectReference,
    Direction,
    MessageType,
    OperationStartPayload,
    ProtocolEnvelope,
    ReferenceArea,
    ResourceLimits,
    SessionCapability,
    SessionHelloPayload,
)


def _uuid(number: int) -> UUID:
    return UUID(f"30000000-0000-4000-8000-{number:012x}")


CAPABILITY = SessionCapability("2" * 64)


def _valid_message() -> ProtocolEnvelope:
    return ProtocolEnvelope(
        protocol=PROTOCOL_NAME,
        protocol_version=PROTOCOL_VERSION,
        message_version=MESSAGE_VERSION,
        message_type=MessageType.SESSION_HELLO,
        direction=Direction.CLIENT_TO_WORKER,
        session_id=_uuid(1),
        worker_instance_id=_uuid(2),
        session_capability=CAPABILITY,
        case_id=_uuid(3),
        message_id=_uuid(4),
        sender_sequence=0,
        request_id=_uuid(5),
        operation_id=None,
        payload=SessionHelloPayload((1,), (NOOP_CAPABILITY,)),
    )


def _document() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(encode_message(_valid_message())))


def _raw(document: object) -> bytes:
    return json.dumps(document, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


class WorkerProtocolSecurityTests(unittest.TestCase):
    def assert_protocol_error(self, raw: bytes, expected_code: str) -> ProtocolError:
        with self.assertRaises(ProtocolError) as raised:
            decode_message(raw)
        self.assertEqual(expected_code, raised.exception.code)
        return raised.exception

    def test_malformed_oversized_and_invalid_utf8_fail_closed(self) -> None:
        cases = (
            (b"", "RPC_MALFORMED_JSON"),
            (b"{", "RPC_MALFORMED_JSON"),
            (b'{"x":"\xff"}', "RPC_INVALID_UTF8"),
            (b" " * (MAX_MESSAGE_BYTES + 1), "RPC_MESSAGE_TOO_LARGE"),
        )
        for raw, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                self.assert_protocol_error(raw, expected_code)

    def test_exact_message_size_boundary_is_accepted_and_one_byte_over_is_rejected(self) -> None:
        encoded = encode_message(_valid_message())
        exact = encoded + b" " * (MAX_MESSAGE_BYTES - len(encoded))

        self.assertEqual(MAX_MESSAGE_BYTES, len(exact))
        self.assertEqual(_valid_message(), decode_message(exact))
        self.assert_protocol_error(exact + b" ", "RPC_MESSAGE_TOO_LARGE")

    def test_duplicate_fields_floats_constants_and_bool_as_int_are_rejected(self) -> None:
        valid = encode_message(_valid_message()).decode("utf-8")
        cases = (
            (
                valid.replace(
                    '"protocol":',
                    f'"protocol":"{PROTOCOL_NAME}","protocol":',
                    1,
                ).encode(),
                "RPC_DUPLICATE_FIELD",
            ),
            (
                valid.replace('"protocol_version":1', '"protocol_version":1.0').encode(),
                "RPC_INVALID_NUMBER",
            ),
            (
                valid.replace('"protocol_version":1', '"protocol_version":NaN').encode(),
                "RPC_INVALID_NUMBER",
            ),
            (
                valid.replace('"protocol_version":1', '"protocol_version":Infinity').encode(),
                "RPC_INVALID_NUMBER",
            ),
            (
                valid.replace('"sender_sequence":0', '"sender_sequence":true').encode(),
                "RPC_INVALID_INTEGER",
            ),
        )
        for raw, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                self.assert_protocol_error(raw, expected_code)

    def test_unknown_message_type_has_a_stable_sanitized_failure(self) -> None:
        canary = "synthetic.unknown-message"
        document = _document()
        document["message_type"] = canary

        error = self.assert_protocol_error(_raw(document), "RPC_UNKNOWN_MESSAGE")
        self.assertNotIn(canary, str(error))

    def test_surrogates_controls_and_excessive_strings_are_rejected(self) -> None:
        cases = (
            (b'{"x":"\\ud800"}', "RPC_INVALID_STRING"),
            (b'{"x":"line\\nbreak"}', "RPC_INVALID_STRING"),
            (
                _raw({"x": "a" * (MAX_JSON_STRING_CODEPOINTS + 1)}),
                "RPC_STRUCTURE_LIMIT",
            ),
        )
        for raw, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                self.assert_protocol_error(raw, expected_code)

    def test_depth_collection_and_node_limits_are_enforced(self) -> None:
        nested: object = None
        for _ in range(MAX_JSON_DEPTH + 1):
            nested = [nested]
        oversized_collection = list(range(MAX_JSON_COLLECTION_ITEMS + 1))
        node_heavy = [{f"k{column}": column for column in range(16)} for _ in range(256)]
        self.assertGreater(1 + 256 * (1 + 16 + 16), MAX_JSON_NODES)

        for document in (nested, oversized_collection, node_heavy):
            with self.subTest(kind=type(document).__name__):
                self.assert_protocol_error(_raw(document), "RPC_STRUCTURE_LIMIT")

    def test_closed_objects_reject_path_command_and_process_injection_fields(self) -> None:
        canary = "C:\\Applicants\\synthetic-person\\source.pdf--inject"
        for field_name in ("path", "command", "argv", "cwd", "env", "exception", "detail"):
            document = _document()
            payload = cast(dict[str, Any], document["payload"])
            payload[field_name] = canary
            with self.subTest(field_name=field_name):
                error = self.assert_protocol_error(_raw(document), "RPC_UNKNOWN_FIELD")
                self.assertNotIn(canary, str(error))
                self.assertNotIn(field_name, str(error))

    def test_traversal_and_injection_values_are_rejected_without_value_leakage(self) -> None:
        canaries = (
            "../../synthetic-secret.pdf",
            r"C:\synthetic-secret.pdf",
            "source.pdf\r\n--execute",
            "$(synthetic-command)",
            '"; synthetic-command',
        )
        for canary in canaries:
            document = _document()
            capabilities = cast(list[dict[str, Any]], document["payload"]["required_capabilities"])
            capabilities[0]["capability_id"] = canary
            with self.subTest(canary=canary):
                with self.assertRaises(ProtocolError) as raised:
                    decode_message(_raw(document))
                self.assertNotIn(canary, str(raised.exception))

    def test_wrong_capability_and_noncanonical_uuid_are_rejected_without_secret_leakage(
        self,
    ) -> None:
        expected = SessionCapability("3" * 64)
        with self.assertRaises(ProtocolError) as mismatch:
            decode_message(encode_message(_valid_message()), expected_capability=expected)
        rendered = str(mismatch.exception)
        self.assertEqual("RPC_CAPABILITY_MISMATCH", mismatch.exception.code)
        self.assertNotIn(CAPABILITY.value, rendered)
        self.assertNotIn(expected.value, rendered)

        document = _document()
        document["message_id"] = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
        self.assert_protocol_error(_raw(document), "RPC_INVALID_IDENTIFIER")

    def test_envelope_and_references_have_no_filesystem_or_process_fields(self) -> None:
        prohibited = {"path", "filename", "command", "argv", "cwd", "env", "shell"}
        for model_type in (ProtocolEnvelope, CaseObjectReference, OperationStartPayload):
            with self.subTest(model_type=model_type.__name__):
                self.assertTrue(prohibited.isdisjoint(field.name for field in fields(model_type)))

    def test_default_noop_registry_rejects_inputs_before_encoding(self) -> None:
        reference = CaseObjectReference(
            area=ReferenceArea.SOURCE_READ,
            object_id=_uuid(20),
            operation_id=None,
            content_kind="application.pdf",
            expected_byte_size=None,
            expected_sha256=None,
        )
        limits = ResourceLimits(1, 1, 1, 1, 1, 1, 1)
        message = ProtocolEnvelope(
            protocol=PROTOCOL_NAME,
            protocol_version=PROTOCOL_VERSION,
            message_version=MESSAGE_VERSION,
            message_type=MessageType.OPERATION_START,
            direction=Direction.CLIENT_TO_WORKER,
            session_id=_uuid(1),
            worker_instance_id=_uuid(2),
            session_capability=CAPABILITY,
            case_id=_uuid(3),
            message_id=_uuid(4),
            sender_sequence=0,
            request_id=_uuid(5),
            operation_id=_uuid(6),
            payload=OperationStartPayload("protocol.noop", 1, (reference,), (), limits),
        )
        with self.assertRaises(ProtocolError) as raised:
            encode_message(message)
        self.assertEqual("RPC_INVALID_OPERATION", raised.exception.code)

    def test_deterministic_byte_fuzz_never_raises_an_unsanitized_exception(self) -> None:
        generator = random.Random(8_008)
        canary = b"synthetic-private-value"
        corpus = [generator.randbytes(generator.randrange(0, 512)) for _ in range(512)]
        corpus.extend((canary, b"{" + canary + b"}"))
        for raw in corpus:
            try:
                decode_message(raw)
            except ProtocolError as error:
                self.assertNotIn(canary.decode("ascii"), str(error))
            except Exception as error:  # pragma: no cover - assertion reports unexpected type
                self.fail(f"decoder raised non-protocol exception type {type(error).__name__}")


if __name__ == "__main__":
    unittest.main()

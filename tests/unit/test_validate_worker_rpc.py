from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, ClassVar, cast

from tools.validate_worker_rpc import (
    MAX_JSON_COLLECTION_ITEMS,
    MAX_JSON_DEPTH,
    MAX_JSON_NODES,
    MAX_JSON_STRING_CODEPOINTS,
    MAX_MESSAGE_BYTES,
    RPC_EXAMPLE_DIRECTORY,
    RPC_INVALID_FIXTURE_DIRECTORY,
    RPC_SCHEMA_PATH,
    RpcIssue,
    load_worker_rpc_schema,
    validate_worker_rpc_data,
    validate_worker_rpc_document,
    validate_worker_rpc_fixture_set,
)

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_DIRECTORY = ROOT / RPC_EXAMPLE_DIRECTORY


def _load_example(name: str) -> dict[str, Any]:
    loaded = json.loads((EXAMPLE_DIRECTORY / name).read_text(encoding="utf-8"))
    return cast(dict[str, Any], loaded)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")


class WorkerRpcValidationTests(unittest.TestCase):
    schema: ClassVar[dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        schema, issues = load_worker_rpc_schema()
        if schema is None:
            raise AssertionError([issue.render() for issue in issues])
        cls.schema = schema

    def _issues(self, data: dict[str, Any]) -> list[RpcIssue]:
        return validate_worker_rpc_data(data, self.schema, document="synthetic-worker-rpc.json")

    def _codes(self, data: dict[str, Any]) -> set[str]:
        return {issue.code for issue in self._issues(data)}

    def test_checked_in_schema_examples_and_negative_fixtures_pass(self) -> None:
        summary, issues = validate_worker_rpc_fixture_set()

        self.assertEqual([], issues)
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(15, summary.positive_count)
        self.assertEqual(21, summary.negative_count)

    def test_every_message_type_has_a_valid_canonical_example(self) -> None:
        expected_types = {
            "session.hello",
            "session.ready",
            "operation.start",
            "operation.accepted",
            "operation.progress",
            "operation.cancel",
            "operation.cancel_ack",
            "operation.result",
            "session.shutdown",
            "session.closed",
            "protocol.error",
            "worker.fault",
        }
        observed_types: set[str] = set()
        for path in sorted(EXAMPLE_DIRECTORY.glob("*.json")):
            data = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
            observed_types.add(cast(str, data["message_type"]))
            self.assertEqual([], self._issues(data), path.name)

        self.assertEqual(expected_types, observed_types)

    def test_each_negative_fixture_is_rejected_for_its_intended_reason(self) -> None:
        expected = {
            "duplicate-capability.json": "RPC_CAPABILITY_DUPLICATE",
            "duplicate-key.json": "RPC_JSON_DUPLICATE_KEY",
            "duplicate-worker-fault-id.json": "RPC_ID_DUPLICATE",
            "error-parameter-unknown.json": "RPC_ERROR_PARAMETER_INVALID",
            "error-restart-mismatch.json": "RPC_ERROR_INVALID",
            "future-message-version.json": "RPC_MESSAGE_VERSION_UNSUPPORTED",
            "future-protocol-version.json": "RPC_VERSION_UNSUPPORTED",
            "floating-number.json": "RPC_JSON_NONSTANDARD_NUMBER",
            "invalid-correlation.json": "RPC_CORRELATION_INVALID",
            "legacy-message-version.json": "RPC_MESSAGE_VERSION_UNSUPPORTED",
            "legacy-protocol-version.json": "RPC_VERSION_UNSUPPORTED",
            "malformed-json.json": "RPC_JSON_INVALID",
            "missing-negotiated-version.json": "RPC_NEGOTIATION_INVALID",
            "noop-input.json": "RPC_OPERATION_INPUT_INVALID",
            "progress-overrun.json": "RPC_PROGRESS_INVALID",
            "prohibited-path-field.json": "RPC_PROHIBITED_MEMBER",
            "result-error-mismatch.json": "RPC_RESULT_INVALID",
            "result-reference-scope.json": "RPC_REFERENCE_OPERATION_INVALID",
            "unknown-message-type.json": "RPC_MESSAGE_TYPE_UNKNOWN",
            "unsupported-operation.json": "RPC_OPERATION_UNSUPPORTED",
            "wrong-direction.json": "RPC_DIRECTION_INVALID",
        }
        fixture_directory = ROOT / RPC_INVALID_FIXTURE_DIRECTORY
        self.assertEqual(set(expected), {path.name for path in fixture_directory.glob("*.json")})
        for filename, expected_code in expected.items():
            with self.subTest(filename=filename):
                _, issues = validate_worker_rpc_document(
                    fixture_directory / filename,
                    schema=self.schema,
                )
                self.assertIn(expected_code, {issue.code for issue in issues})

    def test_exact_protocol_and_version_predispatch_rejects_type_confusion(self) -> None:
        cases: tuple[tuple[str, object, str], ...] = (
            ("protocol", None, "RPC_PROTOCOL_INVALID"),
            ("protocol", "paperworkshell.other", "RPC_PROTOCOL_UNSUPPORTED"),
            ("protocol_version", True, "RPC_VERSION_INVALID"),
            ("protocol_version", "1", "RPC_VERSION_INVALID"),
            ("protocol_version", 2, "RPC_VERSION_UNSUPPORTED"),
            ("message_version", False, "RPC_MESSAGE_VERSION_INVALID"),
            ("message_version", 2, "RPC_MESSAGE_VERSION_UNSUPPORTED"),
            ("message_type", 1, "RPC_MESSAGE_TYPE_INVALID"),
            ("message_type", "operation.execute", "RPC_MESSAGE_TYPE_UNKNOWN"),
        )
        for field, value, expected_code in cases:
            with self.subTest(field=field, value=value):
                data = _load_example("session-hello.json")
                data[field] = value
                self.assertEqual({expected_code}, self._codes(data))

    def test_strict_json_loader_rejects_ambiguous_or_nonstandard_input(self) -> None:
        cases = {
            "duplicate.json": (
                '{"protocol":"paperworkshell.worker-rpc","protocol_version":1,'
                '"protocol_version":1}',
                "RPC_JSON_DUPLICATE_KEY",
            ),
            "nan.json": (
                '{"protocol":"paperworkshell.worker-rpc","value":NaN}',
                "RPC_JSON_NONSTANDARD_CONSTANT",
            ),
            "infinity.json": (
                '{"protocol":"paperworkshell.worker-rpc","value":Infinity}',
                "RPC_JSON_NONSTANDARD_CONSTANT",
            ),
            "array.json": ("[]", "RPC_JSON_ROOT_TYPE"),
            "malformed.json": ("{", "RPC_JSON_INVALID"),
            "float.json": (
                '{"protocol":"paperworkshell.worker-rpc","sender_sequence":0.0}',
                "RPC_JSON_NONSTANDARD_NUMBER",
            ),
            "exponent.json": (
                '{"protocol":"paperworkshell.worker-rpc","sender_sequence":0e0}',
                "RPC_JSON_NONSTANDARD_NUMBER",
            ),
            "unsafe-integer.json": (
                '{"protocol":"paperworkshell.worker-rpc","sender_sequence":9007199254740992}',
                "RPC_JSON_NONSTANDARD_NUMBER",
            ),
        }
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            for filename, (content, expected_code) in cases.items():
                with self.subTest(filename=filename):
                    path = temporary_root / filename
                    path.write_text(content, encoding="utf-8")
                    _, issues = validate_worker_rpc_document(path, schema=self.schema)
                    self.assertIn(expected_code, {issue.code for issue in issues})

    def test_byte_encoding_and_structural_limits_fail_before_schema_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases: list[tuple[str, bytes, str]] = [
                ("empty.json", b"", "RPC_JSON_INVALID"),
                ("invalid-utf8.json", b'{"protocol":"\xff"}', "RPC_TEXT_ENCODING_INVALID"),
                (
                    "oversized.json",
                    b"{" + (b" " * MAX_MESSAGE_BYTES) + b"}",
                    "RPC_INPUT_TOO_LARGE",
                ),
            ]

            too_deep: object = 0
            for _ in range(MAX_JSON_DEPTH):
                too_deep = {"x": too_deep}
            cases.append(("depth.json", json.dumps(too_deep).encode(), "RPC_JSON_RESOURCE_LIMIT"))
            cases.append(
                (
                    "string.json",
                    json.dumps({"x": "a" * (MAX_JSON_STRING_CODEPOINTS + 1)}).encode(),
                    "RPC_JSON_RESOURCE_LIMIT",
                )
            )
            cases.append(
                (
                    "collection.json",
                    json.dumps({"x": [0] * (MAX_JSON_COLLECTION_ITEMS + 1)}).encode(),
                    "RPC_JSON_RESOURCE_LIMIT",
                )
            )

            for filename, content, expected_code in cases:
                with self.subTest(filename=filename):
                    path = root / filename
                    path.write_bytes(content)
                    _, issues = validate_worker_rpc_document(path, root=root, schema=self.schema)
                    self.assertIn(expected_code, {issue.code for issue in issues})

    def test_node_budget_counts_mapping_keys_and_values_at_exact_boundary(self) -> None:
        def bounded_tree(last_item_count: int) -> dict[str, object]:
            chunks: list[list[int]] = [[0] * 255 for _ in range(15)]
            chunks.append([0] * last_item_count)
            return {"x": chunks}

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            at_limit = root / "at-limit.json"
            over_limit = root / "over-limit.json"
            _write_json(at_limit, bounded_tree(252))
            _write_json(over_limit, bounded_tree(253))

            _, at_limit_issues = validate_worker_rpc_document(
                at_limit,
                root=root,
                schema=self.schema,
            )
            _, over_limit_issues = validate_worker_rpc_document(
                over_limit,
                root=root,
                schema=self.schema,
            )

        self.assertNotIn("RPC_JSON_RESOURCE_LIMIT", {issue.code for issue in at_limit_issues})
        self.assertIn("RPC_JSON_RESOURCE_LIMIT", {issue.code for issue in over_limit_issues})
        self.assertEqual(4_096, MAX_JSON_NODES)

    def test_loader_diagnostics_do_not_disclose_external_paths_or_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            case_root = temporary_root / "case-root"
            case_root.mkdir()
            filename_canary = "CANARY-SENSITIVE-ORIGINAL-NAME.json"
            external = case_root / ".." / filename_canary
            external.write_bytes(b'{"protocol":"\xff"}')

            _, path_issues = validate_worker_rpc_document(
                external,
                root=case_root,
                schema=self.schema,
            )
            data = _load_example("session-hello.json")
            cast(dict[str, Any], data["payload"])["raw_error"] = "CANARY-LIBRARY-DETAIL"
            rendered = "\n".join(issue.render() for issue in [*path_issues, *self._issues(data)])

        self.assertIn("RPC_TEXT_ENCODING_INVALID", rendered)
        self.assertIn("RPC_PROHIBITED_MEMBER", rendered)
        self.assertNotIn(str(temporary_root), rendered)
        self.assertNotIn(filename_canary, rendered)
        self.assertNotIn("CANARY-LIBRARY-DETAIL", rendered)

    def test_schema_rejects_external_unresolved_and_huge_local_references(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "schemas").mkdir()
            schema_path = root / RPC_SCHEMA_PATH
            for reference, expected_code in (
                ("https://invalid.example/schema.json", "RPC_SCHEMA_REFERENCE_UNSAFE"),
                ("#/$defs/does-not-exist", "RPC_SCHEMA_REFERENCE_INVALID"),
                ("#/$defs/~2bad-escape", "RPC_SCHEMA_REFERENCE_INVALID"),
                (f"#/allOf/{'9' * 4_000}", "RPC_SCHEMA_REFERENCE_INVALID"),
            ):
                with self.subTest(reference=reference):
                    schema = copy.deepcopy(self.schema)
                    if reference.startswith("#/allOf/"):
                        schema["allOf"] = [{}]
                    cast(dict[str, Any], schema["properties"])["protocol"] = {"$ref": reference}
                    schema_path.write_text(json.dumps(schema), encoding="utf-8")
                    loaded, issues = load_worker_rpc_schema(root)
                    self.assertIsNone(loaded)
                    self.assertIn(expected_code, {issue.code for issue in issues})

    def test_schema_rejects_recursive_and_multi_resource_features(self) -> None:
        cases: list[tuple[str, dict[str, Any], str]] = []
        wrong_dialect = copy.deepcopy(self.schema)
        wrong_dialect["$schema"] = "http://json-schema.org/draft-07/schema#"
        cases.append(("wrong-dialect", wrong_dialect, "RPC_SCHEMA_DIALECT_UNSUPPORTED"))

        root_cycle = copy.deepcopy(self.schema)
        root_cycle["allOf"] = [{"$ref": "#"}]
        cases.append(("root-cycle", root_cycle, "RPC_SCHEMA_REFERENCE_CYCLE"))

        definition_cycle = copy.deepcopy(self.schema)
        cast(dict[str, Any], definition_cycle["$defs"])["loop"] = {"$ref": "#/$defs/loop"}
        cases.append(("definition-cycle", definition_cycle, "RPC_SCHEMA_REFERENCE_CYCLE"))

        nested_resource = copy.deepcopy(self.schema)
        protocol_schema = cast(
            dict[str, Any],
            cast(dict[str, Any], nested_resource["properties"])["protocol"],
        )
        protocol_schema["$id"] = "nested-resource"
        cases.append(("nested-resource", nested_resource, "RPC_SCHEMA_RESOURCE_SCOPE_UNSAFE"))

        dynamic_reference = copy.deepcopy(self.schema)
        dynamic_reference["allOf"] = [{"$dynamicRef": "#node"}]
        cases.append(
            (
                "dynamic-reference",
                dynamic_reference,
                "RPC_SCHEMA_REFERENCE_FEATURE_UNSAFE",
            )
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "schemas").mkdir()
            schema_path = root / RPC_SCHEMA_PATH
            for name, schema, expected_code in cases:
                with self.subTest(name=name):
                    schema_path.write_text(json.dumps(schema), encoding="utf-8")
                    loaded, issues = load_worker_rpc_schema(root)
                    self.assertIsNone(loaded)
                    self.assertIn(expected_code, {issue.code for issue in issues})

    def test_protocol_error_correlation_identifiers_are_independently_nullable(self) -> None:
        operation_id = "22000000-0000-4000-8000-000000000001"
        request_id = "21000000-0000-4000-8000-000000000001"
        for request, operation in (
            (None, None),
            (request_id, None),
            (None, operation_id),
            (request_id, operation_id),
        ):
            with self.subTest(request=request, operation=operation):
                data = _load_example("protocol-error.json")
                data["request_id"] = request
                data["operation_id"] = operation
                self.assertEqual([], self._issues(data))

    def test_semantic_mutations_fail_with_stable_codes(self) -> None:
        cases: list[tuple[str, dict[str, Any], str]] = []

        invalid_uuid = _load_example("session-hello.json")
        invalid_uuid["session_id"] = "10000000-0000-1000-8000-000000000001"
        cases.append(("uuid-version", invalid_uuid, "RPC_ID_INVALID"))

        invalid_capability = _load_example("session-hello.json")
        invalid_capability["session_capability"] = "A" * 64
        cases.append(("session-capability", invalid_capability, "RPC_SESSION_CAPABILITY_INVALID"))

        accepted = _load_example("operation-accepted.json")
        cast(dict[str, Any], accepted["payload"])["operation_version"] = 2
        cases.append(("accepted-registry", accepted, "RPC_OPERATION_UNSUPPORTED"))

        output = _load_example("operation-result-succeeded.json")
        output_ref = cast(list[dict[str, Any]], cast(dict[str, Any], output["payload"])["outputs"])[
            0
        ]
        output_ref["expected_sha256"] = None
        cases.append(("output-integrity", output, "RPC_REFERENCE_INTEGRITY_REQUIRED"))

        protocol_error = _load_example("protocol-error.json")
        error = cast(dict[str, Any], cast(dict[str, Any], protocol_error["payload"])["error"])
        error["category"] = "internal"
        cases.append(("protocol-error-category", protocol_error, "RPC_ERROR_INVALID"))

        worker_fault = _load_example("worker-fault.json")
        fault_error = cast(dict[str, Any], cast(dict[str, Any], worker_fault["payload"])["error"])
        fault_error["retry_action"] = "never"
        fault_error["restart_required"] = False
        cases.append(("worker-restart", worker_fault, "RPC_ERROR_INVALID"))

        for name, data, expected_code in cases:
            with self.subTest(name=name):
                self.assertIn(expected_code, self._codes(data))

    def test_error_restart_coupling_rejects_both_mismatch_directions(self) -> None:
        for retry_action, restart_required in (("never", True), ("restart_worker", False)):
            with self.subTest(
                retry_action=retry_action,
                restart_required=restart_required,
            ):
                data = _load_example("protocol-error.json")
                error = cast(dict[str, Any], cast(dict[str, Any], data["payload"])["error"])
                error["retry_action"] = retry_action
                error["restart_required"] = restart_required
                self.assertIn("RPC_ERROR_INVALID", self._codes(data))

    def test_basic_property_style_decoder_mutations_always_fail_closed(self) -> None:
        original = _load_example("session-hello.json")
        alien_values: tuple[object, ...] = (None, False, 0, "", [], {})
        for field, original_value in original.items():
            deleted = copy.deepcopy(original)
            del deleted[field]
            with self.subTest(field=field, mutation="deleted"):
                self.assertTrue(self._issues(deleted))
            for alien in alien_values:
                if alien == original_value and type(alien) is type(original_value):
                    continue
                mutated = copy.deepcopy(original)
                mutated[field] = alien
                with self.subTest(field=field, mutation=type(alien).__name__):
                    self.assertTrue(self._issues(mutated))


if __name__ == "__main__":
    unittest.main()

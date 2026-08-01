from __future__ import annotations

import copy
import json
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar, cast
from unittest.mock import patch

from tools.validate_dependencies import (
    EXPECTED_NEGATIVE_CODES,
    INVENTORY_PATH,
    INVENTORY_SCHEMA_PATH,
    MAX_INVENTORY_BYTES,
    MAX_JSON_COLLECTION_ITEMS,
    MAX_JSON_DEPTH,
    MAX_JSON_NODES,
    MAX_JSON_STRING_CODEPOINTS,
    MAX_REPORTED_ISSUES,
    MAX_SCHEMA_BYTES,
    PROJECT_ROOT,
    DependencyIssue,
    load_dependency_schema,
    validate_dependency_data,
    validate_dependency_document,
    validate_dependency_fixture_set,
)


def _component(document: dict[str, Any], component_id: str) -> dict[str, Any]:
    components = cast(list[dict[str, Any]], document["components"])
    return next(component for component in components if component["component_id"] == component_id)


def _relationships(document: dict[str, Any]) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], document["relationships"])


def _codes(issues: list[DependencyIssue]) -> set[str]:
    return {issue.code for issue in issues}


class DependencyInventorySecurityTests(unittest.TestCase):
    schema: ClassVar[dict[str, Any]]
    baseline: ClassVar[dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        schema, issues = load_dependency_schema()
        if schema is None:
            rendered = "\n".join(issue.render() for issue in issues)
            raise AssertionError(f"canonical dependency schema did not load:\n{rendered}")
        cls.schema = schema
        cls.baseline = cast(
            dict[str, Any],
            json.loads((PROJECT_ROOT / INVENTORY_PATH).read_text(encoding="utf-8")),
        )

    def _document_issues(self, document: dict[str, Any]) -> list[DependencyIssue]:
        validated, issues = validate_dependency_data(
            document,
            self.schema,
            document="synthetic-dependency-inventory.json",
        )
        self.assertIsNone(validated)
        self.assertTrue(issues)
        return issues

    def _write_schema(self, root: Path, schema: dict[str, Any]) -> None:
        path = root / INVENTORY_SCHEMA_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(schema), encoding="utf-8")

    def test_exact_inventory_byte_limit_is_accepted_and_one_byte_over_is_rejected(self) -> None:
        canonical = (PROJECT_ROOT / INVENTORY_PATH).read_bytes()
        self.assertLessEqual(len(canonical), MAX_INVENTORY_BYTES)
        exact = canonical + b" " * (MAX_INVENTORY_BYTES - len(canonical))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "inventory.json"
            path.write_bytes(exact)
            validated, issues = validate_dependency_document(path, root, schema=self.schema)

            self.assertIsNotNone(validated)
            self.assertEqual([], issues)

            path.write_bytes(exact + b" ")
            validated, issues = validate_dependency_document(path, root, schema=self.schema)

        self.assertIsNone(validated)
        self.assertEqual({"DEPENDENCY_INPUT_TOO_LARGE"}, _codes(issues))

    def test_malformed_encoding_and_non_object_roots_fail_closed(self) -> None:
        cases = (
            (b"", "DEPENDENCY_JSON_INVALID"),
            (b"{", "DEPENDENCY_JSON_INVALID"),
            (b'{"value":"\xff"}', "DEPENDENCY_TEXT_ENCODING_INVALID"),
            (b"[]", "DEPENDENCY_JSON_ROOT_TYPE"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "inventory.json"
            for raw, expected_code in cases:
                with self.subTest(expected_code=expected_code):
                    path.write_bytes(raw)
                    validated, issues = validate_dependency_document(path, root, schema=self.schema)
                    self.assertIsNone(validated)
                    self.assertEqual({expected_code}, _codes(issues))

    def test_duplicate_keys_constants_floats_and_unsafe_integers_fail_closed(self) -> None:
        cases = (
            (
                b'{"schema_version":1,"schema_version":1}',
                "DEPENDENCY_JSON_DUPLICATE_KEY",
            ),
            (b'{"schema_version":NaN}', "DEPENDENCY_JSON_NONSTANDARD_CONSTANT"),
            (b'{"schema_version":Infinity}', "DEPENDENCY_JSON_NONSTANDARD_CONSTANT"),
            (b'{"schema_version":1.0}', "DEPENDENCY_JSON_NONSTANDARD_NUMBER"),
            (b'{"schema_version":1e3}', "DEPENDENCY_JSON_NONSTANDARD_NUMBER"),
            (
                b'{"schema_version":9007199254740992}',
                "DEPENDENCY_JSON_NONSTANDARD_NUMBER",
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "inventory.json"
            for raw, expected_code in cases:
                with self.subTest(expected_code=expected_code):
                    path.write_bytes(raw)
                    validated, issues = validate_dependency_document(path, root, schema=self.schema)
                    self.assertIsNone(validated)
                    self.assertEqual({expected_code}, _codes(issues))

    def test_depth_collection_string_and_node_limits_fail_closed(self) -> None:
        nested: object = None
        for _ in range(MAX_JSON_DEPTH + 1):
            nested = {"nested": nested}
        node_heavy = {"items": [[None] * 101 for _ in range(1_000)]}
        self.assertGreater(1 + 1_000 * 102, MAX_JSON_NODES)
        cases = (
            nested,
            {"items": [None] * (MAX_JSON_COLLECTION_ITEMS + 1)},
            {"text": "x" * (MAX_JSON_STRING_CODEPOINTS + 1)},
            node_heavy,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "inventory.json"
            for index, document in enumerate(cases):
                with self.subTest(case=index):
                    path.write_text(
                        json.dumps(document, separators=(",", ":")),
                        encoding="utf-8",
                    )
                    validated, issues = validate_dependency_document(path, root, schema=self.schema)
                    self.assertIsNone(validated)
                    self.assertEqual({"DEPENDENCY_JSON_RESOURCE_LIMIT"}, _codes(issues))

    def test_schema_input_is_bounded_before_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / INVENTORY_SCHEMA_PATH
            path.parent.mkdir(parents=True)
            path.write_bytes(b" " * (MAX_SCHEMA_BYTES + 1))

            schema, issues = load_dependency_schema(root)

        self.assertIsNone(schema)
        self.assertEqual({"DEPENDENCY_INPUT_TOO_LARGE"}, _codes(issues))

    def test_credentials_and_local_paths_are_rejected_without_value_disclosure(self) -> None:
        cases: tuple[
            tuple[str, str, Callable[[dict[str, Any], str], None], tuple[str, ...]], ...
        ] = (
            (
                "credential URL",
                "DEPENDENCY_SOURCE_URL_UNSAFE",
                lambda component, value: cast(dict[str, Any], component["source"]).__setitem__(
                    "project_url", value
                ),
                ("synthetic-user", "synthetic-password"),
            ),
            (
                "query credential",
                "DEPENDENCY_SOURCE_URL_UNSAFE",
                lambda component, value: cast(dict[str, Any], component["source"]).__setitem__(
                    "release_url", value
                ),
                ("synthetic-query-secret",),
            ),
            (
                "Windows path",
                "DEPENDENCY_SENSITIVE_TEXT_PROHIBITED",
                lambda component, value: cast(dict[str, Any], component["maintenance"]).__setitem__(
                    "note", value
                ),
                ("synthetic-private-document.pdf",),
            ),
            (
                "UNC path",
                "DEPENDENCY_SENSITIVE_TEXT_PROHIBITED",
                lambda component, value: cast(dict[str, Any], component["license"]).__setitem__(
                    "distribution_note", value
                ),
                ("synthetic-private-host", "document.pdf"),
            ),
        )
        values = {
            "credential URL": "https://synthetic-user:synthetic-password@example.invalid/project",
            "query credential": ("https://example.invalid/release?token=synthetic-query-secret"),
            "Windows path": r"C:\Applicants\synthetic-private-document.pdf",
            "UNC path": r"\\synthetic-private-host\share\document.pdf",
        }
        for name, expected_code, mutate, canaries in cases:
            with self.subTest(name=name):
                document = copy.deepcopy(self.baseline)
                mutate(_component(document, "ruff"), values[name])

                issues = self._document_issues(document)
                rendered = "\n".join(issue.render() for issue in issues)

                self.assertIn(expected_code, _codes(issues))
                for canary in canaries:
                    self.assertNotIn(canary, rendered)

    def test_external_document_paths_are_not_disclosed_in_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory(prefix="synthetic-private-inventory-") as temporary:
            path = Path(temporary) / "synthetic-private-name.json"
            path.write_text('{"schema_version":999}', encoding="utf-8")

            validated, issues = validate_dependency_document(
                path,
                PROJECT_ROOT,
                schema=self.schema,
            )

        self.assertIsNone(validated)
        rendered = "\n".join(issue.render() for issue in issues)
        self.assertIn("external-dependency-inventory.json", rendered)
        self.assertNotIn(str(path), rendered)
        self.assertNotIn("synthetic-private-name", rendered)

    def test_schema_fingerprint_rejects_any_contract_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write_schema(root, self.schema)
            loaded, issues = load_dependency_schema(root)
            self.assertIsNotNone(loaded)
            self.assertEqual([], issues)

            tampered = copy.deepcopy(self.schema)
            tampered["title"] = "Synthetic weakened dependency contract"
            self._write_schema(root, tampered)
            loaded, issues = load_dependency_schema(root)

        self.assertIsNone(loaded)
        self.assertIn("DEPENDENCY_SCHEMA_IDENTITY_MISMATCH", _codes(issues))

    def test_external_unresolved_and_dynamic_schema_references_fail_offline(self) -> None:
        external_url = "https://synthetic-private-schema.invalid/contract.json"

        def external_reference(schema: dict[str, Any]) -> None:
            schema["allOf"] = [{"$ref": external_url}]

        def unresolved_reference(schema: dict[str, Any]) -> None:
            cast(dict[str, Any], schema["$defs"])["synthetic"] = {"$ref": "#/$defs/does-not-exist"}

        def nested_resource(schema: dict[str, Any]) -> None:
            cast(dict[str, Any], schema["$defs"])["synthetic"] = {
                "$id": external_url,
                "type": "string",
            }

        def dynamic_reference(schema: dict[str, Any]) -> None:
            cast(dict[str, Any], schema["$defs"])["synthetic"] = {"$dynamicRef": "#item"}

        cases = (
            (external_reference, "DEPENDENCY_SCHEMA_REFERENCE_UNSAFE"),
            (unresolved_reference, "DEPENDENCY_SCHEMA_REFERENCE_INVALID"),
            (nested_resource, "DEPENDENCY_SCHEMA_RESOURCE_SCOPE_UNSAFE"),
            (dynamic_reference, "DEPENDENCY_SCHEMA_REFERENCE_FEATURE_UNSAFE"),
        )
        for mutate, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    schema = copy.deepcopy(self.schema)
                    mutate(schema)
                    self._write_schema(root, schema)
                    with (
                        patch(
                            "socket.create_connection",
                            side_effect=AssertionError("network access attempted"),
                        ),
                        patch(
                            "urllib.request.urlopen",
                            side_effect=AssertionError("network access attempted"),
                        ),
                    ):
                        loaded, issues = load_dependency_schema(root)

                self.assertIsNone(loaded)
                self.assertIn(expected_code, _codes(issues))
                self.assertNotIn(
                    external_url,
                    "\n".join(issue.render() for issue in issues),
                )

    def test_distribution_gate_cannot_be_bypassed_by_self_reported_license_flags(self) -> None:
        document = copy.deepcopy(self.baseline)
        component = _component(document, "ghostscript")
        component["admission_status"] = "approved"
        component["active_scopes"] = ["worker"]
        component["version"] = {
            "constraint": "==1.0.0",
            "selected": "1.0.0",
            "pin_kind": "exact",
            "pin_source": "synthetic security test",
        }
        component["integrity"] = {
            "verification": "sha256",
            "revision": None,
            "hashes": [
                {
                    "algorithm": "SHA-256",
                    "value": "0" * 64,
                    "artifact": "ghostscript-1.0.0.zip",
                }
            ],
        }
        license_data = cast(dict[str, Any], component["license"])
        license_data["selected_route"] = "AGPL-3.0-or-later"
        license_data["distribution_altering"] = False
        cast(dict[str, Any], component["packaging"])["ship_state"] = "not-shipped"

        issues = self._document_issues(document)

        self.assertIn("DEPENDENCY_DISTRIBUTION_DECISION_REQUIRED", _codes(issues))

    def test_unknown_license_identifiers_fail_closed(self) -> None:
        document = copy.deepcopy(self.baseline)
        cast(dict[str, Any], _component(document, "ruff")["license"])["expression"] = (
            "MIT OR Synthetic-Safe"
        )

        issues = self._document_issues(document)

        self.assertIn("DEPENDENCY_LICENSE_EXPRESSION_INVALID", _codes(issues))

    def test_hostile_document_parser_cannot_be_admitted_in_the_gui_process(self) -> None:
        document = copy.deepcopy(self.baseline)
        component = _component(document, "ruff")
        cast(dict[str, Any], component["binary"])["processes_untrusted_documents"] = True
        component["security"] = {
            "boundary": "gui-process",
            "network_needed_at_runtime": False,
            "adapter_required": False,
            "contract_tests_required": False,
        }

        issues = self._document_issues(document)

        self.assertIn("DEPENDENCY_SECURITY_BOUNDARY_INVALID", _codes(issues))

    def test_dependency_graph_mutations_fail_closed(self) -> None:
        def duplicate_component(document: dict[str, Any]) -> None:
            components = cast(list[dict[str, Any]], document["components"])
            components.append(copy.deepcopy(components[0]))

        def dangling_edge(document: dict[str, Any]) -> None:
            _relationships(document).append(
                {
                    "from": "ruff",
                    "type": "depends-on",
                    "to": "unknown-component",
                    "enabled": False,
                }
            )

        def self_edge(document: dict[str, Any]) -> None:
            _relationships(document).append(
                {"from": "ruff", "type": "depends-on", "to": "ruff", "enabled": True}
            )

        def prohibited_edge(document: dict[str, Any]) -> None:
            _relationships(document).append(
                {
                    "from": "ruff",
                    "type": "depends-on",
                    "to": "ghostscript",
                    "enabled": True,
                }
            )

        def cyclic_edge(document: dict[str, Any]) -> None:
            _relationships(document).append(
                {
                    "from": "attrs",
                    "type": "depends-on",
                    "to": "referencing",
                    "enabled": True,
                }
            )

        cases = (
            (duplicate_component, "DEPENDENCY_COMPONENT_ID_DUPLICATE"),
            (dangling_edge, "DEPENDENCY_RELATIONSHIP_REFERENCE_UNKNOWN"),
            (self_edge, "DEPENDENCY_RELATIONSHIP_SELF_REFERENCE"),
            (prohibited_edge, "DEPENDENCY_RELATIONSHIP_ADMISSION_INVALID"),
            (cyclic_edge, "DEPENDENCY_RELATIONSHIP_CYCLE"),
        )
        for mutate, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                document = copy.deepcopy(self.baseline)
                mutate(document)

                issues = self._document_issues(document)

                self.assertIn(expected_code, _codes(issues))

    def test_issue_limit_is_bounded_and_deterministic(self) -> None:
        document = copy.deepcopy(self.baseline)
        repeated_self_edge = {
            "from": "ruff",
            "type": "depends-on",
            "to": "ruff",
            "enabled": True,
        }
        _relationships(document).extend(copy.deepcopy(repeated_self_edge) for _ in range(300))

        first = self._document_issues(document)
        second = self._document_issues(document)

        self.assertEqual(MAX_REPORTED_ISSUES, len(first))
        self.assertEqual(1, sum(issue.code == "DEPENDENCY_ISSUE_LIMIT" for issue in first))
        self.assertEqual(
            [issue.render() for issue in first],
            [issue.render() for issue in second],
        )

    def test_repository_validation_is_offline_and_starts_no_processes(self) -> None:
        forbidden = AssertionError("dependency validation attempted external execution")
        with (
            patch("socket.create_connection", side_effect=forbidden),
            patch("urllib.request.urlopen", side_effect=forbidden),
            patch("subprocess.run", side_effect=forbidden),
            patch("subprocess.Popen", side_effect=forbidden),
            patch("os.system", side_effect=forbidden),
        ):
            summary, issues = validate_dependency_fixture_set(PROJECT_ROOT)

        self.assertEqual([], issues)
        self.assertIsNotNone(summary)
        if summary is not None:
            self.assertEqual(1, summary.positive_count)
            self.assertEqual(len(EXPECTED_NEGATIVE_CODES), summary.negative_count)


if __name__ == "__main__":
    unittest.main()

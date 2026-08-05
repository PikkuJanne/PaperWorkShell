from __future__ import annotations

import copy
import json
import shutil
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


REPOSITORY_POLICY_INPUTS = (
    Path(".github/workflows/quality.yml"),
    Path(".python-version"),
    Path("DECISIONS.md"),
    Path("TASKS.yaml"),
    Path("docs/development/dependency-policy.md"),
    Path("pyproject.toml"),
    Path("tools/dev.py"),
    Path("uv.lock"),
)


def _copy_repository_policy_inputs(root: Path) -> None:
    for relative_path in REPOSITORY_POLICY_INPUTS:
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative_path, target)


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
            (
                "POSIX path",
                "DEPENDENCY_SENSITIVE_TEXT_PROHIBITED",
                lambda component, value: cast(dict[str, Any], component["maintenance"]).__setitem__(
                    "note", value
                ),
                ("synthetic-user", "client.txt"),
            ),
        )
        values = {
            "credential URL": "https://synthetic-user:synthetic-password@example.invalid/project",
            "query credential": ("https://example.invalid/release?token=synthetic-query-secret"),
            "Windows path": r"C:\Applicants\synthetic-private-document.pdf",
            "UNC path": r"\\synthetic-private-host\share\document.pdf",
            "POSIX path": "/home/synthetic-user/private/client.txt",
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

        cases = (
            (
                "MIT AND AGPL-3.0-or-later",
                "DEPENDENCY_DISTRIBUTION_DECISION_REQUIRED",
            ),
            (
                "LicenseRef-Artifex-Commercial",
                "DEPENDENCY_DISTRIBUTION_DECISION_REQUIRED",
            ),
            ("LicenseRef-Qt-Commercial", "DEPENDENCY_LICENSE_ROUTE_INVALID"),
        )
        for expression, expected_code in cases:
            with self.subTest(expression=expression):
                document = copy.deepcopy(self.baseline)
                component = _component(document, "attrs")
                component["active_scopes"] = ["runtime"]
                license_data = cast(dict[str, Any], component["license"])
                license_data["expression"] = expression
                license_data["distribution_altering"] = False

                issues = self._document_issues(document)

                self.assertIn(expected_code, _codes(issues))

    def test_unknown_license_identifiers_fail_closed(self) -> None:
        document = copy.deepcopy(self.baseline)
        cast(dict[str, Any], _component(document, "ruff")["license"])["expression"] = (
            "MIT OR Synthetic-Safe"
        )

        issues = self._document_issues(document)

        self.assertIn("DEPENDENCY_LICENSE_EXPRESSION_INVALID", _codes(issues))

        document = copy.deepcopy(self.baseline)
        cast(dict[str, Any], document["license_refs"])["LicenseRef-Synthetic-Safe"] = {
            "name": "Synthetic unreviewed terms",
            "terms_url": "https://example.invalid/synthetic-terms",
            "review_status": "notice-closure-required",
        }
        cast(dict[str, Any], _component(document, "attrs")["license"])["expression"] = (
            "LicenseRef-Synthetic-Safe"
        )

        issues = self._document_issues(document)

        self.assertIn("DEPENDENCY_LICENSE_REF_UNREVIEWED", _codes(issues))

    def test_custom_license_urls_are_rejected_without_credential_disclosure(self) -> None:
        document = copy.deepcopy(self.baseline)
        canaries = ("synthetic-license-user", "synthetic-license-password")
        license_refs = cast(dict[str, dict[str, Any]], document["license_refs"])
        license_refs["LicenseRef-Artifex-Commercial"]["terms_url"] = (
            "https://synthetic-license-user:synthetic-license-password@127.0.0.1/terms"
        )

        issues = self._document_issues(document)
        rendered = "\n".join(issue.render() for issue in issues)

        self.assertIn("DEPENDENCY_SOURCE_URL_UNSAFE", _codes(issues))
        for canary in canaries:
            self.assertNotIn(canary, rendered)

        for unsafe_host in (
            "2130706433",
            "0x7f000001",
            "127.1",
            "0177.0.0.1",
            "%31%32%37.0.0.1",
        ):
            with self.subTest(unsafe_host=unsafe_host):
                document = copy.deepcopy(self.baseline)
                license_refs = cast(dict[str, dict[str, Any]], document["license_refs"])
                license_refs["LicenseRef-Artifex-Commercial"]["terms_url"] = (
                    f"https://{unsafe_host}/terms"
                )

                issues = self._document_issues(document)

                self.assertIn("DEPENDENCY_SOURCE_URL_UNSAFE", _codes(issues))

    def test_shipped_license_obligations_cannot_be_self_declared_away(self) -> None:
        document = copy.deepcopy(self.baseline)
        component = _component(document, "attrs")
        license_data = cast(dict[str, Any], component["license"])
        license_data["notice_required"] = False
        license_data["source_offer_required"] = False
        license_data["relinking_required"] = False
        packaging = cast(dict[str, Any], component["packaging"])
        packaging["ship_state"] = "bundled"
        packaging["redistribution_assets"] = ["sbom"]

        issues = self._document_issues(document)

        self.assertIn("DEPENDENCY_LICENSE_OBLIGATION_ASSET_MISSING", _codes(issues))

    def test_reviewed_source_and_license_identities_cannot_be_relabelled(self) -> None:
        cases: tuple[tuple[str, Callable[[dict[str, Any]], None], str], ...] = (
            (
                "project origin",
                lambda document: cast(
                    dict[str, Any], _component(document, "ruff")["source"]
                ).update({"project_url": "https://attacker.example/ruff"}),
                "DEPENDENCY_SOURCE_IDENTITY_INVALID",
            ),
            (
                "release origin",
                lambda document: cast(
                    dict[str, Any], _component(document, "ruff")["source"]
                ).update({"release_url": "https://attacker.example/releases/0.15.22/"}),
                "DEPENDENCY_RELEASE_PIN_INVALID",
            ),
            (
                "license origin",
                lambda document: cast(
                    dict[str, Any], _component(document, "cpython")["source"]
                ).update({"license_url": "https://example.com/fake-license"}),
                "DEPENDENCY_SOURCE_IDENTITY_INVALID",
            ),
            (
                "license expression",
                lambda document: cast(
                    dict[str, Any], _component(document, "git")["license"]
                ).update({"expression": "MIT"}),
                "DEPENDENCY_LICENSE_IDENTITY_INVALID",
            ),
            (
                "license path traversal",
                lambda document: cast(
                    dict[str, Any], _component(document, "gitleaks")["source"]
                ).update(
                    {
                        "license_url": (
                            "https://github.com/gitleaks/gitleaks/../../evil/repo/blob/main/LICENSE"
                        )
                    }
                ),
                "DEPENDENCY_SOURCE_URL_UNSAFE",
            ),
        )
        for label, mutate, expected_code in cases:
            with self.subTest(case=label):
                document = copy.deepcopy(self.baseline)
                mutate(document)

                issues = self._document_issues(document)

                self.assertIn(expected_code, _codes(issues))

        document = copy.deepcopy(self.baseline)
        license_refs = cast(dict[str, dict[str, Any]], document["license_refs"])
        license_refs["LicenseRef-PDFium-Third-Party"]["terms_url"] = (
            "https://example.com/fake-terms"
        )

        issues = self._document_issues(document)

        self.assertIn("DEPENDENCY_LICENSE_REF_UNREVIEWED", _codes(issues))

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

        document = copy.deepcopy(self.baseline)
        component = copy.deepcopy(_component(document, "tesseract-language-data"))
        component["component_id"] = "tessdata-eng-fast"
        component["admission_status"] = "approved"
        component["active_scopes"] = ["data"]
        component["version"] = {
            "constraint": "==1.0.0",
            "selected": "1.0.0",
            "pin_kind": "exact",
            "pin_source": "synthetic security test",
        }
        component["source"] = {
            "project_url": "https://github.com/tesseract-ocr/tessdata",
            "release_url": "https://github.com/tesseract-ocr/tessdata/releases/tag/v1.0.0",
            "license_url": "https://github.com/tesseract-ocr/tessdata/blob/main/LICENSE",
        }
        component["integrity"] = {
            "verification": "sha256",
            "revision": None,
            "hashes": [
                {
                    "algorithm": "SHA-256",
                    "value": "1" * 64,
                    "artifact": "tessdata-eng-fast-1.0.0.traineddata",
                }
            ],
        }
        component["security"] = {
            "boundary": "gui-process",
            "network_needed_at_runtime": False,
            "adapter_required": False,
            "contract_tests_required": False,
        }
        cast(dict[str, Any], component["binary"])["processes_untrusted_documents"] = False
        cast(dict[str, Any], component["maintenance"])["status"] = "active"
        cast(dict[str, Any], component["packaging"])["ship_state"] = "bundled"
        cast(dict[str, Any], component["sbom"])["purl"] = "pkg:generic/tessdata-eng-fast@1.0.0"
        cast(list[dict[str, Any]], document["components"]).append(component)

        issues = self._document_issues(document)

        self.assertIn("DEPENDENCY_SECURITY_BOUNDARY_INVALID", _codes(issues))

    def test_reviewed_admission_profile_cannot_move_tools_into_runtime(self) -> None:
        document = copy.deepcopy(self.baseline)
        component = _component(document, "mypy")
        component["active_scopes"] = ["development", "runtime"]
        cast(dict[str, Any], component["packaging"])["ship_state"] = "bundled"
        component["security"] = {
            "boundary": "gui-process",
            "network_needed_at_runtime": False,
            "adapter_required": False,
            "contract_tests_required": False,
        }

        issues = self._document_issues(document)

        self.assertIn("DEPENDENCY_ADMISSION_PROFILE_INVALID", _codes(issues))

        document = copy.deepcopy(self.baseline)
        component = _component(document, "qpdf")
        component["active_scopes"] = ["worker"]
        cast(dict[str, Any], component["packaging"])["ship_state"] = "bundled"

        issues = self._document_issues(document)

        self.assertIn("DEPENDENCY_ADMISSION_PROFILE_INVALID", _codes(issues))

        document = copy.deepcopy(self.baseline)
        component = _component(document, "pikepdf")
        cast(dict[str, Any], component["binary"])["processes_untrusted_documents"] = False
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

        def conflicting_edge_state(document: dict[str, Any]) -> None:
            relationship = copy.deepcopy(_relationships(document)[0])
            relationship["enabled"] = not relationship["enabled"]
            _relationships(document).append(relationship)

        cases = (
            (duplicate_component, "DEPENDENCY_COMPONENT_ID_DUPLICATE"),
            (dangling_edge, "DEPENDENCY_RELATIONSHIP_REFERENCE_UNKNOWN"),
            (self_edge, "DEPENDENCY_RELATIONSHIP_SELF_REFERENCE"),
            (prohibited_edge, "DEPENDENCY_RELATIONSHIP_ADMISSION_INVALID"),
            (cyclic_edge, "DEPENDENCY_RELATIONSHIP_CYCLE"),
            (conflicting_edge_state, "DEPENDENCY_RELATIONSHIP_DUPLICATE"),
        )
        for mutate, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                document = copy.deepcopy(self.baseline)
                mutate(document)

                issues = self._document_issues(document)

                self.assertIn(expected_code, _codes(issues))

    def test_issue_limit_is_bounded_and_deterministic(self) -> None:
        document = copy.deepcopy(self.baseline)
        components = cast(list[dict[str, Any]], document["components"])
        template = _component(document, "ruff")
        for index in range(200):
            component = copy.deepcopy(template)
            component_id = f"synthetic-{index:03d}"
            canary = rf"C:\Synthetic\private-{index}.txt"
            component["component_id"] = component_id
            component["name"] = canary
            cast(dict[str, Any], component["version"])["constraint"] = canary
            cast(dict[str, Any], component["license"])["distribution_note"] = canary
            cast(dict[str, Any], component["maintenance"])["note"] = canary
            cast(dict[str, Any], component["packaging"])["windows_impact"] = canary
            sbom = cast(dict[str, Any], component["sbom"])
            sbom["purl"] = f"pkg:pypi/{component_id}@0.15.22"
            sbom["supplier"] = canary
            components.append(component)
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

    def test_repository_rejects_unreviewed_build_configuration(self) -> None:
        cases = (
            (
                'build-backend = "uv_build"',
                'build-backend = "synthetic.backend"',
                "DEPENDENCY_DECLARATION_MISMATCH",
            ),
            (
                'requires = ["uv_build==0.11.32"]',
                'requires = ["uv_build==0.11.32", "synthetic-build-tool>=1"]',
                "DEPENDENCY_DECLARATION_VERSION_MISMATCH",
            ),
        )
        for original, replacement, expected_code in cases:
            with (
                self.subTest(expected_code=expected_code),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                _copy_repository_policy_inputs(root)
                project_path = root / "pyproject.toml"
                project = project_path.read_text(encoding="utf-8")
                self.assertIn(original, project)
                project_path.write_text(project.replace(original, replacement), encoding="utf-8")

                validated, issues = validate_dependency_data(
                    self.baseline,
                    self.schema,
                    document="synthetic-dependency-inventory.json",
                    root=root,
                    repository_checks=True,
                )

                self.assertIn(expected_code, _codes(issues))
                self.assertIsNone(validated)

    def test_repository_rejects_sbom_purl_identity_mismatch(self) -> None:
        document = copy.deepcopy(self.baseline)
        cast(dict[str, Any], _component(document, "ruff")["sbom"])["purl"] = (
            "pkg:pypi/synthetic-unrelated-name@999.0"
        )

        validated, issues = validate_dependency_data(
            document,
            self.schema,
            document="synthetic-dependency-inventory.json",
            root=PROJECT_ROOT,
            repository_checks=True,
        )

        self.assertIn("DEPENDENCY_SBOM_INPUT_INVALID", _codes(issues))
        self.assertIsNone(validated)

        document = copy.deepcopy(self.baseline)
        component = _component(document, "ruff")
        component["name"] = "Synthetic unrelated product"
        sbom = cast(dict[str, Any], component["sbom"])
        sbom["component_type"] = "data"
        sbom["supplier"] = "Synthetic unrelated supplier"
        sbom["transitive_components_from"] = "upstream-release-inventory"

        issues = self._document_issues(document)

        self.assertIn("DEPENDENCY_SBOM_INPUT_INVALID", _codes(issues))

    def test_concrete_version_integrity_and_release_identity_are_bound(self) -> None:
        cases: tuple[tuple[str, Callable[[dict[str, Any]], None], str], ...] = (
            (
                "pin-integrity pair",
                lambda document: _component(document, "attrs").update(
                    {
                        "integrity": {
                            "verification": "git-commit",
                            "revision": "0" * 40,
                            "hashes": [],
                        }
                    }
                ),
                "DEPENDENCY_RELEASE_PIN_INVALID",
            ),
            (
                "release URL version boundary",
                lambda document: cast(
                    dict[str, Any], _component(document, "attrs")["source"]
                ).update({"release_url": "https://example.invalid/releases/226.1.0"}),
                "DEPENDENCY_RELEASE_PIN_INVALID",
            ),
            (
                "artifact version boundary",
                lambda document: cast(
                    list[dict[str, Any]],
                    cast(dict[str, Any], _component(document, "uv-build")["integrity"])["hashes"],
                )[0].update({"artifact": "uv_build-0.11.320.tar.gz"}),
                "DEPENDENCY_RELEASE_PIN_INVALID",
            ),
            (
                "maintenance review",
                lambda document: cast(
                    dict[str, Any], _component(document, "ruff")["maintenance"]
                ).update({"status": "unselected"}),
                "DEPENDENCY_RELEASE_PIN_INVALID",
            ),
        )
        for label, mutate, expected_code in cases:
            with self.subTest(case=label):
                document = copy.deepcopy(self.baseline)
                mutate(document)

                issues = self._document_issues(document)

                self.assertIn(expected_code, _codes(issues))

        document = copy.deepcopy(self.baseline)
        component = _component(document, "pypdfium2")
        component["admission_status"] = "evaluated"
        component["version"] = {
            "constraint": ">=1.0.0",
            "selected": "1.0.0",
            "pin_kind": "minimum",
            "pin_source": "synthetic security test",
        }
        component["integrity"] = {
            "verification": "environment-version",
            "revision": None,
            "hashes": [],
        }

        issues = self._document_issues(document)

        self.assertIn("DEPENDENCY_SCHEMA_VALIDATION", _codes(issues))

        document = copy.deepcopy(self.baseline)
        component = _component(document, "pypdfium2")
        component["admission_status"] = "evaluated"
        component["version"] = {
            "constraint": "==1.0.0",
            "selected": "1.0.0",
            "pin_kind": "exact",
            "pin_source": "synthetic security test",
        }
        cast(dict[str, Any], component["source"])["release_url"] = (
            "https://pypi.org/project/pypdfium2/1.0.0/"
        )
        component["integrity"] = {
            "verification": "sha256",
            "revision": None,
            "hashes": [
                {
                    "algorithm": "SHA-256",
                    "value": "0" * 64,
                    "artifact": "pypdfium2-1.0.0-win_amd64.whl",
                }
            ],
        }
        component["artifact_evidence"] = {
            "artifact": "pypdfium2-1.0.0-win_amd64.whl",
            "url": (
                "https://files.pythonhosted.org/packages/synthetic/pypdfium2-1.0.0-win_amd64.whl"
            ),
            "target_platform": "windows-x64",
            "size_bytes": 1,
            "sha256": "0" * 64,
        }
        cast(dict[str, Any], component["sbom"])["purl"] = "pkg:pypi/pypdfium2@1.0.0"

        issues = self._document_issues(document)

        self.assertIn("DEPENDENCY_RELEASE_PIN_INVALID", _codes(issues))

        document = copy.deepcopy(self.baseline)
        component = _component(document, "gitleaks")
        integrity_hash = cast(
            list[dict[str, Any]], cast(dict[str, Any], component["integrity"])["hashes"]
        )[0]
        component["artifact_evidence"] = {
            "artifact": integrity_hash["artifact"],
            "url": (
                "https://github.com/gitleaks/gitleaks/releases/download/v0.0.1/"
                f"{integrity_hash['artifact']}"
            ),
            "target_platform": "windows-x64",
            "size_bytes": 1,
            "sha256": integrity_hash["value"],
        }

        issues = self._document_issues(document)

        self.assertIn("DEPENDENCY_RELEASE_PIN_INVALID", _codes(issues))

    def test_repository_rejects_spoofed_workflow_security_controls(self) -> None:
        cases = (
            ("fetch-depth: 0", "fetch-depth: 1"),
            ("persist-credentials: false", "persist-credentials: true"),
            ("check-latest: false", "check-latest: true"),
            ("enable-cache: false", "enable-cache: true"),
            ("restore-cache: false", "restore-cache: true"),
            ("save-cache: false", "save-cache: true"),
            (
                "download-from-astral-mirror: false",
                "download-from-astral-mirror: true",
            ),
            (
                "8941870a604e7c87ed24566b038d46c24ce76616254d2383c578f60c0677f202",
                "0" * 64,
            ),
            ("PWS_QPDF_EXECUTABLE=$qpdf", "PATH=$qpdf"),
            ("$version = (& $qpdf --version)[0]", "$version = (& $qpdf --help)[0]"),
        )
        for original, replacement in cases:
            with self.subTest(control=original), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                _copy_repository_policy_inputs(root)
                workflow_path = root / ".github" / "workflows" / "quality.yml"
                workflow = workflow_path.read_text(encoding="utf-8")
                self.assertIn(original, workflow)
                workflow_path.write_text(
                    workflow.replace(original, replacement),
                    encoding="utf-8",
                )

                validated, issues = validate_dependency_data(
                    self.baseline,
                    self.schema,
                    document="synthetic-dependency-inventory.json",
                    root=root,
                    repository_checks=True,
                )

                self.assertIn("DEPENDENCY_CONFIG_PIN_MISMATCH", _codes(issues))
                self.assertIsNone(validated)

    def test_lock_package_without_sdist_accepts_exact_windows_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _copy_repository_policy_inputs(root)
            lock_path = root / "uv.lock"
            lock = lock_path.read_text(encoding="utf-8")
            sdist_line = next(
                line
                for line in lock.splitlines()
                if line.startswith("sdist = ") and "attrs-26.1.0.tar.gz" in line
            )
            lock_path.write_text(lock.replace(f"{sdist_line}\n", "", 1), encoding="utf-8")
            document = copy.deepcopy(self.baseline)
            attrs_hashes = cast(
                list[dict[str, Any]],
                cast(dict[str, Any], _component(document, "attrs")["integrity"])["hashes"],
            )
            attrs_hashes[:] = [
                record for record in attrs_hashes if record["artifact"] != "attrs-26.1.0.tar.gz"
            ]

            validated, issues = validate_dependency_data(
                document,
                self.schema,
                document="synthetic-dependency-inventory.json",
                root=root,
                repository_checks=True,
            )

        self.assertEqual([], issues)
        self.assertIsNotNone(validated)

    def test_synthetic_evaluated_lock_edge_is_required_and_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _copy_repository_policy_inputs(root)
            lock_path = root / "uv.lock"
            lock = lock_path.read_text(encoding="utf-8")
            package_header = (
                'name = "pypdfium2"\n'
                'version = "5.12.1"\n'
                'source = { registry = "https://pypi.org/simple" }\n'
            )
            self.assertIn(package_header, lock)
            lock_path.write_text(
                lock.replace(
                    package_header,
                    package_header + 'dependencies = [{ name = "pyside6-essentials" }]\n',
                    1,
                ),
                encoding="utf-8",
            )
            document = copy.deepcopy(self.baseline)
            synthetic_edge = {
                "from": "pypdfium2",
                "type": "depends-on",
                "to": "pyside6-essentials",
                "enabled": False,
            }
            _relationships(document).append(synthetic_edge)

            validated, issues = validate_dependency_data(
                document,
                self.schema,
                document="synthetic-dependency-inventory.json",
                root=root,
                repository_checks=True,
            )
            self.assertEqual([], issues)
            self.assertIsNotNone(validated)

            missing = copy.deepcopy(document)
            _relationships(missing).remove(synthetic_edge)
            _, first_missing = validate_dependency_data(
                missing,
                self.schema,
                document="synthetic-dependency-inventory.json",
                root=root,
                repository_checks=True,
            )
            _, second_missing = validate_dependency_data(
                missing,
                self.schema,
                document="synthetic-dependency-inventory.json",
                root=root,
                repository_checks=True,
            )
            self.assertIn("DEPENDENCY_LOCK_RELATIONSHIP_MISMATCH", _codes(first_missing))
            self.assertEqual(
                [issue.render() for issue in first_missing],
                [issue.render() for issue in second_missing],
            )

            enabled = copy.deepcopy(document)
            next(
                relationship
                for relationship in _relationships(enabled)
                if relationship["from"] == "pypdfium2"
                and relationship["type"] == "depends-on"
                and relationship["to"] == "pyside6-essentials"
            )["enabled"] = True
            _, first_enabled = validate_dependency_data(
                enabled,
                self.schema,
                document="synthetic-dependency-inventory.json",
                root=root,
                repository_checks=True,
            )
            _, second_enabled = validate_dependency_data(
                enabled,
                self.schema,
                document="synthetic-dependency-inventory.json",
                root=root,
                repository_checks=True,
            )

        self.assertIn("DEPENDENCY_RELATIONSHIP_ADMISSION_INVALID", _codes(first_enabled))
        self.assertIn("DEPENDENCY_LOCK_RELATIONSHIP_MISMATCH", _codes(first_enabled))
        self.assertEqual(
            [issue.render() for issue in first_enabled],
            [issue.render() for issue in second_enabled],
        )

    def test_repository_rejects_lock_origin_duplicates_and_optional_declarations(self) -> None:
        def duplicate_attrs(root: Path) -> None:
            path = root / "uv.lock"
            lock = path.read_text(encoding="utf-8")
            sections = lock.split("[[package]]")
            attrs = next(
                section for section in sections if section.lstrip().startswith('name = "attrs"')
            )
            path.write_text(lock + "\n[[package]]" + attrs, encoding="utf-8")

        def replace_wheel_origin(root: Path) -> None:
            path = root / "uv.lock"
            lock = path.read_text(encoding="utf-8")
            original = (
                "https://files.pythonhosted.org/packages/cb/46/"
                "4965251734c2b6fcdca1b1b187d20bcac3af0ee5b083b89c910bb961ce3a/"
                "ruff-0.15.22-py3-none-win_amd64.whl"
            )
            self.assertIn(original, lock)
            path.write_text(
                lock.replace(original, "https://example.invalid/unreviewed-ruff-win_amd64.whl"),
                encoding="utf-8",
            )

        def add_optional_dependency(root: Path) -> None:
            path = root / "pyproject.toml"
            project = path.read_text(encoding="utf-8")
            project += '\n[project.optional-dependencies]\nfeature = ["attrs==26.1.0"]\n'
            path.write_text(project, encoding="utf-8")

        def replace_windows_wheel_hash(root: Path) -> None:
            path = root / "uv.lock"
            lock = path.read_text(encoding="utf-8")
            original = "9be63ba1eb936acd2d1342fb8337c356353706fce233b2a15a09a97037e6acde"
            self.assertIn(original, lock)
            path.write_text(lock.replace(original, "0" * 64), encoding="utf-8")

        def replace_root_version(root: Path) -> None:
            path = root / "uv.lock"
            lock = path.read_text(encoding="utf-8")
            original = 'name = "paperworkshell"\nversion = "0.0.0"'
            self.assertIn(original, lock)
            path.write_text(
                lock.replace(original, 'name = "paperworkshell"\nversion = "999.0.0"'),
                encoding="utf-8",
            )

        cases = (
            (duplicate_attrs, "DEPENDENCY_LOCK_COMPONENT_MISMATCH"),
            (replace_wheel_origin, "DEPENDENCY_LOCK_SOURCE_MISMATCH"),
            (add_optional_dependency, "DEPENDENCY_DECLARATION_MISMATCH"),
            (replace_windows_wheel_hash, "DEPENDENCY_LOCK_HASH_MISMATCH"),
            (replace_root_version, "DEPENDENCY_LOCK_METADATA_MISMATCH"),
        )
        for mutate, expected_code in cases:
            with (
                self.subTest(expected_code=expected_code),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                _copy_repository_policy_inputs(root)
                mutate(root)

                validated, issues = validate_dependency_data(
                    self.baseline,
                    self.schema,
                    document="synthetic-dependency-inventory.json",
                    root=root,
                    repository_checks=True,
                )

                self.assertIn(expected_code, _codes(issues))
                self.assertIsNone(validated)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _copy_repository_policy_inputs(root)
            add_optional_dependency(root)
            document = copy.deepcopy(self.baseline)
            _component(document, "attrs")["relationship"] = "direct"

            validated, issues = validate_dependency_data(
                document,
                self.schema,
                document="synthetic-dependency-inventory.json",
                root=root,
                repository_checks=True,
            )

            self.assertIn("DEPENDENCY_LOCK_METADATA_MISMATCH", _codes(issues))
            self.assertIsNone(validated)

    def test_repository_rejects_yaml_and_job_control_bypasses(self) -> None:
        cases = (
            (
                "          save-cache: false",
                "          save-cache: false\n          save-cache: true",
                "DEPENDENCY_REPOSITORY_INPUT_INVALID",
            ),
            (
                "    timeout-minutes: 20",
                "    timeout-minutes: 20\n    permissions:\n      contents: write\n      id-token: write",
                "DEPENDENCY_CONFIG_PIN_MISMATCH",
            ),
            (
                "      - name: Run local-equivalent quality suite\n"
                "        run: python tools/dev.py check",
                "      - name: Run local-equivalent quality suite\n"
                "        if: false\n"
                "        run: python tools/dev.py check",
                "DEPENDENCY_CONFIG_PIN_MISMATCH",
            ),
            (
                "  push:\n  pull_request:\n  workflow_dispatch:",
                "  workflow_dispatch:",
                "DEPENDENCY_CONFIG_PIN_MISMATCH",
            ),
            (
                "          Invoke-WebRequest -Uri $uri -OutFile $archive",
                '          $uri = "https://example.invalid/unreviewed.zip"\n'
                "          Invoke-WebRequest -Uri $uri -OutFile $archive",
                "DEPENDENCY_CONFIG_PIN_MISMATCH",
            ),
            (
                "          if ($actual -ne $expected) {",
                "          $expected = $actual\n          if ($actual -ne $expected) {",
                "DEPENDENCY_CONFIG_PIN_MISMATCH",
            ),
            (
                '            throw "Gitleaks archive checksum mismatch."',
                '            Write-Output "Gitleaks archive checksum mismatch."',
                "DEPENDENCY_CONFIG_PIN_MISMATCH",
            ),
            (
                '          & (Join-Path $destination "gitleaks.exe") version',
                '          & (Join-Path $destination "gitleaks.exe") version\n'
                "          Invoke-RestMethod -Uri https://example.invalid/synthetic",
                "DEPENDENCY_CONFIG_PIN_MISMATCH",
            ),
        )
        for original, replacement, expected_code in cases:
            with (
                self.subTest(expected_code=expected_code),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                _copy_repository_policy_inputs(root)
                path = root / ".github" / "workflows" / "quality.yml"
                workflow = path.read_text(encoding="utf-8")
                self.assertIn(original, workflow)
                path.write_text(workflow.replace(original, replacement), encoding="utf-8")

                validated, issues = validate_dependency_data(
                    self.baseline,
                    self.schema,
                    document="synthetic-dependency-inventory.json",
                    root=root,
                    repository_checks=True,
                )

                self.assertIn(expected_code, _codes(issues))
                self.assertIsNone(validated)

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

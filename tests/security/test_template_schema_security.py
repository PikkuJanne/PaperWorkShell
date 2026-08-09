from __future__ import annotations

import builtins
import copy
import importlib
import json
import socket
import subprocess
import tempfile
import unittest
import urllib.request
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock, patch

from tools.validate_templates import (
    MAX_JSON_COLLECTION_ITEMS,
    MAX_JSON_DEPTH,
    MAX_JSON_NODES,
    MAX_JSON_STRING_CODEPOINTS,
    MAX_SAFE_INTEGER,
    MAX_TEMPLATE_BYTES,
    TemplateIssue,
    load_template_schema,
    validate_template_data,
    validate_template_document,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_TEMPLATE_PATH = PROJECT_ROOT / "schemas/examples/template-v1-minimal.json"

JsonPath = tuple[str | int, ...]


def _canonical_template() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(CANONICAL_TEMPLATE_PATH.read_text(encoding="utf-8")),
    )


def _object_paths(value: object, path: JsonPath = ()) -> list[JsonPath]:
    paths: list[JsonPath] = []
    if isinstance(value, dict):
        paths.append(path)
        for key, child in cast(dict[str, object], value).items():
            paths.extend(_object_paths(child, (*path, key)))
    elif isinstance(value, list):
        for index, child in enumerate(cast(list[object], value)):
            paths.extend(_object_paths(child, (*path, index)))
    return paths


def _object_at(document: dict[str, Any], path: JsonPath) -> dict[str, Any]:
    current: object = document
    for component in path:
        if isinstance(component, str):
            current = cast(dict[str, object], current)[component]
        else:
            current = cast(list[object], current)[component]
    return cast(dict[str, Any], current)


def _raw(document: object) -> bytes:
    return json.dumps(document, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


class TemplateSchemaSecurityTests(unittest.TestCase):
    schema: dict[str, Any]
    template: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        schema, issues = load_template_schema(PROJECT_ROOT)
        if schema is None:
            rendered = "; ".join(issue.render() for issue in issues)
            raise AssertionError(f"canonical template schema did not load: {rendered}")
        cls.schema = schema
        cls.template = _canonical_template()

    def assert_has_code(
        self,
        issues: list[TemplateIssue],
        expected_code: str,
    ) -> None:
        self.assertIn(expected_code, {issue.code for issue in issues})

    def validate_data(
        self,
        document: dict[str, Any],
        *,
        as_of: date | None = None,
    ) -> list[TemplateIssue]:
        return validate_template_data(
            document,
            self.schema,
            document="synthetic-template.json",
            as_of=as_of,
        )

    def validate_raw(self, raw: bytes) -> list[TemplateIssue]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "synthetic-template.json"
            path.write_bytes(raw)
            _, issues = validate_template_document(path, root, schema=self.schema)
            return issues

    def load_mutated_schema(self, schema: dict[str, Any]) -> list[TemplateIssue]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            schema_directory = root / "schemas"
            schema_directory.mkdir()
            (schema_directory / "template-v1.schema.json").write_text(
                json.dumps(schema, ensure_ascii=True),
                encoding="utf-8",
            )
            loaded, issues = load_template_schema(root)
            self.assertIsNone(loaded)
            return issues

    def test_template_byte_limit_accepts_exact_boundary_and_rejects_one_over(self) -> None:
        encoded = _raw(self.template)
        self.assertLess(len(encoded), MAX_TEMPLATE_BYTES)
        exact = encoded + b" " * (MAX_TEMPLATE_BYTES - len(encoded))

        self.assertEqual([], self.validate_raw(exact))
        self.assert_has_code(
            self.validate_raw(exact + b" "),
            "TEMPLATE_INPUT_TOO_LARGE",
        )

    def test_depth_node_string_and_collection_limits_fail_closed(self) -> None:
        nested: object = None
        for _ in range(MAX_JSON_DEPTH + 1):
            nested = [nested]
        depth_heavy = {"schema_version": 1, "probe": nested}
        string_heavy = {
            "schema_version": 1,
            "probe": "x" * (MAX_JSON_STRING_CODEPOINTS + 1),
        }
        collection_heavy = {
            "schema_version": 1,
            "probe": [None] * (MAX_JSON_COLLECTION_ITEMS + 1),
        }
        node_heavy = {
            "schema_version": 1,
            "probe": [
                {"a": None, "b": None, "c": None, "d": None}
                for _ in range(MAX_JSON_COLLECTION_ITEMS)
            ],
        }
        self.assertGreater(2 + MAX_JSON_COLLECTION_ITEMS * 9, MAX_JSON_NODES)

        for label, document in (
            ("depth", depth_heavy),
            ("string", string_heavy),
            ("collection", collection_heavy),
            ("nodes", node_heavy),
        ):
            with self.subTest(label=label):
                self.assert_has_code(
                    self.validate_raw(_raw(document)),
                    "TEMPLATE_JSON_RESOURCE_LIMIT",
                )

    def test_duplicate_nonstandard_float_and_boolean_version_values_are_rejected(self) -> None:
        canonical = _raw(self.template).decode("utf-8")
        cases = (
            (
                canonical.replace(
                    '"schema_version":1',
                    '"schema_version":1,"schema_version":1',
                    1,
                ).encode(),
                "TEMPLATE_JSON_DUPLICATE_KEY",
            ),
            (
                canonical.replace('"schema_version":1', '"schema_version":NaN', 1).encode(),
                "TEMPLATE_JSON_NONSTANDARD_CONSTANT",
            ),
            (
                canonical.replace('"schema_version":1', '"schema_version":Infinity', 1).encode(),
                "TEMPLATE_JSON_NONSTANDARD_CONSTANT",
            ),
            (
                canonical.replace('"schema_version":1', '"schema_version":1.0', 1).encode(),
                "TEMPLATE_JSON_NONSTANDARD_NUMBER",
            ),
            (
                canonical.replace(
                    '"schema_version":1',
                    f'"schema_version":{MAX_SAFE_INTEGER + 1}',
                    1,
                ).encode(),
                "TEMPLATE_JSON_NONSTANDARD_NUMBER",
            ),
            (
                canonical.replace('"schema_version":1', '"schema_version":true', 1).encode(),
                "TEMPLATE_VERSION_INVALID",
            ),
        )
        for raw, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                self.assert_has_code(self.validate_raw(raw), expected_code)

    def test_schema_identity_external_dynamic_and_cycle_changes_fail_closed(self) -> None:
        identity = copy.deepcopy(self.schema)
        identity["$id"] = "https://synthetic.invalid/substitute.json"

        external = copy.deepcopy(self.schema)
        external_defs = cast(dict[str, Any], external["$defs"])
        external_defs["token"] = {"$ref": "https://synthetic.invalid/schema.json"}

        dynamic = copy.deepcopy(self.schema)
        dynamic["$dynamicRef"] = "#"

        cycle = copy.deepcopy(self.schema)
        cycle_defs = cast(dict[str, Any], cycle["$defs"])
        cycle_defs["token"] = {"$ref": "#/$defs/token"}

        fingerprint = copy.deepcopy(self.schema)
        fingerprint["title"] = "Synthetic permissive substitute"

        for label, mutated, expected_code in (
            ("identity", identity, "TEMPLATE_SCHEMA_IDENTITY_INVALID"),
            ("external", external, "TEMPLATE_SCHEMA_REFERENCE_UNSAFE"),
            ("dynamic", dynamic, "TEMPLATE_SCHEMA_REFERENCE_FEATURE_UNSAFE"),
            ("cycle", cycle, "TEMPLATE_SCHEMA_REFERENCE_CYCLE"),
            ("fingerprint", fingerprint, "TEMPLATE_SCHEMA_IDENTITY_MISMATCH"),
        ):
            with self.subTest(label=label):
                self.assert_has_code(self.load_mutated_schema(mutated), expected_code)

    def test_prohibited_capabilities_are_rejected_at_every_object_boundary(self) -> None:
        canary = "SYNTHETIC_PRIVATE_TEMPLATE_VALUE"
        object_paths = _object_paths(self.template)
        self.assertGreater(len(object_paths), 20)
        probes = (
            ("command-line", "TEMPLATE_EXECUTABLE_MEMBER"),
            ("IMPORT", "TEMPLATE_EXECUTABLE_MEMBER"),
            ("expression", "TEMPLATE_EXECUTABLE_MEMBER"),
            ("file.path", "TEMPLATE_EXECUTABLE_MEMBER"),
            ("condition", "TEMPLATE_ELIGIBILITY_MEMBER"),
            ("eligible-if", "TEMPLATE_ELIGIBILITY_MEMBER"),
            ("is.trusted", "TEMPLATE_TRUST_ASSERTION_MEMBER"),
        )

        for path in object_paths:
            for member, expected_code in probes:
                with self.subTest(path=path, member=member):
                    document = copy.deepcopy(self.template)
                    _object_at(document, path)[member] = canary
                    issues = self.validate_data(document)
                    self.assert_has_code(issues, expected_code)
                    rendered = "\n".join(issue.render() for issue in issues)
                    self.assertNotIn(canary, rendered)

    def test_cross_reference_date_count_mode_limit_and_stem_mutations_reject(self) -> None:
        cases: list[tuple[str, dict[str, Any], str]] = []

        duplicate_requirement = copy.deepcopy(self.template)
        duplicate_requirement["requirements"][1]["requirement_id"] = "application-form"
        cases.append(("duplicate-requirement", duplicate_requirement, "TEMPLATE_ID_DUPLICATE"))

        unknown_order = copy.deepcopy(self.template)
        unknown_order["requirement_order"][0] = "unknown-requirement"
        cases.append(
            (
                "unknown-order-reference",
                unknown_order,
                "TEMPLATE_REQUIREMENT_REFERENCE_UNKNOWN",
            )
        )

        unknown_warning_scope = copy.deepcopy(self.template)
        unknown_warning_scope["warnings"][0]["scope"]["requirement_id"] = "unknown-requirement"
        cases.append(
            (
                "unknown-warning-reference",
                unknown_warning_scope,
                "TEMPLATE_REQUIREMENT_REFERENCE_UNKNOWN",
            )
        )

        inverted_count = copy.deepcopy(self.template)
        inverted_count["requirements"][0]["count"] = {
            "unit": "documents",
            "minimum": 2,
            "maximum": 1,
        }
        cases.append(("inverted-count", inverted_count, "TEMPLATE_COUNT_RANGE_INVALID"))

        invalid_default = copy.deepcopy(self.template)
        invalid_default["output"]["allowed_modes"] = ["separate_pdfs"]
        cases.append(("invalid-default-mode", invalid_default, "TEMPLATE_DEFAULT_MODE_INVALID"))

        duplicate_mode = copy.deepcopy(self.template)
        duplicate_mode["output"]["allowed_modes"] = ["combined_pdf", "combined_pdf"]
        cases.append(("duplicate-mode", duplicate_mode, "TEMPLATE_SCHEMA_VALIDATION"))

        invalid_limit = copy.deepcopy(self.template)
        invalid_limit["output"]["limits"]["maximum_file_bytes"] = 20_000_000
        cases.append(("incoherent-limit", invalid_limit, "TEMPLATE_OUTPUT_LIMIT_INVALID"))

        missing_stem = copy.deepcopy(self.template)
        missing_stem["output"]["filename_policy"]["requirement_stems"].pop()
        cases.append(
            (
                "missing-requirement-stem",
                missing_stem,
                "TEMPLATE_REQUIREMENT_STEMS_INCOMPLETE",
            )
        )

        colliding_stem = copy.deepcopy(self.template)
        colliding_stem["output"]["filename_policy"]["requirement_stems"][0]["stem"] = (
            "generic-application-packet"
        )
        cases.append(
            (
                "colliding-stem",
                colliding_stem,
                "TEMPLATE_FILENAME_STEM_COLLISION",
            )
        )

        invalid_lifecycle = copy.deepcopy(self.template)
        invalid_lifecycle["lifecycle"] = {
            "verified_on": "2027-01-01",
            "review_due_on": "2026-12-01",
            "expires_on": "2027-08-01",
        }
        cases.append(
            (
                "invalid-lifecycle-order",
                invalid_lifecycle,
                "TEMPLATE_LIFECYCLE_ORDER_INVALID",
            )
        )

        for label, document, expected_code in cases:
            with self.subTest(label=label):
                self.assert_has_code(self.validate_data(document), expected_code)

    def test_explicit_as_of_expiry_boundaries_are_deterministic(self) -> None:
        verified_on = date.fromisoformat(self.template["lifecycle"]["verified_on"])
        review_due = date.fromisoformat(self.template["lifecycle"]["review_due_on"])
        expiry = date.fromisoformat(self.template["lifecycle"]["expires_on"])

        self.assert_has_code(
            self.validate_data(
                copy.deepcopy(self.template),
                as_of=verified_on.fromordinal(verified_on.toordinal() - 1),
            ),
            "TEMPLATE_LIFECYCLE_AS_OF_INVALID",
        )
        self.assertEqual([], self.validate_data(copy.deepcopy(self.template), as_of=verified_on))
        self.assertEqual([], self.validate_data(copy.deepcopy(self.template), as_of=review_due))
        self.assertEqual(
            [],
            self.validate_data(
                copy.deepcopy(self.template),
                as_of=expiry.fromordinal(expiry.toordinal() - 1),
            ),
        )
        self.assertEqual([], self.validate_data(copy.deepcopy(self.template), as_of=expiry))
        self.assert_has_code(
            self.validate_data(
                copy.deepcopy(self.template),
                as_of=expiry.fromordinal(expiry.toordinal() + 1),
            ),
            "TEMPLATE_EXPIRED",
        )

    def test_provenance_url_credentials_and_fragments_are_rejected_without_leakage(self) -> None:
        canary = "synthetic-provenance-secret"
        for source_url in (
            f"https://user:{canary}@synthetic.invalid/template",
            f"https://synthetic.invalid/template#{canary}",
        ):
            with self.subTest(kind="credentials" if "@" in source_url else "fragment"):
                document = copy.deepcopy(self.template)
                document["provenance"]["source_url"] = source_url
                issues = self.validate_data(document)
                self.assert_has_code(issues, "TEMPLATE_PROVENANCE_URL_INVALID")
                self.assertNotIn(canary, "\n".join(issue.render() for issue in issues))

    def test_hostile_values_and_absolute_paths_are_absent_from_diagnostics(self) -> None:
        canary = "SYNTHETIC_DIAGNOSTIC_CANARY_4F6C"
        document = copy.deepcopy(self.template)
        document[canary] = f"../../{canary}/private-template.json"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / f"{canary}.json"
            path.write_bytes(_raw(document))
            _, issues = validate_template_document(
                path,
                PROJECT_ROOT,
                schema=self.schema,
            )

        self.assertTrue(issues)
        rendered = "\n".join(issue.render() for issue in issues)
        self.assertNotIn(canary, rendered)
        self.assertNotIn(str(path), rendered)
        self.assertNotIn(str(path.parent), rendered)
        self.assertIn("external-template.json", rendered)

    def test_inert_narrative_and_https_provenance_trigger_no_common_side_effects(self) -> None:
        document = copy.deepcopy(self.template)
        document["instructions"][0]["text"] = (
            "Display $(synthetic-command); && <strong>review this text</strong> as plain guidance."
        )
        document["provenance"]["source_url"] = (
            "https://synthetic.invalid/templates/generic-portal-application"
        )

        targets: tuple[tuple[object, str], ...] = (
            (builtins, "eval"),
            (builtins, "exec"),
            (builtins, "open"),
            (importlib, "import_module"),
            (Path, "open"),
            (socket, "create_connection"),
            (subprocess, "Popen"),
            (subprocess, "run"),
            (urllib.request, "urlopen"),
        )
        mocks: list[Mock] = []
        with ExitStack() as stack:
            for owner, attribute in targets:
                mocked = stack.enter_context(
                    patch.object(
                        owner,
                        attribute,
                        side_effect=AssertionError(
                            f"unexpected side effect: {type(owner).__name__}.{attribute}"
                        ),
                    )
                )
                mocks.append(mocked)
            issues = self.validate_data(document)

        self.assertEqual([], issues)
        for mocked in mocks:
            mocked.assert_not_called()

        # These spies cover this pure validation call only. PWS-0401 must separately prove that
        # its loader does not dereference provenance or execute template-controlled values.


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from typing import Any, ClassVar, cast

from tools.validate_dependencies import (
    EXPECTED_NEGATIVE_CODES,
    INVALID_FIXTURE_DIRECTORY,
    INVENTORY_PATH,
    DependencyIssue,
    load_dependency_schema,
    validate_dependency_data,
    validate_dependency_document,
    validate_dependency_fixture_set,
)

ROOT = Path(__file__).resolve().parents[2]


def _load_inventory() -> dict[str, Any]:
    loaded = json.loads((ROOT / INVENTORY_PATH).read_text(encoding="utf-8"))
    return cast(dict[str, Any], loaded)


def _component(data: dict[str, Any], component_id: str) -> dict[str, Any]:
    components = cast(list[dict[str, Any]], data["components"])
    return next(component for component in components if component["component_id"] == component_id)


def _relationship(data: dict[str, Any], source: str, target: str) -> dict[str, Any]:
    relationships = cast(list[dict[str, Any]], data["relationships"])
    return next(
        relationship
        for relationship in relationships
        if relationship["from"] == source and relationship["to"] == target
    )


class DependencyInventoryValidationTests(unittest.TestCase):
    schema: ClassVar[dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        schema, issues = load_dependency_schema()
        if schema is None:
            raise AssertionError([issue.render() for issue in issues])
        cls.schema = schema

    def _issues(
        self,
        data: dict[str, Any],
        *,
        repository_checks: bool = False,
    ) -> list[DependencyIssue]:
        _, issues = validate_dependency_data(
            data,
            self.schema,
            document="synthetic-dependency-inventory.json",
            root=ROOT,
            repository_checks=repository_checks,
        )
        return issues

    def _codes(
        self,
        data: dict[str, Any],
        *,
        repository_checks: bool = False,
    ) -> set[str]:
        return {issue.code for issue in self._issues(data, repository_checks=repository_checks)}

    def test_checked_in_inventory_and_rejected_examples_pass(self) -> None:
        summary, issues = validate_dependency_fixture_set()

        self.assertEqual([], issues)
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(1, summary.positive_count)
        self.assertEqual(len(EXPECTED_NEGATIVE_CODES), summary.negative_count)

    def test_each_negative_fixture_is_rejected_for_its_intended_reason(self) -> None:
        fixture_directory = ROOT / INVALID_FIXTURE_DIRECTORY
        actual_names = {path.name for path in fixture_directory.glob("*.json")}
        self.assertEqual(set(EXPECTED_NEGATIVE_CODES), actual_names)

        for filename, expected_code in EXPECTED_NEGATIVE_CODES.items():
            with self.subTest(filename=filename):
                _, issues = validate_dependency_document(
                    fixture_directory / filename,
                    schema=self.schema,
                )
                self.assertIn(expected_code, {issue.code for issue in issues})

    def test_version_dispatch_rejects_missing_boolean_string_old_and_future(self) -> None:
        cases: tuple[tuple[object | None, str], ...] = (
            (None, "DEPENDENCY_VERSION_INVALID"),
            (True, "DEPENDENCY_VERSION_INVALID"),
            ("1", "DEPENDENCY_VERSION_INVALID"),
            (0, "DEPENDENCY_VERSION_UNSUPPORTED"),
            (2, "DEPENDENCY_VERSION_UNSUPPORTED"),
        )
        for value, expected_code in cases:
            with self.subTest(value=value):
                data = _load_inventory()
                if value is None:
                    del data["schema_version"]
                else:
                    data["schema_version"] = value
                self.assertEqual({expected_code}, self._codes(data))

    def test_validation_does_not_mutate_valid_input(self) -> None:
        data = _load_inventory()
        original = copy.deepcopy(data)

        validated, issues = validate_dependency_data(
            data,
            self.schema,
            document="synthetic-dependency-inventory.json",
        )

        self.assertEqual([], issues)
        self.assertEqual(original, data)
        self.assertEqual(original, validated)
        self.assertIsNot(data, validated)

    def test_schema_rejects_unknown_component_members(self) -> None:
        data = _load_inventory()
        _component(data, "jsonschema")["executable_hook"] = "disabled"

        self.assertEqual({"DEPENDENCY_SCHEMA_VALIDATION"}, self._codes(data))

    def test_duplicate_component_ids_and_non_null_purls_are_rejected(self) -> None:
        cases: tuple[tuple[str, str], ...] = (
            ("component_id", "DEPENDENCY_COMPONENT_ID_DUPLICATE"),
            ("purl", "DEPENDENCY_PURL_DUPLICATE"),
        )
        for mutation, expected_code in cases:
            with self.subTest(mutation=mutation):
                data = _load_inventory()
                if mutation == "component_id":
                    duplicate = copy.deepcopy(_component(data, "jsonschema"))
                    duplicate["sbom"]["purl"] = "pkg:pypi/jsonschema-copy@4.26.0"
                    cast(list[dict[str, Any]], data["components"]).append(duplicate)
                else:
                    _component(data, "attrs")["sbom"]["purl"] = _component(data, "jsonschema")[
                        "sbom"
                    ]["purl"]
                self.assertIn(expected_code, self._codes(data))

    def test_duplicate_artifact_name_with_a_different_hash_is_rejected(self) -> None:
        data = _load_inventory()
        hashes = cast(list[dict[str, Any]], _component(data, "jsonschema")["integrity"]["hashes"])
        duplicate_artifact = copy.deepcopy(hashes[0])
        duplicate_artifact["value"] = "0" * 64
        hashes.append(duplicate_artifact)

        self.assertIn("DEPENDENCY_HASH_ARTIFACT_DUPLICATE", self._codes(data))

    def test_license_expression_and_selected_route_are_checked(self) -> None:
        cases: tuple[tuple[str, str], ...] = (
            ("unknown-expression", "DEPENDENCY_LICENSE_EXPRESSION_INVALID"),
            ("missing-route", "DEPENDENCY_LICENSE_ROUTE_REQUIRED"),
            ("wrong-route", "DEPENDENCY_LICENSE_ROUTE_INVALID"),
        )
        for mutation, expected_code in cases:
            with self.subTest(mutation=mutation):
                data = _load_inventory()
                license_data = cast(dict[str, Any], _component(data, "uv")["license"])
                if mutation == "unknown-expression":
                    license_data["expression"] = "Unknown-1.0"
                elif mutation == "missing-route":
                    license_data.pop("selected_route")
                else:
                    license_data["selected_route"] = "BSD-3-Clause"
                self.assertIn(expected_code, self._codes(data))

    def test_distribution_changing_and_hard_gated_components_cannot_be_admitted(self) -> None:
        data = _load_inventory()
        ghostscript = _component(data, "ghostscript")
        ghostscript["license"]["distribution_altering"] = False

        self.assertIn("DEPENDENCY_DISTRIBUTION_DECISION_REQUIRED", self._codes(data))

    def test_hostile_document_processors_require_worker_boundary_adapter_and_tests(self) -> None:
        cases: tuple[tuple[str, object], ...] = (
            ("boundary", "gui-process"),
            ("adapter_required", False),
            ("contract_tests_required", False),
        )
        for member, value in cases:
            with self.subTest(member=member):
                data = _load_inventory()
                _component(data, "pikepdf")["security"][member] = value
                self.assertIn("DEPENDENCY_SECURITY_BOUNDARY_INVALID", self._codes(data))

    def test_review_dates_and_source_urls_are_checked_semantically(self) -> None:
        cases: tuple[tuple[str, str], ...] = (
            ("component-review-after-inventory", "DEPENDENCY_REVIEW_DATE_ORDER_INVALID"),
            ("future-inventory-review", "DEPENDENCY_REVIEW_DATE_ORDER_INVALID"),
            ("credentialed-release-url", "DEPENDENCY_SOURCE_URL_UNSAFE"),
        )
        for mutation, expected_code in cases:
            with self.subTest(mutation=mutation):
                data = _load_inventory()
                if mutation == "component-review-after-inventory":
                    _component(data, "jsonschema")["maintenance"]["checked_on"] = "2026-08-04"
                elif mutation == "future-inventory-review":
                    data["reviewed_on"] = "9999-12-31"
                else:
                    _component(data, "jsonschema")["source"]["release_url"] = (
                        "https://user:password@example.invalid/release"
                    )
                self.assertIn(expected_code, self._codes(data))

    def test_renderer_families_require_complete_assessments(self) -> None:
        data = _load_inventory()
        _component(data, "pdfium").pop("renderer_assessment")

        self.assertIn("DEPENDENCY_RENDERER_ASSESSMENT_REQUIRED", self._codes(data))

    def test_release_components_require_exact_provenance_and_sbom_mapping(self) -> None:
        cases = ("nonexact-pin", "missing-sbom")
        for mutation in cases:
            with self.subTest(mutation=mutation):
                data = _load_inventory()
                if mutation == "nonexact-pin":
                    _component(data, "cpython")["packaging"]["ship_state"] = "bundled"
                else:
                    uv = _component(data, "uv")
                    uv["packaging"]["ship_state"] = "bundled"
                    uv["packaging"]["redistribution_assets"].remove("sbom")
                self.assertIn("DEPENDENCY_RELEASE_PIN_INVALID", self._codes(data))

    def test_relationship_identity_references_admission_and_cycles_are_checked(self) -> None:
        cases: tuple[tuple[str, str], ...] = (
            ("duplicate", "DEPENDENCY_RELATIONSHIP_DUPLICATE"),
            ("unknown", "DEPENDENCY_RELATIONSHIP_REFERENCE_UNKNOWN"),
            ("self", "DEPENDENCY_RELATIONSHIP_SELF_REFERENCE"),
            ("unadmitted", "DEPENDENCY_RELATIONSHIP_ADMISSION_INVALID"),
            ("cycle", "DEPENDENCY_RELATIONSHIP_CYCLE"),
        )
        for mutation, expected_code in cases:
            with self.subTest(mutation=mutation):
                data = _load_inventory()
                relationships = cast(list[dict[str, Any]], data["relationships"])
                if mutation == "duplicate":
                    relationships.append(copy.deepcopy(relationships[0]))
                elif mutation == "unknown":
                    relationships.append(
                        {
                            "from": "jsonschema",
                            "type": "depends-on",
                            "to": "unknown-component",
                            "enabled": False,
                        }
                    )
                elif mutation == "self":
                    relationships.append(
                        {
                            "from": "jsonschema",
                            "type": "depends-on",
                            "to": "jsonschema",
                            "enabled": True,
                        }
                    )
                elif mutation == "unadmitted":
                    relationships.append(
                        {
                            "from": "jsonschema",
                            "type": "depends-on",
                            "to": "ocrmypdf",
                            "enabled": True,
                        }
                    )
                else:
                    relationships.append(
                        {
                            "from": "attrs",
                            "type": "depends-on",
                            "to": "jsonschema",
                            "enabled": True,
                        }
                    )
                self.assertIn(expected_code, self._codes(data))

    def test_sensitive_free_text_is_rejected_without_echoing_it(self) -> None:
        data = _load_inventory()
        canary = "C:\\synthetic\\private-canary.txt"
        _component(data, "jsonschema")["maintenance"]["note"] = canary

        issues = self._issues(data)

        self.assertIn("DEPENDENCY_SENSITIVE_TEXT_PROHIBITED", {issue.code for issue in issues})
        self.assertNotIn(canary, "\n".join(issue.render() for issue in issues))

    def test_repository_checks_reject_unknown_authority_references(self) -> None:
        cases: tuple[tuple[str, str], ...] = (
            ("decision", "DEPENDENCY_DECISION_REFERENCE_UNKNOWN"),
            ("task", "DEPENDENCY_TASK_REFERENCE_UNKNOWN"),
        )
        for mutation, expected_code in cases:
            with self.subTest(mutation=mutation):
                data = _load_inventory()
                component = _component(data, "jsonschema")
                if mutation == "decision":
                    component["decision_refs"].append("PWS-D999")
                else:
                    component["task_refs"].append("PWS-9999")
                self.assertIn(
                    expected_code,
                    self._codes(data, repository_checks=True),
                )

    def test_repository_checks_require_frozen_families_and_decision_refs(self) -> None:
        data = _load_inventory()
        components = cast(list[dict[str, Any]], data["components"])
        components[:] = [
            component
            for component in components
            if component["component_id"] != "tesseract-language-data"
        ]
        relationships = cast(list[dict[str, Any]], data["relationships"])
        relationships[:] = [
            relationship
            for relationship in relationships
            if relationship["to"] != "tesseract-language-data"
        ]
        self.assertIn(
            "DEPENDENCY_REQUIRED_FAMILY_MISSING",
            self._codes(data, repository_checks=True),
        )

        data = _load_inventory()
        _component(data, "tesseract")["decision_refs"].remove("PWS-D016")
        self.assertIn(
            "DEPENDENCY_REQUIRED_FAMILY_REFERENCE_INVALID",
            self._codes(data, repository_checks=True),
        )

    def test_selected_families_reference_their_actual_implementation_tasks(self) -> None:
        data = _load_inventory()
        expected = {
            "pyside6": "PWS-0111",
            "lxml": "PWS-0105",
            "pikepdf": "PWS-0105",
            "pillow": "PWS-0105",
            "qpdf": "PWS-0105",
            "ocrmypdf": "PWS-0204",
            "tesseract": "PWS-0204",
            "tesseract-language-data": "PWS-0205",
        }

        for component_id, task_id in expected.items():
            with self.subTest(component_id=component_id):
                self.assertIn(task_id, _component(data, component_id)["task_refs"])

    def test_pikepdf_native_and_lock_closure_relationships_fail_closed(self) -> None:
        cases: tuple[tuple[str, str, str], ...] = (
            ("qpdf", "contains", "DEPENDENCY_RELATIONSHIP_PROFILE_INVALID"),
            ("lxml", "depends-on", "DEPENDENCY_LOCK_RELATIONSHIP_MISMATCH"),
            ("packaging", "depends-on", "DEPENDENCY_LOCK_RELATIONSHIP_MISMATCH"),
            ("pillow", "depends-on", "DEPENDENCY_LOCK_RELATIONSHIP_MISMATCH"),
        )
        for target, relation_type, expected_code in cases:
            with self.subTest(target=target):
                data = _load_inventory()
                relationship = next(
                    relationship
                    for relationship in cast(list[dict[str, Any]], data["relationships"])
                    if relationship["from"] == "pikepdf"
                    and relationship["to"] == target
                    and relationship["type"] == relation_type
                )
                relationship["enabled"] = False
                self.assertIn(
                    expected_code,
                    self._codes(data, repository_checks=True),
                )

    def test_lock_versions_hashes_and_dependency_edges_are_reconciled(self) -> None:
        cases: tuple[tuple[str, str], ...] = (
            ("version", "DEPENDENCY_LOCK_VERSION_MISMATCH"),
            ("hash", "DEPENDENCY_LOCK_HASH_MISMATCH"),
            ("edge", "DEPENDENCY_LOCK_RELATIONSHIP_MISMATCH"),
        )
        for mutation, expected_code in cases:
            with self.subTest(mutation=mutation):
                data = _load_inventory()
                if mutation == "version":
                    _component(data, "jsonschema")["version"]["selected"] = "4.26.1"
                elif mutation == "hash":
                    _component(data, "jsonschema")["integrity"]["hashes"][0]["value"] = "0" * 64
                else:
                    _relationship(data, "jsonschema", "attrs")["enabled"] = False
                self.assertIn(
                    expected_code,
                    self._codes(data, repository_checks=True),
                )

    def test_project_declarations_and_tool_pins_are_reconciled(self) -> None:
        data = _load_inventory()
        _component(data, "jsonschema")["relationship"] = "transitive"
        self.assertIn(
            "DEPENDENCY_DECLARATION_MISMATCH",
            self._codes(data, repository_checks=True),
        )

        data = _load_inventory()
        _component(data, "actions-checkout")["integrity"]["revision"] = "0" * 40
        self.assertIn(
            "DEPENDENCY_CONFIG_PIN_MISMATCH",
            self._codes(data, repository_checks=True),
        )

    def test_optional_ghostscript_and_qt_pdf_edges_remain_disabled(self) -> None:
        data = _load_inventory()
        _relationship(data, "ocrmypdf", "ghostscript")["enabled"] = True
        self.assertIn(
            "DEPENDENCY_OPTIONAL_TOOL_POLICY_INVALID",
            self._codes(data, repository_checks=True),
        )

        data = _load_inventory()
        _relationship(data, "pyside6", "qt-pdf")["enabled"] = True
        self.assertIn(
            "DEPENDENCY_PYSIDE_VARIANT_POLICY_INVALID",
            self._codes(data, repository_checks=True),
        )


if __name__ == "__main__":
    unittest.main()

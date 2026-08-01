from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, ClassVar, cast

from tools.validate_manifests import (
    MANIFEST_INVALID_FIXTURE_DIRECTORY,
    MANIFEST_SCHEMA_PATH,
    MAX_MANIFEST_BYTES,
    ManifestIssue,
    load_manifest_schema,
    validate_manifest_data,
    validate_manifest_document,
    validate_manifest_fixture_set,
)

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_DIRECTORY = ROOT / "schemas" / "examples"


def _load_example(name: str) -> dict[str, Any]:
    loaded = json.loads((EXAMPLE_DIRECTORY / name).read_text(encoding="utf-8"))
    return cast(dict[str, Any], loaded)


class CaseManifestValidationTests(unittest.TestCase):
    schema: ClassVar[dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        schema, issues = load_manifest_schema()
        if schema is None:
            raise AssertionError([issue.render() for issue in issues])
        cls.schema = schema

    def _issues(self, data: dict[str, Any]) -> list[ManifestIssue]:
        return validate_manifest_data(data, self.schema, document="synthetic-manifest.json")

    def _codes(self, data: dict[str, Any]) -> set[str]:
        return {issue.code for issue in self._issues(data)}

    def test_checked_in_schema_examples_and_negative_fixtures_pass(self) -> None:
        summary, issues = validate_manifest_fixture_set()

        self.assertEqual([], issues)
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(2, summary.positive_count)
        self.assertGreaterEqual(summary.negative_count, 10)

    def test_each_negative_fixture_is_rejected_for_its_intended_reason(self) -> None:
        expected = {
            "duplicate-key.json": "MANIFEST_JSON_DUPLICATE_KEY",
            "duplicate-source-id.json": "MANIFEST_SOURCE_ID_DUPLICATE",
            "executable-operation-field.json": "MANIFEST_SCHEMA_VALIDATION",
            "future-version.json": "MANIFEST_VERSION_UNSUPPORTED",
            "invalid-hash.json": "MANIFEST_SCHEMA_VALIDATION",
            "legacy-version.json": "MANIFEST_VERSION_UNSUPPORTED",
            "malformed-normalized-box.json": "MANIFEST_NORMALIZED_BOX_INVALID",
            "out-of-range-source-page.json": "MANIFEST_SOURCE_PAGE_OUT_OF_RANGE",
            "unknown-page-reference.json": "MANIFEST_PAGE_REFERENCE_UNKNOWN",
            "unknown-source-reference.json": "MANIFEST_SOURCE_REFERENCE_UNKNOWN",
            "unsafe-storage-name.json": "MANIFEST_SCHEMA_VALIDATION",
        }
        fixture_directory = ROOT / MANIFEST_INVALID_FIXTURE_DIRECTORY
        self.assertEqual(set(expected), {path.name for path in fixture_directory.glob("*.json")})
        for filename, expected_code in expected.items():
            with self.subTest(filename=filename):
                _, issues = validate_manifest_document(
                    fixture_directory / filename,
                    schema=self.schema,
                )
                self.assertIn(expected_code, {issue.code for issue in issues})

    def test_version_dispatch_rejects_missing_boolean_string_old_and_future(self) -> None:
        cases: tuple[tuple[object, str], ...] = (
            (None, "MANIFEST_VERSION_INVALID"),
            (True, "MANIFEST_VERSION_INVALID"),
            ("1", "MANIFEST_VERSION_INVALID"),
            (0, "MANIFEST_VERSION_UNSUPPORTED"),
            (2, "MANIFEST_VERSION_UNSUPPORTED"),
        )
        for value, expected_code in cases:
            with self.subTest(value=value):
                data = _load_example("case-manifest-v1-minimal.json")
                if value is None:
                    del data["schema_version"]
                else:
                    data["schema_version"] = value
                self.assertEqual({expected_code}, self._codes(data))

    def test_strict_json_loader_rejects_ambiguous_or_nonstandard_input(self) -> None:
        cases = {
            "duplicate.json": (
                '{"schema_version":1,"schema_version":1}',
                "MANIFEST_JSON_DUPLICATE_KEY",
            ),
            "nan.json": ('{"schema_version":1,"value":NaN}', "MANIFEST_JSON_NONSTANDARD_CONSTANT"),
            "infinity.json": (
                '{"schema_version":1,"value":Infinity}',
                "MANIFEST_JSON_NONSTANDARD_CONSTANT",
            ),
            "array.json": ("[]", "MANIFEST_JSON_ROOT_TYPE"),
            "malformed.json": ("{", "MANIFEST_JSON_INVALID"),
        }
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            for filename, (content, expected_code) in cases.items():
                with self.subTest(filename=filename):
                    path = temporary_root / filename
                    path.write_text(content, encoding="utf-8")
                    _, issues = validate_manifest_document(path, schema=self.schema)
                    self.assertIn(expected_code, {issue.code for issue in issues})

    def test_loader_limits_and_diagnostics_do_not_disclose_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            invalid_utf8 = temporary_root / "private-case.json"
            invalid_utf8.write_bytes(b'{"schema_version":1,"value":"\xff"}')
            _, encoding_issues = validate_manifest_document(invalid_utf8, schema=self.schema)
            oversized = temporary_root / "oversized.json"
            oversized.write_bytes(b"{" + (b" " * MAX_MANIFEST_BYTES) + b"}")
            _, size_issues = validate_manifest_document(oversized, schema=self.schema)
            rendered = "\n".join(issue.render() for issue in [*encoding_issues, *size_issues])

        self.assertIn("MANIFEST_TEXT_ENCODING_INVALID", rendered)
        self.assertIn("MANIFEST_INPUT_TOO_LARGE", rendered)
        self.assertNotIn(str(temporary_root), rendered)

    def test_schema_rejects_external_or_unresolved_references(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            (temporary_root / "schemas").mkdir()
            schema_path = temporary_root / MANIFEST_SCHEMA_PATH
            for reference, expected_code in (
                ("https://invalid.example/schema.json", "MANIFEST_SCHEMA_REFERENCE_UNSAFE"),
                ("#/$defs/does-not-exist", "MANIFEST_SCHEMA_REFERENCE_INVALID"),
            ):
                with self.subTest(reference=reference):
                    schema = copy.deepcopy(self.schema)
                    cast(dict[str, Any], schema["properties"])["schema_version"] = {
                        "$ref": reference
                    }
                    schema_path.write_text(json.dumps(schema), encoding="utf-8")
                    loaded, issues = load_manifest_schema(temporary_root)
                    self.assertIsNone(loaded)
                    self.assertIn(expected_code, {issue.code for issue in issues})

    def test_validation_does_not_mutate_input(self) -> None:
        data = _load_example("case-manifest-v1-representative.json")
        before = copy.deepcopy(data)

        issues = self._issues(data)

        self.assertEqual([], issues)
        self.assertEqual(before, data)

    def test_generated_normalized_box_boundaries_are_exact(self) -> None:
        cases = (
            ((0, 0, 1, 1), True),
            ((0, 0, 1_000_000, 1_000_000), True),
            ((750_000, 0, 250_000, 1_000_000), True),
            ((750_001, 0, 250_000, 1_000_000), False),
            ((0, 900_000, 1_000_000, 100_001), False),
        )
        for (x, y, width, height), valid in cases:
            with self.subTest(box=(x, y, width, height)):
                data = _load_example("case-manifest-v1-representative.json")
                operations = cast(list[dict[str, Any]], data["page_operations"])
                payload = cast(dict[str, Any], operations[1]["payload"])
                box = cast(dict[str, Any], payload["box"])
                box.update(x=x, y=y, width=width, height=height)
                codes = self._codes(data)
                self.assertEqual(valid, "MANIFEST_NORMALIZED_BOX_INVALID" not in codes)

    def test_generated_source_page_reference_boundaries_are_exact(self) -> None:
        for source_page_index, valid in ((0, True), (1, False), (2, False)):
            with self.subTest(source_page_index=source_page_index):
                data = _load_example("case-manifest-v1-representative.json")
                pages = cast(list[dict[str, Any]], data["pages"])
                pages[2]["source_page_index"] = source_page_index
                codes = self._codes(data)
                self.assertEqual(valid, "MANIFEST_SOURCE_PAGE_OUT_OF_RANGE" not in codes)

    def test_source_page_pairs_must_be_unique_and_complete(self) -> None:
        duplicate = _load_example("case-manifest-v1-representative.json")
        duplicate_pages = cast(list[dict[str, Any]], duplicate["pages"])
        duplicate_pages[1]["source_page_index"] = 0
        incomplete = _load_example("case-manifest-v1-representative.json")
        cast(list[dict[str, Any]], incomplete["pages"]).pop(1)

        self.assertIn("MANIFEST_SOURCE_PAGE_DUPLICATE", self._codes(duplicate))
        self.assertIn("MANIFEST_SOURCE_PAGE_MISSING", self._codes(incomplete))

    def test_storage_names_are_opaque_unique_and_bound_to_ids(self) -> None:
        data = _load_example("case-manifest-v1-representative.json")
        sources = cast(list[dict[str, Any]], data["sources"])
        sources[0]["storage_name"] = "20000000-0000-4000-8000-000000000099.bin"

        self.assertIn("MANIFEST_STORAGE_NAME_ID_MISMATCH", self._codes(data))

    def test_media_inspection_states_are_conservative_and_type_appropriate(self) -> None:
        pdf_unknown = _load_example("case-manifest-v1-representative.json")
        pdf_source = cast(list[dict[str, Any]], pdf_unknown["sources"])[0]
        pdf_source["signature_state"] = "unknown"
        pdf_source["encryption_state"] = "unknown"
        image_unknown = _load_example("case-manifest-v1-representative.json")
        image_source = cast(list[dict[str, Any]], image_unknown["sources"])[1]
        image_source["signature_state"] = "unknown"

        self.assertEqual([], self._issues(pdf_unknown))
        self.assertIn("MANIFEST_MEDIA_INSPECTION_STATE_INVALID", self._codes(image_unknown))

    def test_operation_order_and_page_sequence_must_replay_exactly(self) -> None:
        wrong_order = _load_example("case-manifest-v1-representative.json")
        wrong_operations = cast(list[dict[str, Any]], wrong_order["page_operations"])
        wrong_operations[1]["sequence"] = 9
        wrong_final = _load_example("case-manifest-v1-representative.json")
        wrong_final["page_sequence"] = list(reversed(cast(list[str], wrong_final["page_sequence"])))

        self.assertIn("MANIFEST_OPERATION_SEQUENCE_INVALID", self._codes(wrong_order))
        self.assertIn("MANIFEST_SEQUENCE_REPLAY_MISMATCH", self._codes(wrong_final))

    def test_duplicate_and_cross_namespace_ids_are_rejected(self) -> None:
        duplicate = _load_example("case-manifest-v1-representative.json")
        sources = cast(list[dict[str, Any]], duplicate["sources"])
        sources.append(copy.deepcopy(sources[0]))
        collision = _load_example("case-manifest-v1-representative.json")
        overlays = cast(list[dict[str, Any]], collision["overlays"])
        overlays[0]["overlay_id"] = cast(str, collision["case_id"])

        self.assertIn("MANIFEST_SOURCE_ID_DUPLICATE", self._codes(duplicate))
        self.assertIn("MANIFEST_ID_NAMESPACE_COLLISION", self._codes(collision))

    def test_page_asset_confirmation_and_export_references_resolve(self) -> None:
        mutations = (
            ("overlay-page", "MANIFEST_PAGE_REFERENCE_UNKNOWN"),
            ("overlay-asset", "MANIFEST_ASSET_REFERENCE_UNKNOWN"),
            ("assignment-page", "MANIFEST_PAGE_REFERENCE_UNKNOWN"),
            ("confirmation-scope", "MANIFEST_CONFIRMATION_SCOPE_UNKNOWN"),
            ("export-scope", "MANIFEST_CONFIRMATION_SCOPE_UNKNOWN"),
        )
        unknown_page = "30000000-0000-4000-8000-000000000099"
        unknown_asset = "40000000-0000-4000-8000-000000000099"
        unknown_source = "20000000-0000-4000-8000-000000000099"
        for mutation, expected_code in mutations:
            with self.subTest(mutation=mutation):
                data = _load_example("case-manifest-v1-representative.json")
                overlays = cast(list[dict[str, Any]], data["overlays"])
                assignments = cast(list[dict[str, Any]], data["assignments"])
                confirmations = cast(list[dict[str, Any]], data["confirmations"])
                if mutation == "overlay-page":
                    overlays[0]["page_id"] = unknown_page
                elif mutation == "overlay-asset":
                    cast(dict[str, Any], overlays[1]["payload"])["asset_id"] = unknown_asset
                elif mutation == "assignment-page":
                    assignments[0]["page_ids"] = [unknown_page]
                elif mutation == "confirmation-scope":
                    cast(dict[str, Any], confirmations[0]["scope"])["source_id"] = unknown_source
                else:
                    cast(dict[str, Any], data["export_history"])["entries"] = []
                self.assertIn(expected_code, self._codes(data))

    def test_confirmed_assignment_requires_matching_user_confirmation(self) -> None:
        data = _load_example("case-manifest-v1-representative.json")
        assignments = cast(list[dict[str, Any]], data["assignments"])
        assignments[0]["confirmation_id"] = "a0000000-0000-4000-8000-000000000003"

        self.assertIn("MANIFEST_ASSIGNMENT_CONFIRMATION_MISMATCH", self._codes(data))

    def test_output_names_reject_reserved_and_windows_colliding_values(self) -> None:
        reserved = _load_example("case-manifest-v1-representative.json")
        cast(dict[str, Any], reserved["constraints"])["requested_output_names"] = ["CON.txt"]
        colliding = _load_example("case-manifest-v1-representative.json")
        cast(dict[str, Any], colliding["constraints"])["requested_output_names"] = [
            "Résumé.pdf",
            "re\u0301sume\u0301.pdf",
        ]

        self.assertIn("MANIFEST_OUTPUT_NAME_RESERVED", self._codes(reserved))
        collision_codes = self._codes(colliding)
        self.assertIn("MANIFEST_OUTPUT_NAME_NONCANONICAL", collision_codes)
        self.assertIn("MANIFEST_OUTPUT_NAME_COLLISION", collision_codes)

    def test_display_names_and_overlay_text_remain_inert_data(self) -> None:
        data = _load_example("case-manifest-v1-representative.json")
        sources = cast(list[dict[str, Any]], data["sources"])
        overlays = cast(list[dict[str, Any]], data["overlays"])
        sources[0]["display_name"] = "SYNTHETIC $(not-run) & ^ %! résumé.pdf"
        cast(dict[str, Any], overlays[0]["payload"])["value"] = "$(synthetic); do-not-run"

        self.assertEqual([], self._issues(data))
        cast(dict[str, Any], overlays[0]["payload"])["script"] = "do-not-run"
        self.assertEqual({"MANIFEST_SCHEMA_VALIDATION"}, self._codes(data))

    def test_case_and_event_timestamps_must_be_ordered(self) -> None:
        revision = _load_example("case-manifest-v1-representative.json")
        revision["updated_at"] = "2026-08-01T09:59:59Z"
        event = _load_example("case-manifest-v1-representative.json")
        source = cast(list[dict[str, Any]], event["sources"])[0]
        source["imported_at"] = "2026-08-01T10:31:00Z"

        self.assertIn("MANIFEST_TIMESTAMP_ORDER_INVALID", self._codes(revision))
        self.assertIn("MANIFEST_TIMESTAMP_ORDER_INVALID", self._codes(event))

    def test_diagnostics_are_deterministic_and_do_not_echo_instance_values(self) -> None:
        canary = "CANARY-SENSITIVE-DISPLAY-NAME"
        data = _load_example("case-manifest-v1-representative.json")
        source = cast(list[dict[str, Any]], data["sources"])[0]
        source["display_name"] = f"{canary}/invalid"
        first = [issue.render() for issue in self._issues(data)]
        second = [issue.render() for issue in self._issues(data)]

        self.assertEqual(first, second)
        self.assertTrue(first)
        self.assertNotIn(canary, "\n".join(first))


class ManifestSchemaRegistryIntegrationTests(unittest.TestCase):
    def _copy_fixture_root(self, target: Path) -> None:
        (target / "schemas" / "examples").mkdir(parents=True)
        (target / MANIFEST_INVALID_FIXTURE_DIRECTORY).mkdir(parents=True)
        shutil.copy2(ROOT / MANIFEST_SCHEMA_PATH, target / MANIFEST_SCHEMA_PATH)
        for source in EXAMPLE_DIRECTORY.glob("case-manifest-v1-*.json"):
            shutil.copy2(source, target / "schemas" / "examples" / source.name)
        for source in (ROOT / MANIFEST_INVALID_FIXTURE_DIRECTORY).glob("*.json"):
            shutil.copy2(source, target / MANIFEST_INVALID_FIXTURE_DIRECTORY / source.name)

    def test_aggregate_rejects_a_corrupted_positive_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_fixture_root(root)
            positive = root / "schemas" / "examples" / "case-manifest-v1-minimal.json"
            data = cast(dict[str, Any], json.loads(positive.read_text(encoding="utf-8")))
            data["schema_version"] = 2
            positive.write_text(json.dumps(data), encoding="utf-8")

            summary, issues = validate_manifest_fixture_set(root)

        self.assertIsNone(summary)
        self.assertIn("MANIFEST_POSITIVE_FIXTURE_INVALID", {issue.code for issue in issues})

    def test_aggregate_rejects_an_accidentally_valid_negative_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_fixture_root(root)
            invalid = root / MANIFEST_INVALID_FIXTURE_DIRECTORY / "future-version.json"
            shutil.copy2(
                root / "schemas" / "examples" / "case-manifest-v1-minimal.json",
                invalid,
            )

            summary, issues = validate_manifest_fixture_set(root)

        self.assertIsNone(summary)
        self.assertIn("MANIFEST_NEGATIVE_FIXTURE_ACCEPTED", {issue.code for issue in issues})


if __name__ == "__main__":
    unittest.main()

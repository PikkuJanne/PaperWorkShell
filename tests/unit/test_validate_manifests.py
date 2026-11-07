from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, ClassVar, cast
from unittest.mock import patch

from tools.validate_manifests import (
    CURRENT_MANIFEST_EXAMPLE_PATTERN,
    CURRENT_MANIFEST_INVALID_FIXTURE_DIRECTORY,
    CURRENT_MANIFEST_SCHEMA_PATH,
    MANIFEST_INVALID_FIXTURE_DIRECTORY,
    MANIFEST_SCHEMA_PATH,
    MAX_MANIFEST_BYTES,
    V2_MANIFEST_EXAMPLE_PATTERN,
    V2_MANIFEST_INVALID_FIXTURE_DIRECTORY,
    V2_MANIFEST_SCHEMA_PATH,
    V3_MANIFEST_EXAMPLE_PATTERN,
    V3_MANIFEST_INVALID_FIXTURE_DIRECTORY,
    V3_MANIFEST_SCHEMA_PATH,
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


def _uuid(namespace: int, value: int) -> str:
    return f"{namespace:08x}-0000-4000-8000-{value:012x}"


def _generated_reference_graph(source_count: int, pages_per_source: int) -> dict[str, Any]:
    data = _load_example("case-manifest-v1-minimal.json")
    sources = cast(list[dict[str, Any]], data["sources"])
    pages = cast(list[dict[str, Any]], data["pages"])
    sequence: list[str] = []
    for source_offset in range(source_count):
        source_id = _uuid(0x20000000, source_offset + 100)
        sources.append(
            {
                "source_id": source_id,
                "display_name": f"Synthetic source {source_offset + 1}.pdf",
                "storage_name": f"{source_id}.bin",
                "detected_media_type": "application/pdf",
                "byte_size": 64 + source_offset,
                "page_count": pages_per_source,
                "sha256": f"{source_offset + 1:064x}",
                "signature_state": "unknown",
                "encryption_state": "unknown",
                "import_state": "complete",
                "imported_at": "2026-08-01T10:00:00Z",
            }
        )
        for source_page_index in range(pages_per_source):
            page_id = _uuid(
                0x30000000,
                (source_offset * pages_per_source) + source_page_index + 100,
            )
            pages.append(
                {
                    "page_id": page_id,
                    "source_id": source_id,
                    "source_page_index": source_page_index,
                }
            )
            sequence.append(page_id)
    data["page_sequence_base"] = list(sequence)
    data["page_sequence"] = list(sequence)
    return data


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
        self.assertEqual(8, summary.positive_count)
        self.assertGreaterEqual(summary.negative_count, 10)

    def test_each_negative_fixture_is_rejected_for_its_intended_reason(self) -> None:
        expected = {
            "duplicate-key.json": "MANIFEST_JSON_DUPLICATE_KEY",
            "duplicate-source-id.json": "MANIFEST_SOURCE_ID_DUPLICATE",
            "executable-operation-field.json": "MANIFEST_SCHEMA_VALIDATION",
            "future-version.json": "MANIFEST_SCHEMA_IDENTITY_MISMATCH",
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
            (5, "MANIFEST_VERSION_UNSUPPORTED"),
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

    def test_schema_accepted_but_unsupported_timestamps_fail_without_traceback(self) -> None:
        for timestamp in ("0000-01-01T00:00:00Z", "2016-12-31T23:59:60Z"):
            with self.subTest(timestamp=timestamp):
                data = _load_example("case-manifest-v1-minimal.json")
                data["created_at"] = timestamp
                data["updated_at"] = timestamp

                self.assertIn("MANIFEST_TIMESTAMP_INVALID", self._codes(data))

        trailing_newline = _load_example("case-manifest-v1-minimal.json")
        trailing_newline["created_at"] = "2026-08-01T10:00:00Z\n"
        self.assertEqual({"MANIFEST_SCHEMA_VALIDATION"}, self._codes(trailing_newline))

        sub_microsecond = _load_example("case-manifest-v1-minimal.json")
        sub_microsecond["created_at"] = "2026-08-01T10:00:00.1234567Z"
        sub_microsecond["updated_at"] = "2026-08-01T10:00:00.1234567Z"
        self.assertEqual({"MANIFEST_SCHEMA_VALIDATION"}, self._codes(sub_microsecond))

    def test_loader_limits_and_diagnostics_do_not_disclose_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            filename_canary = "CANARY-SENSITIVE-CASE-NAME.json"
            invalid_utf8 = temporary_root / filename_canary
            invalid_utf8.write_bytes(b'{"schema_version":1,"value":"\xff"}')
            _, encoding_issues = validate_manifest_document(invalid_utf8, schema=self.schema)
            oversized = temporary_root / "oversized.json"
            oversized.write_bytes(b"{" + (b" " * MAX_MANIFEST_BYTES) + b"}")
            _, size_issues = validate_manifest_document(oversized, schema=self.schema)
            case_root = temporary_root / "case-root"
            case_root.mkdir()
            traversal_canary = "CANARY-TRAVERSAL-CLIENT-NAME.json"
            traversal_path = case_root / ".." / traversal_canary
            traversal_path.write_bytes(b'{"schema_version":1,"value":"\xff"}')
            _, traversal_issues = validate_manifest_document(
                traversal_path,
                root=case_root,
                schema=self.schema,
            )
            rendered = "\n".join(
                issue.render() for issue in [*encoding_issues, *size_issues, *traversal_issues]
            )

        self.assertIn("MANIFEST_TEXT_ENCODING_INVALID", rendered)
        self.assertIn("MANIFEST_INPUT_TOO_LARGE", rendered)
        self.assertNotIn(str(temporary_root), rendered)
        self.assertNotIn(filename_canary, rendered)
        self.assertNotIn(traversal_canary, rendered)

    def test_schema_rejects_external_or_unresolved_references(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            (temporary_root / "schemas").mkdir()
            schema_path = temporary_root / MANIFEST_SCHEMA_PATH
            for reference, expected_code in (
                ("https://invalid.example/schema.json", "MANIFEST_SCHEMA_REFERENCE_UNSAFE"),
                ("#/$defs/does-not-exist", "MANIFEST_SCHEMA_REFERENCE_INVALID"),
                ("#/$defs/~2invalid-escape", "MANIFEST_SCHEMA_REFERENCE_INVALID"),
                (f"#/allOf/{'9' * 5_000}", "MANIFEST_SCHEMA_REFERENCE_INVALID"),
            ):
                with self.subTest(reference=reference):
                    schema = copy.deepcopy(self.schema)
                    if reference.startswith("#/allOf/"):
                        schema["allOf"] = [{}]
                    cast(dict[str, Any], schema["properties"])["schema_version"] = {
                        "$ref": reference
                    }
                    schema_path.write_text(json.dumps(schema), encoding="utf-8")
                    loaded, issues = load_manifest_schema(temporary_root)
                    self.assertIsNone(loaded)
                    self.assertIn(expected_code, {issue.code for issue in issues})

    def test_schema_rejects_recursive_and_multi_resource_reference_features(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            (temporary_root / "schemas").mkdir()
            schema_path = temporary_root / MANIFEST_SCHEMA_PATH
            cases: list[tuple[str, dict[str, Any], str]] = []

            root_cycle = copy.deepcopy(self.schema)
            root_cycle["allOf"] = [{"$ref": "#"}]
            cases.append(("root-cycle", root_cycle, "MANIFEST_SCHEMA_REFERENCE_CYCLE"))

            definition_cycle = copy.deepcopy(self.schema)
            cast(dict[str, Any], definition_cycle["$defs"])["loop"] = {"$ref": "#/$defs/loop"}
            cases.append(("definition-cycle", definition_cycle, "MANIFEST_SCHEMA_REFERENCE_CYCLE"))

            nested_resource = copy.deepcopy(self.schema)
            case_id_schema = cast(
                dict[str, Any],
                cast(dict[str, Any], nested_resource["properties"])["case_id"],
            )
            case_id_schema["$id"] = "nested-resource"
            cases.append(
                (
                    "nested-resource",
                    nested_resource,
                    "MANIFEST_SCHEMA_RESOURCE_SCOPE_UNSAFE",
                )
            )

            dynamic_reference = copy.deepcopy(self.schema)
            cast(dict[str, Any], dynamic_reference["properties"])["case_id"] = {
                "$dynamicRef": "#/$defs/uuidV4"
            }
            cases.append(
                (
                    "dynamic-reference",
                    dynamic_reference,
                    "MANIFEST_SCHEMA_REFERENCE_FEATURE_UNSAFE",
                )
            )

            for name, schema, expected_code in cases:
                with self.subTest(name=name):
                    schema_path.write_text(json.dumps(schema), encoding="utf-8")
                    loaded, issues = load_manifest_schema(temporary_root)
                    self.assertIsNone(loaded)
                    self.assertIn(expected_code, {issue.code for issue in issues})

        direct_cycle = {"allOf": [{"$ref": "#"}]}
        direct_issues = validate_manifest_data(
            _load_example("case-manifest-v1-minimal.json"),
            direct_cycle,
            document="synthetic-manifest.json",
        )
        self.assertEqual(
            {"MANIFEST_SCHEMA_IDENTITY_MISMATCH"},
            {issue.code for issue in direct_issues},
        )

    def test_validation_does_not_mutate_input(self) -> None:
        data = _load_example("case-manifest-v1-representative.json")
        before = copy.deepcopy(data)

        issues = self._issues(data)

        self.assertEqual([], issues)
        self.assertEqual(before, data)

    def test_substitute_schema_cannot_escape_the_manifest_contract_or_raise(self) -> None:
        incomplete = {"schema_version": 1}
        issues = validate_manifest_data(
            incomplete,
            {},
            document="synthetic-manifest.json",
        )
        self.assertEqual(
            {"MANIFEST_SCHEMA_IDENTITY_MISMATCH"},
            {issue.code for issue in issues},
        )

        structurally_complete = _load_example("case-manifest-v1-representative.json")
        structurally_complete["script"] = "do-not-run"
        complete_sources = cast(list[dict[str, Any]], structurally_complete["sources"])
        complete_sources[0]["external_path"] = "C:/private/source.pdf"
        complete_operations = cast(list[dict[str, Any]], structurally_complete["page_operations"])
        cast(dict[str, Any], complete_operations[0]["payload"])["script"] = "do-not-run"
        complete_issues = validate_manifest_data(
            structurally_complete,
            {},
            document="synthetic-manifest.json",
        )
        self.assertEqual(
            {"MANIFEST_SCHEMA_IDENTITY_MISMATCH"},
            {issue.code for issue in complete_issues},
        )

        with patch(
            "tools.validate_manifests.json.dumps",
            side_effect=RecursionError("synthetic encoder depth limit"),
        ):
            deeply_nested_issues = validate_manifest_data(
                _load_example("case-manifest-v1-minimal.json"),
                {},
                document="synthetic-manifest.json",
            )
        self.assertEqual(
            {"MANIFEST_SCHEMA_IDENTITY_MISMATCH"},
            {issue.code for issue in deeply_nested_issues},
        )

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            path.write_text(json.dumps(structurally_complete), encoding="utf-8")
            loaded, document_issues = validate_manifest_document(path, schema={})

        self.assertIsNone(loaded)
        self.assertEqual(
            {"MANIFEST_SCHEMA_IDENTITY_MISMATCH"},
            {issue.code for issue in document_issues},
        )

    def test_loaded_schema_identity_is_bound_to_the_reviewed_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            (temporary_root / "schemas").mkdir()
            schema_path = temporary_root / MANIFEST_SCHEMA_PATH
            modified_schema = copy.deepcopy(self.schema)
            modified_schema["description"] = "Synthetic weakened substitute"
            schema_path.write_text(json.dumps(modified_schema), encoding="utf-8")

            loaded, issues = load_manifest_schema(temporary_root)

        self.assertIsNone(loaded)
        self.assertEqual(
            {"MANIFEST_SCHEMA_IDENTITY_MISMATCH"},
            {issue.code for issue in issues},
        )

    def test_generated_normalized_box_boundaries_are_exact(self) -> None:
        coordinates = (0, 1, 125_000, 500_000, 875_000, 999_999)
        cases: list[tuple[int, int, int, int, bool]] = []
        for x in coordinates:
            for y in coordinates:
                maximum_width = 1_000_000 - x
                maximum_height = 1_000_000 - y
                cases.append((x, y, maximum_width, maximum_height, True))
                if x > 0:
                    cases.append((x, y, maximum_width + 1, maximum_height, False))
                if y > 0:
                    cases.append((x, y, maximum_width, maximum_height + 1, False))

        for x, y, width, height, valid in cases:
            with self.subTest(box=(x, y, width, height), valid=valid):
                data = _load_example("case-manifest-v1-representative.json")
                operations = cast(list[dict[str, Any]], data["page_operations"])
                payload = cast(dict[str, Any], operations[1]["payload"])
                box = cast(dict[str, Any], payload["box"])
                box.update(x=x, y=y, width=width, height=height)
                codes = self._codes(data)
                self.assertEqual(valid, "MANIFEST_NORMALIZED_BOX_INVALID" not in codes)

    def test_generated_source_page_reference_boundaries_are_exact(self) -> None:
        for source_count in (1, 2, 4, 8):
            for pages_per_source in (1, 2, 5):
                with self.subTest(
                    source_count=source_count,
                    pages_per_source=pages_per_source,
                    mutation="valid",
                ):
                    data = _generated_reference_graph(source_count, pages_per_source)
                    self.assertEqual([], self._issues(data))

                out_of_range = _generated_reference_graph(source_count, pages_per_source)
                out_of_range_pages = cast(list[dict[str, Any]], out_of_range["pages"])
                out_of_range_pages[-1]["source_page_index"] = pages_per_source
                with self.subTest(
                    source_count=source_count,
                    pages_per_source=pages_per_source,
                    mutation="out-of-range",
                ):
                    self.assertIn(
                        "MANIFEST_SOURCE_PAGE_OUT_OF_RANGE",
                        self._codes(out_of_range),
                    )

                dangling = _generated_reference_graph(source_count, pages_per_source)
                dangling_pages = cast(list[dict[str, Any]], dangling["pages"])
                dangling_pages[-1]["source_id"] = _uuid(0x20000000, 99_999)
                with self.subTest(
                    source_count=source_count,
                    pages_per_source=pages_per_source,
                    mutation="dangling-source",
                ):
                    self.assertIn(
                        "MANIFEST_SOURCE_REFERENCE_UNKNOWN",
                        self._codes(dangling),
                    )

                incomplete = _generated_reference_graph(source_count, pages_per_source)
                cast(list[dict[str, Any]], incomplete["pages"]).pop()
                with self.subTest(
                    source_count=source_count,
                    pages_per_source=pages_per_source,
                    mutation="missing-page",
                ):
                    self.assertIn("MANIFEST_SOURCE_PAGE_MISSING", self._codes(incomplete))

        duplicate_pair = _generated_reference_graph(2, 2)
        duplicate_pair_pages = cast(list[dict[str, Any]], duplicate_pair["pages"])
        duplicate_pair_pages[1]["source_page_index"] = 0
        duplicate_id = _generated_reference_graph(2, 2)
        duplicate_id_pages = cast(list[dict[str, Any]], duplicate_id["pages"])
        duplicate_id_pages[1]["page_id"] = duplicate_id_pages[0]["page_id"]

        self.assertIn("MANIFEST_SOURCE_PAGE_DUPLICATE", self._codes(duplicate_pair))
        self.assertIn("MANIFEST_PAGE_ID_DUPLICATE", self._codes(duplicate_id))

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

    def test_sequence_change_reason_must_match_membership_transition(self) -> None:
        reorder_removal = _load_example("case-manifest-v1-representative.json")
        reorder_removal_operations = cast(list[dict[str, Any]], reorder_removal["page_operations"])
        cast(dict[str, Any], reorder_removal_operations[2]["payload"])["reason"] = "reorder"
        remove_reorder = _load_example("case-manifest-v1-representative.json")
        remove_reorder_operations = cast(list[dict[str, Any]], remove_reorder["page_operations"])
        cast(dict[str, Any], remove_reorder_operations[3]["payload"])["reason"] = "remove"

        self.assertIn("MANIFEST_SEQUENCE_REASON_INVALID", self._codes(reorder_removal))
        self.assertIn("MANIFEST_SEQUENCE_REASON_INVALID", self._codes(remove_reorder))

        restoration = _load_example("case-manifest-v1-representative.json")
        restoration_operations = cast(list[dict[str, Any]], restoration["page_operations"])
        restoration_payload = cast(dict[str, Any], restoration_operations[3]["payload"])
        restoration_payload["reason"] = "restore"
        restoration_payload["after_page_ids"] = list(restoration["page_sequence_base"])
        restoration["page_sequence"] = list(restoration["page_sequence_base"])
        self.assertEqual([], self._issues(restoration))

        mislabeled_restore = copy.deepcopy(restoration)
        mislabeled_restore_operations = cast(
            list[dict[str, Any]], mislabeled_restore["page_operations"]
        )
        cast(dict[str, Any], mislabeled_restore_operations[3]["payload"])["reason"] = "import"
        self.assertIn(
            "MANIFEST_SEQUENCE_REASON_INVALID",
            self._codes(mislabeled_restore),
        )

        imported = _generated_reference_graph(1, 2)
        imported_pages = cast(list[dict[str, Any]], imported["pages"])
        first_page_id = imported_pages[0]["page_id"]
        second_page_id = imported_pages[1]["page_id"]
        imported["page_sequence_base"] = [first_page_id]
        imported["page_sequence"] = [first_page_id, second_page_id]
        imported["page_operations"] = [
            {
                "operation_id": _uuid(0x50000000, 100),
                "operation_version": 1,
                "sequence": 1,
                "payload": {
                    "kind": "change_sequence",
                    "reason": "import",
                    "before_page_ids": [first_page_id],
                    "after_page_ids": [first_page_id, second_page_id],
                },
            }
        ]
        self.assertEqual([], self._issues(imported))

        page_local_payloads: tuple[dict[str, Any], ...] = (
            {
                "kind": "rotate",
                "page_id": second_page_id,
                "quarter_turns_clockwise": 1,
            },
            {
                "kind": "crop",
                "page_id": second_page_id,
                "box": {
                    "coordinate_space": "normalized_page_millionths_v1",
                    "x": 0,
                    "y": 0,
                    "width": 1_000_000,
                    "height": 1_000_000,
                },
            },
            {
                "kind": "set_group",
                "group_id": _uuid(0x90000000, 100),
                "page_ids": [second_page_id],
            },
        )
        for offset, operation_payload in enumerate(page_local_payloads, start=1):
            with self.subTest(
                operation_kind=operation_payload["kind"],
                timing="before-import",
            ):
                before_import = copy.deepcopy(imported)
                before_import_operations = cast(
                    list[dict[str, Any]], before_import["page_operations"]
                )
                before_import_operations[0]["sequence"] = 2
                before_import_operations.insert(
                    0,
                    {
                        "operation_id": _uuid(0x50000000, 200 + offset),
                        "operation_version": 1,
                        "sequence": 1,
                        "payload": operation_payload,
                    },
                )
                self.assertIn(
                    "MANIFEST_OPERATION_PAGE_NOT_INTRODUCED",
                    self._codes(before_import),
                )

            with self.subTest(
                operation_kind=operation_payload["kind"],
                timing="after-import",
            ):
                after_import = copy.deepcopy(imported)
                cast(list[dict[str, Any]], after_import["page_operations"]).append(
                    {
                        "operation_id": _uuid(0x50000000, 300 + offset),
                        "operation_version": 1,
                        "sequence": 2,
                        "payload": operation_payload,
                    }
                )
                self.assertEqual([], self._issues(after_import))

        never_introduced = _generated_reference_graph(1, 2)
        never_introduced["page_sequence_base"] = [first_page_id]
        never_introduced["page_sequence"] = [first_page_id]
        self.assertIn(
            "MANIFEST_PAGE_INTRODUCTION_INCOMPLETE",
            self._codes(never_introduced),
        )

        for reason in ("reorder", "split", "combine"):
            with self.subTest(reason=reason):
                same_membership = _load_example("case-manifest-v1-representative.json")
                same_membership_operations = cast(
                    list[dict[str, Any]], same_membership["page_operations"]
                )
                cast(dict[str, Any], same_membership_operations[3]["payload"])["reason"] = reason
                self.assertEqual([], self._issues(same_membership))

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

    def test_output_names_reject_format_controls_and_count_amplification(self) -> None:
        disguised = _load_example("case-manifest-v1-representative.json")
        cast(dict[str, Any], disguised["constraints"])["requested_output_names"] = [
            "application\u202efdp.exe"
        ]
        c1_control = _load_example("case-manifest-v1-representative.json")
        cast(dict[str, Any], c1_control["constraints"])["requested_output_names"] = [
            "application\u0085form.pdf"
        ]
        surrogate = _load_example("case-manifest-v1-representative.json")
        cast(dict[str, Any], surrogate["constraints"])["requested_output_names"] = [
            "application\ud800form.pdf"
        ]
        exact_utf16_limit = _load_example("case-manifest-v1-representative.json")
        cast(dict[str, Any], exact_utf16_limit["constraints"])["requested_output_names"] = [
            ("\U0001f4c4" * 88) + ".pdf"
        ]
        over_utf16_limit = _load_example("case-manifest-v1-representative.json")
        cast(dict[str, Any], over_utf16_limit["constraints"])["requested_output_names"] = [
            ("\U0001f4c4" * 89) + ".pdf"
        ]
        too_many_for_constraint = _load_example("case-manifest-v1-representative.json")
        cast(dict[str, Any], too_many_for_constraint["constraints"])["requested_output_names"] = [
            "one.pdf",
            "two.pdf",
            "three.pdf",
            "four.pdf",
            "five.pdf",
        ]
        too_many_for_schema = _load_example("case-manifest-v1-minimal.json")
        cast(dict[str, Any], too_many_for_schema["constraints"])["requested_output_names"] = [
            f"output-{index}.pdf" for index in range(4_097)
        ]

        self.assertIn("MANIFEST_OUTPUT_NAME_UNSAFE_UNICODE", self._codes(disguised))
        self.assertIn("MANIFEST_OUTPUT_NAME_UNSAFE_UNICODE", self._codes(c1_control))
        self.assertIn("MANIFEST_OUTPUT_NAME_UNSAFE_UNICODE", self._codes(surrogate))
        self.assertEqual([], self._issues(exact_utf16_limit))
        self.assertIn("MANIFEST_OUTPUT_NAME_TOO_LONG", self._codes(over_utf16_limit))
        self.assertIn(
            "MANIFEST_OUTPUT_NAME_COUNT_INVALID",
            self._codes(too_many_for_constraint),
        )
        self.assertEqual({"MANIFEST_SCHEMA_VALIDATION"}, self._codes(too_many_for_schema))

    def test_source_display_names_reject_ambiguous_unicode_formatting(self) -> None:
        format_control = _load_example("case-manifest-v1-representative.json")
        format_control_source = cast(list[dict[str, Any]], format_control["sources"])[0]
        format_control_source["display_name"] = "Synthetic application\u202efdp.exe"
        noncanonical = _load_example("case-manifest-v1-representative.json")
        noncanonical_source = cast(list[dict[str, Any]], noncanonical["sources"])[0]
        noncanonical_source["display_name"] = "Synthetic re\u0301sume\u0301.pdf"

        c1_control = _load_example("case-manifest-v1-representative.json")
        c1_source = cast(list[dict[str, Any]], c1_control["sources"])[0]
        c1_source["display_name"] = "Synthetic\u0085application.pdf"
        surrogate = _load_example("case-manifest-v1-representative.json")
        surrogate_source = cast(list[dict[str, Any]], surrogate["sources"])[0]
        surrogate_source["display_name"] = "Synthetic\ud800application.pdf"

        self.assertIn("MANIFEST_DISPLAY_NAME_UNSAFE_UNICODE", self._codes(format_control))
        self.assertIn("MANIFEST_DISPLAY_NAME_UNSAFE_UNICODE", self._codes(c1_control))
        self.assertIn("MANIFEST_DISPLAY_NAME_UNSAFE_UNICODE", self._codes(surrogate))
        self.assertIn("MANIFEST_DISPLAY_NAME_NONCANONICAL", self._codes(noncanonical))

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
        fractional = _load_example("case-manifest-v1-representative.json")
        fractional_assignment = cast(list[dict[str, Any]], fractional["assignments"])[0]
        fractional_confirmation = cast(list[dict[str, Any]], fractional["confirmations"])[1]
        fractional_assignment["assigned_at"] = "2026-08-01T10:20:00.123456Z"
        fractional_confirmation["recorded_at"] = "2026-08-01T10:20:00.123455Z"

        self.assertIn("MANIFEST_TIMESTAMP_ORDER_INVALID", self._codes(revision))
        self.assertIn("MANIFEST_TIMESTAMP_ORDER_INVALID", self._codes(event))
        self.assertIn("MANIFEST_TIMESTAMP_ORDER_INVALID", self._codes(fractional))

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
        (target / CURRENT_MANIFEST_INVALID_FIXTURE_DIRECTORY).mkdir(parents=True)
        (target / V2_MANIFEST_INVALID_FIXTURE_DIRECTORY).mkdir(parents=True)
        (target / V3_MANIFEST_INVALID_FIXTURE_DIRECTORY).mkdir(parents=True)
        shutil.copy2(ROOT / MANIFEST_SCHEMA_PATH, target / MANIFEST_SCHEMA_PATH)
        shutil.copy2(ROOT / V2_MANIFEST_SCHEMA_PATH, target / V2_MANIFEST_SCHEMA_PATH)
        shutil.copy2(ROOT / V3_MANIFEST_SCHEMA_PATH, target / V3_MANIFEST_SCHEMA_PATH)
        shutil.copy2(
            ROOT / CURRENT_MANIFEST_SCHEMA_PATH,
            target / CURRENT_MANIFEST_SCHEMA_PATH,
        )
        for source in EXAMPLE_DIRECTORY.glob("case-manifest-v1-*.json"):
            shutil.copy2(source, target / "schemas" / "examples" / source.name)
        for source in EXAMPLE_DIRECTORY.glob(CURRENT_MANIFEST_EXAMPLE_PATTERN):
            shutil.copy2(source, target / "schemas" / "examples" / source.name)
        for source in EXAMPLE_DIRECTORY.glob(V2_MANIFEST_EXAMPLE_PATTERN):
            shutil.copy2(source, target / "schemas" / "examples" / source.name)
        for source in EXAMPLE_DIRECTORY.glob(V3_MANIFEST_EXAMPLE_PATTERN):
            shutil.copy2(source, target / "schemas" / "examples" / source.name)
        for source in (ROOT / V2_MANIFEST_INVALID_FIXTURE_DIRECTORY).glob("*.json"):
            shutil.copy2(source, target / V2_MANIFEST_INVALID_FIXTURE_DIRECTORY / source.name)
        for source in (ROOT / V3_MANIFEST_INVALID_FIXTURE_DIRECTORY).glob("*.json"):
            shutil.copy2(source, target / V3_MANIFEST_INVALID_FIXTURE_DIRECTORY / source.name)
        for source in (ROOT / MANIFEST_INVALID_FIXTURE_DIRECTORY).glob("*.json"):
            shutil.copy2(source, target / MANIFEST_INVALID_FIXTURE_DIRECTORY / source.name)
        for source in (ROOT / CURRENT_MANIFEST_INVALID_FIXTURE_DIRECTORY).glob("*.json"):
            shutil.copy2(
                source,
                target / CURRENT_MANIFEST_INVALID_FIXTURE_DIRECTORY / source.name,
            )

    def test_aggregate_rejects_a_corrupted_positive_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_fixture_root(root)
            positive = root / "schemas" / "examples" / "case-manifest-v1-minimal.json"
            data = cast(dict[str, Any], json.loads(positive.read_text(encoding="utf-8")))
            data["schema_version"] = 3
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

from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

from tools.validate_templates import (
    EXPECTED_NEGATIVE_CODES,
    PROJECT_ROOT,
    REPOSITORY_FIXTURE_AS_OF,
    TEMPLATE_EXAMPLE_DIRECTORY,
    TEMPLATE_INVALID_FIXTURE_DIRECTORY,
    TEMPLATE_SCHEMA_PATH,
    TemplateIssue,
    _safe_filename_stem,
    load_template_schema,
    validate_template_data,
    validate_template_document,
    validate_template_fixture_set,
)

CANONICAL_TEMPLATE_PATH = PROJECT_ROOT / "schemas/examples/template-v1-minimal.json"


def _canonical_template() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(CANONICAL_TEMPLATE_PATH.read_text(encoding="utf-8")),
    )


class TemplateValidatorTests(unittest.TestCase):
    schema: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        schema, issues = load_template_schema(PROJECT_ROOT)
        if schema is None:
            rendered = "; ".join(issue.render() for issue in issues)
            raise AssertionError(f"canonical template schema did not load: {rendered}")
        cls.schema = schema

    def assert_has_code(self, issues: list[TemplateIssue], expected_code: str) -> None:
        self.assertIn(expected_code, {issue.code for issue in issues})

    def validate(
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

    def _copy_fixture_root(self, root: Path) -> None:
        schema_target = root / TEMPLATE_SCHEMA_PATH
        schema_target.parent.mkdir(parents=True)
        shutil.copy2(PROJECT_ROOT / TEMPLATE_SCHEMA_PATH, schema_target)
        shutil.copytree(
            PROJECT_ROOT / TEMPLATE_EXAMPLE_DIRECTORY,
            root / TEMPLATE_EXAMPLE_DIRECTORY,
        )
        shutil.copytree(
            PROJECT_ROOT / TEMPLATE_INVALID_FIXTURE_DIRECTORY,
            root / TEMPLATE_INVALID_FIXTURE_DIRECTORY,
        )

    def test_checked_in_fixture_contract_passes_with_exact_counts_and_reasons(self) -> None:
        summary, issues = validate_template_fixture_set(PROJECT_ROOT)

        self.assertEqual([], issues)
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(1, summary.positive_count)
        self.assertEqual(len(EXPECTED_NEGATIVE_CODES), summary.negative_count)

        for fixture_name, expected_code in EXPECTED_NEGATIVE_CODES.items():
            with self.subTest(fixture=fixture_name):
                _, fixture_issues = validate_template_document(
                    PROJECT_ROOT / TEMPLATE_INVALID_FIXTURE_DIRECTORY / fixture_name,
                    PROJECT_ROOT,
                    schema=self.schema,
                    as_of=REPOSITORY_FIXTURE_AS_OF,
                )
                self.assert_has_code(fixture_issues, expected_code)
                expected_label = (TEMPLATE_INVALID_FIXTURE_DIRECTORY / fixture_name).as_posix()
                self.assertEqual(
                    {expected_label},
                    {issue.document for issue in fixture_issues},
                )

    def test_version_predispatch_rejects_missing_boolean_legacy_and_future_values(self) -> None:
        cases: tuple[tuple[str, object, str], ...] = (
            ("missing", None, "TEMPLATE_VERSION_INVALID"),
            ("boolean", True, "TEMPLATE_VERSION_INVALID"),
            ("legacy", 0, "TEMPLATE_VERSION_UNSUPPORTED"),
            ("future", 2, "TEMPLATE_VERSION_UNSUPPORTED"),
        )

        for label, version, expected_code in cases:
            with self.subTest(label=label):
                document = _canonical_template()
                if label == "missing":
                    document.pop("schema_version")
                else:
                    document["schema_version"] = version
                issues = self.validate(document)
                self.assertEqual([expected_code], [issue.code for issue in issues])

    def test_validation_does_not_mutate_the_supplied_document_or_schema(self) -> None:
        document = _canonical_template()
        original_document = copy.deepcopy(document)
        original_schema = copy.deepcopy(self.schema)

        self.assertEqual([], self.validate(document, as_of=REPOSITORY_FIXTURE_AS_OF))

        self.assertEqual(original_document, document)
        self.assertEqual(original_schema, self.schema)

    def test_already_decoded_inputs_preserve_the_strict_json_number_and_key_contract(self) -> None:
        floating_point = _canonical_template()
        floating_point["requirements"][0]["count"]["minimum"] = 1.0

        oversized_integer = _canonical_template()
        oversized_integer["requirements"][0]["count"]["minimum"] = 9_007_199_254_740_992

        non_string_key = _canonical_template()
        cast(dict[object, Any], non_string_key)[1] = "synthetic"

        for label, document, expected_code in (
            ("floating-point", floating_point, "TEMPLATE_JSON_NONSTANDARD_NUMBER"),
            ("oversized-integer", oversized_integer, "TEMPLATE_JSON_NONSTANDARD_NUMBER"),
            ("non-string-key", non_string_key, "TEMPLATE_JSON_VALUE_TYPE"),
        ):
            with self.subTest(label=label):
                self.assert_has_code(self.validate(document), expected_code)

    def test_text_must_be_nfc_and_exclude_unicode_control_formats_and_surrogates(self) -> None:
        unsafe_values = (
            ("non-nfc", "Cafe\u0301"),
            ("bidi-format", "Review \u202ethis"),
            ("control", "Review\u0007this"),
            ("surrogate", "Review \ud800 this"),
        )

        for label, value in unsafe_values:
            with self.subTest(label=label):
                document = _canonical_template()
                document["instructions"][0]["text"] = value
                self.assert_has_code(self.validate(document), "TEMPLATE_TEXT_UNSAFE")

        document = _canonical_template()
        document["unsafe\u202ekey"] = "inert"
        self.assert_has_code(self.validate(document), "TEMPLATE_TEXT_UNSAFE")

        document = _canonical_template()
        document["title"] = "طلب مراجعة المستندات"
        document["instructions"][0]["text"] = (
            "Treat $(synthetic-command), &&, and <strong>markup</strong> as plain guidance."
        )
        self.assertEqual([], self.validate(document, as_of=REPOSITORY_FIXTURE_AS_OF))

    def test_diagnostics_redact_unknown_root_and_nested_member_names(self) -> None:
        root_canary = "SYNTHETIC_PRIVATE_ROOT_CANARY"
        nested_canary = "SYNTHETIC_PRIVATE_NESTED_CANARY"
        document = _canonical_template()
        document[root_canary] = "Review \u202ethis"
        document["provenance"][nested_canary] = {"text": "Cafe\u0301"}

        issues = self.validate(document)
        rendered = "\n".join(issue.render() for issue in issues)

        self.assert_has_code(issues, "TEMPLATE_TEXT_UNSAFE")
        self.assertNotIn(root_canary, rendered)
        self.assertNotIn(nested_canary, rendered)
        self.assertIn("[*]", rendered)

    def test_caller_supplied_document_labels_are_bounded_repository_relative_tokens(self) -> None:
        hostile_labels = (
            r"C:\Users\Sensitive Name\private-template.json",
            r"\\server\private\template.json",
            "/home/sensitive/private-template.json",
            "../private-template.json",
            "safe/../../private-template.json",
            "safe/\nprivate-template.json",
            "safe/\u202eprivate-template.json",
            "a" * 241,
            "safe/private label.json",
        )
        invalid = {"schema_version": 2}

        for label in hostile_labels:
            with self.subTest(label=label[:40]):
                issues = validate_template_data(
                    invalid,
                    self.schema,
                    document=label,
                )
                self.assertTrue(issues)
                self.assertEqual(
                    {"external-template.json"},
                    {issue.document for issue in issues},
                )

        valid_label = "tests/fixtures/templates/v1/invalid/future-version.json"
        issues = validate_template_data(
            invalid,
            self.schema,
            document=valid_label,
        )
        self.assertEqual({valid_label}, {issue.document for issue in issues})

    def test_executable_eligibility_and_trust_members_are_rejected_before_schema_dispatch(
        self,
    ) -> None:
        cases = (
            ("$ref", "TEMPLATE_EXECUTABLE_MEMBER"),
            ("command-line", "TEMPLATE_EXECUTABLE_MEMBER"),
            ("eligible-if", "TEMPLATE_ELIGIBILITY_MEMBER"),
            ("is.trusted", "TEMPLATE_TRUST_ASSERTION_MEMBER"),
        )
        for member, expected_code in cases:
            with self.subTest(member=member):
                document = _canonical_template()
                document[member] = "synthetic inert value"
                self.assert_has_code(self.validate(document), expected_code)

    def test_requirement_filename_stems_are_conditional_on_separate_pdf_mode(self) -> None:
        combined_only = _canonical_template()
        combined_only["output"]["allowed_modes"] = ["combined_pdf", "zip"]
        combined_only["output"]["filename_policy"]["requirement_stems"] = []
        self.assertEqual([], self.validate(combined_only, as_of=REPOSITORY_FIXTURE_AS_OF))

        unused_stems = _canonical_template()
        unused_stems["output"]["allowed_modes"] = ["combined_pdf", "zip"]
        self.assert_has_code(
            self.validate(unused_stems),
            "TEMPLATE_REQUIREMENT_STEMS_UNUSED",
        )

        incomplete_stems = _canonical_template()
        incomplete_stems["output"]["filename_policy"]["requirement_stems"].pop()
        self.assert_has_code(
            self.validate(incomplete_stems),
            "TEMPLATE_REQUIREMENT_STEMS_INCOMPLETE",
        )

        insufficient_file_capacity = _canonical_template()
        insufficient_file_capacity["output"]["limits"]["maximum_file_count"] = 3
        self.assert_has_code(
            self.validate(insufficient_file_capacity),
            "TEMPLATE_OUTPUT_LIMIT_INVALID",
        )

    def test_windows_device_names_remain_reserved_when_followed_by_an_extension(self) -> None:
        for stem in ("CON.txt", "con.report", "LPT1.report", "aux.pdf"):
            with self.subTest(stem=stem):
                self.assertFalse(_safe_filename_stem(stem))
        self.assertTrue(_safe_filename_stem("application-form"))
        self.assertTrue(_safe_filename_stem("a" * 118 + "\U0001f4c4"))
        self.assertFalse(_safe_filename_stem("a" * 119 + "\U0001f4c4"))

    def test_identifiers_references_and_order_are_checked_semantically(self) -> None:
        namespace_collision = _canonical_template()
        namespace_collision["warnings"][0]["warning_id"] = "application-form"

        incomplete_order = _canonical_template()
        incomplete_order["requirement_order"].pop()

        unknown_confirmation_scope = _canonical_template()
        unknown_confirmation_scope["confirmations"][0]["scope"] = {
            "kind": "requirement",
            "requirement_id": "missing-requirement",
        }

        cases = (
            (
                "namespace-collision",
                namespace_collision,
                "TEMPLATE_ID_NAMESPACE_COLLISION",
            ),
            (
                "incomplete-order",
                incomplete_order,
                "TEMPLATE_REQUIREMENT_ORDER_INCOMPLETE",
            ),
            (
                "unknown-confirmation-scope",
                unknown_confirmation_scope,
                "TEMPLATE_REQUIREMENT_REFERENCE_UNKNOWN",
            ),
        )
        for label, document, expected_code in cases:
            with self.subTest(label=label):
                self.assert_has_code(self.validate(document), expected_code)

    def test_expiry_is_inclusive_and_requires_an_explicit_evaluation_date(self) -> None:
        document = _canonical_template()
        expiry = date.fromisoformat(document["lifecycle"]["expires_on"])

        self.assertEqual([], self.validate(copy.deepcopy(document)))
        self.assertEqual([], self.validate(copy.deepcopy(document), as_of=expiry))
        self.assert_has_code(
            self.validate(copy.deepcopy(document), as_of=expiry + timedelta(days=1)),
            "TEMPLATE_EXPIRED",
        )

        verified_on = date.fromisoformat(document["lifecycle"]["verified_on"])
        self.assert_has_code(
            self.validate(copy.deepcopy(document), as_of=verified_on - timedelta(days=1)),
            "TEMPLATE_LIFECYCLE_AS_OF_INVALID",
        )

    def test_strict_document_loader_rejects_duplicate_keys_invalid_utf8_and_non_object_roots(
        self,
    ) -> None:
        cases: tuple[tuple[str, bytes, str], ...] = (
            (
                "duplicate-key",
                b'{"schema_version":1,"schema_version":1}',
                "TEMPLATE_JSON_DUPLICATE_KEY",
            ),
            (
                "invalid-utf8",
                b'{"schema_version":1,"title":"\xff"}',
                "TEMPLATE_TEXT_ENCODING_INVALID",
            ),
            ("array-root", b"[]", "TEMPLATE_JSON_ROOT_TYPE"),
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for label, raw, expected_code in cases:
                with self.subTest(label=label):
                    path = root / f"{label}.json"
                    path.write_bytes(raw)
                    data, issues = validate_template_document(
                        path,
                        root,
                        schema=self.schema,
                    )
                    self.assertIsNone(data)
                    self.assert_has_code(issues, expected_code)

    def test_fixture_aggregate_rejects_unreviewed_names_and_accidentally_valid_negatives(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_fixture_root(root)
            extra = root / TEMPLATE_INVALID_FIXTURE_DIRECTORY / "unreviewed.json"
            shutil.copy2(CANONICAL_TEMPLATE_PATH, extra)

            summary, issues = validate_template_fixture_set(root)

            self.assertIsNone(summary)
            self.assert_has_code(issues, "TEMPLATE_NEGATIVE_FIXTURE_SET_MISMATCH")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_fixture_root(root)
            negative = root / TEMPLATE_INVALID_FIXTURE_DIRECTORY / "future-version.json"
            shutil.copy2(CANONICAL_TEMPLATE_PATH, negative)

            summary, issues = validate_template_fixture_set(root)

            self.assertIsNone(summary)
            self.assert_has_code(issues, "TEMPLATE_NEGATIVE_FIXTURE_ACCEPTED")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_fixture_root(root)
            negative = root / TEMPLATE_INVALID_FIXTURE_DIRECTORY / "script-injection.json"
            loaded = cast(dict[str, Any], json.loads(negative.read_text(encoding="utf-8")))
            loaded["eligibility"] = "synthetic condition"
            negative.write_text(json.dumps(loaded), encoding="utf-8")

            summary, issues = validate_template_fixture_set(root)

            self.assertIsNone(summary)
            self.assert_has_code(issues, "TEMPLATE_NEGATIVE_FIXTURE_REASON_MISMATCH")

    def test_negative_fixture_directory_rejects_every_unreviewed_entry(self) -> None:
        cases = (
            ("extra-template.yaml", False),
            ("review-notes.txt", False),
            ("unreviewed-directory", True),
        )
        for entry_name, is_directory in cases:
            with self.subTest(entry=entry_name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self._copy_fixture_root(root)
                entry = root / TEMPLATE_INVALID_FIXTURE_DIRECTORY / entry_name
                if is_directory:
                    entry.mkdir()
                else:
                    entry.write_text("synthetic unreviewed entry", encoding="utf-8")

                summary, issues = validate_template_fixture_set(root)

                self.assertIsNone(summary)
                self.assertEqual(
                    {"TEMPLATE_NEGATIVE_FIXTURE_SET_MISMATCH"},
                    {issue.code for issue in issues},
                )
                self.assertNotIn(entry_name, "\n".join(issue.render() for issue in issues))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from tools.validate_generic_portal_template import (
    APPROVED_PACKET_FIXTURE_IDS,
    CANONICAL_TEMPLATE_PATH,
    EXPECTED_REQUIREMENT_LIMITS,
    EXPECTED_TEMPLATE_BYTES,
    EXPECTED_TEMPLATE_SHA256,
    PACKAGED_TEMPLATE_PATH,
    PACKET_FIXTURE_PATH,
    PROJECT_ROOT,
    SYNTHETIC_INDEX_PATH,
    GenericPortalIssue,
    PacketFinding,
    evaluate_document_counts,
    validate_generic_portal_template,
)
from tools.validate_templates import TEMPLATE_SCHEMA_PATH


def _read_object(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


class GenericPortalPacketDemonstrationTests(unittest.TestCase):
    def assert_has_code(
        self,
        issues: list[GenericPortalIssue],
        expected_code: str,
    ) -> None:
        self.assertIn(expected_code, {issue.code for issue in issues})

    @contextmanager
    def copied_contract(self) -> Iterator[Path]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in (
                CANONICAL_TEMPLATE_PATH,
                PACKAGED_TEMPLATE_PATH,
                PACKET_FIXTURE_PATH,
                TEMPLATE_SCHEMA_PATH,
            ):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(PROJECT_ROOT / relative, target)
            shutil.copytree(
                PROJECT_ROOT / SYNTHETIC_INDEX_PATH.parent,
                root / SYNTHETIC_INDEX_PATH.parent,
            )
            yield root

    def test_checked_in_demonstration_pins_identity_inventory_and_count_results(self) -> None:
        summary, issues = validate_generic_portal_template(PROJECT_ROOT)

        self.assertEqual([], issues)
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(3, summary.scenario_count)
        self.assertEqual(21, summary.document_count)
        self.assertEqual(14, summary.approved_fixture_count)
        self.assertEqual(
            (
                ("complete", ()),
                (
                    "missing",
                    (
                        PacketFinding(
                            "GENERIC_PACKET_REQUIREMENT_MISSING",
                            "address-evidence",
                            0,
                            1,
                            2,
                        ),
                    ),
                ),
                (
                    "excessive",
                    (
                        PacketFinding(
                            "GENERIC_PACKET_REQUIREMENT_EXCESSIVE",
                            "supporting-evidence",
                            11,
                            1,
                            10,
                        ),
                    ),
                ),
            ),
            summary.findings_by_scenario,
        )

        packet = _read_object(PROJECT_ROOT / PACKET_FIXTURE_PATH)
        reference = cast(dict[str, object], packet["template"])
        self.assertEqual(EXPECTED_TEMPLATE_BYTES, reference["byte_size"])
        self.assertEqual(EXPECTED_TEMPLATE_SHA256, reference["sha256"])
        scenarios = cast(list[dict[str, Any]], packet["scenarios"])
        excessive = scenarios[2]
        supporting = [
            document
            for document in cast(list[dict[str, str]], excessive["documents"])
            if document["requirement_id"] == "supporting-evidence"
        ]
        self.assertEqual(11, len(supporting))

    def test_count_evaluator_has_no_assignment_checklist_or_output_inputs(self) -> None:
        documents: tuple[Mapping[str, object], ...] = (
            {"requirement_id": "application-form"},
            {"requirement_id": "identity-evidence"},
            {"requirement_id": "supporting-evidence"},
        )

        self.assertEqual(
            (
                PacketFinding(
                    "GENERIC_PACKET_REQUIREMENT_MISSING",
                    "address-evidence",
                    0,
                    1,
                    2,
                ),
            ),
            evaluate_document_counts(documents, EXPECTED_REQUIREMENT_LIMITS),
        )

    def test_canonical_and_packaged_artifacts_must_remain_byte_identical_and_pinned(self) -> None:
        with self.copied_contract() as root:
            with (root / PACKAGED_TEMPLATE_PATH).open("ab") as stream:
                stream.write(b"\n")

            summary, issues = validate_generic_portal_template(root)

        self.assertIsNone(summary)
        self.assert_has_code(issues, "GENERIC_TEMPLATE_SIZE_MISMATCH")
        self.assert_has_code(issues, "GENERIC_TEMPLATE_DIGEST_MISMATCH")
        self.assert_has_code(issues, "GENERIC_TEMPLATE_COPY_MISMATCH")

    def test_packet_json_rejects_duplicate_keys_and_non_integer_numbers(self) -> None:
        mutations = (
            (
                "duplicate",
                '"schema_version": 1,\n  "schema_version": 1,',
                "GENERIC_PACKET_JSON_DUPLICATE_KEY",
            ),
            (
                "float",
                '"schema_version": 1.0,',
                "GENERIC_PACKET_JSON_NUMBER_INVALID",
            ),
        )
        for label, replacement, expected_code in mutations:
            with self.subTest(label=label), self.copied_contract() as root:
                path = root / PACKET_FIXTURE_PATH
                text = path.read_text(encoding="utf-8")
                text = text.replace('"schema_version": 1,', replacement, 1)
                path.write_text(text, encoding="utf-8")

                summary, issues = validate_generic_portal_template(root)

                self.assertIsNone(summary)
                self.assert_has_code(issues, expected_code)

    def test_packet_contract_is_closed_and_bounded(self) -> None:
        with self.copied_contract() as root:
            packet = _read_object(root / PACKET_FIXTURE_PATH)
            packet["unreviewed_member"] = "synthetic"
            (root / PACKET_FIXTURE_PATH).write_text(
                json.dumps(packet),
                encoding="utf-8",
            )

            summary, issues = validate_generic_portal_template(root)

        self.assertIsNone(summary)
        self.assert_has_code(issues, "GENERIC_PACKET_SHAPE_INVALID")

        with self.copied_contract() as root:
            packet_path = root / PACKET_FIXTURE_PATH
            packet_path.write_bytes(packet_path.read_bytes() + (b" " * 65_536))

            summary, issues = validate_generic_portal_template(root)

        self.assertIsNone(summary)
        self.assert_has_code(issues, "GENERIC_PACKET_INPUT_TOO_LARGE")

    def test_scenario_inventory_and_reviewed_counts_are_exact(self) -> None:
        with self.copied_contract() as root:
            packet = _read_object(root / PACKET_FIXTURE_PATH)
            scenarios = cast(list[dict[str, Any]], packet["scenarios"])
            scenarios.reverse()
            (root / PACKET_FIXTURE_PATH).write_text(json.dumps(packet), encoding="utf-8")

            summary, issues = validate_generic_portal_template(root)

        self.assertIsNone(summary)
        self.assert_has_code(issues, "GENERIC_PACKET_SCENARIO_SET_MISMATCH")

        with self.copied_contract() as root:
            packet = _read_object(root / PACKET_FIXTURE_PATH)
            scenarios = cast(list[dict[str, Any]], packet["scenarios"])
            excessive_documents = cast(list[dict[str, str]], scenarios[2]["documents"])
            excessive_documents.pop()
            (root / PACKET_FIXTURE_PATH).write_text(json.dumps(packet), encoding="utf-8")

            summary, issues = validate_generic_portal_template(root)

        self.assertIsNone(summary)
        self.assert_has_code(issues, "GENERIC_PACKET_SCENARIO_COUNTS_MISMATCH")

    def test_requirement_ids_must_be_known_and_fixture_ids_must_be_reviewed(self) -> None:
        mutations = (
            ("requirement_id", "unknown-requirement", "GENERIC_PACKET_REQUIREMENT_UNKNOWN"),
            ("fixture_id", "pdf-action-marker", "GENERIC_PACKET_FIXTURE_UNAPPROVED"),
        )
        for member, value, expected_code in mutations:
            with self.subTest(member=member), self.copied_contract() as root:
                packet = _read_object(root / PACKET_FIXTURE_PATH)
                scenarios = cast(list[dict[str, Any]], packet["scenarios"])
                documents = cast(list[dict[str, str]], scenarios[0]["documents"])
                documents[0][member] = value
                (root / PACKET_FIXTURE_PATH).write_text(json.dumps(packet), encoding="utf-8")

                summary, issues = validate_generic_portal_template(root)

                self.assertIsNone(summary)
                self.assert_has_code(issues, expected_code)

    def test_every_packet_fixture_resolves_through_the_public_synthetic_index(self) -> None:
        packet = _read_object(PROJECT_ROOT / PACKET_FIXTURE_PATH)
        scenarios = cast(list[dict[str, Any]], packet["scenarios"])
        used_ids = {
            document["fixture_id"]
            for scenario in scenarios
            for document in cast(list[dict[str, str]], scenario["documents"])
        }
        self.assertEqual(APPROVED_PACKET_FIXTURE_IDS, used_ids)

        with self.copied_contract() as root:
            index = _read_object(root / SYNTHETIC_INDEX_PATH)
            fixtures = cast(list[dict[str, Any]], index["fixtures"])
            index["fixtures"] = [
                fixture for fixture in fixtures if fixture["id"] != "pdf-born-digital"
            ]
            (root / SYNTHETIC_INDEX_PATH).write_text(json.dumps(index), encoding="utf-8")

            summary, issues = validate_generic_portal_template(root)

        self.assertIsNone(summary)
        self.assert_has_code(issues, "GENERIC_PACKET_FIXTURE_UNRESOLVED")


if __name__ == "__main__":
    unittest.main()

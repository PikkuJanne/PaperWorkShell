from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from tools.generate_synthetic_fixtures import (
    CORPUS_DIRECTORY,
    GOLDEN_DIRECTORY,
    INDEX_PATH,
    PROJECT_ROOT,
    SCHEMA_PATH,
)
from tools.validate_synthetic_fixtures import (
    EXPECTED_NEGATIVE_CODES,
    MAX_CORPUS_ENTRIES,
    load_fixture_schema,
    validate_fixture_corpus,
    validate_fixture_index_document,
    validate_fixture_index_set,
)


class SyntheticFixtureValidatorTests(unittest.TestCase):
    def _copy_fixture_root(self, destination: Path) -> None:
        (destination / SCHEMA_PATH).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / SCHEMA_PATH, destination / SCHEMA_PATH)
        shutil.copytree(PROJECT_ROOT / CORPUS_DIRECTORY, destination / CORPUS_DIRECTORY)
        shutil.copytree(PROJECT_ROOT / GOLDEN_DIRECTORY, destination / GOLDEN_DIRECTORY)

    def test_checked_in_index_and_corpus_pass(self) -> None:
        index_summary, index_issues = validate_fixture_index_set()
        corpus_summary, corpus_issues = validate_fixture_corpus(inspect_repository=False)

        self.assertEqual([], index_issues)
        self.assertIsNotNone(index_summary)
        self.assertEqual(41, index_summary.fixture_count if index_summary else 0)
        self.assertEqual(24, index_summary.coverage_count if index_summary else 0)
        self.assertEqual(
            len(EXPECTED_NEGATIVE_CODES), index_summary.negative_count if index_summary else 0
        )
        self.assertEqual([], corpus_issues)
        self.assertEqual(41, corpus_summary.fixture_count if corpus_summary else 0)
        self.assertEqual(15, corpus_summary.golden_reference_count if corpus_summary else 0)

    def test_mutated_missing_and_extra_renderer_goldens_fail_closed(self) -> None:
        cases = {
            "mutated": "FIXTURE_GOLDEN_CONTENT_MISMATCH",
            "missing": "FIXTURE_GOLDEN_SET_MISMATCH",
            "extra": "FIXTURE_GOLDEN_SET_MISMATCH",
        }
        for mutation, expected_code in cases.items():
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self._copy_fixture_root(root)
                target = root / GOLDEN_DIRECTORY / "render-calibration-crop-rotate.png"
                if mutation == "mutated":
                    target.write_bytes(target.read_bytes() + b"x")
                elif mutation == "missing":
                    target.unlink()
                else:
                    (root / GOLDEN_DIRECTORY / "extra.png").write_bytes(b"not-an-image")

                summary, issues = validate_fixture_corpus(root, inspect_repository=False)

            self.assertIsNone(summary)
            self.assertEqual({expected_code}, {issue.code for issue in issues})

    def test_mutated_missing_and_extra_artifacts_fail_closed(self) -> None:
        mutations = ("mutated", "missing", "extra")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self._copy_fixture_root(root)
                target = root / CORPUS_DIRECTORY / "pdf" / "born-digital.pdf"
                if mutation == "mutated":
                    target.write_bytes(target.read_bytes() + b"x")
                elif mutation == "missing":
                    target.unlink()
                else:
                    (root / CORPUS_DIRECTORY / "pdf" / "extra.pdf").write_bytes(b"%PDF-1.4")

                summary, issues = validate_fixture_corpus(root, inspect_repository=False)

            self.assertIsNone(summary)
            self.assertTrue(issues)
            self.assertIn(
                issues[0].code,
                {"FIXTURE_CONTENT_MISMATCH", "FIXTURE_SET_MISMATCH"},
            )

    def test_index_metadata_and_encoding_are_generator_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_fixture_root(root)
            path = root / INDEX_PATH
            loaded = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
            fixtures = cast(list[dict[str, Any]], loaded["fixtures"])
            fixtures[0]["bytes"] = cast(int, fixtures[0]["bytes"]) + 1
            path.write_text(
                json.dumps(loaded, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
            )

            summary, issues = validate_fixture_corpus(root, inspect_repository=False)

        self.assertIsNone(summary)
        self.assertEqual({"FIXTURE_INDEX_CANONICAL_MISMATCH"}, {issue.code for issue in issues})

    def test_schema_fingerprint_rejects_unreviewed_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_fixture_root(root)
            schema_path = root / SCHEMA_PATH
            schema_path.write_bytes(schema_path.read_bytes() + b"\n")

            schema, issues = load_fixture_schema(root)

        self.assertIsNone(schema)
        self.assertEqual(
            {"FIXTURE_SCHEMA_FINGERPRINT_MISMATCH"},
            {issue.code for issue in issues},
        )

    def test_schema_parser_consumes_the_authenticated_read_only(self) -> None:
        authenticated = (PROJECT_ROOT / SCHEMA_PATH).read_bytes()
        unauthenticated = authenticated.replace(b'"minItems": 17', b'"minItems": 18')
        with patch(
            "tools.validate_synthetic_fixtures._read_regular_bytes",
            side_effect=[(authenticated, []), (unauthenticated, [])],
        ) as read:
            schema, issues = load_fixture_schema()

        self.assertEqual([], issues)
        self.assertIsNotNone(schema)
        self.assertEqual(1, read.call_count)

    def test_schema_read_rejects_a_linked_ancestor_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_fixture_root(root)
            schema_directory = (root / SCHEMA_PATH).parent
            with patch(
                "tools.validate_synthetic_fixtures._path_is_link_or_reparse",
                side_effect=lambda path: path == schema_directory,
            ):
                schema, issues = load_fixture_schema(root)

        self.assertIsNone(schema)
        self.assertEqual({"FIXTURE_SCHEMA_PATH_UNSAFE"}, {issue.code for issue in issues})

    def test_schema_read_rejects_an_open_handle_outside_the_authenticated_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_fixture_root(root)
            with patch(
                "tools.validate_synthetic_fixtures._opened_file_path",
                return_value=root.parent / "external" / "synthetic-fixture-schema.json",
            ):
                schema, issues = load_fixture_schema(root)

        self.assertIsNone(schema)
        self.assertEqual({"FIXTURE_SCHEMA_PATH_UNSAFE"}, {issue.code for issue in issues})

    def test_version_duplicate_key_and_schema_failures_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / SCHEMA_PATH).parent.mkdir(parents=True)
            shutil.copy2(PROJECT_ROOT / SCHEMA_PATH, root / SCHEMA_PATH)
            cases = {
                "future.json": (b'{"schema_version":2}', "FIXTURE_INDEX_VERSION_FUTURE"),
                "legacy.json": (b'{"schema_version":0}', "FIXTURE_INDEX_VERSION_LEGACY"),
                "duplicate.json": (
                    b'{"schema_version":1,"schema_version":1}',
                    "FIXTURE_INDEX_JSON_DUPLICATE_KEY",
                ),
                "invalid.json": (b'{"schema_version":1}', "FIXTURE_INDEX_SCHEMA_INVALID"),
            }
            for name, (content, expected_code) in cases.items():
                with self.subTest(name=name):
                    path = root / name
                    path.write_bytes(content)

                    loaded, issues = validate_fixture_index_document(path, root)

                    self.assertIsNone(loaded)
                    self.assertEqual({expected_code}, {issue.code for issue in issues})

    def test_linked_fixture_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_fixture_root(root)
            source = root / CORPUS_DIRECTORY / "pdf" / "duplicate-a.pdf"
            target = root / CORPUS_DIRECTORY / "pdf" / "duplicate-b.pdf"
            target.unlink()
            os.link(source, target)

            summary, issues = validate_fixture_corpus(root, inspect_repository=False)

        self.assertIsNone(summary)
        self.assertEqual({"FIXTURE_CONTENT_PATH_UNSAFE"}, {issue.code for issue in issues})

    def test_corpus_entry_limit_stops_oversized_nested_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_fixture_root(root)
            extra_root = root / CORPUS_DIRECTORY / "excess"
            extra_root.mkdir()
            for index in range(MAX_CORPUS_ENTRIES + 1):
                (extra_root / f"item-{index:03d}").write_bytes(b"x")

            summary, issues = validate_fixture_corpus(root, inspect_repository=False)

        self.assertIsNone(summary)
        self.assertEqual({"FIXTURE_SET_LIMIT"}, {issue.code for issue in issues})

    def test_negative_inventory_limit_fails_before_validating_extra_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_fixture_root(root)
            (root / CORPUS_DIRECTORY / "invalid" / "extra.json").write_bytes(b"not-json")

            summary, issues = validate_fixture_index_set(root)

        self.assertIsNone(summary)
        self.assertEqual({"FIXTURE_NEGATIVE_SET_MISMATCH"}, {issue.code for issue in issues})


if __name__ == "__main__":
    unittest.main()

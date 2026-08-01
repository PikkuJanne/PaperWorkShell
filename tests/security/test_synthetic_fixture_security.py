from __future__ import annotations

import ast
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.dev import inspect_repository_contents
from tools.generate_synthetic_fixtures import PROJECT_ROOT, build_committed_fixtures
from tools.validate_synthetic_fixtures import (
    RepositoryPrivacyFinding,
    _contains_prohibited_pattern,
    _repository_inspector,
)


class SyntheticFixtureSecurityTests(unittest.TestCase):
    def _git(self, root: Path, *arguments: str) -> None:
        completed = subprocess.run(  # noqa: S603 - fixed Git test setup with literal arguments
            ["git", *arguments],
            cwd=root,
            check=False,
            shell=False,
            capture_output=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr.decode(errors="replace"))

    def _repository(self, root: Path) -> None:
        self._git(root, "init", "--initial-branch=main")
        self._git(root, "config", "user.name", "PWS Synthetic Quality")
        self._git(root, "config", "user.email", "quality@example.invalid")
        (root / "README.md").write_text("synthetic repository\n", encoding="utf-8")
        self._git(root, "add", "README.md")
        self._git(root, "commit", "-m", "synthetic baseline")

    def test_high_confidence_patterns_are_detected_without_matching_reserved_examples(self) -> None:
        candidates = (
            b"person" + b"@" + b"ordinary-domain" + b".org",
            b"123" + b"-" + b"45" + b"-" + b"6789",
            b"4111" + b" " + b"1111" + b" " + b"1111" + b" " + b"1111",
            b"GB82" + b"WEST" + b"1234" + b"5698" + b"7654" + b"32",
            b"P<" + b"UTO" + b"SYNTHETIC<<<<<<<<<<<<<<<<<<<<<<",
        )

        for candidate in candidates:
            with self.subTest(kind=len(candidate)):
                self.assertTrue(_contains_prohibited_pattern(candidate))
        self.assertFalse(_contains_prohibited_pattern(b"quality" + b"@" + b"example.invalid"))

    def test_generated_fixture_bytes_have_no_prohibited_patterns(self) -> None:
        for fixture in build_committed_fixtures():
            with self.subTest(fixture=fixture.fixture_id):
                self.assertFalse(_contains_prohibited_pattern(fixture.content))

    def test_unapproved_document_diagnostic_does_not_echo_source_bytes(self) -> None:
        canary = b"private-" + b"document-" + b"token"
        inspect = _repository_inspector(frozenset())

        with self.assertRaises(RepositoryPrivacyFinding) as raised:
            inspect(b"%PDF-1.4\n" + canary)

        rendered = f"{raised.exception.code} {raised.exception.message}"
        self.assertEqual("FIXTURE_UNINDEXED_DOCUMENT", raised.exception.code)
        self.assertNotIn(canary.decode("ascii"), rendered)

    def test_deleted_historical_document_blob_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repository(root)
            path = root / "historical.pdf"
            path.write_bytes(b"%PDF-1.4\n% unapproved historical test blob\n")
            self._git(root, "add", "historical.pdf")
            self._git(root, "commit", "-m", "add synthetic policy violation")
            path.unlink()
            self._git(root, "add", "--all")
            self._git(root, "commit", "-m", "remove synthetic policy violation")

            with self.assertRaises(RepositoryPrivacyFinding) as raised:
                inspect_repository_contents(root, _repository_inspector(frozenset()))

        self.assertEqual("FIXTURE_UNINDEXED_DOCUMENT", raised.exception.code)

    def test_exact_index_blob_is_inspected_even_if_working_file_is_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repository(root)
            path = root / "staged.pdf"
            path.write_bytes(b"%PDF-1.4\n% unapproved index test blob\n")
            self._git(root, "add", "staged.pdf")
            path.write_text("working replacement\n", encoding="utf-8")

            with self.assertRaises(RepositoryPrivacyFinding) as raised:
                inspect_repository_contents(root, _repository_inspector(frozenset()))

        self.assertEqual("FIXTURE_UNINDEXED_DOCUMENT", raised.exception.code)

    def test_nonignored_untracked_document_is_inspected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repository(root)
            (root / "untracked.png").write_bytes(
                b"\x89PNG\r\n\x1a\n" + b"unapproved untracked test blob"
            )

            with self.assertRaises(RepositoryPrivacyFinding) as raised:
                inspect_repository_contents(root, _repository_inspector(frozenset()))

        self.assertEqual("FIXTURE_UNINDEXED_DOCUMENT", raised.exception.code)

    def test_generator_has_no_network_process_random_clock_or_external_document_import(
        self,
    ) -> None:
        source_path = PROJECT_ROOT / "tools" / "generate_synthetic_fixtures.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        prohibited = {"datetime", "random", "secrets", "socket", "subprocess", "time", "urllib"}
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", maxsplit=1)[0])

        self.assertEqual(set(), imports & prohibited)


if __name__ == "__main__":
    unittest.main()

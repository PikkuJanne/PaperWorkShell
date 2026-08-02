from __future__ import annotations

import ast
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.dev import inspect_repository_contents
from tools.generate_synthetic_fixtures import (
    CORPUS_DIRECTORY,
    LONG_PATH_COMPONENT,
    PROJECT_ROOT,
    FixtureGenerationError,
    build_committed_fixtures,
    generate_long_path_tree,
    write_committed_corpus,
)
from tools.validate_synthetic_fixtures import (
    _IBAN_LENGTHS,
    RepositoryPrivacyFinding,
    _contains_prohibited_pattern,
    _repository_inspector,
)


class SyntheticFixtureSecurityTests(unittest.TestCase):
    def _git_output(
        self,
        root: Path,
        *arguments: str,
        input_bytes: bytes | None = None,
    ) -> bytes:
        completed = subprocess.run(  # noqa: S603 - fixed Git test setup with literal arguments
            ["git", *arguments],
            cwd=root,
            check=False,
            shell=False,
            capture_output=True,
            input=input_bytes,
        )
        self.assertEqual(0, completed.returncode, completed.stderr.decode(errors="replace"))
        return completed.stdout.strip()

    def _git(self, root: Path, *arguments: str) -> None:
        self._git_output(root, *arguments)

    def _repository(self, root: Path) -> None:
        self._git(root, "init", "--initial-branch=main")
        self._git(root, "config", "user.name", "PWS Synthetic Quality")
        self._git(root, "config", "user.email", "quality@example.invalid")
        (root / "README.md").write_text("synthetic repository\n", encoding="utf-8")
        self._git(root, "add", "README.md")
        self._git(root, "commit", "-m", "synthetic baseline")

    @staticmethod
    def _luhn_candidate(prefix: bytes, length: int) -> bytes:
        fill = (b"1234567890" * 2)[: length - len(prefix) - 1]
        body = prefix + fill
        total = 0
        for index, encoded in enumerate(body):
            value = encoded - ord("0")
            if index % 2 == length % 2:
                value *= 2
                if value > 9:
                    value -= 9
            total += value
        return body + bytes((ord("0") + (-total % 10),))

    def test_high_confidence_patterns_are_detected_without_matching_reserved_examples(self) -> None:
        compact_iban = b"GB82" + b"WEST" + b"1234" + b"5698" + b"7654" + b"32"
        grouped_iban = b"gb82" + b" west" + b" 1234" + b" 5698" + b" 7654" + b" 32"
        nbsp = b"\xc2\xa0"
        narrow_nbsp = b"\xe2\x80\xaf"
        candidates = (
            b"person" + b"@" + b"ordinary-domain" + b".org",
            b"123" + b"-" + b"45" + b"-" + b"6789",
            b"4111" + b" " + b"1111" + b" " + b"1111" + b" " + b"1111",
            b"4111" + b"-" + b"1111" + b"-" + b"1111" + b"-" + b"1111",
            b"4111" + b"1111" + b"1111" + b"1111",
            b"4000" + b"0000" + b"0000" + b"0000" + b"006",
            b"3530" + b"1113" + b"3330" + b"0000",
            b"3056" + b"9309" + b"0259" + b"04",
            self._luhn_candidate(b"1", 15),
            self._luhn_candidate(b"3530", 17),
            self._luhn_candidate(b"3530", 18),
            b"4111" + nbsp + b"1111" + nbsp + b"1111" + nbsp + b"1111",
            b"4111" + narrow_nbsp + b"1111" + narrow_nbsp + b"1111" + narrow_nbsp + b"1111",
            b"4111\t1111\t1111\t1111",
            b"4111-" + b"1111 " + b"1111-" + b"1111",
            b"4111" + nbsp + b"1111-1111" + narrow_nbsp + b"1111",
            compact_iban,
            grouped_iban,
            compact_iban + b" PAYMENT",
            compact_iban + b" 12345",
            grouped_iban + b" PAYMENT",
            nbsp.join((b"GB82", b"WEST", b"1234", b"5698", b"7654", b"32")),
            narrow_nbsp.join((b"GB82", b"WEST", b"1234", b"5698", b"7654", b"32")),
            b"GB82\tWEST\t1234\t5698\t7654\t32",
            b"AL47" + b"2121" + b"1009" + b"0000" + b"0002" + b"3569" + b"8741",
            b"TR33" + b"0006" + b"1005" + b"1978" + b"6457" + b"8413" + b"26",
            b"P<" + b"UTO" + b"SYNTHETIC<<<<<<<<<<<<<<<<<<<<<<",
        )

        for candidate in candidates:
            with self.subTest(kind=len(candidate)):
                self.assertTrue(_contains_prohibited_pattern(candidate))
        reserved_addresses = (
            b"quality" + b"@" + b"example.invalid",
            b"quality" + b"@" + b"example.com",
            b"quality" + b"@" + b"sub.example.net",
            b"quality" + b"@" + b"example.org",
        )
        for address in reserved_addresses:
            self.assertFalse(_contains_prohibited_pattern(address))
        self.assertTrue(_contains_prohibited_pattern(b"quality" + b"@" + b"notexample.com"))
        self.assertFalse(
            _contains_prohibited_pattern(b"abc" + b"3649" + b"5221" + b"0051" + b"811" + b"def")
        )
        self.assertFalse(_contains_prohibited_pattern(b"00000000-0000-4000-8000-000000000001"))

    def test_iban_registry_bounds_validation_work_and_rejects_digest_shapes(self) -> None:
        self.assertEqual(89, len(_IBAN_LENGTHS))
        self.assertEqual(
            {b"AL": 28, b"BI": 27, b"RU": 33, b"TR": 26, b"YE": 30},
            {code: _IBAN_LENGTHS[code] for code in (b"AL", b"BI", b"RU", b"TR", b"YE")},
        )
        digest = hashlib.md5(b"829", usedforsecurity=False).hexdigest().encode("ascii")
        self.assertEqual(b"ce78d1da254c0843eb23951ae077ff5f", digest)
        self.assertFalse(_contains_prohibited_pattern(digest))

        with patch("tools.validate_synthetic_fixtures._iban_valid") as validate_iban:
            self.assertFalse(_contains_prohibited_pattern(b"AA00 " * 50_000))
        validate_iban.assert_not_called()

    def test_non_card_luhn_values_are_not_classified_as_payment_cards(self) -> None:
        for offset in range(10):
            with self.subTest(offset=offset):
                epoch_milliseconds = str(1_780_000_000_000 + offset).encode("ascii")
                self.assertFalse(_contains_prohibited_pattern(epoch_milliseconds))

    def test_generated_fixture_bytes_have_no_prohibited_patterns(self) -> None:
        for fixture in build_committed_fixtures():
            with self.subTest(fixture=fixture.fixture_id):
                self.assertFalse(_contains_prohibited_pattern(fixture.content))

    def test_unapproved_document_diagnostic_does_not_echo_source_bytes(self) -> None:
        canary = b"private-" + b"document-" + b"token"
        inspect = _repository_inspector(frozenset())

        variants = (
            b"%PDF-1.4\n" + canary,
            b"\xef\xbb\xbf\n%PDF-1.4\n" + canary,
            b"\r\n  %PDF-1.4\n" + canary,
            b"X\x00JUNK%PDF-1.7\n" + canary,
            b"X\x00JUNK\x89PNG\r\n\x1a\n" + canary,
            b"X\x00JUNK\xff\xd8\xff" + canary,
            b"X\x00JUNKII*\x00" + canary,
            b"X\x00JUNKMM\x00*" + canary,
            b"X" * 1023 + b"%PDF-1.7\n" + canary,
        )
        for variant_number, variant in enumerate(variants):
            with self.subTest(variant=variant_number):
                with self.assertRaises(RepositoryPrivacyFinding) as raised:
                    inspect(variant)

                self.assertEqual("FIXTURE_UNINDEXED_DOCUMENT", raised.exception.code)
                self.assertNotIn(canary.decode("ascii"), str(raised.exception))

        rendered = f"{raised.exception.code} {raised.exception.message}"
        self.assertEqual("FIXTURE_UNINDEXED_DOCUMENT", raised.exception.code)
        self.assertNotIn(canary.decode("ascii"), rendered)

    def test_deleted_historical_document_blob_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repository(root)
            path = root / "historical.pdf"
            path.write_bytes(b"\xef\xbb\xbf\n%PDF-1.4\n% unapproved historical test blob\n")
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
            path.write_bytes(b"\n%PDF-1.4\n% unapproved index test blob\n")
            self._git(root, "add", "staged.pdf")
            path.write_text("working replacement\n", encoding="utf-8")

            with self.assertRaises(RepositoryPrivacyFinding) as raised:
                inspect_repository_contents(root, _repository_inspector(frozenset()))

        self.assertEqual("FIXTURE_UNINDEXED_DOCUMENT", raised.exception.code)

    def test_nonignored_untracked_document_is_inspected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repository(root)
            (root / "untracked.pdf").write_bytes(
                b"\xef\xbb\xbf  \r\n%PDF-1.4\n" + b"unapproved untracked test blob"
            )

            with self.assertRaises(RepositoryPrivacyFinding) as raised:
                inspect_repository_contents(root, _repository_inspector(frozenset()))

        self.assertEqual("FIXTURE_UNINDEXED_DOCUMENT", raised.exception.code)

    def test_commit_message_body_is_inspected_without_author_header_false_positive(self) -> None:
        canary = "123" + "-" + "45" + "-" + "6789"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repository(root)
            (root / "README.md").write_text("second synthetic revision\n", encoding="utf-8")
            self._git(root, "add", "README.md")
            self._git(root, "commit", "-m", f"synthetic message {canary}")

            with self.assertRaises(RepositoryPrivacyFinding) as raised:
                inspect_repository_contents(root, _repository_inspector(frozenset()))

        self.assertEqual("FIXTURE_PRIVACY_PATTERN", raised.exception.code)
        self.assertNotIn(canary, str(raised.exception))

    def test_annotated_tag_message_body_is_inspected(self) -> None:
        canary = "123" + "-" + "45" + "-" + "6789"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repository(root)
            self._git(root, "tag", "-a", "synthetic-review", "-m", f"synthetic tag {canary}")

            with self.assertRaises(RepositoryPrivacyFinding) as raised:
                inspect_repository_contents(root, _repository_inspector(frozenset()))

        self.assertEqual("FIXTURE_PRIVACY_PATTERN", raised.exception.code)
        self.assertNotIn(canary, str(raised.exception))

    def test_custom_commit_header_is_inspected(self) -> None:
        canary = "123" + "-" + "45" + "-" + "6789"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repository(root)
            tree = self._git_output(root, "write-tree")
            parent = self._git_output(root, "rev-parse", "HEAD")
            identity = b"PWS Synthetic Quality <quality@example.invalid> 0 +0000"
            commit = b"\n".join(
                (
                    b"tree " + tree,
                    b"parent " + parent,
                    b"author " + identity,
                    b"committer " + identity,
                    b"x-audit " + canary.encode("ascii"),
                    b"",
                    b"synthetic custom-header regression",
                    b"",
                )
            )
            object_id = self._git_output(
                root,
                "hash-object",
                "-t",
                "commit",
                "-w",
                "--stdin",
                input_bytes=commit,
            )
            self._git(root, "update-ref", "refs/heads/custom-header", object_id.decode("ascii"))

            with self.assertRaises(RepositoryPrivacyFinding) as raised:
                inspect_repository_contents(root, _repository_inspector(frozenset()))

        self.assertEqual("FIXTURE_PRIVACY_PATTERN", raised.exception.code)
        self.assertNotIn(canary, str(raised.exception))

    def test_duplicate_identity_header_cannot_bypass_inspection(self) -> None:
        canary = "123" + "-" + "45" + "-" + "6789"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repository(root)
            tree = self._git_output(root, "write-tree")
            parent = self._git_output(root, "rev-parse", "HEAD")
            identity = b"PWS Synthetic Quality <quality@example.invalid> 0 +0000"
            commit = b"\n".join(
                (
                    b"tree " + tree,
                    b"parent " + parent,
                    b"author " + identity,
                    b"author " + canary.encode("ascii") + b" <quality@example.invalid> 0 +0000",
                    b"committer " + identity,
                    b"",
                    b"synthetic duplicate-identity regression",
                    b"",
                )
            )
            object_id = self._git_output(
                root,
                "hash-object",
                "--literally",
                "-t",
                "commit",
                "-w",
                "--stdin",
                input_bytes=commit,
            )
            self._git(root, "update-ref", "refs/heads/duplicate-author", object_id.decode("ascii"))

            with self.assertRaises(RepositoryPrivacyFinding) as raised:
                inspect_repository_contents(root, _repository_inspector(frozenset()))

        self.assertEqual("FIXTURE_PRIVACY_PATTERN", raised.exception.code)
        self.assertNotIn(canary, str(raised.exception))

    def test_duplicate_tagger_header_cannot_bypass_inspection(self) -> None:
        canary = "123" + "-" + "45" + "-" + "6789"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repository(root)
            target = self._git_output(root, "rev-parse", "HEAD")
            identity = b"PWS Synthetic Quality <quality@example.invalid> 0 +0000"
            tag = b"\n".join(
                (
                    b"object " + target,
                    b"type commit",
                    b"tag synthetic-duplicate",
                    b"tagger " + identity,
                    b"tagger " + canary.encode("ascii") + b" <quality@example.invalid> 0 +0000",
                    b"",
                    b"synthetic duplicate-tagger regression",
                    b"",
                )
            )
            object_id = self._git_output(
                root,
                "hash-object",
                "-t",
                "tag",
                "-w",
                "--stdin",
                input_bytes=tag,
            )
            self._git(root, "update-ref", "refs/tags/duplicate-tagger", object_id.decode("ascii"))

            with self.assertRaises(RepositoryPrivacyFinding) as raised:
                inspect_repository_contents(root, _repository_inspector(frozenset()))

        self.assertEqual("FIXTURE_PRIVACY_PATTERN", raised.exception.code)
        self.assertNotIn(canary, str(raised.exception))

    def test_folded_mergetag_identity_is_treated_as_git_provenance(self) -> None:
        provenance_email = b"provenance" + b"@" + b"ordinary-domain" + b".org"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._repository(root)
            tree = self._git_output(root, "write-tree")
            parent = self._git_output(root, "rev-parse", "HEAD")
            identity = b"PWS Synthetic Quality <quality@example.invalid> 0 +0000"
            commit = b"\n".join(
                (
                    b"tree " + tree,
                    b"parent " + parent,
                    b"author " + identity,
                    b"committer " + identity,
                    b"mergetag object " + parent,
                    b" type commit",
                    b" tag synthetic-review",
                    b" tagger PWS Provenance <" + provenance_email + b"> 0 +0000",
                    b" ",
                    b" synthetic embedded tag message",
                    b"",
                    b"synthetic outer message",
                    b"",
                )
            )
            object_id = self._git_output(
                root,
                "hash-object",
                "-t",
                "commit",
                "-w",
                "--stdin",
                input_bytes=commit,
            )
            self._git(root, "update-ref", "refs/heads/mergetag-case", object_id.decode("ascii"))

            summary = inspect_repository_contents(root, _repository_inspector(frozenset()))

        self.assertGreaterEqual(summary.history_message_object_count, 2)

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

    def test_regeneration_rejects_a_real_linked_directory_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = base / "repository"
            corpus = repository / CORPUS_DIRECTORY
            corpus.mkdir(parents=True)
            outside = base / "outside"
            outside.mkdir()
            sentinel = outside / "born-digital.pdf"
            sentinel.write_bytes(b"outside sentinel")
            try:
                (corpus / "pdf").symlink_to(outside, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"directory links unavailable: {type(error).__name__}")

            with self.assertRaises(FixtureGenerationError):
                write_committed_corpus(repository)

            self.assertEqual(b"outside sentinel", sentinel.read_bytes())

    def test_long_path_recipe_rejects_a_real_linked_child_without_touching_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = base / "repository"
            protected = repository / "protected"
            protected.mkdir(parents=True)
            output = base / "long-path"
            output.mkdir()
            linked_component = output / LONG_PATH_COMPONENT
            try:
                linked_component.symlink_to(protected, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"directory links unavailable: {type(error).__name__}")

            with self.assertRaises(FixtureGenerationError):
                generate_long_path_tree(
                    output,
                    repository_root=repository,
                    component_count=2,
                )

            self.assertEqual([], list(protected.iterdir()))


if __name__ == "__main__":
    unittest.main()

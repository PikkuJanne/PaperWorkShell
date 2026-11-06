from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Callable, Sequence
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tools import dev as dev_tools
from tools.dev import (
    SecretScanError,
    ToolRequirementError,
    _capture_bytes,
    _GitHistoryRoots,
    _gitleaks_executable,
    _historical_objects,
    _historical_path_entries,
    _PathBlobEntry,
    _read_scan_input,
    _repository_relative_path,
    _scan_working_path_batch,
    _SecretScanBatcher,
    _SecretScanEnumerationBudget,
    _trusted_gitleaks_config,
    _uncovered_index_scan_inputs,
    inspect_repository_contents,
    scan_repository,
)
from tools.quality_self_test import _secret_violation

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _initialize_repository(root: Path, *, ignored_directory: bool = False) -> str:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("Git is required for the security test")
    shutil.copy2(PROJECT_ROOT / ".gitleaks.toml", root)
    baseline_files = [".gitleaks.toml", "baseline.txt"]
    if ignored_directory:
        (root / ".gitignore").write_text("ignored/\n", encoding="utf-8")
        baseline_files.append(".gitignore")
    (root / "baseline.txt").write_text("synthetic baseline\n", encoding="utf-8")
    commands = (
        (git, "init"),
        (git, "add", *baseline_files),
        (
            git,
            "-c",
            "user.name=PaperWorkShell Quality",
            "-c",
            "user.email=quality@example.invalid",
            "commit",
            "-m",
            "synthetic baseline",
        ),
    )
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=root,
            check=False,
            shell=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError("temporary Git repository setup failed")
    return git


def _commit_all(git: str, root: Path, message: str) -> None:
    commands = (
        (git, "add", "--all"),
        (
            git,
            "-c",
            "user.name=PaperWorkShell Quality",
            "-c",
            "user.email=quality@example.invalid",
            "commit",
            "-m",
            message,
        ),
    )
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=root,
            check=False,
            shell=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError("temporary Git commit setup failed")


class SecretScanningSecurityTests(unittest.TestCase):
    def test_pws0506_public_corpus_token_allowlist_is_exact_and_rule_scoped(self) -> None:
        benign_token = "pws0506-corpus-v1"
        one_character_near_miss = f"{benign_token[:-1]}2"
        realistic_access_key = "".join(("AK", "IA", "Q7W8E9R0T1Y2U3I4"))

        with tempfile.TemporaryDirectory() as temporary:
            trusted_ignore = Path(temporary) / "trusted-empty-ignore"
            trusted_ignore.touch()
            shutil.copy2(PROJECT_ROOT / ".gitleaks.toml", _trusted_gitleaks_config(trusted_ignore))
            gitleaks = _gitleaks_executable()

            _scan_working_path_batch(
                gitleaks,
                trusted_ignore,
                [
                    (
                        Path("pws0506-benign.py"),
                        f'manifest_cas_token = "{benign_token}"\n'.encode("ascii"),
                        "PWS-0506 fixed public corpus token",
                    )
                ],
                category="PWS-0506 allowlist regression",
                batch_number=1,
                budget=_SecretScanEnumerationBudget(),
            )

            for batch_number, credential_like_value in enumerate(
                (one_character_near_miss, realistic_access_key), start=2
            ):
                with (
                    self.subTest(batch_number=batch_number),
                    self.assertRaises(SecretScanError) as raised,
                ):
                    _scan_working_path_batch(
                        gitleaks,
                        trusted_ignore,
                        [
                            (
                                Path(f"credential-near-miss-{batch_number}.py"),
                                f'manifest_cas_token = "{credential_like_value}"\n'.encode("ascii"),
                                "credential-like allowlist near miss",
                            )
                        ],
                        category="PWS-0506 allowlist regression",
                        batch_number=batch_number,
                        budget=_SecretScanEnumerationBudget(),
                    )

                self.assertEqual("SECRET_SCAN_FINDING", raised.exception.code)

    def test_pws0104_public_canary_allowlist_is_exact_and_rule_scoped(self) -> None:
        benign_canary = "PRIVATE-PARENT-PWS0104-CANARY"
        one_character_near_miss = f"{benign_canary[:-1]}Z"
        realistic_access_key = "".join(("AK", "IA", "Q7W8E9R0T1Y2U3I4"))

        with tempfile.TemporaryDirectory() as temporary:
            trusted_ignore = Path(temporary) / "trusted-empty-ignore"
            trusted_ignore.touch()
            shutil.copy2(PROJECT_ROOT / ".gitleaks.toml", _trusted_gitleaks_config(trusted_ignore))
            gitleaks = _gitleaks_executable()

            _scan_working_path_batch(
                gitleaks,
                trusted_ignore,
                [
                    (
                        Path("pws0104-benign.py"),
                        f'private_parent_token = "{benign_canary}"\n'.encode("ascii"),
                        "PWS-0104 fixed public canary",
                    )
                ],
                category="PWS-0104 allowlist regression",
                batch_number=1,
                budget=_SecretScanEnumerationBudget(),
            )

            for batch_number, credential_like_value in enumerate(
                (one_character_near_miss, realistic_access_key), start=2
            ):
                with (
                    self.subTest(batch_number=batch_number),
                    self.assertRaises(SecretScanError) as raised,
                ):
                    _scan_working_path_batch(
                        gitleaks,
                        trusted_ignore,
                        [
                            (
                                Path(f"credential-near-miss-{batch_number}.py"),
                                (f'private_parent_token = "{credential_like_value}"\n').encode(
                                    "ascii"
                                ),
                                "credential-like allowlist near miss",
                            )
                        ],
                        category="PWS-0104 allowlist regression",
                        batch_number=batch_number,
                        budget=_SecretScanEnumerationBudget(),
                    )

                self.assertEqual("SECRET_SCAN_FINDING", raised.exception.code)

    def test_runtime_synthetic_canary_is_detected_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside_index = root / "outside.index"
            outside_index.write_bytes(b"preserve-external-index")
            outside_worktree = root / "outside-worktree"
            outside_worktree.mkdir()
            hostile_template = root / "hostile-template" / "hooks"
            hostile_template.mkdir(parents=True)
            hook = hostile_template / "pre-commit"
            hook.write_text("#!/bin/sh\nexit 1\n", encoding="ascii")
            hook.chmod(0o700)
            overrides = {
                "GIT_DIR": str(root / "outside.git"),
                "GIT_INDEX_FILE": str(outside_index),
                "GIT_WORK_TREE": str(outside_worktree),
                "GIT_TEMPLATE_DIR": str(hostile_template.parent),
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "commit.gpgSign",
                "GIT_CONFIG_VALUE_0": "true",
            }
            with patch.dict(os.environ, overrides, clear=False):
                result = _secret_violation(root)

            self.assertEqual(b"preserve-external-index", outside_index.read_bytes())
            self.assertFalse((root / "outside.git").exists())

        self.assertTrue(result.passed)

    def test_secret_only_in_source_path_is_detected_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _initialize_repository(root)
            canary = "_".join(("PWS", "QUALITY", "CANARY")) + "_" + "J" * 32
            (root / f"{canary}.txt").write_text("synthetic clean content\n", encoding="utf-8")
            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()

            with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
                result = scan_repository(root)

        output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
        self.assertEqual(42, result, output)
        self.assertIn("SECRET_SCAN_FINDING", output)
        self.assertNotIn(canary, output)

    def test_secret_only_in_ref_name_is_detected_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git = _initialize_repository(root)
            canary = "_".join(("PWS", "QUALITY", "CANARY")) + "_" + "N" * 32
            updated = subprocess.run(
                (git, "update-ref", f"refs/heads/{canary}", "HEAD"),
                cwd=root,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, updated.returncode, updated.stderr)
            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()

            with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
                result = scan_repository(root)

        output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
        self.assertEqual(42, result, output)
        self.assertIn("SECRET_SCAN_FINDING", output)
        self.assertNotIn(canary, output)

    def test_linked_worktree_root_ref_is_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "main"
            linked = base / "linked"
            root.mkdir()
            git = _initialize_repository(root)
            added = subprocess.run(
                (git, "worktree", "add", "--detach", str(linked)),
                cwd=root,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, added.returncode, added.stderr)
            canary = "_".join(("PWS", "QUALITY", "CANARY")) + "_" + "K" * 32
            (linked / "linked-root-ref.txt").write_text(canary, encoding="ascii")
            _commit_all(git, linked, "synthetic linked-worktree violation")
            rewound = subprocess.run(
                (git, "reset", "--hard", "HEAD^"),
                cwd=linked,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, rewound.returncode, rewound.stderr)
            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()

            with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
                result = scan_repository(root)

        output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
        self.assertEqual(42, result, output)
        self.assertIn("SECRET_SCAN_FINDING", output)
        self.assertNotIn(canary, output)
        self.assertNotIn("linked-root-ref.txt", output)

    def test_repository_state_changes_fail_closed(self) -> None:
        def build_mutator(
            repository_root: Path,
            git_executable: str,
            change_state: str,
            secret: str,
        ) -> Callable[[_SecretScanBatcher], None]:
            mutated = False
            original_flush = _SecretScanBatcher.flush

            def mutate_after_flush(batcher: _SecretScanBatcher) -> None:
                nonlocal mutated
                original_flush(batcher)
                if mutated:
                    return
                changed = repository_root / "late-state.txt"
                changed.write_text(secret, encoding="ascii")
                if change_state == "index":
                    staged = subprocess.run(
                        (git_executable, "add", "late-state.txt"),
                        cwd=repository_root,
                        check=False,
                        shell=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(0, staged.returncode, staged.stderr)
                    changed.write_text("synthetic clean working bytes\n", encoding="utf-8")
                mutated = True

            return mutate_after_flush

        for state, marker in (("index", "L"), ("untracked", "M")):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                git = _initialize_repository(root)
                canary = "_".join(("PWS", "QUALITY", "CANARY")) + "_" + marker * 32
                mutate_after_flush = build_mutator(root, git, state, canary)

                captured_stdout = io.StringIO()
                captured_stderr = io.StringIO()
                with (
                    patch("tools.dev._SecretScanBatcher.flush", new=mutate_after_flush),
                    redirect_stdout(captured_stdout),
                    redirect_stderr(captured_stderr),
                ):
                    result = scan_repository(root)

                output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
                self.assertEqual(1, result, output)
                self.assertIn("SECRET_SCAN_INPUT_INVALID", output)
                self.assertNotIn(canary, output)
                self.assertNotIn("late-state.txt", output)

    def test_scan_deadline_is_checked_after_final_configuration_read(self) -> None:
        def cross_deadline_after_final_read(
            root: Path,
            relative_path: Path,
            *,
            allow_missing: bool,
            source_label: str,
            work_budget: _SecretScanEnumerationBudget | None = None,
        ) -> bytes | None:
            content = _read_scan_input(
                root,
                relative_path,
                allow_missing=allow_missing,
                source_label=source_label,
                work_budget=work_budget,
            )
            if source_label == "isolated secret-scanning configuration":
                if work_budget is None:
                    raise AssertionError("final configuration read must share the scan budget")
                work_budget.started_at = float("-inf")
            return content

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _initialize_repository(root)
            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()

            with (
                patch("tools.dev._read_scan_input", new=cross_deadline_after_final_read),
                redirect_stdout(captured_stdout),
                redirect_stderr(captured_stderr),
            ):
                result = scan_repository(root)

        output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
        self.assertEqual(1, result, output)
        self.assertIn("SECRET_SCAN_TIMEOUT", output)

    def test_oversized_history_fails_before_gitleaks_content_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git = _initialize_repository(root)
            (root / "oversized-history.bin").write_bytes(b"x" * 2048)
            _commit_all(git, root, "synthetic oversized history")
            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()

            with (
                patch("tools.dev.MAX_SECRET_SCAN_BYTES", 1024),
                patch("tools.dev._scan_mirror") as scan_mirror,
                redirect_stdout(captured_stdout),
                redirect_stderr(captured_stderr),
            ):
                result = scan_repository(root)

        output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
        self.assertEqual(1, result, output)
        self.assertIn("SECRET_SCAN_INPUT_TOO_LARGE", output)
        self.assertNotIn("oversized-history.bin", output)
        scan_mirror.assert_not_called()

    def test_oversized_configuration_fails_before_gitleaks_starts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _initialize_repository(root)
            configuration = root / ".gitleaks.toml"
            with configuration.open("ab") as stream:
                stream.write(b"\n# " + (b"x" * 2048) + b"\n")
            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()

            with (
                patch("tools.dev.MAX_SECRET_SCAN_BYTES", 1024),
                patch("tools.dev._gitleaks_executable") as gitleaks_executable,
                patch("tools.dev._scan_mirror") as scan_mirror,
                redirect_stdout(captured_stdout),
                redirect_stderr(captured_stderr),
            ):
                result = scan_repository(root)

        output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
        self.assertEqual(1, result, output)
        self.assertIn("SECRET_SCAN_INPUT_TOO_LARGE", output)
        self.assertNotIn(".gitleaks.toml", output)
        gitleaks_executable.assert_not_called()
        scan_mirror.assert_not_called()

    def test_excessive_repository_enumeration_fails_before_gitleaks_starts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _initialize_repository(root)
            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()

            with (
                patch("tools.dev.MAX_SECRET_SCAN_ITEMS", 1),
                patch("tools.dev._gitleaks_executable") as gitleaks_executable,
                redirect_stdout(captured_stdout),
                redirect_stderr(captured_stderr),
            ):
                result = scan_repository(root)

        output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
        self.assertEqual(1, result, output)
        self.assertIn("SECRET_SCAN_INPUT_TOO_LARGE", output)
        self.assertNotIn("baseline.txt", output)
        gitleaks_executable.assert_not_called()

    def test_git_metadata_capture_is_byte_bounded_and_record_budget_is_shared(self) -> None:
        self.assertEqual(10 * 60, dev_tools.MAX_SECRET_SCAN_SECONDS)

        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(SecretScanError) as oversized:
                _capture_bytes(
                    (
                        sys.executable,
                        "-c",
                        "import sys; sys.stdout.buffer.write(b'x' * 2048)",
                    ),
                    cwd=Path(temporary),
                    stdout_limit=1024,
                )

        self.assertEqual("SECRET_SCAN_INPUT_TOO_LARGE", oversized.exception.code)

        budget = _SecretScanEnumerationBudget()
        with (
            patch("tools.dev.MAX_SECRET_SCAN_ITEMS", 1),
            patch("tools.dev.MAX_SECRET_SCAN_METADATA_RECORDS", 2),
        ):
            budget.consume_records(b"duplicate\0duplicate\0", b"\0")
            with self.assertRaises(SecretScanError) as excessive_records:
                budget.consume_records(b"third\n", b"\n")

        self.assertEqual("SECRET_SCAN_INPUT_TOO_LARGE", excessive_records.exception.code)

        work_budget = _SecretScanEnumerationBudget()
        with patch("tools.dev.MAX_SECRET_SCAN_WORK_BYTES", 2):
            work_budget.consume_work(2)
            with self.assertRaises(SecretScanError) as excessive_work:
                work_budget.consume_work(1)

        self.assertEqual("SECRET_SCAN_INPUT_TOO_LARGE", excessive_work.exception.code)

        deadline_budget = _SecretScanEnumerationBudget(started_at=0)
        with (
            patch("tools.dev.MAX_SECRET_SCAN_SECONDS", 5),
            patch("tools.dev.time.monotonic", return_value=5),
            self.assertRaises(SecretScanError) as timed_out,
        ):
            deadline_budget.checkpoint()

        self.assertEqual("SECRET_SCAN_TIMEOUT", timed_out.exception.code)

        deadline_budget = _SecretScanEnumerationBudget(started_at=0)
        with (
            patch("tools.dev.MAX_SECRET_SCAN_SECONDS", 5),
            patch("tools.dev.time.monotonic", side_effect=(4.9, 5)),
            self.assertRaises(SecretScanError) as subprocess_timed_out,
        ):
            deadline_budget.subprocess_timeout(10)

        self.assertEqual("SECRET_SCAN_TIMEOUT", subprocess_timed_out.exception.code)

    def test_exact_historical_index_inputs_reuse_only_identical_physical_work(self) -> None:
        exact_path = _PathBlobEntry(Path("node_modules/exact.pfx"), "a" * 40, 10)
        historical_source = _PathBlobEntry(Path("historical-source.bin"), "b" * 40, 20)
        same_blob_new_path = _PathBlobEntry(Path("renamed.pfx"), "b" * 40, 20)
        same_path_changed_blob = _PathBlobEntry(Path("node_modules/exact.pfx"), "c" * 40, 11)
        novel_path_and_blob = _PathBlobEntry(Path("novel.txt"), "d" * 40, 30)

        uncovered_paths, uncovered_blobs = _uncovered_index_scan_inputs(
            (exact_path, historical_source),
            (exact_path, same_blob_new_path, same_path_changed_blob, novel_path_and_blob),
            (("a" * 40, 10), ("b" * 40, 20)),
            (("a" * 40, 10), ("b" * 40, 20), ("c" * 40, 11), ("d" * 40, 30)),
        )

        self.assertEqual(
            [same_blob_new_path, same_path_changed_blob, novel_path_and_blob],
            uncovered_paths,
        )
        self.assertEqual([("c" * 40, 11), ("d" * 40, 30)], uncovered_blobs)

    def test_identical_index_work_fits_item_limit_and_keeps_full_coverage_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _initialize_repository(root)
            # Two committed files: 2 refs + 2 historical paths + 2 blobs +
            # 1 commit + 2 working files = 9 distinct scan items. Counting the
            # identical index paths again made this bounded repository need 11.
            with patch("tools.dev.MAX_SECRET_SCAN_ITEMS", 10):
                inspected: list[bytes] = []
                summary = inspect_repository_contents(root, inspected.append)
                self.assertEqual(2, summary.index_blob_count)
                self.assertEqual(2, summary.tracked_count)
                self.assertIn(b"baseline.txt", inspected)
                self.assertIn(b"synthetic baseline\n", inspected)
                self.assertEqual(2, inspected.count(b"baseline.txt"))
                self.assertEqual(2, inspected.count(b"synthetic baseline\n"))
                with (
                    patch("tools.dev._gitleaks_executable", return_value="synthetic-scanner"),
                    patch("tools.dev._scan_mirror"),
                    patch("tools.dev._SecretScanBatcher.flush"),
                    redirect_stdout(io.StringIO()),
                    redirect_stderr(io.StringIO()),
                ):
                    self.assertEqual(0, scan_repository(root))

            # Unique work still fails against the same limit, before a scanner
            # can start. No input or budget category is exempted.
            with (
                patch("tools.dev.MAX_SECRET_SCAN_ITEMS", 8),
                self.assertRaises(SecretScanError) as overflow,
            ):
                inspect_repository_contents(root, lambda _content: None)
            self.assertEqual("SECRET_SCAN_INPUT_TOO_LARGE", overflow.exception.code)

    def test_index_reuse_retains_staged_rename_aliases_and_dirty_working_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git = _initialize_repository(root)
            renamed = Path("node_modules/renamed.tf")
            (root / renamed.parent).mkdir()
            moved = subprocess.run(
                (git, "mv", "baseline.txt", renamed.as_posix()),
                cwd=root,
                check=False,
                shell=False,
                capture_output=True,
            )
            self.assertEqual(0, moved.returncode)
            dirty = b"synthetic dirty working bytes\n"
            (root / renamed).write_bytes(dirty)
            mirrors: list[tuple[str, bytes]] = []

            def observe(
                _gitleaks: str,
                _trusted_ignore: Path,
                mirror_root: Path,
                _target: Path,
                _source_label: str,
                _budget: _SecretScanEnumerationBudget,
            ) -> None:
                mirrors.extend(
                    (target.name, target.read_bytes())
                    for target in mirror_root.rglob("*")
                    if target.is_file()
                )

            with (
                patch("tools.dev._gitleaks_executable", return_value="synthetic-scanner"),
                patch("tools.dev._scan_mirror", side_effect=observe),
                patch("tools.dev._SecretScanBatcher.flush"),
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(0, scan_repository(root))
            for payload in (b"synthetic baseline\n", dirty):
                self.assertIn(("pws-rule.tf", dev_tools.RAW_SCAN_SENTINEL + payload), mirrors)
            inspected: list[bytes] = []
            summary = inspect_repository_contents(root, inspected.append)
            self.assertEqual(2, summary.index_blob_count)
            self.assertIn(renamed.as_posix().encode(), inspected)
            self.assertIn(dirty, inspected)

    def test_exact_index_reuse_does_not_hide_working_mutation(self) -> None:
        for mode in ("callback", "scanner"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                _initialize_repository(root)
                mutated = False

                def mutate(_root: Path = root) -> None:
                    nonlocal mutated
                    if not mutated:
                        mutated = True
                        (_root / "baseline.txt").write_bytes(b"synthetic changed after snapshot\n")

                def inspect(_content: bytes) -> None:
                    mutate()

                with patch("tools.dev.MAX_SECRET_SCAN_ITEMS", 10):
                    if mode == "callback":
                        with self.assertRaises(SecretScanError) as changed:
                            inspect_repository_contents(root, inspect)
                        self.assertEqual("SECRET_SCAN_INPUT_INVALID", changed.exception.code)
                    else:
                        stderr = io.StringIO()
                        with (
                            patch(
                                "tools.dev._gitleaks_executable", return_value="synthetic-scanner"
                            ),
                            patch("tools.dev._scan_mirror", side_effect=lambda *_args: mutate()),
                            patch("tools.dev._SecretScanBatcher.flush"),
                            redirect_stdout(io.StringIO()),
                            redirect_stderr(stderr),
                        ):
                            self.assertEqual(1, scan_repository(root))
                        self.assertIn("SECRET_SCAN_INPUT_INVALID", stderr.getvalue())
                self.assertTrue(mutated)

    def test_completed_mirror_reuse_fits_lower_budget_and_preserves_all_source_reads(self) -> None:
        for relative_path in (Path("unchanged.bin"), Path("node_modules/gitleaks.toml.tf")):
            with self.subTest(path=relative_path), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                git = _initialize_repository(root)
                payload = b"synthetic clean bytes\n".ljust(32 * 1024, b"x")
                source = root / relative_path
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(payload)
                _commit_all(git, root, "add unchanged bounded synthetic input")
                # Raw scanning now shares the already-required Git path read.
                # Keep this mirror-specific proof tight after removing that read.
                work_limit = (7 if relative_path.suffix == ".tf" else 6) * len(payload) + 16384
                budget = _SecretScanEnumerationBudget()
                working_mirrors: list[str] = []
                source_reads = 0
                original_batch = dev_tools._scan_working_path_batch

                def observe_mirror(
                    gitleaks: str,
                    trusted_ignore: Path,
                    items: Sequence[tuple[Path, bytes, str]],
                    *,
                    category: str,
                    batch_number: int,
                    budget: _SecretScanEnumerationBudget,
                    _working_mirrors: list[str] = working_mirrors,
                    _batch_runner: Callable[..., None] = original_batch,
                ) -> None:
                    if items:
                        _working_mirrors.extend(item[0].as_posix() for item in items)
                    _batch_runner(
                        gitleaks,
                        trusted_ignore,
                        items,
                        category=category,
                        batch_number=batch_number,
                        budget=budget,
                    )

                def observe_read(
                    repository_root: Path,
                    source_path: Path,
                    *,
                    allow_missing: bool,
                    source_label: str,
                    work_budget: _SecretScanEnumerationBudget | None = None,
                    _expected_location: tuple[Path, Path] = (root, relative_path),
                ) -> bytes | None:
                    nonlocal source_reads
                    if (repository_root, source_path) == _expected_location:
                        source_reads += 1
                    return _read_scan_input(
                        repository_root,
                        source_path,
                        allow_missing=allow_missing,
                        source_label=source_label,
                        work_budget=work_budget,
                    )

                captured_stdout, captured_stderr = io.StringIO(), io.StringIO()
                with (
                    patch("tools.dev.MAX_SECRET_SCAN_WORK_BYTES", work_limit),
                    patch("tools.dev._SecretScanEnumerationBudget", return_value=budget),
                    patch("tools.dev._scan_working_path_batch", new=observe_mirror),
                    patch("tools.dev._read_scan_input", new=observe_read),
                    redirect_stdout(captured_stdout),
                    redirect_stderr(captured_stderr),
                ):
                    result = scan_repository(root)
                output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
                self.assertEqual(0, result, output)
                self.assertNotIn(relative_path.as_posix(), working_mirrors)
                self.assertEqual(3, source_reads)
                saved_work = (len(payload) + len(dev_tools.RAW_SCAN_SENTINEL)) * (
                    2 if relative_path.suffix == ".tf" else 1
                )
                self.assertLessEqual(budget.work_bytes, work_limit)
                self.assertGreater(budget.work_bytes + saved_work, work_limit)

    def test_working_mirror_reuse_requires_exact_case_sensitive_path_and_content(self) -> None:
        original = b"synthetic original bytes\n"
        for association in ("exact", "case", "path", "changed"):
            with self.subTest(association=association), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                relative_path = Path("config.tf")
                content = b"synthetic changed bytes\n" if association == "changed" else original
                (root / relative_path).write_bytes(content)
                prior_path = {
                    "case": "CONFIG.tf",
                    "path": "renamed.tf",
                }.get(association, relative_path.as_posix())
                coverage = frozenset({(prior_path, hashlib.sha256(original).digest())})
                budget = _SecretScanEnumerationBudget()
                batcher = _SecretScanBatcher("synthetic-scanner", root, root / "ignore", budget)
                with patch("tools.dev._scan_mirror") as scan_mirror:
                    count = dev_tools._scan_working_paths(
                        "synthetic-scanner",
                        root,
                        root / "ignore",
                        (relative_path,),
                        category="tracked working-tree",
                        allow_missing=False,
                        batcher=batcher,
                        expected_state=((relative_path, hashlib.sha256(content).digest()),),
                        budget=budget,
                        covered_path_inputs=coverage,
                    )
                self.assertEqual(1, count)
                self.assertEqual(0 if association == "exact" else 2, scan_mirror.call_count)
                # The original path bytes and complete raw content remain inputs,
                # even when the already completed mirror itself is reused.
                self.assertEqual(
                    {relative_path.as_posix().encode(), content},
                    {item[1] for item in batcher._items},
                )

    def test_mirror_coverage_is_published_only_after_original_and_alias_scans_pass(self) -> None:
        payload = b"synthetic completed mirror\n"
        entry = _PathBlobEntry(Path("node_modules/config.tf"), "a" * 40, len(payload))
        for fails in (None, 0, 1):
            with self.subTest(fails=fails), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                outcomes = (
                    [None, None]
                    if fails is None
                    else [
                        SecretScanError("SECRET_SCAN_FINDING", "synthetic failure")
                        if index == fails
                        else None
                        for index in range(2)
                    ]
                )
                with (
                    patch("tools.dev._read_git_blob", return_value=payload),
                    patch("tools.dev._scan_mirror", side_effect=outcomes) as scan,
                ):
                    if fails is not None:
                        with self.assertRaises(SecretScanError):
                            dev_tools._scan_path_aware_blobs(
                                "synthetic-scanner",
                                "synthetic-git",
                                root,
                                root / "ignore",
                                (entry,),
                                category="synthetic",
                                budget=_SecretScanEnumerationBudget(),
                            )
                    else:
                        coverage = dev_tools._scan_path_aware_blobs(
                            "synthetic-scanner",
                            "synthetic-git",
                            root,
                            root / "ignore",
                            (entry,),
                            category="synthetic",
                            budget=_SecretScanEnumerationBudget(),
                        )
                        self.assertEqual(
                            frozenset(
                                {(entry.relative_path.as_posix(), hashlib.sha256(payload).digest())}
                            ),
                            coverage,
                        )
                    self.assertEqual(2 if fails is None else fails + 1, scan.call_count)

    def test_completed_coverage_does_not_hide_changed_working_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relative_path = Path("changed.tf")
            expected_digest = hashlib.sha256(b"synthetic original\n").digest()
            (root / relative_path).write_bytes(b"synthetic changed\n")
            budget = _SecretScanEnumerationBudget()
            batcher = _SecretScanBatcher("synthetic-scanner", root, root / "ignore", budget)
            with (
                patch("tools.dev._scan_working_path_batch") as mirror,
                self.assertRaises(SecretScanError) as changed,
            ):
                dev_tools._scan_working_paths(
                    "synthetic-scanner",
                    root,
                    root / "ignore",
                    (relative_path,),
                    category="tracked working-tree",
                    allow_missing=False,
                    batcher=batcher,
                    expected_state=((relative_path, expected_digest),),
                    budget=budget,
                    covered_path_inputs=frozenset({(relative_path.as_posix(), expected_digest)}),
                )
            self.assertEqual("SECRET_SCAN_INPUT_INVALID", changed.exception.code)
            mirror.assert_not_called()

    def test_historical_path_recreated_as_untracked_still_gets_its_own_mirror(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git = _initialize_repository(root)
            relative_path = Path("recreated.tf")
            source = root / relative_path
            payload = b"synthetic unchanged historical bytes\n"
            source.write_bytes(payload)
            _commit_all(git, root, "add synthetic path before recreation")
            source.unlink()
            _commit_all(git, root, "remove synthetic path before recreation")
            source.write_bytes(payload)
            original_batch = dev_tools._scan_working_path_batch
            untracked_inputs: list[tuple[Path, bytes, str]] = []

            def observe(
                gitleaks: str,
                trusted_ignore: Path,
                items: Sequence[tuple[Path, bytes, str]],
                *,
                category: str,
                batch_number: int,
                budget: _SecretScanEnumerationBudget,
            ) -> None:
                if category == "untracked source":
                    untracked_inputs.extend(items)
                original_batch(
                    gitleaks,
                    trusted_ignore,
                    items,
                    category=category,
                    batch_number=batch_number,
                    budget=budget,
                )

            captured_stdout, captured_stderr = io.StringIO(), io.StringIO()
            with (
                patch("tools.dev._scan_working_path_batch", new=observe),
                redirect_stdout(captured_stdout),
                redirect_stderr(captured_stderr),
            ):
                result = scan_repository(root)
            output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
            self.assertEqual(0, result, output)
            self.assertEqual(
                [(relative_path, payload, "untracked source item 1")], untracked_inputs
            )

    def test_completed_path_coverage_respects_existing_item_limit(self) -> None:
        with (
            patch("tools.dev.MAX_SECRET_SCAN_ITEMS", 1),
            self.assertRaises(SecretScanError) as overflow,
        ):
            dev_tools._scan_path_aware_blobs(
                "synthetic-scanner",
                "synthetic-git",
                Path("."),
                Path("ignore"),
                (
                    _PathBlobEntry(Path("one.bin"), "a" * 40, 1),
                    _PathBlobEntry(Path("two.bin"), "b" * 40, 1),
                ),
                category="synthetic",
                budget=_SecretScanEnumerationBudget(),
            )
        self.assertEqual("SECRET_SCAN_INPUT_TOO_LARGE", overflow.exception.code)

    def test_aggregate_historical_work_fails_before_gitleaks_starts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git = _initialize_repository(root)
            payload = b"x" * 2048
            (root / "first.bin").write_bytes(payload)
            (root / "second.bin").write_bytes(payload)
            _commit_all(git, root, "synthetic repeated historical payload")
            removed = subprocess.run(
                (git, "rm", "first.bin", "second.bin"),
                cwd=root,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, removed.returncode, removed.stderr)
            _commit_all(git, root, "synthetic clean aggregate-work tip")
            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()

            with (
                patch("tools.dev.MAX_SECRET_SCAN_WORK_BYTES", 8 * 1024),
                patch("tools.dev._gitleaks_executable") as gitleaks_executable,
                redirect_stdout(captured_stdout),
                redirect_stderr(captured_stderr),
            ):
                result = scan_repository(root)

        output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
        self.assertEqual(1, result, output)
        self.assertIn("SECRET_SCAN_INPUT_TOO_LARGE", output)
        self.assertNotIn("first.bin", output)
        self.assertNotIn("second.bin", output)
        gitleaks_executable.assert_not_called()

    def test_historical_scan_fits_actual_work_budget_without_losing_findings(self) -> None:
        canary = "_".join(("PWS", "QUALITY", "CANARY")) + "_" + "B" * 32
        assignment = "".join(("pass", "word = ", '"', "Ab1cD2e3", '"'))
        cases = (
            (Path("historical.bin"), 184 * 1024, canary),
            (Path("node_modules/gitleaks.toml.tf"), 192 * 1024, assignment),
        )
        for relative_path, work_limit, secret_text in cases:
            for contains_secret in (False, True):
                with (
                    self.subTest(path=relative_path, contains_secret=contains_secret),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    root = Path(temporary)
                    git = _initialize_repository(root)
                    source = root / relative_path
                    source.parent.mkdir(parents=True, exist_ok=True)
                    prefix = secret_text.encode("ascii") if contains_secret else b"synthetic clean"
                    source.write_bytes(prefix + b"\n" + b"x" * (32 * 1024 - len(prefix) - 1))
                    _commit_all(git, root, "add bounded synthetic historical input")
                    source.unlink()
                    _commit_all(git, root, "remove bounded synthetic historical input")
                    captured_stdout = io.StringIO()
                    captured_stderr = io.StringIO()

                    with (
                        patch("tools.dev.MAX_SECRET_SCAN_WORK_BYTES", work_limit),
                        redirect_stdout(captured_stdout),
                        redirect_stderr(captured_stderr),
                    ):
                        result = scan_repository(root)

                output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
                self.assertEqual(42 if contains_secret else 0, result, output)
                if contains_secret:
                    self.assertIn("SECRET_SCAN_FINDING", output)
                self.assertNotIn("SECRET_SCAN_INPUT_TOO_LARGE", output)
                self.assertNotIn(secret_text, output)
                self.assertNotIn(relative_path.name, output)

    def test_actual_historical_work_over_budget_still_fails_before_scanning(self) -> None:
        for relative_path in (Path("historical.bin"), Path("node_modules/gitleaks.toml.tf")):
            with self.subTest(path=relative_path), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                git = _initialize_repository(root)
                source = root / relative_path
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(b"x" * (32 * 1024))
                _commit_all(git, root, "add excessive synthetic historical input")
                source.unlink()
                _commit_all(git, root, "remove excessive synthetic historical input")
                captured_stdout = io.StringIO()
                captured_stderr = io.StringIO()

                with (
                    patch("tools.dev.MAX_SECRET_SCAN_WORK_BYTES", 96 * 1024),
                    patch("tools.dev._gitleaks_executable") as gitleaks_executable,
                    redirect_stdout(captured_stdout),
                    redirect_stderr(captured_stderr),
                ):
                    result = scan_repository(root)

            output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
            self.assertEqual(1, result, output)
            self.assertIn("SECRET_SCAN_INPUT_TOO_LARGE", output)
            self.assertNotIn(relative_path.name, output)
            gitleaks_executable.assert_not_called()

    def test_path_aware_scan_charges_empty_framing_and_repeated_associations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git = _initialize_repository(root)
            payload = b"synthetic shared bytes\n"
            cases = (
                (Path("empty.bin"), b""),
                (Path("empty.tf"), b""),
                (Path("shared.bin"), payload),
                (Path("shared.tf"), payload),
            )
            for relative_path, content in cases:
                (root / relative_path).write_bytes(content)
            _commit_all(git, root, "add synthetic repeated associations and empty objects")
            entries = dev_tools._index_entries(git, root, _SecretScanEnumerationBudget())
            selected_paths = {relative_path for relative_path, _ in cases}
            selected_entries = [entry for entry in entries if entry.relative_path in selected_paths]
            self.assertEqual(4, len(selected_entries))
            self.assertEqual(2, len({entry.object_id for entry in selected_entries}))
            trusted_ignore = root / "trusted-empty-ignore"
            trusted_ignore.touch()
            shutil.copy2(PROJECT_ROOT / ".gitleaks.toml", _trusted_gitleaks_config(trusted_ignore))
            budget = _SecretScanEnumerationBudget()

            dev_tools._scan_path_aware_blobs(
                _gitleaks_executable(),
                git,
                root,
                trusted_ignore,
                selected_entries,
                category="synthetic associations",
                budget=budget,
            )

        # Both empty associations retain framing; the shared blob is read once
        # in this bounded batch, and both aliases retain their own mirror.
        self.assertEqual(4 * len(payload) + 6 * len(dev_tools.RAW_SCAN_SENTINEL), budget.work_bytes)

    def test_complete_scan_reuses_path_read_for_raw_objects_without_losing_associations(
        self,
    ) -> None:
        payload = b"synthetic shared complete blob\n"
        direct_payload = b"synthetic blob reachable only through its direct ref\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git = _initialize_repository(root)
            for name in ("shared.bin", "shared.tf"):
                (root / name).write_bytes(payload)
            _commit_all(git, root, "synthetic shared path associations")
            direct = subprocess.run(
                (git, "hash-object", "-w", "--stdin"),
                cwd=root,
                input=direct_payload,
                check=False,
                shell=False,
                capture_output=True,
            )
            self.assertEqual(0, direct.returncode)
            direct_id = direct.stdout.decode("ascii").strip()
            referenced = subprocess.run(
                (git, "update-ref", "refs/pws/direct-blob", direct_id),
                cwd=root,
                check=False,
                shell=False,
                capture_output=True,
            )
            self.assertEqual(0, referenced.returncode)
            shared_id = hashlib.sha1(
                b"blob " + str(len(payload)).encode("ascii") + b"\0" + payload,
                usedforsecurity=False,
            ).hexdigest()
            mirror_paths: list[str] = []
            original_mirror = dev_tools._scan_mirror

            def observe_mirror(
                gitleaks: str,
                trusted_ignore: Path,
                mirror_root: Path,
                target: Path,
                source_label: str,
                budget: _SecretScanEnumerationBudget,
            ) -> None:
                for path in mirror_root.rglob("*"):
                    if (
                        path.is_file()
                        and path.read_bytes() == dev_tools.RAW_SCAN_SENTINEL + payload
                    ):
                        mirror_paths.append(path.relative_to(mirror_root).as_posix())
                original_mirror(gitleaks, trusted_ignore, mirror_root, target, source_label, budget)

            captured_stdout, captured_stderr = io.StringIO(), io.StringIO()
            with (
                patch("tools.dev._read_git_blob", wraps=dev_tools._read_git_blob) as reads,
                patch("tools.dev._capture_bytes", wraps=dev_tools._capture_bytes) as captures,
                patch("tools.dev._scan_mirror", new=observe_mirror),
                redirect_stdout(captured_stdout),
                redirect_stderr(captured_stderr),
            ):
                result = scan_repository(root)
            output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
            self.assertEqual(0, result, output)
            read_ids = [call.args[2] for call in reads.call_args_list]
            # Every original path and the .tf alias still get their own completed
            # mirror. Their existing read supplies the complete raw input too.
            self.assertEqual(1, read_ids.count(shared_id))
            self.assertEqual(1, read_ids.count(direct_id))
            raw_payloads = [
                call.kwargs["input_bytes"]
                for call in captures.call_args_list
                if "stdin" in call.args[0]
            ]
            self.assertEqual(1, sum(content.count(payload) for content in raw_payloads))
            self.assertEqual(1, sum(content.count(direct_payload) for content in raw_payloads))
            self.assertEqual(3, len(mirror_paths))
            self.assertEqual(
                {"shared.bin", "shared.tf", "pws-rule.tf"},
                {Path(path).name for path in mirror_paths},
            )

    def test_shared_path_reads_stay_batch_bounded_and_charge_all_mirrors(self) -> None:
        payload = b"synthetic batch-bounded shared bytes\n".ljust(4096, b"x")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git = _initialize_repository(root)
            for name in ("shared.bin", "shared.tf"):
                (root / name).write_bytes(payload)
            _commit_all(git, root, "synthetic bounded same-object path reads")
            entries = [
                entry
                for entry in dev_tools._index_entries(git, root, _SecretScanEnumerationBudget())
                if entry.relative_path.name in {"shared.bin", "shared.tf"}
            ]
            sentinel_bytes = len(dev_tools.RAW_SCAN_SENTINEL)
            for single_batch in (True, False):
                expected_reads = 1 if single_batch else 2
                batch_limit = (sentinel_bytes + len(payload)) * (2 if single_batch else 1)
                # Actual reads, every original/alias write, and the one deduplicated
                # raw frame all count. The unchanged batcher retains the raw bytes.
                expected_work = (expected_reads + 4) * len(payload) + 4 * sentinel_bytes + 1
                for work_limit in (expected_work, expected_work - 1):
                    with self.subTest(single_batch=single_batch, work_limit=work_limit):
                        budget = _SecretScanEnumerationBudget()
                        batcher = _SecretScanBatcher(
                            "synthetic-scanner", root, root / "ignore", budget
                        )
                        mirrored: list[tuple[str, bytes]] = []

                        def observe(
                            _gitleaks: str,
                            _ignore: Path,
                            mirror_root: Path,
                            _target: Path,
                            _label: str,
                            _budget: _SecretScanEnumerationBudget,
                            _mirrored: list[tuple[str, bytes]] = mirrored,
                        ) -> None:
                            _mirrored.extend(
                                (path.name, path.read_bytes())
                                for path in mirror_root.rglob("*")
                                if path.is_file()
                            )

                        with (
                            patch("tools.dev.MAX_SECRET_SCAN_BATCH_BYTES", batch_limit),
                            patch("tools.dev.MAX_SECRET_SCAN_WORK_BYTES", work_limit),
                            patch(
                                "tools.dev._read_git_blob", wraps=dev_tools._read_git_blob
                            ) as reads,
                            patch("tools.dev._scan_mirror", new=observe),
                        ):
                            self.assertEqual(
                                expected_reads * len(payload),
                                dev_tools._path_blob_read_work(entries),
                            )
                            if work_limit < expected_work:
                                with self.assertRaises(SecretScanError) as overflow:
                                    dev_tools._scan_path_aware_blobs(
                                        "synthetic-scanner",
                                        git,
                                        root,
                                        root / "ignore",
                                        entries,
                                        category="synthetic",
                                        budget=budget,
                                        raw_batcher=batcher,
                                    )
                                self.assertEqual(
                                    "SECRET_SCAN_INPUT_TOO_LARGE", overflow.exception.code
                                )
                                continue
                            coverage = dev_tools._scan_path_aware_blobs(
                                "synthetic-scanner",
                                git,
                                root,
                                root / "ignore",
                                entries,
                                category="synthetic",
                                budget=budget,
                                raw_batcher=batcher,
                            )
                        self.assertEqual(expected_reads, reads.call_count)
                        self.assertEqual(expected_work, budget.work_bytes)
                        self.assertEqual(2, len(coverage))
                        self.assertEqual(
                            {"shared.bin", "shared.tf", "pws-rule.tf"},
                            {name for name, _ in mirrored},
                        )
                        self.assertEqual(3, len(mirrored))
                        self.assertTrue(
                            all(
                                content == dev_tools.RAW_SCAN_SENTINEL + payload
                                for _, content in mirrored
                            )
                        )
                        self.assertEqual([payload], [content for _, content in batcher._items])

    def test_combined_preflight_and_git_work_fails_before_gitleaks_starts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _initialize_repository(root)
            (root / "bounded-untracked.bin").write_bytes(b"x" * (26 * 1024))
            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()

            with (
                patch("tools.dev.MAX_SECRET_SCAN_WORK_BYTES", 30 * 1024),
                patch(
                    "tools.dev._gitleaks_executable",
                    side_effect=AssertionError("Gitleaks must not start"),
                ) as gitleaks_executable,
                redirect_stdout(captured_stdout),
                redirect_stderr(captured_stderr),
            ):
                result = scan_repository(root)

        output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
        self.assertEqual(1, result, output)
        self.assertIn("SECRET_SCAN_INPUT_TOO_LARGE", output)
        self.assertNotIn("bounded-untracked.bin", output)
        gitleaks_executable.assert_not_called()

    def test_long_known_paths_are_forecast_before_gitleaks_starts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git = _initialize_repository(root)
            historical_paths = [root / f"{index:02d}-{'x' * 180}.bin" for index in range(20)]
            for historical_path in historical_paths:
                historical_path.write_bytes(b"")
            _commit_all(git, root, "synthetic long zero-byte historical paths")
            for historical_path in historical_paths:
                historical_path.unlink()
            _commit_all(git, root, "synthetic clean long-path tip")
            for index in range(10):
                (root / f"current-{index:02d}-{'y' * 175}.bin").write_bytes(b"")
            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()

            with (
                patch("tools.dev.MAX_SECRET_SCAN_WORK_BYTES", 11 * 1024),
                patch(
                    "tools.dev._gitleaks_executable",
                    side_effect=AssertionError("Gitleaks must not start"),
                ) as gitleaks_executable,
                redirect_stdout(captured_stdout),
                redirect_stderr(captured_stderr),
            ):
                result = scan_repository(root)

        output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
        self.assertEqual(1, result, output)
        self.assertIn("SECRET_SCAN_INPUT_TOO_LARGE", output)
        self.assertNotIn("00-", output)
        gitleaks_executable.assert_not_called()

    def test_bounded_capture_closes_descendant_pipe_and_rejects_truncated_input(self) -> None:
        descendant = (
            "import subprocess, sys; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(6)'], "
            "stdout=sys.stdout, stderr=subprocess.DEVNULL)"
        )
        started = time.monotonic()
        with tempfile.TemporaryDirectory() as temporary:
            completed = _capture_bytes(
                (sys.executable, "-c", descendant),
                cwd=Path(temporary),
                stdout_limit=10,
                timeout_seconds=2,
            )
        elapsed = time.monotonic() - started

        self.assertEqual(0, completed.returncode)
        self.assertLess(elapsed, 4.5)

        with self.assertRaises(SecretScanError) as truncated:
            _capture_bytes(
                (sys.executable, "-c", "raise SystemExit(0)"),
                cwd=PROJECT_ROOT,
                input_bytes=b"x" * (2 * 1024 * 1024),
                stdout_limit=10,
                timeout_seconds=2,
            )

        self.assertEqual("SECRET_SCAN_TOOL_FAILED", truncated.exception.code)

    def test_bounded_capture_timeout_releases_temporary_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(SecretScanError) as timed_out:
                _capture_bytes(
                    (sys.executable, "-c", "import time; time.sleep(5)"),
                    cwd=Path(temporary),
                    stdout_limit=10,
                    timeout_seconds=0.05,
                )

        self.assertEqual("SECRET_SCAN_TOOL_FAILED", timed_out.exception.code)

    @unittest.skipUnless(os.name == "nt", "Windows Job cleanup test")
    def test_windows_job_cleanup_failure_is_sanitized_and_fails_closed(self) -> None:
        terminate_job = dev_tools._terminate_and_close_windows_job

        def report_failure_after_cleanup(job_handle: int) -> None:
            terminate_job(job_handle)
            raise OSError("synthetic private cleanup detail")

        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch(
                    "tools.dev._terminate_and_close_windows_job",
                    side_effect=report_failure_after_cleanup,
                ),
                self.assertRaises(SecretScanError) as cleanup_failed,
            ):
                _capture_bytes(
                    (sys.executable, "-c", "raise SystemExit(0)"),
                    cwd=Path(temporary),
                    stdout_limit=10,
                    timeout_seconds=2,
                )

        self.assertEqual("SECRET_SCAN_TOOL_FAILED", cleanup_failed.exception.code)
        self.assertNotIn("synthetic", str(cleanup_failed.exception))

    def test_commit_and_nested_annotated_tag_messages_are_scanned(self) -> None:
        for location, marker in (("commit", "E"), ("nested annotated tag", "F")):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                git = _initialize_repository(root)
                canary = "_".join(("PWS", "QUALITY", "CANARY")) + "_" + marker * 32
                identity = (
                    "-c",
                    "user.name=PaperWorkShell Quality",
                    "-c",
                    "user.email=quality@example.invalid",
                )
                if location == "commit":
                    command = (git, *identity, "commit", "--allow-empty", "-m", canary)
                    completed = subprocess.run(
                        command,
                        cwd=root,
                        check=False,
                        shell=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(0, completed.returncode, completed.stderr)
                else:
                    inner = subprocess.run(
                        (git, *identity, "tag", "-a", "synthetic-inner", "-m", canary),
                        cwd=root,
                        check=False,
                        shell=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(0, inner.returncode, inner.stderr)
                    inner_id = subprocess.run(
                        (git, "rev-parse", "refs/tags/synthetic-inner"),
                        cwd=root,
                        check=False,
                        shell=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(0, inner_id.returncode, inner_id.stderr)
                    outer = subprocess.run(
                        (
                            git,
                            *identity,
                            "tag",
                            "-a",
                            "synthetic-outer",
                            "-m",
                            "synthetic outer tag",
                            inner_id.stdout.strip(),
                        ),
                        cwd=root,
                        check=False,
                        shell=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(0, outer.returncode, outer.stderr)
                    deleted = subprocess.run(
                        (git, "tag", "--delete", "synthetic-inner"),
                        cwd=root,
                        check=False,
                        shell=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(0, deleted.returncode, deleted.stderr)

                captured_stdout = io.StringIO()
                captured_stderr = io.StringIO()
                with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
                    result = scan_repository(root)

                output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
                self.assertEqual(42, result, output)
                self.assertIn("SECRET_SCAN_FINDING", output)
                self.assertNotIn(canary, output)
                self.assertNotIn("synthetic-inner", output)

    def test_mismatched_gitleaks_version_fails_closed(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["gitleaks", "version"],
            returncode=0,
            stdout=b"0.0.0\n",
            stderr=b"",
        )
        with (
            patch("tools.dev.shutil.which", return_value="gitleaks"),
            patch("tools.dev._capture_bytes", return_value=completed) as capture,
            self.assertRaisesRegex(ToolRequirementError, "^gitleaks$"),
        ):
            _gitleaks_executable()

        self.assertEqual(4096, capture.call_args.kwargs["stdout_limit"])
        self.assertEqual(10, capture.call_args.kwargs["timeout_seconds"])

    def test_oversized_input_fails_closed_before_scanning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "synthetic.bin").write_bytes(b"xx")
            with (
                patch("tools.dev.MAX_SECRET_SCAN_BYTES", 1),
                self.assertRaises(SecretScanError) as raised,
            ):
                _read_scan_input(
                    root,
                    Path("synthetic.bin"),
                    allow_missing=False,
                    source_label="synthetic item",
                )

        self.assertEqual("SECRET_SCAN_INPUT_TOO_LARGE", raised.exception.code)

    def test_non_bijective_windows_paths_fail_closed(self) -> None:
        invalid_paths = (
            b"base:config.tf",
            b"con.txt",
            b".git/synthetic.pfx",
            b"git~1/synthetic.pfx",
            b"trailing.",
            b"trailing ",
            b"bad<name.txt",
            b"control\x01name.txt",
            b"literal\\backslash.txt",
        )

        for encoded_path in invalid_paths:
            with (
                self.subTest(encoded_path=encoded_path),
                self.assertRaises(SecretScanError) as raised,
            ):
                _repository_relative_path(encoded_path, "synthetic source path")

            self.assertEqual("SECRET_SCAN_INPUT_INVALID", raised.exception.code)
            self.assertNotIn(encoded_path.decode("ascii"), raised.exception.message)

    def test_source_controlled_ignore_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _initialize_repository(root)
            (root / ".gitleaksignore").write_text("synthetic exclusion\n", encoding="utf-8")
            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()

            with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
                result = scan_repository(root)

        output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
        self.assertEqual(1, result, output)
        self.assertIn("SECRET_SCAN_POLICY_INVALID", output)
        self.assertNotIn(".gitleaksignore", output)

    def test_inline_allow_annotation_cannot_suppress_a_finding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _initialize_repository(root)
            canary = "_".join(("PWS", "QUALITY", "CANARY")) + "_" + "B" * 32
            annotation = "gitleaks" + ":allow"
            (root / "synthetic-source.txt").write_text(
                f"{canary}  # {annotation}\n",
                encoding="utf-8",
            )
            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()

            with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
                result = scan_repository(root)

        output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
        self.assertEqual(42, result, output)
        self.assertIn("SECRET_SCAN_FINDING", output)
        self.assertNotIn(canary, output)
        self.assertNotIn("synthetic-source.txt", output)

    def test_ignored_state_is_excluded_and_deleted_tracked_file_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _initialize_repository(root, ignored_directory=True)
            baseline = root / "baseline.txt"
            baseline.unlink()
            ignored = root / "ignored"
            ignored.mkdir()
            canary = "_".join(("PWS", "QUALITY", "CANARY")) + "_" + "A" * 32
            (ignored / "private-state.txt").write_text(canary, encoding="utf-8")
            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()

            with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
                result = scan_repository(root)

        output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
        self.assertEqual(0, result, output)
        self.assertNotIn(canary, output)

    def test_path_constrained_rule_covers_untracked_and_staged_binary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git = _initialize_repository(root)
            path_constrained_bytes = b"\x30\x82\x00\x04\x00\xffsynthetic"
            (root / "historical-source.bin").write_bytes(path_constrained_bytes)
            _commit_all(git, root, "add synthetic historical raw input")
            path_constrained = root / "node_modules" / "synthetic.pfx"
            path_constrained.parent.mkdir()
            path_constrained.write_bytes(path_constrained_bytes)

            for state in ("untracked", "staged"):
                with self.subTest(state=state):
                    if state == "staged":
                        completed = subprocess.run(
                            (git, "add", "node_modules/synthetic.pfx"),
                            cwd=root,
                            check=False,
                            shell=False,
                            capture_output=True,
                            text=True,
                        )
                        self.assertEqual(0, completed.returncode, completed.stderr)
                    captured_stdout = io.StringIO()
                    captured_stderr = io.StringIO()
                    with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
                        result = scan_repository(root)
                    output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"

                    self.assertEqual(42, result, output)
                    self.assertIn("SECRET_SCAN_FINDING", output)
                    self.assertNotIn("synthetic.pfx", output)

            committed = subprocess.run(
                (
                    git,
                    "-c",
                    "user.name=PaperWorkShell Quality",
                    "-c",
                    "user.email=quality@example.invalid",
                    "commit",
                    "-m",
                    "synthetic path-constrained file",
                ),
                cwd=root,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, committed.returncode, committed.stderr)
            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()
            with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
                result = scan_repository(root)
            output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"

            self.assertEqual(42, result, output)
            self.assertIn("SECRET_SCAN_FINDING", output)
            self.assertNotIn("synthetic.pfx", output)

            history_commands = (
                (git, "rm", "node_modules/synthetic.pfx"),
                (
                    git,
                    "-c",
                    "user.name=PaperWorkShell Quality",
                    "-c",
                    "user.email=quality@example.invalid",
                    "commit",
                    "-m",
                    "remove synthetic path-constrained file",
                ),
            )
            for command in history_commands:
                completed = subprocess.run(
                    command,
                    cwd=root,
                    check=False,
                    shell=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, completed.returncode, completed.stderr)
            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()
            with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
                result = scan_repository(root)
            output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"

            self.assertEqual(42, result, output)
            self.assertIn("SECRET_SCAN_FINDING", output)
            self.assertNotIn("synthetic.pfx", output)

    def test_shadow_covers_every_pinned_path_constrained_rule(self) -> None:
        freemius = "".join(("'secret", "_key' => '", "sk_", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4Q", "'"))
        terraform = "".join(("pass", "word = ", '"', "Ab1cD2e3", '"'))
        kubernetes = "".join(
            ("kind: Sec", "ret\ndata:\n  token: ", "YWJjZGVmZ2hpamtsbW5vcA==", "\n")
        )
        nuget = "".join(
            (
                '<add key="ClearText',
                'Password" value="',
                "Ab1cD2e3Xy9!",
                '" />',
            )
        )
        cases = (
            ("verification-metadata.xml.php", freemius.encode("ascii")),
            ("gitleaks.toml.tf", terraform.encode("ascii")),
            ("pnpm-lock.yaml", kubernetes.encode("ascii")),
            ("gitleaks.toml.nuget.config", nuget.encode("ascii")),
            ("gitleaks.toml.pfx", b"\x30\x82\x00\x04\x00\xffsynthetic"),
        )

        with tempfile.TemporaryDirectory() as temporary:
            trusted_ignore = Path(temporary) / "trusted-empty-ignore"
            trusted_ignore.touch()
            shutil.copy2(PROJECT_ROOT / ".gitleaks.toml", _trusted_gitleaks_config(trusted_ignore))
            gitleaks = _gitleaks_executable()
            for basename, content in cases:
                with (
                    self.subTest(basename=basename),
                    self.assertRaises(SecretScanError) as raised,
                ):
                    _scan_working_path_batch(
                        gitleaks,
                        trusted_ignore,
                        [(Path("node_modules") / basename, content, "synthetic item")],
                        category="synthetic",
                        batch_number=1,
                        budget=_SecretScanEnumerationBudget(),
                    )

                self.assertEqual("SECRET_SCAN_FINDING", raised.exception.code)

    def test_direct_and_auto_merge_tree_refs_retain_path_blob_associations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git = _initialize_repository(root)
            blob = subprocess.run(
                (git, "hash-object", "-w", "--stdin"),
                cwd=root,
                check=False,
                shell=False,
                input=b"\x30\x82\x00\x04\x00\xffsynthetic",
                capture_output=True,
            )
            self.assertEqual(0, blob.returncode, blob.stderr)
            blob_id = blob.stdout.decode("ascii").strip()

            for path, ref_name in (
                ("synthetic.txt", "refs/tags/tree-txt"),
                ("synthetic.pfx", "AUTO_MERGE"),
            ):
                tree = subprocess.run(
                    (git, "mktree"),
                    cwd=root,
                    check=False,
                    shell=False,
                    input=f"100644 blob {blob_id}\t{path}\n".encode("ascii"),
                    capture_output=True,
                )
                self.assertEqual(0, tree.returncode, tree.stderr)
                tree_id = tree.stdout.decode("ascii").strip()
                updated = subprocess.run(
                    (git, "update-ref", ref_name, tree_id),
                    cwd=root,
                    check=False,
                    shell=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, updated.returncode, updated.stderr)

            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()
            with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
                result = scan_repository(root)

        output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
        self.assertEqual(42, result, output)
        self.assertIn("SECRET_SCAN_FINDING", output)
        self.assertNotIn("synthetic.pfx", output)
        self.assertNotIn("synthetic.txt", output)

    def test_multiple_fetch_head_trees_retain_every_path_blob_association(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            root = temporary_root / "local"
            remote = temporary_root / "remote.git"
            root.mkdir()
            git = _initialize_repository(root)
            initialized = subprocess.run(
                (git, "init", "--bare", str(remote)),
                cwd=temporary_root,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, initialized.returncode, initialized.stderr)
            blob = subprocess.run(
                (git, "-C", str(remote), "hash-object", "-w", "--stdin"),
                check=False,
                shell=False,
                input=b"\x30\x82\x00\x04\x00\xffsynthetic",
                capture_output=True,
            )
            self.assertEqual(0, blob.returncode, blob.stderr)
            blob_id = blob.stdout.decode("ascii").strip()
            for path in ("synthetic.txt", "synthetic.pfx"):
                tree = subprocess.run(
                    (git, "-C", str(remote), "mktree"),
                    check=False,
                    shell=False,
                    input=f"100644 blob {blob_id}\t{path}\n".encode("ascii"),
                    capture_output=True,
                )
                self.assertEqual(0, tree.returncode, tree.stderr)
                updated = subprocess.run(
                    (
                        git,
                        "-C",
                        str(remote),
                        "update-ref",
                        f"refs/tags/tree-{Path(path).suffix[1:]}",
                        tree.stdout.decode("ascii").strip(),
                    ),
                    check=False,
                    shell=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, updated.returncode, updated.stderr)
            fetched = subprocess.run(
                (
                    git,
                    "fetch",
                    "--no-tags",
                    str(remote),
                    "refs/tags/tree-txt",
                    "refs/tags/tree-pfx",
                ),
                cwd=root,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, fetched.returncode, fetched.stderr)

            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()
            with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
                result = scan_repository(root)

        output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
        self.assertEqual(42, result, output)
        self.assertIn("SECRET_SCAN_FINDING", output)
        self.assertNotIn("synthetic.pfx", output)
        self.assertNotIn("synthetic.txt", output)

    def test_fetch_head_direct_blob_is_scanned_as_raw_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            root = temporary_root / "local"
            remote = temporary_root / "remote.git"
            root.mkdir()
            git = _initialize_repository(root)
            initialized = subprocess.run(
                (git, "init", "--bare", str(remote)),
                cwd=temporary_root,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, initialized.returncode, initialized.stderr)
            canary = "_".join(("PWS", "QUALITY", "CANARY")) + "_" + "C" * 32
            blob = subprocess.run(
                (git, "-C", str(remote), "hash-object", "-w", "--stdin"),
                check=False,
                shell=False,
                input=canary.encode("ascii"),
                capture_output=True,
            )
            self.assertEqual(0, blob.returncode, blob.stderr)
            updated = subprocess.run(
                (
                    git,
                    "-C",
                    str(remote),
                    "update-ref",
                    "refs/tags/blob-direct",
                    blob.stdout.decode("ascii").strip(),
                ),
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, updated.returncode, updated.stderr)
            fetched = subprocess.run(
                (git, "fetch", "--no-tags", str(remote), "refs/tags/blob-direct"),
                cwd=root,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, fetched.returncode, fetched.stderr)

            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()
            with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
                result = scan_repository(root)

        output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
        self.assertEqual(42, result, output)
        self.assertIn("SECRET_SCAN_FINDING", output)
        self.assertNotIn(canary, output)

    def test_root_commit_ref_is_scanned_after_branch_rewinds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git = _initialize_repository(root)
            path_constrained = root / "node_modules" / "synthetic.pfx"
            path_constrained.parent.mkdir()
            path_constrained.write_bytes(b"\x30\x82\x00\x04\x00\xffsynthetic")
            _commit_all(git, root, "synthetic root-ref violation")
            rewound = subprocess.run(
                (git, "reset", "--hard", "HEAD^"),
                cwd=root,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, rewound.returncode, rewound.stderr)

            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()
            with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
                result = scan_repository(root)

        output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
        self.assertEqual(42, result, output)
        self.assertIn("SECRET_SCAN_FINDING", output)
        self.assertNotIn("synthetic.pfx", output)

    def test_captured_ref_roots_survive_an_ordinary_ref_aba_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git = _initialize_repository(root)

            def git_text(*arguments: str) -> str:
                completed = subprocess.run(
                    (git, *arguments),
                    cwd=root,
                    check=False,
                    shell=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, completed.returncode, completed.stderr)
                return completed.stdout.strip()

            initial_branch = git_text("symbolic-ref", "--short", "HEAD")
            baseline = git_text("rev-parse", "HEAD")
            git_text("switch", "-c", "synthetic-aba")
            canary = "_".join(("PWS", "QUALITY", "CANARY")) + "_" + "R" * 32
            hidden_source = root / "ordinary-ref-aba.txt"
            hidden_source.write_text(canary, encoding="ascii")
            _commit_all(git, root, "synthetic ABA-hidden violation")
            git_text("rm", "ordinary-ref-aba.txt")
            _commit_all(git, root, "synthetic clean ABA tip")
            clean_tip = git_text("rev-parse", "HEAD")
            git_text("switch", initial_branch)

            original_path_traversal = _historical_path_entries
            original_object_traversal = _historical_objects

            def traverse_captured_paths(
                git_executable: str,
                repository_root: Path,
                roots: _GitHistoryRoots,
                budget: _SecretScanEnumerationBudget,
            ) -> list[_PathBlobEntry]:
                git_text("update-ref", "refs/heads/synthetic-aba", baseline)
                return original_path_traversal(
                    git_executable,
                    repository_root,
                    roots,
                    budget,
                )

            def traverse_captured_objects(
                git_executable: str,
                repository_root: Path,
                roots: _GitHistoryRoots,
                path_entries: list[_PathBlobEntry],
                budget: _SecretScanEnumerationBudget,
            ) -> tuple[list[tuple[str, int]], list[tuple[str, str, int]]]:
                try:
                    return original_object_traversal(
                        git_executable,
                        repository_root,
                        roots,
                        path_entries,
                        budget,
                    )
                finally:
                    git_text("update-ref", "refs/heads/synthetic-aba", clean_tip)

            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()
            with (
                patch("tools.dev._historical_path_entries", new=traverse_captured_paths),
                patch("tools.dev._historical_objects", new=traverse_captured_objects),
                redirect_stdout(captured_stdout),
                redirect_stderr(captured_stderr),
            ):
                result = scan_repository(root)

        output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
        self.assertEqual(42, result, output)
        self.assertIn("SECRET_SCAN_FINDING", output)
        self.assertNotIn(canary, output)
        self.assertNotIn("ordinary-ref-aba.txt", output)

    def test_replace_refs_cannot_hide_physical_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git = _initialize_repository(root)
            baseline = subprocess.run(
                (git, "rev-parse", "HEAD"),
                cwd=root,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, baseline.returncode, baseline.stderr)
            path_constrained = root / "node_modules" / "synthetic.pfx"
            path_constrained.parent.mkdir()
            path_constrained.write_bytes(b"\x30\x82\x00\x04\x00\xffsynthetic")
            _commit_all(git, root, "synthetic replace-hidden violation")
            secret_commit = subprocess.run(
                (git, "rev-parse", "HEAD"),
                cwd=root,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, secret_commit.returncode, secret_commit.stderr)
            replaced = subprocess.run(
                (
                    git,
                    "replace",
                    secret_commit.stdout.strip(),
                    baseline.stdout.strip(),
                ),
                cwd=root,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, replaced.returncode, replaced.stderr)
            cleaned = subprocess.run(
                (git, "reset", "--hard", "HEAD"),
                cwd=root,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, cleaned.returncode, cleaned.stderr)
            self.assertFalse(path_constrained.exists())

            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()
            with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
                result = scan_repository(root)

        output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
        self.assertEqual(42, result, output)
        self.assertIn("SECRET_SCAN_FINDING", output)
        self.assertNotIn("synthetic.pfx", output)

    def test_commit_graph_cannot_hide_physical_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git = _initialize_repository(root)
            baseline = subprocess.run(
                (git, "rev-parse", "HEAD"),
                cwd=root,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, baseline.returncode, baseline.stderr)

            canary = "_".join(("PWS", "QUALITY", "CANARY")) + "_" + "G" * 32
            source = root / "historical-canary.txt"
            source.write_text(canary, encoding="ascii")
            _commit_all(git, root, "synthetic commit-graph-hidden violation")
            hidden = subprocess.run(
                (git, "rev-parse", "HEAD"),
                cwd=root,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, hidden.returncode, hidden.stderr)

            removed = subprocess.run(
                (git, "rm", "historical-canary.txt"),
                cwd=root,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, removed.returncode, removed.stderr)
            _commit_all(git, root, "synthetic clean tip")
            tip = subprocess.run(
                (git, "rev-parse", "HEAD"),
                cwd=root,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, tip.returncode, tip.stderr)

            written = subprocess.run(
                (git, "-c", "core.commitGraph=true", "commit-graph", "write", "--reachable"),
                cwd=root,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, written.returncode, written.stderr)
            configured = subprocess.run(
                (git, "config", "core.commitGraph", "true"),
                cwd=root,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, configured.returncode, configured.stderr)

            commit_graph = root / ".git" / "objects" / "info" / "commit-graph"
            graph = bytearray(commit_graph.read_bytes())
            self.assertEqual(b"CGPH", graph[:4])
            self.assertEqual(1, graph[4])
            hash_details = {1: (20, "sha1"), 2: (32, "sha256")}
            self.assertIn(graph[5], hash_details)
            object_id_size, hash_name = hash_details[graph[5]]

            chunk_offsets: dict[bytes, int] = {}
            chunk_entry_offset = 8
            for _ in range(graph[6] + 1):
                chunk_name = bytes(graph[chunk_entry_offset : chunk_entry_offset + 4])
                chunk_offset = int.from_bytes(
                    graph[chunk_entry_offset + 4 : chunk_entry_offset + 12], "big"
                )
                chunk_offsets[chunk_name] = chunk_offset
                chunk_entry_offset += 12
            self.assertTrue({b"OIDF", b"OIDL", b"CDAT"} <= chunk_offsets.keys())

            fanout_offset = chunk_offsets[b"OIDF"]
            object_id_offset = chunk_offsets[b"OIDL"]
            commit_data_offset = chunk_offsets[b"CDAT"]
            commit_count = int.from_bytes(
                graph[fanout_offset + 255 * 4 : fanout_offset + 256 * 4], "big"
            )
            object_ids = [
                bytes(
                    graph[
                        object_id_offset + index * object_id_size : object_id_offset
                        + (index + 1) * object_id_size
                    ]
                )
                for index in range(commit_count)
            ]
            baseline_id = bytes.fromhex(baseline.stdout.strip())
            hidden_id = bytes.fromhex(hidden.stdout.strip())
            tip_id = bytes.fromhex(tip.stdout.strip())
            self.assertIn(baseline_id, object_ids)
            self.assertIn(hidden_id, object_ids)
            self.assertIn(tip_id, object_ids)

            record_size = object_id_size + 16
            tip_record = commit_data_offset + object_ids.index(tip_id) * record_size
            first_parent = slice(
                tip_record + object_id_size,
                tip_record + object_id_size + 4,
            )
            self.assertEqual(
                object_ids.index(hidden_id), int.from_bytes(graph[first_parent], "big")
            )
            graph[first_parent] = object_ids.index(baseline_id).to_bytes(4, "big")
            graph[-object_id_size:] = hashlib.new(hash_name, graph[:-object_id_size]).digest()
            commit_graph.chmod(0o600)
            commit_graph.write_bytes(graph)

            verified = subprocess.run(
                (git, "-c", "core.commitGraph=true", "commit-graph", "verify"),
                cwd=root,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, verified.returncode)
            graph_traversal = subprocess.run(
                (git, "-c", "core.commitGraph=true", "log", "--format=%H", "--all"),
                cwd=root,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, graph_traversal.returncode, graph_traversal.stderr)
            self.assertNotIn(hidden.stdout.strip(), graph_traversal.stdout.splitlines())
            physical_traversal = subprocess.run(
                (git, "-c", "core.commitGraph=false", "log", "--format=%H", "--all"),
                cwd=root,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, physical_traversal.returncode, physical_traversal.stderr)
            self.assertIn(hidden.stdout.strip(), physical_traversal.stdout.splitlines())

            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()
            with (
                patch.dict(os.environ, {"GIT_TEST_COMMIT_GRAPH": "1"}, clear=False),
                redirect_stdout(captured_stdout),
                redirect_stderr(captured_stderr),
            ):
                result = scan_repository(root)

        output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
        self.assertEqual(42, result, output)
        self.assertIn("SECRET_SCAN_FINDING", output)
        self.assertNotIn(canary, output)
        self.assertNotIn("historical-canary.txt", output)

    def test_history_truncation_metadata_fails_closed(self) -> None:
        cases = (
            ("shallow", "SECRET_SCAN_HISTORY_INCOMPLETE"),
            ("grafts", "SECRET_SCAN_POLICY_INVALID"),
        )
        for state, expected_code in cases:
            with self.subTest(state=state), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                git = _initialize_repository(root)
                path_constrained = root / "node_modules" / "synthetic.pfx"
                path_constrained.parent.mkdir()
                path_constrained.write_bytes(b"\x30\x82\x00\x04\x00\xffsynthetic")
                _commit_all(git, root, "synthetic truncated ancestor")
                removed = subprocess.run(
                    (git, "rm", "node_modules/synthetic.pfx"),
                    cwd=root,
                    check=False,
                    shell=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, removed.returncode, removed.stderr)
                _commit_all(git, root, "synthetic clean tip")
                tip = subprocess.run(
                    (git, "rev-parse", "HEAD"),
                    cwd=root,
                    check=False,
                    shell=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, tip.returncode, tip.stderr)
                if state == "shallow":
                    control_path = root / ".git" / "shallow"
                else:
                    control_path = root / ".git" / "info" / "grafts"
                control_path.write_text(f"{tip.stdout.strip()}\n", encoding="ascii")
                captured_stdout = io.StringIO()
                captured_stderr = io.StringIO()
                with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
                    result = scan_repository(root)

            output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
            self.assertEqual(1, result, output)
            self.assertIn(expected_code, output)
            self.assertNotIn("synthetic.pfx", output)

    def test_local_config_cannot_redirect_the_requested_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            root = temporary_root / "requested-root"
            redirected = temporary_root / "redirected-worktree"
            root.mkdir()
            redirected.mkdir()
            git = _initialize_repository(root)
            canary = "_".join(("PWS", "QUALITY", "CANARY")) + "_" + "D" * 32
            (root / "untracked-source.txt").write_text(canary, encoding="ascii")
            configured = subprocess.run(
                (git, "config", "core.worktree", str(redirected)),
                cwd=root,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, configured.returncode, configured.stderr)
            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()
            with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
                result = scan_repository(root)

        output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
        self.assertEqual(1, result, output)
        self.assertIn("SECRET_SCAN_REPOSITORY_MISMATCH", output)
        self.assertNotIn(canary, output)
        self.assertNotIn("untracked-source.txt", output)

    def test_reference_backend_environment_cannot_hide_physical_refs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            root = temporary_root / "repository"
            empty_refstore = temporary_root / "empty-refstore"
            root.mkdir()
            empty_refstore.mkdir()
            git = _initialize_repository(root)
            path_constrained = root / "node_modules" / "synthetic.pfx"
            path_constrained.parent.mkdir()
            path_constrained.write_bytes(b"\x30\x82\x00\x04\x00\xffsynthetic")
            _commit_all(git, root, "synthetic physical ancestor")
            removed = subprocess.run(
                (git, "rm", "node_modules/synthetic.pfx"),
                cwd=root,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, removed.returncode, removed.stderr)
            _commit_all(git, root, "synthetic clean tip")
            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()
            with (
                patch.dict(
                    os.environ,
                    {"GIT_REFERENCE_BACKEND": f"files://{empty_refstore.as_posix()}"},
                    clear=False,
                ),
                redirect_stdout(captured_stdout),
                redirect_stderr(captured_stderr),
            ):
                result = scan_repository(root)

        output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
        self.assertEqual(42, result, output)
        self.assertIn("SECRET_SCAN_FINDING", output)
        self.assertNotIn("synthetic.pfx", output)

    def test_local_config_cannot_redirect_ref_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            root = temporary_root / "repository"
            empty_refstore = temporary_root / "empty-refstore"
            root.mkdir()
            empty_refstore.mkdir()
            git = _initialize_repository(root)
            path_constrained = root / "node_modules" / "synthetic.pfx"
            path_constrained.parent.mkdir()
            path_constrained.write_bytes(b"\x30\x82\x00\x04\x00\xffsynthetic")
            _commit_all(git, root, "synthetic physical ancestor")
            removed = subprocess.run(
                (git, "rm", "node_modules/synthetic.pfx"),
                cwd=root,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, removed.returncode, removed.stderr)
            _commit_all(git, root, "synthetic clean tip")
            configurations = (
                (git, "config", "core.repositoryformatversion", "1"),
                (
                    git,
                    "config",
                    "extensions.refStorage",
                    f"files://{empty_refstore.as_posix()}",
                ),
            )
            for command in configurations:
                configured = subprocess.run(
                    command,
                    cwd=root,
                    check=False,
                    shell=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, configured.returncode, configured.stderr)
            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()
            with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
                result = scan_repository(root)

        output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
        self.assertEqual(1, result, output)
        # Newer Git versions reject the unsupported extension during repository discovery before
        # the scanner can classify the value itself. Both outcomes fail closed before enumeration.
        self.assertRegex(output, r"SECRET_SCAN_(?:POLICY_INVALID|GIT_FAILED)")
        self.assertNotIn("synthetic.pfx", output)

    def test_path_and_content_rule_covers_historical_and_unstaged_binary(self) -> None:
        assignment = "".join(("pass", "word = ", '"', "Ab1cD2e3", '"'))
        secret_content = b"\x00" + assignment.encode("ascii") + b"\n"
        clean_content = b"\x00synthetic clean content\n"

        for state in ("historical-cleaned", "unstaged-tracked"):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                git = _initialize_repository(root)
                configuration = root / "config.tf"
                configuration.write_bytes(clean_content)
                _commit_all(git, root, "add clean synthetic configuration")
                if state == "historical-cleaned":
                    configuration.write_bytes(secret_content)
                    _commit_all(git, root, "add synthetic historical violation")
                    configuration.write_bytes(clean_content)
                    _commit_all(git, root, "clean synthetic historical violation")
                else:
                    configuration.write_bytes(secret_content)

                captured_stdout = io.StringIO()
                captured_stderr = io.StringIO()
                with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
                    result = scan_repository(root)
                output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"

                self.assertEqual(42, result, output)
                self.assertIn("SECRET_SCAN_FINDING", output)
                self.assertNotIn("config.tf", output)
                self.assertNotIn(assignment, output)

    def test_historical_ntfs_alternate_stream_path_fails_closed(self) -> None:
        assignment = "".join(("pass", "word = ", '"', "Ab1cD2e3", '"'))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git = _initialize_repository(root)

            def git_text(*arguments: str) -> str:
                completed = subprocess.run(
                    (git, *arguments),
                    cwd=root,
                    check=False,
                    shell=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, completed.returncode, completed.stderr)
                return completed.stdout.strip()

            baseline_commit = git_text("rev-parse", "HEAD")
            baseline_tree = git_text("rev-parse", "HEAD^{tree}")
            blob = subprocess.run(
                (git, "hash-object", "-w", "--stdin"),
                cwd=root,
                check=False,
                shell=False,
                input=b"\x00" + assignment.encode("ascii") + b"\n",
                capture_output=True,
            )
            self.assertEqual(0, blob.returncode, blob.stderr)
            blob_id = blob.stdout.decode("ascii").strip()
            tree = subprocess.run(
                (git, "mktree"),
                cwd=root,
                check=False,
                shell=False,
                input=f"100644 blob {blob_id}\tbase:config.tf\n".encode("ascii"),
                capture_output=True,
            )
            self.assertEqual(0, tree.returncode, tree.stderr)
            unsafe_tree = tree.stdout.decode("ascii").strip()
            unsafe_commit = git_text(
                "-c",
                "user.name=PaperWorkShell Quality",
                "-c",
                "user.email=quality@example.invalid",
                "commit-tree",
                unsafe_tree,
                "-p",
                baseline_commit,
                "-m",
                "synthetic unsafe-path ancestor",
            )
            clean_commit = git_text(
                "-c",
                "user.name=PaperWorkShell Quality",
                "-c",
                "user.email=quality@example.invalid",
                "commit-tree",
                baseline_tree,
                "-p",
                unsafe_commit,
                "-m",
                "synthetic clean-path tip",
            )
            git_text("update-ref", "refs/heads/synthetic-unsafe-path", clean_commit)
            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()
            with redirect_stdout(captured_stdout), redirect_stderr(captured_stderr):
                result = scan_repository(root)

        output = f"{captured_stdout.getvalue()}\n{captured_stderr.getvalue()}"
        self.assertEqual(1, result, output)
        self.assertIn("SECRET_SCAN_INPUT_INVALID", output)
        self.assertNotIn("base:config.tf", output)
        self.assertNotIn(assignment, output)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/quality.yml"


class ContinuousIntegrationWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_actions_are_pinned_to_full_commit_hashes(self) -> None:
        uses = re.findall(r"^\s*uses:\s*([^\s#]+)", self.text, flags=re.MULTILINE)
        expected = {
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
            "astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9",
        }

        self.assertEqual(expected, set(uses))
        for reference in uses:
            self.assertRegex(reference, r"^[^@]+@[0-9a-f]{40}$")

    def test_workflow_has_read_only_nonuploading_policy(self) -> None:
        lowered = self.text.casefold()

        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn("fetch-depth: 0", self.text)
        self.assertIn("enable-cache: false", self.text)
        self.assertIn("restore-cache: false", self.text)
        self.assertIn("save-cache: false", self.text)
        self.assertNotIn("pull_request_target", lowered)
        self.assertNotIn("upload-artifact", lowered)
        self.assertNotIn("actions/cache@", lowered)

    def test_ci_invokes_repository_dispatcher(self) -> None:
        self.assertIn("run: python tools/dev.py bootstrap", self.text)
        self.assertIn(
            "run: uv sync --locked --group renderer-evaluation",
            self.text,
        )
        self.assertIn("run: python tools/dev.py check", self.text)

    def test_ci_stages_copy_owned_worker_python_before_bootstrap(self) -> None:
        stage = self.text.index("- name: Stage copy-owned worker CPython")
        bootstrap = self.text.index("- name: Bootstrap locked environment")

        self.assertLess(stage, bootstrap)
        self.assertIn(
            '$source = (& python -I -S -c "import sys; print(sys.base_prefix)").Trim()',
            self.text,
        )
        self.assertIn(
            '$destination = Join-Path $env:RUNNER_TEMP "PaperWorkShell-ci-python"',
            self.text,
        )
        self.assertIn(
            "Copy-Item -LiteralPath $source -Destination $destination -Recurse -Force",
            self.text,
        )
        self.assertIn(
            '$reported = (& $python -I -S -c "import sys; print(sys.base_prefix)").Trim()',
            self.text,
        )
        self.assertIn('"UV_PYTHON=$python"', self.text)
        self.assertNotIn("Remove-Item -LiteralPath $source", self.text)

    def test_quality_job_timeout_covers_the_full_hosted_suite(self) -> None:
        timeout = re.search(
            r"^\s*timeout-minutes:\s*(\d+)\s*$",
            self.text,
            flags=re.MULTILINE,
        )

        self.assertIsNotNone(timeout)
        assert timeout is not None
        self.assertEqual(90, int(timeout.group(1)))

    def test_gitleaks_download_is_versioned_and_hash_verified(self) -> None:
        self.assertIn("gitleaks_8.30.1_windows_x64.zip", self.text)
        self.assertIn(
            "d29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e",
            self.text,
        )
        self.assertIn("Get-FileHash -Algorithm SHA256", self.text)
        self.assertEqual(2, self.text.count("$maximumAttempts = 3"))
        self.assertEqual(
            2,
            self.text.count("for ($attempt = 1; $attempt -le $maximumAttempts; $attempt++)"),
        )
        self.assertEqual(
            2,
            self.text.count("Remove-Item -LiteralPath $archive -Force"),
        )
        self.assertEqual(2, self.text.count("-ConnectionTimeoutSeconds 30"))
        self.assertEqual(2, self.text.count("-OperationTimeoutSeconds 60"))

    def test_qpdf_archive_is_verified_before_program_files_extraction(self) -> None:
        self.assertIn(
            '$archive = Join-Path $env:RUNNER_TEMP "qpdf-12.3.2-msvc64.zip"',
            self.text,
        )
        self.assertIn(
            '$destination = Join-Path $env:ProgramFiles "PaperWorkShell-ci-qpdf-12.3.2"',
            self.text,
        )
        self.assertNotIn(
            '$destination = Join-Path $env:RUNNER_TEMP "qpdf-12.3.2"',
            self.text,
        )
        self.assertIn(
            "8941870a604e7c87ed24566b038d46c24ce76616254d2383c578f60c0677f202",
            self.text,
        )
        self.assertIn(
            "43f79db620ce09529a67572a5de87aec4065b95f11ba6e5918db557f943a7eac",
            self.text,
        )
        self.assertIn(
            "623338ff5a9caab476f9e80ccc40c28c194208f4bc5d8e51eac7fca792e2e969",
            self.text,
        )
        self.assertIn(
            "d5e4d9a3e835fa679450145d6a7d94e36573a509317111904d9b3712c30d9066",
            self.text,
        )


if __name__ == "__main__":
    unittest.main()

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
        self.assertIn("run: python tools/dev.py check", self.text)

    def test_gitleaks_download_is_versioned_and_hash_verified(self) -> None:
        self.assertIn("gitleaks_8.30.1_windows_x64.zip", self.text)
        self.assertIn(
            "d29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e",
            self.text,
        )
        self.assertIn("Get-FileHash -Algorithm SHA256", self.text)


if __name__ == "__main__":
    unittest.main()

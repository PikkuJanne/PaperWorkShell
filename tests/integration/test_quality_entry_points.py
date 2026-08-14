from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class QualityEntryPointIntegrationTests(unittest.TestCase):
    def test_command_list_entry_point(self) -> None:
        completed = subprocess.run(  # noqa: S603 - interpreter and repository script are fixed
            [sys.executable, "tools/dev.py", "list"],
            cwd=ROOT,
            check=False,
            shell=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("test-integration", completed.stdout)
        self.assertIn("validate-docs", completed.stdout)
        self.assertIn("validate-portfolio-boundary", completed.stdout)
        self.assertIn("validate-dependencies", completed.stdout)
        self.assertIn("generate-fixtures", completed.stdout)
        self.assertIn("validate-fixtures", completed.stdout)
        self.assertIn("scan-secrets", completed.stdout)
        self.assertIn("run-m1-vertical-slice", completed.stdout)

    def test_fixture_index_entry_point_is_non_disclosing(self) -> None:
        completed = subprocess.run(  # noqa: S603 - interpreter and repository module are fixed
            [
                sys.executable,
                "-m",
                "tools.validate_synthetic_fixtures",
                "--index-only",
            ],
            cwd=ROOT,
            check=False,
            shell=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("24 coverage entries", completed.stdout)
        self.assertNotIn(str(ROOT), completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()

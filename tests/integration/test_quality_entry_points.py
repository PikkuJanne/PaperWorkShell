from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

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
        self.assertIn("scan-secrets", completed.stdout)
        self.assertIn("benchmark-reference", completed.stdout)

    def test_reference_benchmark_entry_point_is_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = temporary
            completed = subprocess.run(  # noqa: S603 - interpreter and module are fixed
                [
                    sys.executable,
                    "-m",
                    "tools.reference_profile",
                    "benchmark",
                    "--work-directory",
                    temporary,
                ],
                cwd=ROOT,
                check=False,
                shell=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(0, completed.returncode, completed.stderr)
        report = cast(dict[str, Any], json.loads(completed.stdout))
        self.assertEqual("record-only", report["measurement_status"])
        self.assertEqual([], report["product_targets"])
        output = f"{completed.stdout}\n{completed.stderr}"
        self.assertNotIn(str(ROOT), output)
        self.assertNotIn(temporary_path, output)
        self.assertNotIn("hostname", output.casefold())
        self.assertNotIn("username", output.casefold())


if __name__ == "__main__":
    unittest.main()

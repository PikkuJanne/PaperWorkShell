from __future__ import annotations

import unittest
from pathlib import Path

from tools.dev import COMMANDS

ROOT = Path(__file__).resolve().parents[2]


class AccessibilityBaselineTests(unittest.TestCase):
    def test_accessibility_lane_is_registered(self) -> None:
        specification = COMMANDS["test-accessibility"]

        self.assertTrue(specification.include_in_check)
        self.assertEqual(("pytest", "tests/accessibility"), specification.arguments)

    def test_no_ui_surface_exists_without_widget_accessibility_tests(self) -> None:
        source_files = sorted((ROOT / "src/paperworkshell").rglob("*.py"))
        toolkit_references = [
            path for path in source_files if "PySide6" in path.read_text(encoding="utf-8")
        ]

        self.assertEqual(
            [],
            toolkit_references,
            "Replace this foundation sentinel with keyboard, focus, label, scaling, and "
            "announcement tests when the first PySide6 UI surface is introduced.",
        )


if __name__ == "__main__":
    unittest.main()

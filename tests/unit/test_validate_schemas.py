from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from tools.reference_profile import PROJECT_ROOT
from tools.validate_schemas import validation_messages


class RepositorySchemaRegistryTests(unittest.TestCase):
    def test_checked_in_schema_registry_passes(self) -> None:
        self.assertEqual([], validation_messages())

    def test_invalid_reference_profile_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "benchmarks").mkdir()
            (root / "schemas").mkdir()
            for name in ("TASKS.yaml", "TASKS.schema.json"):
                shutil.copy2(PROJECT_ROOT / name, root / name)
            shutil.copy2(
                PROJECT_ROOT / "schemas/reference-profile.schema.json",
                root / "schemas/reference-profile.schema.json",
            )
            loaded = json.loads(
                (PROJECT_ROOT / "benchmarks/reference-windows-v1.json").read_text(encoding="utf-8")
            )
            profile = cast(dict[str, Any], loaded)
            profile["schema_version"] = 2
            (root / "benchmarks/reference-windows-v1.json").write_text(
                json.dumps(profile),
                encoding="utf-8",
            )

            messages = validation_messages(root)

        self.assertTrue(any("REFERENCE_PROFILE_INVALID" in message for message in messages))


if __name__ == "__main__":
    unittest.main()

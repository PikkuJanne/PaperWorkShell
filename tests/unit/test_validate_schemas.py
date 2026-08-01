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
    def _copy_registry_root(self, root: Path) -> None:
        (root / "benchmarks").mkdir()
        (root / "schemas" / "examples").mkdir(parents=True)
        (root / "tests" / "fixtures" / "manifests" / "v1").mkdir(parents=True)
        for name in ("TASKS.yaml", "TASKS.schema.json"):
            shutil.copy2(PROJECT_ROOT / name, root / name)
        for relative_path in (
            Path("schemas/reference-profile.schema.json"),
            Path("schemas/case-manifest-v1.schema.json"),
            Path("benchmarks/reference-windows-v1.json"),
        ):
            shutil.copy2(PROJECT_ROOT / relative_path, root / relative_path)
        shutil.copytree(
            PROJECT_ROOT / "schemas" / "examples",
            root / "schemas" / "examples",
            dirs_exist_ok=True,
        )
        shutil.copytree(
            PROJECT_ROOT / "tests" / "fixtures" / "manifests" / "v1" / "invalid",
            root / "tests" / "fixtures" / "manifests" / "v1" / "invalid",
        )

    def test_checked_in_schema_registry_passes(self) -> None:
        self.assertEqual([], validation_messages())

    def test_invalid_reference_profile_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_registry_root(root)
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

    def test_invalid_manifest_example_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_registry_root(root)
            manifest_path = root / "schemas" / "examples" / "case-manifest-v1-minimal.json"
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = cast(dict[str, Any], loaded)
            manifest["schema_version"] = 2
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            messages = validation_messages(root)

        self.assertTrue(any("MANIFEST_POSITIVE_FIXTURE_INVALID" in message for message in messages))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from tools.reference_profile import PROJECT_ROOT
from tools.validate_dependencies import (
    INVALID_FIXTURE_DIRECTORY,
    INVENTORY_PATH,
    INVENTORY_SCHEMA_PATH,
)
from tools.validate_schemas import validation_messages
from tools.validate_templates import (
    TEMPLATE_INVALID_FIXTURE_DIRECTORY,
    TEMPLATE_SCHEMA_PATH,
)


class RepositorySchemaRegistryTests(unittest.TestCase):
    def _copy_registry_root(self, root: Path) -> None:
        for relative_path in (
            Path(".github/workflows/quality.yml"),
            Path(".python-version"),
            Path("DECISIONS.md"),
            Path("TASKS.schema.json"),
            Path("TASKS.yaml"),
            Path("benchmarks/reference-windows-v1.json"),
            Path("docs/development/dependency-policy.md"),
            INVENTORY_PATH,
            Path("pyproject.toml"),
            Path("schemas/reference-profile.schema.json"),
            Path("schemas/case-manifest-v1.schema.json"),
            TEMPLATE_SCHEMA_PATH,
            INVENTORY_SCHEMA_PATH,
            Path("schemas/worker-rpc-v1.schema.json"),
            Path("tools/dev.py"),
            Path("uv.lock"),
        ):
            (root / relative_path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(PROJECT_ROOT / relative_path, root / relative_path)
        (root / "schemas" / "examples").mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            PROJECT_ROOT / "schemas" / "examples",
            root / "schemas" / "examples",
            dirs_exist_ok=True,
        )
        shutil.copytree(
            PROJECT_ROOT / "tests" / "fixtures" / "manifests" / "v1" / "invalid",
            root / "tests" / "fixtures" / "manifests" / "v1" / "invalid",
        )
        shutil.copytree(
            PROJECT_ROOT / "tests" / "fixtures" / "worker-rpc" / "v1" / "invalid",
            root / "tests" / "fixtures" / "worker-rpc" / "v1" / "invalid",
        )
        shutil.copytree(
            PROJECT_ROOT / INVALID_FIXTURE_DIRECTORY,
            root / INVALID_FIXTURE_DIRECTORY,
        )
        shutil.copytree(
            PROJECT_ROOT / TEMPLATE_INVALID_FIXTURE_DIRECTORY,
            root / TEMPLATE_INVALID_FIXTURE_DIRECTORY,
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

    def test_invalid_worker_rpc_example_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_registry_root(root)
            rpc_path = next((root / "schemas" / "examples" / "worker-rpc" / "v1").glob("*.json"))
            loaded = json.loads(rpc_path.read_text(encoding="utf-8"))
            rpc_message = cast(dict[str, Any], loaded)
            rpc_message["protocol_version"] = 2
            rpc_path.write_text(json.dumps(rpc_message), encoding="utf-8")

            messages = validation_messages(root)

        self.assertTrue(any("RPC_POSITIVE_FIXTURE_INVALID" in message for message in messages))

    def test_invalid_template_example_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_registry_root(root)
            template_path = root / "schemas" / "examples" / "template-v1-minimal.json"
            loaded = json.loads(template_path.read_text(encoding="utf-8"))
            template = cast(dict[str, Any], loaded)
            template["schema_version"] = 2
            template_path.write_text(json.dumps(template), encoding="utf-8")

            messages = validation_messages(root)

        self.assertTrue(any("TEMPLATE_POSITIVE_FIXTURE_INVALID" in message for message in messages))

    def test_invalid_dependency_inventory_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_registry_root(root)
            inventory_path = root / INVENTORY_PATH
            loaded = json.loads(inventory_path.read_text(encoding="utf-8"))
            inventory = cast(dict[str, Any], loaded)
            inventory["schema_version"] = 2
            inventory_path.write_text(json.dumps(inventory), encoding="utf-8")

            messages = validation_messages(root)

        self.assertTrue(
            any("DEPENDENCY_POSITIVE_FIXTURE_INVALID" in message for message in messages)
        )


if __name__ == "__main__":
    unittest.main()

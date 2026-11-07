from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from tools.generate_synthetic_fixtures import (
    INDEX_PATH as FIXTURE_INDEX_PATH,
)
from tools.generate_synthetic_fixtures import (
    SCHEMA_PATH as FIXTURE_INDEX_SCHEMA_PATH,
)
from tools.validate_dependencies import (
    INVALID_FIXTURE_DIRECTORY,
    INVENTORY_PATH,
    INVENTORY_SCHEMA_PATH,
)
from tools.validate_generic_portal_template import (
    CANONICAL_TEMPLATE_PATH as GENERIC_TEMPLATE_PATH,
)
from tools.validate_generic_portal_template import (
    PACKAGED_TEMPLATE_PATH as PACKAGED_GENERIC_TEMPLATE_PATH,
)
from tools.validate_generic_portal_template import (
    PACKET_FIXTURE_PATH as GENERIC_PACKET_FIXTURE_PATH,
)
from tools.validate_schemas import (
    EXPORT_HISTORY_PACKAGED_SCHEMA_PATH,
    EXPORT_HISTORY_SCHEMA_PATH,
    PROJECT_ROOT,
    validation_messages,
)
from tools.validate_synthetic_fixtures import (
    NEGATIVE_FIXTURE_DIRECTORY as FIXTURE_NEGATIVE_DIRECTORY,
)
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
            Path("docs/development/dependency-policy.md"),
            INVENTORY_PATH,
            Path("pyproject.toml"),
            Path("schemas/m1-vertical-slice-benchmark-report-v1.schema.json"),
            Path("schemas/m1-vertical-slice-benchmark-report-v2.schema.json"),
            Path("schemas/m1-vertical-slice-benchmark-report-v3.schema.json"),
            Path("schemas/case-manifest-v1.schema.json"),
            Path("schemas/case-manifest-v2.schema.json"),
            Path("schemas/case-manifest-v3.schema.json"),
            Path("schemas/case-manifest-v4.schema.json"),
            FIXTURE_INDEX_SCHEMA_PATH,
            TEMPLATE_SCHEMA_PATH,
            INVENTORY_SCHEMA_PATH,
            Path("schemas/worker-rpc-v1.schema.json"),
            EXPORT_HISTORY_SCHEMA_PATH,
            EXPORT_HISTORY_PACKAGED_SCHEMA_PATH,
            Path("tools/dev.py"),
            Path("uv.lock"),
            FIXTURE_INDEX_PATH,
            GENERIC_TEMPLATE_PATH,
            PACKAGED_GENERIC_TEMPLATE_PATH,
            GENERIC_PACKET_FIXTURE_PATH,
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
            PROJECT_ROOT / "tests" / "fixtures" / "manifests" / "v2" / "invalid",
            root / "tests" / "fixtures" / "manifests" / "v2" / "invalid",
        )
        shutil.copytree(
            PROJECT_ROOT / "tests" / "fixtures" / "manifests" / "v3" / "invalid",
            root / "tests" / "fixtures" / "manifests" / "v3" / "invalid",
        )
        shutil.copytree(
            PROJECT_ROOT / "tests" / "fixtures" / "manifests" / "v4" / "invalid",
            root / "tests" / "fixtures" / "manifests" / "v4" / "invalid",
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
        shutil.copytree(
            PROJECT_ROOT / FIXTURE_NEGATIVE_DIRECTORY,
            root / FIXTURE_NEGATIVE_DIRECTORY,
        )
        shutil.copytree(
            PROJECT_ROOT / FIXTURE_INDEX_PATH.parent,
            root / FIXTURE_INDEX_PATH.parent,
            dirs_exist_ok=True,
        )

    def test_checked_in_schema_registry_passes(self) -> None:
        self.assertEqual([], validation_messages())

    def test_invalid_manifest_example_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_registry_root(root)
            manifest_path = root / "schemas" / "examples" / "case-manifest-v1-minimal.json"
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = cast(dict[str, Any], loaded)
            manifest["schema_version"] = 3
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

    def test_changed_generic_portal_template_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_registry_root(root)
            with (root / GENERIC_TEMPLATE_PATH).open("ab") as stream:
                stream.write(b"\n")

            messages = validation_messages(root)

        self.assertTrue(any("GENERIC_TEMPLATE_DIGEST_MISMATCH" in message for message in messages))

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

    def test_invalid_synthetic_fixture_index_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._copy_registry_root(root)
            index_path = root / FIXTURE_INDEX_PATH
            loaded = json.loads(index_path.read_text(encoding="utf-8"))
            index = cast(dict[str, Any], loaded)
            index["schema_version"] = 2
            index_path.write_text(json.dumps(index), encoding="utf-8")

            messages = validation_messages(root)

        self.assertTrue(any("FIXTURE_INDEX_VERSION_FUTURE" in message for message in messages))


if __name__ == "__main__":
    unittest.main()

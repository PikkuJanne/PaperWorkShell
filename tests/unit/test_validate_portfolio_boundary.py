"""Tests for the durable ThirdLife late-binding governance boundary."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import yaml

from tools.validate_portfolio_boundary import (
    ALIGNMENT_DECISION,
    BUNDLE_PATH,
    DECISIONS_PATH,
    DOCUMENT_METADATA_REQUIREMENTS,
    MAXIMUM_GOVERNANCE_INPUT_BYTES,
    REQUIRED_BUNDLE_FILES,
    STEERING_SOURCE_BYTES,
    STEERING_SOURCE_SHA256,
    _bundle_issues,
    _dependency_declaration_issues,
    _document_issues,
    _inventory_dependency_issues,
    _lock_dependency_issues,
    _production_source_issues,
    _release_body_has_structured_placeholders,
    _release_checklist_is_complete,
    _release_metadata_is_final,
    _runtime_schema_issues,
    _semver_at_least,
    _task_contract_issues,
    main,
    validation_issues,
)

ROOT = Path(__file__).resolve().parents[2]


def _load_tasks() -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load((ROOT / "TASKS.yaml").read_text(encoding="utf-8")))


def _task(data: dict[str, Any], task_id: str) -> dict[str, Any]:
    tasks = cast(list[dict[str, Any]], data["tasks"])
    return next(task for task in tasks if task["id"] == task_id)


def _issue_codes(issues: object) -> set[str]:
    return {issue.code for issue in cast(list[Any], issues)}


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _bundle_validation(tasks_data: dict[str, Any]) -> dict[str, object]:
    tasks = cast(list[dict[str, Any]], tasks_data["tasks"])
    return {
        "json_schema": "passed",
        "dag": "passed",
        "task_count": len(tasks),
        "dependency_edge_count": sum(len(cast(list[object], task["depends_on"])) for task in tasks),
        "milestone_count": len(cast(list[object], tasks_data["milestones"])),
        "gate_task_count": sum(task["kind"] == "gate" for task in tasks),
        "initial_ready_tasks": sorted(
            cast(str, task["id"]) for task in tasks if task["status"] == "ready"
        ),
        "all_tasks_reachable_from": "PWS-0001",
    }


def _write_bundle(
    root: Path,
    tasks_data: dict[str, Any],
    *,
    version: str = "0.2.0",
    extra_path: str | None = None,
) -> None:
    paths = set(REQUIRED_BUNDLE_FILES)
    if extra_path is not None:
        paths.add(extra_path)
    records: list[dict[str, object]] = []
    for relative_path in sorted(paths):
        raw = f"fixture for {relative_path}\n".encode()
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        records.append(
            {
                "path": relative_path,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    _write_json(
        root / BUNDLE_PATH,
        {
            "bundle_version": version,
            "source_inputs": [
                {
                    "bytes": STEERING_SOURCE_BYTES,
                    "sha256": STEERING_SOURCE_SHA256,
                }
            ],
            "files": records,
            "validation": _bundle_validation(tasks_data),
        },
    )


class PortfolioBoundaryTests(unittest.TestCase):
    def test_checked_in_boundary_passes(self) -> None:
        self.assertEqual([], validation_issues(ROOT))

    def test_recursive_dependency_mapping_and_path_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "pyproject.toml").write_text(
                """
[build-system]
requires = []
[project]
name = "paperworkshell"
version = "0.0.0"
dependencies = []
[dependency-groups]
dev = [{ path = "../suite-assembly/runtime" }]
""".strip()
                + "\n",
                encoding="utf-8",
            )

            codes = _issue_codes(_dependency_declaration_issues(root))

        self.assertIn("PORTFOLIO_RUNTIME_DEPENDENCY", codes)

    def test_uv_workspace_is_rejected_even_with_a_generic_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "pyproject.toml").write_text(
                """
[build-system]
requires = []
[project]
name = "paperworkshell"
version = "0.0.0"
dependencies = []
[tool.uv.workspace]
members = ["../suite-assembly"]
""".strip()
                + "\n",
                encoding="utf-8",
            )

            codes = _issue_codes(_dependency_declaration_issues(root))

        self.assertIn("PORTFOLIO_RUNTIME_DEPENDENCY", codes)

    def test_build_backend_path_is_rejected_even_with_a_generic_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "pyproject.toml").write_text(
                """
[build-system]
requires = []
backend-path = ["../suite-build"]
[project]
name = "paperworkshell"
version = "0.0.0"
dependencies = []
""".strip()
                + "\n",
                encoding="utf-8",
            )

            codes = _issue_codes(_dependency_declaration_issues(root))

        self.assertIn("PORTFOLIO_RUNTIME_DEPENDENCY", codes)

    def test_portfolio_build_backend_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "pyproject.toml").write_text(
                """
[build-system]
requires = []
build-backend = "thirdlife.build"
[project]
name = "paperworkshell"
version = "0.0.0"
dependencies = []
""".strip()
                + "\n",
                encoding="utf-8",
            )

            codes = _issue_codes(_dependency_declaration_issues(root))

        self.assertIn("PORTFOLIO_RUNTIME_DEPENDENCY", codes)

    def test_pep508_direct_source_without_spaces_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "pyproject.toml").write_text(
                """
[build-system]
requires = []
[project]
name = "paperworkshell"
version = "0.0.0"
dependencies = ["safe@../suite-runtime"]
""".strip()
                + "\n",
                encoding="utf-8",
            )

            codes = _issue_codes(_dependency_declaration_issues(root))

        self.assertIn("PORTFOLIO_RUNTIME_DEPENDENCY", codes)

    def test_non_https_pep508_direct_sources_are_rejected(self) -> None:
        dependencies = (
            r"safe@\\server\share\pkg.whl",
            "safe@/absolute/pkg.whl",
            r"safe@C:relative\pkg.whl",
            r"safe@\rooted\pkg.whl",
            r"safe@~\pkg.whl",
            "safe @ local.whl",
            "safe [feature] @ file:///absolute/pkg.whl",
            "safe\t[feature]\t@\tC:relative\\pkg.whl",
        )
        for dependency in dependencies:
            with self.subTest(dependency=dependency):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    root = Path(temporary_directory)
                    (root / "pyproject.toml").write_text(
                        (
                            "[build-system]\n"
                            "requires = []\n"
                            "[project]\n"
                            'name = "paperworkshell"\n'
                            'version = "0.0.0"\n'
                            f"dependencies = [{json.dumps(dependency)}]\n"
                        ),
                        encoding="utf-8",
                    )

                    issues = _dependency_declaration_issues(root)
                    codes = _issue_codes(issues)

                self.assertIn("PORTFOLIO_RUNTIME_DEPENDENCY", codes)

    def test_uv_dependency_surfaces_reject_portfolio_and_local_sources(self) -> None:
        declarations = (
            'dev-dependencies = ["safe@../suite-dev"]',
            'override-dependencies = ["thirdlife-runtime==1"]',
            'constraint-dependencies = ["safe@../suite-constraint"]',
            'build-constraint-dependencies = ["safe@../suite-build"]',
            'extra-build-dependencies = { safe = ["safe@../suite-extra"] }',
            'dependency-metadata = [{ name = "safe", requires-dist = ["thirdlife-runtime==1"] }]',
            'find-links = ["../suite-index"]',
            'find-links = ["wheelhouse"]',
            'index-url = "file:///private/suite-index"',
            'extra-index-url = "http://example.invalid/simple"',
        )
        for declaration in declarations:
            with self.subTest(declaration=declaration):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    root = Path(temporary_directory)
                    (root / "pyproject.toml").write_text(
                        (
                            "[build-system]\n"
                            "requires = []\n"
                            "[project]\n"
                            'name = "paperworkshell"\n'
                            'version = "0.0.0"\n'
                            "dependencies = []\n"
                            "[tool.uv]\n"
                            f"{declaration}\n"
                        ),
                        encoding="utf-8",
                    )

                    issues = _dependency_declaration_issues(root)
                    codes = _issue_codes(issues)

                self.assertIn("PORTFOLIO_RUNTIME_DEPENDENCY", codes)
                if "http://" in declaration:
                    self.assertIn(
                        "insecure resolution sources",
                        "\n".join(issue.message for issue in issues),
                    )

    def test_uv_https_resolution_locations_remain_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "pyproject.toml").write_text(
                (
                    "[build-system]\n"
                    "requires = []\n"
                    "[project]\n"
                    'name = "paperworkshell"\n'
                    'version = "0.0.0"\n'
                    "dependencies = []\n"
                    "[tool.uv]\n"
                    'find-links = ["https://packages.example.invalid/wheels"]\n'
                    "[[tool.uv.index]]\n"
                    'name = "reviewed"\n'
                    'url = "https://packages.example.invalid/simple"\n'
                ),
                encoding="utf-8",
            )

            issues = _dependency_declaration_issues(root)

        self.assertEqual([], issues)

    def test_root_uv_toml_dependency_surface_is_inspected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "pyproject.toml").write_text(
                (
                    "[build-system]\n"
                    "requires = []\n"
                    "[project]\n"
                    'name = "paperworkshell"\n'
                    'version = "0.0.0"\n'
                    "dependencies = []\n"
                ),
                encoding="utf-8",
            )
            (root / "uv.toml").write_text(
                'extra-build-dependencies = { safe = ["safe@../suite-extra"] }\n',
                encoding="utf-8",
            )

            issues = _dependency_declaration_issues(root)
            codes = _issue_codes(issues)

        self.assertIn("PORTFOLIO_RUNTIME_DEPENDENCY", codes)
        self.assertIn(
            "uv.toml",
            {issue.path for issue in issues if issue.code == "PORTFOLIO_RUNTIME_DEPENDENCY"},
        )

    def test_all_public_entry_point_tables_reject_portfolio_commands(self) -> None:
        declarations = (
            '[project.gui-scripts]\nthirdlife-open = "paperworkshell.__main__:main"\n',
            '[project.entry-points."paperworkshell.commands"]\n'
            'thirdlife-open = "paperworkshell.__main__:main"\n',
        )
        for declaration in declarations:
            with self.subTest(declaration=declaration.splitlines()[0]):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    root = Path(temporary_directory)
                    (root / "pyproject.toml").write_text(
                        (
                            "[build-system]\n"
                            "requires = []\n"
                            "[project]\n"
                            'name = "paperworkshell"\n'
                            'version = "0.0.0"\n'
                            "dependencies = []\n"
                            f"{declaration}"
                        ),
                        encoding="utf-8",
                    )

                    codes = _issue_codes(_dependency_declaration_issues(root))

                self.assertIn("PORTFOLIO_PUBLIC_ENTRYPOINT_INVALID", codes)

    def test_lock_and_inventory_nested_sources_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "uv.lock").write_text(
                '[[package]]\nname = "safe"\nsource = { editable = "../suite-assembly" }\n',
                encoding="utf-8",
            )
            _write_json(
                root / "packaging/dependency-inventory-v1.json",
                {"components": [{"component_id": "safe", "source": {"path": "../suite-assembly"}}]},
            )

            lock_codes = _issue_codes(_lock_dependency_issues(root))
            inventory_codes = _issue_codes(_inventory_dependency_issues(root))

        self.assertIn("PORTFOLIO_RUNTIME_DEPENDENCY", lock_codes)
        self.assertIn("PORTFOLIO_RUNTIME_DEPENDENCY", inventory_codes)

    def test_lock_source_mapping_rejects_bare_local_and_vcs_schemes(self) -> None:
        for source in (
            "file:///C:/projects/suite",
            "git+https://example.invalid/suite.git",
            "hg+https://example.invalid/suite",
            "/absolute/suite",
            r"\\server\share\suite",
            r"C:relative\suite",
            r"~\suite",
        ):
            with self.subTest(source=source):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    root = Path(temporary_directory)
                    (root / "uv.lock").write_text(
                        f'[[package]]\nname = "safe"\nsource = {{ url = {json.dumps(source)} }}\n',
                        encoding="utf-8",
                    )

                    codes = _issue_codes(_lock_dependency_issues(root))

                self.assertIn("PORTFOLIO_RUNTIME_DEPENDENCY", codes)

    def test_production_docstring_is_allowed_but_runtime_reference_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "src/paperworkshell/module.py"
            source.parent.mkdir(parents=True)
            source.write_text(
                '"""ThirdLife is governance context only."""\nVALUE = "safe"\n', encoding="utf-8"
            )
            self.assertEqual([], _production_source_issues(root))

            source.write_text('"""Context."""\nACTION = "Open in ThirdLife"\n', encoding="utf-8")
            codes = _issue_codes(_production_source_issues(root))

        self.assertIn("PORTFOLIO_PRODUCTION_REFERENCE", codes)

    def test_production_module_path_and_relative_import_are_rejected(self) -> None:
        cases = (
            ("src/paperworkshell/app/thirdlife/bridge.py", "VALUE = 1\n"),
            ("src/paperworkshell/app/bridge.py", "from .thirdlife import bridge\n"),
            ("src/paperworkshell/app/thirdlife.pyi", "VALUE: int\n"),
        )
        for relative_path, source_text in cases:
            with self.subTest(relative_path=relative_path):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    root = Path(temporary_directory)
                    source = root / relative_path
                    source.parent.mkdir(parents=True)
                    source.write_text(source_text, encoding="utf-8")

                    codes = _issue_codes(_production_source_issues(root))

                self.assertIn("PORTFOLIO_PRODUCTION_REFERENCE", codes)

    def test_production_package_root_alias_fails_before_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            package_root = root / "src/paperworkshell"
            package_root.mkdir(parents=True)
            (package_root / "private-path-canary.py").write_text(
                'ACTION = "Open in ThirdLife"\n',
                encoding="utf-8",
            )
            original_is_symlink = Path.is_symlink

            def injected_is_symlink(path: Path) -> bool:
                return path == package_root or original_is_symlink(path)

            with patch.object(Path, "is_symlink", injected_is_symlink):
                issues = _production_source_issues(root)

        self.assertEqual(
            {"PORTFOLIO_PRODUCT_SOURCE_UNSAFE"},
            _issue_codes(issues),
        )

    def test_production_nested_directory_alias_fails_before_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            package_root = root / "src/paperworkshell"
            aliased_directory = package_root / "app"
            aliased_directory.mkdir(parents=True)
            (aliased_directory / "private-path-canary.py").write_text(
                'ACTION = "Open in ThirdLife"\n',
                encoding="utf-8",
            )
            original_is_symlink = Path.is_symlink
            original_rglob = Path.rglob

            def injected_is_symlink(path: Path) -> bool:
                return path == aliased_directory or original_is_symlink(path)

            def guarded_rglob(path: Path, pattern: str) -> Any:
                if path == package_root:
                    raise AssertionError("unsafe directory trees must not be traversed")
                return original_rglob(path, pattern)

            with (
                patch.object(Path, "is_symlink", injected_is_symlink),
                patch.object(Path, "rglob", guarded_rglob),
            ):
                issues = _production_source_issues(root)

        self.assertEqual(
            {"PORTFOLIO_PRODUCT_SOURCE_UNSAFE"},
            _issue_codes(issues),
        )
        self.assertNotIn("PORTFOLIO_PRODUCTION_REFERENCE", _issue_codes(issues))

    def test_runtime_schema_reference_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            schema_path = root / "schemas/case-manifest-v1.schema.json"
            _write_json(schema_path, {"type": "object", "properties": {"thirdlife_id": {}}})

            codes = _issue_codes(_runtime_schema_issues(root))

        self.assertIn("PORTFOLIO_RUNTIME_SCHEMA_REFERENCE", codes)

    def test_unregistered_runtime_schema_reference_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            schema_path = root / "schemas/thirdlife-handoff-v1.schema.json"
            _write_json(schema_path, {"type": "object"})

            codes = _issue_codes(_runtime_schema_issues(root))

        self.assertIn("PORTFOLIO_RUNTIME_SCHEMA_REFERENCE", codes)

    def test_runtime_schema_root_alias_fails_before_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            schema_root = root / "schemas"
            schema_root.mkdir()
            _write_json(
                schema_root / "private-path-canary.schema.json",
                {"properties": {"thirdlife_runtime": {}}},
            )
            original_is_symlink = Path.is_symlink

            def injected_is_symlink(path: Path) -> bool:
                return path == schema_root or original_is_symlink(path)

            with patch.object(Path, "is_symlink", injected_is_symlink):
                issues = _runtime_schema_issues(root)

        codes = _issue_codes(issues)
        self.assertIn("PORTFOLIO_INPUT_UNSAFE", codes)
        self.assertNotIn("PORTFOLIO_RUNTIME_SCHEMA_REFERENCE", codes)

    def test_canonical_document_metadata_matches_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for relative_path in DOCUMENT_METADATA_REQUIREMENTS:
                target = root / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative_path, target)

            self.assertEqual([], _document_issues(root))
            traceability = root / "docs/portfolio/SOURCE_TRACEABILITY.md"
            traceability.write_text(
                traceability.read_text(encoding="utf-8").replace(
                    "Preserve supplied portfolio provenance", "Create a runtime protocol"
                ),
                encoding="utf-8",
            )
            codes = _issue_codes(_document_issues(root))

        self.assertIn("PORTFOLIO_DOCUMENT_STATUS_INVALID", codes)

    def test_release_document_metadata_can_advance_at_the_final_gate(self) -> None:
        data = _load_tasks()
        _task(data, "PWS-0808")["status"] = "review"
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for relative_path in DOCUMENT_METADATA_REQUIREMENTS:
                target = root / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative_path, target)

            self.assertIn(
                "PORTFOLIO_DOCUMENT_STAGE_INVALID",
                _issue_codes(_document_issues(root, data)),
            )
            interface = root / "docs/release/RELEASE_INTERFACE.md"
            interface.write_text(
                interface.read_text(encoding="utf-8")
                .replace(
                    "DRAFT TEMPLATE — not a release guarantee and not an application API",
                    "FINAL — exact packaged candidate; not an application API",
                )
                .replace("`0-draft`", "`1`", 1),
                encoding="utf-8",
            )
            evidence = root / "docs/release/RELEASE_EVIDENCE.md"
            evidence.write_text(
                evidence.read_text(encoding="utf-8")
                .replace(
                    "DRAFT TEMPLATE — complete only for an exact packaged M8 candidate",
                    "FINAL — complete for an exact packaged M8 candidate",
                )
                .replace("**TBD**", "**1.0.0**", 1)
                .replace("**TBD**", "**Stable**", 1)
                .replace("`0-draft`", "`1`", 1)
                .replace("**TBD**", "**source-revision**", 1)
                .replace("**TBD**", "**1**", 1),
                encoding="utf-8",
            )

            self.assertIn(
                "PORTFOLIO_DOCUMENT_STAGE_INVALID",
                _issue_codes(_document_issues(root, data)),
            )
            for release_document in (interface, evidence):
                release_document.write_text(
                    release_document.read_text(encoding="utf-8")
                    .replace("TBD", "verified-value")
                    .replace("Pending", "Passed"),
                    encoding="utf-8",
                )
            checklist = root / "docs/release/STANDALONE_RELEASE_CHECKLIST.md"
            checklist.write_text(
                checklist.read_text(encoding="utf-8").replace("- [ ]", "- [x]"),
                encoding="utf-8",
            )

            for status in ("planned", "ready", "deferred"):
                with self.subTest(status=status):
                    _task(data, "PWS-0808")["status"] = status
                    self.assertIn(
                        "PORTFOLIO_DOCUMENT_STAGE_INVALID",
                        _issue_codes(_document_issues(root, data)),
                    )
            for status in ("in_progress", "blocked", "review", "done"):
                with self.subTest(status=status):
                    _task(data, "PWS-0808")["status"] = status
                    self.assertEqual([], _document_issues(root, data))

    def test_negated_or_incomplete_release_status_is_not_final(self) -> None:
        for status in (
            "INCOMPLETE",
            "UNRELEASED",
            "NOT FINAL",
            "FINAL — INCOMPLETE",
            "FINAL — DRAFT",
            "COMPLETE — NOT FINAL",
            "RELEASED — PENDING",
        ):
            with self.subTest(status=status):
                self.assertFalse(
                    _release_metadata_is_final(
                        Path("docs/release/RELEASE_INTERFACE.md"),
                        {"Document status": status, "Interface revision": "1"},
                    )
                )

    def test_final_release_body_rejects_structured_placeholders_only(self) -> None:
        self.assertTrue(
            _release_body_has_structured_placeholders(
                "| Artifact | Status |\n|---|---|\n| A | TBD |"
            )
        )
        self.assertTrue(_release_body_has_structured_placeholders("- Verification: Pending"))
        self.assertFalse(
            _release_body_has_structured_placeholders(
                "Final documents cannot retain explanatory `TBD` placeholders."
            )
        )
        self.assertFalse(_release_checklist_is_complete("- [ ] Verify candidate\n"))
        self.assertTrue(_release_checklist_is_complete("- [x] Verify candidate\n"))

    def test_durable_task_ownership_and_order_are_enforced(self) -> None:
        data = _load_tasks()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            shutil.copyfile(ROOT / DECISIONS_PATH, root / DECISIONS_PATH)
            self.assertEqual([], _task_contract_issues(root, data))

            changed = copy.deepcopy(data)
            cast(list[str], _task(changed, "PWS-0806")["depends_on"]).remove("PWS-0804")
            codes = _issue_codes(_task_contract_issues(root, changed))
            self.assertIn("PORTFOLIO_MIGRATION_DOC_ORDER_INVALID", codes)

            changed = copy.deepcopy(data)
            cast(list[str], _task(changed, "PWS-0809")["decision_refs"]).remove(ALIGNMENT_DECISION)
            codes = _issue_codes(_task_contract_issues(root, changed))

        self.assertIn("PORTFOLIO_DECISION_COVERAGE_INVALID", codes)

    def test_alignment_decision_status_and_core_contract_are_enforced(self) -> None:
        data = _load_tasks()
        replacements = (
            (
                "## PWS-D042 — PaperWorkShell remains standalone and ThirdLife portfolio "
                "assembly is late-bound\n\n**Status:** FROZEN",
                "## PWS-D042 — PaperWorkShell remains standalone and ThirdLife portfolio "
                "assembly is late-bound\n\n**Status:** PROPOSED",
            ),
            (
                "independently useful, independently versioned",
                "runtime-integrated and portfolio-versioned",
            ),
        )
        for before, after in replacements:
            with self.subTest(after=after):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    root = Path(temporary_directory)
                    decisions = (ROOT / DECISIONS_PATH).read_text(encoding="utf-8")
                    self.assertIn(before, decisions)
                    (root / DECISIONS_PATH).write_text(
                        decisions.replace(before, after, 1),
                        encoding="utf-8",
                    )

                    codes = _issue_codes(_task_contract_issues(root, data))

                self.assertIn("PORTFOLIO_DECISION_CONTRACT_INVALID", codes)

    def test_bundle_summary_is_dynamic_and_extra_files_are_allowed(self) -> None:
        data = _load_tasks()
        added_task = copy.deepcopy(_task(data, "PWS-0809"))
        added_task.update(
            {
                "id": "PWS-9999",
                "depends_on": ["PWS-0001"],
                "status": "planned",
            }
        )
        cast(list[dict[str, Any]], data["tasks"]).append(added_task)
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            _write_bundle(root, data, version="0.3.0-alpha.1", extra_path="docs/future.md")
            bundle = cast(
                dict[str, Any], json.loads((root / BUNDLE_PATH).read_text(encoding="utf-8"))
            )
            cast(list[dict[str, object]], bundle["files"])[0]["role"] = "future metadata"
            _write_json(root / BUNDLE_PATH, bundle)

            self.assertEqual([], _bundle_issues(root, data))
            bundle["validation"]["task_count"] -= 1
            _write_json(root / BUNDLE_PATH, bundle)
            codes = _issue_codes(_bundle_issues(root, data))

        self.assertIn("PORTFOLIO_BUNDLE_GRAPH_INVALID", codes)

    def test_bundle_version_uses_semver_floor(self) -> None:
        self.assertTrue(_semver_at_least("0.2.0", (0, 2, 0)))
        self.assertTrue(_semver_at_least("1.0.0", (0, 2, 0)))
        self.assertTrue(_semver_at_least("0.3.0-alpha.1", (0, 2, 0)))
        self.assertFalse(_semver_at_least("0.2.0-alpha.1", (0, 2, 0)))
        self.assertFalse(_semver_at_least("0.1.99", (0, 2, 0)))
        self.assertFalse(_semver_at_least("v0.2.0", (0, 2, 0)))

    def test_bundle_rejects_windows_drive_relative_and_reserved_paths(self) -> None:
        data = _load_tasks()
        for unsafe_path in ("C:../outside.md", "docs/CON.txt"):
            with self.subTest(unsafe_path=unsafe_path):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    root = Path(temporary_directory)
                    _write_bundle(root, data)
                    bundle = cast(
                        dict[str, Any],
                        json.loads((root / BUNDLE_PATH).read_text(encoding="utf-8")),
                    )
                    cast(list[dict[str, object]], bundle["files"]).append(
                        {
                            "path": unsafe_path,
                            "bytes": 0,
                            "sha256": hashlib.sha256(b"").hexdigest(),
                        }
                    )
                    _write_json(root / BUNDLE_PATH, bundle)

                    codes = _issue_codes(_bundle_issues(root, data))

                self.assertIn("PORTFOLIO_BUNDLE_INVENTORY_INVALID", codes)

    def test_governance_input_byte_limit_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "pyproject.toml").write_bytes(b" " * (MAXIMUM_GOVERNANCE_INPUT_BYTES + 1))

            codes = _issue_codes(_dependency_declaration_issues(root))

        self.assertIn("PORTFOLIO_INPUT_LIMIT_EXCEEDED", codes)

    def test_cli_failure_is_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = main(["--root", str(root)])

            output = stderr.getvalue()
            self.assertEqual(1, result)
            self.assertNotIn(str(root), output)
            self.assertNotIn("Traceback", output)
            self.assertEqual("", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()

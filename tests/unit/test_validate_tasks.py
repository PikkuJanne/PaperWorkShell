"""Tests for task schema and dependency graph validation."""

from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, cast

import yaml

from tools.validate_tasks import main, validate_data, validate_repository

ROOT = Path(__file__).resolve().parents[2]


def _load_mapping(path: Path, *, yaml_input: bool) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    loaded = yaml.safe_load(text) if yaml_input else json.loads(text)
    return cast(dict[str, Any], loaded)


def _task(data: dict[str, Any], task_id: str) -> dict[str, Any]:
    tasks = cast(list[dict[str, Any]], data["tasks"])
    return next(task for task in tasks if task["id"] == task_id)


def _milestone(data: dict[str, Any], milestone_id: str) -> dict[str, Any]:
    milestones = cast(list[dict[str, Any]], data["milestones"])
    return next(milestone for milestone in milestones if milestone["id"] == milestone_id)


class TaskValidationTests(unittest.TestCase):
    data: dict[str, Any]
    schema: dict[str, Any]
    decisions: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.data = _load_mapping(ROOT / "TASKS.yaml", yaml_input=True)
        cls.schema = _load_mapping(ROOT / "TASKS.schema.json", yaml_input=False)
        cls.decisions = (ROOT / "DECISIONS.md").read_text(encoding="utf-8")

    def issue_codes(self, data: dict[str, Any]) -> set[str]:
        return {issue.code for issue in validate_data(data, self.schema, self.decisions)}

    def test_repository_graph_passes(self) -> None:
        summary, issues = validate_repository(ROOT)

        self.assertEqual([], issues)
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(80, summary.task_count)
        self.assertEqual(238, summary.dependency_count)
        self.assertEqual(9, summary.milestone_count)
        self.assertEqual(40, summary.decision_count)

    def test_unknown_dependency_fails(self) -> None:
        data = copy.deepcopy(self.data)
        _task(data, "PWS-0001")["depends_on"] = ["PWS-9999"]

        self.assertIn("TASK_DEPENDENCY_UNKNOWN", self.issue_codes(data))

    def test_dependency_cycle_fails(self) -> None:
        data = copy.deepcopy(self.data)
        _task(data, "PWS-0001")["depends_on"] = ["PWS-0002"]

        self.assertIn("TASK_GRAPH_CYCLE", self.issue_codes(data))

    def test_unknown_decision_reference_fails(self) -> None:
        data = copy.deepcopy(self.data)
        _task(data, "PWS-0001")["decision_refs"] = ["PWS-D999"]

        self.assertIn("TASK_DECISION_UNKNOWN", self.issue_codes(data))

    def test_gate_must_cover_every_milestone_task(self) -> None:
        data = copy.deepcopy(self.data)
        gate = _task(data, "PWS-0010")
        gate["depends_on"].remove("PWS-0009")

        self.assertIn("MILESTONE_GATE_COVERAGE", self.issue_codes(data))

    def test_gate_kind_must_match_milestone(self) -> None:
        data = copy.deepcopy(self.data)
        _task(data, "PWS-0010")["kind"] = "implementation"

        self.assertIn("MILESTONE_GATE_KIND", self.issue_codes(data))

    def test_release_target_must_use_milestone_gate(self) -> None:
        data = copy.deepcopy(self.data)
        data["release_targets"]["v0.1"]["gate_task"] = "PWS-0010"

        self.assertIn("RELEASE_GATE_MISMATCH", self.issue_codes(data))

    def test_active_task_requires_completed_dependencies(self) -> None:
        data = copy.deepcopy(self.data)
        _task(data, "PWS-0002")["status"] = "ready"

        self.assertIn("TASK_DEPENDENCY_INCOMPLETE", self.issue_codes(data))

    def test_blocked_task_requires_blocker_record(self) -> None:
        data = copy.deepcopy(self.data)
        _task(data, "PWS-0001")["status"] = "blocked"

        self.assertIn("TASK_BLOCKER_MISSING", self.issue_codes(data))

    def test_done_task_requires_completion_evidence(self) -> None:
        data = copy.deepcopy(self.data)
        _task(data, "PWS-0001")["status"] = "done"

        self.assertIn("TASK_DONE_EVIDENCE_MISSING", self.issue_codes(data))

    def test_schema_date_format_is_enforced(self) -> None:
        data = copy.deepcopy(self.data)
        data["frozen_at"] = "not-a-date"

        self.assertIn("SCHEMA_VALIDATION", self.issue_codes(data))

    def test_duplicate_task_id_fails(self) -> None:
        data = copy.deepcopy(self.data)
        tasks = cast(list[dict[str, Any]], data["tasks"])
        tasks.append(copy.deepcopy(tasks[0]))

        self.assertIn("TASK_ID_DUPLICATE", self.issue_codes(data))

    def test_unknown_milestone_fails(self) -> None:
        data = copy.deepcopy(self.data)
        _task(data, "PWS-0001")["milestone"] = "M99"

        self.assertIn("TASK_MILESTONE_UNKNOWN", self.issue_codes(data))

    def test_duplicate_yaml_key_fails_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "TASKS.yaml").write_text(
                "schema_version: 1\nschema_version: 1\n",
                encoding="utf-8",
            )

            summary, issues = validate_repository(root)

        self.assertIsNone(summary)
        self.assertEqual(["YAML_DUPLICATE_KEY"], [issue.code for issue in issues])

    def test_cli_success_is_concise(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = main(["--root", str(ROOT)])

        self.assertEqual(0, result)
        self.assertIn("80 tasks, 238 dependencies", stdout.getvalue())
        self.assertEqual("", stderr.getvalue())

    def test_schema_only_does_not_require_decisions_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "TASKS.yaml").write_text(
                (ROOT / "TASKS.yaml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (root / "TASKS.schema.json").write_text(
                (ROOT / "TASKS.schema.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )

            summary, issues = validate_repository(root, schema_only=True)

        self.assertEqual([], issues)
        self.assertIsNotNone(summary)

    def test_milestone_helper_covers_known_gate(self) -> None:
        milestone = _milestone(self.data, "M0")

        self.assertEqual("PWS-0010", milestone["gate_task"])


if __name__ == "__main__":
    unittest.main()

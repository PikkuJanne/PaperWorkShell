"""Tests for task schema and dependency graph validation."""

from __future__ import annotations

import copy
import io
import json
import re
import shutil
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
        tasks = cast(list[dict[str, Any]], self.data["tasks"])
        self.assertEqual(len(tasks), summary.task_count)
        self.assertEqual(
            sum(len(cast(list[object], task["depends_on"])) for task in tasks),
            summary.dependency_count,
        )
        self.assertEqual(len(cast(list[object], self.data["milestones"])), summary.milestone_count)
        self.assertEqual(
            len(re.findall(r"^## PWS-D[0-9]{3}\b", self.decisions, re.MULTILINE)),
            summary.decision_count,
        )

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

    def test_gate_allows_independent_work_in_its_milestone(self) -> None:
        data = copy.deepcopy(self.data)
        task = copy.deepcopy(_task(data, "PWS-0814"))
        task.update(id="PWS-9999", depends_on=[], status="ready", blocker=None)
        cast(list[dict[str, Any]], data["tasks"]).append(task)

        self.assertEqual(set(), self.issue_codes(data))

    def test_product_completion_cannot_require_publisher_code_signing(self) -> None:
        for use_signing_requirement in (True, None):
            with self.subTest(requirement=use_signing_requirement):
                data = copy.deepcopy(self.data)
                target = data["release_targets"]["v1.0"]
                if use_signing_requirement is None:
                    target.pop("publisher_code_signing_required")
                else:
                    target["publisher_code_signing_required"] = use_signing_requirement

                self.assertIn("RELEASE_UNSIGNED_PATH_REQUIRED", self.issue_codes(data))

    def test_unsigned_gate_can_complete_with_optional_signing_outside_its_closure(self) -> None:
        data = copy.deepcopy(self.data)
        signing = copy.deepcopy(_task(data, "PWS-0814"))
        signing.update(
            id="PWS-9999",
            title="Prepare a separately authorized signed distribution variant",
            depends_on=[],
            risk_tags=["publisher-code-signing"],
            status="deferred",
        )
        cast(list[dict[str, Any]], data["tasks"]).append(signing)
        for task_id in ("PWS-0802", "PWS-0807", "PWS-0808"):
            task = _task(data, task_id)
            task["status"] = "done"
            task["evidence"] = {
                "completed_at": "2026-11-26",
                "revision": "fixture:unsigned-candidate",
                "commands": ["Synthetic complete exact-candidate evidence"],
                "notes": ["Unsigned with verified inventory and checksums"],
            }

        self.assertEqual(set(), self.issue_codes(data))

        signing["status"] = "done"
        for task_id in ("PWS-0808", "PWS-0802"):
            with self.subTest(dependent=task_id):
                dependencies = _task(data, task_id)["depends_on"]
                dependencies.append("PWS-9999")
                self.assertIn("RELEASE_PUBLISHER_SIGNING_DEPENDENCY", self.issue_codes(data))
                dependencies.remove("PWS-9999")

        _task(data, "PWS-0807")["status"] = "in_progress"
        self.assertIn("TASK_DEPENDENCY_INCOMPLETE", self.issue_codes(data))

    def test_gate_kind_must_match_milestone(self) -> None:
        data = copy.deepcopy(self.data)
        _task(data, "PWS-0010")["kind"] = "implementation"

        self.assertIn("MILESTONE_GATE_KIND", self.issue_codes(data))

    def test_release_target_must_use_milestone_gate(self) -> None:
        data = copy.deepcopy(self.data)
        data["release_targets"]["v0.1"]["gate_task"] = "PWS-0010"

        self.assertIn("RELEASE_GATE_MISMATCH", self.issue_codes(data))

    def test_pending_prerequisite_blocks_completion_only(self) -> None:
        data = copy.deepcopy(self.data)
        prerequisite = copy.deepcopy(_task(data, "PWS-0001"))
        prerequisite.update(id="PWS-9998", status="planned")
        dependent = copy.deepcopy(prerequisite)
        dependent.update(id="PWS-9999", depends_on=["PWS-9998"], status="in_progress")
        cast(list[dict[str, Any]], data["tasks"]).extend((prerequisite, dependent))

        self.assertEqual(set(), self.issue_codes(data))

        dependent["status"] = "done"

        self.assertEqual({"TASK_DEPENDENCY_INCOMPLETE"}, self.issue_codes(data))

    def test_public_release_requires_the_independent_redaction_gate(self) -> None:
        data = copy.deepcopy(self.data)
        _task(data, "PWS-0808")["depends_on"].remove("PWS-0608")

        self.assertIn("RELEASE_GATE_HISTORY_MISSING", self.issue_codes(data))

    def test_blocked_task_requires_blocker_record(self) -> None:
        data = copy.deepcopy(self.data)
        _task(data, "PWS-0001")["status"] = "blocked"

        self.assertIn("TASK_BLOCKER_MISSING", self.issue_codes(data))

    def test_done_task_requires_completion_evidence(self) -> None:
        data = copy.deepcopy(self.data)
        task = _task(data, "PWS-0002")
        task["status"] = "done"
        task["evidence"] = {
            "completed_at": None,
            "revision": None,
            "commands": [],
            "notes": [],
        }

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
        tasks = cast(list[dict[str, Any]], self.data["tasks"])
        dependency_count = sum(len(cast(list[object], task["depends_on"])) for task in tasks)
        self.assertIn(f"{len(tasks)} tasks, {dependency_count} dependencies", stdout.getvalue())
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


class X64ReleaseScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _load_mapping(ROOT / "TASKS.yaml", yaml_input=True)
        self.schema = _load_mapping(ROOT / "TASKS.schema.json", yaml_input=False)
        decisions = (ROOT / "DECISIONS.md").read_text(encoding="utf-8")
        self.decisions = re.sub(r"(?ms)^## PWS-D043\b.*?(?=^## PWS-D|\Z)", "", decisions)
        self.decisions += "\n## PWS-D043 — Windows x64 only\n\n**Status:** FROZEN\n"
        for task_id in ("PWS-0802", "PWS-0803", "PWS-0808"):
            task = _task(self.data, task_id)
            task["decision_refs"] = sorted(set(task["decision_refs"]) | {"PWS-D043"})
            task["objective"] = "Qualify the Windows 11 x64-only product."
            for field in ("deliverables", "acceptance_criteria", "verification"):
                task[field] = ["Windows 11 x64-only release evidence."]
        _task(self.data, "PWS-0802").update(
            status="blocked",
            blocker={
                "reason": "An isolated test environment is unavailable.",
                "evidence": ["Synthetic blocker for architecture-scope tests."],
                "requested_action": "Prepare the existing isolated Windows 11 x64 test target.",
            },
        )
        # This fixture represents the period before installer qualification.
        for task_id in ("PWS-0807", "PWS-0808"):
            _task(self.data, task_id)["status"] = "planned"

    def codes(self) -> set[str]:
        return {issue.code for issue in validate_data(self.data, self.schema, self.decisions)}

    def test_explicit_out_of_scope_statement_is_allowed(self) -> None:
        _task(self.data, "PWS-0802")["acceptance_criteria"].append(
            "ARM64 native support and x64 emulation are outside project scope."
        )
        self.assertEqual(set(), self.codes())

    def test_arm_prerequisites_are_rejected_in_each_current_requirement_field(self) -> None:
        for task_id in ("PWS-0802", "PWS-0803", "PWS-0808"):
            for field in ("objective", "deliverables", "acceptance_criteria", "verification"):
                with self.subTest(task=task_id, field=field):
                    task = _task(self.data, task_id)
                    old = task[field]
                    requirement = "ARM64 qualification must pass before public release."
                    task[field] = requirement if field == "objective" else [requirement]
                    self.assertIn("TASK_ARCHITECTURE_SCOPE", self.codes())
                    task[field] = old

    def test_current_blocker_cannot_reintroduce_an_arm_prerequisite(self) -> None:
        blocker = _task(self.data, "PWS-0802")["blocker"]
        for field in ("reason", "requested_action"):
            with self.subTest(field=field):
                old = blocker[field]
                blocker[field] = "Acquire an ARM64 environment before PWS-0803 can proceed."
                self.assertIn("TASK_ARCHITECTURE_SCOPE", self.codes())
                blocker[field] = old

    def test_out_of_scope_until_qualified_is_not_an_exclusion(self) -> None:
        _task(self.data, "PWS-0802")["verification"].append(
            "ARM is outside scope until it is qualified."
        )
        self.assertIn("TASK_ARCHITECTURE_SCOPE", self.codes())

    def test_negative_clause_cannot_hide_positive_arm_support(self) -> None:
        for statement in (
            "ARM64 is outside scope, but support is required before release.",
            "ARM64 is not supported; ARM64 qualification must pass.",
        ):
            with self.subTest(statement=statement):
                _task(self.data, "PWS-0803")["acceptance_criteria"] = [statement]
                self.assertIn("TASK_ARCHITECTURE_SCOPE", self.codes())

    def test_historical_evidence_and_upstream_architecture_facts_remain_allowed(self) -> None:
        task = _task(self.data, "PWS-0802")
        task["evidence"]["notes"].append("Historical ARM64 emulation plan was not qualified.")
        task["blocker"]["evidence"].append("Upstream Inno Setup includes ARM64 examples.")
        self.assertEqual(set(), self.codes())

    def test_scope_decision_reference_is_required(self) -> None:
        for task_id in ("PWS-0802", "PWS-0803", "PWS-0808"):
            with self.subTest(task=task_id):
                task = _task(self.data, task_id)
                task["decision_refs"].remove("PWS-D043")
                self.assertIn("TASK_ARCHITECTURE_DECISION", self.codes())
                task["decision_refs"].append("PWS-D043")

    def test_historical_graph_before_adoption_keeps_its_original_contract(self) -> None:
        self.decisions = re.sub(r"(?ms)^## PWS-D043\b.*?(?=^## PWS-D|\Z)", "", self.decisions)
        for task in self.data["tasks"]:
            task["decision_refs"] = [d for d in task["decision_refs"] if d != "PWS-D043"]
        _task(self.data, "PWS-0802")["verification"].append("Qualify ARM64 emulation.")
        self.assertEqual(set(), self.codes())

    def test_repository_checks_current_product_and_packaging_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "TASKS.yaml").write_text(yaml.safe_dump(self.data), encoding="utf-8")
            (root / "TASKS.schema.json").write_text(json.dumps(self.schema), encoding="utf-8")
            (root / "DECISIONS.md").write_text(self.decisions, encoding="utf-8")
            names = (
                "packaging/windows/product-v1.json",
                "packaging/windows/paperworkshell.iss",
                "schemas/dependency-inventory-v1.schema.json",
                "schemas/resolved-dependency-inventory-v1.schema.json",
            )
            for name in names:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, root / name)
            product_path = root / names[0]
            product = json.loads(product_path.read_bytes())
            product["arm64_route"] = (
                "not supported; native ARM64 and x64 emulation are outside project scope"
            )
            product_path.write_text(json.dumps(product), encoding="utf-8")
            _, issues = validate_repository(root)
            self.assertEqual([], issues)
            for name, old, new in (
                (names[0], '"architecture": "x64"', '"architecture": "ARM64"'),
                (names[1], "ArchitecturesAllowed=x64os", "ArchitecturesAllowed=x64compatible"),
                (names[2], '"const": "x86_64"', '"const": "ARM64"'),
                (names[3], '"const": "x86_64"', '"const": "ARM64"'),
            ):
                with self.subTest(name=name):
                    target = root / name
                    original = target.read_text(encoding="utf-8")
                    self.assertIn(old, original)
                    target.write_text(original.replace(old, new), encoding="utf-8")
                    _, issues = validate_repository(root)
                    self.assertIn("REPOSITORY_ARCHITECTURE_SCOPE", {i.code for i in issues})
                    target.write_text(original, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()

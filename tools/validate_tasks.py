"""Validate the PaperWorkShell task schema and dependency graph."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

TASK_FILE: Final = "TASKS.yaml"
SCHEMA_FILE: Final = "TASKS.schema.json"
DECISIONS_FILE: Final = "DECISIONS.md"
DECISION_HEADING: Final = re.compile(r"^## (PWS-D[0-9]{3})\b", re.MULTILINE)
ACTIVE_STATUSES: Final = frozenset({"ready", "in_progress", "review", "done", "blocked"})
EXPECTED_LISTS: Final[dict[str, list[str]]] = {
    "status_values": ["planned", "ready", "in_progress", "review", "done", "blocked", "deferred"],
    "priority_order": ["P0", "P1", "P2"],
    "effort_values": ["S", "M", "L", "XL"],
}
REQUIRED_GRAPH_RULES: Final = (
    "one_task_per_change",
    "gate_requires_all_dependencies_done",
    "decision_refs_must_exist",
    "cycles_forbidden",
)
X64_SCOPE_DECISION: Final = "PWS-D043"
X64_SCOPE_TASKS: Final = frozenset({"PWS-0802", "PWS-0803", "PWS-0808"})
_ARM_SCOPE: Final = re.compile(r"\b(?:arm(?:64)?|aarch64|x64\s+emulation)\b", re.IGNORECASE)
_SCOPE_EXCLUSION: Final = re.compile(
    r"\b(?:outside (?:the )?(?:project )?scope|out of scope|not supported|"
    r"not required|not (?:an? )?prerequisite)\b",
    re.IGNORECASE,
)
_NEGATIVE_REQUIREMENT: Final = re.compile(
    r"\bnot (?:supported|required|enabled|(?:an? )?prerequisites?)\b", re.IGNORECASE
)
_DEFERRED_OR_REQUIRED_SCOPE: Final = re.compile(
    r"\b(?:until|pending|must|shall|will|needs?|before|after|if|unless|but|however|"
    r"except|although|yet|supported|enabled|required|requires?|prerequisites?|qualified)\b",
    re.IGNORECASE,
)


class DuplicateKeyError(yaml.YAMLError):
    """Raised when a YAML mapping repeats a key."""


class UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: UniqueKeyLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    loader.flatten_mapping(node)
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from error
        if duplicate:
            raise DuplicateKeyError(f"duplicate YAML key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True, order=True)
class ValidationIssue:
    """One deterministic validation error."""

    code: str
    path: str
    message: str

    def render(self) -> str:
        return f"ERROR [{self.code}] {self.path}: {self.message}"


@dataclass(frozen=True)
class ValidationSummary:
    """Counts emitted after successful validation."""

    task_count: int
    dependency_count: int
    milestone_count: int
    decision_count: int


class RepositoryInputError(Exception):
    """A stable, expected repository input failure."""

    def __init__(self, issue: ValidationIssue) -> None:
        super().__init__(issue.message)
        self.issue = issue


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise RepositoryInputError(
            ValidationIssue("INPUT_UNREADABLE", path.name, str(error))
        ) from error


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        loaded = yaml.load(_read_text(path), Loader=UniqueKeyLoader)
    except DuplicateKeyError as error:
        raise RepositoryInputError(
            ValidationIssue("YAML_DUPLICATE_KEY", path.name, str(error))
        ) from error
    except yaml.YAMLError as error:
        raise RepositoryInputError(
            ValidationIssue("YAML_INVALID", path.name, str(error).splitlines()[0])
        ) from error
    if not isinstance(loaded, dict):
        raise RepositoryInputError(
            ValidationIssue("YAML_ROOT_TYPE", path.name, "root value must be a mapping")
        )
    return cast(dict[str, Any], loaded)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(_read_text(path))
    except json.JSONDecodeError as error:
        raise RepositoryInputError(
            ValidationIssue("JSON_INVALID", path.name, f"line {error.lineno}: {error.msg}")
        ) from error
    if not isinstance(loaded, dict):
        raise RepositoryInputError(
            ValidationIssue("JSON_ROOT_TYPE", path.name, "root value must be an object")
        )
    return cast(dict[str, Any], loaded)


def _json_path(parts: Iterable[object]) -> str:
    rendered = "$"
    for part in parts:
        if isinstance(part, int):
            rendered += f"[{part}]"
        else:
            rendered += f".{part}"
    return rendered


def schema_issues(data: Mapping[str, Any], schema: Mapping[str, Any]) -> list[ValidationIssue]:
    """Return JSON Schema definition and instance errors."""

    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        return [
            ValidationIssue(
                "SCHEMA_DEFINITION_INVALID",
                _json_path(error.absolute_schema_path),
                error.message,
            )
        ]

    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(
        validator.iter_errors(data),
        key=lambda error: (_json_path(error.absolute_path), error.message),
    )
    return [
        ValidationIssue("SCHEMA_VALIDATION", _json_path(error.absolute_path), error.message)
        for error in errors
    ]


def _indexed_records(
    records: Sequence[Mapping[str, Any]],
    record_type: str,
    issues: list[ValidationIssue],
) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for index, record in enumerate(records):
        record_id = cast(str, record["id"])
        if record_id in indexed:
            issues.append(
                ValidationIssue(
                    f"{record_type.upper()}_ID_DUPLICATE",
                    f"$.{record_type}s[{index}].id",
                    f"{record_id} is already defined",
                )
            )
        else:
            indexed[record_id] = record
    return indexed


def _cycle_issues(tasks: Mapping[str, Mapping[str, Any]]) -> list[ValidationIssue]:
    colors: dict[str, int] = {task_id: 0 for task_id in tasks}
    stack: list[str] = []
    reported: set[tuple[str, ...]] = set()
    issues: list[ValidationIssue] = []

    def visit(task_id: str) -> None:
        colors[task_id] = 1
        stack.append(task_id)
        dependencies = cast(list[str], tasks[task_id]["depends_on"])
        for dependency in sorted(dependencies):
            if dependency not in tasks:
                continue
            if colors[dependency] == 0:
                visit(dependency)
            elif colors[dependency] == 1:
                start = stack.index(dependency)
                cycle = tuple((*stack[start:], dependency))
                if cycle not in reported:
                    reported.add(cycle)
                    issues.append(
                        ValidationIssue(
                            "TASK_GRAPH_CYCLE",
                            "$.tasks",
                            " -> ".join(cycle),
                        )
                    )
        stack.pop()
        colors[task_id] = 2

    for task_id in sorted(tasks):
        if colors[task_id] == 0:
            visit(task_id)
    return issues


def _transitive_dependencies(
    task_id: str,
    tasks: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    found: set[str] = set()
    pending = list(cast(list[str], tasks[task_id]["depends_on"]))
    while pending:
        dependency = pending.pop()
        if dependency in found or dependency not in tasks:
            continue
        found.add(dependency)
        pending.extend(cast(list[str], tasks[dependency]["depends_on"]))
    return found


def _x64_scope_adopted(decisions_text: str) -> bool:
    section = re.search(r"(?ms)^## PWS-D043\b.*?(?=^## PWS-D|\Z)", decisions_text)
    return section is not None and "**Status:** FROZEN" in section.group()


def _x64_task_scope_issues(
    tasks: Sequence[Mapping[str, Any]], decisions_text: str
) -> list[ValidationIssue]:
    """Check current requirements only after the new scope decision is adopted.

    Evidence and blocker.evidence are intentionally historical records. Upstream
    inventories and older governance snapshots are not current product promises.
    """
    if not _x64_scope_adopted(decisions_text):
        return []
    issues: list[ValidationIssue] = []
    for index, task in enumerate(tasks):
        if task["id"] not in X64_SCOPE_TASKS or task["status"] == "deferred":
            continue
        path = f"$.tasks[{index}]"
        if X64_SCOPE_DECISION not in task["decision_refs"]:
            issues.append(
                ValidationIssue(
                    "TASK_ARCHITECTURE_DECISION",
                    path + ".decision_refs",
                    f"{task['id']} must reference {X64_SCOPE_DECISION}",
                )
            )
        fields: list[tuple[str, str]] = [("objective", task["objective"])]
        for name in ("deliverables", "acceptance_criteria", "verification"):
            fields.extend((f"{name}[{i}]", text) for i, text in enumerate(task[name]))
        if task["blocker"] is not None:
            fields.extend(
                (f"blocker.{name}", task["blocker"][name])
                for name in ("reason", "requested_action")
            )
        for field, text in fields:
            # An explicit exclusion is allowed. Conditional/deferred support or
            # a positive requirement in that same sentence remains a refusal.
            for sentence in re.split(r"[.!?;\n]", text):
                if not _ARM_SCOPE.search(sentence):
                    continue
                remainder = _NEGATIVE_REQUIREMENT.sub("", sentence)
                if not _SCOPE_EXCLUSION.search(sentence) or _DEFERRED_OR_REQUIRED_SCOPE.search(
                    remainder
                ):
                    issues.append(
                        ValidationIssue(
                            "TASK_ARCHITECTURE_SCOPE",
                            path + "." + field,
                            "current requirements must keep ARM native and emulation out of scope",
                        )
                    )
                    break
    return issues


def _x64_repository_scope_issues(root: Path) -> list[ValidationIssue]:
    """Join current product, target schemas and installer policy without artifacts.

    Immutable R7 metadata is validated by its retained build-epoch tools/receipts,
    not by loading it as the current source product configuration.
    """
    product_path = "packaging/windows/product-v1.json"
    try:
        product = _load_json(root / product_path)
        expected = {
            "schema_version": 1,
            "os": "Windows 11",
            "minimum_os_build": 22000,
            "architecture": "x64",
            "arm64_route": (
                "not supported; native ARM64 and x64 emulation are outside project scope"
            ),
        }
        if any(
            type(product.get(k)) is not type(v) or product.get(k) != v for k, v in expected.items()
        ):
            raise ValueError
        script = _read_text(root / "packaging/windows/paperworkshell.iss")
        directives = [
            line.strip()
            for line in script.splitlines()
            if line.strip().startswith(
                ("ArchitecturesAllowed=", "ArchitecturesInstallIn64BitMode=")
            )
        ]
        if sorted(directives) != [
            "ArchitecturesAllowed=x64os",
            "ArchitecturesInstallIn64BitMode=x64os",
        ]:
            raise ValueError
        for name in ("dependency-inventory-v1", "resolved-dependency-inventory-v1"):
            schema = _load_json(root / f"schemas/{name}.schema.json")
            target = schema["properties"]["target"]
            if "$ref" in target:
                target = schema["$defs"]["target"]
            if target["properties"]["architecture"] != {"const": "x86_64"}:
                raise ValueError
    except RepositoryInputError, KeyError, TypeError, ValueError:
        return [
            ValidationIssue(
                "REPOSITORY_ARCHITECTURE_SCOPE",
                product_path,
                "current product, dependency targets and installer must agree on native Windows 11 x64 only",
            )
        ]
    return []


def semantic_issues(data: Mapping[str, Any], decisions_text: str) -> list[ValidationIssue]:
    """Return cross-record, dependency, gate, and evidence errors."""

    issues: list[ValidationIssue] = []
    milestones = cast(list[Mapping[str, Any]], data["milestones"])
    tasks = cast(list[Mapping[str, Any]], data["tasks"])
    milestone_map = _indexed_records(milestones, "milestone", issues)
    task_map = _indexed_records(tasks, "task", issues)
    issues.extend(_x64_task_scope_issues(tasks, decisions_text))

    decision_matches = DECISION_HEADING.findall(decisions_text)
    decision_counts = Counter(decision_matches)
    decision_ids = set(decision_matches)
    for decision_id, count in sorted(decision_counts.items()):
        if count > 1:
            issues.append(
                ValidationIssue(
                    "DECISION_ID_DUPLICATE",
                    DECISIONS_FILE,
                    f"{decision_id} appears {count} times",
                )
            )

    for field, expected in EXPECTED_LISTS.items():
        if data[field] != expected:
            issues.append(
                ValidationIssue(
                    "CONTRACT_ENUM_MISMATCH",
                    f"$.{field}",
                    f"expected {expected!r}",
                )
            )

    graph_rules = cast(Mapping[str, Any], data["graph_rules"])
    for rule in REQUIRED_GRAPH_RULES:
        if graph_rules[rule] is not True:
            issues.append(
                ValidationIssue(
                    "GRAPH_RULE_DISABLED",
                    f"$.graph_rules.{rule}",
                    "binding graph rule must be true",
                )
            )

    for index, task in enumerate(tasks):
        task_id = cast(str, task["id"])
        milestone_id = cast(str, task["milestone"])
        dependencies = cast(list[str], task["depends_on"])
        if milestone_id not in milestone_map:
            issues.append(
                ValidationIssue(
                    "TASK_MILESTONE_UNKNOWN",
                    f"$.tasks[{index}].milestone",
                    f"{milestone_id} does not exist",
                )
            )
        for dependency_index, dependency in enumerate(dependencies):
            dependency_path = f"$.tasks[{index}].depends_on[{dependency_index}]"
            if dependency == task_id:
                issues.append(
                    ValidationIssue(
                        "TASK_DEPENDENCY_SELF",
                        dependency_path,
                        f"{task_id} cannot depend on itself",
                    )
                )
            elif dependency not in task_map:
                issues.append(
                    ValidationIssue(
                        "TASK_DEPENDENCY_UNKNOWN",
                        dependency_path,
                        f"{dependency} does not exist",
                    )
                )
        for decision_index, decision_id in enumerate(cast(list[str], task["decision_refs"])):
            if decision_id not in decision_ids:
                issues.append(
                    ValidationIssue(
                        "TASK_DECISION_UNKNOWN",
                        f"$.tasks[{index}].decision_refs[{decision_index}]",
                        f"{decision_id} does not exist in {DECISIONS_FILE}",
                    )
                )

        status = cast(str, task["status"])
        known_dependencies = [dependency for dependency in dependencies if dependency in task_map]
        incomplete = [
            dependency
            for dependency in known_dependencies
            if task_map[dependency]["status"] != "done"
        ]
        if status in ACTIVE_STATUSES and incomplete:
            issues.append(
                ValidationIssue(
                    "TASK_DEPENDENCY_INCOMPLETE",
                    f"$.tasks[{index}].status",
                    f"{task_id} is {status} but dependencies are not done: {', '.join(incomplete)}",
                )
            )

        blocker = task["blocker"]
        if status == "blocked" and blocker is None:
            issues.append(
                ValidationIssue(
                    "TASK_BLOCKER_MISSING",
                    f"$.tasks[{index}].blocker",
                    f"{task_id} is blocked without blocker evidence",
                )
            )
        elif status != "blocked" and blocker is not None:
            issues.append(
                ValidationIssue(
                    "TASK_BLOCKER_STALE",
                    f"$.tasks[{index}].blocker",
                    f"{task_id} is {status} but retains blocker evidence",
                )
            )

        if status == "done":
            evidence = cast(Mapping[str, Any], task["evidence"])
            missing_fields = [
                field for field in ("completed_at", "revision") if evidence[field] is None
            ]
            if not cast(list[str], evidence["commands"]):
                missing_fields.append("commands")
            if missing_fields:
                issues.append(
                    ValidationIssue(
                        "TASK_DONE_EVIDENCE_MISSING",
                        f"$.tasks[{index}].evidence",
                        f"{task_id} is done without {', '.join(missing_fields)}",
                    )
                )

    issues.extend(_cycle_issues(task_map))

    gate_claims: Counter[str] = Counter()
    for index, milestone in enumerate(milestones):
        milestone_id = cast(str, milestone["id"])
        gate_id = cast(str, milestone["gate_task"])
        gate_claims[gate_id] += 1
        gate = task_map.get(gate_id)
        if gate is None:
            issues.append(
                ValidationIssue(
                    "MILESTONE_GATE_UNKNOWN",
                    f"$.milestones[{index}].gate_task",
                    f"{gate_id} does not exist",
                )
            )
            continue
        if gate["kind"] != "gate":
            issues.append(
                ValidationIssue(
                    "MILESTONE_GATE_KIND",
                    f"$.milestones[{index}].gate_task",
                    f"{gate_id} is not a gate task",
                )
            )
        if gate["milestone"] != milestone_id:
            issues.append(
                ValidationIssue(
                    "MILESTONE_GATE_ASSIGNMENT",
                    f"$.milestones[{index}].gate_task",
                    f"{gate_id} belongs to {gate['milestone']}, not {milestone_id}",
                )
            )

        expected_dependencies = {
            cast(str, task["id"])
            for task in tasks
            if task["milestone"] == milestone_id and task["kind"] != "gate"
        }
        actual_dependencies = set(cast(list[str], gate["depends_on"]))
        missing_dependencies = sorted(expected_dependencies - actual_dependencies)
        if missing_dependencies:
            issues.append(
                ValidationIssue(
                    "MILESTONE_GATE_COVERAGE",
                    f"$.milestones[{index}].gate_task",
                    f"{gate_id} omits: {', '.join(missing_dependencies)}",
                )
            )

    for task_id, task in sorted(task_map.items()):
        if task["kind"] == "gate" and gate_claims[task_id] != 1:
            issues.append(
                ValidationIssue(
                    "GATE_CLAIM_COUNT",
                    f"$.tasks.{task_id}",
                    f"gate is claimed by {gate_claims[task_id]} milestones",
                )
            )

    milestone_order = [cast(str, milestone["id"]) for milestone in milestones]
    release_targets = cast(Mapping[str, Mapping[str, Any]], data["release_targets"])
    for release_name, release in sorted(release_targets.items()):
        milestone_id = cast(str, release["through_milestone"])
        gate_id = cast(str, release["gate_task"])
        if milestone_id not in milestone_map:
            issues.append(
                ValidationIssue(
                    "RELEASE_MILESTONE_UNKNOWN",
                    f"$.release_targets.{release_name}.through_milestone",
                    f"{milestone_id} does not exist",
                )
            )
            continue
        expected_gate = cast(str, milestone_map[milestone_id]["gate_task"])
        if gate_id != expected_gate:
            issues.append(
                ValidationIssue(
                    "RELEASE_GATE_MISMATCH",
                    f"$.release_targets.{release_name}.gate_task",
                    f"expected {expected_gate} for {milestone_id}, found {gate_id}",
                )
            )
        if gate_id not in task_map:
            continue
        ancestors = _transitive_dependencies(gate_id, task_map)
        milestone_index = milestone_order.index(milestone_id)
        for previous_id in milestone_order[:milestone_index]:
            previous_gate = cast(str, milestone_map[previous_id]["gate_task"])
            if previous_gate not in ancestors:
                issues.append(
                    ValidationIssue(
                        "RELEASE_GATE_HISTORY_MISSING",
                        f"$.release_targets.{release_name}.gate_task",
                        f"{gate_id} does not transitively depend on {previous_gate}",
                    )
                )

    return sorted(set(issues))


def validate_data(
    data: Mapping[str, Any],
    schema: Mapping[str, Any],
    decisions_text: str,
    *,
    schema_only: bool = False,
) -> list[ValidationIssue]:
    """Validate already-loaded repository data."""

    issues = schema_issues(data, schema)
    if issues or schema_only:
        return issues
    return semantic_issues(data, decisions_text)


def validate_repository(
    root: Path,
    *,
    schema_only: bool = False,
) -> tuple[ValidationSummary | None, list[ValidationIssue]]:
    """Load and validate repository task inputs from ``root``."""

    try:
        data = _load_yaml(root / TASK_FILE)
        schema = _load_json(root / SCHEMA_FILE)
        decisions_text = "" if schema_only else _read_text(root / DECISIONS_FILE)
    except RepositoryInputError as error:
        return None, [error.issue]

    issues = validate_data(data, schema, decisions_text, schema_only=schema_only)
    if not schema_only and _x64_scope_adopted(decisions_text):
        issues.extend(_x64_repository_scope_issues(root))
    if issues:
        return None, issues

    tasks = cast(list[Mapping[str, Any]], data["tasks"])
    milestones = cast(list[Mapping[str, Any]], data["milestones"])
    summary = ValidationSummary(
        task_count=len(tasks),
        dependency_count=sum(len(cast(list[str], task["depends_on"])) for task in tasks),
        milestone_count=len(milestones),
        decision_count=len(set(DECISION_HEADING.findall(decisions_text))),
    )
    return summary, []


def main(argv: Sequence[str] | None = None) -> int:
    """Run the repository validator CLI."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="repository root (default: current directory)",
    )
    parser.add_argument(
        "--schema-only",
        action="store_true",
        help="validate YAML and JSON Schema without semantic graph checks",
    )
    arguments = parser.parse_args(argv)
    summary, issues = validate_repository(arguments.root, schema_only=arguments.schema_only)
    if issues:
        for issue in issues:
            print(issue.render(), file=sys.stderr)
        return 1

    assert summary is not None
    if arguments.schema_only:
        print(f"OK: {TASK_FILE} conforms to {SCHEMA_FILE} ({summary.task_count} tasks).")
    else:
        print(
            f"OK: {TASK_FILE} schema and graph are valid "
            f"({summary.task_count} tasks, {summary.dependency_count} dependencies, "
            f"{summary.milestone_count} milestones, {summary.decision_count} decisions)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

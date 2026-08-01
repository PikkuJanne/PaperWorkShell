"""Validate local documentation links and PaperWorkShell traceability offline."""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final
from urllib.parse import unquote, urlsplit

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
DOCUMENTATION_TREES: Final = ("docs", "benchmarks", "packaging", "schemas")
DECISIONS_PATH: Final = Path("DECISIONS.md")
TASKS_PATH: Final = Path("TASKS.yaml")
THREAT_MODEL_PATH: Final = Path("docs/security/threat-model.md")
TRACEABILITY_PATH: Final = Path("docs/security/threat-traceability.md")
TEST_PLAN_PATH: Final = Path("docs/security/security-test-plan.md")

DECISION_DEFINITION: Final = re.compile(r"^##[ \t]+(PWS-D[0-9]{3})\b", re.MULTILINE)
TASK_DEFINITION: Final = re.compile(r"^- id:[ \t]+[\"']?(PWS-[0-9]{4})[\"']?[ \t]*$", re.MULTILINE)
DECISION_REFERENCE: Final = re.compile(r"(?<![A-Za-z0-9-])PWS-D[0-9]{3}(?![A-Za-z0-9-])")
TASK_REFERENCE: Final = re.compile(r"(?<![A-Za-z0-9-])PWS-[0-9]{4}(?![A-Za-z0-9-])")
THREAT_REFERENCE: Final = re.compile(r"(?<![A-Za-z0-9-])TM-[0-9]{3}(?![A-Za-z0-9-])")
TEST_REFERENCE: Final = re.compile(r"(?<![A-Za-z0-9-])ST-[0-9]{3}(?![A-Za-z0-9-])")
ATX_HEADING: Final = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(?P<title>.*?)[ \t]*$")
FENCE: Final = re.compile(r"^[ \t]{0,3}(?P<marker>`{3,}|~{3,})(?P<rest>.*)$")
INLINE_LINK: Final = re.compile(
    r"!?\[[^\]\n]*\]\(\s*(?P<target><[^>\n]*>|[^)\s]+)"
    r"(?:\s+(?:\"[^\"\n]*\"|'[^'\n]*'|\([^)]*\)))?\s*\)"
)
WINDOWS_DRIVE_PATH: Final = re.compile(r"^[A-Za-z]:")
TABLE_SEPARATOR_CELL: Final = re.compile(r"^:?-{3,}:?$")

ALLOWED_EXTERNAL_SCHEMES: Final = frozenset({"http", "https", "mailto"})
ALLOWED_IMPACTS: Final = frozenset({"low", "medium", "high", "critical"})

# These stable catalogue entries prove the eleven categories explicitly required by PWS-0004.
# A scenario must retain the listed plain-language terms; a matching identifier alone is not enough.
MANDATED_THREAT_CATEGORIES: Final[dict[str, tuple[str, ...]]] = {
    "TM-001": ("parser", "exploit"),
    "TM-002": ("exhaust",),
    "TM-005": ("modif", "original"),
    "TM-009": ("inject", "command"),
    "TM-010": ("template", "execute"),
    "TM-011": ("log", "disclose"),
    "TM-012": ("temp", "persist"),
    "TM-014": ("corrupt", "export"),
    "TM-016": ("invalidat", "signature"),
    "TM-017": ("redaction", "recoverable"),
    "TM-020": ("dependency", "compromised"),
}


@dataclass(frozen=True)
class DocumentationIssue:
    """One stable, path-sanitized documentation validation error."""

    code: str
    path: str
    line: int
    message: str

    def render(self) -> str:
        location = self.path if self.line <= 0 else f"{self.path}:{self.line}"
        return f"ERROR [{self.code}] {location}: {self.message}"


@dataclass(frozen=True)
class VisibleLine:
    """One Markdown source line outside fenced code."""

    number: int
    text: str


@dataclass(frozen=True)
class ThreatRecord:
    """One canonical threat-catalogue row."""

    threat_id: str
    scenario: str
    impact: str
    line: int


def _relative_label(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return "<outside-repository>"


def _issue(
    code: str,
    root: Path,
    path: Path,
    line: int,
    message: str,
) -> DocumentationIssue:
    return DocumentationIssue(code, _relative_label(root, path), line, message)


def _read_utf8(root: Path, path: Path) -> tuple[str | None, DocumentationIssue | None]:
    try:
        return path.read_text(encoding="utf-8"), None
    except UnicodeError:
        return None, _issue(
            "DOC_TEXT_ENCODING_INVALID",
            root,
            path,
            0,
            "file must be valid UTF-8",
        )
    except OSError as error:
        return None, _issue(
            "DOC_INPUT_UNREADABLE",
            root,
            path,
            0,
            f"could not read required input ({type(error).__name__})",
        )


def _visible_lines(text: str) -> tuple[VisibleLine, ...]:
    visible: list[VisibleLine] = []
    fence_character: str | None = None
    fence_length = 0
    for number, line in enumerate(text.splitlines(), start=1):
        match = FENCE.match(line)
        if fence_character is None:
            if match is not None:
                marker = match.group("marker")
                fence_character = marker[0]
                fence_length = len(marker)
                continue
            visible.append(VisibleLine(number, line))
            continue

        if match is None:
            continue
        marker = match.group("marker")
        if (
            marker[0] == fence_character
            and len(marker) >= fence_length
            and not match.group("rest").strip()
        ):
            fence_character = None
            fence_length = 0
    return tuple(visible)


def _heading_slug(title: str) -> str:
    # Repository documentation uses ATX headings. This is the GitHub-style subset needed by
    # those headings: formatting punctuation is removed, Unicode letters/numbers are retained,
    # and each whitespace character becomes a hyphen.
    without_closing_hashes = re.sub(r"[ \t]+#+[ \t]*$", "", title)
    without_markup = re.sub(r"<[^>]*>", "", without_closing_hashes).replace("`", "")
    slug_characters: list[str] = []
    for character in without_markup.strip().lower():
        category = unicodedata.category(character)
        if character.isspace():
            slug_characters.append("-")
        elif character in {"-", "_"} or category[0] in {"L", "M", "N"}:
            slug_characters.append(character)
    return "".join(slug_characters)


def _heading_anchors(lines: Iterable[VisibleLine]) -> frozenset[str]:
    anchors: set[str] = set()
    occurrences: Counter[str] = Counter()
    for line in lines:
        match = ATX_HEADING.match(line.text)
        if match is None:
            continue
        base = _heading_slug(match.group("title"))
        if not base:
            continue
        occurrence = occurrences[base]
        occurrences[base] += 1
        anchors.add(base if occurrence == 0 else f"{base}-{occurrence}")
    return frozenset(anchors)


def _documentation_paths(root: Path) -> tuple[Path, ...]:
    paths = set(root.glob("*.md"))
    for directory_name in DOCUMENTATION_TREES:
        directory = root / directory_name
        if directory.is_dir():
            paths.update(directory.rglob("*.md"))
    return tuple(sorted(paths, key=lambda path: _relative_label(root, path)))


def _split_table_row(text: str) -> tuple[str, ...] | None:
    stripped = text.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    cells = tuple(cell.strip() for cell in stripped[1:-1].split("|"))
    if cells and all(TABLE_SEPARATOR_CELL.fullmatch(cell) is not None for cell in cells):
        return None
    return cells


def _exact_case_file(root: Path, relative_path: Path) -> tuple[Path | None, str | None]:
    current = root
    for component in relative_path.parts:
        try:
            entries = {entry.name: entry for entry in current.iterdir()}
        except OSError:
            return None, "missing"
        exact = entries.get(component)
        if exact is None:
            if any(name.casefold() == component.casefold() for name in entries):
                return None, "case"
            return None, "missing"
        current = exact
    if not current.is_file():
        return None, "missing"
    return current, None


def _resolved_local_target(
    root: Path,
    source: Path,
    raw_target: str,
) -> tuple[Path | None, str | None, str | None]:
    target = (
        raw_target[1:-1] if raw_target.startswith("<") and raw_target.endswith(">") else raw_target
    )
    if "\\" in target:
        return None, None, "backslash"
    if WINDOWS_DRIVE_PATH.match(target) is not None or target.startswith("//"):
        return None, None, "absolute"

    parsed = urlsplit(target)
    scheme = parsed.scheme.casefold()
    if scheme in ALLOWED_EXTERNAL_SCHEMES:
        return None, None, "external"
    if scheme:
        return None, None, "scheme"
    if parsed.netloc or parsed.path.startswith("/"):
        return None, None, "absolute"
    if parsed.query:
        return None, None, "query"

    decoded_path = unquote(parsed.path)
    fragment = unquote(parsed.fragment)
    if "\x00" in decoded_path or "\x00" in fragment:
        return None, None, "invalid"
    if "\\" in decoded_path:
        return None, None, "backslash"
    if WINDOWS_DRIVE_PATH.match(decoded_path) is not None or decoded_path.startswith("//"):
        return None, None, "absolute"
    if not decoded_path:
        return source, fragment, None

    posix_path = PurePosixPath(decoded_path)
    if posix_path.is_absolute():
        return None, None, "absolute"
    relative_components = list(source.parent.relative_to(root).parts)
    for component in posix_path.parts:
        if component in {"", "."}:
            continue
        if component == "..":
            if not relative_components:
                return None, None, "outside"
            relative_components.pop()
            continue
        relative_components.append(component)
    relative_target = Path(*relative_components)
    exact_target, problem = _exact_case_file(root, relative_target)
    if problem is not None:
        return None, None, problem
    assert exact_target is not None
    try:
        final_target = exact_target.resolve(strict=True)
        resolved_root = root.resolve(strict=True)
    except OSError:
        return None, None, "missing"
    if not final_target.is_relative_to(resolved_root):
        return None, None, "outside"
    return exact_target, fragment, None


def _link_issue(
    root: Path,
    source: Path,
    line: int,
    problem: str,
) -> DocumentationIssue:
    code_and_message = {
        "absolute": ("DOC_LINK_ABSOLUTE", "local link target must be repository-relative"),
        "backslash": ("DOC_LINK_BACKSLASH", "local link target must use forward slashes"),
        "case": ("DOC_LINK_CASE_MISMATCH", "local link target uses incorrect path casing"),
        "invalid": ("DOC_LINK_INVALID", "local link target is invalid"),
        "missing": ("DOC_LINK_TARGET_MISSING", "local link target does not exist as a file"),
        "outside": ("DOC_LINK_OUTSIDE_ROOT", "local link target escapes the repository"),
        "query": ("DOC_LINK_QUERY_UNSUPPORTED", "local link targets cannot contain a query"),
        "scheme": ("DOC_LINK_SCHEME_UNSUPPORTED", "link uses an unsupported URI scheme"),
    }
    code, message = code_and_message[problem]
    return _issue(code, root, source, line, message)


def _canonical_threats(
    root: Path,
    visible_by_path: dict[Path, tuple[VisibleLine, ...]],
    issues: list[DocumentationIssue],
) -> dict[str, ThreatRecord]:
    path = root / THREAT_MODEL_PATH
    records: dict[str, ThreatRecord] = {}
    for line in visible_by_path.get(path, ()):
        cells = _split_table_row(line.text)
        if cells is None or len(cells) != 4 or THREAT_REFERENCE.fullmatch(cells[0]) is None:
            continue
        threat_id, scenario, impact, _outcome = cells
        if threat_id in records:
            issues.append(
                _issue(
                    "THREAT_CATALOGUE_DUPLICATE",
                    root,
                    path,
                    line.number,
                    f"{threat_id} is defined more than once",
                )
            )
            continue
        if impact.casefold() not in ALLOWED_IMPACTS:
            issues.append(
                _issue(
                    "THREAT_IMPACT_INVALID",
                    root,
                    path,
                    line.number,
                    f"{threat_id} has an unsupported impact",
                )
            )
        records[threat_id] = ThreatRecord(threat_id, scenario, impact.casefold(), line.number)

    for threat_id, required_terms in MANDATED_THREAT_CATEGORIES.items():
        record = records.get(threat_id)
        scenario = "" if record is None else record.scenario.casefold()
        if record is None or any(term not in scenario for term in required_terms):
            issues.append(
                _issue(
                    "THREAT_CATEGORY_MISSING",
                    root,
                    path,
                    0 if record is None else record.line,
                    f"{threat_id} does not document its required PWS-0004 threat category",
                )
            )
    if not records:
        issues.append(
            _issue(
                "THREAT_CATALOGUE_MISSING",
                root,
                path,
                0,
                "canonical threat catalogue has no valid rows",
            )
        )
    return records


def _validate_traceability(
    root: Path,
    visible_by_path: dict[Path, tuple[VisibleLine, ...]],
    threats: dict[str, ThreatRecord],
    decisions: frozenset[str],
    tasks: frozenset[str],
) -> list[DocumentationIssue]:
    path = root / TRACEABILITY_PATH
    issues: list[DocumentationIssue] = []
    traced: dict[str, int] = {}
    for line in visible_by_path.get(path, ()):
        cells = _split_table_row(line.text)
        if cells is None or len(cells) != 4 or THREAT_REFERENCE.fullmatch(cells[0]) is None:
            continue
        threat_id, decision_cell, task_cell, _evidence = cells
        if threat_id in traced:
            issues.append(
                _issue(
                    "THREAT_TRACE_DUPLICATE",
                    root,
                    path,
                    line.number,
                    f"{threat_id} has more than one traceability row",
                )
            )
            continue
        traced[threat_id] = line.number
        record = threats.get(threat_id)
        if record is None:
            continue
        mapped_decisions = set(DECISION_REFERENCE.findall(decision_cell)) & decisions
        mapped_tasks = set(TASK_REFERENCE.findall(task_cell)) & tasks
        if record.impact in {"high", "critical"} and (not mapped_decisions or not mapped_tasks):
            issues.append(
                _issue(
                    "THREAT_MAPPING_INCOMPLETE",
                    root,
                    path,
                    line.number,
                    f"{threat_id} requires a known decision and task or gate",
                )
            )

    for threat_id in sorted(threats.keys() - traced.keys()):
        issues.append(
            _issue(
                "THREAT_TRACE_MISSING",
                root,
                path,
                0,
                f"{threat_id} has no traceability row",
            )
        )
    return issues


def _validate_test_plan(
    root: Path,
    visible_by_path: dict[Path, tuple[VisibleLine, ...]],
    threats: dict[str, ThreatRecord],
) -> list[DocumentationIssue]:
    path = root / TEST_PLAN_PATH
    issues: list[DocumentationIssue] = []
    test_lines: dict[str, int] = {}
    covered: set[str] = set()
    for line in visible_by_path.get(path, ()):
        cells = _split_table_row(line.text)
        if cells is None or len(cells) != 5 or TEST_REFERENCE.fullmatch(cells[0]) is None:
            continue
        test_id, threat_cell, _tasks, _scenario, _oracle = cells
        if test_id in test_lines:
            issues.append(
                _issue(
                    "SECURITY_TEST_ID_DUPLICATE",
                    root,
                    path,
                    line.number,
                    f"{test_id} is defined more than once",
                )
            )
        else:
            test_lines[test_id] = line.number

        references = THREAT_REFERENCE.findall(threat_cell)
        counts = Counter(references)
        for threat_id in sorted(threat_id for threat_id, count in counts.items() if count > 1):
            issues.append(
                _issue(
                    "THREAT_REFERENCE_DUPLICATE",
                    root,
                    path,
                    line.number,
                    f"{threat_id} is repeated in {test_id}",
                )
            )
        covered.update(set(references) & threats.keys())

    for threat_id in sorted(threats.keys() - covered):
        issues.append(
            _issue(
                "THREAT_TEST_COVERAGE_MISSING",
                root,
                path,
                0,
                f"{threat_id} has no security-test-plan coverage",
            )
        )
    if not test_lines:
        issues.append(
            _issue(
                "SECURITY_TEST_PLAN_MISSING",
                root,
                path,
                0,
                "security test plan has no valid rows",
            )
        )
    return issues


def validation_issues(root: Path = PROJECT_ROOT) -> list[DocumentationIssue]:
    """Return deterministic errors for local documentation and security traceability."""

    root = root.resolve()
    issues: list[DocumentationIssue] = []
    documentation_paths = _documentation_paths(root)
    text_by_path: dict[Path, str] = {}
    visible_by_path: dict[Path, tuple[VisibleLine, ...]] = {}
    anchors_by_path: dict[Path, frozenset[str]] = {}

    for path in documentation_paths:
        if path.is_symlink():
            issues.append(
                _issue(
                    "DOC_INPUT_UNSAFE",
                    root,
                    path,
                    0,
                    "documentation inputs cannot be symbolic links",
                )
            )
            continue
        text, read_issue = _read_utf8(root, path)
        if read_issue is not None:
            issues.append(read_issue)
            continue
        assert text is not None
        text_by_path[path] = text
        visible = _visible_lines(text)
        visible_by_path[path] = visible
        anchors_by_path[path] = _heading_anchors(visible)

    tasks_text, tasks_issue = _read_utf8(root, root / TASKS_PATH)
    if tasks_issue is not None:
        issues.append(tasks_issue)
        tasks_text = ""
    decisions_text = text_by_path.get(root / DECISIONS_PATH, "")

    decision_definitions = DECISION_DEFINITION.findall(decisions_text)
    task_definitions = TASK_DEFINITION.findall(tasks_text or "")
    decisions = frozenset(decision_definitions)
    tasks = frozenset(task_definitions)
    for decision_id, count in sorted(Counter(decision_definitions).items()):
        if count > 1:
            issues.append(
                _issue(
                    "DOC_DECISION_DEFINITION_DUPLICATE",
                    root,
                    root / DECISIONS_PATH,
                    0,
                    f"{decision_id} is defined more than once",
                )
            )
    for task_id, count in sorted(Counter(task_definitions).items()):
        if count > 1:
            issues.append(
                _issue(
                    "DOC_TASK_DEFINITION_DUPLICATE",
                    root,
                    root / TASKS_PATH,
                    0,
                    f"{task_id} is defined more than once",
                )
            )

    threats = _canonical_threats(root, visible_by_path, issues)

    for source in documentation_paths:
        source_lines = visible_by_path.get(source, ())
        for line in source_lines:
            for decision_id in sorted(set(DECISION_REFERENCE.findall(line.text)) - decisions):
                issues.append(
                    _issue(
                        "DOC_DECISION_REFERENCE_UNKNOWN",
                        root,
                        source,
                        line.number,
                        f"{decision_id} is not defined in DECISIONS.md",
                    )
                )
            for task_id in sorted(set(TASK_REFERENCE.findall(line.text)) - tasks):
                issues.append(
                    _issue(
                        "DOC_TASK_REFERENCE_UNKNOWN",
                        root,
                        source,
                        line.number,
                        f"{task_id} is not defined in TASKS.yaml",
                    )
                )
            for threat_id in sorted(set(THREAT_REFERENCE.findall(line.text)) - threats.keys()):
                issues.append(
                    _issue(
                        "DOC_THREAT_REFERENCE_UNKNOWN",
                        root,
                        source,
                        line.number,
                        f"{threat_id} is not defined in the threat catalogue",
                    )
                )

            for link in INLINE_LINK.finditer(line.text):
                target, fragment, problem = _resolved_local_target(
                    root,
                    source,
                    link.group("target"),
                )
                if problem == "external":
                    continue
                if problem is not None:
                    issues.append(_link_issue(root, source, line.number, problem))
                    continue
                assert target is not None
                if fragment and fragment not in anchors_by_path.get(target, frozenset()):
                    issues.append(
                        _issue(
                            "DOC_LINK_ANCHOR_MISSING",
                            root,
                            source,
                            line.number,
                            "local Markdown link anchor does not exist",
                        )
                    )

    issues.extend(_validate_traceability(root, visible_by_path, threats, decisions, tasks))
    issues.extend(_validate_test_plan(root, visible_by_path, threats))
    return sorted(
        set(issues),
        key=lambda issue: (issue.path, issue.line, issue.code, issue.message),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the offline documentation validator CLI."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=PROJECT_ROOT,
        help="repository root (default: detected project root)",
    )
    arguments = parser.parse_args(argv)
    issues = validation_issues(arguments.root)
    if issues:
        for issue in issues:
            print(issue.render(), file=sys.stderr)
        return 1
    print("OK: local documentation links, references, and security traceability are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

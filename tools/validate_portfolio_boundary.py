"""Validate PaperWorkShell's structured ThirdLife late-binding boundary."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
import tomllib
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Final, cast

import yaml

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
PYPROJECT_PATH: Final = Path("pyproject.toml")
UV_CONFIG_PATH: Final = Path("uv.toml")
LOCK_PATH: Final = Path("uv.lock")
DEPENDENCY_INVENTORY_PATH: Final = Path("packaging/dependency-inventory-v1.json")
TASKS_PATH: Final = Path("TASKS.yaml")
DECISIONS_PATH: Final = Path("DECISIONS.md")
BUNDLE_PATH: Final = Path("BUNDLE_MANIFEST.json")
PACKAGE_ROOT: Final = Path("src/paperworkshell")
RUNTIME_SCHEMA_ROOT: Final = Path("schemas")

ALIGNMENT_DECISION: Final = "PWS-D042"
ALIGNMENT_TASK: Final = "PWS-0809"
RELEASE_GATE_TASK: Final = "PWS-0808"
MINIMUM_BUNDLE_VERSION: Final = (0, 2, 0)
MAXIMUM_GOVERNANCE_INPUT_BYTES: Final = 8 * 1024 * 1024
STEERING_SOURCE_BYTES: Final = 1_727
STEERING_SOURCE_SHA256: Final = "d6bf62f3a382166546b446d1aa9a8d4ab81bfc24f1c4f42fd170d3fbe65f563a"

ROLE_OWNERS: Final[dict[str, str]] = {
    "release-artifact-provenance": "PWS-0801",
    "application-identity-and-installation": "PWS-0802",
    "independent-authenticated-updates": "PWS-0803",
    "case-migration-and-recovery": "PWS-0804",
    "separate-sanitized-support-bundle": "PWS-0805",
    "release-interface-and-evidence-documents": "PWS-0806",
    "public-packaged-black-box-matrix": "PWS-0807",
    "standalone-release-gate": RELEASE_GATE_TASK,
    "portfolio-governance": ALIGNMENT_TASK,
}
M8_ALIGNMENT_TASKS: Final = frozenset(ROLE_OWNERS.values())

REQUIRED_BUNDLE_FILES: Final = frozenset(
    {
        "ROADMAP.md",
        "DECISIONS.md",
        "TASKS.yaml",
        "TASKS.schema.json",
        "AGENTS.md",
        "CODEX_START_PROMPT.md",
        "README.md",
        "THIRDLIFE_ALIGNMENT.md",
        "docs/release/README.md",
        "docs/release/RELEASE_INTERFACE.md",
        "docs/release/RELEASE_EVIDENCE.md",
        "docs/release/STANDALONE_RELEASE_CHECKLIST.md",
        "docs/portfolio/THIRDLIFE_FUTURE_NOTES.md",
        "docs/portfolio/SOURCE_TRACEABILITY.md",
    }
)

DOCUMENT_METADATA_REQUIREMENTS: Final[dict[Path, dict[str, tuple[str, ...]]]] = {
    Path("THIRDLIFE_ALIGNMENT.md"): {
        "Status": ("inherited portfolio constraint", "local authority order"),
        "Integration model": ("standalone first", "late-binding", "black-box"),
        "Future assembly owner": ("ThirdLife Deployment and Suite Assembly", "B4"),
    },
    Path("docs/release/RELEASE_INTERFACE.md"): {
        "Document status": ("not an application api",),
        "Intended completion": ("preview", "stable"),
    },
    Path("docs/release/RELEASE_EVIDENCE.md"): {
        "Document status": ("exact packaged m8 candidate",),
    },
    Path("docs/portfolio/THIRDLIFE_FUTURE_NOTES.md"): {
        "Status": (
            "non-binding",
            "non-gating",
            "no implementation authorization",
        ),
        "Future implementation owner": ("ThirdLife Deployment and Suite Assembly", "B4"),
    },
    Path("docs/release/STANDALONE_RELEASE_CHECKLIST.md"): {
        "Principle": ("complete without ThirdLife", "frozen public artifacts"),
        "Completion rule": ("exact packaged candidate",),
    },
    Path("docs/portfolio/SOURCE_TRACEABILITY.md"): {
        "Supplied source version/date": ("2.0", "2026-08-14"),
        "Purpose": ("provenance", "adapted local documents"),
    },
}

RELEASE_DRAFT_METADATA_REQUIREMENTS: Final[dict[Path, dict[str, tuple[str, ...]]]] = {
    Path("docs/release/RELEASE_INTERFACE.md"): {
        "Document status": ("draft", "not a release guarantee"),
        "Interface revision": ("0-draft",),
    },
    Path("docs/release/RELEASE_EVIDENCE.md"): {
        "Document status": ("draft template",),
        "Evidence revision": ("0-draft",),
        "Release version": ("tbd",),
    },
}
RELEASE_CHECKLIST_PATH: Final = Path("docs/release/STANDALONE_RELEASE_CHECKLIST.md")

ALIGNMENT_DECISION_REQUIRED_FRAGMENTS: Final = (
    "**Status:** FROZEN",
    "independently useful, independently versioned",
    "has no permanent integration lane",
    "exact frozen PaperWorkShell release",
    "ThirdLife owns any later catalogue entry",
    "does not read or index PaperWorkShell documents",
)

RUNTIME_SCHEMA_SEARCH_ROOTS: Final = (
    Path("schemas"),
    PACKAGE_ROOT,
)
PRODUCTION_SOURCE_SUFFIXES: Final = frozenset({".py", ".pyi"})
EXTERNAL_SOURCE_KEYS: Final = frozenset(
    {"directory", "editable", "git", "path", "virtual", "workspace"}
)
UV_DEPENDENCY_KEYS: Final = (
    "build-constraint-dependencies",
    "constraint-dependencies",
    "dependency-metadata",
    "dev-dependencies",
    "extra-build-dependencies",
    "extra-index-url",
    "find-links",
    "index",
    "index-url",
    "override-dependencies",
    "sources",
)
UV_LOCATION_KEYS: Final = frozenset({"extra-index-url", "find-links", "index", "index-url"})
WINDOWS_RESERVED_COMPONENTS: Final = frozenset(
    {
        "aux",
        "clock$",
        "con",
        "nul",
        "prn",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
)
DIRECT_SOURCE_REFERENCE: Final = re.compile(
    r"^[a-z0-9][a-z0-9._-]*\s*(?:\[[^\]\r\n]+\]\s*)?@\s*(?!https://)",
    re.IGNORECASE,
)
BARE_EXTERNAL_SOURCE_REFERENCE: Final = re.compile(
    r"^(?:file:|(?:bzr|git|hg|svn)\+|ssh:|[a-z]:|[\\/]|~[\\/]|\.\.(?:[\\/]|$)|\.(?:[\\/]|$))",
    re.IGNORECASE,
)
DECISION_HEADING: Final = re.compile(r"^##[ \t]+(PWS-D[0-9]{3})\b", re.MULTILINE)
SEMVER: Final = re.compile(
    r"^(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)\."
    r"(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


class DuplicateKeyError(ValueError):
    """Raised when a structured input repeats a mapping key."""


class UniqueKeyLoader(yaml.SafeLoader):
    """YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: UniqueKeyLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    loader.flatten_mapping(node)
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as error:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from error
        if duplicate:
            raise DuplicateKeyError
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True, order=True)
class PortfolioIssue:
    """One deterministic, repository-relative validation issue."""

    path: str
    line: int
    code: str
    message: str

    def render(self) -> str:
        location = self.path if self.line <= 0 else f"{self.path}:{self.line}"
        return f"ERROR [{self.code}] {location}: {self.message}"


def _relative_label(root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
    except OSError, ValueError:
        return "<outside-repository>"


def _issue(
    code: str,
    root: Path,
    path: Path,
    message: str,
    *,
    line: int = 0,
) -> PortfolioIssue:
    return PortfolioIssue(_relative_label(root, path), line, code, message)


def _is_unaliased_repository_path(root: Path, path: Path) -> bool:
    try:
        lexical_root = root.absolute()
        relative = path.absolute().relative_to(lexical_root)
        if path.is_symlink():
            return False
        resolved_root = root.resolve(strict=True)
        resolved_path = path.resolve(strict=True)
    except OSError, ValueError:
        return False
    return resolved_path == resolved_root / relative


def _unsafe_descendant_directory(root: Path, directory: Path) -> Path | None:
    """Find a directory alias or unreadable subtree without following it."""

    pending = [directory]
    while pending:
        current = pending.pop()
        try:
            entries = tuple(
                sorted(
                    current.iterdir(),
                    key=lambda entry: (entry.name.casefold(), entry.name),
                )
            )
        except OSError:
            return current
        for entry in entries:
            try:
                if entry.is_symlink() or entry.is_junction():
                    return entry
                if not entry.is_dir():
                    continue
            except OSError:
                return entry
            if not _is_unaliased_repository_path(root, entry):
                return entry
            pending.append(entry)
    return None


def _read_bytes(root: Path, path: Path) -> tuple[bytes | None, list[PortfolioIssue]]:
    try:
        path.lstat()
        if not _is_unaliased_repository_path(root, path):
            return None, [
                _issue(
                    "PORTFOLIO_INPUT_UNSAFE",
                    root,
                    path,
                    "governance inputs must be unaliased repository-local paths",
                )
            ]
        if path.stat().st_size > MAXIMUM_GOVERNANCE_INPUT_BYTES:
            return None, [
                _issue(
                    "PORTFOLIO_INPUT_LIMIT_EXCEEDED",
                    root,
                    path,
                    "governance inputs must remain within the fixed byte limit",
                )
            ]
        with path.open("rb") as source:
            raw = source.read(MAXIMUM_GOVERNANCE_INPUT_BYTES + 1)
        if len(raw) > MAXIMUM_GOVERNANCE_INPUT_BYTES:
            return None, [
                _issue(
                    "PORTFOLIO_INPUT_LIMIT_EXCEEDED",
                    root,
                    path,
                    "governance inputs must remain within the fixed byte limit",
                )
            ]
        return raw, []
    except OSError:
        return None, [
            _issue(
                "PORTFOLIO_INPUT_UNREADABLE",
                root,
                path,
                "required governance input could not be read",
            )
        ]


def _read_text(root: Path, path: Path) -> tuple[str | None, list[PortfolioIssue]]:
    raw, issues = _read_bytes(root, path)
    if raw is None:
        return None, issues
    try:
        return raw.decode("utf-8"), []
    except UnicodeError:
        return None, [
            _issue(
                "PORTFOLIO_TEXT_ENCODING_INVALID",
                root,
                path,
                "governance text must be valid UTF-8",
            )
        ]


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError
        result[key] = value
    return result


def _load_json(
    root: Path,
    path: Path,
) -> tuple[dict[str, object] | None, list[PortfolioIssue]]:
    text, issues = _read_text(root, path)
    if text is None:
        return None, issues
    try:
        loaded = json.loads(text, object_pairs_hook=_unique_json_object)
    except (
        json.JSONDecodeError,
        DuplicateKeyError,
        MemoryError,
        RecursionError,
        TypeError,
        ValueError,
    ):
        return None, [
            _issue(
                "PORTFOLIO_JSON_INVALID",
                root,
                path,
                "governance JSON must be one duplicate-free object",
            )
        ]
    if not isinstance(loaded, dict):
        return None, [
            _issue(
                "PORTFOLIO_JSON_INVALID",
                root,
                path,
                "governance JSON must be one duplicate-free object",
            )
        ]
    return cast(dict[str, object], loaded), []


def _load_yaml(
    root: Path,
    path: Path,
) -> tuple[dict[str, object] | None, list[PortfolioIssue]]:
    text, issues = _read_text(root, path)
    if text is None:
        return None, issues
    try:
        loaded = yaml.load(text, Loader=UniqueKeyLoader)
    except yaml.YAMLError, DuplicateKeyError, MemoryError, RecursionError, TypeError, ValueError:
        return None, [
            _issue(
                "PORTFOLIO_YAML_INVALID",
                root,
                path,
                "task governance must be one duplicate-free YAML mapping",
            )
        ]
    if not isinstance(loaded, dict):
        return None, [
            _issue(
                "PORTFOLIO_YAML_INVALID",
                root,
                path,
                "task governance must be one duplicate-free YAML mapping",
            )
        ]
    return cast(dict[str, object], loaded), []


def _load_toml(
    root: Path,
    path: Path,
) -> tuple[dict[str, object] | None, list[PortfolioIssue]]:
    raw, issues = _read_bytes(root, path)
    if raw is None:
        return None, issues
    try:
        loaded = tomllib.loads(raw.decode("utf-8"))
    except (
        MemoryError,
        RecursionError,
        UnicodeError,
        tomllib.TOMLDecodeError,
        TypeError,
        ValueError,
    ):
        return None, [
            _issue(
                "PORTFOLIO_TOML_INVALID",
                root,
                path,
                "dependency declarations must be valid TOML",
            )
        ]
    return cast(dict[str, object], loaded), []


def _contains_portfolio_runtime_marker(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", value.casefold())
    return "thirdlife" in normalized


def _contains_production_runtime_literal(value: str) -> bool:
    """Reject product literals; portfolio disclosure belongs in canonical documentation."""

    return _contains_portfolio_runtime_marker(value)


def _structured_contains_runtime_marker(value: object) -> bool:
    """Inspect nested TOML/JSON dependency values without assuming string arrays."""

    pending: deque[object] = deque((value,))
    while pending:
        member = pending.popleft()
        if isinstance(member, Mapping):
            for key, child in member.items():
                if isinstance(key, str) and _contains_portfolio_runtime_marker(key):
                    return True
                pending.append(child)
        elif isinstance(member, list):
            pending.extend(member)
        elif isinstance(member, str) and _contains_portfolio_runtime_marker(member):
            return True
    return False


def _structured_contains_external_source(value: object) -> bool:
    """Reject local/VCS/workspace declarations even when they use generic names."""

    pending: deque[object] = deque((value,))
    while pending:
        member = pending.popleft()
        if isinstance(member, Mapping):
            for key, child in member.items():
                if isinstance(key, str):
                    normalized_key = re.sub(r"[^a-z0-9]+", "", key.casefold())
                    if normalized_key in EXTERNAL_SOURCE_KEYS and child not in (
                        None,
                        False,
                        "",
                        (),
                        [],
                        {},
                    ):
                        return True
                pending.append(child)
        elif isinstance(member, list):
            pending.extend(member)
        elif isinstance(member, str):
            stripped = member.strip()
            if DIRECT_SOURCE_REFERENCE.search(stripped) or BARE_EXTERNAL_SOURCE_REFERENCE.search(
                stripped
            ):
                return True
    return False


def _structured_contains_non_https_location(
    value: object,
    *,
    strings_are_locations: bool,
) -> bool:
    """Reject local/insecure uv resolution locations without treating index names as paths."""

    if isinstance(value, str):
        return strings_are_locations and not value.strip().casefold().startswith("https://")
    if isinstance(value, list):
        return any(
            _structured_contains_non_https_location(
                member,
                strings_are_locations=strings_are_locations,
            )
            for member in value
        )
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                continue
            normalized_key = re.sub(r"[^a-z0-9]+", "", key.casefold())
            if normalized_key in EXTERNAL_SOURCE_KEYS and child not in (
                None,
                False,
                "",
                (),
                [],
                {},
            ):
                return True
            child_is_location = normalized_key in {
                "extraindexurl",
                "findlinks",
                "indexurl",
                "url",
            }
            if _structured_contains_non_https_location(
                child,
                strings_are_locations=child_is_location,
            ):
                return True
    return False


def _semver_at_least(value: object, minimum: tuple[int, int, int]) -> bool:
    if not isinstance(value, str) or (match := SEMVER.fullmatch(value)) is None:
        return False
    core = tuple(int(match.group(index)) for index in range(1, 4))
    if core != minimum:
        return core > minimum
    return match.group(4) is None


def _mapping(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        return None
    return cast(Mapping[str, object], value)


def _sequence(value: object) -> Sequence[object] | None:
    if not isinstance(value, list):
        return None
    return cast(Sequence[object], value)


def _dependency_declaration_issues(root: Path) -> list[PortfolioIssue]:
    path = root / PYPROJECT_PATH
    configuration, issues = _load_toml(root, path)
    if configuration is None:
        return issues

    dependency_declarations: list[tuple[Path, object]] = []
    build = _mapping(configuration.get("build-system"))
    project = _mapping(configuration.get("project"))
    groups = _mapping(configuration.get("dependency-groups"))
    if build is not None:
        dependency_declarations.append((path, build))
        dependency_declarations.append((path, build.get("requires")))
        if "backend-path" in build:
            dependency_declarations.append((path, {"path": build["backend-path"]}))
    if project is not None:
        dependency_declarations.extend(
            (
                (path, project.get("dependencies")),
                (path, project.get("optional-dependencies")),
            )
        )
    if groups is not None:
        dependency_declarations.append((path, groups))
    tool = _mapping(configuration.get("tool"))
    uv_configurations: list[tuple[Path, Mapping[str, object]]] = []
    uv_location_declarations: list[tuple[Path, object, bool]] = []
    pyproject_uv = None if tool is None else _mapping(tool.get("uv"))
    if pyproject_uv is not None:
        uv_configurations.append((path, pyproject_uv))
    uv_config_path = root / UV_CONFIG_PATH
    try:
        uv_config_path.lstat()
    except FileNotFoundError:
        pass
    except OSError:
        issues.append(
            _issue(
                "PORTFOLIO_INPUT_UNREADABLE",
                root,
                uv_config_path,
                "the optional uv configuration could not be inspected",
            )
        )
    else:
        uv_configuration, uv_issues = _load_toml(root, uv_config_path)
        issues.extend(uv_issues)
        if uv_configuration is not None:
            uv_configurations.append((uv_config_path, uv_configuration))
    for configuration_path, uv in uv_configurations:
        dependency_declarations.extend(
            (configuration_path, uv[key]) for key in UV_DEPENDENCY_KEYS if key in uv
        )
        uv_location_declarations.extend(
            (configuration_path, uv[key], True) for key in UV_LOCATION_KEYS if key in uv
        )
        if "workspace" in uv:
            dependency_declarations.append((configuration_path, {"workspace": uv["workspace"]}))

    runtime_paths = {
        declaration_path
        for declaration_path, declaration in dependency_declarations
        if _structured_contains_runtime_marker(declaration)
    }
    for declaration_path in sorted(runtime_paths, key=lambda candidate: candidate.as_posix()):
        issues.append(
            _issue(
                "PORTFOLIO_RUNTIME_DEPENDENCY",
                root,
                declaration_path,
                "ThirdLife cannot be a PaperWorkShell dependency",
            )
        )
    external_source_paths = {
        declaration_path
        for declaration_path, declaration in dependency_declarations
        if _structured_contains_external_source(declaration)
    }
    external_source_paths.update(
        declaration_path
        for declaration_path, declaration, strings_are_locations in uv_location_declarations
        if _structured_contains_non_https_location(
            declaration,
            strings_are_locations=strings_are_locations,
        )
    )
    for declaration_path in sorted(
        external_source_paths, key=lambda candidate: candidate.as_posix()
    ):
        issues.append(
            _issue(
                "PORTFOLIO_RUNTIME_DEPENDENCY",
                root,
                declaration_path,
                "local, VCS, shared-workspace, and insecure resolution sources are prohibited",
            )
        )

    if project is not None:
        public_entry_points = (
            project.get("scripts"),
            project.get("gui-scripts"),
            project.get("entry-points"),
        )
        if any(
            _structured_contains_runtime_marker(entry_points)
            for entry_points in public_entry_points
        ):
            issues.append(
                _issue(
                    "PORTFOLIO_PUBLIC_ENTRYPOINT_INVALID",
                    root,
                    path,
                    "public entry points cannot be ThirdLife-specific",
                )
            )
    return issues


def _lock_dependency_issues(root: Path) -> list[PortfolioIssue]:
    path = root / LOCK_PATH
    lock, issues = _load_toml(root, path)
    if lock is None:
        return issues
    packages = _sequence(lock.get("package"))
    if packages is None:
        return [
            _issue(
                "PORTFOLIO_LOCK_INVALID",
                root,
                path,
                "the lock must contain a package inventory",
            )
        ]
    for package_value in packages:
        package = _mapping(package_value)
        if package is None:
            continue
        name = package.get("name")
        source = _mapping(package.get("source"))
        if isinstance(name, str) and _contains_portfolio_runtime_marker(name):
            issues.append(
                _issue(
                    "PORTFOLIO_RUNTIME_DEPENDENCY",
                    root,
                    path,
                    "the resolved lock cannot contain ThirdLife",
                )
            )
            break
        source_is_exact_self = name == "paperworkshell" and dict(source or {}) == {"editable": "."}
        if source is not None and (
            _structured_contains_runtime_marker(source)
            or _structured_contains_external_source(source)
            and not source_is_exact_self
        ):
            issues.append(
                _issue(
                    "PORTFOLIO_RUNTIME_DEPENDENCY",
                    root,
                    path,
                    "dependency sources cannot point at ThirdLife",
                )
            )
            break
    return issues


def _inventory_dependency_issues(root: Path) -> list[PortfolioIssue]:
    path = root / DEPENDENCY_INVENTORY_PATH
    inventory, issues = _load_json(root, path)
    if inventory is None:
        return issues
    components = _sequence(inventory.get("components"))
    if components is None:
        return [
            _issue(
                "PORTFOLIO_DEPENDENCY_INVENTORY_INVALID",
                root,
                path,
                "the dependency inventory must contain components",
            )
        ]
    for component_value in components:
        component = _mapping(component_value)
        if component is None:
            continue
        source = _mapping(component.get("source"))
        if _structured_contains_runtime_marker(component) or (
            source is not None and _structured_contains_external_source(source)
        ):
            issues.append(
                _issue(
                    "PORTFOLIO_RUNTIME_DEPENDENCY",
                    root,
                    path,
                    "the reviewed dependency inventory cannot contain ThirdLife",
                )
            )
            break
    return issues


def _production_docstring_nodes(tree: ast.AST) -> frozenset[int]:
    docstrings: set[int] = set()
    owners = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, owners) or not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstrings.add(id(first.value))
    return frozenset(docstrings)


def _production_source_issues(root: Path) -> list[PortfolioIssue]:
    package_root = root / PACKAGE_ROOT
    try:
        package_root.lstat()
    except OSError:
        return [
            _issue(
                "PORTFOLIO_PRODUCT_SOURCE_MISSING",
                root,
                package_root,
                "the PaperWorkShell product package is required",
            )
        ]
    if not _is_unaliased_repository_path(root, package_root):
        return [
            _issue(
                "PORTFOLIO_PRODUCT_SOURCE_UNSAFE",
                root,
                package_root,
                "the product package must be an unaliased repository-local directory",
            )
        ]
    if not package_root.is_dir():
        return [
            _issue(
                "PORTFOLIO_PRODUCT_SOURCE_MISSING",
                root,
                package_root,
                "the PaperWorkShell product package is required",
            )
        ]
    unsafe_directory = _unsafe_descendant_directory(root, package_root)
    if unsafe_directory is not None:
        return [
            _issue(
                "PORTFOLIO_PRODUCT_SOURCE_UNSAFE",
                root,
                unsafe_directory,
                "product source directories must be readable, unaliased repository paths",
            )
        ]
    issues: list[PortfolioIssue] = []
    paths = (
        path
        for path in package_root.rglob("*")
        if path.is_file() and path.suffix.casefold() in PRODUCTION_SOURCE_SUFFIXES
    )
    for path in sorted(paths, key=lambda candidate: candidate.as_posix()):
        text, read_issues = _read_text(root, path)
        if text is None:
            issues.extend(read_issues)
            continue
        try:
            tree = ast.parse(text, filename=path.name)
        except MemoryError, RecursionError, SyntaxError, ValueError:
            issues.append(
                _issue(
                    "PORTFOLIO_PRODUCTION_PARSE_INVALID",
                    root,
                    path,
                    "product source could not be parsed",
                )
            )
            continue
        docstrings = _production_docstring_nodes(tree)
        marker_lines: set[int] = set()
        relative_path = path.relative_to(package_root)
        if any(_contains_portfolio_runtime_marker(part) for part in relative_path.parts):
            marker_lines.add(1)
        for node in ast.walk(tree):
            values: list[str] = []
            if isinstance(node, ast.Name):
                values.append(node.id)
            elif isinstance(node, ast.Attribute):
                values.append(node.attr)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                values.append(node.name)
            elif isinstance(node, ast.arg):
                values.append(node.arg)
            elif isinstance(node, ast.ImportFrom):
                values.append(node.module or "")
            elif isinstance(node, ast.alias):
                values.extend((node.name, node.asname or ""))
            elif isinstance(node, ast.Constant) and id(node) not in docstrings:
                literal = ""
                if isinstance(node.value, str):
                    literal = node.value
                elif isinstance(node.value, bytes):
                    literal = node.value.decode("ascii", errors="ignore")
                if literal and _contains_production_runtime_literal(literal):
                    marker_lines.add(getattr(node, "lineno", 1))
                continue
            if any(_contains_portfolio_runtime_marker(value) for value in values):
                marker_lines.add(getattr(node, "lineno", 1))
        for line in sorted(marker_lines):
            issues.append(
                _issue(
                    "PORTFOLIO_PRODUCTION_REFERENCE",
                    root,
                    path,
                    "production code cannot contain a ThirdLife-specific runtime interface",
                    line=line,
                )
            )
    return issues


def _json_contains_runtime_marker(value: object) -> bool:
    pending: deque[object] = deque((value,))
    while pending:
        member = pending.popleft()
        if isinstance(member, Mapping):
            for key, child in member.items():
                if isinstance(key, str) and _contains_portfolio_runtime_marker(key):
                    return True
                pending.append(child)
        elif isinstance(member, list):
            pending.extend(member)
        elif isinstance(member, str) and _contains_portfolio_runtime_marker(member):
            return True
    return False


def _runtime_schema_issues(root: Path) -> list[PortfolioIssue]:
    schema_root = root / RUNTIME_SCHEMA_ROOT
    issues: list[PortfolioIssue] = []
    search_roots: list[Path] = []
    for relative_root in RUNTIME_SCHEMA_SEARCH_ROOTS:
        search_root = root / relative_root
        try:
            search_root.lstat()
        except OSError:
            continue
        if not _is_unaliased_repository_path(root, search_root):
            issues.append(
                _issue(
                    "PORTFOLIO_INPUT_UNSAFE",
                    root,
                    search_root,
                    "runtime schema roots must be unaliased repository-local directories",
                )
            )
            continue
        if search_root.is_dir():
            unsafe_directory = _unsafe_descendant_directory(root, search_root)
            if unsafe_directory is not None:
                issues.append(
                    _issue(
                        "PORTFOLIO_INPUT_UNSAFE",
                        root,
                        unsafe_directory,
                        "runtime schema trees must be readable and cannot contain aliases",
                    )
                )
                continue
            search_roots.append(search_root)
    paths = {
        path
        for search_root in search_roots
        for path in search_root.rglob("*.schema.json")
        if path.is_file()
    }
    if not paths:
        issues.append(
            _issue(
                "PORTFOLIO_RUNTIME_SCHEMA_MISSING",
                root,
                schema_root,
                "registered product runtime schemas are required",
            )
        )
    for path in sorted(paths, key=lambda candidate: candidate.as_posix()):
        try:
            relative_schema_path = path.relative_to(root).as_posix()
        except ValueError:
            relative_schema_path = path.name
        if _contains_portfolio_runtime_marker(relative_schema_path):
            issues.append(
                _issue(
                    "PORTFOLIO_RUNTIME_SCHEMA_REFERENCE",
                    root,
                    path,
                    "runtime schemas cannot use a ThirdLife-specific path or protocol",
                )
            )
            continue
        schema, load_issues = _load_json(root, path)
        if schema is None:
            issues.extend(load_issues)
            continue
        if _json_contains_runtime_marker(schema):
            issues.append(
                _issue(
                    "PORTFOLIO_RUNTIME_SCHEMA_REFERENCE",
                    root,
                    path,
                    "runtime schemas cannot contain a ThirdLife-specific field or protocol",
                )
            )
    return issues


def _table_cells(line: str) -> tuple[str, ...] | None:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    return tuple(cell.strip() for cell in stripped[1:-1].split("|"))


def _plain_markdown(value: str) -> str:
    return value.replace("**", "").replace("`", "").strip().casefold()


def _metadata_table(text: str) -> dict[str, str] | None:
    lines = text.splitlines()
    for index, line in enumerate(lines[:80]):
        header = _table_cells(line)
        if header is None or len(header) != 2 or header[0].casefold() != "field":
            continue
        if index + 1 >= len(lines):
            return None
        separator = _table_cells(lines[index + 1])
        if (
            separator is None
            or len(separator) != 2
            or any(re.fullmatch(r":?-{3,}:?", cell) is None for cell in separator)
        ):
            return None
        metadata: dict[str, str] = {}
        for row in lines[index + 2 :]:
            cells = _table_cells(row)
            if cells is None or len(cells) != 2:
                break
            if cells[0] in metadata:
                return None
            metadata[cells[0]] = cells[1]
        return metadata if metadata else None
    return None


def _metadata_satisfies(
    metadata: Mapping[str, str],
    requirements: Mapping[str, tuple[str, ...]],
) -> bool:
    for field, fragments in requirements.items():
        value = metadata.get(field)
        normalized = "" if value is None else _plain_markdown(value)
        if value is None or any(fragment.casefold() not in normalized for fragment in fragments):
            return False
    return True


def _release_metadata_is_final(path: Path, metadata: Mapping[str, str]) -> bool:
    status = _plain_markdown(metadata.get("Document status", ""))
    if re.match(r"^(?:complete|final|frozen|released)\b", status) is None or re.search(
        r"\b(?:draft|incomplete|pending|unreleased)\b|\bnot\s+(?:complete|final|frozen|released)\b",
        status,
    ):
        return False
    fields = (
        ("Interface revision",)
        if path.name == "RELEASE_INTERFACE.md"
        else (
            "Release version",
            "Release channel",
            "Evidence revision",
            "Source revision/tag",
            "Release interface revision",
        )
    )
    for field in fields:
        value = _plain_markdown(metadata.get(field, ""))
        if not value or "tbd" in value or "pending" in value or value == "0-draft":
            return False
    return True


def _release_body_has_structured_placeholders(text: str) -> bool:
    """Reject unresolved release fields while allowing explanatory template prose."""

    placeholder = re.compile(r"\b(?:pending|tbd)\b", re.IGNORECASE)
    field_line = re.compile(r"^\s*[^|:\r\n]{1,160}:\s*\S")
    list_line = re.compile(r"^\s*(?:[-+*]|[0-9]+[.)])\s+")
    for line in text.splitlines():
        plain = _plain_markdown(line)
        if placeholder.search(plain) is None:
            continue
        if _table_cells(line) is not None or list_line.match(line) or field_line.match(line):
            return True
        if plain.startswith(("pending", "tbd")):
            return True
    return False


def _release_checklist_is_complete(text: str) -> bool:
    states = re.findall(r"^\s*[-+*]\s+\[([ xX])\]\s+", text, re.MULTILINE)
    return bool(states) and all(state.casefold() == "x" for state in states)


def _document_issues(
    root: Path,
    tasks_data: Mapping[str, object] | None = None,
) -> list[PortfolioIssue]:
    issues: list[PortfolioIssue] = []
    tasks = None if tasks_data is None else _task_map(tasks_data)
    release_gate = None if tasks is None else tasks.get(RELEASE_GATE_TASK)
    release_gate_status = None if release_gate is None else release_gate.get("status")
    release_must_be_final = release_gate_status in {"review", "done"}
    release_may_be_final = release_gate_status in {"in_progress", "blocked"}
    for relative_path, requirements in DOCUMENT_METADATA_REQUIREMENTS.items():
        path = root / relative_path
        text, read_issues = _read_text(root, path)
        if text is None:
            issues.extend(
                _issue(
                    "PORTFOLIO_DOCUMENT_MISSING",
                    root,
                    path,
                    "a required late-binding portfolio document is missing",
                )
                for _ignored in read_issues or (None,)
            )
            continue
        metadata = _metadata_table(text)
        if metadata is None:
            issues.append(
                _issue(
                    "PORTFOLIO_DOCUMENT_STATUS_INVALID",
                    root,
                    path,
                    "the canonical document metadata table is missing or ambiguous",
                )
            )
            continue
        if not _metadata_satisfies(metadata, requirements):
            issues.append(
                _issue(
                    "PORTFOLIO_DOCUMENT_STATUS_INVALID",
                    root,
                    path,
                    "required standalone or non-gating document metadata is not preserved",
                )
            )
            continue
        draft_requirements = RELEASE_DRAFT_METADATA_REQUIREMENTS.get(relative_path)
        if draft_requirements is None:
            continue
        final_metadata = _release_metadata_is_final(relative_path, metadata)
        if final_metadata and _release_body_has_structured_placeholders(text):
            final_metadata = False
        draft_metadata = _metadata_satisfies(metadata, draft_requirements)
        if release_must_be_final:
            stage_valid = final_metadata
        elif release_may_be_final:
            stage_valid = draft_metadata or final_metadata
        else:
            stage_valid = draft_metadata
        if not stage_valid:
            issues.append(
                _issue(
                    "PORTFOLIO_DOCUMENT_STAGE_INVALID",
                    root,
                    path,
                    "release-document metadata must match the current M8 gate stage",
                )
            )
    if release_must_be_final:
        checklist_path = root / RELEASE_CHECKLIST_PATH
        checklist_text, checklist_issues = _read_text(root, checklist_path)
        if checklist_text is None:
            issues.extend(checklist_issues)
        elif not _release_checklist_is_complete(checklist_text):
            issues.append(
                _issue(
                    "PORTFOLIO_DOCUMENT_STAGE_INVALID",
                    root,
                    checklist_path,
                    "the exact-candidate standalone checklist must be complete at the release gate",
                )
            )
    return issues


def _task_map(data: Mapping[str, object]) -> dict[str, Mapping[str, object]] | None:
    tasks = _sequence(data.get("tasks"))
    if tasks is None:
        return None
    result: dict[str, Mapping[str, object]] = {}
    for task_value in tasks:
        task = _mapping(task_value)
        if task is None or not isinstance(task.get("id"), str):
            return None
        task_id = cast(str, task["id"])
        if task_id in result:
            return None
        result[task_id] = task
    return result


def _task_contract_issues(
    root: Path,
    data: Mapping[str, object],
) -> list[PortfolioIssue]:
    path = root / TASKS_PATH
    issues: list[PortfolioIssue] = []
    tasks = _task_map(data)
    if tasks is None:
        return [
            _issue(
                "PORTFOLIO_TASK_GRAPH_INVALID",
                root,
                path,
                "the task graph cannot be indexed safely",
            )
        ]
    decisions_text, decision_read_issues = _read_text(root, root / DECISIONS_PATH)
    if decisions_text is None:
        issues.extend(decision_read_issues)
    else:
        decision_matches = tuple(DECISION_HEADING.finditer(decisions_text))
        alignment_matches = tuple(
            match for match in decision_matches if match.group(1) == ALIGNMENT_DECISION
        )
        if len(alignment_matches) != 1:
            issues.append(
                _issue(
                    "PORTFOLIO_DECISION_MISSING",
                    root,
                    root / DECISIONS_PATH,
                    "the frozen late-binding decision must exist exactly once",
                )
            )
        else:
            alignment_match = alignment_matches[0]
            remaining_text = decisions_text[alignment_match.end() :]
            next_heading = re.search(
                r"^##[ \t]+",
                remaining_text,
                re.MULTILINE,
            )
            section_end = (
                len(decisions_text)
                if next_heading is None
                else alignment_match.end() + next_heading.start()
            )
            section = decisions_text[alignment_match.start() : section_end]
            if any(fragment not in section for fragment in ALIGNMENT_DECISION_REQUIRED_FRAGMENTS):
                issues.append(
                    _issue(
                        "PORTFOLIO_DECISION_CONTRACT_INVALID",
                        root,
                        root / DECISIONS_PATH,
                        "the frozen standalone and late-binding decision contract changed",
                    )
                )

    for role, task_id in ROLE_OWNERS.items():
        task = tasks.get(task_id)
        expected_kind = "gate" if task_id == RELEASE_GATE_TASK else "implementation"
        if task is None or task.get("milestone") != "M8" or task.get("kind") != expected_kind:
            issues.append(
                _issue(
                    "PORTFOLIO_ROLE_OWNER_INVALID",
                    root,
                    path,
                    f"the {role} role must retain its project-local M8 owner",
                )
            )

    for task_id in sorted(M8_ALIGNMENT_TASKS):
        task = tasks.get(task_id)
        references = None if task is None else _sequence(task.get("decision_refs"))
        if references is None or ALIGNMENT_DECISION not in references:
            issues.append(
                _issue(
                    "PORTFOLIO_DECISION_COVERAGE_INVALID",
                    root,
                    path,
                    "every M8 release-envelope owner must reference the late-binding decision",
                )
            )
            break

    alignment_task = tasks.get(ALIGNMENT_TASK)
    if alignment_task is None or (
        alignment_task.get("priority") != "P0"
        or alignment_task.get("effort") != "M"
        or alignment_task.get("critical_path") is not False
        or _sequence(alignment_task.get("depends_on")) != ["PWS-0010"]
    ):
        issues.append(
            _issue(
                "PORTFOLIO_GOVERNANCE_TASK_INVALID",
                root,
                path,
                "the accepted portfolio-governance task contract changed",
            )
        )

    gate = tasks.get(RELEASE_GATE_TASK)
    gate_dependencies = None if gate is None else _sequence(gate.get("depends_on"))
    required_gate_dependencies = M8_ALIGNMENT_TASKS - {RELEASE_GATE_TASK}
    if gate_dependencies is None or not required_gate_dependencies.issubset(gate_dependencies):
        issues.append(
            _issue(
                "PORTFOLIO_GATE_COVERAGE_INVALID",
                root,
                path,
                "the v1.0 gate must directly cover every release-envelope owner",
            )
        )

    release_docs_task = tasks.get("PWS-0806")
    release_docs_dependencies = (
        None if release_docs_task is None else _sequence(release_docs_task.get("depends_on"))
    )
    if release_docs_dependencies is None or "PWS-0804" not in release_docs_dependencies:
        issues.append(
            _issue(
                "PORTFOLIO_MIGRATION_DOC_ORDER_INVALID",
                root,
                path,
                "release documentation must follow the owned migration and recovery contract",
            )
        )

    releases = _mapping(data.get("release_targets"))
    v1_release = None if releases is None else _mapping(releases.get("v1.0"))
    milestones = _sequence(data.get("milestones"))
    m8 = None
    if milestones is not None:
        m8 = next(
            (
                milestone
                for value in milestones
                if (milestone := _mapping(value)) is not None and milestone.get("id") == "M8"
            ),
            None,
        )
    if (
        v1_release is None
        or v1_release.get("gate_task") != RELEASE_GATE_TASK
        or m8 is None
        or m8.get("gate_task") != RELEASE_GATE_TASK
    ):
        issues.append(
            _issue(
                "PORTFOLIO_RELEASE_GATE_INVALID",
                root,
                path,
                "M8 and v1.0 must retain the standalone PaperWorkShell release gate",
            )
        )
    return issues


def _safe_bundle_path(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or ":" in value
        or any(ord(character) < 32 for character in value)
    ):
        return None
    path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if path.is_absolute() or windows_path.drive or windows_path.root:
        return None
    if any(
        part in {"", ".", ".."}
        or part != part.rstrip(" .")
        or any(character in '<>"|?*' for character in part)
        or part.rstrip(" .").partition(".")[0].casefold() in WINDOWS_RESERVED_COMPONENTS
        for part in path.parts
    ):
        return None
    return path.as_posix()


def _all_tasks_reachable_from(
    root_task: str,
    tasks: Mapping[str, Mapping[str, object]],
) -> bool:
    consumers: dict[str, set[str]] = {task_id: set() for task_id in tasks}
    for task_id, task in tasks.items():
        for dependency in _sequence(task.get("depends_on")) or ():
            if isinstance(dependency, str) and dependency in consumers:
                consumers[dependency].add(task_id)
    reached: set[str] = set()
    pending = [root_task]
    while pending:
        task_id = pending.pop()
        if task_id in reached or task_id not in consumers:
            continue
        reached.add(task_id)
        pending.extend(consumers[task_id])
    return reached == set(tasks)


def _bundle_issues(
    root: Path,
    tasks_data: Mapping[str, object],
) -> list[PortfolioIssue]:
    path = root / BUNDLE_PATH
    bundle, issues = _load_json(root, path)
    if bundle is None:
        return issues
    if not _semver_at_least(bundle.get("bundle_version"), MINIMUM_BUNDLE_VERSION):
        issues.append(
            _issue(
                "PORTFOLIO_BUNDLE_VERSION_INVALID",
                root,
                path,
                "the aligned roadmap bundle must use SemVer version 0.2.0 or newer",
            )
        )

    source_inputs = _sequence(bundle.get("source_inputs"))
    source_matches = 0
    if source_inputs is not None:
        for source_value in source_inputs:
            source = _mapping(source_value)
            if (
                source is not None
                and source.get("bytes") == STEERING_SOURCE_BYTES
                and source.get("sha256") == STEERING_SOURCE_SHA256
            ):
                source_matches += 1
    if source_matches != 1:
        issues.append(
            _issue(
                "PORTFOLIO_BUNDLE_SOURCE_INVALID",
                root,
                path,
                "the verified steering source manifest must be recorded exactly once",
            )
        )

    file_values = _sequence(bundle.get("files"))
    records: dict[str, Mapping[str, object]] = {}
    malformed = file_values is None
    for record_value in file_values or ():
        record = _mapping(record_value)
        normalized_path = None if record is None else _safe_bundle_path(record.get("path"))
        if (
            record is None
            or not {"path", "bytes", "sha256"}.issubset(record)
            or normalized_path is None
            or normalized_path in records
        ):
            malformed = True
            continue
        records[normalized_path] = record
    if malformed or not REQUIRED_BUNDLE_FILES.issubset(records):
        issues.append(
            _issue(
                "PORTFOLIO_BUNDLE_INVENTORY_INVALID",
                root,
                path,
                "the bundle must include every canonical planning and portfolio document",
            )
        )
    if BUNDLE_PATH.as_posix() in records:
        issues.append(
            _issue(
                "PORTFOLIO_BUNDLE_SELF_REFERENCE",
                root,
                path,
                "the bundle manifest must omit a hash of itself",
            )
        )

    resolved_root = root.resolve(strict=False)
    for relative_path, record in sorted(records.items()):
        relative_parts = PurePosixPath(relative_path).parts
        target = root / Path(*relative_parts)
        expected_target = resolved_root / Path(*relative_parts)
        try:
            resolved_target = target.resolve(strict=False)
            resolved_target.relative_to(resolved_root)
        except OSError, ValueError:
            issues.append(
                _issue(
                    "PORTFOLIO_BUNDLE_INVENTORY_INVALID",
                    root,
                    path,
                    "bundled canonical paths must remain inside the repository",
                )
            )
            continue
        if resolved_target != expected_target:
            issues.append(
                _issue(
                    "PORTFOLIO_BUNDLE_INVENTORY_INVALID",
                    root,
                    path,
                    "bundled canonical paths cannot traverse filesystem aliases",
                )
            )
            continue
        raw, read_issues = _read_bytes(root, target)
        if raw is None:
            issues.extend(read_issues)
            continue
        if record.get("bytes") != len(raw):
            issues.append(
                _issue(
                    "PORTFOLIO_BUNDLE_FILE_SIZE_MISMATCH",
                    root,
                    path,
                    "a bundled canonical file has a stale byte count",
                )
            )
        if record.get("sha256") != hashlib.sha256(raw).hexdigest():
            issues.append(
                _issue(
                    "PORTFOLIO_BUNDLE_FILE_HASH_MISMATCH",
                    root,
                    path,
                    "a bundled canonical file has a stale SHA-256",
                )
            )

    tasks = _task_map(tasks_data)
    validation = _mapping(bundle.get("validation"))
    if tasks is None or validation is None:
        issues.append(
            _issue(
                "PORTFOLIO_BUNDLE_GRAPH_INVALID",
                root,
                path,
                "the bundle graph summary is missing",
            )
        )
        return issues
    dependency_count = sum(len(_sequence(task.get("depends_on")) or ()) for task in tasks.values())
    milestones = _sequence(tasks_data.get("milestones")) or ()
    ready_tasks = sorted(
        task_id for task_id, task in tasks.items() if task.get("status") == "ready"
    )
    gate_count = sum(task.get("kind") == "gate" for task in tasks.values())
    graph_valid = (
        validation.get("json_schema") == "passed"
        and validation.get("dag") == "passed"
        and validation.get("task_count") == len(tasks)
        and validation.get("dependency_edge_count") == dependency_count
        and validation.get("milestone_count") == len(milestones)
        and validation.get("gate_task_count") == gate_count
        and validation.get("initial_ready_tasks") == ready_tasks
        and validation.get("all_tasks_reachable_from") == "PWS-0001"
        and _all_tasks_reachable_from("PWS-0001", tasks)
    )
    if not graph_valid:
        issues.append(
            _issue(
                "PORTFOLIO_BUNDLE_GRAPH_INVALID",
                root,
                path,
                "the bundle graph summary must exactly match the validated task graph",
            )
        )
    return issues


def validation_issues(root: Path = PROJECT_ROOT) -> list[PortfolioIssue]:
    """Return deterministic issues for the complete local portfolio boundary."""

    root = root.resolve(strict=False)
    issues: list[PortfolioIssue] = []
    issues.extend(_dependency_declaration_issues(root))
    issues.extend(_lock_dependency_issues(root))
    issues.extend(_inventory_dependency_issues(root))
    issues.extend(_production_source_issues(root))
    issues.extend(_runtime_schema_issues(root))
    tasks_data, task_load_issues = _load_yaml(root, root / TASKS_PATH)
    if tasks_data is None:
        issues.extend(task_load_issues)
        issues.extend(_document_issues(root))
    else:
        issues.extend(_document_issues(root, tasks_data))
        issues.extend(_task_contract_issues(root, tasks_data))
        issues.extend(_bundle_issues(root, tasks_data))
    return sorted(set(issues))


def _success_counts(root: Path) -> tuple[int, int, int]:
    tasks_data, _task_issues = _load_yaml(root, root / TASKS_PATH)
    bundle, _bundle_issues_found = _load_json(root, root / BUNDLE_PATH)
    tasks = None if tasks_data is None else _task_map(tasks_data)
    task_count = 0 if tasks is None else len(tasks)
    dependency_count = 0
    if tasks is not None:
        dependency_count = sum(
            len(_sequence(task.get("depends_on")) or ()) for task in tasks.values()
        )
    bundle_files = None if bundle is None else _sequence(bundle.get("files"))
    return task_count, dependency_count, len(bundle_files or ())


def main(argv: Sequence[str] | None = None) -> int:
    """Run the offline portfolio-boundary validator."""

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
    task_count, dependency_count, bundled_file_count = _success_counts(
        arguments.root.resolve(strict=False)
    )
    print(
        "OK: ThirdLife late-binding portfolio boundary is valid "
        f"({task_count} tasks, {dependency_count} dependencies, "
        f"{bundled_file_count} bundled canonical files)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

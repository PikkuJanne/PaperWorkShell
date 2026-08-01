"""Validate the PaperWorkShell declarative-template v1 contract and fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final, NoReturn, cast
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
TEMPLATE_SCHEMA_PATH: Final = Path("schemas/template-v1.schema.json")
TEMPLATE_EXAMPLE_DIRECTORY: Final = Path("schemas/examples")
TEMPLATE_INVALID_FIXTURE_DIRECTORY: Final = Path("tests/fixtures/templates/v1/invalid")
TEMPLATE_EXAMPLE_PATTERN: Final = "template-v1-*.json"
SUPPORTED_SCHEMA_VERSION: Final = 1
DRAFT_2020_12_DIALECT: Final = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_IDENTIFIER: Final = "https://paperworkshell.invalid/schemas/template-v1.json"
CANONICAL_SCHEMA_FINGERPRINT: Final = (
    "dd156071a2ec546f6fafa663168b49b93cf8298e782b4d2d21564eab3b38a06f"
)
REPOSITORY_FIXTURE_AS_OF: Final = date(2026, 8, 1)

MAX_TEMPLATE_BYTES: Final = 1024 * 1024
MAX_SCHEMA_BYTES: Final = 2 * 1024 * 1024
MAX_JSON_DEPTH: Final = 48
MAX_JSON_NODES: Final = 32_768
MAX_JSON_STRING_CODEPOINTS: Final = 65_536
MAX_JSON_COLLECTION_ITEMS: Final = 4_096
MAX_FIXTURE_FILES: Final = 128
MAX_FIXTURE_DIRECTORY_ENTRIES: Final = 2_048
MAX_REPORTED_ISSUES: Final = 512
MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991
MAX_FILENAME_STEM_UTF16_UNITS: Final = 120
MAX_DOCUMENT_LABEL_CODEPOINTS: Final = 240
MAX_DOCUMENT_LABEL_SEGMENTS: Final = 32
MAX_DOCUMENT_LABEL_SEGMENT_CODEPOINTS: Final = 128

EXPECTED_POSITIVE_FIXTURES: Final = frozenset({"template-v1-minimal.json"})
EXPECTED_NEGATIVE_CODES: Final[dict[str, str]] = {
    "duplicate-key.json": "TEMPLATE_JSON_DUPLICATE_KEY",
    "eligibility-decision.json": "TEMPLATE_ELIGIBILITY_MEMBER",
    "expired.json": "TEMPLATE_EXPIRED",
    "future-version.json": "TEMPLATE_VERSION_UNSUPPORTED",
    "legacy-version.json": "TEMPLATE_VERSION_UNSUPPORTED",
    "script-injection.json": "TEMPLATE_EXECUTABLE_MEMBER",
    "trust-assertion.json": "TEMPLATE_TRUST_ASSERTION_MEMBER",
}

_SAFE_PATH_MEMBER_NAMES: Final = frozenset(
    {
        "$defs",
        "$id",
        "$ref",
        "$schema",
        "actor",
        "additionalProperties",
        "allowed_modes",
        "assertion_status",
        "collision_policy",
        "confirmation_id",
        "confirmations",
        "const",
        "count",
        "default_mode",
        "description",
        "effect",
        "encryption_policy",
        "enum",
        "expires_on",
        "extension_policy",
        "filename_policy",
        "flatten_policy",
        "format",
        "guidance",
        "instruction_id",
        "instructions",
        "items",
        "kind",
        "lifecycle",
        "limits",
        "locale",
        "maximum",
        "maximum_file_bytes",
        "maximum_file_count",
        "maximum_pages_per_file",
        "maximum_total_bytes",
        "maxItems",
        "maxLength",
        "message",
        "minimum",
        "minItems",
        "minLength",
        "not",
        "oneOf",
        "output",
        "output_version",
        "package_stem",
        "pattern",
        "policy_version",
        "properties",
        "provenance",
        "publisher",
        "required",
        "requirement_id",
        "requirement_order",
        "requirement_stems",
        "requirements",
        "review_due_on",
        "schema_version",
        "scope",
        "severity",
        "source_document_sha256",
        "source_kind",
        "source_reference",
        "source_url",
        "statement",
        "stem",
        "template_id",
        "template_version",
        "text",
        "title",
        "trust_policy",
        "type",
        "uniqueItems",
        "unit",
        "verified_on",
        "warning_id",
        "warnings",
    }
)
_SAFE_DOCUMENT_LABEL_SEGMENT: Final = re.compile(r"^[A-Za-z0-9._-]+$")
_INVALID_JSON_POINTER_ESCAPE: Final = re.compile(r"~(?:[^01]|$)")
_REFERENCE_KEYWORDS: Final = frozenset({"$ref", "$dynamicRef", "$recursiveRef"})
_UNSUPPORTED_SCHEMA_REFERENCE_KEYWORDS: Final = frozenset(
    {"$anchor", "$dynamicAnchor", "$dynamicRef", "$recursiveAnchor", "$recursiveRef"}
)
_EXECUTABLE_MEMBER_NAMES: Final = frozenset(
    {
        "arguments",
        "argv",
        "code",
        "command",
        "command_line",
        "commands",
        "compile",
        "cwd",
        "env",
        "environment",
        "eval",
        "exec",
        "executable",
        "expression",
        "expressions",
        "file_path",
        "formula",
        "formulas",
        "hook",
        "hooks",
        "import",
        "imports",
        "module",
        "modules",
        "path",
        "plugin",
        "plugins",
        "ref",
        "schema",
        "script",
        "scripts",
        "shell",
        "subprocess",
        "working_directory",
    }
)
_ELIGIBILITY_MEMBER_NAMES: Final = frozenset(
    {
        "condition",
        "conditions",
        "decision",
        "decision_tree",
        "decisions",
        "eligible",
        "eligible_if",
        "eligibility",
        "eligibility_rule",
        "eligibility_rules",
        "ineligible_if",
        "predicate",
        "predicates",
        "qualification",
        "qualifies",
        "score",
        "scoring",
        "when",
    }
)
_TRUST_ASSERTION_MEMBER_NAMES: Final = frozenset(
    {
        "approved",
        "is_approved",
        "is_official",
        "is_trusted",
        "official",
        "signature_valid",
        "signature_verified",
        "trust_override",
        "trusted",
        "verified_signature",
    }
)
_WINDOWS_FORBIDDEN_STEM_CHARACTERS: Final = frozenset('<>:"/\\|?*')
_WINDOWS_REPARSE_POINT_ATTRIBUTE: Final = 0x400
_WINDOWS_RESERVED_BASENAMES: Final = frozenset(
    {
        "aux",
        "clock$",
        "con",
        "conin$",
        "conout$",
        "nul",
        "prn",
        *(f"com{number}" for number in range(1, 10)),
        *(f"lpt{number}" for number in range(1, 10)),
        "com¹",
        "com²",
        "com³",
        "lpt¹",
        "lpt²",
        "lpt³",
    }
)
_SCHEMA_MESSAGES: Final[dict[object, str]] = {
    "additionalProperties": "unknown members are not permitted",
    "anyOf": "value does not match a supported shape",
    "const": "value is not supported",
    "dependentRequired": "a conditionally required member is missing",
    "enum": "value is not supported",
    "format": "value has an invalid format",
    "maxItems": "array length is outside the allowed range",
    "maxLength": "text length is outside the allowed range",
    "maxProperties": "object size is outside the allowed range",
    "maximum": "number is outside the allowed range",
    "minItems": "array length is outside the allowed range",
    "minLength": "text length is outside the allowed range",
    "minProperties": "object size is outside the allowed range",
    "minimum": "number is outside the allowed range",
    "not": "value is explicitly prohibited",
    "oneOf": "value does not match exactly one supported shape",
    "pattern": "value has an invalid format",
    "required": "a required member is missing",
    "type": "value has an invalid JSON type",
    "uniqueItems": "array members must be unique",
}


@dataclass(frozen=True)
class TemplateIssue:
    """One stable, non-disclosing template validation error."""

    code: str
    document: str
    path: str
    message: str

    def sort_key(self) -> tuple[str, str, str, str]:
        return (self.document, self.path, self.code, self.message)

    def render(self) -> str:
        return f"ERROR [{self.code}] {self.document}#{self.path}: {self.message}"


@dataclass(frozen=True)
class TemplateFixtureSummary:
    """Counts for the validated canonical and rejected template examples."""

    positive_count: int
    negative_count: int


class DuplicateJsonKeyError(ValueError):
    """Raised when a JSON object repeats a member name."""


class NonStandardJsonConstantError(ValueError):
    """Raised for NaN and Infinity, which are not standard JSON."""


class InvalidJsonNumberError(ValueError):
    """Raised for floating-point or out-of-safe-range JSON numbers."""


def _sanitize_document_label(document: object) -> str:
    fallback = "external-template.json"
    if (
        not isinstance(document, str)
        or not document
        or len(document) > MAX_DOCUMENT_LABEL_CODEPOINTS
    ):
        return fallback
    if (
        document.startswith(("/", "\\"))
        or "\\" in document
        or unicodedata.normalize("NFC", document) != document
        or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in document)
    ):
        return fallback
    segments = document.split("/")
    if not segments or len(segments) > MAX_DOCUMENT_LABEL_SEGMENTS:
        return fallback
    for segment in segments:
        if (
            not segment
            or segment in {".", ".."}
            or len(segment) > MAX_DOCUMENT_LABEL_SEGMENT_CODEPOINTS
            or segment.endswith((" ", "."))
            or _SAFE_DOCUMENT_LABEL_SEGMENT.fullmatch(segment) is None
            or segment.split(".", maxsplit=1)[0].casefold() in _WINDOWS_RESERVED_BASENAMES
        ):
            return fallback
    return document


def _issue(code: str, document: str, path: str, message: str) -> TemplateIssue:
    return TemplateIssue(
        code=code,
        document=_sanitize_document_label(document),
        path=path,
        message=message,
    )


def _add_issue(issues: list[TemplateIssue], issue: TemplateIssue) -> None:
    if len(issues) < MAX_REPORTED_ISSUES - 1:
        issues.append(issue)
    elif not any(existing.code == "TEMPLATE_ISSUE_LIMIT" for existing in issues):
        issues.append(
            _issue(
                "TEMPLATE_ISSUE_LIMIT",
                issue.document,
                "$",
                "additional validation errors were suppressed",
            )
        )


def _sorted_issues(issues: Iterable[TemplateIssue]) -> list[TemplateIssue]:
    return sorted(issues, key=TemplateIssue.sort_key)


def _json_path(parts: Iterable[object]) -> str:
    rendered = "$"
    for part in parts:
        if isinstance(part, int):
            rendered += f"[{part}]"
        elif isinstance(part, str) and part in _SAFE_PATH_MEMBER_NAMES:
            rendered += f".{part}"
        else:
            rendered += "[*]"
    return rendered


def _document_label(path: Path, root: Path) -> str:
    try:
        resolved_root = root.resolve(strict=False)
        resolved_path = path.resolve(strict=False)
        return resolved_path.relative_to(resolved_root).as_posix()
    except OSError, RuntimeError, ValueError:
        return "external-template.json"


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> NoReturn:
    raise NonStandardJsonConstantError


def _reject_json_float(_value: str) -> NoReturn:
    raise InvalidJsonNumberError


def _parse_bounded_json_integer(value: str) -> int:
    digits = value[1:] if value.startswith("-") else value
    if len(digits) > 16:
        raise InvalidJsonNumberError
    parsed = int(value, 10)
    if not -MAX_SAFE_INTEGER <= parsed <= MAX_SAFE_INTEGER:
        raise InvalidJsonNumberError
    return parsed


def _read_bounded_utf8(
    path: Path,
    *,
    document: str,
    maximum_bytes: int,
) -> tuple[str | None, list[TemplateIssue]]:
    try:
        with path.open("rb") as stream:
            content = stream.read(maximum_bytes + 1)
    except OSError as error:
        return None, [_issue("TEMPLATE_INPUT_UNREADABLE", document, "$", type(error).__name__)]
    if len(content) > maximum_bytes:
        return None, [
            _issue(
                "TEMPLATE_INPUT_TOO_LARGE",
                document,
                "$",
                "JSON input exceeds its byte limit",
            )
        ]
    try:
        return content.decode("utf-8"), []
    except UnicodeDecodeError:
        return None, [
            _issue(
                "TEMPLATE_TEXT_ENCODING_INVALID",
                document,
                "$",
                "input must be valid UTF-8",
            )
        ]


def _json_resource_issue(loaded: Mapping[str, Any], document: str) -> TemplateIssue | None:
    queue: deque[tuple[object, int]] = deque([(loaded, 1)])
    node_count = 0
    while queue:
        value, depth = queue.popleft()
        node_count += 1
        if node_count > MAX_JSON_NODES:
            return _issue(
                "TEMPLATE_JSON_RESOURCE_LIMIT",
                document,
                "$",
                "JSON input exceeds its structural node limit",
            )
        if depth > MAX_JSON_DEPTH:
            return _issue(
                "TEMPLATE_JSON_RESOURCE_LIMIT",
                document,
                "$",
                "JSON input exceeds its nesting limit",
            )
        if isinstance(value, Mapping):
            if len(value) > MAX_JSON_COLLECTION_ITEMS:
                return _issue(
                    "TEMPLATE_JSON_RESOURCE_LIMIT",
                    document,
                    "$",
                    "JSON object exceeds its member limit",
                )
            for key, child in value.items():
                if not isinstance(key, str):
                    return _issue(
                        "TEMPLATE_JSON_VALUE_TYPE",
                        document,
                        "$",
                        "decoded JSON object member names must be strings",
                    )
                node_count += 1
                if node_count > MAX_JSON_NODES:
                    return _issue(
                        "TEMPLATE_JSON_RESOURCE_LIMIT",
                        document,
                        "$",
                        "JSON input exceeds its structural node limit",
                    )
                if len(key) > MAX_JSON_STRING_CODEPOINTS:
                    return _issue(
                        "TEMPLATE_JSON_RESOURCE_LIMIT",
                        document,
                        "$",
                        "JSON text exceeds its string limit",
                    )
                queue.append((child, depth + 1))
        elif isinstance(value, list):
            if len(value) > MAX_JSON_COLLECTION_ITEMS:
                return _issue(
                    "TEMPLATE_JSON_RESOURCE_LIMIT",
                    document,
                    "$",
                    "JSON array exceeds its item limit",
                )
            queue.extend((child, depth + 1) for child in value)
        elif isinstance(value, str) and len(value) > MAX_JSON_STRING_CODEPOINTS:
            return _issue(
                "TEMPLATE_JSON_RESOURCE_LIMIT",
                document,
                "$",
                "JSON text exceeds its string limit",
            )
        elif type(value) is int and not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            return _issue(
                "TEMPLATE_JSON_NONSTANDARD_NUMBER",
                document,
                "$",
                "floating-point and out-of-range numbers are not permitted",
            )
        elif isinstance(value, float):
            return _issue(
                "TEMPLATE_JSON_NONSTANDARD_NUMBER",
                document,
                "$",
                "floating-point and out-of-range numbers are not permitted",
            )
        elif value is not None and type(value) not in {bool, int, str}:
            return _issue(
                "TEMPLATE_JSON_VALUE_TYPE",
                document,
                "$",
                "decoded template contains a value outside the JSON data model",
            )
    return None


def _load_json_object(
    path: Path,
    *,
    root: Path,
    maximum_bytes: int,
) -> tuple[dict[str, Any] | None, list[TemplateIssue]]:
    document = _document_label(path, root)
    text, read_issues = _read_bounded_utf8(
        path,
        document=document,
        maximum_bytes=maximum_bytes,
    )
    if text is None:
        return None, read_issues
    try:
        loaded = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
            parse_float=_reject_json_float,
            parse_int=_parse_bounded_json_integer,
        )
    except DuplicateJsonKeyError:
        return None, [
            _issue(
                "TEMPLATE_JSON_DUPLICATE_KEY",
                document,
                "$",
                "object member names must be unique",
            )
        ]
    except NonStandardJsonConstantError:
        return None, [
            _issue(
                "TEMPLATE_JSON_NONSTANDARD_CONSTANT",
                document,
                "$",
                "NaN and Infinity are not permitted",
            )
        ]
    except InvalidJsonNumberError:
        return None, [
            _issue(
                "TEMPLATE_JSON_NONSTANDARD_NUMBER",
                document,
                "$",
                "floating-point and out-of-range numbers are not permitted",
            )
        ]
    except json.JSONDecodeError as error:
        return None, [
            _issue(
                "TEMPLATE_JSON_INVALID",
                document,
                "$",
                f"invalid JSON at line {error.lineno}, column {error.colno}",
            )
        ]
    except RecursionError, ValueError:
        return None, [
            _issue(
                "TEMPLATE_JSON_INVALID",
                document,
                "$",
                "JSON numeric or nesting syntax is invalid",
            )
        ]
    if not isinstance(loaded, dict):
        return None, [
            _issue(
                "TEMPLATE_JSON_ROOT_TYPE",
                document,
                "$",
                "root value must be an object",
            )
        ]
    result = cast(dict[str, Any], loaded)
    resource_issue = _json_resource_issue(result, document)
    if resource_issue is not None:
        return None, [resource_issue]
    return result, []


def _schema_fingerprint(schema: Mapping[str, Any]) -> str | None:
    try:
        canonical = json.dumps(
            schema,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except RecursionError, TypeError, UnicodeEncodeError, ValueError:
        return None
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _schema_references(schema: Mapping[str, Any]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    stack: list[tuple[object, tuple[object, ...]]] = [(schema, ())]
    while stack:
        value, path = stack.pop()
        if isinstance(value, Mapping):
            for key, child in value.items():
                child_path = (*path, key)
                if key in _REFERENCE_KEYWORDS and isinstance(child, str):
                    found.append((_json_path(child_path), child))
                else:
                    stack.append((child, child_path))
        elif isinstance(value, list):
            stack.extend((child, (*path, index)) for index, child in enumerate(value))
    return found


def _local_reference_target_path(
    schema: Mapping[str, Any], reference: str
) -> tuple[object, ...] | None:
    if reference == "#":
        return ()
    if not reference.startswith("#/"):
        return None
    current: object = schema
    target_path: list[object] = []
    for encoded_part in reference[2:].split("/"):
        if _INVALID_JSON_POINTER_ESCAPE.search(encoded_part):
            return None
        part = encoded_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping) and part in current:
            current = current[part]
            target_path.append(part)
        elif isinstance(current, list) and part.isascii() and part.isdecimal():
            if not current or (len(part) > 1 and part.startswith("0")):
                return None
            if len(part) > len(str(len(current) - 1)):
                return None
            index = int(part)
            if index >= len(current):
                return None
            current = current[index]
            target_path.append(index)
        else:
            return None
    return tuple(target_path)


def _schema_has_reference_cycle(schema: Mapping[str, Any]) -> bool:
    graph: dict[tuple[object, ...], list[tuple[object, ...]]] = {}
    stack: list[tuple[object, tuple[object, ...]]] = [(schema, ())]
    while stack:
        value, path = stack.pop()
        edges = graph.setdefault(path, [])
        if isinstance(value, Mapping):
            for key, child in value.items():
                child_path = (*path, key)
                if key == "$ref" and isinstance(child, str):
                    target = _local_reference_target_path(schema, child)
                    if target is not None:
                        edges.append(target)
                elif isinstance(child, (Mapping, list)):
                    edges.append(child_path)
                    stack.append((child, child_path))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                if isinstance(child, (Mapping, list)):
                    child_path = (*path, index)
                    edges.append(child_path)
                    stack.append((child, child_path))
    indegree = {node: 0 for node in graph}
    for targets in graph.values():
        for target in targets:
            indegree.setdefault(target, 0)
            indegree[target] += 1
    ready = deque(node for node, count in indegree.items() if count == 0)
    visited = 0
    while ready:
        node = ready.popleft()
        visited += 1
        for target in graph.get(node, ()):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    return visited != len(indegree)


def _schema_policy_issues(schema: Mapping[str, Any], document: str) -> list[TemplateIssue]:
    issues: list[TemplateIssue] = []
    if schema.get("$schema") != DRAFT_2020_12_DIALECT or schema.get("$id") != SCHEMA_IDENTIFIER:
        _add_issue(
            issues,
            _issue(
                "TEMPLATE_SCHEMA_IDENTITY_INVALID",
                document,
                "$",
                "schema dialect and identifier must match the reviewed v1 contract",
            ),
        )
    stack: list[tuple[object, tuple[object, ...]]] = [(schema, ())]
    while stack:
        value, path = stack.pop()
        if isinstance(value, Mapping):
            for key, child in value.items():
                child_path = (*path, key)
                if key in {"$id", "$schema"} and path:
                    _add_issue(
                        issues,
                        _issue(
                            "TEMPLATE_SCHEMA_RESOURCE_SCOPE_UNSAFE",
                            document,
                            _json_path(child_path),
                            "nested schema resources and dialect declarations are not permitted",
                        ),
                    )
                if key in _UNSUPPORTED_SCHEMA_REFERENCE_KEYWORDS:
                    _add_issue(
                        issues,
                        _issue(
                            "TEMPLATE_SCHEMA_REFERENCE_FEATURE_UNSAFE",
                            document,
                            _json_path(child_path),
                            "dynamic, recursive, and anchor reference features are not permitted",
                        ),
                    )
                stack.append((child, child_path))
        elif isinstance(value, list):
            stack.extend((child, (*path, index)) for index, child in enumerate(value))
    for reference_path, reference in _schema_references(schema):
        if not reference.startswith("#"):
            _add_issue(
                issues,
                _issue(
                    "TEMPLATE_SCHEMA_REFERENCE_UNSAFE",
                    document,
                    reference_path,
                    "schema references must remain inside this schema",
                ),
            )
        elif _local_reference_target_path(schema, reference) is None:
            _add_issue(
                issues,
                _issue(
                    "TEMPLATE_SCHEMA_REFERENCE_INVALID",
                    document,
                    reference_path,
                    "local schema reference does not resolve",
                ),
            )
    if not issues and _schema_has_reference_cycle(schema):
        _add_issue(
            issues,
            _issue(
                "TEMPLATE_SCHEMA_REFERENCE_CYCLE",
                document,
                "$",
                "recursive local schema references are not permitted",
            ),
        )
    if not issues and _schema_fingerprint(schema) != CANONICAL_SCHEMA_FINGERPRINT:
        _add_issue(
            issues,
            _issue(
                "TEMPLATE_SCHEMA_IDENTITY_MISMATCH",
                document,
                "$",
                "schema does not match the reviewed canonical contract",
            ),
        )
    return _sorted_issues(issues)


def load_template_schema(
    root: Path = PROJECT_ROOT,
) -> tuple[dict[str, Any] | None, list[TemplateIssue]]:
    """Strictly load and check the self-contained Draft 2020-12 template schema."""

    schema, issues = _load_json_object(
        root / TEMPLATE_SCHEMA_PATH,
        root=root,
        maximum_bytes=MAX_SCHEMA_BYTES,
    )
    if schema is None:
        return None, issues
    document = TEMPLATE_SCHEMA_PATH.as_posix()
    policy_issues = _schema_policy_issues(schema, document)
    if policy_issues:
        return None, policy_issues
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        return None, [
            _issue(
                "TEMPLATE_SCHEMA_DEFINITION_INVALID",
                document,
                _json_path(error.absolute_schema_path),
                "schema definition is invalid",
            )
        ]
    except Exception:
        return None, [
            _issue(
                "TEMPLATE_SCHEMA_EXECUTION_INVALID",
                document,
                "$",
                "schema validation could not complete safely",
            )
        ]
    return schema, []


def _normalized_member_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")


def _prohibited_member_issues(data: Mapping[str, Any], document: str) -> list[TemplateIssue]:
    issues: list[TemplateIssue] = []
    queue: deque[tuple[object, tuple[object, ...]]] = deque([(data, ())])
    while queue:
        value, path = queue.popleft()
        if isinstance(value, Mapping):
            for key, child in value.items():
                normalized = _normalized_member_name(key)
                if normalized in _EXECUTABLE_MEMBER_NAMES:
                    _add_issue(
                        issues,
                        _issue(
                            "TEMPLATE_EXECUTABLE_MEMBER",
                            document,
                            _json_path(path),
                            "executable, import, command, expression, or path members are prohibited",
                        ),
                    )
                elif normalized in _ELIGIBILITY_MEMBER_NAMES:
                    _add_issue(
                        issues,
                        _issue(
                            "TEMPLATE_ELIGIBILITY_MEMBER",
                            document,
                            _json_path(path),
                            "templates cannot encode eligibility or substantive decisions",
                        ),
                    )
                elif normalized in _TRUST_ASSERTION_MEMBER_NAMES:
                    _add_issue(
                        issues,
                        _issue(
                            "TEMPLATE_TRUST_ASSERTION_MEMBER",
                            document,
                            _json_path(path),
                            "embedded content cannot assert its own trust or approval",
                        ),
                    )
                queue.append((child, (*path, key)))
        elif isinstance(value, list):
            queue.extend((child, (*path, index)) for index, child in enumerate(value))
    return _sorted_issues(issues)


def _text_safety_issues(data: Mapping[str, Any], document: str) -> list[TemplateIssue]:
    """Reject ambiguous Unicode without echoing template-controlled text."""

    def is_unsafe(value: str) -> bool:
        return unicodedata.normalize("NFC", value) != value or any(
            unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value
        )

    issues: list[TemplateIssue] = []
    queue: deque[tuple[object, tuple[object, ...]]] = deque([(data, ())])
    while queue:
        value, path = queue.popleft()
        if isinstance(value, Mapping):
            for key, child in value.items():
                child_path = (*path, key)
                if is_unsafe(key):
                    _add_issue(
                        issues,
                        _issue(
                            "TEMPLATE_TEXT_UNSAFE",
                            document,
                            _json_path(child_path),
                            "text must be NFC and contain no control, formatting, or surrogate characters",
                        ),
                    )
                queue.append((child, child_path))
        elif isinstance(value, list):
            queue.extend((child, (*path, index)) for index, child in enumerate(value))
        elif isinstance(value, str) and is_unsafe(value):
            _add_issue(
                issues,
                _issue(
                    "TEMPLATE_TEXT_UNSAFE",
                    document,
                    _json_path(path),
                    "text must be NFC and contain no control, formatting, or surrogate characters",
                ),
            )
    return _sorted_issues(issues)


def _schema_validation_issues(
    data: Mapping[str, Any],
    schema: Mapping[str, Any],
    document: str,
) -> list[TemplateIssue]:
    issues: list[TemplateIssue] = []
    try:
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        for error in validator.iter_errors(data):
            _add_issue(
                issues,
                _issue(
                    "TEMPLATE_SCHEMA_VALIDATION",
                    document,
                    _json_path(error.absolute_path),
                    _SCHEMA_MESSAGES.get(error.validator, "value violates the template schema"),
                ),
            )
            if any(issue.code == "TEMPLATE_ISSUE_LIMIT" for issue in issues):
                break
    except Exception:
        return [
            _issue(
                "TEMPLATE_SCHEMA_EXECUTION_INVALID",
                document,
                "$",
                "schema validation could not complete safely",
            )
        ]
    return _sorted_issues(issues)


def _parse_date(value: str, document: str, path: str, issues: list[TemplateIssue]) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        _add_issue(
            issues,
            _issue("TEMPLATE_DATE_INVALID", document, path, "date value is invalid"),
        )
        return None


def _index_records(
    records: Sequence[Mapping[str, Any]],
    *,
    id_field: str,
    collection_path: str,
    namespace: dict[str, str],
    document: str,
    issues: list[TemplateIssue],
) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for index, record in enumerate(records):
        record_id = cast(str, record[id_field])
        if record_id in indexed:
            _add_issue(
                issues,
                _issue(
                    "TEMPLATE_ID_DUPLICATE",
                    document,
                    f"{collection_path}[{index}].{id_field}",
                    "identifier must be unique in its collection",
                ),
            )
        else:
            indexed[record_id] = record
        previous = namespace.get(record_id)
        if previous is not None and previous != collection_path:
            _add_issue(
                issues,
                _issue(
                    "TEMPLATE_ID_NAMESPACE_COLLISION",
                    document,
                    f"{collection_path}[{index}].{id_field}",
                    "identifier collides with another template namespace",
                ),
            )
        else:
            namespace[record_id] = collection_path
    return indexed


def _check_requirement_reference(
    requirement_id: str,
    requirement_index: Mapping[str, Mapping[str, Any]],
    *,
    document: str,
    path: str,
    issues: list[TemplateIssue],
) -> None:
    if requirement_id not in requirement_index:
        _add_issue(
            issues,
            _issue(
                "TEMPLATE_REQUIREMENT_REFERENCE_UNKNOWN",
                document,
                path,
                "requirement reference does not resolve",
            ),
        )


def _safe_filename_stem(stem: str) -> bool:
    if not stem or stem in {".", ".."} or stem.endswith((" ", ".")):
        return False
    if unicodedata.normalize("NFC", stem) != stem:
        return False
    if any(character in _WINDOWS_FORBIDDEN_STEM_CHARACTERS for character in stem):
        return False
    if any(unicodedata.category(character).startswith("C") for character in stem):
        return False
    device_basename = stem.split(".", maxsplit=1)[0].casefold()
    if device_basename in _WINDOWS_RESERVED_BASENAMES:
        return False
    return len(stem.encode("utf-16-le")) // 2 <= MAX_FILENAME_STEM_UTF16_UNITS


def _url_is_inert_https(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
        and (port is None or 1 <= port <= 65_535)
        and "\\" not in value
        and not any(unicodedata.category(character).startswith("C") for character in value)
    )


def _semantic_issues(
    data: Mapping[str, Any],
    document: str,
    *,
    as_of: date | None,
) -> list[TemplateIssue]:
    issues: list[TemplateIssue] = []
    namespace: dict[str, str] = {}
    instructions = cast(list[Mapping[str, Any]], data["instructions"])
    requirements = cast(list[Mapping[str, Any]], data["requirements"])
    warnings = cast(list[Mapping[str, Any]], data["warnings"])
    confirmations = cast(list[Mapping[str, Any]], data["confirmations"])
    _index_records(
        instructions,
        id_field="instruction_id",
        collection_path="$.instructions",
        namespace=namespace,
        document=document,
        issues=issues,
    )
    requirement_index = _index_records(
        requirements,
        id_field="requirement_id",
        collection_path="$.requirements",
        namespace=namespace,
        document=document,
        issues=issues,
    )
    _index_records(
        warnings,
        id_field="warning_id",
        collection_path="$.warnings",
        namespace=namespace,
        document=document,
        issues=issues,
    )
    _index_records(
        confirmations,
        id_field="confirmation_id",
        collection_path="$.confirmations",
        namespace=namespace,
        document=document,
        issues=issues,
    )

    requirement_order = cast(list[str], data["requirement_order"])
    seen_order: set[str] = set()
    for index, requirement_id in enumerate(requirement_order):
        if requirement_id in seen_order:
            _add_issue(
                issues,
                _issue(
                    "TEMPLATE_REQUIREMENT_ORDER_DUPLICATE",
                    document,
                    f"$.requirement_order[{index}]",
                    "requirement ordering cannot repeat an identifier",
                ),
            )
        seen_order.add(requirement_id)
        _check_requirement_reference(
            requirement_id,
            requirement_index,
            document=document,
            path=f"$.requirement_order[{index}]",
            issues=issues,
        )
    if set(requirement_order) != set(requirement_index):
        _add_issue(
            issues,
            _issue(
                "TEMPLATE_REQUIREMENT_ORDER_INCOMPLETE",
                document,
                "$.requirement_order",
                "requirement ordering must contain every requirement exactly once",
            ),
        )

    for index, requirement in enumerate(requirements):
        count = cast(Mapping[str, Any], requirement["count"])
        minimum = cast(int, count["minimum"])
        maximum = cast(int, count["maximum"])
        if minimum > maximum:
            _add_issue(
                issues,
                _issue(
                    "TEMPLATE_COUNT_RANGE_INVALID",
                    document,
                    f"$.requirements[{index}].count",
                    "minimum count cannot exceed maximum count",
                ),
            )

    for collection_name, records in (("warnings", warnings), ("confirmations", confirmations)):
        for index, record in enumerate(records):
            scope = cast(Mapping[str, Any], record["scope"])
            if scope["kind"] == "requirement":
                _check_requirement_reference(
                    cast(str, scope["requirement_id"]),
                    requirement_index,
                    document=document,
                    path=f"$.{collection_name}[{index}].scope.requirement_id",
                    issues=issues,
                )

    output = cast(Mapping[str, Any], data["output"])
    allowed_modes = cast(list[str], output["allowed_modes"])
    default_mode = cast(str, output["default_mode"])
    if default_mode not in allowed_modes:
        _add_issue(
            issues,
            _issue(
                "TEMPLATE_DEFAULT_MODE_INVALID",
                document,
                "$.output.default_mode",
                "default output mode must be one of the allowed modes",
            ),
        )
    if len(set(allowed_modes)) != len(allowed_modes):
        _add_issue(
            issues,
            _issue(
                "TEMPLATE_OUTPUT_MODE_DUPLICATE",
                document,
                "$.output.allowed_modes",
                "allowed output modes must be unique",
            ),
        )
    limits = cast(Mapping[str, Any], output["limits"])
    maximum_file_count = cast(int, limits["maximum_file_count"])
    maximum_total_bytes = cast(int, limits["maximum_total_bytes"])
    maximum_file_bytes = cast(int, limits["maximum_file_bytes"])
    if maximum_file_bytes > maximum_total_bytes:
        _add_issue(
            issues,
            _issue(
                "TEMPLATE_OUTPUT_LIMIT_INVALID",
                document,
                "$.output.limits.maximum_file_bytes",
                "per-file byte limit cannot exceed the total byte limit",
            ),
        )
    if "separate_pdfs" in allowed_modes and maximum_file_count < len(requirement_index):
        _add_issue(
            issues,
            _issue(
                "TEMPLATE_OUTPUT_LIMIT_INVALID",
                document,
                "$.output.limits.maximum_file_count",
                "file-count limit cannot represent every requirement in separate-PDF mode",
            ),
        )

    filename_policy = cast(Mapping[str, Any], output["filename_policy"])
    stems = cast(list[Mapping[str, Any]], filename_policy["requirement_stems"])
    package_stem = cast(str, filename_policy["package_stem"])
    if not _safe_filename_stem(package_stem):
        _add_issue(
            issues,
            _issue(
                "TEMPLATE_FILENAME_STEM_INVALID",
                document,
                "$.output.filename_policy.package_stem",
                "filename stem is not safe for the Windows output boundary",
            ),
        )
    seen_stem_requirements: set[str] = set()
    seen_stems: set[str] = {package_stem.casefold()}
    for index, stem_record in enumerate(stems):
        requirement_id = cast(str, stem_record["requirement_id"])
        stem = cast(str, stem_record["stem"])
        _check_requirement_reference(
            requirement_id,
            requirement_index,
            document=document,
            path=f"$.output.filename_policy.requirement_stems[{index}].requirement_id",
            issues=issues,
        )
        if requirement_id in seen_stem_requirements:
            _add_issue(
                issues,
                _issue(
                    "TEMPLATE_REQUIREMENT_STEM_DUPLICATE",
                    document,
                    f"$.output.filename_policy.requirement_stems[{index}].requirement_id",
                    "requirement filename policy cannot repeat an identifier",
                ),
            )
        seen_stem_requirements.add(requirement_id)
        if not _safe_filename_stem(stem):
            _add_issue(
                issues,
                _issue(
                    "TEMPLATE_FILENAME_STEM_INVALID",
                    document,
                    f"$.output.filename_policy.requirement_stems[{index}].stem",
                    "filename stem is not safe for the Windows output boundary",
                ),
            )
        folded = stem.casefold()
        if folded in seen_stems:
            _add_issue(
                issues,
                _issue(
                    "TEMPLATE_FILENAME_STEM_COLLISION",
                    document,
                    f"$.output.filename_policy.requirement_stems[{index}].stem",
                    "filename stems must be unique under Windows case folding",
                ),
            )
        seen_stems.add(folded)
    if "separate_pdfs" in allowed_modes and seen_stem_requirements != set(requirement_index):
        _add_issue(
            issues,
            _issue(
                "TEMPLATE_REQUIREMENT_STEMS_INCOMPLETE",
                document,
                "$.output.filename_policy.requirement_stems",
                "filename policy must name every requirement exactly once",
            ),
        )
    elif "separate_pdfs" not in allowed_modes and stems:
        _add_issue(
            issues,
            _issue(
                "TEMPLATE_REQUIREMENT_STEMS_UNUSED",
                document,
                "$.output.filename_policy.requirement_stems",
                "requirement filename stems require the separate-PDF output mode",
            ),
        )

    lifecycle = cast(Mapping[str, Any], data["lifecycle"])
    verified_on = _parse_date(
        cast(str, lifecycle["verified_on"]),
        document,
        "$.lifecycle.verified_on",
        issues,
    )
    review_due_on = _parse_date(
        cast(str, lifecycle["review_due_on"]),
        document,
        "$.lifecycle.review_due_on",
        issues,
    )
    expires_on = _parse_date(
        cast(str, lifecycle["expires_on"]),
        document,
        "$.lifecycle.expires_on",
        issues,
    )
    if (
        verified_on is not None
        and review_due_on is not None
        and expires_on is not None
        and not verified_on <= review_due_on <= expires_on
    ):
        _add_issue(
            issues,
            _issue(
                "TEMPLATE_LIFECYCLE_ORDER_INVALID",
                document,
                "$.lifecycle",
                "verification, review, and expiry dates must be ordered",
            ),
        )
    if as_of is not None and expires_on is not None and as_of > expires_on:
        _add_issue(
            issues,
            _issue(
                "TEMPLATE_EXPIRED",
                document,
                "$.lifecycle.expires_on",
                "template is expired for the supplied evaluation date",
            ),
        )
    if as_of is not None and verified_on is not None and as_of < verified_on:
        _add_issue(
            issues,
            _issue(
                "TEMPLATE_LIFECYCLE_AS_OF_INVALID",
                document,
                "$.lifecycle.verified_on",
                "template verification date is later than the supplied evaluation date",
            ),
        )

    provenance = cast(Mapping[str, Any], data["provenance"])
    source_url = cast(str | None, provenance["source_url"])
    if source_url is not None and not _url_is_inert_https(source_url):
        _add_issue(
            issues,
            _issue(
                "TEMPLATE_PROVENANCE_URL_INVALID",
                document,
                "$.provenance.source_url",
                "provenance URL must be an inert HTTPS URL without credentials or a fragment",
            ),
        )
    if provenance["assertion_status"] != "untrusted_embedded_claims":
        _add_issue(
            issues,
            _issue(
                "TEMPLATE_TRUST_ASSERTION_INVALID",
                document,
                "$.provenance.assertion_status",
                "template content cannot establish its own trust",
            ),
        )
    if provenance["trust_policy"] != "external_verification_required":
        _add_issue(
            issues,
            _issue(
                "TEMPLATE_TRUST_ASSERTION_INVALID",
                document,
                "$.provenance.trust_policy",
                "template trust requires verification outside embedded content",
            ),
        )
    return _sorted_issues(issues)


def validate_template_data(
    data: Mapping[str, Any],
    schema: Mapping[str, Any],
    *,
    document: str = "template.json",
    as_of: date | None = None,
) -> list[TemplateIssue]:
    """Validate one decoded template without mutating or dereferencing it."""

    version = data.get("schema_version")
    if type(version) is not int:
        return [
            _issue(
                "TEMPLATE_VERSION_INVALID",
                document,
                "$.schema_version",
                "schema version must be an integer",
            )
        ]
    if version != SUPPORTED_SCHEMA_VERSION:
        return [
            _issue(
                "TEMPLATE_VERSION_UNSUPPORTED",
                document,
                "$.schema_version",
                "schema version is not supported",
            )
        ]
    if _schema_fingerprint(schema) != CANONICAL_SCHEMA_FINGERPRINT:
        return [
            _issue(
                "TEMPLATE_SCHEMA_IDENTITY_MISMATCH",
                document,
                "$",
                "schema does not match the reviewed canonical contract",
            )
        ]
    resource_issue = _json_resource_issue(data, document)
    if resource_issue is not None:
        return [resource_issue]
    prohibited_issues = _prohibited_member_issues(data, document)
    if prohibited_issues:
        return prohibited_issues
    text_issues = _text_safety_issues(data, document)
    if text_issues:
        return text_issues
    schema_issues = _schema_validation_issues(data, schema, document)
    if schema_issues:
        return schema_issues
    try:
        return _semantic_issues(data, document, as_of=as_of)
    except Exception:
        return [
            _issue(
                "TEMPLATE_SEMANTIC_EXECUTION_INVALID",
                document,
                "$",
                "semantic validation could not complete safely",
            )
        ]


def validate_template_document(
    path: Path,
    root: Path = PROJECT_ROOT,
    *,
    schema: Mapping[str, Any] | None = None,
    as_of: date | None = None,
) -> tuple[dict[str, Any] | None, list[TemplateIssue]]:
    """Strictly load and validate one declarative-template JSON document."""

    active_schema = schema
    if active_schema is None:
        active_schema, schema_issues = load_template_schema(root)
        if active_schema is None:
            return None, schema_issues
    data, load_issues = _load_json_object(
        path,
        root=root,
        maximum_bytes=MAX_TEMPLATE_BYTES,
    )
    if data is None:
        return None, load_issues
    issues = validate_template_data(
        data,
        active_schema,
        document=_document_label(path, root),
        as_of=as_of,
    )
    return (None, issues) if issues else (data, [])


def _path_is_link_or_reparse(path: Path) -> bool:
    metadata = path.lstat()
    file_attributes = cast(int, getattr(metadata, "st_file_attributes", 0))
    return path.is_symlink() or bool(file_attributes & _WINDOWS_REPARSE_POINT_ATTRIBUTE)


def _fixture_paths(
    directory: Path,
    *,
    pattern: str,
    expected_names: frozenset[str],
    root: Path,
    set_code: str,
    strict_entries: bool = False,
) -> tuple[list[Path] | None, list[TemplateIssue]]:
    document = _document_label(directory, root)
    try:
        if _path_is_link_or_reparse(directory):
            return None, [
                _issue(
                    "TEMPLATE_FIXTURE_PATH_UNSAFE",
                    document,
                    "$",
                    "fixture paths must remain regular files inside their fixture directory",
                )
            ]
        resolved_root = root.resolve(strict=True)
        resolved_directory = directory.resolve(strict=True)
        if not resolved_directory.is_relative_to(resolved_root):
            return None, [
                _issue(
                    "TEMPLATE_FIXTURE_PATH_UNSAFE",
                    document,
                    "$",
                    "fixture paths must remain regular files inside their fixture directory",
                )
            ]
        candidates: list[Path] = []
        entry_count = 0
        for candidate in directory.iterdir():
            entry_count += 1
            if entry_count > MAX_FIXTURE_DIRECTORY_ENTRIES:
                return None, [
                    _issue(
                        "TEMPLATE_FIXTURE_LIMIT",
                        document,
                        "$",
                        "fixture directory exceeds its entry-count limit",
                    )
                ]
            matches_pattern = candidate.match(pattern)
            if strict_entries and _path_is_link_or_reparse(candidate):
                return None, [
                    _issue(
                        "TEMPLATE_FIXTURE_PATH_UNSAFE",
                        document,
                        "$",
                        "fixture paths must remain regular files inside their fixture directory",
                    )
                ]
            if strict_entries and (
                candidate.name not in expected_names
                or not matches_pattern
                or not candidate.is_file()
            ):
                return None, [
                    _issue(
                        set_code,
                        document,
                        "$",
                        "fixture set does not match the reviewed contract",
                    )
                ]
            if not matches_pattern:
                continue
            if (
                _path_is_link_or_reparse(candidate)
                or not candidate.is_file()
                or candidate.resolve(strict=True).parent != resolved_directory
            ):
                return None, [
                    _issue(
                        "TEMPLATE_FIXTURE_PATH_UNSAFE",
                        document,
                        "$",
                        "fixture paths must remain regular files inside their fixture directory",
                    )
                ]
            candidates.append(candidate)
            if len(candidates) > MAX_FIXTURE_FILES:
                return None, [
                    _issue(
                        "TEMPLATE_FIXTURE_LIMIT",
                        document,
                        "$",
                        "fixture set exceeds its file-count limit",
                    )
                ]
    except (OSError, ValueError) as error:
        return None, [
            _issue(
                "TEMPLATE_FIXTURE_SET_UNREADABLE",
                document,
                "$",
                type(error).__name__,
            )
        ]
    actual_names = {candidate.name for candidate in candidates}
    if actual_names != expected_names:
        return None, [
            _issue(
                set_code,
                document,
                "$",
                "fixture set does not match the reviewed contract",
            )
        ]
    return sorted(candidates, key=lambda candidate: candidate.name), []


def validate_template_fixture_set(
    root: Path = PROJECT_ROOT,
) -> tuple[TemplateFixtureSummary | None, list[TemplateIssue]]:
    """Validate canonical examples and prove every rejected fixture fails as intended."""

    schema, schema_issues = load_template_schema(root)
    if schema is None:
        return None, schema_issues
    positive_paths, positive_path_issues = _fixture_paths(
        root / TEMPLATE_EXAMPLE_DIRECTORY,
        pattern=TEMPLATE_EXAMPLE_PATTERN,
        expected_names=EXPECTED_POSITIVE_FIXTURES,
        root=root,
        set_code="TEMPLATE_POSITIVE_FIXTURE_SET_MISMATCH",
    )
    invalid_paths, invalid_path_issues = _fixture_paths(
        root / TEMPLATE_INVALID_FIXTURE_DIRECTORY,
        pattern="*.json",
        expected_names=frozenset(EXPECTED_NEGATIVE_CODES),
        root=root,
        set_code="TEMPLATE_NEGATIVE_FIXTURE_SET_MISMATCH",
        strict_entries=True,
    )
    path_issues = [*positive_path_issues, *invalid_path_issues]
    if path_issues or positive_paths is None or invalid_paths is None:
        return None, _sorted_issues(path_issues)
    issues: list[TemplateIssue] = []
    for path in positive_paths:
        _, fixture_issues = validate_template_document(
            path,
            root,
            schema=schema,
            as_of=REPOSITORY_FIXTURE_AS_OF,
        )
        for fixture_issue in fixture_issues:
            _add_issue(
                issues,
                _issue(
                    "TEMPLATE_POSITIVE_FIXTURE_INVALID",
                    fixture_issue.document,
                    fixture_issue.path,
                    f"canonical template example failed with {fixture_issue.code}",
                ),
            )
    for path in invalid_paths:
        data, fixture_issues = validate_template_document(
            path,
            root,
            schema=schema,
            as_of=REPOSITORY_FIXTURE_AS_OF,
        )
        if data is not None or not fixture_issues:
            _add_issue(
                issues,
                _issue(
                    "TEMPLATE_NEGATIVE_FIXTURE_ACCEPTED",
                    _document_label(path, root),
                    "$",
                    "rejected template example was accepted",
                ),
            )
            continue
        expected_code = EXPECTED_NEGATIVE_CODES[path.name]
        if {issue.code for issue in fixture_issues} != {expected_code}:
            _add_issue(
                issues,
                _issue(
                    "TEMPLATE_NEGATIVE_FIXTURE_REASON_MISMATCH",
                    _document_label(path, root),
                    "$",
                    "rejected template example failed for an unexpected reason",
                ),
            )
    if issues:
        return None, _sorted_issues(issues)
    return TemplateFixtureSummary(len(positive_paths), len(invalid_paths)), []


def validation_messages(root: Path = PROJECT_ROOT) -> list[str]:
    """Return deterministic diagnostics for schema-registry integration."""

    _, issues = validate_template_fixture_set(root)
    return [issue.render() for issue in issues]


def _parse_as_of(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("as-of must be an ISO calendar date") from error


def main(argv: Sequence[str] | None = None) -> int:
    """Validate the checked-in template contract or one supplied JSON document."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=PROJECT_ROOT,
        help="repository root (default: detected project root)",
    )
    parser.add_argument(
        "--as-of",
        type=_parse_as_of,
        help="explicit ISO date for expiry evaluation; omitted means structural validation only",
    )
    parser.add_argument("document", nargs="?", type=Path)
    arguments = parser.parse_args(argv)
    if arguments.document is None:
        summary, issues = validate_template_fixture_set(arguments.root)
    else:
        _, issues = validate_template_document(
            arguments.document,
            arguments.root,
            as_of=arguments.as_of,
        )
        summary = None
    if issues:
        for issue in issues:
            print(issue.render(), file=sys.stderr)
        return 1
    if summary is None:
        print("OK: validated one declarative-template v1 document.")
    else:
        print(
            "OK: validated declarative-template v1 with "
            f"{summary.positive_count} positive and {summary.negative_count} negative fixtures."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

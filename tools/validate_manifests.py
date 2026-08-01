"""Validate the PaperWorkShell case-manifest v1 contract and fixtures."""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final, NoReturn, cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
MANIFEST_SCHEMA_PATH: Final = Path("schemas/case-manifest-v1.schema.json")
MANIFEST_EXAMPLE_DIRECTORY: Final = Path("schemas/examples")
MANIFEST_INVALID_FIXTURE_DIRECTORY: Final = Path("tests/fixtures/manifests/v1/invalid")
MANIFEST_EXAMPLE_PATTERN: Final = "case-manifest-v1-*.json"
SUPPORTED_SCHEMA_VERSION: Final = 1

MAX_MANIFEST_BYTES: Final = 8 * 1024 * 1024
MAX_SCHEMA_BYTES: Final = 2 * 1024 * 1024
MAX_JSON_DEPTH: Final = 64
MAX_JSON_NODES: Final = 100_000
MAX_JSON_STRING_CODEPOINTS: Final = 1_048_576
MAX_FIXTURE_FILES: Final = 256
MAX_REPORTED_ISSUES: Final = 512
MAX_OUTPUT_NAME_UTF16_CODE_UNITS: Final = 180
NORMALIZED_PAGE_MILLIONTHS: Final = 1_000_000

_SAFE_PATH_TOKEN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INVALID_JSON_POINTER_ESCAPE: Final = re.compile(r"~(?:[^01]|$)")
_REFERENCE_KEYWORDS: Final = frozenset({"$ref", "$dynamicRef", "$recursiveRef"})
_UNSUPPORTED_SCHEMA_REFERENCE_KEYWORDS: Final = frozenset(
    {"$anchor", "$dynamicAnchor", "$dynamicRef", "$recursiveAnchor", "$recursiveRef"}
)
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
    "exclusiveMaximum": "number is outside the allowed range",
    "exclusiveMinimum": "number is outside the allowed range",
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
class ManifestIssue:
    """One stable, non-disclosing manifest validation error."""

    code: str
    document: str
    path: str
    message: str

    def sort_key(self) -> tuple[str, str, str, str]:
        """Return the deterministic repository diagnostic order."""

        return (self.document, self.path, self.code, self.message)

    def render(self) -> str:
        """Render without including manifest values or absolute paths."""

        return f"ERROR [{self.code}] {self.document}#{self.path}: {self.message}"


@dataclass(frozen=True)
class ManifestFixtureSummary:
    """Counts for a successfully validated manifest fixture set."""

    positive_count: int
    negative_count: int


class DuplicateJsonKeyError(ValueError):
    """Raised when a JSON object repeats a member name."""


class NonStandardJsonConstantError(ValueError):
    """Raised for NaN and Infinity, which are not standard JSON."""


def _issue(
    code: str,
    document: str,
    path: str,
    message: str,
) -> ManifestIssue:
    return ManifestIssue(code=code, document=document, path=path, message=message)


def _add_issue(issues: list[ManifestIssue], issue: ManifestIssue) -> None:
    if len(issues) < MAX_REPORTED_ISSUES - 1:
        issues.append(issue)
        return
    if not any(existing.code == "MANIFEST_ISSUE_LIMIT" for existing in issues):
        issues.append(
            _issue(
                "MANIFEST_ISSUE_LIMIT",
                issue.document,
                "$",
                "additional validation errors were suppressed",
            )
        )


def _sorted_issues(issues: Iterable[ManifestIssue]) -> list[ManifestIssue]:
    return sorted(issues, key=ManifestIssue.sort_key)


def _json_path(parts: Iterable[object]) -> str:
    rendered = "$"
    for part in parts:
        if isinstance(part, int):
            rendered += f"[{part}]"
        elif isinstance(part, str) and _SAFE_PATH_TOKEN.fullmatch(part):
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
        return "external-manifest.json"


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> NoReturn:
    raise NonStandardJsonConstantError


def _read_bounded_utf8(
    path: Path,
    *,
    document: str,
    maximum_bytes: int,
) -> tuple[str | None, list[ManifestIssue]]:
    try:
        with path.open("rb") as source:
            content = source.read(maximum_bytes + 1)
    except OSError as error:
        return None, [
            _issue(
                "MANIFEST_INPUT_UNREADABLE",
                document,
                "$",
                type(error).__name__,
            )
        ]
    if len(content) > maximum_bytes:
        return None, [
            _issue(
                "MANIFEST_INPUT_TOO_LARGE",
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
                "MANIFEST_TEXT_ENCODING_INVALID",
                document,
                "$",
                "input must be valid UTF-8",
            )
        ]


def _json_resource_issue(
    loaded: Mapping[str, Any],
    document: str,
) -> ManifestIssue | None:
    stack: list[tuple[object, int]] = [(loaded, 1)]
    node_count = 0
    while stack:
        value, depth = stack.pop()
        node_count += 1
        if node_count > MAX_JSON_NODES:
            return _issue(
                "MANIFEST_JSON_RESOURCE_LIMIT",
                document,
                "$",
                "JSON input exceeds its structural node limit",
            )
        if depth > MAX_JSON_DEPTH:
            return _issue(
                "MANIFEST_JSON_RESOURCE_LIMIT",
                document,
                "$",
                "JSON input exceeds its nesting limit",
            )
        if isinstance(value, Mapping):
            for key, child in value.items():
                if len(key) > MAX_JSON_STRING_CODEPOINTS:
                    return _issue(
                        "MANIFEST_JSON_RESOURCE_LIMIT",
                        document,
                        "$",
                        "JSON text exceeds its string limit",
                    )
                stack.append((child, depth + 1))
        elif isinstance(value, list):
            stack.extend((child, depth + 1) for child in value)
        elif isinstance(value, str) and len(value) > MAX_JSON_STRING_CODEPOINTS:
            return _issue(
                "MANIFEST_JSON_RESOURCE_LIMIT",
                document,
                "$",
                "JSON text exceeds its string limit",
            )
    return None


def _load_json_object(
    path: Path,
    *,
    root: Path,
    maximum_bytes: int,
) -> tuple[dict[str, Any] | None, list[ManifestIssue]]:
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
            parse_float=Decimal,
        )
    except DuplicateJsonKeyError:
        return None, [
            _issue(
                "MANIFEST_JSON_DUPLICATE_KEY",
                document,
                "$",
                "object member names must be unique",
            )
        ]
    except NonStandardJsonConstantError:
        return None, [
            _issue(
                "MANIFEST_JSON_NONSTANDARD_CONSTANT",
                document,
                "$",
                "NaN and Infinity are not permitted",
            )
        ]
    except json.JSONDecodeError as error:
        return None, [
            _issue(
                "MANIFEST_JSON_INVALID",
                document,
                "$",
                f"invalid JSON at line {error.lineno}, column {error.colno}",
            )
        ]
    except InvalidOperation, RecursionError, ValueError:
        return None, [
            _issue(
                "MANIFEST_JSON_INVALID",
                document,
                "$",
                "JSON numeric or nesting syntax is invalid",
            )
        ]
    if not isinstance(loaded, dict):
        return None, [
            _issue(
                "MANIFEST_JSON_ROOT_TYPE",
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


def _schema_policy_issues(schema: Mapping[str, Any], document: str) -> list[ManifestIssue]:
    issues: list[ManifestIssue] = []
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
                            "MANIFEST_SCHEMA_RESOURCE_SCOPE_UNSAFE",
                            document,
                            _json_path(child_path),
                            "nested schema resource and dialect declarations are not permitted",
                        ),
                    )
                if key in _UNSUPPORTED_SCHEMA_REFERENCE_KEYWORDS:
                    _add_issue(
                        issues,
                        _issue(
                            "MANIFEST_SCHEMA_REFERENCE_FEATURE_UNSAFE",
                            document,
                            _json_path(child_path),
                            "dynamic, recursive, and anchor reference features are not permitted",
                        ),
                    )
                stack.append((child, child_path))
        elif isinstance(value, list):
            stack.extend((child, (*path, index)) for index, child in enumerate(value))
    return _sorted_issues(issues)


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
                    target_path = _local_reference_target_path(schema, child)
                    if target_path is not None:
                        edges.append(target_path)
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
    for edges in graph.values():
        for target in edges:
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


def load_manifest_schema(
    root: Path = PROJECT_ROOT,
) -> tuple[dict[str, Any] | None, list[ManifestIssue]]:
    """Strictly load and check the self-contained Draft 2020-12 schema."""

    schema, issues = _load_json_object(
        root / MANIFEST_SCHEMA_PATH,
        root=root,
        maximum_bytes=MAX_SCHEMA_BYTES,
    )
    if schema is None:
        return None, issues
    document = MANIFEST_SCHEMA_PATH.as_posix()
    policy_issues = _schema_policy_issues(schema, document)
    if policy_issues:
        return None, policy_issues
    reference_issues: list[ManifestIssue] = []
    for path, reference in _schema_references(schema):
        if not reference.startswith("#"):
            _add_issue(
                reference_issues,
                _issue(
                    "MANIFEST_SCHEMA_REFERENCE_UNSAFE",
                    document,
                    path,
                    "schema references must remain inside this schema",
                ),
            )
        elif _local_reference_target_path(schema, reference) is None:
            _add_issue(
                reference_issues,
                _issue(
                    "MANIFEST_SCHEMA_REFERENCE_INVALID",
                    document,
                    path,
                    "local schema reference does not resolve",
                ),
            )
    if reference_issues:
        return None, _sorted_issues(reference_issues)
    if _schema_has_reference_cycle(schema):
        return None, [
            _issue(
                "MANIFEST_SCHEMA_REFERENCE_CYCLE",
                document,
                "$",
                "recursive local schema references are not permitted",
            )
        ]
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        return None, [
            _issue(
                "MANIFEST_SCHEMA_DEFINITION_INVALID",
                document,
                _json_path(error.absolute_schema_path),
                "schema definition is invalid",
            )
        ]
    return schema, []


def _schema_validation_issues(
    data: Mapping[str, Any],
    schema: Mapping[str, Any],
    document: str,
) -> list[ManifestIssue]:
    issues: list[ManifestIssue] = []
    try:
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        for error in validator.iter_errors(data):
            message = _SCHEMA_MESSAGES.get(error.validator, "value violates the manifest schema")
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_SCHEMA_VALIDATION",
                    document,
                    _json_path(error.absolute_path),
                    message,
                ),
            )
            if any(issue.code == "MANIFEST_ISSUE_LIMIT" for issue in issues):
                break
    except Exception:
        return [
            _issue(
                "MANIFEST_SCHEMA_EXECUTION_INVALID",
                document,
                "$",
                "schema validation could not complete safely",
            )
        ]
    return _sorted_issues(issues)


def _index_records(
    records: Sequence[Mapping[str, Any]],
    *,
    id_field: str,
    collection_path: str,
    duplicate_code: str,
    document: str,
    issues: list[ManifestIssue],
) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for index, record in enumerate(records):
        record_id = cast(str, record[id_field])
        if record_id in indexed:
            _add_issue(
                issues,
                _issue(
                    duplicate_code,
                    document,
                    f"{collection_path}[{index}].{id_field}",
                    "identifier is already used in this collection",
                ),
            )
        else:
            indexed[record_id] = record
    return indexed


def _register_namespace(
    namespace: dict[str, str],
    records: Mapping[str, Mapping[str, Any]],
    *,
    kind: str,
    id_field: str,
    collection_path: str,
    document: str,
    issues: list[ManifestIssue],
) -> None:
    for record_id in records:
        prior = namespace.get(record_id)
        if prior is not None:
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_ID_NAMESPACE_COLLISION",
                    document,
                    f"{collection_path}.{id_field}",
                    "identifier collides with another manifest namespace",
                ),
            )
        else:
            namespace[record_id] = kind


def _timestamp(
    value: str,
    *,
    document: str,
    path: str,
    issues: list[ManifestIssue],
) -> datetime | None:
    try:
        return datetime.fromisoformat(f"{value[:-1]}+00:00")
    except OverflowError, ValueError:
        _add_issue(
            issues,
            _issue(
                "MANIFEST_TIMESTAMP_INVALID",
                document,
                path,
                "timestamp is not supported by the canonical UTC parser",
            ),
        )
        return None


def _check_time_within_case(
    value: str,
    *,
    created_at: datetime | None,
    updated_at: datetime | None,
    document: str,
    path: str,
    issues: list[ManifestIssue],
) -> datetime | None:
    parsed = _timestamp(value, document=document, path=path, issues=issues)
    if (
        parsed is not None
        and created_at is not None
        and updated_at is not None
        and (parsed < created_at or parsed > updated_at)
    ):
        _add_issue(
            issues,
            _issue(
                "MANIFEST_TIMESTAMP_ORDER_INVALID",
                document,
                path,
                "timestamp is outside the case revision interval",
            ),
        )
    return parsed


def _check_box(
    box: Mapping[str, Any],
    *,
    document: str,
    path: str,
    issues: list[ManifestIssue],
) -> None:
    x = cast(int, box["x"])
    y = cast(int, box["y"])
    width = cast(int, box["width"])
    height = cast(int, box["height"])
    if x + width > NORMALIZED_PAGE_MILLIONTHS or y + height > NORMALIZED_PAGE_MILLIONTHS:
        _add_issue(
            issues,
            _issue(
                "MANIFEST_NORMALIZED_BOX_INVALID",
                document,
                path,
                "normalized box extends beyond the page",
            ),
        )


def _check_known_page_list(
    page_ids: Sequence[str],
    known_pages: Mapping[str, Mapping[str, Any]],
    *,
    document: str,
    path: str,
    issues: list[ManifestIssue],
) -> None:
    for index, page_id in enumerate(page_ids):
        if page_id not in known_pages:
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_PAGE_REFERENCE_UNKNOWN",
                    document,
                    f"{path}[{index}]",
                    "page reference does not resolve",
                ),
            )


def _normalized_output_name(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def _contains_unsafe_unicode_codepoint(value: str) -> bool:
    return any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value)


def _utf16_code_units(value: str) -> int | None:
    try:
        return len(value.encode("utf-16-le")) // 2
    except UnicodeEncodeError:
        return None


def _check_output_names(
    constraints: Mapping[str, Any],
    *,
    document: str,
    issues: list[ManifestIssue],
) -> None:
    names = cast(list[str], constraints.get("requested_output_names", []))
    normalized_names: set[str] = set()
    for index, name in enumerate(names):
        path = f"$.constraints.requested_output_names[{index}]"
        normalized = _normalized_output_name(name)
        if _contains_unsafe_unicode_codepoint(name):
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_OUTPUT_NAME_UNSAFE_UNICODE",
                    document,
                    path,
                    "output name contains unsafe Unicode controls or surrogate code points",
                ),
            )
        utf16_units = _utf16_code_units(name)
        if utf16_units is not None and utf16_units > MAX_OUTPUT_NAME_UTF16_CODE_UNITS:
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_OUTPUT_NAME_TOO_LONG",
                    document,
                    path,
                    "output name exceeds the Windows-safe UTF-16 component limit",
                ),
            )
        if name != unicodedata.normalize("NFC", name):
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_OUTPUT_NAME_NONCANONICAL",
                    document,
                    path,
                    "output name must use canonical Unicode normalization",
                ),
            )
        basename = normalized.split(".", maxsplit=1)[0].rstrip(" .")
        if basename in _WINDOWS_RESERVED_BASENAMES:
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_OUTPUT_NAME_RESERVED",
                    document,
                    path,
                    "output name uses a reserved Windows basename",
                ),
            )
        if normalized in normalized_names:
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_OUTPUT_NAME_COLLISION",
                    document,
                    path,
                    "output names collide under Windows comparison rules",
                ),
            )
        else:
            normalized_names.add(normalized)
    maximum_file_count = cast(int | None, constraints.get("maximum_file_count"))
    if maximum_file_count is not None and len(names) > maximum_file_count:
        _add_issue(
            issues,
            _issue(
                "MANIFEST_OUTPUT_NAME_COUNT_INVALID",
                document,
                "$.constraints.requested_output_names",
                "requested output names exceed the configured file-count limit",
            ),
        )


def _sequence_reason_matches(
    reason: str,
    before: Sequence[str],
    after: Sequence[str],
    introduced_page_ids: set[str],
) -> bool:
    before_set = set(before)
    after_set = set(after)
    added_page_ids = after_set - before_set
    if reason == "remove":
        return after_set < before_set and list(after) == [
            page_id for page_id in before if page_id in after_set
        ]
    if reason in {"import", "restore"}:
        preserves_prior_order = before_set < after_set and list(before) == [
            page_id for page_id in after if page_id in before_set
        ]
        if reason == "import":
            return preserves_prior_order and added_page_ids.isdisjoint(introduced_page_ids)
        return preserves_prior_order and added_page_ids <= introduced_page_ids
    return before_set == after_set


def _semantic_issues(data: Mapping[str, Any], document: str) -> list[ManifestIssue]:
    issues: list[ManifestIssue] = []
    sources = cast(list[Mapping[str, Any]], data["sources"])
    pages = cast(list[Mapping[str, Any]], data["pages"])
    operations = cast(list[Mapping[str, Any]], data["page_operations"])
    assets = cast(list[Mapping[str, Any]], data["assets"])
    overlays = cast(list[Mapping[str, Any]], data["overlays"])
    assignments = cast(list[Mapping[str, Any]], data["assignments"])
    confirmations = cast(list[Mapping[str, Any]], data["confirmations"])
    export_history = cast(Mapping[str, Any], data["export_history"])
    exports = cast(list[Mapping[str, Any]], export_history["entries"])

    source_index = _index_records(
        sources,
        id_field="source_id",
        collection_path="$.sources",
        duplicate_code="MANIFEST_SOURCE_ID_DUPLICATE",
        document=document,
        issues=issues,
    )
    page_index = _index_records(
        pages,
        id_field="page_id",
        collection_path="$.pages",
        duplicate_code="MANIFEST_PAGE_ID_DUPLICATE",
        document=document,
        issues=issues,
    )
    operation_index = _index_records(
        operations,
        id_field="operation_id",
        collection_path="$.page_operations",
        duplicate_code="MANIFEST_OPERATION_ID_DUPLICATE",
        document=document,
        issues=issues,
    )
    asset_index = _index_records(
        assets,
        id_field="asset_id",
        collection_path="$.assets",
        duplicate_code="MANIFEST_ASSET_ID_DUPLICATE",
        document=document,
        issues=issues,
    )
    overlay_index = _index_records(
        overlays,
        id_field="overlay_id",
        collection_path="$.overlays",
        duplicate_code="MANIFEST_OVERLAY_ID_DUPLICATE",
        document=document,
        issues=issues,
    )
    assignment_index = _index_records(
        assignments,
        id_field="assignment_id",
        collection_path="$.assignments",
        duplicate_code="MANIFEST_ASSIGNMENT_ID_DUPLICATE",
        document=document,
        issues=issues,
    )
    confirmation_index = _index_records(
        confirmations,
        id_field="confirmation_id",
        collection_path="$.confirmations",
        duplicate_code="MANIFEST_CONFIRMATION_ID_DUPLICATE",
        document=document,
        issues=issues,
    )
    export_index = _index_records(
        exports,
        id_field="export_id",
        collection_path="$.export_history.entries",
        duplicate_code="MANIFEST_EXPORT_ID_DUPLICATE",
        document=document,
        issues=issues,
    )

    namespace: dict[str, str] = {cast(str, data["case_id"]): "case"}
    for records, kind, field, path in (
        (source_index, "source", "source_id", "$.sources"),
        (page_index, "page", "page_id", "$.pages"),
        (operation_index, "operation", "operation_id", "$.page_operations"),
        (asset_index, "asset", "asset_id", "$.assets"),
        (overlay_index, "overlay", "overlay_id", "$.overlays"),
        (assignment_index, "assignment", "assignment_id", "$.assignments"),
        (confirmation_index, "confirmation", "confirmation_id", "$.confirmations"),
        (export_index, "export", "export_id", "$.export_history.entries"),
    ):
        _register_namespace(
            namespace,
            records,
            kind=kind,
            id_field=field,
            collection_path=path,
            document=document,
            issues=issues,
        )

    created_at = _timestamp(
        cast(str, data["created_at"]),
        document=document,
        path="$.created_at",
        issues=issues,
    )
    updated_at = _timestamp(
        cast(str, data["updated_at"]),
        document=document,
        path="$.updated_at",
        issues=issues,
    )
    if created_at is not None and updated_at is not None and updated_at < created_at:
        _add_issue(
            issues,
            _issue(
                "MANIFEST_TIMESTAMP_ORDER_INVALID",
                document,
                "$.updated_at",
                "updated timestamp precedes creation",
            ),
        )

    storage_names: set[str] = set()
    for index, source in enumerate(sources):
        source_id = cast(str, source["source_id"])
        storage_name = cast(str, source["storage_name"])
        display_name = cast(str, source["display_name"])
        if _contains_unsafe_unicode_codepoint(display_name):
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_DISPLAY_NAME_UNSAFE_UNICODE",
                    document,
                    f"$.sources[{index}].display_name",
                    "display name contains unsafe Unicode controls or surrogate code points",
                ),
            )
        if display_name != unicodedata.normalize("NFC", display_name):
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_DISPLAY_NAME_NONCANONICAL",
                    document,
                    f"$.sources[{index}].display_name",
                    "display name must use canonical Unicode normalization",
                ),
            )
        if storage_name != f"{source_id}.bin":
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_STORAGE_NAME_ID_MISMATCH",
                    document,
                    f"$.sources[{index}].storage_name",
                    "storage name must be derived from its internal identifier",
                ),
            )
        normalized_storage = storage_name.casefold()
        if normalized_storage in storage_names:
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_STORAGE_NAME_COLLISION",
                    document,
                    f"$.sources[{index}].storage_name",
                    "storage name collides with another case object",
                ),
            )
        storage_names.add(normalized_storage)
        media_type = cast(str, source["detected_media_type"])
        signature_state = cast(str, source["signature_state"])
        encryption_state = cast(str, source["encryption_state"])
        if media_type == "application/pdf":
            state_invalid = signature_state in {"not_checked", "not_applicable"} or (
                encryption_state in {"not_checked", "not_applicable"}
            )
        else:
            state_invalid = (
                signature_state != "not_applicable" or encryption_state != "not_applicable"
            )
        if state_invalid:
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_MEDIA_INSPECTION_STATE_INVALID",
                    document,
                    f"$.sources[{index}]",
                    "inspection states do not match the detected media type",
                ),
            )
        _check_time_within_case(
            cast(str, source["imported_at"]),
            created_at=created_at,
            updated_at=updated_at,
            document=document,
            path=f"$.sources[{index}].imported_at",
            issues=issues,
        )

    for index, asset in enumerate(assets):
        asset_id = cast(str, asset["asset_id"])
        storage_name = cast(str, asset["storage_name"])
        if storage_name != f"{asset_id}.bin":
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_STORAGE_NAME_ID_MISMATCH",
                    document,
                    f"$.assets[{index}].storage_name",
                    "storage name must be derived from its internal identifier",
                ),
            )
        normalized_storage = storage_name.casefold()
        if normalized_storage in storage_names:
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_STORAGE_NAME_COLLISION",
                    document,
                    f"$.assets[{index}].storage_name",
                    "storage name collides with another case object",
                ),
            )
        storage_names.add(normalized_storage)

    source_page_indices: dict[str, set[int]] = {source_id: set() for source_id in source_index}
    for index, page in enumerate(pages):
        source_id = cast(str, page["source_id"])
        source_page_index = cast(int, page["source_page_index"])
        resolved_source = source_index.get(source_id)
        if resolved_source is None:
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_SOURCE_REFERENCE_UNKNOWN",
                    document,
                    f"$.pages[{index}].source_id",
                    "source reference does not resolve",
                ),
            )
            continue
        page_count = cast(int, resolved_source["page_count"])
        if source_page_index >= page_count:
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_SOURCE_PAGE_OUT_OF_RANGE",
                    document,
                    f"$.pages[{index}].source_page_index",
                    "source page index is outside the source page count",
                ),
            )
            continue
        seen_indices = source_page_indices[source_id]
        if source_page_index in seen_indices:
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_SOURCE_PAGE_DUPLICATE",
                    document,
                    f"$.pages[{index}].source_page_index",
                    "source page is represented more than once",
                ),
            )
        seen_indices.add(source_page_index)
    for source_id, source in source_index.items():
        if len(source_page_indices[source_id]) != cast(int, source["page_count"]):
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_SOURCE_PAGE_MISSING",
                    document,
                    "$.pages",
                    "source page catalogue is incomplete",
                ),
            )

    base_sequence = cast(list[str], data["page_sequence_base"])
    final_sequence = cast(list[str], data["page_sequence"])
    _check_known_page_list(
        base_sequence,
        page_index,
        document=document,
        path="$.page_sequence_base",
        issues=issues,
    )
    _check_known_page_list(
        final_sequence,
        page_index,
        document=document,
        path="$.page_sequence",
        issues=issues,
    )
    replay_sequence = list(base_sequence)
    introduced_page_ids = set(base_sequence)
    group_ids: set[str] = set()
    for index, operation in enumerate(operations):
        if cast(int, operation["sequence"]) != index + 1:
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_OPERATION_SEQUENCE_INVALID",
                    document,
                    f"$.page_operations[{index}].sequence",
                    "operation sequence must be contiguous and match array order",
                ),
            )
        payload = cast(Mapping[str, Any], operation["payload"])
        kind = cast(str, payload["kind"])
        if kind in {"rotate", "crop"}:
            if cast(str, payload["page_id"]) not in page_index:
                _add_issue(
                    issues,
                    _issue(
                        "MANIFEST_PAGE_REFERENCE_UNKNOWN",
                        document,
                        f"$.page_operations[{index}].payload.page_id",
                        "page reference does not resolve",
                    ),
                )
            if kind == "crop":
                _check_box(
                    cast(Mapping[str, Any], payload["box"]),
                    document=document,
                    path=f"$.page_operations[{index}].payload.box",
                    issues=issues,
                )
        elif kind == "change_sequence":
            before = cast(list[str], payload["before_page_ids"])
            after = cast(list[str], payload["after_page_ids"])
            reason = cast(str, payload["reason"])
            _check_known_page_list(
                before,
                page_index,
                document=document,
                path=f"$.page_operations[{index}].payload.before_page_ids",
                issues=issues,
            )
            _check_known_page_list(
                after,
                page_index,
                document=document,
                path=f"$.page_operations[{index}].payload.after_page_ids",
                issues=issues,
            )
            if before != replay_sequence:
                _add_issue(
                    issues,
                    _issue(
                        "MANIFEST_SEQUENCE_REPLAY_MISMATCH",
                        document,
                        f"$.page_operations[{index}].payload.before_page_ids",
                        "operation baseline does not match replayed sequence",
                    ),
                )
            if not _sequence_reason_matches(
                reason,
                before,
                after,
                introduced_page_ids,
            ):
                _add_issue(
                    issues,
                    _issue(
                        "MANIFEST_SEQUENCE_REASON_INVALID",
                        document,
                        f"$.page_operations[{index}].payload.reason",
                        "sequence transition does not match its declared reason",
                    ),
                )
            if reason == "import":
                introduced_page_ids.update(set(after) - set(before))
            replay_sequence = list(after)
        elif kind == "set_group":
            _check_known_page_list(
                cast(list[str], payload["page_ids"]),
                page_index,
                document=document,
                path=f"$.page_operations[{index}].payload.page_ids",
                issues=issues,
            )
            group_id = cast(str, payload["group_id"])
            if group_id not in group_ids:
                group_ids.add(group_id)
                if group_id in namespace:
                    _add_issue(
                        issues,
                        _issue(
                            "MANIFEST_ID_NAMESPACE_COLLISION",
                            document,
                            f"$.page_operations[{index}].payload.group_id",
                            "identifier collides with another manifest namespace",
                        ),
                    )
                else:
                    namespace[group_id] = "group"
    if replay_sequence != final_sequence:
        _add_issue(
            issues,
            _issue(
                "MANIFEST_SEQUENCE_REPLAY_MISMATCH",
                document,
                "$.page_sequence",
                "final page sequence does not match replayed operations",
            ),
        )
    if introduced_page_ids != set(page_index):
        _add_issue(
            issues,
            _issue(
                "MANIFEST_PAGE_INTRODUCTION_INCOMPLETE",
                document,
                "$.page_sequence_base",
                "every page must appear in the replay baseline or one import operation",
            ),
        )

    z_indices: set[tuple[str, int]] = set()
    for index, overlay in enumerate(overlays):
        page_id = cast(str, overlay["page_id"])
        if page_id not in page_index:
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_PAGE_REFERENCE_UNKNOWN",
                    document,
                    f"$.overlays[{index}].page_id",
                    "page reference does not resolve",
                ),
            )
        _check_box(
            cast(Mapping[str, Any], overlay["box"]),
            document=document,
            path=f"$.overlays[{index}].box",
            issues=issues,
        )
        z_key = (page_id, cast(int, overlay["z_index"]))
        if z_key in z_indices:
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_OVERLAY_Z_INDEX_COLLISION",
                    document,
                    f"$.overlays[{index}].z_index",
                    "overlay ordering collides on the same page",
                ),
            )
        z_indices.add(z_key)
        payload = cast(Mapping[str, Any], overlay["payload"])
        if payload["kind"] == "signature_image":
            asset_id = cast(str, payload["asset_id"])
            resolved_asset = asset_index.get(asset_id)
            if resolved_asset is None:
                _add_issue(
                    issues,
                    _issue(
                        "MANIFEST_ASSET_REFERENCE_UNKNOWN",
                        document,
                        f"$.overlays[{index}].payload.asset_id",
                        "asset reference does not resolve",
                    ),
                )
            elif resolved_asset["kind"] != "signature_image":
                _add_issue(
                    issues,
                    _issue(
                        "MANIFEST_ASSET_REFERENCE_INCOMPATIBLE",
                        document,
                        f"$.overlays[{index}].payload.asset_id",
                        "asset type is incompatible with this overlay",
                    ),
                )

    assignment_times: dict[str, datetime | None] = {}
    for index, assignment in enumerate(assignments):
        _check_known_page_list(
            cast(list[str], assignment["page_ids"]),
            page_index,
            document=document,
            path=f"$.assignments[{index}].page_ids",
            issues=issues,
        )
        assignment_id = cast(str, assignment["assignment_id"])
        assignment_times[assignment_id] = _check_time_within_case(
            cast(str, assignment["assigned_at"]),
            created_at=created_at,
            updated_at=updated_at,
            document=document,
            path=f"$.assignments[{index}].assigned_at",
            issues=issues,
        )

    confirmation_times: dict[str, datetime | None] = {}
    for index, confirmation in enumerate(confirmations):
        confirmation_id = cast(str, confirmation["confirmation_id"])
        confirmation_times[confirmation_id] = _check_time_within_case(
            cast(str, confirmation["recorded_at"]),
            created_at=created_at,
            updated_at=updated_at,
            document=document,
            path=f"$.confirmations[{index}].recorded_at",
            issues=issues,
        )
        scope = cast(Mapping[str, Any], confirmation["scope"])
        scope_kind = cast(str, scope["kind"])
        target_exists = True
        if scope_kind == "source":
            target_exists = cast(str, scope["source_id"]) in source_index
        elif scope_kind == "page":
            target_exists = cast(str, scope["page_id"]) in page_index
        elif scope_kind == "assignment":
            target_exists = cast(str, scope["assignment_id"]) in assignment_index
        elif scope_kind == "export":
            target_exists = cast(str, scope["export_id"]) in export_index
        if not target_exists:
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_CONFIRMATION_SCOPE_UNKNOWN",
                    document,
                    f"$.confirmations[{index}].scope",
                    "confirmation scope does not resolve",
                ),
            )

    for index, assignment in enumerate(assignments):
        if assignment["status"] != "user_confirmed":
            continue
        confirmation_id = cast(str, assignment["confirmation_id"])
        resolved_confirmation = confirmation_index.get(confirmation_id)
        if resolved_confirmation is None:
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_ASSIGNMENT_CONFIRMATION_UNKNOWN",
                    document,
                    f"$.assignments[{index}].confirmation_id",
                    "assignment confirmation does not resolve",
                ),
            )
            continue
        scope = cast(Mapping[str, Any], resolved_confirmation["scope"])
        coupled = (
            resolved_confirmation["state"] == "confirmed"
            and resolved_confirmation["confirmation_code"] == "assignment-reviewed"
            and scope["kind"] == "assignment"
            and scope.get("assignment_id") == assignment["assignment_id"]
        )
        if not coupled:
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_ASSIGNMENT_CONFIRMATION_MISMATCH",
                    document,
                    f"$.assignments[{index}].confirmation_id",
                    "confirmation does not affirm this assignment",
                ),
            )
        else:
            confirmation_time = confirmation_times[confirmation_id]
            assignment_time = assignment_times[cast(str, assignment["assignment_id"])]
            if (
                confirmation_time is not None
                and assignment_time is not None
                and confirmation_time < assignment_time
            ):
                _add_issue(
                    issues,
                    _issue(
                        "MANIFEST_TIMESTAMP_ORDER_INVALID",
                        document,
                        f"$.assignments[{index}].confirmation_id",
                        "assignment confirmation predates the assignment",
                    ),
                )

    _check_output_names(
        cast(Mapping[str, Any], data["constraints"]),
        document=document,
        issues=issues,
    )
    return _sorted_issues(issues)


def validate_manifest_data(
    data: Mapping[str, Any],
    schema: Mapping[str, Any],
    *,
    document: str = "manifest.json",
) -> list[ManifestIssue]:
    """Validate one already decoded manifest without mutating it."""

    version = data.get("schema_version")
    if type(version) is not int:
        return [
            _issue(
                "MANIFEST_VERSION_INVALID",
                document,
                "$.schema_version",
                "schema version must be an integer",
            )
        ]
    if version != SUPPORTED_SCHEMA_VERSION:
        return [
            _issue(
                "MANIFEST_VERSION_UNSUPPORTED",
                document,
                "$.schema_version",
                "schema version is not supported",
            )
        ]
    schema_issues = _schema_validation_issues(data, schema, document)
    if schema_issues:
        return schema_issues
    return _semantic_issues(data, document)


def validate_manifest_document(
    path: Path,
    root: Path = PROJECT_ROOT,
    *,
    schema: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[ManifestIssue]]:
    """Strictly load and validate one manifest document."""

    active_schema = schema
    if active_schema is None:
        active_schema, schema_issues = load_manifest_schema(root)
        if active_schema is None:
            return None, schema_issues
    data, load_issues = _load_json_object(
        path,
        root=root,
        maximum_bytes=MAX_MANIFEST_BYTES,
    )
    if data is None:
        return None, load_issues
    issues = validate_manifest_data(
        data,
        active_schema,
        document=_document_label(path, root),
    )
    return (None, issues) if issues else (data, [])


def _fixture_paths(
    directory: Path,
    *,
    pattern: str,
    document: str,
) -> tuple[list[Path] | None, list[ManifestIssue]]:
    try:
        candidates: list[Path] = []
        for candidate in directory.iterdir():
            if candidate.is_file() and candidate.match(pattern):
                candidates.append(candidate)
                if len(candidates) > MAX_FIXTURE_FILES:
                    return None, [
                        _issue(
                            "MANIFEST_FIXTURE_LIMIT",
                            document,
                            "$",
                            "fixture set exceeds its file-count limit",
                        )
                    ]
    except OSError as error:
        return None, [
            _issue(
                "MANIFEST_FIXTURE_SET_UNREADABLE",
                document,
                "$",
                type(error).__name__,
            )
        ]
    return sorted(candidates, key=lambda path: path.name), []


def validate_manifest_fixture_set(
    root: Path = PROJECT_ROOT,
) -> tuple[ManifestFixtureSummary | None, list[ManifestIssue]]:
    """Validate canonical examples and prove every negative fixture is rejected."""

    schema, schema_issues = load_manifest_schema(root)
    if schema is None:
        return None, schema_issues
    positive_paths, positive_path_issues = _fixture_paths(
        root / MANIFEST_EXAMPLE_DIRECTORY,
        pattern=MANIFEST_EXAMPLE_PATTERN,
        document=MANIFEST_EXAMPLE_DIRECTORY.as_posix(),
    )
    invalid_paths, invalid_path_issues = _fixture_paths(
        root / MANIFEST_INVALID_FIXTURE_DIRECTORY,
        pattern="*.json",
        document=MANIFEST_INVALID_FIXTURE_DIRECTORY.as_posix(),
    )
    path_issues = [*positive_path_issues, *invalid_path_issues]
    if path_issues or positive_paths is None or invalid_paths is None:
        return None, _sorted_issues(path_issues)
    fixture_issues: list[ManifestIssue] = []
    if not positive_paths:
        _add_issue(
            fixture_issues,
            _issue(
                "MANIFEST_POSITIVE_FIXTURES_MISSING",
                MANIFEST_EXAMPLE_DIRECTORY.as_posix(),
                "$",
                "at least one positive fixture is required",
            ),
        )
    if not invalid_paths:
        _add_issue(
            fixture_issues,
            _issue(
                "MANIFEST_NEGATIVE_FIXTURES_MISSING",
                MANIFEST_INVALID_FIXTURE_DIRECTORY.as_posix(),
                "$",
                "at least one negative fixture is required",
            ),
        )
    for path in positive_paths:
        _, issues = validate_manifest_document(path, root, schema=schema)
        for issue in issues:
            _add_issue(
                fixture_issues,
                _issue(
                    "MANIFEST_POSITIVE_FIXTURE_INVALID",
                    issue.document,
                    issue.path,
                    f"positive fixture failed with {issue.code}",
                ),
            )
    for path in invalid_paths:
        _, issues = validate_manifest_document(path, root, schema=schema)
        if not issues:
            _add_issue(
                fixture_issues,
                _issue(
                    "MANIFEST_NEGATIVE_FIXTURE_ACCEPTED",
                    _document_label(path, root),
                    "$",
                    "negative fixture was accepted",
                ),
            )
    if fixture_issues:
        return None, _sorted_issues(fixture_issues)
    return ManifestFixtureSummary(len(positive_paths), len(invalid_paths)), []


def validation_messages(root: Path = PROJECT_ROOT) -> list[str]:
    """Return rendered aggregate errors for the repository schema registry."""

    _, issues = validate_manifest_fixture_set(root)
    return [issue.render() for issue in issues]


def main(argv: Sequence[str] | None = None) -> int:
    """Run local-only manifest schema and fixture validation."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=PROJECT_ROOT,
        help="repository root (default: detected project root)",
    )
    parser.add_argument(
        "--document",
        type=Path,
        help="validate one document instead of the checked-in fixture set",
    )
    arguments = parser.parse_args(argv)
    if arguments.document is not None:
        _, issues = validate_manifest_document(arguments.document, arguments.root)
        summary = None
    else:
        summary, issues = validate_manifest_fixture_set(arguments.root)
    if issues:
        for issue in issues:
            print(issue.render(), file=sys.stderr)
        return 1
    if arguments.document is not None:
        print("OK: validated one case-manifest v1 document.")
    else:
        assert summary is not None
        print(
            "OK: validated case-manifest v1 with "
            f"{summary.positive_count} positive and {summary.negative_count} negative fixtures."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

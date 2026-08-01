"""Validate the PaperWorkShell dependency-policy baseline and rejected examples."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import sys
import tomllib
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final, NoReturn, cast
from urllib.parse import unquote, urlsplit

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
INVENTORY_PATH: Final = Path("packaging/dependency-inventory-v1.json")
INVENTORY_SCHEMA_PATH: Final = Path("schemas/dependency-inventory-v1.schema.json")
INVALID_FIXTURE_DIRECTORY: Final = Path("tests/fixtures/dependencies/v1/invalid")
SUPPORTED_SCHEMA_VERSION: Final = 1
DRAFT_2020_12_DIALECT: Final = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_IDENTIFIER: Final = "https://paperworkshell.invalid/schemas/dependency-inventory-v1.json"
CANONICAL_SCHEMA_FINGERPRINT: Final = (
    "6f3939aa1ce2e016675ee1776da8d77369ae78fe634f9a3151f6b9d382dea0be"
)

MAX_INVENTORY_BYTES: Final = 2 * 1024 * 1024
MAX_SCHEMA_BYTES: Final = 1024 * 1024
MAX_JSON_DEPTH: Final = 64
MAX_JSON_NODES: Final = 100_000
MAX_JSON_STRING_CODEPOINTS: Final = 1_048_576
MAX_JSON_COLLECTION_ITEMS: Final = 1_024
MAX_FIXTURE_FILES: Final = 256
MAX_REPORTED_ISSUES: Final = 512
MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991

RENDERER_COMPONENT_IDS: Final = frozenset({"mupdf", "pdfium", "poppler", "pypdfium2", "qt-pdf"})
HARD_GATED_COMPONENT_IDS: Final = frozenset({"ghostscript", "mupdf", "poppler"})
REQUIRED_COMPONENT_IDS: Final = frozenset(
    {
        "actions-checkout",
        "actions-setup-python",
        "astral-setup-uv",
        "cpython",
        "ghostscript",
        "git",
        "gitleaks",
        "ocrmypdf",
        "pikepdf",
        "pyside6",
        "qpdf",
        "tesseract",
        "tesseract-language-data",
        "uv",
        "uv-build",
        *RENDERER_COMPONENT_IDS,
    }
)
REQUIRED_FAMILY_DECISIONS: Final[dict[str, frozenset[str]]] = {
    "PWS-D014": frozenset({"cpython", "pyside6"}),
    "PWS-D015": frozenset({"pikepdf", "qpdf"}),
    "PWS-D016": frozenset({"ocrmypdf", "tesseract", "tesseract-language-data"}),
    "PWS-D017": RENDERER_COMPONENT_IDS,
}
EXPECTED_NEGATIVE_CODES: Final[dict[str, str]] = {
    "distribution-decision-bypass.json": "DEPENDENCY_DISTRIBUTION_DECISION_REQUIRED",
    "duplicate-key.json": "DEPENDENCY_JSON_DUPLICATE_KEY",
    "future-version.json": "DEPENDENCY_VERSION_UNSUPPORTED",
    "legacy-version.json": "DEPENDENCY_VERSION_UNSUPPORTED",
}
KNOWN_LICENSE_IDS: Final = frozenset(
    {
        "AGPL-3.0-or-later",
        "Apache-2.0",
        "Artistic-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "GPL-2.0-only",
        "GPL-3.0-only",
        "LGPL-3.0-only",
        "MIT",
        "MPL-2.0",
        "PSF-2.0",
    }
)

_SAFE_PATH_TOKEN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_REFERENCE_KEYWORDS: Final = frozenset({"$ref", "$dynamicRef", "$recursiveRef"})
_UNSUPPORTED_SCHEMA_REFERENCE_KEYWORDS: Final = frozenset(
    {"$anchor", "$dynamicAnchor", "$dynamicRef", "$recursiveAnchor", "$recursiveRef"}
)
_DECISION_HEADING: Final = re.compile(r"^## (PWS-D[0-9]{3})\b", re.MULTILINE)
_NORMALIZE_PACKAGE: Final = re.compile(r"[-_.]+")
_LICENSE_TOKEN: Final = re.compile(r"LicenseRef-[A-Za-z0-9.-]+|[A-Za-z0-9.+-]+|[()]")
_WINDOWS_ABSOLUTE_PATH: Final = re.compile(r"(?:^|[^A-Za-z])[A-Za-z]:[\\/]")
_PROHIBITED_TEXT: Final = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|file://|\\\\[^\\])",
    re.IGNORECASE,
)
_SCHEMA_MESSAGES: Final[dict[object, str]] = {
    "additionalProperties": "unknown members are not permitted",
    "allOf": "value violates a conditional policy",
    "const": "value is not supported",
    "contains": "required array member is missing",
    "else": "value violates a conditional policy",
    "enum": "value is not supported",
    "format": "value has an invalid format",
    "if": "value violates a conditional policy",
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
    "propertyNames": "object member name has an invalid format",
    "required": "a required member is missing",
    "then": "value violates a conditional policy",
    "type": "value has an invalid JSON type",
    "uniqueItems": "array members must be unique",
}


@dataclass(frozen=True)
class DependencyIssue:
    """One stable, non-disclosing dependency validation error."""

    code: str
    document: str
    path: str
    message: str

    def sort_key(self) -> tuple[str, str, str, str]:
        return (self.document, self.path, self.code, self.message)

    def render(self) -> str:
        return f"ERROR [{self.code}] {self.document} {self.path}: {self.message}"


@dataclass(frozen=True)
class DependencyFixtureSummary:
    """Counts for the validated canonical and rejected dependency examples."""

    positive_count: int
    negative_count: int


class DuplicateJsonKeyError(ValueError):
    """Raised when a JSON object repeats a member name."""


class NonStandardJsonConstantError(ValueError):
    """Raised for NaN and Infinity."""


class InvalidJsonNumberError(ValueError):
    """Raised for floating-point or unsafe integer values."""


def _issue(code: str, document: str, path: str, message: str) -> DependencyIssue:
    return DependencyIssue(code=code, document=document, path=path, message=message)


def _add_issue(issues: list[DependencyIssue], issue: DependencyIssue) -> None:
    if len(issues) < MAX_REPORTED_ISSUES - 1:
        issues.append(issue)
    elif not any(existing.code == "DEPENDENCY_ISSUE_LIMIT" for existing in issues):
        issues.append(
            _issue(
                "DEPENDENCY_ISSUE_LIMIT",
                issue.document,
                "$",
                "additional validation errors were suppressed",
            )
        )


def _sorted_issues(issues: Iterable[DependencyIssue]) -> list[DependencyIssue]:
    return sorted(issues, key=DependencyIssue.sort_key)


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
        return "external-dependency-inventory.json"


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
    parsed = int(value)
    if abs(parsed) > MAX_SAFE_INTEGER:
        raise InvalidJsonNumberError
    return parsed


def _read_bounded_utf8(
    path: Path,
    *,
    document: str,
    maximum_bytes: int,
) -> tuple[str | None, list[DependencyIssue]]:
    try:
        with path.open("rb") as stream:
            content = stream.read(maximum_bytes + 1)
    except OSError as error:
        return None, [_issue("DEPENDENCY_INPUT_UNREADABLE", document, "$", type(error).__name__)]
    if len(content) > maximum_bytes:
        return None, [
            _issue(
                "DEPENDENCY_INPUT_TOO_LARGE",
                document,
                "$",
                "input exceeds its byte limit",
            )
        ]
    try:
        return content.decode("utf-8"), []
    except UnicodeDecodeError:
        return None, [
            _issue(
                "DEPENDENCY_TEXT_ENCODING_INVALID",
                document,
                "$",
                "input must be valid UTF-8",
            )
        ]


def _json_resource_issue(loaded: Mapping[str, Any], document: str) -> DependencyIssue | None:
    queue: deque[tuple[object, int]] = deque([(loaded, 1)])
    node_count = 0
    while queue:
        value, depth = queue.popleft()
        node_count += 1
        if node_count > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            return _issue(
                "DEPENDENCY_JSON_RESOURCE_LIMIT",
                document,
                "$",
                "JSON structure exceeds its resource limits",
            )
        if isinstance(value, str):
            if len(value) > MAX_JSON_STRING_CODEPOINTS:
                return _issue(
                    "DEPENDENCY_JSON_RESOURCE_LIMIT",
                    document,
                    "$",
                    "JSON text exceeds its resource limits",
                )
        elif isinstance(value, Mapping):
            if len(value) > MAX_JSON_COLLECTION_ITEMS:
                return _issue(
                    "DEPENDENCY_JSON_RESOURCE_LIMIT",
                    document,
                    "$",
                    "JSON object exceeds its resource limits",
                )
            for key, member in value.items():
                queue.append((key, depth + 1))
                queue.append((member, depth + 1))
        elif isinstance(value, list):
            if len(value) > MAX_JSON_COLLECTION_ITEMS:
                return _issue(
                    "DEPENDENCY_JSON_RESOURCE_LIMIT",
                    document,
                    "$",
                    "JSON array exceeds its resource limits",
                )
            queue.extend((member, depth + 1) for member in value)
    return None


def _load_json_object(
    path: Path,
    *,
    root: Path,
    maximum_bytes: int,
) -> tuple[dict[str, Any] | None, list[DependencyIssue]]:
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
                "DEPENDENCY_JSON_DUPLICATE_KEY",
                document,
                "$",
                "object member names must be unique",
            )
        ]
    except NonStandardJsonConstantError:
        return None, [
            _issue(
                "DEPENDENCY_JSON_NONSTANDARD_CONSTANT",
                document,
                "$",
                "NaN and Infinity are not permitted",
            )
        ]
    except InvalidJsonNumberError:
        return None, [
            _issue(
                "DEPENDENCY_JSON_NONSTANDARD_NUMBER",
                document,
                "$",
                "floating-point and out-of-range numbers are not permitted",
            )
        ]
    except json.JSONDecodeError as error:
        return None, [
            _issue(
                "DEPENDENCY_JSON_INVALID",
                document,
                "$",
                f"invalid JSON at line {error.lineno}, column {error.colno}",
            )
        ]
    except RecursionError, ValueError:
        return None, [
            _issue(
                "DEPENDENCY_JSON_INVALID",
                document,
                "$",
                "JSON numeric or nesting syntax is invalid",
            )
        ]
    if not isinstance(loaded, dict):
        return None, [
            _issue(
                "DEPENDENCY_JSON_ROOT_TYPE",
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


def _walk_mappings(
    value: object, path: tuple[object, ...] = ()
) -> Iterable[tuple[tuple[object, ...], Mapping[str, Any]]]:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[str, Any], value)
        yield path, mapping
        for key, member in mapping.items():
            yield from _walk_mappings(member, (*path, key))
    elif isinstance(value, list):
        for index, member in enumerate(value):
            yield from _walk_mappings(member, (*path, index))


def _resolve_json_pointer(schema: Mapping[str, Any], reference: str) -> object | None:
    if reference == "#":
        return schema
    if not reference.startswith("#/"):
        return None
    current: object = schema
    for encoded in reference[2:].split("/"):
        token = unquote(encoded).replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdecimal() and int(token) < len(current):
            current = current[int(token)]
        else:
            return None
    return current


def _schema_fingerprint(schema: Mapping[str, Any]) -> str:
    canonical = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _schema_policy_issues(schema: Mapping[str, Any], document: str) -> list[DependencyIssue]:
    issues: list[DependencyIssue] = []
    if schema.get("$schema") != DRAFT_2020_12_DIALECT or schema.get("$id") != SCHEMA_IDENTIFIER:
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_SCHEMA_IDENTITY_MISMATCH",
                document,
                "$",
                "schema dialect or identifier does not match the pinned contract",
            ),
        )
    for path, mapping in _walk_mappings(schema):
        if path and path[-1] != "properties" and ("$id" in mapping or "$schema" in mapping):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_SCHEMA_RESOURCE_SCOPE_UNSAFE",
                    document,
                    _json_path(path),
                    "nested schema resources are not permitted",
                ),
            )
        if _UNSUPPORTED_SCHEMA_REFERENCE_KEYWORDS.intersection(mapping):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_SCHEMA_REFERENCE_FEATURE_UNSAFE",
                    document,
                    _json_path(path),
                    "dynamic, recursive, and anchor references are not permitted",
                ),
            )
        for keyword in _REFERENCE_KEYWORDS.intersection(mapping):
            reference = mapping[keyword]
            if keyword != "$ref" or not isinstance(reference, str) or not reference.startswith("#"):
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_SCHEMA_REFERENCE_UNSAFE",
                        document,
                        _json_path((*path, keyword)),
                        "only local JSON Pointer references are permitted",
                    ),
                )
            elif _resolve_json_pointer(schema, reference) is None:
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_SCHEMA_REFERENCE_INVALID",
                        document,
                        _json_path((*path, keyword)),
                        "schema reference does not resolve locally",
                    ),
                )
    if _schema_fingerprint(schema) != CANONICAL_SCHEMA_FINGERPRINT:
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_SCHEMA_IDENTITY_MISMATCH",
                document,
                "$",
                "schema fingerprint does not match the reviewed contract",
            ),
        )
    return _sorted_issues(issues)


def load_dependency_schema(
    root: Path = PROJECT_ROOT,
) -> tuple[dict[str, Any] | None, list[DependencyIssue]]:
    """Load, constrain, and self-check the reviewed dependency schema."""

    schema, issues = _load_json_object(
        root / INVENTORY_SCHEMA_PATH,
        root=root,
        maximum_bytes=MAX_SCHEMA_BYTES,
    )
    if schema is None:
        return None, issues
    document = INVENTORY_SCHEMA_PATH.as_posix()
    policy_issues = _schema_policy_issues(schema, document)
    if policy_issues:
        return None, policy_issues
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        return None, [
            _issue(
                "DEPENDENCY_SCHEMA_DEFINITION_INVALID",
                document,
                "$",
                "schema definition is invalid",
            )
        ]
    except Exception:
        return None, [
            _issue(
                "DEPENDENCY_SCHEMA_EXECUTION_INVALID",
                document,
                "$",
                "schema self-check failed safely",
            )
        ]
    return schema, []


def _schema_validation_issues(
    data: Mapping[str, Any],
    schema: Mapping[str, Any],
    document: str,
) -> list[DependencyIssue]:
    issues: list[DependencyIssue] = []
    try:
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        for error in validator.iter_errors(data):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_SCHEMA_VALIDATION",
                    document,
                    _json_path(error.absolute_path),
                    _SCHEMA_MESSAGES.get(error.validator, "value violates the schema"),
                ),
            )
    except Exception:
        return [
            _issue(
                "DEPENDENCY_SCHEMA_EXECUTION_INVALID",
                document,
                "$",
                "schema validation failed safely",
            )
        ]
    return _sorted_issues(issues)


def _normalize_package_name(value: str) -> str:
    return _NORMALIZE_PACKAGE.sub("-", value).casefold()


def _safe_url(value: str, *, release_url: bool) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False
    if "@" in parsed.netloc or parsed.query or (release_url and parsed.fragment):
        return False
    hostname = parsed.hostname.casefold().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".local"):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _text_safety_issues(value: object, document: str) -> list[DependencyIssue]:
    issues: list[DependencyIssue] = []
    queue: deque[tuple[object, tuple[object, ...]]] = deque([(value, ())])
    while queue:
        member, path = queue.popleft()
        if isinstance(member, str):
            if _WINDOWS_ABSOLUTE_PATH.search(member) or _PROHIBITED_TEXT.search(member):
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_SENSITIVE_TEXT_PROHIBITED",
                        document,
                        _json_path(path),
                        "free text must not contain local paths or credential-like data",
                    ),
                )
        elif isinstance(member, Mapping):
            queue.extend((child, (*path, key)) for key, child in member.items())
        elif isinstance(member, list):
            queue.extend((child, (*path, index)) for index, child in enumerate(member))
    return issues


def _license_expression_valid(expression: str, license_refs: Mapping[str, Any]) -> bool:
    tokens = _LICENSE_TOKEN.findall(expression)
    if "".join(tokens) != re.sub(r"\s+", "", expression):
        return False
    expect_operand = True
    depth = 0
    for token in tokens:
        if expect_operand:
            if token == "(":
                depth += 1
            elif token in {"AND", "OR", ")"}:
                return False
            elif token not in KNOWN_LICENSE_IDS and token not in license_refs:
                return False
            else:
                expect_operand = False
        elif token in {"AND", "OR"}:
            expect_operand = True
        elif token == ")" and depth > 0:
            depth -= 1
        else:
            return False
    return bool(tokens) and not expect_operand and depth == 0


def _component_semantic_issues(
    data: Mapping[str, Any], document: str
) -> tuple[dict[str, Mapping[str, Any]], list[DependencyIssue]]:
    components = cast(list[Mapping[str, Any]], data["components"])
    license_refs = cast(Mapping[str, Any], data["license_refs"])
    reviewed_on = date.fromisoformat(cast(str, data["reviewed_on"]))
    issues: list[DependencyIssue] = []
    indexed: dict[str, Mapping[str, Any]] = {}
    purls: set[str] = set()
    for index, component in enumerate(components):
        path = f"$.components[{index}]"
        component_id = cast(str, component["component_id"])
        if component_id in indexed:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_COMPONENT_ID_DUPLICATE",
                    document,
                    f"{path}.component_id",
                    "component IDs must be unique",
                ),
            )
        else:
            indexed[component_id] = component

        sbom = cast(Mapping[str, Any], component["sbom"])
        purl = cast(str | None, sbom["purl"])
        if purl is not None:
            if purl in purls:
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_PURL_DUPLICATE",
                        document,
                        f"{path}.sbom.purl",
                        "non-null package URLs must be unique",
                    ),
                )
            purls.add(purl)

        integrity = cast(Mapping[str, Any], component["integrity"])
        artifacts: set[str] = set()
        for hash_index, hash_record in enumerate(
            cast(list[Mapping[str, Any]], integrity["hashes"])
        ):
            artifact = cast(str, hash_record["artifact"])
            if artifact in artifacts:
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_HASH_ARTIFACT_DUPLICATE",
                        document,
                        f"{path}.integrity.hashes[{hash_index}].artifact",
                        "artifact names must be unique within a component",
                    ),
                )
            artifacts.add(artifact)

        license_data = cast(Mapping[str, Any], component["license"])
        expression = cast(str, license_data["expression"])
        if not _license_expression_valid(expression, license_refs):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_LICENSE_EXPRESSION_INVALID",
                    document,
                    f"{path}.license.expression",
                    "license expression uses an unsupported or malformed policy identifier",
                ),
            )
        selected_route = cast(str | None, license_data.get("selected_route"))
        status = cast(str, component["admission_status"])
        if status == "approved" and " OR " in expression and selected_route is None:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_LICENSE_ROUTE_REQUIRED",
                    document,
                    f"{path}.license.selected_route",
                    "approved dual-license components require one selected route",
                ),
            )
        if selected_route is not None and selected_route not in _LICENSE_TOKEN.findall(expression):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_LICENSE_ROUTE_INVALID",
                    document,
                    f"{path}.license.selected_route",
                    "selected license route must be one identifier from the expression",
                ),
            )
        if selected_route in license_refs and status == "approved":
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_LICENSE_ROUTE_INVALID",
                    document,
                    f"{path}.license.selected_route",
                    "custom license terms cannot be approved by this policy baseline",
                ),
            )

        packaging = cast(Mapping[str, Any], component["packaging"])
        ship_state = cast(str, packaging["ship_state"])
        distribution_altering = cast(bool, license_data["distribution_altering"])
        license_tokens = set(_LICENSE_TOKEN.findall(expression))
        spdx_options = {token for token in license_tokens if token in KNOWN_LICENSE_IDS}
        strong_or_network_copyleft = (
            selected_route is not None and selected_route.startswith(("AGPL-", "GPL-"))
        ) or (
            selected_route is None
            and bool(spdx_options)
            and all(token.startswith(("AGPL-", "GPL-")) for token in spdx_options)
        )
        active_scopes = set(cast(list[str], component["active_scopes"]))
        shipping_or_runtime = ship_state != "not-shipped" or bool(
            active_scopes.intersection({"data", "runtime", "worker"})
        )
        requires_decision = (
            component_id in HARD_GATED_COMPONENT_IDS
            or distribution_altering
            or (strong_or_network_copyleft and shipping_or_runtime)
        )
        if requires_decision and (
            status != "requires-decision-change"
            or ship_state != "prohibited-without-decision-change"
            or not distribution_altering
        ):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_DISTRIBUTION_DECISION_REQUIRED",
                    document,
                    f"{path}.license",
                    "distribution-changing terms require a decision change and prohibited ship state",
                ),
            )
        if status == "requires-decision-change" and (
            not distribution_altering or ship_state != "prohibited-without-decision-change"
        ):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_DISTRIBUTION_DECISION_REQUIRED",
                    document,
                    f"{path}.admission_status",
                    "decision-gated components must remain explicitly prohibited",
                ),
            )

        binary = cast(Mapping[str, Any], component["binary"])
        security = cast(Mapping[str, Any], component["security"])
        if binary["processes_untrusted_documents"]:
            expected_boundaries = (
                {"data-only", "worker-process"}
                if component["ecosystem"] == "data"
                else {"worker-process"}
            )
            if (
                security["boundary"] not in expected_boundaries
                or not security["adapter_required"]
                or not security["contract_tests_required"]
            ):
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_SECURITY_BOUNDARY_INVALID",
                        document,
                        f"{path}.security",
                        "hostile document processors require an isolated boundary, adapter, and tests",
                    ),
                )

        maintenance = cast(Mapping[str, Any], component["maintenance"])
        if date.fromisoformat(cast(str, maintenance["checked_on"])) > reviewed_on:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_REVIEW_DATE_ORDER_INVALID",
                    document,
                    f"{path}.maintenance.checked_on",
                    "component review date cannot follow the inventory review date",
                ),
            )

        source = cast(Mapping[str, Any], component["source"])
        urls: list[tuple[str, str, bool]] = [
            ("project_url", cast(str, source["project_url"]), False),
            ("license_url", cast(str, source["license_url"]), False),
        ]
        release_url = cast(str | None, source["release_url"])
        if release_url is not None:
            urls.append(("release_url", release_url, True))
        urls.append(("evidence_url", cast(str, maintenance["evidence_url"]), False))
        renderer = cast(Mapping[str, Any] | None, component.get("renderer_assessment"))
        if renderer is not None:
            for criterion_name, criterion in renderer.items():
                criterion_data = cast(Mapping[str, Any], criterion)
                for evidence_url in cast(list[str], criterion_data["evidence_urls"]):
                    urls.append((f"renderer_assessment.{criterion_name}", evidence_url, False))
        for member_name, url, is_release in urls:
            if not _safe_url(url, release_url=is_release):
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_SOURCE_URL_UNSAFE",
                        document,
                        f"{path}.{member_name}",
                        "source and evidence URLs must be credential-free public HTTPS URLs",
                    ),
                )

        if component_id in RENDERER_COMPONENT_IDS:
            if renderer is None:
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_RENDERER_ASSESSMENT_REQUIRED",
                        document,
                        f"{path}.renderer_assessment",
                        "renderer families require all five assessment criteria",
                    ),
                )
            elif status == "approved":
                if any(
                    cast(Mapping[str, Any], criterion)["status"] != "acceptable"
                    for criterion in renderer.values()
                ):
                    _add_issue(
                        issues,
                        _issue(
                            "DEPENDENCY_RENDERER_ADMISSION_INVALID",
                            document,
                            f"{path}.renderer_assessment",
                            "approved renderers require acceptable evidence for every criterion",
                        ),
                    )

        release_states = {"bundled", "external-runtime", "shipped"}
        if ship_state in release_states:
            version = cast(Mapping[str, Any], component["version"])
            if (
                status != "approved"
                or version["pin_kind"] not in {"exact", "git-commit", "lockfile"}
                or integrity["verification"] not in {"git-commit", "sha256", "uv-lock"}
                or purl is None
                or "sbom" not in packaging["redistribution_assets"]
            ):
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_RELEASE_PIN_INVALID",
                        document,
                        f"{path}.packaging",
                        "release components require admitted exact provenance and SBOM mapping",
                    ),
                )
    return indexed, _sorted_issues(issues)


def _relationship_semantic_issues(
    data: Mapping[str, Any],
    components: Mapping[str, Mapping[str, Any]],
    document: str,
) -> list[DependencyIssue]:
    relationships = cast(list[Mapping[str, Any]], data["relationships"])
    issues: list[DependencyIssue] = []
    seen: set[tuple[str, str, str, bool]] = set()
    adjacency: dict[str, set[str]] = {}
    for index, relationship in enumerate(relationships):
        path = f"$.relationships[{index}]"
        source = cast(str, relationship["from"])
        target = cast(str, relationship["to"])
        relation_type = cast(str, relationship["type"])
        enabled = cast(bool, relationship["enabled"])
        identity = (source, relation_type, target, enabled)
        if identity in seen:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_RELATIONSHIP_DUPLICATE",
                    document,
                    path,
                    "relationship records must be unique",
                ),
            )
        seen.add(identity)
        if source not in components or target not in components:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_RELATIONSHIP_REFERENCE_UNKNOWN",
                    document,
                    path,
                    "relationship endpoints must identify known components",
                ),
            )
            continue
        if source == target:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_RELATIONSHIP_SELF_REFERENCE",
                    document,
                    path,
                    "a component cannot depend on itself",
                ),
            )
        if enabled and (
            components[source]["admission_status"] != "approved"
            or components[target]["admission_status"] != "approved"
        ):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_RELATIONSHIP_ADMISSION_INVALID",
                    document,
                    path,
                    "enabled relationships require admitted endpoints",
                ),
            )
        if enabled and source != target:
            adjacency.setdefault(source, set()).add(target)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(component_id: str) -> bool:
        if component_id in visiting:
            return True
        if component_id in visited:
            return False
        visiting.add(component_id)
        if any(visit(target) for target in adjacency.get(component_id, set())):
            return True
        visiting.remove(component_id)
        visited.add(component_id)
        return False

    if any(
        visit(component_id) for component_id in sorted(adjacency) if component_id not in visited
    ):
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_RELATIONSHIP_CYCLE",
                document,
                "$.relationships",
                "enabled dependency relationships must be acyclic",
            ),
        )
    return _sorted_issues(issues)


def _authority_reference_issues(
    root: Path,
    components: Mapping[str, Mapping[str, Any]],
    document: str,
) -> list[DependencyIssue]:
    issues: list[DependencyIssue] = []
    try:
        decisions_text = (root / "DECISIONS.md").read_text(encoding="utf-8")
        task_data = yaml.safe_load((root / "TASKS.yaml").read_text(encoding="utf-8"))
        tasks = cast(list[Mapping[str, Any]], cast(Mapping[str, Any], task_data)["tasks"])
    except OSError, UnicodeError, KeyError, TypeError, yaml.YAMLError:
        return [
            _issue(
                "DEPENDENCY_REPOSITORY_INPUT_INVALID",
                document,
                "$",
                "repository authority inputs could not be read safely",
            )
        ]
    known_decisions = set(_DECISION_HEADING.findall(decisions_text))
    known_tasks = {cast(str, task["id"]) for task in tasks if isinstance(task.get("id"), str)}
    for component_id, component in components.items():
        for decision_ref in cast(list[str], component["decision_refs"]):
            if decision_ref not in known_decisions:
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_DECISION_REFERENCE_UNKNOWN",
                        document,
                        "$.components[*].decision_refs",
                        "component references an unknown frozen decision",
                    ),
                )
        for task_ref in cast(list[str], component["task_refs"]):
            if task_ref not in known_tasks:
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_TASK_REFERENCE_UNKNOWN",
                        document,
                        "$.components[*].task_refs",
                        "component references an unknown task",
                    ),
                )
        if component_id in RENDERER_COMPONENT_IDS and "PWS-D017" not in component["decision_refs"]:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_REQUIRED_FAMILY_REFERENCE_INVALID",
                    document,
                    "$.components[*].decision_refs",
                    "renderer families must reference the renderer decision",
                ),
            )
    missing = REQUIRED_COMPONENT_IDS.difference(components)
    if missing:
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_REQUIRED_FAMILY_MISSING",
                document,
                "$.components",
                "one or more required dependency families are absent",
            ),
        )
    for decision_id, component_ids in REQUIRED_FAMILY_DECISIONS.items():
        for component_id in component_ids.intersection(components):
            if decision_id not in components[component_id]["decision_refs"]:
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_REQUIRED_FAMILY_REFERENCE_INVALID",
                        document,
                        "$.components[*].decision_refs",
                        "required dependency family is missing its frozen decision reference",
                    ),
                )
    return _sorted_issues(issues)


def _load_toml(path: Path) -> Mapping[str, Any]:
    with path.open("rb") as stream:
        return cast(Mapping[str, Any], tomllib.load(stream))


def _lock_reconciliation_issues(
    root: Path,
    data: Mapping[str, Any],
    components: Mapping[str, Mapping[str, Any]],
    document: str,
) -> list[DependencyIssue]:
    issues: list[DependencyIssue] = []
    try:
        lock = _load_toml(root / "uv.lock")
        packages = cast(list[Mapping[str, Any]], lock["package"])
    except OSError, UnicodeError, KeyError, TypeError, tomllib.TOMLDecodeError:
        return [
            _issue(
                "DEPENDENCY_REPOSITORY_INPUT_INVALID",
                document,
                "$",
                "lockfile could not be read safely",
            )
        ]
    locked = {
        _normalize_package_name(cast(str, package["name"])): package
        for package in packages
        if _normalize_package_name(cast(str, package["name"])) != "paperworkshell"
    }
    inventoried = {
        component_id
        for component_id, component in components.items()
        if component["ecosystem"] == "python-package"
        and component["admission_status"] == "approved"
        and component_id != "uv-build"
    }
    if set(locked) != inventoried:
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_LOCK_COMPONENT_MISMATCH",
                document,
                "$.components",
                "approved Python component set must exactly match the resolved lock",
            ),
        )
    for component_id in sorted(set(locked).intersection(inventoried)):
        package = locked[component_id]
        component = components[component_id]
        version = cast(Mapping[str, Any], component["version"])
        if version["selected"] != package["version"]:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_LOCK_VERSION_MISMATCH",
                    document,
                    "$.components[*].version.selected",
                    "selected Python version must match the lock",
                ),
            )
        sdist = cast(Mapping[str, Any] | None, package.get("sdist"))
        if sdist is None:
            continue
        hash_value = cast(str, sdist["hash"]).removeprefix("sha256:")
        artifact = Path(urlsplit(cast(str, sdist["url"])).path).name
        inventory_hashes = cast(
            list[Mapping[str, Any]], cast(Mapping[str, Any], component["integrity"])["hashes"]
        )
        if not any(
            item["value"] == hash_value and item["artifact"] == artifact
            for item in inventory_hashes
        ):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_LOCK_HASH_MISMATCH",
                    document,
                    "$.components[*].integrity.hashes",
                    "recorded release source must match the locked source artifact",
                ),
            )

    expected_edges: set[tuple[str, str]] = set()
    for component_id, package in locked.items():
        for dependency in cast(list[Mapping[str, Any]], package.get("dependencies", [])):
            target = _normalize_package_name(cast(str, dependency["name"]))
            if target in locked:
                expected_edges.add((component_id, target))
    actual_edges = {
        (cast(str, relationship["from"]), cast(str, relationship["to"]))
        for relationship in cast(list[Mapping[str, Any]], data["relationships"])
        if relationship["enabled"]
        and relationship["type"] == "depends-on"
        and relationship["from"] in locked
        and relationship["to"] in locked
    }
    if actual_edges != expected_edges:
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_LOCK_RELATIONSHIP_MISMATCH",
                document,
                "$.relationships",
                "enabled Python dependency edges must match the resolved lock",
            ),
        )
    return _sorted_issues(issues)


def _exact_requirement(requirement: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)", requirement)
    if match is None:
        return None
    return _normalize_package_name(match.group(1)), match.group(2)


def _declaration_reconciliation_issues(
    root: Path,
    components: Mapping[str, Mapping[str, Any]],
    document: str,
) -> list[DependencyIssue]:
    issues: list[DependencyIssue] = []
    try:
        configuration = _load_toml(root / "pyproject.toml")
        groups = cast(Mapping[str, list[object]], configuration["dependency-groups"])
        build_system = cast(Mapping[str, Any], configuration["build-system"])
        uv_configuration = cast(
            Mapping[str, Any], cast(Mapping[str, Any], configuration["tool"])["uv"]
        )
    except OSError, UnicodeError, KeyError, TypeError, tomllib.TOMLDecodeError:
        return [
            _issue(
                "DEPENDENCY_REPOSITORY_INPUT_INVALID",
                document,
                "$",
                "project declarations could not be read safely",
            )
        ]
    declarations: dict[str, str] = {}
    for members in groups.values():
        for member in members:
            if not isinstance(member, str):
                continue
            parsed = _exact_requirement(member)
            if parsed is None:
                _add_issue(
                    issues,
                    _issue(
                        "DEPENDENCY_DECLARATION_VERSION_MISMATCH",
                        document,
                        "$.components",
                        "direct Python declarations must use exact versions",
                    ),
                )
                continue
            declarations[parsed[0]] = parsed[1]
    build_requirements = cast(list[str], build_system["requires"])
    for requirement in build_requirements:
        parsed = _exact_requirement(requirement)
        if parsed is not None:
            declarations[parsed[0]] = parsed[1]
    approved_direct = {
        component_id
        for component_id, component in components.items()
        if component["ecosystem"] == "python-package"
        and component["relationship"] == "direct"
        and component["admission_status"] == "approved"
    }
    if set(declarations) != approved_direct:
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_DECLARATION_MISMATCH",
                document,
                "$.components",
                "approved direct Python components must match project declarations",
            ),
        )
    for component_id, version in declarations.items():
        component = components.get(component_id)
        if (
            component is not None
            and cast(Mapping[str, Any], component["version"])["selected"] != version
        ):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_DECLARATION_VERSION_MISMATCH",
                    document,
                    "$.components[*].version.selected",
                    "selected direct dependency version must match the project declaration",
                ),
            )
    uv_requirement = cast(str, uv_configuration["required-version"])
    if uv_requirement != f"=={cast(Mapping[str, Any], components['uv']['version'])['selected']}":
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_CONFIG_PIN_MISMATCH",
                document,
                "$.components[*].version.selected",
                "uv policy pin must match the project configuration",
            ),
        )
    return _sorted_issues(issues)


def _workflow_reconciliation_issues(
    root: Path,
    components: Mapping[str, Mapping[str, Any]],
    document: str,
) -> list[DependencyIssue]:
    issues: list[DependencyIssue] = []
    try:
        workflow = (root / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
        dev_tool = (root / "tools" / "dev.py").read_text(encoding="utf-8")
        python_minor = (root / ".python-version").read_text(encoding="utf-8").strip()
    except OSError, UnicodeError:
        return [
            _issue(
                "DEPENDENCY_REPOSITORY_INPUT_INVALID",
                document,
                "$",
                "workflow pin inputs could not be read safely",
            )
        ]
    action_components = {
        "actions/checkout": "actions-checkout",
        "actions/setup-python": "actions-setup-python",
        "astral-sh/setup-uv": "astral-setup-uv",
    }
    found_actions: set[str] = set()
    for match in re.finditer(r"uses:\s+([^@\s]+)@([0-9a-f]{40})\s+#\s+v([^\s]+)", workflow):
        action_name, revision, version = match.groups()
        component_id = action_components.get(action_name)
        if component_id is None:
            continue
        found_actions.add(component_id)
        component = components[component_id]
        if (
            cast(Mapping[str, Any], component["integrity"])["revision"] != revision
            or cast(Mapping[str, Any], component["version"])["selected"] != version
        ):
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_CONFIG_PIN_MISMATCH",
                    document,
                    "$.components[*]",
                    "workflow action pin must match the dependency inventory",
                ),
            )
    if found_actions != set(action_components.values()):
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_CONFIG_PIN_MISMATCH",
                document,
                "$.components",
                "required workflow action pins are incomplete",
            ),
        )
    uv_version = re.search(r'^\s+version:\s+"([^"]+)"$', workflow, re.MULTILINE)
    uv_checksum = re.search(r"^\s+checksum:\s+([0-9a-f]{64})$", workflow, re.MULTILINE)
    gitleaks_version = re.search(r'GITLEAKS_VERSION: Final = "([^"]+)"', dev_tool)
    minimum_git = re.search(r"MINIMUM_GIT_VERSION: Final = \((\d+),\s*(\d+)\)", dev_tool)
    uv_component = components["uv"]
    uv_hashes = cast(
        list[Mapping[str, Any]], cast(Mapping[str, Any], uv_component["integrity"])["hashes"]
    )
    if (
        uv_version is None
        or uv_checksum is None
        or uv_version.group(1) != cast(Mapping[str, Any], uv_component["version"])["selected"]
        or not any(item["value"] == uv_checksum.group(1) for item in uv_hashes)
    ):
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_CONFIG_PIN_MISMATCH",
                document,
                "$.components[*]",
                "workflow uv version and checksum must match the dependency inventory",
            ),
        )
    if (
        gitleaks_version is None
        or gitleaks_version.group(1)
        != cast(Mapping[str, Any], components["gitleaks"]["version"])["selected"]
    ):
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_CONFIG_PIN_MISMATCH",
                document,
                "$.components[*]",
                "developer Gitleaks pin must match the dependency inventory",
            ),
        )
    git_constraint = cast(str, cast(Mapping[str, Any], components["git"]["version"])["constraint"])
    expected_git = (
        None if minimum_git is None else f">={minimum_git.group(1)}.{minimum_git.group(2)}"
    )
    if expected_git is None or git_constraint != expected_git:
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_CONFIG_PIN_MISMATCH",
                document,
                "$.components[*]",
                "developer Git minimum must match the dependency inventory",
            ),
        )
    if python_minor != cast(Mapping[str, Any], components["cpython"]["version"])["selected"]:
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_CONFIG_PIN_MISMATCH",
                document,
                "$.components[*]",
                "Python minor policy must match the dependency inventory",
            ),
        )
    return _sorted_issues(issues)


def _repository_semantic_issues(
    root: Path,
    data: Mapping[str, Any],
    components: Mapping[str, Mapping[str, Any]],
    document: str,
) -> list[DependencyIssue]:
    issues: list[DependencyIssue] = []
    policy = cast(Mapping[str, Any], data["policy"])
    policy_path = root / cast(str, policy["document"])
    if not policy_path.is_file():
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_POLICY_DOCUMENT_MISSING",
                document,
                "$.policy.document",
                "dependency policy document is missing",
            ),
        )
    for collection in (
        _authority_reference_issues(root, components, document),
        _lock_reconciliation_issues(root, data, components, document),
        _declaration_reconciliation_issues(root, components, document),
        _workflow_reconciliation_issues(root, components, document),
    ):
        for issue in collection:
            _add_issue(issues, issue)

    relationships = cast(list[Mapping[str, Any]], data["relationships"])
    ghostscript_edge = next(
        (
            relationship
            for relationship in relationships
            if relationship["from"] == "ocrmypdf" and relationship["to"] == "ghostscript"
        ),
        None,
    )
    qt_pdf_edge = next(
        (
            relationship
            for relationship in relationships
            if relationship["from"] == "pyside6" and relationship["to"] == "qt-pdf"
        ),
        None,
    )
    if ghostscript_edge is None or ghostscript_edge["enabled"]:
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_OPTIONAL_TOOL_POLICY_INVALID",
                document,
                "$.relationships",
                "ambient Ghostscript discovery must remain disabled",
            ),
        )
    if (
        qt_pdf_edge is None
        or qt_pdf_edge["enabled"]
        or components["pyside6"]["admission_status"] == "approved"
    ):
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_PYSIDE_VARIANT_POLICY_INVALID",
                document,
                "$.relationships",
                "PySide6 meta-package and Qt PDF containment must remain unadmitted",
            ),
        )
    return _sorted_issues(issues)


def validate_dependency_data(
    data: Mapping[str, Any],
    schema: Mapping[str, Any],
    *,
    document: str = "dependency-inventory.json",
    root: Path = PROJECT_ROOT,
    repository_checks: bool = False,
) -> tuple[dict[str, Any] | None, list[DependencyIssue]]:
    """Validate one already-loaded dependency inventory without mutating it."""

    version = data.get("schema_version")
    if type(version) is not int:
        return None, [
            _issue(
                "DEPENDENCY_VERSION_INVALID",
                document,
                "$.schema_version",
                "schema_version must be an integer",
            )
        ]
    if version != SUPPORTED_SCHEMA_VERSION:
        return None, [
            _issue(
                "DEPENDENCY_VERSION_UNSUPPORTED",
                document,
                "$.schema_version",
                "schema_version is not supported",
            )
        ]
    schema_issues = _schema_validation_issues(data, schema, document)
    if schema_issues:
        return None, schema_issues
    try:
        components, component_issues = _component_semantic_issues(data, document)
        relationship_issues = _relationship_semantic_issues(data, components, document)
        issues = [*component_issues, *relationship_issues, *_text_safety_issues(data, document)]
        if cast(str, data["inventory_profile"]) != "policy-baseline":
            issues.append(
                _issue(
                    "DEPENDENCY_SBOM_INPUT_INVALID",
                    document,
                    "$.inventory_profile",
                    "schema v1 accepts only a non-release policy baseline",
                )
            )
        if date.fromisoformat(cast(str, data["reviewed_on"])) > date.today():
            issues.append(
                _issue(
                    "DEPENDENCY_REVIEW_DATE_ORDER_INVALID",
                    document,
                    "$.reviewed_on",
                    "inventory review date cannot be in the future",
                )
            )
        if repository_checks:
            issues.extend(_repository_semantic_issues(root, data, components, document))
    except Exception:
        return None, [
            _issue(
                "DEPENDENCY_SEMANTIC_EXECUTION_INVALID",
                document,
                "$",
                "semantic validation failed safely",
            )
        ]
    sorted_issues = _sorted_issues(issues)
    return (None, sorted_issues) if sorted_issues else (dict(data), [])


def validate_dependency_document(
    path: Path,
    root: Path = PROJECT_ROOT,
    *,
    schema: Mapping[str, Any] | None = None,
    repository_checks: bool = False,
) -> tuple[dict[str, Any] | None, list[DependencyIssue]]:
    """Load and validate one dependency inventory document."""

    resolved_path = path if path.is_absolute() else root / path
    data, data_issues = _load_json_object(
        resolved_path,
        root=root,
        maximum_bytes=MAX_INVENTORY_BYTES,
    )
    if data is None:
        return None, data_issues
    loaded_schema = schema
    if loaded_schema is None:
        loaded_schema, schema_issues = load_dependency_schema(root)
        if loaded_schema is None:
            return None, schema_issues
    return validate_dependency_data(
        data,
        loaded_schema,
        document=_document_label(resolved_path, root),
        root=root,
        repository_checks=repository_checks,
    )


def _fixture_paths(
    directory: Path,
    *,
    root: Path,
) -> tuple[list[Path] | None, list[DependencyIssue]]:
    document = _document_label(directory, root)
    try:
        resolved_directory = directory.resolve(strict=True)
        candidates: list[Path] = []
        for candidate in directory.iterdir():
            if not candidate.is_file() or not candidate.match("*.json"):
                continue
            if (
                candidate.is_symlink()
                or candidate.resolve(strict=True).parent != resolved_directory
            ):
                return None, [
                    _issue(
                        "DEPENDENCY_FIXTURE_PATH_UNSAFE",
                        document,
                        "$",
                        "fixture paths must remain inside the fixture directory",
                    )
                ]
            candidates.append(candidate)
            if len(candidates) > MAX_FIXTURE_FILES:
                return None, [
                    _issue(
                        "DEPENDENCY_FIXTURE_LIMIT",
                        document,
                        "$",
                        "fixture set exceeds its file-count limit",
                    )
                ]
    except OSError as error:
        return None, [
            _issue(
                "DEPENDENCY_FIXTURE_SET_UNREADABLE",
                document,
                "$",
                type(error).__name__,
            )
        ]
    return sorted(candidates, key=lambda candidate: candidate.name), []


def validate_dependency_fixture_set(
    root: Path = PROJECT_ROOT,
) -> tuple[DependencyFixtureSummary | None, list[DependencyIssue]]:
    """Validate the canonical baseline and prove each rejected example fails as intended."""

    schema, schema_issues = load_dependency_schema(root)
    if schema is None:
        return None, schema_issues
    _, positive_issues = validate_dependency_document(
        INVENTORY_PATH,
        root,
        schema=schema,
        repository_checks=True,
    )
    invalid_paths, path_issues = _fixture_paths(root / INVALID_FIXTURE_DIRECTORY, root=root)
    if invalid_paths is None:
        return None, path_issues
    issues: list[DependencyIssue] = []
    for positive_issue in positive_issues:
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_POSITIVE_FIXTURE_INVALID",
                positive_issue.document,
                positive_issue.path,
                "canonical dependency policy baseline is invalid",
            ),
        )
    if not invalid_paths:
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_NEGATIVE_FIXTURES_MISSING",
                INVALID_FIXTURE_DIRECTORY.as_posix(),
                "$",
                "at least one rejected dependency example is required",
            ),
        )
    actual_names = {path.name for path in invalid_paths}
    if actual_names != set(EXPECTED_NEGATIVE_CODES):
        _add_issue(
            issues,
            _issue(
                "DEPENDENCY_NEGATIVE_FIXTURE_SET_MISMATCH",
                INVALID_FIXTURE_DIRECTORY.as_posix(),
                "$",
                "rejected dependency example set does not match the reviewed contract",
            ),
        )
    for path in invalid_paths:
        data, fixture_issues = validate_dependency_document(path, root, schema=schema)
        if data is not None or not fixture_issues:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_NEGATIVE_FIXTURE_ACCEPTED",
                    _document_label(path, root),
                    "$",
                    "rejected dependency example was accepted",
                ),
            )
            continue
        expected_code = EXPECTED_NEGATIVE_CODES.get(path.name)
        if expected_code is not None and expected_code not in {
            issue.code for issue in fixture_issues
        }:
            _add_issue(
                issues,
                _issue(
                    "DEPENDENCY_NEGATIVE_FIXTURE_REASON_MISMATCH",
                    _document_label(path, root),
                    "$",
                    "rejected dependency example failed for an unexpected reason",
                ),
            )
    if issues:
        return None, _sorted_issues(issues)
    return DependencyFixtureSummary(positive_count=1, negative_count=len(invalid_paths)), []


def validation_messages(root: Path = PROJECT_ROOT) -> list[str]:
    """Return deterministic diagnostics for schema-registry integration."""

    _, issues = validate_dependency_fixture_set(root)
    return [issue.render() for issue in issues]


def main(argv: Sequence[str] | None = None) -> int:
    """Validate the dependency policy baseline or one supplied document."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=PROJECT_ROOT,
        help="repository root (default: detected project root)",
    )
    parser.add_argument("document", nargs="?", type=Path)
    arguments = parser.parse_args(argv)
    if arguments.document is None:
        summary, issues = validate_dependency_fixture_set(arguments.root)
    else:
        _, issues = validate_dependency_document(arguments.document, arguments.root)
        summary = None
    if issues:
        for issue in issues:
            print(issue.render(), file=sys.stderr)
        return 1
    if summary is None:
        print("OK: validated dependency inventory v1 document.")
    else:
        print(
            "OK: validated dependency inventory v1 with "
            f"{summary.positive_count} complete and {summary.negative_count} rejected examples."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

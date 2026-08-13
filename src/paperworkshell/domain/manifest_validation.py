"""Pure, non-disclosing validation for decoded case-manifest v1/v2 data.

The runtime validator intentionally implements only the reviewed JSON Schema
subset used by the canonical manifest-v1/v2 contracts. Keeping this interpreter
in the domain layer avoids a runtime dependency on schema libraries while the
schema fingerprint prevents callers from substituting a weaker contract.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Final, cast

# ``CANONICAL_SCHEMA_FINGERPRINT`` remains the published v1 identity for
# compatibility with callers that explicitly validate a historical manifest.
SUPPORTED_SCHEMA_VERSION: Final = 2
CANONICAL_SCHEMA_FINGERPRINT: Final = (
    "a4dfbc925b601ce3dca08085173a742642cab6b962b4358f91d921bd9fdbd695"
)
CURRENT_SCHEMA_FINGERPRINT: Final = (
    "af27d3d3569d660638371d840a35b754335b074021c39d20cc4f282df4c7e599"
)
_SCHEMA_FINGERPRINTS_BY_VERSION: Final = {
    1: CANONICAL_SCHEMA_FINGERPRINT,
    2: CURRENT_SCHEMA_FINGERPRINT,
}
MAX_REPORTED_ISSUES: Final = 512
MAX_VALIDATION_STEPS: Final = 1_000_000
MAX_SCHEMA_RECURSION: Final = 128
MAX_OUTPUT_NAME_UTF16_CODE_UNITS: Final = 180
NORMALIZED_PAGE_MILLIONTHS: Final = 1_000_000

_SAFE_PATH_TOKEN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INVALID_JSON_POINTER_ESCAPE: Final = re.compile(r"~(?:[^01]|$)")
_RFC3339_DATE_TIME: Final = re.compile(
    r"^(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})"
    r"[Tt](?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"
    r"(?:\.[0-9]+)?(?P<zone>[Zz]|[+-][0-9]{2}:[0-9]{2})$"
)
_UUID_V4_EXACT: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
_SHA256_EXACT: Final = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN_EXACT: Final = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*\Z")
_SEMVER_EXACT: Final = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
_STORAGE_NAME_EXACT: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.bin\Z"
)
_RGBA_EXACT: Final = re.compile(r"#[0-9a-fA-F]{8}\Z")
_UUID_FIELDS: Final = frozenset(
    {
        "asset_id",
        "assignment_id",
        "case_id",
        "confirmation_id",
        "export_id",
        "group_id",
        "operation_id",
        "overlay_id",
        "page_id",
        "source_id",
    }
)
_UUID_LIST_FIELDS: Final = frozenset(
    {
        "after_page_ids",
        "before_page_ids",
        "page_ids",
        "page_sequence",
        "page_sequence_base",
    }
)
_TOKEN_FIELDS: Final = frozenset(
    {"confirmation_code", "font_token", "requirement_id", "template_id"}
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
_SCHEMA_MESSAGES: Final[dict[str, str]] = {
    "additionalProperties": "unknown members are not permitted",
    "allOf": "value does not match every required shape",
    "const": "value is not supported",
    "enum": "value is not supported",
    "format": "value has an invalid format",
    "maxItems": "array length is outside the allowed range",
    "maxLength": "text length is outside the allowed range",
    "maximum": "number is outside the allowed range",
    "minItems": "array length is outside the allowed range",
    "minLength": "text length is outside the allowed range",
    "minimum": "number is outside the allowed range",
    "not": "value is explicitly prohibited",
    "oneOf": "value does not match exactly one supported shape",
    "pattern": "value has an invalid format",
    "required": "a required member is missing",
    "type": "value has an invalid JSON type",
    "uniqueItems": "array members must be unique",
}


@dataclass(frozen=True, slots=True)
class ManifestValidationIssue:
    """One stable validation issue without manifest values or document paths."""

    code: str
    path: str
    message: str


class _SchemaExecutionError(RuntimeError):
    """Internal sentinel for a malformed or over-budget supplied schema."""


def _issue(code: str, path: str, message: str) -> ManifestValidationIssue:
    return ManifestValidationIssue(code=code, path=path, message=message)


def _add_issue(
    issues: list[ManifestValidationIssue],
    issue: ManifestValidationIssue,
) -> None:
    if len(issues) < MAX_REPORTED_ISSUES - 1:
        issues.append(issue)
        return
    if not any(existing.code == "MANIFEST_ISSUE_LIMIT" for existing in issues):
        issues.append(
            _issue(
                "MANIFEST_ISSUE_LIMIT",
                "$",
                "additional validation errors were suppressed",
            )
        )


def _sorted_issues(
    issues: Iterable[ManifestValidationIssue],
) -> tuple[ManifestValidationIssue, ...]:
    return tuple(sorted(issues, key=lambda issue: (issue.path, issue.code, issue.message)))


def _child_path(path: str, part: object) -> str:
    if type(part) is int:
        return f"{path}[{part}]"
    if type(part) is str and _SAFE_PATH_TOKEN.fullmatch(part) is not None:
        return f"{path}.{part}"
    return f"{path}[*]"


def _schema_fingerprint(schema: Mapping[str, object]) -> str | None:
    try:
        encoded = json.dumps(
            schema,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except RecursionError, TypeError, UnicodeEncodeError, ValueError:
        return None
    return hashlib.sha256(encoded).hexdigest()


def _json_number(value: object) -> bool:
    if type(value) is int:
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) is Decimal:
        return value.is_finite()
    return False


def _json_integer(value: object) -> bool:
    if type(value) is int:
        return True
    if type(value) is float:
        return math.isfinite(value) and value.is_integer()
    if type(value) is Decimal:
        return value.is_finite() and value == value.to_integral_value()
    return False


def _json_equal(left: object, right: object) -> bool:
    if type(left) is bool or type(right) is bool:
        return type(left) is bool and type(right) is bool and left is right
    if _json_number(left) and _json_number(right):
        try:
            return bool(left == right)
        except ArithmeticError, TypeError, ValueError:
            return False
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        if set(left) != set(right):
            return False
        return all(_json_equal(left[key], right[key]) for key in left)
    if type(left) is list and type(right) is list:
        return len(left) == len(right) and all(
            _json_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return type(left) is type(right) and bool(left == right)


def _json_equality_key(value: object, *, depth: int = 0) -> object:
    """Return a hashable JSON-equivalence key for bounded uniqueness checks."""

    if depth > MAX_SCHEMA_RECURSION:
        raise _SchemaExecutionError
    if value is None:
        return ("null",)
    if type(value) is bool:
        return ("boolean", value)
    if _json_number(value):
        try:
            number = Decimal(str(value)).normalize()
        except (ArithmeticError, ValueError) as error:
            raise _SchemaExecutionError from error
        return ("number", number)
    if type(value) is str:
        return ("string", value)
    if type(value) is list:
        return (
            "array",
            tuple(_json_equality_key(item, depth=depth + 1) for item in value),
        )
    if isinstance(value, Mapping):
        if not all(type(member) is str for member in value):
            raise _SchemaExecutionError
        return (
            "object",
            tuple(
                (member, _json_equality_key(value[member], depth=depth + 1))
                for member in sorted(value)
            ),
        )
    raise _SchemaExecutionError


def _matches_type(value: object, expected: object) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return type(value) is bool
    if expected == "integer":
        return _json_integer(value)
    if expected == "number":
        return _json_number(value)
    if expected == "string":
        return type(value) is str
    if expected == "array":
        return type(value) is list
    if expected == "object":
        return isinstance(value, Mapping)
    raise _SchemaExecutionError


def _valid_rfc3339_date_time(value: str) -> bool:
    matched = _RFC3339_DATE_TIME.fullmatch(value)
    if matched is None:
        return False
    year = int(matched["year"])
    month = int(matched["month"])
    day = int(matched["day"])
    hour = int(matched["hour"])
    minute = int(matched["minute"])
    second = int(matched["second"])
    zone = matched["zone"]
    if not 1 <= month <= 12 or hour > 23 or minute > 59 or second > 60:
        return False
    if zone not in {"Z", "z"}:
        zone_hour = int(zone[1:3])
        zone_minute = int(zone[4:6])
        if zone_hour > 23 or zone_minute > 59:
            return False
    leap_year = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    month_lengths = (31, 29 if leap_year else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    return 1 <= day <= month_lengths[month - 1]


class _SchemaSubsetValidator:
    """Bounded interpreter for the canonical manifest schema keyword subset."""

    def __init__(self, root_schema: Mapping[str, object]) -> None:
        self._root_schema = root_schema
        self._steps = 0

    def validate(self, value: object) -> tuple[ManifestValidationIssue, ...]:
        issues: list[ManifestValidationIssue] = []
        self._validate(value, self._root_schema, "$", issues, depth=0, active_refs=set())
        return _sorted_issues(issues)

    def _step(self, depth: int) -> None:
        self._steps += 1
        if self._steps > MAX_VALIDATION_STEPS or depth > MAX_SCHEMA_RECURSION:
            raise _SchemaExecutionError

    def _target_for_reference(self, reference: object) -> Mapping[str, object] | bool:
        if type(reference) is not str or not reference.startswith("#/"):
            raise _SchemaExecutionError
        current: object = self._root_schema
        for encoded_part in reference[2:].split("/"):
            if _INVALID_JSON_POINTER_ESCAPE.search(encoded_part) is not None:
                raise _SchemaExecutionError
            part = encoded_part.replace("~1", "/").replace("~0", "~")
            if not isinstance(current, Mapping) or part not in current:
                raise _SchemaExecutionError
            current = current[part]
        if type(current) is bool or isinstance(current, Mapping):
            return current
        raise _SchemaExecutionError

    def _branch_matches(
        self,
        value: object,
        schema: object,
        path: str,
        *,
        depth: int,
        active_refs: set[str],
    ) -> bool:
        branch_issues: list[ManifestValidationIssue] = []
        self._validate(
            value,
            schema,
            path,
            branch_issues,
            depth=depth,
            active_refs=set(active_refs),
        )
        return not branch_issues

    def _schema_issue(
        self,
        issues: list[ManifestValidationIssue],
        path: str,
        keyword: str,
    ) -> None:
        _add_issue(
            issues,
            _issue("MANIFEST_SCHEMA_VALIDATION", path, _SCHEMA_MESSAGES[keyword]),
        )

    def _validate(
        self,
        value: object,
        raw_schema: object,
        path: str,
        issues: list[ManifestValidationIssue],
        *,
        depth: int,
        active_refs: set[str],
    ) -> None:
        self._step(depth)
        if type(raw_schema) is bool:
            if not raw_schema:
                self._schema_issue(issues, path, "not")
            return
        if not isinstance(raw_schema, Mapping):
            raise _SchemaExecutionError
        schema = cast(Mapping[str, object], raw_schema)

        reference = schema.get("$ref")
        if reference is not None:
            if type(reference) is not str or reference in active_refs:
                raise _SchemaExecutionError
            target = self._target_for_reference(reference)
            active_refs.add(reference)
            try:
                self._validate(
                    value,
                    target,
                    path,
                    issues,
                    depth=depth + 1,
                    active_refs=active_refs,
                )
            finally:
                active_refs.remove(reference)

        expected_type = schema.get("type")
        if expected_type is not None:
            if type(expected_type) is str:
                type_matches = _matches_type(value, expected_type)
            elif type(expected_type) is list and expected_type:
                type_matches = all(type(item) is str for item in expected_type) and any(
                    _matches_type(value, item) for item in expected_type
                )
            else:
                raise _SchemaExecutionError
            if not type_matches:
                self._schema_issue(issues, path, "type")

        if "const" in schema and not _json_equal(value, schema["const"]):
            self._schema_issue(issues, path, "const")
        enum = schema.get("enum")
        if enum is not None:
            if type(enum) is not list or not enum:
                raise _SchemaExecutionError
            if not any(_json_equal(value, candidate) for candidate in enum):
                self._schema_issue(issues, path, "enum")

        if type(value) is str:
            self._validate_string(value, schema, path, issues)
        if _json_number(value):
            self._validate_number(value, schema, path, issues)
        if type(value) is list:
            self._validate_array(
                value,
                schema,
                path,
                issues,
                depth=depth,
                active_refs=active_refs,
            )
        if isinstance(value, Mapping):
            self._validate_object(
                value,
                schema,
                path,
                issues,
                depth=depth,
                active_refs=active_refs,
            )

        all_of = schema.get("allOf")
        if all_of is not None:
            if type(all_of) is not list or not all_of:
                raise _SchemaExecutionError
            for subschema in all_of:
                self._validate(
                    value,
                    subschema,
                    path,
                    issues,
                    depth=depth + 1,
                    active_refs=set(active_refs),
                )

        one_of = schema.get("oneOf")
        if one_of is not None:
            if type(one_of) is not list or not one_of:
                raise _SchemaExecutionError
            match_count = sum(
                self._branch_matches(
                    value,
                    subschema,
                    path,
                    depth=depth + 1,
                    active_refs=active_refs,
                )
                for subschema in one_of
            )
            if match_count != 1:
                self._schema_issue(issues, path, "oneOf")

        prohibited = schema.get("not")
        if prohibited is not None and self._branch_matches(
            value,
            prohibited,
            path,
            depth=depth + 1,
            active_refs=active_refs,
        ):
            self._schema_issue(issues, path, "not")

        condition = schema.get("if")
        if condition is not None:
            branch_keyword = (
                "then"
                if self._branch_matches(
                    value,
                    condition,
                    path,
                    depth=depth + 1,
                    active_refs=active_refs,
                )
                else "else"
            )
            branch = schema.get(branch_keyword)
            if branch is not None:
                self._validate(
                    value,
                    branch,
                    path,
                    issues,
                    depth=depth + 1,
                    active_refs=set(active_refs),
                )

    def _validate_string(
        self,
        value: str,
        schema: Mapping[str, object],
        path: str,
        issues: list[ManifestValidationIssue],
    ) -> None:
        minimum_length = schema.get("minLength")
        maximum_length = schema.get("maxLength")
        if minimum_length is not None:
            if type(minimum_length) is not int or minimum_length < 0:
                raise _SchemaExecutionError
            if len(value) < minimum_length:
                self._schema_issue(issues, path, "minLength")
        if maximum_length is not None:
            if type(maximum_length) is not int or maximum_length < 0:
                raise _SchemaExecutionError
            if len(value) > maximum_length:
                self._schema_issue(issues, path, "maxLength")
        pattern = schema.get("pattern")
        if pattern is not None:
            if type(pattern) is not str:
                raise _SchemaExecutionError
            try:
                matched = re.search(pattern, value) is not None
            except re.error as error:
                raise _SchemaExecutionError from error
            if not matched:
                self._schema_issue(issues, path, "pattern")
        format_name = schema.get("format")
        if format_name is not None:
            if format_name != "date-time":
                raise _SchemaExecutionError
            if not _valid_rfc3339_date_time(value):
                self._schema_issue(issues, path, "format")

    def _validate_number(
        self,
        value: object,
        schema: Mapping[str, object],
        path: str,
        issues: list[ManifestValidationIssue],
    ) -> None:
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None:
            if not _json_number(minimum):
                raise _SchemaExecutionError
            try:
                below = bool(value < minimum)  # type: ignore[operator]
            except (ArithmeticError, TypeError, ValueError) as error:
                raise _SchemaExecutionError from error
            if below:
                self._schema_issue(issues, path, "minimum")
        if maximum is not None:
            if not _json_number(maximum):
                raise _SchemaExecutionError
            try:
                above = bool(value > maximum)  # type: ignore[operator]
            except (ArithmeticError, TypeError, ValueError) as error:
                raise _SchemaExecutionError from error
            if above:
                self._schema_issue(issues, path, "maximum")

    def _validate_array(
        self,
        value: list[object],
        schema: Mapping[str, object],
        path: str,
        issues: list[ManifestValidationIssue],
        *,
        depth: int,
        active_refs: set[str],
    ) -> None:
        minimum_items = schema.get("minItems")
        maximum_items = schema.get("maxItems")
        if minimum_items is not None:
            if type(minimum_items) is not int or minimum_items < 0:
                raise _SchemaExecutionError
            if len(value) < minimum_items:
                self._schema_issue(issues, path, "minItems")
        if maximum_items is not None:
            if type(maximum_items) is not int or maximum_items < 0:
                raise _SchemaExecutionError
            if len(value) > maximum_items:
                self._schema_issue(issues, path, "maxItems")
                return
        if schema.get("uniqueItems") is True:
            equality_keys = [_json_equality_key(item) for item in value]
            duplicate_found = len(equality_keys) != len(set(equality_keys))
            if duplicate_found:
                self._schema_issue(issues, path, "uniqueItems")
        elif "uniqueItems" in schema and schema["uniqueItems"] is not False:
            raise _SchemaExecutionError
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value):
                self._validate(
                    item,
                    item_schema,
                    _child_path(path, index),
                    issues,
                    depth=depth + 1,
                    active_refs=set(active_refs),
                )

    def _validate_object(
        self,
        value: Mapping[object, object],
        schema: Mapping[str, object],
        path: str,
        issues: list[ManifestValidationIssue],
        *,
        depth: int,
        active_refs: set[str],
    ) -> None:
        properties_value = schema.get("properties", {})
        if not isinstance(properties_value, Mapping):
            raise _SchemaExecutionError
        properties = cast(Mapping[str, object], properties_value)
        required = schema.get("required", [])
        if type(required) is not list or not all(type(item) is str for item in required):
            raise _SchemaExecutionError
        for member in required:
            if member not in value:
                self._schema_issue(issues, path, "required")
        for member, subschema in properties.items():
            if type(member) is not str:
                raise _SchemaExecutionError
            if member in value:
                self._validate(
                    value[member],
                    subschema,
                    _child_path(path, member),
                    issues,
                    depth=depth + 1,
                    active_refs=set(active_refs),
                )
        additional = schema.get("additionalProperties", True)
        unknown_members = [member for member in value if member not in properties]
        if additional is False and unknown_members:
            self._schema_issue(issues, path, "additionalProperties")
        elif isinstance(additional, Mapping) or type(additional) is bool:
            if additional is not True and additional is not False:
                for member in unknown_members:
                    self._validate(
                        value[member],
                        additional,
                        _child_path(path, member),
                        issues,
                        depth=depth + 1,
                        active_refs=set(active_refs),
                    )
        else:
            raise _SchemaExecutionError


def _index_records(
    records: Sequence[Mapping[str, Any]],
    *,
    id_field: str,
    collection_path: str,
    duplicate_code: str,
    issues: list[ManifestValidationIssue],
) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for index, record in enumerate(records):
        record_id = cast(str, record[id_field])
        if record_id in indexed:
            _add_issue(
                issues,
                _issue(
                    duplicate_code,
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
    issues: list[ManifestValidationIssue],
) -> None:
    for record_id in records:
        if record_id in namespace:
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_ID_NAMESPACE_COLLISION",
                    f"{collection_path}.{id_field}",
                    "identifier collides with another manifest namespace",
                ),
            )
        else:
            namespace[record_id] = kind


def _timestamp(
    value: str,
    *,
    path: str,
    issues: list[ManifestValidationIssue],
) -> datetime | None:
    try:
        return datetime.fromisoformat(f"{value[:-1]}+00:00")
    except OverflowError, ValueError:
        _add_issue(
            issues,
            _issue(
                "MANIFEST_TIMESTAMP_INVALID",
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
    path: str,
    issues: list[ManifestValidationIssue],
) -> datetime | None:
    parsed = _timestamp(value, path=path, issues=issues)
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
                path,
                "timestamp is outside the case revision interval",
            ),
        )
    return parsed


def _check_box(
    box: Mapping[str, Any],
    *,
    path: str,
    issues: list[ManifestValidationIssue],
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
                path,
                "normalized box extends beyond the page",
            ),
        )


def _check_known_page_list(
    page_ids: Sequence[str],
    known_pages: Mapping[str, Mapping[str, Any]],
    *,
    path: str,
    issues: list[ManifestValidationIssue],
) -> None:
    for index, page_id in enumerate(page_ids):
        if page_id not in known_pages:
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_PAGE_REFERENCE_UNKNOWN",
                    f"{path}[{index}]",
                    "page reference does not resolve",
                ),
            )


def _check_introduced_page_list(
    page_ids: Sequence[str],
    known_pages: Mapping[str, Mapping[str, Any]],
    introduced_page_ids: set[str],
    *,
    path: str,
    issues: list[ManifestValidationIssue],
) -> None:
    for index, page_id in enumerate(page_ids):
        if page_id in known_pages and page_id not in introduced_page_ids:
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_OPERATION_PAGE_NOT_INTRODUCED",
                    f"{path}[{index}]",
                    "page operation precedes the page's first import",
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
    issues: list[ManifestValidationIssue],
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
                    path,
                    "output name exceeds the Windows-safe UTF-16 component limit",
                ),
            )
        if name != unicodedata.normalize("NFC", name):
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_OUTPUT_NAME_NONCANONICAL",
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
                    path,
                    "output name uses a reserved Windows basename",
                ),
            )
        if normalized in normalized_names:
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_OUTPUT_NAME_COLLISION",
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


def _check_exact_lexical_tokens(
    value: object,
    *,
    path: str,
    issues: list[ManifestValidationIssue],
) -> None:
    """Enforce exact v1 token shapes without changing released JSON Schema semantics."""

    if isinstance(value, Mapping):
        for key, member in value.items():
            child_path = _child_path(path, key)
            pattern: re.Pattern[str] | None = None
            if key in _UUID_FIELDS:
                pattern = _UUID_V4_EXACT
            elif key == "sha256":
                pattern = _SHA256_EXACT
            elif key in _TOKEN_FIELDS:
                pattern = _TOKEN_EXACT
            elif key == "template_version":
                pattern = _SEMVER_EXACT
            elif key == "storage_name" and member != "export-history.json":
                pattern = _STORAGE_NAME_EXACT
            elif key in {"color", "fill_color", "stroke_color"}:
                pattern = _RGBA_EXACT
            if type(member) is str and pattern is not None and pattern.fullmatch(member) is None:
                _add_issue(
                    issues,
                    _issue(
                        "MANIFEST_EXACT_TOKEN_INVALID",
                        child_path,
                        "token must match its complete canonical lexical form",
                    ),
                )
            if key in _UUID_LIST_FIELDS and type(member) is list:
                for index, item in enumerate(member):
                    if type(item) is str and _UUID_V4_EXACT.fullmatch(item) is None:
                        _add_issue(
                            issues,
                            _issue(
                                "MANIFEST_EXACT_TOKEN_INVALID",
                                f"{child_path}[{index}]",
                                "identifier must match its complete canonical lexical form",
                            ),
                        )
            _check_exact_lexical_tokens(member, path=child_path, issues=issues)
    elif type(value) is list:
        for index, member in enumerate(value):
            _check_exact_lexical_tokens(
                member,
                path=f"{path}[{index}]",
                issues=issues,
            )


def _semantic_issues(data: Mapping[str, Any]) -> tuple[ManifestValidationIssue, ...]:
    """Return the complete shared manifest-v1/v2 reference and replay diagnostics."""

    issues: list[ManifestValidationIssue] = []
    _check_exact_lexical_tokens(data, path="$", issues=issues)
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
        issues=issues,
    )
    page_index = _index_records(
        pages,
        id_field="page_id",
        collection_path="$.pages",
        duplicate_code="MANIFEST_PAGE_ID_DUPLICATE",
        issues=issues,
    )
    operation_index = _index_records(
        operations,
        id_field="operation_id",
        collection_path="$.page_operations",
        duplicate_code="MANIFEST_OPERATION_ID_DUPLICATE",
        issues=issues,
    )
    asset_index = _index_records(
        assets,
        id_field="asset_id",
        collection_path="$.assets",
        duplicate_code="MANIFEST_ASSET_ID_DUPLICATE",
        issues=issues,
    )
    overlay_index = _index_records(
        overlays,
        id_field="overlay_id",
        collection_path="$.overlays",
        duplicate_code="MANIFEST_OVERLAY_ID_DUPLICATE",
        issues=issues,
    )
    assignment_index = _index_records(
        assignments,
        id_field="assignment_id",
        collection_path="$.assignments",
        duplicate_code="MANIFEST_ASSIGNMENT_ID_DUPLICATE",
        issues=issues,
    )
    confirmation_index = _index_records(
        confirmations,
        id_field="confirmation_id",
        collection_path="$.confirmations",
        duplicate_code="MANIFEST_CONFIRMATION_ID_DUPLICATE",
        issues=issues,
    )
    export_index = _index_records(
        exports,
        id_field="export_id",
        collection_path="$.export_history.entries",
        duplicate_code="MANIFEST_EXPORT_ID_DUPLICATE",
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
            issues=issues,
        )

    created_at = _timestamp(
        cast(str, data["created_at"]),
        path="$.created_at",
        issues=issues,
    )
    updated_at = _timestamp(
        cast(str, data["updated_at"]),
        path="$.updated_at",
        issues=issues,
    )
    if created_at is not None and updated_at is not None and updated_at < created_at:
        _add_issue(
            issues,
            _issue(
                "MANIFEST_TIMESTAMP_ORDER_INVALID",
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
                    f"$.sources[{index}].display_name",
                    "display name contains unsafe Unicode controls or surrogate code points",
                ),
            )
        if display_name != unicodedata.normalize("NFC", display_name):
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_DISPLAY_NAME_NONCANONICAL",
                    f"$.sources[{index}].display_name",
                    "display name must use canonical Unicode normalization",
                ),
            )
        if storage_name != f"{source_id}.bin":
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_STORAGE_NAME_ID_MISMATCH",
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
                    f"$.sources[{index}]",
                    "inspection states do not match the detected media type",
                ),
            )
        _check_time_within_case(
            cast(str, source["imported_at"]),
            created_at=created_at,
            updated_at=updated_at,
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
                    "$.pages",
                    "source page catalogue is incomplete",
                ),
            )

    base_sequence = cast(list[str], data["page_sequence_base"])
    final_sequence = cast(list[str], data["page_sequence"])
    _check_known_page_list(
        base_sequence,
        page_index,
        path="$.page_sequence_base",
        issues=issues,
    )
    _check_known_page_list(
        final_sequence,
        page_index,
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
                    f"$.page_operations[{index}].sequence",
                    "operation sequence must be contiguous and match array order",
                ),
            )
        payload = cast(Mapping[str, Any], operation["payload"])
        kind = cast(str, payload["kind"])
        if kind in {
            "rotate",
            "crop",
            "deskew",
            "contrast",
            "background_cleanup",
        }:
            page_id = cast(str, payload["page_id"])
            if page_id not in page_index:
                _add_issue(
                    issues,
                    _issue(
                        "MANIFEST_PAGE_REFERENCE_UNKNOWN",
                        f"$.page_operations[{index}].payload.page_id",
                        "page reference does not resolve",
                    ),
                )
            elif page_id not in introduced_page_ids:
                _add_issue(
                    issues,
                    _issue(
                        "MANIFEST_OPERATION_PAGE_NOT_INTRODUCED",
                        f"$.page_operations[{index}].payload.page_id",
                        "page operation precedes the page's first import",
                    ),
                )
            if kind == "crop":
                _check_box(
                    cast(Mapping[str, Any], payload["box"]),
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
                path=f"$.page_operations[{index}].payload.before_page_ids",
                issues=issues,
            )
            _check_known_page_list(
                after,
                page_index,
                path=f"$.page_operations[{index}].payload.after_page_ids",
                issues=issues,
            )
            if before != replay_sequence:
                _add_issue(
                    issues,
                    _issue(
                        "MANIFEST_SEQUENCE_REPLAY_MISMATCH",
                        f"$.page_operations[{index}].payload.before_page_ids",
                        "operation baseline does not match replayed sequence",
                    ),
                )
            if not _sequence_reason_matches(reason, before, after, introduced_page_ids):
                _add_issue(
                    issues,
                    _issue(
                        "MANIFEST_SEQUENCE_REASON_INVALID",
                        f"$.page_operations[{index}].payload.reason",
                        "sequence transition does not match its declared reason",
                    ),
                )
            if reason == "import":
                introduced_page_ids.update(set(after) - set(before))
            replay_sequence = list(after)
        elif kind == "set_group":
            group_page_ids = cast(list[str], payload["page_ids"])
            _check_known_page_list(
                group_page_ids,
                page_index,
                path=f"$.page_operations[{index}].payload.page_ids",
                issues=issues,
            )
            _check_introduced_page_list(
                group_page_ids,
                page_index,
                introduced_page_ids,
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
                "$.page_sequence",
                "final page sequence does not match replayed operations",
            ),
        )
    if introduced_page_ids != set(page_index):
        _add_issue(
            issues,
            _issue(
                "MANIFEST_PAGE_INTRODUCTION_INCOMPLETE",
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
                    f"$.overlays[{index}].page_id",
                    "page reference does not resolve",
                ),
            )
        _check_box(
            cast(Mapping[str, Any], overlay["box"]),
            path=f"$.overlays[{index}].box",
            issues=issues,
        )
        z_key = (page_id, cast(int, overlay["z_index"]))
        if z_key in z_indices:
            _add_issue(
                issues,
                _issue(
                    "MANIFEST_OVERLAY_Z_INDEX_COLLISION",
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
                        f"$.overlays[{index}].payload.asset_id",
                        "asset reference does not resolve",
                    ),
                )
            elif resolved_asset["kind"] != "signature_image":
                _add_issue(
                    issues,
                    _issue(
                        "MANIFEST_ASSET_REFERENCE_INCOMPATIBLE",
                        f"$.overlays[{index}].payload.asset_id",
                        "asset type is incompatible with this overlay",
                    ),
                )

    assignment_times: dict[str, datetime | None] = {}
    for index, assignment in enumerate(assignments):
        _check_known_page_list(
            cast(list[str], assignment["page_ids"]),
            page_index,
            path=f"$.assignments[{index}].page_ids",
            issues=issues,
        )
        assignment_id = cast(str, assignment["assignment_id"])
        assignment_times[assignment_id] = _check_time_within_case(
            cast(str, assignment["assigned_at"]),
            created_at=created_at,
            updated_at=updated_at,
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
                        f"$.assignments[{index}].confirmation_id",
                        "assignment confirmation predates the assignment",
                    ),
                )

    _check_output_names(cast(Mapping[str, Any], data["constraints"]), issues=issues)
    return _sorted_issues(issues)


def validate_manifest(
    data: Mapping[str, object],
    schema: Mapping[str, object],
) -> tuple[ManifestValidationIssue, ...]:
    """Validate decoded manifest-v1/v2 data without mutation, I/O, or disclosure.

    Version dispatch happens before schema execution so unknown future versions
    fail closed without interpreting or traversing their content. Released v1
    remains independently valid only when paired with its exact frozen schema;
    current persisted state uses v2.
    """

    version = data.get("schema_version")
    if type(version) is not int:
        return (
            _issue(
                "MANIFEST_VERSION_INVALID",
                "$.schema_version",
                "schema version must be an integer",
            ),
        )
    expected_fingerprint = _SCHEMA_FINGERPRINTS_BY_VERSION.get(version)
    if expected_fingerprint is None:
        return (
            _issue(
                "MANIFEST_VERSION_UNSUPPORTED",
                "$.schema_version",
                "schema version is not supported",
            ),
        )
    if _schema_fingerprint(schema) != expected_fingerprint:
        return (
            _issue(
                "MANIFEST_SCHEMA_IDENTITY_MISMATCH",
                "$",
                "schema does not match the reviewed canonical contract",
            ),
        )
    try:
        schema_issues = _SchemaSubsetValidator(schema).validate(data)
    except Exception:
        return (
            _issue(
                "MANIFEST_SCHEMA_EXECUTION_INVALID",
                "$",
                "schema validation could not complete safely",
            ),
        )
    if schema_issues:
        return schema_issues
    try:
        return _semantic_issues(cast(Mapping[str, Any], data))
    except Exception:
        return (
            _issue(
                "MANIFEST_SEMANTIC_EXECUTION_INVALID",
                "$",
                "semantic validation could not complete safely",
            ),
        )

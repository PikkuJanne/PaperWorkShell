"""Standard-library runtime validation for the frozen declarative template v1 contract."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from importlib.resources import files
from typing import Final, Never, cast
from urllib.parse import urlsplit

from paperworkshell.templates.codec import decode_template_bytes
from paperworkshell.templates.models import (
    SUPPORTED_TEMPLATE_SCHEMA_VERSION,
    TemplateFreshness,
    TemplateLoadError,
    TemplateLoadErrorCode,
    reject_template,
)

CANONICAL_TEMPLATE_SCHEMA_FINGERPRINT: Final = (
    "dd156071a2ec546f6fafa663168b49b93cf8298e782b4d2d21564eab3b38a06f"
)
_MAX_VALIDATION_STEPS: Final = 250_000
_MAX_SCHEMA_RECURSION: Final = 96
_SAFE_MEMBER: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_INVALID_POINTER_ESCAPE: Final = re.compile(r"~(?:[^01]|$)")

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


class _SchemaExecutionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TemplateValidationResult:
    """Validated lifecycle state with no template-controlled text in its representation."""

    verified_on: date
    review_due_on: date
    expires_on: date
    freshness: TemplateFreshness


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


def runtime_template_schema_fingerprint() -> str:
    """Return the authenticated packaged v1 schema fingerprint.

    Loading the resource is intentionally part of this call: a missing or substituted
    package resource fails closed instead of exposing an unauthenticated value.
    """

    fingerprint = _schema_fingerprint(_runtime_schema())
    if fingerprint != CANONICAL_TEMPLATE_SCHEMA_FINGERPRINT:
        reject_template(
            TemplateLoadErrorCode.SCHEMA_IDENTITY_MISMATCH,
            "template.schema_identity",
        )
    return fingerprint


@lru_cache(maxsize=1)
def _runtime_schema() -> dict[str, object]:
    schema: dict[str, object] | None = None
    resource_failed = False
    try:
        resource = files("paperworkshell.templates").joinpath("template-v1.schema.json")
        payload = resource.read_bytes()
        schema = decode_template_bytes(payload)
    except Exception:
        resource_failed = True
    if resource_failed or schema is None:
        # Do not retain a resource/parser exception as the sanitized error's
        # implicit context; packaged schema bytes must never enter tracebacks.
        reject_template(
            TemplateLoadErrorCode.SCHEMA_IDENTITY_MISMATCH,
            "template.schema_resource",
        )
    if _schema_fingerprint(schema) != CANONICAL_TEMPLATE_SCHEMA_FINGERPRINT:
        reject_template(
            TemplateLoadErrorCode.SCHEMA_IDENTITY_MISMATCH,
            "template.schema_identity",
        )
    return schema


class _SchemaMatcher:
    """Bounded boolean interpreter for the authenticated template-v1 keyword subset."""

    def __init__(self, root: Mapping[str, object]) -> None:
        self._root = root
        self._steps = 0

    def matches(self, value: object) -> bool:
        return self._matches(value, self._root, depth=0, active_refs=set())

    def _step(self, depth: int) -> None:
        self._steps += 1
        if self._steps > _MAX_VALIDATION_STEPS or depth > _MAX_SCHEMA_RECURSION:
            raise _SchemaExecutionError

    def _reference_target(self, reference: object) -> Mapping[str, object] | bool:
        if type(reference) is not str or not reference.startswith("#/"):
            raise _SchemaExecutionError
        current: object = self._root
        for encoded in reference[2:].split("/"):
            if _INVALID_POINTER_ESCAPE.search(encoded) is not None:
                raise _SchemaExecutionError
            part = encoded.replace("~1", "/").replace("~0", "~")
            if not isinstance(current, Mapping) or part not in current:
                raise _SchemaExecutionError
            current = current[part]
        if type(current) is bool or isinstance(current, Mapping):
            return current
        raise _SchemaExecutionError

    def _matches(
        self,
        value: object,
        raw_schema: object,
        *,
        depth: int,
        active_refs: set[str],
    ) -> bool:
        self._step(depth)
        if type(raw_schema) is bool:
            return raw_schema
        if not isinstance(raw_schema, Mapping):
            raise _SchemaExecutionError
        schema = cast(Mapping[str, object], raw_schema)

        reference = schema.get("$ref")
        if reference is not None:
            if type(reference) is not str or reference in active_refs:
                raise _SchemaExecutionError
            active_refs.add(reference)
            try:
                if not self._matches(
                    value,
                    self._reference_target(reference),
                    depth=depth + 1,
                    active_refs=active_refs,
                ):
                    return False
            finally:
                active_refs.remove(reference)

        expected_type = schema.get("type")
        if expected_type is not None and not _matches_type(value, expected_type):
            return False
        if "const" in schema and not _json_equal(value, schema["const"]):
            return False
        enum = schema.get("enum")
        if enum is not None:
            if type(enum) is not list or not enum:
                raise _SchemaExecutionError
            if not any(_json_equal(value, candidate) for candidate in enum):
                return False

        if type(value) is str and not self._matches_string(value, schema):
            return False
        if type(value) is int and type(value) is not bool and not _matches_integer(value, schema):
            return False
        if type(value) is list and not self._matches_array(
            value,
            schema,
            depth=depth,
            active_refs=active_refs,
        ):
            return False
        if type(value) is dict and not self._matches_object(
            value,
            schema,
            depth=depth,
            active_refs=active_refs,
        ):
            return False

        one_of = schema.get("oneOf")
        if one_of is not None:
            if type(one_of) is not list or not one_of:
                raise _SchemaExecutionError
            matches = sum(
                self._matches(
                    value,
                    branch,
                    depth=depth + 1,
                    active_refs=set(active_refs),
                )
                for branch in one_of
            )
            if matches != 1:
                return False
        prohibited = schema.get("not")
        if prohibited is not None and self._matches(
            value,
            prohibited,
            depth=depth + 1,
            active_refs=set(active_refs),
        ):
            return False
        return True

    def _matches_string(self, value: str, schema: Mapping[str, object]) -> bool:
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if minimum is not None:
            if type(minimum) is not int or minimum < 0:
                raise _SchemaExecutionError
            if len(value) < minimum:
                return False
        if maximum is not None:
            if type(maximum) is not int or maximum < 0:
                raise _SchemaExecutionError
            if len(value) > maximum:
                return False
        pattern = schema.get("pattern")
        if pattern is not None:
            if type(pattern) is not str:
                raise _SchemaExecutionError
            try:
                if re.search(pattern, value) is None:
                    return False
            except re.error as error:
                raise _SchemaExecutionError from error
        format_name = schema.get("format")
        if format_name == "date":
            return _valid_date(value)
        if format_name == "uri":
            return _valid_uri(value)
        if format_name is not None:
            raise _SchemaExecutionError
        return True

    def _matches_array(
        self,
        value: list[object],
        schema: Mapping[str, object],
        *,
        depth: int,
        active_refs: set[str],
    ) -> bool:
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if minimum is not None:
            if type(minimum) is not int or minimum < 0:
                raise _SchemaExecutionError
            if len(value) < minimum:
                return False
        if maximum is not None:
            if type(maximum) is not int or maximum < 0:
                raise _SchemaExecutionError
            if len(value) > maximum:
                return False
        unique = schema.get("uniqueItems")
        if unique is True:
            keys = [_json_equality_key(item) for item in value]
            if len(keys) != len(set(keys)):
                return False
        elif unique not in {None, False}:
            raise _SchemaExecutionError
        item_schema = schema.get("items")
        return item_schema is None or all(
            self._matches(
                item,
                item_schema,
                depth=depth + 1,
                active_refs=set(active_refs),
            )
            for item in value
        )

    def _matches_object(
        self,
        value: dict[str, object],
        schema: Mapping[str, object],
        *,
        depth: int,
        active_refs: set[str],
    ) -> bool:
        raw_properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(raw_properties, Mapping) or type(required) is not list:
            raise _SchemaExecutionError
        if not all(type(member) is str for member in required):
            raise _SchemaExecutionError
        properties = cast(Mapping[str, object], raw_properties)
        if any(member not in value for member in required):
            return False
        for member, child in value.items():
            if type(member) is not str:
                return False
            child_schema = properties.get(member)
            if child_schema is not None:
                if not self._matches(
                    child,
                    child_schema,
                    depth=depth + 1,
                    active_refs=set(active_refs),
                ):
                    return False
                continue
            additional = schema.get("additionalProperties", True)
            if additional is False:
                return False
            if isinstance(additional, Mapping) and not self._matches(
                child,
                additional,
                depth=depth + 1,
                active_refs=set(active_refs),
            ):
                return False
            if type(additional) is not bool and not isinstance(additional, Mapping):
                raise _SchemaExecutionError
        return True


def _matches_type(value: object, expected: object) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return type(value) is bool
    if expected == "integer":
        return type(value) is int
    if expected == "string":
        return type(value) is str
    if expected == "array":
        return type(value) is list
    if expected == "object":
        return type(value) is dict
    raise _SchemaExecutionError


def _json_equal(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    return bool(left == right)


def _json_equality_key(value: object) -> str:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (RecursionError, TypeError, ValueError) as error:
        raise _SchemaExecutionError from error


def _matches_integer(value: int, schema: Mapping[str, object]) -> bool:
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if minimum is not None and (type(minimum) is not int or value < minimum):
        return False
    if maximum is not None and (type(maximum) is not int or value > maximum):
        return False
    return True


def _valid_date(value: str) -> bool:
    if len(value) != 10:
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _valid_uri(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return bool(parsed.scheme and parsed.netloc)
    except ValueError:
        return False


def _normalized_member_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")


def _reject_prohibited_members(data: Mapping[str, object]) -> None:
    queue: deque[object] = deque([data])
    while queue:
        value = queue.popleft()
        if type(value) is dict:
            for key, child in value.items():
                normalized = _normalized_member_name(key)
                if normalized in _EXECUTABLE_MEMBER_NAMES:
                    reject_template(
                        TemplateLoadErrorCode.EXECUTABLE_MEMBER,
                        "template.member",
                    )
                if normalized in _ELIGIBILITY_MEMBER_NAMES:
                    reject_template(
                        TemplateLoadErrorCode.ELIGIBILITY_MEMBER,
                        "template.member",
                    )
                if normalized in _TRUST_ASSERTION_MEMBER_NAMES:
                    reject_template(
                        TemplateLoadErrorCode.TRUST_ASSERTION_MEMBER,
                        "template.member",
                    )
                queue.append(child)
        elif type(value) is list:
            queue.extend(value)


def _reject_unsafe_text(data: Mapping[str, object]) -> None:
    queue: deque[object] = deque([data])
    while queue:
        value = queue.popleft()
        if type(value) is dict:
            for key, child in value.items():
                if _unsafe_text(key):
                    reject_template(TemplateLoadErrorCode.TEXT_UNSAFE, "template.text")
                queue.append(child)
        elif type(value) is list:
            queue.extend(value)
        elif type(value) is str and _unsafe_text(value):
            reject_template(TemplateLoadErrorCode.TEXT_UNSAFE, "template.text")


def _unsafe_text(value: str) -> bool:
    return unicodedata.normalize("NFC", value) != value or any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value
    )


def validate_template_data(
    data: Mapping[str, object],
    *,
    as_of: date,
) -> TemplateValidationResult:
    """Validate one decoded template without mutation, execution, or dereferencing."""

    if type(as_of) is not date:
        reject_template(TemplateLoadErrorCode.MODEL_INVALID, "template.as_of")
    version = data.get("schema_version")
    if type(version) is not int:
        reject_template(TemplateLoadErrorCode.VERSION_INVALID, "template.schema_version")
    if version != SUPPORTED_TEMPLATE_SCHEMA_VERSION:
        reject_template(TemplateLoadErrorCode.VERSION_UNSUPPORTED, "template.schema_version")
    _reject_prohibited_members(data)
    _reject_unsafe_text(data)
    schema = _runtime_schema()
    schema_matches = False
    schema_execution_failed = False
    try:
        schema_matches = _SchemaMatcher(schema).matches(data)
    except TemplateLoadError:
        raise
    except Exception:
        schema_execution_failed = True
    if schema_execution_failed or not schema_matches:
        reject_template(TemplateLoadErrorCode.SCHEMA_INVALID, "template.schema")
    return _validate_semantics(data, as_of=as_of)


def _validate_semantics(
    data: Mapping[str, object],
    *,
    as_of: date,
) -> TemplateValidationResult:
    try:
        return _validate_semantics_checked(data, as_of=as_of)
    except TemplateLoadError:
        raise
    except Exception:
        pass
    # Raise after leaving the unexpected exception handler so values embedded in
    # KeyError/ValueError/etc. cannot survive as an implicit exception context.
    _reject_semantic()


def _validate_semantics_checked(
    data: Mapping[str, object],
    *,
    as_of: date,
) -> TemplateValidationResult:
    """Apply v1 cross-field rules; the public wrapper sanitizes unexpected faults."""

    instructions = cast(list[dict[str, object]], data["instructions"])
    requirements = cast(list[dict[str, object]], data["requirements"])
    warnings = cast(list[dict[str, object]], data["warnings"])
    confirmations = cast(list[dict[str, object]], data["confirmations"])
    namespace: set[str] = set()
    _register_ids(instructions, "instruction_id", namespace)
    requirement_ids = _register_ids(requirements, "requirement_id", namespace)
    _register_ids(warnings, "warning_id", namespace)
    _register_ids(confirmations, "confirmation_id", namespace)

    requirement_order = cast(list[str], data["requirement_order"])
    if (
        len(requirement_order) != len(set(requirement_order))
        or set(requirement_order) != requirement_ids
    ):
        _reject_semantic()
    for requirement in requirements:
        count = cast(dict[str, object], requirement["count"])
        if cast(int, count["minimum"]) > cast(int, count["maximum"]):
            _reject_semantic()
    for records in (warnings, confirmations):
        for record in records:
            scope = cast(dict[str, object], record["scope"])
            if scope["kind"] == "requirement" and scope["requirement_id"] not in requirement_ids:
                _reject_semantic()

    output = cast(dict[str, object], data["output"])
    allowed_modes = cast(list[str], output["allowed_modes"])
    if len(allowed_modes) != len(set(allowed_modes)) or output["default_mode"] not in allowed_modes:
        _reject_semantic()
    limits = cast(dict[str, object], output["limits"])
    if cast(int, limits["maximum_file_bytes"]) > cast(int, limits["maximum_total_bytes"]):
        _reject_semantic()
    if "separate_pdfs" in allowed_modes and cast(int, limits["maximum_file_count"]) < len(
        requirement_ids
    ):
        _reject_semantic()

    filename_policy = cast(dict[str, object], output["filename_policy"])
    package_stem = cast(str, filename_policy["package_stem"])
    if not _safe_filename_stem(package_stem):
        _reject_semantic()
    stems = cast(list[dict[str, object]], filename_policy["requirement_stems"])
    seen_requirements: set[str] = set()
    seen_stems = {package_stem.casefold()}
    for record in stems:
        requirement_id = cast(str, record["requirement_id"])
        stem = cast(str, record["stem"])
        if (
            requirement_id not in requirement_ids
            or requirement_id in seen_requirements
            or not _safe_filename_stem(stem)
            or stem.casefold() in seen_stems
        ):
            _reject_semantic()
        seen_requirements.add(requirement_id)
        seen_stems.add(stem.casefold())
    if "separate_pdfs" in allowed_modes:
        if seen_requirements != requirement_ids:
            _reject_semantic()
    elif stems:
        _reject_semantic()

    lifecycle = cast(dict[str, object], data["lifecycle"])
    verified_on = date.fromisoformat(cast(str, lifecycle["verified_on"]))
    review_due_on = date.fromisoformat(cast(str, lifecycle["review_due_on"]))
    expires_on = date.fromisoformat(cast(str, lifecycle["expires_on"]))
    if not verified_on <= review_due_on <= expires_on or as_of < verified_on:
        reject_template(
            TemplateLoadErrorCode.LIFECYCLE_INVALID,
            "template.lifecycle",
        )
    if as_of > expires_on:
        reject_template(TemplateLoadErrorCode.EXPIRED, "template.lifecycle")

    provenance = cast(dict[str, object], data["provenance"])
    source_url = cast(str | None, provenance["source_url"])
    if source_url is not None and not _url_is_inert_https(source_url):
        _reject_semantic()
    if (
        provenance["assertion_status"] != "untrusted_embedded_claims"
        or provenance["trust_policy"] != "external_verification_required"
    ):
        _reject_semantic()
    freshness = (
        TemplateFreshness.REVIEW_DUE if as_of >= review_due_on else TemplateFreshness.CURRENT
    )
    return TemplateValidationResult(
        verified_on=verified_on,
        review_due_on=review_due_on,
        expires_on=expires_on,
        freshness=freshness,
    )


def _register_ids(
    records: Sequence[Mapping[str, object]],
    field: str,
    namespace: set[str],
) -> set[str]:
    current: set[str] = set()
    for record in records:
        value = cast(str, record[field])
        if value in current or value in namespace:
            _reject_semantic()
        current.add(value)
        namespace.add(value)
    return current


def _reject_semantic() -> Never:
    reject_template(TemplateLoadErrorCode.SEMANTIC_INVALID, "template.semantic")


def _safe_filename_stem(stem: str) -> bool:
    if not stem or stem in {".", ".."} or stem.endswith((" ", ".")):
        return False
    if unicodedata.normalize("NFC", stem) != stem:
        return False
    if any(character in _WINDOWS_FORBIDDEN_STEM_CHARACTERS for character in stem):
        return False
    if any(unicodedata.category(character).startswith("C") for character in stem):
        return False
    if stem.split(".", maxsplit=1)[0].casefold() in _WINDOWS_RESERVED_BASENAMES:
        return False
    return sum(2 if ord(character) > 0xFFFF else 1 for character in stem) <= 120


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

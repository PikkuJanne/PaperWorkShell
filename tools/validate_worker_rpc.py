"""Validate the PaperWorkShell worker-RPC v1 contract and fixtures."""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, NoReturn, cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
RPC_SCHEMA_PATH: Final = Path("schemas/worker-rpc-v1.schema.json")
RPC_EXAMPLE_DIRECTORY: Final = Path("schemas/examples/worker-rpc/v1")
RPC_INVALID_FIXTURE_DIRECTORY: Final = Path("tests/fixtures/worker-rpc/v1/invalid")
SUPPORTED_PROTOCOL_VERSION: Final = 1
PROTOCOL_NAME: Final = "paperworkshell.worker-rpc"
SUPPORTED_MESSAGE_VERSION: Final = 1
DRAFT_2020_12_DIALECT: Final = "https://json-schema.org/draft/2020-12/schema"

MAX_MESSAGE_BYTES: Final = 65_536
MAX_SCHEMA_BYTES: Final = 2 * 1024 * 1024
MAX_JSON_DEPTH: Final = 32
MAX_JSON_NODES: Final = 4_096
MAX_JSON_STRING_CODEPOINTS: Final = 4_096
MAX_JSON_COLLECTION_ITEMS: Final = 256
MAX_FIXTURE_FILES: Final = 256
MAX_REPORTED_ISSUES: Final = 512
MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991

_KNOWN_MESSAGE_TYPES: Final = frozenset(
    {
        "session.hello",
        "session.ready",
        "operation.start",
        "operation.accepted",
        "operation.progress",
        "operation.cancel",
        "operation.cancel_ack",
        "operation.result",
        "session.shutdown",
        "session.closed",
        "protocol.error",
        "worker.fault",
    }
)
_CLIENT_TO_WORKER_TYPES: Final = frozenset(
    {
        "session.hello",
        "operation.start",
        "operation.cancel",
        "session.shutdown",
    }
)
_WORKER_TO_CLIENT_TYPES: Final = frozenset(
    {
        "session.ready",
        "operation.accepted",
        "operation.progress",
        "operation.cancel_ack",
        "operation.result",
        "session.closed",
        "worker.fault",
    }
)
_SESSION_MESSAGE_TYPES: Final = frozenset(
    {"session.hello", "session.ready", "session.shutdown", "session.closed"}
)
_OPERATION_MESSAGE_TYPES: Final = frozenset(
    {
        "operation.start",
        "operation.accepted",
        "operation.progress",
        "operation.cancel",
        "operation.cancel_ack",
        "operation.result",
    }
)
_UUID_MEMBER_NAMES: Final = frozenset(
    {
        "case_id",
        "message_id",
        "object_id",
        "operation_id",
        "request_id",
        "session_id",
        "worker_instance_id",
    }
)
_SESSION_CAPABILITY: Final = re.compile(r"^[0-9a-f]{64}$")
_ADVERTISED_OPERATION_CAPABILITIES: Final = frozenset(
    {
        ("protocol.noop", 1),
        ("pdf.inspect", 2),
        ("pdf.thumbnail", 1),
        ("pdf.export", 1),
        ("pdf.export", 2),
        ("pdf.export.searchable", 1),
        ("pdf.image_stage", 1),
        ("image.normalize", 1),
        ("image.transform", 1),
        ("ocr.process", 1),
    }
)
_OFFLINE_START_CAPABILITIES: Final = frozenset(
    {
        ("protocol.noop", 1),
        ("image.normalize", 1),
        ("image.transform", 1),
        ("ocr.process", 1),
        ("pdf.export.searchable", 1),
        ("pdf.image_stage", 1),
    }
)

_SAFE_PATH_TOKEN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INVALID_JSON_POINTER_ESCAPE: Final = re.compile(r"~(?:[^01]|$)")
_REFERENCE_KEYWORDS: Final = frozenset({"$ref", "$dynamicRef", "$recursiveRef"})
_UNSUPPORTED_SCHEMA_REFERENCE_KEYWORDS: Final = frozenset(
    {"$anchor", "$dynamicAnchor", "$dynamicRef", "$recursiveAnchor", "$recursiveRef"}
)
_PROHIBITED_MEMBER_NAMES: Final = frozenset(
    {
        "absolute_path",
        "arguments",
        "argv",
        "command",
        "command_line",
        "exception",
        "exception_message",
        "external_path",
        "filename",
        "full_path",
        "message",
        "original_filename",
        "path",
        "raw_error",
        "shell",
        "stack",
        "stack_trace",
        "traceback",
    }
)
_SCHEMA_MESSAGES: Final[dict[object, str]] = {
    "additionalProperties": "unknown members are not permitted",
    "anyOf": "value does not match a supported shape",
    "const": "value is not supported",
    "enum": "value is not supported",
    "format": "value has an invalid format",
    "maxItems": "array length is outside the allowed range",
    "maxLength": "text length is outside the allowed range",
    "maxProperties": "object size is outside the allowed range",
    "maximum": "number is outside the allowed range",
    "minItems": "array length is outside the allowed range",
    "minLength": "text length is outside the allowed range",
    "minimum": "number is outside the allowed range",
    "oneOf": "value does not match exactly one supported shape",
    "pattern": "value has an invalid format",
    "required": "a required member is missing",
    "type": "value has an invalid JSON type",
    "uniqueItems": "array members must be unique",
}


@dataclass(frozen=True)
class RpcIssue:
    """One stable, non-disclosing worker-protocol validation error."""

    code: str
    document: str
    path: str
    message: str

    def sort_key(self) -> tuple[str, str, str, str]:
        return (self.document, self.path, self.code, self.message)

    def render(self) -> str:
        return f"ERROR [{self.code}] {self.document}#{self.path}: {self.message}"


@dataclass(frozen=True)
class RpcFixtureSummary:
    """Counts for a successfully validated worker-RPC fixture set."""

    positive_count: int
    negative_count: int


class DuplicateJsonKeyError(ValueError):
    """Raised when a JSON object repeats a member name."""


class NonStandardJsonConstantError(ValueError):
    """Raised for NaN and Infinity, which are not standard JSON."""


class InvalidJsonNumberError(ValueError):
    """Raised for floating-point or out-of-safe-range JSON numbers."""


def _issue(code: str, document: str, path: str, message: str) -> RpcIssue:
    return RpcIssue(code=code, document=document, path=path, message=message)


def _add_issue(issues: list[RpcIssue], issue: RpcIssue) -> None:
    if len(issues) < MAX_REPORTED_ISSUES - 1:
        issues.append(issue)
        return
    if not any(existing.code == "RPC_ISSUE_LIMIT" for existing in issues):
        issues.append(
            _issue(
                "RPC_ISSUE_LIMIT",
                issue.document,
                "$",
                "additional validation errors were suppressed",
            )
        )


def _sorted_issues(issues: Iterable[RpcIssue]) -> list[RpcIssue]:
    return sorted(issues, key=RpcIssue.sort_key)


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
        return "external-worker-rpc.json"


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
) -> tuple[str | None, list[RpcIssue]]:
    try:
        with path.open("rb") as source:
            content = source.read(maximum_bytes + 1)
    except OSError as error:
        return None, [_issue("RPC_INPUT_UNREADABLE", document, "$", type(error).__name__)]
    if len(content) > maximum_bytes:
        return None, [
            _issue(
                "RPC_INPUT_TOO_LARGE",
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
                "RPC_TEXT_ENCODING_INVALID",
                document,
                "$",
                "input must be valid UTF-8",
            )
        ]


def _json_resource_issue(loaded: Mapping[str, Any], document: str) -> RpcIssue | None:
    stack: list[tuple[object, int]] = [(loaded, 1)]
    node_count = 0
    while stack:
        value, depth = stack.pop()
        node_count += 1
        if node_count > MAX_JSON_NODES:
            return _issue(
                "RPC_JSON_RESOURCE_LIMIT",
                document,
                "$",
                "JSON input exceeds its structural node limit",
            )
        if depth > MAX_JSON_DEPTH:
            return _issue(
                "RPC_JSON_RESOURCE_LIMIT",
                document,
                "$",
                "JSON input exceeds its nesting limit",
            )
        if isinstance(value, Mapping):
            if len(value) > MAX_JSON_COLLECTION_ITEMS:
                return _issue(
                    "RPC_JSON_RESOURCE_LIMIT",
                    document,
                    "$",
                    "JSON object exceeds its member limit",
                )
            for key, child in value.items():
                if len(key) > MAX_JSON_STRING_CODEPOINTS:
                    return _issue(
                        "RPC_JSON_RESOURCE_LIMIT",
                        document,
                        "$",
                        "JSON text exceeds its string limit",
                    )
                node_count += 1
                if node_count > MAX_JSON_NODES:
                    return _issue(
                        "RPC_JSON_RESOURCE_LIMIT",
                        document,
                        "$",
                        "JSON input exceeds its structural node limit",
                    )
                stack.append((child, depth + 1))
        elif isinstance(value, list):
            if len(value) > MAX_JSON_COLLECTION_ITEMS:
                return _issue(
                    "RPC_JSON_RESOURCE_LIMIT",
                    document,
                    "$",
                    "JSON array exceeds its item limit",
                )
            stack.extend((child, depth + 1) for child in value)
        elif isinstance(value, str) and len(value) > MAX_JSON_STRING_CODEPOINTS:
            return _issue(
                "RPC_JSON_RESOURCE_LIMIT",
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
) -> tuple[dict[str, Any] | None, list[RpcIssue]]:
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
                "RPC_JSON_DUPLICATE_KEY",
                document,
                "$",
                "object member names must be unique",
            )
        ]
    except NonStandardJsonConstantError:
        return None, [
            _issue(
                "RPC_JSON_NONSTANDARD_CONSTANT",
                document,
                "$",
                "NaN and Infinity are not permitted",
            )
        ]
    except InvalidJsonNumberError:
        return None, [
            _issue(
                "RPC_JSON_NONSTANDARD_NUMBER",
                document,
                "$",
                "floating-point and out-of-range numbers are not permitted",
            )
        ]
    except json.JSONDecodeError as error:
        return None, [
            _issue(
                "RPC_JSON_INVALID",
                document,
                "$",
                f"invalid JSON at line {error.lineno}, column {error.colno}",
            )
        ]
    except RecursionError, ValueError:
        return None, [
            _issue(
                "RPC_JSON_INVALID",
                document,
                "$",
                "JSON numeric or nesting syntax is invalid",
            )
        ]
    if not isinstance(loaded, dict):
        return None, [
            _issue(
                "RPC_JSON_ROOT_TYPE",
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


def _schema_policy_issues(schema: Mapping[str, Any], document: str) -> list[RpcIssue]:
    issues: list[RpcIssue] = []
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
                            "RPC_SCHEMA_RESOURCE_SCOPE_UNSAFE",
                            document,
                            _json_path(child_path),
                            "nested schema resource and dialect declarations are not permitted",
                        ),
                    )
                if key in _UNSUPPORTED_SCHEMA_REFERENCE_KEYWORDS:
                    _add_issue(
                        issues,
                        _issue(
                            "RPC_SCHEMA_REFERENCE_FEATURE_UNSAFE",
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


def load_worker_rpc_schema(
    root: Path = PROJECT_ROOT,
) -> tuple[dict[str, Any] | None, list[RpcIssue]]:
    """Strictly load and check the self-contained Draft 2020-12 RPC schema."""

    schema, issues = _load_json_object(
        root / RPC_SCHEMA_PATH,
        root=root,
        maximum_bytes=MAX_SCHEMA_BYTES,
    )
    if schema is None:
        return None, issues
    document = RPC_SCHEMA_PATH.as_posix()
    if schema.get("$schema") != DRAFT_2020_12_DIALECT:
        return None, [
            _issue(
                "RPC_SCHEMA_DIALECT_UNSUPPORTED",
                document,
                "$.$schema",
                "schema must declare the supported Draft 2020-12 dialect",
            )
        ]
    policy_issues = _schema_policy_issues(schema, document)
    if policy_issues:
        return None, policy_issues
    reference_issues: list[RpcIssue] = []
    for path, reference in _schema_references(schema):
        if not reference.startswith("#"):
            _add_issue(
                reference_issues,
                _issue(
                    "RPC_SCHEMA_REFERENCE_UNSAFE",
                    document,
                    path,
                    "schema references must remain inside this schema",
                ),
            )
        elif _local_reference_target_path(schema, reference) is None:
            _add_issue(
                reference_issues,
                _issue(
                    "RPC_SCHEMA_REFERENCE_INVALID",
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
                "RPC_SCHEMA_REFERENCE_CYCLE",
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
                "RPC_SCHEMA_DEFINITION_INVALID",
                document,
                _json_path(error.absolute_schema_path),
                "schema definition is invalid",
            )
        ]
    except Exception:
        return None, [
            _issue(
                "RPC_SCHEMA_EXECUTION_INVALID",
                document,
                "$",
                "schema validation could not complete safely",
            )
        ]
    return schema, []


def _schema_validation_issues(
    data: Mapping[str, Any],
    schema: Mapping[str, Any],
    document: str,
) -> list[RpcIssue]:
    issues: list[RpcIssue] = []
    try:
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        for error in validator.iter_errors(data):
            _add_issue(
                issues,
                _issue(
                    "RPC_SCHEMA_VALIDATION",
                    document,
                    _json_path(error.absolute_path),
                    _SCHEMA_MESSAGES.get(error.validator, "value violates the worker protocol"),
                ),
            )
            if any(issue.code == "RPC_ISSUE_LIMIT" for issue in issues):
                break
    except Exception:
        return [
            _issue(
                "RPC_SCHEMA_EXECUTION_INVALID",
                document,
                "$",
                "schema validation could not complete safely",
            )
        ]
    return _sorted_issues(issues)


def _canonical_uuid_v4(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = uuid.UUID(value)
    except ValueError, AttributeError:
        return False
    return parsed.version == 4 and str(parsed) == value


def _walk_members(value: object) -> Iterable[tuple[tuple[object, ...], str, object]]:
    stack: list[tuple[object, tuple[object, ...]]] = [(value, ())]
    while stack:
        current, path = stack.pop()
        if isinstance(current, Mapping):
            for key, child in current.items():
                child_path = (*path, key)
                yield child_path, key, child
                stack.append((child, child_path))
        elif isinstance(current, list):
            stack.extend((child, (*path, index)) for index, child in enumerate(current))


def _predispatch_issues(data: Mapping[str, Any], document: str) -> list[RpcIssue]:
    protocol = data.get("protocol")
    if not isinstance(protocol, str):
        return [
            _issue(
                "RPC_PROTOCOL_INVALID",
                document,
                "$.protocol",
                "protocol identifier must be text",
            )
        ]
    if protocol != PROTOCOL_NAME:
        return [
            _issue(
                "RPC_PROTOCOL_UNSUPPORTED",
                document,
                "$.protocol",
                "protocol identifier is not supported",
            )
        ]

    protocol_version = data.get("protocol_version")
    if type(protocol_version) is not int:
        return [
            _issue(
                "RPC_VERSION_INVALID",
                document,
                "$.protocol_version",
                "protocol version must be an integer",
            )
        ]
    if protocol_version != SUPPORTED_PROTOCOL_VERSION:
        return [
            _issue(
                "RPC_VERSION_UNSUPPORTED",
                document,
                "$.protocol_version",
                "protocol version is not supported",
            )
        ]

    message_version = data.get("message_version")
    if type(message_version) is not int:
        return [
            _issue(
                "RPC_MESSAGE_VERSION_INVALID",
                document,
                "$.message_version",
                "message version must be an integer",
            )
        ]
    if message_version != SUPPORTED_MESSAGE_VERSION:
        return [
            _issue(
                "RPC_MESSAGE_VERSION_UNSUPPORTED",
                document,
                "$.message_version",
                "message version is not supported",
            )
        ]

    message_type = data.get("message_type")
    if not isinstance(message_type, str):
        return [
            _issue(
                "RPC_MESSAGE_TYPE_INVALID",
                document,
                "$.message_type",
                "message type must be text",
            )
        ]
    if message_type not in _KNOWN_MESSAGE_TYPES:
        return [
            _issue(
                "RPC_MESSAGE_TYPE_UNKNOWN",
                document,
                "$.message_type",
                "message type is not supported",
            )
        ]
    return []


def _security_member_issues(data: Mapping[str, Any], document: str) -> list[RpcIssue]:
    issues: list[RpcIssue] = []
    for path, key, value in _walk_members(data):
        prohibited_parameter_name = (
            key == "name" and isinstance(value, str) and value in _PROHIBITED_MEMBER_NAMES
        )
        if key in _PROHIBITED_MEMBER_NAMES or prohibited_parameter_name:
            _add_issue(
                issues,
                _issue(
                    "RPC_PROHIBITED_MEMBER",
                    document,
                    _json_path(path),
                    "protocol messages cannot carry path, command, or raw error members",
                ),
            )
    return _sorted_issues(issues)


def _identifier_issues(data: Mapping[str, Any], document: str) -> list[RpcIssue]:
    issues: list[RpcIssue] = []
    capability = data.get("session_capability")
    if not isinstance(capability, str) or _SESSION_CAPABILITY.fullmatch(capability) is None:
        _add_issue(
            issues,
            _issue(
                "RPC_SESSION_CAPABILITY_INVALID",
                document,
                "$.session_capability",
                "session capability must be lowercase 256-bit hexadecimal text",
            ),
        )
    for path, key, value in _walk_members(data):
        if key in _UUID_MEMBER_NAMES and value is not None and not _canonical_uuid_v4(value):
            _add_issue(
                issues,
                _issue(
                    "RPC_ID_INVALID",
                    document,
                    _json_path(path),
                    "protocol identifiers must be canonical UUIDv4 values",
                ),
            )
        if key == "invalidated_operation_ids" and isinstance(value, list):
            seen: set[str] = set()
            for index, operation_id in enumerate(value):
                item_path = _json_path((*path, index))
                if not _canonical_uuid_v4(operation_id):
                    _add_issue(
                        issues,
                        _issue(
                            "RPC_ID_INVALID",
                            document,
                            item_path,
                            "protocol identifiers must be canonical UUIDv4 values",
                        ),
                    )
                elif operation_id in seen:
                    _add_issue(
                        issues,
                        _issue(
                            "RPC_ID_DUPLICATE",
                            document,
                            item_path,
                            "operation identifiers must be unique",
                        ),
                    )
                else:
                    seen.add(operation_id)
        if (
            key == "value"
            and isinstance(value, str)
            and isinstance(data_at_path := _value_at_path(data, path[:-1]), Mapping)
            and data_at_path.get("value_type") == "uuid"
            and not _canonical_uuid_v4(value)
        ):
            _add_issue(
                issues,
                _issue(
                    "RPC_ID_INVALID",
                    document,
                    _json_path(path),
                    "UUID parameters must use canonical UUIDv4 text",
                ),
            )
    return _sorted_issues(issues)


def _value_at_path(root: object, path: Sequence[object]) -> object:
    current = root
    for part in path:
        if isinstance(current, Mapping) and isinstance(part, str):
            current = current.get(part)
        elif isinstance(current, list) and isinstance(part, int) and part < len(current):
            current = current[part]
        else:
            return None
    return current


def _correlation_issues(data: Mapping[str, Any], document: str) -> list[RpcIssue]:
    issues: list[RpcIssue] = []
    message_type = cast(str, data["message_type"])
    direction = data.get("direction")
    if message_type in _CLIENT_TO_WORKER_TYPES and direction != "client_to_worker":
        _add_issue(
            issues,
            _issue(
                "RPC_DIRECTION_INVALID",
                document,
                "$.direction",
                "message direction does not match its type",
            ),
        )
    elif message_type in _WORKER_TO_CLIENT_TYPES and direction != "worker_to_client":
        _add_issue(
            issues,
            _issue(
                "RPC_DIRECTION_INVALID",
                document,
                "$.direction",
                "message direction does not match its type",
            ),
        )

    request_id = data.get("request_id")
    operation_id = data.get("operation_id")
    if message_type in _SESSION_MESSAGE_TYPES:
        if not _canonical_uuid_v4(request_id) or operation_id is not None:
            _add_issue(
                issues,
                _issue(
                    "RPC_CORRELATION_INVALID",
                    document,
                    "$",
                    "session messages require a request identifier and no operation identifier",
                ),
            )
    elif message_type in _OPERATION_MESSAGE_TYPES:
        if not _canonical_uuid_v4(request_id) or not _canonical_uuid_v4(operation_id):
            _add_issue(
                issues,
                _issue(
                    "RPC_CORRELATION_INVALID",
                    document,
                    "$",
                    "operation messages require request and operation identifiers",
                ),
            )
    elif message_type == "worker.fault":
        if (
            request_id is not None and not _canonical_uuid_v4(request_id)
        ) or operation_id is not None:
            _add_issue(
                issues,
                _issue(
                    "RPC_CORRELATION_INVALID",
                    document,
                    "$",
                    "worker faults allow an optional request identifier and no operation identifier",
                ),
            )
    elif message_type == "protocol.error":
        if (request_id is not None and not _canonical_uuid_v4(request_id)) or (
            operation_id is not None and not _canonical_uuid_v4(operation_id)
        ):
            _add_issue(
                issues,
                _issue(
                    "RPC_CORRELATION_INVALID",
                    document,
                    "$",
                    "protocol errors contain only valid available correlation identifiers",
                ),
            )
    return _sorted_issues(issues)


def _capability_issues(data: Mapping[str, Any], document: str) -> list[RpcIssue]:
    message_type = data["message_type"]
    payload = data.get("payload")
    if not isinstance(payload, Mapping):
        return []
    issues: list[RpcIssue] = []
    if message_type == "session.hello":
        versions = payload.get("supported_protocol_versions")
        if isinstance(versions, list):
            if len(versions) != len({value for value in versions if type(value) is int}):
                _add_issue(
                    issues,
                    _issue(
                        "RPC_NEGOTIATION_INVALID",
                        document,
                        "$.payload.supported_protocol_versions",
                        "supported protocol versions must be unique",
                    ),
                )
            if SUPPORTED_PROTOCOL_VERSION not in versions:
                _add_issue(
                    issues,
                    _issue(
                        "RPC_NEGOTIATION_INVALID",
                        document,
                        "$.payload.supported_protocol_versions",
                        "hello must advertise its envelope protocol version",
                    ),
                )
        capabilities = payload.get("required_capabilities")
        capability_path = "$.payload.required_capabilities"
    elif message_type == "session.ready":
        if payload.get("selected_protocol_version") != SUPPORTED_PROTOCOL_VERSION:
            _add_issue(
                issues,
                _issue(
                    "RPC_NEGOTIATION_INVALID",
                    document,
                    "$.payload.selected_protocol_version",
                    "selected protocol version must match the envelope version",
                ),
            )
        capabilities = payload.get("capabilities")
        capability_path = "$.payload.capabilities"
    else:
        return []

    if isinstance(capabilities, list):
        seen_capabilities: set[tuple[str, int]] = set()
        for index, capability in enumerate(capabilities):
            if not isinstance(capability, Mapping):
                continue
            capability_id = capability.get("capability_id")
            capability_version = capability.get("capability_version")
            if isinstance(capability_id, str) and type(capability_version) is int:
                descriptor = (capability_id, capability_version)
                if descriptor in seen_capabilities:
                    _add_issue(
                        issues,
                        _issue(
                            "RPC_CAPABILITY_DUPLICATE",
                            document,
                            f"{capability_path}[{index}].capability_id",
                            "capability descriptors must be unique",
                        ),
                    )
                seen_capabilities.add(descriptor)
                if (
                    message_type == "session.ready"
                    and descriptor not in _ADVERTISED_OPERATION_CAPABILITIES
                ):
                    _add_issue(
                        issues,
                        _issue(
                            "RPC_OPERATION_UNSUPPORTED",
                            document,
                            f"{capability_path}[{index}].capability_id",
                            "the worker advertised a capability absent from the local registry",
                        ),
                    )
    return _sorted_issues(issues)


def _reference_issues(data: Mapping[str, Any], document: str) -> list[RpcIssue]:
    payload = data.get("payload")
    if not isinstance(payload, Mapping):
        return []
    message_type = data["message_type"]
    if message_type == "operation.start":
        references = payload.get("inputs")
        base_path = "$.payload.inputs"
    elif message_type == "operation.result":
        references = payload.get("outputs")
        base_path = "$.payload.outputs"
    else:
        return []
    if not isinstance(references, list):
        return []

    issues: list[RpcIssue] = []
    envelope_operation_id = data.get("operation_id")
    for index, reference in enumerate(references):
        if not isinstance(reference, Mapping):
            continue
        area = reference.get("area")
        reference_operation_id = reference.get("operation_id")
        path = f"{base_path}[{index}].operation_id"
        if message_type == "operation.start" and area not in {"source_read", "asset_read"}:
            _add_issue(
                issues,
                _issue(
                    "RPC_REFERENCE_AREA_INVALID",
                    document,
                    f"{base_path}[{index}].area",
                    "operation inputs must use read-only source or asset areas",
                ),
            )
        if message_type == "operation.result" and area != "operation_staging":
            _add_issue(
                issues,
                _issue(
                    "RPC_REFERENCE_AREA_INVALID",
                    document,
                    f"{base_path}[{index}].area",
                    "operation outputs must use the staging area",
                ),
            )
        if area in {"source_read", "asset_read"}:
            if reference_operation_id is not None:
                _add_issue(
                    issues,
                    _issue(
                        "RPC_REFERENCE_OPERATION_INVALID",
                        document,
                        path,
                        "source and asset references cannot name an operation",
                    ),
                )
        elif isinstance(area, str) and area.startswith("operation_"):
            if reference_operation_id != envelope_operation_id:
                _add_issue(
                    issues,
                    _issue(
                        "RPC_REFERENCE_OPERATION_INVALID",
                        document,
                        path,
                        "operation-scoped references must match the envelope operation",
                    ),
                )
        if message_type == "operation.result" and (
            reference.get("expected_byte_size") is None or reference.get("expected_sha256") is None
        ):
            _add_issue(
                issues,
                _issue(
                    "RPC_REFERENCE_INTEGRITY_REQUIRED",
                    document,
                    f"{base_path}[{index}]",
                    "operation outputs require expected size and SHA-256 values",
                ),
            )
    return _sorted_issues(issues)


def _operation_registry_issues(data: Mapping[str, Any], document: str) -> list[RpcIssue]:
    message_type = data["message_type"]
    if message_type not in {"operation.start", "operation.accepted"}:
        return []
    payload = data.get("payload")
    if not isinstance(payload, Mapping):
        return []
    issues: list[RpcIssue] = []
    descriptor = (payload.get("operation_kind"), payload.get("operation_version"))
    admitted = (
        _OFFLINE_START_CAPABILITIES
        if message_type == "operation.start"
        else _ADVERTISED_OPERATION_CAPABILITIES
    )
    if descriptor not in admitted:
        _add_issue(
            issues,
            _issue(
                "RPC_OPERATION_UNSUPPORTED",
                document,
                "$.payload.operation_kind",
                "operation kind or version has no exact local validation contract",
            ),
        )
    if message_type == "operation.start" and descriptor == ("protocol.noop", 1):
        if payload.get("inputs") != [] or payload.get("parameters") != []:
            _add_issue(
                issues,
                _issue(
                    "RPC_OPERATION_INPUT_INVALID",
                    document,
                    "$.payload",
                    "protocol.noop does not accept inputs or parameters",
                ),
            )
    elif message_type == "operation.start" and descriptor == ("image.normalize", 1):
        for issue in _image_normalize_start_issues(data, payload, document):
            _add_issue(issues, issue)
    elif message_type == "operation.start" and descriptor == ("image.transform", 1):
        for issue in _image_transform_start_issues(data, payload, document):
            _add_issue(issues, issue)
    elif message_type == "operation.start" and descriptor == ("ocr.process", 1):
        for issue in _ocr_process_start_issues(data, payload, document):
            _add_issue(issues, issue)
    elif message_type == "operation.start" and descriptor == (
        "pdf.export.searchable",
        1,
    ):
        for issue in _searchable_pdf_export_start_issues(data, payload, document):
            _add_issue(issues, issue)
    elif message_type == "operation.start" and descriptor == ("pdf.image_stage", 1):
        for issue in _pdf_image_stage_start_issues(data, payload, document):
            _add_issue(issues, issue)
    return _sorted_issues(issues)


def _pdf_image_stage_start_issues(
    data: Mapping[str, Any], payload: Mapping[str, Any], document: str
) -> list[RpcIssue]:
    """Admit only the new operation's private plan and reduced source authorities."""

    def invalid(path: str, message: str) -> list[RpcIssue]:
        return [_issue("RPC_OPERATION_INPUT_INVALID", document, path, message)]

    inputs = payload.get("inputs")
    if (
        not isinstance(inputs, list)
        or not 2 <= len(inputs) <= MAX_JSON_COLLECTION_ITEMS
        or any(not isinstance(reference, Mapping) for reference in inputs)
    ):
        return invalid("$.payload.inputs", "image stage requires one plan and at least one source")
    plan, *sources = cast(list[Mapping[str, Any]], inputs)
    if (
        plan.get("area") != "source_read"
        or plan.get("operation_id") is not None
        or plan.get("content_kind") != "pdf-image-stage-plan-v1"
        or type(plan.get("expected_byte_size")) is not int
        or not 1 <= cast(int, plan.get("expected_byte_size")) <= 2 * 1024 * 1024
        or not isinstance(plan.get("expected_sha256"), str)
    ):
        return invalid("$.payload.inputs[0]", "image stage requires one authenticated private plan")
    if any(
        source.get("area") != "source_read"
        or source.get("operation_id") is not None
        or source.get("content_kind") not in {"pdf", "jpeg", "png"}
        or type(source.get("expected_byte_size")) is not int
        or cast(int, source.get("expected_byte_size")) < 1
        or not isinstance(source.get("expected_sha256"), str)
        for source in sources
    ):
        return invalid(
            "$.payload.inputs", "image stage requires authenticated PDF or image sources"
        )
    expected_types = {
        "plan_handle": "integer",
        "output_handle": "integer",
        "evidence_handle": "integer",
        "result_handle": "integer",
        "output_object_id": "uuid",
        "evidence_object_id": "uuid",
        "evidence_capacity_bytes": "integer",
    }
    parameters = payload.get("parameters")
    parameter_map: dict[str, Mapping[str, Any]] = {}
    if isinstance(parameters, list):
        for parameter in parameters:
            if isinstance(parameter, Mapping) and isinstance(parameter.get("name"), str):
                parameter_map[parameter["name"]] = parameter
    if (
        not isinstance(parameters, list)
        or len(parameters) != len(expected_types)
        or set(parameter_map) != set(expected_types)
        or any(
            parameter_map[name].get("value_type") != value_type
            for name, value_type in expected_types.items()
            if name in parameter_map
        )
    ):
        return invalid(
            "$.payload.parameters", "image stage requires its exact closed parameter set"
        )
    handles = tuple(
        parameter_map[name].get("value")
        for name in ("plan_handle", "output_handle", "evidence_handle", "result_handle")
    )
    identifiers = (
        *(reference.get("object_id") for reference in inputs),
        parameter_map["output_object_id"].get("value"),
        parameter_map["evidence_object_id"].get("value"),
    )
    capacity = parameter_map["evidence_capacity_bytes"].get("value")
    limits = payload.get("requested_limits")
    if not isinstance(limits, Mapping):
        return invalid("$.payload.requested_limits", "image stage requires bounded resource limits")
    memory = limits.get("memory_bytes")
    temporary = limits.get("temporary_storage_bytes")
    output = limits.get("output_bytes")
    pages = limits.get("page_count")
    if (
        any(type(handle) is not int or handle < 1 for handle in handles)
        or len(set(handles)) != len(handles)
        or any(not _canonical_uuid_v4(identifier) for identifier in identifiers)
        or len(set(identifiers)) != len(identifiers)
        or type(capacity) is not int
        or not 1 <= capacity <= 2 * 1024 * 1024
        or any(type(value) is not int or value < 1 for value in (memory, temporary, output, pages))
    ):
        return invalid(
            "$.payload", "image stage identities, handles and resource limits are invalid"
        )
    if (
        capacity > cast(int, memory) // 8
        or cast(int, pages) > 4_096
        or sum(cast(int, source["expected_byte_size"]) for source in sources)
        + 2 * cast(int, output)
        + capacity
        > cast(int, temporary)
    ):
        return invalid(
            "$.payload.requested_limits", "image stage evidence or storage exceeds its budget"
        )
    return []


def _searchable_pdf_export_start_issues(
    data: Mapping[str, Any],
    payload: Mapping[str, Any],
    document: str,
) -> list[RpcIssue]:
    """Validate the exact additive searchable mixed-output operation."""

    issues: list[RpcIssue] = []
    inputs = payload.get("inputs")
    plan: Mapping[str, Any] | None = None
    sources: list[Mapping[str, Any]] = []
    if isinstance(inputs, list) and 2 <= len(inputs) <= MAX_JSON_COLLECTION_ITEMS:
        if isinstance(inputs[0], Mapping):
            plan = inputs[0]
        sources = [value for value in inputs[1:] if isinstance(value, Mapping)]
    if plan is None or len(sources) != (0 if not isinstance(inputs, list) else len(inputs) - 1):
        _add_issue(
            issues,
            _issue(
                "RPC_OPERATION_INPUT_INVALID",
                document,
                "$.payload.inputs",
                "searchable PDF export requires one plan and at least one source",
            ),
        )
        return _sorted_issues(issues)
    if (
        plan.get("area") != "source_read"
        or plan.get("operation_id") is not None
        or plan.get("content_kind") != "searchable-pdf-export-plan-v1"
        or type(plan.get("expected_byte_size")) is not int
        or not 1 <= cast(int, plan.get("expected_byte_size")) <= 2 * 1024 * 1024
        or not isinstance(plan.get("expected_sha256"), str)
    ):
        _add_issue(
            issues,
            _issue(
                "RPC_OPERATION_INPUT_INVALID",
                document,
                "$.payload.inputs[0]",
                "searchable PDF export requires one authenticated immutable plan",
            ),
        )
    if any(
        source.get("area") != "source_read"
        or source.get("operation_id") is not None
        or source.get("content_kind") not in {"pdf", "jpeg", "png"}
        or type(source.get("expected_byte_size")) is not int
        or cast(int, source.get("expected_byte_size")) < 1
        or not isinstance(source.get("expected_sha256"), str)
        for source in sources
    ):
        _add_issue(
            issues,
            _issue(
                "RPC_OPERATION_INPUT_INVALID",
                document,
                "$.payload.inputs[1:]",
                "searchable PDF export requires authenticated PDF, JPEG, or PNG sources",
            ),
        )

    expected_types = {
        "plan_handle": "integer",
        "output_handle": "integer",
        "status_handle": "integer",
        "result_handle": "integer",
        "output_object_id": "uuid",
        "status_object_id": "uuid",
        "status_capacity_bytes": "integer",
    }
    parameters = payload.get("parameters")
    parameter_map: dict[str, Mapping[str, Any]] = {}
    if isinstance(parameters, list):
        for value in parameters:
            if isinstance(value, Mapping) and isinstance(value.get("name"), str):
                parameter_map[cast(str, value["name"])] = value
    if (
        not isinstance(parameters, list)
        or len(parameters) != len(expected_types)
        or set(parameter_map) != set(expected_types)
        or any(
            parameter_map[name].get("value_type") != value_type
            for name, value_type in expected_types.items()
            if name in parameter_map
        )
    ):
        _add_issue(
            issues,
            _issue(
                "RPC_OPERATION_INPUT_INVALID",
                document,
                "$.payload.parameters",
                "searchable PDF export requires its exact closed parameter set",
            ),
        )
        return _sorted_issues(issues)

    handles = tuple(
        parameter_map[name].get("value")
        for name in ("plan_handle", "output_handle", "status_handle", "result_handle")
    )
    output_id = parameter_map["output_object_id"].get("value")
    status_id = parameter_map["status_object_id"].get("value")
    status_capacity = parameter_map["status_capacity_bytes"].get("value")
    input_ids = tuple(reference.get("object_id") for reference in (plan, *sources))
    correlated_ids = (
        data.get("case_id"),
        data.get("operation_id"),
        *input_ids,
        output_id,
        status_id,
    )
    limits = payload.get("requested_limits")
    page_count = limits.get("page_count") if isinstance(limits, Mapping) else None
    temporary_bytes = limits.get("temporary_storage_bytes") if isinstance(limits, Mapping) else None
    output_bytes = limits.get("output_bytes") if isinstance(limits, Mapping) else None
    source_sizes = tuple(source.get("expected_byte_size") for source in sources)
    source_total = (
        sum(cast(tuple[int, ...], source_sizes))
        if all(type(value) is int and value > 0 for value in source_sizes)
        else None
    )
    if (
        any(type(handle) is not int or handle <= 0 for handle in handles)
        or len(set(handles)) != len(handles)
        or not _canonical_uuid_v4(output_id)
        or not _canonical_uuid_v4(status_id)
        or len(set(correlated_ids)) != len(correlated_ids)
        or type(status_capacity) is not int
        or not 1 <= status_capacity <= 2 * 1024 * 1024
        or type(page_count) is not int
        or not 1 <= page_count <= 4_096
        or type(temporary_bytes) is not int
        or type(output_bytes) is not int
        or output_bytes < 1
        or source_total is None
        or source_total > temporary_bytes
        or output_bytes > temporary_bytes - source_total
        or status_capacity > temporary_bytes - source_total - output_bytes
    ):
        _add_issue(
            issues,
            _issue(
                "RPC_OPERATION_INPUT_INVALID",
                document,
                "$.payload",
                "searchable PDF export identities, handles, capacities, and limits must be bounded",
            ),
        )
    return _sorted_issues(issues)


def _ocr_process_start_issues(
    data: Mapping[str, Any],
    payload: Mapping[str, Any],
    document: str,
) -> list[RpcIssue]:
    """Validate the exact additive OCR operation without widening schema v1."""

    issues: list[RpcIssue] = []
    inputs = payload.get("inputs")
    source: Mapping[str, Any] | None = None
    if isinstance(inputs, list) and len(inputs) == 1 and isinstance(inputs[0], Mapping):
        source = inputs[0]
    if source is None or (
        source.get("area") != "source_read"
        or source.get("operation_id") is not None
        or source.get("content_kind") not in {"pdf", "jpeg", "png"}
        or type(source.get("expected_byte_size")) is not int
        or cast(int, source.get("expected_byte_size")) < 1
        or not isinstance(source.get("expected_sha256"), str)
    ):
        _add_issue(
            issues,
            _issue(
                "RPC_OPERATION_INPUT_INVALID",
                document,
                "$.payload.inputs",
                "ocr.process requires one authenticated PDF, JPEG, or PNG source reference",
            ),
        )

    expected_types = {
        "source_handle": "integer",
        "output_handle": "integer",
        "result_handle": "integer",
        "output_object_id": "uuid",
        "page_count": "integer",
        "language_set_id": "token",
        "profile_id": "token",
    }
    parameters = payload.get("parameters")
    parameter_map: dict[str, Mapping[str, Any]] = {}
    if isinstance(parameters, list):
        for value in parameters:
            if isinstance(value, Mapping) and isinstance(value.get("name"), str):
                parameter_map[cast(str, value["name"])] = value
    if (
        not isinstance(parameters, list)
        or len(parameters) != len(expected_types)
        or set(parameter_map) != set(expected_types)
        or any(
            parameter_map[name].get("value_type") != value_type
            for name, value_type in expected_types.items()
            if name in parameter_map
        )
    ):
        _add_issue(
            issues,
            _issue(
                "RPC_OPERATION_INPUT_INVALID",
                document,
                "$.payload.parameters",
                "ocr.process requires its exact closed parameter set",
            ),
        )
        return _sorted_issues(issues)

    handles = tuple(
        parameter_map[name].get("value")
        for name in (
            "source_handle",
            "output_handle",
            "result_handle",
        )
    )
    output_id = parameter_map["output_object_id"].get("value")
    page_count = parameter_map["page_count"].get("value")
    language_set_id = parameter_map["language_set_id"].get("value")
    profile_id = parameter_map["profile_id"].get("value")
    limits = payload.get("requested_limits")
    limit_pages = limits.get("page_count") if isinstance(limits, Mapping) else None
    temporary_bytes = limits.get("temporary_storage_bytes") if isinstance(limits, Mapping) else None
    output_bytes = limits.get("output_bytes") if isinstance(limits, Mapping) else None
    source_bytes = None if source is None else source.get("expected_byte_size")
    correlated_ids = (
        data.get("case_id"),
        data.get("operation_id"),
        None if source is None else source.get("object_id"),
        output_id,
    )
    invalid = (
        any(type(value) is not int or value < 1 for value in handles)
        or len(set(handles)) != 3
        or any(not _canonical_uuid_v4(value) for value in correlated_ids)
        or len(set(cast(tuple[str, str, str, str], correlated_ids))) != 4
        or type(page_count) is not int
        or not 1 <= page_count <= 4_096
        or page_count != limit_pages
        or (
            source is not None and source.get("content_kind") in {"jpeg", "png"} and page_count != 1
        )
        or not isinstance(language_set_id, str)
        or not isinstance(profile_id, str)
        or type(source_bytes) is not int
        or type(temporary_bytes) is not int
        or type(output_bytes) is not int
        or source_bytes > temporary_bytes
        or output_bytes > temporary_bytes - source_bytes
    )
    if invalid:
        _add_issue(
            issues,
            _issue(
                "RPC_OPERATION_INPUT_INVALID",
                document,
                "$.payload",
                "ocr.process identities, handles, page count, tokens, and limits must be bounded",
            ),
        )
    return _sorted_issues(issues)


def _image_normalize_start_issues(
    data: Mapping[str, Any],
    payload: Mapping[str, Any],
    document: str,
) -> list[RpcIssue]:
    """Validate the exact additive image operation without changing schema v1."""

    issues: list[RpcIssue] = []
    inputs = payload.get("inputs")
    source: Mapping[str, Any] | None = None
    if isinstance(inputs, list) and len(inputs) == 1 and isinstance(inputs[0], Mapping):
        source = inputs[0]
    if source is None or (
        source.get("area") != "source_read"
        or source.get("operation_id") is not None
        or source.get("content_kind") not in {"jpeg", "png"}
        or type(source.get("expected_byte_size")) is not int
        or cast(int, source.get("expected_byte_size")) < 1
        or not isinstance(source.get("expected_sha256"), str)
    ):
        _add_issue(
            issues,
            _issue(
                "RPC_OPERATION_INPUT_INVALID",
                document,
                "$.payload.inputs",
                "image.normalize requires one authenticated JPEG or PNG source reference",
            ),
        )

    parameters = payload.get("parameters")
    expected_types = {
        "source_handle": "integer",
        "output_handle": "integer",
        "output_object_id": "uuid",
        "maximum_width_pixels": "integer",
        "maximum_height_pixels": "integer",
        "additional_rotation": "integer",
    }
    parameter_map: dict[str, Mapping[str, Any]] = {}
    if isinstance(parameters, list):
        for value in parameters:
            if isinstance(value, Mapping) and isinstance(value.get("name"), str):
                parameter_map[cast(str, value["name"])] = value
    parameter_count = len(parameters) if isinstance(parameters, list) else -1
    if (
        parameter_count != len(expected_types)
        or set(parameter_map) != set(expected_types)
        or any(
            parameter_map[name].get("value_type") != value_type
            for name, value_type in expected_types.items()
            if name in parameter_map
        )
    ):
        _add_issue(
            issues,
            _issue(
                "RPC_OPERATION_INPUT_INVALID",
                document,
                "$.payload.parameters",
                "image.normalize requires its exact closed parameter set",
            ),
        )
        return _sorted_issues(issues)

    source_handle = parameter_map["source_handle"].get("value")
    output_handle = parameter_map["output_handle"].get("value")
    output_id = parameter_map["output_object_id"].get("value")
    maximum_width = parameter_map["maximum_width_pixels"].get("value")
    maximum_height = parameter_map["maximum_height_pixels"].get("value")
    rotation = parameter_map["additional_rotation"].get("value")
    limits = payload.get("requested_limits")
    pixel_limit = limits.get("pixel_count") if isinstance(limits, Mapping) else None
    invalid_values = (
        type(source_handle) is not int
        or source_handle < 1
        or type(output_handle) is not int
        or output_handle < 1
        or source_handle == output_handle
        or not _canonical_uuid_v4(output_id)
        or type(maximum_width) is not int
        or maximum_width < 1
        or type(maximum_height) is not int
        or maximum_height < 1
        or type(pixel_limit) is not int
        or maximum_width * maximum_height > pixel_limit
        or type(rotation) is not int
        or rotation not in {0, 90, 180, 270}
    )
    correlated_ids = (
        data.get("case_id"),
        data.get("operation_id"),
        None if source is None else source.get("object_id"),
        output_id,
    )
    invalid_correlations = (
        any(not _canonical_uuid_v4(value) for value in correlated_ids)
        or len(set(cast(tuple[str, str, str, str], correlated_ids))) != 4
    )
    if invalid_values or invalid_correlations:
        _add_issue(
            issues,
            _issue(
                "RPC_OPERATION_INPUT_INVALID",
                document,
                "$.payload.parameters",
                "image.normalize parameters and identities must be bounded and distinct",
            ),
        )
    return _sorted_issues(issues)


def _image_transform_start_issues(
    data: Mapping[str, Any],
    payload: Mapping[str, Any],
    document: str,
) -> list[RpcIssue]:
    """Validate the exact additive scan-transform operation."""

    issues: list[RpcIssue] = []
    inputs = payload.get("inputs")
    source: Mapping[str, Any] | None = None
    if isinstance(inputs, list) and len(inputs) == 1 and isinstance(inputs[0], Mapping):
        source = inputs[0]
    if source is None or (
        source.get("area") != "source_read"
        or source.get("operation_id") is not None
        or source.get("content_kind") not in {"jpeg", "png"}
        or type(source.get("expected_byte_size")) is not int
        or cast(int, source.get("expected_byte_size")) < 1
        or not isinstance(source.get("expected_sha256"), str)
    ):
        _add_issue(
            issues,
            _issue(
                "RPC_OPERATION_INPUT_INVALID",
                document,
                "$.payload.inputs",
                "image.transform requires one authenticated JPEG or PNG source reference",
            ),
        )
    expected_types = {
        "source_handle": "integer",
        "output_handle": "integer",
        "page_id": "uuid",
        "output_object_id": "uuid",
        "maximum_width_pixels": "integer",
        "maximum_height_pixels": "integer",
        "quarter_turns_clockwise": "integer",
        "crop_x_millionths": "integer",
        "crop_y_millionths": "integer",
        "crop_width_millionths": "integer",
        "crop_height_millionths": "integer",
        "deskew_millidegrees_clockwise": "integer",
        "contrast_factor_millionths": "integer",
        "background_cleanup_strength_millionths": "integer",
    }
    parameters = payload.get("parameters")
    parameter_map: dict[str, Mapping[str, Any]] = {}
    if isinstance(parameters, list):
        for value in parameters:
            if isinstance(value, Mapping) and isinstance(value.get("name"), str):
                parameter_map[cast(str, value["name"])] = value
    if (
        not isinstance(parameters, list)
        or len(parameters) != len(expected_types)
        or set(parameter_map) != set(expected_types)
        or any(
            parameter_map[name].get("value_type") != value_type
            for name, value_type in expected_types.items()
            if name in parameter_map
        )
    ):
        _add_issue(
            issues,
            _issue(
                "RPC_OPERATION_INPUT_INVALID",
                document,
                "$.payload.parameters",
                "image.transform requires its exact closed parameter set",
            ),
        )
        return _sorted_issues(issues)
    values = {name: parameter_map[name].get("value") for name in expected_types}
    limits = payload.get("requested_limits")
    pixel_limit = limits.get("pixel_count") if isinstance(limits, Mapping) else None
    x = values["crop_x_millionths"]
    y = values["crop_y_millionths"]
    width = values["crop_width_millionths"]
    height = values["crop_height_millionths"]
    maximum_width = values["maximum_width_pixels"]
    maximum_height = values["maximum_height_pixels"]
    invalid = (
        type(values["source_handle"]) is not int
        or values["source_handle"] < 1
        or type(values["output_handle"]) is not int
        or values["output_handle"] < 1
        or values["source_handle"] == values["output_handle"]
        or not _canonical_uuid_v4(values["page_id"])
        or not _canonical_uuid_v4(values["output_object_id"])
        or type(maximum_width) is not int
        or maximum_width < 1
        or type(maximum_height) is not int
        or maximum_height < 1
        or type(pixel_limit) is not int
        or maximum_width * maximum_height > pixel_limit
        or values["quarter_turns_clockwise"] not in {0, 1, 2, 3}
        or type(x) is not int
        or type(y) is not int
        or type(width) is not int
        or type(height) is not int
        or not 0 <= x <= 1_000_000
        or not 0 <= y <= 1_000_000
        or not 1 <= width <= 1_000_000
        or not 1 <= height <= 1_000_000
        or x + width > 1_000_000
        or y + height > 1_000_000
        or type(values["deskew_millidegrees_clockwise"]) is not int
        or not -15_000 <= values["deskew_millidegrees_clockwise"] <= 15_000
        or type(values["contrast_factor_millionths"]) is not int
        or not 0 <= values["contrast_factor_millionths"] <= 4_000_000
        or type(values["background_cleanup_strength_millionths"]) is not int
        or not 0 <= values["background_cleanup_strength_millionths"] <= 1_000_000
    )
    correlated_ids = (
        data.get("case_id"),
        data.get("operation_id"),
        None if source is None else source.get("object_id"),
        values["page_id"],
        values["output_object_id"],
    )
    if (
        invalid
        or any(not _canonical_uuid_v4(value) for value in correlated_ids)
        or len(set(cast(tuple[str, str, str, str, str], correlated_ids))) != 5
    ):
        _add_issue(
            issues,
            _issue(
                "RPC_OPERATION_INPUT_INVALID",
                document,
                "$.payload.parameters",
                "image.transform parameters and identities must be bounded and distinct",
            ),
        )
    return _sorted_issues(issues)


def _result_and_progress_issues(data: Mapping[str, Any], document: str) -> list[RpcIssue]:
    payload = data.get("payload")
    if not isinstance(payload, Mapping):
        return []
    message_type = data["message_type"]
    issues: list[RpcIssue] = []
    if message_type == "operation.result":
        outcome = payload.get("outcome")
        outputs = payload.get("outputs")
        error = payload.get("error")
        if outcome == "succeeded" and error is not None:
            _add_issue(
                issues,
                _issue(
                    "RPC_RESULT_INVALID",
                    document,
                    "$.payload.error",
                    "successful results cannot contain an error",
                ),
            )
        elif outcome == "cancelled" and (outputs != [] or error is not None):
            _add_issue(
                issues,
                _issue(
                    "RPC_RESULT_INVALID",
                    document,
                    "$.payload",
                    "cancelled results contain no outputs or error",
                ),
            )
        elif outcome in {"failed", "timed_out"} and (
            outputs != [] or not isinstance(error, Mapping)
        ):
            _add_issue(
                issues,
                _issue(
                    "RPC_RESULT_INVALID",
                    document,
                    "$.payload",
                    "failed and timed-out results require an error and contain no outputs",
                ),
            )
        for issue in _pdf_image_stage_result_issues(payload, document):
            _add_issue(issues, issue)
    elif message_type == "operation.progress":
        if payload.get("stage_code") != "processing":
            _add_issue(
                issues,
                _issue(
                    "RPC_PROGRESS_INVALID",
                    document,
                    "$.payload.stage_code",
                    "the progress stage is not registered for protocol.noop version 1",
                ),
            )
        completed = payload.get("completed_units")
        total = payload.get("total_units")
        if type(completed) is int and type(total) is int and completed > total:
            _add_issue(
                issues,
                _issue(
                    "RPC_PROGRESS_INVALID",
                    document,
                    "$.payload.completed_units",
                    "completed units cannot exceed total units",
                ),
            )
    return _sorted_issues(issues)


def _pdf_image_stage_result_issues(payload: Mapping[str, Any], document: str) -> list[RpcIssue]:
    """Check recognizable image-stage output pairs without inventing session state.

    Runtime state additionally binds IDs and per-operation byte capacities to the
    accepted start. A standalone result fixture supplies neither of those facts.
    """

    outputs = payload.get("outputs")
    kinds = ("pdf-image-stage-pdf-v1", "pdf-image-stage-evidence-v1")
    if not isinstance(outputs, list) or not any(
        isinstance(reference, Mapping) and reference.get("content_kind") in kinds
        for reference in outputs
    ):
        return []
    invalid = (
        payload.get("outcome") != "succeeded"
        or len(outputs) != 2
        or any(not isinstance(reference, Mapping) for reference in outputs)
    )
    if not invalid:
        references = cast(list[Mapping[str, Any]], outputs)
        invalid = (
            tuple(reference.get("content_kind") for reference in references) != kinds
            or references[0].get("object_id") == references[1].get("object_id")
            or any(
                type(reference.get("expected_byte_size")) is not int
                or cast(int, reference.get("expected_byte_size")) < 1
                for reference in references
            )
            or type(references[1].get("expected_byte_size")) is not int
            or cast(int, references[1].get("expected_byte_size")) > 2 * 1024 * 1024
        )
    return (
        [
            _issue(
                "RPC_RESULT_INVALID",
                document,
                "$.payload.outputs",
                "image stage requires its exact bounded PDF and evidence output pair",
            )
        ]
        if invalid
        else []
    )


def _parameter_issues(data: Mapping[str, Any], document: str) -> list[RpcIssue]:
    issues: list[RpcIssue] = []
    for path, key, value in _walk_members(data):
        if key != "parameters" or not isinstance(value, list):
            continue
        seen_names: set[str] = set()
        for index, parameter in enumerate(value):
            if not isinstance(parameter, Mapping):
                continue
            name = parameter.get("name")
            if isinstance(name, str):
                if name in seen_names:
                    _add_issue(
                        issues,
                        _issue(
                            "RPC_PARAMETER_DUPLICATE",
                            document,
                            _json_path((*path, index, "name")),
                            "parameter names must be unique",
                        ),
                    )
                seen_names.add(name)
    return _sorted_issues(issues)


def _error_issues(data: Mapping[str, Any], document: str) -> list[RpcIssue]:
    message_type = data["message_type"]
    payload = data.get("payload")
    if not isinstance(payload, Mapping):
        return []
    if message_type in {"protocol.error", "worker.fault"}:
        error = payload.get("error")
    elif message_type == "operation.result":
        error = payload.get("error")
    else:
        return []
    if error is None or not isinstance(error, Mapping):
        return []

    issues: list[RpcIssue] = []
    code = error.get("code")
    retry_action = error.get("retry_action")
    restart_required = error.get("restart_required")
    if (retry_action == "restart_worker") is not (restart_required is True):
        _add_issue(
            issues,
            _issue(
                "RPC_ERROR_INVALID",
                document,
                "$.payload.error.restart_required",
                "restart requirement must match the retry action",
            ),
        )
    if message_type == "protocol.error" and error.get("category") != "protocol":
        _add_issue(
            issues,
            _issue(
                "RPC_ERROR_INVALID",
                document,
                "$.payload.error.category",
                "protocol errors require the protocol category",
            ),
        )
    expected_code: str | None = None
    if message_type == "protocol.error":
        expected_code = "RPC_MESSAGE_INVALID"
    elif message_type == "worker.fault":
        expected_code = "WORKER_INTERNAL_FAULT"
    elif message_type == "operation.result":
        outcome = payload.get("outcome")
        if outcome == "failed":
            expected_code = "WORKER_OPERATION_FAILED"
        elif outcome == "timed_out":
            expected_code = "WORKER_OPERATION_TIMED_OUT"
    if expected_code is not None and code != expected_code:
        _add_issue(
            issues,
            _issue(
                "RPC_ERROR_INVALID",
                document,
                "$.payload.error.code",
                "the error code is not registered for this message context",
            ),
        )
    if message_type == "protocol.error" and (
        retry_action != "restart_worker" or restart_required is not True
    ):
        _add_issue(
            issues,
            _issue(
                "RPC_ERROR_INVALID",
                document,
                "$.payload.error.retry_action",
                "protocol errors require a worker restart",
            ),
        )
    if message_type == "worker.fault" and restart_required is not True:
        _add_issue(
            issues,
            _issue(
                "RPC_ERROR_INVALID",
                document,
                "$.payload.error.restart_required",
                "worker faults require a worker restart",
            ),
        )

    parameters = error.get("parameters")
    if isinstance(parameters, list) and parameters:
        _add_issue(
            issues,
            _issue(
                "RPC_ERROR_PARAMETER_INVALID",
                document,
                "$.payload.error.parameters",
                "public protocol-v1 errors cannot carry context values",
            ),
        )
    return _sorted_issues(issues)


def _semantic_issues(data: Mapping[str, Any], document: str) -> list[RpcIssue]:
    issues = [
        *_identifier_issues(data, document),
        *_correlation_issues(data, document),
        *_capability_issues(data, document),
        *_reference_issues(data, document),
        *_operation_registry_issues(data, document),
        *_result_and_progress_issues(data, document),
        *_parameter_issues(data, document),
        *_error_issues(data, document),
    ]
    return _sorted_issues(issues)


def validate_worker_rpc_data(
    data: Mapping[str, Any],
    schema: Mapping[str, Any],
    *,
    document: str = "worker-rpc.json",
) -> list[RpcIssue]:
    """Validate one already decoded worker-protocol message without mutating it."""

    predispatch_issues = _predispatch_issues(data, document)
    if predispatch_issues:
        return predispatch_issues
    security_issues = _security_member_issues(data, document)
    if security_issues:
        return security_issues
    schema_issues = _schema_validation_issues(data, schema, document)
    try:
        semantic_issues = _semantic_issues(data, document)
    except Exception:
        semantic_issues = [
            _issue(
                "RPC_SEMANTIC_EXECUTION_INVALID",
                document,
                "$",
                "semantic validation could not complete safely",
            )
        ]
    return _sorted_issues([*schema_issues, *semantic_issues])


def validate_worker_rpc_document(
    path: Path,
    root: Path = PROJECT_ROOT,
    *,
    schema: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[RpcIssue]]:
    """Strictly load and validate one worker-protocol JSON document."""

    active_schema = schema
    if active_schema is None:
        active_schema, schema_issues = load_worker_rpc_schema(root)
        if active_schema is None:
            return None, schema_issues
    data, load_issues = _load_json_object(
        path,
        root=root,
        maximum_bytes=MAX_MESSAGE_BYTES,
    )
    if data is None:
        return None, load_issues
    issues = validate_worker_rpc_data(
        data,
        active_schema,
        document=_document_label(path, root),
    )
    return (None, issues) if issues else (data, [])


def _fixture_paths(
    directory: Path,
    *,
    document: str,
) -> tuple[list[Path] | None, list[RpcIssue]]:
    try:
        candidates: list[Path] = []
        for candidate in directory.iterdir():
            if candidate.is_file() and candidate.match("*.json"):
                candidates.append(candidate)
                if len(candidates) > MAX_FIXTURE_FILES:
                    return None, [
                        _issue(
                            "RPC_FIXTURE_LIMIT",
                            document,
                            "$",
                            "fixture set exceeds its file-count limit",
                        )
                    ]
    except OSError as error:
        return None, [_issue("RPC_FIXTURE_SET_UNREADABLE", document, "$", type(error).__name__)]
    return sorted(candidates, key=lambda path: path.name), []


def validate_worker_rpc_fixture_set(
    root: Path = PROJECT_ROOT,
) -> tuple[RpcFixtureSummary | None, list[RpcIssue]]:
    """Validate canonical RPC examples and prove every negative fixture is rejected."""

    schema, schema_issues = load_worker_rpc_schema(root)
    if schema is None:
        return None, schema_issues
    positive_paths, positive_path_issues = _fixture_paths(
        root / RPC_EXAMPLE_DIRECTORY,
        document=RPC_EXAMPLE_DIRECTORY.as_posix(),
    )
    invalid_paths, invalid_path_issues = _fixture_paths(
        root / RPC_INVALID_FIXTURE_DIRECTORY,
        document=RPC_INVALID_FIXTURE_DIRECTORY.as_posix(),
    )
    path_issues = [*positive_path_issues, *invalid_path_issues]
    if path_issues or positive_paths is None or invalid_paths is None:
        return None, _sorted_issues(path_issues)

    fixture_issues: list[RpcIssue] = []
    if not positive_paths:
        _add_issue(
            fixture_issues,
            _issue(
                "RPC_POSITIVE_FIXTURES_MISSING",
                RPC_EXAMPLE_DIRECTORY.as_posix(),
                "$",
                "at least one positive fixture is required",
            ),
        )
    if not invalid_paths:
        _add_issue(
            fixture_issues,
            _issue(
                "RPC_NEGATIVE_FIXTURES_MISSING",
                RPC_INVALID_FIXTURE_DIRECTORY.as_posix(),
                "$",
                "at least one negative fixture is required",
            ),
        )
    for path in positive_paths:
        _, issues = validate_worker_rpc_document(path, root, schema=schema)
        for issue in issues:
            _add_issue(
                fixture_issues,
                _issue(
                    "RPC_POSITIVE_FIXTURE_INVALID",
                    issue.document,
                    issue.path,
                    "canonical worker-protocol example is invalid",
                ),
            )
    for path in invalid_paths:
        data, issues = validate_worker_rpc_document(path, root, schema=schema)
        if data is not None or not issues:
            _add_issue(
                fixture_issues,
                _issue(
                    "RPC_NEGATIVE_FIXTURE_ACCEPTED",
                    _document_label(path, root),
                    "$",
                    "negative worker-protocol fixture was accepted",
                ),
            )
    if fixture_issues:
        return None, _sorted_issues(fixture_issues)
    return RpcFixtureSummary(len(positive_paths), len(invalid_paths)), []


def main(argv: Sequence[str] | None = None) -> int:
    """Validate the checked-in worker-RPC contract and fixtures."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=PROJECT_ROOT,
        help="repository root (default: detected project root)",
    )
    parser.add_argument("document", nargs="?", type=Path)
    arguments = parser.parse_args(argv)
    if arguments.document is not None:
        _, issues = validate_worker_rpc_document(arguments.document, arguments.root)
        summary = None
    else:
        summary, issues = validate_worker_rpc_fixture_set(arguments.root)
    if issues:
        for issue in issues:
            print(issue.render(), file=sys.stderr)
        return 1
    if summary is None:
        print("OK: validated worker-RPC v1 document.")
    else:
        print(
            "OK: validated worker-RPC v1 with "
            f"{summary.positive_count} positive and {summary.negative_count} negative fixtures."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

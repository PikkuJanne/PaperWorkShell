"""Bounded JSON codec and lifecycle envelope for case manifests."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Final, Never, cast
from uuid import UUID

from paperworkshell.domain.case_lifecycle import (
    CURRENT_CASE_MANIFEST_VERSION,
    INTERMEDIATE_CASE_MANIFEST_VERSION,
    LEGACY_CASE_MANIFEST_VERSION,
    MAX_SAFE_MANIFEST_INTEGER,
    REMOVAL_CASE_MANIFEST_VERSION,
    SUPPORTED_CASE_MANIFEST_VERSIONS,
    CaseLifecycleErrorCode,
    CaseSummary,
    reject_case_lifecycle,
)

MAX_MANIFEST_BYTES: Final = 8 * 1024 * 1024
MAX_JSON_DEPTH: Final = 64
MAX_JSON_NODES: Final = 100_000
MAX_JSON_STRING_CODEPOINTS: Final = 1_048_576
MAX_MANIFEST_COLLECTION_ITEMS: Final = 4096

_CASE_ID_RE: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
_UTC_TIMESTAMP_RE: Final = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?Z\Z"
)
_MANIFEST_KEYS: Final = frozenset(
    {
        "schema_version",
        "case_id",
        "revision",
        "created_at",
        "updated_at",
        "template_ref",
        "sources",
        "pages",
        "page_sequence_base",
        "page_sequence",
        "page_operations",
        "assets",
        "overlays",
        "assignments",
        "constraints",
        "confirmations",
        "export_history",
    }
)
_COLLECTION_KEYS: Final = (
    "sources",
    "pages",
    "page_sequence_base",
    "page_sequence",
    "page_operations",
    "assets",
    "overlays",
    "assignments",
    "confirmations",
)


class _DuplicateJsonKey(ValueError):
    pass


class _UnsupportedJsonNumber(ValueError):
    pass


class _JsonIntegerToken(str):
    """Integer spelling retained while probing a version before versioned decoding."""


_JSON_FLOAT_SENTINEL: Final = object()


def has_current_manifest_schema_version(payload: bytes) -> bool:
    """Probe a bounded JSON object's integer version before current-schema value rules.

    Future schemas may legally use members or number forms that the current schema rejects. This
    probe therefore preserves integer spelling and treats floats as inert nodes. The byte,
    UTF-8, duplicate-key, and non-finite-constant bounds apply before dispatch; current depth,
    node, and string bounds apply only after the probe identifies the current version.
    """

    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_MANIFEST_BYTES:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_size")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_utf8")
    if text.startswith("\ufeff"):
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_bom")
    try:
        loaded = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=lambda _value: _JSON_FLOAT_SENTINEL,
            parse_int=_JsonIntegerToken,
        )
    except (
        _DuplicateJsonKey,
        _UnsupportedJsonNumber,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ):
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_json")
    if type(loaded) is not dict:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_object")
    document = cast(dict[str, object], loaded)
    version = document.get("schema_version")
    if type(version) is not _JsonIntegerToken:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_version")
    current = version == str(CURRENT_CASE_MANIFEST_VERSION)
    if current:
        _validate_json_resources(document)
    return current


def probe_supported_manifest_schema_version(payload: bytes) -> int | None:
    """Return an exact supported v1/v2/v3 dispatch value without interpreting fields.

    Unknown future integer versions return ``None`` after the same bounded outer
    JSON probe. Malformed version tokens still fail as invalid JSON data.
    """

    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_MANIFEST_BYTES:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_size")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_utf8")
    if text.startswith("\ufeff"):
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_bom")
    try:
        loaded = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=lambda _value: _JSON_FLOAT_SENTINEL,
            parse_int=_JsonIntegerToken,
        )
    except (
        _DuplicateJsonKey,
        _UnsupportedJsonNumber,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ):
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_json")
    if type(loaded) is not dict:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_object")
    document = cast(dict[str, object], loaded)
    version = document.get("schema_version")
    if type(version) is not _JsonIntegerToken:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_version")
    selected = {
        str(LEGACY_CASE_MANIFEST_VERSION): LEGACY_CASE_MANIFEST_VERSION,
        str(INTERMEDIATE_CASE_MANIFEST_VERSION): INTERMEDIATE_CASE_MANIFEST_VERSION,
        str(REMOVAL_CASE_MANIFEST_VERSION): REMOVAL_CASE_MANIFEST_VERSION,
        str(CURRENT_CASE_MANIFEST_VERSION): CURRENT_CASE_MANIFEST_VERSION,
    }.get(version)
    if selected is not None:
        _validate_json_resources(document)
    return selected


def decode_manifest(payload: bytes) -> dict[str, object]:
    """Decode one bounded strict-JSON manifest without accepting duplicate keys or floats."""

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_utf8")
    if text.startswith("\ufeff"):
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_bom")
    try:
        loaded = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
        )
    except (
        _DuplicateJsonKey,
        _UnsupportedJsonNumber,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ):
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_json")
    if type(loaded) is not dict:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_object")
    document = cast(dict[str, object], loaded)
    _validate_json_resources(document)
    return document


def summary_from_manifest(document: dict[str, object], expected_case_id: UUID) -> CaseSummary:
    """Validate the bounded lifecycle envelope and return path-free summary metadata."""

    version = document.get("schema_version")
    if type(version) is not int:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_version")
    if version not in SUPPORTED_CASE_MANIFEST_VERSIONS:
        reject_case_lifecycle(
            CaseLifecycleErrorCode.MANIFEST_VERSION_UNSUPPORTED,
            "case.manifest_version",
        )
    expected_keys = _MANIFEST_KEYS
    collection_keys: tuple[str, ...] = _COLLECTION_KEYS
    if version in {REMOVAL_CASE_MANIFEST_VERSION, CURRENT_CASE_MANIFEST_VERSION}:
        expected_keys = expected_keys | {"redaction_marks"}
        collection_keys = (*collection_keys, "redaction_marks")
    if version == CURRENT_CASE_MANIFEST_VERSION and "case_name" in document:
        expected_keys = expected_keys | {"case_name"}
    if frozenset(document) != expected_keys:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_members")
    raw_case_id = document["case_id"]
    if type(raw_case_id) is not str or _CASE_ID_RE.fullmatch(raw_case_id) is None:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_id")
    parsed_case_id = UUID(raw_case_id)
    if parsed_case_id != expected_case_id:
        reject_case_lifecycle(
            CaseLifecycleErrorCode.MANIFEST_CASE_ID_MISMATCH,
            "case.manifest_id",
        )
    revision = document["revision"]
    if type(revision) is not int or not 0 <= revision <= MAX_SAFE_MANIFEST_INTEGER:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_revision")
    for key in collection_keys:
        value = document[key]
        if type(value) is not list or len(value) > MAX_MANIFEST_COLLECTION_ITEMS:
            reject_case_lifecycle(
                CaseLifecycleErrorCode.MANIFEST_INVALID,
                "case.manifest_collections",
            )
    if document["template_ref"] is not None and type(document["template_ref"]) is not dict:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_template")
    if type(document["constraints"]) is not dict or type(document["export_history"]) is not dict:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_records")
    return CaseSummary(
        case_id=parsed_case_id,
        manifest_version=version,
        revision=revision,
        created_at=parse_utc_timestamp(document["created_at"]),
        updated_at=parse_utc_timestamp(document["updated_at"]),
        case_name=cast(str, document.get("case_name", "")),
    )


def parse_utc_timestamp(value: object) -> datetime:
    """Parse the exact canonical UTC timestamp shape shared by manifest v1/v2."""

    if type(value) is not str or _UTC_TIMESTAMP_RE.fullmatch(value) is None:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_timestamp")
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, "case.manifest_timestamp")
    return parsed


def render_utc_timestamp(value: datetime) -> str:
    """Render one aware UTC timestamp in canonical manifest form."""

    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        reject_case_lifecycle(CaseLifecycleErrorCode.CREATE_FAILED, "case.initial_timestamp")
    normalized = value.astimezone(UTC)
    timespec = "seconds" if normalized.microsecond == 0 else "microseconds"
    return normalized.isoformat(timespec=timespec).replace("+00:00", "Z")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _reject_constant(_value: str) -> Never:
    raise _UnsupportedJsonNumber


def _reject_float(_value: str) -> Never:
    raise _UnsupportedJsonNumber


def _validate_json_resources(root: object) -> None:
    stack: list[tuple[object, int]] = [(root, 1)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            reject_case_lifecycle(
                CaseLifecycleErrorCode.MANIFEST_INVALID,
                "case.manifest_resources",
            )
        if type(value) is dict:
            mapping = cast(dict[str, object], value)
            if len(mapping) > MAX_JSON_NODES - nodes - len(stack):
                reject_case_lifecycle(
                    CaseLifecycleErrorCode.MANIFEST_INVALID,
                    "case.manifest_resources",
                )
            for key, child in mapping.items():
                if len(key) > MAX_JSON_STRING_CODEPOINTS:
                    reject_case_lifecycle(
                        CaseLifecycleErrorCode.MANIFEST_INVALID,
                        "case.manifest_resources",
                    )
                stack.append((child, depth + 1))
        elif type(value) is list:
            items = cast(list[object], value)
            if len(items) > MAX_JSON_NODES - nodes - len(stack):
                reject_case_lifecycle(
                    CaseLifecycleErrorCode.MANIFEST_INVALID,
                    "case.manifest_resources",
                )
            stack.extend((child, depth + 1) for child in items)
        elif isinstance(value, str) and len(value) > MAX_JSON_STRING_CODEPOINTS:
            reject_case_lifecycle(
                CaseLifecycleErrorCode.MANIFEST_INVALID,
                "case.manifest_resources",
            )

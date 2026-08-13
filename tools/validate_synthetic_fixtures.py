"""Validate the synthetic corpus, provenance, determinism, and repository boundary."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import stat
import sys
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Final, NoReturn, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from tools.dev import SecretScanError, inspect_repository_contents
from tools.generate_synthetic_fixtures import (
    CORPUS_DIRECTORY,
    GOLDEN_DIRECTORY,
    GOLDEN_INDEX_PATH,
    INDEX_PATH,
    PROJECT_ROOT,
    SCHEMA_PATH,
    SYNTHETIC_MARKER,
    GeneratedFixture,
    build_committed_fixtures,
    build_index,
    build_renderer_golden_references,
    canonical_index_bytes,
    canonical_renderer_golden_index_bytes,
)
from tools.image_transform_goldens import (
    CASE_PLANS as IMAGE_TRANSFORM_GOLDEN_PLANS,
)
from tools.image_transform_goldens import (
    GOLDEN_DIRECTORY as IMAGE_TRANSFORM_GOLDEN_DIRECTORY,
)
from tools.image_transform_goldens import validate_committed_golden_set

CANONICAL_SCHEMA_FINGERPRINT: Final = (
    "33229d2fdd6a84377fd10bdc690634037307862257f938f9a6c4c053ef954f2e"
)
SUPPORTED_SCHEMA_VERSION: Final = 1
MAX_INDEX_BYTES: Final = 1 * 1024 * 1024
MAX_SCHEMA_BYTES: Final = 1 * 1024 * 1024
MAX_JSON_DEPTH: Final = 32
MAX_JSON_NODES: Final = 10_000
MAX_JSON_STRING_CODEPOINTS: Final = 16_384
MAX_COLLECTION_MEMBERS: Final = 512
MAX_CORPUS_ENTRIES: Final = 128
MAX_FIXTURE_BYTES: Final = 1 * 1024 * 1024
EXPECTED_NEGATIVE_CODES: Final[dict[str, str]] = {
    "duplicate-key.json": "FIXTURE_INDEX_JSON_DUPLICATE_KEY",
    "future-version.json": "FIXTURE_INDEX_VERSION_FUTURE",
    "legacy-version.json": "FIXTURE_INDEX_VERSION_LEGACY",
    "missing-provenance.json": "FIXTURE_INDEX_SCHEMA_INVALID",
    "unsafe-path.json": "FIXTURE_INDEX_SCHEMA_INVALID",
}
NEGATIVE_FIXTURE_DIRECTORY: Final = Path("tests/fixtures/synthetic/v1/invalid")
_SAFE_JSON_PATH_TOKEN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_EMAIL_PATTERN: Final = re.compile(
    rb"(?i)(?<![A-Z0-9._%+-])[A-Z0-9._%+-]{1,64}@"
    rb"[A-Z0-9.-]{1,190}\.[A-Z]{2,24}(?![A-Z0-9-])"
)
_SSN_PATTERN: Final = re.compile(rb"(?<![0-9])([0-9]{3})-([0-9]{2})-([0-9]{4})(?![0-9])")
_PAN_DIGIT_GROUP_PATTERN: Final = re.compile(rb"[0-9]+")
_PAN_CANDIDATE_START_PATTERN: Final = re.compile(rb"(?<![A-Za-z0-9])[1-6]")
_UUID_PATTERN: Final = re.compile(
    rb"(?i)(?<![A-F0-9])[A-F0-9]{8}-[A-F0-9]{4}-[A-F0-9]{4}-"
    rb"[A-F0-9]{4}-[A-F0-9]{12}(?![A-F0-9])"
)
_IBAN_START_PATTERN: Final = re.compile(rb"(?i)(?<![A-Z0-9])[A-Z]{2}[0-9]{2}")
_MRZ_PATTERN: Final = re.compile(rb"(?m)^P<[A-Z<]{3}[A-Z<]{20,40}$")
_DOCUMENT_SIGNATURES: Final = (
    b"%PDF-",
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"II*\x00",
    b"MM\x00*",
)
_RESERVED_EMAIL_SUFFIXES: Final = (b".invalid", b".example", b".test", b".localhost")
_RESERVED_EMAIL_DOMAINS: Final = (b"example.com", b"example.net", b"example.org")
_MAX_PAN_CANDIDATE_STARTS: Final = 4_096
# SWIFT IBAN Registry, Release 102 (June 2026). Each country has one fixed IBAN length.
_IBAN_LENGTHS: Final[dict[bytes, int]] = {
    b"AD": 24,
    b"AE": 23,
    b"AL": 28,
    b"AT": 20,
    b"AZ": 28,
    b"BA": 20,
    b"BE": 16,
    b"BG": 22,
    b"BH": 22,
    b"BI": 27,
    b"BR": 29,
    b"BY": 28,
    b"CH": 21,
    b"CR": 22,
    b"CY": 28,
    b"CZ": 24,
    b"DE": 22,
    b"DJ": 27,
    b"DK": 18,
    b"DO": 28,
    b"EE": 20,
    b"EG": 29,
    b"ES": 24,
    b"FI": 18,
    b"FK": 18,
    b"FO": 18,
    b"FR": 27,
    b"GB": 22,
    b"GE": 22,
    b"GI": 23,
    b"GL": 18,
    b"GR": 27,
    b"GT": 28,
    b"HN": 28,
    b"HR": 21,
    b"HU": 28,
    b"IE": 22,
    b"IL": 23,
    b"IQ": 23,
    b"IS": 26,
    b"IT": 27,
    b"JO": 30,
    b"KW": 30,
    b"KZ": 20,
    b"LB": 28,
    b"LC": 32,
    b"LI": 21,
    b"LT": 20,
    b"LU": 20,
    b"LV": 21,
    b"LY": 25,
    b"MC": 27,
    b"MD": 24,
    b"ME": 22,
    b"MK": 19,
    b"MN": 20,
    b"MR": 27,
    b"MT": 31,
    b"MU": 30,
    b"NI": 28,
    b"NL": 18,
    b"NO": 15,
    b"OM": 23,
    b"PK": 24,
    b"PL": 28,
    b"PS": 29,
    b"PT": 25,
    b"QA": 29,
    b"RO": 24,
    b"RS": 22,
    b"RU": 33,
    b"SA": 24,
    b"SC": 31,
    b"SD": 18,
    b"SE": 24,
    b"SI": 19,
    b"SK": 24,
    b"SM": 27,
    b"SO": 23,
    b"ST": 25,
    b"SV": 28,
    b"TL": 23,
    b"TN": 24,
    b"TR": 26,
    b"UA": 29,
    b"VA": 22,
    b"VG": 24,
    b"XK": 20,
    b"YE": 30,
}

type JsonScalar = str | int | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


@dataclass(frozen=True)
class FixtureIssue:
    """One deterministic, non-disclosing fixture validation issue."""

    code: str
    document: str
    path: str
    message: str

    def sort_key(self) -> tuple[str, str, str, str]:
        return (self.document, self.path, self.code, self.message)

    def render(self) -> str:
        return f"ERROR [{self.code}] {self.document}#{self.path}: {self.message}"


@dataclass(frozen=True)
class FixtureIndexSummary:
    """Counts for the schema-level index contract."""

    fixture_count: int
    coverage_count: int
    negative_count: int


@dataclass(frozen=True)
class FixtureCorpusSummary:
    """Counts for a complete corpus and repository validation."""

    fixture_count: int
    golden_reference_count: int
    image_transform_golden_reference_count: int
    total_fixture_bytes: int
    repository_history_blob_count: int
    repository_index_blob_count: int
    repository_working_file_count: int


class DuplicateJsonKeyError(ValueError):
    """Raised when strict JSON repeats an object member."""


class NonIntegerJsonNumberError(ValueError):
    """Raised when JSON contains a float or non-standard number."""


class RepositoryPrivacyFinding(Exception):
    """Raised by the repository snapshot callback without disclosing source bytes."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _UnsafePathError(OSError):
    """Signal a containment failure without exposing a path in diagnostics."""


def _issue(code: str, document: str, path: str, message: str) -> FixtureIssue:
    return FixtureIssue(code, document, path, message)


def _sorted_issues(issues: Iterable[FixtureIssue]) -> list[FixtureIssue]:
    return sorted(issues, key=FixtureIssue.sort_key)


def _json_path(parts: Iterable[object]) -> str:
    rendered = "$"
    for part in parts:
        if isinstance(part, int):
            rendered += f"[{part}]"
        elif isinstance(part, str) and _SAFE_JSON_PATH_TOKEN.fullmatch(part):
            rendered += f".{part}"
        else:
            rendered += "[*]"
    return rendered


def _document_label(path: Path, root: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
    except OSError, RuntimeError, ValueError:
        return "external-fixture-index.json"


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError
        result[key] = value
    return result


def _reject_number(_value: str) -> NoReturn:
    raise NonIntegerJsonNumberError


def _path_is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def _path_chain_is_safe(path: Path, trusted_root: Path) -> Path | None:
    try:
        root = Path(os.path.abspath(trusted_root))
        candidate = Path(os.path.abspath(path))
        relative = candidate.relative_to(root)
        if not relative.parts or any(component in {".", ".."} for component in relative.parts):
            return None
        if _path_is_link_or_reparse(root) or not root.is_dir():
            return None
        current = root
        for component in relative.parts[:-1]:
            current = current / component
            if _path_is_link_or_reparse(current) or not current.is_dir():
                return None
        resolved_root = root.resolve(strict=True)
        resolved_parent = candidate.parent.resolve(strict=True)
        if resolved_parent != resolved_root and not resolved_parent.is_relative_to(resolved_root):
            return None
        return resolved_parent / candidate.name
    except OSError, RuntimeError, ValueError:
        return None


def _strip_windows_extended_prefix(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def _opened_file_path(stream: IO[bytes]) -> Path:
    """Return the OS-authenticated path for an already-open regular-file handle."""

    if os.name == "nt":
        import msvcrt
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_final_path = kernel32.GetFinalPathNameByHandleW
        get_final_path.argtypes = (
            wintypes.HANDLE,
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        )
        get_final_path.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32_768)
        length = get_final_path(
            wintypes.HANDLE(msvcrt.get_osfhandle(stream.fileno())),
            buffer,
            len(buffer),
            0,
        )
        if length == 0 or length >= len(buffer):
            raise _UnsafePathError
        return Path(_strip_windows_extended_prefix(buffer.value))

    for descriptor_root in (Path("/proc/self/fd"), Path("/dev/fd")):
        descriptor_path = descriptor_root / str(stream.fileno())
        try:
            return Path(os.readlink(descriptor_path))
        except OSError:
            continue
    raise _UnsafePathError


def _same_authenticated_path(actual: Path, expected: Path) -> bool:
    actual_text = os.path.normcase(os.path.normpath(os.path.abspath(actual)))
    expected_text = os.path.normcase(os.path.normpath(os.path.abspath(expected)))
    return actual_text == expected_text


def _read_regular_bytes(
    path: Path,
    *,
    trusted_root: Path,
    maximum_bytes: int,
    document: str,
    code_prefix: str,
) -> tuple[bytes | None, list[FixtureIssue]]:
    expected_path = _path_chain_is_safe(path, trusted_root)
    if expected_path is None:
        return None, [
            _issue(
                f"{code_prefix}_PATH_UNSAFE",
                document,
                "$",
                "input path must remain beneath non-linked directories",
            )
        ]
    try:
        metadata = path.lstat()
        if (
            _path_is_link_or_reparse(path)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
        ):
            return None, [
                _issue(
                    f"{code_prefix}_PATH_UNSAFE",
                    document,
                    "$",
                    "input must be one non-linked regular file",
                )
            ]
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (
                opened.st_dev != metadata.st_dev
                or opened.st_ino != metadata.st_ino
                or opened.st_size != metadata.st_size
            ):
                raise OSError("input identity changed")
            if not _same_authenticated_path(_opened_file_path(stream), expected_path):
                raise _UnsafePathError
            content = stream.read(maximum_bytes + 1)
            opened_after = os.fstat(stream.fileno())
        final = path.lstat()
        initial_identity = (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
        )
        if initial_identity != (
            opened_after.st_dev,
            opened_after.st_ino,
            opened_after.st_size,
            opened_after.st_mtime_ns,
        ) or initial_identity != (
            final.st_dev,
            final.st_ino,
            final.st_size,
            final.st_mtime_ns,
        ):
            raise OSError("input changed")
        if _path_chain_is_safe(path, trusted_root) != expected_path:
            raise _UnsafePathError
    except _UnsafePathError:
        return None, [
            _issue(
                f"{code_prefix}_PATH_UNSAFE",
                document,
                "$",
                "input path did not retain its authenticated containment",
            )
        ]
    except OSError as error:
        return None, [_issue(f"{code_prefix}_UNREADABLE", document, "$", type(error).__name__)]
    if len(content) > maximum_bytes:
        return None, [
            _issue(
                f"{code_prefix}_TOO_LARGE",
                document,
                "$",
                "input exceeds its byte limit",
            )
        ]
    return content, []


def _resource_issue(loaded: Mapping[str, Any], document: str) -> FixtureIssue | None:
    stack: list[tuple[object, int]] = [(loaded, 1)]
    node_count = 0
    while stack:
        value, depth = stack.pop()
        node_count += 1
        if node_count > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            return _issue(
                "FIXTURE_INDEX_JSON_RESOURCE_LIMIT",
                document,
                "$",
                "JSON exceeds its structural resource limit",
            )
        if isinstance(value, Mapping):
            if len(value) > MAX_COLLECTION_MEMBERS:
                return _issue(
                    "FIXTURE_INDEX_JSON_RESOURCE_LIMIT",
                    document,
                    "$",
                    "JSON object exceeds its member limit",
                )
            for key, child in value.items():
                if len(key) > MAX_JSON_STRING_CODEPOINTS:
                    return _issue(
                        "FIXTURE_INDEX_JSON_RESOURCE_LIMIT",
                        document,
                        "$",
                        "JSON text exceeds its code-point limit",
                    )
                stack.append((child, depth + 1))
        elif isinstance(value, list):
            if len(value) > MAX_COLLECTION_MEMBERS:
                return _issue(
                    "FIXTURE_INDEX_JSON_RESOURCE_LIMIT",
                    document,
                    "$",
                    "JSON array exceeds its member limit",
                )
            stack.extend((child, depth + 1) for child in value)
        elif isinstance(value, str) and len(value) > MAX_JSON_STRING_CODEPOINTS:
            return _issue(
                "FIXTURE_INDEX_JSON_RESOURCE_LIMIT",
                document,
                "$",
                "JSON text exceeds its code-point limit",
            )
    return None


def _decode_json_bytes(
    content: bytes,
    *,
    document: str,
    code_prefix: str,
) -> tuple[dict[str, Any] | None, list[FixtureIssue]]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None, [
            _issue(
                f"{code_prefix}_TEXT_ENCODING_INVALID",
                document,
                "$",
                "input must be strict UTF-8",
            )
        ]
    try:
        loaded = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except DuplicateJsonKeyError:
        return None, [
            _issue(
                f"{code_prefix}_JSON_DUPLICATE_KEY",
                document,
                "$",
                "JSON object members must be unique",
            )
        ]
    except NonIntegerJsonNumberError:
        return None, [
            _issue(
                f"{code_prefix}_JSON_NUMBER_INVALID",
                document,
                "$",
                "JSON numbers must be finite integers",
            )
        ]
    except json.JSONDecodeError, RecursionError:
        return None, [
            _issue(
                f"{code_prefix}_JSON_INVALID",
                document,
                "$",
                "input must be one valid JSON object",
            )
        ]
    if not isinstance(loaded, dict):
        return None, [
            _issue(
                f"{code_prefix}_JSON_INVALID",
                document,
                "$",
                "input must be one valid JSON object",
            )
        ]
    mapping = cast(dict[str, Any], loaded)
    resource_issue = _resource_issue(mapping, document)
    if resource_issue is not None:
        return None, [resource_issue]
    return mapping, []


def _decode_json(
    path: Path,
    root: Path,
    *,
    maximum_bytes: int,
    code_prefix: str,
) -> tuple[dict[str, Any] | None, list[FixtureIssue]]:
    document = _document_label(path, root)
    content, read_issues = _read_regular_bytes(
        path,
        trusted_root=root,
        maximum_bytes=maximum_bytes,
        document=document,
        code_prefix=code_prefix,
    )
    if content is None:
        return None, read_issues
    return _decode_json_bytes(content, document=document, code_prefix=code_prefix)


def _schema_reference_issue(schema: Mapping[str, Any], document: str) -> FixtureIssue | None:
    stack: list[object] = [schema]
    while stack:
        value = stack.pop()
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key in {"$dynamicRef", "$recursiveRef"}:
                    return _issue(
                        "FIXTURE_SCHEMA_REFERENCE_INVALID",
                        document,
                        "$",
                        "dynamic or recursive schema references are not permitted",
                    )
                if key == "$ref" and (not isinstance(child, str) or not child.startswith("#")):
                    return _issue(
                        "FIXTURE_SCHEMA_REFERENCE_INVALID",
                        document,
                        "$",
                        "schema references must remain inside this file",
                    )
                stack.append(child)
        elif isinstance(value, list):
            stack.extend(value)
    return None


def load_fixture_schema(
    root: Path = PROJECT_ROOT,
) -> tuple[dict[str, Any] | None, list[FixtureIssue]]:
    """Load and authenticate the closed synthetic-index schema."""

    path = root / SCHEMA_PATH
    document = SCHEMA_PATH.as_posix()
    content, read_issues = _read_regular_bytes(
        path,
        trusted_root=root,
        maximum_bytes=MAX_SCHEMA_BYTES,
        document=document,
        code_prefix="FIXTURE_SCHEMA",
    )
    if content is None:
        return None, read_issues
    if hashlib.sha256(content).hexdigest() != CANONICAL_SCHEMA_FINGERPRINT:
        return None, [
            _issue(
                "FIXTURE_SCHEMA_FINGERPRINT_MISMATCH",
                document,
                "$",
                "schema bytes do not match the reviewed contract",
            )
        ]
    schema, decode_issues = _decode_json_bytes(
        content,
        document=document,
        code_prefix="FIXTURE_SCHEMA",
    )
    if schema is None:
        return None, decode_issues
    reference_issue = _schema_reference_issue(schema, document)
    if reference_issue is not None:
        return None, [reference_issue]
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        return None, [
            _issue(
                "FIXTURE_SCHEMA_INVALID",
                document,
                "$",
                "schema is not valid Draft 2020-12",
            )
        ]
    return schema, []


def validate_fixture_index_document(
    path: Path,
    root: Path = PROJECT_ROOT,
    *,
    schema: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[FixtureIssue]]:
    """Validate one bounded fixture-index document without reading artifact bytes."""

    document = _document_label(path, root)
    loaded, decode_issues = _decode_json(
        path,
        root,
        maximum_bytes=MAX_INDEX_BYTES,
        code_prefix="FIXTURE_INDEX",
    )
    if loaded is None:
        return None, decode_issues
    version = loaded.get("schema_version")
    if isinstance(version, int) and not isinstance(version, bool):
        if version < SUPPORTED_SCHEMA_VERSION:
            return None, [
                _issue(
                    "FIXTURE_INDEX_VERSION_LEGACY",
                    document,
                    "$.schema_version",
                    "legacy index versions are not accepted without an explicit migration",
                )
            ]
        if version > SUPPORTED_SCHEMA_VERSION:
            return None, [
                _issue(
                    "FIXTURE_INDEX_VERSION_FUTURE",
                    document,
                    "$.schema_version",
                    "future index versions are not accepted",
                )
            ]
    selected_schema = schema
    if selected_schema is None:
        selected_schema, schema_issues = load_fixture_schema(root)
        if selected_schema is None:
            return None, schema_issues
    validator = Draft202012Validator(selected_schema)
    schema_errors = sorted(
        validator.iter_errors(loaded),
        key=lambda error: (tuple(str(part) for part in error.absolute_path), error.validator or ""),
    )
    if schema_errors:
        return None, [
            _issue(
                "FIXTURE_INDEX_SCHEMA_INVALID",
                document,
                _json_path(schema_errors[0].absolute_path),
                "index does not match the closed version-1 contract",
            )
        ]
    return loaded, []


def validate_fixture_index_set(
    root: Path = PROJECT_ROOT,
) -> tuple[FixtureIndexSummary | None, list[FixtureIssue]]:
    """Validate the current index plus the exact reviewed negative fixture set."""

    schema, schema_issues = load_fixture_schema(root)
    if schema is None:
        return None, schema_issues
    loaded, positive_issues = validate_fixture_index_document(
        root / INDEX_PATH,
        root,
        schema=schema,
    )
    if loaded is None:
        return None, positive_issues
    negative_root = root / NEGATIVE_FIXTURE_DIRECTORY
    too_many_candidates = False
    try:
        if _path_is_link_or_reparse(negative_root) or not negative_root.is_dir():
            raise OSError("negative fixture directory is unsafe")
        candidates: list[Path] = []
        with os.scandir(negative_root) as entries:
            for entry in entries:
                if len(candidates) >= len(EXPECTED_NEGATIVE_CODES):
                    too_many_candidates = True
                    break
                candidates.append(Path(entry.path))
        candidates.sort(key=lambda path: path.name)
    except OSError as error:
        return None, [
            _issue(
                "FIXTURE_NEGATIVE_SET_UNREADABLE",
                NEGATIVE_FIXTURE_DIRECTORY.as_posix(),
                "$",
                type(error).__name__,
            )
        ]
    if too_many_candidates or {candidate.name for candidate in candidates} != set(
        EXPECTED_NEGATIVE_CODES
    ):
        return None, [
            _issue(
                "FIXTURE_NEGATIVE_SET_MISMATCH",
                NEGATIVE_FIXTURE_DIRECTORY.as_posix(),
                "$",
                "negative fixture inventory does not match the reviewed contract",
            )
        ]
    issues: list[FixtureIssue] = []
    for candidate in candidates:
        data, candidate_issues = validate_fixture_index_document(
            candidate,
            root,
            schema=schema,
        )
        expected_code = EXPECTED_NEGATIVE_CODES[candidate.name]
        if data is not None or {issue.code for issue in candidate_issues} != {expected_code}:
            issues.append(
                _issue(
                    "FIXTURE_NEGATIVE_REASON_MISMATCH",
                    _document_label(candidate, root),
                    "$",
                    "negative fixture failed for an unexpected reason",
                )
            )
    if issues:
        return None, _sorted_issues(issues)
    fixtures = loaded.get("fixtures")
    coverage = loaded.get("coverage")
    if not isinstance(fixtures, list) or not isinstance(coverage, list):
        raise AssertionError("schema accepted invalid fixture index collections")
    return FixtureIndexSummary(len(fixtures), len(coverage), len(candidates)), []


def _enumerate_committed_files(
    directory_root: Path,
    *,
    document: str,
) -> tuple[set[Path] | None, list[FixtureIssue]]:
    try:
        if _path_is_link_or_reparse(directory_root) or not directory_root.is_dir():
            raise OSError("committed directory is unsafe")
        files: set[Path] = set()
        stack = [directory_root]
        entry_count = 0
        while stack:
            directory = stack.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    entry_count += 1
                    if entry_count > MAX_CORPUS_ENTRIES:
                        return None, [
                            _issue(
                                "FIXTURE_SET_LIMIT",
                                document,
                                "$",
                                "committed asset set exceeds its entry-count limit",
                            )
                        ]
                    candidate = Path(entry.path)
                    relative = candidate.relative_to(directory_root)
                    if _path_is_link_or_reparse(candidate):
                        raise OSError("committed asset path is unsafe")
                    for component in relative.parts:
                        if unicodedata.normalize("NFC", component) != component:
                            raise OSError("committed asset path is not normalized")
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(candidate)
                    elif entry.is_file(follow_symlinks=False):
                        files.add(relative)
                    else:
                        raise OSError("committed asset path is not regular")
    except (OSError, RuntimeError, ValueError) as error:
        return None, [_issue("FIXTURE_PATH_UNSAFE", document, "$", type(error).__name__)]
    return files, []


def _looks_like_document(content: bytes) -> bool:
    maximum_signature_bytes = max(len(signature) for signature in _DOCUMENT_SIGNATURES)
    header = content[: 1024 + maximum_signature_bytes - 1]
    return any(0 <= header.find(signature) < 1024 for signature in _DOCUMENT_SIGNATURES)


def _luhn_valid(digits: bytes) -> bool:
    # A leading zero is not a public industry identifier and commonly occurs in PDF xref padding.
    if not 13 <= len(digits) <= 19 or digits[0] == ord("0") or len(set(digits)) == 1:
        return False
    total = 0
    parity = len(digits) % 2
    for index, encoded in enumerate(digits):
        value = encoded - ord("0")
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _plausible_pan_issuer(digits: bytes) -> bool:
    """Recognize reviewed, high-confidence payment-card issuer ranges."""

    length = len(digits)
    first_two = int(digits[:2])
    first_three = int(digits[:3])
    first_four = int(digits[:4])
    first_six = int(digits[:6])
    if length == 15 and digits.startswith(b"1"):  # UATP
        return True
    if length in {13, 16, 19} and digits.startswith(b"4"):  # Visa
        return True
    if length == 16 and (51 <= first_two <= 55 or 2221 <= first_four <= 2720):
        return True  # Mastercard
    if length == 15 and first_two in {34, 37}:
        return True  # American Express
    if 16 <= length <= 19 and 3528 <= first_four <= 3589:
        return True  # JCB
    if length == 14 and (
        300 <= first_three <= 305 or first_four == 3095 or first_two == 36 or 38 <= first_two <= 39
    ):
        return True  # Diners Club
    if length in {16, 19} and (
        first_four == 6011
        or 622126 <= first_six <= 622925
        or 644 <= first_three <= 649
        or first_two == 65
    ):
        return True  # Discover
    return 16 <= length <= 19 and digits.startswith(b"62")  # UnionPay


def _iban_valid(candidate: bytes) -> bool:
    compact = candidate.replace(b" ", b"").decode("ascii").upper()
    if not 15 <= len(compact) <= 34:
        return False
    rearranged = compact[4:] + compact[:4]
    remainder = 0
    for character in rearranged:
        digits = character if character.isdigit() else str(ord(character) - ord("A") + 10)
        for digit in digits:
            remainder = (remainder * 10 + int(digit)) % 97
    return remainder == 1


def _ascii_alphanumeric(value: int) -> bool:
    return (
        ord("0") <= value <= ord("9")
        or ord("A") <= value <= ord("Z")
        or ord("a") <= value <= ord("z")
    )


def _financial_separator_width(content: bytes, position: int) -> int:
    if content[position] in b" \t":
        return 1
    if content.startswith(b"\xc2\xa0", position):
        return 2
    if content.startswith(b"\xe2\x80\xaf", position):
        return 3
    return 0


def _pan_separator_width(content: bytes, position: int) -> int:
    if content[position] == ord("-"):
        return 1
    return _financial_separator_width(content, position)


def _pan_start_is_within_uuid(content: bytes, position: int) -> bool:
    window_start = max(0, position - 36)
    window_end = min(len(content), position + 37)
    return any(
        match.start() <= position < match.end()
        for match in _UUID_PATTERN.finditer(content, window_start, window_end)
    )


def _contains_valid_pan(content: bytes) -> bool:
    candidate_count = 0
    for candidate_start in _PAN_CANDIDATE_START_PATTERN.finditer(content):
        if _pan_start_is_within_uuid(content, candidate_start.start()):
            continue
        digits = bytearray()
        position = candidate_start.start()
        candidate_counted = False
        while len(digits) < 19:
            group_match = _PAN_DIGIT_GROUP_PATTERN.match(content, position)
            if group_match is None:
                break
            group_start, group_end = group_match.span(0)
            if len(digits) + group_end - group_start > 19:
                break
            digits.extend(content[group_start:group_end])
            position = group_end
            ends_at_boundary = position == len(content) or not _ascii_alphanumeric(
                content[position]
            )
            compact = bytes(digits)
            if len(compact) >= 13 and not candidate_counted:
                candidate_count += 1
                candidate_counted = True
                if candidate_count > _MAX_PAN_CANDIDATE_STARTS:
                    return True
            if (
                ends_at_boundary
                and 13 <= len(compact) <= 19
                and _plausible_pan_issuer(compact)
                and _luhn_valid(compact)
            ):
                return True

            if len(digits) == 19:
                break
            separator_start = position
            while position < len(content):
                separator_width = _pan_separator_width(content, position)
                if separator_width == 0:
                    break
                position += separator_width
            if (
                position == separator_start
                or position == len(content)
                or not ord("0") <= content[position] <= ord("9")
            ):
                break
    return False


def _contains_valid_iban(content: bytes) -> bool:
    for match in _IBAN_START_PATTERN.finditer(content):
        expected_length = _IBAN_LENGTHS.get(match.group(0)[:2].upper())
        if expected_length is None:
            continue
        compact = bytearray(match.group(0))
        position = match.end()
        while len(compact) < expected_length and position < len(content):
            if _ascii_alphanumeric(content[position]):
                compact.append(content[position])
                position += 1
                continue
            separator_width = _financial_separator_width(content, position)
            next_position = position + separator_width
            if (
                separator_width == 0
                or next_position == len(content)
                or not _ascii_alphanumeric(content[next_position])
            ):
                break
            position = next_position
        if (
            len(compact) == expected_length
            and (position == len(content) or not _ascii_alphanumeric(content[position]))
            and _iban_valid(bytes(compact))
        ):
            return True
    return False


def _is_reserved_email_domain(domain: bytes) -> bool:
    if domain.endswith(_RESERVED_EMAIL_SUFFIXES):
        return True
    return any(
        domain == reserved or domain.endswith(b"." + reserved)
        for reserved in _RESERVED_EMAIL_DOMAINS
    )


def _contains_prohibited_pattern(content: bytes) -> bool:
    for match in _EMAIL_PATTERN.finditer(content):
        domain = match.group(0).split(b"@", maxsplit=1)[1].lower()
        if not _is_reserved_email_domain(domain):
            return True
    if _SSN_PATTERN.search(content) is not None or _MRZ_PATTERN.search(content) is not None:
        return True
    return _contains_valid_pan(content) or _contains_valid_iban(content)


def _repository_inspector(
    approved_document_digests: frozenset[bytes],
) -> Callable[[bytes], None]:
    def inspect(content: bytes) -> None:
        if _looks_like_document(content):
            digest = hashlib.sha256(content).digest()
            if digest not in approved_document_digests:
                raise RepositoryPrivacyFinding(
                    "FIXTURE_UNINDEXED_DOCUMENT",
                    "repository snapshot contains an unapproved document or image blob",
                )
        if _contains_prohibited_pattern(content):
            raise RepositoryPrivacyFinding(
                "FIXTURE_PRIVACY_PATTERN",
                "repository snapshot contains a prohibited data-shaped pattern",
            )

    return inspect


def _validate_renderer_goldens(
    root: Path,
    generated: Sequence[GeneratedFixture],
) -> tuple[int | None, list[FixtureIssue]]:
    references = build_renderer_golden_references(generated)
    golden_root = root / GOLDEN_DIRECTORY
    actual_files, inventory_issues = _enumerate_committed_files(
        golden_root,
        document=GOLDEN_DIRECTORY.as_posix(),
    )
    if actual_files is None:
        return None, inventory_issues
    expected_files = {Path("index.json")}
    expected_files.update(Path(*reference.relative_path.parts) for reference in references)
    if actual_files != expected_files:
        return None, [
            _issue(
                "FIXTURE_GOLDEN_SET_MISMATCH",
                GOLDEN_DIRECTORY.as_posix(),
                "$",
                "renderer golden inventory differs from the analytical generator",
            )
        ]
    index_content, index_issues = _read_regular_bytes(
        root / GOLDEN_INDEX_PATH,
        trusted_root=root,
        maximum_bytes=MAX_INDEX_BYTES,
        document=GOLDEN_INDEX_PATH.as_posix(),
        code_prefix="FIXTURE_GOLDEN_INDEX",
    )
    if index_content is None:
        return None, index_issues
    if index_content != canonical_renderer_golden_index_bytes(generated):
        return None, [
            _issue(
                "FIXTURE_GOLDEN_INDEX_MISMATCH",
                GOLDEN_INDEX_PATH.as_posix(),
                "$",
                "renderer golden index differs from the analytical generator",
            )
        ]
    for reference in references:
        relative = Path(*reference.relative_path.parts)
        document = (GOLDEN_DIRECTORY / relative).as_posix()
        content, content_issues = _read_regular_bytes(
            golden_root / relative,
            trusted_root=root,
            maximum_bytes=MAX_FIXTURE_BYTES,
            document=document,
            code_prefix="FIXTURE_GOLDEN_CONTENT",
        )
        if content is None:
            return None, content_issues
        if content != reference.content:
            return None, [
                _issue(
                    "FIXTURE_GOLDEN_CONTENT_MISMATCH",
                    document,
                    "$",
                    "renderer golden bytes differ from their analytical corpus mirror",
                )
            ]
    return len(references), []


def validate_fixture_corpus(
    root: Path = PROJECT_ROOT,
    *,
    inspect_repository: bool = True,
) -> tuple[FixtureCorpusSummary | None, list[FixtureIssue]]:
    """Validate exact bytes and optionally the hardened repository content snapshot."""

    _, index_issues = validate_fixture_index_set(root)
    if index_issues:
        return None, index_issues
    loaded, load_issues = validate_fixture_index_document(root / INDEX_PATH, root)
    if loaded is None:
        return None, load_issues
    generated = build_committed_fixtures()
    canonical = build_index(generated)
    if loaded != canonical:
        return None, [
            _issue(
                "FIXTURE_INDEX_CANONICAL_MISMATCH",
                INDEX_PATH.as_posix(),
                "$",
                "index metadata differs from deterministic generator output",
            )
        ]
    index_content, index_read_issues = _read_regular_bytes(
        root / INDEX_PATH,
        trusted_root=root,
        maximum_bytes=MAX_INDEX_BYTES,
        document=INDEX_PATH.as_posix(),
        code_prefix="FIXTURE_INDEX",
    )
    if index_content is None:
        return None, index_read_issues
    if index_content != canonical_index_bytes(generated):
        return None, [
            _issue(
                "FIXTURE_INDEX_ENCODING_MISMATCH",
                INDEX_PATH.as_posix(),
                "$",
                "index is not in canonical UTF-8 JSON form",
            )
        ]

    corpus_root = root / CORPUS_DIRECTORY
    actual_files, inventory_issues = _enumerate_committed_files(
        corpus_root,
        document=CORPUS_DIRECTORY.as_posix(),
    )
    if actual_files is None:
        return None, inventory_issues
    expected_files = {Path("index.json"), Path("invalid")}
    expected_files.update(Path(*fixture.relative_path.parts) for fixture in generated)
    actual_regular_files = {
        path for path in actual_files if not path.parts or path.parts[0] != "invalid"
    }
    expected_regular_files = expected_files - {Path("invalid")}
    if actual_regular_files != expected_regular_files:
        return None, [
            _issue(
                "FIXTURE_SET_MISMATCH",
                CORPUS_DIRECTORY.as_posix(),
                "$",
                "committed corpus inventory differs from the reviewed generator",
            )
        ]

    total_bytes = 0
    approved_digests: set[bytes] = set()
    for fixture in generated:
        relative = Path(*fixture.relative_path.parts)
        content, read_issues = _read_regular_bytes(
            corpus_root / relative,
            trusted_root=root,
            maximum_bytes=MAX_FIXTURE_BYTES,
            document=(CORPUS_DIRECTORY / relative).as_posix(),
            code_prefix="FIXTURE_CONTENT",
        )
        if content is None:
            return None, read_issues
        if content != fixture.content:
            return None, [
                _issue(
                    "FIXTURE_CONTENT_MISMATCH",
                    (CORPUS_DIRECTORY / relative).as_posix(),
                    "$",
                    "fixture bytes differ from deterministic generator output",
                )
            ]
        if SYNTHETIC_MARKER.encode("ascii") not in content:
            return None, [
                _issue(
                    "FIXTURE_MARKER_MISSING",
                    (CORPUS_DIRECTORY / relative).as_posix(),
                    "$",
                    "fixture lacks the reviewed public-synthetic marker",
                )
            ]
        if _contains_prohibited_pattern(content):
            return None, [
                _issue(
                    "FIXTURE_PRIVACY_PATTERN",
                    (CORPUS_DIRECTORY / relative).as_posix(),
                    "$",
                    "fixture contains a prohibited data-shaped pattern",
                )
            ]
        total_bytes += len(content)
        approved_digests.add(hashlib.sha256(content).digest())

    golden_reference_count, golden_issues = _validate_renderer_goldens(root, generated)
    if golden_reference_count is None:
        return None, golden_issues
    transform_golden_count, transform_golden_codes = validate_committed_golden_set(root)
    if transform_golden_codes:
        return None, [
            _issue(
                code,
                IMAGE_TRANSFORM_GOLDEN_DIRECTORY.as_posix(),
                "$",
                "image-transform golden validation failed closed",
            )
            for code in transform_golden_codes
        ]
    for plan in IMAGE_TRANSFORM_GOLDEN_PLANS:
        content, content_issues = _read_regular_bytes(
            root / IMAGE_TRANSFORM_GOLDEN_DIRECTORY / plan.output_name,
            trusted_root=root,
            maximum_bytes=MAX_FIXTURE_BYTES,
            document=(IMAGE_TRANSFORM_GOLDEN_DIRECTORY / plan.output_name).as_posix(),
            code_prefix="FIXTURE_IMAGE_TRANSFORM_GOLDEN",
        )
        if content is None:
            return None, content_issues
        approved_digests.add(hashlib.sha256(content).digest())

    history_blob_count = 0
    index_blob_count = 0
    working_file_count = 0
    if inspect_repository:
        try:
            repository_summary = inspect_repository_contents(
                root,
                _repository_inspector(frozenset(approved_digests)),
            )
        except RepositoryPrivacyFinding as error:
            return None, [_issue(error.code, "repository-snapshot", "$", error.message)]
        except SecretScanError as error:
            return None, [
                _issue(
                    "FIXTURE_REPOSITORY_SCAN_FAILED",
                    "repository-snapshot",
                    "$",
                    f"repository snapshot failed with {error.code}",
                )
            ]
        history_blob_count = repository_summary.history_blob_count
        index_blob_count = repository_summary.index_blob_count
        working_file_count = repository_summary.tracked_count + repository_summary.untracked_count

    return (
        FixtureCorpusSummary(
            fixture_count=len(generated),
            golden_reference_count=golden_reference_count,
            image_transform_golden_reference_count=transform_golden_count,
            total_fixture_bytes=total_bytes,
            repository_history_blob_count=history_blob_count,
            repository_index_blob_count=index_blob_count,
            repository_working_file_count=working_file_count,
        ),
        [],
    )


def validation_messages(root: Path = PROJECT_ROOT) -> list[str]:
    """Return schema-registry diagnostics without requiring a Git repository."""

    _, issues = validate_fixture_index_set(root)
    return [issue.render() for issue in issues]


def main(argv: Sequence[str] | None = None) -> int:
    """Validate the complete checked-in corpus and repository privacy boundary."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT, help=argparse.SUPPRESS)
    parser.add_argument(
        "--index-only",
        action="store_true",
        help="validate only the schema/index fixture contract",
    )
    arguments = parser.parse_args(argv)
    summary: FixtureIndexSummary | FixtureCorpusSummary | None
    issues: list[FixtureIssue]
    if arguments.index_only:
        summary, issues = validate_fixture_index_set(arguments.root)
    else:
        summary, issues = validate_fixture_corpus(arguments.root)
    if issues:
        for issue in issues:
            print(issue.render(), file=sys.stderr)
        return 1
    if isinstance(summary, FixtureIndexSummary):
        print(
            "OK: validated synthetic fixture index v1 with "
            f"{summary.fixture_count} fixtures, {summary.coverage_count} coverage entries, "
            f"and {summary.negative_count} negative examples."
        )
    elif isinstance(summary, FixtureCorpusSummary):
        print(
            "OK: validated "
            f"{summary.fixture_count} deterministic public synthetic fixtures "
            f"and {summary.golden_reference_count} analytical renderer references "
            f"plus {summary.image_transform_golden_reference_count} approved image-transform "
            "references "
            f"({summary.total_fixture_bytes} corpus bytes), "
            f"{summary.repository_history_blob_count} historical blobs, "
            f"{summary.repository_index_blob_count} index blobs, and "
            f"{summary.repository_working_file_count} working files."
        )
    else:
        raise AssertionError("successful fixture validation must return a summary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Validate the synthetic corpus, provenance, determinism, and repository boundary."""

from __future__ import annotations

import argparse
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
from typing import Any, Final, NoReturn, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from tools.dev import SecretScanError, inspect_repository_contents
from tools.generate_synthetic_fixtures import (
    CORPUS_DIRECTORY,
    INDEX_PATH,
    PROJECT_ROOT,
    SCHEMA_PATH,
    SYNTHETIC_MARKER,
    build_committed_fixtures,
    build_index,
    canonical_index_bytes,
)

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
    rb"[A-Z0-9.-]{1,190}\.[A-Z]{2,24}(?![A-Z0-9.-])"
)
_SSN_PATTERN: Final = re.compile(rb"(?<![0-9])([0-9]{3})-([0-9]{2})-([0-9]{4})(?![0-9])")
_PAN_PATTERN: Final = re.compile(rb"(?<![A-Za-z0-9])(?:[0-9][ -]?){12,18}[0-9](?![A-Za-z0-9])")
_IBAN_PATTERN: Final = re.compile(
    rb"(?i)(?<![A-Z0-9])[A-Z]{2}[0-9]{2}(?: ?[A-Z0-9]){11,30}(?![A-Z0-9])"
)
_MRZ_PATTERN: Final = re.compile(rb"(?m)^P<[A-Z<]{3}[A-Z<]{20,40}$")
_DOCUMENT_SIGNATURES: Final = (
    b"%PDF-",
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"II*\x00",
    b"MM\x00*",
)
_RESERVED_EMAIL_SUFFIXES: Final = (b".invalid", b".example", b".test", b".localhost")

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


def _read_regular_bytes(
    path: Path,
    *,
    maximum_bytes: int,
    document: str,
    code_prefix: str,
) -> tuple[bytes | None, list[FixtureIssue]]:
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


def _enumerate_corpus_files(corpus_root: Path) -> tuple[set[Path] | None, list[FixtureIssue]]:
    document = CORPUS_DIRECTORY.as_posix()
    try:
        if _path_is_link_or_reparse(corpus_root) or not corpus_root.is_dir():
            raise OSError("corpus directory is unsafe")
        files: set[Path] = set()
        stack = [corpus_root]
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
                                "corpus exceeds its entry-count limit",
                            )
                        ]
                    candidate = Path(entry.path)
                    relative = candidate.relative_to(corpus_root)
                    if _path_is_link_or_reparse(candidate):
                        raise OSError("corpus path is unsafe")
                    for component in relative.parts:
                        if unicodedata.normalize("NFC", component) != component:
                            raise OSError("corpus path is not normalized")
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(candidate)
                    elif entry.is_file(follow_symlinks=False):
                        files.add(relative)
                    else:
                        raise OSError("corpus path is not regular")
    except (OSError, RuntimeError, ValueError) as error:
        return None, [_issue("FIXTURE_PATH_UNSAFE", document, "$", type(error).__name__)]
    return files, []


def _looks_like_document(content: bytes) -> bool:
    header = content[:1024]
    if b"%PDF-" in header:
        return True
    if header.startswith(b"\xef\xbb\xbf"):
        header = header[3:]
    normalized = header.lstrip(b" \t\r\n\f\v")
    return any(
        normalized.startswith(signature)
        for signature in _DOCUMENT_SIGNATURES
        if signature != b"%PDF-"
    )


def _luhn_valid(digits: bytes) -> bool:
    if (
        len(digits) not in {13, 14, 15, 16, 19}
        or digits[0] not in b"23456"
        or len(set(digits)) == 1
    ):
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


def _contains_prohibited_pattern(content: bytes) -> bool:
    for match in _EMAIL_PATTERN.finditer(content):
        domain = match.group(0).split(b"@", maxsplit=1)[1].lower()
        if not domain.endswith(_RESERVED_EMAIL_SUFFIXES):
            return True
    if _SSN_PATTERN.search(content) is not None or _MRZ_PATTERN.search(content) is not None:
        return True
    for match in _PAN_PATTERN.finditer(content):
        digits = bytes(value for value in match.group(0) if ord("0") <= value <= ord("9"))
        if _luhn_valid(digits):
            return True
    return any(_iban_valid(match.group(0)) for match in _IBAN_PATTERN.finditer(content))


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
    actual_files, inventory_issues = _enumerate_corpus_files(corpus_root)
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
            f"({summary.total_fixture_bytes} bytes), "
            f"{summary.repository_history_blob_count} historical blobs, "
            f"{summary.repository_index_blob_count} index blobs, and "
            f"{summary.repository_working_file_count} working files."
        )
    else:
        raise AssertionError("successful fixture validation must return a summary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

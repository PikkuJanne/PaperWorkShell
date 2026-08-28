"""Validate the reviewed generic-portal template and synthetic packet demonstrations."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Final, NoReturn, cast

from tools.validate_templates import validate_template_document

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
CANONICAL_TEMPLATE_PATH: Final = Path("templates/builtin/generic-portal-application-v1.1.0.json")
PACKAGED_TEMPLATE_PATH: Final = Path(
    "src/paperworkshell/templates/builtin/generic-portal-application-v1.1.0.json"
)
PACKET_FIXTURE_PATH: Final = Path("tests/fixtures/templates/v1/generic-portal-packets.json")
SYNTHETIC_INDEX_PATH: Final = Path("tests/fixtures/synthetic/v1/index.json")

EXPECTED_TEMPLATE_ID: Final = "generic-portal-application"
EXPECTED_TEMPLATE_VERSION: Final = "1.1.0"
EXPECTED_TEMPLATE_BYTES: Final = 7_145
EXPECTED_TEMPLATE_SHA256: Final = "f06ca149a4ff049de49a8a671352e43527900287dd898c7a49e24871d1b6d9bd"
TEMPLATE_REVIEW_AS_OF: Final = date(2026, 8, 28)
EXPECTED_PACKET_SET_ID: Final = "generic-portal-packets-v1"
EXPECTED_SCENARIO_IDS: Final = ("complete", "missing", "excessive")
EXPECTED_REQUIREMENT_LIMITS: Final[dict[str, tuple[int, int]]] = {
    "application-form": (1, 1),
    "identity-evidence": (1, 2),
    "address-evidence": (1, 2),
    "supporting-evidence": (1, 10),
}
EXPECTED_SCENARIO_COUNTS: Final[dict[str, dict[str, int]]] = {
    "complete": {
        "application-form": 1,
        "identity-evidence": 1,
        "address-evidence": 1,
        "supporting-evidence": 1,
    },
    "missing": {
        "application-form": 1,
        "identity-evidence": 1,
        "address-evidence": 0,
        "supporting-evidence": 1,
    },
    "excessive": {
        "application-form": 1,
        "identity-evidence": 1,
        "address-evidence": 1,
        "supporting-evidence": 11,
    },
}
APPROVED_PACKET_FIXTURE_IDS: Final = frozenset(
    {
        "pdf-born-digital",
        "pdf-page-geometry",
        "png-ocr-raster-text",
        "pdf-mixed",
        "pdf-image-only",
        "pdf-ocr-raster-text",
        "pdf-ocr-readable-mixed",
        "pdf-render-calibration",
        "pdf-render-cmyk",
        "pdf-render-colorspaces",
        "pdf-render-embedded-type3-font",
        "pdf-render-geometry-matrix",
        "pdf-render-transparency",
        "pdf-multilingual-tounicode",
    }
)

MAX_TEMPLATE_BYTES: Final = 64 * 1024
MAX_PACKET_BYTES: Final = 64 * 1024
MAX_INDEX_BYTES: Final = 1024 * 1024
MAX_APPROVED_FIXTURE_BYTES: Final = 16 * 1024 * 1024
MAX_JSON_DEPTH: Final = 32
MAX_JSON_NODES: Final = 32_768
MAX_JSON_COLLECTION_ITEMS: Final = 4_096
MAX_JSON_STRING_CODEPOINTS: Final = 8_192
MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991
MAX_SCENARIO_DOCUMENTS: Final = 32
MAX_REPORTED_ISSUES: Final = 128
_IDENTIFIER: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_REPARSE_POINT_ATTRIBUTE: Final = 0x400

_PACKET_ROOT_KEYS: Final = frozenset({"schema_version", "packet_set_id", "template", "scenarios"})
_TEMPLATE_REFERENCE_KEYS: Final = frozenset(
    {"template_id", "template_version", "byte_size", "sha256"}
)
_SCENARIO_KEYS: Final = frozenset({"scenario_id", "documents", "expected_findings"})
_DOCUMENT_KEYS: Final = frozenset({"document_id", "fixture_id", "requirement_id"})
_FINDING_KEYS: Final = frozenset({"code", "requirement_id", "observed_count", "minimum", "maximum"})


@dataclass(frozen=True, order=True)
class GenericPortalIssue:
    """One deterministic, non-disclosing validation error."""

    document: str
    path: str
    code: str
    message: str

    def render(self) -> str:
        return f"ERROR [{self.code}] {self.document}#{self.path}: {self.message}"


@dataclass(frozen=True)
class PacketFinding:
    """One count-only result produced from a synthetic packet scenario."""

    code: str
    requirement_id: str
    observed_count: int
    minimum: int
    maximum: int


@dataclass(frozen=True)
class GenericPortalValidationSummary:
    """Summary of the reviewed artifact and count-only demonstrations."""

    scenario_count: int
    document_count: int
    approved_fixture_count: int
    findings_by_scenario: tuple[tuple[str, tuple[PacketFinding, ...]], ...]


class DuplicateJsonKeyError(ValueError):
    """Raised when a JSON object repeats a member name."""


class InvalidJsonNumberError(ValueError):
    """Raised for floating-point, non-standard, or unsafe JSON numbers."""


def _issue(code: str, document: Path, path: str, message: str) -> GenericPortalIssue:
    return GenericPortalIssue(document.as_posix(), path, code, message)


def _add_issue(
    issues: list[GenericPortalIssue],
    issue: GenericPortalIssue,
) -> None:
    if len(issues) < MAX_REPORTED_ISSUES:
        issues.append(issue)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError
        result[key] = value
    return result


def _reject_json_number(_value: str) -> NoReturn:
    raise InvalidJsonNumberError


def _parse_bounded_integer(value: str) -> int:
    digits = value[1:] if value.startswith("-") else value
    if len(digits) > 16:
        raise InvalidJsonNumberError
    parsed = int(value, 10)
    if not -MAX_SAFE_INTEGER <= parsed <= MAX_SAFE_INTEGER:
        raise InvalidJsonNumberError
    return parsed


def _read_bounded_bytes(
    path: Path,
    *,
    document: Path,
    maximum_bytes: int,
    code_prefix: str,
) -> tuple[bytes | None, list[GenericPortalIssue]]:
    try:
        with path.open("rb") as stream:
            content = stream.read(maximum_bytes + 1)
    except OSError as error:
        return None, [
            _issue(
                f"{code_prefix}_INPUT_UNREADABLE",
                document,
                "$",
                type(error).__name__,
            )
        ]
    if len(content) > maximum_bytes:
        return None, [
            _issue(
                f"{code_prefix}_INPUT_TOO_LARGE",
                document,
                "$",
                "input exceeds its byte limit",
            )
        ]
    return content, []


def _resource_issue(
    value: object,
    *,
    document: Path,
    code_prefix: str,
) -> GenericPortalIssue | None:
    queue: deque[tuple[object, int]] = deque([(value, 1)])
    nodes = 0
    while queue:
        current, depth = queue.popleft()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            return _issue(
                f"{code_prefix}_JSON_RESOURCE_LIMIT",
                document,
                "$",
                "JSON exceeds its structural resource limits",
            )
        if isinstance(current, Mapping):
            if len(current) > MAX_JSON_COLLECTION_ITEMS:
                return _issue(
                    f"{code_prefix}_JSON_RESOURCE_LIMIT",
                    document,
                    "$",
                    "JSON object exceeds its member limit",
                )
            for key, child in current.items():
                if not isinstance(key, str):
                    return _issue(
                        f"{code_prefix}_JSON_VALUE_INVALID",
                        document,
                        "$",
                        "JSON member names must be strings",
                    )
                if len(key) > MAX_JSON_STRING_CODEPOINTS:
                    return _issue(
                        f"{code_prefix}_JSON_RESOURCE_LIMIT",
                        document,
                        "$",
                        "JSON text exceeds its string limit",
                    )
                queue.append((child, depth + 1))
        elif isinstance(current, list):
            if len(current) > MAX_JSON_COLLECTION_ITEMS:
                return _issue(
                    f"{code_prefix}_JSON_RESOURCE_LIMIT",
                    document,
                    "$",
                    "JSON array exceeds its item limit",
                )
            queue.extend((child, depth + 1) for child in current)
        elif isinstance(current, str):
            if len(current) > MAX_JSON_STRING_CODEPOINTS:
                return _issue(
                    f"{code_prefix}_JSON_RESOURCE_LIMIT",
                    document,
                    "$",
                    "JSON text exceeds its string limit",
                )
        elif current is not None and type(current) not in {bool, int}:
            return _issue(
                f"{code_prefix}_JSON_VALUE_INVALID",
                document,
                "$",
                "decoded value is outside the JSON data model",
            )
    return None


def _load_json_object(
    path: Path,
    *,
    document: Path,
    maximum_bytes: int,
    code_prefix: str,
) -> tuple[dict[str, object] | None, list[GenericPortalIssue]]:
    content, issues = _read_bounded_bytes(
        path,
        document=document,
        maximum_bytes=maximum_bytes,
        code_prefix=code_prefix,
    )
    if content is None:
        return None, issues
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None, [
            _issue(
                f"{code_prefix}_TEXT_ENCODING_INVALID",
                document,
                "$",
                "input must be valid UTF-8",
            )
        ]
    try:
        loaded = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_number,
            parse_float=_reject_json_number,
            parse_int=_parse_bounded_integer,
        )
    except DuplicateJsonKeyError:
        return None, [
            _issue(
                f"{code_prefix}_JSON_DUPLICATE_KEY",
                document,
                "$",
                "object member names must be unique",
            )
        ]
    except InvalidJsonNumberError:
        return None, [
            _issue(
                f"{code_prefix}_JSON_NUMBER_INVALID",
                document,
                "$",
                "floating-point, non-standard, and unsafe numbers are not permitted",
            )
        ]
    except json.JSONDecodeError, RecursionError, ValueError:
        return None, [
            _issue(
                f"{code_prefix}_JSON_INVALID",
                document,
                "$",
                "input is not valid bounded JSON",
            )
        ]
    if not isinstance(loaded, dict):
        return None, [
            _issue(
                f"{code_prefix}_JSON_ROOT_INVALID",
                document,
                "$",
                "root value must be an object",
            )
        ]
    result = cast(dict[str, object], loaded)
    limit_issue = _resource_issue(result, document=document, code_prefix=code_prefix)
    if limit_issue is not None:
        return None, [limit_issue]
    return result, []


def _exact_keys(value: Mapping[str, object], expected: frozenset[str]) -> bool:
    return set(value) == expected


def _is_identifier(value: object) -> bool:
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None


def _validate_artifact_identity(
    root: Path,
) -> tuple[dict[str, object] | None, list[GenericPortalIssue]]:
    documents = (CANONICAL_TEMPLATE_PATH, PACKAGED_TEMPLATE_PATH)
    loaded_bytes: list[bytes] = []
    issues: list[GenericPortalIssue] = []
    for document in documents:
        content, read_issues = _read_bounded_bytes(
            root / document,
            document=document,
            maximum_bytes=MAX_TEMPLATE_BYTES,
            code_prefix="GENERIC_TEMPLATE",
        )
        issues.extend(read_issues)
        if content is None:
            continue
        loaded_bytes.append(content)
        if len(content) != EXPECTED_TEMPLATE_BYTES:
            _add_issue(
                issues,
                _issue(
                    "GENERIC_TEMPLATE_SIZE_MISMATCH",
                    document,
                    "$",
                    "artifact byte size does not match the reviewed identity",
                ),
            )
        if hashlib.sha256(content).hexdigest() != EXPECTED_TEMPLATE_SHA256:
            _add_issue(
                issues,
                _issue(
                    "GENERIC_TEMPLATE_DIGEST_MISMATCH",
                    document,
                    "$",
                    "artifact digest does not match the reviewed identity",
                ),
            )
    if len(loaded_bytes) == 2 and loaded_bytes[0] != loaded_bytes[1]:
        _add_issue(
            issues,
            _issue(
                "GENERIC_TEMPLATE_COPY_MISMATCH",
                PACKAGED_TEMPLATE_PATH,
                "$",
                "packaged artifact is not byte-identical to the canonical artifact",
            ),
        )
    if issues:
        return None, sorted(issues)
    template, contract_issues = validate_template_document(
        root / CANONICAL_TEMPLATE_PATH,
        root,
        as_of=TEMPLATE_REVIEW_AS_OF,
    )
    if contract_issues:
        return None, [
            _issue(
                "GENERIC_TEMPLATE_CONTRACT_INVALID",
                CANONICAL_TEMPLATE_PATH,
                issue.path,
                f"declarative-template v1 validation failed with {issue.code}",
            )
            for issue in contract_issues
        ]
    return cast(dict[str, object], template), []


def _requirement_limits(
    template: Mapping[str, object],
) -> tuple[dict[str, tuple[int, int]] | None, list[GenericPortalIssue]]:
    if (
        template.get("template_id") != EXPECTED_TEMPLATE_ID
        or template.get("template_version") != EXPECTED_TEMPLATE_VERSION
    ):
        return None, [
            _issue(
                "GENERIC_TEMPLATE_REFERENCE_MISMATCH",
                CANONICAL_TEMPLATE_PATH,
                "$",
                "template identifier or version does not match the reviewed artifact",
            )
        ]
    requirements = template.get("requirements")
    if not isinstance(requirements, list) or len(requirements) != len(EXPECTED_REQUIREMENT_LIMITS):
        return None, [
            _issue(
                "GENERIC_TEMPLATE_REQUIREMENTS_INVALID",
                CANONICAL_TEMPLATE_PATH,
                "$.requirements",
                "reviewed requirement inventory is invalid",
            )
        ]
    limits: dict[str, tuple[int, int]] = {}
    for requirement in requirements:
        if not isinstance(requirement, Mapping):
            break
        requirement_id = requirement.get("requirement_id")
        count = requirement.get("count")
        if not isinstance(requirement_id, str) or not isinstance(count, Mapping):
            break
        minimum = count.get("minimum")
        maximum = count.get("maximum")
        if (
            count.get("unit") != "documents"
            or type(minimum) is not int
            or type(maximum) is not int
            or requirement_id in limits
        ):
            break
        limits[requirement_id] = (minimum, maximum)
    if limits != EXPECTED_REQUIREMENT_LIMITS:
        return None, [
            _issue(
                "GENERIC_TEMPLATE_REQUIREMENTS_INVALID",
                CANONICAL_TEMPLATE_PATH,
                "$.requirements",
                "reviewed document-count requirements do not match the pinned contract",
            )
        ]
    return limits, []


def _validate_packet_shape(
    packet: Mapping[str, object],
) -> list[GenericPortalIssue]:
    document = PACKET_FIXTURE_PATH
    issues: list[GenericPortalIssue] = []
    if not _exact_keys(packet, _PACKET_ROOT_KEYS):
        return [
            _issue(
                "GENERIC_PACKET_SHAPE_INVALID",
                document,
                "$",
                "packet root must contain exactly the reviewed members",
            )
        ]
    if packet.get("schema_version") != 1 or packet.get("packet_set_id") != EXPECTED_PACKET_SET_ID:
        _add_issue(
            issues,
            _issue(
                "GENERIC_PACKET_IDENTITY_MISMATCH",
                document,
                "$",
                "packet schema version or set identifier is not supported",
            ),
        )
    reference = packet.get("template")
    if not isinstance(reference, Mapping) or not _exact_keys(reference, _TEMPLATE_REFERENCE_KEYS):
        _add_issue(
            issues,
            _issue(
                "GENERIC_PACKET_TEMPLATE_REFERENCE_INVALID",
                document,
                "$.template",
                "template reference must contain exactly the reviewed members",
            ),
        )
    elif (
        reference.get("template_id") != EXPECTED_TEMPLATE_ID
        or reference.get("template_version") != EXPECTED_TEMPLATE_VERSION
        or reference.get("byte_size") != EXPECTED_TEMPLATE_BYTES
        or reference.get("sha256") != EXPECTED_TEMPLATE_SHA256
    ):
        _add_issue(
            issues,
            _issue(
                "GENERIC_PACKET_TEMPLATE_REFERENCE_MISMATCH",
                document,
                "$.template",
                "template reference does not match the reviewed artifact identity",
            ),
        )
    scenarios = packet.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) != len(EXPECTED_SCENARIO_IDS):
        _add_issue(
            issues,
            _issue(
                "GENERIC_PACKET_SCENARIO_SET_MISMATCH",
                document,
                "$.scenarios",
                "scenario inventory must contain exactly three reviewed cases",
            ),
        )
        return sorted(issues)
    scenario_ids: list[str] = []
    for scenario_index, scenario in enumerate(scenarios):
        scenario_path = f"$.scenarios[{scenario_index}]"
        if not isinstance(scenario, Mapping) or not _exact_keys(scenario, _SCENARIO_KEYS):
            _add_issue(
                issues,
                _issue(
                    "GENERIC_PACKET_SCENARIO_SHAPE_INVALID",
                    document,
                    scenario_path,
                    "scenario must contain exactly the reviewed members",
                ),
            )
            continue
        scenario_id = scenario.get("scenario_id")
        documents = scenario.get("documents")
        expected_findings = scenario.get("expected_findings")
        if not isinstance(scenario_id, str):
            _add_issue(
                issues,
                _issue(
                    "GENERIC_PACKET_SCENARIO_ID_INVALID",
                    document,
                    f"{scenario_path}.scenario_id",
                    "scenario identifier must be text",
                ),
            )
            continue
        scenario_ids.append(scenario_id)
        if not isinstance(documents, list) or not 1 <= len(documents) <= MAX_SCENARIO_DOCUMENTS:
            _add_issue(
                issues,
                _issue(
                    "GENERIC_PACKET_DOCUMENT_LIMIT",
                    document,
                    f"{scenario_path}.documents",
                    "scenario document count is outside the reviewed bound",
                ),
            )
            continue
        document_ids: set[str] = set()
        fixture_ids: set[str] = set()
        for item_index, item in enumerate(documents):
            item_path = f"{scenario_path}.documents[{item_index}]"
            if not isinstance(item, Mapping) or not _exact_keys(item, _DOCUMENT_KEYS):
                _add_issue(
                    issues,
                    _issue(
                        "GENERIC_PACKET_DOCUMENT_SHAPE_INVALID",
                        document,
                        item_path,
                        "document mapping must contain exactly the reviewed members",
                    ),
                )
                continue
            document_id = item.get("document_id")
            fixture_id = item.get("fixture_id")
            requirement_id = item.get("requirement_id")
            if not all(
                _is_identifier(value) for value in (document_id, fixture_id, requirement_id)
            ):
                _add_issue(
                    issues,
                    _issue(
                        "GENERIC_PACKET_DOCUMENT_VALUE_INVALID",
                        document,
                        item_path,
                        "document mapping identifiers must use the bounded identifier form",
                    ),
                )
                continue
            assert isinstance(document_id, str)
            assert isinstance(fixture_id, str)
            assert isinstance(requirement_id, str)
            if document_id in document_ids or fixture_id in fixture_ids:
                _add_issue(
                    issues,
                    _issue(
                        "GENERIC_PACKET_DOCUMENT_DUPLICATE",
                        document,
                        item_path,
                        "document and fixture identifiers must be unique within a scenario",
                    ),
                )
            document_ids.add(document_id)
            fixture_ids.add(fixture_id)
            if fixture_id not in APPROVED_PACKET_FIXTURE_IDS:
                _add_issue(
                    issues,
                    _issue(
                        "GENERIC_PACKET_FIXTURE_UNAPPROVED",
                        document,
                        f"{item_path}.fixture_id",
                        "fixture identifier is not in the reviewed packet allowlist",
                    ),
                )
            if requirement_id not in EXPECTED_REQUIREMENT_LIMITS:
                _add_issue(
                    issues,
                    _issue(
                        "GENERIC_PACKET_REQUIREMENT_UNKNOWN",
                        document,
                        f"{item_path}.requirement_id",
                        "requirement identifier is not present in the reviewed template",
                    ),
                )
        if not isinstance(expected_findings, list) or len(expected_findings) > len(
            EXPECTED_REQUIREMENT_LIMITS
        ):
            _add_issue(
                issues,
                _issue(
                    "GENERIC_PACKET_FINDINGS_INVALID",
                    document,
                    f"{scenario_path}.expected_findings",
                    "expected findings must be a bounded array",
                ),
            )
            continue
        for finding_index, finding in enumerate(expected_findings):
            finding_path = f"{scenario_path}.expected_findings[{finding_index}]"
            if not isinstance(finding, Mapping) or not _exact_keys(finding, _FINDING_KEYS):
                _add_issue(
                    issues,
                    _issue(
                        "GENERIC_PACKET_FINDING_SHAPE_INVALID",
                        document,
                        finding_path,
                        "expected finding must contain exactly the reviewed members",
                    ),
                )
                continue
            values = (
                finding.get("observed_count"),
                finding.get("minimum"),
                finding.get("maximum"),
            )
            if (
                finding.get("code")
                not in {
                    "GENERIC_PACKET_REQUIREMENT_MISSING",
                    "GENERIC_PACKET_REQUIREMENT_EXCESSIVE",
                }
                or finding.get("requirement_id") not in EXPECTED_REQUIREMENT_LIMITS
                or any(type(value) is not int or value < 0 for value in values)
            ):
                _add_issue(
                    issues,
                    _issue(
                        "GENERIC_PACKET_FINDING_VALUE_INVALID",
                        document,
                        finding_path,
                        "expected finding contains an unsupported bounded value",
                    ),
                )
    if tuple(scenario_ids) != EXPECTED_SCENARIO_IDS:
        _add_issue(
            issues,
            _issue(
                "GENERIC_PACKET_SCENARIO_SET_MISMATCH",
                document,
                "$.scenarios",
                "scenario identifiers and order must match the reviewed inventory",
            ),
        )
    return sorted(issues)


def evaluate_document_counts(
    documents: Sequence[Mapping[str, object]],
    requirement_limits: Mapping[str, tuple[int, int]],
) -> tuple[PacketFinding, ...]:
    """Evaluate only document counts; this is not assignment or output-feasibility logic."""

    counts = Counter(
        cast(str, document["requirement_id"])
        for document in documents
        if isinstance(document.get("requirement_id"), str)
    )
    findings: list[PacketFinding] = []
    for requirement_id, (minimum, maximum) in requirement_limits.items():
        observed = counts[requirement_id]
        if observed < minimum:
            findings.append(
                PacketFinding(
                    "GENERIC_PACKET_REQUIREMENT_MISSING",
                    requirement_id,
                    observed,
                    minimum,
                    maximum,
                )
            )
        elif observed > maximum:
            findings.append(
                PacketFinding(
                    "GENERIC_PACKET_REQUIREMENT_EXCESSIVE",
                    requirement_id,
                    observed,
                    minimum,
                    maximum,
                )
            )
    return tuple(findings)


def _finding_from_mapping(finding: Mapping[str, object]) -> PacketFinding:
    return PacketFinding(
        cast(str, finding["code"]),
        cast(str, finding["requirement_id"]),
        cast(int, finding["observed_count"]),
        cast(int, finding["minimum"]),
        cast(int, finding["maximum"]),
    )


def _scenario_semantic_issues(
    packet: Mapping[str, object],
    limits: Mapping[str, tuple[int, int]],
) -> tuple[
    tuple[tuple[str, tuple[PacketFinding, ...]], ...],
    set[str],
    int,
    list[GenericPortalIssue],
]:
    evaluations: list[tuple[str, tuple[PacketFinding, ...]]] = []
    used_fixtures: set[str] = set()
    document_total = 0
    issues: list[GenericPortalIssue] = []
    scenarios = cast(list[Mapping[str, object]], packet["scenarios"])
    for index, scenario in enumerate(scenarios):
        scenario_id = cast(str, scenario["scenario_id"])
        documents = cast(list[Mapping[str, object]], scenario["documents"])
        document_total += len(documents)
        counts = Counter(cast(str, item["requirement_id"]) for item in documents)
        expected_counts = EXPECTED_SCENARIO_COUNTS[scenario_id]
        if {requirement_id: counts[requirement_id] for requirement_id in limits} != expected_counts:
            _add_issue(
                issues,
                _issue(
                    "GENERIC_PACKET_SCENARIO_COUNTS_MISMATCH",
                    PACKET_FIXTURE_PATH,
                    f"$.scenarios[{index}].documents",
                    "scenario document counts do not match the reviewed demonstration",
                ),
            )
        used_fixtures.update(cast(str, item["fixture_id"]) for item in documents)
        evaluated = evaluate_document_counts(documents, limits)
        declared = tuple(
            _finding_from_mapping(finding)
            for finding in cast(list[Mapping[str, object]], scenario["expected_findings"])
        )
        if evaluated != declared:
            _add_issue(
                issues,
                _issue(
                    "GENERIC_PACKET_EXPECTATION_MISMATCH",
                    PACKET_FIXTURE_PATH,
                    f"$.scenarios[{index}].expected_findings",
                    "declared finding does not match count-only evaluation",
                ),
            )
        evaluations.append((scenario_id, evaluated))
    if used_fixtures != APPROVED_PACKET_FIXTURE_IDS:
        _add_issue(
            issues,
            _issue(
                "GENERIC_PACKET_FIXTURE_SET_MISMATCH",
                PACKET_FIXTURE_PATH,
                "$.scenarios",
                "scenario fixture inventory does not match the reviewed allowlist",
            ),
        )
    return tuple(evaluations), used_fixtures, document_total, sorted(issues)


def _safe_fixture_path(index_root: Path, relative: object) -> Path | None:
    if not isinstance(relative, str) or "\\" in relative:
        return None
    candidate = PurePosixPath(relative)
    if (
        candidate.is_absolute()
        or not candidate.parts
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        return None
    path = index_root.joinpath(*candidate.parts)
    try:
        resolved_root = index_root.resolve(strict=True)
        resolved_path = path.resolve(strict=True)
        attributes = cast(int, getattr(path.lstat(), "st_file_attributes", 0))
        if (
            not resolved_path.is_relative_to(resolved_root)
            or not path.is_file()
            or path.is_symlink()
            or bool(attributes & _WINDOWS_REPARSE_POINT_ATTRIBUTE)
        ):
            return None
    except OSError, RuntimeError, ValueError:
        return None
    return path


def _validate_synthetic_index(
    root: Path,
    used_fixture_ids: set[str],
) -> list[GenericPortalIssue]:
    index, load_issues = _load_json_object(
        root / SYNTHETIC_INDEX_PATH,
        document=SYNTHETIC_INDEX_PATH,
        maximum_bytes=MAX_INDEX_BYTES,
        code_prefix="GENERIC_PACKET_INDEX",
    )
    if index is None:
        return load_issues
    policy = index.get("policy")
    fixtures = index.get("fixtures")
    if (
        index.get("schema_version") != 1
        or index.get("corpus_id") != "paperworkshell-public-synthetic-v1"
        or not isinstance(policy, Mapping)
        or policy.get("classification") != "public-synthetic-only"
        or policy.get("external_source_material") is not False
        or policy.get("decision_ref") != "PWS-D035"
        or not isinstance(fixtures, list)
        or len(fixtures) > 256
    ):
        return [
            _issue(
                "GENERIC_PACKET_INDEX_CONTRACT_INVALID",
                SYNTHETIC_INDEX_PATH,
                "$",
                "synthetic fixture index does not match the public corpus contract",
            )
        ]
    entries: dict[str, Mapping[str, object]] = {}
    for entry in fixtures:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("id"), str):
            return [
                _issue(
                    "GENERIC_PACKET_INDEX_CONTRACT_INVALID",
                    SYNTHETIC_INDEX_PATH,
                    "$.fixtures[*]",
                    "synthetic fixture index contains an invalid entry",
                )
            ]
        fixture_id = cast(str, entry["id"])
        if fixture_id in entries:
            return [
                _issue(
                    "GENERIC_PACKET_INDEX_ID_DUPLICATE",
                    SYNTHETIC_INDEX_PATH,
                    "$.fixtures[*].id",
                    "synthetic fixture identifiers must be unique",
                )
            ]
        entries[fixture_id] = entry
    issues: list[GenericPortalIssue] = []
    for fixture_id in sorted(used_fixture_ids):
        entry = entries.get(fixture_id)
        if entry is None:
            _add_issue(
                issues,
                _issue(
                    "GENERIC_PACKET_FIXTURE_UNRESOLVED",
                    SYNTHETIC_INDEX_PATH,
                    "$.fixtures",
                    "reviewed packet fixture is absent from the synthetic index",
                ),
            )
            continue
        provenance = entry.get("provenance")
        expected_bytes = entry.get("bytes")
        expected_sha256 = entry.get("sha256")
        fixture_path = _safe_fixture_path(root / SYNTHETIC_INDEX_PATH.parent, entry.get("path"))
        if (
            not isinstance(provenance, Mapping)
            or provenance.get("source_material") != "none"
            or provenance.get("contains_personal_data") is not False
            or provenance.get("review_decision") != "PWS-D035"
            or type(expected_bytes) is not int
            or not 0 < expected_bytes <= MAX_APPROVED_FIXTURE_BYTES
            or not isinstance(expected_sha256, str)
            or _SHA256.fullmatch(expected_sha256) is None
            or fixture_path is None
        ):
            _add_issue(
                issues,
                _issue(
                    "GENERIC_PACKET_FIXTURE_ENTRY_INVALID",
                    SYNTHETIC_INDEX_PATH,
                    "$.fixtures[*]",
                    "reviewed packet fixture has an invalid public-corpus entry",
                ),
            )
            continue
        content, read_issues = _read_bounded_bytes(
            fixture_path,
            document=SYNTHETIC_INDEX_PATH,
            maximum_bytes=MAX_APPROVED_FIXTURE_BYTES,
            code_prefix="GENERIC_PACKET_FIXTURE",
        )
        issues.extend(read_issues)
        if content is not None and (
            len(content) != expected_bytes or hashlib.sha256(content).hexdigest() != expected_sha256
        ):
            _add_issue(
                issues,
                _issue(
                    "GENERIC_PACKET_FIXTURE_IDENTITY_MISMATCH",
                    SYNTHETIC_INDEX_PATH,
                    "$.fixtures[*]",
                    "reviewed packet fixture does not match its indexed identity",
                ),
            )
    return sorted(issues)


def validate_generic_portal_template(
    root: Path = PROJECT_ROOT,
) -> tuple[GenericPortalValidationSummary | None, list[GenericPortalIssue]]:
    """Validate both artifact copies and the bounded count-only packet matrix."""

    template, artifact_issues = _validate_artifact_identity(root)
    if template is None:
        return None, sorted(artifact_issues)
    limits, requirement_issues = _requirement_limits(template)
    if limits is None:
        return None, sorted(requirement_issues)
    packet, packet_load_issues = _load_json_object(
        root / PACKET_FIXTURE_PATH,
        document=PACKET_FIXTURE_PATH,
        maximum_bytes=MAX_PACKET_BYTES,
        code_prefix="GENERIC_PACKET",
    )
    if packet is None:
        return None, sorted(packet_load_issues)
    shape_issues = _validate_packet_shape(packet)
    if shape_issues:
        return None, shape_issues
    evaluations, used_fixtures, document_total, semantic_issues = _scenario_semantic_issues(
        packet, limits
    )
    if semantic_issues:
        return None, semantic_issues
    index_issues = _validate_synthetic_index(root, used_fixtures)
    if index_issues:
        return None, index_issues
    return (
        GenericPortalValidationSummary(
            scenario_count=len(evaluations),
            document_count=document_total,
            approved_fixture_count=len(used_fixtures),
            findings_by_scenario=evaluations,
        ),
        [],
    )


def validation_messages(root: Path = PROJECT_ROOT) -> list[str]:
    """Return deterministic diagnostics for repository tooling."""

    _, issues = validate_generic_portal_template(root)
    return [issue.render() for issue in issues]


def main(argv: Sequence[str] | None = None) -> int:
    """Run the checked-in generic-portal demonstration validator."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=PROJECT_ROOT,
        help="repository root (default: detected project root)",
    )
    arguments = parser.parse_args(argv)
    summary, issues = validate_generic_portal_template(arguments.root)
    if issues:
        for issue in issues:
            print(issue.render(), file=sys.stderr)
        return 1
    assert summary is not None
    print(
        "OK: validated generic-portal template identity and "
        f"{summary.scenario_count} count-only packet scenarios using "
        f"{summary.approved_fixture_count} approved synthetic fixtures."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

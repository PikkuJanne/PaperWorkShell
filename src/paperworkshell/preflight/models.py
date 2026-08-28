"""Pure, bounded contracts for deterministic preflight evaluation.

Preflight models carry opaque internal identifiers and static presentation text
only.  Document content, source names, paths, OCR text, template values, and raw
dependency failures do not belong in this boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Never
from uuid import UUID

from paperworkshell.domain.checklist import TemplateBinding

PREFLIGHT_MODEL_VERSION: Final = 1
PREFLIGHT_CONFIRMATION_VERSION: Final = 1
PREFLIGHT_FINDING_DEFINITION_VERSION: Final = 1
PREFLIGHT_REPORT_VERSION: Final = 1
MAX_PREFLIGHT_RULES: Final = 256
MAX_PREFLIGHT_FINDING_DEFINITIONS: Final = 1_024
MAX_PREFLIGHT_FINDINGS_PER_RULE: Final = 65_536
MAX_PREFLIGHT_FINDINGS: Final = 131_072
MAX_PREFLIGHT_SCOPE_POSITION: Final = 4_096
MAX_PREFLIGHT_SCOPES: Final = 16_513
MAX_PREFLIGHT_MESSAGE_CODEPOINTS: Final = 4_096
MAX_PREFLIGHT_REMEDIATION_CODEPOINTS: Final = 1_024
MAX_PREFLIGHT_FACT_STRING_CODEPOINTS: Final = 65_536
MAX_PREFLIGHT_FACT_DEPTH: Final = 32
MAX_PREFLIGHT_FACT_NODES: Final = 1_000_000
MAX_PREFLIGHT_RULE_VERSION: Final = 2**31 - 1
MAX_PREFLIGHT_STATEMENT_VERSION: Final = 2**53 - 1

_CONTEXT_TOKEN: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")
_RULE_ID: Final = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
_FINDING_CODE: Final = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*\Z")
_CONFIRMATION_CODE: Final = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
_SCOPE_KEY: Final = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")


class PreflightErrorCode(StrEnum):
    """Closed failures for the preflight boundary."""

    MODEL_INVALID = "PREFLIGHT_MODEL_INVALID"
    RULE_INVALID = "PREFLIGHT_RULE_INVALID"
    DEPENDENCY_MISSING = "PREFLIGHT_DEPENDENCY_MISSING"
    DEPENDENCY_UNDECLARED = "PREFLIGHT_DEPENDENCY_UNDECLARED"
    RULE_FAILED = "PREFLIGHT_RULE_FAILED"
    RESULT_INVALID = "PREFLIGHT_RESULT_INVALID"
    LIMIT_EXCEEDED = "PREFLIGHT_LIMIT_EXCEEDED"


_SAFE_ERROR_MESSAGES: Final[dict[PreflightErrorCode, str]] = {
    PreflightErrorCode.MODEL_INVALID: "PaperWorkShell received invalid preflight data.",
    PreflightErrorCode.RULE_INVALID: "PaperWorkShell received an invalid preflight rule.",
    PreflightErrorCode.DEPENDENCY_MISSING: (
        "A required preflight input is not available. Refresh the case checks and try again."
    ),
    PreflightErrorCode.DEPENDENCY_UNDECLARED: (
        "A preflight rule requested data outside its declared inputs."
    ),
    PreflightErrorCode.RULE_FAILED: "PaperWorkShell could not complete a preflight rule safely.",
    PreflightErrorCode.RESULT_INVALID: "PaperWorkShell received an invalid preflight result.",
    PreflightErrorCode.LIMIT_EXCEEDED: "Preflight exceeded a configured safety limit.",
}


@dataclass(slots=True, repr=False)
class PreflightError(ValueError):
    """Stable, content-free preflight failure."""

    code: PreflightErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not PreflightErrorCode:
            self.code = PreflightErrorCode.MODEL_INVALID
        if type(self.context) is not str or _CONTEXT_TOKEN.fullmatch(self.context) is None:
            self.context = "preflight.internal"
            self.code = PreflightErrorCode.MODEL_INVALID
        self.safe_message = _SAFE_ERROR_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"PreflightError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_preflight(code: PreflightErrorCode, context: str) -> Never:
    """Raise one closed preflight failure without chaining private detail."""

    error = PreflightError(code=code, context=context)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


class PreflightDependency(StrEnum):
    """Closed semantic inputs on which a preflight rule may depend."""

    CASE = "case"
    TEMPLATE = "template"
    CHECKLIST = "checklist"
    SOURCE_INSPECTIONS = "source_inspections"
    QUALITY_FINDINGS = "quality_findings"
    OUTPUT_PLAN = "output_plan"


class PreflightSeverity(StrEnum):
    """User-visible finding classes without an implied portal decision."""

    BLOCKING_ERROR = "blocking_error"
    WARNING = "warning"
    CONFIRMATION_REQUIRED = "confirmation_required"


class PreflightScopeKind(StrEnum):
    """Navigable case items to which a finding can point."""

    CASE = "case"
    REQUIREMENT = "requirement"
    SOURCE = "source"
    ASSIGNMENT = "assignment"
    PAGE = "page"
    OUTPUT = "output"


_SEVERITY_ORDER: Final[dict[PreflightSeverity, int]] = {
    PreflightSeverity.BLOCKING_ERROR: 0,
    PreflightSeverity.CONFIRMATION_REQUIRED: 1,
    PreflightSeverity.WARNING: 2,
}
_SCOPE_ORDER: Final[dict[PreflightScopeKind, int]] = {
    PreflightScopeKind.CASE: 0,
    PreflightScopeKind.REQUIREMENT: 1,
    PreflightScopeKind.SOURCE: 2,
    PreflightScopeKind.ASSIGNMENT: 3,
    PreflightScopeKind.PAGE: 4,
    PreflightScopeKind.OUTPUT: 5,
}


class PreflightFactValue:
    """Marker base for immutable, rule-specific fact projections.

    Application adapters create frozen dataclass subclasses containing only the
    exact typed facts needed by registered rules.  PaperWorkShell computes a
    canonical identity from those fields; callers cannot assert a separate
    cache token.  The engine never renders, logs, or calls a fact value.
    """

    __slots__ = ()
    case_id: UUID
    manifest_revision: int


@dataclass(frozen=True, slots=True, repr=False)
class PreflightInput:
    """One exact fact projection and its authoritative content identity."""

    authority: PreflightAuthority
    dependency: PreflightDependency
    value: PreflightFactValue = field(repr=False)
    model_version: int = PREFLIGHT_MODEL_VERSION
    _revision_sha256: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.authority) is not PreflightAuthority
            or type(self.dependency) is not PreflightDependency
            or not isinstance(self.value, PreflightFactValue)
            or type(self.model_version) is not int
            or self.model_version != PREFLIGHT_MODEL_VERSION
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.input")
        try:
            value_case_id = object.__getattribute__(self.value, "case_id")
            value_manifest_revision = object.__getattribute__(self.value, "manifest_revision")
        except AttributeError:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.input_binding")
        if (
            value_case_id != self.authority.case_id
            or type(value_manifest_revision) is not int
            or value_manifest_revision != self.authority.manifest_revision
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.input_binding")
        material = {
            "model_version": self.model_version,
            "authority_sha256": self.authority.sha256,
            "dependency": self.dependency.value,
            "value": _canonical_fact_value(self.value),
        }
        try:
            encoded = json.dumps(
                material,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        except TypeError, ValueError, UnicodeError, RecursionError:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.input_identity")
        object.__setattr__(self, "_revision_sha256", hashlib.sha256(encoded).hexdigest())

    @property
    def revision_sha256(self) -> str:
        """Return the engine-computed identity of authority, kind, and typed facts."""

        return self._revision_sha256

    def __repr__(self) -> str:
        return f"PreflightInput(dependency={self.dependency.value!r})"


@dataclass(frozen=True, slots=True)
class PreflightScope:
    """Tagged navigation target plus stable user-order position."""

    kind: PreflightScopeKind
    item_id: UUID | str
    position: int | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not PreflightScopeKind:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.scope")
        if self.kind is PreflightScopeKind.CASE:
            if type(self.item_id) is not UUID or self.item_id.version != 4:
                reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.scope_id")
            if self.position is not None:
                reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.scope_position")
            return
        if self.kind in {PreflightScopeKind.REQUIREMENT, PreflightScopeKind.OUTPUT}:
            if (
                type(self.item_id) is not str
                or not 1 <= len(self.item_id) <= 128
                or _SCOPE_KEY.fullmatch(self.item_id) is None
            ):
                reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.scope_id")
        elif type(self.item_id) is not UUID or self.item_id.version != 4:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.scope_id")
        if type(self.position) is not int or not 1 <= self.position <= MAX_PREFLIGHT_SCOPE_POSITION:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.scope_position")

    @property
    def identity(self) -> tuple[PreflightScopeKind, UUID | str]:
        return (self.kind, self.item_id)


@dataclass(frozen=True, slots=True, repr=False)
class PreflightScopeCatalog:
    """Complete current navigation inventory used to reject stale findings."""

    case_id: UUID
    scopes: tuple[PreflightScope, ...] = field(repr=False)
    _sha256: str = field(init=False, repr=False)
    _scope_set: frozenset[PreflightScope] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _require_uuid4(self.case_id, "preflight.catalog_case")
        if (
            type(self.scopes) is not tuple
            or not 1 <= len(self.scopes) <= MAX_PREFLIGHT_SCOPES
            or any(type(scope) is not PreflightScope for scope in self.scopes)
            or self.scopes[0] != PreflightScope(PreflightScopeKind.CASE, self.case_id)
            or sum(scope.kind is PreflightScopeKind.CASE for scope in self.scopes) != 1
            or self.scopes
            != tuple(
                sorted(
                    self.scopes,
                    key=lambda scope: (
                        _SCOPE_ORDER[scope.kind],
                        0 if scope.position is None else scope.position,
                    ),
                )
            )
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.scope_catalog")
        identities: set[tuple[PreflightScopeKind, UUID | str]] = set()
        positions: set[tuple[PreflightScopeKind, int]] = set()
        for scope in self.scopes:
            if scope.identity in identities:
                reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.catalog_identity")
            identities.add(scope.identity)
            if scope.position is not None:
                position_key = (scope.kind, scope.position)
                if position_key in positions:
                    reject_preflight(
                        PreflightErrorCode.MODEL_INVALID,
                        "preflight.catalog_position",
                    )
                positions.add(position_key)
        material = [
            {
                "kind": scope.kind.value,
                "item_id": str(scope.item_id),
                "position": scope.position,
            }
            for scope in self.scopes
        ]
        encoded = json.dumps(
            material,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        object.__setattr__(self, "_sha256", hashlib.sha256(encoded).hexdigest())
        object.__setattr__(self, "_scope_set", frozenset(self.scopes))

    @property
    def sha256(self) -> str:
        return self._sha256

    def contains(self, scope: PreflightScope) -> bool:
        """Return whether the exact identity and current position are catalogued."""

        if type(scope) is not PreflightScope:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.catalog_scope")
        return scope in self._scope_set

    def __repr__(self) -> str:
        return f"PreflightScopeCatalog(scope_count={len(self.scopes)})"


@dataclass(frozen=True, slots=True, repr=False)
class PreflightAuthority:
    """Exact saved case and scope inventory to which every input is bound."""

    case_id: UUID
    manifest_revision: int
    manifest_sha256: str = field(repr=False)
    template_binding: TemplateBinding | None = field(repr=False)
    scope_catalog: PreflightScopeCatalog = field(repr=False)
    _sha256: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        _require_uuid4(self.case_id, "preflight.authority_case")
        _require_sha256(self.manifest_sha256, "preflight.authority_manifest")
        if (
            type(self.manifest_revision) is not int
            or not 0 <= self.manifest_revision <= MAX_PREFLIGHT_STATEMENT_VERSION
            or (
                self.template_binding is not None
                and type(self.template_binding) is not TemplateBinding
            )
            or type(self.scope_catalog) is not PreflightScopeCatalog
            or self.scope_catalog.case_id != self.case_id
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.authority")
        material = {
            "case_id": str(self.case_id),
            "manifest_revision": self.manifest_revision,
            "manifest_sha256": self.manifest_sha256,
            "template_binding": (
                None
                if self.template_binding is None
                else {
                    "template_id": self.template_binding.template_id,
                    "template_version": self.template_binding.template_version,
                    "sha256": self.template_binding.sha256,
                }
            ),
            "scope_catalog_sha256": self.scope_catalog.sha256,
        }
        encoded = json.dumps(
            material,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        object.__setattr__(self, "_sha256", hashlib.sha256(encoded).hexdigest())

    @property
    def sha256(self) -> str:
        return self._sha256

    def __repr__(self) -> str:
        return (
            "PreflightAuthority("
            f"case_id={self.case_id!r}, manifest_revision={self.manifest_revision!r}, "
            f"has_template_binding={self.template_binding is not None}, "
            f"scope_count={len(self.scope_catalog.scopes)})"
        )


@dataclass(frozen=True, slots=True)
class PreflightConfirmationRequirement:
    """Revision-bound request to record review; never proof of statement truth."""

    confirmation_code: str
    request_model_version: int = PREFLIGHT_CONFIRMATION_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.confirmation_code) is not str
            or not 1 <= len(self.confirmation_code) <= 128
            or _CONFIRMATION_CODE.fullmatch(self.confirmation_code) is None
            or type(self.request_model_version) is not int
            or self.request_model_version != PREFLIGHT_CONFIRMATION_VERSION
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.confirmation")


class PreflightEvidenceUnit(StrEnum):
    """Content-free units permitted in dynamic numeric finding evidence."""

    COUNT = "count"
    BYTES = "bytes"
    FILES = "files"
    DOCUMENTS = "documents"
    PAGES = "pages"
    PARTS_PER_MILLION = "parts_per_million"


@dataclass(frozen=True, slots=True)
class PreflightEvidence:
    """Bounded numeric observation that cannot carry document text or names."""

    metric: str
    observed_value: int
    unit: PreflightEvidenceUnit
    limit_value: int | None = None

    def __post_init__(self) -> None:
        if (
            type(self.metric) is not str
            or not 1 <= len(self.metric) <= 128
            or _CONFIRMATION_CODE.fullmatch(self.metric) is None
            or type(self.observed_value) is not int
            or not 0 <= self.observed_value <= MAX_PREFLIGHT_STATEMENT_VERSION
            or type(self.unit) is not PreflightEvidenceUnit
            or (
                self.limit_value is not None
                and (
                    type(self.limit_value) is not int
                    or not 0 <= self.limit_value <= MAX_PREFLIGHT_STATEMENT_VERSION
                )
            )
        ):
            reject_preflight(PreflightErrorCode.RESULT_INVALID, "preflight.evidence")


class PreflightRemediationAction(StrEnum):
    """Closed user actions; no callback, command, path, or URL is carried."""

    REVIEW_CASE = "review_case"
    ASSIGN_PAGES = "assign_pages"
    CONFIRM_ASSIGNMENT = "confirm_assignment"
    REVIEW_PAGE = "review_page"
    COMPARE_PAGES = "compare_pages"
    INSPECT_OR_REPLACE_SOURCE = "inspect_or_replace_source"
    REVIEW_OUTPUT_PLAN = "review_output_plan"
    REVIEW_TEMPLATE_STATEMENT = "review_template_statement"
    RERUN_PREFLIGHT = "rerun_preflight"


@dataclass(frozen=True, slots=True, repr=False)
class PreflightFindingDefinition:
    """Static presentation and severity for one stable finding code."""

    code: str
    severity: PreflightSeverity
    remediation_action: PreflightRemediationAction
    message: str = field(repr=False)
    remediation: str = field(repr=False)
    confirmation: PreflightConfirmationRequirement | None = field(default=None, repr=False)
    definition_version: int = PREFLIGHT_FINDING_DEFINITION_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.code) is not str
            or not 1 <= len(self.code) <= 128
            or _FINDING_CODE.fullmatch(self.code) is None
            or type(self.severity) is not PreflightSeverity
            or type(self.remediation_action) is not PreflightRemediationAction
            or type(self.definition_version) is not int
            or self.definition_version != PREFLIGHT_FINDING_DEFINITION_VERSION
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.finding_definition")
        _require_presentation(
            self.message,
            "preflight.finding_message",
            maximum_codepoints=MAX_PREFLIGHT_MESSAGE_CODEPOINTS,
        )
        _require_presentation(
            self.remediation,
            "preflight.finding_remediation",
            maximum_codepoints=MAX_PREFLIGHT_REMEDIATION_CODEPOINTS,
        )
        if self.severity is PreflightSeverity.CONFIRMATION_REQUIRED:
            if type(self.confirmation) is not PreflightConfirmationRequirement:
                reject_preflight(
                    PreflightErrorCode.MODEL_INVALID,
                    "preflight.finding_confirmation",
                )
        elif self.confirmation is not None:
            reject_preflight(
                PreflightErrorCode.MODEL_INVALID,
                "preflight.finding_confirmation",
            )

    def __repr__(self) -> str:
        return f"PreflightFindingDefinition(code={self.code!r}, severity={self.severity.value!r})"


@dataclass(frozen=True, slots=True)
class PreflightFindingTarget:
    """Minimal dynamic result that one registered rule may emit."""

    code: str
    scope: PreflightScope
    related_scope: PreflightScope | None = None
    evidence: tuple[PreflightEvidence, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.code) is not str
            or not 1 <= len(self.code) <= 128
            or _FINDING_CODE.fullmatch(self.code) is None
            or type(self.scope) is not PreflightScope
            or (
                self.related_scope is not None
                and (
                    type(self.related_scope) is not PreflightScope
                    or self.related_scope.identity == self.scope.identity
                )
            )
            or type(self.evidence) is not tuple
            or len(self.evidence) > 32
            or any(type(item) is not PreflightEvidence for item in self.evidence)
            or len({item.metric for item in self.evidence}) != len(self.evidence)
        ):
            reject_preflight(PreflightErrorCode.RESULT_INVALID, "preflight.finding_target")
        object.__setattr__(
            self,
            "evidence",
            tuple(sorted(self.evidence, key=lambda item: item.metric)),
        )


@dataclass(frozen=True, slots=True, repr=False)
class PreflightFinding:
    """Materialized static finding tied to one rule and one case item."""

    rule_id: str
    definition: PreflightFindingDefinition
    scope: PreflightScope
    related_scope: PreflightScope | None = None
    evidence: tuple[PreflightEvidence, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        _require_rule_id(self.rule_id, "preflight.finding_rule")
        if (
            type(self.definition) is not PreflightFindingDefinition
            or type(self.scope) is not PreflightScope
            or (
                self.related_scope is not None
                and (
                    type(self.related_scope) is not PreflightScope
                    or self.related_scope.identity == self.scope.identity
                )
            )
            or type(self.evidence) is not tuple
            or len(self.evidence) > 32
            or any(type(item) is not PreflightEvidence for item in self.evidence)
            or self.evidence != tuple(sorted(self.evidence, key=lambda item: item.metric))
            or len({item.metric for item in self.evidence}) != len(self.evidence)
        ):
            reject_preflight(PreflightErrorCode.RESULT_INVALID, "preflight.finding")

    @property
    def code(self) -> str:
        return self.definition.code

    @property
    def severity(self) -> PreflightSeverity:
        return self.definition.severity

    @property
    def message(self) -> str:
        return self.definition.message

    @property
    def remediation(self) -> str:
        return self.definition.remediation

    @property
    def confirmation(self) -> PreflightConfirmationRequirement | None:
        return self.definition.confirmation

    @property
    def identity(
        self,
    ) -> tuple[
        str,
        PreflightScopeKind,
        UUID | str,
        PreflightScopeKind | None,
        UUID | str | None,
    ]:
        """Return the stable deduplication identity, independent of current position."""

        return (
            self.code,
            self.scope.kind,
            self.scope.item_id,
            None if self.related_scope is None else self.related_scope.kind,
            None if self.related_scope is None else self.related_scope.item_id,
        )

    def __repr__(self) -> str:
        return (
            "PreflightFinding("
            f"rule_id={self.rule_id!r}, code={self.code!r}, "
            f"severity={self.severity.value!r}, scope={self.scope.kind.value!r}, "
            f"has_related_scope={self.related_scope is not None}, "
            f"evidence_count={len(self.evidence)})"
        )


def preflight_finding_sort_key(
    finding: PreflightFinding,
) -> tuple[int, int, int, str, int, int, str, str, str]:
    """Return one content-free canonical ordering key."""

    if type(finding) is not PreflightFinding:
        reject_preflight(PreflightErrorCode.RESULT_INVALID, "preflight.finding_sort")
    position = 0 if finding.scope.position is None else finding.scope.position
    related_kind = -1 if finding.related_scope is None else _SCOPE_ORDER[finding.related_scope.kind]
    related_position = (
        0
        if finding.related_scope is None or finding.related_scope.position is None
        else finding.related_scope.position
    )
    related_id = "" if finding.related_scope is None else str(finding.related_scope.item_id)
    return (
        _SEVERITY_ORDER[finding.severity],
        _SCOPE_ORDER[finding.scope.kind],
        position,
        str(finding.scope.item_id),
        related_kind,
        related_position,
        related_id,
        finding.code,
        finding.rule_id,
    )


@dataclass(frozen=True, slots=True, repr=False)
class PreflightReport:
    """Canonical finding set without a policy claim that a packet is acceptable."""

    findings: tuple[PreflightFinding, ...] = field(repr=False)
    authority_sha256: str = field(repr=False)
    engine_sha256: str = field(repr=False)
    rule_ids: tuple[str, ...]
    report_version: int = PREFLIGHT_REPORT_VERSION

    def __post_init__(self) -> None:
        _require_sha256(self.authority_sha256, "preflight.report_authority")
        _require_sha256(self.engine_sha256, "preflight.report_engine")
        if (
            type(self.findings) is not tuple
            or len(self.findings) > MAX_PREFLIGHT_FINDINGS
            or any(type(finding) is not PreflightFinding for finding in self.findings)
            or type(self.rule_ids) is not tuple
            or not self.rule_ids
            or len(self.rule_ids) > MAX_PREFLIGHT_RULES
            or any(type(rule_id) is not str for rule_id in self.rule_ids)
            or self.rule_ids != tuple(sorted(self.rule_ids))
            or len(set(self.rule_ids)) != len(self.rule_ids)
            or type(self.report_version) is not int
            or self.report_version != PREFLIGHT_REPORT_VERSION
            or self.findings != tuple(sorted(self.findings, key=preflight_finding_sort_key))
        ):
            reject_preflight(PreflightErrorCode.RESULT_INVALID, "preflight.report")
        identities: dict[
            tuple[
                str,
                PreflightScopeKind,
                UUID | str,
                PreflightScopeKind | None,
                UUID | str | None,
            ],
            PreflightFinding,
        ] = {}
        for finding in self.findings:
            prior = identities.get(finding.identity)
            if prior is not None:
                reject_preflight(PreflightErrorCode.RESULT_INVALID, "preflight.report_duplicate")
            identities[finding.identity] = finding
        for rule_id in self.rule_ids:
            _require_rule_id(rule_id, "preflight.report_rule")
        if any(finding.rule_id not in self.rule_ids for finding in self.findings):
            reject_preflight(PreflightErrorCode.RESULT_INVALID, "preflight.report_rule")

    @property
    def blocking_errors(self) -> tuple[PreflightFinding, ...]:
        return tuple(
            finding
            for finding in self.findings
            if finding.severity is PreflightSeverity.BLOCKING_ERROR
        )

    @property
    def warnings(self) -> tuple[PreflightFinding, ...]:
        return tuple(
            finding for finding in self.findings if finding.severity is PreflightSeverity.WARNING
        )

    @property
    def confirmations_required(self) -> tuple[PreflightFinding, ...]:
        return tuple(
            finding
            for finding in self.findings
            if finding.severity is PreflightSeverity.CONFIRMATION_REQUIRED
        )

    @property
    def has_blocking_errors(self) -> bool:
        return any(
            finding.severity is PreflightSeverity.BLOCKING_ERROR for finding in self.findings
        )

    @property
    def requires_user_confirmation(self) -> bool:
        return any(
            finding.severity is PreflightSeverity.CONFIRMATION_REQUIRED for finding in self.findings
        )

    def __repr__(self) -> str:
        return (
            "PreflightReport("
            f"finding_count={len(self.findings)}, "
            f"blocking_error_count={len(self.blocking_errors)}, "
            f"warning_count={len(self.warnings)}, "
            f"confirmation_count={len(self.confirmations_required)})"
        )


def _require_rule_id(value: object, context: str) -> str:
    if type(value) is not str or not 1 <= len(value) <= 128 or _RULE_ID.fullmatch(value) is None:
        reject_preflight(PreflightErrorCode.MODEL_INVALID, context)
    return value


def _require_sha256(value: object, context: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        reject_preflight(PreflightErrorCode.MODEL_INVALID, context)
    return value


def _require_uuid4(value: object, context: str) -> UUID:
    if type(value) is not UUID or value.version != 4:
        reject_preflight(PreflightErrorCode.MODEL_INVALID, context)
    return value


def _require_presentation(
    value: object,
    context: str,
    *,
    maximum_codepoints: int,
) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= maximum_codepoints
        or value != value.strip()
        or value != unicodedata.normalize("NFC", value)
        or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value)
    ):
        reject_preflight(PreflightErrorCode.MODEL_INVALID, context)
    return value


def _canonical_fact_value(value: object) -> object:
    """Return bounded canonical material for one frozen fact projection."""

    if not is_dataclass(value):
        reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.fact_dataclass")
    parameters = getattr(type(value), "__dataclass_params__", None)
    if parameters is None or not bool(parameters.frozen):
        reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.fact_frozen")
    remaining = [MAX_PREFLIGHT_FACT_NODES]
    return _canonical_fact_node(value, depth=0, active=set(), remaining=remaining)


def _canonical_fact_node(
    value: object,
    *,
    depth: int,
    active: set[int],
    remaining: list[int],
) -> object:
    if depth > MAX_PREFLIGHT_FACT_DEPTH or remaining[0] < 1:
        reject_preflight(PreflightErrorCode.LIMIT_EXCEEDED, "preflight.fact_capacity")
    remaining[0] -= 1
    if value is None or type(value) is bool:
        return value
    if isinstance(value, StrEnum):
        enum_type = type(value)
        return {
            "$enum": f"{enum_type.__module__}.{enum_type.__qualname__}",
            "value": value.value,
        }
    if type(value) is int:
        if not -MAX_PREFLIGHT_STATEMENT_VERSION <= value <= MAX_PREFLIGHT_STATEMENT_VERSION:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.fact_integer")
        return value
    if type(value) is str:
        if (
            len(value) > MAX_PREFLIGHT_FACT_STRING_CODEPOINTS
            or value != unicodedata.normalize("NFC", value)
            or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value)
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.fact_text")
        return value
    if type(value) is UUID:
        if value.version != 4:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.fact_uuid")
        return {"$uuid4": str(value)}
    if type(value) is datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.fact_datetime")
        timespec = "seconds" if value.microsecond == 0 else "microseconds"
        return {"$utc": value.astimezone(UTC).isoformat(timespec=timespec).replace("+00:00", "Z")}
    if type(value) is tuple:
        identity = id(value)
        if identity in active:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.fact_cycle")
        active.add(identity)
        try:
            return [
                _canonical_fact_node(
                    item,
                    depth=depth + 1,
                    active=active,
                    remaining=remaining,
                )
                for item in value
            ]
        finally:
            active.remove(identity)
    if is_dataclass(value):
        parameters = getattr(type(value), "__dataclass_params__", None)
        if parameters is None or not bool(parameters.frozen):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.fact_nested")
        identity = id(value)
        if identity in active:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.fact_cycle")
        active.add(identity)
        try:
            dataclass_type = type(value)
            return {
                "$dataclass": f"{dataclass_type.__module__}.{dataclass_type.__qualname__}",
                "fields": [
                    {
                        "name": item.name,
                        "value": _canonical_fact_node(
                            object.__getattribute__(value, item.name),
                            depth=depth + 1,
                            active=active,
                            remaining=remaining,
                        ),
                    }
                    for item in fields(value)
                ],
            }
        finally:
            active.remove(identity)
    reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.fact_value")


__all__ = (
    "MAX_PREFLIGHT_FINDINGS",
    "MAX_PREFLIGHT_FINDINGS_PER_RULE",
    "MAX_PREFLIGHT_FINDING_DEFINITIONS",
    "MAX_PREFLIGHT_RULES",
    "MAX_PREFLIGHT_SCOPES",
    "PreflightConfirmationRequirement",
    "PreflightDependency",
    "PreflightAuthority",
    "PreflightEvidence",
    "PreflightEvidenceUnit",
    "PreflightError",
    "PreflightErrorCode",
    "PreflightFactValue",
    "PreflightFinding",
    "PreflightFindingDefinition",
    "PreflightFindingTarget",
    "PreflightInput",
    "PreflightRemediationAction",
    "PreflightReport",
    "PreflightScope",
    "PreflightScopeCatalog",
    "PreflightScopeKind",
    "PreflightSeverity",
    "preflight_finding_sort_key",
    "reject_preflight",
)

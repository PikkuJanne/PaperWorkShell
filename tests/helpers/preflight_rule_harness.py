"""Synthetic PWS-0404 rule harness shared by engine contract tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import UUID

from paperworkshell.preflight.engine import PreflightEngine
from paperworkshell.preflight.models import (
    PreflightAuthority,
    PreflightConfirmationRequirement,
    PreflightDependency,
    PreflightFactValue,
    PreflightFindingDefinition,
    PreflightFindingTarget,
    PreflightInput,
    PreflightRemediationAction,
    PreflightScope,
    PreflightScopeCatalog,
    PreflightScopeKind,
    PreflightSeverity,
)
from paperworkshell.preflight.rules import PreflightRuleContext, PreflightRuleDescriptor

_FIXTURE_MEMBERS = frozenset({"fixture_version", "cases"})
_CASE_MEMBERS = frozenset(
    {
        "fixture_id",
        "case_id",
        "document_id",
        "page_id",
        "document_count",
        "quality_review_needed",
        "user_confirmation_recorded",
        "expected_codes",
    }
)

MISSING_CODE = "SYNTHETIC_REQUIRED_DOCUMENT_MISSING"
QUALITY_CODE = "SYNTHETIC_PAGE_QUALITY_REVIEW"
CONFIRMATION_CODE = "SYNTHETIC_ASSIGNMENT_CONFIRMATION_REQUIRED"


@dataclass(frozen=True, slots=True, repr=False)
class SyntheticPacketFacts(PreflightFactValue):
    """Content-free facts for one deterministic test-only rule."""

    case_id: UUID
    manifest_revision: int
    document_id: UUID
    page_id: UUID
    document_count: int
    quality_review_needed: bool
    user_confirmation_recorded: bool

    def __post_init__(self) -> None:
        identifiers = (self.case_id, self.document_id, self.page_id)
        if (
            any(type(item) is not UUID or item.version != 4 for item in identifiers)
            or len(set(identifiers)) != len(identifiers)
            or type(self.manifest_revision) is not int
            or not 0 <= self.manifest_revision <= 2**53 - 1
            or type(self.document_count) is not int
            or not 0 <= self.document_count <= 4_096
            or type(self.quality_review_needed) is not bool
            or type(self.user_confirmation_recorded) is not bool
        ):
            raise AssertionError("invalid synthetic preflight facts")

    def __repr__(self) -> str:
        return f"SyntheticPacketFacts(document_count={self.document_count!r})"


@dataclass(frozen=True, slots=True)
class SyntheticRuleCase:
    fixture_id: str
    facts: SyntheticPacketFacts
    expected_codes: tuple[str, ...]


class SyntheticPacketRule:
    """Instrumented deterministic rule used only by the test harness."""

    __slots__ = ("_descriptor", "invocation_count")

    def __init__(
        self,
        *,
        rule_id: str = "synthetic.packet",
        rule_version: int = 1,
        dependencies: tuple[PreflightDependency, ...] = (PreflightDependency.CASE,),
    ) -> None:
        self._descriptor = PreflightRuleDescriptor(
            rule_id=rule_id,
            rule_version=rule_version,
            dependencies=dependencies,
            finding_definitions=(
                PreflightFindingDefinition(
                    code=MISSING_CODE,
                    severity=PreflightSeverity.BLOCKING_ERROR,
                    remediation_action=PreflightRemediationAction.ASSIGN_PAGES,
                    message="A required document has not been added.",
                    remediation="Add the missing document, then run preflight again.",
                ),
                PreflightFindingDefinition(
                    code=QUALITY_CODE,
                    severity=PreflightSeverity.WARNING,
                    remediation_action=PreflightRemediationAction.REVIEW_PAGE,
                    message="A page-quality signal needs review.",
                    remediation="Review the indicated page before export.",
                ),
                PreflightFindingDefinition(
                    code=CONFIRMATION_CODE,
                    severity=PreflightSeverity.CONFIRMATION_REQUIRED,
                    remediation_action=PreflightRemediationAction.CONFIRM_ASSIGNMENT,
                    message="Your review of the assigned document is required.",
                    remediation="Review the assignment and record your confirmation.",
                    confirmation=PreflightConfirmationRequirement(
                        confirmation_code="synthetic.assignment.reviewed",
                    ),
                ),
            ),
        )
        self.invocation_count = 0

    @property
    def descriptor(self) -> PreflightRuleDescriptor:
        return self._descriptor

    def evaluate(
        self,
        context: PreflightRuleContext,
        /,
    ) -> tuple[PreflightFindingTarget, ...]:
        self.invocation_count += 1
        facts = context.require(PreflightDependency.CASE, SyntheticPacketFacts)
        findings: list[PreflightFindingTarget] = []
        if facts.document_count == 0:
            findings.append(
                PreflightFindingTarget(
                    MISSING_CODE,
                    PreflightScope(PreflightScopeKind.CASE, facts.case_id),
                )
            )
        if facts.quality_review_needed:
            findings.append(
                PreflightFindingTarget(
                    QUALITY_CODE,
                    PreflightScope(PreflightScopeKind.PAGE, facts.page_id, 1),
                )
            )
        if not facts.user_confirmation_recorded:
            findings.append(
                PreflightFindingTarget(
                    CONFIRMATION_CODE,
                    PreflightScope(PreflightScopeKind.ASSIGNMENT, facts.document_id, 1),
                )
            )
        return tuple(findings)


def synthetic_engine(rule: SyntheticPacketRule | None = None) -> PreflightEngine:
    return PreflightEngine((SyntheticPacketRule() if rule is None else rule,))


def synthetic_input(
    facts: SyntheticPacketFacts,
    *,
    dependency: PreflightDependency = PreflightDependency.CASE,
) -> PreflightInput:
    return PreflightInput(
        authority=synthetic_authority(facts),
        dependency=dependency,
        value=facts,
    )


def synthetic_authority(facts: SyntheticPacketFacts) -> PreflightAuthority:
    """Build the exact current navigation authority for one fixture case."""

    return PreflightAuthority(
        case_id=facts.case_id,
        manifest_revision=facts.manifest_revision,
        manifest_sha256="0" * 64,
        template_binding=None,
        scope_catalog=PreflightScopeCatalog(
            case_id=facts.case_id,
            scopes=(
                PreflightScope(PreflightScopeKind.CASE, facts.case_id),
                PreflightScope(PreflightScopeKind.ASSIGNMENT, facts.document_id, 1),
                PreflightScope(PreflightScopeKind.PAGE, facts.page_id, 1),
            ),
        ),
    )


def load_synthetic_rule_cases(path: Path) -> tuple[SyntheticRuleCase, ...]:
    decoded = json.loads(path.read_text(encoding="utf-8"))
    if type(decoded) is not dict or frozenset(decoded) != _FIXTURE_MEMBERS:
        raise AssertionError("invalid synthetic preflight fixture")
    document = cast(dict[str, object], decoded)
    raw_cases = document["cases"]
    if document["fixture_version"] != 1 or type(raw_cases) is not list:
        raise AssertionError("invalid synthetic preflight fixture")
    result: list[SyntheticRuleCase] = []
    for raw_case in raw_cases:
        if type(raw_case) is not dict or frozenset(raw_case) != _CASE_MEMBERS:
            raise AssertionError("invalid synthetic preflight fixture case")
        item = cast(dict[str, object], raw_case)
        raw_codes = item["expected_codes"]
        if type(raw_codes) is not list or any(type(code) is not str for code in raw_codes):
            raise AssertionError("invalid synthetic preflight expected codes")
        result.append(
            SyntheticRuleCase(
                fixture_id=cast(str, item["fixture_id"]),
                facts=SyntheticPacketFacts(
                    case_id=UUID(cast(str, item["case_id"])),
                    manifest_revision=1,
                    document_id=UUID(cast(str, item["document_id"])),
                    page_id=UUID(cast(str, item["page_id"])),
                    document_count=cast(int, item["document_count"]),
                    quality_review_needed=cast(bool, item["quality_review_needed"]),
                    user_confirmation_recorded=cast(
                        bool,
                        item["user_confirmation_recorded"],
                    ),
                ),
                expected_codes=tuple(cast(list[str], raw_codes)),
            )
        )
    if not result or len({item.fixture_id for item in result}) != len(result):
        raise AssertionError("invalid synthetic preflight fixture identifiers")
    return tuple(result)


__all__ = (
    "CONFIRMATION_CODE",
    "MISSING_CODE",
    "QUALITY_CODE",
    "SyntheticPacketFacts",
    "SyntheticPacketRule",
    "SyntheticRuleCase",
    "load_synthetic_rule_cases",
    "synthetic_engine",
    "synthetic_authority",
    "synthetic_input",
)

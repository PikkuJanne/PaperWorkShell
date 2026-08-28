"""Deterministic, dependency-aware evaluation of registered preflight rules."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID

from paperworkshell.preflight.facts import validate_canonical_preflight_bindings
from paperworkshell.preflight.models import (
    MAX_PREFLIGHT_FINDINGS,
    MAX_PREFLIGHT_FINDINGS_PER_RULE,
    MAX_PREFLIGHT_RULES,
    PreflightAuthority,
    PreflightDependency,
    PreflightError,
    PreflightErrorCode,
    PreflightFinding,
    PreflightFindingDefinition,
    PreflightFindingTarget,
    PreflightInput,
    PreflightReport,
    PreflightScopeKind,
    _require_rule_id,
    _require_sha256,
    preflight_finding_sort_key,
    reject_preflight,
)
from paperworkshell.preflight.rules import (
    PreflightRule,
    PreflightRuleContext,
    PreflightRuleDescriptor,
)

PREFLIGHT_EVALUATION_VERSION = 1

type _RuleEvaluator = Callable[[PreflightRuleContext], tuple[PreflightFindingTarget, ...]]
type _FindingIdentity = tuple[
    str,
    PreflightScopeKind,
    UUID | str,
    PreflightScopeKind | None,
    UUID | str | None,
]


@dataclass(frozen=True, slots=True, repr=False)
class _RuleBinding:
    descriptor: PreflightRuleDescriptor
    evaluator: _RuleEvaluator = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True, repr=False)
class _RuleCacheEntry:
    rule_id: str
    descriptor_sha256: str = field(repr=False)
    dependency_revisions: tuple[tuple[PreflightDependency, str], ...] = field(repr=False)
    findings: tuple[PreflightFinding, ...] = field(repr=False)

    def __post_init__(self) -> None:
        _require_rule_id(self.rule_id, "preflight.cache_rule")
        _require_sha256(self.descriptor_sha256, "preflight.cache_descriptor")
        if (
            type(self.dependency_revisions) is not tuple
            or not self.dependency_revisions
            or any(
                type(item) is not tuple
                or len(item) != 2
                or type(item[0]) is not PreflightDependency
                or type(item[1]) is not str
                for item in self.dependency_revisions
            )
            or len({item[0] for item in self.dependency_revisions})
            != len(self.dependency_revisions)
            or self.dependency_revisions
            != tuple(sorted(self.dependency_revisions, key=lambda item: item[0].value))
            or type(self.findings) is not tuple
            or len(self.findings) > MAX_PREFLIGHT_FINDINGS_PER_RULE
            or any(type(finding) is not PreflightFinding for finding in self.findings)
            or any(finding.rule_id != self.rule_id for finding in self.findings)
            or self.findings != tuple(sorted(self.findings, key=preflight_finding_sort_key))
        ):
            reject_preflight(PreflightErrorCode.RESULT_INVALID, "preflight.cache")
        for _, revision in self.dependency_revisions:
            _require_sha256(revision, "preflight.cache_revision")


@dataclass(frozen=True, slots=True, repr=False)
class PreflightEvaluation:
    """One report plus content-free ephemeral state for incremental reuse."""

    report: PreflightReport
    authority: PreflightAuthority = field(repr=False)
    evaluated_rule_ids: tuple[str, ...]
    reused_rule_ids: tuple[str, ...]
    engine_sha256: str = field(repr=False)
    _cache_entries: tuple[_RuleCacheEntry, ...] = field(repr=False)
    evaluation_version: int = PREFLIGHT_EVALUATION_VERSION

    def __post_init__(self) -> None:
        _require_sha256(self.engine_sha256, "preflight.evaluation_engine")
        if (
            type(self.report) is not PreflightReport
            or type(self.authority) is not PreflightAuthority
            or type(self.evaluated_rule_ids) is not tuple
            or type(self.reused_rule_ids) is not tuple
            or any(type(item) is not str for item in self.evaluated_rule_ids)
            or any(type(item) is not str for item in self.reused_rule_ids)
            or len(set(self.evaluated_rule_ids)) != len(self.evaluated_rule_ids)
            or len(set(self.reused_rule_ids)) != len(self.reused_rule_ids)
            or not set(self.evaluated_rule_ids).isdisjoint(self.reused_rule_ids)
            or type(self._cache_entries) is not tuple
            or not self._cache_entries
            or len(self._cache_entries) > MAX_PREFLIGHT_RULES
            or any(type(entry) is not _RuleCacheEntry for entry in self._cache_entries)
            or tuple(entry.rule_id for entry in self._cache_entries)
            != tuple(sorted(entry.rule_id for entry in self._cache_entries))
            or set(self.evaluated_rule_ids) | set(self.reused_rule_ids)
            != {entry.rule_id for entry in self._cache_entries}
            or type(self.evaluation_version) is not int
            or self.evaluation_version != PREFLIGHT_EVALUATION_VERSION
        ):
            reject_preflight(PreflightErrorCode.RESULT_INVALID, "preflight.evaluation")
        all_findings = tuple(
            sorted(
                (finding for entry in self._cache_entries for finding in entry.findings),
                key=preflight_finding_sort_key,
            )
        )
        if self.report.findings != all_findings:
            reject_preflight(PreflightErrorCode.RESULT_INVALID, "preflight.evaluation_report")
        if (
            self.report.authority_sha256 != self.authority.sha256
            or self.report.engine_sha256 != self.engine_sha256
            or self.report.rule_ids != tuple(entry.rule_id for entry in self._cache_entries)
        ):
            reject_preflight(PreflightErrorCode.RESULT_INVALID, "preflight.evaluation_report")

    @property
    def evaluated_rule_count(self) -> int:
        return len(self.evaluated_rule_ids)

    @property
    def reused_rule_count(self) -> int:
        return len(self.reused_rule_ids)

    def __repr__(self) -> str:
        return (
            "PreflightEvaluation("
            f"finding_count={len(self.report.findings)}, "
            f"evaluated_rule_count={self.evaluated_rule_count}, "
            f"reused_rule_count={self.reused_rule_count})"
        )


class PreflightEngine:
    """Pure engine that runs only registered Python rules over opaque facts."""

    __slots__ = ("_bindings", "_engine_sha256")

    def __init__(self, rules: tuple[PreflightRule, ...]) -> None:
        if type(rules) is not tuple or not 1 <= len(rules) <= MAX_PREFLIGHT_RULES:
            reject_preflight(PreflightErrorCode.RULE_INVALID, "preflight.rules")
        bindings: list[_RuleBinding] = []
        for rule in rules:
            try:
                descriptor = rule.descriptor
                evaluator = rule.evaluate
            except Exception:
                reject_preflight(PreflightErrorCode.RULE_INVALID, "preflight.rule_access")
            if type(descriptor) is not PreflightRuleDescriptor or not callable(evaluator):
                reject_preflight(PreflightErrorCode.RULE_INVALID, "preflight.rule")
            bindings.append(_RuleBinding(descriptor, evaluator))
        bindings.sort(key=lambda item: item.descriptor.rule_id)
        rule_ids = tuple(binding.descriptor.rule_id for binding in bindings)
        if len(set(rule_ids)) != len(rule_ids):
            reject_preflight(PreflightErrorCode.RULE_INVALID, "preflight.rule_ids")
        finding_codes: set[str] = set()
        for binding in bindings:
            for definition in binding.descriptor.finding_definitions:
                if definition.code in finding_codes:
                    reject_preflight(PreflightErrorCode.RULE_INVALID, "preflight.finding_codes")
                finding_codes.add(definition.code)
        material = [
            {
                "rule_id": binding.descriptor.rule_id,
                "descriptor_sha256": binding.descriptor.sha256,
            }
            for binding in bindings
        ]
        encoded = json.dumps(
            material,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        self._bindings = tuple(bindings)
        self._engine_sha256 = hashlib.sha256(encoded).hexdigest()

    @property
    def descriptors(self) -> tuple[PreflightRuleDescriptor, ...]:
        return tuple(binding.descriptor for binding in self._bindings)

    @property
    def engine_sha256(self) -> str:
        return self._engine_sha256

    def evaluate(
        self,
        inputs: tuple[PreflightInput, ...],
        *,
        previous: PreflightEvaluation | None = None,
    ) -> PreflightEvaluation:
        """Evaluate changed dependencies and reuse only exact unaffected rule results."""

        input_by_dependency, authority = _validate_inputs(inputs)
        if previous is not None and type(previous) is not PreflightEvaluation:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.previous")
        if previous is not None and previous.authority.case_id != authority.case_id:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.previous_authority")
        prior_by_rule = (
            {}
            if previous is None or previous.authority != authority
            else {entry.rule_id: entry for entry in previous._cache_entries}
        )
        cache_entries: list[_RuleCacheEntry] = []
        evaluated_rule_ids: list[str] = []
        reused_rule_ids: list[str] = []
        finding_count = 0
        for binding in self._bindings:
            descriptor = binding.descriptor
            dependency_revisions = _dependency_revisions(
                descriptor,
                input_by_dependency,
            )
            prior = prior_by_rule.get(descriptor.rule_id)
            if (
                prior is not None
                and prior.descriptor_sha256 == descriptor.sha256
                and prior.dependency_revisions == dependency_revisions
            ):
                entry = prior
                reused_rule_ids.append(descriptor.rule_id)
            else:
                findings = _evaluate_rule(binding, input_by_dependency, authority)
                entry = _RuleCacheEntry(
                    rule_id=descriptor.rule_id,
                    descriptor_sha256=descriptor.sha256,
                    dependency_revisions=dependency_revisions,
                    findings=findings,
                )
                evaluated_rule_ids.append(descriptor.rule_id)
            finding_count += len(entry.findings)
            if finding_count > MAX_PREFLIGHT_FINDINGS:
                reject_preflight(PreflightErrorCode.LIMIT_EXCEEDED, "preflight.findings")
            cache_entries.append(entry)
        findings = tuple(
            sorted(
                (finding for entry in cache_entries for finding in entry.findings),
                key=preflight_finding_sort_key,
            )
        )
        report = PreflightReport(
            findings=findings,
            authority_sha256=authority.sha256,
            engine_sha256=self._engine_sha256,
            rule_ids=tuple(binding.descriptor.rule_id for binding in self._bindings),
        )
        return PreflightEvaluation(
            report=report,
            authority=authority,
            evaluated_rule_ids=tuple(evaluated_rule_ids),
            reused_rule_ids=tuple(reused_rule_ids),
            engine_sha256=self._engine_sha256,
            _cache_entries=tuple(cache_entries),
        )

    def __repr__(self) -> str:
        return f"PreflightEngine(rule_count={len(self._bindings)})"


def _validate_inputs(
    inputs: tuple[PreflightInput, ...],
) -> tuple[dict[PreflightDependency, PreflightInput], PreflightAuthority]:
    if (
        type(inputs) is not tuple
        or not inputs
        or len(inputs) > len(PreflightDependency)
        or any(type(item) is not PreflightInput for item in inputs)
    ):
        reject_preflight(
            PreflightErrorCode.DEPENDENCY_MISSING
            if type(inputs) is tuple and not inputs
            else PreflightErrorCode.MODEL_INVALID,
            "preflight.inputs",
        )
    authority = inputs[0].authority
    result: dict[PreflightDependency, PreflightInput] = {}
    for item in inputs:
        if item.authority != authority:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.input_authority")
        if item.dependency in result:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.input_duplicate")
        result[item.dependency] = item
    validate_canonical_preflight_bindings(result, authority)
    return result, authority


def _dependency_revisions(
    descriptor: PreflightRuleDescriptor,
    inputs: dict[PreflightDependency, PreflightInput],
) -> tuple[tuple[PreflightDependency, str], ...]:
    result: list[tuple[PreflightDependency, str]] = []
    for dependency in descriptor.dependencies:
        item = inputs.get(dependency)
        if item is None:
            reject_preflight(PreflightErrorCode.DEPENDENCY_MISSING, "preflight.input_missing")
        result.append((dependency, item.revision_sha256))
    return tuple(result)


def _evaluate_rule(
    binding: _RuleBinding,
    inputs: dict[PreflightDependency, PreflightInput],
    authority: PreflightAuthority,
) -> tuple[PreflightFinding, ...]:
    descriptor = binding.descriptor
    context = PreflightRuleContext(inputs, descriptor.dependencies)
    try:
        targets = binding.evaluator(context)
    except PreflightError as error:
        if type(error) is PreflightError:
            reject_preflight(error.code, error.context)
        reject_preflight(PreflightErrorCode.RULE_FAILED, "preflight.rule_evaluate")
    except Exception:
        reject_preflight(PreflightErrorCode.RULE_FAILED, "preflight.rule_evaluate")
    if type(targets) is not tuple:
        reject_preflight(PreflightErrorCode.RESULT_INVALID, "preflight.rule_result")
    if len(targets) > MAX_PREFLIGHT_FINDINGS_PER_RULE:
        reject_preflight(PreflightErrorCode.LIMIT_EXCEEDED, "preflight.rule_result")
    if any(type(target) is not PreflightFindingTarget for target in targets):
        reject_preflight(PreflightErrorCode.RESULT_INVALID, "preflight.rule_result")
    definitions: dict[str, PreflightFindingDefinition] = {
        definition.code: definition for definition in descriptor.finding_definitions
    }
    findings_by_identity: dict[_FindingIdentity, PreflightFinding] = {}
    for target in targets:
        if not authority.scope_catalog.contains(target.scope) or (
            target.related_scope is not None
            and not authority.scope_catalog.contains(target.related_scope)
        ):
            reject_preflight(PreflightErrorCode.RESULT_INVALID, "preflight.rule_scope")
        definition = definitions.get(target.code)
        if definition is None:
            reject_preflight(PreflightErrorCode.RESULT_INVALID, "preflight.rule_code")
        finding = PreflightFinding(
            rule_id=descriptor.rule_id,
            definition=definition,
            scope=target.scope,
            related_scope=target.related_scope,
            evidence=target.evidence,
        )
        identity: _FindingIdentity = finding.identity
        prior = findings_by_identity.get(identity)
        if prior is not None and prior != finding:
            reject_preflight(PreflightErrorCode.RESULT_INVALID, "preflight.rule_duplicate")
        findings_by_identity[identity] = finding
    return tuple(sorted(findings_by_identity.values(), key=preflight_finding_sort_key))


__all__ = (
    "PREFLIGHT_EVALUATION_VERSION",
    "PreflightEngine",
    "PreflightEvaluation",
)

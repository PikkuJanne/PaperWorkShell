"""Contract and deterministic property tests for PWS-0404 preflight."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from paperworkshell.preflight.engine import PreflightEngine, PreflightEvaluation
from paperworkshell.preflight.models import (
    MAX_PREFLIGHT_FINDINGS_PER_RULE,
    MAX_PREFLIGHT_SCOPES,
    PreflightAuthority,
    PreflightConfirmationRequirement,
    PreflightDependency,
    PreflightError,
    PreflightErrorCode,
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
from paperworkshell.preflight.rules import (
    PreflightRuleContext,
    PreflightRuleDescriptor,
)
from tests.helpers.preflight_rule_harness import (
    CONFIRMATION_CODE,
    MISSING_CODE,
    QUALITY_CODE,
    SyntheticPacketFacts,
    SyntheticRuleCase,
    load_synthetic_rule_cases,
    synthetic_authority,
    synthetic_engine,
    synthetic_input,
)

FIXTURE = Path("tests/fixtures/preflight/v1/engine-cases.json")
CASE_ID = UUID("10000000-0000-4000-8000-000000000010")
OTHER_CASE_ID = UUID("10000000-0000-4000-8000-000000000011")
DOCUMENT_ID = UUID("20000000-0000-4000-8000-000000000010")
PAGE_ID = UUID("30000000-0000-4000-8000-000000000010")
SECOND_PAGE_ID = UUID("30000000-0000-4000-8000-000000000011")
OUTPUT_KEY = "synthetic-output"
ZERO_SHA256 = "0" * 64
ONE_SHA256 = "1" * 64


@dataclass(frozen=True, slots=True)
class _NamedFacts(PreflightFactValue):
    case_id: UUID
    manifest_revision: int
    marker: int


def _authority(
    *,
    case_id: UUID = CASE_ID,
    manifest_revision: int = 1,
    manifest_sha256: str = ZERO_SHA256,
) -> PreflightAuthority:
    return PreflightAuthority(
        case_id=case_id,
        manifest_revision=manifest_revision,
        manifest_sha256=manifest_sha256,
        template_binding=None,
        scope_catalog=PreflightScopeCatalog(
            case_id=case_id,
            scopes=(
                PreflightScope(PreflightScopeKind.CASE, case_id),
                PreflightScope(PreflightScopeKind.ASSIGNMENT, DOCUMENT_ID, 1),
                PreflightScope(PreflightScopeKind.PAGE, PAGE_ID, 1),
                PreflightScope(PreflightScopeKind.PAGE, SECOND_PAGE_ID, 2),
                PreflightScope(PreflightScopeKind.OUTPUT, OUTPUT_KEY, 1),
            ),
        ),
    )


DEFAULT_AUTHORITY = _authority()


class _Rule:
    def __init__(
        self,
        descriptor: PreflightRuleDescriptor,
        evaluator: Callable[[PreflightRuleContext], tuple[PreflightFindingTarget, ...]],
    ) -> None:
        self._descriptor = descriptor
        self._evaluator = evaluator
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
        return self._evaluator(context)


def _definition(
    code: str,
    severity: PreflightSeverity = PreflightSeverity.WARNING,
) -> PreflightFindingDefinition:
    return PreflightFindingDefinition(
        code=code,
        severity=severity,
        remediation_action=(
            PreflightRemediationAction.REVIEW_TEMPLATE_STATEMENT
            if severity is PreflightSeverity.CONFIRMATION_REQUIRED
            else PreflightRemediationAction.REVIEW_CASE
        ),
        message="A synthetic check needs review.",
        remediation="Review the indicated item before export.",
        confirmation=(
            PreflightConfirmationRequirement("synthetic.reviewed", 1)
            if severity is PreflightSeverity.CONFIRMATION_REQUIRED
            else None
        ),
    )


def _rule(
    *,
    rule_id: str,
    code: str,
    dependency: PreflightDependency,
    scope: PreflightScope,
    severity: PreflightSeverity = PreflightSeverity.WARNING,
    version: int = 1,
) -> _Rule:
    descriptor = PreflightRuleDescriptor(
        rule_id=rule_id,
        rule_version=version,
        dependencies=(dependency,),
        finding_definitions=(_definition(code, severity),),
    )

    def evaluate(context: PreflightRuleContext) -> tuple[PreflightFindingTarget, ...]:
        context.require(dependency, _NamedFacts)
        return (PreflightFindingTarget(code, scope),)

    return _Rule(descriptor, evaluate)


def _input(
    dependency: PreflightDependency,
    *,
    marker: int = 1,
    authority: PreflightAuthority = DEFAULT_AUTHORITY,
) -> PreflightInput:
    return PreflightInput(
        authority,
        dependency,
        _NamedFacts(authority.case_id, authority.manifest_revision, marker),
    )


@pytest.mark.parametrize(
    "case", load_synthetic_rule_cases(FIXTURE), ids=lambda item: item.fixture_id
)
def test_versioned_rule_harness_distinguishes_all_finding_classes(
    case: SyntheticRuleCase,
) -> None:
    selected = case
    evaluation = synthetic_engine().evaluate((synthetic_input(selected.facts),))

    assert tuple(finding.code for finding in evaluation.report.findings) == selected.expected_codes
    assert evaluation.report.has_blocking_errors is (MISSING_CODE in selected.expected_codes)
    assert evaluation.report.requires_user_confirmation is (
        CONFIRMATION_CODE in selected.expected_codes
    )
    assert tuple(finding.code for finding in evaluation.report.warnings) == (
        (QUALITY_CODE,) if QUALITY_CODE in selected.expected_codes else ()
    )
    assert all(type(finding.scope.item_id) is UUID for finding in evaluation.report.findings)


def test_confirmation_definition_requires_exact_typed_statement() -> None:
    with pytest.raises(PreflightError) as missing:
        _definition("SYNTHETIC_CONFIRM", PreflightSeverity.CONFIRMATION_REQUIRED).__class__(
            code="SYNTHETIC_CONFIRM",
            severity=PreflightSeverity.CONFIRMATION_REQUIRED,
            remediation_action=PreflightRemediationAction.REVIEW_TEMPLATE_STATEMENT,
            message="Review is required.",
            remediation="Review the item.",
        )

    assert missing.value.code is PreflightErrorCode.MODEL_INVALID
    assert "Review is required" not in repr(missing.value)


@pytest.mark.parametrize(
    "scope",
    (
        lambda: PreflightScope(PreflightScopeKind.CASE, CASE_ID, 1),
        lambda: PreflightScope(PreflightScopeKind.PAGE, PAGE_ID),
        lambda: PreflightScope(PreflightScopeKind.PAGE, PAGE_ID, 0),
        lambda: PreflightScope(PreflightScopeKind.PAGE, UUID(int=0), 1),
    ),
)
def test_scope_rejects_missing_or_invalid_navigation_identity(
    scope: Callable[[], PreflightScope],
) -> None:
    with pytest.raises(PreflightError) as captured:
        scope()

    assert captured.value.code is PreflightErrorCode.MODEL_INVALID


def test_scope_catalog_capacity_covers_released_case_maxima() -> None:
    def generated_uuid(namespace: int, position: int) -> UUID:
        return UUID(int=(namespace << 96) | (4 << 76) | (2 << 62) | position)

    case_id = generated_uuid(1, 0)
    scopes = [PreflightScope(PreflightScopeKind.CASE, case_id)]
    scopes.extend(
        PreflightScope(PreflightScopeKind.REQUIREMENT, f"requirement-{position}", position)
        for position in range(1, 129)
    )
    for kind, namespace in (
        (PreflightScopeKind.SOURCE, 2),
        (PreflightScopeKind.ASSIGNMENT, 3),
        (PreflightScopeKind.PAGE, 4),
    ):
        scopes.extend(
            PreflightScope(kind, generated_uuid(namespace, position), position)
            for position in range(1, 4_097)
        )
    scopes.extend(
        PreflightScope(PreflightScopeKind.OUTPUT, f"output-{position}", position)
        for position in range(1, 4_097)
    )

    catalog = PreflightScopeCatalog(case_id, tuple(scopes))

    assert len(catalog.scopes) == MAX_PREFLIGHT_SCOPES == 16_513


def test_scope_catalog_rejects_second_case_and_noncanonical_order() -> None:
    with pytest.raises(PreflightError) as second_case:
        PreflightScopeCatalog(
            CASE_ID,
            (
                PreflightScope(PreflightScopeKind.CASE, CASE_ID),
                PreflightScope(PreflightScopeKind.CASE, OTHER_CASE_ID),
            ),
        )
    with pytest.raises(PreflightError) as reordered:
        PreflightScopeCatalog(
            CASE_ID,
            (
                PreflightScope(PreflightScopeKind.CASE, CASE_ID),
                PreflightScope(PreflightScopeKind.PAGE, SECOND_PAGE_ID, 2),
                PreflightScope(PreflightScopeKind.PAGE, PAGE_ID, 1),
            ),
        )

    assert second_case.value.code is PreflightErrorCode.MODEL_INVALID
    assert reordered.value.code is PreflightErrorCode.MODEL_INVALID


def test_input_requires_fact_marker_and_hides_value_and_digest() -> None:
    facts = _NamedFacts(CASE_ID, 1, 7)
    selected = PreflightInput(DEFAULT_AUTHORITY, PreflightDependency.CASE, facts)
    equivalent = PreflightInput(
        DEFAULT_AUTHORITY,
        PreflightDependency.CASE,
        _NamedFacts(CASE_ID, 1, 7),
    )
    changed = PreflightInput(
        DEFAULT_AUTHORITY,
        PreflightDependency.CASE,
        _NamedFacts(CASE_ID, 1, 8),
    )

    assert selected.value is facts
    assert selected.revision_sha256 == equivalent.revision_sha256
    assert selected.revision_sha256 != changed.revision_sha256
    assert ZERO_SHA256 not in repr(selected)
    assert "marker" not in repr(selected)
    with pytest.raises(PreflightError):
        PreflightInput(
            DEFAULT_AUTHORITY,
            PreflightDependency.CASE,
            cast(PreflightFactValue, {"private": "value"}),
        )


def test_engine_computed_identity_includes_private_dataclass_fields() -> None:
    @dataclass(frozen=True, slots=True)
    class _PrivateFacts(PreflightFactValue):
        case_id: UUID
        manifest_revision: int
        _marker: int

    first = PreflightInput(
        DEFAULT_AUTHORITY,
        PreflightDependency.CASE,
        _PrivateFacts(CASE_ID, 1, 1),
    )
    second = PreflightInput(
        DEFAULT_AUTHORITY,
        PreflightDependency.CASE,
        _PrivateFacts(CASE_ID, 1, 2),
    )

    assert first.revision_sha256 != second.revision_sha256


def test_descriptor_canonicalizes_dependencies_definitions_and_fingerprint() -> None:
    first = PreflightRuleDescriptor(
        rule_id="synthetic.canonical",
        rule_version=1,
        dependencies=(PreflightDependency.TEMPLATE, PreflightDependency.CASE),
        finding_definitions=(_definition("SYNTHETIC_Z"), _definition("SYNTHETIC_A")),
    )
    second = PreflightRuleDescriptor(
        rule_id="synthetic.canonical",
        rule_version=1,
        dependencies=(PreflightDependency.CASE, PreflightDependency.TEMPLATE),
        finding_definitions=(_definition("SYNTHETIC_A"), _definition("SYNTHETIC_Z")),
    )

    assert first == second
    assert first.sha256 == second.sha256
    assert first.dependencies == (PreflightDependency.CASE, PreflightDependency.TEMPLATE)
    assert tuple(item.code for item in first.finding_definitions) == (
        "SYNTHETIC_A",
        "SYNTHETIC_Z",
    )
    assert replace(first, rule_version=2).sha256 != first.sha256
    assert (
        replace(
            first,
            finding_definitions=(
                replace(first.finding_definitions[0], message="A changed static message."),
                first.finding_definitions[1],
            ),
        ).sha256
        != first.sha256
    )


def test_engine_output_is_independent_of_rule_registration_order() -> None:
    case_rule = _rule(
        rule_id="synthetic.case",
        code="SYNTHETIC_CASE_WARNING",
        dependency=PreflightDependency.CASE,
        scope=PreflightScope(PreflightScopeKind.CASE, CASE_ID),
    )
    output_rule = _rule(
        rule_id="synthetic.output",
        code="SYNTHETIC_OUTPUT_ERROR",
        dependency=PreflightDependency.OUTPUT_PLAN,
        scope=PreflightScope(PreflightScopeKind.OUTPUT, OUTPUT_KEY, 1),
        severity=PreflightSeverity.BLOCKING_ERROR,
    )
    inputs = (_input(PreflightDependency.OUTPUT_PLAN), _input(PreflightDependency.CASE))

    first = PreflightEngine((case_rule, output_rule)).evaluate(inputs)
    second = PreflightEngine((output_rule, case_rule)).evaluate(tuple(reversed(inputs)))

    assert first.report == second.report
    assert tuple(item.code for item in first.report.findings) == (
        "SYNTHETIC_OUTPUT_ERROR",
        "SYNTHETIC_CASE_WARNING",
    )
    assert first.engine_sha256 == second.engine_sha256
    assert first.report.engine_sha256 == first.engine_sha256
    assert first.report.authority_sha256 == first.authority.sha256
    assert first.report.rule_ids == ("synthetic.case", "synthetic.output")


def test_exact_duplicate_targets_collapse_but_position_conflict_fails_closed() -> None:
    definition = _definition("SYNTHETIC_DUPLICATE")
    descriptor = PreflightRuleDescriptor(
        "synthetic.duplicate",
        1,
        (PreflightDependency.CASE,),
        (definition,),
    )
    target = PreflightFindingTarget(
        definition.code,
        PreflightScope(PreflightScopeKind.PAGE, PAGE_ID, 1),
    )
    duplicate_rule = _Rule(descriptor, lambda _: (target, target))

    report = PreflightEngine((duplicate_rule,)).evaluate((_input(PreflightDependency.CASE),)).report
    assert len(report.findings) == 1

    conflicting_rule = _Rule(
        descriptor,
        lambda _: (
            target,
            PreflightFindingTarget(
                definition.code,
                PreflightScope(PreflightScopeKind.PAGE, PAGE_ID, 2),
            ),
        ),
    )
    with pytest.raises(PreflightError) as captured:
        PreflightEngine((conflicting_rule,)).evaluate((_input(PreflightDependency.CASE),))
    assert captured.value.code is PreflightErrorCode.RESULT_INVALID


def test_related_scope_is_typed_catalogued_and_part_of_finding_identity() -> None:
    definition = _definition("SYNTHETIC_RELATED")
    descriptor = PreflightRuleDescriptor(
        "synthetic.related",
        1,
        (PreflightDependency.CASE,),
        (definition,),
    )
    target = PreflightFindingTarget(
        definition.code,
        PreflightScope(PreflightScopeKind.PAGE, PAGE_ID, 1),
        related_scope=PreflightScope(PreflightScopeKind.PAGE, SECOND_PAGE_ID, 2),
    )

    report = (
        PreflightEngine((_Rule(descriptor, lambda _: (target,)),))
        .evaluate((_input(PreflightDependency.CASE),))
        .report
    )

    assert report.findings[0].related_scope == target.related_scope
    with pytest.raises(PreflightError) as same_scope:
        PreflightFindingTarget(
            definition.code,
            target.scope,
            related_scope=target.scope,
        )
    assert same_scope.value.code is PreflightErrorCode.RESULT_INVALID


def test_unknown_finding_code_and_non_tuple_result_fail_closed() -> None:
    definition = _definition("SYNTHETIC_KNOWN")
    descriptor = PreflightRuleDescriptor(
        "synthetic.invalid-result",
        1,
        (PreflightDependency.CASE,),
        (definition,),
    )
    unknown = _Rule(
        descriptor,
        lambda _: (
            PreflightFindingTarget(
                "SYNTHETIC_UNKNOWN",
                PreflightScope(PreflightScopeKind.CASE, CASE_ID),
            ),
        ),
    )
    with pytest.raises(PreflightError) as code_error:
        PreflightEngine((unknown,)).evaluate((_input(PreflightDependency.CASE),))
    assert code_error.value.code is PreflightErrorCode.RESULT_INVALID

    invalid = _Rule(
        descriptor,
        cast(
            Callable[[PreflightRuleContext], tuple[PreflightFindingTarget, ...]],
            lambda _: [],
        ),
    )
    with pytest.raises(PreflightError) as type_error:
        PreflightEngine((invalid,)).evaluate((_input(PreflightDependency.CASE),))
    assert type_error.value.code is PreflightErrorCode.RESULT_INVALID


def test_rule_failure_is_sanitized_without_raw_exception_chaining() -> None:
    canary = "private-source-name-and-path-canary"
    descriptor = PreflightRuleDescriptor(
        "synthetic.failure",
        1,
        (PreflightDependency.CASE,),
        (_definition("SYNTHETIC_FAILURE"),),
    )

    def fail(_: PreflightRuleContext) -> tuple[PreflightFindingTarget, ...]:
        raise RuntimeError(canary)

    with pytest.raises(PreflightError) as captured:
        PreflightEngine((_Rule(descriptor, fail),)).evaluate((_input(PreflightDependency.CASE),))

    assert captured.value.code is PreflightErrorCode.RULE_FAILED
    assert captured.value.__cause__ is None
    assert canary not in str(captured.value)
    assert canary not in repr(captured.value)


def test_rule_cannot_read_undeclared_dependency_or_wrong_fact_type() -> None:
    descriptor = PreflightRuleDescriptor(
        "synthetic.dependency",
        1,
        (PreflightDependency.CASE,),
        (_definition("SYNTHETIC_DEPENDENCY"),),
    )

    def undeclared(context: PreflightRuleContext) -> tuple[PreflightFindingTarget, ...]:
        context.require(PreflightDependency.TEMPLATE, _NamedFacts)
        return ()

    with pytest.raises(PreflightError) as dependency_error:
        PreflightEngine((_Rule(descriptor, undeclared),)).evaluate(
            (
                _input(PreflightDependency.CASE),
                _input(PreflightDependency.TEMPLATE),
            )
        )
    assert dependency_error.value.code is PreflightErrorCode.DEPENDENCY_UNDECLARED

    def wrong_type(context: PreflightRuleContext) -> tuple[PreflightFindingTarget, ...]:
        context.require(PreflightDependency.CASE, SyntheticPacketFacts)
        return ()

    with pytest.raises(PreflightError) as type_error:
        PreflightEngine((_Rule(descriptor, wrong_type),)).evaluate(
            (_input(PreflightDependency.CASE),)
        )
    assert type_error.value.code is PreflightErrorCode.MODEL_INVALID


def test_missing_and_duplicate_inputs_fail_before_rule_execution() -> None:
    rule = _rule(
        rule_id="synthetic.required-input",
        code="SYNTHETIC_REQUIRED_INPUT",
        dependency=PreflightDependency.CASE,
        scope=PreflightScope(PreflightScopeKind.CASE, CASE_ID),
    )
    engine = PreflightEngine((rule,))

    with pytest.raises(PreflightError) as missing:
        engine.evaluate(())
    assert missing.value.code is PreflightErrorCode.DEPENDENCY_MISSING
    assert rule.invocation_count == 0

    selected = _input(PreflightDependency.CASE)
    with pytest.raises(PreflightError) as duplicate:
        engine.evaluate((selected, selected))
    assert duplicate.value.code is PreflightErrorCode.MODEL_INVALID
    assert rule.invocation_count == 0


def test_mixed_input_authority_and_cross_case_previous_state_fail_closed() -> None:
    rule = _rule(
        rule_id="synthetic.authority",
        code="SYNTHETIC_AUTHORITY",
        dependency=PreflightDependency.CASE,
        scope=PreflightScope(PreflightScopeKind.CASE, CASE_ID),
    )
    engine = PreflightEngine((rule,))
    other_authority = _authority(case_id=OTHER_CASE_ID)

    with pytest.raises(PreflightError) as mixed:
        engine.evaluate(
            (
                _input(PreflightDependency.CASE),
                _input(PreflightDependency.TEMPLATE, authority=other_authority),
            )
        )
    assert mixed.value.code is PreflightErrorCode.MODEL_INVALID
    assert rule.invocation_count == 0

    first = engine.evaluate((_input(PreflightDependency.CASE),))
    with pytest.raises(PreflightError) as cross_case:
        engine.evaluate(
            (_input(PreflightDependency.CASE, authority=other_authority),),
            previous=first,
        )
    assert cross_case.value.code is PreflightErrorCode.MODEL_INVALID


def test_changed_same_case_authority_forces_safe_full_reevaluation() -> None:
    rule = _rule(
        rule_id="synthetic.authority-refresh",
        code="SYNTHETIC_AUTHORITY_REFRESH",
        dependency=PreflightDependency.CASE,
        scope=PreflightScope(PreflightScopeKind.CASE, CASE_ID),
    )
    engine = PreflightEngine((rule,))
    first = engine.evaluate((_input(PreflightDependency.CASE),))
    changed_authority = _authority(manifest_revision=2, manifest_sha256=ONE_SHA256)

    second = engine.evaluate(
        (_input(PreflightDependency.CASE, authority=changed_authority),),
        previous=first,
    )

    assert second.evaluated_rule_ids == ("synthetic.authority-refresh",)
    assert second.reused_rule_ids == ()
    assert second.authority == changed_authority
    assert rule.invocation_count == 2


def test_incremental_evaluation_reruns_only_rules_with_changed_dependencies() -> None:
    case_rule = _rule(
        rule_id="synthetic.incremental-case",
        code="SYNTHETIC_INCREMENTAL_CASE",
        dependency=PreflightDependency.CASE,
        scope=PreflightScope(PreflightScopeKind.CASE, CASE_ID),
    )
    template_rule = _rule(
        rule_id="synthetic.incremental-template",
        code="SYNTHETIC_INCREMENTAL_TEMPLATE",
        dependency=PreflightDependency.TEMPLATE,
        scope=PreflightScope(PreflightScopeKind.ASSIGNMENT, DOCUMENT_ID, 1),
    )
    engine = PreflightEngine((template_rule, case_rule))
    first = engine.evaluate(
        (
            _input(PreflightDependency.CASE),
            _input(PreflightDependency.TEMPLATE),
            _input(PreflightDependency.QUALITY_FINDINGS),
        )
    )
    second = engine.evaluate(
        (
            _input(PreflightDependency.QUALITY_FINDINGS, marker=2),
            _input(PreflightDependency.TEMPLATE),
            _input(PreflightDependency.CASE, marker=2),
        ),
        previous=first,
    )

    assert first.evaluated_rule_ids == (
        "synthetic.incremental-case",
        "synthetic.incremental-template",
    )
    assert first.reused_rule_ids == ()
    assert second.evaluated_rule_ids == ("synthetic.incremental-case",)
    assert second.reused_rule_ids == ("synthetic.incremental-template",)
    assert case_rule.invocation_count == 2
    assert template_rule.invocation_count == 1
    assert second.report == first.report


def test_rule_version_and_static_definition_changes_invalidate_prior_result() -> None:
    first_rule = _rule(
        rule_id="synthetic.versioned",
        code="SYNTHETIC_VERSIONED",
        dependency=PreflightDependency.CASE,
        scope=PreflightScope(PreflightScopeKind.CASE, CASE_ID),
    )
    first = PreflightEngine((first_rule,)).evaluate((_input(PreflightDependency.CASE),))
    second_rule = _rule(
        rule_id="synthetic.versioned",
        code="SYNTHETIC_VERSIONED",
        dependency=PreflightDependency.CASE,
        scope=PreflightScope(PreflightScopeKind.CASE, CASE_ID),
        version=2,
    )
    second = PreflightEngine((second_rule,)).evaluate(
        (_input(PreflightDependency.CASE),),
        previous=first,
    )

    assert second.evaluated_rule_ids == ("synthetic.versioned",)
    assert second.reused_rule_ids == ()
    assert second_rule.invocation_count == 1


def test_identical_input_permutations_yield_equivalent_finding_sets() -> None:
    case_rule = _rule(
        rule_id="synthetic.property-case",
        code="SYNTHETIC_PROPERTY_CASE",
        dependency=PreflightDependency.CASE,
        scope=PreflightScope(PreflightScopeKind.PAGE, PAGE_ID, 1),
    )
    output_rule = _rule(
        rule_id="synthetic.property-output",
        code="SYNTHETIC_PROPERTY_OUTPUT",
        dependency=PreflightDependency.OUTPUT_PLAN,
        scope=PreflightScope(PreflightScopeKind.OUTPUT, OUTPUT_KEY, 1),
    )
    inputs = (
        _input(PreflightDependency.CASE),
        _input(PreflightDependency.OUTPUT_PLAN),
        _input(PreflightDependency.QUALITY_FINDINGS),
    )
    expected = PreflightEngine((case_rule, output_rule)).evaluate(inputs).report

    for input_order in itertools.permutations(inputs):
        for rule_order in ((case_rule, output_rule), (output_rule, case_rule)):
            assert PreflightEngine(rule_order).evaluate(input_order).report == expected


def test_rule_result_capacity_is_bounded_before_materialization() -> None:
    definition = _definition("SYNTHETIC_CAPACITY")
    descriptor = PreflightRuleDescriptor(
        "synthetic.capacity",
        1,
        (PreflightDependency.CASE,),
        (definition,),
    )
    target = PreflightFindingTarget(
        definition.code,
        PreflightScope(PreflightScopeKind.CASE, CASE_ID),
    )
    rule = _Rule(descriptor, lambda _: (target,) * (MAX_PREFLIGHT_FINDINGS_PER_RULE + 1))

    with pytest.raises(PreflightError) as captured:
        PreflightEngine((rule,)).evaluate((_input(PreflightDependency.CASE),))
    assert captured.value.code is PreflightErrorCode.LIMIT_EXCEEDED


def test_previous_evaluation_must_be_exact_typed_state() -> None:
    engine = synthetic_engine()
    case = load_synthetic_rule_cases(FIXTURE)[0]

    with pytest.raises(PreflightError) as captured:
        engine.evaluate(
            (synthetic_input(case.facts),),
            previous=cast(PreflightEvaluation, object()),
        )
    assert captured.value.code is PreflightErrorCode.MODEL_INVALID


def test_static_presentation_rejects_controls_and_dynamic_multiline_content() -> None:
    for value in (" leading", "trailing ", "line one\nline two", "hidden\u202evalue"):
        with pytest.raises(PreflightError) as captured:
            PreflightFindingDefinition(
                code="SYNTHETIC_PRESENTATION",
                severity=PreflightSeverity.WARNING,
                remediation_action=PreflightRemediationAction.REVIEW_CASE,
                message=value,
                remediation="Review the item.",
            )
        assert captured.value.code is PreflightErrorCode.MODEL_INVALID


def test_engine_never_calls_an_extra_callable_template_fact() -> None:
    @dataclass(frozen=True, slots=True)
    class _CallableFacts(PreflightFactValue):
        case_id: UUID
        manifest_revision: int
        marker: int

        def __call__(self) -> None:
            raise AssertionError("fact callables must remain inert")

    case = load_synthetic_rule_cases(FIXTURE)[0]
    callable_fact = _CallableFacts(case.facts.case_id, case.facts.manifest_revision, 1)
    extra = PreflightInput(
        synthetic_authority(case.facts),
        PreflightDependency.TEMPLATE,
        callable_fact,
    )

    evaluation = synthetic_engine().evaluate((extra, synthetic_input(case.facts)))

    assert evaluation.report.findings == ()
    assert "callable" not in repr(evaluation)

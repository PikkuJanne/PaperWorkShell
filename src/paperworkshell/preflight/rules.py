"""Registered rule contracts for the pure preflight engine."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from paperworkshell.preflight.models import (
    MAX_PREFLIGHT_FINDING_DEFINITIONS,
    MAX_PREFLIGHT_RULE_VERSION,
    PreflightDependency,
    PreflightErrorCode,
    PreflightFactValue,
    PreflightFindingDefinition,
    PreflightFindingTarget,
    PreflightInput,
    _require_rule_id,
    reject_preflight,
)


@dataclass(frozen=True, slots=True, repr=False)
class PreflightRuleDescriptor:
    """Versioned dependencies and static findings owned by one code rule."""

    rule_id: str
    rule_version: int
    dependencies: tuple[PreflightDependency, ...]
    finding_definitions: tuple[PreflightFindingDefinition, ...] = field(repr=False)
    _sha256: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        _require_rule_id(self.rule_id, "preflight.rule_id")
        if (
            type(self.rule_version) is not int
            or not 1 <= self.rule_version <= MAX_PREFLIGHT_RULE_VERSION
            or type(self.dependencies) is not tuple
            or not self.dependencies
            or any(type(item) is not PreflightDependency for item in self.dependencies)
            or len(set(self.dependencies)) != len(self.dependencies)
            or type(self.finding_definitions) is not tuple
            or not self.finding_definitions
            or len(self.finding_definitions) > MAX_PREFLIGHT_FINDING_DEFINITIONS
            or any(
                type(item) is not PreflightFindingDefinition for item in self.finding_definitions
            )
        ):
            reject_preflight(PreflightErrorCode.RULE_INVALID, "preflight.rule_descriptor")
        dependencies = tuple(sorted(self.dependencies, key=lambda item: item.value))
        definitions = tuple(sorted(self.finding_definitions, key=lambda item: item.code))
        if len({definition.code for definition in definitions}) != len(definitions):
            reject_preflight(PreflightErrorCode.RULE_INVALID, "preflight.rule_codes")
        object.__setattr__(self, "dependencies", dependencies)
        object.__setattr__(self, "finding_definitions", definitions)
        material = {
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "dependencies": [dependency.value for dependency in dependencies],
            "finding_definitions": [
                {
                    "code": definition.code,
                    "severity": definition.severity.value,
                    "remediation_action": definition.remediation_action.value,
                    "message": definition.message,
                    "remediation": definition.remediation,
                    "definition_version": definition.definition_version,
                    "confirmation": (
                        None
                        if definition.confirmation is None
                        else {
                            "confirmation_code": definition.confirmation.confirmation_code,
                            "request_model_version": (
                                definition.confirmation.request_model_version
                            ),
                        }
                    ),
                }
                for definition in definitions
            ],
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
        """Return the exact semantic identity used for incremental reuse."""

        return self._sha256

    def __repr__(self) -> str:
        return (
            "PreflightRuleDescriptor("
            f"rule_id={self.rule_id!r}, rule_version={self.rule_version!r}, "
            f"dependency_count={len(self.dependencies)}, "
            f"finding_definition_count={len(self.finding_definitions)})"
        )


class PreflightRuleContext:
    """Read-only view exposing only one rule's declared fact projections."""

    __slots__ = ("_allowed", "_inputs")

    def __init__(
        self,
        inputs: Mapping[PreflightDependency, PreflightInput],
        allowed: tuple[PreflightDependency, ...],
    ) -> None:
        if (
            not isinstance(inputs, Mapping)
            or type(allowed) is not tuple
            or not allowed
            or any(type(item) is not PreflightDependency for item in allowed)
            or len(set(allowed)) != len(allowed)
        ):
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.rule_context")
        selected: dict[PreflightDependency, PreflightInput] = {}
        for dependency in allowed:
            item = inputs.get(dependency)
            if type(item) is not PreflightInput:
                reject_preflight(
                    PreflightErrorCode.DEPENDENCY_MISSING,
                    "preflight.context_input",
                )
            selected[dependency] = item
        self._allowed = frozenset(allowed)
        self._inputs = selected

    @property
    def dependencies(self) -> tuple[PreflightDependency, ...]:
        """Return the closed dependency inventory visible to the rule."""

        return tuple(sorted(self._allowed, key=lambda item: item.value))

    def require[T: PreflightFactValue](
        self,
        dependency: PreflightDependency,
        expected_type: type[T],
    ) -> T:
        """Return one fact projection after exact dependency and type checks."""

        if type(dependency) is not PreflightDependency:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.context_dependency")
        if dependency not in self._allowed:
            reject_preflight(
                PreflightErrorCode.DEPENDENCY_UNDECLARED,
                "preflight.context_dependency",
            )
        item = self._inputs.get(dependency)
        if type(item) is not PreflightInput:
            reject_preflight(
                PreflightErrorCode.DEPENDENCY_MISSING,
                "preflight.context_input",
            )
        value = item.value
        if type(value) is not expected_type:
            reject_preflight(PreflightErrorCode.MODEL_INVALID, "preflight.context_value")
        return value

    def __repr__(self) -> str:
        return f"PreflightRuleContext(dependency_count={len(self._allowed)})"


class PreflightRule(Protocol):
    """One deterministic code rule; templates cannot construct or execute it."""

    @property
    def descriptor(self) -> PreflightRuleDescriptor: ...

    def evaluate(
        self,
        context: PreflightRuleContext,
        /,
    ) -> tuple[PreflightFindingTarget, ...]: ...


__all__ = (
    "PreflightRule",
    "PreflightRuleContext",
    "PreflightRuleDescriptor",
)

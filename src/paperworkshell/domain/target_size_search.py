"""Bounded policy and content-free evidence for staged target-size export.

The order expresses increasing permitted image change, never an assumption that
encoded sizes decrease monotonically. Actual package constraints are evaluated
by the canonical preflight evaluator after each verified export attempt.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Never

from paperworkshell.domain.manifest_repository import MAX_SAFE_MANIFEST_INTEGER
from paperworkshell.domain.page_operations import MAX_PAGE_COUNT
from paperworkshell.domain.pdf_export import PdfExportErrorCode, reject_pdf_export
from paperworkshell.domain.pdf_image_stages import PdfImageStageProfile
from paperworkshell.domain.resource_policy import ResourcePolicy

TARGET_SIZE_SEARCH_POLICY_VERSION: Final = 1
TARGET_SIZE_DECISION_REPORT_VERSION: Final = 1
TARGET_SIZE_PROFILE_LADDER: Final[tuple[PdfImageStageProfile | None, ...]] = (
    None,
    PdfImageStageProfile.PRESERVE_300_DPI_Q90,
    PdfImageStageProfile.PRESERVE_240_DPI_Q85,
    PdfImageStageProfile.PRESERVE_200_DPI_Q80,
)
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")
_CODE: Final = re.compile(r"[A-Z][A-Z0-9_]{0,79}\Z")


def _invalid(context: str) -> Never:
    reject_pdf_export(PdfExportErrorCode.MODEL_INVALID, f"target_size.{context}")


def _integer(value: object, *, maximum: int = MAX_SAFE_MANIFEST_INTEGER, minimum: int = 0) -> bool:
    return type(value) is int and minimum <= value <= maximum


@dataclass(frozen=True, slots=True)
class TargetSizeSearchPolicy:
    """A fixed policy prefix with a deadline bounded by the resource policy.

    ``None`` in ``profiles`` means a verified safe-optimization attempt. Every
    image policy must use that immutable baseline, not an earlier lossy output.
    The total wall-clock allowance is at most four worker CPU-time budgets;
    individual workers still enforce their own CPU and resource limits.
    """

    resource_policy: ResourcePolicy
    max_attempts: int
    max_duration_ms: int
    policy_version: int = TARGET_SIZE_SEARCH_POLICY_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.resource_policy) is not ResourcePolicy
            or not _integer(self.max_attempts, minimum=1, maximum=len(TARGET_SIZE_PROFILE_LADDER))
            or not _integer(self.max_duration_ms, minimum=1)
            or type(self.policy_version) is not int
            or self.policy_version != TARGET_SIZE_SEARCH_POLICY_VERSION
        ):
            _invalid("policy")
        if self.max_duration_ms > self.resource_policy.worker_cpu_time_ms * len(
            TARGET_SIZE_PROFILE_LADDER
        ):
            _invalid("duration")

    @classmethod
    def for_resource_policy(
        cls,
        resource_policy: ResourcePolicy,
        *,
        max_attempts: int = len(TARGET_SIZE_PROFILE_LADDER),
        max_duration_ms: int | None = None,
    ) -> TargetSizeSearchPolicy:
        """Derive the default deadline from the real enforced resource policy."""

        if type(resource_policy) is not ResourcePolicy:
            _invalid("resource_policy")
        duration = (
            resource_policy.worker_cpu_time_ms * len(TARGET_SIZE_PROFILE_LADDER)
            if max_duration_ms is None
            else max_duration_ms
        )
        return cls(resource_policy, max_attempts, duration)

    @property
    def profiles(self) -> tuple[PdfImageStageProfile | None, ...]:
        return TARGET_SIZE_PROFILE_LADDER[: self.max_attempts]

    @property
    def resource_policy_fingerprint(self) -> str:
        """Bind every numeric budget, including custom constrained test budgets."""

        encoded = json.dumps(
            self.resource_policy.to_evidence(), sort_keys=True, separators=(",", ":")
        ).encode("ascii")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class TargetSizeAttempt:
    """Measured package facts; positions and attempt ordinals are one-based.

    A satisfied attempt says that output constraints passed; the separate
    package-export result remains authoritative about destination commit state.
    Blocking codes are kept out of representations, even for invalid callers.
    """

    ordinal: int
    profile: PdfImageStageProfile | None
    pdf_byte_sizes: tuple[int, ...]
    archive_byte_size: int | None
    constraints_satisfied: bool
    blocking_codes: tuple[str, ...] = field(default=(), repr=False)
    unsupported_output_positions: tuple[int, ...] = ()
    budget_skipped: bool = False

    def __post_init__(self) -> None:
        if (
            not _integer(self.ordinal, minimum=1, maximum=len(TARGET_SIZE_PROFILE_LADDER))
            or (self.profile is not None and type(self.profile) is not PdfImageStageProfile)
            or type(self.pdf_byte_sizes) is not tuple
            or not 1 <= len(self.pdf_byte_sizes) <= MAX_PAGE_COUNT
            or any(not _integer(size, minimum=1) for size in self.pdf_byte_sizes)
            or (
                self.archive_byte_size is not None
                and not _integer(self.archive_byte_size, minimum=1)
            )
            or type(self.constraints_satisfied) is not bool
            or type(self.budget_skipped) is not bool
            or type(self.blocking_codes) is not tuple
            or len(self.blocking_codes) > MAX_PAGE_COUNT
            or any(
                type(code) is not str or not _CODE.fullmatch(code) for code in self.blocking_codes
            )
            or tuple(sorted(set(self.blocking_codes))) != self.blocking_codes
            or self.constraints_satisfied != (not self.blocking_codes)
            or type(self.unsupported_output_positions) is not tuple
            or len(self.unsupported_output_positions) > len(self.pdf_byte_sizes)
            or any(
                not _integer(position, minimum=1, maximum=len(self.pdf_byte_sizes))
                for position in self.unsupported_output_positions
            )
            or tuple(sorted(set(self.unsupported_output_positions)))
            != self.unsupported_output_positions
        ):
            _invalid("attempt")


class TargetSizeSearchTerminal(StrEnum):
    """Finite reasons the search stopped; none asserts destination commit."""

    SATISFIED = "satisfied"
    UNATTAINABLE = "unattainable"
    WORK_LIMIT = "work_limit"
    OTHER_CONSTRAINTS = "other_constraints"
    UNSUPPORTED_POLICY = "unsupported_policy"


@dataclass(frozen=True, slots=True)
class TargetSizeDecisionReport:
    """Reproducible policy choice bound to the package's semantic input digest.

    Attempts must be a contiguous ladder prefix and stop at the first success.
    Size changes are deliberately unconstrained: JPEG/PDF/ZIP bytes need not be
    monotonic. ``UNATTAINABLE`` means this full supported ladder was exhausted,
    not that no possible encoding could ever meet the target.
    """

    semantic_fingerprint: str = field(repr=False)
    policy: TargetSizeSearchPolicy
    attempts: tuple[TargetSizeAttempt, ...]
    terminal: TargetSizeSearchTerminal
    selected_profile: PdfImageStageProfile | None = None
    report_version: int = TARGET_SIZE_DECISION_REPORT_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.semantic_fingerprint) is not str
            or _SHA256.fullmatch(self.semantic_fingerprint) is None
            or type(self.policy) is not TargetSizeSearchPolicy
            or type(self.attempts) is not tuple
            or any(type(attempt) is not TargetSizeAttempt for attempt in self.attempts)
            or type(self.terminal) is not TargetSizeSearchTerminal
            or (
                self.selected_profile is not None
                and type(self.selected_profile) is not PdfImageStageProfile
            )
            or type(self.report_version) is not int
            or self.report_version != TARGET_SIZE_DECISION_REPORT_VERSION
        ):
            _invalid("report")
        if len(self.attempts) > self.policy.max_attempts:
            _invalid("work_limit")
        if (
            tuple(attempt.ordinal for attempt in self.attempts)
            != tuple(range(1, len(self.attempts) + 1))
            or tuple(attempt.profile for attempt in self.attempts)
            != self.policy.profiles[: len(self.attempts)]
        ):
            _invalid("attempt_order")
        if self.attempts and any(
            len(attempt.pdf_byte_sizes) != len(self.attempts[0].pdf_byte_sizes)
            or (attempt.archive_byte_size is None) != (self.attempts[0].archive_byte_size is None)
            for attempt in self.attempts
        ):
            _invalid("output_binding")
        successes = tuple(attempt for attempt in self.attempts if attempt.constraints_satisfied)
        if self.terminal is TargetSizeSearchTerminal.SATISFIED:
            if (
                len(successes) != 1
                or successes[0] != self.attempts[-1]
                or self.selected_profile != successes[0].profile
            ):
                _invalid("selection")
        elif successes or self.selected_profile is not None:
            _invalid("unsatisfied_selection")
        elif self.terminal is TargetSizeSearchTerminal.UNATTAINABLE:
            if len(self.attempts) != len(TARGET_SIZE_PROFILE_LADDER) or any(
                attempt.budget_skipped for attempt in self.attempts
            ):
                _invalid("incomplete_ladder")
        elif self.terminal is not TargetSizeSearchTerminal.WORK_LIMIT and not self.attempts:
            _invalid("missing_attempt")

    @property
    def resource_policy_fingerprint(self) -> str:
        return self.policy.resource_policy_fingerprint

    @property
    def message(self) -> str:
        """Describe exactly the search outcome; saved-file status is separate."""

        if self.terminal is TargetSizeSearchTerminal.WORK_LIMIT and any(
            attempt.budget_skipped for attempt in self.attempts
        ):
            return "The search could not evaluate every image change within its resource limits."
        return {
            TargetSizeSearchTerminal.SATISFIED: (
                "The selected verified candidate meets the output constraints."
            ),
            TargetSizeSearchTerminal.UNATTAINABLE: (
                "The size target could not be met within the supported image policies."
            ),
            TargetSizeSearchTerminal.WORK_LIMIT: (
                "The search stopped at its work limit before finding a candidate that meets the constraints."
            ),
            TargetSizeSearchTerminal.OTHER_CONSTRAINTS: (
                "Other output constraints must be resolved before the package can be saved."
            ),
            TargetSizeSearchTerminal.UNSUPPORTED_POLICY: (
                "The remaining size target requires image changes that this policy does not support."
            ),
        }[self.terminal]

    def suggestions(self, *, allow_splitting: bool) -> tuple[str, ...]:
        """Return fixed plain-language alternatives without changing constraints."""

        if type(allow_splitting) is not bool:
            _invalid("suggestions")
        if self.terminal is TargetSizeSearchTerminal.SATISFIED:
            return ()
        if self.terminal is TargetSizeSearchTerminal.OTHER_CONSTRAINTS:
            return ("Review and resolve the reported output constraints, then try again.",)
        suggestions = ["Increase the allowed size if the recipient's instructions permit it."]
        if allow_splitting:
            suggestions.append("Split the package into more files if the recipient permits it.")
        if self.terminal is TargetSizeSearchTerminal.WORK_LIMIT:
            suggestions.append("Review the configured work limits before trying the search again.")
        if self.terminal is TargetSizeSearchTerminal.UNSUPPORTED_POLICY or any(
            attempt.unsupported_output_positions for attempt in self.attempts
        ):
            suggestions.append(
                "Keep unsupported images unchanged and review another permitted submission option."
            )
        return tuple(suggestions)

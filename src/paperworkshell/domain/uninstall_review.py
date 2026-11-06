"""Path-free, bounded consent and accounting for application removal."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final
from uuid import UUID

from paperworkshell.domain.case_lifecycle import require_case_id

MAX_UNINSTALL_CASES: Final = 4096


class UninstallReviewErrorCode(StrEnum):
    INVALID = "UNINSTALL_REVIEW_INVALID"
    INSPECTION_FAILED = "UNINSTALL_INSPECTION_FAILED"
    CONFIRMATION_REQUIRED = "UNINSTALL_CONFIRMATION_REQUIRED"
    REVIEW_STALE = "UNINSTALL_REVIEW_STALE"
    CANCELLED = "UNINSTALL_REVIEW_CANCELLED"
    APPLICATION_RUNNING = "UNINSTALL_APPLICATION_RUNNING"


@dataclass(slots=True, repr=False)
class UninstallReviewError(ValueError):
    """Closed failure; raw filesystem exceptions never enter presentation."""

    code: UninstallReviewErrorCode

    def __post_init__(self) -> None:
        if type(self.code) is not UninstallReviewErrorCode:
            self.code = UninstallReviewErrorCode.INVALID
        self.args = (self.code.value,)

    def __str__(self) -> str:
        return self.code.value


class UninstallDeletionConfirmation(StrEnum):
    DELETE_REVIEWED_MANAGED_CASES = "delete_reviewed_managed_cases"


@dataclass(frozen=True, slots=True)
class UninstallCaseReference:
    """Exact reviewed manifest identity without retaining full case documents."""

    case_id: UUID
    manifest_sha256: str = field(repr=False)
    case_identity_sha256: str = field(repr=False)

    def __post_init__(self) -> None:
        require_case_id(self.case_id, context="uninstall.case_id")
        if any(
            type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in (self.manifest_sha256, self.case_identity_sha256)
        ):
            raise UninstallReviewError(UninstallReviewErrorCode.INVALID)


@dataclass(frozen=True, slots=True)
class UninstallCaseInventory:
    """Only recognized cases are counted; unknown entries always remain."""

    cases: tuple[UninstallCaseReference, ...]
    unavailable_count: int = 0

    def __post_init__(self) -> None:
        if (
            type(self.cases) is not tuple
            or any(type(case) is not UninstallCaseReference for case in self.cases)
            or type(self.unavailable_count) is not int
            or not 0 <= self.unavailable_count <= MAX_UNINSTALL_CASES
            or len(self.cases) + self.unavailable_count > MAX_UNINSTALL_CASES
            or len({case.case_id for case in self.cases}) != len(self.cases)
        ):
            raise UninstallReviewError(UninstallReviewErrorCode.INVALID)


@dataclass(frozen=True, slots=True)
class UninstallReview:
    review_id: UUID
    inventory: UninstallCaseInventory

    def __post_init__(self) -> None:
        require_case_id(self.review_id, context="uninstall.review_id")
        if type(self.inventory) is not UninstallCaseInventory:
            raise UninstallReviewError(UninstallReviewErrorCode.INVALID)


@dataclass(frozen=True, slots=True)
class UninstallRemovalResult:
    """Partial deletion is data remaining, never a completed removal claim."""

    deleted_count: int
    remaining_count: int
    failed_count: int
    cancelled: bool

    def __post_init__(self) -> None:
        counts = (self.deleted_count, self.remaining_count, self.failed_count)
        if (
            any(type(count) is not int or not 0 <= count <= MAX_UNINSTALL_CASES for count in counts)
            or self.deleted_count + self.remaining_count > MAX_UNINSTALL_CASES
            or self.failed_count > self.remaining_count
            or type(self.cancelled) is not bool
        ):
            raise UninstallReviewError(UninstallReviewErrorCode.INVALID)

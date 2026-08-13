"""Pure case-lifecycle models, result contracts, and sanitized failures."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, Never
from uuid import UUID

LEGACY_CASE_MANIFEST_VERSION: Final = 1
CURRENT_CASE_MANIFEST_VERSION: Final = 2
SUPPORTED_CASE_MANIFEST_VERSIONS: Final = frozenset(
    {LEGACY_CASE_MANIFEST_VERSION, CURRENT_CASE_MANIFEST_VERSION}
)
MAX_SAFE_MANIFEST_INTEGER: Final = 9_007_199_254_740_991

_CONTEXT_TOKEN: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")


class CaseLifecycleErrorCode(StrEnum):
    """Closed registry of safe case-lifecycle failures."""

    CASE_ID_INVALID = "CASE_ID_INVALID"
    ACTIVE_CASE_CONFLICT = "CASE_ACTIVE_CONFLICT"
    ACTIVE_CASE_MISMATCH = "CASE_ACTIVE_MISMATCH"
    NO_ACTIVE_CASE = "CASE_ACTIVE_NONE"
    CASE_NOT_FOUND = "CASE_NOT_FOUND"
    CASE_INCOMPLETE = "CASE_INCOMPLETE"
    MANIFEST_INVALID = "CASE_MANIFEST_INVALID"
    MANIFEST_VERSION_UNSUPPORTED = "CASE_MANIFEST_VERSION_UNSUPPORTED"
    MANIFEST_CASE_ID_MISMATCH = "CASE_MANIFEST_CASE_ID_MISMATCH"
    CREATE_FAILED = "CASE_CREATE_FAILED"
    OPEN_FAILED = "CASE_OPEN_FAILED"
    CLOSE_FAILED = "CASE_CLOSE_FAILED"
    LIST_FAILED = "CASE_LIST_FAILED"
    DELETE_CONFIRMATION_REQUIRED = "CASE_DELETE_CONFIRMATION_REQUIRED"
    DELETE_ACTIVE_CASE = "CASE_DELETE_ACTIVE"
    DELETE_FAILED = "CASE_DELETE_FAILED"
    INTERNAL_ERROR = "CASE_INTERNAL_ERROR"


_SAFE_ERROR_MESSAGES: Final[dict[CaseLifecycleErrorCode, str]] = {
    CaseLifecycleErrorCode.CASE_ID_INVALID: (
        "The selected case does not have a valid internal identifier."
    ),
    CaseLifecycleErrorCode.ACTIVE_CASE_CONFLICT: (
        "Close the current case before opening or creating another case."
    ),
    CaseLifecycleErrorCode.ACTIVE_CASE_MISMATCH: (
        "The requested case is not the case currently open."
    ),
    CaseLifecycleErrorCode.NO_ACTIVE_CASE: "No case is currently open.",
    CaseLifecycleErrorCode.CASE_NOT_FOUND: "The selected case is not available.",
    CaseLifecycleErrorCode.CASE_INCOMPLETE: (
        "The selected case is incomplete and cannot be opened."
    ),
    CaseLifecycleErrorCode.MANIFEST_INVALID: (
        "The selected case record is invalid and was not changed."
    ),
    CaseLifecycleErrorCode.MANIFEST_VERSION_UNSUPPORTED: (
        "This case uses an unsupported record version and was not changed."
    ),
    CaseLifecycleErrorCode.MANIFEST_CASE_ID_MISMATCH: (
        "The selected case record does not match its internal identifier."
    ),
    CaseLifecycleErrorCode.CREATE_FAILED: ("PaperWorkShell could not create the case safely."),
    CaseLifecycleErrorCode.OPEN_FAILED: "PaperWorkShell could not open the selected case safely.",
    CaseLifecycleErrorCode.CLOSE_FAILED: (
        "PaperWorkShell could not finish closing the selected case."
    ),
    CaseLifecycleErrorCode.LIST_FAILED: (
        "PaperWorkShell could not inspect the local case list safely."
    ),
    CaseLifecycleErrorCode.DELETE_CONFIRMATION_REQUIRED: (
        "Confirm removal from PaperWorkShell case storage before continuing."
    ),
    CaseLifecycleErrorCode.DELETE_ACTIVE_CASE: (
        "Close the selected case before removing it from case storage."
    ),
    CaseLifecycleErrorCode.DELETE_FAILED: (
        "PaperWorkShell could not completely remove the selected case. "
        "Some application-managed files may remain."
    ),
    CaseLifecycleErrorCode.INTERNAL_ERROR: (
        "PaperWorkShell could not complete the case operation safely."
    ),
}


@dataclass(slots=True, repr=False)
class CaseLifecycleError(ValueError):
    """A stable failure containing no path, manifest value, or raw exception text."""

    code: CaseLifecycleErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not CaseLifecycleErrorCode:
            self.code = CaseLifecycleErrorCode.INTERNAL_ERROR
        if type(self.context) is not str or _CONTEXT_TOKEN.fullmatch(self.context) is None:
            self.context = "case.lifecycle"
        self.safe_message = _SAFE_ERROR_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"CaseLifecycleError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_case_lifecycle(code: CaseLifecycleErrorCode, context: str) -> Never:
    """Raise one closed, sanitized lifecycle failure."""

    raise CaseLifecycleError(code=code, context=context)


def require_case_id(value: object, *, context: str = "case.id") -> UUID:
    """Require an opaque RFC-4122 UUIDv4 without accepting display or path text."""

    if type(value) is not UUID or value.version != 4:
        reject_case_lifecycle(CaseLifecycleErrorCode.CASE_ID_INVALID, context)
    return value


class CaseDeletionConfirmation(StrEnum):
    """Exact destructive intent accepted by the lifecycle boundary."""

    REMOVE_FROM_APPLICATION_STORAGE = "remove_from_application_storage"


class CaseDeletionStatus(StrEnum):
    """Whether all authenticated application-managed case objects were removed."""

    COMPLETE = "complete"
    PARTIAL = "partial"


class CaseDeletionAssurance(StrEnum):
    """The deliberately limited claim made by a deletion result."""

    APPLICATION_STORAGE_REMOVAL_ONLY = "application_storage_removal_only"


@dataclass(frozen=True, slots=True)
class CaseSummary:
    """Path-free lifecycle metadata for one valid supported-version case."""

    case_id: UUID
    manifest_version: int
    revision: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        require_case_id(self.case_id, context="case.summary_id")
        if (
            type(self.manifest_version) is not int
            or self.manifest_version not in SUPPORTED_CASE_MANIFEST_VERSIONS
        ):
            reject_case_lifecycle(
                CaseLifecycleErrorCode.MANIFEST_VERSION_UNSUPPORTED,
                "case.summary_version",
            )
        if type(self.revision) is not int or not 0 <= self.revision <= MAX_SAFE_MANIFEST_INTEGER:
            reject_case_lifecycle(
                CaseLifecycleErrorCode.MANIFEST_INVALID,
                "case.summary_revision",
            )
        _require_utc_timestamp(self.created_at, context="case.summary_created")
        _require_utc_timestamp(self.updated_at, context="case.summary_updated")
        if self.updated_at < self.created_at:
            reject_case_lifecycle(
                CaseLifecycleErrorCode.MANIFEST_INVALID,
                "case.summary_timestamps",
            )


@dataclass(frozen=True, slots=True)
class CaseIndexIssue:
    """One unavailable internal case ID and its sanitized reason."""

    case_id: UUID
    code: CaseLifecycleErrorCode

    def __post_init__(self) -> None:
        require_case_id(self.case_id, context="case.index_issue_id")
        if not isinstance(self.code, CaseLifecycleErrorCode):
            reject_case_lifecycle(CaseLifecycleErrorCode.LIST_FAILED, "case.index_issue_code")


@dataclass(frozen=True, slots=True)
class CaseIndex:
    """Deterministic valid-case listing plus non-valid internal case entries."""

    cases: tuple[CaseSummary, ...] = ()
    unavailable: tuple[CaseIndexIssue, ...] = ()

    def __post_init__(self) -> None:
        if type(self.cases) is not tuple or not all(
            isinstance(item, CaseSummary) for item in self.cases
        ):
            reject_case_lifecycle(CaseLifecycleErrorCode.LIST_FAILED, "case.index_cases")
        if type(self.unavailable) is not tuple or not all(
            isinstance(item, CaseIndexIssue) for item in self.unavailable
        ):
            reject_case_lifecycle(CaseLifecycleErrorCode.LIST_FAILED, "case.index_unavailable")
        cases = tuple(sorted(self.cases, key=lambda item: str(item.case_id)))
        unavailable = tuple(sorted(self.unavailable, key=lambda item: str(item.case_id)))
        available_ids = tuple(item.case_id for item in cases)
        unavailable_ids = tuple(item.case_id for item in unavailable)
        if len(set(available_ids)) != len(available_ids) or len(set(unavailable_ids)) != len(
            unavailable_ids
        ):
            reject_case_lifecycle(CaseLifecycleErrorCode.LIST_FAILED, "case.index_duplicate")
        if not set(available_ids).isdisjoint(unavailable_ids):
            reject_case_lifecycle(CaseLifecycleErrorCode.LIST_FAILED, "case.index_overlap")
        object.__setattr__(self, "cases", cases)
        object.__setattr__(self, "unavailable", unavailable)


@dataclass(frozen=True, slots=True)
class CaseDeletionResult:
    """Sanitized deletion accounting that explicitly avoids an erasure claim."""

    case_id: UUID
    status: CaseDeletionStatus
    removed_files: int
    removed_directories: int
    released_bytes: int
    assurance: CaseDeletionAssurance = CaseDeletionAssurance.APPLICATION_STORAGE_REMOVAL_ONLY
    failure_code: CaseLifecycleErrorCode | None = None

    def __post_init__(self) -> None:
        require_case_id(self.case_id, context="case.deletion_id")
        if not isinstance(self.status, CaseDeletionStatus):
            reject_case_lifecycle(CaseLifecycleErrorCode.DELETE_FAILED, "case.deletion_status")
        counts = (self.removed_files, self.removed_directories, self.released_bytes)
        if any(
            type(value) is not int or not 0 <= value <= MAX_SAFE_MANIFEST_INTEGER
            for value in counts
        ):
            reject_case_lifecycle(CaseLifecycleErrorCode.DELETE_FAILED, "case.deletion_counts")
        if self.assurance is not CaseDeletionAssurance.APPLICATION_STORAGE_REMOVAL_ONLY:
            reject_case_lifecycle(CaseLifecycleErrorCode.DELETE_FAILED, "case.deletion_assurance")
        if self.status is CaseDeletionStatus.COMPLETE and self.failure_code is not None:
            reject_case_lifecycle(CaseLifecycleErrorCode.DELETE_FAILED, "case.deletion_complete")
        if self.status is CaseDeletionStatus.PARTIAL and not isinstance(
            self.failure_code, CaseLifecycleErrorCode
        ):
            reject_case_lifecycle(CaseLifecycleErrorCode.DELETE_FAILED, "case.deletion_partial")

    @property
    def safe_message(self) -> str:
        """Describe application-level removal without promising physical erasure."""

        if self.status is CaseDeletionStatus.COMPLETE:
            return (
                "PaperWorkShell removed the case from its application-managed storage. "
                "This does not guarantee forensic erasure from storage devices or backups."
            )
        return (
            "PaperWorkShell could not remove all application-managed case files. "
            "Some files may remain, and forensic erasure is not guaranteed."
        )


def _require_utc_timestamp(value: datetime, *, context: str) -> None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        reject_case_lifecycle(CaseLifecycleErrorCode.MANIFEST_INVALID, context)

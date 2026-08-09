"""Pure, path-free startup recovery results and sanitized failures."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Never
from uuid import UUID

MAX_RECOVERY_COUNT: Final = (1 << 53) - 1
_CONTEXT_TOKEN: Final = re.compile(r"[a-z][a-z0-9_.]{0,63}\Z")


class RecoveryErrorCode(StrEnum):
    """Closed registry of safe startup-recovery failures."""

    MODEL_INVALID = "RECOVERY_MODEL_INVALID"
    SCAN_FAILED = "RECOVERY_SCAN_FAILED"
    INTERNAL_ERROR = "RECOVERY_INTERNAL_ERROR"


_SAFE_ERROR_MESSAGES: Final[dict[RecoveryErrorCode, str]] = {
    RecoveryErrorCode.MODEL_INVALID: ("PaperWorkShell received invalid startup-recovery data."),
    RecoveryErrorCode.SCAN_FAILED: (
        "PaperWorkShell could not inspect local cases for recovery safely."
    ),
    RecoveryErrorCode.INTERNAL_ERROR: (
        "PaperWorkShell could not complete startup recovery safely."
    ),
}


@dataclass(slots=True, repr=False)
class RecoveryError(ValueError):
    """Stable recovery failure containing no paths or document values."""

    code: RecoveryErrorCode
    context: str
    safe_message: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.code) is not RecoveryErrorCode:
            self.code = RecoveryErrorCode.INTERNAL_ERROR
        if type(self.context) is not str or _CONTEXT_TOKEN.fullmatch(self.context) is None:
            self.context = "recovery.internal"
        self.safe_message = _SAFE_ERROR_MESSAGES[self.code]
        self.args = (self.code.value, self.context, self.safe_message)

    def __repr__(self) -> str:
        return f"RecoveryError(code={self.code.value!r}, context={self.context!r})"

    def __str__(self) -> str:
        return f"{self.code.value} at {self.context}: {self.safe_message}"


def reject_recovery(code: RecoveryErrorCode, context: str) -> Never:
    """Raise one closed recovery failure without retaining an exception chain."""

    error = RecoveryError(code=code, context=context)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


class CaseRecoveryStatus(StrEnum):
    """Authoritative startup state for one internal case."""

    CURRENT = "current"
    RECOVERED = "recovered"
    UNAVAILABLE = "unavailable"


class RecoveryIssueCode(StrEnum):
    """Closed user-safe reasons why recovery needs attention."""

    MANIFEST_INVALID = "RECOVERY_MANIFEST_INVALID"
    MANIFEST_VERSION_UNSUPPORTED = "RECOVERY_MANIFEST_VERSION_UNSUPPORTED"
    MANIFEST_RECOVERY_FAILED = "RECOVERY_MANIFEST_RECOVERY_FAILED"
    CASE_CHANGED = "RECOVERY_CASE_CHANGED"
    SOURCE_ARTIFACTS_RETAINED = "RECOVERY_SOURCE_ARTIFACTS_RETAINED"
    THUMBNAIL_CLEANUP_PARTIAL = "RECOVERY_THUMBNAIL_CLEANUP_PARTIAL"
    TEMP_CLEANUP_PARTIAL = "RECOVERY_TEMP_CLEANUP_PARTIAL"
    EXPORT_COMMIT_AMBIGUOUS = "RECOVERY_EXPORT_COMMIT_AMBIGUOUS"
    EXPORT_HISTORY_PENDING = "RECOVERY_EXPORT_HISTORY_PENDING"
    EXPORT_RETRY_INVALID = "RECOVERY_EXPORT_RETRY_INVALID"


class StartupRecoveryStatus(StrEnum):
    """Aggregate state suitable for a plain-language startup notice."""

    COMPLETE = "complete"
    ATTENTION_REQUIRED = "attention_required"


def _require_count(value: object, *, context: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_RECOVERY_COUNT:
        reject_recovery(RecoveryErrorCode.MODEL_INVALID, context)
    return value


@dataclass(frozen=True, slots=True)
class ExportHistoryRecoveryResult:
    """Bounded, content-free settlement facts for one case export journal."""

    repaired_entries: int = 0
    pending_entries: int = 0
    ambiguous_entries: int = 0
    invalid_entries: int = 0

    def __post_init__(self) -> None:
        for value, context in (
            (self.repaired_entries, "recovery.export_repaired"),
            (self.pending_entries, "recovery.export_pending"),
            (self.ambiguous_entries, "recovery.export_ambiguous"),
            (self.invalid_entries, "recovery.export_invalid"),
        ):
            _require_count(value, context=context)

    @property
    def issues(self) -> tuple[RecoveryIssueCode, ...]:
        issues: list[RecoveryIssueCode] = []
        if self.ambiguous_entries:
            issues.append(RecoveryIssueCode.EXPORT_COMMIT_AMBIGUOUS)
        if self.pending_entries:
            issues.append(RecoveryIssueCode.EXPORT_HISTORY_PENDING)
        if self.invalid_entries:
            issues.append(RecoveryIssueCode.EXPORT_RETRY_INVALID)
        return tuple(issues)


@dataclass(frozen=True, slots=True)
class CaseRecoveryResult:
    """Content-free recovery and cleanup accounting for one case."""

    case_id: UUID
    status: CaseRecoveryStatus
    manifest_revision: int | None
    issues: tuple[RecoveryIssueCode, ...] = ()
    removed_source_orphans: int = 0
    retained_source_artifacts: int = 0
    removed_thumbnail_stages: int = 0
    removed_temp_files: int = 0
    removed_temp_directories: int = 0
    repaired_export_history: int = 0
    pending_export_history: int = 0
    ambiguous_export_commits: int = 0
    invalid_export_retries: int = 0
    released_bytes: int = 0

    def __post_init__(self) -> None:
        if type(self.case_id) is not UUID or self.case_id.version != 4:
            reject_recovery(RecoveryErrorCode.MODEL_INVALID, "recovery.case_id")
        if type(self.status) is not CaseRecoveryStatus:
            reject_recovery(RecoveryErrorCode.MODEL_INVALID, "recovery.case_status")
        if self.status is CaseRecoveryStatus.UNAVAILABLE:
            if self.manifest_revision is not None or not self.issues:
                reject_recovery(RecoveryErrorCode.MODEL_INVALID, "recovery.case_unavailable")
        elif (
            type(self.manifest_revision) is not int
            or not 0 <= self.manifest_revision <= MAX_RECOVERY_COUNT
        ):
            reject_recovery(RecoveryErrorCode.MODEL_INVALID, "recovery.case_revision")
        if type(self.issues) is not tuple or not all(
            type(issue) is RecoveryIssueCode for issue in self.issues
        ):
            reject_recovery(RecoveryErrorCode.MODEL_INVALID, "recovery.case_issues")
        normalized_issues = tuple(sorted(set(self.issues), key=lambda issue: issue.value))
        if len(normalized_issues) != len(self.issues):
            reject_recovery(RecoveryErrorCode.MODEL_INVALID, "recovery.case_issues")
        if self.status is CaseRecoveryStatus.UNAVAILABLE and any(
            value != 0
            for value in (
                self.removed_source_orphans,
                self.retained_source_artifacts,
                self.removed_thumbnail_stages,
                self.removed_temp_files,
                self.removed_temp_directories,
                self.repaired_export_history,
                self.pending_export_history,
                self.ambiguous_export_commits,
                self.invalid_export_retries,
                self.released_bytes,
            )
        ):
            reject_recovery(RecoveryErrorCode.MODEL_INVALID, "recovery.case_unavailable")
        for value, context in (
            (self.removed_source_orphans, "recovery.source_removed"),
            (self.retained_source_artifacts, "recovery.source_retained"),
            (self.removed_thumbnail_stages, "recovery.thumbnail_removed"),
            (self.removed_temp_files, "recovery.temp_files"),
            (self.removed_temp_directories, "recovery.temp_directories"),
            (self.repaired_export_history, "recovery.export_repaired"),
            (self.pending_export_history, "recovery.export_pending"),
            (self.ambiguous_export_commits, "recovery.export_ambiguous"),
            (self.invalid_export_retries, "recovery.export_invalid"),
            (self.released_bytes, "recovery.released_bytes"),
        ):
            _require_count(value, context=context)
        object.__setattr__(self, "issues", normalized_issues)


@dataclass(frozen=True, slots=True)
class StartupRecoveryResult:
    """Deterministic startup scan result containing only safe case facts."""

    cases: tuple[CaseRecoveryResult, ...] = ()

    def __post_init__(self) -> None:
        if type(self.cases) is not tuple or not all(
            type(result) is CaseRecoveryResult for result in self.cases
        ):
            reject_recovery(RecoveryErrorCode.MODEL_INVALID, "recovery.result_cases")
        ordered = tuple(sorted(self.cases, key=lambda result: str(result.case_id)))
        case_ids = tuple(result.case_id for result in ordered)
        if len(case_ids) != len(set(case_ids)):
            reject_recovery(RecoveryErrorCode.MODEL_INVALID, "recovery.result_duplicate")
        object.__setattr__(self, "cases", ordered)

    @property
    def status(self) -> StartupRecoveryStatus:
        if any(
            result.status is CaseRecoveryStatus.UNAVAILABLE or result.issues
            for result in self.cases
        ):
            return StartupRecoveryStatus.ATTENTION_REQUIRED
        return StartupRecoveryStatus.COMPLETE

    @property
    def scanned_cases(self) -> int:
        return len(self.cases)

    @property
    def recovered_cases(self) -> int:
        return sum(result.status is CaseRecoveryStatus.RECOVERED for result in self.cases)

    @property
    def unavailable_cases(self) -> int:
        return sum(result.status is CaseRecoveryStatus.UNAVAILABLE for result in self.cases)

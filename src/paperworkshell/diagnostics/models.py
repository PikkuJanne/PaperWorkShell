"""Closed, content-free models for routine PaperWorkShell diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from paperworkshell.diagnostics.errors import (
    DiagnosticErrorCode,
    DiagnosticErrorContext,
    reject_diagnostic,
)

DIAGNOSTIC_RECORD_VERSION: Final = 1
MAX_SAFE_DIAGNOSTIC_INTEGER: Final = 9_007_199_254_740_991
MAX_DIAGNOSTIC_VERSION_COMPONENT: Final = 999_999_999
MAX_DIAGNOSTIC_VERSION_COMPONENTS: Final = 4
MAX_DIAGNOSTIC_VERSIONS: Final = 16
MAX_DIAGNOSTIC_COUNTS: Final = 32
MAX_DIAGNOSTIC_RESOURCES: Final = 32


class DiagnosticEventCode(StrEnum):
    """Closed routine event registry; no free-form log messages are accepted."""

    APPLICATION_STARTED = "APPLICATION_STARTED"
    APPLICATION_STOPPED = "APPLICATION_STOPPED"
    RECOVERY_SCAN_STARTED = "RECOVERY_SCAN_STARTED"
    RECOVERY_SCAN_COMPLETED = "RECOVERY_SCAN_COMPLETED"
    CASE_RECOVERY_COMPLETED = "CASE_RECOVERY_COMPLETED"
    CLEANUP_COMPLETED = "CLEANUP_COMPLETED"
    WORKER_STARTED = "WORKER_STARTED"
    WORKER_STOPPED = "WORKER_STOPPED"
    WORKER_FAILED = "WORKER_FAILED"
    OPERATION_STARTED = "OPERATION_STARTED"
    OPERATION_COMPLETED = "OPERATION_COMPLETED"


class DiagnosticOperationKind(StrEnum):
    """Closed operation kinds that may be named in routine diagnostics."""

    PROTOCOL_NOOP = "protocol.noop"
    PDF_INSPECT = "pdf.inspect"
    PDF_THUMBNAIL = "pdf.thumbnail"
    PDF_EXPORT = "pdf.export"


class DiagnosticOutcome(StrEnum):
    """Closed terminal outcomes for a diagnostic event."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class DiagnosticFailureCode(StrEnum):
    """Generic failures that disclose no rejected value or implementation detail."""

    RECOVERY_SCAN_FAILED = "RECOVERY_SCAN_FAILED"
    CASE_INVALID = "CASE_INVALID"
    MANIFEST_INVALID = "MANIFEST_INVALID"
    CACHE_INVALID = "CACHE_INVALID"
    CLEANUP_FAILED = "CLEANUP_FAILED"
    WORKER_LAUNCH_FAILED = "WORKER_LAUNCH_FAILED"
    WORKER_PROTOCOL_FAILED = "WORKER_PROTOCOL_FAILED"
    WORKER_TERMINATED = "WORKER_TERMINATED"
    OPERATION_FAILED = "OPERATION_FAILED"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
    STORAGE_FAILED = "STORAGE_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class DiagnosticVersionKind(StrEnum):
    """Approved version categories; values are represented by numeric components."""

    APPLICATION = "application"
    PYTHON_RUNTIME = "python_runtime"
    MANIFEST_SCHEMA = "manifest_schema"
    EXPORT_HISTORY_SCHEMA = "export_history_schema"
    WORKER_PROTOCOL = "worker_protocol"
    PIKEPDF = "pikepdf"
    QPDF = "qpdf"
    PYPDFIUM2 = "pypdfium2"
    PDFIUM = "pdfium"


class DiagnosticCountKind(StrEnum):
    """Approved content-free integer count categories."""

    CASES_SCANNED = "cases_scanned"
    CASES_RECOVERABLE = "cases_recoverable"
    CASES_RECOVERED = "cases_recovered"
    CASES_ISOLATED = "cases_isolated"
    CASES_UNAVAILABLE = "cases_unavailable"
    OPERATIONS_COMPLETED = "operations_completed"
    PAGES_PROCESSED = "pages_processed"
    FILES_REMOVED = "files_removed"
    DIRECTORIES_REMOVED = "directories_removed"
    CACHE_ENTRIES_REMOVED = "cache_entries_removed"
    WARNINGS = "warnings"


class DiagnosticResourceKind(StrEnum):
    """Approved numeric resource observations."""

    DURATION_MILLISECONDS = "duration_milliseconds"
    CPU_MILLISECONDS = "cpu_milliseconds"
    PEAK_MEMORY_BYTES = "peak_memory_bytes"
    TEMPORARY_STORAGE_BYTES = "temporary_storage_bytes"


def _reject_record() -> None:
    reject_diagnostic(DiagnosticErrorCode.RECORD_INVALID, DiagnosticErrorContext.RECORD)


def _valid_safe_integer(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_SAFE_DIAGNOSTIC_INTEGER


def _valid_uuid4(value: object) -> bool:
    return type(value) is UUID and value.version == 4


def _valid_utc_timestamp(value: object) -> bool:
    return type(value) is datetime and value.tzinfo is UTC and value.fold == 0


@dataclass(frozen=True, slots=True, repr=False)
class DiagnosticVersion:
    """One allowlisted engine or schema version with no raw version text."""

    kind: DiagnosticVersionKind
    components: tuple[int, ...]

    def __post_init__(self) -> None:
        self.validate()
        object.__setattr__(self, "components", tuple(self.components))

    def validate(self) -> None:
        """Revalidate the instance before it crosses another boundary."""

        if type(self.kind) is not DiagnosticVersionKind or type(self.components) is not tuple:
            _reject_record()
        if not 1 <= len(self.components) <= MAX_DIAGNOSTIC_VERSION_COMPONENTS:
            _reject_record()
        if any(
            type(component) is not int or not 0 <= component <= MAX_DIAGNOSTIC_VERSION_COMPONENT
            for component in self.components
        ):
            _reject_record()

    def __repr__(self) -> str:
        kind = self.kind.value if type(self.kind) is DiagnosticVersionKind else "invalid"
        component_count = len(self.components) if type(self.components) is tuple else 0
        return f"DiagnosticVersion(kind={kind!r}, component_count={component_count})"


@dataclass(frozen=True, slots=True, repr=False)
class DiagnosticCount:
    """One allowlisted nonnegative integer count."""

    kind: DiagnosticCountKind
    value: int

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Revalidate the instance before it crosses another boundary."""

        if type(self.kind) is not DiagnosticCountKind or not _valid_safe_integer(self.value):
            _reject_record()

    def __repr__(self) -> str:
        kind = self.kind.value if type(self.kind) is DiagnosticCountKind else "invalid"
        value = self.value if _valid_safe_integer(self.value) else 0
        return f"DiagnosticCount(kind={kind!r}, value={value})"


@dataclass(frozen=True, slots=True, repr=False)
class DiagnosticResource:
    """One allowlisted nonnegative integer resource observation."""

    kind: DiagnosticResourceKind
    value: int

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Revalidate the instance before it crosses another boundary."""

        if type(self.kind) is not DiagnosticResourceKind or not _valid_safe_integer(self.value):
            _reject_record()

    def __repr__(self) -> str:
        kind = self.kind.value if type(self.kind) is DiagnosticResourceKind else "invalid"
        value = self.value if _valid_safe_integer(self.value) else 0
        return f"DiagnosticResource(kind={kind!r}, value={value})"


_OPERATION_EVENTS: Final = frozenset(
    (DiagnosticEventCode.OPERATION_STARTED, DiagnosticEventCode.OPERATION_COMPLETED)
)
_CASE_EVENTS: Final = frozenset(
    (
        DiagnosticEventCode.CASE_RECOVERY_COMPLETED,
        DiagnosticEventCode.CLEANUP_COMPLETED,
        DiagnosticEventCode.WORKER_STARTED,
        DiagnosticEventCode.WORKER_STOPPED,
        DiagnosticEventCode.WORKER_FAILED,
        *_OPERATION_EVENTS,
    )
)
_TERMINAL_EVENTS: Final = frozenset(
    (
        DiagnosticEventCode.APPLICATION_STOPPED,
        DiagnosticEventCode.RECOVERY_SCAN_COMPLETED,
        DiagnosticEventCode.CASE_RECOVERY_COMPLETED,
        DiagnosticEventCode.CLEANUP_COMPLETED,
        DiagnosticEventCode.WORKER_STOPPED,
        DiagnosticEventCode.WORKER_FAILED,
        DiagnosticEventCode.OPERATION_COMPLETED,
    )
)
_FAILURE_OUTCOMES: Final = frozenset(
    (DiagnosticOutcome.PARTIAL, DiagnosticOutcome.FAILED, DiagnosticOutcome.TIMED_OUT)
)


@dataclass(frozen=True, slots=True, repr=False)
class DiagnosticRecord:
    """One bounded structured record containing only documented safe fields."""

    occurred_at: datetime
    event: DiagnosticEventCode
    case_id: UUID | None = None
    operation_id: UUID | None = None
    operation_kind: DiagnosticOperationKind | None = None
    outcome: DiagnosticOutcome | None = None
    failure_code: DiagnosticFailureCode | None = None
    versions: tuple[DiagnosticVersion, ...] = ()
    counts: tuple[DiagnosticCount, ...] = ()
    resources: tuple[DiagnosticResource, ...] = ()
    record_version: int = DIAGNOSTIC_RECORD_VERSION

    def __post_init__(self) -> None:
        self.validate()
        object.__setattr__(
            self,
            "versions",
            tuple(
                sorted(
                    (
                        DiagnosticVersion(item.kind, tuple(item.components))
                        for item in self.versions
                    ),
                    key=lambda item: item.kind.value,
                )
            ),
        )
        object.__setattr__(
            self,
            "counts",
            tuple(
                sorted(
                    (DiagnosticCount(item.kind, item.value) for item in self.counts),
                    key=lambda item: item.kind.value,
                )
            ),
        )
        object.__setattr__(
            self,
            "resources",
            tuple(
                sorted(
                    (DiagnosticResource(item.kind, item.value) for item in self.resources),
                    key=lambda item: item.kind.value,
                )
            ),
        )

    def validate(self) -> None:
        """Revalidate the full record, including event-specific field invariants."""

        if type(self.record_version) is not int or self.record_version != DIAGNOSTIC_RECORD_VERSION:
            _reject_record()
        if not _valid_utc_timestamp(self.occurred_at):
            _reject_record()
        if type(self.event) is not DiagnosticEventCode:
            _reject_record()
        if self.case_id is not None and not _valid_uuid4(self.case_id):
            _reject_record()
        if self.operation_id is not None and not _valid_uuid4(self.operation_id):
            _reject_record()
        if (
            self.operation_kind is not None
            and type(self.operation_kind) is not DiagnosticOperationKind
        ):
            _reject_record()
        if self.outcome is not None and type(self.outcome) is not DiagnosticOutcome:
            _reject_record()
        if self.failure_code is not None and type(self.failure_code) is not DiagnosticFailureCode:
            _reject_record()
        self._validate_entries()
        self._validate_event_fields()

    def _validate_entries(self) -> None:
        if (
            type(self.versions) is not tuple
            or len(self.versions) > MAX_DIAGNOSTIC_VERSIONS
            or any(type(item) is not DiagnosticVersion for item in self.versions)
        ):
            _reject_record()
        if (
            type(self.counts) is not tuple
            or len(self.counts) > MAX_DIAGNOSTIC_COUNTS
            or any(type(item) is not DiagnosticCount for item in self.counts)
        ):
            _reject_record()
        if (
            type(self.resources) is not tuple
            or len(self.resources) > MAX_DIAGNOSTIC_RESOURCES
            or any(type(item) is not DiagnosticResource for item in self.resources)
        ):
            _reject_record()
        for version in self.versions:
            version.validate()
        for count in self.counts:
            count.validate()
        for resource in self.resources:
            resource.validate()
        if len({item.kind for item in self.versions}) != len(self.versions):
            _reject_record()
        if len({item.kind for item in self.counts}) != len(self.counts):
            _reject_record()
        if len({item.kind for item in self.resources}) != len(self.resources):
            _reject_record()

    def _validate_event_fields(self) -> None:
        if (self.event in _CASE_EVENTS) != (self.case_id is not None):
            _reject_record()
        is_operation_event = self.event in _OPERATION_EVENTS
        if is_operation_event != (self.operation_id is not None):
            _reject_record()
        if is_operation_event != (self.operation_kind is not None):
            _reject_record()
        is_terminal = self.event in _TERMINAL_EVENTS
        if is_terminal != (self.outcome is not None):
            _reject_record()
        has_failure_outcome = self.outcome in _FAILURE_OUTCOMES
        if has_failure_outcome != (self.failure_code is not None):
            _reject_record()
        if self.event is DiagnosticEventCode.WORKER_FAILED and self.outcome not in (
            DiagnosticOutcome.FAILED,
            DiagnosticOutcome.TIMED_OUT,
        ):
            _reject_record()

    def __repr__(self) -> str:
        event = self.event.value if type(self.event) is DiagnosticEventCode else "invalid"
        version_count = len(self.versions) if type(self.versions) is tuple else 0
        count_count = len(self.counts) if type(self.counts) is tuple else 0
        resource_count = len(self.resources) if type(self.resources) is tuple else 0
        return (
            f"DiagnosticRecord(event={event!r}, has_case_id={self.case_id is not None}, "
            f"has_operation_id={self.operation_id is not None}, versions={version_count}, "
            f"counts={count_count}, resources={resource_count})"
        )


def copy_diagnostic_record(value: DiagnosticRecord) -> DiagnosticRecord:
    """Return an independently validated copy safe from later caller mutation."""

    if type(value) is not DiagnosticRecord:
        _reject_record()
    value.validate()
    return DiagnosticRecord(
        occurred_at=value.occurred_at,
        event=value.event,
        case_id=value.case_id,
        operation_id=value.operation_id,
        operation_kind=value.operation_kind,
        outcome=value.outcome,
        failure_code=value.failure_code,
        versions=tuple(
            DiagnosticVersion(item.kind, tuple(item.components)) for item in value.versions
        ),
        counts=tuple(DiagnosticCount(item.kind, item.value) for item in value.counts),
        resources=tuple(DiagnosticResource(item.kind, item.value) for item in value.resources),
        record_version=value.record_version,
    )

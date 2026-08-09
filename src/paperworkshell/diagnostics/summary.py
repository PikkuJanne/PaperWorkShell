"""Immutable, human-readable summaries of allowlisted diagnostic fields."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from paperworkshell.diagnostics.errors import (
    DiagnosticErrorCode,
    DiagnosticErrorContext,
    reject_diagnostic,
)
from paperworkshell.diagnostics.models import (
    MAX_DIAGNOSTIC_COUNTS,
    MAX_DIAGNOSTIC_RECORDS,
    MAX_DIAGNOSTIC_RESOURCES,
    MAX_DIAGNOSTIC_VERSIONS,
    MAX_SAFE_DIAGNOSTIC_INTEGER,
    DiagnosticCount,
    DiagnosticCountKind,
    DiagnosticFailureCode,
    DiagnosticRecord,
    DiagnosticResource,
    DiagnosticResourceKind,
    DiagnosticVersion,
    DiagnosticVersionKind,
    copy_diagnostic_record,
)

DIAGNOSTIC_SUMMARY_VERSION: Final = 1
MAX_DIAGNOSTIC_SUMMARY_OPERATION_IDS: Final = MAX_DIAGNOSTIC_RECORDS
MAX_DIAGNOSTIC_SUMMARY_CODES: Final = 32

_VERSION_LABELS: Final = {
    DiagnosticVersionKind.APPLICATION: "Application",
    DiagnosticVersionKind.PYTHON_RUNTIME: "Python runtime",
    DiagnosticVersionKind.MANIFEST_SCHEMA: "Manifest schema",
    DiagnosticVersionKind.EXPORT_HISTORY_SCHEMA: "Export history schema",
    DiagnosticVersionKind.WORKER_PROTOCOL: "Worker protocol",
    DiagnosticVersionKind.PIKEPDF: "pikepdf",
    DiagnosticVersionKind.QPDF: "qpdf",
    DiagnosticVersionKind.PYPDFIUM2: "pypdfium2",
    DiagnosticVersionKind.PDFIUM: "PDFium",
}
_COUNT_LABELS: Final = {
    DiagnosticCountKind.CASES_SCANNED: "Cases scanned",
    DiagnosticCountKind.CASES_RECOVERABLE: "Cases recoverable",
    DiagnosticCountKind.CASES_RECOVERED: "Cases recovered",
    DiagnosticCountKind.CASES_ISOLATED: "Cases isolated",
    DiagnosticCountKind.CASES_UNAVAILABLE: "Cases unavailable",
    DiagnosticCountKind.OPERATIONS_COMPLETED: "Operations completed",
    DiagnosticCountKind.PAGES_PROCESSED: "Pages processed",
    DiagnosticCountKind.FILES_REMOVED: "Files removed",
    DiagnosticCountKind.DIRECTORIES_REMOVED: "Directories removed",
    DiagnosticCountKind.CACHE_ENTRIES_REMOVED: "Cache entries removed",
    DiagnosticCountKind.WARNINGS: "Warnings",
}
_RESOURCE_LABELS: Final = {
    DiagnosticResourceKind.DURATION_MILLISECONDS: "Duration milliseconds",
    DiagnosticResourceKind.CPU_MILLISECONDS: "CPU milliseconds",
    DiagnosticResourceKind.PEAK_MEMORY_BYTES: "Peak memory bytes",
    DiagnosticResourceKind.TEMPORARY_STORAGE_BYTES: "Temporary storage bytes",
    DiagnosticResourceKind.STORAGE_BYTES_RELEASED: "Storage bytes released",
}


def _reject_summary() -> None:
    reject_diagnostic(DiagnosticErrorCode.SUMMARY_INVALID, DiagnosticErrorContext.SUMMARY)


def _valid_safe_integer(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_SAFE_DIAGNOSTIC_INTEGER


def _valid_uuid4(value: object) -> bool:
    return type(value) is UUID and value.version == 4


def _valid_utc_timestamp(value: object) -> bool:
    return type(value) is datetime and value.tzinfo is UTC and value.fold == 0


def _timestamp_text(value: datetime) -> str:
    return (
        f"{value.year:04d}-{value.month:02d}-{value.day:02d}T"
        f"{value.hour:02d}:{value.minute:02d}:{value.second:02d}."
        f"{value.microsecond:06d}Z"
    )


@dataclass(frozen=True, slots=True, repr=False)
class DiagnosticSummary:
    """A deterministic snapshot containing only the documented support fields."""

    generated_at: datetime
    record_count: int = 0
    dropped_record_count: int = 0
    sink_failure_count: int = 0
    versions: tuple[DiagnosticVersion, ...] = ()
    operation_ids: tuple[UUID, ...] = ()
    counts: tuple[DiagnosticCount, ...] = ()
    codes: tuple[DiagnosticFailureCode, ...] = ()
    resources: tuple[DiagnosticResource, ...] = ()
    summary_version: int = DIAGNOSTIC_SUMMARY_VERSION

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
            "operation_ids",
            tuple(sorted(self.operation_ids, key=lambda item: item.hex)),
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
        object.__setattr__(self, "codes", tuple(sorted(self.codes, key=lambda item: item.value)))
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
        """Revalidate the snapshot before rendering or future export collection."""

        if (
            type(self.summary_version) is not int
            or self.summary_version != DIAGNOSTIC_SUMMARY_VERSION
            or not _valid_utc_timestamp(self.generated_at)
            or not _valid_safe_integer(self.record_count)
            or not _valid_safe_integer(self.dropped_record_count)
            or not _valid_safe_integer(self.sink_failure_count)
        ):
            _reject_summary()
        self._validate_versions()
        self._validate_operation_ids()
        self._validate_counts()
        self._validate_codes()
        self._validate_resources()

    def _validate_versions(self) -> None:
        if (
            type(self.versions) is not tuple
            or len(self.versions) > MAX_DIAGNOSTIC_VERSIONS
            or any(type(item) is not DiagnosticVersion for item in self.versions)
        ):
            _reject_summary()
        for item in self.versions:
            item.validate()
        if len({item.kind for item in self.versions}) != len(self.versions):
            _reject_summary()

    def _validate_operation_ids(self) -> None:
        if (
            type(self.operation_ids) is not tuple
            or len(self.operation_ids) > MAX_DIAGNOSTIC_SUMMARY_OPERATION_IDS
            or any(not _valid_uuid4(item) for item in self.operation_ids)
            or len(set(self.operation_ids)) != len(self.operation_ids)
        ):
            _reject_summary()

    def _validate_counts(self) -> None:
        if (
            type(self.counts) is not tuple
            or len(self.counts) > MAX_DIAGNOSTIC_COUNTS
            or any(type(item) is not DiagnosticCount for item in self.counts)
        ):
            _reject_summary()
        for item in self.counts:
            item.validate()
        if len({item.kind for item in self.counts}) != len(self.counts):
            _reject_summary()

    def _validate_codes(self) -> None:
        if (
            type(self.codes) is not tuple
            or len(self.codes) > MAX_DIAGNOSTIC_SUMMARY_CODES
            or any(type(item) is not DiagnosticFailureCode for item in self.codes)
            or len(set(self.codes)) != len(self.codes)
        ):
            _reject_summary()

    def _validate_resources(self) -> None:
        if (
            type(self.resources) is not tuple
            or len(self.resources) > MAX_DIAGNOSTIC_RESOURCES
            or any(type(item) is not DiagnosticResource for item in self.resources)
        ):
            _reject_summary()
        for item in self.resources:
            item.validate()
        if len({item.kind for item in self.resources}) != len(self.resources):
            _reject_summary()

    def __repr__(self) -> str:
        operation_count = len(self.operation_ids) if type(self.operation_ids) is tuple else 0
        version_count = len(self.versions) if type(self.versions) is tuple else 0
        count_count = len(self.counts) if type(self.counts) is tuple else 0
        code_count = len(self.codes) if type(self.codes) is tuple else 0
        resource_count = len(self.resources) if type(self.resources) is tuple else 0
        record_count = self.record_count if _valid_safe_integer(self.record_count) else 0
        return (
            f"DiagnosticSummary(record_count={record_count}, versions={version_count}, "
            f"operation_ids={operation_count}, counts={count_count}, codes={code_count}, "
            f"resources={resource_count})"
        )


def copy_diagnostic_summary(value: DiagnosticSummary) -> DiagnosticSummary:
    """Return an independently validated snapshot safe from caller mutation."""

    if type(value) is not DiagnosticSummary:
        _reject_summary()
    value.validate()
    return DiagnosticSummary(
        generated_at=value.generated_at,
        record_count=value.record_count,
        dropped_record_count=value.dropped_record_count,
        sink_failure_count=value.sink_failure_count,
        versions=tuple(
            DiagnosticVersion(item.kind, tuple(item.components)) for item in value.versions
        ),
        operation_ids=tuple(value.operation_ids),
        counts=tuple(DiagnosticCount(item.kind, item.value) for item in value.counts),
        codes=tuple(value.codes),
        resources=tuple(DiagnosticResource(item.kind, item.value) for item in value.resources),
        summary_version=value.summary_version,
    )


def build_diagnostic_summary(
    records: tuple[DiagnosticRecord, ...],
    *,
    generated_at: datetime,
    dropped_record_count: int = 0,
    sink_failure_count: int = 0,
) -> DiagnosticSummary:
    """Aggregate one frozen typed-record snapshot into documented safe summary fields."""

    if (
        type(records) is not tuple
        or len(records) > MAX_DIAGNOSTIC_RECORDS
        or any(type(record) is not DiagnosticRecord for record in records)
    ):
        _reject_summary()
    copied = tuple(copy_diagnostic_record(record) for record in records)
    versions: dict[DiagnosticVersionKind, tuple[int, ...]] = {}
    operation_ids: set[UUID] = set()
    counts: dict[DiagnosticCountKind, int] = {}
    codes: set[DiagnosticFailureCode] = set()
    resources: dict[DiagnosticResourceKind, int] = {}
    for record in copied:
        for version in record.versions:
            previous = versions.setdefault(version.kind, version.components)
            if previous != version.components:
                _reject_summary()
        if record.operation_id is not None:
            operation_ids.add(record.operation_id)
        if record.failure_code is not None:
            codes.add(record.failure_code)
        for count in record.counts:
            counts[count.kind] = counts.get(count.kind, 0) + count.value
        for resource in record.resources:
            if resource.kind is DiagnosticResourceKind.PEAK_MEMORY_BYTES:
                resources[resource.kind] = max(resources.get(resource.kind, 0), resource.value)
            else:
                resources[resource.kind] = resources.get(resource.kind, 0) + resource.value
    if any(not _valid_safe_integer(value) for value in (*counts.values(), *resources.values())):
        _reject_summary()
    return DiagnosticSummary(
        generated_at=generated_at,
        record_count=len(copied),
        dropped_record_count=dropped_record_count,
        sink_failure_count=sink_failure_count,
        versions=tuple(DiagnosticVersion(kind, value) for kind, value in versions.items()),
        operation_ids=tuple(operation_ids),
        counts=tuple(DiagnosticCount(kind, value) for kind, value in counts.items()),
        codes=tuple(codes),
        resources=tuple(DiagnosticResource(kind, value) for kind, value in resources.items()),
    )


def render_diagnostic_summary(value: DiagnosticSummary) -> str:
    """Render deterministic human-readable safe fields without recollecting state."""

    summary = copy_diagnostic_summary(value)
    lines = [
        "PaperWorkShell diagnostic summary",
        f"Summary version: {summary.summary_version}",
        f"Generated UTC: {_timestamp_text(summary.generated_at)}",
        f"Record count: {summary.record_count}",
        f"Dropped record count: {summary.dropped_record_count}",
        f"Sink failure count: {summary.sink_failure_count}",
        "Versions:",
    ]
    lines.extend(
        f"- {_VERSION_LABELS[item.kind]}: {'.'.join(str(part) for part in item.components)}"
        for item in summary.versions
    )
    if not summary.versions:
        lines.append("- None")
    lines.append("Operation IDs:")
    lines.extend(f"- {operation_id}" for operation_id in summary.operation_ids)
    if not summary.operation_ids:
        lines.append("- None")
    lines.append("Counts:")
    lines.extend(f"- {_COUNT_LABELS[item.kind]}: {item.value}" for item in summary.counts)
    if not summary.counts:
        lines.append("- None")
    lines.append("Codes:")
    lines.extend(f"- {code.value}" for code in summary.codes)
    if not summary.codes:
        lines.append("- None")
    lines.append("Resources:")
    lines.extend(f"- {_RESOURCE_LABELS[item.kind]}: {item.value}" for item in summary.resources)
    if not summary.resources:
        lines.append("- None")
    return "\n".join(lines) + "\n"

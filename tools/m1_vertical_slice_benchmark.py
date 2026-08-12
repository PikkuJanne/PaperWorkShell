"""Build and validate sanitized M1 constrained-resource evidence.

The report measures bounded software behaviour.  It deliberately contains no
hardware observations or operator attestations and cannot certify a named
processor, memory, graphics, or storage configuration.  Completed live-run
evidence remains available only through the canonical runner's private writer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Literal, NoReturn, Protocol, Self, cast
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from paperworkshell.domain.resource_policy import (
    ProcessingProfile,
    ResourcePolicy,
    select_resource_policy,
)
from paperworkshell.storage._windows import (
    FileIdentity,
    WindowsHandle,
    open_handle,
    rename_open_file_no_replace_at,
    require_expected_handle,
    write_new_file,
)
from paperworkshell.storage.errors import WorkspaceError

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
HISTORICAL_REPORT_SCHEMA_PATH: Final = Path(
    "schemas/m1-vertical-slice-benchmark-report-v1.schema.json"
)
HISTORICAL_V2_REPORT_SCHEMA_PATH: Final = Path(
    "schemas/m1-vertical-slice-benchmark-report-v2.schema.json"
)
REPORT_SCHEMA_PATH: Final = Path("schemas/m1-vertical-slice-benchmark-report-v3.schema.json")
MAX_REPORT_BYTES: Final = 256 * 1024
MAX_SCHEMA_BYTES: Final = 256 * 1024
MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991
NOT_RUN_CODE: Final = "M1_VERTICAL_SLICE_NOT_RUN"
LIVE_RUNNER_UNAVAILABLE_CODE: Final = "M1_LIVE_RUNNER_UNAVAILABLE"
LIVE_REPORT_FILENAME: Final = "PWS-0114-m1-benchmark-report.json"
LIVE_REPORT_OUTPUT_INVALID_CODE: Final = "M1_LIVE_REPORT_OUTPUT_INVALID"
LIVE_REPORT_ALREADY_EXISTS_CODE: Final = "M1_LIVE_REPORT_ALREADY_EXISTS"
LIVE_REPORT_PUBLISH_FAILED_CODE: Final = "M1_LIVE_REPORT_PUBLISH_FAILED"
LIVE_REPORT_WRITER_CLOSED_CODE: Final = "M1_LIVE_REPORT_WRITER_CLOSED"
EXPECTED_VERIFIER_CODES: Final = (
    "PDF_REOPENED",
    "STRUCTURE_CLEAN",
    "PAGE_COUNT_MATCHED",
    "UNENCRYPTED",
    "ALL_PAGES_RENDERED",
    "ORDER_ROTATION_MATCHED",
    "OUTPUT_COMMIT_REOPENED",
)
_SHA256: Final = re.compile(r"[0-9a-f]{64}\Z")
_GIT_OBJECT_ID: Final = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_VERSION_IDENTITY: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}\Z")
_LIVE_STAGE_PREFIX: Final = ".pws-0114-m1-report-"
_LIVE_STAGE_SUFFIX: Final = ".tmp"
_WINDOWS_ALREADY_EXISTS_ERRORS: Final = frozenset({80, 183})


class M1Phase(StrEnum):
    """Measured phases in the complete M1 vertical slice."""

    CASE_CREATE = "case-create"
    PDF_IMPORT = "pdf-import"
    THUMBNAIL_RENDER = "thumbnail-render"
    ORGANIZE = "organize"
    VERIFIED_EXPORT = "verified-export"
    CASE_REOPEN_RECOVERY = "case-reopen-recovery"


class PhaseOutcome(StrEnum):
    """Whether one measured phase returned normally."""

    COMPLETED = "completed"
    FAILED = "failed"


class VerticalSliceRunStatus(StrEnum):
    """Truthful extent of the vertical-slice measurement."""

    NOT_RUN = "not-run"
    COMPLETED = "completed"
    FAILED = "failed"


class FailureStage(StrEnum):
    """Closed stages at which the canonical runner may publish failure evidence."""

    SETUP = "setup"
    WORKLOAD = "workload"
    BUDGET_EVALUATION = "budget-evaluation"


class WorkerOperation(StrEnum):
    """Worker boundaries whose retained Job snapshots are measured."""

    SOURCE_INSPECTION = "source-inspection"
    THUMBNAIL_RENDER = "thumbnail-render"
    VERIFIED_EXPORT = "verified-export"


class WorkspaceCheckpoint(StrEnum):
    """Bounded inventory points in a complete vertical slice."""

    CREATED = "created"
    IMPORTED = "imported"
    ORGANIZED = "organized"
    EXPORTED = "exported"
    RECOVERED = "recovered"


EXPECTED_PHASES: Final = tuple(M1Phase)
EXPECTED_WORKER_OPERATIONS: Final = tuple(WorkerOperation)
EXPECTED_WORKSPACE_CHECKPOINTS: Final = tuple(WorkspaceCheckpoint)
WORKLOAD_ID: Final = "m1-vertical-slice"
WORKLOAD_VERSION: Final = 1
HARDWARE_CLAIM_LIMITATION: Final = (
    "This resource report does not certify a physical low-end hardware profile; physical "
    "low-end hardware was not tested."
)
MEASUREMENT_LIMITATION: Final = (
    "Peak process-tree RSS is sampled every 50 ms, so shorter memory transients may not be "
    "observed. Case-workspace cache and temporary-storage peaks combine strict quiescent "
    "inventories with event-driven reservation high-water bounds and may conservatively exceed "
    "simultaneously materialized bytes. Worker-private temporary-storage peaks are not observed, "
    "while each worker operation enforces its configured temporary-storage limit."
)
WORKSPACE_PEAK_MEASUREMENT_METHOD: Final = "quiescent-inventory-plus-reservation-high-water-v1"


class M1BenchmarkError(Exception):
    """A sanitized expected measurement or codec failure."""

    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class DuplicateJsonKeyError(ValueError):
    """Raised when strict JSON loading sees a duplicate member name."""


class NonStandardJsonConstantError(ValueError):
    """Raised when strict JSON loading sees NaN or Infinity."""


class _LiveM1ReportAuthority:
    """Unsupported module-private marker retained by the canonical live runner."""

    __slots__ = ("_marker",)

    def __init__(self, marker: object) -> None:
        self._marker = marker


_LIVE_M1_REPORT_AUTHORITY_MARKER: Final = object()


@dataclass(frozen=True, order=True, slots=True)
class BenchmarkIssue:
    """One stable, path-only schema or report validation issue."""

    code: str
    path: str
    safe_message: str

    def render(self) -> str:
        return f"ERROR [{self.code}] {self.path}: {self.safe_message}"


class WorkerResourceSnapshot(Protocol):
    """Structural subset exposed by the retained Windows worker Job."""

    @property
    def process_memory_limit_bytes(self) -> int: ...

    @property
    def job_memory_limit_bytes(self) -> int: ...

    @property
    def peak_process_memory_used_bytes(self) -> int: ...

    @property
    def peak_job_memory_used_bytes(self) -> int: ...


class WorkspaceUsageSnapshot(Protocol):
    """Structural subset exposed by a bounded workspace inventory."""

    @property
    def case_bytes(self) -> int: ...

    @property
    def cache_bytes(self) -> int: ...

    @property
    def temp_bytes(self) -> int: ...

    @property
    def entry_count(self) -> int: ...

    @property
    def maximum_depth_seen(self) -> int: ...


def _reject(code: str, safe_message: str) -> NoReturn:
    error = M1BenchmarkError(code, safe_message)
    try:
        raise error from None
    finally:
        error.__context__ = None
        error.__cause__ = None


def _require_live_m1_report_authority(authority: object) -> None:
    if (
        type(authority) is not _LiveM1ReportAuthority
        or authority._marker is not _LIVE_M1_REPORT_AUTHORITY_MARKER
    ):
        _reject(
            LIVE_RUNNER_UNAVAILABLE_CODE,
            "Completed workload reports require the trusted live runner.",
        )


def _exact_integer(value: object, field: str, *, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= MAX_SAFE_INTEGER:
        _reject(
            "M1_MEASUREMENT_INVALID",
            f"{field} must be an integer from {minimum} through {MAX_SAFE_INTEGER}.",
        )
    return value


def _exact_clock_reading(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        _reject("M1_CLOCK_INVALID", f"{field} must be a non-negative integer.")
    return value


def _exact_digest(value: object, field: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        _reject("M1_MEASUREMENT_INVALID", f"{field} must be a lowercase SHA-256 digest.")
    return value


def _exact_git_object_id(value: object, field: str) -> str:
    if type(value) is not str or _GIT_OBJECT_ID.fullmatch(value) is None:
        _reject("M1_MEASUREMENT_INVALID", f"{field} must be a lowercase Git object identity.")
    return value


def _exact_version_identity(value: object, field: str) -> str:
    if type(value) is not str or _VERSION_IDENTITY.fullmatch(value) is None:
        _reject("M1_MEASUREMENT_INVALID", f"{field} uses an invalid version identity.")
    return value


def _stable_result_code(value: object) -> str:
    if type(value) is not str:
        _reject("M1_MEASUREMENT_INVALID", "The result code must use the stable M1 code format.")
    text = value
    if (
        not text.startswith("M1_")
        or not 4 <= len(text) <= 99
        or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for character in text)
    ):
        _reject("M1_MEASUREMENT_INVALID", "The result code must use the stable M1 code format.")
    return text


@dataclass(frozen=True, slots=True)
class PhaseMeasurement:
    """One perf-counter wall-time measurement without a product threshold."""

    phase: M1Phase
    outcome: PhaseOutcome
    elapsed_ns: int

    def __post_init__(self) -> None:
        if type(self.phase) is not M1Phase or type(self.outcome) is not PhaseOutcome:
            _reject("M1_MEASUREMENT_INVALID", "A phase measurement uses an unknown enum value.")
        _exact_integer(self.elapsed_ns, "elapsed_ns", minimum=1)


@dataclass(frozen=True, slots=True)
class WorkerResourceMeasurement:
    """Path-free numeric fields copied from one authoritative Job snapshot."""

    operation: WorkerOperation
    process_memory_limit_bytes: int
    job_memory_limit_bytes: int
    peak_process_memory_used_bytes: int
    peak_job_memory_used_bytes: int

    def __post_init__(self) -> None:
        if type(self.operation) is not WorkerOperation:
            _reject("M1_MEASUREMENT_INVALID", "A worker measurement uses an unknown operation.")
        process_limit = _exact_integer(
            self.process_memory_limit_bytes,
            "process_memory_limit_bytes",
            minimum=1,
        )
        job_limit = _exact_integer(
            self.job_memory_limit_bytes,
            "job_memory_limit_bytes",
            minimum=1,
        )
        process_peak = _exact_integer(
            self.peak_process_memory_used_bytes,
            "peak_process_memory_used_bytes",
            minimum=1,
        )
        job_peak = _exact_integer(
            self.peak_job_memory_used_bytes,
            "peak_job_memory_used_bytes",
            minimum=1,
        )
        if (
            process_limit > job_limit
            or process_peak > process_limit
            or job_peak > job_limit
            or job_peak < process_peak
        ):
            _reject(
                "M1_MEASUREMENT_INVALID",
                "Worker peak measurements must remain within their retained Job limits.",
            )

    @classmethod
    def from_snapshot(
        cls,
        operation: WorkerOperation,
        snapshot: WorkerResourceSnapshot,
    ) -> WorkerResourceMeasurement:
        """Copy only the four approved numeric fields from a live snapshot."""

        return cls(
            operation=operation,
            process_memory_limit_bytes=snapshot.process_memory_limit_bytes,
            job_memory_limit_bytes=snapshot.job_memory_limit_bytes,
            peak_process_memory_used_bytes=snapshot.peak_process_memory_used_bytes,
            peak_job_memory_used_bytes=snapshot.peak_job_memory_used_bytes,
        )


@dataclass(frozen=True, slots=True)
class WorkspaceUsageMeasurement:
    """One bounded, no-follow workspace inventory without path information."""

    checkpoint: WorkspaceCheckpoint
    case_bytes: int
    cache_bytes: int
    temp_bytes: int
    entry_count: int
    maximum_depth_seen: int

    def __post_init__(self) -> None:
        if type(self.checkpoint) is not WorkspaceCheckpoint:
            _reject("M1_MEASUREMENT_INVALID", "A workspace measurement uses an unknown checkpoint.")
        case_bytes = _exact_integer(self.case_bytes, "case_bytes")
        cache_bytes = _exact_integer(self.cache_bytes, "cache_bytes")
        temp_bytes = _exact_integer(self.temp_bytes, "temp_bytes")
        _exact_integer(self.entry_count, "entry_count")
        _exact_integer(self.maximum_depth_seen, "maximum_depth_seen")
        if cache_bytes > case_bytes or temp_bytes > case_bytes - cache_bytes:
            _reject(
                "M1_MEASUREMENT_INVALID",
                "Workspace area measurements cannot exceed total case usage.",
            )

    @classmethod
    def from_usage(
        cls,
        checkpoint: WorkspaceCheckpoint,
        usage: WorkspaceUsageSnapshot,
    ) -> WorkspaceUsageMeasurement:
        """Copy only approved logical-usage fields from a workspace inventory."""

        return cls(
            checkpoint=checkpoint,
            case_bytes=usage.case_bytes,
            cache_bytes=usage.cache_bytes,
            temp_bytes=usage.temp_bytes,
            entry_count=usage.entry_count,
            maximum_depth_seen=usage.maximum_depth_seen,
        )


class BudgetId(StrEnum):
    """Hard gates evaluated for the canonical constrained workload."""

    PROCESS_TREE_RSS = "process-tree-rss"
    CACHE_BYTES = "cache-bytes"
    TEMP_BYTES = "temporary-storage-bytes"
    HEAVY_CONCURRENCY = "heavy-operation-concurrency"
    UI_HEARTBEAT = "ui-heartbeat-gap"
    CANCELLATION_ACKNOWLEDGEMENT = "cancellation-acknowledgement"


@dataclass(frozen=True, slots=True)
class BudgetResult:
    """One sanitized observed/limit comparison."""

    budget_id: BudgetId
    observed: int
    limit: int
    passed: bool

    def __post_init__(self) -> None:
        if type(self.budget_id) is not BudgetId or type(self.passed) is not bool:
            _reject("M1_MEASUREMENT_INVALID", "A resource-budget result is invalid.")
        observed = _exact_integer(self.observed, "budget observed value")
        limit = _exact_integer(self.limit, "budget limit", minimum=1)
        if self.passed is not (observed <= limit):
            _reject(
                "M1_MEASUREMENT_INVALID",
                "A resource-budget result does not match its observed value and limit.",
            )

    @classmethod
    def from_payload(cls, value: Mapping[str, object]) -> BudgetResult:
        if set(value) != {"budget_id", "observed", "limit", "passed"}:
            _reject("M1_MEASUREMENT_INVALID", "A resource-budget result is invalid.")
        try:
            budget_id = BudgetId(cast(str, value["budget_id"]))
        except TypeError, ValueError:
            _reject("M1_MEASUREMENT_INVALID", "A resource-budget result is invalid.")
        return cls(
            budget_id,
            cast(int, value["observed"]),
            cast(int, value["limit"]),
            cast(bool, value["passed"]),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "budget_id": self.budget_id.value,
            "observed": self.observed,
            "limit": self.limit,
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class CompletedRunEvidence:
    """Closed identity, correctness, and hard resource-budget evidence."""

    run_id: UUID
    sample_count: int
    total_elapsed_ns: int
    repository_revision: str
    repository_tree: str
    repository_worktree_clean: bool
    report_schema_sha256: str
    fixture_set_sha256: str
    source_set_sha256: str
    python_version: str
    pikepdf_version: str
    qpdf_version: str
    pdfium_version: str
    qt_version: str
    worker_config_sha256: str
    renderer_profile_sha256: str
    manifest_schema_version: int
    worker_protocol_version: int
    export_profile_version: int
    workload_version: int
    source_count: int
    source_page_count: int
    active_page_count: int
    preview_count: int
    output_count: int
    output_page_count: int
    output_bytes: int
    output_sha256: str
    semantic_fingerprint: str
    deterministic_semantic_equal: bool
    deterministic_output_equal: bool
    verifier_codes: tuple[str, ...]
    recovery_status: str
    case_reopened: bool
    reopened_page_count: int
    reopened_sequence_equal: bool
    privacy_scan_status: str
    privacy_canary_count: int
    background_export_completed: bool
    workers_terminated: bool
    worker_recycle_policy: str
    low_disk_simulation_status: str
    slow_io_simulation_status: str
    worker_recovery_status: str
    heartbeat_count: int
    maximum_heartbeat_gap_ns: int
    peak_process_tree_rss_bytes: int
    workspace_peak_measurement_method: str
    peak_cache_bytes: int
    peak_temp_bytes: int
    max_heavy_operation_concurrency: int
    cancellation_acknowledgement_ms: int
    ui_heartbeat_violation_count: int
    duration_ms: int
    input_bytes: int
    budget_results: tuple[BudgetResult, ...]

    def __post_init__(self) -> None:
        if type(self.run_id) is not UUID or self.run_id.version != 4:
            _reject("M1_MEASUREMENT_INVALID", "The run identity must be a UUIDv4 value.")
        sample_count = _exact_integer(self.sample_count, "sample_count", minimum=1)
        _exact_integer(self.total_elapsed_ns, "total_elapsed_ns", minimum=1)
        workload_version = _exact_integer(self.workload_version, "workload_version", minimum=1)
        output_count = _exact_integer(self.output_count, "output_count", minimum=1)
        if sample_count != 1 or workload_version != 1 or output_count != 2:
            _reject(
                "M1_MEASUREMENT_INVALID",
                "Completed M1 evidence must use its exact sample, workload, and output counts.",
            )
        _exact_git_object_id(self.repository_revision, "repository_revision")
        _exact_git_object_id(self.repository_tree, "repository_tree")
        for digest_value, field in (
            (self.report_schema_sha256, "report_schema_sha256"),
            (self.fixture_set_sha256, "fixture_set_sha256"),
            (self.source_set_sha256, "source_set_sha256"),
            (self.worker_config_sha256, "worker_config_sha256"),
            (self.renderer_profile_sha256, "renderer_profile_sha256"),
            (self.output_sha256, "output_sha256"),
            (self.semantic_fingerprint, "semantic_fingerprint"),
        ):
            _exact_digest(digest_value, field)
        for version_value, field in (
            (self.python_version, "python_version"),
            (self.pikepdf_version, "pikepdf_version"),
            (self.qpdf_version, "qpdf_version"),
            (self.pdfium_version, "pdfium_version"),
            (self.qt_version, "qt_version"),
        ):
            _exact_version_identity(version_value, field)
        for numeric_value, field, minimum in (
            (self.manifest_schema_version, "manifest_schema_version", 1),
            (self.worker_protocol_version, "worker_protocol_version", 1),
            (self.export_profile_version, "export_profile_version", 1),
            (self.source_count, "source_count", 2),
            (self.source_page_count, "source_page_count", 2),
            (self.active_page_count, "active_page_count", 1),
            (self.preview_count, "preview_count", 1),
            (self.output_page_count, "output_page_count", 1),
            (self.output_bytes, "output_bytes", 1),
            (self.reopened_page_count, "reopened_page_count", 1),
            (self.privacy_canary_count, "privacy_canary_count", 1),
            (self.heartbeat_count, "heartbeat_count", 1),
            (self.maximum_heartbeat_gap_ns, "maximum_heartbeat_gap_ns", 1),
            (self.peak_process_tree_rss_bytes, "peak_process_tree_rss_bytes", 1),
            (self.peak_cache_bytes, "peak_cache_bytes", 0),
            (self.peak_temp_bytes, "peak_temp_bytes", 0),
            (
                self.max_heavy_operation_concurrency,
                "max_heavy_operation_concurrency",
                1,
            ),
            (self.ui_heartbeat_violation_count, "ui_heartbeat_violation_count", 0),
            (self.duration_ms, "duration_ms", 1),
            (self.input_bytes, "input_bytes", 1),
        ):
            _exact_integer(numeric_value, field, minimum=minimum)
        _exact_integer(
            self.cancellation_acknowledgement_ms,
            "cancellation_acknowledgement_ms",
        )
        if type(self.budget_results) is not tuple or any(
            type(item) is not BudgetResult for item in self.budget_results
        ):
            _reject(
                "M1_RESOURCE_BUDGET_FAILED",
                "Completed evidence requires every ordered hard resource budget to pass.",
            )
        if tuple(item.budget_id for item in self.budget_results) != tuple(BudgetId) or any(
            not item.passed for item in self.budget_results
        ):
            _reject(
                "M1_RESOURCE_BUDGET_FAILED",
                "Completed evidence requires every ordered hard resource budget to pass.",
            )
        if (
            self.repository_worktree_clean is not True
            or self.deterministic_semantic_equal is not True
            or self.deterministic_output_equal is not True
            or self.case_reopened is not True
            or self.reopened_sequence_equal is not True
            or self.background_export_completed is not True
            or self.workers_terminated is not True
            or self.worker_recycle_policy != "one-shot"
            or self.low_disk_simulation_status != "passed"
            or self.slow_io_simulation_status != "passed"
            or self.worker_recovery_status != "passed"
            or self.workspace_peak_measurement_method != WORKSPACE_PEAK_MEASUREMENT_METHOD
            or self.verifier_codes != EXPECTED_VERIFIER_CODES
            or self.recovery_status not in {"current", "recovered"}
            or self.privacy_scan_status != "passed"
            or self.source_page_count < self.source_count
            or not 1 <= self.active_page_count <= self.source_page_count
            or not self.active_page_count <= self.preview_count <= self.source_page_count
            or self.output_page_count != self.active_page_count
            or self.reopened_page_count != self.output_page_count
        ):
            _reject(
                "M1_MEASUREMENT_INVALID",
                "Completed evidence does not bind one exact successful vertical-slice result.",
            )

    @classmethod
    def from_payload(cls, value: Mapping[str, object]) -> CompletedRunEvidence:
        """Decode the already schema-validated closed completed-evidence object."""

        try:
            run_id = UUID(cast(str, value["run_id"]))
        except ValueError, TypeError, AttributeError:
            _reject("M1_MEASUREMENT_INVALID", "The run identity must be a UUIDv4 value.")
        return cls(
            run_id=run_id,
            sample_count=cast(int, value["sample_count"]),
            total_elapsed_ns=cast(int, value["total_elapsed_ns"]),
            repository_revision=cast(str, value["repository_revision"]),
            repository_tree=cast(str, value["repository_tree"]),
            repository_worktree_clean=cast(bool, value["repository_worktree_clean"]),
            report_schema_sha256=cast(str, value["report_schema_sha256"]),
            fixture_set_sha256=cast(str, value["fixture_set_sha256"]),
            source_set_sha256=cast(str, value["source_set_sha256"]),
            python_version=cast(str, value["python_version"]),
            pikepdf_version=cast(str, value["pikepdf_version"]),
            qpdf_version=cast(str, value["qpdf_version"]),
            pdfium_version=cast(str, value["pdfium_version"]),
            qt_version=cast(str, value["qt_version"]),
            worker_config_sha256=cast(str, value["worker_config_sha256"]),
            renderer_profile_sha256=cast(str, value["renderer_profile_sha256"]),
            manifest_schema_version=cast(int, value["manifest_schema_version"]),
            worker_protocol_version=cast(int, value["worker_protocol_version"]),
            export_profile_version=cast(int, value["export_profile_version"]),
            workload_version=cast(int, value["workload_version"]),
            source_count=cast(int, value["source_count"]),
            source_page_count=cast(int, value["source_page_count"]),
            active_page_count=cast(int, value["active_page_count"]),
            preview_count=cast(int, value["preview_count"]),
            output_count=cast(int, value["output_count"]),
            output_page_count=cast(int, value["output_page_count"]),
            output_bytes=cast(int, value["output_bytes"]),
            output_sha256=cast(str, value["output_sha256"]),
            semantic_fingerprint=cast(str, value["semantic_fingerprint"]),
            deterministic_semantic_equal=cast(bool, value["deterministic_semantic_equal"]),
            deterministic_output_equal=cast(bool, value["deterministic_output_equal"]),
            verifier_codes=tuple(cast(list[str], value["verifier_codes"])),
            recovery_status=cast(str, value["recovery_status"]),
            case_reopened=cast(bool, value["case_reopened"]),
            reopened_page_count=cast(int, value["reopened_page_count"]),
            reopened_sequence_equal=cast(bool, value["reopened_sequence_equal"]),
            privacy_scan_status=cast(str, value["privacy_scan_status"]),
            privacy_canary_count=cast(int, value["privacy_canary_count"]),
            background_export_completed=cast(bool, value["background_export_completed"]),
            workers_terminated=cast(bool, value["workers_terminated"]),
            worker_recycle_policy=cast(str, value["worker_recycle_policy"]),
            low_disk_simulation_status=cast(str, value["low_disk_simulation_status"]),
            slow_io_simulation_status=cast(str, value["slow_io_simulation_status"]),
            worker_recovery_status=cast(str, value["worker_recovery_status"]),
            heartbeat_count=cast(int, value["heartbeat_count"]),
            maximum_heartbeat_gap_ns=cast(int, value["maximum_heartbeat_gap_ns"]),
            peak_process_tree_rss_bytes=cast(int, value["peak_process_tree_rss_bytes"]),
            workspace_peak_measurement_method=cast(str, value["workspace_peak_measurement_method"]),
            peak_cache_bytes=cast(int, value["peak_cache_bytes"]),
            peak_temp_bytes=cast(int, value["peak_temp_bytes"]),
            max_heavy_operation_concurrency=cast(int, value["max_heavy_operation_concurrency"]),
            cancellation_acknowledgement_ms=cast(int, value["cancellation_acknowledgement_ms"]),
            ui_heartbeat_violation_count=cast(int, value["ui_heartbeat_violation_count"]),
            duration_ms=cast(int, value["duration_ms"]),
            input_bytes=cast(int, value["input_bytes"]),
            budget_results=tuple(
                BudgetResult.from_payload(item)
                for item in cast(list[Mapping[str, object]], value["budget_results"])
            ),
        )

    def _to_payload(self) -> dict[str, object]:
        return {
            "run_id": str(self.run_id),
            "sample_count": self.sample_count,
            "total_elapsed_ns": self.total_elapsed_ns,
            "repository_revision": self.repository_revision,
            "repository_tree": self.repository_tree,
            "repository_worktree_clean": self.repository_worktree_clean,
            "report_schema_sha256": self.report_schema_sha256,
            "fixture_set_sha256": self.fixture_set_sha256,
            "source_set_sha256": self.source_set_sha256,
            "python_version": self.python_version,
            "pikepdf_version": self.pikepdf_version,
            "qpdf_version": self.qpdf_version,
            "pdfium_version": self.pdfium_version,
            "qt_version": self.qt_version,
            "worker_config_sha256": self.worker_config_sha256,
            "renderer_profile_sha256": self.renderer_profile_sha256,
            "manifest_schema_version": self.manifest_schema_version,
            "worker_protocol_version": self.worker_protocol_version,
            "export_profile_version": self.export_profile_version,
            "workload_version": self.workload_version,
            "source_count": self.source_count,
            "source_page_count": self.source_page_count,
            "active_page_count": self.active_page_count,
            "preview_count": self.preview_count,
            "output_count": self.output_count,
            "output_page_count": self.output_page_count,
            "output_bytes": self.output_bytes,
            "output_sha256": self.output_sha256,
            "semantic_fingerprint": self.semantic_fingerprint,
            "deterministic_semantic_equal": self.deterministic_semantic_equal,
            "deterministic_output_equal": self.deterministic_output_equal,
            "verifier_codes": list(self.verifier_codes),
            "recovery_status": self.recovery_status,
            "case_reopened": self.case_reopened,
            "reopened_page_count": self.reopened_page_count,
            "reopened_sequence_equal": self.reopened_sequence_equal,
            "privacy_scan_status": self.privacy_scan_status,
            "privacy_canary_count": self.privacy_canary_count,
            "background_export_completed": self.background_export_completed,
            "workers_terminated": self.workers_terminated,
            "worker_recycle_policy": self.worker_recycle_policy,
            "low_disk_simulation_status": self.low_disk_simulation_status,
            "slow_io_simulation_status": self.slow_io_simulation_status,
            "worker_recovery_status": self.worker_recovery_status,
            "heartbeat_count": self.heartbeat_count,
            "maximum_heartbeat_gap_ns": self.maximum_heartbeat_gap_ns,
            "peak_process_tree_rss_bytes": self.peak_process_tree_rss_bytes,
            "workspace_peak_measurement_method": self.workspace_peak_measurement_method,
            "peak_cache_bytes": self.peak_cache_bytes,
            "peak_temp_bytes": self.peak_temp_bytes,
            "max_heavy_operation_concurrency": self.max_heavy_operation_concurrency,
            "cancellation_acknowledgement_ms": self.cancellation_acknowledgement_ms,
            "ui_heartbeat_violation_count": self.ui_heartbeat_violation_count,
            "duration_ms": self.duration_ms,
            "input_bytes": self.input_bytes,
            "budget_results": [item.to_payload() for item in self.budget_results],
        }


@dataclass(frozen=True, slots=True)
class FailedRunEvidence:
    """Closed identity and available hard-budget facts for one failed live run."""

    run_id: UUID
    failure_stage: FailureStage
    total_elapsed_ns: int
    repository_revision: str
    repository_tree: str
    repository_worktree_clean: bool
    report_schema_sha256: str
    budget_results: tuple[BudgetResult, ...]

    def __post_init__(self) -> None:
        if type(self.run_id) is not UUID or self.run_id.version != 4:
            _reject("M1_MEASUREMENT_INVALID", "The run identity must be a UUIDv4 value.")
        if type(self.failure_stage) is not FailureStage:
            _reject("M1_MEASUREMENT_INVALID", "The failure stage is invalid.")
        _exact_integer(self.total_elapsed_ns, "total_elapsed_ns", minimum=1)
        _exact_git_object_id(self.repository_revision, "repository_revision")
        _exact_git_object_id(self.repository_tree, "repository_tree")
        _exact_digest(self.report_schema_sha256, "report_schema_sha256")
        if (
            self.repository_worktree_clean is not True
            or type(self.budget_results) is not tuple
            or any(type(item) is not BudgetResult for item in self.budget_results)
        ):
            _reject("M1_MEASUREMENT_INVALID", "Failed evidence identity is invalid.")
        budget_ids = tuple(item.budget_id for item in self.budget_results)
        if len(set(budget_ids)) != len(budget_ids) or not _ordered_subset(
            budget_ids, tuple(BudgetId)
        ):
            _reject("M1_MEASUREMENT_INVALID", "Failed resource-budget evidence is invalid.")
        if self.failure_stage is FailureStage.BUDGET_EVALUATION:
            if budget_ids != tuple(BudgetId) or all(item.passed for item in self.budget_results):
                _reject(
                    "M1_MEASUREMENT_INVALID",
                    "A budget-evaluation failure requires every ordered budget and a failure.",
                )
        elif self.budget_results:
            _reject(
                "M1_MEASUREMENT_INVALID",
                "Pre-budget failure evidence cannot invent resource-budget results.",
            )

    @classmethod
    def from_payload(cls, value: Mapping[str, object]) -> FailedRunEvidence:
        expected = {
            "run_id",
            "failure_stage",
            "total_elapsed_ns",
            "repository_revision",
            "repository_tree",
            "repository_worktree_clean",
            "report_schema_sha256",
            "budget_results",
        }
        if set(value) != expected:
            _reject("M1_MEASUREMENT_INVALID", "Failed evidence uses an invalid field set.")
        try:
            run_id = UUID(cast(str, value["run_id"]))
            failure_stage = FailureStage(cast(str, value["failure_stage"]))
        except ValueError, TypeError, AttributeError:
            _reject("M1_MEASUREMENT_INVALID", "Failed evidence identity is invalid.")
        return cls(
            run_id=run_id,
            failure_stage=failure_stage,
            total_elapsed_ns=cast(int, value["total_elapsed_ns"]),
            repository_revision=cast(str, value["repository_revision"]),
            repository_tree=cast(str, value["repository_tree"]),
            repository_worktree_clean=cast(bool, value["repository_worktree_clean"]),
            report_schema_sha256=cast(str, value["report_schema_sha256"]),
            budget_results=tuple(
                BudgetResult.from_payload(item)
                for item in cast(list[Mapping[str, object]], value["budget_results"])
            ),
        )

    def _to_payload(self) -> dict[str, object]:
        return {
            "run_id": str(self.run_id),
            "failure_stage": self.failure_stage.value,
            "total_elapsed_ns": self.total_elapsed_ns,
            "repository_revision": self.repository_revision,
            "repository_tree": self.repository_tree,
            "repository_worktree_clean": self.repository_worktree_clean,
            "report_schema_sha256": self.report_schema_sha256,
            "budget_results": [item.to_payload() for item in self.budget_results],
        }


@dataclass(frozen=True, slots=True, init=False)
class VerticalSliceMeasurement:
    """Decode-only workload evidence plus the public policy-only non-run state.

    Ordinary callers can only construct the explicit non-run value.  Completed
    evidence is assembled through the live-writer authority, while failed
    states remain decode-only for audit compatibility.
    """

    run_status: VerticalSliceRunStatus
    result_code: str | None
    phase_measurements: tuple[PhaseMeasurement, ...] = ()
    worker_resource_measurements: tuple[WorkerResourceMeasurement, ...] = ()
    workspace_usage_measurements: tuple[WorkspaceUsageMeasurement, ...] = ()
    completed_evidence: CompletedRunEvidence | None = None
    failed_evidence: FailedRunEvidence | None = None

    def __init__(self) -> None:
        object.__setattr__(self, "run_status", VerticalSliceRunStatus.NOT_RUN)
        object.__setattr__(self, "result_code", NOT_RUN_CODE)
        object.__setattr__(self, "phase_measurements", ())
        object.__setattr__(self, "worker_resource_measurements", ())
        object.__setattr__(self, "workspace_usage_measurements", ())
        object.__setattr__(self, "completed_evidence", None)
        object.__setattr__(self, "failed_evidence", None)
        self._validate()

    @classmethod
    def _from_decoded(
        cls,
        *,
        run_status: VerticalSliceRunStatus,
        result_code: str | None,
        phase_measurements: tuple[PhaseMeasurement, ...],
        worker_resource_measurements: tuple[WorkerResourceMeasurement, ...],
        workspace_usage_measurements: tuple[WorkspaceUsageMeasurement, ...],
        completed_evidence: CompletedRunEvidence | None,
        failed_evidence: FailedRunEvidence | None,
    ) -> VerticalSliceMeasurement:
        instance = object.__new__(cls)
        object.__setattr__(instance, "run_status", run_status)
        object.__setattr__(instance, "result_code", result_code)
        object.__setattr__(instance, "phase_measurements", phase_measurements)
        object.__setattr__(
            instance,
            "worker_resource_measurements",
            worker_resource_measurements,
        )
        object.__setattr__(
            instance,
            "workspace_usage_measurements",
            workspace_usage_measurements,
        )
        object.__setattr__(instance, "completed_evidence", completed_evidence)
        object.__setattr__(instance, "failed_evidence", failed_evidence)
        instance._validate()
        return instance

    def _validate(self) -> None:
        if type(self.run_status) is not VerticalSliceRunStatus:
            _reject("M1_MEASUREMENT_INVALID", "The vertical-slice status is unknown.")
        if (
            type(self.phase_measurements) is not tuple
            or any(type(item) is not PhaseMeasurement for item in self.phase_measurements)
            or type(self.worker_resource_measurements) is not tuple
            or any(
                type(item) is not WorkerResourceMeasurement
                for item in self.worker_resource_measurements
            )
            or type(self.workspace_usage_measurements) is not tuple
            or any(
                type(item) is not WorkspaceUsageMeasurement
                for item in self.workspace_usage_measurements
            )
            or (
                self.completed_evidence is not None
                and type(self.completed_evidence) is not CompletedRunEvidence
            )
            or (
                self.failed_evidence is not None
                and type(self.failed_evidence) is not FailedRunEvidence
            )
        ):
            _reject("M1_MEASUREMENT_INVALID", "Workload measurements use an invalid type.")
        phases = tuple(item.phase for item in self.phase_measurements)
        workers = tuple(item.operation for item in self.worker_resource_measurements)
        checkpoints = tuple(item.checkpoint for item in self.workspace_usage_measurements)
        if len(set(phases)) != len(phases) or not _ordered_subset(phases, EXPECTED_PHASES):
            _reject("M1_MEASUREMENT_INVALID", "Phase measurements must be unique and ordered.")
        if len(set(workers)) != len(workers) or not _ordered_subset(
            workers, EXPECTED_WORKER_OPERATIONS
        ):
            _reject("M1_MEASUREMENT_INVALID", "Worker measurements must be unique and ordered.")
        if len(set(checkpoints)) != len(checkpoints) or not _ordered_subset(
            checkpoints, EXPECTED_WORKSPACE_CHECKPOINTS
        ):
            _reject("M1_MEASUREMENT_INVALID", "Workspace measurements must be unique and ordered.")

        if self.run_status is VerticalSliceRunStatus.NOT_RUN:
            if (
                self.result_code != NOT_RUN_CODE
                or self.phase_measurements
                or self.worker_resource_measurements
                or self.workspace_usage_measurements
                or self.completed_evidence is not None
                or self.failed_evidence is not None
            ):
                _reject(
                    "M1_MEASUREMENT_INVALID",
                    "A non-run report cannot contain invented workload measurements.",
                )
            return

        if self.run_status is VerticalSliceRunStatus.COMPLETED:
            if (
                self.result_code is not None
                or phases != EXPECTED_PHASES
                or any(
                    item.outcome is not PhaseOutcome.COMPLETED for item in self.phase_measurements
                )
                or workers != EXPECTED_WORKER_OPERATIONS
                or checkpoints != EXPECTED_WORKSPACE_CHECKPOINTS
                or type(self.completed_evidence) is not CompletedRunEvidence
                or self.failed_evidence is not None
            ):
                _reject(
                    "M1_MEASUREMENT_INVALID",
                    "A completed run requires every ordered phase and resource observation.",
                )
            assert self.completed_evidence is not None
            measured_phase_ns = sum(item.elapsed_ns for item in self.phase_measurements)
            if (
                self.completed_evidence.total_elapsed_ns < measured_phase_ns
                or self.completed_evidence.maximum_heartbeat_gap_ns
                > self.completed_evidence.total_elapsed_ns
            ):
                _reject(
                    "M1_MEASUREMENT_INVALID",
                    "Completed timing evidence is not causally consistent.",
                )
            return

        if self.result_code is None or self.result_code == NOT_RUN_CODE:
            _reject("M1_MEASUREMENT_INVALID", "A failed run requires a stable failure code.")
        _stable_result_code(self.result_code)
        if (
            self.completed_evidence is not None
            or type(self.failed_evidence) is not FailedRunEvidence
        ):
            _reject(
                "M1_MEASUREMENT_INVALID",
                "A failed run requires closed failure evidence.",
            )
        failed_evidence = self.failed_evidence
        measured_phase_ns = sum(item.elapsed_ns for item in self.phase_measurements)
        if failed_evidence.total_elapsed_ns < measured_phase_ns:
            _reject("M1_MEASUREMENT_INVALID", "Failed timing evidence is not causally consistent.")
        if failed_evidence.failure_stage is FailureStage.SETUP:
            if self.phase_measurements or workers or checkpoints:
                _reject(
                    "M1_MEASUREMENT_INVALID",
                    "A setup failure cannot contain invented workload measurements.",
                )
            return
        if failed_evidence.failure_stage is FailureStage.BUDGET_EVALUATION:
            if (
                self.result_code != "M1_RESOURCE_BUDGET_FAILED"
                or phases != EXPECTED_PHASES
                or any(
                    item.outcome is not PhaseOutcome.COMPLETED for item in self.phase_measurements
                )
                or workers != EXPECTED_WORKER_OPERATIONS
                or checkpoints != EXPECTED_WORKSPACE_CHECKPOINTS
            ):
                _reject(
                    "M1_MEASUREMENT_INVALID",
                    "A budget-evaluation failure requires the complete measured workload.",
                )
            return
        if phases != EXPECTED_PHASES[: len(phases)]:
            _reject(
                "M1_MEASUREMENT_INVALID",
                "A workload failure requires a contiguous phase prefix.",
            )
        outcomes_are_truthful = (
            not self.phase_measurements
            or (
                self.phase_measurements[-1].outcome is PhaseOutcome.FAILED
                and all(
                    item.outcome is PhaseOutcome.COMPLETED for item in self.phase_measurements[:-1]
                )
            )
            or (
                phases == EXPECTED_PHASES
                and all(item.outcome is PhaseOutcome.COMPLETED for item in self.phase_measurements)
            )
        )
        if not outcomes_are_truthful:
            _reject(
                "M1_MEASUREMENT_INVALID",
                "A workload failure has an invalid phase outcome sequence.",
            )
        attempted_phases = frozenset(phases)
        worker_phase = {
            WorkerOperation.SOURCE_INSPECTION: M1Phase.PDF_IMPORT,
            WorkerOperation.THUMBNAIL_RENDER: M1Phase.THUMBNAIL_RENDER,
            WorkerOperation.VERIFIED_EXPORT: M1Phase.VERIFIED_EXPORT,
        }
        if any(worker_phase[operation] not in attempted_phases for operation in workers):
            _reject(
                "M1_MEASUREMENT_INVALID",
                "Failed-run worker measurements cannot outlive the attempted phase prefix.",
            )
        completed_phases = frozenset(
            item.phase for item in self.phase_measurements if item.outcome is PhaseOutcome.COMPLETED
        )
        checkpoint_phase = {
            WorkspaceCheckpoint.CREATED: M1Phase.CASE_CREATE,
            WorkspaceCheckpoint.IMPORTED: M1Phase.PDF_IMPORT,
            WorkspaceCheckpoint.ORGANIZED: M1Phase.ORGANIZE,
            WorkspaceCheckpoint.EXPORTED: M1Phase.VERIFIED_EXPORT,
            WorkspaceCheckpoint.RECOVERED: M1Phase.CASE_REOPEN_RECOVERY,
        }
        if any(checkpoint_phase[checkpoint] not in completed_phases for checkpoint in checkpoints):
            _reject(
                "M1_MEASUREMENT_INVALID",
                "Failed-run workspace checkpoints require their completed phase.",
            )

    def _to_payload(self) -> dict[str, object]:
        """Return the closed M1 workload object."""

        payload: dict[str, object] = {
            "run_status": self.run_status.value,
            "result_code": self.result_code,
            "phase_measurements": [
                {
                    "phase": item.phase.value,
                    "outcome": item.outcome.value,
                    "elapsed_ns": item.elapsed_ns,
                }
                for item in self.phase_measurements
            ],
            "worker_resource_measurements": [
                {
                    "operation": item.operation.value,
                    "process_memory_limit_bytes": item.process_memory_limit_bytes,
                    "job_memory_limit_bytes": item.job_memory_limit_bytes,
                    "peak_process_memory_used_bytes": item.peak_process_memory_used_bytes,
                    "peak_job_memory_used_bytes": item.peak_job_memory_used_bytes,
                }
                for item in self.worker_resource_measurements
            ],
            "workspace_usage_measurements": [
                {
                    "checkpoint": item.checkpoint.value,
                    "case_bytes": item.case_bytes,
                    "cache_bytes": item.cache_bytes,
                    "temp_bytes": item.temp_bytes,
                    "entry_count": item.entry_count,
                    "maximum_depth_seen": item.maximum_depth_seen,
                }
                for item in self.workspace_usage_measurements
            ],
        }
        if self.completed_evidence is not None:
            payload["completed_evidence"] = self.completed_evidence._to_payload()
        if self.failed_evidence is not None:
            payload["failed_evidence"] = self.failed_evidence._to_payload()
        return payload


def _ordered_subset[T](actual: tuple[T, ...], expected: tuple[T, ...]) -> bool:
    positions = {item: index for index, item in enumerate(expected)}
    try:
        indices = tuple(positions[item] for item in actual)
    except KeyError:
        return False
    return indices == tuple(sorted(indices))


class PhaseMeasurementRecorder:
    """Record real phase wall time with ``time.perf_counter_ns`` by default."""

    def __init__(self, clock: Callable[[], int] = time.perf_counter_ns) -> None:
        self._clock = clock
        self._measurements: list[PhaseMeasurement] = []

    @property
    def measurements(self) -> tuple[PhaseMeasurement, ...]:
        return tuple(self._measurements)

    @contextmanager
    def measure(self, phase: M1Phase) -> Iterator[None]:
        if type(phase) is not M1Phase:
            _reject("M1_MEASUREMENT_INVALID", "The requested phase is unknown.")
        current = tuple(item.phase for item in self._measurements)
        if len(current) >= len(EXPECTED_PHASES) or phase is not EXPECTED_PHASES[len(current)]:
            _reject(
                "M1_MEASUREMENT_INVALID",
                "Measured phases must form the exact vertical-slice prefix.",
            )
        started = _exact_clock_reading(self._clock(), "perf_counter start")
        try:
            yield
        except BaseException:
            elapsed = _elapsed_ns(started, self._clock())
            self._measurements.append(PhaseMeasurement(phase, PhaseOutcome.FAILED, elapsed))
            raise
        else:
            elapsed = _elapsed_ns(started, self._clock())
            self._measurements.append(PhaseMeasurement(phase, PhaseOutcome.COMPLETED, elapsed))


def _elapsed_ns(started: int, finished_value: object) -> int:
    finished = _exact_clock_reading(finished_value, "perf_counter finish")
    if finished <= started:
        _reject("M1_CLOCK_INVALID", "The monotonic phase clock did not advance.")
    return finished - started


@dataclass(frozen=True, slots=True)
class M1BenchmarkReport:
    """A closed resource report with no host identity or hardware claims."""

    processing_profile: str
    resource_policy_version: int
    resource_limits: Mapping[str, int]
    vertical_slice: VerticalSliceMeasurement

    def __post_init__(self) -> None:
        if (
            type(self.processing_profile) is not str
            or not self.processing_profile
            or type(self.resource_policy_version) is not int
            or self.resource_policy_version < 1
            or type(self.resource_limits) is not dict
            or any(
                type(key) is not str or type(value) is not int
                for key, value in self.resource_limits.items()
            )
            or type(self.vertical_slice) is not VerticalSliceMeasurement
        ):
            _reject("M1_REPORT_INVALID", "The report resource-policy identity is invalid.")

    def to_payload(self) -> dict[str, object]:
        """Return only the supported public policy-only non-run payload."""

        if self.vertical_slice.run_status is not VerticalSliceRunStatus.NOT_RUN:
            _reject(
                LIVE_RUNNER_UNAVAILABLE_CODE,
                "Completed or failed workload reports require the trusted live runner.",
            )
        return self._to_payload()

    def _to_payload(self) -> dict[str, object]:
        return {
            "schema_version": 3,
            "report_kind": "m1-resource-budget-measurement",
            "workload": {"id": WORKLOAD_ID, "version": WORKLOAD_VERSION},
            "processing_profile": self.processing_profile,
            "resource_policy_version": self.resource_policy_version,
            "resource_limits": dict(self.resource_limits),
            "limitations": [HARDWARE_CLAIM_LIMITATION, MEASUREMENT_LIMITATION],
            "vertical_slice": self.vertical_slice._to_payload(),
        }


@dataclass(frozen=True, slots=True)
class HistoricalM1BenchmarkReport:
    """Validated canonical prior-version bytes retained for historical reads only."""

    canonical_payload: bytes
    run_status: str

    def __post_init__(self) -> None:
        if (
            type(self.canonical_payload) is not bytes
            or not self.canonical_payload
            or self.run_status not in {"not-run", "completed", "failed"}
        ):
            _reject("M1_REPORT_INVALID", "The historical report is invalid.")


def build_m1_benchmark_report(
    resource_policy: ResourcePolicy,
) -> M1BenchmarkReport:
    """Build a constrained-profile non-run report without inspecting the host."""

    if type(resource_policy) is not ResourcePolicy:
        _reject("M1_RESOURCE_POLICY_INVALID", "The resource policy is invalid.")
    return M1BenchmarkReport(
        resource_policy.profile.value,
        resource_policy.policy_version,
        _resource_limits(resource_policy),
        VerticalSliceMeasurement(),
    )


def _resource_limits(policy: ResourcePolicy) -> dict[str, int]:
    """Serialize the complete numeric policy control set, excluding its top-level identity."""

    return {
        "maximum_case_bytes": policy.maximum_case_bytes,
        "maximum_cache_bytes": policy.maximum_cache_bytes,
        "maximum_temporary_storage_bytes": policy.maximum_temporary_storage_bytes,
        "maximum_source_bytes": policy.maximum_source_bytes,
        "maximum_page_count": policy.maximum_page_count,
        "worker_process_memory_bytes": policy.worker_process_memory_bytes,
        "worker_job_memory_bytes": policy.worker_job_memory_bytes,
        "worker_cpu_time_ms": policy.worker_cpu_time_ms,
        "worker_maximum_processes": policy.worker_maximum_processes,
        "maximum_heavy_operation_concurrency": policy.maximum_heavy_operation_concurrency,
        "maximum_preview_request_pages": policy.maximum_preview_request_pages,
        "preview_prefetch_radius_pages": policy.preview_prefetch_radius_pages,
        "thumbnail_preview_cache_bytes": policy.thumbnail_preview_cache_bytes,
        "maximum_preview_pixels_per_page": policy.maximum_preview_pixels_per_page,
        "import_io_chunk_bytes": policy.import_io_chunk_bytes,
        "canonical_process_tree_rss_bytes": policy.canonical_process_tree_rss_bytes,
        "minimum_export_free_space_reserve_bytes": policy.minimum_export_free_space_reserve_bytes,
        "maximum_ui_heartbeat_gap_ms": policy.maximum_ui_heartbeat_gap_ms,
        "cancellation_acknowledgement_ms": policy.cancellation_acknowledgement_ms,
        "maximum_active_full_resolution_page_buffers": (
            policy.maximum_active_full_resolution_page_buffers
        ),
        "resource_sampling_interval_ms": policy.resource_sampling_interval_ms,
    }


def _build_live_m1_completed_report(
    authority: _LiveM1ReportAuthority,
    policy_report: M1BenchmarkReport,
    *,
    phase_measurements: tuple[PhaseMeasurement, ...],
    worker_resource_measurements: tuple[WorkerResourceMeasurement, ...],
    workspace_usage_measurements: tuple[WorkspaceUsageMeasurement, ...],
    completed_evidence: CompletedRunEvidence,
) -> M1BenchmarkReport:
    """Build one completed report through the live runner's retained authority."""

    _require_live_m1_report_authority(authority)
    if (
        type(policy_report) is not M1BenchmarkReport
        or policy_report.vertical_slice.run_status is not VerticalSliceRunStatus.NOT_RUN
        or type(completed_evidence) is not CompletedRunEvidence
    ):
        _reject(
            "M1_REPORT_INVALID",
            "The live run is not bound to a valid policy report.",
        )
    try:
        # Reuse the public encoder as the authority check for the policy-only
        # portion before live evidence is attached.
        encode_m1_benchmark_report(policy_report)
        measurement = VerticalSliceMeasurement._from_decoded(
            run_status=VerticalSliceRunStatus.COMPLETED,
            result_code=None,
            phase_measurements=phase_measurements,
            worker_resource_measurements=worker_resource_measurements,
            workspace_usage_measurements=workspace_usage_measurements,
            completed_evidence=completed_evidence,
            failed_evidence=None,
        )
        return M1BenchmarkReport(
            policy_report.processing_profile,
            policy_report.resource_policy_version,
            dict(policy_report.resource_limits),
            measurement,
        )
    except M1BenchmarkError:
        raise
    except Exception:
        _reject(
            "M1_REPORT_INVALID",
            "The completed measurement report could not be constructed safely.",
        )


def _build_live_m1_failed_report(
    authority: _LiveM1ReportAuthority,
    policy_report: M1BenchmarkReport,
    *,
    result_code: str,
    phase_measurements: tuple[PhaseMeasurement, ...],
    worker_resource_measurements: tuple[WorkerResourceMeasurement, ...],
    workspace_usage_measurements: tuple[WorkspaceUsageMeasurement, ...],
    failed_evidence: FailedRunEvidence,
) -> M1BenchmarkReport:
    """Build one truthful failed report through the live runner's retained authority."""

    _require_live_m1_report_authority(authority)
    if (
        type(policy_report) is not M1BenchmarkReport
        or policy_report.vertical_slice.run_status is not VerticalSliceRunStatus.NOT_RUN
        or type(failed_evidence) is not FailedRunEvidence
    ):
        _reject("M1_REPORT_INVALID", "The failed live run is not bound to a valid policy report.")
    try:
        encode_m1_benchmark_report(policy_report)
        measurement = VerticalSliceMeasurement._from_decoded(
            run_status=VerticalSliceRunStatus.FAILED,
            result_code=result_code,
            phase_measurements=phase_measurements,
            worker_resource_measurements=worker_resource_measurements,
            workspace_usage_measurements=workspace_usage_measurements,
            completed_evidence=None,
            failed_evidence=failed_evidence,
        )
        return M1BenchmarkReport(
            policy_report.processing_profile,
            policy_report.resource_policy_version,
            dict(policy_report.resource_limits),
            measurement,
        )
    except M1BenchmarkError:
        raise
    except Exception:
        _reject(
            "M1_REPORT_INVALID",
            "The failed measurement report could not be constructed safely.",
        )


def encode_m1_benchmark_report(report: M1BenchmarkReport) -> bytes:
    """Encode only the supported policy-only non-run report."""

    if (
        type(report) is not M1BenchmarkReport
        or report.vertical_slice.run_status is not VerticalSliceRunStatus.NOT_RUN
    ):
        _reject(
            LIVE_RUNNER_UNAVAILABLE_CODE,
            "Completed or failed workload reports require the trusted live runner.",
        )
    encoded = _canonical_report_bytes(report)
    decoded, issues = decode_m1_benchmark_report(encoded)
    if issues or decoded is None:
        _reject("M1_REPORT_INVALID", "The constructed measurement report failed validation.")
    return encoded


def _encode_live_m1_completed_report(
    authority: _LiveM1ReportAuthority,
    report: M1BenchmarkReport,
) -> bytes:
    """Canonically encode and independently decode one trusted completed report."""

    _require_live_m1_report_authority(authority)
    if (
        type(report) is not M1BenchmarkReport
        or report.vertical_slice.run_status is not VerticalSliceRunStatus.COMPLETED
    ):
        _reject(
            "M1_REPORT_INVALID",
            "The trusted live serializer accepts only a completed workload report.",
        )
    try:
        encoded = _canonical_report_bytes(report)
        decoded, issues = decode_m1_benchmark_report(encoded)
    except M1BenchmarkError:
        raise
    except Exception:
        _reject(
            "M1_REPORT_INVALID",
            "The completed measurement report could not be validated safely.",
        )
    if issues or decoded != report:
        _reject(
            "M1_REPORT_INVALID",
            "The completed measurement report failed canonical validation.",
        )
    return encoded


def _encode_live_m1_failed_report(
    authority: _LiveM1ReportAuthority,
    report: M1BenchmarkReport,
) -> bytes:
    """Canonically encode and independently decode one trusted failed report."""

    _require_live_m1_report_authority(authority)
    if (
        type(report) is not M1BenchmarkReport
        or report.vertical_slice.run_status is not VerticalSliceRunStatus.FAILED
    ):
        _reject(
            "M1_REPORT_INVALID",
            "The trusted live serializer accepts only a failed workload report.",
        )
    try:
        encoded = _canonical_report_bytes(report)
        decoded, issues = decode_m1_benchmark_report(encoded)
    except M1BenchmarkError:
        raise
    except Exception:
        _reject(
            "M1_REPORT_INVALID",
            "The failed measurement report could not be validated safely.",
        )
    if issues or decoded != report:
        _reject(
            "M1_REPORT_INVALID",
            "The failed measurement report failed canonical validation.",
        )
    return encoded


def _canonical_report_bytes(report: M1BenchmarkReport) -> bytes:
    try:
        return (
            json.dumps(
                report._to_payload(),
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    except TypeError, ValueError:
        _reject("M1_REPORT_INVALID", "The measurement report cannot be encoded safely.")


def decode_m1_benchmark_report(
    payload: bytes,
    *,
    root: Path = PROJECT_ROOT,
) -> tuple[M1BenchmarkReport | HistoricalM1BenchmarkReport | None, list[BenchmarkIssue]]:
    """Validate a bounded closed report without asserting hardware equivalence.

    Decoding supports the closed completed/failed shapes needed for audit and
    the trusted live writer, while the public serializer still refuses them.
    """

    loaded, issues = _load_report_object(payload)
    if loaded is None:
        return None, sorted(issues)
    if loaded.get("report_schema_version") == 1 and "schema_version" not in loaded:
        return _decode_historical_report(
            payload,
            loaded,
            root,
            issues,
            version=1,
            schema_path=HISTORICAL_REPORT_SCHEMA_PATH,
        )
    if loaded.get("schema_version") == 2 and "report_schema_version" not in loaded:
        return _decode_historical_report(
            payload,
            loaded,
            root,
            issues,
            version=2,
            schema_path=HISTORICAL_V2_REPORT_SCHEMA_PATH,
        )
    if loaded.get("schema_version") != 3 or "report_schema_version" in loaded:
        return None, [
            BenchmarkIssue(
                "M1_REPORT_VERSION_UNSUPPORTED",
                "$",
                "report schema version is unsupported",
            )
        ]
    schema, schema_issues = _load_schema(root, REPORT_SCHEMA_PATH)
    issues.extend(schema_issues)
    if loaded is None or schema is None or issues:
        return None, sorted(issues)
    validator = Draft202012Validator(schema)
    validation_errors = sorted(
        validator.iter_errors(loaded), key=lambda error: tuple(str(item) for item in error.path)
    )
    if validation_errors:
        return None, [
            BenchmarkIssue(
                "M1_REPORT_SCHEMA_INVALID",
                _json_path(tuple(error.absolute_path)),
                "report does not match the closed v3 schema",
            )
            for error in validation_errors
        ]
    try:
        report = _report_from_payload(loaded, root)
    except M1BenchmarkError as error:
        return None, [BenchmarkIssue(error.code, "$", error.safe_message)]
    if payload != _canonical_report_bytes(report):
        return None, [
            BenchmarkIssue(
                "M1_REPORT_NONCANONICAL",
                "$",
                "report bytes do not use the canonical v3 encoding",
            )
        ]
    return report, []


def _decode_historical_report(
    payload: bytes,
    loaded: dict[str, Any],
    root: Path,
    issues: list[BenchmarkIssue],
    *,
    version: int,
    schema_path: Path,
) -> tuple[HistoricalM1BenchmarkReport | None, list[BenchmarkIssue]]:
    """Validate immutable prior-version evidence without offering a legacy writer."""

    schema, schema_issues = _load_schema(root, schema_path)
    issues.extend(schema_issues)
    if schema is None or issues:
        return None, sorted(issues)
    validation_errors = sorted(
        Draft202012Validator(schema).iter_errors(loaded),
        key=lambda error: tuple(str(item) for item in error.path),
    )
    if validation_errors:
        return None, [
            BenchmarkIssue(
                "M1_REPORT_SCHEMA_INVALID",
                _json_path(tuple(error.absolute_path)),
                f"historical report does not match the closed v{version} schema",
            )
            for error in validation_errors
        ]
    canonical = _canonical_mapping_bytes(loaded)
    if payload != canonical:
        return None, [
            BenchmarkIssue(
                "M1_REPORT_NONCANONICAL",
                "$",
                f"historical report bytes do not use the canonical v{version} encoding",
            )
        ]
    vertical = cast(Mapping[str, object], loaded["vertical_slice"])
    return HistoricalM1BenchmarkReport(canonical, cast(str, vertical["run_status"])), []


def _canonical_mapping_bytes(value: Mapping[str, object]) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    except TypeError, ValueError:
        _reject("M1_REPORT_INVALID", "The measurement report cannot be encoded safely.")


def _load_report_object(payload: bytes) -> tuple[dict[str, Any] | None, list[BenchmarkIssue]]:
    if not 1 <= len(payload) <= MAX_REPORT_BYTES:
        return None, [
            BenchmarkIssue("M1_REPORT_SIZE_INVALID", "$", "report exceeds the bounded size")
        ]
    try:
        text = payload.decode("utf-8")
        loaded = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except UnicodeDecodeError:
        return None, [BenchmarkIssue("M1_REPORT_ENCODING_INVALID", "$", "report is not UTF-8")]
    except DuplicateJsonKeyError:
        return None, [
            BenchmarkIssue("M1_REPORT_DUPLICATE_KEY", "$", "report member names must be unique")
        ]
    except NonStandardJsonConstantError:
        return None, [
            BenchmarkIssue(
                "M1_REPORT_NONSTANDARD_NUMBER", "$", "NaN and Infinity are not permitted"
            )
        ]
    except json.JSONDecodeError, RecursionError, ValueError:
        return None, [BenchmarkIssue("M1_REPORT_JSON_INVALID", "$", "report is not valid JSON")]
    if type(loaded) is not dict:
        return None, [BenchmarkIssue("M1_REPORT_ROOT_INVALID", "$", "report must be an object")]
    return cast(dict[str, Any], loaded), []


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError(key)
        result[key] = value
    return result


def _reject_json_constant(value: str) -> NoReturn:
    raise NonStandardJsonConstantError(value)


def _load_schema(
    root: Path,
    relative_path: Path,
) -> tuple[dict[str, Any] | None, list[BenchmarkIssue]]:
    path = root / relative_path
    try:
        payload = path.read_bytes()
    except OSError:
        return None, [
            BenchmarkIssue(
                "M1_REPORT_SCHEMA_UNREADABLE",
                relative_path.as_posix(),
                "schema is unavailable",
            )
        ]
    if not 1 <= len(payload) <= MAX_SCHEMA_BYTES:
        return None, [
            BenchmarkIssue(
                "M1_REPORT_SCHEMA_SIZE_INVALID",
                relative_path.as_posix(),
                "schema size is invalid",
            )
        ]
    loaded, issues = _load_report_object(payload)
    if issues or loaded is None:
        return None, [
            BenchmarkIssue(
                "M1_REPORT_SCHEMA_JSON_INVALID",
                relative_path.as_posix(),
                "schema is not strict JSON",
            )
        ]
    try:
        Draft202012Validator.check_schema(loaded)
    except SchemaError:
        return None, [
            BenchmarkIssue(
                "M1_REPORT_SCHEMA_DEFINITION_INVALID",
                relative_path.as_posix(),
                "schema is not valid Draft 2020-12",
            )
        ]
    return loaded, []


def validate_report_schema(root: Path = PROJECT_ROOT) -> list[BenchmarkIssue]:
    """Validate immutable historical v1/v2 and current v3 schemas."""

    _, historical_v1_issues = _load_schema(root, HISTORICAL_REPORT_SCHEMA_PATH)
    _, historical_v2_issues = _load_schema(root, HISTORICAL_V2_REPORT_SCHEMA_PATH)
    _, current_issues = _load_schema(root, REPORT_SCHEMA_PATH)
    return sorted([*historical_v1_issues, *historical_v2_issues, *current_issues])


def _report_from_payload(value: Mapping[str, Any], root: Path) -> M1BenchmarkReport:
    try:
        selected_policy = select_resource_policy(
            ProcessingProfile(cast(str, value["processing_profile"])),
            environ={},
        )
    except TypeError, ValueError:
        _reject("M1_RESOURCE_POLICY_INVALID", "The report resource-policy identity is invalid.")
    if value["resource_policy_version"] != selected_policy.policy_version or value[
        "resource_limits"
    ] != _resource_limits(selected_policy):
        _reject(
            "M1_RESOURCE_POLICY_INVALID",
            "The report limits do not match the checked-in resource policy.",
        )
    vertical = cast(Mapping[str, Any], value["vertical_slice"])
    phase_measurements = tuple(
        PhaseMeasurement(
            M1Phase(cast(str, item["phase"])),
            PhaseOutcome(cast(str, item["outcome"])),
            cast(int, item["elapsed_ns"]),
        )
        for item in cast(list[Mapping[str, object]], vertical["phase_measurements"])
    )
    worker_measurements = tuple(
        WorkerResourceMeasurement(
            WorkerOperation(cast(str, item["operation"])),
            cast(int, item["process_memory_limit_bytes"]),
            cast(int, item["job_memory_limit_bytes"]),
            cast(int, item["peak_process_memory_used_bytes"]),
            cast(int, item["peak_job_memory_used_bytes"]),
        )
        for item in cast(list[Mapping[str, object]], vertical["worker_resource_measurements"])
    )
    workspace_measurements = tuple(
        WorkspaceUsageMeasurement(
            WorkspaceCheckpoint(cast(str, item["checkpoint"])),
            cast(int, item["case_bytes"]),
            cast(int, item["cache_bytes"]),
            cast(int, item["temp_bytes"]),
            cast(int, item["entry_count"]),
            cast(int, item["maximum_depth_seen"]),
        )
        for item in cast(list[Mapping[str, object]], vertical["workspace_usage_measurements"])
    )
    raw_completed_evidence = vertical.get("completed_evidence")
    completed_evidence = (
        None
        if raw_completed_evidence is None
        else CompletedRunEvidence.from_payload(cast(Mapping[str, object], raw_completed_evidence))
    )
    raw_failed_evidence = vertical.get("failed_evidence")
    failed_evidence = (
        None
        if raw_failed_evidence is None
        else FailedRunEvidence.from_payload(cast(Mapping[str, object], raw_failed_evidence))
    )
    result_code = vertical["result_code"]
    measurement = VerticalSliceMeasurement._from_decoded(
        run_status=VerticalSliceRunStatus(cast(str, vertical["run_status"])),
        result_code=cast(str | None, result_code),
        phase_measurements=phase_measurements,
        worker_resource_measurements=worker_measurements,
        workspace_usage_measurements=workspace_measurements,
        completed_evidence=completed_evidence,
        failed_evidence=failed_evidence,
    )
    report = M1BenchmarkReport(
        cast(str, value["processing_profile"]),
        cast(int, value["resource_policy_version"]),
        cast(dict[str, int], value["resource_limits"]),
        measurement,
    )
    if measurement.completed_evidence is not None:
        evidence = measurement.completed_evidence
        if evidence.report_schema_sha256 != _checked_file_sha256(
            root, REPORT_SCHEMA_PATH, context="report schema"
        ):
            _reject(
                "M1_IDENTITY_INVALID",
                "Completed evidence is not bound to the checked-in report schema.",
            )
        _validate_completed_resource_evidence(report, selected_policy)
    if measurement.failed_evidence is not None:
        if measurement.failed_evidence.report_schema_sha256 != _checked_file_sha256(
            root, REPORT_SCHEMA_PATH, context="report schema"
        ):
            _reject(
                "M1_IDENTITY_INVALID",
                "Failed evidence is not bound to the checked-in report schema.",
            )
    return report


def _validate_completed_resource_evidence(
    report: M1BenchmarkReport,
    policy: ResourcePolicy,
) -> None:
    evidence = report.vertical_slice.completed_evidence
    assert evidence is not None
    checkpoint_cache = max(
        item.cache_bytes for item in report.vertical_slice.workspace_usage_measurements
    )
    checkpoint_temp = max(
        item.temp_bytes for item in report.vertical_slice.workspace_usage_measurements
    )
    expected_results = (
        BudgetResult(
            BudgetId.PROCESS_TREE_RSS,
            evidence.peak_process_tree_rss_bytes,
            policy.canonical_process_tree_rss_bytes,
            evidence.peak_process_tree_rss_bytes <= policy.canonical_process_tree_rss_bytes,
        ),
        BudgetResult(
            BudgetId.CACHE_BYTES,
            evidence.peak_cache_bytes,
            policy.maximum_cache_bytes + policy.thumbnail_preview_cache_bytes,
            evidence.peak_cache_bytes
            <= policy.maximum_cache_bytes + policy.thumbnail_preview_cache_bytes,
        ),
        BudgetResult(
            BudgetId.TEMP_BYTES,
            evidence.peak_temp_bytes,
            policy.maximum_temporary_storage_bytes,
            evidence.peak_temp_bytes <= policy.maximum_temporary_storage_bytes,
        ),
        BudgetResult(
            BudgetId.HEAVY_CONCURRENCY,
            evidence.max_heavy_operation_concurrency,
            policy.maximum_heavy_operation_concurrency,
            evidence.max_heavy_operation_concurrency <= policy.maximum_heavy_operation_concurrency,
        ),
        BudgetResult(
            BudgetId.UI_HEARTBEAT,
            _ceil_milliseconds(evidence.maximum_heartbeat_gap_ns),
            policy.maximum_ui_heartbeat_gap_ms,
            evidence.maximum_heartbeat_gap_ns <= policy.maximum_ui_heartbeat_gap_ms * 1_000_000,
        ),
        BudgetResult(
            BudgetId.CANCELLATION_ACKNOWLEDGEMENT,
            evidence.cancellation_acknowledgement_ms,
            policy.cancellation_acknowledgement_ms,
            evidence.cancellation_acknowledgement_ms <= policy.cancellation_acknowledgement_ms,
        ),
    )
    if (
        evidence.peak_cache_bytes < checkpoint_cache
        or evidence.peak_temp_bytes < checkpoint_temp
        or evidence.duration_ms != max(1, evidence.total_elapsed_ns // 1_000_000)
        or evidence.ui_heartbeat_violation_count
        != int(evidence.maximum_heartbeat_gap_ns > policy.maximum_ui_heartbeat_gap_ms * 1_000_000)
        or evidence.budget_results != expected_results
    ):
        _reject(
            "M1_RESOURCE_BUDGET_INVALID",
            "Completed resource evidence is inconsistent with measured values.",
        )


def _ceil_milliseconds(nanoseconds: int) -> int:
    return (nanoseconds + 999_999) // 1_000_000


def _checked_file_sha256(root: Path, relative_path: Path, *, context: str) -> str:
    try:
        payload = (root / relative_path).read_bytes()
    except OSError:
        _reject("M1_IDENTITY_INVALID", f"The checked-in {context} identity is unavailable.")
    if not 1 <= len(payload) <= MAX_SCHEMA_BYTES:
        _reject("M1_IDENTITY_INVALID", f"The checked-in {context} identity is invalid.")
    return hashlib.sha256(payload).hexdigest()


class _LiveM1ReportWriter:
    """Canonical-runner-only writer for one fixed, no-overwrite live report.

    Opening retains an authenticated no-follow handle to an existing output
    directory.  Publication writes an opaque same-directory stage, validates
    it through an identity-checked read handle, renames the exact stage handle
    without replacement, and then reopens and decodes the fixed final leaf.
    """

    __slots__ = (
        "_authority",
        "_cleanup_incomplete",
        "_closed",
        "_directory_handle",
        "_output_directory",
        "_published",
    )

    def __init__(
        self,
        authority: _LiveM1ReportAuthority,
        output_directory: Path,
        directory_handle: WindowsHandle,
    ) -> None:
        _require_live_m1_report_authority(authority)
        self._authority = authority
        self._output_directory = output_directory
        self._directory_handle = directory_handle
        self._cleanup_incomplete = False
        self._closed = False
        self._published = False

    @classmethod
    def _open(cls, output_directory: Path) -> Self:
        """Authenticate one existing absolute, non-reparse output directory."""

        if not isinstance(output_directory, Path) or not output_directory.is_absolute():
            _reject(
                LIVE_REPORT_OUTPUT_INVALID_CODE,
                "The live-report output directory must be an existing absolute directory.",
            )
        retained: WindowsHandle | None = None
        selected = Path(output_directory)
        try:
            retained = open_handle(selected, traverse=True)
            require_expected_handle(retained, selected, directory=True)
            before = retained.info
            current = retained.refresh_info()
            if (
                current.identity != before.identity
                or not current.is_directory
                or current.is_reparse_point
            ):
                _reject(
                    LIVE_REPORT_OUTPUT_INVALID_CODE,
                    "The live-report output directory could not be authenticated safely.",
                )
        except M1BenchmarkError:
            _close_live_handle_safely(retained)
            raise
        except WorkspaceError:
            _close_live_handle_safely(retained)
            _reject(
                LIVE_REPORT_OUTPUT_INVALID_CODE,
                "The live-report output directory could not be authenticated safely.",
            )
        except Exception:
            _close_live_handle_safely(retained)
            _reject(
                LIVE_REPORT_OUTPUT_INVALID_CODE,
                "The live-report output directory could not be authenticated safely.",
            )
        except BaseException:
            _close_live_handle_safely(retained)
            raise
        try:
            return cls(
                _LiveM1ReportAuthority(_LIVE_M1_REPORT_AUTHORITY_MARKER),
                selected,
                retained,
            )
        except BaseException:
            _close_live_handle_safely(retained)
            raise

    @property
    def _destination_path(self) -> Path:
        """Return the fixed report destination; callers cannot choose a leaf."""

        return self._output_directory / LIVE_REPORT_FILENAME

    def __enter__(self) -> Self:
        self._require_open()
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: object | None,
    ) -> Literal[False]:
        if exception_type is None:
            self._close()
        else:
            try:
                self._close()
            except BaseException:
                pass
        return False

    def _close(self) -> None:
        """Release the retained output-directory authority exactly once."""

        if self._closed:
            return
        self._closed = True
        try:
            self._directory_handle.close()
        except Exception:
            self._cleanup_incomplete = True
            if self._published:
                return
            _reject(
                LIVE_REPORT_PUBLISH_FAILED_CODE,
                "The live-report output authority could not be released safely.",
            )
        except BaseException:
            self._cleanup_incomplete = True
            raise

    def _build_completed_report(
        self,
        policy_report: M1BenchmarkReport,
        *,
        phase_measurements: tuple[PhaseMeasurement, ...],
        worker_resource_measurements: tuple[WorkerResourceMeasurement, ...],
        workspace_usage_measurements: tuple[WorkspaceUsageMeasurement, ...],
        completed_evidence: CompletedRunEvidence,
    ) -> M1BenchmarkReport:
        """Bind one exact successful workload to a validated host observation."""

        self._require_open()
        try:
            return _build_live_m1_completed_report(
                self._authority,
                policy_report,
                phase_measurements=phase_measurements,
                worker_resource_measurements=worker_resource_measurements,
                workspace_usage_measurements=workspace_usage_measurements,
                completed_evidence=completed_evidence,
            )
        except M1BenchmarkError:
            raise
        except Exception:
            _reject(
                "M1_REPORT_INVALID",
                "The completed measurement report could not be constructed safely.",
            )

    def _encode_completed_report(self, report: M1BenchmarkReport) -> bytes:
        """Encode a completed report through this writer's retained authority."""

        self._require_open()
        try:
            return _encode_live_m1_completed_report(self._authority, report)
        except M1BenchmarkError:
            raise
        except Exception:
            _reject(
                "M1_REPORT_INVALID",
                "The completed measurement report could not be encoded safely.",
            )

    def _build_failed_report(
        self,
        policy_report: M1BenchmarkReport,
        *,
        result_code: str,
        phase_measurements: tuple[PhaseMeasurement, ...],
        worker_resource_measurements: tuple[WorkerResourceMeasurement, ...],
        workspace_usage_measurements: tuple[WorkspaceUsageMeasurement, ...],
        failed_evidence: FailedRunEvidence,
    ) -> M1BenchmarkReport:
        """Bind one failed workload to only the evidence available at failure."""

        self._require_open()
        try:
            return _build_live_m1_failed_report(
                self._authority,
                policy_report,
                result_code=result_code,
                phase_measurements=phase_measurements,
                worker_resource_measurements=worker_resource_measurements,
                workspace_usage_measurements=workspace_usage_measurements,
                failed_evidence=failed_evidence,
            )
        except M1BenchmarkError:
            raise
        except Exception:
            _reject(
                "M1_REPORT_INVALID",
                "The failed measurement report could not be constructed safely.",
            )

    def _encode_failed_report(self, report: M1BenchmarkReport) -> bytes:
        """Encode a failed report through this writer's retained authority."""

        self._require_open()
        try:
            return _encode_live_m1_failed_report(self._authority, report)
        except M1BenchmarkError:
            raise
        except Exception:
            _reject(
                "M1_REPORT_INVALID",
                "The failed measurement report could not be encoded safely.",
            )

    def _publish_completed_report(self, report: M1BenchmarkReport) -> Path:
        """Atomically publish one validated report without replacing any object."""

        return self._publish_report(report, self._encode_completed_report)

    def _publish_failed_report(self, report: M1BenchmarkReport) -> Path:
        """Atomically publish one failed report without publishing a derivative."""

        return self._publish_report(report, self._encode_failed_report)

    def _publish_report(
        self,
        report: M1BenchmarkReport,
        encoder: Callable[[M1BenchmarkReport], bytes],
    ) -> Path:
        """Publish one authenticated canonical report through the shared commit boundary."""

        self._require_open()
        if self._published:
            _reject(
                LIVE_REPORT_ALREADY_EXISTS_CODE,
                "This live-report writer has already published its fixed report.",
            )
        encoded = encoder(report)
        stage_path = self._output_directory / (
            f"{_LIVE_STAGE_PREFIX}{uuid4().hex}{_LIVE_STAGE_SUFFIX}"
        )
        stage_handle: WindowsHandle | None = None
        destination = self._destination_path
        try:
            stage_handle = write_new_file(stage_path, encoded)
            stage_identity = stage_handle.info.identity
            _validate_authenticated_live_report(
                stage_path,
                expected_identity=stage_identity,
                expected_report=report,
            )
            published, error_code = rename_open_file_no_replace_at(
                stage_handle,
                self._directory_handle,
                destination_name=LIVE_REPORT_FILENAME,
            )
            if not published:
                if error_code in _WINDOWS_ALREADY_EXISTS_ERRORS:
                    _reject(
                        LIVE_REPORT_ALREADY_EXISTS_CODE,
                        "The fixed live-report destination already exists and was preserved.",
                    )
                _reject(
                    LIVE_REPORT_PUBLISH_FAILED_CODE,
                    "The live report could not be published safely.",
                )
            current = stage_handle.refresh_info()
            if current.identity != stage_identity or current.link_count != 1:
                _reject(
                    LIVE_REPORT_PUBLISH_FAILED_CODE,
                    "The live report changed during publication.",
                )
            require_expected_handle(
                stage_handle,
                destination,
                directory=False,
                expected_identity=stage_identity,
            )
            _validate_authenticated_live_report(
                destination,
                expected_identity=stage_identity,
                expected_report=report,
            )
            self._mark_report_committed()
        except M1BenchmarkError:
            self._settle_interrupted_publication(stage_handle)
            raise
        except WorkspaceError:
            self._settle_interrupted_publication(stage_handle)
            _reject(
                LIVE_REPORT_PUBLISH_FAILED_CODE,
                "The live report could not be published safely.",
            )
        except Exception:
            self._settle_interrupted_publication(stage_handle)
            _reject(
                LIVE_REPORT_PUBLISH_FAILED_CODE,
                "The live report could not be published safely.",
            )
        except BaseException:
            self._settle_interrupted_publication(stage_handle)
            raise
        self._settle_committed_stage_handle(stage_handle)
        return destination

    def _mark_report_committed(self) -> None:
        """Record the report commit before any interruptible post-commit work."""

        self._published = True

    def _settle_committed_stage_handle(self, handle: WindowsHandle) -> None:
        """Release a post-commit handle without changing the commit result."""

        try:
            handle.close()
        except Exception:
            self._cleanup_incomplete = True
        except BaseException:
            self._cleanup_incomplete = True
            raise

    def _settle_committed_stage_handle_no_raise(self, handle: WindowsHandle | None) -> None:
        """Release post-commit authority without replacing an active exception."""

        if handle is None:
            self._cleanup_incomplete = True
            return
        try:
            handle.close()
        except BaseException:
            self._cleanup_incomplete = True

    def _settle_interrupted_publication(self, handle: WindowsHandle | None) -> None:
        """Preserve committed output and discard only an uncommitted stage."""

        if self._published:
            self._settle_committed_stage_handle_no_raise(handle)
        else:
            _discard_live_stage_safely(handle)

    def _require_open(self) -> None:
        if self._closed or self._directory_handle.value == 0:
            _reject(
                LIVE_REPORT_WRITER_CLOSED_CODE,
                "The live-report writer is already closed.",
            )


def _open_live_m1_report_writer(output_directory: Path) -> _LiveM1ReportWriter:
    """Open the unsupported completed-report boundary for the canonical runner."""

    return _LiveM1ReportWriter._open(output_directory)


def _validate_authenticated_live_report(
    path: Path,
    *,
    expected_identity: FileIdentity,
    expected_report: M1BenchmarkReport,
) -> None:
    reader: WindowsHandle | None = None
    try:
        reader = open_handle(
            path,
            read=True,
            share_delete=True,
            share_write=True,
        )
        require_expected_handle(
            reader,
            path,
            directory=False,
            expected_identity=expected_identity,
        )
        current = reader.refresh_info()
        if (
            current.identity != expected_identity
            or current.is_reparse_point
            or current.is_sparse
            or current.link_count != 1
        ):
            _reject(
                LIVE_REPORT_PUBLISH_FAILED_CODE,
                "The completed live report changed during validation.",
            )
        payload = reader.read_bytes(maximum_bytes=MAX_REPORT_BYTES)
    except BaseException as primary:
        if reader is not None:
            try:
                reader.close()
            except BaseException as cleanup:
                if isinstance(primary, Exception) and not isinstance(cleanup, Exception):
                    raise
        raise
    else:
        if reader is not None:
            reader.close()
    decoded, issues = decode_m1_benchmark_report(payload)
    if issues or decoded != expected_report:
        _reject(
            LIVE_REPORT_PUBLISH_FAILED_CODE,
            "The completed live report failed canonical reopen validation.",
        )


def _discard_live_stage_safely(handle: WindowsHandle | None) -> None:
    if handle is None:
        return
    try:
        handle.mark_for_deletion()
    except BaseException:
        pass
    try:
        handle.close()
    except BaseException:
        pass


def _close_live_handle_safely(handle: WindowsHandle | None) -> None:
    if handle is None:
        return
    try:
        handle.close()
    except BaseException:
        pass


def _json_path(parts: Sequence[object]) -> str:
    rendered = "$"
    for part in parts:
        rendered += f"[{part}]" if isinstance(part, int) else f".{part}"
    return rendered


def main(argv: Sequence[str] | None = None) -> int:
    """Validate the schema or print a constrained-profile non-run report."""

    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="validate the report schema")
    describe = subparsers.add_parser(
        "describe-policy",
        help="print the enforced constrained software policy without inspecting hardware",
    )
    describe.add_argument("--profile", choices=("normal", "constrained"), default="constrained")
    arguments = parser.parse_args(argv)

    if arguments.command == "validate":
        issues = validate_report_schema()
        if issues:
            for issue in issues:
                print(issue.render(), file=sys.stderr)
            return 1
        print(
            "OK: validated constrained-resource M1 report schema at "
            f"{REPORT_SCHEMA_PATH.as_posix()}."
        )
        return 0

    try:
        policy = select_resource_policy(
            ProcessingProfile(cast(str, arguments.profile)),
            environ={},
        )
        report = build_m1_benchmark_report(policy)
        encoded = encode_m1_benchmark_report(report)
    except M1BenchmarkError as error:
        print(f"ERROR [{error.code}] {error.safe_message}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Build and validate record-only Milestone 1 vertical-slice measurements.

The supported builder and CLI intentionally emit only host observations with a
``not-run`` workload status.  Completed and failed payloads are decode-only
until a direct live runner can bind every measurement to the real workload.
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
from typing import Any, Final, NoReturn, Protocol, cast
from uuid import UUID

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from tools.reference_profile import (
    PROFILE_PATH,
    SCENARIO_IDS,
    BenchmarkError,
    ReferenceAttestations,
    evaluate_reference_environment,
    observe_reference_environment,
    validate_profile,
)

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
REPORT_SCHEMA_PATH: Final = Path("schemas/m1-vertical-slice-benchmark-report-v1.schema.json")
MAX_REPORT_BYTES: Final = 256 * 1024
MAX_SCHEMA_BYTES: Final = 256 * 1024
MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991
NOT_RUN_CODE: Final = "M1_VERTICAL_SLICE_NOT_RUN"
LIVE_RUNNER_UNAVAILABLE_CODE: Final = "M1_LIVE_RUNNER_UNAVAILABLE"
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


class ReferenceEvaluationStatus(StrEnum):
    """Result vocabulary owned by the reference-profile evaluator."""

    NOT_EVALUATED = "not-evaluated"
    MATCHED = "matched"
    MISMATCHED = "mismatched"


class RequirementStatus(StrEnum):
    """One requirement comparison from the reference-profile evaluator."""

    MATCHED = "matched"
    MISMATCHED = "mismatched"
    UNKNOWN = "unknown"


class RequirementId(StrEnum):
    """Closed requirement identifiers emitted by reference-profile v1."""

    OS_FAMILY = "os_family"
    ARCHITECTURE = "architecture"
    LOGICAL_PROCESSORS = "logical_processors"
    MEMORY_MIB = "memory_mib"
    GRAPHICS_CLASS = "graphics_class"
    STORAGE_CAPACITY_BYTES = "storage_capacity_bytes"
    STORAGE_FREE_BYTES = "storage_free_bytes"
    STORAGE_MEDIA = "storage_media"


class RequirementBoundary(StrEnum):
    """How a profile requirement constrains an observed value."""

    EXACT = "required"
    MAXIMUM = "required_maximum"
    MINIMUM = "required_minimum"


class ActualValueSource(StrEnum):
    """Where the compared value came from."""

    OBSERVED = "observed"
    ATTESTED = "attested"


class VerificationMethod(StrEnum):
    """Evidence source names pinned by the reference profile."""

    RUNTIME = "runtime-observation"
    OPERATOR_ALLOCATION = "operator-attested-allocation"
    OPERATOR = "operator-attested"


EXPECTED_PHASES: Final = tuple(M1Phase)
EXPECTED_WORKER_OPERATIONS: Final = tuple(WorkerOperation)
EXPECTED_WORKSPACE_CHECKPOINTS: Final = tuple(WorkspaceCheckpoint)
REQUIREMENT_ORDER: Final = tuple(RequirementId)


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


@dataclass(frozen=True, slots=True)
class CompletedRunEvidence:
    """Closed exact identity and result binding for a future direct live runner."""

    run_id: UUID
    sample_count: int
    repository_revision: str
    repository_tree: str
    repository_worktree_clean: bool
    reference_profile_sha256: str
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
    heartbeat_count: int
    maximum_heartbeat_gap_ns: int

    def __post_init__(self) -> None:
        if type(self.run_id) is not UUID or self.run_id.version != 4:
            _reject("M1_MEASUREMENT_INVALID", "The run identity must be a UUIDv4 value.")
        sample_count = _exact_integer(self.sample_count, "sample_count", minimum=1)
        workload_version = _exact_integer(self.workload_version, "workload_version", minimum=1)
        output_count = _exact_integer(self.output_count, "output_count", minimum=1)
        if sample_count != 1 or workload_version != 1 or output_count != 2:
            _reject(
                "M1_MEASUREMENT_INVALID",
                "Completed v1 evidence must use its exact sample, workload, and output counts.",
            )
        _exact_git_object_id(self.repository_revision, "repository_revision")
        _exact_git_object_id(self.repository_tree, "repository_tree")
        for digest_value, field in (
            (self.reference_profile_sha256, "reference_profile_sha256"),
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
        ):
            _exact_integer(numeric_value, field, minimum=minimum)
        if (
            self.repository_worktree_clean is not True
            or self.deterministic_semantic_equal is not True
            or self.deterministic_output_equal is not True
            or self.case_reopened is not True
            or self.reopened_sequence_equal is not True
            or self.background_export_completed is not True
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
            repository_revision=cast(str, value["repository_revision"]),
            repository_tree=cast(str, value["repository_tree"]),
            repository_worktree_clean=cast(bool, value["repository_worktree_clean"]),
            reference_profile_sha256=cast(str, value["reference_profile_sha256"]),
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
            heartbeat_count=cast(int, value["heartbeat_count"]),
            maximum_heartbeat_gap_ns=cast(int, value["maximum_heartbeat_gap_ns"]),
        )

    def _to_payload(self) -> dict[str, object]:
        return {
            "run_id": str(self.run_id),
            "sample_count": self.sample_count,
            "repository_revision": self.repository_revision,
            "repository_tree": self.repository_tree,
            "repository_worktree_clean": self.repository_worktree_clean,
            "reference_profile_sha256": self.reference_profile_sha256,
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
            "heartbeat_count": self.heartbeat_count,
            "maximum_heartbeat_gap_ns": self.maximum_heartbeat_gap_ns,
        }


@dataclass(frozen=True, slots=True, init=False)
class VerticalSliceMeasurement:
    """Decode-only workload evidence plus the one public host-only non-run state.

    A direct runner will own a future trusted completed constructor.  Until
    then, callers can only construct the explicit non-run value; completed and
    failed states exist solely so checked evidence can be decoded and audited.
    """

    run_status: VerticalSliceRunStatus
    result_code: str | None
    phase_measurements: tuple[PhaseMeasurement, ...] = ()
    worker_resource_measurements: tuple[WorkerResourceMeasurement, ...] = ()
    workspace_usage_measurements: tuple[WorkspaceUsageMeasurement, ...] = ()
    completed_evidence: CompletedRunEvidence | None = None

    def __init__(self) -> None:
        object.__setattr__(self, "run_status", VerticalSliceRunStatus.NOT_RUN)
        object.__setattr__(self, "result_code", NOT_RUN_CODE)
        object.__setattr__(self, "phase_measurements", ())
        object.__setattr__(self, "worker_resource_measurements", ())
        object.__setattr__(self, "workspace_usage_measurements", ())
        object.__setattr__(self, "completed_evidence", None)
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
            ):
                _reject(
                    "M1_MEASUREMENT_INVALID",
                    "A completed run requires every ordered phase and resource observation.",
                )
            return

        if self.result_code is None or self.result_code == NOT_RUN_CODE:
            _reject("M1_MEASUREMENT_INVALID", "A failed run requires a stable failure code.")
        _stable_result_code(self.result_code)
        if (
            not self.phase_measurements
            or phases != EXPECTED_PHASES[: len(phases)]
            or self.phase_measurements[-1].outcome is not PhaseOutcome.FAILED
            or any(
                item.outcome is not PhaseOutcome.COMPLETED for item in self.phase_measurements[:-1]
            )
            or self.completed_evidence is not None
        ):
            _reject(
                "M1_MEASUREMENT_INVALID",
                "A failed run requires a contiguous phase prefix ending in failure.",
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
        """Return the closed v1 workload object."""

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
class ObservedReferenceEnvironment:
    """Closed path-free host observations from the reference-profile tool."""

    os_family: str
    architecture: str
    logical_processors: int | None
    memory_bytes: int | None
    storage_capacity_bytes: int
    storage_free_bytes: int
    graphics: None
    storage_media: None

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ObservedReferenceEnvironment:
        expected = {
            "os_family",
            "architecture",
            "logical_processors",
            "memory_bytes",
            "storage_capacity_bytes",
            "storage_free_bytes",
            "graphics",
            "storage_media",
        }
        if set(value) != expected:
            _reject("M1_REFERENCE_INVALID", "Reference observations use an unexpected field set.")
        os_family = value["os_family"]
        architecture = value["architecture"]
        if type(os_family) is not str or type(architecture) is not str:
            _reject("M1_REFERENCE_INVALID", "Reference platform observations must be strings.")
        logical_processors = _optional_integer(value["logical_processors"], minimum=1)
        memory_bytes = _optional_integer(value["memory_bytes"])
        capacity = _exact_integer(value["storage_capacity_bytes"], "storage_capacity_bytes")
        free = _exact_integer(value["storage_free_bytes"], "storage_free_bytes")
        if free > capacity or value["graphics"] is not None or value["storage_media"] is not None:
            _reject("M1_REFERENCE_INVALID", "Reference observations violate the closed contract.")
        return cls(
            os_family=os_family,
            architecture=architecture,
            logical_processors=logical_processors,
            memory_bytes=memory_bytes,
            storage_capacity_bytes=capacity,
            storage_free_bytes=free,
            graphics=None,
            storage_media=None,
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "os_family": self.os_family,
            "architecture": self.architecture,
            "logical_processors": self.logical_processors,
            "memory_bytes": self.memory_bytes,
            "storage_capacity_bytes": self.storage_capacity_bytes,
            "storage_free_bytes": self.storage_free_bytes,
            "graphics": None,
            "storage_media": None,
        }


def _optional_integer(value: object, *, minimum: int = 0) -> int | None:
    if value is None:
        return None
    return _exact_integer(value, "optional observation", minimum=minimum)


ScalarObservation = int | str | None


@dataclass(frozen=True, slots=True)
class ReferenceRequirementMeasurement:
    """Typed representation of one existing reference-profile comparison."""

    requirement_id: RequirementId
    boundary: RequirementBoundary
    required_value: int | str
    actual_source: ActualValueSource
    actual_value: ScalarObservation
    verification: VerificationMethod
    status: RequirementStatus

    def __post_init__(self) -> None:
        if (
            type(self.requirement_id) is not RequirementId
            or type(self.boundary) is not RequirementBoundary
            or type(self.actual_source) is not ActualValueSource
            or type(self.verification) is not VerificationMethod
            or type(self.status) is not RequirementStatus
            or type(self.required_value) not in {int, str}
            or type(self.actual_value) not in {int, str, type(None)}
        ):
            _reject("M1_REFERENCE_INVALID", "A reference requirement uses an invalid value.")
        if type(self.required_value) is int:
            _exact_integer(self.required_value, "reference required value")
        if type(self.actual_value) is int:
            _exact_integer(self.actual_value, "reference actual value")

    def to_payload(self) -> dict[str, object]:
        return {
            self.boundary.value: self.required_value,
            self.actual_source.value: self.actual_value,
            "verification": self.verification.value,
            "status": self.status.value,
        }


@dataclass(frozen=True, slots=True)
class ReferenceEnvironmentMeasurement:
    """Host observations plus the unchanged existing profile evaluation."""

    observations: ObservedReferenceEnvironment
    attestations: ReferenceAttestations
    evaluation_status: ReferenceEvaluationStatus
    requirements: tuple[ReferenceRequirementMeasurement, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "observations": self.observations.to_payload(),
            "attestations": {
                "memory_mib": self.attestations.memory_mib,
                "graphics_class": self.attestations.graphics_class,
                "storage_media": self.attestations.storage_media,
            },
            "evaluation": {
                "status": self.evaluation_status.value,
                "requirements": {
                    item.requirement_id.value: item.to_payload() for item in self.requirements
                },
            },
        }


@dataclass(frozen=True, slots=True)
class M1BenchmarkReport:
    """A typed record-only report; it intentionally has no gate-pass field."""

    profile_id: str
    scenario_id: str
    reference_environment: ReferenceEnvironmentMeasurement
    vertical_slice: VerticalSliceMeasurement

    def __post_init__(self) -> None:
        if (
            type(self.profile_id) is not str
            or not self.profile_id
            or type(self.reference_environment) is not ReferenceEnvironmentMeasurement
            or type(self.vertical_slice) is not VerticalSliceMeasurement
        ):
            _reject("M1_REPORT_INVALID", "The report profile identity is invalid.")
        if self.scenario_id not in SCENARIO_IDS:
            _reject("M1_REPORT_INVALID", "The report scenario identity is invalid.")

    def to_payload(self) -> dict[str, object]:
        """Return only the supported public host-observation/non-run payload."""

        if self.vertical_slice.run_status is not VerticalSliceRunStatus.NOT_RUN:
            _reject(
                LIVE_RUNNER_UNAVAILABLE_CODE,
                "Completed or failed workload reports require the future direct live runner.",
            )
        return self._to_payload()

    def _to_payload(self) -> dict[str, object]:
        return {
            "report_schema_version": 1,
            "report_kind": "m1-vertical-slice-measurement",
            "profile_id": self.profile_id,
            "scenario_id": self.scenario_id,
            "measurement_policy": {"status": "record-only", "product_targets": []},
            "reference_environment": self.reference_environment.to_payload(),
            "vertical_slice": self.vertical_slice._to_payload(),
        }


def build_m1_benchmark_report(
    profile: Mapping[str, Any],
    scenario_id: str,
    work_directory: Path,
    *,
    attestations: ReferenceAttestations,
) -> M1BenchmarkReport:
    """Build the supported host-only report with an explicit non-run workload."""

    if scenario_id not in SCENARIO_IDS:
        _reject("M1_REPORT_INVALID", "The requested reference scenario is unknown.")
    _validate_attestations(attestations)
    try:
        work_directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        _reject("M1_OBSERVATION_FAILED", "The reference work directory is unavailable.")
    if not work_directory.is_dir():
        _reject("M1_OBSERVATION_FAILED", "The reference work directory is unavailable.")
    try:
        observed = ObservedReferenceEnvironment.from_mapping(
            observe_reference_environment(work_directory)
        )
        evaluation = evaluate_reference_environment(
            profile,
            scenario_id,
            observed.to_payload(),
            attestations,
        )
    except BenchmarkError:
        _reject("M1_OBSERVATION_FAILED", "Reference environment observation failed safely.")
    reference = _reference_measurement(observed, attestations, evaluation)
    profile_id = profile.get("profile_id")
    if type(profile_id) is not str:
        _reject("M1_REPORT_INVALID", "The reference profile identity is invalid.")
    return M1BenchmarkReport(
        profile_id,
        scenario_id,
        reference,
        VerticalSliceMeasurement(),
    )


def _validate_attestations(attestations: ReferenceAttestations) -> None:
    if type(attestations) is not ReferenceAttestations:
        _reject("M1_REFERENCE_INVALID", "Reference attestations use an invalid type.")
    if attestations.memory_mib is not None:
        _exact_integer(attestations.memory_mib, "attested memory", minimum=1)
    if attestations.graphics_class not in {None, "integrated"}:
        _reject("M1_REFERENCE_INVALID", "The graphics attestation is unsupported.")
    if attestations.storage_media not in {None, "hdd-or-validated-emulation"}:
        _reject("M1_REFERENCE_INVALID", "The storage-media attestation is unsupported.")


def _reference_measurement(
    observations: ObservedReferenceEnvironment,
    attestations: ReferenceAttestations,
    evaluation: Mapping[str, object],
) -> ReferenceEnvironmentMeasurement:
    try:
        status = ReferenceEvaluationStatus(cast(str, evaluation["status"]))
        raw_requirements = cast(Mapping[str, object], evaluation["requirements"])
    except KeyError, TypeError, ValueError:
        _reject("M1_REFERENCE_INVALID", "Reference evaluation uses an invalid result contract.")
    requirements: list[ReferenceRequirementMeasurement] = []
    for requirement_id in REQUIREMENT_ORDER:
        raw = raw_requirements.get(requirement_id.value)
        if raw is None:
            continue
        if not isinstance(raw, Mapping):
            _reject("M1_REFERENCE_INVALID", "A reference requirement uses an invalid contract.")
        requirements.append(_reference_requirement(requirement_id, cast(Mapping[str, object], raw)))
    if set(raw_requirements) != {item.requirement_id.value for item in requirements}:
        _reject("M1_REFERENCE_INVALID", "Reference evaluation contains an unknown requirement.")
    if status is ReferenceEvaluationStatus.NOT_EVALUATED and requirements:
        _reject(
            "M1_REFERENCE_INVALID", "A non-evaluated reference result cannot list requirements."
        )
    if status is not ReferenceEvaluationStatus.NOT_EVALUATED and not requirements:
        _reject("M1_REFERENCE_INVALID", "An evaluated reference result must list requirements.")
    return ReferenceEnvironmentMeasurement(observations, attestations, status, tuple(requirements))


def _reference_requirement(
    requirement_id: RequirementId,
    raw: Mapping[str, object],
) -> ReferenceRequirementMeasurement:
    boundary_keys = [item for item in RequirementBoundary if item.value in raw]
    source_keys = [item for item in ActualValueSource if item.value in raw]
    if (
        len(boundary_keys) != 1
        or len(source_keys) != 1
        or set(raw)
        != {
            boundary_keys[0].value,
            source_keys[0].value,
            "verification",
            "status",
        }
    ):
        _reject("M1_REFERENCE_INVALID", "A reference requirement uses an invalid field set.")
    required_value = raw[boundary_keys[0].value]
    actual_value = raw[source_keys[0].value]
    if type(required_value) not in {int, str} or type(actual_value) not in {int, str, type(None)}:
        _reject("M1_REFERENCE_INVALID", "A reference requirement uses an invalid scalar value.")
    try:
        verification = VerificationMethod(cast(str, raw["verification"]))
        status = RequirementStatus(cast(str, raw["status"]))
    except TypeError, ValueError:
        _reject("M1_REFERENCE_INVALID", "A reference requirement uses an invalid status.")
    return ReferenceRequirementMeasurement(
        requirement_id=requirement_id,
        boundary=boundary_keys[0],
        required_value=cast(int | str, required_value),
        actual_source=source_keys[0],
        actual_value=cast(ScalarObservation, actual_value),
        verification=verification,
        status=status,
    )


def encode_m1_benchmark_report(report: M1BenchmarkReport) -> bytes:
    """Encode only the supported host-observation/non-run report."""

    if (
        type(report) is not M1BenchmarkReport
        or report.vertical_slice.run_status is not VerticalSliceRunStatus.NOT_RUN
    ):
        _reject(
            LIVE_RUNNER_UNAVAILABLE_CODE,
            "Completed or failed workload reports require the future direct live runner.",
        )
    encoded = _canonical_report_bytes(report)
    decoded, issues = decode_m1_benchmark_report(encoded)
    if issues or decoded is None:
        _reject("M1_REPORT_INVALID", "The constructed measurement report failed validation.")
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
) -> tuple[M1BenchmarkReport | None, list[BenchmarkIssue]]:
    """Validate a bounded closed report without attesting who produced it.

    Decoding supports the closed completed/failed shapes needed by the future
    runner, but the current public serializer still refuses those states.
    """

    loaded, issues = _load_report_object(payload)
    schema, schema_issues = _load_schema(root)
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
                "report does not match the closed v1 schema",
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
                "report bytes do not use the canonical v1 encoding",
            )
        ]
    return report, []


def _load_report_object(payload: bytes) -> tuple[dict[str, Any] | None, list[BenchmarkIssue]]:
    if not 1 <= len(payload) <= MAX_REPORT_BYTES:
        return None, [
            BenchmarkIssue("M1_REPORT_SIZE_INVALID", "$", "report exceeds the bounded v1 size")
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


def _load_schema(root: Path) -> tuple[dict[str, Any] | None, list[BenchmarkIssue]]:
    path = root / REPORT_SCHEMA_PATH
    try:
        payload = path.read_bytes()
    except OSError:
        return None, [
            BenchmarkIssue(
                "M1_REPORT_SCHEMA_UNREADABLE",
                REPORT_SCHEMA_PATH.as_posix(),
                "schema is unavailable",
            )
        ]
    if not 1 <= len(payload) <= MAX_SCHEMA_BYTES:
        return None, [
            BenchmarkIssue(
                "M1_REPORT_SCHEMA_SIZE_INVALID",
                REPORT_SCHEMA_PATH.as_posix(),
                "schema size is invalid",
            )
        ]
    loaded, issues = _load_report_object(payload)
    if issues or loaded is None:
        return None, [
            BenchmarkIssue(
                "M1_REPORT_SCHEMA_JSON_INVALID",
                REPORT_SCHEMA_PATH.as_posix(),
                "schema is not strict JSON",
            )
        ]
    try:
        Draft202012Validator.check_schema(loaded)
    except SchemaError:
        return None, [
            BenchmarkIssue(
                "M1_REPORT_SCHEMA_DEFINITION_INVALID",
                REPORT_SCHEMA_PATH.as_posix(),
                "schema is not valid Draft 2020-12",
            )
        ]
    return loaded, []


def validate_report_schema(root: Path = PROJECT_ROOT) -> list[BenchmarkIssue]:
    """Validate the bounded self-contained report schema."""

    _, issues = _load_schema(root)
    return issues


def _report_from_payload(value: Mapping[str, Any], root: Path) -> M1BenchmarkReport:
    reference = cast(Mapping[str, Any], value["reference_environment"])
    observations = ObservedReferenceEnvironment.from_mapping(
        cast(Mapping[str, object], reference["observations"])
    )
    raw_attestations = cast(Mapping[str, object], reference["attestations"])
    attestations = ReferenceAttestations(
        memory_mib=cast(int | None, raw_attestations["memory_mib"]),
        graphics_class=cast(str | None, raw_attestations["graphics_class"]),
        storage_media=cast(str | None, raw_attestations["storage_media"]),
    )
    _validate_attestations(attestations)
    reference_measurement = _reference_measurement(
        observations,
        attestations,
        cast(Mapping[str, object], reference["evaluation"]),
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
    result_code = vertical["result_code"]
    measurement = VerticalSliceMeasurement._from_decoded(
        run_status=VerticalSliceRunStatus(cast(str, vertical["run_status"])),
        result_code=cast(str | None, result_code),
        phase_measurements=phase_measurements,
        worker_resource_measurements=worker_measurements,
        workspace_usage_measurements=workspace_measurements,
        completed_evidence=completed_evidence,
    )
    report = M1BenchmarkReport(
        cast(str, value["profile_id"]),
        cast(str, value["scenario_id"]),
        reference_measurement,
        measurement,
    )
    if measurement.completed_evidence is not None:
        evidence = measurement.completed_evidence
        if evidence.reference_profile_sha256 != _checked_file_sha256(
            root, PROFILE_PATH, context="reference profile"
        ) or evidence.report_schema_sha256 != _checked_file_sha256(
            root, REPORT_SCHEMA_PATH, context="report schema"
        ):
            _reject(
                "M1_IDENTITY_INVALID",
                "Completed evidence is not bound to the checked-in profile and report schema.",
            )
    expected = evaluate_reference_environment(
        _profile_for_decode(report.profile_id, root),
        report.scenario_id,
        observations.to_payload(),
        attestations,
    )
    if reference_measurement.to_payload()["evaluation"] != expected:
        _reject("M1_REFERENCE_INVALID", "Reference evaluation does not match the pinned evaluator.")
    return report


def _profile_for_decode(profile_id: str, root: Path) -> Mapping[str, Any]:
    profile, issues = validate_profile(root)
    if issues or profile is None or profile.get("profile_id") != profile_id:
        _reject(
            "M1_REFERENCE_INVALID", "The report is not bound to the checked-in reference profile."
        )
    return profile


def _checked_file_sha256(root: Path, relative_path: Path, *, context: str) -> str:
    try:
        payload = (root / relative_path).read_bytes()
    except OSError:
        _reject("M1_IDENTITY_INVALID", f"The checked-in {context} identity is unavailable.")
    if not 1 <= len(payload) <= MAX_SCHEMA_BYTES:
        _reject("M1_IDENTITY_INVALID", f"The checked-in {context} identity is invalid.")
    return hashlib.sha256(payload).hexdigest()


def _json_path(parts: Sequence[object]) -> str:
    rendered = "$"
    for part in parts:
        rendered += f"[{part}]" if isinstance(part, int) else f".{part}"
    return rendered


def main(argv: Sequence[str] | None = None) -> int:
    """Validate the schema or print an honest host-only observation report."""

    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="validate the report schema")
    observe = subparsers.add_parser(
        "observe-host",
        help="record host/reference observations without running the vertical slice",
    )
    observe.add_argument("--scenario", choices=sorted(SCENARIO_IDS), default="limited-storage")
    observe.add_argument(
        "--work-directory",
        type=Path,
        default=PROJECT_ROOT / ".paperworkshell-local" / "m1-benchmark-work",
    )
    observe.add_argument("--attest-memory-mib", type=int)
    observe.add_argument("--attest-graphics", choices=("integrated",))
    observe.add_argument(
        "--attest-storage-media",
        choices=("hdd-or-validated-emulation",),
    )
    arguments = parser.parse_args(argv)

    if arguments.command == "validate":
        issues = validate_report_schema()
        if issues:
            for issue in issues:
                print(issue.render(), file=sys.stderr)
            return 1
        print(f"OK: validated record-only M1 report schema at {REPORT_SCHEMA_PATH.as_posix()}.")
        return 0

    profile, profile_issues = validate_profile()
    if profile_issues or profile is None:
        print(
            "ERROR [M1_REFERENCE_INVALID] The checked-in reference profile is invalid.",
            file=sys.stderr,
        )
        return 1
    try:
        report = build_m1_benchmark_report(
            profile,
            cast(str, arguments.scenario),
            cast(Path, arguments.work_directory),
            attestations=ReferenceAttestations(
                memory_mib=cast(int | None, arguments.attest_memory_mib),
                graphics_class=cast(str | None, arguments.attest_graphics),
                storage_media=cast(str | None, arguments.attest_storage_media),
            ),
        )
        encoded = encode_m1_benchmark_report(report)
    except M1BenchmarkError as error:
        print(f"ERROR [{error.code}] {error.safe_message}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

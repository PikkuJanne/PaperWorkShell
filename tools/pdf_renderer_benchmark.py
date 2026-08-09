"""Run the versioned, synthetic-only PWS-0106 Windows renderer comparison.

The public command is a parent-process harness.  Renderer libraries are imported only by
short-lived child processes, each assigned to a kill-on-close Windows Job Object.  Reports contain
only versioned synthetic identifiers, hashes, stable outcome codes, and numeric measurements.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import csv
import ctypes
import hashlib
import importlib
import importlib.metadata
import importlib.util
import io
import json
import math
import os
import platform
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path, PurePosixPath
from typing import Any, Final, Literal, NoReturn, Protocol, cast
from uuid import uuid4

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
WORKLOAD_PATH: Final = Path("benchmarks/pdf-renderer-windows-v1.json")
MAX_JSON_BYTES: Final = 2 * 1024 * 1024
MAX_RENDER_OUTPUT_BYTES: Final = 64 * 1024 * 1024
SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}\Z")
CORRELATION_PATTERN: Final = re.compile(r"[0-9a-f]{32}\Z")
DRIVE_PATH_PATTERN: Final = re.compile(r"(?i)(?:^|[^a-z0-9])[a-z]:[\\/]")
EXPECTED_FIDELITY_CASE_IDS: Final = (
    "render-annotation-appearance",
    "render-calibration-crop-rotate",
    "render-calibration-media-box",
    "render-embedded-type3-font",
    "render-geometry-additional-rotate-180",
    "render-geometry-additional-rotate-270",
    "render-geometry-additional-rotate-90",
    "render-geometry-direct-box",
    "render-geometry-inherited-rotate-0",
    "render-geometry-inherited-rotate-180",
    "render-geometry-inherited-rotate-270",
    "render-geometry-inherited-rotate-90",
    "render-geometry-user-unit",
    "render-soft-mask-blend",
    "render-transparency",
)
EXPECTED_OUTCOME_CASES: Final = {
    "malformed-truncated": ("pdf-malformed", "PDF_RENDER_MALFORMED"),
    "malformed-page-tree": ("pdf-render-malformed-page-tree", "PDF_RENDER_MALFORMED"),
    "declared-high-dimension": (
        "pdf-render-high-dimension",
        "PDF_RENDER_LIMIT_EXCEEDED",
    ),
}
EXPECTED_SEMANTIC_CASES: Final = {
    "missing-font-fallback": (
        "pdf-render-missing-font-fallback",
        "PDF_RENDER_OK",
        "bounded-opaque-rgba8",
        0,
        "crop",
        "default-equivalent",
        0,
        128,
        64,
        128,
        64,
        128,
        64,
    ),
    "media-box-aspect-fit": (
        "pdf-render-calibration",
        "PDF_RENDER_OK",
        "bounded-opaque-rgba8-aspect-fit",
        0,
        "media",
        "distinct-from-crop",
        0,
        60,
        80,
        101,
        101,
        76,
        101,
    ),
}
EXPECTED_RENDERERS: Final = {
    "pypdfium2": (
        "pypdfium2",
        "5.12.1",
        "PDFium",
        "152.0.7947.0",
        "9609be73a6701a68f29dffe0335f7a2e4b3ba581542ed65d35d49f761a4600ca",
    ),
    "qt-pdf": (
        "PySide6-Addons",
        "6.11.1",
        "Qt PDF",
        "6.11.1",
        "0d13c4dfd671b050a48e4f8d8ddc724b7248f9c0437e7fc47fdf316278572923",
    ),
}
RENDERER_IDS: Final = tuple(EXPECTED_RENDERERS)
OUTCOME_DEPENDENCY_UNAVAILABLE: Final = "PDF_RENDER_BENCHMARK_DEPENDENCY_UNAVAILABLE"
OUTCOME_DEPENDENCY_MISMATCH: Final = "PDF_RENDER_BENCHMARK_DEPENDENCY_MISMATCH"
OUTCOME_CHILD_FAILED: Final = "PDF_RENDER_BENCHMARK_CHILD_FAILED"
OUTCOME_CHILD_TIMEOUT: Final = "PDF_RENDER_BENCHMARK_CHILD_TIMEOUT"
OUTCOME_PROTOCOL_INVALID: Final = "PDF_RENDER_BENCHMARK_PROTOCOL_INVALID"
OUTCOME_TEMP_MEASUREMENT_FAILED: Final = "PDF_RENDER_BENCHMARK_TEMP_MEASUREMENT_FAILED"
OUTCOME_PLATFORM_UNSUPPORTED: Final = "PDF_RENDER_BENCHMARK_PLATFORM_UNSUPPORTED"
OUTCOME_REPOSITORY_DIRTY: Final = "PDF_RENDER_BENCHMARK_REPOSITORY_DIRTY"
OUTCOME_CLEANUP_FAILED: Final = "PDF_RENDER_BENCHMARK_CLEANUP_FAILED"
OUTCOME_CANCELLED: Final = "PDF_RENDER_CANCELLED"
OUTCOME_JOB_TERMINATED: Final = "PDF_RENDER_JOB_TERMINATED"
OUTCOME_PAGE_BOX_UNSUPPORTED: Final = "PDF_RENDER_PAGE_BOX_UNSUPPORTED"
OUTCOME_BENCHMARK_FAILED: Final = "PDF_RENDER_BENCHMARK_FAILED"
STABLE_RENDER_OUTCOME_CODES: Final = frozenset(
    {
        "PDF_RENDER_OK",
        "PDF_RENDER_MALFORMED",
        "PDF_RENDER_PASSWORD_REQUIRED",
        "PDF_RENDER_LIMIT_EXCEEDED",
        "PDF_RENDER_GEOMETRY_INVALID",
        "PDF_RENDER_RESULT_INVALID",
        "PDF_RENDER_DEPENDENCY_FAILED",
        "PDF_RENDER_UNEXPECTED_SUCCESS",
        "PDF_RENDER_BENCHMARK_GOLDEN_UNREADABLE",
        "PDF_RENDER_BENCHMARK_GOLDEN_CHANGED",
        "PDF_RENDER_BENCHMARK_GOLDEN_INVALID",
        OUTCOME_PAGE_BOX_UNSUPPORTED,
    }
)
CHILD_ERROR_MESSAGES: Final[Mapping[str, frozenset[str]]] = {
    "PDF_RENDER_BENCHMARK_ISOLATION_INVALID": frozenset(
        {"the isolated renderer boundary is invalid"}
    ),
    "PDF_RENDER_BENCHMARK_STAGE_INVALID": frozenset({"the staged benchmark resources are invalid"}),
    "PDF_RENDER_BENCHMARK_RUNTIME_INVALID": frozenset({"the staged benchmark runtime is invalid"}),
    "PDF_RENDER_BENCHMARK_HARNESS_INVALID": frozenset({"the staged benchmark harness is invalid"}),
    "PDF_RENDER_BENCHMARK_WORKLOAD_INVALID": frozenset(
        {"the versioned renderer workload is invalid"}
    ),
    OUTCOME_DEPENDENCY_UNAVAILABLE: frozenset({"a required renderer dependency is unavailable"}),
    OUTCOME_DEPENDENCY_MISMATCH: frozenset(
        {
            "a renderer dependency does not match the versioned workload",
            "a renderer dependency has no installed artifact record",
        }
    ),
    OUTCOME_PROTOCOL_INVALID: frozenset(
        {
            "renderer ID is invalid",
            "cancel command is invalid",
            "child mode is invalid",
        }
    ),
    OUTCOME_TEMP_MEASUREMENT_FAILED: frozenset(
        {
            "isolated temporary-storage measurement failed",
            "isolated temporary-storage root is invalid",
            "isolated temporary-storage root is missing",
            "isolated temporary-storage root is relative",
            "isolated temporary-storage root is unavailable",
            "isolated temporary-storage root access was denied",
            "isolated temporary-storage root does not exist",
            "isolated temporary-storage root is redirected",
            "isolated temporary-storage entry is unavailable",
            "isolated temporary-storage entry policy is invalid",
            "isolated temporary-storage budget was exceeded",
            "isolated temporary-storage entry type is invalid",
            "isolated temporary-storage enumeration failed",
        }
    ),
    OUTCOME_CHILD_FAILED: frozenset({"hard-termination child survived", "renderer child failed"}),
}
PUBLIC_FAILURE_MESSAGES: Final[Mapping[str, str]] = {
    OUTCOME_DEPENDENCY_UNAVAILABLE: "a required renderer dependency is unavailable",
    OUTCOME_DEPENDENCY_MISMATCH: "a renderer dependency is mismatched",
    OUTCOME_CHILD_FAILED: "renderer child failed",
    OUTCOME_CHILD_TIMEOUT: "renderer child timed out",
    OUTCOME_PROTOCOL_INVALID: "renderer child protocol is invalid",
    OUTCOME_PLATFORM_UNSUPPORTED: "the renderer comparison requires Windows",
    OUTCOME_REPOSITORY_DIRTY: "the benchmark requires a clean repository tree",
    OUTCOME_CLEANUP_FAILED: "benchmark cleanup failed",
    "PDF_RENDER_BENCHMARK_REFERENCE_INVALID": "the reference profile is invalid",
    "PDF_RENDER_BENCHMARK_REFERENCE_MISMATCH": (
        "the selected environment does not match the reference profile"
    ),
    "PDF_RENDER_BENCHMARK_SCENARIO_INVALID": "the reference scenario is invalid",
    "PDF_RENDER_BENCHMARK_TOOL_UNAVAILABLE": "a required benchmark tool is unavailable",
    "PDF_RENDER_BENCHMARK_TOOL_FAILED": "a required benchmark tool failed",
    "PDF_RENDER_BENCHMARK_WORK_SCOPE_INVALID": "the benchmark work scope is invalid",
    "PDF_RENDER_BENCHMARK_WORK_SCOPE_FAILED": "the benchmark work scope failed",
}
PROCESS_MODULE_STARTED_NS: Final = time.perf_counter_ns()
BENCHMARK_STAGE_PREFIX: Final = "pws-render-benchmark-stage-"
BENCHMARK_BOOTSTRAP_PATH: Final = Path("tools/pdf_renderer_benchmark_bootstrap.py")
BENCHMARK_STAGE_MANIFEST: Final = PurePosixPath("code/benchmark-stage-manifest-v1.json")
MAX_STAGE_ENTRIES: Final = 12_000
MAX_STAGE_DEPTH: Final = 64
MAX_STAGE_BYTES: Final = 768 * 1024 * 1024
MAX_STAGE_FILE_BYTES: Final = 256 * 1024 * 1024
MAX_TEMP_ENTRIES: Final = 4_096
MAX_TEMP_DEPTH: Final = 32
MAX_TEMP_SCAN_ATTEMPTS: Final = 3
STAGED_DISTRIBUTIONS: Final = (
    "pypdfium2",
    "PySide6-Addons",
    "PySide6-Essentials",
    "shiboken6",
)


@dataclass(frozen=True, order=True)
class BenchmarkIssue:
    """One stable workload-validation issue."""

    code: str
    location: str
    message: str

    def render(self) -> str:
        return f"ERROR [{self.code}] {self.location}: {self.message}"


class BenchmarkFailure(Exception):
    """An expected benchmark failure with a stable, content-free outcome."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.cleanup_failed = False

    def note_cleanup_failure(self) -> None:
        """Attach stable secondary cleanup evidence without replacing the primary failure."""

        self.cleanup_failed = True


def _propagate_cleanup_failure(primary_failure: BaseException | None) -> None:
    """Preserve an expected primary or sanitize an unexpected primary with cleanup evidence."""

    if isinstance(primary_failure, BenchmarkFailure):
        primary_failure.note_cleanup_failure()
        return
    if isinstance(primary_failure, Exception):
        failure = BenchmarkFailure(
            OUTCOME_BENCHMARK_FAILED,
            "renderer benchmark failed",
        )
        failure.note_cleanup_failure()
        raise failure from primary_failure
    if primary_failure is None:
        raise BenchmarkFailure(
            OUTCOME_CLEANUP_FAILED,
            "benchmark cleanup failed",
        ) from None


class DuplicateJsonKeyError(ValueError):
    """Raised when strict JSON input repeats a member name."""


class NonStandardJsonConstantError(ValueError):
    """Raised when strict JSON input contains NaN or Infinity."""


class _StableEngineError(Exception):
    """An internal renderer failure already reduced to a stable code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class FidelityCaseRecord:
    case_id: str
    fixture_id: str
    source_path: Path
    source_sha256: str
    golden_path: Path
    golden_sha256: str
    page_index: int
    page_box: str
    page_box_relation: str
    additional_rotation_degrees: int
    oriented_width_units: float
    oriented_height_units: float
    maximum_width_pixels: int
    maximum_height_pixels: int
    width_pixels: int
    height_pixels: int
    maximum_channel_delta: int

    def __repr__(self) -> str:
        return (
            "FidelityCaseRecord("
            f"case_id={self.case_id!r}, fixture_id={self.fixture_id!r}, "
            f"page_index={self.page_index}, page_box={self.page_box!r}, "
            f"maximum_width_pixels={self.maximum_width_pixels}, "
            f"maximum_height_pixels={self.maximum_height_pixels}, "
            f"width_pixels={self.width_pixels}, height_pixels={self.height_pixels})"
        )


@dataclass(frozen=True, slots=True)
class OutcomeCaseRecord:
    case_id: str
    fixture_id: str
    source_path: Path
    source_sha256: str
    page_index: int
    maximum_width_pixels: int
    maximum_height_pixels: int
    expected_code: str

    def __repr__(self) -> str:
        return (
            "OutcomeCaseRecord("
            f"case_id={self.case_id!r}, fixture_id={self.fixture_id!r}, "
            f"expected_code={self.expected_code!r})"
        )


@dataclass(frozen=True, slots=True)
class SemanticCaseRecord:
    case_id: str
    fixture_id: str
    source_path: Path
    source_sha256: str
    page_index: int
    page_box: str
    page_box_relation: str
    additional_rotation_degrees: int
    oriented_width_units: float
    oriented_height_units: float
    maximum_width_pixels: int
    maximum_height_pixels: int
    width_pixels: int
    height_pixels: int
    expected_code: str
    oracle: str

    def __repr__(self) -> str:
        return (
            "SemanticCaseRecord("
            f"case_id={self.case_id!r}, fixture_id={self.fixture_id!r}, "
            f"expected_code={self.expected_code!r}, oracle={self.oracle!r})"
        )


@dataclass(frozen=True, slots=True)
class RendererSpec:
    renderer_id: str
    package: str
    package_version: str
    engine: str
    engine_version: str
    artifact_sha256: str
    engine_feature_flags: tuple[str, ...] | str


@dataclass(frozen=True, slots=True)
class BenchmarkMeasurementPolicy:
    warm_iterations: int
    child_timeout_milliseconds: int
    cooperative_cancel_timeout_milliseconds: int
    hard_termination_timeout_milliseconds: int
    private_bytes_poll_milliseconds: int


@dataclass(frozen=True, slots=True)
class BenchmarkWorkload:
    workload_id: str
    workload_sha256: str
    reference_profile_id: str
    reference_profile_sha256: str
    reference_logical_processors: int
    reference_memory_bytes: int
    fixture_index_sha256: str
    golden_index_sha256: str
    render_profile_id: str
    maximum_width_pixels: int
    maximum_height_pixels: int
    maximum_pixels: int
    maximum_output_bytes: int
    fidelity_cases: tuple[FidelityCaseRecord, ...]
    semantic_cases: tuple[SemanticCaseRecord, ...]
    outcome_cases: tuple[OutcomeCaseRecord, ...]
    renderers: tuple[RendererSpec, ...]
    measurement: BenchmarkMeasurementPolicy


@dataclass(frozen=True, slots=True)
class ChildCapture:
    payload: Mapping[str, object]
    correlation_id: str
    isolated_worker_ready_elapsed_ns: int
    parent_observed_elapsed_ns: int
    process_affinity_count: int
    process_memory_limit_bytes: int
    job_memory_limit_bytes: int
    peak_process_memory_used_bytes: int
    peak_job_memory_used_bytes: int


@dataclass(frozen=True, slots=True)
class StagedBenchmarkResources:
    """One immutable, bounded worker runtime copied off the live repository tree."""

    stage_root: Path
    runtime_root: Path
    code_root: Path
    data_root: Path
    python_executable: Path
    bootstrap_script: Path
    manifest_path: Path
    manifest_sha256: str
    cleanup_token: str = dataclass_field(repr=False)
    staged_file_sha256: tuple[tuple[PurePosixPath, str], ...] = dataclass_field(repr=False)
    installed_record_sha256: tuple[tuple[str, str], ...] = dataclass_field(
        default=(),
        repr=False,
    )

    def __repr__(self) -> str:
        return "StagedBenchmarkResources(<immutable fixed-drive resources>)"


@dataclass(slots=True)
class _StageBudget:
    entries: int = 0
    total_bytes: int = 0

    def observe_directory(self) -> None:
        self.entries += 1
        if self.entries > MAX_STAGE_ENTRIES:
            raise BenchmarkFailure(OUTCOME_CHILD_FAILED, "benchmark stage is too large")

    def observe_file(self, size: int) -> None:
        if not 0 <= size <= MAX_STAGE_FILE_BYTES:
            raise BenchmarkFailure(OUTCOME_CHILD_FAILED, "benchmark stage is too large")
        self.entries += 1
        self.total_bytes += size
        if self.entries > MAX_STAGE_ENTRIES or self.total_bytes > MAX_STAGE_BYTES:
            raise BenchmarkFailure(OUTCOME_CHILD_FAILED, "benchmark stage is too large")


@dataclass(slots=True, eq=False)
class _StageAuthority:
    cleanup_token: str = dataclass_field(repr=False)
    parent: Path = dataclass_field(repr=False)
    tree: Any = dataclass_field(repr=False)
    cleanup_authorized: bool = dataclass_field(default=True, repr=False)
    state_lock: threading.Lock = dataclass_field(
        default_factory=threading.Lock,
        repr=False,
    )
    expected_files: dict[PurePosixPath, str] | None = dataclass_field(
        default=None,
        repr=False,
    )


_STAGE_AUTHORITIES: dict[Path, _StageAuthority] = {}
_STAGE_AUTHORITIES_LOCK: Final = threading.Lock()


class RendererRuntime(Protocol):
    """Private benchmark-only adapter implemented by each child runtime."""

    identity: Mapping[str, object]

    def open_document(self, source: Path) -> object: ...

    def close_document(self, document: object) -> None: ...

    def render_page(self, document: object, case: FidelityCaseRecord) -> bytes: ...

    def outcome_code(self, case: OutcomeCaseRecord) -> str: ...


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError(key)
        result[key] = value
    return result


def _reject_json_constant(value: str) -> NoReturn:
    raise NonStandardJsonConstantError(value)


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _raise_stage_failure(message: str) -> NoReturn:
    raise BenchmarkFailure(OUTCOME_CHILD_FAILED, message) from None


class _StageStreamContext:
    """Close a staged file handle without replacing an active primary failure."""

    def __init__(self, stream: Any, failure_message: str) -> None:
        self._stream = stream
        self._failure_message = failure_message

    def __enter__(self) -> Any:
        return self._stream

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: object,
    ) -> Literal[False]:
        try:
            self._stream.close()
        except OSError:
            if exception_type is None:
                _raise_stage_failure(self._failure_message)
        return False


class _RepositoryInputStreamContext:
    """Close an input handle without replacing an active validation failure."""

    def __init__(self, stream: Any) -> None:
        self._stream = stream

    def __enter__(self) -> Any:
        return self._stream

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: object,
    ) -> Literal[False]:
        try:
            self._stream.close()
        except OSError:
            if exception_type is None:
                raise BenchmarkFailure(
                    "PDF_RENDER_BENCHMARK_INPUT_UNREADABLE",
                    "repository input could not be read",
                ) from None
        return False


def _same_file_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        left.st_mode,
        left.st_size,
        left.st_mtime_ns,
        left.st_ctime_ns,
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_mode,
        right.st_size,
        right.st_mtime_ns,
        right.st_ctime_ns,
    )


def _opened_handle_matches_path(opened: os.stat_result, current: os.stat_result) -> bool:
    """Compare stable Windows handle/path fields; fstat ctime/mode encodings differ."""

    return (
        opened.st_dev,
        opened.st_ino,
        opened.st_size,
        opened.st_mtime_ns,
    ) == (
        current.st_dev,
        current.st_ino,
        current.st_size,
        current.st_mtime_ns,
    )


def _require_fixed_drive(path: Path) -> Path:
    """Resolve a local directory and reject removable, remote, or redirected storage."""

    if os.name != "nt":
        raise BenchmarkFailure(
            OUTCOME_PLATFORM_UNSUPPORTED,
            "the renderer comparison requires Windows",
        ) from None
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_dir() or not resolved.anchor:
            _raise_stage_failure("benchmark stage is unavailable")
        current = resolved
        while True:
            if _is_reparse_point(current):
                _raise_stage_failure("benchmark stage is unavailable")
            if current.parent == current:
                break
            current = current.parent
        get_drive_type = ctypes.WinDLL("kernel32", use_last_error=True).GetDriveTypeW
        get_drive_type.argtypes = (ctypes.c_wchar_p,)
        get_drive_type.restype = ctypes.c_uint
        drive_type = int(get_drive_type(resolved.anchor))
    except BenchmarkFailure:
        raise
    except AttributeError, OSError:
        _raise_stage_failure("benchmark stage is unavailable")
    if drive_type != 3:  # DRIVE_FIXED
        _raise_stage_failure("benchmark stage is unavailable")
    return resolved


def create_benchmark_stage_root(work_directory: Path) -> Path:
    """Create and register one random stage on the approved fixed work drive."""

    parent = _require_fixed_drive(work_directory)
    created: Path | None = None
    tree: Any | None = None
    try:
        created = Path(tempfile.mkdtemp(prefix=BENCHMARK_STAGE_PREFIX, dir=parent))
        resolved = created.resolve(strict=True)
        identity = resolved.lstat()
        if (
            resolved.parent != parent
            or not resolved.is_dir()
            or _is_reparse_point(resolved)
            or not stat.S_ISDIR(identity.st_mode)
        ):
            _raise_stage_failure("benchmark stage is unavailable")
        from tools.pdf_renderer_stage_windows import PinnedStageTree

        tree = PinnedStageTree.open(resolved)
        authority = _StageAuthority(
            cleanup_token=secrets.token_hex(32),
            parent=parent,
            tree=tree,
        )
        with _STAGE_AUTHORITIES_LOCK:
            if resolved in _STAGE_AUTHORITIES:
                _raise_stage_failure("benchmark stage authority is invalid")
            _STAGE_AUTHORITIES[resolved] = authority
        return resolved
    except BenchmarkFailure as error:
        if tree is not None:
            try:
                tree.delete(None, MAX_STAGE_FILE_BYTES)
            except BaseException:
                error.note_cleanup_failure()
        elif created is not None:
            error.note_cleanup_failure()
        raise
    except Exception as cause:
        failure = BenchmarkFailure(
            OUTCOME_CHILD_FAILED,
            "benchmark stage could not be created",
        )
        if tree is not None:
            try:
                tree.delete(None, MAX_STAGE_FILE_BYTES)
            except BaseException:
                failure.note_cleanup_failure()
        elif created is not None and not bool(getattr(cause, "root_cleanup_completed", False)):
            failure.note_cleanup_failure()
        raise failure from cause


def _registered_stage_entry(stage_root: Path) -> tuple[Path, _StageAuthority]:
    """Look up exact in-process authority without touching the filesystem or stage tree."""

    if not isinstance(stage_root, Path) or not stage_root.is_absolute():
        _raise_stage_failure("benchmark stage authority is invalid")
    root = Path(os.path.normpath(os.path.abspath(stage_root)))
    with _STAGE_AUTHORITIES_LOCK:
        authority = _STAGE_AUTHORITIES.get(root)
    if authority is None or root.parent != authority.parent:
        _raise_stage_failure("benchmark stage authority is invalid")
    return root, authority


def _registered_stage_authority(stage_root: Path) -> tuple[Path, _StageAuthority]:
    root, authority = _registered_stage_entry(stage_root)
    try:
        authority.tree.require_live()
    except Exception:
        _raise_stage_failure("benchmark stage authority is invalid")
    return root, authority


def _source_stream_identity(source: Path, stream: Any) -> os.stat_result:
    try:
        opened = os.fstat(stream.fileno())
        current = source.lstat()
    except OSError:
        _raise_stage_failure("benchmark stage source is unavailable")
    if (
        not stat.S_ISREG(opened.st_mode)
        or _is_reparse_point(source)
        or not _opened_handle_matches_path(opened, current)
    ):
        _raise_stage_failure("benchmark stage source is invalid")
    return opened


def _stage_copy_file(
    source: Path,
    destination: Path,
    stage_root: Path,
    budget: _StageBudget,
    staged: dict[PurePosixPath, str],
) -> None:
    root, authority = _registered_stage_authority(stage_root)
    try:
        relative = PurePosixPath(destination.relative_to(root).as_posix())
    except ValueError:
        _raise_stage_failure("benchmark stage scope is invalid")
    if (
        relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
        or not relative.parts
    ):
        _raise_stage_failure("benchmark stage scope is invalid")
    try:
        authority.tree.ensure_directory(PurePosixPath(*relative.parts[:-1]))
        copied, source_sha256 = authority.tree.copy_file(
            source,
            relative,
            maximum_bytes=MAX_STAGE_FILE_BYTES,
        )
    except Exception:
        _raise_stage_failure("benchmark stage copy failed")
    budget.observe_file(copied)
    existing = staged.get(relative)
    if existing is not None and existing != source_sha256:
        _raise_stage_failure("benchmark stage collision is invalid")
    staged[relative] = source_sha256


def _read_git_blob(
    revision: str,
    relative: PurePosixPath,
    *,
    maximum_bytes: int,
    repository_root: Path = PROJECT_ROOT,
) -> bytes:
    """Read one exact committed blob with fixed bounded Git commands."""

    if (
        not isinstance(revision, str)
        or re.fullmatch(r"[0-9a-f]{40,64}", revision) is None
        or type(relative) is not PurePosixPath
        or relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or "\\" in relative.as_posix()
        or type(maximum_bytes) is not int
        or not 0 <= maximum_bytes <= MAX_STAGE_FILE_BYTES
    ):
        raise BenchmarkFailure(
            OUTCOME_REPOSITORY_DIRTY,
            "the benchmark requires a clean repository tree",
        )
    git = shutil.which("git")
    if git is None:
        raise BenchmarkFailure(
            "PDF_RENDER_BENCHMARK_TOOL_UNAVAILABLE",
            "Git is required to bind the benchmark revision",
        )
    object_name = f"{revision}:{relative.as_posix()}"
    try:
        size_result = subprocess.run(  # noqa: S603 - fixed Git executable and argv
            (git, "--no-replace-objects", "cat-file", "-s", object_name),
            cwd=repository_root,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
        if (
            size_result.returncode != 0
            or len(size_result.stdout) > 32
            or re.fullmatch(rb"0|[1-9][0-9]*\r?\n?", size_result.stdout) is None
        ):
            raise ValueError
        size = int(size_result.stdout.strip())
        if size > maximum_bytes:
            raise ValueError
        blob_result = subprocess.run(  # noqa: S603 - fixed Git executable and argv
            (git, "--no-replace-objects", "cat-file", "blob", object_name),
            cwd=repository_root,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        raise BenchmarkFailure(
            OUTCOME_REPOSITORY_DIRTY,
            "the benchmark requires a clean repository tree",
        ) from error
    if blob_result.returncode != 0 or len(blob_result.stdout) != size:
        raise BenchmarkFailure(
            OUTCOME_REPOSITORY_DIRTY,
            "the benchmark requires a clean repository tree",
        )
    return blob_result.stdout


def _stage_git_file(
    revision: str,
    source: Path,
    destination: Path,
    stage_root: Path,
    budget: _StageBudget,
    staged: dict[PurePosixPath, str],
) -> None:
    """Stage exact committed bytes rather than a raceable worktree path."""

    root, authority = _registered_stage_authority(stage_root)
    try:
        repository_relative = PurePosixPath(source.relative_to(PROJECT_ROOT).as_posix())
        stage_relative = PurePosixPath(destination.relative_to(root).as_posix())
    except ValueError:
        _raise_stage_failure("benchmark stage scope is invalid")
    if (
        repository_relative.is_absolute()
        or stage_relative.is_absolute()
        or not repository_relative.parts
        or not stage_relative.parts
        or any(part in {"", ".", ".."} for part in repository_relative.parts)
        or any(part in {"", ".", ".."} for part in stage_relative.parts)
    ):
        _raise_stage_failure("benchmark stage scope is invalid")
    payload = _read_git_blob(
        revision,
        repository_relative,
        maximum_bytes=MAX_STAGE_FILE_BYTES,
    )
    budget.observe_file(len(payload))
    digest = hashlib.sha256(payload).hexdigest()
    existing = staged.get(stage_relative)
    if existing is not None:
        if existing != digest:
            _raise_stage_failure("benchmark stage collision is invalid")
        return
    try:
        authority.tree.ensure_directory(PurePosixPath(*stage_relative.parts[:-1]))
        observed_digest = authority.tree.write_bytes(
            stage_relative,
            payload,
            maximum_bytes=MAX_STAGE_FILE_BYTES,
        )
    except Exception:
        _raise_stage_failure("benchmark stage copy failed")
    if observed_digest != digest:
        _raise_stage_failure("benchmark stage copy failed")
    staged[stage_relative] = digest


def _stage_copy_tree(
    source_root: Path,
    destination_root: Path,
    stage_root: Path,
    budget: _StageBudget,
    staged: dict[PurePosixPath, str],
    *,
    ignored_directories: frozenset[str] = frozenset(),
) -> None:
    from paperworkshell.storage._windows import (
        FileIdentity,
        WindowsHandle,
        open_handle,
        require_expected_handle,
    )

    retained: dict[Path, tuple[WindowsHandle, FileIdentity]] = {}
    snapshots: dict[Path, frozenset[str]] = {}
    primary: BaseException | None = None

    def pin_directory(path: Path) -> None:
        if path in retained:
            existing_handle, identity = retained[path]
            try:
                existing_handle.refresh_info()
                require_expected_handle(
                    existing_handle,
                    path,
                    directory=True,
                    expected_identity=identity,
                )
            except Exception:
                _raise_stage_failure("benchmark stage source changed during copy")
            return
        if not path.is_absolute() or not path.anchor:
            _raise_stage_failure("benchmark stage source is invalid")
        current = Path(path.anchor)
        chain = [current]
        for component in path.parts[1:]:
            current /= component
            chain.append(current)
        for candidate in chain:
            if candidate in retained:
                continue
            candidate_handle: WindowsHandle | None = None
            try:
                candidate_handle = open_handle(candidate)
                require_expected_handle(candidate_handle, candidate, directory=True)
            except Exception:
                if candidate_handle is not None:
                    try:
                        candidate_handle.close()
                    except Exception:
                        pass
                _raise_stage_failure("benchmark stage source is invalid")
            retained[candidate] = (candidate_handle, candidate_handle.info.identity)

    try:
        try:
            source_stat = source_root.lstat()
        except OSError:
            _raise_stage_failure("benchmark stage source is unavailable")
        if stat.S_ISREG(source_stat.st_mode):
            _stage_copy_file(source_root, destination_root, stage_root, budget, staged)
            return
        if not stat.S_ISDIR(source_stat.st_mode) or _is_reparse_point(source_root):
            _raise_stage_failure("benchmark stage source is invalid")

        pin_directory(source_root)
        pending: list[tuple[Path, Path, int]] = [(source_root, destination_root, 0)]
        while pending:
            source, destination, depth = pending.pop()
            if depth > MAX_STAGE_DEPTH:
                _raise_stage_failure("benchmark stage source is invalid")
            pin_directory(source)
            budget.observe_directory()
            try:
                children = sorted(source.iterdir(), key=lambda item: item.name.casefold())
            except OSError:
                _raise_stage_failure("benchmark stage source is unavailable")
            snapshots[source] = frozenset(child.name for child in children)
            discovered_directories: list[tuple[Path, Path, int]] = []
            for child in children:
                try:
                    child_stat = child.lstat()
                except OSError:
                    _raise_stage_failure("benchmark stage source is unavailable")
                if _is_reparse_point(child):
                    _raise_stage_failure("benchmark stage source is invalid")
                if child.name == "__pycache__" or child.suffix.casefold() in {".pyc", ".pyo"}:
                    continue
                if stat.S_ISDIR(child_stat.st_mode):
                    if child.name.casefold() in ignored_directories:
                        continue
                    pin_directory(child)
                    discovered_directories.append((child, destination / child.name, depth + 1))
                    continue
                if not stat.S_ISREG(child_stat.st_mode):
                    _raise_stage_failure("benchmark stage source is invalid")
                _stage_copy_file(
                    child,
                    destination / child.name,
                    stage_root,
                    budget,
                    staged,
                )
            pending.extend(reversed(discovered_directories))

        for source, expected_names in snapshots.items():
            pin_directory(source)
            try:
                observed_names = frozenset(child.name for child in source.iterdir())
            except OSError:
                _raise_stage_failure("benchmark stage source changed during copy")
            if observed_names != expected_names:
                _raise_stage_failure("benchmark stage source changed during copy")
    except BaseException as error:
        primary = error
        raise
    finally:
        close_failed = False
        for handle, _identity in reversed(tuple(retained.values())):
            try:
                handle.close()
            except Exception:
                close_failed = True
        if close_failed and primary is None:
            _raise_stage_failure("benchmark stage source could not be closed")


def _distribution_entry_selected(distribution_name: str, relative: PurePosixPath) -> bool:
    parts = relative.parts
    if not parts or ".." in parts or relative.is_absolute():
        return False
    first = parts[0].casefold()
    normalized = distribution_name.casefold().replace("-", "_")
    if first.endswith(".dist-info") and first.startswith(normalized):
        return True
    if distribution_name == "pypdfium2":
        return first in {"pypdfium2", "pypdfium2_cfg", "pypdfium2_raw"}
    if distribution_name == "shiboken6":
        return first == "shiboken6"
    if first != "pyside6" or len(parts) != 2:
        return False
    name = parts[1]
    if distribution_name == "PySide6-Addons":
        return name in {"QtPdf.pyd", "QtPdf.pyi", "Qt6Pdf.dll"}
    if distribution_name != "PySide6-Essentials":
        return False
    if name in {
        "__init__.py",
        "_config.py",
        "_git_pyside_version.py",
        "QtCore.pyd",
        "QtCore.pyi",
        "QtGui.pyd",
        "QtGui.pyi",
        "Qt6Core.dll",
        "Qt6Gui.dll",
        "Qt6Network.dll",
        "pyside6.abi3.dll",
    }:
        return True
    return (
        re.fullmatch(
            r"(?:concrt140|msvcp140(?:_1|_2|_codecvt_ids)?|vcamp140|vccorlib140|vcomp140|"
            r"vcruntime140(?:_1)?)\.dll",
            name,
            flags=re.IGNORECASE,
        )
        is not None
    )


def _record_sha256(value: str) -> str:
    if not value.startswith("sha256="):
        raise BenchmarkFailure(
            OUTCOME_DEPENDENCY_MISMATCH,
            "a renderer dependency has an invalid installed artifact record",
        )
    encoded = value.removeprefix("sha256=")
    if not encoded or len(encoded) > 64:
        raise BenchmarkFailure(
            OUTCOME_DEPENDENCY_MISMATCH,
            "a renderer dependency has an invalid installed artifact record",
        )
    try:
        digest = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error) as error:
        raise BenchmarkFailure(
            OUTCOME_DEPENDENCY_MISMATCH,
            "a renderer dependency has an invalid installed artifact record",
        ) from error
    if len(digest) != hashlib.sha256().digest_size:
        raise BenchmarkFailure(
            OUTCOME_DEPENDENCY_MISMATCH,
            "a renderer dependency has an invalid installed artifact record",
        )
    return digest.hex()


def _parse_installed_record(
    payload: bytes,
) -> dict[PurePosixPath, tuple[str | None, int | None]]:
    try:
        text = payload.decode("utf-8")
        rows = csv.reader(io.StringIO(text, newline=""), strict=True)
        output: dict[PurePosixPath, tuple[str | None, int | None]] = {}
        observed_paths: set[str] = set()
        for row in rows:
            if len(row) != 3:
                raise ValueError
            path_text, hash_text, size_text = row
            if not path_text or path_text in observed_paths:
                raise ValueError
            observed_paths.add(path_text)
            relative = PurePosixPath(path_text)
            if (
                "\\" in path_text
                or relative.is_absolute()
                or not relative.parts
                or any(part in {"", ".", ".."} for part in relative.parts)
            ):
                # Windows installers legitimately record generated ``../../Scripts`` entries.
                # They are outside the selected site-packages closure and are never staged.
                continue
            if relative in output:
                raise ValueError
            digest = _record_sha256(hash_text) if hash_text else None
            if size_text:
                if re.fullmatch(r"0|[1-9][0-9]*", size_text) is None:
                    raise ValueError
                size = int(size_text)
                if size > MAX_STAGE_FILE_BYTES:
                    raise ValueError
            else:
                size = None
            output[relative] = (digest, size)
    except (UnicodeDecodeError, csv.Error, ValueError) as error:
        raise BenchmarkFailure(
            OUTCOME_DEPENDENCY_MISMATCH,
            "a renderer dependency has an invalid installed artifact record",
        ) from error
    if not output:
        raise BenchmarkFailure(
            OUTCOME_DEPENDENCY_MISMATCH,
            "a renderer dependency has no installed artifact record",
        )
    return output


def _stage_distribution_closures(
    site_packages: Path,
    stage_root: Path,
    budget: _StageBudget,
    staged: dict[PurePosixPath, str],
) -> dict[str, str]:
    root, authority = _registered_stage_authority(stage_root)
    installed_records: dict[str, str] = {}
    for distribution_name in STAGED_DISTRIBUTIONS:
        try:
            distribution = importlib.metadata.distribution(distribution_name)
            files = tuple(distribution.files or ())
        except importlib.metadata.PackageNotFoundError:
            raise BenchmarkFailure(
                OUTCOME_DEPENDENCY_UNAVAILABLE,
                "a required renderer dependency is unavailable",
            ) from None
        record_candidates = []
        for package_path in files:
            relative = PurePosixPath(str(package_path).replace("\\", "/"))
            if (
                relative.name == "RECORD"
                and len(relative.parts) >= 2
                and relative.parts[-2].casefold().endswith(".dist-info")
            ):
                record_candidates.append((package_path, relative))
        if len(record_candidates) != 1:
            raise BenchmarkFailure(
                OUTCOME_DEPENDENCY_MISMATCH,
                "a renderer dependency has no installed artifact record",
            )
        record_package_path, record_relative = record_candidates[0]
        _stage_copy_file(
            Path(str(distribution.locate_file(record_package_path))),
            site_packages.joinpath(*record_relative.parts),
            root,
            budget,
            staged,
        )
        staged_record_relative = PurePosixPath(
            site_packages.joinpath(*record_relative.parts).relative_to(root).as_posix()
        )
        try:
            record_payload = authority.tree.read_bytes(
                staged_record_relative,
                maximum_bytes=MAX_JSON_BYTES,
            )
        except Exception:
            raise BenchmarkFailure(
                OUTCOME_DEPENDENCY_MISMATCH,
                "a renderer dependency has an invalid installed artifact record",
            ) from None
        record_entries = _parse_installed_record(record_payload)
        record_entry = record_entries.get(record_relative)
        if record_entry != (None, None):
            raise BenchmarkFailure(
                OUTCOME_DEPENDENCY_MISMATCH,
                "a renderer dependency has an invalid installed artifact record",
            )
        installed_records[distribution_name] = hashlib.sha256(record_payload).hexdigest()

        selected = 0
        for relative, (expected_digest, expected_size) in sorted(
            record_entries.items(),
            key=lambda item: item[0].as_posix(),
        ):
            if not _distribution_entry_selected(distribution_name, relative):
                continue
            selected += 1
            if relative == record_relative:
                continue
            if expected_digest is None or expected_size is None:
                raise BenchmarkFailure(
                    OUTCOME_DEPENDENCY_MISMATCH,
                    "a renderer dependency has an invalid installed artifact record",
                )
            source = Path(str(distribution.locate_file(relative.as_posix())))
            destination = site_packages.joinpath(*relative.parts)
            _stage_copy_file(
                source,
                destination,
                root,
                budget,
                staged,
            )
            staged_relative = PurePosixPath(destination.relative_to(root).as_posix())
            sealed = authority.tree.sealed_files.get(staged_relative)
            if sealed != (expected_size, expected_digest):
                raise BenchmarkFailure(
                    OUTCOME_DEPENDENCY_MISMATCH,
                    "a renderer dependency does not match its installed artifact record",
                )
        if selected == 0:
            raise BenchmarkFailure(
                OUTCOME_DEPENDENCY_MISMATCH,
                "a renderer dependency has no installed artifact record",
            ) from None
    return installed_records


def _staged_data_sources(workload: BenchmarkWorkload) -> tuple[Path, ...]:
    sources = {
        PROJECT_ROOT / WORKLOAD_PATH,
        PROJECT_ROOT / "benchmarks/reference-windows-v1.json",
        PROJECT_ROOT / "tests/fixtures/synthetic/v1/index.json",
        PROJECT_ROOT / "tests/golden/renderer-v1/index.json",
    }
    sources.update(case.source_path for case in workload.fidelity_cases)
    sources.update(case.golden_path for case in workload.fidelity_cases)
    sources.update(case.source_path for case in workload.semantic_cases)
    sources.update(case.source_path for case in workload.outcome_cases)
    return tuple(sorted(sources, key=lambda item: item.as_posix()))


def _write_stage_manifest(
    stage_root: Path,
    staged: dict[PurePosixPath, str],
    budget: _StageBudget,
    revision: str,
) -> tuple[Path, str]:
    root, authority = _registered_stage_authority(stage_root)
    sealed_files = authority.tree.sealed_files
    entries: list[dict[str, object]] = []
    for relative, digest in sorted(staged.items(), key=lambda item: item[0].as_posix()):
        sealed = sealed_files.get(relative)
        if sealed is None or sealed[1] != digest:
            _raise_stage_failure("benchmark stage manifest failed")
        size = sealed[0]
        entries.append({"path": relative.as_posix(), "size": size, "sha256": digest})
    encoded = json.dumps(
        {
            "schema_version": 1,
            "repository_revision": revision,
            "files": entries,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    if not 1 <= len(encoded) <= MAX_JSON_BYTES:
        _raise_stage_failure("benchmark stage manifest failed")
    budget.observe_file(len(encoded))
    try:
        manifest_sha256 = authority.tree.write_bytes(
            BENCHMARK_STAGE_MANIFEST,
            encoded,
            maximum_bytes=MAX_STAGE_FILE_BYTES,
        )
    except Exception:
        _raise_stage_failure("benchmark stage manifest failed")
    return root.joinpath(*BENCHMARK_STAGE_MANIFEST.parts), manifest_sha256


def _hash_staged_file(path: Path, expected_size: int) -> str:
    digest = hashlib.sha256()
    observed = 0
    try:
        stream = path.open("rb")
    except OSError:
        _raise_stage_failure("benchmark stage audit failed")
    with _StageStreamContext(stream, "benchmark stage audit failed"):
        opened = _source_stream_identity(path, stream)
        if opened.st_size != expected_size:
            _raise_stage_failure("benchmark stage audit failed")
        while True:
            try:
                chunk = stream.read(min(1024 * 1024, expected_size - observed + 1))
            except OSError:
                _raise_stage_failure("benchmark stage audit failed")
            if not chunk:
                break
            observed += len(chunk)
            if observed > expected_size:
                _raise_stage_failure("benchmark stage audit failed")
            digest.update(chunk)
        after = _source_stream_identity(path, stream)
    if observed != expected_size or not _same_file_identity(opened, after):
        _raise_stage_failure("benchmark stage audit failed")
    return digest.hexdigest()


def _make_stage_immutable(stage_root: Path, relative_files: Sequence[PurePosixPath]) -> None:
    for relative in relative_files:
        target = stage_root.joinpath(*relative.parts)
        try:
            target.chmod(stat.S_IREAD)
        except OSError:
            _raise_stage_failure("benchmark stage could not be sealed")


def _audit_staged_files(
    stage_root: Path,
    expected: Mapping[PurePosixPath, str],
    manifest_path: Path,
) -> None:
    root, authority = _registered_stage_authority(stage_root)
    manifest_relative = PurePosixPath(manifest_path.relative_to(stage_root).as_posix())
    if root != stage_root or manifest_relative != BENCHMARK_STAGE_MANIFEST:
        _raise_stage_failure("benchmark stage audit failed")
    manifest_digest = authority.tree.sealed_hashes.get(manifest_relative)
    if manifest_digest is None:
        _raise_stage_failure("benchmark stage audit failed")
    complete_expected = dict(expected)
    complete_expected[manifest_relative] = manifest_digest
    try:
        authority.tree.audit(complete_expected, MAX_STAGE_FILE_BYTES)
    except Exception:
        _raise_stage_failure("benchmark stage audit failed")


def stage_benchmark_resources(
    stage_root: Path,
    workload: BenchmarkWorkload,
    revision: str,
) -> StagedBenchmarkResources:
    """Copy the exact benchmark runtime, code, and bound public-synthetic data."""

    root, authority = _registered_stage_authority(stage_root)
    runtime_root = root / "runtime"
    code_root = root / "code"
    data_root = root / "data"
    budget = _StageBudget()
    staged: dict[PurePosixPath, str] = {}
    try:
        if (
            hashlib.sha256(
                _read_git_blob(
                    revision,
                    PurePosixPath(WORKLOAD_PATH.as_posix()),
                    maximum_bytes=MAX_JSON_BYTES,
                )
            ).hexdigest()
            != workload.workload_sha256
        ):
            raise BenchmarkFailure(
                OUTCOME_REPOSITORY_DIRTY,
                "the benchmark requires a clean repository tree",
            )
        authority.tree.ensure_directory(PurePosixPath("runtime"))
        authority.tree.ensure_directory(PurePosixPath("code"))
        authority.tree.ensure_directory(PurePosixPath("data"))
        runtime_source = Path(sys.base_prefix).resolve(strict=True)
        for child in sorted(runtime_source.iterdir(), key=lambda item: item.name.casefold()):
            if child.name == "__pycache__" or child.suffix.casefold() in {".pyc", ".pyo"}:
                continue
            try:
                child_stat = child.lstat()
            except OSError:
                _raise_stage_failure("benchmark stage source is unavailable")
            if stat.S_ISDIR(child_stat.st_mode):
                if child.name not in {"DLLs", "Lib"}:
                    continue
                _stage_copy_tree(
                    child,
                    runtime_root / child.name,
                    root,
                    budget,
                    staged,
                    ignored_directories=(
                        frozenset({"site-packages"})
                        if child.name.casefold() == "lib"
                        else frozenset()
                    ),
                )
            elif child.name.casefold() != "python3.exe":
                _stage_copy_file(child, runtime_root / child.name, root, budget, staged)
        site_packages = runtime_root / "Lib" / "site-packages"
        authority.tree.ensure_directory(PurePosixPath("runtime/Lib/site-packages"))
        installed_records = _stage_distribution_closures(
            site_packages,
            root,
            budget,
            staged,
        )

        code_files = (
            PROJECT_ROOT / "tools/__init__.py",
            PROJECT_ROOT / "tools/pdf_renderer_benchmark.py",
            PROJECT_ROOT / BENCHMARK_BOOTSTRAP_PATH,
            PROJECT_ROOT / "src/paperworkshell/__init__.py",
            PROJECT_ROOT / "src/paperworkshell/worker/__init__.py",
            PROJECT_ROOT / "src/paperworkshell/worker/_windows_runtime.py",
            PROJECT_ROOT / "src/paperworkshell/worker/protocol/__init__.py",
            PROJECT_ROOT / "src/paperworkshell/worker/protocol/bootstrap.py",
            PROJECT_ROOT / "src/paperworkshell/worker/protocol/errors.py",
            PROJECT_ROOT / "src/paperworkshell/worker/protocol/framing.py",
            PROJECT_ROOT / "src/paperworkshell/worker/protocol/models.py",
        )
        for source in code_files:
            _stage_git_file(
                revision,
                source,
                code_root / source.relative_to(PROJECT_ROOT),
                root,
                budget,
                staged,
            )
        for source in _staged_data_sources(workload):
            try:
                relative = source.relative_to(PROJECT_ROOT)
            except ValueError:
                _raise_stage_failure("benchmark data scope is invalid")
            _stage_git_file(
                revision,
                source,
                data_root / relative,
                root,
                budget,
                staged,
            )

        manifest_path, manifest_sha256 = _write_stage_manifest(
            root,
            staged,
            budget,
            revision,
        )
        _audit_staged_files(root, staged, manifest_path)
        authority.expected_files = dict(staged)
        authority.expected_files[BENCHMARK_STAGE_MANIFEST] = manifest_sha256
        try:
            authority.tree.seal_namespace(
                authority.expected_files,
                MAX_STAGE_FILE_BYTES,
            )
        except Exception:
            _raise_stage_failure("benchmark stage could not be sealed")
        resources = StagedBenchmarkResources(
            stage_root=root,
            runtime_root=runtime_root,
            code_root=code_root,
            data_root=data_root,
            python_executable=runtime_root / "python.exe",
            bootstrap_script=code_root / BENCHMARK_BOOTSTRAP_PATH,
            manifest_path=manifest_path,
            manifest_sha256=manifest_sha256,
            cleanup_token=authority.cleanup_token,
            staged_file_sha256=tuple(sorted(staged.items(), key=lambda item: item[0].as_posix())),
            installed_record_sha256=tuple(sorted(installed_records.items())),
        )
        required_files = {
            PurePosixPath(resources.python_executable.relative_to(root).as_posix()),
            PurePosixPath(resources.bootstrap_script.relative_to(root).as_posix()),
        }
        if not required_files.issubset(authority.tree.file_paths):
            _raise_stage_failure("benchmark stage is incomplete")
        return resources
    except BenchmarkFailure as error:
        try:
            _remove_benchmark_stage_root(root, authority.cleanup_token)
        except BenchmarkFailure:
            error.note_cleanup_failure()
        raise
    except Exception as cause:
        failure = BenchmarkFailure(
            OUTCOME_CHILD_FAILED,
            "benchmark stage failed",
        )
        try:
            _remove_benchmark_stage_root(root, authority.cleanup_token)
        except BenchmarkFailure:
            failure.note_cleanup_failure()
        raise failure from cause
    except BaseException:
        try:
            _remove_benchmark_stage_root(root, authority.cleanup_token)
        except BenchmarkFailure:
            pass
        raise


def _remove_benchmark_stage_root(stage_root: Path, cleanup_token: str) -> None:
    root, authority = _registered_stage_entry(stage_root)
    if (
        type(cleanup_token) is not str
        or not secrets.compare_digest(cleanup_token, authority.cleanup_token)
        or not root.name.startswith(BENCHMARK_STAGE_PREFIX)
        or len(root.name) <= len(BENCHMARK_STAGE_PREFIX)
    ):
        _raise_stage_failure("benchmark stage cleanup target is invalid")

    with authority.state_lock:
        if not authority.cleanup_authorized:
            _raise_stage_failure("benchmark stage cleanup authority was withdrawn")
        # Cleanup authority is one-shot.  Any partial delete failure therefore leaves the
        # retained stage permanently unavailable to a later retry in this process.
        authority.cleanup_authorized = False
    try:
        authority.tree.delete(authority.expected_files, MAX_STAGE_FILE_BYTES)
    except Exception:
        _raise_stage_failure("benchmark stage cleanup failed")
    with _STAGE_AUTHORITIES_LOCK:
        if _STAGE_AUTHORITIES.get(root) is authority:
            del _STAGE_AUTHORITIES[root]


def cleanup_benchmark_resources(resources: StagedBenchmarkResources) -> None:
    """Remove exactly one authenticated stage and no broader path."""

    if type(resources) is not StagedBenchmarkResources:
        _raise_stage_failure("benchmark stage cleanup target is invalid")
    try:
        _remove_benchmark_stage_root(resources.stage_root, resources.cleanup_token)
    except BenchmarkFailure as error:
        raise BenchmarkFailure(OUTCOME_CLEANUP_FAILED, "benchmark cleanup failed") from error


def _require_staged_namespace_sealed(resources: StagedBenchmarkResources) -> None:
    if type(resources) is not StagedBenchmarkResources:
        _raise_stage_failure("benchmark stage authority is invalid")
    root, authority = _registered_stage_authority(resources.stage_root)
    if root != resources.stage_root:
        _raise_stage_failure("benchmark stage authority is invalid")
    try:
        authority.tree.require_namespace_sealed()
    except Exception:
        _raise_stage_failure("benchmark stage is not sealed")


def _withdraw_stage_cleanup_authority(resources: StagedBenchmarkResources) -> None:
    """Permanently retain a stage when worker-profile cleanup is uncertain."""

    if type(resources) is not StagedBenchmarkResources:
        _raise_stage_failure("benchmark stage authority is invalid")
    root, authority = _registered_stage_entry(resources.stage_root)
    if root != resources.stage_root or not secrets.compare_digest(
        resources.cleanup_token, authority.cleanup_token
    ):
        _raise_stage_failure("benchmark stage authority is invalid")
    with authority.state_lock:
        authority.cleanup_authorized = False


def _require_stage_cleanup_authorized(resources: StagedBenchmarkResources) -> None:
    """Prevent another child launch after cleanup authority has been withdrawn."""

    if type(resources) is not StagedBenchmarkResources:
        _raise_stage_failure("benchmark stage authority is invalid")
    root, authority = _registered_stage_entry(resources.stage_root)
    if root != resources.stage_root or not secrets.compare_digest(
        resources.cleanup_token, authority.cleanup_token
    ):
        _raise_stage_failure("benchmark stage authority is invalid")
    with authority.state_lock:
        if not authority.cleanup_authorized:
            _raise_stage_failure("benchmark stage cleanup authority was withdrawn")


def _safe_relative_path(value: object, *, prefix: str) -> PurePosixPath | None:
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        return None
    if not candidate.parts or candidate.parts[0] != prefix:
        return None
    return candidate


def _read_repository_bytes(
    root: Path,
    relative: PurePosixPath,
    *,
    maximum_bytes: int,
) -> bytes:
    if (
        not root.is_absolute()
        or relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise BenchmarkFailure("PDF_RENDER_BENCHMARK_INPUT_UNSAFE", "repository input is unsafe")
    try:
        root_stat = root.lstat()
    except OSError:
        raise BenchmarkFailure(
            "PDF_RENDER_BENCHMARK_INPUT_UNREADABLE",
            "repository input could not be read",
        ) from None
    if not stat.S_ISDIR(root_stat.st_mode) or bool(
        int(getattr(root_stat, "st_file_attributes", 0))
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    ):
        raise BenchmarkFailure("PDF_RENDER_BENCHMARK_INPUT_UNSAFE", "repository input is unsafe")
    current = root
    for component in relative.parts:
        current = current / component
        try:
            current_stat = current.lstat()
        except OSError:
            raise BenchmarkFailure(
                "PDF_RENDER_BENCHMARK_INPUT_UNREADABLE",
                "repository input could not be read",
            ) from None
        if bool(
            int(getattr(current_stat, "st_file_attributes", 0))
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise BenchmarkFailure(
                "PDF_RENDER_BENCHMARK_INPUT_UNSAFE",
                "repository input is unsafe",
            )
    try:
        stream = current.open("rb")
    except OSError:
        raise BenchmarkFailure(
            "PDF_RENDER_BENCHMARK_INPUT_UNREADABLE",
            "repository input could not be read",
        ) from None
    with _RepositoryInputStreamContext(stream):
        try:
            opened = os.fstat(stream.fileno())
            path_before = current.lstat()
        except OSError:
            raise BenchmarkFailure(
                "PDF_RENDER_BENCHMARK_INPUT_UNREADABLE",
                "repository input could not be read",
            ) from None
        if (
            not stat.S_ISREG(opened.st_mode)
            or not _opened_handle_matches_path(opened, path_before)
            or opened.st_size < 0
            or opened.st_size > maximum_bytes
        ):
            raise BenchmarkFailure(
                "PDF_RENDER_BENCHMARK_INPUT_INVALID",
                "repository input exceeds its fixed limit",
            ) from None
        content = bytearray()
        observed = 0
        while True:
            try:
                chunk = stream.read(min(1024 * 1024, opened.st_size - observed + 1))
            except OSError:
                raise BenchmarkFailure(
                    "PDF_RENDER_BENCHMARK_INPUT_UNREADABLE",
                    "repository input could not be read",
                ) from None
            if not chunk:
                break
            observed += len(chunk)
            if observed > opened.st_size or observed > maximum_bytes:
                raise BenchmarkFailure(
                    "PDF_RENDER_BENCHMARK_INPUT_CHANGED",
                    "repository input changed during validation",
                ) from None
            content.extend(chunk)
        try:
            after = os.fstat(stream.fileno())
            path_after = current.lstat()
        except OSError:
            raise BenchmarkFailure(
                "PDF_RENDER_BENCHMARK_INPUT_UNREADABLE",
                "repository input could not be read",
            ) from None
        if (
            observed != opened.st_size
            or not _same_file_identity(opened, after)
            or not _opened_handle_matches_path(opened, path_after)
        ):
            raise BenchmarkFailure(
                "PDF_RENDER_BENCHMARK_INPUT_CHANGED",
                "repository input changed during validation",
            ) from None
    return bytes(content)


def _decode_json_bytes(content: bytes) -> object:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BenchmarkFailure(
            "PDF_RENDER_BENCHMARK_JSON_ENCODING_INVALID",
            "benchmark JSON must use UTF-8",
        ) from error
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except DuplicateJsonKeyError as error:
        raise BenchmarkFailure(
            "PDF_RENDER_BENCHMARK_JSON_DUPLICATE_KEY",
            "benchmark JSON member names must be unique",
        ) from error
    except NonStandardJsonConstantError as error:
        raise BenchmarkFailure(
            "PDF_RENDER_BENCHMARK_JSON_CONSTANT_INVALID",
            "benchmark JSON cannot contain NaN or Infinity",
        ) from error
    except json.JSONDecodeError as error:
        raise BenchmarkFailure(
            "PDF_RENDER_BENCHMARK_JSON_INVALID",
            "benchmark JSON is invalid",
        ) from error


def _strict_object(
    value: object,
    keys: frozenset[str],
    location: str,
    issues: list[BenchmarkIssue],
) -> Mapping[str, object] | None:
    if not isinstance(value, dict):
        issues.append(BenchmarkIssue("BENCHMARK_FIELD_TYPE", location, "must be an object"))
        return None
    if set(value) != keys:
        issues.append(
            BenchmarkIssue(
                "BENCHMARK_OBJECT_KEYS",
                location,
                "object members do not match the versioned workload",
            )
        )
        return None
    return cast(Mapping[str, object], value)


def _hash_is_valid(value: object) -> bool:
    return isinstance(value, str) and SHA256_PATTERN.fullmatch(value) is not None


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _load_bound_json(
    root: Path,
    binding: Mapping[str, object],
    *,
    prefix: str,
    location: str,
    issues: list[BenchmarkIssue],
) -> tuple[Mapping[str, object] | None, str | None]:
    if set(binding) != {"path", "sha256"}:
        issues.append(BenchmarkIssue("BENCHMARK_OBJECT_KEYS", location, "binding is invalid"))
        return None, None
    relative = _safe_relative_path(binding.get("path"), prefix=prefix)
    digest = binding.get("sha256")
    if relative is None or not _hash_is_valid(digest):
        issues.append(BenchmarkIssue("BENCHMARK_BINDING_INVALID", location, "binding is invalid"))
        return None, None
    try:
        content = _read_repository_bytes(root, relative, maximum_bytes=MAX_JSON_BYTES)
    except BenchmarkFailure as error:
        issues.append(BenchmarkIssue(error.code, location, error.message))
        return None, None
    actual = _sha256(content)
    if actual != digest:
        issues.append(
            BenchmarkIssue(
                "BENCHMARK_BINDING_HASH_MISMATCH",
                location,
                "bound input does not match its SHA-256",
            )
        )
        return None, None
    try:
        decoded = _decode_json_bytes(content)
    except BenchmarkFailure as error:
        issues.append(BenchmarkIssue(error.code, location, error.message))
        return None, None
    if not isinstance(decoded, dict):
        issues.append(BenchmarkIssue("BENCHMARK_JSON_ROOT_TYPE", location, "must be an object"))
        return None, None
    return cast(Mapping[str, object], decoded), digest


def _required_int(
    value: object,
    *,
    minimum: int,
    maximum: int,
    location: str,
    issues: list[BenchmarkIssue],
) -> int | None:
    if type(value) is not int or not minimum <= value <= maximum:
        issues.append(
            BenchmarkIssue(
                "BENCHMARK_FIELD_VALUE",
                location,
                f"must be an integer from {minimum} through {maximum}",
            )
        )
        return None
    return value


def _fit_dimensions(
    oriented_width_units: float,
    oriented_height_units: float,
    maximum_width_pixels: int,
    maximum_height_pixels: int,
) -> tuple[int, int]:
    """Ceil-fit one oriented page box without treating maxima as output dimensions."""

    if (
        not math.isfinite(oriented_width_units)
        or not math.isfinite(oriented_height_units)
        or oriented_width_units <= 0
        or oriented_height_units <= 0
        or type(maximum_width_pixels) is not int
        or type(maximum_height_pixels) is not int
        or maximum_width_pixels <= 0
        or maximum_height_pixels <= 0
    ):
        raise _StableEngineError("PDF_RENDER_GEOMETRY_INVALID")
    scale = min(
        maximum_width_pixels / oriented_width_units,
        maximum_height_pixels / oriented_height_units,
    )
    width = min(maximum_width_pixels, math.ceil(oriented_width_units * scale))
    height = min(maximum_height_pixels, math.ceil(oriented_height_units * scale))
    if width <= 0 or height <= 0:
        raise _StableEngineError("PDF_RENDER_GEOMETRY_INVALID")
    return width, height


def _is_valid_expected_fit(
    width_pixels: int | None,
    height_pixels: int | None,
    maximum_width_pixels: int | None,
    maximum_height_pixels: int | None,
) -> bool:
    return (
        width_pixels is not None
        and height_pixels is not None
        and maximum_width_pixels is not None
        and maximum_height_pixels is not None
        and width_pixels <= maximum_width_pixels
        and height_pixels <= maximum_height_pixels
        and (width_pixels == maximum_width_pixels or height_pixels == maximum_height_pixels)
    )


def _indexed_objects(
    value: object,
    *,
    location: str,
    issues: list[BenchmarkIssue],
) -> dict[str, Mapping[str, object]]:
    if not isinstance(value, list):
        issues.append(BenchmarkIssue("BENCHMARK_FIELD_TYPE", location, "must be an array"))
        return {}
    indexed: dict[str, Mapping[str, object]] = {}
    for index, item in enumerate(value):
        item_location = f"{location}[{index}]"
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            issues.append(
                BenchmarkIssue("BENCHMARK_INDEX_ENTRY_INVALID", item_location, "entry is invalid")
            )
            continue
        item_id = cast(str, item["id"])
        if item_id in indexed:
            issues.append(
                BenchmarkIssue(
                    "BENCHMARK_INDEX_ID_DUPLICATE",
                    item_location,
                    "entry ID must be unique",
                )
            )
            continue
        indexed[item_id] = cast(Mapping[str, object], item)
    return indexed


def _joined_repository_path(base: str, value: object) -> PurePosixPath | None:
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    suffix = PurePosixPath(value)
    if suffix.is_absolute() or any(part in {"", ".", ".."} for part in suffix.parts):
        return None
    combined = PurePosixPath(base) / suffix
    return combined if combined.parts and combined.parts[0] == "tests" else None


def _verify_bound_artifact(
    root: Path,
    relative: PurePosixPath | None,
    expected_sha256: object,
    *,
    location: str,
    issues: list[BenchmarkIssue],
) -> Path | None:
    if relative is None or not _hash_is_valid(expected_sha256):
        issues.append(BenchmarkIssue("BENCHMARK_ARTIFACT_BINDING", location, "binding is invalid"))
        return None
    try:
        content = _read_repository_bytes(
            root,
            relative,
            maximum_bytes=MAX_RENDER_OUTPUT_BYTES,
        )
    except BenchmarkFailure as error:
        issues.append(BenchmarkIssue(error.code, location, error.message))
        return None
    if _sha256(content) != expected_sha256:
        issues.append(
            BenchmarkIssue(
                "BENCHMARK_ARTIFACT_HASH_MISMATCH",
                location,
                "artifact does not match its bound SHA-256",
            )
        )
        return None
    return root / Path(*relative.parts)


def _parse_fidelity_cases(
    root: Path,
    selected_ids: object,
    fixture_index: Mapping[str, object],
    golden_index: Mapping[str, object],
    issues: list[BenchmarkIssue],
) -> tuple[FidelityCaseRecord, ...]:
    if selected_ids != list(EXPECTED_FIDELITY_CASE_IDS):
        issues.append(
            BenchmarkIssue(
                "BENCHMARK_CORPUS_SELECTION",
                "$.corpus.fidelity_case_ids",
                "case IDs or order do not match renderer workload v1",
            )
        )
        return ()
    fixtures = _indexed_objects(
        fixture_index.get("fixtures"),
        location="fixture-index.fixtures",
        issues=issues,
    )
    references = _indexed_objects(
        golden_index.get("references"),
        location="golden-index.references",
        issues=issues,
    )
    cases: list[FidelityCaseRecord] = []
    for case_id in EXPECTED_FIDELITY_CASE_IDS:
        location = f"golden-index.references[{case_id}]"
        reference = references.get(case_id)
        if reference is None:
            issues.append(
                BenchmarkIssue("BENCHMARK_GOLDEN_MISSING", location, "golden case is missing")
            )
            continue
        fixture_id = reference.get("source_fixture_id")
        source_sha256 = reference.get("source_sha256")
        golden_sha256 = reference.get("reference_sha256")
        if (
            not isinstance(fixture_id, str)
            or not _hash_is_valid(source_sha256)
            or not _hash_is_valid(golden_sha256)
        ):
            issues.append(
                BenchmarkIssue("BENCHMARK_GOLDEN_BINDING", location, "golden binding is invalid")
            )
            continue
        fixture = fixtures.get(fixture_id)
        if fixture is None or fixture.get("sha256") != source_sha256:
            issues.append(
                BenchmarkIssue(
                    "BENCHMARK_SOURCE_BINDING",
                    location,
                    "source fixture binding is invalid",
                )
            )
            continue
        source_relative = _joined_repository_path(
            "tests/fixtures/synthetic/v1",
            fixture.get("path"),
        )
        golden_relative = _joined_repository_path(
            "tests/golden/renderer-v1",
            reference.get("path"),
        )
        source_path = _verify_bound_artifact(
            root,
            source_relative,
            source_sha256,
            location=f"{location}.source",
            issues=issues,
        )
        golden_path = _verify_bound_artifact(
            root,
            golden_relative,
            golden_sha256,
            location=f"{location}.golden",
            issues=issues,
        )
        request = reference.get("request")
        comparison = reference.get("comparison")
        if not isinstance(request, dict) or not isinstance(comparison, dict):
            issues.append(
                BenchmarkIssue("BENCHMARK_GOLDEN_REQUEST", location, "request is invalid")
            )
            continue
        page_index = _required_int(
            request.get("page_index"),
            minimum=0,
            maximum=10_000,
            location=f"{location}.request.page_index",
            issues=issues,
        )
        rotation = _required_int(
            request.get("additional_rotation_degrees"),
            minimum=0,
            maximum=270,
            location=f"{location}.request.additional_rotation_degrees",
            issues=issues,
        )
        page_box = request.get("page_box")
        if page_box not in {"crop", "media"}:
            issues.append(
                BenchmarkIssue(
                    "BENCHMARK_PAGE_BOX_INVALID",
                    f"{location}.request.page_box",
                    "page box must be crop or media",
                )
            )
            page_box = None
        maximum_width = _required_int(
            request.get("maximum_width_pixels"),
            minimum=1,
            maximum=4_096,
            location=f"{location}.request.maximum_width_pixels",
            issues=issues,
        )
        maximum_height = _required_int(
            request.get("maximum_height_pixels"),
            minimum=1,
            maximum=4_096,
            location=f"{location}.request.maximum_height_pixels",
            issues=issues,
        )
        width = _required_int(
            reference.get("width"),
            minimum=1,
            maximum=4_096,
            location=f"{location}.width",
            issues=issues,
        )
        height = _required_int(
            reference.get("height"),
            minimum=1,
            maximum=4_096,
            location=f"{location}.height",
            issues=issues,
        )
        delta = _required_int(
            comparison.get("maximum_channel_delta"),
            minimum=0,
            maximum=255,
            location=f"{location}.comparison.maximum_channel_delta",
            issues=issues,
        )
        render_scale_value = reference.get("render_scale_pixels_per_user_unit")
        if (
            type(render_scale_value) not in {int, float}
            or not math.isfinite(cast(float, render_scale_value))
            or cast(float, render_scale_value) <= 0
        ):
            issues.append(
                BenchmarkIssue(
                    "BENCHMARK_GOLDEN_SCALE",
                    f"{location}.render_scale_pixels_per_user_unit",
                    "render scale must be finite and positive",
                )
            )
            render_scale: float | None = None
        else:
            render_scale = float(cast(int | float, render_scale_value))
        oriented_width = None if width is None or render_scale is None else width / render_scale
        oriented_height = None if height is None or render_scale is None else height / render_scale
        if rotation not in {0, 90, 180, 270}:
            issues.append(
                BenchmarkIssue(
                    "BENCHMARK_ROTATION_INVALID",
                    f"{location}.request.additional_rotation_degrees",
                    "rotation must be a quarter-turn",
                )
            )
            rotation = None
        valid_fit = _is_valid_expected_fit(width, height, maximum_width, maximum_height)
        if (
            valid_fit
            and oriented_width is not None
            and oriented_height is not None
            and maximum_width is not None
            and maximum_height is not None
            and _fit_dimensions(
                oriented_width,
                oriented_height,
                maximum_width,
                maximum_height,
            )
            != (width, height)
        ):
            valid_fit = False
        if not valid_fit:
            issues.append(
                BenchmarkIssue(
                    "BENCHMARK_GOLDEN_DIMENSIONS",
                    location,
                    "golden dimensions must be an aspect fit within the request maxima",
                )
            )
        if (
            source_path is not None
            and golden_path is not None
            and page_index is not None
            and isinstance(page_box, str)
            and rotation is not None
            and oriented_width is not None
            and oriented_height is not None
            and maximum_width is not None
            and maximum_height is not None
            and width is not None
            and height is not None
            and delta is not None
        ):
            cases.append(
                FidelityCaseRecord(
                    case_id=case_id,
                    fixture_id=fixture_id,
                    source_path=source_path,
                    source_sha256=cast(str, source_sha256),
                    golden_path=golden_path,
                    golden_sha256=cast(str, golden_sha256),
                    page_index=page_index,
                    page_box=page_box,
                    page_box_relation="default-equivalent",
                    additional_rotation_degrees=rotation,
                    oriented_width_units=oriented_width,
                    oriented_height_units=oriented_height,
                    maximum_width_pixels=maximum_width,
                    maximum_height_pixels=maximum_height,
                    width_pixels=width,
                    height_pixels=height,
                    maximum_channel_delta=delta,
                )
            )
    return tuple(cases)


def _parse_outcome_cases(
    root: Path,
    value: object,
    fixture_index: Mapping[str, object],
    issues: list[BenchmarkIssue],
) -> tuple[OutcomeCaseRecord, ...]:
    if not isinstance(value, list):
        issues.append(
            BenchmarkIssue("BENCHMARK_FIELD_TYPE", "$.corpus.outcome_cases", "must be an array")
        )
        return ()
    fixtures = _indexed_objects(
        fixture_index.get("fixtures"),
        location="fixture-index.fixtures",
        issues=issues,
    )
    cases: list[OutcomeCaseRecord] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        location = f"$.corpus.outcome_cases[{index}]"
        item_object = _strict_object(
            item,
            frozenset(
                {
                    "id",
                    "fixture_id",
                    "page_index",
                    "maximum_width_pixels",
                    "maximum_height_pixels",
                    "expected_code",
                }
            ),
            location,
            issues,
        )
        if item_object is None:
            continue
        case_id = item_object.get("id")
        fixture_id = item_object.get("fixture_id")
        expected_code = item_object.get("expected_code")
        expected = EXPECTED_OUTCOME_CASES.get(cast(str, case_id))
        if (
            not isinstance(case_id, str)
            or case_id in seen
            or expected is None
            or not isinstance(fixture_id, str)
            or not isinstance(expected_code, str)
            or (fixture_id, expected_code) != expected
        ):
            issues.append(
                BenchmarkIssue(
                    "BENCHMARK_OUTCOME_CASE_INVALID",
                    location,
                    "outcome case does not match workload v1",
                )
            )
            continue
        seen.add(case_id)
        fixture = fixtures.get(fixture_id)
        source_sha256 = None if fixture is None else fixture.get("sha256")
        source_relative = (
            None
            if fixture is None
            else _joined_repository_path("tests/fixtures/synthetic/v1", fixture.get("path"))
        )
        source_path = _verify_bound_artifact(
            root,
            source_relative,
            source_sha256,
            location=f"{location}.source",
            issues=issues,
        )
        page_index = _required_int(
            item_object.get("page_index"),
            minimum=0,
            maximum=10_000,
            location=f"{location}.page_index",
            issues=issues,
        )
        width = _required_int(
            item_object.get("maximum_width_pixels"),
            minimum=1,
            maximum=1_000_000,
            location=f"{location}.maximum_width_pixels",
            issues=issues,
        )
        height = _required_int(
            item_object.get("maximum_height_pixels"),
            minimum=1,
            maximum=1_000_000,
            location=f"{location}.maximum_height_pixels",
            issues=issues,
        )
        if (
            source_path is not None
            and isinstance(source_sha256, str)
            and page_index is not None
            and width is not None
            and height is not None
        ):
            cases.append(
                OutcomeCaseRecord(
                    case_id=case_id,
                    fixture_id=fixture_id,
                    source_path=source_path,
                    source_sha256=source_sha256,
                    page_index=page_index,
                    maximum_width_pixels=width,
                    maximum_height_pixels=height,
                    expected_code=expected_code,
                )
            )
    if tuple(case.case_id for case in cases) != tuple(EXPECTED_OUTCOME_CASES):
        issues.append(
            BenchmarkIssue(
                "BENCHMARK_OUTCOME_SELECTION",
                "$.corpus.outcome_cases",
                "outcome cases or order do not match workload v1",
            )
        )
    return tuple(cases)


def _parse_semantic_cases(
    root: Path,
    value: object,
    fixture_index: Mapping[str, object],
    issues: list[BenchmarkIssue],
) -> tuple[SemanticCaseRecord, ...]:
    if not isinstance(value, list):
        issues.append(
            BenchmarkIssue("BENCHMARK_FIELD_TYPE", "$.corpus.semantic_cases", "must be an array")
        )
        return ()
    fixtures = _indexed_objects(
        fixture_index.get("fixtures"),
        location="fixture-index.fixtures",
        issues=issues,
    )
    cases: list[SemanticCaseRecord] = []
    for index, item in enumerate(value):
        location = f"$.corpus.semantic_cases[{index}]"
        item_object = _strict_object(
            item,
            frozenset(
                {
                    "id",
                    "fixture_id",
                    "page_index",
                    "page_box",
                    "page_box_relation",
                    "additional_rotation_degrees",
                    "oriented_width_units",
                    "oriented_height_units",
                    "maximum_width_pixels",
                    "maximum_height_pixels",
                    "expected_width_pixels",
                    "expected_height_pixels",
                    "expected_code",
                    "oracle",
                }
            ),
            location,
            issues,
        )
        if item_object is None:
            continue
        case_id = item_object.get("id")
        fixture_id = item_object.get("fixture_id")
        expected_code = item_object.get("expected_code")
        oracle = item_object.get("oracle")
        expected = EXPECTED_SEMANTIC_CASES.get(cast(str, case_id))
        actual = (
            fixture_id,
            expected_code,
            oracle,
            item_object.get("page_index"),
            item_object.get("page_box"),
            item_object.get("page_box_relation"),
            item_object.get("additional_rotation_degrees"),
            item_object.get("oriented_width_units"),
            item_object.get("oriented_height_units"),
            item_object.get("maximum_width_pixels"),
            item_object.get("maximum_height_pixels"),
            item_object.get("expected_width_pixels"),
            item_object.get("expected_height_pixels"),
        )
        if (
            not isinstance(case_id, str)
            or not isinstance(fixture_id, str)
            or not isinstance(expected_code, str)
            or not isinstance(oracle, str)
            or actual != expected
        ):
            issues.append(
                BenchmarkIssue(
                    "BENCHMARK_SEMANTIC_CASE_INVALID",
                    location,
                    "semantic case does not match workload v1",
                )
            )
            continue
        fixture = fixtures.get(fixture_id)
        source_sha256 = None if fixture is None else fixture.get("sha256")
        source_relative = (
            None
            if fixture is None
            else _joined_repository_path("tests/fixtures/synthetic/v1", fixture.get("path"))
        )
        source_path = _verify_bound_artifact(
            root,
            source_relative,
            source_sha256,
            location=f"{location}.source",
            issues=issues,
        )
        page_index = _required_int(
            item_object.get("page_index"),
            minimum=0,
            maximum=10_000,
            location=f"{location}.page_index",
            issues=issues,
        )
        page_box = item_object.get("page_box")
        page_box_relation = item_object.get("page_box_relation")
        rotation = _required_int(
            item_object.get("additional_rotation_degrees"),
            minimum=0,
            maximum=270,
            location=f"{location}.additional_rotation_degrees",
            issues=issues,
        )
        oriented_width_value = item_object.get("oriented_width_units")
        oriented_height_value = item_object.get("oriented_height_units")
        if (
            type(oriented_width_value) not in {int, float}
            or type(oriented_height_value) not in {int, float}
            or not math.isfinite(cast(float, oriented_width_value))
            or not math.isfinite(cast(float, oriented_height_value))
            or cast(float, oriented_width_value) <= 0
            or cast(float, oriented_height_value) <= 0
        ):
            issues.append(
                BenchmarkIssue(
                    "BENCHMARK_SEMANTIC_GEOMETRY",
                    location,
                    "oriented page-box dimensions must be finite and positive",
                )
            )
            oriented_width: float | None = None
            oriented_height: float | None = None
        else:
            oriented_width = float(cast(int | float, oriented_width_value))
            oriented_height = float(cast(int | float, oriented_height_value))
        maximum_width = _required_int(
            item_object.get("maximum_width_pixels"),
            minimum=1,
            maximum=4_096,
            location=f"{location}.maximum_width_pixels",
            issues=issues,
        )
        maximum_height = _required_int(
            item_object.get("maximum_height_pixels"),
            minimum=1,
            maximum=4_096,
            location=f"{location}.maximum_height_pixels",
            issues=issues,
        )
        width = _required_int(
            item_object.get("expected_width_pixels"),
            minimum=1,
            maximum=4_096,
            location=f"{location}.expected_width_pixels",
            issues=issues,
        )
        height = _required_int(
            item_object.get("expected_height_pixels"),
            minimum=1,
            maximum=4_096,
            location=f"{location}.expected_height_pixels",
            issues=issues,
        )
        if page_box not in {"crop", "media"} or page_box_relation not in {
            "default-equivalent",
            "distinct-from-crop",
        }:
            issues.append(
                BenchmarkIssue(
                    "BENCHMARK_PAGE_BOX_INVALID",
                    location,
                    "page-box request or relation is invalid",
                )
            )
            page_box = None
            page_box_relation = None
        if rotation not in {0, 90, 180, 270}:
            issues.append(
                BenchmarkIssue(
                    "BENCHMARK_ROTATION_INVALID",
                    f"{location}.additional_rotation_degrees",
                    "rotation must be a quarter-turn",
                )
            )
            rotation = None
        valid_fit = _is_valid_expected_fit(width, height, maximum_width, maximum_height)
        if (
            valid_fit
            and oriented_width is not None
            and oriented_height is not None
            and maximum_width is not None
            and maximum_height is not None
            and _fit_dimensions(
                oriented_width,
                oriented_height,
                maximum_width,
                maximum_height,
            )
            != (width, height)
        ):
            valid_fit = False
        if not valid_fit:
            issues.append(
                BenchmarkIssue(
                    "BENCHMARK_SEMANTIC_DIMENSIONS",
                    location,
                    "expected dimensions must be an aspect fit within the request maxima",
                )
            )
        if (
            source_path is not None
            and isinstance(source_sha256, str)
            and page_index is not None
            and isinstance(page_box, str)
            and isinstance(page_box_relation, str)
            and rotation is not None
            and oriented_width is not None
            and oriented_height is not None
            and maximum_width is not None
            and maximum_height is not None
            and width is not None
            and height is not None
        ):
            cases.append(
                SemanticCaseRecord(
                    case_id=case_id,
                    fixture_id=fixture_id,
                    source_path=source_path,
                    source_sha256=source_sha256,
                    page_index=page_index,
                    page_box=page_box,
                    page_box_relation=page_box_relation,
                    additional_rotation_degrees=rotation,
                    oriented_width_units=oriented_width,
                    oriented_height_units=oriented_height,
                    maximum_width_pixels=maximum_width,
                    maximum_height_pixels=maximum_height,
                    width_pixels=width,
                    height_pixels=height,
                    expected_code=expected_code,
                    oracle=oracle,
                )
            )
    if tuple(case.case_id for case in cases) != tuple(EXPECTED_SEMANTIC_CASES):
        issues.append(
            BenchmarkIssue(
                "BENCHMARK_SEMANTIC_SELECTION",
                "$.corpus.semantic_cases",
                "semantic cases or order do not match workload v1",
            )
        )
    return tuple(cases)


def _parse_renderers(value: object, issues: list[BenchmarkIssue]) -> tuple[RendererSpec, ...]:
    if not isinstance(value, list):
        issues.append(BenchmarkIssue("BENCHMARK_FIELD_TYPE", "$.renderers", "must be an array"))
        return ()
    renderers: list[RendererSpec] = []
    for index, item in enumerate(value):
        location = f"$.renderers[{index}]"
        item_object = _strict_object(
            item,
            frozenset(
                {
                    "id",
                    "package",
                    "package_version",
                    "artifact",
                    "artifact_sha256",
                    "engine",
                    "engine_version",
                    "engine_feature_flags",
                }
            ),
            location,
            issues,
        )
        if item_object is None:
            continue
        renderer_id = item_object.get("id")
        expected = EXPECTED_RENDERERS.get(cast(str, renderer_id))
        actual = (
            item_object.get("package"),
            item_object.get("package_version"),
            item_object.get("engine"),
            item_object.get("engine_version"),
            item_object.get("artifact_sha256"),
        )
        flags = item_object.get("engine_feature_flags")
        if (
            not isinstance(renderer_id, str)
            or expected is None
            or actual != expected
            or not isinstance(item_object.get("artifact"), str)
            or not _hash_is_valid(item_object.get("artifact_sha256"))
            or not (
                (renderer_id == "pypdfium2" and flags == [])
                or (renderer_id == "qt-pdf" and flags == "not-exposed-by-public-api")
            )
        ):
            issues.append(
                BenchmarkIssue(
                    "BENCHMARK_RENDERER_IDENTITY",
                    location,
                    "renderer identity does not match workload v1",
                )
            )
            continue
        feature_flags: tuple[str, ...] | str = (
            tuple(cast(list[str], flags)) if isinstance(flags, list) else flags
        )
        renderers.append(
            RendererSpec(
                renderer_id=renderer_id,
                package=cast(str, item_object["package"]),
                package_version=cast(str, item_object["package_version"]),
                engine=cast(str, item_object["engine"]),
                engine_version=cast(str, item_object["engine_version"]),
                artifact_sha256=cast(str, item_object["artifact_sha256"]),
                engine_feature_flags=feature_flags,
            )
        )
    if tuple(renderer.renderer_id for renderer in renderers) != RENDERER_IDS:
        issues.append(
            BenchmarkIssue(
                "BENCHMARK_RENDERER_SELECTION",
                "$.renderers",
                "renderer order does not match workload v1",
            )
        )
    return tuple(renderers)


def _parse_measurement(
    value: object,
    issues: list[BenchmarkIssue],
) -> BenchmarkMeasurementPolicy | None:
    location = "$.measurement"
    measurement = _strict_object(
        value,
        frozenset(
            {
                "warm_iterations",
                "child_timeout_milliseconds",
                "cooperative_cancel_timeout_milliseconds",
                "hard_termination_timeout_milliseconds",
                "private_bytes_poll_milliseconds",
                "percentile_method",
            }
        ),
        location,
        issues,
    )
    if measurement is None:
        return None
    warm_iterations = _required_int(
        measurement.get("warm_iterations"),
        minimum=3,
        maximum=100,
        location=f"{location}.warm_iterations",
        issues=issues,
    )
    child_timeout = _required_int(
        measurement.get("child_timeout_milliseconds"),
        minimum=1_000,
        maximum=120_000,
        location=f"{location}.child_timeout_milliseconds",
        issues=issues,
    )
    cancel_timeout = _required_int(
        measurement.get("cooperative_cancel_timeout_milliseconds"),
        minimum=100,
        maximum=30_000,
        location=f"{location}.cooperative_cancel_timeout_milliseconds",
        issues=issues,
    )
    termination_timeout = _required_int(
        measurement.get("hard_termination_timeout_milliseconds"),
        minimum=100,
        maximum=30_000,
        location=f"{location}.hard_termination_timeout_milliseconds",
        issues=issues,
    )
    poll = _required_int(
        measurement.get("private_bytes_poll_milliseconds"),
        minimum=1,
        maximum=100,
        location=f"{location}.private_bytes_poll_milliseconds",
        issues=issues,
    )
    if measurement.get("percentile_method") != "nearest-rank":
        issues.append(
            BenchmarkIssue(
                "BENCHMARK_PERCENTILE_METHOD",
                f"{location}.percentile_method",
                "must be nearest-rank",
            )
        )
    if None in {warm_iterations, child_timeout, cancel_timeout, termination_timeout, poll}:
        return None
    return BenchmarkMeasurementPolicy(
        warm_iterations=cast(int, warm_iterations),
        child_timeout_milliseconds=cast(int, child_timeout),
        cooperative_cancel_timeout_milliseconds=cast(int, cancel_timeout),
        hard_termination_timeout_milliseconds=cast(int, termination_timeout),
        private_bytes_poll_milliseconds=cast(int, poll),
    )


def validate_workload(
    root: Path = PROJECT_ROOT,
) -> tuple[BenchmarkWorkload | None, list[BenchmarkIssue]]:
    """Validate and bind every checked-in input used by renderer benchmark v1."""

    issues: list[BenchmarkIssue] = []
    workload_relative = PurePosixPath(WORKLOAD_PATH.as_posix())
    try:
        workload_content = _read_repository_bytes(
            root,
            workload_relative,
            maximum_bytes=MAX_JSON_BYTES,
        )
        decoded = _decode_json_bytes(workload_content)
    except BenchmarkFailure as error:
        return None, [BenchmarkIssue(error.code, WORKLOAD_PATH.name, error.message)]
    root_object = _strict_object(
        decoded,
        frozenset(
            {
                "schema_version",
                "workload_id",
                "task_id",
                "reference_profile",
                "corpus",
                "render_profile",
                "renderers",
                "measurement",
                "report_policy",
            }
        ),
        "$",
        issues,
    )
    if root_object is None:
        return None, sorted(issues)
    if (
        root_object.get("schema_version") != 1
        or root_object.get("workload_id") != "paperworkshell-pdf-renderer-windows-v1"
        or root_object.get("task_id") != "PWS-0106"
    ):
        issues.append(
            BenchmarkIssue(
                "BENCHMARK_WORKLOAD_IDENTITY",
                "$",
                "workload identity does not match version 1",
            )
        )

    reference_binding = _strict_object(
        root_object.get("reference_profile"),
        frozenset({"id", "path", "sha256"}),
        "$.reference_profile",
        issues,
    )
    reference_profile: Mapping[str, object] | None = None
    reference_sha256: str | None = None
    reference_logical_processors: int | None = None
    reference_memory_bytes: int | None = None
    if reference_binding is not None:
        reference_profile, reference_sha256 = _load_bound_json(
            root,
            {"path": reference_binding["path"], "sha256": reference_binding["sha256"]},
            prefix="benchmarks",
            location="$.reference_profile",
            issues=issues,
        )
        if (
            reference_binding.get("id") != "windows-low-spec-v1"
            or reference_profile is None
            or reference_profile.get("profile_id") != reference_binding.get("id")
        ):
            issues.append(
                BenchmarkIssue(
                    "BENCHMARK_REFERENCE_PROFILE",
                    "$.reference_profile",
                    "reference profile binding is invalid",
                )
            )
        if reference_profile is not None:
            environment = reference_profile.get("environment")
            if isinstance(environment, dict):
                logical_processors = environment.get("logical_processors")
                memory_mib = environment.get("memory_mib")
                if (
                    type(logical_processors) is int
                    and logical_processors > 0
                    and type(memory_mib) is int
                    and memory_mib > 0
                ):
                    reference_logical_processors = logical_processors
                    reference_memory_bytes = memory_mib * 1024 * 1024
            if reference_logical_processors is None or reference_memory_bytes is None:
                issues.append(
                    BenchmarkIssue(
                        "BENCHMARK_REFERENCE_ENVELOPE",
                        "$.reference_profile",
                        "processor and memory envelope is invalid",
                    )
                )

    corpus = _strict_object(
        root_object.get("corpus"),
        frozenset(
            {
                "id",
                "fixture_index",
                "golden_index",
                "fidelity_case_ids",
                "semantic_cases",
                "outcome_cases",
            }
        ),
        "$.corpus",
        issues,
    )
    fixture_index: Mapping[str, object] | None = None
    golden_index: Mapping[str, object] | None = None
    fixture_index_sha256: str | None = None
    golden_index_sha256: str | None = None
    fidelity_cases: tuple[FidelityCaseRecord, ...] = ()
    semantic_cases: tuple[SemanticCaseRecord, ...] = ()
    outcome_cases: tuple[OutcomeCaseRecord, ...] = ()
    if corpus is not None:
        if corpus.get("id") != "paperworkshell-public-synthetic-v1":
            issues.append(
                BenchmarkIssue(
                    "BENCHMARK_CORPUS_IDENTITY",
                    "$.corpus.id",
                    "corpus identity is invalid",
                )
            )
        fixture_binding = corpus.get("fixture_index")
        golden_binding = corpus.get("golden_index")
        if isinstance(fixture_binding, dict):
            fixture_index, fixture_index_sha256 = _load_bound_json(
                root,
                cast(Mapping[str, object], fixture_binding),
                prefix="tests",
                location="$.corpus.fixture_index",
                issues=issues,
            )
        else:
            issues.append(
                BenchmarkIssue(
                    "BENCHMARK_FIELD_TYPE",
                    "$.corpus.fixture_index",
                    "must be an object",
                )
            )
        if isinstance(golden_binding, dict):
            golden_index, golden_index_sha256 = _load_bound_json(
                root,
                cast(Mapping[str, object], golden_binding),
                prefix="tests",
                location="$.corpus.golden_index",
                issues=issues,
            )
        else:
            issues.append(
                BenchmarkIssue(
                    "BENCHMARK_FIELD_TYPE",
                    "$.corpus.golden_index",
                    "must be an object",
                )
            )
        if fixture_index is not None and fixture_index.get("corpus_id") != corpus.get("id"):
            issues.append(
                BenchmarkIssue(
                    "BENCHMARK_FIXTURE_INDEX_IDENTITY",
                    "$.corpus.fixture_index",
                    "fixture index identity is invalid",
                )
            )
        if golden_index is not None and golden_index.get("source_corpus_id") != corpus.get("id"):
            issues.append(
                BenchmarkIssue(
                    "BENCHMARK_GOLDEN_INDEX_IDENTITY",
                    "$.corpus.golden_index",
                    "golden index identity is invalid",
                )
            )
        if fixture_index is not None and golden_index is not None:
            fidelity_cases = _parse_fidelity_cases(
                root,
                corpus.get("fidelity_case_ids"),
                fixture_index,
                golden_index,
                issues,
            )
            semantic_cases = _parse_semantic_cases(
                root,
                corpus.get("semantic_cases"),
                fixture_index,
                issues,
            )
            outcome_cases = _parse_outcome_cases(
                root,
                corpus.get("outcome_cases"),
                fixture_index,
                issues,
            )

    render_profile = _strict_object(
        root_object.get("render_profile"),
        frozenset(
            {
                "id",
                "adapter_version",
                "pixel_layout",
                "background",
                "annotations",
                "forms",
                "scripts",
                "maximum_width_pixels",
                "maximum_height_pixels",
                "maximum_pixels",
                "maximum_output_bytes",
            }
        ),
        "$.render_profile",
        issues,
    )
    profile_values: tuple[int, int, int, int] | None = None
    if render_profile is not None:
        identity = (
            render_profile.get("id"),
            render_profile.get("adapter_version"),
            render_profile.get("pixel_layout"),
            render_profile.get("background"),
            render_profile.get("annotations"),
            render_profile.get("forms"),
            render_profile.get("scripts"),
        )
        if identity != (
            "paperworkshell-pdf-renderer-rgba8-white-v1",
            1,
            "rgba8-top-down-packed",
            "opaque-white",
            "stored-appearances",
            "disabled",
            "disabled",
        ):
            issues.append(
                BenchmarkIssue(
                    "BENCHMARK_RENDER_PROFILE",
                    "$.render_profile",
                    "render profile identity is invalid",
                )
            )
        width = _required_int(
            render_profile.get("maximum_width_pixels"),
            minimum=1,
            maximum=16_384,
            location="$.render_profile.maximum_width_pixels",
            issues=issues,
        )
        height = _required_int(
            render_profile.get("maximum_height_pixels"),
            minimum=1,
            maximum=16_384,
            location="$.render_profile.maximum_height_pixels",
            issues=issues,
        )
        pixels = _required_int(
            render_profile.get("maximum_pixels"),
            minimum=1,
            maximum=268_435_456,
            location="$.render_profile.maximum_pixels",
            issues=issues,
        )
        output_bytes = _required_int(
            render_profile.get("maximum_output_bytes"),
            minimum=4,
            maximum=MAX_RENDER_OUTPUT_BYTES,
            location="$.render_profile.maximum_output_bytes",
            issues=issues,
        )
        if None not in {width, height, pixels, output_bytes}:
            profile_values = (
                cast(int, width),
                cast(int, height),
                cast(int, pixels),
                cast(int, output_bytes),
            )
            if profile_values[0] * profile_values[1] != profile_values[2]:
                issues.append(
                    BenchmarkIssue(
                        "BENCHMARK_RENDER_LIMITS",
                        "$.render_profile",
                        "pixel limit must match the fixed v1 dimension product",
                    )
                )
            if profile_values[2] * 4 != profile_values[3]:
                issues.append(
                    BenchmarkIssue(
                        "BENCHMARK_RENDER_LIMITS",
                        "$.render_profile",
                        "output limit must match packed RGBA8",
                    )
                )

    renderers = _parse_renderers(root_object.get("renderers"), issues)
    measurement = _parse_measurement(root_object.get("measurement"), issues)
    report_policy = _strict_object(
        root_object.get("report_policy"),
        frozenset(
            {
                "classification",
                "include_paths",
                "include_filenames",
                "include_machine_identifiers",
                "include_document_or_pixel_content",
            }
        ),
        "$.report_policy",
        issues,
    )
    if report_policy != {
        "classification": "public-synthetic-identifiers-and-measurements-only",
        "include_paths": False,
        "include_filenames": False,
        "include_machine_identifiers": False,
        "include_document_or_pixel_content": False,
    }:
        issues.append(
            BenchmarkIssue(
                "BENCHMARK_REPORT_POLICY",
                "$.report_policy",
                "report policy must remain content-free",
            )
        )

    if issues:
        return None, sorted(set(issues))
    assert reference_sha256 is not None
    assert reference_logical_processors is not None
    assert reference_memory_bytes is not None
    assert fixture_index_sha256 is not None
    assert golden_index_sha256 is not None
    assert render_profile is not None
    assert profile_values is not None
    assert measurement is not None
    return (
        BenchmarkWorkload(
            workload_id=cast(str, root_object["workload_id"]),
            workload_sha256=_sha256(workload_content),
            reference_profile_id="windows-low-spec-v1",
            reference_profile_sha256=reference_sha256,
            reference_logical_processors=reference_logical_processors,
            reference_memory_bytes=reference_memory_bytes,
            fixture_index_sha256=fixture_index_sha256,
            golden_index_sha256=golden_index_sha256,
            render_profile_id=cast(str, render_profile["id"]),
            maximum_width_pixels=profile_values[0],
            maximum_height_pixels=profile_values[1],
            maximum_pixels=profile_values[2],
            maximum_output_bytes=profile_values[3],
            fidelity_cases=fidelity_cases,
            semantic_cases=semantic_cases,
            outcome_cases=outcome_cases,
            renderers=renderers,
            measurement=measurement,
        ),
        [],
    )


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= upper_left_distance:
        return left
    return above if above_distance <= upper_left_distance else upper_left


def _decode_golden_rgb(case: FidelityCaseRecord) -> bytes:
    """Decode the closed RGB8 PNG subset emitted by the synthetic fixture generator."""

    try:
        content = case.golden_path.read_bytes()
    except OSError as error:
        raise _StableEngineError("PDF_RENDER_BENCHMARK_GOLDEN_UNREADABLE") from error
    if len(content) > MAX_RENDER_OUTPUT_BYTES or _sha256(content) != case.golden_sha256:
        raise _StableEngineError("PDF_RENDER_BENCHMARK_GOLDEN_CHANGED")
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise _StableEngineError("PDF_RENDER_BENCHMARK_GOLDEN_INVALID")
    offset = 8
    width = 0
    height = 0
    compressed = bytearray()
    saw_header = False
    saw_end = False
    while offset < len(content):
        if offset + 12 > len(content):
            raise _StableEngineError("PDF_RENDER_BENCHMARK_GOLDEN_INVALID")
        length = int.from_bytes(content[offset : offset + 4], "big")
        chunk_type = content[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        crc_end = data_end + 4
        if length > MAX_RENDER_OUTPUT_BYTES or crc_end > len(content):
            raise _StableEngineError("PDF_RENDER_BENCHMARK_GOLDEN_INVALID")
        data = content[data_start:data_end]
        expected_crc = int.from_bytes(content[data_end:crc_end], "big")
        if zlib.crc32(chunk_type + data) & 0xFFFFFFFF != expected_crc:
            raise _StableEngineError("PDF_RENDER_BENCHMARK_GOLDEN_INVALID")
        if chunk_type == b"IHDR":
            if saw_header or length != 13:
                raise _StableEngineError("PDF_RENDER_BENCHMARK_GOLDEN_INVALID")
            width = int.from_bytes(data[0:4], "big")
            height = int.from_bytes(data[4:8], "big")
            if data[8:] != bytes((8, 2, 0, 0, 0)):
                raise _StableEngineError("PDF_RENDER_BENCHMARK_GOLDEN_INVALID")
            saw_header = True
        elif chunk_type == b"IDAT":
            if not saw_header or len(compressed) + len(data) > MAX_RENDER_OUTPUT_BYTES:
                raise _StableEngineError("PDF_RENDER_BENCHMARK_GOLDEN_INVALID")
            compressed.extend(data)
        elif chunk_type == b"IEND":
            if length != 0:
                raise _StableEngineError("PDF_RENDER_BENCHMARK_GOLDEN_INVALID")
            saw_end = True
            offset = crc_end
            break
        offset = crc_end
    if (
        not saw_header
        or not saw_end
        or offset != len(content)
        or width != case.width_pixels
        or height != case.height_pixels
    ):
        raise _StableEngineError("PDF_RENDER_BENCHMARK_GOLDEN_INVALID")
    row_bytes = width * 3
    expected_inflated = height * (row_bytes + 1)
    try:
        decompressor = zlib.decompressobj()
        inflated = decompressor.decompress(bytes(compressed), expected_inflated + 1)
        if len(inflated) > expected_inflated or decompressor.unconsumed_tail:
            raise _StableEngineError("PDF_RENDER_BENCHMARK_GOLDEN_INVALID")
        # zlib's default flush is unbounded.  Permit at most the remaining expected bytes plus
        # one sentinel byte so an over-expanding IDAT stream cannot allocate past the PNG oracle.
        inflated += decompressor.flush(expected_inflated - len(inflated) + 1)
    except zlib.error as error:
        raise _StableEngineError("PDF_RENDER_BENCHMARK_GOLDEN_INVALID") from error
    if (
        len(inflated) != expected_inflated
        or decompressor.unused_data
        or decompressor.unconsumed_tail
        or not decompressor.eof
    ):
        raise _StableEngineError("PDF_RENDER_BENCHMARK_GOLDEN_INVALID")
    result = bytearray(height * row_bytes)
    previous = bytearray(row_bytes)
    source_offset = 0
    for row_index in range(height):
        filter_type = inflated[source_offset]
        source_offset += 1
        encoded = inflated[source_offset : source_offset + row_bytes]
        source_offset += row_bytes
        row = bytearray(row_bytes)
        for column, byte in enumerate(encoded):
            left = row[column - 3] if column >= 3 else 0
            above = previous[column]
            upper_left = previous[column - 3] if column >= 3 else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            elif filter_type == 4:
                predictor = _paeth(left, above, upper_left)
            else:
                raise _StableEngineError("PDF_RENDER_BENCHMARK_GOLDEN_INVALID")
            row[column] = (byte + predictor) & 0xFF
        target_offset = row_index * row_bytes
        result[target_offset : target_offset + row_bytes] = row
        previous = row
    return bytes(result)


def _fidelity_result(rendered_rgba: bytes, case: FidelityCaseRecord) -> dict[str, object]:
    expected_bytes = case.width_pixels * case.height_pixels * 4
    request = {
        "page_box": case.page_box,
        "maximum_width_pixels": case.maximum_width_pixels,
        "maximum_height_pixels": case.maximum_height_pixels,
        "width_pixels": case.width_pixels,
        "height_pixels": case.height_pixels,
    }
    if len(rendered_rgba) != expected_bytes:
        return {
            "case_id": case.case_id,
            "fixture_id": case.fixture_id,
            "page_index": case.page_index,
            "status": "invalid-output",
            "maximum_channel_delta": 255,
            "allowed_channel_delta": case.maximum_channel_delta,
            "differing_channels": case.width_pixels * case.height_pixels * 3,
            **request,
        }
    golden_rgb = _decode_golden_rgb(case)
    maximum_delta = 0
    differing_channels = 0
    golden_offset = 0
    for rendered_offset in range(0, len(rendered_rgba), 4):
        if rendered_rgba[rendered_offset + 3] != 255:
            maximum_delta = 255
            differing_channels += 1
        for channel in range(3):
            delta = abs(rendered_rgba[rendered_offset + channel] - golden_rgb[golden_offset])
            golden_offset += 1
            maximum_delta = max(maximum_delta, delta)
            if delta > case.maximum_channel_delta:
                differing_channels += 1
    return {
        "case_id": case.case_id,
        "fixture_id": case.fixture_id,
        "page_index": case.page_index,
        "status": "matched" if differing_channels == 0 else "different",
        "maximum_channel_delta": maximum_delta,
        "allowed_channel_delta": case.maximum_channel_delta,
        "differing_channels": differing_channels,
        **request,
    }


def _installed_record_sha256(distribution_name: str) -> str:
    try:
        distribution = importlib.metadata.distribution(distribution_name)
        record = distribution.read_text("RECORD")
    except (importlib.metadata.PackageNotFoundError, OSError) as error:
        raise BenchmarkFailure(
            OUTCOME_DEPENDENCY_UNAVAILABLE,
            "a required renderer dependency is unavailable",
        ) from error
    if not record:
        raise BenchmarkFailure(
            OUTCOME_DEPENDENCY_MISMATCH,
            "a renderer dependency has no installed artifact record",
        )
    return hashlib.sha256(record.encode("utf-8")).hexdigest()


def _packed_opaque_rgba(raw: bytes, width: int, height: int, stride: int) -> bytes:
    if width <= 0 or height <= 0 or stride < width * 4 or len(raw) != height * stride:
        raise _StableEngineError("PDF_RENDER_RESULT_INVALID")
    output = bytearray(width * height * 4)
    output_offset = 0
    for row_index in range(height):
        row_offset = row_index * stride
        for column in range(width):
            pixel_offset = row_offset + column * 4
            red, green, blue, alpha = raw[pixel_offset : pixel_offset + 4]
            inverse = 255 - alpha
            output[output_offset] = (red * alpha + 255 * inverse + 127) // 255
            output[output_offset + 1] = (green * alpha + 255 * inverse + 127) // 255
            output[output_offset + 2] = (blue * alpha + 255 * inverse + 127) // 255
            output[output_offset + 3] = 255
            output_offset += 4
    return bytes(output)


class _PypdfiumRuntime:
    def __init__(self, specification: RendererSpec) -> None:
        try:
            package_version = importlib.metadata.version(specification.package)
            pdfium = importlib.import_module("pypdfium2")
            version = importlib.import_module("pypdfium2.version")
            raw = importlib.import_module("pypdfium2.raw")
        except (ImportError, importlib.metadata.PackageNotFoundError) as error:
            raise BenchmarkFailure(
                OUTCOME_DEPENDENCY_UNAVAILABLE,
                "a required renderer dependency is unavailable",
            ) from error
        engine_version = str(version.PDFIUM_INFO)
        flags = tuple(cast(Sequence[str], version.PDFIUM_INFO.flags))
        if (
            package_version != specification.package_version
            or str(version.PYPDFIUM_INFO) != specification.package_version
            or engine_version != specification.engine_version
            or flags != specification.engine_feature_flags
        ):
            raise BenchmarkFailure(
                OUTCOME_DEPENDENCY_MISMATCH,
                "a renderer dependency does not match the versioned workload",
            )
        self._pdfium = pdfium
        self._raw = raw
        self.identity: Mapping[str, object] = {
            "renderer_id": specification.renderer_id,
            "package": specification.package,
            "package_version": package_version,
            "installed_record_sha256": _installed_record_sha256(specification.package),
            "declared_published_artifact_sha256": specification.artifact_sha256,
            "engine": specification.engine,
            "engine_version": engine_version,
            "engine_feature_flags": list(flags),
        }

    def open_document(self, source: Path) -> object:
        try:
            return cast(object, self._pdfium.PdfDocument(str(source)))
        except Exception as error:
            raise _StableEngineError(self._map_error(error)) from error

    def close_document(self, document: object) -> None:
        try:
            cast(Any, document).close()
        except Exception as error:
            raise _StableEngineError("PDF_RENDER_DEPENDENCY_FAILED") from error

    def render_page(self, document: object, case: FidelityCaseRecord) -> bytes:
        page: Any | None = None
        bitmap: Any | None = None
        original_crop_box: tuple[float, float, float, float] | None = None
        try:
            page = cast(Any, document)[case.page_index]
            if case.page_box == "media":
                media_values = tuple(cast(Sequence[float], page.get_mediabox()))
                crop_values = tuple(cast(Sequence[float], page.get_cropbox()))
                if len(media_values) != 4 or len(crop_values) != 4:
                    raise _StableEngineError("PDF_RENDER_GEOMETRY_INVALID")
                media_box = media_values
                original_crop_box = crop_values
                page.set_cropbox(*media_box)
            page_width, page_height = cast(tuple[float, float], page.get_size())
            if case.additional_rotation_degrees in {90, 270}:
                page_width, page_height = page_height, page_width
            if (
                not math.isfinite(page_width)
                or not math.isfinite(page_height)
                or page_width <= 0
                or page_height <= 0
            ):
                raise _StableEngineError("PDF_RENDER_GEOMETRY_INVALID")
            if not (
                math.isclose(page_width, case.oriented_width_units, rel_tol=1e-9, abs_tol=1e-6)
                and math.isclose(
                    page_height,
                    case.oriented_height_units,
                    rel_tol=1e-9,
                    abs_tol=1e-6,
                )
            ):
                raise _StableEngineError("PDF_RENDER_GEOMETRY_INVALID")
            fitted = _fit_dimensions(
                page_width,
                page_height,
                case.maximum_width_pixels,
                case.maximum_height_pixels,
            )
            if fitted != (case.width_pixels, case.height_pixels):
                raise _StableEngineError("PDF_RENDER_RESULT_INVALID")
            scale = min(
                case.maximum_width_pixels / page_width,
                case.maximum_height_pixels / page_height,
            )
            scale = math.nextafter(scale, 0.0)
            bitmap = page.render(
                scale=scale,
                rotation=case.additional_rotation_degrees,
                crop=(0, 0, 0, 0),
                may_draw_forms=False,
                fill_color=(255, 255, 255, 255),
                optimize_mode=None,
                draw_annots=True,
                no_smoothtext=False,
                no_smoothimage=False,
                no_smoothpath=False,
                force_halftone=False,
                rev_byteorder=True,
                force_bitmap_format=self._raw.FPDFBitmap_BGRA,
            )
            width = int(bitmap.width)
            height = int(bitmap.height)
            if width != case.width_pixels or height != case.height_pixels:
                raise _StableEngineError("PDF_RENDER_RESULT_INVALID")
            raw = bytes(bitmap.buffer)
            return _packed_opaque_rgba(raw, width, height, int(bitmap.stride))
        except _StableEngineError:
            raise
        except Exception as error:
            raise _StableEngineError(self._map_error(error)) from error
        finally:
            if bitmap is not None:
                try:
                    bitmap.close()
                except Exception:
                    pass
            if page is not None:
                restoration_error: Exception | None = None
                if original_crop_box is not None:
                    try:
                        page.set_cropbox(*original_crop_box)
                    except Exception as error:
                        restoration_error = error
                try:
                    page.close()
                except Exception:
                    pass
                if restoration_error is not None:
                    raise _StableEngineError("PDF_RENDER_DEPENDENCY_FAILED") from restoration_error

    def outcome_code(self, case: OutcomeCaseRecord) -> str:
        if case.maximum_width_pixels > 4_096 or case.maximum_height_pixels > 4_096:
            return "PDF_RENDER_LIMIT_EXCEEDED"
        document: object | None = None
        try:
            document = self.open_document(case.source_path)
            probe = FidelityCaseRecord(
                case_id=case.case_id,
                fixture_id=case.fixture_id,
                source_path=case.source_path,
                source_sha256=case.source_sha256,
                golden_path=case.source_path,
                golden_sha256=case.source_sha256,
                page_index=case.page_index,
                page_box="crop",
                page_box_relation="default-equivalent",
                additional_rotation_degrees=0,
                oriented_width_units=float(case.maximum_width_pixels),
                oriented_height_units=float(case.maximum_height_pixels),
                maximum_width_pixels=case.maximum_width_pixels,
                maximum_height_pixels=case.maximum_height_pixels,
                width_pixels=case.maximum_width_pixels,
                height_pixels=case.maximum_height_pixels,
                maximum_channel_delta=0,
            )
            self.render_page(document, probe)
        except _StableEngineError as error:
            return error.code
        finally:
            if document is not None:
                try:
                    self.close_document(document)
                except _StableEngineError:
                    pass
        return "PDF_RENDER_UNEXPECTED_SUCCESS"

    def _map_error(self, error: Exception) -> str:
        error_code = getattr(error, "err_code", None)
        if error_code == getattr(self._raw, "FPDF_ERR_PASSWORD", object()):
            return "PDF_RENDER_PASSWORD_REQUIRED"
        if error_code in {
            getattr(self._raw, "FPDF_ERR_FILE", object()),
            getattr(self._raw, "FPDF_ERR_FORMAT", object()),
            getattr(self._raw, "FPDF_ERR_PAGE", object()),
        }:
            return "PDF_RENDER_MALFORMED"
        return "PDF_RENDER_MALFORMED"


class _QtPdfRuntime:
    def __init__(self, specification: RendererSpec) -> None:
        pyside_specification = importlib.util.find_spec("PySide6")
        if (
            pyside_specification is None
            or pyside_specification.origin is None
            or pyside_specification.origin in {"built-in", "frozen"}
        ):
            raise BenchmarkFailure(
                OUTCOME_DEPENDENCY_UNAVAILABLE,
                "a required renderer dependency is unavailable",
            ) from None
        pyside_package_directory = Path(pyside_specification.origin).parent
        if (
            not pyside_package_directory.is_absolute()
            or not pyside_package_directory.is_dir()
            or _is_reparse_point(pyside_package_directory)
        ):
            raise BenchmarkFailure(
                OUTCOME_DEPENDENCY_UNAVAILABLE,
                "a required renderer dependency is unavailable",
            ) from None
        previous_path = os.environ.get("PATH")
        os.environ["PATH"] = os.fspath(pyside_package_directory)
        try:
            package_version = importlib.metadata.version(specification.package)
            essentials_version = importlib.metadata.version("PySide6-Essentials")
            shiboken_version = importlib.metadata.version("shiboken6")
            pyside = importlib.import_module("PySide6")
            qt_core = importlib.import_module("PySide6.QtCore")
            qt_gui = importlib.import_module("PySide6.QtGui")
            qt_pdf = importlib.import_module("PySide6.QtPdf")
        except (ImportError, importlib.metadata.PackageNotFoundError) as error:
            raise BenchmarkFailure(
                OUTCOME_DEPENDENCY_UNAVAILABLE,
                "a required renderer dependency is unavailable",
            ) from error
        finally:
            if previous_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = previous_path
        qt_version = str(qt_core.qVersion())
        if (
            package_version != specification.package_version
            or essentials_version != specification.package_version
            or shiboken_version != specification.package_version
            or str(pyside.__version__) != specification.package_version
            or qt_version != specification.engine_version
        ):
            raise BenchmarkFailure(
                OUTCOME_DEPENDENCY_MISMATCH,
                "a renderer dependency does not match the versioned workload",
            )
        application = qt_core.QCoreApplication.instance()
        self._application = application or qt_core.QCoreApplication([])
        self._core = qt_core
        self._gui = qt_gui
        self._pdf = qt_pdf
        self.identity: Mapping[str, object] = {
            "renderer_id": specification.renderer_id,
            "package": specification.package,
            "package_version": package_version,
            "installed_record_sha256": _installed_record_sha256(specification.package),
            "declared_published_artifact_sha256": specification.artifact_sha256,
            "engine": specification.engine,
            "engine_version": qt_version,
            "engine_feature_flags": specification.engine_feature_flags,
            "embedded_pdfium_identity": "not-exposed-by-public-api",
            "pyside6_essentials_version": essentials_version,
            "pyside6_essentials_record_sha256": _installed_record_sha256("PySide6-Essentials"),
            "shiboken6_version": shiboken_version,
            "shiboken6_record_sha256": _installed_record_sha256("shiboken6"),
        }

    def open_document(self, source: Path) -> object:
        document = self._pdf.QPdfDocument(None)
        try:
            error = document.load(str(source))
        except Exception as exception:
            document.close()
            raise _StableEngineError("PDF_RENDER_MALFORMED") from exception
        no_error = self._pdf.QPdfDocument.Error.None_
        if error != no_error:
            code = self._map_error(error)
            document.close()
            raise _StableEngineError(code)
        if document.status() != self._pdf.QPdfDocument.Status.Ready:
            document.close()
            raise _StableEngineError("PDF_RENDER_MALFORMED")
        return cast(object, document)

    def close_document(self, document: object) -> None:
        try:
            cast(Any, document).close()
        except Exception as error:
            raise _StableEngineError("PDF_RENDER_DEPENDENCY_FAILED") from error

    def render_page(self, document: object, case: FidelityCaseRecord) -> bytes:
        try:
            if case.page_box_relation == "distinct-from-crop" and case.page_box == "media":
                # Qt 6.11's public QPdfDocument API exposes no CropBox/MediaBox selector.  Record
                # that capability boundary instead of rendering the native default box under a
                # false MediaBox label.
                raise _StableEngineError(OUTCOME_PAGE_BOX_UNSUPPORTED)
            options = self._pdf.QPdfDocumentRenderOptions()
            options.setRenderFlags(self._pdf.QPdfDocumentRenderOptions.RenderFlag.Annotations)
            rotations = {
                0: self._pdf.QPdfDocumentRenderOptions.Rotation.None_,
                90: self._pdf.QPdfDocumentRenderOptions.Rotation.Clockwise90,
                180: self._pdf.QPdfDocumentRenderOptions.Rotation.Clockwise180,
                270: self._pdf.QPdfDocumentRenderOptions.Rotation.Clockwise270,
            }
            options.setRotation(rotations[case.additional_rotation_degrees])
            image = cast(Any, document).render(
                case.page_index,
                self._core.QSize(case.width_pixels, case.height_pixels),
                options,
            )
            if image.isNull():
                raise _StableEngineError("PDF_RENDER_MALFORMED")
            image = image.convertToFormat(self._gui.QImage.Format.Format_RGBA8888)
            width = int(image.width())
            height = int(image.height())
            stride = int(image.bytesPerLine())
            if width != case.width_pixels or height != case.height_pixels:
                raise _StableEngineError("PDF_RENDER_RESULT_INVALID")
            raw = bytes(image.constBits())
            return _packed_opaque_rgba(raw, width, height, stride)
        except _StableEngineError:
            raise
        except Exception as error:
            raise _StableEngineError("PDF_RENDER_DEPENDENCY_FAILED") from error

    def outcome_code(self, case: OutcomeCaseRecord) -> str:
        if case.maximum_width_pixels > 4_096 or case.maximum_height_pixels > 4_096:
            return "PDF_RENDER_LIMIT_EXCEEDED"
        document: object | None = None
        try:
            document = self.open_document(case.source_path)
            probe = FidelityCaseRecord(
                case_id=case.case_id,
                fixture_id=case.fixture_id,
                source_path=case.source_path,
                source_sha256=case.source_sha256,
                golden_path=case.source_path,
                golden_sha256=case.source_sha256,
                page_index=case.page_index,
                page_box="crop",
                page_box_relation="default-equivalent",
                additional_rotation_degrees=0,
                oriented_width_units=float(case.maximum_width_pixels),
                oriented_height_units=float(case.maximum_height_pixels),
                maximum_width_pixels=case.maximum_width_pixels,
                maximum_height_pixels=case.maximum_height_pixels,
                width_pixels=case.maximum_width_pixels,
                height_pixels=case.maximum_height_pixels,
                maximum_channel_delta=0,
            )
            self.render_page(document, probe)
        except _StableEngineError as error:
            return error.code
        finally:
            if document is not None:
                try:
                    self.close_document(document)
                except _StableEngineError:
                    pass
        return "PDF_RENDER_UNEXPECTED_SUCCESS"

    def _map_error(self, error: object) -> str:
        if error == self._pdf.QPdfDocument.Error.IncorrectPassword:
            return "PDF_RENDER_PASSWORD_REQUIRED"
        return "PDF_RENDER_MALFORMED"


def _load_runtime(renderer_id: str, workload: BenchmarkWorkload) -> RendererRuntime:
    specification = next(
        (renderer for renderer in workload.renderers if renderer.renderer_id == renderer_id),
        None,
    )
    if specification is None:
        raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "renderer ID is invalid")
    if renderer_id == "pypdfium2":
        return _PypdfiumRuntime(specification)
    if renderer_id == "qt-pdf":
        return _QtPdfRuntime(specification)
    raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "renderer ID is invalid")


def _scan_temporary_bytes_once(root: Path, maximum_bytes: int) -> int:
    total = 0
    entries = 0
    pending: list[tuple[Path, int]] = [(root, 0)]
    while pending:
        current, depth = pending.pop()
        if depth > MAX_TEMP_DEPTH:
            raise BenchmarkFailure(
                OUTCOME_TEMP_MEASUREMENT_FAILED,
                "isolated temporary-storage entry policy is invalid",
            )
        try:
            current_stat = current.lstat()
        except FileNotFoundError:
            raise
        except OSError:
            raise BenchmarkFailure(
                OUTCOME_TEMP_MEASUREMENT_FAILED,
                "isolated temporary-storage entry is unavailable",
            ) from None
        attributes = int(getattr(current_stat, "st_file_attributes", 0))
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise BenchmarkFailure(
                OUTCOME_TEMP_MEASUREMENT_FAILED,
                "isolated temporary-storage entry policy is invalid",
            )
        entries += 1
        if entries > MAX_TEMP_ENTRIES:
            raise BenchmarkFailure(
                OUTCOME_TEMP_MEASUREMENT_FAILED,
                "isolated temporary-storage budget was exceeded",
            )
        if stat.S_ISREG(current_stat.st_mode):
            total += current_stat.st_size
            if total > maximum_bytes:
                raise BenchmarkFailure(
                    OUTCOME_TEMP_MEASUREMENT_FAILED,
                    "isolated temporary-storage budget was exceeded",
                )
            continue
        if not stat.S_ISDIR(current_stat.st_mode):
            raise BenchmarkFailure(
                OUTCOME_TEMP_MEASUREMENT_FAILED,
                "isolated temporary-storage entry type is invalid",
            )
        try:
            pending.extend((child, depth + 1) for child in current.iterdir())
        except FileNotFoundError:
            raise
        except OSError:
            raise BenchmarkFailure(
                OUTCOME_TEMP_MEASUREMENT_FAILED,
                "isolated temporary-storage enumeration failed",
            ) from None
    return total


def _scan_temporary_bytes(root: Path, maximum_bytes: int) -> int:
    for attempt in range(MAX_TEMP_SCAN_ATTEMPTS):
        try:
            return _scan_temporary_bytes_once(root, maximum_bytes)
        except FileNotFoundError:
            if attempt + 1 == MAX_TEMP_SCAN_ATTEMPTS:
                raise BenchmarkFailure(
                    OUTCOME_TEMP_MEASUREMENT_FAILED,
                    "isolated temporary-storage entry is unavailable",
                ) from None
    raise AssertionError("temporary scan attempt bound is invalid")


class _TemporaryDirectorySampler:
    """Concurrent, bounded sampler for the isolated child's private TEMP root."""

    def __init__(self, poll_milliseconds: int, maximum_bytes: int) -> None:
        local_value = os.environ.get("LOCALAPPDATA")
        temp_value = os.environ.get("TEMP")
        tmp_value = os.environ.get("TMP")
        if (
            type(local_value) is not str
            or type(temp_value) is not str
            or type(tmp_value) is not str
            or not local_value
            or not temp_value
            or not tmp_value
            or any("\x00" in value for value in (local_value, temp_value, tmp_value))
        ):
            raise BenchmarkFailure(
                OUTCOME_TEMP_MEASUREMENT_FAILED,
                "isolated temporary-storage root is invalid",
            )
        local_root = Path(local_value)
        self._root = Path(temp_value)
        tmp_root = Path(tmp_value)
        if (
            not local_root.is_absolute()
            or not self._root.is_absolute()
            or not tmp_root.is_absolute()
            or os.path.normcase(os.path.abspath(self._root))
            != os.path.normcase(os.path.abspath(tmp_root))
            or os.path.normcase(os.path.abspath(self._root.parent))
            != os.path.normcase(os.path.abspath(local_root))
            or self._root.name.casefold() != "temp"
            or os.fspath(local_root).startswith(("\\\\", "\\\\?\\", "\\\\.\\", "\\??\\"))
        ):
            raise BenchmarkFailure(
                OUTCOME_TEMP_MEASUREMENT_FAILED,
                "isolated temporary-storage root is invalid",
            )
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            local_stat = local_root.lstat()
            root_stat = self._root.lstat()
        except OSError:
            raise BenchmarkFailure(
                OUTCOME_TEMP_MEASUREMENT_FAILED,
                "isolated temporary-storage root is unavailable",
            ) from None
        for information in (local_stat, root_stat):
            attributes = int(getattr(information, "st_file_attributes", 0))
            if not stat.S_ISDIR(information.st_mode) or attributes & getattr(
                stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
            ):
                raise BenchmarkFailure(
                    OUTCOME_TEMP_MEASUREMENT_FAILED,
                    "isolated temporary-storage root is redirected",
                )
        try:
            root_after = self._root.lstat()
        except OSError:
            raise BenchmarkFailure(
                OUTCOME_TEMP_MEASUREMENT_FAILED,
                "isolated temporary-storage root is unavailable",
            ) from None
        if not _same_file_identity(root_stat, root_after):
            raise BenchmarkFailure(
                OUTCOME_TEMP_MEASUREMENT_FAILED,
                "isolated temporary-storage root is unavailable",
            )
        self._interval = poll_milliseconds / 1_000
        self._maximum_bytes = maximum_bytes
        self._stop = threading.Event()
        self._high_water = 0
        self._failure: BenchmarkFailure | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._started = False

    def start(self) -> None:
        self._high_water = _scan_temporary_bytes(self._root, self._maximum_bytes)
        self._thread.start()
        self._started = True

    def stop(self) -> int:
        self._stop.set()
        if self._started:
            self._thread.join(timeout=1.0)
            if self._thread.is_alive():
                raise BenchmarkFailure(
                    OUTCOME_TEMP_MEASUREMENT_FAILED,
                    "isolated temporary-storage measurement failed",
                )
        if self._failure is not None:
            raise self._failure
        if not self._started:
            return self._high_water
        self._high_water = max(
            self._high_water,
            _scan_temporary_bytes(self._root, self._maximum_bytes),
        )
        return self._high_water

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                observed = _scan_temporary_bytes(self._root, self._maximum_bytes)
            except BenchmarkFailure as error:
                self._failure = error
                self._stop.set()
                return
            self._high_water = max(self._high_water, observed)


def _semantic_render_case(case: SemanticCaseRecord) -> FidelityCaseRecord:
    return FidelityCaseRecord(
        case_id=case.case_id,
        fixture_id=case.fixture_id,
        source_path=case.source_path,
        source_sha256=case.source_sha256,
        golden_path=case.source_path,
        golden_sha256=case.source_sha256,
        page_index=case.page_index,
        page_box=case.page_box,
        page_box_relation=case.page_box_relation,
        additional_rotation_degrees=case.additional_rotation_degrees,
        oriented_width_units=case.oriented_width_units,
        oriented_height_units=case.oriented_height_units,
        maximum_width_pixels=case.maximum_width_pixels,
        maximum_height_pixels=case.maximum_height_pixels,
        width_pixels=case.width_pixels,
        height_pixels=case.height_pixels,
        maximum_channel_delta=0,
    )


def _semantic_result(rendered_rgba: bytes, case: SemanticCaseRecord) -> dict[str, object]:
    expected_bytes = case.width_pixels * case.height_pixels * 4
    opaque = len(rendered_rgba) == expected_bytes and all(
        rendered_rgba[index] == 255 for index in range(3, len(rendered_rgba), 4)
    )
    nonwhite_pixels = 0
    minimum_x = case.width_pixels
    minimum_y = case.height_pixels
    maximum_x = -1
    maximum_y = -1
    if len(rendered_rgba) == expected_bytes:
        for y in range(case.height_pixels):
            for x in range(case.width_pixels):
                offset = (y * case.width_pixels + x) * 4
                if rendered_rgba[offset : offset + 3] != b"\xff\xff\xff":
                    nonwhite_pixels += 1
                    minimum_x = min(minimum_x, x)
                    minimum_y = min(minimum_y, y)
                    maximum_x = max(maximum_x, x)
                    maximum_y = max(maximum_y, y)
    bounded_nonwhite_region = (
        0 < nonwhite_pixels <= (case.width_pixels * case.height_pixels) // 2
        and 0 < minimum_x <= maximum_x < case.width_pixels - 1
        and 0 < minimum_y <= maximum_y < case.height_pixels - 1
    )
    semantic_match = opaque and (case.oracle != "bounded-opaque-rgba8" or bounded_nonwhite_region)
    return {
        "case_id": case.case_id,
        "fixture_id": case.fixture_id,
        "page_index": case.page_index,
        "page_box": case.page_box,
        "page_box_relation": case.page_box_relation,
        "oracle": case.oracle,
        "status": "matched" if semantic_match else "invalid-output",
        "outcome_code": "PDF_RENDER_OK" if semantic_match else "PDF_RENDER_RESULT_INVALID",
        "width_pixels": case.width_pixels,
        "height_pixels": case.height_pixels,
        "output_bytes": len(rendered_rgba),
        "opaque": opaque,
        "nonwhite_pixels": nonwhite_pixels,
        "bounded_nonwhite_region": bounded_nonwhite_region,
    }


def _semantic_error_result(
    case: SemanticCaseRecord,
    outcome_code: str,
) -> dict[str, object]:
    status = "unsupported" if outcome_code == OUTCOME_PAGE_BOX_UNSUPPORTED else "render-error"
    return {
        "case_id": case.case_id,
        "fixture_id": case.fixture_id,
        "page_index": case.page_index,
        "page_box": case.page_box,
        "page_box_relation": case.page_box_relation,
        "oracle": case.oracle,
        "status": status,
        "outcome_code": outcome_code,
        "width_pixels": case.width_pixels,
        "height_pixels": case.height_pixels,
        "output_bytes": 0,
        "opaque": False,
        "nonwhite_pixels": 0,
        "bounded_nonwhite_region": False,
    }


def _child_measure(renderer_id: str, workload: BenchmarkWorkload) -> dict[str, object]:
    import_started = time.perf_counter_ns()
    runtime = _load_runtime(renderer_id, workload)
    import_elapsed = time.perf_counter_ns() - import_started
    first_case = workload.fidelity_cases[0]
    documents: dict[str, object] = {}
    output_bytes_high_water = 0
    try:
        open_started = time.perf_counter_ns()
        first_document = runtime.open_document(first_case.source_path)
        open_elapsed = time.perf_counter_ns() - open_started
        documents[first_case.source_sha256] = first_document

        first_render_started = time.perf_counter_ns()
        first_output = runtime.render_page(first_document, first_case)
        first_page_elapsed = time.perf_counter_ns() - first_render_started
        output_bytes_high_water = len(first_output)
        fidelity_results: dict[str, dict[str, object]] = {
            first_case.case_id: _fidelity_result(first_output, first_case)
        }

        all_render_cases = (
            *workload.fidelity_cases,
            *(_semantic_render_case(case) for case in workload.semantic_cases),
        )
        for case in all_render_cases:
            if case.source_sha256 not in documents:
                documents[case.source_sha256] = runtime.open_document(case.source_path)

        warm_samples: list[dict[str, object]] = []
        semantic_results: dict[str, dict[str, object]] = {}
        semantic_by_id = {case.case_id: case for case in workload.semantic_cases}
        fidelity_by_id = {case.case_id: case for case in workload.fidelity_cases}
        for iteration in range(workload.measurement.warm_iterations):
            for case in all_render_cases:
                fidelity_case = fidelity_by_id.get(case.case_id)
                semantic_case = semantic_by_id.get(case.case_id)
                started = time.perf_counter_ns()
                try:
                    output = runtime.render_page(documents[case.source_sha256], case)
                    render_error: _StableEngineError | None = None
                except _StableEngineError as error:
                    if semantic_case is None:
                        raise
                    output = b""
                    render_error = error
                elapsed = time.perf_counter_ns() - started
                output_bytes_high_water = max(output_bytes_high_water, len(output))
                warm_samples.append(
                    {
                        "case_id": case.case_id,
                        "fixture_id": case.fixture_id,
                        "page_index": case.page_index,
                        "iteration": iteration,
                        "elapsed_ns": elapsed,
                    }
                )
                if iteration == 0:
                    if fidelity_case is not None:
                        fidelity_results[case.case_id] = _fidelity_result(
                            output,
                            fidelity_case,
                        )
                    elif semantic_case is not None:
                        semantic_results[case.case_id] = (
                            _semantic_result(output, semantic_case)
                            if render_error is None
                            else _semantic_error_result(semantic_case, render_error.code)
                        )

        outcomes = [
            {
                "case_id": case.case_id,
                "fixture_id": case.fixture_id,
                "expected_code": case.expected_code,
                "observed_code": runtime.outcome_code(case),
            }
            for case in workload.outcome_cases
        ]
        return {
            "protocol_version": 1,
            "event": "measurement",
            "renderer_id": renderer_id,
            "identity": dict(runtime.identity),
            "child_elapsed_ns": time.perf_counter_ns() - PROCESS_MODULE_STARTED_NS,
            "cold": {
                "import_elapsed_ns": import_elapsed,
                "document_open_elapsed_ns": open_elapsed,
                "first_page_elapsed_ns": first_page_elapsed,
                "first_case_id": first_case.case_id,
                "first_fixture_id": first_case.fixture_id,
                "first_page_index": first_case.page_index,
            },
            "warm_samples": warm_samples,
            "fidelity": [fidelity_results[case.case_id] for case in workload.fidelity_cases],
            "semantic_fidelity": [
                semantic_results[case.case_id] for case in workload.semantic_cases
            ],
            "outcomes": outcomes,
            "output_bytes_high_water": output_bytes_high_water,
        }
    finally:
        close_failed = False
        for document in reversed(tuple(documents.values())):
            try:
                runtime.close_document(document)
            except _StableEngineError:
                close_failed = True
        if close_failed and sys.exc_info()[0] is None:
            raise _StableEngineError("PDF_RENDER_DEPENDENCY_FAILED")


def _child_probe(renderer_id: str, workload: BenchmarkWorkload) -> dict[str, object]:
    started = time.perf_counter_ns()
    runtime = _load_runtime(renderer_id, workload)
    return {
        "protocol_version": 1,
        "event": "probe",
        "renderer_id": renderer_id,
        "identity": dict(runtime.identity),
        "import_elapsed_ns": time.perf_counter_ns() - started,
        "child_elapsed_ns": time.perf_counter_ns() - PROCESS_MODULE_STARTED_NS,
    }


def _process_affinity_count() -> int:
    if os.name != "nt":
        raise BenchmarkFailure(
            OUTCOME_PLATFORM_UNSUPPORTED, "the renderer comparison requires Windows"
        )
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.argtypes = ()
    get_current_process.restype = wintypes.HANDLE
    get_affinity = kernel32.GetProcessAffinityMask
    get_affinity.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.POINTER(ctypes.c_size_t),
    )
    get_affinity.restype = wintypes.BOOL
    process_mask = ctypes.c_size_t()
    system_mask = ctypes.c_size_t()
    if not get_affinity(
        get_current_process(), ctypes.byref(process_mask), ctypes.byref(system_mask)
    ):
        raise BenchmarkFailure(OUTCOME_CHILD_FAILED, "renderer child failed")
    count = int(process_mask.value).bit_count()
    if count < 1 or process_mask.value & ~system_mask.value:
        raise BenchmarkFailure(OUTCOME_CHILD_FAILED, "renderer child failed")
    return count


def _limit_process_affinity_count(logical_processors: int) -> int:
    if os.name != "nt" or type(logical_processors) is not int or logical_processors < 1:
        raise BenchmarkFailure(OUTCOME_CHILD_FAILED, "renderer child failed")
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.argtypes = ()
    get_current_process.restype = wintypes.HANDLE
    get_affinity = kernel32.GetProcessAffinityMask
    get_affinity.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.POINTER(ctypes.c_size_t),
    )
    get_affinity.restype = wintypes.BOOL
    set_affinity = kernel32.SetProcessAffinityMask
    set_affinity.argtypes = (wintypes.HANDLE, ctypes.c_size_t)
    set_affinity.restype = wintypes.BOOL
    process = get_current_process()
    process_mask = ctypes.c_size_t()
    system_mask = ctypes.c_size_t()
    if not get_affinity(process, ctypes.byref(process_mask), ctypes.byref(system_mask)):
        raise BenchmarkFailure(OUTCOME_CHILD_FAILED, "renderer child failed")
    available_mask = int(process_mask.value)
    if available_mask.bit_count() < logical_processors or available_mask & ~system_mask.value:
        raise BenchmarkFailure(OUTCOME_CHILD_FAILED, "renderer child failed")
    selected_mask = 0
    remaining_mask = available_mask
    for _index in range(logical_processors):
        lowest_available_bit = remaining_mask & -remaining_mask
        selected_mask |= lowest_available_bit
        remaining_mask &= ~lowest_available_bit
    if not set_affinity(process, ctypes.c_size_t(selected_mask)):
        raise BenchmarkFailure(OUTCOME_CHILD_FAILED, "renderer child failed")
    count = _process_affinity_count()
    if count != logical_processors:
        raise BenchmarkFailure(OUTCOME_CHILD_FAILED, "renderer child failed")
    return count


def _write_child_payload(writer: Any, payload: Mapping[str, object]) -> None:
    from paperworkshell.worker.protocol.framing import write_frame

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
        "ascii"
    )
    write_frame(writer, encoded)


def _read_child_payload(reader: Any) -> Mapping[str, object]:
    from paperworkshell.worker.protocol.framing import read_frame

    raw = read_frame(reader)
    if raw is None:
        raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "renderer child protocol is invalid")
    decoded = _decode_json_bytes(raw)
    if not isinstance(decoded, dict):
        raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "renderer child protocol is invalid")
    return cast(Mapping[str, object], decoded)


def _child_ready_runtime(
    renderer_id: str,
    workload: BenchmarkWorkload,
) -> tuple[RendererRuntime, object]:
    runtime = _load_runtime(renderer_id, workload)
    first = workload.fidelity_cases[0]
    document = runtime.open_document(first.source_path)
    try:
        runtime.render_page(document, first)
    except BaseException:
        runtime.close_document(document)
        raise
    return runtime, document


def _child_cancel(
    renderer_id: str,
    workload: BenchmarkWorkload,
    correlation_id: str,
    reader: Any,
    writer: Any,
) -> Mapping[str, object]:
    runtime, document = _child_ready_runtime(renderer_id, workload)
    try:
        _write_child_payload(
            writer,
            {
                "protocol_version": 1,
                "event": "ready-for-cooperative-cancel",
                "renderer_id": renderer_id,
                "correlation_id": correlation_id,
                "checkpoint": "between-native-renders",
            },
        )
        received = _read_child_payload(reader)
        if received != {
            "protocol_version": 1,
            "event": "cancel",
            "correlation_id": correlation_id,
            "checkpoint": "between-native-renders",
        }:
            raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "cancel command is invalid")
        return {
            "protocol_version": 1,
            "event": "cancelled",
            "renderer_id": renderer_id,
            "correlation_id": correlation_id,
            "outcome_code": OUTCOME_CANCELLED,
            "checkpoint": "between-native-renders",
        }
    finally:
        runtime.close_document(document)


def _child_idle_wait(
    renderer_id: str,
    workload: BenchmarkWorkload,
    correlation_id: str,
    writer: Any,
) -> NoReturn:
    runtime, document = _child_ready_runtime(renderer_id, workload)
    try:
        _write_child_payload(
            writer,
            {
                "protocol_version": 1,
                "event": "ready-for-idle-worker-tree-termination",
                "renderer_id": renderer_id,
                "correlation_id": correlation_id,
            },
        )
        threading.Event().wait(600)
        raise BenchmarkFailure(OUTCOME_CHILD_FAILED, "renderer child failed")
    finally:
        runtime.close_document(document)


def _isolated_child_main(
    data_root: Path,
    isolation: Any,
    correlation_id: str,
    reader: Any,
    writer: Any,
) -> int:
    """Validate staged inputs and serve exactly one framed benchmark command."""

    if CORRELATION_PATTERN.fullmatch(correlation_id) is None:
        return 2
    try:
        workload, issues = validate_workload(data_root)
    except BaseException:
        _write_child_payload(
            writer,
            {
                "protocol_version": 1,
                "event": "error",
                "correlation_id": correlation_id,
                "code": "PDF_RENDER_BENCHMARK_WORKLOAD_INVALID",
                "message": "the versioned renderer workload is invalid",
            },
        )
        return 2
    if issues or workload is None:
        _write_child_payload(
            writer,
            {
                "protocol_version": 1,
                "event": "error",
                "correlation_id": correlation_id,
                "code": "PDF_RENDER_BENCHMARK_WORKLOAD_INVALID",
                "message": "the versioned renderer workload is invalid",
            },
        )
        return 2
    sampler: _TemporaryDirectorySampler | None = None
    sampler_stopped = False
    try:
        process_affinity_count = _limit_process_affinity_count(
            workload.reference_logical_processors
        )
        _write_child_payload(
            writer,
            {
                "protocol_version": 1,
                "event": "isolated-worker-ready",
                "correlation_id": correlation_id,
                "workload_sha256": workload.workload_sha256,
                "process_affinity_count": process_affinity_count,
                "isolation": {
                    "app_container": isolation.app_container,
                    "zero_capabilities": isolation.zero_capabilities,
                    "in_job": isolation.in_job,
                    "minimal_launch_environment": isolation.minimal_environment,
                    "standard_handles_non_inheritable": (
                        isolation.standard_handles_non_inheritable
                    ),
                },
            },
        )
        command = _read_child_payload(reader)
        if set(command) != {
            "protocol_version",
            "command",
            "mode",
            "renderer_id",
            "correlation_id",
        }:
            raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "child mode is invalid")
        if (
            command.get("protocol_version") != 1
            or command.get("command") != "run"
            or command.get("correlation_id") != correlation_id
        ):
            raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "child mode is invalid")
        mode = command.get("mode")
        renderer_id = command.get("renderer_id")
        if renderer_id not in RENDERER_IDS:
            raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "renderer ID is invalid")
        if mode not in {"probe", "measure", "cancel", "idle"}:
            raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "child mode is invalid")
        sampler = _TemporaryDirectorySampler(
            workload.measurement.private_bytes_poll_milliseconds,
            workload.maximum_output_bytes,
        )
        sampler.start()
        if mode == "probe":
            payload = _child_probe(renderer_id, workload)
            payload["correlation_id"] = correlation_id
            sampler.stop()
            sampler_stopped = True
            _write_child_payload(writer, payload)
            return 0
        if mode == "measure":
            payload = _child_measure(renderer_id, workload)
            payload["correlation_id"] = correlation_id
            payload["temporary_bytes_high_water"] = sampler.stop()
            sampler_stopped = True
            _write_child_payload(writer, payload)
            return 0
        if mode == "cancel":
            payload = dict(_child_cancel(renderer_id, workload, correlation_id, reader, writer))
            payload["temporary_bytes_high_water"] = sampler.stop()
            sampler_stopped = True
            _write_child_payload(writer, payload)
            return 0
        _child_idle_wait(renderer_id, workload, correlation_id, writer)
    except BenchmarkFailure as error:
        failure = error
        if sampler is not None and not sampler_stopped:
            try:
                sampler.stop()
            except BaseException:
                pass
        allowed_messages = CHILD_ERROR_MESSAGES.get(failure.code)
        safe_code = (
            failure.code
            if allowed_messages is not None and failure.message in allowed_messages
            else OUTCOME_CHILD_FAILED
        )
        safe_message = failure.message if safe_code == failure.code else "renderer child failed"
        _write_child_payload(
            writer,
            {
                "protocol_version": 1,
                "event": "error",
                "correlation_id": correlation_id,
                "code": safe_code,
                "message": safe_message,
            },
        )
        return 2
    except _StableEngineError as error:
        if sampler is not None and not sampler_stopped:
            try:
                sampler.stop()
            except BaseException:
                pass
        _write_child_payload(
            writer,
            {
                "protocol_version": 1,
                "event": "error",
                "correlation_id": correlation_id,
                "code": error.code,
                "message": "renderer child returned a stable failure",
            },
        )
        return 2
    except BaseException:
        if sampler is not None and not sampler_stopped:
            try:
                sampler.stop()
            except BaseException:
                pass
        _write_child_payload(
            writer,
            {
                "protocol_version": 1,
                "event": "error",
                "correlation_id": correlation_id,
                "code": OUTCOME_CHILD_FAILED,
                "message": "renderer child failed",
            },
        )
        return 2


def _decode_transport_payload(
    raw: bytes,
    *,
    expected_event: str,
    renderer_id: str | None,
    correlation_id: str,
) -> Mapping[str, object]:
    try:
        payload = _decode_json_bytes(raw)
    except BenchmarkFailure as error:
        raise BenchmarkFailure(
            OUTCOME_PROTOCOL_INVALID,
            "renderer child protocol is invalid",
        ) from error
    if not isinstance(payload, dict):
        raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "renderer child protocol is invalid")
    result = cast(Mapping[str, object], payload)
    if result.get("protocol_version") != 1:
        raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "renderer child protocol is invalid")
    if result.get("event") == "error":
        code = result.get("code")
        message = result.get("message")
        allowed_messages = None
        if isinstance(code, str):
            allowed_messages = (
                frozenset({"renderer child returned a stable failure"})
                if code in STABLE_RENDER_OUTCOME_CODES
                else CHILD_ERROR_MESSAGES.get(code)
            )
        if (
            set(result) != {"protocol_version", "event", "correlation_id", "code", "message"}
            or result.get("correlation_id") != correlation_id
            or not isinstance(code, str)
            or not isinstance(message, str)
            or allowed_messages is None
            or message not in allowed_messages
        ):
            raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "renderer child protocol is invalid")
        raise BenchmarkFailure(code, message)
    if (
        result.get("event") != expected_event
        or result.get("correlation_id") != correlation_id
        or (renderer_id is not None and result.get("renderer_id") != renderer_id)
    ):
        raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "renderer child protocol is invalid")
    return result


def _receive_transport(process: Any, timeout_milliseconds: int) -> bytes:
    from paperworkshell.worker.client.process import TransportReceiveKind

    try:
        received = process.transport.receive_payload(timeout_milliseconds / 1_000)
    except Exception:
        raise BenchmarkFailure(
            OUTCOME_CHILD_FAILED,
            "renderer child communication failed",
        ) from None
    if received.kind is TransportReceiveKind.TIMED_OUT:
        raise BenchmarkFailure(OUTCOME_CHILD_TIMEOUT, "renderer child timed out")
    if received.kind is not TransportReceiveKind.PAYLOAD or received.payload is None:
        raise BenchmarkFailure(OUTCOME_CHILD_FAILED, "renderer child failed")
    return cast(bytes, received.payload)


def _send_transport(process: Any, payload: Mapping[str, object]) -> None:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    try:
        process.transport.send_payload(encoded)
    except Exception:
        raise BenchmarkFailure(
            OUTCOME_CHILD_FAILED,
            "renderer child communication failed",
        ) from None


@dataclass(frozen=True, slots=True)
class _IsolatedChildSession:
    process: Any
    correlation_id: str
    isolated_worker_ready_elapsed_ns: int
    process_affinity_count: int


def _validate_reference_affinity(
    affinity: object,
    workload: BenchmarkWorkload,
) -> int:
    if type(affinity) is not int or affinity != workload.reference_logical_processors:
        raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "renderer child protocol is invalid")
    return affinity


def _close_and_verify_isolated_process(
    process: Any | None,
    resources: StagedBenchmarkResources,
    primary_failure: BaseException | None,
    *,
    cleanup_uncertain: bool = False,
) -> None:
    """Close one worker and revalidate the retained seal after profile revocation."""

    cleanup_cause: BaseException | None = (
        BenchmarkFailure(OUTCOME_CLEANUP_FAILED, "benchmark cleanup failed")
        if cleanup_uncertain
        else None
    )
    authority_withdrawn = False

    def withdraw() -> None:
        nonlocal authority_withdrawn, cleanup_cause
        if authority_withdrawn:
            return
        try:
            _withdraw_stage_cleanup_authority(resources)
        except BaseException as error:
            if cleanup_cause is None:
                cleanup_cause = error
        else:
            authority_withdrawn = True

    if cleanup_uncertain:
        withdraw()
    if process is not None:
        try:
            process.close()
        except BaseException as error:
            cleanup_cause = error
            withdraw()
    try:
        _require_staged_namespace_sealed(resources)
    except BaseException as error:
        if cleanup_cause is None:
            cleanup_cause = error
        withdraw()
    if cleanup_cause is None:
        return
    withdraw()
    if primary_failure is not None:
        _propagate_cleanup_failure(primary_failure)
        return
    if not isinstance(cleanup_cause, Exception):
        raise cleanup_cause
    raise BenchmarkFailure(
        OUTCOME_CLEANUP_FAILED,
        "benchmark cleanup failed",
    ) from cleanup_cause


def _start_isolated_child(
    resources: StagedBenchmarkResources,
    workload: BenchmarkWorkload,
    timeout_milliseconds: int,
) -> _IsolatedChildSession:
    from paperworkshell.worker.client._windows import (
        WindowsLaunchConfig,
        WindowsProcessLauncher,
    )
    from paperworkshell.worker.client.process import WorkerLaunchRequest, WorkerResourcePolicy
    from paperworkshell.worker.protocol.models import SessionCapability

    process: Any | None = None
    launch_attempted = False
    started = time.perf_counter_ns()
    try:
        _require_stage_cleanup_authorized(resources)
        _require_staged_namespace_sealed(resources)
        worker_instance_id = uuid4()
        correlation_id = worker_instance_id.hex
        config = WindowsLaunchConfig(
            python_executable=resources.python_executable,
            bootstrap_script=resources.bootstrap_script,
            runtime_roots=(resources.runtime_root,),
            code_roots=(resources.stage_root,),
            working_directory=resources.code_root,
        )
        request = WorkerLaunchRequest(
            case_id=uuid4(),
            session_id=uuid4(),
            worker_instance_id=worker_instance_id,
            session_capability=SessionCapability(secrets.token_hex(32)),
            resource_policy=WorkerResourcePolicy(
                process_memory_bytes=workload.reference_memory_bytes,
                job_memory_bytes=workload.reference_memory_bytes,
                cpu_time_ms=max(1, workload.measurement.child_timeout_milliseconds * 4),
                maximum_processes=1,
            ),
        )
        launcher = WindowsProcessLauncher(config)
        launch_attempted = True
        process = launcher.launch(request)
        _require_stage_cleanup_authorized(resources)
        _require_staged_namespace_sealed(resources)
        if not process.isolation.satisfies_required_boundary:
            raise BenchmarkFailure(OUTCOME_CHILD_FAILED, "renderer child isolation failed")
        _send_transport(
            process,
            {
                "protocol_version": 1,
                "event": "stage-auth",
                "correlation_id": correlation_id,
                "manifest_sha256": resources.manifest_sha256,
            },
        )
        ready = _decode_transport_payload(
            _receive_transport(process, timeout_milliseconds),
            expected_event="isolated-worker-ready",
            renderer_id=None,
            correlation_id=correlation_id,
        )
        ready_elapsed = time.perf_counter_ns() - started
        expected_isolation = {
            "app_container": True,
            "zero_capabilities": True,
            "in_job": True,
            "minimal_launch_environment": True,
            "standard_handles_non_inheritable": True,
        }
        affinity = ready.get("process_affinity_count")
        if (
            set(ready)
            != {
                "protocol_version",
                "event",
                "correlation_id",
                "workload_sha256",
                "process_affinity_count",
                "isolation",
            }
            or ready.get("workload_sha256") != workload.workload_sha256
            or ready.get("isolation") != expected_isolation
        ):
            raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "renderer child protocol is invalid")
        affinity = _validate_reference_affinity(affinity, workload)
        return _IsolatedChildSession(
            process,
            correlation_id,
            ready_elapsed,
            affinity,
        )
    except BenchmarkFailure as error:
        _close_and_verify_isolated_process(
            process,
            resources,
            error,
            cleanup_uncertain=launch_attempted and process is None,
        )
        raise
    except Exception as cause:
        failure = BenchmarkFailure(OUTCOME_CHILD_FAILED, "renderer child could not start")
        _close_and_verify_isolated_process(
            process,
            resources,
            failure,
            cleanup_uncertain=launch_attempted and process is None,
        )
        raise failure from cause
    except BaseException as error:
        _close_and_verify_isolated_process(
            process,
            resources,
            error,
            cleanup_uncertain=launch_attempted and process is None,
        )
        raise


def _validated_resource_snapshot(
    process: Any,
    workload: BenchmarkWorkload,
) -> tuple[int, int, int, int]:
    try:
        snapshot = process.resource_snapshot()
    except Exception:
        raise BenchmarkFailure(OUTCOME_CHILD_FAILED, "renderer resource evidence failed") from None
    values = (
        snapshot.process_memory_limit_bytes,
        snapshot.job_memory_limit_bytes,
        snapshot.peak_process_memory_used_bytes,
        snapshot.peak_job_memory_used_bytes,
    )
    if (
        any(type(value) is not int for value in values)
        or values[0] != workload.reference_memory_bytes
        or values[1] != workload.reference_memory_bytes
        or not 0 < values[2] <= values[0]
        or not 0 < values[3] <= values[1]
        or values[3] < values[2]
    ):
        raise BenchmarkFailure(OUTCOME_CHILD_FAILED, "renderer resource evidence failed")
    return values


def _finish_isolated_child(
    session: _IsolatedChildSession,
    workload: BenchmarkWorkload,
    timeout_milliseconds: int,
) -> tuple[int, int, int, int]:
    from paperworkshell.worker.client.process import TransportReceiveKind

    process = session.process
    returncode = process.wait(timeout_milliseconds / 1_000)
    if returncode is None:
        raise BenchmarkFailure(OUTCOME_CHILD_TIMEOUT, "renderer child timed out")
    if returncode != 0:
        raise BenchmarkFailure(OUTCOME_CHILD_FAILED, "renderer child failed")
    try:
        trailing = process.transport.receive_payload(1.0)
    except Exception:
        raise BenchmarkFailure(
            OUTCOME_CHILD_FAILED,
            "renderer child communication failed",
        ) from None
    if trailing.kind is not TransportReceiveKind.CLOSED:
        raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "renderer child protocol is invalid")
    return _validated_resource_snapshot(process, workload)


def _capture_child(
    renderer_id: str,
    mode: str,
    timeout_milliseconds: int,
    resources: StagedBenchmarkResources,
    workload: BenchmarkWorkload,
) -> ChildCapture:
    parent_started = time.perf_counter_ns()
    session = _start_isolated_child(resources, workload, timeout_milliseconds)
    process = session.process
    primary_failure: BaseException | None = None
    try:
        _validate_reference_affinity(session.process_affinity_count, workload)
        _send_transport(
            process,
            {
                "protocol_version": 1,
                "command": "run",
                "mode": mode,
                "renderer_id": renderer_id,
                "correlation_id": session.correlation_id,
            },
        )
        expected_event = "measurement" if mode == "measure" else mode
        payload = _decode_transport_payload(
            _receive_transport(process, timeout_milliseconds),
            expected_event=expected_event,
            renderer_id=renderer_id,
            correlation_id=session.correlation_id,
        )
        limits = _finish_isolated_child(session, workload, timeout_milliseconds)
        parent_observed_elapsed = time.perf_counter_ns() - parent_started
    except BaseException as error:
        primary_failure = error
        raise
    finally:
        _close_and_verify_isolated_process(process, resources, primary_failure)
    return ChildCapture(
        payload=payload,
        correlation_id=session.correlation_id,
        isolated_worker_ready_elapsed_ns=session.isolated_worker_ready_elapsed_ns,
        parent_observed_elapsed_ns=parent_observed_elapsed,
        process_affinity_count=session.process_affinity_count,
        process_memory_limit_bytes=limits[0],
        job_memory_limit_bytes=limits[1],
        peak_process_memory_used_bytes=limits[2],
        peak_job_memory_used_bytes=limits[3],
    )


def _measure_cooperative_cancellation(
    renderer_id: str,
    workload: BenchmarkWorkload,
    resources: StagedBenchmarkResources,
) -> dict[str, object]:
    timeout = workload.measurement.cooperative_cancel_timeout_milliseconds
    session = _start_isolated_child(resources, workload, timeout)
    process = session.process
    primary_failure: BaseException | None = None
    try:
        _validate_reference_affinity(session.process_affinity_count, workload)
        _send_transport(
            process,
            {
                "protocol_version": 1,
                "command": "run",
                "mode": "cancel",
                "renderer_id": renderer_id,
                "correlation_id": session.correlation_id,
            },
        )
        ready = _decode_transport_payload(
            _receive_transport(process, timeout),
            expected_event="ready-for-cooperative-cancel",
            renderer_id=renderer_id,
            correlation_id=session.correlation_id,
        )
        if (
            set(ready)
            != {
                "protocol_version",
                "event",
                "renderer_id",
                "correlation_id",
                "checkpoint",
            }
            or ready.get("checkpoint") != "between-native-renders"
        ):
            raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "renderer child protocol is invalid")
        started = time.perf_counter_ns()
        _send_transport(
            process,
            {
                "protocol_version": 1,
                "event": "cancel",
                "correlation_id": session.correlation_id,
                "checkpoint": "between-native-renders",
            },
        )
        cancelled = _decode_transport_payload(
            _receive_transport(process, timeout),
            expected_event="cancelled",
            renderer_id=renderer_id,
            correlation_id=session.correlation_id,
        )
        latency = time.perf_counter_ns() - started
        temporary_bytes = cancelled.get("temporary_bytes_high_water")
        if (
            set(cancelled)
            != {
                "protocol_version",
                "event",
                "renderer_id",
                "correlation_id",
                "outcome_code",
                "checkpoint",
                "temporary_bytes_high_water",
            }
            or cancelled.get("outcome_code") != OUTCOME_CANCELLED
            or cancelled.get("checkpoint") != "between-native-renders"
            or type(temporary_bytes) is not int
            or not 0 <= temporary_bytes <= workload.maximum_output_bytes
        ):
            raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "renderer child protocol is invalid")
        limits = _finish_isolated_child(session, workload, timeout)
        return {
            "outcome_code": OUTCOME_CANCELLED,
            "checkpoint": "between-native-renders",
            "latency_ns": latency,
            "isolated_worker_ready_elapsed_ns": session.isolated_worker_ready_elapsed_ns,
            "process_affinity_count": session.process_affinity_count,
            "process_memory_limit_bytes": limits[0],
            "job_memory_limit_bytes": limits[1],
            "peak_process_memory_used_bytes": limits[2],
            "peak_job_memory_used_bytes": limits[3],
            "temporary_bytes_high_water": temporary_bytes,
        }
    except BaseException as error:
        primary_failure = error
        raise
    finally:
        _close_and_verify_isolated_process(process, resources, primary_failure)


def _measure_idle_worker_tree_termination(
    renderer_id: str,
    workload: BenchmarkWorkload,
    resources: StagedBenchmarkResources,
) -> dict[str, object]:
    timeout = workload.measurement.hard_termination_timeout_milliseconds
    session = _start_isolated_child(resources, workload, timeout)
    process = session.process
    primary_failure: BaseException | None = None
    try:
        _validate_reference_affinity(session.process_affinity_count, workload)
        _send_transport(
            process,
            {
                "protocol_version": 1,
                "command": "run",
                "mode": "idle",
                "renderer_id": renderer_id,
                "correlation_id": session.correlation_id,
            },
        )
        ready = _decode_transport_payload(
            _receive_transport(process, timeout),
            expected_event="ready-for-idle-worker-tree-termination",
            renderer_id=renderer_id,
            correlation_id=session.correlation_id,
        )
        if set(ready) != {
            "protocol_version",
            "event",
            "renderer_id",
            "correlation_id",
        }:
            raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "renderer child protocol is invalid")
        if process.poll() is not None:
            raise BenchmarkFailure(OUTCOME_CHILD_FAILED, "renderer child failed")
        _validated_resource_snapshot(process, workload)
        started = time.perf_counter_ns()
        process.terminate_tree()
        returncode = process.wait(timeout / 1_000)
        latency = time.perf_counter_ns() - started
        if returncode is None:
            raise BenchmarkFailure(OUTCOME_CHILD_TIMEOUT, "renderer child timed out")
        if returncode == 0:
            raise BenchmarkFailure(OUTCOME_CHILD_FAILED, "renderer child failed")
        limits = _validated_resource_snapshot(process, workload)
        return {
            "outcome_code": OUTCOME_JOB_TERMINATED,
            "boundary": "windows-appcontainer-job-tree",
            "latency_ns": latency,
            "termination_exit_code": returncode,
            "isolated_worker_ready_elapsed_ns": session.isolated_worker_ready_elapsed_ns,
            "process_affinity_count": session.process_affinity_count,
            "process_memory_limit_bytes": limits[0],
            "job_memory_limit_bytes": limits[1],
            "peak_process_memory_used_bytes": limits[2],
            "peak_job_memory_used_bytes": limits[3],
        }
    except BaseException as error:
        primary_failure = error
        raise
    finally:
        _close_and_verify_isolated_process(process, resources, primary_failure)


def _nearest_rank(values: Sequence[int], percentile: int) -> int:
    if not values or not 1 <= percentile <= 100:
        raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "warm measurements are invalid")
    ordered = sorted(values)
    rank = math.ceil(percentile * len(ordered) / 100)
    return ordered[rank - 1]


def summarize_warm_samples(
    samples: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Summarize validated warm samples with deterministic nearest-rank percentiles."""

    if not samples:
        raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "warm measurements are invalid")
    elapsed_values: list[int] = []
    grouped: dict[tuple[str, str, int], list[int]] = {}
    maximum_sample: Mapping[str, object] | None = None
    maximum_elapsed = -1
    for sample in samples:
        case_id = sample.get("case_id")
        fixture_id = sample.get("fixture_id")
        page_index = sample.get("page_index")
        elapsed = sample.get("elapsed_ns")
        if (
            not isinstance(case_id, str)
            or not isinstance(fixture_id, str)
            or type(page_index) is not int
            or type(elapsed) is not int
            or elapsed < 0
        ):
            raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "warm measurements are invalid")
        elapsed_values.append(elapsed)
        grouped.setdefault((case_id, fixture_id, page_index), []).append(elapsed)
        if elapsed > maximum_elapsed:
            maximum_elapsed = elapsed
            maximum_sample = sample
    assert maximum_sample is not None
    per_case = []
    for (case_id, fixture_id, page_index), durations in grouped.items():
        per_case.append(
            {
                "case_id": case_id,
                "fixture_id": fixture_id,
                "page_index": page_index,
                "sample_count": len(durations),
                "p50_elapsed_ns": _nearest_rank(durations, 50),
                "p95_elapsed_ns": _nearest_rank(durations, 95),
                "maximum_elapsed_ns": max(durations),
            }
        )
    return {
        "sample_count": len(elapsed_values),
        "percentile_method": "nearest-rank",
        "p50_elapsed_ns": _nearest_rank(elapsed_values, 50),
        "p95_elapsed_ns": _nearest_rank(elapsed_values, 95),
        "maximum_elapsed_ns": maximum_elapsed,
        "maximum_attribution": {
            "case_id": cast(str, maximum_sample["case_id"]),
            "fixture_id": cast(str, maximum_sample["fixture_id"]),
            "page_index": cast(int, maximum_sample["page_index"]),
            "iteration": cast(int, maximum_sample["iteration"]),
        },
        "per_case": per_case,
    }


def _validate_identity(
    value: object,
    specification: RendererSpec,
) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "renderer identity is invalid")
    identity = cast(Mapping[str, object], value)
    expected_keys = {
        "renderer_id",
        "package",
        "package_version",
        "installed_record_sha256",
        "declared_published_artifact_sha256",
        "engine",
        "engine_version",
        "engine_feature_flags",
    }
    if specification.renderer_id == "qt-pdf":
        expected_keys.update(
            {
                "embedded_pdfium_identity",
                "pyside6_essentials_version",
                "pyside6_essentials_record_sha256",
                "shiboken6_version",
                "shiboken6_record_sha256",
            }
        )
    if set(identity) != expected_keys:
        raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "renderer identity is invalid")
    expected_values = {
        "renderer_id": specification.renderer_id,
        "package": specification.package,
        "package_version": specification.package_version,
        "declared_published_artifact_sha256": specification.artifact_sha256,
        "engine": specification.engine,
        "engine_version": specification.engine_version,
        "engine_feature_flags": (
            list(specification.engine_feature_flags)
            if isinstance(specification.engine_feature_flags, tuple)
            else specification.engine_feature_flags
        ),
    }
    if any(identity.get(key) != value for key, value in expected_values.items()):
        raise BenchmarkFailure(OUTCOME_DEPENDENCY_MISMATCH, "renderer identity is mismatched")
    if not _hash_is_valid(identity.get("installed_record_sha256")):
        raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "renderer identity is invalid")
    if specification.renderer_id == "qt-pdf" and (
        identity.get("embedded_pdfium_identity") != "not-exposed-by-public-api"
        or identity.get("pyside6_essentials_version") != specification.package_version
        or identity.get("shiboken6_version") != specification.package_version
        or not _hash_is_valid(identity.get("pyside6_essentials_record_sha256"))
        or not _hash_is_valid(identity.get("shiboken6_record_sha256"))
    ):
        raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "renderer identity is invalid")
    return identity


def _validate_stage_record_identity(
    identity: Mapping[str, object],
    specification: RendererSpec,
    resources: StagedBenchmarkResources,
) -> None:
    records = dict(resources.installed_record_sha256)
    if identity.get("installed_record_sha256") != records.get(specification.package):
        raise BenchmarkFailure(
            OUTCOME_DEPENDENCY_MISMATCH,
            "renderer identity is mismatched",
        )
    if specification.renderer_id == "qt-pdf" and (
        identity.get("pyside6_essentials_record_sha256") != records.get("PySide6-Essentials")
        or identity.get("shiboken6_record_sha256") != records.get("shiboken6")
    ):
        raise BenchmarkFailure(
            OUTCOME_DEPENDENCY_MISMATCH,
            "renderer identity is mismatched",
        )


def _validated_measurement_result(
    capture: ChildCapture,
    specification: RendererSpec,
    workload: BenchmarkWorkload,
) -> dict[str, object]:
    payload = capture.payload
    expected_keys = {
        "protocol_version",
        "event",
        "renderer_id",
        "correlation_id",
        "identity",
        "child_elapsed_ns",
        "cold",
        "warm_samples",
        "fidelity",
        "semantic_fidelity",
        "outcomes",
        "output_bytes_high_water",
        "temporary_bytes_high_water",
    }
    if (
        set(payload) != expected_keys
        or payload.get("protocol_version") != 1
        or payload.get("event") != "measurement"
        or payload.get("renderer_id") != specification.renderer_id
        or payload.get("correlation_id") != capture.correlation_id
    ):
        raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "measurement payload is invalid")
    identity = _validate_identity(payload.get("identity"), specification)
    child_elapsed = payload.get("child_elapsed_ns")
    output_high_water = payload.get("output_bytes_high_water")
    temporary_high_water = payload.get("temporary_bytes_high_water")
    _validate_reference_affinity(capture.process_affinity_count, workload)
    capture_resource_values = (
        capture.isolated_worker_ready_elapsed_ns,
        capture.parent_observed_elapsed_ns,
        capture.process_memory_limit_bytes,
        capture.job_memory_limit_bytes,
        capture.peak_process_memory_used_bytes,
        capture.peak_job_memory_used_bytes,
    )
    if (
        type(child_elapsed) is not int
        or child_elapsed <= 0
        or type(output_high_water) is not int
        or not 0 < output_high_water <= workload.maximum_output_bytes
        or type(temporary_high_water) is not int
        or not 0 <= temporary_high_water <= workload.maximum_output_bytes
        or any(type(value) is not int for value in capture_resource_values)
        or capture.isolated_worker_ready_elapsed_ns <= 0
        or capture.parent_observed_elapsed_ns <= 0
        or capture.parent_observed_elapsed_ns < capture.isolated_worker_ready_elapsed_ns
        or child_elapsed > capture.parent_observed_elapsed_ns
        or capture.process_memory_limit_bytes != workload.reference_memory_bytes
        or capture.job_memory_limit_bytes != workload.reference_memory_bytes
        or not 0 < capture.peak_process_memory_used_bytes <= capture.process_memory_limit_bytes
        or not 0 < capture.peak_job_memory_used_bytes <= capture.job_memory_limit_bytes
        or capture.peak_job_memory_used_bytes < capture.peak_process_memory_used_bytes
    ):
        raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "measurement payload is invalid")
    cold_value = payload.get("cold")
    if not isinstance(cold_value, dict) or set(cold_value) != {
        "import_elapsed_ns",
        "document_open_elapsed_ns",
        "first_page_elapsed_ns",
        "first_case_id",
        "first_fixture_id",
        "first_page_index",
    }:
        raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "cold measurements are invalid")
    cold = cast(Mapping[str, object], cold_value)
    first_case = workload.fidelity_cases[0]
    if (
        cold.get("first_case_id") != first_case.case_id
        or cold.get("first_fixture_id") != first_case.fixture_id
        or type(cold.get("first_page_index")) is not int
        or cold.get("first_page_index") != first_case.page_index
        or any(
            type(cold.get(key)) is not int or cast(int, cold[key]) <= 0
            for key in (
                "import_elapsed_ns",
                "document_open_elapsed_ns",
                "first_page_elapsed_ns",
            )
        )
    ):
        raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "cold measurements are invalid")
    samples_value = payload.get("warm_samples")
    if not isinstance(samples_value, list):
        raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "warm measurements are invalid")
    samples = cast(list[Mapping[str, object]], samples_value)
    render_cases = (
        *workload.fidelity_cases,
        *(_semantic_render_case(case) for case in workload.semantic_cases),
    )
    expected_count = len(render_cases) * workload.measurement.warm_iterations
    if len(samples) != expected_count:
        raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "warm measurements are invalid")
    warm_elapsed_total = 0
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict) or set(sample) != {
            "case_id",
            "fixture_id",
            "page_index",
            "iteration",
            "elapsed_ns",
        }:
            raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "warm measurements are invalid")
        iteration, case_offset = divmod(index, len(render_cases))
        expected_case = render_cases[case_offset]
        if (
            sample.get("case_id") != expected_case.case_id
            or sample.get("fixture_id") != expected_case.fixture_id
            or type(sample.get("page_index")) is not int
            or sample.get("page_index") != expected_case.page_index
            or type(sample.get("iteration")) is not int
            or sample.get("iteration") != iteration
            or type(sample.get("elapsed_ns")) is not int
            or cast(int, sample["elapsed_ns"]) <= 0
        ):
            raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "warm measurements are invalid")
        warm_elapsed_total += cast(int, sample["elapsed_ns"])
    cold_elapsed_total = sum(
        cast(int, cold[key])
        for key in (
            "import_elapsed_ns",
            "document_open_elapsed_ns",
            "first_page_elapsed_ns",
        )
    )
    if child_elapsed < cold_elapsed_total + warm_elapsed_total:
        raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "warm measurements are invalid")

    fidelity_value = payload.get("fidelity")
    if not isinstance(fidelity_value, list) or len(fidelity_value) != len(workload.fidelity_cases):
        raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "fidelity measurements are invalid")
    fidelity: list[Mapping[str, object]] = []
    known_output_bytes: list[int] = []
    for result, case in zip(fidelity_value, workload.fidelity_cases, strict=True):
        if not isinstance(result, dict) or set(result) != {
            "case_id",
            "fixture_id",
            "page_index",
            "status",
            "maximum_channel_delta",
            "allowed_channel_delta",
            "differing_channels",
            "page_box",
            "maximum_width_pixels",
            "maximum_height_pixels",
            "width_pixels",
            "height_pixels",
        }:
            raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "fidelity measurements are invalid")
        if (
            result.get("case_id") != case.case_id
            or result.get("fixture_id") != case.fixture_id
            or type(result.get("page_index")) is not int
            or result.get("page_index") != case.page_index
            or result.get("page_box") != case.page_box
            or type(result.get("maximum_width_pixels")) is not int
            or result.get("maximum_width_pixels") != case.maximum_width_pixels
            or type(result.get("maximum_height_pixels")) is not int
            or result.get("maximum_height_pixels") != case.maximum_height_pixels
            or type(result.get("width_pixels")) is not int
            or result.get("width_pixels") != case.width_pixels
            or type(result.get("height_pixels")) is not int
            or result.get("height_pixels") != case.height_pixels
            or result.get("status") not in {"matched", "different", "invalid-output"}
            or type(result.get("allowed_channel_delta")) is not int
            or result.get("allowed_channel_delta") != case.maximum_channel_delta
            or type(result.get("maximum_channel_delta")) is not int
            or type(result.get("differing_channels")) is not int
        ):
            raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "fidelity measurements are invalid")
        status = cast(str, result["status"])
        maximum_delta = cast(int, result["maximum_channel_delta"])
        differing_channels = cast(int, result["differing_channels"])
        pixel_count = case.width_pixels * case.height_pixels
        if (
            not 0 <= maximum_delta <= 255
            or not 0 <= differing_channels <= pixel_count * 4
            or (
                status == "matched"
                and not (differing_channels == 0 and maximum_delta <= case.maximum_channel_delta)
            )
            or (
                status == "different"
                and not (differing_channels > 0 and maximum_delta > case.maximum_channel_delta)
            )
            or (
                status == "invalid-output"
                and not (maximum_delta == 255 and differing_channels == pixel_count * 3)
            )
        ):
            raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "fidelity measurements are invalid")
        if status != "invalid-output":
            known_output_bytes.append(pixel_count * 4)
        fidelity.append(cast(Mapping[str, object], result))

    semantic_value = payload.get("semantic_fidelity")
    if not isinstance(semantic_value, list) or len(semantic_value) != len(workload.semantic_cases):
        raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "semantic measurements are invalid")
    semantic_fidelity: list[Mapping[str, object]] = []
    for result, semantic_case in zip(semantic_value, workload.semantic_cases, strict=True):
        if not isinstance(result, dict) or set(result) != {
            "case_id",
            "fixture_id",
            "page_index",
            "page_box",
            "page_box_relation",
            "oracle",
            "status",
            "outcome_code",
            "width_pixels",
            "height_pixels",
            "output_bytes",
            "opaque",
            "nonwhite_pixels",
            "bounded_nonwhite_region",
        }:
            raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "semantic measurements are invalid")
        if (
            result.get("case_id") != semantic_case.case_id
            or result.get("fixture_id") != semantic_case.fixture_id
            or type(result.get("page_index")) is not int
            or result.get("page_index") != semantic_case.page_index
            or result.get("page_box") != semantic_case.page_box
            or result.get("page_box_relation") != semantic_case.page_box_relation
            or result.get("oracle") != semantic_case.oracle
            or result.get("outcome_code") not in STABLE_RENDER_OUTCOME_CODES
            or result.get("status")
            not in {"matched", "invalid-output", "unsupported", "render-error"}
            or type(result.get("width_pixels")) is not int
            or result.get("width_pixels") != semantic_case.width_pixels
            or type(result.get("height_pixels")) is not int
            or result.get("height_pixels") != semantic_case.height_pixels
            or type(result.get("output_bytes")) is not int
            or type(result.get("opaque")) is not bool
            or type(result.get("nonwhite_pixels")) is not int
            or type(result.get("bounded_nonwhite_region")) is not bool
        ):
            raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "semantic measurements are invalid")
        status = cast(str, result["status"])
        outcome_code = cast(str, result["outcome_code"])
        output_bytes = cast(int, result["output_bytes"])
        opaque = cast(bool, result["opaque"])
        nonwhite_pixels = cast(int, result["nonwhite_pixels"])
        bounded_nonwhite_region = cast(bool, result["bounded_nonwhite_region"])
        pixel_count = semantic_case.width_pixels * semantic_case.height_pixels
        expected_output_bytes = pixel_count * 4
        if (
            not 0 <= output_bytes <= workload.maximum_output_bytes
            or not 0 <= nonwhite_pixels <= pixel_count
            or (
                output_bytes != expected_output_bytes
                and (opaque or nonwhite_pixels != 0 or bounded_nonwhite_region)
            )
            or (bounded_nonwhite_region and not 0 < nonwhite_pixels <= pixel_count // 2)
        ):
            raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "semantic measurements are invalid")
        oracle_matched = opaque and (
            semantic_case.oracle != "bounded-opaque-rgba8" or bounded_nonwhite_region
        )
        empty_error_output = (
            output_bytes == 0
            and not opaque
            and nonwhite_pixels == 0
            and not bounded_nonwhite_region
        )
        if (
            (
                status == "matched"
                and not (
                    outcome_code == "PDF_RENDER_OK"
                    and output_bytes == expected_output_bytes
                    and oracle_matched
                )
            )
            or (
                status == "invalid-output"
                and not (outcome_code == "PDF_RENDER_RESULT_INVALID" and not oracle_matched)
            )
            or (
                status == "unsupported"
                and not (outcome_code == OUTCOME_PAGE_BOX_UNSUPPORTED and empty_error_output)
            )
            or (
                status == "render-error"
                and not (
                    outcome_code
                    not in {
                        "PDF_RENDER_OK",
                        "PDF_RENDER_RESULT_INVALID",
                        OUTCOME_PAGE_BOX_UNSUPPORTED,
                    }
                    and empty_error_output
                )
            )
        ):
            raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "semantic measurements are invalid")
        known_output_bytes.append(output_bytes)
        semantic_fidelity.append(cast(Mapping[str, object], result))

    outcomes_value = payload.get("outcomes")
    if not isinstance(outcomes_value, list) or len(outcomes_value) != len(workload.outcome_cases):
        raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "outcome measurements are invalid")
    outcomes: list[Mapping[str, object]] = []
    for result, outcome_case in zip(outcomes_value, workload.outcome_cases, strict=True):
        if not isinstance(result, dict) or set(result) != {
            "case_id",
            "fixture_id",
            "expected_code",
            "observed_code",
        }:
            raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "outcome measurements are invalid")
        if (
            result.get("case_id") != outcome_case.case_id
            or result.get("fixture_id") != outcome_case.fixture_id
            or result.get("expected_code") != outcome_case.expected_code
            or not isinstance(result.get("observed_code"), str)
            or result.get("observed_code") not in STABLE_RENDER_OUTCOME_CODES
        ):
            raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "outcome measurements are invalid")
        outcomes.append(cast(Mapping[str, object], result))

    minimum_output_high_water = max(known_output_bytes, default=0)
    all_render_oracles_matched = all(
        result["status"] == "matched" for result in (*fidelity, *semantic_fidelity)
    )
    if output_high_water < minimum_output_high_water or (
        all_render_oracles_matched and output_high_water != minimum_output_high_water
    ):
        raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "measurement payload is invalid")

    return {
        "renderer_id": specification.renderer_id,
        "identity": dict(identity),
        "cold": {
            "isolated_worker_ready_elapsed_ns": capture.isolated_worker_ready_elapsed_ns,
            "parent_observed_total_elapsed_ns": capture.parent_observed_elapsed_ns,
            "import_elapsed_ns": cold["import_elapsed_ns"],
            "document_open_elapsed_ns": cold["document_open_elapsed_ns"],
            "first_page_elapsed_ns": cold["first_page_elapsed_ns"],
            "first_case_id": first_case.case_id,
            "first_fixture_id": first_case.fixture_id,
            "first_page_index": first_case.page_index,
        },
        "warm": summarize_warm_samples(samples),
        "fidelity": [dict(result) for result in fidelity],
        "semantic_fidelity": [dict(result) for result in semantic_fidelity],
        "outcomes": [dict(result) for result in outcomes],
        "resources": {
            "process_affinity_count": capture.process_affinity_count,
            "process_memory_limit_bytes": capture.process_memory_limit_bytes,
            "job_memory_limit_bytes": capture.job_memory_limit_bytes,
            "peak_process_memory_used_bytes": capture.peak_process_memory_used_bytes,
            "peak_job_memory_used_bytes": capture.peak_job_memory_used_bytes,
            "output_bytes_high_water": output_high_water,
            "temporary_bytes_high_water": temporary_high_water,
        },
    }


def _validate_probe(
    capture: ChildCapture,
    specification: RendererSpec,
) -> Mapping[str, object]:
    payload = capture.payload
    if (
        set(payload)
        != {
            "protocol_version",
            "event",
            "renderer_id",
            "correlation_id",
            "identity",
            "import_elapsed_ns",
            "child_elapsed_ns",
        }
        or payload.get("correlation_id") != capture.correlation_id
        or type(capture.parent_observed_elapsed_ns) is not int
        or capture.parent_observed_elapsed_ns <= 0
    ):
        raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "renderer probe is invalid")
    for key in ("import_elapsed_ns", "child_elapsed_ns"):
        if type(payload.get(key)) is not int or cast(int, payload[key]) <= 0:
            raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "renderer probe is invalid")
    if cast(int, payload["child_elapsed_ns"]) > capture.parent_observed_elapsed_ns:
        raise BenchmarkFailure(OUTCOME_PROTOCOL_INVALID, "renderer probe is invalid")
    return _validate_identity(payload.get("identity"), specification)


def _run_renderer_suite(
    specification: RendererSpec,
    workload: BenchmarkWorkload,
    resources: StagedBenchmarkResources,
    probe_identity: Mapping[str, object],
) -> dict[str, object]:
    measurement_capture = _capture_child(
        specification.renderer_id,
        "measure",
        workload.measurement.child_timeout_milliseconds,
        resources,
        workload,
    )
    result = _validated_measurement_result(measurement_capture, specification, workload)
    if result["identity"] != dict(probe_identity):
        raise BenchmarkFailure(OUTCOME_DEPENDENCY_MISMATCH, "renderer identity changed")
    cancellation = _measure_cooperative_cancellation(
        specification.renderer_id,
        workload,
        resources,
    )
    idle_termination = _measure_idle_worker_tree_termination(
        specification.renderer_id,
        workload,
        resources,
    )
    result["cooperative_cancellation"] = cancellation
    result["idle_worker_tree_termination"] = idle_termination
    return result


def _repository_revision() -> str:
    git = shutil.which("git")
    if git is None:
        raise BenchmarkFailure(
            "PDF_RENDER_BENCHMARK_TOOL_UNAVAILABLE",
            "Git is required to bind the benchmark revision",
        )
    try:
        revision = subprocess.run(  # noqa: S603 - fixed Git command
            (git, "rev-parse", "--verify", "HEAD"),
            cwd=PROJECT_ROOT,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
        status = subprocess.run(  # noqa: S603 - fixed Git command
            (git, "status", "--porcelain=v1", "--untracked-files=normal"),
            cwd=PROJECT_ROOT,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BenchmarkFailure(
            "PDF_RENDER_BENCHMARK_TOOL_FAILED",
            "Git revision binding failed",
        ) from error
    try:
        revision_text = revision.stdout.decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise BenchmarkFailure(
            "PDF_RENDER_BENCHMARK_TOOL_FAILED",
            "Git revision binding failed",
        ) from error
    if (
        revision.returncode != 0
        or status.returncode != 0
        or len(revision.stdout) > 128
        or len(status.stdout) > MAX_JSON_BYTES
        or re.fullmatch(r"[0-9a-f]{40,64}", revision_text) is None
    ):
        raise BenchmarkFailure(
            "PDF_RENDER_BENCHMARK_TOOL_FAILED",
            "Git revision binding failed",
        )
    if status.stdout:
        raise BenchmarkFailure(
            OUTCOME_REPOSITORY_DIRTY,
            "the benchmark requires a clean repository tree",
        )
    return revision_text


def _require_same_repository_revision(expected: str) -> None:
    if _repository_revision() != expected:
        raise BenchmarkFailure(
            OUTCOME_REPOSITORY_DIRTY,
            "the benchmark requires a clean repository tree",
        )


@dataclass(frozen=True, slots=True)
class BenchmarkAttestations:
    memory_mib: int | None = None
    graphics_class: str | None = None
    storage_media: str | None = None


def _reference_environment(
    scenario_id: str,
    work_directory: Path,
    attestations: BenchmarkAttestations,
) -> dict[str, object]:
    try:
        reference = importlib.import_module("tools.reference_profile")
        profile, issues = reference.validate_profile()
        if issues or profile is None:
            raise BenchmarkFailure(
                "PDF_RENDER_BENCHMARK_REFERENCE_INVALID",
                "the reference profile is invalid",
            )
        observations = cast(
            Mapping[str, object],
            reference._observed_environment(work_directory),
        )
        reference_attestations = reference.ReferenceAttestations(
            memory_mib=attestations.memory_mib,
            graphics_class=attestations.graphics_class,
            storage_media=attestations.storage_media,
        )
        evaluation = cast(
            Mapping[str, object],
            reference._evaluate_reference_environment(
                profile,
                scenario_id,
                observations,
                reference_attestations,
            ),
        )
    except BenchmarkFailure:
        raise
    except Exception as error:
        raise BenchmarkFailure(
            "PDF_RENDER_BENCHMARK_REFERENCE_INVALID",
            "the reference profile could not be evaluated",
        ) from error
    if evaluation.get("status") == "mismatched":
        raise BenchmarkFailure(
            "PDF_RENDER_BENCHMARK_REFERENCE_MISMATCH",
            "the selected environment does not match the reference profile",
        )
    return {
        "profile_id": "windows-low-spec-v1",
        "scenario_id": scenario_id,
        "evaluation": dict(evaluation),
        "observations": dict(observations),
    }


def _windows_runtime_identity() -> dict[str, object]:
    if sys.platform != "win32" or not hasattr(sys, "getwindowsversion"):
        raise BenchmarkFailure(
            OUTCOME_PLATFORM_UNSUPPORTED,
            "the renderer comparison requires Windows",
        )
    windows = sys.getwindowsversion()
    return {
        "family": "Windows",
        "version": f"{windows.major}.{windows.minor}.{windows.build}",
        "platform_version": ".".join(str(part) for part in windows.platform_version),
        "service_pack_major": int(windows.service_pack_major),
        "service_pack_minor": int(windows.service_pack_minor),
        "product_type": int(windows.product_type),
    }


def _comparison_summary(
    renderer_results: Sequence[Mapping[str, object]],
    workload: BenchmarkWorkload,
) -> dict[str, object]:
    fidelity_matrix = []
    for index, case in enumerate(workload.fidelity_cases):
        statuses = {
            cast(str, result["renderer_id"]): cast(list[Mapping[str, object]], result["fidelity"])[
                index
            ]["status"]
            for result in renderer_results
        }
        fidelity_matrix.append(
            {
                "case_id": case.case_id,
                "fixture_id": case.fixture_id,
                "page_index": case.page_index,
                "page_box": case.page_box,
                "maximum_width_pixels": case.maximum_width_pixels,
                "maximum_height_pixels": case.maximum_height_pixels,
                "width_pixels": case.width_pixels,
                "height_pixels": case.height_pixels,
                "renderer_status": statuses,
            }
        )
    semantic_matrix = []
    for index, semantic_case in enumerate(workload.semantic_cases):
        outcomes = {
            cast(str, result["renderer_id"]): cast(
                list[Mapping[str, object]], result["semantic_fidelity"]
            )[index]["outcome_code"]
            for result in renderer_results
        }
        semantic_matrix.append(
            {
                "case_id": semantic_case.case_id,
                "fixture_id": semantic_case.fixture_id,
                "page_box": semantic_case.page_box,
                "page_box_relation": semantic_case.page_box_relation,
                "maximum_width_pixels": semantic_case.maximum_width_pixels,
                "maximum_height_pixels": semantic_case.maximum_height_pixels,
                "width_pixels": semantic_case.width_pixels,
                "height_pixels": semantic_case.height_pixels,
                "oracle": semantic_case.oracle,
                "renderer_outcome": outcomes,
            }
        )
    outcome_matrix = []
    for index, outcome_case in enumerate(workload.outcome_cases):
        outcomes = {
            cast(str, result["renderer_id"]): cast(list[Mapping[str, object]], result["outcomes"])[
                index
            ]["observed_code"]
            for result in renderer_results
        }
        outcome_matrix.append(
            {
                "case_id": outcome_case.case_id,
                "fixture_id": outcome_case.fixture_id,
                "expected_code": outcome_case.expected_code,
                "renderer_outcome": outcomes,
            }
        )
    fidelity_passed = all(
        status == "matched"
        for item in fidelity_matrix
        for status in cast(Mapping[str, object], item["renderer_status"]).values()
    )
    semantic_passed = all(
        outcome == workload.semantic_cases[index].expected_code
        for index, item in enumerate(semantic_matrix)
        for outcome in cast(Mapping[str, object], item["renderer_outcome"]).values()
    )
    outcomes_passed = all(
        outcome == workload.outcome_cases[index].expected_code
        for index, item in enumerate(outcome_matrix)
        for outcome in cast(Mapping[str, object], item["renderer_outcome"]).values()
    )
    return {
        "status": "complete",
        "selection_decision": "not-automated",
        "oracle_status": (
            "passed"
            if fidelity_passed and semantic_passed and outcomes_passed
            else "differences-recorded"
        ),
        "fidelity_matrix": fidelity_matrix,
        "semantic_fidelity_matrix": semantic_matrix,
        "stable_outcome_matrix": outcome_matrix,
    }


def assert_sanitized_report(report: Mapping[str, object]) -> None:
    """Reject path, filename, host identity, or document-content channels in a report."""

    prohibited_key_fragments = (
        "path",
        "filename",
        "hostname",
        "username",
        "machine_id",
        "document_content",
        "pixel_content",
        "pixel_digest",
        "output_digest",
    )

    def inspect(value: object, key: str = "") -> None:
        folded_key = key.casefold()
        if any(fragment in folded_key for fragment in prohibited_key_fragments):
            raise BenchmarkFailure(
                "PDF_RENDER_BENCHMARK_REPORT_UNSAFE",
                "benchmark report contains a prohibited field",
            )
        if value is None or type(value) in {bool, int, float}:
            if isinstance(value, float) and not math.isfinite(value):
                raise BenchmarkFailure(
                    "PDF_RENDER_BENCHMARK_REPORT_UNSAFE",
                    "benchmark report contains a non-finite number",
                )
            return
        if isinstance(value, str):
            folded = value.casefold()
            if (
                len(value) > 256
                or any(ord(character) < 32 for character in value)
                or DRIVE_PATH_PATTERN.search(value) is not None
                or "\\" in value
                or "/" in value
                or folded.endswith((".pdf", ".png", ".whl"))
                or value.startswith(("//", "\\\\"))
            ):
                raise BenchmarkFailure(
                    "PDF_RENDER_BENCHMARK_REPORT_UNSAFE",
                    "benchmark report contains a prohibited value",
                )
            return
        if isinstance(value, Mapping):
            for nested_key, nested_value in value.items():
                if not isinstance(nested_key, str):
                    raise BenchmarkFailure(
                        "PDF_RENDER_BENCHMARK_REPORT_UNSAFE",
                        "benchmark report contains a non-string field",
                    )
                inspect(nested_value, nested_key)
            return
        if isinstance(value, (list, tuple)):
            for nested in value:
                inspect(nested)
            return
        raise BenchmarkFailure(
            "PDF_RENDER_BENCHMARK_REPORT_UNSAFE",
            "benchmark report contains an unsupported value",
        )

    inspect(report)


def benchmark_report(
    workload: BenchmarkWorkload,
    *,
    scenario_id: str,
    work_directory: Path,
    attestations: BenchmarkAttestations,
) -> dict[str, object]:
    """Run both child-only renderers and return one sanitized comparison report."""

    if sys.platform != "win32":
        raise BenchmarkFailure(
            OUTCOME_PLATFORM_UNSUPPORTED,
            "the renderer comparison requires Windows",
        )
    if scenario_id not in {"local-smoke", "limited-storage", "rotational-hdd"}:
        raise BenchmarkFailure(
            "PDF_RENDER_BENCHMARK_SCENARIO_INVALID",
            "the reference scenario is invalid",
        )
    if (
        attestations.memory_mib != workload.reference_memory_bytes // (1024 * 1024)
        or attestations.graphics_class != "integrated"
    ):
        raise BenchmarkFailure(
            "PDF_RENDER_BENCHMARK_REFERENCE_MISMATCH",
            "the selected environment does not match the reference profile",
        )
    try:
        work_directory.mkdir(parents=True, exist_ok=True)
        if (
            not work_directory.is_dir()
            or work_directory.is_symlink()
            or _is_reparse_point(work_directory)
        ):
            raise OSError
    except OSError as error:
        raise BenchmarkFailure(
            "PDF_RENDER_BENCHMARK_WORK_SCOPE_INVALID",
            "the benchmark work scope is invalid",
        ) from error
    revision = _repository_revision()
    environment = _reference_environment(scenario_id, work_directory, attestations)
    observations = environment.get("observations")
    if (
        not isinstance(observations, dict)
        or observations.get("logical_processors") != workload.reference_logical_processors
        or attestations.memory_mib * 1024 * 1024 != workload.reference_memory_bytes
    ):
        raise BenchmarkFailure(
            "PDF_RENDER_BENCHMARK_REFERENCE_MISMATCH",
            "the selected environment does not match the reference profile",
        )
    windows_identity = _windows_runtime_identity()

    probe_identities: dict[str, Mapping[str, object]] = {}
    renderer_results: list[dict[str, object]] = []
    resources: StagedBenchmarkResources | None = None
    primary_stage_failure: BaseException | None = None
    try:
        stage_root = create_benchmark_stage_root(work_directory)
        resources = stage_benchmark_resources(stage_root, workload, revision)
        _require_same_repository_revision(revision)
        # Probe every candidate before measuring either one.  A missing candidate therefore fails
        # closed without producing an apparently complete one-sided comparison.
        for specification in workload.renderers:
            capture = _capture_child(
                specification.renderer_id,
                "probe",
                workload.measurement.child_timeout_milliseconds,
                resources,
                workload,
            )
            probe_identity = _validate_probe(
                capture,
                specification,
            )
            _validate_stage_record_identity(probe_identity, specification, resources)
            probe_identities[specification.renderer_id] = probe_identity
        for specification in workload.renderers:
            renderer_results.append(
                _run_renderer_suite(
                    specification,
                    workload,
                    resources,
                    probe_identities[specification.renderer_id],
                )
            )
    except OSError:
        failure = BenchmarkFailure(
            "PDF_RENDER_BENCHMARK_WORK_SCOPE_FAILED",
            "the benchmark temporary scope failed",
        )
        primary_stage_failure = failure
        raise failure from None
    except BaseException as error:
        primary_stage_failure = error
        raise
    finally:
        if resources is not None:
            try:
                cleanup_benchmark_resources(resources)
            except BenchmarkFailure:
                _propagate_cleanup_failure(primary_stage_failure)

    _require_same_repository_revision(revision)
    if resources is None:
        raise BenchmarkFailure(OUTCOME_CHILD_FAILED, "renderer child failed")

    report: dict[str, object] = {
        "report_schema_version": 1,
        "status": "complete",
        "measurement_policy": "record-only",
        "task_id": "PWS-0106",
        "repository": {
            "revision": revision,
            "tree_state": "clean",
            "same_revision_clean_checks": 3,
        },
        "runtime": {
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "windows": windows_identity,
        },
        "reference_environment": environment,
        "execution_envelope": {
            "logical_processors": workload.reference_logical_processors,
            "processor_limit_source": "isolated-child-affinity-mask",
            "job_memory_limit_bytes": workload.reference_memory_bytes,
            "memory_limit_source": "bound-reference-profile",
        },
        "workload_binding": {
            "workload_id": workload.workload_id,
            "workload_sha256": workload.workload_sha256,
            "reference_profile_id": workload.reference_profile_id,
            "reference_profile_sha256": workload.reference_profile_sha256,
        },
        "stage_binding": {
            "manifest_schema_version": 1,
            "repository_revision": revision,
            "manifest_sha256": resources.manifest_sha256,
            "closure_sha256": resources.manifest_sha256,
            "file_count": len(resources.staged_file_sha256) + 1,
            "installed_records": [
                {"distribution": name, "record_sha256": digest}
                for name, digest in resources.installed_record_sha256
            ],
        },
        "corpus_binding": {
            "corpus_id": "paperworkshell-public-synthetic-v1",
            "fixture_index_sha256": workload.fixture_index_sha256,
            "golden_index_sha256": workload.golden_index_sha256,
            "fidelity_cases": [
                {
                    "case_id": case.case_id,
                    "fixture_id": case.fixture_id,
                    "source_sha256": case.source_sha256,
                    "golden_sha256": case.golden_sha256,
                    "page_box": case.page_box,
                    "maximum_width_pixels": case.maximum_width_pixels,
                    "maximum_height_pixels": case.maximum_height_pixels,
                    "width_pixels": case.width_pixels,
                    "height_pixels": case.height_pixels,
                }
                for case in workload.fidelity_cases
            ],
            "semantic_cases": [
                {
                    "case_id": case.case_id,
                    "fixture_id": case.fixture_id,
                    "source_sha256": case.source_sha256,
                    "page_box": case.page_box,
                    "page_box_relation": case.page_box_relation,
                    "maximum_width_pixels": case.maximum_width_pixels,
                    "maximum_height_pixels": case.maximum_height_pixels,
                    "width_pixels": case.width_pixels,
                    "height_pixels": case.height_pixels,
                    "oracle": case.oracle,
                }
                for case in workload.semantic_cases
            ],
            "outcome_cases": [
                {
                    "case_id": case.case_id,
                    "fixture_id": case.fixture_id,
                    "source_sha256": case.source_sha256,
                    "expected_code": case.expected_code,
                }
                for case in workload.outcome_cases
            ],
        },
        "render_profile": {
            "id": workload.render_profile_id,
            "pixel_layout": "rgba8-top-down-packed",
            "background": "opaque-white",
            "annotations": "stored-appearances",
            "forms": "disabled",
            "scripts": "disabled",
        },
        "renderer_results": renderer_results,
        "comparison": _comparison_summary(renderer_results, workload),
    }
    assert_sanitized_report(report)
    return report


def _failure_report(error: BenchmarkFailure) -> dict[str, object]:
    code = error.code if error.code in PUBLIC_FAILURE_MESSAGES else OUTCOME_BENCHMARK_FAILED
    message = PUBLIC_FAILURE_MESSAGES.get(code, "renderer benchmark failed")
    report: dict[str, object] = {
        "report_schema_version": 1,
        "status": "failed",
        "task_id": "PWS-0106",
        "error": {"code": code, "message": message},
    }
    if error.cleanup_failed:
        report["cleanup"] = {
            "code": OUTCOME_CLEANUP_FAILED,
            "message": PUBLIC_FAILURE_MESSAGES[OUTCOME_CLEANUP_FAILED],
        }
    try:
        assert_sanitized_report(report)
    except BenchmarkFailure:
        return {
            "report_schema_version": 1,
            "status": "failed",
            "task_id": "PWS-0106",
            "error": {
                "code": OUTCOME_BENCHMARK_FAILED,
                "message": "renderer benchmark failed",
            },
        }
    return report


def main(argv: Sequence[str] | None = None) -> int:
    """Validate the workload or run the isolated renderer comparison."""

    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="validate every versioned corpus and golden binding")
    benchmark = subparsers.add_parser(
        "benchmark",
        help="run the child-only Windows renderer comparison",
    )
    benchmark.add_argument(
        "--scenario",
        choices=("local-smoke", "limited-storage", "rotational-hdd"),
        default="local-smoke",
    )
    benchmark.add_argument(
        "--work-directory",
        type=Path,
        default=PROJECT_ROOT / ".paperworkshell-local" / "renderer-benchmark-work",
    )
    benchmark.add_argument("--attest-memory-mib", type=int)
    benchmark.add_argument("--attest-graphics", choices=("integrated",))
    benchmark.add_argument(
        "--attest-storage-media",
        choices=("hdd-or-validated-emulation",),
    )
    arguments = parser.parse_args(argv)

    workload, issues = validate_workload()
    if issues or workload is None:
        for issue in issues:
            print(issue.render(), file=sys.stderr)
        return 1
    if arguments.command == "validate":
        print(
            "OK: pdf-renderer-windows-v1 binds 15 analytical goldens, "
            "2 semantic comparisons, 3 stable outcomes, 2 renderer identities, and the "
            "windows-low-spec-v1 profile."
        )
        return 0

    try:
        report = benchmark_report(
            workload,
            scenario_id=cast(str, arguments.scenario),
            work_directory=cast(Path, arguments.work_directory),
            attestations=BenchmarkAttestations(
                memory_mib=cast(int | None, arguments.attest_memory_mib),
                graphics_class=cast(str | None, arguments.attest_graphics),
                storage_media=cast(str | None, arguments.attest_storage_media),
            ),
        )
    except BenchmarkFailure as error:
        print(json.dumps(_failure_report(error), indent=2, sort_keys=True), file=sys.stderr)
        return 1
    except Exception:
        failure = BenchmarkFailure(
            OUTCOME_BENCHMARK_FAILED,
            "renderer benchmark failed",
        )
        print(json.dumps(_failure_report(failure), indent=2, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
